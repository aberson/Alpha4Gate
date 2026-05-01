import {
  describe,
  it,
  expect,
  vi,
  beforeEach,
  afterEach,
} from "vitest";
import { renderHook, waitFor, cleanup } from "@testing-library/react";

// IMPORTANT: hoist the cache-mock state via vi.hoisted so the
// vi.mock factory (also hoisted) can close over it without TDZ.
// Tests mutate `mockCacheStore` to seed pre-existing cache entries.
const mockState = vi.hoisted(() => {
  const store: Map<string, { data: unknown; fetchedAt: number }> = new Map();
  const writes: Array<{ key: string; data: unknown }> = [];
  return { store, writes };
});

vi.mock("../lib/idbCache", () => ({
  readCache: vi.fn(async (key: string) => {
    const entry = mockState.store.get(key);
    if (entry === undefined) return null;
    return { endpoint: key, data: entry.data, fetchedAt: entry.fetchedAt };
  }),
  writeCache: vi.fn(async (key: string, data: unknown) => {
    mockState.writes.push({ key, data });
    mockState.store.set(key, { data, fetchedAt: Date.now() });
  }),
  deleteCache: vi.fn(async () => undefined),
  clearCache: vi.fn(async () => undefined),
}));

import { useEvolveRun } from "./useEvolveRun";
import type { RunningRoundsResponse } from "./useEvolveRun";

// --- Test fixtures ---

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

const idleRunState = {
  status: "idle",
  parent_start: null,
  parent_current: null,
  started_at: null,
  wall_budget_hours: null,
  generation_index: null,
  generations_completed: null,
  generations_promoted: null,
  evictions: null,
  resurrections_remaining: null,
  pool_remaining_count: null,
  last_result: null,
  run_id: null,
  concurrency: null,
};

const idleControl = { stop_run: false, pause_after_round: false };
const idlePool = {
  parent: null,
  generated_at: null,
  generation: 0,
  pool: [],
};
const idleResults = { rounds: [] };
const idleCurrentRound = {
  active: false,
  generation: null,
  phase: null,
  imp_title: null,
  imp_rank: null,
  imp_index: null,
  candidate: null,
  stacked_titles: [],
  new_parent: null,
  prior_parent: null,
  games_played: null,
  games_total: null,
  score_cand: null,
  score_parent: null,
  updated_at: null,
};

const idleRunningRounds: RunningRoundsResponse = {
  active: false,
  concurrency: null,
  run_id: null,
  rounds: [],
};

const populatedRunningRounds: RunningRoundsResponse = {
  active: true,
  concurrency: 3,
  run_id: "20260430-1200",
  rounds: [
    {
      worker_id: 0,
      active: true,
      phase: "fitness",
      imp_title: "Chrono Boost",
      candidate: "cand_aaa",
      parent: "v4",
      games_played: 2,
      games_total: 5,
      score_cand: 1,
      score_parent: 1,
      updated_at: "2026-04-30T12:01:00+00:00",
    },
    {
      worker_id: 1,
      active: true,
      phase: "fitness",
      imp_title: "Forward Pylon",
      candidate: "cand_bbb",
      parent: "v4",
      games_played: 0,
      games_total: 5,
      score_cand: 0,
      score_parent: 0,
      updated_at: "2026-04-30T12:01:00+00:00",
    },
    {
      worker_id: 2,
      active: false,
      phase: null,
      imp_title: null,
      candidate: null,
      parent: null,
      games_played: null,
      games_total: null,
      score_cand: null,
      score_parent: null,
      updated_at: null,
    },
  ],
};

interface MockOverrides {
  runningRounds?: RunningRoundsResponse;
}

// Tracks fetch calls per-URL so polling-cadence tests can count them.
interface FetchTracker {
  calls: string[];
}

function installFetch(
  overrides: MockOverrides = {},
  tracker?: FetchTracker,
): ReturnType<typeof vi.spyOn> {
  const runningRounds = overrides.runningRounds ?? idleRunningRounds;
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      tracker?.calls.push(url);
      if (url.includes("/api/evolve/running-rounds")) {
        return jsonResponse(runningRounds);
      }
      if (url.includes("/api/evolve/current-round")) {
        return jsonResponse(idleCurrentRound);
      }
      if (url.includes("/api/evolve/state")) {
        return jsonResponse(idleRunState);
      }
      if (url.includes("/api/evolve/control")) {
        return jsonResponse(idleControl);
      }
      if (url.includes("/api/evolve/pool")) {
        return jsonResponse(idlePool);
      }
      if (url.includes("/api/evolve/results")) {
        return jsonResponse(idleResults);
      }
      return jsonResponse({});
    });
}

