"""Tests for orchestrator.registry — version + data-path resolver."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator import registry
from orchestrator.contracts import Manifest, VersionFingerprint


def _write_manifest(path: Path, *, version: str = "v0") -> Manifest:
    """Seed a valid ``manifest.json`` at ``path`` and return the expected object."""
    manifest = Manifest(
        version=version,
        best="ckpt_000123",
        previous_best="ckpt_000100",
        parent=None,
        git_sha="abc1234",
        timestamp="2026-04-16T00:00:00Z",
        elo=1234.5,
        fingerprint=VersionFingerprint(
            feature_dim=24,
            action_space_size=6,
            obs_spec_hash="deadbeef",
        ),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.to_json(), encoding="utf-8")
    return manifest


def _seed_pointer(root: Path, version: str = "v0") -> None:
    """Write ``<root>/bots/current/current.txt`` with ``version`` content."""
    pointer = root / "bots" / "current" / "current.txt"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(f"{version}\n", encoding="utf-8")


class TestCurrentVersion:
    def test_current_version_reads_committed_pointer(self) -> None:
        """The committed pointer in the real worktree resolves to ``v0``."""
        assert registry.current_version() == "v0"

    def test_current_version_missing_file_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        with pytest.raises(FileNotFoundError):
            registry.current_version()

    def test_current_version_empty_file_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        pointer = tmp_path / "bots" / "current" / "current.txt"
        pointer.parent.mkdir(parents=True)
        pointer.write_text("   \n", encoding="utf-8")
        with pytest.raises(ValueError):
            registry.current_version()


class TestGetVersionDir:
    def test_get_version_dir_returns_absolute_path(self) -> None:
        p = registry.get_version_dir("v0")
        assert p.is_absolute()
        # Path separator is platform-dependent; compare by parts.
        assert p.parts[-2:] == ("bots", "v0")

    def test_get_version_dir_does_not_require_existence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        p = registry.get_version_dir("v99")
        assert not p.exists()
        assert p.parts[-2:] == ("bots", "v99")


class TestResolveDataPath:
    def test_resolve_data_path_prefers_per_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        per_version = tmp_path / "bots" / "v0" / "data" / "foo.json"
        fallback = tmp_path / "data" / "foo.json"
        per_version.parent.mkdir(parents=True)
        per_version.write_text("{}", encoding="utf-8")
        fallback.parent.mkdir(parents=True)
        fallback.write_text("{}", encoding="utf-8")
        assert registry.resolve_data_path("foo.json", "v0") == per_version

    def test_resolve_data_path_falls_back_to_legacy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        fallback = tmp_path / "data" / "foo.json"
        fallback.parent.mkdir(parents=True)
        fallback.write_text("{}", encoding="utf-8")
        assert registry.resolve_data_path("foo.json", "v0") == fallback

    def test_resolve_data_path_returns_per_version_for_writes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With neither file present, the per-version path is returned so
        callers opening the file in write mode land under the versioned tree.
        """
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        result = registry.resolve_data_path("foo.json", "v0")
        assert result == tmp_path / "bots" / "v0" / "data" / "foo.json"
        assert not result.exists()

    def test_resolve_data_path_uses_current_version_when_omitted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        _seed_pointer(tmp_path, "v0")
        result = registry.resolve_data_path("foo.json")
        assert result == tmp_path / "bots" / "v0" / "data" / "foo.json"


class TestGetDataDir:
    def test_get_data_dir_prefers_per_version_when_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        per_version = tmp_path / "bots" / "v0" / "data"
        per_version.mkdir(parents=True)
        # Legacy also exists — per-version should still win.
        (tmp_path / "data").mkdir()
        assert registry.get_data_dir("v0") == per_version

    def test_get_data_dir_falls_back_to_legacy_when_per_version_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        # Neither directory exists, but the fallback branch is returned so
        # Settings.ensure_dirs() can create it lazily.
        assert registry.get_data_dir("v0") == tmp_path / "data"

    def test_get_data_dir_uses_current_version_when_omitted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        _seed_pointer(tmp_path, "v0")
        per_version = tmp_path / "bots" / "v0" / "data"
        per_version.mkdir(parents=True)
        assert registry.get_data_dir() == per_version


class TestGetManifest:
    def test_get_manifest_happy_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        expected = _write_manifest(tmp_path / "bots" / "v0" / "manifest.json")
        loaded = registry.get_manifest("v0")
        assert isinstance(loaded, Manifest)
        assert loaded == expected

    def test_get_manifest_missing_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        with pytest.raises(FileNotFoundError):
            registry.get_manifest("v0")

    def test_get_manifest_malformed_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        path = tmp_path / "bots" / "v0" / "manifest.json"
        path.parent.mkdir(parents=True)
        # Missing every required field; Manifest.from_json pops "fingerprint"
        # first, so a payload without it raises KeyError.
        path.write_text(json.dumps({"version": "v0"}), encoding="utf-8")
        with pytest.raises((KeyError, TypeError)):
            registry.get_manifest("v0")
