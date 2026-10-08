"""Jev's read-only run-evidence API and its dashboard mount (Step 204).

The integration tests run the production path end to end: :func:`jev.runner.run_match`
-> :class:`jev.bot.JevController` (``JevBot.on_step``) -> :class:`jev.telemetry.RunRecorder`
writes a run directory under a temporary run root, and the real router
(:func:`jev.api.create_router`) mounted on a FastAPI app serves it back through
``TestClient``. Only SC2 is replaced: a duck-typed ``BotAI`` stand-in and port, as in
``test_jev_sc2.py``. The router tests then attack the path-ID, link and
stored-record boundaries, and the mount tests check the dashboard app
(``bots/v13/api.py``) gained exactly the Jev routes and nothing else changed.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import os
import random
import shutil
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bots.jev.v1 import load_policy
from fastapi import FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from sc2.data import Result

from jev.api import (
    API_PREFIX,
    MAX_LIST_READ_BYTES,
    RUN_LIST_LIMIT,
    STALE_AFTER_SECONDS,
    create_router,
)
from jev.bot import JevBot, JevController
from jev.contracts import RunMetadata
from jev.policy import PolicyBundle
from jev.runner import EXIT_FAILURE, MatchOptions, Sc2Launcher, run_match
from jev.runtime import JevRuntime
from jev.telemetry import (
    MAX_STATE_BYTES,
    METADATA_FILE,
    POLICY_ARCHIVE_FILE,
    REPLAY_FILE,
    STATE_FILE,
    STATE_SUMMARY_BYTES,
    EvidenceFiles,
    RunRecorder,
    default_run_root,
    repository_root,
    timestamp_seconds,
    utc_timestamp,
)
from orchestrator import registry

BUNDLE = load_policy()
#: JavaScript-unsafe unit tags (> 2**53) for the stand-in game's probes.
BIG_TAGS = (2**60 + 11, 2**60 + 12)
MARKER = "<script>alert(1)</script>"
START = (30.5, 30.5)
PATCH = 3000

# ---------------------------------------------------------------------------
# A burnysc2-shaped stand-in game and launcher (as in test_jev_sc2.py)
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
    """Idle probes and no minerals: the v1 policy's only commands are gathers."""
    return SimpleNamespace(
        state=SimpleNamespace(game_loop=0),
        minerals=0,
        supply_used=2,
        supply_cap=15,
        units=[_unit(tag) for tag in BIG_TAGS],
        structures=[_unit(1000, "Nexus", position=START, is_structure=True)],
        enemy_units=[],
        enemy_structures=[],
        mineral_field=[
            _unit(PATCH + i, "MineralField", position=(26.5 + i, 22.5)) for i in range(8)
        ],
        start_location=START,
        enemy_start_locations=[(120.5, 120.5)],
        expansion_locations_list=[(120.5, 120.5), START],
        game_info=SimpleNamespace(map_center=(75.5, 75.5)),
    )


class _Port:
    """Accepts every command, like an SC2 that never refuses."""

    def __init__(self) -> None:
        self.issued: list[tuple[str, object, object]] = []
        self.left = False

    def is_visible(self, point: tuple[float, float]) -> bool:
        return False

    async def placement_legal(self, ability: str, sites: Any) -> list[bool]:
        return [True] * len(sites)

    async def issue(self, ability: str, actor: object, target: object) -> object:
        self.issued.append((ability, actor, target))
        return None

    def action_errors(self) -> list[object]:
        return []

    async def leave(self) -> None:
        self.left = True


type _Hook = Callable[[SimpleNamespace, int], None]


