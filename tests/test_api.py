"""Unit tests for REST API endpoints."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from bots.v0.api import app, configure
from bots.v0.error_log import get_error_log_buffer
from fastapi.testclient import TestClient


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


class TestOperatorCommandsEndpoint:
    """`/api/operator-commands` reads the wiki doc from disk so the Help
    dashboard tab stays in sync with the on-disk markdown without a
    rebuild. Test confirms the file is found and the response shape is
    `{"markdown": "<contents>"}`."""

    def test_returns_markdown(self, client: TestClient) -> None:
        resp = client.get("/api/operator-commands")
        assert resp.status_code == 200
        data = resp.json()
        assert "markdown" in data
        # Sanity: real content, not an empty string. Look for a known
        # heading from the doc; if the doc is renamed the test breaks
        # which is the right signal.
        assert "# Operator commands" in data["markdown"]


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

    def test_training_history_empty(self, client: TestClient) -> None:
        resp = client.get("/api/training/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total_games", 0) == 0 or data.get("games") == []


class TestDaemonStatusEndpoints:
    """`/api/training/daemon` and `/api/training/triggers` feed
    ``useDaemonStatus`` -> ``useAlerts`` (Alerts tab). Reclassified KEPT
    in dashboard refactor Step 6 because the Alerts pipeline depends on
    them even after the Loop tab is gone."""

    def test_daemon_status_returns_payload(self, client: TestClient) -> None:
        resp = client.get("/api/training/daemon")
        assert resp.status_code == 200
        data = resp.json()
        # Daemon is configured but not running in the test fixture.
        assert "running" in data
        assert data["running"] is False

    def test_triggers_returns_payload(self, client: TestClient) -> None:
        resp = client.get("/api/training/triggers")
        assert resp.status_code == 200
        data = resp.json()
        assert "would_trigger" in data
        assert "transitions_since_last" in data


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
        test_logger = logging.getLogger("bots.v0.test_api")
        test_logger.error("synthetic test error %d", 42)

        resp_after = client.get("/api/training/status")
        assert resp_after.status_code == 200
        body = resp_after.json()
        assert body["error_count_since_start"] == 1
        assert len(body["recent_errors"]) == 1
        record = body["recent_errors"][0]
        assert record["level"] == "ERROR"
        assert "bots.v0.test_api" in record["logger"]
        assert "synthetic test error 42" in record["message"]


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


class TestEvolveEndpoints:
    """Tests for the evolve run dashboard API (GET/PUT /api/evolve/*)."""

    def test_get_state_idle_when_no_file(self, client: TestClient) -> None:
        resp = client.get("/api/evolve/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"
        # Idle skeleton carries the new generation-phase keys so the
        # frontend can destructure without null-coalescing every field.
        # ``run_id`` + ``concurrency`` are Step 4 additions for the
        # parallel-evolve dashboard; they live alongside the rest of the
        # run-state shape so single-flight callers see them as ``None``.
        for key in (
            "parent_start",
            "parent_current",
            "started_at",
            "wall_budget_hours",
            "generation_index",
            "generations_completed",
            "generations_promoted",
            "evictions",
            "resurrections_remaining",
            "pool_remaining_count",
            "last_result",
            "run_id",
            "concurrency",
        ):
            assert key in data
            assert data[key] is None

    def test_get_state_returns_file_content(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        state = {
            "status": "running",
            "parent_start": "v0",
            "parent_current": "v1",
            "started_at": "2026-04-19T10:00:00+00:00",
            "wall_budget_hours": 4.0,
            "generation_index": 3,
            "generations_completed": 2,
            "generations_promoted": 1,
            "evictions": 4,
            "resurrections_remaining": 3,
            "pool_remaining_count": 6,
            "last_result": None,
        }
        (tmp_path / "data" / "evolve_run_state.json").write_text(
            json.dumps(state)
        )
        resp = client.get("/api/evolve/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["parent_start"] == "v0"
        assert data["generations_completed"] == 2
        assert data["generations_promoted"] == 1

    def test_get_state_returns_idle_on_corrupt_file(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        (tmp_path / "data" / "evolve_run_state.json").write_text("not json!")
        resp = client.get("/api/evolve/state")
        assert resp.status_code == 200
        assert resp.json()["status"] == "idle"

    def test_get_control_defaults_when_no_file(self, client: TestClient) -> None:
        resp = client.get("/api/evolve/control")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"stop_run": False, "pause_after_round": False}

    def test_put_control_stop_run_creates_file(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        resp = client.put("/api/evolve/control", json={"stop_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["stop_run"] is True
        assert data["pause_after_round"] is False

        # Verify file was created atomically
        path = tmp_path / "data" / "evolve_run_control.json"
        assert path.exists()
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content["stop_run"] is True

    def test_put_control_pause_after_round(self, client: TestClient) -> None:
        resp = client.put(
            "/api/evolve/control", json={"pause_after_round": True}
        )
        assert resp.status_code == 200
        assert resp.json()["pause_after_round"] is True

    def test_put_control_merges_with_existing(self, client: TestClient) -> None:
        client.put("/api/evolve/control", json={"pause_after_round": True})
        resp = client.put("/api/evolve/control", json={"stop_run": True})
        data = resp.json()
        # Previous pause flag should be preserved, new stop flag added.
        assert data["pause_after_round"] is True
        assert data["stop_run"] is True

    def test_put_control_rejects_unknown_field(self, client: TestClient) -> None:
        resp = client.put(
            "/api/evolve/control", json={"stop_run": True, "bogus": 1}
        )
        assert resp.status_code == 400

    def test_put_control_rejects_non_bool(self, client: TestClient) -> None:
        resp = client.put("/api/evolve/control", json={"stop_run": "yes"})
        assert resp.status_code == 400

    def test_get_pool_empty_when_no_file(self, client: TestClient) -> None:
        resp = client.get("/api/evolve/pool")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"parent": None, "generated_at": None, "pool": []}

    def test_get_pool_returns_file_content(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        pool = {
            "parent": "v0",
            "generated_at": "2026-04-19T10:00:00+00:00",
            "pool": [
                {
                    "rank": 1,
                    "title": "Reward scouting",
                    "type": "training",
                    "description": "...",
                    "principle_ids": [],
                    "expected_impact": "...",
                    "concrete_change": "{}",
                    "status": "active",
                },
            ],
        }
        (tmp_path / "data" / "evolve_pool.json").write_text(json.dumps(pool))
        resp = client.get("/api/evolve/pool")
        assert resp.status_code == 200
        data = resp.json()
        assert data["parent"] == "v0"
        assert len(data["pool"]) == 1
        assert data["pool"][0]["title"] == "Reward scouting"

    def test_get_results_empty_when_no_file(self, client: TestClient) -> None:
        resp = client.get("/api/evolve/results")
        assert resp.status_code == 200
        assert resp.json() == {"rounds": []}

    def test_get_results_returns_jsonl_lines(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        lines = [
            json.dumps({"round_index": 1, "winner": "v0-abc", "promoted": True}),
            json.dumps({"round_index": 2, "winner": None, "promoted": False}),
        ]
        (tmp_path / "data" / "evolve_results.jsonl").write_text(
            "\n".join(lines) + "\n"
        )
        resp = client.get("/api/evolve/results")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["rounds"]) == 2
        assert data["rounds"][0]["round_index"] == 1
        assert data["rounds"][1]["promoted"] is False

    def test_get_results_truncates_to_last_50(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        lines = [json.dumps({"round_index": i}) for i in range(75)]
        (tmp_path / "data" / "evolve_results.jsonl").write_text(
            "\n".join(lines) + "\n"
        )
        resp = client.get("/api/evolve/results")
        data = resp.json()
        assert len(data["rounds"]) == 50
        # Should be the tail 50: round_index 25..74.
        assert data["rounds"][0]["round_index"] == 25
        assert data["rounds"][-1]["round_index"] == 74

    def test_get_results_skips_malformed_lines(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        path = tmp_path / "data" / "evolve_results.jsonl"
        path.write_text(
            json.dumps({"round_index": 1}) + "\n"
            "not-json\n"
            + json.dumps({"round_index": 2}) + "\n"
        )
        resp = client.get("/api/evolve/results")
        data = resp.json()
        assert len(data["rounds"]) == 2
        assert [r["round_index"] for r in data["rounds"]] == [1, 2]

    def test_get_current_round_idle_when_no_file(
        self, client: TestClient
    ) -> None:
        resp = client.get("/api/evolve/current-round")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is False
        # Full skeleton so the frontend can destructure without
        # null-coalescing every field.
        for key in (
            "generation",
            "phase",
            "imp_title",
            "imp_rank",
            "imp_index",
            "candidate",
            "new_parent",
            "prior_parent",
            "games_played",
            "games_total",
            "score_cand",
            "score_parent",
            "updated_at",
        ):
            assert key in data
            assert data[key] is None
        # List / bool defaults for non-null fields.
        assert data["stacked_titles"] == []

    def test_get_current_round_returns_file_content(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        live = {
            "active": True,
            "generation": 3,
            "phase": "fitness",
            "imp_title": "Chrono Boost",
            "imp_rank": 1,
            "imp_index": 0,
            "candidate": "cand_abc",
            "stacked_titles": [],
            "new_parent": None,
            "prior_parent": None,
            "games_played": 3,
            "games_total": 5,
            "score_cand": 2,
            "score_parent": 1,
            "updated_at": "2026-04-21T19:15:00+00:00",
        }
        (tmp_path / "data" / "evolve_current_round.json").write_text(
            json.dumps(live)
        )
        resp = client.get("/api/evolve/current-round")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert data["generation"] == 3
        assert data["phase"] == "fitness"
        assert data["score_cand"] == 2
        assert data["score_parent"] == 1

    def test_get_current_round_merges_partial_payload_with_skeleton(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Older writers might omit fields added later — the endpoint backfills
        from the idle skeleton so the response shape is always stable."""
        partial = {"active": True, "generation": 7, "phase": "fitness"}
        (tmp_path / "data" / "evolve_current_round.json").write_text(
            json.dumps(partial)
        )
        resp = client.get("/api/evolve/current-round")
        data = resp.json()
        assert data["active"] is True
        assert data["generation"] == 7
        assert data["phase"] == "fitness"
        assert data["games_played"] is None
        # Skeleton backfill gives non-null defaults for list fields.
        assert data["stacked_titles"] == []

    # --- Step 4: parallel-evolve running-rounds endpoint ---

    @staticmethod
    def _write_run_state(
        tmp_path: Path,
        *,
        run_id: str | None,
        concurrency: int | None,
        parent_current: str = "v3",
    ) -> None:
        state = {
            "status": "running",
            "parent_start": parent_current,
            "parent_current": parent_current,
            "started_at": "2026-04-29T10:00:00+00:00",
            "wall_budget_hours": 8.0,
            "generation_index": 1,
            "generations_completed": 0,
            "generations_promoted": 0,
            "evictions": 0,
            "resurrections_remaining": 0,
            "pool_remaining_count": 4,
            "last_result": None,
            "run_id": run_id,
            "concurrency": concurrency,
        }
        (tmp_path / "data" / "evolve_run_state.json").write_text(
            json.dumps(state)
        )

    @staticmethod
    def _write_round_file(
        tmp_path: Path,
        *,
        worker_id: int,
        run_id: str,
        active: bool = True,
        phase: str = "fitness",
        imp_title: str = "tweak macro",
        candidate: str = "cand_a1b2c3d4",
        games_played: int = 2,
        games_total: int = 5,
        score_cand: int = 1,
        score_parent: int = 1,
    ) -> None:
        entry = {
            "active": active,
            "generation": 1,
            "phase": phase,
            "imp_title": imp_title,
            "imp_rank": 1,
            "imp_index": worker_id,
            "candidate": candidate,
            "stacked_titles": [],
            "new_parent": None,
            "prior_parent": None,
            "games_played": games_played,
            "games_total": games_total,
            "score_cand": score_cand,
            "score_parent": score_parent,
            "updated_at": "2026-04-29T14:32:11+00:00",
            "worker_id": worker_id,
            "run_id": run_id,
        }
        (tmp_path / "data" / f"evolve_round_{worker_id}.json").write_text(
            json.dumps(entry)
        )

    def test_running_rounds_idle_when_run_state_missing(
        self, client: TestClient
    ) -> None:
        """No run-state file → all-idle response (no parallel run in progress)."""
        resp = client.get("/api/evolve/running-rounds")
        assert resp.status_code == 200
        assert resp.json() == {
            "active": False,
            "concurrency": None,
            "run_id": None,
            "rounds": [],
        }

    def test_running_rounds_idle_when_concurrency_none(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Run-state file present but ``concurrency=None`` (single-flight) →
        all-idle response so the dashboard knows no parallel run is
        in progress."""
        self._write_run_state(tmp_path, run_id="abc12345", concurrency=None)
        resp = client.get("/api/evolve/running-rounds")
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] is False
        assert body["concurrency"] is None
        assert body["run_id"] is None
        assert body["rounds"] == []

    def test_running_rounds_pads_idle_slots_to_concurrency(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """concurrency=4 with only 1 round file → 4 entries, 3 idle skeletons."""
        self._write_run_state(tmp_path, run_id="run00001", concurrency=4)
        self._write_round_file(tmp_path, worker_id=0, run_id="run00001")
        resp = client.get("/api/evolve/running-rounds")
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] is True
        assert body["concurrency"] == 4
        assert body["run_id"] == "run00001"
        assert len(body["rounds"]) == 4

        # Worker 0 is active.
        assert body["rounds"][0]["worker_id"] == 0
        assert body["rounds"][0]["active"] is True
        assert body["rounds"][0]["phase"] == "fitness"
        assert body["rounds"][0]["candidate"] == "cand_a1b2c3d4"
        # parent is projected from run_state.parent_current.
        assert body["rounds"][0]["parent"] == "v3"

        # Workers 1-3 are idle skeletons with worker_id stamped.
        for wid in (1, 2, 3):
            slot = body["rounds"][wid]
            assert slot["worker_id"] == wid
            assert slot["active"] is False
            assert slot["phase"] is None
            assert slot["candidate"] is None
            assert slot["parent"] is None

        # Full key-set assertion: idle skeleton must carry exactly the
        # 11 documented per-worker fields. Pinning the full set
        # prevents silent drift between the active-slot shape and the
        # idle skeleton (e.g. adding a field to one but not the other).
        idle_slot = body["rounds"][1]
        assert set(idle_slot.keys()) == {
            "worker_id",
            "active",
            "phase",
            "imp_title",
            "candidate",
            "parent",
            "games_played",
            "games_total",
            "score_cand",
            "score_parent",
            "updated_at",
        }

    def test_running_rounds_filters_stale_run_id(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """4 round files (2 with current run_id, 2 with ``stale``) → only the
        2 current ones surface; the other 2 worker slots become idle."""
        self._write_run_state(tmp_path, run_id="run00002", concurrency=4)
        self._write_round_file(tmp_path, worker_id=0, run_id="run00002")
        self._write_round_file(tmp_path, worker_id=1, run_id="stale")
        self._write_round_file(tmp_path, worker_id=2, run_id="run00002")
        self._write_round_file(tmp_path, worker_id=3, run_id="stale")
        resp = client.get("/api/evolve/running-rounds")
        body = resp.json()
        assert body["active"] is True
        assert len(body["rounds"]) == 4
        assert body["rounds"][0]["active"] is True
        assert body["rounds"][1]["active"] is False
        assert body["rounds"][2]["active"] is True
        assert body["rounds"][3]["active"] is False

    def test_running_rounds_full_4_workers(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """All 4 round files present and current → 4 active rounds."""
        self._write_run_state(tmp_path, run_id="run00003", concurrency=4)
        for wid in range(4):
            self._write_round_file(
                tmp_path,
                worker_id=wid,
                run_id="run00003",
                imp_title=f"imp #{wid}",
                candidate=f"cand_{wid:08x}",
                games_played=wid,
            )
        resp = client.get("/api/evolve/running-rounds")
        body = resp.json()
        assert body["active"] is True
        assert body["concurrency"] == 4
        assert body["run_id"] == "run00003"
        assert len(body["rounds"]) == 4
        for wid in range(4):
            slot = body["rounds"][wid]
            assert slot["worker_id"] == wid
            assert slot["active"] is True
            assert slot["imp_title"] == f"imp #{wid}"
            assert slot["candidate"] == f"cand_{wid:08x}"
            assert slot["games_played"] == wid

    def test_running_rounds_response_shape_is_lean(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Per-worker entries omit dispatcher-level fields (imp_rank,
        imp_index, stacked_titles, new_parent, prior_parent) — those
        are leaner than the legacy ``current-round`` shape."""
        self._write_run_state(tmp_path, run_id="run00004", concurrency=1)
        self._write_round_file(tmp_path, worker_id=0, run_id="run00004")
        resp = client.get("/api/evolve/running-rounds")
        slot = resp.json()["rounds"][0]
        for absent in ("imp_rank", "imp_index", "stacked_titles",
                       "new_parent", "prior_parent"):
            assert absent not in slot, (
                f"running-rounds slot should not carry {absent!r}: {slot}"
            )
        # The shape we DO promise.
        for present in ("worker_id", "active", "phase", "imp_title",
                        "candidate", "parent", "games_played",
                        "games_total", "score_cand", "score_parent",
                        "updated_at"):
            assert present in slot, (
                f"running-rounds slot missing {present!r}: {slot}"
            )

    # --- Step 4: backwards-compat shim on /api/evolve/current-round ---

    def test_current_round_existing_takes_priority_at_concurrency_1(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """At concurrency=1 the legacy ``evolve_current_round.json`` file is
        the sole source of truth — single-flight callers see no change."""
        live = {
            "active": True,
            "generation": 4,
            "phase": "fitness",
            "imp_title": "Single-flight imp",
            "imp_rank": 1,
            "imp_index": 0,
            "candidate": "cand_legacy",
            "stacked_titles": [],
            "new_parent": None,
            "prior_parent": None,
            "games_played": 1,
            "games_total": 5,
            "score_cand": 0,
            "score_parent": 1,
            "updated_at": "2026-04-29T14:00:00+00:00",
        }
        (tmp_path / "data" / "evolve_current_round.json").write_text(
            json.dumps(live)
        )
        # Even when run_state has concurrency=1, the legacy file wins.
        self._write_run_state(tmp_path, run_id="run00005", concurrency=1)
        resp = client.get("/api/evolve/current-round")
        data = resp.json()
        assert data["active"] is True
        assert data["imp_title"] == "Single-flight imp"
        assert data["candidate"] == "cand_legacy"

    def test_current_round_backwards_compat_falls_back_to_running_rounds(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """At concurrency>1 with no ``evolve_current_round.json``, returns
        the first running round (shaped to the legacy CurrentRound iface)."""
        self._write_run_state(tmp_path, run_id="run00006", concurrency=4)
        # Worker 0 is idle; worker 1 is the first active.
        self._write_round_file(
            tmp_path,
            worker_id=0,
            run_id="run00006",
            active=False,
        )
        self._write_round_file(
            tmp_path,
            worker_id=1,
            run_id="run00006",
            imp_title="Parallel imp 1",
            candidate="cand_parallel1",
        )
        resp = client.get("/api/evolve/current-round")
        data = resp.json()
        assert data["active"] is True
        assert data["imp_title"] == "Parallel imp 1"
        assert data["candidate"] == "cand_parallel1"
        # Full 14-field legacy-shape projection: pin every key in the
        # ``_EVOLVE_CURRENT_ROUND_IDLE`` skeleton (besides ``active``,
        # already asserted above) so a future refactor can't shrink the
        # shape and break frontend destructuring.
        for key in (
            "generation",
            "phase",
            "imp_title",
            "imp_rank",
            "imp_index",
            "candidate",
            "stacked_titles",
            "new_parent",
            "prior_parent",
            "games_played",
            "games_total",
            "score_cand",
            "score_parent",
            "updated_at",
        ):
            assert key in data, f"legacy field {key!r} missing from response"

    def test_current_round_falls_back_to_idle_when_no_active_rounds(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """At concurrency>1 with no current-round file AND no active worker
        rounds, the endpoint returns the idle skeleton (not a stale entry)."""
        self._write_run_state(tmp_path, run_id="run00007", concurrency=4)
        # All worker rounds inactive.
        for wid in range(4):
            self._write_round_file(
                tmp_path,
                worker_id=wid,
                run_id="run00007",
                active=False,
            )
        resp = client.get("/api/evolve/current-round")
        data = resp.json()
        assert data["active"] is False

    def test_current_round_skips_stale_run_id_in_fallback(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Stale-file guard also applies to the backwards-compat fallback."""
        self._write_run_state(tmp_path, run_id="run00008", concurrency=2)
        # Only worker 0 has a current entry; worker 1 is from a prior run.
        self._write_round_file(
            tmp_path,
            worker_id=0,
            run_id="stale",
            imp_title="stale imp",
        )
        self._write_round_file(
            tmp_path,
            worker_id=1,
            run_id="run00008",
            imp_title="fresh imp",
        )
        resp = client.get("/api/evolve/current-round")
        data = resp.json()
        assert data["active"] is True
        assert data["imp_title"] == "fresh imp"


# ---------------------------------------------------------------------------
# Step 4: re-run the load-bearing parallel-evolve endpoint invariants against
# ``bots.v3.api`` (the version named in the plan) and ``bots.v4.api`` (the
# production runtime per ``bots/current``). The base ``TestEvolveEndpoints``
# class loads ``bots.v0.api``; the two sibling classes below pin the same
# three invariants on v3 and v4 so a future drift between versions is caught.
# v1 and v2 are dormant historical snapshots — no code path imports them, so
# they're left out of this matrix.
# ---------------------------------------------------------------------------


def _make_versioned_client(
    app_module: str, tmp_path: Path
) -> TestClient:
    """Configure + return a TestClient for one of the per-version
    ``bots.v*.api`` modules. Each module exposes its own ``app`` and
    ``configure`` symbols (FastAPI ``app`` is a module-level global)."""
    import importlib

    api_mod = importlib.import_module(app_module)
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    replay_dir = tmp_path / "replays"
    data_dir.mkdir()
    log_dir.mkdir()
    replay_dir.mkdir()
    api_mod.configure(data_dir, log_dir, replay_dir)
    return TestClient(api_mod.app)


class TestEvolveEndpointsV3:
    """Three load-bearing parallel-evolve invariants against ``bots.v3.api``.

    Mirrors three tests from ``TestEvolveEndpoints`` (which loads
    ``bots.v0.api``):
    * ``test_running_rounds_full_4_workers``
    * ``test_running_rounds_filters_stale_run_id``
    * ``test_current_round_existing_takes_priority_at_concurrency_1``
    """

    @pytest.fixture()
    def client(self, tmp_path: Path) -> TestClient:
        return _make_versioned_client("bots.v3.api", tmp_path)

    def test_running_rounds_full_4_workers(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        TestEvolveEndpoints._write_run_state(
            tmp_path, run_id="run00003", concurrency=4
        )
        for wid in range(4):
            TestEvolveEndpoints._write_round_file(
                tmp_path,
                worker_id=wid,
                run_id="run00003",
                imp_title=f"imp #{wid}",
                candidate=f"cand_{wid:08x}",
                games_played=wid,
            )
        resp = client.get("/api/evolve/running-rounds")
        body = resp.json()
        assert body["active"] is True
        assert body["concurrency"] == 4
        assert body["run_id"] == "run00003"
        assert len(body["rounds"]) == 4
        for wid in range(4):
            slot = body["rounds"][wid]
            assert slot["worker_id"] == wid
            assert slot["active"] is True
            assert slot["imp_title"] == f"imp #{wid}"
            assert slot["candidate"] == f"cand_{wid:08x}"
            assert slot["games_played"] == wid

    def test_running_rounds_filters_stale_run_id(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        TestEvolveEndpoints._write_run_state(
            tmp_path, run_id="run00002", concurrency=4
        )
        TestEvolveEndpoints._write_round_file(
            tmp_path, worker_id=0, run_id="run00002"
        )
        TestEvolveEndpoints._write_round_file(
            tmp_path, worker_id=1, run_id="stale"
        )
        TestEvolveEndpoints._write_round_file(
            tmp_path, worker_id=2, run_id="run00002"
        )
        TestEvolveEndpoints._write_round_file(
            tmp_path, worker_id=3, run_id="stale"
        )
        resp = client.get("/api/evolve/running-rounds")
        body = resp.json()
        assert body["active"] is True
        assert len(body["rounds"]) == 4
        assert body["rounds"][0]["active"] is True
        assert body["rounds"][1]["active"] is False
        assert body["rounds"][2]["active"] is True
        assert body["rounds"][3]["active"] is False

    def test_current_round_existing_takes_priority_at_concurrency_1(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        live = {
            "active": True,
            "generation": 4,
            "phase": "fitness",
            "imp_title": "Single-flight imp",
            "imp_rank": 1,
            "imp_index": 0,
            "candidate": "cand_legacy",
            "stacked_titles": [],
            "new_parent": None,
            "prior_parent": None,
            "games_played": 1,
            "games_total": 5,
            "score_cand": 0,
            "score_parent": 1,
            "updated_at": "2026-04-29T14:00:00+00:00",
        }
        (tmp_path / "data" / "evolve_current_round.json").write_text(
            json.dumps(live)
        )
        TestEvolveEndpoints._write_run_state(
            tmp_path, run_id="run00005", concurrency=1
        )
        resp = client.get("/api/evolve/current-round")
        data = resp.json()
        assert data["active"] is True
        assert data["imp_title"] == "Single-flight imp"
        assert data["candidate"] == "cand_legacy"


class TestEvolveEndpointsV4:
    """Same three invariants against ``bots.v4.api`` — production runtime
    via ``bots/current`` -> v4."""

    @pytest.fixture()
    def client(self, tmp_path: Path) -> TestClient:
        return _make_versioned_client("bots.v4.api", tmp_path)

    def test_running_rounds_full_4_workers(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        TestEvolveEndpoints._write_run_state(
            tmp_path, run_id="run00003", concurrency=4
        )
        for wid in range(4):
            TestEvolveEndpoints._write_round_file(
                tmp_path,
                worker_id=wid,
                run_id="run00003",
                imp_title=f"imp #{wid}",
                candidate=f"cand_{wid:08x}",
                games_played=wid,
            )
        resp = client.get("/api/evolve/running-rounds")
        body = resp.json()
        assert body["active"] is True
        assert body["concurrency"] == 4
        assert body["run_id"] == "run00003"
        assert len(body["rounds"]) == 4
        for wid in range(4):
            slot = body["rounds"][wid]
            assert slot["worker_id"] == wid
            assert slot["active"] is True
            assert slot["imp_title"] == f"imp #{wid}"
            assert slot["candidate"] == f"cand_{wid:08x}"
            assert slot["games_played"] == wid

    def test_running_rounds_filters_stale_run_id(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        TestEvolveEndpoints._write_run_state(
            tmp_path, run_id="run00002", concurrency=4
        )
        TestEvolveEndpoints._write_round_file(
            tmp_path, worker_id=0, run_id="run00002"
        )
        TestEvolveEndpoints._write_round_file(
            tmp_path, worker_id=1, run_id="stale"
        )
        TestEvolveEndpoints._write_round_file(
            tmp_path, worker_id=2, run_id="run00002"
        )
        TestEvolveEndpoints._write_round_file(
            tmp_path, worker_id=3, run_id="stale"
        )
        resp = client.get("/api/evolve/running-rounds")
        body = resp.json()
        assert body["active"] is True
        assert len(body["rounds"]) == 4
        assert body["rounds"][0]["active"] is True
        assert body["rounds"][1]["active"] is False
        assert body["rounds"][2]["active"] is True
        assert body["rounds"][3]["active"] is False

    def test_current_round_existing_takes_priority_at_concurrency_1(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        live = {
            "active": True,
            "generation": 4,
            "phase": "fitness",
            "imp_title": "Single-flight imp",
            "imp_rank": 1,
            "imp_index": 0,
            "candidate": "cand_legacy",
            "stacked_titles": [],
            "new_parent": None,
            "prior_parent": None,
            "games_played": 1,
            "games_total": 5,
            "score_cand": 0,
            "score_parent": 1,
            "updated_at": "2026-04-29T14:00:00+00:00",
        }
        (tmp_path / "data" / "evolve_current_round.json").write_text(
            json.dumps(live)
        )
        TestEvolveEndpoints._write_run_state(
            tmp_path, run_id="run00005", concurrency=1
        )
        resp = client.get("/api/evolve/current-round")
        data = resp.json()
        assert data["active"] is True
        assert data["imp_title"] == "Single-flight imp"
        assert data["candidate"] == "cand_legacy"


class TestImprovementsUnifiedEndpoint:
    """`/api/improvements/unified` merges advised + evolve sources into one
    timeline keyed off the dashboard refactor plan §5. Both source files
    live at ``_evolve_dir`` (cross-version). The test fixture leaves
    ``evolve_dir`` defaulted to ``data_dir`` so we stage both files in the
    same tmp_path / "data" directory."""

    @staticmethod
    def _advised_file(tmp_path: Path, entries: list[dict[str, object]]) -> None:
        path = tmp_path / "data" / "improvement_log.json"
        path.write_text(
            json.dumps({"improvements": entries}), encoding="utf-8"
        )

    @staticmethod
    def _evolve_file(tmp_path: Path, rows: list[dict[str, object]]) -> None:
        path = tmp_path / "data" / "evolve_results.jsonl"
        path.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )

    @staticmethod
    def _advised_entry(
        run_id: str = "20260412-2007",
        iteration: int = 1,
        timestamp: str = "2026-04-12T20:50:00Z",
        title: str = "Stronger mineral floating penalties",
        result: str = "pass",
        metrics: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "id": f"advised-{run_id}-iter{iteration}",
            "timestamp": timestamp,
            "run_id": run_id,
            "iteration": iteration,
            "title": title,
            "type": "training",
            "description": "Tweaked thresholds.",
            "principles": ["§4.2 Resource Spending"],
            "result": result,
            "metrics": (
                metrics
                if metrics is not None
                else {"validation_wins": 7, "validation_total": 10}
            ),
            "files_changed": ["data/reward_rules.json"],
        }

    @staticmethod
    def _evolve_row(
        phase: str,
        generation: int,
        title: str,
        outcome: str,
        timestamp: str | None,
        candidate: object = "cand_2e57ef46",
        wins_cand: int = 3,
        wins_parent: int = 2,
        parent: str = "v3",
    ) -> dict[str, object]:
        row: dict[str, object] = {
            "phase": phase,
            "generation": generation,
            "parent": parent,
            "imp": {
                "rank": 1,
                "title": title,
                "type": "dev",
                "description": f"{title} body.",
                "principle_ids": ["4.1", "22"],
                "files_touched": ["bots/v3/macro_manager.py"],
            },
            "candidate": candidate,
            "record": [],
            "wins_cand": wins_cand,
            "wins_parent": wins_parent,
            "games": wins_cand + wins_parent,
            "outcome": outcome,
            "reason": "",
        }
        if timestamp is not None:
            row["timestamp"] = timestamp
        return row

    def test_only_advised_when_evolve_file_missing(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._advised_file(tmp_path, [self._advised_entry()])
        resp = client.get("/api/improvements/unified")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["improvements"]) == 1
        entry = data["improvements"][0]
        assert entry["source"] == "advised"
        assert entry["id"] == "advised-20260412-2007-iter1"
        assert entry["outcome"] == "promoted"
        assert entry["metric"] == "7/10 wins (validation)"
        assert entry["principles"] == ["§4.2 Resource Spending"]
        assert entry["files_changed"] == ["data/reward_rules.json"]

    def test_advised_outcome_mapping(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._advised_file(
            tmp_path,
            [
                self._advised_entry(iteration=1, result="pass"),
                self._advised_entry(
                    iteration=2,
                    timestamp="2026-04-12T21:00:00Z",
                    result="stopped",
                ),
                self._advised_entry(
                    iteration=3,
                    timestamp="2026-04-12T21:10:00Z",
                    result="fail",
                ),
            ],
        )
        resp = client.get("/api/improvements/unified")
        outcomes = {e["id"]: e["outcome"] for e in resp.json()["improvements"]}
        assert outcomes["advised-20260412-2007-iter1"] == "promoted"
        assert outcomes["advised-20260412-2007-iter2"] == "discarded"
        assert outcomes["advised-20260412-2007-iter3"] == "discarded"

    def test_advised_metric_falls_back(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._advised_file(
            tmp_path,
            [
                self._advised_entry(
                    iteration=1,
                    metrics={"observation_wins": 4, "observation_total": 5},
                ),
                self._advised_entry(
                    iteration=2,
                    timestamp="2026-04-12T21:00:00Z",
                    metrics={},
                ),
            ],
        )
        resp = client.get("/api/improvements/unified")
        entries = {e["id"]: e for e in resp.json()["improvements"]}
        assert (
            entries["advised-20260412-2007-iter1"]["metric"]
            == "4/5 wins (observation)"
        )
        assert entries["advised-20260412-2007-iter2"]["metric"] is None

    def test_only_evolve_when_advised_file_missing(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._evolve_file(
            tmp_path,
            [
                self._evolve_row(
                    "fitness",
                    generation=2,
                    title="Comeback probe-rebuild push",
                    outcome="fitness-pass",
                    timestamp="2026-04-25T10:00:00Z",
                ),
            ],
        )
        resp = client.get("/api/improvements/unified")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["improvements"]) == 1
        entry = data["improvements"][0]
        assert entry["source"] == "evolve"
        assert entry["id"] == "evolve-gen2-cand_2e57ef46"
        assert entry["outcome"] == "fitness-pass"
        assert entry["metric"] == "3-2 vs v3"
        assert entry["type"] == "dev"
        assert entry["principles"] == ["4.1", "22"]
        assert entry["files_changed"] == ["bots/v3/macro_manager.py"]

    def test_both_sources_merged_and_sorted_desc(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._advised_file(
            tmp_path,
            [
                self._advised_entry(
                    iteration=1, timestamp="2026-04-12T20:50:00Z"
                ),
                self._advised_entry(
                    iteration=2, timestamp="2026-04-26T11:00:00Z"
                ),
            ],
        )
        self._evolve_file(
            tmp_path,
            [
                self._evolve_row(
                    "fitness",
                    generation=2,
                    title="Imp A",
                    outcome="fitness-pass",
                    timestamp="2026-04-25T10:00:00Z",
                ),
                self._evolve_row(
                    "fitness",
                    generation=3,
                    title="Imp B",
                    outcome="fitness-pass",
                    timestamp="2026-04-27T09:00:00Z",
                ),
            ],
        )
        resp = client.get("/api/improvements/unified")
        timestamps = [e["timestamp"] for e in resp.json()["improvements"]]
        assert timestamps == sorted(timestamps, reverse=True)
        # Newest entry is the gen-3 evolve row from 04-27.
        first = resp.json()["improvements"][0]
        assert first["source"] == "evolve"
        assert first["title"] == "Imp B"

    def test_source_filter_advised(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._advised_file(tmp_path, [self._advised_entry()])
        self._evolve_file(
            tmp_path,
            [
                self._evolve_row(
                    "fitness",
                    generation=2,
                    title="Imp A",
                    outcome="fitness-pass",
                    timestamp="2026-04-25T10:00:00Z",
                ),
            ],
        )
        resp = client.get("/api/improvements/unified?source=advised")
        sources = {e["source"] for e in resp.json()["improvements"]}
        assert sources == {"advised"}

    def test_source_filter_evolve(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._advised_file(tmp_path, [self._advised_entry()])
        self._evolve_file(
            tmp_path,
            [
                self._evolve_row(
                    "fitness",
                    generation=2,
                    title="Imp A",
                    outcome="fitness-pass",
                    timestamp="2026-04-25T10:00:00Z",
                ),
            ],
        )
        resp = client.get("/api/improvements/unified?source=evolve")
        sources = {e["source"] for e in resp.json()["improvements"]}
        assert sources == {"evolve"}

    def test_limit_caps_response(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._advised_file(
            tmp_path,
            [
                self._advised_entry(
                    iteration=i,
                    timestamp=f"2026-04-12T20:{50 + i:02d}:00Z",
                )
                for i in range(1, 6)
            ],
        )
        resp = client.get("/api/improvements/unified?limit=2")
        data = resp.json()
        assert len(data["improvements"]) == 2
        # Default sort desc: the two newest iterations come back.
        ids = [e["id"] for e in data["improvements"]]
        assert ids == [
            "advised-20260412-2007-iter5",
            "advised-20260412-2007-iter4",
        ]

    def test_evolve_rollup_collapses_phases(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Multiple phase rows for the same (title, generation) collapse to
        one entry; the canonical row is the one with the highest phase
        ordinal (regression > stack_apply > fitness)."""
        self._evolve_file(
            tmp_path,
            [
                self._evolve_row(
                    "fitness",
                    generation=2,
                    title="Imp A",
                    outcome="fitness-pass",
                    timestamp="2026-04-25T10:00:00Z",
                ),
                self._evolve_row(
                    "stack_apply",
                    generation=2,
                    title="Imp A",
                    outcome="stack-apply-pass",
                    timestamp="2026-04-25T10:30:00Z",
                ),
                self._evolve_row(
                    "regression",
                    generation=2,
                    title="Imp A",
                    outcome="regression-rollback",
                    timestamp="2026-04-25T11:00:00Z",
                ),
            ],
        )
        resp = client.get("/api/improvements/unified")
        data = resp.json()
        # Three input rows collapse to one unified entry.
        assert len(data["improvements"]) == 1
        entry = data["improvements"][0]
        assert entry["outcome"] == "regression-rollback"
        # The canonical row carries the regression-phase timestamp.
        assert entry["timestamp"] == "2026-04-25T11:00:00Z"

    def test_evolve_rollup_collapses_multiple_stack_apply_attempts(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """When the same phase repeats (multiple stack_apply attempts),
        the latest by timestamp wins."""
        self._evolve_file(
            tmp_path,
            [
                self._evolve_row(
                    "fitness",
                    generation=2,
                    title="Imp A",
                    outcome="fitness-pass",
                    timestamp="2026-04-25T10:00:00Z",
                ),
                self._evolve_row(
                    "stack_apply",
                    generation=2,
                    title="Imp A",
                    outcome="stack-apply-commit-fail",
                    timestamp="2026-04-25T10:20:00Z",
                ),
                self._evolve_row(
                    "stack_apply",
                    generation=2,
                    title="Imp A",
                    outcome="stack-apply-pass",
                    timestamp="2026-04-25T10:40:00Z",
                ),
            ],
        )
        resp = client.get("/api/improvements/unified")
        data = resp.json()
        assert len(data["improvements"]) == 1
        # Highest phase ordinal present is stack_apply; the latest
        # attempt is the canonical row.
        assert data["improvements"][0]["outcome"] == "stack-apply-pass"

    def test_both_files_missing_returns_empty(
        self, client: TestClient
    ) -> None:
        resp = client.get("/api/improvements/unified")
        assert resp.status_code == 200
        assert resp.json() == {"improvements": []}
