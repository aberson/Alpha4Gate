"""Dashboard-first launch (Jev v2 Step 224, plan D7): sessions, the ready barrier, following.

Nothing here launches StarCraft II or calls the hosted service. Every match is played
by the production :func:`jev.runner.run_match` against a stand-in game launcher
(``test_jev_benchmark._GameLauncher``) that records whether the dashboard had
acknowledged the run when it was asked to "start SC2". The dashboard side is the real
router (:func:`jev.api.create_router`): through ``TestClient`` for the fake page that
the non-browser tests use, and served over real loopback HTTP by uvicorn for the
launcher and browser tests -- always on free ports of this test, never the operator's
dashboard on 8765/3000.

The real-browser roundtrip (:class:`TestRealBrowser`) drives Chromium through
Playwright against a production build of this checkout's frontend. It is opt-in so CI
without browsers stays green::

    $env:JEV_BROWSER_TESTS = '1'
    uv run --with playwright pytest tests/test_jev_launch.py -k RealBrowser -p no:cacheprovider

(``uv run --with playwright python -m playwright install chromium`` once if Chromium
is missing; Node and ``npm --prefix frontend ci`` are needed for the build.) With
``$env:JEV_BROWSER_EVIDENCE_DIR = '<dir>'`` as well, the browser tests also save a
full-page screenshot per phase and a ``timeline.jsonl`` there for reviewers, and the
timeout case waits out the production 60-second deadline.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import io
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
from bots.jev.v1 import load_policy
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from jev import api as api_module
from jev import benchmark, launch, runner
from jev.api import create_router
from jev.benchmark import (
    BatchStore,
    CaseSpec,
    ChildExit,
    ChildLaunch,
    DashboardObserver,
    capture_source,
    run_batch,
    runtime_source_paths,
)
from jev.contracts import is_valid_run_id
from jev.launch import (
    LAUNCH_ERROR_CODES,
    LAUNCH_STATES,
    MAX_LAUNCH_MESSAGE_CHARS,
    MAX_LAUNCH_RECORD_BYTES,
    DashboardEndpoints,
    DashboardLaunch,
    DashboardUnavailable,
    LaunchAborted,
    LaunchError,
    LaunchSession,
    LaunchSessionWriter,
    dashboard_health,
    launch_root_for,
    open_browser,
    read_ready,
    read_session,
    ready_hook,
    scrubbed_environment,
    start_dashboard_servers,
)
from jev.policy import load_policy_bundle
from jev.runner import EXIT_FAILURE, EXIT_OK, MatchOptions, run_match
from jev.telemetry import (
    METADATA_FILE,
    POLICY_ARCHIVE_FILE,
    STATE_FILE,
    EvidenceFiles,
    RunRecorder,
    default_run_root,
    read_run_metadata,
    read_run_state,
    repository_root,
)
from test_jev_benchmark import _GameLauncher, _sources, _store

REPO = Path(__file__).resolve().parents[1]
BUNDLE = load_policy()
KEY = "TYPESAFE_API_KEY"
FAKE_KEY = "launch-test-key"  # never a real credential
ORIGIN = "http://localhost:3000"


@pytest.fixture(autouse=True)
def no_real_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly instead of starting servers, opening a browser or reaching the
    operator's dashboard: every test injects its own servers, opener and starter."""

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a launch test reached a real server start, browser or dashboard")

    monkeypatch.setattr(launch, "start_dashboard_servers", forbidden)
    monkeypatch.setattr(launch, "open_browser", forbidden)
    monkeypatch.setattr(benchmark, "_default_dashboard", lambda *args: forbidden)


# ---------------------------------------------------------------------------
# Stand-ins: a gated game, the dashboard page, a served dashboard
# ---------------------------------------------------------------------------


class _GatedGame(_GameLauncher):
    """A stand-in SC2 launch that records whether the page had acknowledged its run.

    ``prepare`` is what starts SC2 in production: it notes the session's readiness
    receipt at that moment. ``launch_delay`` imitates SC2 starting up and
    ``step_delay`` a realtime game, so a page can watch Starting and then Live.
    """

    def __init__(
        self,
        launch_root: Path | None = None,
        session_id: str | None = None,
        *,
        steps: int = 6,
        launch_delay: float = 0.0,
        step_delay: float = 0.0,
        hosted: bool = False,
    ) -> None:
        pace = (lambda game, controller, index: time.sleep(step_delay)) if step_delay else None
        super().__init__(steps=steps, before_step=pace, hosted=hosted)
        self.launch_root = launch_root
        self.session_id = session_id
        self.launch_delay = launch_delay
        self.prepared = threading.Event()
        self.receipt: launch.LaunchReady | None = None

    def prepare(self, options: MatchOptions) -> object:
        if self.launch_root is not None and self.session_id is not None:
            self.receipt = read_ready(self.launch_root, self.session_id)
        self.prepared.set()
        time.sleep(self.launch_delay)
        return super().prepare(options)


def _page_client(run_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(run_root))
    return TestClient(app, base_url="http://localhost:3000", client=("127.0.0.1", 50124))


class _FakePage(threading.Thread):
    """What the dashboard page does, through the real API: follow the session and
    acknowledge each starting run once its state and archived policy have loaded."""

    def __init__(self, run_root: Path, session_id: str, *, acknowledge: bool = True) -> None:
        super().__init__(daemon=True)
        self.client = _page_client(run_root)
        self.session_id = session_id
        self.acknowledge = acknowledge
        self.done = threading.Event()
        self.acked: list[str] = []
        self.indexes: list[int] = []
        self.states: list[str] = []

    def run(self) -> None:
        while not self.done.wait(0.05):
            session = self.client.get(f"/api/jev/launches/{self.session_id}").json()
            state = session.get("state")
            if state is not None and (not self.states or self.states[-1] != state):
                self.states.append(state)
            run_id = session.get("active_run_id")
            if state != "starting" or run_id in self.acked or not self.acknowledge:
                continue
            run = self.client.get(f"/api/jev/runs/{run_id}")
            policy = self.client.get(f"/api/jev/runs/{run_id}/policy")
            if run.status_code != 200 or policy.status_code != 200:
                continue
            if run.json()["policy_hash"] != policy.json()["policy_hash"]:
                continue
            body = {"run_id": run_id, "policy_hash": policy.json()["policy_hash"]}
            answer = self.client.post(
                f"/api/jev/launches/{self.session_id}/ready", json=body, headers={"Origin": ORIGIN}
            )
            if answer.status_code == 200:
                self.acked.append(run_id)
                self.indexes.append(session["case_index"])

    def stop(self) -> None:
        self.done.set()
        self.join(10)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


class _Server:
    """A dashboard-like app on a free loopback port: the Jev router plus a page."""

    def __init__(
        self,
        run_root: Path,
        *,
        dist: Path | None = None,
        origins: set[str] | None = None,
        access_log: Path | None = None,
    ) -> None:
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        app = FastAPI()
        allowed = {self.base, f"http://localhost:{self.port}"} if origins is None else origins
        app.include_router(create_router(run_root, launch_origins=allowed))
        if dist is None:

            @app.get("/", response_class=HTMLResponse)
            def page() -> str:
                return '<!doctype html><div id="root"></div>'

        else:
            app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
        level = "error" if access_log is None else "info"
        config = uvicorn.Config(
            app, host="127.0.0.1", port=self.port, log_level=level, lifespan="off"
        )
        # Evidence mode: this server's access and error log also go to a file (the
        # Config above has just installed uvicorn's own logging configuration).
        self.log_handler: logging.Handler | None = None
        if access_log is not None:
            access_log.parent.mkdir(parents=True, exist_ok=True)
            self.log_handler = logging.FileHandler(access_log, encoding="utf-8")
            self.log_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
            for name in ("uvicorn.access", "uvicorn.error"):
                logging.getLogger(name).addHandler(self.log_handler)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.started = False

    @property
    def endpoints(self) -> DashboardEndpoints:
        return DashboardEndpoints(api_url=self.base, dashboard_url=self.base)

    def start(self) -> None:
        self.started = True
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("the test dashboard did not start")
            time.sleep(0.05)

    def stop(self) -> None:
        if self.started:
            self.server.should_exit = True
            self.thread.join(15)
        if self.log_handler is not None:
            for name in ("uvicorn.access", "uvicorn.error"):
                logging.getLogger(name).removeHandler(self.log_handler)
            self.log_handler.close()
            self.log_handler = None


@contextlib.contextmanager
def _serving(
    run_root: Path, *, dist: Path | None = None, access_log: Path | None = None
) -> Iterator[_Server]:
    server = _Server(run_root, dist=dist, access_log=access_log)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _recorded_run(run_root: Path) -> str:
    """A finished run (an archive the dashboard lists), unrelated to any session."""
    run_id = uuid.uuid4().hex
    metadata = runner._run_metadata(run_id, MatchOptions(), BUNDLE)
    recorder = RunRecorder(run_root, metadata, roots=BUNDLE.policy.roots)
    recorder.start(BUNDLE.policy_bytes)
    recorder.finish(None, status="finished", result="loss")
    return run_id


def _no_start() -> None:
    raise AssertionError("a healthy dashboard is reused, never started (and never the real one)")


def _navigate(page: Any) -> Callable[[str], None]:
    """The browser opener for a Playwright page: navigate, report success (None)."""

    def opener(url: str) -> None:
        page.goto(url)

    return opener


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


# ---------------------------------------------------------------------------
# Records: one owner, bounded, validated like run storage
# ---------------------------------------------------------------------------


def test_a_session_is_created_once_beside_the_run_root_and_read_back(tmp_path: Path) -> None:
    run_root = tmp_path / "data" / "jev" / "runs"
    root = launch_root_for(run_root)
    assert root == tmp_path / "data" / "jev" / "launches"
    assert launch_root_for(default_run_root()) == repository_root() / "data" / "jev" / "launches"
    assert api_module.launch_root_for is launch.launch_root_for  # one derivation for both sides
    writer = LaunchSessionWriter.create(root, case_count=6, message="preparing")
    assert is_valid_run_id(writer.session_id)
    session = read_session(root, writer.session_id)
    assert (session.state, session.active_run_id, session.case_index, session.case_count) == (
        "preparing",
        None,
        0,
        6,
    )
    document = json.loads((root / writer.session_id / "session.json").read_bytes())
    assert document == session.to_dict() and document["schema_version"] == 1
    with pytest.raises(FileExistsError):  # an existing session is never reused
        LaunchSessionWriter.create(root, session_id=writer.session_id)
    assert read_ready(root, writer.session_id) is None


def test_launch_records_are_bounded_and_strictly_validated(tmp_path: Path) -> None:
    root = tmp_path / "launches"
    writer = LaunchSessionWriter.create(root)
    long = writer.publish("preparing", message="x" * 1000 + "\x1b[31m")
    assert len(long.message) == MAX_LAUNCH_MESSAGE_CHARS
    shown = writer.publish("preparing", message="bell\x07")
    assert shown.message == "bell\\x07"  # rendered, never a raw control character
    assert len(json.dumps(long.to_dict())) <= MAX_LAUNCH_RECORD_BYTES
    base: dict[str, Any] = {
        "session_id": writer.session_id,
        "active_run_id": None,
        "state": "preparing",
        "case_index": 0,
        "case_count": 1,
        "updated_at": "2026-10-08T12:00:00.000+00:00",
        "message": "",
    }
    for change in (
        {"session_id": "../x"},
        {"active_run_id": "RUN"},
        {"state": "launching"},
        {"state": "starting"},  # a starting session names its run
        {"case_index": 1},
        {"case_count": 0},
        {"case_count": 65},
        {"updated_at": "yesterday"},
        {"message": "y" * 201},
        {"message": "\x00"},
    ):
        with pytest.raises(ValueError):
            LaunchSession(**{**base, **change})


def test_corrupt_or_foreign_records_are_corrupt_launches(tmp_path: Path) -> None:
    root = tmp_path / "launches"
    writer = LaunchSessionWriter.create(root)
    path = root / writer.session_id / "session.json"
    good = path.read_bytes()
    for content in (b"{", b"x" * 5000, good.replace(b'"preparing"', b'"paused"')):
        path.write_bytes(content)
        with pytest.raises(LaunchError) as caught:
            read_session(root, writer.session_id)
        assert (caught.value.code, caught.value.status) == ("corrupt_launch", 503)
    path.write_bytes(good)
    (root / writer.session_id / "ready.json").write_bytes(b'{"schema_version": 1}')
    with pytest.raises(LaunchError, match="ready.json"):
        read_ready(root, writer.session_id)
    for bad in ("..", "C:\\Windows", uuid.uuid4().hex.upper(), uuid.uuid1().hex):
        with pytest.raises(LaunchError) as caught:
            read_session(root, bad)
        assert caught.value.code == "invalid_launch_request"
    with pytest.raises(LaunchError) as caught:
        read_session(root, uuid.uuid4().hex)
    assert caught.value.code == "launch_not_found"


