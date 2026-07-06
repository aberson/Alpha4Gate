"""Tests for ``orchestrator.gate_stats`` — one-sided posterior rollback gate.

Pins the exact closed-form posterior against a hand-derived oracle table and
exercises the fail-open / rollback verdicts of :func:`one_sided_rollback`. The
constants are imported (never re-literalled) so a future retune of MIN_DECIDED
or ROLLBACK_THRESHOLD is caught here in one place.
"""

from __future__ import annotations

import inspect

import pytest

from orchestrator.gate_stats import (
    MIN_DECIDED,
    ROLLBACK_THRESHOLD,
    one_sided_rollback,
    posterior_prob_worse,
)

_TOL = 1e-9


class TestPosteriorProbWorse:
    @pytest.mark.parametrize(
        ("wins", "losses", "expected"),
        [
            # n=5 decided (n1=6) — the load-bearing oracle table.
            (0, 5, 63 / 64),  # 0.984375
            (1, 4, 57 / 64),  # 0.890625
            (2, 3, 42 / 64),  # 0.65625
            (3, 2, 22 / 64),  # 0.34375
            (5, 0, 1 / 64),  # 0.015625
            # wins=losses=0 → n1=1 → P(X>=1) on Bin(1,0.5) = 0.5.
            (0, 0, 0.5),
            # n=3 decided (n1=4) spot values.
            (0, 3, 15 / 16),
            (1, 2, 11 / 16),
            # n=7 decided (n1=8) spot values.
            (0, 7, 255 / 256),
            (3, 4, 163 / 256),
            # n=9 decided (n1=10) spot values.
            (0, 9, 1023 / 1024),
            (4, 5, 638 / 1024),
        ],
    )
    def test_oracle_table(self, wins: int, losses: int, expected: float) -> None:
        assert posterior_prob_worse(wins, losses) == pytest.approx(expected, abs=_TOL)

    def test_symmetry(self) -> None:
        """P(worse | w, l) + P(worse | l, w) == 1.0 across the 10x10 grid.

        (For w == l each term is exactly 0.5, so the identity still holds.)
        The (wins, losses) pair is in the assertion message, so a single
        internal loop keeps per-case failure localization without inflating
        the suite with 100 parametrized instances.
        """
        for wins in range(0, 10):
            for losses in range(0, 10):
                a = posterior_prob_worse(wins, losses)
                b = posterior_prob_worse(losses, wins)
                assert a + b == pytest.approx(1.0, abs=_TOL), (wins, losses)
                if wins == losses:
                    assert a == pytest.approx(0.5, abs=_TOL), (wins, losses)

    def test_monotone_in_wins(self) -> None:
        """More wins (fixed losses) strictly lowers P(worse)."""
        losses = 4
        vals = [posterior_prob_worse(w, losses) for w in range(0, 6)]
        assert vals == sorted(vals, reverse=True)
        assert len(set(vals)) == len(vals)  # strictly decreasing

    def test_monotone_in_losses(self) -> None:
        """More losses (fixed wins) strictly raises P(worse)."""
        wins = 2
        vals = [posterior_prob_worse(wins, n) for n in range(0, 6)]
        assert vals == sorted(vals)
        assert len(set(vals)) == len(vals)  # strictly increasing

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            posterior_prob_worse(-1, 3)
        with pytest.raises(ValueError, match="non-negative"):
            posterior_prob_worse(3, -1)


class TestOneSidedRollback:
    def test_constants(self) -> None:
        assert MIN_DECIDED == 4
        assert ROLLBACK_THRESHOLD == 0.85

    @pytest.mark.parametrize(
        ("wins_new", "wins_prior", "expected_rollback"),
        [
            # n=5 decided: rollback on 0-5 (0.984) and 1-4 (0.891) only.
            (0, 5, True),
            (1, 4, True),
            (2, 3, False),  # neutral 2-3 (0.656) is KEPT
            (3, 2, False),
            (4, 1, False),
            (5, 0, False),
        ],
    )
    def test_n5_table(self, wins_new: int, wins_prior: int, expected_rollback: bool) -> None:
        rollback, reason = one_sided_rollback(wins_new, wins_prior)
        assert rollback is expected_rollback
        assert reason  # non-empty human-readable reason

    def test_fail_open_below_min_decided(self) -> None:
        """A draw-heavy record under MIN_DECIDED keeps with a fail-open reason.

        e.g. 1 new-win, 2 prior-wins, remainder draws → 3 decided < 4.
        """
        rollback, reason = one_sided_rollback(1, 2)
        assert rollback is False
        assert "fail-open" in reason
        assert "3 decided" in reason

    def test_zero_zero_fails_open(self) -> None:
        rollback, reason = one_sided_rollback(0, 0)
        assert rollback is False
        assert "fail-open" in reason

    def test_boundary_exactly_min_decided(self) -> None:
        """At exactly MIN_DECIDED decided the posterior is trusted.

        4 decided: 0-4 (P=0.969) rolls back; 1-3 (P=0.8125) keeps.
        """
        rollback_04, _ = one_sided_rollback(0, 4)
        assert rollback_04 is True
        rollback_13, reason_13 = one_sided_rollback(1, 3)
        assert rollback_13 is False
        assert "fail-open" not in reason_13  # trusted, not fail-open

    def test_reason_reports_probability(self) -> None:
        rollback, reason = one_sided_rollback(0, 5)
        assert rollback is True
        assert "P(worse)=0.9844" in reason
        assert "rollback" in reason

    def test_signature_defaults_are_the_named_constants(self) -> None:
        """The default args ARE the module constants (not re-literalled).

        Pins the single-source-of-truth: a hardcoded ``4`` / ``0.85`` slipping
        into the signature would fail here even though it computes identically.
        """
        params = inspect.signature(one_sided_rollback).parameters
        assert params["min_decided"].default is MIN_DECIDED
        assert params["threshold"].default is ROLLBACK_THRESHOLD
