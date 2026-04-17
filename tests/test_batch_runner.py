"""Unit tests for batch runner and statistics aggregation."""

from __future__ import annotations

import json
from pathlib import Path

from bots.v0.batch_runner import (
    GameRecord,
    StatsAggregates,
    append_stats_game,
    compute_aggregates,
    load_stats,
    record_game,
    save_stats,
)


def _sample_games() -> list[GameRecord]:
    return [
        GameRecord(
            timestamp="2026-03-29T14:00:00Z",
            map_name="Simple64",
            opponent="built-in-easy",
            result="win",
            duration_seconds=300.0,
            build_order_used="4gate",
            score=2000,
        ),
        GameRecord(
            timestamp="2026-03-29T14:10:00Z",
            map_name="Simple64",
            opponent="built-in-easy",
            result="win",
            duration_seconds=250.0,
            build_order_used="4gate",
            score=2500,
        ),
        GameRecord(
            timestamp="2026-03-29T14:20:00Z",
            map_name="CatalystLE",
            opponent="built-in-medium",
            result="loss",
            duration_seconds=400.0,
            build_order_used="4gate",
            score=1500,
        ),
    ]


class TestGameRecord:
    def test_to_dict(self) -> None:
        g = _sample_games()[0]
        d = g.to_dict()
        assert d["map"] == "Simple64"
        assert d["result"] == "win"
        assert d["score"] == 2000

    def test_from_dict(self) -> None:
        data = {
            "timestamp": "2026-01-01T00:00:00Z",
            "map": "Simple64",
            "opponent": "test",
            "result": "loss",
            "duration_seconds": 100.0,
            "build_order_used": "4gate",
            "score": 500,
        }
        g = GameRecord.from_dict(data)
        assert g.map_name == "Simple64"
        assert g.result == "loss"

    def test_roundtrip(self) -> None:
        original = _sample_games()[0]
        restored = GameRecord.from_dict(original.to_dict())
        assert restored.map_name == original.map_name
        assert restored.result == original.result
        assert restored.score == original.score


class TestComputeAggregates:
    def test_total_wins_losses(self) -> None:
        agg = compute_aggregates(_sample_games())
        assert agg.total_wins == 2
        assert agg.total_losses == 1

    def test_by_map(self) -> None:
        agg = compute_aggregates(_sample_games())
        assert agg.by_map["Simple64"]["wins"] == 2
        assert agg.by_map["Simple64"]["losses"] == 0
        assert agg.by_map["CatalystLE"]["wins"] == 0
        assert agg.by_map["CatalystLE"]["losses"] == 1

    def test_by_opponent(self) -> None:
        agg = compute_aggregates(_sample_games())
        assert agg.by_opponent["built-in-easy"]["wins"] == 2
        assert agg.by_opponent["built-in-medium"]["losses"] == 1

    def test_by_build_order(self) -> None:
        agg = compute_aggregates(_sample_games())
        assert agg.by_build_order["4gate"]["wins"] == 2
        assert agg.by_build_order["4gate"]["losses"] == 1

    def test_empty_games(self) -> None:
        agg = compute_aggregates([])
        assert agg.total_wins == 0
        assert agg.total_losses == 0
        assert agg.by_map == {}


class TestStatsAggregates:
    def test_to_dict(self) -> None:
        agg = StatsAggregates(total_wins=3, total_losses=1)
        d = agg.to_dict()
        assert d["total_wins"] == 3
        assert d["total_losses"] == 1


class TestPersistence:
    def test_save_and_load(self, tmp_path: Path) -> None:
        games = _sample_games()
        path = tmp_path / "stats.json"
        save_stats(games, path)
        loaded_games, loaded_agg = load_stats(path)
        assert len(loaded_games) == 3
        assert loaded_agg.total_wins == 2

    def test_load_nonexistent(self, tmp_path: Path) -> None:
        games, agg = load_stats(tmp_path / "nope.json")
        assert games == []
        assert agg.total_wins == 0

    def test_saved_file_valid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "stats.json"
        save_stats(_sample_games(), path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "games" in data
        assert "aggregates" in data
        assert data["aggregates"]["total_wins"] == 2


class TestAppendStatsGame:
    """Phase 4.6 Step 2: per-game stats append for the trainer path.

    The trainer cannot use :func:`save_stats` directly because that helper
    rewrites the whole file from a full ``list[GameRecord]``. Per-game
    overwrites from SC2Env would be O(N^2) for N games in a cycle. The
    :func:`append_stats_game` seam loads, appends, recomputes aggregates,
    and writes — it is the additive callable seam that SC2Env uses after
    each trainer game completes.
    """

    def _sample_record(self, result: str = "win") -> GameRecord:
        return GameRecord(
            timestamp="2026-04-11T12:00:00Z",
            map_name="Simple64",
            opponent="built-in-1",
            result=result,
            duration_seconds=300.0,
            build_order_used="4gate",
            score=0,
        )

    def test_append_creates_file_when_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "stats.json"
        assert not path.exists()

        append_stats_game(path, self._sample_record("win"))

        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["games"]) == 1
        assert data["games"][0]["result"] == "win"
        assert data["aggregates"]["total_wins"] == 1
        assert data["aggregates"]["total_losses"] == 0

    def test_append_extends_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "stats.json"
        save_stats(_sample_games(), path)  # 3 prior games (2W, 1L)

        append_stats_game(path, self._sample_record("loss"))

        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["games"]) == 4
        assert data["aggregates"]["total_wins"] == 2
        assert data["aggregates"]["total_losses"] == 2

    def test_append_recomputes_by_opponent(self, tmp_path: Path) -> None:
        """Aggregates must include the newly appended game's opponent.

        Regression guard: if ``append_stats_game`` forgot to recompute
        aggregates, ``by_opponent`` would still reflect the pre-append
        list and the dashboard would undercount trainer games.
        """
        path = tmp_path / "stats.json"
        append_stats_game(path, self._sample_record("win"))
        append_stats_game(path, self._sample_record("loss"))

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["aggregates"]["by_opponent"] == {
            "built-in-1": {"wins": 1, "losses": 1}
        }

    def test_append_creates_parent_dir(self, tmp_path: Path) -> None:
        """``data/stats.json`` may live in a dir that does not exist yet."""
        path = tmp_path / "nested" / "stats.json"
        append_stats_game(path, self._sample_record("win"))
        assert path.exists()


class TestRecordGame:
    def test_appends_to_list(self) -> None:
        games: list[GameRecord] = []
        record = record_game(
            games,
            map_name="Simple64",
            opponent="test",
            result="win",
            duration_seconds=200.0,
            build_order_used="4gate",
            score=1000,
        )
        assert len(games) == 1
        assert record.result == "win"
        assert record.map_name == "Simple64"

    def test_timestamp_auto_set(self) -> None:
        games: list[GameRecord] = []
        record = record_game(
            games,
            map_name="Simple64",
            opponent="test",
            result="loss",
            duration_seconds=100.0,
            build_order_used="4gate",
            score=500,
        )
        assert "T" in record.timestamp  # ISO format
