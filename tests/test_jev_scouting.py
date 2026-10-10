"""Jev v2 scouting with bounded, honest enemy memory (Phase J2 Step 215, plan D3).

Two layers, both through production code:

* **Enemy memory.** :class:`jev.sc2_adapter.Sc2Adapter` keeps the bot's own
  timestamped sightings (:class:`jev.contracts.EnemySighting`) and every
  Observation carries them. The scenarios run the real path -- :func:`jev.runner.run_match`
  -> :class:`jev.bot.JevController` -> ``JevBot.on_step`` -> the adapter and
  :class:`jev.sc2_adapter.BotAIPort` -> :class:`jev.runtime.JevRuntime` with the
  packaged v2 policy -- against the army suite's burnysc2 world (real ``Unit``
  objects from protobuf, a vision radius around own entities). :class:`_ScoutWorld`
  adds walking probes and, by default, reports every enemy outside vision as a
  fogged snapshot carrying its *true current* state: the worst case of hidden
  information, which must never enter or refresh the memory.
* **The scout.** One Probe is reserved once the first Gateway is started; it tours
  the enemy start and the expansions, least recently visited first; the scouting
  lane owns it (economy and builders exclude it even between its tasks); a threat
  sends it home, where it mines again; replacements are spaced; and the lane can
  never take another lane's evaluation budget.

Adapter-only memory rules (duck-typed game state) live in ``test_jev_sc2.py``.
"""

from __future__ import annotations

import dataclasses
import json
import math
import uuid
from typing import Any

import pytest
from bots.jev.v1 import load_policy as load_v1_policy
from bots.jev.v2 import load_policy as load_v2_policy
from sc2.data import Alliance, DisplayType
from sc2.ids.ability_id import AbilityId
from sc2.unit import Unit

from jev.bot import JevController
from jev.contracts import (
    MAX_ENEMY_SIGHTINGS,
    SIGHTING_UNIT_TTL_SECONDS,
    EnemySighting,
    Entity,
    Event,
    Observation,
    Order,
    Policy,
)
from jev.operations import PYLON_POWER_RADIUS, distance, validate_operation_args
from jev.policy import parse_policy
from jev.runtime import NODE_BUDGET_REASON, JevRuntime, TickConfig, TickResult
from jev.sc2_adapter import GAME_LOOPS_PER_SECOND
from test_jev_army import (
    _KINDS,
    BASE_BUILD,
    CENTER,
    ENEMY_NATURAL,
    ENEMY_START,
    EXPANSIONS,
    GATEWAY_SITES,
    HIDDEN_SITE,
    NEXUS,
    OWN_NATURAL,
    PROBES,
    PYLON,
    START,
    STEP_LOOPS,
    STEP_SECONDS,
    THIRD,
    _Body,
    _enemy,
    _gather,
    _Hook,
    _play,
    _Run,
    _World,
)

#: The packaged v2 scouting lane's action nodes (the one owner is the policy file).
SCOUT_MOVE = "scouting.move"
RETREAT = "scouting.retreat.go"
#: Every v2 node that selects a Probe for another job: each must exclude the scout.
PROBE_SELECTIONS = (
    "economy.idle_probes",
    "supply.first.worker",
    "supply.buffer.worker",
    "construction.repower.worker",
    "construction.gateways.worker",
)
_EPSILON = 1e-6
#: 8.6 east of the enemy natural, 16.5 from the enemy start and 12.4 from the
#: START -> ENEMY_START diagonal: unseen until the scout heads for the natural.
GUARD = (104.0, 121.5)


def _seconds(loop: int) -> float:
    return loop / GAME_LOOPS_PER_SECOND


def _parameter(name: str) -> float:
    value = load_v2_policy().policy.parameters[name]
    assert isinstance(value, int | float) and not isinstance(value, bool)
    return float(value)


#: The packaged policy's spacing between scout reservations (game seconds).
REPLACE_INTERVAL = _parameter("scout_replace_interval")


# ---------------------------------------------------------------------------
# The scripted world: walking probes and worst-case hidden information
# ---------------------------------------------------------------------------


