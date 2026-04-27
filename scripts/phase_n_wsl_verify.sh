#!/usr/bin/env bash
# Pre-flight check for the Phase N §7 soak on WSL2 Ubuntu 22.04.
# Confirms every prereq the soak will need; fails loudly if anything is missing.
# Sourced from a file (NOT inlined into `wsl -- bash -lc`) per
# feedback_wsl_bash_lc_heredoc_fragile.md.
set -u

cd /mnt/c/Users/abero/dev/Alpha4Gate

pass() { echo "  PASS: $*"; }
warn() { echo "  WARN: $*"; }
fail() { echo "  FAIL: $*"; }

echo "=== distro ==="
grep -E "^(NAME|VERSION_ID)=" /etc/os-release

echo
echo "=== Python 3.12 + uv ==="
if command -v python3.12 >/dev/null; then pass "python3.12 $(python3.12 --version)"; else fail "python3.12 not on PATH"; fi
if command -v uv >/dev/null; then pass "uv $(uv --version)"; else fail "uv not on PATH"; fi

echo
echo "=== Linux SC2 binary (~/StarCraftII/Versions) ==="
if [ -d "$HOME/StarCraftII" ]; then
  pass "~/StarCraftII exists"
  bin=$(find "$HOME/StarCraftII" -maxdepth 4 -name "SC2_x64" -type f 2>/dev/null | head -1)
  if [ -n "$bin" ]; then pass "binary: $bin"; else fail "no SC2_x64 binary under ~/StarCraftII"; fi
  ls "$HOME/StarCraftII/Maps" 2>/dev/null | head -3 || warn "no Maps dir"
else
  fail "~/StarCraftII missing — Spike 1 (Step 2) wasn't completed on this distro"
fi

echo
echo "=== ext4 venv (UV_PROJECT_ENVIRONMENT) ==="
echo "  UV_PROJECT_ENVIRONMENT=${UV_PROJECT_ENVIRONMENT:-<unset>}"
if [ -n "${UV_PROJECT_ENVIRONMENT:-}" ] && [ -d "${UV_PROJECT_ENVIRONMENT}" ]; then
  pass "venv at $UV_PROJECT_ENVIRONMENT exists"
  case "$UV_PROJECT_ENVIRONMENT" in
    /mnt/*) fail "venv on /mnt/c — feedback_uv_venv_must_be_on_ext4 says move to ~/..." ;;
    *) pass "venv on ext4 (not /mnt/c)" ;;
  esac
else
  warn "UV_PROJECT_ENVIRONMENT unset or path missing — uv sync will need to run"
fi

echo
echo "=== SC2 env vars (~/.profile sourced) ==="
echo "  SC2PATH=${SC2PATH:-<unset>}"
echo "  SC2_WSL_DETECT=${SC2_WSL_DETECT:-<unset>}"
if [ "${SC2_WSL_DETECT:-}" = "0" ]; then pass "SC2_WSL_DETECT=0 set (forces pure-Linux mode)"; else fail "SC2_WSL_DETECT must be 0 — burnysc2 will else flip to Windows-via-WSL"; fi
if [ "${SC2PATH:-}" = "$HOME/StarCraftII" ] || [ "${SC2PATH:-}" = "$HOME/StarCraftII/" ]; then pass "SC2PATH points at ~/StarCraftII"; else warn "SC2PATH=${SC2PATH:-<unset>} (resolver should still pick branch 3 if WSL_DETECT=0; verify with paths smoke)"; fi

echo
echo "=== quick pytest collect (proves uv sync done + imports clean) ==="
if uv run pytest --collect-only -q 2>&1 | tail -3 | grep -q "tests collected"; then
  pass "pytest collects in WSL"
else
  fail "pytest --collect-only failed — uv sync probably not run on this venv"
fi

echo
echo "=== resolver smoke (paths.resolve_sc2_path → branch 3 expected) ==="
uv run python -c "
import os
from src.orchestrator.paths import resolve_sc2_path
print('  resolved =', resolve_sc2_path())
print('  HOME-relative =', str(resolve_sc2_path()).startswith(os.path.expanduser('~')))
" 2>&1

echo
echo "=== bots.current pointer (Phase N is dormant in v2) ==="
cat bots/current/current.txt
echo "  reminder: load_settings().data_dir → bots/v2/data/training.db"
echo "  the soak will write Phase N transitions to v2's DB. that is correct & expected."

echo
echo "DONE."
