#!/bin/sh
# Live probing tools (see probe.py). Example:  ./probe.sh test
cd "$(dirname "$0")"
PY="@@PYTHON@@"
[ -x .venv/bin/python ] && PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
exec "$PY" probe.py "$@"
