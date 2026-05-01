"""Ladder entry point for ``python -m bots.v0``.

This is the Phase 0-validated contract used by the orchestrator's
subprocess self-play harness (``scripts/spike_subprocess_selfplay.py``):

    python -m bots.v0 --role {p1|p2|solo} --map NAME \
        [--GamePort N --LadderServer H --StartPort N] \
        [--result-out PATH] [--seed N] [--difficulty N] [--RealTime]

Role semantics:

* ``--role solo`` — delegate to the existing single-game flow in
  :mod:`bots.v0.runner` (``python -m bots.v0 --role solo --map X
  --difficulty N`` is equivalent to ``python -m bots.v0.runner --map X
  --difficulty N`` *minus the background uvicorn dashboard* — see
  :func:`_run_solo` below). Reward logging, training DB recording, and
  Claude advisor detection are reused verbatim.

* ``--role p1`` / ``--role p2`` — attach to an already-hosted SC2 match
  over the burnysc2 ladder websocket protocol. This mirrors
  ``scripts/spike_bot_stub.py`` (the Phase 0 canonical reference)
  swapping the stub bot for the full :class:`~bots.v0.bot.Alpha4GateBot`
  stack. The parent process (Phase 3's ``src/orchestrator/selfplay.py``)
  is responsible for the burnysc2 7.1.3 ``Portconfig.contiguous_ports``
  monkey-patch; the bot-side entry point only reconstructs the shared
  port layout from ``--StartPort``.

The parser is exposed as :func:`build_parser` so tests can assert
argparse behavior without subprocess overhead.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sc2.portconfig import Portconfig

_log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``python -m bots.v0`` argument parser.

    Separated from :func:`main` so tests can assert on the parsed
    :class:`argparse.Namespace` directly.
    """
    parser = argparse.ArgumentParser(
        prog="python -m bots.v0",
        description="Alpha4Gate v0 ladder / solo entry point",
    )
    parser.add_argument(
        "--role",
        required=True,
        choices=["p1", "p2", "solo"],
        help=(
            "p1/p2 = join an already-hosted SC2 match via play_from_websocket; "
            "solo = run a local game vs the built-in AI"
        ),
    )
    parser.add_argument(
        "--map",
        required=True,
        help="SC2 map name (must exist in SC2 Maps directory)",
    )
    # Ladder-specific flags (required when --role is p1/p2).
    parser.add_argument(
        "--GamePort",
        type=int,
        default=None,
        help="WebSocket port exposed by the SC2 Proxy (ladder roles only)",
    )
    parser.add_argument(
        "--LadderServer",
        type=str,
        default="127.0.0.1",
        help="Ladder server host (ladder roles only; default 127.0.0.1)",
    )
    parser.add_argument(
        "--StartPort",
        type=int,
        default=None,
        help=(
            "Base port shared by both bots to derive a Portconfig "
            "(ladder roles only)"
        ),
    )
    parser.add_argument(
        "--RealTime",
        action="store_true",
        default=False,
        help="Run in realtime mode (ladder roles only)",
    )
    parser.add_argument(
        "--result-out",
        type=Path,
        default=None,
        help=(
            "Optional path where the per-bot result JSON is written "
            "(ladder roles only)"
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional RNG seed (threaded through wherever applicable)",
    )
    # Solo-specific flags. Mirror the subset of runner.py's parser that
    # a ladder-style invocation would reasonably forward.
    parser.add_argument(
        "--difficulty",
        type=int,
        default=None,
        help="Built-in AI difficulty 1-10 (solo role only)",
    )
    parser.add_argument(
        "--realtime",
        action="store_true",
        default=False,
        help="Run in realtime mode (solo role only)",
    )
    parser.add_argument(
        "--build-order",
        default="4gate",
        help="Build order ID (solo role only; default: 4gate)",
    )
    parser.add_argument(
        "--decision-mode",
        default="rules",
        choices=["rules", "neural", "hybrid"],
        help="Decision mode (solo role only; default: rules)",
    )
    parser.add_argument(
        "--model-path",
        default=None,
        help="Path to SB3 PPO checkpoint (solo role only; for neural/hybrid)",
    )
    parser.add_argument(
        "--no-claude",
        action="store_true",
        help="Disable the Claude advisor (solo role only)",
    )
    parser.add_argument(
        "--no-reward-log",
        action="store_true",
        help="Disable per-step reward JSONL logging (solo role only)",
    )
    parser.add_argument(
        "--game-time-limit",
        type=int,
        default=1800,
        metavar="SECS",
        help=(
            "In-game time (seconds) before burnysc2 declares a Tie "
            "(solo role only; default 1800 = 30 min so games play to "
            "natural conclusion rather than auto-Tie at the historical "
            "5-minute default)."
        ),
    )
    return parser


def _validate_ladder_args(args: argparse.Namespace) -> None:
    """Enforce ladder-role required flags post-parse.

    argparse's ``required=True`` is per-flag and cannot express "required
    iff --role is p1/p2". Validate here so the error is surfaced before
    we try to open a websocket.
    """
    missing = [
        name
        for name, val in (
            ("--GamePort", args.GamePort),
            ("--StartPort", args.StartPort),
        )
        if val is None
    ]
    if missing:
        raise SystemExit(
            f"--role {args.role} requires {' and '.join(missing)}"
        )


def _build_portconfig(start_port: int) -> Portconfig:
    """Rebuild the shared Portconfig from the base port.

    Mirrors ``scripts/spike_bot_stub.py`` exactly. Lazy import of
    ``sc2.portconfig`` so argparse-level tests don't pay the burnysc2
    import cost.

    Note: ``player_ports`` is documented as ``list[list[int]]`` (see
    :class:`sc2.portconfig.Portconfig` docstring) but burnysc2 7.1.3's
    type annotation is the narrower ``list[int] | None``. The docstring
    + spike runtime are authoritative, so we pass the nested list and
    silence the resulting false-positive.
    """
    from sc2.portconfig import Portconfig

    return Portconfig(
        server_ports=[start_port + 2, start_port + 3],
        player_ports=[[start_port + 4, start_port + 5]],  # type: ignore[list-item]
    )


def _run_solo(args: argparse.Namespace) -> None:
    """Dispatch ``--role solo`` to the existing single-game runner.

    IMPORTANT: we pass ``start_server=False`` so the solo entry does NOT
    spin up a background uvicorn thread. The operator-facing dashboard
    is expected to be managed out-of-band via
    ``python -m bots.v0.runner --serve`` (or ``scripts/start-dev.sh``),
    and duplicating that lifecycle from ``python -m bots.v0 --role solo``
    would silently bind a second server attempt on every invocation —
    see ``memory/feedback_backend_lifecycle.md``.

    To keep defaults consistent with the legacy
    ``python -m bots.v0.runner --map X`` flow, we reconstruct the
    runner's own Namespace by re-parsing through
    :func:`bots.v0.runner.build_parser` rather than hand-rolling one.
    """
    from bots.v6 import runner
    from bots.v6.config import load_settings

    runner_argv: list[str] = ["--map", args.map]
    if args.difficulty is not None:
        runner_argv += ["--difficulty", str(args.difficulty)]
    if args.realtime:
        runner_argv.append("--realtime")
    if args.build_order:
        runner_argv += ["--build-order", args.build_order]
    if args.decision_mode:
        runner_argv += ["--decision-mode", args.decision_mode]
    if args.model_path is not None:
        runner_argv += ["--model-path", str(args.model_path)]
    if args.no_claude:
        runner_argv.append("--no-claude")
    if args.no_reward_log:
        runner_argv.append("--no-reward-log")
    if args.game_time_limit is not None:
        runner_argv += ["--game-time-limit", str(args.game_time_limit)]

    runner_args = runner.build_parser().parse_args(runner_argv)
    settings = load_settings()
    runner._run_single_game(settings, runner_args, start_server=False)


def _run_ladder(args: argparse.Namespace) -> None:
    """Dispatch ``--role p1|p2`` to ``play_from_websocket``.

    Mirrors ``scripts/spike_bot_stub.py`` — argparse + portconfig
    reconstruction + websocket connect — but swaps the stub for the
    real :class:`~bots.v0.bot.Alpha4GateBot`.

    Ladder-flag validation is performed in :func:`main` *before* the
    lazy SC2/torch imports below, so bad argv doesn't pay the cold-start
    import cost.
    """
    # Lazy imports: keep argparse-level tests cheap and avoid pulling
    # the whole SC2/torch stack into the import graph when running
    # ``python -m bots.v0 --help`` or the unit tests.
    from sc2.data import Race
    from sc2.main import play_from_websocket
    from sc2.player import Bot

    from bots.v6.bot import Alpha4GateBot

    assert args.GamePort is not None  # narrowed by _validate_ladder_args
    assert args.StartPort is not None  # narrowed by _validate_ladder_args
    portconfig = _build_portconfig(args.StartPort)
    bot = Alpha4GateBot()
    ws_url = f"ws://{args.LadderServer}:{args.GamePort}/sc2api"

    _log.info(
        "bots.v6 ladder entry: role=%s ws=%s start_port=%s",
        args.role,
        ws_url,
        args.StartPort,
    )

    asyncio.run(
        play_from_websocket(
            ws_url,
            Bot(Race.Protoss, bot, name=f"Alpha4Gate-{args.role}"),
            realtime=args.RealTime,
            portconfig=portconfig,
        )
    )


def main(argv: list[str] | None = None) -> None:
    """Entry point for ``python -m bots.v0``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)

    # Validate BEFORE dispatch so argparse errors (missing --GamePort,
    # etc.) are surfaced before the lazy SC2/torch imports inside
    # _run_ladder / _run_solo pay their cold-start cost.
    if args.role in ("p1", "p2"):
        _validate_ladder_args(args)

    if args.role == "solo":
        _run_solo(args)
    else:
        _run_ladder(args)


if __name__ == "__main__":
    main(sys.argv[1:])
