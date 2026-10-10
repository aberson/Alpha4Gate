"""Typesafe decision provider, coordinator, and production graph integration.

Jev v2 (plan J2 Step 219): the shared provider boundary sends any typed question
by its own key, and :class:`jev.strategy.HostedQuestions` schedules the strategy
and army questions on one request budget -- exercised here against a scripted
provider at the :class:`jev.decision.DecisionProvider` boundary (the production
controller and graph scenarios are in ``test_jev_v2.py``).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from bots.jev.v1 import load_policy
from bots.jev.v2 import load_policy as load_v2_policy
from sc2.data import Result

from jev import runner
from jev.bot import JevBot, JevController
from jev.contracts import Entity, Observation
from jev.decision import (
    ARMY_QUESTION,
    CRITERIA,
    QUESTION,
    Answer,
    ArmyDecisions,
    DecisionConfig,
    DecisionError,
    Question,
    TypesafeProvider,
    context,
    parse_answer,
)
from jev.plans import STRATEGY_CHOICES, PlanRecord
from jev.runner import EXIT_FAILURE, MatchLimits, MatchOptions, run_match
from jev.runtime import JevRuntime
from jev.strategy import (
    ANSWER_WAIT_EXPIRED_REASON,
    ARMY_V2_QUESTION,
    POSTURE_HOLD_SECONDS,
    STARVATION_SECONDS,
    STRATEGY_INTERVAL,
    STRATEGY_QUESTION,
    HostedQuestions,
    answer_wait_seconds,
    army_context,
    strategy_branches,
    uses_strategy,
)
from jev.telemetry import RunRecorder, read_run_metadata, read_run_state
from test_jev_sc2 import START, _game, _Port, _unit


def _entity(
    tag: int,
    kind: str,
    position: tuple[float, float],
    *,
    structure: bool = False,
    flying: bool = False,
    ready: bool = True,
) -> Entity:
    return Entity(
        tag,
        kind,
        position,
        100,
        is_structure=structure,
        is_flying=flying,
        ready=ready,
    )


def _observation(
    seconds: float = 10,
    *,
    zealots: int = 4,
    enemies: tuple[Entity, ...] = (),
    structures: tuple[Entity, ...] | None = None,
) -> Observation:
    army = tuple(_entity(2000 + i, "Zealot", START) for i in range(zealots))
    home = structures or (_entity(1000, "Nexus", START, structure=True),)
    return Observation(
        round(seconds * 22.4),
        seconds,
        0,
        12,
        30,
        army,
        home,
        enemies,
        (),
        START,
        ((120.5, 120.5),),
        (START, (120.5, 120.5)),
        (75.5, 75.5),
    )


def _body(choice: str = "attack", *, model: str = "jev-observed") -> dict[str, Any]:
    probabilities = {"attack": 0.1, "defend": 0.1, "regroup": 0.1}
    probabilities[choice] = 0.8
    return {
        "answers": {
            "army_mode": {
                "type": "choice",
                "choice": choice,
                "confidence": probabilities[choice],
                "probabilities": probabilities,
            }
        },
        "model": model,
        "usage": {"input_tokens": 123, "output_tokens": 7},
    }


def test_typesafe_provider_sends_bearer_model_state_and_choice_question() -> None:
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_body())

    config = DecisionConfig(model="jev-test-model")
    provider = TypesafeProvider("secret-token", config, transport=httpx.MockTransport(respond))
    state = {"game_seconds": 12.5, "ready_zealots": 4}
    answer = asyncio.run(provider.decide(state, ("attack", "defend", "regroup")))

    assert answer == Answer(
        "attack",
        0.8,
        {"attack": 0.8, "defend": 0.1, "regroup": 0.1},
        "jev-observed",
        123,
        7,
    )
    (request,) = requests
    assert (request.method, str(request.url)) == (
        "POST",
        "https://api.typesafe.ai/v1/systemone",
    )
    assert request.headers["Authorization"] == "Bearer secret-token"
    sent = json.loads(request.content)
    assert sent["model"] == "jev-test-model" and sent["state"] == state
    question = sent["questions"]["army_mode"]
    assert question["type"] == "choice"
    assert set(question["criteria"]) == {"attack", "defend", "regroup"}


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401), "http_401"),
        (httpx.Response(503), "http_503"),
        (httpx.Response(200, content=b"not json"), "invalid_response"),
        (httpx.Response(200, json={"answers": {}}), "invalid_response"),
    ],
)
def test_typesafe_provider_turns_http_and_schema_failures_into_safe_codes(
    response: httpx.Response, code: str
) -> None:
    provider = TypesafeProvider(
        "key", DecisionConfig(), transport=httpx.MockTransport(lambda request: response)
    )
    with pytest.raises(DecisionError, match=f"^{code}$"):
        asyncio.run(provider.decide({}, ("attack", "regroup")))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body["answers"]["army_mode"].update(choice="retreat"),
        lambda body: body["answers"]["army_mode"].update(confidence=True),
        lambda body: body["answers"]["army_mode"].update(probabilities={"attack": 1.0}),
        lambda body: body["answers"]["army_mode"].update(
            probabilities={"attack": 0.6, "defend": 0.1, "regroup": 0.1}
        ),
        lambda body: body.update(model=""),
        lambda body: body.update(usage={"input_tokens": -1, "output_tokens": 2}),
    ],
)
def test_answer_validation_rejects_untrusted_response_fields(mutate: Any) -> None:
    body = _body()
    mutate(body)
    with pytest.raises((ValueError, KeyError, TypeError)):
        parse_answer(body, ("attack", "defend", "regroup"))


def test_answer_validation_rejects_choice_that_disagrees_with_probabilities() -> None:
    body = _body("regroup")
    body["answers"]["army_mode"]["probabilities"] = {
        "attack": 0.8,
        "defend": 0.1,
        "regroup": 0.1,
    }
    with pytest.raises(ValueError, match="highest-probability"):
        parse_answer(body, ("attack", "defend", "regroup"))


def test_answer_validation_accepts_a_choice_tied_for_highest_probability() -> None:
    body = _body("regroup")
    body["answers"]["army_mode"]["probabilities"] = {
        "attack": 0.45,
        "defend": 0.1,
        "regroup": 0.45,
    }
    assert parse_answer(body, ("attack", "defend", "regroup")).choice == "regroup"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _Provider:
    def __init__(self, answers: list[Answer | BaseException]) -> None:
        self.answers = answers
        self.calls: list[tuple[dict[str, Any], tuple[str, ...]]] = []

    async def decide(self, state: dict[str, Any], choices: tuple[str, ...]) -> Answer:
        self.calls.append((dict(state), choices))
        result = self.answers.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _answer(choice: str, confidence: float = 0.9) -> Answer:
    probs = {"attack": 0.05, "defend": 0.05, "regroup": 0.05}
    probs[choice] = confidence
    return Answer(choice, confidence, probs, "reported-model", 10, 2)


async def _settle() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def test_context_exposes_bounded_entities_choices_and_full_change_signature() -> None:
    threat = _entity(7000, "Marine", (35, 35))
    flying = _entity(7001, "Medivac", (35, 35), flying=True)
    far = _entity(7002, "Marine", (100, 100))
    obs = _observation(enemies=(threat, flying, far))
    state, choices, signature = context(obs, False, 4, 20)

    assert choices == ("attack", "defend", "regroup")
    assert [u["tag"] for u in state["army"]] == [str(2000 + i) for i in range(4)]
    assert [u["tag"] for u in state["home_threats"]] == ["7000"]
    assert state["own_structure_counts"] == {"Nexus": 1}
    assert signature == (
        (2000, 2001, 2002, 2003),
        (7000,),
        (1000,),
        False,
        choices,
    )


def test_context_defense_matches_graph_home_and_nonstructure_filters() -> None:
    far_pylon = _entity(1001, "Pylon", (100, 100), structure=True)
    enemy_at_pylon = _entity(7000, "Marine", (100, 100))
    enemy_structure_at_home = _entity(7001, "Bunker", (35, 35), structure=True)
    obs = _observation(
        enemies=(enemy_at_pylon, enemy_structure_at_home),
        structures=(
            _entity(1000, "Nexus", START, structure=True),
            far_pylon,
        ),
    )

    state, choices, _ = context(obs, True, 4, 20)
    assert "defend" not in choices
    assert state["home_threats"] == []


def test_demotion_invalidates_accepted_defend_and_removes_it_from_next_request() -> None:
    async def scenario() -> None:
        threat = _entity(7000, "Marine", (35, 35))
        obs = _observation(enemies=(threat,))
        provider = _Provider([_answer("defend"), _answer("regroup")])
        clock = _Clock()
        decisions = ArmyDecisions(
            provider,
            DecisionConfig(interval=0.1, timeout=10, max_requests=2),
            clock=clock,
        )

        decisions.poll(obs, launched=True, first_wave=4, defense_radius=20)
        await _settle()
        mode, facts = decisions.poll(
            obs, launched=True, first_wave=4, defense_radius=20
        )
        assert mode == "defend" and facts["reason"] == "accepted"

        clock.now = 1
        mode, facts = decisions.poll(
            obs,
            launched=True,
            first_wave=4,
            defense_radius=20,
            demoted_defense=frozenset({7000}),
        )
        assert mode is None and facts["reason"] == "expired_or_changed_state"
        assert facts["available_options"] == ["attack", "regroup"]
        await _settle()
        assert provider.calls[1][1] == ("attack", "regroup")
        await decisions.close()

    asyncio.run(scenario())


def test_coordinator_is_nonblocking_keeps_one_request_and_honors_cadence_and_cap() -> None:
    async def scenario() -> None:
        gate = asyncio.Event()

        class WaitingProvider:
            calls = 0

            async def decide(self, state: dict[str, Any], choices: tuple[str, ...]) -> Answer:
                self.calls += 1
                await gate.wait()
                return _answer("attack")

        provider = WaitingProvider()
        clock = _Clock()
        decisions = ArmyDecisions(
            provider, DecisionConfig(interval=2, timeout=10, max_requests=1), clock=clock
        )
        obs = _observation()
        mode, facts = decisions.poll(obs, launched=True, first_wave=4, defense_radius=20)
        assert mode is None and facts["pending"] is True and provider.calls == 0
        await _settle()
        assert provider.calls == 1
        clock.now = 1
        decisions.poll(obs, launched=True, first_wave=4, defense_radius=20)
        assert provider.calls == 1 and decisions.calls == 1
        gate.set()
        await _settle()
        mode, facts = decisions.poll(obs, launched=True, first_wave=4, defense_radius=20)
        assert mode == "attack" and facts["model"] == "reported-model"
        assert facts["calls"] == 1 and facts["input_tokens"] == 10
        await decisions.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("answer", "advance", "changed", "reason"),
    [
        (_answer("attack", 0.4), 0.0, False, "low_confidence"),
        (_answer("attack"), 6.0, False, "stale_answer"),
        (_answer("attack"), 0.0, True, "stale_answer"),
        (DecisionError("timeout"), 0.0, False, "timeout"),
    ],
)
def test_coordinator_falls_back_for_low_confidence_stale_changed_and_failed_answers(
    answer: Answer | BaseException, advance: float, changed: bool, reason: str
) -> None:
    async def scenario() -> None:
        clock = _Clock()
        decisions = ArmyDecisions(
            _Provider([answer]),
            DecisionConfig(interval=20, timeout=10, max_wall_age=5, min_confidence=0.5),
            clock=clock,
        )
        obs = _observation()
        decisions.poll(obs, launched=True, first_wave=4, defense_radius=20)
        await _settle()
        clock.now = advance
        current = replace(obs, own_structures=(
            _entity(1000, "Nexus", START, structure=True),
            _entity(1001, "Pylon", (31, 35), structure=True),
        )) if changed else obs
        mode, facts = decisions.poll(
            current, launched=True, first_wave=4, defense_radius=20
        )
        assert mode is None and facts["source"] == "scripted_fallback"
        assert facts["reason"] == reason
        await decisions.close()

    asyncio.run(scenario())


def test_coordinator_timeout_and_close_cancel_pending_work() -> None:
    async def scenario() -> None:
        cancelled = asyncio.Event()

        class Hanging:
            async def decide(self, state: dict[str, Any], choices: tuple[str, ...]) -> Answer:
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()

        decisions = ArmyDecisions(Hanging(), DecisionConfig(timeout=0.01))
        decisions.poll(_observation(), launched=True, first_wave=4, defense_radius=20)
        await asyncio.sleep(0.03)
        _, facts = decisions.poll(
            _observation(), launched=True, first_wave=4, defense_radius=20
        )
        assert facts["reason"] == "request_failed" and cancelled.is_set()

        second = ArmyDecisions(Hanging(), DecisionConfig(timeout=10))
        second.poll(_observation(), launched=True, first_wave=4, defense_radius=20)
        await _settle()
        await second.close()
        assert second.task is None and second.disabled and cancelled.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mode", "enemies", "node", "ability"),
    [
        ("attack", (), "army.attack.go", "ATTACK"),
        (
            "defend",
            (_entity(7000, "Marine", (35, 35)),),
            "army.defend.attack",
            "ATTACK",
        ),
        ("regroup", (), "army.rally.move", "MOVE"),
    ],
)
def test_packaged_graph_routes_provider_mode_to_actual_command_node(
    mode: str, enemies: tuple[Entity, ...], node: str, ability: str
) -> None:
    runtime = JevRuntime(load_policy().policy, run_id=uuid.uuid4().hex)
    runtime.set_army_decision(mode, {"decision_provider": "typesafe", "choice": mode})
    result = runtime.tick(_observation(enemies=enemies))
    army_commands = [command for command in result.commands if command.node_id.startswith("army.")]
    assert len(army_commands) == 4
    assert {(command.node_id, command.ability) for command in army_commands} == {(node, ability)}


def test_switching_attack_to_regroup_cancels_old_task_before_its_retry() -> None:
    runtime = JevRuntime(load_policy().policy, run_id=uuid.uuid4().hex)
    obs = _observation(10)
    runtime.set_army_decision("attack", {"choice": "attack"})
    first = runtime.tick(obs)
    assert {c.node_id for c in first.commands} == {"army.attack.go"}
    attack_ids = {task.id for task in runtime.active_tasks() if task.node_id == "army.attack.go"}
    assert attack_ids

    runtime.set_army_decision("regroup", {"choice": "regroup"})
    switched = runtime.tick(replace(obs, game_loop=230, game_seconds=10.3))
    assert {c.node_id for c in switched.commands} == {"army.rally.move"}
    assert not attack_ids & {task.id for task in runtime.active_tasks()}

    later = runtime.tick(replace(obs, game_loop=260, game_seconds=11.3))
    assert all(command.node_id != "army.attack.go" for command in later.commands)


def test_controller_persists_reported_provider_evidence_for_reader(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = _Clock()
        decisions = ArmyDecisions(
            _Provider([_answer("regroup")]), DecisionConfig(interval=10), clock=clock
        )
        bundle = load_policy()
        run_id = uuid.uuid4().hex
        from jev.runner import _run_metadata

        options = MatchOptions()
        recorder = RunRecorder(
            tmp_path,
            _run_metadata(run_id, options, bundle),
            roots=bundle.policy.roots,
            clock=clock,
        )
        recorder.start(bundle.policy_bytes)
        controller = JevController(
            bundle,
            run_id=run_id,
            limits=MatchLimits(60, 60),
            clock=clock,
            recorder=recorder,
            decisions=decisions,
        )
        # Match the already-launched production state before dispatching the request;
        # otherwise the first scripted fallback legitimately changes its signature.
        controller.runtime.set_army_decision("attack", {})
        controller.runtime.tick(_observation(9))
        port = _Port()
        game = _game(units=[_unit(2000 + i, "Zealot") for i in range(4)], minerals=0)
        controller.attach(game, port)
        await controller.step()  # dispatch; scripted fallback is safe on the first tick
        await _settle()
        game.state.game_loop += 8
        await controller.step()  # accepts and reports the provider answer
        recorder.finish(controller.runtime, status="finished", result="win")
        metadata = read_run_metadata(recorder.run_dir)
        assert metadata is not None
        state = read_run_state(recorder.run_dir, metadata)
        decision_events = [e for e in state.recent_events if e.reason == "Army decision source"]
        assert decision_events
        facts = decision_events[-1].facts
        assert facts["source"] == "typesafe"
        assert facts["choice"] == "regroup"
        assert facts["model"] == "reported-model"

    asyncio.run(scenario())


def test_controller_forwards_runtime_defense_demotions_to_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DecisionSpy:
        demoted: frozenset[int] | None = None

        def poll(self, observation: Observation, **kwargs: Any) -> tuple[None, dict[str, Any]]:
            self.demoted = kwargs["demoted_defense"]
            return None, {
                "decision_provider": "typesafe",
                "source": "scripted_fallback",
                "choice": None,
                "reason": "test",
            }

        async def close(self) -> None:
            pass

    spy = DecisionSpy()
    controller = JevController(
        load_policy(),
        run_id=uuid.uuid4().hex,
        limits=MatchLimits(60, 60),
        decisions=spy,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        controller.runtime,
        "demoted_targets",
        lambda node_id: frozenset({7000}) if node_id == "army.defend.attack" else frozenset(),
    )
    game = _game(
        units=[_unit(2000 + i, "Zealot") for i in range(4)],
        enemy_units=[_unit(7000, "Marine", position=(35, 35))],
        minerals=0,
    )
    controller.attach(game, _Port())
    asyncio.run(controller.step())
    assert spy.demoted == frozenset({7000})


def test_missing_typesafe_key_fails_before_launcher_or_sc2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class NeverLaunch:
        called = False

        def prepare(self, options: MatchOptions) -> object:
            self.called = True
            raise AssertionError("SC2 must not be prepared")

        def play(self, controller: object, setup: object, options: MatchOptions) -> object:
            raise AssertionError("SC2 must not be played")

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    launcher = NeverLaunch()
    outcome = run_match(
        MatchOptions(decision_provider="typesafe"),
        load_policy(),
        run_root=tmp_path,
        launcher=launcher,
    )
    assert outcome.exit_code == EXIT_FAILURE and not launcher.called
    assert outcome.error is not None and outcome.error.code == "sc2_unavailable"
    assert "TYPESAFE_API_KEY is required" in outcome.message


def test_runner_main_wires_cli_typesafe_response_into_graph_and_disk_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = _body("regroup", model="service-selected-model")
        body["answers"]["army_mode"]["probabilities"] = {
            "attack": 0.2,
            "regroup": 0.8,
        }
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(respond)
    original_client = httpx.AsyncClient

    def mock_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)
    monkeypatch.setenv("TYPESAFE_API_KEY", "runner-secret")

    class Launcher:
        options: MatchOptions | None = None

        def __init__(self) -> None:
            self.army_nodes: list[str] = []

        def prepare(self, options: MatchOptions) -> object:
            self.options = options
            return "fake-sc2-setup"

        def play(
            self, controller: JevController, setup: object, options: MatchOptions
        ) -> str:
            assert setup == "fake-sc2-setup"

            async def drive() -> str:
                # Begin launched so the first fallback cannot change the request signature.
                controller.runtime.set_army_decision("attack", {})
                controller.runtime.tick(_observation(9))
                game = _game(
                    units=[_unit(2000 + i, "Zealot", position=START) for i in range(4)],
                    minerals=0,
                )
                controller.attach(game, _Port())
                first = await controller.step()
                if first is not None:
                    self.army_nodes.extend(
                        command.node_id
                        for command in first.commands
                        if command.node_id.startswith("army.")
                    )
                for _ in range(8):
                    await _settle()
                    game.state.game_loop += 8
                    result = await controller.step()
                    if result is not None:
                        self.army_nodes.extend(
                            command.node_id
                            for command in result.commands
                            if command.node_id.startswith("army.")
                        )
                    if "army.rally.move" in self.army_nodes:
                        break
                await JevBot(controller).on_end(Result.Victory)
                return "win"

            return asyncio.run(drive())

    launcher = Launcher()
    code = runner.main(
        [
            "--run-root",
            str(tmp_path),
            "--decision-provider",
            "typesafe",
            "--decision-model",
            "cli-requested-model",
            "--decision-max-requests",
            "1",
        ],
        load_policy=load_policy,
        prog="jev-test",
        launcher=launcher,
    )

    assert code == 0
    assert launcher.options is not None
    assert launcher.options.decision_model == "cli-requested-model"
    assert launcher.options.decision_max_requests == 1
    assert "army.rally.move" in launcher.army_nodes
    (request,) = requests
    assert request.headers["Authorization"] == "Bearer runner-secret"
    sent = json.loads(request.content)
    assert sent["model"] == "cli-requested-model"
    assert set(sent["questions"]["army_mode"]["criteria"]) == {"attack", "regroup"}

    (run_dir,) = [path for path in tmp_path.iterdir() if path.is_dir()]
    metadata = read_run_metadata(run_dir)
    assert metadata is not None
    state = read_run_state(run_dir, metadata)
    decision_events = [
        event for event in state.recent_events if event.reason == "Army decision source"
    ]
    assert decision_events
    facts = decision_events[-1].facts
    assert facts["source"] == "typesafe"
    assert facts["choice"] == "regroup"
    assert facts["requested_model"] == "cli-requested-model"
    assert facts["model"] == "service-selected-model"
    assert facts["max_requests"] == 1
    assert facts["answer_request_id"] == 1
    assert facts["options"] == ["attack", "regroup"]
    assert float(facts["response_age_game_seconds"]) > 0


# ---------------------------------------------------------------------------
# Jev v2 (Step 219): typed questions at the HTTP boundary
# ---------------------------------------------------------------------------

V2_POLICY = load_v2_policy().policy


def _choice_body(question_id: str, choice: str, options: list[str]) -> dict[str, Any]:
    probabilities = dict.fromkeys(options, 0.1 / max(1, len(options) - 1))
    probabilities[choice] = 0.9
    total = sum(probabilities.values())
    probabilities = {key: value / total for key, value in probabilities.items()}
    return {
        "answers": {
            question_id: {
                "type": "choice",
                "choice": choice,
                "confidence": probabilities[choice],
                "probabilities": probabilities,
            }
        },
        "model": "jev-observed",
        "usage": {"input_tokens": 11, "output_tokens": 3},
    }


def test_v1_army_request_body_keeps_its_frozen_shape() -> None:
    """The v1 question is still exactly the JI contract: one ``army_mode`` Choice
    question with v1's instructions and only the offered criteria."""
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_body())

    provider = TypesafeProvider("key", DecisionConfig(), transport=httpx.MockTransport(respond))
    asyncio.run(provider.decide({"game_seconds": 1}, ("attack", "defend", "regroup")))
    sent = json.loads(requests[0].content)
    assert list(sent) == ["model", "state", "questions"]
    assert sent["questions"] == {
        "army_mode": {"type": "choice", "instructions": QUESTION, "criteria": dict(CRITERIA)}
    }
    assert ARMY_QUESTION == Question("army_mode", QUESTION, CRITERIA)
    assert not uses_strategy(load_policy().policy)  # frozen v1 never gets strategy questions


