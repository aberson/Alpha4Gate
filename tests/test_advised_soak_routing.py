"""Tests for the ``improve-bot-advised`` soak budget clamp + routing prose.

The soak *routing* (Phase 4.1/4.2 of ``.claude/skills/improve-bot-advised/
SKILL.md``) is markdown that an LLM follows, so it cannot be unit-run. What
*can* be made testable is the budget guard at its core — the clamp that bounds a
requested soak duration against the loop's remaining wall-clock. Step 5 of
Phase 7 extracted that arithmetic into :func:`orchestrator.staleness.
clamp_soak_hours` (single source of truth; SKILL.md §4.2 now calls it) so the
≤50%-of-budget rule, the 4h cap and the whole-hour floor are pinned by code.

This module holds three things:

* **Clamp math unit tests** — the real budget guard, exhaustively cased.
* **A doc-assertion regression guard** — asserts the SKILL.md soak wiring prose
  still exists (the best available signal that the un-unit-runnable routing was
  not silently deleted).
* **A real-DB smoke** — resolves ``bots/current/current.txt`` and, skip-if-
  absent, runs :func:`compute_staleness` against the real per-version
  ``training.db`` to catch sqlite/path/schema drift the synthetic tests can't.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from orchestrator.staleness import StalenessReport, clamp_soak_hours, compute_staleness


def _repo_root() -> Path:
    """Return the repository root (two parents above ``tests/``).

    This test file lives at ``<repo>/tests/test_advised_soak_routing.py``, so the
    repo root is two ``parent`` hops up. Resolved from ``__file__`` rather than
    cwd so the test is robust to the directory pytest is invoked from.
    """
    return Path(__file__).resolve().parent.parent


def _skill_md_path() -> Path:
    """Path to the improve-bot-advised SKILL.md, relative to the repo root."""
    return _repo_root() / ".claude" / "skills" / "improve-bot-advised" / "SKILL.md"


# ---------------------------------------------------------------------------
# Clamp math — the real budget guard (orchestrator.staleness.clamp_soak_hours)
# ---------------------------------------------------------------------------


def test_clamp_halved_when_request_exceeds_half_remaining() -> None:
    """req=8h, remaining=6h -> 3h (half of 6 wins over the 8h request)."""
    assert clamp_soak_hours(8.0, 21600) == 3


def test_clamp_request_honored_when_under_half_remaining() -> None:
    """req=2h, remaining=10h -> 2h (request is under half, honored as-is)."""
    assert clamp_soak_hours(2.0, 36000) == 2


def test_clamp_half_of_small_remaining() -> None:
    """req=4h, remaining=2h -> 1h (half of 2 = 1, below the 4h request)."""
    assert clamp_soak_hours(4.0, 7200) == 1


def test_clamp_hard_cap_at_four_hours() -> None:
    """req=10h, remaining=100h -> 4h (the hard 4h ceiling, not half=50h)."""
    assert clamp_soak_hours(10.0, 360000) == 4


def test_clamp_floors_not_rounds() -> None:
    """req=4h, remaining=3h -> half=1.5h must FLOOR to 1 (round would give 2).

    Pins the floor-not-round semantics the docstring/_SOAK_HOURS_HARD_CAP comment
    require; a `.5` fraction is the only input that distinguishes int() from round().
    """
    assert clamp_soak_hours(4.0, 10800) == 1


def test_clamp_floor_to_zero_is_the_skip_guard_case() -> None:
    """remaining=1h -> half is 0.5h -> floor 0 (the ``-lt 1`` skip-guard case)."""
    assert clamp_soak_hours(4.0, 3600) == 0


def test_clamp_never_negative_on_exhausted_budget() -> None:
    """Past the deadline (remaining <= 0) -> 0, never a negative duration."""
    assert clamp_soak_hours(4.0, 0) == 0
    assert clamp_soak_hours(4.0, -7200) == 0


def test_clamp_returns_int_for_bash_arithmetic() -> None:
    """The result must be a real ``int`` — §4.1 uses ``$((SOAK_HOURS * 3600))``.

    A float like ``2.0`` is a hard bash syntax error there, so the floor must
    produce a genuine integer, not a float that merely prints without a decimal.
    """
    result = clamp_soak_hours(2.0, 36000)
    assert isinstance(result, int)
    assert not isinstance(result, bool)


# ---------------------------------------------------------------------------
# Doc-assertion regression guard for the SKILL.md soak routing prose
# ---------------------------------------------------------------------------


def test_skill_md_soak_routing_prose_present() -> None:
    """The soak wiring in SKILL.md must not be silently deleted.

    The routing itself is markdown an LLM executes, so it can't be unit-run;
    asserting the load-bearing tokens still exist is the best regression signal.
    Checks: the soak improvement type, the hybrid decision-mode the soak runs in,
    the §4.2 call into the extracted clamp helper (single source of truth), and
    the ``SOAK_HOURS -lt 1`` skip guard that prevents a 0-hour backend teardown.
    """
    skill_md = _skill_md_path()
    assert skill_md.is_file(), f"SKILL.md not found at {skill_md}"
    text = skill_md.read_text(encoding="utf-8")

    assert '"type": "soak"' in text, "soak improvement type prose missing"
    assert "--decision-mode hybrid" in text, "hybrid soak decision-mode missing"
    assert "clamp_soak_hours" in text, "§4.2 must call the extracted clamp helper"
    # The skip guard: SOAK_HOURS compared with -lt 1 (tolerate quoting variants).
    assert "SOAK_HOURS" in text, "SOAK_HOURS variable missing from §4.2"
    assert "-lt 1" in text, 'the `[ "$SOAK_HOURS" -lt 1 ]` skip guard is missing'


def test_skill_md_has_no_inline_clamp_arithmetic() -> None:
    """§4.2 must route through the helper, not reinline the clamp arithmetic.

    Guards against a future edit re-introducing the ``min(req, rem/3600.0/2.0,
    4.0)`` inline ``python -c`` clamp (the very drift Step 5 removed): there must
    be exactly one source of truth, :func:`clamp_soak_hours`.
    """
    text = _skill_md_path().read_text(encoding="utf-8")
    assert "rem/3600.0/2.0" not in text, "inline clamp arithmetic resurfaced in SKILL.md"


# ---------------------------------------------------------------------------
# Real-DB smoke — catches sqlite/path/schema drift the synthetic tests can't
# ---------------------------------------------------------------------------


def test_compute_staleness_real_db_smoke() -> None:
    """Run ``compute_staleness`` against the real per-version ``training.db``.

    Resolves the active version from ``bots/current/current.txt`` and opens the
    real ``bots/<current>/data/training.db``. Skips cleanly when either the
    pointer or the DB is absent so CI without game data stays green. When the DB
    *is* present, the call must EITHER return a :class:`StalenessReport` OR raise
    :class:`ValueError` (too-few-games is an acceptable, documented outcome) —
    but must NOT leak an unexpected exception (``sqlite3.Error`` from a real
    schema/path drift, or a ``TypeError`` from a shape mismatch). This is the
    §15.5 smoke: the synthetic fixtures can't see drift between the module and
    the real on-disk schema/paths.
    """
    pointer = _repo_root() / "bots" / "current" / "current.txt"
    if not pointer.is_file():
        pytest.skip("bots/current/current.txt absent — no version to smoke")
    current = pointer.read_text(encoding="utf-8").strip()
    if not current:
        pytest.skip("bots/current/current.txt is empty — no version to smoke")

    db_path = _repo_root() / "bots" / current / "data" / "training.db"
    if not db_path.is_file():
        pytest.skip(f"real training.db absent for {current} at {db_path}")

    try:
        report = compute_staleness(current)
    except ValueError:
        # Acceptable: e.g. fewer than min_games rows in the real DB. The point of
        # the smoke is that the *path/schema* round-trip works, not that this
        # particular version happens to have enough games.
        return
    except (sqlite3.Error, TypeError) as exc:  # pragma: no cover - drift tripwire
        pytest.fail(
            f"compute_staleness({current!r}) leaked an unexpected {type(exc).__name__} "
            f"against the real DB at {db_path}: {exc}"
        )

    assert isinstance(report, StalenessReport)
