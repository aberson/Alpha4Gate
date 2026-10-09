"""Jev run-evidence API (plan section 5, "Read-only API") plus the D7 launch receipt.

:func:`create_router` serves one run root (plan D5) under ``/api/jev``; the
dashboard app (``bots/v13/api.py``) mounts it on ``<repository>/data/jev/runs``:

* ``GET /api/jev/runs`` -- ``{schema_version, runs, truncated, omitted}``: the
  newest :data:`RUN_LIST_LIMIT` runs by metadata ``created_at`` (ties by run ID),
  each ``{run_id, family, version, status, updated_at, policy_hash}``.
  ``truncated`` is True when more runs exist than are listed (or the root holds
  more than :data:`MAX_SCANNED_ENTRIES` entries, beyond which it is not scanned);
  ``omitted`` counts runs left out because a stored record is malformed. A missing
  or empty root is an empty list. Each entry is the run's metadata (needed to
  order by creation) plus the summary that leads its state
  (:func:`jev.telemetry.read_run_summary`), never the whole state, so one listing
  reads at most :data:`MAX_LIST_READ_BYTES` (about 4 MiB: 4096 metadata files of
  at most 1 KiB, 50 state prefixes of 512 bytes).
* ``GET /api/jev/runs/{run_id}`` -- the run's RunState plus the computed
  ``stale`` flag and the run's ``metadata`` (provenance and replay reference).
  ``stale`` is True for a nonterminal run whose heartbeat (``updated_at``) is
  older than :data:`STALE_AFTER_SECONDS` wall-clock seconds: its producer stopped
  writing without a final record, e.g. it crashed. (A run still launching SC2 has
  not stepped yet, so its ``starting`` state also reads as stale until it does.)
* ``GET /api/jev/runs/{run_id}/policy`` -- the archived policy document plus its
  ``policy_hash`` (recomputed from the archive and checked against the run's).

Dashboard-first launch (plan D7, :mod:`jev.launch`), separate from run evidence and
served from the run root's sibling ``launches`` folder (``launch_root_for``):

* ``GET /api/jev/launches/{session_id}`` -- the launch session (``session.json``).
* ``POST /api/jev/launches/{session_id}/ready`` -- body ``{run_id, policy_hash}``: the
  dashboard page acknowledges it rendered the session's exact starting run and that
  run's archived policy; answers ``{schema_version, ready: true, session_id, run_id}``
  and writes the session's ``ready.json``. A readiness receipt only: it launches
  nothing, writes nothing else and controls no game. Only a loopback client on a
  loopback Host, from an allowed dashboard Origin (``launch_origins``; by default
  ``http://localhost:3000`` and ``http://127.0.0.1:3000``, which the Vite proxy
  forwards unchanged) is accepted: a missing or other Origin is refused. CORS is not
  broadened.

Launch errors use the same envelope with their own codes (never a run error code):
403 ``launch_forbidden``, 422 ``invalid_launch_request``, 404 ``launch_not_found``,
409 ``launch_not_ready``, 503 ``corrupt_launch``.

A run's detail reads its metadata and whole state (at most 1 KiB + 8 MiB); its
policy, the metadata and the archive (at most 1 KiB + 1 MiB).

Errors are ``{schema_version, error: {code, message}}``: 422 ``invalid_run_id``
unless the path ID is a lowercase UUID4 hex string, 404 ``run_not_found`` unless
it names a run directory directly inside the root (a link that resolves anywhere
else, a differently-spelled name, or a directory without metadata is not one),
503 ``corrupt_run`` for a malformed stored record. Messages are fixed text: they
never echo the request or file content. Nothing is written, and no JSONL trace is
ever served. Every read goes through :mod:`jev.telemetry`'s validation boundary.
The one write is the launch readiness receipt above.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Final

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from jev.contracts import (
    SCHEMA_VERSION,
    TERMINAL_RUN_STATUSES,
    ErrorCode,
    JevError,
    JsonValue,
    RunMetadata,
    RunState,
    is_valid_run_id,
    safe_repr,
)
from jev.launch import (
    DEFAULT_LAUNCH_ORIGINS,
    MAX_READY_BODY_BYTES,
    LaunchError,
    accept_ready,
    check_launch_client,
    launch_root_for,
    read_session,
    resolve_child_dir,
    resolve_root,
)
from jev.telemetry import (
    MAX_METADATA_BYTES,
    STATE_SUMMARY_BYTES,
    CorruptRun,
    read_policy_archive,
    read_run_metadata,
    read_run_state,
    read_run_summary,
    timestamp_seconds,
)

__all__ = [
    "API_PREFIX",
    "MAX_LIST_READ_BYTES",
    "MAX_SCANNED_ENTRIES",
    "RUN_LIST_LIMIT",
    "STALE_AFTER_SECONDS",
    "create_router",
]

#: Every Jev route lives under this prefix.
API_PREFIX: Final = "/api/jev"
#: The run list shows at most this many runs, newest first.
RUN_LIST_LIMIT: Final = 50
#: A nonterminal run whose heartbeat is older than this (wall-clock seconds) is stale.
STALE_AFTER_SECONDS: Final = 5.0
#: Run-root entries examined per listing (bounds the work of one request).
MAX_SCANNED_ENTRIES: Final = 4096
#: The most bytes one run listing reads: every scanned run's metadata, plus the
#: state summary prefix of each listed run.
MAX_LIST_READ_BYTES: Final = (
    MAX_SCANNED_ENTRIES * MAX_METADATA_BYTES + RUN_LIST_LIMIT * STATE_SUMMARY_BYTES
)

_INVALID_RUN_ID: Final = "the run id must be a lowercase UUID4 hex string"
_RUN_NOT_FOUND: Final = "no run with this id exists"
_ROOT_UNREADABLE: Final = "the Jev run root cannot be read"


class _Rejected(Exception):
    """A request the router answers with an error document."""

    def __init__(self, status: int, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.error = JevError(code, message)

    def response(self) -> JSONResponse:
        content = {"schema_version": SCHEMA_VERSION, "error": self.error.to_dict()}
        return JSONResponse(status_code=self.status, content=content)


def _corrupt(exc: CorruptRun) -> _Rejected:
    return _Rejected(503, "corrupt_run", exc.message)


def _resolved_root(run_root: Path) -> Path | None:
    return resolve_root(run_root)  # missing, unreadable, a link loop or a file: None


def _run_dir(root: Path | None, run_id: str) -> Path:
    """The run directory for a path ID: validated, then resolved strictly inside ``root``."""
    if not is_valid_run_id(run_id):
        raise _Rejected(422, "invalid_run_id", _INVALID_RUN_ID)
    # A link resolving elsewhere, or a name the filesystem matched case-insensitively,
    # is not this run's directory (the same check guards launch sessions).
    run_dir = resolve_child_dir(root, run_id)
    if run_dir is None:
        raise _Rejected(404, "run_not_found", _RUN_NOT_FOUND)
    return run_dir


def _metadata(run_dir: Path) -> RunMetadata:
    metadata = read_run_metadata(run_dir)
    if metadata is None:  # the directory exists, but the run never finished starting
        raise _Rejected(404, "run_not_found", _RUN_NOT_FOUND)
    return metadata


def _is_stale(state: RunState, now: float) -> bool:
    if state.status in TERMINAL_RUN_STATUSES:
        return False
    return now - timestamp_seconds(state.updated_at) > STALE_AFTER_SECONDS


def _list_runs(run_root: Path) -> dict[str, JsonValue]:
    root = _resolved_root(run_root)
    found: list[tuple[float, str, Path, RunMetadata]] = []
    omitted = 0
    overflow = False
    if root is not None:
        try:
            entries = list(itertools.islice(root.iterdir(), MAX_SCANNED_ENTRIES + 1))
        except OSError:
            raise _Rejected(503, "corrupt_run", _ROOT_UNREADABLE) from None
        overflow = len(entries) > MAX_SCANNED_ENTRIES
        for entry in entries[:MAX_SCANNED_ENTRIES]:
            if not is_valid_run_id(entry.name):
                continue  # not a run directory (e.g. a writer's temporary file)
            try:
                run_dir = _run_dir(root, entry.name)
                metadata = _metadata(run_dir)
            except _Rejected:
                continue  # an escaping link, a file, or a run still starting
            except CorruptRun:
                omitted += 1
                continue
            created = timestamp_seconds(metadata.created_at)
            found.append((created, metadata.run_id, run_dir, metadata))
    found.sort(key=lambda item: (item[0], item[1]), reverse=True)
    runs: list[JsonValue] = []
    for _, _, run_dir, metadata in found[:RUN_LIST_LIMIT]:
        try:
            runs.append(read_run_summary(run_dir, metadata).to_dict())
        except CorruptRun:
            omitted += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "runs": runs,
        "truncated": overflow or len(found) > RUN_LIST_LIMIT,
        "omitted": omitted,
    }


def _launch_rejected(exc: LaunchError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.document())


async def _bounded_body(request: Request) -> bytes:
    """The request body, refused once it grows past :data:`MAX_READY_BODY_BYTES`."""
    received = bytearray()
    async for chunk in request.stream():
        received.extend(chunk)
        if len(received) > MAX_READY_BODY_BYTES:
            raise LaunchError("invalid_launch_request", "the request body is too large")
    return bytes(received)


def create_router(
    run_root: Path,
    *,
    wall_time: Callable[[], float] = time.time,
    launch_origins: Collection[str] = DEFAULT_LAUNCH_ORIGINS,
) -> APIRouter:
    """The ``/api/jev`` router over ``run_root`` (resolved at each request).

    ``run_root`` must be absolute (never relative to a working directory);
    ``wall_time`` (POSIX seconds) is the clock ``stale`` is computed against;
    ``launch_origins`` are the browser origins allowed to acknowledge launch
    readiness (default: the dashboard on port 3000). Launch sessions are read from
    the run root's sibling ``launches`` folder. Raises :class:`ValueError` for a
    relative root.
    """
    if not isinstance(run_root, Path) or not run_root.is_absolute():
        raise ValueError(f"run_root must be an absolute Path, got {safe_repr(str(run_root))}")
    origins = frozenset(launch_origins)
    launch_root = launch_root_for(run_root)
    router = APIRouter(prefix=API_PREFIX)

    @router.get("/runs", response_model=None)
    def list_runs() -> dict[str, JsonValue] | JSONResponse:
        try:
            return _list_runs(run_root)
        except _Rejected as rejected:
            return rejected.response()

    @router.get("/runs/{run_id}", response_model=None)
    def get_run(run_id: str) -> dict[str, JsonValue] | JSONResponse:
        try:
            run_dir = _run_dir(_resolved_root(run_root), run_id)
            metadata = _metadata(run_dir)
            state = read_run_state(run_dir, metadata)
        except _Rejected as rejected:
            return rejected.response()
        except CorruptRun as exc:
            return _corrupt(exc).response()
        document = state.to_dict()
        document["stale"] = _is_stale(state, wall_time())
        document["metadata"] = metadata.to_dict()
        return document

    @router.get("/runs/{run_id}/policy", response_model=None)
    def get_policy(run_id: str) -> dict[str, JsonValue] | JSONResponse:
        try:
            run_dir = _run_dir(_resolved_root(run_root), run_id)
            metadata = _metadata(run_dir)
            policy = read_policy_archive(run_dir, metadata)
        except _Rejected as rejected:
            return rejected.response()
        except CorruptRun as exc:
            return _corrupt(exc).response()
        document = policy.to_document()
        document["policy_hash"] = metadata.policy_hash
        return document

    @router.get("/launches/{session_id}", response_model=None)
    def get_launch(session_id: str) -> dict[str, JsonValue] | JSONResponse:
        try:
            return read_session(launch_root, session_id).to_dict()
        except LaunchError as exc:
            return _launch_rejected(exc)

    @router.post("/launches/{session_id}/ready", response_model=None)
    async def acknowledge_launch(
        session_id: str, request: Request
    ) -> dict[str, JsonValue] | JSONResponse:
        try:
            check_launch_client(
                None if request.client is None else request.client.host,
                request.headers.get("host"),
                request.headers.get("origin"),
                origins,
            )
            body = await _bounded_body(request)
            # Record reads (with their sharing-violation retries), the archive re-hash
            # and the atomic write block: they run in the threadpool, as the GET routes
            # do, never on the event loop that serves every other request.
            return await run_in_threadpool(
                accept_ready, launch_root, run_root, session_id, body, wall_time=wall_time
            )
        except LaunchError as exc:
            return _launch_rejected(exc)

    return router
