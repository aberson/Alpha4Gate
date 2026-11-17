import { describe, it, expect, vi, beforeEach, afterEach, type MockInstance } from "vitest";
import { renderHook, act, cleanup } from "@testing-library/react";
import {
  JEV_LAUNCH_MAX_FAILURES,
  JEV_LAUNCH_POLL_MS,
  JEV_REQUEST_TIMEOUT_MS,
  JEV_RUN_LIST_POLL_MS,
  JEV_RUN_POLL_MS,
  parseJevLink,
  useJevRun,
} from "./useJevRun";

/**
 * useJevRun polling contract (plan section 5): the selected run every
 * second, the run list every five seconds, only while mounted. The first
 * group's runs answer with a body the hook rejects, because those tests only
 * count and inspect requests.
 *
 * The plan D7 group serves complete runs: ``?run=`` selects exactly one run;
 * ``?launch=`` follows a launch session's exact runs from a background-safe
 * ticker, and acknowledges each run only once it is rendered.
 */

const RUN_ID = "0a1b2c3d4e5f4a6b8c7d9e0f1a2b3c4d";
const RUNS_URL = "/api/jev/runs";
const STATE_URL = `${RUNS_URL}/${RUN_ID}`;
const POLICY_URL = `${STATE_URL}/policy`;

const RUN_LIST = {
  schema_version: 1,
  runs: [
    {
      run_id: RUN_ID,
      family: "jev",
      version: 1,
      policy_hash: "a".repeat(64),
      status: "running",
      updated_at: "2026-10-08T12:00:00.000+00:00",
    },
  ],
  truncated: false,
  omitted: 0,
};

const POLICY = {
  schema_version: 1,
  family: "jev",
  version: 1,
  roots: ["economy"],
  parameters: {},
  nodes: [
    {
      id: "economy",
      label: "Economy",
      kind: "condition",
      children: [],
      operation: "count_compare",
      args: {},
    },
  ],
  policy_hash: "a".repeat(64),
};

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

let fetchSpy: MockInstance<typeof fetch>;
let stateResponder: (signal: AbortSignal) => Promise<Response>;

function count(url: string): number {
  return fetchSpy.mock.calls.filter((call) => String(call[0]) === url).length;
}

beforeEach(() => {
  vi.useFakeTimers();
  // The state body is irrelevant here (it fails validation, so nothing renders).
  stateResponder = async () => jsonResponse({});
  fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === RUNS_URL) return jsonResponse(RUN_LIST);
    if (url === POLICY_URL) return jsonResponse(POLICY);
    if (url === STATE_URL) return stateResponder(init?.signal as AbortSignal);
    return jsonResponse({}, 404);
  });
});

afterEach(() => {
  fetchSpy.mockRestore();
  vi.useRealTimers();
  cleanup();
});

/** Mount the hook and let the first list response select the newest run. */
async function mountPolling() {
  const hook = renderHook(() => useJevRun());
  await act(() => vi.advanceTimersByTimeAsync(0));
  return hook;
}

describe("useJevRun polling", () => {
  it("polls the selected run every second and the run list every five seconds", async () => {
    const { result } = await mountPolling();
    expect(result.current.selectedRunId).toBe(RUN_ID);
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    // The first request of each, then one per elapsed interval.
    expect(count(RUNS_URL)).toBe(3);
    expect(count(STATE_URL)).toBe(11);
  });

  it("fetches the immutable archived policy only until it loads", async () => {
    const { result } = await mountPolling();
    await act(() => vi.advanceTimersByTimeAsync(5 * JEV_RUN_POLL_MS));
    expect(count(POLICY_URL)).toBe(1);
    expect(result.current.policy.data?.roots).toEqual(["economy"]);
  });

  it("never overlaps a slow state request with the next poll", async () => {
    let finish: (response: Response) => void = () => {};
    stateResponder = () => new Promise((resolve) => (finish = resolve));
    await mountPolling();
    // Several poll ticks pass while the request is in flight (short of its timeout).
    await act(() => vi.advanceTimersByTimeAsync(JEV_REQUEST_TIMEOUT_MS - 1));
    expect(count(STATE_URL)).toBe(1);
    await act(async () => finish(jsonResponse({})));
    await act(() => vi.advanceTimersByTimeAsync(JEV_RUN_POLL_MS));
    expect(count(STATE_URL)).toBe(2);
  });

  it("stops polling and aborts in-flight requests on unmount", async () => {
    const signals: AbortSignal[] = [];
    stateResponder = (signal) => {
      signals.push(signal);
      return new Promise(() => {});
    };
    const { unmount } = await mountPolling();
    const requests = fetchSpy.mock.calls.length;
    unmount();
    expect(signals).toHaveLength(1);
    expect(signals[0].aborted).toBe(true);
    await act(() => vi.advanceTimersByTimeAsync(3 * JEV_RUN_LIST_POLL_MS));
    expect(fetchSpy.mock.calls.length).toBe(requests);
  });

  it("ignores a run id that is not lowercase UUID4 hex", async () => {
    const { result } = await mountPolling();
    act(() => result.current.selectRun("../../pyproject.toml"));
    await act(() => vi.advanceTimersByTimeAsync(JEV_RUN_POLL_MS));
    expect(result.current.selectedRunId).toBe(RUN_ID);
    expect(fetchSpy.mock.calls.some((call) => String(call[0]).includes(".."))).toBe(false);
  });
});

