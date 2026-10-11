import { describe, it, expect, vi, beforeEach, afterEach, type MockInstance } from "vitest";
import { render, screen, cleanup, fireEvent, within, act } from "@testing-library/react";
import { JevTab } from "./JevTab";
import { JEV_REQUEST_TIMEOUT_MS, JEV_RUN_POLL_MS } from "../hooks/useJevRun";
import {
  JEV_LIMITS,
  JEV_SCHEMA_VERSION,
  LAUNCH_STATES,
  launchLiveness,
  launchPhase,
  type JevLaunchFacts,
  type JevLaunchPhase,
  type JevLaunchState,
  type JevRunStatus,
  type JevShownRun,
} from "../types/jev";
// The archived policy the API serves is the packaged policy document plus
// its hash, so the policy responses below use the real shipped document.
import shippedPolicyText from "../../../bots/jev/v1/policy.json?raw";
import shippedV2PolicyText from "../../../bots/jev/v2/policy.json?raw";

/**
 * JevTab tests: every run state the tab must label, the node inspector,
 * contract validation, run switching, and safe rendering. ``fetch`` is
 * routed per URL; ``deferred`` responses let a test decide when (and in
 * which order) runs answer.
 */

const RUN_A = "0a1b2c3d4e5f4a6b8c7d9e0f1a2b3c4d";
const RUN_B = "9f8e7d6c5b4a4f3e9d2c1b0a9f8e7d6c";
const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);
const BIG_TAG = "18446744073709551615"; // 2**64 - 1: not representable as a JS number

type Doc = Record<string, unknown>;

function shippedPolicy(): Doc {
  return JSON.parse(shippedPolicyText) as Doc;
}

function policyDoc(hash: string, rootLabel?: string): Doc {
  const policy = shippedPolicy();
  if (rootLabel !== undefined) {
    policy.nodes = (policy.nodes as Doc[]).map((node) =>
      node.id === "economy" ? { ...node, label: rootLabel } : node,
    );
  }
  return { ...policy, policy_hash: hash };
}

function summary(runId: string, hash: string, status = "running"): Doc {
  return {
    run_id: runId,
    family: "jev",
    version: 1,
    policy_hash: hash,
    status,
    updated_at: "2026-10-08T12:00:00.000+00:00",
  };
}

function runList(...runs: Doc[]): Doc {
  return { schema_version: 1, runs, truncated: false, omitted: 0 };
}

function event(runId: string, sequence: number, nodeId: string, extra: Doc = {}): Doc {
  return {
    schema_version: 1,
    run_id: runId,
    sequence,
    game_loop: sequence * 6,
    game_seconds: sequence / 4,
    node_id: nodeId,
    task_id: null,
    kind: "node",
    status: "success",
    reason: `evaluated ${nodeId}`,
    facts: {},
    action: null,
    ...extra,
  };
}

function task(runId: string, nodeId: string, extra: Doc = {}): Doc {
  return {
    id: `${runId}:1`,
    node_id: nodeId,
    intent_key: `${nodeId}|1`,
    actor_tag: "4294967297",
    target: "3001",
    status: "issued",
    created_game_seconds: 1,
    deadline_game_seconds: 6,
    attempts: 1,
    last_progress_game_seconds: null,
    reason: "command issued",
    ...extra,
  };
}

