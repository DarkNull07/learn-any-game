#!/bin/sh
# gamekit (macOS/Linux): learn any phone game from one recorded session. Run "./gamekit.sh help" for the steps.
GK="$(cd "$(dirname "$0")" && pwd)"
PY=python3
[ -x "$GK/.venv/bin/python" ] && PY="$GK/.venv/bin/python"
exec "$PY" "$GK/gamekit.py" "$@"
