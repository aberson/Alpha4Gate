"""The one allowlisted operation registry for Jev policies.

Every operation a policy node may name is declared in :data:`OPERATIONS` with its
typed arguments and (for selections) its permitted binding output. The validator
(:mod:`jev.policy`) checks nodes against these declarations; the interpreter
(:mod:`jev.runtime`) calls the implementations below.

Three categories:

* **Predicates** (``condition`` and ``wait`` nodes): count comparisons, resources
  and supply, planned supply (see :func:`planned_supply`), threat distance, task
  state (task counts, recent task outcomes and which map locations a node
  visited), the ``attack_launched`` latch (set or still clear), game time, the
  enemy sighting memory (``sighting_count_compare``: counts and ages of what the
  bot has seen, the safety facts later plan choices read) and how long ago an
  action node took charge of a new member (``assignment_age_compare``).
  Powered/idle/ready checks are filter keys on the counted collection.
* **Selections** (``select`` nodes): filter/sort/limit over an observation
  collection, map locations (optionally least recently visited first, which
  cycles through them), a derived point, or bounded placement candidates. The
  result is bound under the node's ``bind`` name in the root-local context.
* **Actions** (``action`` nodes): explicit gather/build/train/move/attack command
  intents plus the ``set_latch`` memory write. The runtime turns intents into
  deduplicated tasks and :class:`~jev.contracts.CommandSpec` records.

Gas (Jev v2, plan J2 Step 216) is three explicit actions, never a helper that
"manages" it: ``build_gas`` orders a gas structure on a vespene geyser *unit* (its
tag is the command target; there is no placement query and no point-target build
of a gas structure -- ``build``/``select_placement`` do not accept one);
``gather_gas`` fills a gas building's open slots from bound workers; ``release_gas``
returns a gas building's surplus workers to minerals. A gas building's slots
(:func:`gas_slots`) are the authored per-building count, never above SC2's ideal,
and none while it is unfinished or once it is depleted. Every cost is a minerals
*and* a vespene amount (:data:`MINERAL_COST`, :data:`VESPENE_COST`); the runtime
reserves both and releases them on acknowledgement or failure.

Visits are explicit runtime memory, not a hidden variable in an operation: for
every action node a ``visits`` argument names, the runtime records which map
locations that node visited and when (:class:`Visit`, read through
:meth:`RuntimeView.visits`). A node *visits* a
location when one of its tasks targeting it succeeds (arrived) or gives up on
it -- fails ``no_progress`` or ``deadline``, the D4 replan -- and when the node
finds one of its actors already there (an intent satisfied on arrival). A
search therefore moves on both from a location it reached and from one it
could not reach.

Demotions are the same kind of memory for entity targets: for every action node
a ``demotions`` argument names, an entity the node's tasks gave up on in
consecutive rounds is *demoted* (:meth:`RuntimeView.demoted`), and a selection
with ``skip_demoted_by`` passes over it -- to the next structure, memory or
the search -- until the demotion lapses (see :mod:`jev.runtime`).

Two further memories are kept only when a policy reads them (Jev v2; a policy
that names neither, such as v1, never records them):

* **Assignments.** When any ``assigned_to`` / ``not_assigned_to`` argument (or an
  ``assignment_age_compare``) names a node, the runtime remembers, per own entity,
  the action node that most recently took charge of it: created a task for it,
  holds it with an active task, or found it already in that node's intended state
  while no other node's task held it (:meth:`RuntimeView.assigned`). A unit
  therefore stays in a group after its task ends until another node takes charge
  of it, so a selection can tell an army group from reinforcements without a
  distance guess -- and a lane can own an actor (the v2 scout) that every other
  selection excludes with ``not_assigned_to`` even between its tasks. Per node,
  the time it last took charge of a new member is kept too
  (:meth:`RuntimeView.assignment_age`), so replacements can be spaced.
* **Finishes.** For every action node a ``recent_task_count_compare`` names, the
  game times and terminal statuses of its latest
  :data:`MAX_TRACKED_FINISHES` finished tasks (:meth:`RuntimeView.recent_task_count`),
  so a lane can back off after repeated failures instead of retrying new
  intents forever.

Persistent plans (Jev v2, plan J2 Step 217; :mod:`jev.plans`): ``start_plan``
opens a plan for its root lane (one at a time per slot, never a duplicate), and
the lane's nodes are its stages. ``plan_status_is`` reads it; ``select_expansion``
chooses (and commits) the plan's site -- the nearest *reachable* unoccupied
expansion location by SC2 path distance (:class:`~jev.contracts.PathFact`):
Euclidean distance only orders at most :data:`MAX_EXPANSION_CANDIDATES`
candidates, and a candidate without a path fact is not yet eligible (the
selection asks the adapter to query it) -- a site farther than ``max_path_ratio``
times the nearest reachable one is never taken; ``plan_site_seen`` requires fresh
own vision of the site (unknown is not safe); ``select_plan_actor`` keeps the plan's
one worker or claims a free one; ``plan_wait`` holds the plan still on a named
blocker; ``build_expansion`` builds the plan's Nexus at its site (never a third:
:data:`MAX_BASES`); ``transfer_workers`` moves workers to the new base;
``plan_complete`` ends the plan; ``plan_reserve`` holds the plan's next
structure's cost each tick (a reservation the plan's own build then takes over:
transferred, never charged twice). Their failures carry fixed reason codes
(:data:`jev.plans.PLAN_CODES`), so the plan records why it waits.

Limited tech and a mixed army (Jev v2, plan J2 Step 218, D4): the Cybernetics Core
and the Stalker join the tables, with SC2's tech requirements in
:data:`TECH_REQUIREMENT` (the Core needs a ready Gateway, the Stalker a ready Core).
``build`` and ``train`` check that requirement against the observation before they
ask for anything, and ``train`` never uses a producer SC2 reports unpowered, so a
lost or unfinished prerequisite blocks visibly instead of reaching SC2. SC2 counts a
prerequisite's build progress only; a ``train`` node may opt into a stricter,
operational prerequisite (``powered_prerequisite``: the Core must also have Pylon
power), which v2's Stalker node does. ``train`` also takes optional composition
guards -- ``max_count`` and a ``max_ratio`` to another unit type (``ratio_of``) --
over *planned* counts (standing units, queued orders and unconfirmed train tasks,
:meth:`RuntimeView.in_flight_trains`), so one tick with many idle producers never
overshoots the mix. ``affordable`` asks whether
a type's price from the central tables is available after reservations (so a policy
never spells out a cost). ``attack`` never assigns a flying target to an actor whose
weapons cannot hit air (``Entity.can_attack_air``): such actors are blocked with a
reason, so a Zealot is never ordered onto an air unit while a Stalker may be.

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
    ACTIVE_TASK_STATUSES,
    ENTITY_COLLECTIONS,
    LATCHES,
    LOCATION_SOURCES,
    MAX_ABS_NUMBER,
    NAME_RE,
    NODE_STATUSES,
    POINT_KEYWORDS,
    SIGHTING_VISIBILITIES,
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
    TaskStatus,
    freeze_json,
    full_match,
    is_bounded_number,
    safe_repr,
)
from jev.plans import (
    PLAN_KINDS,
    PLAN_STATUSES,
    WAIT_CODES,
    PlanCode,
    PlanKind,
    PlanRecord,
)

__all__ = [
    "AFFORD_RESOURCES",
    "ATTACK_ABILITY",
    "Ack",
    "ActionOp",
    "ActionPlan",
    "AffordResource",
    "ArgIssue",
    "ArgSpec",
    "ArgType",
    "Args",
    "BINDING_KINDS",
    "BOOL_FILTER_ATTRIBUTES",
    "BUILD_ABILITY",
    "Binding",
    "BindingKind",
    "COMMAND_ABILITIES",
    "COMPARATORS",
    "Comparator",
    "CompiledFilter",
    "DEFAULT_FOOTPRINT",
    "ENTITY_SORT_METRICS",
    "EXPANSION_BUILD_ABILITY",
    "EXPANSION_STRUCTURE",
    "FILTER_KEYS",
    "FOOTPRINT",
    "GAS_BUILD_ABILITY",
    "GAS_DEPLETED_REASON",
    "GAS_FOOTPRINT",
    "GAS_STRUCTURE_TYPES",
    "GATHER_ABILITY",
    "GATHER_TARGET_LOST_REASON",
    "HARVEST_ABILITIES",
    "Intent",
    "KNOWN_ORDER_ABILITIES",
    "LOCATION_SORT_KEYS",
    "Lifecycle",
    "MAX_ANY_OF",
    "MAX_BASES",
    "MAX_COMPOSITION_RATIO",
    "MAX_ELAPSED_SECONDS",
    "MAX_EXPANSION_CANDIDATES",
    "MAX_FILTER_DEPTH",
    "MAX_FILTER_NAMES",
    "MAX_GAS_HARVESTERS",
    "MAX_ISSUES_PER_FIELD",
    "MAX_LOCATIONS",
    "MAX_PLACEMENT_CANDIDATES",
    "MAX_PLACEMENT_RADIUS",
    "MAX_QUERY_DISTANCE",
    "MAX_SELECTION",
    "MAX_SUPPLY",
    "MAX_TRACKED_FINISHES",
    "MINERAL_COST",
    "MOVE_ABILITY",
    "OPERATIONS",
    "OP_BUILD",
    "OP_BUILD_EXPANSION",
    "OP_BUILD_GAS",
    "OP_GATHER",
    "OP_GATHER_GAS",
    "OP_RELEASE_GAS",
    "OP_START_PLAN",
    "OP_TRAIN",
    "OP_TRANSFER_WORKERS",
    "OWN_TYPES",
    "OpOutcome",
    "OperationSpec",
    "PARAM_ARRIVE_WITHIN",
    "PARAM_SITE",
    "PARAM_TARGET_TAG",
    "PARAM_STRUCTURE",
    "PARAM_UNIT",
    "PARAM_UNIT_COUNT",
    "POINT_MATCH_TOLERANCE",
    "PRODUCER_TYPE",
    "PYLON_POWER_RADIUS",
    "PlanEffect",
    "PredicateOp",
    "Progress",
    "REQUIRES_POWER",
    "RESOURCE_NAMES",
    "RESOURCE_READERS",
    "RETURN_ABILITY",
    "RuntimeView",
    "SORT_KEYS",
    "SORT_ORDERS",
    "SORT_SPEC_KEYS",
    "SUPPLY_COST",
    "SUPPLY_MEASURES",
    "SUPPLY_PROVIDED",
    "SelectOp",
    "Selection",
    "SortKey",
    "SortOrder",
    "SupplyMeasure",
    "SupplyPlan",
    "TARGET_LOST_REASON",
    "TECH_REQUIREMENT",
    "TERMINAL_TASK_STATUSES",
    "TRACKED_MEMORIES",
    "TRAIN_ABILITY",
    "TaskSubject",
    "TrackedMemory",
    "VESPENE_COST",
    "VIEW_FILTER_ATTRIBUTES",
    "Visit",
    "WORKER_TYPE",
    "binding_inputs",
    "binding_output",
    "compare",
    "compile_filter",
    "decode_arrive_within",
    "decode_site",
    "distance",
    "entity_matches",
    "expansion_candidates",
    "format_point",
    "gas_slots",
    "is_gas_worker",
    "node_outcomes",
    "operation_node_kinds",
    "parameter_references",
    "placement_candidates",
    "planned_supply",
    "planned_unit_count",
    "resolve_args",
    "resolve_point",
    "tracked_nodes",
    "validate_operation_args",
]

# ---------------------------------------------------------------------------
# Game data: the single source of truth for Jev's allowed unit/structure set.
# ---------------------------------------------------------------------------

GATHER_ABILITY: Final = "HARVEST_GATHER"
RETURN_ABILITY: Final = "HARVEST_RETURN"
MOVE_ABILITY: Final = "MOVE"
ATTACK_ABILITY: Final = "ATTACK"
HARVEST_ABILITIES: Final = frozenset({GATHER_ABILITY, RETURN_ABILITY})

#: Structures Jev may build *at a point* (placement candidates the SC2 adapter tests)
#: -> generic SC2 build ability. No Nexus here: a Nexus is built only by
#: ``build_expansion`` at a plan's path-checked site (EXPANSION_BUILD_ABILITY), so
#: no placement selection can propose one. A gas structure is never a point build:
#: see GAS_BUILD_ABILITY.
BUILD_ABILITY: Final[Mapping[str, str]] = {
    "Pylon": "PROTOSSBUILD_PYLON",
    "Gateway": "PROTOSSBUILD_GATEWAY",
    "CyberneticsCore": "PROTOSSBUILD_CYBERNETICSCORE",  # plan J2 Step 218 (D4)
}
#: Gas structures Jev v2 may build -> generic SC2 build ability. SC2 builds them on
#: a vespene geyser *unit*: the command targets the geyser's tag (burnysc2's
#: ``Unit.build_gas``), never a point, and only ``build_gas`` emits them.
GAS_BUILD_ABILITY: Final[Mapping[str, str]] = {"Assimilator": "PROTOSSBUILD_ASSIMILATOR"}
#: The base structure Jev v2 may build -> generic SC2 build ability (plan J2 Step 217):
#: only ``build_expansion`` emits it, at an expansion plan's site, and never a third.
EXPANSION_BUILD_ABILITY: Final[Mapping[str, str]] = {"Nexus": "PROTOSSBUILD_NEXUS"}
EXPANSION_STRUCTURE: Final = "Nexus"
#: D3/D4: at most two Nexuses, completed or in progress (no third base).
MAX_BASES: Final = 2
#: D4: path-query at most this many expansion candidates (nearest first by Euclidean
#: distance from home); the adapter caches each static answer for the match.
MAX_EXPANSION_CANDIDATES: Final = 8
#: Every race's gas extraction structure type (SC2 names a rich-geyser variant
#: separately). The ``gas_building`` filter flag reads this set, so a policy names
#: its gas buildings without listing type names.
GAS_STRUCTURE_TYPES: Final = frozenset(
    {"Assimilator", "AssimilatorRich", "Extractor", "ExtractorRich", "Refinery", "RefineryRich"}
)
#: Footprint edge (cells) of a gas structure: a vespene geyser's 3x3.
GAS_FOOTPRINT: Final = 3
#: SC2's ideal harvester count for one gas building (game data): the most workers
#: any ``per_building`` argument may ask for.
MAX_GAS_HARVESTERS: Final = 3
#: Units Jev may train -> generic SC2 train ability.
TRAIN_ABILITY: Final[Mapping[str, str]] = {
    "Probe": "NEXUSTRAIN_PROBE",
    "Zealot": "GATEWAYTRAIN_ZEALOT",
    "Stalker": "GATEWAYTRAIN_STALKER",  # plan J2 Step 218 (D4)
}
#: Unit -> structure type that trains it.
PRODUCER_TYPE: Final[Mapping[str, str]] = {
    "Probe": "Nexus",
    "Zealot": "Gateway",
    "Stalker": "Gateway",
}
#: SC2's tech requirements among the allowlisted types: the structure type that must
#: stand *ready* (build complete) before this type may be built or trained. The
#: allowlisted part of burnysc2's ``PROTOSS_TECH_REQUIREMENT``: SC2 counts build
#: progress only, never Pylon power (``BotAI.tech_requirement_progress``). Pylon,
#: Gateway, Nexus, Assimilator, Probe and Zealot have none (a Gateway's need for
#: Pylon power is its placement, which ``select_placement`` screens).
TECH_REQUIREMENT: Final[Mapping[str, str]] = {
    "CyberneticsCore": "Gateway",
    "Stalker": "CyberneticsCore",
}
#: The unit type that mines and constructs.
WORKER_TYPE: Final = "Probe"
#: Costs (SC2 5.0 game data; the Assimilator is 75 minerals since LotV; Step 218
#: adds the Cybernetics Core at 150 minerals and the Stalker at 125 minerals, 50
#: vespene and 2 supply). Both cost tables cover every buildable and trainable
#: type, so an intent always carries both amounts.
MINERAL_COST: Final[Mapping[str, int]] = {
    "Probe": 50,
    "Zealot": 100,
    "Stalker": 125,
    "Pylon": 100,
    "Gateway": 150,
    "CyberneticsCore": 150,
    "Assimilator": 75,
    "Nexus": 400,
}
VESPENE_COST: Final[Mapping[str, int]] = {
    "Probe": 0,
    "Zealot": 0,
    "Stalker": 50,
    "Pylon": 0,
    "Gateway": 0,
    "CyberneticsCore": 0,
    "Assimilator": 0,
    "Nexus": 0,
}
SUPPLY_COST: Final[Mapping[str, int]] = {"Probe": 1, "Zealot": 2, "Stalker": 2}
#: Supply each supply-providing structure adds once complete (SC2 game data).
SUPPLY_PROVIDED: Final[Mapping[str, int]] = {"Nexus": 15, "Pylon": 8}
#: SC2's supply ceiling: no supply structure raises the cap above it.
MAX_SUPPLY: Final = 200
#: Square footprint edge length (cells) for placement overlap checks.
FOOTPRINT: Final[Mapping[str, int]] = {
    "Nexus": 5,
    "Pylon": 2,
    "Gateway": 3,
    "CyberneticsCore": 3,
}
DEFAULT_FOOTPRINT: Final = 2
PYLON_POWER_RADIUS: Final = 6.5
#: Allowed structures that only operate inside Pylon power (every other own
#: structure -- Nexus, Pylon, gas buildings -- needs none). The SC2 adapter reads
#: SC2's powered flag for these types only.
REQUIRES_POWER: Final = frozenset({"Gateway", "CyberneticsCore"})
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
    | set(GAS_BUILD_ABILITY.values())
    | set(EXPANSION_BUILD_ABILITY.values())
    | set(TRAIN_ABILITY.values())
)
#: Abilities a command may carry -- exactly what the action operations below emit
#: (a worker returns cargo on its own; Jev never orders HARVEST_RETURN).
COMMAND_ABILITIES: Final = KNOWN_ORDER_ABILITIES - {RETURN_ABILITY}
#: Build ability -> the supply structure it starts (a worker still carrying one).
_SUPPLY_BUILDS: Final[Mapping[str, str]] = {
    ability: structure
    for structure, ability in BUILD_ABILITY.items()
    if structure in SUPPLY_PROVIDED
}
#: Producer type -> supply of the most expensive unit it trains (its next unit).
_NEXT_UNIT_SUPPLY: Final[Mapping[str, int]] = {
    producer: max(SUPPLY_COST[unit] for unit, owner in PRODUCER_TYPE.items() if owner == producer)
    for producer in sorted(set(PRODUCER_TYPE.values()))
}
#: Terminal task statuses (the ones a finished task can have).
TERMINAL_TASK_STATUSES: Final[tuple[TaskStatus, ...]] = tuple(
    status for status in TASK_STATUSES if status not in ACTIVE_TASK_STATUSES
)
#: Finished tasks remembered per node a ``recent_task_count_compare`` names.
MAX_TRACKED_FINISHES: Final = 64

MAX_PLACEMENT_CANDIDATES: Final = 8
#: Operation names the runtime's reservation views recognize (used by the registry too).
OP_BUILD: Final = "build"
OP_TRAIN: Final = "train"
OP_GATHER: Final = "gather"
OP_BUILD_GAS: Final = "build_gas"
OP_GATHER_GAS: Final = "gather_gas"
OP_RELEASE_GAS: Final = "release_gas"
OP_BUILD_EXPANSION: Final = "build_expansion"
OP_TRANSFER_WORKERS: Final = "transfer_workers"
OP_START_PLAN: Final = "start_plan"
#: Why a gather task's target can no longer be gathered from (D4: visible outcome).
GATHER_TARGET_LOST_REASON: Final = (
    "gather target lost: no longer an own gas building or a visible mineral field"
)
GAS_DEPLETED_REASON: Final = "gas building depleted"
#: Intent.params keys shared by the operations' lifecycle checks and the runtime.
PARAM_STRUCTURE: Final = "structure"
PARAM_UNIT: Final = "unit"
#: Own units of the trained type when the train intent was planned (D4 "new unit" ack).
PARAM_UNIT_COUNT: Final = "unit_count"
PARAM_ARRIVE_WITHIN: Final = "arrive_within"
#: The entity an attack intent was planned against (its target may be the point
#: where that entity was seen); the runtime keys give-ups and demotions on it.
PARAM_TARGET_TAG: Final = "target_tag"
#: Where a gas build's geyser stands (``[x, y]``): its target is the geyser's tag,
#: so acknowledgement, site exclusion and placement screening read the site here.
PARAM_SITE: Final = "site"


def decode_site(params: Mapping[str, JsonValue]) -> Point | None:
    """THE decode of a gas build's geyser site: ``[x, y]`` -> a point, else None."""
    value = params.get(PARAM_SITE)
    if not isinstance(value, list | tuple) or len(value) != 2:
        return None
    x, y = value
    if isinstance(x, int | float) and isinstance(y, int | float):
        if is_bounded_number(x) and is_bounded_number(y):
            return (float(x), float(y))
    return None


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
#: Largest ``max_ratio`` a train node's composition guard may name.
MAX_COMPOSITION_RATIO: Final = 8.0
#: Most map locations a select_locations node may bind.
MAX_LOCATIONS: Final = 64
#: Largest distance any distance-valued argument may take (game units).
MAX_QUERY_DISTANCE: Final = 200.0
#: Largest game-time span (an age or interval, game seconds) an argument may take.
MAX_ELAPSED_SECONDS: Final = 86400.0
MAX_FILTER_DEPTH: Final = 3
MAX_ANY_OF: Final = 8
#: A list-valued field (unknown keys, name lists, ...) names at most this many bad
#: entries, then one summary issue -- per-site output stays O(1) on hostile input.
MAX_ISSUES_PER_FIELD: Final = 8
#: Most names a filter's types/exclude_types/order_in list may hold.
MAX_FILTER_NAMES: Final = 32
#: An order/structure within this distance of a target point counts as "at" it.
POINT_MATCH_TOLERANCE: Final = 1.0
#: Why a unit-targeted task ends at once (D4): its target is no longer a visible
#: enemy -- destroyed or out of sight; visible state cannot tell which.
TARGET_LOST_REASON: Final = "target lost: no longer a visible enemy"

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


