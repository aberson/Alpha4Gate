"""Jev v2 gas economy through the production entry point (Phase J2 Step 216, plan D3/D4).

Every scenario runs the path a real match runs: :func:`jev.runner.run_match` ->
:class:`jev.bot.JevController` -> ``JevBot.on_step`` ->
:class:`jev.sc2_adapter.Sc2Adapter` -> :class:`jev.runtime.JevRuntime` with the
packaged v2 policy -> :class:`jev.sc2_adapter.BotAIPort`. Only the SC2 process is
replaced: :class:`_GasWorld` extends the economy suite's burnysc2 world (real
``Unit`` objects from protobuf with real ``GameData``) with vespene geysers,
Assimilators built on a geyser *unit*, gas mining, SC2's harvester counts, a
probe that is out of sight while inside an Assimilator, and vespene costs.

Covered: the Assimilator command targets the geyser by unit tag (never a point,
never a placement query), one per base; saturation at three workers (never above
SC2's ideal); a depleted Assimilator returns its workers to minerals; a destroyed
one fails the gathers still on their way and is rebuilt; a lost gas worker or
builder is replaced at once; gas is reserved so concurrent spenders never
double-spend, and released on acknowledgement or failure. Gas workers never take
the scout or a probe another task holds.

No allowlisted v2 type costs gas until Step 218 (the Stalker), so the spending
scenarios give the Zealot a hypothetical gas cost through the one cost table
(:data:`jev.operations.VESPENE_COST`), which the production intents read.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from bots.jev.v1 import load_policy as load_v1_policy
from bots.jev.v2 import load_policy as load_v2_policy
from s2clientprotocol import common_pb2, data_pb2, raw_pb2, sc2api_pb2
from sc2.data import ActionResult, Alliance, Attribute, DisplayType
from sc2.game_data import GameData
from sc2.game_state import ActionError
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit import Unit
from sc2.unit_command import UnitCommand

import jev.operations as operations
from jev.bot import JevController
from jev.contracts import Entity, Event, Observation, Point
from jev.operations import (
    BUILD_ABILITY,
    COMMAND_ABILITIES,
    EXPANSION_BUILD_ABILITY,
    GAS_BUILD_ABILITY,
    GAS_DEPLETED_REASON,
    GAS_FOOTPRINT,
    GATHER_TARGET_LOST_REASON,
    MAX_GAS_HARVESTERS,
    MINERAL_COST,
    PYLON_POWER_RADIUS,
    TRAIN_ABILITY,
    VESPENE_COST,
    distance,
    gas_slots,
    placement_candidates,
    validate_operation_args,
)
from jev.runner import MatchOptions, run_match
from jev.sc2_adapter import GAME_LOOPS_PER_SECOND
from test_jev_economy import (
    _BUILD_KIND,
    _KINDS,
    _REMAPS,
    _TRAIN_KIND,
    BASE_BUILD,
    NEXUS,
    START,
    STEP_LOOPS,
    _Body,
    _Launcher,
    _World,
)

#: The geyser nearest the Nexus (the Assimilator goes here) and a second one.
GEYSER = 5000
GEYSER_FAR = 5001
GEYSER_SITES = {GEYSER: (37.5, 25.5), GEYSER_FAR: (37.5, 36.5)}
PYLON = 1200
GATEWAYS = (1100, 1101, 1102, 1103)
_GATEWAY_SITES = ((20.5, 37.5), (24.5, 39.5), (27.5, 37.5), (21.5, 33.5))
PROBES = tuple(range(2000, 2016))
PATCHES = tuple(range(3000, 3008))

ASSIMILATOR = AbilityId.PROTOSSBUILD_ASSIMILATOR
GAS_BUILD_NODE = "construction.gas.build"
SATURATE_NODE = "economy.gas.saturate.go"
RELEASE_NODE = "economy.gas.release.go"
SCOUT_NODES = ("scouting.move", "scouting.retreat.go")

_GAS_KINDS: dict[str, tuple[UnitTypeId, bool]] = {
    **_KINDS,
    "Assimilator": (UnitTypeId.ASSIMILATOR, True),
    "VespeneGeyser": (UnitTypeId.VESPENEGEYSER, True),
}
#: SC2 "faster" Assimilator build time, game seconds.
_ASSIMILATOR_SECONDS = 21.0
#: Gas one harvesting probe brings in per game second (about 3 x 57 per minute).
_GAS_RATE = 0.94
#: Minerals one mining probe brings in per game second (about 57 per minute).
_MINING_RATE = 0.95
_GEYSER_GAS = 2250
#: A probe stays out of sight inside an Assimilator for one of every two 32-loop
#: windows once it has gathered there this long (SC2 hides it while it harvests).
_INSIDE_AFTER_LOOPS = 64


def _gas_game_data() -> GameData:
    units = [
        data_pb2.UnitTypeData(
            unit_id=type_id.value,
            name=name,
            available=True,
            attributes=[Attribute.Structure.value] if structure else [],
        )
        for name, (type_id, structure) in _GAS_KINDS.items()
    ]
    plain = {*_BUILD_KIND, *_TRAIN_KIND, *_REMAPS.values(), ASSIMILATOR}
    abilities = [data_pb2.AbilityData(ability_id=a.value, available=True) for a in plain] + [
        data_pb2.AbilityData(ability_id=a.value, remaps_to_ability_id=g.value, available=True)
        for a, g in _REMAPS.items()
    ]
    return GameData(sc2api_pb2.ResponseData(units=units, abilities=abilities))


_GAS_GAME_DATA = _gas_game_data()


class _GasWorld(_World):
    """The economy suite's world plus SC2's gas mechanics (see the module docstring).

    Geysers are neutral and keep their gas (``contents``); an Assimilator reports its
    geyser's gas, SC2's ``assigned_harvesters`` (every probe ordered to gather from
    it, inside or not) and ``ideal_harvesters`` (``ideal`` while it has gas, else 0).
    A depleted Assimilator keeps its workers' orders (the worst case: nothing makes
    them leave but Jev). Destroying a structure ends every order on it, as in SC2. A
    probe in ``returning`` is on its way back with gas: SC2's ``HARVEST_RETURN`` to the
    Nexus (never the gas building) and the carry-gas buff, still in the building's
    assigned count.
    """

    def __init__(
        self,
        bodies: list[_Body],
        *,
        minerals: int,
        vespene: int = 0,
        place_after: float | None = 2.0,
        hide_inside: bool = True,
        supply: tuple[int, int] = (30, 100),
        mining_rate: float = 0.0,
    ) -> None:
        self.mining_rate = mining_rate
        self._mineral_income = 0.0
        self.contents: dict[int, int] = {
            b.tag: _GEYSER_GAS for b in bodies if b.kind == "VespeneGeyser"
        }
        self.on_geyser: dict[int, int] = {}  # Assimilator tag -> its geyser's tag
        self.returning: dict[int, int] = {}  # probe tag -> the Assimilator it carries from
        self.ideal = 3
        self.hide_inside = hide_inside
        self.inside: set[int] = set()
        self.gather_since: dict[int, int] = {}
        self.placement_abilities: list[AbilityId] = []
        self.vespene_refusals: list[str] = []
        self.refuse_train_from: set[int] = set()
        self.gas_mined = 0
        self._gas_income = 0.0
        self._gas_placements: list[tuple[int, int, int]] = []  # (due loop, worker, geyser)
        super().__init__(bodies, minerals=minerals, supply=supply, place_after=place_after)
        self.game_data = _GAS_GAME_DATA
        self.vespene = vespene
        self.refresh()

    def add_assimilator(self, geyser: int, progress: float = 1.0) -> int:
        tag = self.add("Assimilator", GEYSER_SITES[geyser], progress=progress)
        self.on_geyser[tag] = geyser
        self.refresh()
        return tag

    def add_returning(self, probe: int, building: int) -> None:
        """``probe`` carries gas from ``building`` back to the Nexus."""
        self.bodies[probe].orders = [(AbilityId.HARVEST_RETURN_PROBE, NEXUS, 0.0)]
        self.returning[probe] = building
        self.refresh()

    def gatherers(self, building: int) -> list[_Body]:
        """Own probes ordered to gather from ``building`` (inside it or not)."""
        return [
            b
            for b in self.own("Probe")
            if b.orders[:1] and b.orders[0][:2] == (AbilityId.HARVEST_GATHER_PROBE, building)
        ]

    def kill(self, tag: int) -> None:
        for body in self.bodies.values():  # SC2: a gather or build order on it ends
            if body.orders and body.orders[0][1] == tag:
                body.orders = []
        self.on_geyser.pop(tag, None)
        self.returning.pop(tag, None)
        self.inside.discard(tag)
        super().kill(tag)

    # -- what burnysc2 exposes -------------------------------------------------

    def refresh(self) -> None:
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
            if body.tag in self.inside:
                continue  # inside an Assimilator: out of sight, still in SC2's count
            powered = body.kind == "Gateway" and any(
                distance(body.position, p) <= PYLON_POWER_RADIUS for p in pylons
            )
            unit = Unit(self._gas_proto(body, powered), self, base_build=BASE_BUILD)
            structure = _GAS_KINDS[body.kind][1]
            if body.kind == "MineralField":
                groups["mineral_field"].append(unit)
            elif body.kind == "VespeneGeyser":
                groups["vespene_geyser"].append(unit)
            elif body.alliance == Alliance.Enemy:
                groups["enemy_structures" if structure else "enemy_units"].append(unit)
            else:
                groups["structures" if structure else "units"].append(unit)
        for name, units in groups.items():
            setattr(self, name, units)

    def _gas_proto(self, body: _Body, powered: bool) -> raw_pb2.Unit:
        orders = []
        for ability, target, progress in body.orders:
            order = raw_pb2.UnitOrder(ability_id=ability.value, progress=progress)
            if isinstance(target, tuple):
                order.target_world_space_pos.x, order.target_world_space_pos.y = target
            elif target is not None:
                order.target_unit_tag = target
            orders.append(order)
        proto = raw_pb2.Unit(
            display_type=DisplayType.Visible.value,
            alliance=body.alliance.value,
            tag=body.tag,
            unit_type=_GAS_KINDS[body.kind][0].value,
            pos=common_pb2.Point(x=body.position[0], y=body.position[1], z=10.0),
            health=100.0,
            build_progress=body.progress,
            is_powered=powered,
            orders=orders,
        )
        if body.tag in self.returning:
            proto.buff_ids.append(BuffId.CARRYHARVESTABLEVESPENEGEYSERGASPROTOSS.value)
        if body.kind == "VespeneGeyser":
            proto.vespene_contents = self.contents[body.tag]
        elif body.kind == "Assimilator":
            gas = self.contents[self.on_geyser[body.tag]]
            proto.vespene_contents = gas
            carrying = sum(1 for b in self.returning.values() if b == body.tag)
            proto.assigned_harvesters = len(self.gatherers(body.tag)) + carrying
            proto.ideal_harvesters = self.ideal if body.progress >= 1.0 and gas > 0 else 0
        return proto

    async def can_place(self, ability: AbilityId, positions: list[Point2]) -> list[bool]:
        self.placement_abilities.append(ability)
        return await super().can_place(ability, positions)

    # -- SC2's response to a command -------------------------------------------

    def receive(self, command: UnitCommand) -> ActionResult:
        ability, target = command.ability, command.target
        body = self.bodies.get(command.unit.tag)
        if ability == ASSIMILATOR:
            self.sent.append((self.state.game_loop, command))
            if body is None or body.alliance != Alliance.Self:
                self.foreign.append(command.unit.tag)
                return ActionResult.NotSupported
            geyser = self.bodies.get(target.tag) if isinstance(target, Unit) else None
            if geyser is None or geyser.kind != "VespeneGeyser":
                return ActionResult.MustTargetResources  # SC2 needs the geyser unit
            body.orders = [(ability, geyser.tag, 0.0)]
            delay = self.place_delay(geyser.position)
            if delay is not None:
                due = self.state.game_loop + round(delay * GAME_LOOPS_PER_SECOND)
                self._gas_placements.append((due, body.tag, geyser.tag))
            return ActionResult.Success
        if ability in _TRAIN_KIND:
            if command.unit.tag in self.refuse_train_from:
                self.sent.append((self.state.game_loop, command))
                return ActionResult.Error
            cost = VESPENE_COST[_TRAIN_KIND[ability]]  # read now: a test may set a gas cost
            if cost and self.vespene < cost:
                self.sent.append((self.state.game_loop, command))
                self.vespene_refusals.append(f"loop {self.state.game_loop}")
                return ActionResult.NotEnoughVespene
            result = super().receive(command)
            if result == ActionResult.Success:
                self.vespene -= cost
            return result
        return super().receive(command)

    def advance(self, loops: int = STEP_LOOPS) -> None:
        dt = loops / GAME_LOOPS_PER_SECOND
        now = self.state.game_loop + loops
        patches = {b.tag for b in self.bodies.values() if b.kind == "MineralField"}
        mining = sum(
            1
            for b in self.own("Probe")
            if b.orders[:1]
            and b.orders[0][0] == AbilityId.HARVEST_GATHER_PROBE
            and b.orders[0][1] in patches
        )
        self._mineral_income += mining * self.mining_rate * dt
        self.minerals += int(self._mineral_income)
        self._mineral_income -= int(self._mineral_income)
        gathering: set[int] = set()
        for building in self.own("Assimilator"):
            if building.progress < 1.0:
                building.progress = min(1.0, building.progress + dt / _ASSIMILATOR_SECONDS)
                continue
            workers = self.gatherers(building.tag)
            gathering.update(w.tag for w in workers)
            geyser = self.on_geyser[building.tag]
            self._gas_income += min(len(workers), 3) * _GAS_RATE * dt
            gained = min(int(self._gas_income), self.contents[geyser])
            self._gas_income -= int(self._gas_income)
            self.contents[geyser] -= gained
            self.vespene += gained
            self.gas_mined += gained
        for tag in gathering:
            self.gather_since.setdefault(tag, self.state.game_loop)
        self.gather_since = {t: s for t, s in self.gather_since.items() if t in gathering}
        self.inside = set()
        if self.hide_inside and (now // 32) % 2 == 0:
            for building in self.own("Assimilator"):
                settled = [
                    w.tag
                    for w in self.gatherers(building.tag)
                    if now - self.gather_since.get(w.tag, now) >= _INSIDE_AFTER_LOOPS
                ]
                if settled and self.contents[self.on_geyser[building.tag]] > 0:
                    self.inside.add(min(settled))
        for placement in [p for p in self._gas_placements if p[0] <= now]:
            self._gas_placements.remove(placement)
            self._place_gas(placement[1], placement[2])
        super().advance(loops)

    def _place_gas(self, worker_tag: int, geyser_tag: int) -> None:
        worker = self.bodies.get(worker_tag)
        if worker is None or worker.orders[:1] != [(ASSIMILATOR, geyser_tag, 0.0)]:
            return  # the worker died or was re-ordered on the way
        worker.orders = []
        refusal: ActionResult | None = None
        if geyser_tag in self.on_geyser.values():
            refusal = ActionResult.CantBuildLocationInvalid
        elif self.minerals < MINERAL_COST["Assimilator"]:
            self.overspent.append(f"Assimilator at loop {self.state.game_loop}")
            refusal = ActionResult.NotEnoughMinerals
        if refusal is not None:
            self.state.action_errors = [ActionError(ASSIMILATOR.value, worker_tag, refusal.value)]
            return
        self.minerals -= MINERAL_COST["Assimilator"]
        tag = self.add("Assimilator", GEYSER_SITES[geyser_tag], progress=0.05)
        self.on_geyser[tag] = geyser_tag


def _gas_world(
    *,
    minerals: int = 0,
    vespene: int = 0,
    geysers: bool = True,
    assimilator: float | None = None,
    gas_workers: int = 0,
    place_after: float | None = 2.0,
    hide_inside: bool = True,
) -> _GasWorld:
    """One Nexus, a Pylon, four finished powered Gateways (the opening is placed),
    eight patches, sixteen mining probes and -- with ``geysers`` -- two geysers.

    ``assimilator`` adds one on GEYSER at that build progress, with ``gas_workers``
    of the probes already gathering from it.
    """
    bodies = [
        _Body(NEXUS, "Nexus", START),
        _Body(PYLON, "Pylon", (24.0, 36.0)),
        *(_Body(tag, "Gateway", site) for tag, site in zip(GATEWAYS, _GATEWAY_SITES, strict=True)),
        *(
            _Body(tag, "MineralField", (26.5 + i, 22.5), alliance=Alliance.Neutral)
            for i, tag in enumerate(PATCHES)
        ),
    ]
    if geysers:
        bodies += [
            _Body(tag, "VespeneGeyser", site, alliance=Alliance.Neutral)
            for tag, site in GEYSER_SITES.items()
        ]
    for i, tag in enumerate(PROBES):
        orders = [(AbilityId.HARVEST_GATHER_PROBE, PATCHES[i % 8], 0.0)]
        bodies.append(_Body(tag, "Probe", (27.0 + i * 0.4, 26.0), orders=orders))
    world = _GasWorld(
        bodies,
        minerals=minerals,
        vespene=vespene,
        place_after=place_after,
        hide_inside=hide_inside,
    )
    if assimilator is not None:
        building = world.add_assimilator(GEYSER, progress=assimilator)
        for probe in PROBES[-gas_workers:] if gas_workers else ():
            world.bodies[probe].orders = [(AbilityId.HARVEST_GATHER_PROBE, building, 0.0)]
        world.refresh()
    return world


def _opening_world() -> _GasWorld:
    """The v2 opening's start: one Nexus, eight patches, two geysers, twelve mining
    probes and 50 minerals, with mining income."""
    bodies = [
        _Body(NEXUS, "Nexus", START),
        *(
            _Body(tag, "MineralField", (26.5 + i, 22.5), alliance=Alliance.Neutral)
            for i, tag in enumerate(PATCHES)
        ),
        *(
            _Body(tag, "VespeneGeyser", site, alliance=Alliance.Neutral)
            for tag, site in GEYSER_SITES.items()
        ),
    ]
    for i, tag in enumerate(PROBES[:12]):
        orders = [(AbilityId.HARVEST_GATHER_PROBE, PATCHES[i % 8], 0.0)]
        bodies.append(_Body(tag, "Probe", (27.0 + i * 0.5, 26.0), orders=orders))
    return _GasWorld(bodies, minerals=50, supply=(12, 15), mining_rate=_MINING_RATE)


# ---------------------------------------------------------------------------
# The production runner, recording every tick
# ---------------------------------------------------------------------------

_Hook = Callable[[_GasWorld, JevController, int], None]


class _Run:
    """One v2 match against a :class:`_GasWorld`: every event of every tick, and per
    step (by its game loop) the actors tasks held, the scout, the probes gathering gas
    and the Gateways standing as the step began."""

    def __init__(self, world: _GasWorld) -> None:
        self.world = world
        self.events: list[Event] = []
        self.held: dict[int, dict[int, str]] = {}
        self.scouts: dict[int, frozenset[int]] = {}
        self.gas_workers: dict[int, frozenset[int]] = {}
        self.gateways: dict[int, int] = {}
        self.gas_load: dict[int, int] = {}
        self.hidden_steps = 0
        self.controller: JevController | None = None

    def hook(self, extra: _Hook | None) -> _Hook:
        def before(world: _GasWorld, controller: JevController, index: int) -> None:
            if index == 0:
                self._capture(controller)
            if extra is not None:
                extra(world, controller, index)
            runtime = controller.runtime
            self.held[world.state.game_loop] = {
                task.actor_tag: task.node_id
                for task in runtime.active_tasks()
                if task.actor_tag is not None
                and (task.status in ("pending", "issued") or task.node_id in SCOUT_NODES)
            }
            self.scouts[world.state.game_loop] = frozenset().union(
                *(runtime.assigned_to(node) for node in SCOUT_NODES)
            )
            self.hidden_steps += bool(world.inside)
            self.gateways[world.state.game_loop] = len(world.own("Gateway"))
            self.gas_workers[world.state.game_loop] = frozenset(
                worker.tag
                for building in world.own("Assimilator")
                for worker in world.gatherers(building.tag)
            ) | frozenset(world.returning)
            for building in world.own("Assimilator"):
                load = len(world.gatherers(building.tag))
                self.gas_load[building.tag] = max(self.gas_load.get(building.tag, 0), load)

        return before

    def _capture(self, controller: JevController) -> None:
        self.controller = controller
        runtime = controller.runtime
        tick = runtime.tick

        def recording(observation: object) -> object:
            result = tick(observation)  # type: ignore[arg-type]
            self.events.extend(result.events)
            return result

        runtime.tick = recording  # type: ignore[method-assign,assignment]

    def commands(self, node_id: str) -> list[Event]:
        return [e for e in self.events if e.kind == "command" and e.node_id == node_id]

    def tasks(self, node_id: str, status: str) -> list[Event]:
        return [
            e
            for e in self.events
            if e.kind == "task" and e.node_id == node_id and e.status == status
        ]

    def node_events(self, node_id: str) -> list[Event]:
        return [e for e in self.events if e.kind == "node" and e.node_id == node_id]

    def sent(self, ability: AbilityId) -> list[tuple[int, UnitCommand]]:
        return [(loop, c) for loop, c in self.world.sent if c.ability == ability]


def _play(world: _GasWorld, steps: int, hook: _Hook | None = None) -> _Run:
    run = _Run(world)
    launcher = _Launcher(world, steps, run.hook(hook))  # type: ignore[arg-type]
    with tempfile.TemporaryDirectory() as run_root:  # the evidence is not under test here
        run_match(MatchOptions(), load_v2_policy(), launcher=launcher, run_root=Path(run_root))
    assert run.controller is not None
    return run


def _actor(event: Event) -> int:
    assert event.action is not None
    (actor,) = event.action["actor_tags"]  # type: ignore[misc]
    return int(str(actor))


def _target(event: Event) -> object:
    assert event.action is not None
    return event.action["target"]


def _gas_gathers(
    sent: list[tuple[int, UnitCommand]], building: int
) -> list[tuple[int, UnitCommand]]:
    """The gathers in ``sent`` (a world's commands) that target ``building``."""
    return [
        (loop, c)
        for loop, c in sent
        if c.ability == AbilityId.HARVEST_GATHER
        and isinstance(c.target, Unit)
        and c.target.tag == building
    ]


#: The gas lane's own worker commands (the only nodes that may command a gas worker).
_GAS_WORKER_NODES = (SATURATE_NODE, RELEASE_NODE)


def _assert_single_owner(run: _Run) -> None:
    """D3 single ownership, both ways: a gas command never takes the scout or a probe
    another node's task holds, and no other node (a builder, the scout, the Assimilator
    builder included) ever commands a probe that is gathering gas."""
    for node in (*_GAS_WORKER_NODES, GAS_BUILD_NODE):
        for event in run.commands(node):
            actor = _actor(event)
            assert actor not in run.scouts.get(event.game_loop, ()), (node, actor)
            holder = run.held.get(event.game_loop, {}).get(actor)
            assert holder is None or holder == node, (node, actor, holder)
    for event in run.events:
        if event.kind != "command" or event.node_id in _GAS_WORKER_NODES:
            continue
        actor = _actor(event)
        assert actor not in run.gas_workers.get(event.game_loop, ()), (event.node_id, actor)


# ---------------------------------------------------------------------------
# Construction: the geyser unit, one per base
# ---------------------------------------------------------------------------


def test_v2_builds_one_assimilator_on_the_geyser_unit_never_at_a_point() -> None:
    world = _gas_world(minerals=400)
    run = _play(world, 110)
    builds = run.sent(ASSIMILATOR)
    assert len(builds) == 1  # one per active base, though minerals and a 2nd geyser remain
    loop, command = builds[0]
    assert type(command.target) is Unit and command.target.tag == GEYSER  # the nearest geyser
    assert ASSIMILATOR not in world.placement_abilities  # no placement query, no point
    assert run.controller is not None
    (record,) = [r for r in run.controller.adapter.records if r.ability == ASSIMILATOR.name]
    assert (record.outcome, record.target, record.candidates) == ("accepted", GEYSER, ())
    (event,) = run.commands(GAS_BUILD_NODE)
    assert _target(event) == str(GEYSER) and event.game_loop == loop
    (assimilator,) = world.own("Assimilator")
    assert assimilator.position == GEYSER_SITES[GEYSER] and assimilator.progress >= 1.0
    assert world.on_geyser == {assimilator.tag: GEYSER}
    (done,) = run.tasks(GAS_BUILD_NODE, "succeeded")
    assert done.reason == "construction complete"
    assert world.minerals <= 400 - MINERAL_COST["Assimilator"] and world.overspent == []
    assert world.foreign == []
    _assert_single_owner(run)


def test_v2_takes_gas_only_once_the_four_gateway_opening_is_placed() -> None:
    world = _gas_world(minerals=1000)
    for gateway in GATEWAYS[1:]:
        world.kill(gateway)  # one Gateway placed: the opening is not
    run = _play(world, 60)
    four_at = min(loop for loop, count in run.gateways.items() if count >= 4)
    opening = run.node_events("construction.gas.opening")
    before = {e.status for e in opening if e.game_loop < four_at}
    after = {e.status for e in opening if e.game_loop >= four_at}
    assert (before, after) == ({"failure"}, {"success"})  # evaluated, gating, then open
    ((loop, build),) = run.sent(ASSIMILATOR)
    assert loop >= four_at and build.target.tag == GEYSER  # then it IS built
    _assert_single_owner(run)


def test_v2_takes_gas_after_the_opening_and_never_lends_its_gas_workers() -> None:
    """From twelve probes, 50 minerals and mining income: four Gateways, then one
    Assimilator on the nearest geyser and three gas workers. Supply Pylons keep going up
    while they gather, and no builder or scout ever takes one of them (D3 single
    ownership: in this very world a buffer-Pylon builder took a gas worker until builder
    selections excluded ``gathering_gas`` probes)."""
    world = _opening_world()
    run = _play(world, 600)
    four_at = min(loop for loop, count in run.gateways.items() if count >= 4)
    ((loop, build),) = run.sent(ASSIMILATOR)
    assert loop >= four_at and build.target.tag == GEYSER
    (building,) = world.own("Assimilator")
    assert building.progress >= 1.0 and len(world.gatherers(building.tag)) == 3
    gas_from = min(lp for lp, workers in run.gas_workers.items() if len(workers) == 3)
    pylons = [lp for lp, _ in run.sent(AbilityId.PROTOSSBUILD_PYLON) if lp > gas_from]
    assert pylons  # builders were chosen while the gas workers gathered
    assert len(_gas_gathers(world.sent, building.tag)) == 3  # none had to be replaced
    assert world.overspent == [] and world.foreign == []
    _assert_single_owner(run)


#: Where this world's buffer Pylon goes (asserted in the test).
_BUFFER_PYLON_SITE = (25.0, 28.0)


def test_v2_builders_never_take_a_gas_worker_while_mineral_workers_remain() -> None:
    """The three gas workers stand nearest the buffer Pylon's site, so a builder chosen
    by distance alone would be one of them; the selection takes a mineral worker."""
    world = _gas_world(minerals=300, assimilator=1.0, gas_workers=3)
    (building,) = world.own("Assimilator")
    gas = PROBES[-3:]
    for tag, spot in zip(gas, ((25.0, 27.0), (24.0, 28.5), (26.0, 28.5)), strict=True):
        world.bodies[tag].position = spot
    world.supply_used, world.supply_cap = 96, 100  # the supply buffer needs a Pylon now
    world.refresh()
    run = _play(world, 4)
    ((_, pylon),) = run.sent(AbilityId.PROTOSSBUILD_PYLON)
    site = (pylon.target.x, pylon.target.y)
    assert site == _BUFFER_PYLON_SITE
    builder = pylon.unit.tag
    assert builder not in gas
    assert min(distance(world.bodies[t].position, site) for t in gas) < distance(
        world.bodies[builder].position, site
    )  # nearest-first alone would have taken a gas worker
    assert {w.tag for w in world.gatherers(building.tag)} == set(gas)  # still on gas
    _assert_single_owner(run)


@pytest.mark.parametrize("pylon_needed", [True, False], ids=["builder", "scout"])
def test_v2_a_gas_worker_returning_with_vespene_is_never_taken(pylon_needed: bool) -> None:
    """On its way back a gas worker orders HARVEST_RETURN to the Nexus, never the gas
    building, so only SC2's carry-gas buff still marks it. Here it is the lowest-tag
    mining probe (the scout replacement's first pick) and stands nearest the buffer
    Pylon's site (the builder's first pick, when supply needs a Pylon -- the builder
    lane runs before scouting, so the scout case runs without one). Neither takes it,
    and a mineral worker, not it, fills the Assimilator's open slot."""
    world = _gas_world(minerals=300, assimilator=1.0, gas_workers=1)
    (building,) = world.own("Assimilator")
    carrier = PROBES[0]
    world.add_returning(carrier, building.tag)
    world.bodies[carrier].position = (25.0, 27.5)
    if pylon_needed:
        world.supply_used, world.supply_cap = 96, 100  # the supply buffer needs a Pylon
    world.refresh()
    run = _play(world, 4)
    assert [e.node_id for e in run.events if e.kind == "command" and _actor(e) == carrier] == []
    pylons = run.sent(AbilityId.PROTOSSBUILD_PYLON)
    if pylon_needed:
        ((_, pylon),) = pylons
        site = (pylon.target.x, pylon.target.y)
        assert site == _BUFFER_PYLON_SITE
        assert distance(world.bodies[carrier].position, site) < distance(
            world.bodies[pylon.unit.tag].position, site
        )  # nearest-first alone would have taken the carrier
    else:
        assert pylons == []
    (scout,) = {_actor(e) for e in run.commands("scouting.move")}
    assert carrier < scout  # tag order alone would have made the carrier the scout
    (filled,) = run.commands(SATURATE_NODE)  # one open slot: two of three are taken
    assert _actor(filled) != carrier
    assert world.bodies[carrier].orders[0][0] == AbilityId.HARVEST_RETURN_PROBE
    _assert_single_owner(run)


# ---------------------------------------------------------------------------
# Gas workers: saturation, depletion, destruction, losses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ideal", [3, 2])
def test_v2_saturates_an_assimilator_up_to_its_ideal_and_never_beyond(ideal: int) -> None:
    """Three probes (or SC2's lower ideal) go to gas at once and no more ever follow:
    a probe out of sight inside the Assimilator still counts (SC2's assigned count)."""
    world = _gas_world(assimilator=1.0)
    world.ideal = ideal
    world.refresh()
    run = _play(world, 120)
    (building,) = world.own("Assimilator")
    gathers = _gas_gathers(world.sent, building.tag)
    assert len(gathers) == ideal  # each probe sent once: no churn while one is inside
    assert len({loop for loop, _ in gathers}) == 1  # all on one tick
    assert run.gas_load[building.tag] == ideal  # never a fourth, inside or not
    assert len(world.gatherers(building.tag)) == ideal
    assert world.gas_mined > 0 and world.vespene == world.gas_mined
    assert run.controller is not None
    bank = run.controller.metrics.summary(end_game_seconds=world.seconds)["mean_vespene_bank"]
    assert isinstance(bank, dict) and bank["status"] == "measured" and bank["value"] > 0  # D6
    assert run.hidden_steps > 0  # a probe was out of sight inside it on some steps
    assert all("vespene" not in e.facts for e in run.commands(SATURATE_NODE))  # costs no gas
    saturated = [
        e
        for e in run.node_events(SATURATE_NODE)
        if e.reason == "every bound gas building holds its allowed workers"
    ]
    assert saturated and saturated[-1].facts["buildings"] == {
        str(building.tag): {"workers": ideal, "slots": ideal}
    }
    _assert_single_owner(run)


def test_v2_returns_the_workers_of_a_depleted_assimilator_to_minerals() -> None:
    world = _gas_world(assimilator=1.0, gas_workers=3)
    (building,) = world.own("Assimilator")
    depleted_at: list[int] = []

    def deplete(world: _GasWorld, controller: JevController, index: int) -> None:
        if index == 12:
            world.contents[GEYSER] = 0
            depleted_at.append(world.state.game_loop)

    run = _play(world, 40, deplete)
    assert world.gatherers(building.tag) == []  # every worker left the depleted building
    releases = run.commands(RELEASE_NODE)
    assert len(releases) == 3 and {_actor(e) for e in releases} == set(PROBES[-3:])
    assert all(e.game_loop >= depleted_at[0] for e in releases)
    patches = {str(tag) for tag in PATCHES}
    assert all(_target(e) in patches for e in releases)  # back to minerals
    assert [c for loop, c in _gas_gathers(world.sent, building.tag) if loop >= depleted_at[0]] == []
    first = next(e for e in run.node_events(RELEASE_NODE) if e.facts.get("released"))
    assert first.facts["buildings"] == {str(building.tag): {"workers": 3, "slots": 0}}
    assert gas_slots(_entity_of(world, building.tag), 3) == 0
    _assert_single_owner(run)


def test_v2_a_gather_on_its_way_to_a_depleted_assimilator_fails_visibly() -> None:
    world = _gas_world(assimilator=1.0, gas_workers=2, hide_inside=False)
    (building,) = world.own("Assimilator")

    def deplete(world: _GasWorld, controller: JevController, index: int) -> None:
        if index == 1:  # the third worker, sent on the first tick, is on its way
            world.contents[GEYSER] = 0
            world.refresh()

    run = _play(world, 6, deplete)
    (sent,) = run.commands(SATURATE_NODE)
    (failed,) = run.tasks(SATURATE_NODE, "failed")
    assert failed.task_id == sent.task_id
    assert (failed.reason, failed.facts["failure_cause"]) == (GAS_DEPLETED_REASON, "lost")
    assert world.gatherers(building.tag) == []  # all three went back to minerals


def _entity_of(world: _GasWorld, tag: int) -> Entity:
    body = world.bodies[tag]
    gas = world.contents[world.on_geyser[tag]]
    return Entity(
        tag,
        body.kind,
        body.position,
        100.0,
        build_progress=body.progress,
        is_structure=True,
        vespene_contents=gas,
        assigned_harvesters=len(world.gatherers(tag)),
        ideal_harvesters=world.ideal if gas > 0 else 0,
    )


def test_v2_destroyed_assimilator_fails_gathers_on_their_way_and_is_rebuilt() -> None:
    world = _gas_world(assimilator=1.0)
    (building,) = world.own("Assimilator")

    def destroy_then_fund(world: _GasWorld, controller: JevController, index: int) -> None:
        if index == 1:
            world.kill(building.tag)  # the three just sent are on their way
        if index == 4:
            world.minerals = 100

    run = _play(world, 50, destroy_then_fund)  # the rebuilt one is still warping in
    sent = run.commands(SATURATE_NODE)
    assert len(sent) == 3 and len({e.game_loop for e in sent}) == 1
    failed = run.tasks(SATURATE_NODE, "failed")
    assert len(failed) == 3
    assert {(e.reason, e.facts["failure_cause"]) for e in failed} == {
        (GATHER_TARGET_LOST_REASON, "lost")
    }
    builds = run.sent(ASSIMILATOR)
    assert len(builds) == 1 and builds[0][1].target.tag == GEYSER  # rebuilt on the geyser
    (rebuilt,) = world.own("Assimilator")
    assert rebuilt.tag != building.tag and world.on_geyser == {rebuilt.tag: GEYSER}
    assert world.overspent == [] and world.foreign == []


def test_v2_a_gas_worker_lost_before_acknowledgement_is_replaced_at_once() -> None:
    world = _gas_world(assimilator=1.0, hide_inside=False)
    (building,) = world.own("Assimilator")
    victims: list[int] = []

    def kill_one(world: _GasWorld, controller: JevController, index: int) -> None:
        if index == 1:
            victim = max(c.unit.tag for _, c in _gas_gathers(world.sent, building.tag))
            victims.append(victim)
            world.kill(victim)

    run = _play(world, 12, kill_one)
    first, *later = sorted({loop for loop, _ in _gas_gathers(world.sent, building.tag)})
    lost = run.tasks(SATURATE_NODE, "failed")
    assert [(int(str(e.facts["actor_tag"])), e.reason) for e in lost] == [
        (victims[0], "actor lost")
    ]
    assert later == [lost[0].game_loop]  # replaced on the very tick the loss is seen
    assert len(world.gatherers(building.tag)) == 3
    assert victims[0] not in {b.tag for b in world.own("Probe")}


def test_v2_a_lost_assimilator_builder_is_replaced_and_the_cost_charged_once() -> None:
    world = _gas_world(minerals=100, place_after=3.0)
    builders: list[int] = []

    def kill_builder(world: _GasWorld, controller: JevController, index: int) -> None:
        if index == 2:
            command = next(c for _, c in world.sent if c.ability == ASSIMILATOR)
            builders.append(command.unit.tag)
            world.kill(command.unit.tag)

    run = _play(world, 30, kill_builder)
    builds = run.sent(ASSIMILATOR)
    assert len(builds) == 2 and {c.target.tag for _, c in builds} == {GEYSER}
    assert builds[1][1].unit.tag != builders[0]
    (lost,) = run.tasks(GAS_BUILD_NODE, "failed")
    assert (lost.reason, lost.facts["failure_cause"]) == ("actor lost", "lost")
    assert builds[1][0] == lost.game_loop  # a new builder on the same tick
    assert len(world.own("Assimilator")) == 1
    assert world.minerals == 100 - MINERAL_COST["Assimilator"] and world.overspent == []


# ---------------------------------------------------------------------------
# Gas cost accounting: reserve, never double-spend, release
# ---------------------------------------------------------------------------


@pytest.fixture
def zealot_costs_gas(monkeypatch: pytest.MonkeyPatch) -> int:
    """A hypothetical 50-gas Zealot, through the one cost table the intents read."""
    monkeypatch.setitem(operations.VESPENE_COST, "Zealot", 50)  # type: ignore[arg-type]
    return 50


def _zealot_trains(run: _Run) -> list[Event]:
    return run.commands("production.zealots.train")


def test_concurrent_gas_spenders_never_double_spend(zealot_costs_gas: int) -> None:
    world = _gas_world(minerals=1000, vespene=120, geysers=False)

    def income(world: _GasWorld, controller: JevController, index: int) -> None:
        if index == 4:
            world.vespene += 100

    run = _play(world, 6, income)
    trains = _zealot_trains(run)
    loops = sorted({e.game_loop for e in trains})
    assert [sum(1 for e in trains if e.game_loop == lp) for lp in loops] == [2, 2]
    assert world.vespene_refusals == [] and world.overspent == []
    assert world.vespene == 120 + 100 - 4 * zealot_costs_gas
    for event in trains:
        assert (event.facts["vespene"], event.facts["cost_committed"]) == (50, True)
    waits = [
        e
        for e in run.node_events("production.zealots.train")
        if e.reason == "waiting for vespene; holding reservation"
    ]
    # First tick: 120 - 2 x 50 leaves 20 to hold. Next tick the two are acknowledged
    # (SC2 deducted their gas), so their commitment is released, not charged again.
    assert [e.facts["held_vespene"] for e in waits[:2]] == [20, 20]
    acknowledged = [
        e
        for e in run.events
        if e.kind == "task" and e.node_id == "production.zealots.train" and e.status == "running"
    ]
    assert len(acknowledged) == 4
    assert all(e.facts["cost_committed"] is False for e in acknowledged)


def test_a_failed_gas_command_releases_its_reservation(zealot_costs_gas: int) -> None:
    world = _gas_world(minerals=1000, vespene=50, geysers=False)
    world.refuse_train_from = {GATEWAYS[0]}  # SC2 refuses that Gateway's Zealot every time
    run = _play(world, 30)
    trains = _zealot_trains(run)
    refused = [e for e in trains if _actor(e) == GATEWAYS[0]]
    assert len(refused) == 4  # the first attempt and three retries
    (failed,) = [
        e for e in run.tasks("production.zealots.train", "failed") if e.facts["actor_tag"] == "1100"
    ]
    assert failed.facts["failure_cause"] == "rejected" and failed.facts["cost_committed"] is False
    others = [e for e in trains if _actor(e) != GATEWAYS[0]]
    assert len(others) == 1  # the 50 gas buys exactly one other Zealot ...
    assert others[0].game_loop > failed.game_loop  # ... only once the failure released it
    assert world.vespene == 0 and world.vespene_refusals == []


# ---------------------------------------------------------------------------
# The allowlist, the cost tables and the packaged policies
# ---------------------------------------------------------------------------


def test_a_gas_structure_is_never_a_point_build() -> None:
    kinds = {"lane.pick": "select", "lane.go": "action"}
    for operation, args in (
        ("build", {"structure": "Assimilator", "worker": "$w", "site": "$s"}),
        (
            "select_placement",
            {
                "structure": "Assimilator",
                "near": "start_location",
                "radius_max": 5,
                "require_power": False,
                "limit": 1,
                "bind": "s",
            },
        ),
        ("build_gas", {"structure": "Pylon", "worker": "$w", "geyser": "$g"}),
    ):
        issues = validate_operation_args(operation, args, parameters={}, node_kinds=kinds)  # type: ignore[arg-type]
        assert any("must be one of" in text for _, text in issues), (operation, issues)
    assert not set(BUILD_ABILITY) & set(GAS_BUILD_ABILITY)
    assert set(GAS_BUILD_ABILITY.values()) <= COMMAND_ABILITIES


def test_cost_tables_have_one_entry_per_buildable_and_trainable_type() -> None:
    every = {*BUILD_ABILITY, *GAS_BUILD_ABILITY, *EXPANSION_BUILD_ABILITY, *TRAIN_ABILITY}
    assert set(MINERAL_COST) == set(VESPENE_COST) == every
    assert MINERAL_COST["Assimilator"] == 75 and VESPENE_COST["Assimilator"] == 0
    assert MINERAL_COST["Nexus"] == 400 and VESPENE_COST["Nexus"] == 0  # SC2 5.0 game data
    assert operations.VESPENE_COST is VESPENE_COST  # the intents read this very table


def test_gas_slots_follow_sc2_ideal_and_close_when_unfinished_or_depleted() -> None:
    def assimilator(**fields: object) -> Entity:
        values: dict[str, object] = {"vespene_contents": 2000, "ideal_harvesters": 3}
        values.update(fields)
        return Entity(9000, "Assimilator", (37.5, 25.5), 450.0, is_structure=True, **values)  # type: ignore[arg-type]

    assert gas_slots(assimilator(), 3) == 3
    assert gas_slots(assimilator(), 2) == 2
    assert gas_slots(assimilator(ideal_harvesters=2), 3) == 2
    assert gas_slots(assimilator(ideal_harvesters=0), 3) == 3  # SC2 reported no ideal
    assert gas_slots(assimilator(vespene_contents=0), 3) == 0
    assert gas_slots(assimilator(build_progress=0.5), 3) == 0


_GAS_OPERATIONS = {"build_gas", "gather_gas", "release_gas"}


def test_frozen_v1_reads_no_gas_and_v2_owns_the_gas_lanes() -> None:
    v1 = load_v1_policy().policy
    assert not [n.id for n in v1.nodes if n.operation in _GAS_OPERATIONS]
    assert all(n.args.get("resource") != "vespene" for n in v1.nodes)
    v2 = load_v2_policy().policy
    owners = {n.operation: n.id for n in v2.nodes if n.operation in _GAS_OPERATIONS}
    assert owners == {
        "build_gas": GAS_BUILD_NODE,
        "gather_gas": SATURATE_NODE,
        "release_gas": RELEASE_NODE,
    }
    geysers = v2.node("construction.gas.geysers").args
    assert geysers["collection"] == "vespene_geysers"
    assert v2.node(GAS_BUILD_NODE).args["geyser"] == f"${geysers['bind']}"
    per_gas = v2.parameters["workers_per_gas"]
    assert isinstance(per_gas, int) and 1 <= per_gas <= MAX_GAS_HARVESTERS
    assert v2.parameters["assimilator_target"] == 1  # D4: one per active base (one base)
    probe_picks = [
        n
        for n in v2.nodes
        if n.operation in ("select_entities", "select_plan_actor")  # an expansion's worker too
        and n.args.get("collection", "own_units") == "own_units"
        and "Probe" in n.args.get("filter", {}).get("types", [])
        and "assigned_to" not in n.args  # the scout itself, not a new pick
        and n.id != "economy.idle_probes"  # idle only: never a gathering probe
    ]
    assert len(probe_picks) == 9  # Step 217: the expansion worker and its transfer
    for node in probe_picks:  # every builder, scout and gas pick leaves gas workers alone
        assert node.args["filter"]["gathering_gas"] is False, node.id


# ---------------------------------------------------------------------------
# Placement screening: a gas structure is its geyser's 3x3, standing or ordered
# ---------------------------------------------------------------------------


@dataclass
class _PlacementView:
    """The three RuntimeView facts placement_candidates reads."""

    observation: Observation
    builds: tuple[tuple[str, Point], ...] = ()

    def in_flight_builds(self) -> tuple[tuple[str, Point], ...]:
        return self.builds

    def rejected_sites(self, structure: str) -> frozenset[Point]:
        return frozenset()


#: Integer-centred, so a Pylon two cells away clears a 2x2 screen but overlaps the 3x3.
_GAS_SITE = (36.0, 26.0)


def _pylon_candidates(
    *,
    structures: tuple[Entity, ...] = (),
    enemies: tuple[Entity, ...] = (),
    builds: tuple[tuple[str, Point], ...] = (),
) -> list[Point]:
    observation = Observation(
        game_loop=0,
        game_seconds=0.0,
        minerals=0,
        supply_used=0,
        supply_cap=0,
        own_units=(),
        own_structures=structures,
        visible_enemies=enemies,
        remembered_enemy_structures=(),
        start_location=START,
        enemy_start_locations=(),
        expansion_locations=(),
        map_center=START,
    )
    return placement_candidates(
        _PlacementView(observation, builds),  # type: ignore[arg-type]
        structure="Pylon",
        near=_GAS_SITE,
        radius_min=0.0,
        radius_max=5.0,
        require_power=False,
        resource_clearance=0.0,
        limit=500,
    )


def test_standing_and_ordered_gas_structures_are_screened_as_the_same_3x3() -> None:
    standing = _pylon_candidates(
        structures=(Entity(9000, "Assimilator", _GAS_SITE, 450.0, is_structure=True),)
    )
    ordered = _pylon_candidates(builds=(("Assimilator", _GAS_SITE),))
    enemy = _pylon_candidates(
        enemies=(Entity(9001, "Refinery", _GAS_SITE, 500.0, is_structure=True),)
    )
    assert standing == ordered == enemy and standing
    reach = (2 + GAS_FOOTPRINT) / 2  # a Pylon's 2x2 against the geyser's 3x3
    for x, y in standing:
        assert not (abs(x - _GAS_SITE[0]) < reach and abs(y - _GAS_SITE[1]) < reach)
    assert (34.0, 26.0) not in standing  # clear of a 2x2, inside the 3x3
