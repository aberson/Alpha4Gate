"""CLI-level tests for ``scripts/evolve.py`` (generation-phase algorithm).

Exercises the orchestration loop's control flow — pool generation, fitness
phase, stack-apply + import check, regression + rollback, pool refresh,
commit / revert helpers, and state-file writes. Every heavy boundary
(``run_fitness_eval``, ``_stack_apply_and_promote``, ``run_regression_eval``,
``generate_pool``, ``git_commit_evo_auto``, ``git_revert_evo_auto``) is
replaced with a scripted fake.
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import logging
import re
import subprocess
import sys
import threading
import tomllib
from collections.abc import Callable
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from orchestrator.contracts import SelfPlayRecord
from orchestrator.evolve import (
    FitnessResult,
    Improvement,
    RegressionResult,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------


def _load_cli_module() -> ModuleType:
    """Import ``scripts/evolve.py`` as module ``evolve_cli``.

    Register in sys.modules BEFORE exec so Python 3.14's @dataclass can
    resolve ``cls.__module__`` during KW_ONLY detection. Without this the
    first @dataclass in the script raises AttributeError on 3.14.
    """
    if "evolve_cli" in sys.modules:
        return sys.modules["evolve_cli"]
    spec = importlib.util.spec_from_file_location(
        "evolve_cli", str(_REPO_ROOT / "scripts" / "evolve.py")
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["evolve_cli"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cli() -> ModuleType:
    return _load_cli_module()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_imp(
    title: str = "imp",
    type_: str = "dev",
    *,
    rank: int = 1,
    files_touched: list[str] | None = None,
) -> Improvement:
    return Improvement(
        rank=rank,
        title=title,
        type=cast(Any, type_),
        description=f"{title} description",
        principle_ids=[],
        expected_impact=f"{title} impact",
        concrete_change=(
            json.dumps({"file": "reward_rules.json", "patch": {"dummy": 1}})
            if type_ == "training"
            else f"edit module_{rank}.py to do the thing"
        ),
        files_touched=list(files_touched) if files_touched is not None else [],
    )


def _make_pool(n: int) -> list[Improvement]:
    return [_make_imp(title=f"imp-{i}", rank=i + 1) for i in range(n)]


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
        timestamp="2026-04-21T00:00:00+00:00",
        error=None,
    )


def _fitness(
    imp: Improvement,
    *,
    bucket: str,
    wins: int | None = None,
    games: int = 5,
    candidate: str = "cand_x",
    parent: str = "v0",
) -> FitnessResult:
    if wins is None:
        wins = {"pass": 3, "close": 2, "fail": 1}[bucket]
    record = [_rec(candidate, parent, candidate) for _ in range(wins)] + [
        _rec(candidate, parent, parent) for _ in range(games - wins)
    ]
    return FitnessResult(
        parent=parent,
        candidate=candidate,
        imp=imp,
        record=record,
        wins_candidate=wins,
        wins_parent=games - wins,
        games=games,
        bucket=cast(Any, bucket),
        reason=f"fitness {bucket}: {candidate} {wins}-{games - wins}",
    )


def _regression(
    *,
    new_parent: str,
    prior_parent: str,
    rolled_back: bool,
    wins_new: int | None = None,
    games: int = 5,
) -> RegressionResult:
    if wins_new is None:
        wins_new = 1 if rolled_back else 3
    record = [_rec(new_parent, prior_parent, new_parent) for _ in range(wins_new)] + [
        _rec(new_parent, prior_parent, prior_parent)
        for _ in range(games - wins_new)
    ]
    return RegressionResult(
        new_parent=new_parent,
        prior_parent=prior_parent,
        record=record,
        wins_new=wins_new,
        wins_prior=games - wins_new,
        games=games,
        rolled_back=rolled_back,
        reason=f"regression {'rollback' if rolled_back else 'pass'}",
    )


def _build_args(
    tmp_path: Path,
    *,
    hours: float = 0.0,
    generations: int = 0,
    pool_size: int = 4,
    games_per_eval: int = 5,
    no_commit: bool = True,
    map_name: str = "Simple64",
    run_log: Path | None = None,
    game_time_limit: int = 1800,
    hard_timeout: float = 2700.0,
    screen_null_diff: bool = False,
) -> argparse.Namespace:
    """Construct an argparse.Namespace pointing at tmp_path for all state.

    Defaults to generations=0 (unlimited) so existing tests that drive
    multi-generation runs are not capped by the new --generations default.
    """
    return argparse.Namespace(
        pool_size=pool_size,
        games_per_eval=games_per_eval,
        hours=hours,
        generations=generations,
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
        resume=False,
        priors_path=None,
        post_training_cycles=0,
        backend_url="http://localhost:8765",
        concurrency=1,
        screen_null_diff=screen_null_diff,
    )


class _ScriptedFitness:
    """Pops a scripted FitnessResult per call; rewrites imp to caller's imp."""

    def __init__(self, bucket_plan: list[str]) -> None:
        self._buckets = list(bucket_plan)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        parent: str,
        imp: Improvement,
        **kwargs: Any,
    ) -> FitnessResult:
        self.calls.append({"parent": parent, "imp": imp, "kwargs": kwargs})
        if not self._buckets:
            raise AssertionError(
                "ScriptedFitness: no more scripted buckets; "
                f"call #{len(self.calls)} has nothing to return"
            )
        bucket = self._buckets.pop(0)
        return _fitness(
            imp,
            bucket=bucket,
            parent=parent,
            candidate=f"cand_{imp.title}",
        )


class _ScriptedStackApply:
    """Pops a stack-apply outcome per call; echoes imps back from the caller.

    Plan entries are ``(promoted, new_version)``. ``promoted=True``
    means the import check passed and the snapshot was promoted to
    ``new_version``; ``promoted=False`` means the import check failed
    and the snapshot was rolled back (``new_version`` should be
    ``None``).

    Post-H3-refactor: the helper's contract includes invoking the
    caller-supplied ``commit_fn``. When ``promoted=True`` and the
    caller passed ``commit_fn`` via kwargs, this scripted stand-in
    calls it so tests asserting commit observation still work. If
    ``commit_fn`` returns ``(False, None)``, the scripted outcome is
    flipped to ``stack-apply-commit-fail`` with ``promoted=False``
    — matching what the real helper does on commit failure.

    Imports ``StackApplyOutcome`` from the module under test lazily so
    the fixture works with the dynamic module-loading dance in
    :func:`_load_cli_module`.
    """

    def __init__(self, plan: list[tuple[bool, str | None]]) -> None:
        self._plan: list[tuple[bool, str | None]] = list(plan)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        parent: str,
        winning_imps: list[Improvement],
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {
                "parent": parent,
                "winning_imps": list(winning_imps),
                "kwargs": kwargs,
            }
        )
        if not self._plan:
            raise AssertionError(
                "ScriptedStackApply: no more scripted outcomes; "
                f"call #{len(self.calls)} has nothing to return"
            )
        promoted, new_version = self._plan.pop(0)
        cli = _load_cli_module()
        outcome: str
        reason: str
        promote_sha: str | None = None
        if promoted:
            # Simulate the helper's commit step: if the caller passed
            # commit_fn, invoke it and honor the (ok, sha) return.
            commit_fn = kwargs.get("commit_fn")
            generation = kwargs.get("generation", 0)
            if commit_fn is not None:
                commit_ok, sha = commit_fn(
                    new_version,
                    generation,
                    [imp.title for imp in winning_imps],
                )
                if not commit_ok:
                    outcome = "stack-apply-commit-fail"
                    reason = (
                        f"stack-apply commit-fail: {new_version} "
                        f"({len(winning_imps)} imps) rolled back"
                    )
                    return cli.StackApplyOutcome(
                        parent=parent,
                        stacked_imps=list(winning_imps),
                        new_version=None,
                        promote_sha=None,
                        promoted=False,
                        outcome=outcome,
                        reason=reason,
                    )
                promote_sha = sha
            outcome = "stack-apply-pass"
            reason = (
                f"stack-apply pass: promoted {new_version} "
                f"({len(winning_imps)} imps) from parent {parent}"
            )
        else:
            outcome = "stack-apply-import-fail"
            reason = (
                f"stack-apply import-fail: scratch ({len(winning_imps)} "
                f"imps) failed import check"
            )
        return cli.StackApplyOutcome(
            parent=parent,
            stacked_imps=list(winning_imps),
            new_version=new_version if promoted else None,
            promote_sha=promote_sha,
            promoted=promoted,
            outcome=outcome,
            reason=reason,
        )


class _ScriptedRegression:
    """Pops a rolled_back bool per call."""

    def __init__(self, plan: list[bool]) -> None:
        self._plan = list(plan)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        new_parent: str,
        prior_parent: str,
        **kwargs: Any,
    ) -> RegressionResult:
        self.calls.append(
            {
                "new_parent": new_parent,
                "prior_parent": prior_parent,
                "kwargs": kwargs,
            }
        )
        if not self._plan:
            raise AssertionError(
                "ScriptedRegression: no more scripted outcomes"
            )
        rolled_back = self._plan.pop(0)
        return _regression(
            new_parent=new_parent,
            prior_parent=prior_parent,
            rolled_back=rolled_back,
        )


# ---------------------------------------------------------------------------
# 0. _atomic_write_json retry behavior (Windows file-lock race)
# ---------------------------------------------------------------------------


def test_atomic_write_json_retries_on_permission_error(
    cli: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient PermissionError on ``tmp.replace`` is retried with backoff.

    The Alpha4Gate backend polls evolve state files while the run is in
    flight. On Windows, its short-lived read handles can cause
    ``os.replace`` to fail with WinError 5. Two failures followed by a
    success must still land the file.
    """
    target = tmp_path / "state.json"

    original_replace = Path.replace
    call_count = {"n": 0}

    def flaky_replace(self: Path, new: Path | str) -> Path:
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise PermissionError("simulated WinError 5")
        return original_replace(self, new)

    sleeps: list[float] = []
    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)

    cli._atomic_write_json(target, {"k": "v"})

    assert call_count["n"] == 3
    assert sleeps == [0.05, 0.1]  # two backoffs before the third attempt succeeded
    assert json.loads(target.read_text()) == {"k": "v"}


def test_atomic_write_json_final_attempt_raises(
    cli: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If every retry fails, the original PermissionError propagates."""
    target = tmp_path / "state.json"

    def always_fail(self: Path, new: Path | str) -> Path:
        raise PermissionError("simulated WinError 5")

    monkeypatch.setattr(Path, "replace", always_fail)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    with pytest.raises(PermissionError):
        cli._atomic_write_json(target, {"k": "v"})


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
    assert "--games-per-eval" in out
    assert "--hours" in out
    assert "--viewer" in out
    # Removed flags must not re-appear.
    assert "--ab-games" not in out
    assert "--gate-games" not in out
    assert "--return-loser" not in out


def test_default_flags(cli: ModuleType) -> None:
    """Pin the documented defaults."""
    args = cli.build_parser().parse_args([])
    assert args.pool_size == 10
    assert args.games_per_eval == 5
    assert args.hours == 0.0
    assert args.generations == 1
    assert args.map == "Simple64"
    assert args.no_commit is False
    assert args.resume is False
    assert args.current_round_path.name == "evolve_current_round.json"
    assert args.post_training_cycles == 0
    assert args.backend_url == "http://localhost:8765"
    assert args.lineages == 1
    assert args.regression_rule == "majority"
    assert args.viewer is False


# ---------------------------------------------------------------------------
# 2. Pre-flight guards
# ---------------------------------------------------------------------------


def test_sc2_not_installed_returns_1(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-flight fails cleanly when the SC2 install dir is missing."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: False)
    args = _build_args(tmp_path)
    # run_loop returns 1 before touching any heavy boundary.
    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("should not reach pool gen")
        ),
        run_fitness_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        current_version_fn=lambda: "v0",
    )
    assert rc == 1


def test_pool_generation_failure_returns_1(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pool-gen exception is logged and returns 1 (writes status=failed)."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path)

    def boom(*a: Any, **k: Any) -> list[Improvement]:
        raise RuntimeError("Claude rate limit")

    rc = cli.run_loop(
        args,
        generate_pool_fn=boom,
        run_fitness_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        current_version_fn=lambda: "v0",
    )
    assert rc == 1
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["status"] == "failed"


# ---------------------------------------------------------------------------
# 3. Pool exhaustion + wall-clock stop
# ---------------------------------------------------------------------------


def test_pool_exhaustion_stops_loop(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All pool items evict on fitness-fail → pool exhausted, loop exits."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=3)
    pool = _make_pool(3)

    # Every fitness eval evicts (fail). Pool refresh generates 0 replacements
    # so the loop exits pool-exhausted after generation 1.
    scripted_fitness = _ScriptedFitness(["fail", "fail", "fail"])

    def refresh_empty(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            # Refresh call — return empty so pool stays empty.
            return []
        return pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh_empty,
        run_fitness_fn=scripted_fitness,
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no winners, stack-apply should not fire")
        ),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no promotion, regression should not fire")
        ),
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["generations_completed"] == 1
    assert state["generations_promoted"] == 0
    assert state["evictions"] == 3


def test_wall_clock_stops_before_second_generation(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """time_fn trips past the budget so the second generation-head exits."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=2, hours=1.0)
    pool = _make_pool(2)

    # Every fitness close → fitness-close; pool refresh tops up so active
    # count doesn't drop. Budget check at the top of gen 2 trips.
    def refresh_same(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []  # no refreshes needed — close-losses flip back to active
        return pool

    scripted_fitness = _ScriptedFitness(["close", "close", "close", "close"])
    call_count = {"n": 0}

    def fake_time() -> float:
        n = call_count["n"]
        call_count["n"] += 1
        # 0: start_monotonic; 1: budget check pre-gen-1 (0s elapsed); thereafter past.
        return 0.0 if n <= 1 else 7200.0

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh_same,
        run_fitness_fn=scripted_fitness,
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        current_version_fn=lambda: "v0",
        time_fn=fake_time,
    )
    assert rc == 0
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["generations_completed"] == 1


def test_generations_cap_stops_after_n_generations(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--generations N stops the loop after N completed generations.

    Pool stays full (close-losses flip back to active + refresh tops up),
    so the only thing that can stop the loop here is the gen cap.
    """
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=2, hours=0.0, generations=2)
    pool = _make_pool(2)

    def refresh_same(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    # 4 buckets = 2 imps × 2 generations; nothing left for a 3rd gen.
    scripted_fitness = _ScriptedFitness(["close", "close", "close", "close"])

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh_same,
        run_fitness_fn=scripted_fitness,
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        current_version_fn=lambda: "v0",
        time_fn=lambda: 0.0,
    )
    assert rc == 0
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    # If the cap hadn't fired, the loop would request a 5th fitness bucket
    # and _ScriptedFitness would raise — so reaching gen=2 cleanly is itself
    # the proof that "generations-reached" exited the loop.
    assert state["generations_completed"] == 2


# ---------------------------------------------------------------------------
# 4. Happy-path single generation (stack-apply promote + regression pass)
# ---------------------------------------------------------------------------


def test_happy_path_stack_promote_then_regression_pass(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=3, no_commit=True)
    pool = _make_pool(3)

    # 2 pass, 1 fail.
    fitness = _ScriptedFitness(["pass", "pass", "fail"])
    stack_apply = _ScriptedStackApply([(True, "v1")])
    regression = _ScriptedRegression([False])  # regression pass → keep new

    parent_holder = {"current": "v0"}

    def current_version_fn() -> str:
        return parent_holder["current"]

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=regression,
        current_version_fn=current_version_fn,
    )
    assert rc == 0
    # Fitness ran once per pool item (3 calls).
    assert len(fitness.calls) == 3
    # Stack-apply ran once, against 2 winners.
    assert len(stack_apply.calls) == 1
    assert len(stack_apply.calls[0]["winning_imps"]) == 2
    # Regression ran once, v1 vs v0.
    assert len(regression.calls) == 1
    assert regression.calls[0]["new_parent"] == "v1"
    assert regression.calls[0]["prior_parent"] == "v0"

    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["generations_promoted"] == 1
    assert state["parent_current"] == "v1"

    # Pool file shows two promoted, one evicted.
    pool_state = json.loads(args.pool_path.read_text(encoding="utf-8"))
    statuses = [item["status"] for item in pool_state["pool"]]
    assert statuses.count("promoted") == 2
    assert statuses.count("evicted") == 1


def _run_one_gen_capturing_regression(
    cli: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    regression_rule: str | None,
) -> _ScriptedRegression:
    """Drive one promote+regression generation, returning the regression spy.

    When *regression_rule* is None the ``regression_rule`` attribute is left
    OFF the Namespace (mirrors an operator who never passed the flag), so the
    production ``getattr(args, "regression_rule", "majority")`` default is
    exercised end-to-end.
    """
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=2, no_commit=True)
    if regression_rule is not None:
        args.regression_rule = regression_rule
    pool = _make_pool(2)

    fitness = _ScriptedFitness(["pass", "pass"])
    stack_apply = _ScriptedStackApply([(True, "v1")])
    regression = _ScriptedRegression([False])  # keep new

    def current_version_fn() -> str:
        return "v0"

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        return [] if k.get("skip_mirror") else pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=regression,
        current_version_fn=current_version_fn,
    )
    assert rc == 0
    assert len(regression.calls) == 1
    return regression


