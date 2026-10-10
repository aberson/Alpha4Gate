"""Visible-state adapter between burnysc2 and the Jev runtime (plan D3/D4).

The adapter is mechanical in both directions; it never makes a gameplay choice.

**Observation.** :meth:`Sc2Adapter.observe` normalizes the *visible* state of a
burnysc2 ``BotAI`` into the :class:`~jev.contracts.Observation` contract:

* ``state.game_loop`` -> ``game_loop``; ``game_seconds`` is the loop divided by
  :data:`GAME_LOOPS_PER_SECOND` (burnysc2's ``BotAI.time`` convention).
* ``minerals`` / ``supply_used`` / ``supply_cap`` -> the same fields
  (non-negative integers).
* ``units`` -> ``own_units`` and ``structures`` -> ``own_structures``, orders
  included. Own structures also carry ``ready`` / ``idle`` (SC2's ``is_ready`` /
  ``is_idle``) and ``powered``: SC2's ``is_powered`` for the types in
  :data:`~jev.operations.REQUIRES_POWER`, True for structures that need no Pylon
  power (Nexus, Pylon).
* ``enemy_units`` + ``enemy_structures`` -> ``visible_enemies``: only entries SC2
  reports as visible (no fogged snapshots), and never their orders.
* ``remembered_enemy_structures``: enemy structures seen earlier and not visible
  now. A memory entry is refreshed on every sighting and dropped once vision
  covers its position without seeing it (D3: revalidated on sight).
* ``enemy_sightings`` (plan J2 Step 215): the timestamped memory of every enemy
  unit and structure the bot has seen, keyed by tag
  (:class:`~jev.contracts.EnemySighting`). Honest by construction: an entry is
  created or refreshed only from an entity SC2 reports as *visible* in this game
  step (never a fogged snapshot, a sensor-tower blip, burnysc2's previous-frame
  maps or map metadata such as the enemy start location), with its orders never
  read. An entity out of sight keeps its last-seen state and time and is marked
  not visible; an unseen unit expires
  :data:`~jev.contracts.SIGHTING_UNIT_TTL_SECONDS` after it was last seen; a
  structure stays until vision covers its last-seen position without it; above
  :data:`~jev.contracts.MAX_ENEMY_SIGHTINGS` entries the oldest are evicted.
* ``mineral_field`` -> ``mineral_fields`` (visible patches only).
* ``vespene`` -> ``vespene`` and ``vespene_geyser`` -> ``vespene_geysers`` (plan J2
  Step 216): the gas bank and the visible geysers with their ``vespene_contents``.
  Own gas buildings (:data:`~jev.operations.GAS_STRUCTURE_TYPES`) also carry
  ``vespene_contents`` and SC2's ``assigned_harvesters`` / ``ideal_harvesters``
  (a worker inside a gas building is out of sight but still in SC2's count).
  Own units carry ``carrying_vespene`` (burnysc2's buff-based
  ``is_carrying_vespene``, every race's carry-gas buff, rich geysers included).
* ``start_location`` / ``enemy_start_locations`` / ``expansion_locations_list`` /
  ``game_info.map_center``: map metadata (D3 permits it), read once.
* ``expansion_paths`` (plan J2 Step 217): SC2's ground path distance from the start
  location to each expansion location the graph asked about
  (:meth:`JevRuntime.path_requests`), queried through
  :meth:`GamePort.pathing_distance` by :meth:`Sc2Adapter.query_paths` in the
  controller's asynchronous step before the tick, at most
  :data:`~jev.operations.MAX_EXPANSION_CANDIDATES` per map, and cached for the
  match (static map paths, plan D4). SC2's "no path" is ``None``; a location never
  answered has no fact, so it is not yet eligible -- the adapter never assumes a
  path. The production port asks burnysc2's ``Client.query_pathing`` from the start
  location, as burnysc2's own ``BotAI.get_next_expansion`` does.
* ``location_vision`` (plan J2 Step 217): for each of those queried locations,
  whether it is in the bot's own sight now (:meth:`GamePort.is_visible`, SC2's
  answer, asked at most once per position per step) and the latest game time it
  was, refreshed on every step -- :meth:`Sc2Adapter.remember` between ticks too, so
  a glimpse is never lost. Unknown is not safe (D4): never seen is ``None``.

Between policy ticks the controller needs only the clock
(:meth:`Sc2Adapter.game_seconds`) and the enemy memories
(:meth:`Sc2Adapter.remember`, the same memory rules), so a full Observation is
built only when the runtime will tick while a glimpse between ticks is still
remembered with its own time.

Per entity: ``tag`` (uint64), ``name`` (SC2's unit type name, e.g. ``"Probe"``,
the spelling the operation tables use), ``position``, ``health``, ``shield`` (a
non-negative number), ``build_progress``, ``is_structure``, ``is_flying`` and,
for own entities, ``orders``: ``order.ability.id.name`` (burnysc2 reports the
generic ability, e.g.
``HARVEST_GATHER``), the target (tag, point, or None -- burnysc2 reports tag 0
for "no target") and progress; at most :data:`~jev.contracts.MAX_ENTITY_ORDERS`
queued orders are read. Game state is untrusted input: anything malformed raises
:class:`ValueError` naming the field, and no other exception type escapes.

**Commands.** :meth:`Sc2Adapter.issue` turns the runtime's graph-selected
:class:`~jev.contracts.CommandSpec` records into explicit SC2 unit commands
through a :class:`GamePort`. A command is only issued when it is attributed to a
policy node and to a task the runtime is awaiting, names exactly one actor Jev
owns in the current observation, uses a Jev command ability, and targets a
visible mineral field or an own finished gas building with gas left (gather), a
visible vespene geyser (a gas build: the geyser *unit* is the target, as
burnysc2's ``Unit.build_gas`` sends it -- never a point and never a placement
query), a visible enemy (unit attack) or a point. For a point build -- a Pylon, a Gateway or
an expansion plan's Nexus -- the API tests placement legality over the
graph-selected candidates (at most :data:`~jev.operations.MAX_PLACEMENT_CANDIDATES`)
and the first legal one is used.
Issuing is never success (D4): SC2's acceptance is reported with
:meth:`~jev.runtime.JevRuntime.mark_command_accepted` and the runtime still waits
for observation (an acceptance the runtime does not apply is recorded as
``ignored`` and neither counted nor awaited); every refusal -- by the adapter,
by SC2 at issue time, or by an SC2 action error reported later for the same
actor and ability (:meth:`Sc2Adapter.collect_rejections`) -- goes to
:meth:`~jev.runtime.JevRuntime.mark_command_rejected`, which owns retries. Every
handled command is kept as a :class:`CommandRecord` naming its node and task.

:class:`BotAIPort` is the production :class:`GamePort` over a live ``BotAI``.
This module imports burnysc2 only inside :class:`BotAIPort`'s methods.
"""

