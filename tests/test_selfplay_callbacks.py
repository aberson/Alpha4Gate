"""Tests for Step 4 ``run_batch`` callback behaviour.

Covers:

- Callback fire ORDER across a multi-game batch (``start`` always
  precedes ``end`` for the same game; games are serialized).
- Callback exception isolation on both ``on_game_start`` and
  ``on_game_end`` — one thrown exception must not abort the batch.
- PID-discovery timeout → callback fires with ``(-1, -1)`` and logs
  a warning.
- Fast game crash: ``pid_task`` is cancelled so the callback does not
  block on a 15s poll when the game dies before PIDs appear.
- ``_wait_for_sc2_pids`` poll-loop unit behaviour (descendant filter
  intersection, fallback on descendant-walk failure).
- Cooperative ``stop_event`` cancellation between games.

SC2 / burnysc2 / psutil are all mocked. These tests are pure Python
and do not require a Windows host or an SC2 install. Back-compat for
the ``None`` callback path is already covered by
``tests/test_selfplay.py::TestRunBatch::test_batch_produces_correct_count``;
FIFO/dup-shape semantics of the underlying ``queue.Queue`` are stdlib
guarantees and not re-tested here.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.contracts import SelfPlayRecord
from orchestrator.selfplay import run_batch

# ---------------------------------------------------------------------------
# Fixtures (mirror test_selfplay.py's ``tmp_repo`` for registry isolation)
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Minimal fake repo with v0, v1 versions registered."""
    for v in ("v0", "v1"):
        d = tmp_path / "bots" / v
        d.mkdir(parents=True)
        (d / "VERSION").write_text(v)
    cur = tmp_path / "bots" / "current"
    cur.mkdir(parents=True)
    (cur / "current.txt").write_text("v0")

    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()

    monkeypatch.setattr("orchestrator.registry._repo_root", lambda: tmp_path)
    monkeypatch.setattr("orchestrator.selfplay._repo_root", lambda: tmp_path)

    return tmp_path


@pytest.fixture()
def patch_sc2_layer(monkeypatch: pytest.MonkeyPatch):
    """Centralize the SC2 / psutil mocks that every callback test needs.

    Yields a small handle the test can customise. ``_sc2_pid_snapshot``
    side_effect defaults to repeated ``(set(), {fake_pair})`` pairs so
    ``_wait_for_sc2_pids`` returns a deterministic ``(1001, 1002)`` on
    the first game. Tests that need per-game variance override
    ``mock_snap.side_effect`` before calling ``run_batch``.
    """
    import orchestrator.selfplay as _sp

    # Default: always return the same (before -> after) sequence per game.
    mock_snap = MagicMock(
        side_effect=[
            set(), {1001, 1002},
            set(), {2001, 2002},
            set(), {3001, 3002},
            set(), {4001, 4002},
        ]
    )
    mock_build = MagicMock(
        side_effect=[
            (MagicMock(), False),
            (MagicMock(), True),
            (MagicMock(), False),
            (MagicMock(), True),
        ]
    )

    async def _fake_run(_matches: Any) -> list[None]:
        return [None]

    # Descendant filter returns a superset so ``new & descendants`` keeps
    # everything by default. Tests that want to exercise filtering
    # override this.
    def _descendants() -> set[int]:
        return {1001, 1002, 2001, 2002, 3001, 3002, 4001, 4002}

    monkeypatch.setattr(_sp, "_install_port_collision_patch", lambda: None)
    monkeypatch.setattr(_sp, "_build_match", mock_build)
    monkeypatch.setattr(_sp, "_sc2_pid_snapshot", mock_snap)
    monkeypatch.setattr(_sp, "_orchestrator_descendant_pids", _descendants)
    monkeypatch.setattr("sc2.main.a_run_multiple_games", _fake_run)

    class _Handle:
        def __init__(self) -> None:
            self.mock_snap = mock_snap
            self.mock_build = mock_build

    return _Handle()


# ---------------------------------------------------------------------------
# Callback fire-order
# ---------------------------------------------------------------------------


