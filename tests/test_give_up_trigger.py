"""Tests for the pure-function give-up trigger (Phase N Step 4)."""

from __future__ import annotations

from collections import deque

from bots.v0.give_up import (
    GIVE_UP_WINDOW,
    should_give_up,
)


def test_fires_with_thirty_zeros_after_eight_minutes() -> None:
    history: deque[float] = deque([0.0] * 30, maxlen=30)
    assert should_give_up(history, game_time=500.0) is True


def test_does_not_fire_under_eight_minutes() -> None:
    history: deque[float] = deque([0.0] * 30, maxlen=30)
    assert should_give_up(history, game_time=400.0) is False


def test_does_not_fire_with_only_29_entries() -> None:
    history: deque[float] = deque([0.0] * 29, maxlen=30)
    assert should_give_up(history, game_time=500.0) is False


def test_does_not_fire_with_empty_history() -> None:
    """The most common early-game state: no win-prob samples yet collected."""
    history: deque[float] = deque(maxlen=30)
    assert should_give_up(history, game_time=500.0) is False


def test_does_not_fire_with_one_entry_above_threshold_in_window() -> None:
    history: deque[float] = deque([0.0] * 29 + [0.06], maxlen=30)
    assert should_give_up(history, game_time=500.0) is False


def test_fires_with_window_at_exactly_threshold_minus_epsilon() -> None:
    history: deque[float] = deque([0.04999] * 30, maxlen=30)
    assert should_give_up(history, game_time=500.0) is True


def test_does_not_fire_when_value_equals_threshold() -> None:
    history: deque[float] = deque([0.05] * 30, maxlen=30)
    assert should_give_up(history, game_time=500.0) is False


def test_does_not_fire_at_exactly_eight_minutes() -> None:
    history: deque[float] = deque([0.0] * 30, maxlen=30)
    assert should_give_up(history, game_time=480.0) is False


def test_fires_when_history_longer_than_window() -> None:
    history: deque[float] = deque([1.0] * 100 + [0.0] * 30, maxlen=200)
    assert len(history) == 130
    assert should_give_up(history, game_time=500.0) is True


def test_does_not_mutate_history() -> None:
    history: deque[float] = deque([0.0] * 30, maxlen=30)
    snapshot = list(history)
    should_give_up(history, game_time=500.0)
    assert list(history) == snapshot


def test_window_constant_is_thirty() -> None:
    assert GIVE_UP_WINDOW == 30
