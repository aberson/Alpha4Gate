"""CLI-level tests for ``scripts/evolve.py``.

Exercises the orchestration loop's control flow (budget, pool exhaustion,
commit-on-promote, state-file writes) with every heavy boundary mocked
out. The production ``orchestrator.evolve.run_round``, Claude invocation,
and git subprocess calls are all replaced with scripted fakes.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from orchestrator.contracts import SelfPlayRecord
from orchestrator.evolve import Improvement, RoundResult

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------


def _load_cli_module() -> ModuleType:
    """Import ``scripts/evolve.py`` as module ``evolve_cli``.

    Mirrors the pattern in ``tests/test_selfplay_cli.py`` — the script
    isn't part of a package so ``importlib.util`` is the idiomatic loader.
    """
    spec = importlib.util.spec_from_file_location(
        "evolve_cli", str(_REPO_ROOT / "scripts" / "evolve.py")
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_imp(
    title: str = "imp",
    type_: str = "training",
) -> Improvement:
    """Build a minimal Improvement for pool injection."""
    return Improvement(
        rank=1,
        title=title,
        type=cast(Any, type_),
        description=f"{title} description",
        principle_ids=[],
        expected_impact=f"{title} impact",
        concrete_change=json.dumps(
            {"file": "reward_rules.json", "patch": {"dummy": 1}}
        ),
    )


def _make_pool(n: int) -> list[Improvement]:
    """Build ``n`` distinct improvements."""
    return [_make_imp(title=f"imp-{i}") for i in range(n)]


def _rec(
    p1: str, p2: str, winner: str | None, match_id: str = "m"
) -> SelfPlayRecord:
    return SelfPlayRecord(
        match_id=match_id,
        p1_version=p1,
        p2_version=p2,
        winner=winner,
        map_name="Simple64",
        duration_s=10.0,
        seat_swap=False,
        timestamp="2026-04-19T00:00:00+00:00",
        error=None,
    )


def _make_round(
    *,
    imp_a: Improvement,
    imp_b: Improvement,
    promoted: bool,
    reason: str,
    parent: str = "v0",
    cand_a: str = "cand_x_a",
    cand_b: str = "cand_x_b",
    winner: str | None = None,
    ab_score: tuple[int, int] = (6, 4),
    gate_score: tuple[int, int] = (3, 2),
) -> RoundResult:
    """Build a RoundResult whose ab_record / gate_record match ab_score /
    gate_score so _count_record_winners reproduces them exactly."""
    ab_a, ab_b = ab_score
    ab_record = [_rec(cand_a, cand_b, cand_a) for _ in range(ab_a)] + [
        _rec(cand_a, cand_b, cand_b) for _ in range(ab_b)
    ]
    gate_record: list[SelfPlayRecord] | None
    if winner is not None:
        gc, gp = gate_score
        gate_record = [_rec(winner, parent, winner) for _ in range(gc)] + [
            _rec(winner, parent, parent) for _ in range(gp)
        ]
    else:
        gate_record = None
    return RoundResult(
        parent=parent,
        candidate_a=cand_a,
        candidate_b=cand_b,
        imp_a=imp_a,
        imp_b=imp_b,
        ab_record=ab_record,
        gate_record=gate_record,
        winner=winner,
        promoted=promoted,
        reason=reason,
    )


def _build_args(
    tmp_path: Path,
    *,
    hours: float = 0.0,
    pool_size: int = 4,
    ab_games: int = 4,
    gate_games: int = 3,
    no_commit: bool = True,
    seed: int = 42,
    map_name: str = "Simple64",
    run_log: Path | None = None,
    game_time_limit: int = 1800,
    hard_timeout: float = 2700.0,
) -> argparse.Namespace:
    """Construct an argparse.Namespace pointing at tmp_path for all state."""
    return argparse.Namespace(
        pool_size=pool_size,
        ab_games=ab_games,
        gate_games=gate_games,
        hours=hours,
        map=map_name,
        game_time_limit=game_time_limit,
        hard_timeout=hard_timeout,
        no_commit=no_commit,
        results_path=tmp_path / "evolve_results.jsonl",
        pool_path=tmp_path / "evolve_pool.json",
        state_path=tmp_path / "evolve_run_state.json",
        current_round_path=tmp_path / "evolve_current_round.json",
        crash_log_path=tmp_path / "evolve_crashes.jsonl",
        run_log=run_log if run_log is not None else tmp_path / "run.md",
        seed=seed,
        return_loser=False,
        resume=False,
        post_training_cycles=0,
        backend_url="http://localhost:8765",
    )


class _ScriptedRoundRunner:
    """Stateful mock for ``run_round_fn``. Pops results off the queue."""

    def __init__(self, results: list[RoundResult]) -> None:
        self._queue = list(results)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        parent: str,
        imp_a: Improvement,
        imp_b: Improvement,
        **kwargs: Any,
    ) -> RoundResult:
        self.calls.append(
            {
                "parent": parent,
                "imp_a": imp_a,
                "imp_b": imp_b,
                "kwargs": kwargs,
            }
        )
        if not self._queue:
            raise AssertionError(
                "ScriptedRoundRunner: no more scripted results; "
                f"call #{len(self.calls)} has nothing to return"
            )
        out = self._queue.pop(0)
        # Rewrite imp_a / imp_b on the result so assertions match the
        # actual improvements sampled — tests usually only care about
        # outcome/reason, not which imp landed in which slot.
        return RoundResult(
            parent=out.parent,
            candidate_a=out.candidate_a,
            candidate_b=out.candidate_b,
            imp_a=imp_a,
            imp_b=imp_b,
            ab_record=out.ab_record,
            gate_record=out.gate_record,
            winner=out.winner,
            promoted=out.promoted,
            reason=out.reason,
        )


@pytest.fixture
def cli() -> ModuleType:
    """Load the CLI module once per test."""
    return _load_cli_module()


# ---------------------------------------------------------------------------
# 1. argparse smoke
# ---------------------------------------------------------------------------


def test_help_exits_zero(cli: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    """``--help`` prints usage and exits 0."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "usage" in out.lower()
    assert "--pool-size" in out
    assert "--ab-games" in out
    assert "--gate-games" in out
    assert "--hours" in out


