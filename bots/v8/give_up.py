"""Pure-function give-up trigger for Alpha4Gate (Phase N Step 4).

The bot resigns when its rolling win-probability estimate has been
consistently low for a sustained window. Thresholds (window=30,
prob_threshold=0.05, time_threshold=480.0s) are sourced from the Phase N
build plan; they are tuned so early-game volatility cannot trigger an
unnecessary resignation.
"""

from __future__ import annotations

from collections import deque

GIVE_UP_WINDOW: int = 30
GIVE_UP_PROB_THRESHOLD: float = 0.05
GIVE_UP_TIME_THRESHOLD_SECONDS: float = 480.0


def should_give_up(history: deque[float], game_time: float) -> bool:
    """Return True iff the bot should resign; pure function, no mutation."""
    if len(history) < GIVE_UP_WINDOW or game_time <= GIVE_UP_TIME_THRESHOLD_SECONDS:
        return False
    last_window = list(history)[-GIVE_UP_WINDOW:]
    return all(value < GIVE_UP_PROB_THRESHOLD for value in last_window)
