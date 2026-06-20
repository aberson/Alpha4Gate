"""Staleness signal for a bot version (Phase 7).

Answers one question: *is this version's neural policy stale relative to the
reward rules that should be shaping it?* A version is "stale" when its recent
evaluation win-rate trend is flat-or-falling **and** its ``reward_rules.json``
was edited after the newest training checkpoint — i.e. the reward signal moved
but the policy never retrained on it.

Public surface:

- :class:`StalenessReport` — frozen dataclass carrying the verdict + the
  numbers behind it.
- :func:`compute_staleness` — read a version's ``training.db`` (read-only),
  ``checkpoints/`` and ``reward_rules.json``, and produce a
  :class:`StalenessReport`.

Implementation notes:

* This module is part of the orchestrator substrate and must **NOT** import
  ``bots.current`` or ``bots.<version>`` — doing so trips the MetaPathFinder
  installed by ``bots/current/__init__.py``. The per-version ``training.db`` is
  read directly with :mod:`sqlite3` (read-only URI) + :mod:`pathlib`; paths are
  resolved through :mod:`orchestrator.registry` (same package, safe to import).
* All three reads — ``training.db``, ``checkpoints/`` and ``reward_rules.json``
  — derive from a single ``get_data_dir(version)`` so the win-series and the
  checkpoint/reward mtimes always come from the *same* data directory (never
  fusing a legacy ``data/`` win-series with a per-version checkpoint mtime in a
  mixed-migration state).
* The ``games`` table schema mirrored here is owned by
  ``bots/v0/learning/database.py`` (columns ``game_id, map_name, difficulty,
  result, duration_secs, total_reward, model_version, created_at``); only
  ``result`` is read. The DB is version-scoped by directory, so no
  ``model_version`` filter is applied (production never writes the package
  version into that column).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from orchestrator.registry import get_data_dir

__all__ = [
    "StalenessReport",
    "compute_staleness",
]

_BUCKET_SIZE = 10
"""Games per win-rate bucket in :attr:`StalenessReport.recent_win_rates`."""


@dataclass(frozen=True)
class StalenessReport:
    """Verdict on whether a version's policy is stale, plus the evidence.

    Attributes
    ----------
    is_stale:
        ``True`` when the eval win-rate trend is flat-or-falling AND the reward
        rules were edited after the newest checkpoint.
    eval_wr_trend:
        Slope of a linear regression of per-game win (0/1) vs game-index over
        the analysis window. Negative = falling, ~0 = flat, positive = rising.
    checkpoint_age_seconds:
        Newest checkpoint mtime minus ``reward_rules.json`` mtime, as an int.
        Negative means the reward rules were edited *after* the newest
        checkpoint (the policy is behind the reward signal).
    recent_win_rates:
        Windowed win rates used to summarise the trend (one per
        :data:`_BUCKET_SIZE`-game bucket), oldest -> newest.
    reason:
        Human-readable explanation of which condition(s) fired.
    """

    is_stale: bool
    eval_wr_trend: float
    checkpoint_age_seconds: int
    recent_win_rates: tuple[float, ...]
    reason: str


def _read_recent_results(db_path: Path, version: str, window: int) -> list[str]:
    """Return up to ``window`` recent ``result`` strings, newest-first.

    Opens ``db_path`` read-only via a sqlite URI connection so a concurrent
    backend writer is never blocked and this module never mutates the file.

    The per-version ``training.db`` is already version-scoped (it lives under
    ``bots/<version>/data/``), so this intentionally does **not** filter on
    ``model_version`` — production writes decision-mode / checkpoint / training
    -cycle labels into that column (``"rules"``, ``"hybrid"``, a checkpoint
    stem, ``f"v{cycle}"``), never the package version string. Filtering on the
    package version would match zero rows. This mirrors
    ``TrainingDB.get_recent_win_rate`` (``bots/v0/learning/database.py``), which
    has no filter either. ``version`` is retained only for error messages.

    A present-but-malformed/corrupt/locked DB (e.g. missing the ``games``
    table) raises :class:`sqlite3.Error`; the caller wraps it as a
    :class:`ValueError` so the public surface only ever raises ``ValueError``.
    """
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT result FROM games ORDER BY rowid DESC LIMIT ?",
            (window,),
        ).fetchall()
    finally:
        conn.close()
    return [str(r[0]) for r in rows]


def _win_series(results_chrono: list[str]) -> list[float]:
    """Map result strings to a 0/1 win series (1.0 for ``'win'``)."""
    return [1.0 if r == "win" else 0.0 for r in results_chrono]


def _win_rate_buckets(wins: list[float], bucket_size: int) -> tuple[float, ...]:
    """Win rate per ``bucket_size``-game chunk, oldest -> newest.

    The final chunk may be shorter than ``bucket_size``; its win rate is over
    however many games it holds.
    """
    buckets: list[float] = []
    for start in range(0, len(wins), bucket_size):
        chunk = wins[start : start + bucket_size]
        buckets.append(sum(chunk) / len(chunk))
    return tuple(buckets)


def _regression_slope(wins: list[float]) -> float:
    """Slope of win(0/1) vs game-index via least-squares linear regression.

    Returns ``0.0`` for a degenerate window (<2 points), where a slope is
    undefined and "flat" is the only sensible reading.
    """
    if len(wins) < 2:
        return 0.0
    x = np.arange(len(wins), dtype=float)
    y = np.asarray(wins, dtype=float)
    slope = float(np.polyfit(x, y, 1)[0])
    return slope


def _newest_checkpoint_mtime(checkpoints_dir: Path) -> float | None:
    """Return the newest ``*.zip`` mtime under ``checkpoints_dir``, or None.

    None means "no checkpoints" — either the directory is absent or holds no
    ``*.zip`` files (never trained).
    """
    if not checkpoints_dir.is_dir():
        return None
    mtimes = [p.stat().st_mtime for p in checkpoints_dir.glob("*.zip") if p.is_file()]
    if not mtimes:
        return None
    return max(mtimes)


def compute_staleness(
    version: str,
    *,
    window: int = 30,
    min_games: int = 10,
    slope_threshold: float = 0.01,
) -> StalenessReport:
    """Compute the staleness verdict for ``version``.

    Parameters
    ----------
    version:
        Bot version string (e.g. ``"v0"``). Resolves the per-version data dir
        (``training.db``, ``checkpoints/``, ``reward_rules.json``). The DB is
        version-scoped by directory, so no ``model_version`` filter is applied.
    window:
        Maximum number of most-recent games to analyse.
    min_games:
        Minimum games required; fewer raises :class:`ValueError`.
    slope_threshold:
        The trend is considered flat-or-falling when ``eval_wr_trend <=
        slope_threshold``.

    Returns
    -------
    StalenessReport
        Populated frozen report.

    Raises
    ------
    ValueError
        If fewer than ``min_games`` games exist for ``version`` (including the
        case where ``training.db`` does not exist), or if a present
        ``training.db`` is unreadable (corrupt/locked/missing the ``games``
        table) — the underlying :class:`sqlite3.Error` is wrapped.
    """
    # Single source of truth for the data directory: db, checkpoints/ and
    # reward_rules.json all resolve from the SAME get_data_dir(version) so the
    # win-series and the mtimes never come from two different directories.
    data_dir = get_data_dir(version)
    db_path = data_dir / "training.db"
    checkpoints_dir = data_dir / "checkpoints"
    reward_rules_path = data_dir / "reward_rules.json"

    if not db_path.exists():
        raise ValueError(
            f"no training.db for version {version!r} at {db_path} (need >= {min_games} games)"
        )

    try:
        results_desc = _read_recent_results(db_path, version, window)
    except sqlite3.Error as exc:
        raise ValueError(
            f"training.db for version {version!r} at {db_path} is unreadable "
            f"(corrupt, locked, or missing the 'games' table): {exc}"
        ) from exc
    if len(results_desc) < min_games:
        raise ValueError(
            f"version {version!r} has only {len(results_desc)} game(s) (need >= {min_games})"
        )

    # DESC from SQL -> reverse to chronological (oldest -> newest).
    results_chrono = list(reversed(results_desc))
    wins = _win_series(results_chrono)
    eval_wr_trend = _regression_slope(wins)
    recent_win_rates = _win_rate_buckets(wins, _BUCKET_SIZE)

    newest_ckpt = _newest_checkpoint_mtime(checkpoints_dir)
    rules_exists = reward_rules_path.is_file()
    rules_mtime = reward_rules_path.stat().st_mtime if rules_exists else None

    # checkpoint_age_seconds = newest checkpoint mtime - reward_rules mtime.
    # Negative => rewards edited AFTER the newest checkpoint (policy behind).
    reason_parts: list[str] = []
    if newest_ckpt is None and rules_mtime is None:
        # Never trained and no reward rules on disk: treat as deeply behind.
        checkpoint_age_seconds = -1
        reason_parts.append("no checkpoint and no reward_rules.json (never trained)")
    elif newest_ckpt is None:
        # No checkpoint but rules exist: policy is maximally behind the rules.
        checkpoint_age_seconds = -1
        reason_parts.append("no checkpoint found (never trained) but reward_rules.json exists")
    elif rules_mtime is None:
        # Trained but no reward rules to be behind: not reward-stale.
        checkpoint_age_seconds = 1
        reason_parts.append("checkpoint exists but no reward_rules.json to lag behind")
    else:
        checkpoint_age_seconds = int(newest_ckpt - rules_mtime)

    rewards_after_ckpt = checkpoint_age_seconds < 0
    trend_flat_or_falling = eval_wr_trend <= slope_threshold
    is_stale = trend_flat_or_falling and rewards_after_ckpt

    # Label only: clamp float-noise slopes (e.g. ~-1e-17 on a perfectly flat
    # series) to "flat" so the reason string doesn't read "falling". This does
    # NOT touch the is_stale arithmetic above.
    if abs(eval_wr_trend) < 1e-9:
        trend_word = "flat"
    elif eval_wr_trend < 0:
        trend_word = "falling"
    elif eval_wr_trend <= slope_threshold:
        trend_word = "flat"
    else:
        trend_word = "rising"
    reason_parts.append(
        f"trend {trend_word} (slope={eval_wr_trend:.4f} vs threshold "
        f"{slope_threshold:.4f}) over {len(wins)} games"
    )
    if rewards_after_ckpt:
        reason_parts.append(
            f"reward rules newer than checkpoint by "
            f"{abs(checkpoint_age_seconds)}s (age={checkpoint_age_seconds}s)"
        )
    else:
        reason_parts.append(f"checkpoint newer than reward rules (age={checkpoint_age_seconds}s)")
    verdict = "STALE" if is_stale else "not stale"
    reason = f"{verdict}: " + "; ".join(reason_parts)

    return StalenessReport(
        is_stale=is_stale,
        eval_wr_trend=eval_wr_trend,
        checkpoint_age_seconds=checkpoint_age_seconds,
        recent_win_rates=recent_win_rates,
        reason=reason,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: print a version's :class:`StalenessReport` as JSON to stdout."""
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.staleness",
        description="Print the staleness report for a bot version as JSON.",
    )
    parser.add_argument("version", help="Version name (e.g. v0)")
    parser.add_argument(
        "--window",
        type=int,
        default=30,
        help="Max most-recent games to analyse (default: 30).",
    )
    parser.add_argument(
        "--min-games",
        type=int,
        default=10,
        help="Minimum games required (default: 10).",
    )
    parser.add_argument(
        "--slope-threshold",
        type=float,
        default=0.01,
        help="Trend is flat-or-falling at or below this slope (default: 0.01).",
    )
    args = parser.parse_args(argv)

    try:
        report = compute_staleness(
            args.version,
            window=args.window,
            min_games=args.min_games,
            slope_threshold=args.slope_threshold,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(dataclasses.asdict(report), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
