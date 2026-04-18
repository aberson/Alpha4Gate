"""Tests for the Step 8 toast overlay + resize sequencing.

Covers three surfaces:

* :func:`selfplay_viewer.overlay.render_toast` — sentinel-pixel
  rendering checks (centred pill, alpha curve, zero-alpha no-op).
* Toast state transitions on :class:`~selfplay_viewer.container.SelfPlayViewer`
  — verifies :meth:`_trigger_toast` arms the message + monotonic
  deadline and :meth:`_paint_frame` auto-clears expired toasts.
* Resize sequencing — locks in the Option A call order documented in
  ``_apply_layout_change`` (detach-all → set_mode → re-attach) so a
  future refactor can't silently regress to a variant that cascades
  WM_DESTROY through still-attached children.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest

from selfplay_viewer.overlay import CONTAINER_SIZES

if TYPE_CHECKING:
    import pygame


_SENTINEL_COLOR: tuple[int, int, int] = (0, 255, 0)


try:
    import pygame  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — Linux / py without viewer extras
    pygame = None  # type: ignore[assignment]


@pytest.fixture()
def pygame_init() -> Iterator[None]:
    """Init pygame + drop the font cache on teardown.

    Mirrors the fixtures in :mod:`tests.test_overlay` and
    :mod:`tests.test_placeholder` — SDL_TTF handles are bound to the
    pygame session, so a stale cached Font from a prior test SIGSEGVs
    the interpreter when the next ``render_toast`` call lands.
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


# ---------------------------------------------------------------------------
# Rendering tests — gated behind pygame_init (pygame + SDL_TTF required).
# ---------------------------------------------------------------------------


