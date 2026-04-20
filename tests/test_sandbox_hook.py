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


@pytest.fixture(autouse=True)
def _clear_auto_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure neither autonomous-mode env var leaks in from the caller's shell."""
    monkeypatch.delenv("ADVISED_AUTO", raising=False)
    monkeypatch.delenv("EVO_AUTO", raising=False)


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


# ── (j) EVO_AUTO=1 + bots/v99/foo.py → allowed (exit 0) ─────────────
def test_evo_allowed_new_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVO_AUTO", "1")
    with patch("check_sandbox.subprocess.run", return_value=_fake_git_diff(["bots/v99/foo.py"])):
        assert main() == 0


# ── (k) EVO_AUTO=1 + bots/v0/learning/trainer.py → allowed (exit 0) ─
def test_evo_allowed_deep_nested_existing_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVO_AUTO", "1")
    fake = _fake_git_diff(["bots/v0/learning/trainer.py"])
    with patch("check_sandbox.subprocess.run", return_value=fake):
        assert main() == 0


# ── (l) EVO_AUTO=1 + src/orchestrator/foo.py → blocked (exit 1) ─────
def test_evo_blocked_src(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVO_AUTO", "1")
    fake = _fake_git_diff(["src/orchestrator/foo.py"])
    with patch("check_sandbox.subprocess.run", return_value=fake):
        assert main() == 1


# ── (m) EVO_AUTO=1 + tests/foo.py → blocked (exit 1) ────────────────
def test_evo_blocked_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVO_AUTO", "1")
    with patch("check_sandbox.subprocess.run", return_value=_fake_git_diff(["tests/foo.py"])):
        assert main() == 1


# ── (n) EVO_AUTO=1 + scripts/foo.py → blocked (exit 1) ──────────────
def test_evo_blocked_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVO_AUTO", "1")
    with patch("check_sandbox.subprocess.run", return_value=_fake_git_diff(["scripts/foo.py"])):
        assert main() == 1


# ── (o) EVO_AUTO=1 + pyproject.toml → blocked (exit 1) ──────────────
def test_evo_blocked_pyproject(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVO_AUTO", "1")
    with patch("check_sandbox.subprocess.run", return_value=_fake_git_diff(["pyproject.toml"])):
        assert main() == 1


# ── (p) EVO_AUTO=1 + .pre-commit-config.yaml → blocked (exit 1) ─────
def test_evo_blocked_precommit_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVO_AUTO", "1")
    fake = _fake_git_diff([".pre-commit-config.yaml"])
    with patch("check_sandbox.subprocess.run", return_value=fake):
        assert main() == 1


# ── (q) EVO_AUTO=1 + bots/../src/foo.py traversal → blocked (exit 1)─
def test_evo_blocked_path_traversal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVO_AUTO", "1")
    fake = _fake_git_diff(["bots/../src/foo.py"])
    with patch("check_sandbox.subprocess.run", return_value=fake):
        assert main() == 1


# ── (r) EVO_AUTO=1 + mixed bots/v5 + src/orchestrator → blocked (1) ─
def test_evo_blocked_mixed_bots_and_src(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVO_AUTO", "1")
    staged = ["bots/v5/foo.py", "src/orchestrator/bar.py"]
    with patch("check_sandbox.subprocess.run", return_value=_fake_git_diff(staged)):
        assert main() == 1


# ── (s) EVO_AUTO=1 + nothing staged → allowed (exit 0, vacuous) ─────
def test_evo_allowed_nothing_staged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVO_AUTO", "1")
    with patch("check_sandbox.subprocess.run", return_value=_fake_git_diff([])):
        assert main() == 0


# ── (t) ADVISED_AUTO=1 AND EVO_AUTO=1 → blocked (conflict, exit 1) ──
def test_both_modes_set_is_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADVISED_AUTO", "1")
    monkeypatch.setenv("EVO_AUTO", "1")
    # Should fail before even running git diff — no subprocess mock needed,
    # but patch it to guarantee we don't accidentally call the real git.
    with patch("check_sandbox.subprocess.run") as mock_run:
        assert main() == 1
        mock_run.assert_not_called()


# ── (u) EVO_AUTO unset + bots/v5/foo.py → passthrough (exit 0) ──────
def test_passthrough_when_evo_unset_with_bots_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADVISED_AUTO", raising=False)
    monkeypatch.delenv("EVO_AUTO", raising=False)
    # Human commit: passthrough without even inspecting staged files.
    with patch("check_sandbox.subprocess.run") as mock_run:
        assert main() == 0
        mock_run.assert_not_called()
