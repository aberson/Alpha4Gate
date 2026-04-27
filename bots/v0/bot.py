"""Main BotAI subclass: on_step() orchestrates all decision layers."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import numpy as np
from sc2.bot_ai import BotAI
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

from bots.v0.army_coherence import ArmyCoherenceManager
from bots.v0.build_backlog import BuildBacklog
from bots.v0.build_orders import BuildOrder
from bots.v0.claude_advisor import ClaudeAdvisor, build_prompt
from bots.v0.commands import (
    CommandExecutor,
    CommandMode,
    CommandSource,
    filter_executable,
    get_command_queue,
    get_command_settings,
)
from bots.v0.commands.dispatch_guard import DispatchGuard
from bots.v0.console import print_status
from bots.v0.decision_engine import DecisionEngine, GameSnapshot, StrategicState
from bots.v0.fortification import FortificationManager
from bots.v0.learning import winprob_heuristic
from bots.v0.learning.features import _FEATURE_SPEC
from bots.v0.learning.neural_engine import DecisionMode, NeuralDecisionEngine
from bots.v0.learning.rewards import RewardCalculator
from bots.v0.learning.threat_classes import THREAT_CLASS_MAP
from bots.v0.logger import GameLogger
from bots.v0.macro_manager import MacroDecision, MacroManager
from bots.v0.micro import MicroController
from bots.v0.observer import observe
from bots.v0.scouting import ScoutManager
from bots.v0.web_socket import queue_broadcast, queue_command_event

if TYPE_CHECKING:
    from bots.v0.learning.database import TrainingDB

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

# Maps own Protoss combat unit types to GameSnapshot field names
_OWN_UNIT_MAP: dict[UnitTypeId, str] = {
    UnitTypeId.ZEALOT: "zealot_count",
    UnitTypeId.STALKER: "stalker_count",
    UnitTypeId.SENTRY: "sentry_count",
    UnitTypeId.IMMORTAL: "immortal_count",
    UnitTypeId.COLOSSUS: "colossus_count",
    UnitTypeId.ARCHON: "archon_count",
    UnitTypeId.HIGHTEMPLAR: "high_templar_count",
    UnitTypeId.DARKTEMPLAR: "dark_templar_count",
    UnitTypeId.PHOENIX: "phoenix_count",
    UnitTypeId.VOIDRAY: "void_ray_count",
    UnitTypeId.CARRIER: "carrier_count",
    UnitTypeId.TEMPEST: "tempest_count",
    UnitTypeId.DISRUPTOR: "disruptor_count",
    UnitTypeId.WARPPRISM: "warp_prism_count",
    UnitTypeId.WARPPRISMPHASING: "warp_prism_count",
    UnitTypeId.OBSERVER: "observer_count",
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
    "TemplarArchive": UnitTypeId.TEMPLARARCHIVE,
    "Probe": UnitTypeId.PROBE,
    "Zealot": UnitTypeId.ZEALOT,
    "Stalker": UnitTypeId.STALKER,
    "Sentry": UnitTypeId.SENTRY,
    "Immortal": UnitTypeId.IMMORTAL,
    "Observer": UnitTypeId.OBSERVER,
    "Colossus": UnitTypeId.COLOSSUS,
    "PhotonCannon": UnitTypeId.PHOTONCANNON,
    "ShieldBattery": UnitTypeId.SHIELDBATTERY,
    "FleetBeacon": UnitTypeId.FLEETBEACON,
    "VoidRay": UnitTypeId.VOIDRAY,
    "Carrier": UnitTypeId.CARRIER,
    "Tempest": UnitTypeId.TEMPEST,
    "Phoenix": UnitTypeId.PHOENIX,
}

# Army units to train from gateways (priority order)
_GATEWAY_ARMY: list[UnitTypeId] = [UnitTypeId.STALKER, UnitTypeId.ZEALOT]
# WarpGate abilities for each unit type (priority order)
_WARPGATE_ABILITIES: list[tuple[UnitTypeId, AbilityId]] = [
    (UnitTypeId.STALKER, AbilityId.WARPGATETRAIN_STALKER),
    (UnitTypeId.ZEALOT, AbilityId.WARPGATETRAIN_ZEALOT),
]
# HighTemplar warp ability — gated on TemplarArchives + HT-count target
# (Separate list so we only warp HTs for the Archon morph pipeline.)
_HIGH_TEMPLAR_WARP: tuple[UnitTypeId, AbilityId] = (
    UnitTypeId.HIGHTEMPLAR,
    AbilityId.WARPGATETRAIN_HIGHTEMPLAR,
)
# Pair up to N HighTemplars at a time into Archons
ARCHON_TARGET: int = 4
HIGHTEMPLAR_WARP_CAP: int = ARCHON_TARGET * 2
# Army units to train from robos
_ROBO_ARMY: list[UnitTypeId] = [UnitTypeId.IMMORTAL, UnitTypeId.OBSERVER]


def _should_reissue_attack_to_unit(unit: Any, target_tag: int) -> bool:
    """Return True if unit should be (re-)commanded to attack ``target_tag``.

    Skips re-issue when the unit is already attacking the same target tag,
    to avoid churning the weapon cycle every tick. Each fresh ``unit.attack``
    resets the unit's weapon-ready timer, so spamming it per-tick prevents
    units from ever firing in large melees.
    """
    if unit.is_idle:
        return True
    if unit.is_attacking:
        current = unit.order_target
        # order_target is either an int (unit tag) or Point2 (position) or None.
        if isinstance(current, int) and current == target_tag:
            return False
    return True


def _should_reissue_attack_to_position(
    unit: Any, target_pos: Point2, tolerance: float = 2.0,
) -> bool:
    """Return True if unit should be (re-)commanded to attack-move to ``target_pos``.

    Skips re-issue when the unit is already attack-moving toward the same
    spot (within ``tolerance`` tiles), to avoid churning the weapon cycle or
    recomputing the path every tick.
    """
    if unit.is_idle:
        return True
    if unit.is_attacking:
        current = unit.order_target
        # A position target is a Point2 — check distance. ``None`` and int tags
        # both fall through to returning True (re-issue).
        if current is not None and hasattr(current, "distance_to"):
            try:
                if current.distance_to(target_pos) <= tolerance:
                    return False
            except (TypeError, AttributeError):
                # Defensive: unexpected target type — fall through and re-issue.
                return True
    return True


def _maybe_log_winprob(
    iteration: int,
    snapshot: GameSnapshot,
    state_name: str,
    logger: logging.Logger,
) -> None:
    """Emit a heuristic-winprob log line every 10th iteration.

    Used by ``Alpha4GateBot.on_step`` for the operator-facing debug
    surface from Phase N (see investigation §6).  Pulled out as a
    module-level pure helper so it can be unit-tested directly without
    standing up a burnysc2 BotAI.
    """
    if iteration % 10 != 0:
        return
    prob = winprob_heuristic.score(snapshot)
    logger.info("winprob=%.2f state=%s", prob, state_name)


class Alpha4GateBot(BotAI):
    """Main bot class that orchestrates all decision layers."""

    # Staging point recalculation interval in game seconds
    _STAGING_RECALC_SECONDS: float = 30.0

    # At this supply or above, attack the enemy main instead of the natural
    PUSH_MAIN_SUPPLY: int = 60
    FINISHER_SUPPLY: int = 100

    # Bleeding-commit thresholds (see _is_bleeding_stationary for rationale)
    BLEEDING_MOVE_THRESHOLD: float = 2.0  # tiles — centroid moving < this per tick = stationary
    BLEEDING_HP_PER_TICK_THRESHOLD: int = 1  # HP lost per tick to count as "taking damage"
    BLEEDING_COMMIT_SECONDS: float = 3.0  # bleed this long before force-commit

    # Maps StrategicState → action index (inverse of _ACTION_TO_STATE in environment.py)
    _STATE_TO_ACTION: dict[StrategicState, int] = {
        StrategicState.OPENING: 0,
        StrategicState.EXPAND: 1,
        StrategicState.ATTACK: 2,
        StrategicState.DEFEND: 3,
        StrategicState.LATE_GAME: 4,
        StrategicState.FORTIFY: 5,
    }

    def __init__(
        self,
        build_order: BuildOrder | None = None,
        logger: GameLogger | None = None,
        enable_console: bool = True,
        decision_mode: DecisionMode = DecisionMode.RULES,
        model_path: str | None = None,
        training_db: TrainingDB | None = None,
        game_id: str | None = None,
        reward_calculator: RewardCalculator | None = None,
        claude_advisor: ClaudeAdvisor | None = None,
    ) -> None:
        super().__init__()
        # Army coherence manager (randomized params per game) — created first
        # so its rolled params can be passed to the decision engine.
        self.coherence_manager = ArmyCoherenceManager()
        self.decision_engine = DecisionEngine(
            build_order=build_order,
            fortify_trigger_ratio=self.coherence_manager.fortify_trigger_ratio,
            attack_supply_ratio=self.coherence_manager.attack_supply_ratio,
        )
        self.macro_manager = MacroManager()
        self.scout_manager = ScoutManager()
        self.micro_controller = MicroController()
        self._logger = logger
        self._enable_console = enable_console
        self._actions_this_step: list[dict[str, Any]] = []
        self._dispatch_guard = DispatchGuard()
        self._decision_mode = decision_mode
        self._neural_engine: NeuralDecisionEngine | None = None
        if decision_mode != DecisionMode.RULES and model_path is not None:
            self._neural_engine = NeuralDecisionEngine(
                model_path=model_path,
                mode=decision_mode,
            )
        self._coherence_params_logged: bool = False
        self._cached_staging_point: tuple[float, float] | None = None
        self._staging_point_time: float = -999.0  # last recalc time
        self._cached_enemy_natural: tuple[float, float] | None = None

        # Fortification manager (randomized params from coherence manager)
        self._fortification_manager = FortificationManager(
            defense_scaling_divisor=self.coherence_manager.defense_scaling_divisor,
            max_defenses=self.coherence_manager.max_defenses,
        )
        self._build_backlog = BuildBacklog()

        # Command system executor
        self._command_executor = CommandExecutor(self)

        # Claude advisor integration
        self._claude_advisor = claude_advisor
        self._ai_lockout_until: float = 0.0

        # Transition recording for training
        self._training_db = training_db
        self._game_id = game_id
        self._reward_calc = reward_calculator or RewardCalculator()
        self._transition_step: int = 0
        self._prev_snapshot: GameSnapshot | None = None
        self._prev_obs: np.ndarray | None = None
        self._prev_action: int | None = None

        # Bleeding-commit state (see _is_bleeding_stationary)
        self._last_army_centroid: tuple[float, float] | None = None
        self._last_army_hp: int = 0
        self._bleeding_since: float | None = None  # game time bleeding started

    def _build_snapshot(self) -> GameSnapshot:
        """Build a GameSnapshot from current bot state."""
        army_supply = 0
        worker_count = 0
        own_counts: dict[str, int] = {}
        for unit in self.units:
            if unit.type_id == UnitTypeId.PROBE:
                worker_count += 1
            elif not unit.is_structure:
                army_supply += _SUPPLY_COST.get(unit.type_id, 2)
            field = _OWN_UNIT_MAP.get(unit.type_id)
            if field is not None:
                own_counts[field] = own_counts.get(field, 0) + 1

        enemy_near = False
        for enemy in self.enemy_units:
            if enemy.distance_to(self.start_location) < 40:
                enemy_near = True
                break

        enemy_supply = 0
        enemy_counts: dict[str, int] = {}
        for u in self.enemy_units:
            enemy_supply += _SUPPLY_COST.get(u.type_id, 2)
            bucket = THREAT_CLASS_MAP.get(u.type_id.value)
            if bucket is not None:
                enemy_counts[bucket] = enemy_counts.get(bucket, 0) + 1

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
            cannon_count=len(self.structures(UnitTypeId.PHOTONCANNON)),
            battery_count=len(self.structures(UnitTypeId.SHIELDBATTERY)),
            **own_counts,
            **enemy_counts,
        )

    async def on_step(self, iteration: int) -> None:
        """Called every game step by burnysc2."""
        self._actions_this_step = []
        state = self.decision_engine.state

        # Build game snapshot and run decision engine
        snapshot = self._build_snapshot()
        self.decision_engine.evaluate(snapshot, game_step=self.state.game_loop)
        state = self.decision_engine.state

        # Neural override: let the trained model choose the strategic state
        if self._neural_engine is not None:
            state = self._neural_engine.predict(snapshot)

        _maybe_log_winprob(iteration, snapshot, state.value, _log)

        # --- Command system: drain queue and execute ---
        settings = get_command_settings()
        if not settings.muted:
            queue = get_command_queue()
            commands = filter_executable(
                queue.drain(snapshot.game_time_seconds), settings.mode
            )
            for cmd in commands:
                # Trigger lockout when human command arrives in hybrid mode
                if (
                    cmd.source == CommandSource.HUMAN
                    and settings.mode == CommandMode.HYBRID_CMD
                ):
                    self.set_ai_lockout(snapshot.game_time_seconds)
                result = await self._command_executor.execute(cmd)
                if result.success:
                    _log.info("Cmd OK: %s %s → %s", cmd.action.value, cmd.target, result.message)
                    queue_command_event({
                        "type": "executed",
                        "id": cmd.id,
                        "reason": result.message,
                    })
                else:
                    _log.warning(
                        "Cmd FAIL: %s %s → %s", cmd.action.value, cmd.target, result.message
                    )
                    queue_command_event({
                        "type": "failed",
                        "id": cmd.id,
                        "reason": result.message,
                    })

        # --- Claude advisor → command queue ---
        if (
            self._claude_advisor is not None
            and self._claude_advisor.enabled
            and not settings.muted
            and settings.mode != CommandMode.HUMAN_ONLY
        ):
            if not self._is_ai_locked_out(snapshot.game_time_seconds):
                # Check for completed advice
                response = self._claude_advisor.collect_response()
                if response and response.commands:
                    queue = get_command_queue()
                    for cmd in response.commands:
                        queue.push(cmd)

                # Fire new advice request if rate limit allows
                sequencer = self.decision_engine.sequencer
                prompt = build_prompt(
                    game_time=f"{int(snapshot.game_time_seconds // 60)}:"
                    f"{int(snapshot.game_time_seconds % 60):02d}",
                    strategic_state=state.value,
                    minerals=snapshot.minerals,
                    vespene=snapshot.vespene,
                    supply_used=snapshot.supply_used,
                    supply_cap=snapshot.supply_cap,
                    army_composition=f"{snapshot.army_supply} supply",
                    enemy_composition=(
                        f"{snapshot.enemy_army_supply_visible} supply visible"
                    ),
                    recent_decisions=str(state.value),
                    build_order_name=sequencer.order.id,
                    build_step=sequencer.current_index,
                    total_steps=len(sequencer.order.steps),
                )
                self._claude_advisor.request_advice(
                    prompt, snapshot.game_time_seconds
                )

        # --- Record transition for training DB (every 22 steps) ---
        if self._training_db is not None and iteration % 22 == 0:
            self._record_transition(snapshot, state)

        # --- Worker distribution: transfer probes to unsaturated bases/gas ---
        await self.distribute_workers()

        # --- Opening: follow build order + keep training probes ---
        if state == StrategicState.OPENING:
            await self._execute_build_order(snapshot)
            self._train_probes_opening()

        # --- Post-opening macro: economy, supply, production buildings ---
        if state != StrategicState.OPENING:
            decisions = self.macro_manager.evaluate(self, state)
            for decision in decisions:
                await self._execute_macro(decision)

        # --- FORTIFY: defensive structure production ---
        if state == StrategicState.FORTIFY:
            fort_decisions = self._evaluate_fortification(snapshot)
            for fd in fort_decisions:
                success = await self._execute_macro(fd)
                if not success:
                    self._build_backlog.add(
                        fd.target,
                        (float(self.start_location.x), float(self.start_location.y)),
                        fd.reason,
                        snapshot.game_time_seconds,
                    )

        # --- Drain build backlog (all non-OPENING states) ---
        if state != StrategicState.OPENING:
            await self._drain_backlog(snapshot)

        # --- Army production from idle gateways / robos ---
        if state != StrategicState.OPENING:
            await self._produce_army()

        # --- Scouting ---
        await self._run_scouting()

        # --- Micro: combat commands for army units ---
        if state in (
            StrategicState.ATTACK, StrategicState.DEFEND,
            StrategicState.FORTIFY, StrategicState.LATE_GAME,
        ):
            await self._run_micro(state)
        elif state == StrategicState.EXPAND:
            # Rally idle army to a defensive position near natural
            await self._rally_idle_army()

        # Observe and log every 11 steps (~0.5 real seconds at normal speed)
        if iteration % 11 == 0:
            entry = observe(self, actions_taken=self._actions_this_step)
            entry["strategic_state"] = state.value

            # Log coherence params on the first entry each game
            if not self._coherence_params_logged:
                entry["coherence_params"] = self.coherence_manager.get_params_dict()
                self._coherence_params_logged = True

            if self._logger is not None:
                self._logger.put(entry)
            if self._enable_console:
                print_status(entry)
            queue_broadcast(entry)

    # ------------------------------------------------------------------ #
    #  Transition recording for training
    # ------------------------------------------------------------------ #

    def _record_transition(self, snapshot: GameSnapshot, state: StrategicState) -> None:
        """Record a (s, a, r, s') transition into the training DB."""
        assert self._training_db is not None
        # Raw (un-normalized) feature vector for DB storage
        raw = np.array(
            [getattr(snapshot, field) for field, _ in _FEATURE_SPEC],
            dtype=np.float32,
        )
        action = self._STATE_TO_ACTION[state]
        state_dict = asdict(snapshot)
        reward = self._reward_calc.compute_step_reward(state_dict)

        # Capture action probabilities from neural engine when available
        action_probs: list[float] | None = None
        if self._neural_engine is not None and hasattr(self._neural_engine, "last_probabilities"):
            probs = self._neural_engine.last_probabilities
            if probs:
                action_probs = probs

        if self._prev_obs is not None and self._prev_action is not None:
            self._training_db.store_transition(
                game_id=self._game_id or "unknown",
                step_index=self._transition_step,
                game_time=snapshot.game_time_seconds,
                state=self._prev_obs,
                action=self._prev_action,
                reward=reward,
                next_state=raw,
                done=False,
                action_probs=action_probs,
            )
            self._transition_step += 1

        self._prev_obs = raw
        self._prev_action = action
        self._prev_snapshot = snapshot

    def record_final_transition(self, result: str) -> None:
        """Record the terminal transition at game end. Call from runner."""
        if self._training_db is None or self._prev_obs is None:
            return
        state_dict = asdict(self._prev_snapshot) if self._prev_snapshot else {}
        reward = self._reward_calc.compute_step_reward(state_dict, is_terminal=True, result=result)
        self._training_db.store_transition(
            game_id=self._game_id or "unknown",
            step_index=self._transition_step,
            game_time=self._prev_snapshot.game_time_seconds if self._prev_snapshot else 0.0,
            state=self._prev_obs,
            action=self._prev_action or 0,
            reward=reward,
            next_state=None,
            done=True,
        )

    # ------------------------------------------------------------------ #
    #  AI lockout (hybrid mode)
    # ------------------------------------------------------------------ #

    def set_ai_lockout(self, game_time: float) -> None:
        """Set AI lockout after a human command in hybrid mode.

        Args:
            game_time: Current game time in seconds.
        """
        settings = get_command_settings()
        self._ai_lockout_until = game_time + settings.lockout_duration

    def _is_ai_locked_out(self, game_time: float) -> bool:
        """Check if AI commands are currently locked out.

        Args:
            game_time: Current game time in seconds.

        Returns:
            True if AI commands should be suppressed.
        """
        return game_time < self._ai_lockout_until

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

    async def _execute_macro(self, decision: MacroDecision) -> bool:
        """Execute a single MacroManager decision. Returns True if issued."""
        if decision.action == "expand":
            if self.can_afford(UnitTypeId.NEXUS):
                await self.expand_now()
                self._actions_this_step.append(decision.to_dict())
                return True
        elif decision.action == "build":
            unit_id = _TARGET_MAP.get(decision.target)
            if unit_id and await self._build_structure(unit_id):
                self._actions_this_step.append(decision.to_dict())
                return True
        elif decision.action == "train":
            unit_id = _TARGET_MAP.get(decision.target)
            if unit_id and self._train_unit(unit_id):
                self._actions_this_step.append(decision.to_dict())
                return True
        elif decision.action == "research":
            if decision.target == "WarpGateResearch":
                cores = self.structures(UnitTypeId.CYBERNETICSCORE).ready.idle
                if cores and self.can_afford(UpgradeId.WARPGATERESEARCH):
                    cores.first.research(UpgradeId.WARPGATERESEARCH)
                    self._actions_this_step.append(decision.to_dict())
                    return True
            else:
                try:
                    upgrade_id = UpgradeId[decision.target]
                except KeyError:
                    return False
                # Twilight Council upgrades (Charge, Blink)
                _TC_UPGRADES = {UpgradeId.CHARGE, UpgradeId.BLINKTECH}
                if upgrade_id in _TC_UPGRADES:
                    tcs = self.structures(UnitTypeId.TWILIGHTCOUNCIL).ready.idle
                    if tcs and self.can_afford(upgrade_id):
                        tcs.first.research(upgrade_id)
                        self._actions_this_step.append(decision.to_dict())
                        return True
                else:
                    # Ground upgrades from Forge
                    forges = self.structures(UnitTypeId.FORGE).ready.idle
                    if forges and self.can_afford(upgrade_id):
                        forges.first.research(upgrade_id)
                        self._actions_this_step.append(decision.to_dict())
                        return True
        return False

    # ------------------------------------------------------------------ #
    #  Fortification helpers
    # ------------------------------------------------------------------ #

    def _evaluate_fortification(self, snapshot: GameSnapshot) -> list[MacroDecision]:
        """Run the fortification manager and return defensive build decisions."""
        has_forge = len(self.structures(UnitTypeId.FORGE).ready) > 0
        forge_building = len(self.structures(UnitTypeId.FORGE).not_ready) > 0
        has_cyber = len(self.structures(UnitTypeId.CYBERNETICSCORE).ready) > 0

        # Check for pylon near natural. Count in-progress pylons too —
        # otherwise fortification queues a new "build pylon" decision on
        # every tick of the ~25 game-second window between placement and
        # completion, producing a pile of duplicate pylon orders near the
        # natural. (Observed 2026-04-15 mid-game screenshot.)
        natural_pos = self.main_base_ramp.bottom_center
        has_pylon_near_natural = any(
            p.distance_to(natural_pos) < 12
            for p in self.structures(UnitTypeId.PYLON)
        )
        # Tick-race guard: burnysc2 has a one-tick window between
        # bot.build(PYLON) dispatch and the pylon appearing in structures().
        # If any pylon is pending anywhere on the map, treat the natural
        # check as satisfied for this tick — worst case we delay one real
        # tick before re-emitting, which is fine.
        if not has_pylon_near_natural and self.already_pending(UnitTypeId.PYLON) > 0:
            has_pylon_near_natural = True

        existing_cannons = len(self.structures(UnitTypeId.PHOTONCANNON))
        existing_batteries = len(self.structures(UnitTypeId.SHIELDBATTERY))

        return self._fortification_manager.evaluate(
            enemy_supply=float(snapshot.enemy_army_supply_visible),
            own_supply=float(snapshot.army_supply),
            existing_cannons=existing_cannons,
            existing_batteries=existing_batteries,
            has_forge=has_forge,
            forge_building=forge_building,
            has_cybernetics_core=has_cyber,
            has_pylon_near_natural=has_pylon_near_natural,
        )

    async def _drain_backlog(self, snapshot: GameSnapshot) -> None:
        """Try to retry one failed build from the backlog."""

        def _can_afford(structure_type: str, location: tuple[float, float]) -> bool:
            unit_id = _TARGET_MAP.get(structure_type)
            if unit_id is None:
                return False
            return bool(self.can_afford(unit_id))

        entry = self._build_backlog.tick(
            game_time=snapshot.game_time_seconds,
            can_afford=_can_afford,
        )
        if entry is not None:
            unit_id = _TARGET_MAP.get(entry.structure_type)
            if unit_id is not None:
                if not self._dispatch_guard.should_dispatch(
                    "build", entry.structure_type, snapshot.game_time_seconds,
                ):
                    return
                built = await self._build_structure(unit_id)
                if built:
                    self._dispatch_guard.mark_dispatched(
                        "build", entry.structure_type, snapshot.game_time_seconds,
                    )
                    self._actions_this_step.append({
                        "action": "build",
                        "target": entry.structure_type,
                        "reason": "backlog_retry",
                    })

    # ------------------------------------------------------------------ #
    #  Army production
    # ------------------------------------------------------------------ #

    async def _produce_army(self) -> None:
        """Train army units from idle gateways/warpgates and robos."""
        # WarpGates → warp in at nearest pylon
        # When gas is high (>200), skip Zealots — save minerals for Stalkers
        gas_high = int(self.vespene) > 200
        # HighTemplar priority: when Templar Archives unlocks and HT+Archon
        # count below target, warp HTs first so the Archon morph pipeline
        # can feed the army with splash bulk.
        has_templar_archives = bool(
            self.structures(UnitTypeId.TEMPLARARCHIVE).ready,
        )
        ht_count = len(self.units(UnitTypeId.HIGHTEMPLAR))
        archon_count = len(self.units(UnitTypeId.ARCHON))
        want_high_templar = (
            has_templar_archives
            and archon_count < ARCHON_TARGET
            and ht_count < HIGHTEMPLAR_WARP_CAP - archon_count * 2
        )
        warp_priority: list[tuple[UnitTypeId, AbilityId]] = (
            [_HIGH_TEMPLAR_WARP, *_WARPGATE_ABILITIES]
            if want_high_templar
            else list(_WARPGATE_ABILITIES)
        )
        for wg in self.structures(UnitTypeId.WARPGATE).ready:
            abilities_list = await self.get_available_abilities([wg])
            abilities = abilities_list[0] if abilities_list else []
            for unit_id, ability in warp_priority:
                if gas_high and unit_id == UnitTypeId.ZEALOT:
                    continue
                if ability not in abilities:
                    continue
                if not self.can_afford(unit_id) or self.supply_left < 2:
                    continue
                pylons = self.structures(UnitTypeId.PYLON).ready
                if not pylons:
                    break
                # Prefer forward pylons (furthest from main) — they have more
                # open space than the crowded main base, and warping forward
                # avoids trapping units behind Nexus/Gateway/Assimilator.
                # Try each pylon in descending distance from start, fall back
                # to main base only if no forward placement works.
                sorted_pylons = sorted(
                    pylons,
                    key=lambda p: -p.position.distance_to(self.start_location),
                )
                pos = None
                for pylon in sorted_pylons:
                    candidate = await self.find_placement(
                        ability, pylon.position, placement_step=2,
                    )
                    if candidate is not None:
                        pos = candidate
                        break
                if pos is not None:
                    if not self._dispatch_guard.should_dispatch(
                        "WarpIn", unit_id.name, self.time,
                    ):
                        break
                    wg.warp_in(unit_id, pos)
                    self._dispatch_guard.mark_dispatched(
                        "WarpIn", unit_id.name, self.time,
                    )
                    self._actions_this_step.append(
                        {"action": "WarpIn", "target": unit_id.name},
                    )
                break

        # Regular Gateways → stalkers (prefer) or zealots
        for gw in self.structures(UnitTypeId.GATEWAY).idle:
            for unit_id in _GATEWAY_ARMY:
                if gas_high and unit_id == UnitTypeId.ZEALOT:
                    continue  # save minerals for Stalkers
                if self.can_afford(unit_id) and self.supply_left >= 2:
                    gw.train(unit_id)
                    self._actions_this_step.append(
                        {"action": "Train", "target": unit_id.name}
                    )
                    break

        # Robos → colossus (if RoboBay) > immortals > observers (capped at 2)
        obs_count = len(self.units(UnitTypeId.OBSERVER))
        has_robo_bay = bool(self.structures(UnitTypeId.ROBOTICSBAY).ready)
        colossus_count = len(self.units(UnitTypeId.COLOSSUS))
        COLOSSUS_TARGET = 6
        for robo in self.structures(UnitTypeId.ROBOTICSFACILITY).idle:
            if has_robo_bay and colossus_count < COLOSSUS_TARGET:
                if self.can_afford(UnitTypeId.COLOSSUS) and self.supply_left >= 6:
                    robo.train(UnitTypeId.COLOSSUS)
                    self._actions_this_step.append(
                        {"action": "Train", "target": "Colossus"}
                    )
                    colossus_count += 1
                    continue
            for unit_id in _ROBO_ARMY:
                if unit_id == UnitTypeId.OBSERVER and obs_count >= 2:
                    continue
                if self.can_afford(unit_id) and self.supply_left >= 2:
                    robo.train(unit_id)
                    self._actions_this_step.append(
                        {"action": "Train", "target": unit_id.name}
                    )
                    break

        # Stargates → void rays, then carriers (if Fleet Beacon)
        has_fleet_beacon = bool(self.structures(UnitTypeId.FLEETBEACON).ready)
        voidray_count = len(self.units(UnitTypeId.VOIDRAY))
        carrier_count = len(self.units(UnitTypeId.CARRIER))
        for sg in self.structures(UnitTypeId.STARGATE).idle:
            # Carriers if Fleet Beacon exists (cap at 3)
            if has_fleet_beacon and carrier_count < 3:
                if self.can_afford(UnitTypeId.CARRIER) and self.supply_left >= 6:
                    sg.train(UnitTypeId.CARRIER)
                    self._actions_this_step.append(
                        {"action": "Train", "target": "Carrier"}
                    )
                    carrier_count += 1
                    continue
            # Void Rays (cap at 4)
            if voidray_count < 4:
                if self.can_afford(UnitTypeId.VOIDRAY) and self.supply_left >= 4:
                    sg.train(UnitTypeId.VOIDRAY)
                    self._actions_this_step.append(
                        {"action": "Train", "target": "VoidRay"}
                    )
                    voidray_count += 1

        # Archon morph: any pair of HighTemplars → 1 Archon.
        # MORPH_ARCHON is a no-target combineable ability in burnysc2 — issue
        # it to both templars separately; the protocol layer combines the two
        # unit tags into a single morph command that the engine resolves into
        # one Archon. Passing b as a target triggers a RuntimeWarning and the
        # command is silently dropped.
        high_templars = self.units(UnitTypeId.HIGHTEMPLAR)
        if high_templars.amount >= 2:
            pairs = list(high_templars)
            for i in range(0, len(pairs) - 1, 2):
                a, b = pairs[i], pairs[i + 1]
                a(AbilityId.MORPH_ARCHON)
                b(AbilityId.MORPH_ARCHON)
                self._actions_this_step.append(
                    {"action": "Morph", "target": "Archon"},
                )

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

        # Check for forced scout target (from command system)
        forced = self.scout_manager.consume_forced_target()
        if forced is not None:
            probes = self.units(UnitTypeId.PROBE)
            if len(probes) >= 2:
                scout = probes.furthest_to(self.start_location)
                scout.move(Point2(forced))
                self.scout_manager.assign_scout(scout.tag, self.time)
                self._actions_this_step.append(
                    {"action": "Scout", "target": f"probe→{forced} (forced)"}
                )
                return

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

    def _is_bleeding_stationary(self, army: list[Any], snapshot: GameSnapshot) -> bool:
        """Return True if army is stationary AND losing HP over BLEEDING_COMMIT_SECONDS.

        Stationary + taking damage means the army is stuck in a bad position
        (classically: clustered below an enemy ramp taking free ranged fire).
        The fix is to commit forward — either the push succeeds or dies trying,
        either way faster than the current slow bleed.
        """
        if not army:
            # No army — reset state, not bleeding
            self._last_army_centroid = None
            self._last_army_hp = 0
            self._bleeding_since = None
            return False

        # Compute current centroid and HP+shield
        cx = sum(u.position.x for u in army) / len(army)
        cy = sum(u.position.y for u in army) / len(army)
        current_centroid = (cx, cy)
        current_hp = sum(int(u.health) + int(u.shield) for u in army)

        if self._last_army_centroid is None:
            # First observation — cache and return
            self._last_army_centroid = current_centroid
            self._last_army_hp = current_hp
            return False

        # Distance moved since last tick
        dx = current_centroid[0] - self._last_army_centroid[0]
        dy = current_centroid[1] - self._last_army_centroid[1]
        moved = (dx * dx + dy * dy) ** 0.5

        # HP delta — positive means we lost HP
        hp_lost = self._last_army_hp - current_hp

        # Update cache for next call
        self._last_army_centroid = current_centroid
        self._last_army_hp = current_hp

        is_bleeding_now = (
            moved < self.BLEEDING_MOVE_THRESHOLD
            and hp_lost >= self.BLEEDING_HP_PER_TICK_THRESHOLD
        )

        if is_bleeding_now:
            if self._bleeding_since is None:
                self._bleeding_since = snapshot.game_time_seconds
                return False
            elapsed = snapshot.game_time_seconds - self._bleeding_since
            if elapsed >= self.BLEEDING_COMMIT_SECONDS:
                return True
        else:
            self._bleeding_since = None

        return False

    async def _run_micro(self, state: StrategicState) -> None:
        """Issue combat micro commands to army units."""
        army = [u for u in self.units if not u.is_structure and u.type_id != UnitTypeId.PROBE]
        enemies = list(self.enemy_units)
        snapshot = self._build_snapshot()
        cm = self.coherence_manager

        if state in (StrategicState.ATTACK, StrategicState.LATE_GAME):
            # Finisher override: at overwhelming supply, commit the whole
            # army to attack-move on the enemy main. Bypasses coherence,
            # staging, and rally logic — we've won positionally and just
            # need to close the game out.
            if snapshot.army_supply >= self.FINISHER_SUPPLY:
                target = self._enemy_main()
                if target is not None:
                    target_pt = Point2(target)
                    for u in army:
                        u.attack(target_pt)
                    return

            # Bleeding commit: stationary army losing HP (classically stuck
            # below an enemy ramp taking free ranged fire). Force commit
            # forward — the push either succeeds or dies faster than the
            # current slow bleed. Same primitive as the FINISHER override.
            if self._is_bleeding_stationary(army, snapshot):
                target = self._enemy_main()
                if target is not None:
                    target_pt = Point2(target)
                    for u in army:
                        if _should_reissue_attack_to_position(u, target_pt):
                            u.attack(target_pt)
                    _log.info(
                        "Bleeding commit: stationary+losing HP %.1fs, forcing attack-move",
                        snapshot.game_time_seconds - (self._bleeding_since or 0.0),
                    )
                # Reset unconditionally: we've handled this bleeding state (either
                # committed, or deferred because _enemy_main is unknown). Either
                # way don't retry commit every tick from the same _bleeding_since.
                self._bleeding_since = None
                return

            rally = self._resolve_attack_rally(army, snapshot, cm)

            # Hard coherence gate: in ATTACK state, if army is not grouped,
            # gather units at staging point before pushing.
            # BYPASS when army is overwhelming (50+ supply) — just attack-move.
            # Does NOT apply in DEFEND — must fight immediately even if ungrouped.
            if not cm.is_coherent(army) and snapshot.army_supply < 50:
                staging = self._get_staging_point()
                if staging is not None:
                    staging_pt = Point2(staging)
                    for u in army:
                        if u.distance_to(staging_pt) > 5:
                            u.attack(staging_pt)
                return
        else:
            rally = self._defense_rally()

        # Defensive containment: during DEFEND/FORTIFY, only engage enemies
        # near our base. Don't chase enemies across the map — it causes
        # piecemeal fights and constant attrition.
        if state in (StrategicState.DEFEND, StrategicState.FORTIFY) and rally:
            rally_pt = Point2(rally)
            enemies = [e for e in enemies if e.distance_to(rally_pt) < 25]

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
                    if _should_reissue_attack_to_unit(unit, cmd.target_tag):
                        unit.attack(target_unit)
                elif cmd.target_position:
                    pos = Point2(cmd.target_position)
                    if _should_reissue_attack_to_position(unit, pos):
                        unit.attack(pos)
            elif cmd.action == "move" and cmd.target_position is not None:
                # _run_micro only runs in combat states (ATTACK, DEFEND,
                # FORTIFY, LATE_GAME — see _step dispatch). Any advance toward
                # a rally/staging/target point in these states must be
                # attack-move so the army engages enemies on the way instead
                # of walking past them. Plain move is reserved for kiting
                # (handled below) and non-combat scouting paths.
                pos = Point2(cmd.target_position)
                if _should_reissue_attack_to_position(unit, pos):
                    unit.attack(pos)
            elif cmd.action == "kite" and cmd.target_position is not None:
                # Kiting uses plain move so the unit disengages; attack-move
                # would re-target and break the kite.
                unit.move(Point2(cmd.target_position))

    async def _rally_idle_army(self) -> None:
        """Move idle army units to staging point (pre-stage) or defense rally."""
        staging = self._get_staging_point()
        rally = staging if staging else self._defense_rally()
        if rally is None:
            return
        rally_pt = Point2(rally)
        for unit in self.units:
            if unit.is_structure or unit.type_id == UnitTypeId.PROBE:
                continue
            if unit.distance_to(rally_pt) > 10:
                if _should_reissue_attack_to_position(unit, rally_pt):
                    unit.attack(rally_pt)

    def _attack_target(self) -> tuple[float, float] | None:
        """Pick an attack target: enemy natural (early) or enemy main (high supply)."""
        # Late game: push enemy main when at high supply
        if self.supply_used >= self.PUSH_MAIN_SUPPLY:
            return self._enemy_main()
        # Default: deny the enemy natural expansion
        natural = self._enemy_natural()
        if natural is not None:
            return natural
        # Fallback to enemy main
        return self._enemy_main()

    def _enemy_main(self) -> tuple[float, float] | None:
        """Return enemy main base position (scouted or start location)."""
        if self.scout_manager.enemy_base_locations:
            pos = self.scout_manager.enemy_base_locations[0]
            return (float(pos.x), float(pos.y))
        if self.enemy_start_locations:
            pos = self.enemy_start_locations[0]
            return (float(pos.x), float(pos.y))
        return None

    def _enemy_natural(self) -> tuple[float, float] | None:
        """Find the enemy's natural expansion (closest expansion to enemy start).

        Caches the result since expansion locations don't change during a game.
        """
        if self._cached_enemy_natural is not None:
            return self._cached_enemy_natural
        if not self.enemy_start_locations:
            return None
        enemy_start = self.enemy_start_locations[0]
        all_expansions: list[Point2] = self.expansion_locations_list
        candidates = [
            exp
            for exp in all_expansions
            if exp.distance_to(enemy_start) > 1  # exclude start location itself
        ]
        if not candidates:
            return None
        natural = min(candidates, key=lambda loc: loc.distance_to(enemy_start))
        self._cached_enemy_natural = (float(natural.x), float(natural.y))
        return self._cached_enemy_natural

    def _defense_rally(self) -> tuple[float, float] | None:
        """Rally point for defending: between main and natural."""
        if not self.townhalls:
            return None
        main = self.start_location
        natural = self.main_base_ramp.bottom_center
        mid = main.towards(natural, main.distance_to(natural) * 0.6)
        return (float(mid.x), float(mid.y))

    # ------------------------------------------------------------------ #
    #  Army coherence helpers
    # ------------------------------------------------------------------ #

    def _get_staging_point(self) -> tuple[float, float] | None:
        """Return cached staging point, recalculating every ~30s."""
        if self.time - self._staging_point_time >= self._STAGING_RECALC_SECONDS:
            own_base = (float(self.start_location.x), float(self.start_location.y))
            enemy_structs = [
                (float(s.position.x), float(s.position.y))
                for s in self.enemy_structures
            ]
            if self.enemy_start_locations:
                enemy_start = (
                    float(self.enemy_start_locations[0].x),
                    float(self.enemy_start_locations[0].y),
                )
            else:
                enemy_start = own_base  # fallback — shouldn't happen
            self._cached_staging_point = ArmyCoherenceManager.compute_staging_point(
                own_base=own_base,
                enemy_structures=enemy_structs,
                enemy_start=enemy_start,
                staging_distance=self.coherence_manager.staging_distance,
            )
            self._staging_point_time = self.time
        return self._cached_staging_point

    def _resolve_attack_rally(
        self,
        army: list[Any],
        snapshot: GameSnapshot,
        cm: ArmyCoherenceManager,
    ) -> tuple[float, float] | None:
        """Decide rally point during ATTACK state using coherence logic.

        Priority:
        1. Retreat if outnumbered → staging or defense rally (per rolled param)
        2. Not coherent → staging point (gathering)
        3. Coherent + strong enough → attack target (push)
        4. Coherent but not strong enough → hold at staging
        5. Staging timeout → push anyway
        """
        own_supply = float(snapshot.army_supply)
        enemy_supply = float(snapshot.enemy_army_supply_visible)

        # 1. Retreat check
        if cm.should_retreat(own_supply, enemy_supply):
            self.decision_engine.notify_retreat()
            cm.update_staging_timer(self.time, is_staging=False)
            if cm.retreat_to_staging:
                return self._get_staging_point()
            return self._defense_rally()

        staging = self._get_staging_point()
        coherent = cm.is_coherent(army)
        timed_out = cm.update_staging_timer(self.time, is_staging=not coherent)

        # 2. Not coherent (and not timed out) → gather at staging
        if not coherent and not timed_out:
            return staging

        # 3. Coherent (or timed out) + strong enough → push
        if cm.should_attack(own_supply, enemy_supply):
            cm.update_staging_timer(self.time, is_staging=False)
            return self._attack_target()

        # 4. Timed out but not strong enough → push anyway (safety valve)
        if timed_out:
            cm.update_staging_timer(self.time, is_staging=False)
            return self._attack_target()

        # 5. Coherent but not strong enough → hold at staging
        return staging
