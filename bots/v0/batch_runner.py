"""Run N games in sequence, aggregate statistics."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class GameRecord:
    """Result of a single game."""

    timestamp: str
    map_name: str
    opponent: str
    result: str  # "win" or "loss"
    duration_seconds: float
    build_order_used: str
    score: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "timestamp": self.timestamp,
            "map": self.map_name,
            "opponent": self.opponent,
            "result": self.result,
            "duration_seconds": self.duration_seconds,
            "build_order_used": self.build_order_used,
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameRecord:
        """Deserialize from dict."""
        return cls(
            timestamp=data["timestamp"],
            map_name=data.get("map", ""),
            opponent=data.get("opponent", ""),
            result=data["result"],
            duration_seconds=data.get("duration_seconds", 0.0),
            build_order_used=data.get("build_order_used", ""),
            score=data.get("score", 0),
        )


@dataclass
class StatsAggregates:
    """Aggregated game statistics."""

    total_wins: int = 0
    total_losses: int = 0
    by_map: dict[str, dict[str, int]] = field(default_factory=dict)
    by_opponent: dict[str, dict[str, int]] = field(default_factory=dict)
    by_build_order: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
            "by_map": self.by_map,
            "by_opponent": self.by_opponent,
            "by_build_order": self.by_build_order,
        }


def compute_aggregates(games: list[GameRecord]) -> StatsAggregates:
    """Compute aggregate statistics from a list of game records.

    Args:
        games: List of GameRecord instances.

    Returns:
        StatsAggregates with win/loss breakdowns.
    """
    agg = StatsAggregates()

    for game in games:
        is_win = game.result == "win"
        if is_win:
            agg.total_wins += 1
        else:
            agg.total_losses += 1

        # By map
        if game.map_name not in agg.by_map:
            agg.by_map[game.map_name] = {"wins": 0, "losses": 0}
        agg.by_map[game.map_name]["wins" if is_win else "losses"] += 1

        # By opponent
        if game.opponent not in agg.by_opponent:
            agg.by_opponent[game.opponent] = {"wins": 0, "losses": 0}
        agg.by_opponent[game.opponent]["wins" if is_win else "losses"] += 1

        # By build order
        if game.build_order_used not in agg.by_build_order:
            agg.by_build_order[game.build_order_used] = {"wins": 0, "losses": 0}
        agg.by_build_order[game.build_order_used]["wins" if is_win else "losses"] += 1

    return agg


def load_stats(path: Path) -> tuple[list[GameRecord], StatsAggregates]:
    """Load stats from a JSON file.

    Args:
        path: Path to stats.json.

    Returns:
        Tuple of (game records, aggregates). Empty if file doesn't exist.
    """
    if not path.exists():
        return [], StatsAggregates()

    data = json.loads(path.read_text(encoding="utf-8"))
    games = [GameRecord.from_dict(g) for g in data.get("games", [])]
    agg = compute_aggregates(games)
    return games, agg


def save_stats(games: list[GameRecord], path: Path) -> None:
    """Save stats to a JSON file.

    Recomputes aggregates from the full game list before saving.

    Args:
        games: List of all game records.
        path: Path to write stats.json.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    agg = compute_aggregates(games)
    data = {
        "games": [g.to_dict() for g in games],
        "aggregates": agg.to_dict(),
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def append_stats_game(stats_path: Path, record: GameRecord) -> None:
    """Append a single game record to ``stats.json`` and recompute aggregates.

    This is the per-game seam used by the trainer (``SC2Env._sync_game``) so
    that dashboard surfaces reading ``data/stats.json`` — Stats tab and any
    other legacy aggregators — see trainer games as they happen, not only
    after a ``--batch`` run.

    The batch path still uses :func:`save_stats` to write the entire list
    atomically at the end of a batch. This helper is deliberately additive:
    it loads the existing file (or initializes an empty record), appends the
    new record, recomputes aggregates, and writes back via
    :func:`save_stats`. Behaviour for the batch code path is unchanged.

    Args:
        stats_path: Path to ``data/stats.json``. Parent directories are
            created on demand.
        record: The completed :class:`GameRecord` to append.
    """
    games, _ = load_stats(stats_path)
    games.append(record)
    save_stats(games, stats_path)


def record_game(
    games: list[GameRecord],
    *,
    map_name: str,
    opponent: str,
    result: str,
    duration_seconds: float,
    build_order_used: str,
    score: int,
) -> GameRecord:
    """Create a new game record and append it to the list.

    Args:
        games: Mutable list of existing game records.
        map_name: Map that was played.
        opponent: Opponent identifier.
        result: "win" or "loss".
        duration_seconds: Game duration.
        build_order_used: Build order ID used.
        score: Final score.

    Returns:
        The newly created GameRecord.
    """
    record = GameRecord(
        timestamp=datetime.now(UTC).isoformat(),
        map_name=map_name,
        opponent=opponent,
        result=result,
        duration_seconds=duration_seconds,
        build_order_used=build_order_used,
        score=score,
    )
    games.append(record)
    return record
