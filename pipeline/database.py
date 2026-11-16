"""
database.py — SQLite store for clip training data
Every processed clip gets saved here so the model can learn from it.
"""

import os
import json
import sqlite3
from datetime import datetime
from config import Config

DB_PATH = os.path.join(Config.OUTPUT_DIR, "viralclipper_brain.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    """Create tables if they don't exist."""
    with _conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS clips (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at       TEXT,
            video_title      TEXT,
            video_duration   REAL,
            clip_start       REAL,
            clip_end         REAL,
            clip_duration    REAL,
            clip_type        TEXT,
            viral_score      INTEGER,
            score_source     TEXT,      -- 'api_claude' | 'api_openai' | 'heuristic' | 'model'
            transcript_text  TEXT,

            -- Feature columns (the ML input vector)
            keyword_score    REAL,
            position_pct     REAL,
            words_per_sec    REAL,
            question_count   INTEGER,
            exclamation_count INTEGER,
            hook_word_count  INTEGER,
            emotion_score    REAL,
            avg_word_length  REAL,
            sentence_count   INTEGER,
            near_start       INTEGER,   -- 0/1
            near_end         INTEGER,   -- 0/1
            text_length      INTEGER,
            energy_score     REAL
        )""")

        c.execute("""
        CREATE TABLE IF NOT EXISTS model_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )""")
        c.commit()


def save_clip(clip: dict, video_info: dict, features: dict, score_source: str):
    """Persist one processed clip with its feature vector."""
    init_db()
    with _conn() as c:
        c.execute("""
        INSERT INTO clips (
            created_at, video_title, video_duration,
            clip_start, clip_end, clip_duration, clip_type,
            viral_score, score_source, transcript_text,
            keyword_score, position_pct, words_per_sec,
            question_count, exclamation_count, hook_word_count,
            emotion_score, avg_word_length, sentence_count,
            near_start, near_end, text_length, energy_score
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            datetime.utcnow().isoformat(),
            video_info.get('title', ''),
            video_info.get('duration', 0),
            clip.get('start', 0),
            clip.get('end',   0),
            clip.get('duration', clip.get('end', 0) - clip.get('start', 0)),
            clip.get('type', 'insight'),
            clip.get('viral_score', 50),
            score_source,
            features.get('text', '')[:500],
            features.get('keyword_score', 0),
            features.get('position_pct', 0.5),
            features.get('words_per_sec', 2.0),
            features.get('question_count', 0),
            features.get('exclamation_count', 0),
            features.get('hook_word_count', 0),
            features.get('emotion_score', 0),
            features.get('avg_word_length', 4.5),
            features.get('sentence_count', 1),
            int(features.get('near_start', 0)),
            int(features.get('near_end', 0)),
            features.get('text_length', 0),
            features.get('energy_score', 0),
        ))
        c.commit()


def get_training_data():
    """Return all stored clips as (feature_matrix, scores) for training."""
    init_db()
    with _conn() as c:
        rows = c.execute("""
            SELECT keyword_score, position_pct, words_per_sec,
                   question_count, exclamation_count, hook_word_count,
                   emotion_score, avg_word_length, sentence_count,
                   near_start, near_end, text_length, energy_score,
                   viral_score, score_source
            FROM clips
            ORDER BY created_at
        """).fetchall()
    return rows


def get_stats():
    """Return learning statistics for the UI."""
    init_db()
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        api_count = c.execute(
            "SELECT COUNT(*) FROM clips WHERE score_source LIKE 'api%'"
        ).fetchone()[0]
        avg_score = c.execute(
            "SELECT AVG(viral_score) FROM clips"
        ).fetchone()[0] or 0
        type_dist = c.execute(
            "SELECT clip_type, COUNT(*) as cnt FROM clips GROUP BY clip_type ORDER BY cnt DESC"
        ).fetchall()
        meta_acc = c.execute(
            "SELECT value FROM model_meta WHERE key='accuracy'"
        ).fetchone()
        meta_ver = c.execute(
            "SELECT value FROM model_meta WHERE key='version'"
        ).fetchone()

    return {
        "total_clips":    total,
        "api_clips":      api_count,
        "avg_score":      round(avg_score, 1),
        "type_dist":      {r['clip_type']: r['cnt'] for r in type_dist},
        "model_accuracy": float(meta_acc['value']) if meta_acc else None,
        "model_version":  int(meta_ver['value']) if meta_ver else 0,
        "model_ready":    total >= 10,
    }


def set_meta(key: str, value):
    init_db()
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO model_meta(key,value) VALUES(?,?)",
            (key, str(value))
        )
        c.commit()
