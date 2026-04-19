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
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.contracts import SelfPlayRecord
from orchestrator.registry import _repo_root, list_versions

if TYPE_CHECKING:
    from sc2.main import GameMatch
    from sc2.player import AbstractPlayer, BotProcess

#: Type alias for the ``on_game_start`` callback. Takes
#: ``(game_index, total, p1_pid, p2_pid, p1_label, p2_label)`` — labels
#: are resolved AFTER seat swap so they match the versions the user
#: sees. ``-1`` for a PID means discovery timed out OR the game crashed
#: before PIDs appeared; the viewer paints a placeholder in that slot.
#:
#: .. warning::
#:
#:     The (pid, label) positional correspondence is **not authoritative**.
#:     We cannot reliably determine whether a freshly-spawned SC2 process
#:     belongs to p1 or p2 without cooperation from burnysc2 (the bot
#:     subprocess PID is a local in ``sc2.proxy.play_with_proxy`` and not
#:     exposed on ``BotProcess``). The viewer treats the pair as an
#:     unordered set — slot-0 / slot-1 assignment is arbitrary and both
#:     slots show the same "P1 vs P2" label context.
OnGameStart = Callable[[int, int, int, int, str, str], None]

#: Type alias for the ``on_game_end`` callback. Receives the finalised
#: :class:`SelfPlayRecord` for the just-completed game.
OnGameEnd = Callable[[SelfPlayRecord], None]

_log = logging.getLogger(__name__)

