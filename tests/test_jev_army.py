"""Jev's rush army and terminal lifecycle through the production entry point (Step 203).

Every scenario runs the path a real match runs: :func:`jev.runner.run_match` (or
the CLI :func:`jev.runner.main`) -> :class:`jev.bot.JevController` ->
``JevBot.on_step`` -> :class:`jev.sc2_adapter.Sc2Adapter` ->
:class:`jev.runtime.JevRuntime` with the packaged v1 policy ->
:class:`jev.sc2_adapter.BotAIPort`. Only the SC2 process is replaced: :class:`_World`
is a ``BotAI`` stand-in whose units are real burnysc2 ``Unit`` objects built from
protobuf with real ``GameData``. Its explicit model of SC2's response:

* units walk toward their order's target at a fixed speed; an attack-move engages
  the nearest visible ground enemy within acquisition range; melee damage removes
  an enemy at zero health, and an attack order on a dead unit ends;
* a blind spot (vision blocked, e.g. across a cliff) hides what stands in it;
* an untouchable enemy (across a cliff edge) can be stood beside but takes no damage;
* an unreachable spot (a plateau without a ramp) stops a unit ordered into it at
  its edge; other trips are unaffected;
* a move or attack-move onto an unwalkable spot is refused
  (``MustTargetWalkableLocation``), and one onto an ignored spot is reported a
  success but never carried out; either way the unit keeps its previous orders;
* vision is a radius around own entities: enemies outside it are not reported (a
  structure seen before comes back only as a fogged snapshot, which the adapter
  must not treat as visible state);
* SC2 ends the match with a Victory once the last enemy structure dies and with a
  Defeat once the last own structure does.

The last section audits Jev's import graph for legacy gameplay, PPO inference and
LLM clients, statically and in a fresh interpreter.
"""

from __future__ import annotations

import ast
import asyncio
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bots.jev.v1 import load_policy
from bots.jev.v2 import load_policy as load_v2_policy
from s2clientprotocol import common_pb2, data_pb2, raw_pb2, sc2api_pb2
from sc2.data import ActionResult, Alliance, Attribute, DisplayType, Result
from sc2.game_data import GameData
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit import Unit
from sc2.unit_command import UnitCommand

from jev import runner
from jev.bot import MAX_LEAVE_ATTEMPTS, JevBot, JevController
from jev.contracts import Event
from jev.operations import PYLON_POWER_RADIUS, TARGET_LOST_REASON, distance
from jev.policy import PolicyBundle
from jev.runner import (
    EXIT_FAILURE,
    MatchOptions,
    MatchOutcome,
    run_match,
)
from jev.runtime import MAX_TARGET_GIVE_UPS
from jev.sc2_adapter import GAME_LOOPS_PER_SECOND, BotAIPort

START = (30.5, 30.5)
ENEMY_START = (120.5, 120.5)
CENTER = (75.5, 75.5)
ENEMY_NATURAL = (95.5, 120.5)
THIRD = (75.5, 105.5)
OWN_NATURAL = (55.5, 30.5)
#: Distances from the enemy start: 0, 25, 47.4, 111.0 and 127.3.
EXPANSIONS = (START, ENEMY_START, ENEMY_NATURAL, THIRD, OWN_NATURAL)
NEXUS = 1000
PYLON = 1100
GATEWAY_SITES = ((26.5, 34.5), (29.5, 34.5), (24.0, 37.5), (21.5, 34.5))
PROBES = tuple(range(2000, 2012))
ZEALOTS = (6000, 6001, 6002, 6003)
#: burnysc2 reads visibility from display_type alone at or above this base build.
BASE_BUILD = 90_000
#: Game loops per on_step: every step is a policy tick (0.36 s > the 0.25 s cadence).
STEP_LOOPS = 8
STEP_SECONDS = STEP_LOOPS / GAME_LOOPS_PER_SECOND
#: Float slack for game-time boundaries (game seconds are loop / 22.4).
_EPSILON = 1e-6
#: The scripted world's mechanics (game units and seconds; small numbers keep runs short).
SPEED = 12.0
SIGHT = 10.0
ACQUIRE = 6.0
REACH = 1.5
DPS = 20.0
#: Never within sight of any route in these scenarios: hidden-map information.
HIDDEN_SITE = (140.0, 30.0)

# ---------------------------------------------------------------------------
# Real burnysc2 game data and units, built from protobuf (no SC2 process)
# ---------------------------------------------------------------------------

#: name -> (unit type, is a structure); names are SC2's unit type names.
_KINDS: dict[str, tuple[UnitTypeId, bool]] = {
    "Probe": (UnitTypeId.PROBE, False),
    "Zealot": (UnitTypeId.ZEALOT, False),
    "Nexus": (UnitTypeId.NEXUS, True),
    "Pylon": (UnitTypeId.PYLON, True),
    "Gateway": (UnitTypeId.GATEWAY, True),
    "MineralField": (UnitTypeId.MINERALFIELD, False),
    "Marine": (UnitTypeId.MARINE, False),
    "CommandCenter": (UnitTypeId.COMMANDCENTER, True),
    "SupplyDepot": (UnitTypeId.SUPPLYDEPOT, True),
    "Barracks": (UnitTypeId.BARRACKS, True),
    "Bunker": (UnitTypeId.BUNKER, True),
    "PhotonCannon": (UnitTypeId.PHOTONCANNON, True),
    # Step 218 (the mixed army): Stalkers, and an air unit only they can hit.
    "Stalker": (UnitTypeId.STALKER, False),
    "Banshee": (UnitTypeId.BANSHEE, False),
}
#: SC2 structures that need Pylon power to operate (written out independently of
#: Jev's own REQUIRES_POWER).
_POWERED_KINDS = frozenset({"Gateway", "CyberneticsCore"})
#: Exact ability -> generic ability, as SC2's game data remaps them.
_REMAPS = {
    AbilityId.HARVEST_GATHER_PROBE: AbilityId.HARVEST_GATHER,
    AbilityId.HARVEST_RETURN_PROBE: AbilityId.HARVEST_RETURN,
    AbilityId.MOVE_MOVE: AbilityId.MOVE,
    AbilityId.ATTACK_ATTACK: AbilityId.ATTACK,
}
#: Production-queue abilities, reported without a remap.
_PLAIN = (AbilityId.GATEWAYTRAIN_ZEALOT, AbilityId.NEXUSTRAIN_PROBE)
_GATEWAY_SECONDS = 46.0
_ZEALOT_SECONDS = 27.0
#: SC2's weapon targets (game data) of the armed kinds: burnysc2 derives a unit's
#: ``can_attack_ground`` / ``can_attack_air`` from these (Step 218).
_WEAPONS: dict[str, int] = {
    "Probe": data_pb2.Weapon.Ground,
    "Zealot": data_pb2.Weapon.Ground,
    "Stalker": data_pb2.Weapon.Any,
    "Marine": data_pb2.Weapon.Any,
    "Banshee": data_pb2.Weapon.Ground,
    "PhotonCannon": data_pb2.Weapon.Any,
}
#: Own kinds that fight, with their reach (a Zealot is melee, a Stalker shoots at 6).
_REACH = {"Zealot": REACH, "Stalker": 6.0}
_AIR = frozenset({data_pb2.Weapon.Air, data_pb2.Weapon.Any})


def _game_data() -> GameData:
    units = [
        data_pb2.UnitTypeData(
            unit_id=type_id.value,
            name=name,
            available=True,
            attributes=[Attribute.Structure.value] if structure else [],
            weapons=(
                [data_pb2.Weapon(type=_WEAPONS[name], damage=10, attacks=1, range=1, speed=1)]
                if name in _WEAPONS
                else []
            ),
        )
        for name, (type_id, structure) in _KINDS.items()
    ]
    plain = {*_PLAIN, *_REMAPS.values()}
    abilities = [data_pb2.AbilityData(ability_id=a.value, available=True) for a in plain] + [
        data_pb2.AbilityData(ability_id=a.value, remaps_to_ability_id=g.value, available=True)
        for a, g in _REMAPS.items()
    ]
    return GameData(sc2api_pb2.ResponseData(units=units, abilities=abilities))


_GAME_DATA = _game_data()

type _Point = tuple[float, float]
type _OrderTarget = int | _Point | None


@dataclass
class _Body:
    """One entity of the scripted world (re-sent as a fresh burnysc2 Unit every step)."""

    tag: int
    kind: str
    position: _Point
    alliance: Alliance = Alliance.Self
    progress: float = 1.0
    health: float = 100.0
    flying: bool = False
    shield: float = 0.0
    orders: list[tuple[AbilityId, _OrderTarget, float]] = field(default_factory=list)
    initial: tuple[float, float] = (-1.0, -1.0)  # (health, shield) when first fought

    def durability_lost(self) -> float:
        """Health plus shields lost since the body was created."""
        return self.initial[0] + self.initial[1] - self.health - self.shield

    def __post_init__(self) -> None:
        self.initial = (self.health, self.shield)


class _Client:
    """burnysc2 ``Client`` stand-in: one verdict per command; a leave may fail."""

    def __init__(self, world: _World) -> None:
        self._world = world

    async def actions(
        self, commands: list[UnitCommand], return_successes: bool = False
    ) -> list[ActionResult]:
        assert return_successes, "the port must ask for one verdict per command"
        return [self._world.receive(command) for command in commands]

    async def leave(self) -> None:
        world = self._world
        world.leave_calls += 1
        if world.leave_failures > 0:
            world.leave_failures -= 1
            raise ConnectionError("SC2 did not acknowledge the leave")
        world.left = True


