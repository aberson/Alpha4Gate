"""Tests for scripts/check_sandbox.py — sandbox enforcement hook."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Load the script as a module (it's not a package).
_script_path = str(Path(__file__).resolve().parent.parent / "scripts" / "check_sandbox.py")
_spec = importlib.util.spec_from_file_location("check_sandbox", _script_path)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["check_sandbox"] = _mod  # register so unittest.mock.patch can resolve it
_spec.loader.exec_module(_mod)
main = _mod.main


def _fake_git_diff(staged_files: list[str]) -> MagicMock:
    """Return a mock CompletedProcess whose stdout lists *staged_files*."""
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = 0
    cp.stdout = "\n".join(staged_files) + ("\n" if staged_files else "")
    cp.stderr = ""
    return cp


# ── (a) env var unset → passthrough (exit 0) ────────────────────────
def test_passthrough_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADVISED_AUTO", raising=False)
    assert main() == 0


# ── (b) env var set + only bots/current/foo.py → allowed (exit 0) ───
def test_allowed_single_current_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADVISED_AUTO", "1")
    fake = _fake_git_diff(["bots/current/foo.py"])
    with patch("check_sandbox.subprocess.run", return_value=fake):
        assert main() == 0


# ── (c) env var set + src/orchestrator/ladder.py → blocked (exit 1) ──
def test_blocked_orchestrator_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADVISED_AUTO", "1")
    fake = _fake_git_diff(["src/orchestrator/ladder.py"])
    with patch("check_sandbox.subprocess.run", return_value=fake):
        assert main() == 1


# ── (d) env var set + pyproject.toml → blocked (exit 1) ─────────────
def test_blocked_root_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADVISED_AUTO", "1")
    with patch("check_sandbox.subprocess.run", return_value=_fake_git_diff(["pyproject.toml"])):
        assert main() == 1


# ── (e) mixed: bots/current/ + tests/ → blocked (exit 1) ───────────
def test_blocked_mixed_current_and_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADVISED_AUTO", "1")
    staged = ["bots/current/learning/trainer.py", "tests/test_foo.py"]
    with patch("check_sandbox.subprocess.run", return_value=_fake_git_diff(staged)):
        assert main() == 1


# ── (f) path normalization escapes sandbox → blocked (exit 1) ───────
def test_blocked_path_traversal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADVISED_AUTO", "1")
    staged = ["bots/current/../orchestrator/foo.py"]
    with patch("check_sandbox.subprocess.run", return_value=_fake_git_diff(staged)):
        assert main() == 1


# ── (g) nested bots/current/subdir/deep/file.py → allowed (exit 0) ─
def test_allowed_deep_nested(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADVISED_AUTO", "1")
    fake = _fake_git_diff(["bots/current/subdir/deep/file.py"])
    with patch("check_sandbox.subprocess.run", return_value=fake):
        assert main() == 0


# ── (h) bots/v0/foo.py → blocked (only bots/current/) ──────────────
def test_blocked_other_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADVISED_AUTO", "1")
    with patch("check_sandbox.subprocess.run", return_value=_fake_git_diff(["bots/v0/foo.py"])):
        assert main() == 1


# ── (i) env var set + nothing staged → allowed (exit 0, vacuous) ────
def test_allowed_nothing_staged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADVISED_AUTO", "1")
    with patch("check_sandbox.subprocess.run", return_value=_fake_git_diff([])):
        assert main() == 0
