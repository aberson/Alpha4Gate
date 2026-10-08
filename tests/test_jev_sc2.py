"""Jev's SC2 boundary: adapter, burnysc2 port, bot shim and single-match runner (Step 202).

The adapter tests feed duck-typed ``BotAI`` stand-ins (only the attributes
burnysc2 exposes) through the production :class:`jev.sc2_adapter.Sc2Adapter`
and a v1-policy :class:`jev.runtime.JevRuntime`. The port tests send real
burnysc2 ``UnitCommand`` objects built from protobuf ``Unit`` records. Runner
tests never launch SC2: they inject a launcher or point ``SC2PATH`` at an empty
folder. The one live match is marked ``sc2`` (deselected by default) for the
later real smoke on the SC2 host.
"""

from __future__ import annotations

import asyncio
import math
import os
import signal
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bots.jev.v1 import load_policy
from s2clientprotocol import raw_pb2
from sc2.data import ActionResult, Result
from sc2.game_state import ActionError
from sc2.ids.ability_id import AbilityId
from sc2.position import Point2
from sc2.unit import Unit

import jev.bot as bot_module
from jev import runner
from jev.bot import JevBot, JevController
from jev.contracts import MAX_ENTITY_ORDERS, MAX_OBSERVED_ENTITIES, CommandSpec, Order
from jev.operations import BUILD_ABILITY, COMMAND_ABILITIES, MAX_PLACEMENT_CANDIDATES
from jev.runner import (
    EXIT_FAILURE,
    EXIT_OK,
    EXIT_STOPPED,
    EXIT_TIMEOUT,
    EXIT_USAGE,
    MatchOptions,
    Sc2Launcher,
    Sc2Unavailable,
    run_match,
)
from jev.runtime import JevRuntime
from jev.sc2_adapter import GAME_LOOPS_PER_SECOND, BotAIPort, Sc2Adapter

START = (30.5, 30.5)
PATCH = 3000

# ---------------------------------------------------------------------------
# Duck-typed BotAI stand-ins
# ---------------------------------------------------------------------------


def _unit(tag: int, name: str = "Probe", **fields: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "tag": tag,
        "name": name,
        "position": (28.0, 26.0),
        "health": 40.0,
        "build_progress": 1.0,
        "orders": [],
        "is_structure": False,
        "is_flying": False,
        "is_ready": True,
        "is_idle": True,
        "is_powered": False,
        "is_visible": True,
    }
    values.update(fields)
    return SimpleNamespace(**values)


def _order(ability: str, target: object = 0, progress: float = 0.0) -> SimpleNamespace:
    """burnysc2 UnitOrder shape: ``ability.id.name`` is the generic AbilityId name."""
    return SimpleNamespace(
        ability=SimpleNamespace(id=SimpleNamespace(name=ability)), target=target, progress=progress
    )


def _nexus(**fields: Any) -> SimpleNamespace:
    return _unit(1000, "Nexus", position=START, is_structure=True, **fields)


def _patches() -> list[SimpleNamespace]:
    return [_unit(PATCH + i, "MineralField", position=(26.5 + i, 22.5)) for i in range(8)]


def _mining(tag: int) -> SimpleNamespace:
    return _unit(tag, orders=[_order("HARVEST_GATHER", PATCH)], is_idle=False)


def _game(**fields: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "state": SimpleNamespace(game_loop=224),
        "minerals": 50,
        "supply_used": 12,
        "supply_cap": 15,
        "units": [_mining(2000 + i) for i in range(12)],
        "structures": [_nexus()],
        "enemy_units": [],
        "enemy_structures": [],
        "mineral_field": _patches(),
        "start_location": START,
        "enemy_start_locations": [(120.5, 120.5)],
        "expansion_locations_list": [(120.5, 120.5), START],
        "game_info": SimpleNamespace(map_center=(75.5, 75.5)),
    }
    values.update(fields)
    return SimpleNamespace(**values)


def _idle_game(*tags: int) -> SimpleNamespace:
    """Idle probes and no minerals: the v1 policy's only commands are gathers."""
    return _game(units=[_unit(tag) for tag in tags], minerals=0)


class _Port:
    """Records what the adapter asks of SC2; answers with configured verdicts."""

    def __init__(
        self,
        *,
        visible: Callable[[tuple[float, float]], bool] = lambda point: False,
        legal: Callable[[int], bool] = lambda index: True,
        refusal: object = None,
    ) -> None:
        self.visible = visible
        self.legal = legal
        self.refusal = refusal
        self.errors: list[object] = []
        self.queries: list[tuple[str, tuple[tuple[float, float], ...]]] = []
        self.issued: list[tuple[str, object, object]] = []
        self.left = False

    def is_visible(self, point: tuple[float, float]) -> bool:
        return self.visible(point)

    async def placement_legal(self, ability: str, sites: Any) -> list[bool]:
        self.queries.append((ability, tuple(sites)))
        return [self.legal(index) for index in range(len(sites))]

    async def issue(self, ability: str, actor: object, target: object) -> object:
        self.issued.append((ability, actor, target))
        return self.refusal

    def action_errors(self) -> list[object]:
        return list(self.errors)

    async def leave(self) -> None:
        self.left = True


def _runtime() -> JevRuntime:
    return JevRuntime(load_policy().policy, run_id=uuid.uuid4().hex)


def _ticked(game: SimpleNamespace, port: _Port) -> tuple[Sc2Adapter, JevRuntime, list[CommandSpec]]:
    adapter, runtime = Sc2Adapter(), _runtime()
    result = runtime.tick(adapter.observe(game, port))
    return adapter, runtime, list(result.commands)


def _pylon_command(commands: list[CommandSpec]) -> CommandSpec:
    (command,) = [c for c in commands if c.ability == BUILD_ABILITY["Pylon"]]
    return command


# ---------------------------------------------------------------------------
# Observation: visible state only
# ---------------------------------------------------------------------------


