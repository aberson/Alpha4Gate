"""Jev interpreter tests: cadence, budgets, fairness, tasks and policy scenarios.

Everything runs through the production :class:`jev.runtime.JevRuntime` with
policies parsed by the production validator. Scenario tests load the *packaged*
v1 policy via ``bots.jev.v1.load_policy`` and feed fixed Observation fixtures,
then assert which node IDs executed using the emitted events.
"""

from __future__ import annotations

import dataclasses
import json
import math
import random
import time
import uuid
from collections import Counter
from typing import Any

import pytest
from bots.jev.v1 import load_policy

from jev.contracts import (
    MAX_COORDINATE,
    Entity,
    Event,
    Observation,
    Order,
    Policy,
)
from jev.operations import (
    BUILD_ABILITY,
    PYLON_POWER_RADIUS,
    TRAIN_ABILITY,
    decode_arrive_within,
    distance,
)
from jev.policy import PolicyError, parse_policy
from jev.runtime import (
    COMMAND_BUDGET_REASON,
    NODE_BUDGET_REASON,
    RUNTIME_NODE_ID,
    JevRuntime,
    LifecycleConfig,
    TickConfig,
    TickResult,
)

START = (30.5, 30.5)
ENEMY_START = (120.5, 120.5)
CENTER = (75.0, 75.0)
NEXUS_TAG = 1000


# ---------------------------------------------------------------------------
# Observation fixtures (built from the contract dataclasses, never a 2nd schema)
# ---------------------------------------------------------------------------


def _nexus(*, idle: bool = True, orders: tuple[Order, ...] = ()) -> Entity:
    return Entity(
        tag=NEXUS_TAG,
        type_name="Nexus",
        position=START,
        health=1000,
        orders=orders,
        is_structure=True,
        ready=True,
        idle=idle and not orders,
    )


def _probe(tag: int, position: tuple[float, float], orders: tuple[Order, ...] = ()) -> Entity:
    return Entity(tag=tag, type_name="Probe", position=position, health=20, orders=orders)


def _mining_probe(tag: int, patch: int = 3000) -> Entity:
    return _probe(tag, (27.0 + (tag % 8) * 0.5, 25.0), (Order("HARVEST_GATHER", patch),))


def _mineral(tag: int, position: tuple[float, float]) -> Entity:
    return Entity(tag=tag, type_name="MineralField", position=position, health=0, is_structure=True)


MINERALS = tuple(_mineral(3000 + i, (26.5 + i, 22.5)) for i in range(8))


def _pylon(tag: int, position: tuple[float, float], progress: float = 1.0) -> Entity:
    return Entity(
        tag=tag,
        type_name="Pylon",
        position=position,
        health=200,
        build_progress=progress,
        is_structure=True,
    )


def _gateway(
    tag: int,
    position: tuple[float, float],
    *,
    idle: bool = True,
    orders: tuple[Order, ...] = (),
) -> Entity:
    return Entity(
        tag=tag,
        type_name="Gateway",
        position=position,
        health=500,
        orders=orders,
        is_structure=True,
        ready=True,
        idle=idle and not orders,
        powered=True,
    )


def _zealot(tag: int, position: tuple[float, float], orders: tuple[Order, ...] = ()) -> Entity:
    return Entity(tag=tag, type_name="Zealot", position=position, health=150, orders=orders)


def _marine(tag: int, position: tuple[float, float]) -> Entity:
    return Entity(tag=tag, type_name="Marine", position=position, health=45)


def _observation(
    seconds: float,
    *,
    minerals: int = 50,
    supply_used: int = 12,
    supply_cap: int = 15,
    units: tuple[Entity, ...] = (),
    structures: tuple[Entity, ...] | None = None,
    enemies: tuple[Entity, ...] = (),
    remembered: tuple[Entity, ...] = (),
    mineral_fields: tuple[Entity, ...] = MINERALS,
) -> Observation:
    return Observation(
        game_loop=round(seconds * 22.4),
        game_seconds=seconds,
        minerals=minerals,
        supply_used=supply_used,
        supply_cap=supply_cap,
        own_units=units,
        own_structures=(_nexus(),) if structures is None else structures,
        visible_enemies=enemies,
        remembered_enemy_structures=remembered,
        start_location=START,
        enemy_start_locations=(ENEMY_START,),
        expansion_locations=(),
        map_center=CENTER,
        mineral_fields=mineral_fields,
    )


# ---------------------------------------------------------------------------
# Policy builders
# ---------------------------------------------------------------------------


def _node(
    node_id: str,
    kind: str,
    children: list[str] | None = None,
    operation: str | None = None,
    **args: Any,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "label": f"label for {node_id}",
        "kind": kind,
        "children": children or [],
        "operation": operation,
        "args": args,
    }


def _build_policy(roots: list[str], nodes: list[dict[str, Any]]) -> Policy:
    return parse_policy(
        {
            "schema_version": 1,
            "family": "jev",
            "version": 1,
            "roots": roots,
            "parameters": {},
            "nodes": nodes,
        }
    )


def _probe_lane(root: str) -> list[dict[str, Any]]:
    """``root``: select the Nexus, then train a probe from it."""
    return [
        _node(root, "sequence", [f"{root}.nexus", f"{root}.train"]),
        _node(
            f"{root}.nexus",
            "select",
            operation="select_entities",
            collection="own_structures",
            filter={"types": ["Nexus"]},
            limit=1,
            bind="nexus",
        ),
        _node(f"{root}.train", "action", operation="train", unit="Probe", producers="$nexus"),
    ]


def _runtime_for(policy: Policy, **tick: Any) -> JevRuntime:
    config = TickConfig(**tick) if tick else None
    return JevRuntime(policy, run_id=uuid.uuid4().hex, tick_config=config)


def _node_events(result: TickResult) -> dict[str, Event]:
    return {e.node_id: e for e in result.events if e.kind == "node"}


def _kinds(result: TickResult, kind: str) -> list[Event]:
    return [e for e in result.events if e.kind == kind]


# ---------------------------------------------------------------------------
# Construction and cadence
# ---------------------------------------------------------------------------


def test_at_most_one_tick_per_quarter_game_second() -> None:
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    assert runtime.tick(_observation(10.0, minerals=0)).ticked
    skipped = runtime.tick(_observation(10.1, minerals=0))
    assert not skipped.ticked and skipped.events == () and skipped.commands == ()
    assert runtime.tick(_observation(10.25, minerals=0)).ticked


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("game_seconds", math.nan, "finite"),
        ("game_seconds", math.inf, "finite"),
        ("game_seconds", -1.0, "non-negative"),
        ("game_loop", -5, "non-negative"),
        ("game_loop", True, "non-negative int"),
    ],
)
def test_invalid_clock_is_rejected_before_any_state_changes(
    field: str, value: object, fragment: str
) -> None:
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    first = runtime.tick(_observation(10.0, minerals=0))
    assert first.ticked
    bad = dataclasses.replace(_observation(10.5, minerals=0), **{field: value})
    for _ in range(3):  # repeated bad input never ticks or spins
        with pytest.raises(ValueError, match=fragment):
            runtime.tick(bad)
    assert runtime.last_sequence == first.events[-1].sequence
    # The runtime is intact: the cadence still holds and valid time still ticks.
    assert not runtime.tick(_observation(10.1, minerals=0)).ticked
    assert runtime.tick(_observation(10.25, minerals=0)).ticked


def test_clock_running_backwards_is_rejected_even_on_skipped_ticks() -> None:
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    runtime.tick(_observation(10.0, minerals=0))
    with pytest.raises(ValueError, match="game_loop 112 precedes 224"):
        runtime.tick(_observation(5.0, minerals=0))
    assert not runtime.tick(_observation(10.2, minerals=0)).ticked  # seen, not ticked
    same_loop_earlier_time = dataclasses.replace(_observation(10.2, minerals=0), game_seconds=10.1)
    with pytest.raises(ValueError, match="game_seconds 10.1 precedes 10.2"):
        runtime.tick(same_loop_earlier_time)


def _with_entity(field: str, **changes: Any) -> Observation:
    base = _observation(10.5, minerals=0, units=(_probe(2000, (30.0, 26.0)),))
    if field == "units":
        return dataclasses.replace(
            base, own_units=(dataclasses.replace(base.own_units[0], **changes),)
        )
    return dataclasses.replace(base, **{field: changes["value"]})


@pytest.mark.parametrize(
    ("bad", "fragment"),
    [
        (lambda: _with_entity("start_location", value=(math.nan, 1.0)), "start_location"),
        (lambda: _with_entity("map_center", value=(math.inf, 1.0)), "map_center"),
        (
            lambda: _with_entity("expansion_locations", value=((1.0, math.nan),)),
            "expansion_locations",
        ),
        (lambda: _with_entity("enemy_start_locations", value=((1.0,),)), "enemy_start_locations"),
        (lambda: _with_entity("units", position=(math.nan, 0.0)), "position"),
        (lambda: _with_entity("units", health=math.inf), "health"),
        (lambda: _with_entity("units", shield=math.nan), "shield"),
        (lambda: _with_entity("units", shield=-1.0), "shield"),
        (lambda: _with_entity("units", orders=(Order("MOVE", (math.nan, 1.0)),)), "order target"),
        (lambda: _with_entity("units", orders=(Order("MOVE", None, math.nan),)), "order progress"),
        (lambda: _with_entity("units", tag=True), "tag"),
        (lambda: _with_entity("minerals", value=-1), "minerals"),
        (lambda: _with_entity("supply_cap", value=True), "supply_cap"),
        (lambda: _with_entity("units", type_name=["Probe"]), "type_name"),
        (lambda: _with_entity("units", orders=(Order(7, None),)), "order ability"),
        (lambda: _with_entity("start_location", value=(1e308, 0.0)), "coordinate"),
        (lambda: _with_entity("map_center", value=(0.0, -2 * MAX_COORDINATE)), "coordinate"),
        (lambda: _with_entity("units", position=(MAX_COORDINATE * 1.5, 0.0)), "coordinate"),
        (
            lambda: _with_entity("units", orders=(Order("MOVE", (0.0, 1e300)),)),
            "coordinate",
        ),
    ],
)
def test_non_finite_or_ill_typed_observation_numbers_are_rejected_before_any_state_change(
    bad: Any, fragment: str
) -> None:
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    first = runtime.tick(_observation(10.0, minerals=0))
    with pytest.raises(ValueError, match=fragment):
        runtime.tick(bad())
    assert runtime.last_sequence == first.events[-1].sequence  # nothing emitted or mutated
    assert runtime.tick(_observation(10.25, minerals=0)).ticked  # cadence and state intact


