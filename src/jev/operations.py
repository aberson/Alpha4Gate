"""The one allowlisted operation registry for Jev policies.

Every operation a policy node may name is declared in :data:`OPERATIONS` with its
typed arguments and (for selections) its permitted binding output. The validator
(:mod:`jev.policy`) checks nodes against these declarations; the interpreter
(:mod:`jev.runtime`) calls the implementations below.

Three categories:

* **Predicates** (``condition`` and ``wait`` nodes): count comparisons, resources
  and supply, threat distance, task state, the ``attack_launched`` latch and game
  time. Powered/idle/ready checks are filter keys on the counted collection.
* **Selections** (``select`` nodes): filter/sort/limit over an observation
  collection, map locations, a derived point, or bounded placement candidates.
  The result is bound under the node's ``bind`` name in the root-local context.
* **Actions** (``action`` nodes): explicit gather/build/train/move/attack command
  intents plus the ``set_latch`` memory write. The runtime turns intents into
  deduplicated tasks and :class:`~jev.contracts.CommandSpec` records.

There are deliberately no strategy helpers (``manage_economy``, ``rush``,
``expand_now``, ``distribute_workers`` ...): the node arguments carry every
criterion, tie-break and limit, so the graph is the policy.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol, get_args

from jev.contracts import (
    ENTITY_COLLECTIONS,
    LATCHES,
    LOCATION_SOURCES,
    MAX_ABS_NUMBER,
    NAME_RE,
    NODE_STATUSES,
    POINT_KEYWORDS,
    TASK_STATUSES,
    Entity,
    EntityCollection,
    FrozenJsonDict,
    JsonValue,
    Latch,
    LocationSource,
    NodeKind,
    NodeStatus,
    Observation,
    Point,
    PointKeyword,
    Target,
    freeze_json,
    full_match,
    is_bounded_number,
    safe_repr,
)

__all__ = [
    "ATTACK_ABILITY",
    "Ack",
    "ActionOp",
    "ActionPlan",
    "ArgIssue",
    "ArgSpec",
    "ArgType",
    "Args",
    "BINDING_KINDS",
    "BOOL_FILTER_ATTRIBUTES",
    "BUILD_ABILITY",
    "Binding",
    "BindingKind",
    "COMPARATORS",
    "Comparator",
    "CompiledFilter",
    "DEFAULT_FOOTPRINT",
    "ENTITY_SORT_METRICS",
    "FILTER_KEYS",
    "FOOTPRINT",
    "GATHER_ABILITY",
    "HARVEST_ABILITIES",
    "Intent",
    "KNOWN_ORDER_ABILITIES",
    "LOCATION_SORT_KEYS",
    "Lifecycle",
    "MAX_ANY_OF",
    "MAX_FILTER_DEPTH",
    "MAX_FILTER_NAMES",
    "MAX_ISSUES_PER_FIELD",
    "MAX_LOCATIONS",
    "MAX_PLACEMENT_CANDIDATES",
    "MAX_PLACEMENT_RADIUS",
    "MAX_QUERY_DISTANCE",
    "MAX_SELECTION",
    "MINERAL_COST",
    "MOVE_ABILITY",
    "OPERATIONS",
    "OP_BUILD",
    "OP_GATHER",
    "OWN_TYPES",
    "OpOutcome",
    "OperationSpec",
    "PARAM_ARRIVE_WITHIN",
    "PARAM_STRUCTURE",
    "PARAM_UNIT",
    "PARAM_UNIT_COUNT",
    "POINT_MATCH_TOLERANCE",
    "PRODUCER_TYPE",
    "PYLON_POWER_RADIUS",
    "PredicateOp",
    "Progress",
    "RESOURCE_NAMES",
    "RESOURCE_READERS",
    "RETURN_ABILITY",
    "RuntimeView",
    "SORT_KEYS",
    "SORT_ORDERS",
    "SORT_SPEC_KEYS",
    "SUPPLY_COST",
    "SelectOp",
    "Selection",
    "SortKey",
    "SortOrder",
    "TRAIN_ABILITY",
    "TaskSubject",
    "WORKER_TYPE",
    "binding_inputs",
    "binding_output",
    "compare",
    "compile_filter",
    "decode_arrive_within",
    "distance",
    "entity_matches",
    "format_point",
    "node_outcomes",
    "operation_node_kinds",
    "parameter_references",
    "placement_candidates",
    "resolve_args",
    "resolve_point",
    "validate_operation_args",
]

# ---------------------------------------------------------------------------
# Game data: the single source of truth for Jev v1's allowed unit/structure set.
# ---------------------------------------------------------------------------

GATHER_ABILITY: Final = "HARVEST_GATHER"
RETURN_ABILITY: Final = "HARVEST_RETURN"
MOVE_ABILITY: Final = "MOVE"
ATTACK_ABILITY: Final = "ATTACK"
HARVEST_ABILITIES: Final = frozenset({GATHER_ABILITY, RETURN_ABILITY})

#: Structures Jev v1 may build -> generic SC2 build ability. No Nexus/Assimilator:
#: one base, no gas (D3) is enforced by the allowlist itself.
BUILD_ABILITY: Final[Mapping[str, str]] = {
    "Pylon": "PROTOSSBUILD_PYLON",
    "Gateway": "PROTOSSBUILD_GATEWAY",
}
#: Units Jev v1 may train -> generic SC2 train ability.
TRAIN_ABILITY: Final[Mapping[str, str]] = {
    "Probe": "NEXUSTRAIN_PROBE",
    "Zealot": "GATEWAYTRAIN_ZEALOT",
}
#: Unit -> structure type that trains it.
PRODUCER_TYPE: Final[Mapping[str, str]] = {"Probe": "Nexus", "Zealot": "Gateway"}
#: The unit type that mines and constructs.
WORKER_TYPE: Final = "Probe"
MINERAL_COST: Final[Mapping[str, int]] = {"Probe": 50, "Zealot": 100, "Pylon": 100, "Gateway": 150}
SUPPLY_COST: Final[Mapping[str, int]] = {"Probe": 1, "Zealot": 2}
#: Square footprint edge length (cells) for placement overlap checks.
FOOTPRINT: Final[Mapping[str, int]] = {"Nexus": 5, "Pylon": 2, "Gateway": 3}
DEFAULT_FOOTPRINT: Final = 2
PYLON_POWER_RADIUS: Final = 6.5
#: Own entity types a filter on ``own_units`` / ``own_structures`` may name: derived
#: from the tables above (buildable + trainable + producers + footprinted structures).
OWN_TYPES: Final = (
    frozenset(BUILD_ABILITY)
    | frozenset(TRAIN_ABILITY)
    | frozenset(PRODUCER_TYPE.values())
    | frozenset(FOOTPRINT)
)
#: Order abilities a filter's ``order_in`` may name.
KNOWN_ORDER_ABILITIES: Final = frozenset(
    {GATHER_ABILITY, RETURN_ABILITY, MOVE_ABILITY, ATTACK_ABILITY}
    | set(BUILD_ABILITY.values())
    | set(TRAIN_ABILITY.values())
)

MAX_PLACEMENT_CANDIDATES: Final = 8
#: Operation names the runtime's reservation views recognize (used by the registry too).
OP_BUILD: Final = "build"
OP_GATHER: Final = "gather"
#: Intent.params keys shared by the operations' lifecycle checks and the runtime.
PARAM_STRUCTURE: Final = "structure"
PARAM_UNIT: Final = "unit"
#: Own units of the trained type when the train intent was planned (D4 "new unit" ack).
PARAM_UNIT_COUNT: Final = "unit_count"
PARAM_ARRIVE_WITHIN: Final = "arrive_within"


def decode_arrive_within(params: Mapping[str, JsonValue]) -> float:
    """THE decode of an intent's arrival radius (game units): absent or ill-typed -> 0.0.

    Shared by movement acknowledgement, movement progress and the runtime's
    engagement check, so all three agree on one value and one fallback.
    """
    value = params.get(PARAM_ARRIVE_WITHIN, 0.0)
    if isinstance(value, int | float) and is_bounded_number(value):
        return float(value)
    return 0.0


MAX_PLACEMENT_RADIUS: Final = 20.0
MAX_SELECTION: Final = 200
#: Most map locations a select_locations node may bind.
MAX_LOCATIONS: Final = 64
#: Largest distance any distance-valued argument may take (game units).
MAX_QUERY_DISTANCE: Final = 200.0
MAX_FILTER_DEPTH: Final = 3
MAX_ANY_OF: Final = 8
#: A list-valued field (unknown keys, name lists, ...) names at most this many bad
#: entries, then one summary issue -- per-site output stays O(1) on hostile input.
MAX_ISSUES_PER_FIELD: Final = 8
#: Most names a filter's types/exclude_types/order_in list may hold.
MAX_FILTER_NAMES: Final = 32
#: An order/structure within this distance of a target point counts as "at" it.
POINT_MATCH_TOLERANCE: Final = 1.0

Comparator = Literal["<", "<=", "==", "!=", ">=", ">"]
COMPARATORS: Final[tuple[Comparator, ...]] = get_args(Comparator)
BindingKind = Literal["units", "points"]
BINDING_KINDS: Final[tuple[BindingKind, ...]] = get_args(BindingKind)
SortOrder = Literal["asc", "desc"]
SORT_ORDERS: Final[tuple[SortOrder, ...]] = get_args(SortOrder)
#: Keys a sort specification object may carry.
SORT_SPEC_KEYS: Final = frozenset({"by", "from", "order"})
SortKey = Literal["distance", "tag", "health", "build_progress"]
#: Entity sort keys -- derived from the Literal, the one source of truth.
SORT_KEYS: Final[tuple[SortKey, ...]] = get_args(SortKey)
#: Map locations only sort by distance.
LOCATION_SORT_KEYS: Final[tuple[SortKey, ...]] = ("distance",)


def compare(actual: float, op: str, expected: float) -> bool:
    if op == "<":
        return actual < expected
    if op == "<=":
        return actual <= expected
    if op == "==":
        return actual == expected
    if op == "!=":
        return actual != expected
    if op == ">=":
        return actual >= expected
    if op == ">":
        return actual > expected
    raise ValueError(f"unknown comparator {safe_repr(op)}")


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def format_point(point: Point) -> str:
    return f"{point[0]:g},{point[1]:g}"


# ---------------------------------------------------------------------------
# Evaluation-time types shared with the runtime
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Binding:
    """A root-local selection result: either entities or points, never both."""

    kind: BindingKind
    entities: tuple[Entity, ...] = ()
    points: tuple[Point, ...] = ()

    def first_point(self) -> Point | None:
        if self.kind == "points":
            return self.points[0] if self.points else None
        return self.entities[0].position if self.entities else None

    def describe(self) -> JsonValue:
        if self.kind == "points":
            return [[p[0], p[1]] for p in self.points]
        return [str(e.tag) for e in self.entities]


class RuntimeView(Protocol):
    """Read-only facts an operation may consult (implemented by the runtime)."""

    @property
    def observation(self) -> Observation: ...

    def binding(self, name: str) -> Binding | None: ...

    def available_minerals(self) -> int:
        """Observed minerals minus in-flight commitments and this tick's holds."""
        ...

    def available_supply(self) -> int:
        """Free supply minus in-flight training commitments and this tick's holds."""
        ...

    def latch_is_set(self, latch: str) -> bool: ...

    def own_unit_tags(self) -> frozenset[int]:
        """Tags of this tick's own units (actors for unit commands)."""
        ...

    def own_structure_tags(self) -> frozenset[int]:
        """Tags of this tick's own structures (producers)."""
        ...

    def node_filter(self) -> CompiledFilter | None:
        """The evaluating node's ``filter`` argument, compiled once at runtime start."""
        ...

    def count_tasks(self, node_id: str, statuses: frozenset[str]) -> int:
        """Active tasks in active states plus whole-run totals for terminal states."""
        ...

    def actor_is_busy(self, tag: int) -> bool:
        """True when an active task holds ``tag`` or this tick already reserved it."""
        ...

    def in_flight_builds(self) -> tuple[tuple[str, Point], ...]:
        """``(structure, site)`` for build tasks whose structure has not appeared yet."""
        ...

    def gather_assignments(self) -> Mapping[int, int]:
        """Mineral-patch tag -> workers assigned by unacknowledged gather tasks."""
        ...


