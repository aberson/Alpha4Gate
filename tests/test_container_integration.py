"""Integration tests for ``SelfPlayViewer.attach_pane`` / ``detach_pane``.

Wires the Step 1 pygame container together with the Step 2 reparent
primitive: spawns real ``charmap.exe`` processes, opens a pygame
container window, reparents the charmaps into pane slots 0 and 1,
asserts the Win32 state machine, then detaches and asserts cleanup.

Like ``tests/test_reparent.py`` we use ``charmap`` rather than
``notepad`` because Win11 notepad is a UWP shim whose window is owned
by a wrapper PID — ``find_hwnd_for_pid`` (correctly) never finds it.

The whole module skips cleanly on Linux via ``pytestmark``; on Windows
the tests are additionally flagged ``@pytest.mark.win32`` so they can
be selected with ``pytest -m win32``. The viewer extras are imported
via ``importorskip`` so the file collects cleanly on venvs without
pygame / pywin32.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from selfplay_viewer.container import SelfPlayViewer

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")

# The viewer deps are installed on demand (``uv sync --extra viewer``),
# so the test module must skip cleanly when they are absent — otherwise
# a default ``uv run pytest`` on a venv without the optional deps fails
# at collection.
pygame = pytest.importorskip("pygame", reason="viewer extra (pygame) not installed")
pywintypes = pytest.importorskip("pywintypes", reason="viewer extra (pywin32) not installed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_CHARMAP_PATH = r"C:\Windows\System32\charmap.exe"


def _spawn_charmap() -> subprocess.Popen[bytes]:
    """Spawn one ``charmap.exe`` and return the Popen handle."""
    return subprocess.Popen([_CHARMAP_PATH])


def _terminate_quietly(proc: subprocess.Popen[bytes] | None) -> None:
    """Best-effort terminate + wait. Production code never does this — tests
    own the charmap they spawned, so cleanup here is explicit and test-local.
    """
    if proc is None:
        return
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture()
def charmap_pid_pair() -> Iterator[tuple[int, int]]:
    """Spawn two ``charmap.exe`` processes and yield their PIDs.

    Both processes are terminated in teardown regardless of test
    outcome. Mirrors the single-process ``charmap_process`` fixture
    in ``tests/test_reparent.py`` but yields the PID pair the
    integration tests need.
    """
    proc1 = _spawn_charmap()
    proc2 = _spawn_charmap()
    try:
        yield (proc1.pid, proc2.pid)
    finally:
        _terminate_quietly(proc1)
        _terminate_quietly(proc2)


@pytest.fixture()
def viewer_with_display() -> Iterator[tuple[SelfPlayViewer, int]]:
    """Create a SelfPlayViewer, init pygame, and yield ``(viewer, container_hwnd)``.

    We do NOT call ``viewer.run()`` — the test drives the lifecycle
    directly so it can assert intermediate state. ``pygame.quit()`` runs
    in teardown after every charmap is detached.
    """
    # Avoid audio init surprises in CI.
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    import pygame

    from selfplay_viewer import SelfPlayViewer

    viewer = SelfPlayViewer(bar="top", size="large")
    pygame.init()
    try:
        # Use a tiny display rather than the full 2188x948 layout so the
        # test window doesn't dominate the screen during a manual run.
        pygame.display.set_mode((300, 300))
        pygame.display.set_caption("container-integration-test")
        info = pygame.display.get_wm_info()
        hwnd = int(info["window"])
        yield (viewer, hwnd)
    finally:
        # Defensive: detach anything still attached so pygame.quit()
        # doesn't take charmap windows down with the container cascade.
        try:
            viewer._detach_all_panes()
        except Exception:  # noqa: BLE001
            pass
        pygame.quit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.win32
def test_attach_then_detach_roundtrip(
    charmap_pid_pair: tuple[int, int],
    viewer_with_display: tuple[SelfPlayViewer, int],
) -> None:
    """attach_pane wires both children; detach_pane restores top-level."""
    import win32con
    import win32gui

    viewer, container_hwnd = viewer_with_display
    pid0, pid1 = charmap_pid_pair

    viewer.attach_pane(0, pid0, "charmap0")
    viewer.attach_pane(1, pid1, "charmap1")

    panes = viewer._attached_panes
    hwnd0 = panes[0].hwnd
    hwnd1 = panes[1].hwnd

    # Both children parented to the container.
    assert win32gui.GetParent(hwnd0) == container_hwnd, (
        f"slot 0 GetParent expected {container_hwnd}, got {win32gui.GetParent(hwnd0)}"
    )
    assert win32gui.GetParent(hwnd1) == container_hwnd, (
        f"slot 1 GetParent expected {container_hwnd}, got {win32gui.GetParent(hwnd1)}"
    )

    # Both children carry WS_CHILD.
    style0 = win32gui.GetWindowLong(hwnd0, win32con.GWL_STYLE)
    style1 = win32gui.GetWindowLong(hwnd1, win32con.GWL_STYLE)
    assert style0 & win32con.WS_CHILD, "slot 0 missing WS_CHILD after attach"
    assert style1 & win32con.WS_CHILD, "slot 1 missing WS_CHILD after attach"

    viewer.detach_pane(0)
    viewer.detach_pane(1)

    assert viewer._attached_panes == {}
    assert win32gui.GetParent(hwnd0) == 0, "slot 0 child still parented after detach"
    assert win32gui.GetParent(hwnd1) == 0, "slot 1 child still parented after detach"


@pytest.mark.win32
def test_pane_slot_contract(
    viewer_with_display: tuple[SelfPlayViewer, int],
) -> None:
    """Slot validation contract: detach is no-op on empty, attach/detach reject bad slots."""
    viewer, _ = viewer_with_display

    # detach on empty slots is a silent no-op.
    viewer.detach_pane(0)
    viewer.detach_pane(1)
    assert viewer._attached_panes == {}

    # Out-of-range slots raise ValueError.
    with pytest.raises(ValueError, match="slot must be one of"):
        viewer.attach_pane(2, 12345, "out-of-range")
    with pytest.raises(ValueError, match="slot must be one of"):
        viewer.detach_pane(-1)


@pytest.mark.win32
def test_attach_pane_replaces_existing(
    viewer_with_display: tuple[SelfPlayViewer, int],
) -> None:
    """Re-attaching slot 0 with a different PID detaches the prior child first."""
    import win32gui

    viewer, container_hwnd = viewer_with_display

    # Spawn the two charmaps inline (one per attach call) — the
    # ``charmap_pid_pair`` fixture pre-spawns both, but here we want
    # to be sure each attach uses a fresh PID we can assert against.
    proc_first = _spawn_charmap()
    proc_second = _spawn_charmap()
    try:
        viewer.attach_pane(0, proc_first.pid, "first")
        first_hwnd = viewer._attached_panes[0].hwnd
        assert win32gui.GetParent(first_hwnd) == container_hwnd, (
            "first attach didn't reparent into the container"
        )

        viewer.attach_pane(0, proc_second.pid, "second")

        # Slot 0 now binds the second PID.
        pane = viewer._attached_panes[0]
        assert pane.pid == proc_second.pid
        assert pane.label == "second"
        assert pane.hwnd != first_hwnd, (
            "replace must rebind to the new child HWND"
        )

        # The first child is back to top-level (parent == 0).
        assert win32gui.GetParent(first_hwnd) == 0, (
            f"first child should be detached after replace; "
            f"GetParent={win32gui.GetParent(first_hwnd)}"
        )

        # The second child is parented to the container.
        assert win32gui.GetParent(pane.hwnd) == container_hwnd, (
            "second child should be parented to the container"
        )

        # Cleanup: detach the second so the fixture teardown doesn't
        # destroy it via the pygame.quit() cascade.
        viewer.detach_pane(0)
    finally:
        _terminate_quietly(proc_first)
        _terminate_quietly(proc_second)


@pytest.mark.win32
def test_attach_pane_same_pid_reattach(
    viewer_with_display: tuple[SelfPlayViewer, int],
) -> None:
    """Re-attaching the SAME PID into the same slot succeeds.

    The first attach makes the PID's window a WS_CHILD, so a naive
    second ``find_hwnd_for_pid(pid)`` would time out (the enumeration
    only sees top-level windows). The container handles this by
    detaching the prior pane to top-level FIRST when the same PID
    re-targets the same slot.
    """
    import win32gui

    viewer, container_hwnd = viewer_with_display

    proc = _spawn_charmap()
    try:
        viewer.attach_pane(0, proc.pid, "a")
        first_hwnd = viewer._attached_panes[0].hwnd
        assert win32gui.GetParent(first_hwnd) == container_hwnd

        # Re-attach with the same PID under a different label. Without
        # the same-PID short-circuit this would block on
        # find_hwnd_for_pid until the 15s timeout (and then raise).
        viewer.attach_pane(0, proc.pid, "b")

        pane = viewer._attached_panes[0]
        assert pane.pid == proc.pid
        assert pane.label == "b"
        # Same process, so very likely the same HWND — but the contract
        # only requires the slot to be wired, not the HWND identity.
        assert win32gui.GetParent(pane.hwnd) == container_hwnd, (
            "second attach should re-parent the same-PID window into the container"
        )

        viewer.detach_pane(0)
    finally:
        _terminate_quietly(proc)
