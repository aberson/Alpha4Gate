"""Build backlog: retries failed build requests when resources become available."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class BacklogEntry:
    """A single failed build request stored for retry."""

    structure_type: str
    location: tuple[float, float]
    reason: str
    enqueued_time: float  # game-seconds when the entry was added
    expiry_seconds: float = 120.0  # per-entry expiry


# Type alias for the affordability check callback.
# Takes (structure_type, location) and returns True if the build can be attempted.
AffordabilityCheck = Callable[[str, tuple[float, float]], bool]


class BuildBacklog:
    """Stores failed build requests and retries them when affordable.

    Items expire after ``expiry_seconds`` game-seconds (default 120).
    The backlog is capped at ``max_size`` items (default 6).
    Each game step, call ``tick()`` with the current game time and an
    affordability checker to retry the oldest affordable entry.
    """

    DEFAULT_EXPIRY_SECONDS: float = 120.0
    DEFAULT_MAX_SIZE: int = 6
    PRIORITY_STRUCTURES: set[str] = {"PYLON"}

    def __init__(
        self,
        expiry_seconds: float = DEFAULT_EXPIRY_SECONDS,
        max_size: int = DEFAULT_MAX_SIZE,
    ) -> None:
        self._entries: list[BacklogEntry] = []
        self._expiry_seconds = expiry_seconds
        self._max_size = max_size

    @property
    def entries(self) -> list[BacklogEntry]:
        """Current backlog entries (oldest first)."""
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def add(
        self,
        structure_type: str,
        location: tuple[float, float],
        reason: str,
        game_time: float,
    ) -> bool:
        """Add a failed build request to the backlog.

        Returns True if the entry was added, False if the backlog is full.
        """
        if len(self._entries) >= self._max_size:
            return False
        # Pylons get longer expiry window so they have more retry attempts.
        effective_expiry = self._expiry_seconds
        if structure_type in self.PRIORITY_STRUCTURES:
            effective_expiry = self._expiry_seconds * 1.5
        self._entries.append(
            BacklogEntry(
                structure_type=structure_type,
                location=location,
                reason=reason,
                enqueued_time=game_time,
                expiry_seconds=effective_expiry,
            )
        )
        return True

    def tick(
        self,
        game_time: float,
        can_afford: AffordabilityCheck,
    ) -> BacklogEntry | None:
        """Expire old entries, then retry the oldest affordable entry.

        Args:
            game_time: Current game time in seconds.
            can_afford: Callback that returns True if we can build the entry now.

        Returns:
            The entry that should be retried, or None if nothing is ready.
        """
        # Purge expired entries (using per-entry expiry)
        self._entries = [
            e
            for e in self._entries
            if (game_time - e.enqueued_time) < e.expiry_seconds
        ]

        # Sort so priority structures come first (stable sort preserves age order)
        self._entries.sort(
            key=lambda e: 0 if e.structure_type in self.PRIORITY_STRUCTURES else 1
        )

        # Try the oldest affordable entry
        for i, entry in enumerate(self._entries):
            if can_afford(entry.structure_type, entry.location):
                self._entries.pop(i)
                return entry

        return None

    def clear(self) -> None:
        """Remove all entries."""
        self._entries.clear()