HUGE = 10**400  # beyond float range: must be a ValueError, never an OverflowError


@pytest.mark.parametrize(
    ("bad", "fragment"),
    [
        (lambda: _with_entity("game_seconds", value=HUGE), "game_seconds"),
        (lambda: _with_entity("game_loop", value=HUGE), "game_loop"),
        (lambda: _with_entity("minerals", value=HUGE), "minerals"),
        (lambda: _with_entity("start_location", value=(HUGE, 0.0)), "start_location"),
        (lambda: _with_entity("expansion_locations", value=((0.0, HUGE),)), "expansion"),
        (lambda: _with_entity("units", position=(HUGE, 0.0)), "position"),
        (lambda: _with_entity("units", health=HUGE), "health"),
        (lambda: _with_entity("units", shield=HUGE), "shield"),
        (lambda: _with_entity("units", build_progress=HUGE), "build_progress"),
        (lambda: _with_entity("units", tag=2**64), "tag"),
        (lambda: _with_entity("units", orders=(Order("MOVE", None, HUGE),)), "order progress"),
        (lambda: _with_entity("units", orders=(Order("MOVE", (0.0, HUGE)),)), "order target"),
        (lambda: _with_entity("units", orders=(Order("ATTACK", 2**64),)), "order target"),
    ],
)
def test_huge_numbers_in_every_observation_field_are_value_errors(bad: Any, fragment: str) -> None:
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    first = runtime.tick(_observation(10.0, minerals=0))
    with pytest.raises(ValueError, match=fragment):
        runtime.tick(bad())
    assert runtime.last_sequence == first.events[-1].sequence


@pytest.mark.parametrize(
    "make",
    [
        lambda: TickConfig(min_tick_interval_seconds=HUGE),
        lambda: TickConfig(max_commands=HUGE),
        lambda: LifecycleConfig(ack_timeout_seconds=HUGE),
        lambda: LifecycleConfig(task_history_limit=HUGE),
    ],
)
def test_huge_numbers_in_configs_are_value_errors(make: Any) -> None:
    with pytest.raises(ValueError):
        make()


@pytest.mark.parametrize(
    ("bad", "fragment"),
    [
        (lambda: _with_entity("own_units", value=None), "own_units"),
        (lambda: _with_entity("own_units", value=[_probe(2000, (1.0, 1.0))]), "own_units"),
        (lambda: _with_entity("own_units", value=(object(),)), r"own_units\[0\]"),
        (lambda: _with_entity("mineral_fields", value=None), "mineral_fields"),
        (lambda: _with_entity("enemy_start_locations", value=None), "enemy_start_locations"),
        (lambda: _with_entity("start_location", value=[1.0, 2.0]), "start_location"),
        (lambda: _with_entity("units", orders=None), "orders"),
        (lambda: _with_entity("units", orders=("MOVE",)), r"orders\[0\]"),
        (lambda: _with_entity("units", orders=(Order("MOVE", "x"),)), "order target"),
        (lambda: _with_entity("units", orders=(Order("MOVE", True),)), "order target"),
        (lambda: _with_entity("units", tag=-1), "tag"),
        (lambda: _with_entity("units", is_flying="yes"), "is_flying"),
        (lambda: _with_entity("units", ready="no"), "ready"),
    ],
)
def test_structurally_malformed_observations_are_value_errors_with_a_field_path(
    bad: Any, fragment: str
) -> None:
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    first = runtime.tick(_observation(10.0, minerals=0))
    with pytest.raises(ValueError, match=fragment):
        runtime.tick(bad())
    assert runtime.last_sequence == first.events[-1].sequence  # nothing mutated
    assert runtime.tick(_observation(10.25, minerals=0)).ticked


class _RaisingRepr:
    def __repr__(self) -> str:
        raise RuntimeError("hostile __repr__")


def _dag(depth: int = 40) -> tuple[Any, ...]:
    """A shared-subobject DAG: 2**depth leaves if expanded, ``depth`` objects in memory."""
    value: tuple[Any, ...] = ()
    for _ in range(depth):
        value = (value, value)
    return value


def _cyclic() -> list[Any]:
    loop: list[Any] = []
    loop.append(loop)
    return loop


@pytest.mark.parametrize(
    ("bad", "fragment"),
    [
        pytest.param(lambda: _with_entity("units", tag=_RaisingRepr()), "tag", id="raising-repr"),
        pytest.param(
            lambda: _with_entity("start_location", value=_dag()), "start_location", id="dag"
        ),
        pytest.param(lambda: _with_entity("units", type_name=_cyclic()), "type_name", id="cyclic"),
        pytest.param(
            lambda: _with_entity("units", orders=(Order(_dag(), None),)),
            "ability",
            id="dag-ability",
        ),
        pytest.param(
            lambda: _with_entity("own_units", value=(_probe(1, (1.0, 1.0)),) * 4097),
            "own_units has 4097 entities",
            id="fan-out-entities",
        ),
        pytest.param(
            lambda: _with_entity("expansion_locations", value=((1.0, 1.0),) * 257),
            "expansion_locations has 257 points",
            id="fan-out-locations",
        ),
        pytest.param(
            lambda: _with_entity("units", orders=(Order("MOVE", None),) * 65),
            "65 orders",
            id="fan-out-orders",
        ),
    ],
)
def test_hostile_observation_shapes_are_bounded_value_errors(bad: Any, fragment: str) -> None:
    """Class sweep: DAG, cyclic, raising-repr and huge fan-out inputs to tick()."""
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    runtime.tick(_observation(10.0, minerals=0))
    hostile = bad()
    started = time.perf_counter()
    with pytest.raises(ValueError, match=fragment) as excinfo:
        runtime.tick(hostile)
    assert time.perf_counter() - started < 5.0  # exponential walks never finish
    assert len(str(excinfo.value)) < 600  # rendering is bounded too
    assert runtime.tick(_observation(10.25, minerals=0)).ticked


def test_public_runtime_entry_points_reject_hostile_inputs_with_documented_errors() -> None:
    """Bugs-lens sweep over JevRuntime's public surface."""
    policy = _build_policy(["lane"], _probe_lane("lane"))
    with pytest.raises(ValueError, match="invalid_run_id"):
        JevRuntime(policy, run_id=None)
    with pytest.raises(PolicyError):
        JevRuntime(None, run_id=uuid.uuid4().hex)
    with pytest.raises(ValueError, match="tick_config"):
        JevRuntime(policy, run_id=uuid.uuid4().hex, tick_config={})
    with pytest.raises(ValueError, match="lifecycle_config"):
        JevRuntime(policy, run_id=uuid.uuid4().hex, lifecycle_config=3)
    runtime = _runtime_for(policy)
    for hostile in (None, "observation", {"game_loop": 0}):
        with pytest.raises(ValueError, match="needs an Observation"):
            runtime.tick(hostile)
    runtime.tick(_observation(0.0))
    hostile_ids = (None, 123, [1], {"a": 1}, "x" * 100_000, _dag(), _RaisingRepr())
    reasons = (None, HUGE, "r", _dag(), "\x1b" * 50, _cyclic(), _RaisingRepr())
    for task_id, reason in zip(hostile_ids, reasons, strict=True):
        assert runtime.mark_command_rejected(task_id, reason) is False
    good = {"status": "running", "updated_at": "2026-10-07T00:00:00Z"}
    for bad in (
        {"status": "bogus"},
        {"result": "victory"},
        {"updated_at": None},
        {"updated_at": ""},
        {"recent_events": None},
        {"recent_events": ("not an event",)},
        {"error": "boom"},
    ):
        with pytest.raises(ValueError):
            runtime.run_state(**{**good, **bad})
    assert runtime.run_state(**good).status == "running"


