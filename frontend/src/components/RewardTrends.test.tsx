import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  cleanup,
  fireEvent,
  within,
} from "@testing-library/react";
import {
  RewardTrends,
  buildChartData,
  sortRules,
} from "./RewardTrends";
import type {
  RewardTrendRule,
  RewardTrendsResponse,
} from "./RewardTrends";

/**
 * Stub recharts. jsdom doesn't compute layout so real recharts components
 * render nothing useful and flood the console with ResponsiveContainer
 * warnings. We replace them with minimal divs so the component under test
 * still mounts but the tests focus on data wiring (table, sort, selector).
 */
vi.mock("recharts", () => {
  const Passthrough = ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="recharts-stub">{children}</div>
  );
  const Empty = () => <div data-testid="recharts-empty" />;
  return {
    LineChart: Passthrough,
    Line: Empty,
    XAxis: Empty,
    YAxis: Empty,
    CartesianGrid: Empty,
    Tooltip: Empty,
    Legend: Empty,
    ResponsiveContainer: Passthrough,
  };
});

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

const emptyResponse: RewardTrendsResponse = {
  rules: [],
  n_games: 0,
  generated_at: "2026-04-09T10:00:00+00:00",
};

const ruleAlpha: RewardTrendRule = {
  rule_id: "alpha_rule",
  total_contribution: 12.5,
  contribution_per_game: 2.5,
  points: [
    { game_id: "001", timestamp: "2026-04-09T09:00:00+00:00", contribution: 3.0 },
    { game_id: "002", timestamp: "2026-04-09T09:10:00+00:00", contribution: 2.5 },
    { game_id: "003", timestamp: "2026-04-09T09:20:00+00:00", contribution: 2.0 },
    { game_id: "004", timestamp: "2026-04-09T09:30:00+00:00", contribution: 2.5 },
    { game_id: "005", timestamp: "2026-04-09T09:40:00+00:00", contribution: 2.5 },
  ],
};

const ruleBeta: RewardTrendRule = {
  rule_id: "beta_rule",
  total_contribution: 30.0,
  contribution_per_game: 10.0,
  points: [
    { game_id: "001", timestamp: "2026-04-09T09:00:00+00:00", contribution: 10.0 },
    { game_id: "002", timestamp: "2026-04-09T09:10:00+00:00", contribution: 10.0 },
    { game_id: "003", timestamp: "2026-04-09T09:20:00+00:00", contribution: 10.0 },
  ],
};

const ruleGamma: RewardTrendRule = {
  rule_id: "gamma_rule",
  total_contribution: 5.0,
  contribution_per_game: 1.0,
  points: [
    { game_id: "001", timestamp: "2026-04-09T09:00:00+00:00", contribution: 1.0 },
    { game_id: "002", timestamp: "2026-04-09T09:10:00+00:00", contribution: 1.0 },
    { game_id: "003", timestamp: "2026-04-09T09:20:00+00:00", contribution: 1.0 },
    { game_id: "004", timestamp: "2026-04-09T09:30:00+00:00", contribution: 1.0 },
    { game_id: "005", timestamp: "2026-04-09T09:40:00+00:00", contribution: 1.0 },
  ],
};

const populatedResponse: RewardTrendsResponse = {
  rules: [ruleAlpha, ruleBeta, ruleGamma],
  n_games: 5,
  generated_at: "2026-04-09T10:00:00+00:00",
};

/**
 * Build a fetch mock that records every called URL and returns the given
 * response body for any /api/training/reward-trends request.
 */
function mockRewardTrendsFetch(body: RewardTrendsResponse) {
  const calls: string[] = [];
  const fn = async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push(url);
    if (url.includes("/api/training/reward-trends")) {
      return jsonResponse(body);
    }
    throw new Error(`Unexpected fetch: ${url}`);
  };
  return { fn, calls };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async () => jsonResponse(emptyResponse),
  );
});

describe("sortRules", () => {
  it("sorts by total_contribution descending", () => {
    const sorted = sortRules(
      [ruleAlpha, ruleBeta, ruleGamma],
      "total_contribution",
      "desc",
    );
    expect(sorted.map((r) => r.rule_id)).toEqual([
      "beta_rule",
      "alpha_rule",
      "gamma_rule",
    ]);
  });

  it("sorts by total_contribution ascending", () => {
    const sorted = sortRules(
      [ruleAlpha, ruleBeta, ruleGamma],
      "total_contribution",
      "asc",
    );
    expect(sorted.map((r) => r.rule_id)).toEqual([
      "gamma_rule",
      "alpha_rule",
      "beta_rule",
    ]);
  });

  it("sorts by rule_id ascending (alphabetical)", () => {
    const sorted = sortRules(
      [ruleGamma, ruleBeta, ruleAlpha],
      "rule_id",
      "asc",
    );
    expect(sorted.map((r) => r.rule_id)).toEqual([
      "alpha_rule",
      "beta_rule",
      "gamma_rule",
    ]);
  });

  it("sorts by contribution_per_game descending", () => {
    const sorted = sortRules(
      [ruleAlpha, ruleBeta, ruleGamma],
      "contribution_per_game",
      "desc",
    );
    expect(sorted.map((r) => r.rule_id)).toEqual([
      "beta_rule",
      "alpha_rule",
      "gamma_rule",
    ]);
  });
});

