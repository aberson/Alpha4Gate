"""Unit tests for WebSocket connection management and broadcasting."""

from __future__ import annotations

from bots.v0.web_socket import drain_broadcast_queue, queue_broadcast


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
        from bots.v0.api import app

        assert app.router.lifespan_context is not None


# Dashboard refactor Step 6 retired ``/ws/game`` along with the
# ``_drain_and_broadcast_once`` helper; the queue-drain machinery is now
# silent (the new lifespan loop drains queues into /dev/null to keep the
# bot-thread queues bounded — there is no surviving WS client for an
# integration test to hook into).
