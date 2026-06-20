"""Tests for ``orchestrator.baselines`` + the baseline gauntlet (Phase EL Step 2).

Covers:
- :class:`Baseline` json round-trip + ``from_dict`` optional-field defaults.
- Registry I/O round-trip (``load_baselines`` / ``write_baselines``).
- :func:`register_baseline` happy path + unknown-version rejection (the
  ``list_versions`` seam is monkeypatched).
- :func:`run_baseline_gauntlet` aggregation across 2-3 baselines with a
  mocked ``run_batch_fn``, plus the empty-baselines case.
- ``scripts/evolve.py``: ``--fitness-mode parent`` never invokes the
  gauntlet seam; ``--fitness-mode both`` DOES after a promotion.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from orchestrator import baselines as baselines_mod
from orchestrator.baselines import (
    Baseline,
    default_baselines_path,
    load_baselines,
    register_baseline,
    write_baselines,
)
from orchestrator.contracts import SelfPlayRecord
from orchestrator.evolve import (
    FitnessResult,
    GauntletResult,
    Improvement,
    RegressionResult,
    run_baseline_gauntlet,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_baseline(name: str, version: str, note: str = "") -> Baseline:
    return Baseline(
        name=name,
        version=version,
        added_at="2026-06-19T00:00:00+00:00",
        note=note,
    )


def _rec(p1: str, p2: str, winner: str | None) -> SelfPlayRecord:
    return SelfPlayRecord(
        match_id="m",
        p1_version=p1,
        p2_version=p2,
        winner=winner,
        map_name="Simple64",
        duration_s=10.0,
        seat_swap=False,
        timestamp="2026-06-19T00:00:00+00:00",
        error=None,
    )


# ---------------------------------------------------------------------------
# Dataclass json helpers
# ---------------------------------------------------------------------------


def test_baseline_json_round_trip() -> None:
    base = _make_baseline("v7-strong", "v7", note="strong v7 reference")
    restored = Baseline.from_json(base.to_json())
    assert restored == base


def test_baseline_from_dict_fills_optional_defaults() -> None:
    base = Baseline.from_dict({"name": "early-rush", "version": "v3"})
    assert base.name == "early-rush"
    assert base.version == "v3"
    assert base.note == ""
    assert base.added_at  # default factory stamped a timestamp


# ---------------------------------------------------------------------------
# Registry round-trip
# ---------------------------------------------------------------------------


def test_registry_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "baselines.json"
    registry = {
        "v7-strong": _make_baseline("v7-strong", "v7", note="a"),
        "early-rush": _make_baseline("early-rush", "v3"),
    }
    write_baselines(path, registry)
    loaded = load_baselines(path)
    assert loaded == registry
    # On-disk shape is a JSON object keyed by name.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw.keys()) == {"v7-strong", "early-rush"}
    assert raw["v7-strong"]["version"] == "v7"


def test_load_baselines_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_baselines(tmp_path / "nope.json") == {}


def test_load_baselines_blank_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "baselines.json"
    path.write_text("   \n", encoding="utf-8")
    assert load_baselines(path) == {}


def test_load_baselines_rejects_non_object_top_level(tmp_path: Path) -> None:
    path = tmp_path / "baselines.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        load_baselines(path)


def test_load_baselines_missing_version_raises_value_error(
    tmp_path: Path,
) -> None:
    # An entry missing the required 'version' key must raise ValueError,
    # NOT KeyError — so the run-loop guard (which catches ValueError)
    # degrades gracefully instead of aborting the whole evolve run.
    path = tmp_path / "baselines.json"
    path.write_text(
        json.dumps({"v7-strong": {"note": "missing version"}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing required field 'version'"):
        load_baselines(path)


def test_from_dict_missing_name_raises_value_error() -> None:
    with pytest.raises(ValueError, match="missing required field 'name'"):
        Baseline.from_dict({"version": "v7"})


def test_load_baselines_key_is_authoritative(tmp_path: Path) -> None:
    # Entry value omits 'name'; the registry key fills it in.
    path = tmp_path / "baselines.json"
    path.write_text(
        json.dumps({"v7-strong": {"version": "v7"}}), encoding="utf-8"
    )
    loaded = load_baselines(path)
    assert loaded["v7-strong"].name == "v7-strong"
    assert loaded["v7-strong"].version == "v7"


def test_default_baselines_path_points_at_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(baselines_mod, "_repo_root", lambda: tmp_path)
    assert default_baselines_path() == tmp_path / "data" / "baselines.json"


# ---------------------------------------------------------------------------
# register_baseline
# ---------------------------------------------------------------------------


def test_register_baseline_adds_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        baselines_mod, "list_versions", lambda: ["v0", "v7"]
    )
    path = tmp_path / "baselines.json"
    base = register_baseline(path, "v7-strong", "v7", note="ref")
    assert base.name == "v7-strong"
    assert base.version == "v7"
    assert base.note == "ref"
    loaded = load_baselines(path)
    assert loaded["v7-strong"].version == "v7"


def test_register_baseline_updates_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        baselines_mod, "list_versions", lambda: ["v0", "v7", "v9"]
    )
    path = tmp_path / "baselines.json"
    register_baseline(path, "panel", "v7")
    register_baseline(path, "panel", "v9", note="bumped")
    loaded = load_baselines(path)
    assert len(loaded) == 1
    assert loaded["panel"].version == "v9"
    assert loaded["panel"].note == "bumped"


def test_register_baseline_rejects_unknown_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(baselines_mod, "list_versions", lambda: ["v0", "v7"])
    path = tmp_path / "baselines.json"
    with pytest.raises(ValueError, match="not a registered version"):
        register_baseline(path, "ghost", "v99")
    # Nothing was written.
    assert not path.exists()


def test_register_baseline_rejects_empty_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(baselines_mod, "list_versions", lambda: ["v0"])
    with pytest.raises(ValueError, match="name must be a non-empty"):
        register_baseline(tmp_path / "b.json", "", "v0")


# ---------------------------------------------------------------------------
# run_baseline_gauntlet
# ---------------------------------------------------------------------------


def test_gauntlet_empty_baselines_returns_zero() -> None:
    def _run_batch(*a: Any, **k: Any) -> list[SelfPlayRecord]:
        raise AssertionError("run_batch must not be called with no baselines")

    result = run_baseline_gauntlet(
        "v8", [], games_each=5, run_batch_fn=_run_batch
    )
    assert isinstance(result, GauntletResult)
    assert result.per_baseline == {}
    assert result.mean_win_rate == 0.0
    assert result.record == []


def test_gauntlet_aggregates_win_rates_across_baselines() -> None:
    # candidate v8 vs three baselines, 4 games each.
    # b1 (v3): wins 3/4 → 0.75
    # b2 (v5): wins 1/4 → 0.25
    # b3 (v7): wins 2/4 → 0.50 (one draw counted as a loss for the rate)
    games = 4
    plan: dict[str, list[SelfPlayRecord]] = {
        "v3": (
            [_rec("v8", "v3", "v8")] * 3 + [_rec("v8", "v3", "v3")] * 1
        ),
        "v5": (
            [_rec("v8", "v5", "v8")] * 1 + [_rec("v8", "v5", "v5")] * 3
        ),
        "v7": (
            [_rec("v8", "v7", "v8")] * 2
            + [_rec("v8", "v7", "v7")] * 1
            + [_rec("v8", "v7", None)] * 1
        ),
    }
    calls: list[tuple[str, str, int]] = []

    def _run_batch(
        p1: str, p2: str, n: int, map_name: str = "Simple64", **k: Any
    ) -> list[SelfPlayRecord]:
        calls.append((p1, p2, n))
        return plan[p2]

    baselines = [
        _make_baseline("b1", "v3"),
        _make_baseline("b2", "v5"),
        _make_baseline("b3", "v7"),
    ]
    result = run_baseline_gauntlet(
        "v8", baselines, games_each=games, run_batch_fn=_run_batch
    )
    assert result.per_baseline == {"b1": 0.75, "b2": 0.25, "b3": 0.5}
    assert result.mean_win_rate == pytest.approx((0.75 + 0.25 + 0.5) / 3)
    # One run_batch per baseline, candidate always p1.
    assert [c[0] for c in calls] == ["v8", "v8", "v8"]
    assert [c[1] for c in calls] == ["v3", "v5", "v7"]
    assert all(c[2] == games for c in calls)
    # record is the flat concatenation of all baselines' games.
    assert len(result.record) == games * 3


def test_gauntlet_skips_self_version() -> None:
    # A baseline whose version IS the candidate is skipped and excluded
    # from the mean.
    def _run_batch(
        p1: str, p2: str, n: int, map_name: str = "Simple64", **k: Any
    ) -> list[SelfPlayRecord]:
        return [_rec(p1, p2, p1)] * n  # candidate sweeps

    baselines = [
        _make_baseline("self", "v8"),  # skipped
        _make_baseline("real", "v3"),
    ]
    result = run_baseline_gauntlet(
        "v8", baselines, games_each=2, run_batch_fn=_run_batch
    )
    assert "self" not in result.per_baseline
    assert result.per_baseline == {"real": 1.0}
    assert result.mean_win_rate == 1.0


# ---------------------------------------------------------------------------
# scripts/evolve.py --fitness-mode wiring
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


def _make_imp(title: str, rank: int) -> Improvement:
    return Improvement(
        rank=rank,
        title=title,
        type=cast(Any, "dev"),
        description=f"{title} desc",
        principle_ids=[],
        expected_impact=f"{title} impact",
        concrete_change=f"edit module_{rank}.py",
        files_touched=[],
    )


def _fitness(imp: Improvement, *, bucket: str, parent: str) -> FitnessResult:
    wins = {"pass": 3, "close": 2, "fail": 1}[bucket]
    games = 5
    cand = f"cand_{imp.title}"
    record = [_rec(cand, parent, cand) for _ in range(wins)] + [
        _rec(cand, parent, parent) for _ in range(games - wins)
    ]
    return FitnessResult(
        parent=parent,
        candidate=cand,
        imp=imp,
        record=record,
        wins_candidate=wins,
        wins_parent=games - wins,
        games=games,
        bucket=cast(Any, bucket),
        reason=f"fitness {bucket}",
    )


def _regression(*, new_parent: str, prior_parent: str) -> RegressionResult:
    record = [_rec(new_parent, prior_parent, new_parent) for _ in range(3)] + [
        _rec(new_parent, prior_parent, prior_parent) for _ in range(2)
    ]
    return RegressionResult(
        new_parent=new_parent,
        prior_parent=prior_parent,
        record=record,
        wins_new=3,
        wins_prior=2,
        games=5,
        rolled_back=False,
        reason="regression pass",
    )


class _ScriptedFitness:
    def __init__(self, buckets: list[str]) -> None:
        self._buckets = list(buckets)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, parent: str, imp: Improvement, **kwargs: Any
    ) -> FitnessResult:
        self.calls.append({"parent": parent, "imp": imp})
        return _fitness(imp, bucket=self._buckets.pop(0), parent=parent)


class _ScriptedStackApply:
    def __init__(self, cli: ModuleType, plan: list[tuple[bool, str | None]]):
        self._cli = cli
        self._plan = list(plan)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, parent: str, winning_imps: list[Improvement], **kwargs: Any
    ) -> Any:
        self.calls.append({"parent": parent, "winning_imps": list(winning_imps)})
        promoted, new_version = self._plan.pop(0)
        return self._cli.StackApplyOutcome(
            parent=parent,
            stacked_imps=list(winning_imps),
            new_version=new_version if promoted else None,
            promote_sha=None,
            promoted=promoted,
            outcome="stack-apply-pass" if promoted else "stack-apply-import-fail",
            reason="scripted",
        )


def _build_args(
    tmp_path: Path, *, fitness_mode: str = "parent"
) -> argparse.Namespace:
    return argparse.Namespace(
        pool_size=2,
        games_per_eval=5,
        hours=0.0,
        generations=1,
        map="Simple64",
        game_time_limit=1800,
        hard_timeout=2700.0,
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
        concurrency=1,
        fitness_mode=fitness_mode,
    )


def test_fitness_mode_parent_never_calls_gauntlet(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default 'parent' mode must never invoke the gauntlet seam."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    args = _build_args(tmp_path, fitness_mode="parent")
    pool = [_make_imp("imp-0", 1), _make_imp("imp-1", 2)]

    fitness = _ScriptedFitness(["pass", "pass"])
    stack_apply = _ScriptedStackApply(cli, [(True, "v1")])

    gauntlet_calls: list[Any] = []

    def _gauntlet(*a: Any, **k: Any) -> Any:
        gauntlet_calls.append((a, k))
        raise AssertionError("gauntlet must not run in parent mode")

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        return [] if k.get("skip_mirror") else pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=lambda np, pp, **k: _regression(
            new_parent=np, prior_parent=pp
        ),
        run_gauntlet_fn=_gauntlet,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    assert gauntlet_calls == []


def test_fitness_mode_both_calls_gauntlet_after_promotion(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'both' mode runs the gauntlet for the promoted version."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    # Make load_baselines (called inside run_loop) return one baseline.
    import orchestrator.baselines as _bl

    monkeypatch.setattr(
        _bl, "load_baselines", lambda _p: {"b1": _make_baseline("b1", "v0")}
    )

    args = _build_args(tmp_path, fitness_mode="both")
    pool = [_make_imp("imp-0", 1), _make_imp("imp-1", 2)]

    fitness = _ScriptedFitness(["pass", "pass"])
    stack_apply = _ScriptedStackApply(cli, [(True, "v1")])

    gauntlet_calls: list[dict[str, Any]] = []

    def _gauntlet(
        candidate: str, baselines: list[Baseline], **k: Any
    ) -> GauntletResult:
        gauntlet_calls.append({"candidate": candidate, "baselines": baselines})
        return GauntletResult(
            candidate=candidate,
            per_baseline={"b1": 0.6},
            mean_win_rate=0.6,
            games_each=k.get("games_each", 5),
            record=[],
        )

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        return [] if k.get("skip_mirror") else pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=lambda np, pp, **k: _regression(
            new_parent=np, prior_parent=pp
        ),
        run_gauntlet_fn=_gauntlet,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    # Gauntlet ran exactly once, with the promoted version v1.
    assert len(gauntlet_calls) == 1
    assert gauntlet_calls[0]["candidate"] == "v1"
    assert [b.name for b in gauntlet_calls[0]["baselines"]] == ["b1"]
    # A gauntlet row was appended to the results file.
    rows = [
        json.loads(line)
        for line in args.results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    gauntlet_rows = [r for r in rows if r.get("phase") == "gauntlet"]
    assert len(gauntlet_rows) == 1
    assert gauntlet_rows[0]["candidate"] == "v1"
    assert gauntlet_rows[0]["mean_win_rate"] == 0.6


def test_fitness_mode_both_empty_baselines_skips_gauntlet(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'both' mode with no registered baselines behaves like parent."""
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    import orchestrator.baselines as _bl

    monkeypatch.setattr(_bl, "load_baselines", lambda _p: {})

    args = _build_args(tmp_path, fitness_mode="both")
    pool = [_make_imp("imp-0", 1), _make_imp("imp-1", 2)]

    fitness = _ScriptedFitness(["pass", "pass"])
    stack_apply = _ScriptedStackApply(cli, [(True, "v1")])

    def _gauntlet(*a: Any, **k: Any) -> Any:
        raise AssertionError("gauntlet must not run with empty baselines")

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        return [] if k.get("skip_mirror") else pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=lambda np, pp, **k: _regression(
            new_parent=np, prior_parent=pp
        ),
        run_gauntlet_fn=_gauntlet,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0


