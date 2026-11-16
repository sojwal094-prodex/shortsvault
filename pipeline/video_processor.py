"""
video_processor.py — Cut clips, reframe to 9:16, burn animated captions
Uses FFmpeg (must be installed separately) + OpenCV for face detection.
"""

import os
import re
import json
import math
import subprocess
import tempfile
from pathlib import Path
from config import Config


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def process_clip(
    source_path: str,
    clip: dict,
    transcript: dict,
    job_id: str,
    brand_kit: dict = None,
    progress_cb=None,
) -> dict:
    """
    Full pipeline for a single clip:
      1. Cut from source video
      2. Reframe to 9:16 (face-aware crop)
      3. Burn animated word-by-word captions
      4. Apply brand watermark (optional)

    Returns:
        {
          'filepath':   str,   # path to finished .mp4
          'filename':   str,
          'size_mb':    float,
          'resolution': str,
        }
    """
    out_dir = os.path.join(Config.OUTPUT_DIR, job_id, "clips")
    os.makedirs(out_dir, exist_ok=True)

    clip_num  = clip.get('index', 1)
    safe_title = _safe_filename(clip.get('title', f'clip_{clip_num}'))
    final_path = os.path.join(out_dir, f"{clip_num:02d}_{safe_title}.mp4")

    if progress_cb:
        progress_cb(f"Processing clip {clip_num}: {clip.get('title', '')}", None)

    # ── Step 1: Cut ──────────────────────────────────────────────────────────
    cut_path = os.path.join(out_dir, f"_cut_{clip_num}.mp4")
    _cut_clip(source_path, clip['start'], clip['end'], cut_path)

    # ── Step 2: Reframe to 9:16 ──────────────────────────────────────────────
    reframed_path = os.path.join(out_dir, f"_reframe_{clip_num}.mp4")
    _reframe_to_vertical(cut_path, reframed_path)

    # ── Step 3: Burn captions ────────────────────────────────────────────────
    caption_path = os.path.join(out_dir, f"_caption_{clip_num}.mp4")
    clip_words   = _get_words_for_clip(transcript, clip['start'], clip['end'])
    _burn_captions(reframed_path, clip_words, clip['start'], caption_path, brand_kit)

    # ── Step 4: Brand watermark ──────────────────────────────────────────────
    if brand_kit and (brand_kit.get('logo_path') or brand_kit.get('channel_name')):
        _apply_brand(caption_path, final_path, brand_kit)
    else:
        os.rename(caption_path, final_path)

    # ── Cleanup intermediates ────────────────────────────────────────────────
    for tmp in [cut_path, reframed_path]:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass

    size_mb = os.path.getsize(final_path) / (1024 * 1024)
    return {
        'filepath':   os.path.abspath(final_path),
        'filename':   os.path.basename(final_path),
        'size_mb':    round(size_mb, 1),
        'resolution': '1080x1920',
    }


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Cut
# ─────────────────────────────────────────────────────────────────────────────

def _cut_clip(source: str, start: float, end: float, out: str):
    duration = end - start
    cmd = [
        'ffmpeg', '-y',
        '-ss',       str(start),
        '-i',        source,
        '-t',        str(duration),
        '-c:v',      'libx264',
        '-preset',   'fast',
        '-crf',      '18',
        '-c:a',      'aac',
        '-b:a',      '192k',
        '-movflags', '+faststart',
        out,
    ]
    _run(cmd, f"Cut clip [{start:.1f}s → {end:.1f}s]")


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Reframe to 9:16
# ─────────────────────────────────────────────────────────────────────────────

def _reframe_to_vertical(source: str, out: str):
    """
    Detect face position and crop to 9:16.
    Falls back to centre-crop if OpenCV not available or no face found.
    """
    face_x = _detect_face_x(source)   # returns None if unavailable

    # Get source dimensions
    probe = _probe(source)
    src_w = probe['width']
    src_h = probe['height']

    # Target crop width for 9:16 from the source height
    crop_w = int(src_h * 9 / 16)
    crop_w = min(crop_w, src_w)  # can't crop wider than source

    if face_x is not None:
        # Centre crop on face, clamped to frame
        cx = max(crop_w // 2, min(src_w - crop_w // 2, int(face_x)))
        crop_x = cx - crop_w // 2
    else:
        crop_x = (src_w - crop_w) // 2  # centre crop

    crop_x = max(0, min(src_w - crop_w, crop_x))

    # If source is already narrower than the target crop_w, just pad
    if src_w <= crop_w:
        # Just scale to 1080x1920 with padding
        vf = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
    else:
        vf = (
            f"crop={crop_w}:{src_h}:{crop_x}:0,"
            f"scale=1080:1920:force_original_aspect_ratio=decrease,"
            f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
        )

    cmd = [
        'ffmpeg', '-y',
        '-i',    source,
        '-vf',   vf,
        '-c:v',  'libx264',
        '-preset', 'fast',
        '-crf',  '18',
        '-c:a',  'copy',
        out,
    ]
    _run(cmd, "Reframe to 9:16")


def _detect_face_x(video_path: str):
    """Return average horizontal centre of detected faces. None if unavailable."""
    try:
        import cv2
        import numpy as np

        cap    = cv2.VideoCapture(video_path)
        fps    = cap.get(cv2.CAP_PROP_FPS) or 25
        total  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        step   = max(1, int(fps * 2))   # sample every 2 seconds

        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )

        cx_values = []
        for frame_no in range(0, min(total, int(fps * 60)), step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
            ret, frame = cap.read()
            if not ret:
                break
            gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, 1.1, 5, minSize=(60, 60))
            for (x, y, w, h) in faces:
                cx_values.append(x + w / 2)

        cap.release()
        return float(np.mean(cx_values)) if cx_values else None

    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Animated word-by-word captions