def test_typesafe_provider_asks_the_strategy_question_by_its_own_key() -> None:
    requests: list[httpx.Request] = []
    offered = ("pressure", "expand")

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_choice_body("strategy", "expand", list(offered)))

    provider = TypesafeProvider("key", DecisionConfig(), transport=httpx.MockTransport(respond))
    answer = asyncio.run(provider.decide({"game_seconds": 1}, offered, STRATEGY_QUESTION))
    assert answer.choice == "expand" and answer.model == "jev-observed"
    question = json.loads(requests[0].content)["questions"]
    assert list(question) == ["strategy"]
    assert question["strategy"]["instructions"] == STRATEGY_QUESTION.instructions
    assert question["strategy"]["criteria"] == {c: STRATEGY_QUESTION.criteria[c] for c in offered}


def test_a_reply_keyed_to_another_question_is_invalid() -> None:
    """A crossed reply (an army answer to a strategy request) never parses as one."""

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_choice_body("army_mode", "attack", ["attack", "regroup"]))

    provider = TypesafeProvider("key", DecisionConfig(), transport=httpx.MockTransport(respond))
    with pytest.raises(DecisionError, match="^invalid_response$"):
        asyncio.run(provider.decide({}, ("pressure", "recover"), STRATEGY_QUESTION))


