"""Jev's burnysc2 lifecycle: one match driven entirely by the policy graph.

:class:`JevController` is the per-step production path. Each game step it

1. ends the match cleanly when a stop was requested (Ctrl+C) or a wall-clock or
   game-time limit is reached (it leaves the game through the port);
2. forwards SC2's action errors to the runtime (SC2 reports each one once, so
   this runs every step);
3. between policy ticks (the runtime's 0.25 game-second cadence) only refreshes
   the adapter's enemy-structure memory -- the full visible-state
   :class:`~jev.contracts.Observation` is built only when the runtime will tick,
   which is also the only time it checks acknowledgements;
4. on a tick, builds that Observation through
   :class:`~jev.sc2_adapter.Sc2Adapter`, ticks :class:`~jev.runtime.JevRuntime`
   and hands the graph-selected commands to the adapter, which issues them;
5. hands the step's runtime events to the run's
   :class:`~jev.telemetry.RunRecorder`, which traces them and refreshes the run
   state at most twice per wall-clock second -- on every live step, ticked or
   not, so the state's heartbeat keeps pace with the game.

A failure inside that pipeline never escapes a step: it is recorded as a crash
(error code ``match_crashed``) and the bot leaves the game, so the runner reports
a failure whatever burnysc2 does with bot exceptions (some of its loops log them
and report a Defeat). Run evidence that cannot be persisted
(:class:`~jev.telemetry.PersistenceFailed`) ends the match the same way, with
error code ``persistence_failed`` instead. A failed leave is retried on the
following steps, at most :data:`MAX_LEAVE_ATTEMPTS` attempts in all; if every
attempt fails, :class:`LeaveFailed` is raised out of the step so burnysc2 ends its
game loop, and a match never idles on past its limits.

:class:`JevBot` is the thin ``BotAI`` shim burnysc2 runs: ``on_start`` attaches
the controller to the live game and registers Ctrl+C as a clean-leave request,
``on_step`` steps the controller, ``on_end`` records SC2's result. There is no
legacy gameplay code or RL policy on this path. The optional Typesafe coordinator
supplies army intent; every command still comes from a graph node and runtime task.

**Benchmark metrics** (plan D6). On every policy tick the controller also feeds
the tick's Observation and decision facts to its :class:`MatchMetrics`, which
only reads them: it never changes an observation, a decision, a command or the
run's events. A defect in the accumulator disables it (its summary then reads
``failed``) instead of ending the match. The runner writes the summary into the
run archive at the end (``jev.runner.DIAGNOSTICS_FILE``).
"""

from __future__ import annotations

import math
import signal
import statistics
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import FrameType
from typing import Any, Final, Literal

from sc2.bot_ai import BotAI
from sc2.data import Result

from jev.contracts import (
    MAX_MESSAGE_CHARS,
    RECENT_EVENT_LIMIT,
    Entity,
    Event,
    JevError,
    JsonValue,
    Observation,
    render_text,
    safe_exception_text,
    safe_repr,
)
from jev.decision import (
    ACCEPTED_REASON,
    AUTH_FAILURE_REASONS,
    LOW_CONFIDENCE_REASON,
    STALE_REASONS,
    ArmyDecisions,
)
from jev.operations import MINERAL_COST, PRODUCER_TYPE, REQUIRES_POWER, SUPPLY_COST, WORKER_TYPE
from jev.policy import PolicyBundle
from jev.runner import MatchLimits, exception_error
from jev.runtime import JevRuntime, TickResult
from jev.sc2_adapter import BotAIPort, GamePort, Sc2Adapter
from jev.telemetry import PersistenceFailed, RunRecorder

__all__ = [
    "GATEWAY",
    "JevBot",
    "JevController",
    "LeaveFailed",
    "MAX_LATENCY_SAMPLES",
    "MAX_LEAVE_ATTEMPTS",
    "MAX_METRIC_KEYS",
    "MAX_METRIC_TAGS",
    "METRICS_SCHEMA_VERSION",
    "METRIC_SAMPLE_GAP_SECONDS",
    "MatchMetrics",
    "MatchResult",
    "SUPPLY_CEILING",
    "TerminalReason",
    "match_result",
]

