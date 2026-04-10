import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup, within } from "@testing-library/react";
import { TriggerControls } from "./TriggerControls";
import type {
  DaemonStatus,
  DaemonConfigShape,
  UseDaemonStatusResult,
} from "../hooks/useDaemonStatus";

// --- Mock useDaemonStatus -------------------------------------------------

const refreshMock = vi.fn();

let currentHookResult: UseDaemonStatusResult = {
  status: null,
  triggers: null,
  loading: true,
  error: null,
  refresh: refreshMock,
};

vi.mock("../hooks/useDaemonStatus", async () => {
  const actual = await vi.importActual<typeof import("../hooks/useDaemonStatus")>(
    "../hooks/useDaemonStatus",
  );
  return {
    ...actual,
    useDaemonStatus: (): UseDaemonStatusResult => currentHookResult,
  };
});

function setHookResult(partial: Partial<UseDaemonStatusResult>): void {
  currentHookResult = { ...currentHookResult, ...partial };
}

// --- Fetch mock helpers ---------------------------------------------------

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

type FetchCall = { url: string; init?: RequestInit };

interface RouteMap {
  [pattern: string]: unknown;
}

/**
 * Build a fetch mock that matches URL substrings against a map of canned
 * responses. Records every call in `calls` so tests can assert on method +
 * body.
 */
function buildFetchMock(
  routes: RouteMap,
  calls: FetchCall[],
): (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> {
  return async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push({ url, init });
    for (const pattern of Object.keys(routes)) {
      if (url.includes(pattern)) {
        return jsonResponse(routes[pattern]);
      }
    }
    return jsonResponse({});
  };
}

const baseConfig: DaemonConfigShape = {
  check_interval_seconds: 60,
  min_transitions: 500,
  min_hours_since_last: 1.0,
  cycles_per_run: 5,
  games_per_cycle: 10,
  current_difficulty: 2,
  max_difficulty: 8,
  win_rate_threshold: 0.75,
};

function statusRunning(): DaemonStatus {
  return {
    running: true,
    state: "training",
    last_run: null,
    next_check: null,
    runs_completed: 3,
    last_result: null,
    last_error: null,
    last_rollback: null,
    config: { ...baseConfig },
  };
}

function statusStopped(): DaemonStatus {
  return {
    running: false,
    state: "idle",
    last_run: null,
    next_check: null,
    runs_completed: 3,
    last_result: null,
    last_error: null,
    last_rollback: null,
    config: { ...baseConfig },
  };
}

const defaultRoutes: RouteMap = {
  "/api/training/checkpoints": {
    checkpoints: [
      { name: "v1", file: "v1.zip" },
      { name: "v2", file: "v2.zip" },
    ],
    best: "v1",
  },
  "/api/training/curriculum": {
    current_difficulty: 2,
    max_difficulty: 8,
    win_rate_threshold: 0.75,
    last_advancement: null,
  },
  "/api/training/daemon/config": { status: "updated", config: baseConfig },
  "/api/training/start": { status: "started" },
  "/api/training/stop": { status: "stopped" },
  "/api/training/evaluate": { job_id: "job-42", status: "pending" },
  "/api/training/promote": {
    status: "promoted",
    checkpoint: "v1",
    old_best: "v0",
  },
  "/api/training/rollback": {
    status: "rolled_back",
    old_best: "v1",
    new_best: "v0",
  },
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  refreshMock.mockClear();
  currentHookResult = {
    status: null,
    triggers: null,
    loading: false,
    error: null,
    refresh: refreshMock,
  };
});

beforeEach(() => {
  setHookResult({
    status: statusStopped(),
    triggers: null,
    loading: false,
    error: null,
  });
});

function installFetch(routes: RouteMap = defaultRoutes): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(buildFetchMock(routes, calls));
  return calls;
}

async function renderAndWait(): Promise<FetchCall[]> {
  const calls = installFetch();
  render(<TriggerControls />);
  // Wait for on-mount fetches (checkpoints + curriculum) to settle.
  await waitFor(() => {
    expect(calls.some((c) => c.url.includes("/api/training/checkpoints"))).toBe(true);
    expect(calls.some((c) => c.url.includes("/api/training/curriculum"))).toBe(true);
  });
  return calls;
}

