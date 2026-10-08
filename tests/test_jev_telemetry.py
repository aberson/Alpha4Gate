"""Jev run evidence: the run-directory writer, reader and CLI wiring (Step 204, plan D5).

The writer tests drive :class:`jev.telemetry.RunRecorder` with the packaged v1
policy. Where a test needs a runtime state, it uses a real
:class:`jev.runtime.JevRuntime`; where it needs states no runtime builds on demand
(more than 128 active tasks, 64-bit unit tags), a small
:class:`~jev.telemetry.RunStateSource` stand-in. Disk failures are injected
through :class:`~jev.telemetry.EvidenceFiles`; clocks are injected, nothing
sleeps. Reader tests corrupt real writer output one defect at a time. The
end-to-end path (runner -> bot -> telemetry -> router) is in ``test_jev_api.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from bots.jev.v1 import load_policy

import jev.api as api_module
import jev.bot as bot_module
import jev.contracts as contracts
import jev.telemetry as telemetry
from jev import runner
from jev.api import RUN_LIST_LIMIT, STALE_AFTER_SECONDS
from jev.bot import JevController
from jev.contracts import (
    MAX_ACTIVE_TASKS,
    RECENT_EVENT_LIMIT,
    CommandSpec,
    Event,
    JevError,
    RunMetadata,
    RunResult,
    RunState,
    RunStatus,
    Task,
)
from jev.runner import EXIT_OK, EXIT_USAGE, MatchOptions
from jev.runtime import JevRuntime
from jev.telemetry import (
    MAX_METADATA_BYTES,
    MAX_STATE_BYTES,
    METADATA_FILE,
    POLICY_ARCHIVE_FILE,
    REPLAY_FILE,
    SHARING_RETRY_DELAYS,
    STATE_FILE,
    STATE_SUMMARY_BYTES,
    CorruptRun,
    EvidenceFiles,
    PersistenceFailed,
    RunRecorder,
    TelemetryLimits,
    read_policy_archive,
    read_run_metadata,
    read_run_state,
    read_source_commit,
    repository_root,
    trace_segment_name,
)

BUNDLE = load_policy()
ROOT_NODE = BUNDLE.policy.roots[0]
#: A JavaScript-unsafe unit tag (> 2**53).
BIG_TAG = 2**60 + 7
#: Injected into corrupted records; no error message may echo it.
MARKER = "<script>alert(1)</script>"
SHA = "0123456789abcdef0123456789abcdef01234567"


def _metadata(**changes: Any) -> RunMetadata:
    metadata = RunMetadata(
        run_id=uuid.uuid4().hex,
        created_at="2026-10-08T12:00:00.000+00:00",
        family=BUNDLE.policy.family,
        version=BUNDLE.policy.version,
        policy_hash=BUNDLE.policy_hash,
        source_commit=SHA,
        map="Simple64",
        opponent_race="Terran",
        difficulty=1,
        seed=1,
        max_game_seconds=900.0,
        max_wall_seconds=1800.0,
    )
    return replace(metadata, **changes)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _recorder(root: Path, metadata: RunMetadata | None = None, **kwargs: Any) -> RunRecorder:
    chosen = _metadata() if metadata is None else metadata
    return RunRecorder(root, chosen, roots=BUNDLE.policy.roots, **kwargs)


def _started(root: Path, **kwargs: Any) -> RunRecorder:
    recorder = _recorder(root, **kwargs)
    recorder.start(BUNDLE.policy_bytes)
    return recorder


def _runtime(recorder: RunRecorder) -> JevRuntime:
    return JevRuntime(BUNDLE.policy, run_id=recorder.run_dir.name)


def _event(
    recorder: RunRecorder,
    sequence: int,
    *,
    kind: str = "diagnostic",
    node_id: str = "@runtime",
    status: str = "warning",
    game_seconds: float = 0.0,
    facts: dict[str, Any] | None = None,
    action: dict[str, Any] | None = None,
) -> Event:
    return Event(
        run_id=recorder.run_dir.name,
        sequence=sequence,
        game_loop=round(game_seconds * 22.4),
        game_seconds=game_seconds,
        node_id=node_id,
        task_id=None,
        kind=kind,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        reason="scenario",
        facts={} if facts is None else facts,
        action=action,
    )


def _diagnostics(recorder: RunRecorder, sequences: range) -> list[Event]:
    return [_event(recorder, sequence) for sequence in sequences]


class _Source:
    """A RunStateSource with fixed tasks: states a runtime does not build on demand."""

    def __init__(self, recorder: RunRecorder, tasks: tuple[Task, ...] = ()) -> None:
        self.run_id = recorder.run_dir.name
        self.tasks = tasks

    def run_state(
        self,
        *,
        status: RunStatus,
        updated_at: str,
        recent_events: tuple[Event, ...] = (),
        result: RunResult | None = None,
        error: JevError | None = None,
    ) -> RunState:
        return RunState(
            run_id=self.run_id,
            family=BUNDLE.policy.family,
            version=BUNDLE.policy.version,
            policy_hash=BUNDLE.policy_hash,
            status=status,
            updated_at=updated_at,
            game_seconds=12.5,
            last_sequence=recent_events[-1].sequence if recent_events else 0,
            active_nodes=(ROOT_NODE,),
            waiting_nodes=(),
            tasks=self.tasks,
            recent_events=recent_events,
            result=result,
            error=error,
        )


def _task(index: int, *, actor_tag: int | None = 2000, target: Any = None) -> Task:
    return Task(
        id=f"run:{index}",
        node_id="economy.gather",
        intent_key=f"economy.gather:{index}",
        actor_tag=actor_tag,
        target=target,
        status="running",
        created_game_seconds=1.0,
        deadline_game_seconds=None,
        attempts=1,
        last_progress_game_seconds=2.0,
        reason="awaiting observation",
    )


def _state(recorder: RunRecorder) -> RunState:
    metadata = read_run_metadata(recorder.run_dir)
    assert metadata is not None
    return read_run_state(recorder.run_dir, metadata)


def _trace(run_dir: Path, segment: int = 1) -> list[dict[str, Any]]:
    lines = (run_dir / trace_segment_name(segment)).read_bytes().split(b"\n")
    assert lines[-1] == b""  # every complete line ends with a newline
    return [json.loads(line) for line in lines[:-1]]


def _sequences(run_dir: Path, segment: int = 1) -> list[int]:
    return [event["sequence"] for event in _trace(run_dir, segment)]


# ---------------------------------------------------------------------------
# Start, cadence and state bounds
# ---------------------------------------------------------------------------


def test_the_production_bounds_are_the_plan_values() -> None:
    """Plan D5 and section 5 as literal magnitudes: a drifted default fails here."""
    limits = TelemetryLimits()
    assert (limits.max_segment_bytes, limits.max_segments) == (10 * 1024 * 1024, 5)
    assert limits.min_state_interval_seconds == 0.5  # at most two state writes a second
    assert (RECENT_EVENT_LIMIT, MAX_ACTIVE_TASKS) == (200, 128)
    assert (RUN_LIST_LIMIT, STALE_AFTER_SECONDS) == (50, 5.0)


def test_start_archives_the_policy_bytes_and_publishes_a_starting_run(tmp_path: Path) -> None:
    metadata = _metadata()
    recorder = _recorder(tmp_path, metadata)
    recorder.start(BUNDLE.policy_bytes)
    run_dir = tmp_path / metadata.run_id
    assert recorder.run_dir == run_dir
    assert (run_dir / POLICY_ARCHIVE_FILE).read_bytes() == BUNDLE.policy_bytes  # byte-exact
    assert read_run_metadata(run_dir) == metadata
    state = read_run_state(run_dir, metadata)
    assert (state.status, state.policy_hash, state.tasks) == ("starting", BUNDLE.policy_hash, ())
    assert read_policy_archive(run_dir, metadata).to_document() == BUNDLE.policy.to_document()


def test_an_existing_run_directory_is_never_reused(tmp_path: Path) -> None:
    metadata = _metadata()
    (tmp_path / metadata.run_id).mkdir()
    (tmp_path / metadata.run_id / "kept.txt").write_text("earlier evidence", encoding="utf-8")
    with pytest.raises(PersistenceFailed) as excinfo:
        _recorder(tmp_path, metadata).start(BUNDLE.policy_bytes)
    assert excinfo.value.code == "persistence_failed"
    assert sorted(p.name for p in (tmp_path / metadata.run_id).iterdir()) == ["kept.txt"]


class _RecordingFiles(EvidenceFiles):
    def __init__(self, clock: _Clock) -> None:
        super().__init__()
        self.clock = clock
        self.state_writes: list[float] = []

    def write(self, path: Path, data: bytes) -> None:
        super().write(path, data)
        if path.name == STATE_FILE:
            self.state_writes.append(self.clock.now)


def test_live_state_is_written_at_most_twice_a_second_plus_the_terminal_state(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    files = _RecordingFiles(clock)
    recorder = _started(tmp_path, files=files, clock=clock)
    runtime = _runtime(recorder)
    for now in (0.1, 0.2, 0.49, 0.5, 0.7, 0.99, 1.0):
        clock.now = now
        recorder.update(runtime, ())
    clock.now = 1.05
    recorder.finish(runtime, status="stopped")
    assert files.state_writes == [0.0, 0.5, 1.0, 1.05]  # start, two live, terminal
    assert _state(recorder).status == "stopped"


def test_state_keeps_the_newest_200_traced_events(tmp_path: Path) -> None:
    recorder = _started(tmp_path)
    runtime = _runtime(recorder)
    recorder.update(runtime, _diagnostics(recorder, range(1, 251)))
    recorder.finish(runtime, status="stopped")
    shown = [event.sequence for event in _state(recorder).recent_events]
    assert shown == list(range(251 - RECENT_EVENT_LIMIT, 251))
    assert _sequences(recorder.run_dir) == list(range(1, 251))  # the trace keeps them all


def test_state_lists_at_most_128_active_tasks_and_counts_the_rest(tmp_path: Path) -> None:
    recorder = _started(tmp_path)
    tasks = tuple(_task(index) for index in range(MAX_ACTIVE_TASKS + 2))
    recorder.finish(_Source(recorder, tasks), status="stopped")
    state = _state(recorder)
    assert state.tasks == tasks[:MAX_ACTIVE_TASKS]
    assert state.tasks_omitted == 2


def _rich_run(tmp_path: Path) -> RunRecorder:
    """A finished run whose state carries every field shape, with 64-bit tags."""
    recorder = _started(tmp_path)
    command = CommandSpec("army.attack.go", "run:3", "ATTACK", (BIG_TAG,), BIG_TAG - 1)
    events = [
        _event(recorder, 1, kind="node", node_id=ROOT_NODE, status="running", facts={"child": "x"}),
        _event(
            recorder,
            2,
            kind="command",
            node_id="army.attack.go",
            status="issued",
            action=command.to_dict(),
        ),
    ]
    tasks = (
        _task(1, actor_tag=BIG_TAG, target=BIG_TAG - 1),
        _task(2, actor_tag=None, target=(40.5, 41.0)),
    )
    recorder.update(_Source(recorder, tasks), events)
    error = JevError("game_timeout", "game-time limit of 900 seconds reached")
    recorder.finish(_Source(recorder, tasks), status="finished", result="timeout", error=error)
    return recorder


def _integers(value: object) -> list[int]:
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, dict):
        return [i for item in value.values() for i in _integers(item)]
    if isinstance(value, list):
        return [i for item in value for i in _integers(item)]
    return []


def test_unit_tags_beyond_2_53_are_decimal_strings_in_every_record(tmp_path: Path) -> None:
    recorder = _rich_run(tmp_path)
    documents = [
        json.loads((recorder.run_dir / STATE_FILE).read_bytes()),
        *_trace(recorder.run_dir),
    ]
    assert all(abs(i) <= 2**53 for document in documents for i in _integers(document))
    raw_state = (recorder.run_dir / STATE_FILE).read_text(encoding="ascii")
    raw_trace = (recorder.run_dir / trace_segment_name(1)).read_text(encoding="ascii")
    assert f'"{BIG_TAG}"' in raw_state and f'"{BIG_TAG}"' in raw_trace
    assert _state(recorder).tasks[0].actor_tag == BIG_TAG  # an int again once read


def test_the_reader_rebuilds_exactly_what_the_writer_wrote(tmp_path: Path) -> None:
    recorder = _rich_run(tmp_path)
    written = json.loads((recorder.run_dir / STATE_FILE).read_bytes())
    assert _state(recorder).to_dict() == written


# ---------------------------------------------------------------------------
# Trace: selection, rotation, torn tails
# ---------------------------------------------------------------------------


def test_trace_keeps_changes_actions_and_one_root_summary_per_game_second(
    tmp_path: Path,
) -> None:
    recorder = _started(tmp_path)
    runtime = _runtime(recorder)

    def node(sequence: int, node_id: str, status: str, seconds: float, child: str = "") -> Event:
        facts = {"child": child} if child else {}
        return _event(
            recorder,
            sequence,
            kind="node",
            node_id=node_id,
            status=status,
            game_seconds=seconds,
            facts=facts,
        )

    ticks = [
        [
            node(1, ROOT_NODE, "running", 0.0, "a"),
            node(2, "a", "running", 0.0),
            _event(recorder, 3, kind="command", status="issued"),
        ],
        [
            node(4, ROOT_NODE, "running", 0.25, "a"),
            node(5, "a", "running", 0.25),
            _event(recorder, 6, kind="task", status="running"),
        ],
        [node(7, ROOT_NODE, "running", 0.5, "a"), node(8, "a", "success", 0.5)],  # status
        [node(9, ROOT_NODE, "running", 0.75, "b")],  # branch
        [node(10, ROOT_NODE, "running", 1.0, "b"), node(11, "a", "success", 1.0)],  # summary
    ]
    for events in ticks:
        recorder.update(runtime, events)
    assert _sequences(recorder.run_dir) == [1, 2, 3, 6, 8, 9, 10]


def _segments(run_dir: Path) -> list[int]:
    names = [p.name for p in run_dir.iterdir() if p.name.startswith("events.")]
    return sorted(int(name.split(".")[1]) for name in names)


def test_trace_rotates_before_the_size_limit_and_keeps_the_newest_five_segments(
    tmp_path: Path,
) -> None:
    limit = 600  # two or three ~220-byte events per segment
    recorder = _started(tmp_path, limits=TelemetryLimits(max_segment_bytes=limit, max_segments=5))
    runtime = _runtime(recorder)
    for first in range(1, 31, 3):
        recorder.update(runtime, _diagnostics(recorder, range(first, first + 3)))
    recorder.finish(runtime, status="stopped")
    kept = _segments(recorder.run_dir)
    newest = kept[-1]
    assert kept == list(range(newest - 4, newest + 1))
    assert all((recorder.run_dir / trace_segment_name(n)).stat().st_size <= limit for n in kept)
    retained = [seq for n in kept for seq in _sequences(recorder.run_dir, n)]
    assert retained == list(range(31 - len(retained), 31))  # the newest, in order
    trace = _state(recorder).trace
    assert (trace.segment, trace.rotated_segments, trace.dropped_segments) == (
        newest,
        newest - 1,
        newest - 5,
    )
    assert (trace.events, trace.dropped_events, trace.complete) == (30, 30 - len(retained), False)


def _tear(run_dir: Path, segment: int = 1) -> None:
    """What a crash in the middle of an append leaves: a line without its newline."""
    with (run_dir / trace_segment_name(segment)).open("ab") as handle:
        handle.write(b'{"schema_version":1,"run_id":"')


def test_a_torn_trace_tail_is_cut_before_the_next_append(tmp_path: Path) -> None:
    recorder = _started(tmp_path)
    runtime = _runtime(recorder)
    recorder.update(runtime, _diagnostics(recorder, range(1, 4)))
    _tear(recorder.run_dir)
    recorder.update(runtime, _diagnostics(recorder, range(4, 6)))
    recorder.finish(runtime, status="stopped")
    assert _sequences(recorder.run_dir) == [1, 2, 3, 4, 5]  # every line parses
    trace = _state(recorder).trace  # the state was never touched by the torn tail
    assert (trace.events, trace.dropped_events, trace.complete) == (5, 0, True)


class _TornAppend(EvidenceFiles):
    """Appends that can be made to fail halfway, as a full disk would."""

    def __init__(self) -> None:
        super().__init__()
        self.tear_next = False

    def append(self, path: Path, data: bytes) -> None:
        if self.tear_next:
            self.tear_next = False
            super().append(path, data[: len(data) // 2])
            raise OSError(28, "No space left on device")
        super().append(path, data)


def test_a_failed_append_drops_only_its_own_events(tmp_path: Path) -> None:
    files = _TornAppend()
    recorder = _started(tmp_path, files=files)
    runtime = _runtime(recorder)
    recorder.update(runtime, _diagnostics(recorder, range(1, 3)))
    files.tear_next = True
    recorder.update(runtime, _diagnostics(recorder, range(3, 5)))
    recorder.update(runtime, _diagnostics(recorder, range(5, 6)))
    recorder.finish(runtime, status="stopped")
    assert _sequences(recorder.run_dir) == [1, 2, 5]
    trace = _state(recorder).trace
    assert (trace.events, trace.dropped_events, trace.complete) == (3, 2, False)


class _Uncuttable(EvidenceFiles):
    def truncate(self, path: Path, size: int) -> None:
        raise PermissionError(13, "Access is denied")


def test_a_tail_that_cannot_be_cut_is_left_behind_for_a_fresh_segment(tmp_path: Path) -> None:
    recorder = _started(tmp_path, files=_Uncuttable())
    runtime = _runtime(recorder)
    recorder.update(runtime, _diagnostics(recorder, range(1, 3)))
    _tear(recorder.run_dir)
    recorder.update(runtime, _diagnostics(recorder, range(3, 4)))
    recorder.finish(runtime, status="stopped")
    assert _sequences(recorder.run_dir, 2) == [3]
    assert (recorder.run_dir / trace_segment_name(1)).read_bytes().endswith(b'"run_id":"')
    assert _state(recorder).trace.segment == 2


class _TornAndUncuttable(_TornAppend, _Uncuttable):
    """Appends that can fail halfway, onto a segment that cannot be cut back."""


def test_a_segment_a_failed_append_left_behind_is_still_retired(tmp_path: Path) -> None:
    files = _TornAndUncuttable()
    limits = TelemetryLimits(max_segment_bytes=600, max_segments=2)
    recorder = _started(tmp_path, files=files, limits=limits)
    runtime = _runtime(recorder)
    files.tear_next = True  # segment 1 gets only half a batch, and cannot be cut back
    recorder.update(runtime, _diagnostics(recorder, range(1, 3)))
    for first in range(3, 15, 2):
        recorder.update(runtime, _diagnostics(recorder, range(first, first + 2)))
    recorder.finish(runtime, status="stopped")
    kept = _segments(recorder.run_dir)
    assert kept == [kept[-1] - 1, kept[-1]] and kept[0] > 2  # segment 1 is gone too


# ---------------------------------------------------------------------------
# Mandatory records, retries and the replay reference
# ---------------------------------------------------------------------------


class _Refusing(EvidenceFiles):
    """Refuses writes of one record (as a full disk would) after ``allowed`` succeed."""

    def __init__(self, refused: str, *, allowed: int = 0) -> None:
        super().__init__()
        self.refused = refused
        self.allowed = allowed

    def write(self, path: Path, data: bytes) -> None:
        if path.name == self.refused:
            if self.allowed == 0:
                raise OSError(28, "No space left on device")
            self.allowed -= 1
        super().write(path, data)


@pytest.mark.parametrize("refused", [POLICY_ARCHIVE_FILE, STATE_FILE, METADATA_FILE])
def test_start_fails_when_any_mandatory_record_cannot_be_written(
    tmp_path: Path, refused: str
) -> None:
    recorder = _recorder(tmp_path, files=_Refusing(refused))
    with pytest.raises(PersistenceFailed) as excinfo:
        recorder.start(BUNDLE.policy_bytes)
    assert refused in excinfo.value.message and "No space left" in excinfo.value.message
    assert str(tmp_path) not in excinfo.value.message  # no local paths: the API serves it
    assert read_run_metadata(recorder.run_dir) is None  # never listed as a run


@pytest.mark.parametrize(("refusals", "succeeds"), [(2, True), (99, False)])
def test_sharing_violations_are_retried_with_backoff_then_reported(
    tmp_path: Path, refusals: int, succeeds: bool
) -> None:
    attempts: list[Path] = []
    sleeps: list[float] = []

    def replace_file(source: Path, target: Path) -> None:
        attempts.append(target)
        if len(attempts) <= refusals:
            raise PermissionError(32, "The process cannot access the file")
        os.replace(source, target)

    files = EvidenceFiles(replace_file=replace_file, sleep=sleeps.append)
    target = tmp_path / STATE_FILE
    if succeeds:
        files.write(target, b"{}")
        assert target.read_bytes() == b"{}"
        assert sleeps == list(SHARING_RETRY_DELAYS[:refusals])
    else:
        with pytest.raises(PermissionError):
            files.write(target, b"{}")
        assert not target.exists()
        assert sleeps == list(SHARING_RETRY_DELAYS)
        assert len(attempts) == len(SHARING_RETRY_DELAYS) + 1
    assert [p.name for p in tmp_path.iterdir()] == ([STATE_FILE] if succeeds else [])


def test_a_written_terminal_state_is_final(tmp_path: Path) -> None:
    recorder = _started(tmp_path)
    runtime = _runtime(recorder)
    recorder.finish(runtime, status="finished", result="win")
    with pytest.raises(RuntimeError, match="after finish"):
        recorder.finish(runtime, status="stopped")
    with pytest.raises(RuntimeError, match="after finish"):
        recorder.update(runtime, ())
    assert (_state(recorder).status, recorder.live) == ("finished", False)


@pytest.mark.parametrize("saved", [True, False], ids=["saved", "not-saved"])
def test_the_replay_is_referenced_relative_to_the_run_only_once_saved(
    tmp_path: Path, saved: bool
) -> None:
    recorder = _started(tmp_path)
    if saved:
        recorder.replay_file.write_bytes(b"MPQ replay")  # what burnysc2 saves
    recorder.finish(None, status="finished", result="win")
    metadata = read_run_metadata(recorder.run_dir)
    assert metadata is not None
    assert metadata.replay_path == (REPLAY_FILE if saved else None)


def test_an_unwritable_replay_reference_fails_the_run_but_keeps_its_result(
    tmp_path: Path,
) -> None:
    recorder = _started(tmp_path, files=_Refusing(METADATA_FILE, allowed=1))
    recorder.replay_file.write_bytes(b"MPQ replay")
    earlier = JevError("game_timeout", "game-time limit of 900 seconds reached")
    with pytest.raises(PersistenceFailed) as excinfo:
        recorder.finish(_runtime(recorder), status="finished", result="timeout", error=earlier)
    state = _state(recorder)
    assert (state.status, state.result) == ("failed", "timeout")  # the true outcome stays
    assert state.error is not None and state.error.code == "persistence_failed"
    assert "game_timeout" in state.error.message and "game_timeout" in excinfo.value.message
    metadata = read_run_metadata(recorder.run_dir)
    assert metadata is not None and metadata.replay_path is None  # the rewrite never landed


def test_the_largest_real_run_records_fit_their_read_caps(tmp_path: Path) -> None:
    largest = _metadata(
        version=1_000_000,
        source_commit="f" * 64,
        map="M" * 64,
        opponent_race="Protoss",
        difficulty=10,
        seed=2**32 - 1,
        max_game_seconds=86_400.0,
        max_wall_seconds=86_400.0,
    )
    recorder = _recorder(tmp_path, largest)
    recorder.start(BUNDLE.policy_bytes)
    recorder.replay_file.write_bytes(b"MPQ replay")
    recorder.finish(None, status="finished", result="win")
    assert read_run_metadata(recorder.run_dir) == replace(largest, replay_path=REPLAY_FILE)
    state = (recorder.run_dir / STATE_FILE).read_bytes()
    assert state.index(b'"game_seconds"') < STATE_SUMMARY_BYTES  # the summary leads it
    assert (recorder.run_dir / METADATA_FILE).stat().st_size <= MAX_METADATA_BYTES


@pytest.mark.parametrize(
    ("changes", "refused"),
    [
        ({"map": "M" * 2000}, METADATA_FILE),
        ({"family": "x" * 600}, STATE_FILE),
        ({"family": "jev\ud800"}, STATE_FILE),
    ],
    ids=["metadata-size", "state-summary-size", "lone-surrogate"],
)
def test_the_writer_refuses_a_record_its_reader_would_reject(
    tmp_path: Path, changes: dict[str, Any], refused: str
) -> None:
    recorder = _recorder(tmp_path, _metadata(**changes))
    with pytest.raises(PersistenceFailed, match=refused):
        recorder.start(BUNDLE.policy_bytes)
    assert read_run_metadata(recorder.run_dir) is None  # never listed as a run


# ---------------------------------------------------------------------------
# Reader: the one validation boundary
# ---------------------------------------------------------------------------


def _edit(name: str, change: Callable[[dict[str, Any]], None]) -> Callable[[Path], None]:
    def apply(run_dir: Path) -> None:
        path = run_dir / name
        document = json.loads(path.read_bytes())
        change(document)
        path.write_text(json.dumps(document), encoding="utf-8")

    return apply


def _write(name: str, data: bytes) -> Callable[[Path], None]:
    def apply(run_dir: Path) -> None:
        (run_dir / name).write_bytes(data)

    return apply


def _remove(name: str) -> Callable[[Path], None]:
    def apply(run_dir: Path) -> None:
        (run_dir / name).unlink()

    return apply


def _set(key: str, value: object) -> Callable[[dict[str, Any]], None]:
    return lambda document: document.__setitem__(key, value)


def _set_first_task_actor(document: dict[str, Any]) -> None:
    document["tasks"][0]["actor_tag"] = f"0x{MARKER}"


def _relabel(document: dict[str, Any]) -> None:
    document["nodes"][0]["label"] = MARKER


_CORRUPTIONS: dict[str, tuple[str, Callable[[Path], None]]] = {
    "state-not-json": (STATE_FILE, _write(STATE_FILE, b'{"status": "' + MARKER.encode())),
    "state-schema-version": (STATE_FILE, _edit(STATE_FILE, _set("schema_version", 2))),
    "state-field-type": (STATE_FILE, _edit(STATE_FILE, _set("game_seconds", MARKER))),
    "state-unknown-status": (STATE_FILE, _edit(STATE_FILE, _set("status", MARKER))),
    "state-unexpected-field": (STATE_FILE, _edit(STATE_FILE, _set(MARKER, 1))),
    "state-missing-field": (STATE_FILE, _edit(STATE_FILE, lambda d: d.pop("trace"))),
    "state-tag-not-decimal": (STATE_FILE, _edit(STATE_FILE, _set_first_task_actor)),
    "state-other-run": (STATE_FILE, _edit(STATE_FILE, _set("run_id", uuid.uuid4().hex))),
    "state-oversized": (STATE_FILE, _write(STATE_FILE, b" " * (MAX_STATE_BYTES + 1))),
    "state-missing": (STATE_FILE, _remove(STATE_FILE)),
    "metadata-replay-elsewhere": (
        METADATA_FILE,
        _edit(METADATA_FILE, _set("replay_path", f"../{MARKER}")),
    ),
    "metadata-timestamp": (METADATA_FILE, _edit(METADATA_FILE, _set("created_at", MARKER))),
    "policy-edited": (POLICY_ARCHIVE_FILE, _edit(POLICY_ARCHIVE_FILE, _relabel)),
    "policy-not-json": (POLICY_ARCHIVE_FILE, _write(POLICY_ARCHIVE_FILE, MARKER.encode())),
}


@pytest.mark.parametrize("case", list(_CORRUPTIONS))
def test_a_malformed_record_is_corrupt_run_and_its_content_is_never_echoed(
    tmp_path: Path, case: str
) -> None:
    record, corrupt = _CORRUPTIONS[case]
    run_dir = _rich_run(tmp_path).run_dir
    corrupt(run_dir)
    with pytest.raises(CorruptRun) as excinfo:
        metadata = read_run_metadata(run_dir)
        assert metadata is not None
        read_run_state(run_dir, metadata)
        read_policy_archive(run_dir, metadata)
    assert excinfo.value.code == "corrupt_run"
    assert record in excinfo.value.message
    assert MARKER not in excinfo.value.message and "script" not in excinfo.value.message


def test_a_record_reached_through_a_link_is_never_read(tmp_path: Path) -> None:
    run_dir = _rich_run(tmp_path / "runs").run_dir
    outside = tmp_path / "outside.json"
    shutil.copyfile(run_dir / STATE_FILE, outside)  # a valid state, but not in the run
    (run_dir / STATE_FILE).unlink()
    try:
        os.symlink(outside, run_dir / STATE_FILE)
    except OSError:
        pytest.skip("creating file symlinks needs a privilege this host lacks")
    metadata = read_run_metadata(run_dir)
    assert metadata is not None
    with pytest.raises(CorruptRun, match="not a regular file in the run directory"):
        read_run_state(run_dir, metadata)


# ---------------------------------------------------------------------------
# Source commit: read from git's files, never by running git
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_source_commit_is_the_checked_out_commit_of_this_repository() -> None:
    git = shutil.which("git")
    assert git is not None
    expected = subprocess.run(
        [git, "rev-parse", "HEAD"],
        cwd=repository_root(),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert read_source_commit(repository_root()) == expected


def _git_files(repo: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="ascii")
    return repo


_LAYOUTS: dict[str, tuple[str, dict[str, str]]] = {
    "loose-ref": (
        "repo",
        {"repo/.git/HEAD": "ref: refs/heads/main\n", "repo/.git/refs/heads/main": f"{SHA}\n"},
    ),
    "packed-ref": (
        "repo",
        {
            "repo/.git/HEAD": "ref: refs/heads/main\n",
            "repo/.git/packed-refs": f"# pack-refs with: peeled\n{SHA} refs/heads/main\n^{SHA}\n",
        },
    ),
    "detached": ("repo", {"repo/.git/HEAD": f"{SHA}\n"}),
    "worktree": (
        "wt",
        {
            "wt/.git": "gitdir: ../main/.git/worktrees/wt\n",
            "main/.git/worktrees/wt/HEAD": "ref: refs/heads/feature\n",
            "main/.git/worktrees/wt/commondir": "../..\n",
            "main/.git/refs/heads/feature": f"{SHA}\n",
        },
    ),
}


@pytest.mark.parametrize("layout", list(_LAYOUTS))
def test_source_commit_follows_head_through_each_git_layout(tmp_path: Path, layout: str) -> None:
    checkout, files = _LAYOUTS[layout]
    _git_files(tmp_path, files)
    assert read_source_commit(tmp_path / checkout) == SHA


@pytest.mark.parametrize(
    "files",
    [
        {},
        {"repo/.git/HEAD": "ref: refs/heads/../../../outside\n", "outside": f"{SHA}\n"},
        {"repo/.git/HEAD": "ref: refs/heads/main\n", "repo/.git/refs/heads/main": "not-a-sha\n"},
        {"repo/.git/HEAD": "x" * 5000},
    ],
    ids=["no-repository", "ref-escapes", "ref-not-a-commit", "oversized-head"],
)
def test_source_commit_is_null_when_unknown_or_unsafe(
    tmp_path: Path, files: dict[str, str]
) -> None:
    (tmp_path / "repo").mkdir()
    _git_files(tmp_path, files)
    assert read_source_commit(tmp_path / "repo") is None


# ---------------------------------------------------------------------------
# CLI wiring and settings
# ---------------------------------------------------------------------------


class _NoSteps:
    """A launcher whose match ends with a win before any step (wiring tests only)."""

    def prepare(self, options: MatchOptions) -> object:
        return "setup"

    def play(self, controller: JevController, setup: object, options: MatchOptions) -> Any:
        return "win"


def _only_run(root: Path) -> Path:
    (run_dir,) = root.iterdir()
    return run_dir


def test_cli_records_the_match_under_the_run_root_override(tmp_path: Path) -> None:
    argv = ["--run-root", str(tmp_path)]
    code = runner.main(argv, load_policy=load_policy, prog="jev-test", launcher=_NoSteps())
    assert code == EXIT_OK
    run_dir = _only_run(tmp_path)
    metadata = read_run_metadata(run_dir)
    assert metadata is not None and metadata.run_id == run_dir.name
    state = read_run_state(run_dir, metadata)
    assert (state.status, state.result) == ("finished", "win")


def test_cli_without_a_run_root_records_under_the_default_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = tmp_path / "default"
    monkeypatch.setattr(runner, "default_run_root", lambda: default)
    code = runner.main([], load_policy=load_policy, prog="jev-test", launcher=_NoSteps())
    assert code == EXIT_OK
    assert read_run_metadata(_only_run(default)) is not None


@pytest.mark.parametrize(
    ("argv", "fragment"),
    [
        (["--run-root", "relative/runs"], "expected an absolute path"),
        (["--validate-policy", "--run-root", "{root}"], "cannot be used with --validate-policy"),
    ],
    ids=["relative", "with-validate-policy"],
)
def test_cli_run_root_misuse_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str], fragment: str
) -> None:
    filled = [arg.format(root=tmp_path) for arg in argv]
    with pytest.raises(SystemExit) as excinfo:
        runner.main(filled, load_policy=load_policy, prog="jev-test", launcher=_NoSteps())
    assert excinfo.value.code == EXIT_USAGE
    assert fragment in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "build",
    [
        lambda root: TelemetryLimits(max_segment_bytes=0),
        lambda root: TelemetryLimits(max_segments=True),
        lambda root: TelemetryLimits(min_state_interval_seconds=-1.0),
        lambda root: _recorder(Path("relative")),
        lambda root: _recorder(root, _metadata(run_id="F" * 32)),
    ],
    ids=["segment-bytes", "segments", "interval", "relative-root", "run-id"],
)
def test_invalid_recorder_settings_are_value_errors(
    tmp_path: Path, build: Callable[[Path], object]
) -> None:
    with pytest.raises(ValueError):
        build(tmp_path)


def test_a_trace_segment_number_below_one_is_a_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        telemetry.read_trace_segment(tmp_path, 0)


def test_shared_shapes_have_one_source_across_telemetry_api_bot_and_runner() -> None:
    assert api_module.TERMINAL_RUN_STATUSES is contracts.TERMINAL_RUN_STATUSES
    assert telemetry.ERROR_CODES is contracts.ERROR_CODES
    assert telemetry.TASK_STATUSES is contracts.TASK_STATUSES
    assert telemetry.RUN_STATUSES is contracts.RUN_STATUSES
    assert bot_module.PersistenceFailed is telemetry.PersistenceFailed
    assert runner.default_run_root is telemetry.default_run_root
    assert api_module.MAX_METADATA_BYTES is telemetry.MAX_METADATA_BYTES
    assert api_module.STATE_SUMMARY_BYTES is telemetry.STATE_SUMMARY_BYTES
    assert bot_module.exception_error is runner.exception_error