def test_observation_maps_visible_state_and_production_flags() -> None:
    probe = _unit(2000, orders=[_order("HARVEST_GATHER", PATCH, 0.5)], is_idle=False)
    training_nexus = _nexus(orders=[_order("NEXUSTRAIN_PROBE", 0, 0.25)], is_idle=False)
    warping_gateway = _unit(
        1100,
        "Gateway",
        position=(26.5, 34.5),
        is_structure=True,
        build_progress=0.4,
        is_ready=False,
        is_powered=False,
    )
    pylon = _unit(1200, "Pylon", position=(24.0, 34.0), is_structure=True)
    fogged_patch = _unit(PATCH + 9, "MineralField", is_visible=False)
    marine = _unit(7000, "Marine", position=(40.0, 40.0), orders=[_order("ATTACK", 2000)])
    snapshot = _unit(7100, "CommandCenter", is_structure=True, is_visible=False)
    game = _game(
        units=[probe],
        structures=[training_nexus, warping_gateway, pylon],
        mineral_field=[*_patches(), fogged_patch],
        enemy_units=[marine],
        enemy_structures=[snapshot],
    )
    obs = Sc2Adapter().observe(game, _Port())
    assert (obs.game_loop, obs.game_seconds) == (224, 10.0)  # 22.4 loops per game second
    assert (obs.minerals, obs.supply_used, obs.supply_cap) == (50, 12, 15)
    (own_probe,) = obs.own_units
    assert own_probe.orders == (Order("HARVEST_GATHER", PATCH, 0.5),)
    nexus, gateway, own_pylon = obs.own_structures
    assert nexus.orders == (Order("NEXUSTRAIN_PROBE", None, 0.25),)  # tag 0 means no target
    assert (nexus.ready, nexus.idle, nexus.powered) == (True, False, True)  # needs no power
    assert (gateway.ready, gateway.idle, gateway.powered) == (False, True, False)
    assert own_pylon.powered is True
    assert [e.tag for e in obs.mineral_fields] == [PATCH + i for i in range(8)]
    (enemy,) = obs.visible_enemies
    assert enemy.tag == 7000 and enemy.orders == ()  # enemy orders are never read
    assert obs.remembered_enemy_structures == ()  # a snapshot never seen is not memory
    assert obs.start_location == START and obs.map_center == (75.5, 75.5)
    assert obs.expansion_locations == (START, (120.5, 120.5))


def test_remembered_enemy_structures_are_revalidated_on_sight() -> None:
    adapter, port = Sc2Adapter(), _Port()
    bunker = _unit(7200, "Bunker", position=(60.0, 60.0), is_structure=True, health=300.0)
    seen = adapter.observe(_game(enemy_structures=[bunker]), port)
    assert [e.tag for e in seen.visible_enemies] == [7200] and not seen.remembered_enemy_structures
    fogged = adapter.observe(_game(state=SimpleNamespace(game_loop=448)), port)
    assert [(e.tag, e.health) for e in fogged.remembered_enemy_structures] == [(7200, 300.0)]
    port.visible = lambda point: point == (60.0, 60.0)  # vision returns; it is gone
    gone = adapter.observe(_game(state=SimpleNamespace(game_loop=672)), port)
    assert gone.remembered_enemy_structures == ()


def test_only_the_first_queued_orders_up_to_the_cap_are_read() -> None:
    queue = [_order("MOVE", (40.0 + i, 40.0)) for i in range(MAX_ENTITY_ORDERS + 6)]
    obs = Sc2Adapter().observe(_game(units=[_unit(2000, orders=queue, is_idle=False)]), _Port())
    (probe,) = obs.own_units
    assert len(probe.orders) == MAX_ENTITY_ORDERS
    assert probe.orders[-1].target == (40.0 + MAX_ENTITY_ORDERS - 1, 40.0)


def test_failed_observation_leaves_adapter_memory_untouched() -> None:
    adapter, port = Sc2Adapter(), _Port()
    bunker = _unit(7200, "Bunker", position=(60.0, 60.0), is_structure=True)
    adapter.observe(_game(enemy_structures=[bunker]), port)
    with pytest.raises(ValueError):
        adapter.observe(_game(minerals=-1), port)
    later = adapter.observe(_game(state=SimpleNamespace(game_loop=448)), port)
    assert [e.tag for e in later.remembered_enemy_structures] == [7200]