# ---------------------------------------------------------------------------
# Jev v2 (Step 219): the choice set is the graph's
# ---------------------------------------------------------------------------


def test_the_strategic_choices_offered_are_derived_from_the_v2_graph() -> None:
    branches = strategy_branches(V2_POLICY)
    assert tuple(b.choice for b in branches) == STRATEGY_CHOICES  # all four implemented
    for branch in branches:
        guard = V2_POLICY.node(branch.guard)
        assert (guard.operation, guard.args["choice"]) == ("strategy_is", branch.choice)
        if branch.slot == "macro":  # choosing it opens that lane's plan
            assert branch.start is not None
            start = V2_POLICY.node(branch.start)
            assert (start.operation, start.args["kind"]) == ("start_plan", branch.choice)
    assert set(STRATEGY_QUESTION.criteria) == set(STRATEGY_CHOICES)
    assert set(ARMY_V2_QUESTION.criteria) == set(CRITERIA)  # the graph's army modes


def test_a_choice_without_an_executable_branch_is_never_offered() -> None:
    """A macro guard whose lane cannot open that plan (here: the expansion lane's guard
    renamed to tech) leaves the choice out: nothing unimplemented is offered."""
    from jev.policy import parse_policy

    document = V2_POLICY.to_document()
    nodes = document["nodes"]
    assert isinstance(nodes, list)
    for node in nodes:
        assert isinstance(node, dict)
        if node["id"] == "expansion.start.chosen":
            node["args"] = {"choice": "tech"}
    mutated = parse_policy(document)
    choices = [b.choice for b in strategy_branches(mutated)]
    assert "expand" not in choices and choices == ["pressure", "recover", "tech"]
    provider = _QuestionProvider({"army_mode": [_army(0.1)], "strategy": [_strategic("tech")]})
    clock = _Clock()
    questions = HostedQuestions(provider, DecisionConfig(timeout=10), mutated, clock=clock)
    runtime = _FakeRuntime(reach={"expand", "tech"})  # both lanes' guards were reached
    _drive(questions, runtime, clock, seconds=12)
    offered = {c for q, options, _ in provider.calls if q == "strategy" for c in options}
    assert offered == {"pressure", "recover", "tech"}


