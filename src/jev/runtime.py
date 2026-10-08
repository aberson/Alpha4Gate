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
  active task. Task IDs are ``run_id:counter``. Mineral/supply commitments of
  unacknowledged tasks and this tick's holds are subtracted before later nodes
  spend; an actor held by a task cannot be taken by another node unless the
  action opts into same-lane ``preempt``.
* **Lifecycle (D4).** Issuance is not success: acknowledgement comes from the
  observation (structure progress, queued order, unit order/position). A rejected
  or unconfirmed command is retried at most three times after its first attempt
  (four attempts in all), at least one game second apart, with a five-second
  acknowledgement timeout; construction/training deadlines after acknowledgement;
  movement replans after 30 game seconds without progress; dead actors fail tasks
  at once; failed intents cool down for ten game seconds with a diagnostic. While
  a build intent cools down after failing placement, its candidate sites are
  excluded from new placement selections, so recovery picks a new site.
* **Adapter hooks.** The SC2 adapter reports what SC2 did with each command:
  :meth:`JevRuntime.mark_command_accepted` (still not success) or
  :meth:`JevRuntime.mark_command_rejected`; :meth:`JevRuntime.task_status` lets it
  ignore late reports for tasks that have already moved on.
* **Memory.** Cross-tick memory is task state plus the ``attack_launched`` latch.
  Bindings live only for one root evaluation in one tick.