class _ScoutWorld(_World):
    """The army suite's world plus walking probes (MOVE orders, like its Zealots).

    With ``leak_hidden`` (the default) every enemy outside vision is reported
    anyway, as a fogged snapshot carrying its *true current* position and health
    (SC2 omits fogged units and reports only stale snapshots of structures seen
    before): the adapter must take neither into its memory. With ``idle_arrival``
    ``(within, stop)``, a probe whose step would bring it within ``within`` of its
    MOVE point stops ``stop`` short of it with no order: it is idle on the very
    step it arrives (by default probes walk all the way, idle one step later).
    """

    def __init__(
        self,
        bodies: list[_Body],
        *,
        leak_hidden: bool = True,
        idle_arrival: tuple[float, float] | None = None,
    ) -> None:
        self.leak_hidden = leak_hidden
        self.idle_arrival = idle_arrival
        super().__init__(bodies)

    def advance(self, loops: int = STEP_LOOPS) -> None:
        dt = loops / GAME_LOOPS_PER_SECOND
        for body in self.own("Probe"):
            if not body.orders or body.tag in self.stuck:
                continue
            ability, target, _ = body.orders[0]
            if ability != AbilityId.MOVE_MOVE or not isinstance(target, tuple):
                continue
            gap = distance(body.position, target)
            if self.idle_arrival is not None and gap - self.speed * dt <= self.idle_arrival[0]:
                stop = min(gap, self.idle_arrival[1])
                ratio = 0.0 if gap == 0.0 else stop / gap
                body.position = (
                    target[0] + (body.position[0] - target[0]) * ratio,
                    target[1] + (body.position[1] - target[1]) * ratio,
                )
                body.orders = []
            elif self._walk(body, target, dt, stop=0.0):
                body.orders = []
        super().advance(loops)

    def refresh(self) -> None:
        if not self.leak_hidden:
            super().refresh()
            return
        groups: dict[str, list[Unit]] = {
            "units": [],
            "structures": [],
            "enemy_units": [],
            "enemy_structures": [],
            "mineral_field": [],
        }
        pylons = [b.position for b in self.own("Pylon") if b.progress >= 1.0]
        for body in sorted(self.bodies.values(), key=lambda b: b.tag):
            structure = _KINDS[body.kind][1]
            display = DisplayType.Visible
            if body.alliance == Alliance.Enemy:
                if self.sees(body.position):
                    self.ever_seen.add(body.tag)
                else:
                    display = DisplayType.Snapshot  # the true state, but not in sight
            powered = body.kind == "Gateway" and any(
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


def _home(*, gateways: int = 4, extra: tuple[_Body, ...] = ()) -> list[_Body]:
    """One Nexus, a Pylon, ``gateways`` finished Gateways and twelve mining probes.

    The world starts with no minerals, so the only commands are the scout's (and
    the economy's, for an idle probe) unless a scenario changes that.
    """
    bodies = [_Body(NEXUS, "Nexus", START), _Body(PYLON, "Pylon", (24.0, 34.0))]
    bodies += [_Body(1200 + i, "Gateway", GATEWAY_SITES[i]) for i in range(gateways)]
    patches = [3000 + i for i in range(8)]
    bodies += [
        _Body(tag, "MineralField", (26.5 + i, 22.5), alliance=Alliance.Neutral)
        for i, tag in enumerate(patches)
    ]
    bodies += [
        _Body(tag, "Probe", (27.0 + i * 0.5, 26.0), orders=[_gather(patches[i % 8])])
        for i, tag in enumerate(PROBES)
    ]
    return [*bodies, *extra]


class _Watch:
    """A hook that records what the runtime was given and the adapter remembered.

    On the first step it wraps the controller's ``runtime.tick`` (an instance
    attribute shadows the method), so every Observation the production adapter
    hands the runtime is kept, keyed by game loop.
    """

    def __init__(self) -> None:
        self.observations: dict[int, Observation] = {}

    def __call__(self, world: _World, controller: JevController, index: int) -> None:
        if index:
            return
        tick = controller.runtime.tick
        observations = self.observations

        def recording(observation: Observation) -> TickResult:
            observations[observation.game_loop] = observation
            return tick(observation)

        controller.runtime.tick = recording  # type: ignore[method-assign]

    def sightings_of(self, tag: int) -> dict[int, EnemySighting]:
        """Game loop -> the Observation's sighting of ``tag``, where it has one."""
        found: dict[int, EnemySighting] = {}
        for loop, observation in sorted(self.observations.items()):
            for sighting in observation.enemy_sightings:
                if sighting.entity.tag == tag:
                    found[loop] = sighting
        return found


def _commands(run: _Run, node_id: str) -> list[tuple[int, int, Any]]:
    """``(game loop, actor, target)`` of every command event ``node_id`` issued."""
    found: list[tuple[int, int, Any]] = []
    for event in run.events:
        if event.kind != "command" or event.node_id != node_id or event.action is None:
            continue
        actors = event.action["actor_tags"]
        assert isinstance(actors, list) and len(actors) == 1
        target = event.action["target"]
        point = tuple(target) if isinstance(target, list) else target
        found.append((event.game_loop, int(str(actors[0])), point))
    return found


def _actor_commands(run: _Run, actor: int) -> list[Event]:
    return [
        e
        for e in run.events
        if e.kind == "command" and e.action is not None and e.action["actor_tags"] == [str(actor)]
    ]


def _node_events(run: _Run, node_id: str) -> list[Event]:
    return [e for e in run.events if e.kind == "node" and e.node_id == node_id]


def _v2(world: _World, steps: int, *hooks: _Hook) -> _Run:
    return _play(world, steps, *hooks, bundle=load_v2_policy())


def _lanes(policy: Policy) -> dict[str, str]:
    """Node id -> the root lane it belongs to."""
    lane: dict[str, str] = {}
    for root in policy.roots:
        stack = [root]
        while stack:
            node_id = stack.pop()
            lane[node_id] = root
            stack.extend(policy.node(node_id).children)
    return lane


# ---------------------------------------------------------------------------
# The scout: reservation, tour and ownership
# ---------------------------------------------------------------------------


def test_one_probe_is_reserved_once_the_first_gateway_starts_and_tours_every_site() -> None:
    """D3: no scout before the first Gateway is started; then exactly one probe tours
    the enemy start and the expansions nearest it first, cycling through them, and
    no other lane ever commands it."""
    world = _ScoutWorld(_home(gateways=0))
    first_gateway = 5

    def start_a_gateway(world: _World, controller: JevController, index: int) -> None:
        if index == first_gateway:  # placed, still warping in: "started"
            world.bodies[1200] = _Body(1200, "Gateway", GATEWAY_SITES[0], progress=0.2)
            world.refresh()

    run = _v2(world, 80, start_a_gateway)
    moves = _commands(run, SCOUT_MOVE)
    assert moves[0][0] == first_gateway * STEP_LOOPS  # at once, not before
    gates = _node_events(run, "scouting.scout.unit.replace.gateway")
    early = [e for e in gates if e.game_loop < first_gateway * STEP_LOOPS]
    assert early and {(e.status, e.facts["count"]) for e in early} == {("failure", 0)}
    assert {actor for _, actor, _ in moves} == {PROBES[0]}  # one scout
    tour = [target for _, _, target in moves]
    assert tour == [ENEMY_START, ENEMY_NATURAL, THIRD, OWN_NATURAL, START, ENEMY_START]
    assert {e.node_id for e in _actor_commands(run, PROBES[0])} == {SCOUT_MOVE}
    assert run.controller.runtime.assigned_to(SCOUT_MOVE) == {PROBES[0]}
    assert _commands(run, RETREAT) == []  # nothing threatened it


def test_economy_never_reclaims_the_scout_between_its_tasks() -> None:
    """The scout stops idle as it arrives (``idle_arrival``), so on the tick its task
    completes no task holds it when the economy -- the first lane -- looks for idle
    probes. The lane's ownership (``not_assigned_to``) keeps it out; the scouting
    lane sends it on the same tick."""
    world = _ScoutWorld(_home(), idle_arrival=(6.0, 4.5))
    run = _v2(world, 70)
    scout = PROBES[0]
    moves = _commands(run, SCOUT_MOVE)
    assert len(moves) >= 4 and {actor for _, actor, _ in moves} == {scout}
    excluded = [
        e
        for e in _node_events(run, "economy.idle_probes")
        if e.facts.get("assigned_elsewhere") == 1
    ]
    # Every arrival: the scout stood idle and unheld while the economy chose, was left
    # out, and was sent on by the scouting lane on that very tick.
    assert {e.game_loop for e in excluded} == {loop for loop, _, _ in moves[1:]}
    assert {e.node_id for e in _actor_commands(run, scout)} == {SCOUT_MOVE}  # never mined
    policy = run.controller.runtime.policy
    for node_id in PROBE_SELECTIONS:  # every other Probe selection excludes the scout
        assert policy.node(node_id).args["not_assigned_to"] == SCOUT_MOVE
    probe_picks = {
        node.id
        for node in policy.nodes
        if node.operation == "select_entities"
        and node.args["collection"] == "own_units"
        and "Probe" in node.args.get("filter", {}).get("types", [])
        and node.id.split(".")[0] != "scouting"
    }
    assert probe_picks == set(PROBE_SELECTIONS)  # the list above is complete


def test_retreat_and_replacement_share_one_threat_definition() -> None:
    """The retreat trigger and the replacement's safety check must agree on what a
    threat is, or a replacement could be sent into a threat the retreat would flee:
    same filter (types, radius, the probe it is measured from), age and visibility,
    opposite verdicts. The policy cannot name a filter once, so drift fails here."""
    policy = load_v2_policy().policy
    retreat = dict(policy.node("scouting.retreat.threat").args)
    safe = dict(policy.node("scouting.scout.unit.replace.safe").args)
    verdicts = ((retreat.pop("op"), retreat.pop("value")), (safe.pop("op"), safe.pop("value")))
    assert retreat == safe
    assert verdicts == ((">=", 1), ("==", 0))
    assert policy.node("scouting.retreat.threat").operation == "sighting_count_compare"
    assert policy.node("scouting.scout.unit.replace.safe").operation == "sighting_count_compare"


# ---------------------------------------------------------------------------
# Retreat, loss and spaced replacement (bounded and visible)
# ---------------------------------------------------------------------------


def test_a_threatened_scout_retreats_home_mines_again_and_the_next_scout_is_spaced() -> None:
    """A Marine guards the way to the enemy natural. The scout sees it on its way
    there and, within the threat radius, turns home at once (its tour task is
    preempted). No probe replaces it while it retreats; home, it is released and
    mines again; the next scout leaves only a full replacement interval after the
    retreat ended, for the site the first never reached."""
    marine = _enemy(7000, "Marine", GUARD)
    world = _ScoutWorld(_home(extra=(marine,)))
    steps = 260
    run = _v2(world, steps)
    scout = PROBES[0]
    ((retreat_loop, actor, home),) = _commands(run, RETREAT)  # one retreat: bounded
    assert (actor, home) == (scout, START)
    threat = [
        e for e in _node_events(run, "scouting.retreat.threat") if e.game_loop == retreat_loop
    ]
    assert threat and threat[0].status == "success"
    assert threat[0].facts["visible"] == 1 and threat[0].facts["newest_age"] == 0.0
    cancelled = [
        e
        for e in run.events
        if e.kind == "task" and e.status == "cancelled" and e.node_id == SCOUT_MOVE
    ]
    assert [e.reason for e in cancelled] == [f"preempted by {RETREAT}"]
    retreat_tasks = [e for e in run.events if e.kind == "task" and e.node_id == RETREAT]
    (done,) = [e for e in retreat_tasks if e.status == "succeeded"]
    gathers = [e for e in _actor_commands(run, scout) if e.node_id == "economy.assign"]
    assert gathers and gathers[0].game_loop >= done.game_loop  # released: mining again
    moves = _commands(run, SCOUT_MOVE)
    first_tour = [m for m in moves if m[0] < retreat_loop]
    later = [m for m in moves if m[0] > retreat_loop]
    assert [t for _, _, t in first_tour] == [ENEMY_START, ENEMY_NATURAL]
    assert later, "a replacement scout was sent within the run"
    replacement_loop, _, target = later[0]
    gap = _seconds(replacement_loop) - _seconds(done.game_loop)
    assert REPLACE_INTERVAL - _EPSILON <= gap < REPLACE_INTERVAL + STEP_SECONDS + _EPSILON
    assert target == ENEMY_NATURAL  # the site it never reached: never marked visited
    calm = [
        e
        for e in _node_events(run, "scouting.scout.unit.replace.calm")
        if done.game_loop <= e.game_loop < replacement_loop
    ]
    assert calm and {e.status for e in calm} == {"failure"}  # visibly held back


def test_a_lost_scout_is_replaced_no_sooner_than_the_interval_after_its_reservation() -> None:
    """Scouts die: the lost scout's task fails at once (``actor lost``); a new probe
    is reserved only a full interval after the previous reservation -- each time --
    and goes to the site its predecessor never reached."""
    world = _ScoutWorld(_home())
    kills: dict[int, int] = {}  # tag -> step it died

    def kill_each_scout(world: _World, controller: JevController, index: int) -> None:
        for tag in sorted(controller.runtime.assigned_to(SCOUT_MOVE)):
            if tag in world.bodies and tag not in kills:
                kills[tag] = index + 10  # about 3.6 game seconds after it set out
        for tag, step in kills.items():
            if step == index and tag in world.bodies:
                world.kill(tag)

    steps = 2 * round(REPLACE_INTERVAL / STEP_SECONDS) + 20
    run = _v2(world, steps, kill_each_scout)
    moves = _commands(run, SCOUT_MOVE)
    reservations: dict[int, int] = {}  # actor -> its first command's loop
    for loop, actor, _ in moves:
        reservations.setdefault(actor, loop)
    actors = sorted(reservations, key=reservations.__getitem__)
    assert actors == list(PROBES[:3])  # three scouts in about two intervals: bounded
    loops = [reservations[a] for a in actors]
    for earlier, later in zip(loops, loops[1:], strict=False):
        gap = _seconds(later) - _seconds(earlier)
        assert REPLACE_INTERVAL - _EPSILON <= gap < REPLACE_INTERVAL + STEP_SECONDS + _EPSILON
    lost = [
        e
        for e in run.events
        if e.kind == "task" and e.status == "failed" and e.node_id == SCOUT_MOVE
    ]
    killed = [tag for tag in kills if tag not in world.bodies]
    assert len(lost) == len(killed) >= 2  # each loss is visible as its task's failure
    assert {e.facts["failure_cause"] for e in lost} == {"lost"}  # no cooldown: replan
    assert {e.reason for e in lost} <= {"unit lost", "actor lost"}
    assert all(target == ENEMY_START for _, _, target in moves)  # nobody arrived yet
    spaced = [
        e
        for e in _node_events(run, "scouting.scout.unit.replace.spaced")
        if loops[0] < e.game_loop < loops[1]
    ]
    assert spaced and {e.status for e in spaced} == {"failure"}
    ages = [e.facts["age"] for e in spaced]
    assert all(isinstance(a, float) and a < REPLACE_INTERVAL for a in ages)


# ---------------------------------------------------------------------------
# Honest enemy memory through the production controller and adapter
# ---------------------------------------------------------------------------


def test_hidden_enemies_never_enter_memory_until_the_bot_itself_sees_them() -> None:
    """The world reports every unseen enemy as a snapshot with its true state, and the
    enemy start is map metadata from the first step: still the Command Center there
    enters memory only on the step the scout's vision reaches it, a Marine nobody
    ever sees never does, and nothing hidden is a visible enemy."""
    command_center = _enemy(7100, "CommandCenter", ENEMY_START, health=1500.0)
    hidden = _enemy(7200, "Marine", HIDDEN_SITE)
    world = _ScoutWorld(_home(extra=(command_center, hidden)))
    watch = _Watch()
    first_seen: list[int] = []

    def sight_check(world: _World, controller: JevController, index: int) -> None:
        watch(world, controller, index)
        if not first_seen and world.sees(ENEMY_START):
            first_seen.append(world.state.game_loop)

    run = _v2(world, 40, sight_check)
    assert _commands(run, RETREAT) == []  # a Command Center is no threat to the scout
    assert first_seen, "the scout reached the enemy start"
    seen_at = first_seen[0]
    sightings = watch.sightings_of(7100)
    assert min(sightings) == seen_at  # not one step earlier
    first = sightings[seen_at]
    assert first.visible and first.last_seen_game_seconds == _seconds(seen_at)
    assert first.entity.position == ENEMY_START and first.entity.orders == ()
    for loop, observation in watch.observations.items():
        assert 7200 not in {s.entity.tag for s in observation.enemy_sightings}
        assert 7200 not in {e.tag for e in observation.visible_enemies}
        if loop < seen_at:
            assert observation.enemy_sightings == ()
            assert observation.visible_enemies == observation.remembered_enemy_structures == ()
            assert observation.enemy_start_locations == (ENEMY_START,)  # metadata only


def test_an_unseen_unit_keeps_its_last_seen_state_and_is_forgotten_after_its_ttl() -> None:
    """The scout passes a Marine 8.5 from its path (in sight, outside the threat
    radius). Out of sight the Marine moves and is wounded: memory keeps where and how
    it was last seen, marked not visible, until the sighting is 30 game seconds old;
    then it is gone, while the world still reports the Marine as a fogged snapshot."""
    beside_path = (81.5, 69.5)  # 8.49 from the START -> ENEMY_START diagonal
    marine = _enemy(7000, "Marine", beside_path)
    world = _ScoutWorld(_home(extra=(marine,)))
    watch = _Watch()
    moved = (90.0, 60.0)

    def move_it_once_unseen(world: _World, controller: JevController, index: int) -> None:
        watch(world, controller, index)
        body = world.bodies[7000]
        if body.position == beside_path and 7000 in world.ever_seen:
            if not world.sees(beside_path):
                body.position, body.health = moved, 7.0
                world.refresh()

    run = _v2(world, 130, move_it_once_unseen)
    assert _commands(run, RETREAT) == []  # in sight, but never within the threat radius
    sightings = watch.sightings_of(7000)
    visible = [loop for loop, s in sightings.items() if s.visible]
    assert visible, "the scout saw the Marine"
    last_seen = _seconds(max(visible))
    remembered = {loop: s for loop, s in sightings.items() if not s.visible}
    assert remembered
    for loop, sighting in remembered.items():
        assert sighting.last_seen_game_seconds == last_seen
        assert sighting.entity.position == beside_path and sighting.entity.health == 40.0
        assert _seconds(loop) - last_seen < SIGHTING_UNIT_TTL_SECONDS
    expired = [loop for loop in watch.observations if loop > max(visible) and loop not in sightings]
    assert expired, "the run outlived the sighting"
    assert _seconds(min(expired)) - last_seen >= SIGHTING_UNIT_TTL_SECONDS
    assert _seconds(min(expired)) - last_seen < SIGHTING_UNIT_TTL_SECONDS + STEP_SECONDS
    assert world.bodies[7000].position == moved  # still there, reported as a snapshot


def test_a_remembered_structure_stays_until_its_site_is_seen_empty() -> None:
    """Two structures beside the enemy start are seen on the first visit. Out of sight
    one is destroyed and the other loses health: memory keeps both exactly as seen.
    When the tour brings the scout back, the destroyed one's empty site removes it
    (also from the remembered structures) and the standing one is seen afresh."""
    barracks = _enemy(7300, "Barracks", (122.5, 114.5), health=1000.0)
    depot = _enemy(7301, "SupplyDepot", (114.5, 122.5), health=400.0)
    world = _ScoutWorld(_home(extra=(barracks, depot)))
    watch = _Watch()
    changed: list[int] = []

    def change_them_unseen(world: _World, controller: JevController, index: int) -> None:
        watch(world, controller, index)
        seen = {7300, 7301} <= world.ever_seen
        if seen and not changed and not world.sees((118.5, 118.5)):
            world.kill(7300)
            world.bodies[7301].health = 150.0
            world.refresh()
            changed.append(world.state.game_loop)

    run = _v2(world, 130, change_them_unseen)
    assert _commands(run, RETREAT) == []  # no defences among them: the tour went on
    assert changed, "the scout left the enemy start"
    revisit = [
        loop for loop, s in watch.sightings_of(7301).items() if s.visible and loop > changed[0]
    ]
    assert revisit, "the tour came back to the enemy start"
    back = min(revisit)
    between = {loop: o for loop, o in watch.observations.items() if changed[0] <= loop < back}
    assert between
    for observation in between.values():
        kept = {s.entity.tag: s for s in observation.enemy_sightings}
        assert set(kept) == {7300, 7301} and not kept[7300].visible
        assert kept[7301].entity.health == 400.0  # as last seen, not the snapshot's 150
        assert {e.tag for e in observation.remembered_enemy_structures} == {7300, 7301}
    again = watch.observations[back]
    assert [s.entity.tag for s in again.enemy_sightings] == [7301]  # the empty site
    (refreshed,) = again.enemy_sightings
    assert refreshed.visible and refreshed.entity.health == 150.0
    assert [e.tag for e in again.remembered_enemy_structures] == []


# ---------------------------------------------------------------------------
# The lane owns its actor: runtime-level ticks of the packaged v2 policy
# ---------------------------------------------------------------------------

_MINERALS = tuple(
    Entity(3000 + i, "MineralField", (26.5 + i, 22.5), 0.0, is_structure=True) for i in range(8)
)
_NEXUS = Entity(NEXUS, "Nexus", START, 1000.0, is_structure=True, ready=True, idle=True)
_GATEWAY = Entity(
    1200, "Gateway", GATEWAY_SITES[0], 500.0, is_structure=True, ready=True, idle=True, powered=True
)
_SCOUT = PROBES[0]


def _obs(
    seconds: float,
    *,
    units: tuple[Entity, ...],
    minerals: int = 0,
    sightings: tuple[EnemySighting, ...] = (),
) -> Observation:
    """One Nexus and one Gateway (no Pylon), supply 14/15: a buffer Pylon is due."""
    return Observation(
        game_loop=round(seconds * GAME_LOOPS_PER_SECOND),
        game_seconds=seconds,
        minerals=minerals,
        supply_used=14,
        supply_cap=15,
        own_units=units,
        own_structures=(_NEXUS, _GATEWAY),
        visible_enemies=tuple(s.entity for s in sightings if s.visible),
        remembered_enemy_structures=(),
        start_location=START,
        enemy_start_locations=(ENEMY_START,),
        expansion_locations=tuple(sorted(EXPANSIONS)),
        map_center=CENTER,
        mineral_fields=_MINERALS,
        enemy_sightings=sightings,
    )


def _mining(tag: int) -> Entity:
    return Entity(tag, "Probe", (28.0, 26.0), 20.0, orders=(Order("HARVEST_GATHER", 3000),))


def _idle_at(tag: int, where: tuple[float, float]) -> Entity:
    return Entity(tag, "Probe", where, 20.0)


def _two_ticks(policy: Policy) -> tuple[JevRuntime, TickResult, TickResult]:
    """Tick 1: the only probe mines (no minerals) and becomes the scout. Tick 2: it
    stands idle at the enemy start -- its task completes on this very tick -- while
    100 minerals would buy the buffer Pylon it is the only possible builder for."""
    runtime = JevRuntime(policy, run_id=uuid.uuid4().hex)
    first = runtime.tick(_obs(10.0, units=(_mining(_SCOUT),)))
    second = runtime.tick(_obs(11.0, units=(_idle_at(_SCOUT, ENEMY_START),), minerals=100))
    return runtime, first, second


def _facts(result: TickResult, node_id: str) -> dict[str, Any]:
    (event,) = [e for e in result.events if e.kind == "node" and e.node_id == node_id]
    return dict(event.facts)


def test_builders_never_take_an_idle_scout_between_its_tasks() -> None:
    """The economy's side is the controller scenario above; here the buffer-Pylon
    builder, whose only candidate is the idle, unheld scout, leaves it out."""
    _, _, second = _two_ticks(load_v2_policy().policy)
    ended = [e.status for e in second.events if e.kind == "task" and e.node_id == SCOUT_MOVE]
    assert ended[:2] == ["running", "succeeded"]  # its task ended on this very tick
    facts = _facts(second, "supply.buffer.worker")
    assert (facts["assigned_elsewhere"], facts["selected"]) == (1, 0)
    assert [c for c in second.commands if c.ability.startswith("PROTOSSBUILD")] == []


@pytest.mark.parametrize(
    ("selection", "taker"),
    [("economy.idle_probes", "economy.assign"), ("supply.buffer.worker", "supply.buffer.build")],
)
def test_without_the_lane_ownership_another_lane_would_take_the_scout(
    selection: str, taker: str
) -> None:
    """Calibration anchor for both exclusions: the same two ticks with one selection's
    ``not_assigned_to`` removed hand the idle scout to that lane instead."""
    document = json.loads(load_v2_policy().policy_bytes)
    for node in document["nodes"]:
        if node["id"] == selection:
            del node["args"]["not_assigned_to"]
    _, _, second = _two_ticks(parse_policy(document))
    by_scout = [c for c in second.commands if _SCOUT in c.actor_tags]
    assert [c.node_id for c in by_scout] == [taker]


# ---------------------------------------------------------------------------
# Budgets: scouting is bounded and never takes a later lane's share
# ---------------------------------------------------------------------------


def _evaluations(result: TickResult, lanes: dict[str, str]) -> dict[str, int]:
    """Node evaluations per lane this tick (each evaluation emits one node event)."""
    counts = dict.fromkeys(set(lanes.values()), 0)
    for event in result.events:
        if event.kind == "node":
            counts[lanes[event.node_id]] += 1
    return counts


@pytest.mark.parametrize("budget", [7, 8, 12, 20, 40, 256])
def test_scouting_never_consumes_the_evaluation_budget_of_later_lanes(budget: int) -> None:
    """Every lane, scouting included, gets at most its fair share of what is left;
    the lanes after scouting are always evaluated, at any legal budget."""
    policy = load_v2_policy().policy
    lanes = _lanes(policy)
    roots = policy.roots
    runtime = JevRuntime(
        policy, run_id=uuid.uuid4().hex, tick_config=TickConfig(max_node_evaluations=budget)
    )
    result = runtime.tick(_obs(10.0, units=(_mining(_SCOUT),)))
    used = _evaluations(result, lanes)
    remaining = budget
    for position, root in enumerate(roots):
        share = math.ceil(remaining / (len(roots) - position))
        assert 1 <= used[root] <= share, (root, used[root], share)
        if root == "scouting":
            assert remaining - used[root] >= len(roots) - position - 1  # later lanes' room
        remaining -= used[root]
    later = roots[roots.index("scouting") + 1 :]
    evaluated = {e.node_id for e in result.events if e.kind == "node"}
    assert set(later) <= evaluated
    budget_hits = [e for e in result.events if e.reason == NODE_BUDGET_REASON]
    if budget == 256:
        assert budget_hits == []  # the default budget fits every lane
        scouting_nodes = sum(1 for lane in lanes.values() if lane == "scouting")
        assert used["scouting"] <= scouting_nodes  # each node at most once: no loop
        assert any(c.node_id == SCOUT_MOVE for c in result.commands)


# ---------------------------------------------------------------------------
# The new operations and the Observation contract
# ---------------------------------------------------------------------------


def _marine(tag: int, where: tuple[float, float]) -> Entity:
    return Entity(tag, "Marine", where, 45.0)


def _scv(tag: int, where: tuple[float, float]) -> Entity:
    return Entity(tag, "SCV", where, 45.0)


def _counting_policy(**count_args: Any) -> Policy:
    """``lane``: bind the home point, then count sightings near it."""
    nodes = [
        {
            "id": "lane",
            "label": "lane",
            "kind": "sequence",
            "children": ["lane.here", "lane.count"],
            "operation": None,
            "args": {},
        },
        {
            "id": "lane.here",
            "label": "home",
            "kind": "select",
            "children": [],
            "operation": "select_point",
            "args": {
                "from": "start_location",
                "toward": "map_center",
                "distance": 0,
                "bind": "here",
            },
        },
        {
            "id": "lane.count",
            "label": "count",
            "kind": "condition",
            "children": [],
            "operation": "sighting_count_compare",
            "args": {"op": ">=", "value": 0, **count_args},
        },
    ]
    document = {
        "schema_version": 1,
        "family": "jev",
        "version": 2,
        "roots": ["lane"],
        "parameters": {},
        "nodes": nodes,
    }
    return parse_policy(document)


_SIGHTINGS = (
    EnemySighting(_marine(7000, (32.0, 30.5)), 50.0, True),  # in sight, near home
    EnemySighting(_marine(7001, (34.0, 30.5)), 41.0, False),  # last seen near home 9 s ago
    EnemySighting(_scv(7002, (31.0, 30.5)), 50.0, True),  # a worker near home
    EnemySighting(_marine(7003, (90.0, 90.0)), 49.0, False),  # last seen far away
)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"max_age": 60}, (4, 2, 0.0, 9.0)),
        ({"max_age": 5}, (3, 2, 0.0, 1.0)),  # 7001 is 9 s old
        ({"max_age": 60, "visibility": "visible"}, (2, 2, 0.0, 0.0)),
        ({"max_age": 60, "visibility": "remembered"}, (2, 0, 1.0, 9.0)),
        (  # last-seen positions near home (7003 was seen far away), workers excluded
            {
                "max_age": 60,
                "filter": {"within": {"point": "$here", "distance": 7}, "exclude_types": ["SCV"]},
            },
            (2, 1, 0.0, 9.0),
        ),
        ({"max_age": 0.5, "visibility": "remembered"}, (0, 0, None, None)),
    ],
)
def test_sighting_count_compare_reads_timestamped_memory(
    args: dict[str, Any], expected: tuple[int, int, float | None, float | None]
) -> None:
    runtime = JevRuntime(_counting_policy(**args), run_id=uuid.uuid4().hex)
    observation = dataclasses.replace(
        _obs(50.0, units=()), enemy_sightings=_SIGHTINGS, visible_enemies=()
    )
    facts = _facts(runtime.tick(observation), "lane.count")
    count, visible, newest, oldest = expected
    assert (facts["count"], facts["visible"], facts["remembered"]) == (
        count,
        visible,
        count - visible,
    )
    assert (facts["newest_age"], facts["oldest_age"]) == (newest, oldest)


