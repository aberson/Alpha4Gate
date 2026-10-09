"""Reproducible Jev benchmarks (Step 212): frozen source, bounded batches, scoring.

Nothing here launches StarCraft II or calls the hosted service. Matches are played
by the production :func:`jev.runner.run_match` against the stand-in game of
``test_jev_sc2`` (fake ports), hosted answers come from an ``httpx.MockTransport``
behind the real Typesafe client, and the one real child process the end-to-end
test starts points ``SC2PATH`` at an empty folder, so the production entrypoint
records ``sc2_unavailable`` before burnysc2 is even imported.

No test here opens the real dashboard: real runs pass ``--no-dashboard`` (explicit
headless) or a fake dashboard, and an autouse fixture fails any test that would reach
the default one (the dashboard-first flow itself is tested in ``test_jev_launch.py``).
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Final, get_args

import httpx
import pytest
from bots.jev.v1 import load_policy
from sc2.data import Result

import jev.bot as bot_module
import jev.decision as decision_module
import jev.telemetry as telemetry_module
from jev import benchmark, runner
from jev.api import STALE_AFTER_SECONDS
from jev.benchmark import (
    BatchStore,
    BenchmarkLimits,
    CaseSpec,
    ChildEvidence,
    ChildExit,
    ChildLaunch,
    LockHeld,
    ProvenanceMismatch,
    ResolvedSource,
    RunExpectation,
    SubprocessLauncher,
    SystemProcessProbe,
    VersionUnavailable,
    build_manifest,
    capture_source,
    child_environment,
    finalize_baseline,
    panel_cases,
    probe_model,
    read_finalized,
    resolve_sources,
    run_batch,
    runtime_source_paths,
    score_run,
    verify_snapshot,
)
from jev.bot import METRIC_SAMPLE_GAP_SECONDS, JevBot, JevController, MatchMetrics
from jev.contracts import Entity, JsonValue, Observation, is_valid_run_id
from jev.decision import DecisionConfig, TypesafeProvider
from jev.launch import (
    FIRST_OBSERVATION_SILENCE_SECONDS,
    LAUNCH_HEARTBEAT_SECONDS,
    LAUNCH_SILENCE_SECONDS,
    STARTING_SILENCE_SECONDS,
)
from jev.policy import load_policy_bundle
from jev.runner import DIAGNOSTICS_FILE, MatchOptions, run_match
from jev.telemetry import REPLAY_FILE, EvidenceFiles, read_run_metadata, repository_root
from test_jev_decision import _body, _observation, _settle
from test_jev_sc2 import START, _game, _Port, _unit

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "benchmark_jev.py"
#: The packaged v1 policy hash JI validated live; J2 must never change it.
V1_HASH = "510a72dfb167b7200123cbc8436c912f02d3b0fb5798657db3c62e3f5f8f68ee"
PINNED = benchmark.PINNED_MODEL
KEY = "TYPESAFE_API_KEY"
FAKE_KEY = "benchmark-test-key"  # never a real credential


# ---------------------------------------------------------------------------
# Stand-ins: a played match, a hosted service, a child process, a lock probe
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_real_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly instead of contacting the operator's dashboard on ports 8765/3000."""

    def forbidden(case_count: int) -> Any:
        raise AssertionError("a benchmark test reached the real dashboard launch")

    monkeypatch.setattr(benchmark, "_default_dashboard", lambda *args: forbidden)


class _GameLauncher:
    """Plays a short stand-in match through the real JevBot/JevController.

    Like burnysc2, it saves the replay into the run directory. ``hosted`` drives the
    army-decision scenario of ``test_jev_decision`` (four ready Zealots, the attack
    already launched) so the production coordinator gets and applies an answer.
    """

    def __init__(
        self,
        result: str | None = "win",
        *,
        steps: int = 6,
        crash: bool = False,
        hosted: bool = False,
        replay: bool = True,
        before_step: Callable[[SimpleNamespace, JevController, int], None] | None = None,
        game: Callable[[], SimpleNamespace] | None = None,
    ) -> None:
        self.game = game
        self.result = result
        self.steps = steps
        self.crash = crash
        self.hosted = hosted
        self.replay = replay
        self.before_step = before_step
        self.port = _Port()

    def prepare(self, options: MatchOptions) -> object:
        return "setup"

    def play(self, controller: JevController, setup: object, options: MatchOptions) -> Any:
        recorder = controller.recorder
        if self.replay and recorder is not None:
            recorder.replay_file.write_bytes(b"stand-in replay")
        if self.hosted:
            controller.runtime.set_army_decision("attack", {})
            controller.runtime.tick(_observation(9))
            game = _game(
                units=[_unit(2000 + i, "Zealot", position=START) for i in range(4)], minerals=0
            )
        else:
            game = self.game() if self.game else _game(state=SimpleNamespace(game_loop=0))
        controller.attach(game, self.port)
        if self.crash:
            raise RuntimeError("stand-in crash after the bot attached")
        bot = JevBot(controller)

        async def drive() -> str | None:
            for index in range(self.steps):
                if self.before_step is not None:
                    self.before_step(game, controller, index)
                await bot.on_step(index)
                if self.port.left:
                    await bot.on_end(Result.Defeat)
                    return "loss"
                await _settle()
                game.state.game_loop += 8
            await controller.close_decisions()  # what on_end does when SC2 ends the game
            return self.result

        return asyncio.run(drive())


def _service(model: str = PINNED, *, status: int = 200) -> httpx.MockTransport:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
        if status != 200:
            return httpx.Response(status, json={"error": "refused"})
        sent = json.loads(request.content)
        choices = set(sent["questions"]["army_mode"]["criteria"])
        body = _body("attack", model=model)
        probabilities = {c: 0.1 for c in choices}
        probabilities["attack"] = 1 - 0.1 * (len(choices) - 1)
        body["answers"]["army_mode"]["probabilities"] = probabilities
        body["answers"]["army_mode"]["confidence"] = probabilities["attack"]
        return httpx.Response(200, json=body)

    return httpx.MockTransport(respond)


@contextlib.contextmanager
def _hosted_service(model: str = PINNED, *, status: int = 200) -> Iterator[None]:
    """Route every production Typesafe request to the mock transport."""
    transport = _service(model, status=status)
    original = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(httpx, "AsyncClient", client)
        patch.setenv(KEY, FAKE_KEY)
        yield


def _options(case: CaseSpec, limits: BenchmarkLimits | None = None, **changes: Any) -> MatchOptions:
    return replace(case.match_options(limits or BenchmarkLimits(), PINNED), **changes)


SCRIPTED = CaseSpec("v1", "scripted", "Simple64", "Terran", 1, 1, False)
HOSTED = CaseSpec("v1", "typesafe", "Simple64", "Terran", 3, 11, True)


def _play(
    run_root: Path,
    options: MatchOptions,
    launcher: _GameLauncher | None = None,
    *,
    model: str = PINNED,
    status: int = 200,
) -> str:
    """Play one match through the production runner; its run id."""
    launcher = launcher or _GameLauncher(hosted=options.decision_provider == "typesafe")
    if options.decision_provider == "typesafe":
        with _hosted_service(model, status=status):
            outcome = run_match(options, load_policy(), run_root=run_root, launcher=launcher)
    else:
        outcome = run_match(options, load_policy(), run_root=run_root, launcher=launcher)
    return outcome.run_id


def _expect(options: MatchOptions, **changes: Any) -> RunExpectation:
    values: dict[str, Any] = {
        "version": 1,
        "entrypoint": "bots.jev.v1",
        "policy_hash": V1_HASH,
        "options": options,
        "expected_returned_model": PINNED if options.decision_provider == "typesafe" else None,
        "snapshot_dir": None,
        "require_diagnostics": True,
    }
    values.update(changes)
    return RunExpectation(**values)


def _diagnostics(run_dir: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((run_dir / DIAGNOSTICS_FILE).read_text())
    return document


def _rewrite_diagnostics(run_dir: Path, change: Callable[[dict[str, Any]], None]) -> None:
    document = _diagnostics(run_dir)
    change(document)
    (run_dir / DIAGNOSTICS_FILE).write_text(json.dumps(document))


class _Probe:
    """A lock process probe: this 'process' is pid 4242; others alive as configured."""

    def __init__(self, *, alive: bool) -> None:
        self.is_alive = alive
        self.asked: list[int] = []

    def current(self) -> tuple[int, float | None]:
        return 4242, 1.0

    def alive(self, pid: int, started: float | None) -> bool:
        self.asked.append(pid)
        return self.is_alive


class _FakeChild:
    """Stands in for the child process the production launcher starts.

    It parses the exact command line with the runner's own parser, loads the policy
    from the snapshot it was pointed at, and plays the match with the production
    ``run_match`` -- with ``repository_root`` resolving to that snapshot, which is
    what the real child computes when it runs from it -- then prints the runner's
    own summary line. Behaviors per case id: ``win`` (default), ``crash``,
    ``interrupt`` (Ctrl+C while the case runs), ``silent`` (exits without output).
    """

    def __init__(
        self, behaviors: dict[str, str] | None = None, *, model: str = PINNED, status: int = 200
    ) -> None:
        self.behaviors = behaviors or {}
        self.model = model
        self.status = status
        self.launches: list[ChildLaunch] = []

    def run(self, launch: ChildLaunch) -> ChildExit:
        self.launches.append(launch)
        if launch.on_started is not None:
            launch.on_started(os.getpid())  # the stand-in "child" is this process
        assert launch.argv[1] == "-m"
        module = launch.argv[2]
        args = runner.build_parser(module).parse_args(list(launch.argv[3:]))
        options = MatchOptions(
            map_name=args.map,
            opponent_race=args.opponent_race,
            difficulty=args.difficulty,
            seed=args.seed,
            max_game_seconds=args.max_game_seconds,
            max_wall_seconds=args.max_wall_seconds,
            realtime=args.realtime,
            decision_provider=args.decision_provider,
            decision_model=args.decision_model,
            decision_max_requests=args.decision_max_requests,
        )
        case = CaseSpec(
            module.rsplit(".", 1)[1],
            "typesafe" if options.decision_provider == "typesafe" else "scripted",
            options.map_name,
            options.opponent_race,
            options.difficulty,
            options.seed,
            options.realtime,
        )
        behavior = self.behaviors.get(case.case_id, "win")
        if behavior == "interrupt":
            raise KeyboardInterrupt
        if behavior == "silent":
            return ChildExit(1, 0.1)
        bundle = load_policy_bundle(
            launch.cwd / "bots" / "jev" / module.rsplit(".", 1)[1], expected_entrypoint=module
        )
        hosted = options.decision_provider == "typesafe"
        game = _GameLauncher(hosted=hosted, crash=behavior == "crash")
        out = io.StringIO()
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(runner, "repository_root", lambda: launch.cwd)
            if KEY in launch.env:
                patch.setenv(KEY, launch.env[KEY])
            else:
                patch.delenv(KEY, raising=False)
            with contextlib.ExitStack() as stack:
                if hosted:
                    stack.enter_context(_hosted_service(self.model, status=self.status))
                outcome = run_match(options, bundle, run_root=args.run_root, launcher=game)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                runner._report(outcome)
        launch.stdout_path.write_text(out.getvalue())
        return ChildExit(outcome.exit_code, 0.5)


_THREE = tuple(
    CaseSpec("v1", "scripted", "Simple64", race, 2, 1, False) for race in benchmark.RACES
)


@pytest.fixture(scope="module")
def snapshot_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The real v1 runtime source, captured once (read-only for the tests)."""
    baselines = tmp_path_factory.mktemp("bench") / "baselines"
    return capture_source(REPO, "v1", baselines).directory or Path()


@pytest.fixture(scope="module")
def source_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A hermetic copy of the v1 runtime source set (never the live checkout)."""
    root = tmp_path_factory.mktemp("source") / "repo"
    for relative in runtime_source_paths(REPO, "v1"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / relative, target)
    return root


def _own_tree(source_tree: Path, tmp_path: Path) -> Path:
    """A private copy of the hermetic source tree (for tests that modify it)."""
    target = tmp_path / "repo"
    shutil.copytree(source_tree, target)
    return target


def _package_v2(root: Path) -> None:
    """A stand-in candidate: the v1 package re-labeled as version 2 (tests only)."""
    package = root / "bots" / "jev" / "v2"
    shutil.copytree(root / "bots" / "jev" / "v1", package)
    manifest = json.loads((package / "manifest.json").read_text())
    manifest.update(version=2, entrypoint="bots.jev.v2")
    (package / "manifest.json").write_text(json.dumps(manifest))
    policy = json.loads((package / "policy.json").read_text())
    policy["version"] = 2
    (package / "policy.json").write_text(json.dumps(policy))


def _own_snapshot(snapshot_dir: Path, tmp_path: Path) -> Path:
    """A private copy of the captured snapshot (for tests that modify it)."""
    target = tmp_path / "baselines" / snapshot_dir.name
    shutil.copytree(snapshot_dir, target)
    return target


def _sources(directory: Path, state: str = "captured") -> dict[str, ResolvedSource]:
    snapshot = verify_snapshot(directory)
    return {
        "v1": ResolvedSource("v1", "bots.jev.v1", V1_HASH, snapshot, state)  # type: ignore[arg-type]
    }


def _store(
    tmp_path: Path,
    sources: dict[str, ResolvedSource],
    *,
    panel: str = "staging",
    cases: tuple[CaseSpec, ...] | None = None,
    limits: BenchmarkLimits | None = None,
    probe: _Probe | None = None,
) -> BatchStore:
    manifest = build_manifest(
        panel,
        sources,
        limits or BenchmarkLimits(),
        batch_id=uuid.uuid4().hex,
        source_commit=None,
        created_at="2026-10-08T00:00:00.000+00:00",
    )
    if cases is not None:
        manifest = replace(manifest, cases=cases)
    return BatchStore.create(tmp_path / "bench", manifest, probe=probe or _Probe(alive=False))


def _attempts(store: BatchStore) -> list[dict[str, Any]]:
    lines = (store.batch_dir / benchmark.ATTEMPTS_FILE).read_text().splitlines()
    return [json.loads(line) for line in lines]


def _load_script() -> ModuleType:
    name = "benchmark_jev_cli"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(SCRIPT))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Panels, case IDs, manifest and limits
