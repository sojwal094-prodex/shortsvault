"""
downloader.py — Download videos from YouTube, Twitch, Google Drive, Dropbox
Uses yt-dlp under the hood. No API key required.
"""

import os
import re
import sys
import yt_dlp
from config import Config


def _progress_hook(d, callback=None):
    if d['status'] == 'downloading':
        pct = d.get('_percent_str', '?').strip()
        speed = d.get('_speed_str', '?').strip()
        msg = f"Downloading: {pct} at {speed}"
        if callback:
            callback(msg, 10)
    elif d['status'] == 'finished':
        if callback:
            callback("Download complete, processing...", 15)


def download_video(url: str, job_id: str, progress_cb=None) -> dict:
    """
    Download a video from the given URL.

    Returns:
        {
          'title': str,
          'duration': float,  # seconds
          'filepath': str,    # absolute path to downloaded .mp4
          'thumbnail': str,   # URL of thumbnail
          'channel': str,
        }
    """
    out_dir = os.path.join(Config.OUTPUT_DIR, job_id, "source")
    os.makedirs(out_dir, exist_ok=True)

    if progress_cb:
        progress_cb("Starting download...", 5)

    # Base options — use iOS + TV clients to bypass YouTube bot detection on server IPs
    ydl_opts = {
        'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best',
        'outtmpl': os.path.join(out_dir, '%(id)s.%(ext)s'),
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [lambda d: _progress_hook(d, progress_cb)],
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }],
        # Bypass bot detection: use iOS/TV player clients instead of web
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'tv_embedded', 'web'],
            }
        },
        # Rotate user-agent to look like a real browser
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 '
                'Mobile/15E148 Safari/604.1'
            ),
        },
    }

    # Google Drive special handling
    if 'drive.google.com' in url:
        ydl_opts['format'] = 'best'
        del ydl_opts['extractor_args']   # not needed for Drive

    # Optional cookies file (place cookies.txt in OUTPUT_DIR to bypass age-gating)
    cookies_path = os.path.join(Config.OUTPUT_DIR, 'cookies.txt')
    if os.path.exists(cookies_path):
        ydl_opts['cookiefile'] = cookies_path

    # Try primary options; if still blocked, retry with android client
    def _try_download(opts):
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True), ydl

    try:
        info, ydl = _try_download(ydl_opts)
    except yt_dlp.utils.DownloadError as e:
        if 'Sign in' in str(e) or 'bot' in str(e).lower():
            if progress_cb:
                progress_cb("Retrying with Android client...", 6)
            fallback = dict(ydl_opts)
            fallback['extractor_args'] = {'youtube': {'player_client': ['android', 'ios']}}
            info, ydl = _try_download(fallback)
        else:
            raise

    # info already fetched above — just resolve the filepath
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        filepath = ydl.prepare_filename(info)
        if not filepath.endswith('.mp4'):
            filepath = os.path.splitext(filepath)[0] + '.mp4'

        # Walk dir to find it if the name changed during merge
        if not os.path.exists(filepath):
            for f in os.listdir(out_dir):
                if f.endswith('.mp4'):
                    filepath = os.path.join(out_dir, f)
                    break

        duration = info.get('duration') or 0
        if duration > Config.MAX_VIDEO_DURATION:
            raise ValueError(
                f"Video is {duration/3600:.1f}h long. "
                f"Max allowed is {Config.MAX_VIDEO_DURATION/3600:.1f}h. "
                "Edit MAX_VIDEO_DURATION in .env to increase the limit."
            )

        return {
            'title':     info.get('title', 'Untitled'),
            'duration':  duration,
            'filepath':  os.path.abspath(filepath),
            'thumbnail': info.get('thumbnail', ''),
            'channel':   info.get('uploader', info.get('channel', '')),
            'view_count': info.get('view_count', 0),
            'description': info.get('description', '')[:500],
        }


def is_supported_url(url: str) -> bool:
    """Quick check if the URL looks like something yt-dlp can handle."""
    patterns = [
        r'youtube\.com/watch',
        r'youtu\.be/',
        r'twitch\.tv/',
        r'drive\.google\.com/',
        r'dropbox\.com/',
        r'vimeo\.com/',
        r'dailymotion\.com/',
        r'reddit\.com/',
        r'twitter\.com/',
        r'x\.com/',
        r'instagram\.com/',
        r'tiktok\.com/',
        r'facebook\.com/',
    ]
    return any(re.search(p, url, re.I) for p in patterns)
