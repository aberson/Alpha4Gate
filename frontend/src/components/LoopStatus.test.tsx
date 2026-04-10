import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { LoopStatus } from "./LoopStatus";
import type { DaemonStatus, TriggerState } from "../hooks/useDaemonStatus";

/**
 * Mock fetch response helper. Returns a fetch-like Response object that
 * matches what useDaemonStatus expects (ok + json()).
 */
function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

type FetchFn = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

/**
 * Build a fetch mock that routes /api/training/daemon and
 * /api/training/triggers to the given payloads.
 */
function mockFetch(
  daemon: DaemonStatus | Error,
  triggers: TriggerState | Error,
): FetchFn {
  return async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/training/daemon")) {
      if (daemon instanceof Error) throw daemon;
      return jsonResponse(daemon);
    }
    if (url.includes("/api/training/triggers")) {
      if (triggers instanceof Error) throw triggers;
      return jsonResponse(triggers);
    }
    throw new Error(`Unexpected fetch: ${url}`);
  };
}

const idleStatus: DaemonStatus = {
  running: true,
  state: "idle",
  last_run: null,
  next_check: "2026-04-09T12:00:00+00:00",
  runs_completed: 0,
  last_result: null,
  last_error: null,
  last_rollback: null,
  config: {
    check_interval_seconds: 60,
    min_transitions: 1000,
    min_hours_since_last: 6,
    cycles_per_run: 5,
    current_difficulty: 3,
    max_difficulty: 7,
    win_rate_threshold: 0.8,
  },
};

const trainingStatus: DaemonStatus = {
  running: true,
  state: "training",
  last_run: "2026-04-09T11:30:00+00:00",
  next_check: "2026-04-09T12:30:00+00:00",
  runs_completed: 7,
  last_result: { cycles: 5, win_rate: 0.72, final_difficulty: 4 },
  last_error: null,
  last_rollback: null,
  config: {
    check_interval_seconds: 60,
    min_transitions: 1000,
    min_hours_since_last: 6,
    cycles_per_run: 5,
    current_difficulty: 4,
    max_difficulty: 7,
    win_rate_threshold: 0.8,
  },
};

const errorStatus: DaemonStatus = {
  ...idleStatus,
  last_error: "CUDA out of memory: tried to allocate 2.00 GiB",
};

const triggerNo: TriggerState = {
  transitions_since_last: 120,
  hours_since_last: 1.5,
  would_trigger: false,
  reason: "no trigger condition met",
};

const triggerYes: TriggerState = {
  transitions_since_last: 1500,
  hours_since_last: 7.0,
  would_trigger: true,
  reason: "transition count trigger: 1500 >= 1000",
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  // Default no-op so accidental leaks don't hit the network
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => jsonResponse({}));
});

describe("LoopStatus", () => {
  it("renders loading state before first fetch resolves", () => {
    // Return a never-resolving promise so loading stays true
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () => new Promise(() => undefined),
    );
    render(<LoopStatus />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("renders error state when fetch fails", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(new Error("network down"), new Error("network down")),
    );
    render(<LoopStatus />);
    await waitFor(() => {
      expect(screen.getByText(/error:/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/network down/i)).toBeInTheDocument();
  });

  it("renders error state when fetch returns non-200", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/training/daemon")) {
        return jsonResponse({ detail: "bad" }, false, 500);
      }
      return jsonResponse(triggerNo);
    });
    render(<LoopStatus />);
    await waitFor(() => {
      expect(screen.getByText(/error:/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/500/)).toBeInTheDocument();
  });

  it("renders idle daemon state with stat cards", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(idleStatus, triggerNo),
    );
    render(<LoopStatus />);
    await waitFor(() => {
      expect(screen.getByText("Training Loop")).toBeInTheDocument();
    });
    // State badge shows "idle"
    expect(screen.getByText("idle")).toBeInTheDocument();
    // runs_completed
    expect(screen.getByText("Runs Completed")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
    // last_run null fallback
    const lastRunLabel = screen.getByText("Last Run");
    expect(lastRunLabel).toBeInTheDocument();
    // Trigger NO badge
    expect(screen.getByText("NO")).toBeInTheDocument();
    expect(screen.getByText(/no trigger condition met/)).toBeInTheDocument();
  });

  it("renders training daemon state with last_result card", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(trainingStatus, triggerYes),
    );
    render(<LoopStatus />);
    await waitFor(() => {
      expect(screen.getByText("Training Loop")).toBeInTheDocument();
    });
    // State badge shows "training"
    expect(screen.getByText("training")).toBeInTheDocument();
    // runs_completed
    expect(screen.getByText("7")).toBeInTheDocument();
    // Would trigger YES
    expect(screen.getByText("YES")).toBeInTheDocument();
    // last_result section
    expect(screen.getByText("Last Result")).toBeInTheDocument();
    const lastResultLabel = screen.getByText("Last Training Result");
    const lastResultCard = lastResultLabel.parentElement;
    expect(lastResultCard).not.toBeNull();
    expect(lastResultCard?.textContent).toContain("cycles=5");
    expect(lastResultCard?.textContent).toContain("win_rate=72.0%");
    expect(lastResultCard?.textContent).toContain("final_difficulty=4");
  });

  it("renders last_error block in red when non-null", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(errorStatus, triggerNo),
    );
    render(<LoopStatus />);
    await waitFor(() => {
      expect(screen.getByText("Training Loop")).toBeInTheDocument();
    });
    expect(screen.getByText("Last Error")).toBeInTheDocument();
    const errNode = screen.getByRole("alert");
    expect(errNode).toHaveTextContent(/CUDA out of memory/);
    // Inline style includes the error red color. jsdom normalizes
    // the hex #e74c3c to rgb(231, 76, 60), so match either form.
    const styleAttr = errNode.getAttribute("style") ?? "";
    expect(styleAttr).toMatch(/#e74c3c|rgb\(231,\s*76,\s*60\)/i);
  });

  it("does not render last_error block when last_error is null", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(idleStatus, triggerNo),
    );
    render(<LoopStatus />);
    await waitFor(() => {
      expect(screen.getByText("Training Loop")).toBeInTheDocument();
    });
    expect(screen.queryByText("Last Error")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