@dataclass(frozen=True)
class Visit:
    """Runtime memory: how many visit events an action node had at one map location,
    and the game time of the latest (the module docstring says what counts)."""

    count: int
    last_game_seconds: float


class RuntimeView(Protocol):
    """Read-only facts an operation may consult (implemented by the runtime)."""

    @property
    def observation(self) -> Observation: ...

    def binding(self, name: str) -> Binding | None: ...

    def available_minerals(self) -> int:
        """Observed minerals minus in-flight commitments and this tick's holds."""
        ...

    def available_vespene(self) -> int:
        """Observed vespene minus in-flight commitments and this tick's holds."""
        ...

    def available_supply(self) -> int:
        """Free supply minus in-flight training commitments and this tick's holds."""
        ...

    def latch_is_set(self, latch: str) -> bool: ...

    def army_mode(self) -> str | None: ...

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
        """``(structure, site)`` for build tasks whose structure has not appeared yet
        (a gas build's site is its geyser's position)."""
        ...

    def gather_assignments(self) -> Mapping[int, int]:
        """Mineral-patch or gas-building tag -> workers assigned to it by unacknowledged
        gather tasks (mineral and gas gathers alike)."""
        ...

    def in_flight_trains(self) -> Mapping[str, int]:
        """Unit type -> train tasks whose command is not confirmed yet (their queue
        entry is not in the observation; a confirmed one is a queued order)."""
        ...

    def rejected_sites(self, structure: str) -> frozenset[Point]:
        """Placement candidates of ``structure`` builds that failed placement.

        The sites a build task tried (target and alternatives) when it failed as
        rejected or unacknowledged, for as long as that intent cools down. It is
        task state, so recovery selects a *new* site instead of re-proposing the
        same rejected ones (D3/D4).
        """
        ...

    def visits(self, node_id: str) -> Mapping[Point, Visit]:
        """Map locations action node ``node_id`` visited, keyed exactly.

        Recorded by the runtime for every node a ``visits`` argument names (empty for
        any other node), for the observation's start and expansion locations
        only. Keys are the tasks' targets verbatim -- the bound points -- so a
        location from map metadata matches itself exactly.
        """
        ...

    def demoted(self, node_id: str) -> frozenset[int]:
        """Entity tags action node ``node_id`` has demoted and not re-allowed yet.

        Recorded for every node a ``demotions`` argument names (empty for any
        other node); see :mod:`jev.runtime` for when a demotion starts and lapses.
        """
        ...

    def assigned(self, node_id: str) -> frozenset[int]:
        """Own entity tags whose latest assignment is action node ``node_id``.

        Kept only while some ``assigned_to`` / ``not_assigned_to`` argument names a
        node (empty otherwise); the module docstring says what takes charge.
        """
        ...

    def recent_task_count(self, node_id: str, statuses: frozenset[str], within: float) -> int:
        """Tasks of ``node_id`` that finished in ``statuses`` less than ``within``
        game seconds ago (of its latest :data:`MAX_TRACKED_FINISHES` finished tasks).

        Kept for every node a ``recent_task_count_compare`` names (0 for any other).
        """
        ...

    def assignment_age(self, node_id: str) -> float | None:
        """Game seconds since action node ``node_id`` last took charge of an entity
        that was not already in its charge; None if it never did.

        Kept while the policy reads assignments (None otherwise); it outlives the
        entity, so a replacement after a loss is spaced from the previous claim.
        """
        ...

    def plan(self, node_id: str) -> PlanRecord | None:
        """The latest plan started by ``start_plan`` node ``node_id`` (open or
        finished); None before it ever started one, or while no policy node starts
        plans (see :mod:`jev.plans`)."""
        ...


@dataclass(frozen=True)
class PlanEffect:
    """A plan write an operation asks the runtime for (applied only when its node
    succeeds): ``start`` a plan for the node itself (of ``kind``), commit a ``site``
    (``replaced``: why a previous one was given up), claim an ``actor``, hold a
    ``reserve`` of ``minerals``/``vespene`` this tick, or ``complete`` the plan named
    by ``plan`` (a start node id)."""

    action: Literal["start", "site", "actor", "reserve", "complete"]
    plan: str = ""
    kind: PlanKind | None = None
    point: Point | None = None
    tag: int | None = None
    replaced: PlanCode | None = None
    minerals: int = 0
    vespene: int = 0
    #: A site's SC2 path distance and its rank among the reachable candidates.
    path: float | None = None
    rank: int | None = None


@dataclass(frozen=True)
class OpOutcome:
    """Predicate result: ``ok`` maps to success (condition) or done-waiting (wait).
    ``code`` is a plan reason code a failure means (None: generic)."""

    ok: bool
    reason: str
    facts: Mapping[str, JsonValue] = field(default_factory=dict)
    code: PlanCode | None = None


@dataclass(frozen=True)
class Selection:
    """Selection result; ``binding`` is None when fewer than ``min_count`` matched.

    Plan selections may also carry a plan reason ``code``, the ``effect`` to apply on
    success and ``queries``: expansion locations whose SC2 path distance the
    adapter must query before the next tick (missing answers mean not yet eligible).
    """

    binding: Binding | None
    reason: str
    facts: Mapping[str, JsonValue] = field(default_factory=dict)
    code: PlanCode | None = None
    effect: PlanEffect | None = None
    queries: tuple[Point, ...] = ()


