"""Tests for the baseline-registry CLI (``scripts/baseline.py``, Phase EL Step 2).

Drives ``main(argv)`` end-to-end against a tmp registry file (via ``--path``)
so no real ``data/`` is touched. The ``list_versions`` seam in
``orchestrator.baselines`` is monkeypatched so ``add`` validation does not
depend on the live registry.

Mirrors the pattern in ``tests/test_registry_cli.py`` / ``tests/test_evolve_cli.py``:
load the script module via importlib, then exercise ``main`` with ``capsys``
and assert exit codes + stderr/stdout.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from orchestrator import baselines as baselines_mod

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_cli_module() -> ModuleType:
    if "baseline_cli" in sys.modules:
        return sys.modules["baseline_cli"]
    spec = importlib.util.spec_from_file_location(
        "baseline_cli", str(_REPO_ROOT / "scripts" / "baseline.py")
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["baseline_cli"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cli() -> ModuleType:
    return _load_cli_module()


@pytest.fixture(autouse=True)
def _known_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the version-validation seam so 'add' does not hit the live repo."""
    monkeypatch.setattr(
        baselines_mod, "list_versions", lambda: ["v0", "v3", "v7"]
    )


def test_add_happy_path_writes_file(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "baselines.json"
    rc = cli.main(["--path", str(path), "add", "v7-strong", "v7", "--note", "ref"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "v7-strong" in out
    assert "v7" in out
    # File was written with the expected entry.
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["v7-strong"]["version"] == "v7"
    assert data["v7-strong"]["note"] == "ref"


def test_add_unknown_version_rc1(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "baselines.json"
    rc = cli.main(["--path", str(path), "add", "ghost", "v99"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not a registered version" in err
    # Nothing was written.
    assert not path.exists()


def test_list_shows_entry(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "baselines.json"
    assert cli.main(["--path", str(path), "add", "v7-strong", "v7"]) == 0
    capsys.readouterr()  # drain the add output
    rc = cli.main(["--path", str(path), "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "v7-strong" in out
    assert "v7" in out


def test_list_empty(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "baselines.json"
    rc = cli.main(["--path", str(path), "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no baselines" in out


def test_remove_existing_rc0(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "baselines.json"
    assert cli.main(["--path", str(path), "add", "v7-strong", "v7"]) == 0
    capsys.readouterr()
    rc = cli.main(["--path", str(path), "remove", "v7-strong"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "removed v7-strong" in out
    # Entry is gone from disk.
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "v7-strong" not in data


def test_remove_nonexistent_rc1(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "baselines.json"
    rc = cli.main(["--path", str(path), "remove", "ghost"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no baseline named" in err
