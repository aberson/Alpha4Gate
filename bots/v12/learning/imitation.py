"""Imitation pre-training: behavior cloning from rule-based DecisionEngine."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import numpy as np
import torch

from bots.v12.learning.checkpoints import save_checkpoint
from bots.v12.learning.database import TrainingDB

_log = logging.getLogger(__name__)


def _imitation_model_class(policy_type: str) -> Any:
    """Pick the SB3 class matching ``policy_type`` for BC.

    Plain PPO / RecurrentPPO — no KL-to-rules here. Imitation already is
    the supervised signal; a KL term on top would be redundant.
    """
    if policy_type == "MlpLstmPolicy":
        from sb3_contrib import RecurrentPPO
        return RecurrentPPO
    from stable_baselines3 import PPO
    return PPO


def _bc_logits(
    policy: Any, batch_states: torch.Tensor, is_recurrent: bool,
) -> torch.Tensor:
    """Return action logits for BC, handling both policy families.

    Feed-forward: skip through ``mlp_extractor`` + ``action_net`` for the
    tightest forward path. Recurrent: run a single-step LSTM pass with
    zero hidden state and ``episode_starts=1``. BC data is stored as
    independent (s,a) pairs, so there's no temporal context to thread
    anyway — the LSTM gate just zeros out.
    """
    if not is_recurrent:
        features = policy.extract_features(batch_states, policy.features_extractor)
        latent_pi, _ = policy.mlp_extractor(features)
        return cast(torch.Tensor, policy.action_net(latent_pi))

    # Recurrent path: replicate what RecurrentActorCriticPolicy does in
    # get_distribution but without constructing the Distribution object.
    from sb3_contrib.common.recurrent.type_aliases import RNNStates

    batch_size = batch_states.shape[0]
    device = batch_states.device
    shape = policy.lstm_hidden_state_shape  # (num_layers, 1, hidden)
    num_layers, _, hidden = shape
    pi_h = torch.zeros(num_layers, batch_size, hidden, device=device)
    pi_c = torch.zeros(num_layers, batch_size, hidden, device=device)
    vf_h = torch.zeros(num_layers, batch_size, hidden, device=device)
    vf_c = torch.zeros(num_layers, batch_size, hidden, device=device)
    lstm_states = RNNStates((pi_h, pi_c), (vf_h, vf_c))
    episode_starts = torch.ones(batch_size, device=device)

    features = policy.extract_features(batch_states, policy.pi_features_extractor)
    latent_pi, _ = policy._process_sequence(
        features, lstm_states.pi, episode_starts, policy.lstm_actor,
    )
    latent_pi = policy.mlp_extractor.forward_actor(latent_pi)
    return cast(torch.Tensor, policy.action_net(latent_pi))


def run_imitation_training(
    db: TrainingDB,
    checkpoint_dir: str | Path,
    hyperparams_path: str | Path | None = None,
    max_epochs: int = 100,
    batch_size: int = 64,
    learning_rate: float = 3e-4,
    agreement_threshold: float = 0.95,
    checkpoint_name: str = "v0_pretrain",
) -> dict[str, Any]:
    """Train an SB3 PPO model via behavior cloning on rule-based transitions.

    Uses a custom PyTorch training loop on model.policy.parameters() because
    SB3's PPO does not natively support behavior cloning.

    Args:
        db: TrainingDB with transitions from rule-based games.
        checkpoint_dir: Where to save the trained model.
        hyperparams_path: Path to hyperparams.json (optional).
        max_epochs: Maximum training epochs.
        batch_size: Mini-batch size for training.
        learning_rate: Learning rate for Adam optimizer.
        agreement_threshold: Stop when action agreement exceeds this.
        checkpoint_name: Name for the saved checkpoint.

    Returns:
        Dict with training stats (epochs, final_loss, agreement, saved_path).
    """
    import gymnasium

    from bots.v12.learning.environment import SC2Env
    from bots.v12.learning.features import (
        _FEATURE_SPEC,
        BASE_GAME_FEATURE_DIM,
        FEATURE_DIM,
    )
    from bots.v12.learning.hyperparams import load_hyperparams, to_ppo_kwargs

    # Load all transitions
    total = db.get_transition_count()
    if total == 0:
        msg = "No transitions in database — run rule-based games first"
        raise ValueError(msg)

    states, actions, rewards = db.sample_batch(total)
    _log.info("Loaded %d transitions for imitation training", total)

    # Normalize 40 base game-state features to [0, 1], then pad with 7 zeros
    # for the advisor slots. Padding keeps the saved checkpoint's input
    # shape aligned with SC2Env.observation_space (FEATURE_DIM=47) so the
    # trainer can load v0_pretrain directly without dimension mismatch.
    # The DB only stores the 40 base features — advisor context is
    # ephemeral — so the advisor slots are legitimately zero at BC time.
    base = np.zeros_like(states)
    for i, (_, divisor) in enumerate(_FEATURE_SPEC):
        base[:, i] = np.clip(states[:, i] / divisor, 0.0, 1.0)
    pad_width = FEATURE_DIM - BASE_GAME_FEATURE_DIM
    norm_states = np.concatenate(
        [base, np.zeros((base.shape[0], pad_width), dtype=base.dtype)], axis=1,
    )

    # Build dummy env with the FULL SC2Env observation space so the model
    # we train here round-trips into the trainer.
    dummy_env = gymnasium.make("CartPole-v1")
    dummy_env.observation_space = SC2Env.observation_space
    dummy_env.action_space = SC2Env.action_space

    # Build model with target architecture. Dispatch on policy_type so the
    # v0_pretrain checkpoint matches whatever the trainer will instantiate.
    params: dict[str, Any] = {}
    ppo_kwargs: dict[str, Any] = {"policy_kwargs": {"net_arch": [128, 128]}}
    if hyperparams_path is not None:
        params = load_hyperparams(hyperparams_path)
        ppo_kwargs = to_ppo_kwargs(params)
    policy_type = str(params.get("policy_type", "MlpPolicy"))
    model_cls = _imitation_model_class(policy_type)
    model = model_cls(policy_type, dummy_env, **ppo_kwargs)
    dummy_env.close()

    # Custom PyTorch training loop for behavior cloning.
    # Uses a policy-type-aware forward helper because MlpLstmPolicy has no
    # mlp_extractor — its forward path threads through the LSTM. BC with
    # sequence-of-1 data can't exploit recurrence (hidden state is fresh
    # each step) but still trains the feature extractor and action head.
    policy = model.policy
    optimizer = torch.optim.Adam(policy.parameters(), lr=learning_rate)
    is_recurrent = policy_type == "MlpLstmPolicy"

    states_tensor = torch.tensor(norm_states, dtype=torch.float32)
    actions_tensor = torch.tensor(actions, dtype=torch.long)

    best_agreement = 0.0
    final_loss = float("inf")
    trained_epochs = 0

    for epoch in range(max_epochs):
        # Shuffle data
        indices = torch.randperm(len(states_tensor))
        epoch_loss = 0.0
        n_batches = 0

        for start in range(0, len(indices), batch_size):
            batch_idx = indices[start : start + batch_size]
            batch_states = states_tensor[batch_idx].to(model.device)
            batch_actions = actions_tensor[batch_idx].to(model.device)

            logits = _bc_logits(policy, batch_states, is_recurrent)

            # Cross-entropy loss (action prediction)
            loss = torch.nn.functional.cross_entropy(logits, batch_actions)

            optimizer.zero_grad()
            loss.backward()  # type: ignore[no-untyped-call]
            optimizer.step()

            epoch_loss += loss.item()
            n_batches += 1

        avg_loss = epoch_loss / max(n_batches, 1)
        final_loss = avg_loss

        # Compute action agreement
        with torch.no_grad():
            all_logits = _bc_logits(
                policy, states_tensor.to(model.device), is_recurrent,
            )
            predicted = all_logits.argmax(dim=1).cpu()
            agreement = (predicted == actions_tensor).float().mean().item()

        if agreement > best_agreement:
            best_agreement = agreement

        trained_epochs = epoch + 1
        _log.info(
            "Epoch %d/%d — loss=%.4f, agreement=%.3f",
            trained_epochs,
            max_epochs,
            avg_loss,
            agreement,
        )

        if agreement >= agreement_threshold:
            _log.info("Agreement threshold reached: %.3f >= %.3f", agreement, agreement_threshold)
            break

    # Save checkpoint
    saved_path = save_checkpoint(
        model,
        checkpoint_dir,
        checkpoint_name,
        metadata={
            "type": "imitation",
            "epochs": trained_epochs,
            "final_loss": final_loss,
            "agreement": best_agreement,
            "transitions": total,
        },
        is_best=True,
    )

    return {
        "epochs": trained_epochs,
        "final_loss": final_loss,
        "agreement": best_agreement,
        "transitions": total,
        "saved_path": str(saved_path),
    }
