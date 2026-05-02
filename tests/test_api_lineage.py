"""Contract tests for ``GET /api/lineage`` (Step 1a, no lazy-init yet).

* Missing ``data/lineage.json`` → ``{nodes: [], edges: []}`` (NEVER 500).
* Existing file → parsed payload returned verbatim.
* Existing file missing one of the keys → backfilled with ``[]`` so
  the frontend can always destructure ``{nodes, edges}``.

Step 2 wires lazy-init via ``scripts/build_lineage.py`` — explicitly
NOT in scope for this step (would be a circular dependency).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

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
    yield
    get_error_log_buffer().reset()


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
        assert resp.json() == {"nodes": [], "edges": []}
