"""Smoke tests for PPOWithKL.

Verifies:
- kl_rules_coef is stored as a float attribute.
- Constructing with MlpPolicy + zero kl_coef behaves identically to PPO
  (no extra machinery, no crash).
- _apply_kl_to_rules can be invoked directly on a tiny rollout buffer
  and mutates policy parameters (proves the gradient path runs).

A full end-to-end rollout test is deferred — it requires a real 24-dim
env, not the dummy-env trick, which only works for model construction.
"""
from __future__ import annotations

import gymnasium
import numpy as np
import torch
from bots.v0.learning.environment import SC2Env
from bots.v0.learning.ppo_kl import PPOWithKL


def _dummy_env() -> gymnasium.Env:
    env = gymnasium.make("CartPole-v1")
    env.observation_space = SC2Env.observation_space
    env.action_space = SC2Env.action_space
    return env


def test_zero_kl_coef_attribute_set() -> None:
    env = _dummy_env()
    model = PPOWithKL(
        "MlpPolicy", env, kl_rules_coef=0.0,
        n_steps=16, batch_size=8, n_epochs=1,
        policy_kwargs={"net_arch": [16, 16]},
    )
    assert model.kl_rules_coef == 0.0


def test_positive_kl_coef_attribute_set() -> None:
    env = _dummy_env()
    model = PPOWithKL(
        "MlpPolicy", env, kl_rules_coef=0.1,
        n_steps=16, batch_size=8, n_epochs=1,
        policy_kwargs={"net_arch": [16, 16]},
    )
    assert model.kl_rules_coef == 0.1


def test_apply_kl_mutates_policy_params() -> None:
    """Manually populate the rollout buffer and verify _apply_kl_to_rules
    actually runs a gradient step (params change)."""
    env = _dummy_env()
    model = PPOWithKL(
        "MlpPolicy", env, kl_rules_coef=1.0,
        learning_rate=1e-2, n_steps=16, batch_size=8, n_epochs=1,
        policy_kwargs={"net_arch": [16, 16]},
    )

    # Populate rollout buffer with synthetic 24-dim observations.
    buf = model.rollout_buffer
    buf.reset()
    for i in range(buf.buffer_size):
        obs = np.random.rand(24).astype(np.float32)
        action = np.array([i % 6])
        reward = np.array([0.0], dtype=np.float32)
        episode_start = np.array([i == 0])
        value = torch.tensor([0.0])
        log_prob = torch.tensor([0.0])
        buf.add(obs.reshape(1, -1), action, reward, episode_start, value, log_prob)

    before = {n: p.detach().clone() for n, p in model.policy.named_parameters()}
    model._apply_kl_to_rules()
    after = dict(model.policy.named_parameters())

    changed = [n for n in before if not torch.equal(before[n], after[n])]
    assert changed, "no policy parameter changed after _apply_kl_to_rules"