def test_only_an_open_session_can_be_adopted_by_a_game(tmp_path: Path) -> None:
    root = tmp_path / "launches"
    with pytest.raises(LaunchError, match="no launch session"):
        LaunchSessionWriter.adopt(root, uuid.uuid4().hex)
    writer = LaunchSessionWriter.create(root)
    assert LaunchSessionWriter.adopt(root, writer.session_id).session == writer.session
    writer.end("finished", "done")
    with pytest.raises(LaunchError) as caught:
        LaunchSessionWriter.adopt(root, writer.session_id)
    assert caught.value.code == "launch_not_ready"
    assert writer.end("failed", "late").state == "finished"  # an ended session keeps its end


# ---------------------------------------------------------------------------
# The barrier: only this session's exact run and archived policy release it
# ---------------------------------------------------------------------------


def _write_ready(root: Path, session_id: str, run_id: str, policy_hash: str) -> None:
    ready = launch.LaunchReady(session_id, run_id, policy_hash, "2026-10-08T12:00:00.000+00:00")
    (root / session_id / "ready.json").write_text(json.dumps(ready.to_dict()))


def test_the_barrier_releases_only_for_this_sessions_exact_run_and_hash(tmp_path: Path) -> None:
    root = tmp_path / "launches"
    writer = LaunchSessionWriter.create(root)
    sid, run_id, other_hash = writer.session_id, uuid.uuid4().hex, "b" * 64
    previous_run, other_session = uuid.uuid4().hex, uuid.uuid4().hex
    attempts = [
        lambda: _write_ready(root, sid, previous_run, BUNDLE.policy_hash),  # stale: older run
        lambda: _write_ready(root, sid, run_id, other_hash),  # another policy
        lambda: (
            _write_ready(root, other_session, run_id, BUNDLE.policy_hash)
            if (root / other_session).mkdir() is None
            else None
        ),  # a receipt in another session's folder
        lambda: (root / sid / "ready.json").write_bytes(b"{corrupt"),
        lambda: _write_ready(root, sid, run_id, BUNDLE.policy_hash),  # the exact one
    ]
    seen: list[str] = []

    def sleep(_: float) -> None:
        seen.append(read_session(root, sid).state)
        if attempts:
            attempts.pop(0)()

    clock = _Clock()
    hook = ready_hook(writer, BUNDLE.policy_hash, clock=clock, sleep=sleep)
    hook(run_id)
    assert not attempts and seen[0] == "starting" and len(seen) == 5  # four never released
    session = read_session(root, sid)
    assert (session.state, session.active_run_id) == ("running", run_id)


def test_no_acknowledgment_in_time_fails_the_session_and_never_releases(tmp_path: Path) -> None:
    root = tmp_path / "launches"
    writer = LaunchSessionWriter.create(root)
    clock = _Clock()

    def sleep(seconds: float) -> None:
        clock.now += seconds

    hook = ready_hook(writer, BUNDLE.policy_hash, deadline_seconds=5, clock=clock, sleep=sleep)
    run_id = uuid.uuid4().hex
    with pytest.raises(LaunchAborted, match="within 5 s"):
        hook(run_id)
    session = read_session(root, writer.session_id)
    assert (session.state, session.active_run_id) == ("failed", run_id)
    assert "SC2 was not started" in session.message


def test_ctrl_c_before_acknowledgment_stops_the_session_cleanly(tmp_path: Path) -> None:
    root = tmp_path / "launches"
    writer = LaunchSessionWriter.create(root)

    def sleep(_: float) -> None:
        raise KeyboardInterrupt

    hook = ready_hook(writer, BUNDLE.policy_hash, sleep=sleep)
    with pytest.raises(KeyboardInterrupt):
        hook(uuid.uuid4().hex)
    assert read_session(root, writer.session_id).state == "stopped"


# ---------------------------------------------------------------------------
# The production runner hook
# ---------------------------------------------------------------------------


def test_run_match_hook_defaults_to_none_and_legacy_runs_are_unchanged(tmp_path: Path) -> None:
    parameter = inspect.signature(run_match).parameters["on_recorded"]
    assert parameter.default is None and parameter.kind is inspect.Parameter.KEYWORD_ONLY
    game = _GatedGame()
    outcome = run_match(MatchOptions(), BUNDLE, run_root=tmp_path, launcher=game)
    assert (outcome.status, outcome.result, outcome.exit_code) == ("finished", "win", EXIT_OK)
    assert game.prepared.is_set() and not (tmp_path.parent / "launches").exists()


def test_the_hook_runs_after_the_archive_and_before_sc2_can_start(tmp_path: Path) -> None:
    game = _GatedGame()
    observed: dict[str, Any] = {}

    def hook(run_id: str) -> None:
        run_dir = tmp_path / run_id
        observed["files"] = {p.name for p in run_dir.iterdir()}
        metadata = read_run_metadata(run_dir)
        assert metadata is not None
        observed["status"] = read_run_state(run_dir, metadata).status
        observed["prepared"] = game.prepared.is_set()
        observed["run_id"] = run_id

    outcome = run_match(MatchOptions(), BUNDLE, run_root=tmp_path, launcher=game, on_recorded=hook)
    assert observed["run_id"] == outcome.run_id
    assert {POLICY_ARCHIVE_FILE, STATE_FILE, METADATA_FILE} <= observed["files"]
    assert (observed["status"], observed["prepared"]) == ("starting", False)
    assert (outcome.status, outcome.result) == ("finished", "win") and game.prepared.is_set()


def test_a_refused_start_ends_the_run_stopped_and_never_starts_sc2(tmp_path: Path) -> None:
    game = _GatedGame()

    def refuse(run_id: str) -> None:
        raise LaunchAborted("the dashboard did not show run x within 60 s; SC2 was not started")

    outcome = run_match(
        MatchOptions(), BUNDLE, run_root=tmp_path, launcher=game, on_recorded=refuse
    )
    assert (outcome.status, outcome.result, outcome.error) == ("stopped", None, None)
    assert outcome.exit_code == EXIT_FAILURE and "did not show run" in outcome.message
    assert not game.prepared.is_set()
    run_dir = tmp_path / outcome.run_id
    metadata = read_run_metadata(run_dir)
    assert metadata is not None and read_run_state(run_dir, metadata).status == "stopped"
    diagnostics = json.loads((run_dir / runner.DIAGNOSTICS_FILE).read_bytes())
    assert diagnostics["outcome"]["status"] == "stopped"


def test_ctrl_c_in_the_hook_stops_the_run_and_propagates(tmp_path: Path) -> None:
    game = _GatedGame()
    recorded: list[str] = []

    def interrupted(run_id: str) -> None:
        recorded.append(run_id)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_match(MatchOptions(), BUNDLE, run_root=tmp_path, launcher=game, on_recorded=interrupted)
    (run_id,) = recorded
    metadata = read_run_metadata(tmp_path / run_id)
    assert metadata is not None
    assert read_run_state(tmp_path / run_id, metadata).status == "stopped"
    assert not game.prepared.is_set()


