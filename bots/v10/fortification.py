"""Fortification manager: defensive structure scaling based on enemy advantage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId

from bots.v10.macro_manager import MacroDecision

if TYPE_CHECKING:
    from sc2.bot_ai import BotAI


def _clamp(value: int, lo: int, hi: int) -> int:
    """Clamp an integer to [lo, hi]."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


class FortificationManager:
    """Decides how many defensive structures to build based on enemy supply advantage.

    Scaling formula:
        enemy_advantage = enemy_supply - own_supply
        count = clamp(enemy_advantage // defense_scaling_divisor, min_defenses, max_defenses)

    Batteries are prioritised over cannons because they only require a
    CyberneticsCore (already built by 4-gate), whereas cannons need a Forge.
    """

    def __init__(
        self,
        defense_scaling_divisor: float,
        max_defenses: int,
        min_defenses: int = 1,
    ) -> None:
        self.defense_scaling_divisor = defense_scaling_divisor
        self.max_defenses = max_defenses
        self.min_defenses = min_defenses
        self._reactive_emplacements_by_nexus: dict[int, set[str]] = {}
        self._battery_placed_nexus_tags: set[int] = set()

    def desired_count(self, enemy_supply: float, own_supply: float) -> int:
        """Return the desired number of each defensive structure type."""
        advantage = enemy_supply - own_supply
        if advantage <= 0:
            return self.min_defenses
        raw = int(advantage // self.defense_scaling_divisor)
        return _clamp(raw, self.min_defenses, self.max_defenses)

    def evaluate(
        self,
        *,
        enemy_supply: float,
        own_supply: float,
        existing_cannons: int,
        existing_batteries: int,
        has_forge: bool,
        forge_building: bool,
        has_cybernetics_core: bool,
        has_pylon_near_natural: bool,
    ) -> list[MacroDecision]:
        """Return a list of defensive build decisions.

        Args:
            enemy_supply: Total visible enemy army supply.
            own_supply: Own army supply.
            existing_cannons: Number of completed + in-progress PhotonCannons.
            existing_batteries: Number of completed + in-progress ShieldBatteries.
            has_forge: Whether a completed Forge exists.
            forge_building: Whether a Forge is currently under construction.
            has_cybernetics_core: Whether a completed CyberneticsCore exists.
            has_pylon_near_natural: Whether a powered Pylon exists near the natural.

        Returns:
            Ordered list of MacroDecision objects to execute.
        """
        count = self.desired_count(enemy_supply, own_supply)
        decisions: list[MacroDecision] = []

        # Pylon for power near natural (needed before cannons/batteries)
        if not has_pylon_near_natural:
            decisions.append(
                MacroDecision(
                    action="build",
                    target="Pylon",
                    reason="fortify: need pylon near natural for defensive power",
                )
            )

        # Batteries first (only need CyberneticsCore which 4-gate already builds)
        if has_cybernetics_core:
            needed_batteries = count - existing_batteries
            for _ in range(max(0, needed_batteries)):
                decisions.append(
                    MacroDecision(
                        action="build",
                        target="ShieldBattery",
                        reason="fortify: shield battery for defense",
                    )
                )

        # Forge prerequisite for cannons
        if not has_forge and not forge_building:
            decisions.append(
                MacroDecision(
                    action="build",
                    target="Forge",
                    reason="fortify: need Forge for PhotonCannons",
                )
            )

        # Cannons (require Forge)
        if has_forge:
            needed_cannons = count - existing_cannons
            for _ in range(max(0, needed_cannons)):
                decisions.append(
                    MacroDecision(
                        action="build",
                        target="PhotonCannon",
                        reason="fortify: photon cannon for static defense",
                    )
                )

        return decisions

    async def auto_battery(self, bot: BotAI) -> None:
        """Auto-place one Shield Battery per Nexus when taken.

        Idempotent across steps via `_battery_placed_nexus_tags`. Requires
        a completed CyberneticsCore (Battery prerequisite).
        """
        if not bot.structures(UnitTypeId.CYBERNETICSCORE).ready.exists:
            return
        for nexus in bot.townhalls.ready:
            tag = int(nexus.tag)
            if tag in self._battery_placed_nexus_tags:
                continue
            if bot.minerals < 100:
                continue
            if bot.structures(UnitTypeId.SHIELDBATTERY).closer_than(7, nexus).exists:
                self._battery_placed_nexus_tags.add(tag)
                continue
            near_pt = nexus.position.towards(bot.game_info.map_center, 4.0)
            placement = await bot.find_placement(
                UnitTypeId.SHIELDBATTERY, near_pt, placement_step=1,
            )
            if placement is None:
                continue
            workers = bot.workers
            if not workers:
                continue
            worker = workers.closest_to(placement)
            worker.build(UnitTypeId.SHIELDBATTERY, placement)
            self._battery_placed_nexus_tags.add(tag)

    def reactive_defend_emplacements(
        self,
        *,
        threatened_nexus_tags: list[int],
        existing_batteries: int,
        existing_cannons: int,
        has_forge: bool,
        has_cybernetics: bool,
    ) -> list[MacroDecision]:
        del existing_batteries, existing_cannons
        decisions: list[MacroDecision] = []
        battery_placed = False
        cannon_placed = False
        for tag in threatened_nexus_tags:
            placed = self._reactive_emplacements_by_nexus.setdefault(tag, set())
            if not battery_placed and has_cybernetics and "battery" not in placed:
                decisions.append(
                    MacroDecision(
                        action="build",
                        target="ShieldBattery",
                        reason="reactive defend: battery at threatened nexus",
                    )
                )
                placed.add("battery")
                battery_placed = True
            if not cannon_placed and has_forge and "cannon" not in placed:
                decisions.append(
                    MacroDecision(
                        action="build",
                        target="PhotonCannon",
                        reason="reactive defend: cannon at threatened nexus",
                    )
                )
                placed.add("cannon")
                cannon_placed = True
            if battery_placed and cannon_placed:
                break
        return decisions
