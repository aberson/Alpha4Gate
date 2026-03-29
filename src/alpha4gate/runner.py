"""CLI entry point: game launch, mode selection, web server."""

from __future__ import annotations

import argparse
import sys

from alpha4gate.build_orders import BuildOrder
from alpha4gate.config import Settings, load_settings


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Alpha4Gate — SC2 Protoss Bot")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start the API/WebSocket server without launching a game",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=0,
        metavar="N",
        help="Run N games in sequence and aggregate stats",
    )
    parser.add_argument(
        "--map",
        default="Simple64",
        help="SC2 map name (default: Simple64)",
    )
    parser.add_argument(
        "--difficulty",
        type=int,
        default=None,
        help="AI difficulty 1-10 (default: Easy)",
    )
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="Run in realtime mode",
    )
    parser.add_argument(
        "--multiplayer",
        action="store_true",
        help="Join a multiplayer game instead of vs AI",
    )
    parser.add_argument(
        "--build-order",
        default="4gate",
        help="Build order ID to use (default: 4gate)",
    )
    parser.add_argument(
        "--no-claude",
        action="store_true",
        help="Disable Claude advisor",
    )
    parser.add_argument(
        "--decision-mode",
        default="rules",
        choices=["rules", "neural", "hybrid"],
        help="Decision mode: rules (default), neural, or hybrid",
    )
    parser.add_argument(
        "--model-path",
        default=None,
        help="Path to SB3 PPO model checkpoint (required for neural/hybrid mode)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = load_settings()

    if args.serve:
        _start_server(settings)
    elif args.batch > 0:
        _run_batch(settings, args)
    elif args.multiplayer:
        _run_multiplayer(settings, args)
    else:
        _run_single_game(settings, args)


def _start_server(settings: Settings) -> None:
    """Start the FastAPI server."""
    import uvicorn

    from alpha4gate.api import configure

    configure(settings.data_dir, settings.log_dir, settings.replay_dir)
    uvicorn.run(
        "alpha4gate.api:app",
        host="0.0.0.0",
        port=settings.web_ui_port,
        reload=False,
    )


def _run_single_game(settings: Settings, args: argparse.Namespace) -> None:
    """Run a single game vs AI."""
    from alpha4gate.bot import Alpha4GateBot
    from alpha4gate.connection import run_bot
    from alpha4gate.learning.neural_engine import DecisionMode
    from alpha4gate.logger import GameLogger

    build_order = _load_build_order(settings, args.build_order)

    logger = GameLogger(log_dir=settings.log_dir)
    logger.start()

    decision_mode = DecisionMode(args.decision_mode)
    bot = Alpha4GateBot(
        build_order=build_order,
        logger=logger,
        decision_mode=decision_mode,
        model_path=args.model_path,
    )
    result = run_bot(
        bot,
        settings,
        map_name=args.map,
        opponent_difficulty=args.difficulty,
        realtime=args.realtime,
    )

    logger.stop()
    print(f"\nGame result: {result}")


def _run_batch(settings: Settings, args: argparse.Namespace) -> None:
    """Run N games in sequence."""
    from sc2.data import Result

    from alpha4gate.batch_runner import load_stats, record_game, save_stats
    from alpha4gate.bot import Alpha4GateBot
    from alpha4gate.connection import run_bot
    from alpha4gate.logger import GameLogger

    stats_path = settings.data_dir / "stats.json"
    games, _ = load_stats(stats_path)
    build_order = _load_build_order(settings, args.build_order)

    for i in range(args.batch):
        print(f"\n=== Game {i + 1}/{args.batch} ===")
        logger = GameLogger(log_dir=settings.log_dir)
        logger.start()

        bot = Alpha4GateBot(
            build_order=build_order,
            logger=logger,
            enable_console=True,
        )
        result = run_bot(
            bot,
            settings,
            map_name=args.map,
            opponent_difficulty=args.difficulty,
            realtime=False,
        )
        logger.stop()

        opponent = f"built-in-{args.difficulty or 'easy'}"
        record_game(
            games,
            map_name=args.map,
            opponent=opponent,
            result="win" if result == Result.Victory else "loss",
            duration_seconds=bot.time if hasattr(bot, "time") else 0.0,
            build_order_used=args.build_order,
            score=0,
        )
        print(f"Result: {result}")

    save_stats(games, stats_path)
    print(f"\nBatch complete. Stats saved to {stats_path}")


def _run_multiplayer(settings: Settings, args: argparse.Namespace) -> None:
    """Run a multiplayer game (placeholder for full implementation)."""
    print("Multiplayer mode is not yet fully implemented.")
    print("This requires burnysc2 multiplayer lobby support.")
    print("Use --difficulty to play vs AI for now.")
    sys.exit(1)


def _load_build_order(settings: Settings, order_id: str) -> BuildOrder:
    """Load a build order by ID, falling back to default 4gate."""
    from alpha4gate.build_orders import default_4gate, load_build_orders

    orders = load_build_orders(settings.data_dir / "build_orders.json")
    for order in orders:
        if order.id == order_id:
            return order

    if order_id == "4gate":
        return default_4gate()

    print(f"Warning: Build order '{order_id}' not found, using default 4gate")
    return default_4gate()


if __name__ == "__main__":
    main()