@pytest.mark.parametrize(
    ("make", "fragment"),
    [
        (lambda: TickConfig(min_tick_interval_seconds=math.nan), "min_tick_interval_seconds"),
        (lambda: TickConfig(min_tick_interval_seconds=-0.1), "min_tick_interval_seconds"),
        (lambda: TickConfig(max_commands=True), "max_commands"),
        (lambda: TickConfig(max_node_evaluations=0), "max_node_evaluations"),
        (lambda: LifecycleConfig(task_history_limit=-1), "task_history_limit"),
        (lambda: LifecycleConfig(max_retries=-1), "max_retries"),
        (lambda: LifecycleConfig(ack_timeout_seconds=math.nan), "ack_timeout_seconds"),
        (lambda: LifecycleConfig(build_deadline_seconds=0.0), "build_deadline_seconds"),
        (lambda: LifecycleConfig(failure_cooldown_seconds=math.inf), "failure_cooldown_seconds"),
        (lambda: LifecycleConfig(max_active_tasks=0), "max_active_tasks"),
    ],
)
def test_runtime_configs_are_validated_at_construction(make: Any, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        make()
    defaults = LifecycleConfig(task_history_limit=0, retry_interval_seconds=0.0)
    assert defaults.max_retries == 3  # D4: three retries after the first attempt
    assert LifecycleConfig(max_retries=0).max_retries == 0  # never retry is legal


def test_tick_budgets_smaller_than_root_count_are_rejected() -> None:
    lanes = [f"lane{i}" for i in range(4)]
    nodes = [n for lane in lanes for n in _probe_lane(lane)]
    policy = _build_policy(lanes, nodes)
    with pytest.raises(ValueError, match="at least the number of roots"):
        _runtime_for(policy, max_node_evaluations=3)
    with pytest.raises(ValueError, match="at least the number of roots"):
        _runtime_for(policy, max_commands=3)
    # At exactly one unit per root, every root is still serviced every tick.
    result = _runtime_for(policy, max_node_evaluations=4, max_commands=4).tick(_observation(0.0))
    assert set(result.executed_nodes()) == set(lanes)
    assert set(result.root_status) == set(lanes)
    assert [d.facts["root"] for d in _kinds(result, "diagnostic")] == lanes


# ---------------------------------------------------------------------------
# Lane fairness and budgets
# ---------------------------------------------------------------------------


def test_running_lane_does_not_starve_later_lanes() -> None:
    policy = _build_policy(
        ["blocked", "worker"],
        [
            _node("blocked", "sequence", ["blocked.wait"]),
            _node("blocked.wait", "wait", operation="game_time_compare", op=">=", seconds=9999),
            *_probe_lane("worker"),
        ],
    )
    runtime = _runtime_for(policy)
    for second in (0.0, 0.5):
        result = runtime.tick(_observation(second))
        assert result.root_status == {"blocked": "running", "worker": "running"}
        events = _node_events(result)
        assert events["blocked.wait"].status == "running"
        assert events["blocked.wait"].reason.startswith("waiting:")
        assert {"worker", "worker.nexus", "worker.train"} <= set(events)
    first = runtime.tick(_observation(1.0))
    assert _node_events(first)["worker.train"].status == "running"
    state = runtime.run_state(status="running", updated_at="2026-10-07T00:00:00Z")
    assert "blocked.wait" in state.waiting_nodes
    assert "worker.train" in state.active_nodes


def test_node_budget_bounds_a_pathological_wide_lane_without_starving_others() -> None:
    """120 groups x 5 always-true conditions: 721 nodes, far past the 256 default."""
    groups, per_group = 120, 5
    nodes = [_node("wide", "sequence", [f"wide.g{g}" for g in range(groups)])]
    for g in range(groups):
        leaves = [f"wide.g{g}.c{c}" for c in range(per_group)]
        nodes.append(_node(f"wide.g{g}", "sequence", leaves))
        nodes.extend(
            _node(leaf, "condition", operation="game_time_compare", op=">=", seconds=0)
            for leaf in leaves
        )
    policy = _build_policy(["wide", "worker"], [*nodes, *_probe_lane("worker")])
    runtime = _runtime_for(policy)  # production defaults: 256 evaluations per tick
    for second in (0.0, 0.25, 0.5):
        result = runtime.tick(_observation(second))
        evaluated = result.executed_nodes()
        wide = [n for n in evaluated if n.startswith("wide")]
        assert len(wide) == 128  # ceil(256 / 2 lanes); the breached node is not evaluated
        assert len(evaluated) <= 256
        diagnostics = _kinds(result, "diagnostic")
        assert [d.reason for d in diagnostics] == [NODE_BUDGET_REASON]
        assert diagnostics[0].facts["root"] == "wide"
        assert diagnostics[0].facts["lane_allowance"] == 128
        assert diagnostics[0].node_id.startswith("wide.g")
        assert result.root_status["wide"] == "running"
        assert {"worker", "worker.nexus", "worker.train"} <= set(evaluated)


def test_coordinates_at_the_bound_are_accepted_and_stay_finite() -> None:
    """|coordinate| == MAX_COORDINATE is legal; derived points stay finite."""
    policy = _build_policy(
        ["lane"],
        [
            _node("lane", "sequence", ["lane.point"]),
            _node(
                "lane.point",
                "select",
                operation="select_point",
                **{"from": "start_location", "toward": "map_center", "distance": 8, "bind": "p"},
            ),
        ],
    )
    edge = dataclasses.replace(
        _observation(0.0),
        start_location=(-MAX_COORDINATE, -MAX_COORDINATE),
        map_center=(MAX_COORDINATE, MAX_COORDINATE),
    )
    result = _runtime_for(policy).tick(edge)
    (event,) = [e for e in result.events if e.node_id == "lane.point"]
    point = event.facts["points"][0]
    assert event.status == "success" and all(math.isfinite(v) for v in point)


def test_budget_yielded_node_is_reported_as_waiting_in_run_state() -> None:
    """A lane stalled by the evaluation budget is visible, not silently missing."""
    nodes = [_node("wide", "sequence", [f"wide.c{i}" for i in range(100)])]
    nodes += [
        _node(f"wide.c{i}", "condition", operation="game_time_compare", op=">=", seconds=0)
        for i in range(100)
    ]
    runtime = _runtime_for(_build_policy(["wide"], nodes), max_node_evaluations=10)
    result = runtime.tick(_observation(0.0))
    (diagnostic,) = _kinds(result, "diagnostic")
    assert diagnostic.reason == NODE_BUDGET_REASON
    yielded = diagnostic.node_id  # the first node the budget did not allow
    assert yielded not in result.executed_nodes()
    state = runtime.run_state(status="running", updated_at="2026-10-07T00:00:00Z")
    assert yielded in state.waiting_nodes and "wide" in state.waiting_nodes
    assert yielded not in state.active_nodes


def test_node_budget_bounds_a_deep_chain() -> None:
    depth = 120
    nodes = [_node(f"deep.d{i}", "sequence", [f"deep.d{i + 1}"]) for i in range(depth)]
    nodes.append(
        _node(f"deep.d{depth}", "condition", operation="game_time_compare", op=">=", seconds=0)
    )
    policy = _build_policy(["deep.d0"], nodes)
    result = _runtime_for(policy).tick(_observation(0.0))
    assert len(result.executed_nodes()) == depth + 1  # fits the default budget
    assert _kinds(result, "diagnostic") == []
    small = _runtime_for(policy, max_node_evaluations=40).tick(_observation(0.0))
    assert len(small.executed_nodes()) == 40
    assert [d.reason for d in _kinds(small, "diagnostic")] == [NODE_BUDGET_REASON]
    assert small.root_status["deep.d0"] == "running"


def _move_lane(root: str) -> list[dict[str, Any]]:
    """``root``: move every Zealot to a point eight units toward the map centre."""
    return [
        _node(root, "sequence", [f"{root}.point", f"{root}.zealots", f"{root}.move"]),
        _node(
            f"{root}.point",
            "select",
            operation="select_point",
            **{"from": "start_location", "toward": "map_center", "distance": 8, "bind": "rally"},
        ),
        _node(
            f"{root}.zealots",
            "select",
            operation="select_entities",
            collection="own_units",
            filter={"types": ["Zealot"]},
            limit=200,
            bind="zealots",
        ),
        _node(
            f"{root}.move",
            "action",
            operation="move",
            units="$zealots",
            target="$rally",
            arrive_within=3,
        ),
    ]


def test_command_budget_caps_commands_and_deduplicates_the_remainder() -> None:
    zealots = tuple(_zealot(5000 + i, (60.0, 60.0)) for i in range(50))
    runtime = _runtime_for(_build_policy(["army"], _move_lane("army")))
    first = runtime.tick(_observation(0.0, units=zealots))
    assert len(first.commands) == 32
    assert [d.reason for d in _kinds(first, "diagnostic")] == [COMMAND_BUDGET_REASON]
    assert _node_events(first)["army.move"].status == "running"
    second = runtime.tick(_observation(0.25, units=zealots))
    assert len(second.commands) == 18
    commanded = [c.actor_tags[0] for c in (*first.commands, *second.commands)]
    assert sorted(commanded) == sorted(z.tag for z in zealots)  # each exactly once
    third = runtime.tick(_observation(0.5, units=zealots))
    assert third.commands == ()
    assert len(runtime.active_tasks()) == 50


def test_lane_command_share_cannot_be_consumed_by_an_earlier_lane() -> None:
    zealots = tuple(_zealot(5000 + i, (60.0, 60.0)) for i in range(50))
    policy = _build_policy(["first", "worker"], [*_move_lane("first"), *_probe_lane("worker")])
    result = _runtime_for(policy).tick(_observation(0.0, units=zealots))
    by_node = [c.node_id for c in result.commands]
    assert by_node.count("first.move") == 16  # ceil(32 / 2)
    assert by_node.count("worker.train") == 1


# ---------------------------------------------------------------------------
# Tasks: dedup, reservations, acknowledgement, retries, cooldown
# ---------------------------------------------------------------------------


def test_reevaluation_does_not_duplicate_an_active_task() -> None:
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    first = runtime.tick(_observation(0.0))
    assert [(c.node_id, c.ability, c.actor_tags) for c in first.commands] == [
        ("lane.train", TRAIN_ABILITY["Probe"], (NEXUS_TAG,))
    ]
    task_id = f"{runtime.run_id}:1"
    assert first.commands[0].task_id == task_id
    second = runtime.tick(_observation(0.25, minerals=50))
    assert second.commands == ()
    assert _node_events(second)["lane.train"].reason.startswith("1 task(s) in progress")
    (task,) = runtime.active_tasks()
    assert task.id == task_id and task.intent_key == f"lane.train|{NEXUS_TAG}"
    assert task.status == "issued" and task.attempts == 1


def test_commitments_prevent_overspending_across_lanes() -> None:
    gate = _gateway(1100, (36.5, 30.5))
    policy = _build_policy(
        ["zealots", "probes"],
        [
            _node("zealots", "sequence", ["zealots.gates", "zealots.train"]),
            _node(
                "zealots.gates",
                "select",
                operation="select_entities",
                collection="own_structures",
                filter={"types": ["Gateway"]},
                limit=4,
                bind="gates",
            ),
            _node("zealots.train", "action", operation="train", unit="Zealot", producers="$gates"),
            *_probe_lane("probes"),
        ],
    )
    runtime = _runtime_for(policy)
    structures = (_nexus(), gate)
    first = runtime.tick(_observation(0.0, minerals=120, structures=structures))
    assert [c.node_id for c in first.commands] == ["zealots.train"]
    assert _node_events(first)["probes.train"].reason == "insufficient minerals"
    # Unacknowledged commitment still counts on the next tick (SC2 has not spent it).
    second = runtime.tick(_observation(0.25, minerals=120, structures=structures))
    assert second.commands == ()
    assert _node_events(second)["probes.train"].reason == "insufficient minerals"


def test_task_lifecycle_acknowledge_then_complete() -> None:
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    runtime.tick(_observation(0.0))
    training = (Order(TRAIN_ABILITY["Probe"], None, 0.2),)
    acked = runtime.tick(_observation(0.25, minerals=0, structures=(_nexus(orders=training),)))
    statuses = [(e.kind, e.status) for e in acked.events if e.kind == "task"]
    assert statuses == [("task", "running")]
    (task,) = runtime.active_tasks()
    assert task.status == "running" and task.deadline_game_seconds == pytest.approx(60.25)
    done = runtime.tick(_observation(12.0, minerals=0, structures=(_nexus(),)))
    assert [e.status for e in _kinds(done, "task")][0] == "succeeded"
    assert runtime.task_history()[-1].reason == "unit trained"


def test_training_is_confirmed_by_a_new_unit_when_its_queue_was_never_seen() -> None:
    """D4: training is confirmed from the queue *or* a new unit of the trained type."""
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    first_probe = _probe(2000, (27.0, 26.0))
    (command,) = runtime.tick(_observation(0.0, units=(first_probe,))).commands
    # The probe finished between two observations: its queue entry was never seen.
    trained = (first_probe, _probe(2001, (28.0, 26.0)))
    result = runtime.tick(_observation(0.25, minerals=0, units=trained))
    statuses = [(e.task_id, e.status) for e in _kinds(result, "task")]
    assert statuses == [(command.task_id, "running"), (command.task_id, "succeeded")]
    assert runtime.task_history()[-1].reason == "unit trained"  # not retried as unconfirmed


def test_running_training_fails_at_its_sixty_second_deadline() -> None:
    """D4: unit training has a 60-game-second deadline after acknowledgement."""
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    runtime.tick(_observation(0.0))
    stuck = (_nexus(orders=(Order(TRAIN_ABILITY["Probe"], None, 0.2),)),)
    runtime.tick(_observation(0.25, minerals=0, structures=stuck))  # acknowledged at 0.25
    on_time = runtime.tick(_observation(60.25, minerals=0, structures=stuck))
    assert not [e for e in _kinds(on_time, "task") if e.status == "failed"]
    late = runtime.tick(_observation(60.5, minerals=0, structures=stuck))
    (failed,) = [e for e in _kinds(late, "task") if e.status == "failed"]
    assert failed.facts["failure_cause"] == "deadline"
    assert any("cooling down" in d.reason for d in _kinds(late, "diagnostic"))


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"arrive_within": 4}, 4.0),
        ({"arrive_within": 2.5}, 2.5),
        ({}, 0.0),
        ({"arrive_within": True}, 0.0),
        ({"arrive_within": "4"}, 0.0),
        ({"arrive_within": None}, 0.0),
        ({"arrive_within": math.nan}, 0.0),
        ({"arrive_within": HUGE}, 0.0),
        ({"arrive_within": [4]}, 0.0),
    ],
    ids=["int", "float", "absent", "bool", "text", "none", "nan", "huge", "list"],
)
def test_arrive_within_decodes_a_radius_or_falls_back_to_zero(
    params: dict[str, Any], expected: float
) -> None:
    decoded = decode_arrive_within(params)
    assert decoded == expected and type(decoded) is float


