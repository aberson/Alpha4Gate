"""FastAPI REST endpoints for the Alpha4Gate dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from bots.v0.error_log import get_error_log_buffer, install_error_log_handler
from bots.v0.learning.daemon import DaemonConfig, TrainingDaemon
from bots.v0.web_socket import drain_broadcast_queue, drain_command_event_queue

_log = logging.getLogger(__name__)

# These are set at startup by the runner
_data_dir: Path = Path("data")
_log_dir: Path = Path("logs")
_replay_dir: Path = Path("replays")
# Cross-version orchestrator state (evolve run state/pool/results/control)
# lives at repo-root ``data/`` regardless of which bot version is current.
# ``_data_dir`` points at ``bots/<current>/data/`` for per-version state
# (training.db etc.), so evolve needs its own resolver.
_evolve_dir: Path = Path("data")

# Training daemon (created in configure(), started via endpoint)
_daemon: TrainingDaemon | None = None


def configure(
    data_dir: Path,
    log_dir: Path,
    replay_dir: Path,
    api_key: str = "",
    daemon_config: DaemonConfig | None = None,
    *,
    evolve_dir: Path | None = None,
) -> None:
    """Configure directory paths for the API.

    Called by the runner at startup. Also installs the root-logger
    ERROR-buffer handler (Phase 4.5 #68) so tests that drive the API
    via ``TestClient`` (which does not enter the FastAPI lifespan by
    default) still capture backend errors in the alerts pipeline.
    ``install_error_log_handler`` is idempotent.

    Parameters
    ----------
    data_dir:
        Per-version data dir (resolves to ``bots/<current>/data/`` in
        production via :func:`orchestrator.registry.get_data_dir`).
    evolve_dir:
        Cross-version data dir for evolve orchestrator state files.
        Defaults to ``data_dir`` if not passed (tests reuse the same
        tmp_path); the production runner passes ``_repo_root() / "data"``
        explicitly so evolve files always land at the repo root
        regardless of which bot version is current.
    """
    global _data_dir, _log_dir, _replay_dir, _daemon, _evolve_dir
    install_error_log_handler()
    _data_dir = data_dir
    _log_dir = log_dir
    _replay_dir = replay_dir
    _evolve_dir = evolve_dir if evolve_dir is not None else data_dir

    # Build a Settings-like object for the daemon from the configured paths
    from bots.v0.config import Settings

    settings = Settings(
        sc2_path=Path("."),
        log_dir=log_dir,
        replay_dir=replay_dir,
        data_dir=data_dir,
        web_ui_port=0,
        anthropic_api_key=api_key,
        spawning_tool_api_key="",
    )
    _daemon = TrainingDaemon(settings, daemon_config or DaemonConfig())


async def _drain_queues_loop() -> None:
    """Silently drain the bot-thread broadcast queues.

    ``bots/v0/bot.py`` still calls ``queue_broadcast`` and
    ``queue_command_event`` on every game tick. Step 6 of the dashboard
    refactor removed the ``/ws/*`` endpoints that consumed these queues,
    so we drain-and-discard here purely to prevent unbounded growth
    over a long-running game. No clients receive the data.
    """
    while True:
        drain_broadcast_queue()
        drain_command_event_queue()
        await asyncio.sleep(0.5)


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Start the silent queue-drain loop on startup."""
    # Attach the ERROR-level log ring buffer to the root logger so the
    # alerts pipeline can surface backend errors to the dashboard
    # (Phase 4.5 #68). Idempotent — safe if a test or a prior --serve
    # invocation already installed it.
    install_error_log_handler()
    # Auto-configure when the app is launched directly (e.g. via
    # `uvicorn bots.v0.api:app --reload` in dev mode). The normal
    # --serve entrypoint calls configure() before uvicorn.run, so this
    # guard is a no-op there.
    if _daemon is None:
        from bots.v0.config import load_settings
        from bots.v0.learning.daemon import load_daemon_config
        settings = load_settings()
        daemon_config = load_daemon_config(
            settings.data_dir / "daemon_config.json"
        )
        configure(
            settings.data_dir,
            settings.log_dir,
            settings.replay_dir,
            api_key=settings.anthropic_api_key,
            daemon_config=daemon_config,
        )
    task = asyncio.create_task(_drain_queues_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Alpha4Gate", version="0.1.0", lifespan=_lifespan)


# --- REST Endpoints ---


@app.get("/api/operator-commands")
async def get_operator_commands() -> dict[str, str]:
    """Return the contents of `documentation/wiki/operator-commands.md`.

    The Help dashboard tab renders this directly via react-markdown so the
    on-disk doc is the single source of truth — edits to the markdown file
    surface immediately in the UI without a frontend rebuild.
    """
    repo_root = Path(__file__).resolve().parents[2]
    doc_path = repo_root / "documentation" / "wiki" / "operator-commands.md"
    if not doc_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"operator-commands.md not found at {doc_path}",
        )
    return {"markdown": doc_path.read_text(encoding="utf-8")}


# --- Training Endpoints ---


@app.get("/api/training/status")
async def get_training_status() -> dict[str, Any]:
    """Get current training status."""
    from bots.v0.learning.checkpoints import get_best_name, list_checkpoints
    from bots.v0.learning.database import TrainingDB

    cp_dir = _data_dir / "checkpoints"
    db_path = _data_dir / "training.db"
    reward_logs_dir = _data_dir / "reward_logs"

    status: dict[str, Any] = {
        "training_active": False,
        "current_checkpoint": None,
        "total_checkpoints": 0,
        "total_games": 0,
        "total_transitions": 0,
        "db_size_bytes": 0,
        "reward_logs_size_bytes": _compute_reward_logs_size(reward_logs_dir),
    }

    if cp_dir.exists():
        cps = list_checkpoints(cp_dir)
        status["total_checkpoints"] = len(cps)
        status["current_checkpoint"] = get_best_name(cp_dir)

    if db_path.exists():
        db = TrainingDB(db_path)
        status["total_games"] = db.get_game_count()
        status["total_transitions"] = db.get_transition_count()
        status["db_size_bytes"] = db.get_db_size_bytes()
        db.close()

    # Phase 4.5 #68: surface the backend ERROR log ring buffer so the
    # frontend alerts pipeline can fire on actual backend failures.
    buffer = get_error_log_buffer()
    total_errors, recent_errors = buffer.snapshot()
    status["error_count_since_start"] = total_errors
    status["recent_errors"] = recent_errors

    return status


def _compute_reward_logs_size(reward_logs_dir: Path) -> int:
    """Sum the sizes of all files in the reward logs directory.

    Returns 0 if the directory does not exist. Unreadable entries are skipped.
    """
    if not reward_logs_dir.exists() or not reward_logs_dir.is_dir():
        return 0
    total = 0
    for entry in reward_logs_dir.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


@app.get("/api/training/history")
async def get_training_history() -> dict[str, Any]:
    """Get training game history with win rates."""
    from bots.v0.learning.database import TrainingDB

    db_path = _data_dir / "training.db"
    if not db_path.exists():
        return {"games": [], "win_rates": {}}

    db = TrainingDB(db_path)
    game_count = db.get_game_count()
    win_rates = {
        "last_10": db.get_recent_win_rate(10),
        "last_50": db.get_recent_win_rate(50),
        "last_100": db.get_recent_win_rate(100),
        "overall": db.get_recent_win_rate(game_count) if game_count > 0 else 0.0,
    }
    db.close()

    return {"total_games": game_count, "win_rates": win_rates}


@app.get("/api/training/daemon")
async def get_daemon_status() -> dict[str, Any]:
    """Return current training daemon status.

    Consumed by ``frontend/src/hooks/useDaemonStatus.ts`` →
    ``useAlerts.ts`` to drive the ``daemon_error`` / ``daemon_stopped``
    alert rules.
    """
    if _daemon is None:
        return {"running": False, "state": "not_configured"}
    return _daemon.get_status()


@app.get("/api/training/triggers")
async def get_training_triggers() -> dict[str, Any]:
    """Return current trigger evaluation state.

    Consumed by ``frontend/src/hooks/useDaemonStatus.ts`` →
    ``useAlerts.ts`` to drive the ``no_training`` alert rule.
    """
    if _daemon is None:
        return {
            "transitions_since_last": 0,
            "hours_since_last": 0.0,
            "would_trigger": False,
            "reason": "daemon not configured",
        }
    return _daemon.get_trigger_state()


# --- Promotion Endpoints ---

# Promotion logger (created lazily)
_promotion_logger: Any = None


def _get_promotion_logger() -> Any:
    """Get or create the PromotionLogger instance."""
    global _promotion_logger
    if _promotion_logger is None:
        from bots.v0.learning.promotion import PromotionLogger

        _promotion_logger = PromotionLogger(
            history_path=_data_dir / "promotion_history.json",
        )
    return _promotion_logger


@app.get("/api/training/promotions/history")
async def get_promotion_history() -> dict[str, Any]:
    """Get the full promotion history from the persistent JSON log."""
    logger = _get_promotion_logger()
    return {"history": logger.get_history()}


# --- Unified Improvements (advised + evolve) ---
#
# The unified endpoint feeds the single Improvements timeline tab on the
# refactored dashboard. It pulls advised-run entries from
# ``improvement_log.json`` and evolve-run entries from
# ``evolve_results.jsonl``, normalises both into a common shape, then
# merges and sorts by timestamp desc. Both source files are CROSS-VERSION
# (repo-root ``data/``) so we read from ``_evolve_dir``, which the runner
# pins to repo-root regardless of which bot version is current.

_PHASE_ORDINAL: dict[str, int] = {"fitness": 0, "stack_apply": 1, "regression": 2}


def _slugify(text: str) -> str:
    """Lowercase + dash-separate a title for a fallback id.

    Used only when the canonical evolve-row candidate identifier is
    missing — in practice the on-disk file always has ``candidate``,
    but the spec calls for a deterministic fallback.
    """
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


def _normalize_advised_metric(metrics: dict[str, Any] | None) -> str | None:
    """Build a short metric blurb from an advised-run ``metrics`` dict.

    Preference order matches the spec: validation_wins → observation_wins
    → first single int/string field → None when empty.
    """
    if not metrics:
        return None
    if "validation_wins" in metrics and "validation_total" in metrics:
        return (
            f"{metrics['validation_wins']}/{metrics['validation_total']} "
            "wins (validation)"
        )
    if "validation_wins" in metrics:
        # Fall back to a single value if total is absent.
        return f"{metrics['validation_wins']} wins (validation)"
    if "observation_wins" in metrics and "observation_total" in metrics:
        return (
            f"{metrics['observation_wins']}/{metrics['observation_total']} "
            "wins (observation)"
        )
    if "observation_wins" in metrics:
        return f"{metrics['observation_wins']} wins (observation)"
    for key, value in metrics.items():
        if isinstance(value, int | str):
            return f"{key}: {value}"
    return None


_ADVISED_OUTCOME_MAP: dict[str, str] = {
    "pass": "promoted",
    "stopped": "discarded",
    "fail": "discarded",
}


def _normalize_advised_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Map one row from ``improvement_log.json`` to the unified shape."""
    run_id = entry.get("run_id", "unknown")
    iteration = entry.get("iteration", 0)
    fallback_id = f"advised-{run_id}-iter{iteration}"
    return {
        "id": entry.get("id") or fallback_id,
        "source": "advised",
        "timestamp": entry.get("timestamp"),
        "title": entry.get("title", ""),
        "description": entry.get("description", ""),
        "type": entry.get("type", "training"),
        "outcome": _ADVISED_OUTCOME_MAP.get(
            entry.get("result", ""), entry.get("result", "")
        ),
        "metric": _normalize_advised_metric(entry.get("metrics")),
        "principles": entry.get("principles", []) or [],
        "files_changed": entry.get("files_changed", []) or [],
    }


def _candidate_identifier(candidate: Any) -> str | None:
    """Pull a stable identifier from the evolve ``candidate`` field.

    Real on-disk rows store ``candidate`` as a plain string (e.g.
    ``"cand_8346016e"``); the spec describes it as a dict with
    ``name``/``id``. Handle both shapes so the rollup works on whatever
    schema variant the file has.
    """
    if isinstance(candidate, str) and candidate:
        return candidate
    if isinstance(candidate, dict):
        for key in ("name", "id"):
            value = candidate.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _evolve_metric(row: dict[str, Any]) -> str | None:
    """Build a ``"X-Y vs <parent>"`` blurb from an evolve canonical row."""
    wins_cand = row.get("wins_cand")
    wins_parent = row.get("wins_parent")
    parent = row.get("parent", "?")
    if isinstance(wins_cand, int) and isinstance(wins_parent, int):
        return f"{wins_cand}-{wins_parent} vs {parent}"
    return None


def _normalize_evolve_rollup(
    title: str,
    generation: int,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Collapse all phase rows for one (title, generation) imp into one entry.

    Sort by phase ordinal (fitness < stack_apply < regression). When the
    same phase appears multiple times (multiple stack_apply attempts),
    the latest by timestamp wins; rows without a timestamp fall back to
    file order via the ``_file_order`` key injected by the caller.
    """
    def _row_sort_key(row: dict[str, Any]) -> tuple[int, str, int]:
        phase = row.get("phase", "")
        return (
            _PHASE_ORDINAL.get(phase, 99),
            row.get("timestamp") or "",
            row.get("_file_order", 0),
        )

    sorted_rows = sorted(rows, key=_row_sort_key)
    canonical = sorted_rows[-1]
    imp = canonical.get("imp") or {}
    candidate_id = _candidate_identifier(canonical.get("candidate"))
    if candidate_id is None:
        candidate_id = _slugify(title) or f"gen{generation}"
    entry_id = f"evolve-gen{generation}-{candidate_id}"
    return {
        "id": entry_id,
        "source": "evolve",
        "timestamp": canonical.get("timestamp"),
        "title": title,
        "description": imp.get("description", ""),
        "type": imp.get("type", "training"),
        "outcome": canonical.get("outcome", ""),
        "metric": _evolve_metric(canonical),
        "principles": imp.get("principle_ids", []) or [],
        "files_changed": imp.get("files_touched", []) or [],
    }


def _load_advised_improvements(path: Path) -> list[dict[str, Any]]:
    """Read ``improvement_log.json`` and normalise each entry. Empty on miss."""
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    entries = raw.get("improvements") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return []
    return [
        _normalize_advised_entry(entry)
        for entry in entries
        if isinstance(entry, dict)
    ]


def _load_evolve_improvements(path: Path) -> list[dict[str, Any]]:
    """Read ``evolve_results.jsonl`` + collapse phase rows per imp+gen."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        imp = row.get("imp")
        if not isinstance(imp, dict):
            continue
        title = imp.get("title")
        generation = row.get("generation")
        if not isinstance(title, str) or not isinstance(generation, int):
            continue
        # Inject file-order tiebreaker for rollup ordering.
        row["_file_order"] = index
        groups.setdefault((title, generation), []).append(row)

    return [
        _normalize_evolve_rollup(title, generation, rows)
        for (title, generation), rows in groups.items()
    ]


@app.get("/api/improvements/unified")
async def get_improvements_unified(
    source: str | None = Query(default=None, pattern="^(advised|evolve)$"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """Return a unified timeline of advised + evolve improvements.

    Pulls ``improvement_log.json`` and ``evolve_results.jsonl`` from the
    cross-version data dir, normalises both into a common entry shape,
    merges and sorts by timestamp descending, then applies the optional
    ``source`` filter and ``limit`` cap. Missing source files produce an
    empty list (no error).
    """
    advised_path = _evolve_dir / "improvement_log.json"
    evolve_path = _evolve_dir / _EVOLVE_RESULTS_FILE

    entries: list[dict[str, Any]] = []
    if source != "evolve":
        entries.extend(_load_advised_improvements(advised_path))
    if source != "advised":
        entries.extend(_load_evolve_improvements(evolve_path))

    # Sort by timestamp descending. Entries without a timestamp sort to
    # the end (treated as the empty string, which compares smallest).
    entries.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    return {"improvements": entries[:limit]}


# --- Advised Run Control Panel Endpoints ---


_ADVISED_STATE_FILE = "advised_run_state.json"
_ADVISED_CONTROL_FILE = "advised_run_control.json"


def _read_json_file(path: Path) -> dict[str, Any] | None:
    """Read a JSON file if it exists, return None otherwise."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        except (json.JSONDecodeError, OSError):
            return None
    return None


@app.get("/api/advised/state")
async def get_advised_state() -> dict[str, Any]:
    """Read the advised run state file.

    Returns the current state of an improve-bot-advised run, or
    ``{"status": "idle"}`` when no run is active / no state file exists.
    """
    data = _read_json_file(_data_dir / _ADVISED_STATE_FILE)
    if data is None:
        return {"status": "idle"}
    return data


@app.get("/api/advised/control")
async def get_advised_control() -> dict[str, Any]:
    """Read current control signals for the advised run."""
    data = _read_json_file(_data_dir / _ADVISED_CONTROL_FILE)
    if data is None:
        return {
            "games_per_cycle": None,
            "user_hint": None,
            "stop_run": False,
            "reset_loop": False,
            "difficulty": None,
            "fail_threshold": None,
            "reward_rule_add": None,
            "updated_at": None,
        }
    return data


@app.put("/api/advised/control")
async def update_advised_control(request: dict[str, Any]) -> dict[str, Any]:
    """Write control signals for the advised run.

    Merges the incoming fields with the existing control file so that
    the UI can send partial updates (e.g. just ``games_per_cycle``).

    When ``stop_run`` is true the training daemon is stopped immediately
    so games don't keep spawning while the skill waits for Phase 7.
    """
    path = _data_dir / _ADVISED_CONTROL_FILE
    existing = _read_json_file(path) or {}
    existing.update(request)
    existing["updated_at"] = datetime.now(UTC).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    # Immediately stop the daemon so no new games are spawned.
    if request.get("stop_run") and _daemon is not None and _daemon.is_running():
        _daemon.stop()
        _log.info("Daemon stopped via advised control stop_run signal")

    return existing


# --- Evolve Run Dashboard Endpoints ---
#
# Evolve state is CROSS-VERSION orchestration (not per-version bot state),
# so these files live at repo-root ``data/`` regardless of which bot
# version is current. ``_data_dir`` points at ``bots/<current>/data/``
# (per-version) for training.db / reward_logs / etc.; ``_evolve_dir``
# is set separately by ``configure(..., evolve_dir=...)`` so the runner
# can point evolve state at repo-root ``data/``.


_EVOLVE_STATE_FILE = "evolve_run_state.json"
_EVOLVE_CONTROL_FILE = "evolve_run_control.json"
_EVOLVE_POOL_FILE = "evolve_pool.json"
_EVOLVE_RESULTS_FILE = "evolve_results.jsonl"
_EVOLVE_CURRENT_ROUND_FILE = "evolve_current_round.json"

_EVOLVE_CURRENT_ROUND_IDLE: dict[str, Any] = {
    "active": False,
    "generation": None,
    "phase": None,
    "imp_title": None,
    "imp_rank": None,
    "imp_index": None,
    "candidate": None,
    "stacked_titles": [],
    "new_parent": None,
    "prior_parent": None,
    "games_played": None,
    "games_total": None,
    "score_cand": None,
    "score_parent": None,
    "updated_at": None,
}

_EVOLVE_IDLE_STATE: dict[str, Any] = {
    "status": "idle",
    "parent_start": None,
    "parent_current": None,
    "started_at": None,
    "wall_budget_hours": None,
    "generation_index": None,
    "generations_completed": None,
    "generations_promoted": None,
    "evictions": None,
    "resurrections_remaining": None,
    "pool_remaining_count": None,
    "last_result": None,
    "run_id": None,
    "concurrency": None,
    "cli_argv": None,
    "gen_durations_seconds": None,
    "generations_target": None,
}

_EVOLVE_DEFAULT_CONTROL: dict[str, Any] = {
    "stop_run": False,
    "pause_after_round": False,
}

_EVOLVE_CONTROL_KEYS: frozenset[str] = frozenset(
    {"stop_run", "pause_after_round"}
)


@app.get("/api/evolve/state")
async def get_evolve_state() -> dict[str, Any]:
    """Read the evolve run state file.

    Returns the current state of an ``improve-bot-evolve`` run, or the
    idle skeleton ``{"status": "idle", "parent_start": None, ...}`` when
    no run is active / no state file exists. The idle skeleton carries
    the same top-level keys the running state would, so the frontend
    can destructure without null-coalescing every field.
    """
    data = _read_json_file(_evolve_dir / _EVOLVE_STATE_FILE)
    if data is None:
        return dict(_EVOLVE_IDLE_STATE)
    return data


@app.get("/api/evolve/control")
async def get_evolve_control() -> dict[str, Any]:
    """Read current control signals for the evolve run."""
    data = _read_json_file(_evolve_dir / _EVOLVE_CONTROL_FILE)
    if data is None:
        return dict(_EVOLVE_DEFAULT_CONTROL)
    return data


@app.put("/api/evolve/control")
async def update_evolve_control(request: dict[str, Any]) -> dict[str, Any]:
    """Write control signals for the evolve run.

    The accepted payload shape is::

        {"stop_run": <bool>, "pause_after_round": <bool>}

    Both keys are optional; a partial payload is merged with the
    existing control file (or the default skeleton when none exists).
    Any key outside the allowed set is rejected with HTTP 400, and
    every value must be a bool. We use PUT to match the
    ``/api/advised/control`` convention even though the build plan
    mentions POST — the dashboard uses a shared mutation pattern.

    The on-disk write is atomic (temp file + rename) so concurrent
    readers never see a half-written document.
    """
    # Validate the request shape first.
    extras = set(request.keys()) - _EVOLVE_CONTROL_KEYS
    if extras:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported fields: {sorted(extras)}",
        )
    for key, value in request.items():
        if not isinstance(value, bool):
            raise HTTPException(
                status_code=400,
                detail=f"Field {key!r} must be a bool, got {type(value).__name__}",
            )

    path = _evolve_dir / _EVOLVE_CONTROL_FILE
    existing = _read_json_file(path) or dict(_EVOLVE_DEFAULT_CONTROL)
    existing.update(request)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(existing, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)

    return existing


@app.get("/api/evolve/current-round")
async def get_evolve_current_round() -> dict[str, Any]:
    """Read the live per-game progress file for the active round.

    Written by ``scripts/evolve.py`` inside each phase — updates on every
    fitness/composition/regression game-end event. Returns the idle
    skeleton ``{"active": False, ...}`` when no phase is in progress / no
    file exists. The Evolution dashboard tab polls this at ~2s cadence so
    operators see score changes between phase-boundary writes.

    Backwards-compat shim (Step 4 of the evolve-parallelization plan):
    at ``concurrency=1`` the legacy ``evolve_current_round.json`` is the
    sole source of truth (single-flight callers see no change). At
    ``concurrency>1`` the dispatcher does NOT write the legacy file, so
    we fall back to the per-worker round-state files (filtered by the
    active ``run_id``) and return the first running round shaped to the
    legacy ``EvolveCurrentRound`` interface. This keeps the Evolution
    dashboard tab usable at parallel concurrencies without forcing a
    frontend cut-over.
    """
    data = _read_json_file(_evolve_dir / _EVOLVE_CURRENT_ROUND_FILE)
    if data is not None and data.get("active"):
        # Single-flight legacy path: the file exists and is active —
        # return it (merged over the idle skeleton for shape stability).
        merged = dict(_EVOLVE_CURRENT_ROUND_IDLE)
        merged.update(data)
        return merged

    # Parallel-evolve fallback: scan per-worker round-state files
    # filtered by the current run's ``run_id``. Return the first active
    # round, padded to the legacy current-round shape.
    run_state = _read_json_file(_evolve_dir / _EVOLVE_STATE_FILE) or {}
    active_run_id = run_state.get("run_id")
    if active_run_id:
        for path in sorted(_evolve_dir.glob("evolve_round_*.json")):
            entry = _read_json_file(path)
            if entry is None:
                continue
            if entry.get("run_id") != active_run_id:
                continue
            if not entry.get("active"):
                continue
            merged = dict(_EVOLVE_CURRENT_ROUND_IDLE)
            # Only copy keys recognised by the legacy interface so the
            # response shape matches single-flight output exactly.
            for key in _EVOLVE_CURRENT_ROUND_IDLE:
                if key in entry:
                    merged[key] = entry[key]
            merged["active"] = True
            return merged

    if data is None:
        return dict(_EVOLVE_CURRENT_ROUND_IDLE)
    # File exists but is inactive — preserve historical behavior of
    # returning whatever the file contained, merged over the idle shape.
    merged = dict(_EVOLVE_CURRENT_ROUND_IDLE)
    merged.update(data)
    return merged


# Per-worker round entry shape returned by ``/api/evolve/running-rounds``.
# Strict subset of the legacy ``EvolveCurrentRound`` interface — no
# ``imp_rank``/``imp_index``/``stacked_titles``/``new_parent``/
# ``prior_parent`` (those are dispatcher-level, not per-worker).
_EVOLVE_RUNNING_ROUND_IDLE: dict[str, Any] = {
    "worker_id": None,
    "active": False,
    "phase": None,
    "imp_title": None,
    "candidate": None,
    "parent": None,
    "games_played": None,
    "games_total": None,
    "score_cand": None,
    "score_parent": None,
    "updated_at": None,
}


@app.get("/api/evolve/running-rounds")
async def get_evolve_running_rounds() -> dict[str, Any]:
    """Read all per-worker round-state files for the active parallel run.

    Returns ``{"active": False, "concurrency": None, "run_id": None,
    "rounds": []}`` when no parallel run is in progress (no run-state
    file, or ``concurrency`` is ``None``). Otherwise returns a
    fixed-length ``rounds`` array padded to ``concurrency`` length: each
    slot is either an active per-worker entry (when its
    ``evolve_round_<wid>.json`` exists with the current ``run_id``) or
    an all-null idle skeleton.

    Stale-file guard (Decision D-6 of the evolve-parallelization plan):
    we filter ``evolve_round_*.json`` by the run-state's ``run_id`` so
    leftover files from a prior run cannot pollute the response. The
    dispatcher's startup cleanup is the primary defence; this filter is
    the safety net for the race between cleanup and a still-dying prior
    worker.
    """
    run_state = _read_json_file(_evolve_dir / _EVOLVE_STATE_FILE)
    if run_state is None:
        return {
            "active": False,
            "concurrency": None,
            "run_id": None,
            "rounds": [],
        }

    concurrency = run_state.get("concurrency")
    run_id = run_state.get("run_id")
    if concurrency is None or run_id is None:
        return {
            "active": False,
            "concurrency": None,
            "run_id": None,
            "rounds": [],
        }

    # The per-worker files don't carry ``parent`` — the dispatcher's
    # ``parent_current`` (from run_state) is the canonical parent for all
    # active workers in a generation, so we project it onto each slot.
    parent_current = run_state.get("parent_current")

    # Index per-worker files by worker_id, filtered by run_id.
    by_worker: dict[int, dict[str, Any]] = {}
    for path in _evolve_dir.glob("evolve_round_*.json"):
        entry = _read_json_file(path)
        if entry is None:
            continue
        if entry.get("run_id") != run_id:
            continue
        wid = entry.get("worker_id")
        if not isinstance(wid, int):
            continue
        by_worker[wid] = entry

    rounds: list[dict[str, Any]] = []
    for wid in range(int(concurrency)):
        entry = by_worker.get(wid)
        if entry is None:
            slot = dict(_EVOLVE_RUNNING_ROUND_IDLE)
            slot["worker_id"] = wid
            rounds.append(slot)
            continue
        slot = dict(_EVOLVE_RUNNING_ROUND_IDLE)
        slot["worker_id"] = wid
        slot["active"] = bool(entry.get("active", False))
        slot["parent"] = parent_current
        for key in (
            "phase",
            "imp_title",
            "candidate",
            "games_played",
            "games_total",
            "score_cand",
            "score_parent",
            "updated_at",
        ):
            if key in entry:
                slot[key] = entry[key]
        rounds.append(slot)

    return {
        "active": True,
        "concurrency": int(concurrency),
        "run_id": run_id,
        "rounds": rounds,
    }


@app.get("/api/evolve/pool")
async def get_evolve_pool() -> dict[str, Any]:
    """Read the evolve pool file.

    Returns ``{"parent": None, "generated_at": None, "pool": []}`` when
    no pool file exists so the frontend can render an empty list
    without special-casing missing data.
    """
    data = _read_json_file(_evolve_dir / _EVOLVE_POOL_FILE)
    if data is None:
        return {"parent": None, "generated_at": None, "pool": []}
    return data


@app.get("/api/evolve/results")
async def get_evolve_results() -> dict[str, Any]:
    """Read the last 50 phase rows from ``evolve_results.jsonl``.

    Each line is one fitness/composition/regression/crash row. Truncated
    to the last 50 to keep the Round History table bounded. The response
    shape is ``{"rounds": [...]}`` (the key is kept as ``rounds`` for
    frontend back-compat; entries are phase rows, not AB-round results).
    Malformed lines are skipped silently so a half-written tail line
    doesn't kill the whole endpoint.
    """
    path = _evolve_dir / _EVOLVE_RESULTS_FILE
    if not path.exists():
        return {"rounds": []}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"rounds": []}
    rounds: list[dict[str, Any]] = []
    for line in lines[-50:]:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rounds.append(json.loads(stripped))
        except json.JSONDecodeError:
            continue
    return {"rounds": rounds}


@app.get("/api/processes")
async def get_processes() -> dict[str, Any]:
    """Get full system process/state status."""
    from bots.v0.process_registry import full_status

    return full_status()


@app.post("/api/cleanup/stop-run")
async def cleanup_stop_run() -> dict[str, Any]:
    """Execute the Stop Run cleanup checklist."""
    results: list[str] = []

    # 1. Stop training daemon
    if _daemon is not None and _daemon.is_running():
        _daemon.stop()
        results.append("Training daemon stopped")
    else:
        results.append("Training daemon was not running")

    # 2. Set advised_run_state.json to stopped
    state_path = _data_dir / "advised_run_state.json"
    if state_path.exists():
        data = json.loads(state_path.read_text())
        data["status"] = "stopped"
        data["phase_name"] = "Stopped (cleanup)"
        state_path.write_text(json.dumps(data, indent=2))
        results.append("advised_run_state.json set to stopped")

    # 3. Clear decision_audit.json
    audit_path = _data_dir / "decision_audit.json"
    if audit_path.exists():
        audit_path.write_text(json.dumps({"entries": []}, indent=2))
        results.append("decision_audit.json cleared")

    # 4. Clear control file signals
    ctrl_path = _data_dir / "advised_run_control.json"
    if ctrl_path.exists():
        ctrl_path.write_text(json.dumps({
            "games_per_cycle": None,
            "user_hint": None,
            "stop_run": False,
            "reset_loop": False,
            "difficulty": None,
            "fail_threshold": None,
            "reward_rule_add": None,
            "updated_at": None,
        }, indent=2))
        results.append("advised_run_control.json reset")

    # 5. Delete stale lock files
    lock_path = Path(".claude/scheduled_tasks.lock")
    if lock_path.exists():
        lock_path.unlink(missing_ok=True)
        results.append("Deleted scheduled_tasks.lock")

    # 6. Kill game runners, advisor, and orphan monitor processes.
    #    Do NOT kill the backend (this process) or frontend (node/vite).
    results.extend(_kill_spawned_processes())

    results.append("Stop Run cleanup complete (backend + frontend still alive)")
    return {"results": results}


@app.post("/api/cleanup/reset-loop")
async def cleanup_reset_loop() -> dict[str, Any]:
    """Execute the Reset Loop cleanup checklist.

    Handles process/state cleanup. Git revert + DB purge must be done
    by the caller (the improve-bot-advised skill) since they require
    git operations outside the API process.
    """
    results: list[str] = []

    # 1. Stop training daemon
    if _daemon is not None and _daemon.is_running():
        _daemon.stop()
        results.append("Training daemon stopped")

    # 2. Reset advised_run_state.json
    state_path = _data_dir / "advised_run_state.json"
    if state_path.exists():
        data = json.loads(state_path.read_text())
        data["status"] = "resetting"
        data["phase_name"] = "Resetting to baseline"
        data["iteration"] = 0
        data["fail_streak"] = 0
        data["iterations"] = []
        data["current_improvement"] = None
        state_path.write_text(json.dumps(data, indent=2))
        results.append("advised_run_state.json reset to iteration 0")

    # 3. Clear decision_audit.json
    audit_path = _data_dir / "decision_audit.json"
    if audit_path.exists():
        audit_path.write_text(json.dumps({"entries": []}, indent=2))
        results.append("decision_audit.json cleared")

    # 4. Clear control file (except reset_loop flag for skill to read)
    ctrl_path = _data_dir / "advised_run_control.json"
    if ctrl_path.exists():
        ctrl_path.write_text(json.dumps({
            "games_per_cycle": None,
            "user_hint": None,
            "stop_run": False,
            "reset_loop": True,
            "difficulty": None,
            "fail_threshold": None,
            "reward_rule_add": None,
            "updated_at": None,
        }, indent=2))
        results.append("advised_run_control.json reset (reset_loop=true)")

    # 5. Delete temp files
    for pattern, label in [
        (_data_dir.parent / "logs", "game logs"),
        (_data_dir / "reward_logs", "reward logs"),
    ]:
        if pattern.is_dir():
            count = sum(1 for f in pattern.iterdir() if f.suffix == ".jsonl")
            for f in pattern.iterdir():
                if f.suffix == ".jsonl":
                    f.unlink()
            results.append(f"Deleted {count} {label}")

    # 6. Delete stale lock files
    lock_path = Path(".claude/scheduled_tasks.lock")
    if lock_path.exists():
        lock_path.unlink(missing_ok=True)
        results.append("Deleted scheduled_tasks.lock")

    # 7. Kill game runners, advisor, and orphan processes.
    #    Do NOT kill the backend (this process) or frontend (node/vite).
    results.extend(_kill_spawned_processes())

    results.append(
        "Reset cleanup complete. Caller must: "
        "git reset --hard to baseline, restore config backups, "
        "purge training.db entries, then restart."
    )
    return {"results": results}


def _kill_spawned_processes() -> list[str]:
    """Kill game runners, advisor subprocesses, and orphan monitors.

    Preserves: backend (this process), frontend (node/vite), SC2_x64.
    """
    import subprocess as _sp

    results: list[str] = []
    my_pid = os.getpid()

    # Find and kill python/uv processes that are NOT this backend.
    # We identify game runners by checking if they're one of "our" processes
    # (bots.v0 / bots.current / legacy alpha4gate — see
    # bots.v0.process_registry._OUR_CMDLINE_TAGS) that aren't our own PID
    # or our parent (uv wrapper).
    try:
        ps_cmd = (
            "Get-Process -Name python,uv -ErrorAction SilentlyContinue | "
            "Select-Object Id,ProcessName | ConvertTo-Json -Compress"
        )
        out = _sp.run(
            ["powershell.exe", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10,
        )
        if out.stdout.strip():
            entries = json.loads(out.stdout.strip())
            if isinstance(entries, dict):
                entries = [entries]
            killed = 0
            for entry in entries:
                pid = entry.get("Id")
                if pid and pid != my_pid and pid != os.getppid():
                    _sp.run(
                        ["powershell.exe", "-Command",
                         f"Stop-Process -Id {pid} -Force "
                         "-ErrorAction SilentlyContinue"],
                        timeout=5, capture_output=True,
                    )
                    killed += 1
            if killed:
                results.append(f"Killed {killed} python/uv process(es)")
            else:
                results.append("No extra python/uv processes to kill")
    except (OSError, _sp.TimeoutExpired, json.JSONDecodeError):
        results.append("Could not scan python/uv processes")

    # Kill orphan monitor processes (tail/grep/sleep)
    try:
        _sp.run(
            ["powershell.exe", "-Command",
             "Get-Process -Name tail,grep,sleep "
             "-ErrorAction SilentlyContinue | Stop-Process -Force"],
            timeout=5, capture_output=True,
        )
        results.append("Killed orphan monitor processes")
    except (OSError, _sp.TimeoutExpired):
        pass

    return results


@app.post("/api/kill-daemons")
async def kill_daemon_processes() -> dict[str, Any]:
    """Kill daemon backend processes (those started with --daemon).

    Preserves: the --serve-only backend (this process), frontend, SC2.
    Targets: python/uv processes whose command line contains ``--daemon``.
    """
    import subprocess as _sp

    results: list[str] = []
    my_pid = os.getpid()

    # Also stop the in-process daemon thread if running
    if _daemon is not None and _daemon.is_running():
        _daemon.stop()
        results.append("In-process training daemon stopped")

    # Find and kill python/uv processes with --daemon in their cmdline
    try:
        ps_cmd = (
            "Get-Process -Name python,uv -ErrorAction SilentlyContinue | "
            "ForEach-Object { $cim = Get-CimInstance Win32_Process "
            "-Filter \"ProcessId=$($_.Id)\" -ErrorAction SilentlyContinue; "
            "[PSCustomObject]@{ Id=$_.Id; "
            "CommandLine=if($cim){$cim.CommandLine}else{$null} "
            "} } | ConvertTo-Json -Compress"
        )
        out = _sp.run(
            ["powershell.exe", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10,
        )
        if out.stdout.strip():
            entries = json.loads(out.stdout.strip())
            if isinstance(entries, dict):
                entries = [entries]
            killed = 0
            for entry in entries:
                pid = entry.get("Id")
                cmdline = entry.get("CommandLine") or ""
                if (
                    pid
                    and pid != my_pid
                    and pid != os.getppid()
                    and "--daemon" in cmdline
                ):
                    _sp.run(
                        ["powershell.exe", "-Command",
                         f"Stop-Process -Id {pid} -Force "
                         "-ErrorAction SilentlyContinue"],
                        timeout=5, capture_output=True,
                    )
                    killed += 1
            if killed:
                results.append(f"Killed {killed} daemon process(es)")
            else:
                results.append("No daemon processes found")
    except (OSError, _sp.TimeoutExpired, json.JSONDecodeError):
        results.append("Could not scan for daemon processes")

    return {"results": results}


@app.post("/api/restart")
async def restart_server() -> dict[str, str]:
    """Restart the backend: spawn a new process, then shut down this one.

    The new process inherits the same flags (--serve --daemon if daemon
    was active). The old process exits after the response is sent.
    """
    import subprocess as _sp
    import sys

    # Build the command to start the new backend.
    # Never inherit --daemon — the improvement loop skill should be the
    # one that starts the daemon, not a UI restart button.
    cmd = [sys.executable, "-m", "bots.v0.runner", "--serve"]
    if _daemon is not None and _daemon.is_running():
        _daemon.stop()
        _log.info("Daemon stopped for restart (will NOT restart with --daemon)")

    # Spawn detached process (survives parent exit)
    _CREATE_NEW_PROCESS_GROUP = 0x00000200
    _DETACHED_PROCESS = 0x00000008
    creation_flags = (
        _CREATE_NEW_PROCESS_GROUP | _DETACHED_PROCESS
        if sys.platform == "win32"
        else 0
    )
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if "DEBUG_ENDPOINTS" in os.environ:
        env["DEBUG_ENDPOINTS"] = "1"
    _sp.Popen(cmd, creationflags=creation_flags, env=env,
              close_fds=True, start_new_session=True)
    _log.info("Spawned new backend process: %s", " ".join(cmd))

    # Schedule exit of current process
    loop = asyncio.get_running_loop()
    loop.call_later(1.0, _exit_server)
    return {"status": "restarting"}


def _exit_server() -> None:
    """Raise SystemExit so uvicorn shuts down cleanly."""
    import signal as _signal
    import sys

    _log.info("Server shutting down via /api/restart")
    # On Windows, SIGBREAK is the closest to a graceful shutdown signal
    # that uvicorn handles.  Fall back to sys.exit if unavailable.
    if sys.platform == "win32":
        _signal.raise_signal(_signal.SIGBREAK)
    else:
        _signal.raise_signal(_signal.SIGINT)
