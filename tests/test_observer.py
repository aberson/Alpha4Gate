"""Unit tests for observation parsing."""

from __future__ import annotations

from unittest.mock import MagicMock

from alpha4gate.observer import observe


def _make_mock_bot(
    minerals: int = 350,
    vespene: int = 125,
    supply_used: float = 23.0,
    supply_cap: float = 31.0,
    game_loop: int = 1024,
    time: float = 64.0,
    score: float = 1250.0,
    units: list[tuple[str, int]] | None = None,
) -> MagicMock:
    """Create a mock BotAI with standard game state."""
    bot = MagicMock()
    bot.minerals = minerals
    bot.vespene = vespene
    bot.supply_used = supply_used
    bot.supply_cap = supply_cap
    bot.time = time
    bot.state.game_loop = game_loop
    bot.state.score.score = score

    # Build mock units
    if units is None:
        units = [("Probe", 12), ("Nexus", 1), ("Pylon", 2)]

    mock_units = []
    for name, count in units:
        for _ in range(count):
            u = MagicMock()
            u.name = name
            mock_units.append(u)
    bot.all_own_units = mock_units
    return bot


class TestObserve:
    def test_returns_all_expected_keys(self) -> None:
        bot = _make_mock_bot()
        entry = observe(bot)
        expected_keys = {
            "timestamp",
            "game_step",
            "game_time_seconds",
            "minerals",
            "vespene",
            "supply_used",
            "supply_cap",
            "units",
            "actions_taken",
            "score",
        }
        assert set(entry.keys()) == expected_keys

    def test_resource_values(self) -> None:
        bot = _make_mock_bot(minerals=400, vespene=200)
        entry = observe(bot)
        assert entry["minerals"] == 400
        assert entry["vespene"] == 200

    def test_supply_values(self) -> None:
        bot = _make_mock_bot(supply_used=15.0, supply_cap=23.0)
        entry = observe(bot)
        assert entry["supply_used"] == 15
        assert entry["supply_cap"] == 23

    def test_game_step_and_time(self) -> None:
        bot = _make_mock_bot(game_loop=2048, time=128.5)
        entry = observe(bot)
        assert entry["game_step"] == 2048
        assert entry["game_time_seconds"] == 128.5

    def test_unit_counts(self) -> None:
        bot = _make_mock_bot(units=[("Probe", 12), ("Zealot", 3), ("Pylon", 2)])
        entry = observe(bot)
        unit_map = {u["type"]: u["count"] for u in entry["units"]}
        assert unit_map["Probe"] == 12
        assert unit_map["Zealot"] == 3
        assert unit_map["Pylon"] == 2

    def test_empty_units(self) -> None:
        bot = _make_mock_bot(units=[])
        entry = observe(bot)
        assert entry["units"] == []

    def test_actions_taken_default_empty(self) -> None:
        bot = _make_mock_bot()
        entry = observe(bot)
        assert entry["actions_taken"] == []

    def test_actions_taken_passed_through(self) -> None:
        bot = _make_mock_bot()
        actions = [{"action": "Build", "target": "Pylon", "location": [32, 48]}]
        entry = observe(bot, actions_taken=actions)
        assert entry["actions_taken"] == actions

    def test_score_value(self) -> None:
        bot = _make_mock_bot(score=5000.0)
        entry = observe(bot)
        assert entry["score"] == 5000.0

    def test_timestamp_is_iso_format(self) -> None:
        bot = _make_mock_bot()
        entry = observe(bot)
        assert "T" in entry["timestamp"]