class _World:
    """A ``BotAI`` stand-in with burnysc2's attribute names (see module docstring)."""

    def __init__(self, bodies: list[_Body], *, minerals: int = 0) -> None:
        self.game_data = _GAME_DATA
        self.state = SimpleNamespace(game_loop=0, action_errors=[])
        self.bodies = {body.tag: body for body in bodies}
        self.minerals = minerals
        self.vespene = 0
        self.supply_used, self.supply_cap = 28, 47  # spare supply: no supply Pylon
        self.start_location = Point2(START)
        self.enemy_start_locations = [Point2(ENEMY_START)]
        self.expansion_locations_list = [Point2(point) for point in EXPANSIONS]
        self.game_info = SimpleNamespace(map_center=Point2(CENTER))
        self.client = _Client(self)
        self.speed = SPEED
        self.frozen = False  # True: units accept orders but never move
        self.stuck: set[int] = set()  # own units that accept orders but never move
        self.plateaus: list[tuple[_Point, float]] = []  # (centre, radius): unreachable
        self.unwalkable: list[tuple[_Point, float]] = []  # (centre, radius): orders refused
        self.ignored: list[tuple[_Point, float]] = []  # (centre, radius): orders dropped
        self.blind_spots: list[tuple[_Point, float]] = []  # (centre, radius): unseeable
        self.untouchable: set[int] = set()  # enemy tags Zealots cannot hurt
        self.result: Result | None = None
        self.left = False
        self.leave_calls = 0
        self.leave_failures = 0
        self.sent: list[tuple[int, UnitCommand]] = []
        self.unexpected: list[str] = []
        self.ever_seen: set[int] = set()
        self._had_enemy_structures = bool(self.enemy_structure_tags())
        self._next_tag = 4_000_000_000_000  # SC2 tags are large 64-bit values
        self.refresh()

    @property
    def seconds(self) -> float:
        return self.state.game_loop / GAME_LOOPS_PER_SECOND

    def add(
        self, kind: str, position: _Point, *, enemy: bool = False, health: float = 100.0
    ) -> int:
        self._next_tag += 1
        alliance = Alliance.Enemy if enemy else Alliance.Self
        self.bodies[self._next_tag] = _Body(self._next_tag, kind, position, alliance, health=health)
        self.refresh()
        return self._next_tag

    def kill(self, tag: int) -> None:
        """Remove an entity; an attack order on it ends, as in SC2."""
        self._remove(tag)
        self.refresh()

    def _remove(self, tag: int) -> None:
        del self.bodies[tag]
        for body in self.bodies.values():
            if body.orders and body.orders[0][1] == tag:
                body.orders = []

    def own(self, kind: str) -> list[_Body]:
        return [b for b in self.bodies.values() if b.kind == kind and b.alliance == Alliance.Self]

    def enemy_structure_tags(self) -> list[int]:
        return [
            b.tag
            for b in self.bodies.values()
            if b.alliance == Alliance.Enemy and _KINDS[b.kind][1]
        ]

    def sees(self, point: _Point) -> bool:
        if any(distance(point, centre) < radius for centre, radius in self.blind_spots):
            return False
        own = (b for b in self.bodies.values() if b.alliance == Alliance.Self)
        return any(distance(point, b.position) <= SIGHT for b in own)

    # -- what burnysc2 exposes -------------------------------------------------

    def refresh(self) -> None:
        """Re-send the observable bodies as fresh burnysc2 Units, as an observation does."""
        pylons = [b.position for b in self.own("Pylon") if b.progress >= 1.0]
        groups: dict[str, list[Unit]] = {
            "units": [],
            "structures": [],
            "enemy_units": [],
            "enemy_structures": [],
            "mineral_field": [],
            "vespene_geyser": [],
        }
        for body in sorted(self.bodies.values(), key=lambda b: b.tag):
            structure = _KINDS[body.kind][1]
            display = DisplayType.Visible
            if body.alliance == Alliance.Enemy:
                if self.sees(body.position):
                    self.ever_seen.add(body.tag)
                elif structure and body.tag in self.ever_seen:
                    display = DisplayType.Snapshot  # fogged memory, not visible state
                else:
                    continue  # never reported: hidden-map information
            powered = body.kind in _POWERED_KINDS and any(
                distance(body.position, p) <= PYLON_POWER_RADIUS for p in pylons
            )
            unit = Unit(self._proto(body, display, powered), self, base_build=BASE_BUILD)
            if body.kind == "MineralField":
                groups["mineral_field"].append(unit)
            elif body.alliance == Alliance.Enemy:
                groups["enemy_structures" if structure else "enemy_units"].append(unit)
            else:
                groups["structures" if structure else "units"].append(unit)
        for name, units in groups.items():
            setattr(self, name, units)

    @staticmethod
    def _proto(body: _Body, display: DisplayType, powered: bool) -> raw_pb2.Unit:
        orders = []
        for ability, target, progress in body.orders:
            order = raw_pb2.UnitOrder(ability_id=ability.value, progress=progress)
            if isinstance(target, tuple):
                order.target_world_space_pos.x, order.target_world_space_pos.y = target
            elif target is not None:
                order.target_unit_tag = target
            orders.append(order)
        return raw_pb2.Unit(
            display_type=display.value,
            alliance=body.alliance.value,
            tag=body.tag,
            unit_type=_KINDS[body.kind][0].value,
            pos=common_pb2.Point(x=body.position[0], y=body.position[1], z=10.0),
            health=body.health,
            shield=body.shield,
            build_progress=body.progress,
            is_powered=powered,
            is_flying=body.flying,
            orders=orders,
        )

    def is_visible(self, pos: Point2) -> bool:
        return self.sees((pos.x, pos.y))

    async def can_place(self, ability: AbilityId, positions: list[Point2]) -> list[bool]:
        self.unexpected.append(f"placement query {ability.name}")
        return [True for _ in positions]

    # -- SC2's response to a command -------------------------------------------

    def receive(self, command: UnitCommand) -> ActionResult:
        self.sent.append((self.state.game_loop, command))
        body = self.bodies.get(command.unit.tag)
        if body is None or body.alliance != Alliance.Self:
            self.unexpected.append(f"command for foreign unit {command.unit.tag}")
            return ActionResult.NotSupported
        ability, target = command.ability, command.target
        point = (target.x, target.y) if isinstance(target, Point2) else None
        if point is not None and ability in (AbilityId.MOVE_MOVE, AbilityId.ATTACK):
            if any(distance(point, centre) < radius for centre, radius in self.unwalkable):
                return ActionResult.MustTargetWalkableLocation  # refused: orders unchanged
            if any(distance(point, centre) < radius for centre, radius in self.ignored):
                return ActionResult.Success  # accepted, then never carried out
        if ability == AbilityId.HARVEST_GATHER and isinstance(target, Unit):
            body.orders = [(AbilityId.HARVEST_GATHER_PROBE, target.tag, 0.0)]
        elif ability == AbilityId.MOVE_MOVE and point is not None:
            body.orders = [(AbilityId.MOVE_MOVE, point, 0.0)]
        elif ability == AbilityId.ATTACK and point is not None:
            body.orders = [(AbilityId.ATTACK_ATTACK, point, 0.0)]
        elif ability == AbilityId.ATTACK and isinstance(target, Unit):
            aimed = self.bodies.get(target.tag)
            if aimed is not None and aimed.flying and _WEAPONS.get(body.kind) not in _AIR:
                self.unexpected.append(f"{body.kind} {body.tag} ordered onto air {target.tag}")
                return ActionResult.CantTargetAirUnits
            body.orders = [(AbilityId.ATTACK_ATTACK, target.tag, 0.0)]
        elif ability == AbilityId.GATEWAYTRAIN_ZEALOT and body.kind == "Gateway":
            if self.minerals < 100 or self.supply_cap - self.supply_used < 2:
                return ActionResult.NotEnoughMinerals
            self.minerals -= 100
            self.supply_used += 2
            body.orders.append((ability, None, 0.0))
        else:
            self.unexpected.append(f"{ability.name} for {command.unit.tag}")
            return ActionResult.NotSupported
        return ActionResult.Success

    def advance(self, loops: int = STEP_LOOPS) -> None:
        """Let ``loops`` game loops pass, then observe again."""
        self.state.game_loop += loops
        self.state.action_errors = []
        dt = loops / GAME_LOOPS_PER_SECOND
        for body in list(self.bodies.values()):
            if body.alliance != Alliance.Self:
                continue
            if body.kind == "Gateway" and body.progress < 1.0:
                body.progress = min(1.0, body.progress + dt / _GATEWAY_SECONDS)
            if body.orders and body.orders[0][0] == AbilityId.GATEWAYTRAIN_ZEALOT:
                ability, _, progress = body.orders[0]
                progress += dt / _ZEALOT_SECONDS
                if progress >= 1.0:
                    body.orders.pop(0)
                    self._next_tag += 1
                    spawn = (body.position[0] + 2.0, body.position[1] - 2.0)
                    self.bodies[self._next_tag] = _Body(self._next_tag, "Zealot", spawn)
                else:
                    body.orders[0] = (ability, None, progress)
            elif body.kind in _REACH and body.orders and not self.frozen:
                if body.tag not in self.stuck:
                    self._act(body, dt)
        for tag in [t for t, b in self.bodies.items() if b.health <= 0.0]:
            self._remove(tag)
        if self.result is None:
            if self._had_enemy_structures and not self.enemy_structure_tags():
                self.result = Result.Victory
            elif not any(
                _KINDS[b.kind][1] for b in self.bodies.values() if b.alliance == Alliance.Self
            ):
                self.result = Result.Defeat
        self.refresh()

    def _act(self, body: _Body, dt: float) -> None:
        ability, target, _ = body.orders[0]
        if ability == AbilityId.MOVE_MOVE and isinstance(target, tuple):
            if self._walk(body, target, dt, stop=0.0):
                body.orders = []
        elif ability == AbilityId.ATTACK_ATTACK and isinstance(target, int):
            self._fight(body, self.bodies[target], dt)  # dead targets end the order
        elif ability == AbilityId.ATTACK_ATTACK and isinstance(target, tuple):
            enemy = self._acquire(body)
            if enemy is not None:
                self._fight(body, enemy, dt)
            elif self._walk(body, target, dt, stop=0.0):
                body.orders = []

    def _walk(self, body: _Body, goal: _Point, dt: float, *, stop: float) -> bool:
        """Walk toward ``goal``, halting ``stop`` short of it; True once there."""
        if self._blocked(body.position, goal, self.speed * dt):
            return False
        gap = distance(body.position, goal)
        travel = gap - stop
        if travel <= self.speed * dt:
            ratio = 0.0 if gap == 0.0 else stop / gap
            body.position = (
                goal[0] + (body.position[0] - goal[0]) * ratio,
                goal[1] + (body.position[1] - goal[1]) * ratio,
            )
            return True
        step = self.speed * dt / gap
        body.position = (
            body.position[0] + (goal[0] - body.position[0]) * step,
            body.position[1] + (goal[1] - body.position[1]) * step,
        )
        return False

    def _blocked(self, position: _Point, goal: _Point, travel: float) -> bool:
        """A goal on a plateau: the walk stops where the next step would climb it."""
        gap = distance(position, goal)
        ratio = 1.0 if gap <= travel else travel / gap
        ahead = (
            position[0] + (goal[0] - position[0]) * ratio,
            position[1] + (goal[1] - position[1]) * ratio,
        )
        return any(
            distance(goal, centre) < radius and distance(ahead, centre) < radius
            for centre, radius in self.plateaus
        )

    def _fight(self, body: _Body, enemy: _Body, dt: float) -> None:
        reach = _REACH.get(body.kind, REACH)
        if distance(body.position, enemy.position) > reach + _EPSILON:
            self._walk(body, enemy.position, dt, stop=reach)
        else:
            if enemy.tag not in self.untouchable:
                damage = DPS * dt
                absorbed = min(enemy.shield, damage)  # shields take damage first
                enemy.shield -= absorbed
                enemy.health -= damage - absorbed

    def _acquire(self, body: _Body) -> _Body | None:
        """The nearest visible enemy its weapons can hit within acquisition range (tie by
        tag): ground only for a Zealot, air too for a Stalker."""
        anti_air = _WEAPONS.get(body.kind) in _AIR
        candidates = [
            (distance(body.position, b.position), b.tag)
            for b in self.bodies.values()
            if b.alliance == Alliance.Enemy
            and (anti_air or not b.flying)
            and distance(body.position, b.position) <= ACQUIRE
            and self.sees(b.position)
        ]
        return self.bodies[min(candidates)[1]] if candidates else None


