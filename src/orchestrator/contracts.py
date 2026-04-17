"""Frozen cross-version contracts.

Every `bots/vN/` must honor these interfaces. Changing any of them is a
human-PR event per the master plan; they are NOT inside the
`/improve-bot-advised` sandbox once Phase 5 lands.

The dataclasses are frozen so instances cannot be mutated after construction.
Round-trip is via `to_json()` / `from_json(...)` — no third-party schema
library; stdlib `json` is enough and avoids a new runtime dep.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["p1", "p2", "solo"]
Outcome = Literal["win", "loss", "draw", "crash"]


@dataclass(frozen=True)
class BotSpawnArgs:
    """CLI contract for `python -m bots.vN --role ... --map ...`.

    Mirrors the sc2ai / aiarena ladder protocol that Phase 0 proved out.
    `sc2_connect` is the Proxy-layer WebSocket port; `start_port` is the
    shared base port both bots use to reconstruct an identical Portconfig.
    """

    role: Role
    map_name: str
    sc2_connect: int
    start_port: int
    result_out: str
    seed: int
    ladder_server: str = "127.0.0.1"
    realtime: bool = False

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    @classmethod
    def from_json(cls, data: str | bytes) -> BotSpawnArgs:
        return cls(**json.loads(data))


@dataclass(frozen=True)
class MatchResult:
    """One row of `data/selfplay_results.jsonl`.

    Written by the orchestrator after a subprocess self-play match completes.
    `error` is non-null only when `outcome == "crash"`.
    """

    version: str
    match_id: str
    outcome: Outcome
    duration_s: float
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    @classmethod
    def from_json(cls, data: str | bytes) -> MatchResult:
        payload = json.loads(data)
        return cls(**payload)


@dataclass(frozen=True)
class VersionFingerprint:
    """Immutable signature for a `bots/vN/` stack.

    `obs_spec_hash` is a stable hash of the ordered feature-slot names from
    `learning/features.py` `_FEATURE_SPEC`. Used by Phase 4's promotion gate
    to flag Elo gains that come from feature-spec changes rather than policy
    improvements.
    """

    feature_dim: int
    action_space_size: int
    obs_spec_hash: str


@dataclass(frozen=True)
class Manifest:
    """`bots/<v>/manifest.json` schema.

    `best` and `previous_best` are checkpoint names (not full paths). They
    live under `bots/<v>/data/checkpoints/`. `parent` is the version this was
    snapshotted from (None for v0). `elo` is the most recent ladder snapshot.

    Finding #11 fix (Step 1.3): `best` may NOT be None — the seed-or-fail
    invariant is enforced at load time in `orchestrator.registry`.
    """

    version: str
    best: str
    previous_best: str | None
    parent: str | None
    git_sha: str
    timestamp: str
    elo: float
    fingerprint: VersionFingerprint
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        payload = dataclasses.asdict(self)
        return json.dumps(payload, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, data: str | bytes) -> Manifest:
        payload = json.loads(data)
        fp = payload.pop("fingerprint")
        return cls(fingerprint=VersionFingerprint(**fp), **payload)


@dataclass(frozen=True)
class SelfPlayRecord:
    """One row of ``data/selfplay_results.jsonl``.

    Written by :func:`orchestrator.selfplay.run_batch` after each game.
    ``winner`` is the version string of the winning side, or ``None`` for
    draws / crashes.
    """

    match_id: str
    p1_version: str
    p2_version: str
    winner: str | None
    map_name: str
    duration_s: float
    seat_swap: bool
    timestamp: str
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    @classmethod
    def from_json(cls, data: str | bytes) -> SelfPlayRecord:
        return cls(**json.loads(data))


__all__ = [
    "BotSpawnArgs",
    "Manifest",
    "MatchResult",
    "Outcome",
    "Role",
    "SelfPlayRecord",
    "VersionFingerprint",
]
