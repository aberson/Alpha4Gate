"""Tests for cross-version promotion gate (Step 4.3).

All tests mock ``selfplay.run_batch`` and ``snapshot.snapshot_current``
to avoid requiring SC2.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.contracts import PromotionResult, SelfPlayRecord
from orchestrator.ladder import check_promotion

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_records(
    p1: str, p2: str, games: int, winner: str | None
) -> list[SelfPlayRecord]:
    """Generate *games* records where *winner* wins every game.

    Pass ``winner=None`` for draws.
    """
    return [
        SelfPlayRecord(
            match_id=f"g{i}",
            p1_version=p1,
            p2_version=p2,
            winner=winner,
            map_name="Simple64",
            duration_s=30.0,
            seat_swap=False,
            timestamp="2026-04-17T00:00:00",
            error=None,
        )
        for i in range(games)
    ]


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestCheckPromotion:
    """Cross-version promotion gate."""

    def test_elo_gain_above_threshold_promotes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Candidate wins all games -> elo_delta > 10 -> promoted."""
        records = _make_records("candidate", "parent", 10, "candidate")
        monkeypatch.setattr(
            "orchestrator.selfplay.run_batch", lambda *a, **kw: records
        )
        snapshot_called = False

        def _mock_snapshot(*a: object, **kw: object) -> Path:
            nonlocal snapshot_called
            snapshot_called = True
            return Path("bots/v99")

        monkeypatch.setattr(
            "orchestrator.snapshot.snapshot_current", _mock_snapshot
        )
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )

        result = check_promotion("candidate", "parent", 10)

        assert isinstance(result, PromotionResult)
        assert result.promoted is True
        assert result.elo_delta > 10.0
        assert result.candidate == "candidate"
        assert result.parent == "parent"
        assert result.games_played == 10
        assert result.wr_vs_sc2 is None
        assert "promoted" in result.reason
        assert snapshot_called

    def test_elo_gain_below_threshold_not_promoted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Parent wins all games -> negative elo_delta -> not promoted."""
        records = _make_records("candidate", "parent", 10, "parent")
        monkeypatch.setattr(
            "orchestrator.selfplay.run_batch", lambda *a, **kw: records
        )
        snapshot_called = False

        def _mock_snapshot(*a: object, **kw: object) -> Path:
            nonlocal snapshot_called
            snapshot_called = True
            return Path("bots/v99")

        monkeypatch.setattr(
            "orchestrator.snapshot.snapshot_current", _mock_snapshot
        )
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )

        result = check_promotion("candidate", "parent", 10)

        assert result.promoted is False
        assert result.elo_delta < 0
        assert "not promoted" in result.reason
        assert not snapshot_called

    def test_exactly_at_threshold_promotes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Edge case: elo_delta == threshold -> promoted (>=, not >)."""
        # We need to find a game count where the Elo delta lands exactly
        # on the threshold. Instead, use a low threshold that a single win
        # exceeds, then set threshold to match the computed delta.
        records = _make_records("candidate", "parent", 1, "candidate")
        monkeypatch.setattr(
            "orchestrator.selfplay.run_batch", lambda *a, **kw: records
        )
        monkeypatch.setattr(
            "orchestrator.snapshot.snapshot_current",
            lambda *a, **kw: Path("bots/v99"),
        )
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )

        # First run to discover the exact delta for 1 win from even Elo.
        probe = check_promotion(
            "candidate", "parent", 1, elo_threshold=0.0
        )
        exact_delta = probe.elo_delta

        # Now run again with threshold == exact_delta.
        result = check_promotion(
            "candidate", "parent", 1, elo_threshold=exact_delta
        )
        assert result.promoted is True
        assert result.elo_delta == pytest.approx(exact_delta)

    def test_all_draws_not_promoted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All draws -> elo_delta ~ 0 -> not promoted."""
        records = _make_records("candidate", "parent", 10, None)
        monkeypatch.setattr(
            "orchestrator.selfplay.run_batch", lambda *a, **kw: records
        )
        snapshot_called = False

        def _mock_snapshot(*a: object, **kw: object) -> Path:
            nonlocal snapshot_called
            snapshot_called = True
            return Path("bots/v99")

        monkeypatch.setattr(
            "orchestrator.snapshot.snapshot_current", _mock_snapshot
        )
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )

        result = check_promotion("candidate", "parent", 10)

        assert result.promoted is False
        assert result.elo_delta == pytest.approx(0.0, abs=0.1)
        assert not snapshot_called

    def test_configurable_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Custom elo_threshold=5.0 allows promotion with smaller gains."""
        # 2 wins from even Elo gives ~32 delta — well above 5.0.
        records = _make_records("candidate", "parent", 2, "candidate")
        monkeypatch.setattr(
            "orchestrator.selfplay.run_batch", lambda *a, **kw: records
        )
        monkeypatch.setattr(
            "orchestrator.snapshot.snapshot_current",
            lambda *a, **kw: Path("bots/v99"),
        )
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )

        result = check_promotion(
            "candidate", "parent", 2, elo_threshold=5.0
        )

        assert result.promoted is True
        assert result.elo_delta >= 5.0

    def test_promoted_calls_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify snapshot_current is called exactly once on promotion."""
        records = _make_records("candidate", "parent", 10, "candidate")
        monkeypatch.setattr(
            "orchestrator.selfplay.run_batch", lambda *a, **kw: records
        )
        call_count = 0

        def _mock_snapshot(*a: object, **kw: object) -> Path:
            nonlocal call_count
            call_count += 1
            return Path("bots/v99")

        monkeypatch.setattr(
            "orchestrator.snapshot.snapshot_current", _mock_snapshot
        )
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )

        result = check_promotion("candidate", "parent", 10)

        assert result.promoted is True
        assert call_count == 1

    def test_not_promoted_does_not_call_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify snapshot_current is NOT called when not promoted."""
        records = _make_records("candidate", "parent", 10, "parent")
        monkeypatch.setattr(
            "orchestrator.selfplay.run_batch", lambda *a, **kw: records
        )
        call_count = 0

        def _mock_snapshot(*a: object, **kw: object) -> Path:
            nonlocal call_count
            call_count += 1
            return Path("bots/v99")

        monkeypatch.setattr(
            "orchestrator.snapshot.snapshot_current", _mock_snapshot
        )
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )

        result = check_promotion("candidate", "parent", 10)

        assert result.promoted is False
        assert call_count == 0
