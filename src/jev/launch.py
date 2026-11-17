"""Dashboard-first Jev launch (plan D7): launch sessions and the rendered-ready barrier.

A **launch session** is one attended launch: a single match (``scripts/launch-jev.ps1``)
or one benchmark batch (``scripts/benchmark_jev.py``). It lives in
``<launch root>/<session_id>/`` where the launch root is the run root's sibling
``launches`` (:func:`launch_root_for`; ``data/jev/launches`` beside the default
``data/jev/runs``). Session IDs are independent lowercase UUID4 hex values
(:func:`new_session_id`); they never name "the newest run".

* ``session.json`` -- :class:`LaunchSession`, written atomically by the session's
  single active owner: the launcher that created it, or the game process it
  delegates one case to while it waits (strictly one after the other).
* ``ready.json`` -- :class:`LaunchReady`, written atomically only by the dashboard
  API's readiness endpoint (``POST /api/jev/launches/{session_id}/ready``), and only
  for the session's exact active ``starting`` run and that run's archived policy hash.

Both records are bounded to :data:`MAX_LAUNCH_RECORD_BYTES` and their messages to
:data:`MAX_LAUNCH_MESSAGE_CHARS`. They are separate from the immutable run evidence
(:mod:`jev.telemetry`): nothing here rewrites a run archive.

**The barrier.** :func:`ready_hook` is the ``on_recorded`` hook of
:func:`jev.runner.run_match`: once the recorder has archived the run's policy and
``starting`` state, the hook publishes that run as the session's starting run and
waits until ``ready.json`` names this session, this run and its policy hash -- the
dashboard page writes it only after it rendered exactly that run. A receipt for
another run or session never releases it. After :data:`RUN_READY_SECONDS` without
one, the session is marked ``failed`` and :class:`LaunchAborted` is raised; Ctrl+C
marks it ``stopped`` and propagates. Either way the runner finalizes the run as
``stopped`` and never starts SC2.

**Launcher side.** :class:`DashboardLaunch` starts or reuses the dashboard servers
(``scripts/launch-a4g.ps1 -NoBrowser -NoWait``) with an environment that never holds
the service key, verifies actual Jev API responses from the backend and through the
frontend proxy within :data:`SERVER_READY_SECONDS`, creates the session, verifies the
running servers serve this session (a dashboard serving another checkout's data
root is refused, never silently used), and opens the exact session URL once. If the
browser cannot be opened the URL is printed and that page may still acknowledge
within the run deadline. :func:`main` is the single-match launcher
(``python -m jev.launch``).

Errors use the dashboard's error envelope with their own code set
(:data:`LaunchErrorCode`), never the archived RunState's error codes.
"""

from __future__ import annotations

import argparse
import contextlib
import enum
import importlib.util
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, get_args

from jev.contracts import (
    JsonValue,
    full_match,
    is_valid_run_id,
    render_text,
    safe_exception_text,
    safe_repr,
)
from jev.policy import PolicyError, json_value_issues, parse_json_document
from jev.telemetry import (
    POLICY_HASH_RE,
    SHARING_RETRY_DELAYS,
    CorruptRun,
    EvidenceFiles,
    read_policy_archive,
    read_run_metadata,
    repository_root,
    timestamp_seconds,
    utc_timestamp,
)

__all__ = [
    "API_URL",
    "DASHBOARD_URL",
    "DEFAULT_DIFFICULTY",
    "DEFAULT_LAUNCH_ORIGINS",
    "DEFAULT_PROVIDER",
    "DEFAULT_SEED",
    "DEFAULT_VERSION",
    "DashboardEndpoints",
    "DashboardLaunch",
    "DashboardUnavailable",
    "FIRST_OBSERVATION_SILENCE_SECONDS",
    "LAUNCH_ERROR_CODES",
    "LAUNCH_ERROR_STATUS",
    "LAUNCH_HEARTBEAT_SECONDS",
    "LAUNCH_ROOT_NAME",
    "LAUNCH_SCHEMA_VERSION",
    "LAUNCH_SESSION_FLAG",
    "LAUNCH_SILENCE_SECONDS",
    "LAUNCH_STATES",
    "LaunchAborted",
    "LaunchError",
    "LaunchErrorCode",
    "LaunchReady",
    "LaunchSession",
    "LaunchSessionWriter",
    "LaunchState",
    "MAX_CASE_COUNT",
    "MAX_DASHBOARD_PROBLEM_CHARS",
    "MAX_LAUNCH_MESSAGE_CHARS",
    "MAX_LAUNCH_RECORD_BYTES",
    "MAX_READY_BODY_BYTES",
    "READY_FILE",
    "READY_POLL_SECONDS",
    "RUN_READY_SECONDS",
    "SERVER_READY_SECONDS",
    "SESSION_FILE",
    "STARTING_SILENCE_SECONDS",
    "TERMINAL_LAUNCH_STATES",
    "accept_ready",
    "build_parser",
    "check_launch_client",
    "dashboard_health",
    "is_valid_session_id",
    "launch_error_document",
    "launch_root_for",
    "main",
    "new_session_id",
    "open_browser",
    "read_ready",
    "read_session",
    "ready_hook",
    "resolve_child_dir",
    "resolve_root",
    "scrubbed_environment",
    "session_url",
    "start_dashboard_servers",
    "verify_session_served",
    "wait_for_dashboard",
]

LAUNCH_SCHEMA_VERSION: Final = 1
#: The launch root is ``<run root>/../launches`` (beside ``data/jev/runs``).
LAUNCH_ROOT_NAME: Final = "launches"
SESSION_FILE: Final = "session.json"
READY_FILE: Final = "ready.json"
#: Every launch record is at most this large (plan D7), and its message this long.
MAX_LAUNCH_RECORD_BYTES: Final = 4096
MAX_LAUNCH_MESSAGE_CHARS: Final = 200
#: A readiness request body is far smaller than this; a larger one is refused.
MAX_READY_BODY_BYTES: Final = 1024
#: A launch session covers at most this many cases (a batch is six).
MAX_CASE_COUNT: Final = 64
#: Plan D7 deadlines (wall seconds): healthy servers, and each run's rendered ack.
SERVER_READY_SECONDS: Final = 60.0
RUN_READY_SECONDS: Final = 60.0
#: How often the barrier looks for the acknowledgment.
READY_POLL_SECONDS: Final = 0.25

