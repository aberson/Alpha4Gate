"""Tests for Phase D.3 build-order edit-distance reward primitive."""

from __future__ import annotations

from pathlib import Path

import jsonschema
import pytest
from bots.current.learning.build_order_reward import (
    BuildOrderStepTarget,
    BuildOrderTrajectory,
    compute_progress,
    load_build_order,
    step_reward,
)

from orchestrator.registry import resolve_data_path

# ---------------------------------------------------------------------------
# compute_progress
# ---------------------------------------------------------------------------


def test_compute_progress_empty_trajectory_returns_zero() -> None:
    """Empty target list scores 0 (no work to score, no exceptions)."""
    traj = BuildOrderTrajectory(name="empty", targets=[])
    assert compute_progress([("build", "pylon", 18)], traj) == 0.0


def test_compute_progress_empty_executed_returns_sum_of_weights() -> None:
    """An empty execution means every target is missing -- distance = sum(weights)."""
    traj = BuildOrderTrajectory(
        name="t",
        targets=[
            BuildOrderStepTarget("build", "pylon", 18, weight=1.0),
            BuildOrderStepTarget("build", "gateway", 30, weight=1.5),
            BuildOrderStepTarget("build", "assimilator", 75, weight=2.0),
        ],
    )
    assert compute_progress([], traj) == pytest.approx(1.0 + 1.5 + 2.0)


def test_compute_progress_perfect_match_returns_zero() -> None:
    """All (action, target, time within tolerance) align -- distance 0."""
    traj = BuildOrderTrajectory(
        name="t",
        targets=[
            BuildOrderStepTarget("build", "pylon", 18, weight=1.0),
            BuildOrderStepTarget("build", "gateway", 30, weight=1.5),
        ],
        tolerance_seconds=30,
    )
    executed = [("build", "pylon", 20), ("build", "gateway", 45)]
    assert compute_progress(executed, traj) == 0.0


def test_compute_progress_one_missing_step_costs_that_weight() -> None:
    """A missing target contributes exactly its weight to the distance."""
    traj = BuildOrderTrajectory(
        name="t",
        targets=[
            BuildOrderStepTarget("build", "pylon", 18, weight=1.0),
            BuildOrderStepTarget("build", "gateway", 30, weight=1.5),
        ],
        tolerance_seconds=30,
    )
    # Pylon executed; gateway missing.
    executed = [("build", "pylon", 18)]
    assert compute_progress(executed, traj) == pytest.approx(1.5)


def test_compute_progress_one_out_of_tolerance_timing_costs_that_weight() -> None:
    """An action that happens too late (>tolerance) is a substitution at weight cost."""
    traj = BuildOrderTrajectory(
        name="t",
        targets=[
            BuildOrderStepTarget("build", "pylon", 18, weight=1.0),
            BuildOrderStepTarget("build", "gateway", 30, weight=2.0),
        ],
        tolerance_seconds=30,
    )
    # Gateway is at t=200, way outside the 30s tolerance window around t=30.
    # tolerance_seconds applies to BOTH targets, so the gateway entry doesn't
    # match the gateway target either; we expect a substitution at weight 2.0.
    executed = [("build", "pylon", 18), ("build", "gateway", 200)]
    assert compute_progress(executed, traj) == pytest.approx(2.0)


def test_compute_progress_case_insensitive_matching() -> None:
    """Bot CamelCase (Pylon, CyberneticsCore) matches trajectory lowercase."""
    traj = BuildOrderTrajectory(
        name="t",
        targets=[
            BuildOrderStepTarget("build", "pylon", 18, weight=1.0),
            BuildOrderStepTarget("build", "cyberneticscore", 140, weight=1.5),
        ],
        tolerance_seconds=30,
    )
    executed = [("build", "Pylon", 18), ("BUILD", "CyberneticsCore", 140)]
    assert compute_progress(executed, traj) == 0.0


def test_compute_progress_extra_executed_does_not_break_alignment() -> None:
    """An interleaved extra action is an insertion at cost 1.0, not blocking matches."""
    traj = BuildOrderTrajectory(
        name="t",
        targets=[
            BuildOrderStepTarget("build", "pylon", 18, weight=1.0),
            BuildOrderStepTarget("build", "gateway", 30, weight=1.5),
        ],
        tolerance_seconds=30,
    )
    # Probe build slipped between pylon and gateway. It's not in the
    # trajectory at all -- insertion at cost 1.0.
    executed = [
        ("build", "pylon", 18),
        ("train", "probe", 25),
        ("build", "gateway", 30),
    ]
    assert compute_progress(executed, traj) == pytest.approx(1.0)


