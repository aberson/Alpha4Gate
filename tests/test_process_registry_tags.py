"""Tests for the transitional dual-recognition logic in ``bots.v0.process_registry``.

During Phase 1 migration the registry must classify *all three* command-line
shapes as "ours":

* ``python -m bots.v0 --serve``          — post-Step-1.5 versioned module
* ``python -m bots.current --serve``     — post-Step-1.6 version-pointer
* ``python -m bots.v0.runner --serve`` — legacy, alive until Step 1.10

After Step 1.10 the ``bots.v0`` entry in ``_OUR_CMDLINE_TAGS`` is meant
to be retired. These tests pin the current behavior so that retirement is
a visible, deliberate change.

Step 4 of the evolve-parallelization plan extends the operational
versions (``bots.v0``, ``bots.v3``, ``bots.v4``) with a
``bots.cand_<uuid>`` substring match so parallel-evolve worker SC2
children are recognized in the WSL processes panel. Per-version test
classes below pin v3 (the version named in the plan) and v4
(production runtime per ``bots/current``); v0 cand-prefix recognition
is exercised indirectly via the ``_CASES`` parametrization since the
cand prefix is structurally a strict superset of the v0/v3/v4 tag
match (any cand-prefix cmdline is also an "ours" match).
"""

from __future__ import annotations

import pytest
from bots.v0.process_registry import _classify_process, _is_ours

_CASES: tuple[tuple[str, bool], ...] = (
    ("python -m bots.v0 --serve", True),
    ("python -m bots.v0.runner --serve", True),
    ("python -m bots.current --serve", True),
    ("node vite", False),
    ("uv run mypy", False),
)


@pytest.mark.parametrize("cmdline,expected", _CASES)
def test_is_ours(cmdline: str, expected: bool) -> None:
    """``_is_ours`` must recognise every migration-window shape."""
    assert _is_ours(cmdline.lower()) is expected


@pytest.mark.parametrize("cmdline,expected_is_ours", _CASES)
def test_classify_process_recognises_all_migration_tags(
    cmdline: str, expected_is_ours: bool
) -> None:
    """``_classify_process`` must label all three "ours" cmdline shapes as
    one of the backend/game/runner roles (not ``unknown``), and must
    leave non-ours processes alone."""
    # Derive a plausible process name from the cmdline so the non-ours
    # branch (node / uv) hits its real classification path.
    if "node" in cmdline:
        name, pid_to_name = "node", {}
    elif cmdline.startswith("uv"):
        name, pid_to_name = "uv", {}
    else:
        name = "python"
        # Parent=python mimics the uvicorn-worker parentage used by
        # _classify_process to distinguish backend-server from -runner.
        pid_to_name = {99: "python"}

    role = _classify_process(
        name=name,
        cmdline=cmdline,
        pid=1,
        parent_pid=99,
        pid_to_name=pid_to_name,
    )

    ours_roles = {"backend-server", "backend-runner", "backend-wrapper",
                  "game-runner", "runner"}
    if expected_is_ours:
        assert role in ours_roles, f"{cmdline!r} -> {role} (expected ours role)"
    else:
        assert role not in ours_roles, (
            f"{cmdline!r} -> {role} (expected non-ours)"
        )


# ---------------------------------------------------------------------------
# Step 4: bots.v3 cand-prefix recognition for parallel-evolve workers
# ---------------------------------------------------------------------------