# ---------------------------------------------------------------------------
# Jev v2 (Step 219): HostedQuestions at the DecisionProvider boundary
# ---------------------------------------------------------------------------


class _FakeRuntime:
    """The runtime facts HostedQuestions reads: the launch latch, which strategy guards
    the latest tick reached, the plan store's start refusals and plan records."""

    def __init__(
        self,
        *,
        launched: bool = True,
        reach: set[str] | None = None,
        refusals: dict[str, str] | None = None,
    ) -> None:
        self.latches = {"attack_launched": launched}
        self.reach = {"expand", "tech"} if reach is None else reach
        self.refusals = dict(refusals or {})
        self.records: dict[str, PlanRecord] = {}

    def demoted_targets(self, node_id: str) -> frozenset[int]:
        return frozenset()

    def strategy_reach(self) -> frozenset[str]:
        return frozenset(self.reach)

    def plan_start_refusal(self, kind: str) -> str | None:
        return self.refusals.get(kind)

    def plan(self, node_id: str) -> PlanRecord | None:
        return self.records.get(node_id)

    def plans(self) -> tuple[PlanRecord, ...]:
        return tuple(self.records.values())

    def plan_slots(self) -> tuple[Any, ...]:
        return ()


def _plan_record(kind: str, node: str, request_id: int | None) -> PlanRecord:
    return PlanRecord(
        plan_id=f"{'0' * 32}:plan:1",
        node_id=node,
        root_node_id=node.split(".")[0],
        kind=kind,  # type: ignore[arg-type]
        status="active",
        stage_node_id=node,
        site=None,
        actor_tag=None,
        started_game_seconds=12.0,
        last_progress_game_seconds=12.0,
        deadline_game_seconds=132.0,
        reserved_minerals=0,
        reserved_vespene=0,
        reason_code="started",
        decided_by="typesafe",
        decision_request_id=request_id,
    )


