"""Tests for the placeholder state machine + rendering (Step 7).

Two test surfaces:

* **Rendering tests** (``render_placeholder``) gated behind the
  ``pygame_init`` fixture — they use the same sentinel-pixel approach
  as :mod:`tests.test_overlay`, pre-filling the surface with a
  reserved green colour and diffing post-render to prove bounds +
  content.
* **Container state-transition tests** — Linux-safe (no pygame, no
  pywin32). They exercise
  :meth:`~selfplay_viewer.container.SelfPlayViewer._poll_attached_panes_for_death`
  and the ``attach_pane`` / ``detach_pane`` / layout-change placeholder
  hooks by monkeypatching :meth:`_is_hwnd_alive` (to sidestep pywin32)
  and :mod:`selfplay_viewer.reparent` (to sidestep Win32 I/O).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest

from selfplay_viewer.overlay import (
    CONTAINER_SIZES,
    PLACEHOLDER_MESSAGES,
)

if TYPE_CHECKING:
    import pygame


# Sentinel background colour — pure green. ``render_placeholder`` only
# ever paints dark RGBA fill + white text, so (0, 255, 0) is impossible
# to produce. We fill the surface with it pre-render and pixel-diff
# afterwards.
_SENTINEL_COLOR: tuple[int, int, int] = (0, 255, 0)


try:
    import pygame  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — Linux / py without viewer extras
    pygame = None  # type: ignore[assignment]


@pytest.fixture()
def pygame_init() -> Iterator[None]:
    """Mirror of ``test_overlay.pygame_init`` — init + teardown + cache reset.

    The ``reset_font_cache`` call after ``pygame.quit()`` is mandatory
    because the overlay module caches ``Font`` handles that point into
    SDL_TTF state that ``pygame.quit()`` frees. Reusing a stale handle
    SIGSEGVs the interpreter.
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
    """Count pixels in *rect* whose RGB is NOT the sentinel colour."""
    x0, y0, w, h = rect
    count = 0
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            px = surface.get_at((x, y))
            if (px.r, px.g, px.b) != _SENTINEL_COLOR:
                count += 1
    return count


def _count_white(
    surf: pygame.Surface, rect: tuple[int, int, int, int]
) -> int:
    """Count white-ish pixels (all RGB channels > 200) in *rect*."""
    x0, y0, w, h = rect
    n = 0
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            p = surf.get_at((x, y))
            if p.r > 200 and p.g > 200 and p.b > 200:
                n += 1
    return n


# ---------------------------------------------------------------------------
# Rendering tests — require pygame (skipped on Linux without the extra).
# ---------------------------------------------------------------------------


def test_render_placeholder_paints_dark_fill(pygame_init: None) -> None:
    """Inside pane_rect: non-sentinel. Outside: stays sentinel."""
    from selfplay_viewer.overlay import render_placeholder

    container_size = CONTAINER_SIZES[("top", "large")]
    pane_rect = (100, 100, 400, 300)

    surface = pygame.Surface(container_size)
    surface.fill(_SENTINEL_COLOR)

    render_placeholder(
        surface=surface,
        pane_rect=pane_rect,
        message="v0 has rage-quit",
    )

    inside = _count_non_sentinel_pixels(surface, pane_rect)
    assert inside > 0, "render_placeholder painted nothing inside the pane rect"
    # A dark fill + text is thousands of pixels — guard against a
    # no-op paint slipping through.
    assert inside > 500, (
        f"render_placeholder painted only {inside} non-sentinel pixels "
        f"inside the pane rect — looks blank"
    )

    # Sample corners OUTSIDE the pane rect — they must remain sentinel.
    px0, py0, pw, ph = pane_rect
    cw, ch = container_size
    outside_samples = [
        (0, 0),  # top-left of container
        (cw - 1, 0),  # top-right
        (0, ch - 1),  # bottom-left
        (cw - 1, ch - 1),  # bottom-right
        (px0 - 1, py0),  # one pixel left of pane
        (px0 + pw, py0),  # one pixel right of pane
    ]
    for x, y in outside_samples:
        px = surface.get_at((x, y))
        assert (px.r, px.g, px.b) == _SENTINEL_COLOR, (
            f"render_placeholder stomped outside the pane rect at ({x}, {y}) "
            f"— pixel is {(px.r, px.g, px.b)!r} not sentinel green"
        )


