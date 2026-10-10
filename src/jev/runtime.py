"""Deterministic, bounded Jev interpreter (plan D2 and D4).

One :class:`JevRuntime` executes one validated policy for one run:

* **Ticks.** At most one policy tick per ``min_tick_interval_seconds`` of game
  time (default 0.25). Every root lane is serviced every tick, in order, even when
  an earlier lane returns ``running``; there are no graph back-edges, so repeat
  behaviour comes from ticking.
* **Statuses.** ``success`` / ``failure`` / ``running``. A sequence advances on
  success, a selector on failure; both propagate ``running``. Conditions and
  selections are stateless and re-evaluate every tick; a wait yields ``running``
  immediately when its predicate is false.
* **Budgets.** At most ``max_node_evaluations`` (256) node evaluations and
  ``max_commands`` (32) commands per tick. Each lane gets a fair share of what
  is left (``ceil(remaining / lanes_left)``), so an early lane can never consume
  a later lane's share. A breach emits one diagnostic per lane and yields
  (``running``) without spinning.
* **Side effects.** Only through tasks, deduplicated by ``intent_key`` (node ID
  plus the operation's semantic target), so re-evaluation never duplicates an
  active task. Task IDs are ``run_id:counter``. Mineral/vespene/supply
  commitments of unacknowledged tasks and this tick's holds are subtracted before
  later nodes spend; a commitment ends when the observation acknowledges the task
  (SC2 has then deducted the cost) or the task fails, so no cost is ever charged
  twice. An actor held by a task cannot be taken by another node unless the
  action opts into same-lane ``preempt``.
* **Lifecycle (D4).** Issuance is not success: acknowledgement comes from the
  observation (structure progress, queued order, unit order/position). A rejected
  or unconfirmed command is retried at most three times after its first attempt
  (four attempts in all), at least one game second apart, with a five-second
  acknowledgement timeout; construction/training deadlines after acknowledgement;
  movement checks progress every ten game seconds and replans after 30 without
  progress (closing on the target, or a fight whose engaged enemies lost health
  or shields since the previous check, measured tick by tick over the enemies in
  both samples -- a fight that hurts nobody is a stall); dead actors
  -- and unit targets that are no longer visible enemies -- fail tasks at once
  (cause ``lost``, no cooldown, so the graph reselects from current facts on the
  same tick); failed intents cool down for ten game seconds
  with a diagnostic. While a build intent cools down after failing placement, its
  candidate sites are excluded from new placement selections, so recovery picks a
  new site.
* **Adapter hooks.** The SC2 adapter reports what SC2 did with each command:
  :meth:`JevRuntime.mark_command_accepted` (still not success) or
  :meth:`JevRuntime.mark_command_rejected`; :meth:`JevRuntime.task_status` lets it
  ignore late reports for tasks that have already moved on.
* **Memory.** Cross-tick memory is task state, the visits derived from tasks and
  intents, and the ``attack_launched`` latch. For every action node a ``visits``
  argument names, a start/expansion location counts as visited when one of the
  node's tasks targeting it succeeds (arrived), gives up on it (an
  :data:`ABANDON_CAUSES` failure, the replan; for these army target nodes also a
  :data:`REFUSAL_CAUSES` failure once its retries are spent), or when the node
  finds an actor already there (count and latest game time, at most
  :data:`MAX_TRACKED_VISITS` locations per node). Bindings live only for one
  root evaluation in one tick. Two memories exist only for policies that read
  them (:mod:`jev.operations`): per own entity, the action node that most
  recently took charge of it (assignments, pruned to the tick's own entities,
  plus per node the game time it last took charge of a new member, which
  ``assignment_age_compare`` reads and which outlives that member), and per node
  a ``recent_task_count_compare`` names, the finish time and status of its latest
  :data:`~jev.operations.MAX_TRACKED_FINISHES` tasks. The enemy sighting memory
  is not runtime state: the adapter keeps it and every Observation carries it
  (validated like the other observation fields).
* **Persistent plans (Jev v2, plan J2 Step 217).** Only for a policy with a
  ``start_plan`` node (v1 has none, so none of this runs for it) the runtime owns a
  :class:`~jev.plans.PlanStore`. A plan's lane is the root holding its start node;
  after evaluating that lane each tick the runtime reports the leaf that decided
  the lane's status (its *frontier*, with the leaf's reason code) as the plan's
  stage. A plan's actor is busy for every other lane (selections skip it and their
  intents on it are refused); what the lane holds (``plan_reserve``) is the plan's
  reservation, which its own build -- the lane's point build, a Nexus or (Step 218)
  a Cybernetics Core -- takes over; its build task's creation, acknowledgement and
  failure, and its walk's give-up, drive the plan's recovery attempts. An abort
  releases every
  hold at once -- including this tick's held minerals -- and cancels the lane's
  tasks that have not started a structure, so no retry survives an abandoned
  branch. Selections may ask for SC2 path queries (:meth:`JevRuntime.path_requests`),
  which the controller has the adapter answer before the next tick.
* **Bounded recovery from unreachable targets (D4).** A task that gives up
  makes its intent a fresh task on retry (the order SC2 still carries never
  counts as already done). For every action node a ``demotions`` argument
  names, :data:`MAX_TARGET_GIVE_UPS` consecutive give-up rounds against the same
  entity target (no damage dealt to it in between) demote it, with a diagnostic:
  selections with ``skip_demoted_by`` pass over it to the next candidate. The
  demotion lapses, with a diagnostic, once the entity is seen at another position,
  comes back into sight after being out of it, or the node has visited every
  expansion location since (its search cycle completed); the seen-elsewhere rule
  applies to structures only, so a unit target that keeps moving is still
  demoted. Rounds short of a demotion are
  cleared when the entity is seen elsewhere or when the entity itself loses
  durability to an actor's task against it (hurting the target breaks the chain;
  closing in, or hurting other enemies on the way, is progress but no break), so
  a target that drops out of sight between rounds, or that the army keeps walking
  toward without reaching, is still demoted. Give-ups are
  attributed per target, not per actor: one counts for the army (a visit or a
  round) only when no actor has made progress on that target -- closed in,
  hurt it, or reached it -- for the replan window, and then at most once per
  window. Staggered attackers cannot keep a stuck target alive, and one stuck
  actor cannot abandon a target the others are winning.

Every event and command names its originating node (and task where applicable).
Issuing commands to SC2 is the adapter's job; :meth:`JevRuntime.tick` returns the
command specifications it decided on.
"""

from __future__ import annotations

import itertools
from collections import Counter, deque
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Final, Literal, get_args

from jev.contracts import (
    ACTIVE_TASK_STATUSES,
    ENTITY_COLLECTIONS,
    LATCHES,
    LOCATION_SOURCES,
    MAX_ABS_NUMBER,
    MAX_ACTIVE_TASKS,
    MAX_COORDINATE,
    MAX_ENEMY_SIGHTINGS,
    MAX_ENTITY_ORDERS,
    MAX_OBSERVED_ENTITIES,
    MAX_OBSERVED_LOCATIONS,
    MAX_TAG,
    POINT_KEYWORDS,
    RUN_RESULTS,
    RUN_STATUSES,
    CommandSpec,
    EnemySighting,
    Entity,
    Event,
    EventKind,
    EventStatus,
    JevError,
    JsonValue,
    LocationVision,
    NodeStatus,
    Observation,
    Order,
    PathFact,
    Point,
    Policy,
    PolicyNode,
    RunResult,
    RunState,
    RunStatus,
    Target,
    Task,
    TaskStatus,
    is_bounded_number,
    is_valid_run_id,
    safe_repr,
)
from jev.operations import (
    EXPANSION_STRUCTURE,
    MAX_TRACKED_FINISHES,
    OP_BUILD,
    OP_BUILD_EXPANSION,
    OP_BUILD_GAS,
    OP_GATHER,
    OP_GATHER_GAS,
    OP_RELEASE_GAS,
    OP_START_PLAN,
    OP_TRAIN,
    OP_TRANSFER_WORKERS,
    OPERATIONS,
    PARAM_STRUCTURE,
    PARAM_TARGET_TAG,
    PARAM_UNIT,
    POINT_MATCH_TOLERANCE,
    ActionOp,
    Binding,
    CompiledFilter,
    Intent,
    Lifecycle,
    PlanEffect,
    PredicateOp,
    SelectOp,
    TaskSubject,
    TrackedMemory,
    Visit,
    compile_filter,
    decode_site,
    distance,
    resolve_args,
    tracked_nodes,
)
from jev.plans import PLAN_KINDS, BuildFailure, PlanCode, PlanRecord, PlanStore, SlotRecord
from jev.policy import policy_hash, validate_policy

__all__ = [
    "ABANDON_CAUSES",
    "COMMAND_BUDGET_REASON",
    "COOLDOWN_CAUSES",
    "FAILURE_CAUSES",
    "FailureCause",
    "JevRuntime",
    "LifecycleConfig",
    "MAX_CONFIG_INT",
    "MAX_TARGET_GIVE_UPS",
    "MAX_TRACKED_GIVE_UPS",
    "MAX_TRACKED_VISITS",
    "NODE_BUDGET_REASON",
    "REFUSAL_CAUSES",
    "RUNTIME_NODE_ID",
    "SITE_EXCLUSION_CAUSES",
    "TickConfig",
    "TickResult",
    "UNACKNOWLEDGED_STATUSES",
]

NODE_BUDGET_REASON: Final = "node evaluation budget exhausted; yielding until next tick"
COMMAND_BUDGET_REASON: Final = "command budget exhausted; yielding until next tick"
#: Typed cause recorded on every failed task (the reason text stays human-readable).
FailureCause = Literal["lost", "unacknowledged", "rejected", "deadline", "no_progress"]
FAILURE_CAUSES: Final[tuple[FailureCause, ...]] = get_args(FailureCause)
#: Causes that start an intent cooldown; a lost actor/structure replans at once.
COOLDOWN_CAUSES: Final[frozenset[FailureCause]] = frozenset(
    {"unacknowledged", "rejected", "deadline", "no_progress"}
)
#: Failures that mean a task gave up on its target (the D4 replan): a location it
#: targeted counts as visited, so a search moves on instead of retrying it first.
ABANDON_CAUSES: Final[frozenset[FailureCause]] = frozenset({"no_progress", "deadline"})
#: Terminal refusals (retries spent) that are also a give-up for the nodes whose
#: targets the runtime tracks (visits/demotions: the army's targets) -- SC2 dropping
#: an order on an unpathable point or an unattackable unit must not hold the search.
REFUSAL_CAUSES: Final[frozenset[FailureCause]] = frozenset({"rejected", "unacknowledged"})
#: Build failures that exclude the task's candidate sites while the intent cools down.
SITE_EXCLUSION_CAUSES: Final[frozenset[FailureCause]] = frozenset({"rejected", "unacknowledged"})
#: Task states whose command is not yet confirmed: cost committed, actor held.
UNACKNOWLEDGED_STATUSES: Final[frozenset[TaskStatus]] = frozenset({"pending", "issued"})
#: Operations whose tasks send a worker to gather (minerals or gas): their
#: unacknowledged tasks count toward a resource's workers.
_GATHER_OPERATIONS: Final = frozenset(
    {OP_GATHER, OP_GATHER_GAS, OP_RELEASE_GAS, OP_TRANSFER_WORKERS}
)
#: Operations whose tasks build at a point target (their site and alternatives).
_POINT_BUILD_OPERATIONS: Final = frozenset({OP_BUILD, OP_BUILD_EXPANSION})
#: A plan lane's build: its task takes the plan's reservation over, and its
#: acknowledgement, completion and failure drive the plan (an expansion's Nexus,
#: Step 217; the tech plan's Cybernetics Core, Step 218).
_PLAN_BUILD_OPERATIONS: Final = _POINT_BUILD_OPERATIONS
#: Pseudo node id for runtime diagnostics that no policy node originated (it can
#: never collide with an authored id: authored ids must match NODE_ID_RE).
RUNTIME_NODE_ID: Final = "@runtime"
_TIME_EPSILON: Final = 1e-9
#: Map locations whose visits are kept per tracked node: every start and expansion
#: location one observation may carry, so no location is ever dropped.
MAX_TRACKED_VISITS: Final = len(LOCATION_SOURCES) * MAX_OBSERVED_LOCATIONS
#: Consecutive give-up rounds against one entity target before the node demotes it
#: (consecutive: no damage dealt to the target in between; see _target_damaged).
MAX_TARGET_GIVE_UPS: Final = 2
#: Entity targets (and given-up intents) remembered per tracked node, oldest dropped.
MAX_TRACKED_GIVE_UPS: Final = MAX_OBSERVED_ENTITIES
#: Why a demotion lapsed: the ``allowed_again`` fact of its diagnostic.
_AllowedAgain = Literal["moved", "sighted_again", "search_cycle"]