def _strategic(choice: str, confidence: float = 0.9) -> Answer:
    probs: dict[str, Any] = dict.fromkeys(STRATEGY_CHOICES, 0.02)
    probs[choice] = confidence
    return Answer(choice, confidence, probs, "reported-model", 20, 4)


def _army(confidence: float = 0.9, choice: str = "attack") -> Answer:
    return _answer(choice, confidence)


class _QuestionProvider:
    """Answers each question from its own script (the last entry repeats), records
    every request and how many ran at once; an asyncio.Event entry holds the reply
    until it is set, then the next entry answers."""

    def __init__(self, answers: dict[str, list[Any]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, tuple[str, ...], dict[str, Any]]] = []
        self.running = 0
        self.most_at_once = 0

    async def decide(
        self, state: dict[str, Any], choices: tuple[str, ...], question: Question = ARMY_QUESTION
    ) -> Answer:
        self.calls.append((question.question_id, choices, dict(state)))
        self.running += 1
        self.most_at_once = max(self.most_at_once, self.running)
        try:
            script = self.answers[question.question_id]
            result = script.pop(0) if len(script) > 1 else script[0]
            if isinstance(result, asyncio.Event):
                await result.wait()
                result = script.pop(0) if len(script) > 1 else script[0]
            if isinstance(result, BaseException):
                raise result
            assert isinstance(result, Answer)
            return result
        finally:
            self.running -= 1


def _v2_observation(seconds: float, *, zealots: int = 8, extra: int = 0) -> Observation:
    """The decision suite's base world with a larger army (``extra`` adds new tags)."""
    obs = _observation(seconds, zealots=0)
    army = tuple(_entity(2000 + i, "Zealot", START) for i in range(zealots + extra))
    return replace(obs, own_units=army)


def _drive(
    questions: HostedQuestions,
    runtime: _FakeRuntime,
    clock: _Clock,
    *,
    seconds: float,
    step: float = 0.5,
    observe: Any = None,
    before: Any = None,
    wall: Any = None,
) -> list[Any]:
    """Poll every ``step`` wall seconds (game time keeps pace; ``wall(i)`` may set the
    wall clock instead), letting the pending request run between polls; ``before(i)``
    may change the world first. Every poll's Decisions, in order; the questions are
    closed at the end."""

    async def scenario() -> list[Any]:
        polled = []
        for i in range(round(seconds / step) + 1):
            clock.now = i * step if wall is None else wall(i)
            if before is not None:
                before(i)
            obs = observe(i) if observe is not None else _v2_observation(10 + i * step)
            polled.append(questions.poll(obs, runtime))  # type: ignore[arg-type]
            for _ in range(5):
                await asyncio.sleep(0)
        await questions.close()
        return polled

    return asyncio.run(scenario())


def _dispatch_steps(polled: list[Any]) -> list[tuple[int, int]]:
    """(poll index, request id) of every dispatch: a new pending request id."""
    found: list[tuple[int, int]] = []
    previous = None
    for index, decided in enumerate(polled):
        pending = decided.metrics_facts["pending_request_id"]
        if pending is not None and pending != previous:
            found.append((index, int(pending)))
        previous = pending
    return found


def test_strategy_and_army_share_one_budget_with_global_request_ids() -> None:
    provider = _QuestionProvider({"army_mode": [_army()], "strategy": [_strategic("pressure")]})
    clock = _Clock()
    config = DecisionConfig(interval=2, timeout=10, max_requests=6)
    questions = HostedQuestions(provider, config, V2_POLICY, clock=clock)
    polled = _drive(questions, _FakeRuntime(), clock, seconds=60)
    asked = [question_id for question_id, _, _ in provider.calls]
    assert len(asked) == 6 and set(asked) == {"army_mode", "strategy"}  # one shared cap
    assert provider.most_at_once == 1  # one request in flight across both questions
    dispatched = _dispatch_steps(polled)
    assert [request_id for _, request_id in dispatched] == [1, 2, 3, 4, 5, 6]
    gaps = [(b - a) * 0.5 for (a, _), (b, _) in zip(dispatched, dispatched[1:], strict=False)]
    assert min(gaps) >= config.interval  # never two dispatches closer than the interval
    last = polled[-1]
    assert last.metrics_facts["calls"] == 6 and last.army_facts["calls"] == 6
    assert last.army_facts["reason"] == "request_limit"
    # The posture's hold ran out without renewals: the local fallback decides again.
    assert last.strategy_facts["reason"] == "request_limit"
    assert all(slot.local for slot in last.strategy.values())


def test_neither_question_starves_the_other() -> None:
    """The army question is dirty every poll (its army grows); the strategy question
    still gets a request within its interval plus the starvation bound, and the army
    is never kept waiting more than two dispatch intervals."""
    provider = _QuestionProvider({"army_mode": [_army()], "strategy": [_strategic("pressure")]})
    clock = _Clock()
    config = DecisionConfig(interval=2, timeout=10, max_requests=1000)
    questions = HostedQuestions(provider, config, V2_POLICY, clock=clock)
    _drive(
        questions,
        _FakeRuntime(),
        clock,
        seconds=90,
        observe=lambda i: _v2_observation(10 + i * 0.5, extra=i),
    )
    times: dict[str, list[float]] = {"army_mode": [], "strategy": []}
    for question_id, _, state in provider.calls:
        times[question_id].append(float(state["game_seconds"]) - 10)
    strategy, army = times["strategy"], times["army_mode"]
    assert len(strategy) >= 4 and len(army) >= 20
    bound = STRATEGY_INTERVAL + STARVATION_SECONDS + config.interval
    assert all(b - a <= bound for a, b in zip(strategy, strategy[1:], strict=False))
    assert all(b - a >= STRATEGY_INTERVAL for a, b in zip(strategy, strategy[1:], strict=False))
    assert all(b - a <= 2 * config.interval + 0.5 for a, b in zip(army, army[1:], strict=False))


