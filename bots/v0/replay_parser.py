"""Parse SC2Replay files, extract stats and timelines."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TimelineEvent:
    """A single event in a replay timeline."""

    game_time_seconds: float
    event: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "game_time_seconds": self.game_time_seconds,
            "event": self.event,
            "detail": self.detail,
        }


@dataclass
class ReplayStats:
    """Aggregate stats from a replay."""

    minerals_collected: int = 0
    gas_collected: int = 0
    units_produced: int = 0
    units_lost: int = 0
    structures_built: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "minerals_collected": self.minerals_collected,
            "gas_collected": self.gas_collected,
            "units_produced": self.units_produced,
            "units_lost": self.units_lost,
            "structures_built": self.structures_built,
        }


@dataclass
class ParsedReplay:
    """Full parsed replay data."""

    replay_id: str
    map_name: str = ""
    duration_seconds: float = 0.0
    result: str = ""
    timeline: list[TimelineEvent] = field(default_factory=list)
    stats: ReplayStats = field(default_factory=ReplayStats)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to API response format."""
        return {
            "id": self.replay_id,
            "map": self.map_name,
            "duration_seconds": self.duration_seconds,
            "result": self.result,
            "timeline": [e.to_dict() for e in self.timeline],
            "stats": self.stats.to_dict(),
        }


def parse_replay_from_log(log_path: Path) -> ParsedReplay:
    """Parse a game log (JSONL) file to extract replay-like data.

    Since SC2Replay binary parsing requires the SC2 client or specialized
    libraries, this function uses the JSONL game logs as a proxy for
    replay data. Each log entry is a game state snapshot.

    Args:
        log_path: Path to a game_*.jsonl log file.

    Returns:
        ParsedReplay with timeline and stats extracted from log entries.
    """
    replay_id = log_path.stem.replace("game_", "")
    timeline: list[TimelineEvent] = []
    stats = ReplayStats()

    if not log_path.exists():
        return ParsedReplay(replay_id=replay_id, stats=stats)

    entries: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").strip().splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not entries:
        return ParsedReplay(replay_id=replay_id, stats=stats)

    # Extract timeline events from actions
    for entry in entries:
        game_time = entry.get("game_time_seconds", 0.0)
        for action in entry.get("actions_taken", []):
            timeline.append(
                TimelineEvent(
                    game_time_seconds=game_time,
                    event=action.get("action", "unknown"),
                    detail=action.get("target", ""),
                )
            )

    # Extract stats from last entry
    last = entries[-1]

    duration = last.get("game_time_seconds", 0.0)

    # Estimate minerals collected from score progression
    stats.minerals_collected = last.get("minerals", 0) + int(last.get("score", 0) * 0.5)
    stats.gas_collected = last.get("vespene", 0)

    # Count units from last snapshot
    for unit_entry in last.get("units", []):
        unit_type = unit_entry.get("type", "")
        count = unit_entry.get("count", 0)
        if unit_type in ("Nexus", "Gateway", "Pylon", "CyberneticsCore", "Assimilator",
                         "RoboticsFacility", "TwilightCouncil", "Forge", "Stargate"):
            stats.structures_built += count
        else:
            stats.units_produced += count

    return ParsedReplay(
        replay_id=replay_id,
        duration_seconds=duration,
        timeline=timeline,
        stats=stats,
    )


def list_replay_logs(log_dir: Path) -> list[Path]:
    """List all game log files in a directory.

    Args:
        log_dir: Path to the logs directory.

    Returns:
        List of log file paths, sorted newest first.
    """
    if not log_dir.exists():
        return []
    return sorted(log_dir.glob("game_*.jsonl"), reverse=True)
