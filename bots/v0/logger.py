"""Background logger thread: drain queue, write JSONL, support WebSocket broadcast."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Any

_SENTINEL = object()


class GameLogger:
    """Background thread that drains a queue and writes JSONL log entries.

    Usage:
        logger = GameLogger(log_dir=Path("logs"))
        logger.start()
        logger.put(entry_dict)  # from observer.observe()
        ...
        logger.stop()  # flush and close
    """

    def __init__(
        self,
        log_dir: Path,
        broadcast_callback: Any | None = None,
    ) -> None:
        self._log_dir = log_dir
        self._queue: Queue[Any] = Queue()
        self._thread: threading.Thread | None = None
        self._last_step: int = -1
        self._log_path: Path | None = None
        self._broadcast: Any | None = broadcast_callback

    @property
    def log_path(self) -> Path | None:
        """Path to the current log file, or None if not started."""
        return self._log_path

    def start(self) -> None:
        """Start the background writer thread."""
        self._log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
        self._log_path = self._log_dir / f"game_{ts}.jsonl"
        self._last_step = -1
        self._thread = threading.Thread(target=self._run, daemon=True, name="game-logger")
        self._thread.start()

    def put(self, entry: dict[str, Any]) -> None:
        """Enqueue a log entry for writing."""
        self._queue.put(entry)

    def stop(self) -> None:
        """Signal the writer to flush and stop, then wait for it."""
        self._queue.put(_SENTINEL)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _run(self) -> None:
        """Writer loop — runs in background thread."""
        assert self._log_path is not None
        with self._log_path.open("w", encoding="utf-8") as f:
            while True:
                try:
                    entry = self._queue.get(timeout=0.1)
                except Empty:
                    continue

                if entry is _SENTINEL:
                    break

                # Deduplication: skip if we've already seen this game_step
                step = entry.get("game_step", -1)
                if step <= self._last_step:
                    continue
                self._last_step = step

                line = json.dumps(entry, separators=(",", ":"))
                f.write(line + "\n")
                f.flush()

                # Broadcast to WebSocket clients if callback is set
                if self._broadcast is not None:
                    self._broadcast(entry)
