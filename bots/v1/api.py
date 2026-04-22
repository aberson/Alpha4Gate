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

from bots.v1.commands import (
    CommandAction,
    CommandInterpreter,
    CommandMode,
    CommandPrimitive,
    CommandSource,
    StructuredParser,
    get_command_queue,
    get_command_settings,
)
from bots.v1.error_log import get_error_log_buffer, install_error_log_handler
from bots.v1.learning.daemon import DaemonConfig, TrainingDaemon
from bots.v1.web_socket import (
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
# Cross-version orchestrator state (evolve run state/pool/results/control)
# lives at repo-root ``data/`` regardless of which bot version is current.
# ``_data_dir`` points at ``bots/<current>/data/`` for per-version state
# (training.db etc.), so evolve needs its own resolver.
_evolve_dir: Path = Path("data")

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
    global _data_dir, _log_dir, _replay_dir, _interpreter, _daemon, _evolve_dir
    install_error_log_handler()
    _data_dir = data_dir
    _log_dir = log_dir
    _replay_dir = replay_dir
    _evolve_dir = evolve_dir if evolve_dir is not None else data_dir
    if api_key:
        _interpreter = CommandInterpreter(api_key)

    # Build a Settings-like object for the daemon from the configured paths
    from bots.v1.config import Settings

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
    # Auto-configure when the app is launched directly (e.g. via
    # `uvicorn bots.v0.api:app --reload` in dev mode). The normal
    # --serve entrypoint calls configure() before uvicorn.run, so this
    # guard is a no-op there.
    if _daemon is None:
        from bots.v1.config import load_settings
        from bots.v1.learning.daemon import load_daemon_config
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
    """Get game statistics from training.db.

    Returns per-difficulty breakdowns, recent games with full detail,
    and overall aggregates.
    """
    import sqlite3

    db_path = _data_dir / "training.db"
    if not db_path.exists():
        return {
            "total_games": 0,
            "by_difficulty": [],
            "recent_games": [],
            "overall": {"wins": 0, "losses": 0, "win_rate": 0},
        }

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Overall totals
    c.execute("SELECT result, COUNT(*) as cnt FROM games GROUP BY result")
    totals = {row["result"]: row["cnt"] for row in c.fetchall()}
    wins = totals.get("win", 0)
    losses = totals.get("loss", 0)
    total = wins + losses

    # Per-difficulty breakdown
    c.execute("""
        SELECT difficulty,
               COUNT(*) as total,
               SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
               ROUND(AVG(duration_secs), 0) as avg_duration,
               ROUND(AVG(total_reward), 1) as avg_reward,
               ROUND(MIN(total_reward), 1) as min_reward,
               ROUND(MAX(total_reward), 1) as max_reward
        FROM games
        GROUP BY difficulty
        ORDER BY difficulty
    """)
    by_difficulty = []
    for row in c.fetchall():
        d_total = row["total"]
        d_wins = row["wins"]
        by_difficulty.append({
            "difficulty": row["difficulty"],
            "total": d_total,
            "wins": d_wins,
            "losses": row["losses"],
            "win_rate": round(d_wins / d_total, 3) if d_total > 0 else 0,
            "avg_duration_secs": row["avg_duration"],
            "avg_reward": row["avg_reward"],
            "min_reward": row["min_reward"],
            "max_reward": row["max_reward"],
        })

    # Recent games (last 30)
    c.execute("""
        SELECT game_id, map_name, difficulty, result,
               ROUND(duration_secs, 0) as duration,
               ROUND(total_reward, 1) as reward,
               model_version, created_at
        FROM games ORDER BY rowid DESC LIMIT 30
    """)
    recent = [dict(row) for row in c.fetchall()]

    # Win rate over last N games (rolling window for trend)
    c.execute("""
        SELECT result, difficulty, created_at
        FROM games ORDER BY rowid DESC LIMIT 50
    """)
    trend_rows = [dict(row) for row in c.fetchall()]
    # Compute rolling 10-game win rate
    win_trend: list[dict[str, Any]] = []
    for i in range(0, len(trend_rows) - 9):
        window = trend_rows[i : i + 10]
        w = sum(1 for r in window if r["result"] == "win")
        win_trend.append({
            "index": i,
            "win_rate": round(w / 10, 2),
            "timestamp": window[0]["created_at"],
        })

    conn.close()

    return {
        "total_games": total,
        "overall": {
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total, 3) if total > 0 else 0,
        },
        "by_difficulty": by_difficulty,
        "recent_games": recent,
        "win_trend": win_trend,
    }


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
    from bots.v1.build_orders import (
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
    from bots.v1.build_orders import load_build_orders, save_build_orders

    path = _data_dir / "build_orders.json"
    orders = load_build_orders(path)
    original_count = len(orders)
    orders = [o for o in orders if o.id != order_id]

    if len(orders) < original_count:
        save_build_orders(orders, path)
        return {"deleted": True}
    return {"deleted": False}


@app.get("/api/games")
async def get_games(
    limit: int = 100,
    offset: int = 0,
    difficulty: int | None = None,
    result: str | None = None,
) -> dict[str, Any]:
    """List games from training.db with optional filters."""
    import sqlite3

    db_path = _data_dir / "training.db"
    if not db_path.exists():
        return {"games": [], "total": 0}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    where_clauses: list[str] = []
    params: list[Any] = []
    if difficulty is not None:
        where_clauses.append("difficulty = ?")
        params.append(difficulty)
    if result is not None:
        where_clauses.append("result = ?")
        params.append(result)

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Total count
    c = conn.execute(f"SELECT COUNT(*) FROM games{where_sql}", params)
    total = c.fetchone()[0]

    # Paginated rows
    c = conn.execute(
        f"SELECT game_id, map_name, difficulty, result, "
        f"ROUND(duration_secs, 0) as duration, "
        f"ROUND(total_reward, 1) as reward, "
        f"model_version, created_at "
        f"FROM games{where_sql} ORDER BY rowid DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    games = [dict(row) for row in c.fetchall()]
    conn.close()

    return {"games": games, "total": total}


@app.get("/api/games/{game_id}")
async def get_game_detail(game_id: str) -> dict[str, Any]:
    """Get details for a single game, including per-step reward breakdown."""
    import sqlite3

    db_path = _data_dir / "training.db"
    game: dict[str, Any] = {}
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        c = conn.execute(
            "SELECT game_id, map_name, difficulty, result, "
            "ROUND(duration_secs, 0) as duration, "
            "ROUND(total_reward, 1) as reward, "
            "model_version, created_at FROM games WHERE game_id = ?",
            [game_id],
        )
        row = c.fetchone()
        if row:
            game = dict(row)
        conn.close()

    if not game:
        return {"game": None, "reward_steps": []}

    # Per-step reward log (if available)
    reward_steps: list[dict[str, Any]] = []
    reward_log = _data_dir / "reward_logs" / f"game_{game_id}.jsonl"
    if reward_log.exists():
        for line in reward_log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    reward_steps.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return {"game": game, "reward_steps": reward_steps}


@app.get("/api/decision-log")
async def get_decision_log() -> dict[str, Any]:
    """Get the decision audit log.

    Applies UI aliases (``_apply_ui_aliases``) on the way out so that
    legacy entries on disk -- written before Phase 4.8 Fix A added the
    shim in ``record_decision`` -- still render correctly in
    ``DecisionQueue.tsx``. The helper is idempotent: entries that already
    have ``game_step`` / ``from_state`` / etc. pass through unchanged.
    """
    from bots.v1.audit_log import _apply_ui_aliases

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
    from bots.v1.learning.checkpoints import get_best_name, list_checkpoints
    from bots.v1.learning.database import TrainingDB

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
    four hours to a run (see ``documentation/soak-test-runs/README.md`` Section 3.5).
    """
    if os.environ.get("DEBUG_ENDPOINTS", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=404, detail="Debug endpoints disabled")
    message = "Synthetic alerts pre-flight test"
    if request is not None and isinstance(request.get("message"), str):
        message = request["message"]
    debug_logger = logging.getLogger("bots.v0.debug")
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
    from bots.v1.learning.reward_aggregator import aggregate_reward_trends

    reward_logs_dir = _data_dir / "reward_logs"
    return aggregate_reward_trends(reward_logs_dir, games)


@app.post("/api/training/reset")
async def reset_training_data() -> dict[str, Any]:
    """Reset training data: back up and delete training.db + reward_logs.

    Creates timestamped backups before deleting so data can be recovered.
    """
    import shutil
    import time

    results: list[str] = []
    timestamp = int(time.time())

    # Back up and remove training.db
    db_path = _data_dir / "training.db"
    if db_path.exists():
        backup = _data_dir / f"training.pre-reset-{timestamp}.db"
        shutil.copy2(db_path, backup)
        db_path.unlink()
        results.append(f"training.db backed up to {backup.name} and deleted")
    else:
        results.append("training.db not found (already clean)")

    # Back up and remove reward_logs
    reward_logs_dir = _data_dir / "reward_logs"
    if reward_logs_dir.exists():
        backup_dir = _data_dir / f"reward_logs.pre-reset-{timestamp}"
        shutil.copytree(reward_logs_dir, backup_dir)
        shutil.rmtree(reward_logs_dir)
        results.append(f"reward_logs backed up to {backup_dir.name} and deleted")
    else:
        results.append("reward_logs not found (already clean)")

    return {"results": results, "backup_timestamp": timestamp}


@app.get("/api/training/history")
async def get_training_history() -> dict[str, Any]:
    """Get training game history with win rates."""
    from bots.v1.learning.database import TrainingDB

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
    from bots.v1.learning.database import TrainingDB

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
    from bots.v1.learning.checkpoints import get_best_name, list_checkpoints

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
        from bots.v1.config import Settings
        from bots.v1.learning.database import TrainingDB
        from bots.v1.learning.evaluator import ModelEvaluator

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
        "cancel_requested": job.cancel_requested,
    }
    if job.result is not None:
        response["result"] = asdict(job.result)
    if job.error is not None:
        response["error"] = job.error
    return response


@app.post("/api/training/evaluate/{job_id}/stop")
async def stop_evaluation(job_id: str) -> JSONResponse:
    """Request cancellation of an in-flight evaluation job.

    The current game finishes (no safe way to interrupt an SC2 game mid-step),
    then the loop exits. Poll GET /api/training/evaluate/{job_id} to observe
    the transition to status ``cancelled``.
    """
    evaluator = _get_evaluator()
    outcome = evaluator.cancel_job(job_id)
    if outcome == "not_found":
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    return JSONResponse(
        status_code=200,
        content={"job_id": job_id, "status": outcome},
    )


# --- Promotion Endpoints ---

# In-memory promotion manager (created lazily)
_promotion_manager: Any = None
# Promotion logger (created lazily)
_promotion_logger: Any = None


def _get_promotion_manager() -> Any:
    """Get or create the PromotionManager instance."""
    global _promotion_manager
    if _promotion_manager is None:
        from bots.v1.learning.promotion import PromotionConfig, PromotionManager

        evaluator = _get_evaluator()
        _promotion_manager = PromotionManager(evaluator, PromotionConfig())
    return _promotion_manager


def _get_promotion_logger() -> Any:
    """Get or create the PromotionLogger instance."""
    global _promotion_logger
    if _promotion_logger is None:
        from bots.v1.learning.promotion import PromotionLogger

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


@app.get("/api/improvements")
async def get_improvements() -> dict[str, Any]:
    """Get the improvement log (changes made by improve-bot-advised runs)."""
    path = _data_dir / "improvement_log.json"
    if not path.exists():
        return {"improvements": []}
    import json as _json

    result: dict[str, Any] = _json.loads(path.read_text())
    return result


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
    from bots.v1.learning.checkpoints import get_best_name
    from bots.v1.learning.database import TrainingDB
    from bots.v1.learning.rollback import RollbackConfig, RollbackDecision, RollbackMonitor

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
    "is_fallback": False,
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
    """
    data = _read_json_file(_evolve_dir / _EVOLVE_CURRENT_ROUND_FILE)
    if data is None:
        return dict(_EVOLVE_CURRENT_ROUND_IDLE)
    # Merge over the idle skeleton so callers can always destructure the
    # full shape, even if the on-disk file is missing a field.
    merged = dict(_EVOLVE_CURRENT_ROUND_IDLE)
    merged.update(data)
    return merged


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
    from bots.v1.process_registry import full_status

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


# ---------------------------------------------------------------------------
# Elo ladder (Phase 4)
# ---------------------------------------------------------------------------

# Shared data dir at repo root — NOT _data_dir (which is per-version).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@app.get("/api/ladder")
async def get_ladder() -> dict[str, Any]:
    """Get Elo ladder standings + head-to-head grid.

    Reads from shared ``data/bot_ladder.json`` (written by
    ``scripts/ladder.py update`` or ``ladder_replay``). Returns empty
    structures if the file does not exist.
    """
    ladder_path = _REPO_ROOT / "data" / "bot_ladder.json"
    if not ladder_path.is_file():
        return {"standings": [], "head_to_head": {}}

    import json as _json

    raw: dict[str, Any] = _json.loads(ladder_path.read_text(encoding="utf-8"))

    # Convert standings dict to sorted list for the frontend.
    standings_dict: dict[str, Any] = raw.get("standings", {})
    standings_list = [
        {"version": v, **entry} for v, entry in standings_dict.items()
    ]
    standings_list.sort(key=lambda e: e.get("elo", 0), reverse=True)

    return {
        "standings": standings_list,
        "head_to_head": raw.get("head_to_head", {}),
    }
