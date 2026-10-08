"""Process-independent Jev run evidence (plan D5): the run-directory writer and reader.

One game process writes one directory per run, ``<run root>/<run_id>/``:

* ``policy.json`` -- the loaded policy bytes, archived once and never rewritten, so
  a viewer reads the policy the run executed, not a later edit of the source;
* ``state.json`` -- the :class:`~jev.contracts.RunState` heartbeat;
* ``metadata.json`` -- :class:`~jev.contracts.RunMetadata` provenance, written at
  the start with ``replay_path`` null and rewritten once at the end if burnysc2
  saved the replay, which it writes itself as ``replay.SC2Replay`` in the run
  directory (``replay_path`` is the only field that ever changes);
* ``events.N.jsonl`` -- the bounded JSONL trace, one event per line.

The default run root is ``<repository>/data/jev/runs`` (:func:`default_run_root`),
derived from this module's path, never the caller's working directory; the
runner's ``--run-root`` overrides it.

**Writing** (:class:`RunRecorder`). :meth:`RunRecorder.start` creates the run
directory (an existing directory is never reused) and writes the policy archive,
a ``starting`` state and the metadata, in that order, so a run that has metadata
has all three records. Then:

* the state is rewritten at most twice per wall-clock second
  (:data:`MIN_STATE_INTERVAL_SECONDS`) while the run is live, plus once at the end;
* a whole-file record goes to a same-directory ``.tmp`` file that
  :func:`os.replace` moves over the target, retried after each of
  :data:`SHARING_RETRY_DELAYS` when Windows reports a sharing violation
  (``PermissionError``: a reader has the target open). Readers see the previous
  or the next document, never a torn one;
* the trace records what changed, not every evaluation: every command, task and
  diagnostic event; a node event only when that node's status or deciding child
  (its branch) differs from the last one traced for it; and once per game second
  each root lane's evaluation, as a summary. A RunState keeps the latest
  :data:`~jev.contracts.RECENT_EVENT_LIMIT` traced events and at most
  :data:`~jev.contracts.MAX_ACTIVE_TASKS` tasks (``tasks_omitted`` counts the rest);
* trace lines are appended in one batch per update. A segment rotates to
  ``events.{N+1}.jsonl`` before it would exceed :data:`MAX_TRACE_SEGMENT_BYTES`
  and only the newest :data:`MAX_TRACE_SEGMENTS` are kept; deleted segments and
  their events are counted in ``RunState.trace``. Every complete trace line ends
  with a newline: a final fragment without one is the tail of an interrupted
  append and readers must ignore it. Before each append the writer compares the
  segment's size with the bytes it completed: a longer file is cut back to the
  last complete line, a shorter (or uncuttable) one is left behind for a fresh
  segment, so a torn tail never merges with later lines and never touches
  ``state.json``. The events of a failed append are counted as dropped;
* the policy archive, state and metadata are mandatory: when one cannot be
  written after the retries, :class:`PersistenceFailed` (``persistence_failed``)
  is raised, the run stops, and ``RunState.trace.complete`` turns False. Prior
  runs are never deleted.

**Reading.** :func:`read_run_metadata`, :func:`read_run_state`,
:func:`read_run_summary` and :func:`read_policy_archive` are the one validation
boundary for stored records. A record that is not a regular file directly inside
the run directory, is larger than its cap, is not strict JSON, has an unsupported
``schema_version``, has a missing, unexpected or ill-typed field, or disagrees
with its run raises :class:`CorruptRun` (``corrupt_run``); nothing else escapes.
Messages name the record and the defect from the schema alone, never file
content. Decoded records are rebuilt as contract dataclasses, so what a reader
serves is exactly what the contracts' ``to_dict`` produce. Unit tags are decimal
strings on disk. Every key and string anywhere in a record must be valid Unicode
(a lone surrogate, e.g. a ``\\ud800`` escape, could not be encoded into a
response), checked by ``jev.policy``'s JSON-data scan, and every read is bounded
by its record's cap; the writer refuses to produce a record that breaks either
rule. :func:`read_run_summary` reads only the :data:`STATE_SUMMARY_BYTES` prefix
of a state, which leads with the summary. Resolving and opening a record are
retried after Windows sharing violations, so a writer's ``os.replace`` in flight
never reads as corrupt.

This module imports only the standard library and ``jev``.
"""

from __future__ import annotations

import contextlib
import dataclasses
import itertools
import json
import math
import os
import re
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Final, Protocol

from jev.contracts import (
    ERROR_CODES,
    EVENT_KINDS,
    EVENT_STATUSES,
    MAX_ABS_NUMBER,
    MAX_ACTIVE_TASKS,
    MAX_COORDINATE,
    MAX_FRAGMENT_CHARS,
    MAX_MESSAGE_CHARS,
    MAX_TAG,
    RECENT_EVENT_LIMIT,
    RUN_RESULTS,
    RUN_STATUSES,
    SCHEMA_VERSION,
    TASK_STATUSES,
    TERMINAL_RUN_STATUSES,
    Event,
    JevError,
    JsonValue,
    Policy,
    PolicyError,
    RunMetadata,
    RunResult,
    RunState,
    RunStatus,
    RunSummary,
    Target,
    Task,
    TraceStats,
    full_match,
    is_bounded_number,
    is_valid_run_id,
    render_text,
    safe_repr,
)
from jev.policy import (
    MAX_NODES,
    MAX_POLICY_BYTES,
    json_value_issues,
    parse_json_document,
    parse_policy,
    policy_hash,
)