@dataclass(frozen=True)
class OpOutcome:
    """Predicate result: ``ok`` maps to success (condition) or done-waiting (wait)."""

    ok: bool
    reason: str
    facts: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class Selection:
    """Selection result; ``binding`` is None when fewer than ``min_count`` matched."""

    binding: Binding | None
    reason: str
    facts: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class Intent:
    """One per-actor side effect an action wants. The runtime owns dedup/budgets."""

    actor_tag: int
    semantic: str
    ability: str
    target: Target
    alternatives: tuple[Point, ...] = ()
    minerals: int = 0
    supply: int = 0
    satisfied: bool = False
    params: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionPlan:
    intents: tuple[Intent, ...] = ()
    blocked: tuple[tuple[int, str], ...] = ()
    failure_reason: str | None = None
    latch: Latch | None = None
    hold_reservation: bool = False
    preempt: bool = False
    facts: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskSubject:
    """What a lifecycle check needs to know about a task."""

    actor_tag: int
    ability: str
    target: Target
    alternatives: tuple[Point, ...]
    params: Mapping[str, JsonValue]
    tracked_tag: int | None


@dataclass(frozen=True)
class Ack:
    """Observed acknowledgement; may re-point the task at the observed site."""

    tracked_tag: int | None = None
    target: Target = None


@dataclass(frozen=True)
class Progress:
    state: Literal["ongoing", "complete", "lost"]
    reason: str
    metric: float | None = None


@dataclass(frozen=True)
class Lifecycle:
    """Observation-based confirmation rules for one action operation (plan D4)."""

    acknowledge: Callable[[TaskSubject, Observation], Ack | None]
    progress: Callable[[TaskSubject, Observation], Progress]
    completes_on_ack: bool
    deadline_kind: Literal["build", "train"] | None
    tracks_movement: bool
    holds_actor_while_running: bool


type Args = Mapping[str, JsonValue]

# ---------------------------------------------------------------------------
# Argument specifications
# ---------------------------------------------------------------------------

ArgType = Literal[
    "int",
    "number",
    "bool",
    "enum",
    "enum_list",
    "filter",
    "sort",
    "point",
    "entities",
    "points",
    "target",
    "bind",
    "node",
]


@dataclass(frozen=True)
class ArgSpec:
    """Typed argument declaration. ``param`` lets a numeric arg cite a policy parameter."""

    type: ArgType
    required: bool = True
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    default: JsonValue = None
    param: bool = False


@dataclass(frozen=True)
class PredicateOp:
    name: str
    summary: str
    args: Mapping[str, ArgSpec]
    evaluate: Callable[[RuntimeView, Args], OpOutcome]


@dataclass(frozen=True)
class SelectOp:
    name: str
    summary: str
    args: Mapping[str, ArgSpec]
    binds: BindingKind
    evaluate: Callable[[RuntimeView, Args], Selection]


@dataclass(frozen=True)
class ActionOp:
    name: str
    summary: str
    args: Mapping[str, ArgSpec]
    plan: Callable[[RuntimeView, Args], ActionPlan]
    outcomes: frozenset[NodeStatus]
    lifecycle: Lifecycle | None


type OperationSpec = PredicateOp | SelectOp | ActionOp

# ---------------------------------------------------------------------------
# Typed argument access (post-validation, post-resolution)
# ---------------------------------------------------------------------------


def _arg_int(args: Args, key: str) -> int:
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"argument {safe_repr(key)} is not an int: {safe_repr(value)}")
    return value


def _arg_num(args: Args, key: str) -> float:
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"argument {safe_repr(key)} is not a number: {safe_repr(value)}")
    return float(value)


def _arg_str(args: Args, key: str) -> str:
    value = args[key]
    if not isinstance(value, str):
        raise TypeError(f"argument {safe_repr(key)} is not a string: {safe_repr(value)}")
    return value


def _arg_bool(args: Args, key: str) -> bool:
    value = args[key]
    if not isinstance(value, bool):
        raise TypeError(f"argument {safe_repr(key)} is not a bool: {safe_repr(value)}")
    return value


def _arg_obj(args: Args, key: str) -> Mapping[str, JsonValue] | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"argument {safe_repr(key)} is not an object: {safe_repr(value)}")
    return value


def _str_list(value: JsonValue) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"expected a list, got {safe_repr(value)}")
    return tuple(str(item) for item in value)


def _binding_name(ref: str) -> str:
    return ref[1:]


def _binding(view: RuntimeView, ref: str) -> Binding:
    found = view.binding(_binding_name(ref))
    if found is None:
        # Validation proves definite assignment; reaching here is an interpreter bug.
        raise LookupError(f"binding {safe_repr(ref)} is not bound")
    return found


def resolve_point(view: RuntimeView, ref: str) -> Point | None:
    if ref.startswith("$"):
        return _binding(view, ref).first_point()
    if ref in POINT_KEYWORDS:
        keyword: PointKeyword = "start_location" if ref == "start_location" else "map_center"
        return view.observation.point(keyword)
    raise ValueError(f"unknown point reference {safe_repr(ref)}")


def _collection(name: str) -> EntityCollection:
    for candidate in ENTITY_COLLECTIONS:
        if candidate == name:
            return candidate
    raise ValueError(f"unknown entity collection {safe_repr(name)}")


