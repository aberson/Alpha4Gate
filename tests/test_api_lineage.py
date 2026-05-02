"""Contract tests for ``GET /api/lineage`` (Step 1a + Step 2 lazy-init).

* Missing ``data/lineage.json`` → run ``build_lineage.py`` lazy-init,
  then return the freshly built file. Subprocess failure → fall back
  to ``{nodes: [], edges: []}`` (NEVER 500).
* Existing file → parsed payload returned verbatim.
* Existing file missing one of the keys → backfilled with ``[]`` so
  the frontend can always destructure ``{nodes, edges}``.
* Concurrent first-time requests → subprocess invoked exactly ONCE
  (process-wide ``asyncio.Lock`` + double-checked locking).

Step 2 (this commit) wires lazy-init via ``scripts/build_lineage.py``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from bots.v10 import api as v10_api
from bots.v10.api import app, configure
from bots.v10.error_log import get_error_log_buffer
from fastapi.testclient import TestClient


@pytest.fixture()
def staged_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Stage a fake repo root with ``data/`` and patch ``_REPO_ROOT``."""
    (tmp_path / "data").mkdir()
    (tmp_path / "bots").mkdir()
    (tmp_path / "bots" / "current").mkdir()
    monkeypatch.setattr(v10_api, "_REPO_ROOT", tmp_path)
    yield tmp_path


@pytest.fixture()
def client(staged_repo: Path, tmp_path: Path) -> TestClient:
    log_dir = tmp_path / "logs"
    replay_dir = tmp_path / "replays"
    log_dir.mkdir()
    replay_dir.mkdir()
    per_v = staged_repo / "bots" / "v0" / "data"
    per_v.mkdir(parents=True, exist_ok=True)
    configure(per_v, log_dir, replay_dir)
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_error_buffer() -> Iterator[None]:
    get_error_log_buffer().reset()
    # Also reset the lazy-init lock between tests so each test gets a
    # fresh ``asyncio.Lock`` bound to its own event loop. Otherwise a
    # lock created against TestClient #1's loop would deadlock when
    # TestClient #2 awaited it on a different loop.
    v10_api._lineage_lazy_init_lock = None
    yield
    get_error_log_buffer().reset()
    v10_api._lineage_lazy_init_lock = None


