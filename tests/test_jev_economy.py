"""Jev's four-Gateway economy through the production entry point (Phase JV Step 202).

Every scenario runs the path a real match runs: :func:`jev.runner.run_match` (or
the CLI :func:`jev.runner.main`) -> :class:`jev.bot.JevController` ->
``JevBot.on_step`` -> :class:`jev.sc2_adapter.Sc2Adapter` ->
:class:`jev.runtime.JevRuntime` with the packaged v1 policy ->
:class:`jev.sc2_adapter.BotAIPort`. Only the SC2 process is replaced:
:class:`_World` is a ``BotAI`` stand-in whose units are real burnysc2 ``Unit``
objects built from protobuf with real ``GameData`` (no SC2 needed). Its client
records every ``UnitCommand`` the port sends and applies a small, explicit model
of SC2's response (orders, delayed placement, training, mineral and supply costs,
Pylon power), so acknowledgement comes from the next observation as in a match.
"""

from __future__ import annotations

import asyncio
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
from sc2.game_state import ActionError
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit import Unit
from sc2.unit_command import UnitCommand

from jev import runner
from jev.bot import JevBot, JevController
from jev.contracts import Event, Task
from jev.operations import (
    BUILD_ABILITY,
    MINERAL_COST,
    PYLON_POWER_RADIUS,
    SUPPLY_COST,
    TRAIN_ABILITY,
    distance,
)
from jev.policy import PolicyBundle
from jev.runner import MatchOptions, MatchOutcome, run_match
from jev.sc2_adapter import GAME_LOOPS_PER_SECOND, MAX_COMMAND_RECORDS, BotAIPort

START = (30.5, 30.5)
ENEMY_START = (120.5, 120.5)
CENTER = (75.5, 75.5)
NEXUS = 1000
PROBES = tuple(range(2000, 2012))
#: burnysc2 reads visibility from display_type alone at or above this base build.
BASE_BUILD = 90_000
#: Game loops per on_step. Eight loops (0.36 game seconds) exceed the runtime's 0.25 s
#: cadence, so every step is a policy tick (burnysc2's own default steps every four).
STEP_LOOPS = 8

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
}
#: Exact ability -> generic ability, as SC2's game data remaps them.
_REMAPS = {
    AbilityId.HARVEST_GATHER_PROBE: AbilityId.HARVEST_GATHER,
    AbilityId.HARVEST_RETURN_PROBE: AbilityId.HARVEST_RETURN,
    AbilityId.MOVE_MOVE: AbilityId.MOVE,
    AbilityId.ATTACK_ATTACK: AbilityId.ATTACK,
}
#: Command abilities sent under another AbilityId (burnysc2's Unit.move convention).
_ISSUED = {"MOVE": AbilityId.MOVE_MOVE}
#: Everything a one-base, no-gas Probe/Zealot player may ever send (D3), written out
#: independently of Jev's own tables.
_V1_ABILITIES = {
    AbilityId.HARVEST_GATHER,
    AbilityId.PROTOSSBUILD_PYLON,
    AbilityId.PROTOSSBUILD_GATEWAY,
    AbilityId.NEXUSTRAIN_PROBE,
    AbilityId.GATEWAYTRAIN_ZEALOT,
    AbilityId.MOVE_MOVE,
    AbilityId.ATTACK,
}
_BUILD_KIND = {AbilityId[ability]: kind for kind, ability in BUILD_ABILITY.items()}
_TRAIN_KIND = {AbilityId[ability]: kind for kind, ability in TRAIN_ABILITY.items()}
#: SC2 "faster" build and train times, in game seconds.
_BUILD_SECONDS = {"Pylon": 18.0, "Gateway": 46.0}
_TRAIN_SECONDS = {"Probe": 12.0, "Zealot": 27.0}


def _game_data() -> GameData:
    units = [
        data_pb2.UnitTypeData(
            unit_id=type_id.value,
            name=name,
            available=True,
            attributes=[Attribute.Structure.value] if structure else [],
        )
        for name, (type_id, structure) in _KINDS.items()
    ]
    plain = {*_BUILD_KIND, *_TRAIN_KIND, *_REMAPS.values()}
    abilities = [data_pb2.AbilityData(ability_id=a.value, available=True) for a in plain] + [
        data_pb2.AbilityData(ability_id=a.value, remaps_to_ability_id=g.value, available=True)
        for a, g in _REMAPS.items()
    ]
    return GameData(sc2api_pb2.ResponseData(units=units, abilities=abilities))


_GAME_DATA = _game_data()

type _OrderTarget = int | tuple[float, float] | None


@dataclass
class _Body:
    """One entity of the scripted world (re-sent as a fresh burnysc2 Unit every step)."""

    tag: int
    kind: str
    position: tuple[float, float]
    alliance: Alliance = Alliance.Self
    progress: float = 1.0
    orders: list[tuple[AbilityId, _OrderTarget, float]] = field(default_factory=list)


@dataclass
class _Placement:
    due_loop: int
    worker: int
    kind: str
    site: tuple[float, float]


class _Client:
    """burnysc2 ``Client`` stand-in: records commands, answers with ActionResults."""

    def __init__(self, world: _World) -> None:
        self._world = world

    async def actions(
        self, commands: list[UnitCommand], return_successes: bool = False
    ) -> list[ActionResult]:
        assert return_successes, "the port must ask for one verdict per command"
        return [self._world.receive(command) for command in commands]

    async def leave(self) -> None:
        self._world.left = True


