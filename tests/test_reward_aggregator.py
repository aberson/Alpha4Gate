"""Unit tests for alpha4gate.learning.reward_aggregator."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from alpha4gate.learning.reward_aggregator import aggregate_reward_trends


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Write a list of records as a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _step(
    total_reward: float,
    fired: list[tuple[str, float]],
    *,
    is_terminal: bool = False,
    result: str | None = None,
) -> dict[str, Any]:
    """Build a single step record matching the documented schema."""
    return {
        "game_time": 0.0,
        "total_reward": total_reward,
        "fired_rules": [{"id": rid, "reward": r} for rid, r in fired],
        "is_terminal": is_terminal,
        "result": result,
    }


class TestEmptyAndMissing:
    def test_missing_directory_returns_empty(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        result = aggregate_reward_trends(missing)
        assert result["rules"] == []
        assert result["n_games"] == 0
        assert isinstance(result["generated_at"], str)

    def test_empty_directory_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "reward_logs").mkdir()
        result = aggregate_reward_trends(tmp_path / "reward_logs")
        assert result["rules"] == []
        assert result["n_games"] == 0

    def test_directory_is_a_file_returns_empty(self, tmp_path: Path) -> None:
        # If something unexpected sits at the path, we should not crash.
        fake = tmp_path / "reward_logs"
        fake.write_text("oops", encoding="utf-8")
        result = aggregate_reward_trends(fake)
        assert result["rules"] == []
        assert result["n_games"] == 0


class TestSingleGame:
    def test_single_game_one_rule_one_step(self, tmp_path: Path) -> None:
        logs = tmp_path / "reward_logs"
        _write_jsonl(
            logs / "game_abc123.jsonl",
            [_step(0.05, [("army_supply_growth", 0.05)])],
        )
        result = aggregate_reward_trends(logs)

        assert result["n_games"] == 1
        assert len(result["rules"]) == 1
        rule = result["rules"][0]
        assert rule["rule_id"] == "army_supply_growth"
        assert rule["total_contribution"] == pytest.approx(0.05)
        assert rule["contribution_per_game"] == pytest.approx(0.05)
        assert len(rule["points"]) == 1
        point = rule["points"][0]
        assert point["game_id"] == "abc123"
        assert point["contribution"] == pytest.approx(0.05)
        assert isinstance(point["timestamp"], str) and len(point["timestamp"]) > 0

    def test_single_game_same_rule_fires_multiple_steps_sums(
        self, tmp_path: Path
    ) -> None:
        logs = tmp_path / "reward_logs"
        _write_jsonl(
            logs / "game_g1.jsonl",
            [
                _step(0.1, [("r1", 0.1)]),
                _step(0.2, [("r1", 0.2)]),
                _step(0.3, [("r1", 0.3)], is_terminal=True, result="win"),
            ],
        )
        result = aggregate_reward_trends(logs)
        assert len(result["rules"]) == 1
        rule = result["rules"][0]
        assert rule["rule_id"] == "r1"
        assert rule["total_contribution"] == pytest.approx(0.6)
        assert rule["contribution_per_game"] == pytest.approx(0.6)
        assert len(rule["points"]) == 1  # one point per game, not per step
        assert rule["points"][0]["contribution"] == pytest.approx(0.6)


class TestMultiGame:
    def test_multi_game_multi_rule(self, tmp_path: Path) -> None:
        logs = tmp_path / "reward_logs"

        _write_jsonl(
            logs / "game_1.jsonl",
            [
                _step(0.1, [("r1", 0.1), ("r2", 0.05)]),
                _step(0.2, [("r1", 0.2)]),
            ],
        )
        _write_jsonl(
            logs / "game_2.jsonl",
            [
                _step(0.4, [("r2", 0.4)]),
            ],
        )
        _write_jsonl(
            logs / "game_3.jsonl",
            [
                _step(0.5, [("r1", 0.5), ("r3", 1.0)]),
            ],
        )

        result = aggregate_reward_trends(logs)
        assert result["n_games"] == 3
        rules_by_id = {r["rule_id"]: r for r in result["rules"]}
        assert set(rules_by_id) == {"r1", "r2", "r3"}

        r1 = rules_by_id["r1"]
        assert r1["total_contribution"] == pytest.approx(0.8)  # 0.3 + 0.5
        # r1 has data for 2 of the 3 games, so per_game is divided by 2
        assert r1["contribution_per_game"] == pytest.approx(0.4)
        assert len(r1["points"]) == 2

        r2 = rules_by_id["r2"]
        assert r2["total_contribution"] == pytest.approx(0.45)
        assert r2["contribution_per_game"] == pytest.approx(0.225)
        assert len(r2["points"]) == 2

        r3 = rules_by_id["r3"]
        assert r3["total_contribution"] == pytest.approx(1.0)
        assert r3["contribution_per_game"] == pytest.approx(1.0)
        assert len(r3["points"]) == 1

    def test_n_games_limit_picks_most_recent_by_mtime(self, tmp_path: Path) -> None:
        logs = tmp_path / "reward_logs"
        # Three games, but we'll ask for only the 2 most recent.
        old = logs / "game_old.jsonl"
        mid = logs / "game_mid.jsonl"
        new = logs / "game_new.jsonl"
        _write_jsonl(old, [_step(0.0, [("only_in_old", 9.9)])])
        _write_jsonl(mid, [_step(0.0, [("shared", 1.0)])])
        _write_jsonl(new, [_step(0.0, [("shared", 2.0)])])

        # Force mtimes in ascending order: old < mid < new.
        now = 1_700_000_000.0
        os.utime(old, (now, now))
        os.utime(mid, (now + 10, now + 10))
        os.utime(new, (now + 20, now + 20))

        result = aggregate_reward_trends(logs, n_games=2)
        assert result["n_games"] == 2
        rule_ids = {r["rule_id"] for r in result["rules"]}
        assert "shared" in rule_ids
        # The oldest game must have been excluded.
        assert "only_in_old" not in rule_ids


class TestMalformedInput:
    def test_malformed_lines_are_skipped(self, tmp_path: Path) -> None:
        logs = tmp_path / "reward_logs"
        logs.mkdir()
        path = logs / "game_bad.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            fh.write("this is not json\n")
            fh.write(json.dumps(_step(0.1, [("r1", 0.1)])) + "\n")
            fh.write("{also not json]\n")
            fh.write("\n")  # empty line, should be silently skipped
            fh.write(json.dumps(_step(0.2, [("r1", 0.2)])) + "\n")

        result = aggregate_reward_trends(logs)
        assert result["n_games"] == 1
        assert len(result["rules"]) == 1
        rule = result["rules"][0]
        assert rule["rule_id"] == "r1"
        assert rule["total_contribution"] == pytest.approx(0.3)

    def test_empty_fired_rules_list_is_ignored(self, tmp_path: Path) -> None:
        logs = tmp_path / "reward_logs"
        _write_jsonl(
            logs / "game_g.jsonl",
            [
                _step(0.0, []),  # fired_rules empty list
                _step(0.1, [("r1", 0.1)]),
            ],
        )
        result = aggregate_reward_trends(logs)
        assert result["n_games"] == 1
        assert len(result["rules"]) == 1
        assert result["rules"][0]["rule_id"] == "r1"
        assert result["rules"][0]["total_contribution"] == pytest.approx(0.1)

    def test_missing_fired_rules_key_is_ignored(self, tmp_path: Path) -> None:
        logs = tmp_path / "reward_logs"
        logs.mkdir()
        path = logs / "game_nofire.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "game_time": 1.0,
                        "total_reward": 0.0,
                        "is_terminal": False,
                        "result": None,
                    }
                )
                + "\n"
            )
            fh.write(json.dumps(_step(0.2, [("r1", 0.2)])) + "\n")

        result = aggregate_reward_trends(logs)
        # File is not empty of parseable lines, so it counts as a game.
        assert result["n_games"] == 1
        assert len(result["rules"]) == 1
        assert result["rules"][0]["rule_id"] == "r1"
        assert result["rules"][0]["total_contribution"] == pytest.approx(0.2)

    def test_file_with_no_parseable_lines_is_skipped_entirely(
        self, tmp_path: Path
    ) -> None:
        logs = tmp_path / "reward_logs"
        logs.mkdir()
        bad = logs / "game_trash.jsonl"
        with bad.open("w", encoding="utf-8") as fh:
            fh.write("not json\n")
            fh.write("also not json\n")
            fh.write("\n")
        good = logs / "game_ok.jsonl"
        _write_jsonl(good, [_step(0.1, [("r1", 0.1)])])

        result = aggregate_reward_trends(logs)
        # n_games counts the selected files (both were selected), but the bad
        # file contributes no rule data and therefore no points.
        assert result["n_games"] == 2
        assert len(result["rules"]) == 1
        rule = result["rules"][0]
        assert rule["rule_id"] == "r1"
        # Only one game actually had data for r1, so per-game uses denom=1.
        assert rule["contribution_per_game"] == pytest.approx(0.1)
        assert len(rule["points"]) == 1

    def test_non_utf8_bytes_do_not_abort_aggregation(self, tmp_path: Path) -> None:
        """A stray non-UTF-8 byte must not raise UnicodeDecodeError.

        The docstring promises that malformed lines are skipped with a warning
        log. That contract must hold even when the bytes on disk aren't valid
        UTF-8 — otherwise one corrupted log file would take down the entire
        aggregate_reward_trends call.
        """
        logs = tmp_path / "reward_logs"
        logs.mkdir()

        # File A: a stray 0xff byte followed by a valid JSON line. The bad byte
        # gets replaced with U+FFFD under errors="replace", which means the
        # first line fails JSON parsing (logged + skipped) and the second
        # line's valid record still gets picked up.
        bad_path = logs / "game_corrupt.jsonl"
        valid_line = json.dumps(_step(0.25, [("r_recovered", 0.25)])) + "\n"
        bad_path.write_bytes(b"\xff{broken first line}\n" + valid_line.encode("utf-8"))

        # File B: a fully clean file, just to confirm aggregation still runs.
        _write_jsonl(
            logs / "game_clean.jsonl",
            [_step(0.5, [("r_clean", 0.5)])],
        )

        # Must not raise.
        result = aggregate_reward_trends(logs)

        assert result["n_games"] == 2
        rules_by_id = {r["rule_id"]: r for r in result["rules"]}
        # Clean file's rule is present.
        assert "r_clean" in rules_by_id
        assert rules_by_id["r_clean"]["total_contribution"] == pytest.approx(0.5)
        # Valid line from AFTER the bad byte in the corrupt file is still
        # recovered, demonstrating the bad byte didn't abort mid-file parsing.
        assert "r_recovered" in rules_by_id
        assert rules_by_id["r_recovered"]["total_contribution"] == pytest.approx(0.25)

    def test_completely_empty_file_is_skipped(self, tmp_path: Path) -> None:
        logs = tmp_path / "reward_logs"
        logs.mkdir()
        empty = logs / "game_empty.jsonl"
        empty.write_text("", encoding="utf-8")
        good = logs / "game_good.jsonl"
        _write_jsonl(good, [_step(0.5, [("r1", 0.5)])])

        result = aggregate_reward_trends(logs)
        assert result["n_games"] == 2
        assert len(result["rules"]) == 1
        rule = result["rules"][0]
        assert rule["total_contribution"] == pytest.approx(0.5)
        # Only one game had data for this rule.
        assert rule["contribution_per_game"] == pytest.approx(0.5)
