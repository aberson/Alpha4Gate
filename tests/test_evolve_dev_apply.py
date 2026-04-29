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
    _sanitize_imp_paths,
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
        # The concrete_change (which carries the large imp payload) must
        # go through stdin, never argv. Check substring membership on
        # every argv token except the one we deliberately pass as the
        # system prompt.
        sys_prompt_idx = argv.index("--append-system-prompt") + 1
        for i, token in enumerate(argv):
            if i == sys_prompt_idx:
                # The static system prompt is intentionally long.
                continue
            assert imp.concrete_change not in token, (
                f"concrete_change leaked into argv token {i}: {token[:80]!r}"
            )
            assert len(token) < 2000, (
                f"argv token {i} length {len(token)} suggests prompt leaked"
            )
        assert imp.concrete_change in kwargs["input"]
        assert kwargs["cwd"] == str(version_dir)

    def test_claude_argv_includes_tool_and_permission_flags(
        self, version_dir: Path
    ) -> None:
        """CLI must restrict tools + auto-accept edits.

        If either of these slips we'd either hang on a permission prompt
        (no Bash approval) or let the sub-agent run arbitrary commands.

        Bash is now in the tool set (so the sub-agent can self-validate
        with ruff/mypy before exiting) but ``--allowed-tools`` scopes
        which Bash commands are pre-approved.
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
        assert argv[tools_idx + 1] == "Read,Edit,Write,Grep,Glob,Bash"
        assert "--allowed-tools" in argv
        allowed_idx = argv.index("--allowed-tools")
        allowed = argv[allowed_idx + 1]
        # Only mypy/ruff/uv/python Bash invocations are pre-approved.
        assert "Bash(mypy:*)" in allowed
        assert "Bash(ruff:*)" in allowed
        assert "Bash(uv:*)" in allowed
        assert "Bash(python:*)" in allowed
        # Must NOT pre-approve broad Bash (git, curl, rm, etc.).
        assert "Bash(git:*)" not in allowed
        assert "Bash(*)" not in allowed
        assert "--permission-mode" in argv
        pm_idx = argv.index("--permission-mode")
        assert argv[pm_idx + 1] == "acceptEdits"
        assert "--add-dir" in argv
        ad_idx = argv.index("--add-dir")
        assert argv[ad_idx + 1] == str(version_dir)

    def test_retry_with_feedback_recovers_on_second_attempt(
        self, version_dir: Path
    ) -> None:
        """Mypy fails on attempt 1; sub-agent re-runs with feedback and
        attempt 2's edit validates clean."""
        fake = FakeRun()
        mypy_results = [
            _CompletedProc(
                returncode=1,
                stdout=(
                    "foo.py:5: error: No overload variant of \"range\" "
                    "matches argument type \"float\""
                ),
            ),
            _CompletedProc(returncode=0),  # retry passes
        ]
        mypy_idx = {"n": 0}

        def routed(argv: list[str], **kwargs: Any) -> _CompletedProc:
            if argv and argv[0] == "claude":
                (version_dir / "foo.py").write_text(
                    f"x = 1\ny = {mypy_idx['n'] + 2}\n", encoding="utf-8"
                )
            if argv[:3] == ["uv", "run", "mypy"]:
                idx = mypy_idx["n"]
                mypy_idx["n"] += 1
                res = mypy_results[idx]
                fake.calls.append((list(argv), dict(kwargs)))
                return res
            return fake(argv, **kwargs)

        # Should NOT raise — the second attempt fixes it.
        spawn_dev_subagent(
            version_dir, _make_improvement(), run=routed
        )

        # Two sub-agent calls (initial + 1 retry), two mypy runs.
        claude_calls = [
            c for c in fake.calls if c[0] and c[0][0] == "claude"
        ]
        assert len(claude_calls) == 2
        mypy_calls = [
            c for c in fake.calls if c[0][:3] == ["uv", "run", "mypy"]
        ]
        assert len(mypy_calls) == 2

        # The second sub-agent invocation must carry the failed validator
        # output in its stdin prompt so the model can self-correct.
        retry_stdin = claude_calls[1][1]["input"]
        assert "YOUR PREVIOUS ATTEMPT FAILED VALIDATION" in retry_stdin
        assert "No overload variant of \"range\"" in retry_stdin

    def test_retry_exhaustion_raises_last_validation_error(
        self, version_dir: Path
    ) -> None:
        """All 3 attempts fail mypy — raise the final DevApplyValidationError
        and stop hammering the sub-agent."""
        fake = FakeRun()
        fake.mypy_result = _CompletedProc(
            returncode=1, stdout="persistent type error"
        )

        def routed(argv: list[str], **kwargs: Any) -> _CompletedProc:
            if argv and argv[0] == "claude":
                # Mutate size each attempt so the diff still sees a change.
                call_n = sum(
                    1 for c in fake.calls if c[0] and c[0][0] == "claude"
                )
                (version_dir / "foo.py").write_text(
                    "x = 1\n" + "y\n" * (call_n + 1), encoding="utf-8"
                )
            return fake(argv, **kwargs)

        with pytest.raises(DevApplyValidationError, match="mypy"):
            spawn_dev_subagent(
                version_dir, _make_improvement(), run=routed
            )
        claude_calls = [
            c for c in fake.calls if c[0] and c[0][0] == "claude"
        ]
        assert len(claude_calls) == 3  # default max_attempts=3

    def test_max_attempts_one_disables_retry(
        self, version_dir: Path
    ) -> None:
        """``max_attempts=1`` gives back the old single-shot behaviour."""
        fake = FakeRun()
        fake.mypy_result = _CompletedProc(
            returncode=1, stdout="type error"
        )

        def routed(argv: list[str], **kwargs: Any) -> _CompletedProc:
            if argv and argv[0] == "claude":
                # Size must differ from the fixture's foo.py ("x = 1\n")
                # so _diff_py_snapshots detects the edit and validation
                # runs. Same-length writes on sub-second mtime would skip
                # validation and spuriously return success here.
                (version_dir / "foo.py").write_text(
                    "x = 2\nextra_content_to_change_size\n",
                    encoding="utf-8",
                )
            return fake(argv, **kwargs)

        with pytest.raises(DevApplyValidationError):
            spawn_dev_subagent(
                version_dir,
                _make_improvement(),
                run=routed,
                max_attempts=1,
            )
        claude_calls = [
            c for c in fake.calls if c[0] and c[0][0] == "claude"
        ]
        assert len(claude_calls) == 1

    def test_out_of_scope_edit_not_retried(
        self, version_dir: Path
    ) -> None:
        """Scope violations are a safety issue, not a type error — they
        must NOT trigger the retry path."""
        fake = FakeRun()
        fake.git_status_sequence = [
            _CompletedProc(stdout=""),
            _CompletedProc(stdout=" M src/unrelated.py\n"),
        ]
        with pytest.raises(DevApplyOutOfScopeError):
            spawn_dev_subagent(
                version_dir, _make_improvement(), run=fake
            )
        claude_calls = [
            c for c in fake.calls if c[0] and c[0][0] == "claude"
        ]
        # Only the initial invocation — no retries for scope violations.
        assert len(claude_calls) == 1


