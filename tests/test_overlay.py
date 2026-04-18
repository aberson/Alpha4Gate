"""Tests for ``selfplay_viewer.overlay`` layout constants.

Pure-Python — does not import pygame. Verifies that the four
``(bar, size)`` keys produce the exact dimensions specified in
``documentation/plans/selfplay-viewer-plan.md`` (Section 5).
"""

from __future__ import annotations

import pytest

from selfplay_viewer.overlay import (
    CONTAINER_SIZES,
    OVERLAY_RECTS,
    PANE_RECTS,
)

# Authoritative layout table from the plan. Mirrored here so the tests
# are independent of any helper that derives them — every entry is
# spelled out so a regression on one doesn't pass silently.
_EXPECTED: dict[
    tuple[str, str],
    tuple[
        tuple[int, int],
        tuple[tuple[int, int, int, int], tuple[int, int, int, int]],
        tuple[int, int, int, int],
    ],
] = {
    ("top", "large"): (
        (2188, 948),
        ((40, 140, 1024, 768), (1124, 140, 1024, 768)),
        (0, 0, 2188, 100),
    ),
    ("top", "small"): (
        (2060, 900),
        ((40, 140, 960, 720), (1060, 140, 960, 720)),
        (0, 0, 2060, 100),
    ),
    ("side", "large"): (
        (2468, 848),
        ((40, 40, 1024, 768), (1124, 40, 1024, 768)),
        (2188, 0, 280, 848),
    ),
    ("side", "small"): (
        (2340, 800),
        ((40, 40, 960, 720), (1060, 40, 960, 720)),
        (2060, 0, 280, 800),
    ),
}


@pytest.mark.parametrize(
    ("bar", "size"),
    list(_EXPECTED.keys()),
    ids=[f"{b}-{s}" for b, s in _EXPECTED],
)
def test_container_size_matches_plan(bar: str, size: str) -> None:
    expected_size, _, _ = _EXPECTED[(bar, size)]
    assert CONTAINER_SIZES[(bar, size)] == expected_size


@pytest.mark.parametrize(
    ("bar", "size"),
    list(_EXPECTED.keys()),
    ids=[f"{b}-{s}" for b, s in _EXPECTED],
)
def test_pane_rects_match_plan(bar: str, size: str) -> None:
    _, expected_panes, _ = _EXPECTED[(bar, size)]
    actual = PANE_RECTS[(bar, size)]
    assert isinstance(actual, tuple)
    assert len(actual) == 2
    assert actual == expected_panes


@pytest.mark.parametrize(
    ("bar", "size"),
    list(_EXPECTED.keys()),
    ids=[f"{b}-{s}" for b, s in _EXPECTED],
)
def test_overlay_rect_matches_plan(bar: str, size: str) -> None:
    _, _, expected_overlay = _EXPECTED[(bar, size)]
    assert OVERLAY_RECTS[(bar, size)] == expected_overlay
