"""
learner.py — Self-learning viral clip predictor

How it works:
  1. Every processed clip → extract features → save to SQLite
  2. After every 5 new clips → auto-retrain the RandomForest model
  3. Future predictions use the trained model (no API needed)
  4. Accuracy improves with every video you process
"""

import os
import re
import math
import pickle
import json
from config import Config
from pipeline.database import get_training_data, set_meta, get_stats

MODEL_PATH = os.path.join(Config.OUTPUT_DIR, "viralclipper_model.pkl")

# ── Viral signal word lists ───────────────────────────────────────────────────
HOOK_WORDS = [
    'secret', 'never told', 'nobody knows', 'shocking', 'reveal', 'actually',
    'truth', 'mistake', 'wrong', 'surprising', 'incredible', 'you need to',
    'most people', 'changed my life', 'hack', 'trick', 'stop', 'wait',
    'this is why', 'reason why', 'simple', 'easy', "don't", 'important',
    'serious', 'listen', 'pay attention', 'mindblowing', 'insane', 'crazy',
    'unbelievable', 'exposed', 'hidden', 'forbidden', 'controversial',
]
EMOTION_WORDS = [
    'love', 'hate', 'amazing', 'terrible', 'best', 'worst', 'incredible',
    'beautiful', 'awful', 'fantastic', 'horrible', 'perfect', 'destroy',
    'win', 'lose', 'fail', 'succeed', 'afraid', 'angry', 'happy', 'sad',
    'excited', 'shocked', 'disgusted', 'surprised', 'proud', 'ashamed',
]


# ─────────────────────────────────────────────────────────────────────────────
# Feature extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_features(text: str, start: float, end: float, video_duration: float) -> dict:
    """
    Turn a clip's transcript text + timing into a numeric feature vector.
    This is the core representation the ML model learns from.
    """
    text_lower = text.lower()
    words      = text_lower.split()
    sentences  = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    duration   = max(end - start, 0.1)

    # 1. Keyword signals
    keyword_score = 0
    for kw in HOOK_WORDS:
        if kw in text_lower:
            keyword_score += 1
    keyword_score = min(keyword_score, 10)  # cap at 10

    # 2. Position in video (0 = start, 1 = end)
    position_pct = start / max(video_duration, 1)

    # 3. Speaking rate (words per second)
    words_per_sec = len(words) / duration if words else 0

    # 4. Punctuation energy
    question_count     = text.count('?')
    exclamation_count  = text.count('!')

    # 5. Hook words specifically
    hook_word_count = sum(1 for w in HOOK_WORDS if w in text_lower)

    # 6. Emotion words
    emotion_score = sum(1 for w in EMOTION_WORDS if w in text_lower)

    # 7. Vocabulary
    avg_word_length = sum(len(w) for w in words) / max(len(words), 1)

    # 8. Structure
    sentence_count = len(sentences)

    # 9. Position flags
    near_start = 1 if position_pct < 0.20 else 0
    near_end   = 1 if position_pct > 0.85 else 0

    # 10. Text length
    text_length = len(text)

    # 11. Combined energy score
    energy_score = (
        keyword_score * 2
        + question_count * 1.5
        + exclamation_count * 1.0
        + emotion_score * 0.8
        + hook_word_count * 2.5
        - (1 if near_end else 0) * 5   # penalise outro segments
    )
    energy_score = max(0, energy_score)

    return {
        'text':              text[:500],
        'keyword_score':     keyword_score,
        'position_pct':      round(position_pct, 3),
        'words_per_sec':     round(words_per_sec, 2),
        'question_count':    question_count,
        'exclamation_count': exclamation_count,
        'hook_word_count':   hook_word_count,
        'emotion_score':     emotion_score,
        'avg_word_length':   round(avg_word_length, 2),
        'sentence_count':    sentence_count,
        'near_start':        near_start,
        'near_end':          near_end,
        'text_length':       text_length,
        'energy_score':      round(energy_score, 2),
    }


def _row_to_vector(row):
    """Convert a database row to an ML feature list (same order every time)."""
    return [
        row['keyword_score'],
        row['position_pct'],
        row['words_per_sec'],
        row['question_count'],
        row['exclamation_count'],
        row['hook_word_count'],
        row['emotion_score'],
        row['avg_word_length'],
        row['sentence_count'],
        row['near_start'],
        row['near_end'],
        row['text_length'],
        row['energy_score'],
    ]