def test_assignment_age_outlives_the_member_and_never_compares_as_long_ago() -> None:
    document = json.loads(load_v2_policy().policy_bytes)
    policy = parse_policy(document)
    runtime = JevRuntime(policy, run_id=uuid.uuid4().hex)
    assert runtime.assignment_age(SCOUT_MOVE) is None  # never: replacement allowed
    first = runtime.tick(_obs(10.0, units=(_mining(_SCOUT),)))
    assert _facts(first, "scouting.scout.unit.replace.spaced")["age"] is None
    runtime.tick(_obs(20.0, units=(_mining(PROBES[1]),)))  # the scout is gone
    assert runtime.assigned_to(SCOUT_MOVE) == frozenset()
    assert runtime.assignment_age(SCOUT_MOVE) == pytest.approx(10.0)  # kept after it
    blocked = runtime.tick(_obs(30.0, units=(_mining(PROBES[1]),)))
    facts = _facts(blocked, "scouting.scout.unit.replace.spaced")
    assert facts["age"] == pytest.approx(20.0)
    assert [c for c in blocked.commands if c.node_id == SCOUT_MOVE] == []
    allowed = runtime.tick(_obs(70.0, units=(_mining(PROBES[1]),)))
    (move,) = [c for c in allowed.commands if c.node_id == SCOUT_MOVE]
    assert move.actor_tags == (PROBES[1],)
    assert runtime.assignment_age(SCOUT_MOVE) == 0.0