__all__ = [
    "COMMIT_RE",
    "CorruptRun",
    "EvidenceFiles",
    "MAX_METADATA_BYTES",
    "MAX_STATE_BYTES",
    "MAX_TRACE_SEGMENTS",
    "MAX_TRACE_SEGMENT_BYTES",
    "METADATA_FILE",
    "MIN_STATE_INTERVAL_SECONDS",
    "POLICY_ARCHIVE_FILE",
    "POLICY_HASH_RE",
    "PersistenceFailed",
    "REPLAY_FILE",
    "RUN_ROOT_PARTS",
    "RunRecorder",
    "RunStateSource",
    "SHARING_RETRY_DELAYS",
    "STATE_FILE",
    "STATE_SUMMARY_BYTES",
    "TelemetryLimits",
    "default_run_root",
    "read_policy_archive",
    "read_run_metadata",
    "read_run_state",
    "read_run_summary",
    "read_source_commit",
    "repository_root",
    "timestamp_seconds",
    "trace_segment_name",
    "utc_timestamp",
]

#: Run-directory record names (plan D5).
POLICY_ARCHIVE_FILE: Final = "policy.json"
STATE_FILE: Final = "state.json"
METADATA_FILE: Final = "metadata.json"
#: burnysc2 saves the match replay here, inside the run directory.
REPLAY_FILE: Final = "replay.SC2Replay"
#: The default run root, relative to the repository root.
RUN_ROOT_PARTS: Final = ("data", "jev", "runs")
#: A trace segment rotates before it would grow past this many bytes (10 MiB).
MAX_TRACE_SEGMENT_BYTES: Final = 10 * 1024 * 1024
#: Trace segments kept per run; older ones are deleted and counted.
MAX_TRACE_SEGMENTS: Final = 5
#: Least wall-clock time between two live state writes: at most two per second.
MIN_STATE_INTERVAL_SECONDS: Final = 0.5
#: Waits before each retry of an operation Windows refused with a sharing
#: violation; mirrors the ``os.replace`` backoff in ``orchestrator.baselines``
#: (Jev imports no orchestrator code beyond the SC2 path resolver).
SHARING_RETRY_DELAYS: Final = (0.05, 0.1, 0.2, 0.4, 0.8)
#: Largest stored record a reader accepts and the writer produces (the policy
#: archive's cap is ``jev.policy.MAX_POLICY_BYTES``). A state holds at most 200
#: events and 128 tasks, far below its cap. Metadata is at most ~530 bytes (a
#: 64-character map name, a SHA-256 commit, every number at its limit).
MAX_STATE_BYTES: Final = 8 * 1024 * 1024
MAX_METADATA_BYTES: Final = 1024
#: A state document leads with ``schema_version`` and the RunSummary fields (at
#: most ~250 bytes), which must fit in this prefix: a run listing reads only it.
STATE_SUMMARY_BYTES: Final = 512
#: A git object name: SHA-1 or SHA-256, lowercase hex.
COMMIT_RE: Final = re.compile(r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
#: The canonical policy hash (``jev.policy.policy_hash``): SHA-256 hex.
POLICY_HASH_RE: Final = re.compile(r"\A[0-9a-f]{64}\Z")

_TAG_RE: Final = re.compile(r"\A(?:0|[1-9][0-9]{0,19})\Z")
_SEGMENT_PREFIX: Final = "events."
_SEGMENT_SUFFIX: Final = ".jsonl"
#: A trace segment's file name, as :func:`trace_segment_name` formats it.
_SEGMENT_NAME_RE: Final = re.compile(
    rf"\A{re.escape(_SEGMENT_PREFIX)}([1-9][0-9]{{0,9}}){re.escape(_SEGMENT_SUFFIX)}\Z"
)
#: Run-directory entries examined when old trace segments are deleted (a run
#: directory holds a handful of records and at most a few segments).
_MAX_RUN_DIR_ENTRIES: Final = 256
#: The keys a state document leads with, in order (``RunState.to_dict``).
_SUMMARY_KEYS: Final = ("schema_version", *(f.name for f in dataclasses.fields(RunSummary)))
_JSON_SPACE: Final = re.compile(r"[ \t\n\r]*")
_REF_RE: Final = re.compile(r"\Arefs/[A-Za-z0-9._/-]{1,200}\Z")
_MAX_TIMESTAMP_CHARS: Final = 64
#: Small git files (``.git``, ``HEAD``, a loose ref, ``commondir``) are read up to this size.
_MAX_GIT_FILE_BYTES: Final = 4096
#: ``packed-refs`` is scanned line by line, at most this many lines of this length.
_MAX_PACKED_REF_LINES: Final = 200_000
_MAX_PACKED_REF_LINE_BYTES: Final = 1024


def trace_segment_name(segment: int) -> str:
    """The file name of trace segment ``segment``: ``events.N.jsonl``."""
    return f"{_SEGMENT_PREFIX}{segment}{_SEGMENT_SUFFIX}"


def repository_root() -> Path:
    """The checkout this module runs from (``<repository>/src/jev/telemetry.py``)."""
    return Path(__file__).resolve().parents[2]


def default_run_root() -> Path:
    """``<repository>/data/jev/runs``: where the runner writes and the dashboard reads."""
    return repository_root().joinpath(*RUN_ROOT_PARTS)


def utc_timestamp(seconds: float) -> str:
    """ISO 8601 UTC timestamp with milliseconds, e.g. ``2026-10-08T12:00:00.000+00:00``."""
    return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="milliseconds")


def timestamp_seconds(text: str) -> float:
    """POSIX seconds of an ISO 8601 UTC timestamp; ValueError for anything else."""
    if not isinstance(text, str) or len(text) > _MAX_TIMESTAMP_CHARS:
        raise ValueError("not an ISO 8601 timestamp")
    moment = datetime.fromisoformat(text)
    if moment.utcoffset() != timedelta(0):
        raise ValueError("not a UTC timestamp")
    return moment.timestamp()


def _retry_sharing[T](
    action: Callable[[], T], delays: Sequence[float], sleep: Callable[[float], None]
) -> T:
    """Run ``action``, retrying after each delay while it raises ``PermissionError``."""
    for delay in delays:
        try:
            return action()
        except PermissionError:
            sleep(delay)
    return action()


# ---------------------------------------------------------------------------
# Source commit (no subprocess, no shell)
# ---------------------------------------------------------------------------


