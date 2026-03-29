"""Imitation pre-training: behavior cloning from rule-based DecisionEngine."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import torch

from alpha4gate.learning.checkpoints import save_checkpoint
from alpha4gate.learning.database import TrainingDB
from alpha4gate.learning.features import FEATURE_DIM

_log = logging.getLogger(__name__)


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
    from gymnasium import spaces
    from stable_baselines3 import PPO

    from alpha4gate.learning.hyperparams import load_hyperparams, to_ppo_kwargs

    # Load all transitions
    total = db.get_transition_count()
    if total == 0:
        msg = "No transitions in database — run rule-based games first"
        raise ValueError(msg)

    states, actions, rewards = db.sample_batch(total)
    _log.info("Loaded %d transitions for imitation training", total)

    # Normalize states to [0, 1] using feature spec
    from alpha4gate.learning.features import _FEATURE_SPEC

    norm_states = np.zeros_like(states)
    for i, (_, divisor) in enumerate(_FEATURE_SPEC):
        norm_states[:, i] = np.clip(states[:, i] / divisor, 0.0, 1.0)

    # Create a dummy gym env for SB3 model initialization
    obs_space = spaces.Box(low=0.0, high=1.0, shape=(FEATURE_DIM,), dtype=np.float32)
    act_space: spaces.Discrete = spaces.Discrete(5)  # type: ignore[type-arg]
    dummy_env = gymnasium.make(
        "CartPole-v1"  # just for init, replaced below
    )
    dummy_env.observation_space = obs_space
    dummy_env.action_space = act_space

    # Build PPO model with target architecture
    ppo_kwargs: dict[str, Any] = {"policy_kwargs": {"net_arch": [128, 128]}}
    if hyperparams_path is not None:
        params = load_hyperparams(hyperparams_path)
        ppo_kwargs = to_ppo_kwargs(params)

    model = PPO("MlpPolicy", dummy_env, **ppo_kwargs)
    dummy_env.close()

    # Custom PyTorch training loop for behavior cloning
    policy = model.policy
    optimizer = torch.optim.Adam(policy.parameters(), lr=learning_rate)

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

            # Forward pass through policy network
            features = policy.extract_features(
                batch_states, policy.features_extractor
            )
            latent_pi, _ = policy.mlp_extractor(features)
            logits = policy.action_net(latent_pi)

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
            all_features = policy.extract_features(
                states_tensor.to(model.device),
                policy.features_extractor,
            )
            all_latent, _ = policy.mlp_extractor(all_features)
            all_logits = policy.action_net(all_latent)
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