@dataclass(frozen=True)
class Intent:
    """One per-actor side effect an action wants. The runtime owns dedup/budgets.

    ``minerals`` / ``vespene`` / ``supply`` are what the command costs: the runtime
    commits them to the task until the observation acknowledges it (SC2 has then
    deducted them) or the task fails, so concurrent spenders never double-spend.
    """

    actor_tag: int
    semantic: str
    ability: str
    target: Target
    alternatives: tuple[Point, ...] = ()
    minerals: int = 0
    supply: int = 0
    satisfied: bool = False
    params: Mapping[str, JsonValue] = field(default_factory=dict)
    vespene: int = 0


@dataclass(frozen=True)
class ActionPlan:
    """What an action wants. Besides intents: a ``latch`` write, a plan ``effect``
    (no command; success unless refused), or ``wait_reason`` (the node waits:
    running, no command). ``code`` is the plan reason code of the outcome."""

    intents: tuple[Intent, ...] = ()
    blocked: tuple[tuple[int, str], ...] = ()
    failure_reason: str | None = None
    latch: Latch | None = None
    hold_reservation: bool = False
    preempt: bool = False
    facts: Mapping[str, JsonValue] = field(default_factory=dict)
    effect: PlanEffect | None = None
    wait_reason: str | None = None
    code: PlanCode | None = None


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
    """A running task's progress. ``metric`` is the distance still to close;
    ``engaged`` is the health plus shields of the enemies the unit is fighting (None
    when it is not fighting) -- the runtime counts a fall in their total by the next
    progress check as progress, so a fight that hurts nobody is still a stall (D4)."""

    state: Literal["ongoing", "complete", "lost"]
    reason: str
    metric: float | None = None
    engaged: Mapping[int, float] | None = None


@dataclass(frozen=True)
class Lifecycle:
    """Observation-based confirmation rules for one action operation (plan D4).

    ``target_lost`` names why a not-yet-acknowledged task's target can no longer
    be acted on (None while it can); ``progress`` reports the same for a running
    task as ``lost``. Either way the task is invalidated at once (D4).
    """

    acknowledge: Callable[[TaskSubject, Observation], Ack | None]
    progress: Callable[[TaskSubject, Observation], Progress]
    target_lost: Callable[[TaskSubject, Observation], str | None]
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
    "plan",
]


#: Runtime memories an operation can read about an action node's tasks.
TrackedMemory = Literal["visits", "demotions", "assignments", "finishes"]
TRACKED_MEMORIES: Final[tuple[TrackedMemory, ...]] = get_args(TrackedMemory)
#: What a ``planned_supply_compare`` measures (see :func:`planned_supply`): the
#: planned free supply, or how far the planned cap can still grow before
#: :data:`MAX_SUPPLY` (so a policy never spells out SC2's ceiling itself).
SupplyMeasure = Literal["free", "ceiling_room"]
SUPPLY_MEASURES: Final[tuple[SupplyMeasure, ...]] = get_args(SupplyMeasure)
#: What an ``affordable`` check may require of a price (post-reservation amounts).
AffordResource = Literal["minerals", "vespene", "supply"]
AFFORD_RESOURCES: Final[tuple[AffordResource, ...]] = get_args(AffordResource)


