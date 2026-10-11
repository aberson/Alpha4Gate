"""Provider selection and credential boundary for the native Battle View host."""

import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def battle():
    return runpy.run_path(str(Path(__file__).parents[1] / "scripts/probes/jev_battle_view.py"))


def test_hosted_key_reaches_only_game_environment(battle, monkeypatch):
    import os

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    child_env = battle["game_environment"]("typesafe")
    assert child_env["TYPESAFE_API_KEY"] == "test-only-key"
    assert (
        "TYPESAFE_API_KEY" not in os.environ
    )  # Later browser/recorder children cannot inherit it.


def test_scripted_probe_drops_key(battle, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    assert "TYPESAFE_API_KEY" not in battle["game_environment"]("scripted")


def test_missing_hosted_key_fails_instead_of_switching_provider(battle, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="no API key"):
        battle["game_environment"]("typesafe")


def test_hosted_selection_reaches_production_runner(battle, monkeypatch, tmp_path):
    import jev.bot
    import jev.runner

    calls = []
    monkeypatch.setattr(jev.bot, "JevBot", jev.bot.JevBot)  # Restore probe subclass after test.
    monkeypatch.setattr(
        jev.runner,
        "run_match",
        lambda options, *args, **kwargs: calls.append(options) or SimpleNamespace(status="test"),
    )
    battle["game_child"]("a" * 32, tmp_path, True, "typesafe", 20)
    assert len(calls) == 1
    assert calls[0].decision_provider == "typesafe"
    assert calls[0].decision_model == "jev-1.13.0"
    assert calls[0].decision_max_requests == 20
    assert calls[0].max_game_seconds == 900