def _location_source(name: str) -> LocationSource:
    for candidate in LOCATION_SOURCES:
        if candidate == name:
            return candidate
    raise ValueError(f"unknown location source {safe_repr(name)}")


# ---------------------------------------------------------------------------
# Filters and sorting
# ---------------------------------------------------------------------------

#: Boolean filter keys and how each reads an entity -- the one table both the
#: validator (allowed keys) and the compiled matcher use.
BOOL_FILTER_ATTRIBUTES: Final[Mapping[str, Callable[[Entity], bool]]] = {
    "ready": lambda e: e.is_ready,
    "idle": lambda e: e.is_idle,
    "powered": lambda e: e.is_powered,
    "flying": lambda e: e.is_flying,
    "structure": lambda e: e.is_structure,
    "under_construction": lambda e: e.build_progress < 1.0,
}
_BOOL_FILTER_KEYS: Final = tuple(BOOL_FILTER_ATTRIBUTES)
FILTER_KEYS: Final = frozenset(
    {"types", "exclude_types", "order_in", "within", "any_of", *_BOOL_FILTER_KEYS}
)


@dataclass(frozen=True)
class CompiledFilter:
    """A validated filter precompiled ONCE per node (name lists become frozensets)."""

    types: frozenset[str] | None = None
    exclude_types: frozenset[str] | None = None
    flags: tuple[tuple[str, bool], ...] = ()
    order_in: frozenset[str] | None = None
    within_point: str | None = None
    within_distance: float = 0.0
    any_of: tuple[CompiledFilter, ...] = ()


