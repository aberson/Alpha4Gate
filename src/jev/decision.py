"""Typesafe's structured army decision, scheduled without blocking SC2 steps.

Only this module knows about the hosted service. The graph still selects actors,
targets and legal commands. No credentials or raw service errors enter telemetry.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import httpx

from jev.contracts import Entity, JsonValue, Observation

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
QUESTION = (
    "Choose the army's next intent in StarCraft II. We are Protoss executing a "
    "one-base four-Gateway Zealot rush. First attack needs four ready Zealots; "
    "after launch, reinforce continuously. Defend endangered home structures, "
    "attack when pressure is worthwhile, regroup when the army should gather or "
    "recover. Zealots fight ground targets only. Fog of war hides enemies; absence "
    "of visible enemies does not imply safety. Choose only an offered option. "
    "Local code handles targets, movement, production and resources."
)
CRITERIA = {
    "attack": "Press the enemy and reinforce the rush.",
    "defend": "Fight visible ground threats near our home structures.",
    "regroup": "Gather the army at our home rally point before resuming pressure.",
}


@dataclass(frozen=True)
class DecisionConfig:
    model: str = "jev-latest"
    interval: float = 2.0  # wall seconds between dispatches, including failures
    timeout: float = 1.5
    max_wall_age: float = 5.0
    max_game_age: float = 8.0
    max_requests: int = 450
    min_confidence: float = 0.5

    def __post_init__(self) -> None:
        if not self.model or len(self.model) > 100 or not self.model.isprintable():
            raise ValueError("model must be a printable identifier of 1..100 characters")
        for name in ("interval", "timeout", "max_wall_age", "max_game_age"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= 300:
                raise ValueError(f"{name} must be finite and in (0, 300]")
        if type(self.max_requests) is not int or not 1 <= self.max_requests <= 10000:
            raise ValueError("max_requests must be an integer in 1..10000")
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("min_confidence must be in [0, 1]")


@dataclass(frozen=True)
class Answer:
    choice: str
    confidence: float
    probabilities: dict[str, JsonValue]
    model: str
    input_tokens: int
    output_tokens: int


class DecisionError(Exception):
    """A fixed, credential-free failure code suitable for recorded evidence."""


class DecisionProvider(Protocol):
    async def decide(self, state: dict[str, JsonValue], choices: tuple[str, ...]) -> Answer: ...


class TypesafeProvider:
    def __init__(
        self,
        api_key: str,
        config: DecisionConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("TYPESAFE_API_KEY is required for the typesafe decision provider")
        self._key = api_key
        self._config = config
        self._transport = transport

    async def decide(self, state: dict[str, JsonValue], choices: tuple[str, ...]) -> Answer:
        import httpx

        # Per-request context also closes the connection on task cancellation.
        async with httpx.AsyncClient(
            timeout=self._config.timeout, transport=self._transport, follow_redirects=False
        ) as client:
            try:
                async with client.stream(
                    "POST",
                    ENDPOINT,
                    headers={"Authorization": f"Bearer {self._key}"},
                    json={
                        "model": self._config.model,
                        "state": state,
                        "questions": {
                            "army_mode": {
                                "type": "choice",
                                "instructions": QUESTION,
                                "criteria": {c: CRITERIA[c] for c in choices},
                            }
                        },
                    },
                ) as response:
                    if response.status_code != 200:
                        raise DecisionError(f"http_{response.status_code}")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 65536:
                            raise DecisionError("response_too_large")
                return parse_answer(json.loads(body), choices)
            except httpx.TimeoutException:
                raise DecisionError("timeout") from None
            except httpx.HTTPError:
                raise DecisionError("connection_error") from None
            except (ValueError, KeyError, TypeError):
                raise DecisionError("invalid_response") from None


def parse_answer(body: object, choices: tuple[str, ...]) -> Answer:
    def probability(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
            raise ValueError("invalid probability")
        return float(value)

    if not isinstance(body, dict):
        raise ValueError("expected object")
    answer = body["answers"]["army_mode"]
    choice = answer["choice"]
    if answer["type"] != "choice" or choice not in choices:
        raise ValueError("invalid choice")
    probabilities = answer["probabilities"]
    if not isinstance(probabilities, dict) or set(probabilities) != set(choices):
        raise ValueError("invalid probability keys")
    probs: dict[str, JsonValue] = {key: probability(value) for key, value in probabilities.items()}
    if abs(sum(float(v) for v in probabilities.values()) - 1) > 0.02:
        raise ValueError("invalid probability sum")
    if probabilities[choice] < max(probabilities.values()):
        raise ValueError("choice is not a highest-probability option")
    model = body["model"]
    if not isinstance(model, str) or not model or len(model) > 100 or not model.isprintable():
        raise ValueError("invalid model")
    usage = body["usage"]
    for name in ("input_tokens", "output_tokens"):
        if type(usage[name]) is not int or not 0 <= usage[name] <= 10**9:
            raise ValueError("invalid usage")
    return Answer(
        choice,
        probability(answer["confidence"]),
        probs,
        model,
        usage["input_tokens"],
        usage["output_tokens"],
    )


def context(
    observation: Observation,
    launched: bool,
    first_wave: int,
    defense_radius: float,
    demoted_defense: frozenset[int] = frozenset(),
) -> tuple[dict[str, JsonValue], tuple[str, ...], tuple[object, ...]]:
    army = tuple(u for u in observation.own_units if u.type_name == "Zealot" and u.is_ready)
    bases = observation.own_structures
    # Match the packaged graph's home-defense predicate and target filter.
    threats = tuple(
        e
        for e in observation.visible_enemies
        if not e.is_flying
        and not e.is_structure
        and e.tag not in demoted_defense
        and math.dist(e.position, observation.start_location) <= defense_radius
    )
    choices = tuple(
        c
        for c in CRITERIA
        if c == "regroup"
        or (c == "attack" and bool(army) and (launched or len(army) >= first_wave))
        or (c == "defend" and bool(army) and bool(threats))
    )

    def entities(items: tuple[Entity, ...]) -> list[JsonValue]:
        return [
            {
                "tag": str(e.tag),
                "type": e.type_name,
                "position": list(e.position),
                "health": e.health,
                "shield": e.shield,
                "flying": e.is_flying,
            }
            for e in sorted(
                items, key=lambda e: (math.dist(e.position, observation.start_location), e.tag)
            )[:64]
        ]

    state: dict[str, JsonValue] = {
        "game_seconds": observation.game_seconds,
        "minerals": observation.minerals,
        "supply_used": observation.supply_used,
        "supply_cap": observation.supply_cap,
        "attack_launched": launched,
        "first_wave_size": first_wave,
        "ready_zealots": len(army),
        "army": entities(army),
        "own_structure_counts": dict(Counter(e.type_name for e in bases)),
        "visible_enemies": entities(observation.visible_enemies),
        "home_threats": entities(threats),
        "remembered_enemy_structures": entities(observation.remembered_enemy_structures),
        "home": list(observation.start_location),
        "enemy_starts": [list(p) for p in observation.enemy_start_locations],
        "entity_list_limit": 64,
    }
    # Strategic changes invalidate a reply even if the time limit has not expired.
    signature = (
        tuple(sorted(e.tag for e in army)),
        tuple(sorted(e.tag for e in threats)),
        tuple(sorted(e.tag for e in bases)),
        launched,
        choices,
    )
    return state, choices, signature


class ArmyDecisions:
    """One outstanding request; no awaiting service work on the gameplay path."""

    def __init__(
        self,
        provider: DecisionProvider,
        config: DecisionConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider, self.config, self.clock = provider, config, clock
        self.task: asyncio.Task[Answer] | None = None
        self.sent = -math.inf
        self.game_sent = 0.0
        self.signature: tuple[object, ...] = ()
        self.state: dict[str, JsonValue] = {}
        self.choices: tuple[str, ...] = ()
        self.accepted: tuple[Answer, float, float, tuple[object, ...]] | None = None
        self.calls = self.input_tokens = self.output_tokens = 0
        self.reason = "awaiting_first_answer"
        self.latency: float | None = None
        self.last_answer: Answer | None = None
        self.answer_request_id: int | None = None
        self.answer_game_seconds: float | None = None
        self.answer_options: tuple[str, ...] = ()
        self.answer_latency: float | None = None
        self.disabled = False

    async def _request(self) -> Answer:
        # Total deadline includes connection, body streaming and provider scheduling.
        try:
            async with asyncio.timeout(self.config.timeout):
                return await self.provider.decide(self.state, self.choices)
        finally:
            self.latency = round((self.clock() - self.sent) * 1000, 1)

    def poll(
        self,
        obs: Observation,
        *,
        launched: bool,
        first_wave: int,
        defense_radius: float,
        demoted_defense: frozenset[int] = frozenset(),
    ) -> tuple[str | None, dict[str, JsonValue]]:
        now = self.clock()
        state, choices, signature = context(
            obs, launched, first_wave, defense_radius, demoted_defense
        )
        if self.task is not None and self.task.done():
            self.accepted = None
            try:
                answer = self.task.result()
                self.last_answer = answer
                self.answer_request_id = self.calls
                self.answer_game_seconds = self.game_sent
                self.answer_options = self.choices
                self.answer_latency = self.latency
                self.input_tokens += answer.input_tokens
                self.output_tokens += answer.output_tokens
                if (
                    signature != self.signature
                    or now - self.sent > self.config.max_wall_age
                    or obs.game_seconds - self.game_sent > self.config.max_game_age
                ):
                    self.reason = "stale_answer"
                elif answer.confidence < self.config.min_confidence:
                    self.reason = "low_confidence"
                else:
                    self.accepted = (answer, self.sent, self.game_sent, signature)
                    self.reason = "accepted"
            except (Exception, asyncio.CancelledError) as exc:
                self.reason = str(exc) if isinstance(exc, DecisionError) else "request_failed"
                if self.reason in ("http_401", "http_403"):
                    self.disabled = True
                self.accepted = None
            self.task = None
        mode = None
        if self.accepted is not None:
            answer, wall, game, old_signature = self.accepted
            if (
                signature == old_signature
                and now - wall <= self.config.max_wall_age
                and obs.game_seconds - game <= self.config.max_game_age
            ):
                mode = answer.choice
            else:
                self.accepted = None
                self.reason = "expired_or_changed_state"
        if (
            not self.disabled
            and self.task is None
            and now - self.sent >= self.config.interval
            and self.calls < self.config.max_requests
            and len(choices) > 1
        ):
            self.state, self.choices, self.signature = state, choices, signature
            self.sent, self.game_sent = now, obs.game_seconds
            self.calls += 1
            self.task = asyncio.create_task(self._request())
        if self.calls >= self.config.max_requests and self.task is None and mode is None:
            self.reason = "request_limit"
        last_answer = self.last_answer
        facts: dict[str, JsonValue] = {
            "decision_provider": "typesafe",
            "source": "typesafe" if mode else "scripted_fallback",
            "choice": mode,
            "reason": self.reason,
            "pending": self.task is not None,
            "requested_model": self.config.model,
            "model": last_answer.model if last_answer else None,
            "question": QUESTION,
            "options": list(self.answer_options if last_answer else self.choices),
            "available_options": list(choices),
            "answer_request_id": self.answer_request_id,
            "pending_request_id": self.calls if self.task is not None else None,
            "answer": last_answer.choice if last_answer else None,
            "confidence": last_answer.confidence if last_answer else None,
            "probabilities": last_answer.probabilities if last_answer else {},
            "latency_ms": self.answer_latency,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "max_requests": self.config.max_requests,
            "observation_game_seconds": self.answer_game_seconds,
            "response_age_game_seconds": obs.game_seconds - self.accepted[2]
            if self.accepted
            else None,
        }
        return mode, facts

    async def close(self) -> None:
        self.disabled = True
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except (Exception, asyncio.CancelledError):
                pass
            self.task = None
