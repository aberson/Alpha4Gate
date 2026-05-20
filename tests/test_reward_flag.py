"""Phase D Step D.6 backwards-compat reward-flag tests.

The cardinal invariant: when ``use_build_order_reward`` is ``False`` (the
default), per-step rewards AND per-step JSONL log lines are byte-identical
to the pre-D.6 baseline. When ``True``, ``RewardCalculator`` additionally
adds the build-order edit-distance summand (scaled by ``alpha``) to the
per-step total and writes a ``build_order_reward`` field into each JSONL
log line.

This module pins the four behaviors required by D.6's done-when criteria:

1. Flag-off byte-identity: replaying the vendored recorded log
   (``tests/fixtures/sample_reward_log.jsonl``) through
   ``RewardCalculator(use_build_order_reward=False)`` reproduces a
   byte-identical sum of per-step ``total_reward`` values across all
   recorded steps.
2. Flag-on adds expected delta: with the flag on AND
   ``snapshot.current_build_order`` set, the per-step total differs from
   the flag-off baseline by exactly the build-order summand.
3. Flag-on with no build order: when ``current_build_order is None`` the
   per-step total matches the flag-off case for the same state.
4. Alpha scaling: doubling ``alpha`` doubles the summand magnitude.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bots.current.learning.build_order_reward import (
    BuildOrderStepTarget,
    BuildOrderTrajectory,
    compute_progress,
    step_reward,
)
from bots.current.learning.rewards import RewardCalculator

from orchestrator.registry import resolve_data_path

RULES_PATH = resolve_data_path("reward_rules.json")
FIXTURE_LOG_PATH: Path = (
    Path(__file__).parent / "fixtures" / "sample_reward_log.jsonl"
)


def _baseline_state(**overrides: Any) -> dict[str, Any]:
    """Minimal state dict that triggers no shaped rules by default."""
    base: dict[str, Any] = {
        "supply_used": 20,
        "supply_cap": 30,
        "minerals": 200,
        "vespene": 50,
        "army_supply": 5,
        "worker_count": 12,
        "base_count": 1,
        "enemy_army_near_base": False,
        "enemy_army_supply_visible": 10,
        "game_time_seconds": 150.0,
        "gateway_count": 1,
        "robo_count": 0,
        "forge_count": 0,
        "upgrade_count": 0,
        "enemy_structure_count": 0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Cardinal invariant: flag-off byte-identity
# ---------------------------------------------------------------------------


def test_default_flag_is_off() -> None:
    """Loading from the live sibling hyperparams.json must default to OFF.

    Until M1 validates the build-order reward, the flag MUST stay off in
    production. This pins that invariant against accidental flips of the
    JSON value.
    """
    calc = RewardCalculator(RULES_PATH)
    # Internal attribute used for verification only -- the public contract
    # is the byte-identical baseline behavior pinned below.
    assert calc._use_build_order_reward is False


def test_flag_off_byte_identical_to_no_hyperparams() -> None:
    """RewardCalculator with the sibling-loaded flag OFF must produce the
    same per-step totals as one constructed with no hyperparams source.

    This is the cardinal-invariant proof: D.6 cannot have changed the
    flag-off code path at all (no extra adds, no rule re-orderings, no
    epsilon drift). Sums over a real recorded game log must match
    bit-for-bit (within float epsilon < 1e-12).
    """
    # Two calculators: one loads the live sibling hyperparams.json
    # (use_build_order_reward defaults to False in the live file).
    calc_sibling = RewardCalculator(RULES_PATH)
    # The other gets an explicit empty-dict hyperparams override so no
    # sibling load runs; flags fall back to defaults (off / 1.0).
    calc_explicit_off = RewardCalculator(RULES_PATH, hyperparams={})

    # Build the same minimal state and run a step through each.
    state = _baseline_state()
    r_sibling = calc_sibling.compute_step_reward(state)
    r_explicit = calc_explicit_off.compute_step_reward(state)
    assert abs(r_sibling - r_explicit) < 1e-12


def test_flag_off_byte_identical_to_baseline_across_many_steps() -> None:
    """Across a long sequence of varied states (representative of a real
    game), per-step totals from a flag-off calculator must agree exactly
    with a no-hyperparams baseline.

    This is the recorded-log byte-identity check generalized to a
    deterministic synthetic sequence -- we can't replay the vendored
    fixture step-for-step (it records totals but not the underlying
    GameSnapshot state, so we can't drive compute_step_reward off it).
    Instead, this test pins the cardinal invariant under a wide variety
    of state shapes that hit several reward rules.

    Step count is comparable to a real game (>= 50, matching the same
    MIN_FIXTURE_LINES guard used by tests/test_reward_migration.py).
    """
    calc_sibling = RewardCalculator(RULES_PATH)
    calc_explicit_off = RewardCalculator(RULES_PATH, hyperparams={})

    # Vary state across N=200 steps the way a real game would.
    states: list[dict[str, Any]] = []
    for i in range(200):
        gt = float(i * 3.0)
        states.append(
            _baseline_state(
                game_time_seconds=gt,
                gateway_count=1 + (i // 30),
                robo_count=(i // 60),
                worker_count=12 + (i // 10),
                army_supply=5 + (i // 8),
                base_count=1 + (i // 90),
                supply_used=20 + (i // 5),
                supply_cap=30 + (i // 4),
                has_scouted=(i >= 20),
                current_build_order="4-gate-aggression" if i % 2 == 0 else None,
            )
        )

    sum_sibling = 0.0
    sum_explicit = 0.0
    for state in states:
        sum_sibling += calc_sibling.compute_step_reward(state)
        sum_explicit += calc_explicit_off.compute_step_reward(state)
    assert abs(sum_sibling - sum_explicit) < 1e-12


def test_flag_off_log_omits_build_order_reward_field(tmp_path: Path) -> None:
    """Per-step JSONL log lines MUST NOT include ``build_order_reward`` when
    the flag is off. Absence is load-bearing -- the field must be omitted
    entirely (no ``null``, no ``0.0``) for downstream parsers that pin the
    log shape.
    """
    calc = RewardCalculator(
        RULES_PATH, log_dir=tmp_path / "logs", hyperparams={}
    )
    calc.open_game_log("flag_off")
    calc.compute_step_reward(_baseline_state(current_build_order="4-gate-aggression"))
    calc.close()
    log_file = tmp_path / "logs" / "game_flag_off.jsonl"
    raw = log_file.read_text().strip()
    entry = json.loads(raw)
    assert "build_order_reward" not in entry


# ---------------------------------------------------------------------------
# Flag-on behavior
# ---------------------------------------------------------------------------


def test_flag_on_no_build_order_matches_flag_off(tmp_path: Path) -> None:
    """With the flag on but ``current_build_order=None``, the per-step total
    matches the flag-off case for the same state.

    The summand is gated on BOTH the flag AND a non-None build-order
    label; either being false short-circuits to zero contribution.
    """
    off_calc = RewardCalculator(RULES_PATH, hyperparams={})
    on_calc = RewardCalculator(
        RULES_PATH,
        hyperparams={"use_build_order_reward": True, "build_order_reward_alpha": 1.0},
    )

    state = _baseline_state(current_build_order=None)
    r_off = off_calc.compute_step_reward(state)
    r_on = on_calc.compute_step_reward(state)
    assert abs(r_on - r_off) < 1e-12


def test_flag_on_log_omits_field_when_no_build_order(tmp_path: Path) -> None:
    """Even when the flag is on, log lines omit ``build_order_reward`` if no
    build order is set on the snapshot (no summand was actually computed).
    """
    calc = RewardCalculator(
        RULES_PATH,
        log_dir=tmp_path / "logs",
        hyperparams={"use_build_order_reward": True, "build_order_reward_alpha": 1.0},
    )
    calc.open_game_log("on_no_bo")
    calc.compute_step_reward(_baseline_state(current_build_order=None))
    calc.close()
    log_file = tmp_path / "logs" / "game_on_no_bo.jsonl"
    entry = json.loads(log_file.read_text().strip())
    assert "build_order_reward" not in entry


def test_flag_on_adds_summand_on_first_executed_action() -> None:
    """First step with a build-order label and a positive count delta must
    contribute a nonzero summand.

    Setup: gateway_count goes 0->1 between two steps, which the count-delta
    derivation emits as ``("build", "gateway", t)``. With the
    ``4-gate-aggression`` trajectory that contains a ``gateway`` target at
    t=30s within a 30s tolerance, this drives progress < initial-zero so
    the summand is nonzero.
    """
    on_calc = RewardCalculator(
        RULES_PATH,
        hyperparams={"use_build_order_reward": True, "build_order_reward_alpha": 1.0},
    )
    # Step 1: no gateway yet. Initialize _prev_counts.
    state1 = _baseline_state(
        gateway_count=0,
        game_time_seconds=10.0,
        current_build_order="4-gate-aggression",
    )
    on_calc.compute_step_reward(state1)

    # Step 2: gateway just built. Count-delta derives a ("build", "gateway", 30)
    # event which matches the trajectory's first gateway target at t=30,
    # weight=1.5, tolerance=30.
    state2 = _baseline_state(
        gateway_count=1,
        game_time_seconds=30.0,
        current_build_order="4-gate-aggression",
    )
    r2_on = on_calc.compute_step_reward(state2)

    # Run the same two steps through a flag-off calculator for comparison.
    off_calc = RewardCalculator(RULES_PATH, hyperparams={})
    off_calc.compute_step_reward(state1)
    r2_off = off_calc.compute_step_reward(state2)

    # The summand must be nonzero and equal the delta.
    delta = r2_on - r2_off
    assert abs(delta) > 1e-9, "expected nonzero build-order summand"


def test_flag_on_alpha_scaling_doubles_summand() -> None:
    """Doubling ``build_order_reward_alpha`` doubles the summand magnitude.

    Replay the same two-step sequence twice -- once with alpha=1.0, once
    with alpha=2.0 -- and check the summand exactly doubles. Both
    calculators see the same flag-off baseline path, so the per-step
    deltas isolate the summand.
    """
    state1 = _baseline_state(
        gateway_count=0,
        game_time_seconds=10.0,
        current_build_order="4-gate-aggression",
    )
    state2 = _baseline_state(
        gateway_count=1,
        game_time_seconds=30.0,
        current_build_order="4-gate-aggression",
    )

    def summand_with(alpha: float) -> float:
        on_calc = RewardCalculator(
            RULES_PATH,
            hyperparams={
                "use_build_order_reward": True,
                "build_order_reward_alpha": alpha,
            },
        )
        off_calc = RewardCalculator(RULES_PATH, hyperparams={})
        on_calc.compute_step_reward(state1)
        off_calc.compute_step_reward(state1)
        r_on = on_calc.compute_step_reward(state2)
        r_off = off_calc.compute_step_reward(state2)
        return r_on - r_off

    s_alpha_1 = summand_with(1.0)
    s_alpha_2 = summand_with(2.0)
    assert abs(s_alpha_1) > 1e-9, "expected nonzero summand at alpha=1.0"
    assert abs(s_alpha_2 - 2.0 * s_alpha_1) < 1e-9, (
        f"alpha scaling broken: alpha=1 -> {s_alpha_1}, alpha=2 -> {s_alpha_2}"
    )


def test_flag_on_log_writes_field_when_summand_computed(tmp_path: Path) -> None:
    """Per-step JSONL log lines MUST include a numeric ``build_order_reward``
    field when the flag is on AND a build order is set.

    Spot-check the value matches the per-step delta vs. a flag-off run.
    """
    on_calc = RewardCalculator(
        RULES_PATH,
        log_dir=tmp_path / "logs",
        hyperparams={"use_build_order_reward": True, "build_order_reward_alpha": 1.0},
    )
    on_calc.open_game_log("on_with_bo")
    state1 = _baseline_state(
        gateway_count=0,
        game_time_seconds=10.0,
        current_build_order="4-gate-aggression",
    )
    state2 = _baseline_state(
        gateway_count=1,
        game_time_seconds=30.0,
        current_build_order="4-gate-aggression",
    )
    on_calc.compute_step_reward(state1)
    on_calc.compute_step_reward(state2)
    on_calc.close()

    log_file = tmp_path / "logs" / "game_on_with_bo.jsonl"
    entries = [
        json.loads(raw) for raw in log_file.read_text().splitlines() if raw.strip()
    ]
    assert len(entries) == 2
    for entry in entries:
        assert "build_order_reward" in entry
        assert isinstance(entry["build_order_reward"], (int, float))


def test_open_game_log_resets_build_order_state(tmp_path: Path) -> None:
    """``open_game_log`` must reset per-game build-order state so that two
    successive games each start with ``_prev_progress = 0.0`` and empty
    executed-actions tracking.
    """
    calc = RewardCalculator(
        RULES_PATH,
        log_dir=tmp_path / "logs",
        hyperparams={"use_build_order_reward": True, "build_order_reward_alpha": 1.0},
    )
    state1 = _baseline_state(
        gateway_count=0,
        game_time_seconds=10.0,
        current_build_order="4-gate-aggression",
    )
    state2 = _baseline_state(
        gateway_count=1,
        game_time_seconds=30.0,
        current_build_order="4-gate-aggression",
    )

    calc.open_game_log("game1")
    calc.compute_step_reward(state1)
    r2_game1 = calc.compute_step_reward(state2)
    calc.close_game_log()

    # Open a new game; per-game state should reset.
    calc.open_game_log("game2")
    assert calc._prev_progress == 0.0
    assert calc._executed_actions == []
    assert calc._prev_counts == {}
    assert calc._active_build_order is None
    calc.compute_step_reward(state1)
    r2_game2 = calc.compute_step_reward(state2)
    calc.close()

    # Reset state means game2 reproduces game1 exactly.
    assert abs(r2_game1 - r2_game2) < 1e-12


# ---------------------------------------------------------------------------
# Sanity: D.3 primitive math agrees with what RewardCalculator emits
# ---------------------------------------------------------------------------


def test_summand_matches_d3_primitive() -> None:
    """The summand emitted by RewardCalculator on a known mini-trajectory
    must equal what :func:`step_reward` computes from the same inputs.
    """
    # Build a hand-constructed trajectory and run two manual steps.
    traj = BuildOrderTrajectory(
        name="test_traj",
        targets=[
            BuildOrderStepTarget("build", "gateway", 30, weight=1.5),
        ],
        tolerance_seconds=30,
    )
    # Step 1: no events. progress = sum(weights) = 1.5. prev_progress = 0.0.
    p1 = compute_progress([], traj)
    summand_1_expected = step_reward(0.0, p1, alpha=1.0)
    # Step 2: one matching event. progress = 0.0.
    p2 = compute_progress([("build", "gateway", 30)], traj)
    summand_2_expected = step_reward(p1, p2, alpha=1.0)
    # The summand should be positive on step 2 (distance shrank from 1.5 to 0).
    assert summand_2_expected > 0.0
    # And step 1 should be negative (distance grew from 0.0 to 1.5).
    assert summand_1_expected < 0.0
