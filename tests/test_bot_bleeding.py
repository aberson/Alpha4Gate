"""Tests for the bleeding-commit heuristic in Alpha4GateBot.

See `bots/v0/bot.py::_is_bleeding_stationary`. Detects when the army is
stationary AND losing HP for at least `BLEEDING_COMMIT_SECONDS` — e.g.
clustered below an enemy ramp taking free ranged fire — and reports
True so `_run_micro` can force a commit forward.

These tests exercise the detection heuristic directly (without the
`_run_micro` integration) by binding the real method to a minimal stub.
"""

from __future__ import annotations

from dataclasses import dataclass

from bots.v0.bot import Alpha4GateBot
from bots.v0.decision_engine import GameSnapshot


# ---------------------------------------------------------------------------
# Minimal mock unit: _is_bleeding_stationary only reads position/health/shield
# ---------------------------------------------------------------------------
@dataclass
class _MockPosition:
    x: float = 0.0
    y: float = 0.0


class _MockUnit:
    """Stand-in army unit. Only fields `_is_bleeding_stationary` reads."""

    __slots__ = ("position", "health", "shield", "is_structure", "type_id")

    def __init__(
        self,
        *,
        position: tuple[float, float] = (0.0, 0.0),
        health: int = 100,
        shield: int = 50,
    ) -> None:
        self.position = _MockPosition(position[0], position[1])
        self.health = health
        self.shield = shield
        self.is_structure = False
        self.type_id = None  # unused by the heuristic


# ---------------------------------------------------------------------------
# Stub bot — binds the real _is_bleeding_stationary method
# ---------------------------------------------------------------------------
class _StubBot:
    """Strict stub exposing only the state the heuristic touches."""

    __slots__ = (
        "_last_army_centroid",
        "_last_army_hp",
        "_bleeding_since",
    )

    # Copy class-level thresholds so the heuristic sees the production values
    BLEEDING_MOVE_THRESHOLD = Alpha4GateBot.BLEEDING_MOVE_THRESHOLD
    BLEEDING_HP_PER_TICK_THRESHOLD = Alpha4GateBot.BLEEDING_HP_PER_TICK_THRESHOLD
    BLEEDING_COMMIT_SECONDS = Alpha4GateBot.BLEEDING_COMMIT_SECONDS

    def __init__(self) -> None:
        self._last_army_centroid: tuple[float, float] | None = None
        self._last_army_hp: int = 0
        self._bleeding_since: float | None = None

    # Bind the real production method
    _is_bleeding_stationary = Alpha4GateBot._is_bleeding_stationary  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _snap(t: float) -> GameSnapshot:
    return GameSnapshot(game_time_seconds=t)


def _call(bot: _StubBot, army: list[_MockUnit], t: float) -> bool:
    return bot._is_bleeding_stationary(army, _snap(t))  # type: ignore[arg-type]


# ===========================================================================
# Edge cases and non-bleeding scenarios
# ===========================================================================
def test_empty_army_not_bleeding() -> None:
    """Empty army → returns False AND resets internal state."""
    bot = _StubBot()
    # Seed state so we can verify reset
    bot._last_army_centroid = (10.0, 10.0)
    bot._last_army_hp = 500
    bot._bleeding_since = 5.0

    assert _call(bot, [], 10.0) is False
    assert bot._last_army_centroid is None
    assert bot._last_army_hp == 0
    assert bot._bleeding_since is None


def test_first_tick_not_bleeding() -> None:
    """Fresh bot, first call → caches state and returns False (no history)."""
    bot = _StubBot()
    army = [_MockUnit(position=(20.0, 20.0), health=100, shield=50)]
    assert _call(bot, army, 0.0) is False
    # Caching contract: first tick MUST seed centroid + HP (pin explicitly)
    assert bot._last_army_centroid is not None
    assert bot._last_army_hp > 0
    # And pin the exact values so we catch accidental shape/type changes
    assert bot._last_army_centroid == (20.0, 20.0)
    assert bot._last_army_hp == 150
    assert bot._bleeding_since is None


def test_moving_army_not_bleeding() -> None:
    """Centroid moved >> threshold + HP dropped → not bleeding (moving)."""
    bot = _StubBot()
    # Tick 1 — cache baseline
    _call(bot, [_MockUnit(position=(0.0, 0.0), health=100, shield=50)], 0.0)
    # Tick 2 — moved 10 tiles AND lost HP. Movement wins, not bleeding.
    army = [_MockUnit(position=(10.0, 0.0), health=80, shield=40)]
    assert _call(bot, army, 1.0) is False
    assert bot._bleeding_since is None


