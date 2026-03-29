"""Unit tests for WebSocket connection management and broadcasting."""

from __future__ import annotations

from alpha4gate.web_socket import ConnectionManager, drain_broadcast_queue, queue_broadcast


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


class TestConnectionManager:
    def test_initial_counts(self) -> None:
        mgr = ConnectionManager()
        assert mgr.game_connection_count == 0
        assert mgr.decision_connection_count == 0
