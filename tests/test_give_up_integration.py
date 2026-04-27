"""Integration tests for ``Alpha4GateBot._maybe_resign`` (Phase N Step 5).

The give-up logic lives on a real ``Alpha4GateBot`` instance method so the
production code path uses the literal ``await self.client.leave()`` call
that burnysc2 expects.  These tests use ``Alpha4GateBot.__new__`` to skip
``BotAI``'s ``__init__`` (which requires a real burnysc2 game context) and
attach only the attributes the method touches plus a mocked async client.

The behavioral contract under test:

  * ``_maybe_resign`` always appends the supplied winprob to
    ``self._winprob_history``.
  * It calls ``await self.client.leave()`` exactly once when
    ``should_give_up`` returns True and ``self._gave_up`` is False.
  * It is idempotent: subsequent calls after the first resignation are
    no-ops (no second ``leave()`` call).
  * It does NOT call ``leave()`` when the give-up criteria are not met
    (e.g. game time below threshold or a single high-winprob entry in
    the rolling window).
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any
from unittest.mock import AsyncMock

from bots.v0.bot import Alpha4GateBot
from bots.v0.give_up import GIVE_UP_WINDOW


def _make_bot(
    history: deque[float] | None = None, gave_up: bool = False
) -> Alpha4GateBot:
    """Return a barebones ``Alpha4GateBot`` with only the give-up attrs set.

    Skips ``BotAI.__init__`` via ``__new__`` so we don't need a live
    burnysc2 game context.  Only attaches the attributes that
    ``_maybe_resign`` reads or writes.
    """
    bot = Alpha4GateBot.__new__(Alpha4GateBot)
    bot._winprob_history = (
        history if history is not None else deque(maxlen=GIVE_UP_WINDOW)
    )
    bot._gave_up = gave_up
    # ``client`` is normally set by burnysc2 once the game starts.  An
    # ``AsyncMock`` lets us assert ``await self.client.leave()`` was called.
    bot.client = AsyncMock()  # type: ignore[assignment]
    return bot


def _run(coro: Any) -> None:
    """Wrap ``asyncio.run`` to match the project's existing async test style.

    The project does not depend on ``pytest-asyncio``; existing async tests
    (e.g. ``test_bot_attack_walking.py``) use ``asyncio.run`` directly.
    """
    asyncio.run(coro)


def test_appends_winprob_to_history() -> None:
    """``_maybe_resign`` always appends the winprob to the rolling history."""
    bot = _make_bot()

    _run(bot._maybe_resign(0.42, game_time=100.0))

    assert list(bot._winprob_history) == [0.42]


def test_calls_leave_when_threshold_met() -> None:
    """Past the time threshold with a full window of low scores → resign."""
    # 29 zeros pre-loaded; the call appends a 30th zero, filling the window.
    history: deque[float] = deque([0.0] * 29, maxlen=GIVE_UP_WINDOW)
    bot = _make_bot(history=history, gave_up=False)

    _run(bot._maybe_resign(0.0, game_time=500.0))

    assert bot.client.leave.await_count == 1
    assert bot._gave_up is True


def test_does_not_call_leave_when_threshold_not_met() -> None:
    """Below the game-time threshold the resign call is suppressed."""
    history: deque[float] = deque([0.0] * 30, maxlen=GIVE_UP_WINDOW)
    bot = _make_bot(history=history, gave_up=False)

    # 400s is below the 480s GIVE_UP_TIME_THRESHOLD_SECONDS.
    _run(bot._maybe_resign(0.0, game_time=400.0))

    assert bot.client.leave.await_count == 0
    assert bot._gave_up is False


def test_idempotent_after_first_resignation() -> None:
    """A second call after ``_gave_up`` is set must NOT trigger ``leave()``.

    Also pins that the history append still happens on the gave-up path,
    matching the docstring contract that ``_winprob_history`` keeps
    tracking even after resignation.
    """
    history: deque[float] = deque([0.0] * 30, maxlen=GIVE_UP_WINDOW)
    bot = _make_bot(history=history, gave_up=True)

    _run(bot._maybe_resign(0.123, game_time=500.0))

    assert bot.client.leave.await_count == 0
    # Deque was at maxlen so the append drops the oldest zero and the new
    # value lands at the end; pin that the append happened despite the
    # gave-up early return.
    assert bot._winprob_history[-1] == 0.123
    assert len(bot._winprob_history) == GIVE_UP_WINDOW


def test_does_not_call_leave_with_29_low_then_one_high() -> None:
    """A single above-threshold entry in the window blocks resignation."""
    # 29 zeros + this call (0.06, above the 0.05 threshold) fills the window
    # but leaves one high entry, so should_give_up returns False.
    history: deque[float] = deque([0.0] * 29, maxlen=GIVE_UP_WINDOW)
    bot = _make_bot(history=history, gave_up=False)

    _run(bot._maybe_resign(0.06, game_time=500.0))

    assert bot.client.leave.await_count == 0
    assert bot._gave_up is False