def compile_filter(raw: JsonValue) -> CompiledFilter | None:
    """Compile a resolved (validated, parameter-substituted) filter.

    ``None`` (no filter argument) and ``{}`` (an empty filter object) both mean
    "match everything": an absent filter compiles to ``None`` and an empty object
    to ``CompiledFilter()``, which has no constraints. An empty object inside
    ``any_of`` is therefore an always-true branch, exactly as the AND/OR semantics
    of the JSON form say, never silently dropped.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TypeError(f"filter is not an object: {safe_repr(raw)}")

    def names(key: str) -> frozenset[str] | None:
        value = raw.get(key)
        return frozenset(_str_list(value)) if value is not None else None

    within = raw.get("within")
    point: str | None = None
    radius = 0.0
    if isinstance(within, dict):
        point = str(within["point"])
        distance_value = within["distance"]
        if isinstance(distance_value, bool) or not isinstance(distance_value, int | float):
            raise TypeError(f"filter distance is not a number: {safe_repr(distance_value)}")
        radius = float(distance_value)
    nested = raw.get("any_of")
    return CompiledFilter(
        types=names("types"),
        exclude_types=names("exclude_types"),
        flags=tuple(
            (key, wanted) for key in _BOOL_FILTER_KEYS if isinstance(wanted := raw.get(key), bool)
        ),
        order_in=names("order_in"),
        within_point=point,
        within_distance=radius,
        any_of=tuple(
            compile_filter(item) or CompiledFilter()
            for item in (nested if isinstance(nested, list) else [])
        ),
    )


def entity_matches(entity: Entity, filt: CompiledFilter | None, view: RuntimeView) -> bool:
    """AND of every present filter key; ``any_of`` is an OR of nested filters."""
    if filt is None:
        return True
    if filt.types is not None and entity.type_name not in filt.types:
        return False
    if filt.exclude_types is not None and entity.type_name in filt.exclude_types:
        return False
    for key, wanted in filt.flags:
        if BOOL_FILTER_ATTRIBUTES[key](entity) != wanted:
            return False
    if filt.order_in is not None and entity.current_ability not in filt.order_in:
        return False
    if filt.within_point is not None:
        origin = resolve_point(view, filt.within_point)
        if origin is None or distance(entity.position, origin) > filt.within_distance:
            return False
    if filt.any_of and not any(entity_matches(entity, item, view) for item in filt.any_of):
        return False
    return True


#: One metric per entity sort key (tags compare exactly as ints). Keys == SORT_KEYS.
ENTITY_SORT_METRICS: Final[Mapping[str, Callable[[Entity, Point | None], float]]] = {
    "distance": lambda e, origin: distance(e.position, origin) if origin is not None else 0.0,
    "tag": lambda e, origin: e.tag,
    "health": lambda e, origin: e.health,
    "build_progress": lambda e, origin: e.build_progress,
}


def _sort_entities(
    entities: Sequence[Entity], sort: Mapping[str, JsonValue] | None, view: RuntimeView
) -> list[Entity]:
    """Sort by the authored criterion, always tie-breaking by ascending tag."""
    by = str(sort.get("by", "tag")) if sort else "tag"
    metric = ENTITY_SORT_METRICS.get(by)
    if metric is None:
        raise ValueError(f"unknown entity sort key {safe_repr(by)} (expected one of {SORT_KEYS})")
    origin: Point | None = None
    if by == "distance" and sort is not None:
        origin = resolve_point(view, str(sort["from"]))
    sign = -1 if sort and sort.get("order") == "desc" else 1
    return sorted(entities, key=lambda e: (sign * metric(e, origin), e.tag))


def _sort_points(
    points: Sequence[Point], sort: Mapping[str, JsonValue] | None, view: RuntimeView
) -> list[Point]:
    """Points sort by distance from ``from`` (if given), then by x, then y."""
    if not sort:
        return sorted(points)
    by = sort.get("by")
    if by not in LOCATION_SORT_KEYS:
        raise ValueError(
            f"unknown location sort key {safe_repr(by)} (expected {LOCATION_SORT_KEYS})"
        )
    origin = resolve_point(view, str(sort["from"]))
    sign = -1.0 if sort.get("order") == "desc" else 1.0

    def key(point: Point) -> tuple[float, float, float]:
        d = distance(point, origin) if origin is not None else 0.0
        return (sign * d, point[0], point[1])

    return sorted(points, key=key)


# ---------------------------------------------------------------------------
# Predicate implementations
# ---------------------------------------------------------------------------


def _count_compare(view: RuntimeView, args: Args) -> OpOutcome:
    collection = _collection(_arg_str(args, "collection"))
    filt = view.node_filter()
    op = _arg_str(args, "op")
    expected = _arg_int(args, "value")
    count = sum(1 for e in view.observation.collection(collection) if entity_matches(e, filt, view))
    ok = compare(count, op, expected)
    verdict = "holds" if ok else "does not hold"
    return OpOutcome(
        ok,
        f"{collection} count {count} {op} {expected} {verdict}",
        {"count": count, "op": op, "value": expected},
    )


#: Resource names a ``resource_compare`` may read, and how -- one table for the
#: validator's choices and the evaluator. Minerals/free supply are post-reservation.
RESOURCE_READERS: Final[Mapping[str, Callable[[RuntimeView], int]]] = {
    "minerals": lambda view: view.available_minerals(),
    "supply_free": lambda view: view.available_supply(),
    "supply_used": lambda view: view.observation.supply_used,
    "supply_cap": lambda view: view.observation.supply_cap,
}
RESOURCE_NAMES: Final = tuple(RESOURCE_READERS)


def _resource_compare(view: RuntimeView, args: Args) -> OpOutcome:
    resource = _arg_str(args, "resource")
    op = _arg_str(args, "op")
    expected = _arg_int(args, "value")
    actual = RESOURCE_READERS[resource](view)
    ok = compare(actual, op, expected)
    verdict = "holds" if ok else "does not hold"
    return OpOutcome(
        ok,
        f"{resource} {actual} {op} {expected} {verdict}",
        {"resource": resource, "actual": actual, "op": op, "value": expected},
    )


def _threat_within(view: RuntimeView, args: Args) -> OpOutcome:
    origin = resolve_point(view, _arg_str(args, "point"))
    radius = _arg_num(args, "distance")
    include_flying = _arg_bool(args, "include_flying")
    include_structures = _arg_bool(args, "include_structures")
    if origin is None:
        return OpOutcome(False, "threat origin unresolved", {})
    threats = sorted(
        (
            (distance(e.position, origin), e.tag)
            for e in view.observation.visible_enemies
            if (include_flying or not e.is_flying)
            and (include_structures or not e.is_structure)
            and distance(e.position, origin) <= radius
        ),
    )
    if not threats:
        return OpOutcome(False, f"no visible threat within {radius:g}", {"count": 0})
    nearest_distance, nearest_tag = threats[0]
    return OpOutcome(
        True,
        f"{len(threats)} threat(s) within {radius:g}; "
        f"nearest {nearest_tag} at {nearest_distance:.1f}",
        {
            "count": len(threats),
            "nearest_tag": str(nearest_tag),
            "nearest_distance": round(nearest_distance, 2),
        },
    )


def _task_count_compare(view: RuntimeView, args: Args) -> OpOutcome:
    node_id = _arg_str(args, "node")
    statuses = frozenset(_str_list(args["statuses"]))
    op = _arg_str(args, "op")
    expected = _arg_int(args, "value")
    count = view.count_tasks(node_id, statuses)
    ok = compare(count, op, expected)
    verdict = "holds" if ok else "does not hold"
    return OpOutcome(
        ok,
        f"tasks of {node_id} in {sorted(statuses)}: {count} {op} {expected} {verdict}",
        {"node": node_id, "count": count, "op": op, "value": expected},
    )


def _latch_is_set(view: RuntimeView, args: Args) -> OpOutcome:
    latch = _arg_str(args, "latch")
    ok = view.latch_is_set(latch)
    return OpOutcome(ok, f"latch {latch} is {'set' if ok else 'clear'}", {"latch": latch})


def _game_time_compare(view: RuntimeView, args: Args) -> OpOutcome:
    op = _arg_str(args, "op")
    seconds = _arg_num(args, "seconds")
    now = view.observation.game_seconds
    ok = compare(now, op, seconds)
    verdict = "holds" if ok else "does not hold"
    return OpOutcome(
        ok,
        f"game time {now:.2f} {op} {seconds:g} {verdict}",
        {"game_seconds": now, "op": op, "seconds": seconds},
    )


# ---------------------------------------------------------------------------
# Selection implementations
# ---------------------------------------------------------------------------


def _select_entities(view: RuntimeView, args: Args) -> Selection:
    collection = _collection(_arg_str(args, "collection"))
    filt = view.node_filter()
    limit = _arg_int(args, "limit")
    min_count = _arg_int(args, "min_count")
    exclude_busy = _arg_bool(args, "exclude_busy")
    candidates = [
        e for e in view.observation.collection(collection) if entity_matches(e, filt, view)
    ]
    if exclude_busy:
        candidates = [e for e in candidates if not view.actor_is_busy(e.tag)]
    chosen = _sort_entities(candidates, _arg_obj(args, "sort"), view)[:limit]
    facts: dict[str, JsonValue] = {"matched": len(candidates), "selected": len(chosen)}
    if len(chosen) < min_count:
        return Selection(None, f"{len(chosen)} {collection} selected, need {min_count}", facts)
    binding = Binding("units", entities=tuple(chosen))
    facts["tags"] = binding.describe()
    return Selection(binding, f"selected {len(chosen)} from {collection}", facts)


def _select_locations(view: RuntimeView, args: Args) -> Selection:
    source = _location_source(_arg_str(args, "source"))
    limit = _arg_int(args, "limit")
    min_count = _arg_int(args, "min_count")
    points = _sort_points(view.observation.locations(source), _arg_obj(args, "sort"), view)[:limit]
    facts: dict[str, JsonValue] = {"selected": len(points)}
    if len(points) < min_count:
        return Selection(None, f"{len(points)} {source} available, need {min_count}", facts)
    binding = Binding("points", points=tuple(points))
    facts["points"] = binding.describe()
    return Selection(binding, f"selected {len(points)} from {source}", facts)


def _select_point(view: RuntimeView, args: Args) -> Selection:
    origin = resolve_point(view, _arg_str(args, "from"))
    toward = resolve_point(view, _arg_str(args, "toward"))
    step = _arg_num(args, "distance")
    if origin is None or toward is None:
        return Selection(None, "point reference unresolved", {})
    span = distance(origin, toward)
    if span == 0.0:
        point = origin
    else:
        ratio = min(step, span) / span
        point = (
            round(origin[0] + (toward[0] - origin[0]) * ratio, 2),
            round(origin[1] + (toward[1] - origin[1]) * ratio, 2),
        )
    binding = Binding("points", points=(point,))
    return Selection(binding, f"point {format_point(point)}", {"points": binding.describe()})


def _overlaps(a: Point, size_a: float, b: Point, size_b: float) -> bool:
    reach = (size_a + size_b) / 2.0
    return abs(a[0] - b[0]) < reach and abs(a[1] - b[1]) < reach


def placement_candidates(
    view: RuntimeView,
    *,
    structure: str,
    near: Point,
    radius_min: float,
    radius_max: float,
    require_power: bool,
    resource_clearance: float,
    limit: int,
) -> list[Point]:
    """Bounded, deterministic placement candidates around ``near``.

    Geometric screening only (footprint overlap with known structures and
    in-flight builds, mineral clearance, Pylon power); the SC2 adapter tests real
    placement legality. Sorted by distance from ``near``, then x, then y.
    """
    obs = view.observation
    size = FOOTPRINT[structure]
    offset = 0.5 if size % 2 else 0.0
    reach = radius_max + 6.0
    enemy_structures = tuple(e for e in obs.visible_enemies if e.is_structure)
    blockers: list[tuple[Point, float]] = [
        (e.position, float(FOOTPRINT.get(e.type_name, DEFAULT_FOOTPRINT)))
        for e in (*obs.own_structures, *enemy_structures)
        if distance(e.position, near) <= reach
    ]
    in_flight = view.in_flight_builds()
    minerals = [
        m.position
        for m in obs.mineral_fields
        if distance(m.position, near) <= reach + resource_clearance
    ]
    pylons = [p.position for p in obs.own_structures if p.type_name == "Pylon" and p.is_ready]
    base_x = math.floor(near[0]) + offset
    base_y = math.floor(near[1]) + offset
    span = math.ceil(radius_max) + 1
    found: list[tuple[float, Point]] = []
    for dx in range(-span, span + 1):
        for dy in range(-span, span + 1):
            point = (base_x + dx, base_y + dy)
            d = distance(point, near)
            if d < radius_min or d > radius_max:
                continue
            if any(_overlaps(point, size, center, other) for center, other in blockers):
                continue
            if any(
                _overlaps(point, size, site, FOOTPRINT[kind])
                and not (kind == structure and distance(point, site) < 1e-6)
                for kind, site in in_flight
            ):
                continue
            if any(distance(point, m) < resource_clearance + size / 2.0 for m in minerals):
                continue
            if require_power and not any(
                distance(point, pylon) <= PYLON_POWER_RADIUS for pylon in pylons
            ):
                continue
            found.append((d, point))
    found.sort(key=lambda item: (item[0], item[1][0], item[1][1]))
    return [point for _, point in found[:limit]]


def _select_placement(view: RuntimeView, args: Args) -> Selection:
    structure = _arg_str(args, "structure")
    near = resolve_point(view, _arg_str(args, "near"))
    if near is None:
        return Selection(None, "placement anchor unresolved", {})
    points = placement_candidates(
        view,
        structure=structure,
        near=near,
        radius_min=_arg_num(args, "radius_min"),
        radius_max=_arg_num(args, "radius_max"),
        require_power=_arg_bool(args, "require_power"),
        resource_clearance=_arg_num(args, "resource_clearance"),
        limit=_arg_int(args, "limit"),
    )
    if not points:
        reason = f"no {structure} placement candidate"
        if _arg_bool(args, "require_power"):
            reason += " within Pylon power"
        return Selection(None, reason, {"selected": 0})
    binding = Binding("points", points=tuple(points))
    return Selection(
        binding,
        f"{len(points)} {structure} candidate(s); first {format_point(points[0])}",
        {"selected": len(points), "points": binding.describe()},
    )


# ---------------------------------------------------------------------------
# Action implementations
# ---------------------------------------------------------------------------


def _own_actors(
    view: RuntimeView, entities: Sequence[Entity], *, structures: bool, worker: bool = False
) -> tuple[list[Entity], list[tuple[int, str]]]:
    """Split bound entities into commandable own actors and visibly blocked ones.

    A binding's kind says "entities", not whose: a policy may select enemies,
    mineral fields or the wrong unit type into an actor argument. Those are never
    commanded; each is reported with a reason instead. Binding order is kept.
    """
    owned = view.own_structure_tags() if structures else view.own_unit_tags()
    noun = "structure" if structures else "unit"
    actors: list[Entity] = []
    blocked: list[tuple[int, str]] = []
    for entity in entities:
        if entity.tag not in owned:
            blocked.append((entity.tag, f"entity {entity.tag} is not an own {noun}"))
        elif worker and entity.type_name != WORKER_TYPE:
            blocked.append((entity.tag, f"entity {entity.tag} is not a {WORKER_TYPE}"))
        else:
            actors.append(entity)
    return actors, blocked


def _gather(view: RuntimeView, args: Args) -> ActionPlan:
    bound_workers = _binding(view, _arg_str(args, "workers")).entities
    workers, blocked = _own_actors(view, bound_workers, structures=False, worker=True)
    mineral_tags = {m.tag for m in view.observation.mineral_fields}
    patches = [
        p for p in _binding(view, _arg_str(args, "minerals")).entities if p.tag in mineral_tags
    ]
    per_patch = _arg_int(args, "per_patch")
    if not patches:
        return ActionPlan(failure_reason="no mineral patch bound", blocked=tuple(blocked))
    patch_tags = {p.tag for p in patches}
    load: Counter[int] = Counter()
    for unit in view.observation.own_units:
        order = unit.orders[0] if unit.orders else None
        if order and order.ability == GATHER_ABILITY and isinstance(order.target, int):
            if order.target in patch_tags:
                load[order.target] += 1
    for patch_tag, count in view.gather_assignments().items():
        load[patch_tag] += count
    intents: list[Intent] = []
    for worker in sorted(workers, key=lambda w: w.tag):
        semantic = str(worker.tag)
        if worker.current_ability in HARVEST_ABILITIES:
            intents.append(Intent(worker.tag, semantic, GATHER_ABILITY, None, satisfied=True))
            continue
        if view.actor_is_busy(worker.tag):
            # Deduplicated (same intent) or refused (other holder) by the runtime.
            intents.append(Intent(worker.tag, semantic, GATHER_ABILITY, None))
            continue
        patch = min(
            patches,
            key=lambda p: (
                load[p.tag] >= per_patch,
                load[p.tag],
                distance(worker.position, p.position),
                p.tag,
            ),
        )
        load[patch.tag] += 1
        intents.append(Intent(worker.tag, semantic, GATHER_ABILITY, patch.tag))
    return ActionPlan(
        intents=tuple(intents), blocked=tuple(blocked), facts={"per_patch": per_patch}
    )


def _build(view: RuntimeView, args: Args) -> ActionPlan:
    structure = _arg_str(args, "structure")
    bound_workers = _binding(view, _arg_str(args, "worker")).entities
    workers, blocked = _own_actors(view, bound_workers, structures=False, worker=True)
    sites = _binding(view, _arg_str(args, "site")).points[:MAX_PLACEMENT_CANDIDATES]
    hold = _arg_bool(args, "hold_reservation")
    if not workers or not sites:
        reason = blocked[0][1] if blocked else "worker or site binding empty"
        return ActionPlan(failure_reason=reason, hold_reservation=hold)
    worker = workers[0]
    site = sites[0]
    intent = Intent(
        actor_tag=worker.tag,
        semantic=f"{structure}@{format_point(site)}",
        ability=BUILD_ABILITY[structure],
        target=site,
        alternatives=tuple(sites[1:]),
        minerals=MINERAL_COST[structure],
        params={PARAM_STRUCTURE: structure},
    )
    return ActionPlan(
        intents=(intent,),
        hold_reservation=hold,
        facts={"structure": structure, "worker": str(worker.tag), "site": [site[0], site[1]]},
    )


def _train(view: RuntimeView, args: Args) -> ActionPlan:
    unit = _arg_str(args, "unit")
    bound = sorted(_binding(view, _arg_str(args, "producers")).entities, key=lambda p: p.tag)
    producers, blocked = _own_actors(view, bound, structures=True)
    hold = _arg_bool(args, "hold_reservation")
    existing = sum(1 for u in view.observation.own_units if u.type_name == unit)
    intents: list[Intent] = []
    for producer in producers:
        if producer.type_name != PRODUCER_TYPE[unit]:
            blocked.append((producer.tag, f"{producer.type_name} cannot train {unit}"))
        elif not producer.is_ready:
            blocked.append((producer.tag, "producer not ready"))
        elif not producer.is_idle and not view.actor_is_busy(producer.tag):
            blocked.append((producer.tag, "producer busy"))
        else:
            intents.append(
                Intent(
                    actor_tag=producer.tag,
                    semantic=str(producer.tag),
                    ability=TRAIN_ABILITY[unit],
                    target=None,
                    minerals=MINERAL_COST[unit],
                    supply=SUPPLY_COST[unit],
                    params={PARAM_UNIT: unit, PARAM_UNIT_COUNT: existing},
                )
            )
    return ActionPlan(
        intents=tuple(intents),
        blocked=tuple(blocked),
        hold_reservation=hold,
        facts={"unit": unit},
    )


def _unit_command(view: RuntimeView, args: Args, ability: str) -> ActionPlan:
    units = _binding(view, _arg_str(args, "units")).entities
    target_binding = _binding(view, _arg_str(args, "target"))
    arrive_within = _arg_num(args, "arrive_within")
    preempt = _arg_bool(args, "preempt")
    target: Target
    target_key: str
    if target_binding.kind == "units" and ability == ATTACK_ABILITY:
        if not target_binding.entities:
            return ActionPlan(failure_reason="target binding empty", preempt=preempt)
        target = target_binding.entities[0].tag
        target_key = f"#{target}"
    else:
        point = target_binding.first_point()
        if point is None:
            return ActionPlan(failure_reason="target binding empty", preempt=preempt)
        target = point
        target_key = format_point(point)
    actors, blocked = _own_actors(view, sorted(units, key=lambda u: u.tag), structures=False)
    intents: list[Intent] = []
    for unit in actors:
        if isinstance(target, tuple):
            satisfied = distance(unit.position, target) <= arrive_within
        else:
            order = unit.orders[0] if unit.orders else None
            satisfied = order is not None and order.ability == ability and order.target == target
        intents.append(
            Intent(
                actor_tag=unit.tag,
                semantic=f"{unit.tag}>{target_key}",
                ability=ability,
                target=target,
                satisfied=satisfied,
                params={PARAM_ARRIVE_WITHIN: arrive_within},
            )
        )
    return ActionPlan(
        intents=tuple(intents),
        blocked=tuple(blocked),
        preempt=preempt,
        facts={"target": target_key, "units": len(units)},
    )


def _move(view: RuntimeView, args: Args) -> ActionPlan:
    return _unit_command(view, args, MOVE_ABILITY)


def _attack(view: RuntimeView, args: Args) -> ActionPlan:
    return _unit_command(view, args, ATTACK_ABILITY)


def _set_latch(view: RuntimeView, args: Args) -> ActionPlan:
    name = _arg_str(args, "latch")
    for latch in LATCHES:
        if latch == name:
            return ActionPlan(latch=latch, facts={"latch": latch})
    raise ValueError(f"unknown latch {safe_repr(name)}")


# ---------------------------------------------------------------------------
# Lifecycle checks (observation-confirmed; command issuance is never success)
# ---------------------------------------------------------------------------


def _target_matches(order_target: Target, wanted: Target) -> bool:
    if isinstance(wanted, tuple):
        return (
            isinstance(order_target, tuple)
            and distance(order_target, wanted) <= POINT_MATCH_TOLERANCE
        )
    return order_target == wanted


def _gather_ack(task: TaskSubject, obs: Observation) -> Ack | None:
    worker = obs.own_entity(task.actor_tag)
    if worker is not None and worker.current_ability in HARVEST_ABILITIES:
        return Ack()
    return None


def _immediate_progress(task: TaskSubject, obs: Observation) -> Progress:
    return Progress("complete", "acknowledged")


def _build_ack(task: TaskSubject, obs: Observation) -> Ack | None:
    structure = str(task.params.get(PARAM_STRUCTURE, ""))
    sites: list[Point] = [task.target] if isinstance(task.target, tuple) else []
    sites.extend(task.alternatives)
    for candidate in sorted(obs.own_structures, key=lambda e: e.tag):
        if candidate.type_name != structure:
            continue
        if any(distance(candidate.position, site) <= POINT_MATCH_TOLERANCE for site in sites):
            return Ack(tracked_tag=candidate.tag, target=candidate.position)
    return None


def _build_progress(task: TaskSubject, obs: Observation) -> Progress:
    if task.tracked_tag is None:
        return Progress("lost", "construction not tracked")
    structure = obs.own_entity(task.tracked_tag)
    if structure is None:
        return Progress("lost", "structure destroyed before completion")
    if structure.build_progress >= 1.0:
        return Progress("complete", "construction complete", structure.build_progress)
    return Progress("ongoing", "under construction", structure.build_progress)


def _train_ack(task: TaskSubject, obs: Observation) -> Ack | None:
    """D4: training is confirmed by the producer's queue *or* a new unit of the type.

    The new-unit path covers a unit that finished between two observations (its
    queue entry never seen). With several producers of one type it can confirm
    early; the producer is then idle again and the graph re-plans it next tick.
    """
    producer = obs.own_entity(task.actor_tag)
    if producer is None:
        return None
    if any(o.ability == task.ability for o in producer.orders):
        return Ack()
    unit = task.params.get(PARAM_UNIT)
    before = task.params.get(PARAM_UNIT_COUNT)
    if isinstance(unit, str) and isinstance(before, int) and not isinstance(before, bool):
        if sum(1 for u in obs.own_units if u.type_name == unit) > before:
            return Ack()
    return None


def _train_progress(task: TaskSubject, obs: Observation) -> Progress:
    producer = obs.own_entity(task.actor_tag)
    if producer is None:
        return Progress("lost", "producer destroyed")
    for order in producer.orders:
        if order.ability == task.ability:
            return Progress("ongoing", "training", order.progress)
    return Progress("complete", "unit trained")


def _unit_ack(task: TaskSubject, obs: Observation) -> Ack | None:
    unit = obs.own_entity(task.actor_tag)
    if unit is None:
        return None
    if isinstance(task.target, tuple):
        if distance(unit.position, task.target) <= decode_arrive_within(task.params):
            return Ack()
    order = unit.orders[0] if unit.orders else None
    if order is not None and order.ability == task.ability:
        if _target_matches(order.target, task.target):
            return Ack()
    return None


def _unit_progress(task: TaskSubject, obs: Observation) -> Progress:
    unit = obs.own_entity(task.actor_tag)
    if unit is None:
        return Progress("lost", "unit lost")
    if isinstance(task.target, tuple):
        gap = distance(unit.position, task.target)
        if gap <= decode_arrive_within(task.params):
            return Progress("complete", "arrived", gap)
        return Progress("ongoing", "moving", gap)
    for enemy in obs.visible_enemies:
        if enemy.tag == task.target:
            return Progress("ongoing", "engaging", distance(unit.position, enemy.position))
    return Progress("complete", "target no longer visible")


_GATHER_LIFECYCLE: Final = Lifecycle(
    acknowledge=_gather_ack,
    progress=_immediate_progress,
    completes_on_ack=True,
    deadline_kind=None,
    tracks_movement=False,
    holds_actor_while_running=False,
)
_BUILD_LIFECYCLE: Final = Lifecycle(
    acknowledge=_build_ack,
    progress=_build_progress,
    completes_on_ack=False,
    deadline_kind="build",
    tracks_movement=False,
    holds_actor_while_running=False,
)
_TRAIN_LIFECYCLE: Final = Lifecycle(
    acknowledge=_train_ack,
    progress=_train_progress,
    completes_on_ack=False,
    deadline_kind="train",
    tracks_movement=False,
    holds_actor_while_running=True,
)
_UNIT_LIFECYCLE: Final = Lifecycle(
    acknowledge=_unit_ack,
    progress=_unit_progress,
    completes_on_ack=False,
    deadline_kind=None,
    tracks_movement=True,
    holds_actor_while_running=True,
)

# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

_COMPARATOR = ArgSpec("enum", choices=COMPARATORS)
_BIND = ArgSpec("bind")
_HOLD = ArgSpec("bool", required=False, default=False)
_ACTION_OUTCOMES: Final[frozenset[NodeStatus]] = frozenset(NODE_STATUSES)
#: Conditions and selections never return running; waits never fail; a latch always succeeds.
_DECISIVE_OUTCOMES: Final[frozenset[NodeStatus]] = _ACTION_OUTCOMES - {"running"}
_NEVER_FAILS_OUTCOMES: Final[frozenset[NodeStatus]] = _ACTION_OUTCOMES - {"failure"}
_ALWAYS_SUCCEEDS_OUTCOMES: Final[frozenset[NodeStatus]] = _ACTION_OUTCOMES - {"failure", "running"}

_SPECS: Final[tuple[OperationSpec, ...]] = (
    PredicateOp(
        "count_compare",
        "Compare how many entities of a collection match a filter",
        {
            "collection": ArgSpec("enum", choices=ENTITY_COLLECTIONS),
            "filter": ArgSpec("filter", required=False),
            "op": _COMPARATOR,
            "value": ArgSpec("int", minimum=0, maximum=1000, param=True),
        },
        _count_compare,
    ),
    PredicateOp(
        "resource_compare",
        "Compare available minerals (after reservations) or supply",
        {
            "resource": ArgSpec("enum", choices=RESOURCE_NAMES),
            "op": _COMPARATOR,
            "value": ArgSpec("int", minimum=0, maximum=100000, param=True),
        },
        _resource_compare,
    ),
    PredicateOp(
        "threat_within",
        "Visible enemy within a distance of a point",
        {
            "point": ArgSpec("point"),
            "distance": ArgSpec("number", minimum=0.0, maximum=MAX_QUERY_DISTANCE, param=True),
            "include_flying": ArgSpec("bool", required=False, default=False),
            "include_structures": ArgSpec("bool", required=False, default=False),
        },
        _threat_within,
    ),
    PredicateOp(
        "task_count_compare",
        "Compare how many tasks of an action node are in the given states "
        "(active states: current tasks; terminal states: counted over the whole run)",
        {
            "node": ArgSpec("node"),
            "statuses": ArgSpec("enum_list", choices=TASK_STATUSES),
            "op": _COMPARATOR,
            "value": ArgSpec("int", minimum=0, maximum=1000, param=True),
        },
        _task_count_compare,
    ),
    PredicateOp(
        "latch_is_set",
        "Whether a cross-tick latch has been set",
        {"latch": ArgSpec("enum", choices=LATCHES)},
        _latch_is_set,
    ),
    PredicateOp(
        "game_time_compare",
        "Compare game time against a deadline in game seconds",
        {
            "op": _COMPARATOR,
            "seconds": ArgSpec("number", minimum=0.0, maximum=86400.0, param=True),
        },
        _game_time_compare,
    ),
    SelectOp(
        "select_entities",
        "Filter, sort and limit an entity collection",
        {
            "collection": ArgSpec("enum", choices=ENTITY_COLLECTIONS),
            "filter": ArgSpec("filter", required=False),
            "sort": ArgSpec("sort", required=False, choices=SORT_KEYS),
            "limit": ArgSpec("int", minimum=1, maximum=MAX_SELECTION, param=True),
            "min_count": ArgSpec(
                "int", required=False, minimum=1, maximum=MAX_SELECTION, default=1, param=True
            ),
            "exclude_busy": ArgSpec("bool", required=False, default=False),
            "bind": _BIND,
        },
        "units",
        _select_entities,
    ),
    SelectOp(
        "select_locations",
        "Sort and limit map start/expansion locations",
        {
            "source": ArgSpec("enum", choices=LOCATION_SOURCES),
            "sort": ArgSpec("sort", required=False, choices=LOCATION_SORT_KEYS),
            "limit": ArgSpec("int", minimum=1, maximum=MAX_LOCATIONS, param=True),
            "min_count": ArgSpec(
                "int", required=False, minimum=1, maximum=MAX_LOCATIONS, default=1, param=True
            ),
            "bind": _BIND,
        },
        "points",
        _select_locations,
    ),
    SelectOp(
        "select_point",
        "A point a fixed distance from one point toward another",
        {
            "from": ArgSpec("point"),
            "toward": ArgSpec("point"),
            "distance": ArgSpec("number", minimum=0.0, maximum=MAX_QUERY_DISTANCE, param=True),
            "bind": _BIND,
        },
        "points",
        _select_point,
    ),
    SelectOp(
        "select_placement",
        "Bounded placement candidates for a structure near a point",
        {
            "structure": ArgSpec("enum", choices=tuple(BUILD_ABILITY)),
            "near": ArgSpec("point"),
            "radius_min": ArgSpec(
                "number", required=False, minimum=0.0, maximum=MAX_PLACEMENT_RADIUS, default=0.0
            ),
            "radius_max": ArgSpec("number", minimum=1.0, maximum=MAX_PLACEMENT_RADIUS),
            "require_power": ArgSpec("bool"),
            "resource_clearance": ArgSpec(
                "number", required=False, minimum=0.0, maximum=10.0, default=3.0
            ),
            "limit": ArgSpec("int", minimum=1, maximum=MAX_PLACEMENT_CANDIDATES),
            "bind": _BIND,
        },
        "points",
        _select_placement,
    ),
    ActionOp(
        OP_GATHER,
        "Send workers to mine bound patches, least-loaded first up to per_patch",
        {
            "workers": ArgSpec("entities"),
            "minerals": ArgSpec("entities"),
            "per_patch": ArgSpec("int", minimum=1, maximum=3, param=True),
        },
        _gather,
        _ACTION_OUTCOMES,
        _GATHER_LIFECYCLE,
    ),
    ActionOp(
        OP_BUILD,
        "Order the first bound worker to build at the first bound site",
        {
            "structure": ArgSpec("enum", choices=tuple(BUILD_ABILITY)),
            "worker": ArgSpec("entities"),
            "site": ArgSpec("points"),
            "hold_reservation": _HOLD,
        },
        _build,
        _ACTION_OUTCOMES,
        _BUILD_LIFECYCLE,
    ),
    ActionOp(
        "train",
        "Train one unit from each bound idle producer",
        {
            "unit": ArgSpec("enum", choices=tuple(TRAIN_ABILITY)),
            "producers": ArgSpec("entities"),
            "hold_reservation": _HOLD,
        },
        _train,
        _ACTION_OUTCOMES,
        _TRAIN_LIFECYCLE,
    ),
    ActionOp(
        "move",
        "Move bound units to a bound point",
        {
            "units": ArgSpec("entities"),
            "target": ArgSpec("target"),
            "arrive_within": ArgSpec("number", minimum=0.5, maximum=50.0, param=True),
            "preempt": ArgSpec("bool", required=False, default=False),
        },
        _move,
        _ACTION_OUTCOMES,
        _UNIT_LIFECYCLE,
    ),
    ActionOp(
        "attack",
        "Attack-move bound units to a bound point, or attack a bound unit",
        {
            "units": ArgSpec("entities"),
            "target": ArgSpec("target"),
            "arrive_within": ArgSpec("number", minimum=0.5, maximum=50.0, param=True),
            "preempt": ArgSpec("bool", required=False, default=False),
        },
        _attack,
        _ACTION_OUTCOMES,
        _UNIT_LIFECYCLE,
    ),
    ActionOp(
        "set_latch",
        "Set a cross-tick latch (no game command)",
        {"latch": ArgSpec("enum", choices=LATCHES)},
        _set_latch,
        _ALWAYS_SUCCEEDS_OUTCOMES,
        None,
    ),
)

OPERATIONS: Final[Mapping[str, OperationSpec]] = {spec.name: spec for spec in _SPECS}


def operation_node_kinds(spec: OperationSpec) -> frozenset[NodeKind]:
    if isinstance(spec, PredicateOp):
        return frozenset({"condition", "wait"})
    if isinstance(spec, SelectOp):
        return frozenset({"select"})
    return frozenset({"action"})


def node_outcomes(kind: NodeKind, operation: str | None) -> frozenset[NodeStatus]:
    """Statuses a leaf node can return (used for unhandled-outcome analysis)."""
    if kind == "condition" or kind == "select":
        return _DECISIVE_OUTCOMES
    if kind == "wait":
        return _NEVER_FAILS_OUTCOMES
    spec = OPERATIONS.get(operation or "")
    if isinstance(spec, ActionOp):
        return spec.outcomes
    return _ACTION_OUTCOMES


# ---------------------------------------------------------------------------
# Argument validation, binding analysis and parameter resolution
# ---------------------------------------------------------------------------

type ArgIssue = tuple[str, str]
_BINDING_KINDS: Final[Mapping[ArgType, frozenset[BindingKind]]] = {
    "entities": frozenset({"units"}),
    "points": frozenset({"points"}),
    "point": frozenset(BINDING_KINDS),
    "target": frozenset(BINDING_KINDS),
}


def _is_number(value: JsonValue) -> bool:
    return is_bounded_number(value)


def _is_binding_ref(value: JsonValue) -> bool:
    return isinstance(value, str) and value.startswith("$") and full_match(NAME_RE, value[1:])


def _is_param_ref(value: JsonValue) -> bool:
    return isinstance(value, dict) and set(value) == {"param"} and isinstance(value["param"], str)


class _ArgContext:
    def __init__(
        self,
        parameters: Mapping[str, JsonValue],
        node_kinds: Mapping[str, NodeKind],
        collection: str | None,
    ) -> None:
        self.parameters = parameters
        self.node_kinds = node_kinds
        self.collection = collection


def _check_bounds(path: str, spec: ArgSpec, value: int | float) -> list[ArgIssue]:
    if spec.minimum is not None and value < spec.minimum:
        return [("invalid_arg", f"argument '{path}' must be >= {spec.minimum:g}, got {value:g}")]
    if spec.maximum is not None and value > spec.maximum:
        return [("invalid_arg", f"argument '{path}' must be <= {spec.maximum:g}, got {value:g}")]
    return []


def _check_numeric(path: str, spec: ArgSpec, value: JsonValue, ctx: _ArgContext) -> list[ArgIssue]:
    want_int = spec.type == "int"
    label = "an integer" if want_int else "a number"
    if _is_param_ref(value):
        assert isinstance(value, dict)
        name = str(value["param"])
        if not spec.param:
            return [("invalid_arg", f"argument '{path}' does not accept a parameter reference")]
        if name not in ctx.parameters:
            return [
                (
                    "unknown_parameter",
                    f"argument '{path}' cites unknown parameter {safe_repr(name)}",
                )
            ]
        resolved = ctx.parameters[name]
        ok = _is_number(resolved) and (not want_int or isinstance(resolved, int))
        if not ok:
            return [
                (
                    "invalid_parameter",
                    f"parameter '{name}' cited by '{path}' must be {label} with magnitude "
                    f"<= {MAX_ABS_NUMBER:g}, got {safe_repr(resolved)}",
                )
            ]
        assert isinstance(resolved, int | float)
        return _check_bounds(path, spec, resolved)
    ok = _is_number(value) and (not want_int or isinstance(value, int))
    if not ok:
        return [
            (
                "invalid_arg",
                f"argument '{path}' must be {label} with magnitude <= {MAX_ABS_NUMBER:g}, "
                f"got {safe_repr(value)}",
            )
        ]
    assert isinstance(value, int | float)
    return _check_bounds(path, spec, value)


def _check_point(path: str, value: JsonValue) -> list[ArgIssue]:
    if _is_binding_ref(value) or (isinstance(value, str) and value in POINT_KEYWORDS):
        return []
    keywords = ", ".join(POINT_KEYWORDS)
    return [
        (
            "invalid_arg",
            f"argument '{path}' must be one of {keywords} or a $binding, got {safe_repr(value)}",
        )
    ]


def _unknown_key_issues(prefix: str, unknown: set[str], noun: str = "key") -> list[ArgIssue]:
    """At most MAX_ISSUES_PER_FIELD named unknown keys, then one summary issue."""
    ordered = sorted(unknown)
    issues: list[ArgIssue] = [
        ("unknown_arg", f"{prefix} unknown {noun} {safe_repr(key)}")
        for key in ordered[:MAX_ISSUES_PER_FIELD]
    ]
    if len(ordered) > MAX_ISSUES_PER_FIELD:
        extra = len(ordered) - MAX_ISSUES_PER_FIELD
        issues.append(("unknown_arg", f"{prefix} {extra} more unknown {noun}(s)"))
    return issues


def _check_name_list(
    path: str,
    value: JsonValue,
    allowed: frozenset[str] | None,
    what: str,
    max_items: int | None = None,
) -> list[ArgIssue]:
    if not isinstance(value, list) or not value:
        return [("invalid_arg", f"argument '{path}' must be a non-empty list of {what}")]
    if max_items is not None and len(value) > max_items:
        return [
            (
                "invalid_arg",
                f"argument '{path}' lists {len(value)} {what}s; at most {max_items} allowed",
            )
        ]
    issues: list[ArgIssue] = []
    seen: set[str] = set()
    bad = 0
    for item in value:
        problem: str | None = None
        if not isinstance(item, str) or not item:
            problem = f"contains non-string {safe_repr(item)}"
        else:
            if item in seen:
                problem = f"repeats {safe_repr(item)}"
            elif allowed is not None and item not in allowed:
                problem = f"names unknown {what} {safe_repr(item)}"
            seen.add(item)
        if problem is not None:
            bad += 1
            if bad <= MAX_ISSUES_PER_FIELD:
                issues.append(("invalid_arg", f"argument '{path}' {problem}"))
    if bad > MAX_ISSUES_PER_FIELD:
        issues.append(
            ("invalid_arg", f"argument '{path}' has {bad - MAX_ISSUES_PER_FIELD} more bad entries")
        )
    return issues


def _check_filter(path: str, value: JsonValue, ctx: _ArgContext, depth: int) -> list[ArgIssue]:
    if not isinstance(value, dict):
        return [("invalid_arg", f"argument '{path}' must be an object")]
    if depth > MAX_FILTER_DEPTH:
        return [("invalid_arg", f"argument '{path}' nests any_of deeper than {MAX_FILTER_DEPTH}")]
    issues: list[ArgIssue] = []
    issues.extend(_unknown_key_issues(f"filter '{path}' has", set(value) - FILTER_KEYS))
    own = ctx.collection in ("own_units", "own_structures")
    type_universe = OWN_TYPES if own else None
    for key in ("types", "exclude_types"):
        if key in value:
            issues.extend(
                _check_name_list(
                    f"{path}.{key}", value[key], type_universe, "type", MAX_FILTER_NAMES
                )
            )
    if "order_in" in value:
        issues.extend(
            _check_name_list(
                f"{path}.order_in",
                value["order_in"],
                KNOWN_ORDER_ABILITIES,
                "ability",
                MAX_FILTER_NAMES,
            )
        )
    for key in _BOOL_FILTER_KEYS:
        if key in value and not isinstance(value[key], bool):
            issues.append(("invalid_arg", f"filter '{path}.{key}' must be a boolean"))
    if "within" in value:
        within = value["within"]
        if not isinstance(within, dict) or set(within) != {"point", "distance"}:
            issues.append(
                ("invalid_arg", f"filter '{path}.within' must be {{point, distance}} exactly")
            )
        else:
            issues.extend(_check_point(f"{path}.within.point", within["point"]))
            radius_spec = ArgSpec("number", minimum=0.0, maximum=MAX_QUERY_DISTANCE, param=True)
            issues.extend(
                _check_numeric(f"{path}.within.distance", radius_spec, within["distance"], ctx)
            )
    if "any_of" in value:
        nested = value["any_of"]
        if not isinstance(nested, list) or not 1 <= len(nested) <= MAX_ANY_OF:
            issues.append(
                ("invalid_arg", f"filter '{path}.any_of' must list 1..{MAX_ANY_OF} filters")
            )
        else:
            for index, item in enumerate(nested):
                issues.extend(_check_filter(f"{path}.any_of[{index}]", item, ctx, depth + 1))
    return issues


def _check_sort(path: str, spec: ArgSpec, value: JsonValue) -> list[ArgIssue]:
    if not isinstance(value, dict):
        return [("invalid_arg", f"argument '{path}' must be an object")]
    issues: list[ArgIssue] = []
    issues.extend(_unknown_key_issues(f"sort '{path}' has", set(value) - SORT_SPEC_KEYS))
    by = value.get("by")
    if by not in spec.choices:
        issues.append(("invalid_arg", f"sort '{path}.by' must be one of {', '.join(spec.choices)}"))
    if by == "distance":
        if "from" not in value:
            issues.append(("missing_arg", f"sort '{path}' by distance requires 'from'"))
        else:
            issues.extend(_check_point(f"{path}.from", value["from"]))
    elif "from" in value:
        issues.append(("invalid_arg", f"sort '{path}.from' is only valid when by is distance"))
    if "order" in value and value["order"] not in SORT_ORDERS:
        issues.append(("invalid_arg", f"sort '{path}.order' must be asc or desc"))
    return issues


def _check_value(path: str, spec: ArgSpec, value: JsonValue, ctx: _ArgContext) -> list[ArgIssue]:
    kind = spec.type
    if kind in ("int", "number"):
        return _check_numeric(path, spec, value, ctx)
    if kind == "bool":
        if isinstance(value, bool):
            return []
        return [("invalid_arg", f"argument '{path}' must be a boolean, got {safe_repr(value)}")]
    if kind == "enum":
        if isinstance(value, str) and value in spec.choices:
            return []
        choices = ", ".join(spec.choices)
        return [
            ("invalid_arg", f"argument '{path}' must be one of {choices}, got {safe_repr(value)}")
        ]
    if kind == "enum_list":
        return _check_name_list(path, value, frozenset(spec.choices), "value")
    if kind == "filter":
        return _check_filter(path, value, ctx, 1)
    if kind == "sort":
        return _check_sort(path, spec, value)
    if kind == "point":
        return _check_point(path, value)
    if kind in ("entities", "points", "target"):
        if _is_binding_ref(value):
            return []
        return [
            (
                "invalid_arg",
                f"argument '{path}' must be a $binding reference, got {safe_repr(value)}",
            )
        ]
    if kind == "bind":
        if full_match(NAME_RE, value):
            return []
        return [
            ("invalid_arg", f"argument '{path}' must be a binding name, got {safe_repr(value)}")
        ]
    # kind == "node"
    if not isinstance(value, str):
        return [("invalid_arg", f"argument '{path}' must be a node id, got {safe_repr(value)}")]
    target_kind = ctx.node_kinds.get(value)
    if target_kind is None:
        return [
            (
                "missing_reference",
                f"argument '{path}' references missing node {safe_repr(value)}",
            )
        ]
    if target_kind != "action":
        return [
            (
                "invalid_arg",
                f"argument '{path}' must reference an action node; '{value}' is {target_kind}",
            )
        ]
    return []


def validate_operation_args(
    operation: str,
    args: Mapping[str, JsonValue],
    *,
    parameters: Mapping[str, JsonValue],
    node_kinds: Mapping[str, NodeKind],
) -> list[ArgIssue]:
    """Return ``(code, message)`` issues for ``args`` against the operation's spec."""
    spec = OPERATIONS[operation]
    collection = args.get("collection")
    ctx = _ArgContext(parameters, node_kinds, collection if isinstance(collection, str) else None)
    issues: list[ArgIssue] = []
    issues.extend(
        _unknown_key_issues(f"operation '{operation}' has", set(args) - set(spec.args), "argument")
    )
    for key, arg_spec in spec.args.items():
        if key not in args:
            if arg_spec.required:
                issues.append(("missing_arg", f"operation '{operation}' requires argument '{key}'"))
            continue
        issues.extend(_check_value(key, arg_spec, args[key], ctx))
    if not issues:
        issues.extend(_cross_checks(operation, args, parameters))
    return issues


