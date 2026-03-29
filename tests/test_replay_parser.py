"""Unit tests for replay parsing and stat extraction."""

from __future__ import annotations

import json
from pathlib import Path

from alpha4gate.replay_parser import (
    ParsedReplay,
    ReplayStats,
    TimelineEvent,
    list_replay_logs,
    parse_replay_from_log,
)


def _write_log(path: Path, entries: list[dict]) -> None:
    """Write JSONL entries to a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _sample_entries() -> list[dict]:
    return [
        {
            "game_step": 100,
            "game_time_seconds": 10.0,
            "minerals": 200,
            "vespene": 50,
            "supply_used": 15,
            "supply_cap": 23,
            "units": [{"type": "Probe", "count": 12}, {"type": "Nexus", "count": 1}],
            "actions_taken": [{"action": "Build", "target": "Pylon"}],
            "score": 500,
        },
        {
            "game_step": 500,
            "game_time_seconds": 60.0,
            "minerals": 400,
            "vespene": 150,
            "supply_used": 30,
            "supply_cap": 46,
            "units": [
                {"type": "Probe", "count": 16},
                {"type": "Stalker", "count": 4},
                {"type": "Nexus", "count": 2},
                {"type": "Gateway", "count": 3},
                {"type": "Pylon", "count": 4},
            ],
            "actions_taken": [
                {"action": "Train", "target": "Stalker"},
                {"action": "Build", "target": "Gateway"},
            ],
            "score": 2000,
        },
    ]


class TestTimelineEvent:
    def test_to_dict(self) -> None:
        e = TimelineEvent(game_time_seconds=10.0, event="Build", detail="Pylon")
        d = e.to_dict()
        assert d["game_time_seconds"] == 10.0
        assert d["event"] == "Build"
        assert d["detail"] == "Pylon"


class TestReplayStats:
    def test_to_dict(self) -> None:
        s = ReplayStats(minerals_collected=500, units_produced=10)
        d = s.to_dict()
        assert d["minerals_collected"] == 500
        assert d["units_produced"] == 10

    def test_defaults_zero(self) -> None:
        s = ReplayStats()
        assert s.minerals_collected == 0
        assert s.structures_built == 0


class TestParsedReplay:
    def test_to_dict(self) -> None:
        r = ParsedReplay(
            replay_id="2026-03-29T14-30-00",
            map_name="Simple64",
            duration_seconds=300.0,
            result="win",
            timeline=[TimelineEvent(10.0, "Build", "Pylon")],
            stats=ReplayStats(minerals_collected=1000),
        )
        d = r.to_dict()
        assert d["id"] == "2026-03-29T14-30-00"
        assert d["duration_seconds"] == 300.0
        assert len(d["timeline"]) == 1
        assert d["stats"]["minerals_collected"] == 1000


class TestParseReplayFromLog:
    def test_extracts_timeline(self, tmp_path: Path) -> None:
        log_path = tmp_path / "game_2026-03-29T14-30-00.jsonl"
        _write_log(log_path, _sample_entries())
        parsed = parse_replay_from_log(log_path)
        assert len(parsed.timeline) == 3  # 1 from first entry + 2 from second
        assert parsed.timeline[0].event == "Build"
        assert parsed.timeline[0].detail == "Pylon"

    def test_extracts_duration(self, tmp_path: Path) -> None:
        log_path = tmp_path / "game_test.jsonl"
        _write_log(log_path, _sample_entries())
        parsed = parse_replay_from_log(log_path)
        assert parsed.duration_seconds == 60.0

    def test_extracts_replay_id(self, tmp_path: Path) -> None:
        log_path = tmp_path / "game_2026-03-29T14-30-00.jsonl"
        _write_log(log_path, _sample_entries())
        parsed = parse_replay_from_log(log_path)
        assert parsed.replay_id == "2026-03-29T14-30-00"

    def test_counts_structures(self, tmp_path: Path) -> None:
        log_path = tmp_path / "game_test.jsonl"
        _write_log(log_path, _sample_entries())
        parsed = parse_replay_from_log(log_path)
        # Nexus(2) + Gateway(3) + Pylon(4) = 9 structures
        assert parsed.stats.structures_built == 9

    def test_counts_units(self, tmp_path: Path) -> None:
        log_path = tmp_path / "game_test.jsonl"
        _write_log(log_path, _sample_entries())
        parsed = parse_replay_from_log(log_path)
        # Probe(16) + Stalker(4) = 20 units
        assert parsed.stats.units_produced == 20

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        log_path = tmp_path / "nonexistent.jsonl"
        parsed = parse_replay_from_log(log_path)
        assert parsed.timeline == []
        assert parsed.stats.minerals_collected == 0

    def test_empty_file(self, tmp_path: Path) -> None:
        log_path = tmp_path / "game_empty.jsonl"
        log_path.write_text("")
        parsed = parse_replay_from_log(log_path)
        assert parsed.timeline == []


class TestListReplayLogs:
    def test_lists_log_files(self, tmp_path: Path) -> None:
        (tmp_path / "game_a.jsonl").write_text("{}")
        (tmp_path / "game_b.jsonl").write_text("{}")
        (tmp_path / "other.txt").write_text("not a log")
        logs = list_replay_logs(tmp_path)
        assert len(logs) == 2

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        logs = list_replay_logs(tmp_path / "nope")
        assert logs == []

    def test_sorted_newest_first(self, tmp_path: Path) -> None:
        (tmp_path / "game_2026-01-01.jsonl").write_text("{}")
        (tmp_path / "game_2026-02-01.jsonl").write_text("{}")
        logs = list_replay_logs(tmp_path)
        assert "02-01" in logs[0].name
