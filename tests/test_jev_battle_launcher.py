"""Provider selection and credential boundary for the native Battle View host."""

import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def battle():
    return runpy.run_path(str(Path(__file__).parents[1] / "scripts/probes/jev_battle_view.py"))


def test_hosted_key_reaches_only_game_environment(battle, monkeypatch):
    import os

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    child_env = battle["game_environment"]("typesafe")
    assert child_env["TYPESAFE_API_KEY"] == "test-only-key"
    assert (
        "TYPESAFE_API_KEY" not in os.environ
    )  # Later browser/recorder children cannot inherit it.


def test_scripted_probe_drops_key(battle, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    assert "TYPESAFE_API_KEY" not in battle["game_environment"]("scripted")


def test_missing_hosted_key_fails_instead_of_switching_provider(battle, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="no API key"):
        battle["game_environment"]("typesafe")


def test_hosted_selection_reaches_production_runner(battle, monkeypatch, tmp_path):
    import jev.bot
    import jev.runner

    calls = []
    monkeypatch.setattr(jev.bot, "JevBot", jev.bot.JevBot)  # Restore probe subclass after test.
    monkeypatch.setattr(
        jev.runner,
        "run_match",
        lambda options, *args, **kwargs: calls.append(options) or SimpleNamespace(status="test"),
    )
    battle["game_child"]("a" * 32, tmp_path, True, "typesafe", 20)
    assert len(calls) == 1
    assert calls[0].decision_provider == "typesafe"
    assert calls[0].decision_model == "jev-1.13.0"
    assert calls[0].decision_max_requests == 20
    assert calls[0].max_game_seconds == 0
    assert calls[0].max_wall_seconds == 0


def test_json_write_retries_windows_reader_lock(battle, monkeypatch, tmp_path):
    destination = tmp_path / "camera-status.json"
    destination.write_text('{"reason":"previous"}')
    original = Path.replace
    attempts = []

    def locked_once(path, target):
        attempts.append(target)
        if len(attempts) == 1:
            raise PermissionError("Windows reader holds destination")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", locked_once)
    battle["write_json"](destination, {"reason": "Scouting"})
    import json

    assert json.loads(destination.read_text()) == {"reason": "Scouting"}
    assert len(attempts) == 2


def test_observer_rush_measurement_preserves_speed_and_match_limits(battle, monkeypatch, tmp_path):
    import jev.bot
    import jev.runner

    calls = []
    monkeypatch.setattr(jev.bot, "JevBot", jev.bot.JevBot)
    monkeypatch.setattr(
        jev.runner, "run_match",
        lambda options, *args, **kwargs: calls.append(options) or SimpleNamespace(status="test"),
    )
    battle["game_child"](
        "a" * 32, tmp_path, True, "typesafe", 450,
        game_speed=2, max_game_seconds=900, max_wall_seconds=1200, seed=101, difficulty=4,
    )
    options = calls[0]
    assert options.game_speed == 2 and not options.realtime
    assert (options.max_game_seconds, options.max_wall_seconds) == (900, 1200)
    assert (options.seed, options.difficulty) == (101, 4)
    assert options.decision_provider == "typesafe"


class BrowserWindows:
    """No render-child API: modern Chromium need not expose a renderer HWND."""

    def __init__(self, windows=None, client=(0, 0, 1000, 800)):
        self.windows = windows or {}
        self.client = client

    def EnumWindows(self, callback, data):
        for hwnd in self.windows:
            callback(hwnd, data)

    def IsWindowVisible(self, hwnd):
        return self.windows[hwnd][2]

    def GetWindowText(self, hwnd):
        return self.windows[hwnd][0]

    def GetClassName(self, hwnd):
        return self.windows[hwnd][1]

    def GetClientRect(self, hwnd):
        assert hwnd == 42
        return self.client

    def ClientToScreen(self, hwnd, point):
        assert hwnd == 42
        return (100 + point[0], 200 + point[1])


@pytest.mark.parametrize(
    ("client", "viewport", "expected"),
    [
        ((0, 0, 1000, 800), {"w": 1000, "h": 770}, (100, 230)),
        ((0, 0, 1000, 800), {"w": 984, "h": 754}, (108, 238)),
        ((0, 0, 1186, 793), {"w": 1178, "h": 662}, (104, 327)),
    ],
)
def test_browser_origin_needs_no_render_child(battle, client, viewport, expected):
    import asyncio
    from unittest.mock import AsyncMock

    page = SimpleNamespace(evaluate=AsyncMock(return_value=viewport))
    origin, measured = asyncio.run(
        battle["wait_browser_origin"](BrowserWindows(client=client), page, 42)
    )
    assert origin == expected
    assert measured == viewport


@pytest.mark.parametrize(
    "viewport",
    [
        {"w": 0, "h": 700},
        {"w": 1001, "h": 700},
        {"w": 984, "h": 799},
        {"w": float("nan"), "h": 700},
        {"w": 999, "h": 700},
        {},
    ],
)
def test_browser_geometry_rejects_impossible_measurements(battle, viewport):
    with pytest.raises(RuntimeError, match="Browser content geometry.*client=.*viewport="):
        battle["browser_content_offset"]((0, 0, 1000, 800), viewport)


def test_browser_selection_is_unique_and_run_specific(battle):
    title = "Alpha4Gate Observer Rush [run-123]"
    gui = BrowserWindows(
        {
            1: (title, "SDL_app", True),
            2: (title + " - Microsoft Edge", "Chrome_WidgetWin_1", True),
            3: ("Alpha4Gate Observer Rush [another-run]", "Chrome_WidgetWin_1", True),
            4: (title, "Chrome_WidgetWin_1", False),
            5: (title, "Chrome_WidgetWin_1", True),
            6: (title + " extra unrelated title", "Chrome_WidgetWin_1", True),
        }
    )
    assert battle["find_browser_window"](gui, title, 5) == 2
    with pytest.raises(RuntimeError, match="2 candidates"):
        battle["find_browser_window"](gui, title, 1)
    with pytest.raises(RuntimeError, match="0 candidates"):
        battle["find_browser_window"](gui, "missing", 1)


def test_browser_window_readiness_retries_and_rejects_ambiguity(battle, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    gui = BrowserWindows()

    async def appear(_):
        gui.windows[42] = ("run-title", "Chrome_WidgetWin_1", True)

    sleep.side_effect = appear
    assert asyncio.run(battle["wait_browser_window"](gui, "run-title", 1)) == 42
    assert sleep.await_count == 1
    gui.windows[43] = ("run-title", "Chrome_WidgetWin_1", True)
    with pytest.raises(RuntimeError, match="2 candidates"):
        asyncio.run(battle["wait_browser_window"](gui, "run-title", 1, attempts=3))
    assert sleep.await_count == 3


def test_browser_readiness_retries_are_bounded(battle, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    page = SimpleNamespace(
        evaluate=AsyncMock(
            side_effect=[
                {"w": 0, "h": 0},
                {"w": 984, "h": 754},
            ]
        )
    )
    assert asyncio.run(battle["wait_browser_origin"](BrowserWindows(), page, 42))[0] == (108, 238)
    assert sleep.await_count == 1
    page.evaluate = AsyncMock(return_value={"w": 0, "h": 0})
    with pytest.raises(RuntimeError, match="Browser content geometry"):
        asyncio.run(battle["wait_browser_origin"](BrowserWindows(), page, 42, attempts=3))
    assert page.evaluate.await_count == 3