@pytest.mark.parametrize(
    ("operation", "args", "message"),
    [
        ("sighting_count_compare", {"max_age": -1, "op": ">=", "value": 1}, "must be >= 0"),
        (
            "sighting_count_compare",
            {"max_age": 5, "visibility": "hidden", "op": ">=", "value": 1},
            "must be one of any, visible, remembered",
        ),
        ("sighting_count_compare", {"op": ">=", "value": 1}, "requires argument 'max_age'"),
        (
            "assignment_age_compare",
            {"node": "lane.pick", "op": ">=", "seconds": 60},
            "must reference an action node",
        ),
        (
            "assignment_age_compare",
            {"node": "lane.go", "op": ">=", "seconds": 1e9},
            "must be <= 86400",
        ),
    ],
)
def test_new_operation_arguments_are_validated(
    operation: str, args: dict[str, Any], message: str
) -> None:
    kinds = {"lane.pick": "select", "lane.go": "action"}
    issues = validate_operation_args(operation, args, parameters={}, node_kinds=kinds)  # type: ignore[arg-type]
    assert any(message in text for _, text in issues), issues


def test_enemy_type_names_in_a_sighting_filter_are_not_limited_to_own_types() -> None:
    issues = validate_operation_args(
        "sighting_count_compare",
        {"filter": {"types": ["Zergling", "Bunker"]}, "max_age": 5, "op": ">=", "value": 1},
        parameters={},
        node_kinds={},
    )
    assert issues == []


