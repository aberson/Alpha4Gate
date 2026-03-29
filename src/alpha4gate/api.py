"""FastAPI REST endpoints for the Alpha4Gate dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from alpha4gate.web_socket import ConnectionManager

app = FastAPI(title="Alpha4Gate", version="0.1.0")
ws_manager = ConnectionManager()

# These are set at startup by the runner
_data_dir: Path = Path("data")
_log_dir: Path = Path("logs")
_replay_dir: Path = Path("replays")


def configure(data_dir: Path, log_dir: Path, replay_dir: Path) -> None:
    """Configure directory paths for the API.

    Called by the runner at startup.
    """
    global _data_dir, _log_dir, _replay_dir
    _data_dir = data_dir
    _log_dir = log_dir
    _replay_dir = replay_dir


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


@app.websocket("/ws/decisions")
async def ws_decisions(websocket: WebSocket) -> None:
    """WebSocket endpoint for live decision events."""
    await ws_manager.connect_decisions(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect_decisions(websocket)
