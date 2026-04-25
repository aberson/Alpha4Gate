"""Observer-support spike — throwaway proof-of-concept for Step 1 (#195).

Spawns a 3-slot ``GameMatch`` with ``[Bot, Bot, Observer()]`` on Simple64 via
a locally-inlined copy of ``_install_observer_dispatch_patch`` +
``_play_observer``, and runs it through burnysc2's ``a_run_multiple_games``
(which spawns one SC2Process per ``needs_sc2`` player — i.e. 3 processes).

Prints checkpoint info for manual verification against the checkpoints (a-e)
in ``documentation/plans/selfplay-viewer-plan.md`` §7 Step 1.

NOT for production — the real patch + coroutine move into
``src/orchestrator/selfplay.py`` in Step 2 (#196). This exists only to
de-risk burnysc2 plumbing before we touch production code.

Run:

    $env:VIRTUAL_ENV=""; $env:UV_PROJECT_ENVIRONMENT=".venv-py312"
    uv run python scripts/spike_observer.py

While running, watch Task Manager for exactly three ``SC2_x64.exe``
processes and visually verify the observer SC2 window shows the full map.

Why ``GameMatch`` and not ``run_game``: burnysc2's ``run_game`` only has a
2-player ``_host_game`` + ``_join_game`` architecture and never spawns a 3rd
SC2Process. ``a_run_multiple_games`` uses ``maintain_SCII_count`` + the
general ``run_match`` which iterates ``players_that_need_sc2`` and spawns a
process per player. Observer inherits ``needs_sc2 = True`` from
``AbstractPlayer`` (``not isinstance(self, Computer)``), so all three slots
get their own SC2 client.
"""

from __future__ import annotations

import asyncio
import os
import time

import sc2.main as _sc2_main
from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.client import Client
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.main import GameMatch, a_run_multiple_games
from sc2.player import Bot, Observer
from sc2.portconfig import Portconfig

_OBS_PATCH_INSTALLED = False


async def _play_observer(
    client: Client,
    observed_player_id: int = 1,
    portconfig: Portconfig | None = None,
    realtime: bool = False,
    game_step: int = 8,
) -> object:
    """Drive an observer client: join, loop, leave."""
    await client.join_game(
        name=None,
        race=None,
        observed_player_id=observed_player_id,
        portconfig=portconfig,
    )
    print(
        f"[spike] observer joined: pid={os.getpid()} "
        f"observed_player_id={observed_player_id}"
    )

    frame = 0
    while True:
        await client.observation()
        if client._game_result:
            print(
                f"[spike] CHECKPOINT (d) observer _game_result="
                f"{client._game_result}"
            )
            break
        frame += 1
        if frame % 100 == 0:
            print(f"[spike] observer frame={frame}")
        if not realtime:
            await client.step(game_step)

    try:
        await client.leave()
    except Exception as e:
        print(f"[spike] observer leave swallowed: {type(e).__name__}: {e}")
    return client._game_result


def _install_observer_dispatch_patch() -> None:
    """Idempotent monkey-patch of sc2.main._play_game to recognise Observer."""
    global _OBS_PATCH_INSTALLED
    if _OBS_PATCH_INSTALLED:
        return

    orig_play = _sc2_main._play_game

    async def _dispatch(player, client, realtime, portconfig, **kwargs):
        if isinstance(player, Observer):
            return await _play_observer(
                client,
                observed_player_id=1,
                portconfig=portconfig,
                realtime=realtime,
            )
        return await orig_play(player, client, realtime, portconfig, **kwargs)

    _sc2_main._play_game = _dispatch
    _OBS_PATCH_INSTALLED = True
    print("[spike] _play_game dispatch patch installed")


class AggroBot(BotAI):
    """Minimal probe-rush bot so the game actually ends."""

    async def on_step(self, iteration: int) -> None:
        if not self.townhalls:
            return
        if (
            self.can_afford(UnitTypeId.PROBE)
            and self.supply_left > 0
            and self.workers.amount < 15
            and self.townhalls.first.is_idle
        ):
            self.townhalls.first.train(UnitTypeId.PROBE)
        if iteration > 200 and self.workers and self.enemy_start_locations:
            target = self.enemy_start_locations[0]
            for probe in self.workers:
                probe.attack(target)


def main() -> None:
    t0 = time.time()
    _install_observer_dispatch_patch()

    match = GameMatch(
        map_sc2=maps.get("Simple64"),
        players=[
            Bot(Race.Protoss, AggroBot()),
            Bot(Race.Protoss, AggroBot()),
            Observer(),
        ],
        realtime=False,
    )
    print(
        f"[spike] GameMatch needed_sc2_count={match.needed_sc2_count} "
        f"(expect 3)"
    )
    print("[spike] CHECKPOINT (a): check Task Manager NOW for 3x SC2_x64.exe")

    results = asyncio.run(a_run_multiple_games([match]))
    elapsed = time.time() - t0
    print(
        f"[spike] CHECKPOINT (c): a_run_multiple_games returned after "
        f"{elapsed:.1f}s: {results!r}"
    )


if __name__ == "__main__":
    main()
