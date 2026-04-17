import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { LadderTab } from "./LadderTab";
import type { LadderData } from "./LadderTab";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

const populatedData: LadderData = {
  standings: [
    { version: "v0", elo: 1200, games_played: 50, last_updated: "2026-04-17T10:00:00Z" },
    { version: "v1", elo: 1150, games_played: 45, last_updated: "2026-04-17T09:30:00Z" },
  ],
  head_to_head: {
    v0: { v1: { wins: 10, losses: 5, draws: 2 } },
    v1: { v0: { wins: 5, losses: 10, draws: 2 } },
  },
};

const emptyData: LadderData = {
  standings: [],
  head_to_head: {},
};

function mockLadderFetch(body: LadderData) {
  return async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/ladder")) {
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
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => jsonResponse({}));
});

describe("LadderTab", () => {
  it("renders loading state before first fetch resolves", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () => new Promise(() => undefined),
    );
    render(<LadderTab />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("renders standings table with mock data", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(mockLadderFetch(populatedData));
    render(<LadderTab />);
    await waitFor(() => {
      expect(screen.getByText("Ladder Standings")).toBeInTheDocument();
    });
    // Check standings rows (versions appear in standings + h2h, so use getAllByText)
    expect(screen.getAllByText("v0").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("v1").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("1200")).toBeInTheDocument();
    expect(screen.getByText("1150")).toBeInTheDocument();
    expect(screen.getByText("50")).toBeInTheDocument();
    expect(screen.getByText("45")).toBeInTheDocument();

    // Check head-to-head section
    expect(screen.getByText("Head-to-Head")).toBeInTheDocument();
    expect(screen.getByText("10W/5L/2D")).toBeInTheDocument();
    expect(screen.getByText("5W/10L/2D")).toBeInTheDocument();
  });

  it("renders empty state when standings is empty", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(mockLadderFetch(emptyData));
    render(<LadderTab />);
    await waitFor(() => {
      expect(screen.getByText("Ladder Standings")).toBeInTheDocument();
    });
    expect(screen.getByText("No ladder data yet")).toBeInTheDocument();
    // Head-to-head should not appear
    expect(screen.queryByText("Head-to-Head")).not.toBeInTheDocument();
  });
});
