"""Tests for Elo ladder math, state I/O, seeding, and update logic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.contracts import LadderEntry, SelfPlayRecord
from orchestrator.ladder import (
    DEFAULT_ELO,
    elo_expected,
    elo_update,
    get_top_n,
    ladder_replay,
    ladder_update,
    load_ladder,
    save_ladder,
    seed_version,
    update_elo,
)

# ---------------------------------------------------------------------------
# Elo math
# ---------------------------------------------------------------------------


class TestEloExpected:
    """Tests for :func:`elo_expected`."""

    def test_equal_ratings_returns_half(self) -> None:
        assert elo_expected(1000.0, 1000.0) == pytest.approx(0.5)

    def test_higher_rating_favoured(self) -> None:
        assert elo_expected(1200.0, 1000.0) > 0.5

    def test_lower_rating_underdog(self) -> None:
        assert elo_expected(1000.0, 1200.0) < 0.5

    def test_symmetric(self) -> None:
        """E(A vs B) + E(B vs A) == 1.0."""
        ea = elo_expected(1100.0, 900.0)
        eb = elo_expected(900.0, 1100.0)
        assert ea + eb == pytest.approx(1.0)

    def test_400_point_gap(self) -> None:
        """400 point gap → ~0.909 expected for the stronger player."""
        assert elo_expected(1400.0, 1000.0) == pytest.approx(
            1.0 / (1.0 + 10.0 ** (-1.0)), rel=1e-6
        )


class TestEloUpdate:
    """Tests for :func:`elo_update`."""

    def test_win_increases_rating(self) -> None:
        new = elo_update(1000.0, 0.5, 1.0, k=32)
        assert new > 1000.0

    def test_loss_decreases_rating(self) -> None:
        new = elo_update(1000.0, 0.5, 0.0, k=32)
        assert new < 1000.0

    def test_draw_equal_ratings_no_change(self) -> None:
        """Draw between equal-rated players → no Elo change."""
        new = elo_update(1000.0, 0.5, 0.5, k=32)
        assert new == pytest.approx(1000.0)

    def test_k32_known_scenario(self) -> None:
        """Equal 1000 vs 1000, player A wins → A gets 1016."""
        new = elo_update(1000.0, 0.5, 1.0, k=32)
        assert new == pytest.approx(1016.0)

    def test_k32_loss_known_scenario(self) -> None:
        """Equal 1000 vs 1000, player A loses → A gets 984."""
        new = elo_update(1000.0, 0.5, 0.0, k=32)
        assert new == pytest.approx(984.0)

    def test_zero_sum(self) -> None:
        """Winner's gain + loser's loss == 0 (Elo is zero-sum)."""
        ea = elo_expected(1000.0, 1000.0)
        eb = elo_expected(1000.0, 1000.0)
        gain = elo_update(1000.0, ea, 1.0, k=32) - 1000.0
        loss = elo_update(1000.0, eb, 0.0, k=32) - 1000.0
        assert gain + loss == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Ladder I/O
# ---------------------------------------------------------------------------


class TestLoadSaveLadder:
    """Tests for :func:`load_ladder` and :func:`save_ladder`."""

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        standings, h2h = load_ladder(tmp_path / "nonexistent.json")
        assert standings == {}
        assert h2h == {}

    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "ladder.json"
        standings = {
            "v0": LadderEntry("v0", 1016.0, 1, "2026-04-17T00:00:00+00:00"),
            "v1": LadderEntry("v1", 984.0, 1, "2026-04-17T00:00:00+00:00"),
        }
        h2h: dict[str, dict[str, dict[str, int]]] = {
            "v0": {"v1": {"wins": 1, "losses": 0, "draws": 0}},
            "v1": {"v0": {"wins": 0, "losses": 1, "draws": 0}},
        }
        save_ladder(standings, h2h, path)

        loaded_standings, loaded_h2h = load_ladder(path)
        assert set(loaded_standings.keys()) == {"v0", "v1"}
        assert loaded_standings["v0"].elo == pytest.approx(1016.0)
        assert loaded_standings["v1"].elo == pytest.approx(984.0)
        assert loaded_standings["v0"].games_played == 1
        assert loaded_h2h["v0"]["v1"]["wins"] == 1
        assert loaded_h2h["v1"]["v0"]["losses"] == 1

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "dir" / "ladder.json"
        save_ladder({}, {}, path)
        assert path.is_file()

    def test_elo_rounded_to_one_decimal(self, tmp_path: Path) -> None:
        path = tmp_path / "ladder.json"
        standings = {
            "v0": LadderEntry("v0", 1016.123456, 1, "2026-04-17T00:00:00+00:00"),
        }
        save_ladder(standings, {}, path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["standings"]["v0"]["elo"] == 1016.1


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


class TestSeedVersion:
    """Tests for :func:`seed_version`."""

    def test_no_manifest_defaults_to_1000(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Version with no manifest seeds at DEFAULT_ELO."""
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError("no manifest")),
        )
        entry = seed_version("v99", {})
        assert entry.elo == DEFAULT_ELO
        assert entry.games_played == 0

    def test_manifest_elo_used(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Version with manifest uses Manifest.elo."""
        from orchestrator.contracts import Manifest, VersionFingerprint

        fake_manifest = Manifest(
            version="v1",
            best="best.pt",
            previous_best=None,
            parent="v0",
            git_sha="abc123",
            timestamp="2026-04-17T00:00:00+00:00",
            elo=1050.0,
            fingerprint=VersionFingerprint(
                feature_dim=64, action_space_size=10, obs_spec_hash="abc"
            ),
        )
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest", lambda v: fake_manifest
        )
        entry = seed_version("v1", {})
        assert entry.elo == pytest.approx(1050.0)


# ---------------------------------------------------------------------------
# update_elo
# ---------------------------------------------------------------------------


def _make_record(
    p1: str = "v0",
    p2: str = "v1",
    winner: str | None = "v0",
    *,
    seat_swap: bool = False,
) -> SelfPlayRecord:
    """Helper to create a SelfPlayRecord for tests."""
    return SelfPlayRecord(
        match_id="test-match-id",
        p1_version=p1,
        p2_version=p2,
        winner=winner,
        map_name="Simple64",
        duration_s=30.0,
        seat_swap=seat_swap,
        timestamp="2026-04-17T12:00:00+00:00",
        error=None,
    )


class TestUpdateElo:
    """Tests for :func:`update_elo`."""

    def _fresh_standings(self) -> dict[str, LadderEntry]:
        return {
            "v0": LadderEntry("v0", 1000.0, 0, "2026-04-17T00:00:00+00:00"),
            "v1": LadderEntry("v1", 1000.0, 0, "2026-04-17T00:00:00+00:00"),
        }

    def test_win_updates_both(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )
        standings = self._fresh_standings()
        h2h: dict[str, dict[str, dict[str, int]]] = {}
        record = _make_record(winner="v0")
        update_elo(standings, h2h, record)

        assert standings["v0"].elo == pytest.approx(1016.0)
        assert standings["v1"].elo == pytest.approx(984.0)
        assert standings["v0"].games_played == 1
        assert standings["v1"].games_played == 1

    def test_draw_splits_evenly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )
        standings = self._fresh_standings()
        h2h: dict[str, dict[str, dict[str, int]]] = {}
        record = _make_record(winner=None)
        update_elo(standings, h2h, record)

        assert standings["v0"].elo == pytest.approx(1000.0)
        assert standings["v1"].elo == pytest.approx(1000.0)

    def test_seeds_missing_versions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )
        standings: dict[str, LadderEntry] = {}
        h2h: dict[str, dict[str, dict[str, int]]] = {}
        record = _make_record(p1="v0", p2="v1", winner="v0")
        update_elo(standings, h2h, record)

        assert "v0" in standings
        assert "v1" in standings
        assert standings["v0"].elo > DEFAULT_ELO
        assert standings["v1"].elo < DEFAULT_ELO

    def test_head_to_head_win(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )
        standings = self._fresh_standings()
        h2h: dict[str, dict[str, dict[str, int]]] = {}
        update_elo(standings, h2h, _make_record(winner="v0"))

        assert h2h["v0"]["v1"]["wins"] == 1
        assert h2h["v0"]["v1"]["losses"] == 0
        assert h2h["v1"]["v0"]["losses"] == 1
        assert h2h["v1"]["v0"]["wins"] == 0

    def test_head_to_head_draw(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )
        standings = self._fresh_standings()
        h2h: dict[str, dict[str, dict[str, int]]] = {}
        update_elo(standings, h2h, _make_record(winner=None))

        assert h2h["v0"]["v1"]["draws"] == 1
        assert h2h["v1"]["v0"]["draws"] == 1

    def test_head_to_head_accumulates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )
        standings = self._fresh_standings()
        h2h: dict[str, dict[str, dict[str, int]]] = {}
        update_elo(standings, h2h, _make_record(winner="v0"))
        update_elo(standings, h2h, _make_record(winner="v1"))
        update_elo(standings, h2h, _make_record(winner=None))

        assert h2h["v0"]["v1"]["wins"] == 1
        assert h2h["v0"]["v1"]["losses"] == 1
        assert h2h["v0"]["v1"]["draws"] == 1
        assert standings["v0"].games_played == 3
        assert standings["v1"].games_played == 3

    def test_elo_is_zero_sum_after_game(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Total Elo across all players is conserved."""
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )
        standings = self._fresh_standings()
        h2h: dict[str, dict[str, dict[str, int]]] = {}
        total_before = sum(e.elo for e in standings.values())
        update_elo(standings, h2h, _make_record(winner="v0"))
        total_after = sum(e.elo for e in standings.values())
        assert total_after == pytest.approx(total_before)


