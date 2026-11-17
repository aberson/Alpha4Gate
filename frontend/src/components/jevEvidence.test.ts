import { describe, it, expect } from "vitest";
import {
  eventSource,
  graphActivity,
  pathGuards,
  plansEvidence,
  readSlotTurn,
} from "./jevEvidence";
import type { JevEvent, JevPolicy, JevPolicyNode, JevRunState, JsonObject } from "../types/jev";

/**
 * The Jev tab's pure evidence derivations (plan J2 Step 221, D5), tested directly:
 * recorded edge activity, node sources, the plan merge rule and ordering, slot turns
 * and path guards. A small hand-made forest:
 *
 *   lane (sequence)                 army (selector)
 *   ├─ lane.guard (strategy_is)     └─ army.attack (action)
 *   └─ lane.body (sequence)
 *      ├─ lane.body.pick (selector: two production_mix guards)
 *      │  ├─ lane.body.pick.a
 *      │  └─ lane.body.pick.b
 *      └─ lane.body.go (action)
 */

function node(
  id: string,
  kind: JevPolicyNode["kind"],
  children: string[] = [],
  operation: string | null = children.length > 0 ? null : "count_compare",
): JevPolicyNode {
  return { id, label: id, kind, children, operation, args: {} };
}

const POLICY: JevPolicy = {
  family: "jev",
  version: 2,
  roots: ["lane", "army"],
  parameters: {},
  policy_hash: "d".repeat(64),
  nodes: [
    node("lane", "sequence", ["lane.guard", "lane.body"]),
    node("lane.guard", "condition", [], "strategy_is"),
    node("lane.body", "sequence", ["lane.body.pick", "lane.body.go"]),
    node("lane.body.pick", "selector", ["lane.body.pick.a", "lane.body.pick.b"]),
    node("lane.body.pick.a", "condition", [], "production_mix_is"),
    node("lane.body.pick.b", "condition", [], "production_mix_local"),
    node("lane.body.go", "action", [], "train"),
    node("army", "selector", ["army.attack"]),
    node("army.attack", "action", [], "attack"),
  ],
};

let sequence = 0;

function event(nodeId: string, extra: Partial<JevEvent> = {}): JevEvent {
  sequence += 1;
  return {
    sequence,
    game_loop: sequence,
    game_seconds: 10,
    node_id: nodeId,
    task_id: null,
    kind: "node",
    status: "running",
    reason: "evaluated",
    facts: {},
    action: null,
    ...extra,
  };
}

function diagnostic(reason: string, facts: JsonObject, nodeId = "lane"): JevEvent {
  return event(nodeId, { kind: "diagnostic", status: "success", reason, facts });
}

function state(extra: Partial<JevRunState>): JevRunState {
  return {
    run_id: "0a1b2c3d4e5f4a6b8c7d9e0f1a2b3c4d",
    family: "jev",
    version: 2,
    policy_hash: POLICY.policy_hash,
    status: "running",
    updated_at: "2026-10-10T12:00:00.000+00:00",
    game_seconds: 10,
    last_sequence: sequence,
    active_nodes: [],
    waiting_nodes: [],
    tasks: [],
    recent_events: [],
    result: null,
    error: null,
    tasks_omitted: 0,
    trace: {
      segment: 1,
      events: 0,
      rotated_segments: 0,
      dropped_segments: 0,
      dropped_events: 0,
      complete: true,
    },
    stale: false,
    metadata: {
      created_at: "2026-10-10T11:59:00.000+00:00",
      source_commit: null,
      map: "Simple64",
      opponent_race: "Terran",
      difficulty: 1,
      seed: 1,
      max_game_seconds: 900,
      max_wall_seconds: 1800,
      replay_path: null,
    },
    ...extra,
  };
}

