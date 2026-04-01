"""WebSocket endpoint for live game state and decision streaming."""

from __future__ import annotations

import json
from queue import Empty, Queue
from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    """Manages WebSocket connections and broadcasts."""

    def __init__(self) -> None:
        self._game_connections: list[WebSocket] = []
        self._decision_connections: list[WebSocket] = []
        self._command_connections: list[WebSocket] = []

    async def connect_game(self, websocket: WebSocket) -> None:
        """Accept a game state WebSocket connection."""
        await websocket.accept()
        self._game_connections.append(websocket)

    async def connect_decisions(self, websocket: WebSocket) -> None:
        """Accept a decision stream WebSocket connection."""
        await websocket.accept()
        self._decision_connections.append(websocket)

    def disconnect_game(self, websocket: WebSocket) -> None:
        """Remove a game state WebSocket connection."""
        if websocket in self._game_connections:
            self._game_connections.remove(websocket)

    def disconnect_decisions(self, websocket: WebSocket) -> None:
        """Remove a decision stream WebSocket connection."""
        if websocket in self._decision_connections:
            self._decision_connections.remove(websocket)

    async def broadcast_game_state(self, data: dict[str, Any]) -> None:
        """Broadcast game state to all connected game clients."""
        message = json.dumps(data)
        disconnected: list[WebSocket] = []
        for ws in self._game_connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect_game(ws)

    async def broadcast_decision(self, data: dict[str, Any]) -> None:
        """Broadcast a decision event to all connected decision clients."""
        message = json.dumps(data)
        disconnected: list[WebSocket] = []
        for ws in self._decision_connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect_decisions(ws)

    async def connect_commands(self, websocket: WebSocket) -> None:
        """Accept a command stream WebSocket connection."""
        await websocket.accept()
        self._command_connections.append(websocket)

    def disconnect_commands(self, websocket: WebSocket) -> None:
        """Remove a command stream WebSocket connection."""
        if websocket in self._command_connections:
            self._command_connections.remove(websocket)

    async def broadcast_command_event(self, data: dict[str, Any]) -> None:
        """Broadcast a command event to all connected command clients."""
        message = json.dumps(data)
        disconnected: list[WebSocket] = []
        for ws in self._command_connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect_commands(ws)

    @property
    def command_connection_count(self) -> int:
        """Number of active command stream connections."""
        return len(self._command_connections)

    @property
    def game_connection_count(self) -> int:
        """Number of active game state connections."""
        return len(self._game_connections)

    @property
    def decision_connection_count(self) -> int:
        """Number of active decision stream connections."""
        return len(self._decision_connections)


# Thread-safe queue for broadcasting from the game loop thread to the async web server
_broadcast_queue: Queue[dict[str, Any]] = Queue()

# Thread-safe queue for command execution results (bot thread → async web server)
_command_event_queue: Queue[dict[str, Any]] = Queue()


def queue_broadcast(entry: dict[str, Any]) -> None:
    """Enqueue a game state entry for WebSocket broadcast.

    Thread-safe: called from the game logger's background thread.
    """
    _broadcast_queue.put(entry)


def queue_command_event(event: dict[str, Any]) -> None:
    """Enqueue a command execution event for WebSocket broadcast.

    Thread-safe: called from the bot's game loop thread after command execution.
    """
    _command_event_queue.put(event)


def drain_broadcast_queue() -> list[dict[str, Any]]:
    """Drain all pending broadcast entries from the queue.

    Returns:
        List of entries to broadcast.
    """
    entries: list[dict[str, Any]] = []
    while True:
        try:
            entries.append(_broadcast_queue.get_nowait())
        except Empty:
            break
    return entries


def drain_command_event_queue() -> list[dict[str, Any]]:
    """Drain all pending command execution events from the queue.

    Returns:
        List of command events to broadcast.
    """
    entries: list[dict[str, Any]] = []
    while True:
        try:
            entries.append(_command_event_queue.get_nowait())
        except Empty:
            break
    return entries