def test_a_dirty_strategy_question_goes_before_an_unchanged_army_refresh() -> None:
    provider = _QuestionProvider({"army_mode": [_army()], "strategy": [_strategic("pressure")]})
    clock = _Clock()
    questions = HostedQuestions(provider, DecisionConfig(timeout=10), V2_POLICY, clock=clock)
    _drive(questions, _FakeRuntime(), clock, seconds=4.5)
    assert [q for q, _, _ in provider.calls][:2] == ["army_mode", "strategy"]


@pytest.mark.parametrize(
    ("reply", "change", "reason"),
    [
        (_strategic("expand", 0.4), None, "low_confidence"),
        (_strategic("expand"), "plan_opened", "stale_answer"),
        (_strategic("expand"), "late", "stale_answer"),
        (_strategic("attack"), None, "invalid_response"),
        (DecisionError("timeout"), None, "timeout"),
        (DecisionError("invalid_response"), None, "invalid_response"),
    ],
)
def test_a_strategy_answer_that_no_longer_holds_never_applies(
    reply: Any, change: str | None, reason: str
) -> None:
    """Low confidence, a changed plan identity (the tech plan opened while the reply
    was delayed), a late reply, an unoffered choice or a failure leaves both slots to
    the attributed local fallback: the answer never applies."""
    gate = asyncio.Event()
    provider = _QuestionProvider({"army_mode": [_army(0.1)], "strategy": [gate, reply]})
    clock = _Clock()
    questions = HostedQuestions(provider, DecisionConfig(timeout=30), V2_POLICY, clock=clock)
    runtime = _FakeRuntime()

    def before(i: int) -> None:
        if i != 10:
            return
        if change == "plan_opened":  # tech left the options: a new plan identity
            runtime.reach = {"expand"}
            runtime.records["tech.start.go"] = _plan_record("tech", "tech.start.go", None)
        gate.set()

    def wall(i: int) -> float:  # "late": the reply is read six wall seconds on
        return i * 0.5 + (6.0 if change == "late" and i > 10 else 0.0)

    polled = _drive(questions, runtime, clock, seconds=10, before=before, wall=wall)
    assert [q for q, _, _ in provider.calls][:2] == ["army_mode", "strategy"]
    assert polled[-1].strategy_facts["reason"] == reason
    for decided in polled:
        assert decided.strategy_facts["choice"] is None  # never applied
        assert all(slot.choice is None for slot in decided.strategy.values())
    final = polled[-1]
    assert final.strategy["macro"].local and final.strategy["army"].local
    assert final.strategy_facts["source"] == "local_fallback"
    assert final.strategy["macro"].source == "local_fallback"


def test_an_accepted_macro_choice_holds_its_slot_until_the_plan_it_starts() -> None:
    provider = _QuestionProvider({"army_mode": [_army(0.1)], "strategy": [_strategic("tech")]})
    clock = _Clock()
    questions = HostedQuestions(provider, DecisionConfig(timeout=10), V2_POLICY, clock=clock)
    runtime = _FakeRuntime()
    polled: list[Any] = []

    def before(i: int) -> None:
        if not polled:
            return
        macro = polled[-1].strategy["macro"]
        if macro.choice == "tech" and "tech.start.go" not in runtime.records:
            # The tech lane's guard passed on that tick and opened the plan.
            record = _plan_record("tech", "tech.start.go", macro.request_id)
            runtime.records["tech.start.go"] = record
            runtime.reach = {"expand"}
            runtime.refusals = {"expand": "the macro slot already holds open plan"}

    def observe(i: int) -> Observation:
        return _v2_observation(10 + i * 0.5)

    original = questions.poll

    def poll(obs: Observation, rt: Any) -> Any:
        decided = original(obs, rt)
        polled.append(decided)
        return decided

    questions.poll = poll  # type: ignore[method-assign]
    _drive(questions, runtime, clock, seconds=8, before=before, observe=observe)
    held = [d for d in polled if d.strategy["macro"].choice == "tech"]
    assert held and held[0].strategy["macro"].request_id == 2
    assert held[0].strategy["macro"].source == "typesafe" and held[0].strategy["army"].local
    after = polled[polled.index(held[-1]) + 1]
    assert after.strategy["macro"].choice is None and not after.strategy["macro"].local
    started = after.strategy_facts["started_plan"]
    assert started["request_id"] == 2 and started["plan_kind"] == "tech"
    assert after.strategy_facts["source"] == "awaiting_answer"  # the next answer decides


def test_an_applied_posture_outlives_its_freshness_until_its_hold_expires() -> None:
    provider = _QuestionProvider({"army_mode": [_army(0.1)], "strategy": [_strategic("recover")]})
    clock = _Clock()
    config = DecisionConfig(timeout=10, max_requests=2)  # no renewal after the answer
    questions = HostedQuestions(provider, config, V2_POLICY, clock=clock)
    polled = _drive(questions, _FakeRuntime(), clock, seconds=50)
    holding = [n for n, d in enumerate(polled) if d.strategy["army"].choice == "recover"]
    assert holding and holding == list(range(holding[0], holding[-1] + 1))
    ages = [float(polled[n].strategy_facts["response_age_game_seconds"]) for n in holding]
    assert max(ages) > 8  # it outlived the answer's eight-second freshness
    for n in holding:  # a posture also keeps the macro slot from opening a plan
        macro = polled[n].strategy["macro"]
        assert not macro.local and macro.choice is None
    expiry = polled[holding[-1] + 1]
    held_for = (len(holding) - 1) * 0.5  # game seconds between the first and last poll
    assert POSTURE_HOLD_SECONDS - 0.5 <= held_for <= POSTURE_HOLD_SECONDS
    assert expiry.strategy["army"].local and expiry.strategy["macro"].local
    assert expiry.strategy_facts["reason"] == "request_limit"  # nothing left to renew it


def test_no_strategy_question_before_the_first_launch() -> None:
    provider = _QuestionProvider({"army_mode": [_army(0.1)], "strategy": [_strategic("pressure")]})
    clock = _Clock()
    questions = HostedQuestions(provider, DecisionConfig(timeout=10), V2_POLICY, clock=clock)
    polled = _drive(questions, _FakeRuntime(launched=False), clock, seconds=30)
    assert {q for q, _, _ in provider.calls} == {"army_mode"}
    assert all(d.strategy["macro"].local for d in polled)  # nothing to wait for


@pytest.mark.parametrize(
    ("reach", "refusals", "offered"),
    [
        (set(), {}, ("pressure", "recover")),
        ({"tech"}, {"tech": "the macro slot cools down"}, ("pressure", "recover")),
        ({"tech"}, {}, ("pressure", "recover", "tech")),
        ({"expand", "tech"}, {}, ("pressure", "recover", "expand", "tech")),
    ],
)
def test_only_currently_executable_choices_are_offered(
    reach: set[str], refusals: dict[str, str], offered: tuple[str, ...]
) -> None:
    provider = _QuestionProvider({"army_mode": [_army(0.1)], "strategy": [_strategic("pressure")]})
    clock = _Clock()
    questions = HostedQuestions(provider, DecisionConfig(timeout=10), V2_POLICY, clock=clock)
    _drive(questions, _FakeRuntime(reach=reach, refusals=refusals), clock, seconds=5)
    (strategy,) = [options for q, options, _ in provider.calls if q == "strategy"]
    assert strategy == offered