def test_fitness_mode_both_malformed_registry_does_not_abort(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed baselines.json (entry missing 'version') must NOT abort
    the evolve run. The real load_baselines raises ValueError, the run-loop
    guard catches it, and the run degrades to parent-like (rc 0, gauntlet
    never called) — matching the --fitness-mode help text.
    """
    monkeypatch.setattr(cli, "check_sc2_installed", lambda: True)
    # Point default_baselines_path() at a tmp data/ dir with a malformed
    # registry, and let the REAL load_baselines run (no monkeypatch of it).
    import orchestrator.baselines as _bl

    monkeypatch.setattr(_bl, "_repo_root", lambda: tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "baselines.json").write_text(
        json.dumps({"broken": {"note": "no version key"}}),
        encoding="utf-8",
    )

    args = _build_args(tmp_path, fitness_mode="both")
    pool = [_make_imp("imp-0", 1), _make_imp("imp-1", 2)]

    fitness = _ScriptedFitness(["pass", "pass"])
    stack_apply = _ScriptedStackApply(cli, [(True, "v1")])

    def _gauntlet(*a: Any, **k: Any) -> Any:
        raise AssertionError(
            "gauntlet must not run when the registry is malformed"
        )

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        return [] if k.get("skip_mirror") else pool

    rc = cli.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=lambda np, pp, **k: _regression(
            new_parent=np, prior_parent=pp
        ),
        run_gauntlet_fn=_gauntlet,
        current_version_fn=lambda: "v0",
    )
    # Degraded to parent-like: did NOT abort, gauntlet never ran.
    assert rc == 0
