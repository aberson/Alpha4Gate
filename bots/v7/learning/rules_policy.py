"""Stateless rule-engine teacher for the KL-to-rules PPO auxiliary loss.

Given a batch of observation vectors, returns the action index the
rule-based ``DecisionEngine`` would choose on each. A fresh engine is
built per observation so this stays correct under rollout-buffer
shuffling.

TRADE-OFF
---------
``DecisionEngine._compute_next_state`` normally depends on internal
state (``self._state``, ``self._sequencer`` build-order progress,
``self._recently_retreated``). This batched path cannot reproduce that
history from a lone observation, so the teacher returns the "cold
start" rule action on each row. That is a lossy signal — notably it
will not replicate ATTACK<->DEFEND hysteresis or OPENING sequencing —
but that matches how AlphaStar uses its supervised policy: as a soft
divergence anchor, not a ground-truth oracle. The KL coefficient keeps
RL honest near the rule policy without over-constraining it.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from bots.v7.decision_engine import ACTION_TO_STATE, DecisionEngine
from bots.v7.learning.features import decode


def rule_actions_for_batch(obs: NDArray[np.float32]) -> NDArray[np.int64]:
    """Return the rule-engine action index for each row of ``obs``.

    Args:
        obs: Batch of encoded observation vectors, shape ``(N, FEATURE_DIM)``.

    Returns:
        Int64 array of shape ``(N,)`` where each entry is an index into
        ``ACTION_TO_STATE``.
    """
    n = obs.shape[0]
    out = np.empty(n, dtype=np.int64)
    for i in range(n):
        snap = decode(obs[i])
        engine = DecisionEngine()  # cold start — see TRADE-OFF above
        state = engine._compute_next_state(snap)
        out[i] = ACTION_TO_STATE.index(state)
    return out