def test_arrive_within_has_one_decoder_shared_by_ack_progress_and_runtime() -> None:
    """One decoder for the arrival radius: one read of its key in all of jev, never a copy."""
    import importlib
    import inspect
    import pkgutil
    import re

    import jev

    names = [f"jev.{info.name}" for info in pkgutil.iter_modules(jev.__path__)]
    modules = [jev, *(importlib.import_module(name) for name in names)]
    reads = re.compile(r"""(?:\.get\(|\[)\s*(?:PARAM_ARRIVE_WITHIN|["']arrive_within["'])""")
    found = [
        (module.__name__, match.group())
        for module in modules
        for match in reads.finditer(inspect.getsource(module))
    ]
    assert found == [("jev.operations", ".get(PARAM_ARRIVE_WITHIN")]  # only inside the decoder
    held = [vars(m)["decode_arrive_within"] for m in modules if "decode_arrive_within" in vars(m)]
    assert held and all(decoder is decode_arrive_within for decoder in held)  # imports, no copies


def test_unacknowledged_command_is_retried_three_times_then_fails_and_cools_down() -> None:
    """D4: at most three retries after the first attempt (four attempts in all)."""
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    runtime.tick(_observation(0.0))
    task_id = f"{runtime.run_id}:1"
    for retry, second in enumerate((5.0, 10.0, 15.0), start=1):
        result = runtime.tick(_observation(second, minerals=50))
        assert [(c.task_id, c.ability) for c in result.commands] == [
            (task_id, TRAIN_ABILITY["Probe"])
        ]
        assert runtime.active_tasks()[0].attempts == 1 + retry
    failed = runtime.tick(_observation(20.0, minerals=50))
    assert failed.commands == ()
    (failure,) = [e for e in failed.events if e.kind == "task" and e.status == "failed"]
    assert failure.facts["failure_cause"] == "unacknowledged"  # typed, not parsed from text
    assert failure.reason == "unacknowledged after 4 attempts"
    cooldown = [d for d in _kinds(failed, "diagnostic") if "cooling down" in d.reason]
    assert cooldown and cooldown[0].node_id == "lane.train"
    assert cooldown[0].facts["failure_cause"] == "unacknowledged"
    assert _node_events(failed)["lane.train"].reason.startswith("intent cooling down")
    later = runtime.tick(_observation(30.25, minerals=50))
    assert [c.task_id for c in later.commands] == [f"{runtime.run_id}:2"]


def test_rejected_command_is_retried_three_times_at_least_one_second_apart() -> None:
    """D4: a rejection retries no sooner than 1 s later, at most three times."""
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    task_id = runtime.tick(_observation(0.0)).commands[0].task_id
    second = 0.0
    for retry in (1, 2, 3):
        assert runtime.mark_command_rejected(task_id, "not enough energy")
        soon = runtime.tick(_observation(second + 0.5, minerals=50))
        assert soon.commands == ()
        second += 1.0
        result = runtime.tick(_observation(second, minerals=50))
        assert [c.task_id for c in result.commands] == [task_id]
        assert runtime.active_tasks()[0].attempts == 1 + retry
    assert runtime.mark_command_rejected(task_id, "not enough energy")
    assert runtime.active_tasks() == ()
    (finished,) = [t for t in runtime.task_history() if t.id == task_id]
    assert finished.status == "failed" and finished.reason.endswith("attempts exhausted")


def test_terminal_task_counts_cover_the_whole_run_despite_bounded_history() -> None:
    policy = _build_policy(
        ["economy", "check"],
        [
            _node("economy", "sequence", ["economy.idle", "economy.patches", "economy.assign"]),
            _node(
                "economy.idle",
                "select",
                operation="select_entities",
                collection="own_units",
                filter={"types": ["Probe"], "idle": True},
                limit=32,
                exclude_busy=True,
                bind="idle",
            ),
            _node(
                "economy.patches",
                "select",
                operation="select_entities",
                collection="mineral_fields",
                limit=8,
                bind="patches",
            ),
            _node(
                "economy.assign",
                "action",
                operation="gather",
                workers="$idle",
                minerals="$patches",
                per_patch=2,
            ),
            _node("check", "sequence", ["check.done"]),
            _node(
                "check.done",
                "condition",
                operation="task_count_compare",
                node="economy.assign",
                statuses=["succeeded"],
                op=">=",
                value=0,
            ),
        ],
    )
    runtime = JevRuntime(
        policy,
        run_id=uuid.uuid4().hex,
        lifecycle_config=LifecycleConfig(task_history_limit=2),
    )
    idle = tuple(_probe(2000 + i, (27.0 + i, 26.0)) for i in range(4))
    runtime.tick(_observation(0.0, units=idle))
    mining = tuple(_mining_probe(2000 + i) for i in range(4))
    counts = []
    for second in (0.25, 0.5, 0.75):
        result = runtime.tick(_observation(second, units=mining))
        counts.append(_node_events(result)["check.done"].facts["count"])
    assert len(runtime.task_history()) == 2  # bounded retention ...
    assert counts == [4, 4, 4]  # ... but the run total never shrinks


def test_dead_actor_fails_task_immediately_without_cooldown() -> None:
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    runtime.tick(_observation(0.0))
    gone = runtime.tick(_observation(0.25, structures=()))
    failed = [e for e in _kinds(gone, "task") if e.status == "failed"]
    assert failed and failed[0].reason == "actor lost"
    assert failed[0].facts["failure_cause"] == "lost"
    assert not [d for d in _kinds(gone, "diagnostic") if "cooling down" in d.reason]
    assert runtime.active_tasks() == ()


def _actor_lane(select: dict[str, Any], action: dict[str, Any]) -> Policy:
    return _build_policy(
        ["lane"],
        [_node("lane", "sequence", ["lane.pick", "lane.act"]), select, action],
    )


def _pick(collection: str, types: list[str], bind: str = "who") -> dict[str, Any]:
    return _node(
        "lane.pick",
        "select",
        operation="select_entities",
        collection=collection,
        filter={"types": types},
        limit=4,
        bind=bind,
    )


@pytest.mark.parametrize(
    ("policy", "reason"),
    [
        pytest.param(
            lambda: _actor_lane(
                _pick("visible_enemies", ["Marine"]),
                _node(
                    "lane.act",
                    "action",
                    operation="move",
                    units="$who",
                    target="$who",
                    arrive_within=3,
                ),
            ),
            "is not an own unit",
            id="move-enemy-units",
        ),
        pytest.param(
            lambda: _actor_lane(
                _pick("visible_enemies", ["Gateway"]),
                _node("lane.act", "action", operation="train", unit="Zealot", producers="$who"),
            ),
            "is not an own structure",
            id="train-from-enemy-gateway",
        ),
        pytest.param(
            lambda: _actor_lane(
                _pick("own_units", ["Probe"]),  # an own worker, but bound as the "patch" too
                _node(
                    "lane.act",
                    "action",
                    operation="gather",
                    workers="$who",
                    minerals="$who",
                    per_patch=2,
                ),
            ),
            "no mineral patch bound",
            id="gather-non-mineral-target",
        ),
    ],
)
def test_actions_never_command_entities_jev_does_not_own(policy: Any, reason: str) -> None:
    enemies = (
        _marine(9001, (40.0, 40.0)),
        Entity(9002, "Gateway", (60.0, 60.0), 500.0, is_structure=True, ready=True, idle=True),
    )
    units = (_zealot(6000, (36.0, 36.0)), _probe(2000, (30.0, 26.0)))
    obs = _observation(0.0, minerals=500, units=units, enemies=enemies)
    result = _runtime_for(policy()).tick(obs)
    assert result.commands == ()
    act = _node_events(result)["lane.act"]
    assert act.status == "failure" and reason in act.reason


def test_build_requires_an_own_worker_type() -> None:
    policy = _build_policy(
        ["build"],
        [
            _node("build", "sequence", ["build.site", "build.worker", "build.go"]),
            _node(
                "build.site",
                "select",
                operation="select_placement",
                structure="Pylon",
                near="start_location",
                radius_min=6,
                radius_max=10,
                require_power=False,
                limit=8,
                bind="site",
            ),
            _node(
                "build.worker",
                "select",
                operation="select_entities",
                collection="own_units",
                filter={"types": ["Zealot"]},
                limit=1,
                bind="worker",
            ),
            _node(
                "build.go",
                "action",
                operation="build",
                structure="Pylon",
                worker="$worker",
                site="$site",
            ),
        ],
    )
    result = _runtime_for(policy).tick(
        _observation(0.0, minerals=500, units=(_zealot(6000, (25.0, 30.0)),))
    )
    assert result.commands == ()
    assert _node_events(result)["build.go"].reason == "entity 6000 is not a Probe"