def test_default_flags(cli: ModuleType) -> None:
    """Pin the documented defaults."""
    args = cli.build_parser().parse_args([])
    assert args.pool_size == 10
    assert args.ab_games == 10
    assert args.gate_games == 5
    assert args.hours == 4.0
    assert args.map == "Simple64"
    assert args.no_commit is False
    assert args.return_loser is False
    assert args.resume is False
    assert args.current_round_path.name == "evolve_current_round.json"
    assert args.post_training_cycles == 0
    assert args.backend_url == "http://localhost:8765"


# ---------------------------------------------------------------------------
# 2. --return-loser raises
# ---------------------------------------------------------------------------


def test_return_loser_raises_not_implemented(
    cli: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--return-loser`` should NotImplementedError (with stderr note)."""
    with pytest.raises(NotImplementedError, match="return-loser"):
        cli.main(["--return-loser"])
    err = capsys.readouterr().err
    assert "return-loser" in err.lower()


# ---------------------------------------------------------------------------
# 3. Wall-clock early stop
# ---------------------------------------------------------------------------


def test_wall_clock_early_stop(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After round 1 the budget is gone; loop exits with one round completed."""
    pool = _make_pool(10)

    # time_fn: first call captures start_monotonic=0. The first budget
    # check (top of the loop) returns 0 (in-budget). After round 1 the
    # counter jumps past the budget so the NEXT budget check exits.
    # Use a generous trailing-value list so any late calls still see
    # "past budget".
    call_count = {"n": 0}

    def fake_time() -> float:
        n = call_count["n"]
        call_count["n"] += 1
        if n <= 1:
            # 0: start_monotonic; 1: first _budget_exceeded check (pre round 1)
            return 0.0
        # All subsequent calls: past the 1h budget.
        return 7200.0

    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=False,
                reason="discarded: A/B was 2-2 tie; both improvements consumed",
            ),
        ]
    )

    args = _build_args(tmp_path, hours=1.0, pool_size=10)

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda parent, **_: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
        time_fn=fake_time,
    )
    assert rc == 0
    assert len(round_runner.calls) == 1

    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["rounds_completed"] == 1


# ---------------------------------------------------------------------------
# 4. Pool exhaustion stop
# ---------------------------------------------------------------------------


def test_pool_exhaustion_stop(
    cli: ModuleType, tmp_path: Path
) -> None:
    """With pool_size=4, after 2 rounds the pool has <2 active; loop exits."""
    pool = _make_pool(4)
    # 2 discards -> 4 improvements consumed -> 0 remaining.
    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=False,
                reason="discarded: A/B was 2-2 tie; both improvements consumed",
            ),
            _make_round(
                imp_a=pool[2],
                imp_b=pool[3],
                promoted=False,
                reason="discarded: A/B was 2-2 tie; both improvements consumed",
            ),
        ]
    )

    args = _build_args(tmp_path, hours=0.0, pool_size=4)

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda parent, **_: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    assert len(round_runner.calls) == 2
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["rounds_completed"] == 2
    assert state["pool_remaining_count"] == 0


# ---------------------------------------------------------------------------
# 5. Discards keep running until pool exhaustion (no no-progress stop)
# ---------------------------------------------------------------------------


def test_consecutive_discards_do_not_stop_the_run(
    cli: ModuleType, tmp_path: Path
) -> None:
    """The loop stops only on wall-clock or pool exhaustion — consecutive
    discards (or crashes) never short-circuit a run.

    Regression test for a rule that was removed after it silently truncated
    a run at 3 rounds when most of the pool was still active. The user's
    intent is "exhaust the pool" — 10 items / 2 per round = 5 rounds max.
    """
    pool = _make_pool(10)
    # 5 scripted discards — one per possible round at pool_size=10.
    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=False,
                reason="discarded: A/B was 2-2 tie; both improvements consumed",
            )
            for _ in range(5)
        ]
    )

    args = _build_args(tmp_path, hours=0.0, pool_size=10)

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda parent, **_: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    # All 5 rounds ran; no_progress_streak is still tracked but no longer
    # a stop condition.
    assert state["rounds_completed"] == 5
    assert state["no_progress_streak"] == 5
    assert state["pool_remaining_count"] == 0
    # Every scripted round was consumed — no early return.
    assert len(round_runner.calls) == 5


# ---------------------------------------------------------------------------
# 6. State file writes (happy path)
# ---------------------------------------------------------------------------