# ---------------------------------------------------------------------------
# get_top_n
# ---------------------------------------------------------------------------


class TestGetTopN:
    """Tests for :func:`get_top_n`."""

    def test_returns_top_n(self) -> None:
        standings = {
            "v0": LadderEntry("v0", 1000.0, 10, "t"),
            "v1": LadderEntry("v1", 1050.0, 10, "t"),
            "v2": LadderEntry("v2", 980.0, 10, "t"),
        }
        assert get_top_n(standings, 2) == ["v1", "v0"]

    def test_n_greater_than_pool(self) -> None:
        standings = {
            "v0": LadderEntry("v0", 1000.0, 10, "t"),
        }
        assert get_top_n(standings, 5) == ["v0"]

    def test_empty_standings(self) -> None:
        assert get_top_n({}, 3) == []


# ---------------------------------------------------------------------------
# ladder_update (Step 4.2)
# ---------------------------------------------------------------------------


def _make_batch_records(
    p1: str, p2: str, games: int, *, p1_win_fraction: float = 0.5
) -> list[SelfPlayRecord]:
    """Generate deterministic SelfPlayRecord list for mock run_batch."""
    records: list[SelfPlayRecord] = []
    p1_wins = int(games * p1_win_fraction)
    for i in range(games):
        winner = p1 if i < p1_wins else p2
        records.append(
            _make_record(
                p1=p1,
                p2=p2,
                winner=winner,
                seat_swap=(i % 2 == 1),
            )
        )
    return records