@dataclass(frozen=True)
class ArgSpec:
    """Typed argument declaration. ``param`` lets a numeric arg cite a policy parameter.

    ``tracks`` marks a ``node`` argument whose runtime memory the operation reads
    (:meth:`RuntimeView.visits` or :meth:`RuntimeView.demoted`); the runtime keeps
    that memory for exactly the nodes such arguments name (:func:`tracked_nodes`).
    """

    type: ArgType
    required: bool = True
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    default: JsonValue = None
    param: bool = False
    tracks: TrackedMemory | None = None


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
    # Gas (Jev v2): any race's gas extraction structure; gas left in a geyser or one.
    "gas_building": lambda e: e.type_name in GAS_STRUCTURE_TYPES,
    "has_vespene": lambda e: e.vespene_contents > 0,
    # Weapons (Jev v2 Step 218): SC2's targets for the entity's weapons.
    "can_attack_ground": lambda e: e.can_attack_ground,
    "can_attack_air": lambda e: e.can_attack_air,
}
#: Boolean filter keys that also read the evaluation's view, the same way.
VIEW_FILTER_ATTRIBUTES: Final[Mapping[str, Callable[[Entity, RuntimeView], bool]]] = {
    # A worker of the gas lane anywhere in its gas cycle (:func:`is_gas_worker`): a
    # builder, scout or gas-candidate selection excludes it (D3 single ownership).
    "gathering_gas": lambda e, view: is_gas_worker(e, view),
}
_BOOL_FILTER_KEYS: Final = (*BOOL_FILTER_ATTRIBUTES, *VIEW_FILTER_ATTRIBUTES)
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
        reader = BOOL_FILTER_ATTRIBUTES.get(key)
        actual = reader(entity) if reader is not None else VIEW_FILTER_ATTRIBUTES[key](entity, view)
        if actual != wanted:
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
#: validator's choices and the evaluator. Minerals, vespene and free supply are
#: post-reservation.
RESOURCE_READERS: Final[Mapping[str, Callable[[RuntimeView], int]]] = {
    "minerals": lambda view: view.available_minerals(),
    "supply_free": lambda view: view.available_supply(),
    "supply_used": lambda view: view.observation.supply_used,
    "supply_cap": lambda view: view.observation.supply_cap,
    "vespene": lambda view: view.available_vespene(),
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


#: Post-reservation amount of each resource an ``affordable`` check reads.
_AFFORD_READERS: Final[Mapping[str, Callable[[RuntimeView], int]]] = {
    "minerals": lambda view: view.available_minerals(),
    "vespene": lambda view: view.available_vespene(),
    "supply": lambda view: view.available_supply(),
}


def _price(item: str, resource: str) -> int:
    """``item``'s price in ``resource`` from the central tables (supply: 0 unless a unit)."""
    if resource == "minerals":
        return MINERAL_COST[item]
    if resource == "vespene":
        return VESPENE_COST[item]
    return SUPPLY_COST.get(item, 0)


def _affordable(view: RuntimeView, args: Args) -> OpOutcome:
    """Whether ``item``'s price in each named resource -- from the central cost tables,
    never spelled in a policy -- is available after in-flight commitments and this
    tick's holds (a plan lane's own hold counts as its own). Failure is a plan's
    ``budget_short``."""
    item = _arg_str(args, "item")
    short: list[str] = []
    facts: dict[str, JsonValue] = {"item": item}
    for resource in _str_list(args["resources"]):
        need = _price(item, resource)
        have = _AFFORD_READERS[resource](view)
        facts[resource] = {"need": need, "available": have}
        if need > have:
            short.append(resource)
    facts["short"] = list(short)
    if short:
        return OpOutcome(
            False, f"{item} unaffordable: short of {', '.join(short)}", facts, "budget_short"
        )
    return OpOutcome(True, f"{item} affordable", facts)


@dataclass(frozen=True)
class SupplyPlan:
    """The supply plan of one evaluation (see :func:`planned_supply`)."""

    available: int  # free supply after in-flight training and this tick's holds
    pending: int  # supply of supply structures warping in, ordered or carried
    cap: int  # the cap once they complete, at most MAX_SUPPLY
    demand: int  # one next unit for every ready (powered) producer
    free: int  # the planned free supply: available + counted pending - demand

    def facts(self) -> dict[str, JsonValue]:
        return {
            "available": self.available,
            "pending": self.pending,
            "planned_cap": self.cap,
            "demand": self.demand,
            "planned_free": self.free,
        }


def planned_supply(view: RuntimeView, *, producer_demand: bool) -> SupplyPlan:
    """Plan D3 supply planning, from observed state and the runtime's commitments.

    * ``available`` -- :meth:`RuntimeView.available_supply`: free supply minus the
      supply of training commands not yet confirmed (SC2 counts queued training in
      ``supply_used`` once confirmed) and this tick's reservations;
    * ``pending`` -- the supply :data:`SUPPLY_PROVIDED` structures will add:
      every own one still warping in, every build task not yet confirmed by its
      structure (:meth:`RuntimeView.in_flight_builds`), and a worker still carrying
      such a build order no task accounts for (its late placement still counts);
    * ``cap`` -- the observed cap plus ``pending``, at most :data:`MAX_SUPPLY`;
    * ``demand`` -- with ``producer_demand``, the supply of one next unit for every
      ready producer (powered, where power is required), so supply is planned a
      production cycle ahead instead of after every producer is blocked.

    ``free = available + (cap - observed cap) - demand``. Each pending structure is
    counted once, so repeated ticks never plan a second structure for a deficit
    one already covers.
    """
    obs = view.observation
    available = view.available_supply()
    warping = sum(
        SUPPLY_PROVIDED[s.type_name]
        for s in obs.own_structures
        if s.type_name in SUPPLY_PROVIDED and s.build_progress < 1.0
    )
    ordered = [(kind, site) for kind, site in view.in_flight_builds() if kind in SUPPLY_PROVIDED]
    carried = 0
    for unit in obs.own_units:
        order = unit.orders[0] if unit.orders else None
        kind = _SUPPLY_BUILDS.get(order.ability) if order is not None else None
        if kind is None or order is None or not isinstance(order.target, tuple):
            continue
        site = order.target
        if any(k == kind and distance(s, site) <= POINT_MATCH_TOLERANCE for k, s in ordered):
            continue  # its task accounts for it
        if any(
            s.type_name == kind and distance(s.position, site) <= POINT_MATCH_TOLERANCE
            for s in obs.own_structures
        ):
            continue  # already placed (counted as warping in or complete)
        carried += SUPPLY_PROVIDED[kind]
    pending = warping + sum(SUPPLY_PROVIDED[kind] for kind, _ in ordered) + carried
    cap = min(MAX_SUPPLY, obs.supply_cap + pending)
    demand = 0
    if producer_demand:
        demand = sum(
            _NEXT_UNIT_SUPPLY[s.type_name]
            for s in obs.own_structures
            if s.type_name in _NEXT_UNIT_SUPPLY
            and s.is_ready
            and (s.is_powered or s.type_name not in REQUIRES_POWER)
        )
    free = available + max(0, cap - obs.supply_cap) - demand
    return SupplyPlan(available, pending, cap, demand, free)


def _planned_supply_compare(view: RuntimeView, args: Args) -> OpOutcome:
    measure = _arg_str(args, "measure")
    op = _arg_str(args, "op")
    expected = _arg_int(args, "value")
    plan = planned_supply(view, producer_demand=_arg_bool(args, "producer_demand"))
    actual = plan.free if measure == "free" else MAX_SUPPLY - plan.cap
    ok = compare(actual, op, expected)
    verdict = "holds" if ok else "does not hold"
    facts: dict[str, JsonValue] = {
        "measure": measure,
        "actual": actual,
        "op": op,
        "value": expected,
        **plan.facts(),
    }
    return OpOutcome(ok, f"planned supply {measure} {actual} {op} {expected} {verdict}", facts)


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


def _recent_task_count_compare(view: RuntimeView, args: Args) -> OpOutcome:
    node_id = _arg_str(args, "node")
    statuses = frozenset(_str_list(args["statuses"]))
    within = _arg_num(args, "within")
    op = _arg_str(args, "op")
    expected = _arg_int(args, "value")
    count = view.recent_task_count(node_id, statuses, within)
    ok = compare(count, op, expected)
    verdict = "holds" if ok else "does not hold"
    return OpOutcome(
        ok,
        f"tasks of {node_id} {sorted(statuses)} in the last {within:g}s: "
        f"{count} {op} {expected} {verdict}",
        {"node": node_id, "count": count, "within": within, "op": op, "value": expected},
    )


def _visit_count_compare(view: RuntimeView, args: Args) -> OpOutcome:
    node_id = _arg_str(args, "node")
    point = resolve_point(view, _arg_str(args, "point"))
    op = _arg_str(args, "op")
    expected = _arg_int(args, "value")
    if point is None:
        return OpOutcome(False, "visit point unresolved", {"node": node_id})
    visit = view.visits(node_id).get(point)
    count = 0 if visit is None else visit.count
    ok = compare(count, op, expected)
    verdict = "holds" if ok else "does not hold"
    reason = f"visits of {node_id} at {format_point(point)}: {count} {op} {expected} {verdict}"
    facts: dict[str, JsonValue] = {
        "node": node_id,
        "point": [point[0], point[1]],
        "count": count,
        "op": op,
        "value": expected,
    }
    return OpOutcome(ok, reason, facts)


def _army_mode_is(view: RuntimeView, args: Args) -> OpOutcome:
    wanted = _arg_str(args, "mode")
    mode = view.army_mode()
    # A missing usable answer restores the original selector and pre-launch rally.
    ok = (mode == wanted) if mode is not None else (
        wanted != "regroup" or not view.latch_is_set("attack_launched")
    )
    return OpOutcome(ok, f"army mode {mode or 'scripted'}; branch {wanted}",
                     {"mode": mode, "branch": wanted})


def _latch_is_set(view: RuntimeView, args: Args) -> OpOutcome:
    latch = _arg_str(args, "latch")
    ok = view.latch_is_set(latch)
    return OpOutcome(ok, f"latch {latch} is {'set' if ok else 'clear'}", {"latch": latch})


def _latch_is_clear(view: RuntimeView, args: Args) -> OpOutcome:
    latch = _arg_str(args, "latch")
    ok = not view.latch_is_set(latch)
    return OpOutcome(ok, f"latch {latch} is {'clear' if ok else 'set'}", {"latch": latch})


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


def _sighting_count_compare(view: RuntimeView, args: Args) -> OpOutcome:
    """Count the enemy sightings matching the node's filter, seen at most ``max_age``
    game seconds ago (and visible now / only remembered, per ``visibility``).

    The filter reads each sighting's entity *as it was last seen*: a ``within``
    radius measures the last-seen position, never where the entity is now. Facts
    carry the count split by visibility and the newest/oldest sighting age (the
    safety facts plan choices read). One pass over the bounded memory.
    """
    filt = view.node_filter()
    max_age = _arg_num(args, "max_age")
    visibility = _arg_str(args, "visibility")
    op = _arg_str(args, "op")
    expected = _arg_int(args, "value")
    now = view.observation.game_seconds
    ages: list[float] = []
    visible = 0
    for sighting in view.observation.enemy_sightings:
        age = sighting.age(now)
        if age > max_age:
            continue
        if (visibility == "visible" and not sighting.visible) or (
            visibility == "remembered" and sighting.visible
        ):
            continue
        if not entity_matches(sighting.entity, filt, view):
            continue
        ages.append(age)
        if sighting.visible:
            visible += 1
    count = len(ages)
    ok = compare(count, op, expected)
    verdict = "holds" if ok else "does not hold"
    return OpOutcome(
        ok,
        f"{count} enemy sighting(s) within {max_age:g}s ({visibility}) {op} {expected} {verdict}",
        {
            "count": count,
            "visible": visible,
            "remembered": count - visible,
            "newest_age": round(min(ages), 2) if ages else None,
            "oldest_age": round(max(ages), 2) if ages else None,
            "max_age": max_age,
            "visibility": visibility,
            "op": op,
            "value": expected,
        },
    )


def _assignment_age_compare(view: RuntimeView, args: Args) -> OpOutcome:
    """Compare the game seconds since an action node last took charge of a new
    member; a node that never did compares as infinitely long ago."""
    node_id = _arg_str(args, "node")
    op = _arg_str(args, "op")
    seconds = _arg_num(args, "seconds")
    age = view.assignment_age(node_id)
    ok = compare(math.inf if age is None else age, op, seconds)
    verdict = "holds" if ok else "does not hold"
    shown = "never" if age is None else f"{age:.2f}s ago"
    return OpOutcome(
        ok,
        f"{node_id} last took charge of a new member {shown}; {op} {seconds:g}s {verdict}",
        {
            "node": node_id,
            "age": None if age is None else round(age, 2),
            "op": op,
            "seconds": seconds,
        },
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
    facts: dict[str, JsonValue] = {}
    assigned_to = args.get("assigned_to")
    if isinstance(assigned_to, str):
        members = view.assigned(assigned_to)
        candidates = [e for e in candidates if e.tag in members]
    not_assigned_to = args.get("not_assigned_to")
    if isinstance(not_assigned_to, str):
        members = view.assigned(not_assigned_to)
        kept = [e for e in candidates if e.tag not in members]
        facts["assigned_elsewhere"] = len(candidates) - len(kept)
        candidates = kept
    skip_by = args.get("skip_demoted_by")
    if isinstance(skip_by, str):
        demoted = view.demoted(skip_by)
        kept = [e for e in candidates if e.tag not in demoted]
        facts["demoted"] = len(candidates) - len(kept)
        candidates = kept
    chosen = _sort_entities(candidates, _arg_obj(args, "sort"), view)[:limit]
    facts.update({"matched": len(candidates), "selected": len(chosen)})
    if len(chosen) < min_count:
        return Selection(None, f"{len(chosen)} {collection} selected, need {min_count}", facts)
    binding = Binding("units", entities=tuple(chosen))
    facts["tags"] = binding.describe()
    return Selection(binding, f"selected {len(chosen)} from {collection}", facts)


def _select_locations(view: RuntimeView, args: Args) -> Selection:
    source = _location_source(_arg_str(args, "source"))
    limit = _arg_int(args, "limit")
    min_count = _arg_int(args, "min_count")
    ordered = _sort_points(view.observation.locations(source), _arg_obj(args, "sort"), view)
    facts: dict[str, JsonValue] = {}
    visited_by = args.get("least_recently_visited_by")
    if isinstance(visited_by, str):
        # Never-visited locations first, then the oldest visit; the sort is stable,
        # so the authored order breaks ties and repeated selection cycles through it.
        visits = view.visits(visited_by)
        ordered = sorted(ordered, key=lambda point: _visit_time(visits.get(point)))
        facts["visited"] = sum(1 for point in ordered if point in visits)
    points = ordered[:limit]
    facts["selected"] = len(points)
    if len(points) < min_count:
        return Selection(None, f"{len(points)} {source} available, need {min_count}", facts)
    binding = Binding("points", points=tuple(points))
    facts["points"] = binding.describe()
    return Selection(binding, f"selected {len(points)} from {source}", facts)


def _visit_time(visit: Visit | None) -> float:
    return -math.inf if visit is None else visit.last_game_seconds


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


def _footprint(type_name: str) -> int:
    """THE footprint edge of a structure type for placement screening: a gas
    structure's is its geyser's 3x3 (standing or ordered alike), a footprinted type's
    its own, anything else :data:`DEFAULT_FOOTPRINT`."""
    if type_name in GAS_STRUCTURE_TYPES:
        return GAS_FOOTPRINT
    return FOOTPRINT.get(type_name, DEFAULT_FOOTPRINT)


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
    in-flight builds, mineral clearance, Pylon power) plus the task-state
    exclusion of :meth:`RuntimeView.rejected_sites`; the SC2 adapter tests real
    placement legality. Sorted by distance from ``near``, then x, then y.
    """
    obs = view.observation
    size = FOOTPRINT[structure]
    offset = 0.5 if size % 2 else 0.0
    reach = radius_max + 6.0
    enemy_structures = tuple(e for e in obs.visible_enemies if e.is_structure)
    blockers: list[tuple[Point, float]] = [
        (e.position, float(_footprint(e.type_name)))
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
    rejected = view.rejected_sites(structure)
    base_x = math.floor(near[0]) + offset
    base_y = math.floor(near[1]) + offset
    span = math.ceil(radius_max) + 1
    found: list[tuple[float, Point]] = []
    for dx in range(-span, span + 1):
        for dy in range(-span, span + 1):
            point = (base_x + dx, base_y + dy)
            d = distance(point, near)
            if d < radius_min or d > radius_max or point in rejected:
                continue
            if any(_overlaps(point, size, center, other) for center, other in blockers):
                continue
            if any(
                _overlaps(point, size, site, _footprint(kind))
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


def _visible_enemy(tag: int, obs: Observation) -> Entity | None:
    """The visible enemy with ``tag``, or None (one linear pass over visible enemies)."""
    for enemy in obs.visible_enemies:
        if enemy.tag == tag:
            return enemy
    return None


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
    load = _patch_loads(view, patches)
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
        patch = _least_loaded_patch(patches, load, per_patch, worker)
        load[patch.tag] += 1
        intents.append(Intent(worker.tag, semantic, GATHER_ABILITY, patch.tag))
    return ActionPlan(
        intents=tuple(intents), blocked=tuple(blocked), facts={"per_patch": per_patch}
    )


def _patch_loads(view: RuntimeView, patches: Sequence[Entity]) -> Counter[int]:
    """Workers per bound patch: visible gather orders on it plus unacknowledged gathers."""
    patch_tags = {p.tag for p in patches}
    load: Counter[int] = Counter()
    for unit in view.observation.own_units:
        order = unit.orders[0] if unit.orders else None
        if order and order.ability == GATHER_ABILITY and isinstance(order.target, int):
            if order.target in patch_tags:
                load[order.target] += 1
    for patch_tag, count in view.gather_assignments().items():
        load[patch_tag] += count
    return load


def _least_loaded_patch(
    patches: Sequence[Entity], load: Counter[int], per_patch: int, worker: Entity
) -> Entity:
    """Below ``per_patch`` first, then least loaded, then nearest ``worker``, then tag."""
    return min(
        patches,
        key=lambda p: (
            load[p.tag] >= per_patch,
            load[p.tag],
            distance(worker.position, p.position),
            p.tag,
        ),
    )


def _missing_requirement(view: RuntimeView, kind: str, *, powered: bool = False) -> str | None:
    """The :data:`TECH_REQUIREMENT` of ``kind`` when no own structure of that type is
    ready in this observation (a live prerequisite, never remembered); else None.

    SC2 counts build progress only. With ``powered`` (a train node's opt-in
    ``powered_prerequisite``) the structure must also have Pylon power when its type
    needs it (:data:`REQUIRES_POWER`): a stricter, operational prerequisite."""
    required = TECH_REQUIREMENT.get(kind)
    if required is None:
        return None
    for structure in view.observation.own_structures:
        if structure.type_name != required or not structure.is_ready:
            continue
        if not powered or required not in REQUIRES_POWER or structure.is_powered:
            return None
    return required


def planned_unit_count(view: RuntimeView, unit: str) -> int:
    """THE planned count of ``unit`` for composition guards: own units of the type,
    plus queued train orders for it on own structures, plus train tasks not yet
    confirmed (:meth:`RuntimeView.in_flight_trains`). A confirmed task is a queued
    order, so nothing is counted twice."""
    obs = view.observation
    ability = TRAIN_ABILITY[unit]
    standing = sum(1 for u in obs.own_units if u.type_name == unit)
    queued = sum(1 for s in obs.own_structures for order in s.orders if order.ability == ability)
    return standing + queued + view.in_flight_trains().get(unit, 0)


def _build(view: RuntimeView, args: Args) -> ActionPlan:
    structure = _arg_str(args, "structure")
    bound_workers = _binding(view, _arg_str(args, "worker")).entities
    workers, blocked = _own_actors(view, bound_workers, structures=False, worker=True)
    sites = _binding(view, _arg_str(args, "site")).points[:MAX_PLACEMENT_CANDIDATES]
    hold = _arg_bool(args, "hold_reservation")
    if not workers or not sites:
        reason = blocked[0][1] if blocked else "worker or site binding empty"
        return ActionPlan(failure_reason=reason, hold_reservation=hold)
    required = _missing_requirement(view, structure)
    if required is not None:
        return ActionPlan(
            failure_reason=f"{structure} requires a ready {required}",
            hold_reservation=hold,
            facts={"structure": structure, "requires": required},
            code="prerequisite_missing",
        )
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
        vespene=VESPENE_COST[structure],
    )
    return ActionPlan(
        intents=(intent,),
        hold_reservation=hold,
        facts={"structure": structure, "worker": str(worker.tag), "site": [site[0], site[1]]},
    )


def _composition_allowance(
    view: RuntimeView, args: Args, unit: str, facts: dict[str, JsonValue]
) -> int | None:
    """How many more ``unit`` the node's composition guards allow (None: no guard).

    ``max_count`` caps the planned count; ``max_ratio`` caps it at that many times
    the planned count of ``ratio_of`` (rounded down), so a 1.0 ratio keeps Stalkers
    at most as many as Zealots. Both read :func:`planned_unit_count`, and the facts
    show the counts and the bound."""
    max_count = args.get("max_count")
    ratio_of = args.get("ratio_of")
    max_ratio = args.get("max_ratio")
    bounds: list[int] = []
    if isinstance(max_count, int) and not isinstance(max_count, bool):
        bounds.append(max_count)
        facts["max_count"] = max_count
    if isinstance(ratio_of, str) and isinstance(max_ratio, int | float):
        other = planned_unit_count(view, ratio_of)
        bounds.append(math.floor(float(max_ratio) * other + 1e-9))
        facts.update({"ratio_of": ratio_of, "max_ratio": max_ratio, "planned_other": other})
    if not bounds:
        return None
    planned = planned_unit_count(view, unit)
    bound = min(bounds)
    facts.update({"planned": planned, "composition_bound": bound})
    return max(0, bound - planned)


def _train(view: RuntimeView, args: Args) -> ActionPlan:
    """One ``unit`` from each bound idle producer of its type: never one that is not
    ready or that SC2 reports unpowered, never while the unit's tech requirement
    (:data:`TECH_REQUIREMENT`) is not ready, and -- with composition guards -- never
    beyond what they allow this evaluation (each new intent counts toward the bound;
    an intent for a producer already held by a task adds nothing)."""
    unit = _arg_str(args, "unit")
    bound = sorted(_binding(view, _arg_str(args, "producers")).entities, key=lambda p: p.tag)
    producers, blocked = _own_actors(view, bound, structures=True)
    hold = _arg_bool(args, "hold_reservation")
    existing = sum(1 for u in view.observation.own_units if u.type_name == unit)
    facts: dict[str, JsonValue] = {"unit": unit}
    powered = _arg_bool(args, "powered_prerequisite")
    required = _missing_requirement(view, unit, powered=powered)
    if required is not None:
        facts["requires"] = required
        state = "ready, powered" if powered and required in REQUIRES_POWER else "ready"
        reason = f"{unit} requires a {state} {required}"
        blocked.extend((producer.tag, reason) for producer in producers)
        return ActionPlan(
            blocked=tuple(blocked),
            failure_reason=reason,
            hold_reservation=hold,
            facts=facts,
            code="prerequisite_missing",
        )
    allowance = _composition_allowance(view, args, unit, facts)
    intents: list[Intent] = []
    for producer in producers:
        held = view.actor_is_busy(producer.tag)
        if producer.type_name != PRODUCER_TYPE[unit]:
            blocked.append((producer.tag, f"{producer.type_name} cannot train {unit}"))
        elif not producer.is_ready:
            blocked.append((producer.tag, "producer not ready"))
        elif producer.powered is False and producer.type_name in REQUIRES_POWER:
            blocked.append((producer.tag, "producer unpowered"))
        elif not producer.is_idle and not held:
            blocked.append((producer.tag, "producer busy"))
        elif allowance is not None and not held and allowance <= 0:
            blocked.append((producer.tag, f"composition guard: no more {unit} allowed now"))
        else:
            if allowance is not None and not held:
                allowance -= 1  # this intent may become a new task: it counts
            intents.append(
                Intent(
                    actor_tag=producer.tag,
                    semantic=str(producer.tag),
                    ability=TRAIN_ABILITY[unit],
                    target=None,
                    minerals=MINERAL_COST[unit],
                    supply=SUPPLY_COST[unit],
                    params={PARAM_UNIT: unit, PARAM_UNIT_COUNT: existing},
                    vespene=VESPENE_COST[unit],
                )
            )
    return ActionPlan(
        intents=tuple(intents),
        blocked=tuple(blocked),
        hold_reservation=hold,
        facts=facts,
    )


def _unit_command(view: RuntimeView, args: Args, ability: str) -> ActionPlan:
    units = _binding(view, _arg_str(args, "units")).entities
    target_binding = _binding(view, _arg_str(args, "target"))
    arrive_within = _arg_num(args, "arrive_within")
    preempt = _arg_bool(args, "preempt")
    target: Target
    target_key: str
    params: dict[str, JsonValue] = {PARAM_ARRIVE_WITHIN: arrive_within}
    air_target: int | None = None  # a visible flying enemy: only anti-air actors may take it
    if target_binding.kind == "units" and ability == ATTACK_ABILITY:
        if not target_binding.entities:
            return ActionPlan(failure_reason="target binding empty", preempt=preempt)
        chosen = target_binding.entities[0]
        params[PARAM_TARGET_TAG] = chosen.tag
        seen = _visible_enemy(chosen.tag, view.observation)
        if seen is not None:
            target = chosen.tag
            target_key = f"#{target}"
            if seen.is_flying:
                air_target = seen.tag
        else:
            # SC2 can only target a unit in sight: an entity that is not a visible
            # enemy (a remembered structure) is attack-moved to where it was seen.
            target = chosen.position
            target_key = format_point(target)
    else:
        point = target_binding.first_point()
        if point is None:
            return ActionPlan(failure_reason="target binding empty", preempt=preempt)
        target = point
        target_key = format_point(point)
    actors, blocked = _own_actors(view, sorted(units, key=lambda u: u.tag), structures=False)
    extra: dict[str, JsonValue] = {}
    if air_target is not None:
        # Capability-aware (D2/D4): a unit whose weapons cannot hit air (a Zealot) is
        # never assigned a flying target; it is blocked, visibly, and left alone.
        unable = [unit for unit in actors if not unit.can_attack_air]
        if unable:
            actors = [unit for unit in actors if unit.can_attack_air]
            blocked.extend(
                (unit.tag, f"{unit.type_name} {unit.tag} cannot attack air target {air_target}")
                for unit in unable
            )
            extra["cannot_attack_air"] = len(unable)
    # An attack-move point is done only once no ground enemy holds it (D3: cleared).
    held = (
        isinstance(target, tuple)
        and ability == ATTACK_ABILITY
        and bool(_ground_enemies_near(target, arrive_within, view.observation))
    )
    intents: list[Intent] = []
    for unit in actors:
        if isinstance(target, tuple):
            satisfied = not held and distance(unit.position, target) <= arrive_within
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
                params=params,
            )
        )
    return ActionPlan(
        intents=tuple(intents),
        blocked=tuple(blocked),
        preempt=preempt,
        facts={"target": target_key, "units": len(units), **extra},
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
# Gas actions (Jev v2, plan J2 Step 216)
# ---------------------------------------------------------------------------


def gas_slots(building: Entity, per_building: int) -> int:
    """How many workers gas building ``building`` may hold (plan D4 saturation).

    None while it is unfinished or once it is depleted; otherwise ``per_building``,
    never above SC2's ideal harvester count when SC2 reports one.
    """
    if not building.is_ready or building.vespene_contents <= 0:
        return 0
    if building.ideal_harvesters > 0:
        return min(per_building, building.ideal_harvesters)
    return per_building


def _gather_target(unit: Entity) -> int | None:
    """The tag a unit's current gather order targets, or None."""
    order = unit.orders[0] if unit.orders else None
    if order is not None and order.ability == GATHER_ABILITY and isinstance(order.target, int):
        return order.target
    return None


def is_gas_worker(unit: Entity, view: RuntimeView) -> bool:
    """THE gas-worker identity (D3): ``unit`` is in a gas cycle -- carrying vespene
    back (SC2's carry buff; its return order targets the Nexus, never the gas
    building), or ordered to gather from an own structure. SC2 gathers only from
    resources, and the only own structure that is one is a gas building."""
    return unit.carrying_vespene or _gather_target(unit) in view.own_structure_tags()


def _gas_structures_at(entities: Sequence[Entity], site: Point) -> list[Entity]:
    """Gas structures standing on ``site`` (a geyser's position)."""
    return [
        e
        for e in entities
        if e.type_name in GAS_STRUCTURE_TYPES
        and distance(e.position, site) <= POINT_MATCH_TOLERANCE
    ]


def _own_gas_buildings(
    view: RuntimeView, entities: Sequence[Entity]
) -> tuple[list[Entity], list[tuple[int, str]]]:
    """Split bound entities into own gas buildings and visibly blocked others (by tag,
    each once): a binding may hold anything, and only own gas buildings are worked."""
    owned = view.own_structure_tags()
    buildings: dict[int, Entity] = {}
    blocked: list[tuple[int, str]] = []
    for entity in entities:
        if entity.tag not in owned or entity.type_name not in GAS_STRUCTURE_TYPES:
            blocked.append((entity.tag, f"entity {entity.tag} is not an own gas building"))
        else:
            buildings.setdefault(entity.tag, entity)
    return [buildings[tag] for tag in sorted(buildings)], blocked


@dataclass(frozen=True)
class _GasBuilding:
    """One gas building's saturation this evaluation.

    ``load`` is the workers it holds: SC2's assigned count (which includes a worker
    inside the building, out of sight) or, when more, the visible workers ordered to
    gather from it, plus unacknowledged gather tasks to it. ``gatherers`` are those
    visible workers -- the only ones a command can reach.
    """

    entity: Entity
    slots: int
    load: int
    gatherers: tuple[Entity, ...]


def _gas_state(
    view: RuntimeView, buildings: Sequence[Entity], per_building: int
) -> list[_GasBuilding]:
    gatherers: dict[int, list[Entity]] = {b.tag: [] for b in buildings}
    for unit in view.observation.own_units:
        target = _gather_target(unit)
        if target is not None and target in gatherers and unit.type_name == WORKER_TYPE:
            gatherers[target].append(unit)
    in_flight = view.gather_assignments()
    return [
        _GasBuilding(
            entity=b,
            slots=gas_slots(b, per_building),
            load=max(b.assigned_harvesters, len(gatherers[b.tag])) + in_flight.get(b.tag, 0),
            gatherers=tuple(gatherers[b.tag]),
        )
        for b in buildings
    ]


def _gas_facts(state: Sequence[_GasBuilding], per_building: int) -> dict[str, JsonValue]:
    """Per building (by tag): the workers it holds and the slots it may hold."""
    buildings: dict[str, JsonValue] = {}
    for b in state:
        buildings[str(b.entity.tag)] = {"workers": b.load, "slots": b.slots}
    return {"per_building": per_building, "buildings": buildings}


def _nearest(entities: Sequence[Entity], point: Point) -> Entity:
    return min(entities, key=lambda e: (distance(e.position, point), e.tag))


def _build_gas(view: RuntimeView, args: Args) -> ActionPlan:
    """A gas structure on the first bound geyser that is free: a visible vespene geyser
    (by tag, in this observation) with gas left, no gas structure on it (own or
    enemy, visible or remembered), no gas build already ordered there and no recent
    refusal there. The command targets the geyser's *tag*: never a point."""
    structure = _arg_str(args, "structure")
    bound_workers = _binding(view, _arg_str(args, "worker")).entities
    workers, blocked = _own_actors(view, bound_workers, structures=False, worker=True)
    bound = _binding(view, _arg_str(args, "geyser")).entities[:MAX_PLACEMENT_CANDIDATES]
    hold = _arg_bool(args, "hold_reservation")
    if not workers or not bound:
        reason = blocked[0][1] if blocked else "worker or geyser binding empty"
        return ActionPlan(failure_reason=reason, hold_reservation=hold)
    obs = view.observation
    visible = {g.tag: g for g in obs.vespene_geysers}
    standing = (*obs.own_structures, *obs.visible_enemies, *obs.remembered_enemy_structures)
    ordered = [site for kind, site in view.in_flight_builds() if kind in GAS_BUILD_ABILITY]
    refused = view.rejected_sites(structure)
    skipped: Counter[str] = Counter()
    chosen: Entity | None = None
    for candidate in bound:
        geyser = visible.get(candidate.tag)
        if geyser is None:
            skipped["not_a_visible_geyser"] += 1
        elif geyser.vespene_contents <= 0:
            skipped["depleted"] += 1
        elif _gas_structures_at(standing, geyser.position):
            skipped["taken"] += 1
        elif any(distance(site, geyser.position) <= POINT_MATCH_TOLERANCE for site in ordered):
            skipped["ordered"] += 1
        elif any(distance(site, geyser.position) <= POINT_MATCH_TOLERANCE for site in refused):
            skipped["refused"] += 1
        else:
            chosen = geyser
            break
    skipped_counts: dict[str, JsonValue] = {key: skipped[key] for key in sorted(skipped)}
    facts: dict[str, JsonValue] = {"structure": structure, "skipped": skipped_counts}
    if chosen is None:
        return ActionPlan(
            failure_reason=f"no free vespene geyser among {len(bound)} bound",
            hold_reservation=hold,
            facts=facts,
        )
    worker = workers[0]
    site = chosen.position
    intent = Intent(
        actor_tag=worker.tag,
        semantic=f"{structure}#{chosen.tag}",
        ability=GAS_BUILD_ABILITY[structure],
        target=chosen.tag,
        minerals=MINERAL_COST[structure],
        params={PARAM_STRUCTURE: structure, PARAM_SITE: [site[0], site[1]]},
        vespene=VESPENE_COST[structure],
    )
    facts.update(
        {
            "worker": str(worker.tag),
            "geyser": str(chosen.tag),
            "site": [site[0], site[1]],
            "minerals": intent.minerals,
            "vespene": intent.vespene,
        }
    )
    return ActionPlan(intents=(intent,), hold_reservation=hold, facts=facts)


def _gather_gas(view: RuntimeView, args: Args) -> ActionPlan:
    """Fill each bound own gas building's open slots (:func:`gas_slots`) with the
    nearest bound workers not already gathering gas and not held by a task."""
    bound_workers = _binding(view, _arg_str(args, "workers")).entities
    workers, blocked = _own_actors(view, bound_workers, structures=False, worker=True)
    bound = _binding(view, _arg_str(args, "buildings")).entities
    buildings, refused = _own_gas_buildings(view, bound)
    blocked.extend(refused)
    per_building = _arg_int(args, "per_building")
    state = _gas_state(view, buildings, per_building)
    facts = _gas_facts(state, per_building)
    if not state:
        return ActionPlan(
            failure_reason="no own gas building bound", blocked=tuple(blocked), facts=facts
        )
    free = [
        w
        for w in sorted(workers, key=lambda w: w.tag)
        if not is_gas_worker(w, view) and not view.actor_is_busy(w.tag)
    ]
    intents: list[Intent] = []
    open_slots = 0
    for building in state:
        deficit = building.slots - building.load
        while deficit > 0 and free:
            worker = _nearest(free, building.entity.position)
            free.remove(worker)
            intents.append(Intent(worker.tag, str(worker.tag), GATHER_ABILITY, building.entity.tag))
            deficit -= 1
        open_slots += max(0, deficit)
    facts.update({"open_slots": open_slots, "assigned": len(intents)})
    if intents:
        return ActionPlan(intents=tuple(intents), blocked=tuple(blocked), facts=facts)
    if open_slots:
        reason = f"no free worker for {open_slots} open gas slot(s)"
    else:
        reason = "every bound gas building holds its allowed workers"
    return ActionPlan(failure_reason=reason, blocked=tuple(blocked), facts=facts)


def _release_gas(view: RuntimeView, args: Args) -> ActionPlan:
    """Send each bound own gas building's surplus -- workers beyond its slots, every
    one once it is depleted -- to the least-loaded bound mineral patch, farthest
    from the building first (not yet inside it)."""
    bound = _binding(view, _arg_str(args, "buildings")).entities
    buildings, blocked = _own_gas_buildings(view, bound)
    mineral_tags = {m.tag for m in view.observation.mineral_fields}
    patches = [
        p for p in _binding(view, _arg_str(args, "minerals")).entities if p.tag in mineral_tags
    ]
    per_building = _arg_int(args, "per_building")
    per_patch = _arg_int(args, "per_patch")
    state = _gas_state(view, buildings, per_building)
    facts = _gas_facts(state, per_building)
    surplus = sum(max(0, b.load - b.slots) for b in state)
    facts["surplus"] = surplus
    if not state:
        return ActionPlan(
            failure_reason="no own gas building bound", blocked=tuple(blocked), facts=facts
        )
    if not surplus:
        return ActionPlan(
            failure_reason="no gas building holds surplus workers",
            blocked=tuple(blocked),
            facts=facts,
        )
    if not patches:
        return ActionPlan(failure_reason="no mineral patch bound", blocked=tuple(blocked))
    load = _patch_loads(view, patches)
    intents: list[Intent] = []
    for building in state:
        excess = building.load - building.slots
        if excess <= 0:
            continue
        site = building.entity.position
        movable = sorted(
            (w for w in building.gatherers if not view.actor_is_busy(w.tag)),
            key=lambda w: (-distance(w.position, site), w.tag),
        )
        for worker in movable[:excess]:
            patch = _least_loaded_patch(patches, load, per_patch, worker)
            load[patch.tag] += 1
            intents.append(Intent(worker.tag, str(worker.tag), GATHER_ABILITY, patch.tag))
    facts["released"] = len(intents)
    if intents:
        return ActionPlan(intents=tuple(intents), blocked=tuple(blocked), facts=facts)
    return ActionPlan(
        failure_reason=f"{surplus} surplus gas worker(s) out of sight or held by a task",
        blocked=tuple(blocked),
        facts=facts,
    )


# ---------------------------------------------------------------------------
# Persistent plans and the natural expansion (Jev v2, plan J2 Step 217)
# ---------------------------------------------------------------------------


def _plan_kind(name: str) -> PlanKind:
    for kind in PLAN_KINDS:
        if kind == name:
            return kind
    raise ValueError(f"unknown plan kind {safe_repr(name)}")


def _open_plan(view: RuntimeView, args: Args) -> tuple[str, PlanRecord | None]:
    """The plan a node's ``plan`` argument names, if it is open."""
    key = _arg_str(args, "plan")
    record = view.plan(key)
    return key, record if record is not None and record.is_open else None


def _plan_status_is(view: RuntimeView, args: Args) -> OpOutcome:
    key = _arg_str(args, "plan")
    statuses = frozenset(_str_list(args["statuses"]))
    record = view.plan(key)
    if record is None:
        return OpOutcome(False, f"{key} has started no plan", {"plan": key, "plan_status": None})
    ok = record.status in statuses
    verdict = "is" if ok else "is not"
    return OpOutcome(
        ok,
        f"plan {record.plan_id} is {record.status} at {record.stage_node_id}; "
        f"{verdict} in {sorted(statuses)}",
        {
            "plan": key,
            "plan_id": record.plan_id,
            "plan_status": record.status,
            "stage_node_id": record.stage_node_id,
            "reason_code": record.reason_code,
        },
    )


def expansion_candidates(observation: Observation, *, clearance: float, limit: int) -> list[Point]:
    """THE bounded expansion candidates (D4): the map's expansion locations that are
    neither home nor a known enemy start (more than ``clearance`` from both), nearest
    the start location first by Euclidean distance (then x, then y), at most
    ``limit``. Euclidean order only bounds which candidates are path-queried; it
    never proves one reachable."""
    home = observation.start_location
    enemies = observation.enemy_start_locations
    points = [
        point
        for point in observation.expansion_locations
        if distance(point, home) > clearance
        and all(distance(point, enemy) > clearance for enemy in enemies)
    ]
    points.sort(key=lambda p: (distance(p, home), p[0], p[1]))
    return points[:limit]


def _near_any(point: Point, entities: Sequence[Entity], radius: float) -> bool:
    return any(distance(entity.position, point) <= radius for entity in entities)


def _select_expansion(view: RuntimeView, args: Args) -> Selection:
    """The plan's site: its committed one while no enemy structure stands on it,
    else the nearest *reachable* unoccupied candidate by SC2 path distance --
    bounded (D4, :mod:`jev.plans` "Site").

    Occupied: an own or enemy structure (visible, or remembered and not disproven
    by vision) within ``clearance``. A site SC2 refused, or an earlier plan was
    aborted over, is avoided (the plan's ``avoided_sites``). A candidate whose path
    is not yet known is not eligible: while any is unknown the selection fails
    ``path_pending`` and asks for the queries (one tick), so the nearest by path is
    never chosen early. Bound: the chosen site's path is at most ``max_path_ratio``
    times the nearest reachable candidate's (whatever that one's state), so when
    the natural is unavailable a nearby alternative may be taken but never a far
    base -- the selection fails visibly instead. The choice carries its path
    distance and its rank among all reachable candidates (0: the nearest).
    """
    key, record = _open_plan(view, args)
    if record is None:
        return Selection(None, f"{key} has no open plan", {"plan": key}, code="blocked")
    limit = _arg_int(args, "candidates")
    clearance = _arg_num(args, "clearance")
    ratio = _arg_num(args, "max_path_ratio")
    obs = view.observation
    enemy_structures: tuple[Entity, ...] = (
        *(e for e in obs.visible_enemies if e.is_structure),
        *obs.remembered_enemy_structures,
    )
    committed = record.site
    replaced: PlanCode | None = None
    facts: dict[str, JsonValue] = {"plan_id": record.plan_id}
    if committed is not None and committed not in record.avoided_sites:
        if not _near_any(committed, enemy_structures, clearance):
            facts.update(
                {
                    "site": [committed[0], committed[1]],
                    "committed": True,
                    "path_distance": record.site_path_distance,
                    "path_rank": record.site_path_rank,
                }
            )
            binding = Binding("points", points=(committed,))
            return Selection(binding, f"the plan's site {format_point(committed)}", facts)
        replaced = "site_occupied"  # an enemy took it: a recovery attempt, then re-choose
    candidates = expansion_candidates(obs, clearance=clearance, limit=limit)
    paths = {fact.location: fact.distance for fact in obs.expansion_paths}
    # Every reachable candidate by path, whatever its state: the rank and the bound.
    reachable = sorted(
        (known, point) for point in candidates if (known := paths.get(point)) is not None
    )
    nearest = reachable[0][0] if reachable else None
    refused = view.rejected_sites(EXPANSION_STRUCTURE)
    standing = (*obs.own_structures, *enemy_structures)
    skipped: Counter[str] = Counter()
    pending: list[Point] = []
    eligible: list[tuple[float, Point]] = []
    for point in candidates:
        if point == committed or _near_any(point, standing, clearance):
            skipped["occupied"] += 1
        elif point in refused:
            skipped["refused"] += 1
        elif point in record.avoided_sites:
            skipped["avoided"] += 1
        elif point not in paths:
            pending.append(point)
        else:
            path = paths[point]
            if path is None:
                skipped["unreachable"] += 1
            else:
                eligible.append((path, point))
    eligible.sort(key=lambda item: (item[0], item[1][0], item[1][1]))
    bound = None if nearest is None else nearest * ratio
    in_bound = [item for item in eligible if bound is not None and item[0] <= bound]
    facts.update(
        {
            "candidates": len(candidates),
            "pending": len(pending),
            "eligible": len(eligible),
            "beyond_bound": len(eligible) - len(in_bound),
            "nearest_path": None if nearest is None else round(nearest, 2),
            "path_bound": None if bound is None else round(bound, 2),
            "skipped": {name: skipped[name] for name in sorted(skipped)},
        }
    )
    if pending:
        return Selection(
            None,
            f"waiting for SC2 path distances to {len(pending)} candidate(s)",
            facts,
            code="path_pending",
            queries=tuple(pending),
        )
    if not in_bound:
        code: PlanCode
        if not candidates:
            code, reason = "no_site", "no expansion location besides home and enemy starts"
        elif skipped["unreachable"] == len(candidates):
            code, reason = "site_unreachable", f"no path to any of {len(candidates)} candidate(s)"
        elif skipped["refused"] or skipped["avoided"]:
            code, reason = "site_blocked", "every reachable site near enough is blocked"
        else:
            code, reason = "site_occupied", "every reachable site near enough is occupied"
        if eligible:
            far = eligible[0][0]
            reason += (
                f"; the nearest free one is {far:.1f} by path, beyond {ratio:g}x the nearest "
                "reachable: not taken"
            )
        return Selection(None, reason, facts, code=code)
    path, site = in_bound[0]
    rank = next(index for index, (_, point) in enumerate(reachable) if point == site)
    facts.update(
        {
            "site": [site[0], site[1]],
            "path_distance": round(path, 2),
            "path_rank": rank,
            "alternatives": len(in_bound) - 1,
            "committed": False,
        }
    )
    effect = PlanEffect("site", plan=key, point=site, replaced=replaced, path=path, rank=rank)
    return Selection(
        Binding("points", points=(site,)),
        f"site {format_point(site)}: reachable by path ({path:.1f}, rank {rank})",
        facts,
        effect=effect,
    )


def _plan_site_seen(view: RuntimeView, args: Args) -> OpOutcome:
    """D4 "unknown is not safe": the open plan's site was in the bot's own sight at
    most ``max_age`` game seconds ago (:class:`~jev.contracts.LocationVision`, SC2's
    visibility answer). Never seen, or seen too long ago, fails ``site_unscouted``,
    so a build waits for fresh vision and its safety is rechecked on it."""
    key, record = _open_plan(view, args)
    max_age = _arg_num(args, "max_age")
    if record is None or record.site is None:
        return OpOutcome(False, f"{key} has no open plan site", {"plan": key}, "site_unscouted")
    site = record.site
    obs = view.observation
    vision = next((v for v in obs.location_vision if v.location == site), None)
    seen = None if vision is None else vision.last_seen_game_seconds
    facts: dict[str, JsonValue] = {
        "site": [site[0], site[1]],
        "visible": vision is not None and vision.visible,
        "last_seen_game_seconds": seen,
        "max_age": max_age,
    }
    if seen is None:
        reason = f"site {format_point(site)} never seen: unknown is not safe"
        return OpOutcome(False, reason, facts, "site_unscouted")
    age = max(0.0, obs.game_seconds - seen)
    facts["age"] = round(age, 2)
    if age > max_age:
        reason = f"site {format_point(site)} last seen {age:.1f}s ago: stale, not safe"
        return OpOutcome(False, reason, facts, "site_unscouted")
    return OpOutcome(True, f"site {format_point(site)} in sight {age:.1f}s ago", facts)


def _select_plan_actor(view: RuntimeView, args: Args) -> Selection:
    """The plan's one actor while it lives; else the first own unit matching the
    filter that no task, plan or this tick holds (not one ``not_assigned_to`` names),
    claimed for the plan on success (D3 single ownership)."""
    key, record = _open_plan(view, args)
    if record is None:
        return Selection(None, f"{key} has no open plan", {"plan": key}, code="blocked")
    units = view.observation.own_units
    held = record.actor_tag
    if held is not None:
        for unit in units:
            if unit.tag == held:
                binding = Binding("units", entities=(unit,))
                return Selection(
                    binding, f"the plan's worker {held}", {"tags": [str(held)], "held": True}
                )
    filt = view.node_filter()
    candidates = [
        unit
        for unit in units
        if entity_matches(unit, filt, view) and not view.actor_is_busy(unit.tag)
    ]
    facts: dict[str, JsonValue] = {"held": False}
    not_assigned_to = args.get("not_assigned_to")
    if isinstance(not_assigned_to, str):
        members = view.assigned(not_assigned_to)
        kept = [unit for unit in candidates if unit.tag not in members]
        facts["assigned_elsewhere"] = len(candidates) - len(kept)
        candidates = kept
    chosen = _sort_entities(candidates, _arg_obj(args, "sort"), view)[:1]
    facts["matched"] = len(candidates)
    if not chosen:
        return Selection(None, "no free unit for the plan", facts, code="no_worker")
    unit = chosen[0]
    facts["tags"] = [str(unit.tag)]
    return Selection(
        Binding("units", entities=(unit,)),
        f"claim {unit.tag} for plan {record.plan_id}",
        facts,
        effect=PlanEffect("actor", plan=key, tag=unit.tag),
    )


def _start_plan(view: RuntimeView, args: Args) -> ActionPlan:
    kind = _plan_kind(_arg_str(args, "kind"))
    return ActionPlan(effect=PlanEffect("start", kind=kind), facts={"plan_kind": kind})


def _plan_wait(view: RuntimeView, args: Args) -> ActionPlan:
    key, record = _open_plan(view, args)
    if record is None:
        return ActionPlan(failure_reason=f"{key} has no open plan")
    reason = _arg_str(args, "reason")
    code: PlanCode = "waiting"
    for wait_code in WAIT_CODES:
        if wait_code == reason:
            code = wait_code
    return ActionPlan(
        wait_reason=f"plan {record.plan_id} waits: {reason}",
        code=code,
        facts={"plan_id": record.plan_id, "reason_code": reason},
    )


def _plan_reserve(view: RuntimeView, args: Args) -> ActionPlan:
    """Hold the plan's next structure's cost (D3), up to what is available after the
    lanes before this one: nothing while the plan's build is in flight (its task
    carries the commitment) or once its structure stands. Always succeeds: the
    plan's other stages (its worker's walk) proceed while it saves."""
    key, record = _open_plan(view, args)
    if record is None:
        return ActionPlan(failure_reason=f"{key} has no open plan")
    structure = _arg_str(args, "structure")
    need_minerals, need_vespene = MINERAL_COST[structure], VESPENE_COST[structure]
    ordered = record.build_in_flight or record.acknowledged_game_seconds is not None
    minerals = vespene = 0
    if not ordered:
        minerals = max(0, min(need_minerals, view.available_minerals()))
        vespene = max(0, min(need_vespene, view.available_vespene()))
    facts: dict[str, JsonValue] = {
        "plan_id": record.plan_id,
        "structure": structure,
        "held_minerals": minerals,
        "held_vespene": vespene,
        "need_minerals": need_minerals,
        "need_vespene": need_vespene,
        "committed_to_build": ordered,
    }
    short = not ordered and (minerals < need_minerals or vespene < need_vespene)
    return ActionPlan(
        effect=PlanEffect("reserve", plan=key, minerals=minerals, vespene=vespene),
        facts=facts,
        code="budget_short" if short else None,
    )


def _plan_complete(view: RuntimeView, args: Args) -> ActionPlan:
    key, record = _open_plan(view, args)
    if record is None:
        return ActionPlan(failure_reason=f"{key} has no open plan")
    return ActionPlan(effect=PlanEffect("complete", plan=key), facts={"plan_id": record.plan_id})


def _base_sites(view: RuntimeView, structure: str, other_than: Point) -> int:
    """Distinct sites of ``structure`` other than ``other_than``: standing (any build
    progress), ordered by a task, or carried by a worker's order no task tracks."""
    obs = view.observation
    ability = EXPANSION_BUILD_ABILITY[structure]
    sites = [s.position for s in obs.own_structures if s.type_name == structure]
    sites += [site for kind, site in view.in_flight_builds() if kind == structure]
    for unit in obs.own_units:
        order = unit.orders[0] if unit.orders else None
        if order is not None and order.ability == ability and isinstance(order.target, tuple):
            sites.append(order.target)
    distinct: list[Point] = []
    for site in sites:
        if distance(site, other_than) <= POINT_MATCH_TOLERANCE:
            continue
        if not any(distance(site, seen) <= POINT_MATCH_TOLERANCE for seen in distinct):
            distinct.append(site)
    return len(distinct)


def _build_expansion(view: RuntimeView, args: Args) -> ActionPlan:
    """The first bound worker builds a Nexus at the first bound site (the plan's,
    exactly: no alternatives); SC2 checks placement through the adapter. Never a
    third base: refused while :data:`MAX_BASES` - 1 Nexus sites stand or are ordered
    elsewhere. Already standing at the site: satisfied, nothing is sent. It never
    holds minerals itself: the plan's ``plan_reserve`` stage does, and this build
    takes that reservation over when it issues."""
    structure = _arg_str(args, "structure")
    bound_workers = _binding(view, _arg_str(args, "worker")).entities
    workers, blocked = _own_actors(view, bound_workers, structures=False, worker=True)
    sites = _binding(view, _arg_str(args, "site")).points[:1]
    if not workers or not sites:
        reason = blocked[0][1] if blocked else "worker or site binding empty"
        return ActionPlan(failure_reason=reason)
    worker, site = workers[0], sites[0]
    facts: dict[str, JsonValue] = {
        "structure": structure,
        "worker": str(worker.tag),
        "site": [site[0], site[1]],
    }
    standing = [
        s
        for s in view.observation.own_structures
        if s.type_name == structure and distance(s.position, site) <= POINT_MATCH_TOLERANCE
    ]
    semantic = f"{structure}@{format_point(site)}"
    ability = EXPANSION_BUILD_ABILITY[structure]
    if standing:
        intent = Intent(worker.tag, semantic, ability, site, satisfied=True)
        return ActionPlan(intents=(intent,), facts=facts)
    elsewhere = _base_sites(view, structure, site)
    facts["bases_elsewhere"] = elsewhere
    if elsewhere + 1 > MAX_BASES:
        return ActionPlan(
            failure_reason=(
                f"{elsewhere} {structure} site(s) stand or are ordered elsewhere: "
                f"never more than {MAX_BASES} bases"
            ),
            facts=facts,
            code="base_cap",
        )
    intent = Intent(
        actor_tag=worker.tag,
        semantic=semantic,
        ability=ability,
        target=site,
        minerals=MINERAL_COST[structure],
        params={PARAM_STRUCTURE: structure},
        vespene=VESPENE_COST[structure],
    )
    facts.update({"minerals": intent.minerals, "vespene": intent.vespene})
    return ActionPlan(intents=(intent,), facts=facts)


def _transfer_workers(view: RuntimeView, args: Args) -> ActionPlan:
    """Bring the bound patches up to ``count`` workers: each one gathering there
    (or ordered there by an unacknowledged task) counts; the deficit is filled from
    the bound workers not already there, not gathering gas and not held, by tag,
    each to the least-loaded patch up to ``per_patch``."""
    bound_workers = _binding(view, _arg_str(args, "workers")).entities
    workers, blocked = _own_actors(view, bound_workers, structures=False, worker=True)
    mineral_tags = {m.tag for m in view.observation.mineral_fields}
    patches = [
        p for p in _binding(view, _arg_str(args, "minerals")).entities if p.tag in mineral_tags
    ]
    per_patch = _arg_int(args, "per_patch")
    count = _arg_int(args, "count")
    if not patches:
        return ActionPlan(failure_reason="no mineral patch bound", blocked=tuple(blocked))
    patch_tags = {p.tag for p in patches}
    load = _patch_loads(view, patches)
    there = [
        unit
        for unit in view.observation.own_units
        if unit.type_name == WORKER_TYPE and _gather_target(unit) in patch_tags
    ]
    total = sum(load[tag] for tag in patch_tags)
    deficit = max(0, count - total)
    intents = [Intent(u.tag, str(u.tag), GATHER_ABILITY, None, satisfied=True) for u in there]
    sent = 0
    for worker in sorted(workers, key=lambda w: w.tag):
        if sent >= deficit:
            break
        if (
            _gather_target(worker) in patch_tags
            or is_gas_worker(worker, view)
            or view.actor_is_busy(worker.tag)
        ):
            continue
        patch = _least_loaded_patch(patches, load, per_patch, worker)
        load[patch.tag] += 1
        intents.append(Intent(worker.tag, str(worker.tag), GATHER_ABILITY, patch.tag))
        sent += 1
    facts: dict[str, JsonValue] = {
        "count": count,
        "workers_there": total,
        "transferred": sent,
        "per_patch": per_patch,
    }
    if not intents:
        reason = f"no free worker to bring {len(patch_tags)} patch(es) up to {count}"
        return ActionPlan(failure_reason=reason, blocked=tuple(blocked), facts=facts)
    return ActionPlan(intents=tuple(intents), facts=facts)


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


def _gas_build_ack(task: TaskSubject, obs: Observation) -> Ack | None:
    """An own gas structure stands on the task's geyser (SC2 picks the rich variant on
    a rich geyser, so any own gas structure type there counts)."""
    site = decode_site(task.params)
    standing = [] if site is None else _gas_structures_at(obs.own_structures, site)
    if not standing:
        return None
    return Ack(tracked_tag=min(e.tag for e in standing))


def _gas_build_target_lost(task: TaskSubject, obs: Observation) -> str | None:
    """The geyser left sight, ran dry or carries someone else's gas structure.

    Checked before acknowledgement (runtime order), so an own gas structure on the
    geyser -- the acknowledgement itself -- is never a loss.
    """
    site = decode_site(task.params)
    if site is not None and _gas_structures_at(obs.own_structures, site):
        return None
    geyser = next((g for g in obs.vespene_geysers if g.tag == task.target), None)
    if geyser is None:
        return "geyser lost: no longer a visible vespene geyser"
    if geyser.vespene_contents <= 0:
        return "geyser depleted"
    enemies = (*obs.visible_enemies, *obs.remembered_enemy_structures)
    if _gas_structures_at(enemies, geyser.position):
        return "geyser taken by an enemy gas structure"
    return None


def _harvest_ack(task: TaskSubject, obs: Observation) -> Ack | None:
    """The worker's current order is to gather from exactly the task's target."""
    worker = obs.own_entity(task.actor_tag)
    if worker is not None and _gather_target(worker) == task.target:
        return Ack()
    return None


def _harvest_target_lost(task: TaskSubject, obs: Observation) -> str | None:
    """A gas or mineral gather's target can no longer be gathered from (D4)."""
    target = task.target
    if not isinstance(target, int):
        return GATHER_TARGET_LOST_REASON
    building = obs.own_entity(target)
    if building is not None and building.type_name in GAS_STRUCTURE_TYPES:
        return GAS_DEPLETED_REASON if building.vespene_contents <= 0 else None
    if any(m.tag == target for m in obs.mineral_fields):
        return None
    return GATHER_TARGET_LOST_REASON


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


def _ground_enemies_near(point: Point, radius: float, obs: Observation) -> dict[int, float]:
    """Health and shields of every visible ground enemy (unit or structure) in range."""
    return {
        enemy.tag: enemy.durability
        for enemy in obs.visible_enemies
        if not enemy.is_flying and distance(point, enemy.position) <= radius
    }


def _unit_progress(task: TaskSubject, obs: Observation) -> Progress:
    """Movement/attack progress (metric: the distance still to close).

    An attack-move point is reached only once it is *clear*: no visible ground
    enemy within ``arrive_within`` of it (D3: move on after clearing a target).
    While enemies hold it, or while the unit fights ground enemies within that
    radius on the way, the task reports them as ``engaged``: the fight is progress
    only while they lose health, so an enemy the unit cannot hurt still ends in
    the 30 s replan. A unit target in range is engaged likewise.
    """
    unit = obs.own_entity(task.actor_tag)
    if unit is None:
        return Progress("lost", "unit lost")
    engage_range = decode_arrive_within(task.params)
    if isinstance(task.target, tuple):
        gap = distance(unit.position, task.target)
        attacking = task.ability == ATTACK_ABILITY
        holding = _ground_enemies_near(task.target, engage_range, obs) if attacking else {}
        if gap <= engage_range and not holding:
            return Progress("complete", "arrived; the location is clear", gap)
        if gap <= engage_range:
            return Progress("ongoing", "clearing the location", gap, holding)
        fighting = _ground_enemies_near(unit.position, engage_range, obs) if attacking else {}
        if fighting:
            return Progress("ongoing", "fighting on the way", gap, fighting)
        return Progress("ongoing", "moving", gap)
    enemy = _visible_enemy(task.target, obs) if isinstance(task.target, int) else None
    if enemy is None:
        return Progress("lost", TARGET_LOST_REASON)
    gap = distance(unit.position, enemy.position)
    engaged = {enemy.tag: enemy.durability} if gap <= engage_range else None
    return Progress("ongoing", "engaging", gap, engaged)


def _unit_target_lost(task: TaskSubject, obs: Observation) -> str | None:
    """A unit target that is no longer a visible enemy invalidates the task (D4)."""
    if isinstance(task.target, int) and _visible_enemy(task.target, obs) is None:
        return TARGET_LOST_REASON
    return None


def _target_never_lost(task: TaskSubject, obs: Observation) -> str | None:
    """Gather/build/train: acknowledgement and retries cover their targets."""
    return None


_GATHER_LIFECYCLE: Final = Lifecycle(
    acknowledge=_gather_ack,
    progress=_immediate_progress,
    target_lost=_target_never_lost,
    completes_on_ack=True,
    deadline_kind=None,
    tracks_movement=False,
    holds_actor_while_running=False,
)
_BUILD_LIFECYCLE: Final = Lifecycle(
    acknowledge=_build_ack,
    progress=_build_progress,
    target_lost=_target_never_lost,
    completes_on_ack=False,
    deadline_kind="build",
    tracks_movement=False,
    holds_actor_while_running=False,
)
_TRAIN_LIFECYCLE: Final = Lifecycle(
    acknowledge=_train_ack,
    progress=_train_progress,
    target_lost=_target_never_lost,
    completes_on_ack=False,
    deadline_kind="train",
    tracks_movement=False,
    holds_actor_while_running=True,
)
_UNIT_LIFECYCLE: Final = Lifecycle(
    acknowledge=_unit_ack,
    progress=_unit_progress,
    target_lost=_unit_target_lost,
    completes_on_ack=False,
    deadline_kind=None,
    tracks_movement=True,
    holds_actor_while_running=True,
)
_GAS_BUILD_LIFECYCLE: Final = Lifecycle(
    acknowledge=_gas_build_ack,
    progress=_build_progress,
    target_lost=_gas_build_target_lost,
    completes_on_ack=False,
    deadline_kind="build",
    tracks_movement=False,
    holds_actor_while_running=False,
)
#: Gas gathers and gas releases: confirmed by the worker's gather order on exactly
#: the target (a released worker's previous order was a gather too).
_HARVEST_LIFECYCLE: Final = Lifecycle(
    acknowledge=_harvest_ack,
    progress=_immediate_progress,
    target_lost=_harvest_target_lost,
    completes_on_ack=True,
    deadline_kind=None,
    tracks_movement=False,
    holds_actor_while_running=False,
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
#: A plan_wait holds still (running) or finds no open plan (failure); it never succeeds.
_WAIT_OUTCOMES: Final[frozenset[NodeStatus]] = _ACTION_OUTCOMES - {"success"}

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
        "affordable",
        "Whether a type's price from the central cost tables, in each named resource, "
        "is available after reservations (never a cost spelled in the policy)",
        {
            "item": ArgSpec("enum", choices=tuple(MINERAL_COST)),
            "resources": ArgSpec("enum_list", choices=AFFORD_RESOURCES),
        },
        _affordable,
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
        "recent_task_count_compare",
        "Compare how many tasks of an action node finished in the given terminal "
        "states within the last 'within' game seconds (of its latest "
        f"{MAX_TRACKED_FINISHES} finished tasks): bounds repeated failures",
        {
            "node": ArgSpec("node", tracks="finishes"),
            "statuses": ArgSpec("enum_list", choices=TERMINAL_TASK_STATUSES),
            "within": ArgSpec("number", minimum=1.0, maximum=3600.0, param=True),
            "op": _COMPARATOR,
            "value": ArgSpec("int", minimum=0, maximum=MAX_TRACKED_FINISHES, param=True),
        },
        _recent_task_count_compare,
    ),
    PredicateOp(
        "planned_supply_compare",
        "Compare planned supply: free supply after in-flight training and holds, plus "
        "supply structures warping in or ordered, minus each ready producer's next unit "
        "(measure 'free'); or how far the cap, once those structures complete, can still "
        "grow before SC2's supply ceiling (measure 'ceiling_room')",
        {
            "measure": ArgSpec("enum", choices=SUPPLY_MEASURES),
            "op": _COMPARATOR,
            "value": ArgSpec("int", minimum=0, maximum=MAX_SUPPLY, param=True),
            "producer_demand": ArgSpec("bool", required=False, default=True),
        },
        _planned_supply_compare,
    ),
    PredicateOp(
        "visit_count_compare",
        "Compare how many times an action node visited a map location (a task "
        "arrived at or gave up on it, or an actor was already there)",
        {
            "node": ArgSpec("node", tracks="visits"),
            "point": ArgSpec("point"),
            "op": _COMPARATOR,
            "value": ArgSpec("int", minimum=0, maximum=1000, param=True),
        },
        _visit_count_compare,
    ),
    PredicateOp(
        "army_mode_is",
        "Branch on the current Typesafe army decision, or use the scripted baseline",
        {"mode": ArgSpec("enum", choices=("attack", "defend", "regroup"))},
        _army_mode_is,
    ),
    PredicateOp(
        "latch_is_set",
        "Whether a cross-tick latch has been set",
        {"latch": ArgSpec("enum", choices=LATCHES)},
        _latch_is_set,
    ),
    PredicateOp(
        "latch_is_clear",
        "Whether a cross-tick latch is still clear (has never been set)",
        {"latch": ArgSpec("enum", choices=LATCHES)},
        _latch_is_clear,
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
    PredicateOp(
        "sighting_count_compare",
        "Compare how many enemies the bot has seen that match a filter (applied to each "
        "entity as last seen: a 'within' radius measures its last-seen position), seen at "
        "most max_age game seconds ago; visibility 'visible' counts only those in sight "
        "now, 'remembered' only those out of sight",
        {
            "filter": ArgSpec("filter", required=False),
            "max_age": ArgSpec("number", minimum=0.0, maximum=MAX_ELAPSED_SECONDS, param=True),
            "visibility": ArgSpec(
                "enum", required=False, choices=SIGHTING_VISIBILITIES, default="any"
            ),
            "op": _COMPARATOR,
            "value": ArgSpec("int", minimum=0, maximum=1000, param=True),
        },
        _sighting_count_compare,
    ),
    PredicateOp(
        "assignment_age_compare",
        "Compare the game seconds since an action node last took charge of an entity "
        "that was not already in its charge (never: infinitely long ago); spaces "
        "replacements such as a new scout",
        {
            "node": ArgSpec("node", tracks="assignments"),
            "op": _COMPARATOR,
            "seconds": ArgSpec("number", minimum=0.0, maximum=MAX_ELAPSED_SECONDS, param=True),
        },
        _assignment_age_compare,
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
            "skip_demoted_by": ArgSpec("node", required=False, tracks="demotions"),
            "assigned_to": ArgSpec("node", required=False, tracks="assignments"),
            "not_assigned_to": ArgSpec("node", required=False, tracks="assignments"),
            "bind": _BIND,
        },
        "units",
        _select_entities,
    ),
    SelectOp(
        "select_locations",
        "Sort and limit map start/expansion locations; with least_recently_visited_by, "
        "locations that node never visited come first, then the one visited longest "
        "ago (the sort breaks ties), so repeated selection cycles through them",
        {
            "source": ArgSpec("enum", choices=LOCATION_SOURCES),
            "sort": ArgSpec("sort", required=False, choices=LOCATION_SORT_KEYS),
            "least_recently_visited_by": ArgSpec("node", required=False, tracks="visits"),
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
        OP_TRAIN,
        "Train one unit from each bound idle, ready, powered producer once the unit's "
        "tech requirement is ready (with powered_prerequisite, also powered: stricter than "
        "SC2); optional composition guards cap the unit's planned count (max_count) and its "
        "ratio to another unit's planned count (ratio_of, max_ratio)",
        {
            "unit": ArgSpec("enum", choices=tuple(TRAIN_ABILITY)),
            "producers": ArgSpec("entities"),
            "hold_reservation": _HOLD,
            "max_count": ArgSpec("int", required=False, minimum=0, maximum=MAX_SUPPLY, param=True),
            "ratio_of": ArgSpec("enum", required=False, choices=tuple(TRAIN_ABILITY)),
            "max_ratio": ArgSpec(
                "number", required=False, minimum=0.0, maximum=MAX_COMPOSITION_RATIO, param=True
            ),
            "powered_prerequisite": ArgSpec("bool", required=False, default=False),
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
        "Attack a bound visible enemy; attack-move bound units to a bound point, or to "
        "where a bound entity that is not a visible enemy (a remembered structure) was seen",
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
    ActionOp(
        OP_BUILD_GAS,
        "Order the first bound worker to build a gas structure on the first bound vespene "
        "geyser that is visible, has gas left, carries no gas structure and has no gas "
        "build ordered or recently refused; the command targets the geyser unit by tag, "
        "never a point",
        {
            "structure": ArgSpec("enum", choices=tuple(GAS_BUILD_ABILITY)),
            "worker": ArgSpec("entities"),
            "geyser": ArgSpec("entities"),
            "hold_reservation": _HOLD,
        },
        _build_gas,
        _ACTION_OUTCOMES,
        _GAS_BUILD_LIFECYCLE,
    ),
    ActionOp(
        OP_GATHER_GAS,
        "Send the nearest bound workers not already gathering gas to each bound own gas "
        "building with open slots: per_building, never above SC2's ideal count, none "
        "while unfinished or once depleted",
        {
            "workers": ArgSpec("entities"),
            "buildings": ArgSpec("entities"),
            "per_building": ArgSpec("int", minimum=1, maximum=MAX_GAS_HARVESTERS, param=True),
        },
        _gather_gas,
        _ACTION_OUTCOMES,
        _HARVEST_LIFECYCLE,
    ),
    ActionOp(
        OP_RELEASE_GAS,
        "Send the workers a bound own gas building holds beyond its slots (all of them "
        "once it is depleted) to the least-loaded bound mineral patch, up to per_patch",
        {
            "buildings": ArgSpec("entities"),
            "minerals": ArgSpec("entities"),
            "per_building": ArgSpec("int", minimum=1, maximum=MAX_GAS_HARVESTERS, param=True),
            "per_patch": ArgSpec("int", minimum=1, maximum=3, param=True),
        },
        _release_gas,
        _ACTION_OUTCOMES,
        _HARVEST_LIFECYCLE,
    ),
    PredicateOp(
        "plan_status_is",
        "Whether the latest plan a start_plan node started is in one of the given statuses "
        "(active or waiting: open; completed; aborted); false before it ever started one",
        {
            "plan": ArgSpec("plan"),
            "statuses": ArgSpec("enum_list", choices=PLAN_STATUSES),
        },
        _plan_status_is,
    ),
    SelectOp(
        "select_expansion",
        "The open plan's site: its committed site while no enemy structure stands on it, "
        "else the nearest reachable unoccupied expansion location by SC2 path distance "
        "(Euclidean order only bounds the candidates; one without a path answer is not "
        "yet eligible and is queried) -- committed to the plan on success",
        {
            "plan": ArgSpec("plan"),
            "candidates": ArgSpec(
                "int",
                required=False,
                minimum=1,
                maximum=MAX_EXPANSION_CANDIDATES,
                default=MAX_EXPANSION_CANDIDATES,
            ),
            "clearance": ArgSpec(
                "number", required=False, minimum=1.0, maximum=20.0, default=6.0, param=True
            ),
            "max_path_ratio": ArgSpec(
                "number", required=False, minimum=1.0, maximum=4.0, default=1.5, param=True
            ),
            "bind": _BIND,
        },
        "points",
        _select_expansion,
    ),
    PredicateOp(
        "plan_site_seen",
        "Whether the open plan's site was in the bot's own sight at most max_age game "
        "seconds ago (unknown is not safe: never seen, or seen too long ago, fails)",
        {
            "plan": ArgSpec("plan"),
            "max_age": ArgSpec("number", minimum=0.0, maximum=MAX_ELAPSED_SECONDS, param=True),
        },
        _plan_site_seen,
    ),
    SelectOp(
        "select_plan_actor",
        "The open plan's one actor while it lives; else the first own unit matching the "
        "filter (sorted) that no task, plan or this tick holds, claimed for the plan",
        {
            "plan": ArgSpec("plan"),
            "filter": ArgSpec("filter", required=False),
            "sort": ArgSpec("sort", required=False, choices=SORT_KEYS),
            "not_assigned_to": ArgSpec("node", required=False, tracks="assignments"),
            "bind": _BIND,
        },
        "units",
        _select_plan_actor,
    ),
    ActionOp(
        OP_START_PLAN,
        "Open a persistent plan of this kind for this node's root lane (no game command); "
        "refused while its slot holds an open plan, cools down after an abort, or the "
        "kind is disabled for the match",
        {"kind": ArgSpec("enum", choices=PLAN_KINDS)},
        _start_plan,
        _DECISIVE_OUTCOMES,
        None,
    ),
    ActionOp(
        "plan_wait",
        "Hold the open plan still on a named blocker (no game command): running, the plan "
        "waiting with this reason code",
        {"plan": ArgSpec("plan"), "reason": ArgSpec("enum", choices=WAIT_CODES)},
        _plan_wait,
        _WAIT_OUTCOMES,
        None,
    ),
    ActionOp(
        "plan_reserve",
        "Hold the open plan's next structure's minerals and vespene this tick, up to what "
        "the lanes before it left (no game command; nothing once its build is in flight); "
        "the plan's own build takes the hold over, never charging it twice",
        {
            "plan": ArgSpec("plan"),
            "structure": ArgSpec(
                "enum",
                choices=(
                    *EXPANSION_BUILD_ABILITY,
                    *BUILD_ABILITY,
                    *GAS_BUILD_ABILITY,
                ),
            ),
        },
        _plan_reserve,
        _DECISIVE_OUTCOMES,
        None,
    ),
    ActionOp(
        "plan_complete",
        "Complete the open plan, releasing its holds (no game command)",
        {"plan": ArgSpec("plan")},
        _plan_complete,
        _DECISIVE_OUTCOMES,
        None,
    ),
    ActionOp(
        OP_BUILD_EXPANSION,
        "Order the first bound worker to build a Nexus at the first bound site (the plan's "
        "path-checked site; SC2 checks placement); refused while another base already "
        "stands or is ordered elsewhere (never a third)",
        {
            "structure": ArgSpec("enum", choices=tuple(EXPANSION_BUILD_ABILITY)),
            "worker": ArgSpec("entities"),
            "site": ArgSpec("points"),
        },
        _build_expansion,
        _ACTION_OUTCOMES,
        _BUILD_LIFECYCLE,
    ),
    ActionOp(
        OP_TRANSFER_WORKERS,
        "Bring the bound patches up to count workers from the bound workers not already "
        "there, not gathering gas and not held, each to the least-loaded patch",
        {
            "workers": ArgSpec("entities"),
            "minerals": ArgSpec("entities"),
            "per_patch": ArgSpec("int", minimum=1, maximum=3, param=True),
            "count": ArgSpec("int", minimum=1, maximum=MAX_SELECTION, param=True),
        },
        _transfer_workers,
        _ACTION_OUTCOMES,
        _HARVEST_LIFECYCLE,
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
        node_operations: Mapping[str, str | None] | None = None,
    ) -> None:
        self.parameters = parameters
        self.node_kinds = node_kinds
        self.collection = collection
        self.node_operations = node_operations


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
    # kind == "node" or "plan"
    if not isinstance(value, str):
        return [("invalid_arg", f"argument '{path}' must be a node id, got {safe_repr(value)}")]
    if kind == "plan" and ctx.node_operations is not None and value in ctx.node_operations:
        if ctx.node_operations[value] != OP_START_PLAN:
            return [
                (
                    "invalid_arg",
                    f"argument '{path}' must reference a {OP_START_PLAN} node; "
                    f"'{value}' runs {safe_repr(ctx.node_operations[value])}",
                )
            ]
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
    node_operations: Mapping[str, str | None] | None = None,
) -> list[ArgIssue]:
    """Return ``(code, message)`` issues for ``args`` against the operation's spec.

    With ``node_operations`` (node id -> its operation), a ``plan`` argument must
    name a ``start_plan`` node (without it, only an action node).
    """
    spec = OPERATIONS[operation]
    collection = args.get("collection")
    ctx = _ArgContext(
        parameters,
        node_kinds,
        collection if isinstance(collection, str) else None,
        node_operations,
    )
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
    if operation == OP_TRAIN:
        ratio_of, max_ratio = resolved.get("ratio_of"), resolved.get("max_ratio")
        if (ratio_of is None) != (max_ratio is None):
            return [("invalid_arg", "arguments 'ratio_of' and 'max_ratio' go together")]
        if ratio_of is not None and ratio_of == resolved.get("unit"):
            return [("invalid_arg", "argument 'ratio_of' must name another unit")]
        if resolved.get("powered_prerequisite") is True:
            required = TECH_REQUIREMENT.get(str(resolved.get("unit")))
            if required is None or required not in REQUIRES_POWER:
                return [
                    (
                        "invalid_arg",
                        "argument 'powered_prerequisite' needs a unit whose tech requirement "
                        "is a structure that uses Pylon power",
                    )
                ]
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


def tracked_nodes(
    operation: str, args: Mapping[str, JsonValue], memory: TrackedMemory
) -> frozenset[str]:
    """Action node ids whose ``memory`` ``args`` read (every argument tracking it)."""
    spec = OPERATIONS[operation]
    return frozenset(
        value
        for key, arg_spec in spec.args.items()
        if arg_spec.tracks == memory and isinstance(value := args.get(key), str)
    )


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