def _cross_checks(
    operation: str, args: Mapping[str, JsonValue], parameters: Mapping[str, JsonValue]
) -> list[ArgIssue]:
    resolved = resolve_args(operation, args, parameters)
    if operation in ("select_entities", "select_locations"):
        if _arg_int(resolved, "min_count") > _arg_int(resolved, "limit"):
            return [("invalid_arg", "argument 'min_count' must not exceed 'limit'")]
    if operation == "select_placement":
        if _arg_num(resolved, "radius_min") > _arg_num(resolved, "radius_max"):
            return [("invalid_arg", "argument 'radius_min' must not exceed 'radius_max'")]
    return []


def _filter_binding_refs(
    path: str, value: JsonValue, out: list[tuple[str, frozenset[BindingKind], str]]
) -> None:
    if not isinstance(value, dict):
        return
    within = value.get("within")
    if isinstance(within, dict) and _is_binding_ref(within.get("point")):
        out.append((str(within["point"])[1:], _BINDING_KINDS["point"], f"{path}.within.point"))
    nested = value.get("any_of")
    if isinstance(nested, list):
        for index, item in enumerate(nested):
            _filter_binding_refs(f"{path}.any_of[{index}]", item, out)


def binding_inputs(
    operation: str, args: Mapping[str, JsonValue]
) -> list[tuple[str, frozenset[BindingKind], str]]:
    """``(binding name, accepted kinds, argument path)`` for every $reference."""
    spec = OPERATIONS[operation]
    out: list[tuple[str, frozenset[BindingKind], str]] = []
    for key, arg_spec in spec.args.items():
        value = args.get(key)
        if value is None:
            continue
        accepted = _BINDING_KINDS.get(arg_spec.type)
        if accepted is not None and _is_binding_ref(value):
            out.append((str(value)[1:], accepted, key))
        elif arg_spec.type == "filter":
            _filter_binding_refs(key, value, out)
        elif arg_spec.type == "sort" and isinstance(value, dict):
            ref = value.get("from")
            if _is_binding_ref(ref):
                out.append((str(ref)[1:], _BINDING_KINDS["point"], f"{key}.from"))
    return out


