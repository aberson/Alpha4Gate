"""Post-promotion hooks — invoked after every promoted version.

Models tab Step 2 deliverable. Three call sites wire this helper:

* ``scripts/evolve.py`` — after ``git_commit_evo_auto`` succeeds.
* ``scripts/snapshot_bot.py`` — after the snapshot completes.
* ``.claude/skills/improve-bot-advised/SKILL.md`` — after each
  iteration commit (manually invoked via ``python -c "..."``).

The helper rebuilds ``data/lineage.json`` and (eventually) refreshes
``data/weight_dynamics.jsonl``. Both run as subprocesses so a crash
inside either script can never corrupt the caller's process.

**Crucial contract:** this helper MUST NOT raise on subprocess failure.
The promotion path already succeeded — failing the post-hook would
falsely roll the caller back. Every failure logs a warning and
returns ``None``. The only path that raises is malformed input (an
invalid version string), and that is rejected BEFORE any subprocess
runs (defense in depth — the validated string is never interpolated
into a shell command, but a bad version arg is still caller-side bug).
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

_VERSION_RE: re.Pattern[str] = re.compile(r"^v\d+$")

# Bounded subprocess wall-clock — both helper scripts are pure-Python
# walks of the data dir + a single JSON write; 60s is several orders of
# magnitude more than we expect them to need but stays well below the
# evolve step's per-generation budget.
_SUBPROCESS_TIMEOUT_S = 60.0


def _repo_root() -> Path:
    """Resolve the repo root from this module's location.

    This module lives three levels under the repo root:
    ``learning`` → ``bots/<version>`` → ``bots`` → repo root.
    """
    return Path(__file__).resolve().parent.parent.parent.parent


def _validate_version(version: str) -> str:
    """Reject version strings that don't match ``^v\\d+$``.

    Raises :class:`ValueError` (NOT ``HTTPException`` — this helper is
    not an HTTP boundary; the api.py validator that does raise
    ``HTTPException`` is appropriate THERE, not here). The validated
    string is the only piece of caller-controlled input that reaches
    the subprocess argv list, so the regex check is a defense-in-depth
    layer on top of ``shell=False`` argv passing — even with shell
    escaping disabled, we'd rather refuse a malformed version
    altogether than rely on the OS to interpret it correctly.
    """
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise ValueError(f"Invalid version: {version!r}")
    return version


def _run_subprocess(args: list[str], *, label: str) -> None:
    """Run a subprocess and swallow every failure mode as a warning.

    Catches every exception class the subprocess module can raise —
    ``CalledProcessError`` on non-zero exit, ``TimeoutExpired`` on the
    bounded wall-clock cap, and ``OSError`` on missing-binary /
    permission-denied / executable-not-found. Logs a warning and
    returns; never raises.
    """
    try:
        result = subprocess.run(  # noqa: S603 — list-form, shell=False
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _log.warning(
            "post-promotion hook: %s timed out after %.0fs (args=%r)",
            label,
            _SUBPROCESS_TIMEOUT_S,
            args,
        )
        return
    except OSError as exc:
        _log.warning(
            "post-promotion hook: %s failed to launch: %s (args=%r)",
            label,
            exc,
            args,
        )
        return

    if result.returncode != 0:
        # Capture both streams in the warning so a sysadmin doesn't have
        # to re-run with stdout capture to see what went wrong.
        _log.warning(
            "post-promotion hook: %s exited with code %d "
            "(stdout=%r stderr=%r args=%r)",
            label,
            result.returncode,
            (result.stdout or "")[:500],
            (result.stderr or "")[:500],
            args,
        )


def run_post_promotion_hooks(version: str) -> None:
    """Rebuild lineage + weight dynamics for the just-promoted ``version``.

    Args:
        version: The promoted version name. Must match ``^v\\d+$``.

    Raises:
        ValueError: When ``version`` is malformed. Subprocesses are
            NOT invoked in this case — the bad input is rejected
            before any side effects.

    Returns:
        ``None`` on success or partial failure. Subprocess failures
        log warnings but never propagate; promotion paths must never
        be blocked by a hook crash.
    """
    version = _validate_version(version)

    repo_root = _repo_root()
    py = sys.executable

    # 1. Rebuild lineage.json. Step 2 deliverable; always runs.
    build_lineage = repo_root / "scripts" / "build_lineage.py"
    if build_lineage.is_file():
        _run_subprocess(
            [py, str(build_lineage)],
            label="build_lineage",
        )
    else:
        # Should never happen post-Step-2, but fail-safe so a partial
        # checkout doesn't break promotion.
        _log.warning(
            "post-promotion hook: build_lineage.py missing at %s — skipping",
            build_lineage,
        )

    # 2. Refresh weight dynamics for ``version``. Step 9 deliverable;
    #    the script doesn't exist yet. Detect-and-skip with a warning
    #    so this hook doesn't 'discover' the file the moment Step 9
    #    lands — we want Step 9 to wire it deliberately.
    compute_weight_dynamics = (
        repo_root / "scripts" / "compute_weight_dynamics.py"
    )
    if compute_weight_dynamics.is_file():
        _run_subprocess(
            [py, str(compute_weight_dynamics), "--version", version],
            label="compute_weight_dynamics",
        )
    else:
        _log.info(
            "post-promotion hook: compute_weight_dynamics.py not present "
            "(Step 9 deliverable); skipping weight-dynamics refresh for %s",
            version,
        )

    return None
