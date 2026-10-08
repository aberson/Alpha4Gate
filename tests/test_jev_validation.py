"""Jev live-validation verifier, ``scripts/validate_jev.py`` (Step 206).

Every run here is real evidence written by the production writer
(:class:`jev.telemetry.RunRecorder`) fed by the production interpreter
(:class:`jev.runtime.JevRuntime` over contract Observations, or the whole runner
path through :func:`jev.runner.run_match` with a burnysc2-shaped stand-in game,
as in ``test_jev_api.py``). It is served by the actual read-only routes
(:func:`jev.api.create_router`) in a real uvicorn server on 127.0.0.1 with an
ephemeral port, and the verifier runs through its real entry point --
``main(argv)``, and as a subprocess -- over real HTTP. Nothing in the verifier's
disk or HTTP access is mocked: a failure case is a run the writer produced
deliberately wrong, a written run edited on disk, or -- for what the real API never
serves (another schema version, a hostile body) -- a real stdlib HTTP server
answering with the real API's documents, one field changed. Every failure code the
verifier knows has a producing case. Staleness comes from the writer's injected
wall clock; nothing sleeps.

The operator guide (``documentation/operator/jev-validation.md``) is checked
against the code: every command it gives for the verifier or the Jev CLI must
parse, and every failure code must be documented.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.server
import importlib.util
import json
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import uvicorn
from bots.jev.v1 import load_policy
from fastapi import FastAPI
from sc2.data import Result

import jev.api as api_module
import jev.contracts as contracts
import jev.runner as runner
import jev.telemetry as telemetry
from jev.api import create_router
from jev.bot import JevBot, JevController
from jev.contracts import (
    Entity,
    JevError,
    Observation,
    RunMetadata,
    RunResult,
    RunStatus,
)
from jev.policy import PolicyBundle
from jev.runner import EXIT_FAILURE, EXIT_OK, EXIT_USAGE, MatchOptions, Sc2Unavailable, run_match
from jev.runtime import JevRuntime
from jev.telemetry import (
    MAX_TRACE_SEGMENTS,
    POLICY_ARCHIVE_FILE,
    STATE_FILE,
    RunRecorder,
    TelemetryLimits,
    trace_segment_name,
    utc_timestamp,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = _REPO_ROOT / "scripts" / "validate_jev.py"
GUIDE = _REPO_ROOT / "documentation" / "operator" / "jev-validation.md"
BUNDLE = load_policy()
START = (30.5, 30.5)
#: JavaScript-unsafe unit tags (> 2**53) for the probes.
BIG_TAGS = tuple(2**60 + i for i in range(1, 7))
#: Injected into hostile answers; it must never reach the output unescaped.
ESCAPE = "\x1b[31m"


def _load_verifier() -> ModuleType:
    """Import ``scripts/validate_jev.py`` as module ``validate_jev_cli`` (registered
    before ``exec_module`` so its dataclasses resolve their module)."""
    name = "validate_jev_cli"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(SCRIPT))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


VERIFIER = _load_verifier()


@pytest.fixture(scope="module")
def verifier() -> ModuleType:
    return VERIFIER


# ---------------------------------------------------------------------------
# A real API server, and runs written by the real writer
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _serving(app: Any) -> Iterator[str]:
    """Serve ``app`` with uvicorn on 127.0.0.1:<ephemeral>; yield its base URL.

    The listening socket is bound before the server thread starts, so the port
    is known up front; readiness is polled against a deadline, and the server
    is always shut down and joined.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    config = uvicorn.Config(app, log_level="warning", lifespan="off", ws="none")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30.0
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("the test API server did not start")
            thread.join(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(30.0)
        listener.close()


@dataclass(frozen=True)
class _Served:
    root: Path
    base: str


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Served]:
    """The real ``/api/jev`` router over a temporary run root, served over HTTP."""
    root = tmp_path_factory.mktemp("jev-runs")
    app = FastAPI()
    app.include_router(create_router(root))
    with _serving(app) as base:
        yield _Served(root, base)


def _observation(seconds: float) -> Observation:
    """Idle probes beside a Nexus and no minerals: the v1 policy issues gathers."""
    return Observation(
        game_loop=round(seconds * 22.4),
        game_seconds=seconds,
        minerals=0,
        supply_used=len(BIG_TAGS),
        supply_cap=15,
        own_units=tuple(
            Entity(tag=tag, type_name="Probe", position=(28.0, 26.0), health=20.0)
            for tag in BIG_TAGS
        ),
        own_structures=(
            Entity(
                tag=1000,
                type_name="Nexus",
                position=START,
                health=1000.0,
                is_structure=True,
                ready=True,
                idle=True,
            ),
        ),
        visible_enemies=(),
        remembered_enemy_structures=(),
        start_location=START,
        enemy_start_locations=((120.5, 120.5),),
        expansion_locations=(),
        map_center=(75.5, 75.5),
        mineral_fields=tuple(
            Entity(
                tag=3000 + i,
                type_name="MineralField",
                position=(26.5 + i, 22.5),
                health=0.0,
                is_structure=True,
            )
            for i in range(8)
        ),
    )