function runState(runId: string, hash: string, extra: Doc = {}): Doc {
  return {
    schema_version: 1,
    ...summary(runId, hash),
    game_seconds: 12.5,
    last_sequence: 0,
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
      schema_version: 1,
      run_id: runId,
      created_at: "2026-10-08T11:59:00.000+00:00",
      family: "jev",
      version: 1,
      policy_hash: hash,
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

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

interface Deferred {
  promise: Promise<Response>;
  resolve: (response: Response) => void;
}

function deferred(): Deferred {
  let resolve: (response: Response) => void = () => {};
  const promise = new Promise<Response>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

type Responder = () => Promise<Response> | Response;
let routes: Map<string, Responder>;
let fetchSpy: MockInstance<typeof fetch>;

function route(url: string, responder: Responder | Doc): void {
  routes.set(url, typeof responder === "function" ? responder : () => jsonResponse(responder));
}

function serveRun(runId: string, state: Doc, policy: Doc): void {
  route(`/api/jev/runs/${runId}`, state);
  route(`/api/jev/runs/${runId}/policy`, policy);
}

function requestedUrls(): string[] {
  return fetchSpy.mock.calls.map((call) => String(call[0]));
}

beforeEach(() => {
  routes = new Map();
  fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const responder = routes.get(String(input));
    if (responder === undefined) {
      return jsonResponse(
        { schema_version: 1, error: { code: "run_not_found", message: "no run with this id exists" } },
        404,
      );
    }
    return responder();
  });
});

afterEach(() => {
  fetchSpy.mockRestore();
  vi.useRealTimers();
  cleanup();
});

function treeitem(id: string): HTMLElement {
  return screen.getByRole("treeitem", { name: new RegExp(`^${id.replace(/\./g, "\\.")},`) });
}

/** The ``<dd>`` describing ``term`` in a definition list inside ``container``. */
function definition(container: HTMLElement, term: string): Element | null {
  return within(container).getByText(term, { selector: "dt" }).nextElementSibling;
}

async function renderRun(state: Doc, policy: Doc = policyDoc(HASH_A)): Promise<void> {
  route("/api/jev/runs", runList(summary(RUN_A, HASH_A, String(state.status))));
  serveRun(RUN_A, state, policy);
  render(<JevTab />);
  await screen.findByRole("tree");
}

describe("JevTab run states", () => {
  it("explains how runs appear when there are none", async () => {
    route("/api/jev/runs", runList());
    render(<JevTab />);
    expect(await screen.findByTestId("jev-empty")).toHaveTextContent("No Jev runs yet.");
    expect(requestedUrls()).toEqual(["/api/jev/runs"]);
  });

  it("shows a loading state until the run list arrives", () => {
    route("/api/jev/runs", () => deferred().promise);
    render(<JevTab />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading Jev runs…");
  });

  it("reports an unreachable API", async () => {
    route("/api/jev/runs", () => Promise.reject(new TypeError("Failed to fetch")));
    render(<JevTab />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load the Jev run list: the dashboard API did not respond",
    );
  });

  it("reports an API error response with its code", async () => {
    route("/api/jev/runs", runList(summary(RUN_A, HASH_A)));
    route(`/api/jev/runs/${RUN_A}`, () =>
      jsonResponse(
        { schema_version: 1, error: { code: "corrupt_run", message: "state.json is missing" } },
        503,
      ),
    );
    render(<JevTab />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      `Could not load run ${RUN_A}: HTTP 503 corrupt_run: state.json is missing`,
    );
  });

  it("labels a live run and draws its active nodes as current", async () => {
    await renderRun(runState(RUN_A, HASH_A, { active_nodes: ["economy"] }));
    expect(screen.getByTestId("jev-live-badge")).toHaveTextContent("Live");
    expect(treeitem("economy")).toHaveAccessibleName("economy, sequence · active");
    expect(treeitem("economy")).toHaveAttribute("data-live", "true");
  });

  it.each([
    ["stale", "running", { stale: true }, "jev-stale-badge", "Stale"],
    [
      "failed",
      "failed",
      { status: "failed", error: { code: "match_crashed", message: "the SC2 client exited" } },
      "jev-run-error",
      "Run failed: match_crashed the SC2 client exited",
    ],
    ["finished", "finished", { status: "finished", result: "win" }, "jev-run-result", "Result: win"],
  ])(
    "labels a %s run and draws its last active nodes as last known, never live",
    async (_, status, extra, markerId, markerText) => {
      await renderRun(runState(RUN_A, HASH_A, { active_nodes: ["economy"], ...extra }));
      expect(screen.getByTestId("jev-run-status")).toHaveTextContent(`Status: ${status}`);
      expect(screen.getByTestId(markerId)).toHaveTextContent(markerText);
      expect(screen.queryByTestId("jev-live-badge")).not.toBeInTheDocument();
      expect(treeitem("economy")).toHaveAccessibleName("economy, sequence · active (last known)");
      expect(treeitem("economy")).toHaveAttribute("data-live", "false");
    },
  );

  it("names a waiting run's waiting nodes", async () => {
    await renderRun(runState(RUN_A, HASH_A, { waiting_nodes: ["construction", "construction.supply"] }));
    expect(screen.getByTestId("jev-waiting")).toHaveTextContent(
      "Waiting: 2 node(s) cannot proceed yet — construction, construction.supply.",
    );
    expect(treeitem("construction")).toHaveAccessibleName(/^construction, selector · waiting/);
  });

  it("times out a hung refresh as offline, then recovers on the next successful poll", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const state = runState(RUN_A, HASH_A, { active_nodes: ["economy"] });
    await renderRun(state);
    route(`/api/jev/runs/${RUN_A}`, () => new Promise<Response>(() => {}));
    await act(() => vi.advanceTimersByTimeAsync(JEV_RUN_POLL_MS + JEV_REQUEST_TIMEOUT_MS));
    expect(screen.getByTestId("jev-offline-badge")).toHaveTextContent("Offline");
    expect(screen.getByText(/did not answer within/)).toBeInTheDocument();
    expect(treeitem("economy")).toHaveAttribute("data-live", "false");

    route(`/api/jev/runs/${RUN_A}`, state);
    await act(() => vi.advanceTimersByTimeAsync(JEV_RUN_POLL_MS));
    expect(screen.queryByTestId("jev-offline-badge")).not.toBeInTheDocument();
    expect(screen.getByTestId("jev-live-badge")).toHaveTextContent("Live");
    expect(treeitem("economy")).toHaveAttribute("data-live", "true");
  });

  it("refuses to draw the graph when the state and archived policy hashes differ", async () => {
    route("/api/jev/runs", runList(summary(RUN_A, HASH_A)));
    serveRun(RUN_A, runState(RUN_A, HASH_A), policyDoc(HASH_B));
    render(<JevTab />);
    expect(await screen.findByTestId("jev-hash-mismatch")).toHaveTextContent(
      "Policy hash mismatch",
    );
    expect(screen.queryByRole("tree")).not.toBeInTheDocument();
  });
});

describe("JevTab node inspector", () => {
  const command = event(RUN_A, 2, "economy.assign", {
    task_id: `${RUN_A}:1`,
    kind: "command",
    status: "issued",
    reason: "HARVEST_GATHER for the task",
    facts: { attempt: 1 },
    action: { ability: "HARVEST_GATHER", actor_tags: [BIG_TAG], target: "3001" },
  });

  it("shows the selected node's archived definition, latest events and task deadline", async () => {
    await renderRun(
      runState(RUN_A, HASH_A, {
        recent_events: [
          event(RUN_A, 1, "economy.assign", {
            status: "running",
            reason: "1 task(s) in progress",
            facts: { issued: 1 },
          }),
          command,
        ],
        tasks: [
          task(RUN_A, "economy.assign"),
          task(RUN_A, "economy.assign", { id: `${RUN_A}:2`, deadline_game_seconds: null }),
        ],
      }),
    );
    fireEvent.click(treeitem("economy.assign"));
    const panel = screen.getByTestId("jev-node-panel");
    const archived = (shippedPolicy().nodes as Doc[]).find((n) => n.id === "economy.assign") as Doc;
    expect(within(panel).getByTestId("jev-node-id")).toHaveTextContent("economy.assign");
    expect(within(panel).getByRole("heading", { level: 3 })).toHaveTextContent(String(archived.label));
    expect(definition(panel, "Kind")).toHaveTextContent("action");
    expect(definition(panel, "Operation")).toHaveTextContent("gather");
    expect(panel).toHaveTextContent('"workers": "$idle_probes"');
    expect(panel).toHaveTextContent("running at 0.3 s (event 1): 1 task(s) in progress");
    const [newest] = within(panel).getAllByTestId("jev-node-event");
    expect(newest).toHaveTextContent("#2 · 0.5 s · command · issued — HARVEST_GATHER for the task");
    expect(newest).toHaveTextContent('"ability": "HARVEST_GATHER"');
    const [timed, untimed] = within(panel).getAllByTestId("jev-node-task");
    expect(timed).toHaveTextContent(`${RUN_A}:1 · issued`);
    expect(timed).toHaveTextContent("deadline 6.0 s");
    expect(untimed).toHaveTextContent(`${RUN_A}:2 · issued`);
    expect(untimed).toHaveTextContent("no deadline");
  });

  it("keeps 64-bit unit tags exact", async () => {
    await renderRun(
      runState(RUN_A, HASH_A, {
        recent_events: [command],
        tasks: [task(RUN_A, "economy.assign", { actor_tag: BIG_TAG })],
      }),
    );
    fireEvent.click(treeitem("economy.assign"));
    const panel = screen.getByTestId("jev-node-panel");
    expect(within(panel).getByTestId("jev-node-task")).toHaveTextContent(`actor ${BIG_TAG}`);
    expect(within(panel).getByTestId("jev-node-event")).toHaveTextContent(`"${BIG_TAG}"`);
  });

  it("renders stored text as text, never as markup", async () => {
    const hostile = '<img src="x" onerror="alert(1)">';
    await renderRun(
      runState(RUN_A, HASH_A, {
        recent_events: [event(RUN_A, 1, "economy", { reason: hostile })],
        error: { code: "match_crashed", message: hostile },
        status: "failed",
      }),
    );
    expect(screen.getByTestId("jev-run-error")).toHaveTextContent(hostile);
    expect(screen.getByTestId("jev-trace")).toHaveTextContent(hostile);
    expect(document.querySelector("img")).toBeNull();
  });
});

describe("JevTab army decision", () => {
  function armyDecision(extraFacts: Doc): Doc {
    return event(RUN_A, 8, "army", {
      reason: "Army decision source",
      facts: extraFacts,
    });
  }

  it("does not infer a provider for an older run without decision evidence", async () => {
    await renderRun(runState(RUN_A, HASH_A));
    expect(screen.getByTestId("jev-decision-unavailable")).toHaveTextContent(
      "Provider evidence unavailable",
    );
    expect(screen.queryByText(/Source: Typesafe/)).not.toBeInTheDocument();
  });

  it("separates the current applied intent from a pending request's last response", async () => {
    await renderRun(
      runState(RUN_A, HASH_A, {
        recent_events: [
          armyDecision({
            decision_provider: "typesafe",
            source: "typesafe",
            choice: "defend",
            reason: "holding the natural",
            pending: true,
            requested_model: "requested-model",
            model: "actual-model",
            question: "Choose the army intent",
            options: ["attack", "defend", "regroup"],
            answer: "attack",
            confidence: 0.72,
            probabilities: { attack: 0.72, defend: 0.2, regroup: 0.08 },
            latency_ms: 124,
            calls: 2,
            input_tokens: 30,
            output_tokens: 8,
            max_requests: 4,
            observation_game_seconds: 11,
            response_age_game_seconds: 3.5,
          }),
        ],
      }),
    );
    const panel = screen.getByTestId("jev-army-decision");
    expect(within(panel).getByText("Source: Typesafe response")).toBeInTheDocument();
    expect(within(panel).getByTestId("jev-decision-age")).toHaveTextContent("Live evidence");
    expect(definition(panel, "Current applied intent")).toHaveTextContent("defend");
    expect(definition(panel, "Request state")).toHaveTextContent("Pending now");

    fireEvent.click(within(panel).getByText("Typesafe request and last response details"));
    expect(definition(panel, "Last service answer")).toHaveTextContent("attack");
    expect(definition(panel, "Requested model")).toHaveTextContent("requested-model");
    expect(definition(panel, "Actual response model")).toHaveTextContent("actual-model");
    expect(panel).toHaveTextContent('"attack": 0.72');
  });

  it("labels a scripted fallback without treating the requested model as an actual response", async () => {
    await renderRun(
      runState(RUN_A, HASH_A, {
        recent_events: [
          armyDecision({
            decision_provider: "typesafe",
            source: "scripted_fallback",
            choice: "regroup",
            reason: "service unavailable",
            pending: false,
            requested_model: "configured-model",
            model: null,
            question: "Choose",
            options: ["attack", "defend", "regroup"],
            answer: null,
            confidence: null,
            probabilities: {},
            latency_ms: null,
            calls: 1,
            input_tokens: 0,
            output_tokens: 0,
            max_requests: 2,
            observation_game_seconds: 4,
            response_age_game_seconds: null,
          }),
        ],
      }),
    );
    const panel = screen.getByTestId("jev-army-decision");
    expect(panel).toHaveTextContent("Source: Scripted fallback");
    expect(definition(panel, "Current applied intent")).toHaveTextContent("regroup");
    fireEvent.click(within(panel).getByText("Typesafe request and last response details"));
    expect(definition(panel, "Requested model")).toHaveTextContent("configured-model");
    expect(definition(panel, "Actual response model")).toHaveTextContent("not recorded");
    expect(definition(panel, "Last service answer")).toHaveTextContent("none recorded");
  });

  it.each([
    ["stale", { stale: true }],
    ["archived", { status: "finished", result: "win" }],
  ])("labels %s decision evidence and pending state as last known", async (_, stateExtra) => {
    await renderRun(
      runState(RUN_A, HASH_A, {
        ...stateExtra,
        recent_events: [
          armyDecision({
            decision_provider: "typesafe",
            source: "typesafe",
            choice: "attack",
            reason: "pressure",
            pending: true,
            calls: 1,
          }),
        ],
      }),
    );
    const panel = screen.getByTestId("jev-army-decision");
    expect(within(panel).getByTestId("jev-decision-age")).toHaveTextContent(
      "Last known evidence",
    );
    expect(definition(panel, "Request state")).toHaveTextContent("Pending when recorded");
  });
});

describe("JevTab recent trace", () => {
  it("lists recent events newest first and labels status changes as transitions", async () => {
    await renderRun(
      runState(RUN_A, HASH_A, {
        recent_events: [
          event(RUN_A, 1, "construction", { status: "failure" }),
          event(RUN_A, 2, "economy"),
          event(RUN_A, 3, "construction", { status: "running" }),
        ],
      }),
    );
    const rows = screen.getAllByTestId("jev-trace-row");
    expect(rows.map((row) => row.firstElementChild?.textContent)).toEqual(["3", "2", "1"]);
    expect(rows[0]).toHaveTextContent("failure → running");
    expect(rows[1]).not.toHaveTextContent("→");
  });

  it("shows at most the newest 40 events", async () => {
    const events = Array.from({ length: 200 }, (_, i) => event(RUN_A, i + 1, "economy"));
    await renderRun(runState(RUN_A, HASH_A, { recent_events: events }));
    const rows = screen.getAllByTestId("jev-trace-row");
    expect(rows).toHaveLength(40);
    expect(rows[0].firstElementChild).toHaveTextContent("200");
  });
});

describe("JevTab contract validation", () => {
  it.each([
    [
      "an unsupported list schema",
      () => route("/api/jev/runs", { ...runList(), schema_version: JEV_SCHEMA_VERSION + 1 }),
    ],
    [
      "a run list over the API's limit",
      () =>
        route(
          "/api/jev/runs",
          runList(...Array.from({ length: JEV_LIMITS.runs + 1 }, () => summary(RUN_A, HASH_A))),
        ),
    ],
    [
      "a numeric unit tag",
      () => {
        route("/api/jev/runs", runList(summary(RUN_A, HASH_A)));
        const tasks = [task(RUN_A, "economy", { actor_tag: 7 })];
        serveRun(RUN_A, runState(RUN_A, HASH_A, { tasks }), policyDoc(HASH_A));
      },
    ],
    [
      "a state for another run",
      () => {
        route("/api/jev/runs", runList(summary(RUN_A, HASH_A)));
        serveRun(RUN_A, runState(RUN_B, HASH_A), policyDoc(HASH_A));
      },
    ],
    [
      "a policy whose nodes form a cycle",
      () => {
        route("/api/jev/runs", runList(summary(RUN_A, HASH_A)));
        const policy = policyDoc(HASH_A);
        policy.nodes = (policy.nodes as Doc[]).map((node) =>
          node.id === "economy.nexus" ? { ...node, kind: "sequence", children: ["economy"] } : node,
        );
        serveRun(RUN_A, runState(RUN_A, HASH_A), policy);
      },
    ],
  ])("shows an error instead of rendering %s", async (_, serve) => {
    serve();
    render(<JevTab />);
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid or unsupported response");
    expect(screen.queryByRole("tree")).not.toBeInTheDocument();
  });

  it("never requests a run whose id is not lowercase UUID4 hex", async () => {
    route("/api/jev/runs", runList(summary("../../pyproject.toml", HASH_A)));
    render(<JevTab />);
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid or unsupported response");
    expect(requestedUrls()).toEqual(["/api/jev/runs"]);
  });
});

describe("JevTab run switching", () => {
  function select(runId: string): void {
    fireEvent.change(screen.getByTestId("jev-run-select"), { target: { value: runId } });
  }

  it("discards the previous run's state, graph and selection immediately", async () => {
    const stateB = deferred();
    route("/api/jev/runs", runList(summary(RUN_A, HASH_A), summary(RUN_B, HASH_B)));
    serveRun(RUN_A, runState(RUN_A, HASH_A), policyDoc(HASH_A));
    route(`/api/jev/runs/${RUN_B}`, () => stateB.promise);
    route(`/api/jev/runs/${RUN_B}/policy`, policyDoc(HASH_B));
    render(<JevTab />);
    fireEvent.click(await screen.findByRole("treeitem", { name: /^economy,/ }));
    expect(screen.getByTestId("jev-node-id")).toHaveTextContent("economy");

    select(RUN_B);
    expect(screen.getByRole("status")).toHaveTextContent(`Loading run ${RUN_B}…`);
    expect(screen.queryByTestId("jev-run-hash")).not.toBeInTheDocument();
    expect(screen.queryByRole("tree")).not.toBeInTheDocument();
    expect(screen.queryByTestId("jev-node-panel")).not.toBeInTheDocument();

    await act(async () => stateB.resolve(jsonResponse(runState(RUN_B, HASH_B))));
    expect(screen.getByTestId("jev-run-hash")).toHaveTextContent(HASH_B);
    expect(screen.getByTestId("jev-node-panel")).toHaveTextContent(
      "Select a node in the graph to inspect it.",
    );
  });

  it("ignores late responses for a previously selected run", async () => {
    const lateState = deferred();
    const latePolicy = deferred();
    route("/api/jev/runs", runList(summary(RUN_A, HASH_A), summary(RUN_B, HASH_B)));
    route(`/api/jev/runs/${RUN_A}`, () => lateState.promise);
    route(`/api/jev/runs/${RUN_A}/policy`, () => latePolicy.promise);
    serveRun(
      RUN_B,
      runState(RUN_B, HASH_B, {
        recent_events: [event(RUN_B, 9, "economy", { reason: "run B evaluated economy" })],
      }),
      policyDoc(HASH_B, "Economy as archived by run B"),
    );
    render(<JevTab />);
    await screen.findByText(`Loading run ${RUN_A}…`);
    select(RUN_B);
    fireEvent.click(await screen.findByRole("treeitem", { name: /^economy,/ }));

    await act(async () => {
      lateState.resolve(
        jsonResponse(
          runState(RUN_A, HASH_A, {
            recent_events: [event(RUN_A, 1, "economy", { reason: "run A evaluated economy" })],
          }),
        ),
      );
      latePolicy.resolve(jsonResponse(policyDoc(HASH_A, "Economy as archived by run A")));
    });

    expect(screen.getByTestId("jev-run-id")).toHaveTextContent(RUN_B);
    expect(screen.getByTestId("jev-run-hash")).toHaveTextContent(HASH_B);
    const panel = screen.getByTestId("jev-node-panel");
    expect(within(panel).getByTestId("jev-node-id")).toHaveTextContent("economy");
    expect(within(panel).getByRole("heading", { level: 3 })).toHaveTextContent(
      "Economy as archived by run B",
    );
    expect(within(panel).getByTestId("jev-node-event")).toHaveTextContent("run B evaluated economy");
    expect(screen.queryByText(/run A evaluated economy/)).not.toBeInTheDocument();
  });
});

describe("JevTab launch following (plan D7)", () => {
  const SESSION = "5d4c3b2a1f0e4d9c8b7a6f5e4d3c2b1a";
  const RUN_C = "1234567890ab4cde8f0123456789abcd";
  const SESSION_URL = `/api/jev/launches/${SESSION}`;
  const READY_URL = `${SESSION_URL}/ready`;

  function launchSession(state: string, activeRunId: string | null, extra: Doc = {}): Doc {
    return {
      schema_version: 1,
      session_id: SESSION,
      active_run_id: activeRunId,
      state,
      case_index: 0,
      case_count: 2,
      updated_at: new Date().toISOString(), // a live launcher wrote it just now
      message: "",
      ...extra,
    };
  }

  function writtenAgo(seconds: number): string {
    return new Date(Date.now() - seconds * 1000).toISOString();
  }

  function posted(): string[] {
    return fetchSpy.mock.calls
      .filter((call) => call[1]?.method === "POST")
      .map((call) => String(call[0]));
  }

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    route(READY_URL, () =>
      jsonResponse({ schema_version: 1, ready: true, session_id: SESSION, run_id: RUN_A }),
    );
  });

  afterEach(() => {
    window.history.replaceState({}, "", "/");
  });

  function openLaunch(search = `/?tab=jev&launch=${SESSION}`): void {
    window.history.replaceState({}, "", search);
    render(<JevTab />);
  }

  async function poll(times = 1): Promise<void> {
    for (let i = 0; i < times; i += 1) {
      await act(() => vi.advanceTimersByTimeAsync(JEV_RUN_POLL_MS));
    }
  }

  it("shows Preparing at once and never an old run while the session has none", async () => {
    route("/api/jev/runs", runList(summary(RUN_B, HASH_B, "finished")));
    serveRun(RUN_B, runState(RUN_B, HASH_B), policyDoc(HASH_B));
    route(SESSION_URL, launchSession("preparing", null));
    openLaunch();
    expect(screen.getByTestId("jev-launch-phase")).toHaveTextContent("Preparing");
    await poll(6);
    expect(screen.getByTestId("jev-launch-phase")).toHaveTextContent("Preparing");
    expect(screen.getByTestId("jev-launch-detail")).toHaveTextContent(
      "waiting for the launcher to record its run",
    );
    expect(screen.queryByTestId("jev-run-summary")).not.toBeInTheDocument();
    expect(screen.getByTestId("jev-run-select")).toHaveValue("");
    expect(requestedUrls()).not.toContain(`/api/jev/runs/${RUN_B}`);
  });

  it("acknowledges the rendered starting run, then shows Starting, Live and Finished", async () => {
    route("/api/jev/runs", runList(summary(RUN_A, HASH_A, "starting")));
    // Paused at launch: no heartbeat yet, so the API computes stale for it.
    serveRun(RUN_A, runState(RUN_A, HASH_A, { status: "starting", stale: true }), policyDoc(HASH_A));
    route(SESSION_URL, launchSession("starting", RUN_A));
    openLaunch();
    await screen.findByRole("tree");
    await poll();
    expect(posted()).toEqual([READY_URL]);
    expect(screen.getByTestId("jev-launch-phase")).toHaveTextContent("Starting");
    expect(screen.getByTestId("jev-launch-starting")).toBeInTheDocument();
    expect(screen.queryByTestId("jev-stale-badge")).not.toBeInTheDocument();
    expect(screen.getByTestId("jev-launch-run")).toHaveTextContent(RUN_A);

    route(SESSION_URL, launchSession("running", RUN_A));
    route(
      `/api/jev/runs/${RUN_A}`,
      runState(RUN_A, HASH_A, {
        status: "running",
        game_seconds: 72.5,
        active_nodes: ["economy"],
        recent_events: [
          event(RUN_A, 4, "army", {
            reason: "Army decision source",
            facts: { choice: "attack", source: "typesafe" },
          }),
        ],
      }),
    );
    await poll(2);
    expect(screen.getByTestId("jev-launch-phase")).toHaveTextContent("Live");
    const detail = screen.getByTestId("jev-launch-detail");
    expect(detail).toHaveTextContent("game time 72.5 s");
    expect(detail).toHaveTextContent("vs Terran (difficulty 1) on Simple64");
    expect(detail).toHaveTextContent("1 active decision node(s)");
    expect(detail).toHaveTextContent("army intent attack");

    route(
      `/api/jev/runs/${RUN_A}`,
      runState(RUN_A, HASH_A, { status: "finished", result: "win" }),
    );
    await poll(2);
    expect(screen.getByTestId("jev-launch-phase")).toHaveTextContent("Finished");
    expect(screen.getByTestId("jev-launch-detail")).toHaveTextContent("Finished game 1 of 2: win.");
    expect(posted()).toEqual([READY_URL]); // acknowledged once, never again
  });

  it("tells a stale running bot apart from a game paused at launch", async () => {
    route("/api/jev/runs", runList(summary(RUN_A, HASH_A)));
    serveRun(RUN_A, runState(RUN_A, HASH_A, { stale: true }), policyDoc(HASH_A));
    route(SESSION_URL, launchSession("running", RUN_A));
    openLaunch();
    await screen.findByRole("tree");
    await poll();
    expect(screen.getByTestId("jev-launch-phase")).toHaveTextContent("Stale");
    expect(screen.getByTestId("jev-stale-badge")).toHaveTextContent("Stale");
    expect(screen.queryByTestId("jev-launch-starting")).not.toBeInTheDocument();
    expect(posted()).toEqual([]); // a running session is never acknowledged again
  });

  it("pauses following on a manual pick and waits for the viewer until Resume live", async () => {
    route(
      "/api/jev/runs",
      runList(summary(RUN_B, HASH_B, "finished"), summary(RUN_A, HASH_A, "finished")),
    );
    serveRun(RUN_A, runState(RUN_A, HASH_A, { status: "finished", result: "win" }), policyDoc(HASH_A));
    serveRun(RUN_B, runState(RUN_B, HASH_B, { status: "finished" }), policyDoc(HASH_B));
    serveRun(RUN_C, runState(RUN_C, HASH_A, { status: "starting", stale: true }), policyDoc(HASH_A));
    route(SESSION_URL, launchSession("between_games", RUN_A));
    openLaunch();
    expect(await screen.findByTestId("jev-run-id")).toHaveTextContent(RUN_A);
    fireEvent.change(screen.getByTestId("jev-run-select"), { target: { value: RUN_B } });
    expect(await screen.findByTestId("jev-launch-paused")).toHaveTextContent("Following paused");
    expect(await screen.findByTestId("jev-run-id")).toHaveTextContent(RUN_B);
    expect(screen.getByTestId("jev-launch-phase")).toHaveTextContent("Following paused");
    expect(screen.getByTestId("jev-launch-detail")).toHaveTextContent("(between games)");

    route(SESSION_URL, launchSession("starting", RUN_C, { case_index: 1 }));
    route(READY_URL, () =>
      jsonResponse({ schema_version: 1, ready: true, session_id: SESSION, run_id: RUN_C }),
    );
    await poll(3);
    expect(screen.getByTestId("jev-launch-phase")).toHaveTextContent("Waiting for this page");
    expect(screen.getByTestId("jev-launch-paused")).toHaveTextContent(
      "The launcher is waiting for this page to show its new game",
    );
    expect(screen.getByTestId("jev-run-id")).toHaveTextContent(RUN_B);
    expect(posted()).toEqual([]);

    fireEvent.click(screen.getByTestId("jev-resume-live"));
    expect(await screen.findByTestId("jev-run-id")).toHaveTextContent(RUN_C);
    await poll();
    expect(posted()).toEqual([READY_URL]);
    expect(screen.queryByTestId("jev-launch-paused")).not.toBeInTheDocument();
    expect(screen.getByTestId("jev-launch-game")).toHaveTextContent("Game 2 of 2");
  });

  it.each([
    ["failed", "the dashboard did not show run 0a1b2c3d within 60 s; SC2 was not started", "Failed"],
    ["stopped", "stopped before SC2 started for run 0a1b2c3d", "Stopped"],
  ])("shows a %s launch with the launcher's reason", async (state, message, label) => {
    route("/api/jev/runs", runList(summary(RUN_A, HASH_A, "stopped")));
    serveRun(RUN_A, runState(RUN_A, HASH_A, { status: "stopped" }), policyDoc(HASH_A));
    route(SESSION_URL, launchSession(state, RUN_A, { message }));
    openLaunch();
    await screen.findByRole("tree");
    expect(screen.getByTestId("jev-launch-phase")).toHaveTextContent(label);
    expect(screen.getByTestId("jev-launch-detail")).toHaveTextContent(message);
    expect(screen.queryByTestId("jev-live-badge")).not.toBeInTheDocument();
  });

  it("shows a launch error and never falls back to another run", async () => {
    route("/api/jev/runs", runList(summary(RUN_B, HASH_B)));
    serveRun(RUN_B, runState(RUN_B, HASH_B), policyDoc(HASH_B));
    route(SESSION_URL, () =>
      jsonResponse(
        {
          schema_version: 1,
          error: { code: "launch_not_found", message: "no launch session with this id exists" },
        },
        404,
      ),
    );
    openLaunch();
    expect(await screen.findByTestId("jev-launch-error")).toHaveTextContent(
      "HTTP 404 launch_not_found: no launch session with this id exists",
    );
    await poll(3);
    expect(screen.queryByTestId("jev-run-summary")).not.toBeInTheDocument();
    expect(screen.getByTestId("jev-launch-error")).toHaveTextContent("No other run is shown");
  });

  it.each([
    ["/?tab=jev&run=../../pyproject.toml", "run"],
    ["/?tab=jev&launch=not-a-session", "launch session"],
  ])("refuses the invalid link %s without selecting any run", async (search, what) => {
    route("/api/jev/runs", runList(summary(RUN_B, HASH_B)));
    serveRun(RUN_B, runState(RUN_B, HASH_B), policyDoc(HASH_B));
    openLaunch(search);
    expect(screen.getByTestId("jev-link-error")).toHaveTextContent(`invalid ${what} id`);
    await poll(2);
    expect(screen.queryByTestId("jev-run-summary")).not.toBeInTheDocument();
    expect(requestedUrls().every((url) => url === "/api/jev/runs")).toBe(true);
  });

  it("selects exactly the run a ?run link names, not the newest", async () => {
    route("/api/jev/runs", runList(summary(RUN_B, HASH_B), summary(RUN_A, HASH_A)));
    serveRun(RUN_A, runState(RUN_A, HASH_A), policyDoc(HASH_A));
    serveRun(RUN_B, runState(RUN_B, HASH_B), policyDoc(HASH_B));
    openLaunch(`/?tab=jev&run=${RUN_A}`);
    expect(await screen.findByTestId("jev-run-id")).toHaveTextContent(RUN_A);
    await act(() => vi.advanceTimersByTimeAsync(6000));
    expect(screen.getByTestId("jev-run-id")).toHaveTextContent(RUN_A);
    expect(screen.queryByTestId("jev-launch")).not.toBeInTheDocument();
  });

  it("never shows a run held at the launch barrier as live, even before it reads stale", async () => {
    route("/api/jev/runs", runList(summary(RUN_A, HASH_A, "starting")));
    // What the API serves for the first 5 s of the hold: starting, heartbeat fresh.
    serveRun(
      RUN_A,
      runState(RUN_A, HASH_A, { status: "starting", stale: false, active_nodes: ["economy"] }),
      policyDoc(HASH_A),
    );
    route(SESSION_URL, launchSession("starting", RUN_A));
    openLaunch();
    await screen.findByRole("tree");
    for (const phase of ["before the acknowledgment", "after it"]) {
      expect(screen.queryByTestId("jev-live-badge"), phase).not.toBeInTheDocument();
      expect(treeitem("economy"), phase).toHaveAttribute("data-live", "false");
      expect(screen.getByTestId("jev-launch-starting"), phase).toBeInTheDocument();
      await poll();
    }
    expect(screen.getByTestId("jev-launch-phase")).toHaveTextContent("Starting");
  });

  it.each([
    [
      "no such session",
      404,
      { code: "launch_not_found", message: "no launch session with this id exists" },
      1,
    ],
    ["a corrupt session, after the retry bound", 503, { code: "corrupt_launch", message: "x" }, 61],
  ])("never reads %s as Preparing", async (_, status, error, polls) => {
    route("/api/jev/runs", runList(summary(RUN_B, HASH_B)));
    serveRun(RUN_B, runState(RUN_B, HASH_B), policyDoc(HASH_B));
    route(SESSION_URL, () => jsonResponse({ schema_version: 1, error }, status));
    openLaunch();
    await poll(polls);
    const phase = screen.getByTestId("jev-launch-phase");
    expect(phase).toHaveTextContent("Launch unavailable");
    expect(phase).not.toHaveTextContent("Preparing");
    expect(screen.getByTestId("jev-launch-detail")).toHaveTextContent(error.code);
    expect(screen.getByTestId("jev-launch-error")).toHaveTextContent("Stopped following launch");
    expect(screen.queryByTestId("jev-run-summary")).not.toBeInTheDocument();
  });

  it("tells a launcher that stopped writing apart from a game paused at launch", async () => {
    route("/api/jev/runs", runList(summary(RUN_A, HASH_A, "starting")));
    // The run has no heartbeat while it is held: the API reads it stale.
    serveRun(RUN_A, runState(RUN_A, HASH_A, { status: "starting", stale: true }), policyDoc(HASH_A));
    route(SESSION_URL, launchSession("starting", RUN_A, { updated_at: writtenAgo(5) }));
    openLaunch();
    await screen.findByRole("tree");
    await poll();
    // Heartbeats arrive: paused at launch, the run's missing heartbeat is expected.
    expect(screen.getByTestId("jev-launch-phase")).not.toHaveTextContent("not responding");
    expect(screen.queryByTestId("jev-stale-badge")).not.toBeInTheDocument();

    route(SESSION_URL, launchSession("starting", RUN_A, { updated_at: writtenAgo(60) }));
    await poll(2);
    expect(screen.getByTestId("jev-launch-phase")).toHaveTextContent("Launcher not responding");
    expect(screen.getByTestId("jev-launch-detail")).toHaveTextContent("may have been closed");
    expect(screen.getByTestId("jev-stale-badge")).toHaveTextContent("Stale"); // no longer hidden
    expect(screen.queryByTestId("jev-launch-starting")).not.toBeInTheDocument();
  });

  it("judges a running launch by its shown run, never by the session's age", async () => {
    route("/api/jev/runs", runList(summary(RUN_A, HASH_A, "starting")));
    // Released 200 s ago: in "running" the session is a one-shot stamp, not a heartbeat.
    route(SESSION_URL, launchSession("running", RUN_A, { updated_at: writtenAgo(200) }));
    const launching = { status: "starting", stale: true, updated_at: writtenAgo(30) };
    serveRun(RUN_A, runState(RUN_A, HASH_A, launching), policyDoc(HASH_A));
    openLaunch();
    await screen.findByRole("tree");
    await poll();
    const phase = screen.getByTestId("jev-launch-phase");
    expect(phase).toHaveTextContent("Starting"); // SC2 launching, within its bound
    expect(screen.queryByText(/not responding/)).not.toBeInTheDocument();
    route(
      `/api/jev/runs/${RUN_A}`,
      runState(RUN_A, HASH_A, { ...launching, updated_at: writtenAgo(400) }),
    );
    await poll(2);
    expect(phase).toHaveTextContent("Stale"); // no first observation in 300 s
    expect(screen.getByTestId("jev-launch-detail")).toHaveTextContent(
      "SC2 has not reported a first game observation",
    );
    expect(screen.getByTestId("jev-stale-badge")).toBeInTheDocument();
    route(`/api/jev/runs/${RUN_A}`, runState(RUN_A, HASH_A, { status: "running", stale: false }));
    await poll(2);
    expect(phase).toHaveTextContent("Live"); // the game's own heartbeat counts
    route(`/api/jev/runs/${RUN_A}`, runState(RUN_A, HASH_A, { status: "running", stale: true }));
    await poll(2);
    expect(phase).toHaveTextContent("Stale");
    expect(screen.getByTestId("jev-launch-detail")).toHaveTextContent("stopped writing heartbeats");
  });

  it("raises no alarm on a paused page during a long healthy game", async () => {
    route(
      "/api/jev/runs",
      runList(summary(RUN_A, HASH_A, "running"), summary(RUN_B, HASH_B, "finished")),
    );
    serveRun(RUN_A, runState(RUN_A, HASH_A, { status: "running", stale: false }), policyDoc(HASH_A));
    serveRun(RUN_B, runState(RUN_B, HASH_B, { status: "finished" }), policyDoc(HASH_B));
    // Twelve minutes into the game: the session was last written at release.
    route(SESSION_URL, launchSession("running", RUN_A, { updated_at: writtenAgo(720) }));
    openLaunch();
    await screen.findByRole("tree");
    await poll();
    expect(screen.getByTestId("jev-launch-phase")).toHaveTextContent("Live");
    fireEvent.change(screen.getByTestId("jev-run-select"), { target: { value: RUN_B } });
    expect(await screen.findByTestId("jev-run-id")).toHaveTextContent(RUN_B);
    await poll(3);
    expect(screen.getByTestId("jev-launch-phase")).toHaveTextContent("Following paused");
    expect(screen.queryByText(/not responding/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("jev-launch-paused-silent")).not.toBeInTheDocument();
  });

  it("alarms on a dead launcher at the barrier, whether the page follows or not", async () => {
    route(
      "/api/jev/runs",
      runList(summary(RUN_A, HASH_A, "starting"), summary(RUN_B, HASH_B, "finished")),
    );
    serveRun(RUN_A, runState(RUN_A, HASH_A, { status: "starting", stale: true }), policyDoc(HASH_A));
    serveRun(RUN_B, runState(RUN_B, HASH_B, { status: "finished" }), policyDoc(HASH_B));
    route(SESSION_URL, launchSession("starting", RUN_A, { updated_at: writtenAgo(60) }));
    openLaunch();
    await screen.findByRole("tree");
    await poll();
    expect(screen.getByTestId("jev-launch-phase")).toHaveTextContent("Launcher not responding");
    fireEvent.change(screen.getByTestId("jev-run-select"), { target: { value: RUN_B } });
    expect(await screen.findByTestId("jev-run-id")).toHaveTextContent(RUN_B);
    await poll();
    // Paused is decided first; the dead launcher is still said.
    expect(screen.getByTestId("jev-launch-phase")).toHaveTextContent("Waiting for this page");
    expect(screen.getByTestId("jev-launch-paused-silent")).toHaveTextContent("may have been closed");
  });
});