class _Exploding:
    """An attribute read that fails with an unrelated exception type."""

    @property
    def tag(self) -> int:
        raise KeyError("no tag here")


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"units": [_unit(2000, position=(math.nan, 1.0))]}, "units[0].position.x"),
        ({"units": [_unit(True)]}, "units[0].tag"),
        ({"units": [_unit(2**64)]}, "units[0].tag"),
        ({"units": [_unit(2000, name="Probe\x1b[2J")]}, "units[0].name"),
        ({"units": [_unit(2000, health="full")]}, "units[0].health"),
        ({"units": [_unit(2000, orders=[_order("HARVEST\nGATHER")])]}, "orders[0].ability"),
        ({"units": [_unit(2000, orders=[_order("MOVE", (1.0,))])]}, "orders[0].target"),
        ({"units": [_unit(2000), _unit(2000)]}, "repeats own tag"),
        ({"units": 12}, "units must be a collection"),
        ({"units": {"2000": _unit(2000)}}, "units must be a collection"),
        ({"enemy_units": [_unit(7000, is_visible=1)]}, "enemy_units[0].is_visible"),
        ({"structures": [_nexus(is_ready=None)]}, "structures[0].is_ready"),
        ({"minerals": 10.5}, "minerals"),
        ({"supply_cap": 1e300}, "supply_cap"),
        ({"state": SimpleNamespace(game_loop=-8)}, "state.game_loop"),
        ({"start_location": (1.0, 2e6)}, "start_location.y"),
        ({"units": [_unit(2000 + i) for i in range(MAX_OBSERVED_ENTITIES + 1)]}, "more than"),
        ({"units": [_Exploding()]}, "unreadable game state ('KeyError'"),
        ({"game_info": None}, "unreadable game state ('AttributeError'"),
    ],
)
def test_malformed_game_state_raises_value_error_naming_the_field(
    change: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(ValueError) as excinfo:
        Sc2Adapter().observe(_game(**change), _Port())
    assert fragment in str(excinfo.value)
    assert "\x1b" not in str(excinfo.value) and "\n" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Commands: attribution, ownership, legality, acknowledgement hooks
# ---------------------------------------------------------------------------


def test_accepted_gather_sends_the_observed_objects_and_awaits_observation() -> None:
    game = _idle_game(2000)
    (idle,) = game.units
    port = _Port()
    adapter, runtime, commands = _ticked(game, port)
    (gather,) = commands
    (record,) = asyncio.run(adapter.issue(commands, port, runtime))
    ((ability, actor, target),) = port.issued
    assert (ability, actor) == ("HARVEST_GATHER", idle)
    assert target is game.mineral_field[[p.tag for p in game.mineral_field].index(gather.target)]
    assert (record.outcome, record.node_id, record.task_id) == (
        "accepted",
        "economy.assign",
        gather.task_id,
    )
    assert runtime.task_status(gather.task_id) == "issued"  # acceptance is not success


def test_build_uses_the_first_legal_candidate_and_repoints_the_task() -> None:
    port = _Port(legal=lambda index: index >= 2)
    adapter, runtime, commands = _ticked(_game(minerals=100), port)
    build = _pylon_command(commands)
    asyncio.run(adapter.issue(commands, port, runtime))
    candidates = (build.target, *build.alternatives)
    ((_, queried),) = port.queries
    assert queried == candidates
    ((ability, actor, target),) = [i for i in port.issued if i[0] == build.ability]
    assert target == candidates[2]
    (task,) = [t for t in runtime.active_tasks() if t.id == build.task_id]
    assert task.target == candidates[2]
    tick = runtime.tick(
        adapter.observe(_game(minerals=100, state=SimpleNamespace(game_loop=232)), port)
    )
    accepted = [e for e in tick.events if e.task_id == build.task_id and e.kind == "task"]
    assert accepted[0].facts["placement"] == [candidates[2][0], candidates[2][1]]
    assert accepted[0].facts["actor_tag"] == str(build.actor_tags[0])  # D3: site and probe traced


def test_no_legal_candidate_rejects_without_sending_and_caps_the_query_at_eight() -> None:
    port = _Port(legal=lambda index: False)
    adapter, runtime, commands = _ticked(_game(minerals=100), port)
    build = _pylon_command(commands)
    extra = tuple((40.0 + i, 40.0) for i in range(10))
    padded = CommandSpec(
        build.node_id, build.task_id, build.ability, build.actor_tags, build.target, extra
    )
    (record,) = asyncio.run(adapter.issue([padded], port, runtime))
    ((_, queried),) = port.queries
    assert len(queried) == MAX_PLACEMENT_CANDIDATES
    assert port.issued == []
    assert record.outcome == "rejected" and "no legal placement among 8" in record.reason
    assert runtime.task_status(build.task_id) == "pending"  # the runtime owns the retry


@pytest.mark.parametrize(
    "site", [None, (1.0, 2.0, 3.0), (math.inf, 1.0), "24,34"], ids=["none", "3d", "inf", "text"]
)
def test_build_without_an_xy_site_is_rejected_before_any_query(site: object) -> None:
    port = _Port()
    adapter, runtime, commands = _ticked(_game(minerals=100), port)
    build = _pylon_command(commands)
    malformed = CommandSpec(build.node_id, build.task_id, build.ability, build.actor_tags, site)
    (record,) = asyncio.run(adapter.issue([malformed], port, runtime))
    assert (port.queries, port.issued) == ([], [])
    assert record.outcome == "rejected" and "(x, y) candidate sites" in record.reason
    assert runtime.task_status(build.task_id) == "pending"


def test_acceptance_reports_for_no_awaiting_task_are_ignored_visibly() -> None:
    port = _Port()
    adapter, runtime, commands = _ticked(_game(minerals=100), port)
    build = _pylon_command(commands)
    assert isinstance(build.target, tuple)
    off_candidate = (build.target[0] + 0.25, build.target[1])
    assert runtime.mark_command_accepted(build.task_id, placement=off_candidate) is False
    assert runtime.mark_command_accepted("\x1b[2Jghost") is False
    assert runtime.mark_command_accepted(7) is False  # type: ignore[arg-type]
    (task,) = [t for t in runtime.active_tasks() if t.id == build.task_id]
    assert task.target == build.target and task.status == "issued"
    assert runtime.task_status("\x1b[2Jghost") is None
    later = _game(minerals=100, state=SimpleNamespace(game_loop=232))
    tick = runtime.tick(adapter.observe(later, port))
    ignored = [e.reason for e in tick.events if "acceptance ignored" in e.reason]
    assert len(ignored) == 3
    assert not any("\x1b" in reason for reason in ignored)


def test_an_acceptance_the_runtime_does_not_apply_is_not_counted_or_awaited() -> None:
    port = _Port()
    adapter, runtime, commands = _ticked(_game(minerals=100), port)
    build = _pylon_command(commands)
    assert isinstance(build.target, tuple)
    elsewhere = (build.target[0] + 40.0, build.target[1])  # not one of the task's sites
    forged = CommandSpec(build.node_id, build.task_id, build.ability, build.actor_tags, elsewhere)
    (record,) = asyncio.run(adapter.issue([forged], port, runtime))
    assert record.outcome == "ignored" and adapter.accepted_count == 0
    (task,) = [t for t in runtime.active_tasks() if t.id == build.task_id]
    assert (task.status, task.target) == ("issued", build.target)
    port.errors = [(build.actor_tags[0], build.ability, "CantBuildLocationInvalid")]
    assert adapter.collect_rejections(port, runtime) == 0  # nothing awaits that command


def test_sc2_refusal_is_reported_to_the_runtime() -> None:
    port = _Port(refusal="NotEnoughMinerals")
    adapter, runtime, commands = _ticked(_idle_game(2000), port)
    (record,) = asyncio.run(adapter.issue(commands, port, runtime))
    assert record.outcome == "rejected" and "NotEnoughMinerals" in record.reason
    assert runtime.task_status(commands[0].task_id) == "pending"


def test_commands_not_attributed_to_an_issued_task_never_reach_sc2() -> None:
    port = _Port()
    adapter, runtime, commands = _ticked(_idle_game(2000), port)
    (gather,) = commands
    forged = [
        CommandSpec(
            "economy.assign", "not-a-task", gather.ability, gather.actor_tags, gather.target
        ),
        CommandSpec("expand_now", gather.task_id, gather.ability, gather.actor_tags, gather.target),
    ]
    records = asyncio.run(adapter.issue(forged, port, runtime))
    assert [r.outcome for r in records] == ["rejected", "rejected"]
    assert port.issued == []
    assert runtime.task_status(gather.task_id) == "issued"  # the real task is untouched


@pytest.mark.parametrize(
    ("ability", "actor", "target", "reason"),
    [
        ("HARVEST_GATHER", 7000, PATCH, "is not an own unit"),  # an enemy marine as actor
        ("HARVEST_GATHER", 2000, PATCH + 99, "not a visible mineral field"),
        ("HARVEST_GATHER", 2000, 7000, "not a visible mineral field"),  # gather an enemy
        ("MOVE", 2000, 7000, "MOVE target is not a visible enemy"),  # only attacks name units
        ("HARVEST_RETURN", 2000, None, "is not a command ability"),
    ],
)
def test_commands_only_use_own_actors_command_abilities_and_visible_targets(
    ability: str, actor: int, target: int | None, reason: str
) -> None:
    port = _Port()
    game = _idle_game(2000)
    game.enemy_units = [_unit(7000, "Marine", position=(90.0, 90.0))]
    adapter, runtime, commands = _ticked(game, port)
    (gather,) = commands
    command = CommandSpec(gather.node_id, gather.task_id, ability, (actor,), target)
    (record,) = asyncio.run(adapter.issue([command], port, runtime))
    assert port.issued == []
    assert record.outcome == "rejected" and reason in record.reason
    assert runtime.task_status(gather.task_id) == "pending"


def test_late_action_errors_reject_only_commands_still_awaiting_acknowledgement() -> None:
    port = _Port()
    adapter, runtime, commands = _ticked(_idle_game(2000, 2001), port)
    asyncio.run(adapter.issue(commands, port, runtime))
    first, second = commands
    port.errors = [
        (first.actor_tags[0], "HARVEST_GATHER", "CantFindMinerals"),
        (first.actor_tags[0], "HARVEST_GATHER", "Repeat"),
    ]
    assert adapter.collect_rejections(port, runtime) == 1  # a repeated report is dropped
    assert runtime.task_status(first.task_id) == "pending"
    assert runtime.task_status(second.task_id) == "issued"
    port.errors = [(9999, "HARVEST_GATHER", "UnknownActor")]
    assert adapter.collect_rejections(port, runtime) == 0
    # The observation confirms the second gather; a late error for it is stale.
    mining = _game(units=[_unit(2000), _mining(2001)], minerals=0)
    mining.state.game_loop = 232
    runtime.tick(adapter.observe(mining, port))
    assert runtime.task_status(second.task_id) is None  # succeeded and retired
    port.errors = [(second.actor_tags[0], "HARVEST_GATHER", "CantFindMinerals")]
    assert adapter.collect_rejections(port, runtime) == 0


def test_a_late_error_from_a_superseded_command_does_not_reject_the_newer_one() -> None:
    """The probe's gather is confirmed, then it is sent to build; an old gather error
    arrives late. Errors match on actor *and* ability, so the build stays awaited."""
    port = _Port()
    adapter, runtime = Sc2Adapter(), _runtime()
    first = runtime.tick(adapter.observe(_game(units=[_unit(2000)], minerals=0), port))
    asyncio.run(adapter.issue(first.commands, port, runtime))
    later = _game(units=[_mining(2000)], minerals=100, state=SimpleNamespace(game_loop=232))
    second = runtime.tick(adapter.observe(later, port))  # gather acknowledged; Pylon ordered
    build = _pylon_command(list(second.commands))
    assert build.actor_tags == (2000,)
    asyncio.run(adapter.issue(second.commands, port, runtime))
    port.errors = [(2000, "HARVEST_GATHER", "CantFindMinerals")]
    assert adapter.collect_rejections(port, runtime) == 0
    assert runtime.task_status(build.task_id) == "issued"
    port.errors = [(2000, "PROTOSSBUILD_PYLON", "CantBuildLocationInvalid")]
    assert adapter.collect_rejections(port, runtime) == 1
    assert runtime.task_status(build.task_id) == "pending"


@pytest.mark.parametrize(
    "bad_reply",
    [
        lambda port: setattr(port, "legal", lambda index: "yes"),
        lambda port: setattr(port, "placement_legal", _short_reply),
    ],
    ids=["non-bool-verdict", "too-few-verdicts"],
)
def test_malformed_placement_replies_are_value_errors(bad_reply: Callable[[_Port], None]) -> None:
    port = _Port()
    adapter, runtime, commands = _ticked(_game(minerals=100), port)
    bad_reply(port)
    with pytest.raises(ValueError, match="placement"):
        asyncio.run(adapter.issue([_pylon_command(commands)], port, runtime))


async def _short_reply(ability: str, sites: Any) -> list[bool]:
    return [True]


@pytest.mark.parametrize(
    ("commands", "fragment"),
    [("not a list", "commands must be a sequence"), (["not a command"], "CommandSpec records")],
)
def test_malformed_command_batches_are_value_errors(commands: Any, fragment: str) -> None:
    port = _Port()
    adapter, runtime, _ = _ticked(_game(), port)
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(adapter.issue(commands, port, runtime))
    assert fragment in str(excinfo.value)


@pytest.mark.parametrize(
    ("errors", "fragment"),
    [
        ([("2000", "HARVEST_GATHER", "x")], "action errors[0] tag"),
        ([(2000, "HARVEST_GATHER", 7)], "must be (tag, ability, reason)"),
        ([(2000, "x")], "must be (tag, ability, reason)"),
    ],
)
def test_malformed_action_error_reports_are_value_errors(
    errors: list[object], fragment: str
) -> None:
    port = _Port()
    adapter, runtime, _ = _ticked(_game(), port)
    port.errors = errors
    with pytest.raises(ValueError) as excinfo:
        adapter.collect_rejections(port, runtime)
    assert fragment in str(excinfo.value)


# ---------------------------------------------------------------------------
# BotAIPort: explicit burnysc2 unit commands
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self, results: list[ActionResult]) -> None:
        self.results = results
        self.sent: list[Any] = []
        self.left = False

    async def actions(self, commands: list[Any], return_successes: bool = False) -> list[Any]:
        self.sent.append((list(commands), return_successes))
        return list(self.results)

    async def leave(self) -> None:
        self.left = True