# ---------------------------------------------------------------------------


def test_each_official_panel_is_exactly_the_six_plan_cases() -> None:
    baseline = panel_cases("baseline")
    assert [c.case_id for c in baseline] == [
        f"v1-typesafe-simple64-{race}-{difficulty}-11"
        for difficulty in (3, 4)
        for race in ("terran", "protoss", "zerg")
    ]
    assert all(c.realtime and c.hosted for c in baseline)
    for panel, seed in (("heldout-a", 101), ("heldout-b", 202)):
        cases = panel_cases(panel)
        assert len(cases) == 6 and {c.seed for c in cases} == {seed}
        assert {c.difficulty for c in cases} == {4} and all(c.hosted for c in cases)
        pairs = [tuple(c.version for c in cases[i : i + 2]) for i in range(0, 6, 2)]
        assert sorted(pairs[0]) == ["v1", "v2"] and pairs[0] != pairs[1] and pairs[1] != pairs[2]
    assert panel_cases("heldout-a")[0].version != panel_cases("heldout-b")[0].version
    attribution = panel_cases("attribution")
    assert len(attribution) == 6 and all(c.version == "v2" and not c.hosted for c in attribution)
    assert {(c.race, c.seed) for c in attribution} == {
        (c.race, c.seed) for c in panel_cases("heldout-a") + panel_cases("heldout-b")
    }
    (staging,) = panel_cases("staging")
    assert staging.case_id == "v1-scripted-simple64-terran-1-1" and not staging.realtime
    with pytest.raises(ValueError, match="unknown panel"):
        panel_cases("smoke")


def test_manifest_carries_the_plan_fields_and_round_trips(snapshot_dir: Path) -> None:
    sources = _sources(snapshot_dir)
    manifest = build_manifest(
        "baseline",
        sources,
        BenchmarkLimits(max_games=2),
        batch_id=uuid.uuid4().hex,
        source_commit=None,
        created_at="2026-10-08T00:00:00.000+00:00",
    )
    document = manifest.to_dict()
    assert {
        "schema_version",
        "batch_id",
        "claim",
        "source_commit",
        "source_fingerprint",
        "policy_hashes",
        "requested_model",
        "expected_returned_model",
        "cases",
        "limits",
        "created_at",
    } <= set(document)
    assert document["schema_version"] == 1 and is_valid_run_id(manifest.batch_id)
    assert document["policy_hashes"] == {"v1": V1_HASH}
    assert document["requested_model"] == document["expected_returned_model"] == "jev-1.13.0"
    assert document["limits"]["max_games"] == 2
    assert document["limits"]["child_max_wall_seconds"] == 1200 - 120
    assert benchmark.BatchManifest.from_dict(json.loads(json.dumps(document))) == manifest


def test_limits_are_mandatory_defaults_that_may_only_be_lowered() -> None:
    limits = BenchmarkLimits()
    assert (limits.max_games, limits.invocation_wall_seconds, limits.invocation_requests) == (
        6,
        7200,
        2700,
    )
    assert (limits.match_game_seconds, limits.match_wall_seconds, limits.match_requests) == (
        900,
        1200,
        450,
    )
    assert BenchmarkLimits(max_games=1, match_requests=10).match_requests == 10
    for name, value in (
        ("max_games", 7),
        ("match_requests", 451),
        ("match_wall_seconds", 1201),
        ("invocation_requests", 2701),
    ):
        with pytest.raises(ValueError, match="may only be lowered"):
            BenchmarkLimits(**{name: value})
    with pytest.raises(ValueError, match="at least"):
        BenchmarkLimits(match_wall_seconds=100)  # nothing left after the launch allowance


def test_cli_rejects_raised_limits_as_usage_errors(tmp_path: Path) -> None:
    for flag, value in (
        ("--max-games", "7"),
        ("--max-requests", "500"),
        ("--max-wall-seconds", "1800"),
        ("--invocation-requests", "3000"),
    ):
        with pytest.raises(SystemExit) as raised:
            benchmark.main(
                ["--panel", "staging", "--dry-run", flag, value, "--benchmark-root", str(tmp_path)]
            )
        assert raised.value.code == 2
    assert not any(tmp_path.iterdir())


# ---------------------------------------------------------------------------
# Frozen source capture (plan D1)
# ---------------------------------------------------------------------------


