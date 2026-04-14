"""Policy-type-aware probe for extracting action probabilities.

Both ``MlpPolicy`` (SB3 PPO) and ``MlpLstmPolicy`` (sb3-contrib
RecurrentPPO) expose ``get_distribution`` but with different
signatures: the recurrent variant also requires ``lstm_states`` and
``episode_starts`` tensors. Callers that just want to peek at action
probabilities for logging/diagnostics should go through
``get_action_probs`` below and not touch ``get_distribution`` directly.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import torch
from numpy.typing import NDArray


def get_action_probs(
    model: Any, obs: NDArray[np.float32],
) -> NDArray[np.float32]:
    """Return action probabilities for a single observation.

    Works for both feed-forward (``MlpPolicy``) and recurrent
    (``MlpLstmPolicy``) policies. For recurrent policies the hidden
    state is initialized to zeros and ``episode_starts=True`` is passed
    — so this function returns the "fresh start" distribution, which is
    what we want for diagnostics / dashboard logging. Live in-game
    inference should use ``model.predict(obs, state=...)`` and thread
    the returned lstm state through the game loop instead.

    Args:
        model: An SB3 ``PPO`` or sb3-contrib ``RecurrentPPO`` instance.
        obs: Single observation vector, shape ``(FEATURE_DIM,)``.

    Returns:
        Float32 array of action probabilities. Empty on error.
    """
    try:
        device = model.device
        obs_t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0).to(device)

        with torch.no_grad():
            policy = model.policy
            if _is_recurrent(policy):
                lstm_states = _zero_lstm_states(policy, batch_size=1, device=device)
                episode_starts = torch.ones(1, device=device)
                dist, _ = policy.get_distribution(obs_t, lstm_states, episode_starts)
            else:
                dist = policy.get_distribution(obs_t)

            probs = dist.distribution.probs[0].cpu().numpy()  # type: ignore[union-attr]
        return probs.astype(np.float32)
    except Exception:
        return np.array([], dtype=np.float32)


def _is_recurrent(policy: Any) -> bool:
    """Duck-type check: recurrent policies expose an ``lstm_actor`` attr."""
    return hasattr(policy, "lstm_actor") or hasattr(policy, "lstm_hidden_state_shape")


def _zero_lstm_states(policy: Any, batch_size: int, device: Any) -> Any:
    """Build a zero-initialised RNNStates matching the policy's LSTM shape."""
    from sb3_contrib.common.recurrent.type_aliases import RNNStates

    shape = policy.lstm_hidden_state_shape  # (num_layers, 1, hidden_size)
    num_layers, _, hidden = shape
    pi_h = torch.zeros(num_layers, batch_size, hidden, device=device)
    pi_c = torch.zeros(num_layers, batch_size, hidden, device=device)
    vf_h = torch.zeros(num_layers, batch_size, hidden, device=device)
    vf_c = torch.zeros(num_layers, batch_size, hidden, device=device)
    return RNNStates((pi_h, pi_c), (vf_h, vf_c))
