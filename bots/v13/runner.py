"""CLI entry point: game launch, mode selection, web server."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from bots.v13.build_orders import BuildOrder
from bots.v13.config import Settings, load_settings

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
        "--game-time-limit",
        type=int,
        default=1800,
        metavar="SECS",
        help=(
            "In-game time (seconds) before burnysc2 declares a Tie. "
            "Default 1800 (30 min) — large enough that v0 vs AI games "
            "play to natural conclusion rather than auto-Tie at 5 min."
        ),
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
    parser.add_argument(
        "--ensure-pretrain",
        action="store_true",
        help=(
            "Before starting --train rl, run imitation training once if "
            "no v0_pretrain checkpoint exists. Required for the imitation-init "
            "path to work on a fresh data dir. No-op if v0_pretrain already exists."
        ),
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


def _ensure_pretrain_checkpoint(settings: Settings, hyperparams_path: Path) -> None:
    """Run imitation training if no v0_pretrain checkpoint exists.

    This is the opt-in safety net for the ``use_imitation_init`` flow in
    hyperparams.json: it turns "RL training silently falls back to a
    fresh model because no one ran --train imitation first" into an
    explicit, observable one-time step.
    """
    from bots.v13.learning.database import TrainingDB
    from bots.v13.learning.imitation import run_imitation_training

    checkpoint_dir = settings.data_dir / "checkpoints"
    pretrain = checkpoint_dir / "v0_pretrain.zip"
    if pretrain.exists():
        _log.info("--ensure-pretrain: %s already exists, skipping", pretrain)
        return

    _log.info("--ensure-pretrain: %s missing, running imitation training", pretrain)
    db = TrainingDB(settings.data_dir / "training.db")
    try:
        result = run_imitation_training(
            db=db,
            checkpoint_dir=checkpoint_dir,
            hyperparams_path=hyperparams_path if hyperparams_path.exists() else None,
        )
    finally:
        db.close()
    _log.info("--ensure-pretrain: imitation complete: %s", result)


def _run_training(settings: Settings, args: argparse.Namespace) -> None:
    """Run training (imitation or RL)."""
    if args.train == "imitation":
        from bots.v13.learning.database import TrainingDB
        from bots.v13.learning.imitation import run_imitation_training

        db = TrainingDB(settings.data_dir / "training.db")
        result = run_imitation_training(
            db=db,
            checkpoint_dir=settings.data_dir / "checkpoints",
            hyperparams_path=settings.data_dir / "hyperparams.json",
        )
        db.close()
        print(f"Imitation training complete: {result}")

    elif args.train == "rl":
        from bots.v13.learning.trainer import TrainingOrchestrator

        reward_rules = settings.data_dir / "reward_rules.json"
        hyperparams = settings.data_dir / "hyperparams.json"

        if getattr(args, "ensure_pretrain", False):
            _ensure_pretrain_checkpoint(settings, hyperparams)

        orchestrator = TrainingOrchestrator(
            checkpoint_dir=settings.data_dir / "checkpoints",
            db_path=settings.data_dir / "training.db",
            reward_rules_path=reward_rules if reward_rules.exists() else None,
            hyperparams_path=hyperparams if hyperparams.exists() else None,
            map_name=args.map,
            initial_difficulty=args.difficulty or 1,
            replay_dir=settings.replay_dir,
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

    from bots.v13.api import configure
    from bots.v13.error_log import install_error_log_handler
    from bots.v13.learning.daemon import load_daemon_config

    # Attach the ERROR-level log ring buffer to the root logger as
    # early as possible so backend errors that happen *during* startup
    # (not just after the FastAPI lifespan fires) still land in the
    # alerts pipeline. Idempotent.
    install_error_log_handler()

    daemon_config = load_daemon_config(settings.data_dir / "daemon_config.json")
    # Cross-version evolve state always lives at repo-root data/,
    # regardless of which bot version's per-version data_dir is active.
    from orchestrator.registry import _repo_root
    evolve_dir = _repo_root() / "data"
    configure(
        settings.data_dir, settings.log_dir, settings.replay_dir,
        api_key=settings.anthropic_api_key,
        daemon_config=daemon_config,
        evolve_dir=evolve_dir,
    )

    if daemon:
        from bots.v13.api import _daemon

        if _daemon is not None:
            _daemon.start()
            _log.info("Training daemon auto-started (--daemon flag)")

    uvicorn.run(
        "bots.v13.api:app",
        host="0.0.0.0",
        port=settings.web_ui_port,
        reload=False,
    )


def _start_server_background(settings: Settings) -> None:
    """Start the FastAPI server in a background daemon thread.

    Skips startup if the port is already in use (e.g. a persistent
    ``--serve`` process is already running). This prevents the
    dashboard from going offline when game processes exit.
    """
    import socket
    import threading

    import uvicorn

    # Check if a server is already listening — if so, skip.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex(("127.0.0.1", settings.web_ui_port)) == 0:
            _log.info(
                "API server already running on port %s, skipping background start",
                settings.web_ui_port,
            )
            return

    from bots.v13.api import configure
    from orchestrator.registry import _repo_root

    configure(
        settings.data_dir, settings.log_dir, settings.replay_dir,
        api_key=settings.anthropic_api_key,
        evolve_dir=_repo_root() / "data",
    )
    config = uvicorn.Config(
        "bots.v13.api:app",
        host="0.0.0.0",
        port=settings.web_ui_port,
        reload=False,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    print(f"API server started on http://localhost:{settings.web_ui_port}")


def _run_single_game(
    settings: Settings,
    args: argparse.Namespace,
    *,
    start_server: bool = True,
) -> None:
    """Run a single game vs AI.

    ``start_server`` gates the in-process uvicorn background thread. The
    default is ``True`` (the long-standing behavior for
    ``python -m bots.v0.runner --map X``, which operators rely on to get
    the dashboard for free when launching a one-off game). The ladder
    entry point (``python -m bots.v0 --role solo``) passes ``False`` so
    it does not duplicate a running backend — see
    ``memory/feedback_backend_lifecycle.md``.
    """
    import uuid

    from bots.v13.bot import Alpha4GateBot
    from bots.v13.connection import run_bot
    from bots.v13.learning.neural_engine import DecisionMode
    from bots.v13.learning.rewards import RewardCalculator
    from bots.v13.logger import GameLogger

    if start_server:
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
        from bots.v13.api import ws_manager
        from bots.v13.claude_advisor import ClaudeAdvisor

        claude_advisor = ClaudeAdvisor(
            data_dir=settings.data_dir,
            ws_manager=ws_manager,
        )

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

    # Record to training DB so games show in dashboard Game History
    from bots.v13.learning.database import TrainingDB

    db = TrainingDB(settings.data_dir / "training.db")

    decision_mode = DecisionMode(args.decision_mode)
    bot = Alpha4GateBot(
        build_order=build_order,
        logger=logger,
        decision_mode=decision_mode,
        model_path=args.model_path,
        claude_advisor=claude_advisor,
        reward_calculator=reward_calc,
        training_db=db,
        game_id=game_id,
    )
    result = run_bot(
        bot,
        settings,
        map_name=args.map,
        opponent_difficulty=args.difficulty,
        realtime=args.realtime,
        game_time_limit=args.game_time_limit,
    )

    logger.stop()
    reward_calc.close()

    # Store game result in training DB for dashboard visibility
    from sc2.data import Result

    result_str = "win" if result == Result.Victory else "loss"
    bot.record_final_transition(result_str)
    model_version = args.model_path or args.decision_mode
    db.store_game(
        game_id=game_id,
        map_name=args.map,
        difficulty=args.difficulty or 1,
        result=result_str,
        duration_secs=bot.time if hasattr(bot, "time") else 0.0,
        total_reward=reward_calc.episode_total,
        model_version=str(model_version),
    )
    db.close()

    print(f"\nGame result: {result}")


def _run_batch(settings: Settings, args: argparse.Namespace) -> None:
    """Run N games in sequence, recording transitions for training."""
    import socket
    import uuid

    from sc2.data import Result

    from bots.v13.batch_runner import load_stats, record_game, save_stats
    from bots.v13.bot import Alpha4GateBot
    from bots.v13.connection import run_bot
    from bots.v13.learning.database import TrainingDB

    # Guard: abort if the API server is already running (likely with daemon),
    # since concurrent game launches cause SC2 connection errors.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex(("127.0.0.1", settings.web_ui_port)) == 0:
            _log.error(
                "API server already running on port %d — this likely means a "
                "daemon is spawning games concurrently. Shut it down first "
                "(POST /api/shutdown or kill the process) before running "
                "--batch.",
                settings.web_ui_port,
            )
            raise SystemExit(1)
    from bots.v13.learning.rewards import RewardCalculator
    from bots.v13.logger import GameLogger

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
        from bots.v13.api import ws_manager
        from bots.v13.claude_advisor import ClaudeAdvisor

        claude_advisor = ClaudeAdvisor(
            data_dir=settings.data_dir,
            ws_manager=ws_manager,
        )

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
            game_time_limit=args.game_time_limit,
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
            total_reward=reward_calc.episode_total,
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
    from bots.v13.build_orders import default_4gate, load_build_orders

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
