"""
app.py — ViralClipper Flask web server
Run with:  python app.py  →  open http://localhost:5000
"""

import os, sys, uuid, json, time, threading, traceback
from flask import Flask, render_template, request, jsonify, send_file, Response
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

from config import Config
from pipeline.downloader    import download_video
from pipeline.transcriber   import transcribe
from pipeline.clip_detector import detect_clips
from pipeline.video_processor import process_clip, check_ffmpeg
from pipeline.database      import init_db, save_clip, get_stats
from pipeline.learner       import extract_features, train_model, should_retrain

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY
CORS(app)

os.makedirs(Config.OUTPUT_DIR, exist_ok=True)
init_db()

JOBS      = {}
JOBS_LOCK = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    stats = get_stats()
    return render_template('index.html',
        ffmpeg_ok    = check_ffmpeg(),
        ai_provider  = Config.effective_ai_provider(),
        whisper_mode = Config.WHISPER_MODE,
        brain_stats  = stats,
    )


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/api/process', methods=['POST'])
def api_process():
    data  = request.get_json(force=True)
    urls  = data.get('urls') or ([data['url']] if data.get('url') else [])
    brand = data.get('brand_kit', {})
    mode  = data.get('mode', 'auto')   # 'auto' | 'api' | 'local'

    if not urls:
        return jsonify({'error': 'No URL provided'}), 400

    jobs = []
    for url in urls:
        jid = str(uuid.uuid4())[:8]
        with JOBS_LOCK:
            JOBS[jid] = {'status':'queued','progress':0,
                         'message':'Queued…','url':url,'clips':[],'error':None}
        threading.Thread(target=_run_pipeline,
                         args=(jid, url, brand, mode), daemon=True).start()
        jobs.append(jid)

    return jsonify({'job_ids': jobs, 'job_id': jobs[0]})


@app.route('/api/status/<job_id>')
def api_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


@app.route('/api/download/<job_id>/<int:clip_idx>')
def api_download(job_id, clip_idx):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or not job.get('clips'):
        return jsonify({'error': 'Not found'}), 404
    clips = job['clips']
    if clip_idx >= len(clips):
        return jsonify({'error': 'Index out of range'}), 404
    fp = clips[clip_idx].get('file', {}).get('filepath', '')
    if not fp or not os.path.exists(fp):
        return jsonify({'error': 'File not on disk'}), 404
    return send_file(fp, mimetype='video/mp4', as_attachment=True,
                     download_name=clips[clip_idx]['file']['filename'])


@app.route('/api/download-all/<job_id>')
def api_download_all(job_id):
    import zipfile, io
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or not job.get('clips'):
        return jsonify({'error': 'No clips'}), 404
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for c in job['clips']:
            fp = c.get('file', {}).get('filepath', '')
            if fp and os.path.exists(fp):
                zf.write(fp, os.path.basename(fp))
    buf.seek(0)
    return send_file(buf, mimetype='application/zip', as_attachment=True,
                     download_name=f'viralclips_{job_id}.zip')


# ── Brain / Learning routes ───────────────────────────────────────────────────

@app.route('/api/brain/stats')
def api_brain_stats():
    return jsonify(get_stats())


@app.route('/api/brain/train', methods=['POST'])
def api_brain_train():
    """Manually trigger model retraining."""
    result = train_model()
    return jsonify(result)


@app.route('/api/cookies', methods=['POST'])
def api_cookies():
    """Save YouTube cookies.txt content so yt-dlp can bypass bot detection."""
    data = request.get_json(force=True)
    content = (data.get('cookies') or '').strip()
    if not content:
        # Delete existing cookies file if empty string sent
        p = os.path.join(Config.OUTPUT_DIR, 'cookies.txt')
        if os.path.exists(p): os.remove(p)
        return jsonify({'ok': True, 'msg': 'Cookies cleared'})
    path = os.path.join(Config.OUTPUT_DIR, 'cookies.txt')
    with open(path, 'w') as f:
        f.write(content)
    return jsonify({'ok': True, 'msg': f'Saved {len(content.splitlines())} cookie lines'})


@app.route('/api/cookies', methods=['GET'])
def api_cookies_status():
    """Check if cookies.txt exists."""
    path = os.path.join(Config.OUTPUT_DIR, 'cookies.txt')
    exists = os.path.exists(path)
    lines  = 0
    if exists:
        with open(path) as f:
            lines = sum(1 for l in f if l.strip() and not l.startswith('#'))
    return jsonify({'has_cookies': exists, 'cookie_count': lines})


