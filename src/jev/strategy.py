"""Jev v2's hosted questions: persistent strategic plans and army intent on one budget.

Plan J2 Step 219 (D2, with D3's emergency preemption and D5's evidence). Typesafe
chooses *where the graph may begin*; the graph keeps every command, legality check
and plan stage. Shared HTTP parsing, answer validation and reason codes live in
:mod:`jev.decision`; this module holds what is particular to Jev v2.

**Questions.** Two typed Choice questions, one per request (:class:`Question`):
``army_mode`` (attack / defend / regroup for the mixed Zealot-Stalker army; defend
covers both bases and air threats for anti-air units) and ``strategy`` (pressure /
recover / expand / tech). A strategic choice is a plan kind (:mod:`jev.plans`): a
*macro* choice (expand, tech) picks which macro plan the macro slot opens -- where
the next macro reservation goes; an *army* choice (pressure, recover) sets the
army's posture and opens no macro plan now. Neither turns off the economy, supply,
production or emergency defense, which run in lanes no guard touches (home
defense also comes first in the army lane, whatever was chosen).

**The choice set is the graph's.** :func:`strategy_branches` derives the choices a
policy implements from its ``strategy_is`` guards -- a macro choice only when its
guard shares a lane with a ``start_plan`` of that kind -- so an unimplemented
choice is never offered. Each poll then filters them by what the graph can execute
*now*: a macro choice when the latest tick reached its guard (its branch's own
preconditions held: launched, prerequisites, safe home, no plan of the lane open)
and the plan store would accept the start (:meth:`jev.plans.PlanStore.refusal`:
slot free, no cooldown or owed turn, not disabled); an army choice once the opening
has launched (the latch both posture branches start from). No strategy question
before the first launch: the opening is fixed (D2's fallback order).

**Persistence (D2).** An answer must be fresh, confident and match its request's
signature when it arrives; after that it is *applied* and outlives its freshness:

* a macro choice holds the macro slot until the tick its plan starts (the plan
  records the request: ``decision_request_id``), which *consumes* it -- the plan
  then persists on its own (:mod:`jev.plans` deadlines and recovery); if the choice
  stops being executable before that, it is dropped (``expired_or_changed_state``);
* an army choice holds the army posture -- and keeps the macro slot from opening a
  plan -- until a newer strategy answer replaces it, it is no longer executable, or
  :data:`POSTURE_HOLD_SECONDS` of game time pass without a renewal.

**Local fallback.** When no hosted answer holds a slot, the graph's own conditions
decide it (``strategy_local`` guards): D2's fallback order is the graph's lane order
and conditions (urgent defense first, the opening, then tech's prerequisite, then
the expansion after four Gateways and eight ready combat units, the few-units
recovery, otherwise pressure). Any stale, low-confidence, invalid, failed or
timed-out strategy answer -- and an expired applied one -- returns both slots to it
(``source: local_fallback``, with the reason); 401/403 disables every question for
the match; an exhausted budget leaves every question to it (``request_limit``).
While the provider can still answer, the macro slot instead *waits* for the hosted
answer (``awaiting_answer``) before the first strategic answer and after a consumed
one, so the model -- not a race with the fallback -- decides the next macro plan.
The wait is bounded on the coordinator's own monotonic (wall) clock -- the clock
its cadence, request deadline and reply freshness already use: :func:`answer_wait_seconds`
(the strategy interval, plus :data:`STARVATION_SECONDS`, plus the longer of the
dispatch interval and the request deadline, plus :data:`ANSWER_WAIT_MARGIN_SECONDS`;
24 seconds by default) without a strategy request dispatched in the wait (each
dispatch restarts it; its answer then applies, or falls back as above) returns the
slot to the local fallback with reason ``answer_wait_expired``. That bound is the
longest the cadence below can take to dispatch a due strategy question, and it is
checked after the poll's dispatch, so a due question is always dispatched before
the slot falls back on it -- at any game speed; it fires only when no request can go
out at all (e.g. a provider that ignores cancellation keeps its request in flight).
At real-time speed a wall second is a game second. Without ``--realtime`` the game
outruns the wall clock, so the same wait spans more game time; hosted replies then
usually arrive older than the 8-game-second freshness and are refused as stale, so
the slot falls back to the local graph on the first reply. The few-units recovery is
a local safety reaction no answer overrides (D3), like home defense.

**One budget, fair scheduling (D2).** One request in flight at a time across both
questions; ``DecisionConfig.max_requests`` (the runner's ``--decision-max-requests``,
450 by default) counts every request of either question, and request ids are one
monotonic sequence per run. At least ``DecisionConfig.interval`` (2) wall seconds
between dispatches; the army question at most every ``interval``, the strategy
question at most every :data:`STRATEGY_INTERVAL` (10) seconds and preferably about
a state that held since the previous poll (its options come from the latest tick,
so a transient set -- e.g. the tick of the launch, before the macro lanes saw it --
is not asked about). A question whose signature changed since its last request is
*dirty*. A due question accrues waiting time from the first poll it could be
asked (two or more options) until it is dispatched, whether or not its state has
settled and across polls where its options dip below two (it is asked only with two
or more). One that has waited :data:`STARVATION_SECONDS` is promoted (longest wait
first) -- even unsettled, so a churning state (a flickering threat, an army size at a
bucket edge, an option coming and going) cannot keep it from being asked -- else the
highest-priority dirty one
(:data:`QUESTION_PRIORITY`: army, then strategy), else the highest-priority
refresh. Neither question can starve the other. :meth:`HostedQuestions.close`
cancels the pending request and stops all questions (stop, leave, match end).

**Staleness.** A request snapshots its question, options, signature, wall and game
time; only its own answer key is parsed, and a result is read only from the one
pending task (a timed-out request is cancelled and never applied), so a late or
crossed reply cannot reach another question or plan. The strategy signature binds
the offered options (prerequisites and plan feasibility), the open plans' identity,
whether home or the natural is threatened, and a coarse army size (below the
regroup size / below the expansion size / above) -- not every unit tag. The army
signature binds its units, threats, bases, launch and options like v1's. A new
threat therefore expires an accepted army intent at once: emergency defense never
waits on the service, and nothing on the gameplay path awaits a request.

**Evidence (D5).** Per poll, :class:`Decisions` carries the army facts (the v1 keys,
reported as ``Army decision source``), the strategy facts (reported as ``Strategy
decision source``: question, offered options, choice, source, reason, model,
confidence, probabilities, latency, ages, calls, tokens, slot holdings and the plan
the latest hosted macro answer started) and one combined stream for
:class:`jev.bot.MatchMetrics`, which counts every request of both questions.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from jev.contracts import Entity, JsonValue, Latch, Observation, Policy
from jev.decision import (
    ACCEPTED_REASON,
    AUTH_FAILURE_REASONS,
    CRITERIA,
    ENTITY_LIST_LIMIT,
    EXPIRED_REASON,
    LOW_CONFIDENCE_REASON,
    STALE_ANSWER_REASON,
    Answer,
    DecisionConfig,
    DecisionError,
    DecisionProvider,
    Question,
    entity_list,
)
from jev.operations import OP_START_PLAN, OP_STRATEGY_IS, PRODUCER_TYPE
from jev.plans import (
    PLAN_SLOT,
    STRATEGY_CHOICES,
    PlanSlot,
    StrategyChoice,
    StrategySlot,
)

if TYPE_CHECKING:
    from jev.runtime import JevRuntime

__all__ = [
    "ANSWER_WAIT_EXPIRED_REASON",
    "ANSWER_WAIT_MARGIN_SECONDS",
    "ARMY_V2_QUESTION",
    "AWAITING_REASON",
    "COMBAT_TYPES",
    "LAUNCH_LATCH",
    "POSTURE_HOLD_SECONDS",
    "QUESTION_PRIORITY",
    "REQUEST_LIMIT_REASON",
    "STARVATION_SECONDS",
    "STRATEGY_CRITERIA",
    "STRATEGY_INTERVAL",
    "STRATEGY_QUESTION",
    "Decisions",
    "HostedQuestions",
    "QuestionId",
    "StrategyBranch",
    "answer_wait_seconds",
    "army_context",
    "scripted_strategy_facts",
    "strategy_branches",
    "uses_strategy",
]

#: The questions this step asks, in D2's priority order (immediate expansion danger
#: and production mix -- Step 220 -- slot in before and after them).
QuestionId = Literal["army_mode", "strategy"]
QUESTION_PRIORITY: Final[tuple[QuestionId, ...]] = ("army_mode", "strategy")
#: D2: strategy reevaluation minimum, wall seconds (army: ``DecisionConfig.interval``).
STRATEGY_INTERVAL: Final = 10.0
#: D2: a due question waiting this long (wall seconds) is served next.
STARVATION_SECONDS: Final = 10.0
#: Game seconds an applied army posture (and the macro decline it carries) holds
#: without a renewed answer before the local fallback takes over again.
POSTURE_HOLD_SECONDS: Final = 30.0
#: The latch both posture branches start from: the opening's first launch.
LAUNCH_LATCH: Final[Latch] = "attack_launched"
#: The army: everything a Gateway trains (the central production table).
COMBAT_TYPES: Final[tuple[str, ...]] = tuple(
    sorted(unit for unit, producer in PRODUCER_TYPE.items() if producer == "Gateway")
)
#: The unit the opening's first wave counts (the graph's first-wave condition).
FIRST_WAVE_TYPE: Final = "Zealot"
AWAITING_REASON: Final = "awaiting_first_answer"
REQUEST_LIMIT_REASON: Final = "request_limit"
#: A reply naming an option its request never offered (the HTTP provider refuses
#: those itself with the same code; this guards any other provider).
INVALID_RESPONSE_REASON: Final = "invalid_response"
#: The strategy's macro slot waits for the hosted answer (module docstring).
AWAITING_ANSWER_SOURCE: Final = "awaiting_answer"
#: Wall seconds added to the longest dispatch delay of a due strategy question for
#: the answer-wait bound (:func:`answer_wait_seconds`): polls are game ticks, so the
#: dispatch happens on the first poll after its delay.
ANSWER_WAIT_MARGIN_SECONDS: Final = 2.0
ANSWER_WAIT_EXPIRED_REASON: Final = "answer_wait_expired"
#: Distinct keys kept in a state's count maps (unit types, structure types).
MAX_COUNT_KEYS: Final = 32

STRATEGY_INSTRUCTIONS: Final = (
    "Choose Jev's strategic priority in StarCraft II. We are Protoss after a "
    "four-Gateway Zealot opening and its first attack; we can add a Cybernetics Core "
    "for Stalkers and one natural expansion, nothing more. The choice decides which "
    "macro plan, if any, gets the next reservation and the army's posture; economy, "
    "supply, production and emergency home defense always continue locally, and a "
    "plan already started continues until it completes or aborts. Fog of war hides "
    "enemies; absence of visible enemies does not imply safety. Choose only an "
    "offered option. Local code handles targets, movement, production and resources."
)
#: The strategic choices' criteria (keys: :data:`jev.plans.STRATEGY_CHOICES`).
STRATEGY_CRITERIA: Final[Mapping[str, str]] = {
    "pressure": "Keep the army pressing the enemy; open no macro plan now, so "
    "production spends on units.",
    "recover": "Pull the army home to regroup and rebuild before pressing again; open "
    "no macro plan now.",
    "expand": "Take the natural expansion now: hold minerals for a second Nexus and "
    "send a worker to build it.",
    "tech": "Build the Cybernetics Core now so the Gateways can train Stalkers.",
}
STRATEGY_QUESTION: Final = Question("strategy", STRATEGY_INSTRUCTIONS, STRATEGY_CRITERIA)

ARMY_INSTRUCTIONS: Final = (
    "Choose the army's next intent in StarCraft II. We are Protoss with a Zealot and "
    "Stalker army after a four-Gateway opening, possibly holding a natural expansion. "
    "Defend endangered structures at either base, attack when pressure is worthwhile, "
    "regroup when the army should gather or recover. Zealots fight ground targets only; "
    "Stalkers also hit air. Fog of war hides enemies; absence of visible enemies does "
    "not imply safety. Choose only an offered option. Local code handles targets, "
    "movement, production and resources."
)
#: The v2 army question: v1's options (the graph's ``army_mode_is`` modes), v2 wording.
ARMY_V2_QUESTION: Final = Question(
    "army_mode",
    ARMY_INSTRUCTIONS,
    {
        "attack": "Press the enemy; reinforcements join the army as groups.",
        "defend": "Fight visible threats near our main or natural; Stalkers take air threats.",
        "regroup": "Gather the army at our home rally point before resuming pressure.",
    },
)
if set(ARMY_V2_QUESTION.criteria) != set(CRITERIA):  # the graph's army_mode_is modes
    raise RuntimeError("the v2 army question must offer exactly the army modes")


# ---------------------------------------------------------------------------
# The choice set: derived from the graph
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyBranch:
    """One strategic choice the policy implements: its ``strategy_is`` guard, the
    guard's root lane and, for a macro choice, the lane's ``start_plan`` node."""

    choice: StrategyChoice
    slot: PlanSlot
    guard: str
    lane: str
    start: str | None


def _strategy_choice(value: object) -> StrategyChoice | None:
    for choice in STRATEGY_CHOICES:
        if choice == value:
            return choice
    return None


def _lane(policy: Policy, root: str) -> list[str]:
    order: list[str] = []
    stack = [root]
    while stack:
        node_id = stack.pop()
        order.append(node_id)
        stack.extend(reversed(policy.node(node_id).children))
    return order


def strategy_branches(policy: Policy) -> tuple[StrategyBranch, ...]:
    """The strategic choices ``policy`` can execute, in :data:`STRATEGY_CHOICES` order.

    A choice is implemented by a ``strategy_is`` guard (the first one in policy
    order); a macro choice only when its guard's lane also holds a ``start_plan`` of
    that kind -- choosing it opens that plan. Nothing else is ever offered."""
    found: dict[StrategyChoice, StrategyBranch] = {}
    for root in policy.roots:
        order = _lane(policy, root)
        starts = {
            str(policy.node(node_id).args.get("kind")): node_id
            for node_id in order
            if policy.node(node_id).operation == OP_START_PLAN
        }
        for node_id in order:
            node = policy.node(node_id)
            if node.operation != OP_STRATEGY_IS:
                continue
            choice = _strategy_choice(node.args.get("choice"))
            if choice is None or choice in found:
                continue
            slot = PLAN_SLOT[choice]
            start = starts.get(choice) if slot == "macro" else None
            if slot == "macro" and start is None:
                continue  # no plan to open: not executable, never offered
            found[choice] = StrategyBranch(choice, slot, node_id, root, start)
    return tuple(found[choice] for choice in STRATEGY_CHOICES if choice in found)


def uses_strategy(policy: Policy) -> bool:
    """Whether ``policy`` implements any strategic choice (Jev v2; never frozen v1)."""
    return bool(strategy_branches(policy))


def _defense_nodes(policy: Policy) -> tuple[str, ...]:
    """The actions of the branch guarded by ``army_mode_is defend``: targets they
    demoted are never offered as threats (the graph's selections skip them too)."""
    found: list[str] = []
    for branch in policy.nodes:
        if branch.kind != "sequence" or not branch.children:
            continue
        guard = policy.node(branch.children[0])
        if guard.operation != "army_mode_is" or guard.args.get("mode") != "defend":
            continue
        pending = list(branch.children)
        while pending:
            node = policy.node(pending.pop())
            if node.kind == "action":
                found.append(node.id)
            pending.extend(node.children)
    return tuple(sorted(found))


def scripted_strategy_facts() -> dict[str, JsonValue]:
    """The strategy facts of a run without a hosted provider (the graph decides)."""
    return {
        "decision_provider": "scripted",
        "question_id": STRATEGY_QUESTION.question_id,
        "source": "local_graph",
        "choice": None,
        "reason": "local_graph",
        "calls": 0,
    }


# ---------------------------------------------------------------------------
# State summaries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Context:
    """One question's state for a request, its offered options and its signature."""

    state: dict[str, JsonValue]
    options: tuple[str, ...]
    signature: tuple[object, ...]


def _counts(entities: tuple[Entity, ...]) -> dict[str, JsonValue]:
    counter = Counter(e.type_name for e in entities)
    kept = sorted(counter)[:MAX_COUNT_KEYS]
    return {name: counter[name] for name in kept}


def _natural(observation: Observation, base_target: int) -> tuple[float, float] | None:
    """The graph's natural: with ``base_target`` Nexuses standing, the farthest from home."""
    nexuses = [s for s in observation.own_structures if s.type_name == "Nexus"]
    if len(nexuses) < base_target:
        return None
    home = observation.start_location
    farthest = max(nexuses, key=lambda s: (math.dist(s.position, home), -s.tag))
    return farthest.position


def _near(entity: Entity, points: tuple[tuple[tuple[float, float], float], ...]) -> bool:
    return any(math.dist(entity.position, point) <= radius for point, radius in points)


def army_context(
    observation: Observation,
    *,
    launched: bool,
    first_wave: int,
    defense_radius: float,
    natural_radius: float,
    base_target: int,
    demoted: frozenset[int] = frozenset(),
    posture: str | None = None,
) -> _Context:
    """The v2 army question: offered options mirror the v2 army lane's branches.

    ``defend`` when a ground threat stands near the main (``defense_radius``) or the
    natural (``natural_radius`` of the farthest of ``base_target`` Nexuses) and a
    combat unit is ready, or an air threat there and a ready unit hits air;
    ``attack`` once launched (or the first wave of ready Zealots stands) with a
    ready combat unit; ``regroup`` always. Demoted defense targets never count."""
    home = observation.start_location
    natural = _natural(observation, base_target)
    zones: tuple[tuple[tuple[float, float], float], ...] = ((home, defense_radius),)
    if natural is not None:
        zones = (*zones, (natural, natural_radius))
    army = tuple(u for u in observation.own_units if u.type_name in COMBAT_TYPES and u.is_ready)
    anti_air = tuple(u for u in army if u.can_attack_air)
    wave = sum(1 for u in army if u.type_name == FIRST_WAVE_TYPE)
    hostile = tuple(
        e
        for e in observation.visible_enemies
        if not e.is_structure and e.tag not in demoted and _near(e, zones)
    )
    ground = tuple(e for e in hostile if not e.is_flying)
    air = tuple(e for e in hostile if e.is_flying)
    can_defend = (bool(army) and bool(ground)) or (bool(anti_air) and bool(air))
    options = tuple(
        option
        for option in ARMY_V2_QUESTION.criteria
        if option == "regroup"
        or (option == "attack" and bool(army) and (launched or wave >= first_wave))
        or (option == "defend" and can_defend)
    )
    bases = tuple(s for s in observation.own_structures if s.type_name == "Nexus")
    state: dict[str, JsonValue] = {
        "game_seconds": observation.game_seconds,
        "minerals": observation.minerals,
        "vespene": observation.vespene,
        "supply_used": observation.supply_used,
        "supply_cap": observation.supply_cap,
        "attack_launched": launched,
        "first_wave_size": first_wave,
        "ready_combat_units": len(army),
        "ready_by_type": _counts(army),
        "army": entity_list(army, home, anti_air=True),
        "own_structure_counts": _counts(observation.own_structures),
        "visible_enemies": entity_list(observation.visible_enemies, home),
        "home_threats": entity_list(ground, home),
        "air_threats": entity_list(air, home),
        "remembered_enemy_structures": entity_list(observation.remembered_enemy_structures, home),
        "home": list(home),
        "natural": None if natural is None else list(natural),
        "enemy_starts": [list(p) for p in observation.enemy_start_locations],
        "strategic_posture": posture,
        "entity_list_limit": ENTITY_LIST_LIMIT,
    }
    signature = (
        tuple(sorted(u.tag for u in army)),
        tuple(sorted(e.tag for e in ground)),
        tuple(sorted(e.tag for e in air)),
        tuple(sorted(s.tag for s in bases)),
        launched,
        options,
    )
    return _Context(state, options, signature)


# ---------------------------------------------------------------------------
# The coordinator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Request:
    """One dispatched request: what was asked and when (its answer is judged by it)."""

    question_id: QuestionId
    request_id: int
    options: tuple[str, ...]
    signature: tuple[object, ...]
    state: dict[str, JsonValue]
    wall: float
    game: float


@dataclass
class _Question:
    """Per-question schedule and evidence (no queue: only the latest answer is kept)."""

    question: Question
    interval: float
    #: Asked only about a state that held since the previous poll (the strategy
    #: question: its options are read from the latest tick, never a transient set).
    settle: bool = False
    seen_signature: tuple[object, ...] | None = None
    last_asked: float = -math.inf
    asked_signature: tuple[object, ...] | None = None
    waiting_since: float | None = None
    reason: str = AWAITING_REASON
    answer: Answer | None = None
    answer_request: _Request | None = None
    answer_latency: float | None = None


@dataclass(frozen=True)
class _Held:
    """An applied strategic answer: the choice, its request and when it was accepted."""

    choice: StrategyChoice
    request: _Request
    answer: Answer
    accepted_game: float


@dataclass(frozen=True)
class Decisions:
    """One poll's outcome for the controller: the army mode and the strategy slots for
    the runtime, each with its facts, plus the combined stream for MatchMetrics."""

    army_mode: str | None
    army_facts: dict[str, JsonValue]
    strategy: Mapping[PlanSlot, StrategySlot]
    strategy_facts: dict[str, JsonValue]
    metrics_facts: dict[str, JsonValue]


def answer_wait_seconds(config: DecisionConfig) -> float:
    """Wall seconds the macro slot may wait for a hosted strategy answer without a
    strategy request dispatched in the wait (module docstring: Local fallback).

    The longest a due strategy question can take to be dispatched, measured on the
    clock the cadence uses: its interval since the previous ask, the starvation
    promotion, then the request in flight (its deadline) or the dispatch spacing,
    plus a margin for the poll that dispatches it."""
    strategy_interval = max(config.interval, STRATEGY_INTERVAL)
    in_flight = max(config.interval, config.timeout)
    return strategy_interval + STARVATION_SECONDS + in_flight + ANSWER_WAIT_MARGIN_SECONDS


def _param(policy: Policy, name: str) -> float:
    value = policy.parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"the strategic policy needs a numeric parameter {name!r}")
    return float(value)