def _small_text(path: Path) -> str:
    with path.open("rb") as handle:
        data = handle.read(_MAX_GIT_FILE_BYTES + 1)
    if len(data) > _MAX_GIT_FILE_BYTES:
        raise ValueError("git file too large")
    return data.decode("ascii").strip()


def _packed_ref(packed_refs: Path, ref: str) -> str | None:
    wanted = ref.encode("ascii")
    with packed_refs.open("rb") as handle:
        for _ in range(_MAX_PACKED_REF_LINES):
            line = handle.readline(_MAX_PACKED_REF_LINE_BYTES)
            if not line:
                return None
            name, _, value = line.rstrip(b"\r\n").partition(b" ")
            if value == wanted:
                commit = name.decode("ascii")
                return commit if full_match(COMMIT_RE, commit) else None
    return None


def _source_commit(repository: Path) -> str | None:
    dot_git = repository / ".git"
    if dot_git.is_file():  # a worktree or submodule: ``gitdir: <path>``
        pointer = _small_text(dot_git)
        if not pointer.startswith("gitdir: "):
            return None
        git_dir = (repository / pointer.removeprefix("gitdir: ")).resolve()
    elif dot_git.is_dir():
        git_dir = dot_git
    else:
        return None
    head = _small_text(git_dir / "HEAD")
    if full_match(COMMIT_RE, head):  # detached HEAD
        return head
    ref = head.removeprefix("ref: ")
    if ref == head or not full_match(_REF_RE, ref) or ".." in ref.split("/"):
        return None
    common = git_dir
    if (git_dir / "commondir").is_file():  # a worktree shares the main repository's refs
        common = (git_dir / _small_text(git_dir / "commondir")).resolve()
    for base in dict.fromkeys((git_dir, common)):
        loose = base.joinpath(*ref.split("/"))
        if loose.is_file():
            commit = _small_text(loose)
            return commit if full_match(COMMIT_RE, commit) else None
    packed = common / "packed-refs"
    return _packed_ref(packed, ref) if packed.is_file() else None


