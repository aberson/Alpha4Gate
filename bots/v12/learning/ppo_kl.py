"""PPO (+ optional LSTM) with a KL-to-rules auxiliary loss.

After the standard PPO update completes, iterate the rollout buffer
once more and apply a cross-entropy-to-rule-action gradient step with
coefficient ``kl_rules_coef``. This keeps RL from drifting far from
the rule baseline while still letting it explore — matching how
AlphaStar uses a supervised-policy KL anchor.

DESIGN
------
The extra-pass approach (vs. folding CE into the main PPO loss) is
chosen for three reasons:

1. **No monkeypatching** of SB3 internals — ``super().train()`` runs
   unchanged and gradients are fully isolated.
2. **No graph-lifetime hazards** — each CE pass owns its own forward.
3. **Easy to disable** — ``kl_rules_coef == 0.0`` short-circuits.

LIMITATIONS (v1)
----------------
KL-to-rules is only wired for ``MlpPolicy``. For ``MlpLstmPolicy`` the
rollout buffer yields ``(obs, lstm_states, episode_starts, ...)`` and
running a separate forward pass requires replaying the hidden state.
v1 logs a warning and disables KL in that case; the class still exists
so the trainer's policy-type switch is symmetric. v2 can add proper
recurrent KL.
"""

from __future__ import annotations

import logging
from typing import Any

import torch
from sb3_contrib import RecurrentPPO
from stable_baselines3 import PPO

from bots.v12.learning.rules_policy import rule_actions_for_batch

_log = logging.getLogger(__name__)


class PPOWithKL(PPO):
    """PPO + KL-to-rules auxiliary loss (MlpPolicy only)."""

    def __init__(self, *args: Any, kl_rules_coef: float = 0.0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.kl_rules_coef = float(kl_rules_coef)

    def train(self) -> None:  # noqa: D401
        super().train()
        if self.kl_rules_coef <= 0.0:
            return
        self._apply_kl_to_rules()

    def _apply_kl_to_rules(self) -> None:
        device = self.policy.device
        # One additional pass over the rollout buffer, matching PPO's
        # batch size. Gradients are computed only from the CE term so
        # they don't interfere with PPO's policy/value loss pass.
        for rollout_data in self.rollout_buffer.get(self.batch_size):
            obs = rollout_data.observations
            obs_np = obs.detach().cpu().numpy()
            rule_actions = rule_actions_for_batch(obs_np)
            rule_t = torch.as_tensor(rule_actions, dtype=torch.long, device=device)

            dist = self.policy.get_distribution(obs)
            # Categorical distribution for Discrete action spaces.
            logits = dist.distribution.logits  # type: ignore[union-attr]
            ce = torch.nn.functional.cross_entropy(logits, rule_t)

            self.policy.optimizer.zero_grad()
            (self.kl_rules_coef * ce).backward()  # type: ignore[no-untyped-call]
            torch.nn.utils.clip_grad_norm_(
                self.policy.parameters(), self.max_grad_norm,
            )
            self.policy.optimizer.step()


class RecurrentPPOWithKL(RecurrentPPO):
    """RecurrentPPO placeholder — KL-to-rules disabled for v1.

    Subclass exists so the trainer's policy-type matrix is symmetric.
    See module docstring "LIMITATIONS" for why the KL term is a no-op
    here. If ``kl_rules_coef > 0`` is requested, log once and drop it.
    """

    def __init__(self, *args: Any, kl_rules_coef: float = 0.0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if float(kl_rules_coef) > 0.0:
            _log.warning(
                "kl_rules_coef=%.3f requested with MlpLstmPolicy — KL-to-rules "
                "is not implemented for recurrent policies in v1. Disabling.",
                kl_rules_coef,
            )
        self.kl_rules_coef = 0.0