describe("launchPhase: every launch state x following/paused x fresh/old", () => {
  const RUN = "0a1b2c3d4e5f4a6b8c7d9e0f1a2b3c4d";
  const FRESH = 3;
  const OLD = 400; // older than every bound (20 s, 120 s, 300 s)

  function facts(state: string, following: boolean, age: number, acked = false): JevLaunchFacts {
    const active = state === "preparing" ? null : RUN;
    return {
      session: {
        session_id: "5d4c3b2a1f0e4d9c8b7a6f5e4d3c2b1a",
        active_run_id: active,
        state: state as JevLaunchState,
        case_index: 0,
        case_count: 1,
        updated_at: "2026-10-08T12:00:00.000+00:00",
        message: "",
      },
      sessionError: false,
      gaveUp: false,
      following,
      acknowledgedRunId: acked ? active : null,
      sessionAgeSeconds: age,
    };
  }

  const shown = (status: JevRunStatus, stale = false, ageSeconds = 10): JevShownRun => ({
    status,
    stale,
    ageSeconds,
  });

  type Row = [string, boolean, number, JevShownRun | null, boolean, JevLaunchPhase];
  const rows: Row[] = [
    // state, following, session age, shown run, acked, expected phase
    ["preparing", true, FRESH, null, false, "preparing"],
    ["preparing", true, OLD, null, false, "silent"],
    ["preparing", false, FRESH, null, false, "paused"],
    ["preparing", false, OLD, null, false, "paused"],
    ["starting", true, FRESH, shown("starting"), false, "preparing"],
    ["starting", true, FRESH, shown("starting"), true, "starting"],
    ["starting", true, OLD, shown("starting"), false, "silent"],
    ["starting", true, OLD, shown("starting"), true, "silent"],
    ["starting", false, FRESH, null, false, "waiting"],
    ["starting", false, OLD, null, false, "waiting"],
    ["running", true, FRESH, null, true, "starting"],
    ["running", true, OLD, null, true, "starting"],
    ["running", true, FRESH, shown("starting"), true, "starting"],
    ["running", true, OLD, shown("starting"), true, "starting"],
    ["running", true, FRESH, shown("starting", true, 400), true, "stale"],
    ["running", true, OLD, shown("starting", true, 400), true, "stale"],
    ["running", true, FRESH, shown("running"), true, "live"],
    ["running", true, OLD, shown("running"), true, "live"], // a long healthy game
    ["running", true, FRESH, shown("running", true), true, "stale"],
    ["running", true, OLD, shown("running", true), true, "stale"],
    ["running", true, OLD, shown("finished"), true, "finished"],
    ["running", false, FRESH, null, true, "paused"],
    ["running", false, OLD, null, true, "paused"], // paused during a long game: no alarm
    ["running", false, OLD, shown("running", true), true, "paused"],
    ["between_games", true, FRESH, null, true, "finished"],
    ["between_games", true, OLD, null, true, "silent"],
    ["between_games", false, FRESH, null, true, "paused"],
    ["between_games", false, OLD, null, true, "paused"],
    ["finished", true, FRESH, null, true, "finished"],
    ["finished", true, OLD, null, true, "finished"],
    ["finished", false, FRESH, null, true, "paused"],
    ["finished", false, OLD, null, true, "paused"],
    ["failed", true, FRESH, null, false, "failed"],
    ["failed", true, OLD, null, false, "failed"],
    ["failed", false, FRESH, null, false, "failed"],
    ["failed", false, OLD, null, false, "failed"],
    ["stopped", true, FRESH, null, false, "stopped"],
    ["stopped", true, OLD, null, false, "stopped"],
    ["stopped", false, FRESH, null, false, "stopped"],
    ["stopped", false, OLD, null, false, "stopped"],
  ];

  it.each(rows)(
    "%s, following=%s, session age %s s, shown run %o, acked=%s -> %s",
    (state, following, age, run, acked, expected) => {
      expect(launchPhase(facts(state, following, age, acked), run)).toBe(expected);
    },
  );

  it("covers every launch state, and an unreadable or abandoned session", () => {
    expect(new Set(rows.map((row) => row[0]))).toEqual(new Set(LAUNCH_STATES));
    const none = { ...facts("preparing", true, FRESH), session: null };
    expect(launchPhase(none, null)).toBe("preparing");
    expect(launchPhase({ ...none, sessionError: true }, null)).toBe("unavailable");
    expect(launchPhase({ ...facts("running", true, FRESH), gaveUp: true }, null)).toBe(
      "unavailable",
    );
  });

  it("asks the session's age nothing in running, and only it elsewhere", () => {
    const running = facts("running", true, OLD).session;
    expect(running).not.toBeNull();
    if (running === null) return;
    expect(launchLiveness(running, 10_000, null)).toBe("unknown");
    expect(launchLiveness(running, 10_000, shown("running"))).toBe("alive");
    expect(launchLiveness(running, 0, shown("running", true))).toBe("game_silent");
    expect(launchLiveness({ ...running, state: "starting" }, 21, null)).toBe("launcher_silent");
    expect(launchLiveness({ ...running, state: "starting" }, 19, null)).toBe("alive");
    expect(launchLiveness({ ...running, state: "preparing" }, 121, null)).toBe("launcher_silent");
    expect(launchLiveness({ ...running, state: "between_games" }, 119, null)).toBe("alive");
    expect(launchLiveness({ ...running, state: "finished" }, 10_000, null)).toBe("none");
    expect(launchLiveness({ ...running, state: "starting" }, null, null)).toBe("alive");
  });
});