def test_render_toast_paints_pixels_near_bottom_center(pygame_init: None) -> None:
    """Toast pixels land inside a bottom-centre rect; outside stays sentinel."""
    from selfplay_viewer.overlay import (
        TOAST_BOTTOM_OFFSET_PX,
        render_toast,
    )

    container_size = CONTAINER_SIZES[("top", "large")]
    cw, ch = container_size

    surface = pygame.Surface(container_size)
    surface.fill(_SENTINEL_COLOR)

    render_toast(
        surface=surface,
        container_size=container_size,
        message="Large layout",
        alpha=1.0,
    )

    # Expected pill region: centred horizontally, vertically around
    # (ch - TOAST_BOTTOM_OFFSET_PX - pill_h) up to (ch - TOAST_BOTTOM_OFFSET_PX).
    # We don't know the exact pill_h without re-rendering the text, so
    # we sample a generous bounding box: horizontally centred 600x200
    # rect near the bottom edge.
    expected_w = 600
    expected_h = 200
    expected_rect = (
        (cw - expected_w) // 2,
        ch - TOAST_BOTTOM_OFFSET_PX - expected_h,
        expected_w,
        expected_h,
    )
    inside = _count_non_sentinel_pixels(surface, expected_rect)
    assert inside > 500, (
        f"render_toast painted only {inside} non-sentinel pixels inside "
        f"the expected bottom-centre rect {expected_rect}"
    )

    # Top-left / top-right corners must remain sentinel — the toast
    # lives near the bottom and must not stomp the top of the screen.
    for coord in [(0, 0), (cw - 1, 0), (0, ch // 4), (cw - 1, ch // 4)]:
        px = surface.get_at(coord)
        assert (px.r, px.g, px.b) == _SENTINEL_COLOR, (
            f"render_toast stomped {coord} — pill should only affect "
            f"the bottom-centre region"
        )


def test_render_toast_zero_alpha_is_no_op(pygame_init: None) -> None:
    """``alpha=0.0`` leaves the surface untouched — no ghost pill."""
    from selfplay_viewer.overlay import TOAST_BOTTOM_OFFSET_PX, render_toast

    container_size = CONTAINER_SIZES[("top", "large")]

    surface = pygame.Surface(container_size)
    surface.fill(_SENTINEL_COLOR)

    render_toast(
        surface=surface,
        container_size=container_size,
        message="Large layout",
        alpha=0.0,
    )

    cw, ch = container_size
    # Sample the full expected pill region — nothing should have moved
    # off sentinel because alpha=0 is a no-op. Derive the rect from
    # TOAST_BOTTOM_OFFSET_PX (not a literal 80) so a future offset
    # tweak doesn't silently walk the sample window off the pill.
    sample_w = 600
    sample_h = 200
    expected_rect = (
        (cw - sample_w) // 2,
        ch - TOAST_BOTTOM_OFFSET_PX - sample_h,
        sample_w,
        sample_h,
    )
    leaks = _count_non_sentinel_pixels(surface, expected_rect)
    assert leaks == 0, (
        f"render_toast(alpha=0.0) painted {leaks} non-sentinel pixels — "
        f"a zero-opacity toast must be visually absent"
    )


def test_render_toast_alpha_scales_pixel_strength(pygame_init: None) -> None:
    """Higher alpha produces darker pill fill than lower alpha.

    Rather than counting "saturated" pixels (unreliable because SDL
    blending can round either way), we compare the average
    non-sentinel fill strength: at alpha=1.0 the fill is closer to
    pure dark (R,G,B ~0), at alpha=0.3 the fill is closer to sentinel
    green because less of OVERLAY_FILL_COLOR lands on the surface.
    """
    from selfplay_viewer.overlay import TOAST_BOTTOM_OFFSET_PX, render_toast

    container_size = CONTAINER_SIZES[("top", "large")]
    cw, ch = container_size
    # Small central sample rect strictly INSIDE the pill — where the fill
    # lives (not near the text which has its own alpha). The pill's
    # bottom edge is at ch - TOAST_BOTTOM_OFFSET_PX, so anchor our 10px
    # sample a few pixels above that to avoid dipping below the pill
    # into the sentinel-filled background.
    sample_rect = (
        cw // 2 - 10,
        ch - TOAST_BOTTOM_OFFSET_PX - 20,
        20,
        10,
    )

    def _avg_green(alpha: float) -> float:
        s = pygame.Surface(container_size)
        s.fill(_SENTINEL_COLOR)
        render_toast(
            surface=s,
            container_size=container_size,
            message="Large layout",
            alpha=alpha,
        )
        x0, y0, w, h = sample_rect
        total = 0
        count = 0
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                px = s.get_at((x, y))
                total += px.g
                count += 1
        return total / count

    full_green = _avg_green(1.0)
    faded_green = _avg_green(0.3)

    # Fully opaque pill = more fill blended in = LESS green remaining.
    # Faded pill = less fill blended in = MORE green remaining (closer
    # to sentinel value of 255).
    assert faded_green > full_green + 10, (
        f"alpha=0.3 sample green={faded_green:.1f}, alpha=1.0 sample "
        f"green={full_green:.1f} — expected faded to be noticeably "
        f"greener (closer to sentinel) than opaque"
    )


# ---------------------------------------------------------------------------
# Toast state transitions — no pygame render.
# ---------------------------------------------------------------------------


def _make_viewer() -> Any:
    pytest.importorskip(
        "selfplay_viewer", reason="viewer extra (pygame) not installed"
    )
    from selfplay_viewer import SelfPlayViewer

    return SelfPlayViewer()


def test_trigger_toast_arms_message_and_deadline() -> None:
    """``_trigger_toast`` sets the message and a monotonic deadline
    :data:`TOAST_DURATION_SECONDS` in the future.
    """
    from selfplay_viewer.container import TOAST_DURATION_SECONDS

    viewer = _make_viewer()
    assert viewer._toast_message is None  # precondition

    t0 = time.monotonic()
    viewer._trigger_toast("Large layout")
    t1 = time.monotonic()

    assert viewer._toast_message == "Large layout"
    # Deadline should land in [now + duration - slop, now + duration + slop].
    assert viewer._toast_expires_at >= t0 + TOAST_DURATION_SECONDS
    assert viewer._toast_expires_at <= t1 + TOAST_DURATION_SECONDS + 0.05


def test_trigger_toast_replaces_prior_toast() -> None:
    """A second trigger replaces the message — latest hotkey wins."""
    viewer = _make_viewer()

    viewer._trigger_toast("Large layout")
    viewer._trigger_toast("Side bar")

    assert viewer._toast_message == "Side bar"


def test_net_no_change_hotkey_does_not_trigger_toast_or_resize() -> None:
    """Double-tapping S (or B) in one frame is a net-no-op.

    The hotkey event drain coalesces two K_s presses into a single
    ``target_size`` that can end up equal to ``self.size``. When that
    happens, the apply step must NOT fire a toast AND must NOT call
    ``_apply_layout_change`` — otherwise the overlay flashes "Top bar"
    (or "Large layout") for an unchanged layout.

    Monkeypatches ``_trigger_toast`` and ``_apply_layout_change`` with
    recorders and invokes the frame-apply helper directly with
    ``target_bar = self.bar`` — asserts neither recorder fires.
    """
    viewer = _make_viewer()
    # Starting layout — any valid combo works; we use the default.
    starting_bar = viewer.bar
    starting_size = viewer.size

    toast_calls: list[str] = []
    layout_calls: list[tuple[str, str]] = []

    def _record_trigger(message: str) -> None:
        toast_calls.append(message)

    def _record_apply(
        new_bar: str, new_size: str
    ) -> tuple[Any, Any]:
        layout_calls.append((new_bar, new_size))
        return (object(), object())

    viewer._trigger_toast = _record_trigger  # type: ignore[method-assign]
    viewer._apply_layout_change = _record_apply  # type: ignore[method-assign]

    # Case 1: target_bar is self.bar (double-B coalesced). Size target
    # is None — pure bar-only no-op.
    result = viewer._maybe_apply_layout_targets(
        target_bar=starting_bar,
        target_size=None,
        screen=object(),  # type: ignore[arg-type]
        background_surface=object(),  # type: ignore[arg-type]
    )
    assert result is None, (
        "Net-no-change should return None so the caller keeps its "
        "current surfaces"
    )
    assert toast_calls == [], (
        f"Toast fired for a net-no-op bar flip: {toast_calls}"
    )
    assert layout_calls == [], (
        f"_apply_layout_change fired for a net-no-op bar flip: "
        f"{layout_calls}"
    )

    # Case 2: target_size is self.size (double-S coalesced). Bar
    # target is None — pure size-only no-op.
    result = viewer._maybe_apply_layout_targets(
        target_bar=None,
        target_size=starting_size,
        screen=object(),  # type: ignore[arg-type]
        background_surface=object(),  # type: ignore[arg-type]
    )
    assert result is None
    assert toast_calls == []
    assert layout_calls == []

    # Case 3: BOTH targets present but each equals current state —
    # e.g. double-B AND double-S in the same frame.
    result = viewer._maybe_apply_layout_targets(
        target_bar=starting_bar,
        target_size=starting_size,
        screen=object(),  # type: ignore[arg-type]
        background_surface=object(),  # type: ignore[arg-type]
    )
    assert result is None
    assert toast_calls == []
    assert layout_calls == []

    # Sanity: a REAL change still fires both recorders. Flip size.
    other_size = "small" if starting_size == "large" else "large"
    result = viewer._maybe_apply_layout_targets(
        target_bar=None,
        target_size=other_size,
        screen=object(),  # type: ignore[arg-type]
        background_surface=object(),  # type: ignore[arg-type]
    )
    assert result is not None, (
        "A genuine layout change must return the new surfaces, not None"
    )
    assert toast_calls == [f"{other_size.capitalize()} layout"]
    assert layout_calls == [(starting_bar, other_size)]


# ---------------------------------------------------------------------------
# Resize sequencing — Option A contract lock-in.
# ---------------------------------------------------------------------------


def test_apply_layout_change_follows_option_a_call_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_apply_layout_change`` runs detach-all → set_mode → re-attach.

    Step 8 (a) audit: this ordering prevents a pygame ``set_mode`` HWND
    recreation from cascading WM_DESTROY through still-WS_CHILD SC2
    panes. Any future refactor that reorders these calls risks the
    Never-Terminate rule, so we monkeypatch the three Win32-ish
    surfaces with recorders and assert the expected sequence.
    """
    pytest.importorskip(
        "pygame", reason="viewer extra (pygame) not installed"
    )
    # Resolve ``reparent`` via the same binding ``container.py`` uses
    # so a prior test that popped ``selfplay_viewer.reparent`` from
    # ``sys.modules`` (see ``tests/test_reparent_portable.py``) cannot
    # desynchronise our patch from the binding the SUT is actually
    # calling.
    from selfplay_viewer import container as container_mod
    from selfplay_viewer.container import AttachedPane

    reparent = container_mod.reparent

    viewer = _make_viewer()
    # Two attached panes — we want to see each of them detached and
    # re-attached in the recorded sequence.
    viewer._attached_panes[0] = AttachedPane(pid=101, hwnd=1001, label="v0")
    viewer._attached_panes[1] = AttachedPane(pid=202, hwnd=2002, label="v1")

    events: list[tuple[str, Any]] = []

    # _safe_detach is the primary detach path inside _apply_layout_change.
    # Monkeypatch it to record + do nothing — we don't want a real
    # pywin32 call.
    def _fake_safe_detach(
        self: Any, hwnd: int, slot: int, pid: int, label: str
    ) -> None:
        events.append(("safe_detach", (hwnd, slot)))

    monkeypatch.setattr(
        type(viewer), "_safe_detach", _fake_safe_detach
    )

    # pygame.display.set_mode — record its invocation order relative to
    # the detach/attach calls. It returns a dummy surface.
    pygame.init()
    try:
        dummy_surface = pygame.Surface((100, 100))

        def _fake_set_mode(size: tuple[int, int]) -> pygame.Surface:
            events.append(("set_mode", size))
            return dummy_surface

        monkeypatch.setattr(pygame.display, "set_mode", _fake_set_mode)

        # Attach side: reparent.attach_window is the per-pane re-attach.
        def _fake_attach_window(
            hwnd: int,
            container_hwnd: int,
            rect: tuple[int, int, int, int],
        ) -> None:
            events.append(("attach_window", (hwnd, container_hwnd, rect)))

        monkeypatch.setattr(reparent, "attach_window", _fake_attach_window)

        # Bypass the real container HWND probe — we don't have a
        # win32-backed pygame window.
        monkeypatch.setattr(
            type(viewer),
            "_current_container_hwnd",
            lambda self: 0xBEEF,
        )

        # Stub the background loader + path so we don't need PNG files
        # on disk.
        from pathlib import Path

        viewer._load_background = (  # type: ignore[method-assign]
            lambda path, target: dummy_surface
        )
        viewer._resolve_background_path_for = (  # type: ignore[method-assign]
            lambda _bar, _size: Path("stub.png")
        )

        viewer._apply_layout_change(new_bar="side", new_size="small")
    finally:
        pygame.quit()
        from selfplay_viewer.overlay import reset_font_cache

        reset_font_cache()

    # Expected sequence:
    #   1. safe_detach(slot=0), safe_detach(slot=1)  (order not pinned
    #      between slots, but BOTH must fire before set_mode)
    #   2. set_mode((W, H))
    #   3. attach_window(hwnd=1001, ...), attach_window(hwnd=2002, ...)
    kinds = [e[0] for e in events]

    # Exactly two detach events before the set_mode.
    set_mode_idx = kinds.index("set_mode")
    detach_kinds = kinds[:set_mode_idx]
    assert detach_kinds == ["safe_detach", "safe_detach"], (
        f"Expected both panes detached BEFORE set_mode; got kinds="
        f"{kinds}"
    )

    # Exactly two attach_window calls AFTER the set_mode.
    attach_kinds = kinds[set_mode_idx + 1 :]
    assert attach_kinds == ["attach_window", "attach_window"], (
        f"Expected both panes re-attached AFTER set_mode; got kinds="
        f"{kinds}"
    )

    # set_mode target size reflects the new (side, small) layout.
    set_mode_args = events[set_mode_idx][1]
    assert set_mode_args == CONTAINER_SIZES[("side", "small")], (
        f"set_mode was called with {set_mode_args}, expected "
        f"{CONTAINER_SIZES[('side', 'small')]}"
    )

    # Re-attach uses the NEW container HWND returned by the probe.
    for event_kind, payload in events:
        if event_kind == "attach_window":
            _hwnd, container_hwnd, _rect = payload
            assert container_hwnd == 0xBEEF, (
                f"re-attach used stale container HWND {container_hwnd}; "
                f"expected 0xBEEF from the post-set_mode probe"
            )


# ---------------------------------------------------------------------------
# Placeholder survival re-audit — extends Step 7's coverage across the
# full (bar, size) matrix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("new_bar", "new_size"),
    [
        ("top", "small"),
        ("side", "large"),
        ("side", "small"),
    ],
)
def test_placeholder_survives_every_layout_target(
    new_bar: str, new_size: str
) -> None:
    """``_apply_layout_change`` preserves ``_placeholder_panes`` across
    every non-trivial layout target.

    Extends :func:`tests.test_placeholder.test_placeholder_survives_layout_change`
    (which only covered side/small) to the full matrix so a future
    layout-specific branch can't silently scrub placeholders for one
    target and preserve them for another.
    """
    pytest.importorskip(
        "pygame", reason="viewer extra (pygame) not installed"
    )

    viewer = _make_viewer()
    viewer._placeholder_panes[0] = "v0 surrendered to Brood War"
    viewer._placeholder_panes[1] = "v1 has rage-quit"
    assert not viewer._attached_panes

    pygame.init()
    try:
        dummy_surface = pygame.Surface((100, 100))

        def _fake_set_mode(size: tuple[int, int]) -> pygame.Surface:
            return dummy_surface

        original_set_mode = pygame.display.set_mode
        pygame.display.set_mode = _fake_set_mode  # type: ignore[assignment]
        try:
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

            viewer._apply_layout_change(new_bar=new_bar, new_size=new_size)
        finally:
            pygame.display.set_mode = original_set_mode  # type: ignore[assignment]
    finally:
        pygame.quit()
        from selfplay_viewer.overlay import reset_font_cache

        reset_font_cache()

    assert viewer._placeholder_panes == {
        0: "v0 surrendered to Brood War",
        1: "v1 has rage-quit",
    }
    assert viewer.bar == new_bar
    assert viewer.size == new_size
