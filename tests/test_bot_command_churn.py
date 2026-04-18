"""Regression tests for the command-churn bug in ``bots/v0/bot.py``.

Previously ``_run_micro`` and ``_rally_idle_army`` issued a fresh
``unit.attack(...)`` every game tick, which resets the unit's weapon cycle.
Units started attacking, got re-commanded, started attacking again, and
never fired.  These tests lock in the guards:

  * ``_should_reissue_attack_to_unit``  — skip re-issue when already
    attacking the same target tag.
  * ``_should_reissue_attack_to_position`` — skip re-issue when already
    attack-moving toward the same spot (within tolerance).

Idle units, different targets, and unrelated orders must still re-issue
so the existing combat code paths keep working.
"""

from __future__ import annotations

from typing import Any

from bots.v0.bot import (
    _should_reissue_attack_to_position,
    _should_reissue_attack_to_unit,
)
from sc2.position import Point2


# ---------------------------------------------------------------------------
# Minimal stub unit with only the attrs the helpers touch
# ---------------------------------------------------------------------------
class _StubUnit:
    """Strict stub for a burnysc2 Unit — matches ``_should_reissue_*`` contract.

    ``__slots__`` keeps the test explicit about which attributes the helpers
    read.  Any access outside this set raises ``AttributeError``.
    """

    __slots__ = ("is_idle", "is_attacking", "order_target")

    def __init__(
        self,
        *,
        is_idle: bool = False,
        is_attacking: bool = False,
        order_target: Any = None,
    ) -> None:
        self.is_idle = is_idle
        self.is_attacking = is_attacking
        self.order_target = order_target


# ===========================================================================
# _should_reissue_attack_to_unit
# ===========================================================================
class TestShouldReissueAttackToUnit:
    def test_idle_unit_reissues(self) -> None:
        """Idle units have no order — always issue the attack."""
        unit = _StubUnit(is_idle=True)
        assert _should_reissue_attack_to_unit(unit, target_tag=42) is True

    def test_same_target_tag_skips(self) -> None:
        """Already attacking the same unit — skip to preserve weapon cycle."""
        unit = _StubUnit(is_idle=False, is_attacking=True, order_target=42)
        assert _should_reissue_attack_to_unit(unit, target_tag=42) is False

    def test_different_target_tag_reissues(self) -> None:
        """Attacking a different target — switch (re-issue)."""
        unit = _StubUnit(is_idle=False, is_attacking=True, order_target=42)
        assert _should_reissue_attack_to_unit(unit, target_tag=99) is True

    def test_unit_moving_not_attacking_reissues(self) -> None:
        """Not attacking (e.g., moving) — re-issue so combat actually starts."""
        unit = _StubUnit(is_idle=False, is_attacking=False, order_target=42)
        assert _should_reissue_attack_to_unit(unit, target_tag=42) is True

    def test_order_target_is_none_reissues(self) -> None:
        """Edge: ``is_attacking`` True but ``order_target`` is None — reissue."""
        unit = _StubUnit(is_idle=False, is_attacking=True, order_target=None)
        assert _should_reissue_attack_to_unit(unit, target_tag=42) is True

    def test_order_target_is_position_reissues(self) -> None:
        """Currently attack-moving to a Point2 — new tag target, reissue."""
        unit = _StubUnit(
            is_idle=False, is_attacking=True, order_target=Point2((5.0, 5.0)),
        )
        assert _should_reissue_attack_to_unit(unit, target_tag=42) is True


# ===========================================================================
# _should_reissue_attack_to_position
# ===========================================================================
class TestShouldReissueAttackToPosition:
    def test_idle_unit_reissues(self) -> None:
        unit = _StubUnit(is_idle=True)
        assert _should_reissue_attack_to_position(unit, Point2((10.0, 10.0))) is True

    def test_same_position_within_tolerance_skips(self) -> None:
        """Attack-moving to (0,0); new target (1,0), tolerance 2.0 — skip."""
        unit = _StubUnit(
            is_idle=False, is_attacking=True, order_target=Point2((0.0, 0.0)),
        )
        assert (
            _should_reissue_attack_to_position(unit, Point2((1.0, 0.0)), tolerance=2.0)
            is False
        )

    def test_far_position_outside_tolerance_reissues(self) -> None:
        """Attack-moving to (0,0); new target (10,0), tolerance 2.0 — reissue."""
        unit = _StubUnit(
            is_idle=False, is_attacking=True, order_target=Point2((0.0, 0.0)),
        )
        assert (
            _should_reissue_attack_to_position(
                unit, Point2((10.0, 0.0)), tolerance=2.0,
            )
            is True
        )

    def test_order_target_is_none_reissues(self) -> None:
        """Edge: attacking flag set but ``order_target`` is None — reissue."""
        unit = _StubUnit(is_idle=False, is_attacking=True, order_target=None)
        assert _should_reissue_attack_to_position(unit, Point2((0.0, 0.0))) is True

    def test_unit_moving_not_attacking_reissues(self) -> None:
        """Plain-moving (not attacking) — reissue so advance becomes attack-move."""
        unit = _StubUnit(
            is_idle=False, is_attacking=False, order_target=Point2((0.0, 0.0)),
        )
        assert _should_reissue_attack_to_position(unit, Point2((0.0, 0.0))) is True

    def test_order_target_is_int_tag_reissues(self) -> None:
        """Attacking a tagged unit (int order_target) — new position target, reissue.

        ``int`` has no ``distance_to`` so falls through to re-issue, which is
        the intended behavior (switching from unit-target to position-target).
        """
        unit = _StubUnit(is_idle=False, is_attacking=True, order_target=42)
        assert _should_reissue_attack_to_position(unit, Point2((0.0, 0.0))) is True

    def test_custom_tolerance_respected(self) -> None:
        """Tolerance 5.0 covers a 3-tile gap that default tolerance 2.0 would not."""
        unit = _StubUnit(
            is_idle=False, is_attacking=True, order_target=Point2((0.0, 0.0)),
        )
        # Gap is 3 tiles along x.
        assert (
            _should_reissue_attack_to_position(unit, Point2((3.0, 0.0)), tolerance=5.0)
            is False
        )
        assert (
            _should_reissue_attack_to_position(unit, Point2((3.0, 0.0)), tolerance=2.0)
            is True
        )
