#!/usr/bin/env bash
# Start Goop. Uses saved Garmin tokens / GARMIN_* env vars if present, else demo mode.
# garminconnect needs Python 3.12+, so pick the newest available interpreter.
set -e
cd "$(dirname "$0")"
PY=""
for c in python3.13 /opt/homebrew/bin/python3.13 python3.12 /opt/homebrew/bin/python3.12 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)'; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "Goop needs Python 3.12 or newer. Install it with: brew install python@3.13"; exit 1; }
if [ ! -f .venv/.goop_ok ] || ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' 2>/dev/null; then
  rm -rf .venv
  "$PY" -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  touch .venv/.goop_ok
fi
exec .venv/bin/python server.py