class _Launcher:
    """Plays ``steps`` JevBot steps against the stand-in game the way burnysc2 does:
    a bot that left the game ends it with a Defeat."""

    def __init__(self, steps: int = 12, *, hook: _Hook | None = None) -> None:
        self.steps = steps
        self.hook = hook
        self.port = _Port()
        self.prepared = False

    def prepare(self, options: MatchOptions) -> object:
        self.prepared = True
        return "setup"

    def play(self, controller: JevController, setup: object, options: MatchOptions) -> Any:
        game = _game()
        bot = JevBot(controller)
        controller.attach(game, self.port)  # what on_start binds against a live game

        async def run() -> str:
            for index in range(self.steps):
                if self.hook is not None:
                    self.hook(game, index)
                await bot.on_step(index)
                if self.port.left:
                    await bot.on_end(Result.Defeat)
                    return "loss"
                game.state.game_loop += 8
            await bot.on_end(Result.Victory)
            return "win"

        return asyncio.run(run())


def _corrupt_game_state(game: SimpleNamespace, index: int) -> None:
    if index == 2:
        game.units = 12  # the adapter raises ValueError while observing: a crash


def _client(root: Path, now: Callable[[], float] = time.time) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(root, wall_time=now))
    return TestClient(app)


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


def _metadata(created_at: str = "2026-10-08T12:00:00.000+00:00") -> RunMetadata:
    return RunMetadata(
        run_id=uuid.uuid4().hex,
        created_at=created_at,
        family=BUNDLE.policy.family,
        version=BUNDLE.policy.version,
        policy_hash=BUNDLE.policy_hash,
        source_commit=None,
        map="Simple64",
        opponent_race="Terran",
        difficulty=1,
        seed=1,
        max_game_seconds=900.0,
        max_wall_seconds=1800.0,
    )


def _finished_run(root: Path, metadata: RunMetadata | None = None) -> str:
    """A finished run written by the real recorder; returns its run ID."""
    chosen = _metadata() if metadata is None else metadata
    recorder = RunRecorder(root, chosen, roots=BUNDLE.policy.roots)
    recorder.start(BUNDLE.policy_bytes)
    recorder.finish(None, status="finished", result="win")
    return chosen.run_id


