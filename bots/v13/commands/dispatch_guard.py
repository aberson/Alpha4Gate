"""Per-(action, target) cooldown guard to suppress retry-storm dispatches."""

from __future__ import annotations

_DEFAULT_COOLDOWNS: dict[str, float] = {"WarpIn": 2.0, "build": 5.0}


class DispatchGuard:
    """Suppresses repeated dispatches of the same (action, target) within a cooldown."""

    def __init__(
        self,
        per_action_cooldown: dict[str, float] | None = None,
        default_cooldown: float = 2.0,
    ) -> None:
        self._base_cooldowns: dict[str, float] = (
            dict(_DEFAULT_COOLDOWNS) if per_action_cooldown is None
            else dict(per_action_cooldown)
        )
        self._base_default: float = default_cooldown
        self._cooldowns: dict[str, float] = dict(self._base_cooldowns)
        self._default_cooldown = default_cooldown
        self._last_dispatch: dict[tuple[str, str], float] = {}
        self._emergency: bool = False

    def set_emergency_mode(self, active: bool) -> None:
        """Halve all cooldowns when *active* is True; restore when False."""
        if active == self._emergency:
            return
        self._emergency = active
        if active:
            self._cooldowns = {k: v / 2.0 for k, v in self._base_cooldowns.items()}
            self._default_cooldown = self._base_default / 2.0
        else:
            self._cooldowns = dict(self._base_cooldowns)
            self._default_cooldown = self._base_default

    def should_dispatch(self, action: str, target: str, now: float) -> bool:
        """True if never dispatched or last dispatch is older than the action's cooldown."""
        last = self._last_dispatch.get((action, target))
        if last is None:
            return True
        cooldown = self._cooldowns.get(action, self._default_cooldown)
        return (now - last) > cooldown

    def mark_dispatched(self, action: str, target: str, now: float) -> None:
        """Record the dispatch time for this (action, target) pair."""
        self._last_dispatch[(action, target)] = now