def test_capture_copies_the_exact_runtime_source_set_and_verifies_it(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    paths = runtime_source_paths(REPO, "v1")
    assert "src/jev/runner.py" in paths and "src/jev/bot.py" in paths
    assert {
        "bots/jev/v1/policy.json",
        "bots/jev/v1/manifest.json",
        "uv.lock",
        "src/orchestrator/paths.py",
    } <= set(paths)
    assert not any(p.startswith("data/") or "__pycache__" in p or ".env" in p for p in paths)
    snapshot = verify_snapshot(snapshot_dir)
    assert [f.path for f in snapshot.files] == list(paths)
    assert snapshot_dir.name == f"v1-{snapshot.fingerprint[:16]}"
    assert (snapshot_dir / "bots" / "jev" / "v1" / "policy.json").read_bytes() == (
        REPO / "bots" / "jev" / "v1" / "policy.json"
    ).read_bytes()
    # Content addressed: capturing the same source again reuses the verified snapshot.
    again = capture_source(REPO, "v1", snapshot_dir.parent)
    assert again.fingerprint == snapshot.fingerprint and again.directory == snapshot_dir
    # A bytecode cache could execute instead of the hashed source: rejected outright.
    copy = _own_snapshot(snapshot_dir, tmp_path)
    (copy / "src" / "jev" / "__pycache__").mkdir()
    (copy / "src" / "jev" / "__pycache__" / "runner.cpython-314.pyc").write_bytes(b"x")
    with pytest.raises(ProvenanceMismatch, match="bytecode cache"):
        verify_snapshot(copy)
    shutil.rmtree(copy / "src" / "jev" / "__pycache__")
    assert verify_snapshot(copy).fingerprint == snapshot.fingerprint
    (copy / "src" / "jev" / "extra.py").write_text("x = 1\n")
    with pytest.raises(ProvenanceMismatch, match="files changed"):
        verify_snapshot(copy)
    (copy / "src" / "jev" / "extra.py").unlink()
    runner_file = copy / "src" / "jev" / "runner.py"
    runner_file.write_bytes(runner_file.read_bytes() + b"\n# edited\n")
    with pytest.raises(ProvenanceMismatch, match="was modified"):
        verify_snapshot(copy)


def test_fingerprint_hashes_paths_and_bytes_in_sorted_order() -> None:
    first, _ = benchmark.source_fingerprint([("a.py", b"1"), ("b.py", b"2")])
    assert benchmark.source_fingerprint([("b.py", b"2"), ("a.py", b"1")])[0] == first
    assert benchmark.source_fingerprint([("a.py", b"2"), ("b.py", b"1")])[0] != first
    assert benchmark.source_fingerprint([("c.py", b"1"), ("b.py", b"2")])[0] != first


def test_capture_is_an_allowlist_and_refuses_credential_names(
    source_tree: Path, tmp_path: Path
) -> None:
    root = _own_tree(source_tree, tmp_path)
    assert runtime_source_paths(root, "v1") == runtime_source_paths(REPO, "v1")
    package = root / "bots" / "jev" / "v1"
    for stray in ("token.json", "auth.json", "local_settings.py", "notes.txt"):
        (package / stray).write_text("{}")  # never copied: not on the allowlist
    snapshot = capture_source(root, "v1", tmp_path / "baselines")
    assert {f.path for f in snapshot.files} == set(runtime_source_paths(REPO, "v1"))
    copied = (snapshot.directory or tmp_path).joinpath("bots", "jev", "v1").iterdir()
    assert sorted(p.name for p in copied) == [
        "__init__.py",
        "__main__.py",
        "manifest.json",
        "policy.json",
    ]
    (root / "src" / "jev" / "secret_store.py").write_text("x = 1\n")
    with pytest.raises(ProvenanceMismatch, match="credential"):
        capture_source(root, "v1", tmp_path / "other")
    assert not (tmp_path / "other").exists()


def test_a_baseline_is_finalized_once_and_never_replaced(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    baselines = tmp_path / "baselines"
    copy = _own_snapshot(snapshot_dir, tmp_path)
    snapshot = verify_snapshot(copy)
    assert read_finalized(baselines, "v1") is None
    finalize_baseline(baselines, snapshot)
    finalize_baseline(baselines, snapshot)  # idempotent for the same source
    assert read_finalized(baselines, "v1") == snapshot.fingerprint
    other = replace(snapshot, fingerprint="0" * 64)
    with pytest.raises(ProvenanceMismatch, match="never replaced"):
        finalize_baseline(baselines, other)
    assert read_finalized(baselines, "v1") == snapshot.fingerprint
    # Once finalized, the baseline panel resolves to the frozen copy, whatever the source.
    resolved = resolve_sources("baseline", source_root=REPO, baselines_dir=baselines, capture=False)
    assert resolved["v1"].state == "finalized" and resolved["v1"].snapshot.directory == copy


def test_panels_needing_v2_fail_resolution_without_substitution(
    source_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baselines = tmp_path / "bench" / "baselines"
    for panel in ("heldout-a", "heldout-b", "attribution"):
        with pytest.raises(VersionUnavailable) as raised:
            resolve_sources(panel, source_root=source_tree, baselines_dir=baselines, capture=True)
        assert raised.value.code == "version_not_packaged"
        assert "bots.jev.v2 is not packaged" in raised.value.message
        assert "no other version or the current runtime is substituted" in raised.value.message
    assert not baselines.exists()  # the current v1 source is never captured instead
    code = benchmark.main(
        ["--panel", "attribution", "--dry-run", "--benchmark-root", str(tmp_path / "bench")],
        source_root=source_tree,
    )
    assert code == 1 and "version_not_packaged" in capsys.readouterr().err
    assert not (tmp_path / "bench").exists()


def test_a_candidate_is_frozen_explicitly_and_then_resolves(
    source_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _own_tree(source_tree, tmp_path)
    _package_v2(root)
    bench = tmp_path / "bench"
    common = ["--benchmark-root", str(bench)]
    assert benchmark.main(["--panel", "heldout-a", "--dry-run", *common], source_root=root) == 1
    err = capsys.readouterr().err
    assert "candidate_not_frozen" in err and "--freeze-candidate v2" in err
    assert benchmark.main(["--freeze-candidate", "v1", *common], source_root=root) == 2
    assert "baseline" in capsys.readouterr().err  # v1 is frozen by the baseline preflight only
    assert benchmark.main(["--freeze-candidate", "v2", *common], source_root=root) == 0
    assert "froze v2" in capsys.readouterr().out
    frozen = read_finalized(bench / "baselines", "v2")
    assert frozen is not None
    finalize_baseline(bench / "baselines", capture_source(root, "v1", bench / "baselines"))
    (root / "bots" / "jev" / "v2" / "manifest.json").write_text("{}")  # later edits never leak
    plan = tmp_path / "plan.json"
    code = benchmark.main(
        ["--panel", "heldout-a", "--dry-run", *common, "--json", str(plan)], source_root=root
    )
    assert code == 0, capsys.readouterr().err
    document = json.loads(plan.read_text())
    assert document["sources"]["v2"]["fingerprint"] == frozen
    assert {s["state"] for s in document["sources"].values()} == {"finalized"}
    assert [c["argv"][2] for c in document["cases"]][:2] == ["bots.jev.v1", "bots.jev.v2"]
    shutil.rmtree(root / "bots" / "jev" / "v2")
    _package_v2(root)
    (root / "bots" / "jev" / "v2" / "__init__.py").write_text("# changed\n")
    with pytest.raises(ProvenanceMismatch, match="never replaced"):  # write-once
        benchmark.freeze_candidate(root, "v2", bench / "baselines")


def test_a_snapshot_with_unexpected_folders_or_too_many_entries_fails(
    snapshot_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy = _own_snapshot(snapshot_dir, tmp_path)
    verify_snapshot(copy)
    (copy / "src" / "jev" / "empty").mkdir()  # no file in it: only a folder comparison sees it
    with pytest.raises(ProvenanceMismatch, match="unexpected folders"):
        verify_snapshot(copy)
    (copy / "src" / "jev" / "empty").rmdir()
    verify_snapshot(copy)
    if hasattr(os, "mkfifo"):  # POSIX: a special file is rejected by name
        os.mkfifo(copy / "src" / "jev" / "pipe")
        with pytest.raises(ProvenanceMismatch, match="special file: src/jev/pipe"):
            verify_snapshot(copy)
        (copy / "src" / "jev" / "pipe").unlink()
    monkeypatch.setattr(benchmark, "_MAX_SNAPSHOT_ENTRIES", 10)
    with pytest.raises(ProvenanceMismatch, match="more than 10 entries: ") as raised:
        verify_snapshot(copy)  # rejected, never silently truncated
    assert (copy / raised.value.message.rsplit(": ", 1)[1]).exists()  # names its entry


# ---------------------------------------------------------------------------
# Dry run: exact resolution, no service call, no launch, no write
# ---------------------------------------------------------------------------


@pytest.fixture
def no_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a dry run must not launch, call the service or probe")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(TypesafeProvider, "decide", forbidden)
    monkeypatch.setattr(benchmark, "probe_model", forbidden)


@pytest.mark.usefixtures("no_side_effects")
def test_cli_dry_run_resolves_the_exact_production_command_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load_script()
    bench, runs, report = tmp_path / "bench", tmp_path / "runs", tmp_path / "plan.json"
    code = cli.main(
        [
            "--panel",
            "baseline",
            "--dry-run",
            "--benchmark-root",
            str(bench),
            "--run-root",
            str(runs),
            "--json",
            str(report),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert not bench.exists() and not runs.exists()
    plan = json.loads(report.read_text())
    assert plan["dry_run"] is True and len(plan["cases"]) == 6
    assert plan["sources"]["v1"]["policy_hash"] == V1_HASH
    assert plan["sources"]["v1"]["state"] == "capture_pending"
    first = plan["cases"][0]
    assert first["argv"][1:3] == ["-m", "bots.jev.v1"]
    assert first["options"] == {
        "map_name": "Simple64",
        "opponent_race": "Terran",
        "difficulty": 3,
        "seed": 11,
        "max_game_seconds": 900,
        "max_wall_seconds": 1080,
        "realtime": True,
        "decision_provider": "typesafe",
        "decision_model": "jev-1.13.0",
        "decision_max_requests": 450,
    }
    argv = first["argv"]
    assert argv[argv.index("--run-root") + 1] == str(runs) and "--realtime" in argv
    assert first["cwd"].endswith(plan["sources"]["v1"]["snapshot"])
    assert "jev-latest" not in json.dumps(plan)
    assert "--decision-model jev-1.13.0" in out and V1_HASH in out
    # Dashboard first by default (plan D7): each child joins the session a real run
    # creates; the dry run itself opens nothing (no_side_effects forbids any Popen).
    assert plan["launch"] == "dashboard" and "launch: dashboard" in out
    assert argv[-2:] == ["--launch-session", "<session_id assigned at start>"]
    assert "http://localhost:3000/?tab=jev&launch=<session_id>" in out
    assert "waits for Step 224" not in out


@pytest.mark.usefixtures("no_side_effects")
def test_a_no_dashboard_dry_run_plans_headless_children(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "plan.json"
    code = benchmark.main(
        ["--panel", "baseline", "--dry-run", "--no-dashboard", "--json", str(report)],
        source_root=REPO,
    )
    out = capsys.readouterr().out
    plan = json.loads(report.read_text())
    assert code == 0 and plan["launch"] == "headless" and "launch: headless" in out
    assert all("--launch-session" not in case["argv"] for case in plan["cases"])
    assert "no dashboard is opened" in out


def test_the_script_runs_as_a_command(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--panel",
            "staging",
            "--dry-run",
            "--benchmark-root",
            str(tmp_path / "bench"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "-m bots.jev.v1" in proc.stdout and "--decision-provider scripted" in proc.stdout
    assert not (tmp_path / "bench").exists()


def test_official_panels_refuse_to_play_before_launch_integration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Step 224 enabled play (the frozen baseline now carries the launch hook); a build
    # without the launch integration keeps refusing before anything is captured.
    assert benchmark.OFFICIAL_PANEL_PLAY_ENABLED is True
    monkeypatch.setattr(benchmark, "OFFICIAL_PANEL_PLAY_ENABLED", False)
    child = _FakeChild()
    code = benchmark.main(
        ["--panel", "baseline", "--benchmark-root", str(tmp_path / "bench")],
        launcher=child,
        environ={KEY: FAKE_KEY},
    )
    assert code == 1 and child.launches == []
    assert "launch_integration_pending" in capsys.readouterr().err
    assert not (tmp_path / "bench").exists()  # nothing captured, nothing finalized


# ---------------------------------------------------------------------------
# Scoring and calibration: evidence, never transcripts
# ---------------------------------------------------------------------------


def test_a_verified_scripted_win_scores_as_a_win(tmp_path: Path) -> None:
    options = _options(SCRIPTED)
    run_id = _play(tmp_path, options)
    scored = score_run(tmp_path, run_id, _expect(options))
    assert (scored.status, scored.result, scored.counted_as_win) == ("complete", "win", True)
    assert scored.evidence == "diagnostics" and scored.findings == ()
    assert scored.metrics["decisions"]["provider"] == "scripted"  # type: ignore[index]


@pytest.mark.parametrize(
    ("tamper", "reason"),
    [
        (lambda d: d.update(entrypoint="bots.jev.v9"), "provenance_mismatch"),
        (lambda d: d["options"].update(decision_provider="typesafe"), "provenance_mismatch"),
        (lambda d: d["options"].update(decision_model="jev-latest"), "provenance_mismatch"),
        (lambda d: d["options"].update(realtime=True), "provenance_mismatch"),
        (lambda d: d["outcome"].update(result="loss"), "corrupt_evidence"),
        (lambda d: d.update(kind="something_else"), "corrupt_evidence"),
    ],
    ids=["entrypoint", "provider-fallback", "model", "realtime", "outcome", "kind"],
)
def test_provenance_or_evidence_mismatch_is_invalid_never_a_win(
    tmp_path: Path, tamper: Callable[[dict[str, Any]], None], reason: str
) -> None:
    options = _options(SCRIPTED)
    run_id = _play(tmp_path, options)
    _rewrite_diagnostics(tmp_path / run_id, tamper)
    scored = score_run(tmp_path, run_id, _expect(options))
    assert scored.status == "invalid" and scored.reason == reason
    assert scored.reported_result == "win" and not scored.counted_as_win and scored.result is None


def test_wrong_policy_or_case_options_are_provenance_mismatches(tmp_path: Path) -> None:
    options = _options(SCRIPTED)
    run_id = _play(tmp_path, options)
    for expectation in (
        _expect(options, policy_hash="f" * 64),
        _expect(options, version=2, entrypoint="bots.jev.v2"),
        _expect(replace(options, difficulty=4)),
        _expect(replace(options, opponent_race="Zerg")),
        _expect(replace(options, max_wall_seconds=1200)),
    ):
        scored = score_run(tmp_path, run_id, expectation)
        assert (scored.status, scored.reason, scored.counted_as_win) == (
            "invalid",
            "provenance_mismatch",
            False,
        )
    # A policy archive that no longer matches its run is corrupt provenance too.
    policy = tmp_path / run_id / "policy.json"
    tampered = json.loads(policy.read_text())
    tampered["parameters"]["first_attack_zealots"] = 1
    policy.write_text(json.dumps(tampered))
    scored = score_run(tmp_path, run_id, _expect(options))
    assert scored.reason == "provenance_mismatch" and not scored.counted_as_win


def test_missing_replay_or_diagnostics_is_never_a_valid_win(tmp_path: Path) -> None:
    options = _options(SCRIPTED)
    no_replay = _play(tmp_path, options, _GameLauncher(replay=False))
    scored = score_run(tmp_path, no_replay, _expect(options))
    assert (scored.reason, scored.counted_as_win) == ("corrupt_evidence", False)
    run_id = _play(tmp_path, options)
    (tmp_path / run_id / DIAGNOSTICS_FILE).unlink()
    assert score_run(tmp_path, run_id, _expect(options)).reason == "corrupt_evidence"
    legacy = score_run(tmp_path, run_id, _expect(options, require_diagnostics=False))
    assert (legacy.status, legacy.result, legacy.evidence) == ("complete", "win", "legacy_trace")


def test_draws_timeouts_and_crashes_are_reported_separately_never_wins(tmp_path: Path) -> None:
    options = _options(SCRIPTED, max_game_seconds=60)

    def past_limit(game: SimpleNamespace, controller: JevController, index: int) -> None:
        if index == 2:
            game.state.game_loop = 22 * 70

    cases = {
        "draw": _GameLauncher("draw"),
        "timeout": _GameLauncher(before_step=past_limit),
        "error": _GameLauncher(crash=True, replay=False),
    }
    for expected, launcher in cases.items():
        scored = score_run(tmp_path, _play(tmp_path, options, launcher), _expect(options))
        assert (scored.status, scored.result, scored.counted_as_win) == (
            "complete",
            expected,
            False,
        ), scored.findings


def test_the_child_summary_and_exit_code_must_agree_with_the_archive(tmp_path: Path) -> None:
    options = _options(SCRIPTED)
    run_id = _play(tmp_path, options)
    expectation = _expect(options)
    agreeing = ChildEvidence(ChildExit(0, 1.0), "finished", "win", run_id)
    assert score_run(tmp_path, run_id, expectation, agreeing).counted_as_win
    for child in (
        ChildEvidence(ChildExit(0, 1.0), "finished", "loss", run_id),
        ChildEvidence(ChildExit(1, 1.0), "finished", "win", run_id),
        ChildEvidence(ChildExit(0, 1.0), "finished", "win", uuid.uuid4().hex),
    ):
        scored = score_run(tmp_path, run_id, expectation, child)
        assert scored.reason == "corrupt_evidence" and not scored.counted_as_win
    killed = ChildEvidence(ChildExit(None, 1200.0, timed_out=True), "finished", "win", run_id)
    assert score_run(tmp_path, run_id, expectation, killed).reason == "infrastructure_failure"
    unreported = ChildEvidence(ChildExit(1, 1.0), None, None, None)
    scored = score_run(tmp_path, None, expectation, unreported)
    assert (scored.status, scored.reason) == ("invalid", "infrastructure_failure")


def test_a_run_outside_its_frozen_snapshot_is_a_fallback_source(
    tmp_path: Path, snapshot_dir: Path
) -> None:
    options = _options(SCRIPTED)
    run_id = _play(tmp_path, options)  # ran from this checkout, not the snapshot
    scored = score_run(tmp_path, run_id, _expect(options, snapshot_dir=snapshot_dir))
    assert scored.reason == "provenance_mismatch"
    assert any("fallback source path" in f.message for f in scored.findings)


def test_hosted_runs_verify_every_answer_model_and_require_an_accepted_answer(
    tmp_path: Path,
) -> None:
    options = _options(HOSTED)
    good = _play(tmp_path, options)
    scored = score_run(tmp_path, good, _expect(options))
    assert scored.counted_as_win, scored.findings
    decisions = scored.metrics["decisions"]
    assert isinstance(decisions, dict)
    assert decisions["returned_models"] == {PINNED: decisions["answers"]}
    assert decisions["accepted"] >= 1 and decisions["requested_model"] == PINNED
    assert scored.verified_calls == decisions["calls"] >= 1

    drifted = _play(tmp_path, options, model="jev-1.14.0")
    scored = score_run(tmp_path, drifted, _expect(options))
    assert (scored.reason, scored.counted_as_win) == ("model_drift", False)

    latest = replace(options, decision_model="jev-latest")
    alias = _play(tmp_path, latest)
    scored = score_run(tmp_path, alias, _expect(options))
    assert (scored.reason, scored.counted_as_win) == ("provenance_mismatch", False)

    # No request is ever sent when only one army option exists: not a hosted case.
    silent = _play(tmp_path, options, _GameLauncher(hosted=False))
    scored = score_run(tmp_path, silent, _expect(options))
    assert (scored.reason, scored.counted_as_win) == ("no_accepted_hosted_decision", False)


def _legacy_hosted_archive(tmp_path: Path) -> tuple[Path, MatchOptions]:
    """The JI archive's shape: a hosted win requested as ``jev-latest``, answered by the
    pinned model, without the diagnostic summary (policy, state, metadata, trace, replay)."""
    options = replace(
        _options(HOSTED), decision_model="jev-latest", max_wall_seconds=1800, difficulty=1, seed=1
    )
    run_id = _play(tmp_path / "runs", options)
    run_dir = tmp_path / "runs" / run_id
    (run_dir / DIAGNOSTICS_FILE).unlink()
    assert sorted(p.name for p in run_dir.iterdir()) == [
        "events.1.jsonl",
        "metadata.json",
        "policy.json",
        REPLAY_FILE,
        "state.json",
    ]
    return run_dir, options


def test_calibration_scores_a_legacy_winning_archive_and_rejects_mismatches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir, _ = _legacy_hosted_archive(tmp_path)
    expect = [
        "--calibrate-run",
        str(run_dir),
        "--expect-race",
        "Terran",
        "--expect-difficulty",
        "1",
        "--expect-seed",
        "1",
        "--expect-max-wall-seconds",
        "1800",
    ]
    report = tmp_path / "calibration.json"
    code = benchmark.main(
        [*expect, "--expect-requested-model", "jev-latest", "--json", str(report)]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "VALID" in out and "counted_as_win=True" in out and "evidence=legacy_trace" in out
    assert json.loads(report.read_text())["counted_as_win"] is True

    # The same archive against the benchmark pin: requested jev-latest, never a valid win.
    assert benchmark.main(expect) == 1
    out = capsys.readouterr().out
    assert "INVALID" in out and "counted_as_win=False" in out
    assert "reason=provenance_mismatch" in out and "requested model 'jev-latest'" in out

    # Requiring the diagnostic summary rejects the legacy archive outright.
    assert (
        benchmark.main([*expect, "--expect-requested-model", "jev-latest", "--require-diagnostics"])
        == 1
    )
    out = capsys.readouterr().out
    assert "reason=corrupt_evidence" in out and "diagnostics.json is missing" in out


def test_legacy_trace_accounting_matches_the_live_accumulator(tmp_path: Path) -> None:
    options = _options(HOSTED)
    run_id = _play(tmp_path, options)
    live = _diagnostics(tmp_path / run_id)["metrics"]["decisions"]
    (tmp_path / run_id / DIAGNOSTICS_FILE).unlink()
    legacy = score_run(tmp_path, run_id, _expect(options, require_diagnostics=False))
    traced = legacy.metrics["decisions"]
    assert isinstance(traced, dict)
    for name in (
        "calls",
        "answers",
        "accepted",
        "stale",
        "returned_models",
        "input_tokens",
        "output_tokens",
        "requested_model",
        "provider",
    ):
        assert traced[name] == live[name], name


# ---------------------------------------------------------------------------
# Batches: lock, persistence, interruption, resume, budget
# ---------------------------------------------------------------------------


def test_one_process_owns_a_batch_and_a_stale_lock_needs_a_dead_owner(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    store = _store(tmp_path, _sources(snapshot_dir))
    lock = store.batch_dir / benchmark.LOCK_FILE
    assert json.loads(lock.read_text())["pid"] == 4242
    with pytest.raises(LockHeld, match="owned by running process 4242"):
        BatchStore.open(tmp_path / "bench", store.manifest.batch_id, probe=_Probe(alive=True))
    # The owner is verifiably gone: the lock is retired (kept) and taken over.
    taker = BatchStore.open(tmp_path / "bench", store.manifest.batch_id, probe=_Probe(alive=False))
    assert json.loads(lock.read_text())["invocation_id"] == taker.owner.invocation_id
    assert len(list(store.batch_dir.glob("lock.stale-*.json"))) == 1
    taker.release()
    assert not lock.exists()
    # A lock from another host cannot be verified: refused.
    record = taker.owner.to_dict() | {"host": "some-other-host"}
    lock.write_text(json.dumps(record))
    with pytest.raises(LockHeld, match="host"):
        BatchStore.open(tmp_path / "bench", store.manifest.batch_id, probe=_Probe(alive=False))


def test_the_system_process_probe_tells_live_from_gone() -> None:
    probe = SystemProcessProbe()
    pid, started = probe.current()
    assert pid == os.getpid() and probe.alive(pid, started)
    if started is not None:
        assert not probe.alive(pid, started + 3600)  # same pid, another process start
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait(timeout=60)
    assert not probe.alive(finished.pid, None)


def test_an_interrupted_batch_resumes_without_overwriting_completed_cases(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    sources = _sources(snapshot_dir)
    runs = tmp_path / "runs"
    store = _store(tmp_path, sources, cases=_THREE)
    child = _FakeChild({_THREE[1].case_id: "interrupt"})
    outcome = run_batch(store, sources, launcher=child, run_root=runs, environ={})
    store.release()
    assert (outcome.status, outcome.exit_code) == ("interrupted", 130)
    first, second, third = outcome.cases
    assert (first.status, first.result) == ("complete", "win")
    assert (second.status, second.reason) == ("invalid", "interrupted")
    assert third.status == "pending"
    saved = json.loads((store.batch_dir / benchmark.RESULTS_FILE).read_text())
    assert [c["status"] for c in saved["cases"]] == ["complete", "invalid", "pending"]
    completed = saved["cases"][0]
    lines_before = (store.batch_dir / benchmark.ATTEMPTS_FILE).read_text()

    # Resume without an explicit retry: the completed case is skipped, the interrupted one kept.
    child = _FakeChild()
    resumed = BatchStore.open(
        tmp_path / "bench", store.manifest.batch_id, probe=_Probe(alive=False)
    )
    outcome = run_batch(resumed, sources, launcher=child, run_root=runs, environ={})
    resumed.release()
    assert [launch.argv[launch.argv.index("--opponent-race") + 1] for launch in child.launches] == [
        "Zerg"
    ]
    assert (outcome.status, outcome.exit_code) == ("incomplete", 1)
    assert [c.status for c in outcome.cases] == ["complete", "invalid", "complete"]

    # Explicit retry replays only the interrupted case.
    child = _FakeChild()
    retried = BatchStore.open(
        tmp_path / "bench", store.manifest.batch_id, probe=_Probe(alive=False)
    )
    outcome = run_batch(
        retried, sources, launcher=child, run_root=runs, environ={}, retry_interrupted=True
    )
    retried.release()
    assert len(child.launches) == 1 and (outcome.status, outcome.exit_code) == ("complete", 0)
    assert outcome.cases[1].attempts == 2 and outcome.cases[1].result == "win"
    saved = json.loads((store.batch_dir / benchmark.RESULTS_FILE).read_text())
    assert saved["cases"][0] == completed  # never overwritten
    assert len(saved["invocations"]) == 3
    attempts = (store.batch_dir / benchmark.ATTEMPTS_FILE).read_text()
    assert attempts.startswith(lines_before)  # append-only
    manifest = (store.batch_dir / benchmark.MANIFEST_FILE).read_bytes()
    assert benchmark.read_manifest(store.batch_dir)[1] == saved["manifest_sha256"]
    assert manifest == benchmark._encode(store.manifest.to_dict())


def test_a_case_left_running_by_a_dead_benchmark_is_labeled_interrupted(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, cases=_THREE[:2])
    cases = store.read_cases()
    cases[0] = replace(cases[0], status="running", attempts=1)
    store.write_results(cases, [], "running", None)  # the benchmark died mid-match
    child = _FakeChild()
    resumed = BatchStore.open(
        tmp_path / "bench", store.manifest.batch_id, probe=_Probe(alive=False)
    )
    outcome = run_batch(resumed, sources, launcher=child, run_root=tmp_path / "runs", environ={})
    resumed.release()
    assert (outcome.cases[0].status, outcome.cases[0].reason) == ("invalid", "interrupted")
    assert outcome.cases[1].status == "complete" and len(child.launches) == 1
    events = [a["event"] for a in _attempts(store)]
    assert "interrupted_detected" in events


def test_infrastructure_failure_stops_the_batch(snapshot_dir: Path, tmp_path: Path) -> None:
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, cases=_THREE)
    child = _FakeChild({_THREE[0].case_id: "silent"})
    outcome = run_batch(store, sources, launcher=child, run_root=tmp_path / "runs", environ={})
    store.release()
    assert (outcome.status, outcome.stop_reason, outcome.exit_code) == (
        "stopped",
        "infrastructure_failure",
        1,
    )
    assert len(child.launches) == 1 and [c.status for c in outcome.cases[1:]] == ["pending"] * 2


def test_a_snapshot_modified_between_cases_stops_the_batch(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    copy = _own_snapshot(snapshot_dir, tmp_path)
    sources = _sources(copy)

    class Tampering(_FakeChild):
        def run(self, launch: ChildLaunch) -> ChildExit:
            result = super().run(launch)
            (copy / "src" / "jev" / "bot.py").write_text("# swapped runtime\n")
            return result

    store = _store(tmp_path, sources, cases=_THREE)
    outcome = run_batch(
        store, sources, launcher=Tampering(), run_root=tmp_path / "runs", environ={}
    )
    store.release()
    assert (outcome.cases[0].status, outcome.cases[0].reason) == ("invalid", "provenance_mismatch")
    assert outcome.cases[0].result is None  # the win it played is not counted
    assert outcome.stop_reason == "provenance_mismatch" and outcome.cases[1].status == "pending"


def test_child_launch_is_explicit_and_scripted_children_never_see_the_key(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, cases=_THREE[:1])
    child = _FakeChild()
    environ = {KEY: FAKE_KEY, "PYTHONPATH": "elsewhere", "SC2PATH": "sc2"}
    run_batch(store, sources, launcher=child, run_root=tmp_path / "runs", environ=environ)
    store.release()
    (launch,) = child.launches
    assert launch.cwd == snapshot_dir and launch.hard_wall_seconds == 1200
    assert KEY not in launch.env and launch.env["SC2PATH"] == "sc2"
    assert launch.env["PYTHONPATH"].split(os.pathsep) == [
        str(snapshot_dir / "src"),
        str(snapshot_dir),
    ]
    assert launch.env["PYTHONDONTWRITEBYTECODE"] == "1"
    hosted = child_environment(environ, snapshot_dir, hosted=True)
    assert hosted[KEY] == FAKE_KEY


def test_the_budget_never_starts_a_match_without_its_full_allowance() -> None:
    budget = benchmark._Budget(BenchmarkLimits(), started=0.0)
    budget.requests = 2250
    assert budget.refusal(HOSTED, 0.0) is None
    budget.requests = 2251
    assert "requests" in (budget.refusal(HOSTED, 0.0) or "")
    assert budget.refusal(SCRIPTED, 0.0) is None  # scripted matches use no service requests
    budget.requests, budget.games = 0, 6
    assert "games" in (budget.refusal(SCRIPTED, 0.0) or "")
    budget.games = 0
    assert budget.refusal(SCRIPTED, 6000.0) is None
    assert "wall seconds" in (budget.refusal(SCRIPTED, 6001.0) or "")


def test_hosted_budget_counts_the_probe_and_crashed_matches_conservatively(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    sources = _sources(snapshot_dir)
    environ = {KEY: FAKE_KEY}
    # The probe used one request: a 450-request match no longer fits in 450.
    store = _store(
        tmp_path / "a", sources, panel="baseline", limits=BenchmarkLimits(invocation_requests=450)
    )
    child = _FakeChild()
    outcome = run_batch(
        store,
        sources,
        launcher=child,
        run_root=tmp_path / "runs",
        environ=environ,
        probe_requests=1,
    )
    store.release()
    assert (outcome.status, outcome.exit_code, child.launches) == ("budget_exhausted", 3, [])

    # A crashed hosted match is charged its whole allowance; a verified one its real calls.
    cases = panel_cases("baseline")
    store = _store(
        tmp_path / "b", sources, panel="baseline", limits=BenchmarkLimits(invocation_requests=900)
    )
    child = _FakeChild({cases[0].case_id: "crash"})
    outcome = run_batch(store, sources, launcher=child, run_root=tmp_path / "runs", environ=environ)
    store.release()
    assert len(child.launches) == 2 and outcome.status == "budget_exhausted"
    crashed, won = outcome.cases[0], outcome.cases[1]
    assert (crashed.status, crashed.reason) == ("invalid", "no_accepted_hosted_decision")
    assert (won.status, won.result) == ("complete", "win")
    ended = [a for a in _attempts(store) if a["event"] == "ended"]
    assert ended[0]["charged_requests"] == 450
    calls = won.metrics["decisions"]["calls"]  # type: ignore[index]
    assert 1 <= ended[1]["charged_requests"] == calls < 450
    saved = json.loads((store.batch_dir / benchmark.RESULTS_FILE).read_text())
    assert saved["invocations"][-1]["requests_charged"] == 450 + calls


def test_cli_baseline_flow_probes_finalizes_plays_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The full CLI path, explicitly headless (fakes for the child and service)."""
    bench, runs = tmp_path / "bench", tmp_path / "runs"
    probed: list[str] = []

    def factory(key: str, config: DecisionConfig) -> TypesafeProvider:
        probed.append(config.model)
        return TypesafeProvider(key, config, transport=_service())

    common = ["--benchmark-root", str(bench), "--run-root", str(runs), "--no-dashboard"]
    assert benchmark.main(["--panel", "baseline", *common], environ={}) == 1
    assert "missing_configuration" in capsys.readouterr().err and not bench.exists()

    child = _FakeChild()
    code = benchmark.main(
        ["--panel", "baseline", "--max-games", "2", *common],
        environ={KEY: FAKE_KEY},
        launcher=child,
        provider_factory=factory,
    )
    out = capsys.readouterr().out
    assert code == 3 and probed == [PINNED] and len(child.launches) == 2
    assert "probe ok: model=jev-1.13.0" in out
    (batch_dir,) = [p for p in bench.iterdir() if is_valid_run_id(p.name)]
    finalized = read_finalized(bench / "baselines", "v1")
    manifest = json.loads((batch_dir / "manifest.json").read_text())
    assert manifest["sources"]["v1"]["fingerprint"] == finalized
    assert manifest["sources"]["v1"]["state"] == "finalized"

    # Resume rejects changed options; the original options resume the batch.
    assert (
        benchmark.main(
            ["--resume", batch_dir.name, "--max-games", "3", *common],
            environ={KEY: FAKE_KEY},
            launcher=child,
            provider_factory=factory,
        )
        == 1
    )
    assert "new options require a new batch" in capsys.readouterr().err
    code = benchmark.main(
        ["--resume", batch_dir.name, *common],
        environ={KEY: FAKE_KEY},
        launcher=child,
        provider_factory=factory,
    )
    assert code == 3 and len(child.launches) == 4 and probed == [PINNED, PINNED]
    saved = json.loads((batch_dir / "results.json").read_text())
    assert [c["status"] for c in saved["cases"]] == ["complete"] * 4 + ["pending"] * 2
    assert saved["scorecard"]["valid_wins"] == 4

    # A changed pin or a modified frozen source is a new batch, never a resume.
    monkeypatch.setattr(benchmark, "PINNED_MODEL", "jev-2.0.0")
    assert (
        benchmark.main(
            ["--resume", batch_dir.name, *common],
            environ={KEY: FAKE_KEY},
            launcher=child,
            provider_factory=factory,
        )
        == 1
    )
    assert "a new model requires a new batch" in capsys.readouterr().err
    monkeypatch.setattr(benchmark, "PINNED_MODEL", PINNED)
    snapshot = bench / "baselines" / manifest["sources"]["v1"]["snapshot"]
    (snapshot / "src" / "jev" / "runtime.py").write_text("# swapped\n")
    assert (
        benchmark.main(
            ["--resume", batch_dir.name, *common],
            environ={KEY: FAKE_KEY},
            launcher=child,
            provider_factory=factory,
        )
        == 1
    )
    assert "provenance_mismatch" in capsys.readouterr().err
    assert len(child.launches) == 4  # neither rejected resume launched anything

    with pytest.raises(SystemExit, match="2"):  # a report opens no dashboard to refuse
        benchmark.main(["--report", batch_dir.name, *common])
    assert "--no-dashboard applies to --panel and --resume" in capsys.readouterr().err
    assert benchmark.main(["--report", batch_dir.name, *common[:-1]]) == 0
    assert '"valid_wins": 4' in capsys.readouterr().out


def test_a_drifted_probe_stops_before_anything_is_captured_or_played(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    child = _FakeChild()

    def factory(key: str, config: DecisionConfig) -> TypesafeProvider:
        return TypesafeProvider(key, config, transport=_service("jev-latest-alias"))

    code = benchmark.main(
        ["--panel", "baseline", "--benchmark-root", str(tmp_path / "b"), "--no-dashboard"],
        environ={KEY: FAKE_KEY},
        launcher=child,
        provider_factory=factory,
    )
    assert code == 1 and child.launches == []
    assert "model_drift" in capsys.readouterr().err
    assert not (tmp_path / "b").exists()


class _InterruptedAfterPlay(_FakeChild):
    """Plays the match fully, then reports the case interrupted (Ctrl+C after play)."""

    def run(self, launch: ChildLaunch) -> ChildExit:
        played = super().run(launch)
        return replace(played, interrupted=True)


def test_a_retried_case_keeps_every_attempts_spend(snapshot_dir: Path, tmp_path: Path) -> None:
    sources = _sources(snapshot_dir)
    hosted = panel_cases("baseline")[:1]
    store = _store(tmp_path, sources, panel="baseline", cases=hosted)
    environ = {KEY: FAKE_KEY}
    outcome = run_batch(
        store,
        sources,
        launcher=_InterruptedAfterPlay(),
        run_root=tmp_path / "runs",
        environ=environ,
    )
    store.release()
    first = outcome.cases[0]
    assert (first.status, first.reason) == ("invalid", "interrupted")
    first_spend = dict(first.spent)
    assert first_spend["calls"] >= 1 and first_spend["input_tokens"] > 0
    resumed = BatchStore.open(
        tmp_path / "bench", store.manifest.batch_id, probe=_Probe(alive=False)
    )
    outcome = run_batch(
        resumed,
        sources,
        launcher=_FakeChild(),
        run_root=tmp_path / "runs",
        environ=environ,
        retry_interrupted=True,
    )
    resumed.release()
    case = outcome.cases[0]
    assert (case.status, case.result, case.attempts) == ("complete", "win", 2)
    second = benchmark._attempt_spend(case.metrics)
    assert second is not None
    assert dict(case.spent) == {k: first_spend[k] + second[k] for k in first_spend}
    saved = json.loads((store.batch_dir / benchmark.RESULTS_FILE).read_text())
    service = saved["scorecard"]["service"]
    assert service["calls"] == first_spend["calls"] + second["calls"]
    assert service["input_tokens"] == first_spend["input_tokens"] + second["input_tokens"]
    assert service["complete"] == second and service["other"] == first_spend
    assert case.unrecorded_attempts == 0 and service["unrecorded_hosted_attempts"] == 0
    ended = [a["spend"] for a in _attempts(store) if a["event"] == "ended"]
    assert ended == [first_spend, second]  # the append-only record keeps both


def test_a_hosted_attempt_without_a_decision_record_counts_unrecorded(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    sources = _sources(snapshot_dir)
    hosted = panel_cases("baseline")[:1]
    store = _store(tmp_path, sources, panel="baseline", cases=hosted)
    child = _FakeChild({hosted[0].case_id: "silent"})  # exits without a run: no record
    outcome = run_batch(
        store, sources, launcher=child, run_root=tmp_path / "runs", environ={KEY: FAKE_KEY}
    )
    store.release()
    case = outcome.cases[0]
    assert (case.status, case.reason, case.unrecorded_attempts) == (
        "invalid",
        "infrastructure_failure",
        1,
    )
    saved = json.loads((store.batch_dir / benchmark.RESULTS_FILE).read_text())
    service = saved["scorecard"]["service"]
    assert (service["unrecorded_hosted_attempts"], service["calls"]) == (1, 0)


def test_ctrl_c_while_a_hosted_case_is_recorded_counts_it_unrecorded(
    snapshot_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = _sources(snapshot_dir)
    hosted = panel_cases("baseline")[:1]
    store = _store(tmp_path, sources, panel="baseline", cases=hosted)

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(benchmark, "score_run", interrupted)
    outcome = run_batch(
        store, sources, launcher=_FakeChild(), run_root=tmp_path / "runs", environ={KEY: FAKE_KEY}
    )
    store.release()
    case = outcome.cases[0]
    assert (case.status, case.reason, case.unrecorded_attempts) == ("invalid", "interrupted", 1)
    saved = json.loads((store.batch_dir / benchmark.RESULTS_FILE).read_text())
    assert saved["scorecard"]["service"]["unrecorded_hosted_attempts"] == 1


def test_a_hosted_case_left_running_counts_unrecorded_on_resume(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    sources = _sources(snapshot_dir)
    hosted = panel_cases("baseline")[:1]
    store = _store(tmp_path, sources, panel="baseline", cases=hosted)
    cases = store.read_cases()
    store.write_results([replace(cases[0], status="running", attempts=1)], [], "running", None)
    store.release()  # the benchmark died mid-match
    resumed = BatchStore.open(
        tmp_path / "bench", store.manifest.batch_id, probe=_Probe(alive=False)
    )
    outcome = run_batch(
        resumed, sources, launcher=_FakeChild(), run_root=tmp_path / "runs", environ={KEY: FAKE_KEY}
    )
    resumed.release()
    case = outcome.cases[0]
    assert (case.status, case.reason, case.unrecorded_attempts) == ("invalid", "interrupted", 1)
    saved = json.loads((store.batch_dir / benchmark.RESULTS_FILE).read_text())
    assert saved["scorecard"]["service"]["unrecorded_hosted_attempts"] == 1


# ---------------------------------------------------------------------------
# Model probe (production client over a mock transport)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("transport", "ok", "reason"),
    [
        (_service(PINNED), True, None),
        (_service("jev-1.14.0"), False, "model_drift"),
        (_service(status=401), False, "authentication_failed"),
        (_service(status=403), False, "authentication_failed"),
        (_service(status=404), False, "model_unavailable"),
        (_service(status=500), False, "model_unavailable"),
    ],
    ids=["pinned", "drift", "401", "403", "404", "500"],
)
def test_the_probe_requires_the_pinned_model(
    transport: httpx.MockTransport, ok: bool, reason: str | None
) -> None:
    def factory(key: str, config: DecisionConfig) -> TypesafeProvider:
        assert config.model == PINNED and config.max_requests == 1
        return TypesafeProvider(key, config, transport=transport)

    result = probe_model(FAKE_KEY, factory=factory)
    assert (result.ok, result.reason) == (ok, reason)
    assert FAKE_KEY not in json.dumps(result.to_dict())


# ---------------------------------------------------------------------------
# Observation-only metrics (production runner / controller path)
# ---------------------------------------------------------------------------


def _entity(tag: int, kind: str, **fields: Any) -> Entity:
    values: dict[str, Any] = {"position": START, "health": 100.0}
    values.update(fields)
    return Entity(tag, kind, **values)


def _obs(
    seconds: float,
    *,
    minerals: int = 0,
    supply: tuple[int, int] = (12, 30),
    units: tuple[Entity, ...] = (),
    structures: tuple[Entity, ...] = (),
) -> Observation:
    return Observation(
        round(seconds * 22.4),
        seconds,
        minerals,
        supply[0],
        supply[1],
        units,
        structures
        or (_entity(1, "Nexus", is_structure=True, ready=True, idle=False, powered=True),),
        (),
        (),
        START,
        ((120.5, 120.5),),
        (START,),
        (75.5, 75.5),
    )


def test_metrics_integrate_samples_and_label_gaps_as_missing_coverage() -> None:
    metrics = MatchMetrics()
    for step in range(5):  # 0.0 .. 1.0 at 0.25 s
        metrics.sample(_obs(step * 0.25, minerals=100), attack_launched=False)
    metrics.sample(_obs(4.0, minerals=500), attack_launched=False)  # a 3 s gap: missed
    summary = metrics.summary(end_game_seconds=4.0)
    sampling = summary["sampling"]
    assert isinstance(sampling, dict)
    assert sampling["covered_game_seconds"] == 1.0 and sampling["missed_game_seconds"] == 3.0
    assert summary["status"] == "incomplete"
    assert summary["mean_mineral_bank"] == {"status": "measured", "value": 100.0}  # gap excluded
    assert METRIC_SAMPLE_GAP_SECONDS == 1.0


def test_unknown_metrics_are_labeled_never_zero() -> None:
    empty = MatchMetrics().summary(end_game_seconds=30.0)
    assert empty["status"] == "unavailable"
    for name in ("mean_mineral_bank", "supply_blocked_game_seconds", "idle_gateway_game_seconds"):
        assert empty[name] == {"status": "unavailable", "value": None}
    assert empty["first_attack"] == {"status": "unavailable", "game_seconds": None}
    assert empty["decisions"] == {"status": "unavailable"}
    assert empty["mean_vespene_bank"]["status"] == "unavailable"  # type: ignore[index]
    assert empty["plan_aborts"]["status"] == "unavailable"  # type: ignore[index]
    broken = MatchMetrics()
    broken.sample(_obs(0.0), attack_launched=False)
    broken.fail(RuntimeError("defect"))
    summary = broken.summary(end_game_seconds=1.0)
    assert summary["status"] == "failed" and "defect" in str(summary["failure"])
    assert summary["workers"]["status"] == "unavailable"  # type: ignore[index]


def test_supply_blocks_idle_gateways_milestones_and_losses_are_measured() -> None:
    gateway = {"is_structure": True, "ready": True, "idle": True, "powered": True}
    nexus = _entity(1, "Nexus", is_structure=True, ready=True, idle=False, powered=True)
    gateways = (_entity(10, "Gateway", **gateway), _entity(11, "Gateway", **gateway))
    zealot = _entity(50, "Zealot")
    probes = tuple(_entity(100 + i, "Probe") for i in range(3))
    metrics = MatchMetrics()
    # Supply blocked: Zealots are affordable, an idle powered Gateway is ready, 2 supply won't fit.
    metrics.sample(
        _obs(
            0.0,
            minerals=150,
            supply=(14, 15),
            units=(zealot, *probes),
            structures=(nexus, *gateways),
        ),
        attack_launched=False,
    )
    # Not blocked; both Gateways idle with 250 minerals and room: min(2, 2, 3) = 2 idle.
    metrics.sample(
        _obs(0.5, minerals=250, supply=(14, 20), units=probes, structures=(nexus, *gateways)),
        attack_launched=True,
    )
    second = _entity(2, "Nexus", is_structure=True, ready=False, idle=False, powered=True)
    metrics.sample(
        _obs(1.0, minerals=0, supply=(14, 20), units=probes, structures=(nexus, second, *gateways)),
        attack_launched=True,
    )
    summary = metrics.summary(end_game_seconds=1.0)
    assert summary["status"] == "complete"
    assert summary["supply_blocked_game_seconds"] == {"status": "measured", "value": 0.5}
    assert summary["idle_gateway_game_seconds"] == {"status": "measured", "value": 1.0}
    assert summary["first_attack"] == {"status": "observed", "game_seconds": 0.5}
    assert summary["first_expansion_nexus"] == {"status": "observed", "game_seconds": 1.0}
    assert summary["first_cybernetics_core"] == {"status": "not_reached", "game_seconds": None}
    assert summary["unit_losses"] == {
        "status": "measured",
        "units": 1,
        "structures": 0,
        "by_type": {"Zealot": 1},
    }
    assert summary["workers"] == {"status": "measured", "final": 3, "max": 3, "mean": 3.0}


def test_decision_accounting_counts_answers_failures_models_and_tokens() -> None:
    metrics = MatchMetrics()
    base = {"decision_provider": "typesafe", "requested_model": PINNED, "max_requests": 450}
    polls: list[dict[str, Any]] = [
        {"pending_request_id": 1, "calls": 1},
        {
            "answer_request_id": 1,
            "source": "typesafe",
            "reason": "accepted",
            "model": PINNED,
            "latency_ms": 400.0,
            "calls": 1,
            "input_tokens": 10,
            "output_tokens": 2,
        },
        {
            "answer_request_id": 1,
            "pending_request_id": 2,
            "calls": 2,
            "reason": "accepted",
            "source": "typesafe",
            "model": PINNED,
        },
        {"answer_request_id": 1, "calls": 2, "reason": "timeout", "model": PINNED},  # 2 failed
        {"answer_request_id": 1, "pending_request_id": 3, "calls": 3, "reason": "timeout"},
        {
            "answer_request_id": 3,
            "source": "scripted_fallback",
            "reason": "stale_answer",
            "model": "jev-other",
            "latency_ms": 600.0,
            "calls": 3,
            "input_tokens": 30,
            "output_tokens": 6,
        },
        {"answer_request_id": 3, "pending_request_id": 4, "calls": 4, "reason": "stale_answer"},
        {"answer_request_id": 3, "calls": 4, "reason": "http_401"},  # 4 refused
    ]
    for poll in polls:
        metrics.record_decision(base | poll)
    decisions = metrics.summary(end_game_seconds=0.0)["decisions"]
    assert isinstance(decisions, dict)
    assert (
        decisions["calls"],
        decisions["answers"],
        decisions["accepted"],
        decisions["stale"],
    ) == (4, 2, 1, 1)
    assert decisions["failures"] == {"http_401": 1, "timeout": 1}
    assert decisions["authentication_failures"] == 1
    assert decisions["returned_models"] == {PINNED: 1, "jev-other": 1}
    assert (decisions["input_tokens"], decisions["output_tokens"]) == (30, 6)
    assert decisions["latency_ms"] == {
        "count": 2,
        "mean": 500.0,
        "median": 500.0,
        "min": 400.0,
        "max": 600.0,
    }


def test_metrics_reach_the_archive_through_the_production_runner(tmp_path: Path) -> None:
    options = _options(SCRIPTED)
    run_id = _play(tmp_path, options)
    document = _diagnostics(tmp_path / run_id)
    assert document["entrypoint"] == "bots.jev.v1" and document["policy_hash"] == V1_HASH
    assert Path(document["runtime_root"]) == repository_root()
    assert document["options"] == runner.match_options_record(options)
    assert document["outcome"]["status"] == "finished" and document["outcome"]["exit_code"] == 0
    metrics = document["metrics"]
    assert metrics["status"] == "complete" and metrics["sampling"]["samples"] >= 5
    assert metrics["sampling"]["missed_game_seconds"] == 0
    assert metrics["workers"]["final"] == 12
    assert metrics["decisions"]["provider"] == "scripted" and metrics["decisions"]["calls"] == 0
    metadata = read_run_metadata(tmp_path / run_id)
    assert metadata is not None and metadata.policy_hash == V1_HASH


def test_metrics_are_observation_only_and_v1_behavior_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert load_policy().policy_hash == V1_HASH
    defaults = runner.build_parser("x").parse_args([])
    assert (defaults.decision_provider, defaults.decision_model, defaults.max_wall_seconds) == (
        "scripted",
        "jev-latest",
        1800,
    )
    assert MatchOptions() == MatchOptions(decision_provider="scripted", max_wall_seconds=1800)

    def issued(launcher: _GameLauncher) -> list[tuple[str, object, object]]:
        return [
            (ability, getattr(actor, "tag", actor), target)
            for ability, actor, target in launcher.port.issued
        ]

    def busy() -> SimpleNamespace:  # idle Probes and a bank: gathers, a Probe, a Pylon
        return _game(
            state=SimpleNamespace(game_loop=0),
            minerals=400,
            units=[_unit(2000 + i) for i in range(12)],
        )

    measured = _GameLauncher(steps=10, game=busy)
    outcome = run_match(MatchOptions(), load_policy(), run_root=tmp_path / "a", launcher=measured)

    def broken(self: MatchMetrics, observation: Observation, *, attack_launched: bool) -> None:
        raise RuntimeError("accumulator defect")

    monkeypatch.setattr(MatchMetrics, "sample", broken)
    unmeasured = _GameLauncher(steps=10, game=busy)
    same = run_match(MatchOptions(), load_policy(), run_root=tmp_path / "b", launcher=unmeasured)
    assert issued(measured) == issued(unmeasured) and issued(measured)
    assert (outcome.status, outcome.result) == (same.status, same.result) == ("finished", "win")
    assert outcome.commands_accepted == same.commands_accepted
    assert _diagnostics(tmp_path / "b" / same.run_id)["metrics"]["status"] == "failed"


# ---------------------------------------------------------------------------
# The real child process and its bound (no SC2: an empty SC2PATH)
# ---------------------------------------------------------------------------


def test_staging_runs_the_real_entrypoint_from_its_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty_sc2 = tmp_path / "no_sc2"
    empty_sc2.mkdir()
    environ = {k: v for k, v in os.environ.items() if k.upper() != KEY}
    environ["SC2PATH"] = str(empty_sc2)  # SC2 is unavailable: nothing is launched
    bench, runs = tmp_path / "bench", tmp_path / "runs"
    code = benchmark.main(
        [
            "--panel",
            "staging",
            "--benchmark-root",
            str(bench),
            "--run-root",
            str(runs),
            "--max-game-seconds",
            "60",
            "--max-wall-seconds",
            "300",
            "--no-dashboard",
        ],
        environ=environ,
    )
    captured = capsys.readouterr()
    assert code == 1 and "infrastructure_failure" in captured.out + captured.err
    (batch_dir,) = [p for p in bench.iterdir() if is_valid_run_id(p.name)]
    saved = json.loads((batch_dir / "results.json").read_text())
    (case,) = saved["cases"]
    assert (case["status"], case["reason"]) == ("invalid", "infrastructure_failure")
    run_dir = runs / case["run_id"]
    document = _diagnostics(run_dir)
    snapshot = (
        bench
        / "baselines"
        / json.loads((batch_dir / "manifest.json").read_text())["sources"]["v1"]["snapshot"]
    )
    assert Path(document["runtime_root"]).resolve() == snapshot.resolve()
    assert Path(document["policy_source"]).resolve().is_relative_to(snapshot.resolve())
    assert document["options"]["max_wall_seconds"] == 300 - benchmark.LAUNCH_ALLOWANCE_SECONDS
    assert document["outcome"]["error_code"] == "sc2_unavailable"
    metadata = read_run_metadata(run_dir)
    assert metadata is not None and metadata.source_commit is None  # never inferred from HEAD
    assert not list(snapshot.rglob("__pycache__"))
    verify_snapshot(snapshot)
    assert not (batch_dir / benchmark.LOCK_FILE).exists()


def test_a_child_outliving_its_bound_has_only_its_own_tree_terminated(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "open(sys.argv[1], 'w').write(str(child.pid))\n"
        "time.sleep(120)\n"
    )
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        launch = ChildLaunch(
            argv=(sys.executable, "-c", code, str(pid_file)),
            cwd=tmp_path,
            env=dict(os.environ),
            hard_wall_seconds=4.0,
            stdout_path=tmp_path / "out.log",
            stderr_path=tmp_path / "err.log",
        )
        started = time.monotonic()
        exit_info = SubprocessLauncher().run(launch)
        assert exit_info.timed_out and exit_info.tree_terminated
        assert time.monotonic() - started < 60
        grandchild = int(pid_file.read_text())
        probe = SystemProcessProbe()
        deadline = time.monotonic() + 10
        while probe.alive(grandchild, None) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not probe.alive(grandchild, None)
        assert bystander.poll() is None  # an unrelated process is never touched
    finally:
        bystander.kill()
        bystander.wait(timeout=30)


def test_a_child_that_exits_reports_its_code(tmp_path: Path) -> None:
    launch = ChildLaunch(
        argv=(sys.executable, "-c", "print('jev'); raise SystemExit(3)"),
        cwd=tmp_path,
        env=dict(os.environ),
        hard_wall_seconds=60.0,
        stdout_path=tmp_path / "out.log",
        stderr_path=tmp_path / "err.log",
    )
    exit_info = SubprocessLauncher().run(launch)
    assert (exit_info.returncode, exit_info.timed_out, exit_info.interrupted) == (3, False, False)
    assert (tmp_path / "out.log").read_text().strip() == "jev"


class _CtrlCWhenReady:
    """A monotonic clock that delivers one Ctrl+C at its first reading once the
    stand-in child has written ``ready`` (its SIGINT handling is installed)."""

    def __init__(self, ready: Path) -> None:
        self.ready = ready
        self.fired = False

    def __call__(self) -> float:
        if not self.fired and self.ready.exists():
            self.fired = True
            raise KeyboardInterrupt
        return time.monotonic()


#: Stand-in children that treat SIGINT like the real Jev child. On POSIX the
#: launcher forwards Ctrl+C to the child's own session, so the first leaves cleanly
#: at once (exit 0), and the second records that SIGINT arrived but keeps running,
#: so the grace period expires. On Windows nothing is forwarded (the console
#: delivers a real Ctrl+C itself), so the first exits 7 after its sleep ("no
#: Ctrl+C arrived") and the second records nothing. The exit code and the marker
#: file therefore tell forwarding from no forwarding on each platform.
_LEAVES_CLEANLY = (
    "import pathlib, signal, sys, time\n"
    "signal.signal(signal.SIGINT, lambda *args: sys.exit(0))\n"
    "pathlib.Path(sys.argv[1]).write_text('ready')\n"
    "time.sleep(3)\n"
    "sys.exit(7)\n"
)
_IGNORES_CTRL_C = (
    "import pathlib, signal, sys, time\n"
    "marker = pathlib.Path(sys.argv[1] + '.sigint')\n"
    "signal.signal(signal.SIGINT, lambda *args: marker.write_text('received'))\n"
    "pathlib.Path(sys.argv[1]).write_text('ready')\n"
    "time.sleep(120)\n"
)
#: Ctrl+C is forwarded to the child only where the console does not deliver it.
_FORWARDED = sys.platform != "win32"


@pytest.mark.parametrize(
    ("code", "grace", "terminated"),
    [(_LEAVES_CLEANLY, 60.0, False), (_IGNORES_CTRL_C, 1.0, True)],
    ids=["leaves-cleanly", "grace-expires"],
)
def test_ctrl_c_waits_for_a_clean_leave_then_ends_only_the_owned_tree(
    tmp_path: Path, code: str, grace: float, terminated: bool
) -> None:
    ready = tmp_path / "ready"
    launch = ChildLaunch(
        argv=(sys.executable, "-c", code, str(ready)),
        cwd=tmp_path,
        env=dict(os.environ),
        hard_wall_seconds=300.0,
        stdout_path=tmp_path / "out.log",
        stderr_path=tmp_path / "err.log",
    )
    started = time.monotonic()
    clock = _CtrlCWhenReady(ready)
    exit_info = SubprocessLauncher(clock=clock, stop_grace_seconds=grace).run(launch)
    assert clock.fired and exit_info.interrupted and not exit_info.timed_out
    assert exit_info.tree_terminated is terminated
    assert time.monotonic() - started < 60
    if terminated:
        assert (tmp_path / "ready.sigint").exists() is _FORWARDED
    else:
        # 0: the forwarded SIGINT made it leave at once; 7: nothing was forwarded.
        assert exit_info.returncode == (0 if _FORWARDED else 7)
        if _FORWARDED:
            assert exit_info.wall_seconds < 3


# ---------------------------------------------------------------------------
# One source of truth for shared codes and shapes
# ---------------------------------------------------------------------------

#: The coordinator's reason codes (owned by jev.decision).
_SHARED_CODES: Final = frozenset(
    {
        decision_module.ACCEPTED_REASON,
        decision_module.LOW_CONFIDENCE_REASON,
        *decision_module.STALE_REASONS,
        *decision_module.AUTH_FAILURE_REASONS,
    }
)


def _code_literals(path: Path) -> list[str]:
    """Shared reason codes written as bare literals where a reason is compared,
    collected or assigned (dict keys and ``.get()`` field names are summary keys)."""
    found: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            candidates = [node.left, *node.comparators]
        elif isinstance(node, ast.Set | ast.Tuple | ast.List):
            candidates = list(node.elts)
        elif isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            candidates = [node.value]
        else:
            continue
        for item in candidates:
            elements = item.elts if isinstance(item, ast.Set | ast.Tuple | ast.List) else [item]
            for element in elements:
                if isinstance(element, ast.Constant) and element.value in _SHARED_CODES:
                    found.append(f"{path.name}:{element.lineno}: {element.value!r}")
    return found


def test_shared_codes_and_shapes_have_one_source() -> None:
    # Container constants built at import time are distinct objects unless shared.
    assert bot_module.AUTH_FAILURE_REASONS is decision_module.AUTH_FAILURE_REASONS
    assert benchmark.AUTH_FAILURE_REASONS is decision_module.AUTH_FAILURE_REASONS
    assert bot_module.STALE_REASONS is decision_module.STALE_REASONS
    assert benchmark.DIAGNOSTICS_FIELDS is runner.DIAGNOSTICS_FIELDS
    assert benchmark.OPPONENT_RACES is runner.OPPONENT_RACES
    assert benchmark.RUN_ROOT_PARTS is telemetry_module.RUN_ROOT_PARTS
    # Single string codes are interned, so ``is`` proves nothing for them: instead,
    # no consumer may spell a shared code as a literal where it classifies reasons.
    consumers = [REPO / "src" / "jev" / name for name in ("bot.py", "benchmark.py")]
    assert [hit for path in consumers for hit in _code_literals(path)] == []
    assert _code_literals(REPO / "src" / "jev" / "decision.py")  # the owner does
    assert set(benchmark.RACES) == set(runner.OPPONENT_RACES) - {"Random"}
    assert benchmark.PROVIDERS == ("scripted", "typesafe")
    parser = benchmark.build_parser()
    choices = {a.dest: a.choices for a in parser._actions if a.choices is not None}
    assert choices["expect_race"] is runner.OPPONENT_RACES
    assert choices["expect_provider"] is benchmark.PROVIDERS


def test_benchmark_exports_every_public_name() -> None:
    tree = ast.parse((REPO / "src" / "jev" / "benchmark.py").read_text(encoding="utf-8"))
    public: set[str] = set()
    for node in tree.body:
        for item in node.body + node.orelse if isinstance(node, ast.If) else [node]:
            if isinstance(item, ast.FunctionDef | ast.ClassDef):
                public.add(item.name)
            elif isinstance(item, ast.Assign):
                public.update(t.id for t in item.targets if isinstance(t, ast.Name))
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                public.add(item.target.id)
    public = {name for name in public if not name.startswith("_")}
    assert set(benchmark.__all__) == public
    assert benchmark.__all__ == sorted(benchmark.__all__)


# ---------------------------------------------------------------------------
# Ctrl+C never downgrades a recorded case and never orphans a run
# ---------------------------------------------------------------------------


def test_ctrl_c_right_after_a_case_is_recorded_never_relabels_it(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, cases=_THREE)
    original = store.write_results
    fired: list[bool] = []

    def write(cases: Any, invocations: Any, status: str, stop_reason: str | None) -> None:
        original(cases, invocations, status, stop_reason)
        if not fired and any(c.status == "complete" for c in cases):
            fired.append(True)
            raise KeyboardInterrupt  # right after the completed case was persisted

    store.write_results = write  # type: ignore[method-assign]
    outcome = run_batch(
        store, sources, launcher=_FakeChild(), run_root=tmp_path / "runs", environ={}
    )
    store.release()
    assert fired and (outcome.status, outcome.exit_code) == ("interrupted", 130)
    first = outcome.cases[0]
    assert (first.status, first.result, first.reason) == ("complete", "win", None)
    assert first.run_id is not None and (tmp_path / "runs" / first.run_id).is_dir()
    saved = json.loads((store.batch_dir / benchmark.RESULTS_FILE).read_text())
    assert saved["cases"][0]["status"] == "complete" and saved["cases"][1]["status"] == "pending"


def test_ctrl_c_while_scoring_keeps_the_finished_run_attached(
    snapshot_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, cases=_THREE)

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(benchmark, "score_run", interrupted)
    outcome = run_batch(
        store, sources, launcher=_FakeChild(), run_root=tmp_path / "runs", environ={}
    )
    store.release()
    first = outcome.cases[0]
    assert (first.status, first.reason) == ("invalid", "interrupted")
    (run_dir,) = (tmp_path / "runs").iterdir()
    assert first.run_id == run_dir.name  # the archive stays attached to its case
    assert first.child_pid == os.getpid()  # the stand-in child reported its pid


def test_ctrl_c_before_a_launch_leaves_the_case_pending(
    snapshot_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, cases=_THREE[:1])

    def interrupted(directory: Path) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(benchmark, "verify_snapshot", interrupted)
    child = _FakeChild()
    outcome = run_batch(store, sources, launcher=child, run_root=tmp_path / "runs", environ={})
    store.release()
    assert outcome.status == "interrupted" and child.launches == []
    assert (outcome.cases[0].status, outcome.cases[0].attempts) == ("pending", 0)
    monkeypatch.undo()
    resumed = BatchStore.open(
        tmp_path / "bench", store.manifest.batch_id, probe=_Probe(alive=False)
    )
    outcome = run_batch(resumed, sources, launcher=child, run_root=tmp_path / "runs", environ={})
    resumed.release()
    assert outcome.status == "complete" and len(child.launches) == 1  # no retry flag needed


def test_a_resume_refuses_while_a_left_running_match_may_still_play(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, cases=_THREE[:1])
    probe = SystemProcessProbe()
    pid, started = probe.current()
    cases = store.read_cases()
    alive = replace(cases[0], status="running", attempts=1, child_pid=pid, child_started=started)
    store.write_results([alive], [], "running", None)
    store.release()
    resumed = BatchStore.open(
        tmp_path / "bench", store.manifest.batch_id, probe=_Probe(alive=False)
    )
    with pytest.raises(LockHeld, match="may still be playing"):
        run_batch(
            resumed,
            sources,
            launcher=_FakeChild(),
            run_root=tmp_path / "runs",
            environ={},
            process_probe=probe,
        )
    resumed.release()
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait(timeout=60)
    gone = replace(alive, child_pid=finished.pid, child_started=None)
    resumed = BatchStore.open(
        tmp_path / "bench", store.manifest.batch_id, probe=_Probe(alive=False)
    )
    resumed.write_results([gone], [], "running", None)
    outcome = run_batch(
        resumed,
        sources,
        launcher=_FakeChild(),
        run_root=tmp_path / "runs",
        environ={},
        process_probe=probe,
    )
    resumed.release()
    assert (outcome.cases[0].status, outcome.cases[0].reason) == ("invalid", "interrupted")


class _ReadsResultsWhilePlaying(_FakeChild):
    """Reads results.json after reporting its pid, before the match ends."""

    seen: dict[str, Any] | None = None

    def run(self, launch: ChildLaunch) -> ChildExit:
        assert launch.on_started is not None
        launch.on_started(os.getpid())
        batch_dir = launch.stdout_path.parent.parent
        self.seen = json.loads((batch_dir / benchmark.RESULTS_FILE).read_text())["cases"][0]
        return super().run(replace(launch, on_started=None))


def test_the_child_pid_is_on_disk_while_the_match_plays(snapshot_dir: Path, tmp_path: Path) -> None:
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, cases=_THREE[:1])
    child = _ReadsResultsWhilePlaying()
    run_batch(store, sources, launcher=child, run_root=tmp_path / "runs", environ={})
    store.release()
    assert child.seen is not None
    assert (child.seen["status"], child.seen["child_pid"]) == ("running", os.getpid())
    assert child.seen["child_started"] == SystemProcessProbe().current()[1]


# ---------------------------------------------------------------------------
# Host failures, in-match service failures and stopping
# ---------------------------------------------------------------------------


class _JumpingClock:
    """Each reading is 50 seconds later: the wall-clock limit ends the match."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 50.0
        return self.now


def test_a_wall_clock_timeout_is_a_host_failure_not_a_bot_timeout(tmp_path: Path) -> None:
    options = _options(SCRIPTED, max_wall_seconds=60)
    outcome = run_match(
        options, load_policy(), run_root=tmp_path, launcher=_GameLauncher(), clock=_JumpingClock()
    )
    assert (outcome.status, outcome.result) == ("failed", "timeout")
    scored = score_run(tmp_path, outcome.run_id, _expect(options))
    assert (scored.status, scored.reason, scored.result) == (
        "invalid",
        "infrastructure_failure",
        None,
    )
    assert benchmark.REASON_PRIORITY[1] == "infrastructure_failure"
    assert "infrastructure_failure" in benchmark.STOPPING_REASONS


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (500, "model_unavailable"),
        (404, "model_unavailable"),
    ],
)
def test_service_failures_during_a_match_stop_the_batch(
    snapshot_dir: Path, tmp_path: Path, status: int, reason: str
) -> None:
    options = _options(HOSTED)
    run_id = _play(tmp_path / "runs", options, status=status)
    scored = score_run(tmp_path / "runs", run_id, _expect(options))
    assert (scored.status, scored.reason, scored.counted_as_win) == ("invalid", reason, False)
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, panel="baseline")
    child = _FakeChild(status=status)
    outcome = run_batch(
        store, sources, launcher=child, run_root=tmp_path / "runs", environ={KEY: FAKE_KEY}
    )
    store.release()
    assert (outcome.status, outcome.stop_reason, len(child.launches)) == ("stopped", reason, 1)


# ---------------------------------------------------------------------------
# Records are strict and the manifest is immutable
# ---------------------------------------------------------------------------


def test_an_edited_manifest_is_refused_by_resume_and_report(
    snapshot_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store(tmp_path, _sources(snapshot_dir))
    store.release()
    path = store.batch_dir / benchmark.MANIFEST_FILE
    manifest = json.loads(path.read_text())
    manifest["limits"]["match_requests"] = 400  # still a valid, lowered limit
    path.write_text(json.dumps(manifest, indent=2))
    with pytest.raises(ProvenanceMismatch, match="changed after the batch started"):
        BatchStore.open(tmp_path / "bench", store.manifest.batch_id, probe=_Probe(alive=False))
    assert not (store.batch_dir / benchmark.LOCK_FILE).exists()  # released on refusal
    code = benchmark.main(
        ["--report", store.manifest.batch_id, "--benchmark-root", str(tmp_path / "bench")]
    )
    assert code == 1 and "provenance_mismatch" in capsys.readouterr().err


def test_records_with_unexpected_fields_are_corrupt(snapshot_dir: Path, tmp_path: Path) -> None:
    store = _store(tmp_path, _sources(snapshot_dir))
    path = store.batch_dir / benchmark.RESULTS_FILE
    results = json.loads(path.read_text())
    results["cases"][0]["note"] = "hand edit"
    path.write_text(json.dumps(results))
    with pytest.raises(benchmark.CorruptRecord, match="unexpected"):
        store.read_cases()
    store.release()
    manifest = benchmark.BatchManifest.from_dict
    document = store.manifest.to_dict() | {"extra": 1}
    with pytest.raises(benchmark.CorruptRecord, match="unexpected"):
        manifest(document)
    options = _options(SCRIPTED)
    run_id = _play(tmp_path / "runs", options)
    _rewrite_diagnostics(tmp_path / "runs" / run_id, lambda d: d.update(extra=1))
    assert score_run(tmp_path / "runs", run_id, _expect(options)).reason == "corrupt_evidence"


def test_the_scorecard_reports_spend_of_invalid_hosted_cases_too() -> None:
    def spent(calls: int) -> dict[str, int]:
        return {"calls": calls, "input_tokens": calls * 10, "output_tokens": calls}

    def decided(calls: int) -> dict[str, JsonValue]:
        return {"decisions": {"status": "measured", **spent(calls)}}

    hosted = panel_cases("baseline")
    cases = [
        benchmark.CaseRecord(
            hosted[0], "complete", result="win", attempts=2, metrics=decided(10), spent=spent(13)
        ),
        benchmark.CaseRecord(
            hosted[1],
            "invalid",
            reason="model_drift",
            attempts=1,
            metrics=decided(5),
            spent=spent(5),
        ),
        benchmark.CaseRecord(
            hosted[2], "invalid", reason="interrupted", attempts=1, unrecorded_attempts=1
        ),
        benchmark.CaseRecord(hosted[3]),
    ]
    card = benchmark.scorecard(cases)
    service = card["service"]
    assert isinstance(service, dict)
    assert (service["calls"], service["input_tokens"], service["output_tokens"]) == (18, 180, 18)
    assert service["complete"] == spent(10)  # the attempt that made the complete case
    assert service["other"] == spent(8)  # a superseded attempt and an invalid case
    assert service["unrecorded_hosted_attempts"] == 1
    assert card["valid_wins"] == 1  # spend is reported; only valid wins count


def test_case_reasons_and_panels_are_closed_sets(snapshot_dir: Path, tmp_path: Path) -> None:
    store = _store(tmp_path, _sources(snapshot_dir))
    path = store.batch_dir / benchmark.RESULTS_FILE
    results = json.loads(path.read_text())
    results["cases"][0].update(status="invalid", reason="anything")
    path.write_text(json.dumps(results))
    with pytest.raises(benchmark.CorruptRecord, match="reason"):
        store.read_cases()
    results["cases"][0].update(reason="interrupted")
    results["status"] = "paused"
    path.write_text(json.dumps(results))
    with pytest.raises(benchmark.CorruptRecord, match="status"):
        store.read_cases()
    store.release()
    assert benchmark.STOP_REASONS == (*benchmark.REASON_PRIORITY, "incomplete")
    assert benchmark.RESULTS_STATUSES == ("created", "running", *get_args(benchmark.BatchStatus))
    document = store.manifest.to_dict() | {"panel": "smoke"}
    with pytest.raises(benchmark.CorruptRecord, match="panel"):
        benchmark.BatchManifest.from_dict(document)


# ---------------------------------------------------------------------------
# Diagnostics are best effort for the run, visible when skipped
# ---------------------------------------------------------------------------


class _NoDiagnostics(EvidenceFiles):
    def write(self, path: Path, data: bytes) -> None:
        if path.name == DIAGNOSTICS_FILE:
            raise OSError(28, "No space left on device")
        super().write(path, data)


def test_a_skipped_diagnostics_write_is_reported_and_never_changes_the_outcome(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = run_match(
        MatchOptions(),
        load_policy(),
        run_root=tmp_path,
        launcher=_GameLauncher(),
        evidence_files=_NoDiagnostics(),
    )
    assert (outcome.status, outcome.result, outcome.exit_code) == ("finished", "win", 0)
    assert not (tmp_path / outcome.run_id / DIAGNOSTICS_FILE).exists()
    err = capsys.readouterr().err
    assert "jev: diagnostics not written:" in err and len(err.splitlines()) == 1

    def broken(self: MatchMetrics, *, end_game_seconds: float) -> dict[str, JsonValue]:
        raise RuntimeError("summary defect")

    monkeypatch.setattr(MatchMetrics, "summary", broken)
    outcome = run_match(MatchOptions(), load_policy(), run_root=tmp_path, launcher=_GameLauncher())
    metrics = _diagnostics(tmp_path / outcome.run_id)["metrics"]
    assert metrics["status"] == "failed" and "summary defect" in metrics["failure"]


def test_a_legacy_trace_with_an_implausible_segment_count_is_corrupt_not_a_hang(
    tmp_path: Path,
) -> None:
    run_dir, options = _legacy_hosted_archive(tmp_path)
    state = json.loads((run_dir / "state.json").read_text())
    state["trace"]["segment"] = 10**15
    (run_dir / "state.json").write_text(json.dumps(state, separators=(",", ":")))
    started = time.monotonic()
    scored = score_run(run_dir.parent, run_dir.name, _expect(options, require_diagnostics=False))
    assert time.monotonic() - started < 30
    assert (scored.reason, scored.counted_as_win) == ("corrupt_evidence", False)


# ---------------------------------------------------------------------------
# The child environment and the dry run's view of it
# ---------------------------------------------------------------------------


def test_children_never_consult_a_bytecode_cache_beside_the_snapshot(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    inherited = {"PYTHONPYCACHEPREFIX": "elsewhere", KEY: FAKE_KEY}
    prefix = tmp_path / "case.1.pycache"
    env = child_environment(inherited, snapshot_dir, hosted=True, pycache_prefix=prefix)
    assert env["PYTHONPYCACHEPREFIX"] == str(prefix) and env[KEY] == FAKE_KEY
    assert "PYTHONPYCACHEPREFIX" not in child_environment(inherited, snapshot_dir, hosted=False)
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, cases=_THREE[:1])
    child = _FakeChild()
    run_batch(store, sources, launcher=child, run_root=tmp_path / "runs", environ=inherited)
    store.release()
    (launch,) = child.launches
    assert Path(launch.env["PYTHONPYCACHEPREFIX"]).parent == store.batch_dir / "attempts"
    assert KEY not in launch.env


def test_the_dry_run_shows_the_production_child_environment(snapshot_dir: Path) -> None:
    sources = _sources(snapshot_dir)
    plan = benchmark.dry_run_plan(
        "baseline",
        sources,
        BenchmarkLimits(),
        run_root=REPO / "runs",
        benchmark_root=REPO / "bench",
        python="python",
    )
    case = plan["cases"][0]  # type: ignore[index]
    env = case["env"]  # type: ignore[index]
    produced = child_environment({}, snapshot_dir, hosted=True, pycache_prefix=Path("prefix"))
    assert set(env) == set(produced) | {KEY}  # type: ignore[arg-type]
    assert env["PYTHONPATH"] == produced["PYTHONPATH"]  # type: ignore[index]
    assert FAKE_KEY not in json.dumps(plan) and "inherited" in env[KEY]  # type: ignore[index]


def test_the_production_launcher_reports_the_child_pid(tmp_path: Path) -> None:
    pids: list[int] = []
    launch = ChildLaunch(
        argv=(sys.executable, "-c", "pass"),
        cwd=tmp_path,
        env=dict(os.environ),
        hard_wall_seconds=60.0,
        stdout_path=tmp_path / "out.log",
        stderr_path=tmp_path / "err.log",
        on_started=pids.append,
    )
    exit_info = SubprocessLauncher().run(launch)
    assert exit_info.returncode == 0 and len(pids) == 1 and pids[0] > 0


def test_each_attempt_gets_a_new_empty_unpredictable_bytecode_prefix(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    sources = _sources(snapshot_dir)
    store = _store(tmp_path, sources, cases=_THREE[:2])
    seen: list[tuple[Path, list[str]]] = []

    class Inspecting(_FakeChild):
        def run(self, launch: ChildLaunch) -> ChildExit:
            prefix = Path(launch.env["PYTHONPYCACHEPREFIX"])
            seen.append((prefix, [p.name for p in prefix.iterdir()]))
            return super().run(launch)

    run_batch(store, sources, launcher=Inspecting(), run_root=tmp_path / "runs", environ={})
    store.release()
    assert len(seen) == 2 and seen[0][0] != seen[1][0]
    for prefix, contents in seen:
        assert contents == [] and prefix.parent == store.batch_dir / "attempts"
        case_id, attempt, random_part = prefix.name.removesuffix(".pycache").rsplit(".", 2)
        assert attempt == "1" and len(random_part) >= 8  # tempfile.mkdtemp naming
        assert case_id in {c.case_id for c in _THREE}


# ---------------------------------------------------------------------------
# The operator guide cannot drift from the command
# ---------------------------------------------------------------------------

GUIDE = REPO / "documentation" / "operator" / "jev-v2-validation.md"
_GUIDE_VARIABLES: Final = {"$batch", "$jiRun", "$plan"}


def _guide_benchmark_commands() -> list[list[str]]:
    text = GUIDE.read_text(encoding="utf-8")
    commands: list[list[str]] = []
    for block in re.findall(r"```powershell\n(.*?)```", text, flags=re.DOTALL):
        for line in block.splitlines():
            if "scripts/benchmark_jev.py" not in line or line.lstrip().startswith("#"):
                continue
            tokens = [t.strip("'\"") for t in shlex.split(line, posix=False)]
            commands.append(tokens[tokens.index("scripts/benchmark_jev.py") + 1 :])
    return commands


def _literal_values(kind: object) -> set[str]:
    values: set[str] = set()
    for arg in get_args(kind):
        values |= _literal_values(arg) if get_args(arg) else {str(arg)}
    return values


def test_every_guide_command_parses(tmp_path: Path) -> None:
    commands = _guide_benchmark_commands()
    assert len(commands) >= 8
    stand_ins = {
        "$batch": uuid.uuid4().hex,
        "$jiRun": str(tmp_path / uuid.uuid4().hex),
        "$plan": str(tmp_path / "plan.json"),
    }
    parser = benchmark.build_parser()
    for arguments in commands:
        assert set(a for a in arguments if a.startswith("$")) <= _GUIDE_VARIABLES, arguments
        # A bare <placeholder> is a PowerShell parse error (redirection operator).
        assert not any(a.startswith("<") or a.endswith(">") for a in arguments), arguments
        parser.parse_args([stand_ins.get(a, a) for a in arguments])


def test_the_guide_documents_every_reason_and_error_code() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    codes = _literal_values(benchmark.BenchmarkErrorCode)
    assert set(get_args(benchmark.Reason)) <= codes
    assert [code for code in sorted(codes) if f"`{code}`" not in text] == []


def test_the_guide_states_the_launch_liveness_table_with_the_codes_bounds() -> None:
    """Section 11's per-state liveness table cites the code's own bounds and labels."""
    text = GUIDE.read_text(encoding="utf-8")
    header = text.index("| Session state |")
    rows: dict[str, list[str]] = {}
    for line in text[header:].split("\n\n", 1)[0].splitlines()[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows[cells[0]] = cells
    tsx = (REPO / "frontend" / "src" / "components" / "JevTab.tsx").read_text(encoding="utf-8")
    block = re.search(r"LAUNCH_PHASE_LABELS[^{]*\{(.*?)\};", tsx, re.DOTALL)
    assert block is not None
    labels = dict(re.findall(r'(\w+): "([^"]+)"', block[1]))
    silent, stale = f"**{labels['silent']}**", f"**{labels['stale']}**"
    # Each state's numbers are exactly its bounds (no stray or outdated bound).
    expected = {
        "`preparing`": ({LAUNCH_SILENCE_SECONDS}, silent),
        "`starting`": ({LAUNCH_HEARTBEAT_SECONDS, STARTING_SILENCE_SECONDS}, silent),
        "`running`": ({FIRST_OBSERVATION_SILENCE_SECONDS, STALE_AFTER_SECONDS}, stale),
        "`between_games`": ({LAUNCH_SILENCE_SECONDS}, silent),
        "`finished`, `failed`, `stopped`": (set(), "no alarm"),
    }
    assert set(rows) == set(expected)
    for state, (bounds, label) in expected.items():
        numbers = {float(n) for n in re.findall(r"(\d+) seconds", " ".join(rows[state]))}
        assert numbers == bounds, (state, numbers)
        assert rows[state][-1] == label, state


@pytest.mark.parametrize(
    "section",
    [
        "Panels",
        "Dry run",
        "Staging",
        "Where outputs land",
        "scored",
        "Limits",
        "Interrupt and resume",
        "Calibrating",
        "Hosted panels",
        "Exit codes",
    ],
)
def test_the_guide_has_each_required_section(section: str) -> None:
    headings = re.findall(r"^## \d+\. (.+)$", GUIDE.read_text(encoding="utf-8"), re.MULTILINE)
    assert any(section in heading for heading in headings), headings