class TestCallbackOrder:
    """Verify callbacks fire in the correct interleaved order."""

    def test_callbacks_fire_in_order(
        self,
        patch_sc2_layer: Any,
        tmp_repo: Path,
    ) -> None:
        """3 games → [start(1), end(1), start(2), end(2), start(3), end(3)].

        The end-side payload records ``record.match_id`` (not a derived
        count) so the interleave check is independent of the ordering
        it's supposed to be validating.
        """
        events: list[tuple[str, Any]] = []

        def on_start(
            idx: int, total: int, p1: int, p2: int, l1: str, l2: str
        ) -> None:
            events.append(("start", idx))

        def on_end(record: SelfPlayRecord) -> None:
            events.append(("end", record.match_id))

        run_batch(
            "v0",
            "v1",
            3,
            "Simple64",
            results_path=tmp_repo / "data" / "selfplay_results.jsonl",
            on_game_start=on_start,
            on_game_end=on_end,
        )

        # Interleave shape first (independent of the index values).
        kinds = [k for k, _ in events]
        assert kinds == ["start", "end", "start", "end", "start", "end"]
        # Then the start-side indices, which we DO control.
        start_indices = [v for k, v in events if k == "start"]
        assert start_indices == [1, 2, 3]
        # End-side match_ids are unique per game (no asserted value —
        # just that we saw 3 distinct ones).
        end_ids = [v for k, v in events if k == "end"]
        assert len(set(end_ids)) == 3


# ---------------------------------------------------------------------------
# Callback exception isolation
# ---------------------------------------------------------------------------


