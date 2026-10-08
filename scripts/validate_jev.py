"""Verify one Jev run: its run directory on disk against the dashboard API (Phase JV Step 206).

The run directory is read through ``jev.telemetry``'s validation boundary -- the
same readers the API serves from -- and the same run is fetched from a live
dashboard backend's read-only ``/api/jev`` routes over real HTTP. The verifier
then checks that the two agree:

* identity: run ID, family, version and ``schema_version`` of every document;
* policy hash: the archived ``policy.json`` (recomputed), ``metadata.json``,
  ``state.json``, the API run detail and its metadata, and the API policy (its
  served ``policy_hash`` and the recomputed hash of the document itself);
* sequence: ``state.json``'s ``last_sequence`` and recent events against the
  retained ``events.N.jsonl`` trace -- sequence numbers strictly increase, and
  the trace matches the counts, rotations and drops ``state.json`` reports;
* outcome: the status / result / error combination
  (``jev.contracts.RUN_OUTCOMES``), the API's ``stale`` flag, and the replay a
  run's metadata names.

A run in a terminal status is never written again, so the API's run detail must
equal ``state.json`` field for field. A live run keeps writing between the two
reads: only its identity, sequence order and staleness are compared, and a note
asks for a rerun once it has ended.

Usage, from the repository root (PowerShell)::

    uv run python scripts/validate_jev.py --run-id <run id> --api-base http://localhost:8765
    uv run python scripts/validate_jev.py --run-id <run id> --api-base http://localhost:8765 `
        --json verify.json

``--run-root`` reads runs under another absolute directory (default: the
repository's ``data/jev/runs``, where the runner writes). Every failed check
prints one ``FAIL <code>: <reason>`` line; a final verdict line follows, and
``--json PATH`` also writes the report as JSON for the acceptance bundle. Exit
codes: 0 when every check agrees, 1 when any check failed (or the JSON report
could not be written), 2 for a usage error -- a malformed command line, or an
invalid ``--run-id`` / ``--api-base`` (reported as ``FAIL invalid_run_id`` /
``FAIL invalid_api_base``; nothing is read and no report is written). Failure
codes are stable; see :data:`FAILURE_CODES` and
``documentation/operator/jev-validation.md``.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import time
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Final, Literal, get_args
from urllib.parse import urlsplit

# Ensure the repository's ``src`` is on sys.path so ``jev`` is importable (and is
# this checkout's) when the script is invoked directly. The ``jev`` imports are
# deferred past this setup (E402 waiver).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from jev.api import API_PREFIX, STALE_AFTER_SECONDS  # noqa: E402
from jev.contracts import (  # noqa: E402
    ERROR_CODES,
    MAX_MESSAGE_CHARS,
    RECENT_EVENT_LIMIT,
    SCHEMA_VERSION,
    TERMINAL_RUN_STATUSES,
    Event,
    JsonValue,
    RunMetadata,
    RunState,
    is_run_outcome,
    is_valid_run_id,
    render_text,
    safe_exception_text,
    safe_repr,
)
from jev.policy import MAX_POLICY_BYTES, PolicyError, parse_json_document, policy_hash  # noqa: E402
from jev.runner import (  # noqa: E402
    EXIT_FAILURE,
    EXIT_OK,
    EXIT_USAGE,
    TerminalSafeArgumentParser,
    absolute_path,
    make_streams_encoding_safe,
)
from jev.telemetry import (  # noqa: E402
    MAX_METADATA_BYTES,
    MAX_STATE_BYTES,
    MAX_TRACE_SEGMENTS,
    METADATA_FILE,
    POLICY_ARCHIVE_FILE,
    STATE_FILE,
    CorruptRun,
    MissingRecord,
    PolicyHashMismatch,
    default_run_root,
    read_policy_archive,
    read_run_metadata,
    read_run_state,
    read_trace_segment,
    trace_segment_name,
    utc_timestamp,
)

FailureCode = Literal[
    "invalid_run_id",
    "invalid_api_base",
    "run_not_found",
    "corrupt_run",
    "policy_missing",
    "hash_mismatch",
    "schema_mismatch",
    "sequence_mismatch",
    "corrupt_trace",
    "trace_mismatch",
    "malformed_terminal_result",
    "stale_run",
    "replay_missing",
    "api_unreachable",
    "api_error",
    "api_mismatch",
]
#: Every failure code the verifier reports (documented in the operator guide).
FAILURE_CODES: Final[tuple[FailureCode, ...]] = get_args(FailureCode)
#: Failures of the command line itself: the verifier exits with EXIT_USAGE.
USAGE_FAILURE_CODES: Final[frozenset[FailureCode]] = frozenset(
    {"invalid_run_id", "invalid_api_base"}
)

#: The API answer's own keys beyond the stored records (``stale``, ``metadata``,
#: ``policy_hash``) and the reader's float normalization of stored numbers (an
#: integral ``0`` is served as ``0.0``) fit within this many bytes.
RESPONSE_ENVELOPE_BYTES: Final = 64 * 1024
#: The largest run detail accepted: a whole state plus its metadata (the writer's
#: caps) plus the envelope.
MAX_RUN_RESPONSE_BYTES: Final = MAX_STATE_BYTES + MAX_METADATA_BYTES + RESPONSE_ENVELOPE_BYTES
#: The largest policy answer accepted: the archive's cap plus the envelope.
MAX_POLICY_RESPONSE_BYTES: Final = MAX_POLICY_BYTES + RESPONSE_ENVELOPE_BYTES
#: Each socket operation (connect, send, one receive) may take this long.
HTTP_TIMEOUT_SECONDS: Final = 10.0
#: Reading one answer's body may take this long in all.
HTTP_BODY_DEADLINE_SECONDS: Final = 30.0
#: Longest ``--api-base`` accepted.
MAX_API_BASE_CHARS: Final = 2048

_READ_CHUNK_BYTES: Final = 64 * 1024
#: The RunState fields that never change during a run.
_IDENTITY_FIELDS: Final = ("schema_version", "run_id", "family", "version", "policy_hash")
#: Fields whose disagreement has its own failure code (any other is ``api_mismatch``).
_FIELD_CODES: Final[Mapping[str, FailureCode]] = {
    "schema_version": "schema_mismatch",
    "policy_hash": "hash_mismatch",
    "last_sequence": "sequence_mismatch",
}
_ABSENT: Final = "absent"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _line(text: str) -> str:
    """One terminal-safe line of at most MAX_MESSAGE_CHARS characters."""
    shown = render_text(text)
    if len(shown) > MAX_MESSAGE_CHARS:
        return f"{shown[:MAX_MESSAGE_CHARS]}... (+{len(shown) - MAX_MESSAGE_CHARS} chars)"
    return shown


@dataclass(frozen=True)
class Failure:
    """One failed check: a stable ``code`` and a rendered one-line ``message``."""

    code: FailureCode
    message: str


@dataclass
class Report:
    """The outcome of verifying one run.

    ``run`` and ``trace`` hold facts for the acceptance report, taken from the
    validated disk records (empty when they could not be read).
    """

    run_id: str | None
    api_base: str | None
    run_dir: str | None
    failures: list[Failure] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    run: dict[str, JsonValue] = field(default_factory=dict)
    trace: dict[str, JsonValue] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.failures

    def fail(self, code: FailureCode, message: str) -> None:
        self.failures.append(Failure(code, _line(message)))

    def note(self, message: str) -> None:
        self.notes.append(_line(message))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": SCHEMA_VERSION,
            "tool": "validate_jev",
            "checked_at": utc_timestamp(time.time()),
            "verdict": "pass" if self.passed else "fail",
            "run_id": self.run_id,
            "api_base": self.api_base,
            "run_dir": self.run_dir,
            "run": dict(self.run),
            "trace": dict(self.trace),
            "failures": [{"code": f.code, "message": f.message} for f in self.failures],
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Disk side: jev.telemetry's readers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Disk:
    run_dir: Path
    metadata: RunMetadata
    state: RunState | None


def _reader_code(exc: CorruptRun, *, missing: FailureCode = "corrupt_run") -> FailureCode:
    if isinstance(exc, PolicyHashMismatch):
        return "hash_mismatch"
    if isinstance(exc, MissingRecord):
        return missing
    return "corrupt_run"


def _read_disk(run_dir: Path, report: Report) -> _Disk | None:
    """The run's validated metadata and state; every reader defect becomes a failure."""
    try:
        metadata = read_run_metadata(run_dir)
    except CorruptRun as exc:
        report.fail("corrupt_run", f"on disk, {exc.message}")
        return None
    if metadata is None:
        report.fail(
            "run_not_found",
            f"no run directory with {METADATA_FILE} at {safe_repr(str(run_dir))} "
            "(wrong --run-id or --run-root?)",
        )
        return None
    state: RunState | None = None
    try:
        state = read_run_state(run_dir, metadata)
    except CorruptRun as exc:
        report.fail(_reader_code(exc), f"on disk, {exc.message}")
    try:
        read_policy_archive(run_dir, metadata)
    except CorruptRun as exc:
        report.fail(_reader_code(exc, missing="policy_missing"), f"on disk, {exc.message}")
    return _Disk(run_dir, metadata, state)


