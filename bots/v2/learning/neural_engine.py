"""Neural decision engine: SB3 PPO model inference for strategic state selection."""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np

from bots.v2.decision_engine import (
    ACTION_TO_STATE as _ACTION_TO_STATE,
)
from bots.v2.decision_engine import (
    DecisionEngine,
    GameSnapshot,
    StrategicState,
)
from bots.v2.learning.features import encode

_log = logging.getLogger(__name__)

# Index of DEFEND in the action list — used for the hybrid override below.
# Computed once at import time so adding/removing actions can never make it
# point at the wrong slot.
_DEFEND_IDX: int = _ACTION_TO_STATE.index(StrategicState.DEFEND)

# Threat-aware hybrid DEFEND override thresholds.
#
# Previously the override fired any time ``enemy_army_near_base=True``,
# which turned trivial scout raids or small harass forces into full army
# recalls. This regressed max-supply engagements where a 130+ supply army
# would abandon an attack for 2-Ultralisk harass.
#
# The override now only fires when the enemy presence is non-trivial OR
# unknown (hidden / cloaked / burrowed), AND our army cannot safely
# counterattack. Otherwise we trust PPO — it can still pick DEFEND for a
# small known threat, or commit to ATTACK for a counterattack when we
# out-supply the raiders.
#
# ``TRIVIAL_RAID_THRESHOLD`` mirrors ``DecisionEngine.DEFEND_INTERRUPT_THRESHOLD``
# and ``MIN_COUNTERATTACK_ARMY`` reuses ``DecisionEngine.ATTACK_ARMY_SUPPLY``
# via constant reference (not a local copy) so the hybrid override stays in
# sync with the rule-based engine. ``COUNTERATTACK_SUPPLY_RATIO`` has no
# equivalent in decision_engine.py, so it lives here.
COUNTERATTACK_SUPPLY_RATIO: float = 1.5


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
        # Hybrid override: rule-based DEFEND, but only for real threats we
        # can't safely counterattack. Trivial known raids and lopsided
        # matchups fall through to PPO so it can pick ATTACK / counterattack
        # / other strategies as appropriate.
        #
        # Fog-of-war handling: ``enemy_vis == 0`` combined with
        # ``enemy_army_near_base=True`` means something we can't see is close
        # to home (burrowed, cloaked, out of sensor). That is MORE dangerous
        # than a small known raid, not less — so unknown threats must also
        # fire the DEFEND override. Both ``is_trivial_raid`` and
        # ``can_counterattack`` therefore require ``enemy_vis > 0``; an
        # unknown threat can never be trivial and can never be safely
        # counterattacked against.
        if self._mode == DecisionMode.HYBRID and snapshot.enemy_army_near_base:
            enemy_vis = snapshot.enemy_army_supply_visible
            our_army = snapshot.army_supply

            is_trivial_raid = 0 < enemy_vis < DecisionEngine.DEFEND_INTERRUPT_THRESHOLD
            can_counterattack = (
                enemy_vis > 0
                and our_army >= DecisionEngine.ATTACK_ARMY_SUPPLY
                and our_army >= enemy_vis * COUNTERATTACK_SUPPLY_RATIO
            )

            if not is_trivial_raid and not can_counterattack:
                probs = [0.0] * len(_ACTION_TO_STATE)
                probs[_DEFEND_IDX] = 1.0
                self._last_probabilities = probs
                _log.info(
                    "Hybrid override: DEFEND (threat=%d near base, army=%d)",
                    enemy_vis,
                    our_army,
                )
                return StrategicState.DEFEND

            # Fires every tick in HYBRID mode when enemy_army_near_base is
            # True — keep at DEBUG to avoid flooding the INFO stream. The
            # override-fires log above stays at INFO (rare, operationally
            # significant).
            _log.debug(
                "Hybrid override suppressed (enemy=%d, army=%d): trusting PPO",
                enemy_vis,
                our_army,
            )

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
        """Extract action probabilities from the model for logging.

        Uses ``policy_probe.get_action_probs`` so this works identically
        for feed-forward and recurrent policies. The recurrent variant
        uses a zero-initialised LSTM state — that's only for the
        dashboard-facing probability log, not for action selection
        (which goes through ``model.predict`` above).
        """
        from bots.v2.learning.policy_probe import get_action_probs

        probs = get_action_probs(self._model, obs)
        self._last_probabilities = [float(p) for p in probs]
