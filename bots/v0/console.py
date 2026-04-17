"""Real-time one-line console status output."""

from __future__ import annotations

import sys
from typing import Any


def format_status(entry: dict[str, Any]) -> str:
    """Format a log entry as a one-line console status string.

    Args:
        entry: A log entry dict from observer.observe().

    Returns:
        Formatted string like:
        [Step 1024 | 1:04] Minerals: 350  Gas: 125  Supply: 23/31  Score: 1250  Units: 21
    """
    game_time = entry.get("game_time_seconds", 0.0)
    minutes = int(game_time) // 60
    seconds = int(game_time) % 60

    total_units = sum(u.get("count", 0) for u in entry.get("units", []))

    return (
        f"[Step {entry.get('game_step', 0)} | {minutes}:{seconds:02d}] "
        f"Minerals: {entry.get('minerals', 0)}  "
        f"Gas: {entry.get('vespene', 0)}  "
        f"Supply: {entry.get('supply_used', 0)}/{entry.get('supply_cap', 0)}  "
        f"Score: {int(entry.get('score', 0))}  "
        f"Units: {total_units}"
    )


def print_status(entry: dict[str, Any]) -> None:
    """Print a one-line status, overwriting the previous line."""
    line = format_status(entry)
    sys.stdout.write(f"\r{line}  ")
    sys.stdout.flush()