def _base(*, zealots: int = 4, at: _Point = (36.0, 40.0), extra: tuple[_Body, ...] = ()) -> _World:
    """A finished one-base Jev: Nexus, Pylon, four powered Gateways, mining probes.

    No minerals: production holds its reservations and the economy is satisfied,
    so every command sent comes from the army lane unless a scenario changes that.
    """
    bodies = [_Body(NEXUS, "Nexus", START), _Body(PYLON, "Pylon", (24.0, 34.0))]
    bodies += [_Body(1200 + i, "Gateway", site) for i, site in enumerate(GATEWAY_SITES)]
    patches = [3000 + i for i in range(8)]
    bodies += [
        _Body(tag, "MineralField", (26.5 + i, 22.5), alliance=Alliance.Neutral)
        for i, tag in enumerate(patches)
    ]
    bodies += [
        _Body(tag, "Probe", (27.0 + i * 0.5, 26.0), orders=[_gather(patches[i % 8])])
        for i, tag in enumerate(PROBES)
    ]
    bodies += [_Body(tag, "Zealot", at, health=150.0) for tag in ZEALOTS[:zealots]]
    return _World([*bodies, *extra])


def _gather(patch: int) -> tuple[AbilityId, _OrderTarget, float]:
    return (AbilityId.HARVEST_GATHER_PROBE, patch, 0.0)


def _training(progress: float) -> tuple[AbilityId, _OrderTarget, float]:
    return (AbilityId.GATEWAYTRAIN_ZEALOT, None, progress)


def _enemy(tag: int, kind: str, position: _Point, health: float = 40.0) -> _Body:
    return _Body(tag, kind, position, Alliance.Enemy, health=health)


# ---------------------------------------------------------------------------
# The scripted launcher: production JevBot / controller path against _World
# ---------------------------------------------------------------------------

type _Hook = Callable[[_World, JevController, int], None]


class _Launcher:
    """A :class:`jev.runner.MatchLauncher` playing ``world`` through ``JevBot``.

    Like burnysc2: an exception out of ``on_step`` propagates (and ends the game),
    a bot that left the game ends it with a Defeat, and SC2's own result ends it.
    Every runtime event is kept (the controller keeps only the latest 200).
    """

    def __init__(self, world: _World, steps: int, *hooks: _Hook) -> None:
        self.world = world
        self.steps = steps
        self.hooks = hooks
        self.controller: JevController | None = None
        self.events: dict[int, Event] = {}

    def prepare(self, options: MatchOptions) -> object:
        return self.world

    def play(self, controller: JevController, setup: object, options: MatchOptions) -> Any:
        self.controller = controller
        bot = JevBot(controller)
        # What JevBot.on_start binds against a live game (minus the Ctrl+C handler).
        controller.attach(self.world, BotAIPort(self.world))  # type: ignore[arg-type]
        asyncio.run(self._run(bot, controller))
        return controller.result

    async def _run(self, bot: JevBot, controller: JevController) -> None:
        world = self.world
        for index in range(self.steps):
            if world.result is not None:
                break
            for hook in self.hooks:
                hook(world, controller, index)
            await bot.on_step(index)
            self.events.update((event.sequence, event) for event in controller.recent_events)
            if world.left:
                break
            world.advance()
        if world.left:
            final = Result.Defeat
        else:
            final = world.result if world.result is not None else Result.Undecided
        await bot.on_end(final)


@dataclass(frozen=True)
class _Run:
    outcome: MatchOutcome
    controller: JevController
    launcher: _Launcher

    @property
    def events(self) -> list[Event]:
        return [self.launcher.events[seq] for seq in sorted(self.launcher.events)]


def _play(
    world: _World,
    steps: int,
    *hooks: _Hook,
    options: MatchOptions | None = None,
    clock: Callable[[], float] | None = None,
    bundle: PolicyBundle | None = None,
) -> _Run:
    """Play through the production runner (the packaged v1 policy unless ``bundle``)."""
    launcher = _Launcher(world, steps, *hooks)
    bundle = load_policy() if bundle is None else bundle
    chosen = MatchOptions() if options is None else options
    with tempfile.TemporaryDirectory() as run_root:  # the evidence is not under test here
        root = Path(run_root)
        if clock is None:
            outcome = run_match(chosen, bundle, launcher=launcher, run_root=root)
        else:
            outcome = run_match(chosen, bundle, launcher=launcher, clock=clock, run_root=root)
    assert launcher.controller is not None
    sequences = sorted(launcher.events)
    assert sequences == list(range(1, len(sequences) + 1))  # the trace lost no event
    assert world.unexpected == []  # nothing but the scripted commands reached SC2
    return _Run(outcome, launcher.controller, launcher)


def _target_of(event: Event) -> int | _Point:
    """A command event's target: a tag (decimal string on the wire) or an (x, y) point."""
    assert event.action is not None
    target = event.action["target"]
    if isinstance(target, str):
        return int(target)
    assert isinstance(target, list) and len(target) == 2
    x, y = target
    assert isinstance(x, float) and isinstance(y, float)
    return (x, y)


def _actor_of(event: Event) -> int:
    assert event.action is not None
    tags = event.action["actor_tags"]
    assert isinstance(tags, list) and len(tags) == 1 and isinstance(tags[0], str)
    return int(tags[0])


def _commands(run: _Run, node_id: str) -> list[Event]:
    return [e for e in run.events if e.kind == "command" and e.node_id == node_id]


def _target_sequence(run: _Run) -> list[int | _Point]:
    """The attack lane's targets in order, consecutive repeats collapsed."""
    sequence: list[int | _Point] = []
    for event in _commands(run, "army.attack.go"):
        target = _target_of(event)
        if not sequence or sequence[-1] != target:
            sequence.append(target)
    return sequence


def _failed(run: _Run, node_id: str) -> list[Event]:
    """The trace's failed-task events of ``node_id``, in order."""
    events = run.events
    return [e for e in events if e.kind == "task" and e.status == "failed" and e.node_id == node_id]


def _demoted_then_searching_on(run: _Run, structure: _Body) -> Event:
    """Assert the run's one demotion: ``structure``, after MAX_TARGET_GIVE_UPS rounds,
    with the army sent on to the search on that very tick. Returns the diagnostic."""
    (demoted,) = [e for e in run.events if e.facts.get("demoted") is True]
    assert demoted.facts["target"] == str(structure.tag)
    assert demoted.facts["give_ups"] == MAX_TARGET_GIVE_UPS  # bounded rounds
    onward = [e for e in _commands(run, "army.attack.go") if e.game_loop == demoted.game_loop]
    assert {_target_of(e) for e in onward} == {ENEMY_NATURAL}  # the search, at once
    return demoted


def _sent_attacks(world: _World) -> list[tuple[int, int, int | _Point]]:
    """``(game loop, actor, target)`` of every ATTACK SC2 received."""
    found: list[tuple[int, int, int | _Point]] = []
    for loop, command in world.sent:
        if command.ability != AbilityId.ATTACK:
            continue
        target = command.target
        shown = target.tag if isinstance(target, Unit) else (target.x, target.y)
        found.append((loop, command.unit.tag, shown))
    return found


def _loop_of(step: int) -> int:
    return step * STEP_LOOPS


# ---------------------------------------------------------------------------
# First attack, rally and reinforcement (D3)
# ---------------------------------------------------------------------------


def test_scenario_four_ready_zealots_launch_the_attack_before_every_gateway_completes() -> None:
    """D3: the first attack latches at four ready Zealots, not at Gateway completion."""
    pylon = _Body(PYLON, "Pylon", (24.0, 34.0))
    gateways = [  # two finishing a Zealot each, two still warping in
        _Body(1200, "Gateway", GATEWAY_SITES[0], orders=[_training(0.9)]),
        _Body(1201, "Gateway", GATEWAY_SITES[1], orders=[_training(0.85)]),
        _Body(1202, "Gateway", GATEWAY_SITES[2], progress=0.3),
        _Body(1203, "Gateway", GATEWAY_SITES[3], progress=0.3),
    ]
    zealots = [_Body(tag, "Zealot", (28.0, 40.0), health=150.0) for tag in ZEALOTS[:2]]
    probes = [_Body(tag, "Probe", (28.0, 26.0), orders=[_gather(3000)]) for tag in PROBES]
    patch = _Body(3000, "MineralField", (26.5, 22.5), alliance=Alliance.Neutral)
    world = _World([_Body(NEXUS, "Nexus", START), pylon, *gateways, *zealots, *probes, patch])
    census: dict[int, tuple[int, list[float]]] = {}

    def count(world: _World, controller: JevController, index: int) -> None:
        progress = [world.bodies[1200 + i].progress for i in range(4)]
        census[world.state.game_loop] = (len(world.own("Zealot")), progress)

    _play(world, 24, count)
    attacks = _sent_attacks(world)
    launch_loop = attacks[0][0]
    zealots_then, gateway_progress = census[launch_loop]
    assert zealots_then == 4  # D3: four ready Zealots latch the first attack ...
    assert min(gateway_progress) < 1.0  # ... while Gateways are still warping in
    launch = [(actor, target) for loop, actor, target in attacks if loop == launch_loop]
    assert sorted(actor for actor, _ in launch) == sorted(z.tag for z in world.own("Zealot"))
    assert {target for _, target in launch} == {ENEMY_START}  # the first known enemy start
    # Before the latch the army gathered eight units from the main toward the map center.
    early = [c for loop, c in world.sent if loop < launch_loop]
    assert {c.ability for c in early} == {AbilityId.MOVE_MOVE}
    for command in early:
        point = (command.target.x, command.target.y)
        assert distance(point, START) == pytest.approx(8.0, abs=0.02)
        assert distance(START, point) + distance(point, CENTER) == pytest.approx(
            distance(START, CENTER), abs=0.02
        )