def _check_outcome(state: RunState, report: Report) -> None:
    code = None if state.error is None else state.error.code
    if not is_run_outcome(state.status, state.result, code):
        report.fail(
            "malformed_terminal_result",
            f"{STATE_FILE} records status {state.status!r} with result {state.result!r} and "
            f"error {code!r}: a combination the run contract does not allow",
        )


def _check_recent_events(state: RunState, report: Report) -> None:
    sequences = [event.sequence for event in state.recent_events]
    if any(later <= earlier for earlier, later in pairwise(sequences)):
        report.fail(
            "sequence_mismatch", f"{STATE_FILE} recent_events are not in increasing sequence order"
        )
    elif sequences and sequences[-1] > state.last_sequence:
        report.fail(
            "sequence_mismatch",
            f"{STATE_FILE} holds recent event {sequences[-1]} beyond its last_sequence "
            f"{state.last_sequence}",
        )


def _check_trace(run_dir: Path, state: RunState, *, settled: bool, report: Report) -> None:
    """The retained trace against ``state``'s own trace bookkeeping and recent events.

    Only the newest :data:`MAX_TRACE_SEGMENTS` segments can exist (older ones are
    deleted and counted by the writer). A live run (``settled`` False) keeps
    appending, so only the sequence order is checked; a settled run's trace must
    end at or before ``last_sequence`` and, when the writer reports it complete,
    hold exactly the counted events, ending with the state's recent events.
    """
    stats = state.trace
    retained = 0
    torn = 0
    tail: deque[Event] = deque(maxlen=RECENT_EVENT_LIMIT)
    previous: int | None = None
    ordered = True
    for number in range(max(1, stats.segment - MAX_TRACE_SEGMENTS + 1), stats.segment + 1):
        try:
            segment = read_trace_segment(run_dir, number)
        except CorruptRun as exc:
            report.fail("corrupt_trace", f"on disk, {exc.message}")
            return
        if segment is None:
            continue
        if segment.torn_tail:
            torn += 1
        for event in segment.events:
            if ordered and previous is not None and event.sequence <= previous:
                report.fail(
                    "sequence_mismatch",
                    f"{trace_segment_name(number)} has sequence {event.sequence} after "
                    f"{previous}: trace sequence numbers must increase",
                )
                ordered = False
            previous = event.sequence
        retained += len(segment.events)
        tail.extend(segment.events)
    report.trace = {**stats.to_dict(), "retained_events": retained, "torn_segments": torn}
    if torn:
        report.note(f"{torn} trace segment(s) end in an interrupted append (ignored, by design)")
    if not stats.complete:
        report.note(
            f"the trace is incomplete: {stats.dropped_events} event(s) dropped, "
            f"{stats.dropped_segments} old segment(s) deleted, {stats.rotated_segments} "
            "rotation(s), as the writer reports"
        )
    if not settled:
        return
    if previous is not None and previous > state.last_sequence:
        report.fail(
            "sequence_mismatch",
            f"the trace reaches sequence {previous} but {STATE_FILE} last_sequence is "
            f"{state.last_sequence}",
        )
    if not stats.complete:
        return
    if stats.dropped_segments or stats.dropped_events:
        report.fail(
            "trace_mismatch",
            f"{STATE_FILE} reports a complete trace yet counts dropped segments or events",
        )
    elif stats.segment > MAX_TRACE_SEGMENTS:
        report.note(
            f"the trace reports no drops but spans {stats.segment} segments, more than the "
            f"{MAX_TRACE_SEGMENTS} retained: its event count was not checked"
        )
    elif retained != stats.events:
        report.fail(
            "trace_mismatch",
            f"the retained trace holds {retained} event(s); {STATE_FILE} counts {stats.events}",
        )
    recent = state.recent_events
    expected = min(RECENT_EVENT_LIMIT, stats.events)
    last = tuple(tail)[len(tail) - len(recent) :] if len(tail) >= len(recent) else None
    if len(recent) != expected or last != recent:
        report.fail(
            "trace_mismatch",
            f"{STATE_FILE} recent_events are not the last {expected} trace event(s)",
        )


