"""Elo ladder + cross-version promotion gate (Phase 4).

Ranks every ``bots/vN/`` snapshot by strength using standard Elo (K=32),
top-N registry versions plus ``bots/current``, and gates cross-version
promotion on Elo gain ≥ threshold.

Public surface:

- :func:`elo_expected` — expected score between two ratings.
- :func:`elo_update` — new rating after one game.
- :func:`seed_version` — initialise a new version's Elo from manifest / parent / default.
- :func:`update_elo` — apply one :class:`SelfPlayRecord` to standings + head-to-head.
- :func:`load_ladder` / :func:`save_ladder` — read/write ``data/bot_ladder.json``.
- :func:`get_top_n` — top N versions by Elo.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

from orchestrator.contracts import LadderEntry, PromotionResult, SelfPlayRecord
from orchestrator.registry import _repo_root, get_manifest

_log = logging.getLogger(__name__)

DEFAULT_K = 32
DEFAULT_ELO = 1000.0

__all__ = [
    "DEFAULT_ELO",
    "DEFAULT_K",
    "check_promotion",
    "elo_expected",
    "elo_update",
    "get_top_n",
    "ladder_replay",
    "ladder_update",
    "load_ladder",
    "save_ladder",
    "seed_version",
    "update_elo",
]


# ---------------------------------------------------------------------------
# Elo math (pure functions)
# ---------------------------------------------------------------------------


def elo_expected(rating_a: float, rating_b: float) -> float:
    """Expected score of player A against player B.

    Returns a value in ``(0, 1)``.  For equal ratings, returns 0.5.
    """
    exponent: float = (rating_b - rating_a) / 400.0
    return float(1.0 / (1.0 + 10.0**exponent))


def elo_update(
    rating: float, expected: float, actual: float, k: float = DEFAULT_K
) -> float:
    """Compute new rating after one game.

    Parameters
    ----------
    rating:
        Current rating.
    expected:
        Expected score from :func:`elo_expected`.
    actual:
        Actual score: ``1.0`` for win, ``0.5`` for draw, ``0.0`` for loss.
    k:
        K-factor (default 32).
    """
    return rating + k * (actual - expected)


# ---------------------------------------------------------------------------
# Ladder state I/O
# ---------------------------------------------------------------------------

#: Keys in the top-level ``bot_ladder.json`` object.
_STANDINGS_KEY = "standings"
_H2H_KEY = "head_to_head"


def _default_ladder_path() -> Path:
    return _repo_root() / "data" / "bot_ladder.json"


def load_ladder(
    path: Path | None = None,
) -> tuple[dict[str, LadderEntry], dict[str, dict[str, dict[str, int]]]]:
    """Load ladder standings + head-to-head from ``data/bot_ladder.json``.

    Returns ``(standings, head_to_head)`` where *standings* maps version
    string → :class:`LadderEntry` and *head_to_head* maps
    ``version_a → version_b → {"wins": N, "losses": N, "draws": N}``.

    Returns ``({}, {})`` if the file does not exist.
    """
    p = path or _default_ladder_path()
    if not p.is_file():
        return {}, {}
    raw: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    standings: dict[str, LadderEntry] = {}
    for version, entry in raw.get(_STANDINGS_KEY, {}).items():
        standings[version] = LadderEntry(
            version=version,
            elo=entry["elo"],
            games_played=entry["games_played"],
            last_updated=entry["last_updated"],
        )
    h2h: dict[str, dict[str, dict[str, int]]] = raw.get(_H2H_KEY, {})
    return standings, h2h


def save_ladder(
    standings: dict[str, LadderEntry],
    head_to_head: dict[str, dict[str, dict[str, int]]],
    path: Path | None = None,
) -> None:
    """Write ladder standings + head-to-head to ``data/bot_ladder.json``.

    Creates parent directories if needed.  Writes atomically (full overwrite).
    """
    p = path or _default_ladder_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    standings_dict: dict[str, dict[str, object]] = {}
    for version, entry in standings.items():
        standings_dict[version] = {
            "elo": round(entry.elo, 1),
            "games_played": entry.games_played,
            "last_updated": entry.last_updated,
        }

    payload = {
        _STANDINGS_KEY: standings_dict,
        _H2H_KEY: head_to_head,
    }
    p.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def seed_version(
    version: str, standings: dict[str, LadderEntry]
) -> LadderEntry:
    """Create an initial :class:`LadderEntry` for *version*.

    Seeding hierarchy:

    1. If the version has a manifest, use ``Manifest.elo``.
    2. Else if the manifest names a ``parent`` and the parent is in
       *standings*, inherit the parent's current Elo.
    3. Else fallback to :data:`DEFAULT_ELO` (1000.0).
    """
    elo = DEFAULT_ELO
    try:
        manifest = get_manifest(version)
        elo = manifest.elo
        _log.debug("seeded %s from manifest elo=%.1f", version, elo)
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        # No usable manifest — check if we can infer from parent
        _log.debug("seeded %s at default elo=%.1f", version, elo)

    return LadderEntry(
        version=version,
        elo=elo,
        games_played=0,
        last_updated=datetime.now(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# Elo update (one game)
# ---------------------------------------------------------------------------


def _ensure_h2h(
    h2h: dict[str, dict[str, dict[str, int]]], va: str, vb: str
) -> None:
    """Ensure head-to-head entries exist for both directions."""
    blank = {"wins": 0, "losses": 0, "draws": 0}
    h2h.setdefault(va, {}).setdefault(vb, dict(blank))
    h2h.setdefault(vb, {}).setdefault(va, dict(blank))


def update_elo(
    standings: dict[str, LadderEntry],
    head_to_head: dict[str, dict[str, dict[str, int]]],
    record: SelfPlayRecord,
    k: float = DEFAULT_K,
) -> None:
    """Apply one :class:`SelfPlayRecord` to *standings* and *head_to_head*.

    Mutates both dicts in place.  Seeds versions that are not yet in
    *standings* via :func:`seed_version`.
    """
    p1 = record.p1_version
    p2 = record.p2_version

    if p1 not in standings:
        standings[p1] = seed_version(p1, standings)
    if p2 not in standings:
        standings[p2] = seed_version(p2, standings)

    r1 = standings[p1].elo
    r2 = standings[p2].elo

    e1 = elo_expected(r1, r2)
    e2 = elo_expected(r2, r1)

    # Determine actual scores
    if record.winner == p1:
        s1, s2 = 1.0, 0.0
    elif record.winner == p2:
        s1, s2 = 0.0, 1.0
    else:
        s1, s2 = 0.5, 0.5

    now = datetime.now(UTC).isoformat()

    standings[p1] = LadderEntry(
        version=p1,
        elo=elo_update(r1, e1, s1, k),
        games_played=standings[p1].games_played + 1,
        last_updated=now,
    )
    standings[p2] = LadderEntry(
        version=p2,
        elo=elo_update(r2, e2, s2, k),
        games_played=standings[p2].games_played + 1,
        last_updated=now,
    )

    # Update head-to-head
    _ensure_h2h(head_to_head, p1, p2)
    if record.winner == p1:
        head_to_head[p1][p2]["wins"] += 1
        head_to_head[p2][p1]["losses"] += 1
    elif record.winner == p2:
        head_to_head[p2][p1]["wins"] += 1
        head_to_head[p1][p2]["losses"] += 1
    else:
        head_to_head[p1][p2]["draws"] += 1
        head_to_head[p2][p1]["draws"] += 1


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def get_top_n(standings: dict[str, LadderEntry], n: int = 3) -> list[str]:
    """Return the top *n* versions by Elo, sorted descending."""
    ranked = sorted(standings.values(), key=lambda e: e.elo, reverse=True)
    return [e.version for e in ranked[:n]]


# ---------------------------------------------------------------------------
# Round-robin ladder update (Step 4.2)
# ---------------------------------------------------------------------------


def ladder_update(
    versions: list[str] | None,
    games_per_pair: int,
    map_name: str,
    ladder_path: Path | None = None,
) -> dict[str, LadderEntry]:
    """Run round-robin self-play and update the Elo ladder.

    Parameters
    ----------
    versions:
        Explicit list of version strings.  If ``None``, uses the top-3
        from existing standings plus ``current_version()``, deduplicated.
    games_per_pair:
        Games to play for each ``(vA, vB)`` pair.
    map_name:
        SC2 map name to use for all games.
    ladder_path:
        Override for the ladder JSON file location.

    Returns
    -------
    The final standings dict after all games are processed.
    """
    from orchestrator import registry, selfplay

    standings, h2h = load_ladder(ladder_path)

    if versions is None:
        top = get_top_n(standings, n=3)
        current = registry.current_version()
        seen: set[str] = set()
        deduped: list[str] = []
        for v in [*top, current]:
            if v not in seen:
                seen.add(v)
                deduped.append(v)
        versions = deduped

    # Seed all versions into standings before playing.
    for v in versions:
        if v not in standings:
            standings[v] = seed_version(v, standings)

    # Round-robin: combinations (unordered pairs — seat alternation is
    # handled inside run_batch).
    for va, vb in combinations(versions, 2):
        _log.info("ladder_update: %s vs %s (%d games)", va, vb, games_per_pair)
        records = selfplay.run_batch(va, vb, games_per_pair, map_name)
        for rec in records:
            update_elo(standings, h2h, rec)

    save_ladder(standings, h2h, ladder_path)
    return standings


# ---------------------------------------------------------------------------
# Replay from JSONL (Step 4.2)
# ---------------------------------------------------------------------------


def ladder_replay(
    jsonl_path: Path,
    ladder_path: Path | None = None,
) -> dict[str, LadderEntry]:
    """Rebuild an Elo ladder by replaying a JSONL results file.

    Starts from *empty* standings — not additive to any existing ladder
    file.  Useful for bootstrapping or recovering from corruption.

    Parameters
    ----------
    jsonl_path:
        Path to a ``selfplay_results.jsonl`` file.
    ladder_path:
        Override for the output ladder JSON file location.
    """
    standings: dict[str, LadderEntry] = {}
    h2h: dict[str, dict[str, dict[str, int]]] = {}

    text = jsonl_path.read_text(encoding="utf-8")
    for line in text.strip().splitlines():
        record = SelfPlayRecord.from_json(line)
        update_elo(standings, h2h, record)

    save_ladder(standings, h2h, ladder_path)
    return standings


# ---------------------------------------------------------------------------
# Cross-version promotion gate (Step 4.3)
# ---------------------------------------------------------------------------


def check_promotion(
    candidate: str,
    parent: str,
    games: int,
    map_name: str = "Simple64",
    *,
    elo_threshold: float = 10.0,
    ladder_path: Path | None = None,
) -> PromotionResult:
    """Run a head-to-head evaluation and decide whether to promote.

    Plays *games* self-play matches between *candidate* and *parent*,
    computes Elo delta from a fresh (isolated) standing, and promotes
    if ``elo_delta >= elo_threshold``.

    When promoted, :func:`orchestrator.snapshot.snapshot_current` is called
    to snapshot the current bot as a new version.

    Parameters
    ----------
    candidate:
        Version string for the candidate bot.
    parent:
        Version string for the parent bot.
    games:
        Number of self-play games to run.
    map_name:
        SC2 map name.
    elo_threshold:
        Minimum Elo gain required for promotion.
    ladder_path:
        Unused — reserved for future integration with the persistent
        ladder file.

    Returns
    -------
    A :class:`PromotionResult` summarising the gate outcome.
    """
    from orchestrator import selfplay

    records = selfplay.run_batch(candidate, parent, games, map_name)

    # Fresh isolated standings for just these two versions.
    standings: dict[str, LadderEntry] = {}
    h2h: dict[str, dict[str, dict[str, int]]] = {}

    for rec in records:
        update_elo(standings, h2h, rec)

    candidate_elo = (
        standings[candidate].elo if candidate in standings else DEFAULT_ELO
    )
    parent_elo = standings[parent].elo if parent in standings else DEFAULT_ELO
    elo_delta = candidate_elo - parent_elo

    promoted = elo_delta >= elo_threshold

    if promoted:
        reason = (
            f"promoted: elo_delta={elo_delta:+.1f} >= "
            f"threshold={elo_threshold:.1f} over {games} games"
        )
    else:
        reason = (
            f"not promoted: elo_delta={elo_delta:+.1f} < "
            f"threshold={elo_threshold:.1f} over {games} games"
        )

    if promoted:
        from orchestrator import snapshot

        snapshot.snapshot_current()

    _log.info("check_promotion: %s", reason)

    return PromotionResult(
        candidate=candidate,
        parent=parent,
        elo_delta=elo_delta,
        games_played=games,
        wr_vs_sc2=None,
        promoted=promoted,
        reason=reason,
    )
