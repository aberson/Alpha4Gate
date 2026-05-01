from __future__ import annotations

import threading

from bots.v6.commands.primitives import CommandAction, CommandPrimitive, CommandSource


class CommandQueue:
    """Thread-safe priority command queue with TTL and overflow eviction."""

    def __init__(self, max_depth: int = 10) -> None:
        self._max_depth = max_depth
        self._queue: list[CommandPrimitive] = []
        self._lock = threading.Lock()

    def push(self, cmd: CommandPrimitive) -> None:
        """Add a command. Evict lowest-priority if full (AI before human at equal priority)."""
        with self._lock:
            if len(self._queue) >= self._max_depth:
                self._evict_one()
            self._queue.append(cmd)

    def _evict_one(self) -> None:
        """Evict the lowest-priority command. At equal priority, evict AI before human."""
        if not self._queue:
            return
        worst_idx = 0
        worst = self._queue[0]
        for i, cmd in enumerate(self._queue[1:], start=1):
            if self._eviction_key(cmd) < self._eviction_key(worst):
                worst_idx = i
                worst = cmd
        self._queue.pop(worst_idx)

    @staticmethod
    def _eviction_key(cmd: CommandPrimitive) -> tuple[int, int]:
        """Lower key = evicted first. (priority, source_rank) where AI=0, HUMAN=1."""
        source_rank = 1 if cmd.source == CommandSource.HUMAN else 0
        return (cmd.priority, source_rank)

    def drain(self, game_time: float) -> list[CommandPrimitive]:
        """Return all non-expired commands sorted by priority (highest first), clear queue."""
        with self._lock:
            alive = [
                cmd
                for cmd in self._queue
                if cmd.timestamp + cmd.ttl > game_time
            ]
            self._queue.clear()
        alive.sort(key=lambda c: c.priority, reverse=True)
        return alive

    def clear(self, source: CommandSource | None = None) -> list[CommandPrimitive]:
        """Clear all commands or only those from a specific source. Return cleared commands."""
        with self._lock:
            if source is None:
                cleared = list(self._queue)
                self._queue.clear()
            else:
                cleared = [cmd for cmd in self._queue if cmd.source == source]
                self._queue = [cmd for cmd in self._queue if cmd.source != source]
        return cleared

    def clear_conflicting(self, action: CommandAction) -> list[CommandPrimitive]:
        """Clear AI commands with the given action. Return cleared commands."""
        with self._lock:
            cleared = [
                cmd
                for cmd in self._queue
                if cmd.source == CommandSource.AI and cmd.action == action
            ]
            self._queue = [
                cmd
                for cmd in self._queue
                if not (cmd.source == CommandSource.AI and cmd.action == action)
            ]
        return cleared

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._queue)

    @property
    def pending(self) -> list[CommandPrimitive]:
        """Snapshot of queued commands."""
        with self._lock:
            return list(self._queue)


_command_queue: CommandQueue | None = None


def get_command_queue() -> CommandQueue:
    global _command_queue
    if _command_queue is None:
        _command_queue = CommandQueue(max_depth=10)
    return _command_queue