#: An SC2 outcome the match ended with (a stop or time limit is a TerminalReason).
MatchResult = Literal["win", "loss", "draw"]
#: Why the controller itself ended the match.
TerminalReason = Literal["stopped", "game_timeout", "wall_timeout", "crashed", "persistence_failed"]
#: Attempts to leave the game, one per game step, before :class:`LeaveFailed`.
MAX_LEAVE_ATTEMPTS: Final = 3


class LeaveFailed(RuntimeError):
    """Leaving the game failed :data:`MAX_LEAVE_ATTEMPTS` times.

    Raised out of :meth:`JevController.step` (so out of ``on_step``): burnysc2 then
    ends its game loop and the SC2 process it launched, so a match whose leave
    keeps failing still ends. The controller keeps the reason it ended the match.
    """


_SC2_RESULTS: Final[dict[Result, MatchResult]] = {
    Result.Victory: "win",
    Result.Defeat: "loss",
    Result.Tie: "draw",
}


def match_result(result: object) -> MatchResult | None:
    """burnysc2's game result as a Jev result; None for Undecided or anything else."""
    return _SC2_RESULTS.get(result) if isinstance(result, Result) else None


# ---------------------------------------------------------------------------
# Observation-only benchmark metrics (plan D6)
# ---------------------------------------------------------------------------

METRICS_SCHEMA_VERSION: Final = 1
#: Plan D6: accumulators sample at most this far apart in game time. A longer gap
#: between two samples is missing coverage, never a zero bank or zero idle time.
METRIC_SAMPLE_GAP_SECONDS: Final = 1.0
#: SC2's supply ceiling: a full army is maxed out, not supply blocked.
SUPPLY_CEILING: Final = 200
#: Own tags the loss counter remembers (far above any real match); more makes the
#: loss metric incomplete rather than unbounded.
MAX_METRIC_TAGS: Final = 8192
#: Distinct keys kept per count map (unit types, models, failure reasons).
MAX_METRIC_KEYS: Final = 32
#: Latencies kept for the median (the request cap is at most 10000 per match).
MAX_LATENCY_SAMPLES: Final = 10_000
#: The structure whose idle time the scorecard reports, and the units it trains.
GATEWAY: Final = "Gateway"
_GATEWAY_UNITS: Final = tuple(
    unit for unit, producer in PRODUCER_TYPE.items() if producer == GATEWAY
)
#: Milestones timed by their first observation (version independent: v1 never
#: builds the last three, so they read ``not_reached``).
_MILESTONES: Final = (
    "first_attack",
    "first_expansion_nexus",
    "first_cybernetics_core",
    "first_stalker",
)


@dataclass(frozen=True)
class _Sample:
    """The per-sample values the time-weighted accumulators integrate."""

    minerals: int
    workers: int
    supply_blocked: bool
    idle_gateways: int


def _ready_producer(entity: Entity, producer: str) -> bool:
    powered = entity.is_powered or producer not in REQUIRES_POWER
    return entity.type_name == producer and entity.is_ready and entity.is_idle and powered


def _supply_blocked(observation: Observation) -> bool:
    """An idle ready producer could afford a unit it trains, but its supply does not fit."""
    if observation.supply_cap >= SUPPLY_CEILING:
        return False
    for unit, producer in PRODUCER_TYPE.items():
        if observation.minerals < MINERAL_COST[unit]:
            continue
        if observation.supply_used + SUPPLY_COST[unit] <= observation.supply_cap:
            continue
        if any(_ready_producer(s, producer) for s in observation.own_structures):
            return True
    return False