def test_state_files_written_after_one_round(
    cli: ModuleType, tmp_path: Path
) -> None:
    """After 1 round, all three state files exist with the expected shape."""
    pool = _make_pool(4)
    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=False,
                reason="discarded: A/B was 2-2 tie; both improvements consumed",
            ),
            _make_round(
                imp_a=pool[2],
                imp_b=pool[3],
                promoted=False,
                reason="discarded: A/B was 2-2 tie; both improvements consumed",
            ),
        ]
    )

    args = _build_args(tmp_path, hours=0.0, pool_size=4)

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda parent, **_: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    # evolve_pool.json
    pool_payload = json.loads(args.pool_path.read_text(encoding="utf-8"))
    assert pool_payload["parent"] == "v0"
    assert "generated_at" in pool_payload
    assert len(pool_payload["pool"]) == 4
    assert {item["status"] for item in pool_payload["pool"]} <= {
        "active",
        "consumed-won",
        "consumed-lost",
        "consumed-tie",
    }
    # Every item has the Improvement schema + status.
    required = {
        "rank",
        "title",
        "type",
        "description",
        "principle_ids",
        "expected_impact",
        "concrete_change",
        "status",
    }
    for item in pool_payload["pool"]:
        assert required.issubset(item.keys())

    # evolve_run_state.json
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    for key in (
        "status",
        "parent_start",
        "parent_current",
        "started_at",
        "wall_budget_hours",
        "rounds_completed",
        "rounds_promoted",
        "no_progress_streak",
        "pool_remaining_count",
        "last_result",
    ):
        assert key in state, f"run-state missing {key!r}"
    assert state["status"] == "completed"
    assert state["rounds_completed"] == 2
    assert state["rounds_promoted"] == 0

    # evolve_results.jsonl — one line per round.
    lines = args.results_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)
        for key in (
            "parent",
            "candidate_a",
            "candidate_b",
            "imp_a",
            "imp_b",
            "ab_record",
            "gate_record",
            "winner",
            "promoted",
            "reason",
        ):
            assert key in parsed


# ---------------------------------------------------------------------------
# 7. Promote triggers commit
# ---------------------------------------------------------------------------


def test_promote_triggers_commit(cli: ModuleType, tmp_path: Path) -> None:
    """Promote -> commit called with EVO_AUTO=1 and [evo-auto] in message."""
    pool = _make_pool(4)
    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=True,
                reason=(
                    "promoted: cand_x_a beat cand_x_b 6-4, "
                    "then beat parent v0 3-2"
                ),
                winner="cand_x_a",
            ),
        ]
    )

    commit_calls: list[dict[str, Any]] = []

    def fake_commit(
        new_version: str, round_index: int, imp_title: str
    ) -> bool:
        commit_calls.append(
            {
                "new_version": new_version,
                "round_index": round_index,
                "imp_title": imp_title,
            }
        )
        return True

    # current_version_fn should flip after promote to reflect the new
    # pointer — first call = parent (at startup), subsequent calls =
    # new version (after run_round swapped the pointer).
    cv_seq = iter(["v0", "cand_x_a", "cand_x_a"])

    args = _build_args(tmp_path, hours=0.0, pool_size=4, no_commit=False)

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda parent, **_: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: next(cv_seq),
        commit_fn=fake_commit,
    )
    assert rc == 0
    assert len(commit_calls) == 1
    assert commit_calls[0]["new_version"] == "cand_x_a"
    assert commit_calls[0]["round_index"] == 1


def test_commit_helper_passes_evo_auto_env_and_marker(
    cli: ModuleType,
) -> None:
    """``git_commit_evo_auto`` runs git with EVO_AUTO=1 + [evo-auto] msg."""
    captured: list[dict[str, Any]] = []

    def fake_run(
        cmd: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        captured.append({"cmd": cmd, "kwargs": kwargs})
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    ok = cli.git_commit_evo_auto(
        "cand_abc_a", 5, "Reduce probe cap", run=fake_run
    )
    assert ok is True
    assert len(captured) == 2  # git add + git commit
    # git add call
    assert captured[0]["cmd"][0:2] == ["git", "add"]
    assert "bots/cand_abc_a/" in captured[0]["cmd"]
    # git commit call
    assert captured[1]["cmd"][0:3] == ["git", "commit", "-m"]
    msg = captured[1]["cmd"][3]
    assert "[evo-auto]" in msg
    assert "round 5" in msg
    assert "Reduce probe cap" in msg
    # EVO_AUTO=1 in env for both calls.
    for call in captured:
        env = call["kwargs"]["env"]
        assert env.get("EVO_AUTO") == "1"
        # ADVISED_AUTO explicitly scrubbed to avoid hook conflict.
        assert "ADVISED_AUTO" not in env


# ---------------------------------------------------------------------------
# 8. --no-commit skips commit
# ---------------------------------------------------------------------------


def test_no_commit_skips_commit(cli: ModuleType, tmp_path: Path) -> None:
    """With --no-commit, a promote does NOT invoke the commit hook."""
    pool = _make_pool(4)
    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=True,
                reason="promoted: cand_x_a beat cand_x_b 6-4, then beat parent v0 3-2",
                winner="cand_x_a",
            ),
        ]
    )

    commit_calls: list[Any] = []

    def fake_commit(*args: Any, **kwargs: Any) -> bool:
        commit_calls.append((args, kwargs))
        return True

    cv_seq = iter(["v0", "cand_x_a", "cand_x_a"])
    args = _build_args(tmp_path, hours=0.0, pool_size=4, no_commit=True)

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda parent, **_: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: next(cv_seq),
        commit_fn=fake_commit,
    )
    assert rc == 0
    assert commit_calls == []


# ---------------------------------------------------------------------------
# 9. Commit failure logs WARNING, loop continues
# ---------------------------------------------------------------------------