#: Largest integer a config field may hold (keeps deque/allocation sizes sane).
MAX_CONFIG_INT: Final = 1_000_000


def _require_number(config: object, name: str, *, minimum: float, exclusive: bool = False) -> None:
    """Config guard: bounded finite real (not bool) above/at ``minimum``; NaN never passes."""
    value = getattr(config, name)
    if not is_bounded_number(value) or value < minimum or (exclusive and value == minimum):
        bound = f"> {minimum:g}" if exclusive else f">= {minimum:g}"
        raise ValueError(
            f"{type(config).__name__}.{name} must be a finite number {bound}, "
            f"got {safe_repr(value)}"
        )


def _require_int(config: object, name: str, *, minimum: int) -> None:
    value = getattr(config, name)
    if not _is_int(value) or not minimum <= value <= MAX_CONFIG_INT:
        raise ValueError(
            f"{type(config).__name__}.{name} must be an int in {minimum}..{MAX_CONFIG_INT}, "
            f"got {safe_repr(value)}"
        )


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_point(point: object) -> bool:
    """An ``(x, y)`` tuple of finite coordinates with |coordinate| <= MAX_COORDINATE."""
    return (
        isinstance(point, tuple)
        and len(point) == 2
        and all(is_bounded_number(v, MAX_COORDINATE) for v in point)
    )


def _check_point(field: str, point: object) -> None:
    if not _is_point(point):
        raise ValueError(
            f"observation {field} must be a finite (x, y) point with |coordinate| <= "
            f"{MAX_COORDINATE:g}, got {safe_repr(point)}"
        )


def _check_entity(path: str, entity: object) -> None:
    """Contract types for one Entity (and its Orders); ValueError names the field."""
    if not isinstance(entity, Entity):
        raise ValueError(f"observation {path} must be an Entity, got {safe_repr(entity)}")
    if not _is_int(entity.tag) or not 0 <= entity.tag <= MAX_TAG:
        raise ValueError(
            f"observation {path} entity tag must be a uint64, got {safe_repr(entity.tag)}"
        )
    if not isinstance(entity.type_name, str):
        raise ValueError(f"observation {path} type_name must be a string")
    _check_point(f"{path}.position", entity.position)
    for field_name in ("health", "build_progress"):
        if not is_bounded_number(getattr(entity, field_name)):
            raise ValueError(f"observation {path}.{field_name} must be a finite number")
    if not is_bounded_number(entity.shield) or entity.shield < 0:
        raise ValueError(f"observation {path}.shield must be a finite number >= 0")
    for field_name in ("vespene_contents", "assigned_harvesters", "ideal_harvesters"):
        count = getattr(entity, field_name)
        if not _is_int(count) or not 0 <= count <= MAX_ABS_NUMBER:
            raise ValueError(f"observation {path}.{field_name} must be a non-negative int")
    for field_name in (
        "is_structure",
        "is_flying",
        "carrying_vespene",
        "can_attack_ground",
        "can_attack_air",
    ):
        if not isinstance(getattr(entity, field_name), bool):
            raise ValueError(f"observation {path}.{field_name} must be a bool")
    for field_name in ("ready", "idle", "powered"):
        flag = getattr(entity, field_name)
        if flag is not None and not isinstance(flag, bool):
            raise ValueError(f"observation {path}.{field_name} must be a bool or None")
    if not isinstance(entity.orders, tuple):
        raise ValueError(f"observation {path}.orders must be a tuple of Order records")
    if len(entity.orders) > MAX_ENTITY_ORDERS:
        raise ValueError(
            f"observation {path}.orders has {len(entity.orders)} orders; "
            f"limit is {MAX_ENTITY_ORDERS}"
        )
    for index, order in enumerate(entity.orders):
        where = f"{path}.orders[{index}]"
        if not isinstance(order, Order):
            raise ValueError(f"observation {where} must be an Order, got {safe_repr(order)}")
        if not isinstance(order.ability, str):
            raise ValueError(f"observation {where} order ability must be a string")
        if not is_bounded_number(order.progress):
            raise ValueError(f"observation {where} order progress must be a finite number")
        target = order.target
        if isinstance(target, tuple):
            _check_point(f"{where} order target", target)
        elif target is not None and (not _is_int(target) or not 0 <= target <= MAX_TAG):
            raise ValueError(
                f"observation {where} order target must be a tag, point or None, "
                f"got {safe_repr(target)}"
            )


def _check_paths(observation: Observation) -> None:
    """``expansion_paths``: a bounded tuple of PathFact records, one per location, each
    with a finite point and a finite non-negative distance or None (no path)."""
    paths = observation.expansion_paths
    if not isinstance(paths, tuple):
        raise ValueError("observation expansion_paths must be a tuple of PathFact records")
    if len(paths) > MAX_OBSERVED_LOCATIONS:
        raise ValueError(
            f"observation expansion_paths has {len(paths)} entries; "
            f"limit is {MAX_OBSERVED_LOCATIONS}"
        )
    seen: set[Point] = set()
    for index, fact in enumerate(paths):
        path = f"expansion_paths[{index}]"
        if not isinstance(fact, PathFact):
            raise ValueError(f"observation {path} must be a PathFact, got {safe_repr(fact)}")
        _check_point(f"{path}.location", fact.location)
        distance_value = fact.distance
        if distance_value is not None and (
            not is_bounded_number(distance_value) or distance_value < 0
        ):
            raise ValueError(
                f"observation {path}.distance must be None or a finite number >= 0, "
                f"got {safe_repr(distance_value)}"
            )
        if fact.location in seen:
            raise ValueError(f"observation {path} repeats location {safe_repr(fact.location)}")
        seen.add(fact.location)


def _check_vision(observation: Observation) -> None:
    """``location_vision``: a bounded tuple of LocationVision records, one per
    location: a finite point, a bool, and a last-seen time that is None or a finite
    game time no later than the observation's (set whenever it is visible)."""
    records = observation.location_vision
    if not isinstance(records, tuple):
        raise ValueError("observation location_vision must be a tuple of LocationVision records")
    if len(records) > MAX_OBSERVED_LOCATIONS:
        raise ValueError(
            f"observation location_vision has {len(records)} entries; "
            f"limit is {MAX_OBSERVED_LOCATIONS}"
        )
    seen: set[Point] = set()
    for index, record in enumerate(records):
        path = f"location_vision[{index}]"
        if not isinstance(record, LocationVision):
            raise ValueError(
                f"observation {path} must be a LocationVision, got {safe_repr(record)}"
            )
        _check_point(f"{path}.location", record.location)
        if not isinstance(record.visible, bool):
            raise ValueError(f"observation {path}.visible must be a bool")
        last = record.last_seen_game_seconds
        if last is None:
            if record.visible:
                raise ValueError(f"observation {path} is visible but never seen")
        elif not is_bounded_number(last) or not 0 <= last <= observation.game_seconds:
            raise ValueError(
                f"observation {path}.last_seen_game_seconds must be None or a game time "
                f"in 0..{observation.game_seconds:g}, got {safe_repr(last)}"
            )
        if record.location in seen:
            raise ValueError(f"observation {path} repeats location {safe_repr(record.location)}")
        seen.add(record.location)


def _check_sightings(observation: Observation) -> None:
    """``enemy_sightings``: a bounded tuple of EnemySighting records, one per tag, each
    seen at a finite game time no later than the observation's (ValueError names it)."""
    sightings = observation.enemy_sightings
    if not isinstance(sightings, tuple):
        raise ValueError("observation enemy_sightings must be a tuple of EnemySighting records")
    if len(sightings) > MAX_ENEMY_SIGHTINGS:
        raise ValueError(
            f"observation enemy_sightings has {len(sightings)} entries; "
            f"limit is {MAX_ENEMY_SIGHTINGS}"
        )
    tags: set[int] = set()
    for index, sighting in enumerate(sightings):
        path = f"enemy_sightings[{index}]"
        if not isinstance(sighting, EnemySighting):
            raise ValueError(
                f"observation {path} must be an EnemySighting, got {safe_repr(sighting)}"
            )
        _check_entity(f"{path}.entity", sighting.entity)
        seen = sighting.last_seen_game_seconds
        if not is_bounded_number(seen) or not 0 <= seen <= observation.game_seconds:
            raise ValueError(
                f"observation {path}.last_seen_game_seconds must be a finite game time "
                f"in 0..{observation.game_seconds:g}, got {safe_repr(seen)}"
            )
        if not isinstance(sighting.visible, bool):
            raise ValueError(f"observation {path}.visible must be a bool")
        if sighting.entity.tag in tags:
            raise ValueError(f"observation {path} repeats tag {sighting.entity.tag}")
        tags.add(sighting.entity.tag)


@dataclass(frozen=True)
class TickConfig:
    """Interpreter cadence and per-tick budgets (plan D2 defaults)."""

    min_tick_interval_seconds: float = 0.25
    max_node_evaluations: int = 256
    max_commands: int = 32

    def __post_init__(self) -> None:
        _require_number(self, "min_tick_interval_seconds", minimum=0.0)
        _require_int(self, "max_node_evaluations", minimum=1)
        _require_int(self, "max_commands", minimum=1)


@dataclass(frozen=True)
class LifecycleConfig:
    """Task acknowledgement, retry, deadline and retention limits (plan D4/D5)."""

    ack_timeout_seconds: float = 5.0
    retry_interval_seconds: float = 1.0
    #: Retries after the first attempt (D4: "at most three times"); 0 never retries.
    max_retries: int = 3
    build_deadline_seconds: float = 120.0
    train_deadline_seconds: float = 60.0
    progress_check_seconds: float = 10.0
    replan_after_seconds: float = 30.0
    failure_cooldown_seconds: float = 10.0
    max_active_tasks: int = MAX_ACTIVE_TASKS
    task_history_limit: int = 256

    def __post_init__(self) -> None:
        for name in (
            "ack_timeout_seconds",
            "build_deadline_seconds",
            "train_deadline_seconds",
            "progress_check_seconds",
            "replan_after_seconds",
        ):
            _require_number(self, name, minimum=0.0, exclusive=True)
        _require_number(self, "retry_interval_seconds", minimum=0.0)
        _require_number(self, "failure_cooldown_seconds", minimum=0.0)
        _require_int(self, "max_retries", minimum=0)
        _require_int(self, "max_active_tasks", minimum=1)
        _require_int(self, "task_history_limit", minimum=0)


