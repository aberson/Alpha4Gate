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


class TestTrainingEndpoints:
    def test_training_status_empty(self, client: TestClient) -> None:
        resp = client.get("/api/training/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_games"] == 0
        assert data["current_checkpoint"] is None
        # Step 1: reward_logs directory does not exist in the empty fixture.
        assert data["reward_logs_size_bytes"] == 0

    def test_training_status_reward_logs_size_with_files(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        reward_logs = tmp_path / "data" / "reward_logs"
        reward_logs.mkdir()
        payload_a = b'{"game_time": 1.0, "total_reward": 0.1, "fired_rules": []}\n'
        payload_b = (
            b'{"game_time": 2.0, "total_reward": 0.2, "fired_rules": [], '
            b'"is_terminal": true, "result": "win"}\n'
        )
        (reward_logs / "game_a.jsonl").write_bytes(payload_a)
        (reward_logs / "game_b.jsonl").write_bytes(payload_b)

        resp = client.get("/api/training/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["reward_logs_size_bytes"] == len(payload_a) + len(payload_b)

    def test_training_history_empty(self, client: TestClient) -> None:
        resp = client.get("/api/training/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total_games", 0) == 0 or data.get("games") == []

    def test_training_checkpoints_empty(self, client: TestClient) -> None:
        resp = client.get("/api/training/checkpoints")
        assert resp.status_code == 200
        assert resp.json()["checkpoints"] == []

    def test_training_start(self, client: TestClient) -> None:
        resp = client.post("/api/training/start", json={"mode": "rl"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"
        # Clean up: stop the daemon
        client.post("/api/training/stop")

    def test_training_stop(self, client: TestClient) -> None:
        resp = client.post("/api/training/stop")
        assert resp.status_code == 200

    def test_training_models_empty(self, client: TestClient) -> None:
        resp = client.get("/api/training/models")
        assert resp.status_code == 200
        assert resp.json()["models"] == []

    def test_training_models_with_data(self, client: TestClient, tmp_path: Path) -> None:
        from alpha4gate.learning.database import TrainingDB

        db = TrainingDB(tmp_path / "data" / "training.db")
        db.store_game("g0", "Simple64", 1, "win", 300.0, 5.0, "v1")
        db.store_game("g1", "Simple64", 1, "loss", 300.0, -5.0, "v1")
        db.store_game("g2", "Simple64", 2, "win", 300.0, 5.0, "v2")
        db.close()

        resp = client.get("/api/training/models")
        assert resp.status_code == 200
        models = resp.json()["models"]
        assert len(models) == 2
        assert models[0]["model_version"] == "v1"
        assert models[0]["wins"] == 1
        assert models[0]["losses"] == 1
        assert models[0]["total"] == 2
        assert models[0]["win_rate"] == 0.5
        assert models[0]["first_game"] is not None
        assert models[0]["last_game"] is not None
        assert models[1]["model_version"] == "v2"
        assert models[1]["wins"] == 1
        assert models[1]["total"] == 1


class TestRewardRulesEndpoints:
    def test_get_empty_rules(self, client: TestClient) -> None:
        resp = client.get("/api/reward-rules")
        assert resp.status_code == 200
        assert resp.json()["rules"] == []

    def test_put_and_get_rules(self, client: TestClient) -> None:
        rules = {
            "rules": [
                {
                    "id": "test-rule",
                    "description": "Test",
                    "condition": {"field": "minerals", "op": ">", "value": 500},
                    "requires": None,
                    "reward": 0.1,
                    "active": True,
                }
            ]
        }
        resp = client.put("/api/reward-rules", json=rules)
        assert resp.status_code == 200
        assert resp.json()["updated"] is True
        assert resp.json()["rule_count"] == 1

        # Verify it persisted
        resp2 = client.get("/api/reward-rules")
        assert len(resp2.json()["rules"]) == 1
        assert resp2.json()["rules"][0]["id"] == "test-rule"