def _check_replay(disk: _Disk, report: Report) -> None:
    replay = disk.metadata.replay_path  # the reader admits only REPLAY_FILE or None
    if replay is None:
        report.note("no replay recorded (burnysc2 saves one only when a match ends in SC2)")
        return
    path = disk.run_dir / replay
    try:
        size = path.stat().st_size if path.is_file() else 0
    except OSError:
        size = 0
    if size == 0:
        report.fail(
            "replay_missing", f"{METADATA_FILE} names replay {replay!r}, but it is absent or empty"
        )


def _record_facts(disk: _Disk, report: Report) -> None:
    metadata = disk.metadata
    facts: dict[str, JsonValue] = {
        "family": metadata.family,
        "version": metadata.version,
        "policy_hash": metadata.policy_hash,
        "created_at": metadata.created_at,
        "source_commit": metadata.source_commit,
        "map": metadata.map,
        "opponent_race": metadata.opponent_race,
        "difficulty": metadata.difficulty,
        "seed": metadata.seed,
        "max_game_seconds": metadata.max_game_seconds,
        "max_wall_seconds": metadata.max_wall_seconds,
        "replay_path": metadata.replay_path,
    }
    state = disk.state
    if state is not None:
        facts.update(
            status=state.status,
            result=state.result,
            error_code=None if state.error is None else state.error.code,
            updated_at=state.updated_at,
            game_seconds=state.game_seconds,
            last_sequence=state.last_sequence,
        )
    report.run.update(facts)


