"""The Jev live graph in a real browser (plan J2 Step 221, D5): opt-in fixtures.

What the operator sees of a Jev run -- coloured lanes, the observed edge activity, the
plan/stage/blocker panel and node-attributed decision sources -- is driven here end
to end in Chromium, against runs written by the production runner
(:func:`jev.runner.run_match`) and the real recorder:

* a hosted **v2** match (the packaged ``bots.jev.v2`` policy) played by a stand-in
  game launcher through the real ``JevBot``/``JevController``/adapter against the
  scripted burnysc2 worlds of the v2 suites, with the Typesafe provider answering
  over an in-process ``httpx.MockTransport`` (nothing leaves the machine; no SC2, no
  hosted service). It is paced on the wall clock so the page sees it live, then held
  mid-match (no heartbeat: stale) and released to finish (final). Offline is the
  browser losing the dashboard API (its run requests aborted);
* an old **v1** archive: the frozen v1 policy through the same runner, which records
  none of the v2 facts.

The harness is :mod:`test_jev_launch`'s (``TestRealBrowser``): a production build of
this checkout's frontend, the real Jev router served by uvicorn on free loopback
ports (never the operator's 8765/3000), Chromium through Playwright. Like it, these
tests are opt-in::

    $env:JEV_BROWSER_TESTS = '1'
    uv run --with playwright pytest tests/test_jev_live_graph.py -p no:cacheprovider

and with ``$env:JEV_BROWSER_EVIDENCE_DIR = '<dir>'`` each test also saves a full-page
screenshot per phase plus ``timeline.jsonl`` there. This file lives beside
``test_jev_launch.py`` (and imports its harness rather than copying it) because those
are the D7 launch tests; these are the Step 221 graph fixtures.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from bots.jev.v1 import load_policy as load_v1_policy
from bots.jev.v2 import load_policy as load_v2_policy
from sc2.data import Result

import test_jev_launch as harness
from jev.bot import JevBot, JevController
from jev.runner import MatchOptions, run_match
from jev.sc2_adapter import BotAIPort
from test_jev_launch import _Evidence, _pump_until, _serving

# The real-browser harness fixtures (module scoped): a production build of this
# checkout's frontend and Chromium, both opt-in exactly as in ``test_jev_launch``.
built_dashboard = harness.built_dashboard
chromium = harness.chromium

#: Wall seconds between two game steps: slow enough for the page to watch it live.
PACE = 0.12
#: Wall seconds the browser waits for a state it must reach.
WAIT_MS = 30_000
#: A safety cap on a paced match's steps (each test stops its match itself).
MAX_STEPS = 1500


class _SteppedGame:
    """A :class:`jev.runner.MatchLauncher` playing a scripted burnysc2 world through the
    real ``JevBot`` at a wall-clock ``pace`` (what a game process does). Setting
    :attr:`hold` makes it stop stepping -- its run stops heartbeating -- until
    :attr:`release`; setting :attr:`stop` ends the match (a win) after the step."""

    def __init__(
        self,
        world: Any,
        steps: int,
        *,
        pace: float = PACE,
        wall: Any = None,
        wall_step: float = 0.0,
    ) -> None:
        self.world = world
        self.steps = steps
        self.pace = pace
        self.wall = wall
        self.wall_step = wall_step
        self.hold = threading.Event()
        self.held = threading.Event()
        self.release = threading.Event()
        self.stop = threading.Event()
        self.step = 0

    def prepare(self, options: MatchOptions) -> object:
        return self.world

    def play(self, controller: JevController, setup: object, options: MatchOptions) -> Any:
        bot = JevBot(controller)
        controller.attach(self.world, BotAIPort(self.world))  # what on_start binds
        asyncio.run(self._run(bot, controller))
        return controller.result

    async def _run(self, bot: JevBot, controller: JevController) -> None:
        for index in range(self.steps):
            if self.hold.is_set() and not self.release.is_set():
                self.held.set()
                while not self.release.is_set():
                    await asyncio.sleep(0.05)
            if self.stop.is_set():
                break
            await bot.on_step(index)
            self.step = index
            if self.world.left:
                break
            self.world.advance()
            if self.wall is not None:
                self.wall.now += self.wall_step
            for _ in range(40):  # an immediate scripted service reply completes
                await asyncio.sleep(0)
            if self.pace:
                await asyncio.sleep(self.pace)
        await bot.on_end(Result.Defeat if self.world.left else Result.Victory)


def _hosted_v2_game(monkeypatch: pytest.MonkeyPatch) -> tuple[_SteppedGame, MatchOptions, Any]:
    """The v2 suites' launched one-base world with an enemy at the natural: Typesafe
    (scripted, in process) chooses to expand and then to delay at the threatened site,
    so the expansion plan waits there -- attributed to its requests -- while the
    economy, scouting, production and army lanes run beside it."""
    import test_jev_plans as plans_suite
    from test_jev_v2 import _STEP_SECONDS, _service_body, _Wall

    world = plans_suite._world()
    world.enemy("Marine", (plans_suite.NATURAL[0] + 3.0, plans_suite.NATURAL[1] + 4.0))

    def answer(question_id: str, options: list[str]) -> tuple[str, float]:
        if question_id == "strategy":
            return ("expand", 0.9) if "expand" in options else (options[0], 0.2)
        if question_id == "expansion_response":
            return ("delay", 0.9) if "delay" in options else (options[0], 0.9)
        return options[0], 0.2  # below the confidence floor: the local fallback decides

    async def respond(request: httpx.Request) -> httpx.Response:
        ((question_id, question),) = json.loads(request.content)["questions"].items()
        options = list(question["criteria"])
        choice, confidence = answer(question_id, options)
        return httpx.Response(200, json=_service_body(question_id, choice, confidence, options))

    transport = httpx.MockTransport(respond)
    original_client = httpx.AsyncClient

    def scripted_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport  # never the network
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", scripted_client)
    monkeypatch.setenv("TYPESAFE_API_KEY", "live-graph-test-key")  # never a real credential
    wall = _Wall()
    game = _SteppedGame(world, MAX_STEPS, wall=wall, wall_step=_STEP_SECONDS)
    return game, MatchOptions(decision_provider="typesafe"), wall


def _start(
    run_root: Path, game: _SteppedGame, options: MatchOptions, bundle: Any, clock: Any = None
) -> tuple[threading.Thread, list[str], list[Any]]:
    """Play one match through the production runner in a thread (the game process)."""
    recorded: list[str] = []
    outcome: list[Any] = []

    def play() -> None:
        kwargs: dict[str, Any] = {} if clock is None else {"clock": clock}
        try:
            outcome.append(
                run_match(
                    options,
                    bundle,
                    run_root=run_root,
                    launcher=game,
                    on_recorded=recorded.append,
                    **kwargs,
                )
            )
        except BaseException as exc:  # surfaced by the test's own assertions
            outcome.append(exc)

    thread = threading.Thread(target=play, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not recorded:
        assert time.monotonic() < deadline, "the run was never recorded"
        time.sleep(0.02)
    return thread, recorded, outcome


def _lanes(page: Any) -> dict[str, tuple[int, int]]:
    """Each branch card's visible (active, waiting) node counts."""
    counts: dict[str, tuple[int, int]] = {}
    for chip in page.get_by_test_id("jev-graph-lane").all():
        lane = chip.get_attribute("data-lane")
        label = chip.locator(".jev-branch-card-counts").inner_text()
        match = re.search(r"(\d+) active.*?(\d+) waiting", label)
        assert match is not None, f"Missing branch counts for {lane}: {label!r}"
        counts[lane] = (int(match[1]), int(match[2]))
    return counts


