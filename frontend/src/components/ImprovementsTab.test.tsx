import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  cleanup,
  fireEvent,
} from "@testing-library/react";
import { ImprovementsTab } from "./ImprovementsTab";
import type {
  ImprovementsResponse,
  UnifiedImprovement,
} from "./ImprovementsTab";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

const ADVISED_ENTRY: UnifiedImprovement = {
  id: "advised-20260412-2007-iter1",
  source: "advised",
  timestamp: "2026-04-12T20:50:00Z",
  title: "Stronger mineral floating penalties",
  description:
    "Add a sharper per-step penalty for mineral floats above 700, reusing the same threshold the spending policy uses.",
  type: "training",
  outcome: "promoted",
  metric: "1/10 wins (validation)",
  principles: ["§1 Core Strategic Objective", "§4.2 Resource Spending"],
  files_changed: ["data/reward_rules.json"],
};

const EVOLVE_ENTRY: UnifiedImprovement = {
  id: "evolve-gen2-cand_2e57ef46",
  source: "evolve",
  timestamp: "2026-04-29T21:34:32Z",
  title: "Gas-dump warp priority when gas floods",
  description:
    "When vespene exceeds 600 and minerals are below 200, prefer gas-heavy units (Stalker, Immortal) over Zealots in the warp queue.",
  type: "dev",
  outcome: "fitness-pass",
  metric: "3-2 vs v3",
  principles: ["4.2", "11.2", "24"],
  files_changed: ["bots/v3/bot.py"],
};

const DISCARDED_EVOLVE_ENTRY: UnifiedImprovement = {
  id: "evolve-gen3-cand_deadbeef",
  source: "evolve",
  timestamp: "2026-04-29T22:10:00Z",
  title: "Aggressive proxy pylon cheese",
  description: "Build a forward pylon at the natural before any Gateway.",
  type: "dev",
  outcome: "discarded",
  metric: "0-5 vs v3",
  principles: ["§7"],
  files_changed: [
    "bots/v3/bot.py",
    "bots/v3/strategy.py",
    "bots/v3/build_orders.py",
  ],
};

const POPULATED: ImprovementsResponse = {
  improvements: [DISCARDED_EVOLVE_ENTRY, EVOLVE_ENTRY, ADVISED_ENTRY],
};

const EMPTY: ImprovementsResponse = { improvements: [] };

function mockUnifiedFetch(body: ImprovementsResponse) {
  return async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/improvements/unified")) {
      return jsonResponse(body);
    }
    throw new Error(`Unexpected fetch: ${url}`);
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    jsonResponse({ improvements: [] }),
  );
});

describe("ImprovementsTab", () => {
  it("shows loading state before first fetch resolves", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () => new Promise(() => undefined),
    );
    render(<ImprovementsTab />);
    expect(screen.getByText(/loading improvements/i)).toBeInTheDocument();
  });

  it("renders rows when populated", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(mockUnifiedFetch(POPULATED));
    render(<ImprovementsTab />);
    await waitFor(() => {
      expect(
        screen.getByText("Stronger mineral floating penalties"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByText("Gas-dump warp priority when gas floods"),
    ).toBeInTheDocument();
    expect(screen.getByText("Aggressive proxy pylon cheese")).toBeInTheDocument();
    expect(screen.getByTestId("improvements-count")).toHaveTextContent(
      "3 improvements",
    );
  });

  it("filter pills change displayed entries", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(mockUnifiedFetch(POPULATED));
    render(<ImprovementsTab />);
    await waitFor(() => {
      expect(
        screen.getByText("Stronger mineral floating penalties"),
      ).toBeInTheDocument();
    });

    // Click "Advised" — only advised entry remains.
    fireEvent.click(screen.getByTestId("filter-pill-advised"));
    expect(
      screen.getByText("Stronger mineral floating penalties"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Gas-dump warp priority when gas floods"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Aggressive proxy pylon cheese"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("improvements-count")).toHaveTextContent(
      "1 of 3 (filtered)",
    );

    // Click "Evolve" — only evolve entries remain.
    fireEvent.click(screen.getByTestId("filter-pill-evolve"));
    expect(
      screen.queryByText("Stronger mineral floating penalties"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("Gas-dump warp priority when gas floods"),
    ).toBeInTheDocument();
    expect(screen.getByText("Aggressive proxy pylon cheese")).toBeInTheDocument();
    expect(screen.getByTestId("improvements-count")).toHaveTextContent(
      "2 of 3 (filtered)",
    );

    // Back to "All".
    fireEvent.click(screen.getByTestId("filter-pill-all"));
    expect(
      screen.getByText("Stronger mineral floating penalties"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Gas-dump warp priority when gas floods"),
    ).toBeInTheDocument();
    expect(screen.getByText("Aggressive proxy pylon cheese")).toBeInTheDocument();
  });

  it("expands a row on click to show the full description", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(mockUnifiedFetch(POPULATED));
    render(<ImprovementsTab />);
    await waitFor(() => {
      expect(
        screen.getByText("Stronger mineral floating penalties"),
      ).toBeInTheDocument();
    });

    // Description text not visible before expansion.
    expect(
      screen.queryByText(/sharper per-step penalty for mineral floats/i),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByTestId(`improvements-row-${ADVISED_ENTRY.id}`),
    );

    expect(
      screen.getByTestId(`improvements-row-expanded-${ADVISED_ENTRY.id}`),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/sharper per-step penalty for mineral floats/i),
    ).toBeInTheDocument();
  });

  it("renders empty state when API returns no improvements", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(mockUnifiedFetch(EMPTY));
    render(<ImprovementsTab />);
    await waitFor(() => {
      expect(screen.getByTestId("improvements-empty")).toBeInTheDocument();
    });
    expect(
      screen.getByText(/No improvements yet/i),
    ).toBeInTheDocument();
  });

  it("shows stale-data banner on fetch error", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      throw new Error("network down");
    });
    render(<ImprovementsTab />);
    await waitFor(() => {
      expect(
        screen.getByText(/Improvements .* backend offline/i),
      ).toBeInTheDocument();
    });
  });

  it("outcome badge classes differ for promoted vs discarded", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(mockUnifiedFetch(POPULATED));
    render(<ImprovementsTab />);
    await waitFor(() => {
      expect(
        screen.getByText("Stronger mineral floating penalties"),
      ).toBeInTheDocument();
    });

    const promoted = screen.getByTestId("outcome-badge-promoted");
    const discarded = screen.getByTestId("outcome-badge-discarded");
    expect(promoted.className).toContain("success");
    expect(discarded.className).toContain("failure");
    expect(promoted.className).not.toEqual(discarded.className);
  });
});
