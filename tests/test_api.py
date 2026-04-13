"""Unit tests for REST API endpoints."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alpha4gate.api import app, configure
from alpha4gate.error_log import get_error_log_buffer


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


@pytest.fixture(autouse=True)
def clean_error_buffer() -> Iterator[None]:
    """Reset the process-wide error log buffer between tests.

    The buffer is a singleton and the ``_ErrorBufferHandler`` is installed
    on the root logger by ``configure()`` (which the ``client`` fixture
    runs before every test in this module), so any test that triggers
    an ERROR-level log would otherwise leak its count into subsequent
    tests. Autouse to guarantee isolation on this file.
    """
    get_error_log_buffer().reset()
    yield
    get_error_log_buffer().reset()


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
        assert data["total_games"] == 0
        assert data["recent_games"] == []

    def test_stats_from_db(self, client: TestClient, tmp_path: Path) -> None:
        import sqlite3

        db_path = tmp_path / "data" / "training.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE games (game_id TEXT, map_name TEXT, "
            "difficulty INTEGER, result TEXT, duration_secs REAL, "
            "total_reward REAL, model_version TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO games VALUES "
            "('g1','Simple64',1,'win',300,5.0,'v1','2026-01-01T00:00:00')"
        )
        conn.commit()
        conn.close()
        resp = client.get("/api/stats")
        data = resp.json()
        assert data["total_games"] == 1
        assert data["overall"]["wins"] == 1


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
        # Phase 4.5 #68: the alerts pipeline fields must always be present.
        assert "error_count_since_start" in data
        assert isinstance(data["error_count_since_start"], int)
        assert "recent_errors" in data
        assert isinstance(data["recent_errors"], list)

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

    def test_reward_trends_empty_no_directory(self, client: TestClient) -> None:
        resp = client.get("/api/training/reward-trends")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rules"] == []
        assert data["n_games"] == 0
        assert isinstance(data["generated_at"], str)

    def test_reward_trends_populated(self, client: TestClient, tmp_path: Path) -> None:
        reward_logs = tmp_path / "data" / "reward_logs"
        reward_logs.mkdir()
        game_a_lines = [
            {
                "game_time": 1.0,
                "total_reward": 0.3,
                "fired_rules": [
                    {"id": "army_supply_growth", "reward": 0.1},
                    {"id": "expand_bonus", "reward": 0.2},
                ],
                "is_terminal": False,
                "result": None,
            },
            {
                "game_time": 2.0,
                "total_reward": 0.4,
                "fired_rules": [
                    {"id": "army_supply_growth", "reward": 0.4},
                ],
                "is_terminal": True,
                "result": "win",
            },
        ]
        game_b_lines = [
            {
                "game_time": 1.5,
                "total_reward": 0.25,
                "fired_rules": [
                    {"id": "army_supply_growth", "reward": 0.25},
                ],
                "is_terminal": True,
                "result": "loss",
            },
        ]
        (reward_logs / "game_a.jsonl").write_text(
            "\n".join(json.dumps(r) for r in game_a_lines) + "\n",
            encoding="utf-8",
        )
        (reward_logs / "game_b.jsonl").write_text(
            "\n".join(json.dumps(r) for r in game_b_lines) + "\n",
            encoding="utf-8",
        )

        resp = client.get("/api/training/reward-trends")
        assert resp.status_code == 200
        data = resp.json()
        assert data["n_games"] == 2
        rules_by_id = {r["rule_id"]: r for r in data["rules"]}
        assert set(rules_by_id.keys()) == {"army_supply_growth", "expand_bonus"}
        assert rules_by_id["army_supply_growth"]["total_contribution"] == pytest.approx(
            0.1 + 0.4 + 0.25
        )
        # army_supply_growth appeared in both games
        assert len(rules_by_id["army_supply_growth"]["points"]) == 2
        assert rules_by_id["army_supply_growth"]["contribution_per_game"] == pytest.approx(
            (0.1 + 0.4 + 0.25) / 2
        )
        # expand_bonus only appeared in game_a
        assert rules_by_id["expand_bonus"]["total_contribution"] == pytest.approx(0.2)
        assert len(rules_by_id["expand_bonus"]["points"]) == 1

    def test_reward_trends_default_games_param(self, client: TestClient) -> None:
        resp = client.get("/api/training/reward-trends")
        assert resp.status_code == 200
        assert resp.json()["n_games"] == 0

    def test_reward_trends_explicit_games_param(self, client: TestClient) -> None:
        resp = client.get("/api/training/reward-trends?games=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rules"] == []
        assert data["n_games"] == 0

    def test_reward_trends_games_below_min(self, client: TestClient) -> None:
        resp = client.get("/api/training/reward-trends?games=0")
        assert resp.status_code == 422

    def test_reward_trends_games_above_max(self, client: TestClient) -> None:
        resp = client.get("/api/training/reward-trends?games=1001")
        assert resp.status_code == 422

    def test_reward_trends_games_non_numeric(self, client: TestClient) -> None:
        resp = client.get("/api/training/reward-trends?games=abc")
        assert resp.status_code == 422

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


class TestErrorLogStatusFields:
    """Phase 4.5 #68: /api/training/status surfaces backend ERROR events."""

    def test_emitting_error_increments_status_count(self, client: TestClient) -> None:
        """End-to-end: an ERROR-level log lands in /api/training/status.

        This proves the full wire-up: root logger -> ``_ErrorBufferHandler``
        -> ``ErrorLogBuffer`` -> ``get_training_status`` -> JSON response.
        A regression in any link of that chain (handler install missed,
        level filter broken, propagation disabled) is caught here. Keep
        direct ``buffer.emit()`` testing in ``tests/test_error_log_buffer.py``.
        """
        resp_before = client.get("/api/training/status")
        assert resp_before.status_code == 200
        assert resp_before.json()["error_count_since_start"] == 0
        assert resp_before.json()["recent_errors"] == []

        # Emit via the real logging API, not buffer.emit() — exercises
        # the full handler chain and the %d substitution in getMessage().
        test_logger = logging.getLogger("alpha4gate.test_api")
        test_logger.error("synthetic test error %d", 42)

        resp_after = client.get("/api/training/status")
        assert resp_after.status_code == 200
        body = resp_after.json()
        assert body["error_count_since_start"] == 1
        assert len(body["recent_errors"]) == 1
        record = body["recent_errors"][0]
        assert record["level"] == "ERROR"
        assert "alpha4gate.test_api" in record["logger"]
        assert "synthetic test error 42" in record["message"]


