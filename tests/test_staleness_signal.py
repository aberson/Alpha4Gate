"""Tests for ``orchestrator.staleness`` — per-version policy-staleness signal.

These exercise :func:`compute_staleness` logic against a synthetic
``training.db`` + ``checkpoints/*.zip`` + ``reward_rules.json`` staged under a
``tmp_path`` tree, with the registry's ``_repo_root`` monkeypatched to point at
that tree (the same seam used by ``test_evolve`` / ``test_baselines``). A later
step adds a real-DB smoke + routing test, so this file stays focused on the
core verdict logic.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from orchestrator import registry
from orchestrator.staleness import StalenessReport, compute_staleness

# Mirror of the columns from ``bots/v0/learning/database.py``'s ``games``
# table. Only ``result`` is read by the module (the DB is version-scoped by
# directory, so ``model_version`` is not filtered on), but we create the full
# shape so the synthetic DB matches production exactly.
_GAMES_SCHEMA = """\
CREATE TABLE games (
    game_id       TEXT PRIMARY KEY,
    map_name      TEXT NOT NULL,
    difficulty    INTEGER NOT NULL,
    result        TEXT NOT NULL,
    duration_secs REAL NOT NULL,
    total_reward  REAL NOT NULL,
    model_version TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _stage_version(root: Path, version: str) -> Path:
    """Create ``<root>/bots/<version>/data/`` and return the data dir."""
    data_dir = root / "bots" / version / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# Realistic ``model_version`` labels production writes into the (already
# version-scoped) per-version DB: decision-mode strings, a checkpoint stem, and
# the per-cycle training counter. NONE of these is the package version string
# like "v0" — the staleness query is unfiltered and must count them all.
_MIXED_MODEL_LABELS = ("rules", "hybrid", "model_step_4096", "v3")


def _write_games_db(
    data_dir: Path,
    results: list[str],
    *,
    version: str,
    model_labels: tuple[str, ...] = _MIXED_MODEL_LABELS,
) -> Path:
    """Write a ``training.db`` with one ``games`` row per result, in order.

    ``results`` are inserted oldest-first; ``rowid`` therefore increases with
    chronological order, matching how production appends games. Each row gets a
    ``model_version`` cycled from ``model_labels`` — deliberately mixed and
    NEVER equal to ``version`` (the package string) — to prove the unfiltered
    query counts every row in this version-scoped DB. ``version`` is used only
    for the ``game_id`` prefix.
    """
    db_path = data_dir / "training.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_GAMES_SCHEMA)
        for i, result in enumerate(results):
            mv = model_labels[i % len(model_labels)]
            conn.execute(
                "INSERT INTO games (game_id, map_name, difficulty, result, "
                "duration_secs, total_reward, model_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"{version}-g{i}", "Simple64", 3, result, 60.0, 0.0, mv),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _write_checkpoint(data_dir: Path, *, mtime: float) -> Path:
    """Write ``checkpoints/model.zip`` with the given mtime."""
    ckpt_dir = data_dir / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt = ckpt_dir / "model.zip"
    ckpt.write_bytes(b"PK\x03\x04")  # zip magic; content irrelevant
    os.utime(ckpt, (mtime, mtime))
    return ckpt


def _write_reward_rules(data_dir: Path, *, mtime: float) -> Path:
    """Write ``reward_rules.json`` with the given mtime."""
    path = data_dir / "reward_rules.json"
    path.write_text("{}", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _redirect_repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point registry path resolution at ``tmp_path``.

    ``compute_staleness`` resolves the data dir (and thus ``training.db``,
    ``checkpoints/`` and ``reward_rules.json``) via ``get_data_dir``, which
    calls ``registry._repo_root``, so a single monkeypatch redirects all path
    resolution at the staged tree.
    """
    monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)


# ---------------------------------------------------------------------------
# (a) flat/falling trend AND reward edited after checkpoint -> is_stale True
# ---------------------------------------------------------------------------


def test_falling_trend_and_rewards_after_checkpoint_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _redirect_repo_root(tmp_path, monkeypatch)
    data_dir = _stage_version(tmp_path, "v0")
    # Falling win rate: 10 wins then 10 losses (oldest -> newest).
    results = ["win"] * 10 + ["loss"] * 10
    _write_games_db(data_dir, results, version="v0")
    # Checkpoint at t=1000, reward rules edited later at t=2000 -> negative age.
    _write_checkpoint(data_dir, mtime=1000.0)
    _write_reward_rules(data_dir, mtime=2000.0)

    report = compute_staleness("v0")

    assert isinstance(report, StalenessReport)
    assert report.is_stale is True
    assert report.eval_wr_trend < 0.0
    assert report.checkpoint_age_seconds == -1000
    assert "STALE" in report.reason


def test_flat_trend_and_rewards_after_checkpoint_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _redirect_repo_root(tmp_path, monkeypatch)
    data_dir = _stage_version(tmp_path, "v0")
    # Perfectly flat: alternating win/loss -> slope ~ 0 (<= threshold).
    results = ["win", "loss"] * 10
    _write_games_db(data_dir, results, version="v0")
    _write_checkpoint(data_dir, mtime=1000.0)
    _write_reward_rules(data_dir, mtime=1500.0)

    report = compute_staleness("v0")

    assert report.is_stale is True
    assert report.eval_wr_trend <= 0.01
    assert report.checkpoint_age_seconds < 0


def test_perfectly_flat_series_labelled_flat_not_falling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flat series yields a ~-1e-17 slope; the reason must say 'flat', not 'falling'."""
    _redirect_repo_root(tmp_path, monkeypatch)
    data_dir = _stage_version(tmp_path, "v0")
    # All-wins: regression slope is exactly flat modulo float noise (often a
    # tiny negative like -1e-17), which previously mislabelled the trend.
    _write_games_db(data_dir, ["win"] * 20, version="v0")
    _write_checkpoint(data_dir, mtime=1000.0)
    _write_reward_rules(data_dir, mtime=2000.0)

    report = compute_staleness("v0")
    assert abs(report.eval_wr_trend) < 1e-9  # genuinely flat (float noise only)
    assert "trend flat" in report.reason
    assert "falling" not in report.reason
    # is_stale arithmetic is unchanged: flat (<= threshold) AND rewards-after.
    assert report.is_stale is True


# ---------------------------------------------------------------------------
# (b) rising trend -> is_stale False (even if rewards newer than checkpoint)
# ---------------------------------------------------------------------------


def test_rising_trend_is_not_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _redirect_repo_root(tmp_path, monkeypatch)
    data_dir = _stage_version(tmp_path, "v0")
    # Rising win rate: losses then wins (oldest -> newest) -> positive slope.
    results = ["loss"] * 10 + ["win"] * 10
    _write_games_db(data_dir, results, version="v0")
    # Rewards newer than checkpoint, but trend is rising -> NOT stale.
    _write_checkpoint(data_dir, mtime=1000.0)
    _write_reward_rules(data_dir, mtime=2000.0)

    report = compute_staleness("v0")

    assert report.eval_wr_trend > 0.01
    assert report.checkpoint_age_seconds < 0
    assert report.is_stale is False
    assert "not stale" in report.reason


# ---------------------------------------------------------------------------
# (c) checkpoint newer than reward edit -> is_stale False
# ---------------------------------------------------------------------------


def test_checkpoint_newer_than_rewards_is_not_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _redirect_repo_root(tmp_path, monkeypatch)
    data_dir = _stage_version(tmp_path, "v0")
    # Falling trend so the trend half of the AND is satisfied...
    results = ["win"] * 10 + ["loss"] * 10
    _write_games_db(data_dir, results, version="v0")
    # ...but checkpoint is NEWER than reward rules -> positive age -> NOT stale.
    _write_reward_rules(data_dir, mtime=1000.0)
    _write_checkpoint(data_dir, mtime=2000.0)

    report = compute_staleness("v0")

    assert report.eval_wr_trend <= 0.01
    assert report.checkpoint_age_seconds == 1000
    assert report.is_stale is False


# ---------------------------------------------------------------------------
# (d) < min_games -> ValueError
# ---------------------------------------------------------------------------


def test_fewer_than_min_games_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _redirect_repo_root(tmp_path, monkeypatch)
    data_dir = _stage_version(tmp_path, "v0")
    _write_games_db(data_dir, ["win"] * 5, version="v0")
    _write_checkpoint(data_dir, mtime=1000.0)
    _write_reward_rules(data_dir, mtime=2000.0)

    with pytest.raises(ValueError, match="only 5 game"):
        compute_staleness("v0", min_games=10)


def test_missing_db_raises_value_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _redirect_repo_root(tmp_path, monkeypatch)
    _stage_version(tmp_path, "v0")  # data dir exists, but no training.db

    with pytest.raises(ValueError, match="no training.db"):
        compute_staleness("v0")


def test_all_model_version_labels_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every row counts regardless of its ``model_version`` label.

    The per-version ``training.db`` is version-scoped by directory and
    production writes decision-mode / checkpoint / cycle labels (never the
    package version) into ``model_version``. The query is unfiltered, so a mix
    of labels — none equal to "v0" — must all satisfy ``min_games`` and feed
    the trend. Codifies the HIGH producer-consumer fix (was previously a
    wrong-behavior "other versions are filtered out" test).
    """
    _redirect_repo_root(tmp_path, monkeypatch)
    data_dir = _stage_version(tmp_path, "v0")
    # 20 rows, each labelled from the mixed set ("rules"/"hybrid"/stem/"v3"):
    # falling trend so the verdict half is exercised end-to-end.
    results = ["win"] * 10 + ["loss"] * 10
    _write_games_db(data_dir, results, version="v0", model_labels=_MIXED_MODEL_LABELS)
    _write_checkpoint(data_dir, mtime=1000.0)
    _write_reward_rules(data_dir, mtime=2000.0)

    # No ValueError despite 0 rows where model_version == "v0": all 20 counted.
    report = compute_staleness("v0", min_games=10)
    assert report.eval_wr_trend < 0.0
    assert report.is_stale is True


def test_single_label_equal_to_decision_mode_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real-world all-"rules" DB (the common production case) counts fully."""
    _redirect_repo_root(tmp_path, monkeypatch)
    data_dir = _stage_version(tmp_path, "v0")
    _write_games_db(data_dir, ["win"] * 10 + ["loss"] * 10, version="v0", model_labels=("rules",))
    _write_checkpoint(data_dir, mtime=1000.0)
    _write_reward_rules(data_dir, mtime=2000.0)

    report = compute_staleness("v0", min_games=10)
    assert len(report.recent_win_rates) == 2  # 20 games -> two 10-game buckets


def test_corrupt_db_missing_games_table_raises_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A present ``training.db`` with no ``games`` table -> ValueError, not OperationalError."""
    _redirect_repo_root(tmp_path, monkeypatch)
    data_dir = _stage_version(tmp_path, "v0")
    # File exists (so the missing-db branch is skipped) but lacks the games table.
    db_path = data_dir / "training.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE not_games (x INTEGER)")
        conn.commit()
    finally:
        conn.close()
    _write_checkpoint(data_dir, mtime=1000.0)
    _write_reward_rules(data_dir, mtime=2000.0)

    with pytest.raises(ValueError, match="unreadable") as excinfo:
        compute_staleness("v0")
    # The raw sqlite error must NOT leak as the public exception type.
    assert not isinstance(excinfo.value, sqlite3.Error)


# ---------------------------------------------------------------------------
# (e) configurable slope_threshold / window
# ---------------------------------------------------------------------------


def test_slope_threshold_flips_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A gently-rising trend is 'stale' under a high threshold, not under 0."""
    _redirect_repo_root(tmp_path, monkeypatch)
    data_dir = _stage_version(tmp_path, "v0")
    # Gentle rise: one win late in an otherwise-flat losing series -> small +slope.
    results = ["loss"] * 18 + ["win"] * 2
    _write_games_db(data_dir, results, version="v0")
    _write_checkpoint(data_dir, mtime=1000.0)
    _write_reward_rules(data_dir, mtime=2000.0)

    slope = compute_staleness("v0").eval_wr_trend
    assert 0.0 < slope  # genuinely rising

    # Default threshold 0.01: rising above it -> not stale.
    assert compute_staleness("v0").is_stale is False
    # Threshold above the slope: now "flat-or-falling" -> stale.
    assert compute_staleness("v0", slope_threshold=slope + 0.01).is_stale is True


def test_window_limits_games_analysed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``window`` caps how many recent games feed the regression + buckets."""
    _redirect_repo_root(tmp_path, monkeypatch)
    data_dir = _stage_version(tmp_path, "v0")
    # 40 games total; the most-recent 10 are all wins (rising within window).
    results = ["loss"] * 30 + ["win"] * 10
    _write_games_db(data_dir, results, version="v0")
    _write_checkpoint(data_dir, mtime=1000.0)
    _write_reward_rules(data_dir, mtime=2000.0)

    # window=10 -> only the trailing 10 wins -> one bucket, WR 1.0, flat slope 0.
    report = compute_staleness("v0", window=10)
    assert report.recent_win_rates == (1.0,)
    assert report.eval_wr_trend == pytest.approx(0.0)

    # window=20 -> 10 losses then 10 wins -> two buckets (0.0, 1.0), rising.
    report20 = compute_staleness("v0", window=20)
    assert report20.recent_win_rates == (0.0, 1.0)
    assert report20.eval_wr_trend > 0.0


def test_recent_win_rates_oldest_to_newest_buckets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Buckets are 10-game chunks, oldest -> newest, with a short tail."""
    _redirect_repo_root(tmp_path, monkeypatch)
    data_dir = _stage_version(tmp_path, "v0")
    # 25 games: bucket0 all-win, bucket1 all-loss, bucket2 (5 games) half-ish.
    results = ["win"] * 10 + ["loss"] * 10 + ["win", "win", "loss", "loss", "loss"]
    _write_games_db(data_dir, results, version="v0")
    _write_checkpoint(data_dir, mtime=1000.0)
    _write_reward_rules(data_dir, mtime=2000.0)

    report = compute_staleness("v0")
    assert report.recent_win_rates == pytest.approx((1.0, 0.0, 2.0 / 5.0))


# ---------------------------------------------------------------------------
# Missing-artifact handling (no crash; stale-leaning when never trained)
# ---------------------------------------------------------------------------


def test_no_checkpoint_treated_as_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No checkpoint + falling trend -> stale (never trained, age forced -1)."""
    _redirect_repo_root(tmp_path, monkeypatch)
    data_dir = _stage_version(tmp_path, "v0")
    _write_games_db(data_dir, ["win"] * 10 + ["loss"] * 10, version="v0")
    _write_reward_rules(data_dir, mtime=2000.0)  # rules exist, no checkpoint

    report = compute_staleness("v0")
    assert report.checkpoint_age_seconds == -1
    assert report.is_stale is True
    assert "no checkpoint" in report.reason


def test_no_reward_rules_not_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Checkpoint but no reward_rules.json -> nothing to lag -> not stale."""
    _redirect_repo_root(tmp_path, monkeypatch)
    data_dir = _stage_version(tmp_path, "v0")
    _write_games_db(data_dir, ["win"] * 10 + ["loss"] * 10, version="v0")
    _write_checkpoint(data_dir, mtime=1000.0)  # checkpoint, no reward rules

    report = compute_staleness("v0")
    assert report.checkpoint_age_seconds == 1
    assert report.is_stale is False
    assert "no reward_rules.json" in report.reason