# ---------------------------------------------------------------------------
# THE liveness invariant: what ``session.json``'s ``updated_at`` means, per state
# ---------------------------------------------------------------------------
#
# One process writes a session at a time; ownership changes hands only at a state
# transition that the new owner performs (the launcher waits while its game process
# owns the session). ``updated_at`` is a heartbeat in exactly one state:
#
# ============== ======================= =========================== =====================
# state          single writer           ``updated_at`` cadence      the ONE liveness
#                                                                     question the page asks
# ============== ======================= =========================== =====================
# preparing      launcher (``create``,   once, at the transition     older than
#                ``before_case``)        (a stamp)                   LAUNCH_SILENCE_SECONDS?
# starting       game process barrier    at once, then every         older than
#                (``ready_hook``)        LAUNCH_HEARTBEAT_SECONDS    STARTING_SILENCE_SECONDS?
#                                        (a heartbeat)
# running        game process (release)  once, at release (a stamp)  never the session's age:
#                                                                     the RUN's own record is
#                                                                     the heartbeat; asked only
#                                                                     while that run is shown
#                                                                     (stale; or no observation
#                                                                     for FIRST_OBSERVATION_
#                                                                     SILENCE_SECONDS), else no
#                                                                     claim
# between_games  launcher (``after_case``) once, after scoring       older than
#                                        (a stamp)                   LAUNCH_SILENCE_SECONDS?
# finished,      whoever ended it        once; never changes again   none
# failed,
# stopped
# ============== ======================= =========================== =====================
#
# The page follows this table in one function, ``launchLiveness`` in
# ``frontend/src/types/jev.ts``, and decides "following paused" before "silent". Its
# limits mirror the constants below (a parity test pins them).

#: The barrier re-publishes ``starting`` this often while it waits (the heartbeat).
LAUNCH_HEARTBEAT_SECONDS: Final = 5.0
#: A ``starting`` session older than this: the barrier stopped (four missed heartbeats).
STARTING_SILENCE_SECONDS: Final = 20.0
#: A ``preparing``/``between_games`` session older than this: the launcher stopped. Its
#: own gaps fit well inside: game process start-up and recording, scoring a case, the
#: benchmark preflight (one probe of at most 10 s, a source capture).
LAUNCH_SILENCE_SECONDS: Final = 120.0
#: A shown run still ``starting`` (no game observation yet) whose record is older than
#: this, while its session is ``running``: SC2 never reported. The run record is written
#: before the barrier (at most RUN_READY_SECONDS), then burnysc2 waits up to 180 s for SC2
#: to accept a connection (``sc2process._connect``); 60 s more covers creating the game.
FIRST_OBSERVATION_SILENCE_SECONDS: Final = RUN_READY_SECONDS + 180.0 + 60.0
#: The dashboard the launcher opens and the backend it verifies (loopback only).
DASHBOARD_URL: Final = "http://localhost:3000"
API_URL: Final = "http://localhost:8765"
#: Browser origins allowed to acknowledge readiness: the dashboard the launcher opens
#: (the Vite proxy forwards the page's Origin unchanged). CORS is not broadened.
DEFAULT_LAUNCH_ORIGINS: Final[frozenset[str]] = frozenset(
    {DASHBOARD_URL, DASHBOARD_URL.replace("//localhost:", "//127.0.0.1:")}
)
#: A dashboard problem is reported with at most this many characters.
MAX_DASHBOARD_PROBLEM_CHARS: Final = 400
#: The runner option a game process joins a session with (``jev.runner`` declares it).
LAUNCH_SESSION_FLAG: Final = "--launch-session"

LaunchState = Literal[
    "preparing", "starting", "running", "between_games", "finished", "failed", "stopped"
]
LAUNCH_STATES: Final[tuple[LaunchState, ...]] = get_args(LaunchState)
#: States after which the session never changes again.
TERMINAL_LAUNCH_STATES: Final[frozenset[LaunchState]] = frozenset({"finished", "failed", "stopped"})
#: States that name the run being launched or played.
_RUN_STATES: Final[frozenset[LaunchState]] = frozenset({"starting", "running"})

#: The launch API's own error codes (plan D7) -- never the RunState ErrorCode union.
LaunchErrorCode = Literal[
    "launch_forbidden",
    "invalid_launch_request",
    "launch_not_found",
    "launch_not_ready",
    "corrupt_launch",
]
LAUNCH_ERROR_CODES: Final[tuple[LaunchErrorCode, ...]] = get_args(LaunchErrorCode)
LAUNCH_ERROR_STATUS: Final[Mapping[LaunchErrorCode, int]] = {
    "launch_forbidden": 403,
    "invalid_launch_request": 422,
    "launch_not_found": 404,
    "launch_not_ready": 409,
    "corrupt_launch": 503,
}

_SESSION_KEYS: Final = (
    "schema_version",
    "session_id",
    "active_run_id",
    "state",
    "case_index",
    "case_count",
    "updated_at",
    "message",
)
_READY_KEYS: Final = ("schema_version", "session_id", "run_id", "policy_hash", "updated_at")
_READY_BODY_KEYS: Final = frozenset({"run_id", "policy_hash"})
_LOOPBACK_NAMES: Final = frozenset({"localhost"})
_HOST_RE: Final = re.compile(
    r"\A(?:\[(?P<v6>[0-9A-Fa-f:.]+)\]|(?P<name>[A-Za-z0-9.-]+))(?::\d{1,5})?\Z"
)
_INVALID_SESSION: Final = "the launch session id must be a lowercase UUID4 hex string"
_NOT_FOUND: Final = "no launch session with this id exists"
_HTTP_TIMEOUT_SECONDS: Final = 3.0


# ---------------------------------------------------------------------------
# Errors and identifiers
# ---------------------------------------------------------------------------


