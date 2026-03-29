"""Unit tests for REST API endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alpha4gate.api import app, configure


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """Create a test client with temporary data directories."""
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    replay_dir = tmp_path / "replays"
    data_dir.mkdir()
    log_dir.mkdir()
    replay_dir.mkdir()
    configure(data_dir, log_dir, replay_dir)
    return TestClient(app)


class TestStatusEndpoint:
    def test_idle_status(self, client: TestClient) -> None:
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "idle"
        assert data["game_step"] is None


class TestStatsEndpoint:
    def test_empty_stats(self, client: TestClient) -> None:
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["games"] == []

    def test_stats_from_file(self, client: TestClient, tmp_path: Path) -> None:
        stats = {"games": [{"result": "win"}], "aggregates": {"total_wins": 1, "total_losses": 0}}
        (tmp_path / "data" / "stats.json").write_text(json.dumps(stats))
        resp = client.get("/api/stats")
        assert resp.json()["games"][0]["result"] == "win"


class TestBuildOrderEndpoints:
    def test_empty_build_orders(self, client: TestClient) -> None:
        resp = client.get("/api/build-orders")
        assert resp.status_code == 200
        assert resp.json()["orders"] == []

    def test_create_and_get(self, client: TestClient) -> None:
        order = {
            "name": "Test Build",
            "steps": [{"supply": 14, "action": "build", "target": "Pylon"}],
        }
        create_resp = client.post("/api/build-orders", json=order)
        assert create_resp.status_code == 200
        assert create_resp.json()["created"] is True
        assert create_resp.json()["id"] == "test-build"

        get_resp = client.get("/api/build-orders")
        assert len(get_resp.json()["orders"]) == 1

    def test_delete_build_order(self, client: TestClient) -> None:
        order = {
            "id": "test",
            "name": "Test",
            "steps": [],
        }
        client.post("/api/build-orders", json=order)
        del_resp = client.delete("/api/build-orders/test")
        assert del_resp.json()["deleted"] is True

        get_resp = client.get("/api/build-orders")
        assert len(get_resp.json()["orders"]) == 0

    def test_delete_nonexistent(self, client: TestClient) -> None:
        resp = client.delete("/api/build-orders/nonexistent")
        assert resp.json()["deleted"] is False


class TestReplayEndpoints:
    def test_empty_replays(self, client: TestClient) -> None:
        resp = client.get("/api/replays")
        assert resp.status_code == 200
        assert resp.json()["replays"] == []

    def test_replay_listed(self, client: TestClient, tmp_path: Path) -> None:
        (tmp_path / "replays" / "game_2026-03-29T14-30-00.SC2Replay").write_bytes(b"dummy")
        resp = client.get("/api/replays")
        replays = resp.json()["replays"]
        assert len(replays) == 1
        assert replays[0]["id"] == "2026-03-29T14-30-00"

    def test_replay_detail_placeholder(self, client: TestClient) -> None:
        resp = client.get("/api/replays/2026-03-29T14-30-00")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "2026-03-29T14-30-00"
        assert data["timeline"] == []


class TestDecisionLogEndpoint:
    def test_empty_log(self, client: TestClient) -> None:
        resp = client.get("/api/decision-log")
        assert resp.status_code == 200
        assert resp.json()["entries"] == []

    def test_log_from_file(self, client: TestClient, tmp_path: Path) -> None:
        entries = {"entries": [{"from_state": "opening", "to_state": "expand"}]}
        (tmp_path / "data" / "decision_audit.json").write_text(json.dumps(entries))
        resp = client.get("/api/decision-log")
        assert len(resp.json()["entries"]) == 1


class TestGameEndpoints:
    def test_start_game(self, client: TestClient) -> None:
        resp = client.post("/api/game/start", json={"map": "Simple64", "difficulty": "Easy"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "starting"

    def test_start_batch(self, client: TestClient) -> None:
        resp = client.post(
            "/api/game/batch",
            json={"count": 5, "map": "Simple64", "difficulty": "Easy"},
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 5
        assert resp.json()["status"] == "running"
