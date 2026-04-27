"""In-memory ring buffer for ERROR-level log records.

The alerts pipeline (Phase 4.5 #68) needs a cheap way to surface backend
errors to the dashboard so operators can trust the Alerts tab during
unattended soak runs. A bounded ring buffer of recent ERROR/CRITICAL
records is attached to the root logger at app startup and exposed via
``/api/training/status`` alongside a running total count.

The module is intentionally dependency-free so tests can exercise it
without spinning up the FastAPI app.
"""

from __future__ import annotations

import collections
import logging
import threading
from datetime import UTC, datetime
from typing import Any

# Maximum number of characters kept per log message. Tracebacks can be
# multi-KB; 500 chars is enough to identify the failure while bounding
# the total memory footprint of the buffer.
_MAX_MESSAGE_CHARS = 500


class ErrorLogBuffer:
    """Thread-safe ring buffer of recent ERROR-level log records.

    Used by the alerts pipeline to surface backend errors to the
    dashboard. Capped at ``MAX_RECENT`` (50) to bound memory; the
    running ``_count_since_start`` is unbounded so the frontend can
    detect fresh errors even after FIFO eviction.
    """

    MAX_RECENT = 50

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: collections.deque[dict[str, Any]] = collections.deque(maxlen=self.MAX_RECENT)
        self._count_since_start = 0

    def emit(self, record: logging.LogRecord) -> None:
        """Append a single log record to the buffer.

        Defensive: catches any failure in ``record.getMessage()`` or
        dict construction and falls back to a minimal record so the
        logging pipeline never raises on bad inputs.
        """
        try:
            message = record.getMessage()
        except Exception:
            message = str(getattr(record, "msg", ""))

        try:
            if len(message) > _MAX_MESSAGE_CHARS:
                message = message[:_MAX_MESSAGE_CHARS]
            entry: dict[str, Any] = {
                "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": message,
            }
        except Exception:  # pragma: no cover - fallback path
            entry = {
                "ts": datetime.now(UTC).isoformat(),
                "level": getattr(record, "levelname", "ERROR"),
                "logger": getattr(record, "name", "unknown"),
                "message": str(getattr(record, "msg", ""))[:_MAX_MESSAGE_CHARS],
            }

        with self._lock:
            self._records.append(entry)
            self._count_since_start += 1

    def snapshot(self) -> tuple[int, list[dict[str, Any]]]:
        """Return ``(total_count_since_start, list of recent records)``.

        The returned list is a fresh copy — mutating it does not affect
        the buffer's internal state.
        """
        with self._lock:
            return self._count_since_start, list(self._records)

    def reset(self) -> None:
        """Clear both the record deque and the running count.

        Intended for tests; production code should never call this.
        """
        with self._lock:
            self._records.clear()
            self._count_since_start = 0


# Module-level singleton. The FastAPI app and the root-logger handler
# both read from this instance, so there is one buffer per process.
_error_log_buffer = ErrorLogBuffer()


def get_error_log_buffer() -> ErrorLogBuffer:
    """Return the process-wide ``ErrorLogBuffer`` singleton."""
    return _error_log_buffer


class _ErrorBufferHandler(logging.Handler):
    """Logging handler that forwards ERROR+ records to the ring buffer.

    Installed on the root logger by :func:`install_error_log_handler`
    during FastAPI startup. The handler is level-filtered to
    ``logging.ERROR`` so INFO/DEBUG/WARNING records are ignored and
    never hit the buffer.
    """

    def __init__(self, buffer: ErrorLogBuffer) -> None:
        super().__init__(level=logging.ERROR)
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._buffer.emit(record)
        except Exception:  # pragma: no cover - never let logging crash the app
            self.handleError(record)


# Guard flag so repeated ``--serve`` invocations in tests do not stack
# multiple handlers on the root logger.
_handler_installed = False


def install_error_log_handler() -> None:
    """Attach ``_ErrorBufferHandler`` to the root logger, once.

    Idempotent: subsequent calls are no-ops. Safe to call from the
    FastAPI startup path even if the test harness has already installed
    the handler in an earlier test.
    """
    global _handler_installed
    root = logging.getLogger()
    # Belt-and-braces: check both the module-level flag AND the actual
    # handler list so a test that resets the flag still cannot double-
    # install the handler.
    if _handler_installed:
        return
    for existing in root.handlers:
        if isinstance(existing, _ErrorBufferHandler):
            _handler_installed = True
            return
    handler = _ErrorBufferHandler(_error_log_buffer)
    root.addHandler(handler)
    _handler_installed = True