class LaunchError(Exception):
    """A launch request or record the launch API answers with an error document.

    ``code`` is a :data:`LaunchErrorCode`; ``message`` is fixed, rendered text that
    never echoes request or file content.
    """

    def __init__(self, code: LaunchErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: LaunchErrorCode = code
        self.message = render_text(message)[:MAX_LAUNCH_MESSAGE_CHARS]

    @property
    def status(self) -> int:
        return LAUNCH_ERROR_STATUS[self.code]

    def document(self) -> dict[str, JsonValue]:
        return launch_error_document(self.code, self.message)


def launch_error_document(code: LaunchErrorCode, message: str) -> dict[str, JsonValue]:
    """The dashboard error envelope carrying a launch error code."""
    return {
        "schema_version": LAUNCH_SCHEMA_VERSION,
        "error": {"code": code, "message": render_text(message)[:MAX_LAUNCH_MESSAGE_CHARS]},
    }


class LaunchAborted(Exception):
    """The barrier did not release (no acknowledgment in time, or the session ended)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = render_text(message)[:MAX_LAUNCH_MESSAGE_CHARS]


class DashboardUnavailable(Exception):
    """The dashboard cannot be used for a launch; the launch stops visibly (no fallback)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = render_text(message)[:MAX_DASHBOARD_PROBLEM_CHARS]


class _CorruptLaunch(Exception):
    pass


def new_session_id() -> str:
    """A fresh launch session ID: lowercase UUID4 hex, independent of any run ID."""
    return uuid.uuid4().hex


def is_valid_session_id(value: object) -> bool:
    """True for a lowercase UUID4 hex string (the same shape as run IDs)."""
    return isinstance(value, str) and is_valid_run_id(value)


def launch_root_for(run_root: Path) -> Path:
    """THE launch storage of a run root: its sibling ``launches`` folder."""
    return run_root.parent / LAUNCH_ROOT_NAME


def session_url(dashboard_url: str, session_id: str) -> str:
    """The exact page a launch opens: the Jev tab following this session."""
    if not is_valid_session_id(session_id):
        raise ValueError(_INVALID_SESSION)
    return f"{dashboard_url.rstrip('/')}/?tab=jev&launch={session_id}"


def _bounded_message(text: str) -> str:
    return render_text(text)[:MAX_LAUNCH_MESSAGE_CHARS]


def _is_int_in(value: object, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _is_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        timestamp_seconds(value)
    except (ValueError, TypeError, OverflowError):
        return False
    return True


def _encode(document: Mapping[str, JsonValue]) -> bytes:
    text = json.dumps(document, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    return text.encode("ascii")


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LaunchSession:
    """``session.json`` (plan section 5). Invalid values raise ValueError."""

    session_id: str
    active_run_id: str | None
    state: LaunchState
    case_index: int
    case_count: int
    updated_at: str
    message: str = ""

    def __post_init__(self) -> None:
        if not is_valid_session_id(self.session_id):
            raise ValueError(_INVALID_SESSION)
        if self.active_run_id is not None and not is_valid_run_id(self.active_run_id):
            raise ValueError("active_run_id must be null or a lowercase UUID4 hex run id")
        if self.state not in LAUNCH_STATES:
            raise ValueError(f"state must be one of {LAUNCH_STATES}, got {safe_repr(self.state)}")
        if self.state in _RUN_STATES and self.active_run_id is None:
            raise ValueError(f"a {self.state} session names its run")
        if not _is_int_in(self.case_count, 1, MAX_CASE_COUNT):
            raise ValueError(f"case_count must be an integer in 1..{MAX_CASE_COUNT}")
        if not _is_int_in(self.case_index, 0, self.case_count - 1):
            raise ValueError("case_index must be an integer in 0..case_count-1")
        if not _is_timestamp(self.updated_at):
            raise ValueError("updated_at must be an ISO 8601 UTC timestamp")
        if (
            not isinstance(self.message, str)
            or len(self.message) > MAX_LAUNCH_MESSAGE_CHARS
            or render_text(self.message) != self.message
        ):
            raise ValueError(
                f"message must be printable text of at most {MAX_LAUNCH_MESSAGE_CHARS} characters"
            )
        if len(_encode(self.to_dict())) > MAX_LAUNCH_RECORD_BYTES:
            raise ValueError(f"a launch session is at most {MAX_LAUNCH_RECORD_BYTES} bytes")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": LAUNCH_SCHEMA_VERSION,
            "session_id": self.session_id,
            "active_run_id": self.active_run_id,
            "state": self.state,
            "case_index": self.case_index,
            "case_count": self.case_count,
            "updated_at": self.updated_at,
            "message": self.message,
        }

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_LAUNCH_STATES


@dataclass(frozen=True)
class LaunchReady:
    """``ready.json`` (plan section 5): the dashboard rendered this exact run."""

    session_id: str
    run_id: str
    policy_hash: str
    updated_at: str

    def __post_init__(self) -> None:
        if not is_valid_session_id(self.session_id):
            raise ValueError(_INVALID_SESSION)
        if not is_valid_run_id(self.run_id):
            raise ValueError("run_id must be a lowercase UUID4 hex run id")
        if not full_match(POLICY_HASH_RE, self.policy_hash):
            raise ValueError("policy_hash must be 64 lowercase hex digits")
        if not _is_timestamp(self.updated_at):
            raise ValueError("updated_at must be an ISO 8601 UTC timestamp")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": LAUNCH_SCHEMA_VERSION,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "policy_hash": self.policy_hash,
            "updated_at": self.updated_at,
        }

    def matches(self, session_id: str, run_id: str, policy_hash: str) -> bool:
        return (self.session_id, self.run_id, self.policy_hash) == (
            session_id,
            run_id,
            policy_hash,
        )


# ---------------------------------------------------------------------------
# Paths and bounded reads (validated exactly as run storage is)
# ---------------------------------------------------------------------------


def resolve_root(root: Path) -> Path | None:
    """``root`` resolved strictly, or None when it is missing, unreadable or not a folder."""
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError):  # missing, unreadable, or a link loop
        return None
    return resolved if resolved.is_dir() else None


def resolve_child_dir(root: Path | None, name: str) -> Path | None:
    """The folder ``name`` directly inside the resolved ``root``, or None.

    A link resolving anywhere else, a name the filesystem matched with another
    spelling (case-insensitive filesystems), or anything that is not a folder is
    not that entry. Shared by the run and launch routes.
    """
    if root is None:
        return None
    try:
        child = (root / name).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if child.parent != root or child.name != name or not child.is_dir():
        return None
    return child


def _session_dir(launch_root: Path, session_id: str) -> Path:
    if not is_valid_session_id(session_id):
        raise LaunchError("invalid_launch_request", _INVALID_SESSION)
    directory = resolve_child_dir(resolve_root(launch_root), session_id)
    if directory is None:
        raise LaunchError("launch_not_found", _NOT_FOUND)
    return directory


def _open_record(directory: Path, name: str) -> Any:
    try:
        base = directory.resolve(strict=True)
        path = (directory / name).resolve(strict=True)
    except FileNotFoundError:
        return None
    if path.parent != base or not path.is_file():
        raise _CorruptLaunch(f"{name} is not a regular file in the session folder")
    try:
        return path.open("rb")
    except FileNotFoundError:  # removed since it was resolved
        return None


def _read_record(directory: Path, name: str) -> dict[str, Any] | None:
    """A launch record as a strict JSON object, None if absent; never reads past the cap."""
    handle = None
    for delay in (*SHARING_RETRY_DELAYS, None):
        try:
            handle = _open_record(directory, name)
            break
        except PermissionError:  # a writer's os.replace in flight (Windows)
            if delay is None:
                raise _CorruptLaunch(f"{name} cannot be read") from None
            time.sleep(delay)
        except (OSError, RuntimeError):
            raise _CorruptLaunch(f"{name} cannot be read") from None
    if handle is None:
        return None
    with handle:
        try:
            data = handle.read(MAX_LAUNCH_RECORD_BYTES + 1)
        except OSError:
            raise _CorruptLaunch(f"{name} cannot be read") from None
    if len(data) > MAX_LAUNCH_RECORD_BYTES:
        raise _CorruptLaunch(f"{name} is larger than {MAX_LAUNCH_RECORD_BYTES} bytes")
    try:
        document = parse_json_document(data, what=name, max_bytes=MAX_LAUNCH_RECORD_BYTES)
    except PolicyError:
        raise _CorruptLaunch(f"{name} is not a strict JSON object") from None
    if json_value_issues(document, name):
        raise _CorruptLaunch(f"{name} holds text that is not valid Unicode")
    return document


def _keys(document: Mapping[str, Any], expected: tuple[str, ...], name: str) -> None:
    if set(document) != set(expected) or document.get("schema_version") != LAUNCH_SCHEMA_VERSION:
        raise _CorruptLaunch(f"{name} does not have the expected fields and schema version")