def _with_sightings(value: object) -> Observation:
    return dataclasses.replace(_obs(10.0, units=()), enemy_sightings=value)  # type: ignore[arg-type]


_GOOD = EnemySighting(_marine(7000, (40.0, 40.0)), 9.0, False)


@pytest.mark.parametrize(
    ("bad", "fragment"),
    [
        (lambda: _with_sightings([_GOOD]), "enemy_sightings must be a tuple"),
        (
            lambda: _with_sightings((_GOOD.entity,)),
            r"enemy_sightings\[0\] must be an EnemySighting",
        ),
        (
            lambda: _with_sightings((dataclasses.replace(_GOOD, last_seen_game_seconds=11.0),)),
            "last_seen_game_seconds",
        ),
        (
            lambda: _with_sightings((dataclasses.replace(_GOOD, last_seen_game_seconds=math.nan),)),
            "last_seen_game_seconds",
        ),
        (lambda: _with_sightings((dataclasses.replace(_GOOD, visible=1),)), "visible"),
        (
            lambda: _with_sightings(
                (dataclasses.replace(_GOOD, entity=_marine(7000, (math.inf, 0.0))),)
            ),
            r"enemy_sightings\[0\].entity.position",
        ),
        (lambda: _with_sightings((_GOOD, _GOOD)), "repeats tag 7000"),
        (
            lambda: _with_sightings(
                tuple(
                    EnemySighting(_marine(7000 + i, (40.0, 40.0)), 9.0, False)
                    for i in range(MAX_ENEMY_SIGHTINGS + 1)
                )
            ),
            f"limit is {MAX_ENEMY_SIGHTINGS}",
        ),
    ],
)
def test_malformed_sightings_are_rejected_before_any_state_change(bad: Any, fragment: str) -> None:
    runtime = JevRuntime(load_v2_policy().policy, run_id=uuid.uuid4().hex)
    first = runtime.tick(_obs(9.0, units=()))
    with pytest.raises(ValueError, match=fragment):
        runtime.tick(bad())
    assert runtime.last_sequence == first.events[-1].sequence  # nothing mutated
    assert runtime.tick(_obs(10.0, units=())).ticked


def test_sightings_are_additive_the_frozen_v1_tick_is_unchanged() -> None:
    """V1 never reads the memory: with or without it, the same commands and events."""
    policy = load_v1_policy().policy
    run_id = uuid.uuid4().hex
    plain = JevRuntime(policy, run_id=run_id)
    remembering = JevRuntime(policy, run_id=run_id)
    units = (_mining(_SCOUT), _idle_at(PROBES[1], (29.0, 26.0)))
    marine = EnemySighting(_marine(7001, (34.0, 30.5)), 9.0, False)  # near home, 1-3 s ago
    for seconds in (10.0, 11.0, 12.0):
        observation = _obs(seconds, units=units, minerals=200)
        with_memory = dataclasses.replace(observation, enemy_sightings=(marine,))
        left, right = plain.tick(observation), remembering.tick(with_memory)
        assert left.commands == right.commands
        assert [e.to_dict() for e in left.events] == [e.to_dict() for e in right.events]


def test_sighting_age_is_never_negative_and_observations_default_to_no_memory() -> None:
    assert _GOOD.age(19.0) == 10.0 and _GOOD.age(5.0) == 0.0
    fields = {f.name: f for f in dataclasses.fields(Observation)}
    assert fields["enemy_sightings"].default == ()
