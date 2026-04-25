"""Spike A from observer-restriction-workarounds-investigation.md §6.

Question: does ``client.debug_show_map()`` change a participant bot's
``self.enemy_units`` / ``self.enemy_structures`` perception, or is it
rendering-only?

Outcome decides whether we can build the wide-single-window viewer (path
4.1(b) in the investigation) where one of the two playing bots has
show_map on (and thus full-map vision in its rendered window) without
poisoning training.

Setup: 1 bot (PerceptionProbeBot) vs Computer.Easy on Simple64. Bot does
NOTHING combat-related — just trains probes that keep mining. This means
no probe scouting, no enemy contact, so any rise in
``self.enemy_units`` / ``self.enemy_structures`` mid-game is attributable
to show_map (the Computer's base is across the map, fully out of vision
without fog-off).

Phases:
  * iter   0-99: baseline (show_map OFF). Expect counts ≈ 0 most of the time.
  * iter 100: call ``debug_show_map()`` (toggle ON).
  * iter 100-199: with show_map ON. If perception changes, counts jump.
  * iter 200: call ``debug_show_map()`` again (toggle OFF per docstring).
  * iter 200-299: counts should drop back if perception was affected.

Reads stdout for the summary table at the end.

Run:

    $env:VIRTUAL_ENV=""; $env:UV_PROJECT_ENVIRONMENT=".venv-py312"
    uv run python scripts/spike_show_map.py
"""

from __future__ import annotations

import time

from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.data import Difficulty, Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.main import run_game
from sc2.player import Bot, Computer

TOGGLE_ON_ITER = 100
TOGGLE_OFF_ITER = 200
END_ITER = 300


class PerceptionProbeBot(BotAI):
    """Trains probes; never scouts or fights. Logs perception each step."""

    def __init__(self) -> None:
        super().__init__()
        self.show_map_on: bool = False
        self.samples: list[tuple[int, bool, int, int]] = []
        # (iteration, show_map_on, n_enemy_units, n_enemy_structures)

    async def on_step(self, iteration: int) -> None:
        if iteration == TOGGLE_ON_ITER:
            print(
                f"[spike] iter={iteration}: TOGGLE show_map ON "
                f"(calling debug_show_map())"
            )
            await self.client.debug_show_map()
            self.show_map_on = True

        if iteration == TOGGLE_OFF_ITER:
            print(
                f"[spike] iter={iteration}: TOGGLE show_map OFF "
                f"(calling debug_show_map() again)"
            )
            await self.client.debug_show_map()
            self.show_map_on = False

        n_enemy_units = self.enemy_units.amount
        n_enemy_structs = self.enemy_structures.amount
        self.samples.append(
            (iteration, self.show_map_on, n_enemy_units, n_enemy_structs)
        )

        if iteration % 20 == 0 or iteration in (
            TOGGLE_ON_ITER - 1,
            TOGGLE_ON_ITER,
            TOGGLE_ON_ITER + 1,
            TOGGLE_OFF_ITER - 1,
            TOGGLE_OFF_ITER,
            TOGGLE_OFF_ITER + 1,
        ):
            print(
                f"[spike] iter={iteration:3d} show_map={'ON ' if self.show_map_on else 'OFF'} "
                f"enemy_units={n_enemy_units:3d} "
                f"enemy_structures={n_enemy_structs:3d}"
            )

        if iteration >= END_ITER:
            self._print_summary()
            await self.client.leave()
            return

        if not self.townhalls:
            return
        if (
            self.can_afford(UnitTypeId.PROBE)
            and self.supply_left > 0
            and self.workers.amount < 16
            and self.townhalls.first.is_idle
        ):
            self.townhalls.first.train(UnitTypeId.PROBE)

    def _print_summary(self) -> None:
        def avg(samples: list[tuple[int, bool, int, int]], idx: int) -> float:
            if not samples:
                return 0.0
            return sum(s[idx] for s in samples) / len(samples)

        baseline = [s for s in self.samples if s[0] < TOGGLE_ON_ITER]
        on = [
            s
            for s in self.samples
            if TOGGLE_ON_ITER <= s[0] < TOGGLE_OFF_ITER
        ]
        off_again = [s for s in self.samples if s[0] >= TOGGLE_OFF_ITER]

        print()
        print("=" * 72)
        print("SPIKE A SUMMARY — does debug_show_map() affect perception?")
        print("=" * 72)
        print(
            f"{'phase':<32} {'avg enemy_units':>16} "
            f"{'avg enemy_structures':>22}"
        )
        print(
            f"{'baseline (iter 0-99,   off)':<32} "
            f"{avg(baseline, 2):>16.2f} {avg(baseline, 3):>22.2f}"
        )
        print(
            f"{'show_map ON  (iter 100-199)':<32} "
            f"{avg(on, 2):>16.2f} {avg(on, 3):>22.2f}"
        )
        print(
            f"{'show_map OFF (iter 200-299)':<32} "
            f"{avg(off_again, 2):>16.2f} {avg(off_again, 3):>22.2f}"
        )
        print()

        baseline_struct = avg(baseline, 3)
        on_struct = avg(on, 3)
        if on_struct > max(baseline_struct + 2, baseline_struct * 2):
            verdict = (
                "PERCEPTION-AFFECTING — enemy_structures count jumped after "
                "show_map ON. show_map changes what the server returns "
                "via RequestObservation. Path 4.1(b) is TRAINING-UNSAFE; "
                "use 4.1(a) (sacrificed viewer-bot) instead."
            )
        elif on_struct <= baseline_struct + 1:
            verdict = (
                "RENDERING-ONLY — enemy_structures count did NOT jump after "
                "show_map ON. show_map only affects the rendered window, "
                "not the bot's RequestObservation data. Path 4.1(b) is "
                "TRAINING-SAFE — both bots can play normally and one's "
                "window has full vision."
            )
        else:
            verdict = (
                "AMBIGUOUS — small change in enemy_structures count. "
                "Re-run with longer iterations or different opponent to "
                "get a clearer signal."
            )

        print("VERDICT:")
        print(verdict)
        print("=" * 72)


def main() -> None:
    t0 = time.time()
    print(f"[spike] starting Spike A at t={t0:.1f}")

    result = run_game(
        maps.get("Simple64"),
        [
            Bot(Race.Protoss, PerceptionProbeBot()),
            Computer(Race.Protoss, Difficulty.Easy),
        ],
        realtime=False,
    )
    print(f"[spike] run_game returned: {result!r} after {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