def test_render_placeholder_renders_text(pygame_init: None) -> None:
    """The pane centre has white-ish text pixels after a placeholder paint."""
    from selfplay_viewer.overlay import render_placeholder

    container_size = CONTAINER_SIZES[("top", "large")]
    pane_rect = (100, 100, 400, 300)

    surface = pygame.Surface(container_size)
    surface.fill(_SENTINEL_COLOR)

    render_placeholder(
        surface=surface,
        pane_rect=pane_rect,
        message="v0 has rage-quit",
    )

    # Interior sub-rect at the pane's centre — text is centred so a
    # 200x60 box at the middle should contain glyph pixels.
    px0, py0, pw, ph = pane_rect
    cx = px0 + pw // 2
    cy = py0 + ph // 2
    sample_rect = (cx - 100, cy - 30, 200, 60)

    white = _count_white(surface, sample_rect)
    assert white > 50, (
        f"render_placeholder drew only {white} white-ish pixels in the "
        f"pane-centre sample rect {sample_rect} — the message did not render"
    )


def test_render_placeholder_fits_long_message(pygame_init: None) -> None:
    """Ultra-long message truncates and stays inside the pane rect.

    Strategy: render a 200-char message into a narrow pane, then sample
    pixels one column LEFT of the pane, one column RIGHT of the pane,
    and one row ABOVE / BELOW the pane. All must still be sentinel —
    a naive non-truncated render would leak glyph pixels past the edge.
    """
    from selfplay_viewer.overlay import render_placeholder

    container_size = CONTAINER_SIZES[("top", "large")]
    pane_rect = (200, 200, 300, 200)

    surface = pygame.Surface(container_size)
    surface.fill(_SENTINEL_COLOR)

    long_message = "v0 " + ("has rage-quit and stormed off into the night " * 10)
    assert len(long_message) > 200, "test premise: message is intentionally huge"

    render_placeholder(
        surface=surface,
        pane_rect=pane_rect,
        message=long_message,
    )

    px0, py0, pw, ph = pane_rect

    # Sample the full outer border (one pixel outside each edge of the
    # pane). Every pixel must remain sentinel — the clip-rect guard
    # plus ellipsis truncation keeps us inside.
    overflows: list[tuple[int, int]] = []
    # Left column (one pixel left of pane).
    if px0 > 0:
        for y in range(py0, py0 + ph):
            px = surface.get_at((px0 - 1, y))
            if (px.r, px.g, px.b) != _SENTINEL_COLOR:
                overflows.append((px0 - 1, y))
    # Right column (one pixel right of pane).
    if px0 + pw < container_size[0]:
        for y in range(py0, py0 + ph):
            px = surface.get_at((px0 + pw, y))
            if (px.r, px.g, px.b) != _SENTINEL_COLOR:
                overflows.append((px0 + pw, y))
    # Top row (one pixel above pane).
    if py0 > 0:
        for x in range(px0, px0 + pw):
            px = surface.get_at((x, py0 - 1))
            if (px.r, px.g, px.b) != _SENTINEL_COLOR:
                overflows.append((x, py0 - 1))
    # Bottom row (one pixel below pane).
    if py0 + ph < container_size[1]:
        for x in range(px0, px0 + pw):
            px = surface.get_at((x, py0 + ph))
            if (px.r, px.g, px.b) != _SENTINEL_COLOR:
                overflows.append((x, py0 + ph))

    assert not overflows, (
        f"render_placeholder leaked {len(overflows)} pixels outside the "
        f"pane rect — truncation / clip-rect guard failed. Sample first 5: "
        f"{overflows[:5]}"
    )


# ---------------------------------------------------------------------------
# Pure-data tests — no pygame, no pywin32.
# ---------------------------------------------------------------------------


def test_placeholder_messages_contains_label_placeholder() -> None:
    """Every PLACEHOLDER_MESSAGES entry must contain ``{label}``.

    Selecting a template and formatting it with ``.format(label=...)``
    is the core transition in ``_poll_attached_panes_for_death``. A
    missing placeholder would leave the formatted string unchanged and
    the operator would see the literal template instead of ``v0 ...``.
    """
    assert PLACEHOLDER_MESSAGES, "PLACEHOLDER_MESSAGES is empty"
    for template in PLACEHOLDER_MESSAGES:
        assert "{label}" in template, (
            f"PLACEHOLDER_MESSAGES entry {template!r} is missing the "
            f"'{{label}}' substitution placeholder"
        )