def read_session(launch_root: Path, session_id: str) -> LaunchSession:
    """The validated session; raises :class:`LaunchError` (422, 404 or 503) only."""
    directory = _session_dir(launch_root, session_id)
    try:
        document = _read_record(directory, SESSION_FILE)
        if document is None:
            raise LaunchError("launch_not_found", _NOT_FOUND)
        _keys(document, _SESSION_KEYS, SESSION_FILE)
        session = LaunchSession(
            session_id=document["session_id"],
            active_run_id=document["active_run_id"],
            state=document["state"],
            case_index=document["case_index"],
            case_count=document["case_count"],
            updated_at=document["updated_at"],
            message=document["message"],
        )
    except _CorruptLaunch as exc:
        raise LaunchError("corrupt_launch", str(exc)) from None
    except (ValueError, TypeError):
        raise LaunchError("corrupt_launch", f"{SESSION_FILE} is malformed") from None
    if session.session_id != session_id:
        raise LaunchError("corrupt_launch", f"{SESSION_FILE} names another session")
    return session


def read_ready(launch_root: Path, session_id: str) -> LaunchReady | None:
    """The session's readiness receipt, None when there is none; raises LaunchError only."""
    directory = _session_dir(launch_root, session_id)
    try:
        document = _read_record(directory, READY_FILE)
        if document is None:
            return None
        _keys(document, _READY_KEYS, READY_FILE)
        return LaunchReady(
            session_id=document["session_id"],
            run_id=document["run_id"],
            policy_hash=document["policy_hash"],
            updated_at=document["updated_at"],
        )
    except _CorruptLaunch as exc:
        raise LaunchError("corrupt_launch", str(exc)) from None
    except (ValueError, TypeError):
        raise LaunchError("corrupt_launch", f"{READY_FILE} is malformed") from None


# ---------------------------------------------------------------------------
# The session's single active owner
# ---------------------------------------------------------------------------


class _Unset(enum.Enum):
    UNSET = 0


_UNSET: Final = _Unset.UNSET