def test_scenario_reinforcements_join_the_attack_after_losses_drop_the_army_below_four() -> None:
    """D3: new Zealots reinforce below four after the latch; D4: dead actors invalidate tasks."""
    world = _base()
    reinforcement: list[int] = []

    def losses_then_a_new_zealot(world: _World, controller: JevController, index: int) -> None:
        if index == 4:
            for tag in ZEALOTS[1:]:
                world.kill(tag)  # three of four lost on the way: one attacker remains
        if index == 6:
            reinforcement.append(world.add("Zealot", (28.5, 32.5)))

    run = _play(world, 10, losses_then_a_new_zealot)
    (new_tag,) = reinforcement
    attacks = _sent_attacks(world)
    assert [(a, t) for loop, a, t in attacks if loop == _loop_of(6)] == [(new_tag, ENEMY_START)]
    assert [a for loop, a, _ in attacks if loop > 0 and a == ZEALOTS[0]] == []  # task kept
    assert not [c for _, c in world.sent if c.ability == AbilityId.MOVE_MOVE]  # never rallied
    lost = _failed(run, "army.attack.go")
    assert sorted(int(str(e.facts["actor_tag"])) for e in lost) == list(ZEALOTS[1:])
    assert {e.game_loop for e in lost} == {_loop_of(4)}  # on the first tick without them
    assert {e.facts["failure_cause"] for e in lost} == {"lost"}  # no cooldown cause


# ---------------------------------------------------------------------------
# Defense interrupts the attack; dead targets are invalidated (D3/D4)
# ---------------------------------------------------------------------------

_NEAR, _FAR = (40.0, 30.5), (30.5, 42.0)  # 9.5 and 11.5 from the main, both in sight


def test_scenario_defense_cancels_the_attack_first_drops_dead_threats_then_resumes() -> None:
    """D3/D4: defense cancels attack tasks first; a dead threat invalidates its tasks at
    once, acknowledged or not, never retried; defense retargets, then the attack resumes."""
    world = _base(at=(60.0, 60.0))
    threats: list[int] = []

    def intruders(world: _World, controller: JevController, index: int) -> None:
        if index == 3:
            threats.append(world.add("Marine", _NEAR, enemy=True, health=30.0))
            threats.append(world.add("Marine", _FAR, enemy=True, health=30.0))
        if index == 4:
            world.kill(threats[0])  # killed elsewhere before SC2 acknowledged the orders

    run = _play(world, 45, intruders)
    near, far = threats
    defense = _commands(run, "army.defend.attack")
    assert defense[0].game_loop == _loop_of(3)
    # Nearest threat to the main first; every Zealot's attack task is cancelled first.
    opening = [e for e in defense if e.game_loop == _loop_of(3)]
    assert sorted(_actor_of(e) for e in opening) == list(ZEALOTS)
    assert {_target_of(e) for e in opening} == {near}
    for event in opening:
        actor = str(_actor_of(event))
        (cancel,) = [
            e
            for e in run.events
            if e.kind == "task" and e.status == "cancelled" and e.facts["actor_tag"] == actor
        ]
        assert cancel.node_id == "army.attack.go"
        assert cancel.sequence < event.sequence  # cancelled before defense takes command
    # A dead threat invalidates its tasks at once; defense retargets on the same tick.
    lost = _failed(run, "army.defend.attack")
    assert {e.reason for e in lost} == {TARGET_LOST_REASON}
    assert {e.facts["failure_cause"] for e in lost} == {"lost"}
    unacknowledged = [e for e in lost if e.game_loop == _loop_of(4)]
    assert sorted(int(str(e.facts["actor_tag"])) for e in unacknowledged) == list(ZEALOTS)
    at_near = [(loop, actor) for loop, actor, target in _sent_attacks(world) if target == near]
    assert sorted(at_near) == [(_loop_of(3), tag) for tag in ZEALOTS]  # never re-sent
    retarget = [e for e in defense if e.game_loop == _loop_of(4)]
    assert {_target_of(e) for e in retarget} == {far} and len(retarget) == 4
    # The far threat dies to the Zealots; the army resumes: fresh tasks for the enemy start.
    resume_loop = max(e.game_loop for e in lost)
    assert resume_loop > _loop_of(4) and far not in world.bodies
    resumed = [e for e in _commands(run, "army.attack.go") if e.game_loop >= resume_loop]
    assert sorted(_actor_of(e) for e in resumed[:4]) == list(ZEALOTS)
    assert {_target_of(e) for e in resumed[:4]} == {ENEMY_START}
    launched = {e.task_id for e in _commands(run, "army.attack.go") if e.game_loop == 0}
    assert launched.isdisjoint(e.task_id for e in resumed)  # reselected from current facts


@pytest.mark.parametrize(
    ("threat", "steps", "spots"),
    [("standing", 330, 1), ("circling", 230, 8)],
)
def test_scenario_defense_against_a_threat_it_cannot_hurt_is_bounded(
    threat: str, steps: int, spots: int
) -> None:
    """D3/D4: defense demotes a threat it cannot hurt after bounded give-up rounds,
    however the threat moves (a unit keeps its rounds); then the attack resumes."""
    world = _base()
    world.frozen = threat == "circling"  # circling: the Zealots stand, it goes round them
    threats: list[int] = []
    seen_at: set[_Point] = set()

    def unhittable_intruder(world: _World, controller: JevController, index: int) -> None:
        if index < 3:
            return
        spot = _NEAR
        if threat == "circling":
            angle = index * math.pi / 4
            spot = (round(36.0 + 2.0 * math.cos(angle), 3), round(40.0 + 2.0 * math.sin(angle), 3))
        if not threats:
            threats.append(world.add("Marine", spot, enemy=True))
            world.untouchable.add(threats[0])  # beside the Zealots, across a cliff edge
        world.bodies[threats[0]].position = spot
        seen_at.add(spot)
        world.refresh()

    run = _play(world, steps, unhittable_intruder)
    (marine,) = threats
    assert len(seen_at) == spots  # where it stood: circling, it never stood still
    defense = _commands(run, "army.defend.attack")
    assert {_target_of(e) for e in defense} == {marine}
    (demoted,) = [e for e in run.events if e.facts.get("demoted") is True]
    assert (demoted.node_id, demoted.facts["target"]) == ("army.defend.attack", str(marine))
    assert max(e.game_loop for e in defense) < demoted.game_loop  # no defense after it
    resumed = [e for e in _commands(run, "army.attack.go") if e.game_loop >= demoted.game_loop]
    assert sorted(_actor_of(e) for e in resumed[:4]) == list(ZEALOTS)  # the attack resumes
    assert world.bodies[marine].durability_lost() == 0  # still there, never hurt


# ---------------------------------------------------------------------------
# After the enemy start: structures, memory and search (D3)
# ---------------------------------------------------------------------------

_SOUTH_BARRACKS, _WEST_BARRACKS = (104.0, 92.5), (90.0, 101.5)  # 8.1 off the march route


def test_scenario_after_the_enemy_start_the_army_takes_visible_then_remembered_structures() -> None:
    """D3: after the start, visible structures, then remembered ones (revalidated on
    sight), then the search; never a structure only hidden-map information shows."""
    command_center = _enemy(7000, "CommandCenter", ENEMY_START)
    depot = _enemy(7001, "SupplyDepot", (126.5, 118.5))
    south = _enemy(7002, "Barracks", _SOUTH_BARRACKS)
    west = _enemy(7003, "Barracks", _WEST_BARRACKS)
    hidden = _enemy(7004, "CommandCenter", HIDDEN_SITE)
    world = _base(at=(60.0, 60.0), extra=(command_center, depot, south, west, hidden))
    removed: list[int] = []

    def salvage_south_unseen(world: _World, controller: JevController, index: int) -> None:
        body = world.bodies.get(south.tag)
        if body is not None and south.tag in world.ever_seen and not world.sees(body.position):
            removed.append(south.tag)
            world.kill(south.tag)  # gone while out of sight: memory still holds it

    run = _play(world, 80, salvage_south_unseen)
    assert removed == [south.tag]
    assert _target_sequence(run)[:6] == [
        ENEMY_START,  # passed both Barracks in sight on the way, never diverted
        depot.tag,  # cleared (its Command Center razed): visible structures next
        _SOUTH_BARRACKS,  # then remembered ones, attack-moved to where they were seen
        _WEST_BARRACKS,  # the south spot came into sight empty: dropped from memory
        west.tag,  # in sight again: attacked as a visible structure
        ENEMY_NATURAL,  # nothing left to see or remember: the search begins
    ]
    assert {south.tag, west.tag} <= world.ever_seen
    army_commands = [e for e in run.events if e.kind == "command" and e.node_id.startswith("army")]
    hidden_targets = [e for e in army_commands if _target_of(e) == hidden.tag]
    assert hidden.tag not in world.ever_seen and hidden_targets == []
    assert world.bodies.keys().isdisjoint({command_center.tag, depot.tag, west.tag})


def test_scenario_the_army_holds_the_enemy_start_until_its_defenders_are_dead() -> None:
    """D3: arriving is not clearing: the army attacks the start until no enemy holds it."""
    holders = (
        _enemy(7300, "Marine", ENEMY_START, health=60.0),
        _enemy(7301, "Marine", (121.5, 119.0), health=60.0),
    )
    world = _base(at=(60.0, 60.0), extra=holders)
    world.bodies[ZEALOTS[0]].position = (100.0, 100.0)  # the first to arrive, alone
    arrived: list[int] = []
    cleared: list[int] = []

    def watch(world: _World, controller: JevController, index: int) -> None:
        alive = any(h.tag in world.bodies for h in holders)
        first = world.bodies[ZEALOTS[0]]
        if alive and not arrived and distance(first.position, ENEMY_START) <= 4.0:
            arrived.append(world.state.game_loop)
        if not alive and not cleared:
            cleared.append(world.state.game_loop)

    run = _play(world, 60, watch)
    (arrived_loop,), (cleared_loop,) = arrived, cleared
    assert arrived_loop < cleared_loop  # one Zealot stood there while the Marines lived
    commands = _commands(run, "army.attack.go")
    holding = {_target_of(e) for e in commands if e.game_loop < cleared_loop}
    assert holding == {ENEMY_START}  # nobody was sent on while the start was held
    cancelled = [e for e in run.events if e.kind == "task" and e.status == "cancelled"]
    assert [e for e in cancelled if e.game_loop < cleared_loop] == []  # all kept attacking
    onward = [e for e in commands if e.game_loop >= cleared_loop]
    assert _target_of(onward[0]) == ENEMY_NATURAL  # cleared: the search begins


def test_scenario_search_cycles_expansion_locations_in_distance_order_from_the_enemy_start() -> (
    None
):
    """D3: after the enemy start, cycle expansion locations in distance order."""
    run = _play(_base(), 170)
    assert _target_sequence(run)[:7] == [
        ENEMY_START,  # the first attack; empty, so nothing to see or remember there
        ENEMY_NATURAL,  # never-visited locations by distance from the enemy start ...
        THIRD,
        OWN_NATURAL,
        START,
        ENEMY_START,  # ... then the one visited longest ago: the cycle wraps around
        ENEMY_NATURAL,
    ]
    arrived = min(
        e.game_loop
        for e in run.events
        if e.kind == "task" and e.status == "succeeded" and e.node_id == "army.attack.go"
    )
    onward = min(e.game_loop for e in _commands(run, "army.attack.go") if e.game_loop > 0)
    assert onward == arrived  # the arrival itself moves the search on, on the same tick