# ---------------------------------------------------------------------------
# Container state-transition tests — Linux-safe (monkeypatches Win32).
# ---------------------------------------------------------------------------


def _make_viewer() -> Any:
    """Construct a SelfPlayViewer instance — __init__ touches no Win32."""
    pytest.importorskip(
        "selfplay_viewer", reason="viewer extra (pygame) not installed"
    )
    from selfplay_viewer import SelfPlayViewer

    return SelfPlayViewer()


def test_placeholder_set_by_dead_hwnd_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polling a dead HWND removes the slot from ``_attached_panes`` and
    records a ``{label}``-substituted message in ``_placeholder_panes``.
    """
    from selfplay_viewer.container import AttachedPane, SelfPlayViewer

    viewer = _make_viewer()
    viewer._attached_panes[0] = AttachedPane(pid=1, hwnd=99999, label="v0")
    # Force the clock to fire on the first call.
    viewer._next_placeholder_check_at = 0.0

    monkeypatch.setattr(
        SelfPlayViewer,
        "_is_hwnd_alive",
        lambda self, hwnd: False,
    )

    viewer._poll_attached_panes_for_death()

    assert 0 not in viewer._attached_panes
    assert 0 in viewer._placeholder_panes
    message = viewer._placeholder_panes[0]
    assert isinstance(message, str)
    assert message.startswith("v0 "), (
        f"expected placeholder message to start with 'v0 ' "
        f"(label-substituted); got {message!r}"
    )


def test_placeholder_survives_when_hwnd_is_still_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polling a live HWND leaves ``_attached_panes`` untouched."""
    from selfplay_viewer.container import AttachedPane, SelfPlayViewer

    viewer = _make_viewer()
    viewer._attached_panes[0] = AttachedPane(pid=1, hwnd=99999, label="v0")
    viewer._next_placeholder_check_at = 0.0

    monkeypatch.setattr(
        SelfPlayViewer,
        "_is_hwnd_alive",
        lambda self, hwnd: True,
    )

    viewer._poll_attached_panes_for_death()

    assert 0 in viewer._attached_panes
    assert 0 not in viewer._placeholder_panes


