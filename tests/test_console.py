"""Unit tests for console output formatting."""

from __future__ import annotations

from alpha4gate.console import format_status


def _sample_entry(
    game_step: int = 1024,
    game_time_seconds: float = 64.0,
    minerals: int = 350,
    vespene: int = 125,
    supply_used: int = 23,
    supply_cap: int = 31,
    score: float = 1250.0,
    units: list[dict] | None = None,
) -> dict:
    if units is None:
        units = [{"type": "Probe", "count": 12}, {"type": "Zealot", "count": 3}]
    return {
        "game_step": game_step,
        "game_time_seconds": game_time_seconds,
        "minerals": minerals,
        "vespene": vespene,
        "supply_used": supply_used,
        "supply_cap": supply_cap,
        "score": score,
        "units": units,
    }


class TestFormatStatus:
    def test_contains_step(self) -> None:
        line = format_status(_sample_entry(game_step=2048))
        assert "Step 2048" in line

    def test_contains_time_formatted(self) -> None:
        # 64 seconds = 1:04
        line = format_status(_sample_entry(game_time_seconds=64.0))
        assert "1:04" in line

    def test_time_zero(self) -> None:
        line = format_status(_sample_entry(game_time_seconds=0.0))
        assert "0:00" in line

    def test_contains_resources(self) -> None:
        line = format_status(_sample_entry(minerals=400, vespene=200))
        assert "Minerals: 400" in line
        assert "Gas: 200" in line

    def test_contains_supply(self) -> None:
        line = format_status(_sample_entry(supply_used=15, supply_cap=23))
        assert "Supply: 15/23" in line

    def test_contains_score(self) -> None:
        line = format_status(_sample_entry(score=5000.0))
        assert "Score: 5000" in line

    def test_contains_unit_count(self) -> None:
        units = [{"type": "Probe", "count": 12}, {"type": "Zealot", "count": 3}]
        line = format_status(_sample_entry(units=units))
        assert "Units: 15" in line

    def test_empty_units(self) -> None:
        line = format_status(_sample_entry(units=[]))
        assert "Units: 0" in line

    def test_full_format_matches_spec(self) -> None:
        entry = _sample_entry()
        line = format_status(entry)
        assert line == (
            "[Step 1024 | 1:04] "
            "Minerals: 350  "
            "Gas: 125  "
            "Supply: 23/31  "
            "Score: 1250  "
            "Units: 15"
        )