def read_source_commit(repository: Path) -> str | None:
    """The commit checked out at ``repository``, read from its git files; None if unknown.

    Follows ``.git`` (a directory, or a worktree's ``gitdir:`` file), ``HEAD``
    (detached, or ``ref: refs/...``), the loose ref in the worktree or shared
    repository, then ``packed-refs``. Never runs git or a shell; every file read is
    bounded, and any unreadable, malformed or unsafe value gives None.
    """
    try:
        return _source_commit(repository)
    except (OSError, ValueError, RuntimeError):  # unreadable, undecodable, symlink loop
        return None


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class PersistenceFailed(Exception):
    """Mandatory run evidence could not be written; stable error code ``persistence_failed``.

    ``message`` names the record and the OS-level cause (rendered and capped, without
    file paths).
    """

    code: Final = "persistence_failed"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class TelemetryLimits:
    """Writer thresholds (plan D5 defaults); tests inject small values."""

    max_segment_bytes: int = MAX_TRACE_SEGMENT_BYTES
    max_segments: int = MAX_TRACE_SEGMENTS
    min_state_interval_seconds: float = MIN_STATE_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        for name in ("max_segment_bytes", "max_segments"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer, got {safe_repr(value)}")
        interval = self.min_state_interval_seconds
        if not is_bounded_number(interval) or interval < 0:
            shown = safe_repr(interval)
            raise ValueError(
                f"min_state_interval_seconds must be a non-negative number, got {shown}"
            )


class EvidenceFiles:
    """The disk operations behind a :class:`RunRecorder` (injectable for tests).

    :meth:`write` replaces a whole file atomically through a same-directory ``.tmp``
    sibling; opening a file and replacing one are retried after each of ``delays``
    while Windows reports a sharing violation, then the last ``OSError`` is raised.
    """

    def __init__(
        self,
        *,
        replace_file: Callable[[Path, Path], None] = os.replace,
        sleep: Callable[[float], None] = time.sleep,
        delays: Sequence[float] = SHARING_RETRY_DELAYS,
    ) -> None:
        self._replace = replace_file
        self._sleep = sleep
        self._delays = tuple(delays)

    def write(self, path: Path, data: bytes) -> None:
        """Atomically make ``path`` hold ``data`` (the ``.tmp`` file is removed on failure)."""
        temporary = path.with_name(path.name + ".tmp")
        try:
            _retry_sharing(lambda: temporary.write_bytes(data), self._delays, self._sleep)
            _retry_sharing(lambda: self._replace(temporary, path), self._delays, self._sleep)
        except OSError:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
            raise

    def append(self, path: Path, data: bytes) -> None:
        """Append ``data`` to ``path`` (created if missing) in one write."""
        handle = _retry_sharing(lambda: path.open("ab"), self._delays, self._sleep)
        with handle:
            handle.write(data)

    def size(self, path: Path) -> int:
        """``path``'s size in bytes; 0 when it does not exist."""
        try:
            return path.stat().st_size
        except FileNotFoundError:
            return 0

    def truncate(self, path: Path, size: int) -> None:
        os.truncate(path, size)

    def remove(self, path: Path) -> None:
        path.unlink(missing_ok=True)


class RunStateSource(Protocol):
    """Builds the runtime-owned part of a RunState (``jev.runtime.JevRuntime``)."""

    def run_state(
        self,
        *,
        status: RunStatus,
        updated_at: str,
        recent_events: tuple[Event, ...] = ...,
        result: RunResult | None = ...,
        error: JevError | None = ...,
    ) -> RunState: ...


def _os_error_text(exc: OSError) -> str:
    """An OS error's cause without its file paths (they are local, and get served)."""
    cause = exc.strerror if isinstance(exc.strerror, str) else type(exc).__name__
    errno = f" (errno {exc.errno})" if isinstance(exc.errno, int) else ""
    return render_text(cause)[:MAX_FRAGMENT_CHARS] + errno


def _encode(document: Mapping[str, JsonValue]) -> bytes:
    """Compact ASCII JSON (non-ASCII text is escaped, so encoding cannot fail)."""
    text = json.dumps(document, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    return text.encode("ascii")


class RunRecorder:
    """Write one run's evidence directory (see the module docstring).

    ``run_root`` must be absolute; the directory is ``run_root / metadata.run_id``.
    ``roots`` are the policy's root node IDs (summarized once per game second).
    ``clock`` (monotonic seconds) paces state writes; ``wall_time`` (POSIX seconds)
    stamps ``updated_at``. Raises :class:`ValueError` for invalid arguments.
    """

    def __init__(
        self,
        run_root: Path,
        metadata: RunMetadata,
        *,
        roots: Iterable[str],
        limits: TelemetryLimits | None = None,
        files: EvidenceFiles | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(run_root, Path) or not run_root.is_absolute():
            raise ValueError(f"run_root must be an absolute Path, got {safe_repr(str(run_root))}")
        if not isinstance(metadata, RunMetadata) or not is_valid_run_id(metadata.run_id):
            raise ValueError("metadata must be a RunMetadata with a lowercase UUID4 hex run_id")
        if limits is not None and not isinstance(limits, TelemetryLimits):
            raise ValueError(f"limits must be TelemetryLimits, got {safe_repr(limits)}")
        self._root = run_root
        self._dir = run_root / metadata.run_id
        self._metadata = metadata
        self._roots = frozenset(roots)
        self._limits = TelemetryLimits() if limits is None else limits
        self._files = EvidenceFiles() if files is None else files
        self._clock = clock
        self._wall_time = wall_time
        self._started = False
        self._live = False  # between a completed start() and finish()
        self._state_written_at = 0.0
        self._last_state: RunState | None = None
        self._recent: deque[Event] = deque(maxlen=RECENT_EVENT_LIMIT)
        # node id -> (status, deciding child) of the node's last traced event.
        self._node_keys: dict[str, tuple[str, JsonValue]] = {}
        self._summary_second = -1
        self._segment = 1
        self._segment_bytes = 0  # bytes of complete lines in the current segment
        self._segment_events: dict[int, int] = {}  # segment -> events it holds on disk
        self._events = 0
        self._rotated = 0
        self._dropped_segments = 0
        self._dropped_events = 0
        self._complete = True

    @property
    def run_dir(self) -> Path:
        return self._dir

    @property
    def live(self) -> bool:
        """Whether start() completed and no terminal state has been written yet."""
        return self._live

    @property
    def replay_file(self) -> Path:
        """Where burnysc2 should save the replay (``run_dir / REPLAY_FILE``)."""
        return self._dir / REPLAY_FILE

    @property
    def trace(self) -> TraceStats:
        """The trace bookkeeping as of now."""
        return TraceStats(
            segment=self._segment,
            events=self._events,
            rotated_segments=self._rotated,
            dropped_segments=self._dropped_segments,
            dropped_events=self._dropped_events,
            complete=self._complete,
        )

    def start(self, policy_bytes: bytes) -> None:
        """Create the run directory; write the policy archive, a starting state, metadata.

        Raises :class:`PersistenceFailed` if any of them cannot be written (an
        existing run directory included), and RuntimeError if already started.
        """
        if self._started:
            raise RuntimeError("RunRecorder.start() called twice")
        self._started = True
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            self._dir.mkdir()  # unique per run: an existing directory is never reused
        except OSError as exc:
            detail = _os_error_text(exc)
            raise self._failure(f"cannot create the run directory: {detail}") from exc
        self._write(POLICY_ARCHIVE_FILE, policy_bytes)
        metadata = self._metadata
        self._write_state(
            RunState(
                run_id=metadata.run_id,
                family=metadata.family,
                version=metadata.version,
                policy_hash=metadata.policy_hash,
                status="starting",
                updated_at=utc_timestamp(self._wall_time()),
                game_seconds=0.0,
                last_sequence=0,
                active_nodes=(),
                waiting_nodes=(),
                tasks=(),
                recent_events=(),
            )
        )
        self._write_metadata(metadata)
        self._live = True

    def update(self, source: RunStateSource, events: Sequence[Event]) -> None:
        """Trace ``events``; rewrite the running state if the write interval has passed.

        Raises :class:`PersistenceFailed` when the state cannot be written.
        """
        self._require_live()
        self._trace_events(events)
        if self._clock() - self._state_written_at >= self._limits.min_state_interval_seconds:
            self._publish(source, "running", None, None)

    def finish(
        self,
        source: RunStateSource | None,
        *,
        status: RunStatus,
        result: RunResult | None = None,
        error: JevError | None = None,
    ) -> None:
        """Record the replay (if burnysc2 saved one) and write the terminal state.

        ``source`` is None when the match never got a runtime (e.g. SC2 was
        unavailable). If the replay reference cannot be written, the terminal state
        is still written, as ``failed`` with ``persistence_failed``: ``result`` keeps
        the match's true outcome, and the message names the error the run had
        ended with, if any. Raises :class:`PersistenceFailed` (with the same note)
        when a record cannot be written, and ValueError for a non-terminal
        ``status``. The recorder stays live until the terminal state is written, so
        a finish interrupted by an exception (e.g. a second Ctrl+C) can be retried;
        the replay reference is still written at most once.
        """
        if status not in TERMINAL_RUN_STATUSES:
            raise ValueError(f"finish() needs a terminal status, got {safe_repr(status)}")
        self._require_live()
        ended_with = error
        failure: PersistenceFailed | None = None
        if self._metadata.replay_path is None and _is_saved(self.replay_file):
            replayed = replace(self._metadata, replay_path=REPLAY_FILE)
            try:
                self._write_metadata(replayed)
            except PersistenceFailed as exc:
                failure = PersistenceFailed(_noting(exc.message, ended_with))
            else:
                self._metadata = replayed
        if failure is not None:
            status, error = "failed", JevError(failure.code, failure.message)
        try:
            self._publish(source, status, result, error)
        except PersistenceFailed as exc:
            raise PersistenceFailed(_noting(exc.message, ended_with)) from exc
        self._live = False
        if failure is not None:
            raise failure

    # -- state ----------------------------------------------------------------

    def _require_live(self) -> None:
        if not self._live:
            raise RuntimeError("RunRecorder used without a completed start() or after finish()")

    def _publish(
        self,
        source: RunStateSource | None,
        status: RunStatus,
        result: RunResult | None,
        error: JevError | None,
    ) -> None:
        updated_at = utc_timestamp(self._wall_time())
        recent = tuple(self._recent)
        if source is not None:
            snapshot = source.run_state(
                status=status,
                updated_at=updated_at,
                recent_events=recent,
                result=result,
                error=error,
            )
        else:
            assert self._last_state is not None  # a completed start() wrote one
            snapshot = replace(
                self._last_state,
                status=status,
                updated_at=updated_at,
                recent_events=recent,
                result=result,
                error=error,
            )
        self._write_state(snapshot)

    def _write_state(self, snapshot: RunState) -> None:
        tasks = snapshot.tasks[:MAX_ACTIVE_TASKS]
        state = replace(
            snapshot,
            tasks=tasks,
            tasks_omitted=len(snapshot.tasks) - len(tasks),
            trace=self.trace,
        )
        summary = _encode({"schema_version": state.schema_version, **state.summary().to_dict()})
        if len(summary) > STATE_SUMMARY_BYTES:
            raise self._failure(
                f"the {STATE_FILE} summary would be {len(summary)} bytes; "
                f"the limit is {STATE_SUMMARY_BYTES}"
            )
        self._write_capped(STATE_FILE, state.to_dict(), MAX_STATE_BYTES)
        self._last_state = state
        self._state_written_at = self._clock()

    def _write_metadata(self, metadata: RunMetadata) -> None:
        self._write_capped(METADATA_FILE, metadata.to_dict(), MAX_METADATA_BYTES)

    def _write_capped(self, name: str, document: Mapping[str, JsonValue], cap: int) -> None:
        """Write a record no reader would reject: valid Unicode text, within its cap."""
        try:  # a lone surrogate cannot be encoded as UTF-8, so no reader would serve it
            json.dumps(document, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            raise self._failure(f"{name} would hold text that is not valid Unicode") from None
        data = _encode(document)
        if len(data) > cap:
            raise self._failure(f"{name} would be {len(data)} bytes; the limit is {cap}")
        self._write(name, data)

    def _write(self, name: str, data: bytes) -> None:
        try:
            self._files.write(self._dir / name, data)
        except OSError as exc:
            raise self._failure(f"cannot write {name}: {_os_error_text(exc)}") from exc

    def _failure(self, message: str) -> PersistenceFailed:
        self._complete = False  # the trace is never reported complete after this
        return PersistenceFailed(message)

    # -- trace ----------------------------------------------------------------

    def _trace_events(self, events: Sequence[Event]) -> None:
        traced = self._select(events)
        if traced:
            self._recent.extend(traced)
            self._append([_encode(event.to_dict()) + b"\n" for event in traced])

    def _select(self, events: Sequence[Event]) -> list[Event]:
        """The events the trace keeps (module docstring: changes plus summaries)."""
        kept: list[Event] = []
        summary_second: int | None = None
        for event in events:
            if event.kind != "node":
                kept.append(event)
                continue
            key = (event.status, event.facts.get("child"))
            changed = self._node_keys.get(event.node_id) != key
            self._node_keys[event.node_id] = key
            second = math.floor(event.game_seconds)
            summary = event.node_id in self._roots and second > self._summary_second
            if summary:
                summary_second = second
            if changed or summary:
                kept.append(event)
        if summary_second is not None:
            self._summary_second = summary_second
        return kept

    def _append(self, lines: list[bytes]) -> None:
        """Append ``lines``, rotating before a segment would exceed its size limit."""
        try:
            self._repair_tail()
        except OSError:
            self._drop(len(lines))
            return
        chunk: list[bytes] = []
        size = self._segment_bytes
        for line in lines:
            if size and size + len(line) > self._limits.max_segment_bytes:
                self._write_chunk(chunk)
                chunk = []
                self._rotate()
                size = 0
            chunk.append(line)
            size += len(line)
        self._write_chunk(chunk)

    def _write_chunk(self, chunk: list[bytes]) -> None:
        if not chunk:
            return
        data = b"".join(chunk)
        try:
            self._files.append(self._dir / trace_segment_name(self._segment), data)
        except OSError:
            self._drop(len(chunk))  # its torn tail is removed before the next append
            return
        self._segment_bytes += len(data)
        self._events += len(chunk)
        held = self._segment_events.get(self._segment, 0)
        self._segment_events[self._segment] = held + len(chunk)

    def _drop(self, count: int) -> None:
        self._dropped_events += count
        self._complete = False

    def _repair_tail(self) -> None:
        """Make the current segment end at its last complete line (module docstring)."""
        path = self._dir / trace_segment_name(self._segment)
        on_disk = self._files.size(path)
        if on_disk == self._segment_bytes:
            return
        if on_disk > self._segment_bytes:
            try:
                self._files.truncate(path, self._segment_bytes)
                return
            except OSError:
                pass  # left behind: continue in a fresh segment
        self._rotate()

    def _rotate(self) -> None:
        self._segment += 1
        self._segment_bytes = 0
        self._rotated += 1
        self._prune_segments(self._segment - self._limits.max_segments + 1)

    def _prune_segments(self, oldest_kept: int) -> None:
        """Delete every segment file numbered below ``oldest_kept``, by validated name.

        Scanning the directory, not only the segments this recorder appended to,
        also removes a segment a failed append left behind. A file that cannot be
        listed or deleted now is retried at the next rotation.
        """
        try:
            entries = list(itertools.islice(self._dir.iterdir(), _MAX_RUN_DIR_ENTRIES))
        except OSError:
            return
        for entry in entries:
            match = _SEGMENT_NAME_RE.fullmatch(entry.name)
            if match is None or int(match[1]) >= oldest_kept:
                continue
            try:
                self._files.remove(entry)
            except OSError:
                continue
            self._dropped_segments += 1
            self._dropped_events += self._segment_events.pop(int(match[1]), 0)
            self._complete = False


def _noting(message: str, earlier: JevError | None) -> str:
    """``message``, naming the error the run had already ended with, so it is not lost."""
    if earlier is None:
        return message
    noted = f"{message}; the run had ended with {earlier.code}: {earlier.message}"
    return render_text(noted)[:MAX_MESSAGE_CHARS]


def _is_saved(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Reader: the one validation boundary for stored records
# ---------------------------------------------------------------------------


class CorruptRun(Exception):
    """A stored run record is malformed; stable error code ``corrupt_run``.

    ``message`` names the record and the defect from the schema alone; it never
    contains file content.
    """

    code: Final = "corrupt_run"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _field_names(record: type) -> frozenset[str]:
    return frozenset(f.name for f in dataclasses.fields(record))


class _Fields:
    """Typed access to one stored JSON object; every defect is a :class:`CorruptRun`."""

    def __init__(self, what: str, value: object, expected: frozenset[str]) -> None:
        if not isinstance(value, dict):
            raise CorruptRun(f"{what} is not a JSON object")
        if value.keys() != expected:
            missing = sorted(expected - value.keys())
            detail = f" (missing: {', '.join(missing)})" if missing else ""
            raise CorruptRun(f"{what} has missing or unexpected fields{detail}")
        self._what = what
        self._value: dict[str, object] = value

    def bad(self, name: str, expected: str) -> CorruptRun:
        return CorruptRun(f"{self._what} field {name!r} is not {expected}")

    def raw(self, name: str) -> object:
        return self._value[name]

    def schema_version(self) -> None:
        value = self._value["schema_version"]
        if isinstance(value, bool) or value != SCHEMA_VERSION:
            raise CorruptRun(f"{self._what} has an unsupported schema_version")

    def text(self, name: str) -> str:
        value = self._value[name]
        if not isinstance(value, str):
            raise self.bad(name, "a string")
        return value

    def optional_text(self, name: str) -> str | None:
        return None if self._value[name] is None else self.text(name)

    def matching(self, name: str, pattern: re.Pattern[str], expected: str) -> str:
        value = self.text(name)
        if not full_match(pattern, value):
            raise self.bad(name, expected)
        return value

    def count(self, name: str) -> int:
        value = self._value[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= MAX_ABS_NUMBER
        ):
            raise self.bad(name, "a non-negative integer")
        return value

    def number(self, name: str) -> float:
        value = self._value[name]
        if not is_bounded_number(value):
            raise self.bad(name, "a finite number")
        assert isinstance(value, int | float)
        return float(value)

    def optional_number(self, name: str) -> float | None:
        return None if self._value[name] is None else self.number(name)

    def flag(self, name: str) -> bool:
        value = self._value[name]
        if not isinstance(value, bool):
            raise self.bad(name, "a boolean")
        return value

    def choice[T: str](self, name: str, allowed: tuple[T, ...]) -> T:
        value = self._value[name]
        for option in allowed:
            if value == option:
                return option
        raise self.bad(name, f"one of {', '.join(allowed)}")

    def timestamp(self, name: str) -> str:
        value = self.text(name)
        try:
            timestamp_seconds(value)
        except ValueError:
            raise self.bad(name, "an ISO 8601 UTC timestamp") from None
        return value

    def items(self, name: str, limit: int) -> list[object]:
        value = self._value[name]
        if not isinstance(value, list) or len(value) > limit:
            raise self.bad(name, f"a list of at most {limit} items")
        return value

    def strings(self, name: str, limit: int) -> tuple[str, ...]:
        items = self.items(name, limit)
        if not all(isinstance(item, str) for item in items):
            raise self.bad(name, "a list of strings")
        return tuple(str(item) for item in items)

    def mapping(self, name: str) -> dict[str, JsonValue]:
        value = self._value[name]
        if not isinstance(value, dict):
            raise self.bad(name, "a JSON object")
        return value

    def optional_mapping(self, name: str) -> dict[str, JsonValue] | None:
        return None if self._value[name] is None else self.mapping(name)

    def tag(self, name: str, value: object) -> int:
        if not full_match(_TAG_RE, value) or int(str(value)) > MAX_TAG:
            raise self.bad(name, "a decimal unit-tag string")
        return int(str(value))

    def target(self, name: str) -> Target:
        value = self._value[name]
        if value is None:
            return None
        if isinstance(value, str):
            return self.tag(name, value)
        if (
            isinstance(value, list)
            and len(value) == 2
            and all(is_bounded_number(v, MAX_COORDINATE) for v in value)
        ):
            return (float(value[0]), float(value[1]))
        raise self.bad(name, "null, a unit-tag string or an [x, y] point")


def _decode_task(what: str, value: object) -> Task:
    fields = _Fields(what, value, _field_names(Task))
    actor = fields.raw("actor_tag")
    return Task(
        id=fields.text("id"),
        node_id=fields.text("node_id"),
        intent_key=fields.text("intent_key"),
        actor_tag=None if actor is None else fields.tag("actor_tag", actor),
        target=fields.target("target"),
        status=fields.choice("status", TASK_STATUSES),
        created_game_seconds=fields.number("created_game_seconds"),
        deadline_game_seconds=fields.optional_number("deadline_game_seconds"),
        attempts=fields.count("attempts"),
        last_progress_game_seconds=fields.optional_number("last_progress_game_seconds"),
        reason=fields.text("reason"),
    )


def _decode_event(what: str, value: object, run_id: str) -> Event:
    fields = _Fields(what, value, _field_names(Event))
    fields.schema_version()
    if fields.raw("run_id") != run_id:
        raise CorruptRun(f"{what} belongs to another run")
    return Event(
        run_id=run_id,
        sequence=fields.count("sequence"),
        game_loop=fields.count("game_loop"),
        game_seconds=fields.number("game_seconds"),
        node_id=fields.text("node_id"),
        task_id=fields.optional_text("task_id"),
        kind=fields.choice("kind", EVENT_KINDS),
        status=fields.choice("status", EVENT_STATUSES),
        reason=fields.text("reason"),
        facts=fields.mapping("facts"),
        action=fields.optional_mapping("action"),
    )


def _decode_error(what: str, value: object) -> JevError | None:
    if value is None:
        return None
    fields = _Fields(what, value, _field_names(JevError))
    return JevError(code=fields.choice("code", ERROR_CODES), message=fields.text("message"))


def _decode_trace(what: str, value: object) -> TraceStats:
    fields = _Fields(what, value, _field_names(TraceStats))
    trace = TraceStats(
        segment=fields.count("segment"),
        events=fields.count("events"),
        rotated_segments=fields.count("rotated_segments"),
        dropped_segments=fields.count("dropped_segments"),
        dropped_events=fields.count("dropped_events"),
        complete=fields.flag("complete"),
    )
    if trace.segment < 1:
        raise fields.bad("segment", "a positive integer")
    return trace


def _decode_summary(fields: _Fields, metadata: RunMetadata) -> RunSummary:
    """The leading fields of a state, checked against the run's metadata."""
    fields.schema_version()
    summary = RunSummary(
        run_id=fields.text("run_id"),
        family=fields.text("family"),
        version=fields.count("version"),
        policy_hash=fields.matching("policy_hash", POLICY_HASH_RE, "a SHA-256 hex digest"),
        status=fields.choice("status", RUN_STATUSES),
        updated_at=fields.timestamp("updated_at"),
    )
    identity = (summary.run_id, summary.family, summary.version, summary.policy_hash)
    if identity != (metadata.run_id, metadata.family, metadata.version, metadata.policy_hash):
        raise CorruptRun(f"{STATE_FILE} does not match {METADATA_FILE}")
    return summary


def _decode_state(document: object, metadata: RunMetadata) -> RunState:
    fields = _Fields(STATE_FILE, document, _field_names(RunState))
    summary = _decode_summary(fields, metadata)
    run_id = metadata.run_id
    tasks = fields.items("tasks", MAX_ACTIVE_TASKS)
    events = fields.items("recent_events", RECENT_EVENT_LIMIT)
    return RunState(
        run_id=summary.run_id,
        family=summary.family,
        version=summary.version,
        policy_hash=summary.policy_hash,
        status=summary.status,
        updated_at=summary.updated_at,
        game_seconds=fields.number("game_seconds"),
        last_sequence=fields.count("last_sequence"),
        active_nodes=fields.strings("active_nodes", MAX_NODES),
        waiting_nodes=fields.strings("waiting_nodes", MAX_NODES),
        tasks=tuple(_decode_task(f"{STATE_FILE} tasks[{i}]", t) for i, t in enumerate(tasks)),
        recent_events=tuple(
            _decode_event(f"{STATE_FILE} recent_events[{i}]", e, run_id)
            for i, e in enumerate(events)
        ),
        result=None if fields.raw("result") is None else fields.choice("result", RUN_RESULTS),
        error=_decode_error(f"{STATE_FILE} error", fields.raw("error")),
        tasks_omitted=fields.count("tasks_omitted"),
        trace=_decode_trace(f"{STATE_FILE} trace", fields.raw("trace")),
    )


def _skip_space(text: str, index: int) -> int:
    match = _JSON_SPACE.match(text, index)
    return index if match is None else match.end()


def _expect(text: str, index: int, token: str) -> int:
    index = _skip_space(text, index)
    if text[index : index + 1] != token:
        raise CorruptRun(f"{STATE_FILE} does not start with its summary")
    return index + 1


def _leading_members(data: bytes) -> dict[str, object]:
    """The members a state prefix must start with, ``_SUMMARY_KEYS`` in order.

    Each key and scalar value is parsed strictly with the stdlib JSON decoder;
    anything else -- other keys or order, a container value, a member cut off by
    the prefix -- is a :class:`CorruptRun`.
    """
    try:
        text = data.decode("ascii")  # the writer escapes every non-ASCII character
    except UnicodeDecodeError as exc:
        raise CorruptRun(f"{STATE_FILE} is not ASCII JSON") from exc
    decoder = json.JSONDecoder()
    members: dict[str, object] = {}
    index = 0
    try:
        for position, key in enumerate(_SUMMARY_KEYS):
            index = _expect(text, index, "," if position else "{")
            name, index = decoder.raw_decode(text, _skip_space(text, index))
            index = _skip_space(text, _expect(text, index, ":"))
            if name != key or text[index : index + 1] in ("[", "{"):
                raise CorruptRun(f"{STATE_FILE} does not start with its summary")
            members[key], index = decoder.raw_decode(text, index)
    except ValueError as exc:  # json.JSONDecodeError: malformed, or cut off
        raise CorruptRun(f"{STATE_FILE} does not start with its summary") from exc
    return members


def _decode_metadata(document: object, run_id: str) -> RunMetadata:
    fields = _Fields(METADATA_FILE, document, _field_names(RunMetadata))
    fields.schema_version()
    if fields.raw("run_id") != run_id:
        raise CorruptRun(f"{METADATA_FILE} belongs to another run")
    commit = fields.raw("source_commit")
    replay = fields.raw("replay_path")
    if replay is not None and replay != REPLAY_FILE:
        raise fields.bad("replay_path", f"null or {REPLAY_FILE!r}")
    return RunMetadata(
        run_id=run_id,
        created_at=fields.timestamp("created_at"),
        family=fields.text("family"),
        version=fields.count("version"),
        policy_hash=fields.matching("policy_hash", POLICY_HASH_RE, "a SHA-256 hex digest"),
        source_commit=(
            None if commit is None else fields.matching("source_commit", COMMIT_RE, "a commit")
        ),
        map=fields.text("map"),
        opponent_race=fields.text("opponent_race"),
        difficulty=fields.count("difficulty"),
        seed=fields.count("seed"),
        max_game_seconds=fields.number("max_game_seconds"),
        max_wall_seconds=fields.number("max_wall_seconds"),
        replay_path=REPLAY_FILE if replay is not None else None,
    )


def _open_record(run_dir: Path, name: str) -> BinaryIO | None:
    """``name`` opened for reading if it is a regular file directly in ``run_dir``."""
    try:
        base = run_dir.resolve(strict=True)
        path = (run_dir / name).resolve(strict=True)
    except FileNotFoundError:
        return None
    if path.parent != base or not path.is_file():
        raise CorruptRun(f"{name} is not a regular file in the run directory")
    try:
        return path.open("rb")
    except FileNotFoundError:  # removed since it was resolved
        return None


def _read_record(run_dir: Path, name: str, max_bytes: int, *, whole: bool = True) -> bytes | None:
    """A record's bytes, None if it is missing; never more than ``max_bytes`` are read.

    With ``whole``, a larger file is a :class:`CorruptRun`; otherwise only its first
    ``max_bytes`` are returned. Resolving and opening are retried after Windows
    sharing violations (a writer's ``os.replace`` in flight).
    """
    try:
        handle = _retry_sharing(
            lambda: _open_record(run_dir, name), SHARING_RETRY_DELAYS, time.sleep
        )
    except (OSError, RuntimeError) as exc:  # unreadable, or a link loop
        raise CorruptRun(f"{name} cannot be read") from exc
    if handle is None:
        return None
    with handle:
        try:
            if whole and os.fstat(handle.fileno()).st_size > max_bytes:
                raise CorruptRun(f"{name} is larger than {max_bytes} bytes")
            return handle.read(max_bytes)
        except OSError as exc:
            raise CorruptRun(f"{name} cannot be read") from exc


def _require_plain_json(name: str, document: Mapping[str, object]) -> None:
    """Every key and string anywhere in ``document`` must be valid Unicode.

    ``jev.policy.json_value_issues`` is the one scan (bounded by its element and
    depth budgets); a lone surrogate would otherwise reach a response and fail to
    encode there.
    """
    if json_value_issues(document, name):
        raise CorruptRun(f"{name} holds text that is not valid Unicode")


def _parse_record(run_dir: Path, name: str, max_bytes: int) -> dict[str, object] | None:
    data = _read_record(run_dir, name, max_bytes)
    if data is None:
        return None
    try:
        document = parse_json_document(data, what=name, max_bytes=max_bytes)
    except PolicyError as exc:  # the strict parser's only exception
        raise CorruptRun(f"{name} is not a strict JSON object") from exc
    _require_plain_json(name, document)
    return document


def _guarded[T](name: str, read: Callable[[], T]) -> T:
    """The boundary: explicit checks raise CorruptRun; any other defect is converted."""
    try:
        return read()
    except CorruptRun:
        raise
    except Exception as exc:  # defensive backstop for a defect the checks missed
        raise CorruptRun(f"{name} could not be processed") from exc


def read_run_metadata(run_dir: Path) -> RunMetadata | None:
    """``run_dir``'s metadata; None while it has none (not yet, or never, started).

    The run ID is the directory name. Raises :class:`CorruptRun` only.
    """

    def read() -> RunMetadata | None:
        document = _parse_record(run_dir, METADATA_FILE, MAX_METADATA_BYTES)
        return None if document is None else _decode_metadata(document, run_dir.name)

    return _guarded(METADATA_FILE, read)


def read_run_state(run_dir: Path, metadata: RunMetadata) -> RunState:
    """``run_dir``'s state, checked against its metadata. Raises :class:`CorruptRun` only."""

    def read() -> RunState:
        document = _parse_record(run_dir, STATE_FILE, MAX_STATE_BYTES)
        if document is None:
            raise CorruptRun(f"{STATE_FILE} is missing")
        return _decode_state(document, metadata)

    return _guarded(STATE_FILE, read)


def read_run_summary(run_dir: Path, metadata: RunMetadata) -> RunSummary:
    """``run_dir``'s run summary, reading at most :data:`STATE_SUMMARY_BYTES` of its state.

    Only the summary fields are validated (and checked against the metadata); a
    defect further into the state shows on :func:`read_run_state`. Raises
    :class:`CorruptRun` only.
    """

    def read() -> RunSummary:
        data = _read_record(run_dir, STATE_FILE, STATE_SUMMARY_BYTES, whole=False)
        if data is None:
            raise CorruptRun(f"{STATE_FILE} is missing")
        leading = _leading_members(data)
        _require_plain_json(STATE_FILE, leading)
        return _decode_summary(_Fields(STATE_FILE, leading, frozenset(_SUMMARY_KEYS)), metadata)

    return _guarded(STATE_FILE, read)


def read_policy_archive(run_dir: Path, metadata: RunMetadata) -> Policy:
    """The archived policy, validated and matching the run's policy hash.

    Raises :class:`CorruptRun` only.
    """

    def read() -> Policy:
        data = _read_record(run_dir, POLICY_ARCHIVE_FILE, MAX_POLICY_BYTES)
        if data is None:
            raise CorruptRun(f"{POLICY_ARCHIVE_FILE} is missing")
        try:
            document = parse_json_document(
                data, what=POLICY_ARCHIVE_FILE, max_bytes=MAX_POLICY_BYTES
            )
            _require_plain_json(POLICY_ARCHIVE_FILE, document)
            policy = parse_policy(document)
            digest = policy_hash(document)
        except PolicyError as exc:
            raise CorruptRun(f"{POLICY_ARCHIVE_FILE} is not a valid Jev policy") from exc
        if digest != metadata.policy_hash:
            raise CorruptRun(f"{POLICY_ARCHIVE_FILE} does not match the run's policy hash")
        return policy

    return _guarded(POLICY_ARCHIVE_FILE, read)
