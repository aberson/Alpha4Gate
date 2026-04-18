"""Win32 window-reparenting helpers for the self-play viewer.

This module is the Step 2 surface: given a child process PID (e.g. an
SC2 window), it finds that process's top-level HWND and attaches that
HWND as a child of a pygame-owned container HWND so the two SC2 panes
render inside a single host window. Four public entry points cover the
full lifecycle — find, attach, move, detach.

The module is cross-platform importable. ``import selfplay_viewer.reparent``
must succeed on Linux even though the pywin32 package is Windows-only, so
every ``win32gui``, ``win32con``, ``win32process``, ``win32api``, and
``pywintypes`` import is either guarded by ``if TYPE_CHECKING`` (types
only) or performed lazily inside a function body. On non-Windows
platforms every public function raises
``RuntimeError("reparent.py requires Windows")``.

Every public function is pinned to the main thread. Win32 window
handles are not guaranteed thread-safe for cross-thread SetParent or
SetWindowLong manipulation, and pygame's event loop runs on the main
thread, so each public entry point asserts
``threading.current_thread() is threading.main_thread()`` before doing
anything else.

The module never terminates the child process. ``detach_window``
restores top-level style and clears the parent, but it does NOT signal
or kill the owning process. This is a hard rule — see
``feedback_sc2_process_management.md`` in the Alpha4Gate memory store.
Tests spawn ``charmap.exe`` and clean it up via ``Popen.terminate()``
in their own teardown; production code never does.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — type-only imports
    import win32con  # noqa: F401
    import win32gui  # noqa: F401
    import win32process  # noqa: F401


_WINDOWS_ONLY_MSG = "reparent.py requires Windows"
_MAIN_THREAD_MSG = (
    "reparent.py functions must be called from the main thread "
    "(Win32 HWND manipulation is not thread-safe in this module)"
)


def _ensure_windows() -> None:
    """Raise ``RuntimeError`` when running on a non-win32 platform."""
    if sys.platform != "win32":
        raise RuntimeError(_WINDOWS_ONLY_MSG)


def _ensure_main_thread() -> None:
    """Raise ``RuntimeError`` when called off the main thread."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError(_MAIN_THREAD_MSG)


def find_hwnd_for_pid(pid: int, timeout_s: float = 15.0) -> int | None:
    """Poll ``EnumWindows`` until a visible top-level HWND owned by *pid* appears.

    Parameters
    ----------
    pid:
        Target process ID (e.g. the SC2 client PID).
    timeout_s:
        Maximum seconds to wait. Polls every 100 ms.

    Returns
    -------
    int | None
        HWND of a visible top-level non-tool window owned by *pid*, or
        ``None`` on timeout. Never raises on timeout — callers decide
        whether a missing HWND is fatal.

    Notes
    -----
    Tool windows (``WS_EX_TOOLWINDOW``) and invisible windows are
    skipped. The first visible, non-tool top-level window belonging to
    the PID wins; SC2 typically only has one such window after the
    splash screen disappears.
    """
    _ensure_main_thread()
    _ensure_windows()

    import pywintypes
    import win32con
    import win32gui
    import win32process

    deadline = time.monotonic() + timeout_s
    tick_s = 0.1
    found: list[int] = []

    def _callback(hwnd: int, _lparam: object) -> bool:
        # Skip invisible windows.
        if not win32gui.IsWindowVisible(hwnd):
            return True
        # Skip tool windows (no taskbar entry — never the main client).
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if ex_style & win32con.WS_EX_TOOLWINDOW:
            return True
        # Must be owned by the target PID.
        _tid, win_pid = win32process.GetWindowThreadProcessId(hwnd)
        if win_pid != pid:
            return True
        found.append(hwnd)
        return False  # Stop enumeration — we have our window.

    while True:
        found.clear()
        try:
            win32gui.EnumWindows(_callback, None)
        except pywintypes.error:
            # EnumWindows raises pywintypes.error when our callback
            # returns False to short-circuit enumeration — that's the
            # success path (we found the window and stopped early). If
            # ``found`` is empty here the error was a real one (access
            # denied, invalid HWND mid-enum, …) and we fall through to
            # the retry/timeout loop so transient failures don't leak
            # out as opaque pywintypes.error to callers. A persistent
            # failure will surface as the normal timeout-returns-None.
            if not found:
                # Narrow fallthrough: transient enumeration failure,
                # retry on the next tick.
                pass

        if found:
            return found[0]

        if time.monotonic() >= deadline:
            return None
        time.sleep(tick_s)