def _sc2_unit(tag: int) -> Unit:
    return Unit(
        raw_pb2.Unit(tag=tag, unit_type=84), SimpleNamespace(state=SimpleNamespace(game_loop=0))
    )


def _bot(results: list[ActionResult]) -> SimpleNamespace:
    async def can_place(ability: AbilityId, positions: list[Point2]) -> list[bool]:
        bot.placed = (ability, positions)
        return [True, False][: len(positions)]

    bot = SimpleNamespace(
        client=_Client(results),
        can_place=can_place,
        is_visible=lambda pos: pos == Point2((60.0, 60.0)),
        state=SimpleNamespace(
            action_errors=[
                ActionError(881, 5, ActionResult.CantBuildLocationInvalid.value),
                ActionError(AbilityId.MOVE_MOVE.value, 6, 99_999),
                ActionError(999_999, 7, ActionResult.Error.value),
            ]
        ),
    )
    return bot


#: Every Jev command ability -> (target kind, the burnysc2 AbilityId sent).
_PORT_CASES: dict[str, tuple[object, AbilityId]] = {
    "HARVEST_GATHER": ("unit", AbilityId.HARVEST_GATHER),
    "PROTOSSBUILD_PYLON": ((24.0, 34.0), AbilityId.PROTOSSBUILD_PYLON),
    "PROTOSSBUILD_GATEWAY": ((26.5, 34.5), AbilityId.PROTOSSBUILD_GATEWAY),
    "NEXUSTRAIN_PROBE": (None, AbilityId.NEXUSTRAIN_PROBE),
    "GATEWAYTRAIN_ZEALOT": (None, AbilityId.GATEWAYTRAIN_ZEALOT),
    "MOVE": ((40.0, 40.0), AbilityId.MOVE_MOVE),  # burnysc2's own Unit.move convention
    "ATTACK": ((40.0, 40.0), AbilityId.ATTACK),
}


