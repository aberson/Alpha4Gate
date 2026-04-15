#!/usr/bin/env bash
# Dev-mode backend: uvicorn with --reload watching src/.
# See dev-serve.ps1 for details. Do NOT use during active soaks.
set -euo pipefail

cd "$(dirname "$0")/.."
echo "[dev-serve] CWD: $(pwd)"

PORT="${WEB_UI_PORT:-8765}"
echo "[dev-serve] Launching uvicorn --reload on :$PORT (watching src/)"

exec uv run uvicorn alpha4gate.api:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --reload \
    --reload-dir src
