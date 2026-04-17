"""Tests for checkpoint save/load/prune logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from bots.v0.learning.checkpoints import (
    get_best_name,
    list_checkpoints,
    prune_checkpoints,
    save_checkpoint,
)


def _mock_model() -> MagicMock:
    """Create a mock SB3 model with a save() method (mimics SB3 .zip append)."""
    model = MagicMock()
    # SB3's save() auto-appends .zip to the given path
    def side_effect(path: str) -> None:
        p = path if path.endswith(".zip") else path + ".zip"
        Path(p).touch()

    model.save.side_effect = side_effect
    return model


class TestSaveCheckpoint:
    def test_save_creates_file(self, tmp_path: Path) -> None:
        model = _mock_model()
        path = save_checkpoint(model, tmp_path, "v1")
        assert path.exists()
        assert path.name == "v1.zip"

    def test_save_updates_manifest(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1")
        cps = list_checkpoints(tmp_path)
        assert len(cps) == 1
        assert cps[0]["name"] == "v1"

    def test_save_with_metadata(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", metadata={"loss": 0.5})
        cps = list_checkpoints(tmp_path)
        assert cps[0]["metadata"]["loss"] == 0.5

    def test_save_best(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        assert get_best_name(tmp_path) == "v1"

    def test_multiple_saves(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1")
        save_checkpoint(model, tmp_path, "v2", is_best=True)
        save_checkpoint(model, tmp_path, "v3")
        cps = list_checkpoints(tmp_path)
        assert len(cps) == 3
        assert get_best_name(tmp_path) == "v2"


class TestListCheckpoints:
    def test_empty_dir(self, tmp_path: Path) -> None:
        cps = list_checkpoints(tmp_path)
        assert cps == []


class TestPrune:
    def test_prune_keeps_recent(self, tmp_path: Path) -> None:
        model = _mock_model()
        for i in range(10):
            save_checkpoint(model, tmp_path, f"v{i}")
        removed = prune_checkpoints(tmp_path, keep=3)
        assert len(removed) == 7
        remaining = list_checkpoints(tmp_path)
        assert len(remaining) == 3

    def test_prune_keeps_best(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v0", is_best=True)
        for i in range(1, 8):
            save_checkpoint(model, tmp_path, f"v{i}")
        prune_checkpoints(tmp_path, keep=3)
        # Best (v0) should be kept even though it's old
        remaining_names = {c["name"] for c in list_checkpoints(tmp_path)}
        assert "v0" in remaining_names

    def test_prune_noop_when_few(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v0")
        save_checkpoint(model, tmp_path, "v1")
        removed = prune_checkpoints(tmp_path, keep=5)
        assert removed == []

    def test_prune_deletes_files(self, tmp_path: Path) -> None:
        model = _mock_model()
        for i in range(5):
            save_checkpoint(model, tmp_path, f"v{i}")
        prune_checkpoints(tmp_path, keep=2)
        # Only 2 recent + maybe best should have files
        zip_files = list(tmp_path.glob("*.zip"))
        assert len(zip_files) <= 3


class TestGetBestName:
    def test_no_best(self, tmp_path: Path) -> None:
        assert get_best_name(tmp_path) is None

    def test_with_best(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v5", is_best=True)
        assert get_best_name(tmp_path) == "v5"
