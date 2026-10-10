"""Jev v2 natural expansion through a persistent plan (Phase J2 Step 217, plan D2-D5).

Every match scenario runs the path a real match runs: :func:`jev.runner.run_match`
-> :class:`jev.bot.JevController` (whose asynchronous step has the adapter answer
the graph's SC2 path queries before the tick) -> ``JevBot.on_step`` ->
:class:`jev.sc2_adapter.Sc2Adapter` -> :class:`jev.runtime.JevRuntime` with the
packaged v2 policy and its :class:`jev.plans.PlanStore` ->
:class:`jev.sc2_adapter.BotAIPort`. Only the SC2 process is replaced:
:class:`_ExpansionWorld` extends the gas suite's burnysc2 world (real ``Unit``
objects from protobuf with real ``GameData``) with a natural and a third expansion
location, SC2 path queries (``Client.query_pathing``), walking probes and Zealots,
honest vision (an enemy out of sight is never reported), and a Nexus that the probe
places on arrival, builds and that adds supply.

Covered: the site is path-checked (Euclidean distance only orders the candidates)
and is the natural; one plan, one worker and the Nexus's minerals are held, the
reservation transfers to the build and is never charged twice; the second Nexus is
acknowledged, workers transfer to it and the plan completes; a lost worker, an
unreachable or blocked site, a threat at the site, a missing budget and a timeout
each reach a visible recovery or abort with deadlines, and every abort releases
its holds; never a third Nexus or a second open plan; second-base defense becomes
eligible once the Nexus stands. Plan-store, operation, adapter and validation
checks close the module.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from bots.jev.v1 import load_policy as load_v1_policy
from bots.jev.v2 import load_policy as load_v2_policy
from s2clientprotocol import data_pb2, sc2api_pb2
from sc2.bot_ai import BotAI
from sc2.data import ActionResult, Alliance, Attribute, DisplayType
from sc2.game_data import GameData
from sc2.game_state import ActionError
from sc2.ids.ability_id import AbilityId
from sc2.position import Point2
from sc2.unit import Unit

from jev.bot import JevController
from jev.contracts import (
    Entity,
    Event,
    JsonValue,
    LocationVision,
    Observation,
    Order,
    PathFact,
    Point,
    Policy,
)
from jev.operations import (
    MAX_BASES,
    MAX_EXPANSION_CANDIDATES,
    OPERATIONS,
    PYLON_POWER_RADIUS,
    ActionOp,
    ActionPlan,
    Binding,
    CompiledFilter,
    OpOutcome,
    PredicateOp,
    RuntimeView,
    Selection,
    SelectOp,
    Visit,
    distance,
    expansion_candidates,
)
from jev.plans import (
    MAX_PLAN_ABORTS,
    MAX_STAGE_RECOVERIES,
    PLAN_COOLDOWN_SECONDS,
    PLAN_NO_PROGRESS_SECONDS,
    PLAN_SITE_AVOID_SECONDS,
    PLAN_TIMEOUT_SECONDS,
    PLAN_YIELD_SECONDS,
    PlanKind,
    PlanRecord,
    PlanStore,
)
from jev.policy import PolicyError, parse_policy
from jev.runner import MatchOptions, run_match
from jev.runtime import JevRuntime
from jev.sc2_adapter import GAME_LOOPS_PER_SECOND, BotAIPort, Sc2Adapter
from test_jev_economy import (
    _BUILD_KIND,
    _POWERED_KINDS,
    _REMAPS,
    _TRAIN_KIND,
    BASE_BUILD,
    ENEMY_START,
    NEXUS,
    START,
    STEP_LOOPS,
    _Body,
    _Client,
    _Launcher,
)
from test_jev_macro import (
    _GAS_KINDS,
    _GATEWAY_SITES,
    ASSIMILATOR,
    GATEWAYS,
    GEYSER,
    GEYSER_SITES,
    PATCHES,
    PROBES,
    PYLON,
    _GasWorld,
    _play,
    _Run,
)
from test_jev_runtime import (
    CENTER,
    _gateway,
    _marine,
    _nexus,
    _node,
    _observation,
    _probe,
    _zealot,
)
from test_jev_sc2 import _game, _Port

NEXUS_BUILD = AbilityId.PROTOSSBUILD_NEXUS
#: The natural (22 from home, 30 by path) and a farther third (34 away, 40 by path).
NATURAL = (52.5, 30.5)
THIRD = (30.5, 64.5)
NATURAL_PATCHES = tuple(range(3100, 3108))
THIRD_PATCHES = tuple(range(3200, 3208))
ZEALOTS = tuple(range(6000, 6008))
#: The finished Cybernetics Core of the launched world (beside its Pylon, powered).
CORE = 1400
CORE_SITE = (27.5, 33.5)
PLAN_NODE = "expansion.start.go"
BUILD_NODE = "expansion.build"
MOVE_NODE = "expansion.move"
TRANSFER_NODE = "expansion.transfer.go"
EXPANSION_NODES = frozenset({MOVE_NODE, BUILD_NODE, "expansion.site_safe.retreat"})
#: Probe and Zealot speeds (game units per game second) and own vision radius.
_SPEED = {"Probe": 3.94, "Zealot": 3.15}
_SIGHT = 12.0
_NEXUS_COST = 400
_V2 = load_v2_policy().policy


def _parameter(name: str) -> float:
    value = _V2.parameters[name]
    assert isinstance(value, int | float) and not isinstance(value, bool)
    return float(value)


def _expansion_game_data() -> GameData:
    units = [
        data_pb2.UnitTypeData(
            unit_id=type_id.value,
            name=name,
            available=True,
            attributes=[Attribute.Structure.value] if structure else [],
        )
        for name, (type_id, structure) in _GAS_KINDS.items()
    ]
    plain = {*_BUILD_KIND, *_TRAIN_KIND, *_REMAPS.values(), ASSIMILATOR, NEXUS_BUILD}
    abilities = [data_pb2.AbilityData(ability_id=a.value, available=True) for a in plain] + [
        data_pb2.AbilityData(ability_id=a.value, remaps_to_ability_id=g.value, available=True)
        for a, g in _REMAPS.items()
    ]
    return GameData(sc2api_pb2.ResponseData(units=units, abilities=abilities))


_EXPANSION_GAME_DATA = _expansion_game_data()


class _PathingClient(_Client):
    """burnysc2's ``Client`` plus ``query_pathing``: the world's scripted distances
    (None: SC2 found no path); every query is recorded."""

    async def query_pathing(self, start: Point2, end: Point2) -> float | None:
        world = self._world
        assert isinstance(world, _ExpansionWorld)
        world.path_queries.append(((start.x, start.y), (end.x, end.y)))
        return world.paths.get((end.x, end.y))


class _ExpansionWorld(_GasWorld):
    """The gas suite's world plus what an expansion needs (see the module docstring).

    ``paths`` maps an expansion location to SC2's path distance from home (None or
    absent: no path). A probe ordered to build a Nexus walks to the site and places
    it on arrival (refused when the site is in ``blocked_on_arrival`` or minerals
    are short, reported as SC2's action error); the Nexus completes after
    ``nexus_seconds`` and adds 15 supply. Enemies are reported only within sight of
    an own body; a structure seen before comes back as a fogged snapshot. A point
    within 3 of a ``blind`` spot is never in sight (vision blocked, e.g. a cliff).
    """

    def __init__(
        self,
        bodies: list[_Body],
        *,
        minerals: int = 0,
        paths: dict[Point, float | None] | None = None,
        nexus_seconds: float = 10.0,
    ) -> None:
        self.paths: dict[Point, float | None] = (
            {NATURAL: 30.0, THIRD: 40.0} if paths is None else dict(paths)
        )
        self.path_queries: list[tuple[Point, Point]] = []
        self.nexus_seconds = nexus_seconds
        self.ever_seen: set[int] = set()
        self.blind: set[Point] = set()
        super().__init__(bodies, minerals=minerals)
        self.game_data = _EXPANSION_GAME_DATA
        self.expansion_locations_list = [Point2(p) for p in (START, ENEMY_START, NATURAL, THIRD)]
        self.client = _PathingClient(self)
        self.refresh()

    def sees(self, point: Point) -> bool:
        if any(distance(point, spot) < 3.0 for spot in self.blind):
            return False
        own = (b for b in self.bodies.values() if b.alliance == Alliance.Self)
        return any(distance(point, b.position) <= _SIGHT for b in own)

    def is_visible(self, pos: Point2) -> bool:
        return self.sees((pos.x, pos.y))

    def enemy(self, kind: str, position: Point) -> int:
        self._next_tag += 1
        self.bodies[self._next_tag] = _Body(self._next_tag, kind, position, alliance=Alliance.Enemy)
        self.refresh()
        return self._next_tag

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
                continue
            structure = _GAS_KINDS[body.kind][1]
            powered = body.kind in _POWERED_KINDS and any(
                distance(body.position, p) <= PYLON_POWER_RADIUS for p in pylons
            )
            proto = self._gas_proto(body, powered)
            if body.alliance == Alliance.Enemy:
                if self.sees(body.position):
                    self.ever_seen.add(body.tag)
                elif structure and body.tag in self.ever_seen:
                    proto.display_type = DisplayType.Snapshot.value
                else:
                    continue  # hidden-map information is never reported
            unit = Unit(proto, self, base_build=BASE_BUILD)
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

    def receive(self, command: Any) -> ActionResult:
        if command.ability != NEXUS_BUILD:
            return super().receive(command)
        self.sent.append((self.state.game_loop, command))
        body = self.bodies.get(command.unit.tag)
        if body is None or body.alliance != Alliance.Self:
            self.foreign.append(command.unit.tag)
            return ActionResult.NotSupported
        body.orders = [(NEXUS_BUILD, (command.target.x, command.target.y), 0.0)]
        return ActionResult.Success

    def advance(self, loops: int = STEP_LOOPS) -> None:
        dt = loops / GAME_LOOPS_PER_SECOND
        arrivals: list[tuple[int, Point]] = []
        moving = (AbilityId.MOVE_MOVE, AbilityId.ATTACK_ATTACK, NEXUS_BUILD)
        for body in list(self.bodies.values()):
            speed = _SPEED.get(body.kind)
            if body.alliance != Alliance.Self or not body.orders or speed is None:
                continue
            ability, target, _ = body.orders[0]
            if ability not in moving or not isinstance(target, tuple):
                continue
            if self._walk(body, target, speed * dt):
                if ability == NEXUS_BUILD:
                    arrivals.append((body.tag, target))
                else:
                    body.orders = []
        for nexus in self.own("Nexus"):
            if nexus.progress < 1.0:
                nexus.progress = min(1.0, nexus.progress + dt / self.nexus_seconds)
                if nexus.progress >= 1.0:
                    self.supply_cap += 15
        super().advance(loops)  # the clock, action errors, gas and mineral income
        for worker, site in arrivals:
            self._place_nexus(worker, site)
        self.refresh()

    @staticmethod
    def _walk(body: _Body, goal: Point, travel: float) -> bool:
        gap = distance(body.position, goal)
        if gap <= travel:
            body.position = goal
            return True
        ratio = travel / gap
        x, y = body.position
        body.position = (x + (goal[0] - x) * ratio, y + (goal[1] - y) * ratio)
        return False

    def _place_nexus(self, worker_tag: int, site: Point) -> None:
        worker = self.bodies.get(worker_tag)
        if worker is None or worker.orders[:1] != [(NEXUS_BUILD, site, 0.0)]:
            return  # the worker died or was re-ordered on the way
        worker.orders = []
        refusal: ActionResult | None = None
        if site in self.blocked_on_arrival:
            refusal = ActionResult.CantBuildLocationInvalid
        elif self.minerals < _NEXUS_COST:
            self.overspent.append(f"Nexus at loop {self.state.game_loop}")
            refusal = ActionResult.NotEnoughMinerals
        if refusal is not None:  # reported in the next observation's action errors
            self.state.action_errors = [ActionError(NEXUS_BUILD.value, worker_tag, refusal.value)]
            return
        self.minerals -= _NEXUS_COST
        self.add("Nexus", site, progress=0.05)


def _world(
    *,
    minerals: int = 0,
    paths: dict[Point, float | None] | None = None,
    nexus_seconds: float = 10.0,
    natural_nexus: bool = False,
    core: bool = True,
) -> _ExpansionWorld:
    """A launched one-base v2 Jev ready to expand: Nexus, Pylon, four powered Gateways,
    a powered Cybernetics Core (Step 218: D2 orders the Core prerequisite before the
    expansion, so the tech plan has already done its work here), an Assimilator with
    three gas workers, sixteen probes on eight main patches, eight ready Zealots at
    home and eight patches at each expansion; no income unless a scenario adds some.
    ``natural_nexus`` stands a finished second Nexus at the natural already; without
    ``core`` there is no Core yet (the tech plan's work is still to do)."""
    bodies = [
        _Body(NEXUS, "Nexus", START),
        _Body(PYLON, "Pylon", (24.0, 36.0)),
        *(_Body(tag, "Gateway", site) for tag, site in zip(GATEWAYS, _GATEWAY_SITES, strict=True)),
        *(
            _Body(tag, "MineralField", (26.5 + i, 22.5), alliance=Alliance.Neutral)
            for i, tag in enumerate(PATCHES)
        ),
        *(
            _Body(tag, "MineralField", (58.5, 26.5 + i), alliance=Alliance.Neutral)
            for i, tag in enumerate(NATURAL_PATCHES)
        ),
        *(
            _Body(tag, "MineralField", (26.5 + i, 70.5), alliance=Alliance.Neutral)
            for i, tag in enumerate(THIRD_PATCHES)
        ),
        *(
            _Body(tag, "VespeneGeyser", site, alliance=Alliance.Neutral)
            for tag, site in GEYSER_SITES.items()
        ),
        *(_Body(tag, "Zealot", (34.0 + (i % 4), 38.0 + i // 4)) for i, tag in enumerate(ZEALOTS)),
    ]
    if natural_nexus:
        bodies.append(_Body(1001, "Nexus", NATURAL))
    if core:
        bodies.append(_Body(CORE, "CyberneticsCore", CORE_SITE))
    for i, tag in enumerate(PROBES):
        orders = [(AbilityId.HARVEST_GATHER_PROBE, PATCHES[i % 8], 0.0)]
        bodies.append(_Body(tag, "Probe", (27.0 + i * 0.4, 26.0), orders=orders))
    world = _ExpansionWorld(bodies, minerals=minerals, paths=paths, nexus_seconds=nexus_seconds)
    building = world.add_assimilator(GEYSER, progress=1.0)
    for probe in PROBES[-3:]:
        world.bodies[probe].orders = [(AbilityId.HARVEST_GATHER_PROBE, building, 0.0)]
    world.refresh()
    return world


type _Hook = Callable[[_ExpansionWorld, JevController, int], None]


def _expand(world: _ExpansionWorld, steps: int, *hooks: _Hook) -> _Run:
    """Play ``world`` through the production runner with the packaged v2 policy."""

    def every(world: _GasWorld, controller: JevController, index: int) -> None:
        assert isinstance(world, _ExpansionWorld)
        for hook in hooks:
            hook(world, controller, index)

    return _play(world, steps, every)


def _fund(at: int, minerals: int = _NEXUS_COST) -> _Hook:
    def fund(world: _ExpansionWorld, controller: JevController, index: int) -> None:
        if index == at:
            world.minerals += minerals

    return fund


def _plan_events(run: _Run, plan: int | None = None) -> list[Event]:
    """Plan diagnostics (transitions and summaries), optionally of plan ``:plan:N`` only."""
    return [
        e
        for e in run.events
        if e.kind == "diagnostic"
        and "plan_id" in e.facts
        and (plan is None or str(e.facts["plan_id"]).endswith(f":plan:{plan}"))
    ]


def _transitions(run: _Run, plan: int | None = None) -> list[Event]:
    return [e for e in _plan_events(run, plan) if not e.facts["summary"]]


def _codes(run: _Run, plan: int | None = None) -> list[str]:
    return [str(e.facts["transition"]) for e in _transitions(run, plan)]


def _plans(run: _Run) -> tuple[PlanRecord, ...]:
    assert run.controller is not None
    return run.controller.runtime.plans()


def _abort(run: _Run, plan: int = 1) -> Event:
    (aborted,) = [e for e in _transitions(run, plan) if e.status == "failed"]
    assert aborted.facts["plan_status"] == "aborted"
    return aborted


def _workers_claimed(run: _Run, plan: int | None = None) -> list[int]:
    return [
        int(str(e.facts["actor_tag"]))
        for e in _transitions(run, plan)
        if e.facts["transition"] == "worker_assigned"
    ]


def _actor(event: Event) -> int:
    assert event.action is not None
    actors = event.action["actor_tags"]
    assert isinstance(actors, list) and len(actors) == 1
    return int(str(actors[0]))


ZEALOT_TRAIN = "production.zealots.train"
STALKER_TRAIN = "production.stalkers.train"


def _production_trains(run: _Run) -> list[Event]:
    """Every army train command (Step 218: the launched world's Core and gas let
    production buy Stalkers too, from the same minerals a plan may hold); tests that
    name a unit assert the node of each command."""
    return [*run.commands(STALKER_TRAIN), *run.commands(ZEALOT_TRAIN)]


def _assert_released(run: _Run, aborted: Event) -> None:
    """D3: an abort releases every hold at once -- the plan's actor and reservation
    (its record and the abort's ``released`` facts) -- and no task of the abandoned
    branch survives to retry a command."""
    record = next(r for r in _plans(run) if r.plan_id == aborted.facts["plan_id"])
    assert (record.actor_tag, record.reserved_minerals, record.reserved_vespene) == (None, 0, 0)
    released = aborted.facts["released"]
    assert isinstance(released, dict) and set(released) == {"actor_tag", "minerals", "vespene"}
    before = {
        e.task_id
        for e in run.events
        if e.kind == "command" and e.node_id in EXPANSION_NODES and e.game_loop < aborted.game_loop
    }
    retried = [
        e
        for e in run.events
        if e.kind == "command" and e.game_loop > aborted.game_loop and e.task_id in before
    ]
    assert retried == []  # no surviving task re-sent a command of the abandoned branch
    assert run.controller is not None
    for task in run.controller.runtime.active_tasks():
        assert task.id not in before


# ---------------------------------------------------------------------------
# The natural: path-checked, built, staffed -- one plan, one worker, one reservation
# ---------------------------------------------------------------------------


def test_v2_expands_to_the_path_checked_natural_and_transfers_workers() -> None:
    world = _world()
    run = _expand(world, 170, _fund(at=6))
    # Path-checked (D4): both bounded candidates were asked once each, from home only.
    assert sorted(world.path_queries) == sorted([(START, NATURAL), (START, THIRD)])
    pending = [e for e in _transitions(run) if e.facts["transition"] == "path_pending"]
    chosen = [e for e in _transitions(run) if e.facts["transition"] == "site_chosen"]
    assert pending and chosen and pending[0].game_loop < chosen[0].game_loop  # asked first
    assert chosen[0].facts["site"] == [NATURAL[0], NATURAL[1]]
    # One worker walked, scouted and built; nothing else commanded it while it was held.
    (worker,) = _workers_claimed(run)
    ((loop, build),) = run.sent(NEXUS_BUILD)
    assert (build.unit.tag, (build.target.x, build.target.y)) == (worker, NATURAL)
    assert NEXUS_BUILD in world.placement_abilities  # SC2 checked the placement
    record = _record(run)
    assert record.acknowledged_game_seconds is not None
    released_at = record.acknowledged_game_seconds * GAME_LOOPS_PER_SECOND
    held = [e for e in run.events if e.kind == "command" and _actor(e) == worker]
    # Released at acknowledgement (before that tick's lanes): until then only the plan's
    # nodes commanded it; from then on the economy may (it stands idle at the natural).
    assert {e.node_id for e in held if e.game_loop < released_at} == {MOVE_NODE, BUILD_NODE}
    (build_event,) = run.commands(BUILD_NODE)
    move_done = [e for e in run.tasks(MOVE_NODE, "succeeded")]
    assert move_done and move_done[0].game_loop <= build_event.game_loop  # scouted first
    # The reservation: held from the funding until the build, then transferred to it.
    issued = next(e for e in _transitions(run) if e.facts["transition"] == "issued")
    assert issued.node_id == BUILD_NODE and issued.facts["held_before"] == [_NEXUS_COST, 0]
    summaries = [
        e
        for e in _plan_events(run)
        if e.facts["summary"] and 6 * STEP_LOOPS < e.game_loop < build_event.game_loop
    ]
    assert summaries and {e.facts["reserved_minerals"] for e in summaries} == {_NEXUS_COST}
    assert _production_trains(run) == []  # production never spent the held minerals
    assert world.minerals == 0 and world.overspent == []  # charged exactly once
    # Acknowledged, constructed, staffed, completed; the second base is defensible.
    nexuses = world.own("Nexus")
    assert len(nexuses) == 2 and all(n.progress >= 1.0 for n in nexuses)
    # The Done-when order: the second Nexus is acknowledged (the plan releases its
    # worker; second-base defense applies), and workers transfer only once it is
    # ready (they cannot return cargo to an unfinished Nexus).
    acknowledged = next(e for e in _transitions(run) if e.facts["transition"] == "acknowledged")
    constructed = next(e for e in _transitions(run) if e.facts["transition"] == "constructed")
    assert acknowledged.facts["second_base_defense"] is True
    transfers = run.commands(TRANSFER_NODE)
    assert transfers and acknowledged.game_loop < constructed.game_loop
    assert min(e.game_loop for e in transfers) >= constructed.game_loop
    assert len(transfers) == int(_parameter("natural_transfer_workers"))
    assert {str(t.action["target"]) for t in transfers if t.action} <= {
        str(tag) for tag in NATURAL_PATCHES
    }
    natural_miners = [
        b for b in world.own("Probe") if b.orders and b.orders[0][1] in NATURAL_PATCHES
    ]
    assert len(natural_miners) == len(transfers)
    assert (record.status, record.reason_code, record.second_base_defense) == (
        "completed",
        "completed",
        True,
    )
    assert (record.site_path_rank, record.site_path_distance) == (0, 30.0)  # the nearest
    codes = _codes(run)
    for expected in ("started", "site_chosen", "worker_assigned", "issued", "acknowledged"):
        assert expected in codes
    assert codes[-1] == "completed" and codes.index("acknowledged") < codes.index("constructed")
    assert len(_plans(run)) == 1  # one plan: never a duplicate
    assert run.controller is not None
    assert record.plan_id == f"{run.controller.runtime.run_id}:plan:1"
    summary = run.controller.metrics.summary(end_game_seconds=world.seconds)
    assert summary["plan_aborts"] == {"status": "measured", "value": 0}  # D6 metric
    assert len(run.sent(NEXUS_BUILD)) == 1 and loop > 0  # never a third Nexus afterwards


def _record(run: _Run) -> PlanRecord:
    assert run.controller is not None
    record = run.controller.runtime.plan(PLAN_NODE)
    assert record is not None
    return record


def test_the_reservation_holds_only_the_nexus_cost_and_transfers_without_double_charge() -> None:
    """600 minerals: the plan holds 400, production after it may spend only the rest
    (two Zealots), and the build takes the hold over -- never charged twice."""
    world = _world()
    run = _expand(world, 60, _fund(at=6, minerals=600))
    trains = _production_trains(run)
    # 600 - 400 held = two Zealots, never a third (no gas yet: the Stalker branch waits).
    assert [e.node_id for e in trains] == [ZEALOT_TRAIN, ZEALOT_TRAIN]
    ((loop, _),) = run.sent(NEXUS_BUILD)
    assert all(e.game_loop < loop for e in trains)
    assert world.overspent == [] and world.minerals == 0
    holds = [e.facts["reserved_minerals"] for e in _plan_events(run) if e.facts["summary"]]
    assert max(int(str(held)) for held in holds) == _NEXUS_COST  # up to its cost, no more


def test_v2_replaces_a_lost_worker_and_still_expands() -> None:
    world = _world()
    killed: list[int] = []

    def kill_once(world: _ExpansionWorld, controller: JevController, index: int) -> None:
        record = controller.runtime.plan(PLAN_NODE)
        if record is not None and record.actor_tag is not None and not killed and index > 8:
            killed.append(record.actor_tag)
            world.kill(record.actor_tag)

    run = _expand(world, 200, _fund(at=6), kill_once)
    first, second = _workers_claimed(run)
    assert first == killed[0] and second != first
    (lost,) = [e for e in _transitions(run) if e.facts["transition"] == "worker_lost"]
    assert lost.status == "warning" and lost.facts["recoveries"] == 1
    assert lost.facts["stage_node_id"] == MOVE_NODE
    (failed,) = run.tasks(MOVE_NODE, "failed")
    assert failed.facts["failure_cause"] == "lost"
    ((_, build),) = run.sent(NEXUS_BUILD)
    assert build.unit.tag == second  # the replacement built it
    assert _record(run).status == "completed"


def test_v2_aborts_after_three_worker_recoveries_and_releases_every_hold() -> None:
    world = _world()
    killed: list[int] = []
    claimed_at: dict[int, int] = {}

    def kill_each(world: _ExpansionWorld, controller: JevController, index: int) -> None:
        record = controller.runtime.plan(PLAN_NODE)
        if record is None or not record.is_open or record.actor_tag is None:
            return
        tag = record.actor_tag
        claimed_at.setdefault(tag, index)
        if len(killed) < MAX_STAGE_RECOVERIES + 1 and index - claimed_at[tag] >= 3:
            killed.append(tag)
            world.kill(tag)

    run = _expand(world, 120, _fund(at=2), kill_each)
    losses = [e for e in _transitions(run, 1) if e.facts["transition"] == "worker_lost"]
    assert [e.facts["recoveries"] for e in losses] == [1, 2, 3]
    aborted = _abort(run)
    assert aborted.facts["reason_code"] == "recoveries_exhausted"
    assert "worker_lost" in aborted.reason
    released = aborted.facts["released"]
    assert isinstance(released, dict) and released["minerals"] == _NEXUS_COST
    # The 400 held through four walks are free at once: production spends them now.
    trains = _production_trains(run)
    assert trains and min(e.game_loop for e in trains) == aborted.game_loop
    assert run.sent(NEXUS_BUILD) == []
    _assert_released(run, aborted)
    # The slot cools down: no second plan before the cooldown ends.
    cooldown = aborted.facts["cooldown_until"]
    assert isinstance(cooldown, float)
    assert cooldown == pytest.approx(aborted.game_seconds + PLAN_COOLDOWN_SECONDS)
    refused = [
        e
        for e in run.node_events(PLAN_NODE)
        if e.status == "failure" and aborted.game_loop < e.game_loop
    ]
    # Then (Step 218) one turn for the tech plan, which this world -- its Core stands --
    # never takes: the window lapses and the expansion may start again.
    yielded = [e for e in refused if "yields to tech" in e.reason]
    assert refused and all(e in yielded or "cools down" in e.reason for e in refused)
    assert yielded  # the turn rule's window did apply
    assert all(cooldown <= e.game_seconds < cooldown + PLAN_YIELD_SECONDS for e in yielded)
    (second,) = [r for r in _plans(run) if r.plan_id.endswith(":plan:2")]
    assert second.kind == "expand"  # the expansion did restart, once the window lapsed
    assert second.started_game_seconds >= cooldown + PLAN_YIELD_SECONDS - 1e-9
    assert run.controller is not None
    summary = run.controller.metrics.summary(end_game_seconds=world.seconds)
    assert summary["plan_aborts"] == {"status": "measured", "value": 1}  # D6 metric


# ---------------------------------------------------------------------------
# Unreachable and blocked sites
# ---------------------------------------------------------------------------


def test_v2_skips_an_unreachable_natural_for_the_nearest_reachable_site() -> None:
    world = _world(paths={NATURAL: None, THIRD: 40.0})
    run = _expand(world, 200, _fund(at=6))
    (chosen,) = [e for e in run.node_events("expansion.site") if e.facts.get("site")][:1]
    assert chosen.facts["skipped"] == {"unreachable": 1}  # Euclidean-nearest, but no path
    ((_, build),) = run.sent(NEXUS_BUILD)
    assert (build.target.x, build.target.y) == THIRD
    assert _record(run).status == "completed" and _record(run).site == THIRD


def test_v2_aborts_and_stops_expanding_when_no_site_is_reachable() -> None:
    world = _world(paths={NATURAL: None, THIRD: None})
    run = _expand(world, 120, _fund(at=6))
    aborted = _abort(run)
    assert (aborted.facts["reason_code"], aborted.facts["disabled"]) == ("site_unreachable", True)
    assert _workers_claimed(run) == [] and run.sent(NEXUS_BUILD) == []
    assert len(world.path_queries) == 2  # static map paths: never asked again
    assert len(_plans(run)) == 1  # the kind is disabled: no futile retries
    refusals = [e for e in run.node_events(PLAN_NODE) if e.game_loop > aborted.game_loop]
    assert refusals and all("disabled" in e.reason for e in refusals if e.status == "failure")
    _assert_released(run, aborted)


def test_v2_an_exhausting_site_change_avoids_the_old_site_not_the_new_one() -> None:
    """The plan waits at the natural without minerals until its stage's recoveries
    are spent; then an enemy structure takes the natural. The re-choice is the
    exhausting recovery: the abort must avoid the occupied natural that caused it --
    never the third it was about to take -- so the next plan may take the third."""
    world = _world()  # no income: the plan spends its three no-progress recoveries
    taken: list[int] = []

    def occupy(world: _ExpansionWorld, controller: JevController, index: int) -> None:
        record = controller.runtime.plan(PLAN_NODE)
        exhausted = record is not None and record.is_open and record.recoveries == 3
        if exhausted and not taken:
            taken.append(world.enemy("Pylon", NATURAL))  # seen by the waiting worker

    run = _expand(world, 340, occupy)
    assert taken  # the natural was taken with every recovery spent
    aborted = _abort(run)
    assert aborted.facts["reason_code"] == "recoveries_exhausted"
    assert "site_occupied" in aborted.reason
    assert aborted.facts["avoided_sites"] == [[NATURAL[0], NATURAL[1]]]  # the old site only
    assert aborted.facts["site"] == [NATURAL[0], NATURAL[1]]  # it never took the third
    chosen = [
        e.facts["site"] for e in _transitions(run, 1) if e.facts["transition"] == "site_chosen"
    ]
    assert chosen == [[NATURAL[0], NATURAL[1]]]
    second = _record_of(run, 2)
    assert second.avoided_sites == (NATURAL,) and second.site == THIRD  # the new site is free


def test_v2_a_refused_site_is_given_up_and_the_plan_rechooses_within_the_bound() -> None:
    """SC2 refuses the Nexus at the natural every time: after the build task's own
    bounded retries the plan gives that site up at once (a visible recovery) and
    re-chooses -- the third, 40 by path, within 1.5x the nearest reachable (30) --
    instead of retrying the identical blocked site."""
    world = _world()
    world.illegal = lambda site: site == NATURAL
    run = _expand(world, 300, _fund(at=6))
    (refused,) = run.tasks(BUILD_NODE, "failed")
    assert refused.facts["failure_cause"] == "rejected"  # one task: its retries, then failed
    (blocked,) = [e for e in _transitions(run) if e.facts["transition"] == "site_blocked"]
    assert blocked.status == "warning" and "given up" in blocked.reason
    assert blocked.facts["site"] is None and blocked.facts["avoided_sites"] == [list(NATURAL)]
    chosen = [e for e in _transitions(run) if e.facts["transition"] == "site_chosen"]
    assert [(e.facts["site"], e.facts["site_path_rank"]) for e in chosen] == [
        ([NATURAL[0], NATURAL[1]], 0),
        ([THIRD[0], THIRD[1]], 1),
    ]
    ((_, build),) = run.sent(NEXUS_BUILD)  # the refused natural was never re-sent
    assert (build.target.x, build.target.y) == THIRD
    record = _record(run)
    assert (record.status, record.site, record.site_path_rank, record.site_path_distance) == (
        "completed",
        THIRD,
        1,
        40.0,
    )
    assert record.avoided_sites == (NATURAL,)


def test_v2_never_walks_to_a_far_base_and_stops_after_the_abort_limit() -> None:
    """The natural is refused and the only alternative is 2x its path (beyond the
    1.5x bound): the plan waits visibly instead of walking to a far base, aborts on
    its deadlines, and after the abort limit the kind is disabled for the match --
    no endless series of plans."""
    world = _world(paths={NATURAL: 30.0, THIRD: 60.0})
    world.illegal = lambda site: site == NATURAL
    run = _expand(world, 1100, _fund(at=6))
    walks = run.commands(MOVE_NODE)
    natural = [NATURAL[0], NATURAL[1]]
    assert walks and all(e.action is not None and e.action["target"] == natural for e in walks)
    assert run.sent(NEXUS_BUILD) == []
    beyond = [
        e
        for e in run.node_events("expansion.site")
        if e.status == "failure" and e.facts.get("beyond_bound") == 1
    ]
    assert beyond and "not taken" in beyond[0].reason
    assert beyond[0].facts["nearest_path"] == 30.0 and beyond[0].facts["path_bound"] == 45.0
    aborts = [e for e in _transitions(run) if e.status == "failed"]
    assert len(aborts) == MAX_PLAN_ABORTS
    assert [e.facts["kind_aborts"] for e in aborts] == list(range(1, MAX_PLAN_ABORTS + 1))
    assert [e.facts["disabled"] for e in aborts] == [False] * (MAX_PLAN_ABORTS - 1) + [True]
    assert aborts[-1].facts["disabled_by"] == "abort_limit"
    for aborted in aborts:
        _assert_released(run, aborted)
    after = [e for e in run.node_events(PLAN_NODE) if e.game_loop > aborts[-1].game_loop]
    assert after and all(
        e.status == "failure" and "disabled for this match (abort_limit)" in e.reason for e in after
    )
    assert len(_plans(run)) == MAX_PLAN_ABORTS
    assert run.controller is not None
    summary = run.controller.metrics.summary(end_game_seconds=world.seconds)
    assert summary["plan_aborts"] == {"status": "measured", "value": MAX_PLAN_ABORTS}


# ---------------------------------------------------------------------------
# A threat at the site, a missing budget and a timeout
# ---------------------------------------------------------------------------


def test_v2_threat_at_the_site_pulls_the_worker_back_waits_then_aborts() -> None:
    """Unknown is not safe: the plan's worker scouts the site; once it sees a threat
    there the worker comes home, the plan waits holding no minerals, the delay is
    bounded by the plan's deadlines, and the next plan takes the alternative."""
    world = _world()
    marine = world.enemy("Marine", (NATURAL[0] + 3.0, NATURAL[1] + 4.0))  # unseen yet

    run = _expand(world, 480, _fund(at=6))
    plan1 = _transitions(run, 1)
    retreat = run.commands("expansion.site_safe.retreat")
    assert retreat  # the worker was pulled back from the threat it saw
    (worker,) = _workers_claimed(run, 1)
    held = [
        e
        for e in run.events
        if e.kind == "command" and _actor(e) == worker and e.game_loop < _abort(run).game_loop
    ]
    assert {e.node_id for e in held} == {MOVE_NODE, "expansion.site_safe.retreat"}  # owned
    waiting = [e for e in plan1 if e.facts["reason_code"] == "site_threatened"]
    assert waiting and all(e.facts["plan_status"] == "waiting" for e in waiting)
    assert all(e.facts["reserved_minerals"] == 0 for e in waiting)  # no hold while threatened
    assert run.sent(NEXUS_BUILD) == [] or all(
        (c.target.x, c.target.y) != NATURAL for _, c in run.sent(NEXUS_BUILD)
    )  # never built into the threat
    recoveries = [e for e in plan1 if e.status == "warning"]
    assert recoveries and all(e.facts["transition"] == "no_progress" for e in recoveries)
    aborted = _abort(run)
    assert aborted.facts["reason_code"] == "timeout"
    assert aborted.game_seconds == pytest.approx(
        _record_of(run, 1).started_game_seconds + PLAN_TIMEOUT_SECONDS, abs=0.4
    )
    assert aborted.facts["avoided_sites"] == [[NATURAL[0], NATURAL[1]]]
    _assert_released(run, aborted)
    second = _record_of(run, 2)
    assert second.site == THIRD  # the alternative, while the natural is avoided
    chosen = [
        e.facts["site"] for e in _transitions(run, 2) if e.facts["transition"] == "site_chosen"
    ]
    assert chosen == [[THIRD[0], THIRD[1]]]  # the avoided natural is never even chosen
    assert marine in world.ever_seen


def _record_of(run: _Run, plan: int) -> PlanRecord:
    return next(r for r in _plans(run) if r.plan_id.endswith(f":plan:{plan}"))


def test_v2_missing_budget_waits_recovers_then_aborts_releasing_its_reservation() -> None:
    world = _world()
    run = _expand(world, 360, _fund(at=6, minerals=250))  # short of a Nexus, no income
    plan1 = _transitions(run, 1)
    short = [e for e in plan1 if e.facts["reason_code"] == "budget_short"]
    assert short and short[0].node_id == BUILD_NODE
    assert {e.facts["reserved_minerals"] for e in short if e.status == "running"} <= {0, 250}
    recoveries = [e for e in plan1 if e.status == "warning"]
    assert [e.facts["recoveries"] for e in recoveries] == [1, 2, 3]
    assert all("budget_short" in e.reason for e in recoveries)
    gaps = [
        b.game_seconds - a.game_seconds for a, b in zip(recoveries, recoveries[1:], strict=False)
    ]
    assert all(gap == pytest.approx(PLAN_NO_PROGRESS_SECONDS, abs=0.4) for gap in gaps)
    aborted = _abort(run)
    assert aborted.facts["reason_code"] == "timeout"
    released = aborted.facts["released"]
    assert isinstance(released, dict) and released["minerals"] == 250
    # D3 single ownership: its worker idled at the site for two minutes, yet no other
    # lane (the economy takes idle probes) commanded it until the abort released it.
    (worker,) = _workers_claimed(run, 1)
    assert released["actor_tag"] == str(worker)
    held = [e for e in run.events if e.kind == "command" and _actor(e) == worker]
    assert {e.node_id for e in held if e.game_loop < aborted.game_loop} == {MOVE_NODE}
    trains = _production_trains(run)
    # The released 250 buy exactly two Stalkers (2 x 125; two minutes of gas income
    # pays their 2 x 50) on the abort tick -- never a third unit.
    assert [e.node_id for e in trains] == [STALKER_TRAIN, STALKER_TRAIN]
    assert {e.game_loop for e in trains} == {aborted.game_loop}
    assert run.sent(NEXUS_BUILD) == []
    _assert_released(run, aborted)


def test_v2_a_plan_that_keeps_saving_but_never_builds_times_out() -> None:
    """Income too slow for a Nexus within the plan timeout: the reservation keeps
    growing (progress, so no recovery is spent), and the deadline still ends it."""
    world = _world()

    def trickle(world: _ExpansionWorld, controller: JevController, index: int) -> None:
        if index >= 2:
            world.minerals += 1

    run = _expand(world, 345, trickle)
    plan1 = _transitions(run, 1)
    assert [e for e in plan1 if e.status == "warning"] == []  # always progressing
    aborted = _abort(run)
    assert aborted.facts["reason_code"] == "timeout"
    record = _record_of(run, 1)
    assert aborted.game_seconds == pytest.approx(
        record.started_game_seconds + PLAN_TIMEOUT_SECONDS, abs=0.4
    )
    released = aborted.facts["released"]
    assert isinstance(released, dict) and 0 < int(str(released["minerals"])) < _NEXUS_COST
    assert run.sent(NEXUS_BUILD) == []
    _assert_released(run, aborted)


# ---------------------------------------------------------------------------
# Never a third base; second-base defense
# ---------------------------------------------------------------------------


def test_v2_never_starts_a_plan_or_a_nexus_with_two_bases() -> None:
    world = _world(natural_nexus=True, minerals=2000)
    run = _expand(world, 80)
    assert _plans(run) == () and run.sent(NEXUS_BUILD) == []
    assert world.path_queries == []  # no plan, no question to SC2
    one_base = run.node_events("expansion.start.one_base")
    assert one_base and {e.status for e in one_base} == {"failure"}


def test_v2_defends_the_second_base_once_its_nexus_stands() -> None:
    world = _world(natural_nexus=True)
    marine: list[int] = []

    def raid(world: _ExpansionWorld, controller: JevController, index: int) -> None:
        if index == 3:
            marine.append(world.enemy("Marine", (NATURAL[0] + 4.0, NATURAL[1] + 2.0)))

    run = _expand(world, 12, raid)
    attacks = run.commands("army.defend.attack")
    assert attacks and {str(e.action["target"]) for e in attacks if e.action} == {str(marine[0])}
    natural = [e for e in run.node_events("army.defend.natural") if e.status == "success"]
    assert natural  # the second-base branch chose the target
    raided = 3 * STEP_LOOPS
    assert [e for e in run.commands("reserve.gather") if e.game_loop >= raided] == []


def test_second_base_defense_follows_the_live_nexus_after_the_plan_ends() -> None:
    """The flag is live state, not a latch: once the completed plan's Nexus is
    destroyed it turns false (a visible transition), so later readers never see a
    stale second base."""
    world = _world()
    destroyed: list[int] = []

    def destroy(world: _ExpansionWorld, controller: JevController, index: int) -> None:
        record = controller.runtime.plan(PLAN_NODE)
        if record is not None and record.status == "completed" and not destroyed:
            natural = next(b for b in world.own("Nexus") if b.position == NATURAL)
            destroyed.append(world.state.game_loop)
            world.kill(natural.tag)

    run = _expand(world, 230, _fund(at=6), destroy)
    first = _record_of(run, 1)
    assert first.status == "completed" and not first.second_base_defense
    (lost,) = [e for e in _transitions(run, 1) if e.facts["transition"] == "nexus_lost"]
    assert lost.status == "warning" and lost.facts["second_base_defense"] is False
    assert lost.game_loop >= destroyed[0]


def test_v2_builds_only_once_the_site_is_in_its_own_sight() -> None:
    """Unknown is not safe: with the natural's vision blocked the worker arrives,
    the Nexus's minerals are held, yet nothing is built while the site is unseen;
    the build follows the first tick it is in sight (safety rechecked on it)."""
    world = _world()
    world.blind.add(NATURAL)
    lifted = 80

    def lift(world: _ExpansionWorld, controller: JevController, index: int) -> None:
        if index == lifted:
            world.blind.clear()

    run = _expand(world, 200, _fund(at=6), lift)
    unscouted = [e for e in run.node_events("expansion.scouted") if e.status == "failure"]
    assert unscouted and "never seen" in unscouted[0].reason
    assert all(e.game_loop < lifted * STEP_LOOPS for e in unscouted)
    waiting = [e for e in _transitions(run) if e.facts["reason_code"] == "site_unscouted"]
    assert waiting and waiting[0].facts["plan_status"] == "waiting"
    ((loop, _),) = run.sent(NEXUS_BUILD)
    assert loop >= lifted * STEP_LOOPS
    assert _record(run).status == "completed"


def test_v2_a_stale_sighting_forces_a_fresh_look_before_the_build() -> None:
    """The worker saw the site on arrival, but the minerals came only after its
    vision was lost: that sighting is stale, so the build waits for a fresh one."""
    world = _world()

    def hook(world: _ExpansionWorld, controller: JevController, index: int) -> None:
        if index == 40:
            world.blind.add(NATURAL)
        if index == 45:
            world.minerals += _NEXUS_COST
        if index == 90:
            world.blind.clear()

    run = _expand(world, 200, hook)
    stale = [e for e in run.node_events("expansion.scouted") if e.status == "failure"]
    assert stale and "stale" in stale[0].reason
    assert stale[0].facts["last_seen_game_seconds"] is not None  # it had been seen
    ((loop, _),) = run.sent(NEXUS_BUILD)
    assert loop >= 90 * STEP_LOOPS


def _second_base_tick(*, natural: bool) -> list[str]:
    """One v2 tick: eight Zealots at home, a Marine 26 from the main (outside its
    defense radius) and 4 from the natural; ``natural`` stands a second Nexus there."""
    runtime = JevRuntime(_V2, run_id=uuid.uuid4().hex)
    structures = [_nexus(), *(_gateway(1100 + i, s) for i, s in enumerate(_GATEWAY_SITES))]
    if natural:
        structures.append(
            Entity(1001, "Nexus", NATURAL, 1000, is_structure=True, ready=True, idle=True)
        )
    zealots = tuple(_zealot(tag, (34.0, 38.0)) for tag in ZEALOTS)
    result = runtime.tick(
        _observation(
            10.0,
            units=zealots,
            structures=tuple(structures),
            enemies=(_marine(9000, (NATURAL[0] + 4.0, NATURAL[1])),),
            supply_used=40,
            supply_cap=100,
        )
    )
    return [c.node_id for c in result.commands]


def test_second_base_defense_is_eligible_only_once_a_second_nexus_stands() -> None:
    assert "army.defend.attack" not in _second_base_tick(natural=False)
    assert "army.defend.attack" in _second_base_tick(natural=True)


# ---------------------------------------------------------------------------
# Step 218: the expansion after the tech prerequisite, and never starved by it
# ---------------------------------------------------------------------------


def _macro_plans(run: _Run, kind: str) -> list[PlanRecord]:
    """The run's kept plans of ``kind``, oldest first."""
    return [record for record in _plans(run) if record.kind == kind]


def _funding(prices: dict[str, int]) -> _Hook:
    """Fund each macro plan with its kind's price on the step it is first seen open."""
    funded: set[str] = set()

    def fund(world: _ExpansionWorld, controller: JevController, index: int) -> None:
        for record in controller.runtime.plans():
            if record.is_open and record.plan_id not in funded:
                funded.add(record.plan_id)
                world.minerals += prices.get(record.kind, 0)

    return fund


def test_v2_without_a_core_the_expansion_waits_for_the_tech_plan_then_expands() -> None:
    """D2 fallback order from the expansion's side: a launched army ready to expand
    but without a Core. The tech plan takes the macro slot first (its lane runs first);
    every tick the expansion tries, the slot refuses it while the tech plan is open --
    through the Core's construction -- and the expansion opens only once the tech plan
    completed, then takes the natural."""
    world = _world(core=False)
    ready: dict[int, bool] = {}

    def mark(world: _ExpansionWorld, controller: JevController, index: int) -> None:
        ready[world.state.game_loop] = any(b.progress >= 1.0 for b in world.own("CyberneticsCore"))

    run = _expand(world, 230, mark, _funding({"tech": 150, "expand": _NEXUS_COST}))
    core_ready = min(loop for loop, ok in ready.items() if ok)
    starts = [e for e in run.events if e.kind == "node" and e.node_id == "expansion.start.go"]
    before = [e for e in starts if e.game_loop < core_ready]
    assert before and {e.status for e in before} == {"failure"}  # waits while tech runs
    assert all("never a duplicate plan" in e.reason for e in before)  # refused by the slot
    placed = min(
        loop for loop, c in world.sent if c.ability == AbilityId.PROTOSSBUILD_CYBERNETICSCORE
    )
    assert placed < core_ready  # it waited through the Core's construction, too
    (tech,) = _macro_plans(run, "tech")
    (expansion,) = _macro_plans(run, "expand")
    assert tech.status == expansion.status == "completed"
    assert tech.finished_game_seconds is not None
    assert expansion.started_game_seconds >= tech.finished_game_seconds
    assert expansion.started_game_seconds * GAME_LOOPS_PER_SECOND >= core_ready - 1e-6
    assert len(world.own("Nexus")) == 2 and world.overspent == []


def _saboteur(
    doomed: dict[str, set[int]], prices: dict[str, dict[int, int]] | None = None
) -> _Hook:
    """Make the run's ``n``th plan of each kind in ``doomed`` fail fast, for a
    non-static reason, and fund the ``n``th plan of each kind in ``prices`` as it
    opens. A doomed tech plan loses its acknowledged Core (``structure_lost``); a
    doomed expansion plan loses every worker it claims (``recoveries_exhausted``)."""
    seen: dict[str, list[str]] = {"tech": [], "expand": []}
    claimed_at: dict[int, int] = {}
    prices = prices or {}

    def hook(world: _ExpansionWorld, controller: JevController, index: int) -> None:
        for record in controller.runtime.plans():
            if not record.is_open or record.kind not in seen:
                continue
            ids = seen[record.kind]
            if record.plan_id not in ids:
                ids.append(record.plan_id)
                world.minerals += prices.get(record.kind, {}).get(len(ids), 0)
            if len(ids) not in doomed.get(record.kind, set()):
                continue
            if record.kind == "tech" and record.acknowledged_game_seconds is not None:
                for core in world.own("CyberneticsCore"):
                    if core.progress < 1.0:
                        world.kill(core.tag)
            elif record.kind == "expand" and record.actor_tag is not None:
                tag = record.actor_tag
                claimed_at.setdefault(tag, index)
                if index - claimed_at[tag] >= 3 and tag in world.bodies:
                    world.kill(tag)

    return hook


def _macro_aborts(run: _Run) -> list[Event]:
    """Every macro plan's abort event, in order."""
    return [
        e
        for e in _transitions(run)
        if e.status == "failed" and e.facts["plan_kind"] in ("tech", "expand")
    ]


def test_v2_repeated_failures_alternate_tech_and_expansion_turns_until_both_are_disabled() -> None:
    """Both macro kinds keep failing for non-static reasons. The shared slot gives each
    kind exactly one turn after the other's abort -- whichever lane runs first -- so
    the kinds alternate strictly until each reaches the abort limit; neither ever takes
    two turns in a row while the other is still enabled (Step 218: the slot's turn rule,
    not lane order or a latch in the graph)."""
    world = _world(core=False)
    hook = _saboteur({"tech": {1, 2, 3}, "expand": {1, 2, 3}}, {"tech": {1: 150, 2: 150, 3: 150}})
    run = _expand(world, 420, hook)
    plans = sorted(
        (r for r in _plans(run) if r.kind in ("tech", "expand")),
        key=lambda r: r.started_game_seconds,
    )
    assert [r.kind for r in plans] == ["tech", "expand"] * MAX_PLAN_ABORTS
    assert all(r.status == "aborted" for r in plans)
    aborts = _macro_aborts(run)
    assert [e.facts["reason_code"] for e in aborts] == [
        "structure_lost",
        "recoveries_exhausted",
    ] * MAX_PLAN_ABORTS  # non-static: no kind is disabled before its abort limit
    # Each abort owes the next turn to the other kind, until that kind is disabled.
    assert [e.facts["yield_to"] for e in aborts] == [["expand"], ["tech"]] * 2 + [["expand"], []]
    # Within one turn: each plan opens at the first tick after the previous cooldown.
    tick = STEP_LOOPS / GAME_LOOPS_PER_SECOND
    for aborted, following in zip(aborts, plans[1:], strict=False):
        cooldown = aborted.facts["cooldown_until"]
        assert isinstance(cooldown, float)
        assert cooldown <= following.started_game_seconds <= cooldown + tick
    assert run.controller is not None
    (macro, _) = run.controller.runtime.plan_slots()
    assert dict(macro.disabled) == {"expand": "abort_limit", "tech": "abort_limit"}


def test_v2_a_failing_expansion_hands_the_slot_to_tech_then_both_complete() -> None:
    """The mirror of the tech-starves-expansion case: the expansion fails after a tech
    failure, and the slot's next turn is the tech plan's -- not the expansion's again
    -- which then builds the Core; the expansion's next turn takes the natural."""
    world = _world(core=False)
    hook = _saboteur(
        {"tech": {1}, "expand": {1}},
        {"tech": {1: 150, 2: 150}, "expand": {2: _NEXUS_COST}},
    )
    run = _expand(world, 380, hook)
    plans = sorted(
        (r for r in _plans(run) if r.kind in ("tech", "expand")),
        key=lambda r: r.started_game_seconds,
    )
    assert [(r.kind, r.status) for r in plans] == [
        ("tech", "aborted"),
        ("expand", "aborted"),
        ("tech", "completed"),
        ("expand", "completed"),
    ]
    failed_expansion = plans[1].finished_game_seconds
    assert failed_expansion is not None
    turn = failed_expansion + PLAN_COOLDOWN_SECONDS + STEP_LOOPS / GAME_LOOPS_PER_SECOND
    assert plans[2].started_game_seconds <= turn  # tech's turn, at once
    tech_refusals = [
        e
        for e in run.node_events("tech.start.go")
        if e.status == "failure" and "yields" in e.reason
    ]
    assert tech_refusals  # after its own abort, tech was told the expansion's turn came first
    assert len(world.own("Nexus")) == 2 and len(world.own("CyberneticsCore")) == 1
    assert all(b.progress >= 1.0 for b in world.own("CyberneticsCore"))
    assert world.overspent == []


def test_v2_a_failing_tech_plan_defers_the_expansion_by_at_most_one_plan() -> None:
    """The tech plan cannot afford its Core (no minerals, no income) and times out. The
    expansion -- whose lane runs before the tech lane -- takes the macro slot as soon
    as the slot has cooled down, not after the tech kind is disabled; the tech plan is
    refused, visibly, while the expansion is open and restarts only after it."""
    world = _world(core=False)
    run = _expand(world, 520, _funding({"expand": _NEXUS_COST}))
    tech = _macro_plans(run, "tech")
    (expansion,) = _macro_plans(run, "expand")
    first = tech[0]
    assert (first.status, first.reason_code) == ("aborted", "timeout")
    assert first.finished_game_seconds is not None
    # The bound: one tech plan's deadline and one cooldown (plus a tick), never more.
    bound = first.started_game_seconds + PLAN_TIMEOUT_SECONDS + PLAN_COOLDOWN_SECONDS + 0.5
    assert first.finished_game_seconds <= expansion.started_game_seconds <= bound
    assert expansion.status == "completed" and len(world.own("Nexus")) == 2
    done = expansion.finished_game_seconds
    assert done is not None
    assert len(tech) == 2 and tech[1].started_game_seconds >= done  # tech restarts after it
    refused = [
        e
        for e in run.events
        if e.kind == "node"
        and e.node_id == "tech.start.go"
        and e.status == "failure"
        and expansion.started_game_seconds <= e.game_seconds < done
    ]
    turn = "yields to expand"  # the slot's turn rule, on the tick the expansion starts
    assert refused and all(
        "never a duplicate plan" in e.reason or turn in e.reason for e in refused
    )
    assert run.controller is not None
    assert run.controller.runtime.plan_aborts() == 1  # tech was never disabled
    assert world.overspent == []


# ---------------------------------------------------------------------------
# The plan store: one per slot, bounded recovery, deadlines, release
# ---------------------------------------------------------------------------

_RUN = "0123456789ab4def8123456789abcdef"


def _store_with_plan() -> PlanStore:
    store = PlanStore(_RUN, kinds=("expand",))  # Step 217's shape: expansion plans only
    record, reason = store.start("lane.start", "lane", "expand", 10.0)
    assert record is not None, reason
    return store


def test_plan_ids_are_monotonic_and_one_plan_per_slot() -> None:
    store = _store_with_plan()
    record = store.record("lane.start")
    assert record is not None and record.plan_id == f"{_RUN}:plan:1"
    macro: tuple[tuple[str, PlanKind], ...] = (("lane.start", "expand"), ("other.start", "tech"))
    for key, kind in macro:
        again, reason = store.start(key, "other", kind, 11.0)
        assert again is None and "never a duplicate plan" in reason
    army, _ = store.start("army.start", "army", "pressure", 11.0)
    assert army is not None and army.plan_id == f"{_RUN}:plan:2"  # the army slot is free
    assert store.complete("lane.start", 12.0) is None
    after, _ = store.start("other.start", "other", "tech", 12.0)  # no cooldown on success
    assert after is not None and after.plan_id == f"{_RUN}:plan:3"


def test_the_fourth_recovery_aborts_releases_and_cools_the_slot_down() -> None:
    store = _store_with_plan()
    store.claim_actor("lane.start", 2000)
    store.hold("lane.start", 300, 25)
    store.after_lane(
        "lane.start",
        stage="lane.build",
        rank=5,
        failed=True,
        waiting=False,
        code="budget_short",
        reason="insufficient minerals",
        now=10.5,
    )
    for attempt in range(1, MAX_STAGE_RECOVERIES + 1):
        store.begin_tick(10.5 + attempt * PLAN_NO_PROGRESS_SECONDS, frozenset({2000}), {})
        record = store.record("lane.start")
        assert record is not None and record.recoveries == attempt and record.is_open
    store.drain_events()
    store.begin_tick(10.5 + 4 * PLAN_NO_PROGRESS_SECONDS, frozenset({2000}), {})
    record = store.record("lane.start")
    assert record is not None and record.status == "aborted"
    # The fourth window ran out exactly at the plan deadline: either code is its end.
    assert record.reason_code in ("timeout", "recoveries_exhausted")
    assert (record.actor_tag, record.reserved_minerals, record.reserved_vespene) == (None, 0, 0)
    (event,) = [e for e in store.drain_events() if e.status == "failed"]
    assert event.facts["released"] == {"actor_tag": "2000", "minerals": 300, "vespene": 25}
    refused, reason = store.start("lane.start", "lane", "expand", 10.5 + 4 * 30 + 1)
    assert refused is None and "cools down" in reason
    later, _ = store.start("lane.start", "lane", "expand", 10.5 + 4 * 30 + PLAN_COOLDOWN_SECONDS)
    assert later is not None


def test_recoveries_exhaust_per_stage_before_the_deadline() -> None:
    store = _store_with_plan()
    for tag in (2000, 2001, 2002, 2003):
        store.claim_actor("lane.start", tag)
        store.begin_tick(11.0, frozenset(), {})  # the worker is gone
    record = store.record("lane.start")
    assert record is not None and record.status == "aborted"
    assert record.reason_code == "recoveries_exhausted"


def test_an_in_flight_build_defers_timeouts_and_an_acknowledged_loss_aborts() -> None:
    store = _store_with_plan()
    store.build_committed("lane.start", "lane.build", "t:1", 400, 0)
    store.begin_tick(10.0 + PLAN_TIMEOUT_SECONDS + 5.0, frozenset(), {})
    record = store.record("lane.start")
    assert record is not None and record.is_open  # SC2's answer decides, not the clock
    assert store.abort("lane.start", "timeout", "abandon", 140.0) is False
    store.build_acknowledged("lane.start", "lane.build", 140.0)
    record = store.record("lane.start")
    assert record is not None and record.second_base_defense
    assert record.deadline_game_seconds == 140.0 + PLAN_TIMEOUT_SECONDS
    store.build_failed("lane.start", "lost", True, "structure destroyed", 150.0)
    record = store.record("lane.start")
    assert record is not None and (record.status, record.reason_code) == ("aborted", "nexus_lost")
    assert not record.second_base_defense


def test_a_static_site_failure_disables_the_kind_and_a_site_failure_is_avoided() -> None:
    store = _store_with_plan()
    store.after_lane(
        "lane.start",
        stage="lane.site",
        rank=1,
        failed=True,
        waiting=False,
        code="site_unreachable",
        reason="no path",
        now=11.0,
    )
    refused, reason = store.start("lane.start", "lane", "expand", 100.0)
    assert refused is None and "disabled" in reason
    store = _store_with_plan()
    store.set_site("lane.start", NATURAL, 10.5, replaced=None)
    store.after_lane(
        "lane.start",
        stage="lane.wait",
        rank=3,
        failed=False,
        waiting=True,
        code="site_threatened",
        reason="threat",
        now=11.0,
    )
    assert store.abort("lane.start", "timeout", "threat persisted", 20.0)
    again, _ = store.start("lane.start", "lane", "expand", 40.0)
    assert again is not None and again.avoided_sites == (NATURAL,)
    store.complete("lane.start", 41.0)
    late, _ = store.start("lane.start", "lane", "expand", 20.0 + PLAN_SITE_AVOID_SECONDS)
    assert late is not None and late.avoided_sites == ()


def test_the_abort_limit_disables_a_kind_for_the_match() -> None:
    store = PlanStore(_RUN, kinds=("expand",))  # one kind: no other kind's turn to wait
    now = 10.0
    for attempt in range(1, MAX_PLAN_ABORTS + 1):
        record, reason = store.start("lane.start", "lane", "expand", now)
        assert record is not None, reason
        assert store.abort("lane.start", "timeout", "deadline", now + 1.0)
        now += 1.0 + PLAN_COOLDOWN_SECONDS
        (event,) = [e for e in store.drain_events() if e.status == "failed"]
        assert event.facts["kind_aborts"] == attempt
        assert event.facts["disabled"] is (attempt == MAX_PLAN_ABORTS)
    refused, reason = store.start("lane.start", "lane", "expand", now + 600.0)
    assert refused is None and "disabled for this match (abort_limit)" in reason
    tech, _ = store.start("tech.start", "tech", "tech", now)  # per kind: tech may still run
    assert tech is not None


def test_a_refused_site_is_given_up_and_avoided_by_the_next_plan() -> None:
    store = _store_with_plan()
    store.set_site("lane.start", NATURAL, 10.5, replaced=None, path_distance=30.0, path_rank=0)
    store.build_committed("lane.start", "lane.build", "t:1", 400, 0)
    store.build_failed("lane.start", "refused", False, "no legal placement", 12.0)
    record = store.record("lane.start")
    assert record is not None and record.is_open
    assert (record.site, record.site_path_rank, record.avoided_sites) == (None, None, (NATURAL,))
    assert record.recoveries == 1 and not record.build_in_flight
    assert store.abort("lane.start", "timeout", "deadline", 30.0)
    again, _ = store.start("lane.start", "lane", "expand", 30.0 + PLAN_COOLDOWN_SECONDS)
    assert again is not None and again.avoided_sites == (NATURAL,)


def test_a_site_change_recovers_on_the_old_site_before_committing_the_new_one() -> None:
    """Not exhausting: the old site is given up (a recovery) and the new one taken.
    Exhausting: the abort avoids the old site, and the new one is neither committed
    nor avoided."""
    store = _store_with_plan()
    store.set_site("lane.start", NATURAL, 10.5, replaced=None, path_distance=30.0, path_rank=0)
    store.set_site(
        "lane.start", THIRD, 11.0, replaced="site_occupied", path_distance=40.0, path_rank=1
    )
    record = store.record("lane.start")
    assert record is not None and record.is_open and record.recoveries == 1
    assert (record.site, record.site_path_rank) == (THIRD, 1)
    store = _store_with_plan()
    store.set_site("lane.start", NATURAL, 10.5, replaced=None, path_distance=30.0, path_rank=0)
    for _ in range(MAX_STAGE_RECOVERIES):
        store.move_failed("lane.start", "stalled", 11.0)
    store.drain_events()
    store.set_site(
        "lane.start", THIRD, 12.0, replaced="site_occupied", path_distance=40.0, path_rank=1
    )
    record = store.record("lane.start")
    assert record is not None and record.reason_code == "recoveries_exhausted"
    assert record.site == NATURAL  # the third was never committed
    (aborted,) = [e for e in store.drain_events() if e.status == "failed"]
    assert aborted.facts["avoided_sites"] == [[NATURAL[0], NATURAL[1]]]
    again, _ = store.start("lane.start", "lane", "expand", 12.0 + PLAN_COOLDOWN_SECONDS)
    assert again is not None and again.avoided_sites == (NATURAL,)


_MACRO_STARTS: dict[str, tuple[str, str]] = {
    "expand": ("exp.start", "exp"),
    "tech": ("tech.start", "tech"),
}


def _macro_store() -> PlanStore:
    return PlanStore(_RUN, kinds=("expand", "tech"))


def _start_kind(store: PlanStore, kind: PlanKind, now: float) -> tuple[PlanRecord | None, str]:
    node, root = _MACRO_STARTS[kind]
    return store.start(node, root, kind, now)


@pytest.mark.parametrize(("aborted", "other"), [("tech", "expand"), ("expand", "tech")])
def test_after_an_abort_the_macro_slot_yields_one_turn_to_the_other_kind(
    aborted: PlanKind, other: PlanKind
) -> None:
    store = _macro_store()
    record, _ = _start_kind(store, aborted, 0.0)
    assert record is not None
    assert store.abort(_MACRO_STARTS[aborted][0], "timeout", "deadline", 10.0)
    (event,) = [e for e in store.drain_events() if e.status == "failed"]
    until = 10.0 + PLAN_COOLDOWN_SECONDS + PLAN_YIELD_SECONDS
    assert (event.facts["yield_to"], event.facts["yield_until"]) == ([other], until)
    turn = event.facts["slot_turn"]
    assert isinstance(turn, dict) and (turn["yield_from"], turn["yield_to"]) == (aborted, [other])
    cooled = 10.0 + PLAN_COOLDOWN_SECONDS
    refused, reason = _start_kind(store, aborted, cooled)
    assert refused is None and f"yields to {other}" in reason
    slot = store.slot("macro")
    assert (slot.yield_from, slot.yield_to, slot.yield_until) == (aborted, (other,), until)
    taken, _ = _start_kind(store, other, cooled)
    assert taken is not None and store.slot("macro").yield_from is None  # its turn, taken
    assert store.slot("macro").open_kind == other


@pytest.mark.parametrize("order", [("tech", "expand"), ("expand", "tech")])
def test_repeated_aborts_alternate_the_macro_kinds_whatever_the_attempt_order(
    order: tuple[PlanKind, PlanKind],
) -> None:
    """Every turn both kinds try to start in a fixed order (as lanes do) and the one
    that starts aborts: the kinds alternate strictly until both are disabled."""
    store = _macro_store()
    now = 0.0
    started: list[str] = []
    for _ in range(2 * MAX_PLAN_ABORTS + 2):
        for kind in order:
            record, _ = _start_kind(store, kind, now)
            if record is not None:
                started.append(kind)
                assert store.abort(_MACRO_STARTS[kind][0], "timeout", "deadline", now + 1.0)
                now += 1.0
                break
        now += PLAN_COOLDOWN_SECONDS
    first, second = order
    assert started == [first, second] * MAX_PLAN_ABORTS  # then both are disabled


def test_the_yield_lapses_when_no_other_kind_takes_the_slot() -> None:
    store = _macro_store()
    _start_kind(store, "tech", 0.0)
    assert store.abort("tech.start", "timeout", "deadline", 10.0)
    cooled = 10.0 + PLAN_COOLDOWN_SECONDS
    early, _ = _start_kind(store, "tech", cooled + PLAN_YIELD_SECONDS - 0.25)
    assert early is None  # the expansion's turn is still open
    again, _ = _start_kind(store, "tech", cooled + PLAN_YIELD_SECONDS)
    assert again is not None and store.slot("macro").yield_from is None


def test_no_turn_is_owed_without_another_enabled_kind_after_a_completion_or_across_slots() -> None:
    store = _macro_store()
    _start_kind(store, "expand", 0.0)
    store.after_lane(
        "exp.start",
        stage="exp.site",
        rank=1,
        failed=True,
        waiting=False,
        code="no_site",
        reason="no site",
        now=1.0,
    )  # a static failure: expansion is disabled for the match
    tech, _ = _start_kind(store, "tech", 1.0 + PLAN_COOLDOWN_SECONDS)
    assert tech is not None
    assert store.abort("tech.start", "timeout", "deadline", 20.0)
    (event,) = [e for e in store.drain_events() if e.status == "failed" and "tech" in e.reason]
    assert event.facts["yield_to"] == [] and event.facts["yield_until"] is None
    again, _ = _start_kind(store, "tech", 20.0 + PLAN_COOLDOWN_SECONDS)
    assert again is not None  # nobody else could take the turn: no wait
    assert dict(store.slot("macro").disabled) == {"expand": "no_site"}
    # A completion owes no turn and sets no cooldown.
    store = _macro_store()
    _start_kind(store, "tech", 0.0)
    assert store.complete("tech.start", 5.0) is None
    slot = store.slot("macro")
    assert (slot.yield_from, slot.cooldown_until) == (None, None)
    # An army plan's abort owes the macro slot nothing (and the other way round).
    store = PlanStore(_RUN)
    army, _ = store.start("army.start", "army", "pressure", 0.0)
    assert army is not None and store.abort("army.start", "timeout", "deadline", 3.0)
    assert store.slot("army").yield_to == ("opening", "recover")
    assert store.slot("macro").yield_from is None
    assert _start_kind(store, "tech", 3.0)[0] is not None


def test_second_base_defense_is_read_from_the_live_state() -> None:
    store = _store_with_plan()
    store.set_site("lane.start", NATURAL, 10.5, replaced=None)
    store.build_committed("lane.start", "lane.build", "t:1", 400, 0)
    store.build_acknowledged("lane.start", "lane.build", 15.0)
    store.observe_sites(lambda site: site == NATURAL)
    record = store.record("lane.start")
    assert record is not None and record.second_base_defense
    assert store.complete("lane.start", 30.0) is None
    store.drain_events()
    store.observe_sites(lambda site: False)  # destroyed after the plan finished
    record = store.record("lane.start")
    assert record is not None and not record.second_base_defense
    (event,) = store.drain_events()
    assert (event.status, event.facts["transition"]) == ("warning", "nexus_lost")


def test_open_plans_are_summarized_once_per_game_second() -> None:
    store = _store_with_plan()
    store.drain_events()
    for now in (10.25, 10.5, 10.75, 11.0, 11.25):
        store.summarize(now)
    summaries = [e for e in store.drain_events() if e.facts["summary"]]
    assert len(summaries) == 2  # seconds 10 and 11


# ---------------------------------------------------------------------------
# Operations: bounded candidates, path answers, the base cap
# ---------------------------------------------------------------------------


def _obs(**fields: Any) -> Observation:
    values: dict[str, Any] = {
        "game_loop": 224,
        "game_seconds": 10.0,
        "minerals": 0,
        "supply_used": 12,
        "supply_cap": 15,
        "own_units": (),
        "own_structures": (_nexus(),),
        "visible_enemies": (),
        "remembered_enemy_structures": (),
        "start_location": START,
        "enemy_start_locations": (ENEMY_START,),
        "expansion_locations": (START, ENEMY_START, NATURAL, THIRD),
        "map_center": (75.5, 75.5),
    }
    values.update(fields)
    return Observation(**values)


@dataclass
class _View:
    """A :class:`~jev.operations.RuntimeView` for the plan operations: an observation,
    one plan record, bindings and in-flight builds; every other memory is empty."""

    observation: Observation
    record: PlanRecord | None = None
    bindings: dict[str, Binding] = field(default_factory=dict)
    builds: tuple[tuple[str, Point], ...] = ()
    trains: dict[str, int] = field(default_factory=dict)  # unconfirmed train tasks (Step 218)
    busy: frozenset[int] = frozenset()  # actors a task holds

    def binding(self, name: str) -> Binding | None:
        return self.bindings.get(name)

    def available_minerals(self) -> int:
        return self.observation.minerals

    def available_vespene(self) -> int:
        return self.observation.vespene

    def available_supply(self) -> int:
        return self.observation.supply_cap - self.observation.supply_used

    def latch_is_set(self, latch: str) -> bool:
        return False

    def army_mode(self) -> str | None:
        return None

    def count_tasks(self, node_id: str, statuses: frozenset[str]) -> int:
        return 0

    def gather_assignments(self) -> dict[int, int]:
        return {}

    def in_flight_trains(self) -> dict[str, int]:
        return dict(self.trains)

    def visits(self, node_id: str) -> dict[Point, Visit]:
        return {}

    def demoted(self, node_id: str) -> frozenset[int]:
        return frozenset()

    def recent_task_count(self, node_id: str, statuses: frozenset[str], within: float) -> int:
        return 0

    def assignment_age(self, node_id: str) -> float | None:
        return None

    def plan(self, node_id: str) -> PlanRecord | None:
        return self.record

    def rejected_sites(self, structure: str) -> frozenset[Point]:
        return frozenset()

    def in_flight_builds(self) -> tuple[tuple[str, Point], ...]:
        return self.builds

    def own_unit_tags(self) -> frozenset[int]:
        return frozenset(u.tag for u in self.observation.own_units)

    def own_structure_tags(self) -> frozenset[int]:
        return frozenset(s.tag for s in self.observation.own_structures)

    def actor_is_busy(self, tag: int) -> bool:
        return tag in self.busy

    def node_filter(self) -> CompiledFilter | None:
        return None

    def assigned(self, node_id: str) -> frozenset[int]:
        return frozenset()


def _open_record(**changes: Any) -> PlanRecord:
    store = _store_with_plan()
    record = store.record("lane.start")
    assert record is not None
    return dataclasses.replace(record, **changes)


def _select_expansion(view: RuntimeView, ratio: float = 1.5) -> Selection:
    spec = OPERATIONS["select_expansion"]
    assert isinstance(spec, SelectOp)
    args: dict[str, JsonValue] = {
        "plan": "lane.start",
        "candidates": 8,
        "clearance": 6.0,
        "max_path_ratio": ratio,
        "bind": "site",
    }
    return spec.evaluate(view, args)


def _plan_site_seen(view: RuntimeView) -> OpOutcome:
    spec = OPERATIONS["plan_site_seen"]
    assert isinstance(spec, PredicateOp)
    return spec.evaluate(view, {"plan": "lane.start", "max_age": 2.0})


def test_expansion_candidates_are_bounded_and_ordered_by_euclidean_distance() -> None:
    ring = tuple((30.5 + 20.0 * math.cos(i / 2), 30.5 + 20.0 * math.sin(i / 2)) for i in range(12))
    far = tuple((30.5 + 40.0 + i, 30.5) for i in range(3))
    observation = _obs(expansion_locations=(START, ENEMY_START, *far, *ring))
    candidates = expansion_candidates(observation, clearance=6.0, limit=MAX_EXPANSION_CANDIDATES)
    assert len(candidates) == MAX_EXPANSION_CANDIDATES
    assert START not in candidates and ENEMY_START not in candidates
    distances = [distance(p, START) for p in candidates]
    assert distances == sorted(distances) and set(candidates) <= set(ring)


def test_select_expansion_asks_for_every_unknown_path_and_never_assumes_reachability() -> None:
    record = _open_record()
    unknown = _select_expansion(_View(_obs(), record))
    assert unknown.binding is None and unknown.code == "path_pending"
    assert set(unknown.queries) == {NATURAL, THIRD}
    partial = _select_expansion(_View(_obs(expansion_paths=(PathFact(THIRD, 40.0),)), record))
    assert partial.binding is None and partial.queries == (NATURAL,)  # nothing chosen early
    longer = _select_expansion(
        _View(_obs(expansion_paths=(PathFact(NATURAL, 60.0), PathFact(THIRD, 40.0))), record)
    )
    assert longer.binding is not None and longer.binding.points == (THIRD,)  # path, not crow
    assert longer.effect is not None and longer.effect.point == THIRD
    none = _select_expansion(
        _View(_obs(expansion_paths=(PathFact(NATURAL, None), PathFact(THIRD, None))), record)
    )
    assert none.binding is None and none.code == "site_unreachable"


def test_select_expansion_keeps_the_committed_site_until_an_enemy_takes_it() -> None:
    record = _open_record(site=NATURAL)
    paths = (PathFact(NATURAL, 30.0), PathFact(THIRD, 40.0))
    kept = _select_expansion(_View(_obs(expansion_paths=paths), record))
    assert kept.binding is not None and kept.binding.points == (NATURAL,) and kept.effect is None
    hall = Entity(9001, "CommandCenter", NATURAL, 1500.0, is_structure=True)
    taken = _select_expansion(
        _View(_obs(expansion_paths=paths, remembered_enemy_structures=(hall,)), record)
    )
    assert taken.binding is not None and taken.binding.points == (THIRD,)
    assert taken.effect is not None and taken.effect.replaced == "site_occupied"
    assert (taken.effect.path, taken.effect.rank) == (40.0, 1)  # rank among all reachable


def test_select_expansion_never_takes_a_site_beyond_the_path_ratio() -> None:
    """The nearest reachable (30) is taken by an enemy; the alternative is 50 by path:
    within a 2x bound it is taken (rank 1), beyond a 1.5x bound the selection fails
    visibly and names why, instead of sending the plan to a far base."""
    hall = Entity(9001, "CommandCenter", NATURAL, 1500.0, is_structure=True)
    paths = (PathFact(NATURAL, 30.0), PathFact(THIRD, 50.0))
    observation = _obs(expansion_paths=paths, remembered_enemy_structures=(hall,))
    wide = _select_expansion(_View(observation, _open_record()), ratio=2.0)
    assert wide.binding is not None and wide.binding.points == (THIRD,)
    assert wide.facts["path_rank"] == 1 and wide.facts["path_bound"] == 60.0
    narrow = _select_expansion(_View(observation, _open_record()), ratio=1.5)
    assert narrow.binding is None and narrow.code == "site_occupied"
    assert narrow.facts["beyond_bound"] == 1 and "not taken" in narrow.reason


def test_plan_site_seen_wants_fresh_own_vision_of_the_site() -> None:
    record = _open_record(site=NATURAL)
    never = _plan_site_seen(_View(_obs(), record))
    assert not never.ok and never.code == "site_unscouted" and "never seen" in never.reason
    stale = _plan_site_seen(
        _View(_obs(location_vision=(LocationVision(NATURAL, False, 5.0),)), record)
    )
    assert not stale.ok and stale.code == "site_unscouted" and "stale" in stale.reason
    fresh = _plan_site_seen(
        _View(_obs(location_vision=(LocationVision(NATURAL, True, 10.0),)), record)
    )
    assert fresh.ok and fresh.facts["visible"] is True


def _build_expansion(view: RuntimeView) -> ActionPlan:
    spec = OPERATIONS["build_expansion"]
    assert isinstance(spec, ActionOp)
    return spec.plan(view, {"structure": "Nexus", "worker": "$w", "site": "$s"})


def test_build_expansion_never_orders_a_third_base() -> None:
    probe = Entity(2000, "Probe", NATURAL, 20.0)
    bindings = {
        "w": Binding("units", entities=(probe,)),
        "s": Binding("points", points=(THIRD,)),
    }
    second = Entity(1001, "Nexus", NATURAL, 1000.0, is_structure=True, build_progress=0.3)
    with_two = _View(_obs(own_units=(probe,), own_structures=(_nexus(), second)), None, bindings)
    plan = _build_expansion(with_two)
    assert plan.intents == () and plan.code == "base_cap"
    ordered = _View(_obs(own_units=(probe,)), None, bindings, builds=(("Nexus", NATURAL),))
    assert _build_expansion(ordered).code == "base_cap"  # a Nexus ordered elsewhere counts
    carrier = Entity(
        2001, "Probe", (40.0, 30.0), 20.0, orders=(Order("PROTOSSBUILD_NEXUS", NATURAL),)
    )
    carried = _View(_obs(own_units=(probe, carrier)), None, bindings)
    assert _build_expansion(carried).code == "base_cap"  # and so does a carried order
    alone = _build_expansion(_View(_obs(own_units=(probe,)), None, bindings))
    (intent,) = alone.intents
    assert (intent.ability, intent.target, intent.minerals) == ("PROTOSSBUILD_NEXUS", THIRD, 400)
    assert MAX_BASES == 2


# ---------------------------------------------------------------------------
# The runtime: duplicate plans, v1 isolation, path facts
# ---------------------------------------------------------------------------


def _two_lane_policy() -> Policy:
    nodes = [
        _node("lane_a", "sequence", ["lane_a.start"]),
        _start_node("lane_a.start", "expand"),
        _node("lane_b", "sequence", ["lane_b.start"]),
        _start_node("lane_b.start", "tech"),
    ]
    return parse_policy(
        {
            "schema_version": 1,
            "family": "jev",
            "version": 1,
            "roots": ["lane_a", "lane_b"],
            "parameters": {},
            "nodes": nodes,
        }
    )


def test_the_runtime_never_opens_a_second_plan_in_a_slot() -> None:
    runtime = JevRuntime(_two_lane_policy(), run_id=uuid.uuid4().hex)
    first = runtime.tick(_observation(1.0))
    events = {e.node_id: e for e in first.events if e.kind == "node"}
    assert events["lane_a.start"].status == "success"
    assert events["lane_b.start"].status == "failure"
    assert "never a duplicate plan" in events["lane_b.start"].reason
    second = runtime.tick(_observation(2.0))
    again = {e.node_id: e for e in second.events if e.kind == "node"}
    assert again["lane_a.start"].status == "failure"  # its own plan is still open
    assert [r.kind for r in runtime.plans()] == ["expand"]


def _lane_policy(nodes: list[dict[str, Any]], roots: list[str]) -> Policy:
    return parse_policy(
        {
            "schema_version": 1,
            "family": "jev",
            "version": 1,
            "roots": roots,
            "parameters": {},
            "nodes": nodes,
        }
    )


def _claim_and(*steps: str) -> list[dict[str, Any]]:
    """``plan``: the open plan (else start one), claim a probe for it, then ``steps``."""
    worker = _node(
        "plan.worker",
        "select",
        operation="select_plan_actor",
        plan="plan.start",
        filter={"types": ["Probe"]},
        bind="w",
    )
    far = _node(
        "plan.point",
        "select",
        operation="select_point",
        toward="map_center",
        distance=100,
        bind="p",
    )
    far["args"]["from"] = "start_location"
    by_id = {
        "plan.reserve": _node(
            "plan.reserve", "action", operation="plan_reserve", plan="plan.start", structure="Nexus"
        ),
        "plan.build": _node(
            "plan.build",
            "action",
            operation="build_expansion",
            structure="Nexus",
            worker="$w",
            site="$p",
        ),
        "plan.go": _node(
            "plan.go", "action", operation="move", units="$w", target="$p", arrive_within=1
        ),
    }
    return [
        _node("plan", "sequence", ["plan.ready", "plan.worker", "plan.point", *steps]),
        _node("plan.ready", "selector", ["plan.open", "plan.start"]),
        _node(
            "plan.open",
            "condition",
            operation="plan_status_is",
            plan="plan.start",
            statuses=["active", "waiting"],
        ),
        _start_node("plan.start", "expand"),
        worker,
        far,
        *(by_id[step] for step in steps),
    ]


def test_the_reservation_becomes_the_build_commitment_on_the_same_tick() -> None:
    """450 minerals: the plan holds 400 and its build takes the hold over on the tick
    it issues, so a later lane can spend the other 50 at once -- were the hold kept
    beside the build's commitment, 800 would be charged and nothing would be left."""
    spend = [
        _node("spend", "sequence", ["spend.nexus", "spend.train"]),
        _node(
            "spend.nexus",
            "select",
            operation="select_entities",
            collection="own_structures",
            filter={"types": ["Nexus"]},
            limit=1,
            bind="n",
        ),
        _node("spend.train", "action", operation="train", unit="Probe", producers="$n"),
    ]
    policy = _lane_policy([*_claim_and("plan.reserve", "plan.build"), *spend], ["plan", "spend"])
    runtime = JevRuntime(policy, run_id=uuid.uuid4().hex)
    result = runtime.tick(_observation(1.0, minerals=450, units=(_probe(2000, (31.0, 31.0)),)))
    by_node = {c.node_id: c for c in result.commands}
    assert by_node["plan.build"].ability == "PROTOSSBUILD_NEXUS"
    assert by_node["spend.train"].ability == "NEXUSTRAIN_PROBE"  # the remaining 50
    reserve = next(e for e in result.events if e.kind == "node" and e.node_id == "plan.reserve")
    assert reserve.facts["held_minerals"] == 400
    issued = next(e for e in result.events if e.facts.get("transition") == "issued")
    assert issued.facts["held_before"] == [400, 0]
    record = runtime.plan("plan.start")
    assert record is not None and record.build_in_flight and record.reserved_minerals == 0
    later = runtime.tick(_observation(1.5, minerals=900, units=(_probe(2000, (31.0, 31.0)),)))
    reserve = next(e for e in later.events if e.kind == "node" and e.node_id == "plan.reserve")
    assert reserve.facts["held_minerals"] == 0  # the in-flight build carries the cost


def test_an_abort_cancels_the_plan_lanes_running_tasks() -> None:
    """A plan whose worker keeps walking (progress, so no recovery) still times out;
    the abort cancels the walk, so nothing of the abandoned branch survives."""
    runtime = JevRuntime(_lane_policy(_claim_and("plan.go"), ["plan"]), run_id=uuid.uuid4().hex)
    target = CENTER  # select_point stops at its "toward" point
    position = (31.0, 31.0)
    runtime.tick(_observation(1.0, units=(_probe(2000, position),)))
    now = 1.5
    while now <= 1.0 + PLAN_TIMEOUT_SECONDS + 0.5:
        ratio = 0.4 * (now - 1.0) / distance(position, target)
        here = (
            position[0] + (target[0] - position[0]) * ratio,
            position[1] + (target[1] - position[1]) * ratio,
        )
        runtime.tick(_observation(now, units=(_probe(2000, here, (Order("MOVE", target),)),)))
        now += 5.0
    record = runtime.plan("plan.start")
    assert record is not None and (record.status, record.reason_code) == ("aborted", "timeout")
    assert record.recoveries == 0 and record.actor_tag is None
    walks = [t for t in runtime.task_history() if t.node_id == "plan.go"]
    assert [t.status for t in walks] == ["cancelled"]
    assert "aborted" in walks[0].reason
    assert not [t for t in runtime.active_tasks() if t.node_id == "plan.go"]


def test_v1_has_no_plan_store_and_never_asks_for_a_path() -> None:
    runtime = JevRuntime(load_v1_policy().policy, run_id=uuid.uuid4().hex)
    for second in (1.0, 2.0, 3.0):
        result = runtime.tick(_observation(second))
        assert not [e for e in result.events if "plan_id" in e.facts]
    assert runtime.plans() == () and runtime.path_requests() == ()
    world = _world()
    _play_v1(world, 30)
    assert world.path_queries == []  # the controller never asked SC2 for a path


def _play_v1(world: _ExpansionWorld, steps: int) -> None:
    launcher = _Launcher(world, steps)
    with tempfile.TemporaryDirectory() as root:
        run_match(MatchOptions(), load_v1_policy(), launcher=launcher, run_root=Path(root))


@pytest.mark.parametrize(
    ("paths", "message"),
    [
        ([PathFact((1.0, math.nan), 3.0)], "location"),
        ([PathFact(NATURAL, -1.0)], "distance"),
        ([PathFact(NATURAL, math.inf)], "distance"),
        ([PathFact(NATURAL, 3.0), PathFact(NATURAL, 4.0)], "repeats"),
        (["not a fact"], "PathFact"),
    ],
)
def test_the_runtime_rejects_malformed_path_facts(paths: list[Any], message: str) -> None:
    runtime = JevRuntime(_V2, run_id=uuid.uuid4().hex)
    observation = _obs(expansion_paths=tuple(paths))
    with pytest.raises(ValueError, match=message):
        runtime.tick(observation)


@pytest.mark.parametrize(
    ("vision", "message"),
    [
        ([LocationVision(NATURAL, True, None)], "never seen"),
        ([LocationVision(NATURAL, False, 11.0)], "game time"),
        ([LocationVision(NATURAL, False, math.nan)], "game time"),
        ([LocationVision((math.inf, 1.0), False, None)], "location"),
        ([LocationVision(NATURAL, False, 1.0), LocationVision(NATURAL, True, 9.0)], "repeats"),
        (["not vision"], "LocationVision"),
    ],
)
def test_the_runtime_rejects_malformed_location_vision(vision: list[Any], message: str) -> None:
    runtime = JevRuntime(_V2, run_id=uuid.uuid4().hex)
    with pytest.raises(ValueError, match=message):
        runtime.tick(_obs(location_vision=tuple(vision)))


# ---------------------------------------------------------------------------
# The adapter and the production port
# ---------------------------------------------------------------------------


class _PathPort(_Port):
    def __init__(self, replies: dict[Point, object]) -> None:
        super().__init__()
        self.replies = replies
        self.asked: list[tuple[Point, Point]] = []

    async def pathing_distance(self, start: Point, end: Point) -> object:
        self.asked.append((start, end))
        return self.replies.get(end)


def _expansion_game(locations: list[Point], **fields: Any) -> SimpleNamespace:
    return _game(expansion_locations_list=[START, ENEMY_START, *locations], **fields)


def test_the_adapter_asks_each_requested_location_once_and_caches_static_paths() -> None:
    adapter = Sc2Adapter()
    port = _PathPort({NATURAL: 30.0, THIRD: 0.0})
    game = _expansion_game([NATURAL, THIRD])
    assert asyncio.run(adapter.query_paths(port, [NATURAL])) == 0  # no map before observing
    adapter.observe(game, port)
    asked = asyncio.run(adapter.query_paths(port, [NATURAL, THIRD, (5.0, 5.0), NATURAL]))
    assert asked == 2 and port.asked == [(START, NATURAL), (START, THIRD)]  # map points only
    assert asyncio.run(adapter.query_paths(port, [NATURAL, THIRD])) == 0  # cached
    facts = adapter.observe(game, port).expansion_paths
    assert facts == (PathFact(THIRD, None), PathFact(NATURAL, 30.0))  # 0 is SC2's "no path"


def test_the_adapter_watches_only_queried_locations_with_honest_vision() -> None:
    """No path asked, no vision asked (v1 never asks either). Once a location's path
    is queried its own-sight record follows SC2's answer on every step -- between
    ticks too -- and keeps the last time it was in sight."""
    looked: list[Point] = []
    in_sight = {"natural": False}

    def visible(point: Point) -> bool:
        looked.append(point)
        return point == NATURAL and in_sight["natural"]

    port = _PathPort({NATURAL: 30.0})
    port.visible = visible
    adapter = Sc2Adapter()
    assert adapter.observe(_expansion_game([NATURAL]), port).location_vision == ()
    assert NATURAL not in looked
    asyncio.run(adapter.query_paths(port, [NATURAL]))
    unseen = adapter.observe(_expansion_game([NATURAL]), port).location_vision
    assert unseen == (LocationVision(NATURAL, False, None),)  # unknown is not safe
    in_sight["natural"] = True
    adapter.remember(_game(state=SimpleNamespace(game_loop=448)), port)  # a glimpse at 20 s
    in_sight["natural"] = False
    later = adapter.observe(
        _expansion_game([NATURAL], state=SimpleNamespace(game_loop=672)), port
    ).location_vision
    assert later == (LocationVision(NATURAL, False, 448 / GAME_LOOPS_PER_SECOND),)


def test_the_adapter_bounds_path_queries_per_match() -> None:
    ring = [(60.5 + i, 60.5 + i) for i in range(12)]
    adapter = Sc2Adapter()
    port = _PathPort({point: 10.0 for point in ring})
    adapter.observe(_expansion_game(ring), port)
    asked = asyncio.run(adapter.query_paths(port, ring))
    assert asked == MAX_EXPANSION_CANDIDATES and len(port.asked) == MAX_EXPANSION_CANDIDATES


@pytest.mark.parametrize("reply", [math.nan, -math.inf, "far", True])
def test_a_malformed_path_reply_fails_visibly(reply: object) -> None:
    adapter = Sc2Adapter()
    port = _PathPort({NATURAL: reply})
    adapter.observe(_expansion_game([NATURAL]), port)
    with pytest.raises(ValueError, match="pathing reply"):
        asyncio.run(adapter.query_paths(port, [NATURAL]))


def test_the_port_asks_burnysc2_query_pathing_with_points() -> None:
    calls: list[tuple[object, object]] = []

    async def query_pathing(start: object, end: object) -> float | None:
        calls.append((start, end))
        return None if end == Point2(THIRD) else 31.5

    bot = SimpleNamespace(client=SimpleNamespace(query_pathing=query_pathing))
    port = BotAIPort(cast(BotAI, bot))  # a stand-in with only the client the port uses
    assert asyncio.run(port.pathing_distance(START, NATURAL)) == 31.5
    assert asyncio.run(port.pathing_distance(START, THIRD)) is None
    assert calls[0] == (Point2(START), Point2(NATURAL)) and type(calls[0][0]) is Point2


# ---------------------------------------------------------------------------
# Validation and the packaged graph
# ---------------------------------------------------------------------------


def _start_node(node_id: str, kind: str) -> dict[str, Any]:
    """A start_plan node (its ``kind`` argument would clash with ``_node``'s)."""
    node = _node(node_id, "action", operation="start_plan")
    node["args"] = {"kind": kind}
    return node


def _plan_policy(extra: list[dict[str, Any]], children: list[str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "family": "jev",
        "version": 1,
        "roots": ["lane"],
        "parameters": {},
        "nodes": [
            _node("lane", "sequence", ["lane.start", *children]),
            _start_node("lane.start", "expand"),
            *extra,
        ],
    }


def test_plan_arguments_name_a_start_plan_node_and_a_lane_starts_one_plan() -> None:
    wrong = _plan_policy(
        [
            _node("lane.latch", "action", operation="set_latch", latch="attack_launched"),
            _node(
                "lane.open",
                "condition",
                operation="plan_status_is",
                plan="lane.latch",
                statuses=["active"],
            ),
        ],
        ["lane.latch", "lane.open"],
    )
    with pytest.raises(PolicyError, match="must reference a start_plan node"):
        parse_policy(wrong)
    twice = _plan_policy([_start_node("lane.again", "tech")], ["lane.again"])
    with pytest.raises(PolicyError) as excinfo:
        parse_policy(twice)
    assert "invalid_plan" in excinfo.value.codes()


def test_v2_runs_macro_plan_lanes_before_production_and_v1_none() -> None:
    plan_operations = {
        "start_plan",
        "plan_status_is",
        "select_expansion",
        "select_plan_actor",
        "plan_reserve",
        "plan_wait",
        "plan_complete",
        "build_expansion",
        "transfer_workers",
    }
    v1 = load_v1_policy().policy
    assert not [n.id for n in v1.nodes if n.operation in plan_operations]
    kinds = sorted(str(n.args["kind"]) for n in _V2.nodes if n.operation == "start_plan")
    assert kinds == ["expand", "tech"]  # Step 218 adds the Core prerequisite plan
    roots = _V2.roots
    assert max(roots.index("expansion"), roots.index("tech")) < roots.index("production")
    # Fairness between the macro kinds is the plan store's turn rule (Step 218): no node
    # reads another lane's plan, so no graph latch can encode (or break) whose turn it is.
    lane: dict[str, str] = {}
    for root in roots:
        stack = [root]
        while stack:
            node_id = stack.pop()
            lane[node_id] = root
            stack.extend(_V2.node(node_id).children)
    for node in _V2.nodes:
        if "plan" in node.args:
            assert lane[node.id] == lane[str(node.args["plan"])], node.id
    assert _V2.node(BUILD_NODE).args == {
        "structure": "Nexus",
        "worker": "$expander",
        "site": "$natural",
    }