def test_an_authentication_failure_stops_every_question_for_the_match() -> None:
    provider = _QuestionProvider(
        {"army_mode": [DecisionError("http_401")], "strategy": [_strategic("pressure")]}
    )
    clock = _Clock()
    questions = HostedQuestions(provider, DecisionConfig(timeout=10), V2_POLICY, clock=clock)
    polled = _drive(questions, _FakeRuntime(), clock, seconds=30)
    assert [q for q, _, _ in provider.calls] == ["army_mode"]
    assert questions.disabled
    assert polled[-1].army_facts["reason"] == "http_401"
    assert all(slot.local for slot in polled[-1].strategy.values())


def test_close_cancels_the_pending_request_and_stops_both_questions() -> None:
    cancelled = asyncio.Event()

    class Hanging:
        calls = 0

        async def decide(
            self, state: Any, choices: Any, question: Question = ARMY_QUESTION
        ) -> Answer:
            self.calls += 1
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
            raise AssertionError("unreachable")

    async def scenario() -> None:
        provider = Hanging()
        questions = HostedQuestions(provider, DecisionConfig(timeout=10), V2_POLICY)
        questions.poll(_v2_observation(10), _FakeRuntime())  # type: ignore[arg-type]
        await asyncio.sleep(0)
        assert questions.task is not None and provider.calls == 1
        await questions.close()
        assert cancelled.is_set() and questions.task is None and questions.disabled
        questions.poll(_v2_observation(30), _FakeRuntime())  # type: ignore[arg-type]
        assert questions.task is None and provider.calls == 1

    asyncio.run(scenario())


def test_v2_army_question_counts_stalkers_and_defends_both_bases() -> None:
    stalker = Entity(
        2100, "Stalker", START, 80, ready=True, can_attack_ground=True, can_attack_air=True
    )
    zealot = Entity(2000, "Zealot", START, 100, ready=True, can_attack_ground=True)
    nexus = _entity(1000, "Nexus", START, structure=True)
    natural = _entity(1001, "Nexus", (60.5, 30.5), structure=True)
    raider = _entity(7000, "Marine", (62, 32))  # at the natural, far from the main
    flyer = _entity(7001, "Banshee", (32, 32), flying=True)
    base = replace(_observation(), own_units=(zealot, stalker), own_structures=(nexus, natural))

    def options(obs: Observation, demoted: frozenset[int] = frozenset()) -> tuple[str, ...]:
        found = army_context(
            obs,
            launched=True,
            first_wave=4,
            defense_radius=20,
            natural_radius=15,
            base_target=2,
            demoted=demoted,
        )
        return found.options

    quiet = army_context(
        base, launched=True, first_wave=4, defense_radius=20, natural_radius=15, base_target=2
    )
    assert quiet.options == ("attack", "regroup")
    assert quiet.state["ready_combat_units"] == 2
    assert quiet.state["ready_by_type"] == {"Stalker": 1, "Zealot": 1}
    assert "defend" in options(replace(base, visible_enemies=(raider,)))  # the natural
    assert "defend" not in options(replace(base, visible_enemies=(raider,)), frozenset({7000}))
    assert "defend" in options(replace(base, visible_enemies=(flyer,)))  # a Stalker hits air
    zealots_only = replace(base, own_units=(zealot,), visible_enemies=(flyer,))
    assert "defend" not in options(zealots_only)  # a Zealot is never sent at air
    # v1's frozen army context still counts Zealots only, with its own keys.
    state, _, _ = context(replace(base, visible_enemies=(raider,)), True, 4, 20)
    assert state["ready_zealots"] == 1 and "ready_combat_units" not in state


def test_a_timed_out_request_is_cancelled_and_its_late_reply_never_applies() -> None:
    """The strategy request outlives its deadline: it is cancelled, the strategy falls
    back, and the next request (the army's) is the only one that can apply -- the
    held strategy reply, released afterwards, never arrives anywhere."""
    gate = asyncio.Event()
    cancelled: list[str] = []

    class Slow:
        calls: list[str] = []

        async def decide(
            self, state: Any, choices: tuple[str, ...], question: Question = ARMY_QUESTION
        ) -> Answer:
            self.calls.append(question.question_id)
            if question.question_id == "army_mode":
                return _army()
            try:
                await gate.wait()
            except asyncio.CancelledError:
                cancelled.append(question.question_id)
                raise
            return _strategic("expand")

    async def scenario() -> list[Any]:
        clock = _Clock()
        provider = Slow()
        config = DecisionConfig(interval=2, timeout=0.05)
        questions = HostedQuestions(provider, config, V2_POLICY, clock=clock)
        runtime = _FakeRuntime()
        polled = []
        for i in range(30):
            clock.now = i * 0.5
            polled.append(questions.poll(_v2_observation(10 + i * 0.5), runtime))  # type: ignore[arg-type]
            if questions.pending_question == "strategy" and not cancelled:
                await asyncio.sleep(0.1)  # past the deadline: the request is cancelled
                gate.set()  # the reply is "ready" only now, too late
            for _ in range(5):
                await asyncio.sleep(0)
        await questions.close()
        assert provider.calls[:3] == ["army_mode", "strategy", "army_mode"]
        return polled

    polled = asyncio.run(scenario())
    assert cancelled == ["strategy"]
    failed = [d for d in polled if d.strategy_facts["reason"] == "request_failed"]
    assert failed and all(d.strategy_facts["choice"] is None for d in polled)
    assert all(slot.choice is None for d in polled for slot in d.strategy.values())
    # The army's later request is still its own: it applies to the army only.
    assert any(d.army_mode == "attack" for d in polled[polled.index(failed[0]) :])


def test_the_strategy_coordinator_reuses_the_single_sourced_reason_codes() -> None:
    """Step 212 single-sourced the coordinator reason codes in jev.decision; the v2
    coordinator imports them (the same objects) and never spells one as a literal."""
    from pathlib import Path as _Path

    import jev.decision as decision_module
    import jev.strategy as strategy_module
    from test_jev_benchmark import _code_literals

    assert strategy_module.AUTH_FAILURE_REASONS is decision_module.AUTH_FAILURE_REASONS
    for name in ("ACCEPTED_REASON", "LOW_CONFIDENCE_REASON", "STALE_ANSWER_REASON"):
        assert getattr(strategy_module, name) is getattr(decision_module, name)
    source = _Path(strategy_module.__file__)
    assert _code_literals(source) == []


# ---------------------------------------------------------------------------
# Step 219 iteration 2: the wait for the hosted answer is bounded under churn
# ---------------------------------------------------------------------------