class TestCallbackExceptionIsolation:
    """Callback exceptions must not abort the batch."""

    def test_on_game_start_exception_does_not_abort_batch(
        self,
        patch_sc2_layer: Any,
        tmp_repo: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A raising on_game_start on game 2 still lets games 1/2/3 run."""
        starts: list[int] = []

        def on_start(
            idx: int, total: int, p1: int, p2: int, l1: str, l2: str
        ) -> None:
            starts.append(idx)
            if idx == 2:
                raise RuntimeError("viewer-side bug")

        with caplog.at_level(logging.WARNING, logger="orchestrator.selfplay"):
            records = run_batch(
                "v0",
                "v1",
                3,
                "Simple64",
                results_path=tmp_repo / "data" / "selfplay_results.jsonl",
                on_game_start=on_start,
            )

        assert len(records) == 3
        # All three games recorded a result (no crash).
        assert starts == [1, 2, 3]
        # The exception was logged at WARNING.
        assert any(
            "on_game_start callback raised" in rec.message
            for rec in caplog.records
            if rec.levelno == logging.WARNING
        )

    def test_on_game_end_exception_does_not_abort_batch(
        self,
        patch_sc2_layer: Any,
        tmp_repo: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A raising on_game_end on game 2 still lets games 1/2/3 run."""
        ends: list[int] = []

        def on_start(
            idx: int, total: int, p1: int, p2: int, l1: str, l2: str
        ) -> None:
            pass

        def on_end(record: SelfPlayRecord) -> None:
            ends.append(len(ends) + 1)
            if len(ends) == 2:
                raise RuntimeError("overlay render bug")

        with caplog.at_level(logging.WARNING, logger="orchestrator.selfplay"):
            records = run_batch(
                "v0",
                "v1",
                3,
                "Simple64",
                results_path=tmp_repo / "data" / "selfplay_results.jsonl",
                on_game_start=on_start,
                on_game_end=on_end,
            )

        assert len(records) == 3
        assert ends == [1, 2, 3]
        assert any(
            "on_game_end callback raised" in rec.message
            for rec in caplog.records
            if rec.levelno == logging.WARNING
        )


# ---------------------------------------------------------------------------
# PID discovery: timeout + fast-crash race
# ---------------------------------------------------------------------------


class TestPidDiscoveryBehaviour:
    """Both timeout and fast-crash paths must fire on_game_start once."""

    @patch("orchestrator.selfplay._install_port_collision_patch")
    @patch("orchestrator.selfplay._build_match")
    @patch("orchestrator.selfplay._sc2_pid_snapshot", return_value=set())
    def test_no_new_sc2_fires_with_minus_one_pids(
        self,
        _mock_snap: MagicMock,
        mock_build: MagicMock,
        _mock_patch: MagicMock,
        tmp_repo: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Timeout path: no new SC2 PIDs ever appear → callback gets (-1, -1).

        Either the pid_task times out first (``"SC2 PID discovery timed
        out"``) OR the game_task completes first (``"completed before
        SC2 PIDs were discovered"``) — both paths fire on_game_start
        with ``(-1, -1)`` and log a WARNING. Scheduling variance on
        tight timeouts can produce either ordering; the contract is
        that the callback fires ONCE with sentinel PIDs and a WARNING
        is logged, not which of the two WARNINGs wins the race.
        """
        mock_build.side_effect = [(MagicMock(), False)]

        async def _fake_run(_matches: Any) -> list[None]:
            # Hold the game "running" just long enough for the PID
            # poll to exhaust its (shortened) budget below.
            import asyncio

            await asyncio.sleep(0.2)
            return [None]

        # Shorten the poll budget so the test runs in milliseconds
        # instead of waiting out the real 15s default.
        import orchestrator.selfplay as _sp

        _real_wait = _sp._wait_for_sc2_pids

        async def _shim(
            before: set[int],
            timeout_s: float = 15.0,
            poll_interval_s: float = 0.2,
        ) -> tuple[int, int]:
            return await _real_wait(before, timeout_s=0.02, poll_interval_s=0.01)

        monkeypatch.setattr(_sp, "_wait_for_sc2_pids", _shim)

        captured: list[tuple[int, int]] = []

        def on_start(
            idx: int, total: int, p1: int, p2: int, l1: str, l2: str
        ) -> None:
            captured.append((p1, p2))

        with (
            patch("sc2.main.a_run_multiple_games", side_effect=_fake_run),
            caplog.at_level(logging.WARNING, logger="orchestrator.selfplay"),
        ):
            run_batch(
                "v0",
                "v1",
                1,
                "Simple64",
                results_path=tmp_repo / "data" / "selfplay_results.jsonl",
                on_game_start=on_start,
            )

        assert captured == [(-1, -1)]
        # Either log message is acceptable — both mean "no real PIDs".
        timeout_logged = any(
            "SC2 PID discovery timed out" in rec.message
            for rec in caplog.records
            if rec.levelno == logging.WARNING
        )
        race_logged = any(
            "completed before SC2 PIDs were discovered" in rec.message
            for rec in caplog.records
            if rec.levelno == logging.WARNING
        )
        assert timeout_logged or race_logged, (
            "expected either 'SC2 PID discovery timed out' or "
            "'completed before SC2 PIDs were discovered' at WARNING"
        )

    @patch("orchestrator.selfplay._install_port_collision_patch")
    @patch("orchestrator.selfplay._build_match")
    @patch("orchestrator.selfplay._sc2_pid_snapshot", return_value=set())
    def test_pid_task_cancelled_on_fast_game_crash(
        self,
        _mock_snap: MagicMock,
        mock_build: MagicMock,
        _mock_patch: MagicMock,
        tmp_repo: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Game crashes immediately → callback fires with (-1, -1) fast.

        Without the FIRST_COMPLETED race in
        ``_run_single_game_with_callbacks`` we would wait the full 15s
        pid timeout before firing the callback. Assert wall-clock is
        well under that.
        """
        mock_build.side_effect = [(MagicMock(), False)]

        async def _fake_run_crash(_matches: Any) -> list[None]:
            # Fail fast — simulate burnysc2 aborting during maintain_SCII_count.
            raise RuntimeError("SC2 launcher died instantly")

        captured: list[tuple[int, int]] = []

        def on_start(
            idx: int, total: int, p1: int, p2: int, l1: str, l2: str
        ) -> None:
            captured.append((p1, p2))

        t0 = time.monotonic()
        with (
            patch("sc2.main.a_run_multiple_games", side_effect=_fake_run_crash),
            caplog.at_level(logging.WARNING, logger="orchestrator.selfplay"),
        ):
            records = run_batch(
                "v0",
                "v1",
                1,
                "Simple64",
                results_path=tmp_repo / "data" / "selfplay_results.jsonl",
                on_game_start=on_start,
            )
        elapsed = time.monotonic() - t0

        # Callback fired exactly once with sentinel PIDs — not via the
        # 15s pid-task timeout.
        assert captured == [(-1, -1)]
        # Batch still records the game (crash path).
        assert len(records) == 1
        assert records[0].error is not None
        # Wall clock must be well under the 15s pid timeout. Allow a
        # generous margin for CI variance.
        assert elapsed < 5.0, (
            f"game crash waited {elapsed:.1f}s — pid_task was not cancelled"
        )


# ---------------------------------------------------------------------------
# _wait_for_sc2_pids poll-loop direct unit tests
# ---------------------------------------------------------------------------


class TestWaitForSc2Pids:
    """Direct tests of ``_wait_for_sc2_pids`` poll loop + descendant filter."""

    def test_returns_descendant_filtered_pair(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``new`` intersected with descendants gives the right pair."""
        import asyncio

        import orchestrator.selfplay as _sp

        # First call: no SC2 yet. Second call: 3 PIDs appear but only
        # 2 are descendants of our process.
        calls: list[set[int]] = [set(), {100, 200, 300}]

        def _snap() -> set[int]:
            return calls.pop(0) if calls else {100, 200, 300}

        monkeypatch.setattr(_sp, "_sc2_pid_snapshot", _snap)
        # PID 300 is a user-spawned SC2 outside our tree.
        monkeypatch.setattr(
            _sp, "_orchestrator_descendant_pids", lambda: {100, 200}
        )

        pair = asyncio.run(
            _sp._wait_for_sc2_pids(
                before=set(), timeout_s=1.0, poll_interval_s=0.01
            )
        )
        assert pair == (100, 200)

    def test_fallback_when_descendant_walk_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Descendant-walk returns None → sort all new PIDs, take lowest two."""
        import asyncio

        import orchestrator.selfplay as _sp

        monkeypatch.setattr(
            _sp, "_sc2_pid_snapshot", lambda: {500, 100, 300, 200}
        )
        monkeypatch.setattr(_sp, "_orchestrator_descendant_pids", lambda: None)

        pair = asyncio.run(
            _sp._wait_for_sc2_pids(
                before=set(), timeout_s=1.0, poll_interval_s=0.01
            )
        )
        assert pair == (100, 200)


# ---------------------------------------------------------------------------
# Viewer-side: on_game_start / on_game_end enqueue the right event shapes
# ---------------------------------------------------------------------------


class TestViewerEventQueue:
    """Smoke-test the (event_type, payload) encoding pushed onto the queue."""

    def test_enqueued_event_shapes(self) -> None:
        """Both callbacks push the expected tuple shapes onto the queue."""
        pytest.importorskip(
            "selfplay_viewer", reason="selfplay_viewer import guard"
        )
        from selfplay_viewer import SelfPlayViewer

        viewer = SelfPlayViewer()
        viewer.on_game_start(1, 10, 1001, 1002, "v0", "v1")
        record = SelfPlayRecord(
            match_id="abc",
            p1_version="v0",
            p2_version="v1",
            winner="v0",
            map_name="Simple64",
            duration_s=22.5,
            seat_swap=False,
            timestamp="2026-04-18T12:00:00+00:00",
        )
        viewer.on_game_end(record)

        event_type, payload = viewer._event_queue.get_nowait()
        assert event_type == "game_start"
        assert payload == (1, 10, 1001, 1002, "v0", "v1")

        event_type, payload = viewer._event_queue.get_nowait()
        assert event_type == "game_end"
        assert payload == (record,)


# ---------------------------------------------------------------------------
# Cooperative cancellation
# ---------------------------------------------------------------------------


class TestStopEvent:
    """``stop_event`` must break the batch loop between games."""

    def test_stop_event_breaks_loop(
        self,
        patch_sc2_layer: Any,
        tmp_repo: Path,
    ) -> None:
        """Set the event after game 1 → batch returns only 1 record."""
        stop_event = threading.Event()

        def on_end(record: SelfPlayRecord) -> None:
            # Trigger cooperative cancellation AFTER the first game.
            stop_event.set()

        records = run_batch(
            "v0",
            "v1",
            3,
            "Simple64",
            results_path=tmp_repo / "data" / "selfplay_results.jsonl",
            on_game_end=on_end,
            stop_event=stop_event,
        )

        # Only game 1 ran; games 2 and 3 were skipped at the inter-game
        # checkpoint.
        assert len(records) == 1
