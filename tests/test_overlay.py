"""Tests for ``selfplay_viewer.overlay`` layout + rendering.

The layout-constant tests are pure Python — they exercise ONLY the
``CONTAINER_SIZES`` / ``PANE_RECTS`` / ``OVERLAY_RECTS`` tables and
never touch pygame. The rendering tests (added in Step 5) import
pygame lazily via ``pytest.importorskip`` so this module still
collects cleanly on Linux CI without the ``[viewer]`` extra.

Authoritative layout numbers come from
``documentation/plans/selfplay-viewer-plan.md`` (Section 5).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest

from selfplay_viewer.overlay import (
    CONTAINER_SIZES,
    OVERLAY_RECTS,
    PANE_RECTS,
)

if TYPE_CHECKING:
    import pygame

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


# ---------------------------------------------------------------------------
# Rendering tests — gated behind pygame importorskip so Linux CI still
# collects the layout tests cleanly.
# ---------------------------------------------------------------------------

# pygame is required by the rendering tests only — NOT by the layout
# tests above or the state-machine tests below. We bind it at module
# level when available so the helper type annotations resolve, but we
# do NOT importorskip here because that would skip the whole module
# (including the pure-Python layout tests) on a venv without the
# viewer extra. Rendering tests depend on the ``pygame_init`` fixture
# which does its own importorskip.
try:
    import pygame  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — Linux / Py3.14 without viewer extras
    pygame = None  # type: ignore[assignment]

# Sentinel background colour: pure green. Our overlay only ever paints
# white text, white border, or semi-transparent dark fill, so an RGB of
# (0, 255, 0) is impossible to produce from render_overlay. We fill the
# entire surface with it pre-render, then pixel-diff afterwards.
_SENTINEL_COLOR: tuple[int, int, int] = (0, 255, 0)


@pytest.fixture()
def pygame_init() -> Iterator[None]:
    """Initialise pygame for rendering tests and tear down cleanly.

    ``pytest.importorskip`` at module scope already guards collection
    on Linux. We re-check defensively here so a future module-level
    refactor that drops the top-of-file guard fails loudly.

    The ``reset_font_cache`` call after ``pygame.quit()`` is mandatory
    — the overlay module caches ``Font`` handles to amortise the
    ~256us-per-open cost across the pygame main loop, but those
    handles point into SDL_TTF state that ``pygame.quit`` frees.
    Reusing a cached handle from a prior pygame session SIGSEGVs the
    interpreter (no Python exception). Tests that share the cache
    across init/quit cycles MUST drop it explicitly.
    """
    pytest.importorskip(
        "pygame", reason="viewer extra (pygame) not installed"
    )
    from selfplay_viewer.overlay import reset_font_cache

    pygame.init()
    try:
        yield
    finally:
        pygame.quit()
        reset_font_cache()


def _count_non_sentinel_pixels(
    surface: pygame.Surface,
    rect: tuple[int, int, int, int],
) -> int:
    """Count pixels in *rect* whose RGB is NOT the sentinel colour.

    Uses ``get_at`` in a tight loop. Surfaces at container resolution
    (roughly 2M pixels) are expensive to walk pixel-by-pixel, so we
    only ever scan within the overlay rect (worst case ~220k pixels =
    fast in native SDL).
    """
    x0, y0, w, h = rect
    count = 0
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            px = surface.get_at((x, y))
            # Compare RGB; alpha may differ due to SRCALPHA blending.
            if (px.r, px.g, px.b) != _SENTINEL_COLOR:
                count += 1
    return count


def _any_sentinel_pixel_outside(
    surface: pygame.Surface,
    overlay_rect: tuple[int, int, int, int],
    container_size: tuple[int, int],
) -> bool:
    """Check that the corner pixels OUTSIDE the overlay rect stayed sentinel.

    We don't walk the whole container (millions of pixels = slow). We
    sample the four corners + midpoints of each edge of the container
    that lie outside the overlay rect. If ANY of them is still
    sentinel-coloured we consider the out-of-bounds region untouched.

    Returns True when at least one sampled pixel is still sentinel
    (i.e. the overlay did NOT stomp on that coordinate).
    """
    cw, ch = container_size
    ox, oy, ow, oh = overlay_rect
    candidates = [
        (0, 0),
        (cw - 1, 0),
        (0, ch - 1),
        (cw - 1, ch - 1),
        (cw // 2, ch - 1),
        (0, ch // 2),
        (cw - 1, ch // 2),
    ]
    for x, y in candidates:
        inside_x = ox <= x < ox + ow
        inside_y = oy <= y < oy + oh
        if inside_x and inside_y:
            continue  # inside the overlay — don't sample
        px = surface.get_at((x, y))
        if (px.r, px.g, px.b) == _SENTINEL_COLOR:
            return True
    return False


def _count_white(
    surf: pygame.Surface, rect: tuple[int, int, int, int]
) -> int:
    """Count white-ish pixels (all RGB channels > 200) in *rect*.

    Promoted from a per-test closure to module scope so the
    same-version overlay test below can share the same threshold.
    """
    x0, y0, w, h = rect
    n = 0
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            p = surf.get_at((x, y))
            if p.r > 200 and p.g > 200 and p.b > 200:
                n += 1
    return n


@pytest.mark.parametrize(
    ("bar", "size"),
    list(_EXPECTED.keys()),
    ids=[f"{b}-{s}" for b, s in _EXPECTED],
)
def test_render_overlay_paints_within_rect(
    bar: str, size: str, pygame_init: None
) -> None:
    """For each (bar, size) the overlay paints text INSIDE its rect only.

    Strategy: pre-fill the whole surface with the sentinel green colour.
    Call render_overlay with mock args. Assert the overlay rect has
    non-sentinel pixels (text + fill were drawn) AND at least one
    coordinate OUTSIDE the rect is still sentinel (we stayed in bounds).
    """
    from selfplay_viewer.overlay import render_overlay

    container_size = CONTAINER_SIZES[(bar, size)]
    overlay_rect = OVERLAY_RECTS[(bar, size)]

    surface = pygame.Surface(container_size)
    surface.fill(_SENTINEL_COLOR)

    render_overlay(
        surface=surface,
        bar=bar,
        size=size,
        p1_label="v0",
        p2_label="v1",
        game_index=3,
        total=10,
        p1_wins=2,
        p2_wins=1,
    )

    non_sentinel = _count_non_sentinel_pixels(surface, overlay_rect)
    assert non_sentinel > 0, (
        f"render_overlay({bar!r}, {size!r}) painted nothing inside its rect"
    )
    # A rendered layout with text + fill + border is AT LEAST thousands
    # of pixels. An empty paint would be 0 or a handful of border
    # pixels. Use a floor that distinguishes "blank" from "text".
    assert non_sentinel > 500, (
        f"render_overlay({bar!r}, {size!r}) painted only {non_sentinel} "
        f"non-sentinel pixels — looks blank"
    )
    assert _any_sentinel_pixel_outside(
        surface, overlay_rect, container_size
    ), (
        f"render_overlay({bar!r}, {size!r}) stomped outside its rect"
    )


def test_render_overlay_empty_state_paints_background_only(
    pygame_init: None,
) -> None:
    """``game_index=0`` paints dark background only — no text.

    Rationale: Before a batch starts we want a visually calm empty bar.
    We sample a CENTRAL interior sub-rect (far from the border) and
    count white-ish pixels there. Active state paints centred text
    there; empty state should have ZERO white pixels in that zone
    (the dark fill is the only thing rendered).
    """
    from selfplay_viewer.overlay import render_overlay

    bar, size = "top", "large"
    container_size = CONTAINER_SIZES[(bar, size)]
    ox, oy, ow, oh = OVERLAY_RECTS[(bar, size)]

    # Interior sample rect — inset well past the 4px border, centred
    # horizontally so the text rows land in the middle.
    inset = 50
    sample_rect = (ox + inset, oy + inset // 2, ow - 2 * inset, oh - inset)

    # Baseline render with real data.
    active_surface = pygame.Surface(container_size)
    active_surface.fill(_SENTINEL_COLOR)
    render_overlay(
        surface=active_surface,
        bar=bar,
        size=size,
        p1_label="v0",
        p2_label="v1",
        game_index=3,
        total=10,
        p1_wins=2,
        p2_wins=1,
    )

    # Empty state.
    empty_surface = pygame.Surface(container_size)
    empty_surface.fill(_SENTINEL_COLOR)
    render_overlay(
        surface=empty_surface,
        bar=bar,
        size=size,
        p1_label="",
        p2_label="",
        game_index=0,
        total=0,
        p1_wins=0,
        p2_wins=0,
    )

    active_white = _count_white(active_surface, sample_rect)
    empty_white = _count_white(empty_surface, sample_rect)

    # Empty state has no text in the interior — white count should be 0.
    # (The border is excluded from the sample rect via the 50px inset.)
    assert empty_white == 0, (
        f"empty state painted {empty_white} white-ish pixels in the "
        f"interior sample rect {sample_rect} — text leaked through "
        f"the game_index=0 guard"
    )
    # Active state paints centred text — must have SOMETHING there.
    assert active_white > 100, (
        f"active state painted only {active_white} white-ish pixels "
        f"in the interior sample rect — text wasn't rendered?"
    )


def test_format_score_line_same_version_returns_unified_wins() -> None:
    """``_format_score_line`` returns "Wins: N" when seats share a label.

    This is the direct unit-level test for the same-version-self-play
    score string. The rendering smoke test below verifies the format
    actually makes it onto the surface; this test pins the string format
    so a future refactor can't silently drift to e.g. "Total: N".
    """
    from selfplay_viewer.overlay import _format_score_line

    same = _format_score_line(is_same_version=True, p1_wins=3, p2_wins=0)
    diff = _format_score_line(is_same_version=False, p1_wins=2, p2_wins=1)
    assert same == "Wins: 3"
    assert diff == "W-L: 2 - 1"


def test_render_overlay_same_version_paints_score_row(
    pygame_init: None,
) -> None:
    """``p1_label == p2_label`` still paints SOMETHING in the score row.

    Pairs with :func:`test_format_score_line_same_version_returns_unified_wins`
    (which pins the exact string) by verifying the score row is non-empty
    after the new branch runs. The two together cover both halves of the
    same-version code path: the helper and the render integration.

    Pixel-shape comparison between same-version and cross-version was
    tried but "Wins: 1" and "W-L: 1 - 0" happen to have very similar
    bounding-box widths at this font size, so a count-based diff isn't
    reliable. The string-level unit test above is the authoritative
    behavioural check.
    """
    from selfplay_viewer.overlay import (
        SIDEBAR_LEFT_PAD_PX,
        SIDEBAR_SCORE_Y_PX,
        render_overlay,
    )

    bar, size = "side", "large"
    container_size = CONTAINER_SIZES[(bar, size)]
    ox, oy, ow, _oh = OVERLAY_RECTS[(bar, size)]

    score_rect = (
        ox + SIDEBAR_LEFT_PAD_PX,
        oy + SIDEBAR_SCORE_Y_PX,
        ow - 2 * SIDEBAR_LEFT_PAD_PX,
        30,
    )

    surface = pygame.Surface(container_size)
    surface.fill(_SENTINEL_COLOR)
    render_overlay(
        surface=surface,
        bar=bar,
        size=size,
        p1_label="v0",
        p2_label="v0",
        game_index=2,
        total=2,
        p1_wins=1,
        p2_wins=0,
    )

    same_white = _count_white(surface, score_rect)
    assert same_white > 50, (
        f"same-version score row painted only {same_white} white pixels — "
        f"the unified 'Wins:' string didn't render"
    )


def test_render_overlay_long_label_does_not_overflow_sidebar(
    pygame_init: None,
) -> None:
    """A 40-char p1_label must not stomp pixels left of the side-bar column.

    The side-bar overlay is the rightmost column of the container, so
    "overflow" means painting LEFT of the overlay rect (into the p2
    pane region). Sample the column one pixel LEFT of the overlay's
    left edge along its full height. All sampled pixels must stay
    sentinel — text overflow from a long label would otherwise stomp
    them.

    Belt-and-braces: this verifies BOTH the ellipsis-truncation in
    ``_fit_text`` AND the clip-rect set in ``render_overlay``. Either
    one breaking would let a 40+ char label leak past the column.
    """
    from selfplay_viewer.overlay import render_overlay

    bar, size = "side", "large"
    container_size = CONTAINER_SIZES[(bar, size)]
    ox, oy, ow, oh = OVERLAY_RECTS[(bar, size)]

    surface = pygame.Surface(container_size)
    surface.fill(_SENTINEL_COLOR)

    long_label = "feat/lstm-kl-imitation-experiment-0040-xyz"
    assert len(long_label) >= 40, "test premise: label is at least 40 chars"

    render_overlay(
        surface=surface,
        bar=bar,
        size=size,
        p1_label=long_label,
        p2_label=long_label,  # both sides long, both must fit
        game_index=1,
        total=5,
        p1_wins=0,
        p2_wins=0,
    )

    # The side-bar sits at the rightmost column of the container, so
    # "outside" means one pixel LEFT of the overlay's left edge —
    # that's the p2-pane / container-background region. Sample the
    # full height of the overlay against that column.
    if ox <= 0:
        pytest.skip(
            "side-bar overlay starts at container left edge — "
            "no exterior column to sample"
        )
    overflow_x = ox - 1
    overflows = []
    for y in range(oy, oy + oh):
        px = surface.get_at((overflow_x, y))
        if (px.r, px.g, px.b) != _SENTINEL_COLOR:
            overflows.append((overflow_x, y))
    assert not overflows, (
        f"side-bar long-label render leaked {len(overflows)} pixels "
        f"into the exterior column at x={overflow_x}; sample first 5: "
        f"{overflows[:5]}"
    )


# ---------------------------------------------------------------------------
# W-L counter integration — exercises the label→counter mapping on
# SelfPlayViewer without requiring pygame / Win32.
# ---------------------------------------------------------------------------


class TestViewerWinLossCounters:
    """State-update helpers increment the right side's counter.

    We intentionally call ``_update_game_start_state`` /
    ``_update_game_end_state`` directly rather than the full
    ``_handle_game_*`` path — that one does pygame + Win32 work which
    needs a container window. The state-update helpers are the pure-
    data split so they're testable off-Windows.
    """

    def _make_viewer(self) -> Any:
        # ``SelfPlayViewer.__init__`` does NOT touch pygame, so import
        # + construct works on Linux. (Only run / attach_pane require
        # pygame + pywin32.)
        pytest.importorskip(
            "selfplay_viewer",
            reason="viewer extra (pygame) not installed",
        )
        from selfplay_viewer import SelfPlayViewer

        return SelfPlayViewer()

    def _record(
        self,
        winner: str | None,
        *,
        p1_version: str = "v0",
        p2_version: str = "v1",
    ) -> Any:
        from orchestrator.contracts import SelfPlayRecord

        return SelfPlayRecord(
            match_id="abc",
            p1_version=p1_version,
            p2_version=p2_version,
            winner=winner,
            map_name="Simple64",
            duration_s=22.5,
            seat_swap=False,
            timestamp="2026-04-18T12:00:00+00:00",
        )

    @pytest.mark.parametrize(
        ("winner", "expected_p1", "expected_p2"),
        [
            ("v0", 1, 0),
            ("v1", 0, 1),
            (None, 0, 0),
            ("somebody-else", 0, 0),
        ],
        ids=["p1-wins", "p2-wins", "draw", "unknown-winner"],
    )
    def test_single_game_counter_attribution(
        self,
        winner: str | None,
        expected_p1: int,
        expected_p2: int,
    ) -> None:
        """Single-game increments the matching side's counter (or neither).

        Consolidates four previously separate tests (p1-wins, p2-wins,
        draw, unknown-winner) into one parametrize. Defensive case
        (``somebody-else``) verifies we don't mis-attribute when the
        winner string matches neither stored label.
        """
        viewer = self._make_viewer()
        viewer._update_game_start_state(1, 3, "v0", "v1")
        viewer._update_game_end_state(self._record(winner))
        assert viewer._p1_wins == expected_p1
        assert viewer._p2_wins == expected_p2

    def test_counters_accumulate_across_batch(self) -> None:
        """A 3-game batch tallies (2, 1) correctly."""
        viewer = self._make_viewer()
        for idx, winner in enumerate(("v0", "v1", "v0"), start=1):
            viewer._update_game_start_state(idx, 3, "v0", "v1")
            viewer._update_game_end_state(self._record(winner))
        assert viewer._p1_wins == 2
        assert viewer._p2_wins == 1
        assert viewer._game_index == 3
        assert viewer._total_games == 3

    def test_labels_track_per_game_seat_swap(self) -> None:
        """``on_game_start`` overwrites the seat labels each game.

        Step 4's ``_run_single_game_with_callbacks`` resolves seat swap
        BEFORE firing ``on_game_start``, so the p1/p2 labels we receive
        are authoritative for that game. Between games the swap can
        flip, and our state update has to reflect the new mapping.
        """
        viewer = self._make_viewer()
        viewer._update_game_start_state(1, 2, "v0", "v1")
        viewer._update_game_end_state(
            self._record("v0", p1_version="v0", p2_version="v1")
        )
        # Seat swap: v1 is now in slot 0.
        viewer._update_game_start_state(2, 2, "v1", "v0")
        viewer._update_game_end_state(
            self._record("v1", p1_version="v1", p2_version="v0")
        )
        # Both wins landed on the respective slot-0 seat.
        assert viewer._p1_wins == 2
        assert viewer._p2_wins == 0

    def test_same_version_selfplay_counts_wins(self) -> None:
        """``p1_label == p2_label`` collapses to a single-counter Wins total.

        Reproduces the spec's first acceptance command:
        ``selfplay --p1 v0 --p2 v0``. Both labels are "v0", so the
        previous "if/elif" attribution would always fire on _p1_wins
        and _p2_wins would stay 0 forever — the overlay would lie.

        Fix: collapse to one counter. We use ``_p1_wins`` as the
        canonical slot; ``_p2_wins`` stays at 0 in this mode (and the
        overlay detects same-label and renders "Wins: N").
        """
        viewer = self._make_viewer()
        for idx in range(1, 4):
            viewer._update_game_start_state(idx, 3, "v0", "v0")
            viewer._update_game_end_state(
                self._record("v0", p1_version="v0", p2_version="v0")
            )
        assert viewer._p1_wins == 3
        assert viewer._p2_wins == 0  # never incremented in same-version mode

    def test_counters_reset_on_new_batch(self) -> None:
        """``game_index == 1`` resets W-L so a reused viewer starts fresh.

        The same SelfPlayViewer instance can be passed to multiple
        ``run_with_batch`` calls back-to-back (e.g. demo + soak run).
        Without the reset, counters from the prior batch would bleed
        into the next batch's overlay header.
        """
        viewer = self._make_viewer()
        # Pretend a previous batch left some accumulated state behind.
        viewer._p1_wins = 4
        viewer._p2_wins = 3
        viewer._game_index = 7
        viewer._total_games = 7

        viewer._update_game_start_state(1, 5, "v2", "v3")

        assert viewer._p1_wins == 0
        assert viewer._p2_wins == 0
        assert viewer._game_index == 1
        assert viewer._total_games == 5
        assert viewer._p1_label == "v2"
        assert viewer._p2_label == "v3"
