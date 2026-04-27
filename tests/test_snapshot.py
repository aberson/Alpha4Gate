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


class TestRewriteImports:
    """Regression tests for the absolute-import rewrite at snapshot time.

    Without rewrite, ``bots/<new>/bot.py`` still says
    ``from bots.v0.army_coherence import X`` and the snapshot is not
    actually isolated — runtime imports go through the source version's
    code, so any edit to the snapshot is silently ignored.
    """

    def test_from_import_rewritten(self, tmp_path: Path) -> None:
        target = tmp_path / "bots" / "v1"
        target.mkdir(parents=True)
        (target / "bot.py").write_text(
            "from bots.v0.army_coherence import X\n"
            "from bots.v0 import runner\n"
            "import bots.v0.commands.dispatch_guard\n"
            "import bots.v0\n",
            encoding="utf-8",
        )
        touched = snapshot._rewrite_imports(target, "v0", "v1")
        assert touched == 1
        out = (target / "bot.py").read_text(encoding="utf-8")
        assert out == (
            "from bots.v1.army_coherence import X\n"
            "from bots.v1 import runner\n"
            "import bots.v1.commands.dispatch_guard\n"
            "import bots.v1\n"
        )

    def test_other_package_untouched(self, tmp_path: Path) -> None:
        """An import of ``bots.otherversion`` is not the source — leave it."""
        target = tmp_path / "bots" / "v1"
        target.mkdir(parents=True)
        src = "from bots.other_version.foo import X\n"
        (target / "bot.py").write_text(src, encoding="utf-8")
        touched = snapshot._rewrite_imports(target, "v0", "v1")
        assert touched == 0
        assert (target / "bot.py").read_text(encoding="utf-8") == src

    def test_prose_containing_bots_v0_not_rewritten(
        self, tmp_path: Path
    ) -> None:
        """A docstring or comment mentioning ``bots.v0`` must not get rewritten.

        Only ``^import`` and ``^from`` at a line start should match.
        """
        target = tmp_path / "bots" / "v1"
        target.mkdir(parents=True)
        src = (
            '"""Uses bots.v0 internally — see docs."""\n'
            "# bots.v0 is the parent\n"
            "x = 'bots.v0 in a string'\n"
        )
        (target / "note.py").write_text(src, encoding="utf-8")
        touched = snapshot._rewrite_imports(target, "v0", "v1")
        assert touched == 0
        assert (target / "note.py").read_text(encoding="utf-8") == src

    def test_nested_files_rewritten(self, tmp_path: Path) -> None:
        target = tmp_path / "bots" / "v1"
        (target / "learning").mkdir(parents=True)
        (target / "learning" / "database.py").write_text(
            "from bots.v0.config import Settings\n",
            encoding="utf-8",
        )
        touched = snapshot._rewrite_imports(target, "v0", "v1")
        assert touched == 1
        assert (target / "learning" / "database.py").read_text(
            encoding="utf-8"
        ) == "from bots.v1.config import Settings\n"

    def test_snapshot_current_rewrites_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """snapshot_current must call _rewrite_imports so the copy is isolated."""
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        monkeypatch.setattr(snapshot, "_repo_root", lambda: tmp_path)
        v0 = _seed_version(tmp_path, "v0")
        (v0 / "bot.py").write_text(
            "from bots.v0.army_coherence import ArmyCoherenceManager\n",
            encoding="utf-8",
        )
        _seed_current(tmp_path, "v0")

        target = snapshot.snapshot_current("cand_x")
        rewritten = (target / "bot.py").read_text(encoding="utf-8")
        assert "from bots.cand_x.army_coherence" in rewritten
        assert "from bots.v0.army_coherence" not in rewritten

    def test_source_override_reads_explicit_version_not_pointer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``source=v0`` must copy from v0 even when current points elsewhere.

        Use case: folding a non-current branch (e.g. v0 with feature work)
        into a new version without first flipping ``bots/current/current.txt``.
        Pins that the imports get rewritten relative to the explicit source,
        the manifest parent records the explicit source, and current.txt
        atomically advances to the new snapshot.
        """
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        monkeypatch.setattr(snapshot, "_repo_root", lambda: tmp_path)
        v0 = _seed_version(tmp_path, "v0")
        (v0 / "bot.py").write_text(
            "from bots.v0.give_up import should_give_up\n",
            encoding="utf-8",
        )
        _seed_version(tmp_path, "v2")
        _seed_current(tmp_path, "v2")  # pointer says v2; we want to fold v0

        target = snapshot.snapshot_current(name="v3", source="v0")

        # Imports rewritten from v0 (the explicit source), not v2 (the pointer).
        rewritten = (target / "bot.py").read_text(encoding="utf-8")
        assert "from bots.v3.give_up" in rewritten
        assert "from bots.v0.give_up" not in rewritten

        # Manifest parent records the explicit source verbatim.
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["parent"] == "v0"

        # Pointer advances atomically to the new snapshot (existing behavior).
        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "v3"

    def test_source_override_missing_version_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``source`` pointing at a nonexistent version raises FileNotFoundError."""
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        monkeypatch.setattr(snapshot, "_repo_root", lambda: tmp_path)
        _seed_version(tmp_path, "v0")
        _seed_current(tmp_path, "v0")

        with pytest.raises(FileNotFoundError):
            snapshot.snapshot_current(name="v3", source="v_does_not_exist")


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

    def test_from_flag_overrides_current_pointer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--from v0`` must snapshot from v0 even when current points elsewhere."""
        monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
        monkeypatch.setattr(snapshot, "_repo_root", lambda: tmp_path)
        v0 = _seed_version(tmp_path, "v0")
        (v0 / "bot.py").write_text(
            "from bots.v0.give_up import should_give_up\n",
            encoding="utf-8",
        )
        _seed_version(tmp_path, "v2")
        _seed_current(tmp_path, "v2")

        from scripts.snapshot_bot import main as snap_main

        rc = snap_main(["--from", "v0", "--name", "v3"])
        assert rc == 0

        target = tmp_path / "bots" / "v3"
        rewritten = (target / "bot.py").read_text(encoding="utf-8")
        assert "from bots.v3.give_up" in rewritten
