"""Jev's burnysc2 lifecycle: one match driven entirely by the policy graph.

:class:`JevController` is the per-step production path. Each game step it

1. ends the match cleanly when a stop was requested (Ctrl+C) or a wall-clock or
   game-time limit is reached (it leaves the game through the port);
2. forwards SC2's action errors to the runtime (SC2 reports each one once, so
   this runs every step);
3. between policy ticks (the runtime's 0.25 game-second cadence) only refreshes
   the adapter's enemy-structure memory -- the full visible-state
   :class:`~jev.contracts.Observation` is built only when the runtime will tick,
   which is also the only time it checks acknowledgements;
4. on a tick, builds that Observation through
   :class:`~jev.sc2_adapter.Sc2Adapter`, ticks :class:`~jev.runtime.JevRuntime`
   and hands the graph-selected commands to the adapter, which issues them.

A failure inside that pipeline never escapes a step: it is recorded as a crash
(error code ``match_crashed``) and the bot leaves the game, so the runner reports
a failure whatever burnysc2 does with bot exceptions (some of its loops log them
and report a Defeat). :class:`JevBot` is the thin ``BotAI`` shim burnysc2 runs:
``on_start`` attaches the controller to the live game and registers Ctrl+C as a
clean-leave request, ``on_step`` steps the controller, ``on_end`` records SC2's
result. There is no legacy gameplay code, LLM or neural policy anywhere on this
path: every game command comes from a policy node through a runtime task.
"""

from __future__ import annotations

import signal
import threading
import time
from collections import deque
from collections.abc import Callable
from types import FrameType
from typing import Any, Final, Literal

from sc2.bot_ai import BotAI
from sc2.data import Result

from jev.contracts import (
    MAX_MESSAGE_CHARS,
    Event,
    JevError,
    render_text,
    safe_exception_text,
    safe_repr,
)
from jev.policy import PolicyBundle
from jev.runner import MatchLimits
from jev.runtime import JevRuntime, TickResult
from jev.sc2_adapter import BotAIPort, GamePort, Sc2Adapter

__all__ = [
    "JevBot",
    "JevController",
    "MatchResult",
    "RECENT_EVENT_LIMIT",
    "TerminalReason",
    "match_result",
]

#: An SC2 outcome the match ended with (a stop or time limit is a TerminalReason).
MatchResult = Literal["win", "loss", "draw"]
#: Why the controller itself ended the match.
TerminalReason = Literal["stopped", "game_timeout", "wall_timeout", "crashed"]
#: Recent events kept in memory (plan D5 keeps 200 in a run snapshot).
RECENT_EVENT_LIMIT: Final = 200

_SC2_RESULTS: Final[dict[Result, MatchResult]] = {
    Result.Victory: "win",
    Result.Defeat: "loss",
    Result.Tie: "draw",
}


def match_result(result: object) -> MatchResult | None:
    """burnysc2's game result as a Jev result; None for Undecided or anything else."""
    return _SC2_RESULTS.get(result) if isinstance(result, Result) else None


