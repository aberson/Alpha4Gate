"""Extract game state from burnysc2 bot state into typed dicts."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sc2.bot_ai import BotAI


def observe(bot: BotAI, actions_taken: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Extract current game state from a BotAI instance into a log-ready dict.

    Args:
        bot: A BotAI instance mid-game (has valid state).
        actions_taken: Optional list of action dicts to include in the entry.

    Returns:
        Dict matching the JSONL log entry schema.
    """
    # Count units by type name
    unit_counts: Counter[str] = Counter()
    for unit in bot.all_own_units:
        unit_counts[unit.name] += 1

    units = [{"type": name, "count": count} for name, count in unit_counts.most_common()]

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "game_step": bot.state.game_loop,
        "game_time_seconds": round(bot.time, 1),
        "minerals": bot.minerals,
        "vespene": bot.vespene,
        "supply_used": int(bot.supply_used),
        "supply_cap": int(bot.supply_cap),
        "units": units,
        "actions_taken": actions_taken or [],
        "score": bot.state.score.score,
    }
