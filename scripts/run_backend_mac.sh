#!/usr/bin/env bash
# Run Pipe Layout FastAPI on macOS (same as Windows backend folder layout).
set -euo pipefail

ROOT="${PIPE_BACKEND_DIR:-$HOME/pipe-layout-backend}"
cd "$ROOT"

if [[ ! -f app.py ]]; then
  echo "Expected app.py in $ROOT"
  echo "Set PIPE_BACKEND_DIR or copy your backend folder to ~/pipe-layout-backend"
  exit 1
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip
if [[ -f requirements.txt ]]; then
  pip install -q -r requirements.txt
else
  pip install -q fastapi "uvicorn[standard]" numpy open3d pydantic torch python-multipart
fi

HOST="${BACKEND_HOST:-0.0.0.0}"
PORT="${BACKEND_PORT:-8001}"
echo "Starting uvicorn on http://${HOST}:${PORT} (cwd: $ROOT)"
exec python -m uvicorn app:app --host "$HOST" --port "$PORT" --reload
