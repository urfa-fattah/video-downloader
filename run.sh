#!/usr/bin/env bash
# Grabbit — one-command launcher for Linux / macOS
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[!] python3 not found. Install Python 3.9+ first."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[*] Creating virtual environment..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
echo "[*] Installing / updating dependencies..."
python -m pip install --quiet --upgrade pip
python -m pip install --quiet --upgrade -r requirements.txt

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[!] FFmpeg not found. HD merge and MP3 need it. See README for install steps."
fi

python app.py