class _Clock:
    """An injectable clock (monotonic seconds) the test sets or advances."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _variant_bundle(tmp_path: Path) -> PolicyBundle:
    document = json.loads(BUNDLE.policy_bytes)
    document["nodes"][0]["label"] = "Variant root lane"
    candidate = tmp_path / "variant-policy.json"
    candidate.write_text(json.dumps(document), encoding="utf-8")
    return load_policy(candidate)


# ---------------------------------------------------------------------------
# Production path: runner -> bot -> telemetry -> router
# ---------------------------------------------------------------------------


def test_match_evidence_round_trips_from_the_runner_through_the_router(
    tmp_path: Path,
) -> None:
    outcome = run_match(MatchOptions(), BUNDLE, run_root=tmp_path, launcher=_Launcher())
    assert (outcome.status, outcome.result) == ("finished", "win")
    client = _client(tmp_path)

    listing = client.get("/api/jev/runs").json()
    (entry,) = listing["runs"]
    assert entry == {
        "run_id": outcome.run_id,
        "family": "jev",
        "version": 1,
        "status": "finished",
        "updated_at": entry["updated_at"],
        "policy_hash": BUNDLE.policy_hash,
    }
    assert (listing["schema_version"], listing["truncated"], listing["omitted"]) == (1, False, 0)

    run = client.get(f"/api/jev/runs/{outcome.run_id}").json()
    assert (run["status"], run["result"], run["error"], run["stale"]) == (
        "finished",
        "win",
        None,
        False,
    )
    assert run["policy_hash"] == run["metadata"]["policy_hash"] == BUNDLE.policy_hash
    actors = {tag for e in run["recent_events"] if e["action"] for tag in e["action"]["actor_tags"]}
    assert {str(tag) for tag in BIG_TAGS} <= actors  # 64-bit tags arrive as decimal strings
    assert all(abs(i) <= 2**53 for i in _integers(run))

    policy = client.get(f"/api/jev/runs/{outcome.run_id}/policy").json()
    assert policy.pop("policy_hash") == BUNDLE.policy_hash
    assert policy == BUNDLE.policy.to_document()  # the archived snapshot, node for node


def test_two_runs_in_one_root_are_served_without_mixing_their_evidence(tmp_path: Path) -> None:
    variant = _variant_bundle(tmp_path)
    root = tmp_path / "runs"
    bundles = {}
    for bundle in (BUNDLE, variant):
        outcome = run_match(MatchOptions(), bundle, run_root=root, launcher=_Launcher())
        bundles[outcome.run_id] = bundle
    assert BUNDLE.policy_hash != variant.policy_hash
    client = _client(root)
    listed = {r["run_id"]: r["policy_hash"] for r in client.get("/api/jev/runs").json()["runs"]}
    assert listed == {run_id: bundle.policy_hash for run_id, bundle in bundles.items()}
    for run_id, bundle in bundles.items():
        run = client.get(f"/api/jev/runs/{run_id}").json()
        assert run["policy_hash"] == bundle.policy_hash
        assert run["recent_events"] and {e["run_id"] for e in run["recent_events"]} == {run_id}
        policy = client.get(f"/api/jev/runs/{run_id}/policy").json()
        assert policy["nodes"][0]["label"] == bundle.policy.nodes[0].label


def test_a_crashed_match_is_served_as_failed_with_its_error_and_never_stale(
    tmp_path: Path,
) -> None:
    launcher = _Launcher(hook=_corrupt_game_state)
    outcome = run_match(MatchOptions(), BUNDLE, run_root=tmp_path, launcher=launcher)
    assert outcome.status == "failed"
    hour_later = _client(tmp_path, now=lambda: time.time() + 3600)
    run = hour_later.get(f"/api/jev/runs/{outcome.run_id}").json()
    assert (run["status"], run["error"]["code"], run["stale"]) == ("failed", "match_crashed", False)
    assert "units must be a collection" in run["error"]["message"]


class _FailingOnce(EvidenceFiles):
    """Refuses one ``state.json`` write (as a full disk would), then recovers."""

    def __init__(self, refused_write: int) -> None:
        super().__init__()
        self.refused_write = refused_write
        self.state_writes = 0

    def write(self, path: Path, data: bytes) -> None:
        if path.name == STATE_FILE:
            self.state_writes += 1
            if self.state_writes == self.refused_write:
                raise OSError(28, "No space left on device")
        super().write(path, data)


def test_live_evidence_that_cannot_be_persisted_stops_the_match(tmp_path: Path) -> None:
    clock = _Clock()

    def tick(game: SimpleNamespace, index: int) -> None:
        clock.now += 0.6  # every step passes the state-write interval

    launcher = _Launcher(hook=tick)
    outcome = run_match(
        MatchOptions(),
        BUNDLE,
        run_root=tmp_path,
        launcher=launcher,
        clock=clock,
        evidence_files=_FailingOnce(refused_write=3),
    )
    assert (outcome.status, outcome.exit_code) == ("failed", EXIT_FAILURE)
    assert outcome.error is not None and outcome.error.code == "persistence_failed"
    assert launcher.port.left  # the bot left the game instead of playing on unrecorded
    run = _client(tmp_path).get(f"/api/jev/runs/{outcome.run_id}").json()
    assert (run["status"], run["error"]["code"]) == ("failed", "persistence_failed")
    assert run["trace"]["complete"] is False  # never claims a complete trace


def test_nothing_launches_when_the_run_evidence_cannot_be_started(tmp_path: Path) -> None:
    launcher = _Launcher()
    outcome = run_match(
        MatchOptions(),
        BUNDLE,
        run_root=tmp_path,
        launcher=launcher,
        evidence_files=_FailingOnce(refused_write=1),
    )
    assert (outcome.status, outcome.exit_code) == ("failed", EXIT_FAILURE)
    assert outcome.error is not None and outcome.error.code == "persistence_failed"
    assert not launcher.prepared
    client = _client(tmp_path)
    assert client.get("/api/jev/runs").json()["runs"] == []
    assert client.get(f"/api/jev/runs/{outcome.run_id}").status_code == 404


class _FailingPreflight:
    """A launcher whose preflight raises (Ctrl+C, or a defect) before any match."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def prepare(self, options: MatchOptions) -> object:
        raise self.error

    def play(self, controller: JevController, setup: object, options: MatchOptions) -> Any:
        raise AssertionError("never played")


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (KeyboardInterrupt(), "stopped", None),
        (RuntimeError("preflight defect"), "failed", "match_crashed"),
    ],
    ids=["ctrl-c", "defect"],
)
def test_an_exception_escaping_the_match_still_ends_the_run_on_disk(
    tmp_path: Path, error: BaseException, status: str, code: str | None
) -> None:
    run_id = uuid.uuid4().hex
    launcher = _FailingPreflight(error)
    with pytest.raises(type(error)):
        run_match(MatchOptions(), BUNDLE, run_root=tmp_path, launcher=launcher, run_id=run_id)
    hour_later = _client(tmp_path, now=lambda: time.time() + 3600)
    run = hour_later.get(f"/api/jev/runs/{run_id}").json()
    assert (run["status"], run["stale"]) == (status, False)  # ended, not left stale
    assert (run["error"] or {}).get("code") == code


