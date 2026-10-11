"""Windows-only, bounded SC2 + React + OBS feasibility probe.

Run with: uv run --extra viewer --with playwright --with obsws-python python
scripts/probes/jev_battle_view.py --record-seconds 40

Requires the dashboard on localhost:3000/8765 and the isolated portable OBS
profile described in the feasibility report. No Typesafe calls. Never kills SC2.
This is a development probe, not the production battle launcher.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def game_child(run_id: str, evidence: Path, interactive: bool = False) -> None:
    from bots.jev.v2 import load_policy
    from sc2.position import Point2

    import jev.bot
    from jev.runner import MatchOptions, run_match

    class CameraProbeBot(jev.bot.JevBot):
        """Camera-only instrumentation; all gameplay stays in JevController."""

        held: tuple[float, float] | None = None
        previous_sample = -1.0

        async def on_step(self, iteration: int) -> None:
            control = json.loads((evidence / "control.json").read_text())
            camera = self.state.observation_raw.player.camera
            actual = (camera.x, camera.y)
            if control["locked"]:
                if self.held is None:
                    self.held = actual
                await self.client.move_camera(Point2(self.held))
            else:
                self.held = None
            if control.get("stop"):
                self.controller.request_stop()
            if self.time - self.previous_sample >= 0.5:
                self.previous_sample = self.time
                with (evidence / "camera.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "wall": time.time(),
                                "game": self.time,
                                "camera": actual,
                                "locked": control["locked"],
                            }
                        )
                        + "\n"
                    )
            await super().on_step(iteration)

    jev.bot.JevBot = CameraProbeBot

    def ready(_: str) -> None:
        deadline = time.monotonic() + 60
        while not (evidence / "ready").exists():
            if (evidence / "control.json").exists() and json.loads(
                (evidence / "control.json").read_text()
            ).get("stop"):
                raise RuntimeError("Battle probe stopped before SC2 launch")
            if time.monotonic() > deadline:
                raise RuntimeError("Battle view did not render its exact run in time")
            time.sleep(0.1)

    outcome = run_match(
        MatchOptions(
            difficulty=3,
            seed=11,
            realtime=True,
            max_game_seconds=900 if interactive else 150,
            max_wall_seconds=1200 if interactive else 210,
        ),
        load_policy(),
        run_root=ROOT / "data/jev/runs",
        run_id=run_id,
        on_recorded=ready,
    )
    print(outcome, flush=True)


async def probe(seconds: int, interactive: bool = False, auto_record: bool = False) -> None:
    import obsws_python as obs
    import psutil
    import pygame
    import win32api
    import win32con
    import win32gui
    from playwright.async_api import async_playwright

    from selfplay_viewer.reparent import attach_window, detach_window, find_hwnd_for_pid

    ctypes.windll.user32.SetProcessDPIAware()
    if any(process.name().lower() == "sc2_x64.exe" for process in psutil.process_iter()):
        raise RuntimeError("An SC2 client is already running; do not attach an unrelated match")
    run_id = uuid.uuid4().hex
    evidence = ROOT / "data/jev/runs" / run_id / "battle"
    base = Path(os.environ["LOCALAPPDATA"]) / "Alpha4Gate/obs-portable"
    client = obs.ReqClient(
        host="127.0.0.1", port=4457, password=(base / "probe-password.txt").read_text(), timeout=5
    )
    if client.get_record_status().output_active:
        raise RuntimeError("OBS is already recording; refusing to change its scene")
    if client.get_profile_list().current_profile_name != "BattleProbe":
        raise RuntimeError("Refusing to alter an OBS profile other than BattleProbe")
    client.set_video_settings(
        numerator=30,
        denominator=1,
        base_width=1920,
        base_height=1080,
        out_width=1920,
        out_height=1080,
    )
    for entry in client.get_input_list().inputs:
        client.remove_input(entry["inputName"])
    scene = client.get_current_program_scene().scene_name
    log_path = ROOT / ".build-step/jev-live-validation" / f"battle-{run_id}.log"
    game_log = log_path.open("w", encoding="utf-8")
    game = subprocess.Popen(
        [sys.executable, str(Path(__file__)), "--game-child", run_id]
        + (["--interactive"] if interactive else []),
        cwd=ROOT,
        stdout=game_log,
        stderr=subprocess.STDOUT,
        env={k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"},
    )
    for _ in range(100):
        if (evidence.parent / "policy.json").exists():
            break
        if game.poll() is not None:
            raise RuntimeError(f"Game failed to initialize; see {log_path}")
        await asyncio.sleep(0.1)
    evidence.mkdir()
    write_json(evidence / "control.json", {"locked": True})
    client.set_record_directory(str(evidence))
    sc2_hwnd = None
    browser = None
    recording = False
    locked = True
    message = "Preparing game · camera locked"
    record_path = None
    started = None
    metrics = []
    stop_requested = False
    pygame.init()
    surface = pygame.display.set_mode((1920, 1080))
    pygame.display.set_caption("Alpha4Gate Battle Probe Host")
    surface.fill((20, 23, 31))
    pygame.display.flip()
    native_host = pygame.display.get_wm_info()["window"]
    win32gui.SetWindowLong(
        native_host,
        win32con.GWL_STYLE,
        win32gui.GetWindowLong(native_host, win32con.GWL_STYLE) | win32con.WS_CLIPCHILDREN,
    )
    win32gui.SetWindowPos(native_host, 0, 0, 0, 0, 0, win32con.SWP_NOSIZE | win32con.SWP_NOZORDER)
    browser_hwnd = None

    async def pump_native_events() -> None:
        # Embedded browser operations can wait for messages from their native parent.
        # Keep that parent responsive even while awaiting Playwright or browser close.
        while True:
            pygame.event.pump()
            await asyncio.sleep(0.02)

    event_pump = asyncio.create_task(pump_native_events())
    playwright = await async_playwright().start()
    try:
        browser = await playwright.chromium.launch_persistent_context(
            str(evidence / "browser-profile"),
            channel="msedge",
            headless=False,
            no_viewport=True,
            chromium_sandbox=True,
            color_scheme="dark",
            args=[
                "--app=http://localhost:3000/?battle=1&run=" + run_id,
                "--window-size=1920,1080",
                "--window-position=0,0",
                "--force-device-scale-factor=1",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
            ],
            env={k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"},
        )
        page = browser.pages[0]
        page.set_default_timeout(10000)
        if "battle=1" not in page.url:
            await page.goto("http://localhost:3000/?battle=1&run=" + run_id)

        async def action(name: str) -> dict:
            nonlocal recording, locked, message, record_path, stop_requested
            if name == "stop":
                stop_requested = True
                message = "Stopping battle"
            elif name == "lock":
                locked = not locked
                write_json(evidence / "control.json", {"locked": locked})
                message = "Camera locked" if locked else "Game controls enabled"
            elif name == "record":
                if recording:
                    record_path = client.stop_record().output_path
                    recording = False
                    message = "Recording saved"
                elif sc2_hwnd:
                    client.start_record()
                    recording = True
                    message = "Recording · game audio only"
            return {"recording": recording, "locked": locked, "message": message}

        await page.expose_function("battleAction", action)
        await page.get_by_test_id("jev-graph").wait_for(timeout=45000)
        await page.evaluate("document.title = 'Alpha4Gate Battle Feasibility'")
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(evidence / "before-game.png"))
        host = None

        def find_host(hwnd: int, _: object) -> None:
            nonlocal host
            if win32gui.IsWindowVisible(
                hwnd
            ) and "Alpha4Gate Battle Feasibility" in win32gui.GetWindowText(hwnd):
                host = hwnd

        win32gui.EnumWindows(find_host, None)
        if host is None:
            raise RuntimeError("Could not identify the dedicated Battle window")
        browser_hwnd = host
        attach_window(browser_hwnd, native_host, (0, 0, 1920, 1080))
        host = native_host
        pygame.event.pump()
        await page.wait_for_timeout(500)
        await page.get_by_role("button", name="Fit", exact=True).click()
        (evidence / "ready").touch()
        print(f"BATTLE run={run_id} host={host} evidence={evidence}", flush=True)
        deadline = time.monotonic() + 50
        while sc2_hwnd is None and time.monotonic() < deadline:
            for process in psutil.Process(game.pid).children(recursive=True):
                if process.name().lower() == "sc2_x64.exe":
                    sc2_hwnd = find_hwnd_for_pid(process.pid, timeout_s=0)
            pygame.event.pump()
            await asyncio.sleep(0.2)
        if not sc2_hwnd:
            raise RuntimeError("The probe's SC2 window did not appear")
        bounds = await page.locator("#battle-game-slot").bounding_box()
        if bounds is None:
            raise RuntimeError("Game slot has no bounds")
        # Edge's native client includes its app title bar; web content begins below it.
        viewport = await page.evaluate("({w: innerWidth, h: innerHeight})")
        client_area = win32gui.GetClientRect(host)
        offset_y = client_area[3] - viewport["h"]
        rectangle = (
            round(bounds["x"]) + 1,
            round(bounds["y"]) + offset_y + 1,
            round(bounds["width"]) - 2,
            round(bounds["height"]) - 2,
        )
        # SC2 resets placement while loading; attach only after live observations.
        while time.monotonic() < deadline:
            state = json.loads((evidence.parent / "state.json").read_text())
            if state["game_seconds"] > 2:
                break
            pygame.event.pump()
            await asyncio.sleep(0.5)
        if state["game_seconds"] <= 2:
            raise RuntimeError("SC2 never reached live observations")
        kinds = client.get_input_kind_list(True).input_kinds
        if "wasapi_process_output_capture" not in kinds:
            raise RuntimeError(
                "OBS process-audio source unavailable; refusing desktop/microphone fallback"
            )
        sc2_title = win32gui.GetWindowText(sc2_hwnd)
        client.create_input(
            scene,
            "SC2 audio only",
            "wasapi_process_output_capture",
            {
                "window": f"{sc2_title}:{win32gui.GetClassName(sc2_hwnd)}:SC2_x64.exe",
                "priority": 1,
            },
            True,
        )
        await asyncio.sleep(1)  # Bind process audio while SC2 is still a top-level window.
        attach_window(sc2_hwnd, host, rectangle)
        win32gui.EnableWindow(sc2_hwnd, True)
        render_hwnds = []

        def find_render(hwnd, _):
            if win32gui.GetClassName(hwnd) == "Chrome_RenderWidgetHostHWND":
                render_hwnds.append(hwnd)

        win32gui.EnumChildWindows(browser_hwnd, find_render, None)
        if not render_hwnds:
            raise RuntimeError("Could not locate the browser content surface")
        # Let SC2 process the asynchronous style/parent changes before measuring.
        await asyncio.sleep(0.3)
        browser_origin = win32gui.ClientToScreen(render_hwnds[0], (0, 0))
        target = (
            browser_origin[0] + round(bounds["x"]) + 1,
            browser_origin[1] + round(bounds["y"]) + 1,
        )
        for _ in range(5):
            actual = win32gui.ClientToScreen(sc2_hwnd, (0, 0))
            actual_client = win32gui.GetClientRect(sc2_hwnd)
            if actual == target and actual_client[2:] == rectangle[2:]:
                break
            outer = win32gui.GetWindowRect(sc2_hwnd)
            pos = win32gui.ScreenToClient(host, (outer[0], outer[1]))
            win32gui.SetWindowPos(
                sc2_hwnd,
                win32con.HWND_TOP,
                pos[0] + target[0] - actual[0],
                pos[1] + target[1] - actual[1],
                outer[2] - outer[0] + rectangle[2] - actual_client[2],
                outer[3] - outer[1] + rectangle[3] - actual_client[3],
                win32con.SWP_NOACTIVATE,
            )
            await asyncio.sleep(0.15)
        if (
            win32gui.ClientToScreen(sc2_hwnd, (0, 0)) != target
            or win32gui.GetClientRect(sc2_hwnd)[2:] != rectangle[2:]
        ):
            raise RuntimeError("SC2 did not settle inside its allocated pane")
        print("Game pane fit verified", target, rectangle[2:], flush=True)
        title = win32gui.GetWindowText(host)
        window = f"{title}:{win32gui.GetClassName(host)}:python.exe"
        client.create_input(
            scene,
            "Battle window",
            "window_capture",
            {"window": window, "method": 2, "client_area": True, "cursor": True},
            True,
        )
        item = client.get_scene_item_id(scene, "Battle window").scene_item_id
        client.set_scene_item_transform(
            scene,
            item,
            {
                "boundsType": "OBS_BOUNDS_SCALE_INNER",
                "boundsWidth": 1920,
                "boundsHeight": 1080,
                "positionX": 0,
                "positionY": 0,
            },
        )
        # Wait for actual game observations, not merely a loading window.
        while time.monotonic() < deadline:
            state = json.loads((evidence.parent / "state.json").read_text())
            if state["game_seconds"] > 2:
                break
            pygame.event.pump()
            await asyncio.sleep(0.5)
        if not interactive or auto_record:
            await page.get_by_role("button", name="Record Battle", exact=True).click()
        started = time.monotonic()
        print(
            "Battle ready. Use Stop battle to leave cleanly."
            if interactive
            else "Recording started; testing graph input and camera auto-lock",
            flush=True,
        )
        previous_inside = False
        milestones = set()
        next_metric = 0.0
        while (
            time.monotonic() - started < (1200 if interactive else seconds)
            and game.poll() is None
            and not stop_requested
        ):
            if page.is_closed() or not win32gui.IsWindow(sc2_hwnd):
                break
            if any(event.type == pygame.QUIT for event in pygame.event.get()):
                break
            x, y = win32gui.ScreenToClient(sc2_hwnd, win32api.GetCursorPos())
            inside = 0 <= x < rectangle[2] and 0 <= y < rectangle[3]
            if not locked and previous_inside and not inside:
                await action("lock")
            previous_inside = inside
            elapsed = time.monotonic() - started
            if not interactive and elapsed >= 5 and 5 not in milestones:
                milestones.add(5)
                await page.get_by_role("button", name="Rotate right", exact=True).click()
                army = page.locator('[role="treeitem"][aria-level="1"][data-lane="army"]')
                await army.focus()
                await page.keyboard.press("Enter")
            if not interactive and elapsed >= 10 and 10 not in milestones:
                milestones.add(10)
                await action("lock")  # Explicitly enable camera input.
                center = win32gui.ClientToScreen(sc2_hwnd, (80, rectangle[3] - 85))
                win32api.SetCursorPos(center)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
            if not interactive and elapsed >= 12 and 12 not in milestones:
                milestones.add(12)
                # Leave across the right edge, reproducing the operator's drift trigger.
                win32api.SetCursorPos(
                    win32gui.ClientToScreen(sc2_hwnd, (rectangle[2] + 30, rectangle[3] // 2))
                )
            if elapsed < next_metric:
                await asyncio.sleep(0.05)
                continue
            next_metric = elapsed + 1
            stats = client.get_stats()
            metrics.append(
                {
                    "elapsed": time.monotonic() - started,
                    "active_fps": stats.active_fps,
                    "render_skipped": stats.render_skipped_frames,
                    "output_skipped": stats.output_skipped_frames,
                    "locked": locked,
                    "game_window": win32gui.GetWindowRect(sc2_hwnd),
                    "game_parent": win32gui.GetParent(sc2_hwnd),
                    "game_client": win32gui.GetClientRect(sc2_hwnd),
                    "game_origin": win32gui.ClientToScreen(sc2_hwnd, (0, 0)),
                }
            )
            await asyncio.sleep(0.05)
        if recording:
            await action("record")
        if win32gui.IsWindow(sc2_hwnd):
            client.save_source_screenshot(
                "Battle window", "png", str(evidence / "composite.png"), 1920, 1080, -1
            )

        write_json(
            evidence / "probe.json",
            {
                "run_id": run_id,
                "video": record_path,
                "duration": None if started is None else time.monotonic() - started,
                "metrics": metrics,
                "viewport": viewport,
                "game_rect": rectangle,
                "graph_nodes": await page.get_by_role("treeitem").count(),
                "inspection": await page.locator(".battle-inspection").inner_text(),
                "audio_inputs": client.get_input_list().inputs,
            },
        )
        print(f"Evidence: {evidence}\nRecording: {record_path or 'none requested'}", flush=True)
    finally:
        write_json(evidence / "control.json", {"locked": True, "stop": True})
        if recording:
            client.stop_record()
        if sc2_hwnd and win32gui.IsWindow(sc2_hwnd):
            win32gui.EnableWindow(sc2_hwnd, True)
            detach_window(sc2_hwnd)
        if browser_hwnd and win32gui.IsWindow(browser_hwnd):
            detach_window(browser_hwnd)
        if browser:
            with suppress(Exception):  # Playwright's context manager may already have closed it.
                await browser.close()
        for _ in range(40):
            if game.poll() is not None:
                break
            pygame.event.pump()
            await asyncio.sleep(0.5)
        event_pump.cancel()
        with suppress(asyncio.CancelledError):
            await event_pump
        pygame.quit()
        await playwright.stop()
        game_log.close()
        if game.poll() is None:
            print("Game still leaving; its own 210-second wall limit remains active", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-seconds", type=int, default=40, choices=range(20, 91))
    parser.add_argument("--game-child")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Operator-controlled local match; no automatic camera/graph movements",
    )
    parser.add_argument(
        "--auto-record", action="store_true", help="Begin recording when the game is ready"
    )
    args = parser.parse_args()
    if args.game_child:
        game_child(
            args.game_child, ROOT / "data/jev/runs" / args.game_child / "battle", args.interactive
        )
    else:
        asyncio.run(probe(args.record_seconds, args.interactive, args.auto_record))
