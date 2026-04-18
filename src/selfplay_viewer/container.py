"""``SelfPlayViewer`` — pygame container window for two SC2 panes.

Step 3 surface: opens a themed pygame window sized per the
``(bar, size)`` layout, blits a background PNG, and either paints grey
placeholder rectangles or hosts real Win32 child windows in each of
the two pane positions. Adds public ``attach_pane`` / ``detach_pane``
methods plus ``S`` / ``B`` hotkeys for live layout toggling.

pygame is imported lazily inside methods so that
``from selfplay_viewer import SelfPlayViewer`` succeeds on Linux CI
where pygame is not installed (the ``[viewer]`` extra is Windows-only
in practice — see ``pyproject.toml``). pywin32 is reached only via the
``selfplay_viewer.reparent`` module, which has its own lazy guards, so
the platform check cascades through that surface.
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from selfplay_viewer import reparent
from selfplay_viewer.backgrounds import pick_background
from selfplay_viewer.overlay import (
    CONTAINER_SIZES,
    OVERLAY_RECTS,
    PANE_RECTS,
)

if TYPE_CHECKING:
    import pygame  # For types only — real import is inside .run()

_VALID_BARS: Final[frozenset[str]] = frozenset({"top", "side"})
_VALID_SIZES: Final[frozenset[str]] = frozenset({"large", "small"})
_VALID_SLOTS: Final[frozenset[int]] = frozenset({0, 1})

_MAIN_THREAD_MSG = (
    "SelfPlayViewer attach/detach must be called from the main thread "
    "(Win32 HWND manipulation is not thread-safe)"
)


def _ensure_main_thread() -> None:
    """Raise ``RuntimeError`` when called off the main thread."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError(_MAIN_THREAD_MSG)


#: Grey placeholder colour for the SC2 panes (RGB).
PLACEHOLDER_COLOR: Final[tuple[int, int, int]] = (0xAA, 0xAA, 0xAA)

#: Border colour for the overlay stub (RGB).
OVERLAY_BORDER_COLOR: Final[tuple[int, int, int]] = (0xFF, 0xFF, 0xFF)

#: Semi-transparent dark fill for the overlay stub (RGBA).
OVERLAY_FILL_COLOR: Final[tuple[int, int, int, int]] = (0x00, 0x00, 0x00, 0x80)

#: Border thickness for the overlay stub (px).
OVERLAY_BORDER_PX: Final[int] = 4

#: Target frame rate for the demo loop.
TARGET_FPS: Final[int] = 30


@dataclass(frozen=True)
class AttachedPane:
    """Bookkeeping record for one reparented child window.

    Stored in ``SelfPlayViewer._attached_panes`` keyed by slot index
    (0 == p1, 1 == p2). The dataclass is frozen because the binding
    is replaced wholesale on re-attach rather than mutated in place.
    """

    pid: int
    hwnd: int
    label: str