Every event and command names its originating node (and task where applicable).
Issuing commands to SC2 is the adapter's job; :meth:`JevRuntime.tick` returns the
command specifications it decided on.
"""

from __future__ import annotations

import itertools
from collections import Counter, deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal, get_args

from jev.contracts import (
    ACTIVE_TASK_STATUSES,
    ENTITY_COLLECTIONS,
    LATCHES,
    LOCATION_SOURCES,
    MAX_ABS_NUMBER,
    MAX_COORDINATE,
    MAX_ENTITY_ORDERS,
    MAX_OBSERVED_ENTITIES,
    MAX_OBSERVED_LOCATIONS,
    MAX_TAG,
    POINT_KEYWORDS,
    RUN_RESULTS,
    RUN_STATUSES,
    CommandSpec,
    Entity,
    Event,
    EventKind,
    EventStatus,
    JevError,
    JsonValue,
    NodeStatus,
    Observation,
    Order,
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
    OP_BUILD,
    OP_GATHER,
    OPERATIONS,
    PARAM_STRUCTURE,
    ActionOp,
    Binding,
    CompiledFilter,
    Intent,
    Lifecycle,
    PredicateOp,
    SelectOp,
    TaskSubject,
    compile_filter,
    decode_arrive_within,
    resolve_args,
)
from jev.policy import policy_hash, validate_policy

__all__ = [
    "COMMAND_BUDGET_REASON",
    "COOLDOWN_CAUSES",
    "FAILURE_CAUSES",
    "FailureCause",
    "JevRuntime",
    "LifecycleConfig",
    "MAX_CONFIG_INT",
    "NODE_BUDGET_REASON",
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
#: Build failures that exclude the task's candidate sites while the intent cools down.
SITE_EXCLUSION_CAUSES: Final[frozenset[FailureCause]] = frozenset({"rejected", "unacknowledged"})
#: Task states whose command is not yet confirmed: cost committed, actor held.
UNACKNOWLEDGED_STATUSES: Final[frozenset[TaskStatus]] = frozenset({"pending", "issued"})
#: Pseudo node id for runtime diagnostics that no policy node originated (it can
#: never collide with an authored id: authored ids must match NODE_ID_RE).
RUNTIME_NODE_ID: Final = "@runtime"
_TIME_EPSILON: Final = 1e-9


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
    for field_name in ("is_structure", "is_flying"):
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
    max_active_tasks: int = 128
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
    acknowledged_at: float | None = None
    next_attempt_at: float | None = None
    last_progress_at: float | None = None
    last_check_at: float | None = None
    last_metric: float | None = None
    tracked_tag: int | None = None
    failure_cause: FailureCause | None = None

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
    status: NodeStatus
    reason: str
    waiting: bool = False


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
        return self._runtime._available_minerals(self._observation)

    def available_supply(self) -> int:
        return self._runtime._available_supply(self._observation)

    def latch_is_set(self, latch: str) -> bool:
        return self._runtime._latches.get(latch, False)

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
        return tag in runtime._reserved_actors or runtime._holder_of(tag) is not None

    def in_flight_builds(self) -> tuple[tuple[str, Point], ...]:
        found: list[tuple[str, Point]] = []
        for task in self._runtime._active.values():
            if task.operation == OP_BUILD and task.committed() and isinstance(task.target, tuple):
                found.append((str(task.params.get(PARAM_STRUCTURE, "")), task.target))
        return tuple(found)

    def gather_assignments(self) -> Mapping[int, int]:
        counts: Counter[int] = Counter()
        for task in self._runtime._active.values():
            if task.operation == OP_GATHER and task.committed() and isinstance(task.target, int):
                counts[task.target] += 1
        return counts

    def rejected_sites(self, structure: str) -> frozenset[Point]:
        now = self._observation.game_seconds
        found: set[Point] = set()
        for kind, sites, until in self._runtime._rejected_sites.values():
            if kind == structure and until > now:
                found.update(sites)
        return frozenset(found)


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
        self._last_tick_seconds: float | None = None
        self._last_game_loop = 0
        self._last_game_seconds = 0.0
        self._seen_game_loop = 0
        self._seen_game_seconds = 0.0
        self._events: list[Event] = []
        self._commands: list[CommandSpec] = []
        self._reserved_actors: set[int] = set()
        self._held_minerals = 0
        self._held_supply = 0
        self._node_status: dict[str, NodeStatus] = {}
        self._waiting_nodes: set[str] = set()
        self._own_unit_tags: frozenset[int] = frozenset()
        self._own_structure_tags: frozenset[int] = frozenset()

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
        self._commands = []
        self._reserved_actors = set()
        self._held_minerals = 0
        self._held_supply = 0
        self._node_status = {}
        self._waiting_nodes = set()
        self._own_unit_tags = frozenset(e.tag for e in observation.own_units)
        self._own_structure_tags = frozenset(e.tag for e in observation.own_structures)
        now = observation.game_seconds
        self._cooldowns = {k: v for k, v in self._cooldowns.items() if v > now}
        self._rejected_sites = {k: v for k, v in self._rejected_sites.items() if v[2] > now}

        self._update_tasks(observation)

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
        for name in ("minerals", "supply_used", "supply_cap"):
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
            },
        )

    # -- resources, actors and task queries --------------------------------------

    def _available_minerals(self, observation: Observation) -> int:
        committed = sum(t.minerals for t in self._active.values() if t.committed())
        return observation.minerals - committed - self._held_minerals

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
        self._terminal_counts[(task.node_id, status)] += 1
        self._task_event(task)
        if task.failure_cause in COOLDOWN_CAUSES:
            until = self._last_game_seconds + self._life.failure_cooldown_seconds
            self._cooldowns[task.intent_key] = until
            excluded: list[JsonValue] = []
            if (
                task.operation == OP_BUILD
                and task.failure_cause in SITE_EXCLUSION_CAUSES
                and isinstance(task.target, tuple)
            ):
                sites = (task.target, *task.alternatives)
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
        if task.last_metric is None:
            task.last_metric = metric
        elif task.last_check_at is not None and (
            now - task.last_check_at >= self._life.progress_check_seconds - _TIME_EPSILON
        ):
            engage_range = decode_arrive_within(task.params)
            improved = metric is not None and metric < task.last_metric - 1.0
            engaged = metric is not None and metric <= engage_range
            if improved or engaged:
                task.last_progress_at = now
            task.last_metric = metric
            task.last_check_at = now
        last_progress = task.last_progress_at if task.last_progress_at is not None else now
        if now - last_progress >= self._life.replan_after_seconds - _TIME_EPSILON:
            self._fail(
                task, "no_progress", f"no progress for {self._life.replan_after_seconds:g}s; replan"
            )

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
            {"intent_key": task.intent_key, "attempt": task.attempts},
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
            return _NodeResult("running", NODE_BUDGET_REASON, waiting=True)
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
        for child in node.children:
            child_result = self._evaluate(child, frame, observation)
            if child_result.status != advance:
                facts["child"] = child
                return _NodeResult(
                    child_result.status,
                    f"child {child} returned {child_result.status}",
                    child_result.waiting,
                )
        if advance == "success":
            return _NodeResult("success", "all children succeeded")
        return _NodeResult("failure", "all children failed")

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
                return _NodeResult("running", f"waiting: {outcome.reason}", waiting=True)
            return _NodeResult("failure", outcome.reason)
        if isinstance(spec, SelectOp):
            selection = spec.evaluate(view, args)
            facts.update(selection.facts)
            name = str(args["bind"])
            facts["bind"] = name
            if selection.binding is None:
                return _NodeResult("failure", selection.reason)  # bindings untouched
            frame.bindings[name] = selection.binding
            return _NodeResult("success", selection.reason)
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
        for intent in plan.intents:
            key = f"{node.id}|{intent.semantic}"
            if key in self._by_intent:
                active += 1
                continue
            if intent.satisfied:
                satisfied += 1
                continue
            refusal = self._refusal(node, frame, view, key, intent, plan.preempt, now)
            if refusal is not None:
                blocked.append(refusal)
                continue
            shortfall = self._shortfall(view, intent)
            if shortfall is not None:
                if plan.hold_reservation:
                    self._held_minerals += max(0, min(intent.minerals, view.available_minerals()))
                    self._held_supply += max(0, min(intent.supply, view.available_supply()))
                    waiting_reason = f"waiting for {shortfall}; holding reservation"
                else:
                    blocked.append(f"insufficient {shortfall}")
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
            return _NodeResult("running", waiting_reason, waiting=True)
        if active:
            return _NodeResult("running", f"{active} task(s) in progress ({issued} issued now)")
        if blocked:
            return _NodeResult("failure", blocked[0])
        if plan.failure_reason is not None:
            return _NodeResult("failure", plan.failure_reason)
        if not plan.intents:
            return _NodeResult("failure", "nothing to act on")
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
        return None

    @staticmethod
    def _shortfall(view: _View, intent: Intent) -> str | None:
        if intent.minerals and intent.minerals > view.available_minerals():
            return "minerals"
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
            status="issued",
            created_at=now,
            issued_at=now,
            attempts=1,
            reason="command issued",
        )
        self._active[task.id] = task
        self._by_intent[key] = task.id
        self._dispatch(task, frame)
