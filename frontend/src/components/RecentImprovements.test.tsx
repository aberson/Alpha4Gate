import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  cleanup,
  within,
  fireEvent,
} from "@testing-library/react";
import {
  RecentImprovements,
  classifyEntry,
  computeDeltaDisplay,
} from "./RecentImprovements";
import type { PromotionHistoryEntry } from "./RecentImprovements";

/**
 * Mock fetch response helper. Mirrors LoopStatus.test.tsx pattern.
 */
function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

function mockHistory(history: PromotionHistoryEntry[]) {
  return async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/training/promotions/history")) {
      return jsonResponse({ history });
    }
    throw new Error(`Unexpected fetch: ${url}`);
  };
}

const promotionEntry: PromotionHistoryEntry = {
  timestamp: "2026-04-09T10:00:00+00:00",
  new_checkpoint: "v5",
  old_best: "v4",
  new_win_rate: 0.72,
  old_win_rate: 0.6,
  delta: 0.12,
  eval_games_played: 40,
  promoted: true,
  reason: "win_rate 0.72 > 0.60 + 0.05 threshold",
  difficulty: 3,
  action_distribution_shift: 0.15,
};

const rollbackEntry: PromotionHistoryEntry = {
  timestamp: "2026-04-09T11:00:00+00:00",
  new_checkpoint: "v4",
  old_best: "v5",
  new_win_rate: 0.55,
  old_win_rate: 0.72,
  delta: -0.17,
  eval_games_played: 30,
  promoted: false,
  reason: "rollback: win rate regression 0.72 -> 0.55",
  difficulty: 3,
  action_distribution_shift: null,
};

const rejectedEntry: PromotionHistoryEntry = {
  timestamp: "2026-04-09T12:00:00+00:00",
  new_checkpoint: "v6",
  old_best: "v4",
  new_win_rate: 0.58,
  old_win_rate: 0.6,
  delta: -0.02,
  eval_games_played: 40,
  promoted: false,
  reason: "win_rate 0.58 < 0.60 + 0.05 threshold",
  difficulty: 3,
  action_distribution_shift: 0.05,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async () => jsonResponse({ history: [] }),
  );
});

describe("classifyEntry", () => {
  it("classifies promoted=true as promotion", () => {
    expect(classifyEntry(promotionEntry)).toBe("promotion");
  });

  it("classifies promoted=false with rollback: prefix as rollback", () => {
    expect(classifyEntry(rollbackEntry)).toBe("rollback");
  });

  it("classifies promoted=false without rollback: prefix as rejected", () => {
    expect(classifyEntry(rejectedEntry)).toBe("rejected");
  });

  it("classifies empty reason + promoted=false as rejected", () => {
    const entry: PromotionHistoryEntry = { ...rejectedEntry, reason: "" };
    expect(classifyEntry(entry)).toBe("rejected");
  });
});

describe("computeDeltaDisplay", () => {
  it("returns green up-arrow for positive delta", () => {
    const d = computeDeltaDisplay(promotionEntry);
    expect(d.text).toBe("+12.0%");
    expect(d.arrow).toBe("\u2191");
    expect(d.color).toBe("#2ecc71");
  });

  it("returns red down-arrow for negative delta", () => {
    const d = computeDeltaDisplay(rollbackEntry);
    expect(d.text).toBe("-17.0%");
    expect(d.arrow).toBe("\u2193");
    expect(d.color).toBe("#e74c3c");
  });

  it("computes delta from win rates when backend delta is null", () => {
    const entry: PromotionHistoryEntry = {
      ...promotionEntry,
      delta: null,
      new_win_rate: 0.8,
      old_win_rate: 0.5,
    };
    const d = computeDeltaDisplay(entry);
    // 0.8 - 0.5 = 0.3 -> +30.0%
    expect(d.text).toBe("+30.0%");
    expect(d.arrow).toBe("\u2191");
  });

  it("returns neutral dash when no prior best and no delta", () => {
    const entry: PromotionHistoryEntry = {
      ...promotionEntry,
      delta: null,
      old_win_rate: null,
      old_best: null,
    };
    const d = computeDeltaDisplay(entry);
    expect(d.text).toBe("\u2014");
    expect(d.arrow).toBe("");
  });
});

