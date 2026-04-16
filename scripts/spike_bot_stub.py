"""Phase 0 spike — minimal bot stub for subprocess self-play.

Joins an already-hosted SC2 match via the burnysc2 ladder protocol:
  --GamePort X        -> WebSocket port exposed by the Proxy layer
  --LadderServer H    -> host (usually 127.0.0.1)
  --StartPort N       -> base port used by both bots to derive a shared Portconfig
  --RealTime          -> passthrough realtime flag (default: False)

Spike-specific extras (prepended via launch_list):
  --role {p1|p2}      -> identity label
  --result-out PATH   -> where to write the per-bot result JSON

Gameplay: both bots mine with workers and build a Pylon, producing observable
game activity. P2 surrenders after a fixed number of steps so the game
terminates with a decisive winner (P1 Victory, P2 Defeat) in <60s wall-clock.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sc2.bot_ai import BotAI
from sc2.data import Race, Result
from sc2.ids.unit_typeid import UnitTypeId
from sc2.main import play_from_websocket
from sc2.player import Bot
from sc2.portconfig import Portconfig

SURRENDER_STEP = {"p1": None, "p2": 200}


class SpikeBot(BotAI):
    def __init__(self, role: str, result_out: Path) -> None:
        super().__init__()
        self.role = role
        self.result_out = result_out
        self.last_iteration = 0
        self.actions_issued = 0

    def _write_progress(self, result_name: str | None) -> None:
        self.result_out.parent.mkdir(parents=True, exist_ok=True)
        self.result_out.write_text(
            json.dumps(
                {
                    "role": self.role,
                    "result": result_name,
                    "steps": self.last_iteration,
                    "actions_issued": self.actions_issued,
                }
            )
        )

    async def on_step(self, iteration: int) -> None:
        self.last_iteration = iteration

        if self.can_afford(UnitTypeId.PROBE) and self.townhalls.ready.exists:
            nexus = self.townhalls.ready.first
            if nexus.is_idle and self.supply_left > 0:
                nexus.train(UnitTypeId.PROBE)
                self.actions_issued += 1

        if (
            self.supply_used >= 14
            and not self.structures(UnitTypeId.PYLON)
            and self.can_afford(UnitTypeId.PYLON)
            and self.already_pending(UnitTypeId.PYLON) == 0
        ):
            if self.workers:
                pos = self.start_location.towards(self.game_info.map_center, 6)
                await self.build(UnitTypeId.PYLON, near=pos)
                self.actions_issued += 1

        if iteration % 50 == 0:
            self._write_progress(None)

        surrender_at = SURRENDER_STEP.get(self.role)
        if surrender_at is not None and iteration >= surrender_at:
            self._write_progress("Surrendering")
            await self.client.leave()

    async def on_end(self, game_result: Result) -> None:
        self._write_progress(game_result.name if game_result else None)


def build_portconfig(start_port: int) -> Portconfig:
    return Portconfig(
        server_ports=[start_port + 2, start_port + 3],
        player_ports=[[start_port + 4, start_port + 5]],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True, choices=["p1", "p2"])
    parser.add_argument("--result-out", required=True, type=Path)
    parser.add_argument("--GamePort", required=True, type=int)
    parser.add_argument("--LadderServer", default="127.0.0.1", type=str)
    parser.add_argument("--StartPort", required=True, type=int)
    parser.add_argument("--RealTime", action="store_true", default=False)
    args = parser.parse_args()

    pc = build_portconfig(args.StartPort)
    bot = SpikeBot(args.role, args.result_out)
    ws_url = f"ws://{args.LadderServer}:{args.GamePort}/sc2api"

    asyncio.run(
        play_from_websocket(
            ws_url,
            Bot(Race.Protoss, bot, name=f"Spike-{args.role}"),
            realtime=args.RealTime,
            portconfig=pc,
        )
    )


if __name__ == "__main__":
    main()