class TestLineageEndpoint:
    """``GET /api/lineage`` — graceful empty fallback + verbatim parse."""

    def test_missing_file_returns_empty_skeleton(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # No data/lineage.json staged.
        assert not (staged_repo / "data" / "lineage.json").exists()
        resp = client.get("/api/lineage")
        assert resp.status_code == 200
        assert resp.json() == {"nodes": [], "edges": []}

    def test_existing_file_returned_verbatim(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        payload = {
            "nodes": [
                {
                    "id": "v0",
                    "version": "v0",
                    "race": "protoss",
                    "harness_origin": "manual",
                    "parent": None,
                },
                {
                    "id": "v3",
                    "version": "v3",
                    "race": "protoss",
                    "harness_origin": "advised",
                    "parent": "v0",
                },
            ],
            "edges": [
                {
                    "from": "v0",
                    "to": "v3",
                    "harness": "advised",
                    "improvement_title": "Stronger mineral floats",
                    "ts": "2026-04-12T20:50:00Z",
                    "outcome": "promoted",
                }
            ],
        }
        (staged_repo / "data" / "lineage.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

        resp = client.get("/api/lineage")
        assert resp.status_code == 200
        assert resp.json() == payload

    def test_partial_file_backfills_missing_keys(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # On-disk file with ``nodes`` but no ``edges`` — endpoint
        # backfills the missing key so the frontend can always
        # destructure ``{nodes, edges}`` without null-coalescing.
        (staged_repo / "data" / "lineage.json").write_text(
            json.dumps({"nodes": [{"id": "v0"}]}), encoding="utf-8"
        )
        body = client.get("/api/lineage").json()
        assert body["nodes"] == [{"id": "v0"}]
        assert body["edges"] == []

    def test_malformed_json_returns_empty_skeleton(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # Half-written file → ``_read_json_file`` returns None →
        # endpoint falls back to the empty skeleton (never 500).
        (staged_repo / "data" / "lineage.json").write_text(
            "{not json", encoding="utf-8"
        )
        resp = client.get("/api/lineage")
        assert resp.status_code == 200
        assert resp.json() == {"nodes": [], "edges": []}

    def test_top_level_list_returns_empty_skeleton(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """A corrupted ``lineage.json`` whose top-level value is a JSON
        list (not a dict) must NOT crash the endpoint. ``_read_json_file``
        guards against non-dict payloads → returns ``None`` → endpoint
        falls back to the empty skeleton (never 500).
        """
        (staged_repo / "data" / "lineage.json").write_text(
            json.dumps([1, 2, 3]), encoding="utf-8"
        )
        resp = client.get("/api/lineage")
        assert resp.status_code == 200
        assert resp.json() == {"nodes": [], "edges": []}

    def test_lineage_uses_cross_version_resolver(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """Smoke-test the resolver wiring — the file must be read from
        the cross-version dir (``<repo>/data/``), NOT a per-version dir
        (``<repo>/bots/vN/data/``). If a developer accidentally swaps
        the resolvers, this test fails because the per-version-staged
        file is never reached.
        """
        # Stage a "wrong" file at the per-version path. It must NOT be
        # picked up.
        per_v = staged_repo / "bots" / "v0" / "data"
        per_v.mkdir(parents=True, exist_ok=True)
        (per_v / "lineage.json").write_text(
            json.dumps({"nodes": [{"id": "WRONG"}], "edges": []}),
            encoding="utf-8",
        )
        resp = client.get("/api/lineage")
        assert resp.status_code == 200
        # File at cross-version path is missing → empty skeleton.
        # (Lazy-init runs build_lineage.py but it's not staged in the
        # fake repo, so the warn-and-fall-back path triggers.)
        assert resp.json() == {"nodes": [], "edges": []}


class TestLazyInit:
    """Step 2 — lazy-init wires ``scripts/build_lineage.py`` on cache miss."""

    def test_lazy_init_runs_build_script_when_file_missing(
        self,
        client: TestClient,
        staged_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cache miss → build_lineage subprocess invoked → fixture returned."""
        assert not (staged_repo / "data" / "lineage.json").exists()

        fixture_payload = {
            "nodes": [{"id": "v0", "version": "v0", "race": "protoss",
                       "harness_origin": "manual", "parent": None}],
            "edges": [],
        }

        # The lazy-init helper checks for the existence of
        # ``scripts/build_lineage.py`` first. Stage it so the helper
        # actually invokes subprocess.run (instead of warn-and-skip).
        (staged_repo / "scripts").mkdir(parents=True, exist_ok=True)
        (staged_repo / "scripts" / "build_lineage.py").write_text(
            "# stub\n", encoding="utf-8"
        )

        captured_calls: list[list[str]] = []

        def fake_run(
            args: list[str], **_kwargs: Any
        ) -> subprocess.CompletedProcess[str]:
            captured_calls.append(list(args))
            # Side-effect: write the fixture lineage.json so the
            # endpoint's post-build re-read succeeds.
            (staged_repo / "data" / "lineage.json").write_text(
                json.dumps(fixture_payload), encoding="utf-8"
            )
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="", stderr=""
            )

        monkeypatch.setattr(v10_api.subprocess, "run", fake_run)

        resp = client.get("/api/lineage")
        assert resp.status_code == 200
        assert resp.json() == fixture_payload
        # Subprocess WAS invoked.
        assert len(captured_calls) == 1
        assert any(
            "build_lineage.py" in arg for arg in captured_calls[0]
        )

    def test_lazy_init_subprocess_failure_falls_back_to_empty(
        self,
        client: TestClient,
        staged_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Build subprocess crashes → endpoint returns empty skeleton, not 500."""
        (staged_repo / "scripts").mkdir(parents=True, exist_ok=True)
        (staged_repo / "scripts" / "build_lineage.py").write_text(
            "# stub\n", encoding="utf-8"
        )

        def fake_run(
            args: list[str], **_kwargs: Any
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="boom"
            )

        monkeypatch.setattr(v10_api.subprocess, "run", fake_run)

        resp = client.get("/api/lineage")
        assert resp.status_code == 200
        assert resp.json() == {"nodes": [], "edges": []}

    def test_lazy_init_concurrent_calls_serialize(
        self,
        staged_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Two concurrent first-time requests → subprocess fires exactly ONCE.

        We bypass TestClient and exercise ``get_lineage`` directly with
        ``asyncio.gather`` so both coroutines hit the lock with the
        file genuinely missing. The first to acquire the lock writes
        the fixture; the second double-checks inside the lock and
        finds the file present, skipping the subprocess call.

        Determinism: we monkeypatch ``_run_build_lineage_sync`` (the
        ``asyncio.to_thread`` target) with an *async* shim that awaits
        ``asyncio.sleep(0)`` once before doing its side-effect work.
        That explicit yield deterministically forces the event loop to
        schedule the second coroutine, guaranteeing it has time to
        contend on the lock — without relying on ``to_thread``'s
        implicit thread-handoff yield, whose timing varies across
        asyncio implementations.
        """
        assert not (staged_repo / "data" / "lineage.json").exists()

        # Wire configure() so the per-version dir helpers don't crash.
        log_dir = tmp_path / "logs2"
        replay_dir = tmp_path / "replays2"
        log_dir.mkdir(exist_ok=True)
        replay_dir.mkdir(exist_ok=True)
        per_v = staged_repo / "bots" / "v0" / "data"
        per_v.mkdir(parents=True, exist_ok=True)
        configure(per_v, log_dir, replay_dir)

        (staged_repo / "scripts").mkdir(parents=True, exist_ok=True)
        (staged_repo / "scripts" / "build_lineage.py").write_text(
            "# stub\n", encoding="utf-8"
        )

        fixture_payload = {"nodes": [{"id": "v0"}], "edges": []}
        call_count = {"n": 0}

        # Replace ``asyncio.to_thread`` with an inline awaitable so the
        # "build" runs *on the event loop* and yields explicitly via
        # ``asyncio.sleep(0)``. That deterministically forces the
        # second coroutine to be scheduled while the first is still
        # inside the lock-protected build region — giving us the
        # tightest possible test of the double-checked-lock contract.
        async def fake_to_thread(
            fn: Any, /, *_args: Any, **_kwargs: Any
        ) -> Any:
            # Only the build_lineage call goes through to_thread in
            # this codepath; assert that to keep the test honest.
            assert fn is v10_api._run_build_lineage_sync
            call_count["n"] += 1
            # Explicit yield — deterministic interleave point.
            await asyncio.sleep(0)
            (staged_repo / "data" / "lineage.json").write_text(
                json.dumps(fixture_payload), encoding="utf-8"
            )
            return None

        monkeypatch.setattr(v10_api.asyncio, "to_thread", fake_to_thread)

        async def _two_concurrent() -> tuple[Any, Any]:
            return await asyncio.gather(
                v10_api.get_lineage(),
                v10_api.get_lineage(),
            )

        r1, r2 = asyncio.run(_two_concurrent())

        assert r1 == fixture_payload
        assert r2 == fixture_payload
        # The subprocess MUST only fire once thanks to the
        # double-checked-lock inside the endpoint.
        assert call_count["n"] == 1, (
            f"expected exactly 1 subprocess invocation, got {call_count['n']}"
        )