from __future__ import annotations

import functools
import itertools
import re
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from jev.contracts import (
    MAX_ABS_NUMBER,
    MAX_COORDINATE,
    MAX_ENEMY_SIGHTINGS,
    MAX_ENTITY_ORDERS,
    MAX_OBSERVED_ENTITIES,
    MAX_OBSERVED_LOCATIONS,
    MAX_TAG,
    SIGHTING_UNIT_TTL_SECONDS,
    CommandSpec,
    EnemySighting,
    Entity,
    LocationVision,
    Observation,
    Order,
    PathFact,
    Point,
    Target,
    full_match,
    is_bounded_number,
    safe_exception_text,
    safe_repr,
)
from jev.operations import (
    ATTACK_ABILITY,
    BUILD_ABILITY,
    COMMAND_ABILITIES,
    EXPANSION_BUILD_ABILITY,
    GAS_BUILD_ABILITY,
    GAS_STRUCTURE_TYPES,
    GATHER_ABILITY,
    MAX_EXPANSION_CANDIDATES,
    MAX_PLACEMENT_CANDIDATES,
    MOVE_ABILITY,
    REQUIRES_POWER,
    TRAIN_ABILITY,
)
from jev.runtime import JevRuntime

if TYPE_CHECKING:
    from sc2.bot_ai import BotAI

__all__ = [
    "ABILITY_NAME_RE",
    "BotAIPort",
    "CommandOutcome",
    "CommandRecord",
    "GAME_LOOPS_PER_SECOND",
    "GamePort",
    "MAX_ACTION_ERRORS",
    "MAX_COMMAND_RECORDS",
    "NO_TARGET_TAG",
    "Sc2Adapter",
    "TYPE_NAME_RE",
]

#: burnysc2 ``BotAI.time``: SC2 "faster" speed runs 22.4 game loops per second.
GAME_LOOPS_PER_SECOND: Final = 22.4
#: burnysc2 reports an order without a target as target tag 0 (the proto default).
NO_TARGET_TAG: Final = 0
#: SC2 unit type names (``Probe``, ``MineralField750``) and AbilityId names.
TYPE_NAME_RE: Final = re.compile(r"\A[A-Za-z][A-Za-z0-9_]{0,63}\Z")
ABILITY_NAME_RE: Final = re.compile(r"\A[A-Z][A-Z0-9_]{0,127}\Z")
#: Attribution records kept for inspection (oldest dropped first).
MAX_COMMAND_RECORDS: Final = 256
#: Most action errors one port report may carry.
MAX_ACTION_ERRORS: Final = 1024

#: ``ignored``: SC2 accepted it, but the runtime no longer awaited that task or
#: placement (its acceptance report was not applied), so nothing awaits it.
CommandOutcome = Literal["accepted", "rejected", "ignored"]

#: Point builds: placement legality is tested over the candidates (a Nexus too).
_BUILD_ABILITIES: Final = frozenset(BUILD_ABILITY.values()) | frozenset(
    EXPANSION_BUILD_ABILITY.values()
)
_GAS_BUILD_ABILITIES: Final = frozenset(GAS_BUILD_ABILITY.values())
_TRAIN_ABILITIES: Final = frozenset(TRAIN_ABILITY.values())
#: burnysc2's own ``Unit.move`` issues MOVE_MOVE; every other Jev command ability
#: is issued under its own AbilityId name.
_SC2_ISSUE_ABILITY: Final[Mapping[str, str]] = {MOVE_ABILITY: "MOVE_MOVE"}