// --- Launch links, session following and the rendered-ready acknowledgment (plan D7) ---

const SESSION = "5d4c3b2a1f0e4d9c8b7a6f5e4d3c2b1a";
const RUN_A = "0a1b2c3d4e5f4a6b8c7d9e0f1a2b3c4d";
const RUN_B = "9f8e7d6c5b4a4f3e9d2c1b0a9f8e7d6c";
const NEWEST = "1234567890ab4cde8f0123456789abcd";
const HASH = "a".repeat(64);
const SESSION_URL = `/api/jev/launches/${SESSION}`;
const READY_URL = `${SESSION_URL}/ready`;

function session(state: string, activeRunId: string | null, caseIndex = 0, extra = {}) {
  return {
    schema_version: 1,
    session_id: SESSION,
    active_run_id: activeRunId,
    state,
    case_index: caseIndex,
    case_count: 2,
    updated_at: new Date().toISOString(), // written just now by a live launcher
    message: "",
    ...extra,
  };
}

function runState(runId: string, status = "starting") {
  return {
    schema_version: 1,
    run_id: runId,
    family: "jev",
    version: 1,
    policy_hash: HASH,
    status,
    updated_at: "2026-10-08T12:00:00.000+00:00",
    game_seconds: 0,
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
      policy_hash: HASH,
      source_commit: null,
      map: "Simple64",
      opponent_race: "Terran",
      difficulty: 3,
      seed: 11,
      max_game_seconds: 900,
      max_wall_seconds: 1200,
      replay_path: null,
    },
  };
}

const NEWEST_LIST = {
  schema_version: 1,
  runs: [
    {
      run_id: NEWEST,
      family: "jev",
      version: 1,
      policy_hash: HASH,
      status: "running",
      updated_at: "2026-10-08T12:00:00.000+00:00",
    },
  ],
  truncated: false,
  omitted: 0,
};

type Route = (init: RequestInit | undefined) => Promise<Response> | Response;

describe("parseJevLink", () => {
  it("lets a launch win over a run, and never falls back on an invalid id", () => {
    expect(parseJevLink("")).toEqual({ kind: "newest" });
    expect(parseJevLink("?tab=jev")).toEqual({ kind: "newest" });
    expect(parseJevLink(`?tab=jev&run=${RUN_A}`)).toEqual({ kind: "run", runId: RUN_A });
    expect(parseJevLink(`?tab=jev&run=${RUN_A}&launch=${SESSION}`)).toEqual({
      kind: "launch",
      sessionId: SESSION,
    });
    expect(parseJevLink("?tab=jev&run=../../x")).toEqual({ kind: "invalid", param: "run" });
    expect(parseJevLink(`?launch=${SESSION.toUpperCase()}&run=${RUN_A}`)).toEqual({
      kind: "invalid",
      param: "launch",
    });
  });
});

