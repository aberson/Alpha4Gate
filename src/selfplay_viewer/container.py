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

import queue
import random
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from selfplay_viewer import reparent
from selfplay_viewer.backgrounds import pick_background
from selfplay_viewer.overlay import (
    CONTAINER_SIZES,
    PANE_RECTS,
    PLACEHOLDER_MESSAGES,
    render_overlay,
    render_placeholder,
    reset_font_cache,
)

if TYPE_CHECKING:
    import pygame  # For types only — real import is inside .run()

    from orchestrator.contracts import SelfPlayRecord

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

#: Target frame rate for the demo loop.
TARGET_FPS: Final[int] = 30

#: Grace window (seconds) after the batch thread completes. Gives the
#: pygame loop time to drain the final ``game_end`` event so the detach
#: is visible to the user before the window closes.
BATCH_COMPLETE_GRACE_SECONDS: Final[float] = 3.0

#: Per-slot HWND discovery timeout (seconds) used by
#: :meth:`SelfPlayViewer._handle_game_start`. Shorter than the default
#: 15s because the ``_handle_game_start`` drain runs on the pygame main
#: thread and a 15s block freezes the UI. Step 7 will move the waiting
#: HWND lookup to a per-frame deferred task.
GAME_START_HWND_TIMEOUT_SECONDS: Final[float] = 2.0

#: Grace window (seconds) given to the batch thread to wind down after
#: the user closes the viewer. If the join times out we log a WARNING
#: and return — we do NOT forcibly kill SC2 (Never-Terminate rule).
BATCH_STOP_JOIN_TIMEOUT_SECONDS: Final[float] = 30.0

