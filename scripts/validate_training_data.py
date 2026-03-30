"""Validate training data collected in the SQLite database.

Checks feature distributions, action labels, reward values, and flags anomalies.
Usage: uv run python scripts/validate_training_data.py [db_path]
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np

# 15 state feature columns in order
STATE_COLS = [
    "supply_used", "supply_cap", "minerals", "vespene", "army_supply",
    "worker_count", "base_count", "enemy_near", "enemy_supply",
    "game_time_secs", "gateway_count", "robo_count", "forge_count",
    "upgrade_count", "enemy_structure_count",
]

ACTION_NAMES = ["OPENING", "EXPAND", "ATTACK", "DEFEND", "LATE_GAME"]


def validate(db_path: str) -> bool:
    """Run all validation checks. Returns True if data is clean."""
    if not Path(db_path).exists():
        print(f"ERROR: Database not found at {db_path}")
        return False

    conn = sqlite3.connect(db_path)
    ok = True

    # --- Games summary ---
    games = conn.execute(
        "SELECT game_id, result, duration_secs, total_reward FROM games ORDER BY rowid"
    ).fetchall()
    print(f"=== Games: {len(games)} ===")
    for gid, result, duration, reward in games:
        print(f"  {gid}: {result}  duration={duration:.0f}s  reward={reward:.2f}")

    wins = sum(1 for _, r, _, _ in games if r == "win")
    print(f"  Win rate: {wins}/{len(games)} ({100*wins/max(len(games),1):.0f}%)\n")

    # --- Transitions ---
    rows = conn.execute(
        f"SELECT {', '.join(STATE_COLS)}, action, reward FROM transitions"
    ).fetchall()
    n = len(rows)
    print(f"=== Transitions: {n} ===")
    if n == 0:
        print("ERROR: No transitions found!")
        return False

    states = np.array([r[:15] for r in rows], dtype=np.float64)
    actions = np.array([r[15] for r in rows], dtype=np.int64)
    rewards = np.array([r[16] for r in rows], dtype=np.float64)

    # --- Feature statistics ---
    print("\n=== Feature Statistics ===")
    print(f"{'Feature':<25} {'Min':>8} {'Max':>8} {'Mean':>8} {'Std':>8}")
    print("-" * 60)
    for i, col in enumerate(STATE_COLS):
        col_data = states[:, i]
        print(
            f"{col:<25} {col_data.min():>8.1f} {col_data.max():>8.1f} "
            f"{col_data.mean():>8.1f} {col_data.std():>8.1f}"
        )

    # --- Check for all-zero rows ---
    zero_rows = np.all(states == 0, axis=1).sum()
    if zero_rows > 0:
        print(f"\nWARNING: {zero_rows} all-zero state rows found!")
        ok = False
    else:
        print("\nAll-zero rows: 0 (clean)")

    # --- Check for NaN ---
    nan_count = np.isnan(states).sum()
    if nan_count > 0:
        print(f"WARNING: {nan_count} NaN values found!")
        ok = False
    else:
        print("NaN values: 0 (clean)")

    # --- Action distribution ---
    print("\n=== Action Distribution ===")
    for a in range(5):
        count = (actions == a).sum()
        pct = 100 * count / n
        label = ACTION_NAMES[a] if a < len(ACTION_NAMES) else f"ACTION_{a}"
        print(f"  {a} ({label:<10}): {count:>5} ({pct:>5.1f}%)")
        if count == 0:
            print(f"    WARNING: action {a} never appears!")
            ok = False

    # --- Reward distribution ---
    print("\n=== Reward Distribution ===")
    print(f"  Min:  {rewards.min():.4f}")
    print(f"  Max:  {rewards.max():.4f}")
    print(f"  Mean: {rewards.mean():.4f}")
    print(f"  Std:  {rewards.std():.4f}")
    pos = (rewards > 0).sum()
    neg = (rewards < 0).sum()
    zero = (rewards == 0).sum()
    print(f"  Positive: {pos}  Negative: {neg}  Zero: {zero}")

    # --- Per-game summary ---
    print("\n=== Per-Game Summary ===")
    game_ids = conn.execute(
        "SELECT game_id, result FROM games ORDER BY rowid"
    ).fetchall()
    for gid, result in game_ids:
        row = conn.execute(
            "SELECT COUNT(*), SUM(reward) FROM transitions WHERE game_id = ?",
            (gid,),
        ).fetchone()
        steps, total_r = row[0], row[1] or 0.0
        print(f"  {gid}: {result:<6} steps={steps:>4}  total_reward={total_r:>7.2f}")

    conn.close()

    print(f"\n{'PASS' if ok else 'ISSUES FOUND'}")
    return ok


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "data/training.db"
    success = validate(db)
    sys.exit(0 if success else 1)
