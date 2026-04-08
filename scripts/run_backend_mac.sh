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

# Reduce native BLAS/OpenMP thread fights (helps odd crashes on Apple Silicon with Open3D).
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export VECLIB_MAXIMUM_THREADS="${VECLIB_MAXIMUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
# Allow ops not implemented on MPS to fall back to CPU automatically.
export PYTORCH_ENABLE_MPS_FALLBACK="${PYTORCH_ENABLE_MPS_FALLBACK:-1}"

HOST="${BACKEND_HOST:-0.0.0.0}"
PORT="${BACKEND_PORT:-8001}"
RELOAD_FLAG=()
if [[ "${UVICORN_RELOAD:-0}" == "1" ]]; then
  RELOAD_FLAG=(--reload)
fi

echo "Starting uvicorn on http://${HOST}:${PORT} (cwd: $ROOT)"
echo "Tip: set UVICORN_RELOAD=1 for dev reload; leave unset for stability."
exec python -m uvicorn app:app --host "$HOST" --port "$PORT" "${RELOAD_FLAG[@]}"