def test_placeholder_cleared_by_attach_pane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``attach_pane`` clears a pre-existing placeholder entry for the slot.

    Exercises the ``on_game_start`` replacement scenario: a slot's
    previous pane died, we showed a placeholder, and the next game
    hands us a fresh HWND. The attach path must scrub the placeholder
    state so the dark overlay stops rendering over the new child.
    """
    from selfplay_viewer import reparent

    viewer = _make_viewer()
    viewer._placeholder_panes[0] = "v0 crashed into the void"

    # Stub the Win32 surface so the attach succeeds without a real HWND.
    monkeypatch.setattr(
        reparent, "find_hwnd_for_pid", lambda pid, timeout_s=15.0: 12345
    )
    monkeypatch.setattr(
        reparent, "attach_window", lambda hwnd, parent, rect: None
    )
    # ``attach_pane`` also reads the current container HWND; stub that
    # to avoid the pygame display probe.
    monkeypatch.setattr(
        type(viewer),
        "_current_container_hwnd",
        lambda self: 0xDEAD,
    )

    viewer.attach_pane(0, pid=111, label="v0")

    assert 0 not in viewer._placeholder_panes
    assert 0 in viewer._attached_panes


def test_placeholder_cleared_by_detach_pane() -> None:
    """``detach_pane`` on a placeholder-only slot clears the placeholder state.

    The slot isn't actually in ``_attached_panes`` (the HWND died), so
    ``detach_pane`` short-circuits the Win32 path — but must still
    clear the placeholder bookkeeping so a subsequent attach starts
    from a clean slate.
    """
    viewer = _make_viewer()
    viewer._placeholder_panes[0] = "v0 took a coffee break"
    assert 0 not in viewer._attached_panes  # precondition

    viewer.detach_pane(0)

    assert 0 not in viewer._placeholder_panes


def test_placeholder_survives_layout_change() -> None:
    """A layout change does not scrub ``_placeholder_panes``.

    ``_apply_layout_change`` detaches and re-attaches real panes, but
    placeholders are layout-independent bookkeeping. The ``_paint_frame``
    reads the current ``(bar, size)`` so the placeholder naturally
    repositions to the new pane rect.

    We stub ``pygame.display.set_mode`` + the container HWND probe so
    this runs without a real pygame session.
    """
    pytest.importorskip(
        "pygame", reason="viewer extra (pygame) not installed"
    )

    viewer = _make_viewer()
    viewer._placeholder_panes[0] = "v0 surrendered to Brood War"
    # No real attached panes — we want to exercise the re-layout path
    # without also exercising the Win32 reparent.
    assert not viewer._attached_panes

    # The Step 3 apply_layout_change helper drains _attached_panes,
    # calls pygame.display.set_mode (which needs pygame.init'd display),
    # reads the container HWND, and reloads the background. Stub all of
    # that out so the helper is exercised as a pure bookkeeping
    # operation.
    pygame.init()
    try:
        # Build a dummy screen surface for set_mode to return. We
        # monkeypatch the function rather than calling set_mode on a
        # headless display.
        dummy_surface = pygame.Surface((100, 100))

        def _fake_set_mode(size: tuple[int, int]) -> pygame.Surface:
            return dummy_surface

        original_set_mode = pygame.display.set_mode
        pygame.display.set_mode = _fake_set_mode  # type: ignore[assignment]
        try:
            # Also stub the container HWND probe + background loader +
            # background-path resolver so the test doesn't depend on
            # img_backgrounds/ having any PNGs on disk.
            from pathlib import Path

            viewer._current_container_hwnd = (  # type: ignore[method-assign]
                lambda: 0xBEEF
            )
            viewer._load_background = (  # type: ignore[method-assign]
                lambda path, target: dummy_surface
            )
            viewer._resolve_background_path_for = (  # type: ignore[method-assign]
                lambda _bar, _size: Path("stub.png")
            )

            viewer._apply_layout_change(new_bar="side", new_size="small")
        finally:
            pygame.display.set_mode = original_set_mode  # type: ignore[assignment]
    finally:
        pygame.quit()
        from selfplay_viewer.overlay import reset_font_cache

        reset_font_cache()

    # Placeholder state survived the layout transition.
    assert viewer._placeholder_panes == {0: "v0 surrendered to Brood War"}
    assert viewer.bar == "side"
    assert viewer.size == "small"


def test_placeholder_poll_respects_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second poll inside the interval is a no-op (no is_alive call).

    Arms the rate-limit by running one scan with ``_is_hwnd_alive``
    returning ``True`` (so ``_attached_panes`` stays populated), then
    flips the probe to count calls and asserts the second invocation
    short-circuits before touching ``_is_hwnd_alive``.
    """
    from selfplay_viewer.container import AttachedPane, SelfPlayViewer

    viewer = _make_viewer()
    viewer._attached_panes[0] = AttachedPane(pid=1, hwnd=99999, label="v0")
    viewer._next_placeholder_check_at = 0.0  # first poll fires

    # First poll — liveness says alive, pane stays attached, clock bumps.
    monkeypatch.setattr(
        SelfPlayViewer,
        "_is_hwnd_alive",
        lambda self, hwnd: True,
    )
    viewer._poll_attached_panes_for_death()
    assert 0 in viewer._attached_panes
    # Clock should have advanced past now — the next call must no-op.
    assert viewer._next_placeholder_check_at > 0.0

    # Second poll — swap in a tracker; it MUST NOT be called.
    call_count = {"n": 0}

    def _tracker(self: SelfPlayViewer, hwnd: int) -> bool:
        call_count["n"] += 1
        return False  # would transition to placeholder if ever called

    monkeypatch.setattr(SelfPlayViewer, "_is_hwnd_alive", _tracker)
    viewer._poll_attached_panes_for_death()

    assert call_count["n"] == 0, (
        "_poll_attached_panes_for_death called _is_hwnd_alive before the "
        "next interval tick — the rate-limit guard did not fire"
    )
    # Pane should still be attached because we never polled.
    assert 0 in viewer._attached_panes


def test_placeholder_detach_all_panes_clears_placeholder_state() -> None:
    """``_detach_all_panes`` wipes placeholder bookkeeping on teardown.

    Placeholder state is tied to the pygame session's lifetime — a
    subsequent ``run`` / ``run_with_batch`` on the same viewer must
    start with a clean slate so stale messages don't resurface.
    """
    viewer = _make_viewer()
    viewer._placeholder_panes[0] = "v0 forgot how to SC2"
    viewer._placeholder_panes[1] = "v1 rage-quit"

    viewer._detach_all_panes()

    assert viewer._placeholder_panes == {}
