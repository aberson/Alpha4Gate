"""Sub-agent dev-apply handler for evolve `dev`-type improvements.

Public surface is :func:`spawn_dev_subagent`, suitable for injection into
``apply_improvement`` via the ``dev_apply_fn`` slot. The handler shells
out to the Claude Code CLI (``claude -p``) with the Read/Edit/Write/Grep/Glob
tools enabled and `cwd` scoped to the candidate's version directory, so
the sub-agent can inspect and mutate that version's source in place.

Safety layers (all enforced on the caller's side via raised exceptions):

1. **Out-of-scope detector** — snapshots repo-wide tracked-file status
   via `git diff --name-only HEAD` before and after the sub-agent call;
   any mutated path outside the candidate dir triggers an immediate
   revert (`git checkout -- <paths>`) and raises
   :class:`DevApplyOutOfScopeError`.
2. **Ruff gate** — runs ``ruff check`` against the changed ``.py`` files
   in the candidate dir; any violation raises
   :class:`DevApplyValidationError` and the round is discarded.
3. **Mypy --strict gate** — same shape as ruff. NOTE: ruff+mypy gate is
   a first-pass heuristic; revisit after the first soak (see Phase 9
   Step 9 follow-up) to tune which rule classes are round-failing vs
   warning.

Every failure path raises a :class:`DevApplyError` subclass; the evolve
loop already catches any exception from ``apply_improvement`` and marks
the round as a consumed-tie discard (improvements removed from the pool,
never retried in the same run — the user-specified "consumed-failed"
semantic).
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.evolve import Improvement

_log = logging.getLogger(__name__)

__all__ = [
    "DevApplyError",
    "DevApplyOutOfScopeError",
    "DevApplySubagentError",
    "DevApplyTimeoutError",
    "DevApplyValidationError",
    "spawn_dev_subagent",
]


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


class DevApplyError(RuntimeError):
    """Base class for any dev-apply failure.

    The evolve loop catches ``Exception`` at round level, so subclassing
    ``RuntimeError`` is enough — the specific subclass is what downstream
    log readers use to attribute failures.
    """


class DevApplySubagentError(DevApplyError):
    """The sub-agent exited non-zero or produced no output."""


class DevApplyTimeoutError(DevApplyError):
    """The sub-agent exceeded the wall-clock timeout."""


class DevApplyOutOfScopeError(DevApplyError):
    """The sub-agent mutated files outside the candidate dir; changes reverted."""


class DevApplyValidationError(DevApplyError):
    """Post-edit ruff or mypy gate failed; the round is unusable."""


# ---------------------------------------------------------------------------
# Sub-agent prompt (static; no per-call formatting besides the imp fields)
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """\
You are applying a SINGLE focused improvement to a snapshot of a
Python SC2 Protoss bot codebase. Rules:

1. Modify ONLY files inside the current working directory. Never touch
   files above the cwd. Never touch `.git`, `data/`, or other snapshot
   state. If a change would require files outside this dir, stop and
   exit without edits — the caller will mark the improvement unusable.
2. Apply EXACTLY the improvement described by the user message. Do not
   refactor adjacent code. Do not rename unrelated symbols. Do not add
   comments. Do not update docstrings. Do not "clean up" while you're
   in the file.
3. Keep the change minimal. If the improvement references a line or
   function that does not exist, do not invent a workaround — exit
   without edits.
4. Do not run shell commands. You have Read, Edit, Write, Grep, and
   Glob only. Verify with Grep/Read; do not attempt Bash/pytest/mypy.
5. When the change is complete, produce a one-line summary of the
   files you modified. No prose before or after.

Scoping is enforced externally: the caller will revert any out-of-scope
edit and discard the round. Syntactic correctness is gated externally
via ruff + mypy --strict on the changed files; passing both is a hard
pre-condition for the round to proceed.
"""


_USER_PROMPT_TEMPLATE = """\
IMPROVEMENT TO APPLY

Title: {title}
Rank: {rank}
Expected impact: {expected_impact}

Description:
{description}

Concrete change (this is the instruction — follow it literally):
{concrete_change}

