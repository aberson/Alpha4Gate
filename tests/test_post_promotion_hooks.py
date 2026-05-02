"""Tests for ``bots.v0.learning.post_promotion_hooks`` (Step 2).

The hook is the canonical post-promotion entrypoint wired from three
call sites (``scripts/evolve.py``, ``scripts/snapshot_bot.py``, and
``.claude/skills/improve-bot-advised/SKILL.md``). Contracts:

1. Invalid version → ``ValueError`` raised BEFORE any subprocess runs.
2. Successful invocation → both subprocesses launch (when both
   scripts exist) with list-form argv, ``shell=False``, ``timeout=60``.
3. Failing build_lineage subprocess → log warning, continue to
   compute_weight_dynamics, return ``None``.
4. Failing compute_weight_dynamics subprocess → log warning, return
   ``None``.
5. ``subprocess.TimeoutExpired`` from either subprocess → log warning,
   return ``None``.
6. Missing ``compute_weight_dynamics.py`` (Step 9 not shipped yet) →
   log warning, no crash.

The "concurrent lazy-init lock" test belongs in
``tests/test_api_lineage.py`` per the spec — that test concerns the
``/api/lineage`` lazy-init, not this helper.
"""

from __future__ import annotations

import ast
import logging
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from bots.v0.learning import post_promotion_hooks