@app.route('/api/settings', methods=['GET'])
def api_settings():
    return jsonify({
        'ai_provider':   Config.effective_ai_provider(),
        'whisper_mode':  Config.WHISPER_MODE,
        'whisper_model': Config.WHISPER_MODEL,
        'has_anthropic': Config.has_anthropic(),
        'has_openai':    Config.has_openai(),
        'max_clips':     Config.MAX_CLIPS,
        'min_duration':  Config.MIN_CLIP_DURATION,
        'max_duration':  Config.MAX_CLIP_DURATION,
        'brain_stats':   get_stats(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline runner
# ─────────────────────────────────────────────────────────────────────────────

def _upd(jid, **kw):
    with JOBS_LOCK:
        if jid in JOBS: JOBS[jid].update(kw)

def _cb(jid):
    def cb(msg, pct=None):
        u = {'message': msg, 'status': 'processing'}
        if pct is not None: u['progress'] = pct
        _upd(jid, **u)
    return cb


def _run_pipeline(jid, url, brand_kit, mode):
    cb = _cb(jid)
    try:
        _upd(jid, status='processing', progress=2, message='Starting…')

        # 1. Download
        cb("Downloading video…", 5)
        video_info = download_video(url, jid, cb)
        _upd(jid, video_info=video_info)

        # 2. Transcribe
        cb("Transcribing audio…", 18)
        transcript = transcribe(video_info['filepath'], progress_cb=cb)

        # 3. Detect clips
        cb("Detecting viral moments…", 38)
        clips, source = detect_clips(transcript, video_info,
                                     mode=mode, progress_cb=cb)
        _upd(jid, clip_count=len(clips), detection_source=source)

        # 4. Process clips + learn from each one
        processed = []
        for i, clip in enumerate(clips):
            pct = 55 + int((i / len(clips)) * 40)
            cb(f"Rendering clip {i+1}/{len(clips)}: {clip.get('title','')}", pct)

            file_info = process_clip(
                source_path  = video_info['filepath'],
                clip         = clip,
                transcript   = transcript,
                job_id       = jid,
                brand_kit    = brand_kit or {},
                progress_cb  = cb,
            )

            # ── LEARN from this clip ─────────────────────────────────────────
            # Extract the text for this clip from the transcript
            seg_texts = [s['text'] for s in transcript.get('segments', [])
                         if s['start'] >= clip['start'] and s['end'] <= clip['end'] + 1]
            clip_text = ' '.join(seg_texts) or clip.get('hook', '')

            features = (clip.get('_features') or
                        extract_features(clip_text, clip['start'], clip['end'],
                                         video_info.get('duration', 0)))
            features['text'] = clip_text

            save_clip(clip, video_info, features, source)
            # ────────────────────────────────────────────────────────────────

            processed.append({**clip, 'file': file_info})

        # 5. Auto-retrain model if enough new data
        if should_retrain():
            cb("🧠 Training brain on new clips…", 97)
            train_result = train_model()
            retrain_msg  = (f"🧠 Model updated — trained on "
                            f"{train_result.get('clips',0)} clips, "
                            f"accuracy ~{train_result.get('accuracy',0):.0f}%")
        else:
            stats = get_stats()
            needed = max(0, 10 - stats['total_clips'])
            retrain_msg = (f"🧠 Brain learned from these clips! "
                           f"({stats['total_clips']} clips stored"
                           + (f", {needed} more until model activates)" if needed > 0 else ")")
                           )

        _upd(jid, status='done', progress=100,
             message=f"Done! {len(processed)} clips ready.",
             clips=processed,
             brain_msg=retrain_msg,
             detection_source=source)

    except Exception as e:
        print(f"[ERROR] Job {jid}: {e}\n{traceback.format_exc()}")
        _upd(jid, status='error', message=str(e), error=str(e), progress=0)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    stats = get_stats()
    print("=" * 55)
    print("  ⚡ ViralClipper")
    print("=" * 55)
    print(f"  AI engine  : {Config.effective_ai_provider()}")
    print(f"  Whisper    : {Config.WHISPER_MODE} ({Config.WHISPER_MODEL})")
    print(f"  FFmpeg     : {'✓' if check_ffmpeg() else '✗ NOT FOUND'}")
    print(f"  Brain      : {stats['total_clips']} clips stored"
          + (f", model active (acc ~{stats['model_accuracy']:.0f}%)"
             if stats.get('model_accuracy') else " (learning…)"))
    print(f"\n  Open → http://localhost:{Config.PORT}\n")
    app.run(host='0.0.0.0', port=Config.PORT, debug=Config.DEBUG)