def test_commit_failure_logs_warning_loop_continues(
    cli: ModuleType, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failing commit_fn logs WARNING but doesn't abort the loop."""
    pool = _make_pool(4)
    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=True,
                reason="promoted: cand_x_a beat cand_x_b 6-4, then beat parent v0 3-2",
                winner="cand_x_a",
            ),
            _make_round(
                imp_a=pool[2],
                imp_b=pool[3],
                promoted=False,
                reason="discarded: A/B was 2-2 tie; both improvements consumed",
            ),
        ]
    )

    def failing_commit(*args: Any, **kwargs: Any) -> bool:
        return False

    cv_seq = iter(["v0", "cand_x_a", "cand_x_a", "cand_x_a"])
    args = _build_args(tmp_path, hours=0.0, pool_size=4, no_commit=False)

    with caplog.at_level(logging.WARNING, logger="evolve"):
        rc = cli.run_loop(
            args,
            generate_pool_fn=lambda parent, **_: pool,
            run_round_fn=round_runner,
            current_version_fn=lambda: next(cv_seq),
            commit_fn=failing_commit,
        )
    assert rc == 0
    # Both rounds completed despite commit failure on round 1.
    assert len(round_runner.calls) == 2
    # WARNING was emitted about the commit failure.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("commit failed" in r.getMessage().lower() for r in warnings)


# ---------------------------------------------------------------------------
# 10. Pool status updates correctly
# ---------------------------------------------------------------------------


def test_pool_status_updates_after_promote_and_tie(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Promote -> winner=consumed-won, loser=consumed-lost. Tie -> both tie."""
    pool = _make_pool(4)
    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=True,
                reason="promoted: cand_x_a beat cand_x_b 6-4, then beat parent v0 3-2",
                winner="cand_x_a",
            ),
            _make_round(
                imp_a=pool[2],
                imp_b=pool[3],
                promoted=False,
                reason="discarded: A/B was 2-2 tie; both improvements consumed",
            ),
        ]
    )

    cv_seq = iter(["v0", "cand_x_a", "cand_x_a"])
    args = _build_args(tmp_path, hours=0.0, pool_size=4, no_commit=True)

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda parent, **_: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: next(cv_seq),
    )
    assert rc == 0
    payload = json.loads(args.pool_path.read_text(encoding="utf-8"))
    # Map pool item by title (which is unique in this test) -> status.
    by_title = {item["title"]: item["status"] for item in payload["pool"]}

    # Exactly one 'consumed-won' and one 'consumed-lost' (the promote).
    wons = [t for t, s in by_title.items() if s == "consumed-won"]
    losts = [t for t, s in by_title.items() if s == "consumed-lost"]
    ties = [t for t, s in by_title.items() if s == "consumed-tie"]
    assert len(wons) == 1
    assert len(losts) == 1
    assert len(ties) == 2


def test_gate_failure_marks_both_lost(
    cli: ModuleType, tmp_path: Path
) -> None:
    """A gate-failure discard marks both improvements consumed-lost."""
    pool = _make_pool(2)
    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=False,
                reason=(
                    "discarded: cand_x_a beat cand_x_b 6-4, "
                    "lost to parent v0 1-4"
                ),
                winner=None,
            ),
        ]
    )

    args = _build_args(tmp_path, hours=0.0, pool_size=2, no_commit=True)

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda parent, **_: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    payload = json.loads(args.pool_path.read_text(encoding="utf-8"))
    statuses = {item["title"]: item["status"] for item in payload["pool"]}
    assert all(v == "consumed-lost" for v in statuses.values())


# ---------------------------------------------------------------------------
# 11. Run-log written on completion
# ---------------------------------------------------------------------------


def test_run_log_written_on_completion(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Explicit --run-log produces a markdown file with the expected shape."""
    pool = _make_pool(2)
    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=False,
                reason="discarded: A/B was 2-2 tie; both improvements consumed",
            ),
        ]
    )

    run_log_path = tmp_path / "run_log.md"
    args = _build_args(
        tmp_path, hours=0.0, pool_size=2, run_log=run_log_path
    )

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda parent, **_: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    assert run_log_path.is_file()
    text = run_log_path.read_text(encoding="utf-8")
    assert text.startswith("# Evolve run")
    assert "Rounds completed: 1" in text
    assert "Stop reason:" in text
    # Markdown round table header.
    assert "| # | candidate A | candidate B |" in text


# ---------------------------------------------------------------------------
# 12. --hours 0 means unlimited (done-when check)
# ---------------------------------------------------------------------------


def test_hours_zero_is_unlimited_and_runs_to_pool_exhaustion(
    cli: ModuleType, tmp_path: Path
) -> None:
    """``--hours 0 --pool-size 2`` runs end-to-end and writes state files.

    Pinned by the plan's Done-When check. With a 2-imp pool and one round,
    pool exhaustion fires after round 1. Budget check is disabled (hours=0).
    """
    pool = _make_pool(2)
    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=False,
                reason="discarded: A/B was 2-2 tie; both improvements consumed",
            ),
        ]
    )

    args = _build_args(tmp_path, hours=0.0, pool_size=2, no_commit=True)

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda parent, **_: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    assert args.pool_path.is_file()
    assert args.state_path.is_file()
    assert args.results_path.is_file()
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["status"] == "completed"


# ---------------------------------------------------------------------------
# Additional: pool-generation failure -> exit 1
# ---------------------------------------------------------------------------


def test_pool_generation_failure_returns_1(
    cli: ModuleType, tmp_path: Path
) -> None:
    """A raising generate_pool_fn -> exit 1 with status=failed written."""

    def failing_pool_gen(*args: Any, **kwargs: Any) -> list[Improvement]:
        raise ValueError("Claude returned garbage JSON")

    args = _build_args(tmp_path, hours=0.0, pool_size=4)
    rc = cli.run_loop(
        args,
        generate_pool_fn=failing_pool_gen,
        run_round_fn=lambda *a, **k: None,
        current_version_fn=lambda: "v0",
    )
    assert rc == 1
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["status"] == "failed"


# ---------------------------------------------------------------------------
# Additional: SC2 not installed -> exit 1 (pre-flight)
# ---------------------------------------------------------------------------


