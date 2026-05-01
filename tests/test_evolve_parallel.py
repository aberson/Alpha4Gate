"""Tests for the parallel fitness-phase dispatcher in ``scripts/evolve.py``.

Step 3 of the evolve-parallelization plan
(``documentation/plans/evolve-parallelization-plan.md``). The dispatcher
fans the strict-serial fitness loop out across N concurrent worker
subprocesses (``scripts/evolve_worker.py``) under a configurable
``--concurrency N`` flag.

These tests deliberately do NOT spawn real ``python scripts/evolve_worker.py``
subprocesses — that's far too slow for unit tests, and exercising real SC2
is the operator's e2e action post-merge. Instead we substitute a
``_FakePopen`` for ``subprocess.Popen``: the fake schedules deterministic
exit codes and result-file writes so we can pin every branch of the
Decision-D-7 failure taxonomy, the out-of-order completion path, the
SIGINT propagation path, the budget-breach drain path, and the run_id
stale-file-cleanup path.

Coverage map (see plan §7 Step 3 done-when):

* ``test_concurrency_1_takes_serial_path`` — at concurrency=1 the
  dispatcher MUST NOT call ``subprocess.Popen``. The byte-identical
  promise that pairs with Decision D-1.
* ``test_out_of_order_completion`` — workers dispatched in 0,1,2 order;
  worker 2 finishes first, then 0, then 1. ``per_item_state`` and
  ``fitness_results`` still consistent.
* ``test_dispatch_fail_*`` — ``subprocess.Popen`` raises ``OSError``;
  ``fitness_counts["dispatch-fail"]`` increments.
* ``test_worker_crash_*`` — fake exits 1 with no result file;
  ``fitness_counts["crash"]`` increments.
* ``test_worker_malformed_*`` — fake exits 0, result file invalid JSON;
  ``fitness_counts["malformed"]`` increments.
* ``test_worker_hang_*`` — fake never exits within ``worker_timeout``;
  parent calls ``kill()``; ``fitness_counts["hang"]`` increments.
* ``test_sigint_propagates_*`` — installed SIGINT handler forwards to
  every in-flight Popen; second invocation escalates to ``kill()``.
* ``test_budget_breach_drains_inflight_*`` — wall-clock breach mid-flight
  flips ``stop_dispatching``; in-flight finish; no further Popen calls.
* ``test_run_id_unlinks_stale_round_files_*`` — pre-touch
  ``data/evolve_round_<n>.json`` files; ``_cleanup_stale_round_files``
  unlinks them.
* ``test_run_id_passed_to_workers`` — every spawned argv carries
  ``--run-id <hex>`` matching the parent's run_id.
* ``test_temp_files_cleaned_up`` — imp_json + result files unlinked
  after dispatch (success and crash paths).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from orchestrator.contracts import SelfPlayRecord
from orchestrator.evolve import FitnessResult, Improvement

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Module loader (mirrors the pattern in tests/test_evolve_cli.py)
# ---------------------------------------------------------------------------


def _load_cli_module() -> ModuleType:
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
# Fixture builders (parallel-friendly variants of test_evolve_cli helpers)
# ---------------------------------------------------------------------------


def _make_imp(title: str, *, rank: int = 1) -> Improvement:
    return Improvement(
        rank=rank,
        title=title,
        type=cast(Any, "dev"),
        description=f"{title} description",
        principle_ids=[],
        expected_impact=f"{title} impact",
        concrete_change=f"edit module_{rank}.py",
        files_touched=[],
    )


def _make_record(p1: str, p2: str, winner: str | None) -> SelfPlayRecord:
    return SelfPlayRecord(
        match_id="m",
        p1_version=p1,
        p2_version=p2,
        winner=winner,
        map_name="Simple64",
        duration_s=1.0,
        seat_swap=False,
        timestamp="2026-04-30T00:00:00+00:00",
        error=None,
    )


def _build_fitness_result(
    imp: Improvement,
    *,
    bucket: str,
    parent: str,
    candidate: str | None = None,
    games: int = 5,
) -> FitnessResult:
    candidate = candidate or f"cand_{imp.title}"
    wins = {"pass": 3, "close": 2, "fail": 1}[bucket]
    record = [
        _make_record(candidate, parent, candidate) for _ in range(wins)
    ] + [
        _make_record(candidate, parent, parent)
        for _ in range(games - wins)
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


def _build_args(
    tmp_path: Path,
    *,
    concurrency: int = 4,
    games_per_eval: int = 5,
    hard_timeout: float = 60.0,
    hours: float = 0.0,
) -> argparse.Namespace:
    return argparse.Namespace(
        pool_size=4,
        games_per_eval=games_per_eval,
        hours=hours,
        map="Simple64",
        game_time_limit=1800,
        hard_timeout=hard_timeout,
        no_commit=True,
        results_path=tmp_path / "evolve_results.jsonl",
        pool_path=tmp_path / "evolve_pool.json",
        state_path=tmp_path / "evolve_run_state.json",
        current_round_path=tmp_path / "evolve_current_round.json",
        crash_log_path=tmp_path / "evolve_crashes.jsonl",
        run_log=tmp_path / "run.md",
        resume=False,
        priors_path=None,
        post_training_cycles=0,
        backend_url="http://localhost:8765",
        concurrency=concurrency,
    )


# ---------------------------------------------------------------------------
# _FakePopen — deterministic stand-in for subprocess.Popen
# ---------------------------------------------------------------------------


@dataclass
class _FakeWorkerPlan:
    """How one fake worker should behave under poll().

    Each fake worker takes a number of poll() calls before "completing"
    (or it can be set to ``never_complete=True`` to model a hang). When
    it completes it writes ``result_payload`` to ``result_path`` (unless
    ``write_result=False``) and reports ``returncode``.
    """

    bucket: str = "pass"
    polls_before_complete: int = 1
    returncode: int = 0
    write_result: bool = True
    write_invalid: bool = False
    never_complete: bool = False
    # Iter-3 Fix 3.3: when set, the fake writes this dict to ``result_path``
    # on the completing poll regardless of ``returncode`` — used to model a
    # worker that exited nonzero AFTER writing a real ``_write_crash``
    # payload to the result file.
    crash_payload: dict[str, Any] | None = None


@dataclass
class _FakePopenLog:
    """Captured argv + lifecycle events for one fake worker."""

    argv: list[str]
    popen_kwargs: dict[str, Any] = field(default_factory=dict)
    poll_count: int = 0
    sent_signals: list[int] = field(default_factory=list)
    killed: bool = False


class _FakePopen:
    """Stand-in for subprocess.Popen used by the parallel dispatcher.

    Behavior is driven by a shared ``_FakePopenFactory`` that pops the
    next ``_FakeWorkerPlan`` per Popen() call. The factory also captures
    every spawned argv list so tests can pin the exact worker invocation.
    """

    def __init__(
        self,
        argv: list[str],
        *,
        plan: _FakeWorkerPlan,
        log: _FakePopenLog,
        cli_module: ModuleType,
    ) -> None:
        self._plan = plan
        self._log = log
        self._cli = cli_module
        self._argv = list(argv)
        # Iter-3 Fix 3.2: synthetic but unique pid so dispatcher code that
        # passes the fake to _sigkill_tree doesn't AttributeError. The real
        # killpg/taskkill syscalls swallow ProcessLookupError on this pid.
        self.pid = id(self) % 100000

    @property
    def argv(self) -> list[str]:
        return list(self._argv)

    def _maybe_write_result(self) -> None:
        if not self._plan.write_result:
            return
        # Find --result-path in argv.
        result_path: Path | None = None
        imp_json_path: Path | None = None
        run_id: str | None = None
        parent: str | None = None
        for i, tok in enumerate(self._argv):
            if tok == "--result-path":
                result_path = Path(self._argv[i + 1])
            elif tok == "--imp-json":
                imp_json_path = Path(self._argv[i + 1])
            elif tok == "--run-id":
                run_id = self._argv[i + 1]
            elif tok == "--parent":
                parent = self._argv[i + 1]
        assert result_path is not None
        if self._plan.write_invalid:
            result_path.write_text("not valid json {", encoding="utf-8")
            return
        # Read the imp from the dispatcher's staged --imp-json so the
        # result imp matches the imp the dispatcher dispatched.
        assert imp_json_path is not None
        imp_text = imp_json_path.read_text(encoding="utf-8")
        imp = Improvement.from_json(imp_text)
        assert parent is not None
        result = _build_fitness_result(
            imp, bucket=self._plan.bucket, parent=parent
        )
        result_path.write_text(result.to_json(), encoding="utf-8")
        # Fake the worker stamping run_id into a side log, just to prove
        # we received it.
        del run_id

    def poll(self) -> int | None:
        self._log.poll_count += 1
        if self._plan.never_complete:
            return None
        if self._log.poll_count < self._plan.polls_before_complete:
            return None
        # On the completing poll, write result file (if applicable) and
        # return the configured returncode.
        if self._log.poll_count == self._plan.polls_before_complete:
            if self._plan.returncode == 0:
                self._maybe_write_result()
            elif self._plan.crash_payload is not None:
                self._write_crash_payload(self._plan.crash_payload)
        return self._plan.returncode

    def _write_crash_payload(self, payload: dict[str, Any]) -> None:
        """Iter-3 Fix 3.3: emulate ``evolve_worker._write_crash``."""
        result_path: Path | None = None
        for i, tok in enumerate(self._argv):
            if tok == "--result-path":
                result_path = Path(self._argv[i + 1])
                break
        assert result_path is not None
        result_path.write_text(json.dumps(payload), encoding="utf-8")

    def send_signal(self, signum: int) -> None:
        self._log.sent_signals.append(signum)

    def kill(self) -> None:
        self._log.killed = True
        # After kill, .poll() should return a non-None code so the
        # dispatcher can drain the in-flight set. We force completion on
        # next poll, stamping the configured returncode (or 137 for hang
        # plans that haven't set one).
        self._plan.never_complete = False
        self._plan.polls_before_complete = max(
            self._log.poll_count + 1, self._plan.polls_before_complete
        )
        if self._plan.returncode == 0 and not self._plan.write_result:
            # Killed hang worker — make poll() report SIGKILL.
            self._plan.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        # Simple: just return the configured returncode (after kill it's
        # been set to a non-zero value by kill()).
        return (
            self._plan.returncode
            if self._plan.returncode != 0
            else -9
        )


class _FakePopenFactory:
    """Builds _FakePopen instances and tracks every call."""

    def __init__(
        self,
        cli_module: ModuleType,
        plans: list[_FakeWorkerPlan],
        *,
        raise_on_call: list[Exception | None] | None = None,
    ) -> None:
        self._cli = cli_module
        self._plans = list(plans)
        self._raises = list(raise_on_call) if raise_on_call else []
        self.call_count = 0
        self.logs: list[_FakePopenLog] = []
        self.popens: list[_FakePopen] = []

    def __call__(self, argv: list[str], *args: Any, **kwargs: Any) -> _FakePopen:
        self.call_count += 1
        if self._raises:
            exc = self._raises.pop(0)
            if exc is not None:
                raise exc
        if not self._plans:
            raise AssertionError(
                "FakePopenFactory: no more scripted plans for call "
                f"#{self.call_count}"
            )
        plan = self._plans.pop(0)
        log = _FakePopenLog(argv=list(argv), popen_kwargs=dict(kwargs))
        self.logs.append(log)
        popen = _FakePopen(
            argv, plan=plan, log=log, cli_module=self._cli
        )
        self.popens.append(popen)
        return popen


# ---------------------------------------------------------------------------
# Helper: invoke the dispatcher with mocked Popen
# ---------------------------------------------------------------------------


def _make_per_item_state(cli: ModuleType, n: int) -> dict[int, Any]:
    return {i: cli.PerItemState() for i in range(n)}


def _invoke_dispatcher(
    cli: ModuleType,
    *,
    pool: list[Improvement],
    args: argparse.Namespace,
    factory: _FakePopenFactory,
    time_fn: Any = None,
    poll_interval_s: float = 0.0,
    state_dir: Path | None = None,
) -> tuple[
    dict[int, FitnessResult],
    dict[str, int],
    dict[int, Any],
    Any,
    Any,
]:
    fitness_results: dict[int, FitnessResult] = {}
    fitness_counts = {"pass": 0, "close": 0, "fail": 0, "crash": 0}
    per_item_state = _make_per_item_state(cli, len(pool))
    write_state_calls: list[dict[str, Any]] = []

    def write_state_fn(**kwargs: Any) -> None:
        write_state_calls.append(kwargs)

    if time_fn is None:
        # Simple monotonic-ish counter so hang-detection works deterministically.
        clock = {"t": 0.0}

        def _clock() -> float:
            clock["t"] += 1.0
            return clock["t"]

        time_fn = _clock

    snap, stop_reason = cli._run_fitness_phase_parallel(
        active_idxs=list(range(len(pool))),
        pool=pool,
        per_item_state=per_item_state,
        fitness_results=fitness_results,
        fitness_counts=fitness_counts,
        parent_current="v0",
        parent_start="v0",
        pool_generated_at=cli._now_iso(),
        generation_index=1,
        generations_completed=0,
        generations_promoted=0,
        args=args,
        run_id="abc12345",
        write_state_fn=write_state_fn,
        time_fn=time_fn,
        start_monotonic=0.0,
        state_dir=state_dir if state_dir is not None else args.results_path.parent,
        popen_factory=factory,
        poll_interval_s=poll_interval_s,
    )
    return fitness_results, fitness_counts, per_item_state, snap, stop_reason


# ---------------------------------------------------------------------------
# 1. Concurrency=1 takes the serial path (Decision D-1, byte-identical)
# ---------------------------------------------------------------------------


def test_concurrency_1_takes_serial_path(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At --concurrency 1, the dispatcher MUST NOT spawn ``evolve_worker.py``.

    Pins Decision D-1: the soak-history baselines were captured against the
    serial code path. Diverging at concurrency=1 invalidates them as a
    comparison baseline. Implementation: wrap ``subprocess.Popen`` and
    fail the test the moment any call references ``evolve_worker.py`` —
    which is the only argv shape the parallel dispatcher emits. Other
    Popen calls (``git status``, etc.) pass through untouched so the rest
    of the run-loop pre-flight still works.
    """
    args = _build_args(tmp_path, concurrency=1)
    pool = [_make_imp("imp-0", rank=1), _make_imp("imp-1", rank=2)]

    real_popen = cli.subprocess.Popen
    worker_popen_calls: list[list[str]] = []

    def guarded_popen(argv: Any, *a: Any, **k: Any) -> Any:
        # Only fail on the parallel-dispatcher's argv shape (worker script
        # path). Any other subprocess call (git, etc.) passes through.
        argv_list = list(argv) if isinstance(argv, (list, tuple)) else [argv]
        if any("evolve_worker" in str(tok) for tok in argv_list):
            worker_popen_calls.append(argv_list)
            raise AssertionError(
                "Parallel dispatcher spawned a worker at concurrency=1 "
                f"(Decision D-1 violated): argv={argv_list!r}"
            )
        return real_popen(argv, *a, **k)

    monkeypatch.setattr(cli.subprocess, "Popen", guarded_popen)
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)

    # Scripted serial fitness so we can verify the serial path actually ran.
    fitness_calls: list[dict[str, Any]] = []

    def serial_fitness(
        parent: str, imp: Improvement, **kwargs: Any
    ) -> FitnessResult:
        fitness_calls.append({"parent": parent, "imp": imp.title})
        return _build_fitness_result(imp, bucket="fail", parent=parent)

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        return [] if k.get("skip_mirror") else pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=serial_fitness,
        stack_apply_fn=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no winners - stack-apply must not fire")
        ),
        run_regression_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    # Both pool items were evaluated serially.
    assert [c["imp"] for c in fitness_calls] == ["imp-0", "imp-1"]
    # No evolve_worker.py subprocess was ever spawned.
    assert worker_popen_calls == []


