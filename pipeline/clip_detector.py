"""
clip_detector.py — Find the best viral clips using AI or heuristics
Supports: Claude API | OpenAI API | Heuristic fallback (no API key needed)
"""

import os
import re
import json
import math
from config import Config
from pipeline.transcriber import build_transcript_text


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def detect_clips(transcript: dict, video_info: dict, progress_cb=None) -> list:
    """
    Analyze a transcript and return a list of viral clip candidates.

    Returns list of dicts:
        {
          'index':       int,
          'start':       float,  # seconds
          'end':         float,  # seconds
          'duration':    float,
          'viral_score': int,    # 0-100
          'title':       str,
          'hook':        str,    # first 3-5 words of clip
          'caption':     str,    # suggested social caption + hashtags
          'reason':      str,    # why this will perform well
          'type':        str,    # emotion | story | insight | humor | value
        }
    """
    provider = Config.effective_ai_provider()

    if progress_cb:
        progress_cb(f"Detecting viral clips ({provider} engine)...", 38)

    if provider == "claude":
        clips = _detect_with_claude(transcript, video_info)
    elif provider == "openai":
        clips = _detect_with_openai(transcript, video_info)
    else:
        clips = _detect_heuristic(transcript, video_info)

    # Validate and clamp all clips
    duration = transcript.get('duration', video_info.get('duration', 0))
    clips = _validate_clips(clips, duration)

    # Sort by viral score descending
    clips.sort(key=lambda c: c.get('viral_score', 0), reverse=True)

    # Add index
    for i, clip in enumerate(clips):
        clip['index'] = i + 1

    if progress_cb:
        progress_cb(f"Found {len(clips)} viral clip candidates!", 50)

    return clips


# ─────────────────────────────────────────────────────────────────────────────
# Claude  (Anthropic)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_with_claude(transcript: dict, video_info: dict) -> list:
    import anthropic

    client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    transcript_text = build_transcript_text(transcript)
    duration = transcript.get('duration', video_info.get('duration', 0))

    prompt = f"""You are a world-class viral content strategist who has helped creators get 100M+ views.

Analyze this video transcript and identify the {Config.MAX_CLIPS} best clips for TikTok, Instagram Reels, and YouTube Shorts.

VIDEO INFO:
- Title: {video_info.get('title', 'Unknown')}
- Channel: {video_info.get('channel', 'Unknown')}
- Duration: {duration:.0f} seconds ({duration/60:.1f} minutes)

TRANSCRIPT (with timestamps):
{transcript_text}

For each clip, choose moments that have:
✓ Strong opening hook (attention in first 3 seconds)
✓ Emotional resonance (surprise, humor, inspiration, controversy, fear, joy)
✓ Clear value or payoff
✓ Natural beginning and end (not mid-sentence)
✓ 15–90 seconds ideal length

Return ONLY a valid JSON array — no markdown, no commentary. Each item:
{{
  "start": <float seconds>,
  "end": <float seconds>,
  "viral_score": <int 0-100>,
  "title": "<punchy 5-8 word title>",
  "hook": "<exact first sentence of clip>",
  "caption": "<2-3 sentence social caption with 5 relevant hashtags>",
  "reason": "<1 sentence: why this will go viral>",
  "type": "<emotion|story|insight|humor|value|controversy>"
}}"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)

    return json.loads(raw)


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI  (GPT-4o)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_with_openai(transcript: dict, video_info: dict) -> list:
    from openai import OpenAI

    client = OpenAI(api_key=Config.OPENAI_API_KEY)
    transcript_text = build_transcript_text(transcript)
    duration = transcript.get('duration', video_info.get('duration', 0))

    prompt = f"""You are a viral content strategist. Analyze this transcript and find the {Config.MAX_CLIPS} best clips for short-form video.

VIDEO: {video_info.get('title', 'Unknown')} | {duration:.0f}s

TRANSCRIPT:
{transcript_text}

Return ONLY a JSON array. Each item must have:
start (float), end (float), viral_score (int 0-100), title (str), hook (str), caption (str with hashtags), reason (str), type (emotion|story|insight|humor|value|controversy)