describe("RecentImprovements component", () => {
  it("renders loading state before first fetch resolves", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () => new Promise(() => undefined),
    );
    render(<RecentImprovements />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("renders empty state when history is empty", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(mockHistory([]));
    render(<RecentImprovements />);
    await waitFor(() => {
      expect(screen.getByText("Recent Improvements")).toBeInTheDocument();
    });
    expect(
      screen.getByText(/No promotion or rollback events yet/i),
    ).toBeInTheDocument();
  });

  it("renders all three entry types with correct badges and reasons", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockHistory([promotionEntry, rollbackEntry, rejectedEntry]),
    );
    render(<RecentImprovements />);
    await waitFor(() => {
      expect(screen.getByText("Recent Improvements")).toBeInTheDocument();
    });

    // Badge labels
    expect(screen.getByText("promote")).toBeInTheDocument();
    expect(screen.getByText("rollback")).toBeInTheDocument();
    expect(screen.getByText("rejected")).toBeInTheDocument();

    // Checkpoint names
    expect(screen.getByText("v5")).toBeInTheDocument();
    expect(screen.getByText("v6")).toBeInTheDocument();
    // Two rows reference v4 as new_checkpoint/old_best -- at least one exists
    expect(screen.getAllByText(/v4/).length).toBeGreaterThan(0);

    // Reason text surfaces
    expect(
      screen.getByText(/rollback: win rate regression/i),
    ).toBeInTheDocument();
  });

  it("filter toggle: Promotions hides rollbacks and rejected", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockHistory([promotionEntry, rollbackEntry, rejectedEntry]),
    );
    render(<RecentImprovements />);
    await waitFor(() => {
      expect(screen.getByText("promote")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Promotions" }));

    expect(screen.getByText("promote")).toBeInTheDocument();
    expect(screen.queryByText("rollback")).not.toBeInTheDocument();
    expect(screen.queryByText("rejected")).not.toBeInTheDocument();
  });

  it("filter toggle: Rollbacks shows only rollback entries", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockHistory([promotionEntry, rollbackEntry, rejectedEntry]),
    );
    render(<RecentImprovements />);
    await waitFor(() => {
      expect(screen.getByText("promote")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Rollbacks" }));

    expect(screen.queryByText("promote")).not.toBeInTheDocument();
    expect(screen.getByText("rollback")).toBeInTheDocument();
    expect(screen.queryByText("rejected")).not.toBeInTheDocument();
  });

  it("filter toggle: All shows all three categories", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockHistory([promotionEntry, rollbackEntry, rejectedEntry]),
    );
    render(<RecentImprovements />);
    await waitFor(() => {
      expect(screen.getByText("promote")).toBeInTheDocument();
    });

    // Start in All; switch to Rollbacks then back to All to exercise toggle.
    fireEvent.click(screen.getByRole("button", { name: "Rollbacks" }));
    fireEvent.click(screen.getByRole("button", { name: "All" }));

    expect(screen.getByText("promote")).toBeInTheDocument();
    expect(screen.getByText("rollback")).toBeInTheDocument();
    expect(screen.getByText("rejected")).toBeInTheDocument();
  });

  it("renders delta direction for both promotion and rollback rows", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockHistory([promotionEntry, rollbackEntry]),
    );
    render(<RecentImprovements />);
    await waitFor(() => {
      expect(screen.getByText("promote")).toBeInTheDocument();
    });

    // Promotion row: +12.0% up arrow
    const promoteBadge = screen.getByText("promote");
    const promoteRow = promoteBadge.closest("li");
    expect(promoteRow).not.toBeNull();
    if (promoteRow) {
      const u = within(promoteRow);
      expect(u.getByText(/\+12\.0%/)).toBeInTheDocument();
    }

    // Rollback row: -17.0% down arrow
    const rollbackBadge = screen.getByText("rollback");
    const rollbackRow = rollbackBadge.closest("li");
    expect(rollbackRow).not.toBeNull();
    if (rollbackRow) {
      const u = within(rollbackRow);
      expect(u.getByText(/-17\.0%/)).toBeInTheDocument();
    }
  });

  it("renders empty-cache fallback when fetch fails on first load and no cached data exists", async () => {
    // Phase 4.8 Phase 1a changed the error rendering behavior: instead of
    // showing "Error: <message>" to the user, the component renders from
    // IndexedDB cache if available, and falls back to an actionable
    // "No cached improvements data yet" message when there is neither
    // cache nor a successful fetch. The error string is still tracked
    // internally (for debugging) but is never rendered — stale cached
    // data is shown with a StaleDataBanner instead.
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      throw new Error("network down");
    });
    render(<RecentImprovements />);
    await waitFor(() => {
      expect(
        screen.getByText(/no cached improvements data yet/i),
      ).toBeInTheDocument();
    });
    // Error message must NOT be rendered to the user.
    expect(screen.queryByText(/network down/i)).not.toBeInTheDocument();
  });
});
