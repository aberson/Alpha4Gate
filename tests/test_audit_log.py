"""Tests for the decision audit log writer."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from alpha4gate import audit_log
from alpha4gate.audit_log import DECISION_AUDIT_FILENAME, record_decision


def _make_ws_manager() -> MagicMock:
    """Return a ConnectionManager-shaped mock with async broadcast_decision."""
    mgr = MagicMock()
    mgr.broadcast_decision = AsyncMock()
    return mgr


def _sample_decision() -> dict[str, Any]:
    return {
        "timestamp": "2026-04-10T12:00:00+00:00",
        "source": "claude_advisor",
        "model": "sonnet",
        "game_time": 120.5,
        "request_summary": "bot state at game_time=120.5",
        "response_commands": [
            {"action": "build", "target": "gateway", "location": "main", "priority": 7}
        ],
        "suggestion": "Add production",
        "urgency": "medium",
        "reasoning": "Need more warpgate capacity",
    }


class TestRecordDecisionFileWrite:
    def test_bootstrap_creates_file_when_missing(self, tmp_path: Path) -> None:
        ws = _make_ws_manager()
        decision = _sample_decision()

        async def run() -> None:
            record_decision(tmp_path, ws, decision)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()

        audit_path = tmp_path / DECISION_AUDIT_FILENAME
        assert audit_path.exists()
        payload = json.loads(audit_path.read_text(encoding="utf-8"))
        assert payload == {"entries": [decision]}

    def test_appends_to_existing_entries(self, tmp_path: Path) -> None:
        audit_path = tmp_path / DECISION_AUDIT_FILENAME
        existing = {"entries": [{"timestamp": "old", "suggestion": "first"}]}
        audit_path.write_text(json.dumps(existing), encoding="utf-8")

        ws = _make_ws_manager()
        decision = _sample_decision()

        async def run() -> None:
            record_decision(tmp_path, ws, decision)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()

        payload = json.loads(audit_path.read_text(encoding="utf-8"))
        assert len(payload["entries"]) == 2
        assert payload["entries"][0]["suggestion"] == "first"
        assert payload["entries"][1] == decision

    def test_schema_matches_api_reader(self, tmp_path: Path) -> None:
        """Writer output must match the shape api.py:get_decision_log reads."""
        ws = _make_ws_manager()
        decision = _sample_decision()

        async def run() -> None:
            record_decision(tmp_path, ws, decision)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()

        audit_path = tmp_path / DECISION_AUDIT_FILENAME
        data = json.loads(audit_path.read_text(encoding="utf-8"))
        # api.py does: data.get("entries", []) and returns {"entries": [...]}
        assert "entries" in data
        assert isinstance(data["entries"], list)
        assert data["entries"][-1] == decision


class TestRecordDecisionBroadcast:
    def test_broadcast_scheduled_when_loop_running(self, tmp_path: Path) -> None:
        ws = _make_ws_manager()
        decision = _sample_decision()

        async def run() -> None:
            record_decision(tmp_path, ws, decision)
            # Let the scheduled broadcast task run.
            await asyncio.sleep(0)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()
        ws.broadcast_decision.assert_called_once_with(decision)

    def test_broadcast_skipped_without_ws_manager(self, tmp_path: Path) -> None:
        decision = _sample_decision()

        async def run() -> None:
            record_decision(tmp_path, None, decision)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()

        audit_path = tmp_path / DECISION_AUDIT_FILENAME
        assert audit_path.exists()
        payload = json.loads(audit_path.read_text(encoding="utf-8"))
        assert payload == {"entries": [decision]}

    def test_broadcast_skipped_without_running_loop(
        self, tmp_path: Path
    ) -> None:
        """Called from a sync context: file still written, no broadcast."""
        ws = _make_ws_manager()
        decision = _sample_decision()

        # Called with no running event loop.
        record_decision(tmp_path, ws, decision)

        audit_path = tmp_path / DECISION_AUDIT_FILENAME
        payload = json.loads(audit_path.read_text(encoding="utf-8"))
        assert payload == {"entries": [decision]}
        ws.broadcast_decision.assert_not_called()


class TestRecordDecisionCorruptJson:
    def test_corrupt_file_is_rotated_and_fresh_entry_written(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        audit_path = tmp_path / DECISION_AUDIT_FILENAME
        audit_path.write_text("{not valid json", encoding="utf-8")

        ws = _make_ws_manager()
        decision = _sample_decision()

        with caplog.at_level(logging.WARNING, logger="alpha4gate.audit_log"):
            async def run() -> None:
                record_decision(tmp_path, ws, decision)

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(run())
            finally:
                loop.close()

        # Fresh file exists with the new decision only.
        payload = json.loads(audit_path.read_text(encoding="utf-8"))
        assert payload == {"entries": [decision]}

        # A corrupt-rotated file exists alongside the fresh one.
        corrupt_files = list(tmp_path.glob("decision_audit.corrupt.*.json"))
        assert len(corrupt_files) == 1
        assert corrupt_files[0].read_text(encoding="utf-8") == "{not valid json"

        # Warning was logged.
        assert "corrupt" in caplog.text.lower()

    def test_non_dict_top_level_starts_fresh(self, tmp_path: Path) -> None:
        audit_path = tmp_path / DECISION_AUDIT_FILENAME
        audit_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")

        ws = _make_ws_manager()
        decision = _sample_decision()

        async def run() -> None:
            record_decision(tmp_path, ws, decision)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()

        payload = json.loads(audit_path.read_text(encoding="utf-8"))
        assert payload == {"entries": [decision]}


class TestBroadcastTaskLifecycle:
    """Regression tests for the GC-resistance fix.

    The asyncio event loop only holds weak references to tasks created via
    ``loop.create_task``. Without an external strong reference, a fire-and-
    forget broadcast task could be garbage-collected mid-execution. The module
    now keeps a ``_pending_broadcasts`` set with an ``add_done_callback`` that
    removes entries on completion -- tests here pin that contract.
    """

    def test_pending_broadcasts_holds_strong_ref_then_clears(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Isolate module state per test so cross-test leaks can't mask a
        # regression.
        fresh: set[asyncio.Task[None]] = set()
        monkeypatch.setattr(audit_log, "_pending_broadcasts", fresh)

        # ws_manager.broadcast_decision waits on an Event so we can observe
        # the task *while* it's in flight (before it completes). Without this,
        # the task could transition to done before we inspect the set.
        gate = asyncio.Event()

        async def slow_broadcast(decision: dict[str, Any]) -> None:
            await gate.wait()

        ws = MagicMock()
        ws.broadcast_decision = AsyncMock(side_effect=slow_broadcast)
        decision = _sample_decision()

        async def run() -> None:
            record_decision(tmp_path, ws, decision)
            # While broadcast is still awaiting the gate, the task must be in
            # the pending set (strong reference held).
            assert len(fresh) == 1
            task = next(iter(fresh))
            assert not task.done()
            # Release the gate and let the task complete.
            gate.set()
            await task
            # Yield so scheduled done-callbacks (call_soon) run before we
            # inspect the set.
            await asyncio.sleep(0)
            assert len(fresh) == 0

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()

        ws.broadcast_decision.assert_called_once_with(decision)

    def test_broadcast_exception_is_logged_via_done_callback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fresh: set[asyncio.Task[None]] = set()
        monkeypatch.setattr(audit_log, "_pending_broadcasts", fresh)

        async def broken_broadcast(decision: dict[str, Any]) -> None:
            raise RuntimeError("boom")

        ws = MagicMock()
        ws.broadcast_decision = AsyncMock(side_effect=broken_broadcast)
        decision = _sample_decision()

        async def run() -> None:
            record_decision(tmp_path, ws, decision)
            assert len(fresh) == 1
            task = next(iter(fresh))
            # Await the task; the exception should be retrieved by the
            # done-callback, not propagate out and not surface as an
            # "exception was never retrieved" warning at GC time.
            try:
                await task
            except RuntimeError:
                # The awaiter still sees the exception; the point is the
                # done-callback also calls ``task.exception()`` to log it.
                pass
            # Yield so scheduled done-callbacks (call_soon) run before we
            # inspect the set.
            await asyncio.sleep(0)
            assert len(fresh) == 0

        with caplog.at_level(logging.ERROR, logger="alpha4gate.audit_log"):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(run())
            finally:
                loop.close()

        # The done-callback must have called _log.error with the identifying
        # message; this is the signal that a dropped broadcast is visible
        # instead of silently lost.
        assert "broadcast_decision task failed" in caplog.text
        assert "boom" in caplog.text
