"""
app.py — ViralClipper Flask web server
Run with:  python app.py
Then open: http://localhost:5000
"""

import os
import sys
import uuid
import json
import time
import threading
import traceback
from pathlib import Path
from flask import (
    Flask, render_template, request, jsonify,
    send_file, Response, stream_with_context
)
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))
from config import Config
from pipeline.downloader import download_video
from pipeline.transcriber import transcribe
from pipeline.clip_detector import detect_clips
from pipeline.video_processor import process_clip, check_ffmpeg

# ─────────────────────────────────────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY
CORS(app)

os.makedirs(Config.OUTPUT_DIR, exist_ok=True)

# In-memory job store  {job_id: {status, progress, message, clips, error}}
JOBS = {}
JOBS_LOCK = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# Routes — UI
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    ffmpeg_ok = check_ffmpeg()
    return render_template('index.html',
                           ffmpeg_ok=ffmpeg_ok,
                           ai_provider=Config.effective_ai_provider(),
                           whisper_mode=Config.WHISPER_MODE)


# ─────────────────────────────────────────────────────────────────────────────
# Routes — API
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/api/process', methods=['POST'])
def api_process():
    """Start processing a video URL (or batch of URLs)."""
    data   = request.get_json(force=True)
    urls   = data.get('urls') or ([data['url']] if data.get('url') else [])
    brand  = data.get('brand_kit', {})

    if not urls:
        return jsonify({'error': 'No URL provided'}), 400

    jobs = []
    for url in urls:
        job_id = str(uuid.uuid4())[:8]
        with JOBS_LOCK:
            JOBS[job_id] = {
                'status':   'queued',
                'progress': 0,
                'message':  'Queued...',
                'url':      url,
                'clips':    [],
                'error':    None,
            }
        t = threading.Thread(target=_run_pipeline,
                             args=(job_id, url, brand), daemon=True)
        t.start()
        jobs.append(job_id)

    return jsonify({'job_ids': jobs, 'job_id': jobs[0]})


@app.route('/api/status/<job_id>')
def api_status(job_id):
    """Get current status of a job (poll this every second)."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


@app.route('/api/jobs')
def api_jobs():
    """List all jobs."""
    with JOBS_LOCK:
        return jsonify(list(JOBS.values()))


@app.route('/api/download/<job_id>/<int:clip_idx>')
def api_download(job_id, clip_idx):
    """Download a processed clip file."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or not job.get('clips'):
        return jsonify({'error': 'Not found'}), 404
    clips = job['clips']
    if clip_idx >= len(clips):
        return jsonify({'error': 'Clip index out of range'}), 404
    clip = clips[clip_idx]
    filepath = clip.get('file', {}).get('filepath', '')
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found on disk'}), 404
    return send_file(
        filepath,
        mimetype='video/mp4',
        as_attachment=True,
        download_name=clip['file']['filename'],
    )


@app.route('/api/download-all/<job_id>')
def api_download_all(job_id):
    """Download all clips as a zip archive."""
    import zipfile, io
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or not job.get('clips'):
        return jsonify({'error': 'No clips found'}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for clip in job['clips']:
            fp = clip.get('file', {}).get('filepath', '')
            if fp and os.path.exists(fp):
                zf.write(fp, os.path.basename(fp))
    buf.seek(0)
    return send_file(buf, mimetype='application/zip',
                     as_attachment=True,
                     download_name=f'viralclips_{job_id}.zip')


@app.route('/api/settings', methods=['GET'])
def api_settings_get():
    return jsonify({
        'ai_provider':   Config.effective_ai_provider(),
        'whisper_mode':  Config.WHISPER_MODE,
        'whisper_model': Config.WHISPER_MODEL,
        'has_anthropic': Config.has_anthropic(),
        'has_openai':    Config.has_openai(),
        'max_clips':     Config.MAX_CLIPS,
        'min_duration':  Config.MIN_CLIP_DURATION,
        'max_duration':  Config.MAX_CLIP_DURATION,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline runner  (runs in background thread)
# ─────────────────────────────────────────────────────────────────────────────

def _update(job_id, **kwargs):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(kwargs)


def _progress(job_id):
    """Return a callback that updates job progress."""
    def cb(message, pct=None):
        updates = {'message': message, 'status': 'processing'}
        if pct is not None:
            updates['progress'] = pct
        _update(job_id, **updates)
    return cb


def _run_pipeline(job_id: str, url: str, brand_kit: dict):
    cb = _progress(job_id)
    try:
        _update(job_id, status='processing', progress=2,
                message='Starting pipeline...')

        # 1. Download
        cb("Downloading video...", 5)
        video_info = download_video(url, job_id, cb)
        _update(job_id, video_info=video_info)

        # 2. Transcribe
        cb("Transcribing audio...", 18)
        transcript = transcribe(video_info['filepath'], progress_cb=cb)
        _update(job_id, word_count=len(transcript.get('words', [])))

        # 3. Detect clips
        cb("Detecting viral moments...", 38)
        clips = detect_clips(transcript, video_info, progress_cb=cb)
        _update(job_id, clip_count=len(clips))

        # 4. Process each clip
        processed = []
        for i, clip in enumerate(clips):
            pct = 55 + int((i / len(clips)) * 40)
            cb(f"Rendering clip {i+1}/{len(clips)}: {clip.get('title', '')}", pct)

            file_info = process_clip(
                source_path=video_info['filepath'],
                clip=clip,
                transcript=transcript,
                job_id=job_id,
                brand_kit=brand_kit or {},
                progress_cb=cb,
            )
            processed.append({**clip, 'file': file_info})

        _update(job_id,
                status='done',
                progress=100,
                message=f"Done! {len(processed)} clips ready.",
                clips=processed)

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[ERROR] Job {job_id}: {e}\n{tb}")
        _update(job_id, status='error', message=str(e), error=str(e), progress=0)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 55)
    print("  ViralClipper — ScaleReach-style local tool")
    print("=" * 55)
    print(f"  AI engine  : {Config.effective_ai_provider()}")
    print(f"  Whisper    : {Config.WHISPER_MODE} ({Config.WHISPER_MODEL})")
    print(f"  FFmpeg     : {'✓ found' if check_ffmpeg() else '✗ NOT FOUND — install ffmpeg!'}")
    print(f"  Output dir : {os.path.abspath(Config.OUTPUT_DIR)}")
    print(f"\n  Open → http://localhost:{Config.PORT}\n")
    app.run(host='0.0.0.0', port=Config.PORT, debug=Config.DEBUG)