class TestDebugRaiseErrorEndpoint:
    """Phase 4.5 #68: synthetic error trigger for soak-test pre-flight."""

    def test_returns_404_when_flag_unset(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DEBUG_ENDPOINTS", raising=False)
        resp = client.post("/api/debug/raise_error", json={})
        assert resp.status_code == 404
        # And the buffer count must not have moved.
        assert get_error_log_buffer().snapshot()[0] == 0

    def test_returns_200_and_logs_when_flag_set(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DEBUG_ENDPOINTS", "1")
        resp = client.post("/api/debug/raise_error", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["logged"] == "Synthetic alerts pre-flight test"
        total, records = get_error_log_buffer().snapshot()
        assert total == 1
        assert "synthetic error: Synthetic alerts pre-flight test" in records[0]["message"]

    def test_custom_message_is_used(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DEBUG_ENDPOINTS", "1")
        resp = client.post("/api/debug/raise_error", json={"message": "operator preflight"})
        assert resp.status_code == 200
        assert resp.json()["logged"] == "operator preflight"
        _, records = get_error_log_buffer().snapshot()
        assert "operator preflight" in records[0]["message"]

    def test_flag_accepts_truthy_values(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for value in ("true", "TRUE", "yes", "1"):
            monkeypatch.setenv("DEBUG_ENDPOINTS", value)
            resp = client.post("/api/debug/raise_error", json={})
            assert resp.status_code == 200, f"value={value!r}"


class TestAdvisedEndpoints:
    """Tests for the advised run control panel API (GET/PUT /api/advised/*)."""

    def test_get_state_idle_when_no_file(self, client: TestClient) -> None:
        resp = client.get("/api/advised/state")
        assert resp.status_code == 200
        assert resp.json()["status"] == "idle"

    def test_get_state_returns_file_content(self, client: TestClient, tmp_path: Path) -> None:
        state = {
            "run_id": "20260412-1832",
            "status": "running",
            "phase": 2,
            "phase_name": "Strategic Analysis",
            "iteration": 1,
            "games_per_cycle": 10,
            "elapsed_seconds": 600,
        }
        (tmp_path / "data" / "advised_run_state.json").write_text(json.dumps(state))
        resp = client.get("/api/advised/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["phase"] == 2
        assert data["run_id"] == "20260412-1832"

    def test_get_control_defaults_when_no_file(self, client: TestClient) -> None:
        resp = client.get("/api/advised/control")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stop_run"] is False
        assert data["reset_loop"] is False
        assert data["user_hint"] is None

    def test_put_control_creates_file(self, client: TestClient, tmp_path: Path) -> None:
        resp = client.put("/api/advised/control", json={"games_per_cycle": 3})
        assert resp.status_code == 200
        data = resp.json()
        assert data["games_per_cycle"] == 3
        assert data["updated_at"] is not None

        # Verify file was created
        path = tmp_path / "data" / "advised_run_control.json"
        assert path.exists()
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content["games_per_cycle"] == 3

    def test_put_control_merges_with_existing(self, client: TestClient, tmp_path: Path) -> None:
        # Set initial control
        client.put("/api/advised/control", json={"games_per_cycle": 5, "difficulty": 2})
        # Merge with new field
        resp = client.put("/api/advised/control", json={"user_hint": "attack walk"})
        data = resp.json()
        assert data["games_per_cycle"] == 5  # preserved
        assert data["difficulty"] == 2  # preserved
        assert data["user_hint"] == "attack walk"  # added

    def test_put_control_overwrites_existing_field(self, client: TestClient) -> None:
        client.put("/api/advised/control", json={"games_per_cycle": 5})
        resp = client.put("/api/advised/control", json={"games_per_cycle": 3})
        assert resp.json()["games_per_cycle"] == 3

    def test_get_state_returns_idle_on_corrupt_file(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        (tmp_path / "data" / "advised_run_state.json").write_text("not json!")
        resp = client.get("/api/advised/state")
        assert resp.status_code == 200
        assert resp.json()["status"] == "idle"


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
