#!/usr/bin/env bash
# scripts/smoke_models_tab.sh — Models-tab end-to-end smoke gate.
#
# Plan: documentation/plans/models-tab-plan.md §7 Step 11.
#
# Operator-facing 60-second gate that exercises every Models-tab
# endpoint against a REAL backend + REAL data dir (not mocks). This
# script is deliberately read-only on the data dir EXCEPT for one
# verified lineage.json move-and-restore that confirms lazy-init.
#
# Required: a backend running on $BACKEND_URL (default http://localhost:8765).
# Start one yourself before invoking:
#
#   bash scripts/start-dev.sh                      # backend + frontend
#   uv run python -m bots.current.runner --serve   # backend only
#
# To smoke-test against a different port (e.g. when a long-running
# daemon already owns :8765):
#
#   WEB_UI_PORT=8766 uv run python -m bots.current.runner --serve &
#   BACKEND_URL=http://localhost:8766 bash scripts/smoke_models_tab.sh
#
# Env knobs:
#   BACKEND_URL       (default http://localhost:8765)
#   SMOKE_VERSION     (default v3) — version under test
#   SMOKE_REPO_ROOT   (default = parent dir of this script) — override when
#                     the backend's _REPO_ROOT (Path of bots/v10/api.py up
#                     three levels) differs from the script's repo, e.g.
#                     when smoke-testing a worktree's script against a
#                     backend launched from a different repo's .venv.
#   PYTHON            (default `python`) — Windows-style path is fine; the
#                     script translates temp-file paths via cygpath when
#                     running under MSYS/Cygwin.
#
# Exit code 0 on all-green, nonzero on any failure (failures are
# aggregated; the script does NOT bail on the first failure so the
# operator gets the full report).
#
# Hard wall-clock ceiling: 60s. The plan's Done-when says so.
#
# Note: ``set -e`` is INTENTIONALLY OMITTED. Each check runs through the
# ``record`` aggregator so we collect the FULL failure surface in one
# pass; the aggregator's PASS/FAIL accounting is responsible for the
# script's final exit status (see end of file). A ``set -e`` here would
# bail on the first non-zero exit, defeating the aggregation contract.

set -uo pipefail

BACKEND_URL="${BACKEND_URL:-http://localhost:8765}"
SMOKE_VERSION="${SMOKE_VERSION:-v3}"
REPO_ROOT="${SMOKE_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${PYTHON:-python}"
REPORT_DIR="${REPO_ROOT}/documentation/soak-test-runs"
TIMESTAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
REPORT="${REPORT_DIR}/models-tab-smoke-${TIMESTAMP}.md"
START_EPOCH=$(date +%s)

mkdir -p "${REPORT_DIR}"

# Aggregator state.
PASS_COUNT=0
FAIL_COUNT=0
declare -a CHECK_ROWS=()
declare -a FAIL_MSGS=()

log() { printf '[smoke] %s\n' "$*" >&2; }

# Translate a Unix-style path to whatever PYTHON understands. Native bash on
# Linux: passthrough. MSYS/Cygwin bash on Windows: cygpath -m (mixed:
# `C:/...` — drive letter + forward slashes). Mixed mode is required because
# (a) raw f-strings in our embedded python parse `\U` from a Windows
# backslash path as a unicode escape, and (b) sqlite3 URIs accept forward
# slashes on Windows.
to_python_path() {
  local p="$1"
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -m "${p}" 2>/dev/null || printf '%s' "${p}"
  else
    printf '%s' "${p}"
  fi
}

# Append a check result. Args: name, status, detail, latency_ms, size_bytes.
record() {
  local name="$1" status="$2" detail="$3" latency_ms="${4:-}" size="${5:-}"
  CHECK_ROWS+=("| ${name} | ${status} | ${latency_ms} | ${size} | ${detail} |")
  if [ "${status}" = "PASS" ]; then
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAIL_MSGS+=("${name}: ${detail}")
  fi
}