def _busy_lanes(page: Any) -> list[str]:
    return [lane for lane, (active, waiting) in _lanes(page).items() if active + waiting > 0]


def _activity(page: Any) -> list[dict[str, Any]]:
    """The activity edges drawn (data attributes and lane colour class)."""
    result: list[dict[str, Any]] = page.evaluate(
        """() => Array.from(document.querySelectorAll('[data-testid="jev-graph-activity"] path'))
             .map((path) => ({
               edge: path.dataset.edge,
               kind: path.dataset.activity,
               animated: path.dataset.animated === "true",
               lane: Array.from(path.classList).find((c) => c.startsWith("jev-lane-")),
               animation: getComputedStyle(path).animationName,
             }))"""
    )
    return result


def _policy_edges(bundle: Any) -> set[str]:
    return {f"{node.id}>{child}" for node in bundle.policy.nodes for child in node.children}


@pytest.mark.usefixtures("built_dashboard", "chromium")
class TestLiveGraphInBrowser:
    """Simultaneous lanes; live, offline, stale and final; reduced motion; an old v1 run."""

    def test_simultaneous_lanes_then_offline_stale_and_final_in_one_v2_match(
        self,
        built_dashboard: Path,
        chromium: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from playwright.sync_api import expect

        evidence = _Evidence("g1")
        run_root = tmp_path / "runs"
        bundle = load_v2_policy()
        game, options, wall = _hosted_v2_game(monkeypatch)
        serving = _serving(run_root, dist=built_dashboard, access_log=evidence.server_log())
        with serving as server:
            thread, recorded, outcome = _start(run_root, game, options, bundle, clock=wall)
            (run_id,) = recorded
            context = chromium.new_context(viewport={"width": 1440, "height": 1100})
            page = context.new_page()
            try:
                page.goto(f"{server.base}/?tab=jev&run={run_id}")
                expect(page.get_by_test_id("jev-run-id")).to_have_text(run_id)
                expect(page.get_by_test_id("jev-live-badge")).to_be_visible(timeout=WAIT_MS)
                # Live: several lanes at once, and the expansion plan waiting at its blocker.
                _pump_until(page, lambda: len(_busy_lanes(page)) >= 3, timeout=30)
                # The compact overview shows all branches; cards toggle them independently.
                roots = page.locator('[role="treeitem"][aria-level="1"]')
                expect(roots).to_have_count(len(bundle.policy.roots))
                expect(page.get_by_role("button", name="Nodes", exact=True)).to_have_attribute(
                    "aria-pressed", "true"
                )
                page.get_by_test_id("jev-full-graph").click()
                expect(roots).to_have_count(len(bundle.policy.roots))
                waiting = page.locator('[data-testid="jev-plan-row"][data-plan-status="waiting"]')
                expect(waiting).to_have_count(1, timeout=WAIT_MS)
                expect(waiting.get_by_test_id("jev-plan-blocker")).to_contain_text(
                    "site_threatened"
                )
                expect(waiting.get_by_test_id("jev-plan-decided-by")).to_contain_text(
                    "Typesafe (request"
                )
                # The slot summary that follows each plan summary never blanks these.
                holds = waiting.get_by_test_id("jev-plan-holds")
                expect(holds).to_contain_text("deadline")
                expect(holds).to_contain_text("holds")
                page.wait_for_timeout(2500)  # two more game-second summaries later
                expect(holds).to_contain_text("deadline")
                expect(page.get_by_test_id("jev-plans-evidence")).to_have_text("Live evidence")
                _pump_until(
                    page,
                    lambda: (
                        len({e["lane"] for e in _activity(page) if e["kind"] == "current"}) >= 2
                    ),
                )
                live_edges = _activity(page)
                busy = _busy_lanes(page)
                # Only the policy's own edges, and only those the evidence recorded.
                assert {e["edge"] for e in live_edges} <= _policy_edges(bundle)
                assert any(e["animated"] and e["animation"] == "jev-flow" for e in live_edges)
                expect(page.get_by_test_id("jev-graph")).to_have_attribute(
                    "data-reduced-motion", "false"
                )
                page.get_by_role("button", name="Fit").click()  # every lane in view
                expansion = page.locator(
                    '[data-testid="jev-decision-row"][data-question="expansion_response"]'
                )
                expect(expansion).to_have_attribute("data-source", "typesafe", timeout=WAIT_MS)
                evidence.shot(
                    page,
                    "01-live-simultaneous-lanes",
                    run_id=run_id,
                    busy_lanes=busy,
                    edges=len(live_edges),
                    step=game.step,
                )

                # Node-attributed source: the plan's stage, shown in the graph.
                waiting.get_by_test_id("jev-plan-stage").click()
                expect(page.get_by_test_id("jev-node-id")).to_have_text("expansion.site_safe.wait")
                expect(roots).to_have_count(len(bundle.policy.roots))
                expansion_card = page.locator('.jev-branch-card[data-lane="expansion"]')
                expect(expansion_card).to_have_attribute("aria-pressed", "true")
                expect(page.get_by_test_id("jev-node-sources")).to_contain_text("Typesafe (request")
                stage = page.locator('[role="treeitem"][aria-label^="expansion.site_safe.wait,"]')
                expect(stage).to_have_attribute("data-source", "hosted")
                # Follow activity: off by default; on, it never changes the selection.
                follow = page.get_by_test_id("jev-follow")
                expect(follow).not_to_be_checked()
                follow.check()
                expect(page.get_by_test_id("jev-follow-status")).to_contain_text("on: showing")
                expect(page.get_by_test_id("jev-node-id")).to_have_text("expansion.site_safe.wait")
                page.wait_for_timeout(1500)  # another live poll must not steal selection
                expect(expansion_card).to_have_attribute("aria-pressed", "true")
                expect(page.get_by_test_id("jev-node-id")).to_have_text("expansion.site_safe.wait")
                evidence.shot(page, "02-follow-and-stage-source", step=game.step)

                # Spatial views preserve the actual run's evidence and selected node.
                for mode, layout in (("Radial", "radial"), ("Dots 3D", "dots3d")):
                    page.get_by_role("button", name=mode, exact=True).click()
                    expect(page.get_by_test_id("jev-graph")).to_have_attribute(
                        "data-layout", layout
                    )
                    expect(page.get_by_test_id("jev-node-id")).to_have_text(
                        "expansion.site_safe.wait"
                    )
                    assert page.locator('[role="treeitem"][data-live="true"]').count() > 0
                    evidence.shot(page, f"02-{layout}", step=game.step)
                page.get_by_role("button", name="Nodes", exact=True).click()
                page.get_by_role("button", name="Tree", exact=True).click()

                # Offline: the page cannot reach the run's state; nothing animates.
                page.route(f"**/api/jev/runs/{run_id}", lambda route: route.abort())
                expect(page.get_by_test_id("jev-offline-badge")).to_be_visible(timeout=WAIT_MS)
                expect(page.get_by_test_id("jev-live-badge")).to_have_count(0)
                assert not [e for e in _activity(page) if e["animated"]]
                expect(page.get_by_test_id("jev-plans-evidence")).to_have_text(
                    "Last known evidence"
                )
                expect(page.get_by_test_id("jev-follow-status")).to_have_text(
                    "paused: this state is not live"
                )
                evidence.shot(page, "03-offline", step=game.step)
                page.unroute(f"**/api/jev/runs/{run_id}")
                expect(page.get_by_test_id("jev-live-badge")).to_be_visible(timeout=WAIT_MS)

                # Stale: the game process holds mid-match and stops heartbeating.
                game.hold.set()
                assert game.held.wait(30), "the game never held"
                expect(page.get_by_test_id("jev-stale-badge")).to_be_visible(timeout=WAIT_MS)
                stale_edges = _activity(page)
                assert stale_edges and not [e for e in stale_edges if e["animated"]]
                assert {e["animation"] for e in stale_edges} == {"none"}
                expect(page.get_by_test_id("jev-graph-active-lanes")).to_contain_text(
                    "(last known)"
                )
                evidence.shot(page, "04-stale", step=game.step)

                # Final: released, the match ends and its result is shown, still not animated.
                game.stop.set()
                game.release.set()
                expect(page.get_by_test_id("jev-run-result")).to_have_text(
                    "Result: win", timeout=WAIT_MS
                )
                expect(page.get_by_test_id("jev-run-status")).to_have_text("Status: finished")
                assert not [e for e in _activity(page) if e["animated"]]
                expect(page.get_by_test_id("jev-plans-evidence")).to_have_text(
                    "Last known evidence"
                )
                expect(page.get_by_test_id("jev-graph-active-lanes")).to_contain_text(
                    "when the run ended"
                )
                evidence.shot(page, "05-final", step=game.step)
            finally:
                game.stop.set()
                game.release.set()
                thread.join(60)
                context.close()
        assert outcome and getattr(outcome[0], "status", None) == "finished"

    def test_reduced_motion_stops_every_animation_of_a_live_run(
        self,
        built_dashboard: Path,
        chromium: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from playwright.sync_api import expect

        evidence = _Evidence("g2")
        run_root = tmp_path / "runs"
        game, options, wall = _hosted_v2_game(monkeypatch)
        serving = _serving(run_root, dist=built_dashboard, access_log=evidence.server_log())
        with serving as server:
            thread, recorded, _ = _start(run_root, game, options, load_v2_policy(), clock=wall)
            (run_id,) = recorded
            motion = chromium.new_context(viewport={"width": 1440, "height": 1100})
            still = chromium.new_context(
                viewport={"width": 1440, "height": 1100}, reduced_motion="reduce"
            )
            try:
                pages = {"motion": motion.new_page(), "reduce": still.new_page()}
                for page in pages.values():
                    page.goto(f"{server.base}/?tab=jev&run={run_id}")
                    expect(page.get_by_test_id("jev-live-badge")).to_be_visible(timeout=WAIT_MS)
                    page.get_by_test_id("jev-full-graph").click()
                    _pump_until(
                        page,
                        lambda page=page: [e for e in _activity(page) if e["kind"] == "current"],
                        timeout=30,
                    )
                normal, reduced = pages["motion"], pages["reduce"]
                for page in pages.values():
                    page.get_by_role("button", name="Fit").click()  # every lane in view
                expect(normal.get_by_test_id("jev-graph")).to_have_attribute(
                    "data-reduced-motion", "false"
                )
                assert any(e["animated"] for e in _activity(normal))
                expect(reduced.get_by_test_id("jev-graph")).to_have_attribute(
                    "data-reduced-motion", "true"
                )
                expect(reduced.get_by_test_id("jev-live-badge")).to_be_visible()  # still live
                drawn = _activity(reduced)
                assert drawn and not [e for e in drawn if e["animated"]]
                assert {e["animation"] for e in drawn} == {"none"}
                pulses = reduced.evaluate(
                    """() => Array.from(document.querySelectorAll(
                         '.jev-graph-node-live rect, .jev-badge-live'))
                         .map((el) => getComputedStyle(el, el.tagName === 'rect' ? null
                           : '::before').animationName)"""
                )
                assert pulses and set(pulses) == {"none"}
                expect(reduced.get_by_test_id("jev-graph-edge-legend")).to_contain_text(
                    "Reduced motion: nothing animates."
                )
                evidence.shot(normal, "01-normal-motion", run_id=run_id, step=game.step)
                evidence.shot(reduced, "02-reduced-motion", run_id=run_id, step=game.step)
            finally:
                game.stop.set()
                thread.join(60)
                motion.close()
                still.close()

    def test_an_old_v1_archive_renders_its_lanes_without_v2_evidence(
        self, built_dashboard: Path, chromium: Any, tmp_path: Path
    ) -> None:
        from playwright.sync_api import expect

        from test_jev_economy import _world
        from test_jev_v2 import _OpeningWorld

        evidence = _Evidence("g3")
        run_root = tmp_path / "runs"
        bundle = load_v1_policy()
        game = _SteppedGame(_world(minerals=50, world_type=_OpeningWorld), 300, pace=0.0)
        thread, recorded, outcome = _start(run_root, game, MatchOptions(), bundle)
        thread.join(120)
        (run_id,) = recorded
        assert outcome and outcome[0].status == "finished"
        serving = _serving(run_root, dist=built_dashboard, access_log=evidence.server_log())
        with serving as server:
            context = chromium.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            try:
                page.goto(f"{server.base}/?tab=jev&run={run_id}")
                expect(page.get_by_test_id("jev-run-id")).to_have_text(run_id)
                expect(page.get_by_role("tree")).to_be_visible(timeout=WAIT_MS)
                expect(page.get_by_test_id("jev-run-status")).to_have_text("Status: finished")
                lanes = _lanes(page)
                assert list(lanes) == list(bundle.policy.roots)  # one coloured lane per root
                roots = page.locator('[role="treeitem"][aria-level="1"]')
                expect(roots).to_have_count(len(bundle.policy.roots))
                page.get_by_test_id("jev-full-graph").click()
                expect(roots).to_have_count(len(bundle.policy.roots))
                classes = page.evaluate(
                    """() => Array.from(document.querySelectorAll('.jev-graph-lane-band'))
                         .map((band) => band.getAttribute('class'))"""
                )
                assert len(classes) == len(bundle.policy.roots)
                assert all("jev-lane-" in name for name in classes)
                expect(page.get_by_test_id("jev-plans")).to_have_count(0)  # no v2 panel
                expect(page.get_by_test_id("jev-army-decision")).to_contain_text("Scripted")
                assert set(e["edge"] for e in _activity(page)) <= _policy_edges(bundle)
                assert not [e for e in _activity(page) if e["animated"]]
                page.get_by_role("button", name="Fit").click()  # every lane in view
                page.locator('[role="treeitem"][aria-label^="economy,"]').click()
                expect(page.get_by_test_id("jev-node-lane")).to_have_text("economy")
                expect(page.get_by_test_id("jev-node-sources-none")).to_be_visible()
                evidence.shot(page, "01-v1-archive", run_id=run_id, lanes=list(lanes))
            finally:
                context.close()