def test_regression_rule_flag_threaded_to_run_regression(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Producer->consumer: --regression-rule reaches run_regression_eval.

    A rename/typo in the getattr key or a dropped kwarg would silently pin the
    gate to "majority" for an operator who asked for "one-sided"; this pins the
    wiring through the production caller (run_loop). See
    dev/.claude/rules/code-quality.md ("new component wired into production
    needs a test through the production caller").
    """
    regression = _run_one_gen_capturing_regression(
        cli, tmp_path, monkeypatch, regression_rule="one-sided"
    )
    assert regression.calls[0]["kwargs"].get("rule") == "one-sided"


def test_regression_rule_defaults_to_majority_when_flag_absent(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flag absent from the Namespace -> production getattr default 'majority'
    is threaded through, keeping the historical gate byte-for-byte."""
    regression = _run_one_gen_capturing_regression(
        cli, tmp_path, monkeypatch, regression_rule=None
    )
    assert regression.calls[0]["kwargs"].get("rule") == "majority"


def test_all_fitness_pass_imps_stacked_into_new_version(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Option B (gate-reduction plan): every fitness-pass imp is stacked.

    Pre-2026-04-23 the composition phase decided empirically which subset
    stacked cleanly. Post-removal, the caller trusts regression to catch
    bad interactions and stacks ALL fitness-pass imps unconditionally.
    """
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=4, no_commit=True)
    pool = _make_pool(4)

    # 3 pass, 1 fail — all three winners should stack.
    fitness = _ScriptedFitness(["pass", "pass", "pass", "fail"])
    stack_apply = _ScriptedStackApply([(True, "v1")])
    regression = _ScriptedRegression([False])

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        return [] if k.get("skip_mirror") else pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=regression,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    assert len(stack_apply.calls) == 1
    # All three fitness-pass imps were passed in, sorted by rank.
    winning = stack_apply.calls[0]["winning_imps"]
    assert [imp.rank for imp in winning] == [1, 2, 3]

    pool_state = json.loads(args.pool_path.read_text(encoding="utf-8"))
    statuses = [item["status"] for item in pool_state["pool"]]
    assert statuses.count("promoted") == 3
    assert statuses.count("evicted") == 1


def test_import_fail_outcome_skips_regression_and_commit(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Import-fail outcome must skip regression and leave parent unchanged.

    CLI-level test with the helper fully mocked — asserts the
    control-flow contract only (no filesystem rollback verification).
    The real rollback primitive is exercised by the primitive tests in
    ``tests/test_evolve.py::TestStackApplyAndPromote``.
    """
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=3, no_commit=True)
    pool = _make_pool(3)

    fitness = _ScriptedFitness(["pass", "pass", "fail"])
    stack_apply = _ScriptedStackApply([(False, None)])

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        return [] if k.get("skip_mirror") else pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("regression must not run when import check fails")
        ),
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    assert len(stack_apply.calls) == 1

    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["generations_promoted"] == 0
    assert state["parent_current"] == "v0"

    # Results jsonl has exactly one stack_apply row with the
    # import-fail outcome.
    results_lines = [
        line
        for line in args.results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    stack_rows = [
        json.loads(line)
        for line in results_lines
        if json.loads(line).get("phase") == "stack_apply"
    ]
    assert len(stack_rows) == 1
    assert stack_rows[0]["outcome"] == "stack-apply-import-fail"


# ---------------------------------------------------------------------------
# 5. Fitness-all-fail skips stack-apply and regression entirely
# ---------------------------------------------------------------------------


def test_fitness_all_fail_no_promotion_no_regression(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No fitness passes → neither stack-apply nor regression fires.

    Post-2026-04-23 gate-reduction: when every imp fails fitness there
    is no winning_imps list to stack-apply, so both stack_apply_fn
    and run_regression_fn must NOT be called. Asserts promoted count
    is 0 and those injected fns never fire.
    """
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=3, no_commit=True)
    pool = _make_pool(3)

    fitness = _ScriptedFitness(["fail", "fail", "fail"])

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        return [] if k.get("skip_mirror") else pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("stack-apply must not run when 0 fitness passes")
        ),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("regression must not run when nothing promoted")
        ),
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["generations_promoted"] == 0
    assert state["parent_current"] == "v0"
    # Every fitness row landed; no stack_apply or regression row.
    results_lines = [
        line
        for line in args.results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    phases = {json.loads(line).get("phase") for line in results_lines}
    assert phases == {"fitness"}


# ---------------------------------------------------------------------------
# 6. Regression rollback triggers revert
# ---------------------------------------------------------------------------


def test_regression_rollback_triggers_revert_and_reverts_parent(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=2, no_commit=False)
    pool = _make_pool(2)

    fitness = _ScriptedFitness(["pass", "pass"])
    stack_apply = _ScriptedStackApply([(True, "v1")])
    regression = _ScriptedRegression([True])  # rollback

    commit_calls: list[dict[str, Any]] = []

    def fake_commit(
        new_version: str,
        generation: int,
        stacked_titles: list[str],
        **kwargs: Any,
    ) -> tuple[bool, str | None]:
        commit_calls.append(
            {
                "new_version": new_version,
                "generation": generation,
                "stacked_titles": list(stacked_titles),
            }
        )
        return True, f"sha-{generation}"

    revert_calls: list[dict[str, Any]] = []

    def fake_revert(
        promote_sha: str,
        generation: int,
        reason: str,
        **kwargs: Any,
    ) -> bool:
        revert_calls.append(
            {
                "promote_sha": promote_sha,
                "generation": generation,
                "reason": reason,
            }
        )
        return True

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=regression,
        commit_fn=fake_commit,
        revert_fn=fake_revert,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    # Commit was made, then reverted.
    assert len(commit_calls) == 1
    assert len(revert_calls) == 1
    assert revert_calls[0]["promote_sha"] == "sha-1"
    assert revert_calls[0]["generation"] == 1

    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    # No promoted generations count rollback.
    assert state["generations_promoted"] == 0
    # Parent restored to v0.
    assert state["parent_current"] == "v0"
    # Imps flipped to regression-rollback.
    pool_state = json.loads(args.pool_path.read_text(encoding="utf-8"))
    statuses = [item["status"] for item in pool_state["pool"]]
    assert statuses.count("regression-rollback") == 2


# ---------------------------------------------------------------------------
# 7. Crash handling
# ---------------------------------------------------------------------------


def test_fitness_crash_evicts_imp_and_continues(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fitness exception evicts that imp but the other fitness evals run."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=3, no_commit=True)
    pool = _make_pool(3)

    def fitness(parent: str, imp: Improvement, **kwargs: Any) -> FitnessResult:
        if imp.title == "imp-1":
            raise RuntimeError("selfplay OOM")
        return _fitness(imp, bucket="pass", candidate=f"cand_{imp.title}", parent=parent)

    stack_apply = _ScriptedStackApply([(True, "v1")])
    regression = _ScriptedRegression([False])

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=regression,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    # Only 2 pass imps reached stack-apply (crashed one was evicted).
    assert len(stack_apply.calls[0]["winning_imps"]) == 2

    # Crash log has an entry for the crashed imp.
    crash_lines = [
        line
        for line in args.crash_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(crash_lines) == 1
    crash = json.loads(crash_lines[0])
    assert crash["phase"] == "fitness"
    assert crash["imp_title"] == "imp-1"


def test_stack_apply_crash_ends_generation_without_promoting(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception inside stack_apply_fn is logged as a crash and the
    generation ends without promoting. Regression must NOT fire."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=2, no_commit=True)
    pool = _make_pool(2)

    fitness = _ScriptedFitness(["pass", "pass"])

    def exploding_stack_apply(*a: Any, **kwargs: Any) -> Any:
        raise RuntimeError("stack-apply OOM")

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=exploding_stack_apply,
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no promotion, regression should not fire")
        ),
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["generations_promoted"] == 0
    crash_lines = [
        line
        for line in args.crash_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(
        json.loads(line)["phase"] == "stack_apply" for line in crash_lines
    )


# ---------------------------------------------------------------------------
# 8. Retry cap enforces eviction after 3 fitness evals
# ---------------------------------------------------------------------------


def test_retry_cap_evicts_chronic_close_loss(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An imp that's fitness-close three times gets evicted at the cap."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=1, no_commit=True)
    pool = _make_pool(1)

    # All three evals close — after the 3rd, retry_count == 3 and the imp
    # evicts at pool refresh. The 4th generation finds no active imps.
    fitness = _ScriptedFitness(["close", "close", "close"])

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []  # no replacement
        return pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no winners, stack-apply should not fire")
        ),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    # Three generations before eviction.
    assert state["generations_completed"] == 3
    assert state["evictions"] == 1
    pool_state = json.loads(args.pool_path.read_text(encoding="utf-8"))
    assert pool_state["pool"][0]["status"] == "evicted"
    assert pool_state["pool"][0]["retry_count"] == 3


# ---------------------------------------------------------------------------
# 9. Pool refresh tops up active pool to pool_size
# ---------------------------------------------------------------------------


def test_pool_refresh_tops_up_to_pool_size(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=3, no_commit=True)
    initial_pool = _make_pool(3)
    # One-shot refresh: first call after promotion returns 2 replacements;
    # subsequent refresh calls (if any) return empty so the loop terminates.
    replacements = [
        _make_imp(title="refresh-0", rank=100, type_="dev"),
        _make_imp(title="refresh-1", rank=101, type_="dev"),
    ]

    # Gen 1: pass/fail/fail. Stack promote. Gen 2: both refresh imps fail.
    fitness = _ScriptedFitness(["pass", "fail", "fail", "fail", "fail"])
    stack_apply = _ScriptedStackApply([(True, "v1")])
    regression = _ScriptedRegression([False])

    refresh_calls = {"n": 0}

    def generate(parent: str, **kwargs: Any) -> list[Improvement]:
        if kwargs.get("skip_mirror"):
            refresh_calls["n"] += 1
            # First refresh call returns the 2 replacements; subsequent
            # calls return empty so the loop terminates on pool-exhaustion.
            return replacements if refresh_calls["n"] == 1 else []
        return initial_pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=generate,
        run_fitness_fn=fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=regression,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    assert refresh_calls["n"] >= 1
    pool_state = json.loads(args.pool_path.read_text(encoding="utf-8"))
    titles = [item["title"] for item in pool_state["pool"]]
    assert "refresh-0" in titles
    assert "refresh-1" in titles


# ---------------------------------------------------------------------------
# 9b. --priors-exclude-promoted wiring (Phase EJ Step EJ.1)
# ---------------------------------------------------------------------------


def _write_priors(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "favorites": [
                    {
                        "title": "Splash-readiness",
                        "type": "dev",
                        "description": "",
                        "principle_ids": ["§3"],
                        "expected_impact": "",
                        "concrete_change": "Warp HTs before engaging.",
                        "files_touched": ["bots/v0/tactics.py"],
                    },
                    {
                        "title": "Chrono Boost sequencing",
                        "type": "dev",
                        "description": "",
                        "principle_ids": ["§7"],
                        "expected_impact": "",
                        "concrete_change": "Chrono the Nexus first.",
                        "files_touched": ["bots/v0/economy.py"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def _drive_promote_then_refresh(
    cli: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    exclude_flag: bool,
) -> dict[str, Any]:
    """Run one promote+survive generation; capture the refresh call's kwargs.

    The initial pool holds a single dev imp titled "Splash-readiness". It
    passes fitness, stack-applies to v1, and survives regression — so the
    run's promoted-title set becomes {"Splash-readiness"}. This asserts the
    PRODUCTION WIRING only: whether run_loop threads that promoted title
    into the pool-refresh call as ``exclude_titles``. The rendered-prompt
    effect is covered by TestFormatPriorsBlock in tests/test_evolve.py — not
    re-tested here (the stub would only be re-testing the helper it calls).
    """
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    priors_file = tmp_path / "favorites.json"
    _write_priors(priors_file)

    args = _build_args(tmp_path, pool_size=1, no_commit=True)
    args.priors_path = priors_file
    args.priors_exclude_promoted = exclude_flag

    initial_pool = [_make_imp(title="Splash-readiness", rank=1, type_="dev")]
    fitness = _ScriptedFitness(["pass"])
    stack_apply = _ScriptedStackApply([(True, "v1")])
    regression = _ScriptedRegression([False])  # not rolled back → survives

    captured: dict[str, Any] = {}

    def generate(parent: str, **kwargs: Any) -> list[Improvement]:
        if kwargs.get("skip_mirror"):
            captured["refresh_kwargs"] = kwargs
            return []  # end the run on pool-exhaustion
        return initial_pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=generate,
        run_fitness_fn=fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=regression,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    assert "refresh_kwargs" in captured, "pool refresh never fired"
    return captured


def test_priors_exclude_promoted_threads_title_to_refresh(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flag ON: run_loop passes the in-run promoted title as exclude_titles."""
    captured = _drive_promote_then_refresh(
        cli, tmp_path, monkeypatch, exclude_flag=True
    )
    assert captured["refresh_kwargs"].get("exclude_titles") == {
        "Splash-readiness"
    }
    # prior_imps_path is still threaded (exclusion trims the block, not priors).
    assert captured["refresh_kwargs"].get("prior_imps_path") is not None


def test_priors_exclude_promoted_off_omits_exclude_titles(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default (flag OFF): no exclude_titles kwarg reaches the refresh call."""
    captured = _drive_promote_then_refresh(
        cli, tmp_path, monkeypatch, exclude_flag=False
    )
    assert "exclude_titles" not in captured["refresh_kwargs"]


# ---------------------------------------------------------------------------
# 10. Commit helper shape
# ---------------------------------------------------------------------------


def test_git_commit_evo_auto_builds_stack_body(
    cli: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stack-promote commit body uses a bullet list of stacked titles."""
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["git", "commit"]:
            captured["msg"] = argv[argv.index("-m") + 1]
            captured["env"] = kwargs.get("env")
        if argv[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(argv, 0, stdout="abc123\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    ok, sha = cli.git_commit_evo_auto(
        "v1",
        3,
        ["imp-a", "imp-b", "imp-c"],
        run=fake_run,
    )
    assert ok is True
    assert sha == "abc123"
    msg = captured["msg"]
    assert "generation 3 promoted stack (3 imps)" in msg
    assert "- imp-a" in msg
    assert "- imp-b" in msg
    assert "- imp-c" in msg
    assert "[evo-auto]" in msg
    # EVO_AUTO=1 must be set in the commit env; ADVISED_AUTO must be absent.
    assert captured["env"]["EVO_AUTO"] == "1"
    assert "ADVISED_AUTO" not in captured["env"]


def test_git_revert_evo_auto_uses_two_stage_revert(
    cli: ModuleType,
) -> None:
    """Revert flow: ``git revert --no-commit <sha>`` then a normal commit."""
    commands: list[list[str]] = []
    envs: list[dict[str, str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(list(argv))
        envs.append(dict(kwargs.get("env") or {}))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    ok = cli.git_revert_evo_auto(
        "abc123",
        7,
        "regression rollback: new v5 1-4 prior v4",
        run=fake_run,
    )
    assert ok is True
    # First command: git revert --no-commit <sha>. Second: git commit -m.
    assert commands[0][:3] == ["git", "revert", "--no-commit"]
    assert commands[0][3] == "abc123"
    assert commands[1][:2] == ["git", "commit"]
    revert_msg = commands[1][commands[1].index("-m") + 1]
    assert "regression rollback" in revert_msg
    assert "[evo-auto]" in revert_msg
    # EVO_AUTO=1 in both subprocess envs.
    for env in envs:
        assert env.get("EVO_AUTO") == "1"
        assert "ADVISED_AUTO" not in env


def test_git_commit_evo_auto_resets_index_when_commit_fails(
    cli: ModuleType,
) -> None:
    """If ``git commit`` fails after ``git add`` staged ``bots/<vN>/``,
    the commit primitive must drop the staged content itself.

    Without this cleanup the staged paths leak into the NEXT generation's
    commit (``git_commit_evo_auto`` does a plain ``git commit -m msg``
    with no pathspec and no ``-a``, which commits everything currently
    staged). The commit function owns the mess it staged, so it owns
    cleaning up on the failure path. Mirrors the revert path's contract.
    """
    commands: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(list(argv))
        if argv[:2] == ["git", "add"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:2] == ["git", "commit"]:
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="hook blocked commit"
            )
        if argv[:2] == ["git", "reset"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected argv: {argv!r}")

    ok, sha = cli.git_commit_evo_auto(
        "v1",
        3,
        ["imp-a"],
        run=fake_run,
    )
    assert ok is False
    assert sha is None
    # Sequence: git add, git commit (fails), git reset HEAD -- .
    assert [c[:2] for c in commands] == [
        ["git", "add"],
        ["git", "commit"],
        ["git", "reset"],
    ]
    assert commands[2] == ["git", "reset", "HEAD", "--", "."]


def test_git_revert_evo_auto_resets_index_when_commit_fails(
    cli: ModuleType,
) -> None:
    """If ``git commit`` fails after ``git revert --no-commit`` staged the
    reverse diff, the revert primitive must drop the staged changes itself.

    Without this cleanup the staged reverse diff leaks into the NEXT
    generation's commit (``git_commit_evo_auto`` does a plain
    ``git commit -m msg`` with no pathspec and no ``-a``, which commits
    everything currently staged). The revert function owns the mess it
    created, so it owns cleaning it up on the failure path.
    """
    commands: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(list(argv))
        # revert succeeds (stages the reverse diff), commit fails, reset
        # must then happen to drop the staged diff.
        if argv[:2] == ["git", "revert"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:2] == ["git", "commit"]:
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="hook blocked commit"
            )
        if argv[:2] == ["git", "reset"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected argv: {argv!r}")

    ok = cli.git_revert_evo_auto(
        "abc123",
        7,
        "regression rollback: new v5 1-4 prior v4",
        run=fake_run,
    )
    assert ok is False
    # Verify the sequence: revert, commit (fails), reset HEAD -- .
    assert [c[:2] for c in commands] == [
        ["git", "revert"],
        ["git", "commit"],
        ["git", "reset"],
    ]
    # The reset must target HEAD and the whole tree (``.``) — scoped to
    # index so the working tree is unchanged, so subsequent pointer
    # fallbacks still see their own writes.
    reset_cmd = commands[2]
    assert reset_cmd == ["git", "reset", "HEAD", "--", "."]


# ---------------------------------------------------------------------------
# 11. Post-training hook
# ---------------------------------------------------------------------------


def test_post_training_fires_on_promotion_when_flag_set(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=2, no_commit=True)
    args.post_training_cycles = 5
    pool = _make_pool(2)

    fitness = _ScriptedFitness(["pass", "pass"])
    stack_apply = _ScriptedStackApply([(True, "v1")])
    regression = _ScriptedRegression([False])

    calls: list[dict[str, Any]] = []

    def fake_post_training(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"status": "ok"}

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=regression,
        current_version_fn=lambda: "v0",
        post_training_fn=fake_post_training,
    )
    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["cycles"] == 5
    assert calls[0]["new_parent"] == "v1"


def test_post_training_does_not_fire_without_promotion(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=2, no_commit=True)
    args.post_training_cycles = 5
    pool = _make_pool(2)

    fitness = _ScriptedFitness(["fail", "fail"])
    called = {"n": 0}

    def fake_post_training(**kwargs: Any) -> dict[str, Any]:
        called["n"] += 1
        return {"status": "ok"}

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        current_version_fn=lambda: "v0",
        post_training_fn=fake_post_training,
    )
    assert rc == 0
    assert called["n"] == 0


# ---------------------------------------------------------------------------
# 12. Resume — load pool + per-item state from disk
# ---------------------------------------------------------------------------


def test_resume_loads_existing_pool_and_skips_generation(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=2, no_commit=True)
    args.resume = True

    # Seed a pool file as if a prior run had completed one fitness eval.
    pool = _make_pool(2)
    per_item_state = {
        0: cli.PerItemState(
            status="fitness-close",
            fitness_score=[2, 5],
            retry_count=1,
            first_evaluated_against="v0",
            last_evaluated_against="v0",
        ),
        1: cli.PerItemState(),  # active
    }
    cli.write_pool_state(
        args.pool_path,
        pool,
        parent="v0",
        per_item_state=per_item_state,
    )

    # Both imps fail in gen 1 → pool exhausted, single generation, loop ends.
    fitness = _ScriptedFitness(["fail", "fail"])

    initial_calls = {"n": 0}
    refresh_calls = {"n": 0}

    def tracked_generate(parent: str, **kwargs: Any) -> list[Improvement]:
        if kwargs.get("skip_mirror"):
            refresh_calls["n"] += 1
            return []  # no refresh after evictions
        initial_calls["n"] += 1
        return _make_pool(2)  # would be used on a fresh run

    rc = cli.run_loop(
        args,
        generate_pool_fn=tracked_generate,
        run_fitness_fn=fitness,
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    # Resume must NOT call generate_pool for initial pool gen.
    assert initial_calls["n"] == 0
    # Both reloaded-pool imps saw fitness.
    assert len(fitness.calls) == 2
    # The imp with retry_count=1 was the one previously fitness-close; after
    # the resumed gen its retry_count is 2.
    pool_state = json.loads(args.pool_path.read_text(encoding="utf-8"))
    assert pool_state["pool"][0]["retry_count"] == 2


def test_resume_parent_mismatch_returns_1(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path)
    args.resume = True
    # Pool file says v5 but current_version returns v0 → mismatch, exit 1.
    cli.write_pool_state(
        args.pool_path,
        _make_pool(2),
        parent="v5",
        per_item_state={0: cli.PerItemState(), 1: cli.PerItemState()},
    )
    rc = cli.run_loop(
        args,
        generate_pool_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_fitness_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        current_version_fn=lambda: "v0",
    )
    assert rc == 1
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["status"] == "failed"


# ---------------------------------------------------------------------------
# 13. Fresh run clears stale state files
# ---------------------------------------------------------------------------


def test_fresh_run_clears_stale_state(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale results.jsonl + pool.json from a prior run get wiped on a fresh run."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=2, no_commit=True)

    # Pre-seed stale content.
    args.results_path.parent.mkdir(parents=True, exist_ok=True)
    args.results_path.write_text(
        json.dumps({"stale": True}) + "\n", encoding="utf-8"
    )
    args.pool_path.parent.mkdir(parents=True, exist_ok=True)
    args.pool_path.write_text(
        json.dumps({"pool": [{"stale": True}], "parent": "vstale"}),
        encoding="utf-8",
    )

    # All fail → pool exhausted immediately.
    pool = _make_pool(2)
    fitness = _ScriptedFitness(["fail", "fail"])

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    # Stale content cleared, then new content written.
    pool_state = json.loads(args.pool_path.read_text(encoding="utf-8"))
    assert pool_state["parent"] == "v0"
    # Results file has fitness rows (no stale `{"stale": true}` line).
    results_lines = [
        line
        for line in args.results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert results_lines  # not empty
    for line in results_lines:
        assert "stale" not in line


# ---------------------------------------------------------------------------
# 14. Run-log markdown shape
# ---------------------------------------------------------------------------


def test_run_log_markdown_has_generation_table(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=2, no_commit=True)
    pool = _make_pool(2)

    fitness = _ScriptedFitness(["pass", "fail"])
    stack_apply = _ScriptedStackApply([(True, "v1")])
    regression = _ScriptedRegression([False])

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=regression,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    md = args.run_log.read_text(encoding="utf-8")
    assert "# Evolve run" in md
    assert "Generations completed: 1" in md
    assert "Generations promoted: 1" in md
    assert "## Generations" in md
    # Table header contains the new column names.
    assert "fitness pass/close/fail" in md
    assert "stack-apply" in md
    assert "regression" in md


# ---------------------------------------------------------------------------
# 15. Rollback-order bug fix (run 20260422-0824)
# ---------------------------------------------------------------------------


def test_regression_rollback_reverts_cleanly_on_dirty_pointer(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback must call revert_fn against a CLEAN tree, not a dirty one.

    Regression: run 20260422-0824 gens 1 and 3 promoted and then
    rolled back, but the primitive (``run_regression_eval``) was
    rewriting ``bots/current/current.txt`` to ``prior_parent`` on its
    own — dirtying the working tree. By the time
    ``scripts/evolve.py`` called ``git revert --no-commit``, git
    refused with exit 128 ("local changes would be overwritten by
    merge"). The promote commit stayed on master unreverted.

    This test pins the fix by running the REAL ``run_regression_eval``
    primitive (with a scripted ``run_batch_fn``) against a real
    ``bots/current/current.txt`` in ``tmp_path``, and snapshotting the
    pointer's on-disk contents at the exact moment ``revert_fn`` is
    invoked. On the pre-fix primitive the snapshot would read ``v0``
    (dirty tree → production ``git revert`` bails with exit 128). On
    the fixed primitive the snapshot must read ``v1`` (clean tree →
    ``git revert`` succeeds and its reverse diff restores the pointer).
    """
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)

    # Import the real primitive + its collaborators and redirect them
    # all at tmp_path so `_restore_pointer`, `current_version`, etc.
    # use the seeded fake repo layout.
    from orchestrator import evolve as primitive_mod
    from orchestrator import registry as registry_mod
    from orchestrator import snapshot as snapshot_mod
    from orchestrator.contracts import SelfPlayRecord as _Rec
    from orchestrator.evolve import run_regression_eval

    monkeypatch.setattr(registry_mod, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(primitive_mod, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(snapshot_mod, "_repo_root", lambda: tmp_path)

    (tmp_path / "bots" / "current").mkdir(parents=True)
    pointer = tmp_path / "bots" / "current" / "current.txt"
    # Starts at v1 — the state after the stack-apply step promoted
    # the new parent. In production this is what git HEAD also holds
    # at this moment.
    pointer.write_text("v1", encoding="utf-8")

    pointer_snapshots: dict[str, str] = {}

    # Scripted run_batch returns a regression outcome where v1 loses
    # 1-4 to v0, triggering rollback.
    def scripted_run_batch(
        p1: str, p2: str, games: int, map_name: str, **kwargs: Any
    ) -> list[_Rec]:
        return [
            _Rec(
                match_id=f"m{i}",
                p1_version=p1,
                p2_version=p2,
                winner=p2 if i < 4 else p1,  # 4 wins for prior, 1 for new
                map_name=map_name,
                duration_s=10.0,
                seat_swap=False,
                timestamp="2026-04-23T00:00:00+00:00",
                error=None,
            )
            for i in range(games)
        ]

    def real_run_regression(
        new_parent: str, prior_parent: str, **kwargs: Any
    ) -> RegressionResult:
        pointer_snapshots["pre_regression"] = pointer.read_text(
            encoding="utf-8"
        )
        # Force the real primitive to use our scripted batch runner.
        # scripts/evolve.py passes run_batch_fn=None through from run_loop;
        # override here regardless of the incoming value.
        kwargs["run_batch_fn"] = scripted_run_batch
        result = run_regression_eval(new_parent, prior_parent, **kwargs)
        pointer_snapshots["post_regression"] = pointer.read_text(
            encoding="utf-8"
        )
        return result

    def fake_revert(
        promote_sha: str,
        generation: int,
        reason: str,
        **kwargs: Any,
    ) -> bool:
        # Load-bearing snapshot: on the pre-fix primitive, the pointer
        # has already been rewritten to v0 here, which is exactly the
        # dirty-tree state where production ``git revert`` bails.
        pointer_snapshots["pre_revert"] = pointer.read_text(
            encoding="utf-8"
        )
        return True

    def fake_commit(
        new_version: str,
        generation: int,
        stacked_titles: list[str],
        **kwargs: Any,
    ) -> tuple[bool, str | None]:
        return True, f"sha-{generation}"

    args = _build_args(tmp_path, pool_size=2, no_commit=False)
    pool = _make_pool(2)
    fitness = _ScriptedFitness(["pass", "pass"])
    stack_apply = _ScriptedStackApply([(True, "v1")])

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        return [] if k.get("skip_mirror") else pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=real_run_regression,
        commit_fn=fake_commit,
        revert_fn=fake_revert,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    # These are the load-bearing invariants:
    # 1. The primitive ran against the promoted pointer (v1).
    assert pointer_snapshots["pre_regression"] == "v1"
    # 2. The primitive did NOT rewrite the pointer on rollback —
    #    this was the bug. On the pre-fix primitive this value is v0.
    assert pointer_snapshots["post_regression"] == "v1", (
        "primitive must leave bots/current/current.txt untouched on "
        f"rollback; got {pointer_snapshots['post_regression']!r}. "
        "Dirty tree would cause production ``git revert`` to bail "
        "with exit 128 (run 20260422-0824 symptom)."
    )
    # 3. revert_fn observed a clean tree (pointer == HEAD == v1).
    #    This is the invariant that guarantees production ``git revert``
    #    actually runs successfully.
    assert pointer_snapshots["pre_revert"] == "v1", (
        "git revert must be invoked on a clean working tree; got "
        f"pointer={pointer_snapshots['pre_revert']!r}. "
        "This is the run 20260422-0824 rollback-order bug."
    )

    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["parent_current"] == "v0"
    assert state["generations_promoted"] == 0


def test_run_loop_aborts_if_master_has_phantom_promote_at_startup(
    cli: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pre-flight aborts with rc=1 and an error message naming both values.

    Prevents a rerun from starting against a promote commit that was
    rolled back on disk but never reverted in git — the exact state
    that run 20260422-0824 left master in before manual cleanup.

    Exercises the helper itself (not just its mocked return) by seeding
    a fake repo layout under ``tmp_path`` and injecting a ``run`` that
    simulates ``git show HEAD:bots/current/current.txt`` returning ``v1``
    while the disk pointer holds ``v0``.
    """
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)

    # --- Verify the helper directly (end-to-end on a fake repo) ---
    monkeypatch.setattr(cli, "_REPO_ROOT", tmp_path)
    (tmp_path / "bots" / "current").mkdir(parents=True)
    (tmp_path / "bots" / "current" / "current.txt").write_text(
        "v0", encoding="utf-8"
    )

    def fake_git_show(
        argv: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        assert argv[:3] == ["git", "show", "HEAD:bots/current/current.txt"]
        return subprocess.CompletedProcess(
            argv, returncode=0, stdout="v1\n", stderr=""
        )

    ok, head_v, disk_v = cli.check_no_phantom_promote(run=fake_git_show)
    assert ok is False
    assert head_v == "v1"
    assert disk_v == "v0"

    # --- Verify run_loop aborts when the helper reports a phantom state ---
    # Monkeypatch the helper directly — defaulting kwargs like ``run`` are
    # bound at module-load time, so patching subprocess.run doesn't reach
    # the helper's default, but patching the helper itself does.
    monkeypatch.setattr(
        cli,
        "check_no_phantom_promote",
        lambda **_: (False, "v1", "v0"),
    )

    args = _build_args(tmp_path, pool_size=2, no_commit=True)

    rc = cli.run_loop(
        args,
        # These must never be called; pre-flight aborts first.
        generate_pool_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("pre-flight should abort before pool gen")
        ),
        run_fitness_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        current_version_fn=lambda: "v0",
    )
    assert rc == 1

    # Error message names both values and suggests a recovery path.
    err = capsys.readouterr().err
    assert "phantom-promote" in err
    assert "'v0'" in err  # disk value
    assert "'v1'" in err  # HEAD value
    assert "git checkout bots/current/current.txt" in err


# ---------------------------------------------------------------------------
# 17. write_run_state — run_id + concurrency persistence (Step 4)
# ---------------------------------------------------------------------------


def _state_kwargs(**overrides: Any) -> dict[str, Any]:
    """Default kwargs for write_run_state in unit tests."""
    base: dict[str, Any] = {
        "status": "running",
        "parent_start": "v3",
        "parent_current": "v3",
        "started_at": "2026-04-29T10:00:00+00:00",
        "wall_budget_hours": 8.0,
        "generations_completed": 0,
        "generations_promoted": 0,
        "evictions": 0,
        "resurrections_remaining": 0,
        "pool_remaining_count": 0,
        "last_result": None,
    }
    base.update(overrides)
    return base


def test_write_run_state_defaults_persist_run_id_concurrency_as_none(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Without ``run_id``/``concurrency`` (single-flight + legacy callers),
    both fields are persisted as JSON ``null`` so the dashboard's
    ``EvolveRunState`` interface always has the keys present. The
    later additions (``cli_argv`` / ``gen_durations_seconds`` /
    ``generations_target``) follow the same null-default contract."""
    state_path = tmp_path / "evolve_run_state.json"
    cli.write_run_state(state_path, **_state_kwargs())
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["run_id"] is None
    assert payload["concurrency"] is None
    assert payload["cli_argv"] is None
    assert payload["gen_durations_seconds"] is None
    assert payload["generations_target"] is None
    # All other expected fields still present.
    for key in (
        "status",
        "parent_start",
        "parent_current",
        "started_at",
        "wall_budget_hours",
        "generation_index",
        "generations_completed",
        "generations_promoted",
        "evictions",
        "resurrections_remaining",
        "pool_remaining_count",
        "last_result",
    ):
        assert key in payload


def test_write_run_state_persists_cli_argv_and_gen_durations(
    cli: ModuleType, tmp_path: Path
) -> None:
    """The dispatcher captures ``sys.argv[1:]`` + per-generation
    durations + ``args.generations`` and persists each verbatim so the
    dashboard can render run flags and a remaining-time range."""
    state_path = tmp_path / "evolve_run_state.json"
    cli.write_run_state(
        state_path,
        cli_argv=["--hours", "8", "--pool-size", "10", "--concurrency", "3"],
        gen_durations_seconds=[1234.5, 1500.25, 1100.0],
        generations_target=20,
        **_state_kwargs(),
    )
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["cli_argv"] == [
        "--hours",
        "8",
        "--pool-size",
        "10",
        "--concurrency",
        "3",
    ]
    assert payload["gen_durations_seconds"] == [1234.5, 1500.25, 1100.0]
    assert payload["generations_target"] == 20


def test_write_run_state_persists_run_id_and_concurrency_when_provided(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Parallel dispatcher (concurrency>1) writes both fields verbatim."""
    state_path = tmp_path / "evolve_run_state.json"
    cli.write_run_state(
        state_path,
        run_id="abc12345",
        concurrency=4,
        **_state_kwargs(),
    )
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "abc12345"
    assert payload["concurrency"] == 4


def test_run_loop_persists_run_id_and_concurrency_into_state_file(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: ``run_loop`` writes run_state with the dispatcher's
    ``run_id`` + ``args.concurrency``, so the API + dashboard see them
    on every state poll. Pool exhausts immediately so the loop exits
    cleanly without spawning any workers."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=1, no_commit=True)
    args.concurrency = 3

    pool = _make_pool(1)
    fitness = _ScriptedFitness(["fail"])

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        current_version_fn=lambda: "v3",
    )
    assert rc == 0

    payload = json.loads(args.state_path.read_text(encoding="utf-8"))
    # run_id is uuid.uuid4().hex[:8] — generated unconditionally inside
    # run_loop. Verify shape, not exact value.
    assert isinstance(payload["run_id"], str)
    assert len(payload["run_id"]) == 8
    # concurrency comes through verbatim from args.concurrency.
    assert payload["concurrency"] == 3
    # The new dashboard fields are also populated end-to-end. cli_argv
    # mirrors sys.argv[1:] -- under pytest that's the test runner's argv,
    # so we only assert the field shape (list[str]). One generation
    # completes before the pool exhausts (the single imp's fitness was
    # scripted to "fail"), so gen_durations_seconds has one nonneg
    # float. generations_target reflects args.generations verbatim.
    assert isinstance(payload["cli_argv"], list)
    assert all(isinstance(s, str) for s in payload["cli_argv"])
    assert isinstance(payload["gen_durations_seconds"], list)
    assert len(payload["gen_durations_seconds"]) == 1
    assert payload["gen_durations_seconds"][0] >= 0.0
    assert payload["generations_target"] == args.generations


# ---------------------------------------------------------------------------
# 18. Multi-lineage scheduling (Phase EL Step 1) — production wiring
# ---------------------------------------------------------------------------


def test_lineages_default_one_takes_single_lineage_path(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--lineages 1 with no registry never flips the pointer to a lineage head.

    Back-compat invariant: the multi-lineage scheduling block stays a no-op,
    so ``_primitive_restore_pointer`` is not called from the scheduler at all
    (the only pointer writes come from stack-apply/regression, neither of
    which fires here since fitness always fails).
    """
    import orchestrator.lineages as lineages_mod

    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    # Point the registry lookup at an empty tmp data/ so no file is found.
    monkeypatch.setattr(lineages_mod, "_repo_root", lambda: tmp_path)

    flips: list[str] = []
    monkeypatch.setattr(
        cli, "_primitive_restore_pointer", lambda v: flips.append(v)
    )

    args = _build_args(tmp_path, pool_size=2, generations=1)
    args.lineages = 1
    pool = _make_pool(2)

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=_ScriptedFitness(["fail", "fail"]),
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    assert flips == []  # scheduler never engaged → no head flip


def test_lineages_two_round_robins_and_flips_pointer(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--lineages 2 with an on-disk registry flips the pointer to each head.

    Integration through the production entry point (``run_loop``): the
    scheduler reads ``data/lineages.json`` via ``orchestrator.lineages``,
    round-robins across the two lineages, and flips
    ``bots/current/current.txt`` to each lineage's head before its
    generation using the existing ``_primitive_restore_pointer``. Two
    generations → ``main`` (v0) then ``line-2`` (v9).
    """
    import orchestrator.lineages as lineages_mod
    from orchestrator.lineages import Lineage, write_lineages

    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    monkeypatch.setattr(lineages_mod, "_repo_root", lambda: tmp_path)

    # Seed a real 2-lineage registry under tmp data/.
    registry = {
        "main": Lineage(lineage_id="main", head_version="v0"),
        "line-2": Lineage(lineage_id="line-2", head_version="v9"),
    }
    write_lineages(tmp_path / "data" / "lineages.json", registry)

    flips: list[str] = []
    monkeypatch.setattr(
        cli, "_primitive_restore_pointer", lambda v: flips.append(v)
    )

    args = _build_args(tmp_path, pool_size=2, generations=2)
    args.lineages = 2
    pool = _make_pool(2)

    # Record which parent each fitness eval ran against, per generation.
    # Use "close" so imps flip back to active and the pool survives into
    # gen 2 (a "fail" pool would exhaust after gen 1 and never schedule the
    # second lineage).
    fitness_parents: list[str] = []

    def recording_fitness(parent: str, imp: Improvement, **k: Any) -> Any:
        fitness_parents.append(parent)
        return _fitness(imp, bucket="close", parent=parent)

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=recording_fitness,
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    # The persisted registry is written with sort_keys=True, so the loaded
    # insertion order is alphabetical: line-2 (v9) then main (v0). The
    # round-robin therefore schedules line-2 in gen 1 and main in gen 2.
    # The pointer is flipped to whichever lineage head differs from the
    # live parent (v0) at scheduling time — v9 at least.
    assert "v9" in flips
    # Both lineage heads were exercised as a fitness parent across the two
    # generations — proof the scheduler advanced ``parent_current`` to each
    # lineage's head. (2 imps per generation → 4 fitness evals total.)
    assert set(fitness_parents) == {"v0", "v9"}
    # Gen 1 is line-2 (v9); both of its imps ran against v9.
    assert fitness_parents[0] == "v9"
    assert fitness_parents[1] == "v9"


def test_lineage_promotion_advances_head_for_next_turn(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lineage that promotes branches from its ADVANCED head next turn.

    Drives a real promotion through ``run_loop`` (integration via the
    production entry point) and verifies the head-writeback persistence:
    after ``line-2`` promotes ``v9 -> v10`` on its first turn (gen 1), its
    second turn (gen 3) must run fitness against the advanced ``v10`` —
    NOT its original ``v9`` head.

    Schedule (registry sort_keys=True → load order line-2, main):
      gen 1: line-2 @ v9  → one imp passes → promote v9 -> v10
      gen 2: main   @ v0  → all "close" (no promotion)
      gen 3: line-2 @ v10 → fitness must see v10 (the advanced head)

    ``run_regression_fn`` returns no rollback so the promotion sticks.
    """
    import orchestrator.lineages as lineages_mod
    from orchestrator.lineages import Lineage, write_lineages

    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    monkeypatch.setattr(lineages_mod, "_repo_root", lambda: tmp_path)
    # Promotion path runs post-promotion hooks; stub so the real bot code
    # is not invoked under the tmp registry.
    monkeypatch.setattr(
        "bots.v0.learning.post_promotion_hooks.run_post_promotion_hooks",
        lambda _v: None,
    )

    registry = {
        "main": Lineage(lineage_id="main", head_version="v0"),
        "line-2": Lineage(lineage_id="line-2", head_version="v9"),
    }
    write_lineages(tmp_path / "data" / "lineages.json", registry)

    monkeypatch.setattr(
        cli, "_primitive_restore_pointer", lambda _v: None
    )

    args = _build_args(tmp_path, pool_size=3, generations=3)
    args.lineages = 2
    pool = _make_pool(3)

    # Record (generation, parent) per fitness call. Promote line-2's first
    # turn by passing exactly one imp against v9 while keeping the other two
    # active ("close") so the pool survives to gen 3.
    fitness_parents: list[str] = []

    def scripted_fitness(parent: str, imp: Improvement, **k: Any) -> Any:
        fitness_parents.append(parent)
        # Pass the rank-1 imp ONLY when evaluated against v9 (line-2 gen 1)
        # so v9 promotes to v10; everything else stays active via "close".
        if parent == "v9" and imp.rank == 1:
            return _fitness(imp, bucket="pass", parent=parent)
        return _fitness(imp, bucket="close", parent=parent)

    stack_apply = _ScriptedStackApply([(True, "v10")])
    regression = _ScriptedRegression([False])  # no rollback → promotion sticks

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=scripted_fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=regression,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    # Promotion happened exactly once, v9 -> v10, and regression passed.
    assert len(stack_apply.calls) == 1
    assert stack_apply.calls[0]["parent"] == "v9"
    assert len(regression.calls) == 1
    assert regression.calls[0]["new_parent"] == "v10"
    assert regression.calls[0]["prior_parent"] == "v9"

    # The head-writeback contract: line-2's SECOND turn (gen 3) ran fitness
    # against the ADVANCED head v10, never its original v9.
    # gen 1 (line-2 @ v9): 3 fitness calls against v9
    # gen 2 (main  @ v0):  2 active imps (rank-1 promoted in gen 1) → v0
    # gen 3 (line-2 @ v10): the surviving imps → v10
    assert "v10" in fitness_parents, fitness_parents
    # Every gen-3 (line-2 second turn) eval was against v10, not v9.
    gen3_parents = fitness_parents[5:]
    assert gen3_parents, fitness_parents
    assert set(gen3_parents) == {"v10"}, fitness_parents


def test_lineage_no_flip_when_head_equals_live_parent(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No redundant pointer write when a lineage's head == the live parent.

    When the scheduled lineage's ``head_version`` already equals the live
    ``parent_current``, ``_primitive_restore_pointer`` must NOT be called
    for that generation (the pointer is not re-written to the same value).

    Single-lineage registry whose head (v0) matches the live parent
    (``current_version_fn`` → v0). With fitness always "fail" there is no
    promotion either, so the ONLY possible pointer write is the scheduler's
    flip — which must not fire because head == parent.
    """
    import orchestrator.lineages as lineages_mod
    from orchestrator.lineages import Lineage, write_lineages

    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    monkeypatch.setattr(lineages_mod, "_repo_root", lambda: tmp_path)

    registry = {"main": Lineage(lineage_id="main", head_version="v0")}
    write_lineages(tmp_path / "data" / "lineages.json", registry)

    flips: list[str] = []
    monkeypatch.setattr(
        cli, "_primitive_restore_pointer", lambda v: flips.append(v)
    )

    # --lineages 1 but a non-empty on-disk registry still engages the
    # scheduler (engaged when --lineages > 1 OR a non-empty registry exists).
    args = _build_args(tmp_path, pool_size=2, generations=1)
    args.lineages = 1
    pool = _make_pool(2)

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=_ScriptedFitness(["fail", "fail"]),
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    # head (v0) == live parent (v0) → scheduler did NOT re-write the pointer.
    assert flips == []


def test_population_cap_zero_never_invokes_extinction(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Back-compat: --population-cap 0 never touches the extinction seam.

    A multi-lineage run with the cap at its default 0 must NOT call
    ``decide_extinctions_fn`` and must write no ``"extinction"`` rows — the
    generation boundary stays byte-identical to its pre-EL.4 state.
    """
    import orchestrator.lineages as lineages_mod
    from orchestrator.lineages import Lineage, write_lineages

    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    monkeypatch.setattr(lineages_mod, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_primitive_restore_pointer", lambda _v: None)

    registry = {
        "main": Lineage(lineage_id="main", head_version="v0"),
        "line-2": Lineage(lineage_id="line-2", head_version="v9"),
    }
    write_lineages(tmp_path / "data" / "lineages.json", registry)

    args = _build_args(tmp_path, pool_size=2, generations=2)
    args.lineages = 2
    args.population_cap = 0  # disabled
    args.diversity_threshold = 0.15
    pool = _make_pool(2)

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    extinction_calls: list[Any] = []

    def boom_decide(*a: Any, **k: Any) -> Any:
        extinction_calls.append((a, k))
        raise AssertionError("decide_extinctions must not be called at cap 0")

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=lambda p, imp, **k: _fitness(imp, bucket="close", parent=p),
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        decide_extinctions_fn=boom_decide,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    assert extinction_calls == []  # seam never invoked

    rows = [
        json.loads(line)
        for line in (tmp_path / "evolve_results.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert not any(r.get("phase") == "extinction" for r in rows)


def test_population_cap_culls_lineage_and_logs_extinction(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--population-cap drives decide_extinctions and culls a scheduled lineage.

    Integration through the production entry point (``run_loop``): a
    3-lineage registry over cap 2 with the extinction seam scripted to cull
    ``line-3`` after the first generation. We assert (1) an ``"extinction"``
    row is written to the results file carrying the culled lineage's id +
    head + dominator + reason, and (2) the culled lineage stops being
    scheduled — no later fitness eval runs against its head.
    """
    import orchestrator.lineages as lineages_mod
    from orchestrator.lineages import Lineage, write_lineages

    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    monkeypatch.setattr(lineages_mod, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_primitive_restore_pointer", lambda _v: None)

    # sort_keys=True load order → line-2 (v8), line-3 (v9), main (v0). The
    # round-robin schedules line-2 first, then line-3, then main, ...
    registry = {
        "main": Lineage(lineage_id="main", head_version="v0"),
        "line-2": Lineage(lineage_id="line-2", head_version="v8"),
        "line-3": Lineage(lineage_id="line-3", head_version="v9"),
    }
    write_lineages(tmp_path / "data" / "lineages.json", registry)

    args = _build_args(tmp_path, pool_size=2, generations=6)
    args.lineages = 3
    args.population_cap = 2
    args.diversity_threshold = 0.15
    pool = _make_pool(2)

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    # Use "close" so imps flip back to active and the pool survives across
    # generations (a "fail" pool would exhaust and stop the loop early).
    fitness_parents: list[str] = []

    def recording_fitness(parent: str, imp: Improvement, **k: Any) -> Any:
        fitness_parents.append(parent)
        return _fitness(imp, bucket="close", parent=parent)

    # Scripted extinction seam: cull line-3 (head v9, dominated by line-2)
    # exactly once, the first time decide_extinctions is invoked.
    decide_calls: list[dict[str, Any]] = []

    def scripted_decide(
        lineages: dict[str, Any],
        fingerprints: dict[str, Any],
        fitnesses: dict[str, float],
        *,
        cap: int,
        diversity_threshold: float,
    ) -> Any:
        decide_calls.append(
            {
                "lineage_ids": sorted(lineages.keys()),
                "cap": cap,
                "diversity_threshold": diversity_threshold,
            }
        )
        from orchestrator.population import CullDecision, PopulationVerdict

        if "line-3" in lineages:
            return PopulationVerdict(
                kept=[lid for lid in lineages if lid != "line-3"],
                culled=[
                    CullDecision(
                        lineage_id="line-3",
                        head_version=lineages["line-3"].head_version,
                        dominated_by="line-2",
                        reason="extinction: line-3 dominated by line-2 (test)",
                    )
                ],
            )
        return PopulationVerdict(kept=list(lineages.keys()), culled=[])

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=recording_fitness,
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        decide_extinctions_fn=scripted_decide,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    # The seam saw the cap + threshold passed through from argparse.
    assert decide_calls, "decide_extinctions seam was never invoked"
    assert decide_calls[0]["cap"] == 2
    assert decide_calls[0]["diversity_threshold"] == 0.15

    # An extinction row was written for line-3 with all the fields.
    rows = [
        json.loads(line)
        for line in (tmp_path / "evolve_results.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    ext_rows = [r for r in rows if r.get("phase") == "extinction"]
    assert len(ext_rows) == 1, ext_rows
    ext = ext_rows[0]
    assert ext["lineage_id"] == "line-3"
    assert ext["head_version"] == "v9"
    assert ext["dominated_by"] == "line-2"
    assert ext["outcome"] == "extinction"
    assert "reason" in ext

    # line-3 (head v9) is culled at gen 1's boundary, BEFORE its first turn
    # (round-robin order: line-2, line-3, main). Once extinct it is removed
    # from both _lineage_heads and the registry and never round-robined, so
    # no fitness eval ever runs against its head v9.
    assert "v9" not in fitness_parents, fitness_parents
    # The two surviving lineages still get scheduled across generations.
    assert set(fitness_parents) == {"v8", "v0"}, fitness_parents


def test_population_cap_real_decide_extinctions_through_gauntlet(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end real seam: real decide_extinctions on real-built fitness.

    Both prior wiring tests inject ``decide_extinctions_fn``, so the REAL
    ``orchestrator.population.decide_extinctions`` plus the EL.2-gauntlet
    feeding block (the code that constructs ``_lineage_fitness`` /
    ``_lineage_fingerprints`` from each gauntlet result and hands them to the
    real decision function) never run through ``run_loop``. This closes that
    gap: ``decide_extinctions_fn=None`` (real seam), ``--fitness-mode both``
    with one registered baseline, and an injected ``run_gauntlet_fn`` that
    returns a *deterministic* per-baseline vector per candidate. Two lineages
    each promote + gauntlet; one head ends up a strictly-weaker behavioral
    near-duplicate of the other. With ``--population-cap 1`` the real
    decide_extinctions must cull the weaker head and an extinction row must be
    written — exercising both the real fitness/fingerprint construction block
    and the real decision function.
    """
    import orchestrator.baselines as baselines_mod
    import orchestrator.fingerprint as fingerprint_mod
    import orchestrator.lineages as lineages_mod
    from orchestrator.baselines import Baseline
    from orchestrator.evolve import GauntletResult
    from orchestrator.lineages import Lineage, write_lineages

    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    monkeypatch.setattr(lineages_mod, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_primitive_restore_pointer", lambda _v: None)
    # Keep fingerprint persistence off the real repo data dir.
    monkeypatch.setattr(
        fingerprint_mod,
        "default_fingerprints_path",
        lambda: tmp_path / "fingerprints.json",
    )
    # One registered baseline so the gauntlet engages (fitness-mode both).
    monkeypatch.setattr(
        baselines_mod,
        "load_baselines",
        lambda _p: {"sparring": Baseline(name="sparring", version="vBase")},
    )

    # sort_keys load order → line-2 (v8) scheduled gen 1, main (v0) gen 2.
    registry = {
        "main": Lineage(lineage_id="main", head_version="v0"),
        "line-2": Lineage(lineage_id="line-2", head_version="v8"),
    }
    write_lineages(tmp_path / "data" / "lineages.json", registry)

    args = _build_args(tmp_path, pool_size=1, generations=2)
    args.lineages = 2
    args.population_cap = 1
    args.diversity_threshold = 0.15
    args.fitness_mode = "both"

    # A fresh single winner imp every time the pool is (re)generated so each
    # generation promotes. The initial seed (no skip_mirror) and every
    # top-up (skip_mirror=True) both return one brand-new active imp.
    imp_counter = {"n": 0}

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        imp_counter["n"] += 1
        return [_make_imp(title=f"win-{imp_counter['n']}", rank=1)]

    # Every fitness eval passes so a promotion runs each generation.
    def all_pass_fitness(parent: str, imp: Improvement, **k: Any) -> Any:
        return _fitness(imp, bucket="pass", parent=parent)

    # Distinct new version per promotion (gen 1: line-2 v8→v100; gen 2:
    # main v0→v200). Regression never rolls back → heads keep the new ver.
    stack_apply = _ScriptedStackApply([(True, "v100"), (True, "v200")])
    regression = _ScriptedRegression([False, False])

    # Deterministic gauntlet: v100 (line-2 head) scores mean 0.50; v200
    # (main head) scores mean 0.55. Distance 0.05 < 0.15 → behaviorally
    # redundant; v100 is strictly less fit → dominated by v200.
    gauntlet_scores = {
        "v100": {"sparring": 0.50},
        "v200": {"sparring": 0.55},
    }

    def scripted_gauntlet(
        candidate: str, baselines: list[Any], **k: Any
    ) -> Any:
        per_baseline = gauntlet_scores[candidate]
        mean = sum(per_baseline.values()) / len(per_baseline)
        return GauntletResult(
            candidate=candidate,
            per_baseline=dict(per_baseline),
            mean_win_rate=mean,
            games_each=k.get("games_each", 5),
            record=[],
        )

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=all_pass_fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=regression,
        run_gauntlet_fn=scripted_gauntlet,
        decide_extinctions_fn=None,  # REAL decide_extinctions
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    rows = [
        json.loads(line)
        for line in (tmp_path / "evolve_results.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    # The real decide_extinctions, fed by the real fitness/fingerprint
    # construction block, culled the weaker redundant head (line-2 @ v100).
    ext_rows = [r for r in rows if r.get("phase") == "extinction"]
    assert len(ext_rows) == 1, ext_rows
    ext = ext_rows[0]
    assert ext["lineage_id"] == "line-2"
    assert ext["head_version"] == "v100"
    assert ext["dominated_by"] == "main"
    assert ext["outcome"] == "extinction"


# ---------------------------------------------------------------------------
# Phase EJ.2: null-diff screen (state bookkeeping + integration)
# ---------------------------------------------------------------------------


def test_per_item_state_roundtrips_consecutive_null_diffs(
    cli: ModuleType,
) -> None:
    """The new livelock field survives the to_json / from_json round-trip,
    and legacy pool files (without the field) default it to 0."""
    st = cli.PerItemState(
        status="active", retry_count=2, consecutive_null_diffs=1
    )
    data = st.to_json()
    assert data["consecutive_null_diffs"] == 1
    assert cli.PerItemState.from_json(data).consecutive_null_diffs == 1

    legacy = {
        "status": "active",
        "fitness_score": None,
        "retry_count": 0,
        "first_evaluated_against": None,
        "last_evaluated_against": None,
    }
    assert cli.PerItemState.from_json(legacy).consecutive_null_diffs == 0


def test_apply_fitness_outcome_resets_consecutive_null_diffs(
    cli: ModuleType,
) -> None:
    """A real (games-played) fitness eval resets the consecutive-null
    counter — the guard tracks CONSECUTIVE nulls only."""
    imp = _make_imp("imp-x")
    per_item_state = {0: cli.PerItemState(consecutive_null_diffs=1)}
    result = _fitness(imp, bucket="pass", parent="v0")
    cli._apply_fitness_outcome(per_item_state, 0, result)
    assert per_item_state[0].consecutive_null_diffs == 0


def test_handle_screen_null_diff_no_retry_bump_then_evicts(
    cli: ModuleType, tmp_path: Path
) -> None:
    """The screen handler emits a row, bumps the consecutive-null counter
    (never retry_count), keeps the imp active on the 1st null, and evicts
    on the 2nd consecutive."""
    imp = _make_imp("imp-x")
    per_item_state = {0: cli.PerItemState()}
    fitness_counts = {"pass": 0, "close": 0, "fail": 0, "crash": 0}
    results_path = tmp_path / "results.jsonl"

    snap = cli._handle_screen_null_diff(
        idx=0,
        imp=imp,
        parent_current="v0",
        generation_index=1,
        per_item_state=per_item_state,
        fitness_counts=fitness_counts,
        results_path=results_path,
        attempts=3,
        ast_equivalent=False,
    )
    st = per_item_state[0]
    assert st.retry_count == 0
    assert st.consecutive_null_diffs == 1
    assert st.status == cli._ACTIVE
    assert fitness_counts[cli._SCREEN_NULL_DIFF_BUCKET] == 1
    assert snap["outcome"] == "screen-null-diff"

    rows = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["phase"] == "fitness"
    assert rows[0]["outcome"] == "screen-null-diff"
    assert rows[0]["games"] == 0
    assert rows[0]["attempts"] == 3

    # Second consecutive null → evicted.
    cli._handle_screen_null_diff(
        idx=0,
        imp=imp,
        parent_current="v0",
        generation_index=2,
        per_item_state=per_item_state,
        fitness_counts=fitness_counts,
        results_path=results_path,
        attempts=3,
        ast_equivalent=True,
    )
    assert per_item_state[0].consecutive_null_diffs == 2
    assert per_item_state[0].status == cli._EVICTED
    assert per_item_state[0].retry_count == 0


def test_parse_null_diff_attempts_none_and_match(cli: ModuleType) -> None:
    """The attempt-count parser recovers ``after N attempt`` and returns
    None for absent/unparseable messages (the worker-path fallback)."""
    assert cli._parse_null_diff_attempts("no count here") is None
    assert cli._parse_null_diff_attempts(None) is None
    assert cli._parse_null_diff_attempts("") is None
    assert (
        cli._parse_null_diff_attempts("no change after 5 attempt(s)") == 5
    )


def test_handle_screen_null_diff_attempts_none_renders_fallback(
    cli: ModuleType, tmp_path: Path
) -> None:
    """When the attempt count is unknown (worker-path, message didn't parse)
    the row's ``attempts`` is None and the reason uses the 'retries'
    fallback wording instead of 'N attempt(s)'."""
    imp = _make_imp("imp-x")
    per_item_state = {0: cli.PerItemState()}
    fitness_counts = {"pass": 0, "close": 0, "fail": 0, "crash": 0}
    results_path = tmp_path / "results.jsonl"

    snap = cli._handle_screen_null_diff(
        idx=0,
        imp=imp,
        parent_current="v0",
        generation_index=1,
        per_item_state=per_item_state,
        fitness_counts=fitness_counts,
        results_path=results_path,
        attempts=None,
        ast_equivalent=None,
    )
    assert "retries" in snap["reason"]
    assert "attempt(s)" not in snap["reason"]

    rows = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[0]["attempts"] is None
    assert rows[0]["outcome"] == "screen-null-diff"


def test_screen_null_diff_integration_zero_games(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through the run_loop generation dispatch: --screen-null-diff
    ON + a dev_apply_fn that produces a null diff must yield a screen-null-diff
    fitness row and play ZERO games for that imp — exercising the real serial
    dispatch/state code, the flag threading (partial), and the row emission."""
    from orchestrator.evolve_dev_apply import DevApplyNullDiffError

    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(
        tmp_path, pool_size=1, generations=1, screen_null_diff=True
    )
    pool = _make_pool(1)

    seen = {"screen_null_diff": None}

    def null_dev_apply(
        version_dir: Any, imp: Improvement, *, screen_null_diff: bool = False
    ) -> None:
        # run_loop wraps this in functools.partial(screen_null_diff=True);
        # this asserts the flag actually reached the dev-apply callable.
        seen["screen_null_diff"] = screen_null_diff
        raise DevApplyNullDiffError(
            "sub-agent produced no semantic .py change after 3 attempt(s)",
            attempts=3,
            ast_equivalent=False,
        )

    games_played = {"n": 0}

    def driving_fitness(
        parent: str,
        imp: Improvement,
        *,
        dev_apply_fn: Any = None,
        **kwargs: Any,
    ) -> FitnessResult:
        # Mirror run_fitness_eval's contract: apply the imp FIRST (which
        # raises the null-diff error before any game), THEN play games. The
        # raise means run_batch is never reached → zero games.
        assert dev_apply_fn is not None
        dev_apply_fn(Path("cand_x"), imp)
        games_played["n"] += 1  # unreachable when the screen fires
        return _fitness(imp, bucket="pass", parent=parent)

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        return [] if k.get("skip_mirror") else pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=driving_fitness,
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no winners → stack-apply must not fire")
        ),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no promotion → regression must not fire")
        ),
        current_version_fn=lambda: "v0",
        dev_apply_fn=null_dev_apply,
    )
    assert rc == 0
    # The flag was threaded through run_loop into the dev-apply callable.
    assert seen["screen_null_diff"] is True
    # ZERO games played for the screened imp.
    assert games_played["n"] == 0

    rows = [
        json.loads(line)
        for line in args.results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    fitness_rows = [r for r in rows if r.get("phase") == "fitness"]
    assert len(fitness_rows) == 1
    assert fitness_rows[0]["outcome"] == "screen-null-diff"
    assert fitness_rows[0]["games"] == 0
    assert fitness_rows[0]["attempts"] == 3
    # No crash row was emitted.
    assert not any(r.get("outcome") == "crash" for r in rows)

    # retry_count NOT bumped; imp back to active (1st consecutive null).
    pool_state = json.loads(args.pool_path.read_text(encoding="utf-8"))
    item = pool_state["pool"][0]
    assert item["retry_count"] == 0
    assert item["consecutive_null_diffs"] == 1
    assert item["status"] == "active"


# ---------------------------------------------------------------------------
# Phase EJ.4: frozen-baseline panel floor
# ---------------------------------------------------------------------------


def _make_gauntlet(scores: dict[str, dict[str, float]]) -> Any:
    """Return an injectable run_gauntlet_fn scripted per-candidate.

    ``scores`` maps candidate version -> {baseline name: win rate}. A 0.0 in a
    candidate's vector is a "sweep loss" vs that anchor (the panel-floor trip).
    """
    from orchestrator.evolve import GauntletResult

    def _fn(candidate: str, baselines: list[Any], **k: Any) -> Any:
        per_baseline = scores[candidate]
        mean = (
            sum(per_baseline.values()) / len(per_baseline)
            if per_baseline
            else 0.0
        )
        return GauntletResult(
            candidate=candidate,
            per_baseline=dict(per_baseline),
            mean_win_rate=mean,
            games_each=k.get("games_each", 5),
            record=[],
        )

    return _fn


def _register_one_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch load_baselines so the gauntlet engages with one anchor."""
    import orchestrator.baselines as baselines_mod
    from orchestrator.baselines import Baseline

    monkeypatch.setattr(
        baselines_mod,
        "load_baselines",
        lambda _p: {"sparring": Baseline(name="sparring", version="vBase")},
    )


def test_panel_floor_flag_parses_and_defaults_off(cli: ModuleType) -> None:
    """--panel-floor parses to args.panel_floor; default is False."""
    assert cli.build_parser().parse_args([]).panel_floor is False
    assert (
        cli.build_parser().parse_args(["--panel-floor"]).panel_floor is True
    )


def test_panel_floor_sweep_loss_rolls_back_via_revert_path(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integration: --fitness-mode both + a registered baseline + a gauntlet
    sweep loss (0 wins vs the anchor) rolls back an otherwise regression-PASS
    promotion through the SAME revert machinery, and emits a ``panel-floor:``
    reason row."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    _register_one_baseline(monkeypatch)

    args = _build_args(tmp_path, pool_size=1, generations=1, no_commit=False)
    args.fitness_mode = "both"
    args.panel_floor = True

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return [_make_imp(title="win", rank=1)]

    def all_pass(parent: str, imp: Improvement, **k: Any) -> Any:
        return _fitness(imp, bucket="pass", parent=parent)

    stack_apply = _ScriptedStackApply([(True, "v1")])
    regression = _ScriptedRegression([False])  # regression itself PASSES

    def fake_commit(
        new_version: str, generation: int, titles: list[str], **k: Any
    ) -> tuple[bool, str | None]:
        return True, f"sha-{generation}"

    revert_calls: list[dict[str, Any]] = []

    def fake_revert(
        promote_sha: str, generation: int, reason: str, **k: Any
    ) -> bool:
        revert_calls.append(
            {"promote_sha": promote_sha, "generation": generation,
             "reason": reason}
        )
        return True

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=all_pass,
        stack_apply_fn=stack_apply,
        run_regression_fn=regression,
        run_gauntlet_fn=_make_gauntlet({"v1": {"sparring": 0.0}}),
        commit_fn=fake_commit,
        revert_fn=fake_revert,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    # Rolled back through the SAME revert path, with a panel-floor reason.
    assert len(revert_calls) == 1
    assert revert_calls[0]["promote_sha"] == "sha-1"
    assert revert_calls[0]["reason"].startswith("panel-floor:")

    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["generations_promoted"] == 0
    assert state["parent_current"] == "v0"

    pool_state = json.loads(args.pool_path.read_text(encoding="utf-8"))
    statuses = [item["status"] for item in pool_state["pool"]]
    assert statuses.count("regression-rollback") == 1

    rows = [
        json.loads(line)
        for line in args.results_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    # The regression row is honest — regression itself did NOT roll back.
    reg_rows = [r for r in rows if r.get("phase") == "regression"]
    assert len(reg_rows) == 1
    assert reg_rows[0]["rolled_back"] is False
    # A distinct panel-floor row carries the panel-floor: reason prefix.
    pf_rows = [r for r in rows if r.get("phase") == "panel_floor"]
    assert len(pf_rows) == 1
    assert pf_rows[0]["outcome"] == "panel-floor-rollback"
    assert pf_rows[0]["reason"].startswith("panel-floor:")
    assert pf_rows[0]["new_parent"] == "v1"
    assert pf_rows[0]["prior_parent"] == "v0"


def test_panel_floor_no_sweep_promotion_stands(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every anchor won at least once (no 0.0) → the panel floor is inert;
    the regression-pass promotion stands, matching the regression-only path."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    monkeypatch.setattr(cli, "_primitive_restore_pointer", lambda _v: None)
    _register_one_baseline(monkeypatch)

    args = _build_args(tmp_path, pool_size=1, generations=1, no_commit=True)
    args.fitness_mode = "both"
    args.panel_floor = True

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return [_make_imp(title="win", rank=1)]

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=lambda p, imp, **k: _fitness(imp, bucket="pass",
                                                    parent=p),
        stack_apply_fn=_ScriptedStackApply([(True, "v1")]),
        run_regression_fn=_ScriptedRegression([False]),
        run_gauntlet_fn=_make_gauntlet({"v1": {"sparring": 0.6}}),
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["generations_promoted"] == 1
    assert state["parent_current"] == "v1"
    rows = [
        json.loads(line)
        for line in args.results_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert not any(r.get("phase") == "panel_floor" for r in rows)


def test_panel_floor_gauntlet_crash_is_fail_open(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gauntlet crash must NEVER block an already-committed promotion:
    panel_floor_swept stays False (fail-open) and the promotion stands."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    monkeypatch.setattr(cli, "_primitive_restore_pointer", lambda _v: None)
    _register_one_baseline(monkeypatch)

    args = _build_args(tmp_path, pool_size=1, generations=1, no_commit=True)
    args.fitness_mode = "both"
    args.panel_floor = True

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return [_make_imp(title="win", rank=1)]

    def crashing_gauntlet(*a: Any, **k: Any) -> Any:
        raise RuntimeError("gauntlet subprocess OOM")

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=lambda p, imp, **k: _fitness(imp, bucket="pass",
                                                    parent=p),
        stack_apply_fn=_ScriptedStackApply([(True, "v1")]),
        run_regression_fn=_ScriptedRegression([False]),
        run_gauntlet_fn=crashing_gauntlet,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["generations_promoted"] == 1
    assert state["parent_current"] == "v1"
    rows = [
        json.loads(line)
        for line in args.results_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert not any(r.get("phase") == "panel_floor" for r in rows)


def test_panel_floor_inert_when_fitness_mode_parent_logs_warning(
    cli: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """--panel-floor armed with --fitness-mode=parent: the gauntlet never runs
    (floor inert), the promotion stands, AND a startup WARNING fires."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    monkeypatch.setattr(cli, "_primitive_restore_pointer", lambda _v: None)

    args = _build_args(tmp_path, pool_size=1, generations=1, no_commit=True)
    args.fitness_mode = "parent"
    args.panel_floor = True

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return [_make_imp(title="win", rank=1)]

    with caplog.at_level(logging.WARNING, logger="evolve"):
        rc = cli.run_loop(
            args,
            generate_pool_fn=refresh,
            run_fitness_fn=lambda p, imp, **k: _fitness(imp, bucket="pass",
                                                        parent=p),
            stack_apply_fn=_ScriptedStackApply([(True, "v1")]),
            run_regression_fn=_ScriptedRegression([False]),
            run_gauntlet_fn=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("gauntlet must not run in parent mode")
            ),
            current_version_fn=lambda: "v0",
        )
    assert rc == 0

    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["generations_promoted"] == 1
    assert state["parent_current"] == "v1"
    assert any(
        "ARMED but INERT" in rec.getMessage() for rec in caplog.records
    ), [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize("regression_rolls_back", [True, False])
@pytest.mark.parametrize("sweep_present", [True, False])
def test_panel_floor_off_is_byte_identical(
    cli: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    regression_rolls_back: bool,
    sweep_present: bool,
) -> None:
    """With --panel-floor OFF, the rollback decision is EXACTLY
    regression_result.rolled_back across the full (rolled_back) x (sweep) grid
    — a sweep loss has NO effect and no panel-floor row is emitted."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    monkeypatch.setattr(cli, "_primitive_restore_pointer", lambda _v: None)
    _register_one_baseline(monkeypatch)

    args = _build_args(tmp_path, pool_size=1, generations=1, no_commit=True)
    args.fitness_mode = "both"  # gauntlet runs, but panel_floor is OFF
    # NB: args.panel_floor deliberately left UNSET → getattr default False.

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return [_make_imp(title="win", rank=1)]

    score = 0.0 if sweep_present else 0.6
    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=lambda p, imp, **k: _fitness(imp, bucket="pass",
                                                    parent=p),
        stack_apply_fn=_ScriptedStackApply([(True, "v1")]),
        run_regression_fn=_ScriptedRegression([regression_rolls_back]),
        run_gauntlet_fn=_make_gauntlet({"v1": {"sparring": score}}),
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in args.results_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    # The ONLY thing that decides rollback is regression_result.rolled_back.
    if regression_rolls_back:
        assert state["generations_promoted"] == 0
        assert state["parent_current"] == "v0"
    else:
        assert state["generations_promoted"] == 1
        assert state["parent_current"] == "v1"
    # A sweep loss never emits a panel-floor row when the flag is off.
    assert not any(r.get("phase") == "panel_floor" for r in rows)


def test_panel_floor_cli_passthrough_honored_both_directions(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production caller: run_loop honors args.panel_floor. Same fake gauntlet
    (a 0.0 sweep) rolls back when the flag is ON and does NOT when it is OFF."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    monkeypatch.setattr(cli, "_primitive_restore_pointer", lambda _v: None)
    _register_one_baseline(monkeypatch)

    def _run(dir_name: str, panel_floor: bool) -> dict[str, Any]:
        d = tmp_path / dir_name
        d.mkdir()
        args = _build_args(d, pool_size=1, generations=1, no_commit=True)
        args.fitness_mode = "both"
        args.panel_floor = panel_floor

        def refresh(*a: Any, **k: Any) -> list[Improvement]:
            if k.get("skip_mirror"):
                return []
            return [_make_imp(title="win", rank=1)]

        rc = cli.run_loop(
            args,
            generate_pool_fn=refresh,
            run_fitness_fn=lambda p, imp, **k: _fitness(imp, bucket="pass",
                                                        parent=p),
            stack_apply_fn=_ScriptedStackApply([(True, "v1")]),
            run_regression_fn=_ScriptedRegression([False]),
            run_gauntlet_fn=_make_gauntlet({"v1": {"sparring": 0.0}}),
            current_version_fn=lambda: "v0",
        )
        assert rc == 0
        return json.loads(args.state_path.read_text(encoding="utf-8"))

    on_state = _run("on", panel_floor=True)
    off_state = _run("off", panel_floor=False)

    # Flag ON → the sweep loss rolls the promotion back.
    assert on_state["generations_promoted"] == 0
    assert on_state["parent_current"] == "v0"
    # Flag OFF → the identical sweep is ignored; promotion stands.
    assert off_state["generations_promoted"] == 1
    assert off_state["parent_current"] == "v1"


def test_panel_floor_precedence_regression_wins_reason(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When BOTH regression rolls back AND the panel floor sweeps, the revert
    reason is the regression reason (precedence), label stays 'rollback', but
    the panel-floor audit row is still emitted for visibility."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    _register_one_baseline(monkeypatch)

    args = _build_args(tmp_path, pool_size=1, generations=1, no_commit=False)
    args.fitness_mode = "both"
    args.panel_floor = True

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return [_make_imp(title="win", rank=1)]

    def fake_commit(
        new_version: str, generation: int, titles: list[str], **k: Any
    ) -> tuple[bool, str | None]:
        return True, f"sha-{generation}"

    revert_calls: list[dict[str, Any]] = []

    def fake_revert(
        promote_sha: str, generation: int, reason: str, **k: Any
    ) -> bool:
        revert_calls.append({"reason": reason})
        return True

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=lambda p, imp, **k: _fitness(imp, bucket="pass",
                                                    parent=p),
        stack_apply_fn=_ScriptedStackApply([(True, "v1")]),
        run_regression_fn=_ScriptedRegression([True]),  # regression ALSO rolls
        run_gauntlet_fn=_make_gauntlet({"v1": {"sparring": 0.0}}),
        commit_fn=fake_commit,
        revert_fn=fake_revert,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    # Regression reason wins precedence (not the panel-floor: prefix).
    assert len(revert_calls) == 1
    assert not revert_calls[0]["reason"].startswith("panel-floor:")
    # The rollback actually happened: promotion pulled, parent restored.
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["generations_promoted"] == 0
    assert state["parent_current"] == "v0"
    # The panel-floor audit row is still emitted for visibility.
    rows = [
        json.loads(line)
        for line in args.results_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert any(r.get("phase") == "panel_floor" for r in rows)


def test_panel_floor_enforces_on_regression_crash(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A regression-eval CRASH must NOT strand a swept version as parent. The
    sweep-loss signal is computed in the gauntlet BEFORE the (independent)
    regression eval, so the floor still rolls back even when run_regression_fn
    raises — the mandatory-backstop guarantee cannot depend on the regression
    subsystem staying up."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    _register_one_baseline(monkeypatch)

    args = _build_args(tmp_path, pool_size=1, generations=1, no_commit=False)
    args.fitness_mode = "both"
    args.panel_floor = True

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return [_make_imp(title="win", rank=1)]

    def fake_commit(
        new_version: str, generation: int, titles: list[str], **k: Any
    ) -> tuple[bool, str | None]:
        return True, f"sha-{generation}"

    revert_calls: list[dict[str, Any]] = []

    def fake_revert(
        promote_sha: str, generation: int, reason: str, **k: Any
    ) -> bool:
        revert_calls.append(
            {"promote_sha": promote_sha, "reason": reason}
        )
        return True

    def crashing_regression(*a: Any, **k: Any) -> Any:
        raise RuntimeError("SC2 hang — regression watchdog kill")

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=lambda p, imp, **k: _fitness(imp, bucket="pass",
                                                    parent=p),
        stack_apply_fn=_ScriptedStackApply([(True, "v1")]),
        run_regression_fn=crashing_regression,
        run_gauntlet_fn=_make_gauntlet({"v1": {"sparring": 0.0}}),
        commit_fn=fake_commit,
        revert_fn=fake_revert,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    # The panel floor rolled back THROUGH THE SAME revert path despite the
    # regression crash — reverted with a panel-floor reason.
    assert len(revert_calls) == 1
    assert revert_calls[0]["promote_sha"] == "sha-1"
    assert revert_calls[0]["reason"].startswith("panel-floor:")

    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["generations_promoted"] == 0
    assert state["parent_current"] == "v0"

    pool_state = json.loads(args.pool_path.read_text(encoding="utf-8"))
    statuses = [item["status"] for item in pool_state["pool"]]
    assert statuses.count("regression-rollback") == 1

    rows = [
        json.loads(line)
        for line in args.results_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    # The regression crash row AND the panel-floor rollback row both land.
    assert any(
        r.get("phase") == "regression" and r.get("outcome") == "crash"
        for r in rows
    )
    pf_rows = [r for r in rows if r.get("phase") == "panel_floor"]
    assert len(pf_rows) == 1
    assert pf_rows[0]["reason"].startswith("panel-floor:")


def test_panel_floor_state_resets_across_generations(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the per-generation reset of panel_floor_swept:
    generation 1 sweeps (rolls back), generation 2 is clean and its promotion
    MUST stand. If the swept flag ever leaked across the loop boundary, gen 2
    would be wrongly rolled back and emit a second panel-floor row."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    monkeypatch.setattr(cli, "_primitive_restore_pointer", lambda _v: None)
    _register_one_baseline(monkeypatch)

    args = _build_args(tmp_path, pool_size=1, generations=2, no_commit=True)
    args.fitness_mode = "both"
    args.panel_floor = True

    imp_counter = {"n": 0}

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        imp_counter["n"] += 1
        return [_make_imp(title=f"win-{imp_counter['n']}", rank=1)]

    # Regression PASSES both generations — gen-1's rollback comes purely from
    # the panel floor (v1 sweep loss), never from regression.
    stack_apply = _ScriptedStackApply([(True, "v1"), (True, "v2")])
    regression = _ScriptedRegression([False, False])
    # v1 sweeps (0 wins); v2 is clean (0.6 vs the anchor).
    gauntlet = _make_gauntlet(
        {"v1": {"sparring": 0.0}, "v2": {"sparring": 0.6}}
    )

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=lambda p, imp, **k: _fitness(imp, bucket="pass",
                                                    parent=p),
        stack_apply_fn=stack_apply,
        run_regression_fn=regression,
        run_gauntlet_fn=gauntlet,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    # Gen 1 rolled back to v0; gen 2 promoted v2 and it STANDS.
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["generations_promoted"] == 1
    assert state["parent_current"] == "v2"

    rows = [
        json.loads(line)
        for line in args.results_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    # Exactly ONE panel-floor row (gen 1). A leaked flag would emit a gen-2 row.
    pf_rows = [r for r in rows if r.get("phase") == "panel_floor"]
    assert len(pf_rows) == 1
    assert pf_rows[0]["generation"] == 1
    assert pf_rows[0]["new_parent"] == "v1"


def test_panel_floor_inert_when_both_mode_but_zero_baselines(
    cli: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The OTHER inert branch of the arm-check predicate: --fitness-mode both
    with ZERO registered baselines. The gauntlet never engages (floor inert),
    the promotion stands, and the armed-but-inert WARNING fires with its
    'no baselines' sub-clause."""
    import orchestrator.baselines as baselines_mod

    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    monkeypatch.setattr(cli, "_primitive_restore_pointer", lambda _v: None)
    # Zero baselines registered → gauntlet cannot engage.
    monkeypatch.setattr(baselines_mod, "load_baselines", lambda _p: {})

    args = _build_args(tmp_path, pool_size=1, generations=1, no_commit=True)
    args.fitness_mode = "both"
    args.panel_floor = True

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return [_make_imp(title="win", rank=1)]

    with caplog.at_level(logging.WARNING, logger="evolve"):
        rc = cli.run_loop(
            args,
            generate_pool_fn=refresh,
            run_fitness_fn=lambda p, imp, **k: _fitness(imp, bucket="pass",
                                                        parent=p),
            stack_apply_fn=_ScriptedStackApply([(True, "v1")]),
            run_regression_fn=_ScriptedRegression([False]),
            run_gauntlet_fn=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("gauntlet must not run with zero baselines")
            ),
            current_version_fn=lambda: "v0",
        )
    assert rc == 0

    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert state["generations_promoted"] == 1
    assert state["parent_current"] == "v1"

    messages = [rec.getMessage() for rec in caplog.records]
    assert any("ARMED but INERT" in m for m in messages), messages
    assert any("no frozen baselines" in m for m in messages), messages

    rows = [
        json.loads(line)
        for line in args.results_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert not any(r.get("phase") == "panel_floor" for r in rows)


# ---------------------------------------------------------------------------
# Phase EJ.5: refresh-time proposal dedup (--refresh-dedup)
# ---------------------------------------------------------------------------


def test_refresh_dedup_flag_parses_and_defaults_off(cli: ModuleType) -> None:
    """--refresh-dedup parses to args.refresh_dedup; default is False."""
    assert cli.build_parser().parse_args([]).refresh_dedup is False
    assert (
        cli.build_parser().parse_args(["--refresh-dedup"]).refresh_dedup
        is True
    )


def test_dedup_threshold_is_single_source_constant(cli: ModuleType) -> None:
    """The 0.85 threshold is ONE named constant; the helper's default IS it."""
    import inspect

    assert cli._REFRESH_DEDUP_THRESHOLD == 0.85
    default = inspect.signature(cli._dedup_fresh_imps).parameters[
        "threshold"
    ].default
    assert default is cli._REFRESH_DEDUP_THRESHOLD


def test_dedup_uses_shared_normalize_prior_title(
    cli: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One source of truth: EJ.5 dedup normalizes via the SAME object EJ.1's
    priors filter uses — asserted by identity AND by a spy proving usage."""
    from orchestrator.evolve import normalize_prior_title as canonical

    # Identity: scripts/evolve.py binds the canonical normalizer, not a copy.
    assert cli.normalize_prior_title is canonical

    # Usage: the helper actually calls that module binding (not a private one).
    calls: list[object] = []
    real = cli.normalize_prior_title

    def spy(title: object) -> str:
        calls.append(title)
        return cast(str, real(title))

    monkeypatch.setattr(cli, "normalize_prior_title", spy)
    cli._dedup_fresh_imps(
        [_make_imp(title="Alpha", rank=10)],
        [_make_imp(title="Beta", rank=1)],
        set(),
    )
    assert calls, "helper never invoked the module's normalize_prior_title"


def test_dedup_drops_exact_promoted_title_normalized(
    cli: ModuleType,
) -> None:
    """(a) A fresh title that normalizes equal to an in-run promoted title is
    dropped — even across casing/punctuation drift."""
    fresh = [_make_imp(title="Splash Readiness!", rank=10)]
    survivors, drops = cli._dedup_fresh_imps(
        fresh, [], {"splash-readiness"}
    )
    assert survivors == []
    assert len(drops) == 1
    assert drops[0]["rule"] == "promoted"
    assert drops[0]["dropped_title"] == "Splash Readiness!"
    assert drops[0]["matched_title"] == "splash-readiness"
    assert drops[0]["ratio"] == 1.0


def test_dedup_drops_near_duplicate_pool_title(cli: ModuleType) -> None:
    """(b) A fresh title >= threshold-similar to an EXISTING pool imp drops."""
    pool = [_make_imp(title="Cannon Rush Opener", rank=1)]
    fresh = [_make_imp(title="Canon Rush Opener", rank=10)]  # 1-char typo
    survivors, drops = cli._dedup_fresh_imps(fresh, pool, set())
    assert survivors == []
    assert drops[0]["rule"] == "pool"
    assert drops[0]["matched_title"] == "Cannon Rush Opener"
    assert drops[0]["ratio"] >= cli._REFRESH_DEDUP_THRESHOLD
    assert drops[0]["ratio"] < 1.0  # genuinely near, not an exact repeat


def test_dedup_subthreshold_title_survives(cli: ModuleType) -> None:
    """A fresh title below the ratio floor vs every pool imp survives."""
    pool = [_make_imp(title="Zerg Rush Defense", rank=1)]
    fresh = [_make_imp(title="Oracle Phoenix Harass", rank=10)]
    survivors, drops = cli._dedup_fresh_imps(fresh, pool, set())
    assert [s.title for s in survivors] == ["Oracle Phoenix Harass"]
    assert drops == []


def test_dedup_intrabatch_keeps_first_drops_later(cli: ModuleType) -> None:
    """(c) Intra-batch: the first occurrence is kept, later near-dups drop."""
    fresh = [
        _make_imp(title="Cannon Rush Opener", rank=10),
        _make_imp(title="Cannon Rush Opener", rank=11),  # exact dup
        _make_imp(title="Oracle Harass", rank=12),  # unique
    ]
    survivors, drops = cli._dedup_fresh_imps(fresh, [], set())
    assert [s.title for s in survivors] == [
        "Cannon Rush Opener",
        "Oracle Harass",
    ]
    assert len(drops) == 1
    assert drops[0]["rule"] == "batch"
    assert drops[0]["dropped_title"] == "Cannon Rush Opener"
    assert drops[0]["matched_title"] == "Cannon Rush Opener"


def test_dedup_audit_row_carries_titles_and_ratio(cli: ModuleType) -> None:
    """The drop payload records the dropped title, matched title, and ratio —
    both as structured fields and woven into the human-readable reason."""
    pool = [_make_imp(title="Blink Stalker Micro", rank=1)]
    fresh = [_make_imp(title="Blink Stalker Micro", rank=10)]
    _, drops = cli._dedup_fresh_imps(fresh, pool, set())
    row = drops[0]
    assert set(row) >= {
        "dropped_title",
        "matched_title",
        "ratio",
        "rule",
        "reason",
    }
    assert row["dropped_title"] in row["reason"]
    assert row["matched_title"] in row["reason"]
    assert str(row["ratio"]) in row["reason"]


def test_dedup_keeps_empty_normalized_title(cli: ModuleType) -> None:
    """Safe direction: a fresh imp whose title normalizes to "" (all
    punctuation, or the empty string) is KEPT — even when a pool imp AND a
    promoted title also normalize to "" — with no drop row and no crash. Pins
    the ``if not norm:`` guard that a refactor could silently remove."""
    fresh = [
        _make_imp(title="!!!", rank=10),  # normalizes to ""
        _make_imp(title="", rank=11),  # empty string → ""
    ]
    survivors, drops = cli._dedup_fresh_imps(
        fresh,
        [_make_imp(title="###", rank=1)],  # pool title also norms to ""
        {"@@@"},  # promoted title also norms to ""
    )
    assert [s.title for s in survivors] == ["!!!", ""]
    assert drops == []


def test_dedup_threshold_boundary_straddles_default(cli: ModuleType) -> None:
    """Boundary pins the exact 0.85 default (not 0.80 / 0.90): a ~0.857 pair
    DROPS while a ~0.837 pair SURVIVES under the default threshold."""
    norm = cli.normalize_prior_title
    just_above = difflib.SequenceMatcher(
        None, norm("Chrono Boost Nexus First"), norm("Chrono Boost Nexus")
    ).ratio()
    just_below = difflib.SequenceMatcher(
        None, norm("Oracle Stasis Ward Harass"), norm("Oracle Stasis Ward")
    ).ratio()
    # Straddle guard: if these strings ever drift, this fails loudly.
    assert just_below < cli._REFRESH_DEDUP_THRESHOLD <= just_above

    _, drops_above = cli._dedup_fresh_imps(
        [_make_imp(title="Chrono Boost Nexus First", rank=10)],
        [_make_imp(title="Chrono Boost Nexus", rank=1)],
        set(),
    )
    assert len(drops_above) == 1
    assert drops_above[0]["rule"] == "pool"

    survivors_below, drops_below = cli._dedup_fresh_imps(
        [_make_imp(title="Oracle Stasis Ward Harass", rank=10)],
        [_make_imp(title="Oracle Stasis Ward", rank=1)],
        set(),
    )
    assert [s.title for s in survivors_below] == ["Oracle Stasis Ward Harass"]
    assert drops_below == []


def test_dedup_threshold_is_inclusive_at_exact_boundary(
    cli: ModuleType,
) -> None:
    """>= is inclusive: a pair whose ratio EQUALS the threshold drops; nudging
    the threshold a hair above that exact ratio flips it to survive — pinning
    the boundary operator as ``>=`` rather than ``>``."""
    norm = cli.normalize_prior_title
    a, b = "Chrono Boost Nexus First", "Chrono Boost Nexus"
    exact = difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()

    # threshold == ratio → dropped (inclusive >=).
    _, drops_eq = cli._dedup_fresh_imps(
        [_make_imp(title=a, rank=10)],
        [_make_imp(title=b, rank=1)],
        set(),
        threshold=exact,
    )
    assert len(drops_eq) == 1

    # threshold a hair above the ratio → survives (a strict > would still drop).
    survivors_gt, drops_gt = cli._dedup_fresh_imps(
        [_make_imp(title=a, rank=10)],
        [_make_imp(title=b, rank=1)],
        set(),
        threshold=exact + 1e-9,
    )
    assert [s.title for s in survivors_gt] == [a]
    assert drops_gt == []


def _drive_refresh_dedup(
    cli: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    dedup: bool,
) -> tuple[dict[str, int], list[dict[str, Any]], dict[str, Any]]:
    """One generation (all fitness-fail → refresh) with a duplicate-heavy
    fresh batch; returns (generate-call counts, results rows, pool state).

    ``generations=1`` runs exactly one fitness round then stops on the
    generation cap, so ScriptedFitness supplies exactly the 4 initial-pool
    evaluations and the refresh survivors are never re-evaluated.
    """
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=4, generations=1, no_commit=True)
    args.refresh_dedup = dedup

    initial_pool = [
        _make_imp(title="Zerg Rush Defense", rank=1),
        _make_imp(title="Blink Micro", rank=2),
        _make_imp(title="Warp Prism Drop", rank=3),
        _make_imp(title="Forge Fast Expand", rank=4),
    ]
    fresh_batch = [
        _make_imp(title="Zerg Rush Defense!", rank=10),  # dup of pool (b)
        _make_imp(title="Blink Micro", rank=11),  # dup of pool (b)
        _make_imp(title="Cannon Rush Opener", rank=12),  # unique survivor
        _make_imp(title="Cannon Rush Opener", rank=13),  # intra-batch dup (c)
        _make_imp(title="Oracle Harass", rank=14),  # unique survivor
    ]
    fitness = _ScriptedFitness(["fail", "fail", "fail", "fail"])

    gen_calls = {"initial": 0, "refresh": 0}

    def generate(parent: str, **kwargs: Any) -> list[Improvement]:
        if kwargs.get("skip_mirror"):
            gen_calls["refresh"] += 1
            return list(fresh_batch)
        gen_calls["initial"] += 1
        return list(initial_pool)

    rc = cli.run_loop(
        args,
        generate_pool_fn=generate,
        run_fitness_fn=fitness,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    rows = [
        json.loads(line)
        for line in args.results_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    pool_state = json.loads(args.pool_path.read_text(encoding="utf-8"))
    return gen_calls, rows, pool_state


def test_refresh_dedup_on_drops_duplicates_pool_shorter(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production caller, flag ON: duplicates are dropped at refresh, only
    survivors append, and pool_dedup rows are emitted."""
    gen_calls, rows, pool_state = _drive_refresh_dedup(
        cli, tmp_path, monkeypatch, dedup=True
    )

    dedup_rows = [r for r in rows if r.get("phase") == "pool_dedup"]
    # 2 pool-dup drops + 1 intra-batch drop = 3 audit rows.
    assert len(dedup_rows) == 3
    # Phase name is underscored; the multi-word outcome is hyphenated.
    assert all(r["outcome"] == "pool-dedup" for r in dedup_rows)
    assert all(r["generation"] == 1 for r in dedup_rows)
    assert sorted(r["rule"] for r in dedup_rows) == ["batch", "pool", "pool"]

    titles = [item["title"] for item in pool_state["pool"]]
    # 4 original (now evicted) + 2 survivors = 6; only ONE "Cannon Rush Opener".
    assert len(pool_state["pool"]) == 6
    assert "Oracle Harass" in titles
    assert titles.count("Cannon Rush Opener") == 1
    assert "Zerg Rush Defense!" not in titles  # the dropped near-dup


def test_refresh_dedup_accept_short_no_topup(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accept-short: after dedup the pool is simply shorter — generate_pool is
    NOT re-called to top up, and no exception is raised."""
    gen_calls, _rows, _pool = _drive_refresh_dedup(
        cli, tmp_path, monkeypatch, dedup=True
    )
    assert gen_calls["initial"] == 1
    assert gen_calls["refresh"] == 1  # exactly one refresh call; no top-up


def test_refresh_dedup_off_is_byte_identical(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flag OFF: the duplicate-heavy batch appends verbatim, zero drop rows —
    byte-identical to today's refresh append."""
    gen_calls, rows, pool_state = _drive_refresh_dedup(
        cli, tmp_path, monkeypatch, dedup=False
    )
    assert not any(r.get("phase") == "pool_dedup" for r in rows)
    assert gen_calls["refresh"] == 1

    titles = [item["title"] for item in pool_state["pool"]]
    # 4 original + 5 fresh (all appended, dups and all) = 9.
    assert len(pool_state["pool"]) == 9
    assert "Zerg Rush Defense!" in titles
    assert titles.count("Cannon Rush Opener") == 2


def test_refresh_dedup_drops_in_run_promoted_title(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production caller, flag ON: a fresh imp re-proposing THIS run's promoted
    (regression-survived) title is dropped via the 'promoted' rule."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=1, generations=1, no_commit=True)
    args.refresh_dedup = True

    initial_pool = [_make_imp(title="Splash Readiness", rank=1)]
    fresh_batch = [
        _make_imp(title="Splash-Readiness!", rank=10),  # re-proposal → drop
        _make_imp(title="Oracle Harass", rank=11),  # unique → survive
    ]

    def generate(parent: str, **kwargs: Any) -> list[Improvement]:
        if kwargs.get("skip_mirror"):
            return list(fresh_batch)
        return list(initial_pool)

    rc = cli.run_loop(
        args,
        generate_pool_fn=generate,
        run_fitness_fn=_ScriptedFitness(["pass"]),
        stack_apply_fn=_ScriptedStackApply([(True, "v1")]),
        run_regression_fn=_ScriptedRegression([False]),  # survives → promoted
        current_version_fn=lambda: "v0",
    )
    assert rc == 0

    rows = [
        json.loads(line)
        for line in args.results_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    dedup_rows = [r for r in rows if r.get("phase") == "pool_dedup"]
    assert len(dedup_rows) == 1
    assert dedup_rows[0]["rule"] == "promoted"
    assert dedup_rows[0]["dropped_title"] == "Splash-Readiness!"
    assert dedup_rows[0]["matched_title"] == "Splash Readiness"

    pool_state = json.loads(args.pool_path.read_text(encoding="utf-8"))
    titles = [item["title"] for item in pool_state["pool"]]
    assert "Oracle Harass" in titles
    assert "Splash-Readiness!" not in titles


# ---------------------------------------------------------------------------
# Phase EJ Step EJ.6 — budget-aware final-generation fit (--budget-fit)
# ---------------------------------------------------------------------------


def test_budget_fit_flag_parses_and_defaults_off(cli: ModuleType) -> None:
    """--budget-fit parses to args.budget_fit; default is False."""
    assert cli.build_parser().parse_args([]).budget_fit is False
    assert cli.build_parser().parse_args(["--budget-fit"]).budget_fit is True


def test_budget_fit_active_keeps_rank_prefix(cli: ModuleType) -> None:
    """The kept prefix is the lowest-RANK imps, NOT the lowest index — the pool
    is not index-ordered once EJ.5 appends higher indexes."""
    # Ranks deliberately anti-correlated with index.
    pool = [
        _make_imp(title="d", rank=4),
        _make_imp(title="b", rank=2),
        _make_imp(title="a", rank=1),
        _make_imp(title="c", rank=3),
    ]
    # per_eval=100, remaining=350, reserve=100 (parent, no gauntlet):
    # fit = floor((350-100)/100) = floor(2.5) = 2 → keep the 2 best ranks.
    kept, dropped, reserve = cli._budget_fit_active(
        [0, 1, 2, 3],
        pool,
        per_eval_s=100.0,
        remaining_s=350.0,
        games_per_eval=5,
        gauntlet_baselines=0,
    )
    # rank 1 is idx 2, rank 2 is idx 1 — kept in ascending-rank order.
    assert kept == [2, 1]
    assert dropped == 2
    assert reserve == 100.0


def test_budget_fit_active_never_trims_below_one(cli: ModuleType) -> None:
    """A tiny remaining budget clamps fit_count to 1, never 0 — a generation
    always runs its single best imp."""
    pool = _make_pool(3)  # idx0=rank1 .. idx2=rank3
    kept, dropped, reserve = cli._budget_fit_active(
        [0, 1, 2],
        pool,
        per_eval_s=100.0,
        remaining_s=10.0,  # raw_fit = floor((10-100)/100) = -1 → clamp to 1
        games_per_eval=5,
        gauntlet_baselines=0,
    )
    assert kept == [0]  # only the rank-1 best survives
    assert dropped == 2


def test_budget_fit_active_ample_budget_keeps_all(cli: ModuleType) -> None:
    """When the budget comfortably fits every imp, nothing is trimmed."""
    pool = _make_pool(3)
    kept, dropped, _reserve = cli._budget_fit_active(
        [0, 1, 2],
        pool,
        per_eval_s=100.0,
        remaining_s=10_000.0,
        games_per_eval=5,
        gauntlet_baselines=0,
    )
    assert sorted(kept) == [0, 1, 2]
    assert dropped == 0


def test_budget_fit_active_reserve_accounts_for_regression_and_gauntlet(
    cli: ModuleType,
) -> None:
    """The reserve holds back one regression eval always, plus the baseline
    gauntlet's games when a gauntlet will run — so the same budget fits fewer
    imps in baseline/both mode than in parent mode."""
    pool = _make_pool(5)
    # parent mode: reserve = per_eval = 100. fit = floor((650-100)/100)=5.
    kept_parent, dropped_parent, reserve_parent = cli._budget_fit_active(
        [0, 1, 2, 3, 4],
        pool,
        per_eval_s=100.0,
        remaining_s=650.0,
        games_per_eval=5,
        gauntlet_baselines=0,
    )
    assert reserve_parent == 100.0
    assert len(kept_parent) == 5
    assert dropped_parent == 0

    # baseline/both with 2 anchors: per_game = 100/5 = 20; gauntlet reserve =
    # 20*5*2 = 200; total reserve = 100 + 200 = 300.
    # fit = floor((650-300)/100) = floor(3.5) = 3.
    kept_g, dropped_g, reserve_g = cli._budget_fit_active(
        [0, 1, 2, 3, 4],
        pool,
        per_eval_s=100.0,
        remaining_s=650.0,
        games_per_eval=5,
        gauntlet_baselines=2,
    )
    assert reserve_g == 300.0
    assert len(kept_g) == 3
    assert dropped_g == 2
    # The gauntlet reserve costs at least one imp vs the parent-mode fit.
    assert len(kept_g) < len(kept_parent)


def _drive_eval_paced(
    cli: ModuleType,
    args: argparse.Namespace,
    *,
    pool: list[Improvement],
    big: float = 600.0,
) -> tuple[int, int, dict[str, Any], list[dict[str, Any]], str]:
    """Drive run_loop with a clock whose value == (# fitness evals) * ``big``.

    Ties wall-clock to fitness progress so budget-fit arithmetic is exact and
    immune to incidental clock reads (the clock is a pure function of the eval
    counter — extra reads never perturb it). Every imp scores 'close', which
    keeps it active across generations without ever promoting (so stack-apply
    and regression must never fire). Caller sets args.hours / args.budget_fit /
    args.generations before calling.

    Returns ``(rc, fitness_calls, state_dict, result_rows, runlog_text)``.
    """
    counter = {"evals": 0}

    def clock() -> float:
        return counter["evals"] * big

    def fitness(parent: str, imp: Improvement, **_k: Any) -> FitnessResult:
        counter["evals"] += 1
        return _fitness(imp, bucket="close", parent=parent)

    def refresh(*_a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return list(pool)

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("bucket=close never promotes; stack-apply fired")
        ),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no promotion; regression must not fire")
        ),
        current_version_fn=lambda: "v0",
        time_fn=clock,
    )
    state = json.loads(args.state_path.read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in args.results_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    runlog = args.run_log.read_text(encoding="utf-8")
    return rc, counter["evals"], state, rows, runlog


def test_budget_fit_generation_one_is_noop(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generation 1 has no observed per-eval timing yet → --budget-fit is a
    no-op: the full pool dispatches and no budget_fit row is written."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=4, generations=1, hours=10.0)
    args.budget_fit = True

    rc, fitness_calls, state, rows, _runlog = _drive_eval_paced(
        cli, args, pool=_make_pool(4)
    )
    assert rc == 0
    assert state["status"] == "completed"
    assert state["generations_completed"] == 1
    # All 4 imps dispatched — nothing trimmed on the first generation.
    assert fitness_calls == 4
    assert not any(r.get("phase") == "budget_fit" for r in rows)


def test_budget_fit_cli_passthrough_trims_and_emits_row(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production caller: with --budget-fit ON and a budget that (from gen 2)
    cannot cover the full active set, gen 2 is trimmed to the top-rank prefix,
    a budget_fit audit row lands, and the generation completes end-to-end."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    # hours=1.3 → 4680s budget. Gen 1 burns 4 evals (evals*600 → 2400s).
    # Gen-2 trim: remaining = 4680-2400 = 2280; reserve = 600;
    # fit = floor((2280-600)/600) = 2 → drop 2 of 4.
    args = _build_args(tmp_path, pool_size=4, generations=2, hours=1.3)
    args.budget_fit = True

    rc, fitness_calls, state, rows, _runlog = _drive_eval_paced(
        cli, args, pool=_make_pool(4)
    )
    assert rc == 0
    assert state["status"] == "completed"
    assert state["generations_completed"] == 2
    # Gen 1 = 4 evals (untrimmed), gen 2 = 2 evals (trimmed) → 6 total.
    assert fitness_calls == 6

    bf_rows = [r for r in rows if r.get("phase") == "budget_fit"]
    assert len(bf_rows) == 1
    row = bf_rows[0]
    assert row["generation"] == 2  # never gen 1
    assert row["dropped"] == 2
    assert row["fit_count"] == 2
    assert row["outcome"] == "budget-fit"
    assert "trimmed 2 imp(s)" in row["reason"]


def test_budget_fit_off_is_byte_identical_full_dispatch(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With --budget-fit OFF, the identical budget-constrained run dispatches
    the FULL active set every generation and writes no budget_fit row."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, pool_size=4, generations=2, hours=1.3)
    # args.budget_fit deliberately left UNSET → getattr default False.

    rc, fitness_calls, state, rows, _runlog = _drive_eval_paced(
        cli, args, pool=_make_pool(4)
    )
    assert rc == 0
    assert state["status"] == "completed"
    assert state["generations_completed"] == 2
    # Gen 1 = 4, gen 2 = full 4 (no trim) → 8 total, vs the ON run's 6.
    assert fitness_calls == 8
    assert not any(r.get("phase") == "budget_fit" for r in rows)


def test_budget_fit_prevents_mid_fitness_strand(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The payoff: on a budget too small for the full pool, --budget-fit ON
    trims gen 2 so it finishes cleanly (stop reason 'generations-reached'),
    while OFF dispatches the full set and strands mid-fitness on wall-clock."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)

    def _run(dir_name: str, budget_fit: bool) -> tuple[int, dict, list, str]:
        d = tmp_path / dir_name
        d.mkdir()
        # hours=0.9 → 3240s. Gen 1 → 2400s elapsed. Gen-2 trim:
        # remaining = 840; reserve = 600; fit = floor((840-600)/600)=0 → 1.
        args = _build_args(d, pool_size=4, generations=2, hours=0.9)
        if budget_fit:
            args.budget_fit = True
        rc, calls, state, rows, runlog = _drive_eval_paced(
            cli, args, pool=_make_pool(4)
        )
        assert rc == 0
        return calls, state, rows, runlog

    on_calls, on_state, on_rows, on_log = _run("on", budget_fit=True)
    off_calls, off_state, off_rows, off_log = _run("off", budget_fit=False)

    # ON: gen 2 trimmed to 1 (dropped 3); the kept imp ran → 5 total evals.
    # The run ended by the generation cap, NOT a mid-fitness wall-clock strand.
    bf_on = [r for r in on_rows if r.get("phase") == "budget_fit"]
    assert len(bf_on) == 1
    assert bf_on[0]["generation"] == 2
    assert bf_on[0]["dropped"] == 3
    assert on_calls == 5
    assert "Stop reason: generations-reached" in on_log

    # OFF: no trim → gen 2 dispatched the full set and stranded on wall-clock
    # (6 evals: 4 + 2 before the mid-fitness budget break).
    assert not any(r.get("phase") == "budget_fit" for r in off_rows)
    assert off_calls == 6
    assert "Stop reason: wall-clock" in off_log


def test_budget_fit_estimator_excludes_null_diff_and_crash_serial(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EJ.6 poisoning guard (serial): a 0-game null-diff screen and a crash
    must contribute NOTHING to the per-eval estimator — only real games-played
    evals feed it. A future refactor moving either append above its `continue`
    would drag per_eval_s down (under-trim → strand); this pins the invariant.

    Proof: gen 1 runs one success (+600s on the clock), one null-diff screen
    (0s), and one crash (0s). At the gen-2 trim, _budget_fit_active is called
    with per_eval_s == the single success's 600s — NOT the 200s mean it would
    be if the two 0-duration screens/crashes had leaked into the accumulator.
    """
    from orchestrator.evolve_dev_apply import DevApplyNullDiffError

    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    # hours large → no wall-clock pressure; the trim still CALLS
    # _budget_fit_active (the dropped>0 row-gate is downstream of the call).
    args = _build_args(
        tmp_path, pool_size=3, generations=2, hours=100.0, screen_null_diff=True
    )
    args.budget_fit = True
    pool = _make_pool(3)  # idx0=rank1, idx1=rank2, idx2=rank3

    clock = {"t": 0.0}
    success_dt = 600.0

    def time_fn() -> float:
        return clock["t"]

    def fitness(parent: str, imp: Improvement, **_k: Any) -> FitnessResult:
        # Behavior keyed by rank so gen-2 re-runs stay consistent.
        if imp.rank == 2:  # null-diff screen: 0 games, advances the clock 0s
            raise DevApplyNullDiffError(
                "no semantic .py change after 3 attempt(s)",
                attempts=3,
                ast_equivalent=False,
            )
        if imp.rank == 3:  # crash: 0 games, advances the clock 0s
            raise RuntimeError("boom")
        # rank 1 → a real games-played eval: advance the clock by success_dt so
        # a correctly-recorded duration is a distinctive 600s.
        clock["t"] += success_dt
        return _fitness(imp, bucket="close", parent=parent)

    def refresh(*_a: Any, **k: Any) -> list[Improvement]:
        return [] if k.get("skip_mirror") else list(pool)

    seen_per_eval: list[float] = []
    real_budget_fit = cli._budget_fit_active

    def spy(active: list[int], pool_arg: list[Improvement], **kw: Any) -> Any:
        seen_per_eval.append(kw["per_eval_s"])
        return real_budget_fit(active, pool_arg, **kw)

    monkeypatch.setattr(cli, "_budget_fit_active", spy)

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("bucket=close never promotes; stack-apply fired")
        ),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no promotion; regression must not fire")
        ),
        current_version_fn=lambda: "v0",
        time_fn=time_fn,
    )
    assert rc == 0

    rows = [
        json.loads(line)
        for line in args.results_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    # The null-diff screen and the crash actually RAN in gen 1 (so they were
    # candidates to poison the estimator)...
    assert any(r.get("outcome") == "screen-null-diff" for r in rows)
    assert any(r.get("outcome") == "crash" for r in rows)
    # ...but the gen-2 trim saw per_eval_s == the single success duration,
    # proving neither contributed a (0-duration) entry to the accumulator.
    assert seen_per_eval == [success_dt]


def test_skill_md_documents_all_six_ej_flags() -> None:
    """The improve-bot-evolve SKILL.md ## Flags section lists all six EJ flags
    with the pairing warning integrated (no duplicate warnings)."""
    skill_md = (
        _REPO_ROOT
        / ".claude"
        / "skills"
        / "improve-bot-evolve"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    for flag in (
        "--priors-exclude-promoted",
        "--screen-null-diff",
        "--regression-rule",
        "--panel-floor",
        "--refresh-dedup",
        "--budget-fit",
    ):
        assert flag in skill_md, f"{flag} missing from SKILL.md"
    # The EJ.3+EJ.4 pairing warning is present exactly once (reconciled, not
    # duplicated) and no longer defers the table to a future step.
    assert skill_md.count("Pairing warning (EJ.3 + EJ.4)") == 1
    assert "The full flags table lands in EJ.6" not in skill_md


def test_skill_md_documents_the_viewer_flag() -> None:
    """The improve-bot-evolve SKILL.md documents `--viewer` (Phase EV.3).

    Same contract as the EJ flags above: a new evolve flag is not shipped
    until the skill an operator actually invokes describes it. The extra
    load-bearing facts here are the safety ones -- `--concurrency 1` is
    mandatory, closing the container detaches rather than stops, and Ctrl+C
    can orphan SC2 processes.
    """
    skill_md = (_REPO_ROOT / ".claude" / "skills" / "improve-bot-evolve" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    lines = skill_md.splitlines()
    # (a) The frontmatter flag summary names it, so a reader who never scrolls
    # to the table still knows the flag exists.
    argument_lines = [line for line in lines if line.startswith("argument:")]
    assert len(argument_lines) == 1, "expected exactly one frontmatter argument: line"
    assert "--viewer" in argument_lines[0], "--viewer missing from the flag summary"
    # (b) Exactly one `--viewer` row in the flags table -- the same exact-count
    # guard the EJ test uses against a duplicated row -- and its DEFAULT CELL
    # says off. Assert the split cell, never `"off" in row`: the word "off"
    # also appears in ordinary row prose ("runs off the main thread"), so a
    # substring check stays GREEN with the documented default flipped to ON.
    viewer_rows = [line for line in lines if line.startswith("| `--viewer` |")]
    assert len(viewer_rows) == 1, f"expected 1 --viewer table row, got {len(viewer_rows)}"
    cells = [cell.strip() for cell in viewer_rows[0].split("|")]
    assert cells[3].lower() == "off", f"--viewer default cell is {cells[3]!r}, expected 'off'"
    # (c) The safety detail lives in a named prose block under the table --
    # the file's established pattern for detail too long for a cell (cf.
    # "Pairing warning (EJ.3 + EJ.4)"). Pin the facts there, not in the row.
    assert skill_md.count("**Viewer note (`--viewer`):**") == 1
    note = skill_md.split("**Viewer note (`--viewer`):**", 1)[1].split("\n### ", 1)[0]
    for fragment in (
        "Windows",  # platform restriction
        "[viewer]",  # optional extra
        "--concurrency 1",  # mutual-exclusion requirement
        # "DETACHES", not "headless": "headless" occurs twice in the note, so
        # deleting the whole detach-vs-stop sentence would leave this green.
        "DETACHES",
        "close the console window",  # the ONE gesture that actually stops a run
        "Ctrl+C",  # orphaned-SC2 warning
        "orphaned SC2",
    ):
        assert fragment in note, f"{fragment!r} missing from the Viewer note"
    # (d) The mutual exclusion is ALSO named on the `--concurrency` row, so an
    # operator reaching for `--concurrency 4` learns it there.
    concurrency_rows = [line for line in lines if line.startswith("| `--concurrency` |")]
    assert len(concurrency_rows) == 1, "expected exactly one --concurrency table row"
    assert "--viewer" in concurrency_rows[0], (
        "--concurrency row does not name the --viewer mutual exclusion"
    )
    # (e) EV.3 H-1: every stop_reason the skill advertises must be one the
    # runner can actually emit. SKILL.md used to list `"dashboard-stop"`,
    # which `scripts/evolve.py` never produces -- `bots/v0/api.py` writes
    # data/evolve_run_control.json but nothing reads it -- so an operator was
    # told about a stop path that does not exist. Producer/consumer check, not
    # a keyword ban: prose ABOUT the gap is fine, an advertised value is not.
    prefix = "`stop_reason` in the final run log:"
    reason_lines = [line for line in lines if line.startswith(prefix)]
    assert len(reason_lines) == 1, f"expected 1 stop_reason summary line, got {len(reason_lines)}"
    documented = set(re.findall(r'`"([a-z-]+)"`', reason_lines[0]))
    assert documented, f"no stop_reason values parsed from: {reason_lines[0]}"
    evolve_src = (_REPO_ROOT / "scripts" / "evolve.py").read_text(encoding="utf-8")
    for reason in sorted(documented):
        assert f'"{reason}"' in evolve_src, (
            f"SKILL.md documents stop_reason {reason!r}, which scripts/evolve.py never emits"
        )


def test_launch_evolve_ps1_pins_the_viewer_spawn_contract() -> None:
    """`scripts/launch-evolve.ps1` is a deployment seam pytest cannot reach.

    The plan's risk table notes a malformed spawn line "would surface at the
    start of EV.5's four-hour window". These are machine-consumed CLI tokens,
    not prose, so pinning them is contract rather than wording.
    """
    raw = (_REPO_ROOT / "scripts" / "launch-evolve.ps1").read_bytes()
    # ASCII-only + no BOM. windows-shell.md: PowerShell 5.1 decodes a no-BOM
    # .ps1 as cp1252, so ONE non-ASCII byte can corrupt string/brace parsing
    # with no parse error -- a silent false green. Currently correct; pin it.
    assert raw[:3] != b"\xef\xbb\xbf", "launch-evolve.ps1 gained a UTF-8 BOM"
    assert max(raw) < 128, "launch-evolve.ps1 gained a non-ASCII byte"
    text = raw.decode("ascii")
    spawn_lines = [line for line in text.splitlines() if "$evolveCmd =" in line]
    assert len(spawn_lines) == 1, f"expected 1 $evolveCmd assignment, got {len(spawn_lines)}"
    spawn = spawn_lines[0]
    # (a) The `--extra <name>` the launcher asks uv for must be a real key in
    # pyproject's [project.optional-dependencies] -- a cross-file coupling
    # with no other mechanical link. A rename there fails the launcher only at
    # run time, in front of the operator.
    extra_match = re.search(r"--extra (\S+)", spawn)
    assert extra_match is not None, f"no --extra in the spawn line: {spawn}"
    with (_REPO_ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    declared = pyproject["project"]["optional-dependencies"]
    assert extra_match.group(1) in declared, (
        f"launcher asks for --extra {extra_match.group(1)!r}, "
        f"which is not in [project.optional-dependencies] ({sorted(declared)})"
    )
    # (b) --viewer is still passed. Dropping it has ZERO dev-facing symptom
    # while silently reverting the whole Phase EV deliverable.
    assert "--viewer" in spawn, f"launcher no longer passes --viewer: {spawn}"
    # (c) The double-run guard's WMI CommandLine token must still match the
    # string actually spawned. Low probability, high consequence: a miss means
    # two concurrent evolve runs both auto-committing [evo-auto] to master.
    guard_lines = [line for line in text.splitlines() if "CommandLine LIKE" in line]
    assert len(guard_lines) == 1, f"expected 1 CommandLine guard, got {len(guard_lines)}"
    token_match = re.search(r"CommandLine LIKE '%([^%]+)%'", guard_lines[0])
    assert token_match is not None, f"unparseable guard filter: {guard_lines[0]}"
    assert token_match.group(1) in spawn, (
        f"guard probes for {token_match.group(1)!r}, absent from the spawn line: {spawn}"
    )


# ---------------------------------------------------------------------------
# Phase EV.1 — `--viewer` flag surface + graceful-degradation gate
# ---------------------------------------------------------------------------
#
# Every probe here manipulates the REAL import machinery (``sys.meta_path`` /
# ``sys.modules``) rather than stubbing ``importlib.util.find_spec``, so the
# tests exercise the same code path a real missing/broken ``[viewer]`` extra
# takes. They are also deterministic on a box that DOES have pygame installed.


class _PygameFinder:
    """``sys.meta_path`` finder that scripts the answer for ``pygame``.

    ``outcome`` is either an exception instance to raise or the spec object
    (possibly ``None``) to return. Every other module name returns ``None``
    so the real finders behind us keep working.
    """

    outcome: object = None

    @classmethod
    def find_spec(cls, name: str, *args: object, **kwargs: object) -> Any:
        if name != "pygame" and not name.startswith("pygame."):
            return None
        if isinstance(cls.outcome, BaseException):
            raise cls.outcome
        return cls.outcome


def _install_pygame_finder(monkeypatch: pytest.MonkeyPatch, outcome: object) -> None:
    """Front-load a ``_PygameFinder`` subclass scripted with ``outcome``."""
    finder = type("_ScriptedPygameFinder", (_PygameFinder,), {"outcome": outcome})
    monkeypatch.delitem(sys.modules, "pygame", raising=False)
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])


def _arrange_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A finder that refuses the name — broken or shadowed install."""
    _install_pygame_finder(monkeypatch, ImportError("simulated missing extra"))


def _arrange_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """``pygame`` cached in ``sys.modules`` with ``__spec__`` set to ``None``.

    ``importlib.util.find_spec`` raises ``ValueError`` for this — real
    behaviour of this interpreter, not a stub.
    """
    monkeypatch.setitem(sys.modules, "pygame", ModuleType("pygame"))


def _arrange_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """No finder can resolve the name at all — ``find_spec`` returns None."""
    monkeypatch.delitem(sys.modules, "pygame", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_PygameFinder])


def _arrange_namespace_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare ``pygame/`` directory resolving as a namespace package.

    ``find_spec`` succeeds but the spec carries ``loader is None``: "found"
    yet not importable. Treating that as available would hand EV.2 a pygame
    it cannot import.
    """
    _install_pygame_finder(monkeypatch, ModuleSpec("pygame", None))


def _arrange_unexpected_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """A finder raising OUTSIDE the import-exception hierarchy.

    ``sys.meta_path`` is open to third parties (this venv already carries
    setuptools' ``DistutilsMetaFinder`` and six's ``_Finder``) and none of
    them are bound to any exception contract. The gate is total: it must
    still degrade, not propagate.
    """
    _install_pygame_finder(monkeypatch, RuntimeError("hostile third-party finder"))


def _arrange_raising_loader_attr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A spec object whose ``loader`` attribute itself raises.

    Pins that the spec INSPECTION is inside the same guard as the probe
    call — reading ``.loader`` outside the ``try`` reopens the same hole.
    """

    class _HostileSpec:
        name = "pygame"

        @property
        def loader(self) -> Any:
            raise AttributeError("exotic spec has no usable loader")

    _install_pygame_finder(monkeypatch, _HostileSpec())


def _arrange_pygame_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend a real, importable pygame is installed (loader-bearing spec)."""
    _install_pygame_finder(monkeypatch, ModuleSpec("pygame", object(), origin="stub"))


def test_viewer_flag_parses_when_passed(cli: ModuleType) -> None:
    """--viewer parses to args.viewer.

    The default-off half lives in ``test_default_flags``, this file's
    declared owner of flag defaults.
    """
    assert cli.build_parser().parse_args(["--viewer"]).viewer is True


def test_viewer_enabled_false_and_silent_by_default(
    cli: ModuleType, caplog: pytest.LogCaptureFixture
) -> None:
    """No --viewer: False, and SILENT (the default path warns about nothing)."""
    args = cli.build_parser().parse_args([])
    with caplog.at_level(logging.DEBUG, logger="evolve"):
        assert cli._viewer_enabled(args) is False
    assert [r.getMessage() for r in caplog.records] == []


def test_viewer_enabled_false_with_warning_on_non_win32(
    cli: ModuleType,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-Windows: False plus a WARNING naming the platform as the reason."""
    monkeypatch.setattr(sys, "platform", "linux")
    args = cli.build_parser().parse_args(["--viewer"])

    with caplog.at_level(logging.WARNING, logger="evolve"):
        assert cli._viewer_enabled(args) is False

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("Windows-only" in m for m in warnings), warnings
    assert any("headless" in m for m in warnings), warnings


@pytest.mark.parametrize(
    "arrange",
    [
        pytest.param(_arrange_import_error, id="find_spec-raises-ImportError"),
        pytest.param(_arrange_value_error, id="find_spec-raises-ValueError"),
        pytest.param(_arrange_not_found, id="find_spec-returns-None"),
        pytest.param(_arrange_namespace_package, id="spec-has-no-loader"),
        pytest.param(_arrange_unexpected_exception, id="find_spec-raises-RuntimeError"),
        pytest.param(_arrange_raising_loader_attr, id="spec-loader-attr-raises"),
    ],
)
def test_viewer_enabled_degrades_when_pygame_not_importable(
    cli: ModuleType,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    arrange: Callable[[pytest.MonkeyPatch], None],
) -> None:
    """``_viewer_enabled`` is TOTAL: no probe failure may propagate.

    The gate's contract is *degrade and keep running*, so every way the
    import machinery can misbehave — a raised ImportError, ValueError, or
    anything else a third-party ``sys.meta_path`` finder invents, a missing
    spec, an unloadable spec, or a spec that raises on inspection — must
    come back as False + a WARNING. A traceback out of here would kill a
    multi-hour evolve at second zero.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    arrange(monkeypatch)
    args = cli.build_parser().parse_args(["--viewer"])

    with caplog.at_level(logging.WARNING, logger="evolve"):
        assert cli._viewer_enabled(args) is False

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("pygame is not importable" in m for m in warnings), warnings
    assert any("headless" in m for m in warnings), warnings


def test_viewer_enabled_true_on_win32_with_pygame(
    cli: ModuleType,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows + an importable pygame: True, and nothing is warned about."""
    monkeypatch.setattr(sys, "platform", "win32")
    _arrange_pygame_available(monkeypatch)
    args = cli.build_parser().parse_args(["--viewer"])

    with caplog.at_level(logging.WARNING, logger="evolve"):
        assert cli._viewer_enabled(args) is True

    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_main_viewer_with_concurrency_gt_1_exits_with_guidance(
    cli: ModuleType,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main(["--viewer", "--concurrency", "2"]) exits before any run_loop work."""

    def _explode(*a: Any, **k: Any) -> int:
        raise AssertionError("run_loop must not be reached")

    monkeypatch.setattr(cli, "run_loop", _explode)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--viewer", "--concurrency", "2"])

    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "--viewer requires --concurrency 1" in err
    assert "evolve_worker.py" in err


def test_main_viewer_with_degraded_gate_falls_through_to_headless(
    cli: ModuleType,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--concurrency 1 passes the guard, and a FALSE gate runs plain headless.

    The interesting case: ``--viewer`` IS set but ``_viewer_enabled``
    returns False (no Windows / no pygame). ``main()`` must gate the whole
    EV.2 inversion on the *gate*, not on the flag — no viewer object, no
    batch thread, and above all no ``run_batch_fn`` seam injected, so the
    ``elif concurrency_int > 1`` mirror-dispatcher branch stays reachable.
    Weakening the guard to ``if args.viewer:`` turns this red.
    """
    monkeypatch.setattr(sys, "platform", "linux")  # forces headless degrade

    seen: dict[str, Any] = {}

    def _fake_run_loop(args: argparse.Namespace, **kwargs: Any) -> int:
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(cli, "run_loop", _fake_run_loop)

    with caplog.at_level(logging.WARNING, logger="evolve"):
        assert cli.main(["--viewer", "--concurrency", "1"]) == 0

    assert seen["kwargs"] == {}
    # No viewer means no viewer-safety noise on stderr either: the Ctrl+C
    # warning is scoped to runs that actually open a window. Assert the whole
    # banner rather than one phrase of it -- strictly stronger, and immune to
    # the rewording EV.3 did (the old sentinel "CLOSE THE VIEWER WINDOW" no
    # longer appears in the banner at all, so it would now pass vacuously).
    assert cli._VIEWER_CTRL_C_WARNING not in capsys.readouterr().err
    # ...and the gate DID explain itself, so this is headless-by-gate, not
    # headless-because-nothing-ran.
    messages = [r.getMessage() for r in caplog.records]
    assert any("Windows-only" in m for m in messages), messages


@pytest.mark.parametrize(
    ("argv", "expected_concurrency"),
    [
        pytest.param([], 1, id="default"),
        pytest.param(["--concurrency", "2"], 2, id="parallel"),
    ],
)
def test_main_without_viewer_reaches_run_loop_unchanged(
    cli: ModuleType,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    expected_concurrency: int,
) -> None:
    """Every no-``--viewer`` path is byte-identical to pre-EV, in three senses.

    * **Call shape** — ``run_loop`` is reached with NO injected seams
      (``kwargs == {}``), so its own ``run_batch_fn=None`` default stands
      and the ``elif concurrency_int > 1`` mirror-dispatcher branch stays
      reachable.
    * **Return value** — ``main()`` returns what ``run_loop`` returned,
      pinned with a distinctive non-zero code. Asserting ``== 0`` against a
      fake that returns 0 cannot tell a passthrough from a hardcoded
      constant, and EV.2's declared job is to wrap this exact call and map
      ``SystemExit`` into the return code.
    * **Observable log stream** — a plain headless run gains no EV-era
      warning; "byte-identical" is a promise about what the operator sees.

    The ``parallel`` case additionally pins that the EV.1 mutual-exclusion
    guard does NOT fire on the pre-existing ``--concurrency N>1`` path: drop
    the ``args.viewer and`` conjunct and every production parallel evolve
    run dies at startup.
    """
    seen: dict[str, Any] = {}

    def _fake_run_loop(args: argparse.Namespace, **kwargs: Any) -> int:
        seen["args"] = args
        seen["kwargs"] = kwargs
        return 7

    monkeypatch.setattr(cli, "run_loop", _fake_run_loop)

    with caplog.at_level(logging.WARNING, logger="evolve"):
        assert cli.main(argv) == 7

    assert seen["kwargs"] == {}
    assert seen["args"].viewer is False
    assert seen["args"].concurrency == expected_concurrency
    messages = [r.getMessage() for r in caplog.records]
    assert not any("--viewer" in m for m in messages), messages


# ---------------------------------------------------------------------------
# Phase EV.2 — viewer inversion in `main()`
# ---------------------------------------------------------------------------
#
# The viewer is faked, never constructed: a real ``SelfPlayViewer`` would
# demand pygame plus a Windows message pump, and neither exists on CI. The
# fakes stand in at the exact production seam ``main()`` reaches for
# (``selfplay_viewer.SelfPlayViewer``), so the wiring under test is real even
# though the window is not.


class _RecordingViewer:
    """Base fake: records the two enqueue-only callbacks the wrapper chains.

    Subclasses supply ``run_with_batch``, which is the only axis that
    matters at ``main()`` level (inline / threaded / detaching / failing).
    The real ``on_game_start`` call shape is contract-tested against the
    actual ``SelfPlayViewer`` in
    ``tests/test_evolve_parallel.py::test_viewer_session_feeds_the_real_viewer_event_queue``.
    """

    instances: list[Any] = []

    def __init__(self) -> None:
        self.starts: list[tuple[Any, ...]] = []
        self.ends: list[Any] = []
        self.stop_event_arg: Any = "<never called>"
        type(self).instances.append(self)

    def on_game_start(
        self,
        game_index: int,
        total: int,
        p1_pid: int,
        p2_pid: int,
        p1_label: str,
        p2_label: str,
    ) -> None:
        self.starts.append((game_index, total, p1_pid, p2_pid, p1_label, p2_label))

    def on_game_end(self, result: Any) -> None:
        self.ends.append(result)


class _FakeInlineViewer(_RecordingViewer):
    """``run_with_batch`` runs the batch inline, then returns.

    Models the happy path: the operator leaves the window open until the
    evolve run finishes on its own.
    """

    instances: list[Any] = []

    def run_with_batch(self, batch_fn: Callable[[], Any], *, stop_event: Any = None) -> Any:
        self.stop_event_arg = stop_event
        return batch_fn()


class _FakeThreadedViewer(_RecordingViewer):
    """``run_with_batch`` runs the batch on a worker thread and joins it.

    Same completion semantics as ``_FakeInlineViewer``, but ``run_loop``
    genuinely executes OFF the main thread — which is what production does,
    and the only way to see failures that would otherwise be swallowed by
    the batch thread instead of reaching ``main()``.
    """

    instances: list[Any] = []

    def run_with_batch(self, batch_fn: Callable[[], Any], *, stop_event: Any = None) -> Any:
        self.stop_event_arg = stop_event
        thread = threading.Thread(target=batch_fn, daemon=True)
        thread.start()
        thread.join(10.0)
        return None


class _FakePostStartFailureViewer(_RecordingViewer):
    """``run_with_batch`` starts the batch and THEN dies.

    The window that matters most in production and the one every other fake
    misses: ``container.run_with_batch`` runs its entire pygame frame loop
    *after* ``batch_thread.start()``, so a viewer can fail with a live,
    multi-hour ``run_loop`` still enqueueing behind it. Only
    ``finally: session.close()`` latches the session on this path — without
    it, ``_event_queue`` grows unbounded with nobody draining it.
    """

    instances: list[Any] = []

    def run_with_batch(self, batch_fn: Callable[[], Any], *, stop_event: Any = None) -> Any:
        self.stop_event_arg = stop_event
        thread = threading.Thread(target=batch_fn, daemon=True)
        thread.start()
        thread.join(10.0)
        raise RuntimeError("pygame frame loop died after the batch started")


def _arrange_viewer_available(
    cli: ModuleType, monkeypatch: pytest.MonkeyPatch, viewer_cls: type
) -> None:
    """Make ``_viewer_enabled`` True and hand ``main()`` a fake viewer class.

    Also shrinks the detached-tail heartbeat. Every viewer-path test blocks
    on ``done.wait(_VIEWER_DETACHED_HEARTBEAT_S)`` in ``main()``, so at the
    production 60s a regression that leaves ``done`` unset would park the
    whole suite instead of failing — which is precisely how the
    "run_with_batch raised before the batch thread started" hang stayed
    invisible. A tiny interval also makes the heartbeat body observable at
    all, and is the reason the constant exists.
    """
    import selfplay_viewer

    monkeypatch.setattr(sys, "platform", "win32")
    _arrange_pygame_available(monkeypatch)
    monkeypatch.setattr(selfplay_viewer, "SelfPlayViewer", viewer_cls)
    monkeypatch.setattr(cli, "_VIEWER_DETACHED_HEARTBEAT_S", 0.01)
    if hasattr(viewer_cls, "instances"):
        monkeypatch.setattr(viewer_cls, "instances", [])


def _run_main_with_watchdog(cli: ModuleType, argv: list[str], *, timeout_s: float = 15.0) -> int:
    """Call ``cli.main(argv)`` on a worker thread, failing if it never returns.

    ``main()``'s viewer path can only fail two ways: an assertion, or an
    infinite park in the heartbeat loop. Tests that could hit the second
    must not hang the suite — the hang IS the regression, so it has to
    surface as a red assertion.
    """
    box: dict[str, Any] = {}

    def _call() -> None:
        try:
            box["rc"] = cli.main(argv)
        except BaseException as exc:  # surfaced on the calling thread below
            box["exc"] = exc

    thread = threading.Thread(target=_call, daemon=True)
    thread.start()
    thread.join(timeout_s)
    assert not thread.is_alive(), (
        f"main({argv}) never returned within {timeout_s}s — it hung instead of "
        "falling back to a headless run"
    )
    if "exc" in box:
        raise box["exc"]
    return cast(int, box["rc"])


def test_main_viewer_reaches_viewer_callbacks_end_to_end(
    cli: ModuleType,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration through the production caller: ``main(["--viewer"])``.

    Drives the whole EV.2 chain — gate -> viewer construction -> session ->
    ``run_loop(run_batch_fn=...)`` -> ``selfplay.run_batch`` -> the viewer's
    own ``on_game_start`` / ``on_game_end``. Unit tests of
    ``_EvolveViewerSession`` alone cannot see a silent wiring failure (e.g.
    ``main()`` forgetting to pass ``run_batch_fn``); this test can.
    """
    _arrange_viewer_available(cli, monkeypatch, _FakeInlineViewer)

    record = _rec("cand_x", "v13", "cand_x")
    seen: dict[str, Any] = {}

    from orchestrator import selfplay

    def fake_run_batch(
        p1: str, p2: str, games: int, map_name: str = "Simple64", **kwargs: Any
    ) -> list[SelfPlayRecord]:
        seen["batch_kwargs"] = kwargs
        kwargs["on_game_start"](1, games, 111, 222, p1, p2)
        kwargs["on_game_end"](record)
        return [record]

    monkeypatch.setattr(selfplay, "run_batch", fake_run_batch)

    caller_ends: list[Any] = []
    caller_stop = threading.Event()

    def _fake_run_loop(args: argparse.Namespace, **kwargs: Any) -> int:
        # Stand in for run_fitness_eval: bring your own on_game_end tally
        # and your own early-stop event, exactly as the real callee does.
        run_batch_fn = kwargs["run_batch_fn"]
        run_batch_fn(
            "cand_x",
            "v13",
            5,
            "Simple64",
            on_game_end=caller_ends.append,
            stop_event=caller_stop,
        )
        return 7

    monkeypatch.setattr(cli, "run_loop", _fake_run_loop)

    assert _run_main_with_watchdog(cli, ["--viewer"]) == 7

    assert len(_FakeInlineViewer.instances) == 1
    viewer = _FakeInlineViewer.instances[0]
    # The viewer really was reached, through the production entry point.
    assert viewer.starts == [(1, 5, 111, 222, "cand_x", "v13")]
    assert viewer.ends == [record]
    # ...without displacing the callee's own tally or early-stop.
    assert caller_ends == [record]
    assert seen["batch_kwargs"]["stop_event"] is caller_stop
    # D-1: the run's stop_event is NEVER handed to the viewer's teardown.
    assert viewer.stop_event_arg is None
    # A run that finished with the window still open must not print the
    # detach counter-message.
    assert "CONTINUES headless" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "viewer_cls",
    [
        pytest.param(_FakeInlineViewer, id="viewer-completes-normally"),
        pytest.param(_FakePostStartFailureViewer, id="viewer-dies-after-batch-start"),
    ],
)
def test_main_closes_the_viewer_session_through_the_production_caller(
    cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    viewer_cls: type,
) -> None:
    """The production ``finally: session.close()`` must be observable.

    Done-when #4's unit test closes the session itself, so deleting the
    production call changed nothing anywhere — textbook silent wiring
    (``.claude/rules/code-quality.md``). Here the latch is only ever closed
    by ``main()``: the test grabs the exact ``run_batch_fn`` ``main()``
    injected, and re-drives it AFTER ``main()`` returned. A live session
    would still chain (adding an ``on_game_start`` key and enqueueing onto
    a queue nobody drains); a closed one is a byte-identical passthrough.

    Both parameters matter, and the second is the one that pins ``finally``
    specifically: a viewer that dies AFTER starting the batch is the real
    production shape (``container.run_with_batch``'s whole pygame frame loop
    runs post-start), and it is the only path where moving ``close()`` onto
    the success path inside the ``try`` would silently leak.
    """
    _arrange_viewer_available(cli, monkeypatch, viewer_cls)

    record = _rec("cand_x", "v13", "cand_x")
    seen: dict[str, Any] = {}

    from orchestrator import selfplay

    def fake_run_batch(
        p1: str, p2: str, games: int, map_name: str = "Simple64", **kwargs: Any
    ) -> list[SelfPlayRecord]:
        seen["batch_kwargs"] = kwargs
        start_cb = kwargs.get("on_game_start")
        if start_cb is not None:
            start_cb(1, games, 111, 222, p1, p2)
        kwargs["on_game_end"](record)
        return [record]

    monkeypatch.setattr(selfplay, "run_batch", fake_run_batch)

    injected: dict[str, Any] = {}

    def _fake_run_loop(args: argparse.Namespace, **kwargs: Any) -> int:
        injected["run_batch_fn"] = kwargs["run_batch_fn"]
        return 7

    monkeypatch.setattr(cli, "run_loop", _fake_run_loop)

    # A viewer that dies after the batch started must not take the run with
    # it: main() still returns run_loop's real code either way (D-5).
    assert _run_main_with_watchdog(cli, ["--viewer"]) == 7

    viewer = viewer_cls.instances[0]  # type: ignore[attr-defined]
    assert viewer.starts == [] and viewer.ends == []  # nothing yet

    def caller_on_end(result: Any) -> None:
        return None

    # Post-run: drive the wrapper main() built, exactly as a still-running
    # evolve loop would after the operator closed the window.
    injected["run_batch_fn"]("cand_x", "v13", 1, "Simple64", on_game_end=caller_on_end)

    assert "on_game_start" not in seen["batch_kwargs"]
    assert seen["batch_kwargs"]["on_game_end"] is caller_on_end
    assert viewer.starts == []
    assert viewer.ends == []


def test_main_viewer_warns_about_ctrl_c_on_stderr_before_the_window_opens(
    cli: ModuleType,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Ctrl+C / orphaned-SC2 hazard needs an operator-facing surface.

    Under ``--viewer`` the evolution loop runs off the main thread, where
    burnysc2's ``signal.signal(SIGINT, KillSwitch.kill_all)`` is never armed
    (``src/orchestrator/selfplay.py`` installs a process-global proxy that
    returns ``None`` off-main-thread), and D-1's ``stop_event=None`` makes
    Ctrl+C the operator's only stop gesture. So Ctrl+C is exactly the wrong
    reflex, and ``.claude/rules/bot-runtime.md`` forbids cleaning up the
    orphans by hand.

    The warning must reach **stderr** (same stream as the container's own
    messages, immune to log routing) and must land BEFORE ``run_with_batch``
    takes over the console — which is what the mid-run capture pins.
    """
    seen_before: dict[str, str] = {}

    class _CaptureThenRunViewer(_RecordingViewer):
        instances: list[Any] = []

        def run_with_batch(self, batch_fn: Callable[[], Any], *, stop_event: Any = None) -> Any:
            # Everything on stderr so far was printed BEFORE the window.
            seen_before["err"] = capsys.readouterr().err
            return batch_fn()

    _arrange_viewer_available(cli, monkeypatch, _CaptureThenRunViewer)
    monkeypatch.setattr(cli, "run_loop", lambda args, **k: 0)

    assert _run_main_with_watchdog(cli, ["--viewer"]) == 0

    err = seen_before["err"]
    # The whole banner, verbatim -- stronger than the previous single-phrase
    # sentinel and independent of its wording.
    assert cli._VIEWER_CTRL_C_WARNING in err, err
    # The load-bearing facts, pinned separately so a future reword cannot drop
    # one silently. EV.3: the banner must NOT present closing the viewer as a
    # way to stop the run -- that only detaches -- so the stop gesture named
    # here is the console window.
    assert "Ctrl+C" in err, err
    assert "orphaned SC2" in err, err
    assert "DETACHES" in err, err
    assert "close this console window" in err, err


def test_main_viewer_close_detaches_and_run_continues_headless(
    cli: ModuleType,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing the window detaches; ``main()`` waits for the real run.

    ``run_loop`` executes on the viewer's DAEMON batch thread, so a
    ``main()`` that returned when ``run_with_batch`` returned would kill a
    multi-hour evolution the instant the operator hit Escape. The ordering
    assertion pins the sequence: viewer returns first, the loop finishes
    later, and ``main()`` still hands back ``run_loop``'s real return code
    (a hardcoded 0 or a lost ``rc_box`` would return 1 here).

    Interleaving is signal-driven, never timed: the fake viewer returns only
    once the batch is provably inside ``run_loop``, and the batch returns
    only once it has seen a heartbeat log record — which also proves the
    heartbeat body actually executes on a detached tail, and that the
    counter-message reached **stderr**, the same stream as ``container.py``'s
    misleading orphaned-SC2 warning.
    """
    order: list[str] = []
    entered = threading.Event()
    heartbeat_seen = threading.Event()

    class _HeartbeatWatcher(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if "still running headless" in record.getMessage():
                heartbeat_seen.set()

    class _FakeDetachingViewer(_RecordingViewer):
        instances: list[Any] = []

        def run_with_batch(self, batch_fn: Callable[[], Any], *, stop_event: Any = None) -> Any:
            self.stop_event_arg = stop_event
            thread = threading.Thread(target=batch_fn, daemon=True)
            thread.start()
            entered.wait(10.0)  # the batch is provably in run_loop
            order.append("viewer-returned")
            return None

    _arrange_viewer_available(cli, monkeypatch, _FakeDetachingViewer)

    saw: dict[str, bool] = {}

    def _fake_run_loop(args: argparse.Namespace, **kwargs: Any) -> int:
        entered.set()
        # Return only once main() has reached its detached-tail heartbeat,
        # i.e. well past the point where a broken main() could have returned.
        saw["heartbeat"] = heartbeat_seen.wait(10.0)
        order.append("loop-finished")
        return 7

    monkeypatch.setattr(cli, "run_loop", _fake_run_loop)

    evolve_logger = logging.getLogger("evolve")
    watcher = _HeartbeatWatcher()
    evolve_logger.addHandler(watcher)
    try:
        with caplog.at_level(logging.INFO, logger="evolve"):
            rc = _run_main_with_watchdog(cli, ["--viewer"])
    finally:
        evolve_logger.removeHandler(watcher)

    assert rc == 7
    assert order == ["viewer-returned", "loop-finished"]
    assert saw["heartbeat"] is True, "main() never logged a detached-tail heartbeat"
    err = capsys.readouterr().err
    assert "CONTINUES headless" in err
    assert "orphaned-SC2 warning does not apply" in err


def test_main_viewer_construction_failure_falls_back_to_headless(
    cli: ModuleType,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-5: a viewer exception ends the viewer, never the run."""

    class _ExplodingViewer:
        def __init__(self) -> None:
            raise RuntimeError("no display available")

    _arrange_viewer_available(cli, monkeypatch, _ExplodingViewer)

    seen: dict[str, Any] = {}

    def _fake_run_loop(args: argparse.Namespace, **kwargs: Any) -> int:
        seen["kwargs"] = kwargs
        return 7

    monkeypatch.setattr(cli, "run_loop", _fake_run_loop)

    with caplog.at_level(logging.WARNING, logger="evolve"):
        assert _run_main_with_watchdog(cli, ["--viewer"]) == 7

    assert seen["kwargs"] == {}  # headless, byte-identical call shape
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("headless" in m for m in warnings), warnings


@pytest.mark.parametrize(
    "arrange_failure",
    [
        pytest.param("raise", id="run_with_batch-raises-pre-start"),
        pytest.param("return", id="run_with_batch-returns-without-starting"),
    ],
)
def test_main_viewer_that_never_starts_the_batch_really_runs_headless(
    cli: ModuleType,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    arrange_failure: str,
) -> None:
    """``run_with_batch`` dying before ``batch_thread.start()`` must not hang.

    ``container.run_with_batch`` has a real pre-start exception window —
    ``import pygame``, ``_resolve_background_path`` (KeyError),
    ``pygame.init()``, ``display.set_mode()`` (pygame.error),
    ``_load_background`` — all of which run BEFORE the batch thread starts
    and none of which ``_viewer_enabled``'s ``find_spec`` probe can predict
    (a headless/RDP desktop hits them for real).

    Logging "continues headless" is not continuing headless: with no batch
    thread, ``done`` is never set, and ``main()`` parks in the heartbeat
    loop forever — printing "the evolution run CONTINUES headless" and then
    a reassuring heartbeat every interval while running ZERO generations.
    The watchdog turns that hang into a red assertion.
    """

    class _NeverStartsViewer(_RecordingViewer):
        instances: list[Any] = []

        def run_with_batch(self, batch_fn: Callable[[], Any], *, stop_event: Any = None) -> Any:
            self.stop_event_arg = stop_event
            if arrange_failure == "raise":
                raise RuntimeError("pygame.display.set_mode failed")
            return None  # returned without ever invoking batch_fn

    _arrange_viewer_available(cli, monkeypatch, _NeverStartsViewer)

    calls: list[dict[str, Any]] = []

    def _fake_run_loop(args: argparse.Namespace, **kwargs: Any) -> int:
        calls.append(kwargs)
        return 7

    monkeypatch.setattr(cli, "run_loop", _fake_run_loop)

    with caplog.at_level(logging.WARNING, logger="evolve"):
        rc = _run_main_with_watchdog(cli, ["--viewer"])

    assert rc == 7
    # Exactly ONE run_loop execution, and a plain headless one (no seam).
    assert calls == [{}]
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("headless" in m for m in warnings), warnings


def test_main_viewer_reraises_run_loop_exception_on_the_main_thread(
    cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crashed evolve must still crash — traceback and all.

    Headless, a ``run_loop`` exception propagates out of ``main()``. Under
    the viewer it is caught on the batch thread, so ``main()`` has to
    re-raise it; dropping that turns an overnight crash into a silent
    ``return 1`` with the cause swallowed.
    """
    _arrange_viewer_available(cli, monkeypatch, _FakeInlineViewer)

    def _boom(args: argparse.Namespace, **kwargs: Any) -> int:
        raise RuntimeError("evolve exploded mid-generation")

    monkeypatch.setattr(cli, "run_loop", _boom)

    with pytest.raises(RuntimeError, match="evolve exploded mid-generation"):
        _run_main_with_watchdog(cli, ["--viewer"])


def test_main_viewer_reraises_base_exception_from_the_batch_thread(
    cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-``Exception`` ``BaseException``s must not vanish on the batch thread.

    ``run_loop`` runs on the viewer's thread, so anything outside the
    ``SystemExit`` / ``Exception`` pair would escape into the container's
    own capture — which nobody reads on the detach path — and ``main()``
    would report a bland ``rc=1`` with no cause. The threaded fake is
    load-bearing: with an inline viewer the exception would propagate by
    accident and hide the defect.
    """

    class _Detonation(BaseException):
        pass

    _arrange_viewer_available(cli, monkeypatch, _FakeThreadedViewer)

    def _boom(args: argparse.Namespace, **kwargs: Any) -> int:
        raise _Detonation("hard stop")

    monkeypatch.setattr(cli, "run_loop", _boom)

    with pytest.raises(_Detonation):
        _run_main_with_watchdog(cli, ["--viewer"])


@pytest.mark.parametrize(
    ("code", "expected_rc", "expected_stderr"),
    [
        pytest.param(None, 0, "", id="bare-sys-exit-is-a-clean-stop"),
        pytest.param(3, 3, "", id="int-code-passes-through"),
        pytest.param("fatal: pool empty", 1, "fatal: pool empty", id="message-code"),
    ],
)
def test_main_viewer_maps_system_exit_like_cpython(
    cli: ModuleType,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    code: Any,
    expected_rc: int,
    expected_stderr: str,
) -> None:
    """``SystemExit`` off the batch thread must exit like it does headless.

    Headless, ``sys.exit(main())`` hands the ``SystemExit`` to CPython,
    which maps ``None`` -> 0, ``int`` -> itself, and anything else -> print
    to stderr + 1. Collapsing all three to 1 would turn a clean stop into a
    false failure and silently eat the operator's message.
    """
    _arrange_viewer_available(cli, monkeypatch, _FakeInlineViewer)

    def _exiting(args: argparse.Namespace, **kwargs: Any) -> int:
        raise SystemExit(code)

    monkeypatch.setattr(cli, "run_loop", _exiting)

    assert _run_main_with_watchdog(cli, ["--viewer"]) == expected_rc
    if expected_stderr:
        assert expected_stderr in capsys.readouterr().err