@pytest.mark.parametrize(
    ("obstacle", "cause", "within"),
    [
        ("plateau", "no_progress", 45.0),  # stalled at its edge: a 10 s check, 30 s, replan
        ("unwalkable", "rejected", 5.0),  # SC2 refuses every order: four tries 1 s apart
        ("ignored", "unacknowledged", 25.0),  # SC2 drops every order: four 5 s waits
    ],
)
def test_scenario_search_moves_past_an_expansion_it_cannot_reach(
    obstacle: str, cause: str, within: float
) -> None:
    """D3/D4: an expansion the army cannot reach is given up and the search moves on:
    a march stalled short of it replans, and orders SC2 refuses or drops spend their
    retries; either way the give-up counts for the target (no endless re-selection)."""
    world = _base()
    spots = {"plateau": world.plateaus, "unwalkable": world.unwalkable, "ignored": world.ignored}
    spots[obstacle].append((ENEMY_NATURAL, 8.0))  # no ramp / not walkable / never obeyed
    run = _play(world, 160)
    assert _target_sequence(run)[:3] == [ENEMY_START, ENEMY_NATURAL, THIRD]
    commands = _commands(run, "army.attack.go")
    tried = min(e.game_seconds for e in commands if _target_of(e) == ENEMY_NATURAL)
    moved_on = min(e.game_seconds for e in commands if _target_of(e) == THIRD)
    assert moved_on - tried < within  # in bounded time
    gave_up = [e for e in _failed(run, "army.attack.go") if e.game_seconds <= moved_on]
    assert {e.facts["failure_cause"] for e in gave_up} == {cause}


def test_scenario_an_army_already_at_a_clear_enemy_start_searches_on() -> None:
    """D3: an army already at a clear enemy start counts it visited and searches on."""
    world = _base(at=ENEMY_START)
    run = _play(world, 8)
    commands = _commands(run, "army.attack.go")
    onward = [e for e in commands if _target_of(e) == ENEMY_NATURAL]
    assert sorted(_actor_of(e) for e in onward) == list(ZEALOTS)
    assert ENEMY_START not in {_target_of(e) for e in commands}  # straight to the search


# ---------------------------------------------------------------------------
# Movement progress (D4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "progress"),
    [("marching", True), ("hurting-health", True), ("hurting-shields", True), ("unhurt", False)],
)
def test_scenario_progress_is_closing_in_on_the_target_or_hurting_the_enemy(
    case: str, progress: bool
) -> None:
    """D4: army movement is checked for progress: closing in on the target, or a fight on
    the way that costs the enemy health or shields, keeps a task past the 30 s replan
    window; standing by an enemy it cannot hurt is no progress, so the task replans."""
    target: _Body | None = None
    if case == "hurting-shields":  # a Protoss target: damage lands on its shields first
        target = _Body(7100, "PhotonCannon", (60.0, 60.0), Alliance.Enemy, health=150.0, shield=1e9)
    elif case != "marching":
        target = _enemy(7100, "Bunker", (60.0, 60.0), health=1e9)  # an endless fight en route
    world = _base(at=(40.0, 40.0), extra=() if target is None else (target,))
    if case == "marching":
        world.speed = 1.5  # never there in this run, closing in all the way
    if case == "unhurt":
        world.untouchable.add(7100)  # beside it, across a cliff edge: no damage
    run = _play(world, 125)  # 44.6 game seconds
    gave_up = _failed(run, "army.attack.go")
    if progress:
        assert gave_up == [] and len(_commands(run, "army.attack.go")) == 4  # one order each
    else:
        assert len(gave_up) == 4 and {e.facts["failure_cause"] for e in gave_up} == {"no_progress"}
    lost = 0.0 if target is None else world.bodies[target.tag].durability_lost()
    assert (lost > 0) is case.startswith("hurting")  # what the fight did to the enemy
    if case == "hurting-shields":
        assert world.bodies[7100].health == 150.0  # only its shields went down