class LaunchSessionWriter:
    """The one writer of a session's ``session.json`` at any time.

    :meth:`create` makes a new session folder (never reusing one) in state
    ``preparing``; :meth:`adopt` hands an existing, still-open session to the game
    process of one case while its launcher waits. Each :meth:`publish` re-reads the
    record first (the previous owner may have advanced it) and replaces it atomically.
    """

    def __init__(
        self,
        launch_root: Path,
        session: LaunchSession,
        *,
        files: EvidenceFiles | None = None,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self.launch_root = launch_root
        self._session = session
        self._files = EvidenceFiles() if files is None else files
        self._wall_time = wall_time

    @property
    def session(self) -> LaunchSession:
        return self._session

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @classmethod
    def create(
        cls,
        launch_root: Path,
        *,
        case_count: int = 1,
        message: str = "",
        session_id: str | None = None,
        files: EvidenceFiles | None = None,
        wall_time: Callable[[], float] = time.time,
    ) -> LaunchSessionWriter:
        """A new session in ``preparing``; raises OSError if its folder cannot be made."""
        if not launch_root.is_absolute():
            raise ValueError("the launch root must be an absolute path")
        chosen = new_session_id() if session_id is None else session_id
        session = LaunchSession(
            session_id=chosen,
            active_run_id=None,
            state="preparing",
            case_index=0,
            case_count=case_count,
            updated_at=utc_timestamp(wall_time()),
            message=_bounded_message(message),
        )
        launch_root.mkdir(parents=True, exist_ok=True)
        (launch_root / chosen).mkdir()  # exclusive: an existing session is never reused
        writer = cls(launch_root, session, files=files, wall_time=wall_time)
        writer._write(session)
        return writer

    @classmethod
    def adopt(
        cls,
        launch_root: Path,
        session_id: str,
        *,
        files: EvidenceFiles | None = None,
        wall_time: Callable[[], float] = time.time,
    ) -> LaunchSessionWriter:
        """An existing open session; LaunchError if it is missing, corrupt or has ended."""
        session = read_session(launch_root, session_id)
        if session.terminal:
            raise LaunchError("launch_not_ready", f"the launch session already {session.state}")
        return cls(launch_root, session, files=files, wall_time=wall_time)

    def refresh(self) -> LaunchSession:
        """Re-read the record (another owner may have advanced it)."""
        self._session = read_session(self.launch_root, self.session_id)
        return self._session

    def publish(
        self,
        state: LaunchState,
        *,
        message: str = "",
        active_run_id: str | None | _Unset = _UNSET,
        case_index: int | _Unset = _UNSET,
    ) -> LaunchSession:
        """Replace ``session.json`` with ``state`` (other fields kept unless given).

        A session that has ended is never reopened: publishing a non-terminal state
        over a terminal one raises :class:`LaunchError` (``launch_not_ready``).
        """
        with contextlib.suppress(LaunchError):
            self.refresh()
        current = self._session
        if current.terminal and state not in TERMINAL_LAUNCH_STATES:
            raise LaunchError("launch_not_ready", f"the launch session already {current.state}")
        session = LaunchSession(
            session_id=current.session_id,
            active_run_id=(
                current.active_run_id if isinstance(active_run_id, _Unset) else active_run_id
            ),
            state=state,
            case_index=current.case_index if isinstance(case_index, _Unset) else case_index,
            case_count=current.case_count,
            updated_at=utc_timestamp(self._wall_time()),
            message=_bounded_message(message),
        )
        self._write(session)
        return session

    def end(self, state: LaunchState, message: str) -> LaunchSession:
        """Publish a terminal ``state`` unless the session already ended (that is kept)."""
        with contextlib.suppress(LaunchError):
            self.refresh()
        if self._session.terminal:
            return self._session
        return self.publish(state, message=message)

    def _write(self, session: LaunchSession) -> None:
        path = self.launch_root / session.session_id / SESSION_FILE
        self._files.write(path, _encode(session.to_dict()))
        self._session = session


# ---------------------------------------------------------------------------
# The readiness barrier (the runner's on_recorded hook)
# ---------------------------------------------------------------------------

_Verdict = Literal["wait", "release", "ended"]


def ready_hook(
    writer: LaunchSessionWriter,
    policy_hash: str,
    *,
    deadline_seconds: float = RUN_READY_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = READY_POLL_SECONDS,
    heartbeat_seconds: float = LAUNCH_HEARTBEAT_SECONDS,
) -> Callable[[str], None]:
    """The :func:`jev.runner.run_match` ``on_recorded`` hook for one case of a session.

    Publishes the recorded run as the session's ``starting`` run, then returns only
    once ``ready.json`` names this session, this run and ``policy_hash`` and the
    session still names this run as starting (the receipt is validated again here,
    not only by the API). While it waits it re-publishes ``starting`` every
    ``heartbeat_seconds``, so the page can tell this pause from a dead launcher.
    Otherwise it raises :class:`LaunchAborted`: after ``deadline_seconds`` (the
    session ends ``failed``, naming the last problem seen, if any), or at once when
    another process already ended the session (kept as it is). Ctrl+C at any point,
    the first publish included, ends the session ``stopped`` and propagates. A
    receipt from another run or session never releases it.
    """
    if not full_match(POLICY_HASH_RE, policy_hash):
        raise ValueError("policy_hash must be 64 lowercase hex digits")

    def hook(run_id: str) -> None:
        session_id = writer.session_id
        short = run_id[:8]
        waiting = f"waiting for the dashboard to show run {short} before SC2 starts"
        problem: str | None = None
        try:
            try:
                writer.publish("starting", active_run_id=run_id, message=waiting)
            except (OSError, ValueError, LaunchError) as exc:
                raise LaunchAborted(
                    f"the launch session could not be updated: {safe_exception_text(exc)}"
                ) from exc
            deadline = clock() + deadline_seconds
            beat = clock()
            while True:
                verdict, detail = _barrier(writer, session_id, run_id, policy_hash)
                if verdict == "release":
                    writer.publish(
                        "running",
                        active_run_id=run_id,
                        message=f"the dashboard shows run {short}; starting SC2",
                    )
                    return
                if verdict == "ended":
                    raise LaunchAborted(f"the launch session was {detail} by another process")
                problem = detail or problem
                now = clock()
                if now >= deadline:
                    break
                if now - beat >= heartbeat_seconds:
                    beat = now
                    with contextlib.suppress(OSError, ValueError, LaunchError):
                        writer.publish("starting", active_run_id=run_id, message=waiting)
                sleep(poll_seconds)
        except KeyboardInterrupt:
            with contextlib.suppress(Exception):
                writer.end("stopped", f"stopped before SC2 started for run {short}")
            raise
        message = (
            f"the dashboard did not show run {short} within {deadline_seconds:g} s; "
            "SC2 was not started"
        )
        if problem is not None:
            message = f"{message} (last problem: {problem})"
        with contextlib.suppress(Exception):
            writer.end("failed", message)
        raise LaunchAborted(message)

    return hook


def _barrier(
    writer: LaunchSessionWriter, session_id: str, run_id: str, policy_hash: str
) -> tuple[_Verdict, str | None]:
    """One look at the barrier: release, keep waiting (with a problem, if any), or ended."""
    try:
        session = read_session(writer.launch_root, session_id)
    except LaunchError as exc:  # never released; the timeout names it
        return "wait", f"{exc.code}: {exc.message}"
    if session.terminal:
        return "ended", session.state
    if session.state != "starting" or session.active_run_id != run_id:
        return "wait", "the launch session no longer names this run as starting"
    try:
        ready = read_ready(writer.launch_root, session_id)
    except LaunchError as exc:  # a corrupt receipt never releases the barrier
        return "wait", f"{exc.code}: {exc.message}"
    if ready is None or not ready.matches(session_id, run_id, policy_hash):
        return "wait", None  # no receipt yet, or a stale one for another run
    return "release", None


# ---------------------------------------------------------------------------
# The readiness endpoint's checks (used by jev.api)
# ---------------------------------------------------------------------------


def _is_loopback_address(host: str) -> bool:
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


def check_launch_client(
    client_host: str | None,
    host_header: str | None,
    origin: str | None,
    allowed_origins: Collection[str],
) -> None:
    """Only a loopback client, on a loopback Host, from an allowed browser Origin.

    Raises :class:`LaunchError` ``launch_forbidden`` otherwise (a missing Origin
    included). The Origin check is a browser-side defense: browsers cannot forge
    Origin, so a cross-site page can never acknowledge. A local non-browser process
    can send any Origin; requests relayed by the Vite dev proxy always come from
    loopback, so for them Origin is the only gate. The worst such a forger can do is
    release this session's own barrier early: the endpoint writes nothing else and
    controls no game, and session IDs are 122 random bits.
    """
    if client_host is None or not _is_loopback_address(client_host):
        raise LaunchError("launch_forbidden", "readiness is accepted only from this computer")
    match = None if host_header is None else _HOST_RE.match(host_header.strip())
    host = None if match is None else (match.group("v6") or match.group("name"))
    if host is None or not _is_loopback_address(host):
        raise LaunchError("launch_forbidden", "readiness is accepted only on a loopback host")
    if origin is None or origin not in allowed_origins:
        raise LaunchError(
            "launch_forbidden", "readiness is accepted only from the dashboard page's origin"
        )


def accept_ready(
    launch_root: Path,
    run_root: Path,
    session_id: str,
    body: bytes,
    *,
    files: EvidenceFiles | None = None,
    wall_time: Callable[[], float] = time.time,
) -> dict[str, JsonValue]:
    """Record that the dashboard rendered the session's exact starting run.

    ``body`` is ``{"run_id", "policy_hash"}``. Accepted only for a session the
    launcher created, in state ``starting`` with that active run, whose archived
    policy verifies and has that hash; then ``ready.json`` is replaced atomically.
    Repeating it for the same active run (also once it is ``running``) is
    idempotent. Raises :class:`LaunchError`: 422 malformed, 404 no session, 409 wrong
    run/hash or not starting, 503 corrupt session or unwritable receipt.
    """
    if not is_valid_session_id(session_id):
        raise LaunchError("invalid_launch_request", _INVALID_SESSION)
    run_id, policy_hash = _ready_body(body)
    session = read_session(launch_root, session_id)
    if session.active_run_id != run_id or session.state not in _RUN_STATES:
        raise LaunchError("launch_not_ready", "the launch session is not starting this run")
    receipt: dict[str, JsonValue] = {
        "schema_version": LAUNCH_SCHEMA_VERSION,
        "ready": True,
        "session_id": session_id,
        "run_id": run_id,
    }
    try:
        existing = read_ready(launch_root, session_id)
    except LaunchError:
        existing = None  # replaced below if the session is still starting
    if existing is not None and existing.matches(session_id, run_id, policy_hash):
        return receipt  # the same acknowledgment again
    if session.state != "starting":
        raise LaunchError("launch_not_ready", "the launch session is not starting this run")
    _check_archive(run_root, run_id, policy_hash)
    ready = LaunchReady(session_id, run_id, policy_hash, utc_timestamp(wall_time()))
    writer = EvidenceFiles() if files is None else files
    try:
        writer.write(launch_root / session_id / READY_FILE, _encode(ready.to_dict()))
    except OSError:
        raise LaunchError("corrupt_launch", "the readiness receipt could not be written") from None
    return receipt


def _ready_body(body: bytes) -> tuple[str, str]:
    invalid = LaunchError(
        "invalid_launch_request",
        "the body must be {run_id: UUID4 hex, policy_hash: 64 lowercase hex}",
    )
    if not isinstance(body, bytes) or len(body) > MAX_READY_BODY_BYTES:
        raise invalid
    try:
        document = parse_json_document(body, what="readiness", max_bytes=MAX_READY_BODY_BYTES)
    except PolicyError:
        raise invalid from None
    if set(document) != _READY_BODY_KEYS:
        raise invalid
    run_id, policy_hash = document["run_id"], document["policy_hash"]
    if not isinstance(run_id, str) or not is_valid_run_id(run_id):
        raise invalid
    if not full_match(POLICY_HASH_RE, policy_hash):
        raise invalid
    return run_id, policy_hash


def _check_archive(run_root: Path, run_id: str, policy_hash: str) -> None:
    """The run is recorded in ``run_root`` and its archived policy verifies with this hash."""
    run_dir = resolve_child_dir(resolve_root(run_root), run_id)
    not_ready = LaunchError("launch_not_ready", "the run's archived policy does not match")
    if run_dir is None:
        raise not_ready
    try:
        metadata = read_run_metadata(run_dir)
        if metadata is None:
            raise not_ready
        read_policy_archive(run_dir, metadata)  # re-hashes the archive against the metadata
    except CorruptRun:
        raise not_ready from None
    if metadata.policy_hash != policy_hash:
        raise not_ready


# ---------------------------------------------------------------------------
# Launcher side: servers, health, the one browser tab
# ---------------------------------------------------------------------------


def _key_name() -> str:
    """The service key's variable name; its one owner is :data:`jev.benchmark.KEY_ENV`."""
    from jev.benchmark import KEY_ENV  # deferred: jev.benchmark imports this module

    return KEY_ENV


def scrubbed_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """``environ`` without the service key (UI services and the browser never get it)."""
    key = _key_name()
    return {k: v for k, v in environ.items() if k.upper() != key}


def _system_root(environ: Mapping[str, str]) -> Path:
    """``SystemRoot`` looked up case-insensitively (Windows upper-cases ``os.environ`` keys)."""
    for name, value in environ.items():
        if name.upper() == "SYSTEMROOT" and value:
            return Path(value)
    return Path(r"C:\Windows")


@dataclass(frozen=True)
class DashboardEndpoints:
    """Where the backend API and the dashboard page answer (loopback)."""

    api_url: str = API_URL
    dashboard_url: str = DASHBOARD_URL


#: The most of a probe response the launcher reads (and parses, depth-checked).
_MAX_PROBE_BYTES: Final = 256 * 1024


def _http_get(url: str, timeout: float = _HTTP_TIMEOUT_SECONDS) -> tuple[int, bytes]:
    """GET ``url`` without any proxy; (status, body prefix). OSError when nothing answers."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    try:
        with opener.open(request, timeout=timeout) as response:
            return int(response.status), response.read(_MAX_PROBE_BYTES)
    except urllib.error.HTTPError as exc:
        with contextlib.closing(exc):
            return int(exc.code), exc.read(64 * 1024)


def _json_body(data: bytes) -> dict[str, Any] | None:
    """A probe answer as a JSON object, or None (bounded, depth-checked, never raises)."""
    try:
        return parse_json_document(data, what="probe answer", max_bytes=_MAX_PROBE_BYTES)
    except PolicyError:
        return None


def _jev_api_problem(base_url: str) -> str | None:
    """None when ``base_url`` answers the Jev run list correctly; otherwise why not."""
    url = f"{base_url.rstrip('/')}/api/jev/runs"
    try:
        status, data = _http_get(url)
    except (OSError, ValueError) as exc:
        return f"{url} does not answer ({safe_exception_text(exc)})"
    document = _json_body(data)
    if status != 200 or document is None:
        return f"{url} answered HTTP {status} without a Jev run list"
    if document.get("schema_version") != 1 or not isinstance(document.get("runs"), list):
        return f"{url} answered something other than a Jev run list"
    return None


def dashboard_health(endpoints: DashboardEndpoints) -> str | None:
    """None when the backend serves the Jev API and the page proxies it; otherwise why not.

    Occupied ports are not proof: the backend must answer the Jev run list, the
    dashboard must serve its page, and the same Jev API must answer through it.
    """
    problem = _jev_api_problem(endpoints.api_url)
    if problem is not None:
        return f"backend: {problem}"
    page = f"{endpoints.dashboard_url.rstrip('/')}/"
    try:
        status, data = _http_get(page)
    except (OSError, ValueError) as exc:
        return f"dashboard: {page} does not answer ({safe_exception_text(exc)})"
    if status != 200 or b'id="root"' not in data:
        return f"dashboard: {page} answered HTTP {status} without the dashboard page"
    problem = _jev_api_problem(endpoints.dashboard_url)
    if problem is not None:
        return f"dashboard proxy: {problem}"
    return None


def verify_session_served(
    endpoints: DashboardEndpoints,
    session_id: str,
    *,
    deadline_seconds: float = SERVER_READY_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """None when both the backend and the page's proxy serve this exact session.

    A server running from another checkout (another data root) or an older backend
    without the launch routes answers 404: that is refused at once, never used. A probe
    that gets no answer (a healthy server can stall for seconds) is retried within the
    server-readiness deadline before the launch is refused.
    """
    deadline = clock() + deadline_seconds
    for label, base in (("backend", endpoints.api_url), ("dashboard", endpoints.dashboard_url)):
        url = f"{base.rstrip('/')}/api/jev/launches/{session_id}"
        while True:
            try:
                status, data = _http_get(url)
                break
            except (OSError, ValueError) as exc:
                if clock() >= deadline:
                    return f"{label}: {url} does not answer ({safe_exception_text(exc)})"
                sleep(1.0)
        document = _json_body(data)
        if status != 200 or document is None or document.get("session_id") != session_id:
            return (
                f"the running {label} does not serve this launch session (HTTP {status}): it "
                "serves another data root or predates the launch API; stop that dashboard "
                "and start it from this checkout"
            )
    return None


def start_dashboard_servers(
    source_root: Path,
    environ: Mapping[str, str],
    *,
    run: Callable[..., Any] = subprocess.run,
) -> None:
    """Start or reuse the servers: ``launch-a4g.ps1 -NoBrowser -NoWait`` (hidden windows).

    The servers inherit ``environ`` without the service key. Only Windows has the
    launcher; elsewhere start the dashboard first (``bash scripts/start-dev.sh``).
    """
    script = source_root / "scripts" / "launch-a4g.ps1"
    if sys.platform != "win32":
        raise DashboardUnavailable(
            "the dashboard is not running; start it (bash scripts/start-dev.sh) or pass "
            "--no-dashboard for a headless run"
        )
    windows_ps = _system_root(environ) / "System32" / "WindowsPowerShell" / "v1.0"
    argv = [
        str(windows_ps / "powershell.exe"),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-NoBrowser",
        "-NoWait",
    ]
    try:
        run(
            argv,
            cwd=source_root,
            env=scrubbed_environment(environ),
            stdin=subprocess.DEVNULL,
            timeout=SERVER_READY_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DashboardUnavailable(
            f"the dashboard servers could not be started: {safe_exception_text(exc)}"
        ) from exc


def wait_for_dashboard(
    endpoints: DashboardEndpoints,
    *,
    start: Callable[[], None] | None,
    deadline_seconds: float = SERVER_READY_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Reuse healthy servers, else start them once and wait; DashboardUnavailable on timeout."""
    deadline = clock() + deadline_seconds
    problem = dashboard_health(endpoints)
    if problem is None:
        return
    if start is not None:
        start()
    while clock() < deadline:
        sleep(1.0)
        problem = dashboard_health(endpoints)
        if problem is None:
            return
    raise DashboardUnavailable(
        f"the dashboard was not healthy within {deadline_seconds:g} s: {problem}"
    )


def open_browser(
    url: str,
    environ: Mapping[str, str],
    *,
    popen: Callable[..., Any] = subprocess.Popen,
) -> str | None:
    """Open ``url`` in the default browser once; None on success, else why not.

    The opener gets an environment without the service key, so a browser it starts
    never inherits it.
    """
    env = scrubbed_environment(environ)
    if sys.platform == "win32":
        rundll32 = _system_root(environ) / "System32" / "rundll32.exe"
        argv = [str(rundll32), "url.dll,FileProtocolHandler", url]
    elif sys.platform == "darwin":
        argv = ["open", url]
    else:
        argv = ["xdg-open", url]
    try:
        popen(
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return safe_exception_text(exc)
    return None


class DashboardLaunch:
    """The launcher side of one session: healthy servers, the session, one browser tab.

    :meth:`open` does plan D7 steps 1-2 and fails visibly with
    :class:`DashboardUnavailable` (never a silent headless fallback). Each case's
    game process then receives :meth:`child_arguments`; between cases
    :meth:`before_case` advances the session, and :meth:`end` closes it.
    """

    def __init__(self, writer: LaunchSessionWriter, url: str) -> None:
        self.writer = writer
        self.url = url

    @property
    def session_id(self) -> str:
        return self.writer.session_id

    @classmethod
    def open(
        cls,
        run_root: Path,
        *,
        case_count: int,
        environ: Mapping[str, str],
        endpoints: DashboardEndpoints | None = None,
        source_root: Path | None = None,
        start: Callable[[], None] | None = None,
        opener: Callable[[str], str | None] | None = None,
        out: Callable[[str], None] = print,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_time: Callable[[], float] = time.time,
    ) -> DashboardLaunch:
        """Healthy servers, a new session the servers verifiably serve, the tab opened.

        Once the session exists, anything that ends this early (Ctrl+C included) ends
        the session too, ``stopped`` for Ctrl+C and ``failed`` otherwise, so an open
        page never waits on a session nobody will advance.
        """
        chosen = DashboardEndpoints() if endpoints is None else endpoints
        root = repository_root() if source_root is None else source_root
        starter = start if start is not None else (lambda: start_dashboard_servers(root, environ))
        wait_for_dashboard(chosen, start=starter, clock=clock, sleep=sleep)
        try:
            writer = LaunchSessionWriter.create(
                launch_root_for(run_root),
                case_count=case_count,
                message="preparing the first game",
                wall_time=wall_time,
            )
        except (OSError, ValueError) as exc:
            raise DashboardUnavailable(
                f"the launch session could not be created: {safe_exception_text(exc)}"
            ) from exc
        try:
            problem = verify_session_served(chosen, writer.session_id)
            if problem is not None:
                raise DashboardUnavailable(problem)
            url = session_url(chosen.dashboard_url, writer.session_id)
            out(render_text(f"jev launch: dashboard {url}"))
            failure = (opener or (lambda target: open_browser(target, environ)))(url)
            if failure is not None:
                out(
                    render_text(
                        f"jev launch: the browser could not be opened ({failure}); open the "
                        f"URL above within {RUN_READY_SECONDS:g} s of the game being prepared"
                    )
                )
        except BaseException as exc:
            stopped = isinstance(exc, KeyboardInterrupt)
            reason = getattr(exc, "message", None) or safe_exception_text(exc)
            with contextlib.suppress(Exception):
                if stopped:
                    writer.end("stopped", "stopped before the first game started")
                else:
                    writer.end("failed", str(reason))
            raise
        return cls(writer, url)

    def child_arguments(self) -> tuple[str, ...]:
        """What a case's game process is given to join this session."""
        return (LAUNCH_SESSION_FLAG, self.session_id)

    def before_case(self, index: int, count: int) -> None:
        """Advance the session to case ``index`` before its game process starts.

        The session is ``preparing`` with no active run until the game process
        records the new run: the previous game's run (and result) is never shown as
        this case's.
        """
        self.writer.publish(
            "preparing",
            active_run_id=None,
            case_index=index,
            message=f"preparing game {index + 1} of {count}",
        )

    def launch_failure(self) -> str | None:
        """Why the current case's barrier provably never released, else None.

        Only the session itself proves it: ``failed`` (the barrier's deadline ended it)
        or still ``starting`` (the game process ended inside the barrier); release always
        publishes ``running`` first. Anything else, an unreadable session included, is
        None: the case's evidence is then scored the usual, conservative way.
        """
        try:
            session = self.writer.refresh()
        except LaunchError:
            return None
        if session.state == "failed":
            return session.message or "the dashboard launch failed"
        if session.state == "starting":
            return "the game process ended before the dashboard acknowledged its run"
        return None

    def after_case(self, index: int, count: int, summary: str) -> None:
        """A case ended: show its outcome and that the next game follows."""
        with contextlib.suppress(LaunchError):
            if self.writer.refresh().terminal:
                return
        if index + 1 < count:
            with contextlib.suppress(OSError, ValueError, LaunchError):
                self.writer.publish("between_games", message=summary)

    def end(self, state: LaunchState, message: str) -> None:
        """Close the session (a session that already ended keeps its state)."""
        with contextlib.suppress(Exception):
            self.writer.end(state, message)


# ---------------------------------------------------------------------------
# The single-match launcher: python -m jev.launch (scripts/launch-jev.ps1)
# ---------------------------------------------------------------------------

_VERSION_RE: Final = re.compile(r"\Av[1-9][0-9]{0,3}\Z")
#: Plan D7 single-match defaults (launch-jev.ps1 states the same).
DEFAULT_VERSION: Final = "v2"
DEFAULT_PROVIDER: Final = "typesafe"
DEFAULT_DIFFICULTY: Final = 3
DEFAULT_SEED: Final = 11


def build_parser() -> argparse.ArgumentParser:
    """The launcher's options. Map, model and match limits come from their owner,
    :mod:`jev.benchmark` (plan D6), so both dashboard-first paths request the same."""
    from jev.benchmark import LIMIT_CEILINGS, MAP, PINNED_MODEL
    from jev.runner import (
        DEFAULT_OPPONENT_RACE,
        MAX_DIFFICULTY,
        MAX_SEED,
        OPPONENT_RACES,
        TerminalSafeArgumentParser,
        _bounded_int,
        _map_name,
        absolute_path,
    )

    parser = TerminalSafeArgumentParser(
        prog="python -m jev.launch",
        description=(
            "Dashboard-first single Jev match: open the dashboard on the exact run, "
            "then start SC2 only after the page rendered it."
        ),
    )
    parser.add_argument(
        "--version", default=DEFAULT_VERSION, help="Jev package to play (default: %(default)s)"
    )
    parser.add_argument(
        "--decision-provider",
        choices=("scripted", "typesafe"),
        default=DEFAULT_PROVIDER,
        help="army decision source (default: %(default)s)",
    )
    parser.add_argument(
        "--difficulty",
        type=_bounded_int(1, MAX_DIFFICULTY),
        default=DEFAULT_DIFFICULTY,
        help="built-in AI difficulty 1-10 (default: %(default)s)",
    )
    parser.add_argument(
        "--seed",
        type=_bounded_int(0, MAX_SEED),
        default=DEFAULT_SEED,
        help="SC2 game seed (default: %(default)s)",
    )
    parser.add_argument(
        "--opponent-race",
        choices=OPPONENT_RACES,
        default=DEFAULT_OPPONENT_RACE,
        help="built-in opponent race (default: %(default)s)",
    )
    parser.add_argument("--map", type=_map_name, default=MAP, help="SC2 map (default: %(default)s)")
    for flag, name, help_text in (
        ("--max-game-seconds", "match_game_seconds", "end the match at this game time"),
        ("--max-wall-seconds", "match_wall_seconds", "end the match after this wall time"),
        ("--decision-max-requests", "match_requests", "maximum Typesafe requests"),
    ):
        ceiling = LIMIT_CEILINGS[name]
        parser.add_argument(
            flag,
            type=_bounded_int(1, ceiling),
            default=ceiling,
            help=f"{help_text}; may only be lowered (default: %(default)s)",
        )
    parser.add_argument(
        "--decision-model",
        default=PINNED_MODEL,
        help="Typesafe model, the benchmark's pin (default: %(default)s)",
    )
    parser.add_argument(
        "--run-root",
        type=absolute_path,
        default=None,
        help="absolute run root (default: <repository>/data/jev/runs)",
    )
    return parser


def _game_argv(args: argparse.Namespace, run_root: Path, session_id: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        f"bots.jev.{args.version}",
        "--map",
        args.map,
        "--opponent-race",
        args.opponent_race,
        "--difficulty",
        str(args.difficulty),
        "--seed",
        str(args.seed),
        "--max-game-seconds",
        str(args.max_game_seconds),
        "--max-wall-seconds",
        str(args.max_wall_seconds),
        "--decision-provider",
        args.decision_provider,
        "--decision-model",
        args.decision_model,
        "--decision-max-requests",
        str(args.decision_max_requests),
        "--realtime",
        "--run-root",
        str(run_root),
        LAUNCH_SESSION_FLAG,
        session_id,
    ]


def _wait_for_game(
    process: subprocess.Popen[bytes], out: Callable[[str], None]
) -> tuple[int, bool]:
    """Wait for the game process: (exit code, whether Ctrl+C was pressed).

    Ctrl+C reaches the game too, so it leaves cleanly (or stops at the barrier);
    a second Ctrl+C ends only this game's process tree.
    """
    interrupted = False
    while True:
        try:
            return process.wait(timeout=0.5), interrupted
        except subprocess.TimeoutExpired:
            continue
        except KeyboardInterrupt:
            if interrupted:
                from jev.benchmark import terminate_process_tree

                terminate_process_tree(process)
                return process.wait(), True
            interrupted = True
            out("jev launch: stopping; waiting for the match to leave (Ctrl+C again to end it)")


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    endpoints: DashboardEndpoints | None = None,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    open_dashboard: Callable[..., DashboardLaunch] | None = None,
) -> int:
    """``python -m jev.launch``: one dashboard-first match; returns the exit code.

    The key is taken out of this process's environment at once and handed only to
    the game process; the servers and the browser never see it. Ctrl+C at any
    point before the game started ends the session ``stopped`` (exit 130).
    """
    from jev.runner import (
        EXIT_FAILURE,
        EXIT_OK,
        EXIT_STOPPED,
        MatchOptions,
        make_streams_encoding_safe,
    )
    from jev.telemetry import default_run_root

    make_streams_encoding_safe()
    parser = build_parser()
    args = parser.parse_args(argv)
    key_name = _key_name()
    source = os.environ if environ is None else environ
    key = source.get(key_name, "")
    if environ is None:
        os.environ.pop(key_name, None)  # never inherited by the servers or the browser
    base_env = scrubbed_environment(source)
    if not full_match(_VERSION_RE, args.version):
        parser.error(f"--version must look like v2, got {safe_repr(args.version)}")
    try:
        MatchOptions(
            map_name=args.map,
            opponent_race=args.opponent_race,
            difficulty=args.difficulty,
            seed=args.seed,
            max_game_seconds=args.max_game_seconds,
            max_wall_seconds=args.max_wall_seconds,
            realtime=True,
            decision_provider=args.decision_provider,
            decision_model=args.decision_model,
            decision_max_requests=args.decision_max_requests,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if importlib.util.find_spec(f"bots.jev.{args.version}") is None:
        print(
            render_text(
                f"jev launch: bots.jev.{args.version} is not packaged; nothing was started"
            ),
            file=sys.stderr,
        )
        return EXIT_FAILURE
    if args.decision_provider == "typesafe" and not key.strip():
        print(
            f"jev launch: {key_name} is required for --decision-provider typesafe "
            "(launch-jev.ps1 loads the saved encrypted key); nothing was started",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    run_root = default_run_root() if args.run_root is None else args.run_root
    try:
        launch = (open_dashboard or DashboardLaunch.open)(
            run_root, case_count=1, environ=base_env, endpoints=endpoints
        )
    except DashboardUnavailable as exc:
        print(render_text(f"jev launch: dashboard_unavailable: {exc.message}"), file=sys.stderr)
        print("jev launch: no game was started", file=sys.stderr)
        return EXIT_FAILURE
    except KeyboardInterrupt:  # DashboardLaunch.open ended a session it had created
        print("jev launch: stopped; no game was started", file=sys.stderr)
        return EXIT_STOPPED
    game_env = dict(base_env)
    if args.decision_provider == "typesafe":
        game_env[key_name] = key  # the game process only
    try:
        process = popen(
            _game_argv(args, run_root, launch.session_id),
            cwd=repository_root(),
            env=game_env,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        reason = safe_exception_text(exc)
        launch.end("failed", f"the game process could not start: {reason}")
        print(render_text(f"jev launch: the game could not start: {reason}"), file=sys.stderr)
        return EXIT_FAILURE
    except KeyboardInterrupt:
        launch.end("stopped", "stopped before the game started")
        print("jev launch: stopped; no game was started", file=sys.stderr)
        return EXIT_STOPPED
    code, interrupted = _wait_for_game(process, print)
    failure = None if interrupted else launch.launch_failure()
    if interrupted:
        # The game stopped at the barrier (it ended the session itself) or left the
        # match on Ctrl+C; a forced end of its tree is never reported as a finish.
        launch.end("stopped", f"stopped with Ctrl+C (exit code {code})")
    elif failure is not None:
        launch.end("failed", failure)
        print(render_text(f"jev launch: launch failed: {failure}"), file=sys.stderr)
        return code if code != EXIT_OK else EXIT_FAILURE
    try:
        session = launch.writer.refresh()
    except LaunchError as exc:
        print(render_text(f"jev launch: {exc.code}: {exc.message}"), file=sys.stderr)
        return code if code != EXIT_OK else EXIT_FAILURE
    if session.state == "running":
        launch.end("finished", f"the match ended (exit code {code})")
    elif not session.terminal:  # the game process never recorded its run
        launch.end("failed", f"the game process ended (exit code {code}) before its run started")
    with contextlib.suppress(LaunchError):
        session = launch.writer.refresh()
    summary = f"jev launch: session {launch.session_id} {session.state}: {session.message}"
    print(render_text(summary))
    return code


if __name__ == "__main__":
    sys.exit(main())
