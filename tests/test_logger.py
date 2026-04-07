"""Unit tests for JSONL serialization and queue drain."""

from __future__ import annotations

import json
from pathlib import Path

from alpha4gate.logger import GameLogger


def _sample_entry(game_step: int = 100, minerals: int = 350) -> dict:
    return {
        "timestamp": "2026-03-28T14:30:05.123+00:00",
        "game_step": game_step,
        "game_time_seconds": 64.0,
        "minerals": minerals,
        "vespene": 125,
        "supply_used": 23,
        "supply_cap": 31,
        "units": [{"type": "Probe", "count": 12}],
        "actions_taken": [],
        "score": 1250,
    }


class TestGameLogger:
    def test_writes_jsonl_file(self, tmp_path: Path) -> None:
        logger = GameLogger(log_dir=tmp_path / "logs")
        logger.start()
        logger.put(_sample_entry(game_step=1))
        logger.put(_sample_entry(game_step=2))
        logger.stop()

        assert logger.log_path is not None
        lines = logger.log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        # Each line is valid JSON
        for line in lines:
            parsed = json.loads(line)
            assert "game_step" in parsed

    def test_deduplication_skips_same_step(self, tmp_path: Path) -> None:
        logger = GameLogger(log_dir=tmp_path / "logs")
        logger.start()
        logger.put(_sample_entry(game_step=10))
        logger.put(_sample_entry(game_step=10))  # duplicate
        logger.put(_sample_entry(game_step=11))
        logger.stop()

        assert logger.log_path is not None
        lines = logger.log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["game_step"] == 10
        assert json.loads(lines[1])["game_step"] == 11

    def test_deduplication_skips_lower_step(self, tmp_path: Path) -> None:
        logger = GameLogger(log_dir=tmp_path / "logs")
        logger.start()
        logger.put(_sample_entry(game_step=20))
        logger.put(_sample_entry(game_step=15))  # lower than last
        logger.stop()

        assert logger.log_path is not None
        lines = logger.log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_data_preserved_in_jsonl(self, tmp_path: Path) -> None:
        logger = GameLogger(log_dir=tmp_path / "logs")
        logger.start()
        entry = _sample_entry(game_step=5, minerals=999)
        logger.put(entry)
        logger.stop()

        assert logger.log_path is not None
        parsed = json.loads(logger.log_path.read_text(encoding="utf-8").strip())
        assert parsed["minerals"] == 999
        assert parsed["game_step"] == 5
        assert parsed["units"] == [{"type": "Probe", "count": 12}]