def test_rejection_reasons_are_rendered_and_capped() -> None:
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    task_id = runtime.tick(_observation(0.0)).commands[0].task_id
    hostile = "\x1b[31m" + "r" * 10_000
    assert runtime.mark_command_rejected(task_id, hostile) is True
    assert not runtime.mark_command_rejected("x" * 10_000, hostile)  # unknown id: ignored
    result = runtime.tick(_observation(0.25, minerals=50))
    texts = [e.reason for e in result.events] + [
        str(value) for e in result.events for value in e.facts.values()
    ]
    rejection_texts = [t for t in texts if "rrrr" in t or "xxxx" in t]
    assert rejection_texts  # both the task reason and the diagnostic carry them
    assert all(len(t) < 500 and "\x1b" not in t for t in rejection_texts)


def test_ignored_command_rejections_are_visible() -> None:
    """Adapter bugs (unknown or no-longer-issued task ids) surface as diagnostics."""
    runtime = _runtime_for(_build_policy(["lane"], _probe_lane("lane")))
    task_id = runtime.tick(_observation(0.0)).commands[0].task_id

    def ignored(result: TickResult) -> list[tuple[str, object]]:
        return [
            (d.node_id, d.facts["task_status"])
            for d in _kinds(result, "diagnostic")
            if "rejection ignored" in d.reason
        ]

    assert runtime.mark_command_rejected("deadbeef:99", "no such task") is False
    # Delivered with the next tick, under the runtime pseudo-node (no policy node owns it).
    assert ignored(runtime.tick(_observation(0.25, structures=()))) == [
        (RUNTIME_NODE_ID, "unknown")
    ]
    # That tick failed the task (its producer vanished); a late rejection names its node.
    assert runtime.mark_command_rejected(task_id, "late report") is False
    assert ignored(runtime.tick(_observation(0.5, structures=()))) == [("lane.train", "failed")]
    assert ignored(runtime.tick(_observation(1.0, structures=()))) == []  # emitted once


def test_build_acknowledged_by_structure_progress_releases_worker() -> None:
    policy = _build_policy(
        ["build"],
        [
            _node("build", "sequence", ["build.site", "build.worker", "build.go"]),
            _node(
                "build.site",
                "select",
                operation="select_placement",
                structure="Pylon",
                near="start_location",
                radius_min=6,
                radius_max=10,
                require_power=False,
                limit=8,
                bind="site",
            ),
            _node(
                "build.worker",
                "select",
                operation="select_entities",
                collection="own_units",
                filter={"types": ["Probe"]},
                sort={"by": "distance", "from": "$site"},
                limit=1,
                exclude_busy=True,
                bind="worker",
            ),
            _node(
                "build.go",
                "action",
                operation="build",
                structure="Pylon",
                worker="$worker",
                site="$site",
            ),
        ],
    )
    runtime = _runtime_for(policy)
    worker = _probe(2001, (25.0, 30.0))
    first = runtime.tick(_observation(0.0, minerals=100, units=(worker,)))
    (command,) = first.commands
    assert command.ability == BUILD_ABILITY["Pylon"] and command.actor_tags == (2001,)
    assert isinstance(command.target, tuple) and len(command.alternatives) <= 7
    site = command.target
    warping = _pylon(4000, site, progress=0.05)
    structures = (_nexus(), warping)
    acked = runtime.tick(_observation(0.25, minerals=0, units=(worker,), structures=structures))
    assert [e.status for e in _kinds(acked, "task")] == ["running"]
    assert runtime.active_tasks()[0].deadline_game_seconds == pytest.approx(120.25)
    # The worker is free again (Protoss build), so a fresh selection can use it.
    assert _node_events(acked)["build.worker"].status == "success"
    finished = runtime.tick(
        _observation(20.0, minerals=0, units=(worker,), structures=(_nexus(), _pylon(4000, site)))
    )
    assert any(e.kind == "task" and e.status == "succeeded" for e in finished.events)


# ---------------------------------------------------------------------------
# Binding rule: validator and interpreter share one model (jev.contracts)
# ---------------------------------------------------------------------------


def _rebinding_observation(seconds: float = 0.0) -> Observation:
    return dataclasses.replace(
        _observation(
            seconds, minerals=500, units=(_probe(2000, (40.0, 40.0)), _probe(2001, (41.0, 40.0)))
        ),
        enemy_start_locations=(),  # selections from here fail
        expansion_locations=((50.0, 50.0),),  # selections from here succeed
    )


def test_failed_reselect_keeps_the_committed_binding() -> None:
    """Review repro: a failing re-select must not unbind an earlier binding."""
    policy = _build_policy(
        ["lane"],
        [
            _node("lane", "sequence", ["lane.first", "lane.retry"]),
            _node(
                "lane.first",
                "select",
                operation="select_locations",
                source="expansion_locations",
                limit=1,
                bind="p",
            ),
            _node("lane.retry", "selector", ["lane.retry.enemy", "lane.retry.near"]),
            _node(
                "lane.retry.enemy",
                "select",
                operation="select_locations",
                source="enemy_start_locations",
                limit=1,
                bind="p",
            ),
            _node(
                "lane.retry.near",
                "condition",
                operation="count_compare",
                collection="own_units",
                filter={"within": {"point": "$p", "distance": 200}},
                op=">=",
                value=1,
            ),
        ],
    )
    result = _runtime_for(policy).tick(_rebinding_observation())
    events = _node_events(result)
    assert events["lane.retry.enemy"].status == "failure"
    assert events["lane.retry.near"].status == "success"
    assert result.root_status["lane"] == "success"


def test_failed_sequence_rolls_back_a_rebinding_to_another_kind() -> None:
    """A sequence that re-binds ``u`` (units -> points) and then fails restores ``u``,
    so the selector's next child sees the units binding the validator proved."""
    policy = _build_policy(
        ["lane"],
        [
            _node("lane", "sequence", ["lane.units", "lane.choice"]),
            _node(
                "lane.units",
                "select",
                operation="select_entities",
                collection="own_units",
                filter={"types": ["Probe"]},
                limit=2,
                bind="u",
            ),
            _node("lane.choice", "selector", ["lane.choice.rebind", "lane.choice.train"]),
            _node(
                "lane.choice.rebind",
                "sequence",
                ["lane.choice.rebind.points", "lane.choice.rebind.no"],
            ),
            _node(
                "lane.choice.rebind.points",
                "select",
                operation="select_locations",
                source="expansion_locations",
                limit=1,
                bind="u",
            ),
            _node(
                "lane.choice.rebind.no",
                "condition",
                operation="game_time_compare",
                op=">=",
                seconds=86000,
            ),
            _node("lane.choice.train", "action", operation="train", unit="Probe", producers="$u"),
        ],
    )
    result = _runtime_for(policy).tick(_rebinding_observation())
    events = _node_events(result)
    assert events["lane.choice.rebind.points"].status == "success"
    assert events["lane.choice.rebind"].status == "failure"
    # Evaluated against the two Probes (units), not the rolled-back point binding.
    assert events["lane.choice.train"].reason == "entity 2000 is not an own structure"
    assert events["lane.choice.train"].facts["blocked"] == 2


_FUZZ_SELECTS: dict[str, dict[str, Any]] = {
    "loc_ok": {"operation": "select_locations", "source": "expansion_locations", "limit": 1},
    "loc_fail": {"operation": "select_locations", "source": "enemy_start_locations", "limit": 1},
    "units_ok": {
        "operation": "select_entities",
        "collection": "own_units",
        "filter": {"types": ["Probe"]},
        "limit": 4,
    },
    "units_fail": {
        "operation": "select_entities",
        "collection": "own_units",
        "filter": {"types": ["Zealot"]},
        "limit": 4,
    },
}


def _fuzz_leaf(rng: random.Random, node_id: str) -> dict[str, Any]:
    name = rng.choice(("p", "q"))
    choice = rng.randrange(8)
    if choice < 4:
        spec = dict(_FUZZ_SELECTS[rng.choice(list(_FUZZ_SELECTS))])
        operation = spec.pop("operation")
        return _node(node_id, "select", operation=operation, bind=name, **spec)
    if choice == 4:  # point consumer: accepts units or points
        return _node(
            node_id,
            "condition",
            operation="count_compare",
            collection="own_units",
            filter={"within": {"point": f"${name}", "distance": 200}},
            op=">=",
            value=rng.choice((0, 1, 9)),
        )
    if choice == 5:  # units-only consumer
        return _node(node_id, "action", operation="train", unit="Probe", producers=f"${name}")
    if choice == 6:  # units consumer with a units-or-points target
        other = rng.choice(("p", "q"))
        return _node(
            node_id,
            "action",
            operation="move",
            units=f"${name}",
            target=f"${other}",
            arrive_within=3,
        )
    seconds = rng.choice((0, 86000))  # always-true / always-false condition
    return _node(node_id, "condition", operation="game_time_compare", op=">=", seconds=seconds)


def _fuzz_tree(rng: random.Random, node_id: str, depth: int, out: list[dict[str, Any]]) -> None:
    if depth > 0 and (depth == 3 or rng.random() < 0.55):
        children = [f"{node_id}.c{i}" for i in range(rng.randint(1, 3))]
        out.append(_node(node_id, rng.choice(("sequence", "selector")), children))
        for child in children:
            _fuzz_tree(rng, child, depth - 1, out)
    else:
        out.append(_fuzz_leaf(rng, node_id))


def _select_node(node_id: str, flavour: str, bind: str) -> dict[str, Any]:
    spec = dict(_FUZZ_SELECTS[flavour])
    operation = spec.pop("operation")
    return _node(node_id, "select", operation=operation, bind=bind, **spec)


def _always(node_id: str, truth: bool) -> dict[str, Any]:
    seconds = 0 if truth else 86000
    return _node(node_id, "condition", operation="game_time_compare", op=">=", seconds=seconds)


def _consumer(node_id: str, flavour: str) -> dict[str, Any]:
    if flavour == "near":
        return _node(
            node_id,
            "condition",
            operation="count_compare",
            collection="own_units",
            filter={"within": {"point": "$p", "distance": 200}},
            op=">=",
            value=1,
        )
    if flavour == "train":
        return _node(node_id, "action", operation="train", unit="Probe", producers="$p")
    return _node(node_id, "action", operation="move", units="$p", target="$p", arrive_within=3)


def _middle(prefix: str, flavour: str) -> list[dict[str, Any]]:
    """The selector's first alternative: re-binds ``p`` and succeeds or fails."""
    if flavour in _FUZZ_SELECTS:
        return [_select_node(prefix, flavour, "p")]
    rebind, truth = flavour.split("+")  # e.g. "loc_ok+false": re-bind, then fail
    return [
        _node(prefix, "sequence", [f"{prefix}.rebind", f"{prefix}.check"]),
        _select_node(f"{prefix}.rebind", rebind, "p"),
        _always(f"{prefix}.check", truth == "true"),
    ]