# ---------------------------------------------------------------------------
# Path sanitization (fix for stack-apply DevApplyOutOfScopeError on imps that
# reference the parent's path in concrete_change)
# ---------------------------------------------------------------------------


class TestSanitizeImpPaths:
    """``_sanitize_imp_paths`` strips ``bots/vN/`` prefixes from imp text
    so the sub-agent doesn't follow them out of its cwd. Reason: imp
    descriptions from the advisor are written against the parent (e.g.
    ``In bots/v3/scouting.py``); during stack-apply the cwd is the new
    candidate (``bots/v4/``) and following the literal path edits the
    parent — caught by ``_assert_scope`` only after a wasted attempt.
    """

    def test_strips_single_reference(self) -> None:
        text = "In bots/v3/scouting.py, add a method `escort_army`."
        assert _sanitize_imp_paths(text) == (
            "In scouting.py, add a method `escort_army`."
        )

    def test_strips_multiple_references(self) -> None:
        text = (
            "Edit bots/v3/bot.py and bots/v3/macro_manager.py to wire "
            "the new helper. Reference: bots/v3/decision_engine.py:273."
        )
        assert _sanitize_imp_paths(text) == (
            "Edit bot.py and macro_manager.py to wire the new helper. "
            "Reference: decision_engine.py:273."
        )

    def test_handles_arbitrary_version_number(self) -> None:
        # Future-proof: works for v0, v1, v12, v999, …
        assert _sanitize_imp_paths("bots/v0/x.py") == "x.py"
        assert _sanitize_imp_paths("bots/v12/y.py") == "y.py"
        assert _sanitize_imp_paths("bots/v999/z.py") == "z.py"

    def test_leaves_cand_paths_alone(self) -> None:
        # cand_xxx paths should NOT be stripped — those are intermediate
        # and shouldn't appear in advisor text anyway, but keep the rule
        # narrow.
        text = "In bots/cand_abc123/foo.py"
        assert _sanitize_imp_paths(text) == text

    def test_empty_string(self) -> None:
        assert _sanitize_imp_paths("") == ""

    def test_text_without_path(self) -> None:
        text = "Refactor the foo helper to take a Bar argument."
        assert _sanitize_imp_paths(text) == text

    def test_invoke_subagent_passes_sanitized_text_to_claude(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end: imp.concrete_change with bots/v3/ becomes bare
        filename in the prompt sent to ``claude -p`` via stdin."""
        repo_root = tmp_path
        cand = repo_root / "bots" / "cand_x"
        cand.mkdir(parents=True)
        (cand / "foo.py").write_text("x = 1\n", encoding="utf-8")
        monkeypatch.setattr(
            "orchestrator.registry._repo_root",
            lambda: repo_root,
        )
        imp = Improvement(
            rank=1,
            title="path-sanitize test",
            type="dev",
            description="Reference: bots/v3/scouting.py:42",
            principle_ids=["1"],
            expected_impact="—",
            concrete_change="Edit bots/v3/foo.py to add a stub.",
        )

        fake = FakeRun()
        fake.subagent_result = _CompletedProc(stdout="done")

        spawn_dev_subagent(
            cand, imp, run=fake, validate=False
        )

        claude_calls = [c for c in fake.calls if c[0] and c[0][0] == "claude"]
        assert len(claude_calls) == 1
        stdin_text = claude_calls[0][1].get("input", "")
        assert "bots/v3/foo.py" not in stdin_text
        assert "bots/v3/scouting.py" not in stdin_text
        assert "Edit foo.py to add a stub." in stdin_text
        assert "Reference: scouting.py:42" in stdin_text