describe("useEvolveRun", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn> | null = null;

  beforeEach(() => {
    mockState.store.clear();
    mockState.writes.length = 0;
  });

  afterEach(() => {
    fetchSpy?.mockRestore();
    fetchSpy = null;
    cleanup();
    vi.useRealTimers();
  });

  it("populates runningRounds from the network response", async () => {
    fetchSpy = installFetch({ runningRounds: populatedRunningRounds });

    const { result } = renderHook(() => useEvolveRun());

    await waitFor(() => {
      expect(result.current.runningRounds.data).not.toBeNull();
    });

    const data = result.current.runningRounds.data;
    expect(data?.active).toBe(true);
    expect(data?.concurrency).toBe(3);
    expect(data?.run_id).toBe("20260430-1200");
    expect(data?.rounds).toHaveLength(3);
    expect(data?.rounds[0].worker_id).toBe(0);
    expect(data?.rounds[0].imp_title).toBe("Chrono Boost");
    expect(data?.rounds[2].active).toBe(false);
  });

  it("does not crash when an old-shape v4 cache entry is present (cache-key bump invalidates it)", async () => {
    // Seed the OLD v4 cache key with a deliberately-broken shape from
    // before the running-rounds endpoint existed. If the hook's cache
    // key were still v4, useApi would readCache(...) this object and
    // hand it to consumers; the v5 bump keeps it isolated.
    //
    // Per feedback_useapi_cache_schema_break.md, the failure mode is
    // "Cannot read properties of undefined" when a consumer destructures
    // the missing fields before the network round-trip completes.
    const brokenV4Shape = {
      // Old shape: pretend nothing about runningRounds existed; if this
      // ever flowed into the hook's typed result for the running-rounds
      // endpoint, downstream `.rounds.map(...)` would throw.
      legacy_field: "stale",
    };
    mockState.store.set(
      "/api/evolve/running-rounds::evolve-v4",
      { data: brokenV4Shape, fetchedAt: Date.now() - 60_000 },
    );

    fetchSpy = installFetch({ runningRounds: populatedRunningRounds });

    // Tap unhandled errors to detect any "Cannot read properties of
    // undefined" surfaced from React render.
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const { result } = renderHook(() => useEvolveRun());

    // The hook should reach a fresh (network-sourced) value and never
    // see the v4-shape blob.
    await waitFor(() => {
      expect(result.current.runningRounds.data).not.toBeNull();
    });

    // Verify the data is the FRESH shape, not the stale v4 blob.
    const data = result.current.runningRounds.data;
    expect(data?.run_id).toBe("20260430-1200");
    expect(data?.rounds).toHaveLength(3);
    // Defensive: the broken-shape sentinel must not have leaked
    // through (TS wouldn't let us read it, but runtime might have).
    expect((data as unknown as { legacy_field?: string }).legacy_field).toBeUndefined();

    // No render-time React errors logged.
    const reactErrors = errorSpy.mock.calls.filter((args) => {
      const msg = args.map((a) => String(a)).join(" ");
      return /Cannot read propert/.test(msg);
    });
    expect(reactErrors).toHaveLength(0);

    errorSpy.mockRestore();
  });

  it("fetches the running-rounds endpoint on mount", async () => {
    const tracker: FetchTracker = { calls: [] };
    fetchSpy = installFetch({ runningRounds: idleRunningRounds }, tracker);

    renderHook(() => useEvolveRun());

    // First-mount fetch only — assert the running-rounds URL is in
    // the initial fetch fan-out. (Polling is exercised in the next
    // test with fake timers.)
    await waitFor(() => {
      expect(
        tracker.calls.some((u) => u.includes("/api/evolve/running-rounds")),
      ).toBe(true);
    });
  });

  it("polls the running-rounds endpoint at the 2000ms cadence", async () => {
    // Use fake timers to advance past pollMs without real wall-clock
    // wait. We don't fake them in the previous tests because waitFor
    // uses real microtasks.
    vi.useFakeTimers();
    const tracker: FetchTracker = { calls: [] };
    fetchSpy = installFetch({ runningRounds: idleRunningRounds }, tracker);

    renderHook(() => useEvolveRun());

    // Flush the mount-time fetches and the cache-read promise chain.
    // Multiple awaits because useApi has a few microtask hops.
    for (let i = 0; i < 5; i++) {
      await vi.advanceTimersByTimeAsync(0);
    }

    const initial = tracker.calls.filter((u) =>
      u.includes("/api/evolve/running-rounds"),
    ).length;
    expect(initial).toBeGreaterThanOrEqual(1);

    // Advance just under one poll interval; should NOT trigger another
    // running-rounds fetch.
    await vi.advanceTimersByTimeAsync(1500);
    const beforeTick = tracker.calls.filter((u) =>
      u.includes("/api/evolve/running-rounds"),
    ).length;
    expect(beforeTick).toBe(initial);

    // Advance to (and past) the 2000ms poll interval; one more call
    // should have fired.
    await vi.advanceTimersByTimeAsync(700);
    const afterTick = tracker.calls.filter((u) =>
      u.includes("/api/evolve/running-rounds"),
    ).length;
    expect(afterTick).toBeGreaterThan(initial);
  });
});