# ---------------------------------------------------------------------------
# 2. Out-of-order completion still updates state correctly
# ---------------------------------------------------------------------------


def test_out_of_order_completion(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Workers 0,1,2 dispatched in order; 2 finishes first, then 0, then 1.

    Pool/per-item state still consistent: every imp is bucketed; fitness_results
    keyed by ``idx`` (not by completion order).
    """
    args = _build_args(tmp_path, concurrency=3)
    pool = [_make_imp(f"imp-{i}", rank=i + 1) for i in range(3)]

    # plans[i] is for the i-th Popen() call (worker_id i).
    # Use polls_before_complete to force order: idx 2 completes before 0/1.
    plans = [
        _FakeWorkerPlan(bucket="pass", polls_before_complete=5),  # worker 0
        _FakeWorkerPlan(bucket="close", polls_before_complete=3),  # worker 1
        _FakeWorkerPlan(bucket="fail", polls_before_complete=1),  # worker 2
    ]
    factory = _FakePopenFactory(cli, plans)

    fitness_results, fitness_counts, per_item_state, snap, stop = _invoke_dispatcher(
        cli, pool=pool, args=args, factory=factory
    )
    # All three popens called.
    assert factory.call_count == 3
    # Buckets accounted.
    assert fitness_counts["pass"] == 1
    assert fitness_counts["close"] == 1
    assert fitness_counts["fail"] == 1
    # All three idxs got a result.
    assert set(fitness_results.keys()) == {0, 1, 2}
    # idx-keyed result mapping correctness.
    assert fitness_results[0].bucket == "pass"
    assert fitness_results[1].bucket == "close"
    assert fitness_results[2].bucket == "fail"
    # per_item_state.status reflects bucket outcome.
    assert per_item_state[0].status == cli._FITNESS_PASS
    assert per_item_state[1].status == cli._FITNESS_CLOSE
    assert per_item_state[2].status == cli._EVICTED
    assert stop is None
    assert snap is not None


# ---------------------------------------------------------------------------
# 3. dispatch-fail bucket (Popen() raises)
# ---------------------------------------------------------------------------


def test_dispatch_fail_increments_bucket(
    cli: ModuleType, tmp_path: Path
) -> None:
    """``subprocess.Popen()`` raising ``OSError`` lands in dispatch-fail."""
    args = _build_args(tmp_path, concurrency=2)
    pool = [_make_imp("imp-0", rank=1), _make_imp("imp-1", rank=2)]

    # First Popen call raises OSError; second succeeds with a pass.
    plans = [_FakeWorkerPlan(bucket="pass")]
    factory = _FakePopenFactory(
        cli,
        plans,
        raise_on_call=[OSError("simulated fork failure"), None],
    )

    _, fitness_counts, per_item_state, _, _ = _invoke_dispatcher(
        cli, pool=pool, args=args, factory=factory
    )
    # Both attempts made.
    assert factory.call_count == 2
    assert fitness_counts["dispatch-fail"] == 1
    assert fitness_counts["pass"] == 1
    # Failed imp is evicted with retry_count incremented.
    assert per_item_state[0].status == cli._EVICTED
    assert per_item_state[0].retry_count == 1
    # Successful imp lives.
    assert per_item_state[1].status == cli._FITNESS_PASS


def test_dispatch_fail_imp_json_stage_error(
    cli: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """imp_json staging ``OSError`` lands in dispatch-fail bucket.

    Pins the staging-OSError branch (Finding #7 from iter-1 review):
    the prior ``test_dispatch_fail_increments_bucket`` only exercised
    the Popen-raise branch. We monkey-patch ``Path.write_text`` to
    raise OSError specifically for ``evolve_imp_*.json`` paths so the
    dispatcher's pre-Popen staging step fails before any Popen call
    happens. Asserts:

    * ``fitness_counts["dispatch-fail"]`` increments.
    * ``per_item_state`` reflects ``_EVICTED`` + ``retry_count == 1``.
    * No ``popen_factory`` call (we never reached ``subprocess.Popen``).
    * No leaked ``evolve_imp_*.json`` file post-run (Finding #4 cleanup
      symmetry with the Popen-raise branch).
    """
    args = _build_args(tmp_path, concurrency=2)
    pool = [_make_imp("imp-stage-fail", rank=1)]
    # Empty plans list — if Popen is called we'll hit AssertionError
    # from the factory's "no more plans" guard, which is exactly the
    # behavior we want (a Popen call here is a regression).
    plans: list[_FakeWorkerPlan] = []
    factory = _FakePopenFactory(cli, plans)

    real_write_text = Path.write_text

    def write_text_raises_for_imp_json(
        self: Path, *a: Any, **k: Any
    ) -> int:
        if self.name.startswith("evolve_imp_") and self.name.endswith(
            ".json"
        ):
            raise OSError("simulated stage failure")
        return cast(int, real_write_text(self, *a, **k))

    monkeypatch.setattr(
        Path, "write_text", write_text_raises_for_imp_json
    )

    _, fitness_counts, per_item_state, _, _ = _invoke_dispatcher(
        cli, pool=pool, args=args, factory=factory, state_dir=tmp_path
    )

    # 1. Bucket counter incremented.
    assert fitness_counts["dispatch-fail"] == 1
    # 2. Imp evicted with retry_count incremented.
    assert per_item_state[0].status == cli._EVICTED
    assert per_item_state[0].retry_count == 1
    # 3. NO Popen call reached.
    assert factory.call_count == 0
    # 4. No leaked imp_json file (Finding #4 symmetry).
    leftover_imp = list(tmp_path.glob("evolve_imp_*.json"))
    assert leftover_imp == [], (
        f"imp_json staging failure leaked file(s): {leftover_imp!r}"
    )


# ---------------------------------------------------------------------------
# 4. crash bucket (worker exits non-zero)
# ---------------------------------------------------------------------------


def test_worker_crash_increments_bucket(
    cli: ModuleType, tmp_path: Path
) -> None:
    """A worker exiting 1 with no result-file lands in the crash bucket."""
    args = _build_args(tmp_path, concurrency=1)
    pool = [_make_imp("imp-crash", rank=1)]
    plans = [
        _FakeWorkerPlan(
            bucket="fail",
            returncode=1,
            write_result=False,
            polls_before_complete=1,
        )
    ]
    factory = _FakePopenFactory(cli, plans)

    # Need to run dispatcher even with concurrency=1 in args, so call
    # _run_fitness_phase_parallel directly (bypassing the run_loop branch).
    args.concurrency = 2  # so the dispatcher is willing to dispatch
    _, fitness_counts, per_item_state, _, _ = _invoke_dispatcher(
        cli, pool=pool, args=args, factory=factory
    )
    assert fitness_counts["crash"] == 1
    assert per_item_state[0].status == cli._EVICTED
    assert per_item_state[0].retry_count == 1
    # _record_parallel_failure must stamp last_evaluated_against so the
    # retry-against-current invariant holds (a future generation knows
    # which parent the imp was last benched against).
    assert per_item_state[0].last_evaluated_against == "v0"


# ---------------------------------------------------------------------------
# 5. malformed bucket (worker exits 0 but result JSON invalid)
# ---------------------------------------------------------------------------


def test_worker_malformed_increments_bucket(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Worker exits 0 but writes invalid JSON → malformed bucket."""
    args = _build_args(tmp_path, concurrency=2)
    pool = [_make_imp("imp-bad-json", rank=1)]
    plans = [
        _FakeWorkerPlan(
            bucket="pass", returncode=0, write_invalid=True
        )
    ]
    factory = _FakePopenFactory(cli, plans)

    _, fitness_counts, per_item_state, _, _ = _invoke_dispatcher(
        cli, pool=pool, args=args, factory=factory
    )
    assert fitness_counts["malformed"] == 1
    assert per_item_state[0].status == cli._EVICTED
    assert per_item_state[0].retry_count == 1


def test_worker_malformed_missing_result_file(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Worker exits 0 without writing the result file → malformed bucket."""
    args = _build_args(tmp_path, concurrency=2)
    pool = [_make_imp("imp-no-result", rank=1)]
    plans = [
        _FakeWorkerPlan(bucket="pass", returncode=0, write_result=False)
    ]
    factory = _FakePopenFactory(cli, plans)

    _, fitness_counts, per_item_state, _, _ = _invoke_dispatcher(
        cli, pool=pool, args=args, factory=factory
    )
    assert fitness_counts["malformed"] == 1
    assert per_item_state[0].status == cli._EVICTED


# ---------------------------------------------------------------------------
# 6. hang bucket (worker exceeds wall-clock cap)
# ---------------------------------------------------------------------------


def test_worker_hang_increments_bucket(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A never-completing worker is SIGKILLed and counted as hang."""
    args = _build_args(
        tmp_path,
        concurrency=2,
        games_per_eval=1,
        hard_timeout=2.0,  # × 1 × 1.5 = 3.0s hang cap
    )
    pool = [_make_imp("imp-hang", rank=1)]
    plans = [_FakeWorkerPlan(bucket="pass", never_complete=True)]
    factory = _FakePopenFactory(cli, plans)

    # Time fn that advances faster than the 3.0s cap so timeout trips
    # within a few polls.
    clock = {"t": 0.0}

    def fast_clock() -> float:
        clock["t"] += 5.0
        return clock["t"]

    # Iter-3 Fix 3.2: dispatcher now uses _sigkill_tree (killpg/taskkill on
    # the worker's process group). For the fake worker we don't have a real
    # process group, so substitute a wrapper that invokes the fake's
    # kill() — preserves the legacy assertion that escalation reaches the
    # in-flight worker.
    monkeypatch.setattr(
        cli, "_sigkill_tree", lambda proc: proc.kill()
    )

    _, fitness_counts, per_item_state, _, _ = _invoke_dispatcher(
        cli,
        pool=pool,
        args=args,
        factory=factory,
        time_fn=fast_clock,
    )
    assert fitness_counts["hang"] == 1
    assert per_item_state[0].status == cli._EVICTED
    assert per_item_state[0].retry_count == 1
    # The fake worker's kill() was called via _sigkill_tree.
    assert factory.logs[0].killed is True


# ---------------------------------------------------------------------------
# 7. SIGINT handler propagates to in-flight Popens; second escalates.
# ---------------------------------------------------------------------------


def test_sigint_propagates_then_escalates(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First SIGINT forwards via send_signal; second invocation calls kill()."""
    # Iter-3 Fix 3.2: second-SIGINT escalation now invokes _sigkill_tree
    # (killpg/taskkill on the worker's process group). Substitute a fake-
    # friendly wrapper that calls the fake's kill() instead, preserving
    # the existing log.killed assertion.
    monkeypatch.setattr(
        cli, "_sigkill_tree", lambda proc: proc.kill()
    )
    args = _build_args(tmp_path, concurrency=2)
    pool = [_make_imp("imp-0", rank=1), _make_imp("imp-1", rank=2)]
    # Plans that take many polls so the workers are in-flight when we fire
    # the signal handler manually.
    plans = [
        _FakeWorkerPlan(bucket="pass", polls_before_complete=100),
        _FakeWorkerPlan(bucket="pass", polls_before_complete=100),
    ]
    factory = _FakePopenFactory(cli, plans)

    # Drive the dispatcher one phase: dispatch both workers, then exit
    # the dispatcher early by patching _budget_exceeded — we want the
    # handler to be installed but the dispatcher not to actually loop
    # forever. Instead, we'll call the handler directly via
    # signal.getsignal() right after the dispatcher would have installed
    # it, but that's tricky. Cleaner approach: capture the registered
    # handler by monkey-patching signal.signal and grabbing the handler
    # reference.
    captured_handler: list[Any] = []
    real_signal = signal.signal

    def capturing_signal(sig: int, handler: Any) -> Any:
        if sig == signal.SIGINT and callable(handler):
            captured_handler.append(handler)
        return real_signal(sig, handler)

    import scripts.evolve as _ignore  # noqa: F401  (cli already loaded)

    # Use a clock + budget that flips us out fast.
    clock = {"t": 0.0}

    def fast_clock() -> float:
        clock["t"] += 1.0
        return clock["t"]

    # Override the dispatcher's signal install to capture handler then
    # immediately fire it twice on the captured handler.
    saved_signal = cli.signal.signal

    def capture_and_run(sig: int, handler: Any) -> Any:
        result = saved_signal(sig, handler)
        if sig == signal.SIGINT and callable(handler):
            captured_handler.append(handler)
        return result

    cli.signal.signal = capture_and_run  # type: ignore[assignment]
    try:
        # Use a budget=0 and high time so dispatcher exits quickly via
        # budget breach after dispatch.
        # Actually, with never-completing workers and budget breach we'd
        # spin forever. Instead: trigger handler by side-effect during a
        # poll. We do it via the _FakePopen.poll override.

        # Hook: after first poll on worker 0, fire the captured handler twice.
        original_poll = _FakePopen.poll
        fire_state = {"fired": False}

        def hooked_poll(self: _FakePopen) -> int | None:
            rv = original_poll(self)
            # Once both workers are in flight (both have been polled at
            # least once) and we haven't fired yet, fire signal handler.
            if (
                not fire_state["fired"]
                and len(captured_handler) > 0
                and len(factory.popens) >= 2
                and all(p._log.poll_count >= 1 for p in factory.popens)
            ):
                fire_state["fired"] = True
                handler = captured_handler[0]
                handler(signal.SIGINT, None)  # first
                handler(signal.SIGINT, None)  # second escalates
            return rv

        _FakePopen.poll = hooked_poll  # type: ignore[assignment]
        try:
            _invoke_dispatcher(
                cli, pool=pool, args=args, factory=factory, time_fn=fast_clock
            )
        finally:
            _FakePopen.poll = original_poll  # type: ignore[assignment]
    finally:
        cli.signal.signal = saved_signal  # type: ignore[assignment]

    # First handler call sent SIGINT to each; second escalated to kill().
    for log in factory.logs:
        assert signal.SIGINT in log.sent_signals
        assert log.killed is True


def test_sigint_second_interrupt_halts_dispatch_with_pending(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Second SIGINT MUST stop dispatch even when ``pending`` is non-empty.

    Sibling to ``test_sigint_propagates_then_escalates``. An operator
    pressing Ctrl+C twice expects the run to stop — not silently keep
    feeding new workers from the queue once in-flight drains. Pin
    Finding #1 from iter-1 review: with 4 imps + concurrency=2, after
    the second interrupt fires while 2 imps are in flight (and 2 still
    in pending), no further ``popen_factory`` calls happen.
    """
    args = _build_args(tmp_path, concurrency=2)
    pool = [_make_imp(f"imp-{i}", rank=i + 1) for i in range(4)]

    # First two workers: many polls so they're in-flight long enough.
    # Remaining workers should NEVER be popped — if they are, the
    # factory will hand out one of these plans and the test will see
    # call_count > 2.
    plans = [
        _FakeWorkerPlan(bucket="pass", polls_before_complete=100),
        _FakeWorkerPlan(bucket="pass", polls_before_complete=100),
        # Sentinels: if dispatcher dispatches a 3rd or 4th worker after
        # double-SIGINT, it'll consume one of these. The assertion
        # below catches the regression either way (call_count or
        # plans-remaining).
        _FakeWorkerPlan(bucket="pass", polls_before_complete=1),
        _FakeWorkerPlan(bucket="pass", polls_before_complete=1),
    ]
    factory = _FakePopenFactory(cli, plans)

    captured_handler: list[Any] = []
    saved_signal = cli.signal.signal

    def capture_and_run(sig: int, handler: Any) -> Any:
        result = saved_signal(sig, handler)
        if sig == signal.SIGINT and callable(handler):
            captured_handler.append(handler)
        return result

    cli.signal.signal = capture_and_run  # type: ignore[assignment]
    try:
        original_poll = _FakePopen.poll
        fire_state = {"fired": False}

        def hooked_poll(self: _FakePopen) -> int | None:
            rv = original_poll(self)
            # Once both initial workers are in flight, fire SIGINT
            # twice (escalate to halt-dispatch) on the captured handler.
            if (
                not fire_state["fired"]
                and len(captured_handler) > 0
                and len(factory.popens) >= 2
                and all(
                    p._log.poll_count >= 1 for p in factory.popens[:2]
                )
            ):
                fire_state["fired"] = True
                handler = captured_handler[0]
                handler(signal.SIGINT, None)  # 1st: forward
                handler(signal.SIGINT, None)  # 2nd: halt-dispatch + kill
            return rv

        _FakePopen.poll = hooked_poll  # type: ignore[assignment]
        try:
            _invoke_dispatcher(
                cli, pool=pool, args=args, factory=factory
            )
        finally:
            _FakePopen.poll = original_poll  # type: ignore[assignment]
    finally:
        cli.signal.signal = saved_signal  # type: ignore[assignment]

    # The crucial assertion: only the first 2 workers were ever
    # spawned, despite 4 imps in pending. Halt-dispatch fired before
    # the dispatcher could pop the remaining 2.
    assert factory.call_count == 2, (
        f"second SIGINT did not halt dispatch — "
        f"factory.call_count={factory.call_count} (expected 2). "
        "stop_dispatching flag or pending.clear() regression."
    )


# ---------------------------------------------------------------------------
# 8. Budget breach drains in-flight, no new dispatch (Decision D-5)
# ---------------------------------------------------------------------------


def test_budget_breach_drains_inflight_no_new_dispatch(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Wall-clock breach mid-flight: in-flight finish, no further Popen calls."""
    # 4 imps, concurrency 2. After dispatching the first 2, simulate the
    # budget exceeding so the remaining 2 are never dispatched.
    args = _build_args(
        tmp_path,
        concurrency=2,
        hours=1.0,  # nominal; we control via fake _budget_exceeded.
    )
    pool = [_make_imp(f"imp-{i}", rank=i + 1) for i in range(4)]
    plans = [
        _FakeWorkerPlan(bucket="pass", polls_before_complete=2),
        _FakeWorkerPlan(bucket="pass", polls_before_complete=2),
    ]
    factory = _FakePopenFactory(cli, plans)

    # Trip _budget_exceeded after the first two are in flight.
    poll_counter = {"n": 0}

    def fake_budget_exceeded(*a: Any, **k: Any) -> bool:
        poll_counter["n"] += 1
        # Allow the first two dispatches (factory.call_count gets to 2),
        # then return True forever.
        return factory.call_count >= 2

    import scripts  # noqa: F401  (placeholder to keep import surface stable)

    # Patch the module-level _budget_exceeded reference inside the cli
    # module. The dispatcher reads it via `from`-style reference.
    saved = cli._budget_exceeded
    cli._budget_exceeded = fake_budget_exceeded  # type: ignore[assignment]
    try:
        _, fitness_counts, _, _, stop_reason = _invoke_dispatcher(
            cli, pool=pool, args=args, factory=factory
        )
    finally:
        cli._budget_exceeded = saved  # type: ignore[assignment]

    # Only the first two workers ever spawned (no new dispatch after breach).
    assert factory.call_count == 2
    assert fitness_counts["pass"] == 2
    assert stop_reason == "wall-clock"


# ---------------------------------------------------------------------------
# 9. run_id startup cleanup (Decision D-6)
# ---------------------------------------------------------------------------


def test_cleanup_stale_round_files_unlinks_pre_existing(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Pre-existing per-worker files and ``cand_*`` dirs are cleaned at startup."""
    # Pre-touch some stale slot files in the state dir.
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    stale_0 = state_dir / "evolve_round_0.json"
    stale_99 = state_dir / "evolve_round_99.json"
    stale_0.write_text('{"stale": "yes"}', encoding="utf-8")
    stale_99.write_text('{"stale": "old run"}', encoding="utf-8")
    # Unrelated file should NOT be unlinked.
    keep = state_dir / "evolve_run_state.json"
    keep.write_text("{}", encoding="utf-8")

    # Iter-3 Fix 3.1: pre-create orphaned cand_* scratch dirs in a separate
    # bots dir. Each contains a file so we can verify rmtree (not just
    # rmdir) succeeds.
    bots_dir = tmp_path / "bots"
    bots_dir.mkdir(parents=True, exist_ok=True)
    cand_a = bots_dir / "cand_a1b2"
    cand_b = bots_dir / "cand_dead1"
    cand_a.mkdir()
    cand_b.mkdir()
    (cand_a / "bot.py").write_text("# leftover", encoding="utf-8")
    (cand_b / "bot.py").write_text("# leftover", encoding="utf-8")

    n = cli._cleanup_stale_round_files(state_dir, bots_dir=bots_dir)
    assert n == 4  # 2 round files + 2 cand dirs
    assert not stale_0.exists()
    assert not stale_99.exists()
    assert keep.exists()
    assert not cand_a.exists()
    assert not cand_b.exists()


def test_cleanup_stale_round_files_handles_missing_dir(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Missing state_dir returns 0 with no error."""
    nonexistent = tmp_path / "does_not_exist"
    assert cli._cleanup_stale_round_files(
        nonexistent, bots_dir=tmp_path / "no_bots"
    ) == 0


def test_cleanup_preserves_versioned_dirs(
    cli: ModuleType, tmp_path: Path
) -> None:
    """``bots/v<N>/`` dirs must NEVER be rmtree'd — only ``cand_*`` matches."""
    bots_dir = tmp_path / "bots"
    bots_dir.mkdir()
    # Pre-create three versioned dirs with content.
    for name in ("v0", "v7", "v99"):
        d = bots_dir / name
        d.mkdir()
        (d / "bot.py").write_text(f"# {name}", encoding="utf-8")
    # And one cand_ dir that SHOULD be cleaned.
    cand = bots_dir / "cand_aaaa1111"
    cand.mkdir()
    (cand / "bot.py").write_text("# scratch", encoding="utf-8")

    n = cli._cleanup_stale_round_files(tmp_path / "missing_state", bots_dir=bots_dir)
    assert n == 1
    for name in ("v0", "v7", "v99"):
        d = bots_dir / name
        assert d.is_dir(), f"versioned dir {name} was wrongly removed"
        assert (d / "bot.py").is_file()
    assert not cand.exists()


# ---------------------------------------------------------------------------
# 10. run_id passed to every spawned worker
# ---------------------------------------------------------------------------


def test_run_id_passed_to_workers(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Every Popen argv contains ``--run-id <hex>`` matching the parent."""
    args = _build_args(tmp_path, concurrency=2)
    pool = [_make_imp(f"imp-{i}", rank=i + 1) for i in range(3)]
    plans = [_FakeWorkerPlan(bucket="pass") for _ in range(3)]
    factory = _FakePopenFactory(cli, plans)

    _invoke_dispatcher(cli, pool=pool, args=args, factory=factory)

    assert len(factory.logs) == 3
    for log in factory.logs:
        assert "--run-id" in log.argv
        idx = log.argv.index("--run-id")
        assert log.argv[idx + 1] == "abc12345"


# ---------------------------------------------------------------------------
# 11. Temp file cleanup on success and crash paths
# ---------------------------------------------------------------------------


def test_temp_files_cleaned_up_on_success(
    cli: ModuleType, tmp_path: Path
) -> None:
    """imp_json + result files unlinked after worker completes."""
    args = _build_args(tmp_path, concurrency=1)
    args.concurrency = 2
    pool = [_make_imp("imp-clean", rank=1)]
    plans = [_FakeWorkerPlan(bucket="pass")]
    factory = _FakePopenFactory(cli, plans)

    _invoke_dispatcher(cli, pool=pool, args=args, factory=factory, state_dir=tmp_path)

    # No leftover staging files in state_dir.
    leftover = list(tmp_path.glob("evolve_imp_*.json")) + list(
        tmp_path.glob("evolve_result_*.json")
    )
    assert leftover == []


def test_temp_files_cleaned_up_on_crash(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Crash path also cleans imp_json + result files."""
    args = _build_args(tmp_path, concurrency=2)
    pool = [_make_imp("imp-crash", rank=1)]
    plans = [
        _FakeWorkerPlan(bucket="fail", returncode=1, write_result=False)
    ]
    factory = _FakePopenFactory(cli, plans)

    _invoke_dispatcher(cli, pool=pool, args=args, factory=factory, state_dir=tmp_path)

    leftover = list(tmp_path.glob("evolve_imp_*.json")) + list(
        tmp_path.glob("evolve_result_*.json")
    )
    assert leftover == []


# ---------------------------------------------------------------------------
# 12. argparse smoke: --concurrency flag is present and defaults to 1
# ---------------------------------------------------------------------------


def test_concurrency_flag_default_and_parse(cli: ModuleType) -> None:
    """``--concurrency`` defaults to 1 (Decision D-1: byte-identical at
    concurrency=1) AND parses an explicit integer.

    Collapsed from two argparse smoke tests in iter-1; one assertion of
    each behavior is sufficient pinning for one flag.
    """
    args_default = cli.build_parser().parse_args([])
    assert args_default.concurrency == 1
    args_explicit = cli.build_parser().parse_args(["--concurrency", "4"])
    assert args_explicit.concurrency == 4


# ---------------------------------------------------------------------------
# 13. Pin the fitness-row + crash-row jsonl side-effects on parallel paths
# ---------------------------------------------------------------------------


def test_parallel_success_appends_fitness_row(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Successful parallel completion appends a phase=fitness row."""
    args = _build_args(tmp_path, concurrency=2)
    pool = [_make_imp("imp-pass", rank=1)]
    plans = [_FakeWorkerPlan(bucket="pass")]
    factory = _FakePopenFactory(cli, plans)

    _invoke_dispatcher(cli, pool=pool, args=args, factory=factory)

    rows = [
        json.loads(line)
        for line in args.results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["phase"] == "fitness"
    assert rows[0]["outcome"] == "fitness-pass"


def test_parallel_success_writes_pool_state_and_run_state(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Plan §5: parallel success path MUST call ``write_pool_state`` AND
    ``write_state_fn`` so the dashboard's pool-state file and run-state
    JSON stay live during a long-running parallel fitness phase. A
    regression dropping either silently is currently invisible — pin
    both side-effects here.
    """
    args = _build_args(tmp_path, concurrency=2)
    pool = [_make_imp("imp-pass", rank=1)]
    plans = [_FakeWorkerPlan(bucket="pass")]
    factory = _FakePopenFactory(cli, plans)

    fitness_results: dict[int, FitnessResult] = {}
    fitness_counts = {"pass": 0, "close": 0, "fail": 0, "crash": 0}
    per_item_state = _make_per_item_state(cli, len(pool))
    write_state_calls: list[dict[str, Any]] = []

    def write_state_fn(**kwargs: Any) -> None:
        write_state_calls.append(kwargs)

    clock = {"t": 0.0}

    def _clock() -> float:
        clock["t"] += 1.0
        return clock["t"]

    cli._run_fitness_phase_parallel(
        active_idxs=list(range(len(pool))),
        pool=pool,
        per_item_state=per_item_state,
        fitness_results=fitness_results,
        fitness_counts=fitness_counts,
        parent_current="v0",
        parent_start="v0",
        pool_generated_at=cli._now_iso(),
        generation_index=1,
        generations_completed=0,
        generations_promoted=0,
        args=args,
        run_id="abc12345",
        write_state_fn=write_state_fn,
        time_fn=_clock,
        start_monotonic=0.0,
        state_dir=tmp_path,
        popen_factory=factory,
        poll_interval_s=0.0,
    )

    # 1. write_pool_state side-effect: pool file written to disk.
    assert args.pool_path.exists(), (
        "write_pool_state did not run on parallel success — pool file "
        "missing post-dispatch"
    )
    pool_payload = json.loads(args.pool_path.read_text(encoding="utf-8"))
    assert "pool" in pool_payload
    assert pool_payload["generation"] == 1

    # 2. write_state_fn called exactly once per success (one imp here).
    assert len(write_state_calls) == 1, (
        f"expected 1 write_state_fn call, got {len(write_state_calls)}: "
        f"{write_state_calls!r}"
    )
    call = write_state_calls[0]
    assert call["status"] == "running"
    assert call["generation_index"] == 1
    assert call["generations_completed"] == 0
    assert call["generations_promoted"] == 0
    assert call["last_result"] is not None
    assert call["pool"] is pool
    assert call["per_item_state"] is per_item_state


def test_parallel_crash_appends_crash_row(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Crash bucket appends a phase=fitness, outcome=crash row + crash log."""
    args = _build_args(tmp_path, concurrency=2)
    pool = [_make_imp("imp-crash", rank=1)]
    plans = [
        _FakeWorkerPlan(bucket="fail", returncode=1, write_result=False)
    ]
    factory = _FakePopenFactory(cli, plans)

    _invoke_dispatcher(cli, pool=pool, args=args, factory=factory)

    rows = [
        json.loads(line)
        for line in args.results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["phase"] == "fitness"
    assert rows[0]["outcome"] == "crash"
    crashes = [
        json.loads(line)
        for line in args.crash_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(crashes) == 1
    assert crashes[0]["phase"] == "fitness"


# ---------------------------------------------------------------------------
# Iter-3 Fix 3.2: worker subprocess group isolation
# ---------------------------------------------------------------------------


def test_popen_kwargs_isolate_process_group(
    cli: ModuleType, tmp_path: Path
) -> None:
    """Every dispatched worker is spawned with platform-appropriate group kwargs.

    POSIX: ``start_new_session=True`` (setsid in child).
    Windows: ``creationflags=CREATE_NEW_PROCESS_GROUP``.

    These are the operational pre-condition for ``_sigkill_tree`` —
    without them, ``os.killpg`` / ``taskkill /T`` cannot reach the
    claude CLI + SC2_x64 grandchildren.
    """
    args = _build_args(tmp_path, concurrency=2)
    pool = [_make_imp("imp-0", rank=1), _make_imp("imp-1", rank=2)]
    plans = [_FakeWorkerPlan(bucket="pass") for _ in range(2)]
    factory = _FakePopenFactory(cli, plans)

    _invoke_dispatcher(cli, pool=pool, args=args, factory=factory)

    assert len(factory.logs) == 2
    for log in factory.logs:
        if sys.platform == "win32":
            assert (
                log.popen_kwargs.get("creationflags")
                == subprocess.CREATE_NEW_PROCESS_GROUP
            ), f"missing CREATE_NEW_PROCESS_GROUP: {log.popen_kwargs}"
        else:
            assert log.popen_kwargs.get("start_new_session") is True, (
                f"missing start_new_session=True: {log.popen_kwargs}"
            )


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX-only killpg behavior"
)
def test_sigkill_tree_calls_killpg_on_posix(
    cli: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_sigkill_tree`` resolves the worker's pgid then SIGKILLs the group."""
    calls: list[tuple[int, int]] = []
    pgid_lookups: list[int] = []

    def fake_killpg(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))

    def fake_getpgid(pid: int) -> int:
        pgid_lookups.append(pid)
        return 12345

    monkeypatch.setattr(os, "killpg", fake_killpg)
    monkeypatch.setattr(os, "getpgid", fake_getpgid)

    class FakeProc:
        pid = 99

    cli._sigkill_tree(FakeProc())
    assert pgid_lookups == [99]
    assert calls == [(12345, signal.SIGKILL)]


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX-only killpg behavior"
)
def test_sigkill_tree_swallows_process_lookup_error_on_posix(
    cli: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Already-dead worker (ProcessLookupError on getpgid) does not raise."""

    def fake_getpgid(pid: int) -> int:
        raise ProcessLookupError(f"no such process {pid}")

    killpg_called = []

    def fake_killpg(pgid: int, sig: int) -> None:  # pragma: no cover
        killpg_called.append((pgid, sig))

    monkeypatch.setattr(os, "getpgid", fake_getpgid)
    monkeypatch.setattr(os, "killpg", fake_killpg)

    class FakeProc:
        pid = 99

    cli._sigkill_tree(FakeProc())  # must not raise
    assert killpg_called == []  # never reached


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows-only taskkill behavior"
)
def test_sigkill_tree_calls_taskkill_on_windows(
    cli: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_sigkill_tree`` runs ``taskkill /T /F /PID <pid>`` on Windows."""
    runs: list[list[str]] = []

    def fake_run(
        argv: list[str], **_kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        runs.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    class FakeProc:
        pid = 99
        sent: list[int] = []

        def send_signal(self, sig: int) -> None:
            self.sent.append(sig)

    proc = FakeProc()
    cli._sigkill_tree(proc)
    assert any(
        argv[:2] == ["taskkill", "/T"] and "/F" in argv and "99" in argv
        for argv in runs
    ), f"taskkill argv not found in: {runs}"


# ---------------------------------------------------------------------------
# Iter-3 Fix 3.3: worker crash payload surfaces in dispatcher's crash log
# ---------------------------------------------------------------------------


def test_worker_crash_payload_surfaces_in_log(
    cli: ModuleType, tmp_path: Path
) -> None:
    """When a worker writes ``_write_crash`` then exits nonzero, the
    dispatcher's ``data/evolve_crashes.jsonl`` row carries the worker's
    real ``traceback`` + ``error_type`` (not the synthetic ``RuntimeError``
    produced from just the exit code).

    Without this, the result file is unlinked in the reaper before any
    diagnosis is possible — the operator sees only ``returncode=N``.
    """
    args = _build_args(tmp_path, concurrency=2)
    pool = [_make_imp("imp-crash", rank=1)]
    fake_worker_tb = (
        "Traceback (most recent call last):\n"
        '  File "scripts/evolve_worker.py", line 99, in <module>\n'
        "    raise NotImplementedError(\"unrolled by worker\")\n"
        "NotImplementedError: unrolled by worker\n"
    )
    plans = [
        _FakeWorkerPlan(
            bucket="fail",
            returncode=1,
            write_result=False,
            crash_payload={
                "crash": True,
                "error_type": "NotImplementedError",
                "error_message": "unrolled by worker",
                "traceback": fake_worker_tb,
            },
        )
    ]
    factory = _FakePopenFactory(cli, plans)

    _invoke_dispatcher(cli, pool=pool, args=args, factory=factory)

    crashes = [
        json.loads(line)
        for line in args.crash_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(crashes) == 1
    row = crashes[0]
    assert row["phase"] == "fitness"
    # The dispatcher's synthetic exc message now includes the worker's
    # real type + message, not just the returncode.
    assert "NotImplementedError" in row["error_message"]
    assert "unrolled by worker" in row["error_message"]
    # And the worker's real traceback is preserved verbatim.
    assert row["worker_traceback"] == fake_worker_tb
    # Sanity: dispatcher's own error_type stays RuntimeError (the synthetic
    # exception class), but error_message now carries the worker's type
    # via _make_crash_exc folding it in.
    assert row["error_type"] == "RuntimeError"


def test_worker_crash_no_payload_falls_back_to_synthetic(
    cli: ModuleType, tmp_path: Path
) -> None:
    """When a worker exits nonzero WITHOUT writing a crash payload (e.g. SIGKILL
    by the OS, segfault), the dispatcher records the legacy synthetic exception
    and ``worker_traceback`` is absent from the JSONL row."""
    args = _build_args(tmp_path, concurrency=2)
    pool = [_make_imp("imp-segfault", rank=1)]
    plans = [
        _FakeWorkerPlan(
            bucket="fail", returncode=139, write_result=False
        )
    ]
    factory = _FakePopenFactory(cli, plans)

    _invoke_dispatcher(cli, pool=pool, args=args, factory=factory)

    crashes = [
        json.loads(line)
        for line in args.crash_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(crashes) == 1
    row = crashes[0]
    assert row["error_type"] == "RuntimeError"
    assert "returncode=139" in row["error_message"]
    assert "worker_traceback" not in row
