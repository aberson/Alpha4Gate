"""Subprocess self-play runner (Phase 3).

Absorbs the port-collision workaround from the Phase 0 spike
(``scripts/spike_subprocess_selfplay.py``) and exposes a batch runner::

    python scripts/selfplay.py --p1 v0 --p2 v0 --games 20 --map Simple64

Per-game results go to ``data/selfplay_results.jsonl`` (shared, append-only).

Public surface:

- :func:`pfsp_sample` — PFSP-lite opponent sampling (pure, no SC2 dep).
- :func:`run_batch` — run *N* games between two versioned bots.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.contracts import SelfPlayRecord
from orchestrator.registry import _repo_root, list_versions

if TYPE_CHECKING:
    from sc2.main import GameMatch
    from sc2.player import AbstractPlayer, BotProcess

_log = logging.getLogger(__name__)

__all__ = [
    "pfsp_sample",
    "run_batch",
]

# ---------------------------------------------------------------------------
# Port-collision monkey-patch (from Phase 0 spike)
# ---------------------------------------------------------------------------

_PATCH_INSTALLED = False
_BLOCKED_PORTS: set[int] = set()


def _install_port_collision_patch() -> None:
    """Idempotent monkey-patch for burnysc2 7.1.3 port-collision bug.

    ``Portconfig.contiguous_ports`` picks only the *first* port via
    portpicker; the remaining 4 LAN ports are verified free but not
    reserved, so later ``pick_unused_port`` calls can return them.
    This patch pushes all picked ports into a shared blocklist and wraps
    ``pick_unused_port`` to retry when it returns a blocked port.

    See ``documentation/wiki/subprocess-selfplay.md`` §1 for full details.
    """
    global _PATCH_INSTALLED  # noqa: PLW0603
    if _PATCH_INSTALLED:
        return

    import portpicker  # type: ignore[import-untyped]
    from sc2.portconfig import Portconfig

    orig_pick = portpicker.pick_unused_port
    orig_contiguous = Portconfig.contiguous_ports

    def _pick_avoiding_blocked(*args: object, **kwargs: object) -> int:
        for _ in range(64):
            port: int = orig_pick(*args, **kwargs)
            if port not in _BLOCKED_PORTS:
                return port
        raise portpicker.NoFreePortFoundError()

    @classmethod  # type: ignore[misc]
    def _contiguous_with_blocklist(
        cls: type[Portconfig], guests: int = 1, attempts: int = 40
    ) -> Portconfig:
        pc: Portconfig = orig_contiguous(guests=guests, attempts=attempts)
        _BLOCKED_PORTS.update(pc.server)
        # burnysc2 7.1.3 runtime: pc.players is list[list[int]], but the
        # type stub says list[int] | None.  Runtime is authoritative.
        for pair in pc.players:
            _BLOCKED_PORTS.update(pair)  # type: ignore[arg-type]
        return pc

    portpicker.pick_unused_port = _pick_avoiding_blocked
    Portconfig.contiguous_ports = _contiguous_with_blocklist  # type: ignore[assignment]
    _PATCH_INSTALLED = True
    _log.info("port-collision patch installed")


# ---------------------------------------------------------------------------
# PFSP-lite sampling
# ---------------------------------------------------------------------------


def pfsp_sample(
    pool: list[str],
    win_rates: dict[str, float],
    *,
    temperature: float = 1.0,
    rng: random.Random | None = None,
) -> str:
    """Sample an opponent from *pool* using PFSP-lite weighting.

    Weight for opponent *i*: ``w_i = (1 - wr_i) ** temperature``.

    * Win-rate-1.0 opponents get weight 0 and are never sampled.
    * If *win_rates* has no entry for an opponent, that opponent is treated
      as cold-start and gets uniform weight (equivalent to ``wr = 0``).
    * ``temperature = 0`` → uniform sampling regardless of win rates.
    * Empty *pool* raises :class:`ValueError`.
    * If all weights are zero (every opponent has 100 % win rate) raises
      :class:`ValueError` — there is no valid opponent to sample.

    Parameters
    ----------
    pool:
        Version strings to sample from (e.g. ``["v0", "v1", "v2"]``).
    win_rates:
        Mapping of version string → win rate in ``[0, 1]``.  Missing
        entries are treated as ``0.0`` (cold-start).
    temperature:
        Exponent on ``(1 - wr)``.  Higher values upweight weaker opponents
        more aggressively.  ``0`` collapses to uniform.
    rng:
        Optional :class:`random.Random` instance for deterministic tests.
    """
    if not pool:
        raise ValueError("pool must be non-empty")

    _rng = rng or random.Random()

    if temperature == 0.0:
        return _rng.choice(pool)

    weights: list[float] = []
    for v in pool:
        wr = win_rates.get(v, 0.0)
        weights.append((1.0 - wr) ** temperature)

    total = sum(weights)
    if total == 0.0:
        raise ValueError(
            "all opponents have 100% win rate — no valid opponent to sample"
        )

    return _rng.choices(pool, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------


def _validate_versions(*versions: str) -> None:
    """Raise ``ValueError`` if any version is not in the registry."""
    known = set(list_versions())
    for v in versions:
        if v not in known:
            raise ValueError(
                f"version {v!r} not found in registry "
                f"(known: {sorted(known)})"
            )


def _bot_module_for_version(version: str) -> str:
    """Return the ``python -m <module>`` string for a bot version.

    For ``v0`` this is ``bots.v0``; for ``v1`` it would be ``bots.v1``, etc.
    """
    return f"bots.{version}"


def _build_bot_process(
    version: str,
    role: str,
    map_name: str,
    result_out: Path,
) -> BotProcess:
    """Construct a burnysc2 ``BotProcess`` for a subprocess bot.

    Lazy-imports ``sc2`` so unit tests that mock the SC2 layer don't need
    the full dependency chain at import time.
    """
    from sc2.data import Race
    from sc2.player import BotProcess as _BotProcess

    repo = _repo_root()
    module = _bot_module_for_version(version)

    return _BotProcess(
        path=str(repo),
        launch_list=[
            sys.executable,
            "-m",
            module,
            "--role",
            role,
            "--map",
            map_name,
            "--result-out",
            str(result_out),
        ],
        race=Race.Protoss,
        name=f"{version}-{role}",
        stdout=str(repo / "logs" / f"selfplay_{version}_{role}.log"),
    )


def _build_match(
    p1_version: str,
    p2_version: str,
    map_name: str,
    game_index: int,
    result_dir: Path,
    game_time_limit: int,
    seed: int | None,
) -> tuple[GameMatch, bool]:
    """Build a ``GameMatch`` for one self-play game.

    Returns ``(match, seat_swap)`` where *seat_swap* is ``True`` when the
    CLI-order players have been swapped (odd-indexed games).
    """
    from sc2 import maps
    from sc2.main import GameMatch as _GameMatch

    seat_swap = game_index % 2 == 1

    if seat_swap:
        p1_ver, p2_ver = p2_version, p1_version
    else:
        p1_ver, p2_ver = p1_version, p2_version

    p1_bot = _build_bot_process(
        p1_ver, "p1", map_name, result_dir / f"game{game_index}_p1.json"
    )
    p2_bot = _build_bot_process(
        p2_ver, "p2", map_name, result_dir / f"game{game_index}_p2.json"
    )

    match = _GameMatch(
        map_sc2=maps.get(map_name),
        players=[p1_bot, p2_bot],
        realtime=False,
        random_seed=seed,
        game_time_limit=game_time_limit,
    )
    return match, seat_swap


async def _run_single_game(
    match: GameMatch,
    hard_timeout: float,
) -> dict[AbstractPlayer, object] | None:
    """Run one game with a hard wall-clock timeout.

    Returns the raw burnysc2 result dict, or ``None`` on timeout/crash.
    Callers handle cleanup.
    """
    from sc2.main import a_run_multiple_games

    results = await asyncio.wait_for(
        a_run_multiple_games([match]),
        timeout=hard_timeout,
    )
    return results[0] if results else None  # type: ignore[return-value]


def _parse_winner(
    raw_result: dict[AbstractPlayer, object] | None,
    p1_version: str,
    p2_version: str,
    seat_swap: bool,
) -> str | None:
    """Extract the winner version string from burnysc2's result dict.

    Returns the winning version string, or ``None`` for draw / unknown.
    """
    if not raw_result:
        return None

    from sc2.data import Result

    for player, result in raw_result.items():
        if result == Result.Victory:
            name: str = getattr(player, "name", "")
            # Name format is "{version}-{role}", e.g. "v0-p1"
            if name.endswith("-p1"):
                return p2_version if seat_swap else p1_version
            if name.endswith("-p2"):
                return p1_version if seat_swap else p2_version
    return None


def _append_result(record: SelfPlayRecord, results_path: Path) -> None:
    """Append one ``SelfPlayRecord`` as a JSON line to the results file."""
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "a", encoding="utf-8") as f:
        f.write(record.to_json() + "\n")


def run_batch(
    p1: str,
    p2: str,
    games: int,
    map_name: str = "Simple64",
    *,
    game_time_limit: int = 300,
    hard_timeout: float = 600.0,
    seed: int | None = None,
    results_path: Path | None = None,
) -> list[SelfPlayRecord]:
    """Run *games* self-play matches between two bot versions.

    Each game spawns two subprocesses (one per bot) and two SC2 instances
    via burnysc2's ``BotProcess`` + ``a_run_multiple_games``.  Games run
    one-at-a-time within the batch (see design 5.2 in the build plan).

    Seats alternate: even-indexed games keep CLI order, odd-indexed games
    swap P1/P2 to neutralize spawn-side bias.

    Parameters
    ----------
    p1, p2:
        Version strings (e.g. ``"v0"``).  Must exist in the registry.
    games:
        Number of games to play.
    map_name:
        SC2 map name.
    game_time_limit:
        In-game time limit in seconds (passed to burnysc2).
    hard_timeout:
        Wall-clock timeout per game in seconds.  If exceeded, the game
        is recorded as a crash and the batch continues.
    seed:
        Optional RNG seed for SC2 (same seed for all games in the batch
        so each game is reproducible at a given index).
    results_path:
        Path to the JSONL results file.  Defaults to
        ``<repo_root>/data/selfplay_results.jsonl``.

    Returns
    -------
    list[SelfPlayRecord]
        One record per game played.
    """
    os.environ.setdefault("SC2PATH", r"C:\Program Files (x86)\StarCraft II")
    _install_port_collision_patch()
    _validate_versions(p1, p2)

    if results_path is None:
        results_path = _repo_root() / "data" / "selfplay_results.jsonl"

    result_dir = _repo_root() / "logs" / "selfplay_tmp"
    result_dir.mkdir(parents=True, exist_ok=True)

    records: list[SelfPlayRecord] = []

    for i in range(games):
        match_id = str(uuid.uuid4())
        match, seat_swap = _build_match(
            p1, p2, map_name, i, result_dir, game_time_limit, seed
        )

        # Resolve the actual p1/p2 versions for this game after swap.
        game_p1 = p2 if seat_swap else p1
        game_p2 = p1 if seat_swap else p2

        _log.info(
            "game %d/%d: %s (p1) vs %s (p2) seat_swap=%s",
            i + 1,
            games,
            game_p1,
            game_p2,
            seat_swap,
        )

        t0 = time.monotonic()
        error: str | None = None
        winner: str | None = None

        try:
            raw = asyncio.run(_run_single_game(match, hard_timeout))
            winner = _parse_winner(raw, p1, p2, seat_swap)
        except TimeoutError:
            error = f"timeout after {hard_timeout}s"
            _log.warning("game %d timed out after %.0fs", i + 1, hard_timeout)
            _kill_sc2()
        except Exception as exc:
            error = str(exc)
            _log.warning("game %d crashed: %s", i + 1, exc)
            _kill_sc2()

        elapsed = time.monotonic() - t0

        record = SelfPlayRecord(
            match_id=match_id,
            p1_version=game_p1,
            p2_version=game_p2,
            winner=winner,
            map_name=map_name,
            duration_s=round(elapsed, 2),
            seat_swap=seat_swap,
            timestamp=datetime.now(UTC).isoformat(),
            error=error,
        )
        records.append(record)
        _append_result(record, results_path)

        _log.info(
            "game %d result: winner=%s duration=%.1fs error=%s",
            i + 1,
            winner,
            elapsed,
            error,
        )

    # Clear blocked ports between batches so future runs start fresh.
    _BLOCKED_PORTS.clear()
    return records


def _kill_sc2() -> None:
    """Call burnysc2's KillSwitch to clean up orphaned SC2 processes.

    Only kills SC2 instances spawned by burnysc2 (tracked internally),
    NOT the user's main-menu SC2 client.
    """
    try:
        from sc2.main import KillSwitch  # type: ignore[attr-defined]

        KillSwitch.kill_all()
    except Exception:
        _log.warning("KillSwitch.kill_all() failed", exc_info=True)
