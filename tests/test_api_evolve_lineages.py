"""Tests for ``GET /api/evolve/lineages`` (Phase EL Step 5).

The endpoint joins three cross-version evolve registries living at the
repo-root ``data/`` dir (resolved via ``_evolve_dir``):

- ``lineages.json``     — keyed by ``lineage_id``
- ``fingerprints.json`` — keyed by ``version``
- ``evolve_results.jsonl`` — extinction rows (``phase == "extinction"``)

These tests configure the API with a tmp ``evolve_dir`` (the ``client``
fixture defaults ``evolve_dir`` to the same tmp ``data_dir``), seed the
registries on disk, and assert the projected response shape.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from bots.v13.api import app, configure
from fastapi.testclient import TestClient


@pytest.fixture()
def evolve_dir(tmp_path: Path) -> Path:
    """Configure the API against a tmp data/evolve dir and yield it."""
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    replay_dir = tmp_path / "replays"
    data_dir.mkdir()
    log_dir.mkdir()
    replay_dir.mkdir()
    configure(data_dir, log_dir, replay_dir, evolve_dir=data_dir)
    return data_dir


@pytest.fixture()
def client(evolve_dir: Path) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _seed_two_lineages(evolve_dir: Path) -> None:
    """Seed two lineages with shared-baseline head fingerprints + extinction.

    line-a head v1 and line-b head v2 share the ``rush`` + ``macro``
    baselines (so they are comparable) and disagree on them so the
    off-diagonal distance is a known non-zero value:

        |0.9-0.3| + |0.4-0.8| = 0.6 + 0.4 = 1.0, over 2 shared -> 0.5
    """
    lineages = {
        "line-a": {
            "lineage_id": "line-a",
            "head_version": "v1",
            "pool_path": "",
            "parent_chain": [],
            "created_at": "2026-06-01T00:00:00+00:00",
            "status": "active",
        },
        "line-b": {
            "lineage_id": "line-b",
            "head_version": "v2",
            "pool_path": "",
            "parent_chain": ["v1"],
            "created_at": "2026-06-02T00:00:00+00:00",
            "status": "active",
        },
    }
    fingerprints = {
        "v1": {
            "version": "v1",
            "per_baseline": {"rush": 0.9, "macro": 0.4},
            "computed_at": "2026-06-01T00:00:00+00:00",
        },
        "v2": {
            "version": "v2",
            "per_baseline": {"rush": 0.3, "macro": 0.8},
            "computed_at": "2026-06-02T00:00:00+00:00",
        },
    }
    (evolve_dir / "lineages.json").write_text(
        json.dumps(lineages), encoding="utf-8"
    )
    (evolve_dir / "fingerprints.json").write_text(
        json.dumps(fingerprints), encoding="utf-8"
    )
    rows = [
        json.dumps({"phase": "fitness", "generation": 1, "outcome": "x"}),
        json.dumps(
            {
                "phase": "extinction",
                "generation": 7,
                "lineage_id": "line-c",
                "head_version": "v5",
                "dominated_by": "line-a",
                "outcome": "culled",
                "reason": "redundant: distance 0.04 < 0.10",
            }
        ),
    ]
    (evolve_dir / "evolve_results.jsonl").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )


def test_empty_when_no_files(client: TestClient) -> None:
    """A fresh project (no registries) returns empty arrays, HTTP 200."""
    resp = client.get("/api/evolve/lineages")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lineages"] == []
    assert body["diversity_matrix"] == {"lineage_ids": [], "distances": []}
    assert body["extinction_events"] == []


def test_lineages_and_baseline_fitness(
    client: TestClient, evolve_dir: Path
) -> None:
    _seed_two_lineages(evolve_dir)
    resp = client.get("/api/evolve/lineages")
    assert resp.status_code == 200
    body = resp.json()

    lineages = body["lineages"]
    assert len(lineages) == 2
    by_id = {ln["lineage_id"]: ln for ln in lineages}

    a = by_id["line-a"]
    assert a["head_version"] == "v1"
    assert a["status"] == "active"
    # mean of {0.9, 0.4} = 0.65
    assert a["baseline_fitness"] == pytest.approx(0.65)
    assert a["per_baseline"] == {"rush": 0.9, "macro": 0.4}

    b = by_id["line-b"]
    # mean of {0.3, 0.8} = 0.55
    assert b["baseline_fitness"] == pytest.approx(0.55)


def test_diversity_matrix(client: TestClient, evolve_dir: Path) -> None:
    _seed_two_lineages(evolve_dir)
    body = client.get("/api/evolve/lineages").json()
    matrix = body["diversity_matrix"]
    assert matrix["lineage_ids"] == ["line-a", "line-b"]
    distances = matrix["distances"]
    assert len(distances) == 2
    assert len(distances[0]) == 2
    # Diagonal is exactly 0.0.
    assert distances[0][0] == 0.0
    assert distances[1][1] == 0.0
    # Off-diagonal: (|0.9-0.3| + |0.4-0.8|) / 2 = 0.5, symmetric.
    assert distances[0][1] == pytest.approx(0.5)
    assert distances[1][0] == pytest.approx(0.5)


def test_disjoint_baselines_yield_null_distance(
    client: TestClient, evolve_dir: Path
) -> None:
    """Heads with no shared baselines -> null (nan sentinel mapped)."""
    lineages = {
        "line-a": {"lineage_id": "line-a", "head_version": "v1"},
        "line-b": {"lineage_id": "line-b", "head_version": "v2"},
    }
    fingerprints = {
        "v1": {"version": "v1", "per_baseline": {"rush": 0.9}},
        "v2": {"version": "v2", "per_baseline": {"turtle": 0.2}},
    }
    (evolve_dir / "lineages.json").write_text(
        json.dumps(lineages), encoding="utf-8"
    )
    (evolve_dir / "fingerprints.json").write_text(
        json.dumps(fingerprints), encoding="utf-8"
    )
    body = client.get("/api/evolve/lineages").json()
    distances = body["diversity_matrix"]["distances"]
    assert distances[0][0] == 0.0
    assert distances[1][1] == 0.0
    # No shared baselines -> fingerprint_distance returns nan -> null.
    assert distances[0][1] is None
    assert distances[1][0] is None


def test_missing_fingerprint_yields_null_fitness_and_distance(
    client: TestClient, evolve_dir: Path
) -> None:
    """A head with no fingerprint -> null fitness, {} vector, null distances."""
    lineages = {
        "line-a": {"lineage_id": "line-a", "head_version": "v1"},
        "line-b": {"lineage_id": "line-b", "head_version": "v2"},
    }
    fingerprints = {
        "v1": {"version": "v1", "per_baseline": {"rush": 0.9, "macro": 0.4}},
        # v2 has no fingerprint entry.
    }
    (evolve_dir / "lineages.json").write_text(
        json.dumps(lineages), encoding="utf-8"
    )
    (evolve_dir / "fingerprints.json").write_text(
        json.dumps(fingerprints), encoding="utf-8"
    )
    body = client.get("/api/evolve/lineages").json()
    by_id = {ln["lineage_id"]: ln for ln in body["lineages"]}
    assert by_id["line-b"]["baseline_fitness"] is None
    assert by_id["line-b"]["per_baseline"] == {}
    distances = body["diversity_matrix"]["distances"]
    assert distances[0][1] is None
    assert distances[1][0] is None


def test_extinction_events(client: TestClient, evolve_dir: Path) -> None:
    _seed_two_lineages(evolve_dir)
    body = client.get("/api/evolve/lineages").json()
    events = body["extinction_events"]
    # Only the extinction-phase row is projected (the fitness row is dropped).
    assert len(events) == 1
    event = events[0]
    assert event["generation"] == 7
    assert event["lineage_id"] == "line-c"
    assert event["head_version"] == "v5"
    assert event["dominated_by"] == "line-a"
    assert event["reason"] == "redundant: distance 0.04 < 0.10"


def test_malformed_fingerprint_skipped_valid_lineage_renders(
    client: TestClient, evolve_dir: Path
) -> None:
    """A head whose fingerprint is malformed is skipped gracefully.

    Exercises the ``except ValueError`` (non-numeric win-rate) and the
    ``not isinstance(entry, dict)`` skip branches in the endpoint. The
    malformed head's lineage gets null fitness + empty per_baseline +
    null matrix row/col, while a valid sibling lineage still renders.
    """
    lineages = {
        "line-a": {"lineage_id": "line-a", "head_version": "v1"},
        "line-b": {"lineage_id": "line-b", "head_version": "v2"},
        "line-c": {"lineage_id": "line-c", "head_version": "v3"},
    }
    fingerprints = {
        # Valid head.
        "v1": {"version": "v1", "per_baseline": {"rush": 0.9, "macro": 0.4}},
        # Malformed: non-numeric win-rate -> Fingerprint.from_dict raises
        # ValueError -> head skipped.
        "v2": {"version": "v2", "per_baseline": {"rush": "high"}},
        # Malformed: non-dict entry value -> skipped at the isinstance guard.
        "v3": "not-a-dict",
    }
    (evolve_dir / "lineages.json").write_text(
        json.dumps(lineages), encoding="utf-8"
    )
    (evolve_dir / "fingerprints.json").write_text(
        json.dumps(fingerprints), encoding="utf-8"
    )

    resp = client.get("/api/evolve/lineages")
    assert resp.status_code == 200
    body = resp.json()
    by_id = {ln["lineage_id"]: ln for ln in body["lineages"]}

    # Valid lineage renders correctly.
    a = by_id["line-a"]
    assert a["baseline_fitness"] == pytest.approx(0.65)
    assert a["per_baseline"] == {"rush": 0.9, "macro": 0.4}

    # Malformed (non-numeric win-rate) head -> null fitness + empty vector.
    b = by_id["line-b"]
    assert b["baseline_fitness"] is None
    assert b["per_baseline"] == {}

    # Malformed (non-dict) head -> null fitness + empty vector.
    c = by_id["line-c"]
    assert c["baseline_fitness"] is None
    assert c["per_baseline"] == {}

    # Matrix row/col for the skipped heads is null; valid-valid pairs would
    # be a number, but here only line-a has a fingerprint so all off-diagonal
    # entries are null.
    distances = body["diversity_matrix"]["distances"]
    assert distances[0][0] == 0.0
    assert distances[0][1] is None  # line-a vs line-b (no fp)
    assert distances[1][0] is None  # line-b (no fp) vs line-a
    assert distances[0][2] is None  # line-a vs line-c (no fp)


def test_no_results_file_yields_empty_extinction(
    client: TestClient, evolve_dir: Path
) -> None:
    lineages = {"line-a": {"lineage_id": "line-a", "head_version": "v1"}}
    (evolve_dir / "lineages.json").write_text(
        json.dumps(lineages), encoding="utf-8"
    )
    body = client.get("/api/evolve/lineages").json()
    assert body["extinction_events"] == []
    assert len(body["lineages"]) == 1