class _World:
    """A ``BotAI`` stand-in with burnysc2's attribute names (see module docstring)."""

    def __init__(
        self,
        bodies: list[_Body],
        *,
        minerals: int,
        supply: tuple[int, int],
        place_after: float | None = 2.0,
    ) -> None:
        self.game_data = _GAME_DATA
        self.state = SimpleNamespace(game_loop=0, action_errors=[])
        self.bodies = {body.tag: body for body in bodies}
        self.minerals = minerals
        self.vespene = 0
        self.supply_used, self.supply_cap = supply
        self.start_location = Point2(START)
        self.enemy_start_locations = [Point2(ENEMY_START)]
        self.expansion_locations_list = [Point2(START), Point2(ENEMY_START)]
        self.game_info = SimpleNamespace(map_center=Point2(CENTER))
        self.client = _Client(self)
        #: Game seconds from an accepted build order to the structure appearing
        #: (the probe's walk); None means SC2 never starts it. ``place_delay``
        #: decides per site and defaults to ``place_after``.
        self.place_after = place_after
        self.place_delay: Callable[[tuple[float, float]], float | None] = lambda site: (
            self.place_after
        )
        self.illegal: Callable[[tuple[float, float]], bool] = lambda site: False
        #: Sites that pass the placement query but are blocked when the probe arrives.
        self.blocked_on_arrival: set[tuple[float, float]] = set()
        self.sent: list[tuple[int, UnitCommand]] = []
        self.queries: list[tuple[int, tuple[tuple[float, float], ...]]] = []
        self.overspent: list[str] = []
        self.foreign: list[int] = []
        self.left = False
        self._placements: list[_Placement] = []
        self._next_tag = 4_000_000_000_000  # SC2 tags are large 64-bit values
        self.refresh()

    @property
    def seconds(self) -> float:
        return self.state.game_loop / GAME_LOOPS_PER_SECOND

    def add(self, kind: str, position: tuple[float, float], progress: float = 1.0) -> int:
        self._next_tag += 1
        self.bodies[self._next_tag] = _Body(self._next_tag, kind, position, progress=progress)
        return self._next_tag

    def kill(self, tag: int) -> None:
        """Destroy an entity; the next observation no longer contains it."""
        body = self.bodies.pop(tag)
        if body.kind == "Pylon" and body.progress >= 1.0:
            self.supply_cap -= 8
        self.refresh()

    def own(self, kind: str) -> list[_Body]:
        return [b for b in self.bodies.values() if b.kind == kind and b.alliance == Alliance.Self]

    # -- what burnysc2 exposes -------------------------------------------------

    def refresh(self) -> None:
        """Re-send every body as a fresh burnysc2 Unit, as a new observation does."""
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
            powered = body.kind == "Gateway" and any(
                distance(body.position, p) <= PYLON_POWER_RADIUS for p in pylons
            )
            unit = Unit(self._proto(body, powered), self, base_build=BASE_BUILD)
            structure = _KINDS[body.kind][1]
            if body.kind == "MineralField":
                groups["mineral_field"].append(unit)
            elif body.alliance == Alliance.Enemy:
                groups["enemy_structures" if structure else "enemy_units"].append(unit)
            else:
                groups["structures" if structure else "units"].append(unit)
        for name, units in groups.items():
            setattr(self, name, units)

    @staticmethod
    def _proto(body: _Body, powered: bool) -> raw_pb2.Unit:
        orders = []
        for ability, target, progress in body.orders:
            order = raw_pb2.UnitOrder(ability_id=ability.value, progress=progress)
            if isinstance(target, tuple):
                order.target_world_space_pos.x, order.target_world_space_pos.y = target
            elif target is not None:
                order.target_unit_tag = target
            orders.append(order)
        return raw_pb2.Unit(
            display_type=DisplayType.Visible.value,
            alliance=body.alliance.value,
            tag=body.tag,
            unit_type=_KINDS[body.kind][0].value,
            pos=common_pb2.Point(x=body.position[0], y=body.position[1], z=10.0),
            health=100.0,
            build_progress=body.progress,
            is_powered=powered,
            orders=orders,
        )

    def is_visible(self, pos: Point2) -> bool:
        own = (b for b in self.bodies.values() if b.alliance == Alliance.Self)
        return any(distance((pos.x, pos.y), b.position) <= 12.0 for b in own)

    async def can_place(self, ability: AbilityId, positions: list[Point2]) -> list[bool]:
        sites = tuple((p.x, p.y) for p in positions)
        self.queries.append((self.state.game_loop, sites))
        return [not self.illegal(site) for site in sites]

    # -- SC2's response to a command -------------------------------------------

    def receive(self, command: UnitCommand) -> ActionResult:
        self.sent.append((self.state.game_loop, command))
        body = self.bodies.get(command.unit.tag)
        if body is None or body.alliance != Alliance.Self:
            self.foreign.append(command.unit.tag)
            return ActionResult.NotSupported
        ability, target = command.ability, command.target
        point = (target.x, target.y) if isinstance(target, Point2) else None
        if ability in _BUILD_KIND:
            assert point is not None
            body.orders = [(ability, point, 0.0)]
            delay = self.place_delay(point)
            if delay is not None:
                due = self.state.game_loop + round(delay * GAME_LOOPS_PER_SECOND)
                self._placements.append(_Placement(due, body.tag, _BUILD_KIND[ability], point))
        elif ability in _TRAIN_KIND:
            kind = _TRAIN_KIND[ability]
            if self.minerals < MINERAL_COST[kind]:
                self.overspent.append(f"{kind} at loop {self.state.game_loop}")
                return ActionResult.NotEnoughMinerals
            if self.supply_cap - self.supply_used < SUPPLY_COST[kind]:
                return ActionResult.NotEnoughFood
            self.minerals -= MINERAL_COST[kind]
            self.supply_used += SUPPLY_COST[kind]
            body.orders.append((ability, None, 0.0))
        elif ability == AbilityId.HARVEST_GATHER:
            assert isinstance(target, Unit)
            body.orders = [(AbilityId.HARVEST_GATHER_PROBE, target.tag, 0.0)]
        else:  # MOVE_MOVE or ATTACK, at a point or a unit
            shown = AbilityId.ATTACK_ATTACK if ability == AbilityId.ATTACK else ability
            body.orders = [(shown, point if point is not None else target.tag, 0.0)]
        return ActionResult.Success

    def advance(self, loops: int = STEP_LOOPS) -> None:
        """Let ``loops`` game loops pass, then observe again."""
        self.state.game_loop += loops
        self.state.action_errors = []
        dt = loops / GAME_LOOPS_PER_SECOND
        for body in list(self.bodies.values()):
            if body.alliance != Alliance.Self:
                continue
            if body.kind in _BUILD_SECONDS and body.progress < 1.0:
                body.progress = min(1.0, body.progress + dt / _BUILD_SECONDS[body.kind])
                if body.progress >= 1.0 and body.kind == "Pylon":
                    self.supply_cap += 8
            if body.orders and body.orders[0][0] in _TRAIN_KIND:
                ability, _, progress = body.orders[0]
                kind = _TRAIN_KIND[ability]
                progress += dt / _TRAIN_SECONDS[kind]
                if progress >= 1.0:
                    body.orders.pop(0)
                    self.add(kind, (body.position[0] + 2.0, body.position[1] - 2.0))
                else:
                    body.orders[0] = (ability, None, progress)
        for placement in [p for p in self._placements if p.due_loop <= self.state.game_loop]:
            self._placements.remove(placement)
            self._place(placement)
        self.refresh()

    def place_carried_order(self, site: tuple[float, float]) -> None:
        """SC2 finally starts a structure a probe has been carrying an order for."""
        for body in self.own("Probe"):
            if body.orders[:1] and body.orders[0][0] in _BUILD_KIND and body.orders[0][1] == site:
                kind = _BUILD_KIND[body.orders[0][0]]
                self._place(_Placement(self.state.game_loop, body.tag, kind, site))
        self.refresh()

    def _place(self, placement: _Placement) -> None:
        worker = self.bodies.get(placement.worker)
        ability = AbilityId[BUILD_ABILITY[placement.kind]]
        if worker is None or worker.orders[:1] != [(ability, placement.site, 0.0)]:
            return  # the worker died or was re-ordered on the way
        worker.orders = []
        cost = MINERAL_COST[placement.kind]
        refusal: ActionResult | None = None
        if placement.site in self.blocked_on_arrival:
            refusal = ActionResult.CantBuildLocationInvalid
        elif self.minerals < cost:
            self.overspent.append(f"{placement.kind} at loop {self.state.game_loop}")
            refusal = ActionResult.NotEnoughMinerals
        if refusal is not None:  # reported in the next observation's action errors
            self.state.action_errors = [ActionError(ability.value, worker.tag, refusal.value)]
            return
        self.minerals -= cost
        self.add(placement.kind, placement.site, progress=0.05)