def test_compute_progress_underscore_preserved_for_upgrades() -> None:
    """warp_gate_research only matches itself; underscores aren't stripped."""
    traj = BuildOrderTrajectory(
        name="t",
        targets=[
            BuildOrderStepTarget("research", "warp_gate_research", 240, weight=1.5),
        ],
        tolerance_seconds=30,
    )
    # Same id, case-shifted -- matches.
    assert compute_progress(
        [("research", "Warp_Gate_Research", 240)], traj
    ) == 0.0
    # Underscores stripped -- must NOT match.
    assert compute_progress(
        [("research", "warpgateresearch", 240)], traj
    ) == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# step_reward
# ---------------------------------------------------------------------------


def test_step_reward_positive_when_distance_shrinks() -> None:
    """Distance went 1.0 -> 0.5: progress improved, reward is positive."""
    assert step_reward(prev_progress=1.0, curr_progress=0.5) == pytest.approx(0.5)


def test_step_reward_negative_when_distance_grows() -> None:
    """Distance went 0.5 -> 1.0: regressed, reward is negative."""
    assert step_reward(prev_progress=0.5, curr_progress=1.0) == pytest.approx(-0.5)


def test_step_reward_zero_when_unchanged() -> None:
    assert step_reward(prev_progress=0.0, curr_progress=0.0) == 0.0
    assert step_reward(prev_progress=2.5, curr_progress=2.5) == 0.0


def test_step_reward_alpha_scales_result() -> None:
    """alpha multiplies the delta."""
    assert step_reward(prev_progress=1.0, curr_progress=0.5, alpha=2.0) == pytest.approx(1.0)
    assert step_reward(prev_progress=0.5, curr_progress=1.0, alpha=2.0) == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# load_build_order
# ---------------------------------------------------------------------------


def _build_orders_dir() -> Path:
    return resolve_data_path("build_orders/_schema.json").parent


def test_load_build_order_parses_4_gate_aggression() -> None:
    """Canonical example trajectory loads + dataclass fields parse correctly."""
    traj = load_build_order("4-gate-aggression")
    assert isinstance(traj, BuildOrderTrajectory)
    assert traj.name == "4-gate-aggression"
    assert traj.tolerance_seconds == 30
    assert len(traj.targets) == 11
    # Spot-check first row.
    first = traj.targets[0]
    assert isinstance(first, BuildOrderStepTarget)
    assert first.action == "build"
    assert first.target == "pylon"
    assert first.time_seconds == 18
    assert first.weight == pytest.approx(1.0)
    # Spot-check a research row -- underscores preserved.
    research_rows = [t for t in traj.targets if t.action == "research"]
    assert len(research_rows) == 1
    assert research_rows[0].target == "warp_gate_research"


def test_load_build_order_explicit_data_dir_overrides_default() -> None:
    """Passing data_dir bypasses the registry lookup."""
    traj = load_build_order("4-gate-aggression", data_dir=_build_orders_dir())
    assert traj.name == "4-gate-aggression"


def test_load_build_order_missing_label_raises_filenotfounderror() -> None:
    with pytest.raises(FileNotFoundError, match="does-not-exist"):
        load_build_order("does-not-exist")


def test_load_build_order_validates_schema(tmp_path: Path) -> None:
    """Trajectory that violates the schema raises ValidationError."""
    # Stage a fake build_orders dir with both a copy of the real schema and a
    # bad trajectory file.
    real_schema = _build_orders_dir() / "_schema.json"
    (tmp_path / "_schema.json").write_text(
        real_schema.read_text(encoding="utf-8"), encoding="utf-8"
    )
    bad = tmp_path / "bad.json"
    bad.write_text(
        '{"name": "bad", "targets": [{"action": "smelt", "target": "iron", "time_seconds": 0}]}',
        encoding="utf-8",
    )
    with pytest.raises(jsonschema.ValidationError):
        load_build_order("bad", data_dir=tmp_path)