# ---------------------------------------------------------------------------
# API side: one stdlib HTTP connection, no redirects, no proxies
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApiBase:
    """A validated ``--api-base``: ``http(s)://host[:port]``, the port made explicit."""

    scheme: Literal["http", "https"]
    host: str
    port: int

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}"


def parse_api_base(text: str) -> ApiBase | None:
    """``text`` as an http(s) origin -- scheme, host, optional port, nothing else -- or None."""
    if len(text) > MAX_API_BASE_CHARS or not text.isascii() or not text.isprintable():
        return None
    if any(char.isspace() for char in text):
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:  # e.g. a non-numeric or out-of-range port
        return None
    if port == 0:
        return None
    if parts.scheme == "http":
        scheme: Literal["http", "https"] = "http"
    elif parts.scheme == "https":
        scheme = "https"
    else:
        return None
    host = parts.hostname
    if not host or parts.username is not None or parts.password is not None:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    try:
        host.encode("idna")  # what name resolution does (an empty or overlong label fails)
    except UnicodeError:
        return None
    # An explicit port also keeps http.client from reading a bare IPv6 host's last
    # group as a port.
    default_port = http.client.HTTPS_PORT if scheme == "https" else http.client.HTTP_PORT
    return ApiBase(scheme, host, default_port if port is None else port)