@dataclass(frozen=True)
class TickResult:
    """What one call to :meth:`JevRuntime.tick` decided."""

    ticked: bool
    game_loop: int
    game_seconds: float
    commands: tuple[CommandSpec, ...] = ()
    events: tuple[Event, ...] = ()
    root_status: Mapping[str, NodeStatus] = field(default_factory=dict)

    def executed_nodes(self) -> list[str]:
        """Node IDs evaluated this tick, in evaluation (post-order) sequence."""
        return [e.node_id for e in self.events if e.kind == "node"]


@dataclass
class _TaskRecord:
    """Mutable runtime state behind one :class:`~jev.contracts.Task` snapshot."""

    id: str
    node_id: str
    root_id: str
    intent_key: str
    operation: str
    lifecycle: Lifecycle
    ability: str
    actor_tag: int
    target: Target
    alternatives: tuple[Point, ...]
    params: Mapping[str, JsonValue]
    minerals: int
    supply: int
    status: TaskStatus
    created_at: float
    issued_at: float
    attempts: int
    reason: str
    vespene: int = 0
    acknowledged_at: float | None = None
    next_attempt_at: float | None = None
    last_progress_at: float | None = None
    last_check_at: float | None = None
    last_metric: float | None = None
    last_engaged: Mapping[int, float] | None = None
    damage_dealt: float = 0.0  # engaged enemies' durability lost since the last check
    target_damage: float = 0.0  # the entity target's own share of damage_dealt
    tracked_tag: int | None = None
    failure_cause: FailureCause | None = None
    #: The plan open in this task's lane when it was created (plan J2 Step 217).
    plan_id: str | None = None

    def committed(self) -> bool:
        """True while the task's cost is promised but not yet observed as spent."""
        return self.status in UNACKNOWLEDGED_STATUSES

    def holds_actor(self) -> bool:
        if self.status in UNACKNOWLEDGED_STATUSES:
            return True
        return self.status == "running" and self.lifecycle.holds_actor_while_running

    def deadline(self, config: LifecycleConfig) -> float | None:
        if self.status == "issued":
            return self.issued_at + config.ack_timeout_seconds
        if self.status == "pending":
            return self.next_attempt_at
        if self.status != "running" or self.acknowledged_at is None:
            return None
        if self.lifecycle.deadline_kind == "build":
            return self.acknowledged_at + config.build_deadline_seconds
        if self.lifecycle.deadline_kind == "train":
            return self.acknowledged_at + config.train_deadline_seconds
        if self.lifecycle.tracks_movement and self.last_progress_at is not None:
            return self.last_progress_at + config.replan_after_seconds
        return None

    def build_sites(self) -> tuple[Point, ...]:
        """The sites a build task tried: a point build's candidates, a gas build's
        geyser position; () for any other task."""
        if self.operation in _POINT_BUILD_OPERATIONS and isinstance(self.target, tuple):
            return (self.target, *self.alternatives)
        if self.operation == OP_BUILD_GAS:
            site = decode_site(self.params)
            return () if site is None else (site,)
        return ()

    def cost_facts(self) -> dict[str, JsonValue]:
        """Gas-costing tasks show their cost and whether it is still committed (D5
        facts; a task without a gas cost adds none, so older traces are unchanged)."""
        if not self.vespene:
            return {}
        return {
            "minerals": self.minerals,
            "vespene": self.vespene,
            "cost_committed": self.committed(),
        }

    def subject(self) -> TaskSubject:
        return TaskSubject(
            actor_tag=self.actor_tag,
            ability=self.ability,
            target=self.target,
            alternatives=self.alternatives,
            params=self.params,
            tracked_tag=self.tracked_tag,
        )

    def command(self) -> CommandSpec:
        return CommandSpec(
            node_id=self.node_id,
            task_id=self.id,
            ability=self.ability,
            actor_tags=(self.actor_tag,),
            target=self.target,
            alternatives=self.alternatives,
        )

    def snapshot(self, config: LifecycleConfig) -> Task:
        return Task(
            id=self.id,
            node_id=self.node_id,
            intent_key=self.intent_key,
            actor_tag=self.actor_tag,
            target=self.target,
            status=self.status,
            created_game_seconds=self.created_at,
            deadline_game_seconds=self.deadline(config),
            attempts=self.attempts,
            last_progress_game_seconds=self.last_progress_at,
            reason=self.reason,
        )


@dataclass
class _GiveUps:
    """An entity target a tracked node gave up on: consecutive rounds (no damage
    dealt to it in between) and demotion."""

    position: Point
    out_of_sight: bool
    rounds: int = 0
    demoted_at: float | None = None


@dataclass
class _RootFrame:
    root_id: str
    eval_allowance: int
    command_allowance: int
    evals_used: int = 0
    commands_used: int = 0
    bindings: dict[str, Binding] = field(default_factory=dict)
    node_budget_reported: bool = False
    command_budget_reported: bool = False


@dataclass(frozen=True)
class _NodeResult:
    """A node's status. ``leaf``/``detail``/``code``: the leaf that decided it (the
    *frontier*: for a composite, the deciding child's), that leaf's own reason and
    its plan reason code -- internal, read only for plan lanes."""

    status: NodeStatus
    reason: str
    waiting: bool = False
    leaf: str = ""
    code: PlanCode | None = None
    detail: str = ""


class _View:
    """The :class:`~jev.operations.RuntimeView` an operation sees for one lane."""

    def __init__(
        self,
        runtime: JevRuntime,
        frame: _RootFrame,
        observation: Observation,
        node_id: str | None = None,
    ) -> None:
        self._runtime = runtime
        self._frame = frame
        self._observation = observation
        self._node_id = node_id

    @property
    def observation(self) -> Observation:
        return self._observation

    def binding(self, name: str) -> Binding | None:
        return self._frame.bindings.get(name)

    def available_minerals(self) -> int:
        own = self._runtime._plan_lane_hold(self._frame.root_id)[0]  # the plan's own hold
        return self._runtime._available_minerals(self._observation) + own

    def available_vespene(self) -> int:
        own = self._runtime._plan_lane_hold(self._frame.root_id)[1]
        return self._runtime._available_vespene(self._observation) + own

    def available_supply(self) -> int:
        return self._runtime._available_supply(self._observation)

    def latch_is_set(self, latch: str) -> bool:
        return self._runtime._latches.get(latch, False)

    def army_mode(self) -> str | None:
        return self._runtime._army_mode

    def own_unit_tags(self) -> frozenset[int]:
        return self._runtime._own_unit_tags

    def own_structure_tags(self) -> frozenset[int]:
        return self._runtime._own_structure_tags

    def node_filter(self) -> CompiledFilter | None:
        if self._node_id is None:
            return None
        return self._runtime._filters.get(self._node_id)

    def count_tasks(self, node_id: str, statuses: frozenset[str]) -> int:
        return self._runtime._count_tasks(node_id, statuses)

    def actor_is_busy(self, tag: int) -> bool:
        runtime = self._runtime
        if tag in runtime._reserved_actors or runtime._holder_of(tag) is not None:
            return True
        return runtime._plan_owner_elsewhere(tag, self._frame.root_id) is not None

    def plan(self, node_id: str) -> PlanRecord | None:
        plans = self._runtime._plans
        return None if plans is None else plans.record(node_id)

    def in_flight_builds(self) -> tuple[tuple[str, Point], ...]:
        found: list[tuple[str, Point]] = []
        for task in self._runtime._active.values():
            if not task.committed():
                continue
            if task.operation in _POINT_BUILD_OPERATIONS and isinstance(task.target, tuple):
                found.append((str(task.params.get(PARAM_STRUCTURE, "")), task.target))
            elif task.operation == OP_BUILD_GAS:
                site = decode_site(task.params)
                if site is not None:
                    found.append((str(task.params.get(PARAM_STRUCTURE, "")), site))
        return tuple(found)

    def in_flight_trains(self) -> Mapping[str, int]:
        counts: Counter[str] = Counter()
        for task in self._runtime._active.values():
            if task.operation == OP_TRAIN and task.committed():
                counts[str(task.params.get(PARAM_UNIT, ""))] += 1
        return counts

    def gather_assignments(self) -> Mapping[int, int]:
        counts: Counter[int] = Counter()
        for task in self._runtime._active.values():
            if (
                task.operation in _GATHER_OPERATIONS
                and task.committed()
                and isinstance(task.target, int)
            ):
                counts[task.target] += 1
        return counts

    def rejected_sites(self, structure: str) -> frozenset[Point]:
        now = self._observation.game_seconds
        found: set[Point] = set()
        for kind, sites, until in self._runtime._rejected_sites.values():
            if kind == structure and until > now:
                found.update(sites)
        return frozenset(found)

    def visits(self, node_id: str) -> Mapping[Point, Visit]:
        return MappingProxyType(self._runtime._visits.get(node_id, {}))

    def demoted(self, node_id: str) -> frozenset[int]:
        return self._runtime.demoted_targets(node_id)

    def assigned(self, node_id: str) -> frozenset[int]:
        return self._runtime.assigned_to(node_id)

    def assignment_age(self, node_id: str) -> float | None:
        return self._runtime.assignment_age(node_id)

    def recent_task_count(self, node_id: str, statuses: frozenset[str], within: float) -> int:
        now = self._observation.game_seconds
        finishes = self._runtime._finishes.get(node_id, ())
        return sum(1 for at, status in finishes if status in statuses and now - at < within)