def test_shape_constants_have_one_source_across_adapter_bot_and_runner() -> None:
    import jev.operations as operations_module
    import jev.sc2_adapter as adapter_module

    assert adapter_module.COMMAND_ABILITIES is operations_module.COMMAND_ABILITIES
    assert adapter_module.REQUIRES_POWER is operations_module.REQUIRES_POWER
    assert adapter_module.MAX_PLACEMENT_CANDIDATES is operations_module.MAX_PLACEMENT_CANDIDATES
    assert bot_module.MatchLimits is runner.MatchLimits
    assert operations_module.REQUIRES_POWER <= operations_module.OWN_TYPES


def test_port_cases_cover_every_command_ability() -> None:
    assert set(_PORT_CASES) == COMMAND_ABILITIES


@pytest.mark.parametrize(
    ("ability", "target", "sc2_ability"),
    [(name, target, sent) for name, (target, sent) in _PORT_CASES.items()],
)
def test_port_sends_one_explicit_unit_command_per_request(
    ability: str, target: object, sc2_ability: AbilityId
) -> None:
    bot = _bot([ActionResult.Success])
    actor, patch = _sc2_unit(5), _sc2_unit(PATCH)
    port = BotAIPort(bot)  # type: ignore[arg-type]
    sent_target = patch if target == "unit" else target
    assert asyncio.run(port.issue(ability, actor, sent_target)) is None
    ((commands, verdict_per_command),) = bot.client.sent
    (command,) = commands
    assert verdict_per_command is True
    assert (command.ability, command.unit) == (sc2_ability, actor)
    expected = Point2(target) if isinstance(target, tuple) else sent_target
    assert command.target == expected and type(command.target) is type(expected)


def test_port_reports_refusals_queries_errors_and_leaves() -> None:
    bot = _bot([ActionResult.NotEnoughMinerals])
    port = BotAIPort(bot)  # type: ignore[arg-type]
    assert asyncio.run(port.issue("NEXUSTRAIN_PROBE", _sc2_unit(5), None)) == "NotEnoughMinerals"
    assert asyncio.run(port.issue("NEXUSTRAIN_PROBE", "not a unit", None)) == (
        "actor is not an SC2 unit"
    )
    bot.client.results = []
    assert "not running" in str(asyncio.run(port.issue("NEXUSTRAIN_PROBE", _sc2_unit(5), None)))
    sites = [(24.0, 34.0), (26.0, 34.0)]
    assert asyncio.run(port.placement_legal("PROTOSSBUILD_PYLON", sites)) == [True, False]
    assert bot.placed == (AbilityId.PROTOSSBUILD_PYLON, [Point2(site) for site in sites])
    assert port.action_errors() == [
        (5, "PROTOSSBUILD_PYLON", "CantBuildLocationInvalid"),
        (6, "MOVE", "action result 99999"),  # reported under the generic ability
        (7, "ability 999999", "Error"),
    ]
    assert port.is_visible((60.0, 60.0)) and not port.is_visible((10.0, 10.0))
    asyncio.run(port.leave())
    assert bot.client.left


# ---------------------------------------------------------------------------
# JevBot and JevController
# ---------------------------------------------------------------------------


def _controller(**kwargs: Any) -> JevController:
    options = MatchOptions(**kwargs)
    return JevController(load_policy(), run_id=uuid.uuid4().hex, limits=options.limits())


def test_ctrl_c_requests_a_clean_leave_then_falls_back_to_burnysc2() -> None:
    controller = _controller()
    bot = JevBot(controller)
    fallback: list[int] = []
    original = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda signum, frame: fallback.append(signum))
    try:
        asyncio.run(bot.on_start())
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        handler(signal.SIGINT, None)
        assert controller.stop_requested and fallback == []
        port = _Port()
        controller.attach(_game(), port)
        asyncio.run(bot.on_step(0))
        assert port.left and controller.terminal == "stopped"
        handler(signal.SIGINT, None)  # a second Ctrl+C reaches burnysc2's own handler
        assert fallback == [signal.SIGINT]
    finally:
        signal.signal(signal.SIGINT, original)


