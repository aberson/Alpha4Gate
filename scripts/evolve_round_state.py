"""Shared round-state helpers for evolve (single + parallel workers).

Houses the payload + writers previously inlined in ``scripts/evolve.py`` so
the new per-worker CLI (``scripts/evolve_worker.py``) and the parent
dispatcher (Step 3 of the evolve-parallelization plan) can share them
without circular-importing the orchestration loop.

Two new optional fields on :class:`CurrentRoundPayload` — ``worker_id`` and
``run_id`` — let the dispatcher's stale-file filter distinguish between
fresh worker writes and leftover state from a previous run. They are
omitted from the JSON output entirely when ``None`` so the existing
single-flight ``evolve_current_round.json`` shape is byte-identical to
the pre-extraction behavior.

Public surface:

- :class:`CurrentRoundPayload`
- :func:`write_current_round_state`
- :func:`clear_current_round_state`
- :func:`atomic_write_json` — atomic write helper used by the writers above

The atomic-write helper is shared with the rest of ``scripts/evolve.py``
via re-import (the parent script keeps a thin alias for back-compat with
its other state-file writers).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "CurrentRoundPayload",
    "atomic_write_json",
    "clear_current_round_state",
    "write_current_round_state",
]


def _now_iso() -> str:
    """Return an ISO-8601 UTC timestamp (seconds resolution)."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# Tuned in scripts/evolve.py to absorb the Windows ``--serve`` polling
# race; keep the shape identical when extracting.
_ATOMIC_REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write *payload* as pretty-printed, sorted JSON, atomically.

    On Windows, ``os.replace`` fails with ``PermissionError`` if any process
    holds an open handle on the target — which happens whenever the backend
    ``--serve`` is polling these state files. Retry a few times with backoff
    before giving up; the races are short-lived.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for delay in _ATOMIC_REPLACE_RETRY_DELAYS:
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(delay)
    tmp.replace(path)


@dataclass
class CurrentRoundPayload:
    """Mutable payload used to update the current-round state file.

    One instance per generation, updated in place as phases progress. The
    ``to_dict`` output is the exact shape read by
    :meth:`frontend/src/components/EvolutionTab.tsx` — keep in lock-step.

    The optional ``worker_id`` and ``run_id`` fields are populated by the
    parallel-evolve dispatcher (Step 3+) so each worker's state file is
    self-identifying; they are omitted from the JSON entirely when ``None``
    so legacy single-flight ``evolve_current_round.json`` output is
    byte-identical to the pre-extraction shape.
    """

    generation: int = 0
    # Phases: starting / mirror_games / claude_prompt / fitness /
    # stack_apply / regression / pool_refresh.
    phase: str = "starting"
    imp_title: str | None = None
    imp_rank: int | None = None
    imp_index: int | None = None
    candidate: str | None = None
    stacked_titles: list[str] = field(default_factory=list)
    new_parent: str | None = None
    prior_parent: str | None = None
    games_played: int = 0
    games_total: int = 0
    score_cand: int = 0
    score_parent: int = 0
    worker_id: int | None = None
    run_id: str | None = None

    def reset_progress(self, total: int) -> None:
        self.games_played = 0
        self.games_total = total
        self.score_cand = 0
        self.score_parent = 0

    def to_dict(self, *, active: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "active": active,
            "generation": self.generation,
            "phase": self.phase,
            "imp_title": self.imp_title,
            "imp_rank": self.imp_rank,
            "imp_index": self.imp_index,
            "candidate": self.candidate,
            "stacked_titles": list(self.stacked_titles),
            "new_parent": self.new_parent,
            "prior_parent": self.prior_parent,
            "games_played": self.games_played,
            "games_total": self.games_total,
            "score_cand": self.score_cand,
            "score_parent": self.score_parent,
            "updated_at": _now_iso(),
        }
        # Optional dispatcher-only fields: omit when None so single-flight
        # output stays byte-identical to the pre-extraction shape.
        if self.worker_id is not None:
            out["worker_id"] = self.worker_id
        if self.run_id is not None:
            out["run_id"] = self.run_id
        return out


def write_current_round_state(
    path: Path, payload: CurrentRoundPayload
) -> None:
    """Write the live per-game progress file (active=True)."""
    atomic_write_json(path, payload.to_dict(active=True))


def clear_current_round_state(path: Path) -> None:
    """Mark the current-round file inactive between phases."""
    atomic_write_json(path, {"active": False, "updated_at": _now_iso()})
