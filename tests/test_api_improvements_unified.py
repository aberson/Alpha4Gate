"""Contract tests for ``GET /api/improvements/unified`` on ``bots/v10``.

Step 4 of the Models-tab build plan exposes the unified-improvements
endpoint that previously only existed on ``bots/v0/api.py`` so the v10
runner that drives the dashboard can subsume the legacy Improvements
tab into the Lineage view's timeline mode.

Deeper coverage of the merging / sorting / outcome-mapping behaviour
already lives in ``tests/test_api.py::TestImprovementsUnifiedEndpoint``
(against ``bots/v0``). These tests are intentionally narrow: confirm
the endpoint is wired on v10 and emits the expected shape with both
sources present.
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
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """Configure the v10 app with cross-version data at ``tmp_path/data``.

    Both ``improvement_log.json`` and ``evolve_results.jsonl`` live in
    the cross-version data dir resolved by ``_cross_version_data_dir()``
    (``_REPO_ROOT / "data"``). Patch ``_REPO_ROOT`` so the resolver
    points into ``tmp_path``; same pattern as ``test_api_lineage.py``.
    """
    data_dir = tmp_path / "bots" / "v10" / "data"
    log_dir = tmp_path / "logs"
    replay_dir = tmp_path / "replays"
    (tmp_path / "data").mkdir()
    data_dir.mkdir(parents=True)
    log_dir.mkdir()
    replay_dir.mkdir()
    monkeypatch.setattr(v10_api, "_REPO_ROOT", tmp_path)
    configure(data_dir, log_dir, replay_dir)
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_error_buffer() -> Iterator[None]:
    get_error_log_buffer().reset()
    yield
    get_error_log_buffer().reset()


class TestImprovementsUnifiedEndpointV10:
    """``GET /api/improvements/unified`` on v10 mirrors the v0 contract.

    Two thin tests that just verify wiring: empty fixture returns an
    empty list, populated fixture returns the merged + sorted shape.
    """

    def test_empty_fixture_returns_empty_list(
        self, client: TestClient
    ) -> None:
        resp = client.get("/api/improvements/unified")
        assert resp.status_code == 200
        assert resp.json() == {"improvements": []}

    def test_merges_advised_and_evolve_with_expected_shape(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        # One advised entry + one evolve entry; expect both back in
        # timestamp-descending order with the documented per-entry shape.
        advised_path = tmp_path / "data" / "improvement_log.json"
        advised_path.write_text(
            json.dumps(
                {
                    "improvements": [
                        {
                            "id": "advised-20260412-2007-iter1",
                            "timestamp": "2026-04-12T20:50:00Z",
                            "run_id": "20260412-2007",
                            "iteration": 1,
                            "title": "Stronger mineral floating penalties",
                            "type": "training",
                            "description": "Tweaked thresholds.",
                            "principles": ["§4.2 Resource Spending"],
                            "result": "pass",
                            "metrics": {
                                "validation_wins": 7,
                                "validation_total": 10,
                            },
                            "files_changed": ["data/reward_rules.json"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        evolve_path = tmp_path / "data" / "evolve_results.jsonl"
        evolve_path.write_text(
            json.dumps(
                {
                    "phase": "fitness",
                    "generation": 2,
                    "parent": "v3",
                    "imp": {
                        "rank": 1,
                        "title": "Gas-dump warp priority",
                        "type": "dev",
                        "description": "Switch warp queue to gas units.",
                        "principle_ids": ["4.2", "11.2"],
                        "files_touched": ["bots/v3/bot.py"],
                    },
                    "candidate": "cand_2e57ef46",
                    "record": [],
                    "wins_cand": 3,
                    "wins_parent": 2,
                    "games": 5,
                    "outcome": "fitness-pass",
                    "reason": "",
                    "timestamp": "2026-04-29T21:34:32Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        resp = client.get("/api/improvements/unified")
        assert resp.status_code == 200
        body = resp.json()
        assert "improvements" in body
        entries = body["improvements"]
        assert len(entries) == 2

        # Timestamp-descending: the evolve row (2026-04-29) sorts first
        # ahead of the advised row (2026-04-12).
        assert [e["source"] for e in entries] == ["evolve", "advised"]

        # Shape contract: every entry carries the documented keys and
        # the ``_commit_sha`` book-keeping field is stripped.
        expected_keys = {
            "id",
            "source",
            "timestamp",
            "title",
            "description",
            "type",
            "outcome",
            "metric",
            "principles",
            "files_changed",
        }
        for entry in entries:
            assert set(entry.keys()) == expected_keys
            assert "_commit_sha" not in entry

        # Spot-check the normalised values to catch helper drift.
        evolve_entry = entries[0]
        assert evolve_entry["title"] == "Gas-dump warp priority"
        assert evolve_entry["outcome"] == "fitness-pass"
        assert evolve_entry["metric"] == "3-2 vs v3"

        advised_entry = entries[1]
        assert advised_entry["outcome"] == "promoted"
        assert advised_entry["metric"] == "7/10 wins (validation)"