def test_post_training_fires_on_promotion_when_flag_set(
    cli: ModuleType, tmp_path: Path
) -> None:
    """If a round promotes AND --post-training-cycles > 0, the CLI calls
    the injected post_training_fn with the new parent version."""
    pool = _make_pool(2)
    args = _build_args(tmp_path, hours=0.0, pool_size=2)
    args.post_training_cycles = 3

    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=True,
                reason="promoted: winner beat parent 3-0",
                cand_a="cand_promo_a",
                cand_b="cand_promo_b",
                winner="cand_promo_a",
            )
        ]
    )

    calls: list[dict[str, Any]] = []

    def fake_post_training(**kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {"ok": True}

    # Simulate the post-promote pointer flip: the first
    # current_version_fn() call (at pre-flight) returns v0; every call
    # after the round's promote branch returns the winner. Production's
    # run_round updates bots/current/current.txt; the mocked run_round
    # does not, so we fake it via call count.
    version_call_count = {"n": 0}

    def version_fn() -> str:
        version_call_count["n"] += 1
        return "v0" if version_call_count["n"] <= 1 else "cand_promo_a"

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda *a, **k: pool,
        run_round_fn=round_runner,
        current_version_fn=version_fn,
        post_training_fn=fake_post_training,
    )
    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["cycles"] == 3
    assert calls[0]["new_parent"] == "cand_promo_a"
    assert calls[0]["backend_url"] == "http://localhost:8765"


def test_post_training_does_not_fire_on_no_promotion(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Run ends without a promotion → post-training NOT invoked even if
    --post-training-cycles is set (no point training on the same baseline)."""
    pool = _make_pool(2)
    args = _build_args(tmp_path, hours=0.0, pool_size=2)
    args.post_training_cycles = 3

    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=False,
                reason="discarded: A/B 0-0 tie",
                ab_score=(0, 0),
            )
        ]
    )

    calls: list[dict[str, Any]] = []

    def fake_post_training(**kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {"ok": True}

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda *a, **k: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
        post_training_fn=fake_post_training,
    )
    assert rc == 0
    assert calls == []


def test_post_training_does_not_fire_when_flag_zero(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Default --post-training-cycles=0 means never start the daemon,
    even on promotion."""
    pool = _make_pool(2)
    args = _build_args(tmp_path, hours=0.0, pool_size=2)
    assert args.post_training_cycles == 0

    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=True,
                reason="promoted",
                cand_a="cand_x_a",
                cand_b="cand_x_b",
                winner="cand_x_a",
            )
        ]
    )

    calls: list[dict[str, Any]] = []

    def fake_post_training(**kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {"ok": True}

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda *a, **k: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: ("cand_x_a" if calls else "v0"),
        post_training_fn=fake_post_training,
    )
    assert rc == 0
    assert calls == []


def test_start_post_training_daemon_swallows_backend_errors(
    cli: ModuleType, tmp_path: Path
) -> None:
    """A backend-down / unreachable error must NOT bubble up — the
    evolve run's promotion is already on disk; we just log a warning."""
    # httpx isn't available under some minimal configs; grab whatever
    # the CLI module imported lazily inside the function.
    import httpx

    def boom(*a: Any, **k: Any) -> Any:
        raise httpx.ConnectError("backend down")

    # Patch both httpx methods to raise.
    orig_put = httpx.put
    orig_post = httpx.post
    httpx.put = boom  # type: ignore[assignment]
    httpx.post = boom  # type: ignore[assignment]
    try:
        result = cli.start_post_training_daemon(
            cycles=3,
            backend_url="http://localhost:9999",
            new_parent="cand_xyz",
        )
    finally:
        httpx.put = orig_put  # type: ignore[assignment]
        httpx.post = orig_post  # type: ignore[assignment]

    assert result["error"] is not None
    assert "ConnectError" in result["error"]


def test_sc2_not_installed_returns_1(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing SC2 install trips the pre-flight guard."""
    # Point SC2PATH at a path that definitely doesn't exist.
    monkeypatch.setenv("SC2PATH", str(tmp_path / "does_not_exist_sc2"))
    args = _build_args(tmp_path, hours=0.0, pool_size=2)
    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda *a, **k: _make_pool(2),
        run_round_fn=lambda *a, **k: None,
        current_version_fn=lambda: "v0",
    )
    assert rc == 1


# ---------------------------------------------------------------------------
# 4. --resume — load existing pool instead of regenerating
# ---------------------------------------------------------------------------


def _seed_pool_file(
    cli: ModuleType,
    pool_path: Path,
    *,
    parent: str,
    pool: list[Improvement],
    statuses: dict[int, str] | None = None,
) -> None:
    """Shortcut for writing a pool file via the CLI's write helper."""
    cli.write_pool_state(
        pool_path,
        pool,
        parent=parent,
        statuses=statuses or {},
        generated_at="2026-04-20T18:48:41+00:00",
    )


def test_resume_loads_existing_pool_and_skips_generation(
    cli: ModuleType, tmp_path: Path
) -> None:
    """With --resume and an existing pool file, generate_pool_fn is NOT called
    and statuses (consumed-*) on disk are honoured."""
    pool = _make_pool(4)
    args = _build_args(tmp_path, hours=0.0, pool_size=4)
    args.resume = True
    # Two consumed-tie items mirror the real-world state we saw on disk
    # after the 11:48 run crashed mid-round 1.
    _seed_pool_file(
        cli,
        args.pool_path,
        parent="v0",
        pool=pool,
        statuses={0: "consumed-tie", 1: "consumed-tie"},
    )

    gen_calls: list[Any] = []

    def no_pool_gen(*a: Any, **k: Any) -> list[Improvement]:
        gen_calls.append((a, k))
        raise AssertionError("generate_pool_fn must not be called on --resume")

    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[2],
                imp_b=pool[3],
                promoted=True,
                reason="promoted: cand_x_a won",
                cand_a="cand_resume_a",
                cand_b="cand_resume_b",
                winner="cand_resume_a",
            )
        ]
    )

    rc = cli.run_loop(
        args,
        generate_pool_fn=no_pool_gen,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    assert gen_calls == []

    # The round runner was called exactly once with imps drawn only from
    # the remaining active slots (indexes 2 and 3).
    assert len(round_runner.calls) == 1
    sampled_titles = {
        round_runner.calls[0]["imp_a"].title,
        round_runner.calls[0]["imp_b"].title,
    }
    assert sampled_titles == {"imp-2", "imp-3"}


def test_resume_parent_mismatch_returns_1(
    cli: ModuleType, tmp_path: Path
) -> None:
    """A pool file whose parent doesn't match current_version() is refused."""
    pool = _make_pool(2)
    args = _build_args(tmp_path, hours=0.0, pool_size=2)
    args.resume = True
    _seed_pool_file(cli, args.pool_path, parent="v7", pool=pool)

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda *a, **k: _fail("must not generate"),
        run_round_fn=lambda *a, **k: _fail("must not run"),
        current_version_fn=lambda: "v0",
    )
    assert rc == 1
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["status"] == "failed"


