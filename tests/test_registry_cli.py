"""Tests for the orchestrator registry CLI (``python -m orchestrator.registry``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator import registry
from orchestrator.__main__ import main
from orchestrator.contracts import Manifest, VersionFingerprint


def _seed_version_with_manifest(root: Path, name: str) -> None:
    """Create a minimal versioned bot with a manifest."""
    version_dir = root / "bots" / name
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / "VERSION").write_text(name, encoding="utf-8")
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


class TestRegistryCLI:
    def test_list_prints_versions(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        _seed_version_with_manifest(tmp_path, "v0")
        _seed_version_with_manifest(tmp_path, "v1")

        rc = main(["list"])
        assert rc == 0
        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        assert lines == ["v0", "v1"]

    def test_list_empty(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        (tmp_path / "bots").mkdir()

        rc = main(["list"])
        assert rc == 0
        out = capsys.readouterr().out
        assert out.strip() == ""

    def test_show_outputs_valid_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        _seed_version_with_manifest(tmp_path, "v0")

        rc = main(["show", "v0"])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["version"] == "v0"
        assert data["fingerprint"]["feature_dim"] == 24

    def test_show_nonexistent_version_exits_with_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)

        rc = main(["show", "v99"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "v99" in err

    def test_no_subcommand_exits_with_error(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = main([])
        assert rc == 1

    def test_list_on_real_repo(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Smoke test against the real repository."""
        rc = main(["list"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "v0" in out

    def test_show_v0_on_real_repo(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Smoke test: show v0 manifest from real repo."""
        rc = main(["show", "v0"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["version"] == "v0"