describe("graphActivity", () => {
  it("draws only the latest state's running path and node events of the last 2 game s", () => {
    const activity = graphActivity(
      state({
        game_seconds: 10,
        active_nodes: ["lane", "lane.body", "lane.body.go"],
        waiting_nodes: ["army"], // its child is not running: no edge
        recent_events: [
          event("army", { game_seconds: 9, facts: { child: "army.attack" } }), // recent
          event("lane", { game_seconds: 7.5, facts: { child: "lane.guard" } }), // too old
          event("lane", { game_seconds: 9.5, facts: { child: "army.attack" } }), // not its child
          event("lane", { game_seconds: 9.9, facts: { child: "lane.body" } }), // already current
          event("lane.body", { game_seconds: 11, facts: { child: "lane.body.pick" } }), // future
        ],
      }),
      POLICY,
    );
    expect(activity.edges.map((e) => [e.parent, e.child, e.kind, e.ageSeconds])).toEqual([
      ["lane", "lane.body", "current", 0],
      ["lane.body", "lane.body.go", "current", 0],
      ["army", "army.attack", "recent", 1],
    ]);
  });

  it("follows the deepest running nodes, the newest recorded one first", () => {
    const activity = graphActivity(
      state({
        active_nodes: ["lane", "lane.body", "lane.body.go", "army"],
        recent_events: [event("lane.body.go"), event("army")],
      }),
      POLICY,
    );
    expect(activity.followTargets).toEqual(["army", "lane.body.go"]);
  });
});

describe("eventSource", () => {
  it("names the hosted request a task or command was created under", () => {
    const source = eventSource(
      event("lane.body.go", {
        kind: "command",
        task_id: "run:9",
        status: "issued",
        facts: {
          decision_question_id: "production_mix",
          decision_request_id: 12,
          decision_choice: "balanced",
          decision_plan_id: null,
        },
      }),
    );
    expect(source).toMatchObject({ tone: "hosted", badge: "Typesafe req #12" });
    expect(source?.detail).toBe(
      "command run:9 created under production_mix = balanced: Typesafe (request 12)",
    );
  });

  it("names a plan's response request, else who started it", () => {
    const plan = { plan_id: "run:plan:1", plan_kind: "expand" };
    expect(
      eventSource(diagnostic("summary", { ...plan, response: "delay", response_request_id: 6 })),
    ).toMatchObject({ tone: "hosted", badge: "Typesafe req #6" });
    expect(
      eventSource(diagnostic("summary", { ...plan, decided_by: "local_graph" })),
    ).toMatchObject({ tone: "local", badge: "local graph" });
  });

  it("reads a guard's source and the army-mode guard's intent, and nothing else", () => {
    expect(
      eventSource(
        event("lane.guard", {
          status: "success",
          facts: { question_id: "strategy", choice: "tech", source: "typesafe", request_id: 3 },
        }),
      )?.detail,
    ).toBe("guard success (strategy = tech): Typesafe (request 3)");
    expect(
      eventSource(event("x", { facts: { source: "local_fallback", request_id: null } })),
    ).toMatchObject({ tone: "fallback", badge: "local fallback" });
    expect(eventSource(event("x", { facts: { mode: null, branch: "attack" } }))).toMatchObject({
      tone: "local",
    });
    expect(eventSource(event("x", { facts: { mode: "attack", branch: "attack" } }))).toMatchObject(
      { tone: "hosted", badge: "army attack" },
    );
    expect(eventSource(event("x", { facts: { child: "y" } }))).toBeNull();
    expect(eventSource(event("x", { kind: "task", facts: {} }))).toBeNull();
  });
});

