"""
clip_detector.py — Viral clip detection with 3 modes
  api      → Claude / OpenAI API (best quality, costs tokens)
  local    → Self-learned ML model (improves with every clip)
  auto     → API if key present, else local model, else heuristic
"""

import os, re, json, math
from config import Config
from pipeline.transcriber import build_transcript_text


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def detect_clips(transcript: dict, video_info: dict,
                 mode: str = None, progress_cb=None) -> tuple:
    """
    Detect viral clips. mode overrides Config if supplied.

    Returns: (clips: list, source: str)
      source = 'api_claude' | 'api_openai' | 'model' | 'heuristic'
    """
    # Decide which engine to use
    provider = mode or Config.effective_ai_provider()

    if progress_cb:
        label = {'api_claude':'Claude AI','api_openai':'OpenAI GPT',
                 'model':'Self-learned model','heuristic':'Smart heuristics',
                 'claude':'Claude AI','openai':'OpenAI GPT',
                 'local':'Self-learned model'}.get(provider, provider)
        progress_cb(f"Detecting viral moments ({label})...", 38)

    # Try local ML model first if mode == 'local'
    if provider in ('local', 'model'):
        from pipeline.learner import predict_clips_local, load_model
        model_data = load_model()
        if model_data:
            clips = predict_clips_local(transcript, video_info)
            source = 'model'
        else:
            clips  = _detect_heuristic(transcript, video_info)
            source = 'heuristic'

    elif provider in ('claude', 'api_claude') and Config.has_anthropic():
        try:
            clips  = _detect_with_claude(transcript, video_info)
            source = 'api_claude'
        except Exception as e:
            print(f"[warn] Claude failed ({e}), falling back")
            clips  = _detect_heuristic(transcript, video_info)
            source = 'heuristic'

    elif provider in ('openai', 'api_openai') and Config.has_openai():
        try:
            clips  = _detect_with_openai(transcript, video_info)
            source = 'api_openai'
        except Exception as e:
            print(f"[warn] OpenAI failed ({e}), falling back")
            clips  = _detect_heuristic(transcript, video_info)
            source = 'heuristic'

    else:  # auto — try API, then model, then heuristic
        if Config.has_anthropic():
            try:
                clips  = _detect_with_claude(transcript, video_info)
                source = 'api_claude'
            except Exception:
                clips  = _detect_heuristic(transcript, video_info)
                source = 'heuristic'
        elif Config.has_openai():
            try:
                clips  = _detect_with_openai(transcript, video_info)
                source = 'api_openai'
            except Exception:
                clips  = _detect_heuristic(transcript, video_info)
                source = 'heuristic'
        else:
            from pipeline.learner import predict_clips_local, load_model
            if load_model():
                clips  = predict_clips_local(transcript, video_info)
                source = 'model'
            else:
                clips  = _detect_heuristic(transcript, video_info)
                source = 'heuristic'

    duration = transcript.get('duration', video_info.get('duration', 0))
    clips    = _validate_clips(clips, duration)
    clips.sort(key=lambda c: c.get('viral_score', 0), reverse=True)
    for i, c in enumerate(clips):
        c['index'] = i + 1

    if progress_cb:
        progress_cb(f"Found {len(clips)} viral clip candidates!", 50)

    return clips, source


# ─────────────────────────────────────────────────────────────────────────────
# Claude
# ─────────────────────────────────────────────────────────────────────────────

def _detect_with_claude(transcript, video_info):
    import anthropic
    client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    transcript_text = build_transcript_text(transcript)
    duration = transcript.get('duration', video_info.get('duration', 0))

    prompt = f"""You are a world-class viral content strategist.

Analyze this transcript and identify the {Config.MAX_CLIPS} best clips for TikTok/Reels/Shorts.

VIDEO: {video_info.get('title','')} | {duration:.0f}s | {video_info.get('channel','')}

TRANSCRIPT:
{transcript_text}

Pick moments with: strong hooks, emotional peaks, surprising insights, humor, or controversy.
Clips should be 15–90 seconds with natural start/end points.

Return ONLY a valid JSON array — no markdown. Each item:
{{"start":<float>,"end":<float>,"viral_score":<int 0-100>,"title":"<string>","hook":"<string>","caption":"<string with hashtags>","reason":"<string>","type":"<emotion|story|insight|humor|value|controversy>"}}"""

    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=3000,
        messages=[{"role":"user","content":prompt}]
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r'^```(?:json)?\s*','',raw); raw = re.sub(r'\s*```$','',raw)
    return json.loads(raw)


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI
# ─────────────────────────────────────────────────────────────────────────────

