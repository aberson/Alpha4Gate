"""Layout constants for the self-play viewer.

Pure-Python module — does NOT import pygame. The four ``(bar, size)``
combinations exhaustively define the container window dimensions, the
two SC2 pane rectangles, and the overlay (stats bar) rectangle.

These numbers are authoritative per the plan in
``documentation/plans/selfplay-viewer-plan.md`` (Section 5, layout
table). Tuning is a one-line edit here; ``container.py`` and tests both
read from these constants — no magic values elsewhere.

A future step (Step 5) will add the rendering helpers that paint text
into the overlay rectangle. Right now this module is layout-only.
"""

from __future__ import annotations

from typing import Literal

#: A pygame-style rectangle: ``(x, y, width, height)`` in container pixels.
Rect = tuple[int, int, int, int]

#: Allowed bar placements.
Bar = Literal["top", "side"]

#: Allowed SC2-pane size presets.
Size = Literal["large", "small"]

#: Container window dimensions ``(width, height)`` keyed by ``(bar, size)``.
CONTAINER_SIZES: dict[tuple[str, str], tuple[int, int]] = {
    ("top", "large"): (2188, 948),
    ("top", "small"): (2060, 900),
    ("side", "large"): (2468, 848),
    ("side", "small"): (2340, 800),
}

#: Two SC2 pane rectangles ``(p1, p2)`` keyed by ``(bar, size)``.
PANE_RECTS: dict[tuple[str, str], tuple[Rect, Rect]] = {
    ("top", "large"): (
        (40, 140, 1024, 768),
        (1124, 140, 1024, 768),
    ),
    ("top", "small"): (
        (40, 140, 960, 720),
        (1060, 140, 960, 720),
    ),
    ("side", "large"): (
        (40, 40, 1024, 768),
        (1124, 40, 1024, 768),
    ),
    ("side", "small"): (
        (40, 40, 960, 720),
        (1060, 40, 960, 720),
    ),
}

#: Overlay (stats-bar) rectangle keyed by ``(bar, size)``.
OVERLAY_RECTS: dict[tuple[str, str], Rect] = {
    ("top", "large"): (0, 0, 2188, 100),
    ("top", "small"): (0, 0, 2060, 100),
    ("side", "large"): (2188, 0, 280, 848),
    ("side", "small"): (2060, 0, 280, 800),
}

__all__ = [
    "Bar",
    "CONTAINER_SIZES",
    "OVERLAY_RECTS",
    "PANE_RECTS",
    "Rect",
    "Size",
]
