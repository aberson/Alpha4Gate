import { describe, it, expect, vi, beforeEach, afterEach, type MockInstance } from "vitest";
import { render, screen, cleanup, fireEvent, within, act } from "@testing-library/react";
import { JevTab } from "./JevTab";
import { JEV_REQUEST_TIMEOUT_MS, JEV_RUN_POLL_MS } from "../hooks/useJevRun";
import { JEV_LIMITS, JEV_SCHEMA_VERSION } from "../types/jev";
// The archived policy the API serves is the packaged policy document plus
// its hash, so the policy responses below use the real shipped document.
import shippedPolicyText from "../../../bots/jev/v1/policy.json?raw";

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