@pytest.mark.parametrize(
    ("sc2_result", "result"),
    [
        (Result.Victory, "win"),
        (Result.Defeat, "loss"),
        (Result.Tie, "draw"),
        (Result.Undecided, None),
    ],
)
def test_bot_records_the_sc2_result_at_match_end(sc2_result: Result, result: str | None) -> None:
    controller = _controller()
    asyncio.run(JevBot(controller).on_end(sc2_result))
    assert controller.result == result


class _CountedUnits(list[Any]):
    """An own-unit collection that counts how often the adapter reads it."""

    reads = 0

    def __iter__(self) -> Any:
        self.reads += 1
        return super().__iter__()


def test_between_policy_ticks_only_errors_and_enemy_memory_are_read() -> None:
    """At burnysc2's four-loop step only every other step is a 0.25 s policy tick."""
    controller = _controller()
    units = _CountedUnits([_unit(2000), _unit(2001)])
    game = _game(units=units, minerals=0, state=SimpleNamespace(game_loop=0))
    port = _Port()
    controller.attach(game, port)
    bunker = _unit(7200, "Bunker", position=(60.0, 60.0), is_structure=True)
    ticked = []
    for index in range(6):
        game.state.game_loop = 4 * index
        if index == 1:  # between ticks: the gather starts, one error, a glimpsed bunker
            units[:] = [_mining(2000), _unit(2001)]
            port.errors = [(2001, "HARVEST_GATHER", "CantFindMinerals")]
            game.enemy_structures = [bunker]
        else:
            port.errors = []
            game.enemy_structures = []
        result = asyncio.run(controller.step())
        ticked.append(result is not None and result.ticked)
    assert ticked == [True, False, True, False, True, False]
    assert units.reads == 3  # own units normalized on the three ticks only
    gathers = {t.actor_tag: t for t in controller.runtime.task_history()}
    assert gathers[2000].status == "succeeded"  # acknowledged at the next tick
    (pending,) = controller.runtime.active_tasks()
    assert pending.actor_tag == 2001 and "CantFindMinerals" in pending.reason
    later = controller.adapter.observe(_game(state=SimpleNamespace(game_loop=40)), port)
    assert [e.tag for e in later.remembered_enemy_structures] == [7200]  # glimpse kept


def test_controller_step_before_attach_is_a_runtime_error() -> None:
    with pytest.raises(RuntimeError, match="before attach"):
        asyncio.run(_controller().step())


# ---------------------------------------------------------------------------
# Runner: outcomes and exit codes (no SC2)
# ---------------------------------------------------------------------------


class _Launcher:
    """Plays ``steps`` JevBot steps against a stand-in game the way burnysc2 does.

    Like burnysc2, a bot that left the game ends it with a Defeat, and an exception
    out of ``on_step`` is logged and reported as a Defeat (``on_end(Defeat)``).
    """

    def __init__(
        self,
        steps: int = 3,
        *,
        result: str | None = "win",
        before_step: Callable[[SimpleNamespace, JevController, int], None] | None = None,
        play_error: BaseException | None = None,
        prepare_error: Exception | None = None,
    ) -> None:
        self.steps = steps
        self.result = result
        self.before_step = before_step
        self.play_error = play_error
        self.prepare_error = prepare_error
        self.port = _Port()
        self.options: MatchOptions | None = None
        self.played = False

    def prepare(self, options: MatchOptions) -> object:
        self.options = options
        if self.prepare_error is not None:
            raise self.prepare_error
        return "setup"

    def play(self, controller: JevController, setup: object, options: MatchOptions) -> Any:
        self.played = True
        game = _game(state=SimpleNamespace(game_loop=0))
        bot = JevBot(controller)
        controller.attach(game, self.port)  # what on_start binds against a live game

        async def run() -> str | None:
            for index in range(self.steps):
                if self.before_step is not None:
                    self.before_step(game, controller, index)
                try:
                    await bot.on_step(index)
                except Exception:
                    await bot.on_end(Result.Defeat)
                    return "loss"
                if self.port.left:
                    await bot.on_end(Result.Defeat)  # leaving the game is a Defeat
                    return "loss"
                game.state.game_loop += 8
            return self.result

        result = asyncio.run(run())
        if self.play_error is not None:
            raise self.play_error
        return result


def _stop_at_step_one(game: SimpleNamespace, controller: JevController, index: int) -> None:
    if index == 1:
        controller.request_stop()


def _jump_past_game_limit(game: SimpleNamespace, controller: JevController, index: int) -> None:
    if index == 1:
        game.state.game_loop = round(60 * GAME_LOOPS_PER_SECOND)


def _corrupt_state_at_step_one(
    game: SimpleNamespace, controller: JevController, index: int
) -> None:
    if index == 1:
        game.units = 12  # the adapter raises ValueError while observing


class _BrokenConnection(_Port):
    def action_errors(self) -> list[object]:
        raise ConnectionResetError("SC2 closed the connection")


def _launcher_with_port(port: _Port, **kwargs: Any) -> _Launcher:
    launcher = _Launcher(**kwargs)
    launcher.port = port
    return launcher