def test_resume_missing_file_falls_through_to_generation(
    cli: ModuleType, tmp_path: Path
) -> None:
    """--resume with no pool file on disk behaves like a fresh run."""
    pool = _make_pool(2)
    args = _build_args(tmp_path, hours=0.0, pool_size=2)
    args.resume = True
    assert not args.pool_path.exists()

    gen_calls: list[tuple[Any, Any]] = []

    def gen(*a: Any, **k: Any) -> list[Improvement]:
        gen_calls.append((a, k))
        return pool

    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=False,
                reason="discarded: A/B 0-0 all-crash",
                ab_score=(0, 0),
            )
        ]
    )

    rc = cli.run_loop(
        args,
        generate_pool_fn=gen,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    assert len(gen_calls) == 1


def _fail(msg: str) -> Any:
    raise AssertionError(msg)


# ---------------------------------------------------------------------------
# 5. Current-round live state — written by on_round_event callback
# ---------------------------------------------------------------------------


def test_current_round_written_on_events_and_cleared_between_rounds(
    cli: ModuleType, tmp_path: Path
) -> None:
    """The CLI builds an on_round_event callback that updates
    evolve_current_round.json on ab_start / ab_game_end / gate_start /
    gate_game_end, and clears the file at round end."""
    pool = _make_pool(2)
    args = _build_args(tmp_path, hours=0.0, pool_size=2)
    captured_events: list[dict[str, Any]] = []

    def round_runner(
        parent: str,
        imp_a: Improvement,
        imp_b: Improvement,
        **kwargs: Any,
    ) -> RoundResult:
        on_event = kwargs.get("on_round_event")
        assert on_event is not None, "CLI must wire on_round_event"
        # Simulate a full round's worth of progress events, asserting the
        # file updates visibly between them so the dashboard UI would see
        # the same snapshots.
        on_event({"type": "ab_start", "cand_a": "x_a", "cand_b": "x_b", "total": 4})
        after_ab_start = json.loads(
            args.current_round_path.read_text(encoding="utf-8")
        )
        captured_events.append(after_ab_start)

        on_event({"type": "ab_game_end", "wins_a": 1, "wins_b": 0})
        after_g1 = json.loads(
            args.current_round_path.read_text(encoding="utf-8")
        )
        captured_events.append(after_g1)

        on_event({"type": "gate_start", "candidate": "x_a", "parent": "v0", "total": 3})
        after_gate_start = json.loads(
            args.current_round_path.read_text(encoding="utf-8")
        )
        captured_events.append(after_gate_start)

        on_event({"type": "gate_game_end", "wins_cand": 1, "wins_parent": 0})
        after_gg1 = json.loads(
            args.current_round_path.read_text(encoding="utf-8")
        )
        captured_events.append(after_gg1)

        return _make_round(
            imp_a=imp_a,
            imp_b=imp_b,
            promoted=False,
            reason="discarded: A/B 2-2 tie",
        )

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda *a, **k: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    # Four in-flight snapshots + one cleared snapshot after round-end.
    ab_start_snap, g1_snap, gate_start_snap, gg1_snap = captured_events
    assert ab_start_snap["active"] is True
    assert ab_start_snap["phase"] == "ab"
    assert ab_start_snap["cand_a"] == "x_a"
    assert ab_start_snap["games_total"] == 4
    assert ab_start_snap["games_played"] == 0

    assert g1_snap["games_played"] == 1
    assert g1_snap["score_a"] == 1 and g1_snap["score_b"] == 0

    assert gate_start_snap["phase"] == "gate"
    assert gate_start_snap["gate_candidate"] == "x_a"
    assert gate_start_snap["games_total"] == 3
    assert gate_start_snap["games_played"] == 0
    assert gate_start_snap["score_a"] == 0 and gate_start_snap["score_b"] == 0

    assert gg1_snap["games_played"] == 1
    assert gg1_snap["score_a"] == 1 and gg1_snap["score_b"] == 0

    # After the round completes the CLI should have cleared the file.
    final = json.loads(args.current_round_path.read_text(encoding="utf-8"))
    assert final["active"] is False


def test_fresh_run_clears_stale_state_files(
    cli: ModuleType, tmp_path: Path
) -> None:
    """A fresh (non-resume) run wipes the prior run's evolve_results.jsonl,
    overwrites evolve_pool.json with an empty placeholder, and clears
    evolve_current_round.json BEFORE pool generation begins — so the
    dashboard doesn't flash stale data while seeding is in flight."""
    args = _build_args(tmp_path, hours=0.0, pool_size=2)
    # Seed stale files that look like leftovers from a previous run.
    args.results_path.write_text(
        json.dumps({"round_index": 99, "winner": "old-v5", "promoted": True})
        + "\n",
        encoding="utf-8",
    )
    old_pool = {
        "parent": "v0",
        "generated_at": "2026-04-18T00:00:00+00:00",
        "pool": [
            {
                "rank": 1,
                "title": "stale improvement",
                "type": "training",
                "description": "old",
                "principle_ids": [],
                "expected_impact": "",
                "concrete_change": "{}",
                "status": "consumed-lost",
            }
        ],
    }
    args.pool_path.write_text(json.dumps(old_pool), encoding="utf-8")
    args.current_round_path.write_text(
        json.dumps({"active": True, "phase": "ab", "round_index": 99}),
        encoding="utf-8",
    )

    pool = _make_pool(2)
    captured: dict[str, Any] = {}

    def gen(*a: Any, **k: Any) -> list[Improvement]:
        captured["pool_file_at_gen"] = json.loads(
            args.pool_path.read_text(encoding="utf-8")
        )
        captured["results_file_at_gen"] = args.results_path.read_text(
            encoding="utf-8"
        )
        captured["current_round_at_gen"] = json.loads(
            args.current_round_path.read_text(encoding="utf-8")
        )
        return pool

    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=False,
                reason="discarded: 0-0 tie",
                ab_score=(0, 0),
            )
        ]
    )

    rc = cli.run_loop(
        args,
        generate_pool_fn=gen,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    # At the moment generate_pool_fn was called, the stale files should have
    # been wiped (not the seeded "mirror_games" state the CLI writes right
    # after _clear_fresh_run_state — that happens just before generate_pool,
    # so current_round may already show phase=mirror_games here).
    assert captured["pool_file_at_gen"]["pool"] == []
    assert captured["pool_file_at_gen"]["parent"] == "v0"
    assert captured["results_file_at_gen"] == ""
    current_snap = captured["current_round_at_gen"]
    # Either the clear happened (active=False) OR the mirror-seeding write
    # just overwrote it with a FRESH round_index (0), imp_a_title referring
    # to mirror games — never the stale round_index=99 ab snapshot.
    assert current_snap.get("round_index") != 99


def test_resume_does_not_clear_stale_state(
    cli: ModuleType, tmp_path: Path
) -> None:
    """--resume must NOT wipe the pool file — that would defeat the point."""
    pool = _make_pool(4)
    args = _build_args(tmp_path, hours=0.0, pool_size=4)
    args.resume = True
    _seed_pool_file(
        cli,
        args.pool_path,
        parent="v0",
        pool=pool,
        statuses={0: "consumed-tie", 1: "consumed-tie"},
    )

    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[2],
                imp_b=pool[3],
                promoted=False,
                reason="discarded",
            )
        ]
    )

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda *a, **k: _fail("must not generate"),
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    # The pool file gets rewritten at the end of the round with updated
    # statuses, but every item from the original must still be present.
    final_pool = json.loads(args.pool_path.read_text(encoding="utf-8"))
    assert len(final_pool["pool"]) == 4  # not an empty placeholder
    # The original consumed-tie statuses survive the rewrite — the round
    # only touched indexes 2 and 3.
    assert final_pool["pool"][0]["status"] == "consumed-tie"
    assert final_pool["pool"][1]["status"] == "consumed-tie"