def _detect_with_openai(transcript, video_info):
    from openai import OpenAI
    client = OpenAI(api_key=Config.OPENAI_API_KEY)
    transcript_text = build_transcript_text(transcript)
    duration = transcript.get('duration', video_info.get('duration', 0))

    prompt = f"""Viral content strategist. Find {Config.MAX_CLIPS} best clips (15-90s each).
VIDEO: {video_info.get('title','')} | {duration:.0f}s
{transcript_text}
Return JSON array only. Each: start,end,viral_score,title,hook,caption,reason,type"""

    r = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role":"user","content":prompt}],
        response_format={"type":"json_object"}, max_tokens=3000,
    )
    parsed = json.loads(r.choices[0].message.content)
    if isinstance(parsed, dict):
        for k in ('clips','results','data'):
            if k in parsed and isinstance(parsed[k], list): return parsed[k]
        return list(parsed.values())[0] if parsed else []
    return parsed


# ─────────────────────────────────────────────────────────────────────────────
# Heuristic fallback
# ─────────────────────────────────────────────────────────────────────────────

VIRAL_HIGH = ['secret','never told','nobody knows','shocking','reveal','actually',
    'truth','mistake','wrong','surprising','incredible','you need to',
    'most people','hack','trick','stop','wait','this is why','reason why',
    'simple','easy',"don't",'important','serious','listen','pay attention']
VIRAL_MED  = ['because','but','however','really','truly','exactly','perfect',
    'amazing','incredible','love','hate','money','success','fail','win',
    'lose','best','worst']

def _detect_heuristic(transcript, video_info):
    segments   = transcript.get('segments', [])
    total_dur  = transcript.get('duration', video_info.get('duration', 0))
    if not segments: return []

    scored = []
    for i, seg in enumerate(segments):
        t = seg['text'].lower(); score = 50
        for kw in VIRAL_HIGH: score += 8 if kw in t else 0
        for kw in VIRAL_MED:  score += 3 if kw in t else 0
        score += min(t.count('?') * 5, 15)
        score += min(t.count('!') * 3, 10)
        rel = seg['start'] / max(total_dur, 1)
        if 0.05 < rel < 0.9: score += 5
        scored.append({'idx':i,'score':min(score,95),'seg':seg})

    scored.sort(key=lambda x: x['score'], reverse=True)
    clips = []; used = []
    for item in scored:
        if len(clips) >= Config.MAX_CLIPS: break
        idx = item['idx']; seg = item['seg']
        start = max(0, seg['start'] - 5); end = seg['end']
        j = idx
        while j < len(segments) and (segments[j]['end'] - start) < 50:
            end = segments[j]['end']; j += 1
        end = min(end, total_dur)
        if (end-start) < Config.MIN_CLIP_DURATION: continue
        overlap = any(min(end,ue)-max(start,us) > 0 and
                      (min(end,ue)-max(start,us))/(end-start) > 0.20
                      for us,ue in used)
        if overlap: continue
        used.append((start,end))
        txt = seg['text'].strip()
        clips.append({
            'start': round(start,2), 'end': round(end,2),
            'viral_score': min(item['score'],95),
            'title': _title(txt), 'hook': txt[:100],
            'caption': _caption(txt, video_info),
            'reason': '⚡ heuristic engine · keyword + energy signals',
            'type': _type(txt),
        })
    return clips

def _type(t):
    t=t.lower()
    if any(w in t for w in ['laugh','funny','joke']): return 'humor'
    if any(w in t for w in ['story','when i','remember']): return 'story'
    if any(w in t for w in ['tip','hack','secret','how to']): return 'value'
    if any(w in t for w in ['feel','love','hate','amazing']): return 'emotion'
    return 'insight'

def _title(t):
    w = t.strip().split()[:8]; s = ' '.join(w)
    return (s[:47]+'...') if len(s)>50 else s.capitalize()

def _caption(t, vi):
    s = t.strip()[:120]; ch = vi.get('channel','')
    tags = '#shorts #viral #trending #fyp #reels'
    return (f'"{s}" — {ch}\n\n{tags}' if ch else f'"{s}"\n\n{tags}')


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def _validate_clips(clips, duration):
    valid = []
    for c in clips:
        s = max(0.0, float(c.get('start',0)))
        e = min(float(duration or 999999), float(c.get('end', s+30)))
        l = e - s
        if l < Config.MIN_CLIP_DURATION: e = s + Config.MIN_CLIP_DURATION
        if l > Config.MAX_CLIP_DURATION: e = s + Config.MAX_CLIP_DURATION
        if duration > 0 and e > duration:
            e = duration; s = max(0, e - Config.MIN_CLIP_DURATION - 5)
        c['start']    = round(s,2)
        c['end']      = round(e,2)
        c['duration'] = round(e-s,2)
        c.setdefault('viral_score', 70)
        c.setdefault('title',   'Viral Clip')
        c.setdefault('hook',    '')
        c.setdefault('caption', '#shorts #viral #trending')
        c.setdefault('reason',  '')
        c.setdefault('type',    'insight')
        c['viral_score'] = max(0, min(100, int(c['viral_score'])))
        if c['duration'] >= Config.MIN_CLIP_DURATION:
            valid.append(c)
    return valid[:Config.MAX_CLIPS]
