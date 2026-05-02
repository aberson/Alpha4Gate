#!/usr/bin/env bash
# Launch a full live-test session: frontend, browser, and game.
# The API server starts automatically with the game (background thread).
#
# Usage:
#   bash scripts/live-test.sh                   # defaults: Simple64, difficulty 1, realtime
#   bash scripts/live-test.sh --map Acropolis    # custom map
#   bash scripts/live-test.sh --difficulty 3     # harder AI
#   bash scripts/live-test.sh --no-claude        # disable Claude advisor

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_PORT=5173
API_PORT="${WEB_UI_PORT:-8765}"

# --- 1. Start frontend dev server in background ---
echo "[live-test] Starting frontend dev server..."
cd "$PROJECT_DIR/frontend"
npm run dev -- --port "$FRONTEND_PORT" &
FRONTEND_PID=$!
cd "$PROJECT_DIR"

# Give Vite a moment to bind the port
sleep 3

# --- 2. Open browser ---
echo "[live-test] Opening browser at http://localhost:$FRONTEND_PORT ..."
if command -v start &>/dev/null; then
    start "http://localhost:$FRONTEND_PORT"       # Windows (Git Bash / MSYS2)
elif command -v xdg-open &>/dev/null; then
    xdg-open "http://localhost:$FRONTEND_PORT"    # Linux
elif command -v open &>/dev/null; then
    open "http://localhost:$FRONTEND_PORT"         # macOS
fi

# --- 3. Launch game (API server starts automatically in-process) ---
echo "[live-test] Launching game... (API on port $API_PORT)"
echo "[live-test] Game args: --realtime --difficulty 1 $*"
uv run python -m bots.current.runner --map Simple64 --difficulty 1 --realtime "$@"

# --- Cleanup ---
echo "[live-test] Game ended. Stopping frontend..."
kill "$FRONTEND_PID" 2>/dev/null || true
