"""Layout constants + overlay rendering for the self-play viewer.

The top of this module is pure-Python layout data (``CONTAINER_SIZES``,
``PANE_RECTS``, ``OVERLAY_RECTS``) — importable on Linux without pygame
so orchestrator/unit code can reason about geometry without the
``[viewer]`` extra installed.

The rendering helpers (:func:`render_overlay` and friends) import pygame
lazily inside each function body so the "Linux-importable" contract
above is preserved. Callers (``SelfPlayViewer._paint_frame``) are
already running a pygame loop so the lazy import is a no-cost noop on
Windows.

Authoritative layout numbers come from
``documentation/plans/selfplay-viewer-plan.md`` (Section 5). Tuning
is a one-line edit here; ``container.py`` and tests both read from
these constants — no magic values elsewhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Literal

if TYPE_CHECKING:
    import pygame  # For types only — real import is inside each function.

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

#: Border colour for the overlay (RGB). White.
OVERLAY_BORDER_COLOR: Final[tuple[int, int, int]] = (0xFF, 0xFF, 0xFF)

#: Semi-transparent dark fill for the overlay (RGBA).
OVERLAY_FILL_COLOR: Final[tuple[int, int, int, int]] = (0x00, 0x00, 0x00, 0x80)

#: Border thickness for the overlay (px).
OVERLAY_BORDER_PX: Final[int] = 4

#: Text colour for the main labels (RGB).
OVERLAY_TEXT_COLOR: Final[tuple[int, int, int]] = (0xFF, 0xFF, 0xFF)

#: Dimmer colour for side-bar header/dividers (RGB).
OVERLAY_DIM_TEXT_COLOR: Final[tuple[int, int, int]] = (0xC0, 0xC0, 0xC0)

#: Side-bar left padding (px) — leaves some breathing room from the
#: overlay-rect edge so text doesn't brush the border.
SIDEBAR_LEFT_PAD_PX: Final[int] = 20

#: Y-offset below which the sidebar is intentionally empty in v1 — the
#: v2 advisor feed lands here. Rendering never draws anything below this
#: line in the side-bar layout.
SIDEBAR_V2_RESERVED_Y: Final[int] = 260

# ---------------------------------------------------------------------------
# Layout y-offsets and font sizes — extracted to module-level so the
# helpers below stay free of magic numbers (matches the
# ``OVERLAY_BORDER_PX`` precedent).
# ---------------------------------------------------------------------------

#: Y offset for the top-bar VS title row (centred, big font).
TOP_BAR_TITLE_Y_PX: Final[int] = 20
#: Y offset for the top-bar subtitle row (Game N / Total + W-L).
TOP_BAR_SUBTITLE_Y_PX: Final[int] = 70

#: Y offset for the side-bar "VS" header row (small dim font).
SIDEBAR_HEADER_Y_PX: Final[int] = 20
#: Y offset for the side-bar p1 label row (label font).
SIDEBAR_P1_Y_PX: Final[int] = 60
#: Y offset for the side-bar dividing rule (small dim font).
SIDEBAR_DIVIDER_Y_PX: Final[int] = 110
#: Y offset for the side-bar p2 label row (label font).
SIDEBAR_P2_Y_PX: Final[int] = 130
#: Y offset for the side-bar "Game N / Total" row (small font).
SIDEBAR_GAME_Y_PX: Final[int] = 180
#: Y offset for the side-bar score row (small font).
SIDEBAR_SCORE_Y_PX: Final[int] = 220

#: Font size for the top-bar VS title (px / pygame "size" units).
OVERLAY_TITLE_FONT_PX: Final[int] = 48
#: Font size for the side-bar player labels.
OVERLAY_LABEL_FONT_PX: Final[int] = 36
#: Font size for the small text rows (subtitle / score / game number).
OVERLAY_SMALL_FONT_PX: Final[int] = 24

#: Module-level font cache so the pygame main loop doesn't open a fresh
#: ``pygame.font.Font(None, N)`` on every frame (60-90 opens/sec
#: otherwise). Populated lazily by :func:`_get_font`. The cache is keyed
#: by font size only because the overlay always uses the default font
#: (``None``).
#:
#: Cache invalidation: ``pygame.quit()`` frees the SDL_TTF state that
#: every cached ``Font`` handle points into; reusing a stale handle
#: SIGSEGVs the interpreter (no Python exception — the process dies).
#: We can't detect the quit→init cycle from inside the cache because
#: ``pygame.init`` from the next session is called BEFORE our code
#: runs, so ``pygame.font.get_init()`` already reports True. The simple
#: fix is to expose :func:`reset_font_cache` and have any caller that
#: tears pygame down (e.g. the ``pygame_init`` test fixture and the
#: viewer's ``run_with_batch`` ``finally`` block) call it explicitly.
_FONT_CACHE: dict[int, Any] = {}


def reset_font_cache() -> None:
    """Drop every cached font handle.

    MUST be called whenever ``pygame.quit()`` is invoked anywhere in the
    process. Cached ``pygame.font.Font`` handles are bound to the
    SDL_TTF state allocated by ``pygame.init()``; ``pygame.quit()``
    frees that state and any subsequent ``Font.render`` call on a
    cached handle from the prior session SIGSEGVs the interpreter (no
    Python-level exception).

    Called by:

    - The ``pygame_init`` pytest fixture in
      ``tests/test_overlay.py`` — once on teardown after every render
      test so the next test starts fresh.
    - :meth:`SelfPlayViewer.run_with_batch` and :meth:`SelfPlayViewer.run`
      ``finally`` blocks after ``pygame.quit()``.
    """
    _FONT_CACHE.clear()


def _ensure_font_init() -> None:
    """Defensive ``pygame.font.init()`` for callers that skip ``pygame.init``.

    ``pygame.init()`` would normally cover this, but unit tests may pump
    surfaces through :func:`render_overlay` without the full engine
    initialisation. ``pygame.font.init()`` is idempotent; calling it
    when already initialised is a cheap noop.
    """
    import pygame

    if not pygame.font.get_init():
        pygame.font.init()


def _get_font(size_px: int) -> pygame.font.Font:
    """Return a cached ``pygame.font.Font`` for the default font + *size_px*.

    Avoids the ~60-90 font opens/sec the overlay would otherwise pay
    inside the pygame main loop. The cache is module-scoped because the
    pygame default font is global and a Font handle is reusable across
    surfaces. ``_ensure_font_init`` is called once per cache miss so
    callers that bypass ``pygame.init`` (unit tests) still work.

    Callers that call ``pygame.quit()`` MUST also call
    :func:`reset_font_cache` afterwards — see that function's docstring
    for the SIGSEGV-on-stale-handle gotcha.
    """
    import pygame

    cached = _FONT_CACHE.get(size_px)
    if cached is not None:
        return cached  # type: ignore[no-any-return,unused-ignore]
    _ensure_font_init()
    font = pygame.font.Font(None, size_px)
    _FONT_CACHE[size_px] = font
    return font


def _fit_text(
    label: str,
    font: pygame.font.Font,
    max_width_px: int,
) -> str:
    """Truncate *label* with an ellipsis until it fits *max_width_px*.

    Strips one character at a time from the END of the label and appends
    ``…`` (single-char ellipsis) until ``font.size(...)`` reports a width
    ``<= max_width_px``. If the label already fits it is returned
    unchanged. If the ellipsis alone is wider than the budget we return
    the empty string rather than a guaranteed-overflow stub.
    """
    if font.size(label)[0] <= max_width_px:
        return label
    ellipsis = "\u2026"  # single-character ellipsis
    if font.size(ellipsis)[0] > max_width_px:
        return ""
    # Strip from the end, retrying until we fit.
    truncated = label
    while truncated and font.size(truncated + ellipsis)[0] > max_width_px:
        truncated = truncated[:-1]
    return truncated + ellipsis if truncated else ellipsis


def _paint_background(
    surface: pygame.Surface,
    overlay_rect: Rect,
) -> None:
    """Paint the semi-transparent dark fill + white border for the overlay rect."""
    import pygame

    ox, oy, ow, oh = overlay_rect
    overlay_surface = pygame.Surface((ow, oh), pygame.SRCALPHA)
    overlay_surface.fill(OVERLAY_FILL_COLOR)
    surface.blit(overlay_surface, (ox, oy))
    pygame.draw.rect(
        surface,
        OVERLAY_BORDER_COLOR,
        pygame.Rect(ox, oy, ow, oh),
        OVERLAY_BORDER_PX,
    )


def _blit_centered(
    surface: pygame.Surface,
    text_surface: pygame.Surface,
    rect_x: int,
    rect_width: int,
    y: int,
) -> None:
    """Blit *text_surface* horizontally centred within ``[rect_x, rect_x+rect_width]``."""
    tw = text_surface.get_width()
    x = rect_x + (rect_width - tw) // 2
    surface.blit(text_surface, (x, y))


def _format_score_line(
    *,
    is_same_version: bool,
    p1_wins: int,
    p2_wins: int,
) -> str:
    """Format the W-L (or unified Wins) score string.

    When ``is_same_version`` is ``True`` (same-version self-play, e.g.
    ``v0`` vs ``v0``), the per-side W-L counter is meaningless because
    both seats share a label — see ``_update_game_end_state``. We render
    a single ``Wins: N`` counter instead, where ``N`` is ``p1_wins``
    (the only counter we incremented in that mode).
    """
    if is_same_version:
        return f"Wins: {p1_wins}"
    return f"W-L: {p1_wins} - {p2_wins}"


def _render_top_bar(
    surface: pygame.Surface,
    overlay_rect: Rect,
    *,
    p1_label: str,
    p2_label: str,
    game_index: int,
    total: int,
    p1_wins: int,
    p2_wins: int,
    is_same_version: bool,
) -> None:
    """Render the top-bar text layout.

    Row 1 (:data:`TOP_BAR_TITLE_Y_PX`, :data:`OVERLAY_TITLE_FONT_PX`):
    ``{p1_label}  VS  {p2_label}`` centred.
    Row 2 (:data:`TOP_BAR_SUBTITLE_Y_PX`, :data:`OVERLAY_SMALL_FONT_PX`):
    ``Game {n} / {total}   •   W-L: {w1} - {w2}`` centred (or
    ``Wins: {n}`` when both seats share a label).
    """
    ox, oy, ow, _oh = overlay_rect

    big_font = _get_font(OVERLAY_TITLE_FONT_PX)
    small_font = _get_font(OVERLAY_SMALL_FONT_PX)

    vs_text = f"{p1_label}  VS  {p2_label}"
    vs_surface = big_font.render(vs_text, True, OVERLAY_TEXT_COLOR)
    _blit_centered(surface, vs_surface, ox, ow, oy + TOP_BAR_TITLE_Y_PX)

    score_str = _format_score_line(
        is_same_version=is_same_version,
        p1_wins=p1_wins,
        p2_wins=p2_wins,
    )
    sub_text = f"Game {game_index} / {total}   \u2022   {score_str}"
    sub_surface = small_font.render(sub_text, True, OVERLAY_TEXT_COLOR)
    _blit_centered(surface, sub_surface, ox, ow, oy + TOP_BAR_SUBTITLE_Y_PX)


def _render_side_bar(
    surface: pygame.Surface,
    overlay_rect: Rect,
    *,
    p1_label: str,
    p2_label: str,
    game_index: int,
    total: int,
    p1_wins: int,
    p2_wins: int,
    is_same_version: bool,
) -> None:
    """Render the side-bar stacked text layout.

    - :data:`SIDEBAR_HEADER_Y_PX`: "VS" header (small, dim)
    - :data:`SIDEBAR_P1_Y_PX`: p1_label (label font, ellipsis-truncated)
    - :data:`SIDEBAR_DIVIDER_Y_PX`: divider rule
    - :data:`SIDEBAR_P2_Y_PX`: p2_label (label font, ellipsis-truncated)
    - :data:`SIDEBAR_GAME_Y_PX`: "Game {n} / {total}" (small)
    - :data:`SIDEBAR_SCORE_Y_PX`: score row (small)

    Below :data:`SIDEBAR_V2_RESERVED_Y` is intentionally blank — the v2
    advisor feed will slot in there. Defensively assert the overlay
    height is large enough to host the v1 layout (any future shrink to
    the side-bar OVERLAY_RECTS entries will trip this).
    """
    ox, oy, _ow, oh = overlay_rect
    assert oh >= SIDEBAR_V2_RESERVED_Y, (
        f"side-bar overlay height {oh} < SIDEBAR_V2_RESERVED_Y "
        f"({SIDEBAR_V2_RESERVED_Y}); shrinking the side-bar overlay rect "
        f"would clip the v1 layout"
    )
    text_x = ox + SIDEBAR_LEFT_PAD_PX
    # Width budget for label ellipsis truncation — from text_x to the
    # right edge of the overlay column, minus a single SIDEBAR_LEFT_PAD_PX
    # so we don't brush the border on the right side either.
    label_max_width = max(0, _ow - 2 * SIDEBAR_LEFT_PAD_PX)

    header_font = _get_font(OVERLAY_SMALL_FONT_PX)
    label_font = _get_font(OVERLAY_LABEL_FONT_PX)
    small_font = _get_font(OVERLAY_SMALL_FONT_PX)

    header_surface = header_font.render("VS", True, OVERLAY_DIM_TEXT_COLOR)
    surface.blit(header_surface, (text_x, oy + SIDEBAR_HEADER_Y_PX))

    p1_fitted = _fit_text(p1_label, label_font, label_max_width)
    p1_surface = label_font.render(p1_fitted, True, OVERLAY_TEXT_COLOR)
    surface.blit(p1_surface, (text_x, oy + SIDEBAR_P1_Y_PX))

    divider_surface = header_font.render(
        "\u2500\u2500\u2500", True, OVERLAY_DIM_TEXT_COLOR
    )
    surface.blit(divider_surface, (text_x, oy + SIDEBAR_DIVIDER_Y_PX))

    p2_fitted = _fit_text(p2_label, label_font, label_max_width)
    p2_surface = label_font.render(p2_fitted, True, OVERLAY_TEXT_COLOR)
    surface.blit(p2_surface, (text_x, oy + SIDEBAR_P2_Y_PX))

    game_surface = small_font.render(
        f"Game {game_index} / {total}", True, OVERLAY_TEXT_COLOR
    )
    surface.blit(game_surface, (text_x, oy + SIDEBAR_GAME_Y_PX))

    score_str = _format_score_line(
        is_same_version=is_same_version,
        p1_wins=p1_wins,
        p2_wins=p2_wins,
    )
    score_surface = small_font.render(score_str, True, OVERLAY_TEXT_COLOR)
    surface.blit(score_surface, (text_x, oy + SIDEBAR_SCORE_Y_PX))


def render_overlay(
    *,
    surface: pygame.Surface,
    bar: str,
    size: str,
    p1_label: str,
    p2_label: str,
    game_index: int,
    total: int,
    p1_wins: int,
    p2_wins: int,
) -> None:
    """Paint the stats overlay onto *surface* at the rect from :data:`OVERLAY_RECTS`.

    When ``game_index == 0`` or ``total == 0`` (batch not yet started)
    the overlay renders only the dark background rect + border; no
    text is drawn. This keeps the idle frame visually calm without
    breaking the invariant that the overlay rect always has a fill.

    Same-version self-play detection: when ``p1_label == p2_label`` (e.g.
    ``selfplay --p1 v0 --p2 v0``), per-seat W-L is meaningless because
    both seats share a label. In that mode the score row collapses to
    ``Wins: N`` where ``N`` is ``p1_wins`` (the only counter
    ``_update_game_end_state`` increments in same-version mode).

    Parameters
    ----------
    surface:
        The pygame display surface (full container size). The overlay
        is rendered into the sub-rect at ``OVERLAY_RECTS[(bar, size)]``.
    bar, size:
        The current layout selector — same keys as
        :data:`CONTAINER_SIZES` / :data:`OVERLAY_RECTS`.
    p1_label, p2_label:
        Version labels for each seat, post-swap resolution (see
        :class:`SelfPlayViewer.on_game_start`).
    game_index:
        1-based game number within the batch. ``0`` means "not started".
    total:
        Total games in the batch. ``0`` means "not started".
    p1_wins, p2_wins:
        Running score (counted against ``p1_label`` / ``p2_label``).

    Notes
    -----
    The lazy ``import pygame`` inside this body is deliberate — the
    module-level has no pygame import so Linux CI can still import
    :mod:`selfplay_viewer.overlay` for its layout constants without the
    ``[viewer]`` extras.

    A clip rect equal to ``OVERLAY_RECTS[(bar, size)]`` is set for the
    duration of the paint and restored in ``finally``. This is a
    belt-and-braces guarantee that NO future label / counter / icon
    addition can bleed past the overlay column into the SC2 panes —
    even when individual ``_fit_text`` calls have a too-generous width
    budget. The previous clip is preserved so callers that set their
    own clip don't have it stomped.
    """
    import pygame

    _ensure_font_init()

    overlay_rect = OVERLAY_RECTS[(bar, size)]

    prior_clip = surface.get_clip()
    surface.set_clip(pygame.Rect(*overlay_rect))
    try:
        _paint_background(surface, overlay_rect)

        if game_index == 0 or total == 0:
            # Empty-state (batch not started) — leave the dark rect alone.
            return

        is_same_version = p1_label == p2_label
        if bar == "top":
            _render_top_bar(
                surface,
                overlay_rect,
                p1_label=p1_label,
                p2_label=p2_label,
                game_index=game_index,
                total=total,
                p1_wins=p1_wins,
                p2_wins=p2_wins,
                is_same_version=is_same_version,
            )
        else:
            _render_side_bar(
                surface,
                overlay_rect,
                p1_label=p1_label,
                p2_label=p2_label,
                game_index=game_index,
                total=total,
                p1_wins=p1_wins,
                p2_wins=p2_wins,
                is_same_version=is_same_version,
            )
    finally:
        surface.set_clip(prior_clip)


__all__ = [
    "Bar",
    "CONTAINER_SIZES",
    "OVERLAY_BORDER_COLOR",
    "OVERLAY_BORDER_PX",
    "OVERLAY_DIM_TEXT_COLOR",
    "OVERLAY_FILL_COLOR",
    "OVERLAY_LABEL_FONT_PX",
    "OVERLAY_RECTS",
    "OVERLAY_SMALL_FONT_PX",
    "OVERLAY_TEXT_COLOR",
    "OVERLAY_TITLE_FONT_PX",
    "PANE_RECTS",
    "Rect",
    "SIDEBAR_DIVIDER_Y_PX",
    "SIDEBAR_GAME_Y_PX",
    "SIDEBAR_HEADER_Y_PX",
    "SIDEBAR_LEFT_PAD_PX",
    "SIDEBAR_P1_Y_PX",
    "SIDEBAR_P2_Y_PX",
    "SIDEBAR_SCORE_Y_PX",
    "SIDEBAR_V2_RESERVED_Y",
    "Size",
    "TOP_BAR_SUBTITLE_Y_PX",
    "TOP_BAR_TITLE_Y_PX",
    "render_overlay",
    "reset_font_cache",
]
