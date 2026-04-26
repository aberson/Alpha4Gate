#!/bin/bash
# Spike 3 RSS-log analysis. Reads /tmp/spike3_rss.csv, prints peak metrics.
LOG=/tmp/spike3_rss.csv
echo "=== row 1 (header) ==="
head -1 "$LOG"
echo
echo "=== distinct pid_count values ==="
tail -n +2 "$LOG" | cut -d, -f2 | sort -n | uniq -c
echo
echo "=== peak rows by total_rss_kb (top 5) ==="
tail -n +2 "$LOG" | sort -t, -k3 -n | tail -5
echo
echo "=== peak rows by peak_per_proc_kb (top 5) ==="
tail -n +2 "$LOG" | sort -t, -k4 -n | tail -5
echo
echo "=== peak rows by pid_count (top 5) ==="
tail -n +2 "$LOG" | sort -t, -k2 -n | tail -5
echo
echo "=== summary stats (awk) ==="
awk -F, '
  NR>1 {
    if ($3+0 > maxtot) { maxtot=$3; maxtotrow=$0 }
    if ($4+0 > maxpeak) { maxpeak=$4; maxpeakrow=$0 }
    if ($2+0 > maxpid) { maxpid=$2; maxpidrow=$0 }
    total+=$3
    cnt++
  }
  END {
    print "rows=" cnt
    print "max total RSS row : " maxtotrow
    print "max per-proc row  : " maxpeakrow
    print "max pid_count row : " maxpidrow
    if (cnt>0) printf "avg total RSS kB  : %.0f\n", total/cnt
  }
' "$LOG"
echo
echo "=== first 3 non-zero pid_count samples ==="
awk -F, 'NR>1 && $2+0 > 0' "$LOG" | head -3
echo
echo "=== last 3 non-zero pid_count samples ==="
awk -F, 'NR>1 && $2+0 > 0' "$LOG" | tail -3
