"""Launch SC2 via burnysc2, verify connection, run a bot."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from sc2.data import Race, Result
from sc2.main import run_game
from sc2.player import Bot, Computer

from alpha4gate.config import Settings

if TYPE_CHECKING:
    from sc2.bot_ai import BotAI


def run_bot(
    bot: BotAI,
    settings: Settings,
    *,
    map_name: str = "Simple64",
    opponent_race: Race = Race.Random,
    opponent_difficulty: int | None = None,
    realtime: bool = False,
    save_replay: bool = True,
    game_time_limit: int | None = None,
) -> Result:
    """Launch SC2 and run a bot against the built-in AI.

    Args:
        bot: A BotAI subclass instance to run.
        settings: Project settings (SC2PATH, replay dir, etc.).
        map_name: SC2 map name (must exist in SC2 Maps directory).
        opponent_race: Race for the computer opponent.
        opponent_difficulty: AI difficulty (1-10). None = Easy (default in burnysc2).
        realtime: Whether to run in realtime mode.
        save_replay: Whether to save a replay file.
        game_time_limit: Optional game time limit in seconds.

    Returns:
        Game result (Victory, Defeat, Tie, Undecided).
    """
    # Ensure SC2PATH is set for burnysc2's path resolution
    os.environ["SC2PATH"] = str(settings.sc2_path)

    from sc2 import maps
    from sc2.data import Difficulty

    map_settings = maps.get(map_name)

    difficulty = Difficulty.Easy
    if opponent_difficulty is not None:
        difficulty = Difficulty(opponent_difficulty)

    replay_path: str | None = None
    if save_replay:
        settings.ensure_dirs()
        replay_path = str(settings.replay_dir / f"game_{map_name}.SC2Replay")

    result = run_game(
        map_settings=map_settings,
        players=[
            Bot(Race.Protoss, bot),
            Computer(opponent_race, difficulty),
        ],
        realtime=realtime,
        save_replay_as=replay_path,
        game_time_limit=game_time_limit,
    )

    # run_game returns Result for single bot vs Computer
    if isinstance(result, list):
        return result[0] or Result.Undecided
    return result