Working directory (cwd): a snapshot of the bot codebase. Modify files
under this directory only. When finished, print a single line listing
the files you modified and nothing else.
"""


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def spawn_dev_subagent(
    version_dir: Path,
    imp: Improvement,
    *,
    model: str = "opus",
    timeout: float = 900.0,
    validate: bool = True,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Apply *imp* to *version_dir* by shelling out to a Claude Code sub-agent.

    This function is intended to be injected into
    ``orchestrator.evolve.apply_improvement`` via the ``dev_apply_fn``
    slot. See :mod:`orchestrator.evolve_dev_apply` module docstring for
    the safety-layer contract.

    Parameters
    ----------
    version_dir:
        Absolute path to the candidate snapshot
        (e.g. ``bots/cand_abc123_a/``). The sub-agent runs with this as
        its cwd and is restricted (by convention + out-of-scope check)
        to edits under it.
    imp:
        The :class:`Improvement` to apply. Must be ``type="dev"``;
        caller (``apply_improvement``) already enforces that.
    model:
        Model alias for the sub-agent. Default ``"opus"`` per user
        preference — dev changes benefit from richer reasoning.
    timeout:
        Wall-clock budget for the sub-agent process, in seconds.
        Default 900s (15 min). A dev-apply that blows this budget is
        almost certainly stuck; timing out and discarding the round is
        safer than letting the soak hang.
    validate:
        If True (default), runs ruff + mypy --strict on the changed
        ``.py`` files after the sub-agent exits. Set False in tests that
        only want to exercise the spawn path.
    run:
        Injected subprocess runner so tests can mock the CLI boundary
        without touching the real ``claude`` binary. Same pattern as
        :func:`orchestrator.evolve._default_claude_fn`.
    """
    from orchestrator.registry import _repo_root

    repo_root = _repo_root()
    if not version_dir.is_dir():
        raise DevApplySubagentError(
            f"version_dir does not exist: {version_dir}"
        )

    # SCOPE CHECK uses git status: captures tracked-file edits AND new
    # untracked files anywhere in the repo. It intentionally does NOT see
    # file-level edits inside the already-untracked candidate dir (git
    # collapses that dir), which is fine — those are all in-scope by
    # construction. See `_collect_candidate_py_snapshot` below for the
    # validation-target discovery that DOES see in-dir edits.
    git_before = _snapshot_repo_state(repo_root, run=run)
    py_before = _collect_candidate_py_snapshot(version_dir)

    _invoke_subagent(version_dir, imp, model=model, timeout=timeout, run=run)

    git_after = _snapshot_repo_state(repo_root, run=run)
    py_after = _collect_candidate_py_snapshot(version_dir)

    repo_changed = sorted(git_after - git_before)
    _log.info(
        "dev-apply: sub-agent exited; %d repo-wide path(s) changed",
        len(repo_changed),
    )
    _assert_scope(repo_root, version_dir, repo_changed, run=run)

    changed_py = sorted(_diff_py_snapshots(py_before, py_after))
    _log.info(
        "dev-apply: %d .py file(s) changed under candidate dir",
        len(changed_py),
    )

    if validate and changed_py:
        _run_ruff(changed_py, run=run)
        _run_mypy(repo_root, changed_py, run=run)


# ---------------------------------------------------------------------------
# Sub-agent invocation
# ---------------------------------------------------------------------------