def _metadata(bundle: PolicyBundle = BUNDLE, run_id: str | None = None) -> RunMetadata:
    return RunMetadata(
        run_id=uuid.uuid4().hex if run_id is None else run_id,
        created_at=utc_timestamp(time.time()),
        family=bundle.policy.family,
        version=bundle.policy.version,
        policy_hash=bundle.policy_hash,
        source_commit=None,
        map="Simple64",
        opponent_race="Terran",
        difficulty=1,
        seed=1,
        max_game_seconds=900.0,
        max_wall_seconds=1800.0,
    )


type _Outcome = tuple[RunStatus, RunResult | None, JevError | None]
FINISHED_WIN: _Outcome = ("finished", "win", None)


def _write_run(
    root: Path,
    *,
    outcome: _Outcome | None = FINISHED_WIN,
    bundle: PolicyBundle = BUNDLE,
    metadata: RunMetadata | None = None,
    wall_time: Callable[[], float] = time.time,
    limits: TelemetryLimits | None = None,
    ticks: int = 12,
    replay: bool = False,
) -> str:
    """One run written by RunRecorder from a ticking JevRuntime; returns its run ID.

    ``outcome`` None leaves the run live (no terminal state). Every update writes
    the state unless ``limits`` say otherwise.
    """
    chosen = _metadata(bundle) if metadata is None else metadata
    recorder = RunRecorder(
        root,
        chosen,
        roots=bundle.policy.roots,
        limits=TelemetryLimits(min_state_interval_seconds=0.0) if limits is None else limits,
        wall_time=wall_time,
    )
    recorder.start(bundle.policy_bytes)
    runtime = JevRuntime(bundle.policy, run_id=chosen.run_id)
    for step in range(ticks):
        recorder.update(runtime, runtime.tick(_observation(step * 0.25)).events)
    if replay:  # what burnysc2 saves into the run directory at the end of a match
        recorder.replay_file.write_bytes(b"SC2 replay bytes")
    if outcome is not None:
        status, result, error = outcome
        recorder.finish(runtime, status=status, result=result, error=error)
    return chosen.run_id


def _variant_bundle(directory: Path) -> PolicyBundle:
    """A valid policy that differs from the packaged one (so its hash differs)."""
    document = json.loads(BUNDLE.policy_bytes)
    document["nodes"][0]["label"] = "Variant root lane"
    candidate = directory / "variant-policy.json"
    candidate.write_text(json.dumps(document), encoding="utf-8")
    return load_policy(candidate)


def _rewrite_json(path: Path, change: Callable[[dict[str, Any]], None]) -> None:
    """Edit a stored record the way the writer encodes it (compact ASCII, key order kept)."""
    document = json.loads(path.read_bytes())
    change(document)
    path.write_bytes(json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode())


def _verify(
    verifier: ModuleType,
    capsys: pytest.CaptureFixture[str],
    run_id: str,
    base: str,
    run_root: Path,
    *extra: str,
) -> tuple[int, str]:
    argv = ["--run-id", run_id, "--api-base", base, "--run-root", str(run_root), *extra]
    code = verifier.main(argv)
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out + captured.err
    return code, captured.out


# ---------------------------------------------------------------------------
# Passing runs
# ---------------------------------------------------------------------------


