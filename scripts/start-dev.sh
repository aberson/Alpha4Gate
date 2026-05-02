#!/usr/bin/env bash
# Start backend + frontend dev servers for build-step --ui capture.
#
# Backend runs in background on :8765, frontend in foreground on :3000.
# build-step waits on the foreground process (frontend) and signals
# this script when capture is done. The trap kills the backend cleanly.
#
# Usage: bash scripts/start-dev.sh

set -euo pipefail

cleanup() {
  if [ -n "${BACKEND_PID:-}" ]; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# Start backend in background (FastAPI on :8765)
# bots.current is a MetaPathFinder alias to the active version (currently bots/v10/);
# the old src/alpha4gate/ package was deleted in Phase 1.
uv run python -m bots.current.runner --serve &
BACKEND_PID=$!

# Start frontend in foreground (Vite dev server on :3000, proxies to :8765).
# build-step polls --ready-url (backend) first, then --url (this).
cd frontend
npm run dev
