#!/bin/bash
# ViralClipper Installer — macOS / Linux

set -e

echo ""
echo "  ============================================="
echo "   ⚡ ViralClipper — Installer (Unix)"
echo "  ============================================="
echo ""

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "  [ERROR] Python 3 not found. Install from https://python.org"
    exit 1
fi
echo "  [OK] $(python3 --version)"

# Check pip
if ! python3 -m pip --version &>/dev/null; then
    echo "  [ERROR] pip not found. Install with: python3 -m ensurepip"
    exit 1
fi
echo "  [OK] pip found"

# Check ffmpeg
if ! command -v ffmpeg &>/dev/null; then
    echo ""
    echo "  [WARN] FFmpeg not found! Install it:"
    echo ""
    echo "    macOS  :  brew install ffmpeg"
    echo "    Ubuntu :  sudo apt install ffmpeg"
    echo "    Arch   :  sudo pacman -S ffmpeg"
    echo ""
else
    echo "  [OK] FFmpeg found"
fi

echo ""
echo "  Installing Python packages..."
python3 -m pip install -r requirements.txt --break-system-packages 2>/dev/null || \
python3 -m pip install -r requirements.txt

# Copy .env if not exists
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  [OK] Created .env  (edit it to add your API keys)"
else
    echo "  [OK] .env already exists"
fi

# Create output dir
mkdir -p output

echo ""
echo "  ============================================="
echo "   Installation complete!"
echo "  ============================================="
echo ""
echo "   Start web UI :  python3 app.py"
echo "   CLI mode     :  python3 main.py <youtube-url>"
echo ""
