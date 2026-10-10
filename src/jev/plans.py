"""Persistent, inspectable plans for Jev v2 (plan J2 section 5 ``PlanRecord``, D2-D4).

A plan is runtime memory that the policy graph drives -- never a hidden workflow.
Every action it takes is a graph node and a runtime task; this module only keeps
what must outlive a tick: who owns what, how far the plan got, and when it must
give up. :class:`PlanStore` is owned by :class:`jev.runtime.JevRuntime`, which
creates it only for a policy with a ``start_plan`` node (frozen v1 has none, so
nothing here ever runs for v1).

**What section 5 fixes.** The record's fields; ``kind`` (opening / pressure /
recover / expand / tech) and ``status`` (active / waiting / completed / aborted)
exactly as section 5 lists them; "max one macro plan plus one army plan". The
slot of each kind follows the plan's own split: expand and tech are the macro
plans (D3 "one macro plan at a time"; D4 "natural expansion and limited tech"),
opening, pressure and recover the army's (D2's fallback sequence: opening
completion, then recover or pressure after the first launch). ``expand`` has a
graph since Step 217 and ``tech`` (the Cybernetics Core prerequisite) since Step
218; nothing here is specific to the other kinds.

**Expansion and tech share the macro slot (Step 218).** Both are macro plans, so
the store never opens one while the other is open (``start`` refuses it, visibly):
only one plan reservation exists at a time, and that reservation becomes its own
build's commitment when the build issues (transferred, never charged twice).

**Turns: a slot is fair between its kinds (Step 218).** Whose turn it is after an
abort is slot state kept here, never inferred from lane order or from another
kind's latest record. When a plan of kind K aborts, its slot *yields* to the slot's
other kinds that the policy can start (the store's ``kinds``) and that are not
disabled: until :data:`PLAN_YIELD_SECONDS` after the cooldown, ``start`` refuses K
-- visibly -- while another kind may start. The yield ends when any plan starts in
the slot (the other kind took its turn), when the window lapses (no other kind
wanted the slot) or when no other kind is left enabled. So repeated failures of one
kind alternate with the other kind's turns, symmetrically and whatever order the
lanes run in: after its own abort a kind waits at most one cooldown, one plan of
the other kind (its deadlines) and that plan's cooldown -- never until the failing
kind is disabled. A completed plan sets neither a cooldown nor a yield.
:meth:`PlanStore.slot` and every plan event's ``slot_turn`` facts show the turn.

The v2 graph keeps D2's preference -- the Core prerequisite before the expansion --
in its lane order alone: the tech lane runs before the expansion lane and its start
conditions are a subset of the expansion's, so when both could start, tech goes
first; the expansion then waits, refused by the slot, until the tech plan ends.
Fairness after failures is the turn rule above. The other spenders are ordered around
them: supply and construction run before them (D3: they may preempt the
discretionary macro hold, recorded as ``budget_preempted``), production after them
(it spends only what the macro hold leaves, gas included). Every spender sees the
bank minus in-flight commitments and the holds of the lanes before it, so the
macro hold, gas holds and production commitments never overlap.

**Identity and scope.** A plan is started by a ``start_plan`` action node; that
node id is the plan's stable policy identity (every plan operation names it in
its ``plan`` argument) and plan ids are ``run_id:plan:counter``, monotonic per
run (section 5). A plan's *lane* is the root lane holding its start node (at most
one start node per lane, enforced by policy validation), so the lane's nodes are
its stages.

**Stage.** After the lane is evaluated each tick the runtime reports the leaf that
decided the lane's status (the *frontier*): that node is the plan's
``stage_node_id``, its status sets ``active`` (in progress) or ``waiting`` (the
node failed or waits), and its code the ``reason_code``. Reaching a node further
along the lane (pre-order) is progress; so is a reservation that grows, and any
task of the lane that is still active. ``blocker`` is the frontier node's reason
while the plan waits (rendered, capped).

**Site (D4).** ``select_expansion`` commits the nearest *reachable* unoccupied
expansion by SC2 path distance (Euclidean distance only bounds which at most
eight candidates are path-queried), and the record keeps the choice inspectable:
``site_path_distance`` and ``site_path_rank`` (0: the nearest reachable candidate
of all, 1: the next ...). Taking another than the nearest is bounded: a site whose
path is more than the node's ``max_path_ratio`` times the nearest reachable one's
is never taken -- the plan waits (and so, bounded by its deadlines, aborts) rather
than walk to a far base. A site SC2 refused placement on is given up at once
(``avoided_sites``) and the selection re-chooses within that bound, or the plan
waits visibly. Unknown is not safe: the build waits until the site is in the
bot's own sight (``plan_site_seen``), so its safety is rechecked on fresh vision.

**Ownership (D3).** While a plan is open it may own one actor (a worker) and a
site. The runtime treats a plan's actor as busy for every other lane, so no other
node selects or commands it, and refuses any intent on it from another lane.
What the lane holds each tick (its ``plan_reserve`` node: up to the next
structure's cost, after the lanes before it) is the plan's reservation, so the
lanes after it cannot spend it; when the lane's build task is created the
reservation becomes the task's commitment (transferred, never charged twice).

**Lifecycle (D3).** At most one open plan per slot, so expansion and tech can
never both spend. Limits:

* :data:`PLAN_TIMEOUT_SECONDS` -- the plan must get its structure started (its
  build acknowledged) within it, and finish within it after that;
* :data:`PLAN_NO_PROGRESS_SECONDS` without progress is one recovery attempt;
* at most :data:`MAX_STAGE_RECOVERIES` recovery attempts per stage (a lost worker,
  a refused placement, a stalled walk, a re-chosen site, no progress); the next
  one aborts the plan;
* an abort releases every hold (actor, site, minerals, vespene), cancels the
  lane's tasks that have not started a structure (no retry survives from an
  abandoned branch) and cools the slot down for :data:`PLAN_COOLDOWN_SECONDS`.
  A plan aborted over its site (blocked, threatened or occupied), and every site
  SC2 refused, stays avoided by the next plans for :data:`PLAN_SITE_AVOID_SECONDS`;
* the aggregate is bounded too: a failure that is a static map fact (no reachable
  or no candidate site) disables the kind for the match at once, and so does the
  :data:`MAX_PLAN_ABORTS`-th abort of a kind (``abort_limit``), so a kind is never
  retried without end.

While the lane's build command is in flight (issued or awaiting a retry), the
plan neither times out nor aborts: SC2's answer arrives within the task's own
acknowledgement and retry bounds and decides the outcome (D4: abandonment stops
an *unissued* plan; there is no in-game building cancel, and a started structure
continues unless destroyed).

**The second base.** Its acknowledgement (the Nexus stands, any build progress)
releases the worker and makes ``second_base_defense`` true; the flag follows the
live state from then on (:meth:`PlanStore.observe_sites`), so it turns false
whenever that Nexus is gone -- also after the plan has finished. Workers transfer
only once the Nexus is *ready* (they cannot return cargo to an unfinished one):
the graph waits for construction between acknowledgement and transfer.

**Evidence (D5).** Every transition is returned as a :class:`PlanEvent` the
runtime emits as a ``diagnostic`` event at once, and every open plan is summarized
once per game second, so the latest plan state survives the bounded recent-event
window. Facts carry the plan id, stage node, status, reason code and holds.

**Strategic choices (Step 219).** The D2 ``strategy`` question chooses among the
plan kinds except the fixed opening (:data:`STRATEGY_CHOICES`); a choice's slot is
what it decides: a macro choice (expand, tech) which macro plan the slot opens, an
army choice (pressure, recover) the army's posture. What the graph's strategy
guards read each tick is one :class:`StrategySlot` per slot: the local fallback
(the graph's own conditions decide) or a hosted choice -- possibly none, which
holds the slot's plan starts back. A plan records which strategic decision
started it (``decided_by``, ``decision_request_id``: request-to-plan evidence), and
:meth:`PlanStore.refusal` says, without starting anything, whether a kind could
start now -- the same check :meth:`PlanStore.start` applies.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter, deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Final, Literal, get_args

from jev.contracts import JsonValue, Point, render_text

__all__ = [
    "MAX_AVOIDED_SITES",
    "MAX_BLOCKER_CHARS",
    "MAX_FINISHED_PLANS",
    "MAX_PLAN_ABORTS",
    "MAX_STAGE_RECOVERIES",
    "OPEN_PLAN_STATUSES",
    "PLAN_CODES",
    "PLAN_COOLDOWN_SECONDS",
    "PLAN_KINDS",
    "PLAN_NO_PROGRESS_SECONDS",
    "PLAN_SITE_AVOID_SECONDS",
    "PLAN_SLOT",
    "PLAN_SLOTS",
    "PLAN_STATUSES",
    "PLAN_TIMEOUT_SECONDS",
    "PLAN_YIELD_SECONDS",
    "SITE_TROUBLE",
    "STATIC_FAILURES",
    "STRATEGY_CHOICES",
    "STRATEGY_SOURCES",
    "WAIT_CODES",
    "BuildFailure",
    "PlanCode",
    "PlanEvent",
    "PlanEventStatus",
    "PlanKind",
    "PlanRecord",
    "PlanSlot",
    "PlanStatus",
    "PlanStore",
    "Released",
    "SlotRecord",
    "StrategyChoice",
    "StrategySlot",
    "StrategySource",
    "WaitCode",
]

#: Section 5 PlanRecord ``kind``, verbatim.
PlanKind = Literal["opening", "pressure", "recover", "expand", "tech"]
PLAN_KINDS: Final[tuple[PlanKind, ...]] = get_args(PlanKind)
#: Section 5 PlanRecord ``status``, verbatim.
PlanStatus = Literal["active", "waiting", "completed", "aborted"]
PLAN_STATUSES: Final[tuple[PlanStatus, ...]] = get_args(PlanStatus)
#: A plan in one of these statuses is open: it owns its holds and blocks its slot.
OPEN_PLAN_STATUSES: Final[frozenset[PlanStatus]] = frozenset({"active", "waiting"})
#: Section 5: "max one macro plan plus one army plan" (the module docstring says
#: which kind is which).
PlanSlot = Literal["macro", "army"]
PLAN_SLOTS: Final[tuple[PlanSlot, ...]] = get_args(PlanSlot)
PLAN_SLOT: Final[Mapping[PlanKind, PlanSlot]] = {
    "opening": "army",
    "pressure": "army",
    "recover": "army",
    "expand": "macro",
    "tech": "macro",
}
#: The D2 ``strategy`` question's vocabulary: every plan kind but the fixed opening
#: (module docstring: Strategic choices). Which of them a policy can execute is the
#: policy's own (``jev.strategy.strategy_branches``).
StrategyChoice = Literal["pressure", "recover", "expand", "tech"]
STRATEGY_CHOICES: Final[tuple[StrategyChoice, ...]] = get_args(StrategyChoice)
#: Who decides a slot's strategic choice: the graph's own conditions with no hosted
#: provider (``local_graph``), the graph's conditions because the hosted answer is
#: unavailable, failed or refused (``local_fallback``), or a hosted answer
#: (``typesafe``).
StrategySource = Literal["local_graph", "local_fallback", "typesafe"]
STRATEGY_SOURCES: Final[tuple[StrategySource, ...]] = get_args(StrategySource)


@dataclass(frozen=True)
class StrategySlot:
    """What the strategy guards of one slot read this tick (module docstring).

    ``local``: the graph's own fallback conditions decide (``strategy_local``
    passes). Otherwise a hosted decision holds the slot: ``choice`` is the hosted
    choice in force (``strategy_is`` passes for it alone), or None -- no plan of the
    slot starts while the hosted question decides. ``request_id`` is the request
    whose answer is in force (None when local or none is)."""

    local: bool = True
    choice: StrategyChoice | None = None
    request_id: int | None = None
    source: StrategySource = "local_graph"

    def __post_init__(self) -> None:
        if self.choice is not None and self.choice not in STRATEGY_CHOICES:
            raise ValueError(f"unknown strategic choice {self.choice!r}")
        if self.source not in STRATEGY_SOURCES:
            raise ValueError(f"unknown strategy source {self.source!r}")
        if self.local and (self.choice is not None or self.source == "typesafe"):
            raise ValueError("a local strategy slot holds no hosted choice")
        if not self.local and self.source != "typesafe":
            raise ValueError("a hosted strategy slot is decided by the hosted provider")

    def facts(self) -> dict[str, JsonValue]:
        return {
            "local": self.local,
            "choice": self.choice,
            "request_id": self.request_id,
            "source": self.source,
        }


#: Fixed reason codes (D5: never free text). The frontier's code, a transition's
#: code or an outcome's code; ``blocker`` carries the human reason beside it.
PlanCode = Literal[
    "started",
    "in_progress",
    "waiting",
    "blocked",
    "path_pending",
    "site_chosen",
    "worker_assigned",
    "no_site",
    "site_unreachable",
    "site_occupied",
    "site_blocked",
    "site_threatened",
    "site_unscouted",
    "home_threatened",
    "prerequisite_missing",
    "no_worker",
    "worker_lost",
    "move_stalled",
    "budget_short",
    "budget_preempted",
    "base_cap",
    "issued",
    "acknowledged",
    "constructed",
    "nexus_lost",
    "structure_lost",
    "no_progress",
    "timeout",
    "recoveries_exhausted",
    "abort_limit",
    "completed",
]
PLAN_CODES: Final[tuple[PlanCode, ...]] = get_args(PlanCode)
#: The codes a ``plan_wait`` node may name: why the plan holds still on that branch
#: (the node Step 220's expansion_response will decide at).
WaitCode = Literal[
    "site_threatened", "home_threatened", "site_blocked", "budget_short", "prerequisite_missing"
]
WAIT_CODES: Final[tuple[WaitCode, ...]] = get_args(WaitCode)
#: Failures that are static map facts: retrying cannot help, so the kind is disabled.
STATIC_FAILURES: Final[frozenset[PlanCode]] = frozenset({"no_site", "site_unreachable"})
#: Blockers at the plan's site: an abort over one leaves the site avoided for a while.
SITE_TROUBLE: Final[frozenset[PlanCode]] = frozenset(
    {"site_blocked", "site_threatened", "site_occupied"}
)
#: How the lane's build task failed, as the runtime classifies its failure cause
#: with its own named cause sets: SC2 refused the placement (``refused``), it never
#: started or finished in time (``stalled``), its actor or structure was ``lost``,
#: or it was ``cancelled``.
BuildFailure = Literal["refused", "stalled", "lost", "cancelled"]

#: D3: the structure must be started within this many game seconds of the plan's
#: start, and the plan must finish within as many after that.
PLAN_TIMEOUT_SECONDS: Final = 120.0
#: D3: this long without progress is one recovery attempt.
PLAN_NO_PROGRESS_SECONDS: Final = 30.0
#: D3: recovery attempts per stage; the next one aborts the plan.
MAX_STAGE_RECOVERIES: Final = 3
#: D3: a slot stays closed this long after an abort.
PLAN_COOLDOWN_SECONDS: Final = 15.0
#: Aborts of one kind a match allows; the last disables the kind (``abort_limit``).
MAX_PLAN_ABORTS: Final = 3
#: After an abort, the slot's other kinds' turn lasts this long beyond the cooldown:
#: the aborted kind may not start again before that unless another kind started, or
#: none is left enabled (module docstring: Turns). Many ticks for an eligible kind.
PLAN_YIELD_SECONDS: Final = 5.0
#: A site a plan was aborted over, or SC2 refused, is avoided by later plans this long.
PLAN_SITE_AVOID_SECONDS: Final = 120.0
#: Avoided sites remembered at once (oldest dropped).
MAX_AVOIDED_SITES: Final = 16
#: Finished plans kept for inspection (oldest dropped).
MAX_FINISHED_PLANS: Final = 16
MAX_BLOCKER_CHARS: Final = 160

#: Diagnostic event statuses a plan event uses.
PlanEventStatus = Literal["running", "warning", "failed", "succeeded"]


@dataclass(frozen=True)
class PlanRecord:
    """Read-only snapshot of one plan (section 5 PlanRecord, plus inspection fields).

    ``node_id`` is the start node (the plan's policy identity) and ``root_node_id``
    its lane. ``deadline_game_seconds`` is when the plan times out unless its build
    command is in flight. ``site_path_distance``/``site_path_rank`` show how the
    site was chosen (module docstring: Site). ``second_base_defense`` is true while
    an expansion's acknowledged Nexus stands: the army lanes may defend it.
    """

    plan_id: str
    node_id: str
    root_node_id: str
    kind: PlanKind
    status: PlanStatus
    stage_node_id: str
    site: Point | None
    actor_tag: int | None
    started_game_seconds: float
    last_progress_game_seconds: float
    deadline_game_seconds: float
    reserved_minerals: int
    reserved_vespene: int
    reason_code: PlanCode
    blocker: str | None = None
    recoveries: int = 0
    acknowledged_game_seconds: float | None = None
    finished_game_seconds: float | None = None
    second_base_defense: bool = False
    avoided_sites: tuple[Point, ...] = ()
    #: The lane's build command is issued or awaiting a retry (its task commits the cost).
    build_in_flight: bool = False
    site_path_distance: float | None = None
    site_path_rank: int | None = None
    #: Which strategic decision started the plan (module docstring: Strategic
    #: choices): its source, and the hosted request when one did.
    decided_by: StrategySource | None = None
    decision_request_id: int | None = None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_PLAN_STATUSES

    def facts(self) -> dict[str, JsonValue]:
        """The bounded D5 facts of this snapshot (tags as strings, points as lists)."""
        site = self.site
        return {
            "plan_id": self.plan_id,
            "plan_node": self.node_id,
            "plan_root": self.root_node_id,
            "plan_kind": self.kind,
            "plan_status": self.status,
            "stage_node_id": self.stage_node_id,
            "reason_code": self.reason_code,
            "blocker": self.blocker,
            "site": None if site is None else [site[0], site[1]],
            "site_path_distance": self.site_path_distance,
            "site_path_rank": self.site_path_rank,
            "actor_tag": None if self.actor_tag is None else str(self.actor_tag),
            "started_game_seconds": self.started_game_seconds,
            "last_progress_game_seconds": self.last_progress_game_seconds,
            "deadline_game_seconds": self.deadline_game_seconds,
            "reserved_minerals": self.reserved_minerals,
            "reserved_vespene": self.reserved_vespene,
            "recoveries": self.recoveries,
            "acknowledged_game_seconds": self.acknowledged_game_seconds,
            "finished_game_seconds": self.finished_game_seconds,
            "second_base_defense": self.second_base_defense,
            "avoided_sites": [[p[0], p[1]] for p in self.avoided_sites],
            "build_in_flight": self.build_in_flight,
            "decided_by": self.decided_by,
            "decision_request_id": self.decision_request_id,
        }


@dataclass(frozen=True)
class PlanEvent:
    """One plan transition or summary for the runtime to emit (a diagnostic event
    attributed to the plan's stage node)."""

    node_id: str
    status: PlanEventStatus
    reason: str
    facts: Mapping[str, JsonValue]


@dataclass(frozen=True)
class Released:
    """What an abort or completion released, for the runtime to apply this tick:
    the plan's lane (its unstarted tasks are cancelled) and this tick's holds."""

    plan_id: str
    root_node_id: str
    minerals: int
    vespene: int
    cancel_tasks: bool


@dataclass(frozen=True)
class SlotRecord:
    """Read-only turn state of one plan slot (module docstring: Turns).

    ``yield_from`` is the kind that aborted and must wait its turn until
    ``yield_until`` while another of ``yield_to`` (the slot's other startable,
    enabled kinds) may start; all None/empty when nobody is owed a turn.
    ``cooldown_until`` is the slot's latest cooldown end (a time, possibly past).
    """

    slot: PlanSlot
    open_plan_id: str | None
    open_kind: PlanKind | None
    cooldown_until: float | None
    yield_from: PlanKind | None
    yield_to: tuple[PlanKind, ...]
    yield_until: float | None
    disabled: tuple[tuple[PlanKind, PlanCode], ...]

    def facts(self) -> dict[str, JsonValue]:
        return {
            "slot": self.slot,
            "open_plan_id": self.open_plan_id,
            "open_kind": self.open_kind,
            "cooldown_until": self.cooldown_until,
            "yield_from": self.yield_from,
            "yield_to": list(self.yield_to),
            "yield_until": self.yield_until,
            "disabled": {kind: code for kind, code in self.disabled},
        }


@dataclass
class _Plan:
    """Mutable state behind one :class:`PlanRecord`."""

    plan_id: str
    node_id: str
    root_node_id: str
    kind: PlanKind
    status: PlanStatus
    stage_node_id: str
    started: float
    last_progress: float
    deadline: float
    reason_code: PlanCode
    site: Point | None = None
    site_path: float | None = None
    site_rank: int | None = None
    actor_tag: int | None = None
    reserved_minerals: int = 0
    reserved_vespene: int = 0
    tick_minerals: int = 0
    tick_vespene: int = 0
    blocker: str | None = None
    recoveries: int = 0
    furthest: int = -1
    acknowledged: float | None = None
    acknowledged_site: Point | None = None
    finished: float | None = None
    in_flight: bool = False
    second_base_defense: bool = False
    summary_second: int = -1
    avoided_sites: tuple[Point, ...] = ()
    refused_sites: tuple[Point, ...] = ()
    decided_by: StrategySource | None = None
    decision_request_id: int | None = None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_PLAN_STATUSES

    def snapshot(self) -> PlanRecord:
        return PlanRecord(
            plan_id=self.plan_id,
            node_id=self.node_id,
            root_node_id=self.root_node_id,
            kind=self.kind,
            status=self.status,
            stage_node_id=self.stage_node_id,
            site=self.site,
            actor_tag=self.actor_tag,
            started_game_seconds=self.started,
            last_progress_game_seconds=self.last_progress,
            deadline_game_seconds=self.deadline,
            reserved_minerals=self.reserved_minerals,
            reserved_vespene=self.reserved_vespene,
            reason_code=self.reason_code,
            blocker=self.blocker,
            recoveries=self.recoveries,
            acknowledged_game_seconds=self.acknowledged,
            finished_game_seconds=self.finished,
            second_base_defense=self.second_base_defense,
            avoided_sites=self.avoided_sites,
            build_in_flight=self.in_flight,
            site_path_distance=self.site_path,
            site_path_rank=self.site_rank,
            decided_by=self.decided_by,
            decision_request_id=self.decision_request_id,
        )


def _blocker(text: str) -> str:
    return render_text(text)[:MAX_BLOCKER_CHARS]


class PlanStore:
    """Every plan of one run: open plans, the latest per start node and a bounded
    history (see the module docstring). All methods are O(kept plans) or O(1)."""

    def __init__(self, run_id: str, kinds: Iterable[PlanKind] = PLAN_KINDS) -> None:
        """``kinds``: the plan kinds the policy can start (its ``start_plan`` nodes);
        after an abort a slot yields only to another of these."""
        self._run_id = run_id
        self._kinds: frozenset[PlanKind] = frozenset(kinds)
        # Slot -> (the kind that aborted, the game time its yield ends): Turns.
        self._yield: dict[PlanSlot, tuple[PlanKind, float]] = {}
        self._counter = itertools.count(1)
        self._open: dict[str, _Plan] = {}  # start node id -> its open plan
        self._latest: dict[str, _Plan] = {}  # start node id -> its latest plan
        self._finished: deque[_Plan] = deque(maxlen=MAX_FINISHED_PLANS)
        self._cooldown: dict[PlanSlot, float] = {}
        self._disabled: dict[PlanKind, PlanCode] = {}
        self._kind_aborts: Counter[PlanKind] = Counter()
        self._avoid: dict[Point, float] = {}  # site -> avoided until (game seconds)
        self._events: list[PlanEvent] = []
        self._released: list[Released] = []
        self._aborts = 0

    # -- reading ------------------------------------------------------------------

    def record(self, node_id: str) -> PlanRecord | None:
        """The latest plan started by start node ``node_id`` (open or finished)."""
        plan = self._latest.get(node_id)
        return None if plan is None else plan.snapshot()

    def records(self) -> tuple[PlanRecord, ...]:
        """Finished plans still kept (oldest first), then the open ones."""
        return (
            *(plan.snapshot() for plan in self._finished),
            *(plan.snapshot() for plan in self._open.values()),
        )

    def open_plans(self) -> Iterator[tuple[str, str]]:
        """``(start node id, lane root)`` of every open plan."""
        for key, plan in self._open.items():
            yield key, plan.root_node_id

    def actor_owner(self, tag: int) -> tuple[str, str, str] | None:
        """``(plan id, start node id, lane root)`` of the open plan owning ``tag``."""
        for key, plan in self._open.items():
            if plan.actor_tag == tag:
                return plan.plan_id, key, plan.root_node_id
        return None

    def open_in_lane(self, root_id: str) -> str | None:
        """The start node id of the open plan whose lane is ``root_id``, if any."""
        for key, plan in self._open.items():
            if plan.root_node_id == root_id:
                return key
        return None

    def slot(self, slot: PlanSlot) -> SlotRecord:
        """The turn state of ``slot`` (module docstring: Turns)."""
        holder = next((p for p in self._open.values() if PLAN_SLOT[p.kind] == slot), None)
        turn = self._yield.get(slot)
        yield_from = None if turn is None else turn[0]
        return SlotRecord(
            slot=slot,
            open_plan_id=None if holder is None else holder.plan_id,
            open_kind=None if holder is None else holder.kind,
            cooldown_until=self._cooldown.get(slot),
            yield_from=yield_from,
            yield_to=() if yield_from is None else self._others(yield_from),
            yield_until=None if turn is None else turn[1],
            disabled=tuple(
                (kind, self._disabled[kind])
                for kind in PLAN_KINDS
                if kind in self._disabled and PLAN_SLOT[kind] == slot
            ),
        )

    def slots(self) -> tuple[SlotRecord, ...]:
        """Every slot's turn state, macro first."""
        return (self.slot("macro"), self.slot("army"))

    @property
    def aborts(self) -> int:
        """Plans aborted so far this run (plan D6 metric)."""
        return self._aborts

    def drain_events(self) -> list[PlanEvent]:
        events, self._events = self._events, []
        return events

    def drain_released(self) -> list[Released]:
        released, self._released = self._released, []
        return released

    # -- starting -------------------------------------------------------------------

    def refusal(self, kind: PlanKind, now: float) -> str | None:
        """Why a plan of ``kind`` could not start at ``now`` (None: it could): its slot
        already holds an open plan (never a duplicate), cools down after an abort or
        owes its turn to another kind, or the kind is disabled for the match. The one
        check :meth:`start` applies; reading it changes nothing."""
        disabled = self._disabled.get(kind)
        if disabled is not None:
            return f"{kind} plans are disabled for this match ({disabled})"
        slot = PLAN_SLOT[kind]
        for other in self._open.values():
            if PLAN_SLOT[other.kind] == slot:
                return (
                    f"the {slot} slot already holds open plan {other.plan_id} ({other.kind}); "
                    "never a duplicate plan"
                )
        until = self._cooldown.get(slot)
        if until is not None and now < until:
            return f"the {slot} slot cools down until {until:.2f}s after an abort"
        turn = self._yield.get(slot)
        if turn is not None and turn[0] == kind and now < turn[1]:
            others = self._others(kind)
            if others:
                return (
                    f"the {slot} slot yields to {', '.join(others)} until {turn[1]:.2f}s after "
                    f"a {kind} abort: one turn each"
                )
        return None

    def start(
        self,
        node_id: str,
        root_id: str,
        kind: PlanKind,
        now: float,
        *,
        decided_by: StrategySource | None = None,
        decision_request_id: int | None = None,
    ) -> tuple[PlanRecord | None, str]:
        """Open a plan for start node ``node_id``; ``(record, reason)``, or ``(None,
        refusal)`` for :meth:`refusal`'s reasons. ``decided_by`` and
        ``decision_request_id`` record the strategic decision that started it."""
        refused = self.refusal(kind, now)
        if refused is not None:
            return None, refused
        slot = PLAN_SLOT[kind]
        self._yield.pop(slot, None)  # a start in the slot ends any yield
        plan = _Plan(
            plan_id=f"{self._run_id}:plan:{next(self._counter)}",
            node_id=node_id,
            root_node_id=root_id,
            kind=kind,
            status="active",
            stage_node_id=node_id,
            started=now,
            last_progress=now,
            deadline=now + PLAN_TIMEOUT_SECONDS,
            reason_code="started",
            avoided_sites=tuple(sorted(p for p, until in self._avoid.items() if until > now)),
            decided_by=decided_by,
            decision_request_id=decision_request_id,
        )
        self._open[node_id] = plan
        self._latest[node_id] = plan
        self._event(plan, "running", f"{kind} plan {plan.plan_id} started")
        return plan.snapshot(), f"started {kind} plan {plan.plan_id}"

    # -- each tick ------------------------------------------------------------------

    def begin_tick(self, now: float, own_units: frozenset[int], busy: Mapping[str, bool]) -> None:
        """Before the lanes: a lost actor is a recovery attempt; then deadlines and
        the no-progress clock (deferred while the lane's build command is in
        flight; any active task of the lane is progress). ``busy`` maps start node
        ids to whether their lane has an active task."""
        for plan in list(self._open.values()):
            plan.tick_minerals = plan.tick_vespene = 0
            if plan.actor_tag is not None and plan.actor_tag not in own_units:
                lost = plan.actor_tag
                plan.actor_tag = None
                self._recover(
                    plan, "worker_lost", f"worker {lost} lost; a replacement is chosen", now
                )
            if not plan.is_open or plan.in_flight:
                continue
            if busy.get(plan.node_id, False):
                plan.last_progress = now
            if now >= plan.deadline:
                phase = "finish" if plan.acknowledged is not None else "start its structure"
                self._abort(
                    plan, "timeout", f"did not {phase} within {PLAN_TIMEOUT_SECONDS:g}s", now
                )
            elif now - plan.last_progress >= PLAN_NO_PROGRESS_SECONDS:
                code = plan.reason_code
                plan.last_progress = now  # the next attempt is a full window later
                self._recover(
                    plan,
                    "no_progress",
                    f"no progress for {PLAN_NO_PROGRESS_SECONDS:g}s while {code}",
                    now,
                )

    def observe_sites(self, stands: Callable[[Point], bool]) -> None:
        """Keep ``second_base_defense`` true to the live state: for every kept
        expansion plan whose structure was acknowledged, whether it still
        ``stands`` at its site (the runtime's own observation). A change -- the
        second base destroyed, open plan or finished -- is a transition event."""
        seen: set[str] = set()
        for plan in (*self._latest.values(), *self._finished):
            if plan.plan_id in seen or plan.kind != "expand" or plan.acknowledged_site is None:
                continue
            seen.add(plan.plan_id)
            present = stands(plan.acknowledged_site)
            if present == plan.second_base_defense:
                continue
            plan.second_base_defense = present
            if present:
                self._event(plan, "running", "the second base stands again")
            else:
                self._event(
                    plan,
                    "warning",
                    "the second base is gone: second-base defense no longer applies",
                    transition="nexus_lost",
                )

    def tick_hold(self, node_id: str) -> tuple[int, int]:
        """``(minerals, vespene)`` the open plan's lane holds this tick so far."""
        plan = self._open.get(node_id)
        return (0, 0) if plan is None else (plan.tick_minerals, plan.tick_vespene)

    def hold(self, node_id: str, minerals: int, vespene: int) -> None:
        """The plan's lane held these amounts this tick (its reservation)."""
        plan = self._open.get(node_id)
        if plan is not None:
            plan.tick_minerals += minerals
            plan.tick_vespene += vespene

    def after_lane(
        self,
        node_id: str,
        *,
        stage: str,
        rank: int,
        failed: bool,
        waiting: bool,
        code: PlanCode,
        reason: str,
        now: float,
        spent_before: tuple[str, ...] = (),
    ) -> None:
        """Record the lane's frontier (module docstring: Stage) and this tick's
        reservation; a static map failure aborts at once (no in-flight build).
        ``waiting`` means the plan holds still (its frontier waits on something no
        task of the lane is progressing)."""
        plan = self._open.get(node_id)
        if plan is None:
            return
        previous = (plan.status, plan.stage_node_id, plan.reason_code)
        if plan.tick_minerals > plan.reserved_minerals or plan.tick_vespene > plan.reserved_vespene:
            plan.last_progress = now  # the reservation grew
        elif (
            plan.tick_minerals < plan.reserved_minerals
            and code == "budget_short"
            and stage == plan.stage_node_id
        ):
            # D3: an earlier lane (supply, construction) spent first and preempted
            # the discretionary macro hold; say who.
            self._event(
                plan,
                "warning",
                f"reservation cut from {plan.reserved_minerals} to {plan.tick_minerals} "
                "minerals by earlier lanes",
                transition="budget_preempted",
                extra={"preempted_by": list(spent_before)},
            )
        plan.reserved_minerals = plan.tick_minerals
        plan.reserved_vespene = plan.tick_vespene
        if rank > plan.furthest:
            plan.furthest = rank
            plan.last_progress = now
            plan.recoveries = 0  # a new stage gets its own recovery attempts
        plan.stage_node_id = stage
        plan.status = "waiting" if failed or waiting else "active"
        plan.reason_code = code
        plan.blocker = _blocker(reason) if plan.status == "waiting" else None
        if code in STATIC_FAILURES and not plan.in_flight:
            self._abort(plan, code, reason, now)
            return
        if (plan.status, plan.stage_node_id, plan.reason_code) != previous:
            self._event(plan, "running", f"{plan.kind} plan at {stage}: {plan.reason_code}")

    def summarize(self, now: float) -> None:
        """One summary per open plan per game second (D5)."""
        second = math.floor(now)
        for plan in self._open.values():
            if second > plan.summary_second:
                plan.summary_second = second
                self._event(plan, "running", f"{plan.kind} plan summary", summary=True)

    # -- effects of plan operations ------------------------------------------------------

    def set_site(
        self,
        node_id: str,
        site: Point,
        now: float,
        *,
        replaced: PlanCode | None,
        path_distance: float | None = None,
        path_rank: int | None = None,
    ) -> None:
        """Commit ``site``, with its path distance and rank (module docstring: Site);
        ``replaced`` names why a previous site was given up (a recovery attempt).

        The recovery runs while the plan still holds the old site: if it is the one
        that exhausts the stage, the abort avoids the site that caused the trouble,
        and the new site -- never attempted -- is not committed (nor avoided)."""
        plan = self._open.get(node_id)
        if plan is None or plan.site == site:
            return
        old = plan.site
        if replaced is not None and old is not None:
            self._recover(plan, replaced, f"site {old[0]:g},{old[1]:g} given up", now)
            if not plan.is_open:
                return
        plan.site, plan.site_path, plan.site_rank = site, path_distance, path_rank
        if plan.is_open:
            self._event(
                plan,
                "running",
                f"site {site[0]:g},{site[1]:g} chosen (path rank {path_rank})",
                transition="site_chosen",
            )

    def claim_actor(self, node_id: str, tag: int) -> None:
        plan = self._open.get(node_id)
        if plan is None or plan.actor_tag == tag:
            return
        plan.actor_tag = tag
        self._event(
            plan, "running", f"worker {tag} assigned to the plan", transition="worker_assigned"
        )

    def complete(self, node_id: str, now: float) -> str | None:
        """Complete the open plan; None, or why it cannot complete."""
        plan = self._open.get(node_id)
        if plan is None:
            return "no open plan to complete"
        if plan.in_flight:
            return "a build command of the plan is still in flight"
        self._finish(plan, "completed", "completed", now)
        self._event(plan, "succeeded", f"{plan.kind} plan {plan.plan_id} completed")
        return None

    def abort(self, node_id: str, code: PlanCode, reason: str, now: float) -> bool:
        """Abort the open plan (the hook Step 220's abandon will use). Refused, and
        False, while its build command is in flight (module docstring)."""
        plan = self._open.get(node_id)
        if plan is None or plan.in_flight:
            return False
        self._abort(plan, code, reason, now)
        return True

    # -- task hooks (the plan lane's tasks) ----------------------------------------------

    def build_committed(
        self, node_id: str, task_node: str, task_id: str, minerals: int, vespene: int
    ) -> None:
        """The lane's build task was created: the reservation became its commitment."""
        plan = self._open.get(node_id)
        if plan is None:
            return
        plan.in_flight = True
        held = (plan.tick_minerals, plan.tick_vespene)
        plan.tick_minerals = plan.tick_vespene = 0
        self._event(
            plan,
            "running",
            f"build issued as task {task_id}; {minerals} minerals and {vespene} vespene "
            "now committed to it",
            transition="issued",
            extra={"task_id": task_id, "held_before": list(held)},
            at=task_node,
        )

    def build_acknowledged(self, node_id: str, task_node: str, now: float) -> None:
        """The structure stands (any build progress): the build is no longer in flight,
        the worker is released and, for an expansion, the second base is defensible."""
        plan = self._open.get(node_id)
        if plan is None:
            return
        plan.in_flight = False
        plan.acknowledged = now
        plan.acknowledged_site = plan.site
        plan.deadline = now + PLAN_TIMEOUT_SECONDS
        plan.last_progress = now
        released = plan.actor_tag
        plan.actor_tag = None
        if plan.kind == "expand":
            plan.second_base_defense = True
        self._event(
            plan,
            "running",
            "structure acknowledged by observation; worker released",
            transition="acknowledged",
            extra={"released_actor": None if released is None else str(released)},
            at=task_node,
        )

    def build_completed(self, node_id: str, task_node: str, now: float) -> None:
        plan = self._open.get(node_id)
        if plan is not None:
            plan.last_progress = now
            self._event(
                plan, "running", "construction complete", transition="constructed", at=task_node
            )

    def build_failed(
        self, node_id: str, failure: BuildFailure, acknowledged: bool, reason: str, now: float
    ) -> None:
        """The lane's build task failed. Refused placement: the site is given up at
        once (avoided for the rest of the plan and, after an abort, by the next
        plans), a recovery attempt, and the selection re-chooses within its bound.
        A structure destroyed after acknowledgement, or one that missed its
        deadline, aborts. A worker lost before acknowledgement is begin_tick's."""
        plan = self._open.get(node_id)
        if plan is None:
            return
        plan.in_flight = False
        if failure == "cancelled":
            return
        if acknowledged:
            plan.second_base_defense = False
            lost: PlanCode = "nexus_lost" if plan.kind == "expand" else "structure_lost"
            self._abort(plan, lost if failure == "lost" else "timeout", reason, now)
        elif failure == "refused":
            site = plan.site
            if site is not None:
                plan.refused_sites = (*plan.refused_sites, site)
                plan.avoided_sites = (*plan.avoided_sites, site)
                plan.site = plan.site_path = plan.site_rank = None
                reason = f"{reason}; site {site[0]:g},{site[1]:g} given up"
            self._recover(plan, "site_blocked", reason, now)
        elif failure == "stalled":
            self._recover(plan, "no_progress", reason, now)

    def move_failed(self, node_id: str, reason: str, now: float) -> None:
        """A walk of the lane gave up (D4 replan) or was refused: a recovery attempt."""
        plan = self._open.get(node_id)
        if plan is not None:
            self._recover(plan, "move_stalled", reason, now)

    # -- internals ------------------------------------------------------------------

    def _others(self, kind: PlanKind) -> tuple[PlanKind, ...]:
        """The other kinds of ``kind``'s slot the policy can start, not disabled."""
        slot = PLAN_SLOT[kind]
        return tuple(
            other
            for other in PLAN_KINDS
            if other != kind
            and PLAN_SLOT[other] == slot
            and other in self._kinds
            and other not in self._disabled
        )

    def _recover(self, plan: _Plan, code: PlanCode, reason: str, now: float) -> None:
        if not plan.is_open:
            return
        if plan.recoveries >= MAX_STAGE_RECOVERIES:
            self._abort(
                plan,
                "recoveries_exhausted",
                f"{code} after {plan.recoveries} recovery attempts at {plan.stage_node_id}: "
                f"{reason}",
                now,
                cause=code,
            )
            return
        plan.recoveries += 1
        self._event(
            plan,
            "warning",
            f"recovery attempt {plan.recoveries}/{MAX_STAGE_RECOVERIES} at "
            f"{plan.stage_node_id}: {reason}",
            transition=code,
        )

    def _avoid_site(self, site: Point, now: float) -> None:
        self._avoid = {p: until for p, until in self._avoid.items() if until > now}
        self._avoid.pop(site, None)  # re-inserted last: the most recent
        self._avoid[site] = now + PLAN_SITE_AVOID_SECONDS
        while len(self._avoid) > MAX_AVOIDED_SITES:
            del self._avoid[next(iter(self._avoid))]

    def _abort(
        self, plan: _Plan, code: PlanCode, reason: str, now: float, cause: PlanCode | None = None
    ) -> None:
        """Abort and release (module docstring); ``cause`` is the recovery that ran out."""
        trouble = cause if cause in SITE_TROUBLE else plan.reason_code
        avoided: list[Point] = list(plan.refused_sites)
        if trouble in SITE_TROUBLE and plan.site is not None and plan.acknowledged is None:
            avoided.append(plan.site)
        for site in avoided:
            self._avoid_site(site, now)
        released = Released(
            plan_id=plan.plan_id,
            root_node_id=plan.root_node_id,
            minerals=plan.tick_minerals,
            vespene=plan.tick_vespene,
            cancel_tasks=True,
        )
        held: dict[str, JsonValue] = {
            "actor_tag": None if plan.actor_tag is None else str(plan.actor_tag),
            "minerals": plan.reserved_minerals,
            "vespene": plan.reserved_vespene,
        }
        self._finish(plan, "aborted", code, now)
        self._aborts += 1
        self._kind_aborts[plan.kind] += 1
        plan.blocker = _blocker(reason)
        slot = PLAN_SLOT[plan.kind]
        self._cooldown[slot] = now + PLAN_COOLDOWN_SECONDS
        if code in STATIC_FAILURES:
            self._disabled[plan.kind] = code
        elif self._kind_aborts[plan.kind] >= MAX_PLAN_ABORTS:
            self._disabled.setdefault(plan.kind, "abort_limit")
        disabled = self._disabled.get(plan.kind)
        yield_to = self._others(plan.kind)
        if yield_to:  # the slot's next turn is another kind's (Turns)
            self._yield[slot] = (plan.kind, self._cooldown[slot] + PLAN_YIELD_SECONDS)
        else:
            self._yield.pop(slot, None)
        self._released.append(released)
        self._event(
            plan,
            "failed",
            f"{plan.kind} plan {plan.plan_id} aborted ({code}): {reason}",
            extra={
                "released": held,
                "cooldown_until": self._cooldown[slot],
                "disabled": disabled is not None,
                "disabled_by": disabled,
                "kind_aborts": self._kind_aborts[plan.kind],
                "yield_to": list(yield_to),
                "yield_until": self._yield[slot][1] if yield_to else None,
                "avoided_sites": [[p[0], p[1]] for p in avoided],
            },
        )

    def _finish(self, plan: _Plan, status: PlanStatus, code: PlanCode, now: float) -> None:
        if status == "completed":
            self._released.append(
                Released(
                    plan.plan_id, plan.root_node_id, plan.tick_minerals, plan.tick_vespene, False
                )
            )
        plan.status = status
        plan.reason_code = code
        plan.finished = now
        plan.actor_tag = None
        plan.reserved_minerals = plan.reserved_vespene = 0
        plan.tick_minerals = plan.tick_vespene = 0
        plan.in_flight = False
        self._open.pop(plan.node_id, None)
        self._finished.append(plan)

    def _event(
        self,
        plan: _Plan,
        status: PlanEventStatus,
        reason: str,
        *,
        transition: PlanCode | None = None,
        summary: bool = False,
        extra: Mapping[str, JsonValue] | None = None,
        at: str | None = None,
    ) -> None:
        """Queue one plan event, attributed to ``at`` (a task's node) or the stage."""
        facts: dict[str, JsonValue] = dict(plan.snapshot().facts())
        facts["slot_turn"] = self.slot(PLAN_SLOT[plan.kind]).facts()
        facts["transition"] = transition if transition is not None else plan.reason_code
        facts["summary"] = summary
        if extra:
            facts.update(extra)
        node = at if at is not None else plan.stage_node_id
        self._events.append(PlanEvent(node, status, reason, facts))
