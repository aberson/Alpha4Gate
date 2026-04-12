"""FastAPI REST endpoints for the Alpha4Gate dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from alpha4gate.commands import (
    CommandAction,
    CommandInterpreter,
    CommandMode,
    CommandPrimitive,
    CommandSource,
    StructuredParser,
    get_command_queue,
    get_command_settings,
)
from alpha4gate.error_log import get_error_log_buffer, install_error_log_handler
from alpha4gate.learning.daemon import DaemonConfig, TrainingDaemon
from alpha4gate.web_socket import (
    ConnectionManager,
    drain_broadcast_queue,
    drain_command_event_queue,
)

_log = logging.getLogger(__name__)

ws_manager = ConnectionManager()

# These are set at startup by the runner
_data_dir: Path = Path("data")
_log_dir: Path = Path("logs")
_replay_dir: Path = Path("replays")

# Command system state
_command_history: list[dict[str, Any]] = []
_interpreter: CommandInterpreter | None = None
_parser = StructuredParser()

# Training daemon (created in configure(), started via endpoint)
_daemon: TrainingDaemon | None = None


def configure(
    data_dir: Path,
    log_dir: Path,
    replay_dir: Path,
    api_key: str = "",
    daemon_config: DaemonConfig | None = None,
) -> None:
    """Configure directory paths for the API.

    Called by the runner at startup. Also installs the root-logger
    ERROR-buffer handler (Phase 4.5 #68) so tests that drive the API
    via ``TestClient`` (which does not enter the FastAPI lifespan by
    default) still capture backend errors in the alerts pipeline.
    ``install_error_log_handler`` is idempotent.
    """
    global _data_dir, _log_dir, _replay_dir, _interpreter, _daemon
    install_error_log_handler()
    _data_dir = data_dir
    _log_dir = log_dir
    _replay_dir = replay_dir
    if api_key:
        _interpreter = CommandInterpreter(api_key)

    # Build a Settings-like object for the daemon from the configured paths
    from alpha4gate.config import Settings

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


async def _drain_and_broadcast_once() -> int:
    """Drain the broadcast queue and push entries to WebSocket clients.

    Returns the number of entries broadcast.
    """
    entries = drain_broadcast_queue()
    for entry in entries:
        await ws_manager.broadcast_game_state(entry)

    # Drain command execution results from the bot thread
    cmd_events = drain_command_event_queue()
    for event in cmd_events:
        await _broadcast_command_event(event)
        # Update in-memory history with execution result
        cmd_id = event.get("id")
        if cmd_id:
            for hist in _command_history:
                if hist["id"] == cmd_id:
                    hist["status"] = event["type"]
                    break

    return len(entries) + len(cmd_events)


async def _game_state_broadcast_loop() -> None:
    """Drain the thread-safe broadcast queue and push to WebSocket clients."""
    while True:
        await _drain_and_broadcast_once()
        await asyncio.sleep(0.5)


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Start the game-state broadcast loop on startup."""
    # Attach the ERROR-level log ring buffer to the root logger so the
    # alerts pipeline can surface backend errors to the dashboard
    # (Phase 4.5 #68). Idempotent — safe if a test or a prior --serve
    # invocation already installed it.
    install_error_log_handler()
    task = asyncio.create_task(_game_state_broadcast_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Alpha4Gate", version="0.1.0", lifespan=_lifespan)


# --- Command Helpers ---


def _primitive_to_dict(p: CommandPrimitive) -> dict[str, Any]:
    """Convert a CommandPrimitive to a JSON-serialisable dict."""
    return {
        "action": p.action.value,
        "target": p.target,
        "location": p.location,
        "priority": p.priority,
        "source": p.source.value,
    }


async def _broadcast_command_event(event: dict[str, Any]) -> None:
    """Broadcast an event to all command WebSocket clients."""
    await ws_manager.broadcast_command_event(event)


def _add_to_history(
    cmd_id: str,
    text: str,
    primitives: list[CommandPrimitive] | None,
    status: str = "queued",
) -> None:
    """Append an entry to the in-memory command history."""
    _command_history.append({
        "id": cmd_id,
        "text": text,
        "parsed": [_primitive_to_dict(p) for p in primitives] if primitives else None,
        "source": "human",
        "status": status,
        "game_time": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
    })


async def _interpret_and_queue(cmd_id: str, text: str) -> None:
    """Background task: parse free text via structured parser, then Claude Haiku fallback."""
    # Fast path: try the regex-based structured parser first
    result = _parser.parse(text, CommandSource.HUMAN)

    # Slow path: fall back to Claude Haiku interpreter for complex natural language
    if result is None and _interpreter is not None:
        result = await _interpreter.interpret(text, CommandSource.HUMAN)
    if result:
        queue = get_command_queue()
        for p in result:
            p.id = cmd_id  # correlate with original request
            p.ttl = float("inf")  # no game clock in API context
            queue.push(p)
        await _broadcast_command_event({
            "type": "queued",
            "id": cmd_id,
            "parsed": [_primitive_to_dict(p) for p in result],
            "source": "human",
        })
        _add_to_history(cmd_id, text, result, status="queued")
    else:
        await _broadcast_command_event({
            "type": "rejected",
            "id": cmd_id,
            "reason": "could not parse input",
        })
        _add_to_history(cmd_id, text, None, status="rejected")


# --- REST Endpoints ---


@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    """Get current game status."""
    return {
        "state": "idle",
        "game_step": None,
        "game_time_seconds": None,
        "minerals": None,
        "vespene": None,
        "supply_used": None,
        "supply_cap": None,
        "strategic_state": None,
    }


@app.get("/api/stats")
async def get_stats() -> dict[str, Any]:
    """Get game statistics."""
    stats_path = _data_dir / "stats.json"
    if stats_path.exists():
        return json.loads(stats_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    return {"games": [], "aggregates": {"total_wins": 0, "total_losses": 0}}


@app.get("/api/build-orders")
async def get_build_orders() -> dict[str, Any]:
    """Get all build orders."""
    path = _data_dir / "build_orders.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    return {"orders": []}


@app.post("/api/build-orders")
async def create_build_order(order: dict[str, Any]) -> dict[str, Any]:
    """Create a new build order."""
    from alpha4gate.build_orders import (
        BuildOrder,
        load_build_orders,
        save_build_orders,
        slug_from_name,
    )

    path = _data_dir / "build_orders.json"
    orders = load_build_orders(path)

    # Generate ID if not provided
    order_id = order.get("id") or slug_from_name(order.get("name", "unnamed"))
    order["id"] = order_id

    new_order = BuildOrder.from_dict(order)
    orders.append(new_order)
    save_build_orders(orders, path)

    return {"id": order_id, "created": True}


@app.delete("/api/build-orders/{order_id}")
async def delete_build_order(order_id: str) -> dict[str, Any]:
    """Delete a build order by ID."""
    from alpha4gate.build_orders import load_build_orders, save_build_orders

    path = _data_dir / "build_orders.json"
    orders = load_build_orders(path)
    original_count = len(orders)
    orders = [o for o in orders if o.id != order_id]

    if len(orders) < original_count:
        save_build_orders(orders, path)
        return {"deleted": True}
    return {"deleted": False}


@app.get("/api/replays")
async def get_replays() -> dict[str, Any]:
    """List available replays."""
    replays: list[dict[str, Any]] = []
    if _replay_dir.exists():
        for f in sorted(_replay_dir.glob("*.SC2Replay"), reverse=True):
            replay_id = f.stem.replace("game_", "")
            replays.append({
                "id": replay_id,
                "timestamp": replay_id,
                "filename": f.name,
            })
    return {"replays": replays}


@app.get("/api/replays/{replay_id}")
async def get_replay(replay_id: str) -> dict[str, Any]:
    """Get parsed replay details."""
    # Placeholder — actual parsing implemented in Step 8
    return {
        "id": replay_id,
        "timeline": [],
        "stats": {
            "minerals_collected": 0,
            "gas_collected": 0,
            "units_produced": 0,
            "units_lost": 0,
            "structures_built": 0,
        },
    }


@app.get("/api/decision-log")
async def get_decision_log() -> dict[str, Any]:
    """Get the decision audit log.

    Applies UI aliases (``_apply_ui_aliases``) on the way out so that
    legacy entries on disk -- written before Phase 4.8 Fix A added the
    shim in ``record_decision`` -- still render correctly in
    ``DecisionQueue.tsx``. The helper is idempotent: entries that already
    have ``game_step`` / ``from_state`` / etc. pass through unchanged.
    """
    from alpha4gate.audit_log import _apply_ui_aliases

    path = _data_dir / "decision_audit.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_entries = data.get("entries", [])
        if isinstance(raw_entries, list):
            entries = [
                _apply_ui_aliases(dict(entry)) if isinstance(entry, dict) else entry
                for entry in raw_entries
            ]
        else:
            entries = []
        return {"entries": entries}
    return {"entries": []}


@app.post("/api/game/start")
async def start_game(request: dict[str, Any]) -> dict[str, Any]:
    """Start a new game (placeholder — actual implementation in runner)."""
    return {
        "game_id": "pending",
        "status": "starting",
    }


@app.post("/api/game/batch")
async def start_batch(request: dict[str, Any]) -> dict[str, Any]:
    """Start a batch run (placeholder — actual implementation in Step 9)."""
    return {
        "batch_id": "pending",
        "count": request.get("count", 0),
        "status": "running",
    }


# --- Training Endpoints ---


@app.get("/api/training/status")
async def get_training_status() -> dict[str, Any]:
    """Get current training status."""
    from alpha4gate.learning.checkpoints import get_best_name, list_checkpoints
    from alpha4gate.learning.database import TrainingDB

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


@app.post("/api/debug/raise_error")
async def debug_raise_error(
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synthetic error trigger for the alerts pre-flight check.

    Behind the ``DEBUG_ENDPOINTS`` env flag — returns 404 unless the
    flag is set to a truthy value. The endpoint logs an ERROR-level
    message that ``ErrorLogBuffer`` will capture and the frontend
    alerts rule will fire on. Used during soak-test pre-flight to
    verify the alerts pipeline is alive end-to-end before committing
    four hours to a run (see ``documentation/soak-test.md`` Section 3.5).
    """
    if os.environ.get("DEBUG_ENDPOINTS", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=404, detail="Debug endpoints disabled")
    message = "Synthetic alerts pre-flight test"
    if request is not None and isinstance(request.get("message"), str):
        message = request["message"]
    debug_logger = logging.getLogger("alpha4gate.debug")
    debug_logger.error("synthetic error: %s", message)
    return {"status": "ok", "logged": message}


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


@app.get("/api/training/reward-trends")
async def get_training_reward_trends(
    games: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """Aggregate per-rule reward contributions across recent games.

    The ``games`` query parameter caps how many of the most recent reward
    log files are scanned (default 100, min 1, max 1000). Missing reward_logs
    directories yield an empty-but-valid response.
    """
    from alpha4gate.learning.reward_aggregator import aggregate_reward_trends

    reward_logs_dir = _data_dir / "reward_logs"
    return aggregate_reward_trends(reward_logs_dir, games)


@app.get("/api/training/history")
async def get_training_history() -> dict[str, Any]:
    """Get training game history with win rates."""
    from alpha4gate.learning.database import TrainingDB

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


@app.get("/api/training/models")
async def get_training_models() -> dict[str, Any]:
    """Get per-model version win rate stats, ordered chronologically."""
    from alpha4gate.learning.database import TrainingDB

    db_path = _data_dir / "training.db"
    if not db_path.exists():
        return {"models": []}

    db = TrainingDB(db_path)
    models = db.get_all_model_stats()
    db.close()

    return {"models": models}


@app.get("/api/training/checkpoints")
async def get_training_checkpoints() -> dict[str, Any]:
    """List all training checkpoints."""
    from alpha4gate.learning.checkpoints import get_best_name, list_checkpoints

    cp_dir = _data_dir / "checkpoints"
    if not cp_dir.exists():
        return {"checkpoints": [], "best": None}

    return {
        "checkpoints": list_checkpoints(cp_dir),
        "best": get_best_name(cp_dir),
    }


@app.post("/api/training/start")
async def start_training(request: dict[str, Any]) -> dict[str, Any]:
    """Start the background training daemon."""
    if _daemon is None:
        return {"status": "error", "message": "Daemon not configured"}
    if _daemon.is_running():
        return {"status": "already_running"}
    _daemon.start()
    return {"status": "started"}


@app.post("/api/training/stop")
async def stop_training() -> dict[str, Any]:
    """Stop the background training daemon."""
    if _daemon is None:
        return {"status": "error", "message": "Daemon not configured"}
    if not _daemon.is_running():
        return {"status": "not_running"}
    _daemon.stop()
    return {"status": "stopped"}


@app.get("/api/training/daemon")
async def get_daemon_status() -> dict[str, Any]:
    """Return current training daemon status."""
    if _daemon is None:
        return {"running": False, "state": "not_configured"}
    return _daemon.get_status()


@app.get("/api/training/triggers")
async def get_training_triggers() -> dict[str, Any]:
    """Return current trigger evaluation state for debugging."""
    if _daemon is None:
        return {
            "transitions_since_last": 0,
            "hours_since_last": 0.0,
            "would_trigger": False,
            "reason": "daemon not configured",
        }
    return _daemon.get_trigger_state()


@app.put("/api/training/daemon/config")
async def update_daemon_config(request: dict[str, Any]) -> dict[str, Any]:
    """Update daemon configuration at runtime."""
    if _daemon is None:
        return {"status": "error", "message": "Daemon not configured"}
    from dataclasses import asdict

    updated = _daemon.update_config(request)
    return {"status": "updated", "config": asdict(updated)}


# --- Evaluation Endpoints ---

# In-memory evaluator instance (created lazily)
_evaluator: Any = None


def _get_evaluator() -> Any:
    """Get or create the ModelEvaluator instance."""
    global _evaluator
    if _evaluator is None:
        from alpha4gate.config import Settings
        from alpha4gate.learning.database import TrainingDB
        from alpha4gate.learning.evaluator import ModelEvaluator

        settings = Settings(
            sc2_path=Path("."),
            log_dir=_log_dir,
            replay_dir=_replay_dir,
            data_dir=_data_dir,
            web_ui_port=0,
            anthropic_api_key="",
            spawning_tool_api_key="",
        )
        db_path = _data_dir / "training.db"
        db = TrainingDB(db_path)
        _evaluator = ModelEvaluator(settings, db)
    return _evaluator


@app.post("/api/training/evaluate")
async def start_evaluation(request: dict[str, Any]) -> JSONResponse:
    """Start a model evaluation. Returns 202 with job ID for polling.

    Query params in body: checkpoint, games, difficulty.
    """
    import threading

    checkpoint = request.get("checkpoint", "")
    n_games = int(request.get("games", 10))
    difficulty = int(request.get("difficulty", 1))

    if not checkpoint:
        return JSONResponse(
            status_code=400,
            content={"error": "checkpoint is required"},
        )

    evaluator = _get_evaluator()
    job_id = evaluator.submit_job(checkpoint, n_games, difficulty)

    # Run evaluation in background thread
    thread = threading.Thread(target=evaluator.run_job, args=(job_id,), daemon=True)
    thread.start()

    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "status": "pending",
            "checkpoint": checkpoint,
            "games": n_games,
            "difficulty": difficulty,
        },
    )


@app.get("/api/training/evaluate/{job_id}", response_model=None)
async def get_evaluation_status(job_id: str) -> JSONResponse | dict[str, Any]:
    """Poll evaluation job status."""
    from dataclasses import asdict

    evaluator = _get_evaluator()
    job = evaluator.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    response: dict[str, Any] = {
        "job_id": job.job_id,
        "status": job.status,
        "checkpoint": job.checkpoint,
        "games_requested": job.n_games,
        "games_completed": job.games_completed,
        "difficulty": job.difficulty,
    }
    if job.result is not None:
        response["result"] = asdict(job.result)
    if job.error is not None:
        response["error"] = job.error
    return response


# --- Promotion Endpoints ---

# In-memory promotion manager (created lazily)
_promotion_manager: Any = None
# Promotion logger (created lazily)
_promotion_logger: Any = None


def _get_promotion_manager() -> Any:
    """Get or create the PromotionManager instance."""
    global _promotion_manager
    if _promotion_manager is None:
        from alpha4gate.learning.promotion import PromotionConfig, PromotionManager

        evaluator = _get_evaluator()
        _promotion_manager = PromotionManager(evaluator, PromotionConfig())
    return _promotion_manager


def _get_promotion_logger() -> Any:
    """Get or create the PromotionLogger instance."""
    global _promotion_logger
    if _promotion_logger is None:
        from alpha4gate.learning.promotion import PromotionLogger

        _promotion_logger = PromotionLogger(
            history_path=_data_dir / "promotion_history.json",
        )
    return _promotion_logger


@app.get("/api/training/promotions")
async def get_promotions() -> dict[str, Any]:
    """Get promotion decision history."""
    pm = _get_promotion_manager()
    return {"promotions": pm.get_history_dicts()}


@app.get("/api/training/promotions/history")
async def get_promotion_history() -> dict[str, Any]:
    """Get the full promotion history from the persistent JSON log."""
    logger = _get_promotion_logger()
    return {"history": logger.get_history()}


@app.get("/api/training/promotions/latest")
async def get_promotion_latest() -> dict[str, Any]:
    """Get the most recent promotion decision from the persistent log."""
    logger = _get_promotion_logger()
    latest = logger.get_latest()
    if latest is None:
        return {"latest": None}
    return {"latest": latest}


@app.post("/api/training/promote", response_model=None)
async def manual_promote(request: dict[str, Any]) -> JSONResponse | dict[str, Any]:
    """Manually promote a checkpoint (skips evaluation).

    Body: {"checkpoint": "v5"}
    """
    checkpoint = request.get("checkpoint", "")
    if not checkpoint:
        return JSONResponse(
            status_code=400,
            content={"error": "checkpoint is required"},
        )

    pm = _get_promotion_manager()
    decision = pm.manual_promote(checkpoint)

    return {
        "status": "promoted",
        "checkpoint": checkpoint,
        "old_best": decision.old_best,
    }


@app.post("/api/training/rollback", response_model=None)
async def manual_rollback(request: dict[str, Any]) -> JSONResponse | dict[str, Any]:
    """Manually rollback to a previous checkpoint.

    Body: {"checkpoint": "v3"}  — checkpoint name to revert to.
    """
    from alpha4gate.learning.checkpoints import get_best_name
    from alpha4gate.learning.database import TrainingDB
    from alpha4gate.learning.rollback import RollbackConfig, RollbackDecision, RollbackMonitor

    checkpoint = request.get("checkpoint", "")
    if not checkpoint:
        return JSONResponse(
            status_code=400,
            content={"error": "checkpoint is required"},
        )

    cp_dir = _data_dir / "checkpoints"
    current_best = get_best_name(cp_dir)
    if current_best is None:
        return JSONResponse(
            status_code=400,
            content={"error": "no current best checkpoint"},
        )

    if current_best == checkpoint:
        return JSONResponse(
            status_code=400,
            content={"error": f"already on checkpoint {checkpoint}"},
        )

    db_path = _data_dir / "training.db"
    db = TrainingDB(db_path)
    monitor = RollbackMonitor(
        db=db,
        config=RollbackConfig(),
        checkpoint_dir=cp_dir,
        history_path=_data_dir / "promotion_history.json",
    )
    decision = RollbackDecision(
        current_model=current_best,
        revert_to=checkpoint,
        current_win_rate=0.0,
        promotion_win_rate=0.0,
        games_played=0,
        reason="manual rollback via API",
    )
    monitor.execute_rollback(decision)
    db.close()

    return {
        "status": "rolled_back",
        "old_best": current_best,
        "new_best": checkpoint,
    }


# --- Curriculum Endpoints ---


@app.get("/api/training/curriculum")
async def get_curriculum() -> dict[str, Any]:
    """Return current curriculum state: difficulty, max, threshold, last advancement."""
    if _daemon is None:
        return {
            "current_difficulty": 1,
            "max_difficulty": 10,
            "win_rate_threshold": 0.8,
            "last_advancement": None,
        }
    return _daemon.get_curriculum_status()


@app.put("/api/training/curriculum", response_model=None)
async def set_curriculum(
    request: dict[str, Any],
) -> JSONResponse | dict[str, Any]:
    """Manually set curriculum difficulty fields.

    Body may include: current_difficulty, max_difficulty, win_rate_threshold.
    """
    if _daemon is None:
        return JSONResponse(
            status_code=400,
            content={"error": "Daemon not configured"},
        )
    return _daemon.set_curriculum(
        current_difficulty=request.get("current_difficulty"),
        max_difficulty=request.get("max_difficulty"),
        win_rate_threshold=request.get("win_rate_threshold"),
    )


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


@app.post("/api/shutdown")
async def shutdown_server() -> dict[str, str]:
    """Gracefully shut down the daemon and the server process.

    Stops the training daemon first, then schedules server exit after
    the response is sent.  The uv wrapper process exits automatically
    when its child (uvicorn) terminates.
    """
    if _daemon is not None and _daemon.is_running():
        _daemon.stop()
        _log.info("Daemon stopped via /api/shutdown")

    # Schedule the process exit after the response is returned.
    loop = asyncio.get_running_loop()
    loop.call_later(0.5, _exit_server)
    return {"status": "shutting_down"}


def _exit_server() -> None:
    """Raise SystemExit so uvicorn shuts down cleanly."""
    import signal as _signal
    import sys

    _log.info("Server shutting down via /api/shutdown")
    # On Windows, SIGBREAK is the closest to a graceful shutdown signal
    # that uvicorn handles.  Fall back to sys.exit if unavailable.
    if sys.platform == "win32":
        _signal.raise_signal(_signal.SIGBREAK)
    else:
        _signal.raise_signal(_signal.SIGINT)


@app.get("/api/reward-rules")
async def get_reward_rules() -> dict[str, Any]:
    """Get current reward shaping rules."""
    path = _data_dir / "reward_rules.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    return {"rules": []}


@app.put("/api/reward-rules")
async def update_reward_rules(rules: dict[str, Any]) -> dict[str, Any]:
    """Update reward shaping rules."""
    path = _data_dir / "reward_rules.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rules, indent=2) + "\n", encoding="utf-8")
    return {"updated": True, "rule_count": len(rules.get("rules", []))}


# --- Command Endpoints ---


@app.post("/api/commands")
async def submit_command(request: dict[str, Any]) -> dict[str, Any]:
    """Submit a text command. Structured parse first, then background interpreter."""
    text = request.get("text", "")
    primitives = _parser.parse(text, CommandSource.HUMAN)

    if primitives:
        queue = get_command_queue()
        for p in primitives:
            # API has no game clock — use infinite TTL so the bot always picks it up.
            p.ttl = float("inf")
            queue.push(p)
        _add_to_history(primitives[0].id, text, primitives, status="queued")
        await _broadcast_command_event({
            "type": "queued",
            "id": primitives[0].id,
            "parsed": [_primitive_to_dict(p) for p in primitives],
            "source": "human",
        })
        return {
            "id": primitives[0].id,
            "status": "queued",
            "text": text,
            "parsed": [_primitive_to_dict(p) for p in primitives],
        }
    else:
        cmd_id = str(uuid.uuid4())
        asyncio.create_task(_interpret_and_queue(cmd_id, text))
        return {
            "id": cmd_id,
            "status": "parsing",
            "text": text,
        }


@app.get("/api/commands/history")
async def get_command_history() -> dict[str, Any]:
    """Get the in-memory command history."""
    return {"commands": _command_history}


@app.get("/api/commands/mode")
async def get_command_mode() -> dict[str, Any]:
    """Get the current command mode."""
    settings = get_command_settings()
    return {"mode": settings.mode.value, "muted": settings.muted}


@app.put("/api/commands/mode", response_model=None)
async def set_command_mode(
    request: dict[str, Any],
) -> JSONResponse | dict[str, Any]:
    """Set the command mode. Clears the queue on mode switch."""
    mode_str = request.get("mode", "")
    try:
        new_mode = CommandMode(mode_str)
    except ValueError:
        valid = [m.value for m in CommandMode]
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid mode: {mode_str!r}", "valid_modes": valid},
        )
    settings = get_command_settings()
    settings.mode = new_mode
    queue = get_command_queue()
    cleared = queue.clear()
    for cmd in cleared:
        await _broadcast_command_event({
            "type": "cleared",
            "id": cmd.id,
            "reason": "mode switch",
        })
    return {"mode": settings.mode.value, "queue_cleared": bool(cleared)}


@app.get("/api/commands/settings")
async def get_current_command_settings() -> dict[str, Any]:
    """Return the current command settings."""
    settings = get_command_settings()
    return {
        "claude_interval": settings.claude_interval,
        "lockout_duration": settings.lockout_duration,
        "muted": settings.muted,
    }


@app.put("/api/commands/settings")
async def update_command_settings(request: dict[str, Any]) -> dict[str, Any]:
    """Update command settings (claude_interval, lockout_duration, muted)."""
    settings = get_command_settings()
    if "claude_interval" in request:
        settings.claude_interval = float(request["claude_interval"])
    if "lockout_duration" in request:
        settings.lockout_duration = float(request["lockout_duration"])
    if "muted" in request:
        settings.muted = bool(request["muted"])
    return {
        "claude_interval": settings.claude_interval,
        "lockout_duration": settings.lockout_duration,
        "muted": settings.muted,
    }


@app.get("/api/commands/primitives")
async def get_primitives() -> dict[str, Any]:
    """Get the action/target/location vocabulary for the command system."""
    return {
        "actions": [a.value for a in CommandAction],
        "targets": {
            "build": [
                "stalkers", "zealots", "immortals", "sentries", "pylon", "gateway", "forge",
            ],
            "tech": [
                "voidrays", "colossi", "high_templar", "dark_templar", "blink", "charge",
            ],
            "upgrade": ["weapons", "armor", "shields", "blink", "charge"],
        },
        "locations": [
            "main", "natural", "third", "fourth",
            "enemy_main", "enemy_natural", "enemy_third",
        ],
    }


# --- WebSocket Endpoints ---


@app.websocket("/ws/game")
async def ws_game(websocket: WebSocket) -> None:
    """WebSocket endpoint for live game state."""
    await ws_manager.connect_game(websocket)
    try:
        while True:
            # Keep connection alive; client doesn't send data
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect_game(websocket)


@app.websocket("/ws/commands")
async def ws_commands(websocket: WebSocket) -> None:
    """WebSocket endpoint for live command events."""
    await ws_manager.connect_commands(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect_commands(websocket)


@app.websocket("/ws/decisions")
async def ws_decisions(websocket: WebSocket) -> None:
    """WebSocket endpoint for live decision events."""
    await ws_manager.connect_decisions(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect_decisions(websocket)
