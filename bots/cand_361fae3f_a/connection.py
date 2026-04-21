"""Launch SC2 via burnysc2, verify connection, run a bot."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sc2.data import Race, Result
from sc2.main import run_game
from sc2.player import Bot, Computer

from bots.cand_361fae3f_a.config import Settings

if TYPE_CHECKING:
    from sc2.bot_ai import BotAI


def build_replay_path(
    replay_dir: Path,
    map_name: str,
    *,
    now: datetime | None = None,
) -> Path:
    """Build a unique replay file path for a single game.

    The filename embeds a timestamp suffix so concurrent/sequential games on
    the same map do not overwrite each other's replays. Format:
    ``game_<map_name>_<YYYYMMDDTHHMMSS>.SC2Replay``.

    Args:
        replay_dir: Directory where replays are stored.
        map_name: SC2 map name (used as part of the filename).
        now: Optional datetime injection point for deterministic tests.
            Defaults to ``datetime.now()`` when omitted.

    Returns:
        Absolute-ish path under ``replay_dir`` for this game's replay.
    """
    ts_source = now if now is not None else datetime.now()
    ts = ts_source.strftime("%Y%m%dT%H%M%S")
    return replay_dir / f"game_{map_name}_{ts}.SC2Replay"


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
        replay_path = str(build_replay_path(settings.replay_dir, map_name))

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