def _world(
    *,
    minerals: int,
    supply: tuple[int, int] = (12, 15),
    mining: bool = True,
    stacked: bool = False,
    extra: tuple[_Body, ...] = (),
    place_after: float | None = 2.0,
    world_type: type[_World] | None = None,
) -> _World:
    """One Nexus, eight visible patches and twelve probes, plus ``extra`` bodies.

    ``world_type`` builds a :class:`_World` subclass instead (one that adds
    mechanics, e.g. income or movement, for a longer scenario)."""
    bodies = [_Body(NEXUS, "Nexus", START)]
    patches = [3000 + i for i in range(8)]
    bodies += [
        _Body(tag, "MineralField", (26.5 + i, 22.5), alliance=Alliance.Neutral)
        for i, tag in enumerate(patches)
    ]
    for i, tag in enumerate(PROBES):
        position = (28.0, 26.0) if stacked else (27.0 + i * 0.5, 26.0)
        orders: list[tuple[AbilityId, _OrderTarget, float]] = (
            [(AbilityId.HARVEST_GATHER_PROBE, patches[i % 8], 0.0)] if mining else []
        )
        bodies.append(_Body(tag, "Probe", position, orders=orders))
    make = _World if world_type is None else world_type
    return make([*bodies, *extra], minerals=minerals, supply=supply, place_after=place_after)


# ---------------------------------------------------------------------------
# The scripted launcher: production JevBot / controller path against _World
# ---------------------------------------------------------------------------

type _Hook = Callable[[_World, JevController, int], None]


class _Launcher:
    """A :class:`jev.runner.MatchLauncher` playing ``world`` through ``JevBot``."""

    def __init__(self, world: _World, steps: int, before_step: _Hook | None = None) -> None:
        self.world = world
        self.steps = steps
        self.before_step = before_step
        self.controller: JevController | None = None
        self.bot: JevBot | None = None

    def prepare(self, options: MatchOptions) -> object:
        return self.world

    def play(self, controller: JevController, setup: object, options: MatchOptions) -> Any:
        self.controller = controller
        self.bot = JevBot(controller)
        # What JevBot.on_start binds against a live game (minus the Ctrl+C handler).
        controller.attach(self.world, BotAIPort(self.world))  # type: ignore[arg-type]
        asyncio.run(self._run(self.bot, controller))
        return controller.result

    async def _run(self, bot: JevBot, controller: JevController) -> None:
        for index in range(self.steps):
            if self.before_step is not None:
                self.before_step(self.world, controller, index)
            await bot.on_step(index)
            if self.world.left:
                break
            self.world.advance()
        await bot.on_end(Result.Defeat if self.world.left else Result.Victory)


def _play(
    world: _World,
    steps: int,
    before_step: _Hook | None = None,
    *,
    bundle: PolicyBundle | None = None,
) -> tuple[MatchOutcome, JevController, _Launcher]:
    """Play ``world`` through the production runner (the packaged v1 policy unless
    ``bundle`` names another packaged version)."""
    launcher = _Launcher(world, steps, before_step)
    chosen = load_policy() if bundle is None else bundle
    with tempfile.TemporaryDirectory() as run_root:  # the evidence is not under test here
        outcome = run_match(MatchOptions(), chosen, launcher=launcher, run_root=Path(run_root))
    assert launcher.controller is not None
    return outcome, launcher.controller, launcher


class _Trace:
    """A hook that keeps every runtime event (the controller keeps only the latest 200)."""

    def __init__(self) -> None:
        self.events: dict[int, Event] = {}

    def __call__(self, world: _World, controller: JevController, index: int) -> None:
        self.events.update((event.sequence, event) for event in controller.recent_events)


def _sent(world: _World, ability: str) -> list[tuple[int, UnitCommand]]:
    wanted = AbilityId[ability]
    return [(loop, command) for loop, command in world.sent if command.ability == wanted]


def _tasks(controller: JevController, node_id: str) -> list[Task]:
    runtime = controller.runtime
    found = [*runtime.task_history(), *runtime.active_tasks()]
    return [task for task in found if task.node_id == node_id]


def _record_node(controller: JevController, loop: int, command: UnitCommand) -> str:
    """The policy node the adapter's record attributes a sent command to."""
    (record,) = [
        r
        for r in controller.adapter.records
        if r.game_loop == loop and r.actor_tags == (command.unit.tag,) and r.outcome == "accepted"
    ]
    return record.node_id