// --- Plan J2 Step 221: lanes, observed activity, plans and node sources -----------

function v2PolicyDoc(hash: string): Doc {
  return { ...(JSON.parse(shippedV2PolicyText) as Doc), policy_hash: hash };
}

function diagnostic(sequence: number, nodeId: string, reason: string, facts: Doc, extra: Doc = {}) {
  return event(RUN_A, sequence, nodeId, {
    kind: "diagnostic",
    status: "success",
    reason,
    facts,
    ...extra,
  });
}

function activityEdges(): Array<[string | undefined, string | undefined]> {
  return Array.from(
    screen.getByTestId("jev-graph-activity").querySelectorAll<SVGPathElement>("path"),
  ).map((path) => [path.dataset.edge, path.dataset.activity]);
}

describe("JevTab observed activity (plan J2 Step 221)", () => {
  const policy = shippedPolicy();
  const nodes = policy.nodes as Array<{ id: string; children: string[] }>;
  const childOf = (id: string) => nodes.find((node) => node.id === id)?.children ?? [];
  const [armyFirst] = childOf("army");
  const [economyFirst, economySecond] = childOf("economy");

  it("draws the latest state's running path and node events of the last 2 game s", async () => {
    await renderRun(
      runState(RUN_A, HASH_A, {
        game_seconds: 12.5,
        active_nodes: ["economy", economySecond],
        recent_events: [
          // Too old for the trail (3.5 game s before the state): never drawn.
          event(RUN_A, 1, "army", {
            status: "running",
            game_seconds: 9.0,
            facts: { child: armyFirst },
          }),
          // Recorded 1 game s before the state: the recent trail.
          event(RUN_A, 2, "economy", {
            status: "running",
            game_seconds: 11.5,
            facts: { child: economyFirst },
          }),
          // A child that is not this node's: never an edge.
          event(RUN_A, 3, "army", {
            status: "running",
            game_seconds: 12.5,
            facts: { child: economyFirst },
          }),
        ],
      }),
    );
    expect(activityEdges()).toEqual([
      [`economy>${economySecond}`, "current"],
      [`economy>${economyFirst}`, "recent"],
    ]);
    expect(screen.getByTestId("jev-graph-active-lanes")).toHaveTextContent(
      "1 lane(s) active: economy",
    );
  });

  it("never invents a transition between two polls", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await renderRun(
      runState(RUN_A, HASH_A, { active_nodes: ["economy", economyFirst], game_seconds: 10 }),
    );
    expect(activityEdges()).toEqual([[`economy>${economyFirst}`, "current"]]);
    // The next poll: the lane moved on, with no event naming how it got there.
    route(
      `/api/jev/runs/${RUN_A}`,
      runState(RUN_A, HASH_A, { active_nodes: ["economy", economySecond], game_seconds: 11 }),
    );
    await act(() => vi.advanceTimersByTimeAsync(JEV_RUN_POLL_MS));
    expect(activityEdges()).toEqual([[`economy>${economySecond}`, "current"]]);
  });

  it("labels the activity of a stale or final run as last known and animates nothing", async () => {
    await renderRun(
      runState(RUN_A, HASH_A, {
        status: "finished",
        result: "win",
        active_nodes: ["economy", economyFirst],
      }),
    );
    const [path] = Array.from(
      screen.getByTestId("jev-graph-activity").querySelectorAll<SVGPathElement>("path"),
    );
    expect(path).toHaveAttribute("data-animated", "false");
    expect(path.querySelector("title")).toHaveTextContent("(last known)");
    expect(screen.getByTestId("jev-graph-active-lanes")).toHaveTextContent(
      "1 lane(s) active when the run ended: economy",
    );
    const economyCard = screen
      .getAllByTestId("jev-graph-lane")
      .find((card) => card.dataset.lane === "economy");
    expect(economyCard).toHaveAttribute("data-status", "active");
    expect(economyCard).toHaveAttribute("data-live", "false");
    expect(economyCard).toHaveTextContent("when ended");
  });

  it("keeps an old v1 archive's presentation: no plans panel and no invented sources", async () => {
    await renderRun(runState(RUN_A, HASH_A, { recent_events: [event(RUN_A, 1, "economy")] }));
    expect(screen.queryByTestId("jev-plans")).not.toBeInTheDocument();
    expect(screen.getByTestId("jev-decision-unavailable")).toBeInTheDocument();
    expect(screen.getAllByTestId("jev-graph-lane").map((chip) => chip.dataset.lane)).toEqual(
      policy.roots,
    );
    fireEvent.click(treeitem("economy"));
    expect(screen.getByTestId("jev-node-lane")).toHaveTextContent("economy");
    expect(screen.getByTestId("jev-node-sources-none")).toBeInTheDocument();
    expect(screen.getAllByRole("treeitem").every((item) => !item.hasAttribute("data-source"))).toBe(
      true,
    );
  });

  it("keeps an explicitly selected branch and node stable across polling updates", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await renderRun(
      runState(RUN_A, HASH_A, { active_nodes: ["economy", economyFirst], game_seconds: 10 }),
    );
    const armyCard = screen
      .getAllByTestId("jev-graph-lane")
      .find((card) => card.dataset.lane === "army") as HTMLElement;
    fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
    fireEvent.click(armyCard);
    fireEvent.click(treeitem("army"));
    expect(armyCard).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("jev-node-id")).toHaveTextContent("army");
    expect(treeitem("army")).toBeInTheDocument();

    route(
      `/api/jev/runs/${RUN_A}`,
      runState(RUN_A, HASH_A, {
        active_nodes: ["production"],
        waiting_nodes: ["construction"],
        game_seconds: 11,
      }),
    );
    await act(() => vi.advanceTimersByTimeAsync(JEV_RUN_POLL_MS));
    expect(armyCard).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("jev-node-id")).toHaveTextContent("army");
    expect(treeitem("army")).toBeInTheDocument();
  });

  it("keeps compact node codes stable when branches hide and boxes are restored", async () => {
    await renderRun(runState(RUN_A, HASH_A));
    expect(screen.getByRole("button", { name: "Nodes" })).toHaveAttribute("aria-pressed", "true");
    const armyCode = treeitem("army").querySelector("text")?.textContent;
    expect(armyCode).toMatch(/^A1/);
    fireEvent.click(screen.getAllByTestId("jev-graph-lane").find((card) => card.dataset.lane === "economy")!);
    expect(treeitem("army").querySelector("text")?.textContent).toBe(armyCode);
    fireEvent.click(treeitem("army"));
    expect(screen.getByTestId("jev-node-id")).toHaveTextContent("army");
    fireEvent.click(screen.getByRole("button", { name: "Boxes" }));
    expect(treeitem("army").querySelector("text")?.textContent).toBe("army");
    expect(treeitem("army")).toHaveAttribute("aria-selected", "true");
  });

  it("adds and removes branches independently, including an empty canvas", async () => {
    await renderRun(runState(RUN_A, HASH_A, { active_nodes: ["economy", economyFirst] }));
    const roots = () =>
      screen
        .getAllByRole("treeitem")
        .filter((node) => node.getAttribute("aria-level") === "1")
        .map((node) => node.dataset.lane);
    expect(roots()).toEqual(policy.roots);
    const cards = screen.getAllByTestId("jev-graph-lane");
    fireEvent.click(cards.find((card) => card.dataset.lane === "economy")!);
    expect(roots()).toEqual((policy.roots as string[]).filter((root) => root !== "economy"));
    fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(screen.queryAllByRole("treeitem")).toHaveLength(0);
    expect(screen.getByText(/Choose branch cards above/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("jev-full-graph"));
    expect(roots()).toEqual(policy.roots);
  });
});

