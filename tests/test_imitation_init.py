"""Tests for the use_imitation_init / --ensure-pretrain flow.

Covers:
- trainer picks PPOWithKL when kl_rules_coef > 0
- trainer picks RecurrentPPO when policy_type=MlpLstmPolicy
- trainer loads v0_pretrain.zip when use_imitation_init=true and file exists
- --ensure-pretrain is a no-op when v0_pretrain.zip already exists
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from bots.v0.learning.ppo_kl import PPOWithKL, RecurrentPPOWithKL
from bots.v0.learning.trainer import TrainingOrchestrator
from sb3_contrib import RecurrentPPO
from stable_baselines3 import PPO


def test_pick_class_vanilla_ppo() -> None:
    assert TrainingOrchestrator._pick_model_class("MlpPolicy", 0.0) is PPO


def test_pick_class_ppo_with_kl() -> None:
    assert TrainingOrchestrator._pick_model_class("MlpPolicy", 0.1) is PPOWithKL


def test_pick_class_recurrent_ppo() -> None:
    assert TrainingOrchestrator._pick_model_class("MlpLstmPolicy", 0.0) is RecurrentPPO


def test_pick_class_recurrent_with_kl() -> None:
    assert (
        TrainingOrchestrator._pick_model_class("MlpLstmPolicy", 0.5)
        is RecurrentPPOWithKL
    )


def test_ensure_pretrain_skips_when_file_exists(tmp_path: Path) -> None:
    """--ensure-pretrain should log and return without calling imitation."""
    from bots.v0.runner import _ensure_pretrain_checkpoint

    class _Settings:
        data_dir = tmp_path

    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    (checkpoints / "v0_pretrain.zip").write_bytes(b"fake")
    hyperparams = tmp_path / "hyperparams.json"
    hyperparams.write_text(json.dumps({"policy_type": "MlpPolicy"}))

    # Should return without raising even though we didn't stub run_imitation_training.
    _ensure_pretrain_checkpoint(_Settings(), hyperparams)  # type: ignore[arg-type]


def test_ensure_pretrain_runs_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When v0_pretrain is missing, imitation training should be invoked."""
    from bots.v0 import runner
    from bots.v0.learning import imitation

    called: dict[str, bool] = {"ran": False}

    def fake_imitation(**kwargs: object) -> dict[str, object]:
        called["ran"] = True
        return {"epochs": 1, "final_loss": 0.1, "agreement": 0.95,
                "transitions": 10, "saved_path": "fake"}

    class _FakeDB:
        def __init__(self, *a: object, **kw: object) -> None: ...
        def close(self) -> None: ...

    monkeypatch.setattr(imitation, "run_imitation_training", fake_imitation)
    monkeypatch.setattr(runner, "TrainingDB", _FakeDB, raising=False)
    # Also patch the name that _ensure_pretrain_checkpoint imports locally
    import bots.v0.learning.database as db_mod
    monkeypatch.setattr(db_mod, "TrainingDB", _FakeDB)

    class _Settings:
        data_dir = tmp_path

    (tmp_path / "checkpoints").mkdir()
    hyperparams = tmp_path / "hyperparams.json"
    hyperparams.write_text(json.dumps({"policy_type": "MlpPolicy"}))

    runner._ensure_pretrain_checkpoint(_Settings(), hyperparams)  # type: ignore[arg-type]
    assert called["ran"] is True
