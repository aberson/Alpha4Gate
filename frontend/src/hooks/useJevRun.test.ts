import { describe, it, expect, vi, beforeEach, afterEach, type MockInstance } from "vitest";
import { renderHook, act, cleanup } from "@testing-library/react";
import {
  JEV_REQUEST_TIMEOUT_MS,
  JEV_RUN_LIST_POLL_MS,
  JEV_RUN_POLL_MS,
  useJevRun,
} from "./useJevRun";

/**
 * useJevRun polling contract (plan section 5): the selected run every
 * second, the run list every five seconds, only while mounted. Runs never
 * answer with a body the hook would render here: these tests count and
 * inspect requests, so responses only need to keep the hook polling.
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