class _InterruptedWrite(EvidenceFiles):
    """A Ctrl+C lands during one ``state.json`` write."""

    def __init__(self, interrupted_write: int) -> None:
        super().__init__()
        self.interrupted_write = interrupted_write
        self.state_writes = 0

    def write(self, path: Path, data: bytes) -> None:
        if path.name == STATE_FILE:
            self.state_writes += 1
            if self.state_writes == self.interrupted_write:
                raise KeyboardInterrupt
        super().write(path, data)


def test_a_ctrl_c_during_the_terminal_write_still_leaves_a_terminal_state(
    tmp_path: Path,
) -> None:
    run_id = uuid.uuid4().hex
    with pytest.raises(KeyboardInterrupt):
        run_match(
            MatchOptions(),
            BUNDLE,
            run_root=tmp_path,
            launcher=_Launcher(),
            run_id=run_id,
            clock=lambda: 0.0,  # no live state write: the second write is the terminal one
            evidence_files=_InterruptedWrite(interrupted_write=2),
        )
    hour_later = _client(tmp_path, now=lambda: time.time() + 3600)
    run = hour_later.get(f"/api/jev/runs/{run_id}").json()
    assert (run["status"], run["result"], run["stale"]) == ("finished", "win", False)


def test_the_replay_burnysc2_saves_is_referenced_in_the_served_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sc2.main
    import sc2.maps
    from sc2.paths import Paths

    install = tmp_path / "StarCraft II"
    (install / "Versions" / "Base90000").mkdir(parents=True)
    (install / "Versions" / "Base90000" / "SC2_x64.exe").write_bytes(b"")  # not run
    (install / "Maps").mkdir()
    (install / "Maps" / "Simple64.SC2Map").write_bytes(b"map")
    monkeypatch.setenv("SC2PATH", str(install))
    executable = install / "Versions" / "Base90000" / "SC2_x64.exe"
    monkeypatch.setattr(Paths, "EXECUTABLE", str(executable), raising=False)
    monkeypatch.setattr(sc2.maps, "get", lambda name: f"map {name}")
    requested: dict[str, Any] = {}

    def run_game(map_settings: object, players: list[Any], **kwargs: Any) -> Result:
        """burnysc2's run_game: the bot plays, then Client.save_replay writes the file."""
        requested.update(kwargs)
        bot = players[0].ai
        bot.controller.attach(_game(), _Port())
        asyncio.run(bot.on_step(0))
        Path(kwargs["save_replay_as"]).write_bytes(b"MPQ\x1a replay")
        asyncio.run(bot.on_end(Result.Victory))
        return Result.Victory

    monkeypatch.setattr(sc2.main, "run_game", run_game)
    root = tmp_path / "runs"
    outcome = run_match(MatchOptions(), BUNDLE, run_root=root, launcher=Sc2Launcher())
    assert (outcome.status, outcome.result) == ("finished", "win")
    assert requested["save_replay_as"] == str(root / outcome.run_id / REPLAY_FILE)
    run = _client(root).get(f"/api/jev/runs/{outcome.run_id}").json()
    assert run["metadata"]["replay_path"] == REPLAY_FILE
    assert (root / outcome.run_id / run["metadata"]["replay_path"]).is_file()


