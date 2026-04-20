"""Tests for ``orchestrator.evolve_dev_apply``.

The handler is fully gated by three subprocess boundaries:

1. the ``claude`` CLI invocation (the sub-agent itself),
2. ``git status --porcelain`` / ``git checkout -- <path>`` (scope check),
3. ``uv run ruff check`` and ``uv run mypy --strict`` (validation gates).

Every test injects a ``FakeRun`` callable that routes by argv prefix so
we can script the exact success/failure story per test without touching
a real CLI. Filesystem-side coverage (py-mtime snapshot + diff) runs
against a real ``tmp_path`` because it's cheap and the helpers are pure.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from orchestrator.evolve import Improvement
from orchestrator.evolve_dev_apply import (
    DevApplyOutOfScopeError,
    DevApplySubagentError,
    DevApplyTimeoutError,
    DevApplyValidationError,
    _collect_candidate_py_snapshot,
    _diff_py_snapshots,
    spawn_dev_subagent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_improvement(title: str = "demo improvement") -> Improvement:
    """Minimal valid dev-type Improvement for test use."""
    return Improvement(
        rank=1,
        title=title,
        type="dev",
        description="test description",
        principle_ids=["1"],
        expected_impact="test impact",
        concrete_change="Edit bots/<cand>/foo.py to add a stub function.",
    )


class _CompletedProc:
    """Structural stand-in for subprocess.CompletedProcess[str]."""

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRun:
    """Route ``subprocess.run``-style calls by argv prefix.

    Each call's (argv, kwargs) is appended to ``.calls`` so assertions
    can verify argv shape, stdin usage, cwd, etc. Routes:

    - ``claude`` (index 0) → subagent_result (override per test)
    - ``git status`` → git_status_sequence[n] (one per call, in order)
    - ``git checkout`` → git_checkout_result
    - ``uv run ruff`` → ruff_result
    - ``uv run mypy`` → mypy_result
    - anything else → ValueError (explicit unhandled call)
    """

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.subagent_result: _CompletedProc | Exception = _CompletedProc()
        self.git_status_sequence: list[_CompletedProc] = []
        self.git_status_default: _CompletedProc = _CompletedProc(stdout="")
        self.git_checkout_result: _CompletedProc = _CompletedProc()
        self.ruff_result: _CompletedProc = _CompletedProc()
        self.mypy_result: _CompletedProc = _CompletedProc()
        self._git_status_idx = 0

    def __call__(
        self, argv: list[str], **kwargs: Any
    ) -> _CompletedProc:
        self.calls.append((list(argv), dict(kwargs)))
        head = argv[0] if argv else ""

        if head == "claude":
            if isinstance(self.subagent_result, Exception):
                raise self.subagent_result
            return self.subagent_result

        if head == "git":
            if len(argv) >= 2 and argv[1] == "status":
                if self._git_status_idx < len(self.git_status_sequence):
                    res = self.git_status_sequence[self._git_status_idx]
                    self._git_status_idx += 1
                    return res
                return self.git_status_default
            if len(argv) >= 2 and argv[1] == "checkout":
                return self.git_checkout_result

        if argv[:3] == ["uv", "run", "ruff"]:
            return self.ruff_result

        if argv[:3] == ["uv", "run", "mypy"]:
            return self.mypy_result

        raise ValueError(f"FakeRun received unrouted argv: {argv!r}")


# ---------------------------------------------------------------------------
# Pure helpers (no subprocess)
# ---------------------------------------------------------------------------


class TestPySnapshots:
    def test_empty_dir(self, tmp_path: Path) -> None:
        assert _collect_candidate_py_snapshot(tmp_path) == {}

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        assert _collect_candidate_py_snapshot(tmp_path / "nope") == {}

    def test_captures_py_only(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("print('a')", encoding="utf-8")
        (tmp_path / "b.txt").write_text("not python", encoding="utf-8")
        nested = tmp_path / "pkg"
        nested.mkdir()
        (nested / "c.py").write_text("x = 1", encoding="utf-8")

        snap = _collect_candidate_py_snapshot(tmp_path)
        assert set(snap.keys()) == {
            tmp_path / "a.py",
            nested / "c.py",
        }

    def test_diff_detects_new_file(self, tmp_path: Path) -> None:
        before: dict[Path, tuple[int, float]] = {}
        (tmp_path / "new.py").write_text("x = 1", encoding="utf-8")
        after = _collect_candidate_py_snapshot(tmp_path)
        changed = _diff_py_snapshots(before, after)
        assert changed == [tmp_path / "new.py"]

    def test_diff_detects_size_change(self, tmp_path: Path) -> None:
        f = tmp_path / "x.py"
        f.write_text("one", encoding="utf-8")
        before = _collect_candidate_py_snapshot(tmp_path)
        f.write_text("two two two", encoding="utf-8")
        after = _collect_candidate_py_snapshot(tmp_path)
        assert _diff_py_snapshots(before, after) == [f]

    def test_diff_ignores_deleted_file(self, tmp_path: Path) -> None:
        f = tmp_path / "x.py"
        f.write_text("one", encoding="utf-8")
        before = _collect_candidate_py_snapshot(tmp_path)
        f.unlink()
        after = _collect_candidate_py_snapshot(tmp_path)
        assert _diff_py_snapshots(before, after) == []

    def test_diff_noop_when_unchanged(self, tmp_path: Path) -> None:
        f = tmp_path / "x.py"
        f.write_text("stable", encoding="utf-8")
        before = _collect_candidate_py_snapshot(tmp_path)
        after = _collect_candidate_py_snapshot(tmp_path)
        assert _diff_py_snapshots(before, after) == []


# ---------------------------------------------------------------------------
# spawn_dev_subagent — subprocess boundary
# ---------------------------------------------------------------------------


class TestSpawnDevSubagent:
    """End-to-end exercise of the handler with all subprocess calls mocked."""

    @pytest.fixture
    def version_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Create a fake candidate dir under a fake repo root.

        We monkey-patch ``_repo_root`` to the tmp dir so the scope-check
        treats ``<tmp>/bots/cand_x/`` as a valid sibling. We also create
        a single .py file inside so mtime-diff has something to latch
        onto when the sub-agent "edits" it.
        """
        repo_root = tmp_path
        cand = repo_root / "bots" / "cand_x"
        cand.mkdir(parents=True)
        (cand / "foo.py").write_text("x = 1\n", encoding="utf-8")
        monkeypatch.setattr(
            "orchestrator.registry._repo_root",
            lambda: repo_root,
        )
        return cand

    def test_happy_path_runs_all_gates(
        self, version_dir: Path
    ) -> None:
        """Sub-agent succeeds, no scope violation, ruff + mypy pass."""
        fake = FakeRun()

        # Simulate an edit by the sub-agent: mutate foo.py size between
        # the two git-status calls so the py-snapshot diff sees it.
        def edit_during_subagent(argv: list[str], **kwargs: Any) -> _CompletedProc:
            (version_dir / "foo.py").write_text(
                "x = 1\ny = 2\n", encoding="utf-8"
            )
            return _CompletedProc(stdout="modified foo.py")

        fake.subagent_result = _CompletedProc(stdout="done")
        # Intercept specifically the claude call to also perform the FS edit.
        orig_call = fake.__call__

        def routed(argv: list[str], **kwargs: Any) -> _CompletedProc:
            if argv and argv[0] == "claude":
                edit_during_subagent(argv, **kwargs)
            return orig_call(argv, **kwargs)

        spawn_dev_subagent(
            version_dir,
            _make_improvement(),
            run=routed,
        )

        # Assert: claude ran once, git status ran twice (before + after),
        # no git checkout (no scope violation), ruff and mypy each ran once.
        heads = [c[0][0] for c in fake.calls]
        assert heads.count("claude") == 1
        assert sum(1 for c in fake.calls if c[0][:2] == ["git", "status"]) == 2
        assert sum(1 for c in fake.calls if c[0][:2] == ["git", "checkout"]) == 0
        assert sum(1 for c in fake.calls if c[0][:3] == ["uv", "run", "ruff"]) == 1
        assert sum(1 for c in fake.calls if c[0][:3] == ["uv", "run", "mypy"]) == 1

    def test_claude_not_found_raises_subagent_error(
        self, version_dir: Path
    ) -> None:
        fake = FakeRun()
        fake.subagent_result = FileNotFoundError("claude missing")
        with pytest.raises(DevApplySubagentError, match="claude CLI not found"):
            spawn_dev_subagent(version_dir, _make_improvement(), run=fake)

    def test_subagent_timeout_raises_timeout_error(
        self, version_dir: Path
    ) -> None:
        fake = FakeRun()
        fake.subagent_result = subprocess.TimeoutExpired(
            cmd="claude", timeout=900
        )
        with pytest.raises(DevApplyTimeoutError, match="900"):
            spawn_dev_subagent(version_dir, _make_improvement(), run=fake)

    def test_subagent_nonzero_exit_raises(
        self, version_dir: Path
    ) -> None:
        fake = FakeRun()
        fake.subagent_result = _CompletedProc(
            returncode=1, stderr="something went wrong"
        )
        with pytest.raises(DevApplySubagentError, match="rc=1"):
            spawn_dev_subagent(version_dir, _make_improvement(), run=fake)

    def test_out_of_scope_edit_raises_and_triggers_revert(
        self, version_dir: Path
    ) -> None:
        """Sub-agent edited a tracked file outside the candidate dir.

        The git-status diff shows ` M src/foo.py` in the *after* sample
        but not *before*. We expect ``DevApplyOutOfScopeError`` AND a
        ``git checkout -- src/foo.py`` call to revert.
        """
        fake = FakeRun()
        fake.git_status_sequence = [
            _CompletedProc(stdout=""),
            _CompletedProc(stdout=" M src/foo.py\n"),
        ]
        with pytest.raises(DevApplyOutOfScopeError, match="src/foo.py"):
            spawn_dev_subagent(version_dir, _make_improvement(), run=fake)

        checkouts = [
            c for c in fake.calls if c[0][:2] == ["git", "checkout"]
        ]
        assert len(checkouts) == 1
        assert "src/foo.py" in checkouts[0][0]

    def test_untracked_new_file_outside_candidate_is_out_of_scope(
        self, version_dir: Path
    ) -> None:
        """Sub-agent created a brand-new file under src/ (not the candidate)."""
        fake = FakeRun()
        fake.git_status_sequence = [
            _CompletedProc(stdout=""),
            _CompletedProc(stdout="?? src/new.py\n"),
        ]
        with pytest.raises(DevApplyOutOfScopeError, match="src/new.py"):
            spawn_dev_subagent(version_dir, _make_improvement(), run=fake)

    def test_untracked_candidate_dir_in_both_snapshots_passes_scope(
        self, version_dir: Path
    ) -> None:
        """Candidate dir shows as untracked before AND after — no diff, no violation."""
        fake = FakeRun()
        fake.git_status_sequence = [
            _CompletedProc(stdout="?? bots/cand_x/\n"),
            _CompletedProc(stdout="?? bots/cand_x/\n"),
        ]
        spawn_dev_subagent(
            version_dir, _make_improvement(), run=fake, validate=False
        )
        assert all(c[0][:2] != ["git", "checkout"] for c in fake.calls)

    def test_ruff_failure_raises_validation_error(
        self, version_dir: Path
    ) -> None:
        fake = FakeRun()

        # Force a py change so ruff would actually run.
        def routed(argv: list[str], **kwargs: Any) -> _CompletedProc:
            if argv and argv[0] == "claude":
                (version_dir / "foo.py").write_text(
                    "x = 1\ny = 2\n", encoding="utf-8"
                )
            return fake(argv, **kwargs)

        fake.ruff_result = _CompletedProc(
            returncode=1, stdout="E501 line too long"
        )
        with pytest.raises(DevApplyValidationError, match="ruff"):
            spawn_dev_subagent(version_dir, _make_improvement(), run=routed)

    def test_mypy_failure_raises_validation_error(
        self, version_dir: Path
    ) -> None:
        fake = FakeRun()

        def routed(argv: list[str], **kwargs: Any) -> _CompletedProc:
            if argv and argv[0] == "claude":
                (version_dir / "foo.py").write_text(
                    "x: int = 'oops'\n", encoding="utf-8"
                )
            return fake(argv, **kwargs)

        fake.mypy_result = _CompletedProc(
            returncode=1, stdout="error: incompatible type"
        )
        with pytest.raises(DevApplyValidationError, match="mypy"):
            spawn_dev_subagent(version_dir, _make_improvement(), run=routed)

    def test_validate_false_skips_ruff_and_mypy(
        self, version_dir: Path
    ) -> None:
        fake = FakeRun()

        def routed(argv: list[str], **kwargs: Any) -> _CompletedProc:
            if argv and argv[0] == "claude":
                (version_dir / "foo.py").write_text(
                    "x = 2\n", encoding="utf-8"
                )
            return fake(argv, **kwargs)

        spawn_dev_subagent(
            version_dir, _make_improvement(), run=routed, validate=False
        )
        assert all(
            c[0][:3] != ["uv", "run", "ruff"] for c in fake.calls
        )
        assert all(
            c[0][:3] != ["uv", "run", "mypy"] for c in fake.calls
        )

    def test_no_py_changes_skips_validation_even_when_enabled(
        self, version_dir: Path
    ) -> None:
        """Sub-agent reported success but didn't actually edit anything."""
        fake = FakeRun()
        # No FS mutation in the route — mtime/size won't change.
        spawn_dev_subagent(version_dir, _make_improvement(), run=fake)
        assert all(
            c[0][:3] != ["uv", "run", "ruff"] for c in fake.calls
        )
        assert all(
            c[0][:3] != ["uv", "run", "mypy"] for c in fake.calls
        )

    def test_prompt_piped_via_stdin_not_argv(
        self, version_dir: Path
    ) -> None:
        """Regression mirror of evolve._default_claude_fn stdin fix (533a02a).

        A concrete_change description can run long. Argv-passing would
        trip WinError 206 on Windows. Verify the prompt routes through
        ``input=`` and not as an argv token.
        """
        fake = FakeRun()
        imp = _make_improvement()
        spawn_dev_subagent(version_dir, imp, run=fake, validate=False)

        claude_calls = [
            c for c in fake.calls if c[0] and c[0][0] == "claude"
        ]
        assert len(claude_calls) == 1
        argv, kwargs = claude_calls[0]
        assert imp.concrete_change not in argv
        for token in argv:
            assert len(token) < 2000, (
                f"argv token length {len(token)} suggests prompt leaked"
            )
        assert imp.concrete_change in kwargs["input"]
        assert kwargs["cwd"] == str(version_dir)

    def test_claude_argv_includes_tool_and_permission_flags(
        self, version_dir: Path
    ) -> None:
        """CLI must restrict tools + auto-accept edits.

        If either of these slips we'd either hang on a permission prompt
        (no Bash approval) or let the sub-agent run arbitrary commands.
        """
        fake = FakeRun()
        spawn_dev_subagent(
            version_dir, _make_improvement(), run=fake, validate=False
        )
        claude_calls = [c for c in fake.calls if c[0] and c[0][0] == "claude"]
        assert len(claude_calls) == 1
        argv = claude_calls[0][0]
        assert "--tools" in argv
        tools_idx = argv.index("--tools")
        assert argv[tools_idx + 1] == "Read,Edit,Write,Grep,Glob"
        assert "--permission-mode" in argv
        pm_idx = argv.index("--permission-mode")
        assert argv[pm_idx + 1] == "acceptEdits"
        assert "--add-dir" in argv
        ad_idx = argv.index("--add-dir")
        assert argv[ad_idx + 1] == str(version_dir)
