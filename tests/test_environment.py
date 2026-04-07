"""Tests for the Gymnasium SC2 environment wrapper.

These tests mock the SC2 game loop to test the environment logic without
needing a running SC2 instance.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import numpy as np

from alpha4gate.decision_engine import GameSnapshot, StrategicState
from alpha4gate.learning.environment import (
    _ACTION_TO_STATE,
    FEATURE_DIM,
    SC2Env,
    _GymStateProxy,
)
from alpha4gate.learning.features import encode
from alpha4gate.learning.rewards import RewardCalculator


def _default_snapshot(**overrides: Any) -> GameSnapshot:
    base = GameSnapshot(
        supply_used=50,
        supply_cap=100,
        minerals=800,
        vespene=400,
        army_supply=30,
        worker_count=22,
        base_count=2,
        game_time_seconds=200.0,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


class TestObservationSpace:
    def test_obs_shape(self) -> None:
        """Encoded observations should match FEATURE_DIM."""
        snap = _default_snapshot()
        obs = encode(snap)
        assert obs.shape == (FEATURE_DIM,)
        assert obs.dtype == np.float32

    def test_obs_bounds(self) -> None:
        snap = _default_snapshot()
        obs = encode(snap)
        assert np.all(obs >= 0.0)
        assert np.all(obs <= 1.0)


class TestGymStateProxy:
    """Test the _GymStateProxy used to inject gym actions into Alpha4GateBot."""

    def test_proxy_returns_correct_state(self) -> None:
        proxy = _GymStateProxy(StrategicState.ATTACK)
        snap = _default_snapshot()
        assert proxy.predict(snap) == StrategicState.ATTACK

    def test_proxy_all_states(self) -> None:
        for _action_idx, expected_state in enumerate(_ACTION_TO_STATE):
            proxy = _GymStateProxy(expected_state)
            assert proxy.predict(_default_snapshot()) == expected_state


class TestRewardComputation:
    """Test that reward calculator integrates correctly with env logic."""

    def test_step_reward_positive(self) -> None:
        calc = RewardCalculator()
        state = asdict(_default_snapshot())
        reward = calc.compute_step_reward(state)
        assert reward > 0  # survival bonus

    def test_terminal_win_reward(self) -> None:
        calc = RewardCalculator()
        state = asdict(_default_snapshot())
        reward = calc.compute_step_reward(state, is_terminal=True, result="win")
        assert reward > 5.0

    def test_terminal_loss_reward(self) -> None:
        calc = RewardCalculator()
        state = asdict(_default_snapshot())
        reward = calc.compute_step_reward(state, is_terminal=True, result="loss")
        assert reward < -5.0


class TestSnapshotToRaw:
    """Test the snapshot-to-raw conversion for DB storage."""

    def test_raw_vector_length(self) -> None:
        env = SC2Env.__new__(SC2Env)
        snap = _default_snapshot()
        # Need to bind the method — use the class method directly
        raw = SC2Env._snapshot_to_raw(env, snap)
        assert raw.shape == (FEATURE_DIM,)

    def test_raw_values_match_snapshot(self) -> None:
        env = SC2Env.__new__(SC2Env)
        snap = _default_snapshot(supply_used=75, minerals=1200)
        raw = SC2Env._snapshot_to_raw(env, snap)
        assert raw[0] == 75.0  # supply_used
        assert raw[2] == 1200.0  # minerals

    def test_bool_converted_to_int(self) -> None:
        env = SC2Env.__new__(SC2Env)
        snap = _default_snapshot(enemy_army_near_base=True)
        raw = SC2Env._snapshot_to_raw(env, snap)
        assert raw[7] == 1.0  # enemy_army_near_base as int