@pytest.mark.parametrize(
    ("launcher", "status", "result", "code", "exit_code", "left"),
    [
        (_Launcher(result="win"), "finished", "win", None, EXIT_OK, False),
        (_Launcher(result="loss"), "finished", "loss", None, EXIT_OK, False),
        (_Launcher(result=None), "failed", None, None, EXIT_FAILURE, False),
        (_Launcher(before_step=_stop_at_step_one), "stopped", None, None, EXIT_STOPPED, True),
        (_Launcher(play_error=KeyboardInterrupt()), "stopped", None, None, EXIT_STOPPED, False),
        (_Launcher(play_error=SystemExit()), "stopped", None, None, EXIT_STOPPED, False),
        (_Launcher(play_error=SystemExit(0)), "stopped", None, None, EXIT_STOPPED, False),
        (
            _Launcher(play_error=SystemExit(2)),
            "failed",
            None,
            "match_crashed",
            EXIT_FAILURE,
            False,
        ),
        (
            _Launcher(play_error=RuntimeError("socket")),
            "failed",
            None,
            "match_crashed",
            EXIT_FAILURE,
            False,
        ),
        (
            _Launcher(before_step=_corrupt_state_at_step_one),
            "failed",
            None,
            "match_crashed",
            EXIT_FAILURE,
            True,
        ),
        (
            _launcher_with_port(_BrokenConnection(), result="win"),
            "failed",
            None,
            "match_crashed",
            EXIT_FAILURE,
            True,
        ),
        (
            _Launcher(before_step=_jump_past_game_limit),
            "finished",
            "timeout",
            "game_timeout",
            EXIT_TIMEOUT,
            True,
        ),
        (
            _Launcher(prepare_error=Sc2Unavailable("no install")),
            "failed",
            None,
            "sc2_unavailable",
            EXIT_FAILURE,
            False,
        ),
    ],
    ids=[
        "win",
        "loss",
        "undecided",
        "stop",
        "ctrl-c",
        "sc2-launch-ctrl-c-exit",
        "sc2-exit-status-0",
        "sc2-protocol-exit-2",
        "crash-out-of-play",
        "crash-in-adapter",
        "crash-in-port",
        "game-limit",
        "unavailable",
    ],
)
def test_match_outcomes_map_to_statuses_and_exit_codes(
    launcher: _Launcher,
    status: str,
    result: str | None,
    code: str | None,
    exit_code: int,
    left: bool,
) -> None:
    outcome = run_match(MatchOptions(max_game_seconds=60), load_policy(), launcher=launcher)
    assert (outcome.status, outcome.result, outcome.exit_code) == (status, result, exit_code)
    assert (outcome.error.code if outcome.error is not None else None) == code
    assert launcher.played is (launcher.prepare_error is None)
    assert launcher.port.left is left  # the bot itself left the game on a stop or limit


def test_an_adapter_crash_is_a_failure_even_though_sc2_reports_a_defeat(
    capsys: pytest.CaptureFixture[str],
) -> None:
    launcher = _Launcher(steps=5, before_step=_corrupt_state_at_step_one)
    code = runner.main([], load_policy=load_policy, prog="jev-test", launcher=launcher)
    assert code == EXIT_FAILURE  # not the 0 an ordinary loss exits with
    captured = capsys.readouterr()
    assert "jev match failed: result=None" in captured.out
    assert "jev: match_crashed: match crashed: 'ValueError':" in captured.err
    assert "units must be a collection" in captured.err


def test_a_failure_in_on_start_is_a_crash_not_a_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(signum: int, handler: object) -> object:
        raise OSError("signal handlers unavailable")

    # Only jev.bot's view of the signal module: asyncio itself still installs handlers.
    unavailable = SimpleNamespace(SIGINT=signal.SIGINT, getsignal=signal.getsignal, signal=refuse)
    monkeypatch.setattr(bot_module, "signal", unavailable)
    controller = _controller()
    bot = JevBot(controller)
    asyncio.run(bot.on_start())  # burnysc2 would turn a raise here into a quiet Defeat
    port = _Port()
    controller.attach(_game(), port)
    assert asyncio.run(bot.on_step(0)) is None
    assert port.left and controller.terminal == "crashed"
    assert (
        controller.crash is not None and "signal handlers unavailable" in controller.crash.message
    )


class _ClosedOnLeave(_Port):
    """SC2 already ended the game on this step: leaving it raises."""

    async def leave(self) -> None:
        self.left = True
        raise ConnectionResetError("Connection already closed.")


@pytest.mark.parametrize(
    ("hook", "status", "code", "exit_code"),
    [
        (_stop_at_step_one, "stopped", None, EXIT_STOPPED),
        (_jump_past_game_limit, "finished", "game_timeout", EXIT_TIMEOUT),
    ],
    ids=["stop", "game-limit"],
)
def test_a_failed_leave_keeps_a_clean_stop_or_time_limit(
    hook: Callable[[SimpleNamespace, JevController, int], None],
    status: str,
    code: str | None,
    exit_code: int,
) -> None:
    launcher = _launcher_with_port(_ClosedOnLeave(), before_step=hook)
    outcome = run_match(MatchOptions(max_game_seconds=60), load_policy(), launcher=launcher)
    assert (outcome.status, outcome.exit_code) == (status, exit_code)
    assert (outcome.error.code if outcome.error is not None else None) == code  # not a crash
    assert "leaving the game failed: 'ConnectionResetError'" in outcome.message


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_wall_clock_limit_counts_from_the_first_game_step() -> None:
    """SC2's launch (1000 s here) is not charged to the 900 s match budget."""
    clock = _Clock()
    seconds_at_step = [1000.0, 1500.0, 1901.0]

    def advance(game: SimpleNamespace, controller: JevController, index: int) -> None:
        clock.now = seconds_at_step[index]

    launcher = _Launcher(steps=3, before_step=advance)
    options = MatchOptions(max_wall_seconds=900)
    outcome = run_match(options, load_policy(), launcher=launcher, clock=clock)
    assert (outcome.status, outcome.result, outcome.exit_code) == (
        "failed",
        "timeout",
        EXIT_TIMEOUT,
    )
    assert outcome.error is not None and outcome.error.code == "wall_timeout"
    assert outcome.game_seconds == pytest.approx(8 / GAME_LOOPS_PER_SECOND)  # 2 steps played
    assert launcher.port.left


def test_cli_defaults_are_the_plan_launch_command(capsys: pytest.CaptureFixture[str]) -> None:
    launcher = _Launcher()
    code = runner.main([], load_policy=load_policy, prog="jev-test", launcher=launcher)
    assert code == EXIT_OK
    assert launcher.options == MatchOptions(
        map_name="Simple64",
        opponent_race="Terran",
        difficulty=1,
        seed=1,
        max_game_seconds=900,
        max_wall_seconds=1800,
        realtime=False,
    )
    assert "jev match finished: result=win" in capsys.readouterr().out