def _systematic_rebinding_graphs() -> list[list[dict[str, Any]]]:
    """sequence[ bind p, selector[ <re-bind p: succeed/fail>, consume $p ], consume $p ]."""
    middles = [*_FUZZ_SELECTS, "loc_ok+false", "units_ok+false", "loc_fail+true", "units_ok+true"]
    graphs = []
    for first in ("loc_ok", "units_ok"):
        for middle in middles:
            for consumer in ("near", "train", "move"):
                for tail in (None, "near", "train"):
                    nodes = [
                        _node(
                            "lane",
                            "sequence",
                            ["lane.first", "lane.choice"] + (["lane.tail"] if tail else []),
                        ),
                        _select_node("lane.first", first, "p"),
                        _node("lane.choice", "selector", ["lane.choice.alt", "lane.choice.use"]),
                        *_middle("lane.choice.alt", middle),
                        _consumer("lane.choice.use", consumer),
                    ]
                    if tail:
                        nodes.append(_consumer("lane.tail", tail))
                    graphs.append(nodes)
    return graphs


def _tick_three_times(policy: Policy) -> list[TickResult]:
    runtime = JevRuntime(policy, run_id=uuid.uuid4().hex)
    return [runtime.tick(_rebinding_observation(second)) for second in (0.0, 0.25, 0.5)]


def _consumed_after_failed_select(policy: Policy, result: TickResult) -> bool:
    failed_select_seen = False
    for event in result.events:
        if event.kind != "node":
            continue
        kind = policy.node(event.node_id).kind
        if kind == "select" and event.status == "failure":
            failed_select_seen = True
        elif failed_select_seen and kind in ("condition", "action"):
            return True
    return False


def test_every_validator_accepted_rebinding_graph_ticks_without_error() -> None:
    """Round trip: whatever the validator accepts, the interpreter evaluates cleanly.

    Two populations, both run through ``JevRuntime.tick`` (any exception fails):
    a systematic enumeration of re-binding shapes -- bind ``p``; re-bind it in a
    selector alternative that succeeds, fails, or re-binds to the other kind and
    then fails; consume ``$p`` inside and after the selector -- and a seeded random
    generator of sequence/selector graphs binding/consuming ``$p``/``$q``.
    """
    systematic_accepted = systematic_failure_paths = 0
    for nodes in _systematic_rebinding_graphs():
        try:
            policy = _build_policy(["lane"], nodes)
        except PolicyError:
            continue
        systematic_accepted += 1
        results = _tick_three_times(policy)
        if any(_consumed_after_failed_select(policy, r) for r in results):
            systematic_failure_paths += 1
    assert systematic_accepted >= 70, systematic_accepted  # 76 of 144 (kind mismatches rejected)
    assert systematic_failure_paths >= 25, systematic_failure_paths  # 27 at present

    rng = random.Random(201)
    accepted = rebinding_graphs = failure_paths = 0
    for case in range(600):
        nodes = []
        roots = [f"r{case}x{i}" for i in range(rng.randint(1, 2))]
        for root in roots:
            _fuzz_tree(rng, root, 3, nodes)
        try:
            policy = _build_policy(roots, nodes)
        except PolicyError:
            continue
        accepted += 1
        binders = [n["args"]["bind"] for n in nodes if n["kind"] == "select"]
        rebinding_graphs += len(binders) != len(set(binders))
        results = _tick_three_times(policy)
        failure_paths += any(_consumed_after_failed_select(policy, r) for r in results)
    assert accepted >= 100, accepted
    assert rebinding_graphs >= 50, rebinding_graphs
    assert failure_paths >= 10, failure_paths


def test_runtime_is_deterministic_for_identical_inputs() -> None:
    policy = load_policy().policy
    run_id = uuid.uuid4().hex
    traces = []
    for _ in range(2):
        runtime = JevRuntime(policy, run_id=run_id)
        trace = []
        for second, minerals in ((0.0, 50), (0.25, 150), (0.5, 175)):
            result = runtime.tick(_opening(second, minerals))
            trace.append(json.dumps([e.to_dict() for e in result.events], sort_keys=True))
            trace.append(json.dumps([c.to_dict() for c in result.commands], sort_keys=True))
        traces.append(trace)
    assert traces[0] == traces[1]


# ---------------------------------------------------------------------------
# Scenarios through the packaged v1 policy
# ---------------------------------------------------------------------------

PROBE_TAGS = tuple(2000 + i for i in range(12))


def _opening(seconds: float, minerals: int, *, mining: bool = False) -> Observation:
    if mining:
        units = tuple(_mining_probe(tag, 3000 + (tag % 8)) for tag in PROBE_TAGS)
    else:
        units = tuple(_probe(tag, (27.0 + i * 0.5, 26.0)) for i, tag in enumerate(PROBE_TAGS))
    return _observation(seconds, minerals=minerals, units=units)


@pytest.fixture
def v1_runtime() -> JevRuntime:
    return JevRuntime(load_policy().policy, run_id=uuid.uuid4().hex)


def test_scenario_opening_gathers_and_holds_minerals_for_emergency_supply(
    v1_runtime: JevRuntime,
) -> None:
    result = v1_runtime.tick(_opening(0.0, 50))
    events = _node_events(result)
    assert events["economy.assign"].status == "running"
    assert events["construction.supply.low"].status == "success"
    assert events["construction.supply.build"].status == "running"
    assert "holding reservation" in events["construction.supply.build"].reason
    assert events["production.probes.train"].status == "failure"
    assert events["production.probes.train"].reason == "insufficient minerals"
    assert events["army.defend.threat"].status == "failure"
    # Every command came from the gather node and names a real task.
    assert {c.node_id for c in result.commands} == {"economy.assign"}
    assert all(c.ability == "HARVEST_GATHER" and c.task_id for c in result.commands)
    assert len(result.commands) == 8  # economy's fair share of 32
    patches = [c.target for c in result.commands]
    assert len(set(patches)) == 8  # least-loaded first: one probe per patch


def test_scenario_supply_pylon_then_probe_with_leftover(v1_runtime: JevRuntime) -> None:
    result = v1_runtime.tick(_opening(0.0, 150, mining=True))
    by_node = {c.node_id: c for c in result.commands}
    assert set(by_node) == {"construction.supply.build", "production.probes.train"}
    build = by_node["construction.supply.build"]
    assert build.ability == BUILD_ABILITY["Pylon"]
    assert build.actor_tags[0] in PROBE_TAGS
    assert isinstance(build.target, tuple)
    assert 6.0 <= distance(build.target, START) <= 12.0
    assert by_node["production.probes.train"].actor_tags == (NEXUS_TAG,)
    # D3: the nearest available mining/idle probe to the chosen site, ties by tag.
    workers = _opening(0.0, 150, mining=True).own_units
    nearest = min(workers, key=lambda w: (distance(w.position, build.target), w.tag))
    assert build.actor_tags == (nearest.tag,)
    executed = set(result.executed_nodes())
    assert {"construction.supply.site", "construction.supply.worker"} <= executed
    # The "no pending Pylon" guard sees the in-flight task on the next tick.
    nxt = v1_runtime.tick(_opening(0.25, 0, mining=True))
    assert _node_events(nxt)["construction.supply.none_ordered"].status == "failure"


def test_scenario_first_gateway_is_placed_inside_pylon_power(v1_runtime: JevRuntime) -> None:
    power = _pylon(4000, (24.0, 34.0))
    units = tuple(_mining_probe(tag) for tag in PROBE_TAGS)
    obs = _observation(
        60.0,
        minerals=150,
        supply_used=14,
        supply_cap=23,
        units=units,
        structures=(_nexus(idle=False, orders=(Order(TRAIN_ABILITY["Probe"]),)), power),
    )
    result = v1_runtime.tick(obs)
    (command,) = [c for c in result.commands if c.node_id == "construction.gateways.build"]
    assert command.ability == BUILD_ABILITY["Gateway"]
    assert isinstance(command.target, tuple)
    assert distance(command.target, power.position) <= PYLON_POWER_RADIUS
    assert _node_events(result)["construction.gateways.allowed.first"].status == "success"


def test_scenario_emergency_supply_precedes_the_first_gateway(v1_runtime: JevRuntime) -> None:
    """D3 priority: emergency supply comes before the first powered Gateway."""
    units = tuple(_mining_probe(tag) for tag in PROBE_TAGS)
    structures = (_nexus(idle=False, orders=(Order(TRAIN_ABILITY["Probe"]),)), _pylon(4000, _POWER))
    result = v1_runtime.tick(
        _observation(
            60.0, minerals=150, supply_used=20, supply_cap=23, units=units, structures=structures
        )
    )
    events = _node_events(result)
    assert events["construction.supply.build"].status == "running"
    assert "construction.gateways" not in events  # the selector stopped at supply
    construction = [c.node_id for c in result.commands if c.node_id.startswith("construction")]
    assert construction == ["construction.supply.build"]


def test_scenario_supply_trigger_is_four_free_or_fewer_with_no_pending_pylon(
    v1_runtime: JevRuntime,
) -> None:
    """D3: supply trigger is four free supply or fewer with no pending Pylon."""
    units = tuple(_mining_probe(tag) for tag in PROBE_TAGS)
    base = (_nexus(idle=False, orders=(Order(TRAIN_ABILITY["Probe"]),)), _pylon(4000, _POWER))
    five_free = v1_runtime.tick(
        _observation(60.0, minerals=0, supply_used=18, supply_cap=23, units=units, structures=base)
    )
    assert _node_events(five_free)["construction.supply.low"].status == "failure"
    warping = (*base, _pylon(4001, (36.0, 24.0), progress=0.4))
    four_free = v1_runtime.tick(
        _observation(
            61.0, minerals=400, supply_used=19, supply_cap=23, units=units, structures=warping
        )
    )
    events = _node_events(four_free)
    assert events["construction.supply.low"].status == "success"
    assert events["construction.supply.none_warping"].status == "failure"
    assert not [c for c in four_free.commands if c.node_id == "construction.supply.build"]


# --- D3 priority: additional Gateways only after the first four Zealots ---------

_POWER = (24.0, 34.0)
_FIRST_GATE = (26.5, 34.5)


