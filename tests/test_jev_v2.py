"""Jev v2, Phase J2 Step 214: the versioned opening, supply plan and grouped army.

* **Package.** ``bots.jev.v2`` is an independent policy package beside the frozen
  ``bots.jev.v1`` (plan D1): its own manifest, policy and thin entry point, both
  validating from the command line; v1's policy hash is pinned.
* **Policy.** The four-Gateway identity, the four-Zealot first launch and the
  coordinator's contract (parameters and the defense node it reads) are kept, and
  the lanes v2 does not change are v1's node for node.
* **Operations.** The new opt-in operations at the operation level: the supply plan
  (:func:`jev.operations.planned_supply`), recent task outcomes and assignments.
* **The opening through the production controller.** :func:`jev.runner.run_match`
  -> :class:`jev.bot.JevController` -> ``JevBot.on_step`` -> the SC2 adapter and
  :class:`jev.sc2_adapter.BotAIPort`, against the economy suite's burnysc2 world
  (``test_jev_economy._World``: real ``Unit`` objects, placement, training, supply
  and costs) plus mining income and Zealot movement. From one Nexus and twelve
  probes, v2 completes four Gateways, launches four Zealots together, never commits
  two Pylon orders at once, is never supply blocked after its first Pylon and sends
  later Zealots only as groups of four.

Grouped reinforcement, recovery, bounded build failures and the Pylon plan are
also exercised scenario by scenario in ``test_jev_army.py`` and
``test_jev_economy.py`` through the same production path.

Step 218 (limited tech and a mixed army): the graph's contract (a tech plan lane
before the expansion's, Stalker production within composition guards, combat lanes
of Zealots and Stalkers, air threats for anti-air units only), the new operation
arguments at the operation level (tech requirements, unpowered producers, planned
counts, affordability, capability-aware attack targets), and -- through the
production controller -- expansion and tech sharing the one macro slot without
ever double-spending. The Core-then-Stalker scenarios live in ``test_jev_macro.py``
and the mixed-army ones in ``test_jev_army.py``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from bots.jev.v1 import load_policy as load_v1_policy
from bots.jev.v2 import load_policy as load_v2_policy
from sc2.data import Result
from sc2.ids.ability_id import AbilityId

import jev.bot
from jev.bot import JevBot, JevController
from jev.contracts import Entity, Event, Observation, Order, Point, Policy
from jev.decision import ARMY_QUESTION, Answer, DecisionConfig, Question
from jev.operations import (
    BUILD_ABILITY,
    MAX_COMPOSITION_RATIO,
    MAX_SUPPLY,
    MINERAL_COST,
    OPERATIONS,
    SUPPLY_PROVIDED,
    TERMINAL_TASK_STATUSES,
    TRAIN_ABILITY,
    ActionOp,
    Binding,
    PredicateOp,
    distance,
    planned_supply,
    validate_operation_args,
)
from jev.plans import PlanStore, StrategySlot
from jev.policy import PolicyError
from jev.runner import DIAGNOSTICS_FILE, MatchLimits, MatchOptions, run_match
from jev.runtime import NODE_BUDGET_REASON, STRATEGY_DECISION_REASON
from jev.sc2_adapter import GAME_LOOPS_PER_SECOND
from jev.strategy import (
    ANSWER_WAIT_EXPIRED_REASON,
    STARVATION_SECONDS,
    HostedQuestions,
    StrategyBranch,
    answer_wait_seconds,
    strategy_branches,
)
from jev.telemetry import read_run_metadata, read_run_state
from test_jev_economy import (
    STEP_LOOPS,
    V2Watch,
    _Launcher,
    _sent,
    _World,
    _world,
)
from test_jev_runtime import (
    _build_policy,
    _gateway,
    _nexus,
    _node,
    _observation,
    _probe,
    _pylon,
    _runtime_for,
    _zealot,
)

#: The frozen v1 policy (plan D1): any change to bots/jev/v1/policy.json breaks this.
V1_POLICY_HASH = "510a72dfb167b7200123cbc8436c912f02d3b0fb5798657db3c62e3f5f8f68ee"
_REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# The package: frozen v1 beside an independent v2
# ---------------------------------------------------------------------------


def test_v1_policy_is_frozen() -> None:
    bundle = load_v1_policy()
    assert bundle.policy_hash == V1_POLICY_HASH
    assert (bundle.manifest.version, bundle.manifest.entrypoint) == (1, "bots.jev.v1")


def test_v2_is_an_independent_package_with_its_own_identity() -> None:
    bundle = load_v2_policy()
    manifest = bundle.manifest
    assert (manifest.family, manifest.version, manifest.entrypoint) == ("jev", 2, "bots.jev.v2")
    assert manifest.display_name == "v2.jev"
    assert (bundle.policy.family, bundle.policy.version) == ("jev", 2)
    assert Path(bundle.source).parts[-3:] == ("jev", "v2", "policy.json")
    assert bundle.policy_hash != V1_POLICY_HASH
    shipped = json.loads((_REPO / "bots" / "jev" / "v2" / "policy.json").read_text("utf-8"))
    assert bundle.policy.to_document() == shipped  # the packaged bytes are what loads


def test_v2_validates_candidates_against_its_own_manifest(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_bytes(load_v1_policy().policy_bytes)  # a version-1 document
    with pytest.raises(PolicyError, match="identity_mismatch"):
        load_v2_policy(candidate)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_both_entry_points_validate_from_the_command_line(version: str, tmp_path: Path) -> None:
    expected = (load_v1_policy if version == "v1" else load_v2_policy)().policy_hash
    proc = subprocess.run(  # an unrelated cwd: the policy must resolve from the package
        [sys.executable, "-m", f"bots.jev.{version}", "--validate-policy"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert f"jev policy valid: {version}.jev policy_hash={expected}" in proc.stdout


def test_both_entry_points_validate_from_an_installed_wheel(tmp_path: Path) -> None:
    """Build the real wheel, install it alone (no dependencies: validation is stdlib
    plus ``jev``), and validate both packaged versions from outside the repository."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to build the wheel")
    dist = tmp_path / "dist"
    build = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(dist), str(_REPO)],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    wheel = next(dist.glob("alpha4gate-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    for version in ("v1", "v2"):
        for name in ("__init__.py", "__main__.py", "manifest.json", "policy.json"):
            assert f"bots/jev/{version}/{name}" in names
    site = tmp_path / "site"
    install = subprocess.run(
        [uv, "pip", "install", "--no-deps", "--python", sys.executable, "--target", str(site)]
        + [str(wheel)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert install.returncode == 0, install.stderr
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    for version, load in (("v1", load_v1_policy), ("v2", load_v2_policy)):
        script = (  # -I -S below: only the stdlib plus the installed wheel on sys.path
            "import runpy, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            f"sys.argv = ['bots.jev.{version}', '--validate-policy']\n"
            f"runpy.run_module('bots.jev.{version}', run_name='__main__', alter_sys=True)\n"
        )
        proc = subprocess.run(
            [sys.executable, "-I", "-S", "-c", script, str(site)],
            cwd=outside,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        (summary,) = [line for line in proc.stdout.splitlines() if line.startswith("jev policy")]
        source = Path(summary.split("source=", 1)[1]).resolve()
        assert source == (site.resolve() / "bots" / "jev" / version / "policy.json")
        assert f"{version}.jev policy_hash={load().policy_hash}" in summary


# ---------------------------------------------------------------------------
# The policy: the hosted coordinator's contract
# ---------------------------------------------------------------------------


def test_v2_keeps_the_contract_the_hosted_coordinator_reads() -> None:
    """With ``--decision-provider typesafe`` jev.bot reads these parameters, the defense
    node's demotions and the mode-guarded branches; jev.decision.context mirrors the
    defense lane's threat filter, so offered choices match what the graph can do."""
    policy = load_v2_policy().policy
    parameters = policy.parameters
    assert isinstance(parameters["first_attack_zealots"], int)  # int(...) in JevController
    assert isinstance(parameters["defense_radius"], int | float)
    assert "army" in policy.roots  # the runtime reports decision facts on the army lane
    assert policy.node("army.defend.attack").kind == "action"  # demoted_targets(...) source
    home = {"point": "start_location", "distance": {"param": "defense_radius"}}
    assert dict(policy.node("army.defend.threat").args) == home  # ground units by default
    target = policy.node("army.defend.target").args
    assert target["filter"] == {"flying": False, "structure": False, "within": home}
    assert target["skip_demoted_by"] == "army.defend.attack"
    guards = {
        branch: policy.node(policy.node(branch).children[0])
        for branch in ("army.defend", "army.attack", "army.rally")
    }
    assert {b: (g.operation, g.args["mode"]) for b, g in guards.items()} == {
        "army.defend": ("army_mode_is", "defend"),
        "army.attack": ("army_mode_is", "attack"),
        "army.rally": ("army_mode_is", "regroup"),
    }


# ---------------------------------------------------------------------------
# The new operations, at the operation level
# ---------------------------------------------------------------------------


@dataclass
class _SupplyView:
    """The three RuntimeView facts planned_supply reads."""

    observation: Observation
    free: int
    builds: tuple[tuple[str, Point], ...] = ()

    def available_supply(self) -> int:
        return self.free

    def in_flight_builds(self) -> tuple[tuple[str, Point], ...]:
        return self.builds


def _building(tag: int, site: Point, kind: str = "Pylon") -> Entity:
    """A probe still carrying a build order for ``kind`` at ``site``."""
    return _probe(tag, (30.0, 26.0), (Order(BUILD_ABILITY[kind], site),))


_WARPING_PYLON = _pylon(4001, (40.0, 30.0), progress=0.5)
_GATES = (
    _gateway(1100, (26.5, 34.5)),  # ready and powered: 2 for its next Zealot
    _gateway(1101, (29.5, 34.5), orders=(Order(TRAIN_ABILITY["Zealot"]),)),  # busy: still 2
)


def _view(
    *,
    structures: tuple[Entity, ...] = (),
    units: tuple[Entity, ...] = (),
    builds: tuple[tuple[str, Point], ...] = (),
    supply: tuple[int, int] = (20, 23),
) -> _SupplyView:
    observation = _observation(
        10.0,
        supply_used=supply[0],
        supply_cap=supply[1],
        units=units,
        structures=(_nexus(), *structures),
    )
    return _SupplyView(observation, supply[1] - supply[0], builds)


def _plan(*, demand: bool = True, **world: Any) -> dict[str, object]:
    view = _view(**world)
    return dict(planned_supply(view, producer_demand=demand).facts())  # type: ignore[arg-type]


def test_planned_supply_counts_each_pending_supply_structure_once() -> None:
    assert _plan() == {
        "available": 3,
        "pending": 0,
        "planned_cap": 23,
        "demand": 1,
        "planned_free": 2,
    }  # the Nexus's next probe
    assert _plan(structures=_GATES)["demand"] == 5  # busy or idle: each one's next unit
    unpowered = dataclasses.replace(_gateway(1102, (60.0, 60.0)), powered=False)
    warping = dataclasses.replace(_gateway(1103, (32.5, 34.5)), build_progress=0.4, ready=False)
    assert _plan(structures=(unpowered, warping))["demand"] == 1  # neither can train yet
    assert _plan(demand=False)["planned_free"] == 3
    pylon = _plan(structures=(_WARPING_PYLON,))
    assert (pylon["pending"], pylon["planned_cap"], pylon["planned_free"]) == (8, 31, 10)
    ordered = _plan(builds=(("Pylon", (44.0, 30.0)), ("Gateway", (26.5, 37.5))))
    assert ordered["pending"] == 8  # a Gateway provides no supply
    same = _plan(builds=(("Pylon", (44.0, 30.0)),), units=(_building(2001, (44.0, 30.0)),))
    assert same["pending"] == 8  # the task's probe carries that very order: counted once
    late = _plan(units=(_building(2001, (44.0, 30.0)),))
    assert late["pending"] == 8  # a carried order no task accounts for still counts
    placed = _plan(structures=(_WARPING_PYLON,), units=(_building(2001, (40.0, 30.0)),))
    assert placed["pending"] == 8  # already placed: counted as warping in, not twice
    gate_order = _plan(units=(_building(2001, (26.5, 37.5), "Gateway"),))
    assert gate_order["pending"] == 0
    assert SUPPLY_PROVIDED == {"Nexus": 15, "Pylon": 8}


def test_planned_supply_never_plans_past_the_supply_ceiling() -> None:
    builds: tuple[tuple[str, Point], ...] = (("Pylon", (44.0, 30.0)),)
    near = _plan(supply=(190, 196), structures=(_WARPING_PYLON,), builds=builds)
    assert (near["pending"], near["planned_cap"]) == (16, MAX_SUPPLY)
    assert near["planned_free"] == 6 + 4 - 1  # only 4 of the 16 count below the ceiling
    assert jev.bot.SUPPLY_CEILING == MAX_SUPPLY  # the metrics read the same ceiling
    room = OPERATIONS["planned_supply_compare"]
    assert isinstance(room, PredicateOp)
    args = {"measure": "ceiling_room", "op": ">", "value": 0, "producer_demand": True}
    for supply, builds_, expected in (((190, 196), (), 4), ((190, 196), builds, 0)):
        outcome = room.evaluate(_view(supply=supply, builds=builds_), args)  # type: ignore[arg-type]
        assert (outcome.facts["actual"], outcome.ok) == (expected, expected > 0)


_KINDS = {
    "lane": "sequence",
    "lane.build": "action",
    "lane.pick": "select",
}


@pytest.mark.parametrize(
    ("operation", "args", "message"),
    [
        (
            "recent_task_count_compare",
            {"node": "lane.build", "statuses": ["pending"], "within": 30, "op": "<", "value": 3},
            "unknown value 'pending'",
        ),
        (
            "recent_task_count_compare",
            {"node": "lane.build", "statuses": ["failed"], "within": 0, "op": "<", "value": 3},
            "must be >= 1",
        ),
        (
            "recent_task_count_compare",
            {"node": "lane.pick", "statuses": ["failed"], "within": 30, "op": "<", "value": 3},
            "must reference an action node",
        ),
        (
            "planned_supply_compare",
            {"measure": "both", "op": "<=", "value": 4},
            "must be one of free, ceiling_room",
        ),
        (
            "select_entities",
            {"collection": "own_units", "limit": 4, "assigned_to": "lane.pick", "bind": "x"},
            "must reference an action node",
        ),
        (
            "select_entities",
            {"collection": "own_units", "limit": 4, "not_assigned_to": "lane.gone", "bind": "x"},
            "missing node",
        ),
    ],
)
def test_new_operation_arguments_are_validated(
    operation: str, args: dict[str, object], message: str
) -> None:
    issues = validate_operation_args(operation, args, parameters={}, node_kinds=_KINDS)  # type: ignore[arg-type]
    assert any(message in text for _, text in issues), issues


def test_terminal_statuses_are_the_non_active_task_statuses() -> None:
    assert TERMINAL_TASK_STATUSES == ("succeeded", "failed", "cancelled")


_TOWARD = {"from": "start_location", "toward": "map_center"}


def _gather_lane() -> list[dict[str, Any]]:
    """``lane``: gather every Zealot at the point 8 from the main (arrive within 4)."""
    return [
        _node("lane", "sequence", ["lane.point", "lane.units", "lane.move"]),
        _node("lane.point", "select", operation="select_point", **_TOWARD, distance=8, bind="p"),
        _node(
            "lane.units",
            "select",
            operation="select_entities",
            collection="own_units",
            filter={"types": ["Zealot"]},
            limit=10,
            bind="units",
        ),
        _node(
            "lane.move", "action", operation="move", units="$units", target="$p", arrive_within=4
        ),
    ]


def _assignment_policy() -> Policy:
    """``other`` sends the Zealots right at the point away first; ``lane`` gathers every
    Zealot at the point; ``probe`` reads the lane's assignments (turning tracking on)."""
    other = [
        _node("other", "sequence", ["other.point", "other.far", "other.near", "other.move"]),
        _node("other.point", "select", operation="select_point", **_TOWARD, distance=8, bind="q"),
        _node("other.far", "select", operation="select_point", **_TOWARD, distance=30, bind="far"),
        _node(
            "other.near",
            "select",
            operation="select_entities",
            collection="own_units",
            filter={"types": ["Zealot"], "within": {"point": "$q", "distance": 2.5}},
            limit=10,
            bind="near",
        ),
        _node(
            "other.move", "action", operation="move", units="$near", target="$far", arrive_within=1
        ),
    ]
    probe = [
        _node("probe", "sequence", ["probe.group"]),
        _node(
            "probe.group",
            "select",
            operation="select_entities",
            collection="own_units",
            filter={"types": ["Zealot"]},
            limit=10,
            assigned_to="lane.move",
            bind="group",
        ),
    ]
    return _build_policy(["other", "lane", "probe"], [*other, *_gather_lane(), *probe])


def test_assignments_follow_the_node_that_takes_charge_and_are_pruned() -> None:
    runtime = _runtime_for(_assignment_policy())
    at_point = _zealot(7001, (36.16, 39.5))  # 3.3 from the point: already gathered
    far = _zealot(7002, (60.0, 30.0))  # the lane creates a task for it
    held = _zealot(7003, (36.16, 37.0))  # gathered, but another node's task holds it
    first = runtime.tick(_observation(1.0, units=(at_point, far, held)))
    assert runtime.assigned_to("lane.move") == {7001, 7002}  # satisfied, or a new task
    assert runtime.assigned_to("other.move") == {7003}  # the holding node stays in charge
    (group,) = [e for e in first.events if e.node_id == "probe.group" and e.kind == "node"]
    assert group.facts["tags"] == ["7001", "7002"]
    runtime.tick(_observation(2.0, units=(far, held)))  # 7001 is gone
    assert runtime.assigned_to("lane.move") == {7002}  # pruned; its own task keeps 7002
    unread = _runtime_for(_build_policy(["lane"], _gather_lane()))
    unread.tick(_observation(1.0, units=(at_point, far)))
    assert unread.assigned_to("lane.move") == frozenset()  # nothing reads it: never kept


# ---------------------------------------------------------------------------
# The opening through the production controller
# ---------------------------------------------------------------------------

#: Minerals one harvesting probe brings in per game second (about 57 per minute).
_MINING_RATE = 0.95
#: A Zealot's speed, game units per game second.
_ZEALOT_SPEED = 3.15


class _OpeningWorld(_World):
    """The economy suite's world plus mining income and Zealot movement."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._income = 0.0

    def advance(self, loops: int = STEP_LOOPS) -> None:
        dt = loops / GAME_LOOPS_PER_SECOND
        mining = sum(
            1
            for body in self.own("Probe")
            if body.orders[:1] and body.orders[0][0] == AbilityId.HARVEST_GATHER_PROBE
        )
        self._income += mining * _MINING_RATE * dt
        self.minerals += int(self._income)
        self._income -= int(self._income)
        for body in self.own("Zealot"):
            ability, target, _ = body.orders[0] if body.orders else (None, None, 0.0)
            if ability not in (AbilityId.MOVE_MOVE, AbilityId.ATTACK_ATTACK):
                continue
            if not isinstance(target, tuple):
                continue
            gap = distance(body.position, target)
            if gap <= _ZEALOT_SPEED * dt:
                body.position, body.orders = target, []
            else:
                ratio = _ZEALOT_SPEED * dt / gap
                x, y = body.position
                body.position = (x + (target[0] - x) * ratio, y + (target[1] - y) * ratio)
        super().advance(loops)


@dataclass(frozen=True)
class _Opening:
    world: _World
    controller: JevController
    watch: V2Watch
    #: Game loop -> each own Zealot's position as that step began.
    zealots: dict[int, dict[int, Point]]
    blocked_after_first_pylon: float
    first_pylon_complete: float | None

    def command_node(self, loop: int, actor: int, ability: str) -> str:
        """The policy node of the command event that sent ``ability`` to ``actor``."""
        (event,) = [
            e
            for e in self.watch.events.values()
            if e.kind == "command"
            and e.game_loop == loop
            and e.action is not None
            and e.action["ability"] == ability
            and e.action["actor_tags"] == [str(actor)]
        ]
        return event.node_id


def _play_opening(steps: int) -> _Opening:
    """V2 from one Nexus, twelve mining probes and 50 minerals, through run_match."""
    world = _world(minerals=50, world_type=_OpeningWorld)
    watch = V2Watch()
    zealots: dict[int, dict[int, Point]] = {}
    blocked = [0.0]
    first_pylon: list[float] = []

    def observe(world: _World, controller: JevController, index: int) -> None:
        if index == 0:  # keep every tick's events, however many (Step 219's guards add some)
            tick = controller.runtime.tick

            def recording(observation: Observation) -> Any:
                result = tick(observation)
                watch.events.update((e.sequence, e) for e in result.events)
                return result

            controller.runtime.tick = recording  # type: ignore[method-assign]
        watch(world, controller, index)
        zealots[world.state.game_loop] = {z.tag: z.position for z in world.own("Zealot")}
        if not first_pylon and any(p.progress >= 1.0 for p in world.own("Pylon")):
            first_pylon.append(world.seconds)
        idle = [g for g in world.own("Gateway") if g.progress >= 1.0 and not g.orders]
        free = world.supply_cap - world.supply_used
        if first_pylon and idle and world.minerals >= 100 and free < 2:
            blocked[0] += STEP_LOOPS / GAME_LOOPS_PER_SECOND  # a Zealot could not fit

    launcher = _Launcher(world, steps, observe)
    with tempfile.TemporaryDirectory() as run_root:
        run_match(MatchOptions(), load_v2_policy(), launcher=launcher, run_root=Path(run_root))
    assert launcher.controller is not None
    watch.events.update((e.sequence, e) for e in launcher.controller.recent_events)  # last step
    first = first_pylon[0] if first_pylon else None
    return _Opening(world, launcher.controller, watch, zealots, blocked[0], first)


_OPENING_STEPS = 700  # 250 game seconds
_HOME_RADIUS = 20.0  # the v2 policy's defense_radius: "at home"
_START = (30.5, 30.5)


@pytest.fixture(scope="module")
def v2_opening() -> _Opening:
    return _play_opening(_OPENING_STEPS)


def _waves(world: _World) -> dict[int, set[int]]:
    """Zealots grouped by the game loop of their first ATTACK command."""
    first: dict[int, int] = {}
    for loop, command in world.sent:
        if command.ability == AbilityId.ATTACK:
            first.setdefault(command.unit.tag, loop)
    waves: dict[int, set[int]] = {}
    for tag, loop in first.items():
        waves.setdefault(loop, set()).add(tag)
    return waves


def test_v2_opening_completes_four_gateways_then_launches_four_zealots_together(
    v2_opening: _Opening,
) -> None:
    opening = v2_opening
    world, controller, watch = opening.world, opening.controller, opening.watch
    sequences = sorted(watch.events)
    assert sequences == list(range(1, len(sequences) + 1))  # the trace lost no event
    gateway_builds = _sent(world, "PROTOSSBUILD_GATEWAY")
    assert len(gateway_builds) == 4  # one command each: none failed, no fifth
    gateways = world.own("Gateway")
    assert len(gateways) == 4 and all(g.progress >= 1.0 for g in gateways)
    for loop, build in gateway_builds:
        node = opening.command_node(loop, build.unit.tag, "PROTOSSBUILD_GATEWAY")
        assert node == "construction.gateways.build"
    waves = _waves(world)
    launch_loop = min(waves)
    assert len(waves[launch_loop]) == 4  # the first launch: four Zealots on one tick
    assert set(opening.zealots[launch_loop]) == waves[launch_loop]  # every Zealot there was
    assert max(loop for loop, _ in gateway_builds) < launch_loop
    latches = watch.node_events("army.attack.launch.first_wave.latch")
    (latched,) = [e for e in latches if e.status == "success"]
    assert latched.game_loop == launch_loop
    assert world.overspent == []
    assert {c.ability for _, c in world.sent} <= {
        AbilityId.HARVEST_GATHER,
        AbilityId.PROTOSSBUILD_PYLON,
        AbilityId.PROTOSSBUILD_GATEWAY,
        AbilityId.NEXUSTRAIN_PROBE,
        AbilityId.GATEWAYTRAIN_ZEALOT,
        AbilityId.MOVE_MOVE,
        AbilityId.ATTACK,
    }  # no gas, no Nexus, no tech
    commands = [e for e in watch.events.values() if e.kind == "command"]
    assert len(commands) == len(world.sent)  # every command SC2 received is graph-attributed
    assert all(controller.runtime.policy.node(e.node_id).kind == "action" for e in commands)
    budget = [e for e in watch.events.values() if e.reason == NODE_BUDGET_REASON]
    assert budget == []  # six lanes, each fully evaluated every tick within the D2 budget


def test_v2_opening_is_never_supply_blocked_after_its_first_pylon(v2_opening: _Opening) -> None:
    """The whole opening's outcome of the supply plan (its parts -- the opening Pylon,
    no duplicate orders, successive Pylons, carried orders, recovery -- are owned by the
    v2 scenarios in test_jev_economy.py). V2Watch also held its one-Pylon-order
    invariant before every one of the 700 steps."""
    opening = v2_opening
    assert opening.first_pylon_complete is not None
    assert opening.blocked_after_first_pylon == 0.0  # D3: no avoidable supply stall
    summary = opening.controller.metrics.summary(end_game_seconds=opening.world.seconds)
    assert summary["supply_blocked_game_seconds"] == {"status": "measured", "value": 0.0}
    assert len(_sent(opening.world, "PROTOSSBUILD_PYLON")) >= 4  # the plan kept pace


def test_v2_opening_sends_later_zealots_only_as_groups_from_home(v2_opening: _Opening) -> None:
    opening = v2_opening
    waves = _waves(opening.world)
    launch = min(waves)
    later = {loop: members for loop, members in waves.items() if loop > launch}
    assert later  # at least one reinforcement group set out after the launch
    for loop, members in later.items():
        assert len(members) >= 4  # D3: never one by one
        for tag in members:  # each had gathered at home first
            assert distance(opening.zealots[loop][tag], _START) <= _HOME_RADIUS
    gathered = {
        e.action["actor_tags"][0]  # type: ignore[index]
        for e in opening.watch.events.values()
        if e.kind == "command" and e.node_id == "reserve.gather" and e.action is not None
    }
    assert gathered  # the reserve lane gathered reinforcements at the rally point
    assert all(int(str(tag)) not in waves[launch] for tag in gathered)  # never the group


def test_v2_opening_spends_on_gateways_as_soon_as_minerals_allow(v2_opening: _Opening) -> None:
    """From the first Gateway order until the fourth, the Gateway lane is never idle: it
    has an order in flight (one placement at a time) or holds the minerals it is short
    of. So the bank stays below the dearest single purchase on average, where frozen
    v1 (Gateways two to four only after four Zealots) averaged about 900 minerals on
    this world (Step 214 dev report)."""
    opening = v2_opening
    builds = [loop for loop, _ in _sent(opening.world, "PROTOSSBUILD_GATEWAY")]
    lane = [
        e
        for e in opening.watch.node_events("construction.gateways")
        if builds[0] < e.game_loop < builds[-1]
    ]
    assert lane
    one_at_a_time = {"construction.gateways.none_ordered", "construction.gateways.no_order_carried"}
    for event in lane:
        if event.status == "failure":  # only while the previous order is still in flight
            assert event.facts["child"] in one_at_a_time, event.reason
        else:
            assert event.status == "running"  # placing, or holding a reservation
    summary = opening.controller.metrics.summary(end_game_seconds=opening.world.seconds)
    bank = summary["mean_mineral_bank"]
    assert isinstance(bank, dict) and bank["status"] == "measured"
    assert float(str(bank["value"])) < MINERAL_COST["Gateway"]


# ---------------------------------------------------------------------------
# Step 218: the graph's contract for tech and the mixed army
# ---------------------------------------------------------------------------

_COMBAT = ["Zealot", "Stalker"]


def test_v2_macro_lanes_hold_before_production_and_the_expansion_after_construction() -> None:
    """The lane-order contract only (the ordering behaviour itself is proven by the
    production scenarios): both macro lanes hold before production spends, after the
    supply and construction lanes that may preempt them, and the tech lane runs before
    the expansion lane (D2's preference when both could start; fairness after aborts is
    the macro slot's own turn rule, whatever the order)."""
    roots = load_v2_policy().policy.roots
    assert roots.index("construction") < roots.index("tech") < roots.index("expansion")
    assert roots.index("expansion") < roots.index("production")


def test_v2_stalkers_train_first_within_the_composition_guards() -> None:
    policy = load_v2_policy().policy
    assert policy.node("production").children[:2] == ("production.stalkers", "production.zealots")
    core = policy.node("production.stalkers.core").args["filter"]
    assert core == {"types": ["CyberneticsCore"], "ready": True, "powered": True}
    gas = policy.node("production.stalkers.gas")
    assert (gas.operation, gas.args["item"], list(gas.args["resources"])) == (
        "affordable",
        "Stalker",
        ["vespene"],
    )
    train = policy.node("production.stalkers.train").args
    assert (train["unit"], train["ratio_of"], train["hold_reservation"]) == (
        "Stalker",
        "Zealot",
        True,
    )
    assert train["max_ratio"] == {"param": "stalkers_per_zealot"}
    assert train["max_count"] == {"param": "stalker_cap"}
    assert train["powered_prerequisite"] is True  # the operation enforces the Core's power
    assert policy.parameters["stalkers_per_zealot"] == 1  # D2 fallback: balanced
    assert policy.parameters["stalker_cap"] == 16
    # No policy node spells an SC2 price: gas gating reads the central cost table.
    assert not [n.id for n in policy.nodes if n.args.get("resource") == "vespene"]


def test_v2_combat_lanes_take_zealots_and_stalkers_and_air_only_for_anti_air() -> None:
    policy = load_v2_policy().policy
    combat = [
        n
        for n in policy.nodes
        if n.args.get("collection") == "own_units"
        and {"Zealot", "Stalker"} & set(n.args.get("filter", {}).get("types", []))
    ]
    first_wave = "army.attack.launch.first_wave.ready"
    for node in combat:
        types = list(node.args["filter"]["types"])
        expected = ["Zealot"] if node.id == first_wave else _COMBAT  # D3: four Zealots
        assert types == expected, node.id
    assert len(combat) == 10  # every army, reserve and expansion count of combat units
    anti_air = policy.node("army.defend.air.units").args["filter"]
    assert anti_air == {"types": _COMBAT, "can_attack_air": True}
    for node_id in ("army.defend.air.main", "army.defend.air.natural.target"):
        assert policy.node(node_id).args["filter"]["flying"] is True
    attack = policy.node("army.defend.air.attack").args
    assert (attack["units"], attack["target"]) == ("$anti_air", "$air_threat")
    assert policy.node("army.defend.engage").children == ("army.defend.ground", "army.defend.air")


# ---------------------------------------------------------------------------
# Step 218: the operations, at the operation level
# ---------------------------------------------------------------------------


def _core(*, progress: float = 1.0, powered: bool = True) -> Entity:
    return Entity(
        1400,
        "CyberneticsCore",
        (34.5, 39.5),
        550.0,
        build_progress=progress,
        is_structure=True,
        ready=progress >= 1.0,
        powered=powered,
    )


_GATE_SITES = ((26.5, 34.5), (29.5, 34.5), (24.0, 37.5), (21.5, 34.5))


def _gates(*, powered: bool = True) -> tuple[Entity, ...]:
    return tuple(
        dataclasses.replace(_gateway(1100 + i, site), powered=powered)
        for i, site in enumerate(_GATE_SITES)
    )


def _op_view(
    *,
    structures: tuple[Entity, ...],
    units: tuple[Entity, ...] = (),
    minerals: int = 1000,
    vespene: int = 1000,
    trains: dict[str, int] | None = None,
    busy: frozenset[int] = frozenset(),
    enemies: tuple[Entity, ...] = (),
    remembered: tuple[Entity, ...] = (),
    **bindings: Binding,
) -> Any:
    from test_jev_plans import _View

    observation = dataclasses.replace(
        _observation(
            10.0,
            minerals=minerals,
            supply_used=20,
            supply_cap=100,
            units=units,
            structures=(_nexus(), *structures),
            enemies=enemies,
            remembered=remembered,
        ),
        vespene=vespene,
    )
    return _View(observation, bindings=dict(bindings), trains=dict(trains or {}), busy=busy)


def _plan_of(operation: str, view: Any, **args: Any) -> Any:
    spec = OPERATIONS[operation]
    assert isinstance(spec, ActionOp)
    from jev.operations import resolve_args

    return spec.plan(view, resolve_args(operation, args, {}))


def _train_stalkers(view: Any, **guards: Any) -> Any:
    return _plan_of("train", view, unit="Stalker", producers="$p", **guards)


def _producers(entities: tuple[Entity, ...]) -> Binding:
    return Binding("units", entities=entities)


def test_train_needs_the_live_tech_requirement_and_a_powered_producer() -> None:
    gates = _gates()
    for structures in ((), (_core(progress=0.5),)):  # no Core, or one still warping in
        plan = _train_stalkers(_op_view(structures=(*gates, *structures), p=_producers(gates)))
        assert plan.intents == () and plan.code == "prerequisite_missing"
        assert {reason for _, reason in plan.blocked} == {
            "Stalker requires a ready CyberneticsCore"
        }
        assert plan.facts["requires"] == "CyberneticsCore"
    # SC2's requirement is build progress only: an unpowered finished Core is enough here
    # (v2's graph is stricter by its own condition, production.stalkers.core).
    ready = _train_stalkers(
        _op_view(structures=(*gates, _core(powered=False)), p=_producers(gates))
    )
    assert len(ready.intents) == 4 and ready.code is None
    (intent, *_) = ready.intents
    assert (intent.ability, intent.minerals, intent.vespene, intent.supply) == (
        "GATEWAYTRAIN_STALKER",
        125,
        50,
        2,
    )
    dark = _gates(powered=False)
    plan = _train_stalkers(_op_view(structures=(*dark, _core()), p=_producers(dark)))
    assert plan.intents == () and {r for _, r in plan.blocked} == {"producer unpowered"}


def test_powered_prerequisite_makes_the_train_refuse_an_unpowered_core() -> None:
    """The opt-in, stricter prerequisite v2's Stalker node uses: the operation itself
    refuses an unpowered Core (SC2 would not), whatever a selector let through."""
    gates = _gates()
    unpowered = _op_view(structures=(*gates, _core(powered=False)), p=_producers(gates))
    strict = _train_stalkers(unpowered, powered_prerequisite=True)
    assert strict.intents == () and strict.code == "prerequisite_missing"
    assert {r for _, r in strict.blocked} == {"Stalker requires a ready, powered CyberneticsCore"}
    assert len(_train_stalkers(unpowered).intents) == 4  # the default: SC2's own rule
    powered = _op_view(structures=(*gates, _core()), p=_producers(gates))
    assert len(_train_stalkers(powered, powered_prerequisite=True).intents) == 4


def test_composition_guards_count_planned_units_and_never_overshoot_in_one_tick() -> None:
    """Planned Zealots: 2 standing + 1 queued + 1 unconfirmed task = 4; planned
    Stalkers: 1 standing + 1 unconfirmed = 2. A 1:1 ratio leaves room for 2 more of
    the four idle Gateways; a cap of 3 leaves room for 1. A Gateway already held by a
    task gets its (deduplicated) intent without using the room."""
    gates = _gates()
    queued = dataclasses.replace(
        _gateway(1104, (32.5, 34.5), orders=(Order(TRAIN_ABILITY["Zealot"]),)), powered=True
    )
    units = (_zealot(6000, (40.0, 40.0)), _zealot(6001, (40.0, 41.0)))
    stalker = Entity(6100, "Stalker", (40.0, 42.0), 80.0)
    view = _op_view(
        structures=(*gates, queued, _core()),
        units=(*units, stalker),
        trains={"Zealot": 1, "Stalker": 1},
        p=_producers(gates),
    )
    plan = _train_stalkers(view, ratio_of="Zealot", max_ratio=1.0)
    assert [i.actor_tag for i in plan.intents] == [1100, 1101]
    assert [tag for tag, _ in plan.blocked] == [1102, 1103]
    assert all(reason.startswith("composition guard") for _, reason in plan.blocked)
    facts = plan.facts
    assert (facts["planned"], facts["planned_other"], facts["composition_bound"]) == (2, 4, 4)
    capped = _train_stalkers(view, ratio_of="Zealot", max_ratio=1.0, max_count=3)
    assert [i.actor_tag for i in capped.intents] == [1100]
    assert capped.facts["composition_bound"] == 3
    held = _train_stalkers(
        dataclasses.replace(view, busy=frozenset({1100})), ratio_of="Zealot", max_ratio=1.0
    )
    assert [i.actor_tag for i in held.intents] == [1100, 1101, 1102]  # 1100 uses no room
    stalker_heavy = _train_stalkers(view, ratio_of="Zealot", max_ratio=2.0)
    assert len(stalker_heavy.intents) == 4  # D2's 1:2: room for 6 more
    zealot_heavy = _train_stalkers(view, ratio_of="Zealot", max_ratio=0.5)
    assert zealot_heavy.intents == ()  # 2:1: 4 Zealots allow 2 Stalkers, both planned


def test_the_runtime_counts_unconfirmed_train_tasks_toward_the_mix() -> None:
    """Through the runtime: one Zealot allows one Stalker. The first tick's Stalker is
    not confirmed on the next tick (SC2 shows no queue yet), and it still counts --
    a second Stalker is never ordered for the same Zealot."""
    lane = [
        _node("lane", "sequence", ["lane.gates", "lane.train"]),
        _node(
            "lane.gates",
            "select",
            operation="select_entities",
            collection="own_structures",
            filter={"types": ["Gateway"], "ready": True, "idle": True, "powered": True},
            limit=4,
            bind="g",
        ),
        _node(
            "lane.train",
            "action",
            operation="train",
            unit="Stalker",
            producers="$g",
            ratio_of="Zealot",
            max_ratio=1,
        ),
    ]
    runtime = _runtime_for(_build_policy(["lane"], lane))
    world = {
        "minerals": 1000,
        "supply_cap": 100,
        "units": (_zealot(6000, (40.0, 40.0)),),
        "structures": (_nexus(), *_gates(), _core()),
    }
    first = runtime.tick(dataclasses.replace(_observation(1.0, **world), vespene=1000))
    second = runtime.tick(dataclasses.replace(_observation(1.5, **world), vespene=1000))
    assert [c.actor_tags for c in first.commands] == [(1100,)]
    assert second.commands == ()  # the unconfirmed Stalker still fills the one slot
    (event,) = [e for e in second.events if e.kind == "node" and e.node_id == "lane.train"]
    assert event.facts["planned"] == 1 and event.facts["planned_other"] == 1


def test_affordable_reads_the_central_cost_tables_after_reservations() -> None:
    spec = OPERATIONS["affordable"]
    assert isinstance(spec, PredicateOp)

    def check(item: str, resources: list[str], **view: Any) -> Any:
        return spec.evaluate(
            _op_view(structures=(), **view), {"item": item, "resources": resources}
        )

    short = check("Stalker", ["vespene"], vespene=49)
    assert (short.ok, short.code, short.facts["short"]) == (False, "budget_short", ["vespene"])
    assert short.facts["vespene"] == {"need": 50, "available": 49}
    assert check("Stalker", ["vespene"], vespene=50).ok
    both = check("Stalker", ["minerals", "vespene", "supply"], minerals=124, vespene=50)
    assert both.facts["short"] == ["minerals"]
    assert check("CyberneticsCore", ["minerals", "supply"], minerals=150).ok  # no supply cost


def test_build_refuses_a_core_without_a_finished_gateway() -> None:
    site = Binding("points", points=((34.5, 39.5),))
    probe = _probe(2000, (31.0, 31.0))
    worker = Binding("units", entities=(probe,))
    args = {"structure": "CyberneticsCore", "worker": "$w", "site": "$s"}
    warping = dataclasses.replace(_gateway(1100, (26.5, 34.5)), ready=False, build_progress=0.5)
    for gates in ((), (warping,)):
        view = _op_view(structures=gates, units=(probe,), w=worker, s=site)
        plan = _plan_of("build", view, **args)
        assert plan.intents == () and plan.code == "prerequisite_missing"
        assert plan.failure_reason == "CyberneticsCore requires a ready Gateway"
    finished = (_gateway(1100, (26.5, 34.5)),)
    ready = _plan_of(
        "build", _op_view(structures=finished, units=(probe,), w=worker, s=site), **args
    )
    (intent,) = ready.intents
    assert (intent.ability, intent.minerals, intent.vespene) == (
        "PROTOSSBUILD_CYBERNETICSCORE",
        150,
        0,
    )


def _flyer(tag: int, position: Point) -> Entity:
    return Entity(tag, "Banshee", position, 140.0, is_flying=True, can_attack_ground=True)


def test_attack_never_assigns_a_flying_target_to_an_actor_that_cannot_hit_air() -> None:
    zealot = dataclasses.replace(_zealot(6000, (40.0, 40.0)), can_attack_ground=True)
    stalker = Entity(
        6100, "Stalker", (40.0, 41.0), 80.0, can_attack_ground=True, can_attack_air=True
    )
    army = Binding("units", entities=(zealot, stalker))
    raider = _flyer(9000, (45.0, 45.0))
    args = {"units": "$u", "target": "$t", "arrive_within": 4}
    view = _op_view(
        structures=(),
        units=(zealot, stalker),
        enemies=(raider,),
        u=army,
        t=Binding("units", entities=(raider,)),
    )
    plan = _plan_of("attack", view, **args)
    assert [(i.actor_tag, i.target) for i in plan.intents] == [(6100, 9000)]
    assert plan.blocked == ((6000, "Zealot 6000 cannot attack air target 9000"),)
    assert plan.facts["cannot_attack_air"] == 1
    marine = Entity(9001, "Marine", (45.0, 45.0), 45.0)
    ground = _op_view(
        structures=(),
        units=(zealot, stalker),
        enemies=(marine,),
        u=army,
        t=Binding("units", entities=(marine,)),
    )
    plan = _plan_of("attack", ground, **args)
    assert [i.actor_tag for i in plan.intents] == [6000, 6100] and plan.blocked == ()
    assert "cannot_attack_air" not in plan.facts  # unchanged facts for ground targets
    lifted = dataclasses.replace(
        _flyer(9002, (60.0, 60.0)), type_name="Barracks", is_structure=True
    )
    remembered = _op_view(
        structures=(),
        units=(zealot, stalker),
        remembered=(lifted,),
        u=army,
        t=Binding("units", entities=(lifted,)),
    )
    plan = _plan_of("attack", remembered, **args)
    assert {i.target for i in plan.intents} == {(60.0, 60.0)}  # attack-move where it was seen
    assert len(plan.intents) == 2  # a point is no unit assignment


@pytest.mark.parametrize(
    ("operation", "args", "message"),
    [
        ("train", {"unit": "Stalker", "producers": "$p", "ratio_of": "Zealot"}, "go together"),
        ("train", {"unit": "Stalker", "producers": "$p", "max_ratio": 1}, "go together"),
        (
            "train",
            {"unit": "Stalker", "producers": "$p", "ratio_of": "Stalker", "max_ratio": 1},
            "another unit",
        ),
        (
            "train",
            {
                "unit": "Stalker",
                "producers": "$p",
                "ratio_of": "Zealot",
                "max_ratio": MAX_COMPOSITION_RATIO + 1,
            },
            "must be <=",
        ),
        ("train", {"unit": "Sentry", "producers": "$p"}, "must be one of"),
        (
            "train",
            {"unit": "Zealot", "producers": "$p", "powered_prerequisite": True},
            "powered_prerequisite",
        ),
        ("train", {"unit": "Stalker", "producers": "$p", "powered_prerequisite": 1}, "boolean"),
        ("affordable", {"item": "Sentry", "resources": ["minerals"]}, "must be one of"),
        ("affordable", {"item": "Stalker", "resources": []}, "non-empty list"),
        ("affordable", {"item": "Stalker", "resources": ["gold"]}, "unknown value"),
    ],
)
def test_step_218_operation_arguments_are_validated(
    operation: str, args: dict[str, object], message: str
) -> None:
    issues = validate_operation_args(operation, args, parameters={}, node_kinds={})  # type: ignore[arg-type]
    assert any(message in text for _, text in issues), issues


def test_the_runtime_rejects_a_non_bool_weapon_flag() -> None:
    runtime = _runtime_for(load_v2_policy().policy)
    bad = dataclasses.replace(_zealot(6000, (40.0, 40.0)), can_attack_air="yes")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"own_units\[0\]\.can_attack_air must be a bool"):
        runtime.tick(_observation(1.0, units=(bad,)))


def test_a_lost_acknowledged_structure_aborts_by_its_plan_kind() -> None:
    """The tech plan's lost Core is ``structure_lost``; an expansion's lost Nexus stays
    ``nexus_lost`` (Step 217's code, unchanged)."""
    for kind, code in (("tech", "structure_lost"), ("expand", "nexus_lost")):
        store = PlanStore("0123456789ab4def8123456789abcdef")
        record, _ = store.start(f"{kind}.start", kind, kind, 1.0)  # type: ignore[arg-type]
        assert record is not None
        store.build_committed(f"{kind}.start", f"{kind}.build", "t:1", 150, 0)
        store.build_acknowledged(f"{kind}.start", f"{kind}.build", 3.0)
        store.build_failed(f"{kind}.start", "lost", True, "structure destroyed", 5.0)
        latest = store.record(f"{kind}.start")
        assert latest is not None and (latest.status, latest.reason_code) == ("aborted", code)


# ---------------------------------------------------------------------------
# Step 218: expansion and tech share the macro slot and never double-spend
# ---------------------------------------------------------------------------


def test_expansion_and_tech_share_the_macro_slot_and_never_double_spend() -> None:
    """A launched one-base army ready to expand but without a Core. The tech plan
    takes the macro slot first (D2: the Core prerequisite before the expansion; its
    lane runs first). Every tick until it completes the slot refuses the expansion,
    visibly -- through the Core's construction -- and on the tick the Core is finished
    the tech lane completes its plan before the expansion lane opens its own. At no
    step are two macro plans open, their holds never exceed the bank, and SC2 never
    sees an order it cannot pay for."""
    import test_jev_plans as expansion_suite

    world = expansion_suite._world(core=False)
    seen: list[tuple[int, int, int, int]] = []
    funded: set[str] = set()
    ready: dict[int, bool] = {}

    def watch(world: Any, controller: JevController, index: int) -> None:
        ready[world.state.game_loop] = any(b.progress >= 1.0 for b in world.own("CyberneticsCore"))
        open_plans = [r for r in controller.runtime.plans() if r.is_open]
        held = sum(r.reserved_minerals for r in open_plans)
        seen.append((world.state.game_loop, len(open_plans), held, world.minerals))
        for record in open_plans:
            if record.plan_id not in funded:  # each plan's price, as it opens
                funded.add(record.plan_id)
                world.minerals += 150 if record.kind == "tech" else 400

    run = expansion_suite._expand(world, 230, watch)
    assert all(open_plans <= 1 for _, open_plans, _, _ in seen)  # one macro plan at a time
    assert all(held <= bank for _, _, held, bank in seen)
    plans = run.controller.runtime.plans() if run.controller is not None else ()
    tech, expand = (next(r for r in plans if r.kind == kind) for kind in ("tech", "expand"))
    assert tech.plan_id.endswith(":plan:1") and tech.status == "completed"
    assert expand.plan_id.endswith(":plan:2") and expand.status == "completed"
    assert tech.finished_game_seconds is not None
    assert expand.started_game_seconds >= tech.finished_game_seconds
    refusals = [
        e
        for e in run.events
        if e.kind == "node" and e.node_id == "expansion.start.go" and e.status == "failure"
    ]
    assert refusals and all("never a duplicate plan" in e.reason for e in refusals)
    core_ready = min(loop for loop, ok in ready.items() if ok)
    assert max(e.game_loop for e in refusals) < core_ready  # refused while tech was open
    assert expand.started_game_seconds * GAME_LOOPS_PER_SECOND == pytest.approx(core_ready)
    nexus_holds = [
        e.game_seconds
        for e in run.events
        if e.kind == "diagnostic"
        and e.facts.get("plan_kind") == "expand"
        and isinstance(e.facts.get("reserved_minerals"), int)
        and int(str(e.facts["reserved_minerals"])) > 0
    ]
    assert nexus_holds and min(nexus_holds) >= tech.finished_game_seconds
    assert world.overspent == [] and world.sc2_refusals == []
    assert len(world.own("Nexus")) == 2 and len(world.own("CyberneticsCore")) == 1


# ---------------------------------------------------------------------------
# Step 219: hosted strategic choices through the production controller
# ---------------------------------------------------------------------------
#
# Every scenario runs the launched one-base world of the expansion suite (Nexus,
# Pylon, four powered Gateways, an Assimilator, eight ready Zealots at home; without
# a Core unless noted) through the production controller -- JevController ->
# JevBot.on_step -> HostedQuestions -> the SC2 adapter and BotAIPort -- and, through
# run_match, the production runner with --decision-provider typesafe, whose
# TypesafeProvider talks HTTP to a scripted httpx transport (no network). Wall time
# passes with game time and the event loop runs between steps, as in a realtime
# match: a pending request progresses without any step awaiting it.

_V2_BRANCHES = strategy_branches(load_v2_policy().policy)
_STEP_SECONDS = STEP_LOOPS / GAME_LOOPS_PER_SECOND
_LOOP_TURNS = 40  # event-loop turns between steps: an immediate HTTP reply completes


class _Wall:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _HostedLauncher(_Launcher):
    """The economy suite's launcher, plus what a realtime match gives a request
    (``game_per_wall`` above 1: the game outruns the wall clock, as without
    ``--realtime``)."""

    def __init__(
        self, world: Any, steps: int, hook: Any, wall: _Wall, game_per_wall: float = 1.0
    ) -> None:
        super().__init__(world, steps, hook)
        self.wall = wall
        self.wall_step = _STEP_SECONDS / game_per_wall

    async def _run(self, bot: JevBot, controller: JevController) -> None:
        for index in range(self.steps):
            if self.before_step is not None:
                self.before_step(self.world, controller, index)
            await bot.on_step(index)
            if self.world.left:
                break
            self.world.advance()
            self.wall.now += self.wall_step
            for _ in range(_LOOP_TURNS):
                await asyncio.sleep(0)
        await bot.on_end(Result.Defeat if self.world.left else Result.Victory)


def _service_body(question_id: str, choice: str, confidence: float, options: list[str]) -> Any:
    rest = (1.0 - confidence) / max(1, len(options) - 1)
    probabilities = {option: rest for option in options}
    probabilities[choice] = confidence
    return {
        "answers": {
            question_id: {
                "type": "choice",
                "choice": choice,
                "confidence": confidence,
                "probabilities": probabilities,
            }
        },
        "model": "jev-test-model",
        "usage": {"input_tokens": 50, "output_tokens": 5},
    }


@dataclass
class _Hosted:
    run: Any
    outcome: Any
    requests: list[dict[str, Any]]
    run_dir: Path

    def asked(self, question_id: str) -> list[tuple[int, list[str]]]:
        """``(request id, offered options)`` of every request of ``question_id``."""
        return [
            (number, list(sent["questions"][question_id]["criteria"]))
            for number, sent in enumerate(self.requests, start=1)
            if question_id in sent["questions"]
        ]


def _hosted_match(
    world: Any,
    steps: int,
    answer: Any,
    monkeypatch: pytest.MonkeyPatch,
    run_root: Path,
    *hooks: Any,
    max_requests: int = 450,
    game_per_wall: float = 1.0,
) -> _Hosted:
    """Play ``world`` through run_match with the Typesafe provider over a scripted
    HTTP transport: ``answer(question_id, options)`` returns ``(choice, confidence)``
    or an ``httpx.Response`` (a service failure)."""
    from test_jev_macro import _Run

    requests: list[dict[str, Any]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        requests.append(sent)
        ((question_id, question),) = sent["questions"].items()
        options = list(question["criteria"])
        reply = answer(question_id, options)
        if isinstance(reply, httpx.Response):
            return reply
        choice, confidence = reply
        return httpx.Response(200, json=_service_body(question_id, choice, confidence, options))

    transport = httpx.MockTransport(respond)
    original_client = httpx.AsyncClient

    def mock_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    run = _Run(world)

    def every(world: Any, controller: JevController, index: int) -> None:
        for hook in hooks:
            hook(world, controller, index)

    wall = _Wall()
    launcher = _HostedLauncher(world, steps, run.hook(every), wall, game_per_wall)
    options = MatchOptions(decision_provider="typesafe", decision_max_requests=max_requests)
    outcome = run_match(options, load_v2_policy(), launcher=launcher, run_root=run_root, clock=wall)
    (run_dir,) = [path for path in run_root.iterdir() if path.is_dir()]
    return _Hosted(run, outcome, requests, run_dir)


def _strategy_events(run: Any) -> list[Event]:
    return [e for e in run.events if e.reason == STRATEGY_DECISION_REASON]


def _node_successes(run: Any, node_id: str, after: int = -1) -> list[Event]:
    return [
        e
        for e in run.events
        if e.kind == "node"
        and e.node_id == node_id
        and e.status == "success"
        and e.game_loop > after
    ]


@pytest.mark.parametrize("branch", _V2_BRANCHES, ids=lambda b: b.choice)
def test_each_strategic_choice_selects_its_graph_branch(
    branch: StrategyBranch, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With every choice executable (no Core yet, the natural free, the army launched),
    the service is offered exactly the graph's choices; the one it answers opens its
    branch -- a macro plan attributed to that request, or the army posture -- and no
    other strategic branch opens."""
    import test_jev_plans as expansion_suite

    def answer(question_id: str, options: list[str]) -> tuple[str, float]:
        if question_id == "strategy":
            return branch.choice, 0.9
        return options[0], 0.2  # army intent below the floor: the local graph decides

    hosted = _hosted_match(expansion_suite._world(core=False), 40, answer, monkeypatch, tmp_path)
    run = hosted.run
    assert hosted.outcome.exit_code == 0
    ((request_id, offered), *_) = hosted.asked("strategy")
    assert offered == [b.choice for b in _V2_BRANCHES]  # the graph's choices, all executable
    accepted = [e for e in _strategy_events(run) if e.facts["source"] == "typesafe"]
    assert accepted and accepted[0].facts["choice"] == branch.choice
    assert accepted[0].facts["in_force_request_id"] == request_id
    applied_at = accepted[0].game_loop
    # The chosen branch's guard passes -- attributed to this request -- and no other
    # choice's guard ever does.
    passed = _node_successes(run, branch.guard, after=applied_at - 1)
    assert passed
    assert {(e.facts["source"], e.facts["request_id"]) for e in passed} == {
        ("typesafe", request_id)
    }
    for other in _V2_BRANCHES:
        if other.choice != branch.choice:
            assert _node_successes(run, other.guard) == [], other.choice
    assert run.controller is not None
    runtime = run.controller.runtime
    plans = runtime.plans()
    if branch.slot == "macro":
        assert branch.start is not None
        (plan,) = plans  # exactly the chosen plan, and it is the request's
        assert plan.node_id == branch.start and plan.kind == branch.choice
        assert (plan.decided_by, plan.decision_request_id) == ("typesafe", request_id)
        started = [e for e in _strategy_events(run) if e.facts.get("started_plan")]
        assert started and started[-1].facts["started_plan"]["plan_id"] == plan.plan_id
        return
    assert plans == ()  # an army posture opens no macro plan, though both were offered
    # The posture holds the macro slot back on the answer's own request id.
    held_back = [
        e
        for e in run.events
        if e.kind == "node" and e.node_id == "tech.start.chosen" and e.game_loop >= applied_at
    ]
    assert held_back and {e.facts["request_id"] for e in held_back} == {request_id}
    if branch.choice == "recover":  # the whole army regroups: not the few-units trigger
        assert [e for e in run.commands("army.recover.move") if e.game_loop >= applied_at]
        assert _node_successes(run, "army.recover.few") == []
        return
    # Pressure: the attack's posture switches from the local path to the hosted guard
    # at this request -- the local graph alone would have kept the local path.
    posture = [
        e
        for e in run.events
        if e.kind == "node" and e.node_id == "army.attack.posture" and e.status == "success"
    ]
    before = {str(e.facts["child"]) for e in posture if e.game_loop < applied_at}
    after = {str(e.facts["child"]) for e in posture if e.game_loop >= applied_at}
    assert before == {"army.attack.local"} and after == {"army.attack.pressure"}
    assert _node_successes(run, "army.attack.local", after=applied_at - 1) == []
    attacking = [t for t in runtime.active_tasks() if t.node_id == "army.attack.go"]
    assert attacking or [e for e in run.commands("army.attack.go") if e.game_loop >= applied_at]


def test_emergency_defense_runs_while_a_request_is_pending() -> None:
    """The army acts on an accepted attack intent (the first answer is stale: the launch
    changed the army's state); the next request then hangs. A raid at home expires the
    attack intent at once (its threats changed) and the local graph defends -- every
    step of it while the request is still pending."""
    import test_jev_macro
    import test_jev_plans as expansion_suite

    gate = asyncio.Event()
    calls: list[str] = []

    class Service:
        async def decide(
            self, state: Any, choices: tuple[str, ...], question: Question = ARMY_QUESTION
        ) -> Answer:
            calls.append(question.question_id)
            if calls.count("army_mode") > 2 or question.question_id == "strategy":
                await gate.wait()  # never released: this request stays pending
            probabilities: dict[str, Any] = dict.fromkeys(choices, 0.05)
            probabilities["attack"] = 0.9
            return Answer("attack", 0.9, probabilities, "jev-test-model", 9, 1)

    world = expansion_suite._world()
    wall = _Wall()
    bundle = load_v2_policy()
    questions = HostedQuestions(Service(), DecisionConfig(timeout=200), bundle.policy, clock=wall)
    controller = JevController(
        bundle,
        run_id=uuid.uuid4().hex,
        limits=MatchLimits(900, 1800),
        clock=wall,
        decisions=questions,
    )
    run = test_jev_macro._Run(world)
    pending: dict[int, bool] = {}
    raid_loop: list[int] = []

    def raid(world: Any, controller: JevController, index: int) -> None:
        pending[world.state.game_loop] = questions.task is not None and not questions.task.done()
        if index == 30:
            raid_loop.append(world.state.game_loop)
            world.enemy("Marine", (33.0, 36.0))

    launcher = _HostedLauncher(world, 50, run.hook(raid), wall)
    launcher.play(controller, world, MatchOptions())
    (loop,) = raid_loop
    army_facts = [e for e in run.events if e.reason == "Army decision source"]
    assert any(e.facts["source"] == "typesafe" for e in army_facts if e.game_loop < loop)
    defended = [e for e in run.commands("army.defend.attack") if e.game_loop >= loop]
    assert defended  # the local graph fought the raid
    assert all(pending[e.game_loop] for e in defended)  # each while the request pended
    assert calls == ["army_mode", "army_mode", "strategy"] and not gate.is_set()
    expired = [e for e in army_facts if e.game_loop >= loop]
    assert expired and expired[0].facts["reason"] == "expired_or_changed_state"
    assert expired[0].facts["choice"] is None


@pytest.mark.parametrize("failure", ["http_503", "low_confidence"])
def test_an_unusable_strategy_answer_leaves_the_plan_to_the_attributed_fallback(
    failure: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed or low-confidence strategy answer never opens its plan: the local
    fallback (the graph's own order, tech first) decides, the plan records that, and
    the archived run state says why."""
    import test_jev_plans as expansion_suite

    def answer(question_id: str, options: list[str]) -> Any:
        if question_id == "army_mode":
            return options[0], 0.2
        if failure == "http_503":
            return httpx.Response(503)
        return "expand", 0.3  # below the 0.5 floor

    hosted = _hosted_match(expansion_suite._world(core=False), 40, answer, monkeypatch, tmp_path)
    run = hosted.run
    assert hosted.asked("strategy")
    assert run.controller is not None
    plans = run.controller.runtime.plans()
    assert plans and all(p.decided_by == "local_fallback" for p in plans)
    assert all(p.decision_request_id is None for p in plans)
    assert [p.kind for p in plans] == ["tech"]  # the fallback's order, never the answer
    metadata = read_run_metadata(hosted.run_dir)
    assert metadata is not None
    state = read_run_state(hosted.run_dir, metadata)
    archived = [e for e in state.recent_events if e.reason == STRATEGY_DECISION_REASON]
    assert archived and archived[-1].facts["source"] == "local_fallback"
    assert archived[-1].facts["reason"] == failure
    assert archived[-1].facts["choice"] is None


def test_strategy_and_army_share_the_runner_request_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import test_jev_plans as expansion_suite

    def answer(question_id: str, options: list[str]) -> tuple[str, float]:
        return ("pressure" if question_id == "strategy" else options[0]), 0.9

    hosted = _hosted_match(
        expansion_suite._world(), 120, answer, monkeypatch, tmp_path, max_requests=5
    )
    asked = [next(iter(sent["questions"])) for sent in hosted.requests]
    assert len(asked) == 5 and set(asked) == {"army_mode", "strategy"}  # one cap for both
    diagnostics = json.loads((hosted.run_dir / DIAGNOSTICS_FILE).read_text("utf-8"))
    decisions = diagnostics["metrics"]["decisions"]
    assert (decisions["provider"], decisions["calls"], decisions["max_requests"]) == (
        "typesafe",
        5,
        5,
    )
    assert decisions["answers"] == 5 and decisions["failures"] == {}
    last = _strategy_events(hosted.run)[-1].facts
    assert last["calls"] == 5 and last["max_requests"] == 5


def test_a_delayed_strategy_reply_after_its_prerequisite_changed_opens_nothing() -> None:
    """A tech reply held back while the Core appears (tech left the executable set) is
    refused on arrival: the reply can never open a plan its request did not hold."""
    import test_jev_macro
    import test_jev_plans as expansion_suite

    gate = asyncio.Event()
    strategy_calls: list[tuple[str, ...]] = []

    class Service:
        async def decide(
            self, state: Any, choices: tuple[str, ...], question: Question = ARMY_QUESTION
        ) -> Answer:
            probabilities: dict[str, Any] = dict.fromkeys(choices, 0.05)
            if question.question_id == "army_mode":
                probabilities[choices[0]] = 0.2
                return Answer(choices[0], 0.2, probabilities, "jev-test-model", 9, 1)
            strategy_calls.append(choices)
            if len(strategy_calls) == 1:
                await gate.wait()
            probabilities["tech"] = 0.9
            return Answer("tech", 0.9, probabilities, "jev-test-model", 9, 1)

    world = expansion_suite._world(core=False)
    wall = _Wall()
    bundle = load_v2_policy()
    questions = HostedQuestions(Service(), DecisionConfig(timeout=200), bundle.policy, clock=wall)
    controller = JevController(
        bundle,
        run_id=uuid.uuid4().hex,
        limits=MatchLimits(900, 1800),
        clock=wall,
        decisions=questions,
    )
    run = test_jev_macro._Run(world)

    def core_appears(world: Any, controller: JevController, index: int) -> None:
        if strategy_calls and not gate.is_set():
            world.add("CyberneticsCore", expansion_suite.CORE_SITE)  # a finished Core
            world.refresh()
            gate.set()  # the held tech reply arrives after the change

    launcher = _HostedLauncher(world, 40, run.hook(core_appears), wall)
    launcher.play(controller, world, MatchOptions())
    assert strategy_calls and "tech" in strategy_calls[0]
    refused = [e for e in _strategy_events(run) if e.facts["answer_request_id"] is not None]
    assert refused and refused[0].facts["answer"] == "tech"
    assert refused[0].facts["reason"] == "stale_answer" and refused[0].facts["choice"] is None
    plans = controller.runtime.plans()
    assert controller.runtime.plan("tech.start.go") is None  # never opened by the old reply
    assert all(p.decided_by != "typesafe" for p in plans)


def test_v2_scripted_runs_leave_strategy_to_the_attributed_local_graph() -> None:
    """The attribution panel's scripted provider (plan D6): no service call, every
    strategy slot left to the graph's own conditions, and the plans say so."""
    import test_jev_plans as expansion_suite

    world = expansion_suite._world(core=False)
    run = expansion_suite._expand(world, 30)
    assert run.controller is not None
    plans = run.controller.runtime.plans()
    assert plans and {(p.kind, p.decided_by, p.decision_request_id) for p in plans} == {
        ("tech", "local_graph", None)
    }
    sources = {e.facts["source"] for e in _strategy_events(run)}
    assert sources == {"local_graph"}
    summary = run.controller.metrics.summary(end_game_seconds=world.seconds)
    decisions = summary["decisions"]
    assert isinstance(decisions, dict)
    assert (decisions["provider"], decisions["calls"]) == ("scripted", 0)


def test_the_strategy_guards_read_the_slot_the_controller_set() -> None:
    """At the operation level: strategy_is passes only for the hosted choice of its own
    slot; strategy_local only while the slot is left to the local fallback; a hosted
    slot without a choice holds every branch of the slot back."""
    runtime = _runtime_for(load_v2_policy().policy)
    view_slots = {
        "local": StrategySlot(),
        "awaiting": StrategySlot(local=False, source="typesafe"),
        "tech": StrategySlot(local=False, choice="tech", request_id=7, source="typesafe"),
    }
    is_tech = OPERATIONS["strategy_is"]
    local = OPERATIONS["strategy_local"]
    for name, held in view_slots.items():
        runtime.set_strategy({"macro": held}, {})
        view = _StrategyView(runtime)
        tech = is_tech.evaluate(view, {"choice": "tech"})  # type: ignore[union-attr]
        expand = is_tech.evaluate(view, {"choice": "expand"})  # type: ignore[union-attr]
        fallback = local.evaluate(view, {"slot": "macro"})  # type: ignore[union-attr]
        assert (tech.ok, expand.ok, fallback.ok) == {
            "local": (False, False, True),
            "awaiting": (False, False, False),
            "tech": (True, False, False),
        }[name]
        assert tech.facts["request_id"] == held.request_id
    with pytest.raises(ValueError, match="does not belong"):
        runtime.set_strategy(
            {"army": StrategySlot(local=False, choice="tech", source="typesafe")}, {}
        )
    with pytest.raises(ValueError):
        StrategySlot(local=True, choice="tech")


class _StrategyView:
    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def strategy(self, slot: str) -> StrategySlot:
        return self._runtime.strategy(slot)  # type: ignore[no-any-return]


def test_the_store_refusal_is_the_check_start_applies_and_changes_nothing() -> None:
    """The coordinator offers a macro choice only when PlanStore.refusal is None: the
    same check start() applies, read without opening or yielding anything."""
    store = PlanStore(uuid.uuid4().hex, kinds=("expand", "tech"))
    assert store.refusal("tech", 0.0) is None and store.records() == ()
    record, _ = store.start(
        "tech.start.go", "tech", "tech", 0.0, decided_by="typesafe", decision_request_id=3
    )
    assert record is not None
    assert (record.decided_by, record.decision_request_id) == ("typesafe", 3)
    assert record.facts()["decision_request_id"] == 3
    refusal = store.refusal("expand", 1.0)
    assert refusal is not None and "never a duplicate plan" in refusal
    assert store.start("expansion.start.go", "expansion", "expand", 1.0) == (None, refusal)
    assert len(store.records()) == 1


# ---------------------------------------------------------------------------
# Step 219 iteration 2: bounded waits and the local precedence rules, end to end
# ---------------------------------------------------------------------------


def test_a_flickering_threat_cannot_hold_the_macro_slot_past_its_bound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Marine appears at home on every other step, so the strategy state (its macro
    options and the home threat) changes on every poll and never settles. The macro
    slot waits for the hosted answer, but not past answer_wait_seconds() (here real time):
    the question is still asked, its answer is stale, and the local fallback -- diagnosed
    in the archived state -- opens the tech plan."""
    import test_jev_plans as expansion_suite

    marine: list[int] = []

    def flicker(world: Any, controller: JevController, index: int) -> None:
        if marine:
            world.kill(marine.pop())
        else:
            marine.append(world.enemy("Marine", (33.0, 36.0)))

    def answer(question_id: str, options: list[str]) -> tuple[str, float]:
        if question_id == "strategy":  # tech while offered: every reply meets a new state
            return ("tech" if "tech" in options else options[0]), 0.9
        return options[0], 0.2

    hosted = _hosted_match(
        expansion_suite._world(core=False), 100, answer, monkeypatch, tmp_path, flicker
    )
    events = _strategy_events(hosted.run)
    waiting = next(e for e in events if e.facts["source"] == "awaiting_answer")
    released = next(
        e
        for e in events
        if e.game_seconds > waiting.game_seconds and e.facts["source"] == "local_fallback"
    )
    assert released.game_seconds - waiting.game_seconds <= answer_wait_seconds(DecisionConfig())
    assert released.facts["reason"] in ("stale_answer", ANSWER_WAIT_EXPIRED_REASON)
    assert hosted.asked("strategy")  # a churning question is still asked
    assert hosted.run.controller is not None
    (plan,) = hosted.run.controller.runtime.plans()
    assert (plan.kind, plan.decided_by, plan.decision_request_id) == (
        "tech",
        "local_fallback",
        None,
    )
    assert plan.started_game_seconds >= released.game_seconds
    metadata = read_run_metadata(hosted.run_dir)
    assert metadata is not None
    archived = [
        e
        for e in read_run_state(hosted.run_dir, metadata).recent_events
        if e.reason == STRATEGY_DECISION_REASON
    ]
    assert archived and archived[-1].facts["reason"] in (
        "stale_answer",
        ANSWER_WAIT_EXPIRED_REASON,
    )


def test_a_hosted_pressure_answer_never_overrides_the_few_units_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pressure holds the army posture; when six of the eight Zealots die, the local
    few-units recovery (D3's safety reaction) still pulls the rest home while pressure
    stays in force."""
    import test_jev_plans as expansion_suite

    held: dict[int, StrategySlot] = {}
    killed: list[int] = []

    def losses(world: Any, controller: JevController, index: int) -> None:
        held[world.state.game_loop] = controller.runtime.strategy("army")
        if index == 30:
            killed.append(world.state.game_loop)
            for tag in expansion_suite.ZEALOTS[:6]:
                world.kill(tag)

    def answer(question_id: str, options: list[str]) -> tuple[str, float]:
        return ("pressure", 0.9) if question_id == "strategy" else (options[0], 0.2)

    hosted = _hosted_match(expansion_suite._world(), 50, answer, monkeypatch, tmp_path, losses)
    run = hosted.run
    (loss,) = killed
    assert held[loss].choice == "pressure"  # the posture was in force before the losses
    recovering = _node_successes(run, "army.recover.few", after=loss - 1)
    assert recovering  # the local safety trigger fired despite the hosted posture
    for event in recovering:  # ... on ticks where pressure still held the army slot
        assert held[event.game_loop].choice == "pressure"
    assert _node_successes(run, "army.recover.chosen") == []  # not a recover answer
    assert [e for e in run.commands("army.recover.move") if e.game_loop >= loss]


@pytest.mark.parametrize(
    ("provider", "zealots", "expands"),
    [
        ("scripted", 8, "local_graph"),  # the local trigger: eight ready combat units
        ("scripted", 5, None),  # below the trigger: the local graph does not expand
        ("pressure", 8, None),  # a hosted answer holds the slot: the trigger never applies
        ("expand", 5, "typesafe"),  # a hosted expand needs no eight-unit trigger
    ],
)
def test_the_eight_unit_expansion_trigger_applies_only_without_a_hosted_macro_answer(
    provider: str,
    zealots: int,
    expands: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Step 217's local trigger (eight ready combat units) decides the expansion only
    while no hosted answer holds the macro slot: a hosted pressure answer keeps it from
    opening with eight units, and a hosted expand opens it with five."""
    import test_jev_plans as expansion_suite

    world = expansion_suite._world()  # the Core stands: only the expansion is a macro plan

    def thin(world: Any, controller: JevController, index: int) -> None:
        if index == 0:
            for tag in expansion_suite.ZEALOTS[zealots:]:
                world.kill(tag)

    if provider == "scripted":
        run = expansion_suite._expand(world, 40, thin)
    else:

        def answer(question_id: str, options: list[str]) -> tuple[str, float]:
            return (provider, 0.9) if question_id == "strategy" else (options[0], 0.2)

        run = _hosted_match(world, 40, answer, monkeypatch, tmp_path, thin).run
    assert run.controller is not None
    plans = run.controller.runtime.plans()
    trigger = _node_successes(run, "expansion.start.army")
    if expands is None:
        assert plans == ()
    else:
        (plan,) = plans
        assert (plan.kind, plan.decided_by) == ("expand", expands)
    if provider == "scripted":
        assert bool(trigger) == (zealots >= 8)
    else:  # hosted: the local trigger is never even evaluated
        assert [e for e in run.events if e.node_id == "expansion.start.army"] == []
        assert _node_successes(run, "expansion.start.local") == []


def test_without_realtime_a_churning_strategy_question_is_asked_before_its_wait_expires(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The game runs ten times faster than the wall clock (no --realtime) while the
    strategy state flickers. The answer-wait bound and the cadence share the wall
    clock, so the starvation promotion dispatches the strategy question long before the
    bound -- however much game time passes -- and the slot never falls back on
    answer_wait_expired."""
    import test_jev_plans as expansion_suite

    marine: list[int] = []

    def flicker(world: Any, controller: JevController, index: int) -> None:
        if marine:
            world.kill(marine.pop())
        else:
            marine.append(world.enemy("Marine", (33.0, 36.0)))

    def answer(question_id: str, options: list[str]) -> tuple[str, float]:
        return (options[0], 0.9) if question_id == "strategy" else (options[0], 0.2)

    game_per_wall = 10.0
    config = DecisionConfig()
    steps = round((STARVATION_SECONDS + 3 * config.interval) * game_per_wall / _STEP_SECONDS)
    hosted = _hosted_match(
        expansion_suite._world(core=False),
        steps,
        answer,
        monkeypatch,
        tmp_path,
        flicker,
        game_per_wall=game_per_wall,
    )
    events = _strategy_events(hosted.run)
    waiting = next(e for e in events if e.facts["source"] == "awaiting_answer")
    dispatched = next(e for e in events if e.facts["pending"])
    expired = [e for e in events if e.facts["reason"] == ANSWER_WAIT_EXPIRED_REASON]
    assert hosted.asked("strategy")
    assert expired == []  # the question went out first: the backstop never fired
    wall_waited = (dispatched.game_seconds - waiting.game_seconds) / game_per_wall
    assert wall_waited <= STARVATION_SECONDS + config.interval + 0.5
    # More game time passed than the whole wall bound: a game-time bound would have fired.
    assert dispatched.game_seconds - waiting.game_seconds > answer_wait_seconds(config)