class JevRuntime:
    """Interpret one validated policy for one run (see module docstring)."""

    def __init__(
        self,
        policy: Policy,
        *,
        run_id: str,
        tick_config: TickConfig | None = None,
        lifecycle_config: LifecycleConfig | None = None,
    ) -> None:
        if not is_valid_run_id(run_id):
            raise ValueError(
                f"invalid_run_id: {safe_repr(run_id)} is not a lowercase UUID4 hex string"
            )
        validate_policy(policy)
        self._policy = policy
        self._policy_hash = policy_hash(policy)
        self._run_id = run_id
        if tick_config is not None and not isinstance(tick_config, TickConfig):
            raise ValueError(f"tick_config must be a TickConfig, got {safe_repr(tick_config)}")
        if lifecycle_config is not None and not isinstance(lifecycle_config, LifecycleConfig):
            raise ValueError(
                f"lifecycle_config must be a LifecycleConfig, got {safe_repr(lifecycle_config)}"
            )
        self._tick_config = tick_config or TickConfig()
        self._life = lifecycle_config or LifecycleConfig()
        lanes = len(policy.roots)
        budgets = self._tick_config
        if budgets.max_node_evaluations < lanes or budgets.max_commands < lanes:
            # Fair shares are ceil(remaining / lanes_left); with fewer units than lanes a
            # later lane would get 0 and never be serviced, breaking the D2 guarantee.
            raise ValueError(
                f"tick budgets ({budgets.max_node_evaluations} evaluations, "
                f"{budgets.max_commands} commands) must be at least the number of roots "
                f"({lanes}) so every root is serviced every tick"
            )
        self._args: dict[str, dict[str, JsonValue]] = {
            node.id: resolve_args(node.operation, node.args, policy.parameters)
            for node in policy.nodes
            if node.operation is not None
        }
        # Each node's filter is compiled exactly once (frozenset name lookups per tick).
        self._filters: dict[str, CompiledFilter | None] = {
            node_id: compile_filter(args.get("filter")) for node_id, args in self._args.items()
        }
        # Visits and demotions are kept only for the nodes some operation reads them for.
        self._visit_nodes = self._tracked_nodes("visits")
        self._demotion_nodes = self._tracked_nodes("demotions")
        # Assignments are per entity and kept only while some argument reads them;
        # finishes only for the nodes a recent_task_count_compare names.
        self._track_assignments = bool(self._tracked_nodes("assignments"))
        self._assignments: dict[int, str] = {}
        # Action node -> game time it last took charge of an entity not already in its
        # charge (one value per node, kept after the entity is gone; with assignments).
        self._claimed_at: dict[str, float] = {}
        self._finish_nodes = self._tracked_nodes("finishes")
        self._finishes: dict[str, deque[tuple[float, TaskStatus]]] = {}
        # node id -> map location -> Visit, least recent first (insertion order).
        self._visits: dict[str, dict[Point, Visit]] = {}
        # This tick's start and expansion locations: the points visits are kept for.
        self._map_locations: frozenset[Point] = frozenset()
        # node id -> entity tag -> give-up record, least recent first.
        self._give_ups: dict[str, dict[int, _GiveUps]] = {}
        # intent key -> actor of a task that gave up on it (never satisfied by the
        # order SC2 still carries: its retry is a fresh task), least recent first.
        self._gave_up_keys: dict[str, int] = {}
        # node id -> target (entity tag, else point) -> game time of the latest progress
        # any actor made on it, or of its latest counted give-up; least recent first.
        self._target_clocks: dict[str, dict[int | Point, float]] = {}
        self._tick_observation: Observation | None = None
        self._sequence = 0
        self._task_counter = itertools.count(1)
        self._active: dict[str, _TaskRecord] = {}
        self._by_intent: dict[str, str] = {}
        self._history: deque[_TaskRecord] = deque(maxlen=self._life.task_history_limit)
        # Terminal-status counts per (node, status) for the whole run; the bounded
        # history deque must not make a task-state condition decrease over time.
        self._terminal_counts: Counter[tuple[str, str]] = Counter()
        self._cooldowns: dict[str, float] = {}
        # intent key -> (structure, candidate sites, cooldown end) of a build intent
        # that failed placement; pruned with the cooldowns (bounded by them).
        self._rejected_sites: dict[str, tuple[str, frozenset[Point], float]] = {}
        self._latches: dict[str, bool] = {latch: False for latch in LATCHES}
        self._army_mode: str | None = None
        self._decision_facts: dict[str, JsonValue] = {}
        self._decision_reported: dict[str, JsonValue] = {}
        self._decision_report_time = -1.0
        # Derive guarded task ownership from the graph, not action-node names.
        self._army_task_modes: dict[str, str] = {}
        for branch in policy.nodes:
            if branch.kind != "sequence" or not branch.children:
                continue
            guard = policy.node(branch.children[0])
            if guard.operation != "army_mode_is":
                continue
            mode = str(self._args[guard.id]["mode"])
            pending = list(branch.children)
            while pending:
                child = policy.node(pending.pop())
                self._army_task_modes[child.id] = mode
                pending.extend(child.children)
        self._last_tick_seconds: float | None = None
        self._last_game_loop = 0
        self._last_game_seconds = 0.0
        self._seen_game_loop = 0
        self._seen_game_seconds = 0.0
        self._events: list[Event] = []
        self._commands: list[CommandSpec] = []
        self._reserved_actors: set[int] = set()
        self._held_minerals = 0
        self._held_vespene = 0
        self._held_supply = 0
        self._node_status: dict[str, NodeStatus] = {}
        self._waiting_nodes: set[str] = set()
        self._own_unit_tags: frozenset[int] = frozenset()
        self._own_structure_tags: frozenset[int] = frozenset()
        # Persistent plans (jev.plans): only for a policy with a start_plan node.
        self._plans: PlanStore | None = None
        self._plan_lanes: dict[str, str] = {}  # lane root -> its start_plan node
        self._plan_rank: dict[str, int] = {}  # plan-lane node -> pre-order position
        self._path_requests: dict[Point, None] = {}
        self._tick_spends: list[tuple[str, str]] = []  # (root, node) of new commitments
        starts = [n.id for n in policy.nodes if n.operation == OP_START_PLAN]
        if starts:
            named = {str(self._args[node_id]["kind"]) for node_id in starts}
            # The kinds this policy can start: a slot yields only among them (jev.plans).
            self._plans = PlanStore(run_id, kinds=[k for k in PLAN_KINDS if k in named])
            for root in policy.roots:
                order: list[str] = []
                stack = [root]
                while stack:
                    node_id = stack.pop()
                    order.append(node_id)
                    stack.extend(reversed(policy.node(node_id).children))
                lane_starts = [node_id for node_id in order if node_id in starts]
                if lane_starts:  # validation allows one start_plan per root lane
                    self._plan_lanes[root] = lane_starts[0]
                    self._plan_rank.update((node_id, i) for i, node_id in enumerate(order))

    def _tracked_nodes(self, memory: TrackedMemory) -> frozenset[str]:
        return frozenset(
            itertools.chain.from_iterable(
                tracked_nodes(node.operation, self._args[node.id], memory)
                for node in self._policy.nodes
                if node.operation is not None
            )
        )

    # -- public surface ------------------------------------------------------

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def policy(self) -> Policy:
        return self._policy

    @property
    def policy_hash(self) -> str:
        return self._policy_hash

    @property
    def latches(self) -> Mapping[str, bool]:
        return dict(self._latches)

    @property
    def last_sequence(self) -> int:
        return self._sequence

    def active_tasks(self) -> tuple[Task, ...]:
        return tuple(task.snapshot(self._life) for task in self._active.values())

    def task_history(self) -> tuple[Task, ...]:
        return tuple(task.snapshot(self._life) for task in self._history)

    def demoted_targets(self, node_id: str) -> frozenset[int]:
        """Visible targets this action has given up on; also used by model eligibility."""
        records = self._give_ups.get(node_id, {})
        return frozenset(tag for tag, record in records.items() if record.demoted_at is not None)

    def assigned_to(self, node_id: str) -> frozenset[int]:
        """Own entities whose latest assignment is action node ``node_id``.

        Empty unless the policy reads assignments (``assigned_to`` /
        ``not_assigned_to``); see :mod:`jev.operations` for what takes charge.
        """
        return frozenset(tag for tag, node in self._assignments.items() if node == node_id)

    def assignment_age(self, node_id: str) -> float | None:
        """Game seconds since ``node_id`` last took charge of an entity that was not
        already in its charge (a new member, e.g. a replacement scout); None if it
        never did, or while the policy reads no assignments. Survives the entity."""
        claimed = self._claimed_at.get(node_id)
        return None if claimed is None else max(0.0, self._last_game_seconds - claimed)

    def plans(self) -> tuple[PlanRecord, ...]:
        """Kept plan records: recently finished, then open (empty without plans)."""
        return () if self._plans is None else self._plans.records()

    def plan(self, node_id: str) -> PlanRecord | None:
        """The latest plan ``start_plan`` node ``node_id`` started, if any."""
        return None if self._plans is None else self._plans.record(node_id)

    def plan_slots(self) -> tuple[SlotRecord, ...]:
        """Each plan slot's turn state (whose turn after an abort, cooldown, disabled
        kinds; see :mod:`jev.plans`); empty for a policy that starts no plans."""
        return () if self._plans is None else self._plans.slots()

    def plan_aborts(self) -> int | None:
        """Plans aborted so far this run; None for a policy that starts no plans."""
        return None if self._plans is None else self._plans.aborts

    def path_requests(self) -> tuple[Point, ...]:
        """Expansion locations the last tick's selections need SC2 path distances
        for (plan D4: missing answers mean not yet eligible). The controller has the
        adapter query them before the next tick; empty for a policy that asks none."""
        return tuple(self._path_requests)

    def set_army_decision(self, mode: str | None, facts: dict[str, JsonValue]) -> None:
        """Supply the coordinator's decision for the next graph evaluation."""
        if mode not in (None, "attack", "defend", "regroup"):
            raise ValueError("invalid army mode")
        self._army_mode = mode
        self._decision_facts = dict(facts)

    def tick(self, observation: Observation) -> TickResult:
        """Run one policy tick if the cadence allows; return commands and events.

        Raises :class:`ValueError` (before touching any state) for a non-finite or
        negative clock, a clock that runs backwards, or any non-finite/ill-typed
        numeric field (resources, points, positions, health, progress, order
        targets), so a broken adapter fails visibly instead of corrupting a tick.
        """
        self._check_clock(observation)
        if not self.is_due(observation.game_seconds):
            return TickResult(False, observation.game_loop, observation.game_seconds)
        self._last_tick_seconds = observation.game_seconds
        self._last_game_loop = observation.game_loop
        self._last_game_seconds = observation.game_seconds
        # Existing event wire format carries provider evidence without breaking archives.
        if self._decision_facts and "army" in self._policy.roots:
            stable = {
                k: v for k, v in self._decision_facts.items() if k != "response_age_game_seconds"
            }
            if (
                stable != self._decision_reported
                or observation.game_seconds - self._decision_report_time >= 1
            ):
                self._emit(
                    "army", None, "diagnostic", "success", "Army decision source",
                    self._decision_facts,
                )
                self._decision_reported = stable
                self._decision_report_time = observation.game_seconds
        self._commands = []
        self._reserved_actors = set()
        self._held_minerals = 0
        self._held_vespene = 0
        self._held_supply = 0
        self._node_status = {}
        self._waiting_nodes = set()
        self._own_unit_tags = frozenset(e.tag for e in observation.own_units)
        self._own_structure_tags = frozenset(e.tag for e in observation.own_structures)
        self._map_locations = frozenset(
            itertools.chain.from_iterable(observation.locations(s) for s in LOCATION_SOURCES)
        )
        self._tick_observation = observation
        own = self._own_unit_tags | self._own_structure_tags
        self._gave_up_keys = {k: a for k, a in self._gave_up_keys.items() if a in own}
        if self._assignments:  # bounded by the tick's own entities
            self._assignments = {t: n for t, n in self._assignments.items() if t in own}
        now = observation.game_seconds
        self._cooldowns = {k: v for k, v in self._cooldowns.items() if v > now}
        self._rejected_sites = {k: v for k, v in self._rejected_sites.items() if v[2] > now}

        self._path_requests = {}
        self._tick_spends = []
        self._update_tasks(observation)
        self._lapse_give_ups(observation)
        if self._army_mode is not None:
            for task in list(self._active.values()):
                mode = self._army_task_modes.get(task.node_id)
                if mode is not None and mode != self._army_mode:
                    self._finish(task, "cancelled", f"army intent changed to {self._army_mode}")
        if self._plans is not None:
            self._plan_begin_tick(observation)

        remaining_evals = self._tick_config.max_node_evaluations
        remaining_commands = self._tick_config.max_commands
        roots = self._policy.roots
        root_status: dict[str, NodeStatus] = {}
        for position, root_id in enumerate(roots):
            lanes_left = len(roots) - position
            frame = _RootFrame(
                root_id=root_id,
                eval_allowance=-(-remaining_evals // lanes_left),
                command_allowance=-(-remaining_commands // lanes_left),
            )
            self._issue_retries(frame, observation)
            result = self._evaluate(root_id, frame, observation)
            root_status[root_id] = result.status
            remaining_evals -= frame.evals_used
            remaining_commands -= frame.commands_used
            if root_id in self._plan_lanes:
                self._plan_after_lane(root_id, result, now)

        if self._plans is not None:
            self._plans.summarize(now)
            self._apply_plan_outcomes()
        events = tuple(self._events)
        self._events = []
        return TickResult(
            True,
            observation.game_loop,
            observation.game_seconds,
            tuple(self._commands),
            events,
            root_status,
        )

    def is_due(self, game_seconds: float) -> bool:
        """Whether a :meth:`tick` at ``game_seconds`` would run a policy tick.

        Lets a caller skip building an Observation between ticks. A value tick()
        would reject also reports True, so that tick() raises its ValueError.
        """
        last = self._last_tick_seconds
        if last is None or not is_bounded_number(game_seconds):
            return True
        interval = self._tick_config.min_tick_interval_seconds
        return bool(game_seconds - last >= interval - _TIME_EPSILON)

    def _check_clock(self, observation: Observation) -> None:
        if not isinstance(observation, Observation):
            raise ValueError(
                f"tick() needs an Observation, got {safe_repr(type(observation).__name__)}"
            )
        loop = observation.game_loop
        seconds = observation.game_seconds
        if not _is_int(loop) or not 0 <= loop <= MAX_ABS_NUMBER:
            raise ValueError(
                f"observation game_loop must be a non-negative int, got {safe_repr(loop)}"
            )
        if not is_bounded_number(seconds) or seconds < 0:
            raise ValueError(
                "observation game_seconds must be finite and non-negative, "
                f"got {safe_repr(seconds)}"
            )
        if loop < self._seen_game_loop:
            raise ValueError(f"observation game_loop {loop} precedes {self._seen_game_loop}")
        if seconds < self._seen_game_seconds:
            raise ValueError(
                f"observation game_seconds {seconds} precedes {self._seen_game_seconds}"
            )
        self._check_numbers(observation)
        self._seen_game_loop = loop
        self._seen_game_seconds = float(seconds)

    @staticmethod
    def _check_numbers(observation: Observation) -> None:
        """Every Observation/Entity/Order field has the contract type and a bounded
        value; violations raise ValueError naming the field path (never mutate)."""
        for name in ("minerals", "vespene", "supply_used", "supply_cap"):
            value = getattr(observation, name)
            if not _is_int(value) or not 0 <= value <= MAX_ABS_NUMBER:
                raise ValueError(
                    f"observation {name} must be a non-negative int, got {safe_repr(value)}"
                )
        for keyword in POINT_KEYWORDS:
            _check_point(keyword, observation.point(keyword))
        for source in LOCATION_SOURCES:
            points = observation.locations(source)
            if not isinstance(points, tuple):
                raise ValueError(f"observation {source} must be a tuple of points")
            if len(points) > MAX_OBSERVED_LOCATIONS:
                raise ValueError(
                    f"observation {source} has {len(points)} points; "
                    f"limit is {MAX_OBSERVED_LOCATIONS}"
                )
            for index, point in enumerate(points):
                _check_point(f"{source}[{index}]", point)
        for name in ENTITY_COLLECTIONS:
            entities = observation.collection(name)
            if not isinstance(entities, tuple):
                raise ValueError(f"observation {name} must be a tuple of Entity records")
            if len(entities) > MAX_OBSERVED_ENTITIES:
                raise ValueError(
                    f"observation {name} has {len(entities)} entities; "
                    f"limit is {MAX_OBSERVED_ENTITIES}"
                )
            for index, entity in enumerate(entities):
                _check_entity(f"{name}[{index}]", entity)
        _check_sightings(observation)
        _check_paths(observation)
        _check_vision(observation)

    def task_status(self, task_id: str) -> TaskStatus | None:
        """Status of an active task; None once it has finished, or for an unknown id."""
        if not isinstance(task_id, str):  # never hash arbitrary adapter objects
            return None
        task = self._active.get(task_id)
        return None if task is None else task.status

    def mark_command_accepted(self, task_id: str, *, placement: Point | None = None) -> bool:
        """Adapter hook: SC2 accepted a command. Acceptance is still not success (D4).

        The task stays ``issued`` until the observation acknowledges it. For a build,
        ``placement`` is the candidate SC2 accepted (the adapter tests legality over
        the task's candidates, D3): the task is re-pointed at it, keeping the other
        candidates for retries, and the trace records site and worker.

        Returns True when applied. A report for an unknown task, one no longer
        awaiting acknowledgement, or a ``placement`` that is not one of the task's
        candidate points is not applied: it emits a diagnostic (adapter bugs stay
        visible) and returns False. Events are returned by the next ticked result.
        """
        task = self._awaiting_report(task_id, "acceptance", {})
        if task is None:
            return False
        candidates = (task.target, *task.alternatives) if isinstance(task.target, tuple) else ()
        site: Point | None = None
        if placement is not None:
            if _is_point(placement):
                site = (float(placement[0]), float(placement[1]))
            if site is None or site not in candidates:
                self._diagnostic(
                    task.node_id,
                    f"command acceptance ignored: placement {safe_repr(placement)} is not a "
                    f"candidate of task {task.id}",
                    {"task_id": task.id, "task_status": task.status},
                )
                return False
            task.alternatives = tuple(other for other in candidates if other != site)
            task.target = site
        where = "" if site is None else f" at {site[0]:g},{site[1]:g}"
        task.reason = f"accepted by SC2{where}; awaiting observation"
        self._emit(
            task.node_id,
            task.id,
            "task",
            task.status,
            task.reason,
            {
                "intent_key": task.intent_key,
                "actor_tag": str(task.actor_tag),
                "attempts": task.attempts,
                "placement": None if site is None else [site[0], site[1]],
            },
        )
        return True

    def mark_command_rejected(self, task_id: str, reason: str) -> bool:
        """Adapter hook: SC2 refused a command. Retries respect the D4 limits.

        Returns True when the rejection was applied. A rejection for an unknown
        task, or one no longer awaiting acknowledgement, is not applied: it emits
        a diagnostic (so adapter bugs are visible) and returns False. Events are
        buffered and returned by the next ticked result.
        """
        shown_reason = safe_repr(reason)  # adapter text: rendered and capped
        task = self._awaiting_report(task_id, "rejection", {"rejection": shown_reason})
        if task is None:
            return False
        now = self._last_game_seconds
        if task.attempts <= self._life.max_retries:  # attempts = 1 + retries so far
            task.status = "pending"
            task.next_attempt_at = now + self._life.retry_interval_seconds
            task.reason = f"rejected: {shown_reason}; retry scheduled"
            self._task_event(task)
        else:
            self._fail(task, "rejected", f"rejected: {shown_reason}; attempts exhausted")
        return True

    def _awaiting_report(
        self, task_id: object, hook: str, facts: Mapping[str, JsonValue]
    ) -> _TaskRecord | None:
        """The issued task an adapter report names, or None after a visible diagnostic."""
        if not isinstance(task_id, str):  # never hash/compare arbitrary adapter objects
            self._diagnostic(
                RUNTIME_NODE_ID,
                f"command {hook} ignored: task id {safe_repr(task_id)} is not a string",
                {"task_id": safe_repr(task_id), "task_status": "unknown", **facts},
            )
            return None
        task = self._active.get(task_id)
        if task is not None and task.status == "issued":
            return task
        finished = next((t for t in self._history if t.id == task_id), task)
        state = "unknown" if finished is None else finished.status
        shown_id = safe_repr(task_id)
        self._diagnostic(
            RUNTIME_NODE_ID if finished is None else finished.node_id,
            f"command {hook} ignored: task {shown_id} is {state}, not issued",
            {"task_id": shown_id, "task_status": state, **facts},
        )
        return None

    def run_state(
        self,
        *,
        status: RunStatus,
        updated_at: str,
        recent_events: tuple[Event, ...] = (),
        result: RunResult | None = None,
        error: JevError | None = None,
    ) -> RunState:
        """Snapshot the runtime-owned parts of a :class:`~jev.contracts.RunState`.

        Caller-supplied fields are checked against the contract (ValueError).
        """
        if status not in RUN_STATUSES:
            raise ValueError(f"run status must be one of {RUN_STATUSES}, got {safe_repr(status)}")
        if result is not None and result not in RUN_RESULTS:
            raise ValueError(f"run result must be one of {RUN_RESULTS}, got {safe_repr(result)}")
        if not isinstance(updated_at, str) or not updated_at:
            raise ValueError(f"updated_at must be a non-empty string, got {safe_repr(updated_at)}")
        if not isinstance(recent_events, tuple) or not all(
            isinstance(event, Event) for event in recent_events
        ):
            raise ValueError("recent_events must be a tuple of Event records")
        if error is not None and not isinstance(error, JevError):
            raise ValueError(f"error must be a JevError or None, got {safe_repr(error)}")
        running = [n for n, s in self._node_status.items() if s == "running"]
        return RunState(
            run_id=self._run_id,
            family=self._policy.family,
            version=self._policy.version,
            policy_hash=self._policy_hash,
            status=status,
            updated_at=updated_at,
            game_seconds=self._last_game_seconds,
            last_sequence=self._sequence,
            active_nodes=tuple(n for n in running if n not in self._waiting_nodes),
            waiting_nodes=tuple(n for n in running if n in self._waiting_nodes),
            tasks=self.active_tasks(),
            recent_events=recent_events,
            result=result,
            error=error,
        )

    # -- events ----------------------------------------------------------------

    def _emit(
        self,
        node_id: str,
        task_id: str | None,
        kind: EventKind,
        status: EventStatus,
        reason: str,
        facts: Mapping[str, JsonValue] | None = None,
        action: Mapping[str, JsonValue] | None = None,
    ) -> None:
        self._sequence += 1
        self._events.append(
            Event(
                run_id=self._run_id,
                sequence=self._sequence,
                game_loop=self._last_game_loop,
                game_seconds=self._last_game_seconds,
                node_id=node_id,
                task_id=task_id,
                kind=kind,
                status=status,
                reason=reason,
                facts=dict(facts or {}),
                action=None if action is None else dict(action),
            )
        )

    def _diagnostic(self, node_id: str, reason: str, facts: Mapping[str, JsonValue]) -> None:
        self._emit(node_id, None, "diagnostic", "warning", reason, facts)

    def _task_event(self, task: _TaskRecord) -> None:
        self._emit(
            task.node_id,
            task.id,
            "task",
            task.status,
            task.reason,
            {
                "intent_key": task.intent_key,
                "actor_tag": str(task.actor_tag),
                "attempts": task.attempts,
                "failure_cause": task.failure_cause,
                **task.cost_facts(),
            },
        )

    # -- resources, actors and task queries --------------------------------------

    def _available_minerals(self, observation: Observation) -> int:
        committed = sum(t.minerals for t in self._active.values() if t.committed())
        return observation.minerals - committed - self._held_minerals

    def _available_vespene(self, observation: Observation) -> int:
        committed = sum(t.vespene for t in self._active.values() if t.committed())
        return observation.vespene - committed - self._held_vespene

    def _available_supply(self, observation: Observation) -> int:
        committed = sum(t.supply for t in self._active.values() if t.committed())
        free = observation.supply_cap - observation.supply_used
        return free - committed - self._held_supply

    def _holder_of(self, tag: int) -> _TaskRecord | None:
        for task in self._active.values():
            if task.actor_tag == tag and task.holds_actor():
                return task
        return None

    def _count_tasks(self, node_id: str, statuses: frozenset[str]) -> int:
        count = sum(
            1 for t in self._active.values() if t.node_id == node_id and t.status in statuses
        )
        for status in statuses - ACTIVE_TASK_STATUSES:
            count += self._terminal_counts[(node_id, status)]
        return count

    # -- task lifecycle ------------------------------------------------------------

    def _fail(self, task: _TaskRecord, cause: FailureCause, reason: str) -> None:
        """Fail a task with a typed cause; cooldown eligibility is decided on the cause."""
        task.failure_cause = cause
        self._finish(task, "failed", reason)

    def _finish(self, task: _TaskRecord, status: TaskStatus, reason: str) -> None:
        if (status == "failed") != (task.failure_cause is not None):
            raise RuntimeError("failed tasks must be finished through _fail with a cause")
        task.status = status
        task.reason = reason
        self._active.pop(task.id, None)
        if self._by_intent.get(task.intent_key) == task.id:
            del self._by_intent[task.intent_key]
        self._history.append(task)
        if self._plans is not None and task.plan_id is not None:
            self._plan_task_finished(task, status)
        self._terminal_counts[(task.node_id, status)] += 1
        if task.node_id in self._finish_nodes:
            finishes = self._finishes.get(task.node_id)
            if finishes is None:
                finishes = self._finishes[task.node_id] = deque(maxlen=MAX_TRACKED_FINISHES)
            finishes.append((self._last_game_seconds, status))
        # A give-up speaks for the army only when the whole target stalled (per target).
        abandoned = task.failure_cause in ABANDON_CAUSES or (
            task.failure_cause in REFUSAL_CAUSES
            and task.node_id in self._visit_nodes | self._demotion_nodes
        )
        gave_up = abandoned and self._target_stalled(task)
        if status == "succeeded":
            self._target_progressed(task)
        if status == "succeeded" or gave_up:
            self._record_visit(task.node_id, task.target)  # cleared, or gave up
        self._task_event(task)
        if task.failure_cause in COOLDOWN_CAUSES:
            until = self._last_game_seconds + self._life.failure_cooldown_seconds
            self._cooldowns[task.intent_key] = until
            excluded: list[JsonValue] = []
            sites = task.build_sites()
            if sites and task.failure_cause in SITE_EXCLUSION_CAUSES:
                structure = str(task.params.get(PARAM_STRUCTURE, ""))
                self._rejected_sites[task.intent_key] = (structure, frozenset(sites), until)
                excluded = [[site[0], site[1]] for site in sites]
            self._diagnostic(
                task.node_id,
                f"intent failed ({reason}); cooling down until {until:.2f}s",
                {
                    "intent_key": task.intent_key,
                    "task_id": task.id,
                    "until": until,
                    "failure_cause": task.failure_cause,
                    "excluded_sites": excluded,
                },
            )
        if abandoned:
            self._retry_fresh(task)
        if gave_up:
            self._remember_give_up(task)

    def _record_visit(self, node_id: str, target: Target) -> None:
        """Runtime memory: ``node_id`` visited map location ``target`` now (O(1)).

        Only nodes a ``visits`` argument names, and only the tick's start and
        expansion locations, are kept; anything else is ignored.
        """
        if node_id not in self._visit_nodes or not isinstance(target, tuple):
            return
        if target not in self._map_locations:
            return
        points = self._visits.setdefault(node_id, {})
        previous = points.pop(target, None)  # re-inserted last: the most recent
        count = 1 if previous is None else previous.count + 1
        points[target] = Visit(count, self._last_game_seconds)
        if len(points) > MAX_TRACKED_VISITS:  # unreachable with fixed map metadata
            del points[next(iter(points))]  # the least recent visit

    @staticmethod
    def _target_key(task: _TaskRecord) -> int | Point | None:
        """The target a give-up is attributed to: its entity, else its point."""
        tag = task.params.get(PARAM_TARGET_TAG)
        if isinstance(tag, int) and not isinstance(tag, bool):
            return tag
        return task.target

    def _target_progressed(self, task: _TaskRecord) -> None:
        """An actor made progress on (or reached) ``task``'s target now (O(1))."""
        key = self._target_key(task)
        if key is None or task.node_id not in self._visit_nodes | self._demotion_nodes:
            return
        clocks = self._target_clocks.setdefault(task.node_id, {})
        clocks.pop(key, None)  # re-inserted last: the most recent
        clocks[key] = self._last_game_seconds
        if len(clocks) > MAX_TRACKED_GIVE_UPS:
            del clocks[next(iter(clocks))]

    def _target_stalled(self, task: _TaskRecord) -> bool:
        """Whether ``task``'s give-up counts for the army: no actor made progress on
        the target for the replan window. A counted give-up opens the next window,
        so simultaneous or staggered give-ups count once per window.
        """
        key = self._target_key(task)
        if key is None or task.node_id not in self._visit_nodes | self._demotion_nodes:
            return True  # nothing reads this node's target memory
        last = self._target_clocks.get(task.node_id, {}).get(key)
        window = self._life.replan_after_seconds - _TIME_EPSILON
        if last is not None and self._last_game_seconds - last < window:
            return False  # someone progressed, or a round counted, within the window
        self._target_progressed(task)  # this round opens the next window
        return True

    def _target_damaged(self, task: _TaskRecord) -> None:
        """``task``'s entity target itself lost durability since the last progress
        check: its partial give-up rounds are forgotten, so rounds add up to a
        demotion only while the target takes no damage in between (O(1)). Closing
        in, or hurting other enemies on the way, is progress but never breaks the
        chain, so a target the army cannot reach or hurt is still demoted; a
        demotion stands until one of its lapse rules lifts it.
        """
        tag = task.params.get(PARAM_TARGET_TAG)
        if not isinstance(tag, int) or isinstance(tag, bool):
            return
        records = self._give_ups.get(task.node_id, {})
        record = records.get(tag)
        if record is not None and record.demoted_at is None:
            del records[tag]

    def _retry_fresh(self, task: _TaskRecord) -> None:
        """After a give-up the intent's retry is a fresh task (never already done)."""
        keys = self._gave_up_keys
        keys.pop(task.intent_key, None)
        keys[task.intent_key] = task.actor_tag
        if len(keys) > MAX_TRACKED_GIVE_UPS:
            del keys[next(iter(keys))]

    def _remember_give_up(self, task: _TaskRecord) -> None:
        """The army gave up on its target: count a round against it.

        Called once per round (see :meth:`_target_stalled`). At
        :data:`MAX_TARGET_GIVE_UPS` rounds the target is demoted, with a diagnostic.
        Bounded and O(1) apart from one pass over the tick's visible enemies.
        """
        tag = task.params.get(PARAM_TARGET_TAG)
        observation = self._tick_observation
        if (
            task.node_id not in self._demotion_nodes
            or not isinstance(tag, int)
            or isinstance(tag, bool)
            or observation is None
        ):
            return
        seen = next((e for e in observation.visible_enemies if e.tag == tag), None)
        if isinstance(task.target, tuple):
            position: Point = task.target  # attacked where it was remembered
        elif seen is not None:
            position = seen.position
        else:
            return  # the target is gone: nothing left to demote
        now = self._last_game_seconds
        records = self._give_ups.setdefault(task.node_id, {})
        record = records.pop(tag, None)
        if record is None:
            record = _GiveUps(position, out_of_sight=seen is None)
        records[tag] = record  # re-inserted last: the most recent
        if len(records) > MAX_TRACKED_GIVE_UPS:
            del records[next(iter(records))]
        if record.demoted_at is not None:
            return  # already demoted
        record.rounds += 1
        record.position = position
        if record.rounds < MAX_TARGET_GIVE_UPS:
            return
        record.demoted_at = now
        record.out_of_sight = seen is None
        self._diagnostic(
            task.node_id,
            f"target {tag} demoted after {record.rounds} give-up rounds; selections skip "
            "it until it is seen elsewhere, sighted again, or the search cycle completes",
            {
                "target": str(tag),
                "demoted": True,
                "give_ups": record.rounds,
                "position": [position[0], position[1]],
            },
        )

    def _lapse_give_ups(self, observation: Observation) -> None:
        """Forget give-ups on targets whose situation changed (D4: fresh evaluation).

        Any record lapses once its entity, a structure, is seen at another position
        (a unit target keeps its record however it moves). A demotion
        also lapses, with a diagnostic, once the entity comes back into sight after
        being out of it or the node has visited every expansion location since the
        demotion; partial rounds survive both, so they still add up to a demotion.
        O(visible + remembered + locations + records) per tick.
        """
        if not any(self._give_ups.values()):
            return
        visible = {e.tag: e for e in observation.visible_enemies}
        remembered = {e.tag: e for e in observation.remembered_enemy_structures}
        locations = observation.expansion_locations
        for node_id, records in self._give_ups.items():
            visits = self._visits.get(node_id, {})
            # Every expansion location visited by then completes a search cycle.
            times = [visits[p].last_game_seconds for p in locations if p in visits]
            cycled_at = min(times) if locations and len(times) == len(locations) else None
            for tag, record in list(records.items()):
                demoted_at = record.demoted_at
                entity = visible.get(tag)
                if entity is None:
                    entity = remembered.get(tag)
                reason: _AllowedAgain | None = None
                if (  # structures only: a unit moving about is still the same target
                    entity is not None
                    and entity.is_structure
                    and distance(entity.position, record.position) > POINT_MATCH_TOLERANCE
                ):
                    reason = "moved"
                elif demoted_at is not None and tag in visible and record.out_of_sight:
                    reason = "sighted_again"
                elif demoted_at is not None and cycled_at is not None and cycled_at > demoted_at:
                    reason = "search_cycle"
                if tag not in visible:
                    record.out_of_sight = True
                if reason is None:
                    continue
                del records[tag]
                if demoted_at is not None:
                    self._diagnostic(
                        node_id,
                        f"target {tag} allowed again ({reason})",
                        {"target": str(tag), "allowed_again": reason},
                    )

    def _unacknowledged(self, task: _TaskRecord, now: float) -> None:
        timeout = self._life.ack_timeout_seconds
        if task.attempts <= self._life.max_retries:  # attempts = 1 + retries so far
            task.status = "pending"
            task.next_attempt_at = max(now, task.issued_at + self._life.retry_interval_seconds)
            task.reason = f"unacknowledged after {timeout:g}s; retry scheduled"
            self._task_event(task)
        else:
            self._fail(task, "unacknowledged", f"unacknowledged after {task.attempts} attempts")

    def _update_tasks(self, observation: Observation) -> None:
        now = observation.game_seconds
        for task in list(self._active.values()):
            if task.status in UNACKNOWLEDGED_STATUSES:
                if observation.own_entity(task.actor_tag) is None:
                    self._fail(task, "lost", "actor lost")
                    continue
                # A running task reports a lost target through its progress instead.
                target_lost = task.lifecycle.target_lost(task.subject(), observation)
                if target_lost is not None:
                    self._fail(task, "lost", target_lost)
                    continue
            if task.status == "issued":
                ack = task.lifecycle.acknowledge(task.subject(), observation)
                if ack is None:
                    if now - task.issued_at >= self._life.ack_timeout_seconds - _TIME_EPSILON:
                        self._unacknowledged(task, now)
                    continue
                if task.lifecycle.completes_on_ack:
                    self._finish(task, "succeeded", "acknowledged by observation")
                    continue
                task.status = "running"
                task.acknowledged_at = now
                task.last_progress_at = now
                task.last_check_at = now
                task.tracked_tag = ack.tracked_tag
                if ack.target is not None:
                    task.target = ack.target
                task.reason = "acknowledged by observation"
                self._task_event(task)
                if task.operation in _PLAN_BUILD_OPERATIONS and task.plan_id is not None:
                    key = self._plan_key(task)
                    if key is not None and self._plans is not None:
                        self._plans.build_acknowledged(key, task.node_id, now)
                        self._apply_plan_outcomes()
            if task.status == "running":
                self._advance_running(task, observation, now)

    def _advance_running(self, task: _TaskRecord, observation: Observation, now: float) -> None:
        progress = task.lifecycle.progress(task.subject(), observation)
        if progress.state == "complete":
            self._finish(task, "succeeded", progress.reason)
            return
        if progress.state == "lost":
            self._fail(task, "lost", progress.reason)
            return
        deadline = task.deadline(self._life)
        if task.lifecycle.deadline_kind is not None and deadline is not None and now > deadline:
            self._fail(task, "deadline", f"deadline passed at {deadline:.2f}s")
            return
        if not task.lifecycle.tracks_movement:
            return
        metric = progress.metric
        # Damage is sampled every tick, over enemies in both consecutive samples only;
        # the entity target's own loss is kept apart: only it breaks a give-up chain.
        task.damage_dealt += self._durability_drop(task.last_engaged, progress.engaged)
        key = self._target_key(task)
        if isinstance(key, int):
            task.target_damage += self._durability_drop(
                task.last_engaged, progress.engaged, only=key
            )
        task.last_engaged = progress.engaged
        if task.last_metric is None:
            task.last_metric = metric
        elif task.last_check_at is not None and (
            now - task.last_check_at >= self._life.progress_check_seconds - _TIME_EPSILON
        ):
            improved = metric is not None and metric < task.last_metric - 1.0
            if improved or task.damage_dealt > 0.0:
                task.last_progress_at = now
                self._target_progressed(task)
            if task.target_damage > 0.0:
                self._target_damaged(task)
            task.last_metric = metric
            task.damage_dealt = 0.0
            task.target_damage = 0.0
            task.last_check_at = now
        last_progress = task.last_progress_at if task.last_progress_at is not None else now
        if now - last_progress >= self._life.replan_after_seconds - _TIME_EPSILON:
            self._fail(
                task, "no_progress", f"no progress for {self._life.replan_after_seconds:g}s; replan"
            )

    @staticmethod
    def _durability_drop(
        before: Mapping[int, float] | None,
        after: Mapping[int, float] | None,
        only: int | None = None,
    ) -> float:
        """Health plus shields the engaged enemies lost between two consecutive samples.

        Only enemies in both samples count (same tags), so an enemy that arrives,
        retreats out of range or leaves sight is never mistaken for damage; damage
        dealt before a kill was already counted on the ticks it landed. O(engaged);
        with ``only``, just that enemy's loss under the same rule (O(1)).
        """
        if not before or not after:
            return 0.0
        if only is not None:
            if only not in before or only not in after:
                return 0.0
            return max(0.0, before[only] - after[only])
        return sum(max(0.0, before[tag] - after[tag]) for tag in before.keys() & after.keys())

    def _issue_retries(self, frame: _RootFrame, observation: Observation) -> None:
        now = observation.game_seconds
        for task in list(self._active.values()):
            if task.root_id != frame.root_id or task.status != "pending":
                continue
            if task.next_attempt_at is not None and now < task.next_attempt_at - _TIME_EPSILON:
                continue
            if task.actor_tag in self._reserved_actors:
                continue
            if frame.commands_used >= frame.command_allowance:
                self._command_budget_breach(task.node_id, frame)
                return
            task.status = "issued"
            task.attempts += 1
            task.issued_at = now
            task.next_attempt_at = None
            task.reason = f"retry attempt {task.attempts}"
            self._dispatch(task, frame)

    def _dispatch(self, task: _TaskRecord, frame: _RootFrame) -> None:
        command = task.command()
        self._reserved_actors.add(task.actor_tag)
        self._commands.append(command)
        frame.commands_used += 1
        self._task_event(task)
        self._emit(
            task.node_id,
            task.id,
            "command",
            "issued",
            f"{task.ability} for task {task.id}",
            {"intent_key": task.intent_key, "attempt": task.attempts, **task.cost_facts()},
            command.to_dict(),
        )

    def _command_budget_breach(self, node_id: str, frame: _RootFrame) -> None:
        if frame.command_budget_reported:
            return
        frame.command_budget_reported = True
        self._diagnostic(
            node_id,
            COMMAND_BUDGET_REASON,
            {
                "root": frame.root_id,
                "lane_allowance": frame.command_allowance,
                "tick_limit": self._tick_config.max_commands,
            },
        )

    # -- node evaluation -----------------------------------------------------------

    def _evaluate(self, node_id: str, frame: _RootFrame, observation: Observation) -> _NodeResult:
        if frame.evals_used >= frame.eval_allowance:
            if not frame.node_budget_reported:
                frame.node_budget_reported = True
                self._diagnostic(
                    node_id,
                    NODE_BUDGET_REASON,
                    {
                        "root": frame.root_id,
                        "lane_allowance": frame.eval_allowance,
                        "tick_limit": self._tick_config.max_node_evaluations,
                    },
                )
            # Visible stall: the yielded node is reported as waiting in run_state(),
            # and the diagnostic above carries its budget reason and lane.
            self._node_status[node_id] = "running"
            self._waiting_nodes.add(node_id)
            return _NodeResult(
                "running", NODE_BUDGET_REASON, waiting=True, leaf=node_id, detail=NODE_BUDGET_REASON
            )
        frame.evals_used += 1
        node = self._policy.node(node_id)
        facts: dict[str, JsonValue] = {}
        # Binding rule (jev.contracts): effects commit only on success. Snapshot
        # here, restore on failure -- the single place the interpreter applies it.
        entry_bindings = dict(frame.bindings)
        if node.kind == "sequence" or node.kind == "selector":
            result = self._evaluate_composite(node, frame, observation, facts)
        else:
            result = self._evaluate_leaf(node, frame, observation, facts)
            if frame.root_id in self._plan_lanes:  # the frontier is read for plan lanes only
                result = replace(result, leaf=node.id, detail=result.reason)
        if result.status == "failure":
            frame.bindings = entry_bindings
        self._node_status[node.id] = result.status
        if result.status == "running" and result.waiting:
            self._waiting_nodes.add(node.id)
        self._emit(node.id, None, "node", result.status, result.reason, facts)
        return result

    def _evaluate_composite(
        self,
        node: PolicyNode,
        frame: _RootFrame,
        observation: Observation,
        facts: dict[str, JsonValue],
    ) -> _NodeResult:
        advance: NodeStatus = "success" if node.kind == "sequence" else "failure"
        last: _NodeResult | None = None
        for child in node.children:
            child_result = self._evaluate(child, frame, observation)
            if child_result.status != advance:
                facts["child"] = child
                return _NodeResult(
                    child_result.status,
                    f"child {child} returned {child_result.status}",
                    child_result.waiting,
                    child_result.leaf,
                    child_result.code,
                    child_result.detail,
                )
            last = child_result
        leaf, code, detail = ("", None, "") if last is None else (last.leaf, last.code, last.detail)
        if advance == "success":
            return _NodeResult("success", "all children succeeded", False, leaf, code, detail)
        return _NodeResult("failure", "all children failed", False, leaf, code, detail)

    def _evaluate_leaf(
        self,
        node: PolicyNode,
        frame: _RootFrame,
        observation: Observation,
        facts: dict[str, JsonValue],
    ) -> _NodeResult:
        spec = OPERATIONS[node.operation or ""]
        args = self._args[node.id]
        view = _View(self, frame, observation, node.id)
        if isinstance(spec, PredicateOp):
            outcome = spec.evaluate(view, args)
            facts.update(outcome.facts)
            if outcome.ok:
                return _NodeResult("success", outcome.reason)
            if node.kind == "wait":
                return _NodeResult(
                    "running", f"waiting: {outcome.reason}", waiting=True, code=outcome.code
                )
            return _NodeResult("failure", outcome.reason, code=outcome.code)
        if isinstance(spec, SelectOp):
            selection = spec.evaluate(view, args)
            facts.update(selection.facts)
            name = str(args["bind"])
            facts["bind"] = name
            for point in selection.queries[:MAX_OBSERVED_LOCATIONS]:
                if len(self._path_requests) < MAX_OBSERVED_LOCATIONS:
                    self._path_requests[point] = None
            if selection.binding is None:  # bindings untouched
                return _NodeResult("failure", selection.reason, code=selection.code)
            frame.bindings[name] = selection.binding
            if selection.effect is not None:  # a site or actor claim: never refused
                self._apply_effect(node, frame, selection.effect, observation.game_seconds)
            return _NodeResult("success", selection.reason, code=selection.code)
        return self._run_action(node, spec, frame, view, facts)

    def _run_action(
        self,
        node: PolicyNode,
        spec: ActionOp,
        frame: _RootFrame,
        view: _View,
        facts: dict[str, JsonValue],
    ) -> _NodeResult:
        plan = spec.plan(view, self._args[node.id])
        facts.update(plan.facts)
        if plan.effect is not None:
            refused, reason = self._apply_effect(
                node, frame, plan.effect, view.observation.game_seconds
            )
            return _NodeResult("failure" if refused else "success", reason, code=plan.code)
        if plan.wait_reason is not None:
            return _NodeResult("running", plan.wait_reason, waiting=True, code=plan.code)
        if plan.latch is not None:
            already = self._latches[plan.latch]
            self._latches[plan.latch] = True
            return _NodeResult(
                "success", f"latch {plan.latch} {'already set' if already else 'set'}"
            )
        now = view.observation.game_seconds
        active = issued = satisfied = 0
        blocked = [reason for _, reason in plan.blocked]
        waiting_reason: str | None = None
        code: PlanCode | None = plan.code
        plan_lane = frame.root_id in self._plan_lanes
        for intent in plan.intents:
            key = f"{node.id}|{intent.semantic}"
            if key in self._by_intent:
                active += 1
                if self._track_assignments:  # this node's own task holds the actor
                    self._assign(self._active[self._by_intent[key]].actor_tag, node.id)
                continue
            if intent.satisfied and key not in self._gave_up_keys:
                satisfied += 1
                self._record_visit(node.id, intent.target)  # an actor is already there
                self._assign_if_free(intent.actor_tag, node.id)
                continue
            refusal = self._refusal(node, frame, view, key, intent, plan.preempt, now)
            if refusal is not None:
                blocked.append(refusal)
                if plan_lane and key in self._rejected_sites:
                    code = "site_blocked"  # cooling down after a refused placement
                continue
            shortfall = self._shortfall(view, intent)
            if shortfall is not None:
                if plan.hold_reservation:
                    held_minerals = max(0, min(intent.minerals, view.available_minerals()))
                    self._held_minerals += held_minerals
                    self._held_supply += max(0, min(intent.supply, view.available_supply()))
                    held = 0
                    if intent.vespene:  # a gas cost shows what it holds (D5 facts)
                        held = max(0, min(intent.vespene, view.available_vespene()))
                        self._held_vespene += held
                        facts["held_vespene"] = held
                    if plan_lane:  # the plan's reservation this tick (jev.plans)
                        facts["held_minerals"] = held_minerals
                        self._plan_hold(frame.root_id, held_minerals, held)
                        code = "budget_short"
                    elif self._plans is not None:
                        self._tick_spends.append((frame.root_id, node.id))
                    waiting_reason = f"waiting for {shortfall}; holding reservation"
                else:
                    blocked.append(f"insufficient {shortfall}")
                    if plan_lane:
                        code = "budget_short"
                        facts["shortfall"] = shortfall
                break  # priority order: later intents must not jump the queue
            if frame.commands_used >= frame.command_allowance:
                self._command_budget_breach(node.id, frame)
                waiting_reason = COMMAND_BUDGET_REASON
                break
            if len(self._active) >= self._life.max_active_tasks:
                self._diagnostic(
                    node.id,
                    "active task limit reached",
                    {"limit": self._life.max_active_tasks},
                )
                blocked.append("active task limit reached")
                break
            holder = self._holder_of(intent.actor_tag)
            if holder is not None:
                self._finish(holder, "cancelled", f"preempted by {node.id}")
            self._create_task(node, spec, frame, key, intent, now)
            active += 1
            issued += 1
        facts.update(
            {"active": active, "issued": issued, "satisfied": satisfied, "blocked": len(blocked)}
        )
        if waiting_reason is not None:
            return _NodeResult("running", waiting_reason, waiting=True, code=code)
        if active:
            if plan_lane and spec.name in _PLAN_BUILD_OPERATIONS:
                code = "issued"
            return _NodeResult(
                "running", f"{active} task(s) in progress ({issued} issued now)", code=code
            )
        if blocked:
            return _NodeResult("failure", blocked[0], code=code)
        if plan.failure_reason is not None:
            return _NodeResult("failure", plan.failure_reason, code=code)
        if not plan.intents:
            return _NodeResult("failure", "nothing to act on", code=code)
        return _NodeResult("success", f"{satisfied} actor(s) already in the intended state")

    def _refusal(
        self,
        node: PolicyNode,
        frame: _RootFrame,
        view: _View,
        key: str,
        intent: Intent,
        preempt: bool,
        now: float,
    ) -> str | None:
        until = self._cooldowns.get(key)
        if until is not None and now < until:
            return f"intent cooling down until {until:.2f}s"
        if intent.actor_tag in self._reserved_actors:
            return f"actor {intent.actor_tag} already commanded this tick"
        holder = self._holder_of(intent.actor_tag)
        if holder is not None and not (preempt and holder.root_id == frame.root_id):
            return f"actor {intent.actor_tag} held by task {holder.id} ({holder.node_id})"
        owner = self._plan_owner_elsewhere(intent.actor_tag, frame.root_id)
        if owner is not None:
            return f"actor {intent.actor_tag} held by plan {owner[0]} ({owner[1]})"
        return None

    def _assign(self, actor_tag: int, node_id: str) -> None:
        """``node_id`` took charge of ``actor_tag`` (kept only if the policy reads it)."""
        if self._track_assignments:
            self._take_charge(actor_tag, node_id)

    def _assign_if_free(self, actor_tag: int, node_id: str) -> None:
        """An actor found in ``node_id``'s intended state joins it unless another
        node's task holds it (that node is still in charge)."""
        if self._track_assignments:
            holder = self._holder_of(actor_tag)
            if holder is None or holder.node_id == node_id:
                self._take_charge(actor_tag, node_id)

    def _take_charge(self, actor_tag: int, node_id: str) -> None:
        if self._assignments.get(actor_tag) != node_id:  # a new member: the claim time
            self._claimed_at[node_id] = self._last_game_seconds
        self._assignments[actor_tag] = node_id

    @staticmethod
    def _shortfall(view: _View, intent: Intent) -> str | None:
        if intent.minerals and intent.minerals > view.available_minerals():
            return "minerals"
        if intent.vespene and intent.vespene > view.available_vespene():
            return "vespene"
        if intent.supply and intent.supply > view.available_supply():
            return "supply"
        return None

    def _create_task(
        self,
        node: PolicyNode,
        spec: ActionOp,
        frame: _RootFrame,
        key: str,
        intent: Intent,
        now: float,
    ) -> None:
        if spec.lifecycle is None:
            raise RuntimeError(f"operation {spec.name} has no task lifecycle")
        task = _TaskRecord(
            id=f"{self._run_id}:{next(self._task_counter)}",
            node_id=node.id,
            root_id=frame.root_id,
            intent_key=key,
            operation=spec.name,
            lifecycle=spec.lifecycle,
            ability=intent.ability,
            actor_tag=intent.actor_tag,
            target=intent.target,
            alternatives=intent.alternatives,
            params=intent.params,
            minerals=intent.minerals,
            supply=intent.supply,
            vespene=intent.vespene,
            status="issued",
            created_at=now,
            issued_at=now,
            attempts=1,
            reason="command issued",
        )
        self._active[task.id] = task
        self._by_intent[key] = task.id
        self._gave_up_keys.pop(key, None)
        self._assign(task.actor_tag, node.id)
        if self._plans is not None:
            self._plan_task_created(task)
        self._dispatch(task, frame)

    # -- persistent plans (jev.plans; never reached without a start_plan node) ------------

    def _plan_owner_elsewhere(self, tag: int, root_id: str) -> tuple[str, str] | None:
        """``(plan id, start node)`` of the open plan owning ``tag``, unless the plan
        is ``root_id``'s own (its lane may command its actor)."""
        if self._plans is None:
            return None
        owner = self._plans.actor_owner(tag)
        if owner is None or owner[2] == root_id:
            return None
        return owner[0], owner[1]

    def _plan_key(self, task: _TaskRecord) -> str | None:
        """The start node of the open plan ``task`` was created for (None once that
        plan has ended: a later plan never inherits another plan's task)."""
        key = self._plan_lanes.get(task.root_id)
        if key is None or self._plans is None:
            return None
        record = self._plans.record(key)
        if record is None or not record.is_open or record.plan_id != task.plan_id:
            return None
        return key

    def _plan_hold(self, root_id: str, minerals: int, vespene: int) -> None:
        if self._plans is not None:
            key = self._plans.open_in_lane(root_id)
            if key is not None:
                self._plans.hold(key, minerals, vespene)

    def _plan_lane_hold(self, root_id: str) -> tuple[int, int]:
        """What the open plan of lane ``root_id`` holds this tick: its own lane may
        spend it (its build takes the reservation over); (0, 0) otherwise."""
        if self._plans is None:
            return (0, 0)
        key = self._plans.open_in_lane(root_id)
        return (0, 0) if key is None else self._plans.tick_hold(key)

    def _plan_begin_tick(self, observation: Observation) -> None:
        plans = self._plans
        if plans is None:
            return
        busy: dict[str, bool] = {}
        for key, _ in plans.open_plans():
            record = plans.record(key)
            plan_id = None if record is None else record.plan_id
            busy[key] = any(task.plan_id == plan_id for task in self._active.values())
        plans.begin_tick(observation.game_seconds, self._own_unit_tags, busy)
        bases = [
            s.position for s in observation.own_structures if s.type_name == EXPANSION_STRUCTURE
        ]

        def stands(site: Point) -> bool:
            return any(distance(base, site) <= POINT_MATCH_TOLERANCE for base in bases)

        plans.observe_sites(stands)  # second_base_defense follows the live state
        self._apply_plan_outcomes()

    def _plan_after_lane(self, root_id: str, result: _NodeResult, now: float) -> None:
        plans = self._plans
        if plans is None:
            return
        key = plans.open_in_lane(root_id)
        if key is None:
            return
        stage = result.leaf or root_id
        record = plans.record(key)
        plan_id = None if record is None else record.plan_id
        # A generic wait (no plan code) while the lane's own task progresses -- the
        # structure under construction -- is progress, not a blocker.
        busy = any(task.plan_id == plan_id for task in self._active.values())
        waiting = result.waiting and (result.code is not None or not busy)
        code: PlanCode
        if result.code is not None:
            code = result.code
        elif result.status == "failure":
            code = "blocked"
        elif waiting:
            code = "waiting"
        else:
            code = "in_progress"
        spent = tuple(node for root, node in self._tick_spends if root != root_id)
        plans.after_lane(
            key,
            stage=stage,
            rank=self._plan_rank.get(stage, -1),
            failed=result.status == "failure",
            waiting=waiting,
            code=code,
            reason=result.detail or result.reason,
            now=now,
            spent_before=spent,
        )
        self._apply_plan_outcomes()

    def _apply_effect(
        self, node: PolicyNode, frame: _RootFrame, effect: PlanEffect, now: float
    ) -> tuple[bool, str]:
        """Apply a plan operation's effect: ``(refused, reason)``."""
        plans = self._plans
        if plans is None:
            return True, "this policy starts no plans"
        refused, reason = False, f"plan {effect.action} applied"
        if effect.action == "start":
            if effect.kind is None:
                return True, "start_plan without a kind"
            record, reason = plans.start(node.id, frame.root_id, effect.kind, now)
            refused = record is None
        elif effect.action == "site" and effect.point is not None:
            plans.set_site(
                effect.plan,
                effect.point,
                now,
                replaced=effect.replaced,
                path_distance=effect.path,
                path_rank=effect.rank,
            )
        elif effect.action == "actor" and effect.tag is not None:
            plans.claim_actor(effect.plan, effect.tag)
        elif effect.action == "reserve":
            if plans.open_in_lane(frame.root_id) == effect.plan:  # only its own lane holds
                self._held_minerals += effect.minerals
                self._held_vespene += effect.vespene
                plans.hold(effect.plan, effect.minerals, effect.vespene)
                reason = f"holding {effect.minerals} minerals, {effect.vespene} vespene"
            else:
                refused, reason = True, "only the plan's own lane holds its reservation"
        elif effect.action == "complete":
            refusal = plans.complete(effect.plan, now)
            refused = refusal is not None
            reason = refusal if refusal is not None else "plan completed"
        self._apply_plan_outcomes()
        return refused, reason

    def _plan_task_created(self, task: _TaskRecord) -> None:
        plans = self._plans
        if plans is None:
            return
        if task.minerals or task.vespene:
            self._tick_spends.append((task.root_id, task.node_id))
        key = self._plan_lanes.get(task.root_id)
        if key is None:
            return
        record = plans.record(key)
        if record is None or not record.is_open:
            return
        task.plan_id = record.plan_id
        if task.operation in _PLAN_BUILD_OPERATIONS:
            # Transfer: the task now commits the cost, so the plan's hold this tick is
            # returned (never both); build_committed zeroes it.
            held_minerals, held_vespene = plans.tick_hold(key)
            self._held_minerals = max(0, self._held_minerals - held_minerals)
            self._held_vespene = max(0, self._held_vespene - held_vespene)
            plans.build_committed(key, task.node_id, task.id, task.minerals, task.vespene)
            self._apply_plan_outcomes()

    def _plan_task_finished(self, task: _TaskRecord, status: TaskStatus) -> None:
        plans = self._plans
        key = self._plan_key(task)
        if plans is None or key is None:
            return
        now = self._last_game_seconds
        acknowledged = task.acknowledged_at is not None
        cause = task.failure_cause
        if task.operation in _PLAN_BUILD_OPERATIONS:
            if status == "succeeded":
                plans.build_completed(key, task.node_id, now)
            else:
                # Classified with the runtime's own named cause sets (one source).
                failure: BuildFailure
                if cause is None:
                    failure = "cancelled"
                elif cause in SITE_EXCLUSION_CAUSES:
                    failure = "refused"
                elif cause in ABANDON_CAUSES:
                    failure = "stalled"
                else:
                    failure = "lost"
                plans.build_failed(key, failure, acknowledged, task.reason, now)
        elif status == "failed" and task.lifecycle.tracks_movement and cause != "lost":
            plans.move_failed(key, task.reason, now)  # a lost walker is begin_tick's
        self._apply_plan_outcomes()

    def _apply_plan_outcomes(self) -> None:
        """Emit the store's plan events, then apply what aborts and completions
        released: this tick's held amounts return to the bank at once, and an
        aborted lane's tasks are cancelled -- except an acknowledged build, whose
        structure already stands and continues (D4)."""
        plans = self._plans
        if plans is None:
            return
        while True:
            events = plans.drain_events()
            released = plans.drain_released()
            if not events and not released:
                return
            for event in events:
                self._emit(
                    event.node_id, None, "diagnostic", event.status, event.reason, event.facts
                )
            for item in released:
                self._held_minerals = max(0, self._held_minerals - item.minerals)
                self._held_vespene = max(0, self._held_vespene - item.vespene)
                if not item.cancel_tasks:
                    continue
                for task in list(self._active.values()):
                    if task.plan_id != item.plan_id:
                        continue
                    if task.status == "running" and task.lifecycle.deadline_kind == "build":
                        continue
                    self._finish(task, "cancelled", f"plan {item.plan_id} aborted")