def test_scenario_an_enemy_slipping_in_and_out_of_reach_unhurt_is_no_progress() -> None:
    """D4: an enemy leaving or re-entering reach unhurt is no fight progress: replan at 30 s."""
    world = _base(at=(70.0, 70.0))
    world.frozen = True  # the Zealots stand still: only the fight could be progress
    marine = world.add("Marine", (72.0, 72.0), enemy=True)
    world.untouchable.add(marine)

    def slips_away_every_ten_seconds(world: _World, controller: JevController, index: int) -> None:
        near = (index // 28) % 2 == 0  # 28 steps = 10 game seconds in reach, then away
        world.bodies[marine].position = (72.0, 72.0) if near else (120.0, 20.0)
        world.refresh()

    run = _play(world, 100, slips_away_every_ten_seconds)
    failed = _failed(run, "army.attack.go")
    assert len(failed) == 4 and {e.facts["failure_cause"] for e in failed} == {"no_progress"}
    assert world.bodies[marine].durability_lost() == 0  # never hurt: it only came and went


def test_scenario_a_stuck_attack_replans_after_thirty_seconds_and_searches_on() -> None:
    """D4: no progress for 30 game seconds replans; the unreachable start is given up."""
    world = _base()
    world.frozen = True  # orders are accepted, nobody moves
    run = _play(world, 100)
    attack_tasks = [e for e in run.events if e.kind == "task" and e.node_id == "army.attack.go"]
    acknowledged = {e.task_id: e.game_seconds for e in attack_tasks if e.status == "running"}
    failures = [e for e in attack_tasks if e.status == "failed"]
    assert len(failures) == 4
    assert {e.facts["failure_cause"] for e in failures} == {"no_progress"}
    for event in failures:  # progress checked at +10 s and +20 s; replanned at +30 s
        stalled = event.game_seconds - acknowledged[str(event.task_id)]
        assert 30.0 - _EPSILON <= stalled < 30.0 + STEP_SECONDS
    replan_loop = failures[0].game_loop
    onward = [e for e in _commands(run, "army.attack.go") if e.game_loop >= replan_loop]
    assert {e.game_loop for e in onward} == {replan_loop}  # the same tick, no cooldown wait
    assert {_target_of(e) for e in onward} == {ENEMY_NATURAL}  # searching on
    assert sorted(_actor_of(e) for e in onward) == list(ZEALOTS)


def test_scenario_a_latched_army_holds_through_a_cooldown_instead_of_rallying_home() -> None:
    """D3/D4: after the latch a replan cooldown holds the army; it never walks home."""
    barracks = _enemy(7002, "Barracks", _SOUTH_BARRACKS)  # glimpsed on the march, then fogged
    world = _base(extra=(barracks,))
    world.plateaus.append((_SOUTH_BARRACKS, 12.0))  # beyond sight from the edge: stays remembered
    run = _play(world, 190)
    failures = _failed(run, "army.attack.go")
    assert len(failures) == 4 and {e.facts["failure_cause"] for e in failures} == {"no_progress"}
    gave_up = failures[0].game_seconds
    cooldown_end = gave_up + 10.0 - _EPSILON
    held = [c for loop, c in world.sent if gave_up <= loop / GAME_LOOPS_PER_SECOND < cooldown_end]
    assert held == []  # the retried memory is cooling down: the army holds, sends nothing
    assert not [c for _, c in world.sent if c.ability == AbilityId.MOVE_MOVE]  # never home
    again = [e for e in _commands(run, "army.attack.go") if e.game_seconds > gave_up]
    assert {_target_of(e) for e in again} == {_SOUTH_BARRACKS}  # then it attacks again
    assert sorted(_actor_of(e) for e in again) == list(ZEALOTS)
    assert all(e.game_seconds >= cooldown_end for e in again)
    assert {e.task_id for e in again}.isdisjoint(e.task_id for e in failures)


_LEDGE, _HILL = (124.5, 113.5), (97.1, 74.4)  # plateau sites: in sight / beyond sight
_MOVED_TO = (116.0, 112.5)  # off the ledge, in sight of the army as it leaves


@pytest.mark.parametrize(
    ("variant", "reason"),
    [("in-sight", "sighted_again"), ("remembered", "search_cycle"), ("moved", "moved")],
)
def test_scenario_an_unreachable_structure_is_demoted_then_allowed_again(
    variant: str, reason: str
) -> None:
    """D4: an unreachable structure is demoted after bounded give-ups, then allowed again."""
    hooks: list[_Hook] = []
    if variant == "remembered":  # glimpsed on the march from the natural, never in sight again
        structure = _enemy(7002, "Barracks", _HILL)
        world = _base(at=OWN_NATURAL, extra=(structure,))
        world.plateaus.append((_HILL, 12.0))
        steps = 360
    else:  # on a ledge in sight of the enemy start
        structure = _enemy(7001, "SupplyDepot", _LEDGE)
        world = _base(extra=(structure,))
        world.plateaus.append((_LEDGE, 5.0))
        steps = 330
    if variant == "moved":

        def lifts_off_once_demoted(world: _World, controller: JevController, index: int) -> None:
            body = world.bodies.get(structure.tag)
            demoted = any(e.facts.get("demoted") is True for e in controller.recent_events)
            if body is not None and body.position == _LEDGE and demoted:
                body.position = _MOVED_TO  # lifted and landed within reach
                world.refresh()

        hooks.append(lifts_off_once_demoted)
    run = _play(world, steps, *hooks)
    demoted = _demoted_then_searching_on(run, structure)
    (allowed,) = [e for e in run.events if "allowed_again" in e.facts]
    assert allowed.facts == {"target": str(structure.tag), "allowed_again": reason}
    assert allowed.game_loop > demoted.game_loop
    places = (structure.position, _MOVED_TO)  # anywhere it stood (SC2 points are float32)

    def is_the_structure(target: int | _Point) -> bool:
        if isinstance(target, int):
            return target == structure.tag
        return min(distance(target, place) for place in places) < 0.01

    again = [
        e
        for e in _commands(run, "army.attack.go")
        if e.game_loop >= allowed.game_loop and is_the_structure(_target_of(e))
    ]
    assert sorted(_actor_of(e) for e in again[:4]) == list(ZEALOTS)  # eligible again


def test_scenario_staggered_attackers_cannot_keep_an_unreachable_target_alive() -> None:
    """D4: give-ups count per target, so a staggered reinforcement cannot stall a demotion."""
    depot = _enemy(7001, "SupplyDepot", _LEDGE)
    world = _base(extra=(depot,))
    world.plateaus.append((_LEDGE, 5.0))
    reinforcement: list[int] = []

    def reinforcement_joins_mid_cycle(world: _World, controller: JevController, index: int) -> None:
        if index == 42:  # 15 game seconds in: its 30 s windows overlap the first group's
            reinforcement.append(world.add("Zealot", (28.5, 32.5)))

    run = _play(world, 360, reinforcement_joins_mid_cycle)
    (late,) = reinforcement
    demoted = _demoted_then_searching_on(run, depot)
    failed = [e for e in _failed(run, "army.attack.go") if e.game_loop <= demoted.game_loop]
    actors = {int(str(e.facts["actor_tag"])) for e in failed}
    assert {late, ZEALOTS[0]} <= actors  # both groups gave up, on staggered clocks


def test_scenario_one_stuck_attacker_never_abandons_or_demotes_a_target_the_army_is_hurting() -> (
    None
):
    """D4: a stuck attacker's give-ups never move the army off a target the others hurt."""
    bunker = _enemy(7100, "Bunker", ENEMY_START, health=2400.0)  # holds the start ~40 s
    depot = _enemy(7001, "SupplyDepot", _LEDGE, health=6000.0)  # reachable, a long fight
    hidden = _enemy(7004, "CommandCenter", HIDDEN_SITE)  # keeps the game going after them
    world = _base(extra=(bunker, depot, hidden))
    world.stuck.add(ZEALOTS[3])  # accepts its orders, never moves
    razed: list[int] = []

    def watch(world: _World, controller: JevController, index: int) -> None:
        if bunker.tag not in world.bodies and not razed:
            razed.append(world.state.game_loop)

    run = _play(world, 440, watch)
    (razed_loop,) = razed
    stuck = [
        e.game_loop
        for e in _failed(run, "army.attack.go")
        if e.facts["actor_tag"] == str(ZEALOTS[3]) and e.facts["failure_cause"] == "no_progress"
    ]
    assert min(stuck) < razed_loop and max(stuck) > razed_loop  # it gave up on both targets
    commands = _commands(run, "army.attack.go")
    assert {_target_of(e) for e in commands if e.game_loop < razed_loop} == {ENEMY_START}
    after = [e for e in commands if e.game_loop >= razed_loop]
    assert _target_of(after[0]) == depot.tag  # its give-ups never moved the army off the start
    assert [e for e in run.events if e.facts.get("demoted") is True] == []  # never demoted
    assert depot.tag not in world.bodies  # the others razed it


@pytest.mark.parametrize(
    ("interlude", "rounds_after"),
    [("out-of-sight", 1), ("called-home", 1), ("defended", 1), ("hurt", 2)],
)
def test_scenario_give_up_rounds_add_up_to_a_demotion_until_damage_breaks_the_chain(
    interlude: str, rounds_after: int
) -> None:
    """D4: give-up rounds against a structure add up across an interlude after the first
    one -- a spell out of sight, a trip home to defend and the march back (closing in
    again is no break), or a fight with defenders beside it while it is hidden (hurting
    other enemies is no break) -- so the next stall demotes it. Damage dealt to the
    structure itself breaks the chain: it takes two fresh rounds."""
    depot = _enemy(7001, "SupplyDepot", _LEDGE, health=6000.0)
    world = _base(extra=(depot,))
    world.untouchable.add(depot.tag)  # beside it, across a cliff edge: no damage
    started: list[int] = []
    defenders: list[int] = []
    spell = round(25.0 * GAME_LOOPS_PER_SECOND)  # the hurt interlude: 25 game seconds

    def interlude_after_the_first_give_up(
        world: _World, controller: JevController, index: int
    ) -> None:
        if started:
            over = (
                interlude == "out-of-sight"  # one observation out of sight, then in view
                or (interlude == "defended" and not world.bodies.keys() & set(defenders))
                or (interlude == "hurt" and world.state.game_loop - started[0] >= spell)
            )
            if over:
                world.blind_spots.clear()  # back in view
                world.untouchable.add(depot.tag)  # unhurt from here on
                world.refresh()
            return
        if not any(e.facts.get("failure_cause") == "no_progress" for e in controller.recent_events):
            return
        started.append(world.state.game_loop)
        if interlude in ("out-of-sight", "defended"):
            world.blind_spots.append((_LEDGE, 1.0))  # hidden: only memory of it is left
        if interlude == "called-home":
            world.add("Marine", _NEAR, enemy=True, health=30.0)  # an intruder at the main
        elif interlude == "defended":  # beside the remembered spot, in view and hurtable
            spots = ((_LEDGE[0] - 2.5, _LEDGE[1]), (_LEDGE[0], _LEDGE[1] - 2.5))
            defenders.extend(world.add("Marine", s, enemy=True, health=700.0) for s in spots)
        elif interlude == "hurt":
            world.untouchable.discard(depot.tag)  # within reach of harm for the spell
        world.refresh()

    run = _play(world, 450, interlude_after_the_first_give_up)
    (start,) = started
    stalls = sorted(
        {
            e.game_loop
            for e in _failed(run, "army.attack.go")
            if e.facts["failure_cause"] == "no_progress" and e.game_loop > start
        }
    )
    demoted = _demoted_then_searching_on(run, depot)
    assert demoted.game_loop == stalls[rounds_after - 1]  # the chain held, or restarted
    lost = world.bodies[depot.tag].durability_lost()
    assert (lost > 0) is (interlude == "hurt")  # only the hurt spell touched the structure
    if interlude == "out-of-sight":
        chosen = {e.node_id for e in run.events if e.game_loop == start and e.status == "success"}
        assert "army.attack.target.remembered" in chosen  # out of sight: only memory left
    elif interlude == "called-home":
        assert min(e.game_loop for e in _commands(run, "army.defend.attack")) >= start
    elif interlude == "defended":
        assert world.bodies.keys().isdisjoint(defenders)  # the defenders were killed
        fought = [e for e in _commands(run, "army.attack.go") if e.game_loop >= start]
        assert _target_of(fought[0]) == _LEDGE  # attack-moved to where it was remembered


# ---------------------------------------------------------------------------
# Terminal results, limits and economy loss (D3/D6)
# ---------------------------------------------------------------------------


def test_cli_game_time_limit_cuts_a_running_rush_short(tmp_path: Path) -> None:
    """The --max-game-seconds limit stops a rush in progress: the bot only leaves."""
    world = _base()
    launcher = _Launcher(world, 200)
    argv = ["--max-game-seconds", "20", "--run-root", str(tmp_path)]
    runner.main(argv, load_policy=load_policy, prog="jev-test", launcher=launcher)
    assert 20.0 <= world.seconds < 20.0 + STEP_SECONDS  # left on the first step past it
    first_wave = sorted((a, t) for loop, a, t in _sent_attacks(world) if loop == 0)
    assert first_wave == [(tag, ENEMY_START) for tag in ZEALOTS]  # the rush was under way
    assert world.left and world.leave_calls == 1
    assert max(loop for loop, _ in world.sent) < world.state.game_loop  # then it only left


def test_razing_the_last_enemy_structure_lets_sc2_end_the_match_without_a_leave() -> None:
    """The attack razes the last enemy structure: SC2's Victory ends the match, no leave."""
    world = _base(at=(100.0, 100.0), extra=(_enemy(7000, "CommandCenter", ENEMY_START),))
    _play(world, 60)
    assert world.result == Result.Victory  # the rush razed it
    assert not world.left and world.leave_calls == 0  # SC2 ended it: the bot never left


def test_scenario_without_a_nexus_only_zealot_production_carries_on() -> None:
    """D3: no Nexus: no probes, Gateway or power rebuilds; surviving Gateways train Zealots."""
    gateways = (
        _Body(1200, "Gateway", GATEWAY_SITES[0]),
        _Body(1201, "Gateway", GATEWAY_SITES[1]),
        _Body(1202, "Gateway", (40.0, 40.0)),  # out of Pylon power: would be re-powered
    )  # three of four: a fourth would be rebuilt
    probes = [_Body(tag, "Probe", (28.0, 26.0)) for tag in PROBES]  # idle
    zealots = [_Body(tag, "Zealot", (36.0, 40.0), health=150.0) for tag in ZEALOTS]
    world = _World([_Body(PYLON, "Pylon", (24.0, 34.0)), *gateways, *probes, *zealots])
    world.minerals = 1000
    world.refresh()
    _play(world, 20)
    sent = {c.ability for _, c in world.sent}
    builds = {AbilityId.PROTOSSBUILD_PYLON, AbilityId.PROTOSSBUILD_GATEWAY}
    assert sent.isdisjoint(builds | {AbilityId.NEXUSTRAIN_PROBE, AbilityId.HARVEST_GATHER})
    trained = {c.unit.tag for _, c in world.sent if c.ability == AbilityId.GATEWAYTRAIN_ZEALOT}
    assert trained == {1200, 1201}  # the powered Gateways keep training Zealots


def test_scenario_a_destroyed_nexus_ends_economy_recovery_while_the_army_fights_on() -> None:
    """D3: a destroyed Nexus ends economy recovery; the surviving army searches on."""
    world = _base()
    for tag in PROBES:
        world.bodies[tag].orders = []  # idle probes: the economy sends them mining
    world.refresh()
    loss_step = 3

    def nexus_destroyed(world: _World, controller: JevController, index: int) -> None:
        if index == loss_step:
            world.kill(NEXUS)
            for tag in PROBES:
                world.bodies[tag].orders = []  # nothing to return cargo to
            world.refresh()

    run = _play(world, 200, nexus_destroyed, options=MatchOptions(max_game_seconds=30))
    gathers = [loop for loop, c in world.sent if c.ability == AbilityId.HARVEST_GATHER]
    assert min(gathers) == 0 and max(gathers) < _loop_of(loss_step)  # stopped at the loss
    guard = [(e.game_loop, e.status) for e in run.events if e.node_id == "economy.nexus"]
    assert {status for loop, status in guard if loop < _loop_of(loss_step)} == {"success"}
    assert {status for loop, status in guard if loop >= _loop_of(loss_step)} == {"failure"}
    later = [t for loop, _, t in _sent_attacks(world) if loop > _loop_of(loss_step)]
    assert ENEMY_NATURAL in later  # the surviving army searched on after the loss


@pytest.mark.parametrize(
    ("failures", "calls", "left"),
    [(1, 2, True), (99, MAX_LEAVE_ATTEMPTS, False)],
    ids=["retried-then-left", "never-left"],
)
def test_a_failed_leave_is_retried_then_bounded(failures: int, calls: int, left: bool) -> None:
    """A leave that fails is retried each step, at most MAX_LEAVE_ATTEMPTS times."""
    world = _base()
    world.leave_failures = failures
    run = _play(world, 60, options=MatchOptions(max_game_seconds=5))
    assert (world.leave_calls, world.left) == (calls, left)
    reported = "leaving the game failed" in run.outcome.message
    assert reported is not left  # the last failure is reported while never left


# ---------------------------------------------------------------------------
# Jev v2 (Phase J2 Step 214): grouped reinforcement and recovery
# ---------------------------------------------------------------------------

#: The rally point: rally_distance (8) from the main toward the map centre.
_RALLY = (36.16, 36.16)
#: Where a Gateway near the main spawns its Zealot (within the home radius).
_SPAWN = (28.5, 32.5)


def _attacks_by_actor(world: _World) -> dict[int, list[tuple[int, int | _Point]]]:
    """Every ATTACK SC2 received, per actor: ``(game loop, target)`` in order."""
    found: dict[int, list[tuple[int, int | _Point]]] = {}
    for loop, actor, target in _sent_attacks(world):
        found.setdefault(actor, []).append((loop, target))
    return found


def _moves_to(world: _World, actor: int) -> list[tuple[int, _Point]]:
    return [
        (loop, (c.target.x, c.target.y))
        for loop, c in world.sent
        if c.unit.tag == actor and c.ability == AbilityId.MOVE_MOVE
    ]


def test_v2_scenario_four_ready_zealots_launch_together_before_every_gateway_completes() -> None:
    """D3 kept by v2: the first attack latches at four ready Zealots and all four set
    out on one tick, while Gateways are still warping in; before it, they rally."""
    pylon = _Body(PYLON, "Pylon", (24.0, 34.0))
    gateways = [
        _Body(1200, "Gateway", GATEWAY_SITES[0], orders=[_training(0.9)]),
        _Body(1201, "Gateway", GATEWAY_SITES[1], orders=[_training(0.85)]),
        _Body(1202, "Gateway", GATEWAY_SITES[2], progress=0.3),
        _Body(1203, "Gateway", GATEWAY_SITES[3], progress=0.3),
    ]
    zealots = [_Body(tag, "Zealot", (28.0, 40.0), health=150.0) for tag in ZEALOTS[:2]]
    probes = [_Body(tag, "Probe", (28.0, 26.0), orders=[_gather(3000)]) for tag in PROBES]
    patch = _Body(3000, "MineralField", (26.5, 22.5), alliance=Alliance.Neutral)
    world = _World([_Body(NEXUS, "Nexus", START), pylon, *gateways, *zealots, *probes, patch])
    census: dict[int, tuple[int, list[float]]] = {}

    def count(world: _World, controller: JevController, index: int) -> None:
        progress = [world.bodies[1200 + i].progress for i in range(4)]
        census[world.state.game_loop] = (len(world.own("Zealot")), progress)

    run = _play(world, 30, count, bundle=load_v2_policy())
    attacks = _sent_attacks(world)
    launch_loop = attacks[0][0]
    zealots_then, gateway_progress = census[launch_loop]
    assert zealots_then == 4 and min(gateway_progress) < 1.0
    launch = [(actor, target) for loop, actor, target in attacks if loop == launch_loop]
    assert sorted(actor for actor, _ in launch) == sorted(z.tag for z in world.own("Zealot"))
    assert {target for _, target in launch} == {ENEMY_START}
    latched = [e for e in run.events if e.node_id == "army.attack.launch.first_wave.latch"]
    assert latched[0].game_loop <= launch_loop and len({e.game_loop for e in latched}) == 1
    early = [c for loop, c in world.sent if loop < launch_loop]
    assert early and {c.ability for c in early} == {AbilityId.MOVE_MOVE}  # rallied first


def test_v2_scenario_reinforcements_gather_at_home_and_join_only_as_a_group() -> None:
    """D3: after the launch, new Zealots gather at the home rally point instead of
    walking across the map one by one; four gathered join the group's attack together."""
    world = _base()
    arrivals: list[int] = []

    def trained_at_home(world: _World, controller: JevController, index: int) -> None:
        if index in (2, 4, 6, 8):
            arrivals.append(world.add("Zealot", _SPAWN))

    run = _play(world, 14, trained_at_home, bundle=load_v2_policy())
    attacks = _attacks_by_actor(world)
    assert {attacks[tag][0] for tag in ZEALOTS} == {(_loop_of(0), ENEMY_START)}  # the launch
    joined = {attacks[tag][0][0] for tag in arrivals}
    assert joined == {_loop_of(8)}  # all four reserves set out on the fourth's tick ...
    assert {attacks[tag][0][1] for tag in arrivals} == {ENEMY_START}  # ... to the group's target
    for tag in arrivals[:3]:  # ... after gathering at home, never alone across the map
        assert {point for _, point in _moves_to(world, tag)} == {_RALLY}
        assert _moves_to(world, tag)[0][0] < _loop_of(8)
    assert {_actor_of(e) for e in _commands(run, "reserve.gather")} == set(arrivals[:3])
    assert all(not _moves_to(world, tag) for tag in ZEALOTS)  # the group was never recalled
    group = run.controller.runtime.assigned_to("army.attack.go")
    assert group == {*ZEALOTS, *arrivals}  # one group now


def test_v2_scenario_a_depleted_army_regroups_at_home_then_relaunches_as_one_group() -> None:
    """D2/D3: after the launch, fewer than four ready Zealots regroup at home (the
    attack's tasks are cancelled); the next full group of four sets out together."""
    world = _base()
    arrivals: list[int] = []

    def losses_then_reinforcements(world: _World, controller: JevController, index: int) -> None:
        if index == 3:
            world.kill(ZEALOTS[1])
            world.kill(ZEALOTS[2])  # two remain: below the regroup size
        if index == 6:
            arrivals.extend(world.add("Zealot", _SPAWN) for _ in range(2))

    run = _play(world, 24, losses_then_reinforcements, bundle=load_v2_policy())
    survivors = (ZEALOTS[0], ZEALOTS[3])
    recalled = _commands(run, "army.recover.move")
    assert {_actor_of(e) for e in recalled} == set(survivors)
    assert {e.game_loop for e in recalled} == {_loop_of(3)}  # at once, on the first tick
    cancelled = [
        e
        for e in run.events
        if e.kind == "task" and e.status == "cancelled" and e.node_id == "army.attack.go"
    ]
    assert {e.reason for e in cancelled} == {"preempted by army.recover.move"}
    assert {str(e.facts["actor_tag"]) for e in cancelled} == {str(t) for t in survivors}
    attacks = _attacks_by_actor(world)
    relaunch = {attacks[tag][-1][0] for tag in (*survivors, *arrivals)}
    assert len(relaunch) == 1  # one group of four, on one tick
    (relaunch_loop,) = relaunch
    home = [e for e in run.events if e.kind == "task" and e.node_id == "army.recover.move"]
    arrived = [e.game_loop for e in home if e.status == "succeeded"]
    assert len(arrived) == 2 and max(arrived) <= relaunch_loop  # only after both got home
    between = [loop for loop, _, _ in _sent_attacks(world) if _loop_of(3) <= loop < relaunch_loop]
    assert between == []  # nothing trickled out while regrouping


def test_v2_assignment_memory_is_kept_only_for_a_policy_that_reads_it() -> None:
    """The v2 group is the attack's assignment; v1 never records assignments."""
    v1 = _play(_base(), 3)
    v2 = _play(_base(), 3, bundle=load_v2_policy())
    assert v1.controller.runtime.assigned_to("army.attack.go") == frozenset()
    assert v2.controller.runtime.assigned_to("army.attack.go") == set(ZEALOTS)


# ---------------------------------------------------------------------------
# Step 218: a mixed army -- capability-aware targets (Stalkers hit air, Zealots never)
# ---------------------------------------------------------------------------

STALKERS = (6100, 6101)
_AIR_RAID = (33.0, 35.0)  # in sight of the main, inside its defense radius
_AIR_DEFENSE = "army.defend.air.attack"


def _mixed(*extra: _Body) -> _World:
    """``_base``'s four Zealots plus two Stalkers at home (the world's weapon data
    gives the Stalker ground and air targets, the Zealot ground only)."""
    stalkers = [_Body(tag, "Stalker", (34.0, 38.0), health=80.0, shield=80.0) for tag in STALKERS]
    return _base(extra=(*stalkers, *extra))


def _raider(tag: int = 900_100, at: _Point = _AIR_RAID) -> _Body:
    return _Body(tag, "Banshee", at, Alliance.Enemy, health=60.0, flying=True)


def _attacks_on(world: _World, tag: int) -> list[tuple[int, int]]:
    """``(game loop, actor)`` of every ATTACK SC2 received on unit ``tag``."""
    return [(loop, actor) for loop, actor, target in _sent_attacks(world) if target == tag]


def test_v2_mixed_scenario_stalkers_shoot_down_an_air_raider_and_zealots_never_take_it() -> None:
    world = _mixed()

    def raid(world: _World, controller: JevController, index: int) -> None:
        if index == 3:
            world.bodies[900_100] = _raider()
            world.refresh()

    run = _play(world, 40, raid, bundle=load_v2_policy())
    on_raider = _attacks_on(world, 900_100)
    assert on_raider and {actor for _, actor in on_raider} == set(STALKERS)
    assert min(loop for loop, _ in on_raider) == _loop_of(3)  # at once, the tick it shows
    assert 900_100 not in world.bodies  # shot down
    defense = _commands(run, _AIR_DEFENSE)
    assert defense and {_actor_of(e) for e in defense} == set(STALKERS)  # never a Zealot
    selections = [
        e for e in run.events if e.node_id == "army.defend.air.units" and e.kind == "node"
    ]
    assert selections and all(
        set(e.facts["tags"]) == {str(t) for t in STALKERS} for e in selections
    )
    # The Zealots were never pulled onto it: they kept the attack they were given.
    assert all(actor not in ZEALOTS for _, actor in on_raider)
    assert world.unexpected == []  # SC2 never saw a Zealot ordered onto an air unit


def test_v2_mixed_scenario_ground_threats_take_every_unit_then_stalkers_take_the_air() -> None:
    marine = _enemy(900_200, "Marine", (34.0, 36.0), health=45.0)
    world = _mixed(marine, _raider(at=(36.0, 34.0)))
    run = _play(world, 40, bundle=load_v2_policy())
    on_marine = _attacks_on(world, 900_200)
    assert {actor for _, actor in on_marine} == {*ZEALOTS, *STALKERS}  # every combat unit
    assert 900_200 not in world.bodies
    marine_dead = max(loop for loop, _ in on_marine)
    on_raider = _attacks_on(world, 900_100)
    assert on_raider and {actor for _, actor in on_raider} == set(STALKERS)
    assert min(loop for loop, _ in on_raider) > marine_dead  # ground first, then air
    first = [e for e in run.events if e.kind == "node" and e.node_id == "army.defend.engage"]
    assert first and first[0].facts["child"] == "army.defend.ground"


def test_without_the_capability_filter_the_attack_itself_refuses_zealots_an_air_target(
    tmp_path: Path,
) -> None:
    """Calibration of both layers: with ``can_attack_air`` removed from the anti-air
    selection, the Zealots are selected too -- and the attack operation still never
    assigns them the air target (blocked, with a reason), so SC2 never sees one."""
    document = json.loads(load_v2_policy().policy_bytes)
    for node in document["nodes"]:
        if node["id"] == "army.defend.air.units":
            del node["args"]["filter"]["can_attack_air"]
    candidate = tmp_path / "policy.json"
    candidate.write_text(json.dumps(document), encoding="utf-8")
    world = _mixed(_raider())
    run = _play(world, 6, bundle=load_v2_policy(candidate))
    selected = [e for e in run.events if e.node_id == "army.defend.air.units" and e.kind == "node"]
    assert set(selected[0].facts["tags"]) == {str(t) for t in (*ZEALOTS, *STALKERS)}
    attacks = [e for e in run.events if e.node_id == _AIR_DEFENSE and e.kind == "node"]
    assert attacks[0].facts["cannot_attack_air"] == len(ZEALOTS)
    assert {_actor_of(e) for e in _commands(run, _AIR_DEFENSE)} == set(STALKERS)
    assert world.unexpected == []
    # The detector is live: SC2 (this world) does refuse a Zealot ordered onto air.
    world.bodies[900_101] = _raider(900_101)  # the first raider was shot down
    world.refresh()
    zealot = next(u for u in world.units if u.tag == ZEALOTS[0])
    raider = next(u for u in world.enemy_units if u.tag == 900_101)
    assert world.receive(UnitCommand(AbilityId.ATTACK, zealot, raider)) == (
        ActionResult.CantTargetAirUnits
    )
    assert world.unexpected and "onto air" in world.unexpected[0]


def test_the_adapter_reads_weapon_targets_from_the_game_data() -> None:
    from jev.sc2_adapter import Sc2Adapter

    world = _mixed(_raider())
    observation = Sc2Adapter().observe(world, BotAIPort(world))  # type: ignore[arg-type]
    own = {e.type_name: e for e in observation.own_units}
    assert (own["Stalker"].can_attack_ground, own["Stalker"].can_attack_air) == (True, True)
    assert (own["Zealot"].can_attack_ground, own["Zealot"].can_attack_air) == (True, False)
    (raider,) = observation.visible_enemies
    assert raider.is_flying and (raider.can_attack_ground, raider.can_attack_air) == (True, False)
    nexus = next(e for e in observation.own_structures if e.type_name == "Nexus")
    assert (nexus.can_attack_ground, nexus.can_attack_air) == (False, False)  # unarmed


# ---------------------------------------------------------------------------
# Legacy isolation: Jev's gameplay call graph
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_SOURCE_ROOTS = (_REPO / "src", _REPO)
_JEV_DIRS = (_REPO / "src" / "jev", _REPO / "bots" / "jev")
#: The one first-party module outside Jev that Jev may import: the SC2 install
#: path resolver used by the runner's preflight.
_ALLOWED_FIRST_PARTY = frozenset({"orchestrator.paths"})
#: The dashboard's read-only router: the one Jev module off the gameplay call graph,
#: and the only one that may import the web framework (the dashboard's own).
_DASHBOARD_MODULES = frozenset({"jev.api"})
_DASHBOARD_EXTERNALS = frozenset({"fastapi"})
#: Legacy gameplay trees, PPO/RL inference stacks and LLM clients.
_FORBIDDEN_MODULE = (
    r"\A(?:bots\.(?:current|v\d+)|torch|stable_baselines3|sb3_contrib|gymnasium|gym|"
    r"anthropic|openai|claude_agent_sdk|claude_code_sdk|langchain\w*|transformers)(?:\.|\Z)"
)
#: Modules the entry points must reach, or the audit proves nothing.
_GAMEPLAY_MODULES = frozenset(
    {
        "bots.jev.v1",
        "bots.jev.v1.__main__",
        "bots.jev.v2",
        "bots.jev.v2.__main__",
        "jev.runner",
        "jev.bot",
        "jev.sc2_adapter",
        "jev.runtime",
        "jev.plans",
        "jev.operations",
        "jev.policy",
        "jev.contracts",
    }
)


def _first_party_file(name: str) -> Path | None:
    for root in _SOURCE_ROOTS:
        base = root.joinpath(*name.split("."))
        if (base / "__init__.py").is_file():
            return base / "__init__.py"
        if base.with_suffix(".py").is_file():
            return base.with_suffix(".py")
    return None


def _module_name(path: Path) -> str:
    root = _REPO / "src" if path.is_relative_to(_REPO / "src") else _REPO
    parts = list(path.relative_to(root).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imports(path: Path) -> tuple[set[str], set[str]]:
    """``(first-party modules, external modules)`` imported anywhere in ``path``.

    Every import statement counts -- module level, function bodies and
    ``TYPE_CHECKING`` blocks alike -- so lazily imported code is audited too.
    """
    module = _module_name(path)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    first_party: set[str] = set()
    external: set[str] = set()

    def classify(name: str, *, maybe_attribute: bool) -> None:
        if _first_party_file(name) is not None:
            first_party.add(name)
        elif not maybe_attribute:
            external.add(name)

    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                classify(alias.name, maybe_attribute=False)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*parts, *([node.module] if node.module else [])])
            else:
                base = node.module or ""
            classify(base, maybe_attribute=False)
            for alias in node.names:  # ``from package import submodule``
                classify(f"{base}.{alias.name}", maybe_attribute=True)
    return first_party, external