class _ApiFailure(Exception):
    def __init__(self, code: FailureCode, message: str) -> None:
        super().__init__(message)
        self.code: FailureCode = code
        self.message = message


@dataclass(frozen=True)
class _Answer:
    status: int
    #: The body as a strict JSON object, or None when it is not one.
    document: Mapping[str, object] | None


class _ApiClient:
    """GET requests over one connection; ``http.client`` follows no redirects and
    uses no proxy, so every answer comes from ``--api-base`` itself."""

    def __init__(self, base: ApiBase) -> None:
        self._base = base
        connection = (
            http.client.HTTPSConnection if base.scheme == "https" else http.client.HTTPConnection
        )
        self._connection = connection(base.host, base.port, timeout=HTTP_TIMEOUT_SECONDS)

    def close(self) -> None:
        self._connection.close()

    def get(self, path: str, max_bytes: int) -> _Answer:
        """One answer; raises :class:`_ApiFailure` when none (or no bounded one) arrives."""
        try:
            self._connection.request("GET", path, headers={"Accept": "application/json"})
            response = self._connection.getresponse()
            body = self._read_body(response, path, max_bytes)
        except _ApiFailure:
            self.close()
            raise
        except OSError as exc:  # refused, unresolvable, timed out, reset
            self.close()
            raise _ApiFailure(
                "api_unreachable",
                f"no answer from {self._base.url} to GET {path}: {safe_exception_text(exc)}",
            ) from exc
        except http.client.HTTPException as exc:  # not an HTTP/1.x answer
            self.close()
            raise _ApiFailure(
                "api_error",
                f"{self._base.url} sent an invalid or incomplete HTTP answer to GET {path}: "
                f"{safe_repr(type(exc).__name__)}",
            ) from exc
        try:
            document: Mapping[str, object] | None = parse_json_document(
                body, what=f"GET {path}", max_bytes=max_bytes
            )
        except PolicyError:  # the strict parser's only exception
            document = None
        return _Answer(response.status, document)

    def _read_body(self, response: http.client.HTTPResponse, path: str, max_bytes: int) -> bytes:
        deadline = time.monotonic() + HTTP_BODY_DEADLINE_SECONDS
        chunks: list[bytes] = []
        size = 0
        while True:
            if time.monotonic() > deadline:
                raise _ApiFailure(
                    "api_unreachable",
                    f"the answer to GET {path} took longer than {HTTP_BODY_DEADLINE_SECONDS:g} s",
                )
            chunk = response.read1(_READ_CHUNK_BYTES)
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > max_bytes:
                raise _ApiFailure(
                    "api_error", f"the answer to GET {path} is larger than {max_bytes} bytes"
                )
            chunks.append(chunk)


def _describe(answer: _Answer) -> str:
    """An unexpected answer, for a failure message (its untrusted text capped)."""
    error = None if answer.document is None else answer.document.get("error")
    if isinstance(error, dict):
        code, message = error.get("code"), error.get("message")
        if type(code) is str and type(message) is str:
            shown = code if code in ERROR_CODES else safe_repr(code)
            return f"HTTP {answer.status} {shown}: {safe_repr(message)}"
    if 300 <= answer.status < 400:
        return f"HTTP {answer.status}, a redirect (redirects are not followed)"
    if answer.status == 404:
        return (
            "HTTP 404 without a Jev error document (no /api/jev routes here: is this the "
            "dashboard backend, uv run python -m bots.current.runner --serve?)"
        )
    return f"HTTP {answer.status} without a Jev error document"


@dataclass(frozen=True)
class _Api:
    detail: Mapping[str, object] | None
    policy: Mapping[str, object] | None


