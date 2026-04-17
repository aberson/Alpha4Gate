"""Aggregate per-game reward logs into per-rule contribution trends.

Reads JSONL files from ``data/reward_logs/`` (one per game) and produces a
per-rule summary suitable for the `GET /api/training/reward-trends` endpoint.

Each JSONL file has the form ``game_<id>.jsonl``, where every line is a
decision-step record emitted by ``RewardCalculator``:

    {
        "game_time": float,
        "total_reward": float,
        "fired_rules": [{"id": str, "reward": float}, ...],
        "is_terminal": bool,
        "result": str | null,
    }

Rule IDs live *inside* ``fired_rules``, not at the top level. The aggregator
walks ``fired_rules`` per line and sums ``reward`` per rule ID across all lines
in a given game, producing one ``(rule_id, total_contribution)`` data point per
game per rule.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


def aggregate_reward_trends(
    reward_logs_dir: Path, n_games: int = 100
) -> dict[str, Any]:
    """Aggregate per-rule reward contributions across recent games.

    Args:
        reward_logs_dir: Directory containing ``game_<id>.jsonl`` files.
        n_games: Maximum number of most-recent (by mtime) games to include.

    Returns:
        A dict matching design decision D8 in
        ``documentation/plans/phase-4-transparency-dashboard-plan.md``::

            {
                "rules": [
                    {
                        "rule_id": str,
                        "total_contribution": float,
                        "contribution_per_game": float,
                        "points": [
                            {"game_id": str, "timestamp": str, "contribution": float},
                            ...
                        ],
                    },
                    ...
                ],
                "n_games": int,
                "generated_at": str,  # ISO timestamp
            }

        ``total_contribution`` is the sum across all returned games for a rule,
        and ``contribution_per_game`` is ``total_contribution`` divided by the
        number of games with data for *that* rule (not ``n_games``).

    Notes:
        - Files are sorted by mtime descending; only the first ``n_games`` are
          parsed.
        - The per-game timestamp is the file mtime (step records contain no
          wall-clock time), formatted as an ISO timestamp in UTC.
        - Empty files are skipped entirely.
        - Malformed / non-JSON lines are skipped with a warning log; a
          ``json.JSONDecodeError`` does not abort aggregation.
    """
    generated_at = datetime.now(UTC).isoformat()

    if not reward_logs_dir.exists() or not reward_logs_dir.is_dir():
        return {"rules": [], "n_games": 0, "generated_at": generated_at}

    jsonl_files = [p for p in reward_logs_dir.glob("*.jsonl") if p.is_file()]
    jsonl_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    selected = jsonl_files[:n_games]

    # rule_id -> list[dict] of per-game contribution points
    rule_points: dict[str, list[dict[str, Any]]] = {}
    # rule_id -> running total across all returned games
    rule_totals: dict[str, float] = {}

    for path in selected:
        per_rule_in_game = _parse_game_file(path)
        if not per_rule_in_game:
            # Empty file or no parseable lines at all — skip entirely.
            continue

        game_id = path.stem.removeprefix("game_")
        timestamp = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()

        for rule_id, contribution in per_rule_in_game.items():
            rule_points.setdefault(rule_id, []).append(
                {
                    "game_id": game_id,
                    "timestamp": timestamp,
                    "contribution": contribution,
                }
            )
            rule_totals[rule_id] = rule_totals.get(rule_id, 0.0) + contribution

    rules: list[dict[str, Any]] = []
    for rule_id in sorted(rule_points.keys()):
        points = rule_points[rule_id]
        n_games_for_rule = len(points)
        total_contribution = rule_totals[rule_id]
        contribution_per_game = (
            total_contribution / n_games_for_rule if n_games_for_rule > 0 else 0.0
        )
        rules.append(
            {
                "rule_id": rule_id,
                "total_contribution": total_contribution,
                "contribution_per_game": contribution_per_game,
                "points": points,
            }
        )

    return {
        "rules": rules,
        "n_games": len(selected),
        "generated_at": generated_at,
    }


def _parse_game_file(path: Path) -> dict[str, float]:
    """Stream-parse a single game JSONL file and return per-rule totals.

    Returns an empty dict if the file has no parseable lines (which signals
    "skip this game entirely" to the caller).
    """
    per_rule: dict[str, float] = {}
    any_parseable = False

    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for lineno, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    _log.warning(
                        "Skipping malformed line %d in %s: %s", lineno, path, exc
                    )
                    continue

                any_parseable = True

                if not isinstance(record, dict):
                    continue

                fired_rules = record.get("fired_rules")
                if not fired_rules or not isinstance(fired_rules, list):
                    continue

                for entry in fired_rules:
                    if not isinstance(entry, dict):
                        continue
                    rule_id = entry.get("id")
                    reward = entry.get("reward")
                    if not isinstance(rule_id, str):
                        continue
                    if not isinstance(reward, (int, float)):
                        continue
                    per_rule[rule_id] = per_rule.get(rule_id, 0.0) + float(reward)
    except OSError as exc:
        _log.warning("Failed to read reward log %s: %s", path, exc)
        return {}

    if not any_parseable:
        return {}

    return per_rule
