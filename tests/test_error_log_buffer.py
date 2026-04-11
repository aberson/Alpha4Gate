"""Unit tests for the ErrorLogBuffer ring buffer (Phase 4.5 #68)."""

from __future__ import annotations

import logging
import threading

from alpha4gate.error_log import ErrorLogBuffer


def _make_record(
    message: str = "boom",
    level: int = logging.ERROR,
    name: str = "test.logger",
) -> logging.LogRecord:
    """Build a LogRecord for direct ``emit`` calls in tests."""
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )


def test_empty_snapshot_returns_zero_and_empty_list() -> None:
    buffer = ErrorLogBuffer()
    total, records = buffer.snapshot()
    assert total == 0
    assert records == []


def test_single_emit_increments_count_and_adds_record() -> None:
    buffer = ErrorLogBuffer()
    buffer.emit(_make_record("first error"))
    total, records = buffer.snapshot()
    assert total == 1
    assert len(records) == 1
    assert records[0]["message"] == "first error"
    assert records[0]["level"] == "ERROR"
    assert records[0]["logger"] == "test.logger"
    assert isinstance(records[0]["ts"], str)


def test_ring_buffer_evicts_old_records_fifo() -> None:
    buffer = ErrorLogBuffer()
    for i in range(60):
        buffer.emit(_make_record(f"error {i}"))
    total, records = buffer.snapshot()
    assert total == 60
    assert len(records) == ErrorLogBuffer.MAX_RECENT  # 50
    # Oldest 10 (0..9) should have been evicted; first remaining is #10.
    assert records[0]["message"] == "error 10"
    assert records[-1]["message"] == "error 59"


def test_snapshot_is_a_copy() -> None:
    buffer = ErrorLogBuffer()
    buffer.emit(_make_record("one"))
    _, records = buffer.snapshot()
    records.clear()
    # Mutating the returned list must not affect the buffer.
    total, records2 = buffer.snapshot()
    assert total == 1
    assert len(records2) == 1


def test_reset_clears_count_and_records() -> None:
    buffer = ErrorLogBuffer()
    for i in range(5):
        buffer.emit(_make_record(f"e{i}"))
    buffer.reset()
    total, records = buffer.snapshot()
    assert total == 0
    assert records == []


def test_message_is_truncated_to_500_chars() -> None:
    buffer = ErrorLogBuffer()
    long_msg = "x" * 2000
    buffer.emit(_make_record(long_msg))
    _, records = buffer.snapshot()
    assert len(records[0]["message"]) == 500


def test_emit_handles_bad_format_args() -> None:
    """A LogRecord with mismatched %d args must not crash emit().

    Exercises the fallback path in ``ErrorLogBuffer.emit`` when
    ``record.getMessage()`` raises a ``TypeError`` because the positional
    args don't match the format string. A future refactor that breaks
    the fallback would now fail this test.
    """
    buffer = ErrorLogBuffer()
    record = logging.LogRecord(
        name="test.bad",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="needs an int: %d",
        args=("not-an-int",),  # TypeError in getMessage()
        exc_info=None,
    )
    buffer.emit(record)  # must not raise

    total, records = buffer.snapshot()
    assert total == 1
    assert records[0]["level"] == "ERROR"
    assert records[0]["logger"] == "test.bad"
    # Fallback path uses str(record.msg) so the format string is
    # captured even though %d substitution failed.
    assert "needs an int" in records[0]["message"]


def test_concurrent_emits_from_multiple_threads() -> None:
    buffer = ErrorLogBuffer()
    thread_count = 4
    per_thread = 25

    def worker(tid: int) -> None:
        for i in range(per_thread):
            buffer.emit(_make_record(f"t{tid}-{i}"))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total, records = buffer.snapshot()
    assert total == thread_count * per_thread  # 100
    # Buffer is capped at MAX_RECENT regardless of total count.
    assert len(records) == ErrorLogBuffer.MAX_RECENT