class SelfPlayViewer:
    """Themed pygame container for two SC2 panes.

    Parameters
    ----------
    bar:
        Where the stats overlay sits — ``"top"`` for a banner, ``"side"``
        for a vertical right-edge bar.
    size:
        SC2 pane preset — ``"large"`` is 1024x768, ``"small"`` is
        960x720.
    background:
        Either ``"random"`` (default) or a derived key from
        ``selfplay_viewer.backgrounds.list_backgrounds``.
    """

    def __init__(
        self,
        bar: str = "top",
        size: str = "large",
        background: str = "random",
    ) -> None:
        if bar not in _VALID_BARS:
            raise ValueError(
                f"bar must be one of {sorted(_VALID_BARS)}, got {bar!r}"
            )
        if size not in _VALID_SIZES:
            raise ValueError(
                f"size must be one of {sorted(_VALID_SIZES)}, got {size!r}"
            )
        self.bar: str = bar
        self.size: str = size
        self.background: str = background
        #: Slot-keyed registry of reparented child windows. Empty until
        #: ``attach_pane`` is called.
        self._attached_panes: dict[int, AttachedPane] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def attach_pane(self, slot: int, pid: int, label: str) -> None:
        """Reparent the visible top-level HWND owned by *pid* into pane *slot*.

        Parameters
        ----------
        slot:
            ``0`` for the p1 pane, ``1`` for the p2 pane.
        pid:
            Process ID whose visible top-level window will be reparented
            (e.g. an SC2 client PID).
        label:
            Human-readable identifier used in error messages and stored
            on the :class:`AttachedPane` record for downstream consumers.

        Raises
        ------
        ValueError
            If *slot* is not 0 or 1.
        RuntimeError
            If no visible top-level window owned by *pid* appears within
            :data:`reparent.find_hwnd_for_pid`'s default timeout, or if
            the container window has not been initialised yet (i.e. the
            method is called before ``run`` has set up the display).

        Notes
        -----
        Idempotent replace: calling ``attach_pane(slot, pid, ...)`` twice
        on the same slot detaches the previously attached child first
        and then attaches the new one. This keeps the slot's invariants
        (single child per slot, no orphaned WS_CHILD handles) intact.
        """
        _ensure_main_thread()
        if slot not in _VALID_SLOTS:
            raise ValueError(
                f"slot must be one of {sorted(_VALID_SLOTS)}, got {slot!r}"
            )

        # Same-PID re-attach: a window already wired into this slot is a
        # WS_CHILD of the container, so ``find_hwnd_for_pid`` cannot see
        # it (the enumeration only returns top-level windows). Restore
        # the prior pane to top-level FIRST, then look up the HWND.
        if slot in self._attached_panes and self._attached_panes[slot].pid == pid:
            prior = self._attached_panes.pop(slot)
            self._safe_detach(prior.hwnd, slot, prior.pid, prior.label)

        container_hwnd = self._current_container_hwnd()

        child_hwnd = reparent.find_hwnd_for_pid(pid)
        if child_hwnd is None:
            raise RuntimeError(
                f"attach_pane: no visible top-level window for "
                f"pid={pid} label={label!r} (find_hwnd_for_pid timed out)"
            )

        # Idempotent replace — restore any prior child before stomping
        # over the slot. We do NOT short-circuit when the same HWND is
        # already present because the caller may want to refresh the
        # reparent (e.g. after a layout change race).
        if slot in self._attached_panes:
            prior = self._attached_panes.pop(slot)
            self._safe_detach(prior.hwnd, slot, prior.pid, prior.label)

        pane_rect = PANE_RECTS[(self.bar, self.size)][slot]
        try:
            reparent.attach_window(child_hwnd, container_hwnd, pane_rect)
        except Exception:
            # Roll back the partially-styled new child so it doesn't
            # become an orphan WS_CHILD with no parent (chrome-less,
            # uncloseable). Best-effort: swallow rescue exceptions so we
            # surface the original failure to the caller. The prior
            # pane (if any) is already gone — that's an accepted side
            # effect of the replace contract.
            try:
                reparent.detach_window(child_hwnd)
            except Exception as rescue_exc:  # noqa: BLE001
                print(
                    f"[selfplay_viewer] warning: rollback detach of "
                    f"hwnd={child_hwnd} pid={pid} label={label!r} after "
                    f"failed attach also failed: {rescue_exc}",
                    file=sys.stderr,
                )
            raise
        self._attached_panes[slot] = AttachedPane(
            pid=pid, hwnd=child_hwnd, label=label
        )

    def detach_pane(self, slot: int) -> None:
        """Restore the pane *slot* child to a top-level window.

        Parameters
        ----------
        slot:
            ``0`` for the p1 pane, ``1`` for the p2 pane.

        Raises
        ------
        ValueError
            If *slot* is not 0 or 1.

        Notes
        -----
        Silent no-op when *slot* is not currently attached. Never kills
        the owning process — see ``feedback_sc2_process_management.md``.
        """
        _ensure_main_thread()
        if slot not in _VALID_SLOTS:
            raise ValueError(
                f"slot must be one of {sorted(_VALID_SLOTS)}, got {slot!r}"
            )
        if slot not in self._attached_panes:
            return
        # Detach FIRST, pop on success — if both the primary and rescue
        # detach paths fail, the slot stays in the dict so a subsequent
        # _detach_all_panes (or operator-driven retry) has another shot.
        pane = self._attached_panes[slot]
        self._safe_detach(pane.hwnd, slot, pane.pid, pane.label)
        del self._attached_panes[slot]

    def run(self, attach_pids: list[int] | None = None) -> None:
        """Open the window, paint the demo scene, and pump events.

        Parameters
        ----------
        attach_pids:
            Optional pair ``[pid0, pid1]`` of process IDs to attach into
            slots 0 and 1 immediately after the display is created and
            before the main loop starts. The list MUST contain exactly
            two PIDs when provided.

        Blocks until the user closes the window. pygame is imported here
        so that callers on Linux can import ``SelfPlayViewer`` without
        the ``[viewer]`` extras installed (e.g. for type-only inspection
        or tests that never call ``run``).

        Hotkeys
        -------
        ``ESC``
            Close the window (triggers detach-all then ``pygame.quit``).
        ``S``
            Toggle SC2 pane size between ``large`` and ``small``.
        ``B``
            Toggle stats bar between ``top`` and ``side``.
        """
        # Lazy import — pygame may not be installed on non-Windows.
        import pygame

        if attach_pids is not None and len(attach_pids) != 2:
            raise ValueError(
                f"attach_pids must contain exactly 2 PIDs, got {len(attach_pids)}"
            )

        bg_path = self._resolve_background_path()

        pygame.init()
        try:
            width, height = CONTAINER_SIZES[(self.bar, self.size)]
            screen = pygame.display.set_mode((width, height))
            pygame.display.set_caption("Alpha4Gate self-play viewer")

            background_surface = self._load_background(bg_path, (width, height))

            if attach_pids is not None:
                self.attach_pane(0, attach_pids[0], f"pid:{attach_pids[0]}")
                self.attach_pane(1, attach_pids[1], f"pid:{attach_pids[1]}")

            clock = pygame.time.Clock()
            running = True
            while running:
                # Coalesce S/B hotkey events: pygame.event.get() drains
                # the entire queue, so two K_s presses in one frame would
                # otherwise trigger two _apply_layout_change calls (double
                # set_mode + double reparent). Track the LATEST target
                # values across the drain and apply at most once per frame.
                target_bar: str | None = None
                target_size: str | None = None
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            running = False
                        elif event.key == pygame.K_s:
                            base_size = (
                                target_size if target_size is not None else self.size
                            )
                            target_size = "small" if base_size == "large" else "large"
                        elif event.key == pygame.K_b:
                            base_bar = (
                                target_bar if target_bar is not None else self.bar
                            )
                            target_bar = "side" if base_bar == "top" else "top"
                if target_bar is not None or target_size is not None:
                    screen, background_surface = self._apply_layout_change(
                        new_bar=target_bar if target_bar is not None else self.bar,
                        new_size=target_size if target_size is not None else self.size,
                    )

                self._paint_frame(screen, background_surface)
                pygame.display.flip()
                clock.tick(TARGET_FPS)
        finally:
            # CRITICAL: detach all child HWNDs BEFORE pygame.quit() so
            # the container's child-window cascade does not destroy
            # SC2 (or any other attached process's) window. See the
            # Step 2 code-gauntlet finding documented in the plan.
            self._detach_all_panes()
            pygame.quit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_background_path(self) -> Path:
        """Resolve the configured background key to an on-disk PNG path."""
        return pick_background(self.background)

    def _current_container_hwnd(self) -> int:
        """Return the live HWND of the pygame container window.

        Re-read on every call so the resize path (which may invalidate
        the previous HWND across ``set_mode``) always sees the current
        value. Raises ``RuntimeError`` if the display has not been
        initialised or if the HWND we read back is not a valid window
        (e.g. after a destruction we missed).
        """
        import pygame
        import win32gui  # lazy — Windows-only

        if not pygame.display.get_init():
            raise RuntimeError(
                "container HWND requested before pygame.display.init()"
            )
        info = pygame.display.get_wm_info()
        hwnd = int(info["window"])
        if not win32gui.IsWindow(hwnd):
            raise RuntimeError(
                f"container HWND {hwnd} is not a valid window"
            )
        return hwnd

    def _safe_detach(self, hwnd: int, slot: int, pid: int, label: str) -> None:
        """Detach a child HWND with a last-resort rescue.

        Primary path: :func:`reparent.detach_window` (full top-level
        restoration — clears WS_CHILD, restores WS_OVERLAPPEDWINDOW,
        SWP_FRAMECHANGED, then ``SetParent(hwnd, 0)``).

        Rescue path: bare ``SetParent(hwnd, 0)`` — the single Win32 call
        that breaks the child-window cascade. Without this, an exception
        from the primary path leaves the HWND as a WS_CHILD of the
        container; ``pygame.quit()`` then destroys the container and
        cascades WM_DESTROY through every orphaned child — killing the
        owning process's window. That is the exact failure the
        Never-Terminate hard rule forbids (Step 2 gauntlet finding,
        feedback_sc2_process_management.md).

        Both failures are logged loudly to stderr. If both Win32 calls
        fail this method re-raises so the caller can decide how to
        handle a truly stuck HWND; ``_detach_all_panes`` catches it.
        """
        import win32gui  # lazy — Windows-only

        try:
            reparent.detach_window(hwnd)
        except Exception as exc:  # noqa: BLE001
            try:
                win32gui.SetParent(hwnd, 0)  # last-resort cascade breaker
                print(
                    f"[selfplay_viewer] detach_window failed for slot "
                    f"{slot} (pid={pid} label={label!r}); fell back to "
                    f"SetParent(0): {exc}",
                    file=sys.stderr,
                )
            except Exception as rescue_exc:  # noqa: BLE001
                print(
                    f"[selfplay_viewer] ORPHAN WS_CHILD: slot {slot} "
                    f"(pid={pid} label={label!r}) could not be detached; "
                    f"pygame.quit() cascade will destroy this window. "
                    f"Primary: {exc}; rescue: {rescue_exc}",
                    file=sys.stderr,
                )
                raise

    def _apply_layout_change(
        self,
        new_bar: str,
        new_size: str,
    ) -> tuple[pygame.Surface, pygame.Surface]:
        """Resize the container and re-attach child panes to the new layout.

        Parameters
        ----------
        new_bar:
            Target ``bar`` value (``"top"`` or ``"side"``).
        new_size:
            Target ``size`` value (``"large"`` or ``"small"``).

        Returns
        -------
        tuple[pygame.Surface, pygame.Surface]
            ``(screen, background_surface)`` for the caller's locals.

        Notes
        -----
        Uniform sequence: detach EVERY attached pane to top-level FIRST
        (so a fresh container HWND across ``set_mode`` cannot cascade
        WM_DESTROY through still-WS_CHILD orphans), THEN call
        ``pygame.display.set_mode``, THEN re-attach every snapshot entry
        against the new container HWND. Per-pane re-attach failures fall
        back to ``_safe_detach`` so the child stays top-level rather than
        in a broken half-reparent.

        ``self.bar`` / ``self.size`` are committed only on full success.
        On any exception both stay at their pre-call values and the
        exception propagates to the caller.
        """
        import pygame

        # Snapshot the slot dict BEFORE we touch anything — we need a
        # stable record even if exceptions interleave with attach/detach.
        snapshot = list(self._attached_panes.items())

        # Detach all current panes to top-level so set_mode (which may
        # destroy and recreate the container HWND) cannot cascade
        # WM_DESTROY through children that are still WS_CHILD-bound to
        # the dying container. _safe_detach handles primary + rescue.
        for slot, pane in snapshot:
            self._safe_detach(pane.hwnd, slot, pane.pid, pane.label)
        # All slots are conceptually empty now — clear the dict so the
        # re-attach loop can rebuild it from the snapshot. (Slots whose
        # _safe_detach raised already broke out via the re-raise; we
        # only get here if every detach landed cleanly or the rescue
        # succeeded.)
        self._attached_panes.clear()

        new_container_size = CONTAINER_SIZES[(new_bar, new_size)]
        screen = pygame.display.set_mode(new_container_size)
        new_container_hwnd = self._current_container_hwnd()

        background_surface = self._load_background(
            self._resolve_background_path_for(new_bar, new_size),
            new_container_size,
        )

        # Re-attach every snapshot pane to its new rect under the new
        # container HWND. A re-attach failure rolls the child back to
        # top-level (via _safe_detach) rather than leaving a broken
        # half-reparent — the slot stays empty in that case.
        new_rects = PANE_RECTS[(new_bar, new_size)]
        for slot, pane in snapshot:
            new_rect = new_rects[slot]
            try:
                reparent.attach_window(pane.hwnd, new_container_hwnd, new_rect)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[selfplay_viewer] warning: re-attach of slot {slot} "
                    f"(hwnd={pane.hwnd} pid={pane.pid} label={pane.label!r}) "
                    f"after layout change failed: {exc}; restoring to top-level",
                    file=sys.stderr,
                )
                self._safe_detach(pane.hwnd, slot, pane.pid, pane.label)
                continue
            self._attached_panes[slot] = pane

        # Commit the new layout state ONLY on success (i.e. we got past
        # set_mode + the snapshot drain). Exceptions earlier propagate
        # with self.bar/self.size unchanged.
        self.bar = new_bar
        self.size = new_size

        return screen, background_surface

    def _resolve_background_path_for(self, _bar: str, _size: str) -> Path:
        """Resolve the background path for a (bar, size) target.

        Currently background selection is layout-independent so this is
        a thin pass-through, but the helper exists so ``_apply_layout_change``
        can resolve paths against the TARGET layout instead of relying
        on ``self.bar`` / ``self.size`` (which we commit only on success).
        """
        return self._resolve_background_path()

    def _detach_all_panes(self) -> None:
        """Restore every attached child to a top-level window.

        Per-slot exception isolation: a failed detach (stale HWND, dead
        process, etc.) is logged and skipped so one slot's failure does
        not strand the others as orphaned WS_CHILD windows. Always runs
        before ``pygame.quit()``.

        Order: detach FIRST, pop on success. A slot whose ``_safe_detach``
        re-raises (both primary and rescue paths failed) stays in the
        dict for visibility — the per-slot ``try`` here catches the
        re-raise so the loop continues to the next slot.
        """
        _ensure_main_thread()
        # Snapshot keys first so the dict-mutation in the loop body is safe.
        for slot in list(self._attached_panes.keys()):
            pane = self._attached_panes[slot]
            try:
                self._safe_detach(pane.hwnd, slot, pane.pid, pane.label)
            except Exception:  # noqa: BLE001 — already logged in _safe_detach
                # Both Win32 calls failed; leave the slot in the dict
                # so the situation is visible to callers / next teardown.
                continue
            del self._attached_panes[slot]

    @staticmethod
    def _load_background(
        path: Path,
        target_size: tuple[int, int],
    ) -> pygame.Surface:
        """Load + smoothscale a PNG to the container size.

        pygame is imported lazily inside the function body so this helper
        does not force a module-level pygame dependency. Returns a
        ``pygame.Surface`` scaled to ``target_size``.
        """
        import pygame

        surface = pygame.image.load(str(path)).convert()
        return pygame.transform.smoothscale(surface, target_size)

    def _paint_frame(
        self,
        screen: pygame.Surface,
        background_surface: pygame.Surface,
    ) -> None:
        """Paint background + placeholder panes (only for unattached slots) + overlay stub.

        Attached panes have a real Win32 child rendering into their pane
        rect already, so painting a grey fill there would cover the
        child window with an opaque rectangle on every frame.
        """
        import pygame

        screen.blit(background_surface, (0, 0))

        pane_rects = PANE_RECTS[(self.bar, self.size)]
        for slot, rect in enumerate(pane_rects):
            if slot in self._attached_panes:
                continue  # Real Win32 child owns this rect — do not overpaint.
            pygame.draw.rect(screen, PLACEHOLDER_COLOR, pygame.Rect(*rect))

        overlay_rect = OVERLAY_RECTS[(self.bar, self.size)]
        ox, oy, ow, oh = overlay_rect
        # Semi-transparent dark fill via a per-pixel-alpha overlay surface.
        overlay_surface = pygame.Surface((ow, oh), pygame.SRCALPHA)
        overlay_surface.fill(OVERLAY_FILL_COLOR)
        screen.blit(overlay_surface, (ox, oy))
        # Visible border on top.
        pygame.draw.rect(
            screen,
            OVERLAY_BORDER_COLOR,
            pygame.Rect(ox, oy, ow, oh),
            OVERLAY_BORDER_PX,
        )


__all__ = ["AttachedPane", "SelfPlayViewer"]