class GamePort(Protocol):
    """The mechanical SC2 interface the adapter queries and commands through."""

    def is_visible(self, point: Point) -> bool:
        """Whether the bot currently has vision of ``point``."""
        ...

    async def placement_legal(self, ability: str, sites: Sequence[Point]) -> Sequence[bool]:
        """SC2's placement verdict for ``ability`` at each site, in order."""
        ...

    async def issue(self, ability: str, actor: object, target: object) -> str | None:
        """Send one unit command; None when SC2 accepted it, else the refusal."""
        ...

    def action_errors(self) -> Sequence[tuple[int, str, str]]:
        """``(unit tag, generic ability name, result name)`` per action SC2 refused.

        SC2's action errors carry no target, so actor and ability are the finest
        match available against an accepted command.
        """
        ...

    async def leave(self) -> None:
        """Leave the game (a clean stop or time limit)."""
        ...

    async def pathing_distance(self, start: Point, end: Point) -> float | None:
        """SC2's ground path distance from ``start`` to ``end``; None: no path."""
        ...


@dataclass(frozen=True)
class CommandRecord:
    """Attribution record of one command the adapter handled.

    ``target`` is what was sent to SC2 (the accepted placement for a build);
    ``candidates`` are the placement sites the API tested. ``reason`` is rendered,
    capped text.
    """

    node_id: str
    task_id: str
    ability: str
    actor_tags: tuple[int, ...]
    target: Target
    candidates: tuple[Point, ...]
    outcome: CommandOutcome
    reason: str
    game_loop: int


@dataclass(frozen=True)
class _MapMetadata:
    start_location: Point
    enemy_start_locations: tuple[Point, ...]
    expansion_locations: tuple[Point, ...]
    map_center: Point


# ---------------------------------------------------------------------------
# Checked conversions (ValueError naming the field on anything malformed)
# ---------------------------------------------------------------------------


