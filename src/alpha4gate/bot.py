"""Main BotAI subclass: on_step() orchestrates all decision layers."""

from __future__ import annotations

import logging
from typing import Any

from sc2.bot_ai import BotAI
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from alpha4gate.build_orders import BuildOrder
from alpha4gate.console import print_status
from alpha4gate.decision_engine import DecisionEngine, GameSnapshot, StrategicState
from alpha4gate.learning.neural_engine import DecisionMode, NeuralDecisionEngine
from alpha4gate.logger import GameLogger
from alpha4gate.macro_manager import MacroDecision, MacroManager
from alpha4gate.micro import MicroController
from alpha4gate.observer import observe
from alpha4gate.scouting import ScoutManager

_log = logging.getLogger(__name__)

# Approximate supply costs for common unit types (used for enemy supply estimation)
_SUPPLY_COST: dict[UnitTypeId, int] = {
    UnitTypeId.PROBE: 1, UnitTypeId.SCV: 1, UnitTypeId.DRONE: 1,
    UnitTypeId.ZEALOT: 2, UnitTypeId.STALKER: 2, UnitTypeId.SENTRY: 2,
    UnitTypeId.IMMORTAL: 4, UnitTypeId.COLOSSUS: 6, UnitTypeId.ARCHON: 4,
    UnitTypeId.VOIDRAY: 4, UnitTypeId.CARRIER: 6, UnitTypeId.OBSERVER: 1,
    UnitTypeId.MARINE: 1, UnitTypeId.MARAUDER: 2, UnitTypeId.SIEGETANK: 3,
    UnitTypeId.SIEGETANKSIEGED: 3, UnitTypeId.MEDIVAC: 2, UnitTypeId.THOR: 6,
    UnitTypeId.BATTLECRUISER: 6,
    UnitTypeId.ZERGLING: 1, UnitTypeId.BANELING: 1, UnitTypeId.ROACH: 2,
    UnitTypeId.HYDRALISK: 2, UnitTypeId.MUTALISK: 2, UnitTypeId.ULTRALISK: 6,
    UnitTypeId.BROODLORD: 4, UnitTypeId.QUEEN: 2,
}

# Maps build-order / macro target strings → UnitTypeId
_TARGET_MAP: dict[str, UnitTypeId] = {
    "Pylon": UnitTypeId.PYLON,
    "Gateway": UnitTypeId.GATEWAY,
    "Assimilator": UnitTypeId.ASSIMILATOR,
    "Nexus": UnitTypeId.NEXUS,
    "CyberneticsCore": UnitTypeId.CYBERNETICSCORE,
    "RoboticsFacility": UnitTypeId.ROBOTICSFACILITY,
    "Forge": UnitTypeId.FORGE,
    "TwilightCouncil": UnitTypeId.TWILIGHTCOUNCIL,
    "Stargate": UnitTypeId.STARGATE,
    "RoboticsBay": UnitTypeId.ROBOTICSBAY,
    "Probe": UnitTypeId.PROBE,
    "Zealot": UnitTypeId.ZEALOT,
    "Stalker": UnitTypeId.STALKER,
    "Sentry": UnitTypeId.SENTRY,
    "Immortal": UnitTypeId.IMMORTAL,
    "Observer": UnitTypeId.OBSERVER,
    "Colossus": UnitTypeId.COLOSSUS,
}

# Army units to train from gateways (priority order)
_GATEWAY_ARMY: list[UnitTypeId] = [UnitTypeId.STALKER, UnitTypeId.ZEALOT]
# Army units to train from robos
_ROBO_ARMY: list[UnitTypeId] = [UnitTypeId.IMMORTAL, UnitTypeId.OBSERVER]


