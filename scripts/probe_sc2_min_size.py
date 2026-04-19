"""Empirically find SC2's minimum WS_CHILD render size.

Launches a single SC2 process, reparents it into a hidden pygame
container, and MoveWindow-s it through a decreasing size ladder. For
each attempt we compare the requested ``(w, h)`` to the actual client
rect Windows gave back. The first size where Windows silently floors to
a larger value is SC2's enforced minimum.

Usage::

    .\\.venv-py312\\Scripts\\python scripts\\probe_sc2_min_size.py

One-off diagnostic — not part of the regular test suite. Safe to keep
in ``scripts/`` since it's read-only and cleans up SC2 on exit.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

import pygame  # noqa: E402
import win32con  # type: ignore[import-untyped]  # noqa: E402
import win32gui  # type: ignore[import-untyped]  # noqa: E402

from orchestrator.selfplay import (  # noqa: E402
    _install_port_collision_patch,
    _install_worker_thread_signal_patch,
)
from selfplay_viewer.reparent import find_hwnd_for_pid  # noqa: E402

_SIZE_LADDER: list[tuple[int, int]] = [
    # Baseline + 4:3 ladder from the first probe run.
    (1024, 768),
    (1024, 576),
    # Widescreen / vertical-stack candidates — want height << 768 so
    # a vertical 2-pane stack can fit on a 1600-tall screen.
    (1280, 720),
    (1280, 600),
    (1280, 540),
    (1024, 640),
    (1024, 600),
    (1024, 540),
    (1152, 720),
    (1366, 768),
]


async def probe() -> None:
    _install_port_collision_patch()
    _install_worker_thread_signal_patch()

    from sc2.sc2process import SC2Process

    pygame.init()
    pygame.display.set_mode((1600, 900))
    pygame.display.set_caption("SC2 min-size probe (container)")
    container_hwnd = pygame.display.get_wm_info()["window"]
    print(f"Container HWND: {container_hwnd}")

    sp = SC2Process()
    try:
        controller = await sp.__aenter__()  # noqa: F841
        pid = sp._process.pid
        print(f"SC2 PID: {pid}")

        sc2_hwnd = find_hwnd_for_pid(pid, timeout_s=30.0)
        if sc2_hwnd is None:
            print("ERROR: SC2 HWND never appeared")
            return
        print(f"SC2 HWND: {sc2_hwnd}")

        # Initial outer + client rect while still top-level.
        wl, wt, wr, wb = win32gui.GetWindowRect(sc2_hwnd)
        cl, ct, cr, cb = win32gui.GetClientRect(sc2_hwnd)
        print(f"Top-level outer: {wr - wl}x{wb - wt}")
        print(f"Top-level client: {cr - cl}x{cb - ct}")

        # Reparent so we test the WS_CHILD path (matches attach_window).
        style = win32gui.GetWindowLong(sc2_hwnd, win32con.GWL_STYLE)
        new_style = (
            style & ~win32con.WS_POPUP & ~win32con.WS_OVERLAPPEDWINDOW
        ) | win32con.WS_CHILD | win32con.WS_VISIBLE
        win32gui.SetWindowLong(sc2_hwnd, win32con.GWL_STYLE, new_style)
        win32gui.SetWindowPos(
            sc2_hwnd,
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
        win32gui.SetParent(sc2_hwnd, container_hwnd)
        print("SC2 reparented into container (WS_CHILD)")

        print()
        print(f"{'Requested':>15}   {'Client rect':>15}   Status")
        print("-" * 60)

        accepted_sizes: list[tuple[int, int]] = []
        for req_w, req_h in _SIZE_LADDER:
            win32gui.MoveWindow(sc2_hwnd, 0, 0, req_w, req_h, True)
            # Pump a few frames so SC2 + the OS settle.
            for _ in range(5):
                pygame.event.pump()
                time.sleep(0.1)

            cl, ct, cr, cb = win32gui.GetClientRect(sc2_hwnd)
            actual_w = cr - cl
            actual_h = cb - ct

            if actual_w == req_w and actual_h == req_h:
                status = "accepted"
                accepted_sizes.append((req_w, req_h))
            else:
                status = f"enforced min ({actual_w}x{actual_h})"
            print(
                f"{req_w}x{req_h:<6}  {actual_w}x{actual_h:<6}   {status}"
            )

        print()
        if accepted_sizes:
            min_w, min_h = accepted_sizes[-1]
            print(f"SMALLEST ACCEPTED: {min_w}x{min_h}")
        else:
            print("No requested size was accepted unchanged.")
    finally:
        try:
            await sp.__aexit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            print(f"SC2 teardown warning: {exc}")
        pygame.quit()


if __name__ == "__main__":
    asyncio.run(probe())
