"""CLI-level tests for ``scripts/evolve.py``.

Exercises the orchestration loop's control flow (budget, pool exhaustion,
no-progress, commit-on-promote, state-file writes) with every heavy
boundary mocked out. The production ``orchestrator.evolve.run_round``,
Claude invocation, and git subprocess calls are all replaced with
scripted fakes.
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
) -> argparse.Namespace:
    """Construct an argparse.Namespace pointing at tmp_path for all state."""
    return argparse.Namespace(
        pool_size=pool_size,
        ab_games=ab_games,
        gate_games=gate_games,
        hours=hours,
        map=map_name,
        no_commit=no_commit,
        results_path=tmp_path / "evolve_results.jsonl",
        pool_path=tmp_path / "evolve_pool.json",
        state_path=tmp_path / "evolve_run_state.json",
        run_log=run_log if run_log is not None else tmp_path / "run.md",
        seed=seed,
        return_loser=False,
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
# 5. Consecutive no-progress stop
# ---------------------------------------------------------------------------


def test_no_progress_stop_after_three_discards(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Three consecutive discards trip the no-progress break."""
    pool = _make_pool(10)
    # All three discards — reason uses AB-tie language so
    # _classify_outcome returns 'discarded-tie' and the sandbox-free
    # status-update marks both imps consumed-tie.
    round_runner = _ScriptedRoundRunner(
        [
            _make_round(
                imp_a=pool[0],
                imp_b=pool[1],
                promoted=False,
                reason="discarded: A/B was 2-2 tie; both improvements consumed",
            )
            for _ in range(3)
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
    assert state["no_progress_streak"] == 3
    assert state["rounds_completed"] == 3


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