__all__ = [
    "OnGameEnd",
    "OnGameStart",
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
# Worker-thread signal-handler monkey-patch
# ---------------------------------------------------------------------------

_SIGNAL_PATCH_INSTALLED = False


def _install_worker_thread_signal_patch() -> None:
    """Idempotent patch: no-op ``signal.signal`` from worker threads.

    ``sc2.sc2process.SC2Process.__aenter__`` unconditionally calls
    ``signal.signal(SIGINT, ...)``. Python's ``signal.signal`` only works
    in the main thread; off-main-thread callers get
    ``ValueError: signal only works in main thread of the main interpreter``
    synchronously. ``SelfPlayViewer.run_with_batch`` puts ``run_batch`` on
    a ``selfplay-batch`` worker so pygame can own the main thread, so
    every game fails in ~10ms before SC2 ever spawns.

    The fix swaps the ``signal`` module reference inside
    ``sc2.sc2process`` for a minimal proxy that exposes ``SIGINT`` /
    ``SIG_DFL`` unchanged and routes ``signal(...)`` calls through the
    real ``signal.signal`` only when invoked from the main thread. Ctrl+C
    cleanup via burnysc2's KillSwitch is preserved for main-thread
    callers (tests, ``--no-viewer`` CLI); viewer-owned batches rely on
    ``Esc`` + ``stop_event`` instead.
    """
    global _SIGNAL_PATCH_INSTALLED  # noqa: PLW0603
    if _SIGNAL_PATCH_INSTALLED:
        return

    import signal as _signal_module
    import types

    import sc2.sc2process as _sc2p

    real_signal = _signal_module.signal

    def _thread_safe_signal(
        signalnum: int, handler: Callable[..., object] | int | None
    ) -> Callable[..., object] | int | None:
        if threading.current_thread() is threading.main_thread():
            return real_signal(signalnum, handler)  # type: ignore[arg-type,return-value,unused-ignore]
        return None

    proxy = types.SimpleNamespace(
        SIGINT=_signal_module.SIGINT,
        SIG_DFL=_signal_module.SIG_DFL,
        signal=_thread_safe_signal,
    )
    _sc2p.signal = proxy  # type: ignore[assignment,attr-defined]
    _SIGNAL_PATCH_INSTALLED = True
    _log.info("worker-thread signal patch installed")


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


def _sc2_pid_snapshot() -> set[int]:
    """Return the current set of SC2_x64.exe PIDs visible to this process.

    ``psutil`` is imported lazily so non-viewer callers (PFSP on Linux,
    unit tests that mock the SC2 layer) do not need the dep at import
    time. ``psutil`` is declared on the ``[viewer]`` optional extra.
    """
    import psutil  # type: ignore[import-untyped]

    pids: set[int] = set()
    for proc in psutil.process_iter(["name"]):
        try:
            name = proc.info.get("name") or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name and "SC2_x64" in name:
            pids.add(proc.pid)
    return pids


def _orchestrator_descendant_pids() -> set[int] | None:
    """Return PIDs descended from the current (orchestrator) process.

    Returns ``None`` on permission / import / platform failures so the
    caller can fall back to a looser filter. Logs a WARNING on failure
    so the operator has a breadcrumb if mid-batch slot-stealing happens.
    """
    try:
        import os as _os

        import psutil

        me = psutil.Process(_os.getpid())
        # recursive=True walks the full subtree (direct + indirect).
        return {child.pid for child in me.children(recursive=True)}
    except Exception:
        _log.warning(
            "psutil descendant walk failed; SC2 PID filter will not "
            "exclude user-spawned SC2 instances",
            exc_info=True,
        )
        return None


async def _wait_for_sc2_pids(
    before: set[int],
    timeout_s: float = 15.0,
    poll_interval_s: float = 0.2,
) -> tuple[int, int]:
    """Poll psutil until two new SC2_x64.exe PIDs appear.

    Returns the pair of new PIDs (lowest PID first for determinism), or
    ``(-1, -1)`` if fewer than two new SC2 processes appear within
    *timeout_s* seconds. The return order is stable but has **no inherent
    p1/p2 meaning** — the caller treats the pair as an unordered set and
    lets the viewer decide which slot to paint first. See the
    :data:`OnGameStart` warning for why.

    The "new" set is filtered to orchestrator-descendant PIDs when
    possible so a user spawning a third SC2 mid-batch does not displace
    one of our two. On descendant-walk failure (permission error, etc.)
    we fall back to sorting all new PIDs and picking the lowest two.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            current = _sc2_pid_snapshot()
        except Exception:
            _log.warning("psutil SC2 PID snapshot failed", exc_info=True)
            return (-1, -1)
        new_all = current - before
        descendants = _orchestrator_descendant_pids()
        if descendants is not None:
            ours = sorted(new_all & descendants)
        else:
            # Best-effort fallback: no way to filter out user-owned SC2
            # instances, so pick the lowest two (stable, deterministic).
            ours = sorted(new_all)
        if len(ours) >= 2:
            return (ours[0], ours[1])
        await asyncio.sleep(poll_interval_s)
    return (-1, -1)


async def _run_single_game_with_callbacks(
    match: GameMatch,
    hard_timeout: float,
    game_index: int,
    total: int,
    p1_label: str,
    p2_label: str,
    on_game_start: OnGameStart | None,
) -> dict[AbstractPlayer, object] | None:
    """Run one game while concurrently discovering its SC2 PIDs.

    Behaves identically to :func:`_run_single_game` when
    *on_game_start* is ``None``. When a callback is supplied, snapshots
    SC2 PIDs BEFORE spawning the game, spawns the game as a task, and
    concurrently polls psutil for the two new SC2_x64.exe PIDs. Races
    the PID poll against the game task: whichever completes first wins.
    If the game crashes before PIDs appear the callback still fires
    (with sentinel ``(-1, -1)`` PIDs) so downstream contracts stay
    intact (every game produces one ``on_game_start`` and one
    ``on_game_end``). Exceptions from *on_game_start* are caught and
    logged at WARNING — operator-side viewer bugs must not abort a
    running batch.

    Parameters
    ----------
    match:
        Pre-built burnysc2 ``GameMatch`` for the game.
    hard_timeout:
        Wall-clock timeout (seconds) for the underlying SC2 game.
    game_index, total:
        1-based position of this game in the batch and batch size.
    p1_label, p2_label:
        Human-readable post-swap version labels for each seat.
    on_game_start:
        Callback forwarded from :func:`run_batch`. ``None`` short-circuits
        to the pid-less :func:`_run_single_game` fast path.

    Returns
    -------
    dict[AbstractPlayer, object] | None
        Raw burnysc2 result dict for the game, or ``None`` if the SC2
        layer returned no result.
    """
    from sc2.main import a_run_multiple_games

    if on_game_start is None:
        return await _run_single_game(match, hard_timeout)

    try:
        before = _sc2_pid_snapshot()
    except Exception:
        _log.warning(
            "psutil pre-spawn snapshot failed; on_game_start will fire "
            "with (-1, -1)",
            exc_info=True,
        )
        before = set()

    game_task = asyncio.create_task(
        asyncio.wait_for(a_run_multiple_games([match]), timeout=hard_timeout)
    )
    pid_task = asyncio.create_task(_wait_for_sc2_pids(before))

    # Race: if the game finishes (normally or via exception) before PIDs
    # appear, cancel the pid_task and fire on_game_start with sentinel
    # PIDs. Without this a fast crash wastes the full PID-poll timeout.
    done, _pending = await asyncio.wait(
        {pid_task, game_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if game_task in done:
        # Game completed (normally or with exception) before PID
        # discovery finished. Cancel the pid_task so we don't leak it.
        pid_task.cancel()
        try:
            await pid_task
        except (asyncio.CancelledError, Exception):
            # CancelledError is expected; any other exception is noise
            # we don't want to surface over the real game result/error.
            pass
        p1_pid, p2_pid = -1, -1
        _log.warning(
            "game %d/%d completed before SC2 PIDs were discovered; "
            "firing on_game_start with (-1, -1)",
            game_index,
            total,
        )
    else:
        # pid_task completed first — it returned either a real pair or
        # (-1, -1) on its own timeout. game_task is still in flight.
        try:
            p1_pid, p2_pid = pid_task.result()
        except Exception:
            _log.warning("SC2 PID discovery crashed", exc_info=True)
            p1_pid, p2_pid = -1, -1
        if p1_pid == -1 or p2_pid == -1:
            _log.warning(
                "SC2 PID discovery timed out for game %d/%d; firing "
                "on_game_start with (-1, -1)",
                game_index,
                total,
            )

    try:
        on_game_start(game_index, total, p1_pid, p2_pid, p1_label, p2_label)
    except Exception:
        _log.warning(
            "on_game_start callback raised (game %d/%d); continuing batch",
            game_index,
            total,
            exc_info=True,
        )

    results = await game_task
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
    on_game_start: OnGameStart | None = None,
    on_game_end: OnGameEnd | None = None,
    stop_event: threading.Event | None = None,
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
    on_game_start:
        Optional callback fired AFTER SC2 spawns for each game with the
        signature ``(game_index, total, p1_pid, p2_pid, p1_label,
        p2_label)``. ``game_index`` is 1-based. Labels / PIDs are
        post-swap so they match the SC2 windows the user sees. ``-1``
        PIDs indicate discovery timeout — the viewer is expected to
        paint a placeholder in that slot. Exceptions raised by the
        callback are caught and logged at WARNING so operator-side
        viewer bugs cannot abort a running batch.
    on_game_end:
        Optional callback fired once per game with the finalised
        :class:`SelfPlayRecord`. Same exception-isolation contract as
        *on_game_start*.
    stop_event:
        Optional :class:`threading.Event`. When set, the batch breaks
        out of the game loop at the next inter-game boundary (the
        currently-running game is allowed to finish). Used by the
        viewer to tear down cooperatively when the user closes the
        window mid-batch. ``None`` disables the check entirely.

    Returns
    -------
    list[SelfPlayRecord]
        One record per game played (up to *games*; fewer if
        *stop_event* interrupts the batch).
    """
    os.environ.setdefault("SC2PATH", r"C:\Program Files (x86)\StarCraft II")
    _install_port_collision_patch()
    _install_worker_thread_signal_patch()
    _validate_versions(p1, p2)

    if results_path is None:
        results_path = _repo_root() / "data" / "selfplay_results.jsonl"

    result_dir = _repo_root() / "logs" / "selfplay_tmp"
    result_dir.mkdir(parents=True, exist_ok=True)

    records: list[SelfPlayRecord] = []

    for i in range(games):
        # Cooperative cancellation checkpoint — check BEFORE starting a
        # game so a viewer close that races with game N+1's launch wins
        # the race and we return whatever we've accumulated so far.
        if stop_event is not None and stop_event.is_set():
            _log.info(
                "stop_event set before game %d/%d; ending batch early "
                "with %d record(s)",
                i + 1,
                games,
                len(records),
            )
            break
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
            raw = asyncio.run(
                _run_single_game_with_callbacks(
                    match,
                    hard_timeout,
                    game_index=i + 1,
                    total=games,
                    p1_label=game_p1,
                    p2_label=game_p2,
                    on_game_start=on_game_start,
                )
            )
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

        if on_game_end is not None:
            try:
                on_game_end(record)
            except Exception:
                _log.warning(
                    "on_game_end callback raised (game %d/%d); "
                    "continuing batch",
                    i + 1,
                    games,
                    exc_info=True,
                )

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