describe("JevTab plans and decisions (plan J2 Step 221)", () => {
  const PLAN_TECH = `${RUN_A}:plan:1`;
  const PLAN_EXPAND = `${RUN_A}:plan:2`;
  const STAGE = "expansion.site_safe.wait";
  const RETREAT = "expansion.site_safe.retreat";
  const slotTurn = {
    slot: "macro",
    open_plan_id: PLAN_EXPAND,
    open_kind: "expand",
    cooldown_until: 265.0,
    yield_from: "tech",
    yield_to: ["expand"],
    yield_until: 270.0,
    disabled: {},
  };
  const expandFacts = {
    plan_id: PLAN_EXPAND,
    plan_node: "expansion.start.go",
    plan_root: "expansion",
    plan_kind: "expand",
    plan_status: "waiting",
    stage_node_id: STAGE,
    reason_code: "site_threatened",
    blocker: "waiting: 1 enemy seen near the expansion site",
    site: [58.5, 30.5],
    actor_tag: BIG_TAG,
    started_game_seconds: 280.0,
    last_progress_game_seconds: 290.0,
    deadline_game_seconds: 400.0,
    reserved_minerals: 75,
    reserved_vespene: 0,
    finished_game_seconds: null,
    decided_by: "typesafe",
    decision_request_id: 4,
    response: "delay",
    response_request_id: 6,
  };
  /** The expansion plan's own summary event (its PlanRecord facts plus the event's). */
  const expandSummary = {
    ...expandFacts,
    slot_turn: slotTurn,
    transition: "site_threatened",
    summary: true,
  };
  const kinds = [
    {
      kind: "expand",
      may_start: false,
      refusal:
        `the macro slot already holds open plan ${PLAN_EXPAND} (expand); ` +
        "never a duplicate plan",
    },
    {
      kind: "tech",
      may_start: false,
      refusal: "the macro slot yields to expand until 270.00s after a tech abort: one turn each",
    },
  ];
  /** A Plan slot summary whose ``plans`` entries are ``entries``. */
  const slotSummary = (sequence: number, entries: Doc[]) =>
    diagnostic(sequence, "tech", "Plan slot summary", {
      slot: "macro",
      slot_turn: slotTurn,
      kinds,
      plans: entries,
      summary: true,
    });
  const techFacts = {
    plan_id: PLAN_TECH,
    plan_node: "tech.start.go",
    plan_root: "tech",
    plan_kind: "tech",
    plan_status: "aborted",
    stage_node_id: "tech.reserve",
    reason_code: "recoveries_exhausted",
    blocker: "no_progress after 3 recovery attempts",
    started_game_seconds: 120.0,
    finished_game_seconds: 250.0,
    decided_by: "local_graph",
    decision_request_id: null,
    response: null,
    response_request_id: null,
  };
  // The runtime's real order within a tick: each open plan's summary, then the slot
  // summary, whose entries carry every PlanRecord fact; the decision diagnostics lead
  // the next tick.
  const events = [
    diagnostic(10, STAGE, "expand plan summary", expandSummary, { status: "running" }),
    slotSummary(11, [techFacts, expandFacts]),
    diagnostic(12, "tech", "Strategy decision source", {
      decision_provider: "typesafe",
      question_id: "strategy",
      source: "typesafe",
      choice: "expand",
      in_force_request_id: 4,
      reason: "accepted",
      pending: false,
      model: "jev-1.13.0",
      available_options: ["pressure", "recover"],
    }),
    diagnostic(13, "expansion", "Expansion response decision source", {
      question_id: "expansion_response",
      source: "typesafe",
      choice: "delay",
      in_force_request_id: 6,
      reason: "accepted",
      pending: true,
      pending_request_id: 7,
      model: "jev-1.13.0",
    }),
    event(RUN_A, 14, "expansion.site_safe.delay.chosen", {
      facts: {
        question_id: "expansion_response",
        choice: "delay",
        source: "typesafe",
        request_id: 6,
        plan: "expansion.start.go",
        applies: true,
        branch: "delay",
      },
    }),
    event(RUN_A, 15, RETREAT, {
      task_id: `${RUN_A}:9`,
      kind: "command",
      status: "issued",
      reason: "MOVE for task",
      facts: {
        intent_key: `${RETREAT}|x`,
        attempt: 1,
        decision_question_id: "expansion_response",
        decision_request_id: 6,
        decision_choice: "delay",
        decision_plan_id: PLAN_EXPAND,
      },
      action: { ability: "MOVE", actor_tags: [BIG_TAG], target: [40, 40] },
    }),
    diagnostic(16, "production", "Production mix decision source", {
      question_id: "production_mix",
      source: "local_fallback",
      choice: null,
      in_force_request_id: null,
      reason: "low_confidence",
      pending: false,
    }),
  ];

  async function renderV2(extra: Doc = {}): Promise<void> {
    await renderRun(
      runState(RUN_A, HASH_A, {
        version: 2,
        game_seconds: 291.0,
        waiting_nodes: ["expansion", STAGE],
        active_nodes: ["economy", "production"],
        recent_events: events,
        ...extra,
      }),
      v2PolicyDoc(HASH_A),
    );
  }

  it("shows each plan's stage, blocker or outcome and the decision behind it", async () => {
    await renderV2();
    const panel = screen.getByTestId("jev-plans");
    expect(within(panel).getByTestId("jev-plans-evidence")).toHaveTextContent("Live evidence");
    const rows = within(panel).getAllByTestId("jev-plan-row");
    expect(rows.map((row) => [row.dataset.planKind, row.dataset.planStatus])).toEqual([
      ["expand", "waiting"],
      ["tech", "aborted"],
    ]);
    const [expand, tech] = rows;
    expect(expand).toHaveTextContent(PLAN_EXPAND);
    expect(expand).toHaveTextContent(STAGE);
    expect(within(expand).getByTestId("jev-plan-blocker")).toHaveTextContent(
      "site_threatened: waiting: 1 enemy seen near the expansion site",
    );
    expect(within(expand).getByTestId("jev-plan-decided-by")).toHaveTextContent(
      "Typesafe (request 4)",
    );
    expect(expand).toHaveTextContent("delay (request 6)");
    expect(within(expand).getByTestId("jev-plan-holds")).toHaveTextContent(
      "deadline 400.0 s · holds 75 minerals, 0 gas",
    );
    expect(within(tech).getByTestId("jev-plan-blocker")).toHaveTextContent(
      "aborted (recoveries_exhausted) at 250.0 s",
    );
    expect(within(tech).getByTestId("jev-plan-decided-by")).toHaveTextContent("local graph");
  });

  // Iteration 2 regression: the slot summary follows the plan's own summary in the same
  // tick, so its entry must never blank the deadline and holds the plan event gave --
  // whether the entry carries every PlanRecord fact (today) or only the plan's identity
  // and status (an archive from before the entries carried them all).
  it.each([
    ["every PlanRecord fact", expandFacts],
    [
      "only identity and status",
      {
        plan_id: PLAN_EXPAND,
        plan_kind: "expand",
        plan_status: "waiting",
        stage_node_id: STAGE,
        reason_code: "site_threatened",
      },
    ],
  ])(
    "keeps an open plan's deadline and holds when a slot summary entry with %s follows it",
    async (_, entry) => {
      await renderV2({
        recent_events: [
          diagnostic(10, STAGE, "expand plan summary", expandSummary, { status: "running" }),
          slotSummary(11, [entry]),
          diagnostic(20, STAGE, "expand plan summary", expandSummary, { status: "running" }),
          slotSummary(21, [entry]),
        ],
      });
      const [row] = screen.getAllByTestId("jev-plan-row");
      expect(within(row).getByTestId("jev-plan-holds")).toHaveTextContent(
        "deadline 400.0 s · holds 75 minerals, 0 gas",
      );
      expect(within(row).getByTestId("jev-plan-decided-by")).toHaveTextContent(
        "Typesafe (request 4)",
      );
    },
  );

  it("explains the slot's turn and which kinds may start next", async () => {
    await renderV2();
    const slot = screen.getByTestId("jev-slot");
    expect(slot).toHaveAttribute("data-slot", "macro");
    expect(within(slot).getByTestId("jev-slot-open")).toHaveTextContent(
      `holds expand plan ${PLAN_EXPAND}`,
    );
    expect(within(slot).getByTestId("jev-slot-turn")).toHaveTextContent(
      "tech yields to expand until 270.0 s (passed)",
    );
    const kinds = within(slot).getAllByTestId("jev-slot-kind");
    expect(kinds.map((kind) => kind.dataset.mayStart)).toEqual(["false", "false"]);
    expect(kinds[1]).toHaveTextContent("tech: waits: the macro slot yields to expand");
    expect(screen.getByTestId("jev-strategy-available")).toHaveTextContent(
      "Strategy choices the graph can execute now: pressure, recover",
    );
  });

  it("names each question's source, choice, request and pending request", async () => {
    await renderV2();
    const rows = screen.getAllByTestId("jev-decision-row");
    expect(rows.map((row) => [row.dataset.question, row.dataset.source])).toEqual([
      ["strategy", "typesafe"],
      ["expansion_response", "typesafe"],
      ["production_mix", "local_fallback"],
    ]);
    expect(rows[0]).toHaveTextContent("Typesafe · model jev-1.13.0");
    expect(rows[1]).toHaveTextContent("delay");
    expect(rows[1]).toHaveTextContent("request 7");
    expect(rows[2]).toHaveTextContent("local fallback");
    expect(rows[2]).toHaveTextContent("low_confidence");
    const recent = screen.getAllByTestId("jev-recent-decision");
    expect(recent[0]).toHaveTextContent("production_mix — no choice recorded · local fallback");
    expect(recent[1]).toHaveTextContent("expansion_response → delay · Typesafe response");
    expect(recent[2]).toHaveTextContent("expansion_response → delay · Typesafe response");
  });

  it("shows a plan's stage in the graph and names the request behind its nodes", async () => {
    await renderV2();
    const stageName = new RegExp(`^${STAGE.replace(/\./g, "\\.")},`);
    expect(screen.queryByRole("treeitem", { name: stageName })).toBeNull();
    fireEvent.click(screen.getAllByTestId("jev-plan-stage")[0]);
    const expansionCard = screen
      .getAllByTestId("jev-graph-lane")
      .find((card) => card.dataset.lane === "expansion");
    expect(expansionCard).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("jev-node-id")).toHaveTextContent(STAGE);
    const stage = treeitem(STAGE); // its collapsed branch was expanded
    expect(stage).toHaveAttribute("data-status", "waiting");
    expect(stage).toHaveAttribute("data-source", "hosted");
    expect(stage.querySelector("title")).toHaveTextContent("Typesafe (request 6)");
    const list = screen.getByTestId("jev-node-sources");
    const sources = within(list).getAllByTestId("jev-node-source");
    expect(sources[0]).toHaveTextContent(
      `plan ${PLAN_EXPAND} (expand) expansion_response = delay: Typesafe (request 6)`,
    );
    // The guards this node runs under, with what each last recorded.
    const guards = screen.getAllByTestId("jev-node-guard");
    expect(guards.map((guard) => guard.textContent?.split(" ")[0])).toEqual([
      "expansion.site_safe.delay.chosen",
      "expansion.site_safe.delay.local",
    ]);
    expect(guards[0]).toHaveTextContent("success at 3.5 s (event 14)");
    expect(guards[0]).toHaveTextContent("Typesafe (request 6)");
    expect(guards[1]).toHaveTextContent("no recent evaluation recorded");
    // The command its sibling sent carries the same request: request -> plan -> command.
    fireEvent.click(within(guards[0]).getByRole("button"));
    expect(screen.getByTestId("jev-node-id")).toHaveTextContent(
      "expansion.site_safe.delay.chosen",
    );
    expect(treeitem(RETREAT)).toHaveAttribute("data-source", "hosted");
    expect(treeitem(RETREAT)).toHaveAccessibleDescription(
      `lane expansion, source: command ${RUN_A}:9 created under expansion_response = delay ` +
        `for plan ${PLAN_EXPAND}: Typesafe (request 6)`,
    );
  });

  it("labels every plan and decision of a stale run as last known", async () => {
    await renderV2({ stale: true });
    expect(screen.getByTestId("jev-plans-evidence")).toHaveTextContent("Last known evidence");
    expect(screen.getAllByTestId("jev-plan-status")[0]).toHaveTextContent("waiting (last known)");
    expect(screen.getAllByTestId("jev-decision-row")[1]).toHaveTextContent(
      "request 7 (when recorded)",
    );
  });
});