@pytest.mark.parametrize(
    "options",
    [
        {"map_name": "../maps/Simple64"},
        {"opponent_race": "Protoss\n"},
        {"difficulty": 11},
        {"seed": -1},
        {"max_game_seconds": 0},
        {"max_wall_seconds": True},
        {"realtime": "yes"},
    ],
)
def test_match_options_reject_out_of_contract_values(options: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        MatchOptions(**options)


def _sc2_folder(tmp_path: Path, *, executable: bool, map_name: str | None) -> Path:
    if executable:
        (tmp_path / "Versions" / "Base90000").mkdir(parents=True)
    maps = tmp_path / "Maps"
    maps.mkdir()
    if map_name is not None:
        (maps / "Ladder").mkdir()
        (maps / "Ladder" / f"{map_name}.SC2Map").write_bytes(b"map")
    return tmp_path


@pytest.mark.parametrize(
    ("executable", "map_name", "fragment"),
    [
        (False, "Simple64", "no StarCraft II install"),
        (True, None, "map 'Simple64' not found"),
        (True, "Other64", "map 'Simple64' not found"),
    ],
)
def test_preflight_fails_early_without_an_install_or_map(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    executable: bool,
    map_name: str | None,
    fragment: str,
) -> None:
    monkeypatch.setenv(
        "SC2PATH", str(_sc2_folder(tmp_path, executable=executable, map_name=map_name))
    )
    with pytest.raises(Sc2Unavailable) as excinfo:
        Sc2Launcher().prepare(MatchOptions())
    assert fragment in excinfo.value.message


def _patch_burnysc2_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_game: Callable[..., object]
) -> None:
    """Stand in for SC2 itself: a valid-looking install and a fake ``run_game``."""
    import sc2.main
    import sc2.maps
    from sc2.paths import Paths

    root = _sc2_folder(tmp_path, executable=True, map_name="Simple64")
    executable = root / "Versions" / "Base90000" / "SC2_x64.exe"
    executable.write_bytes(b"")
    monkeypatch.setenv("SC2PATH", str(root))
    monkeypatch.setattr(Paths, "EXECUTABLE", str(executable), raising=False)
    monkeypatch.setattr(sc2.maps, "get", lambda name: f"map {name}")
    monkeypatch.setattr(sc2.main, "run_game", run_game)


def _launch_times_out(map_settings: object, players: list[Any], **kwargs: Any) -> object:
    raise TimeoutError("Websocket")  # burnysc2 gave up connecting to SC2


def _crash_after_start(map_settings: object, players: list[Any], **kwargs: Any) -> object:
    players[0].ai.controller.attach(_game(), _Port())  # what on_start does in a live game
    raise ConnectionResetError("SC2 closed the connection")


@pytest.mark.parametrize(
    ("run_game", "code"),
    [(_launch_times_out, "sc2_unavailable"), (_crash_after_start, "match_crashed")],
    ids=["before-the-bot-started", "after-the-bot-started"],
)
def test_sc2_failures_inside_run_game_are_classified_by_whether_the_match_began(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    run_game: Callable[..., object],
    code: str,
) -> None:
    _patch_burnysc2_launch(monkeypatch, tmp_path, run_game)
    outcome = run_match(MatchOptions(), load_policy(), launcher=Sc2Launcher())
    assert (outcome.status, outcome.exit_code) == ("failed", EXIT_FAILURE)
    assert outcome.error is not None and outcome.error.code == code


def test_preflight_finds_the_map_one_folder_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _sc2_folder(tmp_path, executable=True, map_name="Simple64")
    monkeypatch.setenv("SC2PATH", str(root))
    setup = Sc2Launcher().prepare(MatchOptions())
    assert setup.map_file == root / "Maps" / "Ladder" / "Simple64.SC2Map"


def _run_cli(
    *args: str, sc2_path: Path, code: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUTF8"}
    env["SC2PATH"] = str(sc2_path)  # an empty folder: SC2 is unavailable
    command = [sys.executable, "-c", code] if code else [sys.executable, "-m", "bots.jev.v1"]
    return subprocess.run(
        [*command, *args],
        cwd=sc2_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )


def test_bare_run_without_sc2_fails_early_and_launches_nothing(tmp_path: Path) -> None:
    code = (
        "import sys\n"
        "from bots.jev.v1.__main__ import main\n"
        "rc = main([])\n"
        "print('SC2_IMPORTED', 'sc2' in sys.modules)\n"
        "raise SystemExit(rc)\n"
    )
    proc = _run_cli(sc2_path=tmp_path, code=code)
    assert proc.returncode == EXIT_FAILURE
    assert "jev: sc2_unavailable: no StarCraft II install" in proc.stderr
    assert "SC2_IMPORTED False" in proc.stdout  # burnysc2 was never even imported
    assert "Traceback" not in proc.stderr


def test_out_of_range_flags_are_capped_usage_errors(tmp_path: Path) -> None:
    proc = _run_cli("--difficulty", "9" * 5000, sc2_path=tmp_path)
    assert proc.returncode == EXIT_USAGE
    assert "expected an integer in 1..10" in proc.stderr
    assert len(proc.stderr) < 3000  # the echoed value is capped
    bad_map = _run_cli("--map", "..\\Maps\\x", sc2_path=tmp_path)
    assert bad_map.returncode == EXIT_USAGE and "expected a map name" in bad_map.stderr


# ---------------------------------------------------------------------------
# Live SC2 (deselected by default; run on the SC2 host for the real smoke)
# ---------------------------------------------------------------------------


@pytest.mark.sc2
def test_live_short_match_issues_only_graph_attributed_commands() -> None:
    """One real 60-game-second match on Simple64 through the production runner."""
    captured: list[JevController] = []

    class _Recording(Sc2Launcher):
        def play(self, controller: JevController, setup: object, options: MatchOptions) -> Any:
            captured.append(controller)
            return super().play(controller, setup, options)

    outcome = run_match(
        MatchOptions(max_game_seconds=60, max_wall_seconds=600),
        load_policy(),
        launcher=_Recording(),
    )
    assert outcome.error is not None and outcome.error.code == "game_timeout", outcome
    (controller,) = captured
    records = controller.adapter.records
    assert any(r.outcome == "accepted" for r in records)
    policy = controller.runtime.policy
    assert all(policy.has_node(r.node_id) and r.task_id for r in records)
    succeeded = [t for t in controller.runtime.task_history() if t.status == "succeeded"]
    assert succeeded  # at least one command was confirmed by observation