def _read_api(base: ApiBase, run_id: str, report: Report) -> _Api | None:
    """The run detail and archived policy as the API serves them."""
    client = _ApiClient(base)
    detail_path = f"{API_PREFIX}/runs/{run_id}"
    policy_path = f"{detail_path}/policy"
    try:
        detail = client.get(detail_path, MAX_RUN_RESPONSE_BYTES)
        policy = client.get(policy_path, MAX_POLICY_RESPONSE_BYTES)
    except _ApiFailure as failure:
        report.fail(failure.code, failure.message)
        return None
    finally:
        client.close()
    detail_document: Mapping[str, object] | None = None
    if detail.status != 200:
        report.fail("api_error", f"GET {detail_path} answered {_describe(detail)}")
    elif detail.document is None:
        report.fail("api_error", f"the answer to GET {detail_path} is not a strict JSON object")
    else:
        detail_document = detail.document
    policy_document: Mapping[str, object] | None = None
    if policy.status != 200:
        report.fail(
            "policy_missing",
            f"the API served no policy: GET {policy_path} answered {_describe(policy)}",
        )
    elif policy.document is None:
        report.fail("api_error", f"the answer to GET {policy_path} is not a strict JSON object")
    else:
        policy_document = policy.document
    return _Api(detail_document, policy_document)


# ---------------------------------------------------------------------------
# Disk against API
# ---------------------------------------------------------------------------


def _same_json(left: object, right: object) -> bool:
    """Strict JSON equality: types must match too (``True`` is not ``1``, ``1`` not ``1.0``).

    Both sides come from the strict JSON parser or a contract's ``to_dict``, so
    nesting is bounded by ``jev.policy.MAX_JSON_NESTING``.
    """
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        assert isinstance(right, dict)
        return left.keys() == right.keys() and all(_same_json(left[k], right[k]) for k in left)
    if isinstance(left, list):
        assert isinstance(right, list)
        return len(left) == len(right) and all(map(_same_json, left, right))
    return left == right


def _shown(document: Mapping[str, object], key: str) -> str:
    return safe_repr(document[key]) if key in document else _ABSENT


def _compare_fields(
    report: Report,
    what: str,
    served: Mapping[str, object],
    expected: Mapping[str, JsonValue],
    keys: Iterable[str],
    source: str,
) -> None:
    """Report each of ``keys`` on which ``served`` differs from the disk record ``expected``."""
    others: list[str] = []
    for key in keys:
        if key in served and _same_json(served[key], expected[key]):
            continue
        code = _FIELD_CODES.get(key)
        if code is None:
            others.append(key)
            continue
        report.fail(
            code,
            f"{what} {key} is {_shown(served, key)}; {source} has {safe_repr(expected[key])}",
        )
    if others:
        report.fail("api_mismatch", f"{what} disagrees with {source} on: {', '.join(others)}")


def _compare_state(served: Mapping[str, object], state: RunState, report: Report) -> None:
    """The API's RunState fields against ``state.json`` (additive fields are allowed)."""
    expected = state.to_dict()
    if state.status in TERMINAL_RUN_STATUSES:
        _compare_fields(report, "the API run detail", served, expected, expected, STATE_FILE)
        return
    _compare_fields(report, "the API run detail", served, expected, _IDENTITY_FIELDS, STATE_FILE)
    sequence = served.get("last_sequence")
    if type(sequence) is not int:
        report.fail("api_error", "the API run detail has no integer last_sequence")
    elif sequence < state.last_sequence:
        report.fail(
            "sequence_mismatch",
            f"the API run detail last_sequence {sequence} is behind {STATE_FILE}'s "
            f"{state.last_sequence}, which was read before it",
        )
    error = served.get("error")
    code = error.get("code") if isinstance(error, dict) else error
    if not is_run_outcome(served.get("status"), served.get("result"), code):
        report.fail(
            "malformed_terminal_result",
            f"the API run detail records status {_shown(served, 'status')} with result "
            f"{_shown(served, 'result')} and error {safe_repr(code)}",
        )