def test_a_churning_strategy_state_is_still_asked_within_the_starvation_bound() -> None:
    """The strategy state flickers every poll (a macro guard's reach toggles, as a
    threat at home does), so it never settles; its wait still accrues and the question
    is asked once it has waited STARVATION_SECONDS. The reply (made on the other
    state) is stale, and the macro slot falls back -- diagnosed -- instead of waiting."""
    provider = _QuestionProvider({"army_mode": [_army(0.1)], "strategy": [_strategic("tech")]})
    clock = _Clock()
    config = DecisionConfig(interval=2, timeout=10)
    questions = HostedQuestions(provider, config, V2_POLICY, clock=clock)
    runtime = _FakeRuntime()

    def flicker(i: int) -> None:
        runtime.reach = {"expand", "tech"} if i % 2 == 0 else set()

    polled = _drive(questions, runtime, clock, seconds=30, before=flicker)
    asked = [n for n, d in enumerate(polled) if d.strategy_facts["pending"]]
    assert asked, "a churning strategy question must still be asked"
    first = asked[0] * 0.5
    assert first <= STARVATION_SECONDS + config.interval + 0.5
    waits = [n for n, d in enumerate(polled) if d.strategy_facts["source"] == "awaiting_answer"]
    fell_back = next(n for n in range(waits[0], len(polled)) if polled[n].strategy["macro"].local)
    assert (fell_back - waits[0]) * 0.5 <= answer_wait_seconds(config)
    assert polled[fell_back].strategy_facts["reason"] == "stale_answer"
    assert polled[fell_back].strategy_facts["source"] == "local_fallback"


def test_the_wait_for_the_hosted_answer_is_bounded_when_no_request_can_go_out() -> None:
    """A provider that ignores cancellation keeps the army's request in flight past its
    deadline, so no strategy request can ever be dispatched: the macro slot waits
    answer_wait_seconds() on the coordinator's clock at most, then the local fallback
    decides it, with its own visible reason (the backstop; a due question is otherwise
    always dispatched first)."""
    gate = asyncio.Event()
    calls: list[str] = []

    class Stuck:
        async def decide(
            self, state: Any, choices: tuple[str, ...], question: Question = ARMY_QUESTION
        ) -> Answer:
            calls.append(question.question_id)
            while not gate.is_set():
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    continue  # a broken provider: the deadline cannot end it
            return _army(0.1)

    clock = _Clock()
    config = DecisionConfig()
    questions = HostedQuestions(Stuck(), config, V2_POLICY, clock=clock)
    bound = answer_wait_seconds(config)
    polls = round((bound + 10) / 0.5)

    def release(i: int) -> None:
        if i == polls:
            gate.set()  # let close() finish the stuck request

    polled = _drive(questions, _FakeRuntime(), clock, seconds=polls * 0.5, before=release)
    assert calls == ["army_mode"]  # nothing else could go out
    waiting = [n for n, d in enumerate(polled) if d.strategy_facts["source"] == "awaiting_answer"]
    assert waiting and waiting == list(range(waiting[0], waiting[-1] + 1))
    expiry = polled[waiting[-1] + 1]
    held = (waiting[-1] + 1 - waiting[0]) * 0.5  # wall seconds the slot waited
    assert bound < held <= bound + 0.5
    assert expiry.strategy["macro"].local and expiry.strategy["macro"].source == "local_fallback"
    assert expiry.strategy_facts["source"] == "local_fallback"
    assert expiry.strategy_facts["reason"] == ANSWER_WAIT_EXPIRED_REASON
    assert all(d.strategy["macro"].local for d in polled[waiting[-1] + 1 :])


@pytest.mark.parametrize("game_per_wall", [10.0, 1.0, 0.25])
def test_a_due_strategy_question_is_dispatched_before_its_wait_expires_at_any_game_speed(
    game_per_wall: float,
) -> None:
    """The answer-wait bound and the cadence share one clock: whether the game runs ten
    times faster than the wall clock (no --realtime), at real time, or slower (a
    lagging client), a churning strategy question (unsettled every poll) is dispatched
    by its starvation promotion before the macro slot could fall back on
    answer_wait_expired."""
    provider = _QuestionProvider({"army_mode": [_army(0.1)], "strategy": [_strategic("pressure")]})
    clock = _Clock()
    config = DecisionConfig()
    questions = HostedQuestions(provider, config, V2_POLICY, clock=clock)
    runtime = _FakeRuntime()
    wall_step = 0.5 / game_per_wall  # each poll is 0.5 game seconds

    def flicker(i: int) -> None:
        runtime.reach = {"expand", "tech"} if i % 2 == 0 else set()

    polls = round((answer_wait_seconds(config) + 5) / wall_step)
    polled = _drive(
        questions,
        runtime,
        clock,
        seconds=polls * 0.5,
        before=flicker,
        wall=lambda i: i * wall_step,
    )
    expired = [
        n for n, d in enumerate(polled) if d.strategy_facts["reason"] == ANSWER_WAIT_EXPIRED_REASON
    ]
    asked = [n for n, d in enumerate(polled) if d.strategy_facts["pending"]]
    assert asked, "the due strategy question must be dispatched"
    assert not expired or asked[0] < expired[0]
    assert expired == []  # it was asked in time, so the backstop never fired
    assert asked[0] * wall_step <= STARVATION_SECONDS + config.interval + wall_step
    assert polled[asked[0] + 1].strategy_facts["reason"] == "stale_answer"  # churned: refused


def test_a_question_whose_options_dip_below_two_is_still_asked() -> None:
    """A policy with one army posture (here both army guards say pressure) offers two
    choices only while tech is executable; tech coming and going every poll makes the
    question unaskable on every other poll. Its wait keeps accruing across the dips, so
    it is still promoted and asked (with two options) after STARVATION_SECONDS."""
    from jev.policy import parse_policy

    document = V2_POLICY.to_document()
    nodes = document["nodes"]
    assert isinstance(nodes, list)
    for node in nodes:
        assert isinstance(node, dict)
        if node["id"] == "army.recover.chosen":
            node["args"] = {"choice": "pressure"}
    single_posture = parse_policy(document)
    assert [b.choice for b in strategy_branches(single_posture)] == ["pressure", "expand", "tech"]
    provider = _QuestionProvider({"army_mode": [_army(0.1)], "strategy": [_strategic("tech")]})
    clock = _Clock()
    config = DecisionConfig()
    questions = HostedQuestions(provider, config, single_posture, clock=clock)
    runtime = _FakeRuntime()

    def dip(i: int) -> None:
        runtime.reach = {"tech"} if i % 2 == 0 else set()

    polled = _drive(questions, runtime, clock, seconds=30, before=dip)
    options = [d.strategy_facts["available_options"] for d in polled]
    assert ["pressure"] in options and ["pressure", "tech"] in options  # it dips every poll
    asked = [(q, choices) for q, choices, _ in provider.calls if q == "strategy"]
    assert asked and asked[0][1] == ("pressure", "tech")  # asked only with two options
    first = next(n for n, d in enumerate(polled) if d.strategy_facts["pending"])
    assert first * 0.5 <= STARVATION_SECONDS + config.interval + 0.5
