#!/usr/bin/env pwsh
# Dev-mode backend: uvicorn with --reload watching src/.
# Auto-restarts the FastAPI app whenever a .py file under src/ changes,
# so code edits take effect without a manual restart.
#
# DO NOT USE during active soaks: a reload will interrupt any in-flight
# SC2 game and daemon cycle. Use plain `uv run python -m alpha4gate.runner --serve`
# for soak runs.
#
# Usage (from repo root):
#   pwsh scripts/dev-serve.ps1
#
# The server binds to host/port from .env (defaults: 0.0.0.0:8765).

$ErrorActionPreference = "Stop"
$env:PYTHONUNBUFFERED = "1"

# Resolve repo root so relative DATA_DIR / LOG_DIR / REPLAY_DIR work.
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
Write-Host "[dev-serve] CWD: $repoRoot"

# Read port from env with 8765 default.
$port = if ($env:WEB_UI_PORT) { $env:WEB_UI_PORT } else { "8765" }
Write-Host "[dev-serve] Launching uvicorn --reload on :$port (watching src/)"

uv run uvicorn alpha4gate.api:app `
    --host 0.0.0.0 `
    --port $port `
    --reload `
    --reload-dir src