describe("buildChartData", () => {
  it("returns empty array when no rules", () => {
    expect(buildChartData([])).toEqual([]);
  });

  it("builds rows keyed by max points length and fills missing with null", () => {
    const rows = buildChartData([ruleAlpha, ruleBeta]);
    // ruleAlpha has 5 points, ruleBeta has 3 -> max 5 rows
    expect(rows).toHaveLength(5);
    expect(rows[0]).toEqual({
      game: 0,
      alpha_rule: 3.0,
      beta_rule: 10.0,
    });
    expect(rows[3]).toEqual({
      game: 3,
      alpha_rule: 2.5,
      beta_rule: null,
    });
    expect(rows[4]).toEqual({
      game: 4,
      alpha_rule: 2.5,
      beta_rule: null,
    });
  });
});

describe("RewardTrends component", () => {
  it("renders loading state before first fetch resolves", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () => new Promise(() => undefined),
    );
    render(<RewardTrends pollIntervalMs={10_000} />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("renders empty state when rules is empty and n_games is 0", async () => {
    const { fn } = mockRewardTrendsFetch(emptyResponse);
    vi.spyOn(globalThis, "fetch").mockImplementation(fn);
    render(<RewardTrends pollIntervalMs={10_000} />);
    await waitFor(() => {
      expect(screen.getByText("Reward Trends")).toBeInTheDocument();
    });
    expect(screen.getByText(/no reward logs yet/i)).toBeInTheDocument();
    // Table should NOT be rendered in empty state
    expect(screen.queryByRole("columnheader", { name: /rule_id/ })).toBeNull();
  });

  it("renders populated state with summary table rows for each rule", async () => {
    const { fn } = mockRewardTrendsFetch(populatedResponse);
    vi.spyOn(globalThis, "fetch").mockImplementation(fn);
    render(<RewardTrends pollIntervalMs={10_000} />);
    await waitFor(() => {
      expect(screen.getByText("alpha_rule")).toBeInTheDocument();
    });
    expect(screen.getByText("beta_rule")).toBeInTheDocument();
    expect(screen.getByText("gamma_rule")).toBeInTheDocument();

    // Numeric cells
    expect(screen.getByText("12.50")).toBeInTheDocument();
    expect(screen.getByText("30.00")).toBeInTheDocument();
    expect(screen.getByText("10.00")).toBeInTheDocument();

    // Chart stub rendered (recharts mocked above)
    expect(screen.getAllByTestId("recharts-stub").length).toBeGreaterThan(0);
  });

  it("toggles sort direction on the summary table: asc -> desc -> asc", async () => {
    const { fn } = mockRewardTrendsFetch(populatedResponse);
    vi.spyOn(globalThis, "fetch").mockImplementation(fn);
    render(<RewardTrends pollIntervalMs={10_000} />);
    await waitFor(() => {
      expect(screen.getByText("alpha_rule")).toBeInTheDocument();
    });

    // Helper to read rule_id column order from the <tbody>
    const getOrder = (): string[] => {
      const body = document.querySelector(".reward-trends-table tbody");
      if (!body) return [];
      const rows = Array.from(body.querySelectorAll("tr"));
      return rows.map((r) => r.querySelectorAll("td")[0]?.textContent ?? "");
    };

    // Default sort: total_contribution desc -> beta, alpha, gamma
    expect(getOrder()).toEqual(["beta_rule", "alpha_rule", "gamma_rule"]);

    // Click rule_id header: switches column to rule_id with desc default
    const ruleIdHeader = screen.getByRole("columnheader", { name: /^rule_id/ });
    fireEvent.click(ruleIdHeader);
    // New column default is desc -> gamma, beta, alpha
    expect(getOrder()).toEqual(["gamma_rule", "beta_rule", "alpha_rule"]);

    // Click again on same column: toggle desc -> asc
    fireEvent.click(ruleIdHeader);
    expect(getOrder()).toEqual(["alpha_rule", "beta_rule", "gamma_rule"]);

    // Click again: toggle asc -> desc
    fireEvent.click(ruleIdHeader);
    expect(getOrder()).toEqual(["gamma_rule", "beta_rule", "alpha_rule"]);
  });

  it("refetches with the new games param when the window selector changes", async () => {
    const { fn, calls } = mockRewardTrendsFetch(populatedResponse);
    vi.spyOn(globalThis, "fetch").mockImplementation(fn);
    render(<RewardTrends pollIntervalMs={10_000} defaultGames={100} />);
    await waitFor(() => {
      expect(screen.getByText("alpha_rule")).toBeInTheDocument();
    });

    // Initial call with games=100
    expect(calls.some((u) => u.includes("games=100"))).toBe(true);
    const initialCount = calls.length;

    // Change the selector to 500
    const select = screen.getByLabelText(/games window/i);
    fireEvent.change(select, { target: { value: "500" } });

    await waitFor(() => {
      expect(calls.length).toBeGreaterThan(initialCount);
    });
    expect(calls.some((u) => u.includes("games=500"))).toBe(true);
  });

  it("renders empty-cache fallback when fetch fails on first load and no cached data exists", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      throw new Error("network down");
    });
    render(<RewardTrends pollIntervalMs={10_000} />);
    await waitFor(() => {
      expect(screen.getByText(/no cached reward trends yet/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/network down/i)).not.toBeInTheDocument();
  });

  it("shows scanned game count for populated response", async () => {
    const { fn } = mockRewardTrendsFetch(populatedResponse);
    vi.spyOn(globalThis, "fetch").mockImplementation(fn);
    render(<RewardTrends pollIntervalMs={10_000} />);
    await waitFor(() => {
      const controls = document.querySelector(".reward-trends-controls");
      expect(controls).not.toBeNull();
      if (controls) {
        expect(within(controls as HTMLElement).getByText(/scanned 5 games/i)).toBeInTheDocument();
      }
    });
  });
});