class TestLadderUpdate:
    """Tests for :func:`ladder_update`."""

    def _mock_run_batch(
        self, p1: str, p2: str, games: int, map_name: str, **_kw: object
    ) -> list[SelfPlayRecord]:
        """p1 always wins all games (deterministic)."""
        return _make_batch_records(p1, p2, games, p1_win_fraction=1.0)

    def test_round_robin_updates_standings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "orchestrator.selfplay.run_batch",
            self._mock_run_batch,
        )
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )

        lpath = tmp_path / "ladder.json"
        standings = ladder_update(
            ["v0", "v1", "v2"], 4, "Simple64", ladder_path=lpath
        )

        # 3 versions -> 3 pairs, 4 games each = 12 games total.
        # Each game touches 2 players -> 24 total games_played across all.
        total_games = sum(e.games_played for e in standings.values())
        assert total_games == 24

        # Elo ordering: v0 > v1 > v2 (p1 always wins).
        assert standings["v0"].elo > standings["v1"].elo > standings["v2"].elo

    def test_versions_none_includes_current(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "orchestrator.selfplay.run_batch",
            self._mock_run_batch,
        )
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )
        monkeypatch.setattr(
            "orchestrator.registry.current_version",
            lambda: "v0",
        )

        lpath = tmp_path / "ladder.json"
        # Pre-seed standings so get_top_n has something to return.
        save_ladder(
            {
                "v1": LadderEntry("v1", 1100.0, 0, "t"),
                "v2": LadderEntry("v2", 1050.0, 0, "t"),
            },
            {},
            lpath,
        )

        standings = ladder_update(None, 2, "Simple64", ladder_path=lpath)

        # current_version() returns "v0" — must be in standings.
        assert "v0" in standings

    def test_ladder_file_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "orchestrator.selfplay.run_batch",
            self._mock_run_batch,
        )
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )

        lpath = tmp_path / "ladder.json"
        ladder_update(["v0", "v1"], 2, "Simple64", ladder_path=lpath)
        assert lpath.is_file()
        data = json.loads(lpath.read_text(encoding="utf-8"))
        assert "standings" in data
        assert "head_to_head" in data


