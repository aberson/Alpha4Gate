"""Tests for ``selfplay_viewer.reparent`` — Windows-only.

Covers the four public functions (``find_hwnd_for_pid``,
``attach_window``, ``move_window``, ``detach_window``) using a real
classic Win32 child process (``charmap.exe``) reparented into a
pygame-owned container window. We use ``charmap`` rather than
``notepad`` because modern Windows 11 notepad is a UWP app whose
window is owned by a wrapper PID — ``find_hwnd_for_pid`` (correctly)
never finds a window owned by the spawned PID.

The whole module skips cleanly on Linux via ``pytestmark``; on Windows
the tests are additionally flagged ``@pytest.mark.win32`` so they can
be selected with ``pytest -m win32``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")

# The viewer deps are installed on demand (``uv sync --extra viewer``),
# so the test module must skip cleanly when they are absent — otherwise
# a default ``uv run pytest`` on a venv without the optional deps fails
# at fixture setup instead of skipping.
pygame = pytest.importorskip("pygame", reason="viewer extra (pygame) not installed")
pywintypes = pytest.importorskip("pywintypes", reason="viewer extra (pywin32) not installed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def charmap_process() -> Iterator[subprocess.Popen[bytes]]:
    """Spawn ``charmap.exe`` and terminate it in teardown regardless of outcome.

    Production code never terminates SC2 — but the test harness owns
    the charmap it spawned, so cleanup here is explicit and test-local.
    We use the full ``C:\\Windows\\System32\\charmap.exe`` path so the
    test doesn't depend on PATH lookup behaviour.
    """
    charmap_path = r"C:\Windows\System32\charmap.exe"
    proc = subprocess.Popen([charmap_path])
    try:
        yield proc
    finally:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture()
def pygame_container() -> Iterator[int]:
    """Create a small hidden-ish pygame window and return its HWND.

    The window is a real visible pygame window (pygame doesn't give us
    a hidden surface cleanly on all drivers) but it's 100x100 so it's
    unobtrusive. Teardown always calls ``pygame.quit()``.
    """
    # Avoid audio init surprises in CI.
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    import pygame

    pygame.init()
    try:
        pygame.display.set_mode((100, 100))
        pygame.display.set_caption("reparent-test-container")
        info = pygame.display.get_wm_info()
        hwnd = int(info["window"])
        yield hwnd
    finally:
        pygame.quit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.win32
def test_find_hwnd_for_pid_returns_charmap_window(
    charmap_process: subprocess.Popen[bytes],
) -> None:
    """``find_hwnd_for_pid`` locates the charmap top-level window."""
    from selfplay_viewer.reparent import find_hwnd_for_pid

    hwnd = find_hwnd_for_pid(charmap_process.pid, timeout_s=10.0)
    assert hwnd is not None
    assert hwnd != 0


@pytest.mark.win32
def test_find_hwnd_for_pid_timeout_returns_none() -> None:
    """Nonexistent PID with a short timeout returns None, does not raise."""
    from selfplay_viewer.reparent import find_hwnd_for_pid

    result = find_hwnd_for_pid(pid=999999, timeout_s=0.5)
    assert result is None


@pytest.mark.win32
def test_attach_sets_child_style_and_parent(
    charmap_process: subprocess.Popen[bytes],
    pygame_container: int,
) -> None:
    """After ``attach_window``: WS_CHILD set, GetParent == container."""
    import win32con
    import win32gui

    from selfplay_viewer.reparent import attach_window, detach_window, find_hwnd_for_pid

    hwnd = find_hwnd_for_pid(charmap_process.pid, timeout_s=10.0)
    assert hwnd is not None

    try:
        attach_window(hwnd, pygame_container, (0, 0, 80, 80))

        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        assert style & win32con.WS_CHILD, "WS_CHILD flag must be set after attach"

        parent = win32gui.GetParent(hwnd)
        assert parent == pygame_container, (
            f"GetParent should return container HWND; got {parent!r}, "
            f"expected {pygame_container!r}"
        )
    finally:
        # Detach before teardown so pygame.quit() doesn't destroy charmap's
        # now-parent window while charmap is still clinging to it.
        try:
            detach_window(hwnd)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


@pytest.mark.win32
def test_attach_is_idempotent(
    charmap_process: subprocess.Popen[bytes],
    pygame_container: int,
) -> None:
    """Calling ``attach_window`` twice on the same HWND is safe."""
    import win32con
    import win32gui

    from selfplay_viewer.reparent import attach_window, detach_window, find_hwnd_for_pid

    hwnd = find_hwnd_for_pid(charmap_process.pid, timeout_s=10.0)
    assert hwnd is not None

    try:
        attach_window(hwnd, pygame_container, (0, 0, 80, 80))
        attach_window(hwnd, pygame_container, (0, 0, 60, 60))  # second call

        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        assert style & win32con.WS_CHILD
        assert win32gui.GetParent(hwnd) == pygame_container
    finally:
        try:
            detach_window(hwnd)
        except Exception:  # noqa: BLE001
            pass


@pytest.mark.win32
def test_move_window_repositions_child(
    charmap_process: subprocess.Popen[bytes],
    pygame_container: int,
) -> None:
    """``move_window`` executes without raising on an attached child."""
    import win32gui

    from selfplay_viewer.reparent import (
        attach_window,
        detach_window,
        find_hwnd_for_pid,
        move_window,
    )

    hwnd = find_hwnd_for_pid(charmap_process.pid, timeout_s=10.0)
    assert hwnd is not None

    try:
        attach_window(hwnd, pygame_container, (0, 0, 40, 40))
        rect_before = win32gui.GetWindowRect(hwnd)
        move_window(hwnd, (10, 10, 50, 50))
        rect_after = win32gui.GetWindowRect(hwnd)
        # Exact coordinates drift under DWM / client-area math, but the
        # rect MUST change — that's the contract of move_window.
        assert rect_before != rect_after, (
            f"move_window did not change the window rect: "
            f"before={rect_before!r} after={rect_after!r}"
        )
    finally:
        try:
            detach_window(hwnd)
        except Exception:  # noqa: BLE001
            pass


@pytest.mark.win32
def test_detach_restores_top_level(
    charmap_process: subprocess.Popen[bytes],
    pygame_container: int,
) -> None:
    """After ``detach_window``: WS_CHILD cleared, GetParent == 0."""
    import win32con
    import win32gui

    from selfplay_viewer.reparent import attach_window, detach_window, find_hwnd_for_pid

    hwnd = find_hwnd_for_pid(charmap_process.pid, timeout_s=10.0)
    assert hwnd is not None

    try:
        attach_window(hwnd, pygame_container, (0, 0, 80, 80))
        detach_window(hwnd)

        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        assert not (style & win32con.WS_CHILD), "WS_CHILD must be cleared after detach"

        parent = win32gui.GetParent(hwnd)
        assert parent == 0, f"GetParent should return 0 after detach; got {parent!r}"
    finally:
        # Defensive: if any assertion failed after attach but before
        # detach, pygame_container teardown (LIFO → runs before
        # charmap_process teardown) would destroy charmap's parent
        # and indirectly terminate charmap. Re-detach here so charmap
        # is always top-level before pygame.quit() fires.
        try:
            detach_window(hwnd)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


@pytest.mark.win32
def test_detach_does_not_terminate_process(
    charmap_process: subprocess.Popen[bytes],
    pygame_container: int,
) -> None:
    """After detach, the owning process is still alive (hard rule)."""
    from selfplay_viewer.reparent import attach_window, detach_window, find_hwnd_for_pid

    hwnd = find_hwnd_for_pid(charmap_process.pid, timeout_s=10.0)
    assert hwnd is not None

    try:
        attach_window(hwnd, pygame_container, (0, 0, 80, 80))
        detach_window(hwnd)

        # Give Windows a tick to settle, then confirm charmap is still up.
        time.sleep(0.2)
        assert charmap_process.poll() is None, (
            "detach_window must NEVER terminate the owning process — "
            "see feedback_sc2_process_management.md"
        )
    finally:
        # Same LIFO-teardown safety as test_detach_restores_top_level:
        # if an assertion fails mid-body we must make sure charmap is
        # top-level before pygame_container tears down.
        try:
            detach_window(hwnd)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


# ---------------------------------------------------------------------------
# Main-thread assertion tests
# ---------------------------------------------------------------------------


@pytest.mark.win32
@pytest.mark.parametrize(
    "fn_name",
    ["find_hwnd_for_pid", "attach_window", "move_window", "detach_window"],
)
def test_main_thread_assertion(fn_name: str) -> None:
    """Calling any public fn off the main thread raises RuntimeError."""
    import selfplay_viewer.reparent as mod

    captured: list[BaseException] = []

    def _off_thread() -> None:
        try:
            if fn_name == "find_hwnd_for_pid":
                mod.find_hwnd_for_pid(pid=999999, timeout_s=0.1)
            elif fn_name == "attach_window":
                mod.attach_window(0, 0, (0, 0, 10, 10))
            elif fn_name == "move_window":
                mod.move_window(0, (0, 0, 10, 10))
            elif fn_name == "detach_window":
                mod.detach_window(0)
        except BaseException as exc:  # noqa: BLE001 — capturing to re-raise in main
            captured.append(exc)

    t = threading.Thread(target=_off_thread)
    t.start()
    t.join(timeout=5.0)
    assert not t.is_alive(), "off-thread call hung"
    assert captured, f"{fn_name} did not raise on a background thread"
    assert isinstance(captured[0], RuntimeError)
    assert "main thread" in str(captured[0]).lower()


# ---------------------------------------------------------------------------
# attach → detach → re-attach (new-game new-PID new-HWND) lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.win32
def test_attach_detach_reattach_cycle(pygame_container: int) -> None:
    """Reparenting a second child after detaching the first works cleanly.

    Mirrors the SC2 self-play use case: game 1 ends, we detach that
    HWND, game 2 starts with a new PID, we attach its HWND into the
    same container pane. We don't reuse the ``charmap_process``
    fixture here because pytest fixtures can't yield twice — the two
    processes are created and cleaned up inline.
    """
    import win32con
    import win32gui

    from selfplay_viewer.reparent import attach_window, detach_window, find_hwnd_for_pid

    charmap_path = r"C:\Windows\System32\charmap.exe"
    proc1 = subprocess.Popen([charmap_path])
    proc2: subprocess.Popen[bytes] | None = None
    hwnd1: int | None = None
    hwnd2: int | None = None
    try:
        hwnd1 = find_hwnd_for_pid(proc1.pid, timeout_s=10.0)
        assert hwnd1 is not None, "first charmap HWND not found"

        attach_window(hwnd1, pygame_container, (0, 0, 60, 60))
        assert win32gui.GetParent(hwnd1) == pygame_container
        detach_window(hwnd1)
        assert win32gui.GetParent(hwnd1) == 0, "first HWND not detached cleanly"

        # Start the second process AFTER the first is detached so
        # its HWND is guaranteed to be a distinct top-level window.
        proc2 = subprocess.Popen([charmap_path])
        hwnd2 = find_hwnd_for_pid(proc2.pid, timeout_s=10.0)
        assert hwnd2 is not None, "second charmap HWND not found"
        assert hwnd2 != hwnd1, "distinct processes must have distinct HWNDs"

        attach_window(hwnd2, pygame_container, (0, 0, 60, 60))
        style2 = win32gui.GetWindowLong(hwnd2, win32con.GWL_STYLE)
        assert style2 & win32con.WS_CHILD, "second HWND must carry WS_CHILD"
        assert win32gui.GetParent(hwnd2) == pygame_container, (
            "second HWND must be reparented into the container"
        )
    finally:
        # Detach any still-attached HWND before pygame fixture tears down.
        for h in (hwnd1, hwnd2):
            if h is not None:
                try:
                    detach_window(h)
                except Exception:  # noqa: BLE001 — best-effort
                    pass
        # Terminate both charmap processes regardless of test outcome.
        for proc in (proc1, proc2):
            if proc is None:
                continue
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
