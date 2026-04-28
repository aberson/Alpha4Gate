"""Phase N §7 soak analyzer — emits PASS/FAIL on the three build-plan criteria.

Reads the manifest produced by ``phase_n_soak_run.sh`` (start/end UTC,
games_each, diff_win, diff_loss, log_dir), queries the games + transitions
table for that window, scans the per-game logs for ``Resigning:`` lines,
and prints a verdict against:

  1. Heuristic separation: mean win_prob on win-game transitions vs
     loss-game transitions, expected >= 0.10 absolute.
  2. Give-up rate at diff_win (winning soak): expected < 5%.
  3. Give-up rate at diff_loss (losing soak): expected >= 30%.

Usage:
    uv run python scripts/phase_n_soak_analyze.py logs/phase-n-soak/<ts>/soak.json
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from bots.v0.config import load_settings


def _scan_logs_for_resigns(log_dir: Path) -> dict[int, list[str]]:
    """Return {difficulty: [logfile_basename, ...]} for games where the
    resign log line fired. Difficulty is parsed from the filename
    pattern ``diff{N}-game{idx}.log`` written by the soak runner."""
    pat = re.compile(r"^diff(\d+)-game\d+\.log$")
    resigned: dict[int, list[str]] = defaultdict(list)
    for f in sorted(log_dir.glob("diff*-game*.log")):
        m = pat.match(f.name)
        if not m:
            continue
        diff = int(m.group(1))
        # Logs are UTF-8 from Linux bash redirection (no Tee-Object UTF-16 issue).
        with f.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "Resigning: winprob" in line:
                    resigned[diff].append(f.name)
                    break
    return dict(resigned)


def _scan_logs_for_total(log_dir: Path) -> dict[int, int]:
    """Return {difficulty: total_log_files} — i.e. games actually launched."""
    pat = re.compile(r"^diff(\d+)-game\d+\.log$")
    total: dict[int, int] = defaultdict(int)
    for f in sorted(log_dir.glob("diff*-game*.log")):
        m = pat.match(f.name)
        if m:
            total[int(m.group(1))] += 1
    return dict(total)


def main(manifest_path: str) -> int:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    db_path = load_settings().data_dir / "training.db"
    log_dir = Path(manifest["log_dir"])

    print(f"manifest:    {manifest_path}")
    print(f"db:          {db_path}")
    print(f"window:      {manifest['start_iso_utc']}  ..  {manifest['end_iso_utc']}")
    print(f"diff_win:    {manifest['diff_win']}  diff_loss: {manifest['diff_loss']}")
    print(f"wall clock:  {manifest['wall_clock_secs']}s")
    print()

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    # Pull every game + transition from the soak window.
    games = list(
        con.execute(
            "SELECT game_id, difficulty, result, duration_secs FROM games "
            "WHERE created_at BETWEEN ? AND ?",
            (manifest["start_iso_utc"], manifest["end_iso_utc"]),
        )
    )
    print(f"games in window:  {len(games)}")
    by_diff: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for g in games:
        by_diff[g["difficulty"]].append(g)
    for d in sorted(by_diff):
        rows = by_diff[d]
        wins = sum(1 for r in rows if r["result"] == "win")
        losses = sum(1 for r in rows if r["result"] == "loss")
        ties = sum(1 for r in rows if r["result"] not in ("win", "loss"))
        avg_dur = sum(r["duration_secs"] for r in rows) / max(len(rows), 1)
        eligible = sum(1 for r in rows if r["duration_secs"] > 480.0)
        print(f"  diff {d}:  n={len(rows):>3}  W={wins:>3} L={losses:>3} other={ties:<3}  "
              f"avg_dur={avg_dur:6.1f}s  past_8min={eligible}")

    # Heuristic separation: mean win_prob per game grouped by win/loss outcome.
    win_means: list[float] = []
    loss_means: list[float] = []
    for g in games:
        avg = con.execute(
            "SELECT AVG(win_prob) FROM transitions WHERE game_id=? AND win_prob IS NOT NULL",
            (g["game_id"],),
        ).fetchone()[0]
        if avg is None:
            continue
        if g["result"] == "win":
            win_means.append(float(avg))
        elif g["result"] == "loss":
            loss_means.append(float(avg))

    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else float("nan")

    win_mean = _mean(win_means)
    loss_mean = _mean(loss_means)
    sep = win_mean - loss_mean
    print()
    print("heuristic mean win_prob (per-game avg, then averaged across games):")
    print(f"  WIN games  (n={len(win_means):>2}):  {win_mean:.3f}")
    print(f"  LOSS games (n={len(loss_means):>2}):  {loss_mean:.3f}")
    print(f"  separation:                  {sep:+.3f}")

    # Give-up rates per difficulty bucket — count log files with the resign line.
    resigned = _scan_logs_for_resigns(log_dir)
    total_logs = _scan_logs_for_total(log_dir)
    print()
    print("give-up trigger fired (from log scan):")
    for d in sorted(set(total_logs) | set(resigned)):
        n_resign = len(resigned.get(d, []))
        n_total = total_logs.get(d, 0)
        pct = (n_resign / n_total * 100.0) if n_total else 0.0
        print(f"  diff {d}:  {n_resign}/{n_total} ({pct:.1f}%)")

    # Verdict against build-plan §7.
    print()
    print("===================== VERDICT =====================")
    pass_all = True

    # 1. Heuristic separation >= 0.10 absolute.
    sep_pass = abs(sep) >= 0.10 and sep > 0  # win > loss is the meaningful direction
    status = "PASS" if sep_pass else "FAIL"
    print(f"[{status}] (1) Heuristic separation |WIN_mean - LOSS_mean| >= 0.10  "
          f"(actual: {sep:+.3f})")
    pass_all &= sep_pass

    # 2. Give-up rate at diff_win < 5%.
    dw = manifest["diff_win"]
    n_resign_w = len(resigned.get(dw, []))
    n_total_w = total_logs.get(dw, 0)
    pct_w = (n_resign_w / n_total_w * 100.0) if n_total_w else 0.0
    win_pass = pct_w < 5.0
    print(f"[{'PASS' if win_pass else 'FAIL'}] (2) Give-up rate at diff {dw} < 5%  "
          f"(actual: {pct_w:.1f}% — {n_resign_w}/{n_total_w})")
    pass_all &= win_pass

    # 3. Give-up rate at diff_loss >= 30%.
    dl = manifest["diff_loss"]
    n_resign_l = len(resigned.get(dl, []))
    n_total_l = total_logs.get(dl, 0)
    pct_l = (n_resign_l / n_total_l * 100.0) if n_total_l else 0.0
    loss_pass = pct_l >= 30.0
    print(f"[{'PASS' if loss_pass else 'FAIL'}] (3) Give-up rate at diff {dl} >= 30%  "
          f"(actual: {pct_l:.1f}% — {n_resign_l}/{n_total_l})")
    pass_all &= loss_pass
    print("===================================================")
    return 0 if pass_all else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