def test_the_runner_cli_starts_sc2_only_after_the_page_acknowledged(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    writer = LaunchSessionWriter.create(launch_root_for(run_root))
    game = _GatedGame(launch_root_for(run_root), writer.session_id)
    page = _FakePage(run_root, writer.session_id)
    page.start()
    try:
        argv = ["--run-root", str(run_root), "--launch-session", writer.session_id]
        code = runner.main(argv, load_policy=load_policy, prog="jev", launcher=game)
    finally:
        page.stop()
    assert code == EXIT_OK
    (run_id,) = page.acked
    assert game.receipt is not None and game.receipt.matches(
        writer.session_id, run_id, BUNDLE.policy_hash
    )
    assert read_session(launch_root_for(run_root), writer.session_id).active_run_id == run_id


def test_the_runner_cli_refuses_a_session_it_cannot_join(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_root = tmp_path / "runs"
    game = _GatedGame()
    argv = ["--run-root", str(run_root), "--launch-session", uuid.uuid4().hex]
    assert runner.main(argv, load_policy=load_policy, prog="jev", launcher=game) == EXIT_FAILURE
    assert "launch_not_found" in capsys.readouterr().err
    assert not run_root.exists() and not game.prepared.is_set()  # nothing recorded or started
    with pytest.raises(SystemExit) as exited:
        runner.main(["--launch-session", "../x"], load_policy=load_policy, prog="jev")
    assert exited.value.code == 2


# ---------------------------------------------------------------------------
# Launcher side: real loopback servers on free ports
# ---------------------------------------------------------------------------


def test_the_launcher_reuses_a_healthy_dashboard_and_opens_the_exact_url_once(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "runs"
    opened: list[str] = []
    lines: list[str] = []
    with _serving(run_root) as server:
        assert dashboard_health(server.endpoints) is None

        def no_start() -> None:
            raise AssertionError("healthy servers are reused, never started again")

        def opener(url: str) -> None:
            opened.append(url)

        result = DashboardLaunch.open(
            run_root,
            case_count=6,
            environ={},
            endpoints=server.endpoints,
            start=no_start,
            opener=opener,
            out=lines.append,
        )
    assert opened == [f"{server.base}/?tab=jev&launch={result.session_id}"] == [result.url]
    assert any(result.url in line for line in lines)
    session = read_session(launch_root_for(run_root), result.session_id)
    assert (session.state, session.case_count) == ("preparing", 6)
    assert result.child_arguments() == ("--launch-session", result.session_id)


def test_cold_startup_starts_the_dashboard_once_and_waits_for_it(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    server = _Server(run_root)
    starts: list[bool] = []

    def start() -> None:
        starts.append(True)
        threading.Timer(1.5, server.start).start()  # servers take a while to answer

    try:
        assert dashboard_health(server.endpoints) is not None
        result = DashboardLaunch.open(
            run_root,
            case_count=1,
            environ={},
            endpoints=server.endpoints,
            start=start,
            opener=lambda url: None,
            out=lambda line: None,
        )
    finally:
        time.sleep(0.1)
        server.stop()
    assert starts == [True] and is_valid_run_id(result.session_id)


def test_a_dashboard_serving_another_data_root_is_refused_before_anything_opens(
    tmp_path: Path,
) -> None:
    served, ours = tmp_path / "other" / "runs", tmp_path / "ours" / "runs"
    opened: list[str] = []
    with _serving(served) as server, pytest.raises(DashboardUnavailable) as caught:
        DashboardLaunch.open(
            ours,
            case_count=1,
            environ={},
            endpoints=server.endpoints,
            start=_no_start,
            opener=lambda url: opened.append(url) or None,
            out=lambda line: None,
        )
    assert "another data root" in caught.value.message and opened == []
    (session_dir,) = launch_root_for(ours).iterdir()
    assert read_session(launch_root_for(ours), session_dir.name).state == "failed"


def test_occupied_ports_are_not_health_and_an_unhealthy_dashboard_times_out(
    tmp_path: Path,
) -> None:
    impostor = FastAPI()

    @impostor.get("/{anything:path}", response_class=HTMLResponse)
    def anything(anything: str) -> str:
        return '<div id="root"></div>'

    port = _free_port()
    config = uvicorn.Config(impostor, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started:  # a failed bind ends the thread: fail, never hang
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(0.05)
        endpoints = DashboardEndpoints(f"http://127.0.0.1:{port}", f"http://127.0.0.1:{port}")
        problem = dashboard_health(endpoints)
        assert problem is not None and "Jev run list" in problem
        clock = _Clock()
        starts: list[bool] = []

        def sleep(seconds: float) -> None:
            clock.now += seconds

        with pytest.raises(DashboardUnavailable, match="not healthy within 60 s"):
            launch.wait_for_dashboard(
                endpoints, start=lambda: starts.append(True), clock=clock, sleep=sleep
            )
        assert starts == [True]  # started once, never in a loop
    finally:
        server.should_exit = True
        thread.join(15)


def test_a_browser_that_cannot_open_prints_the_exact_url(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    lines: list[str] = []
    with _serving(run_root) as server:
        result = DashboardLaunch.open(
            run_root,
            case_count=1,
            environ={},
            endpoints=server.endpoints,
            start=_no_start,
            opener=lambda url: "no default browser",
            out=lines.append,
        )
    assert lines[0].endswith(result.url)
    assert "could not be opened (no default browser)" in lines[1] and "60 s" in lines[1]


def test_ui_services_and_the_browser_never_receive_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows upper-cases os.environ keys: SystemRoot is still found (never a guess).
    environ = {KEY: FAKE_KEY, "typesafe_api_key": FAKE_KEY, "PATH": "x", "SYSTEMROOT": "D:\\W"}
    assert scrubbed_environment(environ) == {"PATH": "x", "SYSTEMROOT": "D:\\W"}
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake(argv: list[str], **kwargs: Any) -> None:
        calls.append((argv, kwargs["env"]))

    url = "http://localhost:3000/?tab=jev&launch=x"
    monkeypatch.setattr(sys, "platform", "win32")  # before any call: on every host OS
    assert open_browser(url, environ, popen=fake) is None
    start_dashboard_servers(tmp_path, environ, run=fake)
    monkeypatch.setattr(sys, "platform", "linux")
    assert open_browser(url, environ, popen=fake) is None
    with pytest.raises(DashboardUnavailable, match="--no-dashboard"):
        start_dashboard_servers(tmp_path, environ, run=fake)
    (browser_argv, _), (server_argv, _), (linux_browser_argv, _) = calls
    for argv, env in calls:
        assert FAKE_KEY not in " ".join(argv) and FAKE_KEY not in env.values()
    assert browser_argv[-1] == url and linux_browser_argv == ["xdg-open", url]
    assert server_argv[0].startswith("D:\\W") and browser_argv[0].startswith("D:\\W")
    assert server_argv[-3:] == [
        str(tmp_path / "scripts" / "launch-a4g.ps1"),
        "-NoBrowser",
        "-NoWait",
    ]


class _FakeProcess:
    def __init__(self, on_wait: Callable[[], int]) -> None:
        self.on_wait = on_wait

    def wait(self, timeout: float | None = None) -> int:
        return self.on_wait()


def test_the_single_launcher_hands_the_key_only_to_the_game(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    writer = LaunchSessionWriter.create(launch_root_for(run_root))
    opened: list[dict[str, Any]] = []
    spawned: list[tuple[list[str], dict[str, str]]] = []

    def open_dashboard(root: Path, **kwargs: Any) -> DashboardLaunch:
        opened.append({"root": root, **kwargs})
        return DashboardLaunch(writer, "http://localhost:3000/?tab=jev&launch=x")

    def popen(argv: list[str], **kwargs: Any) -> _FakeProcess:
        spawned.append((argv, kwargs["env"]))

        def play() -> int:  # the game child: publishes its run, is released, plays
            run_id = uuid.uuid4().hex
            writer.publish("starting", active_run_id=run_id, message="")
            writer.publish("running", message="")
            return 0

        return _FakeProcess(play)

    environ = {KEY: FAKE_KEY, "PATH": os.environ.get("PATH", "")}
    argv = ["--version", "v1", "--run-root", str(run_root)]
    code = launch.main(argv, environ=environ, popen=popen, open_dashboard=open_dashboard)
    assert code == 0
    (opening,) = opened
    assert opening["root"] == run_root and KEY not in opening["environ"]
    (game_argv, game_env) = spawned[0]
    runner.build_parser("bots.jev.v1").parse_args(game_argv[3:])  # its consumer accepts it
    assert game_env[KEY] == FAKE_KEY and FAKE_KEY not in game_argv
    assert game_argv[1:3] == ["-m", "bots.jev.v1"] and "--realtime" in game_argv
    assert game_argv[-2:] == ["--launch-session", writer.session_id]
    for flag, value in (
        ("--decision-provider", "typesafe"),
        ("--difficulty", "3"),
        ("--seed", "11"),
        ("--decision-model", "jev-1.13.0"),
        ("--max-wall-seconds", "1200"),
    ):
        assert game_argv[game_argv.index(flag) + 1] == value
    assert read_session(launch_root_for(run_root), writer.session_id).state == "finished"


def test_the_single_launcher_stops_visibly_before_any_game(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("nothing may start")

    run_root = ["--run-root", str(tmp_path / "runs")]
    no_start = {"popen": forbidden, "open_dashboard": forbidden}
    assert launch.main(["--version", "v1", *run_root], environ={}, **no_start) == 1
    assert "TYPESAFE_API_KEY is required" in capsys.readouterr().err
    assert launch.main(["--version", "v9", *run_root], environ={KEY: FAKE_KEY}, **no_start) == 1
    assert "bots.jev.v9 is not packaged" in capsys.readouterr().err

    def unavailable(*args: Any, **kwargs: Any) -> DashboardLaunch:
        raise DashboardUnavailable("the dashboard was not healthy within 60 s: backend: down")

    code = launch.main(
        ["--version", "v1", "--decision-provider", "scripted", *run_root],
        environ={},
        popen=forbidden,
        open_dashboard=unavailable,
    )
    err = capsys.readouterr().err
    assert code == 1 and "dashboard_unavailable" in err and "no game was started" in err


def test_a_game_that_is_never_acknowledged_fails_the_single_launch(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    writer = LaunchSessionWriter.create(launch_root_for(run_root))

    def popen(argv: list[str], **kwargs: Any) -> _FakeProcess:
        def time_out() -> int:  # what the child's hook does at its deadline
            writer.publish("starting", active_run_id=uuid.uuid4().hex, message="")
            writer.publish("failed", message="the dashboard did not show run 1234 within 60 s")
            return EXIT_FAILURE

        return _FakeProcess(time_out)

    code = launch.main(
        ["--version", "v1", "--decision-provider", "scripted", "--run-root", str(run_root)],
        environ={},
        popen=popen,
        open_dashboard=lambda *a, **k: DashboardLaunch(writer, "u"),
    )
    session = read_session(launch_root_for(run_root), writer.session_id)
    assert code == EXIT_FAILURE and session.state == "failed" and "did not show" in session.message


# ---------------------------------------------------------------------------
# Benchmark batches: one session, followed case by case
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def snapshot_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    baselines = tmp_path_factory.mktemp("bench") / "baselines"
    return capture_source(REPO, "v1", baselines).directory or Path()


class _SessionChild:
    """The child process of one case, in process: ``runner.main`` itself (the
    production CLI, its ``--launch-session`` handling and ``launch_hook``) run on the
    exact argv the benchmark built, against a gated stand-in game. Only the barrier's
    deadline is shortened, through ``runner.launch_hook``; a hosted case's service is
    the mock transport of ``test_jev_benchmark``."""

    def __init__(self, run_root: Path, *, deadline: float = 30.0) -> None:
        self.run_root = run_root
        self.deadline = deadline
        self.games: list[_GatedGame] = []
        self.launches: list[ChildLaunch] = []

    def run(self, launch_spec: ChildLaunch) -> ChildExit:
        from test_jev_benchmark import _hosted_service

        self.launches.append(launch_spec)
        module = launch_spec.argv[2]
        args = runner.build_parser(module).parse_args(list(launch_spec.argv[3:]))
        hosted = args.decision_provider == "typesafe"
        game = _GatedGame(launch_root_for(args.run_root), args.launch_session, hosted=hosted)
        self.games.append(game)
        version = module.rsplit(".", 1)[1]
        package = launch_spec.cwd / "bots" / "jev" / version

        def load(path: Path | None) -> Any:
            return load_policy_bundle(package, expected_entrypoint=module)

        def short_hook(run_root: Path, session_id: str, policy_hash: str) -> Any:
            writer = LaunchSessionWriter.adopt(launch_root_for(run_root), session_id)
            return ready_hook(writer, policy_hash, deadline_seconds=self.deadline)

        out = io.StringIO()
        with pytest.MonkeyPatch.context() as patch, contextlib.ExitStack() as stack:
            patch.setattr(runner, "repository_root", lambda: launch_spec.cwd)
            patch.setattr(runner, "launch_hook", short_hook)
            if hosted:
                stack.enter_context(_hosted_service())
            else:
                patch.delenv(KEY, raising=False)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = runner.main(
                    list(launch_spec.argv[3:]), load_policy=load, prog=module, launcher=game
                )
        launch_spec.stdout_path.write_text(out.getvalue())
        return ChildExit(code, 0.5)


_CASES = tuple(
    CaseSpec("v1", "scripted", "Simple64", race, 2, 1, False) for race in benchmark.RACES
)


def _observer(run_root: Path, count: int) -> DashboardObserver:
    writer = LaunchSessionWriter.create(launch_root_for(run_root), case_count=count)
    url = f"http://localhost:3000/?tab=jev&launch={writer.session_id}"
    return DashboardObserver(DashboardLaunch(writer, url))


def test_a_batch_follows_one_session_and_starts_each_game_after_its_own_ack(
    snapshot_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every write of the session, by either owner, in order: no sampling.
    published: list[tuple[str, str | None, int]] = []
    write = LaunchSessionWriter._write

    def recording(self: LaunchSessionWriter, session: LaunchSession) -> None:
        published.append((session.state, session.active_run_id, session.case_index))
        write(self, session)

    monkeypatch.setattr(LaunchSessionWriter, "_write", recording)
    run_root = tmp_path / "runs"
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, cases=_CASES)
    observer = _observer(run_root, len(_CASES))
    page = _FakePage(run_root, observer.session_id)
    page.start()
    child = _SessionChild(run_root)
    try:
        outcome = run_batch(
            store, sources, launcher=child, run_root=run_root, environ={}, observer=observer
        )
    finally:
        page.stop()
        store.release()
    observer.finish(outcome, outcome.detail)
    assert outcome.status == "complete"
    run_ids = [case.run_id for case in outcome.cases]
    assert [case.result for case in outcome.cases] == ["win"] * 3
    assert page.acked == run_ids and page.indexes == [0, 1, 2]
    for game, run_id in zip(child.games, run_ids, strict=True):
        assert game.receipt is not None  # the page had acknowledged when SC2 "started"
        assert game.receipt.matches(observer.session_id, run_id or "", BUNDLE.policy_hash)
    assert all(
        launch_spec.argv[-2:] == ("--launch-session", observer.session_id)
        for launch_spec in child.launches
    )
    # The same session moved on (no new tab), exactly: the barrier's repeated
    # "starting" heartbeats collapse; each game starts from "preparing" with no run,
    # so the previous game's run is never shown as the next one.
    walk = [w for i, w in enumerate(published) if i == 0 or w[:2] != published[i - 1][:2]]
    assert [state for state, _, _ in walk] == (
        ["preparing", "starting", "running", "between_games"] * 2
        + ["preparing", "starting", "running", "finished"]
    )
    assert all(run is None for state, run, _ in walk if state == "preparing")
    assert [run for state, run, _ in walk if state == "starting"] == run_ids
    assert [index for state, _, index in walk if state == "starting"] == [0, 1, 2]
    session = read_session(launch_root_for(run_root), observer.session_id)
    assert (session.state, session.case_index, session.active_run_id) == (
        "finished",
        2,
        run_ids[-1],
    )


def test_an_unacknowledged_case_fails_its_launch_and_stops_the_batch(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    run_root = tmp_path / "runs"
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, cases=_CASES)
    observer = _observer(run_root, len(_CASES))
    page = _FakePage(run_root, observer.session_id, acknowledge=False)  # page never shows it
    page.start()
    child = _SessionChild(run_root, deadline=1.0)
    try:
        outcome = run_batch(
            store, sources, launcher=child, run_root=run_root, environ={}, observer=observer
        )
    finally:
        page.stop()
        store.release()
    observer.finish(outcome, outcome.detail)
    assert (outcome.status, outcome.stop_reason) == ("stopped", "launch_failed")
    first, *rest = outcome.cases
    assert (first.status, first.reason, first.result) == ("invalid", "launch_failed", None)
    assert first.detail is not None and "did not show run" in first.detail
    assert [case.status for case in rest] == ["pending", "pending"]  # nothing else launched
    (game,) = child.games
    assert not game.prepared.is_set()  # SC2 never started
    assert first.run_id is not None
    metadata = read_run_metadata(run_root / first.run_id)
    assert metadata is not None
    assert read_run_state(run_root / first.run_id, metadata).status == "stopped"
    session = read_session(launch_root_for(run_root), observer.session_id)
    assert session.state == "failed" and "did not show run" in session.message
    events = [json.loads(line)["event"] for line in (store.batch_dir / "attempts.jsonl").open()]
    assert "launch_failed" in events


def test_the_cli_opens_the_dashboard_before_probing_capturing_or_playing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bench, runs = tmp_path / "bench", tmp_path / "runs"
    events: list[Any] = []

    def provider_factory(key: str, config: Any) -> Any:
        events.append("probe")
        raise AssertionError("the probe must not run before the dashboard is open")

    def dashboard(count: int) -> Any:
        events.append(("dashboard", count))
        raise DashboardUnavailable("the dashboard was not healthy within 60 s: backend: down")

    def no_child(launch_spec: ChildLaunch) -> ChildExit:
        raise AssertionError("nothing may be played")

    code = benchmark.main(
        ["--panel", "baseline", "--benchmark-root", str(bench), "--run-root", str(runs)],
        environ={KEY: FAKE_KEY},
        launcher=SimpleNamespace(run=no_child),
        provider_factory=provider_factory,
        dashboard=dashboard,
    )
    err = capsys.readouterr().err
    assert code == 1 and events == [("dashboard", 6)]
    assert "dashboard_unavailable" in err and "--no-dashboard runs headless" in err
    assert not bench.exists()  # nothing captured, nothing finalized, never headless instead


def test_a_failed_preflight_ends_the_open_launch_session_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from jev.decision import TypesafeProvider
    from test_jev_benchmark import FAKE_KEY as SERVICE_KEY
    from test_jev_benchmark import _service

    runs = tmp_path / "runs"
    opened: list[DashboardObserver] = []

    def dashboard(count: int) -> DashboardObserver:
        opened.append(_observer(runs, count))
        return opened[-1]

    def drifted(key: str, config: Any) -> TypesafeProvider:
        return TypesafeProvider(key, config, transport=_service("jev-latest-alias"))

    code = benchmark.main(
        [
            "--panel",
            "baseline",
            "--benchmark-root",
            str(tmp_path / "bench"),
            "--run-root",
            str(runs),
        ],
        environ={KEY: SERVICE_KEY},
        launcher=SimpleNamespace(run=lambda launch_spec: None),
        provider_factory=drifted,
        dashboard=dashboard,
    )
    assert code == 1 and "model_drift" in capsys.readouterr().err
    (observer,) = opened
    session = read_session(launch_root_for(runs), observer.session_id)
    assert session.state == "failed" and session.message.startswith("model_drift")
    assert not (tmp_path / "bench").exists()  # the page shows why; nothing was frozen


def test_the_cli_plays_a_panel_dashboard_first_end_to_end(tmp_path: Path) -> None:
    bench, runs = tmp_path / "bench", tmp_path / "runs"
    pages: list[_FakePage] = []
    opened: list[str] = []

    def opener(url: str) -> None:  # the browser: one page following the session URL
        opened.append(url)
        page = _FakePage(runs, url.rsplit("launch=", 1)[1])
        page.start()
        pages.append(page)

    with _serving(runs) as server:

        def dashboard(count: int) -> DashboardObserver:
            opened_launch = DashboardLaunch.open(
                runs,
                case_count=count,
                environ={},
                endpoints=server.endpoints,
                start=_no_start,
                opener=opener,
                out=lambda line: None,
            )
            return DashboardObserver(opened_launch)

        child = _SessionChild(runs)
        try:
            code = benchmark.main(
                ["--panel", "staging", "--benchmark-root", str(bench), "--run-root", str(runs)],
                environ={},
                launcher=child,
                dashboard=dashboard,
            )
        finally:
            for page in pages:
                page.stop()
    assert code == 0 and len(opened) == 1 and len(pages) == 1
    (game,) = child.games
    (run_id,) = pages[0].acked
    assert game.receipt is not None and game.receipt.run_id == run_id
    session = read_session(launch_root_for(runs), pages[0].session_id)
    assert (session.state, session.active_run_id) == ("finished", run_id)


def test_no_dashboard_is_explicit_and_only_for_runs(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exited:
        benchmark.main(["--report", uuid.uuid4().hex, "--no-dashboard"])
    assert exited.value.code == 2


def test_the_frozen_v1_baseline_carries_the_launch_hook(snapshot_dir: Path) -> None:
    paths = runtime_source_paths(REPO, "v1")
    assert {"src/jev/launch.py", "src/jev/runner.py"} <= set(paths)
    frozen_runner = (snapshot_dir / "src" / "jev" / "runner.py").read_text(encoding="utf-8")
    assert "on_recorded" in frozen_runner and "--launch-session" in frozen_runner
    frozen_launch = (snapshot_dir / "src" / "jev" / "launch.py").read_bytes()
    assert frozen_launch == (REPO / "src" / "jev" / "launch.py").read_bytes()
    assert benchmark.OFFICIAL_PANEL_PLAY_ENABLED is True  # the baseline may now be finalized


# ---------------------------------------------------------------------------
# One vocabulary across the backend and the dashboard; the PowerShell launchers
# ---------------------------------------------------------------------------


def _ts_list(text: str, name: str) -> tuple[str, ...]:
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", text, re.DOTALL)
    assert match is not None, name
    return tuple(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_the_dashboard_launch_vocabulary_matches_the_backend() -> None:
    text = (REPO / "frontend" / "src" / "types" / "jev.ts").read_text(encoding="utf-8")
    assert _ts_list(text, "LAUNCH_STATES") == LAUNCH_STATES
    assert _ts_list(text, "LAUNCH_ERROR_CODES") == LAUNCH_ERROR_CODES
    block = re.search(r"JEV_LAUNCH_LIMITS = \{(.*?)\} as const;", text, re.DOTALL)
    assert block is not None
    limits = dict(re.findall(r"(\w+): (\d+)", block[1]))
    assert {name: int(value) for name, value in limits.items()} == {
        "message": MAX_LAUNCH_MESSAGE_CHARS,
        "cases": launch.MAX_CASE_COUNT,
        "startingSilenceSeconds": launch.STARTING_SILENCE_SECONDS,
        "launchSilenceSeconds": launch.LAUNCH_SILENCE_SECONDS,
        "firstObservationSilenceSeconds": launch.FIRST_OBSERVATION_SILENCE_SECONDS,
    }
    # SC2's own launch fits: the barrier, then burnysc2's 180 one-second connect tries.
    assert launch.FIRST_OBSERVATION_SILENCE_SECONDS >= launch.RUN_READY_SECONDS + 180 + 30
    # The page's "starting" bound leaves room for missed barrier heartbeats.
    assert launch.STARTING_SILENCE_SECONDS >= 3 * launch.LAUNCH_HEARTBEAT_SECONDS


_PS_SCRIPTS = ("launch-jev.ps1", "launch-a4g.ps1", "launch-evolve.ps1")


def _parse_errors(script: Path) -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    command = (
        "$t = $null; $e = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        "$env:JEV_PS_SCRIPT, [ref]$t, [ref]$e); $e.Count"
    )
    proc = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
        env={**os.environ, "JEV_PS_SCRIPT": str(script)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return proc.stdout.strip()


@pytest.mark.parametrize("name", _PS_SCRIPTS)
def test_the_powershell_launchers_are_ascii_and_parse(name: str, tmp_path: Path) -> None:
    script = REPO / "scripts" / name
    assert script.read_bytes().isascii()  # PS 5.1 reads a no-BOM script as cp1252
    if sys.platform != "win32":
        pytest.skip("the Windows PowerShell parser is only on Windows")
    garbage = tmp_path / "garbage.ps1"
    garbage.write_text("function { $x = ", encoding="ascii")
    assert _parse_errors(garbage) not in ("0", "")  # the check can fail (red anchor)
    assert _parse_errors(script) == "0"


def test_launch_a4g_gains_reuse_switches_and_keeps_its_default_behavior() -> None:
    text = (REPO / "scripts" / "launch-a4g.ps1").read_text(encoding="ascii")
    assert "[switch]$NoBrowser" in text and "[switch]$NoWait" in text
    default_tail = text[text.index("if ($NoWait) {\n    # The caller") :]
    assert "Start-Process $url" in default_tail and "Read-Host" in default_tail
    assert "-WindowStyle Hidden" in text and "'-NoExit', '-Command', $Command" in text
    evolve = (REPO / "scripts" / "launch-evolve.ps1").read_text(encoding="ascii")
    assert "& (Join-Path $PSScriptRoot 'launch-a4g.ps1') -Tab evolution\n" in evolve.replace(
        "\r\n", "\n"
    )


def test_launch_jev_keeps_the_key_out_of_arguments_and_ui_services() -> None:
    text = (REPO / "scripts" / "launch-jev.ps1").read_text(encoding="ascii").replace("\r\n", "\n")
    for default in (
        "[string]$Version = 'v2'",
        "[string]$DecisionProvider = 'typesafe'",
        "[int]$Difficulty = 3",
        "[long]$Seed = 11",
    ):
        assert default in text
    arguments = text[text.index("$launchArgs = @(") : text.index(")\n\n$code = 1")]
    assert "Key" not in arguments and "key" not in arguments
    removed = text.index("SetEnvironmentVariable($keyName, $null, 'Process')")
    assert removed < text.index("& uv @launchArgs")  # removed before anything starts
    assert "Write-Host $gameKey" not in text and "Write-Output $gameKey" not in text
    # The operator's own process key (if any) is restored when the script ends.
    finally_block = text[text.index("} finally {") :]
    assert "SetEnvironmentVariable($keyName, $originalKey, 'Process')" in finally_block


# ---------------------------------------------------------------------------
# Clean cancellation, heartbeats and ended sessions (review iteration 2)
# ---------------------------------------------------------------------------


class _InterruptFirstWrite(EvidenceFiles):
    """Disk operations where the first write is interrupted by Ctrl+C."""

    def __init__(self) -> None:
        super().__init__()
        self.writes = 0

    def write(self, path: Path, data: bytes) -> None:
        self.writes += 1
        if self.writes == 1:
            raise KeyboardInterrupt
        super().write(path, data)


def test_ctrl_c_during_the_first_publish_still_stops_the_session(tmp_path: Path) -> None:
    root = tmp_path / "launches"
    created = LaunchSessionWriter.create(root)
    writer = LaunchSessionWriter.adopt(root, created.session_id, files=_InterruptFirstWrite())
    hook = ready_hook(writer, BUNDLE.policy_hash)
    with pytest.raises(KeyboardInterrupt):
        hook(uuid.uuid4().hex)
    assert read_session(root, created.session_id).state == "stopped"


def test_the_barrier_heartbeats_while_it_waits(tmp_path: Path) -> None:
    root = tmp_path / "launches"
    clock = _Clock()
    created = LaunchSessionWriter.create(root)
    writer = LaunchSessionWriter.adopt(
        root, created.session_id, wall_time=lambda: 1_790_000_000 + clock.now
    )
    run_id = uuid.uuid4().hex
    stamps: list[str] = []

    def sleep(seconds: float) -> None:
        clock.now += seconds
        stamps.append(read_session(root, created.session_id).updated_at)
        if clock.now >= 12:  # the page acknowledges after 12 s
            _write_ready(root, created.session_id, run_id, BUNDLE.policy_hash)

    ready_hook(writer, BUNDLE.policy_hash, clock=clock, sleep=sleep)(run_id)
    assert len(set(stamps)) >= 3  # first publish + a heartbeat every 5 s
    assert read_session(root, created.session_id).state == "running"


def test_a_session_ended_elsewhere_aborts_the_barrier_at_once_and_is_kept(
    tmp_path: Path,
) -> None:
    root = tmp_path / "launches"
    writer = LaunchSessionWriter.create(root)
    other = LaunchSessionWriter.adopt(root, writer.session_id)
    looks: list[float] = []

    def sleep(seconds: float) -> None:
        looks.append(seconds)
        assert len(looks) == 1, "the barrier kept waiting on an ended session"
        other.end("stopped", "the operator stopped the batch")

    hook = ready_hook(writer, BUNDLE.policy_hash, clock=_Clock(), sleep=sleep)
    with pytest.raises(LaunchAborted, match="stopped by another process"):
        hook(uuid.uuid4().hex)
    session = read_session(root, writer.session_id)
    assert (session.state, session.message) == ("stopped", "the operator stopped the batch")


def test_an_ended_session_is_never_reopened(tmp_path: Path) -> None:
    root = tmp_path / "launches"
    writer = LaunchSessionWriter.create(root)
    writer.end("failed", "no acknowledgment")
    with pytest.raises(LaunchError) as caught:
        writer.publish("starting", active_run_id=uuid.uuid4().hex, message="")
    assert caught.value.code == "launch_not_ready"
    assert writer.end("stopped", "later").state == "failed"


def test_the_timeout_names_the_last_problem_it_saw(tmp_path: Path) -> None:
    root = tmp_path / "launches"
    writer = LaunchSessionWriter.create(root)
    (root / writer.session_id / "ready.json").write_bytes(b"{corrupt")
    clock = _Clock()

    def sleep(seconds: float) -> None:
        clock.now += seconds

    hook = ready_hook(writer, BUNDLE.policy_hash, deadline_seconds=2, clock=clock, sleep=sleep)
    with pytest.raises(LaunchAborted, match="last problem: corrupt_launch"):
        hook(uuid.uuid4().hex)
    assert "corrupt_launch" in read_session(root, writer.session_id).message


def test_ctrl_c_before_the_run_is_recorded_stops_the_childs_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "runs"
    writer = LaunchSessionWriter.create(launch_root_for(run_root))

    def interrupted(self: RunRecorder, policy_bytes: bytes) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner.RunRecorder, "start", interrupted)
    game = _GatedGame()
    argv = ["--run-root", str(run_root), "--launch-session", writer.session_id]
    with pytest.raises(KeyboardInterrupt):
        runner.main(argv, load_policy=load_policy, prog="jev", launcher=game)
    session = read_session(launch_root_for(run_root), writer.session_id)
    assert (session.state, session.message) == ("stopped", "stopped with Ctrl+C")
    assert not game.prepared.is_set()


class _InterruptedLaunch(_GatedGame):
    """SC2 was released, then Ctrl+C arrives while it launches."""

    def prepare(self, options: MatchOptions) -> object:
        super().prepare(options)
        raise KeyboardInterrupt


def test_ctrl_c_after_the_release_stops_the_session_never_finishes_it(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    writer = LaunchSessionWriter.create(launch_root_for(run_root))
    game = _InterruptedLaunch(launch_root_for(run_root), writer.session_id)
    page = _FakePage(run_root, writer.session_id)
    page.start()
    try:
        argv = ["--run-root", str(run_root), "--launch-session", writer.session_id]
        with pytest.raises(KeyboardInterrupt):
            runner.main(argv, load_policy=load_policy, prog="jev", launcher=game)
    finally:
        page.stop()
    (run_id,) = page.acked
    assert game.receipt is not None and game.receipt.run_id == run_id
    assert read_session(launch_root_for(run_root), writer.session_id).state == "stopped"
    metadata = read_run_metadata(run_root / run_id)
    assert metadata is not None
    assert read_run_state(run_root / run_id, metadata).status == "stopped"


def test_ctrl_c_while_the_dashboard_opens_ends_its_new_session_stopped(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"

    def operator_presses_ctrl_c(url: str) -> None:
        raise KeyboardInterrupt

    with _serving(run_root) as server, pytest.raises(KeyboardInterrupt):
        DashboardLaunch.open(
            run_root,
            case_count=1,
            environ={},
            endpoints=server.endpoints,
            start=_no_start,
            opener=operator_presses_ctrl_c,
            out=lambda line: None,
        )
    (session_dir,) = launch_root_for(run_root).iterdir()
    session = read_session(launch_root_for(run_root), session_dir.name)
    assert (session.state, session.message) == ("stopped", "stopped before the first game started")


def test_the_single_launcher_ends_stopped_on_ctrl_c_before_or_during_the_game(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_root = tmp_path / "runs"
    scripted = ["--version", "v1", "--decision-provider", "scripted", "--run-root", str(run_root)]

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    assert launch.main(scripted, environ={}, open_dashboard=interrupted) == runner.EXIT_STOPPED
    writer = LaunchSessionWriter.create(launch_root_for(run_root))
    code = launch.main(
        scripted,
        environ={},
        popen=interrupted,
        open_dashboard=lambda *a, **k: DashboardLaunch(writer, "u"),
    )
    assert code == runner.EXIT_STOPPED
    assert read_session(launch_root_for(run_root), writer.session_id).state == "stopped"

    # Ctrl+C during the match: the game leaves cleanly; that is never a "finish".
    playing = LaunchSessionWriter.create(launch_root_for(run_root))
    presses: list[bool] = []

    def wait_with_ctrl_c() -> int:
        if not presses:
            playing.publish("starting", active_run_id=uuid.uuid4().hex, message="")
            playing.publish("running", message="")
            presses.append(True)
            raise KeyboardInterrupt
        return runner.EXIT_STOPPED

    code = launch.main(
        scripted,
        environ={},
        popen=lambda *a, **k: _FakeProcess(wait_with_ctrl_c),
        open_dashboard=lambda *a, **k: DashboardLaunch(playing, "u"),
    )
    session = read_session(launch_root_for(run_root), playing.session_id)
    assert code == runner.EXIT_STOPPED and session.state == "stopped"
    assert "Ctrl+C" in session.message


def test_a_game_that_dies_before_recording_its_run_fails_the_launch(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    writer = LaunchSessionWriter.create(launch_root_for(run_root))
    code = launch.main(
        ["--version", "v1", "--decision-provider", "scripted", "--run-root", str(run_root)],
        environ={},
        popen=lambda *a, **k: _FakeProcess(lambda: EXIT_FAILURE),
        open_dashboard=lambda *a, **k: DashboardLaunch(writer, "u"),
    )
    session = read_session(launch_root_for(run_root), writer.session_id)
    assert code == EXIT_FAILURE and session.state == "failed"
    assert "before its run started" in session.message  # never left on Preparing


def test_a_game_that_dies_at_the_barrier_is_a_launch_failure(tmp_path: Path) -> None:
    writer = LaunchSessionWriter.create(tmp_path / "launches")
    dash = DashboardLaunch(writer, "u")
    dash.before_case(0, 2)
    assert dash.launch_failure() is None  # the game never got as far as its run
    writer.publish("starting", active_run_id=uuid.uuid4().hex, message="")  # then it died
    assert dash.launch_failure() == (
        "the game process ended before the dashboard acknowledged its run"
    )


def test_the_next_game_never_shows_the_previous_games_run(tmp_path: Path) -> None:
    writer = LaunchSessionWriter.create(tmp_path / "launches", case_count=2)
    dash = DashboardLaunch(writer, "u")
    writer.publish("starting", active_run_id=uuid.uuid4().hex, message="")
    writer.publish("running", message="")
    dash.after_case(0, 2, "game 1 of 2: complete win")
    assert writer.refresh().state == "between_games"
    dash.before_case(1, 2)
    session = writer.refresh()
    assert (session.state, session.active_run_id, session.case_index) == ("preparing", None, 1)


# ---------------------------------------------------------------------------
# One owner for the match defaults; the observer's endings; resumes and retries
# ---------------------------------------------------------------------------


def _code_constants(source: Path) -> set[object]:
    """Every literal in ``source`` outside docstrings."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and id(node) not in docstrings
    }


def test_the_single_launcher_takes_its_match_defaults_from_their_owner() -> None:
    defaults = vars(launch.build_parser().parse_args([]))
    ceilings = benchmark.LIMIT_CEILINGS
    assert (
        defaults["max_game_seconds"],
        defaults["max_wall_seconds"],
        defaults["decision_max_requests"],
    ) == (
        ceilings["match_game_seconds"],
        ceilings["match_wall_seconds"],
        ceilings["match_requests"],
    )
    assert (defaults["decision_model"], defaults["map"]) == (benchmark.PINNED_MODEL, benchmark.MAP)
    # Structurally: launch.py restates none of the owner's values, and has no KEY_ENV.
    constants = _code_constants(REPO / "src" / "jev" / "launch.py")
    owned = {
        benchmark.KEY_ENV,
        benchmark.PINNED_MODEL,
        benchmark.MAP,
        ceilings["match_game_seconds"],
        ceilings["match_wall_seconds"],
        ceilings["match_requests"],
    }
    assert not owned & constants
    assert not hasattr(launch, "KEY_ENV")
    assert launch.scrubbed_environment({benchmark.KEY_ENV: "x", "PATH": "y"}) == {"PATH": "y"}
    for name in ("--launch-session", "http://localhost:3000"):
        assert sum(1 for c in constants if c == name) == 1  # one definition each
    assert launch.DEFAULT_LAUNCH_ORIGINS == {launch.DASHBOARD_URL, "http://127.0.0.1:3000"}


@pytest.mark.parametrize(
    ("status", "detail", "expected"),
    [
        (None, "model_drift: the probe answered as another model", "failed"),
        ("complete", None, "finished"),
        ("incomplete", "1 case(s) not complete", "finished"),
        ("stopped", "corrupt_evidence: x", "failed"),
        ("interrupted", "Ctrl+C during v1-typesafe-simple64-terran-3-11", "stopped"),
        ("budget_exhausted", "6 of 6 games", "stopped"),
    ],
)
def test_the_observer_ends_the_session_by_how_the_invocation_ended(
    tmp_path: Path, status: str | None, detail: str | None, expected: str
) -> None:
    observer = _observer(tmp_path / "runs", 6)
    outcome = (
        None if status is None else benchmark.BatchOutcome(status, None, detail, 0, ())  # type: ignore[arg-type]
    )
    observer.finish(outcome, detail)
    session = read_session(launch_root_for(tmp_path / "runs"), observer.session_id)
    assert session.state == expected
    if detail is not None:
        assert detail in session.message
    observer.finish(benchmark.BatchOutcome("complete", None, None, 0, ()), None)
    assert read_session(launch_root_for(tmp_path / "runs"), observer.session_id).state == expected


def test_a_launch_failed_case_stays_labeled_until_an_explicit_retry_replays_it(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    from test_jev_benchmark import _Probe

    run_root = tmp_path / "runs"
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, cases=_CASES[:2])
    batch_id = store.manifest.batch_id

    def invoke(*, acknowledge: bool, retry: bool) -> benchmark.BatchOutcome:
        batch = (
            store
            if not invocations
            else BatchStore.open(tmp_path / "bench", batch_id, probe=_Probe(alive=False))
        )
        observer = _observer(run_root, 2)
        page = _FakePage(run_root, observer.session_id, acknowledge=acknowledge)
        page.start()
        child = _SessionChild(run_root, deadline=1.0)
        try:
            outcome = run_batch(
                batch,
                sources,
                launcher=child,
                run_root=run_root,
                environ={},
                observer=observer,
                retry_interrupted=retry,
            )
        finally:
            page.stop()
            batch.release()
        invocations.append(child)
        return outcome

    invocations: list[_SessionChild] = []
    first = invoke(acknowledge=False, retry=False)
    assert (first.status, first.stop_reason) == ("stopped", "launch_failed")
    assert benchmark.RETRYABLE_REASONS == {"interrupted", "launch_failed"}
    plain = invoke(acknowledge=True, retry=False)  # labeled, not replayed
    assert [c.status for c in plain.cases] == ["invalid", "complete"]
    assert plain.cases[0].reason == "launch_failed" and len(invocations[1].games) == 1
    retried = invoke(acknowledge=True, retry=True)
    assert retried.status == "complete" and [c.result for c in retried.cases] == ["win", "win"]
    assert retried.cases[0].attempts == 2 and len(invocations[2].games) == 1


class _RecordingChild:
    """Records each launch; the child exits at once without output."""

    def __init__(self) -> None:
        self.launches: list[ChildLaunch] = []

    def run(self, launch_spec: ChildLaunch) -> ChildExit:
        self.launches.append(launch_spec)
        return ChildExit(1, 0.1)


@pytest.mark.parametrize("dashboard", [True, False])
def test_the_rendered_ready_barrier_is_added_to_the_childs_hard_wall(
    snapshot_dir: Path, tmp_path: Path, dashboard: bool
) -> None:
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, cases=_CASES[:1])
    child = _RecordingChild()
    observer = _observer(tmp_path / "runs", 1) if dashboard else None
    try:
        run_batch(
            store,
            sources,
            launcher=child,
            run_root=tmp_path / "runs",
            environ={},
            observer=observer,
        )
    finally:
        store.release()
    (launched,) = child.launches
    wall = store.manifest.limits.match_wall_seconds
    expected = wall + launch.RUN_READY_SECONDS if dashboard else wall
    assert launched.hard_wall_seconds == expected


def test_a_resumed_batch_reopens_the_dashboard_first_with_its_true_case_positions(
    tmp_path: Path,
) -> None:
    from jev.decision import TypesafeProvider
    from test_jev_benchmark import FAKE_KEY as SERVICE_KEY
    from test_jev_benchmark import _service

    bench, runs = tmp_path / "bench", tmp_path / "runs"
    events: list[str] = []
    pages: list[_FakePage] = []

    def opener(url: str) -> None:
        page = _FakePage(runs, url.rsplit("launch=", 1)[1])
        page.start()
        pages.append(page)

    def provider_factory(key: str, config: Any) -> TypesafeProvider:
        events.append("probe")
        return TypesafeProvider(key, config, transport=_service())

    child = _SessionChild(runs)
    common = ["--benchmark-root", str(bench), "--run-root", str(runs)]
    with _serving(runs) as server:

        def dashboard(count: int) -> DashboardObserver:
            events.append(f"dashboard:{count}")
            opened = DashboardLaunch.open(
                runs,
                case_count=count,
                environ={},
                endpoints=server.endpoints,
                start=_no_start,
                opener=opener,
                out=lambda line: None,
            )
            return DashboardObserver(opened)

        try:
            first = benchmark.main(
                ["--panel", "baseline", "--max-games", "1", *common],
                environ={KEY: SERVICE_KEY},
                launcher=child,
                provider_factory=provider_factory,
                dashboard=dashboard,
            )
            (batch_dir,) = [p for p in bench.iterdir() if is_valid_run_id(p.name)]
            resumed = benchmark.main(
                ["--resume", batch_dir.name, *common],
                environ={KEY: SERVICE_KEY},
                launcher=child,
                provider_factory=provider_factory,
                dashboard=dashboard,
            )
        finally:
            for page in pages:
                page.stop()
    assert (first, resumed) == (3, 3)  # one game each, then the invocation budget
    assert events == ["dashboard:6", "probe", "dashboard:6", "probe"]  # dashboard first
    assert len(pages) == 2 and pages[0].session_id != pages[1].session_id
    assert (pages[0].indexes, pages[1].indexes) == ([0], [1])  # the resume plays game 2 of 6
    assert child.launches[1].argv[-2:] == ("--launch-session", pages[1].session_id)
    for page in pages:
        session = read_session(launch_root_for(runs), page.session_id)
        assert session.state == "stopped" and "budget_exhausted" in session.message
    results = json.loads((batch_dir / "results.json").read_text())
    assert [c["status"] for c in results["cases"]] == ["complete"] * 2 + ["pending"] * 4


def test_a_resume_whose_dashboard_is_unavailable_plays_nothing(
    snapshot_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = tmp_path / "bench" / "baselines" / snapshot_dir.name  # where a resume looks
    shutil.copytree(snapshot_dir, snapshot)
    store = _store(tmp_path, _sources(snapshot))  # staging: one scripted case
    store.release()

    def unavailable(count: int) -> DashboardObserver:
        raise DashboardUnavailable("the dashboard was not healthy within 60 s: backend: down")

    def no_child(launch_spec: ChildLaunch) -> ChildExit:
        raise AssertionError("nothing may be played")

    code = benchmark.main(
        ["--resume", store.manifest.batch_id, "--benchmark-root", str(tmp_path / "bench")],
        environ={},
        launcher=SimpleNamespace(run=no_child),
        dashboard=unavailable,
    )
    assert code == 1 and "dashboard_unavailable" in capsys.readouterr().err
    attempts = (store.batch_dir / benchmark.ATTEMPTS_FILE).read_text()
    assert '"reason":"dashboard_unavailable"' in attempts.replace(" ", "")
    assert not (store.batch_dir / benchmark.LOCK_FILE).exists()  # released, resumable


# ---------------------------------------------------------------------------
# Review iteration 3: Ctrl+C before the first game, provable launch failures,
# one hard-wall bound, patient session verification
# ---------------------------------------------------------------------------


def test_ctrl_c_after_the_dashboard_opened_but_before_any_game_ends_stopped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = tmp_path / "runs"
    opened: list[DashboardObserver] = []

    def dashboard(count: int) -> DashboardObserver:
        opened.append(_observer(runs, count))
        return opened[-1]

    def operator_presses_ctrl_c(key: str, config: Any) -> Any:
        raise KeyboardInterrupt  # during the probe: the tab is open, no game yet

    def no_child(launch_spec: ChildLaunch) -> ChildExit:
        raise AssertionError("nothing may be played")

    code = benchmark.main(
        [
            "--panel",
            "baseline",
            "--benchmark-root",
            str(tmp_path / "bench"),
            "--run-root",
            str(runs),
        ],
        environ={KEY: FAKE_KEY},
        launcher=SimpleNamespace(run=no_child),
        provider_factory=operator_presses_ctrl_c,
        dashboard=dashboard,
    )
    assert code == runner.EXIT_STOPPED and "interrupted" in capsys.readouterr().err
    (observer,) = opened
    session = read_session(launch_root_for(runs), observer.session_id)
    assert (session.state, session.message) == (
        "stopped",
        "interrupted with Ctrl+C; no game was running",
    )
    other = _observer(runs, 1)  # any other ending before an outcome is a failure
    other.finish(None, "lock_held: another process")
    assert read_session(launch_root_for(runs), other.session_id).state == "failed"


class _CorruptingChild:
    """A child that leaves the session unreadable and exits without reporting a run."""

    def __init__(self, launches: Path, session_id: str) -> None:
        self.path = launches / session_id / "session.json"

    def run(self, launch_spec: ChildLaunch) -> ChildExit:
        self.path.write_bytes(b"{not json")
        return ChildExit(1, 0.1)


def test_an_unreadable_session_is_never_counted_as_a_launch_failure(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    run_root = tmp_path / "runs"
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, cases=_CASES[:1])
    observer = _observer(run_root, 1)
    child = _CorruptingChild(launch_root_for(run_root), observer.session_id)
    try:
        outcome = run_batch(
            store, sources, launcher=child, run_root=run_root, environ={}, observer=observer
        )
    finally:
        store.release()
    (case,) = outcome.cases
    # Nothing proves the barrier never released: scored the usual, conservative way.
    assert (case.status, case.reason) == ("invalid", "infrastructure_failure")
    assert case.reason not in benchmark.RETRYABLE_REASONS
    assert observer.launch.launch_failure() is None


@pytest.mark.parametrize("dashboard", [True, False])
def test_the_dry_run_and_the_real_run_bound_the_child_alike(
    snapshot_dir: Path, tmp_path: Path, dashboard: bool
) -> None:
    sources = _sources(snapshot_dir)
    limits = benchmark.BenchmarkLimits()
    plan = benchmark.dry_run_plan(
        "staging",
        sources,
        limits,
        run_root=tmp_path / "runs",
        benchmark_root=tmp_path / "bench",
        python="python",
        dashboard=dashboard,
    )
    (planned,) = plan["cases"]  # type: ignore[misc]
    store = _store(tmp_path, sources)
    child = _RecordingChild()
    observer = _observer(tmp_path / "runs", 1) if dashboard else None
    try:
        run_batch(
            store,
            sources,
            launcher=child,
            run_root=tmp_path / "runs",
            environ={},
            observer=observer,
        )
    finally:
        store.release()
    (launched,) = child.launches
    expected = benchmark.child_hard_wall_seconds(limits, dashboard=dashboard)
    assert planned["hard_wall_seconds"] == launched.hard_wall_seconds == expected  # type: ignore[index]


def test_session_verification_waits_out_a_slow_server_but_refuses_a_wrong_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoints = DashboardEndpoints("http://127.0.0.1:1", "http://127.0.0.1:2")
    sid = uuid.uuid4().hex
    calls: list[str] = []
    stalls = [2]  # the backend misses two probes (stalled), then answers

    def http_get(url: str, timeout: float = 3.0) -> tuple[int, bytes]:
        calls.append(url)
        if stalls[0] > 0:
            stalls[0] -= 1
            raise TimeoutError("timed out")
        return 200, json.dumps({"session_id": sid}).encode()

    clock = _Clock()

    def sleep(seconds: float) -> None:
        clock.now += seconds

    monkeypatch.setattr(launch, "_http_get", http_get)
    assert launch.verify_session_served(endpoints, sid, clock=clock, sleep=sleep) is None
    assert len(calls) == 4  # two misses and one answer from the backend, one from the proxy

    def never(url: str, timeout: float = 3.0) -> tuple[int, bytes]:
        calls.append(url)
        raise TimeoutError("timed out")

    monkeypatch.setattr(launch, "_http_get", never)
    calls.clear()
    clock.now = 0.0
    problem = launch.verify_session_served(endpoints, sid, clock=clock, sleep=sleep)
    assert problem is not None and "does not answer" in problem
    assert clock.now >= launch.SERVER_READY_SECONDS  # retried until the readiness deadline

    def wrong_root(url: str, timeout: float = 3.0) -> tuple[int, bytes]:
        calls.append(url)
        return 404, b'{"schema_version": 1, "error": {"code": "launch_not_found"}}'

    monkeypatch.setattr(launch, "_http_get", wrong_root)
    calls.clear()
    problem = launch.verify_session_served(endpoints, sid, clock=clock, sleep=sleep)
    assert problem is not None and "another data root" in problem
    assert len(calls) == 1  # a real answer is never retried: refused at once


# ---------------------------------------------------------------------------
# The real browser (opt-in): a production frontend build, Chromium, real HTTP
# ---------------------------------------------------------------------------


def _browser_tests_enabled() -> bool:
    return os.environ.get("JEV_BROWSER_TESTS") == "1"


_BROWSER_SKIP = "real-browser tests are opt-in: set JEV_BROWSER_TESTS=1 (see the module docstring)"


@pytest.fixture(scope="module")
def built_dashboard(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A production build of this checkout's frontend (never a stale dist folder)."""
    if not _browser_tests_enabled():
        pytest.skip(_BROWSER_SKIP)
    node = shutil.which("node")
    vite = REPO / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    if node is None or not vite.is_file():
        pytest.skip("Node and the frontend dependencies (npm --prefix frontend ci) are needed")
    out = tmp_path_factory.mktemp("dashboard-dist")
    proc = subprocess.run(
        [node, str(vite), "build", "--outDir", str(out), "--emptyOutDir", "--logLevel", "warn"],
        cwd=REPO / "frontend",
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    assert (out / "index.html").is_file()
    return out


@pytest.fixture(scope="module")
def chromium() -> Iterator[Any]:
    if not _browser_tests_enabled():
        pytest.skip(_BROWSER_SKIP)
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="run with: uv run --with playwright pytest ..."
    )
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:  # no browser build installed for this Playwright
            pytest.skip(f"Chromium for Playwright is missing ({type(exc).__name__})")
        try:
            yield browser
        finally:
            browser.close()


def _play_case(
    run_root: Path,
    session_id: str,
    game: _GatedGame,
    *,
    hook: Callable[[str], None] | None = None,
    options: MatchOptions | None = None,
) -> tuple[threading.Thread, list[Any]]:
    """Play one case through the production runner in a thread (the game process)."""
    chosen = hook or runner.launch_hook(run_root, session_id, BUNDLE.policy_hash)
    match = MatchOptions() if options is None else options
    result: list[Any] = []

    def play() -> None:
        try:
            result.append(
                run_match(match, BUNDLE, run_root=run_root, launcher=game, on_recorded=chosen)
            )
        except BaseException as exc:  # e.g. Ctrl+C in the barrier: the run records stopped
            result.append(exc)

    thread = threading.Thread(target=play, daemon=True)
    thread.start()
    return thread, result


def _pump_until(page: Any, condition: Callable[[], object], timeout: float = 20.0) -> None:
    """Wait for ``condition`` while letting the page (and its route handlers) run."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting in the browser")
        page.wait_for_timeout(100)


class _Evidence:
    """Runtime evidence for reviewers (opt-in): ``JEV_BROWSER_EVIDENCE_DIR=<dir>`` saves a
    full-page screenshot per phase, named by test and phase, plus ``timeline.jsonl``.
    Without the variable nothing is written and the tests behave the same."""

    def __init__(self, test: str) -> None:
        root = os.environ.get("JEV_BROWSER_EVIDENCE_DIR")
        self.dir = Path(root) if root else None
        self.test = test

    @property
    def enabled(self) -> bool:
        return self.dir is not None

    def server_log(self) -> Path | None:
        """Where this test's server access/error log goes (evidence mode only)."""
        return None if self.dir is None else self.dir / f"{self.test}-uvicorn.log"

    def note(self, phase: str, **facts: Any) -> None:
        if self.dir is None:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        record = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "test": self.test, "phase": phase}
        record.update(facts)
        with (self.dir / "timeline.jsonl").open("a", encoding="utf-8") as timeline:
            timeline.write(json.dumps(record, sort_keys=True) + "\n")

    def shot(self, page: Any, phase: str, **facts: Any) -> None:
        if self.dir is None:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        name = f"{self.test}-{phase}.png"
        page.screenshot(path=str(self.dir / name), full_page=True)
        launch_phase = page.get_by_test_id("jev-launch-phase")
        shown = launch_phase.inner_text() if launch_phase.count() else None
        self.note(phase, screenshot=name, launch_phase=shown, **facts)


#: Records, at the moment the page sends each readiness POST, what the page shows.
_ACK_DOM_RECORDER = """
window.__jevAckDom = [];
const jevRealFetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  const url = String(input instanceof Request ? input.url : input);
  if (init && init.method === "POST" && url.endsWith("/ready")) {
    const shown = document.querySelector('[data-testid="jev-run-id"]');
    window.__jevAckDom.push({
      body: String(init.body),
      runId: shown === null ? null : shown.textContent,
      graph: document.querySelector('[role="tree"]') !== null,
    });
  }
  return jevRealFetch(input, init);
};
"""


def _assert_rendered_at_each_ack(page: Any) -> list[dict[str, Any]]:
    """Every readiness POST was sent while the page showed that run and its graph."""
    records: list[dict[str, Any]] = page.evaluate("window.__jevAckDom")
    assert records, "the page sent no readiness acknowledgment"
    for record in records:
        assert record["runId"] == json.loads(record["body"])["run_id"] and record["graph"]
    return records


@pytest.mark.usefixtures("built_dashboard", "chromium")
class TestRealBrowser:
    """The rendered-ready barrier and session following, end to end in Chromium."""

    def test_the_exact_run_is_rendered_before_sc2_and_the_batch_is_followed(
        self, built_dashboard: Path, chromium: Any, tmp_path: Path
    ) -> None:
        from playwright.sync_api import expect

        from test_jev_benchmark import _hosted_service

        evidence = _Evidence("t1")
        run_root = tmp_path / "runs"
        unrelated = _recorded_run(run_root)  # what a legacy page would show
        # Game 1 asks the (mocked) service for its army intent, so Live shows a decision.
        hosted = MatchOptions(decision_provider="typesafe", decision_model=benchmark.PINNED_MODEL)
        service = contextlib.ExitStack()
        service.enter_context(_hosted_service())
        server = _Server(  # cold: not running yet
            run_root, dist=built_dashboard, access_log=evidence.server_log()
        )
        context = chromium.new_context(viewport={"width": 1280, "height": 900})
        context.add_init_script(_ACK_DOM_RECORDER)
        page = context.new_page()
        held: list[Any] = []
        page.route("**/api/jev/launches/*/ready", lambda route: held.append(route))
        opened: list[str] = []

        def opener(url: str) -> None:
            opened.append(url)
            page.goto(url)

        try:
            dash = DashboardLaunch.open(
                run_root,
                case_count=2,
                environ={},
                endpoints=server.endpoints,
                start=server.start,
                opener=opener,
                out=lambda line: None,
            )
            assert opened == [dash.url] and server.started
            sid, launches = dash.session_id, launch_root_for(run_root)
            phase = page.get_by_test_id("jev-launch-phase")
            live_badge = page.get_by_test_id("jev-live-badge")
            expect(phase).to_have_text("Preparing")
            expect(page.get_by_test_id("jev-launch-detail")).to_contain_text(
                "waiting for the launcher to record"
            )
            expect(page.get_by_test_id("jev-run-select")).to_have_value("")
            assert page.get_by_test_id("jev-run-summary").count() == 0  # no old match shown
            evidence.shot(page, "01-preparing", session_id=sid, cold_start=True)

            # Game 1: the barrier holds until this page rendered the exact run.
            first = _GatedGame(
                launches, sid, steps=30, launch_delay=2.5, step_delay=0.2, hosted=True
            )
            dash.before_case(0, 2)
            thread, result = _play_case(run_root, sid, first, options=hosted)
            _pump_until(page, lambda: held)
            run1 = read_session(launches, sid).active_run_id
            assert run1 is not None and not first.prepared.is_set()
            expect(page.get_by_test_id("jev-run-id")).to_have_text(run1)
            expect(page.get_by_test_id("jev-run-hash")).to_have_text(BUNDLE.policy_hash)
            expect(page.get_by_role("tree")).to_be_visible()
            expect(live_badge).to_have_count(0)  # held at the barrier: never live
            expect(page.get_by_test_id("jev-launch-starting")).to_be_visible()
            assert held[0].request.post_data_json == {
                "run_id": run1,
                "policy_hash": BUNDLE.policy_hash,
            }
            page.wait_for_timeout(1500)
            assert not first.prepared.is_set()  # still gated while the receipt is held
            evidence.shot(
                page,
                "02-held-at-barrier",
                run_id=run1,
                session_state=read_session(launches, sid).state,
                sc2_started=first.prepared.is_set(),
            )
            held.pop(0).continue_()
            expect(phase).to_have_text("Starting", timeout=10_000)
            expect(live_badge).to_have_count(0)
            expect(page.get_by_test_id("jev-stale-badge")).to_have_count(0)
            evidence.shot(page, "03-starting", run_id=run1, sc2_started=first.prepared.is_set())
            # A repeated acknowledgment of the same run is idempotent (a receipt only).
            again = page.request.post(
                f"{server.base}/api/jev/launches/{sid}/ready",
                data=json.dumps({"run_id": run1, "policy_hash": BUNDLE.policy_hash}),
                headers={"Origin": server.base, "Content-Type": "application/json"},
            )
            assert again.status == 200 and again.json()["run_id"] == run1
            evidence.note("idempotent-reack", status=again.status, run_id=run1)
            expect(phase).to_have_text("Live", timeout=15_000)
            detail = page.get_by_test_id("jev-launch-detail")
            expect(detail).to_contain_text("vs Terran")
            expect(detail).to_contain_text("active decision node(s)")
            expect(detail).to_contain_text("army intent attack", timeout=10_000)
            expect(detail).not_to_contain_text("game time 0.0 s", timeout=10_000)
            expect(live_badge).to_be_visible()
            expect(page.get_by_test_id("jev-decision-age")).to_have_text("Live evidence")
            evidence.shot(page, "04-live", run_id=run1, detail=detail.inner_text())
            thread.join(60)
            assert first.receipt is not None and first.receipt.run_id == run1
            expect(phase).to_have_text("Finished", timeout=10_000)
            expect(detail).to_contain_text("win")
            evidence.shot(page, "05-finished", run_id=run1, result="win")
            dash.after_case(0, 2, "game 1 of 2: complete win")
            # Between games (the next case is prepared only after the manual pick below).
            expect(detail).to_contain_text("The next game is being prepared in this tab.")
            evidence.shot(
                page, "05b-between-games", session_state=read_session(launches, sid).state
            )

            # Browsing history pauses following; the next game then waits for this page.
            page.get_by_test_id("jev-run-select").select_option(unrelated)
            expect(page.get_by_test_id("jev-launch-paused")).to_be_visible()
            expect(page.get_by_test_id("jev-run-id")).to_have_text(unrelated)
            evidence.shot(page, "06-following-paused", viewing=unrelated)
            second = _GatedGame(launches, sid, steps=60, step_delay=0.15)
            dash.before_case(1, 2)
            thread, result = _play_case(run_root, sid, second)
            _pump_until(page, lambda: read_session(launches, sid).state == "starting")
            run2 = read_session(launches, sid).active_run_id
            assert run2 is not None and run2 != run1
            expect(phase).to_have_text("Waiting for this page")
            expect(page.get_by_test_id("jev-launch-paused")).to_contain_text(
                "waiting for this page"
            )
            page.wait_for_timeout(2000)
            if evidence.enabled:  # the 5 s heartbeat advancing while the page holds the barrier
                beats: list[str] = []
                for tick in range(15):
                    held_session = read_session(launches, sid)
                    beats.append(held_session.updated_at)
                    evidence.note(
                        "heartbeat-sample",
                        second=tick,
                        state=held_session.state,
                        updated_at=held_session.updated_at,
                    )
                    page.wait_for_timeout(1000)
                assert len(set(beats)) >= 3 and not second.prepared.is_set()
            stale = read_ready(launches, sid)
            assert stale is not None and stale.run_id == run1  # the previous run's receipt
            assert not second.prepared.is_set() and held == []
            evidence.shot(
                page, "07-waiting-for-this-page", run_id=run2, stale_receipt_run=stale.run_id
            )
            page.get_by_test_id("jev-resume-live").click()
            expect(page.get_by_test_id("jev-run-id")).to_have_text(run2)
            evidence.shot(page, "08-resume-live", run_id=run2)
            _pump_until(page, lambda: held)
            page.wait_for_timeout(1000)
            assert not second.prepared.is_set()  # run1's receipt never released run2
            evidence.shot(
                page,
                "09-stale-readiness-held",
                run_id=run2,
                stale_receipt_run=stale.run_id,
                sc2_started=second.prepared.is_set(),
            )
            held.pop(0).continue_()
            newer = _recorded_run(run_root)  # an unrelated newer run while following
            expect(phase).to_have_text("Live", timeout=15_000)
            expect(page.get_by_test_id("jev-launch-game")).to_have_text("Game 2 of 2")
            evidence.shot(page, "10-batch-game-2-live", run_id=run2)
            page.wait_for_timeout(6000)  # more than a run-list poll while following
            expect(page.get_by_test_id("jev-run-id")).to_have_text(run2)
            listed = page.get_by_test_id("jev-run-select").locator(f'option[value="{newer}"]')
            expect(listed).to_have_count(1)  # the list shows it; the view does not follow it
            evidence.shot(page, "11-newer-run-not-selected", run_id=run2, newer_run=newer)
            thread.join(60)
            assert second.receipt is not None and second.receipt.run_id == run2
            expect(phase).to_have_text("Finished", timeout=10_000)
            dash.end("finished", "batch complete")
            expect(page.get_by_test_id("jev-launch-message")).to_contain_text("batch complete")
            evidence.shot(page, "12-batch-finished", run_id=run2)
            assert page.get_by_test_id("jev-run-id").inner_text() == run2 != newer
            assert context.pages == [page] and page.url == dash.url  # one tab, never reopened
            assert all(isinstance(r, runner.MatchOutcome) for r in result)
            acks = _assert_rendered_at_each_ack(page)
            assert [json.loads(a["body"])["run_id"] for a in acks] == [run1, run2]
            evidence.note("dom-at-each-ack", acks=acks)
        finally:
            context.close()
            server.stop()
            service.close()

    def test_failure_and_stop_before_start_are_visible_and_never_start_sc2(
        self, built_dashboard: Path, chromium: Any, tmp_path: Path
    ) -> None:
        from playwright.sync_api import expect

        evidence = _Evidence("t2")
        # With evidence on, the production 60 s deadline is observed (slow); else 4 s.
        deadline = launch.RUN_READY_SECONDS if evidence.enabled else 4.0
        run_root = tmp_path / "runs"
        launches = launch_root_for(run_root)
        serving = _serving(run_root, dist=built_dashboard, access_log=evidence.server_log())
        with serving as server:
            context = chromium.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            try:
                # The page cannot acknowledge: the launch times out on a watched page.
                page.route("**/api/jev/launches/*/ready", lambda route: route.abort())
                dash = DashboardLaunch.open(
                    run_root,
                    case_count=1,
                    environ={},
                    endpoints=server.endpoints,
                    start=_no_start,
                    opener=_navigate(page),
                    out=lambda line: None,
                )
                game = _GatedGame(launches, dash.session_id)
                writer = LaunchSessionWriter.adopt(launches, dash.session_id)
                hook = ready_hook(writer, BUNDLE.policy_hash, deadline_seconds=deadline)
                started = time.monotonic()
                thread, result = _play_case(run_root, dash.session_id, game, hook=hook)
                phase = page.get_by_test_id("jev-launch-phase")
                expect(page.get_by_test_id("jev-launch-ack-error")).to_be_visible(timeout=15_000)
                expect(phase).to_have_text("Preparing")
                evidence.shot(page, "01-watched-before-failure", deadline_seconds=deadline)
                expect(phase).to_have_text("Failed", timeout=int((deadline + 20) * 1000))
                failed_after = time.monotonic() - started
                thread.join(30)
                (outcome,) = result
                assert outcome.status == "stopped" and not game.prepared.is_set()
                assert failed_after >= deadline
                expect(page.get_by_test_id("jev-launch-detail")).to_contain_text("did not show run")
                expect(page.get_by_test_id("jev-run-status")).to_have_text("Status: stopped")
                expect(page.get_by_test_id("jev-live-badge")).to_have_count(0)
                evidence.shot(
                    page,
                    "02-failed",
                    run_id=outcome.run_id,
                    run_status=outcome.status,
                    failed_after_seconds=round(failed_after, 1),
                    sc2_started=game.prepared.is_set(),
                )

                # Ctrl+C before the acknowledgment: a clean stop, SC2 never started.
                page.unroute("**/api/jev/launches/*/ready")
                page.route("**/api/jev/launches/*/ready", lambda route: None)  # held forever
                dash2 = DashboardLaunch.open(
                    run_root,
                    case_count=1,
                    environ={},
                    endpoints=server.endpoints,
                    start=_no_start,
                    opener=_navigate(page),
                    out=lambda line: None,
                )
                waited: list[float] = []

                def operator_presses_ctrl_c(seconds: float) -> None:
                    waited.append(seconds)
                    if len(waited) > 12:
                        raise KeyboardInterrupt
                    time.sleep(seconds)

                stopped_game = _GatedGame(launches, dash2.session_id)
                writer2 = LaunchSessionWriter.adopt(launches, dash2.session_id)
                hook2 = ready_hook(writer2, BUNDLE.policy_hash, sleep=operator_presses_ctrl_c)
                thread, result = _play_case(run_root, dash2.session_id, stopped_game, hook=hook2)
                expect(phase).to_have_text("Stopped", timeout=15_000)
                thread.join(30)
                assert isinstance(result[0], KeyboardInterrupt)
                assert not stopped_game.prepared.is_set()
                expect(page.get_by_test_id("jev-run-status")).to_have_text("Status: stopped")
                evidence.shot(page, "03-stopped", sc2_started=stopped_game.prepared.is_set())
                assert context.pages == [page]
            finally:
                context.close()

    def test_links_select_exactly_their_run_or_nothing(
        self, built_dashboard: Path, chromium: Any, tmp_path: Path
    ) -> None:
        from playwright.sync_api import expect

        evidence = _Evidence("t3")
        run_root = tmp_path / "runs"
        older = _recorded_run(run_root)
        time.sleep(0.05)
        newer = _recorded_run(run_root)
        serving = _serving(run_root, dist=built_dashboard, access_log=evidence.server_log())
        with serving as server:
            context = chromium.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            run_id = page.get_by_test_id("jev-run-id")
            try:
                page.request.get(f"{server.base}/api/jev/runs")  # warm the backend first
                page.goto(f"{server.base}/?tab=jev")  # unchanged without parameters: newest
                expect(run_id).to_have_text(newer)
                evidence.shot(page, "01-legacy-newest", run_id=newer)
                page.goto(f"{server.base}/?tab=jev&run={older}")
                expect(run_id).to_have_text(older)
                page.wait_for_timeout(5500)  # a run-list poll later, still exactly that run
                expect(run_id).to_have_text(older)
                evidence.shot(page, "02-run-link-exact", run_id=older)
                for name, bad in (
                    ("03-invalid-run-link", "run=not-a-run"),
                    ("04-traversal-launch-link", "launch=..%2F..%2Fdata"),
                    ("05-uppercase-launch-link", f"launch={older.upper()}"),
                ):
                    page.goto(f"{server.base}/?tab=jev&{bad}")
                    expect(page.get_by_test_id("jev-link-error")).to_be_visible()
                    page.wait_for_timeout(1200)
                    expect(page.get_by_test_id("jev-run-summary")).to_have_count(0)
                    evidence.shot(page, name, link=bad)
                # What the backend itself answers for such ids (the page never sends them).
                for raw in ("..%2F..%2Fdata", "%2e%2e", older.upper()):
                    answer = page.request.get(f"{server.base}/api/jev/launches/{raw}")
                    assert answer.status in (404, 422)
                    evidence.note("backend-rejects-bad-session-id", id=raw, status=answer.status)
                missing = uuid.uuid4().hex
                page.goto(f"{server.base}/?tab=jev&launch={missing}")
                expect(page.get_by_test_id("jev-launch-error")).to_contain_text("launch_not_found")
                expect(page.get_by_test_id("jev-launch-phase")).to_have_text("Launch unavailable")
                expect(page.get_by_test_id("jev-run-summary")).to_have_count(0)
                evidence.shot(page, "06-launch-not-found", session_id=missing)
                writer = LaunchSessionWriter.create(launch_root_for(run_root))
                page.goto(f"{server.base}/?tab=jev&launch={writer.session_id}&run={newer}")
                expect(page.get_by_test_id("jev-launch-phase")).to_have_text("Preparing")
                page.wait_for_timeout(1500)  # the launch wins: no run until the session names one
                expect(page.get_by_test_id("jev-run-summary")).to_have_count(0)
                evidence.shot(page, "07-launch-wins-over-run", session_id=writer.session_id)
            finally:
                context.close()

    def test_a_dashboard_serving_another_root_is_refused_and_its_page_says_why(
        self, built_dashboard: Path, chromium: Any, tmp_path: Path
    ) -> None:
        from playwright.sync_api import expect

        evidence = _Evidence("t4")
        served, ours = tmp_path / "served" / "runs", tmp_path / "ours" / "runs"
        _recorded_run(served)
        serving = _serving(served, dist=built_dashboard, access_log=evidence.server_log())
        with serving as server:
            opened: list[str] = []
            with pytest.raises(DashboardUnavailable, match="another data root") as refused:
                DashboardLaunch.open(
                    ours,
                    case_count=1,
                    environ={},
                    endpoints=server.endpoints,
                    start=_no_start,
                    opener=opened.append,
                    out=lambda line: None,
                )
            assert opened == []  # no browser for a launch that cannot work
            (session_dir,) = launch_root_for(ours).iterdir()
            evidence.note("wrong-root-refused", problem=refused.value.message)
            context = chromium.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            try:
                page.goto(f"{server.base}/?tab=jev&launch={session_dir.name}")
                expect(page.get_by_test_id("jev-launch-error")).to_contain_text("launch_not_found")
                page.wait_for_timeout(1500)
                expect(page.get_by_test_id("jev-run-summary")).to_have_count(0)
                evidence.shot(page, "01-wrong-served-root", session_id=session_dir.name)
            finally:
                context.close()

    def test_a_paused_page_during_a_long_healthy_game_raises_no_false_alarm(
        self, built_dashboard: Path, chromium: Any, tmp_path: Path
    ) -> None:
        """Round-2 regression, at runtime: in ``running`` the session's stamp is from
        the release, so a long game makes it old; a paused page must not call that a
        dead launcher, and following again shows the game Live."""
        from playwright.sync_api import expect

        evidence = _Evidence("t6")
        run_root = tmp_path / "runs"
        unrelated = _recorded_run(run_root)
        launches = launch_root_for(run_root)
        serving = _serving(run_root, dist=built_dashboard, access_log=evidence.server_log())
        with serving as server:
            context = chromium.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            try:
                dash = DashboardLaunch.open(
                    run_root,
                    case_count=1,
                    environ={},
                    endpoints=server.endpoints,
                    start=_no_start,
                    opener=_navigate(page),
                    out=lambda line: None,
                )
                game = _GatedGame(launches, dash.session_id, steps=200, step_delay=0.1)
                thread, result = _play_case(run_root, dash.session_id, game)
                phase = page.get_by_test_id("jev-launch-phase")
                expect(phase).to_have_text("Live", timeout=20_000)
                # Stand-in clock: the release was 200 s ago (more than the 120 s bound).
                released = read_session(launches, dash.session_id)
                aged = LaunchSessionWriter.adopt(
                    launches, dash.session_id, wall_time=lambda: time.time() - 200
                )
                aged.publish("running", message=released.message)
                page.get_by_test_id("jev-run-select").select_option(unrelated)
                expect(phase).to_have_text("Following paused")
                page.wait_for_timeout(3000)  # several session polls with the old stamp
                expect(phase).to_have_text("Following paused")
                expect(page.get_by_text("not responding")).to_have_count(0)
                expect(page.get_by_test_id("jev-launch-paused-silent")).to_have_count(0)
                assert read_session(launches, dash.session_id).state == "running"
                evidence.shot(
                    page,
                    "01-paused-long-game-no-alarm",
                    session_updated_at=read_session(launches, dash.session_id).updated_at,
                    session_age_seconds_at_least=200,
                )
                page.get_by_test_id("jev-resume-live").click()
                expect(phase).to_have_text("Live", timeout=10_000)  # the game's own heartbeat
                evidence.shot(page, "02-resumed-live")
                thread.join(60)
                assert isinstance(result[0], runner.MatchOutcome)
            finally:
                context.close()

    def test_the_vite_dev_proxy_forwards_the_page_origin_to_the_backend(
        self, chromium: Any, tmp_path: Path
    ) -> None:
        """The dashboard's own dev server (frontend/vite.config.ts) proxies /api with the
        page's Origin intact, so the backend can require it (on free ports, never 3000)."""
        node = shutil.which("node")
        vite = REPO / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
        if node is None or not vite.is_file():
            pytest.skip("Node and the frontend dependencies (npm --prefix frontend ci) are needed")
        evidence = _Evidence("t5")
        run_root = tmp_path / "runs"
        vite_port = _free_port()
        page_origin = f"http://localhost:{vite_port}"
        server = _Server(  # only the proxied page may acknowledge
            run_root, origins={page_origin}, access_log=evidence.server_log()
        )
        server.start()
        config = tmp_path / "vite.proxy.config.mjs"
        base_config = (REPO / "frontend" / "vite.config.ts").as_posix()
        lines = [  # the project's own config; only the proxy targets point at this test
            f"import base from '{base_config}'",
            "export default { ...base, server: { ...base.server, proxy: {",
            f"  '/api': '{server.base}',",
            f"  '/ws': {{ target: '{server.base}', ws: true }},",
            "} } }",
        ]
        config.write_text("\n".join(lines) + "\n", encoding="utf-8")
        process = subprocess.Popen(
            [node, str(vite), "--config", str(config), "--port", str(vite_port), "--strictPort"],
            cwd=REPO / "frontend",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        context = chromium.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        try:
            endpoints = DashboardEndpoints(api_url=server.base, dashboard_url=page_origin)
            deadline = time.monotonic() + 60
            while dashboard_health(endpoints) is not None:  # the dev server is starting
                assert process.poll() is None and time.monotonic() < deadline
                time.sleep(0.5)
            dash = DashboardLaunch.open(
                run_root,
                case_count=1,
                environ={},
                endpoints=endpoints,
                start=_no_start,
                opener=_navigate(page),
                out=lambda line: None,
            )
            assert dash.url.startswith(f"{page_origin}/?tab=jev&launch=")
            launches = launch_root_for(run_root)
            game = _GatedGame(launches, dash.session_id)
            thread, result = _play_case(run_root, dash.session_id, game)
            thread.join(60)
            (outcome,) = result
            assert isinstance(outcome, runner.MatchOutcome) and outcome.result == "win"
            receipt = read_ready(launches, dash.session_id)
            assert receipt is not None and receipt.run_id == outcome.run_id
            assert game.receipt == receipt  # acknowledged through the proxy before "SC2"
            from playwright.sync_api import expect

            expect(page.get_by_test_id("jev-launch-phase")).to_have_text("Finished")
            evidence.shot(page, "01-vite-proxy-acknowledged", run_id=outcome.run_id)
        finally:
            context.close()
            benchmark.terminate_process_tree(process)  # only the dev server this test started
            server.stop()
