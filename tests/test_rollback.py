"""Tests for the rollback monitor: regression detection and rollback execution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bots.v0.learning.checkpoints import (
    _load_manifest,
    get_best_name,
    promote_checkpoint,
)
from bots.v0.learning.database import TrainingDB
from bots.v0.learning.rollback import RollbackConfig, RollbackDecision, RollbackMonitor


@pytest.fixture()
def db(tmp_path: Path) -> TrainingDB:
    return TrainingDB(tmp_path / "test.db")


@pytest.fixture()
def cp_dir(tmp_path: Path) -> Path:
    d = tmp_path / "checkpoints"
    d.mkdir()
    return d


@pytest.fixture()
def history_path(tmp_path: Path) -> Path:
    return tmp_path / "promotion_history.json"


def _seed_promotion_history(
    history_path: Path,
    checkpoint: str,
    win_rate: float,
) -> None:
    """Write a minimal promotion history entry for a promoted checkpoint."""
    entries: list[dict[str, object]] = []
    if history_path.exists():
        entries = json.loads(history_path.read_text(encoding="utf-8"))
    entries.append({
        "timestamp": "2026-01-01T00:00:00+00:00",
        "new_checkpoint": checkpoint,
        "old_best": "v_old",
        "new_win_rate": win_rate,
        "old_win_rate": 0.5,
        "delta": win_rate - 0.5,
        "eval_games_played": 20,
        "promoted": True,
        "reason": "test promotion",
        "difficulty": 1,
        "action_distribution_shift": None,
    })
    history_path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


def _seed_games(
    db: TrainingDB,
    model: str,
    wins: int,
    losses: int,
) -> None:
    """Store game records for a model."""
    for i in range(wins):
        db.store_game(f"w_{model}_{i}", "Simple64", 1, "win", 60.0, 1.0, model)
    for i in range(losses):
        db.store_game(f"l_{model}_{i}", "Simple64", 1, "loss", 60.0, -1.0, model)


def _setup_manifest_with_previous(
    cp_dir: Path,
    best: str,
    previous: str,
) -> None:
    """Create a manifest with best and previous_best set."""
    manifest = {"checkpoints": [], "best": best, "previous_best": previous}
    (cp_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


class TestRegressionDetection:
    def test_no_regression_when_winning(
        self, db: TrainingDB, cp_dir: Path, history_path: Path
    ) -> None:
        """No rollback when model is performing at or above promotion rate."""
        _seed_promotion_history(history_path, "v5", win_rate=0.60)
        _seed_games(db, "v5", wins=8, losses=4)  # 67% > 60%
        _setup_manifest_with_previous(cp_dir, "v5", "v4")

        monitor = RollbackMonitor(
            db=db,
            config=RollbackConfig(min_games_before_check=10),
            checkpoint_dir=cp_dir,
            history_path=history_path,
        )
        result = monitor.check_for_regression("v5")
        assert result is None

    def test_no_regression_below_min_games(
        self, db: TrainingDB, cp_dir: Path, history_path: Path
    ) -> None:
        """No rollback when not enough games played yet."""
        _seed_promotion_history(history_path, "v5", win_rate=0.80)
        _seed_games(db, "v5", wins=1, losses=5)  # Only 6 games, need 10
        _setup_manifest_with_previous(cp_dir, "v5", "v4")

        monitor = RollbackMonitor(
            db=db,
            config=RollbackConfig(min_games_before_check=10),
            checkpoint_dir=cp_dir,
            history_path=history_path,
        )
        result = monitor.check_for_regression("v5")
        assert result is None

    def test_regression_detected(
        self, db: TrainingDB, cp_dir: Path, history_path: Path
    ) -> None:
        """Rollback recommended when win rate drops significantly."""
        _seed_promotion_history(history_path, "v5", win_rate=0.80)
        # 3 wins, 12 losses = 20% win rate, drop of 60% (> 15% threshold)
        _seed_games(db, "v5", wins=3, losses=12)
        _setup_manifest_with_previous(cp_dir, "v5", "v4")

        monitor = RollbackMonitor(
            db=db,
            config=RollbackConfig(
                min_games_before_check=10,
                regression_threshold=0.15,
            ),
            checkpoint_dir=cp_dir,
            history_path=history_path,
        )
        result = monitor.check_for_regression("v5")
        assert result is not None
        assert result.current_model == "v5"
        assert result.revert_to == "v4"
        assert result.games_played == 15
        assert result.promotion_win_rate == 0.80
        assert abs(result.current_win_rate - 0.20) < 0.01

    def test_no_regression_small_drop(
        self, db: TrainingDB, cp_dir: Path, history_path: Path
    ) -> None:
        """No rollback when drop is within threshold."""
        _seed_promotion_history(history_path, "v5", win_rate=0.70)
        # 6 wins, 4 losses = 60% win rate, drop of 10% (< 15% threshold)
        _seed_games(db, "v5", wins=6, losses=4)
        _setup_manifest_with_previous(cp_dir, "v5", "v4")

        monitor = RollbackMonitor(
            db=db,
            config=RollbackConfig(
                min_games_before_check=10,
                regression_threshold=0.15,
            ),
            checkpoint_dir=cp_dir,
            history_path=history_path,
        )
        result = monitor.check_for_regression("v5")
        assert result is None

    def test_no_previous_best_in_manifest(
        self, db: TrainingDB, cp_dir: Path, history_path: Path
    ) -> None:
        """No rollback if manifest has no previous_best."""
        _seed_promotion_history(history_path, "v5", win_rate=0.80)
        _seed_games(db, "v5", wins=1, losses=14)
        # Manifest without previous_best
        manifest = {"checkpoints": [], "best": "v5"}
        (cp_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

        monitor = RollbackMonitor(
            db=db,
            config=RollbackConfig(min_games_before_check=10),
            checkpoint_dir=cp_dir,
            history_path=history_path,
        )
        result = monitor.check_for_regression("v5")
        assert result is None

    def test_no_promotion_history(
        self, db: TrainingDB, cp_dir: Path, tmp_path: Path
    ) -> None:
        """No rollback if no promotion history exists."""
        _seed_games(db, "v5", wins=1, losses=14)
        _setup_manifest_with_previous(cp_dir, "v5", "v4")

        monitor = RollbackMonitor(
            db=db,
            config=RollbackConfig(min_games_before_check=10),
            checkpoint_dir=cp_dir,
            history_path=tmp_path / "nonexistent.json",
        )
        result = monitor.check_for_regression("v5")
        assert result is None


class TestExecuteRollback:
    def test_rollback_updates_manifest(
        self, db: TrainingDB, cp_dir: Path, history_path: Path
    ) -> None:
        """Rollback should set manifest best to revert_to."""
        _setup_manifest_with_previous(cp_dir, "v5", "v4")

        monitor = RollbackMonitor(
            db=db,
            config=RollbackConfig(),
            checkpoint_dir=cp_dir,
            history_path=history_path,
        )

        decision = RollbackDecision(
            current_model="v5",
            revert_to="v4",
            current_win_rate=0.20,
            promotion_win_rate=0.80,
            games_played=15,
            reason="test rollback",
        )
        monitor.execute_rollback(decision)

        assert get_best_name(cp_dir) == "v4"
        manifest = _load_manifest(cp_dir)
        assert manifest["previous_best"] == "v5"

    def test_rollback_logs_to_history(
        self, db: TrainingDB, cp_dir: Path, history_path: Path
    ) -> None:
        """Rollback should append a special entry to promotion_history.json."""
        _setup_manifest_with_previous(cp_dir, "v5", "v4")

        monitor = RollbackMonitor(
            db=db,
            config=RollbackConfig(),
            checkpoint_dir=cp_dir,
            history_path=history_path,
        )

        decision = RollbackDecision(
            current_model="v5",
            revert_to="v4",
            current_win_rate=0.20,
            promotion_win_rate=0.80,
            games_played=15,
            reason="regression detected",
        )
        monitor.execute_rollback(decision)

        entries = json.loads(history_path.read_text(encoding="utf-8"))
        assert len(entries) == 1
        entry = entries[0]
        assert entry["promoted"] is False
        assert entry["reason"].startswith("rollback:")
        assert entry["new_checkpoint"] == "v4"
        assert entry["old_best"] == "v5"

    def test_rollback_entry_has_reason_code(
        self, db: TrainingDB, cp_dir: Path, history_path: Path
    ) -> None:
        """Rollback entries must stamp ``reason_code='rollback'``.

        Schema consistency with PromotionLogger entries -- the dashboard
        classifies rollback entries without parsing the free-form ``reason``
        string (Phase 4.6 Step 4 iter 2).
        """
        _setup_manifest_with_previous(cp_dir, "v5", "v4")

        monitor = RollbackMonitor(
            db=db,
            config=RollbackConfig(),
            checkpoint_dir=cp_dir,
            history_path=history_path,
        )
        decision = RollbackDecision(
            current_model="v5",
            revert_to="v4",
            current_win_rate=0.20,
            promotion_win_rate=0.80,
            games_played=15,
            reason="regression detected",
        )
        monitor.execute_rollback(decision)

        entries = json.loads(history_path.read_text(encoding="utf-8"))
        assert entries[0]["reason_code"] == "rollback"

    def test_rollback_appends_to_existing_history(
        self, db: TrainingDB, cp_dir: Path, history_path: Path
    ) -> None:
        """Rollback appends to existing history, doesn't overwrite."""
        _seed_promotion_history(history_path, "v5", win_rate=0.80)
        _setup_manifest_with_previous(cp_dir, "v5", "v4")

        monitor = RollbackMonitor(
            db=db,
            config=RollbackConfig(),
            checkpoint_dir=cp_dir,
            history_path=history_path,
        )
        decision = RollbackDecision(
            current_model="v5",
            revert_to="v4",
            current_win_rate=0.20,
            promotion_win_rate=0.80,
            games_played=15,
            reason="test",
        )
        monitor.execute_rollback(decision)

        entries = json.loads(history_path.read_text(encoding="utf-8"))
        assert len(entries) == 2
        assert entries[0]["promoted"] is True
        assert entries[1]["promoted"] is False