describe("useJevRun launch following", () => {
  let routes: Map<string, Route>;
  let posts: { url: string; body: unknown; headers: HeadersInit | undefined }[];

  function setRoute(url: string, route: Route | object): void {
    routes.set(url, typeof route === "function" ? (route as Route) : () => jsonResponse(route));
  }

  beforeEach(() => {
    routes = new Map();
    posts = [];
    setRoute(RUNS_URL, NEWEST_LIST);
    for (const id of [RUN_A, RUN_B, NEWEST]) {
      setRoute(`${RUNS_URL}/${id}`, runState(id));
      setRoute(`${RUNS_URL}/${id}/policy`, { ...POLICY, policy_hash: HASH });
    }
    setRoute(READY_URL, (init) => {
      const body = JSON.parse(String(init?.body)) as { run_id: string };
      return jsonResponse({ schema_version: 1, ready: true, session_id: SESSION, run_id: body.run_id });
    });
    fetchSpy.mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === "POST") {
        posts.push({ url, body: JSON.parse(String(init.body)), headers: init.headers });
      }
      const route = routes.get(url);
      return route === undefined ? jsonResponse({}, 404) : route(init);
    });
  });

  async function mountLaunch(search = `?tab=jev&launch=${SESSION}`) {
    const hook = renderHook(() => useJevRun(search));
    await act(() => vi.advanceTimersByTimeAsync(0));
    return hook;
  }

  it("selects nothing (never the newest run) until the session names its run", async () => {
    setRoute(SESSION_URL, session("preparing", null));
    const { result } = await mountLaunch();
    expect(result.current.link).toEqual({ kind: "launch", sessionId: SESSION });
    await act(() => vi.advanceTimersByTimeAsync(2 * JEV_RUN_LIST_POLL_MS));
    expect(result.current.runs.data?.runs[0].run_id).toBe(NEWEST);
    expect(result.current.selectedRunId).toBeNull();
    expect(count(`${RUNS_URL}/${NEWEST}`)).toBe(0);
    expect(count(SESSION_URL)).toBeGreaterThanOrEqual(10); // polled every second
  });

  it("acknowledges only the session's exact starting run once rendered, exactly once", async () => {
    setRoute(SESSION_URL, session("starting", RUN_A));
    const { result } = await mountLaunch();
    await act(() => vi.advanceTimersByTimeAsync(JEV_LAUNCH_POLL_MS));
    expect(result.current.selectedRunId).toBe(RUN_A);
    expect(result.current.run.data?.run_id).toBe(RUN_A);
    expect(posts).toEqual([]); // loaded is not rendered: nothing is acknowledged yet
    act(() => result.current.reportRendered(RUN_B, HASH)); // another run's view
    act(() => result.current.reportRendered(RUN_A, "b".repeat(64))); // another policy
    await act(() => vi.advanceTimersByTimeAsync(2 * JEV_LAUNCH_POLL_MS));
    expect(posts).toEqual([]);
    act(() => result.current.reportRendered(RUN_A, HASH));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(posts.map(({ url, body }) => ({ url, body }))).toEqual([
      { url: READY_URL, body: { run_id: RUN_A, policy_hash: HASH } },
    ]);
    expect(result.current.launch?.acknowledgedRunId).toBe(RUN_A);
    await act(() => vi.advanceTimersByTimeAsync(3 * JEV_LAUNCH_POLL_MS));
    expect(posts).toHaveLength(1);
  });

  it("retries a refused acknowledgment on the next poll and shows why", async () => {
    setRoute(SESSION_URL, session("starting", RUN_A));
    let refuse = true;
    setRoute(READY_URL, () =>
      refuse
        ? jsonResponse(
            { schema_version: 1, error: { code: "launch_not_ready", message: "not starting" } },
            409,
          )
        : jsonResponse({ schema_version: 1, ready: true, session_id: SESSION, run_id: RUN_A }),
    );
    const { result } = await mountLaunch();
    await act(() => vi.advanceTimersByTimeAsync(JEV_LAUNCH_POLL_MS));
    act(() => result.current.reportRendered(RUN_A, HASH));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.launch?.ackError).toMatchObject({
      kind: "http",
      status: 409,
      code: "launch_not_ready",
    });
    refuse = false;
    await act(() => vi.advanceTimersByTimeAsync(JEV_LAUNCH_POLL_MS));
    expect(result.current.launch?.acknowledgedRunId).toBe(RUN_A);
    expect(result.current.launch?.ackError).toBeNull();
  });

  it("follows the next game of the same session, never a newer unrelated run", async () => {
    setRoute(SESSION_URL, session("running", RUN_A));
    const { result } = await mountLaunch();
    expect(result.current.selectedRunId).toBe(RUN_A);
    setRoute(SESSION_URL, session("starting", RUN_B, 1));
    await act(() => vi.advanceTimersByTimeAsync(JEV_LAUNCH_POLL_MS));
    expect(result.current.selectedRunId).toBe(RUN_B);
    expect(result.current.run.data?.run_id).toBe(RUN_B);
    expect(count(`${RUNS_URL}/${NEWEST}`)).toBe(0);
  });

  it("pauses following on a manual pick, sends nothing, and resumes on request", async () => {
    setRoute(SESSION_URL, session("between_games", RUN_A));
    const { result } = await mountLaunch();
    act(() => result.current.selectRun(NEWEST));
    expect(result.current.launch?.following).toBe(false);
    setRoute(SESSION_URL, session("starting", RUN_B, 1));
    await act(() => vi.advanceTimersByTimeAsync(2 * JEV_LAUNCH_POLL_MS));
    expect(result.current.selectedRunId).toBe(NEWEST); // still the user's pick
    act(() => result.current.reportRendered(NEWEST, HASH));
    await act(() => vi.advanceTimersByTimeAsync(JEV_LAUNCH_POLL_MS));
    expect(posts).toEqual([]); // the launcher keeps waiting for this page
    act(() => result.current.resumeLive());
    expect(result.current.launch?.following).toBe(true);
    expect(result.current.selectedRunId).toBe(RUN_B);
    await act(() => vi.advanceTimersByTimeAsync(0));
    act(() => result.current.reportRendered(RUN_B, HASH));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(posts.map((post) => post.body)).toEqual([{ run_id: RUN_B, policy_hash: HASH }]);
  });

  it("drops a late state response for the previous game once the session moved on", async () => {
    setRoute(SESSION_URL, session("running", RUN_A));
    let finishA: (response: Response) => void = () => {};
    setRoute(`${RUNS_URL}/${RUN_A}`, () => new Promise((resolve) => (finishA = resolve)));
    const { result } = await mountLaunch();
    expect(result.current.selectedRunId).toBe(RUN_A);
    setRoute(SESSION_URL, session("starting", RUN_B, 1));
    await act(() => vi.advanceTimersByTimeAsync(JEV_LAUNCH_POLL_MS));
    expect(result.current.selectedRunId).toBe(RUN_B);
    await act(async () => finishA(jsonResponse(runState(RUN_A, "finished"))));
    expect(result.current.run.data?.run_id).toBe(RUN_B);
  });

  it("stops at once on an answer that cannot change, never substituting a run", async () => {
    setRoute(SESSION_URL, () =>
      jsonResponse(
        { schema_version: 1, error: { code: "launch_not_found", message: "no launch session" } },
        404,
      ),
    );
    const { result } = await mountLaunch();
    expect(result.current.launch?.session.error).toMatchObject({
      kind: "http",
      status: 404,
      code: "launch_not_found",
    });
    expect(result.current.launch?.gaveUp).toBe(true);
    await act(() => vi.advanceTimersByTimeAsync(10 * JEV_LAUNCH_POLL_MS));
    expect(count(SESSION_URL)).toBe(1);
    expect(result.current.selectedRunId).toBeNull();
  });

  it("retries failing session polls boundedly, never substituting a run", async () => {
    setRoute(SESSION_URL, () =>
      jsonResponse(
        { schema_version: 1, error: { code: "corrupt_launch", message: "session.json" } },
        503,
      ),
    );
    const { result } = await mountLaunch();
    expect(result.current.launch?.session.error).toMatchObject({
      kind: "http",
      status: 503,
      code: "corrupt_launch",
    });
    expect(result.current.launch?.gaveUp).toBe(false);
    await act(() => vi.advanceTimersByTimeAsync(JEV_LAUNCH_MAX_FAILURES * JEV_LAUNCH_POLL_MS));
    expect(result.current.launch?.gaveUp).toBe(true);
    const attempts = count(SESSION_URL);
    expect(attempts).toBe(JEV_LAUNCH_MAX_FAILURES);
    await act(() => vi.advanceTimersByTimeAsync(10 * JEV_LAUNCH_POLL_MS));
    expect(count(SESSION_URL)).toBe(attempts);
    expect(result.current.selectedRunId).toBeNull();
  });

  it("never reads a run error code as a launch error, and stops polling an ended session", async () => {
    setRoute(SESSION_URL, () =>
      jsonResponse({ schema_version: 1, error: { code: "corrupt_run", message: "x" } }, 503),
    );
    const { result } = await mountLaunch();
    expect(result.current.launch?.session.error).toMatchObject({ status: 503, code: null });
    setRoute(SESSION_URL, session("finished", RUN_A));
    await act(() => vi.advanceTimersByTimeAsync(JEV_LAUNCH_POLL_MS));
    const polls = count(SESSION_URL);
    await act(() => vi.advanceTimersByTimeAsync(5 * JEV_LAUNCH_POLL_MS));
    expect(count(SESSION_URL)).toBe(polls);
    expect(result.current.selectedRunId).toBe(RUN_A); // the finished game stays shown
  });

  it("aborts an acknowledgment in flight and stops polling on unmount", async () => {
    setRoute(SESSION_URL, session("starting", RUN_A));
    const signals: AbortSignal[] = [];
    setRoute(READY_URL, (init) => {
      signals.push(init?.signal as AbortSignal);
      return new Promise(() => {});
    });
    const { result, unmount } = await mountLaunch();
    await act(() => vi.advanceTimersByTimeAsync(JEV_LAUNCH_POLL_MS));
    act(() => result.current.reportRendered(RUN_A, HASH));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(signals).toHaveLength(1);
    unmount();
    expect(signals[0].aborted).toBe(true);
    const requests = fetchSpy.mock.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(5 * JEV_LAUNCH_POLL_MS));
    expect(fetchSpy.mock.calls.length).toBe(requests);
  });

  it("selects exactly a ?run link, and nothing for an invalid link", async () => {
    const linked = renderHook(() => useJevRun(`?tab=jev&run=${RUN_A}`));
    await act(() => vi.advanceTimersByTimeAsync(2 * JEV_RUN_LIST_POLL_MS));
    expect(linked.result.current.selectedRunId).toBe(RUN_A);
    expect(linked.result.current.launch).toBeNull();
    linked.unmount();
    const invalid = renderHook(() => useJevRun("?tab=jev&run=../../secrets"));
    await act(() => vi.advanceTimersByTimeAsync(2 * JEV_RUN_LIST_POLL_MS));
    expect(invalid.result.current.link).toEqual({ kind: "invalid", param: "run" });
    expect(invalid.result.current.selectedRunId).toBeNull();
    expect(fetchSpy.mock.calls.some((call) => String(call[0]).includes(".."))).toBe(false);
    expect(count(`${RUNS_URL}/${NEWEST}`)).toBe(0);
  });
});

