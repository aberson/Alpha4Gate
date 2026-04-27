#!/usr/bin/env bash
# Phase N §7 soak runner — 4-way parallel headless SC2 on WSL2 Linux.
#
# Plays N games at difficulty 2 (winning soak per build-plan §7) and
# N games at difficulty 5 (losing soak), all solo bots/v0 vs built-in AI.
# Tags the soak window in logs/phase-n-soak/<ts>/soak.json so the
# analyzer can SELECT games WHERE created_at BETWEEN start AND end.
#
# Usage (from /mnt/c/.../Alpha4Gate, invoked as a login shell so ~/.profile
# is sourced and SC2_WSL_DETECT / SC2PATH / UV_PROJECT_ENVIRONMENT are visible):
#
#   wsl -d Ubuntu-22.04 -- bash -l scripts/phase_n_soak_run.sh \
#       [--games-each 10] [--concurrency 4] [--diff-win 2] [--diff-loss 5]
#
# All flags optional; defaults below match the build-plan §7 spec.
# For a smoke dry-run: --games-each 2 --concurrency 2.

set -u

GAMES_EACH=10
CONCURRENCY=4
DIFF_WIN=2
DIFF_LOSS=5

while [ $# -gt 0 ]; do
  case "$1" in
    --games-each) GAMES_EACH="$2"; shift 2 ;;
    --concurrency) CONCURRENCY="$2"; shift 2 ;;
    --diff-win) DIFF_WIN="$2"; shift 2 ;;
    --diff-loss) DIFF_LOSS="$2"; shift 2 ;;
    -h|--help) sed -n '1,20p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

cd /mnt/c/Users/abero/dev/Alpha4Gate

# Sanity: env vars must be visible (login shell required).
if [ "${SC2_WSL_DETECT:-}" != "0" ]; then
  echo "FATAL: SC2_WSL_DETECT != 0 — invoke this script via 'bash -l' (login shell)." >&2
  echo "       Otherwise burnysc2 flips to Windows-via-WSL passthrough." >&2
  exit 3
fi
if ! command -v uv >/dev/null; then
  echo "FATAL: uv not on PATH — invoke via 'bash -l'." >&2
  exit 3
fi

SOAK_TS=$(date -u +%Y%m%dT%H%M%SZ)
SOAK_DIR="logs/phase-n-soak/$SOAK_TS"
mkdir -p "$SOAK_DIR"

# Capture start time as a UTC ISO string the analyzer can compare to
# games.created_at (sqlite default datetime('now') is UTC ISO).
START_ISO=$(date -u +"%Y-%m-%d %H:%M:%S")
START_EPOCH=$(date +%s)

echo "==================================================================="
echo "Phase N §7 soak — start"
echo "  ts:           $SOAK_TS"
echo "  games_each:   $GAMES_EACH (diff $DIFF_WIN winning, diff $DIFF_LOSS losing)"
echo "  concurrency:  $CONCURRENCY"
echo "  log dir:      $SOAK_DIR"
echo "  start (UTC):  $START_ISO"
echo "==================================================================="

run_one() {
  local diff=$1
  local idx=$2
  local logf="$SOAK_DIR/diff${diff}-game${idx}.log"
  echo "[$(date +%H:%M:%S)] launching diff=$diff idx=$idx -> $logf"
  uv run python -m bots.v0 --role solo --map Simple64 --difficulty "$diff" \
      --no-claude \
      > "$logf" 2>&1
  local rc=$?
  echo "[$(date +%H:%M:%S)] finished  diff=$diff idx=$idx exit=$rc"
  return $rc
}

# Build the full game list (interleaved so concurrent batches mix difficulties
# rather than flooding one bucket first — gives steadier load + fairer wall-clock).
PLAN=()
for i in $(seq 1 "$GAMES_EACH"); do
  PLAN+=("$DIFF_WIN $i")
  PLAN+=("$DIFF_LOSS $i")
done

PIDS=()
RESULTS=()
LAUNCHED=0
TOTAL=${#PLAN[@]}

# Simple semaphore: cap in-flight processes at $CONCURRENCY.
for entry in "${PLAN[@]}"; do
  while [ "$(jobs -r | wc -l)" -ge "$CONCURRENCY" ]; do
    sleep 1
  done
  read -r diff idx <<<"$entry"
  run_one "$diff" "$idx" &
  PIDS+=($!)
  LAUNCHED=$((LAUNCHED + 1))
done

# Wait for the tail.
for p in "${PIDS[@]}"; do
  wait "$p"
  RESULTS+=("$?")
done

END_ISO=$(date -u +"%Y-%m-%d %H:%M:%S")
END_EPOCH=$(date +%s)
WALL=$((END_EPOCH - START_EPOCH))

# Write the soak manifest the analyzer will pick up.
cat > "$SOAK_DIR/soak.json" <<JSON
{
  "soak_ts": "$SOAK_TS",
  "start_iso_utc": "$START_ISO",
  "end_iso_utc":   "$END_ISO",
  "wall_clock_secs": $WALL,
  "games_each":   $GAMES_EACH,
  "concurrency":  $CONCURRENCY,
  "diff_win":     $DIFF_WIN,
  "diff_loss":    $DIFF_LOSS,
  "log_dir":      "$SOAK_DIR"
}
JSON

# Count exit codes.
ok=0; fail=0
for rc in "${RESULTS[@]}"; do
  if [ "$rc" = "0" ]; then ok=$((ok+1)); else fail=$((fail+1)); fi
done

echo
echo "==================================================================="
echo "Phase N §7 soak — done"
echo "  total games:    $TOTAL"
echo "  exit ok:        $ok"
echo "  exit nonzero:   $fail"
echo "  wall clock:     ${WALL}s ($(printf '%dm%02ds' $((WALL/60)) $((WALL%60))))"
echo "  manifest:       $SOAK_DIR/soak.json"
echo
echo "Next:  uv run python scripts/phase_n_soak_analyze.py $SOAK_DIR/soak.json"
echo "==================================================================="