# ─────────────────────────────────────────────────────────────────────────────

def _burn_captions(source: str, words: list, clip_start: float, out: str, brand_kit: dict = None):
    """
    Generate an ASS subtitle file with karaoke-style word highlighting,
    then burn it into the video with FFmpeg.
    """
    if not words:
        # No transcript for this clip — just copy
        import shutil
        shutil.copy2(source, out)
        return

    ass_path = source.replace('.mp4', '_captions.ass')
    _write_ass(ass_path, words, clip_start, brand_kit)

    cmd = [
        'ffmpeg', '-y',
        '-i',    source,
        '-vf',   f"ass='{ass_path}'",
        '-c:v',  'libx264',
        '-preset', 'fast',
        '-crf',  '18',
        '-c:a',  'copy',
        out,
    ]
    _run(cmd, "Burn captions")

    if os.path.exists(ass_path):
        try:
            os.remove(ass_path)
        except Exception:
            pass


def _write_ass(path: str, words: list, clip_start: float, brand_kit: dict = None):
    """Write an ASS subtitle file with animated word highlighting."""
    # Style settings
    bk        = brand_kit or {}
    font      = bk.get('font', 'Arial')
    primary   = bk.get('caption_color', '&H00FFFFFF')   # white
    highlight = bk.get('highlight_color', '&H0000FFFF')  # yellow

    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
Timer: 100.0000

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},68,{primary},&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,80,80,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = []
    # Group words into lines of ~5 words
    GROUP_SIZE = 5
    groups     = [words[i:i+GROUP_SIZE] for i in range(0, len(words), GROUP_SIZE)]

    for group in groups:
        if not group:
            continue

        g_start = group[0]['start']  - clip_start
        g_end   = group[-1]['end']   - clip_start
        g_start = max(0, g_start)
        g_end   = max(g_start + 0.5, g_end)

        # Build karaoke text with {\k} tags
        kara_parts = []
        for w in group:
            w_dur = int((w['end'] - w['start']) * 100)  # centiseconds
            kara_parts.append(f"{{\\kf{w_dur}}}{w['word']} ")

        text = "".join(kara_parts).strip()

        lines.append(
            f"Dialogue: 0,{_ass_time(g_start)},{_ass_time(g_end)},Default,,0,0,0,,{text}"
        )

    with open(path, 'w', encoding='utf-8') as f:
        f.write(ass_header + "\n".join(lines))


def _ass_time(seconds: float) -> str:
    """Format seconds as ASS timestamp H:MM:SS.cc"""
    cs   = int(seconds * 100)
    h    = cs // 360000;  cs %= 360000
    m    = cs // 6000;    cs %= 6000
    s    = cs // 100;     cs %= 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _get_words_for_clip(transcript: dict, start: float, end: float) -> list:
    """Extract words that fall within [start, end]."""
    return [
        w for w in transcript.get('words', [])
        if w['start'] >= start and w['end'] <= end + 0.5
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Brand watermark / channel name overlay
# ─────────────────────────────────────────────────────────────────────────────

def _apply_brand(source: str, out: str, brand_kit: dict):
    """Overlay logo or channel name text onto video."""
    filters = []

    # Text watermark (channel name in top-left)
    channel = brand_kit.get('channel_name', '')
    if channel:
        escaped = channel.replace("'", "\\'").replace(":", "\\:")
        font    = brand_kit.get('font', 'Arial')
        color   = brand_kit.get('watermark_color', 'white')
        filters.append(
            f"drawtext=text='{escaped}':fontfile={font}:fontsize=36:"
            f"fontcolor={color}:x=40:y=40:"
            f"shadowcolor=black:shadowx=2:shadowy=2"
        )

    if not filters:
        import shutil
        shutil.copy2(source, out)
        return

    vf = ",".join(filters)
    cmd = [
        'ffmpeg', '-y',
        '-i',   source,
        '-vf',  vf,
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
        '-c:a', 'copy',
        out,
    ]
    _run(cmd, "Apply brand")


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _run(cmd: list, label: str):
    """Run an FFmpeg command and raise on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed during '{label}':\n{result.stderr[-1000:]}"
        )


def _probe(path: str) -> dict:
    """Get basic video dimensions via ffprobe."""
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_streams', path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    info   = json.loads(result.stdout)
    for stream in info.get('streams', []):
        if stream.get('codec_type') == 'video':
            return {
                'width':  stream.get('width',  1920),
                'height': stream.get('height', 1080),
            }
    return {'width': 1920, 'height': 1080}


def _safe_filename(name: str) -> str:
    name = re.sub(r'[^\w\s-]', '', name)
    name = re.sub(r'[\s]+', '_', name.strip())
    return name[:50]


def check_ffmpeg() -> bool:
    """Return True if ffmpeg is available on PATH."""
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