def _compare_metadata(served: Mapping[str, object], disk: _Disk, report: Report) -> None:
    expected = disk.metadata.to_dict()
    settled = disk.state is not None and disk.state.status in TERMINAL_RUN_STATUSES
    # A live run's replay_path is filled in at its end, possibly between the two reads.
    keys = [key for key in expected if settled or key != "replay_path"]
    _compare_fields(report, "the API run metadata", served, expected, keys, METADATA_FILE)


def _check_stale(detail: Mapping[str, object], report: Report) -> None:
    stale = detail.get("stale")
    status = detail.get("status")
    if type(stale) is not bool:
        report.fail("api_error", "the API run detail has no boolean 'stale' flag")
        return
    report.run["stale"] = stale
    terminal = type(status) is str and status in TERMINAL_RUN_STATUSES
    if stale and terminal:
        report.fail("api_mismatch", f"the API marks a run with terminal status {status!r} stale")
    elif stale:
        report.fail(
            "stale_run",
            f"the run is {safe_repr(status)} but its producer has not written {STATE_FILE} for "
            f"over {STALE_AFTER_SECONDS:g} wall-clock seconds: it crashed or was killed (a run "
            "still launching SC2 also reads stale until its first game step)",
        )


def _check_policy(served: Mapping[str, object], disk: _Disk, report: Report) -> None:
    anchor = disk.metadata.policy_hash
    if not _same_json(served.get("schema_version"), SCHEMA_VERSION):
        report.fail(
            "schema_mismatch",
            f"the API policy schema_version is {_shown(served, 'schema_version')}, "
            f"not {SCHEMA_VERSION}",
        )
    if served.get("policy_hash") != anchor:
        report.fail(
            "hash_mismatch",
            f"the API policy policy_hash is {_shown(served, 'policy_hash')}; "
            f"{METADATA_FILE} has {anchor!r}",
        )
    document = {key: value for key, value in served.items() if key != "policy_hash"}
    try:
        digest = policy_hash(document)
    except PolicyError:
        report.fail("api_error", "the API policy is not a JSON policy document")
        return
    if digest != anchor:
        report.fail(
            "hash_mismatch",
            f"the API policy document hashes to {digest!r}; {METADATA_FILE} and "
            f"{POLICY_ARCHIVE_FILE} have {anchor!r}",
        )


def _compare(disk: _Disk, api: _Api, report: Report) -> None:
    if api.detail is not None:
        served_metadata = api.detail.get("metadata")
        if isinstance(served_metadata, dict):
            _compare_metadata(served_metadata, disk, report)
        else:
            report.fail("api_error", "the API run detail carries no metadata object")
        if disk.state is not None:
            _compare_state(api.detail, disk.state, report)
    if api.policy is not None:
        _check_policy(api.policy, disk, report)


# ---------------------------------------------------------------------------
# Verification and command line
# ---------------------------------------------------------------------------


def verify(run_id: str, api_base: str, run_root: Path) -> Report:
    """Verify run ``run_id`` under ``run_root`` against the API at ``api_base``.

    Never raises for hostile input: every defect is a :class:`Failure`.
    """
    valid_id = is_valid_run_id(run_id)
    base = parse_api_base(api_base)
    run_dir = run_root / run_id if valid_id else None
    report = Report(
        run_id=run_id if valid_id else None,
        api_base=None if base is None else base.url,
        run_dir=None if run_dir is None else str(run_dir),
    )
    if not valid_id:
        report.fail(
            "invalid_run_id",
            f"--run-id {safe_repr(run_id)} is not a lowercase UUID4 hex run id "
            "(32 hex digits, as the runner prints it)",
        )
    if base is None:
        report.fail(
            "invalid_api_base",
            f"--api-base {safe_repr(api_base)} is not an http(s)://host[:port] origin "
            "(no path, query, fragment or credentials)",
        )
    if run_dir is None or base is None:
        return report
    disk = _read_disk(run_dir, report)
    api = _read_api(base, run_id, report)
    state = None if disk is None else disk.state
    if disk is not None:
        _record_facts(disk, report)
        _check_replay(disk, report)
    if state is not None:
        _check_outcome(state, report)
        _check_recent_events(state, report)
        settled = state.status in TERMINAL_RUN_STATUSES
        _check_trace(run_dir, state, settled=settled, report=report)
    if api is not None and api.detail is not None:
        _check_stale(api.detail, report)
    if disk is not None and api is not None:
        _compare(disk, api, report)
    if state is not None and state.status not in TERMINAL_RUN_STATUSES:
        if report.run.get("stale") is False:
            report.note(
                f"the run is live (status {state.status!r} on disk): only its identity, "
                "sequence order and staleness can be compared while it writes; rerun once it "
                "has ended"
            )
    return report