def _idle_gateways(observation: Observation) -> int:
    """Ready powered idle Gateways that minerals and free supply could put to work."""
    if not _GATEWAY_UNITS:
        return 0
    ready = sum(1 for s in observation.own_structures if _ready_producer(s, GATEWAY))
    cost = min(MINERAL_COST[unit] for unit in _GATEWAY_UNITS)
    supply = min(SUPPLY_COST[unit] for unit in _GATEWAY_UNITS)
    free = max(0, observation.supply_cap - observation.supply_used)
    return min(ready, observation.minerals // cost, free // supply)


def _bounded_count(counter: Counter[str], key: str) -> None:
    if key in counter or len(counter) < MAX_METRIC_KEYS:
        counter[key] += 1
    else:
        counter["<other>"] += 1


def _count_map(counter: Counter[str]) -> dict[str, JsonValue]:
    return {key: counter[key] for key in sorted(counter)}


class MatchMetrics:
    """Cumulative, observation-only scorecard accumulators for one match (plan D6).

    :meth:`sample` reads one tick's Observation; :meth:`record_decision` reads the
    decision facts the controller hands the runtime. Time-weighted values (mean
    mineral bank, worker count, supply-blocked and idle-Gateway seconds) integrate
    each sample's value over the game time to the next sample; a gap longer than
    :data:`METRIC_SAMPLE_GAP_SECONDS` is counted as missed coverage instead.
    :meth:`summary` labels every metric ``observed``/``measured``,
    ``not_reached`` or ``unavailable`` -- an unknown is never reported as zero.
    """

    def __init__(self) -> None:
        self._failure: str | None = None
        self._samples = 0
        self._first: float | None = None
        self._last: float | None = None
        self._previous: _Sample | None = None
        self._covered = 0.0
        self._missed = 0.0
        self._max_gap = 0.0
        self._bank = 0.0
        self._worker_time = 0.0
        self._blocked = 0.0
        self._idle_gateways = 0.0
        self._workers_max = 0
        self._milestones: dict[str, float | None] = dict.fromkeys(_MILESTONES)
        self._initial_nexuses: int | None = None
        self._seen_units: dict[int, str] = {}
        self._seen_structures: dict[int, str] = {}
        self._last_units: frozenset[int] = frozenset()
        self._last_structures: frozenset[int] = frozenset()
        self._tags_overflow = False
        # Decisions (facts of each poll).
        self._provider: str | None = None
        self._providers: set[str] = set()
        self._requested_model: str | None = None
        self._max_requests: int | None = None
        self._calls = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._answers = 0
        self._accepted = 0
        self._stale = 0
        self._low_confidence = 0
        self._other_answers = 0
        self._failures: Counter[str] = Counter()
        self._auth_failures = 0
        self._returned_models: Counter[str] = Counter()
        self._latencies: list[float] = []
        self._last_answer_id: int | None = None
        self._last_pending_id: int | None = None

    @property
    def failure(self) -> str | None:
        """Why the accumulator stopped (rendered), if a defect disabled it."""
        return self._failure

    def fail(self, exc: BaseException) -> None:
        """Disable the accumulator after a defect; the match itself is unaffected."""
        if self._failure is None:
            name = safe_repr(type(exc).__name__)
            self._failure = render_text(f"{name}: {safe_exception_text(exc)}")[:MAX_MESSAGE_CHARS]

    # -- sampling -------------------------------------------------------------

    def sample(self, observation: Observation, *, attack_launched: bool) -> None:
        """Integrate the span since the previous sample, then record this one."""
        if self._failure is not None:
            return
        now = observation.game_seconds
        if not math.isfinite(now) or (self._last is not None and now <= self._last):
            return  # a repeated (or non-advancing) clock adds nothing
        if self._last is None:
            self._first = now
            if now > METRIC_SAMPLE_GAP_SECONDS:
                self._missed += now  # nothing was measured before the first sample
                self._max_gap = max(self._max_gap, now)
        else:
            self._integrate(now - self._last, self._previous)
        current = _Sample(
            minerals=observation.minerals,
            workers=sum(1 for u in observation.own_units if u.type_name == WORKER_TYPE),
            supply_blocked=_supply_blocked(observation),
            idle_gateways=_idle_gateways(observation),
        )
        self._workers_max = max(self._workers_max, current.workers)
        self._milestone_checks(observation, now, attack_launched)
        self._track_tags(observation)
        self._previous = current
        self._last = now
        self._samples += 1

    def _integrate(self, span: float, values: _Sample | None) -> None:
        if span <= 0 or values is None:
            return
        if span > METRIC_SAMPLE_GAP_SECONDS:
            self._missed += span
            self._max_gap = max(self._max_gap, span)
            return
        self._covered += span
        self._max_gap = max(self._max_gap, span)
        self._bank += values.minerals * span
        self._worker_time += values.workers * span
        if values.supply_blocked:
            self._blocked += span
        self._idle_gateways += values.idle_gateways * span

    def _milestone_checks(self, observation: Observation, now: float, launched: bool) -> None:
        nexuses = sum(1 for s in observation.own_structures if s.type_name == "Nexus")
        if self._initial_nexuses is None:
            self._initial_nexuses = nexuses
        reached = {
            "first_attack": launched,
            "first_expansion_nexus": nexuses > self._initial_nexuses,
            "first_cybernetics_core": any(
                s.type_name == "CyberneticsCore" for s in observation.own_structures
            ),
            "first_stalker": any(u.type_name == "Stalker" for u in observation.own_units),
        }
        for name, hit in reached.items():
            if hit and self._milestones[name] is None:
                self._milestones[name] = now

    def _track_tags(self, observation: Observation) -> None:
        for entities, seen in (
            (observation.own_units, self._seen_units),
            (observation.own_structures, self._seen_structures),
        ):
            for entity in entities:
                if entity.tag in seen:
                    continue
                if len(self._seen_units) + len(self._seen_structures) >= MAX_METRIC_TAGS:
                    self._tags_overflow = True
                    continue
                seen[entity.tag] = entity.type_name
        self._last_units = frozenset(u.tag for u in observation.own_units)
        self._last_structures = frozenset(s.tag for s in observation.own_structures)

    # -- decisions ------------------------------------------------------------

    def record_decision(self, facts: Mapping[str, JsonValue]) -> None:
        """Count requests, answers, failures, models, latency and tokens from one poll.

        A new ``answer_request_id`` is one answer, classified by the poll's source and
        reason. A pending request that is no longer pending without becoming the
        answer failed with the poll's reason. Cumulative counters (calls, tokens)
        keep their largest value.
        """
        if self._failure is not None:
            return
        provider = facts.get("decision_provider")
        if isinstance(provider, str):
            self._provider = provider
            if len(self._providers) < MAX_METRIC_KEYS:
                self._providers.add(provider)
        requested = facts.get("requested_model")
        if isinstance(requested, str):
            self._requested_model = requested
        self._max_requests = _int_fact(facts, "max_requests", self._max_requests)
        self._calls = max(self._calls, _int_fact(facts, "calls", 0) or 0)
        self._input_tokens = max(self._input_tokens, _int_fact(facts, "input_tokens", 0) or 0)
        self._output_tokens = max(self._output_tokens, _int_fact(facts, "output_tokens", 0) or 0)
        answer_id = _int_fact(facts, "answer_request_id", None)
        pending_id = _int_fact(facts, "pending_request_id", None)
        if answer_id is not None and answer_id != self._last_answer_id:
            self._last_answer_id = answer_id
            self._record_answer(facts)
        finished = self._last_pending_id
        if finished is not None and pending_id != finished and answer_id != finished:
            reason = facts.get("reason")
            code = reason if isinstance(reason, str) else "unknown"
            _bounded_count(self._failures, code)
            if code in AUTH_FAILURE_REASONS:
                self._auth_failures += 1
        self._last_pending_id = pending_id

    def _record_answer(self, facts: Mapping[str, JsonValue]) -> None:
        self._answers += 1
        model = facts.get("model")
        _bounded_count(self._returned_models, model if isinstance(model, str) else "<missing>")
        reason = facts.get("reason")
        if facts.get("source") == "typesafe" or reason == ACCEPTED_REASON:
            self._accepted += 1
        elif reason in STALE_REASONS:
            self._stale += 1
        elif reason == LOW_CONFIDENCE_REASON:
            self._low_confidence += 1
        else:
            self._other_answers += 1
        latency = facts.get("latency_ms")
        if (
            isinstance(latency, int | float)
            and not isinstance(latency, bool)
            and math.isfinite(latency)
            and len(self._latencies) < MAX_LATENCY_SAMPLES
        ):
            self._latencies.append(float(latency))

    # -- summary --------------------------------------------------------------

    def summary(self, *, end_game_seconds: float) -> dict[str, JsonValue]:
        """The compact cumulative record the runner archives (plan D6).

        The span from the last sample to ``end_game_seconds`` is integrated like any
        other (or counted missed when longer than the sample gap); the accumulator
        itself is not changed, so the summary can be taken more than once.
        """
        covered, missed, max_gap = self._covered, self._missed, self._max_gap
        bank, worker_time = self._bank, self._worker_time
        blocked, idle = self._blocked, self._idle_gateways
        tail = 0.0
        if self._last is not None and math.isfinite(end_game_seconds):
            tail = end_game_seconds - self._last
        previous = self._previous
        if tail > 0 and previous is not None:
            if tail > METRIC_SAMPLE_GAP_SECONDS:
                missed += tail
            else:
                covered += tail
                bank += previous.minerals * tail
                worker_time += previous.workers * tail
                blocked += tail if previous.supply_blocked else 0.0
                idle += previous.idle_gateways * tail
            max_gap = max(max_gap, tail)
        usable = self._failure is None and self._samples > 0
        if self._failure is not None:
            status = "failed"
        elif self._samples == 0:
            status = "unavailable"
        elif missed > 0 or self._tags_overflow:
            status = "incomplete"
        else:
            status = "complete"
        timed = usable and covered > 0

        def measured(value: float) -> dict[str, JsonValue]:
            if not timed:
                return {"status": "unavailable", "value": None}
            return {"status": "measured", "value": round(value, 3)}

        record: dict[str, JsonValue] = {
            "schema_version": METRICS_SCHEMA_VERSION,
            "status": status,
            "failure": self._failure,
            "sampling": {
                "max_gap_seconds": METRIC_SAMPLE_GAP_SECONDS,
                "samples": self._samples,
                "first_sample_game_seconds": self._first,
                "last_sample_game_seconds": self._last,
                "end_game_seconds": end_game_seconds if math.isfinite(end_game_seconds) else None,
                "covered_game_seconds": round(covered, 3),
                "missed_game_seconds": round(missed, 3),
                "largest_gap_game_seconds": round(max_gap, 3),
            },
        }
        for name in _MILESTONES:
            at = self._milestones[name]
            if not usable:
                record[name] = {"status": "unavailable", "game_seconds": None}
            elif at is None:
                record[name] = {"status": "not_reached", "game_seconds": None}
            else:
                record[name] = {"status": "observed", "game_seconds": round(at, 3)}
        record["supply_blocked_game_seconds"] = measured(blocked)
        record["idle_gateway_game_seconds"] = measured(idle)
        record["mean_mineral_bank"] = measured(bank / covered if covered > 0 else 0.0)
        record["mean_vespene_bank"] = {
            "status": "unavailable",
            "value": None,
            "reason": "the observation has no vespene field",
        }
        if timed and previous is not None:
            record["workers"] = {
                "status": "measured",
                "final": previous.workers,
                "max": self._workers_max,
                "mean": round(worker_time / covered, 3),
            }
        else:
            record["workers"] = {"status": "unavailable", "final": None, "max": None, "mean": None}
        record["unit_losses"] = self._losses(usable)
        record["plan_aborts"] = {
            "status": "unavailable",
            "value": None,
            "reason": "this runtime has no plan store",
        }
        record["decisions"] = self._decision_summary()
        return record

    def _losses(self, usable: bool) -> dict[str, JsonValue]:
        if not usable:
            return {"status": "unavailable", "units": None, "structures": None, "by_type": {}}
        by_type: Counter[str] = Counter()
        units = structures = 0
        for tag, type_name in self._seen_units.items():
            if tag not in self._last_units:
                units += 1
                _bounded_count(by_type, type_name)
        for tag, type_name in self._seen_structures.items():
            if tag not in self._last_structures:
                structures += 1
                _bounded_count(by_type, type_name)
        return {
            "status": "incomplete" if self._tags_overflow else "measured",
            "units": units,
            "structures": structures,
            "by_type": _count_map(by_type),
        }

    def _decision_summary(self) -> dict[str, JsonValue]:
        if self._failure is not None or self._provider is None:
            return {"status": "unavailable"}
        latency: JsonValue = None
        if self._latencies:
            latency = {
                "count": len(self._latencies),
                "mean": round(statistics.fmean(self._latencies), 1),
                "median": round(statistics.median(self._latencies), 1),
                "min": round(min(self._latencies), 1),
                "max": round(max(self._latencies), 1),
            }
        return {
            "status": "measured",
            "provider": self._provider if len(self._providers) == 1 else "mixed",
            "requested_model": self._requested_model,
            "max_requests": self._max_requests,
            "calls": self._calls,
            "answers": self._answers,
            "accepted": self._accepted,
            "stale": self._stale,
            "low_confidence": self._low_confidence,
            "other_answers": self._other_answers,
            "failures": _count_map(self._failures),
            "authentication_failures": self._auth_failures,
            "returned_models": _count_map(self._returned_models),
            "latency_ms": latency,
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "pending_at_end": self._last_pending_id is not None,
        }


def _int_fact(facts: Mapping[str, JsonValue], name: str, default: int | None) -> int | None:
    value = facts.get(name)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


class JevController:
    """Drive one match: observation -> runtime tick -> adapter command issue.

    The runtime uses the plan's default cadence, budgets and task lifecycle. The
    wall-clock limit counts from the first game step, so SC2's startup is not
    charged to it (burnysc2 bounds the launch with its own connect timeout);
    ``clock`` is injectable. ``recorder`` receives every live step's events (the
    runner always passes one; without it nothing is persisted). Raises
    :class:`ValueError` for an invalid run id or limits.
    """

    def __init__(
        self,
        bundle: PolicyBundle,
        *,
        run_id: str,
        limits: MatchLimits,
        clock: Callable[[], float] = time.monotonic,
        recorder: RunRecorder | None = None,
        decisions: ArmyDecisions | None = None,
    ) -> None:
        if not isinstance(limits, MatchLimits):
            raise ValueError(f"limits must be MatchLimits, got {safe_repr(limits)}")
        self.runtime = JevRuntime(bundle.policy, run_id=run_id)
        self.adapter = Sc2Adapter()
        self._limits = limits
        self._clock = clock
        self._started: float | None = None  # wall clock at the first game step
        self._game: Any = None
        self._port: GamePort | None = None
        self._stop_requested = False
        self._terminal: TerminalReason | None = None
        self._crash: JevError | None = None
        self._left = False
        self._leave_attempts = 0
        self._leave_failure: str | None = None
        self._result: MatchResult | None = None
        self._game_seconds = 0.0
        self._recent: deque[Event] = deque(maxlen=RECENT_EVENT_LIMIT)
        self._recorder = recorder
        self._evidence_failure: JevError | None = None
        self._decisions = decisions
        self._parameters = bundle.policy.parameters
        self.metrics = MatchMetrics()  # observation-only (plan D6)

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    @property
    def terminal(self) -> TerminalReason | None:
        """Why the controller ended the match, if it did."""
        return self._terminal

    @property
    def attached(self) -> bool:
        """Whether the bot was bound to a running game (``on_start`` ran)."""
        return self._port is not None

    @property
    def left(self) -> bool:
        """Whether the bot has left the game (a leave request succeeded)."""
        return self._left

    @property
    def leave_failure(self) -> str | None:
        """Why the latest leave attempt failed, while the bot has not left (rendered,
        capped); a diagnostic, not a crash."""
        return self._leave_failure

    @property
    def crash(self) -> JevError | None:
        """The ``match_crashed`` error, once the match crashed (the first crash wins)."""
        return self._crash

    @property
    def evidence_failure(self) -> JevError | None:
        """The ``persistence_failed`` error, once run evidence could not be written."""
        return self._evidence_failure

    @property
    def recorder(self) -> RunRecorder | None:
        """The run's evidence recorder, if any."""
        return self._recorder

    @property
    def result(self) -> MatchResult | None:
        """The SC2 result reported at match end, if any."""
        return self._result

    @property
    def game_seconds(self) -> float:
        """Game time of the latest step."""
        return self._game_seconds

    @property
    def recent_events(self) -> tuple[Event, ...]:
        """The latest :data:`~jev.contracts.RECENT_EVENT_LIMIT` runtime events, oldest
        first: every event, not only the ones the run trace keeps."""
        return tuple(self._recent)

    def attach(self, game: Any, port: GamePort) -> None:
        """Bind the live game (a burnysc2 ``BotAI``) and its command port."""
        self._game = game
        self._port = port

    def request_stop(self) -> None:
        """Ask for a clean leave at the next step (Ctrl+C)."""
        self._stop_requested = True

    def finish(self, result: MatchResult | None) -> None:
        """Record the result SC2 reported when the match ended."""
        self._result = result

    def record_crash(self, exc: BaseException) -> None:
        """Record that the match crashed; the bot leaves the game at the next step.

        The message is rendered and capped. A crash overrides any other terminal
        reason, and only the first crash's error is kept.
        """
        if self._crash is None:
            self._crash = exception_error("match_crashed", exc)
        self._terminal = "crashed"

    async def step(self) -> TickResult | None:
        """Run one game step; the tick result, or None when the policy did not tick.

        Raises :class:`RuntimeError` before :meth:`attach`, and :class:`LeaveFailed`
        once leaving has failed :data:`MAX_LEAVE_ATTEMPTS` times; nothing else
        escapes. A failure in the step's own pipeline -- malformed game state (the
        adapter's ValueError), a runtime error, an exception from the SC2 port -- is
        recorded with :meth:`record_crash` and the bot leaves the game; evidence
        that cannot be persisted ends the match as ``persistence_failed``. Once the
        match has ended for any reason the bot only leaves: a failed leave is kept as
        :attr:`leave_failure` and retried on the next step. It never changes why the
        match ended: a clean stop or time limit stays one (SC2 may already have ended
        the game on the same step).
        """
        port = self._port
        if port is None:
            raise RuntimeError("JevController.step() called before attach()")
        if self._terminal is not None:
            await self._leave(port)
            return None
        try:
            return await self._step(port)
        except LeaveFailed:
            raise
        except PersistenceFailed as exc:
            self._evidence_failure = JevError(exc.code, exc.message)
            await self._end(port, "persistence_failed")
            return None
        except Exception as exc:
            self.record_crash(exc)
            await self._leave(port)
            return None

    async def _step(self, port: GamePort) -> TickResult | None:
        if self._stop_requested:
            await self._end(port, "stopped")
            return None
        now = self._clock()
        if self._started is None:
            self._started = now
        if now - self._started >= self._limits.max_wall_seconds:
            await self._end(port, "wall_timeout")
            return None
        game_seconds = self.adapter.game_seconds(self._game)
        self._game_seconds = game_seconds
        if game_seconds >= self._limits.max_game_seconds:
            await self._end(port, "game_timeout")
            return None
        self.adapter.collect_rejections(port, self.runtime)
        if not self.runtime.is_due(game_seconds):
            self.adapter.remember(self._game, port)
            self._publish(())
            return None
        observation = self.adapter.observe(self._game, port)
        facts: dict[str, JsonValue]
        if self._decisions is not None:
            mode, facts = self._decisions.poll(
                observation,
                launched=self.runtime.latches.get("attack_launched", False),
                first_wave=int(self._parameters["first_attack_zealots"]),  # type: ignore[arg-type]
                defense_radius=float(self._parameters["defense_radius"]),  # type: ignore[arg-type]
                demoted_defense=self.runtime.demoted_targets("army.defend.attack"),
            )
            self.runtime.set_army_decision(mode, facts)
        else:
            facts = {
                "decision_provider": "scripted",
                "source": "scripted",
                "choice": None,
                "reason": "local_graph",
                "calls": 0,
            }
            self.runtime.set_army_decision(None, facts)
        result = self.runtime.tick(observation)
        if result.ticked:
            await self.adapter.issue(result.commands, port, self.runtime)
            self._recent.extend(result.events)
        self._measure(observation, facts)
        self._publish(result.events)
        return result

    def _measure(self, observation: Observation, facts: Mapping[str, JsonValue]) -> None:
        """Feed the observation-only accumulators; a defect there disables them only."""
        try:
            self.metrics.record_decision(facts)
            self.metrics.sample(
                observation, attack_launched=self.runtime.latches.get("attack_launched", False)
            )
        except Exception as exc:  # never a crash: metrics must not change the match
            self.metrics.fail(exc)

    def _publish(self, events: tuple[Event, ...]) -> None:
        if self._recorder is not None:
            self._recorder.update(self.runtime, events)

    async def _end(self, port: GamePort, reason: TerminalReason) -> None:
        self._terminal = reason
        await self._leave(port)

    async def _leave(self, port: GamePort) -> None:
        """One leave attempt per call until one succeeds; LeaveFailed when exhausted."""
        await self.close_decisions()
        if self._left:
            return
        if self._leave_attempts < MAX_LEAVE_ATTEMPTS:
            self._leave_attempts += 1
            try:
                await port.leave()
            except Exception as exc:  # a diagnostic, not a crash: the reason to end stands
                name = safe_repr(type(exc).__name__)
                detail = safe_exception_text(exc)
                self._leave_failure = render_text(f"{name}: {detail}")[:MAX_MESSAGE_CHARS]
            else:
                self._left = True
                self._leave_failure = None
                return
            if self._leave_attempts < MAX_LEAVE_ATTEMPTS:
                return  # retried on the next step
        raise LeaveFailed(
            f"leaving the game failed {self._leave_attempts} times; "
            f"last failure: {self._leave_failure}"
        )

    async def close_decisions(self) -> None:
        if self._decisions is not None:
            await self._decisions.close()


class JevBot(BotAI):
    """The burnysc2 bot: every decision comes from :class:`JevController`."""

    def __init__(self, controller: JevController) -> None:
        super().__init__()
        self.controller = controller

    async def on_start(self) -> None:
        self.controller.attach(self, BotAIPort(self))
        try:
            self._register_stop_request()
        except Exception as exc:  # burnysc2 would turn it into a silent Defeat
            self.controller.record_crash(exc)

    async def on_step(self, iteration: int) -> None:
        # Pipeline failures are recorded, never raised; only LeaveFailed escapes, so
        # burnysc2 ends a game the bot could not leave.
        await self.controller.step()

    async def on_end(self, game_result: Result) -> None:
        await self.controller.close_decisions()
        self.controller.finish(match_result(game_result))

    def _register_stop_request(self) -> None:
        """Ctrl+C asks for a clean leave; a second Ctrl+C falls back to burnysc2.

        burnysc2 installs its own SIGINT handler when it launches SC2 (it cleans
        up only the SC2 process it started); it is kept as the second-press
        fallback. Signal handlers can only be set from the main thread.
        """
        if threading.current_thread() is not threading.main_thread():
            return
        previous = signal.getsignal(signal.SIGINT)
        controller = self.controller

        def request_leave(signum: int, frame: FrameType | None) -> None:
            if controller.stop_requested and callable(previous):
                previous(signum, frame)
                return
            controller.request_stop()

        signal.signal(signal.SIGINT, request_leave)