class JevController:
    """Drive one match: observation -> runtime tick -> adapter command issue.

    The runtime uses the plan's default cadence, budgets and task lifecycle. The
    wall-clock limit counts from the first game step, so SC2's startup is not
    charged to it (burnysc2 bounds the launch with its own connect timeout);
    ``clock`` is injectable. Raises :class:`ValueError` for an invalid run id or
    limits.
    """

    def __init__(
        self,
        bundle: PolicyBundle,
        *,
        run_id: str,
        limits: MatchLimits,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(limits, MatchLimits):
            raise ValueError(f"limits must be MatchLimits, got {safe_repr(limits)}")
        self.runtime = JevRuntime(bundle.policy, run_id=run_id)
        self.adapter = Sc2Adapter()
        self._limits = limits
        self._clock = clock
        self._started: float | None = None  # wall clock at the first game step
        self._game: Any = None
        self._port: GamePort | None = None
        self._stop_requested = False
        self._terminal: TerminalReason | None = None
        self._crash: JevError | None = None
        self._left = False
        self._leave_failure: str | None = None
        self._result: MatchResult | None = None
        self._game_seconds = 0.0
        self._recent: deque[Event] = deque(maxlen=RECENT_EVENT_LIMIT)

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    @property
    def terminal(self) -> TerminalReason | None:
        """Why the controller ended the match, if it did."""
        return self._terminal

    @property
    def attached(self) -> bool:
        """Whether the bot was bound to a running game (``on_start`` ran)."""
        return self._port is not None

    @property
    def leave_failure(self) -> str | None:
        """Why leaving the game failed, if it did (rendered, capped); not a crash."""
        return self._leave_failure

    @property
    def crash(self) -> JevError | None:
        """The ``match_crashed`` error, once the match crashed (the first crash wins)."""
        return self._crash

    @property
    def result(self) -> MatchResult | None:
        """The SC2 result reported at match end, if any."""
        return self._result

    @property
    def game_seconds(self) -> float:
        """Game time of the latest step."""
        return self._game_seconds

    @property
    def recent_events(self) -> tuple[Event, ...]:
        """The latest :data:`RECENT_EVENT_LIMIT` runtime events, oldest first."""
        return tuple(self._recent)

    def attach(self, game: Any, port: GamePort) -> None:
        """Bind the live game (a burnysc2 ``BotAI``) and its command port."""
        self._game = game
        self._port = port

    def request_stop(self) -> None:
        """Ask for a clean leave at the next step (Ctrl+C)."""
        self._stop_requested = True

    def finish(self, result: MatchResult | None) -> None:
        """Record the result SC2 reported when the match ended."""
        self._result = result

    def record_crash(self, exc: BaseException) -> None:
        """Record that the match crashed; the bot leaves the game at the next step.

        The message is rendered and capped. A crash overrides any other terminal
        reason, and only the first crash's error is kept.
        """
        if self._crash is None:
            name = safe_repr(type(exc).__name__)
            detail = safe_exception_text(exc, MAX_MESSAGE_CHARS)
            message = render_text(f"{name}: {detail}")[:MAX_MESSAGE_CHARS]
            self._crash = JevError("match_crashed", message)
        self._terminal = "crashed"

    async def step(self) -> TickResult | None:
        """Run one game step; the tick result, or None when the policy did not tick.

        Raises :class:`RuntimeError` before :meth:`attach`; nothing else escapes.
        A failure in the step's own pipeline -- malformed game state (the adapter's
        ValueError), a runtime error, an exception from the SC2 port -- is recorded
        with :meth:`record_crash` and the bot leaves the game. Once the match has
        ended for any reason the bot leaves (once) and does nothing else. A failed
        leave is kept as :attr:`leave_failure` and never changes why the match
        ended: a clean stop or time limit stays one (SC2 may already have ended the
        game on the same step).
        """
        port = self._port
        if port is None:
            raise RuntimeError("JevController.step() called before attach()")
        if self._terminal is not None:
            await self._leave(port)
            return None
        try:
            return await self._step(port)
        except Exception as exc:
            self.record_crash(exc)
            await self._leave(port)
            return None

    async def _step(self, port: GamePort) -> TickResult | None:
        if self._stop_requested:
            await self._end(port, "stopped")
            return None
        now = self._clock()
        if self._started is None:
            self._started = now
        if now - self._started >= self._limits.max_wall_seconds:
            await self._end(port, "wall_timeout")
            return None
        game_seconds = self.adapter.game_seconds(self._game)
        self._game_seconds = game_seconds
        if game_seconds >= self._limits.max_game_seconds:
            await self._end(port, "game_timeout")
            return None
        self.adapter.collect_rejections(port, self.runtime)
        if not self.runtime.is_due(game_seconds):
            self.adapter.remember(self._game, port)
            return None
        result = self.runtime.tick(self.adapter.observe(self._game, port))
        if result.ticked:
            await self.adapter.issue(result.commands, port, self.runtime)
            self._recent.extend(result.events)
        return result

    async def _end(self, port: GamePort, reason: TerminalReason) -> None:
        self._terminal = reason
        await self._leave(port)

    async def _leave(self, port: GamePort) -> None:
        if self._left:
            return
        self._left = True
        try:
            await port.leave()
        except Exception as exc:  # a diagnostic, not a crash: the reason to end stands
            name = safe_repr(type(exc).__name__)
            detail = safe_exception_text(exc)
            self._leave_failure = render_text(f"{name}: {detail}")[:MAX_MESSAGE_CHARS]


class JevBot(BotAI):
    """The burnysc2 bot: every decision comes from :class:`JevController`."""

    def __init__(self, controller: JevController) -> None:
        super().__init__()
        self.controller = controller

    async def on_start(self) -> None:
        self.controller.attach(self, BotAIPort(self))
        try:
            self._register_stop_request()
        except Exception as exc:  # burnysc2 would turn it into a silent Defeat
            self.controller.record_crash(exc)

    async def on_step(self, iteration: int) -> None:
        await self.controller.step()  # pipeline failures are recorded, never raised

    async def on_end(self, game_result: Result) -> None:
        self.controller.finish(match_result(game_result))

    def _register_stop_request(self) -> None:
        """Ctrl+C asks for a clean leave; a second Ctrl+C falls back to burnysc2.

        burnysc2 installs its own SIGINT handler when it launches SC2 (it cleans
        up only the SC2 process it started); it is kept as the second-press
        fallback. Signal handlers can only be set from the main thread.
        """
        if threading.current_thread() is not threading.main_thread():
            return
        previous = signal.getsignal(signal.SIGINT)
        controller = self.controller

        def request_leave(signum: int, frame: FrameType | None) -> None:
            if controller.stop_requested and callable(previous):
                previous(signum, frame)
                return
            controller.request_stop()

        signal.signal(signal.SIGINT, request_leave)
