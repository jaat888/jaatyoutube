#!/bin/sh
# Bina Docker ke chalane ke liye: sh start.sh
# Pehle: API_KEY set karo, Python 3 aur node hona chahiye.
set -e
[ -z "$API_KEY" ] && echo "WARNING: API_KEY set nahi hai, server sabke liye khula hai"
command -v node >/dev/null 2>&1 || echo "WARNING: node nahi mila (signature solve ke liye chahiye)"
pip install -q -U -r requirements.txt
exec gunicorn -w 1 --threads 8 --timeout 120 -b 0.0.0.0:${PORT:-8080} app:app
