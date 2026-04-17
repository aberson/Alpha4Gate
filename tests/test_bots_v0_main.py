"""Tests for ``python -m bots.v0`` (the ladder / solo entry point).

These are argparse-level tests (no subprocess overhead, no burnysc2
imports) plus one subprocess-based ``--help`` check that proves the
``python -m bots.v0`` module resolves.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from bots.v0.__main__ import build_parser, main


def test_parser_solo_happy_path() -> None:
    """``--role solo --map X`` parses cleanly."""
    parser = build_parser()
    args = parser.parse_args(["--role", "solo", "--map", "Simple64"])
    assert args.role == "solo"
    assert args.map == "Simple64"


@pytest.mark.parametrize("role", ["p1", "p2"])
def test_parser_ladder_happy_path(role: str) -> None:
    """Ladder roles with the full Phase 0 flag contract parse cleanly."""
    parser = build_parser()
    args = parser.parse_args(
        [
            "--role",
            role,
            "--map",
            "Simple64",
            "--GamePort",
            "5001",
            "--LadderServer",
            "127.0.0.1",
            "--StartPort",
            "5010",
            "--result-out",
            "out.json",
            "--seed",
            "42",
        ]
    )
    assert args.role == role
    assert args.GamePort == 5001
    assert args.StartPort == 5010
    assert args.LadderServer == "127.0.0.1"
    assert args.result_out == Path("out.json")
    assert args.seed == 42


def test_parser_missing_role_exits_nonzero() -> None:
    """Omitting ``--role`` is a parse error (SystemExit 2)."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--map", "Simple64"])
    assert exc_info.value.code == 2


def test_parser_missing_map_exits_nonzero() -> None:
    """Omitting ``--map`` is a parse error (SystemExit 2)."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--role", "solo"])
    assert exc_info.value.code == 2


def test_ladder_role_requires_game_and_start_port() -> None:
    """Ladder roles fail in ``main()`` if GamePort / StartPort are missing.

    Exercises the full dispatch path through ``main()`` (not the private
    validator) so the test stays stable if the helper is renamed.
    """
    with pytest.raises(SystemExit) as exc_info:
        main(["--role", "p1", "--map", "Simple64"])
    msg = str(exc_info.value)
    assert "--GamePort" in msg
    assert "--StartPort" in msg


def test_solo_role_dispatches_to_runner_single_game(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--role solo`` must call ``runner._run_single_game`` exactly once
    with a Namespace that preserves the forwarded flags AND must pass
    ``start_server=False`` so the solo entry does NOT spin up a
    background uvicorn (see feedback_backend_lifecycle.md)."""
    from bots.v0 import __main__ as main_mod
    from bots.v0 import runner

    captured: dict[str, object] = {}

    def fake_run_single_game(
        settings: object,  # noqa: ARG001 (signature-matching stub)
        args: object,
        *,
        start_server: bool = True,
    ) -> None:
        captured["args"] = args
        captured["start_server"] = start_server

    def fake_load_settings() -> object:
        return object()

    monkeypatch.setattr(runner, "_run_single_game", fake_run_single_game)
    monkeypatch.setattr("bots.v0.config.load_settings", fake_load_settings)

    main_mod.main(
        [
            "--role",
            "solo",
            "--map",
            "Simple64",
            "--difficulty",
            "3",
            "--no-claude",
        ]
    )

    assert "args" in captured, "runner._run_single_game was not called"
    assert captured["start_server"] is False, (
        "solo dispatch must not start the dashboard server"
    )
    ns = captured["args"]
    # Runner-parser defaults + forwarded flags must match.
    assert ns.map == "Simple64"  # type: ignore[attr-defined]
    assert ns.difficulty == 3  # type: ignore[attr-defined]
    assert ns.no_claude is True  # type: ignore[attr-defined]
    # Defaults the runner parser supplies (proves we routed through
    # build_parser rather than hand-rolling the Namespace).
    assert ns.build_order == "4gate"  # type: ignore[attr-defined]
    assert ns.decision_mode == "rules"  # type: ignore[attr-defined]
    assert ns.no_reward_log is False  # type: ignore[attr-defined]


def test_help_exits_zero() -> None:
    """``python -m bots.v0 --help`` must exit 0.

    Uses a subprocess so we exercise the real module entry path (the
    ``__main__.py`` file's ``if __name__ == "__main__"`` guard).
    """
    result = subprocess.run(
        [sys.executable, "-m", "bots.v0", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "bots.v0" in result.stdout