def _assert_graph_attributed(world: _World, controller: JevController, bot: JevBot) -> None:
    """Every command SC2 received is an accepted, node/task-attributed graph command."""
    runtime = controller.runtime
    records = controller.adapter.records
    assert len(records) < MAX_COMMAND_RECORDS  # the record log is complete for this run
    known_tasks = {t.id for t in (*runtime.task_history(), *runtime.active_tasks())}
    for loop, command in world.sent:
        matches = [
            r for r in records if r.game_loop == loop and r.actor_tags == (command.unit.tag,)
        ]
        assert len(matches) >= 1, (loop, command)
        record = matches[0]
        assert command.ability == _ISSUED.get(record.ability, AbilityId[record.ability])
        assert runtime.policy.node(record.node_id).kind == "action"
        assert record.task_id.startswith(f"{runtime.run_id}:")
        assert record.task_id in known_tasks
    assert world.foreign == []  # only entities Jev owns were commanded
    assert {command.ability for _, command in world.sent} <= _V1_ABILITIES  # no gas, no Nexus
    assert bot.actions == []  # nothing bypassed the port through burnysc2's do()
    for event in controller.recent_events:
        if event.kind == "command":
            assert event.task_id is not None and runtime.policy.has_node(event.node_id)


# ---------------------------------------------------------------------------
# Entry-point integration
# ---------------------------------------------------------------------------


