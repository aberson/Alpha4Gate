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
import re
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
    """Post-edit ruff or mypy gate failed; the round is unusable.

    Carries the raw validator stdout as ``self.detail`` so the retry
    loop in :func:`spawn_dev_subagent` can feed it back to the sub-agent
    verbatim — a 500-char summary in the error message isn't enough for
    the model to self-correct a multi-line mypy trace.
    """

    def __init__(self, msg: str, *, detail: str = "") -> None:
        super().__init__(msg)
        self.detail = detail


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
4. You have Read, Edit, Write, Grep, Glob, AND a narrowly-scoped Bash
   tool — only `mypy`, `ruff`, and `uv` commands are pre-approved. Use
   Bash ONLY to validate your own edits against the project's gates:

       uv run ruff check <files-you-changed>
       uv run mypy --strict <files-you-changed>

   The project runs under `mypy --strict`, so every function/variable
   must be fully typed, no implicit `Any`, and numeric-to-int casts
   must be explicit (e.g. `range(int(x))` not `range(x)` when `x` is
   a `float`). Common mypy-strict pitfalls to avoid:
     - `range()`, slicing, and indexing require `int`, not `float`.
     - `sum()` and arithmetic over mixed int/float may need an explicit
       cast on the result.
     - Returning from a function annotated `-> None` with an expression
       is an error.
     - `dict.get(k)` returns `Optional[V]` — check `is not None` before
       using.
     - Adding new public attributes on frozen dataclasses / NamedTuples
       won't type-check — pass them in or add them via a subclass.
     - New callables need type annotations on all parameters AND return.

   If your initial edit fails either validator, read the error output,
   fix the specific line it points at, and re-run. Iterate up to a few
   times if needed. Do NOT disable or suppress a check (no `# type:
   ignore`, no `# noqa`) — those cost the round.

5. When the change is complete AND both `ruff check` and
   `mypy --strict` exit 0 on the files you changed, produce a one-line
   summary of the files you modified. No prose before or after.

Scoping is enforced externally: the caller will revert any out-of-scope
edit and discard the round. The caller ALSO re-runs ruff + mypy after
you exit — if either fails there (e.g. because you skipped validation)
the round is consumed-tie and your work is wasted. Better to spend the
extra minute validating.
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

