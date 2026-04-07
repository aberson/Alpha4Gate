"""Unit tests for WebSocket connection management and broadcasting."""

from __future__ import annotations

from alpha4gate.web_socket import drain_broadcast_queue, queue_broadcast


class TestQueueBroadcast:
    def test_queue_and_drain(self) -> None:
        # Drain any leftover from previous tests
        drain_broadcast_queue()

        entry = {"game_step": 100, "minerals": 350}
        queue_broadcast(entry)
        entries = drain_broadcast_queue()
        assert len(entries) == 1
        assert entries[0]["game_step"] == 100

    def test_drain_empty(self) -> None:
        # Drain any leftover
        drain_broadcast_queue()
        entries = drain_broadcast_queue()
        assert entries == []

    def test_multiple_entries(self) -> None:
        drain_broadcast_queue()
        queue_broadcast({"step": 1})
        queue_broadcast({"step": 2})
        queue_broadcast({"step": 3})
        entries = drain_broadcast_queue()
        assert len(entries) == 3


class TestBroadcastPipelineFields:
    """Verify full pipeline: queue_broadcast → drain_broadcast_queue with all game state fields."""

    def test_all_game_state_fields_round_trip(self) -> None:
        drain_broadcast_queue()

        entry = {
            "game_time_seconds": 120,
            "minerals": 450,
            "vespene": 200,
            "supply_used": 34,
            "supply_cap": 46,
            "units": ["Marine", "Medivac", "SCV"],
            "strategic_state": "expanding",
        }
        queue_broadcast(entry)
        entries = drain_broadcast_queue()

        assert len(entries) == 1
        result = entries[0]
        assert result["game_time_seconds"] == 120
        assert result["minerals"] == 450
        assert result["vespene"] == 200
        assert result["supply_used"] == 34
        assert result["supply_cap"] == 46
        assert result["units"] == ["Marine", "Medivac", "SCV"]
        assert result["strategic_state"] == "expanding"


class TestLifespanRegistered:
    """Verify the lifespan handler is registered on the FastAPI app."""

    def test_lifespan_context_is_set(self) -> None:
        from alpha4gate.api import app

        assert app.router.lifespan_context is not None


class TestWSGameEndpointBroadcast:
    """Integration test: queue_broadcast → _drain_and_broadcast_once → WS client receives."""

    def test_ws_client_receives_broadcast(self) -> None:
        import asyncio
        from pathlib import Path

        from fastapi.testclient import TestClient

        from alpha4gate.api import _drain_and_broadcast_once, app, configure

        # Configure with temp dirs so app doesn't complain
        configure(Path("data"), Path("logs"), Path("replays"))

        # Drain leftover
        drain_broadcast_queue()

        client = TestClient(app)
        sample = {
            "game_time_seconds": 42.0,
            "minerals": 300,
            "vespene": 100,
            "supply_used": 20,
            "supply_cap": 30,
            "units": ["Stalker"],
            "strategic_state": "attack",
        }

        with client.websocket_connect("/ws/game") as ws:
            # Queue a broadcast entry from the "game thread"
            queue_broadcast(sample)

            # Drain and broadcast (what the lifespan loop does each tick)
            loop = asyncio.new_event_loop()
            try:
                count = loop.run_until_complete(_drain_and_broadcast_once())
            finally:
                loop.close()

            assert count == 1

            # The WS client should have received the message
            data = ws.receive_json()
            assert data["game_time_seconds"] == 42.0
            assert data["minerals"] == 300
            assert data["strategic_state"] == "attack"