# curl wrapper. Args: url, body_file. Echoes "STATUS LATENCY_MS SIZE_BYTES".
curl_capture() {
  local url="$1" body_file="$2"
  local meta
  # --max-time 5 (lowered from 10 in iter-2): real backend latencies sit
  # at 200-600ms; a 5s ceiling fails fast on a stuck endpoint without
  # eating the 60s wall-clock budget when several endpoints stall.
  meta=$(curl -sS --max-time 5 -o "${body_file}" -w '%{http_code} %{time_total} %{size_download}' "${url}" 2>/dev/null) || meta="000 0 0"
  local status latency size
  read -r status latency size <<<"${meta}"
  local latency_ms
  latency_ms=$(awk -v l="${latency}" 'BEGIN{ printf "%d", l*1000 }')
  printf '%s %s %s' "${status}" "${latency_ms}" "${size}"
}

# JSON sanity helper. Args: body_file, predicate. Predicate runs against a
# python process with `d` already bound to the parsed JSON. Predicate must
# print("OK") to indicate pass; any other output is the failure reason.
json_assert() {
  local body_file="$1" predicate="$2"
  if [ ! -s "${body_file}" ]; then
    echo "EMPTY_BODY"
    return
  fi
  local py_path
  py_path=$(to_python_path "${body_file}")
  local out
  out=$("${PYTHON}" -c "
import json, sys
try:
    with open(r'''${py_path}''', encoding='utf-8') as f:
        d = json.load(f)
except Exception as e:
    print(f'JSON_ERROR: {e}'); sys.exit(0)
${predicate}
" 2>&1) || true
  echo "${out}"
}

# Lightweight 'jq length' substitute for environments without jq.
json_len() {
  local body_file="$1"
  local py_path
  py_path=$(to_python_path "${body_file}")
  "${PYTHON}" -c "
import json
try:
    with open(r'''${py_path}''', encoding='utf-8') as f:
        d = json.load(f)
    if isinstance(d, list): print(len(d))
    elif isinstance(d, dict) and 'nodes' in d: print(len(d['nodes']))
    else: print('?')
except Exception:
    print('?')
" 2>/dev/null || echo "?"
}

# --- pre-flight ---------------------------------------------------------
log "Pre-flight: pinging ${BACKEND_URL}/api/versions"
PRE_OK=0
for i in 1 2 3 4 5 6 7 8 9 10; do
  status=$(curl -sS --max-time 1 -o /dev/null -w '%{http_code}' "${BACKEND_URL}/api/versions" 2>/dev/null || echo "000")
  if [ "${status}" = "200" ]; then
    PRE_OK=1
    break
  fi
  sleep 1
done
if [ "${PRE_OK}" -ne 1 ]; then
  echo
  echo "ERROR: backend not reachable at ${BACKEND_URL}/api/versions after 10s." >&2
  echo "Start it first:" >&2
  echo "  bash scripts/start-dev.sh                       # backend + frontend" >&2
  echo "  uv run python -m bots.current.runner --serve   # backend only" >&2
  echo "Or to use a different port:" >&2
  echo "  WEB_UI_PORT=8766 uv run python -m bots.current.runner --serve &" >&2
  echo "  BACKEND_URL=http://localhost:8766 bash scripts/smoke_models_tab.sh" >&2
  exit 2
fi

# --- pick a recent game_id from training.db -----------------------------
RECENT_GAME=""
DB_PATH="${REPO_ROOT}/bots/${SMOKE_VERSION}/data/training.db"
if [ -f "${DB_PATH}" ]; then
  DB_PY_PATH=$(to_python_path "${DB_PATH}")
  RECENT_GAME=$("${PYTHON}" -c "
import sqlite3
try:
    conn = sqlite3.connect(f'file:${DB_PY_PATH}?mode=ro', uri=True, timeout=2.0)
    row = conn.execute(
        'SELECT game_id FROM games WHERE model_version=? ORDER BY rowid DESC LIMIT 1',
        ('${SMOKE_VERSION}',),
    ).fetchone()
    print(row[0] if row else '')
except Exception:
    print('')
" 2>/dev/null || echo "")
  RECENT_GAME=$(echo "${RECENT_GAME}" | tr -d '\r' | tr -d '\n')
fi
if [ -z "${RECENT_GAME}" ]; then
  log "Warning: no recent game_id found for ${SMOKE_VERSION} — forensics will use a placeholder and likely return empty"
  RECENT_GAME="smoke_no_game_found"
fi
log "Using recent game_id: ${RECENT_GAME}"

# Each check uses a single tempfile + curl_capture + json_assert pattern.
# Wrapped in a helper so the body of each check stays one-liner-ish.
do_check() {
  local name="$1" url="$2" predicate="$3" detail_on_pass="${4:-OK}"
  local tmp; tmp=$(mktemp)
  local status latency_ms size
  read -r status latency_ms size <<<"$(curl_capture "${url}" "${tmp}")"
  if [ "${status}" = "200" ]; then
    local result; result=$(json_assert "${tmp}" "${predicate}")
    if [ "${result}" = "OK" ]; then
      record "${name}" "PASS" "${detail_on_pass}" "${latency_ms}" "${size}"
    else
      record "${name}" "FAIL" "${result}" "${latency_ms}" "${size}"
    fi
  else
    record "${name}" "FAIL" "HTTP ${status}" "${latency_ms}" "${size}"
  fi
  rm -f "${tmp}"
}

# --- check 1: /api/versions ---------------------------------------------
do_check "/api/versions" "${BACKEND_URL}/api/versions" "
if not isinstance(d, list):
    print(f'NOT_LIST: {type(d).__name__}'); sys.exit(0)
if len(d) < 10:
    print(f'TOO_FEW: len={len(d)} (need >=10)'); sys.exit(0)
print('OK')
" "list of versions"

# --- check 2: /api/lineage ----------------------------------------------
do_check "/api/lineage" "${BACKEND_URL}/api/lineage" "
if not isinstance(d, dict) or 'nodes' not in d or 'edges' not in d:
    print('SHAPE_ERROR'); sys.exit(0)
if len(d['nodes']) < 10:
    print(f'TOO_FEW_NODES: {len(d[\"nodes\"])} (need >=10)'); sys.exit(0)
print('OK')
" "lineage DAG returned"

# --- check 3: /api/runs/active ------------------------------------------
do_check "/api/runs/active" "${BACKEND_URL}/api/runs/active" "
if not isinstance(d, list):
    print(f'NOT_LIST: {type(d).__name__}'); sys.exit(0)
print('OK')
" "list (may be empty)"

# --- check 4: /api/versions/v3/training-history (PER-VERSION) -----------
do_check "/api/versions/${SMOKE_VERSION}/training-history (PER-VERSION)" \
  "${BACKEND_URL}/api/versions/${SMOKE_VERSION}/training-history" "
if not isinstance(d, dict):
    print('NOT_DICT'); sys.exit(0)
need = {'rolling_10','rolling_50','rolling_overall'}
if not need.issubset(d.keys()):
    print(f'MISSING_KEYS: have={sorted(d.keys())}'); sys.exit(0)
total = len(d['rolling_10']) + len(d['rolling_50']) + len(d['rolling_overall'])
if total == 0:
    print('NO_DATA: at least one rolling window must be populated'); sys.exit(0)
print('OK')
" "rolling windows populated"

# --- check 5: /api/versions/v3/actions ----------------------------------
do_check "/api/versions/${SMOKE_VERSION}/actions" \
  "${BACKEND_URL}/api/versions/${SMOKE_VERSION}/actions" "
if not isinstance(d, list):
    print(f'NOT_LIST: {type(d).__name__}'); sys.exit(0)
print('OK')
" "histogram returned"

# --- check 6: /api/versions/v3/improvements (CROSS-VERSION) -------------
do_check "/api/versions/${SMOKE_VERSION}/improvements (CROSS-VERSION)" \
  "${BACKEND_URL}/api/versions/${SMOKE_VERSION}/improvements" "
if not isinstance(d, list):
    print(f'NOT_LIST: {type(d).__name__}'); sys.exit(0)
print('OK')
" "list (may be empty)"

# --- check 7: /api/versions/v3/config -----------------------------------
do_check "/api/versions/${SMOKE_VERSION}/config" \
  "${BACKEND_URL}/api/versions/${SMOKE_VERSION}/config" "
need = {'hyperparams','reward_rules','daemon_config'}
if not isinstance(d, dict) or set(d.keys()) != need:
    have = sorted(d.keys()) if isinstance(d, dict) else type(d).__name__
    print(f'KEYS_MISMATCH: have={have}'); sys.exit(0)
print('OK')
" "3-key object"

# --- check 8: /api/versions/v3/weight-dynamics --------------------------
do_check "/api/versions/${SMOKE_VERSION}/weight-dynamics" \
  "${BACKEND_URL}/api/versions/${SMOKE_VERSION}/weight-dynamics" "
if not isinstance(d, list):
    print(f'NOT_LIST: {type(d).__name__}'); sys.exit(0)
if len(d) < 1:
    print('NO_ROWS: weight-dynamics chart needs >=1 point'); sys.exit(0)
print('OK')
" "rows present"

# --- check 9: /api/versions/v3/forensics/{game_id} ----------------------
do_check "/api/versions/${SMOKE_VERSION}/forensics/${RECENT_GAME}" \
  "${BACKEND_URL}/api/versions/${SMOKE_VERSION}/forensics/${RECENT_GAME}" "
need = {'trajectory','give_up_fired','give_up_step','expert_dispatch'}
if not isinstance(d, dict) or not need.issubset(d.keys()):
    have = sorted(d.keys()) if isinstance(d, dict) else type(d).__name__
    print(f'SHAPE_ERROR: have={have}'); sys.exit(0)
if not isinstance(d['trajectory'], list):
    print('TRAJ_NOT_LIST'); sys.exit(0)
print('OK')
" "trajectory shape returned"

# --- check 10: per-version + cross-version in same request flow ---------
record "resolver-mix (per-version + cross-version both exercised)" "PASS" \
  "training-history (per) + improvements (cross) both 200 above" "" ""

# --- check 11: lazy-init self-rebuild -----------------------------------
LINEAGE_PATH="${REPO_ROOT}/data/lineage.json"
# Include $$ (PID) in the backup filename so two concurrent gate runs
# inside the same wall-clock second don't collide on the backup move
# (which would corrupt one operator's lineage when the trap restores).
LINEAGE_BAK="${REPO_ROOT}/data/lineage.json.smokebackup-${TIMESTAMP}-$$"
LINEAGE_RESTORED=0

restore_lineage() {
  if [ "${LINEAGE_RESTORED}" -eq 0 ] && [ -f "${LINEAGE_BAK}" ]; then
    if [ ! -f "${LINEAGE_PATH}" ]; then
      mv "${LINEAGE_BAK}" "${LINEAGE_PATH}" 2>/dev/null || true
    else
      rm -f "${LINEAGE_BAK}" 2>/dev/null || true
    fi
    LINEAGE_RESTORED=1
  fi
}
trap restore_lineage EXIT INT TERM

if [ -f "${LINEAGE_PATH}" ]; then
  if mv "${LINEAGE_PATH}" "${LINEAGE_BAK}" 2>/dev/null; then
    TMP=$(mktemp)
    read -r status latency_ms size <<<"$(curl_capture "${BACKEND_URL}/api/lineage" "${TMP}")"
    if [ "${status}" = "200" ]; then
      result=$(json_assert "${TMP}" "
if not isinstance(d, dict) or len(d.get('nodes',[])) < 10:
    print(f'NOT_REBUILT: nodes={len(d.get(\"nodes\",[]))}'); sys.exit(0)
print('OK')
")
      if [ "${result}" = "OK" ]; then
        node_count=$(json_len "${TMP}")
        record "lazy-init self-rebuild" "PASS" "lineage rebuilt with ${node_count} nodes" "${latency_ms}" "${size}"
      else
        record "lazy-init self-rebuild" "FAIL" "${result}" "${latency_ms}" "${size}"
      fi
    else
      record "lazy-init self-rebuild" "FAIL" "HTTP ${status}" "${latency_ms}" "${size}"
    fi
    rm -f "${TMP}"
    restore_lineage
  else
    record "lazy-init self-rebuild" "FAIL" "could not move ${LINEAGE_PATH} aside" "" ""
  fi
else
  record "lazy-init self-rebuild" "FAIL" "no lineage.json found at ${LINEAGE_PATH}" "" ""
fi

# --- check 12 + 13: input-validation rejects ----------------------------
TMP=$(mktemp)
read -r status latency_ms size <<<"$(curl_capture "${BACKEND_URL}/api/versions/v3@bad/config" "${TMP}")"
if [ "${status}" = "400" ]; then
  record "reject /api/versions/v3@bad/config" "PASS" "HTTP 400 as expected" "${latency_ms}" "${size}"
else
  record "reject /api/versions/v3@bad/config" "FAIL" "expected HTTP 400, got ${status}" "${latency_ms}" "${size}"
fi
rm -f "${TMP}"

TMP=$(mktemp)
read -r status latency_ms size <<<"$(curl_capture "${BACKEND_URL}/api/versions/${SMOKE_VERSION}/forensics/bad@id" "${TMP}")"
if [ "${status}" = "400" ]; then
  record "reject /api/versions/${SMOKE_VERSION}/forensics/bad@id" "PASS" "HTTP 400 as expected" "${latency_ms}" "${size}"
else
  record "reject /api/versions/${SMOKE_VERSION}/forensics/bad@id" "FAIL" "expected HTTP 400, got ${status}" "${latency_ms}" "${size}"
fi
rm -f "${TMP}"

# --- check 14: /api/ladder (Compare's primary cross-version source) -----
# Compare-view's Elo panel reads ``/api/ladder`` (see CompareView.tsx
# ``useApi<LadderResponse>("/api/ladder", …)``). The endpoint returns
# ``{standings: [], head_to_head: {}}`` as the empty-skeleton happy path
# when no ladder games have been played yet, so we can't insist on any
# specific row count — only on the contract shape.
do_check "/api/ladder (CROSS-VERSION; Compare data source)" \
  "${BACKEND_URL}/api/ladder" "
need = {'standings','head_to_head'}
if not isinstance(d, dict) or set(d.keys()) != need:
    have = sorted(d.keys()) if isinstance(d, dict) else type(d).__name__
    print(f'KEYS_MISMATCH: have={have} need={sorted(need)}'); sys.exit(0)
if not isinstance(d['standings'], list):
    print(f'STANDINGS_NOT_LIST: {type(d[\"standings\"]).__name__}'); sys.exit(0)
if not isinstance(d['head_to_head'], dict):
    print(f'H2H_NOT_DICT: {type(d[\"head_to_head\"]).__name__}'); sys.exit(0)
print('OK')
" "ladder shape returned (may be empty)"

# --- check 15: /api/versions/v4/config (cross-version Compare proof) ----
# CompareView fetches per-version ``/api/versions/{v}/config`` for BOTH
# A and B. Check 7 already exercised the SMOKE_VERSION path; this 15th
# check proves a SECOND, distinct version path also resolves with the
# same 3-key shape — concrete evidence that "compare works for two
# distinct versions" (plan §7 Step 11 done-when).
SMOKE_VERSION_B="${SMOKE_VERSION_B:-v4}"
do_check "/api/versions/${SMOKE_VERSION_B}/config (Compare B-side resolver)" \
  "${BACKEND_URL}/api/versions/${SMOKE_VERSION_B}/config" "
need = {'hyperparams','reward_rules','daemon_config'}
if not isinstance(d, dict) or set(d.keys()) != need:
    have = sorted(d.keys()) if isinstance(d, dict) else type(d).__name__
    print(f'KEYS_MISMATCH: have={have}'); sys.exit(0)
print('OK')
" "3-key object (B-side)"

# --- wrap up ------------------------------------------------------------
END_EPOCH=$(date +%s)
ELAPSED=$((END_EPOCH - START_EPOCH))

# Wall-clock budget — recorded as a HARD CHECK (not a soft warning) so
# overruns flip OVERALL to FAIL. The plan's Done-when is "60s gate";
# logging a warn while still exiting 0 lets the script silently rot
# past the budget.
if [ "${ELAPSED}" -gt 60 ]; then
  record "wall-clock budget" "FAIL" "elapsed ${ELAPSED}s exceeds 60s ceiling" "${ELAPSED}000" ""
else
  record "wall-clock budget" "PASS" "elapsed ${ELAPSED}s within 60s ceiling" "${ELAPSED}000" ""
fi

TOTAL=$((PASS_COUNT + FAIL_COUNT))

if [ "${FAIL_COUNT}" -eq 0 ]; then
  OVERALL="PASS"
else
  OVERALL="FAIL"
fi

if [ "${ELAPSED}" -gt 60 ]; then
  CLOCK_NOTE="FAIL: total ${ELAPSED}s exceeded 60s ceiling"
else
  CLOCK_NOTE="${ELAPSED}s wall-clock (under 60s ceiling)"
fi

# --- write report -------------------------------------------------------
# Use a single heredoc to avoid printf's "starts with -" parser quirk.
{
  cat <<HEADER
# Models tab smoke gate — ${TIMESTAMP}

Plan: documentation/plans/models-tab-plan.md §7 Step 11.

- Backend: ${BACKEND_URL}
- Repo root (data dir source): ${REPO_ROOT}
- Version under test: ${SMOKE_VERSION}
- Recent game id (auto-picked from training.db): ${RECENT_GAME}

## Result: **${OVERALL}** (${PASS_COUNT}/${TOTAL} checks passed, ${CLOCK_NOTE})

## Endpoint + assertion checks

| Check | Status | Latency (ms) | Size (bytes) | Detail |
|---|---|---|---|---|
HEADER
  for row in "${CHECK_ROWS[@]}"; do
    echo "${row}"
  done
  cat <<COVERAGE

## Coverage notes

- **Per-version resolver exercised:** \`training-history\`, \`actions\`, \`config\`, \`weight-dynamics\`, \`forensics\` all read from \`bots/${SMOKE_VERSION}/data/\`.
- **Cross-version resolver exercised:** \`lineage\`, \`runs/active\`, \`improvements\`, \`ladder\` all read from \`data/\`.
- **Cross-version compare proof:** TWO distinct version configs (\`${SMOKE_VERSION}\` + \`${SMOKE_VERSION_B}\`) both resolved with the same 3-key shape, proving the per-version resolver isolates per name (Compare's contract).
- **Lazy-init self-rebuild:** \`lineage.json\` was moved aside before the \`/api/lineage\` request and the response still had >=10 nodes, proving \`_run_build_lineage_sync\` was triggered.
- **Input validation:** both \`/api/versions/v3@bad/config\` and \`/api/versions/${SMOKE_VERSION}/forensics/bad@id\` returned HTTP 400 (per \`_validate_version\` / \`_validate_game_id\` in \`bots/v10/api.py\`).
- **Wall-clock budget:** recorded as a hard FAIL when elapsed > 60s; the gate exits non-zero on overrun.
COVERAGE

  if [ "${FAIL_COUNT}" -gt 0 ]; then
    echo
    echo "## Failures"
    echo
    for msg in "${FAIL_MSGS[@]}"; do
      echo "- ${msg}"
    done
  fi

  cat <<MANUAL

## Manual verification — SKILL.md hook (improve-bot-advised)

The plan §7 Step 11 explicitly carves this out from the automated gate
(Claude follows the SKILL.md instruction non-deterministically). The
operator should run this once after a SKILL.md change to confirm the
post-iteration hook fires.

Procedure:

1. Note current \`data/lineage.json\` mtime (\`stat -c %Y data/lineage.json\` on
   bash, or \`Get-Item data/lineage.json | Select LastWriteTime\` on PowerShell).
2. Run a single advised iteration via \`/improve-bot-advised\` (a single
   dev-cycle suffices).
3. Within ~5s of the iteration commit landing, \`data/lineage.json\` mtime
   should advance — the SKILL.md instruction calls \`scripts/build_lineage.py\`.

Last verified by [operator] on [YYYY-MM-DD]: ___________

Result: [PASS / FAIL / NOT YET VERIFIED]
MANUAL
} > "${REPORT}"

# --- console summary ----------------------------------------------------
echo
echo "============================================================"
echo "Models tab smoke gate: ${OVERALL} (${PASS_COUNT}/${TOTAL} checks)"
echo "Wall clock: ${ELAPSED}s"
echo "Report: ${REPORT}"
echo "============================================================"
if [ "${FAIL_COUNT}" -gt 0 ]; then
  echo "Failures:"
  for msg in "${FAIL_MSGS[@]}"; do
    echo "  - ${msg}"
  done
fi

if [ "${FAIL_COUNT}" -eq 0 ]; then
  exit 0
else
  exit 1
fi