#: Cadence for the main-loop HWND liveness check. Every slot in
#: :attr:`SelfPlayViewer._attached_panes` is probed via ``IsWindow`` no
#: more often than this interval; on a dead HWND the slot transitions to
#: the placeholder state rendered by :func:`render_placeholder`. 0.5s is
#: the plan's default (Step 7) — fast enough to catch a mid-game crash
#: within a frame or two, slow enough to stay off the Win32 hot path.
PLACEHOLDER_POLL_INTERVAL_SECONDS: Final[float] = 0.5


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
    seed:
        Optional RNG seed that makes ``background="random"`` selection
        deterministic. Bound once at construction time and used for the
        first (and only) background pick; subsequent calls to
        :meth:`_resolve_background_path` return the cached path so the
        image stays stable across layout changes (``S`` / ``B``
        hotkeys). When ``None`` the module-level ``random`` is used.
    """

    def __init__(
        self,
        bar: str = "top",
        size: str = "large",
        background: str = "random",
        *,
        seed: int | None = None,
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
        #: Seeded RNG for deterministic ``--background random`` picks.
        #: ``None`` when no seed was supplied — ``pick_background`` then
        #: falls back to the module-level ``random``.
        self._background_rng: random.Random | None = (
            random.Random(seed) if seed is not None else None
        )
        #: Separate seeded RNG for placeholder flavor-message selection.
        #: Kept distinct from ``_background_rng`` so reusing the
        #: background picker for messages doesn't tangle the two streams
        #: (a change in background selection frequency would drift the
        #: message sequence for the same seed, and vice versa).
        self._placeholder_rng: random.Random | None = (
            random.Random(seed) if seed is not None else None
        )
        #: Cache for the first :meth:`_resolve_background_path` result.
        #: Populated on first call and reused thereafter so a single
        #: viewer instance keeps the same random background across
        #: layout changes (``S`` / ``B`` hotkeys trigger a re-resolve
        #: via ``_apply_layout_change``).
        self._resolved_background_path: Path | None = None
        #: Slot-keyed registry of reparented child windows. Empty until
        #: ``attach_pane`` is called.
        self._attached_panes: dict[int, AttachedPane] = {}
        #: Slot-keyed registry of panes whose attached HWND died
        #: mid-game. A slot is in placeholder state iff it's in this
        #: dict; the value is the pre-formatted flavor message
        #: (``{label}`` already substituted). Cleared on
        #: ``attach_pane`` / ``detach_pane`` / ``_detach_all_panes``.
        self._placeholder_panes: dict[int, str] = {}
        #: Monotonic deadline for the next HWND-liveness poll. Read +
        #: bumped in :meth:`_poll_attached_panes_for_death`; initialised
        #: by :meth:`run` / :meth:`run_with_batch` before the main loop
        #: starts so the first frame triggers the first poll.
        self._next_placeholder_check_at: float = 0.0
        #: Cross-thread event bus. ``on_game_start`` / ``on_game_end``
        #: push ``(event_type, payload)`` tuples from the ``run_batch``
        #: thread; the pygame main loop drains them each frame and
        #: performs all Win32 work on the main thread. ``queue.Queue``
        #: is thread-safe by construction so no additional lock is needed.
        self._event_queue: queue.Queue[tuple[str, tuple[Any, ...]]] = queue.Queue()
        #: Overlay state — updated from drained ``game_start`` /
        #: ``game_end`` events on the pygame main thread. A
        #: ``game_index`` of ``0`` means "batch not started" and signals
        #: :func:`render_overlay` to paint only the dark background rect.
        self._game_index: int = 0
        self._total_games: int = 0
        self._p1_label: str = ""
        self._p2_label: str = ""
        self._p1_wins: int = 0
        self._p2_wins: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def attach_pane(
        self,
        slot: int,
        pid: int,
        label: str,
        *,
        hwnd_timeout_s: float = 15.0,
    ) -> None:
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
        hwnd_timeout_s:
            Timeout (seconds) for :func:`reparent.find_hwnd_for_pid`.
            Default ``15.0`` matches the reparent default. Callers
            running on the pygame main thread (e.g.
            ``_handle_game_start``) should pass a much smaller value so
            the UI does not freeze; see
            :data:`GAME_START_HWND_TIMEOUT_SECONDS`.

        Raises
        ------
        ValueError
            If *slot* is not 0 or 1.
        RuntimeError
            If no visible top-level window owned by *pid* appears within
            *hwnd_timeout_s* seconds, or if the container window has not
            been initialised yet (i.e. the method is called before
            ``run`` has set up the display).

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

        # If this slot was showing a placeholder (HWND died in a prior
        # game), clear it FIRST. The next ``on_game_start`` hand-off
        # brings a fresh HWND and we want the grey/reparent path to
        # take over cleanly — any surviving placeholder entry would be
        # rendered on top of the new pane each frame until the slot is
        # detached again.
        self._placeholder_panes.pop(slot, None)

        # Same-PID re-attach: a window already wired into this slot is a
        # WS_CHILD of the container, so ``find_hwnd_for_pid`` cannot see
        # it (the enumeration only returns top-level windows). Restore
        # the prior pane to top-level FIRST, then look up the HWND.
        if slot in self._attached_panes and self._attached_panes[slot].pid == pid:
            prior = self._attached_panes.pop(slot)
            self._safe_detach(prior.hwnd, slot, prior.pid, prior.label)

        container_hwnd = self._current_container_hwnd()

        child_hwnd = reparent.find_hwnd_for_pid(pid, timeout_s=hwnd_timeout_s)
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
        # An explicit detach clears any placeholder state for this slot
        # too — a subsequent attach_pane will find a clean slate.
        self._placeholder_panes.pop(slot, None)
        if slot not in self._attached_panes:
            return
        # Detach FIRST, pop on success — if both the primary and rescue
        # detach paths fail, the slot stays in the dict so a subsequent
        # _detach_all_panes (or operator-driven retry) has another shot.
        pane = self._attached_panes[slot]
        self._safe_detach(pane.hwnd, slot, pane.pid, pane.label)
        del self._attached_panes[slot]

    def on_game_start(
        self,
        game_index: int,
        total: int,
        p1_pid: int,
        p2_pid: int,
        p1_label: str,
        p2_label: str,
    ) -> None:
        """Thread-safe callback — enqueues a ``game_start`` event.

        Designed to be passed as the ``on_game_start`` kwarg of
        :func:`orchestrator.selfplay.run_batch`. The batch runs on a
        background thread, so the callback must NOT touch pygame or
        Win32 directly (both are main-thread-only). Instead we push a
        ``("game_start", payload)`` tuple onto :attr:`_event_queue` and
        let the pygame main loop drain it.

        Parameters match the ``on_game_start`` contract documented in
        :mod:`orchestrator.selfplay`. A PID of ``-1`` means SC2 PID
        discovery timed out on the orchestrator side; the main-loop
        drain will skip the attach for that slot so the pane stays in
        its placeholder state until the next game.

        Parameters
        ----------
        game_index:
            1-based index of this game within the batch.
        total:
            Total number of games in the batch.
        p1_pid, p2_pid:
            SC2 process IDs for each seat (post-swap). ``-1`` indicates
            PID discovery timed out upstream.
        p1_label, p2_label:
            Human-readable version labels for each seat (post-swap).

        Notes
        -----
        The (pid, label) positional correspondence is **not authoritative**
        — see :data:`orchestrator.selfplay.OnGameStart` for why.
        """
        self._event_queue.put(
            (
                "game_start",
                (game_index, total, p1_pid, p2_pid, p1_label, p2_label),
            )
        )

    def on_game_end(self, result: SelfPlayRecord) -> None:
        """Thread-safe callback — enqueues a ``game_end`` event.

        Designed to be passed as the ``on_game_end`` kwarg of
        :func:`orchestrator.selfplay.run_batch`. Queues a ``game_end``
        event that the pygame main loop drains to detach both panes.

        The queue is the only cross-thread surface — we never call
        :meth:`detach_pane` directly from the batch thread because
        Win32 HWND manipulation is main-thread-only.

        Parameters
        ----------
        result:
            The finalised :class:`SelfPlayRecord` for the just-completed
            game. Stored with the event for future overlay rendering
            (Step 5 — game count / W-L display).
        """
        self._event_queue.put(("game_end", (result,)))

    def run_with_batch(
        self,
        batch_fn: Callable[[], Any],
        *,
        stop_event: threading.Event | None = None,
    ) -> Any:
        """Run *batch_fn* on a background thread with live pane hand-off.

        Initialises pygame on the current (main) thread, starts
        *batch_fn* on a daemon background thread, and drives the pygame
        main loop until the batch finishes (plus a
        :data:`BATCH_COMPLETE_GRACE_SECONDS` grace window so the final
        detach is visible) or the user closes the window.

        Each frame the loop:

        1. Drains pygame events (``QUIT`` / ``ESC`` / ``S`` / ``B``
           hotkeys mirrored from :meth:`run`).
        2. Drains the cross-thread :attr:`_event_queue`, handling
           ``game_start`` (attach both panes) and ``game_end`` (detach
           both panes). Per-event handlers are wrapped in ``try/except``
           so one bad event cannot crash the loop.
        3. Re-paints background + placeholders + overlay stub.

        Always runs :meth:`_detach_all_panes` in ``finally`` so
        ``pygame.quit`` never cascades ``WM_DESTROY`` through orphan
        WS_CHILD SC2 windows (Step 2 finding).

        Cooperative cancellation: when the user closes the viewer
        mid-batch, ``stop_event.set()`` is called in the ``finally``
        block so :func:`orchestrator.selfplay.run_batch` stops at the
        next inter-game boundary. We never forcibly kill SC2 (see
        ``feedback_sc2_process_management.md``).

        Parameters
        ----------
        batch_fn:
            Zero-arg callable that runs the self-play batch. Typically
            a ``lambda`` bound over :func:`run_batch` with
            :attr:`on_game_start` / :attr:`on_game_end` wired in. The
            return value is captured and returned from this method.
        stop_event:
            Optional :class:`threading.Event` the caller passed to
            ``run_batch``. If provided, ``stop_event.set()`` is called
            during teardown so the batch can shut down cooperatively
            rather than getting abandoned as a daemon thread.

        Returns
        -------
        Any
            Whatever *batch_fn* returned (e.g. the ``list[SelfPlayRecord]``
            from :func:`run_batch`). If *batch_fn* raised, the exception
            is re-raised here AFTER teardown.
        """
        _ensure_main_thread()

        # Lazy import — pygame may not be installed on non-Windows and
        # callers that never invoke run_with_batch should not pay the
        # import cost (mirrors the .run() lazy-import contract).
        import pygame

        bg_path = self._resolve_background_path()

        # Capture batch_fn's return value / exception across threads.
        # A single-slot list is the minimum closure wrapper that keeps
        # the main-thread code readable.
        result_box: list[Any] = []
        exc_box: list[BaseException] = []

        def _thread_target() -> None:
            try:
                result_box.append(batch_fn())
            except BaseException as exc:
                exc_box.append(exc)

        batch_thread = threading.Thread(
            target=_thread_target,
            name="selfplay-batch",
            daemon=True,
        )

        pygame.init()
        try:
            width, height = CONTAINER_SIZES[(self.bar, self.size)]
            screen = pygame.display.set_mode((width, height))
            pygame.display.set_caption("Alpha4Gate self-play viewer")

            background_surface = self._load_background(bg_path, (width, height))

            batch_thread.start()

            clock = pygame.time.Clock()
            running = True
            batch_done_at: float | None = None
            # Initialise the HWND-liveness poll clock so the first frame
            # triggers the first scan (monotonic deadline in the past).
            self._next_placeholder_check_at = time.monotonic()

            while running:
                # Scan BEFORE event drain so a pane that just died is
                # transitioned to placeholder before we re-paint this frame.
                self._poll_attached_panes_for_death()
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

                self._drain_event_queue()

                self._paint_frame(screen, background_surface)
                pygame.display.flip()
                clock.tick(TARGET_FPS)

                # Track batch completion so we can grant a grace period
                # (for the final game_end detach to render) before tearing
                # down the window.
                if not batch_thread.is_alive() and batch_done_at is None:
                    batch_done_at = time.monotonic()
                if batch_done_at is not None:
                    queue_empty = self._event_queue.empty()
                    grace_up = (
                        time.monotonic() - batch_done_at
                        >= BATCH_COMPLETE_GRACE_SECONDS
                    )
                    if queue_empty and grace_up:
                        running = False
        finally:
            # Cooperative cancellation — set the event FIRST so the batch
            # thread can break out of its game loop at the next
            # inter-game boundary, THEN detach panes (which takes minimal
            # time), THEN wait for the batch thread. This ordering lets
            # the batch shut down cleanly rather than leaking a daemon
            # thread that still has an SC2 pair spinning.
            if stop_event is not None:
                stop_event.set()
            # Mirror run()'s teardown contract: detach EVERY pane before
            # pygame.quit() so the container's WM_DESTROY cascade cannot
            # reach still-WS_CHILD SC2 windows. See Step 2 gauntlet.
            try:
                self._detach_all_panes()
            except Exception:  # noqa: BLE001 — already logged per-slot
                pass
            pygame.quit()
            # Drop cached pygame.font.Font handles — they reference
            # SDL_TTF state that pygame.quit() just freed. Without this
            # a future SelfPlayViewer.run* in the same process would
            # hand the dead handle back to the overlay and SIGSEGV on
            # the first Font.render. See overlay.reset_font_cache.
            reset_font_cache()
            # Wait for the batch thread to wind down. When stop_event is
            # wired, the in-flight game completes naturally and the loop
            # exits at the next boundary — give it a real budget
            # (:data:`BATCH_STOP_JOIN_TIMEOUT_SECONDS`). When no
            # stop_event, fall back to the 1-second best-effort join —
            # the daemon flag keeps the interpreter exitable either way.
            join_budget = (
                BATCH_STOP_JOIN_TIMEOUT_SECONDS if stop_event is not None else 1.0
            )
            batch_thread.join(timeout=join_budget)
            if batch_thread.is_alive():
                print(
                    f"[selfplay_viewer] warning: batch thread did not exit "
                    f"within {join_budget}s of viewer close; orphaned SC2 "
                    f"processes may remain until the current game finishes.",
                    file=sys.stderr,
                )

        if exc_box:
            raise exc_box[0]
        return result_box[0] if result_box else None

    def _drain_event_queue(self) -> None:
        """Drain all pending cross-thread events onto the pygame thread.

        Called once per frame from :meth:`run_with_batch`'s main loop.
        Each event is handled in its own ``try/except`` so a bad event
        (e.g. a stale PID whose attach raises) cannot crash the loop
        and strand the user inside a container window that stopped
        responding. All errors are logged to stderr with enough context
        to debug.
        """
        while True:
            try:
                event_type, payload = self._event_queue.get_nowait()
            except queue.Empty:
                return
            try:
                if event_type == "game_start":
                    self._handle_game_start(payload)
                elif event_type == "game_end":
                    self._handle_game_end(payload)
                else:
                    print(
                        f"[selfplay_viewer] warning: unknown event type "
                        f"{event_type!r}; payload={payload!r}",
                        file=sys.stderr,
                    )
            except Exception as exc:  # noqa: BLE001 — isolation per event
                print(
                    f"[selfplay_viewer] warning: event handler for "
                    f"{event_type!r} raised: {exc}",
                    file=sys.stderr,
                )

    def _update_game_start_state(
        self,
        game_index: int,
        total: int,
        p1_label: str,
        p2_label: str,
    ) -> None:
        """Pure state-update for a ``game_start`` event.

        Split out from :meth:`_handle_game_start` so the overlay
        bookkeeping (index, total, labels) can be unit-tested without
        needing pygame / Win32.

        ``game_index == 1`` resets the W-L counters — that's the natural
        batch boundary, and the same ``SelfPlayViewer`` instance can be
        reused across multiple ``run_with_batch`` calls (e.g. live demo
        + soak run back-to-back). Without the reset, counters from the
        previous batch would bleed into the next batch's overlay.
        """
        if game_index == 1:
            self._p1_wins = 0
            self._p2_wins = 0
        self._game_index = game_index
        self._total_games = total
        self._p1_label = p1_label
        self._p2_label = p2_label

    def _update_game_end_state(self, result: SelfPlayRecord) -> None:
        """Pure state-update for a ``game_end`` event.

        Two modes:

        - **Cross-version (default)** — increments ``_p1_wins`` /
          ``_p2_wins`` based on whether the record's ``winner`` matches
          the stored label.
        - **Same-version self-play** (``self._p1_label ==
          self._p2_label``, e.g. ``v0`` vs ``v0``) — both seats share
          one label, so the per-seat ``if/elif`` would always fire on
          the ``_p1_wins`` branch and ``_p2_wins`` would stay 0 forever.
          We collapse to a single counter (``_p1_wins`` is the canonical
          slot, ``_p2_wins`` stays at 0) and the overlay renders a unified
          "Wins: N" string.

        Draws (``winner is None``) and labels that match neither seat
        (defensive) produce no increment.

        Defensive label-consistency check: ``SelfPlayRecord`` carries
        the post-swap ``p1_version`` / ``p2_version`` values that the
        orchestrator authoritatively assigned to each seat. They should
        match the labels we stored from the matching ``on_game_start``.
        If they ever drift (e.g. an out-of-order event reorders the
        queue) we log a warning rather than raise — surfacing the
        inconsistency without crashing the viewer mid-batch.
        """
        if (
            result.p1_version != self._p1_label
            or result.p2_version != self._p2_label
        ):
            print(
                f"[selfplay_viewer] warning: game_end labels "
                f"({result.p1_version!r}, {result.p2_version!r}) do not "
                f"match game_start labels "
                f"({self._p1_label!r}, {self._p2_label!r}); score "
                f"attribution may be wrong",
                file=sys.stderr,
            )
        if result.winner is None:
            return
        if self._p1_label == self._p2_label:
            # Same-version self-play: both seats share a label so the
            # per-side W-L is undefined. Collapse to a unified Wins
            # counter on _p1_wins; _p2_wins stays at 0 (and the overlay
            # detects the same-label case and renders "Wins: N").
            if result.winner == self._p1_label:
                self._p1_wins += 1
            return
        if result.winner == self._p1_label:
            self._p1_wins += 1
        elif result.winner == self._p2_label:
            self._p2_wins += 1
        # else: defensive — winner didn't match either seat label.
        # Silently skip rather than mis-attribute the win.

    def _handle_game_start(self, payload: tuple[Any, ...]) -> None:
        """Apply a ``game_start`` event on the pygame thread.

        Attaches slot 0 and slot 1 independently. A failed attach on one
        slot (e.g. PID already dead, ``find_hwnd_for_pid`` timeout) logs
        and skips so the other slot still gets a chance. ``-1`` PIDs
        (PID discovery timed out on the orchestrator side) are skipped
        entirely; the pane stays in its placeholder state.

        Overlay bookkeeping is updated FIRST via
        :meth:`_update_game_start_state` so a failed attach on one slot
        doesn't prevent the W-L header from showing the right index /
        labels.
        """
        game_index, total, p1_pid, p2_pid, p1_label, p2_label = payload
        self._update_game_start_state(game_index, total, p1_label, p2_label)
        for slot, pid, label in ((0, p1_pid, p1_label), (1, p2_pid, p2_label)):
            if pid == -1:
                # PID discovery failed upstream — leave the slot in its
                # placeholder state rather than block on find_hwnd_for_pid.
                continue
            try:
                # Short timeout — this runs on the pygame main thread,
                # so a 15s block here freezes the UI. On timeout the
                # slot stays in its placeholder; Step 7 will add per-
                # frame deferred HWND polling so we don't drop the SC2
                # window if it takes 3-5s to show.
                self.attach_pane(
                    slot,
                    pid,
                    label,
                    hwnd_timeout_s=GAME_START_HWND_TIMEOUT_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001 — per-slot isolation
                print(
                    f"[selfplay_viewer] warning: attach_pane(slot={slot}, "
                    f"pid={pid}, label={label!r}) failed: {exc}",
                    file=sys.stderr,
                )

    def _handle_game_end(self, payload: tuple[Any, ...]) -> None:
        """Apply a ``game_end`` event on the pygame thread.

        Detaches both slots. ``detach_pane`` is a no-op for slots that
        aren't currently attached (e.g. when PID discovery failed for
        that side), so this is safe to call unconditionally.

        Also updates the running W-L counters via
        :meth:`_update_game_end_state` so the overlay reflects the new
        score BEFORE the next ``game_start`` arrives. Running the
        state-update first means a failure in the detach path doesn't
        strand the scoreboard.
        """
        (result,) = payload
        self._update_game_end_state(result)
        for slot in (0, 1):
            try:
                self.detach_pane(slot)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[selfplay_viewer] warning: detach_pane(slot={slot}) "
                    f"failed: {exc}",
                    file=sys.stderr,
                )

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
            # Initialise the HWND-liveness poll clock so the first frame
            # triggers the first scan (monotonic deadline in the past).
            self._next_placeholder_check_at = time.monotonic()
            while running:
                # Scan BEFORE event drain so a pane that just died is
                # transitioned to placeholder before we re-paint this frame.
                self._poll_attached_panes_for_death()
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
            # Same stale-handle gotcha as run_with_batch — drop the
            # font cache so a subsequent SelfPlayViewer.run in the same
            # process doesn't hand back a SIGSEGV-on-render handle.
            reset_font_cache()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_background_path(self) -> Path:
        """Resolve the configured background key to an on-disk PNG path.

        Cached on first call — every subsequent call returns the same
        :class:`~pathlib.Path` so a layout change (``S`` / ``B`` hotkey)
        re-paints with the same background image rather than drawing a
        fresh random pick each time. The cache also means the seeded RNG
        is only consumed once per viewer; a ``--seed 42`` run is stable
        even when the layout flips multiple times.
        """
        if self._resolved_background_path is None:
            self._resolved_background_path = pick_background(
                self.background, rng=self._background_rng
            )
        return self._resolved_background_path

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

        Placeholder state is cleared wholesale at the end — placeholder
        bookkeeping is tied to the lifetime of a single pygame session
        and must not survive teardown (a subsequent ``run`` / ``run_with_batch``
        on the same instance starts with a clean slate).
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
        self._placeholder_panes.clear()

    def _is_hwnd_alive(self, hwnd: int) -> bool:
        """Return ``True`` when *hwnd* still refers to a live window.

        Thin wrapper over ``win32gui.IsWindow`` with a lazy ``win32gui``
        import (Linux-importable contract) and a defensive
        ``try/except`` so a pywin32 error on a stale handle degrades to
        "dead" rather than crashing the poll loop. The call is read-
        only — we NEVER call ``SetParent(hwnd, NULL)`` or any other
        mutator on a dead HWND (that would raise or no-op) and we NEVER
        signal the owning process (Never-Terminate rule).
        """
        try:
            import win32gui  # lazy — Windows-only
        except ImportError:
            # On non-Windows the Win32 primitive is unreachable anyway;
            # treat every HWND as dead so callers can fall through to
            # the placeholder path uniformly. Production code never
            # hits this branch — run() / run_with_batch() are Windows-
            # only — but tests benefit from the graceful fallback.
            return False
        try:
            return bool(win32gui.IsWindow(hwnd))
        except Exception:  # noqa: BLE001 — treat any Win32 failure as dead
            return False

    def _poll_attached_panes_for_death(self) -> None:
        """Detect dead HWNDs and transition their slots to placeholder state.

        Rate-limited to one scan per :data:`PLACEHOLDER_POLL_INTERVAL_SECONDS`.
        Called once per frame from the pygame main loop (both
        :meth:`run` and :meth:`run_with_batch`). For each currently-
        attached slot whose HWND reports dead via :meth:`_is_hwnd_alive`:

        1. Pick a random flavor message from
           :data:`overlay.PLACEHOLDER_MESSAGES` (using
           :attr:`_placeholder_rng` if seeded, else module ``random``).
        2. Remove the slot from :attr:`_attached_panes` — we do NOT
           call :func:`reparent.detach_window` because the HWND is
           already gone; calling detach on a dead handle would raise
           or no-op.
        3. Record the formatted message in :attr:`_placeholder_panes`
           so the next :meth:`_paint_frame` draws the placeholder.
        4. Log an INFO line to stderr with the slot / label / message
           so operators can correlate the visual with the crash.
        """
        now = time.monotonic()
        if now < self._next_placeholder_check_at:
            return
        self._next_placeholder_check_at = now + PLACEHOLDER_POLL_INTERVAL_SECONDS

        # Snapshot — the loop body mutates self._attached_panes.
        for slot, pane in list(self._attached_panes.items()):
            if self._is_hwnd_alive(pane.hwnd):
                continue
            if self._placeholder_rng is not None:
                template = self._placeholder_rng.choice(PLACEHOLDER_MESSAGES)
            else:
                template = random.choice(PLACEHOLDER_MESSAGES)
            formatted_message = template.format(label=pane.label)
            # Drop the bookkeeping entry without calling detach — the
            # HWND is already gone, so SetParent(0) would raise.
            del self._attached_panes[slot]
            self._placeholder_panes[slot] = formatted_message
            print(
                f"[selfplay_viewer] slot {slot} pane ({pane.label}) died "
                f"mid-game; showing placeholder: {formatted_message!r}",
                file=sys.stderr,
            )

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
        """Paint background + placeholder panes (only for unattached slots) + overlay.

        Attached panes have a real Win32 child rendering into their pane
        rect already, so painting a grey fill there would cover the
        child window with an opaque rectangle on every frame. Slots in
        :attr:`_placeholder_panes` (HWND died mid-game) also skip the
        grey rect — the dark placeholder overlay is painted there
        instead via :func:`render_placeholder`. The grey rect would
        otherwise cover the placeholder dark fill.

        The overlay is delegated to :func:`render_overlay` which handles
        both the semi-transparent background rect AND the text content
        (VS header, game index, W-L). When ``self._game_index == 0``
        (batch not started) ``render_overlay`` paints only the
        background rect.
        """
        import pygame

        screen.blit(background_surface, (0, 0))

        pane_rects = PANE_RECTS[(self.bar, self.size)]
        for slot, rect in enumerate(pane_rects):
            if slot in self._attached_panes:
                continue  # Real Win32 child owns this rect — do not overpaint.
            if slot in self._placeholder_panes:
                continue  # render_placeholder paints this slot below.
            pygame.draw.rect(screen, PLACEHOLDER_COLOR, pygame.Rect(*rect))

        # Paint dead-pane placeholders after the grey rects so they
        # render on top of the background (the overlay fill itself is
        # semi-transparent and would otherwise be washed out by a grey
        # base). Slots with a real attached child don't go through this
        # loop — the Win32 child is painting itself already.
        for slot, message in self._placeholder_panes.items():
            render_placeholder(
                surface=screen,
                pane_rect=pane_rects[slot],
                message=message,
            )

        render_overlay(
            surface=screen,
            bar=self.bar,
            size=self.size,
            p1_label=self._p1_label,
            p2_label=self._p2_label,
            game_index=self._game_index,
            total=self._total_games,
            p1_wins=self._p1_wins,
            p2_wins=self._p2_wins,
        )


__all__ = ["AttachedPane", "SelfPlayViewer"]