def test_static_import_graph_of_jev_has_no_legacy_rl_or_llm_dependency() -> None:
    forbidden = re.compile(_FORBIDDEN_MODULE)
    audited = {p for d in _JEV_DIRS for p in d.rglob("*.py")} | {_REPO / "bots" / "__init__.py"}
    reachable: set[str] = set()
    queue = ["bots.jev.v1", "bots.jev.v1.__main__", "bots.jev.v2", "bots.jev.v2.__main__"]
    first_party: set[str] = set()
    external: set[str] = set()
    while queue:
        name = queue.pop()
        if name in reachable:
            continue
        reachable.add(name)
        path = _first_party_file(name)
        assert path is not None, name
        audited.add(path)
        found, _ = _imports(path)
        parents = {".".join(name.split(".")[:i]) for i in range(1, name.count(".") + 1)}
        queue.extend(m for m in found | parents if m.startswith(("jev", "bots")))
    dashboard_external: set[str] = set()
    for path in sorted(audited):
        found, outside = _imports(path)
        first_party |= found
        if _module_name(path) in _DASHBOARD_MODULES:
            dashboard_external |= outside
        else:
            external |= outside
    assert _GAMEPLAY_MODULES <= reachable  # the scan covered the real entry point's graph
    assert not reachable & _DASHBOARD_MODULES  # a match never loads the web router
    dashboard_tops = {name.split(".")[0] for name in dashboard_external}
    assert dashboard_tops <= set(sys.stdlib_module_names) | _DASHBOARD_EXTERNALS
    others = {m for m in first_party if not m.startswith(("jev", "bots.jev"))} - {"bots"}
    assert others <= _ALLOWED_FIRST_PARTY, sorted(others)
    for name in _ALLOWED_FIRST_PARTY:
        path = _first_party_file(name)
        assert path is not None
        found, outside = _imports(path)
        assert found == set()  # the allowed helper imports no other first-party code
        external |= outside
    tops = {name.split(".")[0] for name in external}
    # Typesafe's opt-in HTTP boundary is the sole new gameplay external dependency.
    assert tops <= set(sys.stdlib_module_names) | {"sc2", "httpx"}, sorted(tops)
    everything = first_party | external | dashboard_external | reachable
    assert not [m for m in everything if forbidden.match(m)]


