"""Optional wall-clock pacing for non-realtime SC2; never changes game step size.

The target is game seconds per wall second, not a guarantee of machine throughput.
Each interval starts from the previous observation's release: a slow frame cannot
build up credit for a later burst. Sleeps yield to hosted requests and check stop
conditions frequently. This does not alter model scheduling or answer freshness.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable


def validate_speed(speed: float | None) -> None:
    if speed is not None and (
        isinstance(speed, bool)
        or not isinstance(speed, (float, int))
        or not math.isfinite(speed)
        or not 1 <= speed <= 16
    ):
        raise ValueError("game_speed must be a finite number in 1..16")


class GamePacer:
    """Pace consecutive observations without blocking the asyncio event loop."""

    def __init__(
        self,
        speed: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        validate_speed(speed)
        self.speed = speed
        self.clock = clock
        self.sleep = sleep
        self.previous_game: float | None = None
        self.previous_wall: float | None = None

    async def wait(self, game_seconds: float, stopped: Callable[[], bool]) -> None:
        now = self.clock()
        if self.previous_game is not None and self.previous_wall is not None:
            target = self.previous_wall + max(0.0, game_seconds - self.previous_game) / self.speed
            while not stopped():
                remaining = target - self.clock()
                if remaining <= 0:
                    break
                await self.sleep(min(remaining, 0.05))
            now = self.clock()
        self.previous_game = game_seconds
        self.previous_wall = now