describe("plansEvidence", () => {
  const open = {
    plan_id: "run:plan:2",
    plan_kind: "expand",
    plan_status: "waiting",
    stage_node_id: "lane.body.go",
    reason_code: "site_threatened",
    blocker: "an enemy at the site",
    started_game_seconds: 300,
    last_progress_game_seconds: 310,
    deadline_game_seconds: 420,
    reserved_minerals: 75,
    reserved_vespene: 0,
    finished_game_seconds: null,
  };

  it("merges each plan fact by fact: a missing fact never erases a recorded one", () => {
    const { plans } = plansEvidence([
      diagnostic("expand plan summary", open),
      diagnostic("Plan slot summary", {
        plans: [{ plan_id: "run:plan:2", plan_kind: "expand", plan_status: "waiting" }],
      }),
    ]);
    expect(plans).toHaveLength(1);
    expect(plans[0]).toMatchObject({
      deadline: 420,
      reservedMinerals: 75,
      blocker: "an enemy at the site",
    });
  });

  it("lets a recorded null replace a value: the blocker clears when the plan moves on", () => {
    const { plans } = plansEvidence([
      diagnostic("expand plan summary", open),
      diagnostic("expand plan summary", { ...open, plan_status: "active", blocker: null }),
    ]);
    expect(plans[0]).toMatchObject({ status: "active", blocker: null, deadline: 420 });
  });

  it("orders open plans first, then finished ones by their own finish time", () => {
    const finished = (id: string, at: number) => ({
      plan_id: id,
      plan_kind: "tech",
      plan_status: "aborted",
      stage_node_id: "lane.body.go",
      reason_code: "timeout",
      finished_game_seconds: at,
    });
    const { plans } = plansEvidence([
      diagnostic("tech plan aborted", finished("run:plan:3", 250)),
      diagnostic("tech plan aborted", finished("run:plan:1", 100)),
      // A slot summary repeats every kept plan with one sequence: it must not reorder them.
      diagnostic("Plan slot summary", {
        plans: [finished("run:plan:1", 100), open, finished("run:plan:3", 250)],
      }),
    ]);
    expect(plans.map((plan) => plan.planId)).toEqual(["run:plan:2", "run:plan:3", "run:plan:1"]);
  });

  it("keeps a slot's next-permitted kinds when a later plan event names its turn", () => {
    const turn = { slot: "macro", open_plan_id: null, yield_from: "tech", yield_to: ["expand"] };
    const { slots } = plansEvidence([
      diagnostic("Plan slot summary", {
        slot_turn: turn,
        kinds: [{ kind: "expand", may_start: true, refusal: null }],
      }),
      diagnostic("tech plan aborted", { ...open, slot_turn: { ...turn, yield_until: 270 } }),
    ]);
    expect(slots).toHaveLength(1);
    expect(slots[0]).toMatchObject({ yieldUntil: 270, yieldTo: ["expand"] });
    expect(slots[0].kinds).toEqual([{ kind: "expand", mayStart: true, refusal: null }]);
  });

  it("lists each question's newest source in question order", () => {
    const { decisions } = plansEvidence([
      diagnostic("Production mix decision source", { source: "local_fallback", choice: null }),
      diagnostic("Strategy decision source", {
        source: "typesafe",
        choice: "tech",
        in_force_request_id: 3,
        available_options: ["pressure", 7],
      }),
    ]);
    expect(decisions.map((view) => [view.question, view.source, view.request])).toEqual([
      ["strategy", "typesafe", 3],
      ["production_mix", "local_fallback", null],
    ]);
    expect(decisions[0].available).toEqual(["pressure"]);
  });
});

describe("pathGuards and readSlotTurn", () => {
  it("names the guards a node runs under, nearest first, never the node itself", () => {
    expect(pathGuards(POLICY, "lane.body.go")).toEqual([
      "lane.body.pick.a",
      "lane.body.pick.b",
      "lane.guard",
    ]);
    expect(pathGuards(POLICY, "lane.guard")).toEqual([]);
    expect(pathGuards(POLICY, "army.attack")).toEqual([]);
  });

  it("reads only a slot turn object with a slot name", () => {
    expect(readSlotTurn(null, 1)).toBeNull();
    expect(readSlotTurn(["macro"], 1)).toBeNull();
    expect(readSlotTurn({ open_plan_id: "x" }, 1)).toBeNull();
    const turn = readSlotTurn({ slot: "macro", disabled: { expand: "abort_limit", x: 1 } }, 2);
    expect(turn).toMatchObject({
      slot: "macro",
      disabled: [["expand", "abort_limit"]],
      yieldTo: [],
      sequence: 2,
    });
  });
});
