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
import importlib.util
import json
import subprocess
import sys
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