# ---------------------------------------------------------------------------
# Stale producers and torn traces
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "stale"), [(STALE_AFTER_SECONDS - 1, False), (STALE_AFTER_SECONDS + 1, True)]
)
def test_a_producer_that_stopped_writing_mid_run_reads_as_stale(
    tmp_path: Path, age: float, stale: bool
) -> None:
    heartbeat = 1_800_000_000.0
    clock = _Clock()
    recorder = RunRecorder(
        tmp_path,
        _metadata(),
        roots=BUNDLE.policy.roots,
        clock=clock,
        wall_time=lambda: heartbeat,
    )
    recorder.start(BUNDLE.policy_bytes)
    clock.now = 1.0
    recorder.update(JevRuntime(BUNDLE.policy, run_id=recorder.run_dir.name), ())
    client = _client(tmp_path, now=lambda: heartbeat + age)  # ... and then it died
    run = client.get(f"/api/jev/runs/{recorder.run_dir.name}").json()
    assert (run["status"], run["stale"]) == ("running", stale)
    assert timestamp_seconds(run["updated_at"]) == heartbeat
    assert client.get("/api/jev/runs").json()["runs"][0]["status"] == "running"


class _TearsOnce(EvidenceFiles):
    """One trace append dies halfway through its bytes, as a crash or full disk would."""

    def __init__(self, torn_append: int) -> None:
        super().__init__()
        self.torn_append = torn_append
        self.appends = 0

    def append(self, path: Path, data: bytes) -> None:
        self.appends += 1
        if self.appends == self.torn_append:
            super().append(path, data[: len(data) // 2])
            raise OSError(28, "No space left on device")
        super().append(path, data)


def test_a_trace_append_torn_mid_line_is_cut_and_reported_while_the_run_goes_on(
    tmp_path: Path,
) -> None:
    files = _TearsOnce(torn_append=2)
    launcher = _Launcher(steps=24)
    outcome = run_match(
        MatchOptions(), BUNDLE, run_root=tmp_path, launcher=launcher, evidence_files=files
    )
    assert files.appends > files.torn_append  # the live run traced on after the tear
    response = _client(tmp_path).get(f"/api/jev/runs/{outcome.run_id}")
    run = response.json()
    assert (response.status_code, run["schema_version"], run["status"], run["result"]) == (
        200,
        1,
        "finished",
        "win",
    )
    assert run["policy_hash"] == BUNDLE.policy_hash
    assert run["trace"]["complete"] is False and run["trace"]["dropped_events"] > 0
    segments = sorted((tmp_path / outcome.run_id).glob("events.*.jsonl"))
    assert segments
    for segment in segments:
        *lines, tail = segment.read_bytes().split(b"\n")
        assert tail == b""  # no unterminated fragment survives
        assert all(json.loads(line)["run_id"] == outcome.run_id for line in lines)


# ---------------------------------------------------------------------------
# Router boundaries
# ---------------------------------------------------------------------------


_MISSING = uuid.uuid4().hex


@pytest.mark.parametrize("endpoint", ["", "/policy"])
@pytest.mark.parametrize(
    ("raw_id", "status", "code"),
    [
        (uuid.uuid4().hex.upper(), 422, "invalid_run_id"),
        (uuid.uuid1().hex, 422, "invalid_run_id"),
        (uuid.uuid4().hex[:31], 422, "invalid_run_id"),
        ("a" * 10_000, 422, "invalid_run_id"),
        ("C:%5CWindows%5Csystem32", 422, "invalid_run_id"),
        ("%2e%2e%2f%2e%2e%2fsecret", 404, None),  # decodes to a path: no route at all
        (_MISSING, 404, "run_not_found"),
    ],
    ids=[
        "uppercase",
        "not-version-4",
        "short",
        "overlong",
        "absolute-path",
        "traversal",
        "missing",
    ],
)
def test_path_ids_that_are_not_runs_in_the_root_are_rejected_without_echo(
    tmp_path: Path, endpoint: str, raw_id: str, status: int, code: str | None
) -> None:
    _finished_run(tmp_path)  # the root holds a real run that must never be served here
    response = _client(tmp_path).get(f"/api/jev/runs/{raw_id}{endpoint}")
    assert response.status_code == status
    if code is not None:
        assert response.json()["error"]["code"] == code
        assert raw_id not in response.text
    assert "policy_hash" not in response.text


@pytest.mark.parametrize("endpoint", ["", "/policy"])
@pytest.mark.parametrize(
    ("raw_id", "code"),
    [
        ("..", None),  # the client resolves it before sending: no route at all
        ("%2e%2e", "invalid_run_id"),
        (".%2E", "invalid_run_id"),
        ("..%5C{sibling}", "invalid_run_id"),
        ("%2e%2e%5c{sibling}", "invalid_run_id"),
        ("secrets", "invalid_run_id"),
        ("{sibling_upper}", "invalid_run_id"),
    ],
    ids=["dot-dot", "encoded", "mixed-case", "backslash", "encoded-backslash", "sibling", "upper"],
)
def test_one_segment_traversal_ids_never_reach_content_beside_the_root(
    tmp_path: Path, endpoint: str, raw_id: str, code: str | None
) -> None:
    root = tmp_path / "runs"
    root.mkdir()
    sibling = _finished_run(tmp_path)  # a real run directory beside the root
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / STATE_FILE).write_text(MARKER, encoding="utf-8")
    requested = raw_id.format(sibling=sibling, sibling_upper=sibling.upper())
    response = _client(root).get(f"/api/jev/runs/{requested}{endpoint}")
    if code is None:
        assert (response.status_code, response.json()) == (404, {"detail": "Not Found"})
    else:
        assert (response.status_code, response.json()["error"]["code"]) == (422, code)
    assert sibling not in response.text.lower() and MARKER not in response.text
    assert "policy_hash" not in response.text


def _link_dir(link: Path, target: Path) -> None:
    """A directory link: a symlink, or a junction where Windows withholds symlinks."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


def test_a_run_directory_link_that_resolves_outside_the_root_is_not_a_run(
    tmp_path: Path,
) -> None:
    run_id = _finished_run(tmp_path / "elsewhere")
    root = tmp_path / "runs"
    root.mkdir()
    _link_dir(root / run_id, tmp_path / "elsewhere" / run_id)
    client = _client(root)
    response = client.get(f"/api/jev/runs/{run_id}")
    assert (response.status_code, response.json()["error"]["code"]) == (404, "run_not_found")
    assert client.get("/api/jev/runs").json()["runs"] == []


@pytest.mark.parametrize(
    ("record", "endpoint"),
    [(METADATA_FILE, ""), (STATE_FILE, ""), (POLICY_ARCHIVE_FILE, "/policy")],
)
def test_a_malformed_stored_record_is_503_corrupt_run(
    tmp_path: Path, record: str, endpoint: str
) -> None:
    run_id = _finished_run(tmp_path)
    (tmp_path / run_id / record).write_text(f'{{"{MARKER}": ', encoding="utf-8")
    response = _client(tmp_path).get(f"/api/jev/runs/{run_id}{endpoint}")
    assert response.status_code == 503
    assert response.json()["schema_version"] == 1
    assert response.json()["error"]["code"] == "corrupt_run"
    assert record in response.json()["error"]["message"]
    assert MARKER not in response.text


def _set_text(document: dict[str, Any], path: tuple[str | int, ...], text: str) -> None:
    *parents, last = path
    target: Any = document
    for key in parents:
        target = target[key]
    target[last] = text


@pytest.mark.parametrize(
    ("edits", "endpoint"),
    [
        ([(METADATA_FILE, ("family",)), (STATE_FILE, ("family",))], ""),
        ([(STATE_FILE, ("recent_events", 0, "facts", "note"))], ""),
        ([(POLICY_ARCHIVE_FILE, ("nodes", 0, "label"))], "/policy"),
    ],
    ids=["metadata-and-summary-family", "state-event-fact", "policy-label"],
)
def test_text_that_is_not_valid_unicode_makes_a_record_corrupt_never_a_500(
    tmp_path: Path, edits: list[tuple[str, tuple[str | int, ...]]], endpoint: str
) -> None:
    """A lone surrogate (a JSON ``\\ud800`` escape) could not be encoded into a response."""
    hurt, healthy = (
        run_match(MatchOptions(), BUNDLE, run_root=tmp_path, launcher=_Launcher()).run_id
        for _ in range(2)
    )
    for record, path in edits:
        stored = tmp_path / hurt / record
        document = json.loads(stored.read_bytes())
        _set_text(document, path, "jev\ud800")
        stored.write_text(json.dumps(document), encoding="ascii")  # escaped as \ud800
    client = _client(tmp_path)
    response = client.get(f"/api/jev/runs/{hurt}{endpoint}")
    assert (response.status_code, response.json()["error"]["code"]) == (503, "corrupt_run")
    listing = client.get("/api/jev/runs")
    assert listing.status_code == 200
    in_list = edits[0][0] != METADATA_FILE  # a listing reads metadata and state summaries only
    assert {r["run_id"] for r in listing.json()["runs"]} == (
        {hurt, healthy} if in_list else {healthy}
    )
    assert listing.json()["omitted"] == (0 if in_list else 1)


def test_the_list_omits_and_counts_runs_whose_records_are_malformed(tmp_path: Path) -> None:
    good = _finished_run(tmp_path)
    for record in (METADATA_FILE, STATE_FILE):
        broken = _finished_run(tmp_path)
        (tmp_path / broken / record).write_text("[]", encoding="utf-8")
    listing = _client(tmp_path).get("/api/jev/runs").json()
    assert [r["run_id"] for r in listing["runs"]] == [good]
    assert listing["omitted"] == 2


def test_an_absent_run_root_is_an_empty_list(tmp_path: Path) -> None:
    client = _client(tmp_path / "never-created")
    assert client.get("/api/jev/runs").json() == {
        "schema_version": 1,
        "runs": [],
        "truncated": False,
        "omitted": 0,
    }
    assert client.get(f"/api/jev/runs/{_MISSING}").status_code == 404


def test_a_listing_reads_only_each_state_s_leading_summary(tmp_path: Path) -> None:
    """One listing reads at most MAX_LIST_READ_BYTES (about 4 MiB), never whole states."""
    assert MAX_LIST_READ_BYTES == 4096 * 1024 + 50 * 512
    run_id = _finished_run(tmp_path)
    state = tmp_path / run_id / STATE_FILE
    raw = state.read_bytes()
    summary = raw[: raw.index(b',"game_seconds"')]
    assert len(summary) <= STATE_SUMMARY_BYTES
    state.write_bytes(summary + b"#" * MAX_STATE_BYTES)  # junk, and over the state cap
    client = _client(tmp_path)
    (entry,) = client.get("/api/jev/runs").json()["runs"]
    assert (entry["run_id"], entry["status"]) == (run_id, "finished")
    assert client.get(f"/api/jev/runs/{run_id}").status_code == 503  # the detail reads it all


def test_the_router_refuses_a_root_relative_to_the_working_directory() -> None:
    with pytest.raises(ValueError, match="absolute"):
        create_router(Path("data") / "jev" / "runs")


def test_the_list_holds_the_newest_50_runs_by_creation_and_flags_truncation(
    tmp_path: Path,
) -> None:
    base = 1_800_000_000.0
    order = list(range(RUN_LIST_LIMIT + 2))
    random.Random(4).shuffle(order)  # creation order is not directory order
    created = {
        _finished_run(tmp_path, _metadata(created_at=utc_timestamp(base + i))): i for i in order
    }
    listing = _client(tmp_path).get("/api/jev/runs").json()
    newest_first = sorted(created, key=created.__getitem__, reverse=True)
    assert [r["run_id"] for r in listing["runs"]] == newest_first[:RUN_LIST_LIMIT]
    assert listing["truncated"] is True


# ---------------------------------------------------------------------------
# The dashboard mount and legacy discovery
# ---------------------------------------------------------------------------


def _route_table(routes: list[Any]) -> set[tuple[str, str]]:
    table: set[tuple[str, str]] = set()
    for route in routes:
        if isinstance(route, APIRoute):
            table |= {(method, route.path) for method in route.methods}
        elif isinstance(route, APIWebSocketRoute):
            table.add(("WEBSOCKET", route.path))
    return table


def _declared_routes(source: Path) -> set[tuple[str, str]]:
    """Every ``@app.<method>("<path>")`` route the dashboard module declares itself."""
    declared: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "app"
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
            ):
                declared.add((decorator.func.attr.upper(), decorator.args[0].value))
    return declared


def test_the_dashboard_gains_exactly_the_jev_routes_and_keeps_its_own(tmp_path: Path) -> None:
    from bots.v13 import api as dashboard

    served = _route_table(dashboard.app.routes)
    jev = {route for route in served if route[1].startswith(f"{API_PREFIX}/")}
    assert jev == _route_table(create_router(tmp_path).routes)
    assert served - jev == _declared_routes(Path(dashboard.__file__))  # nothing else changed


@pytest.fixture()
def run_in_the_default_root() -> Iterator[str]:
    """A finished run in the real default run root, removed (with any directory the
    test created on the way) afterwards."""
    root = default_run_root()
    created = [path for path in (root.parents[1], root.parent, root) if not path.exists()]
    metadata = _metadata()
    run_dir = root / metadata.run_id
    try:
        yield _finished_run(root, metadata)
    finally:
        if run_dir.exists():  # also after a write that failed midway
            shutil.rmtree(run_dir)
        for directory in reversed(created):
            with contextlib.suppress(OSError):  # left in place if anything else is there
                directory.rmdir()


def test_the_dashboard_serves_the_runs_written_to_the_runner_s_default_root(
    run_in_the_default_root: str,
) -> None:
    from bots.v13 import api as dashboard

    response = TestClient(dashboard.app).get(f"/api/jev/runs/{run_in_the_default_root}")
    assert (response.status_code, response.json()["run_id"]) == (200, run_in_the_default_root)


def test_an_existing_dashboard_response_is_unchanged_by_the_jev_family(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bots.v13 import api as dashboard

    version_dir = tmp_path / "bots" / "v1"
    version_dir.mkdir(parents=True)
    (version_dir / "VERSION").write_text("v1", encoding="utf-8")
    manifest = {"version": "v1", "parent": None, "git_sha": "abc", "timestamp": "", "extra": {}}
    (version_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "bots" / "current").mkdir()
    (tmp_path / "data").mkdir()
    for name in ("logs", "replays"):
        (tmp_path / name).mkdir()
    (version_dir / "data").mkdir()
    monkeypatch.setattr(dashboard, "_REPO_ROOT", tmp_path)
    dashboard.configure(version_dir / "data", tmp_path / "logs", tmp_path / "replays")
    client = TestClient(dashboard.app)
    before = client.get("/api/versions")
    shutil.copytree(repository_root() / "bots" / "jev", tmp_path / "bots" / "jev")
    after = client.get("/api/versions")
    assert before.status_code == after.status_code == 200
    assert after.json() == before.json() and len(before.json()) == 1


def test_legacy_version_discovery_excludes_the_jev_family() -> None:
    assert (repository_root() / "bots" / "jev" / "v1" / "manifest.json").is_file()
    versions = registry.list_versions()
    assert "jev" not in versions
    assert registry.current_version() in versions  # discovery itself still works
