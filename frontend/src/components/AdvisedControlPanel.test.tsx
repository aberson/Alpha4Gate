import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import { AdvisedControlPanel } from "./AdvisedControlPanel";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

type FetchFn = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

function mockFetch(
  state: Record<string, unknown>,
  control: Record<string, unknown> = {},
): FetchFn {
  return async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/advised/state")) {
      return jsonResponse(state);
    }
    if (url.includes("/api/advised/control")) {
      if (init?.method === "PUT") {
        return jsonResponse({ ...control, ...JSON.parse(init.body as string) });
      }
      return jsonResponse(control);
    }
    // Other endpoints called by useApi (e.g. training/status for ConnectionStatus)
    return jsonResponse({});
  };
}

const idleState = { status: "idle" };

const runningState = {
  run_id: "20260412-1832",
  status: "running",
  phase: 2,
  phase_name: "Strategic Analysis",
  iteration: 3,
  games_per_cycle: 10,
  difficulty: 1,
  mode: "training",
  hours_budget: 4,
  elapsed_seconds: 3600,
  baseline_win_rate: 0.6,
  current_win_rate: 0.8,
  iterations: [
    { num: 1, title: "Reward scouting", result: "pass", delta: "+10%" },
    { num: 2, title: "Fix supply block", result: "fail", delta: "-5%" },
  ],
  current_improvement: "Chrono boost allocation",
  fail_streak: 0,
  updated_at: "2026-04-12T19:15:00Z",
};

const defaultControl = {
  games_per_cycle: null,
  user_hint: null,
  stop_run: false,
  reset_loop: false,
  difficulty: null,
  fail_threshold: null,
  reward_rule_add: null,
  updated_at: null,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => jsonResponse({}));
});

describe("AdvisedControlPanel", () => {
  it("renders idle state with guidance text", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(idleState, defaultControl),
    );
    render(<AdvisedControlPanel />);
    await waitFor(() => {
      expect(screen.getByText("Advisor Control Panel")).toBeInTheDocument();
    });
    expect(screen.getByText(/no advised run active/i)).toBeInTheDocument();
    expect(screen.getByText(/improve-bot-advised/)).toBeInTheDocument();
  });

  it("renders running state with status cards", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(runningState, defaultControl),
    );
    render(<AdvisedControlPanel />);
    await waitFor(() => {
      expect(screen.getByText("Advisor Control Panel")).toBeInTheDocument();
    });
    // Status badge
    expect(screen.getByText("running")).toBeInTheDocument();
    // Phase info
    expect(screen.getByText(/Strategic Analysis/)).toBeInTheDocument();
    // Iteration count
    expect(screen.getByText("3")).toBeInTheDocument();
    // Win rates (displayed as "60% -> 80%")
    expect(screen.getByText(/60% -> 80%/)).toBeInTheDocument();
    // Current improvement
    expect(screen.getByText("Chrono boost allocation")).toBeInTheDocument();
    // Iteration history table
    expect(screen.getByText("Reward scouting")).toBeInTheDocument();
    expect(screen.getByText("Fix supply block")).toBeInTheDocument();
  });

  it("renders progress bar with correct percentage", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(runningState, defaultControl),
    );
    render(<AdvisedControlPanel />);
    await waitFor(() => {
      expect(screen.getByText(/60m \/ 240m/)).toBeInTheDocument();
    });
    expect(screen.getByText(/25%/)).toBeInTheDocument();
  });

  it("shows loop control inputs when running", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(runningState, defaultControl),
    );
    render(<AdvisedControlPanel />);
    await waitFor(() => {
      expect(screen.getByText("Loop Controls")).toBeInTheDocument();
    });
    expect(screen.getByText(/Games per cycle \(1-50\)/)).toBeInTheDocument();
    expect(screen.getByText(/Difficulty \(1-10\)/)).toBeInTheDocument();
    expect(screen.getByText(/Fail threshold \(5-80%\)/)).toBeInTheDocument();
  });

  it("shows strategic guidance textarea when running", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(runningState, defaultControl),
    );
    render(<AdvisedControlPanel />);
    await waitFor(() => {
      expect(screen.getByText("Strategic Guidance")).toBeInTheDocument();
    });
    expect(screen.getByPlaceholderText(/attack walk/i)).toBeInTheDocument();
    expect(screen.getByText("Send to Advisor")).toBeInTheDocument();
  });

  it("shows pending hint when control has user_hint", async () => {
    const controlWithHint = { ...defaultControl, user_hint: "Try proxy gateway" };
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(runningState, controlWithHint),
    );
    render(<AdvisedControlPanel />);
    await waitFor(() => {
      expect(screen.getByText(/PENDING HINT/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Try proxy gateway/)).toBeInTheDocument();
  });

  it("shows stop and reset buttons when active", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(runningState, defaultControl),
    );
    render(<AdvisedControlPanel />);
    await waitFor(() => {
      expect(screen.getByText("Stop Run")).toBeInTheDocument();
    });
    expect(screen.getByText("Reset Loop")).toBeInTheDocument();
    // Buttons should be enabled for active run
    expect(screen.getByText("Stop Run")).not.toBeDisabled();
    expect(screen.getByText("Reset Loop")).not.toBeDisabled();
  });

  it("stop button opens confirm dialog", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(runningState, defaultControl),
    );
    render(<AdvisedControlPanel />);
    await waitFor(() => {
      expect(screen.getByText("Stop Run")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Stop Run"));
    await waitFor(() => {
      expect(screen.getByText("Stop advised run?")).toBeInTheDocument();
    });
  });

  it("reset button opens confirm dialog", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(runningState, defaultControl),
    );
    render(<AdvisedControlPanel />);
    await waitFor(() => {
      expect(screen.getByText("Reset Loop")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Reset Loop"));
    await waitFor(() => {
      expect(screen.getByText("Reset training loop?")).toBeInTheDocument();
    });
  });

  it("shows reward rule form when running", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(runningState, defaultControl),
    );
    render(<AdvisedControlPanel />);
    await waitFor(() => {
      expect(screen.getByText("Add Reward Rule")).toBeInTheDocument();
    });
    expect(screen.getByPlaceholderText(/attack-walk-reward/)).toBeInTheDocument();
  });
});
