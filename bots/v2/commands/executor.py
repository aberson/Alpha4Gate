"""Command executor: translates CommandPrimitives into bot actions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

from bots.v2.commands.primitives import CommandAction, CommandPrimitive
from bots.v2.commands.recipes import expand_tech
from bots.v2.decision_engine import StrategicState

if TYPE_CHECKING:
    from bots.v2.bot import Alpha4GateBot

_log = logging.getLogger(__name__)

# Maps lowercase command target strings -> UnitTypeId
_UNIT_MAP: dict[str, UnitTypeId] = {
    "stalker": UnitTypeId.STALKER,
    "stalkers": UnitTypeId.STALKER,
    "zealot": UnitTypeId.ZEALOT,
    "zealots": UnitTypeId.ZEALOT,
    "immortal": UnitTypeId.IMMORTAL,
    "immortals": UnitTypeId.IMMORTAL,
    "sentry": UnitTypeId.SENTRY,
    "sentries": UnitTypeId.SENTRY,
    "colossus": UnitTypeId.COLOSSUS,
    "colossi": UnitTypeId.COLOSSUS,
    "voidray": UnitTypeId.VOIDRAY,
    "voidrays": UnitTypeId.VOIDRAY,
    "probe": UnitTypeId.PROBE,
    "probes": UnitTypeId.PROBE,
    "observer": UnitTypeId.OBSERVER,
    "observers": UnitTypeId.OBSERVER,
    "high_templar": UnitTypeId.HIGHTEMPLAR,
    "dark_templar": UnitTypeId.DARKTEMPLAR,
    "archon": UnitTypeId.ARCHON,
    "phoenix": UnitTypeId.PHOENIX,
    "carrier": UnitTypeId.CARRIER,
    "carriers": UnitTypeId.CARRIER,
    "tempest": UnitTypeId.TEMPEST,
    "disruptor": UnitTypeId.DISRUPTOR,
}

# Maps lowercase command target strings -> structure UnitTypeId
_STRUCTURE_MAP: dict[str, UnitTypeId] = {
    "pylon": UnitTypeId.PYLON,
    "gateway": UnitTypeId.GATEWAY,
    "forge": UnitTypeId.FORGE,
    "stargate": UnitTypeId.STARGATE,
    "robotics_facility": UnitTypeId.ROBOTICSFACILITY,
    "robotics_bay": UnitTypeId.ROBOTICSBAY,
    "twilight_council": UnitTypeId.TWILIGHTCOUNCIL,
    "templar_archives": UnitTypeId.TEMPLARARCHIVE,
    "dark_shrine": UnitTypeId.DARKSHRINE,
    "fleet_beacon": UnitTypeId.FLEETBEACON,
    "nexus": UnitTypeId.NEXUS,
    "assimilator": UnitTypeId.ASSIMILATOR,
    "cybernetics_core": UnitTypeId.CYBERNETICSCORE,
    "photon_cannon": UnitTypeId.PHOTONCANNON,
    "shield_battery": UnitTypeId.SHIELDBATTERY,
}

# Structures that should never be multi-built. Prevents a race where two
# command sources emit "build X" in the tick gap before burnysc2's
# already_pending registers the first order.
# True singletons: only one ever makes sense (upgrade buildings).
_STRUCTURE_SINGLETON: frozenset[UnitTypeId] = frozenset({
    UnitTypeId.TWILIGHTCOUNCIL,
    UnitTypeId.ROBOTICSBAY,
    UnitTypeId.TEMPLARARCHIVE,
    UnitTypeId.DARKSHRINE,
    UnitTypeId.FLEETBEACON,
})
# Early-game cap: before this many seconds, allow only one. Late game, more
# is legitimate (2 forges for parallel upgrades, 2 CCs for sky protoss).
_EARLY_GAME_SECONDS: float = 360.0
_STRUCTURE_EARLY_GAME_CAP: frozenset[UnitTypeId] = frozenset({
    UnitTypeId.FORGE,
    UnitTypeId.CYBERNETICSCORE,
})


# Production structures for unit types
_PRODUCTION_MAP: dict[UnitTypeId, UnitTypeId] = {
    UnitTypeId.ZEALOT: UnitTypeId.GATEWAY,
    UnitTypeId.STALKER: UnitTypeId.GATEWAY,
    UnitTypeId.SENTRY: UnitTypeId.GATEWAY,
    UnitTypeId.HIGHTEMPLAR: UnitTypeId.GATEWAY,
    UnitTypeId.DARKTEMPLAR: UnitTypeId.GATEWAY,
    UnitTypeId.IMMORTAL: UnitTypeId.ROBOTICSFACILITY,
    UnitTypeId.OBSERVER: UnitTypeId.ROBOTICSFACILITY,
    UnitTypeId.COLOSSUS: UnitTypeId.ROBOTICSFACILITY,
    UnitTypeId.DISRUPTOR: UnitTypeId.ROBOTICSFACILITY,
    UnitTypeId.VOIDRAY: UnitTypeId.STARGATE,
    UnitTypeId.PHOENIX: UnitTypeId.STARGATE,
    UnitTypeId.CARRIER: UnitTypeId.STARGATE,
    UnitTypeId.TEMPEST: UnitTypeId.STARGATE,
    UnitTypeId.PROBE: UnitTypeId.NEXUS,
}

# Upgrade target mapping
_UPGRADE_MAP: dict[str, UpgradeId] = {
    "weapons": UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1,
    "armor": UpgradeId.PROTOSSGROUNDARMORSLEVEL1,
    "shields": UpgradeId.PROTOSSSHIELDSLEVEL1,
    "blink": UpgradeId.BLINKTECH,
    "charge": UpgradeId.CHARGE,
}


@dataclass
class ExecutionResult:
    """Result of executing a command primitive."""

    success: bool
    message: str
    primitives_executed: int


class CommandExecutor:
    """Translates CommandPrimitives into bot actions."""

    def __init__(self, bot: Alpha4GateBot) -> None:
        self._bot = bot

    async def execute(self, cmd: CommandPrimitive) -> ExecutionResult:
        """Translate a primitive into bot actions."""
        action = cmd.action
        target = cmd.target.lower()

        if action == CommandAction.BUILD:
            return await self._execute_build(target, cmd)
        elif action == CommandAction.EXPAND:
            return await self._execute_expand(cmd)
        elif action == CommandAction.DEFEND:
            return self._execute_defend(cmd)
        elif action == CommandAction.ATTACK:
            return self._execute_attack(cmd)
        elif action == CommandAction.SCOUT:
            return self._execute_scout(cmd)
        elif action == CommandAction.TECH:
            return await self._execute_tech(cmd)
        elif action == CommandAction.UPGRADE:
            return await self._execute_upgrade(target)
        elif action == CommandAction.RALLY:
            return self._execute_rally(cmd)

        return ExecutionResult(
            success=False, message=f"Unknown action: {action}", primitives_executed=0
        )

    def resolve_location(
        self, location: str | None, action: CommandAction
    ) -> Point2 | None:
        """Resolve location string to Point2 coordinates."""
        bot = self._bot

        if location is None:
            return self._default_location(action)

        loc = location.lower()
        if loc == "main":
            return bot.start_location
        elif loc == "natural":
            return self._nth_expansion(0)
        elif loc == "third":
            return self._nth_expansion(1)
        elif loc == "fourth":
            return self._nth_expansion(2)
        elif loc == "enemy_main":
            if bot.enemy_start_locations:
                return bot.enemy_start_locations[0]
            return None
        elif loc == "enemy_natural":
            result = bot._enemy_natural()
            if result is not None:
                return Point2(result)
            return None

        return self._default_location(action)

    def _default_location(self, action: CommandAction) -> Point2 | None:
        """Return action-dependent default location."""
        bot = self._bot
        if action == CommandAction.BUILD:
            return bot.start_location
        elif action == CommandAction.ATTACK:
            result = bot._enemy_natural()
            if result is not None:
                return Point2(result)
            if bot.enemy_start_locations:
                return bot.enemy_start_locations[0]
            return None
        return None

    def _nth_expansion(self, n: int) -> Point2 | None:
        """Get the nth closest expansion to start_location (0=natural, 1=third, etc.)."""
        bot = self._bot
        all_expansions: list[Point2] = bot.expansion_locations_list
        candidates = [
            exp
            for exp in all_expansions
            if exp.distance_to(bot.start_location) > 1
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda loc: loc.distance_to(bot.start_location))
        if n < len(candidates):
            return candidates[n]
        return None

    async def _execute_build(
        self, target: str, cmd: CommandPrimitive
    ) -> ExecutionResult:
        """Execute a BUILD command for either a unit or structure."""
        # Check if target is a structure
        structure_id = _STRUCTURE_MAP.get(target)
        if structure_id is not None:
            return await self._build_structure(structure_id, cmd)

        # Check if target is a unit
        unit_id = _UNIT_MAP.get(target)
        if unit_id is not None:
            return self._train_unit(unit_id)

        return ExecutionResult(
            success=False, message=f"Unknown build target: {target}", primitives_executed=0
        )

    async def _build_structure(
        self, structure_id: UnitTypeId, cmd: CommandPrimitive
    ) -> ExecutionResult:
        """Build a structure at the resolved location."""
        bot = self._bot
        capped = (
            structure_id in _STRUCTURE_SINGLETON
            or (
                structure_id in _STRUCTURE_EARLY_GAME_CAP
                and bot.time < _EARLY_GAME_SECONDS
            )
        )
        if capped:
            existing = bot.structures(structure_id).amount
            pending = bot.already_pending(structure_id)
            if existing + pending >= 1:
                return ExecutionResult(
                    success=False,
                    message=(
                        f"Skipping {structure_id.name}: have {existing}"
                        f" + {pending} pending"
                    ),
                    primitives_executed=0,
                )
        if not bot.can_afford(structure_id):
            return ExecutionResult(
                success=False,
                message=f"Cannot afford {structure_id.name} — not enough resources",
                primitives_executed=0,
            )

        if structure_id == UnitTypeId.NEXUS:
            location = self.resolve_location(cmd.location, cmd.action)
            if location is not None:
                await bot.build(UnitTypeId.NEXUS, near=location)
            else:
                await bot.expand_now()
            return ExecutionResult(
                success=True, message="Expanding", primitives_executed=1
            )

        location = self.resolve_location(cmd.location, cmd.action)
        if location is None:
            # Fall back to nearest pylon
            pylons = bot.structures(UnitTypeId.PYLON).ready
            if pylons:
                location = pylons.closest_to(bot.start_location).position
            else:
                location = bot.start_location

        await bot.build(structure_id, near=location)
        return ExecutionResult(
            success=True,
            message=f"Building {structure_id.name}",
            primitives_executed=1,
        )

    def _train_unit(self, unit_id: UnitTypeId) -> ExecutionResult:
        """Train a unit from an idle production structure."""
        bot = self._bot
        if not bot.can_afford(unit_id):
            return ExecutionResult(
                success=False,
                message=f"Cannot afford {unit_id.name} — not enough resources",
                primitives_executed=0,
            )

        production_type = _PRODUCTION_MAP.get(unit_id)
        if production_type is None:
            return ExecutionResult(
                success=False,
                message=f"No production building for {unit_id.name}",
                primitives_executed=0,
            )

        for structure in bot.structures(production_type).idle:
            structure.train(unit_id)
            return ExecutionResult(
                success=True,
                message=f"Training {unit_id.name}",
                primitives_executed=1,
            )

        all_structures = bot.structures(production_type)
        total = len(all_structures)
        idle_count = len(all_structures.idle)
        if total == 0:
            reason = f"No {production_type.name} built — build one first"
        else:
            busy = total - idle_count
            reason = (
                f"No idle {production_type.name} — all {total} busy"
                f" ({busy} training)"
            )
        return ExecutionResult(
            success=False,
            message=reason,
            primitives_executed=0,
        )

    async def _execute_expand(self, cmd: CommandPrimitive) -> ExecutionResult:
        """Execute an EXPAND command."""
        bot = self._bot
        if not bot.can_afford(UnitTypeId.NEXUS):
            return ExecutionResult(
                success=False,
                message="Cannot afford Nexus — not enough resources",
                primitives_executed=0,
            )

        location = self.resolve_location(cmd.location, cmd.action)
        if location is not None:
            await bot.build(UnitTypeId.NEXUS, near=location)
        else:
            await bot.expand_now()
        return ExecutionResult(success=True, message="Expanding", primitives_executed=1)

    def _execute_defend(self, cmd: CommandPrimitive) -> ExecutionResult:
        """Execute a DEFEND command via decision engine override."""
        bot = self._bot
        bot.decision_engine.set_command_override(
            StrategicState.DEFEND, source=cmd.source.value, duration=120.0
        )
        return ExecutionResult(
            success=True, message="Defend override set", primitives_executed=1
        )

    def _execute_attack(self, cmd: CommandPrimitive) -> ExecutionResult:
        """Execute an ATTACK command via decision engine override + target."""
        bot = self._bot
        bot.decision_engine.set_command_override(
            StrategicState.ATTACK, source=cmd.source.value, duration=120.0
        )
        # Set attack target via staging point if location specified
        location = self.resolve_location(cmd.location, cmd.action)
        if location is not None:
            bot._cached_staging_point = (float(location.x), float(location.y))
        return ExecutionResult(
            success=True, message="Attack override set", primitives_executed=1
        )

    def _execute_scout(self, cmd: CommandPrimitive) -> ExecutionResult:
        """Execute a SCOUT command via scout manager."""
        bot = self._bot
        location = self.resolve_location(cmd.location, CommandAction.SCOUT)
        if location is None:
            # Default scout target: enemy main
            if bot.enemy_start_locations:
                location = bot.enemy_start_locations[0]
            else:
                return ExecutionResult(
                    success=False,
                    message="No scout target available",
                    primitives_executed=0,
                )
        bot.scout_manager.force_scout((float(location.x), float(location.y)))
        return ExecutionResult(
            success=True, message="Scout target set", primitives_executed=1
        )

    async def _execute_tech(self, cmd: CommandPrimitive) -> ExecutionResult:
        """Execute a TECH command by expanding into prerequisite builds."""
        target = cmd.target.lower()
        commands = expand_tech(target, cmd.source, cmd.timestamp)
        executed = 0
        for sub_cmd in commands:
            result = await self.execute(sub_cmd)
            if result.success:
                executed += result.primitives_executed
        return ExecutionResult(
            success=executed > 0,
            message=f"Tech {target}: {executed} steps executed",
            primitives_executed=executed,
        )

    async def _execute_upgrade(self, target: str) -> ExecutionResult:
        """Execute an UPGRADE command."""
        bot = self._bot
        upgrade_id = _UPGRADE_MAP.get(target)
        if upgrade_id is None:
            return ExecutionResult(
                success=False,
                message=f"Unknown upgrade: {target}",
                primitives_executed=0,
            )
        bot.research(upgrade_id)
        return ExecutionResult(
            success=True,
            message=f"Researching {upgrade_id.name}",
            primitives_executed=1,
        )

    def _execute_rally(self, cmd: CommandPrimitive) -> ExecutionResult:
        """Execute a RALLY command by updating the staging point."""
        location = self.resolve_location(cmd.location or cmd.target, CommandAction.RALLY)
        if location is None:
            return ExecutionResult(
                success=False, message="Could not resolve rally location", primitives_executed=0
            )
        self._bot._cached_staging_point = (float(location.x), float(location.y))
        return ExecutionResult(
            success=True,
            message=f"Rally point set to ({location.x:.0f}, {location.y:.0f})",
            primitives_executed=1,
        )