Clips should be 15-90 seconds. Return no other text."""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        max_tokens=3000,
    )

    raw = response.choices[0].message.content
    parsed = json.loads(raw)

    # GPT sometimes wraps in {"clips": [...]}
    if isinstance(parsed, dict):
        for key in ('clips', 'results', 'data'):
            if key in parsed and isinstance(parsed[key], list):
                return parsed[key]
        return list(parsed.values())[0] if parsed else []

    return parsed


# ─────────────────────────────────────────────────────────────────────────────
# Heuristic fallback  (no API key needed)
# ─────────────────────────────────────────────────────────────────────────────

VIRAL_KEYWORDS = {
    'high': [
        'secret', 'never told', 'nobody knows', 'shocking', 'reveal',
        'actually', 'truth', 'mistake', 'wrong', 'surprising', 'incredible',
        'you need to', 'most people', 'changed my life', 'important',
        'serious', 'listen', 'pay attention', 'this is why', 'reason why',
        'hack', 'trick', 'simple', 'easy', 'stop', 'don\'t', 'wait',
    ],
    'medium': [
        'because', 'but', 'however', 'actually', 'really', 'truly',
        'exactly', 'perfect', 'amazing', 'incredible', 'love', 'hate',
        'money', 'success', 'fail', 'win', 'lose', 'best', 'worst',
    ],
}

def _detect_heuristic(transcript: dict, video_info: dict) -> list:
    """Score segments by keyword density, sentence energy, and position."""
    segments   = transcript.get('segments', [])
    total_dur  = transcript.get('duration', video_info.get('duration', 0))

    if not segments:
        return []

    # Score each segment
    scored = []
    for i, seg in enumerate(segments):
        text  = seg['text'].lower()
        score = 50  # baseline

        # Keyword scoring
        for kw in VIRAL_KEYWORDS['high']:
            if kw in text:
                score += 8
        for kw in VIRAL_KEYWORDS['medium']:
            if kw in text:
                score += 3

        # Question marks → engagement
        score += text.count('?') * 5

        # Exclamation marks → energy
        score += min(text.count('!') * 3, 10)

        # Short snappy sentences score higher
        words = text.split()
        if 10 <= len(words) <= 30:
            score += 5

        # Avoid very beginning and very end (usually intro/outro)
        rel_pos = seg['start'] / max(total_dur, 1)
        if 0.05 < rel_pos < 0.9:
            score += 5

        # Cap at 95
        score = min(score, 95)

        scored.append({'idx': i, 'score': score, 'seg': seg})

    # Sort by score and pick top candidates
    scored.sort(key=lambda x: x['score'], reverse=True)
    top = scored[:Config.MAX_CLIPS * 3]  # over-select then merge

    # Expand each top segment into a clip window
    clips = []
    used_ranges = []

    for item in top:
        idx   = item['idx']
        score = item['score']
        seg   = item['seg']

        # Build a 20-60 second window around this segment
        target_len = min(60, max(20, len(seg['text'].split()) * 0.5))
        start = max(0, seg['start'] - 5)
        end   = start

        # Extend forward until we hit target length
        j = idx
        while j < len(segments) and (segments[j]['end'] - start) < target_len:
            end = segments[j]['end']
            j += 1

        end = min(end, total_dur)
        clip_len = end - start

        if clip_len < Config.MIN_CLIP_DURATION:
            continue

        # Check overlap with existing clips (allow at most 20% overlap)
        overlap = False
        for (us, ue) in used_ranges:
            overlap_secs = min(end, ue) - max(start, us)
            if overlap_secs > 0 and overlap_secs / clip_len > 0.20:
                overlap = True
                break

        if overlap:
            continue

        used_ranges.append((start, end))

        # Build hook from first sentence
        hook = seg['text'].strip()
        if len(hook) > 80:
            hook = hook[:80].rsplit(' ', 1)[0] + '...'

        clip_type = _infer_type(seg['text'])

        clips.append({
            'start':       round(start, 2),
            'end':         round(end, 2),
            'viral_score': min(score, 95),
            'title':       _make_title(seg['text']),
            'hook':        hook,
            'caption':     _make_caption(seg['text'], video_info),
            'reason':      f"High-energy {clip_type} moment with strong engagement signals",
            'type':        clip_type,
        })

        if len(clips) >= Config.MAX_CLIPS:
            break

    return clips


def _infer_type(text: str) -> str:
    text = text.lower()
    if any(w in text for w in ['laugh', 'funny', 'lol', 'joke', 'haha']):
        return 'humor'
    if any(w in text for w in ['story', 'when i', 'one day', 'remember']):
        return 'story'
    if any(w in text for w in ['tip', 'hack', 'secret', 'trick', 'how to', 'way to']):
        return 'value'
    if any(w in text for w in ['feel', 'emotion', 'love', 'hate', 'amazing', 'incredible']):
        return 'emotion'
    return 'insight'


def _make_title(text: str) -> str:
    words = text.strip().split()[:8]
    title = ' '.join(words)
    if len(title) > 50:
        title = title[:47] + '...'
    return title.capitalize()


def _make_caption(text: str, video_info: dict) -> str:
    snippet = text.strip()[:120]
    channel = video_info.get('channel', '')
    tags = '#shorts #viral #trending #fyp #reels'
    if channel:
        return f'"{snippet}" — {channel}\n\n{tags}'
    return f'"{snippet}"\n\n{tags}'


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def _validate_clips(clips: list, duration: float) -> list:
    """Clamp, dedupe, and enforce duration constraints."""
    valid = []
    for c in clips:
        start = max(0.0, float(c.get('start', 0)))
        end   = min(float(duration or 999999), float(c.get('end', start + 30)))

        # Enforce min/max duration
        clip_len = end - start
        if clip_len < Config.MIN_CLIP_DURATION:
            end = start + Config.MIN_CLIP_DURATION
        if clip_len > Config.MAX_CLIP_DURATION:
            end = start + Config.MAX_CLIP_DURATION

        # Clamp to video length
        if end > duration > 0:
            end = duration
            start = max(0, end - (Config.MIN_CLIP_DURATION + 5))

        c['start']    = round(start, 2)
        c['end']      = round(end,   2)
        c['duration'] = round(end - start, 2)

        # Ensure required fields
        c.setdefault('viral_score', 70)
        c.setdefault('title',       'Viral Clip')
        c.setdefault('hook',        '')
        c.setdefault('caption',     '#shorts #viral #trending')
        c.setdefault('reason',      '')
        c.setdefault('type',        'insight')

        c['viral_score'] = max(0, min(100, int(c['viral_score'])))

        if c['duration'] >= Config.MIN_CLIP_DURATION:
            valid.append(c)

    return valid[:Config.MAX_CLIPS]