class Alpha4GateBot(BotAI):
    """Main bot class that orchestrates all decision layers."""

    def __init__(
        self,
        build_order: BuildOrder | None = None,
        logger: GameLogger | None = None,
        enable_console: bool = True,
        decision_mode: DecisionMode = DecisionMode.RULES,
        model_path: str | None = None,
    ) -> None:
        super().__init__()
        self.decision_engine = DecisionEngine(build_order=build_order)
        self.macro_manager = MacroManager()
        self.scout_manager = ScoutManager()
        self.micro_controller = MicroController()
        self._logger = logger
        self._enable_console = enable_console
        self._actions_this_step: list[dict[str, Any]] = []
        self._decision_mode = decision_mode
        self._neural_engine: NeuralDecisionEngine | None = None
        if decision_mode != DecisionMode.RULES and model_path is not None:
            self._neural_engine = NeuralDecisionEngine(
                model_path=model_path,
                mode=decision_mode,
            )

    def _build_snapshot(self) -> GameSnapshot:
        """Build a GameSnapshot from current bot state."""
        army_supply = 0
        worker_count = 0
        for unit in self.units:
            if unit.type_id == UnitTypeId.PROBE:
                worker_count += 1
            elif not unit.is_structure:
                army_supply += _SUPPLY_COST.get(unit.type_id, 2)

        enemy_near = False
        for enemy in self.enemy_units:
            if enemy.distance_to(self.start_location) < 40:
                enemy_near = True
                break

        enemy_supply = 0
        for u in self.enemy_units:
            enemy_supply += _SUPPLY_COST.get(u.type_id, 2)

        return GameSnapshot(
            supply_used=int(self.supply_used),
            supply_cap=int(self.supply_cap),
            minerals=self.minerals,
            vespene=self.vespene,
            army_supply=army_supply,
            worker_count=worker_count,
            base_count=len(self.townhalls),
            enemy_army_near_base=enemy_near,
            enemy_army_supply_visible=enemy_supply,
            game_time_seconds=self.time,
            gateway_count=len(self.structures(UnitTypeId.GATEWAY)),
            robo_count=len(self.structures(UnitTypeId.ROBOTICSFACILITY)),
            forge_count=len(self.structures(UnitTypeId.FORGE)),
            upgrade_count=sum(1 for u in self.state.upgrades),
            enemy_structure_count=len(self.enemy_structures),
        )

    async def on_step(self, iteration: int) -> None:
        """Called every game step by burnysc2."""
        self._actions_this_step = []
        state = self.decision_engine.state

        # Build game snapshot and run decision engine
        snapshot = self._build_snapshot()
        self.decision_engine.evaluate(snapshot, game_step=self.state.game_loop)
        state = self.decision_engine.state

        # --- Opening: follow build order + keep training probes ---
        if state == StrategicState.OPENING:
            await self._execute_build_order(snapshot)
            self._train_probes_opening()

        # --- Post-opening macro: economy, supply, production buildings ---
        if state != StrategicState.OPENING:
            decisions = self.macro_manager.evaluate(self, state)
            for decision in decisions:
                await self._execute_macro(decision)

        # --- Army production from idle gateways / robos ---
        if state != StrategicState.OPENING:
            await self._produce_army()

        # --- Scouting ---
        await self._run_scouting()

        # --- Micro: combat commands for army units ---
        if state in (StrategicState.ATTACK, StrategicState.DEFEND):
            await self._run_micro(state)
        elif state == StrategicState.EXPAND or state == StrategicState.LATE_GAME:
            # Rally idle army to a defensive position near natural
            await self._rally_idle_army()

        # Observe and log every 22 steps (~1 real second at normal speed)
        if iteration % 22 == 0:
            entry = observe(self, actions_taken=self._actions_this_step)
            entry["strategic_state"] = state.value

            if self._logger is not None:
                self._logger.put(entry)
            if self._enable_console:
                print_status(entry)

    # ------------------------------------------------------------------ #
    #  Build order execution (OPENING phase)
    # ------------------------------------------------------------------ #

    async def _execute_build_order(self, snapshot: GameSnapshot) -> None:
        """Execute the next build order step if supply threshold is met."""
        sequencer = self.decision_engine.sequencer
        while sequencer.should_execute(snapshot.supply_used):
            step = sequencer.current_step
            if step is None:
                break

            success = await self._do_build_action(step.action, step.target)
            if success:
                self._actions_this_step.append(
                    {"action": step.action.capitalize(), "target": step.target}
                )
                sequencer.advance()
            else:
                break  # Can't afford or prerequisites missing, try next step

    def _train_probes_opening(self) -> None:
        """Continuously train probes from idle nexus during the opening."""
        if self.supply_left < 1:
            return
        if not self.can_afford(UnitTypeId.PROBE):
            return
        for nexus in self.townhalls.idle:
            nexus.train(UnitTypeId.PROBE)
            return  # One at a time

    async def _do_build_action(self, action: str, target: str) -> bool:
        """Translate a build-order action string into a real SC2 command.

        Returns True if the command was issued successfully.
        """
        unit_id = _TARGET_MAP.get(target)
        if unit_id is None:
            _log.warning("Unknown build target: %s", target)
            return False

        if action == "build":
            return await self._build_structure(unit_id)
        elif action == "train":
            return self._train_unit(unit_id)
        return False

    async def _build_structure(self, unit_id: UnitTypeId) -> bool:
        """Build a structure near a pylon (or expand for Nexus, or on geyser for gas)."""
        if unit_id == UnitTypeId.NEXUS:
            if self.can_afford(UnitTypeId.NEXUS):
                await self.expand_now()
                return True
            return False

        if not self.can_afford(unit_id):
            return False

        # Assimilator: must be built on a vespene geyser
        if unit_id == UnitTypeId.ASSIMILATOR:
            for nexus in self.townhalls.ready:
                geysers = self.vespene_geyser.closer_than(10, nexus)
                for geyser in geysers:
                    if not self.gas_buildings.closer_than(1, geyser):
                        await self.build(UnitTypeId.ASSIMILATOR, geyser)
                        return True
            return False

        # Find a pylon to build near
        pylons = self.structures(UnitTypeId.PYLON).ready
        if not pylons:
            # First pylon — build toward map center from start location
            pos = self.start_location.towards(self.game_info.map_center, 5)
            await self.build(unit_id, near=pos)
            return True

        await self.build(unit_id, near=pylons.closest_to(self.start_location))
        return True

    def _train_unit(self, unit_id: UnitTypeId) -> bool:
        """Train a unit from the appropriate idle structure."""
        if not self.can_afford(unit_id):
            return False

        if unit_id == UnitTypeId.PROBE:
            for nexus in self.townhalls.idle:
                nexus.train(UnitTypeId.PROBE)
                return True
        elif unit_id in (UnitTypeId.ZEALOT, UnitTypeId.STALKER, UnitTypeId.SENTRY):
            for gw in self.structures(UnitTypeId.GATEWAY).idle:
                gw.train(unit_id)
                return True
        elif unit_id in (UnitTypeId.IMMORTAL, UnitTypeId.OBSERVER, UnitTypeId.COLOSSUS):
            for robo in self.structures(UnitTypeId.ROBOTICSFACILITY).idle:
                robo.train(unit_id)
                return True
        return False

    # ------------------------------------------------------------------ #
    #  Macro execution (post-opening)
    # ------------------------------------------------------------------ #

    async def _execute_macro(self, decision: MacroDecision) -> None:
        """Execute a single MacroManager decision."""
        if decision.action == "expand":
            if self.can_afford(UnitTypeId.NEXUS):
                await self.expand_now()
                self._actions_this_step.append(decision.to_dict())
        elif decision.action == "build":
            unit_id = _TARGET_MAP.get(decision.target)
            if unit_id and await self._build_structure(unit_id):
                self._actions_this_step.append(decision.to_dict())
        elif decision.action == "train":
            unit_id = _TARGET_MAP.get(decision.target)
            if unit_id and self._train_unit(unit_id):
                self._actions_this_step.append(decision.to_dict())

    # ------------------------------------------------------------------ #
    #  Army production
    # ------------------------------------------------------------------ #

    async def _produce_army(self) -> None:
        """Train army units from idle gateways and robos."""
        # Gateways → stalkers (prefer) or zealots
        for gw in self.structures(UnitTypeId.GATEWAY).idle:
            for unit_id in _GATEWAY_ARMY:
                if self.can_afford(unit_id) and self.supply_left >= 2:
                    gw.train(unit_id)
                    self._actions_this_step.append(
                        {"action": "Train", "target": unit_id.name}
                    )
                    break

        # Robos → immortals (prefer) or observers
        for robo in self.structures(UnitTypeId.ROBOTICSFACILITY).idle:
            for unit_id in _ROBO_ARMY:
                if self.can_afford(unit_id) and self.supply_left >= 2:
                    robo.train(unit_id)
                    self._actions_this_step.append(
                        {"action": "Train", "target": unit_id.name}
                    )
                    break

    # ------------------------------------------------------------------ #
    #  Scouting
    # ------------------------------------------------------------------ #

    async def _run_scouting(self) -> None:
        """Send a probe to scout enemy base locations."""
        # Clear dead scout
        if self.scout_manager.scout_tag is not None:
            scout_alive = any(
                u.tag == self.scout_manager.scout_tag for u in self.units
            )
            if not scout_alive:
                self.scout_manager.clear_scout()

        # Update enemy base locations from visible enemy townhalls
        enemy_bases = [s.position for s in self.enemy_structures if s.is_structure]
        if enemy_bases:
            self.scout_manager.update_enemy_bases(enemy_bases)

        if not self.scout_manager.should_scout(self.time):
            return

        # Pick a probe that's gathering minerals (not the only one)
        probes = self.units(UnitTypeId.PROBE)
        if len(probes) < 2:
            return

        scout = probes.furthest_to(self.start_location)
        target = self.enemy_start_locations[0] if self.enemy_start_locations else None
        if target is None:
            return

        scout.move(target)
        self.scout_manager.assign_scout(scout.tag, self.time)
        self._actions_this_step.append(
            {"action": "Scout", "target": f"probe→{target}"}
        )

    # ------------------------------------------------------------------ #
    #  Micro (combat)
    # ------------------------------------------------------------------ #

    async def _run_micro(self, state: StrategicState) -> None:
        """Issue combat micro commands to army units."""
        army = [u for u in self.units if not u.is_structure and u.type_id != UnitTypeId.PROBE]
        enemies = list(self.enemy_units)

        # Determine rally / attack target
        if state == StrategicState.ATTACK:
            rally = self._attack_target()
        else:
            rally = self._defense_rally()

        commands = self.micro_controller.generate_commands(
            own_units=army,
            enemy_units=enemies,
            rally_point=rally,
        )

        for cmd in commands:
            unit = self.units.find_by_tag(cmd.unit_tag)
            if unit is None:
                continue

            if cmd.action == "attack" and cmd.target_tag is not None:
                target_unit = self.enemy_units.find_by_tag(cmd.target_tag)
                if target_unit:
                    unit.attack(target_unit)
                elif cmd.target_position:
                    unit.attack(Point2(cmd.target_position))
            elif cmd.action == "move" and cmd.target_position is not None:
                unit.move(Point2(cmd.target_position))

    async def _rally_idle_army(self) -> None:
        """Move idle army units to a rally point near the natural."""
        rally = self._defense_rally()
        if rally is None:
            return
        rally_pt = Point2(rally)
        for unit in self.units:
            if unit.is_structure or unit.type_id == UnitTypeId.PROBE:
                continue
            if unit.is_idle and unit.distance_to(rally_pt) > 10:
                unit.attack(rally_pt)

    def _attack_target(self) -> tuple[float, float] | None:
        """Pick an attack target: known enemy base or enemy start location."""
        if self.scout_manager.enemy_base_locations:
            pos = self.scout_manager.enemy_base_locations[0]
            return (float(pos.x), float(pos.y))
        if self.enemy_start_locations:
            pos = self.enemy_start_locations[0]
            return (float(pos.x), float(pos.y))
        return None

    def _defense_rally(self) -> tuple[float, float] | None:
        """Rally point for defending: between main and natural."""
        if not self.townhalls:
            return None
        main = self.start_location
        natural = self.main_base_ramp.bottom_center
        mid = main.towards(natural, main.distance_to(natural) * 0.6)
        return (float(mid.x), float(mid.y))