def attach_window(
    hwnd: int,
    container_hwnd: int,
    rect: tuple[int, int, int, int],
) -> None:
    """Reparent *hwnd* into *container_hwnd* and move it to *rect*.

    Idempotent: if *hwnd* is already a child of *container_hwnd*, the
    call still re-applies the style + position so callers can safely
    re-invoke after a layout change.

    Parameters
    ----------
    hwnd:
        Child window (e.g. the SC2 client HWND from
        :func:`find_hwnd_for_pid`).
    container_hwnd:
        Parent pygame container HWND (from
        ``pygame.display.get_wm_info()['window']``).
    rect:
        ``(x, y, width, height)`` in container-client coordinates.
    """
    _ensure_main_thread()
    _ensure_windows()

    import win32api
    import win32con
    import win32gui

    x, y, w, h = rect

    # Strip top-level decorations, add WS_CHILD. Clearing WS_POPUP /
    # WS_OVERLAPPEDWINDOW is necessary because SC2 launches as a
    # popup-style window.
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    new_style = (
        style
        & ~win32con.WS_POPUP
        & ~win32con.WS_OVERLAPPEDWINDOW
    ) | win32con.WS_CHILD | win32con.WS_VISIBLE
    # SetWindowLong returns 0 on failure AND on the success-when-
    # previous-value-was-0 case; the only way to disambiguate is to
    # clear GetLastError beforehand and check it after.
    win32api.SetLastError(0)
    prev = win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, new_style)
    err = win32api.GetLastError()
    if prev == 0 and err != 0:
        raise OSError(
            f"SetWindowLong(GWL_STYLE) failed for hwnd={hwnd}: GetLastError={err}"
        )

    # After changing GWL_STYLE we must call SetWindowPos with
    # SWP_FRAMECHANGED so Windows recomputes the non-client frame
    # (caption / border). MoveWindow below only triggers
    # WM_WINDOWPOSCHANGED, not WM_NCCALCSIZE, so without this the
    # DirectX-surfaced SC2 window would render the stale popup frame.
    win32gui.SetWindowPos(
        hwnd,
        0,
        0,
        0,
        0,
        0,
        win32con.SWP_NOMOVE
        | win32con.SWP_NOSIZE
        | win32con.SWP_NOZORDER
        | win32con.SWP_NOACTIVATE
        | win32con.SWP_FRAMECHANGED,
    )

    # SetParent — idempotent at the Win32 level: passing a parent a
    # window already has is a no-op, not an error.
    win32gui.SetParent(hwnd, container_hwnd)

    # Final argument to MoveWindow is the repaint flag.
    win32gui.MoveWindow(hwnd, x, y, w, h, True)


def move_window(hwnd: int, rect: tuple[int, int, int, int]) -> None:
    """Reposition an already-attached child HWND.

    Parameters
    ----------
    hwnd:
        Child HWND previously registered via :func:`attach_window`.
    rect:
        New ``(x, y, width, height)`` in container-client coordinates.
    """
    _ensure_main_thread()
    _ensure_windows()

    import win32gui

    x, y, w, h = rect
    win32gui.MoveWindow(hwnd, x, y, w, h, True)


def detach_window(hwnd: int) -> None:
    """Restore *hwnd* to a top-level window.

    Clears ``WS_CHILD``, re-applies ``WS_OVERLAPPEDWINDOW``, and calls
    ``SetParent(hwnd, 0)`` so *hwnd* becomes a desktop-level window
    again. **Never terminates or signals the owning process** — the
    caller is responsible for the process lifecycle.
    """
    _ensure_main_thread()
    _ensure_windows()

    import win32api
    import win32con
    import win32gui

    # Remove WS_CHILD, restore WS_OVERLAPPEDWINDOW so the window has
    # its title bar + resize frame back.
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    new_style = (style & ~win32con.WS_CHILD) | win32con.WS_OVERLAPPEDWINDOW
    # SetWindowLong returns 0 both on failure and on "previous value
    # was 0"; clear GetLastError first so we can disambiguate.
    win32api.SetLastError(0)
    prev = win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, new_style)
    err = win32api.GetLastError()
    if prev == 0 and err != 0:
        raise OSError(
            f"SetWindowLong(GWL_STYLE) failed for hwnd={hwnd}: GetLastError={err}"
        )

    # Recompute the non-client frame after the style change — without
    # SWP_FRAMECHANGED the window keeps drawing its old child-style
    # (frameless) non-client area even though the style now says
    # WS_OVERLAPPEDWINDOW.
    win32gui.SetWindowPos(
        hwnd,
        0,
        0,
        0,
        0,
        0,
        win32con.SWP_NOMOVE
        | win32con.SWP_NOSIZE
        | win32con.SWP_NOZORDER
        | win32con.SWP_NOACTIVATE
        | win32con.SWP_FRAMECHANGED,
    )

    # 0 → desktop / top-level.
    win32gui.SetParent(hwnd, 0)


__all__ = [
    "attach_window",
    "detach_window",
    "find_hwnd_for_pid",
    "move_window",
]