def _gateway_tick(
    runtime: JevRuntime,
    seconds: float,
    *,
    gateways: tuple[Entity, ...],
    zealots: int,
    minerals: int = 400,
) -> TickResult:
    """Plenty of minerals, a ready Pylon, a busy Nexus and 14 mining probes."""
    workers = tuple(_mining_probe(tag) for tag in PROBE_TAGS[:12]) + (
        _mining_probe(2012),
        _mining_probe(2013),
    )
    army = tuple(_zealot(6000 + i, (36.0, 36.0)) for i in range(zealots))
    structures = (
        _nexus(idle=False, orders=(Order(TRAIN_ABILITY["Probe"]),)),
        _pylon(4000, _POWER),
        *gateways,
    )
    obs = _observation(
        seconds,
        minerals=minerals,
        supply_used=14 + 2 * zealots,
        supply_cap=31,
        units=workers + army,
        structures=structures,
    )
    return runtime.tick(obs)


def _gateway_builds(result: TickResult) -> list[Any]:
    return [c for c in result.commands if c.node_id == "construction.gateways.build"]


def test_no_second_gateway_while_the_first_is_under_construction(v1_runtime: JevRuntime) -> None:
    warping = dataclasses.replace(_gateway(1100, _FIRST_GATE), ready=False, build_progress=0.3)
    result = _gateway_tick(v1_runtime, 60.0, gateways=(warping,), zealots=0)
    assert _gateway_builds(result) == []
    assert _node_events(result)["construction.gateways.allowed"].status == "failure"


def test_no_second_gateway_before_the_first_four_zealots(v1_runtime: JevRuntime) -> None:
    training = _gateway(1100, _FIRST_GATE, orders=(Order(TRAIN_ABILITY["Zealot"]),))
    result = _gateway_tick(v1_runtime, 90.0, gateways=(training,), zealots=3)
    assert _gateway_builds(result) == []
    assert _node_events(result)["construction.gateways.allowed"].status == "failure"


def test_second_gateway_is_issued_once_four_zealots_exist(v1_runtime: JevRuntime) -> None:
    training = _gateway(1100, _FIRST_GATE, orders=(Order(TRAIN_ABILITY["Zealot"]),))
    result = _gateway_tick(v1_runtime, 120.0, gateways=(training,), zealots=4)
    (build,) = _gateway_builds(result)
    assert build.ability == BUILD_ABILITY["Gateway"]
    assert _node_events(result)["construction.gateways.allowed.wave_ready"].status == "success"


def test_lost_gateway_is_rebuilt_after_the_milestone(v1_runtime: JevRuntime) -> None:
    gates = (_gateway(1100, _FIRST_GATE), _gateway(1101, (21.5, 34.5)))
    # Milestone reached (four Zealots latch the attack); no minerals, so nothing is built.
    _gateway_tick(v1_runtime, 150.0, gateways=gates, zealots=4, minerals=0)
    assert v1_runtime.latches["attack_launched"] is True
    # Later: one Gateway destroyed and most Zealots lost -- the latch keeps rebuilding on.
    result = _gateway_tick(v1_runtime, 200.0, gateways=gates[:1], zealots=1)
    (build,) = _gateway_builds(result)
    assert build.ability == BUILD_ABILITY["Gateway"]
    events = _node_events(result)
    assert events["construction.gateways.allowed.wave_ready"].status == "failure"
    assert events["construction.gateways.allowed.attack_launched"].status == "success"


def test_additional_gateways_take_minerals_before_continuous_zealots(
    v1_runtime: JevRuntime,
) -> None:
    """D3 priority: additional Gateways to four, *then* continuous Zealots."""
    result = _gateway_tick(
        v1_runtime, 120.0, gateways=(_gateway(1100, _FIRST_GATE),), zealots=4, minerals=150
    )
    assert len(_gateway_builds(result)) == 1  # holds 150: nothing left for a Zealot
    assert not [c for c in result.commands if c.node_id == "production.zealots.train"]
    assert _node_events(result)["production.zealots.train"].reason.startswith(
        "waiting for minerals"
    )
    # At four Gateways every idle Gateway trains Zealots continuously.
    gates = tuple(_gateway(1100 + i, (21.5 + 3 * i, 34.5)) for i in range(4))
    full = _gateway_tick(
        JevRuntime(load_policy().policy, run_id=uuid.uuid4().hex),
        120.0,
        gateways=gates,
        zealots=4,
        minerals=400,
    )
    assert _node_events(full)["construction.gateways.below_target"].status == "failure"
    trains = [c for c in full.commands if c.node_id == "production.zealots.train"]
    assert sorted(c.actor_tags[0] for c in trains) == [1100, 1101, 1102, 1103]


# --- correctness lens: D3/D4 rules through the production interpreter ----------


def test_scenario_probe_training_stops_at_the_probe_target(v1_runtime: JevRuntime) -> None:
    workers = tuple(_mining_probe(2000 + i) for i in range(16))  # D3 target: 16 probes
    obs = _observation(
        90.0,
        minerals=500,
        supply_used=16,
        supply_cap=23,
        units=workers,
        structures=(_nexus(), _pylon(4000, (24.0, 34.0))),
    )
    result = v1_runtime.tick(obs)
    assert _node_events(result)["production.probes.below_target"].status == "failure"
    assert not [c for c in result.commands if c.ability == TRAIN_ABILITY["Probe"]]


def test_scenario_missing_power_pylon_is_replaced(v1_runtime: JevRuntime) -> None:
    workers = tuple(_mining_probe(2000 + i) for i in range(14))
    obs = _observation(
        100.0,
        minerals=150,
        supply_used=14,
        supply_cap=23,
        units=workers,
        structures=(_nexus(idle=False, orders=(Order(TRAIN_ABILITY["Probe"]),)),),
    )
    result = v1_runtime.tick(obs)
    events = _node_events(result)
    assert events["construction.supply.low"].status == "failure"  # not a supply problem
    (build,) = [c for c in result.commands if c.node_id == "construction.power.build"]
    assert build.ability == BUILD_ABILITY["Pylon"]


def test_scenario_unpowered_gateway_gets_a_pylon_near_it(v1_runtime: JevRuntime) -> None:
    """D3: two Pylons, one destroyed -> its Gateway is unpowered -> a Pylon near it."""
    unpowered_at = (40.5, 34.5)
    gates = (
        _gateway(1100, _FIRST_GATE),
        dataclasses.replace(_gateway(1101, unpowered_at), powered=False),
    )
    structures = (
        _nexus(idle=False, orders=(Order(TRAIN_ABILITY["Probe"]),)),
        _pylon(4000, _POWER),  # the surviving Pylon; the one near 1101 was destroyed
        *gates,
    )
    units = tuple(_mining_probe(2000 + i) for i in range(14))
    result = v1_runtime.tick(
        _observation(
            200.0, minerals=150, supply_used=14, supply_cap=31, units=units, structures=structures
        )
    )
    events = _node_events(result)
    assert events["construction.repower.gateway"].status == "success"
    assert "construction.power" not in events  # repowering a Gateway is tried first
    (build,) = [c for c in result.commands if c.node_id == "construction.repower.build"]
    assert build.ability == BUILD_ABILITY["Pylon"]
    assert isinstance(build.target, tuple)
    assert distance(build.target, unpowered_at) <= PYLON_POWER_RADIUS  # it re-powers 1101


def test_scenario_build_keeps_its_worker_then_reselects_from_current_facts(
    v1_runtime: JevRuntime,
) -> None:
    """D3/D4: re-evaluation preserves the chosen worker; a lost one is replaced."""
    first = v1_runtime.tick(_opening(0.0, 150, mining=True))
    (build,) = [c for c in first.commands if c.node_id == "construction.supply.build"]
    assert isinstance(build.target, tuple)
    chosen = build.actor_tags[0]
    facts = _node_events(first)["construction.supply.build"].facts  # D3: trace records both
    assert facts["worker"] == str(chosen) and facts["site"] == [build.target[0], build.target[1]]
    assert 1 + len(build.alternatives) <= 8  # D3: at most eight candidates per attempt
    # Another probe is now nearest the site: the active task keeps its worker.
    base = _opening(0.0, 0, mining=True).own_units
    other = next(w for w in base if w.tag != chosen)
    closer = dataclasses.replace(other, position=build.target)
    units = tuple(closer if w.tag == other.tag else w for w in base)
    kept = v1_runtime.tick(_observation(0.25, minerals=150, units=units))
    assert not [c for c in kept.commands if c.node_id == "construction.supply.build"]
    (task,) = [t for t in v1_runtime.active_tasks() if t.node_id == "construction.supply.build"]
    assert task.actor_tag == chosen
    # The chosen worker dies: the task fails at once and recovery reselects from facts.
    survivors = tuple(u for u in units if u.tag != chosen)
    recovered = v1_runtime.tick(_observation(0.5, minerals=150, units=survivors))
    (rebuild,) = [c for c in recovered.commands if c.node_id == "construction.supply.build"]
    assert rebuild.actor_tags == (closer.tag,)