class TestProcessRegistryV3CandPrefix:
    """The parallel-evolve dispatcher launches worker SC2 children with
    ``--bot bots.cand_<uuid>`` in their argv. Without cand-prefix
    recognition they appear as "Other" / "unknown" in the WSL processes
    panel during a parallel run. Pin the recognition + label-resolution
    behavior here so a future refactor can't silently regress it.
    """

    def test_is_ours_recognises_cand_prefix(self) -> None:
        from bots.v3.process_registry import _is_ours as v3_is_ours

        assert v3_is_ours(
            "python -m bots.cand_a1b2c3d4 --role solo --map simple64"
        ) is True

    def test_is_ours_still_recognises_explicit_versions(self) -> None:
        from bots.v3.process_registry import _is_ours as v3_is_ours

        assert v3_is_ours("python -m bots.v3 --serve") is True
        assert v3_is_ours("python -m bots.current --serve") is True

    def test_is_ours_rejects_unrelated_cmdline(self) -> None:
        from bots.v3.process_registry import _is_ours as v3_is_ours

        # ``cand_`` alone (no ``bots.`` prefix) shouldn't match — that's a
        # common substring.
        assert v3_is_ours("python -m other.cand_abc --foo") is False
        assert v3_is_ours("uv run pytest") is False

    def test_summarize_cmdline_returns_cand_prefix_verbatim(self) -> None:
        from bots.v3.process_registry import (
            _summarize_cmdline as v3_summarize,
        )

        cmdline = "python -m bots.cand_a1b2c3d4 --role solo --map Simple64"
        out = v3_summarize(cmdline)
        # Label is the cand prefix (lower-cased, since _is_ours operates
        # on the lower-cased cmdline; the regex extraction reads from the
        # same lower-cased string).
        assert out.startswith("bots.cand_a1b2c3d4")
        # Flags are preserved.
        assert "--role" in out
        assert "--map" in out
        # The default "bots.v3" fallback DOES NOT appear in front of the
        # cand prefix (the cand prefix wins).
        assert not out.startswith("bots.v3")

    def test_summarize_cmdline_falls_back_to_explicit_version(self) -> None:
        """When no cand prefix is present, fall back to the matching
        explicit-version tag (regression guard)."""
        from bots.v3.process_registry import (
            _summarize_cmdline as v3_summarize,
        )

        out = v3_summarize("python -m bots.v3 --serve --port 8765")
        assert out.startswith("bots.v3")

        out = v3_summarize("python -m bots.current --serve --port 8765")
        assert out.startswith("bots.current")

    def test_classify_process_buckets_cand_as_runner(self) -> None:
        """Cand-prefix cmdlines that aren't ``--serve``/``--batch`` land
        in the generic "runner" bucket — same as any other ``bots.*``
        runtime invocation."""
        from bots.v3.process_registry import (
            _classify_process as v3_classify,
        )

        role = v3_classify(
            name="python",
            cmdline="python -m bots.cand_a1b2c3d4 --role solo --map Simple64",
            pid=1,
            parent_pid=99,
            pid_to_name={99: "python"},
        )
        assert role == "runner"


# ---------------------------------------------------------------------------
# Step 4 review: v4 is the production runtime per ``bots/current`` -> v4.
# Mirror the v3 cand-prefix tests against v4 so a future drift between the
# two operational versions is caught. v3 tests above remain intact as
# regression guards (they cover the version named in the plan).
# ---------------------------------------------------------------------------


class TestProcessRegistryV4CandPrefix:
    """Same six invariants as ``TestProcessRegistryV3CandPrefix`` but
    targeting ``bots.v4.process_registry`` (production runtime)."""

    def test_is_ours_recognises_cand_prefix(self) -> None:
        from bots.v4.process_registry import _is_ours as v4_is_ours

        assert v4_is_ours(
            "python -m bots.cand_a1b2c3d4 --role solo --map simple64"
        ) is True

    def test_is_ours_still_recognises_explicit_versions(self) -> None:
        from bots.v4.process_registry import _is_ours as v4_is_ours

        assert v4_is_ours("python -m bots.v4 --serve") is True
        assert v4_is_ours("python -m bots.current --serve") is True

    def test_is_ours_rejects_unrelated_cmdline(self) -> None:
        from bots.v4.process_registry import _is_ours as v4_is_ours

        # ``cand_`` alone (no ``bots.`` prefix) shouldn't match — that's a
        # common substring.
        assert v4_is_ours("python -m other.cand_abc --foo") is False
        assert v4_is_ours("uv run pytest") is False

    def test_summarize_cmdline_returns_cand_prefix_verbatim(self) -> None:
        from bots.v4.process_registry import (
            _summarize_cmdline as v4_summarize,
        )

        cmdline = "python -m bots.cand_a1b2c3d4 --role solo --map Simple64"
        out = v4_summarize(cmdline)
        # Label is the cand prefix (lower-cased, since _is_ours operates
        # on the lower-cased cmdline; the regex extraction reads from the
        # same lower-cased string).
        assert out.startswith("bots.cand_a1b2c3d4")
        # Flags are preserved.
        assert "--role" in out
        assert "--map" in out
        # The default "bots.v4" fallback DOES NOT appear in front of the
        # cand prefix (the cand prefix wins).
        assert not out.startswith("bots.v4")

    def test_summarize_cmdline_falls_back_to_explicit_version(self) -> None:
        """When no cand prefix is present, fall back to the matching
        explicit-version tag (regression guard)."""
        from bots.v4.process_registry import (
            _summarize_cmdline as v4_summarize,
        )

        out = v4_summarize("python -m bots.v4 --serve --port 8765")
        assert out.startswith("bots.v4")

        out = v4_summarize("python -m bots.current --serve --port 8765")
        assert out.startswith("bots.current")

    def test_classify_process_buckets_cand_as_runner(self) -> None:
        """Cand-prefix cmdlines that aren't ``--serve``/``--batch`` land
        in the generic "runner" bucket — same as any other ``bots.*``
        runtime invocation."""
        from bots.v4.process_registry import (
            _classify_process as v4_classify,
        )

        role = v4_classify(
            name="python",
            cmdline="python -m bots.cand_a1b2c3d4 --role solo --map Simple64",
            pid=1,
            parent_pid=99,
            pid_to_name={99: "python"},
        )
        assert role == "runner"
