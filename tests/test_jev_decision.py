"""Typesafe decision provider, coordinator, and production graph integration."""

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
from sc2.data import Result

from jev import runner
from jev.bot import JevBot, JevController
from jev.contracts import Entity, Observation
from jev.decision import (
    Answer,
    ArmyDecisions,
    DecisionConfig,
    DecisionError,
    TypesafeProvider,
    context,
    parse_answer,
)
from jev.runner import EXIT_FAILURE, MatchLimits, MatchOptions, run_match
from jev.runtime import JevRuntime
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
