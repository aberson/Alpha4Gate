"""FastAPI REST endpoints for the Alpha4Gate dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from bots.v12.commands import (
    CommandAction,
    CommandInterpreter,
    CommandMode,
    CommandPrimitive,
    CommandSource,
    StructuredParser,
    get_command_queue,
    get_command_settings,
)
from bots.v12.error_log import get_error_log_buffer, install_error_log_handler
from bots.v12.learning.daemon import DaemonConfig, TrainingDaemon
from bots.v12.web_socket import (
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
    from bots.v12.config import Settings

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
        from bots.v12.config import load_settings
        from bots.v12.learning.daemon import load_daemon_config
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
    from bots.v12.build_orders import (
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
    from bots.v12.build_orders import load_build_orders, save_build_orders

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
    from bots.v12.audit_log import _apply_ui_aliases

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
    from bots.v12.learning.checkpoints import get_best_name, list_checkpoints
    from bots.v12.learning.database import TrainingDB

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
    debug_logger = logging.getLogger("bots.v12.debug")
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
    from bots.v12.learning.reward_aggregator import aggregate_reward_trends

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
    from bots.v12.learning.database import TrainingDB

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
    from bots.v12.learning.database import TrainingDB

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
    from bots.v12.learning.checkpoints import get_best_name, list_checkpoints

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
        from bots.v12.config import Settings
        from bots.v12.learning.database import TrainingDB
        from bots.v12.learning.evaluator import ModelEvaluator

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
        from bots.v12.learning.promotion import PromotionConfig, PromotionManager

        evaluator = _get_evaluator()
        _promotion_manager = PromotionManager(evaluator, PromotionConfig())
    return _promotion_manager


def _get_promotion_logger() -> Any:
    """Get or create the PromotionLogger instance."""
    global _promotion_logger
    if _promotion_logger is None:
        from bots.v12.learning.promotion import PromotionLogger

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
    from bots.v12.learning.checkpoints import get_best_name
    from bots.v12.learning.database import TrainingDB
    from bots.v12.learning.rollback import RollbackConfig, RollbackDecision, RollbackMonitor

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
    """Read a JSON file if it exists, return None otherwise.

    Hardened against non-dict top-level JSON: callers downstream do
    ``payload.get(...)`` and would crash with ``AttributeError`` (→ HTTP
    500) if the file contained ``[]`` or ``"foo"`` at the top level.
    Such cases are treated as missing — return ``None`` so the endpoint
    can fall back to its empty skeleton.
    """
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload
    return None


def _safe_int(value: Any, default: int = 0) -> int:
    """Coerce ``value`` to ``int``, returning ``default`` on any failure.

    Defense in depth for the file-backed aggregators: a corrupted state
    file with ``"games_played": "not-a-number"`` would 500 the endpoint
    if we used a bare ``int(value)``. Treat anything non-coercible as
    the default so the live-runs response is best-effort even when the
    on-disk JSON is malformed at the field level (rather than the
    line/file level — those are caught by ``_read_json_file``).

    ``None``, ``""``, ``"abc"``, and floats-with-fractional-parts all
    map to ``default``. Numeric strings (``"42"``) and booleans round-
    trip via ``int()``.
    """
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Models tab — version registry, config, lineage (Step 1a)
# ---------------------------------------------------------------------------
#
# IMPORTANT (per ``feedback_per_version_vs_cross_version_data_dir.md``):
# Endpoints that read both per-version state (training.db, hyperparams,
# reward rules, daemon config) AND cross-version state (improvement_log,
# evolve_results, lineage) MUST use SEPARATE resolvers. Sharing one
# resolver silently breaks either side — symptom is a 200 response with
# the idle skeleton even though the file exists at a different absolute
# path. Always pick the right resolver for the data class:
#
#   ``_per_version_data_dir(v)``  → ``<repo>/bots/v{N}/data/``
#   ``_cross_version_data_dir()`` → ``<repo>/data/``

# Repo root used by the resolver helpers below + the Elo-ladder section
# further down. ``api.py`` lives at ``bots/v10/api.py`` so three
# ``parent`` hops land at the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_VERSION_RE: re.Pattern[str] = re.compile(r"^v\d+$")
_SHA_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{7,40}$")
# ``game_id`` follows the format ``<base>_<12 hex>`` written by the
# learning environment (see ``bots/v10/learning/environment.py`` —
# ``self._game_id = f"{base}_{uuid.uuid4().hex[:12]}"``). The base is
# freeform (e.g. ``"unnamed"``, ``"live-test"``, ``"wsl-test"``), but is
# always alphanumeric / ``_`` / ``-`` in practice. The strict pattern
# below rejects shell metacharacters and SQL-special characters so a
# malformed game_id raised before any SQL is dispatched (defense in
# depth — queries are parameterized, but a 400 is still preferable to
# wasting a roundtrip on garbage input).
_GAME_ID_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# Process-wide lock guarding the lazy-init build of ``data/lineage.json``
# inside ``GET /api/lineage``. The endpoint double-checks file existence
# inside the lock so two concurrent first-time requests don't both
# spawn ``scripts/build_lineage.py``. Lazy-init runs ``subprocess.run``
# off the event loop via ``asyncio.to_thread``; the lock itself stays
# in the asyncio domain because it's only ever awaited from coroutines
# (the endpoint handler).
_lineage_lazy_init_lock: asyncio.Lock | None = None


def _get_lineage_lazy_init_lock() -> asyncio.Lock:
    """Return the singleton lazy-init lock, creating it on first use.

    Constructed lazily because ``asyncio.Lock()`` binds itself to the
    running event loop on Python < 3.10 and to a freshly-created loop
    on the FIRST coroutine that touches it on >= 3.10. Building it at
    module import time would attach to a transient loop (FastAPI's
    test loop, or a runner script's preflight loop) and then
    deadlock under uvicorn's real loop.
    """
    global _lineage_lazy_init_lock
    if _lineage_lazy_init_lock is None:
        _lineage_lazy_init_lock = asyncio.Lock()
    return _lineage_lazy_init_lock


def _validate_version(v: str) -> str:
    """Validate a version string against ``^v\\d+$``.

    Raises :class:`HTTPException` with status 400 on malformed input so
    other endpoints can re-use this helper. The string is returned
    unchanged on success so callers can chain ``v = _validate_version(v)``.

    Per plan §6.11 (input validation) — the regex is strict to forbid
    shell-metacharacter injection that might leak into a future
    ``git show <sha>:bots/<v>/...`` subprocess invocation.
    """
    if not isinstance(v, str) or not _VERSION_RE.match(v):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid version {v!r}: must match ^v\\d+$",
        )
    return v


def _validate_sha(sha: str) -> str:
    """Validate a git sha against ``^[0-9a-f]{7,40}$``.

    Returns the sha on success. Raises :class:`ValueError` (NOT
    ``HTTPException``) on malformed input — callers in the
    ``/api/versions/{v}/improvements`` endpoint catch the error and
    skip-and-warn at the call site rather than 500-ing the whole
    response. This is the conservative posture required by plan §6.11
    when the sha is going to feed a ``git show`` subprocess invocation.
    """
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        msg = f"Invalid git sha {sha!r}: must match ^[0-9a-f]{{7,40}}$"
        raise ValueError(msg)
    return sha


def _validate_game_id(game_id: str) -> str:
    """Validate a game_id against ``^[A-Za-z0-9_-]{1,128}$``.

    Raises :class:`HTTPException` with status 400 on malformed input so
    callers can chain ``game_id = _validate_game_id(game_id)``. The
    pattern matches what :class:`bots.v12.learning.environment.SC2Env`
    actually generates (``<base>_<12 hex>``) without being so loose that
    shell metacharacters or SQL-special characters survive — a malformed
    id is rejected BEFORE we hit the parameterized query.
    """
    if not isinstance(game_id, str) or not _GAME_ID_RE.match(game_id):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid game_id {game_id!r}: must match ^[A-Za-z0-9_-]{{1,128}}$",
        )
    return game_id


def _per_version_data_dir(version: str) -> Path:
    """Resolve the per-version data dir at ``<repo>/bots/v{N}/data/``.

    Used for per-version state: ``training.db``, ``hyperparams.json``,
    ``reward_rules.json``, ``daemon_config.json``, ``checkpoints/``,
    ``reward_logs/``. **Never** mix this with cross-version state — see
    the module-level note above and the recorded gotcha in
    ``feedback_per_version_vs_cross_version_data_dir.md``.

    The version arg must already be validated by :func:`_validate_version`
    (callers are responsible). The returned path is **not** required to
    exist; missing directories should be handled gracefully by the
    caller (return empty dict / list, never 500).
    """
    return _REPO_ROOT / "bots" / version / "data"


def _cross_version_data_dir() -> Path:
    """Resolve the cross-version data dir at ``<repo>/data/``.

    Used for cross-version orchestrator state:
    ``improvement_log.json`` (advised harness), ``evolve_results.jsonl``
    (evolve harness), ``lineage.json`` (DAG cache), ``bot_ladder.json``
    (Elo ladder), etc. **Never** mix this with per-version state — see
    the module-level note above.

    The returned path is **not** required to exist; missing directories
    should be handled gracefully by the caller (return empty dict /
    list, never 500).
    """
    return _REPO_ROOT / "data"


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


@app.get("/api/evolve/running-rounds")
async def get_evolve_running_rounds() -> dict[str, Any]:
    """Read all per-worker round-state files for the active parallel run."""
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

    parent_current = run_state.get("parent_current")

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


@app.get("/api/system/substrate")
async def get_system_substrate() -> dict[str, Any]:
    """Return host platform + WSL distro info for the Live tab badge.

    Cheap (cached 30 s).  Tells the operator at a glance whether SC2 is
    running on the Windows host, inside a WSL2 VM, or both.
    """
    from bots.v12.system_info import get_substrate_info

    return get_substrate_info()


@app.get("/api/system/wsl-processes")
async def get_system_wsl_processes() -> dict[str, Any]:
    """Return SC2 + python processes inside the WSL VM.

    Complements ``/api/processes`` (Windows-host only) so the Processes
    tab no longer undercounts when evolve runs on the WSL substrate.
    Returns ``{"available": False, "processes": []}`` when WSL is
    unreachable so the frontend can render an "unavailable" state.
    """
    from bots.v12.system_info import get_wsl_processes

    return get_wsl_processes()


@app.get("/api/system/resources")
async def get_system_resources() -> dict[str, Any]:
    """Return Windows host + WSL VM RAM and ``/mnt/c`` disk-free gauge.

    Surfaces the host-RAM-starvation condition that caused 2026-04-28's
    SC2-spawn timeouts before it bites again.  Cached 3 s.
    """
    from bots.v12.system_info import get_resources

    return get_resources()


@app.get("/api/processes")
async def get_processes() -> dict[str, Any]:
    """Get full system process/state status."""
    from bots.v12.process_registry import full_status

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
    cmd = [sys.executable, "-m", "bots.v12.runner", "--serve"]
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


# ---------------------------------------------------------------------------
# Models tab — endpoints (Step 1a)
# ---------------------------------------------------------------------------
#
# See the helpers near line 1120 for the ``_per_version_data_dir`` /
# ``_cross_version_data_dir`` / ``_validate_version`` rationale.


def _scan_versions_sync() -> list[dict[str, Any]]:
    """Walk ``bots/v*/manifest.json`` and derive the registry rows.

    Pure function (no FastAPI), runs inside ``asyncio.to_thread`` so the
    event loop is never blocked on the directory walk + ~11 small JSON
    reads + the three cross-version log scans (advised, evolve,
    self-play). Returns an empty list when ``bots/`` is missing or
    contains no version directories.
    """
    bots_dir = _REPO_ROOT / "bots"
    if not bots_dir.is_dir():
        return []

    # Identify the "current" version. Missing / unreadable pointer → no
    # version is flagged ``current: True``; every other field still
    # populates so the UI can render the registry without it.
    current_pointer = bots_dir / "current" / "current.txt"
    current_name: str | None = None
    if current_pointer.is_file():
        try:
            current_name = current_pointer.read_text(encoding="utf-8").strip()
        except OSError:
            current_name = None

    # Collect promotion sha/version markers from cross-version logs once
    # so we can O(1)-test each manifest's ``git_sha`` and ``version``
    # against the right harness origin.
    cross = _cross_version_data_dir()

    # ``improvement_log.json``: per-iteration entries written by the
    # advised harness. Today's schema has no ``git_sha``; we collect both
    # any sha-shaped value (forward-compat) and any ``bots/vN/`` target
    # path so we can resolve advised-touched versions either way.
    advised_shas: set[str] = set()
    advised_versions: set[str] = set()
    imp_log = _read_json_file(cross / "improvement_log.json")
    if imp_log is not None:
        entries = imp_log.get("improvements", [])
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                # Forward-compat: future advised entries may include sha.
                sha = entry.get("git_sha") or entry.get("sha") or entry.get("commit")
                if isinstance(sha, str) and sha:
                    advised_shas.add(sha)
                files = entry.get("files_changed", [])
                if isinstance(files, list):
                    for fp in files:
                        if not isinstance(fp, str):
                            continue
                        m = re.match(r"^bots/(v\d+)/", fp)
                        if m:
                            advised_versions.add(m.group(1))

    # ``evolve_results.jsonl``: one row per phase. Promotions are
    # ``stack-apply-pass`` (or contain a ``new_version`` field). We
    # collect ``new_version`` strings and any sha-shaped values for
    # forward-compat.
    evolve_shas: set[str] = set()
    evolve_versions: set[str] = set()
    evolve_path = cross / "evolve_results.jsonl"
    if evolve_path.is_file():
        try:
            text = evolve_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            new_v = row.get("new_version")
            if isinstance(new_v, str) and _VERSION_RE.match(new_v):
                evolve_versions.add(new_v)
            sha = row.get("git_sha") or row.get("sha") or row.get("commit")
            if isinstance(sha, str) and sha:
                evolve_shas.add(sha)

    # ``selfplay_results.jsonl``: one row per self-play match. Today's
    # schema (``SelfPlayRecord``) carries ``p1_version`` / ``p2_version``
    # / ``winner`` — the file logs MATCHES, not promotions. Per plan §5
    # the ``"self-play"`` origin is reserved for versions PROMOTED by a
    # self-play harness. We therefore scan for forward-compat keys
    # ``new_version`` / ``version`` matching ``^v\d+$`` (any sha-shaped
    # value too). Today no row carries these keys, so the set stays
    # empty in practice — but the code path is wired so a future
    # promotion-emitting self-play harness lights up without an API
    # change. This is the conservative wiring documented as option (b)
    # in the iter-2 review prompt, sufficient to honor the plan §5
    # contract.
    selfplay_shas: set[str] = set()
    selfplay_versions: set[str] = set()
    selfplay_path = cross / "selfplay_results.jsonl"
    if selfplay_path.is_file():
        try:
            text = selfplay_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            new_v = row.get("new_version") or row.get("version")
            if isinstance(new_v, str) and _VERSION_RE.match(new_v):
                selfplay_versions.add(new_v)
            sha = row.get("git_sha") or row.get("sha") or row.get("commit")
            if isinstance(sha, str) and sha:
                selfplay_shas.add(sha)

    # Walk version directories and emit rows.
    rows: list[dict[str, Any]] = []
    for child in sorted(bots_dir.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name == "current":
            continue
        if not _VERSION_RE.match(child.name):
            continue
        manifest = _read_json_file(child / "manifest.json")
        if manifest is None:
            continue

        version_name = child.name
        sha = manifest.get("git_sha")
        # Derive ``harness_origin``. Precedence per plan §5:
        # advised → evolve → self-play → manual. Evolve takes precedence
        # over advised because evolve promotions wrap multiple advised
        # iterations into one snapshot; the harness that produced THIS
        # version is the outer one. Advised in turn outranks self-play
        # because advised explicitly mutates ``bots/<v>/`` files;
        # self-play (today) only logs matches. If no cross-version log
        # claims this version, fall back to ``"manual"``.
        if (isinstance(sha, str) and sha in evolve_shas) or version_name in evolve_versions:
            harness_origin = "evolve"
        elif (
            isinstance(sha, str) and sha in advised_shas
        ) or version_name in advised_versions:
            harness_origin = "advised"
        elif (
            isinstance(sha, str) and sha in selfplay_shas
        ) or version_name in selfplay_versions:
            harness_origin = "self-play"
        else:
            harness_origin = "manual"

        rows.append({
            "name": version_name,
            "race": "protoss",  # single-race today (Phase G adds others)
            "parent": manifest.get("parent"),
            "harness_origin": harness_origin,
            "timestamp": manifest.get("timestamp"),
            "sha": sha,
            "fingerprint": manifest.get("fingerprint"),
            "current": version_name == current_name,
        })
    return rows


@app.get("/api/versions")
async def get_versions() -> list[dict[str, Any]]:
    """Return version registry metadata for every ``bots/v*/`` directory.

    Each row has shape::

        {name, race, parent, harness_origin, timestamp, sha,
         fingerprint, current: bool}

    ``race`` is the literal ``"protoss"`` until Phase G introduces
    multi-race versions. ``harness_origin`` ∈ ``{"advised", "evolve",
    "manual", "self-play"}`` is derived by cross-referencing each
    manifest's ``git_sha`` against ``data/improvement_log.json``
    (advised), ``data/evolve_results.jsonl`` (evolve), and
    ``data/selfplay_results.jsonl`` (self-play) — and, for today's logs
    that don't carry sha fields, by matching the version name itself.
    Precedence is evolve → advised → self-play → manual. Falls back to
    ``"manual"`` when no source claims the version. ``current`` is
    true for the version named in ``bots/current/current.txt``.

    Returns ``[]`` when no versions exist (fresh checkout); never 500s.
    """
    return await asyncio.to_thread(_scan_versions_sync)


@app.get("/api/versions/{v}/config")
async def get_version_config(v: str) -> dict[str, Any]:
    """Return per-version config files: hyperparams, reward rules, daemon.

    Reads ``bots/v{N}/data/{hyperparams,reward_rules,daemon_config}.json``.
    Each file is optional — a missing or malformed file returns ``{}``
    for that key, never 500. ``v`` is validated against ``^v\\d+$``;
    malformed input returns 400 (per plan §6.11 input validation).

    Uses :func:`_per_version_data_dir` (per-version resolver) — never
    the cross-version resolver. See module-level note at line ~1120.
    """
    v = _validate_version(v)
    data_dir = _per_version_data_dir(v)
    files = {
        "hyperparams": "hyperparams.json",
        "reward_rules": "reward_rules.json",
        "daemon_config": "daemon_config.json",
    }
    out: dict[str, Any] = {}
    for key, fname in files.items():
        payload = _read_json_file(data_dir / fname)
        out[key] = payload if payload is not None else {}
    return out


# ---------------------------------------------------------------------------
# Models tab — per-version data-read endpoints (Step 1b)
# ---------------------------------------------------------------------------
#
# Three endpoints aggregate per-version data for the Models tab:
#
#   GET /api/versions/{v}/training-history → rolling WR series from
#       ``bots/v{N}/data/training.db`` ``games`` table (filtered by
#       ``model_version == v``). Three series: rolling_10 / rolling_50 /
#       rolling_overall.
#   GET /api/versions/{v}/actions → action-id histogram from the
#       ``transitions`` table joined with ``games.model_version``.
#   GET /api/versions/{v}/improvements → unified-improvements timeline
#       filtered to entries that touched ``v``. Source files are
#       cross-version; derivation reads ``files_changed`` and resolves
#       any ``bots/current/...`` path via ``git show <sha>:bots/current/
#       current.txt`` (cached per sha).
#
# All three endpoints validate ``v`` against ``^v\d+$`` (400 on
# malformed input) and return empty payloads when source files are
# missing — they never 500 on a missing DB / log.


# Action-id → name map. The RL action space is defined as the single
# source of truth in ``bots/v10/decision_engine.py::ACTION_TO_STATE``;
# ``learning/features.py`` does NOT carry an id-to-name map (it owns
# observation-vector encoding, not action decoding). We import the
# decision-engine list lazily inside the resolver so test environments
# that monkeypatch ``_REPO_ROOT`` don't pull in the full bot stack at
# import time.
def _action_name_for(action_id: int) -> str:
    """Return a human label for a PPO action index.

    Reads from ``bots.v12.decision_engine.ACTION_TO_STATE``. Falls back
    to ``f"action_{action_id}"`` when the id is out of range (defensive
    against historical DB rows whose ``action`` column predates an
    action-space change).
    """
    try:
        from bots.v12.decision_engine import ACTION_TO_STATE
    except ImportError:
        return f"action_{action_id}"
    if 0 <= action_id < len(ACTION_TO_STATE):
        # ``StrategicState`` is a ``StrEnum`` — ``str(x)`` gives the
        # underlying string value (e.g. ``"opening"``).
        return str(ACTION_TO_STATE[action_id])
    return f"action_{action_id}"


def _training_history_sync(version: str) -> dict[str, list[dict[str, Any]]]:
    """Aggregate rolling WR series for ``version`` from training.db.

    Returns ``{rolling_10, rolling_50, rolling_overall}``. Each value is
    a list of ``{game_id, ts, wr}`` dicts ordered chronologically (oldest
    first, so the frontend can chart left→right without reversing).

    * ``rolling_10`` and ``rolling_50``: rolling win-rate over a sliding
      window of N most recent games. Emitted only for the most recent
      N entries.
    * ``rolling_overall``: running win-rate across all games for this
      version (cumulative wins / cumulative games).

    Returns the empty skeleton if the DB doesn't exist or has no games
    for ``version``. Uses ``idx_games_model`` for the WHERE filter.
    """
    db_path = _per_version_data_dir(version) / "training.db"
    empty: dict[str, list[dict[str, Any]]] = {
        "rolling_10": [],
        "rolling_50": [],
        "rolling_overall": [],
    }
    if not db_path.is_file():
        return empty

    try:
        # ``check_same_thread=True`` (default) is fine — the function is
        # invoked inside ``asyncio.to_thread`` which guarantees a single
        # owning thread. Read-only path, but we open in URI mode to make
        # that explicit and so a stale writer can't silently mutate the
        # snapshot mid-read.
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
            timeout=2.0,
        )
    except sqlite3.OperationalError:
        return empty

    try:
        # Schema columns are ``game_id`` / ``created_at`` / ``result``
        # (see ``bots/v10/learning/database.py``); we expose them as
        # ``game_id`` / ``ts`` / ``wr`` per the Step 1b spec. ORDER BY
        # ``created_at`` ASC so callers receive rows oldest-first ready
        # for charting; rolling windows are computed in Python.
        rows = conn.execute(
            "SELECT game_id, created_at, result FROM games "
            "WHERE model_version = ? ORDER BY created_at ASC",
            (version,),
        ).fetchall()
    except sqlite3.OperationalError:
        # DB exists but the ``games`` table or ``model_version`` column
        # is missing (e.g. a partial / corrupt file). Treat as no data.
        rows = []
    finally:
        conn.close()

    if not rows:
        return empty

    # Build a wins-flag list once and reuse for all three series.
    games: list[tuple[str, str, int]] = [
        (str(game_id), str(ts), 1 if result == "win" else 0)
        for game_id, ts, result in rows
    ]

    def _rolling_window(window: int) -> list[dict[str, Any]]:
        # The most recent ``window`` games' rolling-WR. We emit one
        # point per game in that tail so the chart shows the WR
        # *moving* across the window.
        if len(games) < window:
            return []
        tail = games[-window:]
        out: list[dict[str, Any]] = []
        wins_sum = 0
        for i, (gid, ts, won) in enumerate(tail, start=1):
            wins_sum += won
            out.append({"game_id": gid, "ts": ts, "wr": wins_sum / i})
        return out

    overall: list[dict[str, Any]] = []
    cum_wins = 0
    for i, (gid, ts, won) in enumerate(games, start=1):
        cum_wins += won
        overall.append({"game_id": gid, "ts": ts, "wr": cum_wins / i})

    return {
        "rolling_10": _rolling_window(10),
        "rolling_50": _rolling_window(50),
        "rolling_overall": overall,
    }


@app.get("/api/versions/{v}/training-history")
async def get_version_training_history(v: str) -> dict[str, list[dict[str, Any]]]:
    """Return rolling-WR series for ``v`` filtered by ``model_version``.

    Three lists of ``{game_id, ts, wr}`` entries:

    * ``rolling_10``: sliding 10-game window (only the last 10 games'
      cumulative WR over the window).
    * ``rolling_50``: sliding 50-game window.
    * ``rolling_overall``: running WR across every game ever recorded
      for this model version.

    Returns ``{rolling_10:[],rolling_50:[],rolling_overall:[]}`` when
    the DB is missing or has no rows for ``v``; never 500.

    Uses :func:`_per_version_data_dir` because ``training.db`` is
    per-version state. SQLite reads are funneled through
    ``asyncio.to_thread`` so the event loop is never blocked.
    """
    v = _validate_version(v)
    return await asyncio.to_thread(_training_history_sync, v)


def _action_distribution_sync(version: str) -> list[dict[str, Any]]:
    """Aggregate per-action counts for ``version`` from the DB.

    Joins ``transitions`` against ``games.model_version`` and groups by
    ``action`` to produce ``[{action_id, name, count, pct}, ...]``
    sorted by count descending. Returns ``[]`` if the DB doesn't exist
    or has no rows.

    Action labels come from :func:`_action_name_for`.
    """
    db_path = _per_version_data_dir(version) / "training.db"
    if not db_path.is_file():
        return []

    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
            timeout=2.0,
        )
    except sqlite3.OperationalError:
        return []

    try:
        rows = conn.execute(
            "SELECT t.action, COUNT(*) FROM transitions t "
            "JOIN games g ON t.game_id = g.game_id "
            "WHERE g.model_version = ? "
            "GROUP BY t.action",
            (version,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()

    if not rows:
        return []

    # Historical rows store ``action`` as np.int64.tobytes() blobs (see
    # ``_coerce_action`` in database.py). Decode any bytes/memoryview
    # values defensively before bucketing — otherwise GROUP BY treats
    # the same logical id as multiple keys.
    bucketed: dict[int, int] = {}
    for raw_action, raw_count in rows:
        if isinstance(raw_action, (bytes, memoryview)):
            try:
                action_id = int.from_bytes(bytes(raw_action), "little", signed=True)
            except (ValueError, TypeError):
                continue
        else:
            try:
                action_id = int(raw_action)
            except (ValueError, TypeError):
                continue
        bucketed[action_id] = bucketed.get(action_id, 0) + int(raw_count)

    total = sum(bucketed.values())
    if total <= 0:
        return []

    rows_out: list[dict[str, Any]] = [
        {
            "action_id": action_id,
            "name": _action_name_for(action_id),
            "count": count,
            "pct": count / total,
        }
        for action_id, count in bucketed.items()
    ]
    rows_out.sort(key=lambda r: int(r["count"]), reverse=True)
    return rows_out


@app.get("/api/versions/{v}/actions")
async def get_version_actions(v: str) -> list[dict[str, Any]]:
    """Return action-id histogram for ``v`` from the per-version DB.

    Each entry: ``{action_id, name, count, pct}``. Sorted by count
    descending. ``name`` is resolved via
    :func:`_action_name_for` (reads ``decision_engine.ACTION_TO_STATE``).

    Returns ``[]`` when no transitions exist for ``v`` or the DB is
    absent — never 500.
    """
    v = _validate_version(v)
    return await asyncio.to_thread(_action_distribution_sync, v)


# Helpers ported from ``bots/v0/api.py`` (the Phase 4.5 dashboard
# refactor's unified endpoint) so the Step 1b filtered version can
# emit the same per-entry shape. Step 4 of the Models-tab plan adds
# the public ``/api/improvements/unified`` endpoint below — both the
# per-version filter (``GET /api/versions/{v}/improvements``) and the
# unified Lineage-view timeline mode share the same private helpers.

# ---- Step 1b: per-version improvements timeline ----------------------------
#
# These helpers are forked from bots/v0/api.py's unified-improvements helpers.
# v10 adds ``_commit_sha`` plumbing (collected during normalization, stripped
# before serialization) for the per-version filter that resolves
# ``bots/current/...`` paths via ``git show <sha>:bots/current/current.txt``.
#
# Drift between bots/v0 and bots/v10 versions is expected and intentional
# under the snapshot-isolation model — ``snapshot_bot.py`` rewrites imports
# per version and the master-plan disallows cross-version imports (see
# memory: feedback_snapshot_import_isolation.md). Future evolve generations
# will inherit this v10 shape via re-snapshot from current.

_PHASE_ORDINAL: dict[str, int] = {"fitness": 0, "stack_apply": 1, "regression": 2}

_ADVISED_OUTCOME_MAP: dict[str, str] = {
    "pass": "promoted",
    "stopped": "discarded",
    "fail": "discarded",
}


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
        # Unified-shape extension for derivation: keep the raw commit
        # sha around so the per-version filter can resolve
        # ``bots/current/...`` paths via ``git show``.
        "_commit_sha": (
            entry.get("git_sha") or entry.get("sha") or entry.get("commit")
        ),
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
        "_commit_sha": (
            canonical.get("git_sha")
            or canonical.get("sha")
            or canonical.get("commit")
        ),
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


def _unified_improvements_sync(
    source: str | None, limit: int
) -> list[dict[str, Any]]:
    """Synchronous worker for ``/api/improvements/unified``.

    Reads ``improvement_log.json`` and ``evolve_results.jsonl`` from the
    cross-version data dir, normalises both into the shared shape, sorts
    by timestamp descending, applies the optional ``source`` filter and
    ``limit`` cap, then strips the internal ``_commit_sha`` book-keeping
    field. Missing source files yield an empty list (no error).

    Pulled out so the FastAPI handler can run it via
    ``asyncio.to_thread`` — keeps the file I/O off the event loop the
    same way the sibling ``/api/versions/{v}/improvements`` endpoint
    does (see ``_filtered_version_improvements`` below).
    """
    cross = _cross_version_data_dir()
    advised_path = cross / "improvement_log.json"
    evolve_path = cross / _EVOLVE_RESULTS_FILE

    entries: list[dict[str, Any]] = []
    if source != "evolve":
        entries.extend(_load_advised_improvements(advised_path))
    if source != "advised":
        entries.extend(_load_evolve_improvements(evolve_path))

    # Sort by timestamp descending. Entries without a timestamp sort to
    # the end (treated as the empty string, which compares smallest).
    entries.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    return [
        {k: v for k, v in entry.items() if k != "_commit_sha"}
        for entry in entries[:limit]
    ]


@app.get("/api/improvements/unified")
async def get_improvements_unified(
    source: str | None = Query(default=None, pattern="^(advised|evolve)$"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """Return a unified timeline of advised + evolve improvements.

    Mirrors the public ``/api/improvements/unified`` endpoint that
    ``bots/v0/api.py`` ships (see lines ~508 there). Step 1b ported the
    helpers privately to v10 for the per-version filter; Step 4 (Models
    tab Lineage view → Timeline mode) needs the same public endpoint on
    the v10 runner so the dashboard can subsume the legacy Improvements
    tab.

    Heavy work (two filesystem reads + JSON/JSONL parsing + normalize +
    sort) is delegated to ``_unified_improvements_sync`` via
    ``asyncio.to_thread`` so it never blocks the event loop — same
    pattern as the sibling ``/api/versions/{v}/improvements`` endpoint.

    The ``_commit_sha`` book-keeping field carried by the v10 helpers is
    stripped from each entry before serialization so the response shape
    matches v0's contract exactly (``id``, ``source``, ``timestamp``,
    ``title``, ``description``, ``type``, ``outcome``, ``metric``,
    ``principles``, ``files_changed``).
    """
    cleaned = await asyncio.to_thread(
        _unified_improvements_sync, source, limit
    )
    return {"improvements": cleaned}


_BOTS_VN_PATH_RE: re.Pattern[str] = re.compile(r"^bots/(v\d+)/")
_BOTS_CURRENT_PATH_RE: re.Pattern[str] = re.compile(r"^bots/current/")


def _resolve_current_at_sha(
    sha: str,
    cache: dict[str, str | None],
    repo_root: Path,
) -> str | None:
    """Resolve ``bots/current/current.txt`` content at a given commit.

    Returns the version string (e.g. ``"v3"``) or ``None`` when the
    lookup fails or the SHA is malformed. Results are memoised in
    ``cache`` (keyed by sha) so the same commit is never shelled out
    twice within a single request.

    Subprocess invocation is list-form, ``shell=False``, ``timeout=5``,
    cwd pinned to ``repo_root``. SHA is re-validated by
    :func:`_validate_sha` before the subprocess is spawned (defense in
    depth — the caller already validates, but the subprocess invocation
    is a security boundary worth re-checking at).
    """
    if sha in cache:
        return cache[sha]

    try:
        _validate_sha(sha)
    except ValueError as exc:
        _log.warning("Skipping improvement entry: %s", exc)
        cache[sha] = None
        return None

    try:
        proc = subprocess.run(
            ["git", "show", f"{sha}:bots/current/current.txt"],
            shell=False,
            timeout=5,
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _log.warning("git show failed for sha %s: %s", sha, exc)
        cache[sha] = None
        return None

    if proc.returncode != 0:
        _log.warning(
            "git show non-zero exit for sha %s: %s", sha, proc.stderr.strip()
        )
        cache[sha] = None
        return None

    value = proc.stdout.strip()
    if not _VERSION_RE.match(value):
        cache[sha] = None
        return None
    cache[sha] = value
    return value


def _entry_targets_version(
    entry: dict[str, Any],
    target_version: str,
    sha_cache: dict[str, str | None],
    repo_root: Path,
) -> bool:
    """Return ``True`` iff ``entry`` claims a file inside ``target_version``.

    Derivation (per Step 1b spec):

    1. Any file path matching ``bots/(v\\d+)/`` is attributed directly
       to that version.
    2. Any file path matching ``bots/current/...`` resolves to the
       version pointed at by ``current.txt`` at the entry's commit sha
       (cached). When no sha is available or git resolution fails, the
       ``bots/current/...`` path is silently skipped (the entry may
       still match via a different ``bots/vN/`` path).

    Files that match neither pattern (legacy ``data/...``, ``src/...``)
    are ignored — they don't belong to any single version. An entry
    whose ``files_changed`` is empty therefore returns ``False``.
    """
    files = entry.get("files_changed") or []
    if not isinstance(files, list):
        return False

    sha = entry.get("_commit_sha")
    for fp in files:
        if not isinstance(fp, str):
            continue
        m = _BOTS_VN_PATH_RE.match(fp)
        if m:
            if m.group(1) == target_version:
                return True
            continue
        if _BOTS_CURRENT_PATH_RE.match(fp):
            if not isinstance(sha, str) or not sha:
                continue
            resolved = _resolve_current_at_sha(sha, sha_cache, repo_root)
            if resolved == target_version:
                return True
    return False


def _filtered_version_improvements(version: str) -> list[dict[str, Any]]:
    """Load advised + evolve entries and filter to those touching ``version``.

    Drops the internal ``_commit_sha`` book-keeping field before
    returning so the response shape exactly matches the unified
    endpoint's ``improvements[]`` entry contract.
    """
    cross = _cross_version_data_dir()
    advised_path = cross / "improvement_log.json"
    evolve_path = cross / _EVOLVE_RESULTS_FILE

    entries: list[dict[str, Any]] = []
    entries.extend(_load_advised_improvements(advised_path))
    entries.extend(_load_evolve_improvements(evolve_path))

    sha_cache: dict[str, str | None] = {}
    out: list[dict[str, Any]] = []
    for entry in entries:
        if _entry_targets_version(entry, version, sha_cache, _REPO_ROOT):
            cleaned = {k: v for k, v in entry.items() if k != "_commit_sha"}
            out.append(cleaned)

    out.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    return out


@app.get("/api/versions/{v}/improvements")
async def get_version_improvements(v: str) -> list[dict[str, Any]]:
    """Return improvement-timeline entries that targeted ``v``.

    Reuses the ``/api/improvements/unified`` per-entry shape (see
    ``bots/v0/api.py``), filtered by deriving the target version from
    each entry's ``files_changed[]``:

    * ``bots/(v\\d+)/...`` paths → version ``vN``.
    * ``bots/current/...`` paths → resolved via ``git show
      <commit_sha>:bots/current/current.txt`` (cached per sha).
      Malformed SHAs and failed lookups are skip-and-warn (entry may
      still match a sibling ``bots/vN/...`` path); never 500.

    Sorted by timestamp descending. Returns ``[]`` when no entries
    target ``v``.
    """
    v = _validate_version(v)
    return await asyncio.to_thread(_filtered_version_improvements, v)


# ---------------------------------------------------------------------------
# Models tab — live-runs aggregator + per-game forensics + weight-dynamics
# (Step 1c)
# ---------------------------------------------------------------------------
#
# Three endpoints aggregate cross-harness state for the Models tab's
# Inspector + Live-Runs panels:
#
#   GET /api/runs/active                   → cross-harness live-run list
#   GET /api/versions/{v}/forensics/{game} → trajectory + give-up + dispatch
#   GET /api/versions/{v}/weight-dynamics  → l2/kl rows from data/weight_dynamics.jsonl
#
# All three are read-only, never 500 on missing data, and validate path
# params strictly (per plan §6.11).


def _runs_active_training_daemon_row() -> dict[str, Any] | None:
    """Build a row for the in-process training daemon if it is running.

    Calls the same ``_daemon.get_status()`` shape that ``/api/training/
    daemon`` returns — directly, NOT via an HTTP self-call. The training
    daemon is per-version-current and at most one is active at a time;
    this helper produces one row when running, ``None`` otherwise.
    """
    if _daemon is None or not _daemon.is_running():
        return None
    status = _daemon.get_status()
    # Best-effort version: the daemon does not carry the version label
    # directly, but the configured per-version data dir resolves to
    # ``bots/<v>/data/`` — fish the version out of the path so the row
    # matches the rest of the aggregator.
    version = ""
    try:
        # ``_data_dir`` points at ``bots/<v>/data`` in production. Walk
        # the path parts and pick the segment matching ``^v\d+$``.
        for part in _data_dir.parts:
            if _VERSION_RE.match(part):
                version = part
                break
    except (TypeError, ValueError):
        version = ""
    last_run = status.get("last_run") or ""
    return {
        "harness": "training-daemon",
        "version": version,
        "phase": str(status.get("state") or ""),
        "current_imp": "",
        "games_played": 0,
        "games_total": 0,
        "score_cand": 0,
        "score_parent": 0,
        "started_at": last_run,
        "updated_at": last_run,
    }


def _runs_active_advised_row() -> dict[str, Any] | None:
    """Build a row for the advised harness if its state file says active."""
    data = _read_json_file(_data_dir / _ADVISED_STATE_FILE)
    if data is None:
        return None
    status = str(data.get("status") or "")
    # Treat anything other than the explicit idle / completed / stopped
    # statuses as active. ``running`` is the canonical active value, but
    # the advised skill writes various intermediate phase strings (e.g.
    # ``"validating"``) and we want all of them to surface.
    inactive = {"", "idle", "completed", "stopped", "done"}
    if status in inactive:
        return None
    iteration = data.get("iteration")
    current_imp = ""
    imp = data.get("current_improvement")
    if isinstance(imp, dict):
        current_imp = str(imp.get("title") or "")
    elif isinstance(imp, str):
        current_imp = imp
    elif iteration is not None:
        current_imp = f"iter{iteration}"
    started_at = str(data.get("started_at") or data.get("created_at") or "")
    updated_at = str(data.get("updated_at") or started_at or "")
    return {
        "harness": "advised",
        "version": str(data.get("version") or ""),
        "phase": str(data.get("phase_name") or status),
        "current_imp": current_imp,
        "games_played": _safe_int(data.get("games_played")),
        "games_total": _safe_int(data.get("games_total")),
        "score_cand": _safe_int(data.get("score_cand")),
        "score_parent": _safe_int(data.get("score_parent")),
        "started_at": started_at,
        "updated_at": updated_at,
    }


def _runs_active_evolve_rows_sync() -> list[dict[str, Any]]:
    """Scan ``evolve_round_<worker>.json`` directly for per-worker rows.

    Per plan §6.3 the aggregator scans the cross-version data dir for
    worker state files directly so we catch workers whose live-state
    file exists but isn't surfaced through ``/api/evolve/running-
    rounds`` yet (parallelization edge case where ``evolve_run_state.
    json`` lags behind, or run_id mismatch).

    Reads ``parent_current`` from ``evolve_run_state.json`` when
    available so each row carries the version label expected by the
    Live-Runs UI; falls back to empty string when the run-state file is
    missing.

    This is a blocking file-scan; callers MUST invoke it through
    ``asyncio.to_thread`` so the event loop is never stalled.
    """
    rows: list[dict[str, Any]] = []
    run_state = _read_json_file(_evolve_dir / _EVOLVE_STATE_FILE)
    parent_current = ""
    if run_state is not None:
        parent_current = str(run_state.get("parent_current") or "")
        started_at = str(run_state.get("started_at") or "")
    else:
        started_at = ""

    for path in _evolve_dir.glob("evolve_round_*.json"):
        entry = _read_json_file(path)
        if entry is None:
            continue
        # Only surface rows that the on-disk file flags as active.
        # Inactive worker files persist across rounds for debugging but
        # should never show up in the live-runs list.
        if not bool(entry.get("active", False)):
            continue
        wid = entry.get("worker_id")
        worker_label = (
            f"worker-{wid}" if isinstance(wid, int) else "worker-?"
        )
        updated_at = str(entry.get("updated_at") or "")
        rows.append({
            "harness": "evolve",
            "version": parent_current,
            "phase": str(entry.get("phase") or ""),
            "current_imp": (
                f"{worker_label}: {entry.get('imp_title') or ''}".strip(": ")
            ),
            "games_played": _safe_int(entry.get("games_played")),
            "games_total": _safe_int(entry.get("games_total")),
            "score_cand": _safe_int(entry.get("score_cand")),
            "score_parent": _safe_int(entry.get("score_parent")),
            "started_at": started_at,
            "updated_at": updated_at,
        })
    return rows


@app.get("/api/runs/active")
async def get_runs_active() -> list[dict[str, Any]]:
    """Return the cross-harness list of currently-active runs.

    Aggregates from three harnesses without making any HTTP self-calls:

    * ``training-daemon`` — in-memory ``_daemon`` reference; reuses the
      same ``.get_status()`` output as ``/api/training/daemon``.
    * ``advised`` — file-backed ``advised_run_state.json``; one row when
      the file's ``status`` indicates the run is active.
    * ``evolve`` — direct glob of ``evolve_round_<worker>.json`` files
      in the cross-version data dir, optionally enriched with
      ``parent_current`` / ``started_at`` from ``evolve_run_state.json``
      when the run-state file is present. One row per active worker.

    The shape of every row is::

        {harness, version, phase, current_imp, games_played, games_total,
         score_cand, score_parent, started_at, updated_at}

    Numeric fields default to ``0`` (not ``null``) when the source file
    lacks them; ``current_imp`` defaults to the empty string. Returns
    ``[]`` when nothing is active. Sorted by ``updated_at`` descending
    (most recently updated first).
    """
    rows: list[dict[str, Any]] = []

    daemon_row = _runs_active_training_daemon_row()
    if daemon_row is not None:
        rows.append(daemon_row)

    advised_row = _runs_active_advised_row()
    if advised_row is not None:
        rows.append(advised_row)

    evolve_rows = await asyncio.to_thread(_runs_active_evolve_rows_sync)
    rows.extend(evolve_rows)

    rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return rows


def _forensics_sync(version: str, game_id: str) -> dict[str, Any]:
    """Read ``transitions`` rows for ``(version, game_id)`` from the per-version DB.

    Returns the forensics shape::

        {trajectory: [{step, win_prob, ts}, ...],
         give_up_fired: bool,
         give_up_step: int | null,
         expert_dispatch: null}

    * ``trajectory`` is ordered by ``step_index`` ascending.
    * ``win_prob`` reads from the ``transitions.win_prob`` column (Phase
      N is live; old rows without the column return ``None``).
    * ``give_up_fired`` is hardcoded ``False`` because the schema in
      ``bots/v10/learning/database.py`` does NOT carry a ``give_up``
      column — give-up is observed externally via ``Alpha4GateBot.
      _maybe_resign`` and isn't persisted per-transition. When the
      column lands in a future schema migration, this helper is the
      single edit point.
    * ``expert_dispatch`` is hardcoded ``None`` per plan §6.8; it
      becomes meaningful only when Phase O writes ``expert_id`` to
      transitions.

    Returns the empty-trajectory shape when the DB is missing, the game
    does not exist, or the join finds zero rows. Never 500s.
    """
    empty: dict[str, Any] = {
        "trajectory": [],
        "give_up_fired": False,
        "give_up_step": None,
        "expert_dispatch": None,
    }

    db_path = _per_version_data_dir(version) / "training.db"
    if not db_path.is_file():
        return empty

    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
            timeout=2.0,
        )
    except sqlite3.Error:
        # Widen to ``sqlite3.Error`` (parent of ``OperationalError``,
        # ``DatabaseError``, etc.) so a corrupted DB file — which raises
        # ``DatabaseError`` on connect / query — still returns the empty
        # skeleton instead of bubbling up as a 500. Defense in depth: the
        # contract is "never 500", and any sqlite3-flavoured failure on
        # the read path means we have no usable trajectory.
        return empty

    try:
        # Filter on BOTH ``model_version`` (via the games table) AND
        # ``game_id`` so a duplicate game_id across two version DBs
        # cannot leak across endpoints. ``ORDER BY step_index ASC`` is
        # the trajectory order the Inspector consumes left-to-right.
        rows = conn.execute(
            "SELECT t.step_index, t.win_prob, t.game_time "
            "FROM transitions t "
            "JOIN games g ON t.game_id = g.game_id "
            "WHERE g.model_version = ? AND t.game_id = ? "
            "ORDER BY t.step_index ASC",
            (version, game_id),
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()

    if not rows:
        return empty

    trajectory: list[dict[str, Any]] = []
    for step_index, win_prob, game_time in rows:
        trajectory.append({
            "step": int(step_index),
            "win_prob": (
                float(win_prob) if win_prob is not None else None
            ),
            # The transitions table has no ISO timestamp column —
            # ``game_time`` (REAL seconds since game start) is the only
            # per-step time signal. Stringify so the response shape
            # matches the spec's ``ts: str``.
            "ts": str(game_time),
        })

    return {
        "trajectory": trajectory,
        # No ``give_up`` column in the schema — see docstring.
        "give_up_fired": False,
        "give_up_step": None,
        # Phase O hasn't shipped — see plan §6.8.
        "expert_dispatch": None,
    }


@app.get("/api/versions/{v}/forensics/{game_id}")
async def get_version_forensics(v: str, game_id: str) -> dict[str, Any]:
    """Return per-game replay-style forensics for ``(v, game_id)``.

    Both path params are validated against the strict regex helpers
    (``^v\\d+$`` for ``v`` via :func:`_validate_version`; the
    ``[A-Za-z0-9_-]{1,128}`` pattern for ``game_id`` via
    :func:`_validate_game_id`). Malformed input returns 400 BEFORE any
    DB work happens — the parameterized SQL is safe, but cheap rejection
    is even safer.

    Reads from the per-version ``training.db`` and joins on
    ``games.model_version`` so a duplicate ``game_id`` across two
    version DBs never leaks. Returns the empty skeleton (empty
    trajectory + ``give_up_fired=False`` + null fields) when the game is
    not found. Never 500.
    """
    v = _validate_version(v)
    game_id = _validate_game_id(game_id)
    return await asyncio.to_thread(_forensics_sync, v, game_id)


_WEIGHT_DYNAMICS_FILE = "weight_dynamics.jsonl"


def _weight_dynamics_sync(version: str) -> list[dict[str, Any]]:
    """Read ``data/weight_dynamics.jsonl`` and filter rows by ``version``.

    Each row is one diagnostic write per checkpoint — see plan §5 for
    the schema. Both success and failure rows are surfaced; the
    Inspector renders failure rows (non-null ``error``) as red dots
    (Step 6's job, not this endpoint's). Lines that fail to parse as
    JSON are skipped + warned about; the endpoint never 500s.

    Returns ``[]`` when the file is absent or has no rows for ``version``.
    """
    path = _cross_version_data_dir() / _WEIGHT_DYNAMICS_FILE
    if not path.is_file():
        return []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    out: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            _log.warning(
                "Skipping malformed weight_dynamics.jsonl line %d: %s",
                index,
                exc,
            )
            continue
        if not isinstance(row, dict):
            continue
        if row.get("version") != version:
            continue
        out.append({
            "checkpoint": str(row.get("checkpoint") or ""),
            "ts": str(row.get("ts") or ""),
            "l2_per_layer": row.get("l2_per_layer"),
            "kl_from_parent": row.get("kl_from_parent"),
            "canary_source": row.get("canary_source"),
            "error": row.get("error"),
        })
    return out


@app.get("/api/versions/{v}/weight-dynamics")
async def get_version_weight_dynamics(v: str) -> list[dict[str, Any]]:
    """Return per-checkpoint weight-dynamics rows for ``v``.

    Reads ``data/weight_dynamics.jsonl`` from the cross-version data
    dir, filters rows where ``version == v``, and emits one entry per
    line in file order (which is checkpoint-write order). The shape::

        {checkpoint, ts, l2_per_layer, kl_from_parent, canary_source, error}

    Both success rows (everything populated, ``error=None``) and failure
    rows (``l2/kl/canary`` all null, ``error="<class>: <msg>"``) are
    surfaced — the Inspector decides how to render them. Returns ``[]``
    when the file is missing or has no rows for ``v``. Never 500.
    """
    v = _validate_version(v)
    return await asyncio.to_thread(_weight_dynamics_sync, v)


def _run_build_lineage_sync() -> None:
    """Invoke ``scripts/build_lineage.py`` synchronously, swallow failures.

    Lazy-init helper for ``/api/lineage`` — runs off the event loop via
    ``asyncio.to_thread``. Subprocess uses list-form + ``shell=False``
    + a 60s wall-clock cap; every failure mode (non-zero exit, timeout,
    OSError) logs a warning and returns silently so the endpoint can
    always fall back to its empty skeleton without surfacing a 500 to
    the dashboard.
    """
    script = _REPO_ROOT / "scripts" / "build_lineage.py"
    if not script.is_file():
        _log.warning(
            "lineage lazy-init: build_lineage.py missing at %s — "
            "falling back to empty skeleton",
            script,
        )
        return
    try:
        result = subprocess.run(  # noqa: S603 — list-form, shell=False
            [sys.executable, str(script)],
            shell=False,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _log.warning("lineage lazy-init: build_lineage.py timed out after 60s")
        return
    except OSError as exc:
        _log.warning("lineage lazy-init: build_lineage.py failed to launch: %s", exc)
        return
    if result.returncode != 0:
        _log.warning(
            "lineage lazy-init: build_lineage.py exit %d (stderr=%r)",
            result.returncode,
            (result.stderr or "")[:500],
        )


@app.get("/api/lineage")
async def get_lineage() -> dict[str, Any]:
    """Return the persisted lineage DAG.

    Reads ``data/lineage.json`` from the cross-version data dir. On a
    cache miss (file missing OR malformed) the endpoint runs
    ``scripts/build_lineage.py`` once under a process-wide
    :class:`asyncio.Lock` (double-checked locking — the existence
    check is repeated inside the lock so two concurrent first-time
    requests don't both spawn the build). On subprocess failure the
    endpoint falls back to ``{"nodes": [], "edges": []}`` rather than
    surfacing a 500 — this matches the policy applied to every other
    Models-tab endpoint.

    Subsequent requests hit the parsed file directly (~10ms cost).

    Uses :func:`_cross_version_data_dir` — lineage is cross-version
    state. See module-level note at line ~1120.
    """
    path = _cross_version_data_dir() / "lineage.json"
    payload = _read_json_file(path)
    if payload is None:
        # Cache miss — acquire the lazy-init lock and re-check inside
        # the lock so two concurrent first-time requests don't both
        # invoke the build script.
        lock = _get_lineage_lazy_init_lock()
        async with lock:
            payload = _read_json_file(path)
            if payload is None:
                await asyncio.to_thread(_run_build_lineage_sync)
                payload = _read_json_file(path)
        if payload is None:
            # Build script failed (logged inside _run_build_lineage_sync).
            return {"nodes": [], "edges": []}
    # Defensive: if the file exists but is missing one of the keys,
    # backfill with empty lists so the frontend can always
    # destructure ``{nodes, edges}``.
    if "nodes" not in payload:
        payload["nodes"] = []
    if "edges" not in payload:
        payload["edges"] = []
    return payload


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
# ``_REPO_ROOT`` is defined above in the Models tab section so the
# version-resolver helpers can use it at import time. Same value either
# way; kept here as a comment for code locality.


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