def binding_output(operation: str, args: Mapping[str, JsonValue]) -> tuple[str, BindingKind] | None:
    spec = OPERATIONS[operation]
    if not isinstance(spec, SelectOp):
        return None
    name = args.get("bind")
    return (name, spec.binds) if isinstance(name, str) else None


def _substitute_params(value: JsonValue, parameters: Mapping[str, JsonValue]) -> JsonValue:
    if _is_param_ref(value):
        assert isinstance(value, dict)
        return parameters[str(value["param"])]
    if isinstance(value, dict):
        return {key: _substitute_params(item, parameters) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute_params(item, parameters) for item in value]
    return value


def resolve_args(
    operation: str, args: Mapping[str, JsonValue], parameters: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    """Fill declared defaults and substitute ``{"param": name}`` references.

    Only call on validated args: parameter references are already proven to exist.
    The result is a fresh read-only copy, so no runtime instance can mutate shared
    parameter values or the registry's ArgSpec defaults (which are scalars).
    """
    spec = OPERATIONS[operation]
    resolved: dict[str, JsonValue] = {}
    for key, arg_spec in spec.args.items():
        if key in args:
            resolved[key] = _substitute_params(args[key], parameters)
        elif not arg_spec.required:
            resolved[key] = arg_spec.default
    return FrozenJsonDict({key: freeze_json(value) for key, value in resolved.items()})


def parameter_references(value: JsonValue) -> set[str]:
    """Every parameter name cited anywhere inside ``value``."""
    if _is_param_ref(value):
        assert isinstance(value, dict)
        return {str(value["param"])}
    found: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            found |= parameter_references(item)
    elif isinstance(value, list):
        for item in value:
            found |= parameter_references(item)
    return found
