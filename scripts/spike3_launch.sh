#!/bin/bash
# Phase 8 Spike 3 launcher — 4-way parallel selfplay + RSS sampler.
# Designed for WSL2 Ubuntu 22.04. Writes results to data/selfplay_results.parallel-{1..4}.jsonl,
# RSS samples to /tmp/spike3_rss.csv, per-invocation logs to /tmp/spike3_run-{1..4}.log,
# summary to stdout. Bounded by `wait` on the 4 selfplay PIDs.

set -u
cd /mnt/c/Users/abero/dev/Alpha4Gate

RSS_LOG=/tmp/spike3_rss.csv
echo "ts,pid_count,total_rss_kb,peak_per_proc_kb" > "$RSS_LOG"

# Background RSS sampler — 1 Hz. Reads /proc/*/exe directly for SC2_x64 detection
# (avoids pidof's mysterious 0-return seen on the prior spike-3 run; /proc is authoritative).
DEBUG_LOG=/tmp/spike3_sampler_debug.log
: > "$DEBUG_LOG"
(
  iter=0
  while true; do
    iter=$((iter+1))
    pids=""
    for procdir in /proc/[0-9]*; do
      exe=$(readlink "$procdir/exe" 2>/dev/null || true)
      case "$exe" in
        */SC2_x64) pid=${procdir#/proc/}; pids="$pids $pid" ;;
      esac
    done
    pids=$(echo "$pids" | xargs)  # trim
    if [ -z "$pids" ]; then
      echo "$(date +%H:%M:%S),0,0,0" >> "$RSS_LOG"
      [ $((iter % 20)) -eq 1 ] && echo "iter=$iter pids=[empty]" >> "$DEBUG_LOG"
    else
      # Read RSS from /proc/$pid/status directly — avoids ps -p multi-PID quirks
      total=0; peak=0; pid_count=0
      for pid in $pids; do
        rss=$(awk '/^VmRSS:/ {print $2}' /proc/$pid/status 2>/dev/null || true)
        if [ -n "$rss" ]; then
          pid_count=$((pid_count + 1))
          total=$((total + rss))
          [ "$rss" -gt "$peak" ] && peak=$rss
        fi
      done
      echo "$(date +%H:%M:%S),$pid_count,$total,$peak" >> "$RSS_LOG"
      [ $((iter % 20)) -eq 1 ] && echo "iter=$iter pids=[$pids] count=$pid_count total=${total}kB peak=${peak}kB" >> "$DEBUG_LOG"
    fi
    sleep 1
  done
) &
SAMPLER_PID=$!

START=$(date +%s)
PIDS=()
for i in 1 2 3 4; do
  uv run python scripts/selfplay.py \
    --p1 v0 --p2 v0 --games 5 --map Simple64 \
    --results-path data/selfplay_results.parallel-$i.jsonl \
    > /tmp/spike3_run-$i.log 2>&1 &
  PIDS+=($!)
  echo "Launched invocation $i (pid=${PIDS[-1]})"
done

EXITCODES=()
for idx in "${!PIDS[@]}"; do
  p=${PIDS[$idx]}
  wait "$p"
  rc=$?
  EXITCODES+=("$rc")
  echo "Invocation $((idx+1)) (pid=$p) exited with code $rc"
done

END=$(date +%s)
WALL=$((END - START))

kill "$SAMPLER_PID" 2>/dev/null || true
wait "$SAMPLER_PID" 2>/dev/null || true

echo "===SPIKE3_SUMMARY==="
echo "WALL_CLOCK_SECONDS=$WALL"
echo "EXIT_CODES=${EXITCODES[*]}"
echo "RSS_SAMPLES=$(wc -l < "$RSS_LOG")"
echo "===END==="
