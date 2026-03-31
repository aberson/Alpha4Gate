"""FastAPI REST endpoints for the Alpha4Gate dashboard."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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
from alpha4gate.web_socket import ConnectionManager, drain_broadcast_queue

ws_manager = ConnectionManager()

# These are set at startup by the runner
_data_dir: Path = Path("data")
_log_dir: Path = Path("logs")
_replay_dir: Path = Path("replays")

# Command system state
_command_history: list[dict[str, Any]] = []
_interpreter: CommandInterpreter | None = None
_parser = StructuredParser()


def configure(
    data_dir: Path,
    log_dir: Path,
    replay_dir: Path,
    api_key: str = "",
) -> None:
    """Configure directory paths for the API.

    Called by the runner at startup.
    """
    global _data_dir, _log_dir, _replay_dir, _interpreter
    _data_dir = data_dir
    _log_dir = log_dir
    _replay_dir = replay_dir
    if api_key:
        _interpreter = CommandInterpreter(api_key)


async def _drain_and_broadcast_once() -> int:
    """Drain the broadcast queue and push entries to WebSocket clients.

    Returns the number of entries broadcast.
    """
    entries = drain_broadcast_queue()
    for entry in entries:
        await ws_manager.broadcast_game_state(entry)
    return len(entries)


async def _game_state_broadcast_loop() -> None:
    """Drain the thread-safe broadcast queue and push to WebSocket clients."""
    while True:
        await _drain_and_broadcast_once()
        await asyncio.sleep(0.5)


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Start the game-state broadcast loop on startup."""
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
    """Get the decision audit log."""
    path = _data_dir / "decision_audit.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"entries": data.get("entries", [])}
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

    status: dict[str, Any] = {
        "training_active": False,
        "current_checkpoint": None,
        "total_checkpoints": 0,
        "total_games": 0,
        "total_transitions": 0,
        "db_size_bytes": 0,
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

    return status


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
    """Start training (placeholder — actual training runs via CLI)."""
    mode = request.get("mode", "rl")
    return {
        "status": "accepted",
        "mode": mode,
        "message": f"Use CLI: uv run python -m alpha4gate.runner --train {mode}",
    }


@app.post("/api/training/stop")
async def stop_training() -> dict[str, Any]:
    """Stop training (placeholder — training runs in separate process)."""
    return {"status": "not_running", "message": "Training is managed via CLI process"}


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
