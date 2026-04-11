"""CLI entry point: game launch, mode selection, web server."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from alpha4gate.build_orders import BuildOrder
from alpha4gate.config import Settings, load_settings

_log = logging.getLogger(__name__)


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
        "--train",
        default=None,
        choices=["imitation", "rl"],
        help="Training mode: imitation (behavior cloning) or rl (PPO)",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=10,
        help="Number of RL training cycles (default: 10)",
    )
    parser.add_argument(
        "--games-per-cycle",
        type=int,
        default=10,
        help="Games per RL cycle (default: 10)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume RL training from last checkpoint",
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
    parser.add_argument(
        "--no-reward-log",
        action="store_true",
        help="Disable per-step reward JSONL logging (enabled by default)",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Auto-start the training daemon when running --serve",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Main entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = load_settings()

    if args.train is not None:
        _run_training(settings, args)
    elif args.serve:
        _start_server(settings, daemon=args.daemon)
    elif args.batch > 0:
        _run_batch(settings, args)
    elif args.multiplayer:
        _run_multiplayer(settings, args)
    else:
        _run_single_game(settings, args)


def _run_training(settings: Settings, args: argparse.Namespace) -> None:
    """Run training (imitation or RL)."""
    if args.train == "imitation":
        from alpha4gate.learning.database import TrainingDB
        from alpha4gate.learning.imitation import run_imitation_training

        db = TrainingDB(settings.data_dir / "training.db")
        result = run_imitation_training(
            db=db,
            checkpoint_dir=settings.data_dir / "checkpoints",
            hyperparams_path=settings.data_dir / "hyperparams.json",
        )
        db.close()
        print(f"Imitation training complete: {result}")

    elif args.train == "rl":
        from alpha4gate.learning.trainer import TrainingOrchestrator

        reward_rules = settings.data_dir / "reward_rules.json"
        hyperparams = settings.data_dir / "hyperparams.json"

        orchestrator = TrainingOrchestrator(
            checkpoint_dir=settings.data_dir / "checkpoints",
            db_path=settings.data_dir / "training.db",
            reward_rules_path=reward_rules if reward_rules.exists() else None,
            hyperparams_path=hyperparams if hyperparams.exists() else None,
            map_name=args.map,
            initial_difficulty=args.difficulty or 1,
        )
        result = orchestrator.run(
            n_cycles=args.cycles,
            games_per_cycle=args.games_per_cycle,
            resume=args.resume,
        )
        print(f"RL training complete: {result}")


def _start_server(settings: Settings, daemon: bool = False) -> None:
    """Start the FastAPI server (blocking)."""
    import uvicorn

    from alpha4gate.api import configure
    from alpha4gate.error_log import install_error_log_handler
    from alpha4gate.learning.daemon import load_daemon_config

    # Attach the ERROR-level log ring buffer to the root logger as
    # early as possible so backend errors that happen *during* startup
    # (not just after the FastAPI lifespan fires) still land in the
    # alerts pipeline. Idempotent.
    install_error_log_handler()

    daemon_config = load_daemon_config(settings.data_dir / "daemon_config.json")
    configure(
        settings.data_dir, settings.log_dir, settings.replay_dir,
        api_key=settings.anthropic_api_key,
        daemon_config=daemon_config,
    )

    if daemon:
        from alpha4gate.api import _daemon

        if _daemon is not None:
            _daemon.start()
            _log.info("Training daemon auto-started (--daemon flag)")

    uvicorn.run(
        "alpha4gate.api:app",
        host="0.0.0.0",
        port=settings.web_ui_port,
        reload=False,
    )


def _start_server_background(settings: Settings) -> None:
    """Start the FastAPI server in a background daemon thread."""
    import threading

    import uvicorn

    from alpha4gate.api import configure

    configure(
        settings.data_dir, settings.log_dir, settings.replay_dir,
        api_key=settings.anthropic_api_key,
    )
    config = uvicorn.Config(
        "alpha4gate.api:app",
        host="0.0.0.0",
        port=settings.web_ui_port,
        reload=False,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    print(f"API server started on http://localhost:{settings.web_ui_port}")


def _run_single_game(settings: Settings, args: argparse.Namespace) -> None:
    """Run a single game vs AI."""
    import uuid

    from alpha4gate.bot import Alpha4GateBot
    from alpha4gate.connection import run_bot
    from alpha4gate.learning.neural_engine import DecisionMode
    from alpha4gate.learning.rewards import RewardCalculator
    from alpha4gate.logger import GameLogger

    _start_server_background(settings)

    build_order = _load_build_order(settings, args.build_order)

    logger = GameLogger(log_dir=settings.log_dir)
    logger.start()

    claude_advisor = None
    if args.no_claude:
        _log.info("ClaudeAdvisor: disabled (--no-claude flag)")
    elif shutil.which("claude") is None:
        _log.info("ClaudeAdvisor: disabled (claude CLI not found on PATH)")
    else:
        from alpha4gate.claude_advisor import ClaudeAdvisor

        claude_advisor = ClaudeAdvisor()

    # Always-on reward logging (unless opted out)
    settings.ensure_dirs()
    log_dir = None if args.no_reward_log else settings.data_dir / "reward_logs"
    reward_rules = settings.data_dir / "reward_rules.json"
    reward_calc = RewardCalculator(
        reward_rules if reward_rules.exists() else None,
        log_dir=log_dir,
    )
    game_id = uuid.uuid4().hex[:12]
    reward_calc.open_game_log(game_id)

    decision_mode = DecisionMode(args.decision_mode)
    bot = Alpha4GateBot(
        build_order=build_order,
        logger=logger,
        decision_mode=decision_mode,
        model_path=args.model_path,
        claude_advisor=claude_advisor,
        reward_calculator=reward_calc,
    )
    result = run_bot(
        bot,
        settings,
        map_name=args.map,
        opponent_difficulty=args.difficulty,
        realtime=args.realtime,
    )

    logger.stop()
    reward_calc.close()
    print(f"\nGame result: {result}")


def _run_batch(settings: Settings, args: argparse.Namespace) -> None:
    """Run N games in sequence, recording transitions for training."""
    import uuid

    from sc2.data import Result

    from alpha4gate.batch_runner import load_stats, record_game, save_stats
    from alpha4gate.bot import Alpha4GateBot
    from alpha4gate.connection import run_bot
    from alpha4gate.learning.database import TrainingDB
    from alpha4gate.learning.rewards import RewardCalculator
    from alpha4gate.logger import GameLogger

    stats_path = settings.data_dir / "stats.json"
    games, _ = load_stats(stats_path)
    build_order = _load_build_order(settings, args.build_order)

    # Open training DB for transition recording
    settings.ensure_dirs()
    db = TrainingDB(settings.data_dir / "training.db")
    reward_rules = settings.data_dir / "reward_rules.json"
    log_dir = None if args.no_reward_log else settings.data_dir / "reward_logs"
    reward_calc = RewardCalculator(
        reward_rules if reward_rules.exists() else None,
        log_dir=log_dir,
    )

    claude_advisor = None
    if args.no_claude:
        _log.info("ClaudeAdvisor: disabled (--no-claude flag)")
    elif shutil.which("claude") is None:
        _log.info("ClaudeAdvisor: disabled (claude CLI not found on PATH)")
    else:
        from alpha4gate.claude_advisor import ClaudeAdvisor

        claude_advisor = ClaudeAdvisor()

    # Determine model_version label for this batch
    decision_mode = getattr(args, "decision_mode", "rules")
    model_path = getattr(args, "model_path", None)
    if model_path:
        model_version = Path(model_path).stem
    else:
        model_version = decision_mode

    for i in range(args.batch):
        print(f"\n=== Game {i + 1}/{args.batch} ===")
        logger = GameLogger(log_dir=settings.log_dir)
        logger.start()

        game_id = uuid.uuid4().hex[:12]
        reward_calc.open_game_log(game_id)
        bot = Alpha4GateBot(
            build_order=build_order,
            logger=logger,
            enable_console=True,
            training_db=db,
            game_id=game_id,
            reward_calculator=reward_calc,
            claude_advisor=claude_advisor,
        )
        result = run_bot(
            bot,
            settings,
            map_name=args.map,
            opponent_difficulty=args.difficulty,
            realtime=False,
        )
        logger.stop()
        reward_calc.close_game_log()

        result_str = "win" if result == Result.Victory else "loss"

        # Record terminal transition and game summary
        bot.record_final_transition(result_str)
        db.store_game(
            game_id=game_id,
            map_name=args.map,
            difficulty=args.difficulty or 1,
            result=result_str,
            duration_secs=bot.time if hasattr(bot, "time") else 0.0,
            total_reward=0.0,
            model_version=model_version,
        )

        opponent = f"built-in-{args.difficulty or 'easy'}"
        record_game(
            games,
            map_name=args.map,
            opponent=opponent,
            result=result_str,
            duration_seconds=bot.time if hasattr(bot, "time") else 0.0,
            build_order_used=args.build_order,
            score=0,
        )
        print(f"Result: {result}")

    reward_calc.close()
    transition_count = db.get_transition_count()
    db.close()
    save_stats(games, stats_path)
    print(f"\nBatch complete. Stats saved to {stats_path}")
    print(f"Transitions recorded: {transition_count} -> {settings.data_dir / 'training.db'}")


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
