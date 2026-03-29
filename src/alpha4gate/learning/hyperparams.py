"""Load and provide PPO hyperparameters from JSON config."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Keys that map directly to PPO() constructor kwargs
_PPO_KWARGS = {
    "learning_rate",
    "n_steps",
    "batch_size",
    "n_epochs",
    "gamma",
    "gae_lambda",
    "clip_range",
    "ent_coef",
    "vf_coef",
    "max_grad_norm",
}


def load_hyperparams(path: str | Path) -> dict[str, Any]:
    """Load hyperparameters from a JSON file.

    Returns:
        Dict of all hyperparameters (including net_arch).
    """
    with open(path) as f:
        data: dict[str, Any] = json.load(f)
    return data


def to_ppo_kwargs(params: dict[str, Any]) -> dict[str, Any]:
    """Convert hyperparams dict to kwargs suitable for SB3 PPO().

    Extracts net_arch into policy_kwargs and passes through known PPO kwargs.
    Unknown keys are logged as warnings and ignored.
    """
    kwargs: dict[str, Any] = {}
    for key, value in params.items():
        if key == "net_arch":
            kwargs["policy_kwargs"] = {"net_arch": value}
        elif key in _PPO_KWARGS:
            kwargs[key] = value
        else:
            _log.warning("Unknown hyperparameter key ignored: %s", key)
    return kwargs


def save_hyperparams(params: dict[str, Any], path: str | Path) -> None:
    """Save hyperparameters to a JSON file."""
    with open(path, "w") as f:
        json.dump(params, f, indent=2)
        f.write("\n")
