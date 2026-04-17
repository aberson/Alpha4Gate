"""Tests for PFSP-lite opponent sampling in orchestrator.selfplay."""

from __future__ import annotations

import random

import pytest

from orchestrator.selfplay import pfsp_sample


class TestPfspSample:
    """Unit tests for :func:`pfsp_sample`."""

    def test_empty_pool_raises(self) -> None:
        with pytest.raises(ValueError, match="pool must be non-empty"):
            pfsp_sample([], {})

    def test_cold_start_uniform(self) -> None:
        """No win-rate data → uniform sampling across pool."""
        rng = random.Random(42)
        pool = ["v0", "v1", "v2"]
        counts: dict[str, int] = {v: 0 for v in pool}
        for _ in range(3000):
            counts[pfsp_sample(pool, {}, rng=rng)] += 1
        # With 3000 samples and 3 options, each should get ~1000.
        for v in pool:
            assert 700 < counts[v] < 1300, f"{v} sampled {counts[v]} times"

    def test_temperature_zero_uniform(self) -> None:
        """temperature=0 → uniform regardless of win rates."""
        rng = random.Random(42)
        pool = ["v0", "v1"]
        win_rates = {"v0": 0.9, "v1": 0.1}
        counts: dict[str, int] = {v: 0 for v in pool}
        for _ in range(2000):
            counts[pfsp_sample(pool, win_rates, temperature=0.0, rng=rng)] += 1
        # Should be roughly 50/50.
        for v in pool:
            assert 700 < counts[v] < 1300, f"{v} sampled {counts[v]} times"

    def test_weaker_opponent_sampled_more(self) -> None:
        """Opponent with lower win rate gets higher weight."""
        rng = random.Random(42)
        pool = ["strong", "weak"]
        # "strong" has 90% WR against trainee → weight (1-0.9)^1 = 0.1
        # "weak"   has 10% WR against trainee → weight (1-0.1)^1 = 0.9
        win_rates = {"strong": 0.9, "weak": 0.1}
        counts: dict[str, int] = {"strong": 0, "weak": 0}
        for _ in range(2000):
            counts[pfsp_sample(pool, win_rates, rng=rng)] += 1
        assert counts["weak"] > counts["strong"] * 3

    def test_100_percent_wr_excluded(self) -> None:
        """Opponent with 100% win rate gets zero weight and is never sampled."""
        rng = random.Random(42)
        pool = ["perfect", "beatable"]
        win_rates = {"perfect": 1.0, "beatable": 0.5}
        for _ in range(500):
            assert pfsp_sample(pool, win_rates, rng=rng) == "beatable"

    def test_all_100_percent_raises(self) -> None:
        """All opponents at 100% WR → no valid choice → ValueError."""
        with pytest.raises(ValueError, match="no valid opponent"):
            pfsp_sample(["v0", "v1"], {"v0": 1.0, "v1": 1.0})

    def test_single_opponent_pool(self) -> None:
        """Single-element pool always returns that element."""
        assert pfsp_sample(["v0"], {}) == "v0"
        assert pfsp_sample(["v0"], {"v0": 0.5}) == "v0"

    def test_missing_wr_treated_as_zero(self) -> None:
        """Opponent not in win_rates dict treated as cold-start (wr=0)."""
        rng = random.Random(42)
        pool = ["known", "unknown"]
        win_rates = {"known": 0.8}  # "unknown" missing → wr=0 → weight=1.0
        counts: dict[str, int] = {"known": 0, "unknown": 0}
        for _ in range(2000):
            counts[pfsp_sample(pool, win_rates, rng=rng)] += 1
        # "unknown" weight=1.0, "known" weight=0.2 → unknown ~83%
        assert counts["unknown"] > counts["known"] * 2

    def test_high_temperature_sharpens(self) -> None:
        """Higher temperature makes weighting more extreme."""
        rng_lo = random.Random(42)
        rng_hi = random.Random(42)
        pool = ["strong", "weak"]
        win_rates = {"strong": 0.8, "weak": 0.2}

        counts_lo: dict[str, int] = {"strong": 0, "weak": 0}
        counts_hi: dict[str, int] = {"strong": 0, "weak": 0}
        for _ in range(2000):
            counts_lo[pfsp_sample(pool, win_rates, temperature=1.0, rng=rng_lo)] += 1
            counts_hi[pfsp_sample(pool, win_rates, temperature=3.0, rng=rng_hi)] += 1

        # Higher temp → "weak" gets even larger share.
        weak_ratio_lo = counts_lo["weak"] / 2000
        weak_ratio_hi = counts_hi["weak"] / 2000
        assert weak_ratio_hi > weak_ratio_lo

    def test_deterministic_with_seed(self) -> None:
        """Same RNG seed → same sequence of samples."""
        pool = ["v0", "v1", "v2"]
        win_rates = {"v0": 0.3, "v1": 0.6, "v2": 0.1}
        results_a = [
            pfsp_sample(pool, win_rates, rng=random.Random(99)) for _ in range(10)
        ]
        results_b = [
            pfsp_sample(pool, win_rates, rng=random.Random(99)) for _ in range(10)
        ]
        assert results_a == results_b
