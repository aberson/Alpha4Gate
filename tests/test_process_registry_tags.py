"""Tests for the transitional dual-recognition logic in ``bots.v0.process_registry``.

During Phase 1 migration the registry must classify *all three* command-line
shapes as "ours":

* ``python -m bots.v0 --serve``          — post-Step-1.5 versioned module
* ``python -m bots.current --serve``     — post-Step-1.6 version-pointer
* ``python -m alpha4gate.runner --serve`` — legacy, alive until Step 1.10

After Step 1.10 the ``alpha4gate`` entry in ``_OUR_CMDLINE_TAGS`` is meant
to be retired. These tests pin the current behavior so that retirement is
a visible, deliberate change.
"""

from __future__ import annotations

import pytest
from bots.v0.process_registry import _classify_process, _is_ours

_CASES: tuple[tuple[str, bool], ...] = (
    ("python -m bots.v0 --serve", True),
    ("python -m alpha4gate.runner --serve", True),
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
