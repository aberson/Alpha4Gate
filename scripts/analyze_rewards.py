"""Analyze reward log from --reward-log runs.

Reads data/reward_log.jsonl and reports per-rule firing frequency, total
contribution, and flags rules that never fire or fire too often.

Usage: uv run python scripts/analyze_rewards.py [log_path]
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def analyze(log_path: str) -> None:
    if not Path(log_path).exists():
        print(f"ERROR: Log file not found at {log_path}")
        sys.exit(1)

    entries = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    n = len(entries)
    print(f"=== Reward Log: {n} steps ===\n")

    if n == 0:
        print("No entries found.")
        return

    # Per-rule statistics
    rule_fires: Counter[str] = Counter()
    rule_total_reward: dict[str, float] = {}
    rewards = []

    for entry in entries:
        rewards.append(entry["total_reward"])
        for rule in entry.get("fired_rules", []):
            rid = rule["id"]
            rule_fires[rid] += 1
            rule_total_reward[rid] = rule_total_reward.get(rid, 0.0) + rule["reward"]

    # Summary
    print("=== Per-Rule Firing Frequency ===")
    print(f"{'Rule':<25} {'Fires':>6} {'Rate':>7} {'Total':>8}")
    print("-" * 50)

    all_rules = sorted(set(rule_fires.keys()))
    dead_rules = []
    noisy_rules = []
    for rid in all_rules:
        fires = rule_fires[rid]
        rate = fires / n
        total = rule_total_reward.get(rid, 0.0)
        print(f"{rid:<25} {fires:>6} {rate:>6.1%} {total:>8.3f}")
        if fires == 0:
            dead_rules.append(rid)
        if rate > 0.8:
            noisy_rules.append(rid)

    # Reward curve
    print("\n=== Reward Distribution ===")
    import numpy as np

    r = np.array(rewards)
    print(f"  Min:  {r.min():.4f}")
    print(f"  Max:  {r.max():.4f}")
    print(f"  Mean: {r.mean():.4f}")
    print(f"  Std:  {r.std():.4f}")

    # Flags
    if dead_rules:
        print(f"\nWARNING: Dead rules (never fired): {dead_rules}")
    if noisy_rules:
        print(f"\nWARNING: Noisy rules (>80% fire rate): {noisy_rules}")

    fired_count = len(all_rules)
    print(f"\n{fired_count} rules fired at least once out of {n} steps")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/reward_log.jsonl"
    analyze(path)
