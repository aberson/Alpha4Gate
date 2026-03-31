"""Smoke-test Alpha4Gate API and WebSocket endpoints during a live SC2 game.

Usage:
    uv run python scripts/live-smoke.py

Requires a running game with the Alpha4Gate server on localhost:8765.
Set API_URL env var to override the base URL.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import httpx
import websockets

BASE_URL = os.environ.get("API_URL", "http://localhost:8765")
WS_URL = BASE_URL.replace("http://", "ws://").replace("https://", "wss://") + "/ws/game"
WS_TIMEOUT = 5.0
POLL_INTERVAL = 1.0
POLL_MAX_WAIT = 30.0


class Results:
    """Accumulates PASS/FAIL check results."""

    def __init__(self) -> None:
        self.checks: list[tuple[bool, str]] = []

    def record(self, passed: bool, description: str, reason: str = "") -> None:
        self.checks.append((passed, description))
        tag = "[PASS]" if passed else "[FAIL]"
        suffix = f": {reason}" if reason else ""
        print(f"  {tag} {description}{suffix}")

    def summary(self) -> int:
        total = len(self.checks)
        passed = sum(1 for ok, _ in self.checks if ok)
        print(f"\n{passed}/{total} checks passed")
        return 0 if passed == total else 1


def wait_for_api(results: Results) -> bool:
    """Poll GET /api/commands/mode until the server responds (max 30s)."""
    url = f"{BASE_URL}/api/commands/mode"
    deadline = time.monotonic() + POLL_MAX_WAIT
    last_err = ""
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=5.0)
            if r.status_code == 200:
                results.record(True, "API reachable (GET /api/commands/mode)")
                return True
            last_err = f"status {r.status_code}"
        except httpx.HTTPError as exc:
            last_err = str(exc)
        time.sleep(POLL_INTERVAL)
    results.record(
        False, "API reachable (GET /api/commands/mode)", f"timeout after 30s: {last_err}"
    )
    return False


async def ws_wait_first_message() -> dict | None:
    """Connect to WS and wait for the first game-state message."""
    try:
        async with websockets.connect(WS_URL, open_timeout=10) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=WS_TIMEOUT)
            return json.loads(raw)  # type: ignore[no-any-return]
    except Exception:
        return None


def check_ws_first_message(results: Results) -> None:
    """Check 2: WS connects and receives at least one message."""
    msg = asyncio.run(ws_wait_first_message())
    if msg is not None:
        results.record(True, "WebSocket first message received")
    else:
        results.record(False, "WebSocket first message received", "no message within timeout")


def post_command(text: str) -> httpx.Response:
    """Send a command via POST /api/commands."""
    return httpx.post(
        f"{BASE_URL}/api/commands",
        json={"text": text},
        timeout=5.0,
    )


def check_command(results: Results, text: str) -> None:
    """Check that posting a command returns status queued or parsing."""
    try:
        r = post_command(text)
        data = r.json()
        status = data.get("status", "")
        if status in ("queued", "parsing"):
            results.record(True, f"POST /api/commands '{text}' → status={status}")
        else:
            results.record(
                False,
                f"POST /api/commands '{text}'",
                f"unexpected status={status!r} body={data}",
            )
    except Exception as exc:
        results.record(False, f"POST /api/commands '{text}'", str(exc))


def check_mode_switch(results: Results) -> None:
    """Cycle through all 3 modes and back to ai_assisted."""
    modes = ["human_only", "hybrid_cmd", "ai_assisted"]
    all_ok = True
    for mode in modes:
        try:
            r = httpx.put(
                f"{BASE_URL}/api/commands/mode",
                json={"mode": mode},
                timeout=5.0,
            )
            data = r.json()
            if data.get("mode") != mode:
                results.record(
                    False,
                    f"PUT mode → {mode}",
                    f"response mode={data.get('mode')!r}",
                )
                all_ok = False
        except Exception as exc:
            results.record(False, f"PUT mode → {mode}", str(exc))
            all_ok = False
    if all_ok:
        results.record(True, "Mode cycling (human_only → hybrid_cmd → ai_assisted)")


async def ws_collect_messages(count: int) -> list[dict]:
    """Collect `count` game-state messages from WS."""
    messages: list[dict] = []
    try:
        async with websockets.connect(WS_URL, open_timeout=10) as ws:
            for _ in range(count):
                raw = await asyncio.wait_for(ws.recv(), timeout=WS_TIMEOUT)
                messages.append(json.loads(raw))
    except Exception:
        pass
    return messages


def check_ws_game_states(results: Results) -> None:
    """Collect 5 WS messages and print a summary of each."""
    msgs = asyncio.run(ws_collect_messages(5))
    if len(msgs) >= 5:
        results.record(True, f"Collected {len(msgs)} WebSocket game-state messages")
        for i, m in enumerate(msgs, 1):
            gt = m.get("game_time_seconds", "?")
            mins = m.get("minerals", "?")
            supply = f"{m.get('supply_used', '?')}/{m.get('supply_cap', '?')}"
            print(f"    state {i}: time={gt}s  minerals={mins}  supply={supply}")
    else:
        results.record(
            False,
            "Collect 5 WebSocket game-state messages",
            f"only got {len(msgs)}",
        )


def check_history(results: Results) -> None:
    """Check GET /api/commands/history has entries."""
    try:
        r = httpx.get(f"{BASE_URL}/api/commands/history", timeout=5.0)
        data = r.json()
        cmds = data.get("commands", [])
        if len(cmds) > 0:
            results.record(True, f"GET /api/commands/history has {len(cmds)} entries")
        else:
            results.record(False, "GET /api/commands/history", "no entries")
    except Exception as exc:
        results.record(False, "GET /api/commands/history", str(exc))


def main() -> int:
    """Run all smoke-test checks in order."""
    print(f"Alpha4Gate live smoke test — target: {BASE_URL}\n")
    results = Results()

    # 1. Wait for API
    if not wait_for_api(results):
        print("\nServer not reachable — aborting remaining checks.")
        return results.summary()

    # 2. WS first message
    check_ws_first_message(results)

    # 3–4. Send commands
    check_command(results, "build stalkers")
    check_command(results, "attack natural")

    # 5. Mode cycling
    check_mode_switch(results)

    # 6. Collect game states
    check_ws_game_states(results)

    # 7. History
    check_history(results)

    return results.summary()


if __name__ == "__main__":
    sys.exit(main())