def build_parser() -> argparse.ArgumentParser:
    """The command-line parser (its help is what the operator guide quotes)."""
    parser = TerminalSafeArgumentParser(
        prog="python scripts/validate_jev.py",
        description=(
            "Verify one Jev run: compare its run directory on disk with what the dashboard "
            "API serves for it (policy hash, identity, schema, sequence, result, staleness)."
        ),
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="the run's lowercase UUID4 hex id (the runner prints it as run_id=...)",
    )
    parser.add_argument(
        "--api-base",
        required=True,
        help="the dashboard backend's origin, e.g. http://localhost:8765",
    )
    parser.add_argument(
        "--run-root",
        type=absolute_path,
        default=None,
        help="read the run under this absolute directory (default: <repository>/data/jev/runs)",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        metavar="PATH",
        help="also write the report as JSON to PATH (for the acceptance bundle)",
    )
    return parser


def _print_report(report: Report) -> None:
    if report.run_dir is not None:
        print(_line(f"validate_jev: run {report.run_id} at {report.run_dir}"))
    if report.api_base is not None:
        print(f"validate_jev: API {report.api_base}")
    run = report.run
    if "status" in run:
        print(
            _line(
                f"validate_jev: status={run['status']} result={run['result']} "
                f"error={run['error_code']} game_seconds={run['game_seconds']} "
                f"last_sequence={run['last_sequence']} seed={run['seed']} "
                f"policy_hash={run['policy_hash']}"
            )
        )
    trace = report.trace
    if trace:
        print(
            f"validate_jev: trace segment {trace['segment']}, {trace['retained_events']} "
            f"retained of {trace['events']} event(s), complete={trace['complete']}"
        )
    for note in report.notes:
        print(f"note: {note}")
    for failure in report.failures:
        print(f"FAIL {failure.code}: {failure.message}")
    if report.passed:
        print("validate_jev: PASS - disk and API agree on this run")
    else:
        codes = ", ".join(dict.fromkeys(f.code for f in report.failures))
        print(f"validate_jev: FAIL - {len(report.failures)} failed check(s): {codes}")


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv``, verify the run, print the report; return the exit code."""
    make_streams_encoding_safe()
    args = build_parser().parse_args(argv)
    run_root = default_run_root() if args.run_root is None else args.run_root
    report = verify(args.run_id, args.api_base, run_root)
    _print_report(report)
    if any(failure.code in USAGE_FAILURE_CODES for failure in report.failures):
        return EXIT_USAGE  # nothing was verified, so no report is written
    if args.json is not None:
        text = json.dumps(report.to_dict(), indent=2, ensure_ascii=True, allow_nan=False)
        try:
            args.json.write_text(text + "\n", encoding="ascii")
        except OSError as exc:
            print(
                _line(
                    f"validate_jev: cannot write the JSON report to {safe_repr(str(args.json))}: "
                    f"{safe_exception_text(exc)}"
                ),
                file=sys.stderr,
            )
            return EXIT_FAILURE
    return EXIT_OK if report.passed else EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
