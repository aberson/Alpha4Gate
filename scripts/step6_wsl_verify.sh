#!/usr/bin/env bash
# Step 6 done-when verification — Linux dev-deps install + uv sync.
# Sourced from a file (NOT inlined into `wsl -- bash -lc`) per
# feedback_wsl_bash_lc_heredoc_fragile.md. Reusable for future contributors.
set -e

cd /mnt/c/Users/abero/dev/Alpha4Gate

echo "=== distro VERSION_ID ==="
grep VERSION_ID /etc/os-release

echo "=== env-var propagation (UV_PROJECT_ENVIRONMENT / SC2PATH / SC2_WSL_DETECT) ==="
printenv UV_PROJECT_ENVIRONMENT SC2PATH SC2_WSL_DETECT

echo "=== pytest --collect-only ==="
uv run pytest --collect-only 2>&1 | tail -5

echo "=== mypy --strict ==="
uv run mypy src bots --strict 2>&1 | tail -3

echo "=== ruff check . ==="
uv run ruff check .