describe("TriggerControls", () => {
  it("disables Start button when daemon is running", async () => {
    setHookResult({ status: statusRunning() });
    await renderAndWait();
    const start = screen.getByRole("button", { name: /start daemon/i });
    const stop = screen.getByRole("button", { name: /stop daemon/i });
    expect(start).toBeDisabled();
    expect(stop).not.toBeDisabled();
  });

  it("disables Stop button when daemon is stopped", async () => {
    setHookResult({ status: statusStopped() });
    await renderAndWait();
    const start = screen.getByRole("button", { name: /start daemon/i });
    const stop = screen.getByRole("button", { name: /stop daemon/i });
    expect(start).not.toBeDisabled();
    expect(stop).toBeDisabled();
  });

  it("populates config form from useDaemonStatus and saves via PUT", async () => {
    setHookResult({ status: statusStopped() });
    const calls = await renderAndWait();

    // Inputs pre-populated from status.config
    const minTransitions = screen.getByRole("spinbutton", {
      name: /min transitions/i,
    }) as HTMLInputElement;
    expect(minTransitions.value).toBe("500");

    const winRate = screen.getByRole("spinbutton", {
      name: /win rate threshold/i,
    }) as HTMLInputElement;
    expect(winRate.value).toBe("0.75");

    // Submit the form
    fireEvent.click(screen.getByRole("button", { name: /save config/i }));

    await waitFor(() => {
      expect(
        calls.some(
          (c) =>
            c.url.includes("/api/training/daemon/config") &&
            c.init?.method === "PUT",
        ),
      ).toBe(true);
    });

    const putCall = calls.find(
      (c) =>
        c.url.includes("/api/training/daemon/config") &&
        c.init?.method === "PUT",
    );
    expect(putCall).toBeDefined();
    const body = JSON.parse(putCall?.init?.body as string) as DaemonConfigShape;
    expect(body.min_transitions).toBe(500);
    expect(body.win_rate_threshold).toBe(0.75);
    // saved banner appears
    await waitFor(() => {
      expect(screen.getByText(/^saved$/i)).toBeInTheDocument();
    });
  });

  it("rejects out-of-range win_rate_threshold with an error message", async () => {
    setHookResult({ status: statusStopped() });
    const calls = await renderAndWait();

    const winRate = screen.getByRole("spinbutton", {
      name: /win rate threshold/i,
    }) as HTMLInputElement;
    fireEvent.change(winRate, { target: { value: "1.5" } });

    fireEvent.click(screen.getByRole("button", { name: /save config/i }));

    // Error shown, no PUT issued
    expect(
      screen.getByText(/win rate threshold.*between 0 and 1/i),
    ).toBeInTheDocument();
    expect(
      calls.some(
        (c) =>
          c.url.includes("/api/training/daemon/config") &&
          c.init?.method === "PUT",
      ),
    ).toBe(false);
  });

  it("shows ConfirmDialog for promote and calls POST on confirm", async () => {
    setHookResult({ status: statusStopped() });
    const calls = await renderAndWait();

    // Wait for checkpoints to be loaded into dropdown
    await waitFor(() => {
      const select = screen.getByRole("combobox", {
        name: /checkpoint/i,
      }) as HTMLSelectElement;
      expect(select.value).toBe("v1");
    });

    fireEvent.click(screen.getByRole("button", { name: /^promote$/i }));

    // Dialog visible
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText(/promote checkpoint\?/i)).toBeInTheDocument();

    // Confirm
    fireEvent.click(within(dialog).getByRole("button", { name: /^promote$/i }));

    await waitFor(() => {
      expect(
        calls.some(
          (c) =>
            c.url.includes("/api/training/promote") &&
            c.init?.method === "POST",
        ),
      ).toBe(true);
    });
    const promoteCall = calls.find(
      (c) =>
        c.url.includes("/api/training/promote") && c.init?.method === "POST",
    );
    const body = JSON.parse(promoteCall?.init?.body as string) as { checkpoint: string };
    expect(body.checkpoint).toBe("v1");
  });

  it("shows destructive ConfirmDialog for rollback", async () => {
    setHookResult({ status: statusStopped() });
    await renderAndWait();

    await waitFor(() => {
      const select = screen.getByRole("combobox", {
        name: /checkpoint/i,
      }) as HTMLSelectElement;
      expect(select.value).toBe("v1");
    });

    fireEvent.click(screen.getByRole("button", { name: /^rollback$/i }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText(/rollback checkpoint\?/i)).toBeInTheDocument();
    // Destructive styling on confirm button
    const confirmBtn = within(dialog).getByRole("button", { name: /^rollback$/i });
    expect(confirmBtn.className).toContain("destructive");
  });

  it("calls PUT /api/training/curriculum after confirming override", async () => {
    setHookResult({ status: statusStopped() });
    const calls = await renderAndWait();

    const newDiff = screen.getByRole("spinbutton", {
      name: /new difficulty/i,
    }) as HTMLInputElement;
    fireEvent.change(newDiff, { target: { value: "5" } });

    fireEvent.click(screen.getByRole("button", { name: /^set$/i }));

    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(/override curriculum\?/i),
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /^set$/i }));

    await waitFor(() => {
      expect(
        calls.some(
          (c) =>
            c.url.includes("/api/training/curriculum") &&
            c.init?.method === "PUT",
        ),
      ).toBe(true);
    });
    const putCall = calls.find(
      (c) =>
        c.url.includes("/api/training/curriculum") && c.init?.method === "PUT",
    );
    const body = JSON.parse(putCall?.init?.body as string) as {
      current_difficulty: number;
    };
    expect(body.current_difficulty).toBe(5);
  });

  it("renders form with defaults when useDaemonStatus is still loading", async () => {
    setHookResult({ status: null, loading: true });
    await renderAndWait();
    // Form should still render with default values
    const checkInterval = screen.getByRole("spinbutton", {
      name: /check interval/i,
    }) as HTMLInputElement;
    expect(checkInterval.value).toBe("60");
    // Start button disabled while loading
    expect(
      screen.getByRole("button", { name: /start daemon/i }),
    ).toBeDisabled();
  });

  it("calls POST /api/training/evaluate with checkpoint, games, difficulty", async () => {
    setHookResult({ status: statusStopped() });
    const calls = await renderAndWait();

    await waitFor(() => {
      const select = screen.getByRole("combobox", {
        name: /checkpoint/i,
      }) as HTMLSelectElement;
      expect(select.value).toBe("v1");
    });

    fireEvent.click(screen.getByRole("button", { name: /^evaluate$/i }));

    await waitFor(() => {
      expect(
        calls.some(
          (c) =>
            c.url.includes("/api/training/evaluate") &&
            c.init?.method === "POST",
        ),
      ).toBe(true);
    });
    const call = calls.find(
      (c) =>
        c.url.includes("/api/training/evaluate") && c.init?.method === "POST",
    );
    const body = JSON.parse(call?.init?.body as string) as {
      checkpoint: string;
      games: number;
      difficulty: number;
    };
    expect(body.checkpoint).toBe("v1");
    expect(body.games).toBe(10);
    expect(body.difficulty).toBe(1);
    // Job ID surfaces in the UI
    await waitFor(() => {
      expect(screen.getByText(/job-42/)).toBeInTheDocument();
    });
  });

  it("dedupes checkpoints by name when backend returns evaluation history", async () => {
    setHookResult({ status: statusStopped() });
    // Backend returns multiple entries per name (one per evaluation run),
    // mirroring the real GET /api/training/checkpoints payload. The dropdown
    // should render each unique name exactly once.
    const routes: RouteMap = {
      ...defaultRoutes,
      "/api/training/checkpoints": {
        checkpoints: [
          { name: "v1", file: "v1.zip", metadata: { cycle: 1, total_games: 1, win_rate: 1.0 } },
          { name: "v1", file: "v1.zip", metadata: { cycle: 1, total_games: 3, win_rate: 0.833 } },
          { name: "v2", file: "v2.zip", metadata: { cycle: 2, total_games: 5, win_rate: 0.6 } },
          { name: "v1", file: "v1.zip", metadata: { cycle: 1, total_games: 7, win_rate: 0.71 } },
          { name: "v2", file: "v2.zip", metadata: { cycle: 2, total_games: 9, win_rate: 0.55 } },
        ],
        best: "v1",
      },
    };
    const calls: FetchCall[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(buildFetchMock(routes, calls));
    render(<TriggerControls />);

    await waitFor(() => {
      expect(calls.some((c) => c.url.includes("/api/training/checkpoints"))).toBe(true);
    });

    // Wait for the selected checkpoint to populate from the response.
    await waitFor(() => {
      const select = screen.getByRole("combobox", {
        name: /checkpoint/i,
      }) as HTMLSelectElement;
      expect(select.value).toBe("v1");
    });

    const select = screen.getByRole("combobox", {
      name: /checkpoint/i,
    }) as HTMLSelectElement;
    const options = within(select).getAllByRole("option") as HTMLOptionElement[];

    // Exactly 2 unique options, one per unique name, in backend order.
    expect(options).toHaveLength(2);
    expect(options.map((o) => o.value)).toEqual(["v1", "v2"]);
    expect(options.map((o) => o.textContent)).toEqual(["v1", "v2"]);
  });
});
