#!/usr/bin/env bash
# Step 5 verification — runs pytest + mypy in WSL Ubuntu-22.04 against the
# Linux ext4 venv. Sourced from a file (NOT inlined into `wsl -- bash -lc`)
# per feedback_wsl_bash_lc_heredoc_fragile.md.
set -e

cd /mnt/c/Users/abero/dev/Alpha4Gate

echo "=== Linux pytest (Step 5 specific) ==="
uv run pytest tests/test_sc2path_fallback.py -v

echo "=== Linux pytest (full suite) ==="
uv run pytest 2>&1 | tail -5

echo "=== Linux mypy --strict ==="
uv run mypy src bots --strict 2>&1 | tail -3

echo "=== Linux paths.py round-trip (env unset) ==="
unset SC2PATH
uv run python -c 'from orchestrator.paths import resolve_sc2_path; print("native-linux fallback:", resolve_sc2_path())'
SC2PATH=/home/abero/StarCraftII uv run python -c 'from orchestrator.paths import resolve_sc2_path; print("env override:", resolve_sc2_path())'