def _tag(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_TAG:
        raise ValueError(f"{where} must be a uint64 unit tag, got {safe_repr(value)}")
    return int(value)


def _number(value: object, where: str, max_abs: float = MAX_ABS_NUMBER) -> float:
    if not is_bounded_number(value, max_abs):
        raise ValueError(f"{where} must be a finite number, got {safe_repr(value)}")
    assert isinstance(value, int | float)
    return float(value)


def _count(value: object, where: str) -> int:
    """A non-negative integer; burnysc2 also stores some counts as integral floats."""
    if isinstance(value, int | float) and is_bounded_number(value) and value >= 0:
        if isinstance(value, int) or value.is_integer():
            return int(value)
    raise ValueError(f"{where} must be a non-negative integer, got {safe_repr(value)}")


def _amount(value: object, where: str) -> float:
    """A finite, bounded, non-negative number (shields)."""
    number = _number(value, where)
    if number < 0:
        raise ValueError(f"{where} must not be negative, got {safe_repr(value)}")
    return number


def _flag(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{where} must be a bool, got {safe_repr(value)}")
    return value


def _point(value: object, where: str) -> Point:
    """An ``(x, y)`` pair (burnysc2's Point2 is a tuple) -> a plain float tuple."""
    if not isinstance(value, tuple | list) or len(value) != 2:
        raise ValueError(f"{where} must be an (x, y) point, got {safe_repr(value)}")
    return (
        _number(value[0], f"{where}.x", MAX_COORDINATE),
        _number(value[1], f"{where}.y", MAX_COORDINATE),
    )


def _items(value: object, where: str, cap: int) -> list[Any]:
    """At most ``cap`` items of an iterable collection (never more pulled than cap + 1)."""
    if isinstance(value, str | bytes | Mapping) or not isinstance(value, Iterable):
        raise ValueError(f"{where} must be a collection, got {safe_repr(value)}")
    items = list(itertools.islice(value, cap + 1))
    if len(items) > cap:
        raise ValueError(f"{where} has more than {cap} entries")
    return items


def _command_point(value: object) -> Point | None:
    """A command's point target as a plain float tuple, or None when malformed."""
    try:
        return _point(value, "command target")
    except ValueError:
        return None


def _order_target(value: object, where: str) -> Target:
    if value is None:
        return None
    if isinstance(value, tuple | list):
        return _point(value, where)
    tag = _tag(value, where)
    return None if tag == NO_TARGET_TAG else tag


def _orders(value: object, where: str) -> tuple[Order, ...]:
    if isinstance(value, str | bytes | Mapping) or not isinstance(value, Iterable):
        raise ValueError(f"{where} must be a collection, got {safe_repr(value)}")
    orders: list[Order] = []
    for index, order in enumerate(itertools.islice(value, MAX_ENTITY_ORDERS)):
        here = f"{where}[{index}]"
        ability = order.ability.id.name
        if not full_match(ABILITY_NAME_RE, ability):
            raise ValueError(f"{here}.ability must be an AbilityId name, got {safe_repr(ability)}")
        target = _order_target(order.target, f"{here}.target")
        orders.append(Order(ability, target, _number(order.progress, f"{here}.progress")))
    return tuple(orders)


def _entity(
    unit: Any, where: str, *, own: bool, structure_flags: bool, geyser: bool = False
) -> Entity:
    tag = _tag(unit.tag, f"{where}.tag")
    type_name = unit.name
    if not full_match(TYPE_NAME_RE, type_name):
        raise ValueError(f"{where}.name must be an SC2 unit type name, got {safe_repr(type_name)}")
    ready: bool | None = None
    idle: bool | None = None
    powered: bool | None = None
    if structure_flags:
        ready = _flag(unit.is_ready, f"{where}.is_ready")
        idle = _flag(unit.is_idle, f"{where}.is_idle")
        powered = (
            _flag(unit.is_powered, f"{where}.is_powered") if type_name in REQUIRES_POWER else True
        )
    # Gas facts are read only where SC2 fills them: a geyser's gas, and an own gas
    # building's gas and workers (every other entity keeps the 0 defaults).
    gas_building = own and structure_flags and type_name in GAS_STRUCTURE_TYPES
    vespene = assigned = ideal = 0
    if geyser or gas_building:
        vespene = _count(unit.vespene_contents, f"{where}.vespene_contents")
    if gas_building:
        assigned = _count(unit.assigned_harvesters, f"{where}.assigned_harvesters")
        ideal = _count(unit.ideal_harvesters, f"{where}.ideal_harvesters")
    carrying = False
    if own and not structure_flags:
        carrying = _flag(unit.is_carrying_vespene, f"{where}.is_carrying_vespene")
    return Entity(
        tag=tag,
        type_name=type_name,
        position=_point(unit.position, f"{where}.position"),
        health=_number(unit.health, f"{where}.health"),
        shield=_amount(unit.shield, f"{where}.shield"),
        build_progress=_number(unit.build_progress, f"{where}.build_progress"),
        orders=_orders(unit.orders, f"{where}.orders") if own else (),
        is_structure=_flag(unit.is_structure, f"{where}.is_structure"),
        is_flying=_flag(unit.is_flying, f"{where}.is_flying"),
        ready=ready,
        idle=idle,
        powered=powered,
        vespene_contents=vespene,
        assigned_harvesters=assigned,
        ideal_harvesters=ideal,
        carrying_vespene=carrying,
    )


def _own(raw: object, where: str, *, structures: bool, actors: dict[int, Any]) -> list[Entity]:
    entities: list[Entity] = []
    for index, unit in enumerate(_items(raw, where, MAX_OBSERVED_ENTITIES)):
        entity = _entity(unit, f"{where}[{index}]", own=True, structure_flags=structures)
        if entity.tag in actors:
            raise ValueError(f"{where}[{index}] repeats own tag {entity.tag}")
        actors[entity.tag] = unit
        entities.append(entity)
    return entities


def _visible(
    raw: object, where: str, found: dict[int, tuple[Entity, Any]], *, geyser: bool = False
) -> None:
    for index, unit in enumerate(_items(raw, where, MAX_OBSERVED_ENTITIES)):
        here = f"{where}[{index}]"
        if not _flag(unit.is_visible, f"{here}.is_visible"):
            continue  # a fogged snapshot is not visible state
        entity = _entity(unit, here, own=False, structure_flags=False, geyser=geyser)
        if entity.tag in found:
            raise ValueError(f"{here} repeats tag {entity.tag}")
        found[entity.tag] = (entity, unit)
    if len(found) > MAX_OBSERVED_ENTITIES:
        raise ValueError(f"{where} brings visible entities above {MAX_OBSERVED_ENTITIES}")


def _locations(raw: object, where: str) -> tuple[Point, ...]:
    items = _items(raw, where, MAX_OBSERVED_LOCATIONS)
    return tuple(_point(point, f"{where}[{index}]") for index, point in enumerate(items))


def _read_map(game: Any) -> _MapMetadata:
    return _MapMetadata(
        start_location=_point(game.start_location, "start_location"),
        enemy_start_locations=_locations(game.enemy_start_locations, "enemy_start_locations"),
        expansion_locations=tuple(
            sorted(_locations(game.expansion_locations_list, "expansion_locations_list"))
        ),
        map_center=_point(game.game_info.map_center, "game_info.map_center"),
    )


def _path_reply(value: object, where: str) -> float | None:
    """A pathing reply: None or a non-positive distance is SC2's "no path"; anything
    else must be a finite number (a malformed reply raises ValueError)."""
    if value is None:
        return None
    distance = _number(value, where)
    return distance if distance > 0 else None


def _by_tag(found: Mapping[int, tuple[Entity, Any]]) -> tuple[Entity, ...]:
    return tuple(found[tag][0] for tag in sorted(found))


def _vision(port: GamePort) -> Callable[[Point], bool]:
    """``port.is_visible`` asked at most once per position in one game step (vision
    cannot change within a step), so both enemy memories share each answer."""
    answers: dict[Point, bool] = {}

    def visible(point: Point) -> bool:
        answer = answers.get(point)
        if answer is None:
            answer = answers[point] = _flag(port.is_visible(point), "is_visible reply")
        return answer

    return visible


def _reads_game_state[**P, R](read: Callable[P, R]) -> Callable[P, R]:
    """Make :class:`ValueError` the only exception a game-state reader raises.

    Malformed values raise ValueError naming the field; any other exception while
    reading the game (a missing attribute, a failing property) is converted,
    chained with ``from`` so the cause stays inspectable.
    """

    @functools.wraps(read)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return read(*args, **kwargs)
        except ValueError:
            raise
        except Exception as exc:
            detail = safe_exception_text(exc)
            raise ValueError(
                f"unreadable game state ({safe_repr(type(exc).__name__)}: {detail})"
            ) from exc

    return guarded


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


class Sc2Adapter:
    """Normalize visible SC2 state and issue graph-selected commands for one match."""

    def __init__(self) -> None:
        self._map: _MapMetadata | None = None
        self._remembered: dict[int, Entity] = {}
        # Enemy tag -> the latest sighting (plan J2 Step 215; see the module docstring).
        self._sightings: dict[int, EnemySighting] = {}
        # Raw game objects of the latest observation, by tag. Commands may only name
        # these: own actors, visible mineral fields, visible enemies.
        self._actors: dict[int, Any] = {}
        self._minerals: dict[int, Any] = {}
        self._enemies: dict[int, Any] = {}
        # Visible vespene geysers (gas build targets) and own gas buildings with their
        # entities (gather targets), by tag.
        self._geysers: dict[int, Any] = {}
        self._gas: dict[int, tuple[Entity, Any]] = {}
        # Actor tag -> its last command SC2 accepted, until the runtime moves on.
        self._awaiting: dict[int, CommandRecord] = {}
        self._records: deque[CommandRecord] = deque(maxlen=MAX_COMMAND_RECORDS)
        # Expansion location -> SC2's path distance from the start location (None: no
        # path), static for the map: each asked at most once, at most the cap.
        self._paths: dict[Point, float | None] = {}
        # Those locations -> the latest game time they were in own sight (None: never).
        self._seen: dict[Point, float | None] = {}
        self._game_loop = 0
        self._accepted = 0
        self._rejected = 0

    @property
    def records(self) -> tuple[CommandRecord, ...]:
        """The most recent :data:`MAX_COMMAND_RECORDS` command records, oldest first."""
        return tuple(self._records)

    @property
    def sightings(self) -> tuple[EnemySighting, ...]:
        """The enemy sighting memory after the latest observe/remember, by tag."""
        return tuple(self._sightings[tag] for tag in sorted(self._sightings))

    @property
    def accepted_count(self) -> int:
        return self._accepted

    @property
    def paths(self) -> tuple[PathFact, ...]:
        """The cached path answers, by location (what every Observation carries)."""
        return tuple(PathFact(point, self._paths[point]) for point in sorted(self._paths))

    @property
    def rejected_count(self) -> int:
        return self._rejected

    # -- observation -----------------------------------------------------------

    @_reads_game_state
    def game_seconds(self, game: Any) -> float:
        """Game time of ``game``: ``state.game_loop`` / :data:`GAME_LOOPS_PER_SECOND`.

        Raises :class:`ValueError` (only) for an unreadable or malformed clock.
        """
        return _count(game.state.game_loop, "state.game_loop") / GAME_LOOPS_PER_SECOND

    @_reads_game_state
    def remember(self, game: Any, port: GamePort) -> None:
        """Update only the enemy memories from ``game`` (between policy ticks).

        Same sighting and revalidation rules as :meth:`observe`, so memory does not
        depend on which steps the runtime ticks on. Raises :class:`ValueError`
        (only) for malformed state; memory changes only on success.
        """
        now = _count(game.state.game_loop, "state.game_loop") / GAME_LOOPS_PER_SECOND
        enemies: dict[int, tuple[Entity, Any]] = {}
        _visible(game.enemy_units, "enemy_units", enemies)
        _visible(game.enemy_structures, "enemy_structures", enemies)
        visible = _vision(port)
        remembered = self._remember(enemies, visible)  # keeps structures only, as observe()
        sightings = self._sight(enemies, now, visible)
        seen, _ = self._look(visible, now)
        self._remembered = remembered
        self._sightings = sightings
        self._seen = seen

    @_reads_game_state
    def observe(self, game: Any, port: GamePort) -> Observation:
        """Normalize ``game`` (a burnysc2 ``BotAI``) into an Observation.

        Raises :class:`ValueError` -- and only ValueError -- for malformed state;
        an unexpected error while reading it is converted, chained with ``from``.
        Adapter state (memory, command lookups) changes only on success.
        """
        game_loop = _count(game.state.game_loop, "state.game_loop")
        map_data = self._map if self._map is not None else _read_map(game)
        actors: dict[int, Any] = {}
        own_units = _own(game.units, "units", structures=False, actors=actors)
        own_structures = _own(game.structures, "structures", structures=True, actors=actors)
        enemies: dict[int, tuple[Entity, Any]] = {}
        _visible(game.enemy_units, "enemy_units", enemies)
        _visible(game.enemy_structures, "enemy_structures", enemies)
        minerals: dict[int, tuple[Entity, Any]] = {}
        _visible(game.mineral_field, "mineral_field", minerals)
        geysers: dict[int, tuple[Entity, Any]] = {}
        _visible(game.vespene_geyser, "vespene_geyser", geysers, geyser=True)
        visible = _vision(port)
        remembered = self._remember(enemies, visible)
        game_seconds = game_loop / GAME_LOOPS_PER_SECOND
        sightings = self._sight(enemies, game_seconds, visible)
        seen, vision = self._look(visible, game_seconds)
        observation = Observation(
            game_loop=game_loop,
            game_seconds=game_seconds,
            minerals=_count(game.minerals, "minerals"),
            supply_used=_count(game.supply_used, "supply_used"),
            supply_cap=_count(game.supply_cap, "supply_cap"),
            own_units=tuple(own_units),
            own_structures=tuple(own_structures),
            visible_enemies=_by_tag(enemies),
            remembered_enemy_structures=tuple(
                remembered[tag] for tag in sorted(remembered) if tag not in enemies
            ),
            start_location=map_data.start_location,
            enemy_start_locations=map_data.enemy_start_locations,
            expansion_locations=map_data.expansion_locations,
            map_center=map_data.map_center,
            mineral_fields=_by_tag(minerals),
            enemy_sightings=tuple(sightings[tag] for tag in sorted(sightings)),
            vespene=_count(game.vespene, "vespene"),
            vespene_geysers=_by_tag(geysers),
            expansion_paths=self.paths,
            location_vision=vision,
        )
        self._map = map_data
        self._remembered = remembered
        self._sightings = sightings
        self._seen = seen
        self._actors = actors
        self._minerals = {tag: raw for tag, (_, raw) in minerals.items()}
        self._enemies = {tag: raw for tag, (_, raw) in enemies.items()}
        self._geysers = {tag: raw for tag, (_, raw) in geysers.items()}
        self._gas = {
            s.tag: (s, actors[s.tag]) for s in own_structures if s.type_name in GAS_STRUCTURE_TYPES
        }
        self._game_loop = game_loop
        return observation

    def _look(
        self, visible: Callable[[Point], bool], now: float
    ) -> tuple[dict[Point, float | None], tuple[LocationVision, ...]]:
        """The watched locations' vision after this step: each queried location is
        looked at (the step's shared ``visible`` answers) and its last-seen time
        refreshed while in sight. O(watched), at most the path-query cap."""
        seen: dict[Point, float | None] = {}
        records: list[LocationVision] = []
        for point in sorted(self._seen):
            in_sight = visible(point)
            last = now if in_sight else self._seen[point]
            seen[point] = last
            records.append(LocationVision(point, in_sight, last))
        return seen, tuple(records)

    def _remember(
        self, enemies: Mapping[int, tuple[Entity, Any]], visible: Callable[[Point], bool]
    ) -> dict[int, Entity]:
        """Memory after this sighting: refreshed by vision, dropped where vision disproves it."""
        remembered = dict(self._remembered)
        for tag, (entity, _) in enemies.items():
            if entity.is_structure and (
                tag in remembered or len(remembered) < MAX_OBSERVED_ENTITIES
            ):
                remembered[tag] = entity
        for tag, entity in list(remembered.items()):
            if tag not in enemies and visible(entity.position):
                del remembered[tag]  # its spot is in sight and it is not there
        return remembered

    def _sight(
        self,
        enemies: Mapping[int, tuple[Entity, Any]],
        now: float,
        visible: Callable[[Point], bool],
    ) -> dict[int, EnemySighting]:
        """Sighting memory after this game step (see the module docstring).

        ``enemies`` are this step's *visible* enemies only (``_visible`` drops every
        entity SC2 does not report as visible), so nothing hidden can enter or
        refresh the memory. O(memory + visible); a remembered structure's position
        is checked through the step's shared ``visible`` answers (:func:`_vision`).
        Bounded by :data:`~jev.contracts.MAX_ENEMY_SIGHTINGS`.
        """
        sightings: dict[int, EnemySighting] = {}
        for tag, sighting in self._sightings.items():
            if tag in enemies:
                continue  # seen again: refreshed below
            entity = sighting.entity
            if entity.is_structure:
                if visible(entity.position):
                    continue  # its spot is in sight and it is not there
            elif now - sighting.last_seen_game_seconds >= SIGHTING_UNIT_TTL_SECONDS:
                continue  # an unseen unit's sighting has aged out
            sightings[tag] = replace(sighting, visible=False) if sighting.visible else sighting
        for tag, (entity, _) in enemies.items():
            sightings[tag] = EnemySighting(entity, now, True)
        if len(sightings) > MAX_ENEMY_SIGHTINGS:  # evict the oldest (visible ones are newest)
            newest = sorted(
                sightings.values(), key=lambda s: (-s.last_seen_game_seconds, s.entity.tag)
            )[:MAX_ENEMY_SIGHTINGS]
            sightings = {s.entity.tag: s for s in newest}
        return sightings

    async def query_paths(self, port: GamePort, points: Sequence[Point]) -> int:
        """Ask SC2 for the path distance from the start location to each requested
        point not yet answered (plan D4). Only the map's expansion locations are
        asked, at most :data:`~jev.operations.MAX_EXPANSION_CANDIDATES` per match in
        all; nothing before the first observation (no map yet). Answers are cached
        and carried by every later Observation. Returns how many were asked; a
        malformed reply raises :class:`ValueError`, a port failure propagates.
        """
        map_data = self._map
        if map_data is None:
            return 0
        allowed = set(map_data.expansion_locations)
        asked = 0
        for raw in points:
            point = _command_point(raw)
            if point is None or point not in allowed or point in self._paths:
                continue
            if len(self._paths) >= MAX_EXPANSION_CANDIDATES:
                break
            reply = await port.pathing_distance(map_data.start_location, point)
            self._paths[point] = _path_reply(reply, "pathing reply")
            self._seen.setdefault(point, None)  # watched from now on (location_vision)
            asked += 1
        return asked

    # -- commands --------------------------------------------------------------

    def collect_rejections(self, port: GamePort, runtime: JevRuntime) -> int:
        """Forward SC2 action errors for accepted commands to the runtime (D4).

        An error is forwarded only when its actor and ability match the actor's
        last accepted command and that command still awaits acknowledgement;
        anything else (an error left over from an earlier command of the same unit,
        one for a command the observation already confirmed, a repeated report) is
        dropped. Returns the number forwarded. Raises :class:`ValueError` for a
        malformed port report.
        """
        errors = _items(port.action_errors(), "action errors", MAX_ACTION_ERRORS)
        forwarded = 0
        for index, item in enumerate(errors):
            if not (
                isinstance(item, tuple)
                and len(item) == 3
                and isinstance(item[1], str)
                and isinstance(item[2], str)
            ):
                raise ValueError(
                    f"action errors[{index}] must be (tag, ability, reason), got {safe_repr(item)}"
                )
            tag = _tag(item[0], f"action errors[{index}] tag")
            record = self._awaiting.get(tag)
            if (
                record is None
                or record.ability != item[1]
                or runtime.task_status(record.task_id) != "issued"
            ):
                continue
            del self._awaiting[tag]
            reason = f"SC2 reported {item[2]} after accepting the command"
            runtime.mark_command_rejected(record.task_id, reason)
            self._log(replace(record, outcome="rejected", reason=safe_repr(reason)))
            forwarded += 1
        self._awaiting = {
            tag: record
            for tag, record in self._awaiting.items()
            if runtime.task_status(record.task_id) == "issued"
        }
        return forwarded

    async def issue(
        self, commands: Sequence[CommandSpec], port: GamePort, runtime: JevRuntime
    ) -> tuple[CommandRecord, ...]:
        """Issue each command through ``port`` (see the module docstring for the rules).

        Returns one record per command. Raises :class:`ValueError` for a value
        that is not a CommandSpec or a malformed port reply; an exception raised by
        the port itself propagates (an SC2 connection failure ends the match).
        """
        if not isinstance(commands, tuple | list):
            raise ValueError(
                f"commands must be a sequence of CommandSpec, got {safe_repr(commands)}"
            )
        records: list[CommandRecord] = []
        for command in commands:
            if not isinstance(command, CommandSpec):
                raise ValueError(
                    f"commands must hold CommandSpec records, got {safe_repr(command)}"
                )
            records.append(await self._issue_one(command, port, runtime))
        return tuple(records)

    async def _issue_one(
        self, command: CommandSpec, port: GamePort, runtime: JevRuntime
    ) -> CommandRecord:
        node_id, task_id = command.node_id, command.task_id
        if not (
            isinstance(node_id, str)
            and isinstance(task_id, str)
            and runtime.policy.has_node(node_id)
            and runtime.task_status(task_id) == "issued"
        ):
            # Not the runtime's command: nothing to report back, never sent to SC2.
            self._rejected += 1
            return self._log(
                CommandRecord(
                    node_id=node_id if isinstance(node_id, str) else safe_repr(node_id),
                    task_id=task_id if isinstance(task_id, str) else safe_repr(task_id),
                    ability=safe_repr(command.ability),
                    actor_tags=(),
                    target=None,
                    candidates=(),
                    outcome="rejected",
                    reason="refused: not attributed to an issued task of a policy node",
                    game_loop=self._game_loop,
                )
            )
        if len(command.actor_tags) != 1:
            return self._reject(runtime, command, "a command must name exactly one actor")
        actor_tag = command.actor_tags[0]
        actor = self._actors.get(actor_tag) if isinstance(actor_tag, int) else None
        if actor is None:
            return self._reject(
                runtime, command, f"actor {safe_repr(actor_tag)} is not an own unit or structure"
            )
        ability = command.ability
        if not isinstance(ability, str) or ability not in COMMAND_ABILITIES:
            return self._reject(runtime, command, f"{safe_repr(ability)} is not a command ability")
        target = command.target
        sc2_target: object = None
        candidates: tuple[Point, ...] = ()
        placement: Point | None = None
        if ability in _BUILD_ABILITIES:
            sites = [_command_point(site) for site in (target, *command.alternatives)]
            candidates = tuple(site for site in sites if site is not None)
            if len(candidates) != len(sites):
                return self._reject(runtime, command, "a build needs (x, y) candidate sites")
            candidates = candidates[:MAX_PLACEMENT_CANDIDATES]
            verdicts = _items(
                await port.placement_legal(ability, candidates),
                "placement reply",
                MAX_PLACEMENT_CANDIDATES,
            )
            if len(verdicts) != len(candidates):
                raise ValueError(
                    f"placement reply has {len(verdicts)} verdicts for {len(candidates)} sites"
                )
            legal = [_flag(ok, "placement verdict") for ok in verdicts]
            placement = next((site for site, ok in zip(candidates, legal, strict=True) if ok), None)
            if placement is None:
                return self._reject(
                    runtime,
                    command,
                    f"no legal placement among {len(candidates)} candidate(s)",
                    candidates,
                )
            sc2_target = placement
        elif ability in _GAS_BUILD_ABILITIES:
            # SC2 builds a gas structure on the geyser unit: never a point, no placement query.
            if not isinstance(target, int) or command.alternatives:
                return self._reject(
                    runtime, command, f"{ability} targets one vespene geyser by tag, never a point"
                )
            sc2_target = self._geysers.get(target)
            if sc2_target is None:
                return self._reject(runtime, command, f"{ability} target is not a visible geyser")
        elif ability in _TRAIN_ABILITIES:
            if target is not None:
                return self._reject(runtime, command, "a train command takes no target")
        elif ability == GATHER_ABILITY:
            sc2_target = self._minerals.get(target) if isinstance(target, int) else None
            gas = self._gas.get(target) if isinstance(target, int) else None
            if sc2_target is None and gas is not None:
                building, raw = gas
                if not building.is_ready:
                    return self._reject(runtime, command, "gather target gas building unfinished")
                if building.vespene_contents <= 0:
                    return self._reject(runtime, command, "gather target gas building depleted")
                sc2_target = raw
            if sc2_target is None:
                return self._reject(
                    runtime,
                    command,
                    "gather target is not a visible mineral field or an own gas building",
                )
        elif isinstance(target, int):  # only an attack may name a unit target
            sc2_target = self._enemies.get(target) if ability == ATTACK_ABILITY else None
            if sc2_target is None:
                return self._reject(runtime, command, f"{ability} target is not a visible enemy")
        elif isinstance(target, tuple):  # move / attack-move to a point
            sc2_target = _command_point(target)
            if sc2_target is None:
                return self._reject(runtime, command, f"{ability} needs an (x, y) point")
        else:
            return self._reject(runtime, command, f"{ability} needs a point or unit target")
        refusal = await port.issue(ability, actor, sc2_target)
        if refusal is not None:
            text = refusal if isinstance(refusal, str) else safe_repr(refusal)
            return self._reject(runtime, command, f"SC2 refused the command: {text}", candidates)
        if not runtime.mark_command_accepted(task_id, placement=placement):
            return self._log(
                CommandRecord(
                    node_id=node_id,
                    task_id=task_id,
                    ability=ability,
                    actor_tags=(actor_tag,),
                    target=placement if placement is not None else target,
                    candidates=candidates,
                    outcome="ignored",
                    reason="accepted by SC2, but the runtime did not apply the acceptance",
                    game_loop=self._game_loop,
                )
            )
        self._accepted += 1
        record = CommandRecord(
            node_id=node_id,
            task_id=task_id,
            ability=ability,
            actor_tags=(actor_tag,),
            target=placement if placement is not None else target,
            candidates=candidates,
            outcome="accepted",
            reason="accepted by SC2; awaiting observation",
            game_loop=self._game_loop,
        )
        self._awaiting[actor_tag] = record
        return self._log(record)

    def _reject(
        self,
        runtime: JevRuntime,
        command: CommandSpec,
        reason: str,
        candidates: tuple[Point, ...] = (),
    ) -> CommandRecord:
        runtime.mark_command_rejected(command.task_id, reason)
        self._rejected += 1
        return self._log(
            CommandRecord(
                node_id=command.node_id,
                task_id=command.task_id,
                ability=command.ability,
                actor_tags=command.actor_tags,
                target=command.target,
                candidates=candidates,
                outcome="rejected",
                reason=safe_repr(reason),
                game_loop=self._game_loop,
            )
        )

    def _log(self, record: CommandRecord) -> CommandRecord:
        self._records.append(record)
        return record


# ---------------------------------------------------------------------------
# The production port
# ---------------------------------------------------------------------------


class BotAIPort:
    """:class:`GamePort` over a live burnysc2 ``BotAI`` (burnysc2 imported lazily).

    Commands are explicit ``UnitCommand`` records sent one per request with
    ``return_successes``, so each command gets its own immediate SC2 verdict.
    """

    def __init__(self, bot: BotAI) -> None:
        self._bot = bot

    def is_visible(self, point: Point) -> bool:
        from sc2.position import Point2

        return bool(self._bot.is_visible(Point2(point)))

    async def placement_legal(self, ability: str, sites: Sequence[Point]) -> list[bool]:
        from sc2.ids.ability_id import AbilityId
        from sc2.position import Point2

        verdicts = await self._bot.can_place(AbilityId[ability], [Point2(site) for site in sites])
        return [bool(ok) for ok in verdicts]

    async def issue(self, ability: str, actor: object, target: object) -> str | None:
        from sc2.data import ActionResult
        from sc2.ids.ability_id import AbilityId
        from sc2.position import Point2
        from sc2.unit import Unit
        from sc2.unit_command import UnitCommand

        if not isinstance(actor, Unit):
            return "actor is not an SC2 unit"
        sc2_target: Unit | Point2 | None
        if target is None or isinstance(target, Unit):
            sc2_target = target
        elif isinstance(target, tuple):
            sc2_target = Point2(target)
        else:
            return "target is not an SC2 unit or point"
        command = UnitCommand(
            AbilityId[_SC2_ISSUE_ABILITY.get(ability, ability)], actor, sc2_target
        )
        results = await self._bot.client.actions([command], return_successes=True)
        if not results:
            return "no action result (the game is not running)"
        return None if results[0] == ActionResult.Success else results[0].name

    def action_errors(self) -> list[tuple[int, str, str]]:
        from sc2.data import ActionResult
        from sc2.ids.ability_id import AbilityId

        errors: list[tuple[int, str, str]] = []
        for error in self._bot.state.action_errors:
            generic = error.generic_id  # e.g. MOVE_MOVE is reported as MOVE
            # burnysc2 maps an id it does not know to NULL_NULL; keep the raw id instead.
            known = generic != AbilityId.NULL_NULL
            ability = generic.name if known else f"ability {safe_repr(error.ability_id)}"
            try:
                name = ActionResult(error.result).name
            except ValueError:
                name = f"action result {safe_repr(error.result)}"
            errors.append((error.unit_tag, ability, name))
        return errors

    async def leave(self) -> None:
        await self._bot.client.leave()

    async def pathing_distance(self, start: Point, end: Point) -> float | None:
        """burnysc2's ``Client.query_pathing`` from the start point (it reports SC2's
        "no path" as None, which ``BotAI.get_next_expansion`` relies on too)."""
        from sc2.position import Point2

        reply = await self._bot.client.query_pathing(Point2(start), Point2(end))
        return None if reply is None else float(reply)