def test_stationary_army_gaining_hp_not_bleeding() -> None:
    """Stationary but HP going up (shield regen) → not bleeding."""
    bot = _StubBot()
    _call(bot, [_MockUnit(position=(0.0, 0.0), health=100, shield=0)], 0.0)
    # Same position, shield regenerating
    army = [_MockUnit(position=(0.0, 0.0), health=100, shield=25)]
    assert _call(bot, army, 1.0) is False
    assert bot._bleeding_since is None


def test_stationary_bleeding_short_duration_not_yet() -> None:
    """Stationary + HP dropping but elapsed < BLEEDING_COMMIT_SECONDS → False."""
    bot = _StubBot()
    _call(bot, [_MockUnit(position=(0.0, 0.0), health=100, shield=50)], 0.0)
    # Tick 2: same place, HP lost → bleeding starts. Not yet elapsed enough.
    army = [_MockUnit(position=(0.0, 0.0), health=90, shield=40)]
    assert _call(bot, army, 1.0) is False
    assert bot._bleeding_since == 1.0
    # Tick 3: still bleeding but only 1s elapsed (1→2), below 3s threshold
    army = [_MockUnit(position=(0.0, 0.0), health=80, shield=30)]
    assert _call(bot, army, 2.0) is False
    # _bleeding_since should stick at 1.0
    assert bot._bleeding_since == 1.0


def test_stationary_bleeding_long_enough_triggers() -> None:
    """Stationary + HP dropping for >= BLEEDING_COMMIT_SECONDS → True."""
    bot = _StubBot()
    # Tick 1 — baseline
    _call(bot, [_MockUnit(position=(0.0, 0.0), health=100, shield=50)], 0.0)
    # Tick 2 — start bleeding at t=1.0
    assert _call(
        bot, [_MockUnit(position=(0.0, 0.0), health=90, shield=40)], 1.0,
    ) is False
    assert bot._bleeding_since == 1.0
    # Tick 3 — still bleeding, elapsed = 2s (not yet)
    assert _call(
        bot, [_MockUnit(position=(0.0, 0.0), health=80, shield=30)], 3.0,
    ) is False
    # Tick 4 — elapsed = 3s exactly → fires (check is `elapsed >= threshold`)
    assert _call(
        bot, [_MockUnit(position=(0.0, 0.0), health=70, shield=20)], 4.0,
    ) is True


def test_bleeding_fires_at_exact_threshold_boundary() -> None:
    """Explicit boundary test: elapsed == BLEEDING_COMMIT_SECONDS → True.

    The production check is `elapsed >= BLEEDING_COMMIT_SECONDS`, so at
    exactly 3.0s of bleeding we must fire (not off-by-one).
    """
    bot = _StubBot()
    # Tick 1 — baseline
    _call(bot, [_MockUnit(position=(0.0, 0.0), health=100, shield=50)], 0.0)
    # Tick 2 — start bleeding at t=0.5
    _call(bot, [_MockUnit(position=(0.0, 0.0), health=95, shield=45)], 0.5)
    assert bot._bleeding_since == 0.5
    # Tick 3 — elapsed = 3.0 exactly (0.5 → 3.5). Must fire.
    elapsed_boundary = 0.5 + Alpha4GateBot.BLEEDING_COMMIT_SECONDS
    assert _call(
        bot,
        [_MockUnit(position=(0.0, 0.0), health=80, shield=30)],
        elapsed_boundary,
    ) is True


def test_bleeding_just_under_threshold_does_not_fire() -> None:
    """Companion to the boundary test: elapsed < threshold → False.

    At 2.99s of bleeding we must NOT fire, locking the `>=` semantics.
    """
    bot = _StubBot()
    _call(bot, [_MockUnit(position=(0.0, 0.0), health=100, shield=50)], 0.0)
    _call(bot, [_MockUnit(position=(0.0, 0.0), health=95, shield=45)], 0.5)
    # Elapsed = 2.99, below threshold.
    just_under = 0.5 + Alpha4GateBot.BLEEDING_COMMIT_SECONDS - 0.01
    assert _call(
        bot,
        [_MockUnit(position=(0.0, 0.0), health=80, shield=30)],
        just_under,
    ) is False


def test_bleeding_then_moving_resets() -> None:
    """Bleeding detected, then army moves → state resets, returns False."""
    bot = _StubBot()
    # Tick 1 — baseline
    _call(bot, [_MockUnit(position=(0.0, 0.0), health=100, shield=50)], 0.0)
    # Tick 2 — start bleeding
    _call(bot, [_MockUnit(position=(0.0, 0.0), health=90, shield=40)], 1.0)
    assert bot._bleeding_since == 1.0
    # Tick 3 — army moved far enough that movement threshold is cleared.
    army = [_MockUnit(position=(20.0, 20.0), health=80, shield=30)]
    assert _call(bot, army, 2.0) is False
    # Bleeding timer reset (we moved — even if HP dropped, we're not stuck)
    assert bot._bleeding_since is None
