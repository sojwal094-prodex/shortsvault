"""
main.py — ViralClipper CLI
Usage:
    python main.py https://youtube.com/watch?v=...
    python main.py https://youtu.be/abc123 --clips 5 --whisper medium
"""

import os
import sys
import argparse
import json
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from config import Config
from pipeline.downloader import download_video
from pipeline.transcriber import transcribe
from pipeline.clip_detector import detect_clips
from pipeline.video_processor import process_clip, check_ffmpeg


def print_banner():
    print("\n" + "═"*55)
    print("  ⚡ ViralClipper — Turn Any Video Into Viral Shorts")
    print("═"*55)
    print(f"  AI engine  : {Config.effective_ai_provider()}")
    print(f"  Whisper    : {Config.WHISPER_MODE} ({Config.WHISPER_MODEL})")
    print(f"  FFmpeg     : {'✓' if check_ffmpeg() else '✗ NOT FOUND'}")
    print(f"  Output     : {os.path.abspath(Config.OUTPUT_DIR)}")
    print("═"*55 + "\n")


def step(label, num, total=5):
    bar = "█" * num + "░" * (total - num)
    print(f"\n[{bar}] {label}")


def run(url: str, max_clips: int = None, brand_kit: dict = None):
    if not check_ffmpeg():
        print("✗ FFmpeg not found. Install it first: https://ffmpeg.org/download.html")
        sys.exit(1)

    if max_clips:
        Config.MAX_CLIPS = max_clips

    job_id    = str(uuid.uuid4())[:8]
    brand_kit = brand_kit or {}

    def cb(msg, pct=None):
        bar = ""
        if pct is not None:
            filled = int(pct / 5)
            bar    = f"[{'█'*filled}{'░'*(20-filled)}] {pct}%  "
        print(f"  {bar}{msg}")

    # ── 1. Download ───────────────────────────────────────────────────────────
    step("Downloading video", 1)
    video_info = download_video(url, job_id, cb)
    print(f"  ✓ Title    : {video_info['title']}")
    print(f"  ✓ Duration : {video_info['duration']:.0f}s ({video_info['duration']/60:.1f} min)")

    # ── 2. Transcribe ─────────────────────────────────────────────────────────
    step("Transcribing audio", 2)
    transcript = transcribe(video_info['filepath'], progress_cb=cb)
    print(f"  ✓ Language : {transcript['language']}")
    print(f"  ✓ Words    : {len(transcript['words'])}")

    # ── 3. Detect clips ───────────────────────────────────────────────────────
    step("Detecting viral moments", 3)
    clips = detect_clips(transcript, video_info, progress_cb=cb)
    print(f"  ✓ Found {len(clips)} clips")

    # ── 4. Process clips ──────────────────────────────────────────────────────
    step("Rendering clips", 4)
    out_clips = []
    for clip in clips:
        print(f"\n  → Clip {clip['index']}: {clip['title']}")
        print(f"    Score: {clip['viral_score']}/100 | {clip['start']:.1f}s → {clip['end']:.1f}s | {clip['type']}")
        file_info = process_clip(
            source_path=video_info['filepath'],
            clip=clip,
            transcript=transcript,
            job_id=job_id,
            brand_kit=brand_kit,
            progress_cb=cb,
        )
        print(f"    ✓ Saved: {file_info['filename']} ({file_info['size_mb']} MB)")
        out_clips.append({**clip, 'file': file_info})

    # ── 5. Summary ────────────────────────────────────────────────────────────
    step("Done!", 5)
    out_dir = os.path.join(Config.OUTPUT_DIR, job_id, "clips")
    print(f"\n  {'─'*45}")
    print(f"  {'CLIP':<4} {'SCORE':>5}  {'DURATION':>8}  TITLE")
    print(f"  {'─'*45}")
    for c in out_clips:
        dur = c['end'] - c['start']
        print(f"  {c['index']:<4} {c['viral_score']:>5}/100  {dur:>5.0f}s     {c['title'][:35]}")
    print(f"  {'─'*45}")
    print(f"\n  📁 All clips saved to:\n     {os.path.abspath(out_dir)}\n")

    # Save manifest
    manifest_path = os.path.join(Config.OUTPUT_DIR, job_id, "manifest.json")
    with open(manifest_path, 'w') as f:
        json.dump({
            'job_id':     job_id,
            'url':        url,
            'video_info': video_info,
            'clips':      out_clips,
        }, f, indent=2, default=str)
    print(f"  📋 Manifest : {manifest_path}\n")

    return out_clips


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='ViralClipper — Turn any video into viral shorts',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py https://youtube.com/watch?v=abc123
  python main.py https://youtu.be/abc123 --clips 5
  python main.py https://youtu.be/abc123 --whisper medium --channel "@mychannel"
  python main.py --batch urls.txt
        """
    )
    parser.add_argument('url',         nargs='?', help='Video URL to process')
    parser.add_argument('--batch',     metavar='FILE', help='Text file with one URL per line')
    parser.add_argument('--clips',     type=int,  default=None, help='Max clips to generate (default: 7)')
    parser.add_argument('--whisper',   choices=['tiny','base','small','medium','large-v3'],
                        help='Whisper model size (overrides .env)')
    parser.add_argument('--channel',   default='', help='Channel name for watermark')
    parser.add_argument('--font',      default='Arial', help='Caption font')
    parser.add_argument('--no-reframe',action='store_true', help='Skip 9:16 reframe')

    args = parser.parse_args()

    if args.whisper:
        Config.WHISPER_MODEL = args.whisper

    brand_kit = {
        'channel_name': args.channel,
        'font':         args.font,
    }

    print_banner()

    if args.batch:
        # Batch mode
        try:
            with open(args.batch) as f:
                urls = [l.strip() for l in f if l.strip()]
        except FileNotFoundError:
            print(f"✗ Batch file not found: {args.batch}")
            sys.exit(1)
        print(f"Batch mode: processing {len(urls)} URLs\n")
        for i, u in enumerate(urls, 1):
            print(f"\n{'='*55}")
            print(f"  VIDEO {i}/{len(urls)}: {u}")
            print('='*55)
            try:
                run(u, args.clips, brand_kit)
            except Exception as e:
                print(f"  ✗ Failed: {e}")

    elif args.url:
        try:
            run(args.url, args.clips, brand_kit)
        except KeyboardInterrupt:
            print("\n\n  Interrupted by user.")
        except Exception as e:
            print(f"\n  ✗ Error: {e}")
            import traceback; traceback.print_exc()
            sys.exit(1)

    else:
        parser.print_help()
        print("\n  TIP: To use the web UI instead, run:  python app.py\n")