describe("useJevRun launch following, more", () => {
  let routes: Map<string, () => Promise<Response> | Response>;

  beforeEach(() => {
    routes = new Map();
    routes.set(RUNS_URL, () => jsonResponse(NEWEST_LIST));
    for (const id of [RUN_A, RUN_B, NEWEST]) {
      routes.set(`${RUNS_URL}/${id}`, () => jsonResponse(runState(id)));
      routes.set(`${RUNS_URL}/${id}/policy`, () => jsonResponse({ ...POLICY, policy_hash: HASH }));
    }
    fetchSpy.mockImplementation(async (input) => {
      const route = routes.get(String(input));
      return route === undefined ? jsonResponse({}, 404) : route();
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps following when the user picks the launch's own run", async () => {
    routes.set(SESSION_URL, () => jsonResponse(session("running", RUN_A)));
    const { result } = renderHook(() => useJevRun(`?tab=jev&launch=${SESSION}`));
    await act(() => vi.advanceTimersByTimeAsync(0));
    act(() => result.current.selectRun(NEWEST));
    expect(result.current.launch?.following).toBe(false);
    act(() => result.current.selectRun(RUN_A));
    expect(result.current.launch?.following).toBe(true);
    routes.set(SESSION_URL, () => jsonResponse(session("starting", RUN_B, 1)));
    await act(() => vi.advanceTimersByTimeAsync(JEV_LAUNCH_POLL_MS));
    expect(result.current.selectedRunId).toBe(RUN_B); // still following
  });

  it("re-reads the run list at once when the session names a new run", async () => {
    routes.set(SESSION_URL, () => jsonResponse(session("preparing", null)));
    renderHook(() => useJevRun(`?tab=jev&launch=${SESSION}`));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(count(RUNS_URL)).toBe(1);
    routes.set(SESSION_URL, () => jsonResponse(session("starting", RUN_A)));
    await act(() => vi.advanceTimersByTimeAsync(JEV_LAUNCH_POLL_MS));
    expect(count(RUNS_URL)).toBe(2); // not five seconds later: the picker lists the new run
    await act(() => vi.advanceTimersByTimeAsync(JEV_LAUNCH_POLL_MS));
    expect(count(RUNS_URL)).toBe(2); // the same run does not re-read it again
  });

  it("measures how long ago the launcher last wrote the session", async () => {
    const written = new Date(Date.now() - 30_000).toISOString();
    routes.set(SESSION_URL, () =>
      jsonResponse(session("starting", RUN_A, 0, { updated_at: written })),
    );
    const { result } = renderHook(() => useJevRun(`?tab=jev&launch=${SESSION}`));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.launch?.sessionAgeSeconds).toBeGreaterThanOrEqual(30);
    expect(result.current.launch?.sessionAgeSeconds).toBeLessThan(32);
  });

  it("polls the session from a worker tick and at once when the page is shown again", async () => {
    const workers: FakeWorker[] = [];
    class FakeWorker {
      onmessage: ((event: MessageEvent) => void) | null = null;
      interval: unknown = null;
      terminated = false;
      url: string;
      constructor(url: string) {
        this.url = url;
        workers.push(this);
      }
      postMessage(data: unknown) {
        this.interval = data;
      }
      terminate() {
        this.terminated = true;
      }
      tick() {
        this.onmessage?.(new MessageEvent("message", { data: 0 }));
      }
    }
    vi.stubGlobal("Worker", FakeWorker);
    const createObjectURL = vi.fn(() => "blob:ticker");
    const revokeObjectURL = vi.fn();
    class FakeURL extends URL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    }
    vi.stubGlobal("URL", FakeURL);
    routes.set(SESSION_URL, () => jsonResponse(session("preparing", null)));
    const { unmount } = renderHook(() => useJevRun(`?tab=jev&launch=${SESSION}`));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(workers).toHaveLength(1);
    expect(workers[0].interval).toBe(JEV_LAUNCH_POLL_MS);
    // Page timers no longer drive it: a minute of (throttled) page time adds nothing.
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(count(SESSION_URL)).toBe(1);
    for (let i = 0; i < 3; i += 1) {
      await act(async () => workers[0].tick());
    }
    expect(count(SESSION_URL)).toBe(4);
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(count(SESSION_URL)).toBe(5);
    visibility.mockReturnValue("hidden");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(count(SESSION_URL)).toBe(5); // hiding does not poll
    unmount();
    expect(workers[0].terminated).toBe(true);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:ticker");
    visibility.mockRestore();
  });

  it("falls back to the page's timer when the worker cannot load", async () => {
    const workers: FailingWorker[] = [];
    class FailingWorker {
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      terminated = false;
      constructor() {
        workers.push(this);
      }
      postMessage() {}
      terminate() {
        this.terminated = true;
      }
    }
    vi.stubGlobal("Worker", FailingWorker);
    const revokeObjectURL = vi.fn();
    class FakeURL extends URL {
      static createObjectURL = vi.fn(() => "blob:ticker");
      static revokeObjectURL = revokeObjectURL;
    }
    vi.stubGlobal("URL", FakeURL);
    routes.set(SESSION_URL, () => jsonResponse(session("preparing", null)));
    const { unmount } = renderHook(() => useJevRun(`?tab=jev&launch=${SESSION}`));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(count(SESSION_URL)).toBe(1);
    // The script is refused after construction: an asynchronous error event.
    await act(async () => workers[0].onerror?.(new Event("error")));
    expect(workers[0].terminated).toBe(true);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:ticker");
    await act(() => vi.advanceTimersByTimeAsync(3 * JEV_LAUNCH_POLL_MS));
    expect(count(SESSION_URL)).toBe(4); // the page's own timer keeps the session followed
    unmount();
    await act(() => vi.advanceTimersByTimeAsync(3 * JEV_LAUNCH_POLL_MS));
    expect(count(SESSION_URL)).toBe(4); // and stops with the hook
  });
});