def test_a_finished_run_passes_and_writes_the_json_report(
    verifier: ModuleType, served: _Served, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = _write_run(served.root, replay=True)
    report_path = tmp_path / "verify.json"
    code, out = _verify(
        verifier, capsys, run_id, served.base, served.root, "--json", str(report_path)
    )
    assert code == EXIT_OK, out
    assert "validate_jev: PASS" in out and "FAIL" not in out
    report = json.loads(report_path.read_text(encoding="ascii"))
    assert (report["verdict"], report["run_id"], report["failures"]) == ("pass", run_id, [])
    run = report["run"]
    assert (run["status"], run["result"], run["policy_hash"]) == (
        "finished",
        "win",
        BUNDLE.policy_hash,
    )
    assert run["replay_path"] == telemetry.REPLAY_FILE and run["stale"] is False
    trace = report["trace"]
    assert trace["complete"] is True and trace["retained_events"] == trace["events"] > 0


def test_a_live_run_passes_with_a_rerun_note(
    verifier: ModuleType, served: _Served, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = _write_run(served.root, outcome=None)
    code, out = _verify(verifier, capsys, run_id, served.base, served.root)
    assert code == EXIT_OK, out
    assert "note: the run is live (status 'running' on disk)" in out


@pytest.mark.parametrize(
    ("segment_bytes", "kept_segments", "complete"),
    [(16 * 1024, MAX_TRACE_SEGMENTS, True), (4 * 1024, 2, False)],
    ids=["rotated", "rotated-and-pruned"],
)
def test_rotation_and_writer_reported_drops_are_tolerated(
    verifier: ModuleType,
    served: _Served,
    capsys: pytest.CaptureFixture[str],
    segment_bytes: int,
    kept_segments: int,
    complete: bool,
) -> None:
    limits = TelemetryLimits(
        max_segment_bytes=segment_bytes,
        max_segments=kept_segments,
        min_state_interval_seconds=0.0,
    )
    run_id = _write_run(served.root, limits=limits, ticks=24)
    state = json.loads((served.root / run_id / STATE_FILE).read_bytes())
    assert 1 < state["trace"]["segment"] and state["trace"]["complete"] is complete
    if complete:  # every segment is still within the retention window
        assert state["trace"]["segment"] <= MAX_TRACE_SEGMENTS
    code, out = _verify(verifier, capsys, run_id, served.base, served.root)
    assert code == EXIT_OK, out
    assert ("the trace is incomplete" in out) is not complete


def test_a_torn_trace_tail_is_ignored(
    verifier: ModuleType, served: _Served, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = _write_run(served.root)
    with (served.root / run_id / trace_segment_name(1)).open("ab") as segment:
        segment.write(b'{"schema_version":1,"run_id":')  # an interrupted append
    code, out = _verify(verifier, capsys, run_id, served.base, served.root)
    assert code == EXIT_OK, out
    assert "end in an interrupted append" in out


# ---------------------------------------------------------------------------
# Every terminal state the production runner writes verifies
# ---------------------------------------------------------------------------


def _unit(tag: int, name: str = "Probe", **fields: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "tag": tag,
        "name": name,
        "position": (28.0, 26.0),
        "health": 40.0,
        "shield": 0.0,
        "build_progress": 1.0,
        "orders": [],
        "is_structure": False,
        "is_flying": False,
        "is_ready": True,
        "is_idle": True,
        "is_powered": False,
        "is_visible": True,
    }
    values.update(fields)
    return SimpleNamespace(**values)


def _game() -> SimpleNamespace:
    """A burnysc2-shaped ``BotAI`` stand-in: idle probes, a Nexus, no minerals."""
    return SimpleNamespace(
        state=SimpleNamespace(game_loop=0),
        minerals=0,
        supply_used=2,
        supply_cap=15,
        units=[_unit(tag) for tag in BIG_TAGS[:2]],
        structures=[_unit(1000, "Nexus", position=START, is_structure=True)],
        enemy_units=[],
        enemy_structures=[],
        mineral_field=[
            _unit(3000 + i, "MineralField", position=(26.5 + i, 22.5)) for i in range(8)
        ],
        start_location=START,
        enemy_start_locations=[(120.5, 120.5)],
        expansion_locations_list=[(120.5, 120.5), START],
        game_info=SimpleNamespace(map_center=(75.5, 75.5)),
    )


class _Port:
    """A ``jev.sc2_adapter.GamePort`` that accepts every command, like an SC2 that
    never refuses."""

    def __init__(self) -> None:
        self.left = False

    def is_visible(self, point: tuple[float, float]) -> bool:
        return False

    async def placement_legal(
        self, ability: str, sites: Sequence[tuple[float, float]]
    ) -> Sequence[bool]:
        return [True] * len(sites)

    async def issue(self, ability: str, actor: object, target: object) -> str | None:
        return None

    def action_errors(self) -> Sequence[tuple[int, str, str]]:
        return []

    async def leave(self) -> None:
        self.left = True


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


type _Hook = Callable[[SimpleNamespace, JevController, int], None]


class _Launcher:
    """Plays 12 JevBot steps the way burnysc2 does: a bot that left the game ends it
    with a Defeat; otherwise the match ends with ``result``, or ``escape`` is raised
    out of the match once the steps are played."""

    def __init__(
        self,
        *,
        result: RunResult | None = "win",
        hook: _Hook | None = None,
        prepare_error: BaseException | None = None,
        play_error: BaseException | None = None,
        escape: BaseException | None = None,
    ) -> None:
        self.result = result
        self.hook = hook
        self.prepare_error = prepare_error
        self.play_error = play_error
        self.escape = escape
        self.port = _Port()

    def prepare(self, options: MatchOptions) -> object:
        if self.prepare_error is not None:
            raise self.prepare_error
        return "setup"

    def play(self, controller: JevController, setup: object, options: MatchOptions) -> Any:
        if self.play_error is not None:
            raise self.play_error
        game = _game()
        bot = JevBot(controller)
        controller.attach(game, self.port)  # what on_start binds against a live game

        async def run() -> RunResult | None:
            for index in range(12):
                if self.hook is not None:
                    self.hook(game, controller, index)
                await bot.on_step(index)
                if self.port.left:
                    await bot.on_end(Result.Defeat)
                    return "loss"
                game.state.game_loop += 8
            return self.result

        result = asyncio.run(run())
        if self.escape is not None:
            raise self.escape
        return result


def _at_step_two(action: Callable[[SimpleNamespace, JevController], None]) -> _Hook:
    def hook(game: SimpleNamespace, controller: JevController, index: int) -> None:
        if index == 2:
            action(game, controller)

    return hook


def _corrupt_units(game: SimpleNamespace, controller: JevController) -> None:
    game.units = 12  # the adapter raises ValueError while observing: a crash


def _stop(game: SimpleNamespace, controller: JevController) -> None:
    controller.request_stop()  # what Ctrl+C asks for mid-match


def _past_the_wall_limit(clock: _Clock) -> _Launcher:
    def advance(game: SimpleNamespace, controller: JevController) -> None:
        clock.now = 10_000.0

    return _Launcher(hook=_at_step_two(advance))


@pytest.mark.parametrize(
    ("make_launcher", "options", "expected"),
    [
        (lambda clock: _Launcher(), MatchOptions(), ("finished", "win", None)),
        (lambda clock: _Launcher(result="loss"), MatchOptions(), ("finished", "loss", None)),
        (lambda clock: _Launcher(result=None), MatchOptions(), ("failed", None, None)),
        (
            lambda clock: _Launcher(hook=_at_step_two(_stop)),
            MatchOptions(),
            ("stopped", None, None),
        ),
        (
            lambda clock: _Launcher(play_error=KeyboardInterrupt()),
            MatchOptions(),
            ("stopped", None, None),
        ),
        (
            lambda clock: _Launcher(hook=_at_step_two(_corrupt_units)),
            MatchOptions(),
            ("failed", None, "match_crashed"),
        ),
        (
            lambda clock: _Launcher(),
            MatchOptions(max_game_seconds=1),
            ("finished", "timeout", "game_timeout"),
        ),
        (
            _past_the_wall_limit,
            MatchOptions(max_wall_seconds=60),
            ("failed", "timeout", "wall_timeout"),
        ),
        (
            lambda clock: _Launcher(prepare_error=Sc2Unavailable("no install")),
            MatchOptions(),
            ("failed", None, "sc2_unavailable"),
        ),
    ],
    ids=[
        "win",
        "loss",
        "undecided",
        "stop",
        "ctrl-c",
        "crash",
        "game-limit",
        "wall-limit",
        "sc2-unavailable",
    ],
)
def test_every_terminal_state_the_runner_writes_verifies(
    verifier: ModuleType,
    served: _Served,
    capsys: pytest.CaptureFixture[str],
    make_launcher: Callable[[_Clock], _Launcher],
    options: MatchOptions,
    expected: tuple[str, str | None, str | None],
) -> None:
    clock = _Clock()
    launcher = make_launcher(clock)
    outcome = run_match(options, BUNDLE, run_root=served.root, launcher=launcher, clock=clock)
    code = None if outcome.error is None else outcome.error.code
    assert (outcome.status, outcome.result, code) == expected
    verified, out = _verify(verifier, capsys, outcome.run_id, served.base, served.root)
    assert verified == EXIT_OK, out


def test_a_run_ended_by_an_escaping_exception_verifies(
    verifier: ModuleType, served: _Served, capsys: pytest.CaptureFixture[str]
) -> None:
    """A cancellation escaping the match ends the run through ``RunRecorder.finish(None)``.

    The clock never advances, so no state is published after ``starting``: the
    terminal state must still account for every traced event.
    """
    run_id = uuid.uuid4().hex
    launcher = _Launcher(escape=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        run_match(
            MatchOptions(),
            BUNDLE,
            run_root=served.root,
            launcher=launcher,
            run_id=run_id,
            clock=_Clock(),
        )
    state = json.loads((served.root / run_id / STATE_FILE).read_bytes())
    assert state["status"] == "stopped" and state["recent_events"]
    code, out = _verify(verifier, capsys, run_id, served.base, served.root)
    assert code == EXIT_OK, out


# ---------------------------------------------------------------------------
# Failing runs: every failure code has a producing case
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Reply:
    status: int
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()


def _json_reply(document: object) -> _Reply:
    return _Reply(200, json.dumps(document).encode())


NOT_FOUND = _Reply(404, b'{"detail":"Not Found"}')


@contextlib.contextmanager
def _answering(reply_for: Callable[[str], _Reply]) -> Iterator[str]:
    """A stdlib HTTP server on 127.0.0.1:<ephemeral> answering each GET with
    ``reply_for(path)``: a real server standing in for an API that serves what the
    real one never would (another schema version, a hostile body). Yields its base URL.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            reply = reply_for(self.path)
            with contextlib.suppress(OSError):  # the verifier may hang up mid-body
                self.send_response(reply.status)
                for name, value in reply.headers:
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(reply.body)))
                self.end_headers()
                self.wfile.write(reply.body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(30.0)


def _fetch(base: str, path: str) -> dict[str, Any]:
    """A document exactly as the real API serves it (no proxy)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"{base}{path}", timeout=30) as response:
        document: dict[str, Any] = json.load(response)
    return document


@dataclass(frozen=True)
class _Case:
    run_id: str
    disk_root: Path  # --run-root: the served root, or a diverging copy
    base: str  # --api-base: the real API, or a server answering what it never would


@dataclass(frozen=True)
class _Setup:
    root: Path  # the run root the real API serves
    scratch: Path  # an empty directory for a diverging disk copy
    base: str  # the real API
    stack: contextlib.ExitStack  # keeps a crafted server up while the verifier runs

    def served(self, run_id: str) -> _Case:
        return _Case(run_id, self.root, self.base)

    def crafted(self, run_id: str, edit: Callable[[dict[str, Any]], None]) -> _Case:
        """The real API's answers for ``run_id``, with the run detail edited."""
        detail_path = f"{api_module.API_PREFIX}/runs/{run_id}"
        policy_path = f"{detail_path}/policy"
        detail = _fetch(self.base, detail_path)
        edit(detail)
        replies = {
            detail_path: _json_reply(detail),
            policy_path: _json_reply(_fetch(self.base, policy_path)),
        }
        base = self.stack.enter_context(_answering(lambda path: replies.get(path, NOT_FOUND)))
        return _Case(run_id, self.root, base)


def _archive_edited(setup: _Setup) -> _Case:
    run_id = _write_run(setup.root)
    archive = setup.root / run_id / POLICY_ARCHIVE_FILE
    archive.write_bytes(_variant_bundle(setup.scratch).policy_bytes)
    return setup.served(run_id)


def _metadata_names_another_policy(setup: _Setup) -> _Case:
    """The writer recorded the variant's hash, but archived and ran the packaged policy."""
    metadata = replace(_metadata(), policy_hash=_variant_bundle(setup.scratch).policy_hash)
    return setup.served(_write_run(setup.root, metadata=metadata))


def _api_serves_another_policy(setup: _Setup) -> _Case:
    """Disk and API each hold a consistent run under one ID, with different policies."""
    run_id = _write_run(setup.root)
    variant = _variant_bundle(setup.scratch)
    _write_run(setup.scratch, bundle=variant, metadata=_metadata(variant, run_id))
    return _Case(run_id, setup.scratch, setup.base)


def _policy_deleted(setup: _Setup) -> _Case:
    run_id = _write_run(setup.root)
    (setup.root / run_id / POLICY_ARCHIVE_FILE).unlink()
    return setup.served(run_id)


def _state_cut_short(setup: _Setup) -> _Case:
    run_id = _write_run(setup.root)
    (setup.root / run_id / STATE_FILE).write_bytes(b'{"schema_version":1,"run_id":')
    return setup.served(run_id)


def _finished_without_result(setup: _Setup) -> _Case:
    return setup.served(_write_run(setup.root, outcome=("finished", None, None)))


def _stopped_with_a_win(setup: _Setup) -> _Case:
    return setup.served(_write_run(setup.root, outcome=("stopped", "win", None)))


def _stale_running(setup: _Setup) -> _Case:
    """A live run whose producer last wrote an hour ago (the writer's injected clock)."""
    return setup.served(
        _write_run(setup.root, outcome=None, wall_time=lambda: time.time() - 3600.0)
    )


def _unknown_run(setup: _Setup) -> _Case:
    return setup.served(uuid.uuid4().hex)


def _trace_line_corrupted(setup: _Setup) -> _Case:
    run_id = _write_run(setup.root)
    segment = setup.root / run_id / trace_segment_name(1)
    lines = segment.read_bytes().split(b"\n")
    segment.write_bytes(b"\n".join([b'{"schema_version": 1, "run_id": 7}', *lines[1:]]))
    return setup.served(run_id)


def _trace_event_removed(setup: _Setup) -> _Case:
    run_id = _write_run(setup.root)
    segment = setup.root / run_id / trace_segment_name(1)
    segment.write_bytes(b"\n".join(segment.read_bytes().split(b"\n")[1:]))
    return setup.served(run_id)


def _last_sequence_behind_the_trace(setup: _Setup) -> _Case:
    run_id = _write_run(setup.root)
    _rewrite_json(setup.root / run_id / STATE_FILE, lambda state: state.update(last_sequence=1))
    return setup.served(run_id)


def _replay_deleted(setup: _Setup) -> _Case:
    run_id = _write_run(setup.root, replay=True)
    (setup.root / run_id / telemetry.REPLAY_FILE).unlink()
    return setup.served(run_id)


def _disk_copy_diverges(setup: _Setup) -> _Case:
    """The verifier reads a copy whose (valid) state differs from what the API serves."""
    run_id = _write_run(setup.root)
    shutil.copytree(setup.root / run_id, setup.scratch / run_id)
    _rewrite_json(
        setup.scratch / run_id / STATE_FILE,
        lambda state: state.update(game_seconds=state["game_seconds"] + 1.0),
    )
    return _Case(run_id, setup.scratch, setup.base)


def _api_marks_a_finished_run_stale(setup: _Setup) -> _Case:
    return setup.crafted(_write_run(setup.root), lambda detail: detail.update(stale=True))


def _api_serves_schema_2(setup: _Setup) -> _Case:
    return setup.crafted(_write_run(setup.root), lambda detail: detail.update(schema_version=2))


def _live_api_behind_the_disk(setup: _Setup) -> _Case:
    """The API's live state is older than the state.json read before it."""
    run_id = _write_run(setup.root, outcome=None)
    return setup.crafted(
        run_id, lambda detail: detail.update(last_sequence=detail["last_sequence"] - 1)
    )


def _live_api_reports_a_result(setup: _Setup) -> _Case:
    run_id = _write_run(setup.root, outcome=None)
    return setup.crafted(run_id, lambda detail: detail.update(result="win"))


def _live_api_without_a_sequence(setup: _Setup) -> _Case:
    run_id = _write_run(setup.root, outcome=None)
    return setup.crafted(
        run_id, lambda detail: detail.update(last_sequence=str(detail["last_sequence"]))
    )


def _api_unreachable(setup: _Setup) -> _Case:
    run_id = _write_run(setup.root)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]  # nothing listens once it is closed
    return _Case(run_id, setup.root, f"http://127.0.0.1:{closed_port}")


#: One case per defect, each with the failure code it must produce.
FAILURE_CASES: list[tuple[Callable[[_Setup], _Case], str]] = [
    (_archive_edited, "hash_mismatch"),
    (_metadata_names_another_policy, "hash_mismatch"),
    (_api_serves_another_policy, "hash_mismatch"),
    (_policy_deleted, "policy_missing"),
    (_state_cut_short, "corrupt_run"),
    (_finished_without_result, "malformed_terminal_result"),
    (_stopped_with_a_win, "malformed_terminal_result"),
    (_live_api_reports_a_result, "malformed_terminal_result"),
    (_stale_running, "stale_run"),
    (_unknown_run, "run_not_found"),
    (_trace_line_corrupted, "corrupt_trace"),
    (_trace_event_removed, "trace_mismatch"),
    (_last_sequence_behind_the_trace, "sequence_mismatch"),
    (_live_api_behind_the_disk, "sequence_mismatch"),
    (_replay_deleted, "replay_missing"),
    (_disk_copy_diverges, "api_mismatch"),
    (_api_marks_a_finished_run_stale, "api_mismatch"),
    (_api_serves_schema_2, "schema_mismatch"),
    (_live_api_without_a_sequence, "api_error"),
    (_api_unreachable, "api_unreachable"),
]


@pytest.mark.parametrize(
    ("build", "code"),
    FAILURE_CASES,
    ids=lambda value: value if isinstance(value, str) else value.__name__.strip("_"),
)
def test_each_defect_fails_with_its_code(
    verifier: ModuleType,
    served: _Served,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    build: Callable[[_Setup], _Case],
    code: str,
) -> None:
    scratch = tmp_path / "disk"
    scratch.mkdir()
    report_path = tmp_path / "verify.json"
    with contextlib.ExitStack() as stack:
        case = build(_Setup(served.root, scratch, served.base, stack))
        argv = ["--json", str(report_path)]
        exit_code, out = _verify(verifier, capsys, case.run_id, case.base, case.disk_root, *argv)
    assert exit_code == EXIT_FAILURE, out
    assert f"FAIL {code}: " in out
    report = json.loads(report_path.read_text(encoding="ascii"))
    assert report["verdict"] == "fail"
    assert code in {failure["code"] for failure in report["failures"]}


#: A well-formed run ID (UUID4 hex) for argument cases that never reach a run.
RUN_ID = "0123456789ab4def8123456789abcdef"
API_BASE = "http://127.0.0.1:8765"
#: Command-line values the verifier refuses as usage errors, with their code.
INVALID_ARGUMENT_CASES = [
    pytest.param("F" * 32, API_BASE, "invalid_run_id", id="uppercase-id"),
    pytest.param("../" + RUN_ID, API_BASE, "invalid_run_id", id="traversal-id"),
    pytest.param(RUN_ID, "ftp://127.0.0.1:8765", "invalid_api_base", id="ftp-base"),
    pytest.param(RUN_ID, "http://127.0.0.1:8765/api/jev", "invalid_api_base", id="path-base"),
    pytest.param(RUN_ID, "http://user:pw@127.0.0.1:8765", "invalid_api_base", id="credentials"),
    pytest.param(RUN_ID, "http://127.0.0.1:0", "invalid_api_base", id="port-zero"),
    pytest.param(RUN_ID, "http://" + "a" * 64 + ":8765", "invalid_api_base", id="long-label"),
    pytest.param(RUN_ID, "http://127.0.0.1:8765\n", "invalid_api_base", id="newline-base"),
]


@pytest.mark.parametrize(("run_id", "api_base", "code"), INVALID_ARGUMENT_CASES)
def test_invalid_arguments_are_usage_errors_with_their_code(
    verifier: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_id: str,
    api_base: str,
    code: str,
) -> None:
    report_path = tmp_path / "verify.json"
    exit_code, out = _verify(
        verifier, capsys, run_id, api_base, tmp_path, "--json", str(report_path)
    )
    assert exit_code == EXIT_USAGE
    assert f"FAIL {code}: " in out
    assert not report_path.exists()  # nothing was verified, so nothing is reported


#: Failure codes that no case above produces, each with its reason. None today.
UNPRODUCED_CODES: frozenset[str] = frozenset()


def test_every_failure_code_has_a_producing_case(verifier: ModuleType) -> None:
    usage = {param.values[2] for param in INVALID_ARGUMENT_CASES}
    produced = {code for _, code in FAILURE_CASES} | usage
    assert produced == set(verifier.FAILURE_CODES) - UNPRODUCED_CODES
    assert usage == verifier.USAGE_FAILURE_CODES


def test_a_relative_run_root_is_a_usage_error(
    verifier: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--run-id", RUN_ID, "--api-base", API_BASE]
    with pytest.raises(SystemExit) as excinfo:
        verifier.main([*argv, "--run-root", "relative/runs"])
    assert excinfo.value.code == EXIT_USAGE
    assert "expected an absolute path" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Hostile HTTP answers
# ---------------------------------------------------------------------------


def _jev_error(message: str) -> bytes:
    error = {"schema_version": 1, "error": {"code": "corrupt_run", "message": message}}
    return json.dumps(error).encode()


@pytest.mark.parametrize(
    ("reply", "fragment"),
    [
        (_Reply(200, b"<html>dashboard</html>"), "is not a strict JSON object"),
        (_Reply(200, b"[]"), "is not a strict JSON object"),
        (_Reply(200, b" " * (VERIFIER.MAX_RUN_RESPONSE_BYTES + 1)), "is larger than"),
        (
            _Reply(307, b"", (("Location", "http://jev-redirect.invalid/api/jev/runs"),)),
            "redirects are not followed",
        ),
        (NOT_FOUND, "bots.current.runner --serve"),
        (_Reply(503, _jev_error(ESCAPE + "x" * 100_000)), "HTTP 503 corrupt_run: "),
    ],
    ids=["html", "json-array", "oversized", "redirect", "foreign-404", "hostile-message"],
)
def test_hostile_api_answers_fail_cleanly(
    verifier: ModuleType,
    served: _Served,
    capsys: pytest.CaptureFixture[str],
    reply: _Reply,
    fragment: str,
) -> None:
    run_id = _write_run(served.root)
    with _answering(lambda path: reply) as base:
        exit_code, out = _verify(verifier, capsys, run_id, base, served.root)
    assert exit_code == EXIT_FAILURE
    errors = [line for line in out.splitlines() if line.startswith("FAIL api_error: ")]
    assert any(fragment in line for line in errors), out
    assert ESCAPE not in out and max(len(line) for line in out.splitlines()) < 4096


# ---------------------------------------------------------------------------
# Entry point, shared shapes and the operator guide
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("known", "returncode", "verdict"),
    [(True, EXIT_OK, "validate_jev: PASS"), (False, EXIT_FAILURE, "FAIL run_not_found: ")],
    ids=["pass", "fail"],
)
def test_the_script_runs_as_a_subprocess(
    served: _Served, tmp_path: Path, known: bool, returncode: int, verdict: str
) -> None:
    run_id = _write_run(served.root) if known else uuid.uuid4().hex
    argv = ["--run-id", run_id, "--api-base", served.base, "--run-root", str(served.root)]
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        cwd=tmp_path,  # nothing may resolve through the working directory
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == returncode, proc.stdout + proc.stderr
    assert verdict in proc.stdout and "Traceback" not in proc.stderr


def test_the_verifier_imports_its_shapes_from_jev(verifier: ModuleType) -> None:
    assert verifier.API_PREFIX is api_module.API_PREFIX
    assert verifier.STALE_AFTER_SECONDS is api_module.STALE_AFTER_SECONDS
    assert verifier.SCHEMA_VERSION is contracts.SCHEMA_VERSION
    assert verifier.TERMINAL_RUN_STATUSES is contracts.TERMINAL_RUN_STATUSES
    assert verifier.RECENT_EVENT_LIMIT is contracts.RECENT_EVENT_LIMIT
    assert verifier.ERROR_CODES is contracts.ERROR_CODES
    assert verifier.is_run_outcome is contracts.is_run_outcome
    assert verifier.MAX_TRACE_SEGMENTS is telemetry.MAX_TRACE_SEGMENTS
    assert verifier.STATE_FILE is telemetry.STATE_FILE
    assert verifier.METADATA_FILE is telemetry.METADATA_FILE
    assert verifier.POLICY_ARCHIVE_FILE is telemetry.POLICY_ARCHIVE_FILE
    assert verifier.read_trace_segment is telemetry.read_trace_segment
    assert verifier.default_run_root is telemetry.default_run_root
    assert verifier.absolute_path is runner.absolute_path
    assert verifier.make_streams_encoding_safe is runner.make_streams_encoding_safe
    assert verifier.TerminalSafeArgumentParser is runner.TerminalSafeArgumentParser


def _guide_commands(marker: str) -> list[list[str]]:
    """The arguments of every command line in the guide's code blocks that runs ``marker``."""
    text = GUIDE.read_text(encoding="utf-8")
    commands: list[list[str]] = []
    for block in re.findall(r"```powershell\n(.*?)```", text, flags=re.DOTALL):
        for line in block.splitlines():
            if marker not in line or line.lstrip().startswith("#"):
                continue
            tokens = [token.strip('"') for token in shlex.split(line, posix=False)]
            commands.append(tokens[tokens.index(marker) + 1 :])
    return commands


def _substitute(arguments: list[str]) -> list[str]:
    """A stand-in run ID for the guide's ``$runId``; other values parse as written."""
    return [
        RUN_ID
        if argument.startswith("$") and index and arguments[index - 1] == "--run-id"
        else argument
        for index, argument in enumerate(arguments)
    ]


def test_every_guide_command_parses(verifier: ModuleType) -> None:
    verifier_commands = _guide_commands("scripts\\validate_jev.py")
    jev_commands = _guide_commands("bots.jev.v1")
    assert verifier_commands and jev_commands
    for arguments in verifier_commands:
        verifier.build_parser().parse_args(_substitute(arguments))
    for arguments in jev_commands:
        runner.build_parser("bots.jev.v1").parse_args(_substitute(arguments))


def test_the_guide_documents_every_failure_code(verifier: ModuleType) -> None:
    text = GUIDE.read_text(encoding="utf-8")
    assert [code for code in verifier.FAILURE_CODES if f"`{code}`" not in text] == []


@pytest.mark.parametrize(
    "section",
    [
        "Install",
        "Start the dashboard",
        "Stop cleanly",
        "Step 207",
        "Step 208",
        "Evidence locations",
        "Acceptance report template",
        "Cleanup",
    ],
)
def test_the_guide_has_each_required_section(section: str) -> None:
    """Step 206's guide contents, by the guide's numbered section headings."""
    text = GUIDE.read_text(encoding="utf-8")
    headings = re.findall(r"^## \d+\. (.+)$", text, flags=re.MULTILINE)
    assert any(section in heading for heading in headings), headings