def test_pool_gen_events_written_to_current_round_file(
    cli: ModuleType, tmp_path: Path
) -> None:
    """The CLI wires on_pool_gen_event into generate_pool_fn; mirror_start /
    mirror_game_end / claude_start events each update evolve_current_round.json
    with the right phase + progress."""
    args = _build_args(tmp_path, hours=0.0, pool_size=2)
    captured_during_gen: list[dict[str, Any]] = []

    def gen(
        parent: str, *, on_pool_gen_event: Any = None, **_: Any
    ) -> list[Improvement]:
        assert on_pool_gen_event is not None, (
            "CLI must pass on_pool_gen_event into generate_pool"
        )
        on_pool_gen_event(
            {"type": "mirror_start", "total": 3, "parent": parent}
        )
        captured_during_gen.append(
            json.loads(args.current_round_path.read_text(encoding="utf-8"))
        )
        on_pool_gen_event(
            {"type": "mirror_game_end", "games_played": 1, "total": 3}
        )
        captured_during_gen.append(
            json.loads(args.current_round_path.read_text(encoding="utf-8"))
        )
        on_pool_gen_event(
            {"type": "mirror_game_end", "games_played": 2, "total": 3}
        )
        on_pool_gen_event(
            {"type": "mirror_game_end", "games_played": 3, "total": 3}
        )
        on_pool_gen_event({"type": "claude_start", "pool_size": 2})
        captured_during_gen.append(
            json.loads(args.current_round_path.read_text(encoding="utf-8"))
        )
        return _make_pool(2)

    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=_make_imp(title="a"),
                imp_b=_make_imp(title="b"),
                promoted=False,
                reason="discarded",
                ab_score=(0, 0),
            )
        ]
    )

    rc = cli.run_loop(
        args,
        generate_pool_fn=gen,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    mirror_start_snap, mirror_g1_snap, claude_start_snap = captured_during_gen
    assert mirror_start_snap["phase"] == "mirror_games"
    assert mirror_start_snap["games_total"] == 3
    assert mirror_start_snap["games_played"] == 0
    assert mirror_start_snap["cand_a"] == "v0"
    assert mirror_start_snap["cand_b"] == "v0"

    assert mirror_g1_snap["phase"] == "mirror_games"
    assert mirror_g1_snap["games_played"] == 1

    assert claude_start_snap["phase"] == "claude_prompt"


def test_crashed_round_appends_results_jsonl_and_crash_log(
    cli: ModuleType, tmp_path: Path
) -> None:
    """A round that raises must produce (a) a row in evolve_results.jsonl
    with outcome='discarded-crash' + error field and (b) a full-traceback
    entry in evolve_crashes.jsonl so operators can diagnose without
    needing stderr scrollback."""
    pool = _make_pool(2)
    args = _build_args(tmp_path, hours=0.0, pool_size=2)

    def round_runner(*a: Any, **k: Any) -> RoundResult:
        raise RuntimeError("dev sub-agent timed out after 900s")

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda *a, **k: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    # With a 2-item pool and 1 crash, both items get consumed-tie ->
    # pool-exhausted -> normal exit.
    assert rc == 0

    # evolve_results.jsonl should have one crash entry with:
    #  - promoted=false, winner=null, ab_record=[], gate_record=null
    #  - error field (truncated traceback)
    #  - reason beginning with "crashed:"
    results_lines = args.results_path.read_text(encoding="utf-8").splitlines()
    assert len(results_lines) == 1, (
        f"expected 1 crash entry, got {len(results_lines)}"
    )
    crash_entry = json.loads(results_lines[0])
    assert crash_entry["promoted"] is False
    assert crash_entry["winner"] is None
    assert crash_entry["ab_record"] == []
    assert crash_entry["gate_record"] is None
    assert crash_entry["reason"].startswith("crashed:")
    assert "RuntimeError" in crash_entry["reason"]
    assert "dev sub-agent timed out" in crash_entry["reason"]
    assert "error" in crash_entry
    assert crash_entry["error"]  # non-empty

    # evolve_crashes.jsonl gets the full diagnostic payload.
    crash_log_lines = args.crash_log_path.read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(crash_log_lines) == 1
    crash_log = json.loads(crash_log_lines[0])
    assert crash_log["round_index"] == 1
    assert crash_log["error_type"] == "RuntimeError"
    assert "dev sub-agent timed out" in crash_log["error_message"]
    assert "Traceback" in crash_log["traceback"]
    assert crash_log["imp_a_title"] in {"imp-0", "imp-1"}
    assert crash_log["imp_b_title"] in {"imp-0", "imp-1"}


def test_crashed_round_appears_in_run_log_markdown(
    cli: ModuleType, tmp_path: Path
) -> None:
    """The run-log markdown table should include crashed rounds with
    outcome='discarded-crash' so the operator's post-run forensics aren't
    missing the rounds that never produced games."""
    pool = _make_pool(2)
    args = _build_args(tmp_path, hours=0.0, pool_size=2)

    def round_runner(*a: Any, **k: Any) -> RoundResult:
        raise ValueError("candidate snapshot failed: disk full")

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda *a, **k: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    run_log_text = args.run_log.read_text(encoding="utf-8")
    assert "discarded-crash" in run_log_text
    assert "ValueError" in run_log_text
    assert "disk full" in run_log_text
    # Rounds-completed counter includes the crash.
    assert "Rounds completed: 1" in run_log_text


def test_crashed_round_updates_last_result_and_increments_state(
    cli: ModuleType, tmp_path: Path
) -> None:
    """The run state file's last_result and rounds_completed must reflect
    a crash so the dashboard's Last Round card + Stats section update
    immediately (not just at the end of the run)."""
    pool = _make_pool(4)
    args = _build_args(tmp_path, hours=0.0, pool_size=4)

    # First round crashes, second returns a normal tie so we can verify
    # ordering / state survival across mixed outcomes.
    call_count = {"n": 0}

    def round_runner(
        parent: str,
        imp_a: Improvement,
        imp_b: Improvement,
        **kwargs: Any,
    ) -> RoundResult:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("first round blew up")
        return _make_round(
            imp_a=imp_a,
            imp_b=imp_b,
            promoted=False,
            reason="discarded: A/B 0-0",
            ab_score=(0, 0),
        )

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda *a, **k: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    assert call_count["n"] == 2  # crash + real tie

    # Final state reflects BOTH rounds; last_result is the tie (most recent).
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["rounds_completed"] == 2

    # results.jsonl has the crash row + the normal tie row, in order.
    results_lines = args.results_path.read_text(encoding="utf-8").splitlines()
    assert len(results_lines) == 2
    first = json.loads(results_lines[0])
    second = json.loads(results_lines[1])
    assert first["reason"].startswith("crashed:")
    assert "first round blew up" in first["reason"]
    assert second["reason"] == "discarded: A/B 0-0"
    assert "error" not in second or not second.get("error")


def test_current_round_cleared_when_round_crashes(
    cli: ModuleType, tmp_path: Path
) -> None:
    """A round that raises still clears the current-round file so the
    dashboard doesn't show stale progress from the dead round."""
    pool = _make_pool(2)
    args = _build_args(tmp_path, hours=0.0, pool_size=2)

    def round_runner(
        parent: str,
        imp_a: Improvement,
        imp_b: Improvement,
        **kwargs: Any,
    ) -> RoundResult:
        on_event = kwargs.get("on_round_event")
        assert on_event is not None
        on_event({"type": "ab_start", "cand_a": "x_a", "cand_b": "x_b", "total": 4})
        raise RuntimeError("snapshot failed mid-flight")

    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda *a, **k: pool,
        run_round_fn=round_runner,
        current_version_fn=lambda: "v0",
    )
    # Crashes are consumed as ties; with 2-item pool we then exit
    # pool-exhausted.
    assert rc == 0

    final = json.loads(args.current_round_path.read_text(encoding="utf-8"))
    assert final["active"] is False