IMPORTANT: any file path you see above is a path relative to your CWD.
Use bare filenames or `./X` to address them. NEVER edit any file under
`bots/` outside your CWD; reading or editing a sibling version's files
(e.g. a path like ``bots/vN/foo.py`` while your CWD is a different
``bots/...`` snapshot) is a sandbox violation that will cause the round
to be discarded.
"""


# Regex used to strip parent-version path prefixes (``bots/vN/``) from
# advisor-emitted imp text. Without this, a sub-agent given an imp like
# "In bots/v3/foo.py..." resolves the path against the repo root and
# edits the parent (caught by ``_assert_scope`` only after a wasted
# attempt). Stripping ``bots/v3/`` leaves the bare filename, which the
# sub-agent's cwd-rooted Edit tool resolves correctly.
_PARENT_PATH_RE = re.compile(r"bots/v\d+/")


def _sanitize_imp_paths(text: str) -> str:
    return _PARENT_PATH_RE.sub("", text)


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
    max_attempts: int = 3,
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
        safer than letting the soak hang. Applies to each attempt
        individually — the retry loop can consume up to
        ``timeout * max_attempts`` wall-clock in the worst case.
    validate:
        If True (default), runs ruff + mypy --strict on the changed
        ``.py`` files after the sub-agent exits. Set False in tests that
        only want to exercise the spawn path.
    max_attempts:
        Total number of sub-agent invocations allowed (initial + retries).
        Default 3: if the first attempt's edit fails ruff or mypy, we
        re-invoke the sub-agent with the validator error appended to the
        prompt, up to two more times. A :class:`DevApplyOutOfScopeError`
        is NOT retried — that's a safety violation, not a type error.
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
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    # SCOPE CHECK baseline uses git status: captures tracked-file edits
    # AND new untracked files anywhere in the repo. It intentionally does
    # NOT see file-level edits inside the already-untracked candidate dir
    # (git collapses that dir), which is fine — those are all in-scope by
    # construction. `py_before` (see `_collect_candidate_py_snapshot`) is
    # what detects those in-dir edits for the validation target list.
    #
    # We take the baseline ONCE before the retry loop — every attempt's
    # scope check compares back to the same pre-dev-apply state, so a
    # previous-attempt edit that's been kept doesn't trip out-of-scope.
    git_before = _snapshot_repo_state(repo_root, run=run)
    py_before = _collect_candidate_py_snapshot(version_dir)

    last_validation_error: DevApplyValidationError | None = None
    feedback: str | None = None

    for attempt in range(1, max_attempts + 1):
        _invoke_subagent(
            version_dir,
            imp,
            model=model,
            timeout=timeout,
            run=run,
            feedback=feedback,
        )

        git_after = _snapshot_repo_state(repo_root, run=run)
        py_after = _collect_candidate_py_snapshot(version_dir)

        repo_changed = sorted(git_after - git_before)
        _log.info(
            "dev-apply: sub-agent attempt %d/%d exited; "
            "%d repo-wide path(s) changed",
            attempt,
            max_attempts,
            len(repo_changed),
        )
        # Out-of-scope is terminal — don't retry a rule violation.
        _assert_scope(repo_root, version_dir, repo_changed, run=run)

        changed_py = sorted(_diff_py_snapshots(py_before, py_after))
        _log.info(
            "dev-apply: attempt %d/%d — %d .py file(s) changed under "
            "candidate dir",
            attempt,
            max_attempts,
            len(changed_py),
        )

        if not validate or not changed_py:
            # Nothing to validate — either the caller disabled gating
            # (tests) or the sub-agent made no .py edits (trivial imp or
            # bailed). Treat as success; the round will play as-is.
            return

        try:
            _run_ruff(changed_py, run=run)
            _run_mypy(repo_root, changed_py, run=run)
            if attempt > 1:
                _log.info(
                    "dev-apply: validation passed on retry attempt %d/%d",
                    attempt,
                    max_attempts,
                )
            return
        except DevApplyValidationError as exc:
            last_validation_error = exc
            if attempt == max_attempts:
                # Out of retries — let the final error propagate.
                break
            _log.warning(
                "dev-apply: validation failed on attempt %d/%d; "
                "retrying with feedback (%s)",
                attempt,
                max_attempts,
                str(exc).splitlines()[0][:200],
            )
            feedback = _format_retry_feedback(
                exc.detail or str(exc), attempt + 1, max_attempts
            )

    # All retries exhausted.
    assert last_validation_error is not None, (
        "retry loop exited without a validation error — this is a bug"
    )
    raise last_validation_error


# ---------------------------------------------------------------------------
# Sub-agent invocation
# ---------------------------------------------------------------------------


# Bash commands the sub-agent may run for self-validation. Mypy/ruff
# cover the gates the external caller re-runs; `uv` covers the project's
# canonical invocation (`uv run mypy`, `uv run ruff`); `python` covers
# fallback invocations (`python -m mypy`). Scope is still restricted by
# `--add-dir` + the external out-of-scope check, so a rogue Bash call
# can't escape the candidate dir — but widening beyond this list would
# weaken the defence-in-depth posture.
_SUBAGENT_ALLOWED_BASH = (
    "Bash(mypy:*) Bash(ruff:*) Bash(uv:*) Bash(python:*)"
)


def _format_retry_feedback(detail: str, attempt: int, max_attempts: int) -> str:
    """Build the retry-prompt addendum shown to the sub-agent on attempt 2+.

    The detail string is the raw stdout from whichever validator failed
    (ruff or mypy); it can be multi-line and multi-KB. We cap it to stay
    well under Windows' argv + stdin limits while preserving enough of
    the top of the output to be actionable.
    """
    truncated = detail.strip()
    if len(truncated) > 4000:
        truncated = truncated[:4000] + "\n... [truncated]"
    return (
        f"YOUR PREVIOUS ATTEMPT FAILED VALIDATION (attempt {attempt - 1} of "
        f"{max_attempts}). The validator output was:\n\n"
        f"```\n{truncated}\n```\n\n"
        "Fix ONLY the errors above. Keep the rest of your earlier edit. "
        "Re-run the validator via Bash to confirm before exiting. If you "
        "cannot fix the errors without violating the rules, exit without "
        "further edits — the round will be discarded cleanly."
    )


def _invoke_subagent(
    version_dir: Path,
    imp: Improvement,
    *,
    model: str,
    timeout: float,
    run: Callable[..., subprocess.CompletedProcess[str]],
    feedback: str | None = None,
) -> None:
    """Shell out to ``claude -p`` and let the sub-agent do the edits.

    The prompt is piped via stdin (same lesson as commit 533a02a — Windows
    CreateProcess rejects cmdlines > ~32 KiB, and long concrete_change
    descriptions can approach that).

    *feedback* is prepended to the user prompt on retry attempts — the
    retry loop in :func:`spawn_dev_subagent` builds it from the failed
    validator's stdout so the sub-agent has the exact error message to
    self-correct from.
    """
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        title=imp.title,
        rank=imp.rank,
        expected_impact=imp.expected_impact,
        description=_sanitize_imp_paths(imp.description),
        concrete_change=_sanitize_imp_paths(imp.concrete_change),
    )
    if feedback:
        user_prompt = f"{feedback}\n\n---\n\n{user_prompt}"

    argv = [
        "claude",
        "-p",
        "--model", model,
        # Bash joins the tool set so the sub-agent can self-validate with
        # ruff + mypy before exiting. The `--allowed-tools` allowlist
        # below scopes which Bash commands are pre-approved, so
        # permission-mode=acceptEdits doesn't have to prompt for them.
        "--tools", "Read,Edit,Write,Grep,Glob,Bash",
        "--allowed-tools", _SUBAGENT_ALLOWED_BASH,
        "--permission-mode", "acceptEdits",
        "--add-dir", str(version_dir),
        "--output-format", "text",
        "--no-session-persistence",
        "--append-system-prompt", _SYSTEM_PROMPT,
    ]

    _log.info(
        "dev-apply: spawning sub-agent (model=%s, cwd=%s, timeout=%.0fs, "
        "imp=%r, retry=%s)",
        model,
        version_dir,
        timeout,
        imp.title,
        "yes" if feedback else "no",
    )

    try:
        result = run(
            argv,
            input=user_prompt,
            capture_output=True,
            text=True,
            # Force UTF-8 — Windows defaults to cp1252 which can't encode
            # non-ASCII chars common in advisor-generated imp text
            # (em-dashes, arrows, smart quotes). Without this the stdin
            # writer thread raises UnicodeEncodeError before the
            # sub-agent receives the prompt.
            encoding="utf-8",
            errors="replace",
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
            f"stdout: {result.stdout.strip()[:500]!r}",
            detail=f"# ruff check\n{result.stdout}\n{result.stderr}".strip(),
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
            f"stdout: {result.stdout.strip()[:500]!r}",
            detail=f"# mypy --strict\n{result.stdout}\n{result.stderr}".strip(),
        )
