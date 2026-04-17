"""Tests for orchestrator.snapshot — full-stack snapshot tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator import registry, snapshot
from orchestrator.contracts import Manifest, VersionFingerprint


def _seed_version(root: Path, name: str, *, with_manifest: bool = True) -> Path:
    """Create a minimal versioned bot directory at ``<root>/bots/<name>/``."""
    version_dir = root / "bots" / name
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / "VERSION").write_text(name, encoding="utf-8")

    # Minimal data directory
    data_dir = version_dir / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "training.db").write_text("fake-db", encoding="utf-8")
    checkpoints = data_dir / "checkpoints"
    checkpoints.mkdir(exist_ok=True)
    (checkpoints / "best.zip").write_text("fake-checkpoint", encoding="utf-8")

    if with_manifest:
        manifest = Manifest(
            version=name,
            best="best",
            previous_best=None,
            parent=None,
            git_sha="abc1234",
            timestamp="2026-04-16T00:00:00Z",
            elo=100.0,
            fingerprint=VersionFingerprint(
                feature_dim=24,
                action_space_size=6,
                obs_spec_hash="deadbeef",
            ),
        )
        (version_dir / "manifest.json").write_text(
            manifest.to_json(), encoding="utf-8"
        )

    return version_dir


def _seed_current(root: Path, version: str) -> None:
    """Write ``bots/current/current.txt`` pointing at ``version``."""
    pointer = root / "bots" / "current" / "current.txt"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(version, encoding="utf-8")


class TestSnapshotCurrent:
    def test_snapshot_produces_self_contained_tree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        monkeypatch.setattr(snapshot, "_repo_root", lambda: tmp_path)
        _seed_version(tmp_path, "v0")
        _seed_current(tmp_path, "v0")

        result = snapshot.snapshot_current()
        assert result.is_dir()
        assert (result / "VERSION").read_text(encoding="utf-8") == "v1"
        assert (result / "manifest.json").is_file()
        assert (result / "data" / "training.db").is_file()
        assert (result / "data" / "checkpoints" / "best.zip").is_file()

    def test_manifest_has_correct_parent_and_fingerprint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        monkeypatch.setattr(snapshot, "_repo_root", lambda: tmp_path)
        _seed_version(tmp_path, "v0")
        _seed_current(tmp_path, "v0")

        result = snapshot.snapshot_current()
        manifest = Manifest.from_json(
            (result / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest.version == "v1"
        assert manifest.parent == "v0"
        assert manifest.best == "best"
        assert manifest.elo == 100.0
        assert manifest.fingerprint.feature_dim == 24
        assert manifest.fingerprint.action_space_size == 6
        assert manifest.fingerprint.obs_spec_hash == "deadbeef"

    def test_current_txt_updated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        monkeypatch.setattr(snapshot, "_repo_root", lambda: tmp_path)
        _seed_version(tmp_path, "v0")
        _seed_current(tmp_path, "v0")

        snapshot.snapshot_current()
        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "v1"

    def test_auto_increment_naming(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        monkeypatch.setattr(snapshot, "_repo_root", lambda: tmp_path)
        _seed_version(tmp_path, "v0")
        _seed_version(tmp_path, "v1")
        _seed_version(tmp_path, "v2")
        _seed_current(tmp_path, "v2")

        result = snapshot.snapshot_current()
        assert result.name == "v3"
        assert (result / "VERSION").read_text(encoding="utf-8") == "v3"

    def test_explicit_name_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        monkeypatch.setattr(snapshot, "_repo_root", lambda: tmp_path)
        _seed_version(tmp_path, "v0")
        _seed_current(tmp_path, "v0")

        result = snapshot.snapshot_current(name="v_custom")
        assert result.name == "v_custom"
        assert (result / "VERSION").read_text(encoding="utf-8") == "v_custom"

    def test_source_unchanged_after_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        monkeypatch.setattr(snapshot, "_repo_root", lambda: tmp_path)
        _seed_version(tmp_path, "v0")
        _seed_current(tmp_path, "v0")

        source_version_before = (
            (tmp_path / "bots" / "v0" / "VERSION")
            .read_text(encoding="utf-8")
        )
        source_manifest_before = json.loads(
            (tmp_path / "bots" / "v0" / "manifest.json")
            .read_text(encoding="utf-8")
        )

        snapshot.snapshot_current()

        source_version_after = (
            (tmp_path / "bots" / "v0" / "VERSION")
            .read_text(encoding="utf-8")
        )
        source_manifest_after = json.loads(
            (tmp_path / "bots" / "v0" / "manifest.json")
            .read_text(encoding="utf-8")
        )
        assert source_version_before == source_version_after
        assert source_manifest_before == source_manifest_after

    def test_error_if_source_dir_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        monkeypatch.setattr(snapshot, "_repo_root", lambda: tmp_path)
        _seed_current(tmp_path, "v_nonexistent")

        with pytest.raises(FileNotFoundError, match="does not exist"):
            snapshot.snapshot_current()

    def test_error_if_target_already_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        monkeypatch.setattr(snapshot, "_repo_root", lambda: tmp_path)
        _seed_version(tmp_path, "v0")
        _seed_version(tmp_path, "v1")
        _seed_current(tmp_path, "v0")

        # Explicit name targeting an existing directory triggers the error
        with pytest.raises(FileExistsError, match="already exists"):
            snapshot.snapshot_current(name="v1")


class TestNextVersionName:
    def test_next_after_v0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        _seed_version(tmp_path, "v0")
        assert snapshot._next_version_name() == "v1"

    def test_next_with_gap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        _seed_version(tmp_path, "v0")
        _seed_version(tmp_path, "v5")
        assert snapshot._next_version_name() == "v6"

    def test_no_versions_returns_v1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When there are no versions at all, falls back to v1 (v-1+1 = v0 would
        be wrong — but max_n starts at -1 so it's v0. Actually for an empty bots/
        directory with no versions, max_n=-1 → v0."""
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        (tmp_path / "bots").mkdir(parents=True)
        assert snapshot._next_version_name() == "v0"


class TestSnapshotBotScript:
    """Tests for ``scripts/snapshot_bot.py`` CLI argparse."""

    def test_help_exits_zero(self) -> None:
        from scripts.snapshot_bot import main as snap_main

        with pytest.raises(SystemExit) as exc_info:
            snap_main(["--help"])
        assert exc_info.value.code == 0

    def test_name_arg_parsed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Verify --name targets a nonexistent version and errors cleanly."""
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        monkeypatch.setattr(snapshot, "_repo_root", lambda: tmp_path)
        # No seeded version → FileNotFoundError
        _seed_current(tmp_path, "v_nonexistent")

        from scripts.snapshot_bot import main as snap_main

        rc = snap_main(["--name", "v_test"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "does not exist" in err
