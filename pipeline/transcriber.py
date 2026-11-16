"""
transcriber.py — Speech-to-text with word-level timestamps
Supports:  local faster-whisper (free)  |  OpenAI Whisper API (faster)
"""

import os
import json
from config import Config


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def transcribe(video_path: str, language: str = None, progress_cb=None) -> dict:
    """
    Transcribe a video file and return word-level timestamps.

    Returns:
        {
          'words':    [{'word': str, 'start': float, 'end': float}, ...],
          'segments': [{'text': str, 'start': float, 'end': float}, ...],
          'full_text': str,
          'language': str,
          'duration': float,
        }
    """
    if progress_cb:
        progress_cb("Starting transcription...", 20)

    mode = Config.WHISPER_MODE

    if mode == "openai" and Config.has_openai():
        return _transcribe_openai(video_path, language, progress_cb)
    else:
        return _transcribe_local(video_path, language, progress_cb)


# ─────────────────────────────────────────────────────────────────────────────
# Local faster-whisper  (free, runs on CPU, no API key needed)
# ─────────────────────────────────────────────────────────────────────────────

def _transcribe_local(video_path: str, language: str, progress_cb=None) -> dict:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "faster-whisper is not installed. Run:  pip install faster-whisper"
        )

    model_size = Config.WHISPER_MODEL
    if progress_cb:
        progress_cb(f"Loading Whisper model ({model_size})...", 22)

    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    if progress_cb:
        progress_cb("Transcribing audio (this may take a minute)...", 25)

    segments_gen, info = model.transcribe(
        video_path,
        beam_size=5,
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )

    words = []
    segments = []
    full_text_parts = []

    for seg in segments_gen:
        seg_text = seg.text.strip()
        segments.append({
            'text':  seg_text,
            'start': round(seg.start, 3),
            'end':   round(seg.end, 3),
        })
        full_text_parts.append(seg_text)

        if seg.words:
            for w in seg.words:
                words.append({
                    'word':  w.word.strip(),
                    'start': round(w.start, 3),
                    'end':   round(w.end, 3),
                })

    if progress_cb:
        progress_cb("Transcription complete!", 35)

    return {
        'words':     words,
        'segments':  segments,
        'full_text': " ".join(full_text_parts),
        'language':  info.language,
        'duration':  info.duration,
    }


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI Whisper API  (faster, costs ~$0.006/min)
# ─────────────────────────────────────────────────────────────────────────────

def _transcribe_openai(video_path: str, language: str, progress_cb=None) -> dict:
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed. Run:  pip install openai")

    if progress_cb:
        progress_cb("Sending audio to OpenAI Whisper API...", 22)

    client = OpenAI(api_key=Config.OPENAI_API_KEY)

    # Extract audio if the file is large (Whisper API limit is 25 MB)
    audio_path = _extract_audio_if_needed(video_path)

    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
            language=language,
        )

    # Clean up temp audio
    if audio_path != video_path:
        try:
            os.remove(audio_path)
        except Exception:
            pass

    words = []
    if hasattr(response, 'words') and response.words:
        for w in response.words:
            words.append({
                'word':  w.word.strip(),
                'start': round(w.start, 3),
                'end':   round(w.end, 3),
            })

    segments = []
    if hasattr(response, 'segments') and response.segments:
        for s in response.segments:
            segments.append({
                'text':  s.text.strip(),
                'start': round(s.start, 3),
                'end':   round(s.end, 3),
            })

    if progress_cb:
        progress_cb("Transcription complete!", 35)

    return {
        'words':     words,
        'segments':  segments,
        'full_text': response.text,
        'language':  getattr(response, 'language', 'en'),
        'duration':  segments[-1]['end'] if segments else 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_audio_if_needed(video_path: str) -> str:
    """Extract audio to .mp3 if the video file is > 24 MB."""
    if os.path.getsize(video_path) <= 24 * 1024 * 1024:
        return video_path

    import subprocess
    audio_path = video_path.replace('.mp4', '_audio.mp3')
    subprocess.run(
        ['ffmpeg', '-i', video_path, '-vn', '-ar', '16000',
         '-ac', '1', '-b:a', '32k', '-y', audio_path],
        capture_output=True, check=True
    )
    return audio_path


def build_transcript_text(transcript: dict) -> str:
    """Format transcript with timestamps for the AI prompt."""
    lines = []
    for seg in transcript['segments']:
        t = _fmt_time(seg['start'])
        lines.append(f"[{t}] {seg['text']}")
    return "\n".join(lines)


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