def features_to_vector(f: dict) -> list:
    """Convert extract_features() output to ordered ML vector."""
    return [
        f['keyword_score'],
        f['position_pct'],
        f['words_per_sec'],
        f['question_count'],
        f['exclamation_count'],
        f['hook_word_count'],
        f['emotion_score'],
        f['avg_word_length'],
        f['sentence_count'],
        f['near_start'],
        f['near_end'],
        f['text_length'],
        f['energy_score'],
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Model training
# ─────────────────────────────────────────────────────────────────────────────

def train_model() -> dict:
    """
    Retrain the RandomForest on all stored clips.
    Returns {'trained': bool, 'clips': int, 'accuracy': float, 'top_features': list}
    """
    rows = get_training_data()
    if len(rows) < 10:
        return {'trained': False, 'clips': len(rows), 'reason': f'Need 10 clips, have {len(rows)}'}

    try:
        from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
        from sklearn.model_selection import cross_val_score
        from sklearn.preprocessing import StandardScaler
        import numpy as np
    except ImportError:
        return {'trained': False, 'reason': 'scikit-learn not installed'}

    X = [_row_to_vector(r) for r in rows]
    y = [r['viral_score'] for r in rows]

    X = np.array(X)
    y = np.array(y, dtype=float)

    # Weight API-sourced clips 3x (they're higher quality labels)
    sample_weights = np.array([
        3.0 if 'api' in (r['score_source'] or '') else 1.0
        for r in rows
    ])

    # Use GradientBoosting when enough data, RF otherwise
    if len(rows) >= 50:
        model = GradientBoostingRegressor(
            n_estimators=100, max_depth=4, learning_rate=0.1, random_state=42
        )
    else:
        model = RandomForestRegressor(
            n_estimators=100, max_depth=6, random_state=42, n_jobs=-1
        )

    model.fit(X, y, sample_weight=sample_weights)

    # Cross-val accuracy estimate (R² score, clamped 0–100)
    if len(rows) >= 15:
        scores = cross_val_score(model, X, y, cv=min(5, len(rows)//3),
                                  scoring='r2')
        accuracy = max(0, float(np.mean(scores))) * 100
    else:
        accuracy = 50.0  # not enough data to estimate yet

    # Feature importance
    feature_names = [
        'keyword_score', 'position_pct', 'words_per_sec',
        'question_count', 'exclamation_count', 'hook_word_count',
        'emotion_score', 'avg_word_length', 'sentence_count',
        'near_start', 'near_end', 'text_length', 'energy_score',
    ]
    importances = model.feature_importances_
    top_features = sorted(
        zip(feature_names, importances),
        key=lambda x: x[1], reverse=True
    )[:5]

    # Save model
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    model_data = {
        'model':        model,
        'clips_trained': len(rows),
        'accuracy':     accuracy,
        'top_features': top_features,
    }
    with open(MODEL_PATH, 'wb') as f:
        pickle.dump(model_data, f)

    # Update DB meta
    set_meta('accuracy', round(accuracy, 1))
    set_meta('version',  len(rows))

    return {
        'trained':      True,
        'clips':        len(rows),
        'accuracy':     round(accuracy, 1),
        'top_features': [(n, round(float(i)*100, 1)) for n, i in top_features],
    }


def load_model():
    """Load the saved model dict. Returns None if no model exists yet."""
    if not os.path.exists(MODEL_PATH):
        return None
    try:
        with open(MODEL_PATH, 'rb') as f:
            return pickle.load(f)
    except Exception:
        return None


def should_retrain() -> bool:
    """Return True if enough new clips have been added since last training."""
    stats     = get_stats()
    total     = stats['total_clips']
    last_ver  = stats['model_version']   # = clips trained on
    return total >= 10 and (total - last_ver) >= 5


# ─────────────────────────────────────────────────────────────────────────────
# Prediction (local inference, no API)
# ─────────────────────────────────────────────────────────────────────────────

def predict_clips_local(transcript: dict, video_info: dict) -> list:
    """
    Predict viral scores using the trained model.
    Falls back to energy_score heuristic if model not ready.
    Returns same format as detect_clips().
    """
    model_data = load_model()
    duration   = transcript.get('duration', video_info.get('duration', 0))
    segments   = transcript.get('segments', [])

    if not segments:
        return []

    # Extract features for all segments
    scored = []
    for i, seg in enumerate(segments):
        text     = seg.get('text', '')
        f        = extract_features(text, seg['start'], seg['end'], duration)
        vec      = features_to_vector(f)

        if model_data:
            try:
                import numpy as np
                score = float(model_data['model'].predict([vec])[0])
                score = max(0, min(100, score))
                source = 'model'
            except Exception:
                score  = _heuristic_score(f)
                source = 'heuristic'
        else:
            score  = _heuristic_score(f)
            source = 'heuristic'

        scored.append({
            'idx':    i,
            'score':  score,
            'seg':    seg,
            'feat':   f,
            'source': source,
        })

    # Pick top non-overlapping clips
    scored.sort(key=lambda x: x['score'], reverse=True)
    clips      = []
    used       = []
    target_len = 45  # default clip length seconds

    for item in scored:
        if len(clips) >= Config.MAX_CLIPS:
            break

        seg   = item['seg']
        start = max(0, seg['start'] - 3)
        end   = seg['end']
        idx   = item['idx']

        # Extend forward to target_len
        j = idx
        while j < len(segments) and (segments[j]['end'] - start) < target_len:
            end = segments[j]['end']
            j += 1
        end = min(end, duration)

        # Skip if too short
        if (end - start) < Config.MIN_CLIP_DURATION:
            continue

        # Skip if heavily overlapping
        overlap = False
        for (us, ue) in used:
            ov = min(end, ue) - max(start, us)
            if ov > 0 and ov / (end - start) > 0.25:
                overlap = True
                break
        if overlap:
            continue

        used.append((start, end))
        f = item['feat']

        clips.append({
            'start':       round(start, 2),
            'end':         round(end, 2),
            'duration':    round(end - start, 2),
            'viral_score': int(min(95, item['score'])),
            'title':       _make_title(seg['text']),
            'hook':        seg['text'].strip()[:100],
            'caption':     _make_caption(seg['text'], video_info),
            'reason':      _make_reason(f, item['source']),
            'type':        _infer_type(seg['text']),
            '_features':   f,
            '_source':     item['source'],
        })

    return clips


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _heuristic_score(f: dict) -> float:
    return min(95, 40
               + f['keyword_score'] * 3
               + f['question_count'] * 4
               + f['exclamation_count'] * 2
               + f['hook_word_count'] * 4
               + f['emotion_score'] * 2
               + (5 if f['near_start'] else 0)
               - (8 if f['near_end'] else 0))


def _infer_type(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ['laugh','funny','lol','joke','hilarious']): return 'humor'
    if any(w in t for w in ['story','when i','one day','remember','once']): return 'story'
    if any(w in t for w in ['tip','hack','secret','trick','how to']): return 'value'
    if any(w in t for w in ['feel','emotion','love','hate','amazing']): return 'emotion'
    return 'insight'


def _make_title(text: str) -> str:
    words = text.strip().split()[:8]
    t = ' '.join(words)
    return (t[:47] + '...') if len(t) > 50 else t.capitalize()


def _make_caption(text: str, video_info: dict) -> str:
    snippet = text.strip()[:120]
    ch      = video_info.get('channel', '')
    tags    = '#shorts #viral #trending #fyp #reels'
    return (f'"{snippet}" — {ch}\n\n{tags}' if ch else f'"{snippet}"\n\n{tags}')


def _make_reason(f: dict, source: str) -> str:
    signals = []
    if f['hook_word_count'] > 0:   signals.append('power hook words')
    if f['emotion_score']  > 1:    signals.append('emotional language')
    if f['question_count'] > 0:    signals.append('engagement question')
    if f['keyword_score']  > 2:    signals.append('viral keywords')
    if f['near_start']:            signals.append('early video placement')
    if not signals:                signals.append('strong pacing')
    label = '🤖 model' if source == 'model' else '⚡ heuristic'
    return f"{label} · " + ', '.join(signals[:3])
