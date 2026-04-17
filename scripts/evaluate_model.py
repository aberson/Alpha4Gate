"""Evaluate model performance across decision modes.

Runs N games for a given mode and reports win/loss and average game duration.

Usage:
  uv run python scripts/evaluate_model.py --mode rules --games 3
  uv run python scripts/evaluate_model.py --mode neural --model-path checkpoints/v0.zip
"""

from __future__ import annotations

import argparse
import sys

from bots.v0.bot import Alpha4GateBot
from bots.v0.build_orders import default_4gate
from bots.v0.config import load_settings
from bots.v0.connection import run_bot
from bots.v0.learning.neural_engine import DecisionMode
from bots.v0.logger import GameLogger
from sc2.data import Result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Alpha4Gate model")
    parser.add_argument(
        "--mode", required=True, choices=["rules", "neural", "hybrid"],
    )
    parser.add_argument("--model-path", default=None)
    parser.add_argument("--games", type=int, default=3)
    parser.add_argument("--difficulty", type=int, default=1)
    parser.add_argument("--map", default="Simple64")
    args = parser.parse_args()

    if args.mode in ("neural", "hybrid") and args.model_path is None:
        print("ERROR: --model-path required for neural/hybrid mode")
        sys.exit(1)

    settings = load_settings()
    build_order = default_4gate()
    decision_mode = DecisionMode(args.mode)

    results: list[dict[str, object]] = []

    for i in range(args.games):
        print(f"\n=== Eval Game {i + 1}/{args.games} ({args.mode}) ===")
        logger = GameLogger(log_dir=settings.log_dir)
        logger.start()

        bot = Alpha4GateBot(
            build_order=build_order,
            logger=logger,
            enable_console=True,
            decision_mode=decision_mode,
            model_path=args.model_path,
        )
        result = run_bot(
            bot,
            settings,
            map_name=args.map,
            opponent_difficulty=args.difficulty,
            realtime=False,
        )
        logger.stop()

        result_str = "win" if result == Result.Victory else "loss"
        duration = bot.time if hasattr(bot, "time") else 0.0
        results.append({
            "game": i + 1,
            "result": result_str,
            "duration": duration,
        })
        print(f"Result: {result_str}  Duration: {duration:.0f}s")

    # Summary
    wins = sum(1 for r in results if r["result"] == "win")
    total = len(results)
    avg_dur = sum(float(r["duration"]) for r in results) / max(total, 1)

    print(f"\n{'='*50}")
    print(f"Mode: {args.mode}")
    print(f"Games: {total}")
    print(f"Wins: {wins}/{total} ({100*wins/max(total,1):.0f}%)")
    print(f"Avg duration: {avg_dur:.0f}s")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