_RUNTIME_AUDIT = r"""
import asyncio, json, re, sys, uuid
from types import SimpleNamespace

from bots.jev.v1.__main__ import main
from bots.jev.v1 import load_policy
from jev.bot import JevBot, JevController
from jev.runner import MatchOptions

rc = main(["--run-root", sys.argv[2]])  # the real SC2 preflight; SC2PATH is empty here


def unit(tag, name, position, **extra):
    fields = dict(
        tag=tag, name=name, position=position, health=40.0, shield=0.0, build_progress=1.0,
        orders=[],
        is_structure=False, is_flying=False, is_ready=True, is_idle=True, is_powered=True,
        is_visible=True, is_carrying_vespene=False, can_attack_ground=False,
        can_attack_air=False,
    )
    fields.update(extra)
    return SimpleNamespace(**fields)


game = SimpleNamespace(
    state=SimpleNamespace(game_loop=0), minerals=0, supply_used=20, supply_cap=39,
    units=[unit(6000 + i, "Zealot", (36.0, 40.0)) for i in range(4)],
    structures=[unit(1000, "Nexus", (30.5, 30.5), is_structure=True)],
    enemy_units=[unit(7000, "Marine", (40.0, 30.5))], enemy_structures=[], mineral_field=[],
    vespene=0, vespene_geyser=[],
    start_location=(30.5, 30.5), enemy_start_locations=[(120.5, 120.5)],
    expansion_locations_list=[(120.5, 120.5)], game_info=SimpleNamespace(map_center=(75.5, 75.5)),
)


class Port:
    def is_visible(self, point):
        return True

    async def placement_legal(self, ability, sites):
        return [True] * len(sites)

    async def issue(self, ability, actor, target):
        return None

    def action_errors(self):
        return []

    async def leave(self):
        return None


controller = JevController(load_policy(), run_id=uuid.uuid4().hex, limits=MatchOptions().limits())
bot = JevBot(controller)
controller.attach(game, Port())
for step in range(4):
    game.state.game_loop = 8 * step
    asyncio.run(bot.on_step(step))
pattern = re.compile(sys.argv[1])
print(json.dumps({
    "rc": rc,
    "accepted": controller.adapter.accepted_count,
    "forbidden": sorted(m for m in sys.modules if pattern.match(m)),
    "loaded": sorted(m for m in sys.modules if m.startswith(("jev.", "sc2.", "orchestrator"))),
}))
"""


def test_runtime_modules_after_playing_steps_have_no_legacy_rl_or_llm_dependency(
    tmp_path: Path,
) -> None:
    """A fresh interpreter runs the CLI preflight and steps JevBot through the defense
    and attack lanes; nothing forbidden is ever imported, even transitively."""
    script = tmp_path / "audit.py"
    script.write_text(_RUNTIME_AUDIT, encoding="utf-8")
    env_root = tmp_path / "no-sc2"
    env_root.mkdir()
    proc = subprocess.run(
        [sys.executable, str(script), _FORBIDDEN_MODULE, str(tmp_path / "runs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        env={**_inherited_env(), "SC2PATH": str(env_root)},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["rc"] == EXIT_FAILURE  # sc2_unavailable after the real preflight
    assert report["accepted"] == 4  # the defense lane really commanded all four Zealots
    assert report["forbidden"] == []
    loaded = set(report["loaded"])
    assert {
        "jev.bot",
        "jev.sc2_adapter",
        "jev.runtime",
        "jev.plans",
        "sc2.bot_ai",
        "orchestrator.paths",
    } <= loaded


def _inherited_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "SC2PATH")}