def test_cli_match_issues_only_graph_attributed_commands(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    world = _world(minerals=50, mining=False)

    def income(world: _World, controller: JevController, index: int) -> None:
        world.minerals += 6  # ~17 minerals per game second

    launcher = _Launcher(world, steps=150, before_step=income)
    argv = ["--run-root", str(tmp_path)]
    code = runner.main(argv, load_policy=load_policy, prog="jev-test", launcher=launcher)
    assert code == runner.EXIT_OK
    out = capsys.readouterr().out
    assert "jev match finished: result=win" in out
    assert launcher.controller is not None and launcher.bot is not None
    _assert_graph_attributed(world, launcher.controller, launcher.bot)
    abilities = {command.ability for _, command in world.sent}
    assert {
        AbilityId.HARVEST_GATHER,
        AbilityId.PROTOSSBUILD_PYLON,
        AbilityId.NEXUSTRAIN_PROBE,
        AbilityId.PROTOSSBUILD_GATEWAY,
    } <= abilities
    assert world.overspent == []
    assert len(world.own("Gateway")) == 1  # the first Gateway, inside the new Pylon's power
    (gateway,) = world.own("Gateway")
    assert any(
        distance(gateway.position, p.position) <= PYLON_POWER_RADIUS for p in world.own("Pylon")
    )


# ---------------------------------------------------------------------------
# Scarce resources and actors
# ---------------------------------------------------------------------------


def test_scarce_minerals_stay_reserved_for_the_walking_pylon_probe() -> None:
    """100 minerals: the emergency Pylon's cost stays committed while its probe walks."""
    world = _world(minerals=100, place_after=3.0)
    trace = _Trace()
    _play(world, steps=20, before_step=trace)
    assert len(_sent(world, "PROTOSSBUILD_PYLON")) == 1
    assert _sent(world, "NEXUSTRAIN_PROBE") == []  # 100 observed, but all of it promised
    reasons = {
        e.reason
        for e in trace.events.values()
        if e.kind == "node" and e.node_id == "production.probes.train"
    }
    assert reasons == {"insufficient minerals"}
    assert len(world.own("Pylon")) == 1  # placed and paid for after the walk
    assert world.overspent == []


def test_minerals_for_both_buy_exactly_the_pylon_and_one_probe() -> None:
    world = _world(minerals=150, place_after=3.0)
    _play(world, steps=20)
    assert len(_sent(world, "PROTOSSBUILD_PYLON")) == 1
    assert len(_sent(world, "NEXUSTRAIN_PROBE")) == 1
    assert (world.minerals, len(world.own("Pylon"))) == (0, 1)
    assert world.overspent == []


def test_a_probe_is_never_commanded_by_two_roots_in_one_tick() -> None:
    """Economy and construction contend for the same stacked idle probes (D3 tie by tag)."""
    world = _world(minerals=100, mining=False, stacked=True)

    def no_shared_actor(world: _World, controller: JevController, index: int) -> None:
        holding = [
            t.actor_tag
            for t in controller.runtime.active_tasks()
            if t.status in ("pending", "issued")
        ]
        assert len(holding) == len(set(holding))

    _, controller, _ = _play(world, steps=12, before_step=no_shared_actor)
    by_loop: dict[int, list[int]] = {}
    for loop, command in world.sent:
        by_loop.setdefault(loop, []).append(command.unit.tag)
    assert all(len(tags) == len(set(tags)) for tags in by_loop.values())
    (first_loop, build), *_ = _sent(world, "PROTOSSBUILD_PYLON")
    gathered_then = {c.unit.tag for loop, c in _sent(world, "HARVEST_GATHER") if loop == first_loop}
    # Economy took the eight lowest tags this tick; the nearest free probe is next by tag.
    assert gathered_then == set(PROBES[:8])
    assert build.unit.tag == PROBES[8]


def test_no_supply_pylon_while_another_pylon_order_is_pending() -> None:
    """D3: the supply trigger needs four free supply or fewer *and* no pending Pylon."""
    world = _world(minerals=200, supply=(10, 15), place_after=5.0)
    trace = _Trace()

    def supply_runs_low(world: _World, controller: JevController, index: int) -> None:
        trace(world, controller, index)
        if index == 2:
            world.supply_used = 12  # three free: the trigger holds while the Pylon walks

    _, controller, _ = _play(world, steps=8, before_step=supply_runs_low)
    ((loop, pylon),) = _sent(world, "PROTOSSBUILD_PYLON")  # 200 minerals, yet only one
    assert _record_node(controller, loop, pylon) == "construction.power.build"
    guard = [
        e.status
        for e in trace.events.values()
        if e.kind == "node" and e.node_id == "construction.supply.no_power_order"
    ]
    assert len(guard) >= 1 and set(guard) == {"failure"}


# ---------------------------------------------------------------------------
# Bounded recovery
# ---------------------------------------------------------------------------


def test_rejected_placement_retries_boundedly_then_selects_new_sites() -> None:
    world = _world(minerals=100)
    rejected: set[tuple[float, float]] = set()
    world.illegal = lambda site: site in rejected
    answer = world.can_place

    async def reject_first_candidates(ability: AbilityId, positions: list[Point2]) -> list[bool]:
        if not rejected:
            rejected.update((p.x, p.y) for p in positions)
        return await answer(ability, positions)

    world.can_place = reject_first_candidates  # type: ignore[method-assign]
    trace = _Trace()
    _, controller, _ = _play(world, steps=24, before_step=trace)
    first = [(loop, sites) for loop, sites in world.queries if set(sites) == rejected]
    assert len(first) == 4  # D4: the first attempt plus three retries
    gaps = [b[0] - a[0] for a, b in zip(first, first[1:], strict=False)]
    assert all(gap >= GAME_LOOPS_PER_SECOND for gap in gaps)  # >= one game second apart
    later = [sites for loop, sites in world.queries if loop > first[-1][0]]
    assert len(later) >= 1 and rejected.isdisjoint(later[0])  # recovery chose new candidates
    ((_, build),) = _sent(world, "PROTOSSBUILD_PYLON")  # nothing illegal ever reached SC2
    assert (build.target.x, build.target.y) == later[0][0]
    failed = [t for t in _tasks(controller, "construction.supply.build") if t.status == "failed"]
    assert len(failed) == 1 and "attempts exhausted" in failed[0].reason
    assert failed[0].attempts == 4
    (cooldown,) = [
        e
        for e in trace.events.values()
        if e.kind == "diagnostic" and e.facts.get("failure_cause") == "rejected"
    ]
    excluded = cooldown.facts["excluded_sites"]
    assert isinstance(excluded, list)
    assert {(site[0], site[1]) for site in excluded} == rejected  # traced, then avoided


def test_placement_blocked_on_arrival_is_reported_retried_then_moved() -> None:
    """SC2 accepts the order, then reports an action error when the probe arrives."""
    world = _world(minerals=100)

    def block_first_site(world: _World, controller: JevController, index: int) -> None:
        builds = _sent(world, "PROTOSSBUILD_PYLON")
        if builds and not world.blocked_on_arrival:
            world.blocked_on_arrival.add((builds[0][1].target.x, builds[0][1].target.y))

    _, controller, _ = _play(world, steps=48, before_step=block_first_site)
    blocked = next(iter(world.blocked_on_arrival))
    builds = _sent(world, "PROTOSSBUILD_PYLON")
    at_blocked = [loop for loop, c in builds if (c.target.x, c.target.y) == blocked]
    assert len(at_blocked) == 4  # the first attempt plus three retries, then the intent fails
    (failed,) = [t for t in _tasks(controller, "construction.supply.build") if t.status == "failed"]
    assert "SC2 reported CantBuildLocationInvalid" in failed.reason
    (pylon,) = world.own("Pylon")
    assert pylon.position != blocked  # recovery placed it on a new site
    assert world.overspent == []


def test_lost_probe_mid_construction_is_replaced_at_once() -> None:
    world = _world(minerals=100, place_after=6.0)
    killed: list[int] = []

    def kill_builder(world: _World, controller: JevController, index: int) -> None:
        builds = _sent(world, "PROTOSSBUILD_PYLON")
        if builds and not killed:
            killed.append(builds[0][1].unit.tag)
            world.kill(killed[0])

    _, controller, _ = _play(world, steps=12, before_step=kill_builder)
    (first_loop, first), (second_loop, second) = _sent(world, "PROTOSSBUILD_PYLON")
    assert first.unit.tag == killed[0] and second.unit.tag != killed[0]
    assert second_loop - first_loop == STEP_LOOPS  # replanned on the very next tick
    assert (second.target.x, second.target.y) == (first.target.x, first.target.y)
    lost = [t for t in _tasks(controller, "construction.supply.build") if t.status == "failed"]
    assert [t.reason for t in lost] == ["actor lost"]


_FIRST_GATE, _SECOND_GATE = (26.5, 34.5), (40.5, 37.5)


def _two_powered_gateways(*, second_progress: float = 1.0) -> _World:
    """Pylon 5000 powers Gateway 5100 and Pylon 5001 powers Gateway 5101."""
    pylons = (_Body(5000, "Pylon", (24.0, 34.0)), _Body(5001, "Pylon", (40.0, 34.0)))
    busy = [(AbilityId.GATEWAYTRAIN_ZEALOT, None, 0.1)]  # both mid-Zealot: minerals stay
    gates = (
        _Body(5100, "Gateway", _FIRST_GATE, orders=list(busy)),
        _Body(
            5101,
            "Gateway",
            _SECOND_GATE,
            progress=second_progress,
            orders=list(busy) if second_progress >= 1.0 else [],
        ),
    )
    # Spare supply: losing Pylons must be a power problem here, not a supply one.
    return _world(minerals=150, supply=(16, 47), extra=(*pylons, *gates))


@pytest.mark.parametrize(
    ("destroyed", "repowered_gate"),
    [((5001,), _SECOND_GATE), ((5000, 5001), _FIRST_GATE)],
    ids=["one-of-two-pylons", "every-pylon"],
)
def test_lost_power_pylon_is_replaced_next_to_the_unpowered_gateway(
    destroyed: tuple[int, ...], repowered_gate: tuple[float, float]
) -> None:
    world = _two_powered_gateways()

    def destroy_pylons(world: _World, controller: JevController, index: int) -> None:
        if index == 3:
            for tag in destroyed:
                world.kill(tag)

    _, controller, _ = _play(world, steps=8, before_step=destroy_pylons)
    loss_loop = 3 * STEP_LOOPS
    ((loop, build),) = _sent(world, "PROTOSSBUILD_PYLON")
    assert loop == loss_loop  # nothing before the loss; repowered as soon as it was seen
    # With no Pylon left, re-powering a Gateway still comes before a Pylon near the Nexus.
    assert _record_node(controller, loop, build) == "construction.repower.build"
    assert distance((build.target.x, build.target.y), repowered_gate) <= PYLON_POWER_RADIUS


def test_a_warping_gateway_is_repowered_only_once_it_completes() -> None:
    world = _two_powered_gateways(second_progress=0.3)  # warping Gateways finish unpowered
    world.kill(5001)
    _play(world, steps=6)
    assert _sent(world, "PROTOSSBUILD_PYLON") == []


def test_late_structure_is_acknowledged_after_one_retry() -> None:
    """Ack timeout 5 s: one retry, then the structure appears and the task runs (D4)."""
    world = _world(minerals=100, place_after=7.0)
    _, controller, _ = _play(world, steps=40)
    ((first_loop, first), (second_loop, second)) = _sent(world, "PROTOSSBUILD_PYLON")
    assert (second.target.x, second.target.y) == (first.target.x, first.target.y)
    assert second_loop - first_loop >= 5.0 * GAME_LOOPS_PER_SECOND
    (task,) = _tasks(controller, "construction.supply.build")
    assert (task.status, task.attempts) == ("running", 2)
    assert len(world.own("Pylon")) == 1


def test_build_that_never_starts_fails_after_four_attempts_then_moves_site() -> None:
    world = _world(minerals=100, place_after=None)
    _, controller, _ = _play(world, steps=64)
    builds = _sent(world, "PROTOSSBUILD_PYLON")
    first_site = (builds[0][1].target.x, builds[0][1].target.y)
    same = [loop for loop, c in builds if (c.target.x, c.target.y) == first_site]
    assert len(same) == 4  # the first attempt plus three retries
    gaps = [b - a for a, b in zip(same, same[1:], strict=False)]
    assert all(gap >= 5.0 * GAME_LOOPS_PER_SECOND for gap in gaps)
    failed = [t for t in _tasks(controller, "construction.supply.build") if t.status == "failed"]
    assert [(t.attempts, t.reason) for t in failed] == [(4, "unacknowledged after 4 attempts")]
    retry_sites = {(c.target.x, c.target.y) for _, c in builds[4:]}
    assert len(retry_sites) >= 1 and first_site not in retry_sites  # the next intent moved on


# ---------------------------------------------------------------------------
# Policy-encoded targets: four Gateways, no gas, no expansion
# ---------------------------------------------------------------------------


def test_a_late_gateway_from_a_failed_order_never_exceeds_four() -> None:
    """The fourth Gateway's order is never confirmed in time (four attempts, then the
    task fails), but its probe keeps carrying it and SC2 starts it much later. No
    replacement is ordered meanwhile, so the late structure is the fourth, not a fifth."""
    pylons = (_Body(5000, "Pylon", (24.0, 34.0)), _Body(5001, "Pylon", (37.0, 34.0)))
    gates = tuple(_Body(5100 + i, "Gateway", (21.5 + 3.0 * i, 34.5)) for i in range(3))
    zealots = tuple(_Body(6000 + i, "Zealot", (36.0, 36.0)) for i in range(4))
    world = _world(minerals=3000, supply=(20, 39), extra=(*pylons, *gates, *zealots))
    held: list[tuple[float, float]] = []

    def hold_the_first_site(site: tuple[float, float]) -> float | None:
        if not held:
            held.append(site)
        return None if site == held[0] else 1.0  # any replacement would be quick

    world.place_delay = hold_the_first_site
    most: list[int] = []

    def watch_and_release(world: _World, controller: JevController, index: int) -> None:
        if index == 84:  # ~30 game seconds: well after the task failed (~20 s)
            world.place_carried_order(held[0])
        most.append(len(world.own("Gateway")))

    _, controller, _ = _play(world, steps=100, before_step=watch_and_release)
    failed = [t for t in _tasks(controller, "construction.gateways.build") if t.status == "failed"]
    assert [t.reason for t in failed] == ["unacknowledged after 4 attempts"]
    gateway_sites = {(c.target.x, c.target.y) for _, c in _sent(world, "PROTOSSBUILD_GATEWAY")}
    assert gateway_sites == {held[0]}  # no replacement while the order was carried
    assert max(most) == len(world.own("Gateway")) == 4


def test_four_gateway_cap_holds_with_ample_minerals_and_nothing_else_is_built() -> None:
    pylons = (_Body(5000, "Pylon", (24.0, 34.0)), _Body(5001, "Pylon", (37.0, 34.0)))
    gates = tuple(_Body(5100 + i, "Gateway", (21.5 + 3.0 * i, 34.5)) for i in range(3))
    zealots = tuple(_Body(6000 + i, "Zealot", (36.0, 36.0)) for i in range(4))
    world = _world(
        minerals=3000, supply=(20, 39), extra=(*pylons, *gates, *zealots), place_after=1.0
    )
    _, controller, launcher = _play(world, steps=40)
    assert launcher.bot is not None
    _assert_graph_attributed(world, controller, launcher.bot)
    assert len(_sent(world, "PROTOSSBUILD_GATEWAY")) == 1  # one at a time, up to four
    assert len(world.own("Gateway")) == 4
    below = [
        e for e in controller.recent_events if e.node_id == "construction.gateways.below_target"
    ]
    assert below[-1].status == "failure"
    trainers = {c.unit.tag for _, c in _sent(world, "GATEWAYTRAIN_ZEALOT")}
    assert {5100, 5101, 5102} <= trainers  # continuous Zealots from every idle Gateway


# ---------------------------------------------------------------------------
# Jev v2 (Phase J2 Step 214): planned supply, four Gateways, bounded failures
# ---------------------------------------------------------------------------

#: Every v2 node that builds a Pylon (opening, buffer and re-power).
V2_PYLON_NODES = ("supply.first.build", "supply.buffer.build", "construction.repower.build")


def _pylon_tasks_awaiting(controller: JevController) -> list[Task]:
    """Pylon build tasks whose cost is committed (command not yet confirmed)."""
    return [
        task
        for task in controller.runtime.active_tasks()
        if task.node_id in V2_PYLON_NODES and task.status in ("pending", "issued")
    ]


class V2Watch(_Trace):
    """Keeps every event and asserts, before every step, that no two Pylon orders are
    ever committed at once (plan D3: no duplicate Pylons from repeated ticks)."""

    def __init__(self) -> None:
        super().__init__()
        self.supply_used: dict[int, int] = {}

    def __call__(self, world: _World, controller: JevController, index: int) -> None:
        super().__call__(world, controller, index)
        self.supply_used[world.state.game_loop] = world.supply_used
        assert len(_pylon_tasks_awaiting(controller)) <= 1

    def node_events(self, node_id: str) -> list[Event]:
        found = [self.events[seq] for seq in sorted(self.events)]
        return [e for e in found if e.kind == "node" and e.node_id == node_id]


def test_v2_one_pylon_order_watch_covers_every_pylon_builder() -> None:
    """V2Watch's invariant is only as good as its node list: every v2 Pylon builder."""
    builders = {
        node.id
        for node in load_v2_policy().policy.nodes
        if node.operation == "build" and node.args["structure"] == "Pylon"
    }
    assert builders == set(V2_PYLON_NODES)


def test_v2_opening_pylon_waits_for_its_supply_then_comes_from_the_opening_node() -> None:
    """D3: the opening Pylon at the configured supply (14), then nothing more is needed."""
    world = _world(minerals=400)
    watch = V2Watch()
    _, controller, _ = _play(world, steps=80, before_step=watch, bundle=load_v2_policy())
    ((loop, pylon),) = _sent(world, "PROTOSSBUILD_PYLON")  # one Pylon in ~28 game seconds
    assert watch.supply_used[loop] >= 14
    assert max(s for at, s in watch.supply_used.items() if at < loop) < 14  # not earlier
    assert _record_node(controller, loop, pylon) == "supply.first.build"
    buffer = watch.node_events("supply.buffer.low")
    assert buffer and {e.status for e in buffer} == {"failure"}  # the plan covers the rest
    assert world.overspent == []


def test_v2_a_walking_or_warping_pylon_is_never_duplicated_by_repeated_ticks() -> None:
    """The buffer Pylon is ordered once; while its probe walks and while it warps in, the
    supply plan counts its 8 supply, so later ticks never order a second one."""
    pylon = _Body(5000, "Pylon", (24.0, 34.0))
    world = _world(minerals=500, supply=(20, 23), extra=(pylon,), place_after=5.0)
    watch = V2Watch()
    _, controller, _ = _play(world, steps=56, before_step=watch, bundle=load_v2_policy())
    ((loop, build),) = _sent(world, "PROTOSSBUILD_PYLON")  # ~20 s: walked, placed, warping
    assert _record_node(controller, loop, build) == "supply.buffer.build"
    warping = [b for b in world.own("Pylon") if b.progress < 1.0]
    assert len(warping) == 1  # placed, still warping in when the run ends
    later = [e for e in watch.node_events("supply.buffer.low") if e.game_loop > loop]
    assert later and {e.status for e in later} == {"failure"}
    assert {e.facts["pending"] for e in later} == {8}  # ordered, then warping: counted once
    assert world.overspent == []


_POWERED_GATES = ((21.5, 37.5), (26.5, 37.5), (34.5, 37.5), (39.5, 37.5))


def test_v2_a_large_supply_deficit_plans_successive_pylons_and_no_more() -> None:
    """Four ready Gateways and a Nexus each need their next unit's supply (9 in all):
    with one free supply the plan orders a Pylon, then -- once that one is placed and
    the plan is still short -- exactly one more, never a third or two at once."""
    pylons = (_Body(5000, "Pylon", (24.0, 34.0)), _Body(5001, "Pylon", (37.0, 34.0)))
    gates = tuple(
        _Body(5100 + i, "Gateway", site, orders=[(AbilityId.GATEWAYTRAIN_ZEALOT, None, 0.1)])
        for i, site in enumerate(_POWERED_GATES)
    )
    world = _world(minerals=1000, supply=(30, 31), extra=(*pylons, *gates), place_after=1.0)
    world.bodies[NEXUS].orders = [(AbilityId.NEXUSTRAIN_PROBE, None, 0.1)]
    world.refresh()
    watch = V2Watch()
    _, controller, _ = _play(world, steps=42, before_step=watch, bundle=load_v2_policy())
    (first_loop, first), (second_loop, second) = _sent(world, "PROTOSSBUILD_PYLON")
    assert _record_node(controller, first_loop, first) == "supply.buffer.build"
    assert _record_node(controller, second_loop, second) == "supply.buffer.build"
    assert (first.target.x, first.target.y) != (second.target.x, second.target.y)
    assert len([b for b in world.own("Pylon") if b.tag not in (5000, 5001)]) == 2
    low = watch.node_events("supply.buffer.low")
    (at_second,) = [e for e in low if e.game_loop == second_loop]
    assert (at_second.facts["pending"], at_second.facts["demand"]) == (8, 9)  # first counted
    after = [e for e in low if e.game_loop > second_loop]
    assert after and {e.status for e in after} == {"failure"}  # covered: no third


def test_v2_a_late_pylon_order_still_carried_counts_as_planned_supply() -> None:
    """The buffer Pylon's order is never confirmed (four attempts, then the task fails),
    but its probe keeps carrying it. The plan counts that carried order, so no
    replacement is ordered; SC2 finally places it."""
    pylon = _Body(5000, "Pylon", (24.0, 34.0))
    world = _world(minerals=1000, supply=(20, 23), extra=(pylon,))
    held: list[tuple[float, float]] = []

    def hold_the_first_pylon_site(site: tuple[float, float]) -> float | None:
        pylons = _sent(world, "PROTOSSBUILD_PYLON")  # the command being received is last
        if not held and pylons and (pylons[0][1].target.x, pylons[0][1].target.y) == site:
            held.append(site)
        return None if held and site == held[0] else 1.0  # any other site is quick

    world.place_delay = hold_the_first_pylon_site
    watch = V2Watch()

    def release_late(world: _World, controller: JevController, index: int) -> None:
        watch(world, controller, index)
        if index == 84:  # ~30 game seconds: well after the task failed (~20 s)
            world.place_carried_order(held[0])

    _, controller, _ = _play(world, steps=96, before_step=release_late, bundle=load_v2_policy())
    failed = [t for t in _tasks(controller, "supply.buffer.build") if t.status == "failed"]
    assert [t.reason for t in failed] == ["unacknowledged after 4 attempts"]
    pylon_sites = {(c.target.x, c.target.y) for _, c in _sent(world, "PROTOSSBUILD_PYLON")}
    assert pylon_sites == {held[0]}  # no replacement while the order was carried
    failed_at = max(
        e.game_loop
        for e in watch.events.values()
        if e.kind == "task" and e.status == "failed" and e.node_id == "supply.buffer.build"
    )
    carried = [
        e
        for e in watch.node_events("supply.buffer.low")
        if failed_at < e.game_loop < 84 * STEP_LOOPS
    ]
    assert carried and {(e.status, e.facts["pending"]) for e in carried} == {("failure", 8)}
    assert len(world.own("Pylon")) == 2


def test_v2_four_gateways_go_up_without_waiting_for_the_first_wave() -> None:
    """V2 spends on all four Gateways as soon as power and minerals allow, one
    placement at a time (v1 waits for four Zealots before Gateways two to four)."""
    pylons = (_Body(5000, "Pylon", (24.0, 34.0)), _Body(5001, "Pylon", (37.0, 34.0)))
    world = _world(minerals=3000, supply=(14, 31), extra=pylons, place_after=1.0)
    _, controller, launcher = _play(world, steps=30, before_step=V2Watch(), bundle=load_v2_policy())
    assert launcher.bot is not None
    _assert_graph_attributed(world, controller, launcher.bot)
    builds = _sent(world, "PROTOSSBUILD_GATEWAY")
    assert len(builds) == 4 and len(world.own("Gateway")) == 4
    loops = [loop for loop, _ in builds]
    assert loops == sorted(set(loops))  # one placement at a time
    assert world.own("Zealot") == []  # no Zealot was needed first
    for loop, build in builds:
        assert _record_node(controller, loop, build) == "construction.gateways.build"
    assert world.overspent == []


@pytest.mark.parametrize(
    ("destroyed", "repowered_gate"),
    [((5001,), _SECOND_GATE), ((5000, 5001), _FIRST_GATE)],
    ids=["one-of-two-pylons", "every-pylon"],
)
def test_v2_lost_power_is_replaced_next_to_the_unpowered_gateway(
    destroyed: tuple[int, ...], repowered_gate: tuple[float, float]
) -> None:
    """V2 keeps v1's power recovery: with Gateways standing, a lost Pylon is replaced
    next to the unpowered Gateway (the opening-Pylon branch stays out of it)."""
    world = _two_powered_gateways()
    world.minerals = 1000  # v2 also starts Gateway three: power must not wait on it

    def destroy_pylons(world: _World, controller: JevController, index: int) -> None:
        if index == 3:
            for tag in destroyed:
                world.kill(tag)

    watch = V2Watch()

    def both(world: _World, controller: JevController, index: int) -> None:
        destroy_pylons(world, controller, index)
        watch(world, controller, index)

    _, controller, _ = _play(world, steps=8, before_step=both, bundle=load_v2_policy())
    ((loop, build),) = _sent(world, "PROTOSSBUILD_PYLON")
    assert loop == 3 * STEP_LOOPS  # repowered as soon as the loss was seen
    assert _record_node(controller, loop, build) == "construction.repower.build"
    assert distance((build.target.x, build.target.y), repowered_gate) <= PYLON_POWER_RADIUS


@pytest.mark.parametrize(
    ("gateway_progress", "beside_gateway"),
    [(1.0, True), (0.5, False)],
    ids=["completed-gateways", "warping-gateways"],
)
def test_v2_supply_recovers_once_affordable_after_every_pylon_is_lost(
    gateway_progress: float, beside_gateway: bool
) -> None:
    """After the opening, losing every Pylon (Gateways standing) leaves the supply plan
    short. The buffer lane holds its reservation until minerals allow, then plans a
    Pylon at once -- beside the first unpowered completed Gateway, so one Pylon
    restores power and supply, else near the main -- one order, never a duplicate."""
    pylons = (_Body(5000, "Pylon", (24.0, 34.0)), _Body(5001, "Pylon", (37.0, 34.0)))
    gates = tuple(
        _Body(5100 + i, "Gateway", site, progress=gateway_progress)
        for i, site in enumerate(_POWERED_GATES)
    )
    world = _world(minerals=0, supply=(14, 31), extra=(*pylons, *gates), place_after=1.0)
    loss_step, affordable_step = 3, 8
    watch = V2Watch()

    def lose_every_pylon_then_income(world: _World, controller: JevController, index: int) -> None:
        if index == loss_step:
            world.kill(5000)
            world.kill(5001)  # the cap falls from 31 to 15
        if index == affordable_step:
            world.minerals = 1000
        watch(world, controller, index)

    _, controller, _ = _play(
        world, steps=40, before_step=lose_every_pylon_then_income, bundle=load_v2_policy()
    )
    ((loop, build),) = _sent(world, "PROTOSSBUILD_PYLON")  # one order restores the plan
    assert loop == affordable_step * STEP_LOOPS  # at once, the first tick it is affordable
    assert _record_node(controller, loop, build) == "supply.buffer.build"
    held = [
        e
        for e in watch.node_events("supply.buffer.build")
        if loss_step * STEP_LOOPS <= e.game_loop < loop
    ]
    assert held and {e.reason for e in held} == {"waiting for minerals; holding reservation"}
    site = (build.target.x, build.target.y)
    if beside_gateway:
        assert distance(site, _POWERED_GATES[0]) <= PYLON_POWER_RADIUS
    else:
        assert 6.0 <= distance(site, START) <= 12.0  # no completed Gateway: near the main
    later = [e for e in watch.node_events("supply.buffer.low") if e.game_loop > loop]
    assert later and {e.status for e in later} == {"failure"}  # covered: nothing more
    assert {e.facts["pending"] for e in later} == {8}
    assert world.overspent == []


def test_v2_repeated_build_failures_back_off_and_release_the_reservation() -> None:
    """Every Gateway placement is illegal. Each intent is bounded (four attempts, D4);
    after three failed Gateway tasks within the failure window the lane backs off
    without holding minerals, so production spends them; it resumes afterwards."""
    pylon = _Body(5000, "Pylon", (24.0, 34.0))
    world = _world(minerals=150, supply=(14, 23), mining=False, extra=(pylon,))
    answer = world.can_place

    async def no_gateway_site(ability: AbilityId, positions: list[Point2]) -> list[bool]:
        verdicts = await answer(ability, positions)
        if ability == AbilityId.PROTOSSBUILD_GATEWAY:
            return [False for _ in verdicts]
        return verdicts

    world.can_place = no_gateway_site  # type: ignore[method-assign]
    watch = V2Watch()
    _, controller, _ = _play(world, steps=150, before_step=watch, bundle=load_v2_policy())
    failed = [t for t in _tasks(controller, "construction.gateways.build") if t.status == "failed"]
    assert len(failed) == 3  # then the back-off, then a held reservation: no fourth
    assert all(t.attempts == 4 and "attempts exhausted" in t.reason for t in failed)
    failures = sorted(
        e.game_loop
        for e in watch.events.values()
        if e.kind == "task" and e.status == "failed" and e.node_id == "construction.gateways.build"
    )
    backoff = watch.node_events("construction.gateways.backoff")
    backed_off = [e for e in backoff if e.status == "failure"]
    assert backed_off and {e.facts["count"] for e in backed_off} == {3}
    assert min(e.game_loop for e in backed_off) > failures[-1]
    window_end = failures[0] + 30 * GAME_LOOPS_PER_SECOND
    assert max(e.game_loop for e in backed_off) < window_end + STEP_LOOPS  # ends with the window
    probes = [loop for loop, _ in _sent(world, "NEXUSTRAIN_PROBE")]
    assert probes and failures[-1] < probes[0] < window_end  # the released minerals were spent
    resumed = [
        e
        for e in watch.node_events("construction.gateways.build")
        if e.game_loop > window_end and e.status == "running"
    ]
    assert resumed and "holding reservation" in resumed[-1].reason  # no new failure
    assert world.overspent == []