@pytest.fixture()
def fake_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Stage a fake repo root with ``scripts/`` and patch _repo_root."""
    (tmp_path / "scripts").mkdir()
    monkeypatch.setattr(
        post_promotion_hooks, "_repo_root", lambda: tmp_path
    )
    yield tmp_path


def _stage_script(repo: Path, name: str) -> Path:
    """Create an empty ``scripts/<name>`` so the file-existence check passes."""
    script = repo / "scripts" / name
    script.write_text("# stub\n", encoding="utf-8")
    return script


def _ok_completed(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")


def _fail_completed(
    args: list[str], stderr: str = "boom"
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=args, returncode=1, stdout="", stderr=stderr
    )


class TestValidation:
    """Version validation runs before any side effect."""

    @pytest.mark.parametrize(
        "bad_version",
        [
            "v3; rm -rf",  # shell-injection-style payload
            "",  # empty string
            123,  # wrong type (int)
            None,  # wrong type (None)
            "v",  # missing digits
            "version1",  # wrong prefix
            "V3",  # wrong case
            "v3.1",  # extra punctuation
        ],
    )
    def test_invalid_version_rejected_without_subprocess(
        self,
        fake_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        bad_version: object,
    ) -> None:
        """Bad version → ValueError, subprocess.run NEVER called.

        Covers shell-injection payloads, empty string, wrong types
        (int / None), and shape mismatches (missing digits, wrong
        prefix, wrong case, extra punctuation). All are rejected by
        the same regex BEFORE any side effect — the validated string
        is the only caller-controlled input that reaches the argv
        list, so this is the defense-in-depth guard on top of the
        ``shell=False`` argv passing already done by ``_run_subprocess``.
        """
        spy = MagicMock(name="subprocess.run")
        monkeypatch.setattr(post_promotion_hooks.subprocess, "run", spy)

        with pytest.raises(ValueError):
            post_promotion_hooks.run_post_promotion_hooks(
                bad_version  # type: ignore[arg-type]
            )

        spy.assert_not_called()


class TestSuccessfulInvocation:
    """Both subprocess calls fire when both scripts exist."""

    def test_both_subprocesses_called(
        self,
        fake_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stage_script(fake_repo, "build_lineage.py")
        _stage_script(fake_repo, "compute_weight_dynamics.py")

        captured: list[list[str]] = []

        def fake_run(
            args: list[str], **_kwargs: Any
        ) -> subprocess.CompletedProcess[str]:
            captured.append(list(args))
            return _ok_completed(args)

        monkeypatch.setattr(post_promotion_hooks.subprocess, "run", fake_run)

        result = post_promotion_hooks.run_post_promotion_hooks("v3")

        assert result is None
        assert len(captured) == 2
        # First: build_lineage. Second: compute_weight_dynamics with --version v3.
        assert any("build_lineage.py" in arg for arg in captured[0])
        assert any("compute_weight_dynamics.py" in arg for arg in captured[1])
        assert "--version" in captured[1]
        assert "v3" in captured[1]

    def test_subprocess_kwargs_match_contract(
        self,
        fake_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """list-form, shell=False, capture_output, text, timeout=60, check=False."""
        _stage_script(fake_repo, "build_lineage.py")

        captured_kwargs: list[dict[str, Any]] = []

        def fake_run(
            args: list[str], **kwargs: Any
        ) -> subprocess.CompletedProcess[str]:
            captured_kwargs.append(kwargs)
            return _ok_completed(args)

        monkeypatch.setattr(post_promotion_hooks.subprocess, "run", fake_run)

        post_promotion_hooks.run_post_promotion_hooks("v5")

        assert len(captured_kwargs) == 1
        kw = captured_kwargs[0]
        assert kw["shell"] is False
        assert kw["check"] is False
        assert kw["capture_output"] is True
        assert kw["text"] is True
        assert kw["timeout"] == post_promotion_hooks._SUBPROCESS_TIMEOUT_S


class TestFailureModes:
    """Subprocess failures log warnings but never raise."""

    @pytest.mark.parametrize(
        ("failure_kind", "expected_log_substr"),
        [
            ("nonzero_exit", "build_lineage"),
            ("timeout", "timed out"),
            ("oserror", "failed to launch"),
        ],
    )
    def test_build_lineage_failure_modes_swallowed(
        self,
        fake_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        failure_kind: str,
        expected_log_substr: str,
    ) -> None:
        """Each subprocess failure mode → warning logged, no raise.

        Walks the three modes the helper must swallow:
        non-zero exit (``CalledProcessError``-style), ``TimeoutExpired``
        from the bounded wall-clock cap, and ``OSError`` from a
        failed launch (missing binary / permission denied / etc.).
        Each variant expects a different log substring confirming
        the helper took the right branch, but the contract is
        identical: ``run_post_promotion_hooks`` returns ``None``
        without propagating.
        """
        _stage_script(fake_repo, "build_lineage.py")

        def fake_run(args: list[str], **_kwargs: Any) -> Any:
            if failure_kind == "nonzero_exit":
                return _fail_completed(args, stderr="boom")
            if failure_kind == "timeout":
                raise subprocess.TimeoutExpired(cmd=args, timeout=60.0)
            if failure_kind == "oserror":
                raise OSError("ENOENT: no python")
            raise AssertionError(f"unknown kind: {failure_kind}")

        monkeypatch.setattr(post_promotion_hooks.subprocess, "run", fake_run)

        with caplog.at_level(
            logging.WARNING, logger="bots.v0.learning.post_promotion_hooks"
        ):
            result = post_promotion_hooks.run_post_promotion_hooks("v3")

        assert result is None
        assert any(
            expected_log_substr in r.message for r in caplog.records
        )

    def test_first_subprocess_failure_does_not_block_second(
        self,
        fake_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """build_lineage non-zero → compute_weight_dynamics STILL runs.

        Distinct from the parametrized failure-mode walk because the
        load-bearing assertion here is the *count* — both subprocesses
        must be invoked even after the first one fails. Pins the
        sequential-no-short-circuit contract.
        """
        _stage_script(fake_repo, "build_lineage.py")
        _stage_script(fake_repo, "compute_weight_dynamics.py")

        call_count = {"n": 0}

        def fake_run(
            args: list[str], **_kwargs: Any
        ) -> subprocess.CompletedProcess[str]:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _fail_completed(args, stderr="lineage failed")
            return _ok_completed(args)

        monkeypatch.setattr(post_promotion_hooks.subprocess, "run", fake_run)

        with caplog.at_level(
            logging.WARNING, logger="bots.v0.learning.post_promotion_hooks"
        ):
            result = post_promotion_hooks.run_post_promotion_hooks("v3")

        assert result is None
        assert call_count["n"] == 2  # second subprocess STILL ran
        assert any("build_lineage" in r.message for r in caplog.records)

    def test_missing_compute_weight_dynamics_no_crash(
        self,
        fake_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Step 9 deliverable not staged — hook still completes cleanly."""
        _stage_script(fake_repo, "build_lineage.py")
        # NO compute_weight_dynamics.py.

        captured: list[list[str]] = []

        def fake_run(
            args: list[str], **_kwargs: Any
        ) -> subprocess.CompletedProcess[str]:
            captured.append(list(args))
            return _ok_completed(args)

        monkeypatch.setattr(post_promotion_hooks.subprocess, "run", fake_run)

        with caplog.at_level(logging.INFO, logger="bots.v0.learning.post_promotion_hooks"):
            result = post_promotion_hooks.run_post_promotion_hooks("v3")

        assert result is None
        # Only ONE subprocess fired (build_lineage). The missing
        # compute_weight_dynamics.py is detected and skipped.
        assert len(captured) == 1
        assert any("compute_weight_dynamics" in r.message for r in caplog.records)

    def test_missing_build_lineage_no_crash(
        self,
        fake_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Defense-in-depth: even build_lineage.py missing must not crash."""
        # NEITHER script staged.
        spy = MagicMock(name="subprocess.run")
        monkeypatch.setattr(post_promotion_hooks.subprocess, "run", spy)

        with caplog.at_level(logging.WARNING, logger="bots.v0.learning.post_promotion_hooks"):
            result = post_promotion_hooks.run_post_promotion_hooks("v3")

        assert result is None
        spy.assert_not_called()


class TestV10Mirror:
    """``bots.v10.learning.post_promotion_hooks`` is byte-equivalent."""

    def test_v10_module_present(self) -> None:
        # The current snapshot copy must exist so production runtime
        # imports (``bots.current`` → ``bots.v10``) resolve.
        from bots.v10.learning import post_promotion_hooks as v10_hooks

        assert hasattr(v10_hooks, "run_post_promotion_hooks")
        assert callable(v10_hooks.run_post_promotion_hooks)

    def test_v0_and_v10_share_same_logic(self) -> None:
        """Both modules are the canonical+snapshot pair."""
        v0_path = Path(post_promotion_hooks.__file__)
        from bots.v10.learning import post_promotion_hooks as v10_hooks

        v10_path = Path(v10_hooks.__file__)
        assert v0_path.read_text(encoding="utf-8") == v10_path.read_text(
            encoding="utf-8"
        ), "v0 and v10 hook helpers have drifted — update both."


class TestCallSites:
    """Promotion paths must wrap the hook call in try/except.

    The helper itself never raises (verified by ``TestFailureModes``),
    but ``scripts/evolve.py`` and ``scripts/snapshot_bot.py`` both
    wrap the call in ``try/except`` for defense in depth: a future
    refactor of the helper that introduces a raise (e.g. a typo, or
    a deliberate validation strengthening) must NOT propagate up
    into the promotion path. Pin that contract here so a future
    refactor that drops the wrapper fails this test.
    """

    @pytest.mark.parametrize(
        "script_relpath",
        [
            "scripts/evolve.py",
            "scripts/snapshot_bot.py",
        ],
    )
    def test_call_site_is_wrapped_in_try_except(
        self, script_relpath: str
    ) -> None:
        """AST-walk: every call to ``run_post_promotion_hooks`` must be
        inside a ``Try`` block whose handlers include a bare ``except``
        / ``except Exception`` (defense-in-depth wrapper).

        AST is the right tool here: a regex / string-search heuristic
        misses cases like a ``try:`` belonging to a SIBLING block that
        happens to appear within ~500 chars. Walking the syntax tree
        means a future refactor that pulls the call out of its
        ``try`` (e.g. flattens the wrapper, moves it to a helper
        without the wrapper, etc.) is caught precisely.
        """
        repo_root = Path(__file__).resolve().parent.parent
        src_path = repo_root / script_relpath
        assert src_path.is_file(), f"missing {src_path}"
        source = src_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(src_path))

        # Map every Call node whose function name resolves to
        # ``run_post_promotion_hooks`` to the chain of its enclosing
        # ``Try`` ancestors. The contract is satisfied iff EVERY such
        # call is inside at least one ``Try`` block with a broad
        # exception handler.
        found_calls = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                fname = func.id
            elif isinstance(func, ast.Attribute):
                fname = func.attr
            else:
                continue
            if fname != "run_post_promotion_hooks":
                continue
            found_calls += 1

            # Walk back up the parent chain looking for an enclosing
            # Try. We have to compute parents because ast doesn't
            # store them by default.
            parents = _build_parent_map(tree)
            wrapped = False
            cur: ast.AST | None = node
            while cur is not None:
                parent = parents.get(cur)
                if isinstance(parent, ast.Try):
                    # The call must be in the ``body`` of the try
                    # (NOT in a handler / finally), and at least one
                    # handler must be broad (``except`` / ``except
                    # Exception``).
                    if cur in parent.body or _is_descendant(
                        cur, parent.body, parents
                    ):
                        for handler in parent.handlers:
                            if handler.type is None:
                                wrapped = True
                                break
                            if (
                                isinstance(handler.type, ast.Name)
                                and handler.type.id
                                in {"Exception", "BaseException"}
                            ):
                                wrapped = True
                                break
                        if wrapped:
                            break
                cur = parent

            assert wrapped, (
                f"call to run_post_promotion_hooks at "
                f"{script_relpath}:{node.lineno} is not wrapped in a "
                "try/except — promotion path can be broken by a hook "
                "raise. Restore the defense-in-depth wrapper."
            )

        assert found_calls >= 1, (
            f"{script_relpath} no longer calls run_post_promotion_hooks; "
            "if the wiring moved, update this test to point at the new "
            "call site."
        )


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """Return ``{child_node: parent_node}`` for every node in ``tree``.

    Helper for ``TestCallSites`` — Python's ``ast`` doesn't track
    parents by default, so we precompute the mapping with one walk.
    """
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _is_descendant(
    node: ast.AST,
    body: list[ast.stmt],
    parents: dict[ast.AST, ast.AST],
) -> bool:
    """Return True iff ``node`` lives somewhere in ``body``'s subtree."""
    cur: ast.AST | None = node
    body_set = set(body)
    while cur is not None:
        if cur in body_set:
            return True
        cur = parents.get(cur)
    return False


