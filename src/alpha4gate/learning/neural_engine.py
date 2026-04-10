"""Neural decision engine: SB3 PPO model inference for strategic state selection."""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np

from alpha4gate.decision_engine import (
    ACTION_TO_STATE as _ACTION_TO_STATE,
)
from alpha4gate.decision_engine import (
    GameSnapshot,
    StrategicState,
)
from alpha4gate.learning.features import encode

_log = logging.getLogger(__name__)

# Index of DEFEND in the action list — used for the hybrid override below.
# Computed once at import time so adding/removing actions can never make it
# point at the wrong slot.
_DEFEND_IDX: int = _ACTION_TO_STATE.index(StrategicState.DEFEND)


class DecisionMode(StrEnum):
    """How the bot chooses strategic actions."""

    RULES = "rules"
    NEURAL = "neural"
    HYBRID = "hybrid"


class NeuralDecisionEngine:
    """Runs inference on a trained SB3 PPO model to choose strategic actions.

    In hybrid mode, the rule-based DEFEND override takes priority when
    enemy_army_near_base is True.
    """

    def __init__(
        self,
        model_path: str | Path,
        mode: DecisionMode = DecisionMode.NEURAL,
        deterministic: bool = True,
    ) -> None:
        from stable_baselines3 import PPO

        # Strip .zip suffix — SB3's load() appends it automatically
        p = str(model_path)
        if p.endswith(".zip"):
            p = p[:-4]
        self._model = PPO.load(p)
        self._mode = mode
        self._deterministic = deterministic
        self._last_probabilities: list[float] = []

    @property
    def mode(self) -> DecisionMode:
        return self._mode

    @property
    def last_probabilities(self) -> list[float]:
        """Action probabilities from the most recent prediction."""
        return self._last_probabilities

    def predict(self, snapshot: GameSnapshot) -> StrategicState:
        """Choose a strategic state for the given game snapshot.

        In hybrid mode, forces DEFEND when enemy is near base regardless
        of what the neural network suggests.
        """
        # Hybrid override: rule-based DEFEND
        if self._mode == DecisionMode.HYBRID and snapshot.enemy_army_near_base:
            probs = [0.0] * len(_ACTION_TO_STATE)
            probs[_DEFEND_IDX] = 1.0
            self._last_probabilities = probs
            _log.info("Hybrid override: DEFEND (enemy near base)")
            return StrategicState.DEFEND

        obs = encode(snapshot)
        action, _ = self._model.predict(obs, deterministic=self._deterministic)
        action_idx = int(action)

        # Get action probabilities for logging
        self._update_probabilities(obs)

        state = _ACTION_TO_STATE[action_idx]
        _log.info(
            "Neural decision: %s (probs: %s)",
            state.value,
            [f"{p:.3f}" for p in self._last_probabilities],
        )
        return state

    def _update_probabilities(self, obs: np.ndarray[Any, np.dtype[np.float32]]) -> None:
        """Extract action probabilities from the model for logging."""
        try:
            import torch

            with torch.no_grad():
                obs_tensor = torch.as_tensor(obs).unsqueeze(0).to(self._model.device)
                dist = self._model.policy.get_distribution(obs_tensor)
                probs = dist.distribution.probs[0].cpu().numpy()  # type: ignore[union-attr]
                self._last_probabilities = [float(p) for p in probs]
        except Exception:
            self._last_probabilities = []
