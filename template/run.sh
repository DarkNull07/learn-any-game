#!/bin/sh
# Starts this game's bot (macOS/Linux). Examples:  ./run.sh --levels 20     ./run.sh --levels 3 --dry-run
cd "$(dirname "$0")"
PY="@@PYTHON@@"
[ -x .venv/bin/python ] && PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
exec "$PY" bot.py "$@"
