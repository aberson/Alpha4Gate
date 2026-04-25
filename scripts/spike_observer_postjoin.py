"""Observer post-create join spike — Option 2 from #195 blocker.

Tests whether an SC2 client can join a live 1v1 game as an observer AFTER
the game was created as pure 1v1 (no observer in player_setup).

Strategy:
  1. Spawn 3 SC2Processes manually (one per client — host bot, join bot,
     observer).
  2. Call ``create_game`` on the host with ONLY 2 bots — a vanilla 1v1 that
     we know the server accepts.
  3. Build a ``Portconfig(guests=2)`` so the port table has slots for one
     participant-guest (bot2) and one observer-guest.
  4. Start three coroutines in parallel:
       - bot1: standard ``_play_game`` on host client (race=Protoss)
       - bot2: standard ``_play_game`` on join client (race=Protoss)
       - observer: ``client.join_game(race=None, observed_player_id=1,
         portconfig=portconfig)`` on the 3rd client
  5. Log whether the observer's ``join_game`` succeeds or what error the
     server returns.

This is THROWAWAY — if the observer join works, we'll port the mechanism
into ``src/orchestrator/selfplay.py`` in a follow-up plan. If it fails, the
error message tells us whether the server's restriction is just about
``create_game`` player_setup count (in which case another workaround might
exist) or whether observers are flat-out unsupported alongside multi-agent
live games.

Run:

    $env:VIRTUAL_ENV=""; $env:UV_PROJECT_ENVIRONMENT=".venv-py312"
    uv run python scripts/spike_observer_postjoin.py

Expected outcomes:
  - ``OBSERVER JOIN SUCCESS (player_id=N)`` → Option 2 is viable; proceed
    to plan revision.
  - ``OBSERVER JOIN FAILED: ... GameAlreadyStarted`` → observer must join
    before the bots do; retry without the sleep.
  - ``OBSERVER JOIN FAILED: ... InvalidRequest`` or similar → server
    refuses late observer joins; Option 2 is also blocked; pivot.
"""

from __future__ import annotations

import asyncio
import time

from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.client import Client
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.main import _play_game, _setup_host_game
from sc2.player import Bot
from sc2.portconfig import Portconfig
from sc2.sc2process import SC2Process


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


async def _play_observer_client(
    client: Client,
    portconfig: Portconfig,
    observed_player_id: int = 1,
    game_step: int = 8,
) -> object:
    """Observer: call join_game directly (NO dispatch patch this time)."""
    print(
        "[spike] observer: calling join_game"
        f"(race=None, observed_player_id={observed_player_id})..."
    )
    try:
        player_id = await client.join_game(
            race=None,
            observed_player_id=observed_player_id,
            portconfig=portconfig,
        )
        print(
            f"[spike] ====== OBSERVER JOIN SUCCESS "
            f"(player_id={player_id}) ======"
        )
    except Exception as e:
        print(
            f"[spike] ====== OBSERVER JOIN FAILED: "
            f"{type(e).__name__}: {e} ======"
        )
        raise

    frame = 0
    while True:
        await client.observation()
        if client._game_result:
            print(
                f"[spike] observer saw _game_result={client._game_result}"
            )
            break
        frame += 1
        if frame % 100 == 0:
            print(f"[spike] observer frame={frame}")
        await client.step(game_step)

    try:
        await client.leave()
    except Exception as e:
        print(f"[spike] observer leave swallowed: {type(e).__name__}: {e}")

    return client._game_result


async def run_with_observer():
    bot1 = Bot(Race.Protoss, AggroBot())
    bot2 = Bot(Race.Protoss, AggroBot())

    print(f"[spike] spawning 3 SC2Processes at t={time.time():.1f}")
    print("[spike] CHECKPOINT (a): check Task Manager NOW for 3x SC2_x64.exe")

    host_cm = SC2Process()
    join_cm = SC2Process()
    obs_cm = SC2Process()

    host_controller = await host_cm.__aenter__()
    join_controller = await join_cm.__aenter__()
    obs_controller = await obs_cm.__aenter__()

    try:
        await asyncio.gather(
            host_controller.ping(),
            join_controller.ping(),
            obs_controller.ping(),
        )
        print("[spike] all three controllers ping-OK")

        host_client = await _setup_host_game(
            host_controller,
            maps.get("Simple64"),
            [bot1, bot2],
            realtime=False,
        )
        print("[spike] host create_game succeeded (1v1, no observer slot)")

        portconfig = Portconfig(guests=2)
        print(f"[spike] {portconfig}")

        join_client = Client(join_controller._ws)
        obs_client = Client(obs_controller._ws)

        async def bot1_coro():
            return await _play_game(bot1, host_client, False, portconfig)

        async def bot2_coro():
            return await _play_game(bot2, join_client, False, portconfig)

        async def obs_coro():
            return await _play_observer_client(obs_client, portconfig)

        print("[spike] gathering 3 tasks...")
        results = await asyncio.gather(
            bot1_coro(),
            bot2_coro(),
            obs_coro(),
            return_exceptions=True,
        )

        for i, r in enumerate(results):
            task_name = ["bot1", "bot2", "observer"][i]
            if isinstance(r, Exception):
                print(
                    f"[spike] {task_name} exception: "
                    f"{type(r).__name__}: {r}"
                )
            else:
                print(f"[spike] {task_name} result: {r!r}")

        return results

    finally:
        for cm, name in [
            (obs_cm, "obs"),
            (join_cm, "join"),
            (host_cm, "host"),
        ]:
            try:
                await cm.__aexit__(None, None, None)
            except Exception as e:
                print(f"[spike] {name} close swallowed: {e}")


def main() -> None:
    t0 = time.time()
    print(f"[spike] observer post-create-join spike at t={t0:.1f}")
    asyncio.run(run_with_observer())
    print(f"[spike] total elapsed: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