def test_scenario_mineral_assignment_reaches_two_workers_per_patch(
    v1_runtime: JevRuntime,
) -> None:
    """D3: visible nearby patches, a two-worker target per patch."""
    idle = tuple(_probe(2000 + i, (27.0 + (i % 8) * 0.5, 26.0 + i // 8)) for i in range(16))
    first = v1_runtime.tick(_observation(0.0, minerals=0, units=idle))
    second = v1_runtime.tick(_observation(0.25, minerals=0, units=idle))
    gathers = [c for r in (first, second) for c in r.commands if c.node_id == "economy.assign"]
    assert len({c.actor_tags[0] for c in gathers}) == len(gathers) == 16
    assert Counter(c.target for c in gathers) == {m.tag: 2 for m in MINERALS}


def test_scenario_destroyed_nexus_ends_economy_but_the_army_fights_on(
    v1_runtime: JevRuntime,
) -> None:
    workers = tuple(_mining_probe(2000 + i) for i in range(10))
    army = tuple(_zealot(6000 + i, (45.0, 30.0)) for i in range(4))
    obs = _observation(
        300.0, minerals=500, supply_used=18, supply_cap=8, units=workers + army, structures=()
    )
    result = v1_runtime.tick(obs)
    assert _node_events(result)["production.probes.nexus"].status == "failure"
    assert not [c for c in result.commands if c.ability == TRAIN_ABILITY["Probe"]]
    attacks = [c for c in result.commands if c.node_id == "army.attack.go"]
    assert sorted(c.actor_tags[0] for c in attacks) == [6000, 6001, 6002, 6003]


def test_scenario_rally_point_is_eight_units_toward_the_map_center(
    v1_runtime: JevRuntime,
) -> None:
    obs = _observation(
        120.0,
        minerals=0,
        supply_used=16,
        supply_cap=23,
        units=(_zealot(6000, (60.0, 20.0)),),
        structures=(_nexus(idle=False, orders=(Order(TRAIN_ABILITY["Probe"]),)),),
    )
    result = v1_runtime.tick(obs)
    (move,) = [c for c in result.commands if c.node_id == "army.rally.move"]
    assert isinstance(move.target, tuple)
    assert abs(distance(move.target, START) - 8.0) < 0.02  # D3: eight units ...
    on_line = distance(START, move.target) + distance(move.target, CENTER) - distance(START, CENTER)
    assert abs(on_line) < 0.02  # ... toward the map centre


def test_army_task_without_progress_replans_after_thirty_seconds() -> None:
    """D4: movement checks progress every 10 s and replans after 30 s without it."""
    runtime = _runtime_for(_build_policy(["army"], _move_lane("army")))
    stuck = (60.0, 60.0)
    first = runtime.tick(_observation(0.0, units=(_zealot(5000, stuck),)))
    (command,) = first.commands
    moving = (Order("MOVE", command.target),)
    runtime.tick(_observation(0.25, units=(_zealot(5000, stuck, moving),)))  # acknowledged
    for second in (10.25, 20.25):
        result = runtime.tick(_observation(second, units=(_zealot(5000, stuck, moving),)))
        assert not [e for e in _kinds(result, "task") if e.status == "failed"]
    result = runtime.tick(_observation(30.25, units=(_zealot(5000, stuck, moving),)))
    (failed,) = [e for e in _kinds(result, "task") if e.status == "failed"]
    assert failed.facts["failure_cause"] == "no_progress"
    assert any("cooling down" in d.reason for d in _kinds(result, "diagnostic"))


def test_scenario_four_zealots_latch_attack_and_reinforcements_follow(
    v1_runtime: JevRuntime,
) -> None:
    gates = tuple(
        _gateway(
            1100 + i, (36.5, 26.5 + 4 * i), idle=False, orders=(Order(TRAIN_ABILITY["Zealot"]),)
        )
        for i in range(2)
    )
    structures = (
        _nexus(idle=False, orders=(Order(TRAIN_ABILITY["Probe"]),)),
        _pylon(4000, (24.0, 34.0)),
        *gates,
    )
    workers = tuple(_mining_probe(tag) for tag in PROBE_TAGS)
    three = tuple(_zealot(6000 + i, (45.0, 30.0)) for i in range(3))
    obs = _observation(
        150.0,
        minerals=0,
        supply_used=18,
        supply_cap=23,
        units=workers + three,
        structures=structures,
    )
    before = v1_runtime.tick(obs)
    assert _node_events(before)["army.attack.launch.first_wave.ready"].status == "failure"
    assert {c.node_id for c in before.commands} == {"army.rally.move"}
    assert v1_runtime.latches["attack_launched"] is False

    four = three + (_zealot(6003, (45.0, 30.0)),)
    obs = _observation(
        151.0,
        minerals=0,
        supply_used=20,
        supply_cap=23,
        units=workers + four,
        structures=structures,
    )
    launch = v1_runtime.tick(obs)
    events = _node_events(launch)
    assert events["army.attack.launch.first_wave.latch"].status == "success"
    assert v1_runtime.latches["attack_launched"] is True
    attacks = [c for c in launch.commands if c.node_id == "army.attack.go"]
    assert sorted(c.actor_tags[0] for c in attacks) == [6000, 6001, 6002, 6003]
    assert all(c.ability == "ATTACK" and c.target == ENEMY_START for c in attacks)
    cancelled = [e for e in launch.events if e.kind == "task" and e.status == "cancelled"]
    assert len(cancelled) == 3 and all("preempted by army.attack.go" in e.reason for e in cancelled)

    # Below four after the latch: the survivor keeps its task, the new Zealot reinforces.
    survivors = (
        _zealot(6000, (50.0, 50.0), (Order("ATTACK", ENEMY_START),)),
        _zealot(6010, (36.0, 36.0)),
    )
    obs = _observation(
        160.0,
        minerals=0,
        supply_used=16,
        supply_cap=23,
        units=workers + survivors,
        structures=structures,
    )
    reinforce = v1_runtime.tick(obs)
    assert _node_events(reinforce)["army.attack.launch.latched"].status == "success"
    assert [c.actor_tags[0] for c in reinforce.commands if c.node_id == "army.attack.go"] == [6010]


def test_scenario_defense_preempts_the_attack_lane(v1_runtime: JevRuntime) -> None:
    workers = tuple(_mining_probe(tag) for tag in PROBE_TAGS)
    zealots = tuple(_zealot(6000 + i, (60.0, 60.0)) for i in range(4))
    structures = (_nexus(idle=False, orders=(Order(TRAIN_ABILITY["Probe"]),)),)
    v1_runtime.tick(
        _observation(
            200.0,
            minerals=0,
            supply_used=20,
            supply_cap=23,
            units=workers + zealots,
            structures=structures,
        )
    )
    assert v1_runtime.latches["attack_launched"] is True
    intruder = _marine(9_007_199_254_740_993, (34.0, 34.0))  # > 2**53: must stay exact
    result = v1_runtime.tick(
        _observation(
            201.0,
            minerals=0,
            supply_used=20,
            supply_cap=23,
            units=workers + zealots,
            structures=structures,
            enemies=(intruder,),
        )
    )
    events = _node_events(result)
    assert events["army.defend.threat"].status == "success"
    assert events["army.defend.attack"].status == "running"
    assert "army.attack.go" not in events  # selector stopped at defense
    defend = [c for c in result.commands if c.node_id == "army.defend.attack"]
    assert len(defend) == 4 and all(c.target == intruder.tag for c in defend)
    cancelled = [e for e in result.events if e.kind == "task" and e.status == "cancelled"]
    assert len(cancelled) == 4 and all("army.defend.attack" in e.reason for e in cancelled)
    wire = defend[0].to_dict()
    assert wire["target"] == "9007199254740993" and wire["actor_tags"] == [
        str(defend[0].actor_tags[0])
    ]


def test_scenario_defense_ignores_flyers_and_targets_the_nearest_ground_threat() -> None:
    """D3: visible *ground* enemies within 20 of the main; nearest first, tie by tag."""
    zealots = tuple(_zealot(6000 + i, (40.0, 40.0)) for i in range(2))
    structures = (_nexus(idle=False, orders=(Order(TRAIN_ABILITY["Probe"]),)),)
    flyer = dataclasses.replace(_marine(9100, (31.0, 31.0)), is_flying=True)
    tied_low, tied_high = _marine(9150, (30.5, 36.5)), _marine(9160, (36.5, 30.5))

    def tick(enemies: tuple[Entity, ...]) -> TickResult:
        runtime = JevRuntime(load_policy().policy, run_id=uuid.uuid4().hex)
        return runtime.tick(
            _observation(
                200.0,
                minerals=0,
                supply_used=16,
                supply_cap=23,
                units=zealots,
                structures=structures,
                enemies=enemies,
            )
        )

    fight = tick((flyer, _marine(9200, (30.5, 40.5)), tied_high, tied_low))
    defend = [c for c in fight.commands if c.node_id == "army.defend.attack"]
    assert len(defend) == 2 and all(c.target == tied_low.tag for c in defend)
    calm = tick((flyer,))  # only a flyer near the main: no defense, the army rallies
    assert _node_events(calm)["army.defend.threat"].status == "failure"
    army = [c.node_id for c in calm.commands if c.node_id.startswith("army")]
    assert army == ["army.rally.move", "army.rally.move"]


_SEEN = Entity(
    tag=9300, type_name="SupplyDepot", position=(90.0, 90.0), health=400, is_structure=True
)
_REMEMBERED = Entity(
    tag=9400, type_name="Barracks", position=(100.0, 100.0), health=1000, is_structure=True
)


@pytest.mark.parametrize(
    ("enemies", "remembered", "target"),
    [
        pytest.param((_SEEN,), (_REMEMBERED,), _SEEN.tag, id="visible-structure-first"),
        pytest.param((), (_REMEMBERED,), _REMEMBERED.position, id="then-remembered-where-seen"),
    ],
)
def test_scenario_attack_takes_the_enemy_start_then_visible_then_remembered_structures(
    enemies: tuple[Entity, ...], remembered: tuple[Entity, ...], target: object
) -> None:
    """D3: the first attack goes to the enemy start; once there, visible enemy
    structures come first, then remembered ones (attack-moved to where seen)."""
    runtime = JevRuntime(load_policy().policy, run_id=uuid.uuid4().hex)
    structures = (_nexus(idle=False, orders=(Order(TRAIN_ABILITY["Probe"]),)),)

    def attacks(seconds: float, zealots: tuple[Entity, ...]) -> list[object]:
        obs = _observation(
            seconds,
            minerals=0,
            supply_used=20,
            supply_cap=23,
            units=zealots,
            structures=structures,
            enemies=enemies,
            remembered=remembered,
        )
        return [c.target for c in runtime.tick(obs).commands if c.node_id == "army.attack.go"]

    away = tuple(_zealot(6000 + i, (60.0, 60.0)) for i in range(4))
    assert attacks(200.0, away) == [ENEMY_START] * 4  # structures in view do not divert it
    there = tuple(_zealot(6000 + i, ENEMY_START, (Order("ATTACK", ENEMY_START),)) for i in range(4))
    assert attacks(200.25, there) == [target] * 4


def test_scenario_events_follow_the_wire_contract(v1_runtime: JevRuntime) -> None:
    policy_ids = {n.id for n in v1_runtime.policy.nodes}
    sequences: list[int] = []
    for second, minerals in ((0.0, 50), (0.25, 150), (0.5, 175)):
        result = v1_runtime.tick(_opening(second, minerals, mining=second > 0))
        command_tasks = {c.task_id for c in result.commands}
        for event in result.events:
            wire = event.to_dict()
            json.dumps(wire)  # must be JSON-serializable as-is
            assert wire["schema_version"] == 1
            assert wire["run_id"] == v1_runtime.run_id
            assert event.node_id in policy_ids
            sequences.append(event.sequence)
            if event.kind == "command":
                assert event.task_id in command_tasks
                assert event.action is not None
                tags = event.action["actor_tags"]
                assert isinstance(tags, list) and all(isinstance(tag, str) for tag in tags)
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)
    assert sequences[0] == 1 and v1_runtime.last_sequence == sequences[-1]
    state = v1_runtime.run_state(status="running", updated_at="2026-10-07T00:00:00Z").to_dict()
    assert state["policy_hash"] == load_policy().policy_hash
    json.dumps(state)