def _invoke_subagent(
    version_dir: Path,
    imp: Improvement,
    *,
    model: str,
    timeout: float,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Shell out to ``claude -p`` and let the sub-agent do the edits.

    The prompt is piped via stdin (same lesson as commit 533a02a — Windows
    CreateProcess rejects cmdlines > ~32 KiB, and long concrete_change
    descriptions can approach that).
    """
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        title=imp.title,
        rank=imp.rank,
        expected_impact=imp.expected_impact,
        description=imp.description,
        concrete_change=imp.concrete_change,
    )

    argv = [
        "claude",
        "-p",
        "--model", model,
        "--tools", "Read,Edit,Write,Grep,Glob",
        "--permission-mode", "acceptEdits",
        "--add-dir", str(version_dir),
        "--output-format", "text",
        "--no-session-persistence",
        "--append-system-prompt", _SYSTEM_PROMPT,
    ]

    _log.info(
        "dev-apply: spawning sub-agent (model=%s, cwd=%s, timeout=%.0fs, "
        "imp=%r)",
        model,
        version_dir,
        timeout,
        imp.title,
    )

    try:
        result = run(
            argv,
            input=user_prompt,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            cwd=str(version_dir),
        )
    except FileNotFoundError as e:
        raise DevApplySubagentError(
            "claude CLI not found on PATH; install Claude Code."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise DevApplyTimeoutError(
            f"sub-agent exceeded {e.timeout}s wall-clock budget applying "
            f"{imp.title!r}."
        ) from e

    if result.returncode != 0:
        raise DevApplySubagentError(
            f"sub-agent exited rc={result.returncode} applying "
            f"{imp.title!r}; stderr: {result.stderr.strip()[:500]!r}"
        )


# ---------------------------------------------------------------------------
# Repo-state snapshot + scope guard
# ---------------------------------------------------------------------------


def _collect_candidate_py_snapshot(version_dir: Path) -> dict[Path, tuple[int, float]]:
    """Map every ``.py`` file under *version_dir* to ``(size, mtime_ns)``.

    We pair size with mtime because some filesystems (Windows FAT/exFAT,
    network mounts) have 2-second mtime resolution — an edit completing
    inside the same second as the snapshot would look unchanged by
    mtime alone. Size usually differs on a real edit; if both match the
    edit is a no-op anyway (idempotent rewrite), which is fine to skip.
    """
    out: dict[Path, tuple[int, float]] = {}
    if not version_dir.is_dir():
        return out
    for p in version_dir.rglob("*.py"):
        try:
            st = p.stat()
        except OSError:
            continue
        out[p] = (st.st_size, st.st_mtime_ns)
    return out


def _diff_py_snapshots(
    before: dict[Path, tuple[int, float]],
    after: dict[Path, tuple[int, float]],
) -> list[Path]:
    """Return paths present in *after* whose (size, mtime) differs from *before*.

    Newly-created files (absent in *before*) are included. Deleted files
    (absent in *after*) are omitted — a validation run on a deleted file
    would error on FileNotFoundError without adding signal.
    """
    changed: list[Path] = []
    for path, sig_after in after.items():
        sig_before = before.get(path)
        if sig_before is None or sig_before != sig_after:
            changed.append(path)
    return changed


def _snapshot_repo_state(
    repo_root: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> set[str]:
    """Return the set of repo-relative paths currently dirty vs HEAD.

    ``git status --porcelain`` covers tracked modifications, untracked
    files, and deletions in a single pass. We normalise to forward
    slashes so cross-OS diffs stay comparable.
    """
    result = run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(repo_root),
    )
    if result.returncode != 0:
        raise DevApplySubagentError(
            f"git status failed (rc={result.returncode}); "
            f"cannot scope-check sub-agent edits. stderr: "
            f"{result.stderr.strip()[:500]!r}"
        )
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Porcelain format: `XY path`. `??` = untracked. Split off the
        # two status chars + space; handle rename arrows conservatively.
        rest = line[3:] if len(line) > 3 else ""
        if "->" in rest:
            rest = rest.split("->", 1)[1].strip()
        paths.add(rest.strip().replace("\\", "/"))
    return paths


def _assert_scope(
    repo_root: Path,
    version_dir: Path,
    changed: list[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Fail + revert if any changed path is outside *version_dir*.

    The version dir must live under the repo root (otherwise
    git status would not have seen it). ``relative_to`` raises
    ``ValueError`` if the path escapes — that counts as out-of-scope.
    """
    try:
        rel_prefix = version_dir.resolve().relative_to(repo_root.resolve())
    except ValueError as e:
        raise DevApplySubagentError(
            f"version_dir {version_dir} is not inside repo_root {repo_root}"
        ) from e
    prefix = str(rel_prefix).replace("\\", "/") + "/"

    out_of_scope = [p for p in changed if not p.startswith(prefix)]
    if not out_of_scope:
        return

    _log.warning(
        "dev-apply: sub-agent mutated %d path(s) outside %s; reverting",
        len(out_of_scope),
        prefix,
    )
    # Best-effort revert. We don't raise on revert failure because the
    # caller sees the scope error regardless and can reconcile manually.
    for p in out_of_scope:
        revert = run(
            ["git", "checkout", "--", p],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(repo_root),
        )
        if revert.returncode != 0:
            _log.warning(
                "dev-apply: `git checkout -- %s` rc=%d: %s",
                p,
                revert.returncode,
                revert.stderr.strip()[:200],
            )
    raise DevApplyOutOfScopeError(
        f"sub-agent mutated {len(out_of_scope)} path(s) outside {prefix}: "
        f"{out_of_scope[:10]!r}"
    )


# ---------------------------------------------------------------------------
# Validation gate (ruff + mypy --strict)
# ---------------------------------------------------------------------------


def _run_ruff(
    changed_py: list[Path],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Run ``ruff check`` against the changed ``.py`` files.

    Any diagnostic raises :class:`DevApplyValidationError` — the round
    is unusable because the sibling won't even import cleanly in some
    cases. This gate is a first-pass heuristic; revisit after first
    soak (Phase 9 Step 9 follow-up).
    """
    argv = ["uv", "run", "ruff", "check", *[str(p) for p in changed_py]]
    result = run(
        argv,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise DevApplyValidationError(
            f"ruff check failed on {len(changed_py)} file(s); "
            f"stdout: {result.stdout.strip()[:500]!r}"
        )


def _run_mypy(
    repo_root: Path,
    changed_py: list[Path],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Run ``mypy --strict`` from repo root against the changed ``.py`` files.

    Running from repo root so ``mypy.ini`` / ``pyproject.toml`` picks
    up the project-standard config (including strict-mode opt-ins).
    """
    argv = [
        "uv", "run", "mypy", "--strict", *[str(p) for p in changed_py]
    ]
    result = run(
        argv,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(repo_root),
    )
    if result.returncode != 0:
        raise DevApplyValidationError(
            f"mypy --strict failed on {len(changed_py)} file(s); "
            f"stdout: {result.stdout.strip()[:500]!r}"
        )
