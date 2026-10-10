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
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from bots.jev.v1 import load_policy as load_v1_policy
from bots.jev.v2 import load_policy as load_v2_policy
from sc2.ids.ability_id import AbilityId

import jev.bot
from jev.bot import JevController
from jev.contracts import Entity, Observation, Order, Point, Policy
from jev.operations import (
    BUILD_ABILITY,
    MAX_SUPPLY,
    MINERAL_COST,
    OPERATIONS,
    SUPPLY_PROVIDED,
    TERMINAL_TASK_STATUSES,
    TRAIN_ABILITY,
    PredicateOp,
    distance,
    planned_supply,
    validate_operation_args,
)
from jev.policy import PolicyError
from jev.runner import MatchOptions, run_match
from jev.runtime import NODE_BUDGET_REASON
from jev.sc2_adapter import GAME_LOOPS_PER_SECOND
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