class HostedQuestions:
    """The v2 strategy and army questions on one request budget (module docstring).

    :meth:`poll` never awaits: at most one request runs as an asyncio task, and a
    finished one is read on the next poll. Raises :class:`ValueError` for a policy
    without strategic choices or the parameters the army question mirrors."""

    def __init__(
        self,
        provider: DecisionProvider,
        config: DecisionConfig,
        policy: Policy,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        branches = strategy_branches(policy)
        if not branches:
            raise ValueError("this policy implements no strategic choice")
        self.provider, self.config, self._clock = provider, config, clock
        self._branches: dict[StrategyChoice, StrategyBranch] = {b.choice: b for b in branches}
        self._first_wave = int(_param(policy, "first_attack_zealots"))
        self._defense_radius = _param(policy, "defense_radius")
        self._natural_radius = _param(policy, "natural_defense_radius")
        self._base_target = int(_param(policy, "base_target"))
        self._regroup_below = _param(policy, "regroup_below")
        self._expand_army = _param(policy, "expand_army_size")
        self._defense = _defense_nodes(policy)
        self._questions: dict[QuestionId, _Question] = {
            "army_mode": _Question(ARMY_V2_QUESTION, config.interval),
            "strategy": _Question(
                STRATEGY_QUESTION, max(config.interval, STRATEGY_INTERVAL), settle=True
            ),
        }
        self._task: asyncio.Task[Answer] | None = None
        self._pending: _Request | None = None
        self._latency: float | None = None
        self._last_dispatch = -math.inf
        self.calls = self.input_tokens = self.output_tokens = 0
        self.disabled = False
        # Army: the accepted answer in force (v1 semantics: while fresh and unchanged).
        self._army: tuple[Answer, _Request] | None = None
        # Strategy: an unconsumed macro choice, an applied posture, and whether the
        # macro slot waits for the hosted answer (else the local fallback holds it).
        self._macro: _Held | None = None
        self._posture: _Held | None = None
        self._macro_waits = True
        # Wall time (this clock) the current wait for the hosted answer began, or the
        # latest strategy dispatch within it (None while the slot is not waiting).
        self._wait_from: float | None = None
        self._answer_wait = answer_wait_seconds(config)
        self._started: dict[str, JsonValue] | None = None  # the latest hosted plan start
        # The latest resolution of any request (the combined metrics stream).
        self._last_answer_id: int | None = None
        self._last_reason = AWAITING_REASON
        self._last_accepted = False
        self._last_model: str | None = None
        self._last_latency: float | None = None

    @property
    def task(self) -> asyncio.Task[Answer] | None:
        """The request in flight, if any (one at a time across all questions)."""
        return self._task

    @property
    def pending_question(self) -> QuestionId | None:
        return None if self._pending is None else self._pending.question_id

    def offered_choices(self) -> tuple[StrategyChoice, ...]:
        """Every strategic choice this policy implements (before per-poll filtering)."""
        return tuple(self._branches)

    # -- per poll -------------------------------------------------------------

    def poll(self, observation: Observation, runtime: JevRuntime) -> Decisions:
        """Read a finished request, revalidate what is in force, maybe dispatch one
        request, and report what the graph reads this tick; never blocks."""
        now = self._clock()
        game = observation.game_seconds
        launched = runtime.latches.get(LAUNCH_LATCH, False)
        demoted = frozenset().union(*(runtime.demoted_targets(n) for n in self._defense))
        posture = None if self._posture is None else self._posture.choice
        army = army_context(
            observation,
            launched=launched,
            first_wave=self._first_wave,
            defense_radius=self._defense_radius,
            natural_radius=self._natural_radius,
            base_target=self._base_target,
            demoted=demoted,
            posture=posture,
        )
        strategy = self._strategy_context(observation, runtime, launched, demoted)
        contexts: dict[QuestionId, _Context] = {"army_mode": army, "strategy": strategy}
        if self._task is not None and self._task.done():
            self._resolve(contexts, now, game)
        self._revalidate(runtime, contexts, now, game)
        self._schedule(contexts, now, game)
        self._bound_wait(contexts["strategy"].options, now)  # after this poll's dispatch
        return self._decisions(contexts, game)

    def _strategy_options(self, runtime: JevRuntime, launched: bool) -> tuple[str, ...]:
        """The implemented choices the graph can execute now (module docstring)."""
        if not launched:
            return ()
        reached = runtime.strategy_reach()
        options: list[str] = []
        for choice, branch in self._branches.items():
            if branch.slot == "army":
                options.append(choice)
            elif choice in reached and runtime.plan_start_refusal(choice) is None:
                options.append(choice)
        return tuple(options)

    def _strategy_context(
        self,
        observation: Observation,
        runtime: JevRuntime,
        launched: bool,
        demoted: frozenset[int],
    ) -> _Context:
        options = self._strategy_options(runtime, launched)
        home = observation.start_location
        natural = _natural(observation, self._base_target)
        ground = tuple(
            e
            for e in observation.visible_enemies
            if not e.is_structure and not e.is_flying and e.tag not in demoted
        )
        home_threat = any(math.dist(e.position, home) <= self._defense_radius for e in ground)
        natural_threat = natural is not None and any(
            math.dist(e.position, natural) <= self._natural_radius for e in ground
        )
        army = tuple(u for u in observation.own_units if u.type_name in COMBAT_TYPES and u.is_ready)
        size = len(army)
        bucket = (
            "few"
            if size < self._regroup_below
            else "enough"
            if size >= self._expand_army
            else "some"
        )
        records = runtime.plans()
        open_plans = tuple(sorted((r.plan_id, r.kind) for r in records if r.is_open))
        plans: list[JsonValue] = [
            {
                "plan_id": r.plan_id,
                "kind": r.kind,
                "status": r.status,
                "stage": r.stage_node_id,
                "reason_code": r.reason_code,
            }
            for r in records[-8:]
        ]
        slots: dict[str, JsonValue] = {s.slot: s.facts() for s in runtime.plan_slots()}
        workers = sum(1 for u in observation.own_units if u.type_name == "Probe")
        state: dict[str, JsonValue] = {
            "game_seconds": observation.game_seconds,
            "minerals": observation.minerals,
            "vespene": observation.vespene,
            "supply_used": observation.supply_used,
            "supply_cap": observation.supply_cap,
            "attack_launched": launched,
            "workers": workers,
            "ready_combat_units": size,
            "ready_by_type": _counts(army),
            "regroup_below": self._regroup_below,
            "expansion_army_size": self._expand_army,
            "own_structure_counts": _counts(observation.own_structures),
            "home_threatened": home_threat,
            "natural": None if natural is None else list(natural),
            "natural_threatened": natural_threat,
            "visible_enemy_counts": _counts(observation.visible_enemies),
            "remembered_enemy_structure_counts": _counts(observation.remembered_enemy_structures),
            "plans": plans,
            "plan_slots": slots,
            "strategic_posture": None if self._posture is None else self._posture.choice,
        }
        signature = (options, open_plans, home_threat, natural_threat, bucket)
        return _Context(state, options, signature)

    def _resolve(self, contexts: Mapping[QuestionId, _Context], now: float, game: float) -> None:
        task, request = self._task, self._pending
        self._task = self._pending = None
        if task is None or request is None:  # pragma: no cover - both set together
            return
        question = self._questions[request.question_id]
        try:
            answer = task.result()
        except (Exception, asyncio.CancelledError) as exc:
            reason = str(exc) if isinstance(exc, DecisionError) else "request_failed"
            if reason in AUTH_FAILURE_REASONS:
                self.disabled = True
            question.reason = reason
            self._last_reason, self._last_accepted = reason, False
            if request.question_id == "army_mode":
                self._army = None
            else:
                self._fall_back()
            return
        question.answer = answer
        question.answer_request = request
        question.answer_latency = self._latency
        self.input_tokens += answer.input_tokens
        self.output_tokens += answer.output_tokens
        current = contexts[request.question_id]
        if answer.choice not in request.options:  # a crossed or unoffered reply
            reason = INVALID_RESPONSE_REASON
        elif (
            current.signature != request.signature
            or now - request.wall > self.config.max_wall_age
            or game - request.game > self.config.max_game_age
        ):
            reason = STALE_ANSWER_REASON
        elif answer.confidence < self.config.min_confidence:
            reason = LOW_CONFIDENCE_REASON
        else:
            reason = ACCEPTED_REASON
        accepted = reason == ACCEPTED_REASON
        question.reason = reason
        self._last_answer_id = request.request_id
        self._last_reason, self._last_accepted = reason, accepted
        self._last_model, self._last_latency = answer.model, self._latency
        if request.question_id == "army_mode":
            self._army = (answer, request) if accepted else None
        elif accepted:
            self._apply(answer, request, game)
        else:
            self._fall_back()

    def _apply(self, answer: Answer, request: _Request, game: float) -> None:
        """Apply an accepted strategy answer: it defines both slots (docstring)."""
        choice = _strategy_choice(answer.choice)
        if choice is None:  # pragma: no cover - parse_answer only returns offered options
            self._fall_back()
            return
        held = _Held(choice, request, answer, game)
        if PLAN_SLOT[choice] == "macro":
            self._macro, self._posture = held, None
        else:
            self._macro, self._posture = None, held

    def _fall_back(self) -> None:
        """Both slots return to the local fallback (graph logic) until the next answer."""
        self._macro = self._posture = None
        self._macro_waits = False

    def _revalidate(
        self,
        runtime: JevRuntime,
        contexts: Mapping[QuestionId, _Context],
        now: float,
        game: float,
    ) -> None:
        if self._army is not None:
            _, request = self._army
            if (
                contexts["army_mode"].signature != request.signature
                or now - request.wall > self.config.max_wall_age
                or game - request.game > self.config.max_game_age
            ):
                self._army = None
                self._questions["army_mode"].reason = EXPIRED_REASON
        strategy = self._questions["strategy"]
        options = contexts["strategy"].options
        if self._macro is not None:
            held = self._macro
            start = self._branches[held.choice].start
            record = None if start is None else runtime.plan(start)
            if record is not None and record.decision_request_id == held.request.request_id:
                # Consumed: the plan carries the decision now; the slot waits for the next.
                self._started = {
                    "request_id": held.request.request_id,
                    "plan_id": record.plan_id,
                    "plan_kind": record.kind,
                    "started_game_seconds": record.started_game_seconds,
                }
                self._macro = None
                self._macro_waits = True
            elif held.choice not in options:  # newly unexecutable before it applied
                self._fall_back()
                strategy.reason = EXPIRED_REASON
        if self._posture is not None and (
            game - self._posture.accepted_game > POSTURE_HOLD_SECONDS
            or self._posture.choice not in options
        ):
            self._fall_back()
            strategy.reason = EXPIRED_REASON

    def _bound_wait(self, options: tuple[str, ...], now: float) -> None:
        """Bound the macro slot's wait for the hosted answer on the coordinator's clock
        (module docstring: Local fallback); runs after the poll's dispatch."""
        if not self._awaiting(options):
            self._wait_from = None
        elif self._wait_from is None:
            self._wait_from = now
        elif now - self._wait_from > self._answer_wait:
            self._fall_back()
            self._questions["strategy"].reason = ANSWER_WAIT_EXPIRED_REASON
            self._wait_from = None

    def _schedule(self, contexts: Mapping[QuestionId, _Context], now: float, game: float) -> None:
        due: list[QuestionId] = []
        for question_id in QUESTION_PRIORITY:
            question = self._questions[question_id]
            signature = contexts[question_id].signature
            settled = not question.settle or signature == question.seen_signature
            question.seen_signature = signature
            askable = len(contexts[question_id].options) > 1
            if now - question.last_asked < question.interval:
                question.waiting_since = None  # asked too recently: no wait accrues
                continue
            if question.waiting_since is None:
                if not askable:
                    continue  # its wait starts on the first poll it could be asked
                question.waiting_since = now
            # Due: the wait accrues settled or not, and across dips below two options.
            starved = now - question.waiting_since >= STARVATION_SECONDS
            if askable and (settled or starved):
                due.append(question_id)
        if (
            self.disabled
            or self._task is not None
            or self.calls >= self.config.max_requests
            or now - self._last_dispatch < self.config.interval
            or not due
        ):
            return

        def rank(question_id: QuestionId) -> tuple[int, float, int]:
            question = self._questions[question_id]
            since = question.waiting_since
            waited = 0.0 if since is None else now - since
            starved = waited >= STARVATION_SECONDS
            dirty = contexts[question_id].signature != question.asked_signature
            priority = QUESTION_PRIORITY.index(question_id)
            if starved:
                return (0, -waited, priority)
            return (1 if dirty else 2, 0.0, priority)

        chosen = min(due, key=rank)
        context = contexts[chosen]
        question = self._questions[chosen]
        self.calls += 1
        request = _Request(
            chosen, self.calls, context.options, context.signature, context.state, now, game
        )
        question.last_asked = now
        question.asked_signature = context.signature
        question.waiting_since = None
        if chosen == "strategy" and self._wait_from is not None:
            self._wait_from = now  # a request is out: the wait restarts
        self._last_dispatch = now
        self._pending = request
        self._task = asyncio.create_task(self._request(request))

    async def _request(self, request: _Request) -> Answer:
        # Total deadline includes connection, body streaming and provider scheduling.
        question = self._questions[request.question_id].question
        try:
            async with asyncio.timeout(self.config.timeout):
                return await self.provider.decide(request.state, request.options, question)
        finally:
            self._latency = round((self._clock() - request.wall) * 1000, 1)

    # -- outputs ----------------------------------------------------------------

    def _answerable(self, options: tuple[str, ...]) -> bool:
        return not self.disabled and self.calls < self.config.max_requests and len(options) > 1

    def _awaiting(self, options: tuple[str, ...]) -> bool:
        """Whether the macro slot waits for the hosted answer (no hosted choice holds
        it, and the provider can still answer)."""
        return (
            self._macro is None
            and self._posture is None
            and self._macro_waits
            and self._answerable(options)
        )

    def _slots(self, options: tuple[str, ...]) -> dict[PlanSlot, StrategySlot]:
        local = StrategySlot(local=True, source="local_fallback")
        if self._macro is not None:
            macro = StrategySlot(
                local=False,
                choice=self._macro.choice,
                request_id=self._macro.request.request_id,
                source="typesafe",
            )
        elif self._posture is not None:  # an army posture opens no macro plan now
            macro = StrategySlot(
                local=False, request_id=self._posture.request.request_id, source="typesafe"
            )
        elif self._awaiting(options):
            macro = StrategySlot(local=False, source="typesafe")
        else:
            macro = local
        army = local
        if self._posture is not None:
            army = StrategySlot(
                local=False,
                choice=self._posture.choice,
                request_id=self._posture.request.request_id,
                source="typesafe",
            )
        return {"macro": macro, "army": army}

    def _question_facts(
        self, question_id: QuestionId, context: _Context, game: float
    ) -> dict[str, JsonValue]:
        question = self._questions[question_id]
        answer, request = question.answer, question.answer_request
        in_flight = self._pending
        pending = in_flight is not None and in_flight.question_id == question_id
        return {
            "decision_provider": "typesafe",
            "question_id": question_id,
            "reason": question.reason,
            "pending": pending,
            "requested_model": self.config.model,
            "model": None if answer is None else answer.model,
            "question": question.question.instructions,
            "options": list(request.options if request is not None else context.options),
            "available_options": list(context.options),
            "answer_request_id": None if request is None else request.request_id,
            "pending_request_id": in_flight.request_id if in_flight and pending else None,
            "answer": None if answer is None else answer.choice,
            "confidence": None if answer is None else answer.confidence,
            "probabilities": {} if answer is None else answer.probabilities,
            "latency_ms": question.answer_latency,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "max_requests": self.config.max_requests,
            "observation_game_seconds": None if request is None else request.game,
        }

    def _decisions(self, contexts: Mapping[QuestionId, _Context], game: float) -> Decisions:
        army_context_now = contexts["army_mode"]
        mode = None if self._army is None else self._army[0].choice
        army_facts = self._question_facts("army_mode", army_context_now, game)
        if mode is None and self.calls >= self.config.max_requests and self._task is None:
            army_facts["reason"] = REQUEST_LIMIT_REASON
        army_facts["source"] = "typesafe" if mode else "scripted_fallback"
        army_facts["choice"] = mode
        army_facts["response_age_game_seconds"] = (
            None if self._army is None else game - self._army[1].game
        )

        strategy_context = contexts["strategy"]
        slots = self._slots(strategy_context.options)
        held = self._macro or self._posture
        strategy_facts = self._question_facts("strategy", strategy_context, game)
        if held is None and self.calls >= self.config.max_requests and self._task is None:
            strategy_facts["reason"] = REQUEST_LIMIT_REASON
        if held is not None:
            source = "typesafe"
        elif not slots["macro"].local:
            source = AWAITING_ANSWER_SOURCE
        else:
            source = "local_fallback"
        strategy_facts["source"] = source
        strategy_facts["choice"] = None if held is None else held.choice
        strategy_facts["in_force_request_id"] = None if held is None else held.request.request_id
        strategy_facts["slots"] = {slot: holding.facts() for slot, holding in slots.items()}
        strategy_facts["posture_hold_until_game_seconds"] = (
            None if self._posture is None else self._posture.accepted_game + POSTURE_HOLD_SECONDS
        )
        strategy_facts["started_plan"] = None if self._started is None else dict(self._started)
        strategy_facts["response_age_game_seconds"] = (
            None if held is None else game - held.request.game
        )

        pending_id = None if self._pending is None else self._pending.request_id
        metrics: dict[str, JsonValue] = {
            "decision_provider": "typesafe",
            "requested_model": self.config.model,
            "max_requests": self.config.max_requests,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "answer_request_id": self._last_answer_id,
            "pending_request_id": pending_id,
            "source": "typesafe" if self._last_accepted else "fallback",
            "reason": self._last_reason,
            "model": self._last_model,
            "latency_ms": self._last_latency,
        }
        return Decisions(mode, army_facts, slots, strategy_facts, metrics)

    async def close(self) -> None:
        """Stop every question and cancel the request in flight (stop, leave, end)."""
        self.disabled = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (Exception, asyncio.CancelledError):
                pass
            self._task = None
            self._pending = None
