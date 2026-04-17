"""Tests for the stateless rule-engine teacher."""
from __future__ import annotations

import numpy as np
from bots.v0.decision_engine import ACTION_TO_STATE, GameSnapshot, StrategicState
from bots.v0.learning.features import encode
from bots.v0.learning.rules_policy import rule_actions_for_batch


def _obs(**overrides: object) -> np.ndarray:
    return encode(GameSnapshot(**overrides))  # type: ignore[arg-type]


def test_rule_actions_shape_and_dtype() -> None:
    batch = np.stack([_obs(), _obs(base_count=3, game_time_seconds=600.0)])
    out = rule_actions_for_batch(batch)
    assert out.shape == (2,)
    assert out.dtype == np.int64


def test_rule_actions_within_action_space() -> None:
    batch = np.stack([_obs() for _ in range(5)])
    out = rule_actions_for_batch(batch)
    assert all(0 <= int(a) < len(ACTION_TO_STATE) for a in out)


def test_enemy_near_base_triggers_defend() -> None:
    # Enemy at base with a big army should yield DEFEND on a cold-start engine.
    obs = _obs(
        enemy_army_near_base=True,
        enemy_army_supply_visible=20,
        army_supply=5,
    )
    out = rule_actions_for_batch(obs.reshape(1, -1))
    assert ACTION_TO_STATE[int(out[0])] == StrategicState.DEFEND