# ---------------------------------------------------------------------------
# ladder_replay (Step 4.2)
# ---------------------------------------------------------------------------


class TestLadderReplay:
    """Tests for :func:`ladder_replay`."""

    def test_replay_produces_expected_standings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )

        jsonl = tmp_path / "results.jsonl"
        records = [
            _make_record(p1="v0", p2="v1", winner="v0"),
            _make_record(p1="v0", p2="v1", winner="v0"),
            _make_record(p1="v0", p2="v1", winner="v1"),
            _make_record(p1="v1", p2="v2", winner="v1"),
        ]
        jsonl.write_text(
            "\n".join(r.to_json() for r in records),
            encoding="utf-8",
        )
        lpath = tmp_path / "ladder.json"
        standings = ladder_replay(jsonl, ladder_path=lpath)

        assert "v0" in standings
        assert "v1" in standings
        assert "v2" in standings
        # v0 won 2 lost 1, v1 won 2 lost 2, v2 lost 1
        assert standings["v0"].elo > DEFAULT_ELO
        assert standings["v2"].elo < DEFAULT_ELO

    def test_replay_clears_existing_standings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Replay starts from empty — old ladder data is NOT carried over."""
        monkeypatch.setattr(
            "orchestrator.ladder.get_manifest",
            lambda v: (_ for _ in ()).throw(FileNotFoundError),
        )

        lpath = tmp_path / "ladder.json"
        # Write an existing ladder with v99.
        save_ladder(
            {"v99": LadderEntry("v99", 2000.0, 100, "t")}, {}, lpath
        )

        # Replay a single game involving only v0 and v1.
        jsonl = tmp_path / "results.jsonl"
        jsonl.write_text(
            _make_record(p1="v0", p2="v1", winner="v0").to_json(),
            encoding="utf-8",
        )
        standings = ladder_replay(jsonl, ladder_path=lpath)

        # v99 should NOT appear — replay starts fresh.
        assert "v99" not in standings
        assert set(standings.keys()) == {"v0", "v1"}


# ---------------------------------------------------------------------------
# CLI argparse (Step 4.4)
# ---------------------------------------------------------------------------


class TestLadderCLI:
    """Tests for ``scripts/ladder.py`` argparse."""

    def test_help_exits_zero(self) -> None:
        import subprocess

        result = subprocess.run(
            ["uv", "run", "python", "scripts/ladder.py", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Elo ladder" in result.stdout

    def test_show_subcommand_help(self) -> None:
        import subprocess

        result = subprocess.run(
            ["uv", "run", "python", "scripts/ladder.py", "show", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--json" in result.stdout

    def test_no_subcommand_exits_one(self) -> None:
        import subprocess

        result = subprocess.run(
            ["uv", "run", "python", "scripts/ladder.py"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