class TestPromoteTracksPreviousBest:
    def test_promote_sets_previous_best(self, cp_dir: Path) -> None:
        """promote_checkpoint should set previous_best in manifest."""
        manifest = {"checkpoints": [], "best": "v3"}
        (cp_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

        promote_checkpoint(cp_dir, "v4")

        result = _load_manifest(cp_dir)
        assert result["best"] == "v4"
        assert result["previous_best"] == "v3"

    def test_promote_first_checkpoint_no_previous(self, cp_dir: Path) -> None:
        """First promotion should not set previous_best."""
        manifest = {"checkpoints": [], "best": None}
        (cp_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

        promote_checkpoint(cp_dir, "v1")

        result = _load_manifest(cp_dir)
        assert result["best"] == "v1"
        assert "previous_best" not in result


class TestRollbackApiEndpoint:
    """Dashboard refactor Step 6 retired ``/api/training/rollback``;
    only ``/api/training/daemon`` (status read for the Alerts pipeline)
    survives, and the daemon payload still includes ``last_rollback``."""

    def test_daemon_status_includes_rollback(self, tmp_path: Path) -> None:
        from bots.v0.api import app, configure
        from fastapi.testclient import TestClient

        configure(
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            replay_dir=tmp_path / "replays",
        )
        client = TestClient(app)

        resp = client.get("/api/training/daemon")
        assert resp.status_code == 200
        body = resp.json()
        assert "last_rollback" in body
        assert body["last_rollback"] is None
