"""Tests for the heuristic win-probability score."""

from __future__ import annotations

import dataclasses

import pytest
from bots.v0.decision_engine import GameSnapshot
from bots.v0.learning.winprob_heuristic import score


def test_formula_correctness_lands_in_open_interval() -> None:
    """Hand-crafted snapshot lands strictly inside (0, 1) without clamping.

    Chosen values:
        army_supply=20, enemy_army_supply_visible=10
            -> ratio = min(20/10, 2.0) / 2.0 = 2.0/2.0 = 1.0
        worker_count=25, base_count=2
            -> economy = 0.5*25/50 + 0.5*2/3 = 0.25 + 1/3
        supply_used=100
            -> supply = 100/200 = 0.5
        gateway_count=4, robo_count=1
            -> production = (4 + 2*1)/6 = 1.0
        upgrade_count=2
            -> upgrades = 2/4 = 0.5
        cannon_count=2, battery_count=0
            -> static_defense = (2+0)/4 = 0.5
        enemy_army_near_base=False
            -> threat = 0.0

        raw = 0.25*1.0
            + 0.25*(0.25 + 1/3)         # = 0.0625 + 1/12
            + 0.15*0.5                   # = 0.075
            + 0.15*1.0                   # = 0.15
            + 0.10*0.5                   # = 0.05
            + 0.10*0.5                   # = 0.05
            - 0.30*0.0                   # = 0.0
            = 0.6375 + 1/12
    """
    snap = GameSnapshot(
        supply_used=100,
        army_supply=20,
        worker_count=25,
        base_count=2,
        enemy_army_near_base=False,
        enemy_army_supply_visible=10,
        gateway_count=4,
        robo_count=1,
        upgrade_count=2,
        cannon_count=2,
        battery_count=0,
    )

    expected = 0.6375 + 1.0 / 12.0
    # Sanity: expected lands in (0, 1) so no clamp is in play.
    assert 0.0 < expected < 1.0

    assert score(snap) == pytest.approx(expected, abs=1e-9)


def test_clamping_zero_and_saturated() -> None:
    """Default/empty snapshot stays >= 0.0; fully saturated snapshot caps at 1.0."""
    # GameSnapshot() defaults base_count=1, so the economy term contributes a
    # positive 0.25 * (0.5 * 1/3) = 1/24 even with everything else at zero.
    # The defining property is that the score never goes negative (no clamp
    # under-flow); we assert >= 0.0 per spec.
    zero = GameSnapshot()
    assert score(zero) >= 0.0

    # An explicit all-zeros snapshot (base_count=0 forces every term to 0)
    # produces exactly 0.0 with no clamping required.
    all_zero = GameSnapshot(base_count=0)
    assert score(all_zero) == pytest.approx(0.0, abs=1e-9)

    saturated = GameSnapshot(
        supply_used=200,            # supply = 1.0
        army_supply=1000,           # ratio saturates at 2x then /2 -> 1.0
        worker_count=200,           # economy worker term saturates
        base_count=10,              # economy base term saturates
        enemy_army_near_base=False, # threat = 0
        enemy_army_supply_visible=1,
        gateway_count=20,           # production saturates
        robo_count=20,
        upgrade_count=20,           # upgrades saturates
        cannon_count=20,            # static_defense saturates
        battery_count=20,
    )
    # Each non-threat term contributes well above its weighted ceiling, so the
    # raw sum is far above 1.0 and the clamp must take effect.
    assert score(saturated) <= 1.0
    assert score(saturated) == pytest.approx(1.0, abs=1e-9)


def test_enemy_army_near_base_subtracts_exactly_0_30() -> None:
    """Flipping enemy_army_near_base False -> True drops score by exactly 0.30.

    Uses a midrange snapshot so neither variant clamps to [0, 1].
    """
    safe = GameSnapshot(
        supply_used=100,
        army_supply=20,
        worker_count=25,
        base_count=2,
        enemy_army_near_base=False,
        enemy_army_supply_visible=10,
        gateway_count=4,
        robo_count=1,
        upgrade_count=2,
        cannon_count=2,
        battery_count=0,
    )
    threatened = dataclasses.replace(safe, enemy_army_near_base=True)

    safe_score = score(safe)
    threatened_score = score(threatened)

    # Sanity: both values must be strictly inside (0, 1), otherwise the clamp
    # would mask the difference and this test would not be measuring the
    # threat coefficient in isolation.
    assert 0.0 < threatened_score < safe_score < 1.0

    assert (safe_score - threatened_score) == pytest.approx(0.30, abs=1e-9)


def test_lower_clamp_engages_when_only_threat_term_contributes() -> None:
    """Force raw < 0 by zeroing every positive term and setting threat=True.

    With ``base_count=0`` (overriding the dataclass default of 1) every
    economy term is 0, every production/defense term is 0, and the ratio
    term is 0/max(0, 1) = 0.  Only the -0.30 threat term remains, so the
    raw sum is -0.30 — the lower clamp must take effect.
    """
    snap = GameSnapshot(base_count=0, enemy_army_near_base=True)
    assert score(snap) == pytest.approx(0.0, abs=1e-9)


def test_zero_visible_enemy_treated_as_winning_ratio() -> None:
    """``enemy_army_supply_visible == 0`` (no vision) saturates the ratio term.

    Guards the ``max(enemy_army_supply_visible, 1)`` divide-by-zero shield.
    With any positive ``army_supply``, the ratio numerator >= 1 and the
    denominator clamps to 1, so the inner ratio is >= 1 and after the
    /2 normalization the term stays in [0.5, 1.0].
    """
    snap = GameSnapshot(army_supply=10, enemy_army_supply_visible=0, base_count=0)
    # ratio = min(10 / max(0, 1), 2.0) / 2.0 = min(10, 2.0) / 2.0 = 1.0
    # Only the 0.25 * ratio term contributes (everything else is zero).
    assert score(snap) == pytest.approx(0.25, abs=1e-9)


def test_army_ratio_saturates_at_two_times_enemy() -> None:
    """Once army_supply >= 2 * enemy_army_supply_visible, more army doesn't help.

    Two snapshots that differ only in how *much* army_supply exceeds the
    saturation threshold must produce identical scores.  This pins the
    ``min(..., 2.0) / 2.0`` cap rather than letting the ratio scale past 1.
    """
    base = GameSnapshot(
        army_supply=20,        # exactly 2x enemy -> ratio caps at 1.0
        enemy_army_supply_visible=10,
        base_count=0,
    )
    far_above_cap = dataclasses.replace(base, army_supply=200)  # 20x enemy

    assert score(base) == score(far_above_cap)
