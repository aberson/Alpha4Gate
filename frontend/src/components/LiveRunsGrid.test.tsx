import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  cleanup,
  fireEvent,
  within,
} from "@testing-library/react";
import { LiveRunsGrid } from "./LiveRunsGrid";
import { formatRelativeTime, type RunRow } from "../types/runs";

/**
 * LiveRunsGrid + ``formatRelativeTime`` tests — Step 5 of the
 * Models-tab build plan.
 *
 * Coverage:
 *   - Empty state renders a single "No active runs." card.
 *   - Single row renders one card with version label, harness icon,
 *     phase, current_imp, progress bar, and score line.
 *   - Multi-row renders one card per harness instance.
 *   - Expand button (native ``<details>``) reveals full row JSON.
 *   - ``formatRelativeTime`` handles every bucket (just-now / Ns /
 *     Nm / Nh / Nd) plus the empty / unparseable / negative-skew edges.
 *   - The card's "updated 3s ago" text comes from the helper at the
 *     poll-anchored ``Date.now`` snapshot.
 *
 * The grid mounts a 2s ``setInterval`` to refresh its own ``nowMs``
 * anchor; tests use ``vi.useFakeTimers()`` only for the timer-driven
 * test (everything else lets the real timer fire — irrelevant because
 * jsdom tears down between tests).
 */

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

function makeRow(overrides: Partial<RunRow> = {}): RunRow {
  return {
    harness: "evolve",
    version: "v7",
    phase: "fitness",
    current_imp: "Splash readiness",
    games_played: 6,
    games_total: 10,
    score_cand: 4,
    score_parent: 2,
    started_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:01:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async (input: RequestInfo | URL): Promise<Response> => {
      const url =
        typeof input === "string" ? input : (input as URL).toString();
      if (url.includes("/api/runs/active")) {
        return jsonResponse([]);
      }
      return jsonResponse({});
    },
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// formatRelativeTime — pure helper
// ---------------------------------------------------------------------------

describe("formatRelativeTime", () => {
  // Pin "now" so every assertion is independent of wall-clock.
  const NOW = Date.parse("2026-05-01T12:00:00Z");

  it("returns — for empty input", () => {
    expect(formatRelativeTime("", NOW)).toBe("—");
  });

  it("returns — for unparseable input", () => {
    expect(formatRelativeTime("not-a-date", NOW)).toBe("—");
  });

  it("collapses negative deltas (clock skew) to just now", () => {
    // updated_at is in the future relative to NOW.
    const future = new Date(NOW + 5000).toISOString();
    expect(formatRelativeTime(future, NOW)).toBe("just now");
  });

  it("uses just now for sub-5s deltas", () => {
    const t = new Date(NOW - 2000).toISOString();
    expect(formatRelativeTime(t, NOW)).toBe("just now");
  });

  it("uses Ns ago for sub-minute deltas", () => {
    const t = new Date(NOW - 30_000).toISOString();
    expect(formatRelativeTime(t, NOW)).toBe("30s ago");
  });

  it("uses Nm ago for sub-hour deltas", () => {
    const t = new Date(NOW - 5 * 60_000).toISOString();
    expect(formatRelativeTime(t, NOW)).toBe("5m ago");
  });

  it("uses Nh ago for sub-day deltas", () => {
    const t = new Date(NOW - 3 * 3600_000).toISOString();
    expect(formatRelativeTime(t, NOW)).toBe("3h ago");
  });

  it("uses Nd ago for multi-day deltas", () => {
    const t = new Date(NOW - 2 * 86_400_000).toISOString();
    expect(formatRelativeTime(t, NOW)).toBe("2d ago");
  });
});

// ---------------------------------------------------------------------------
// LiveRunsGrid — rendering
// ---------------------------------------------------------------------------

describe("LiveRunsGrid", () => {
  it("renders the empty-state card when /api/runs/active returns []", async () => {
    render(<LiveRunsGrid />);
    await waitFor(() => {
      expect(screen.getByTestId("live-runs-grid")).toBeInTheDocument();
    });
    expect(screen.getByTestId("run-card-empty")).toHaveTextContent(
      "No active runs.",
    );
    // No real per-harness cards.
    expect(screen.queryByTestId("run-card-evolve")).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-card-advised")).not.toBeInTheDocument();
  });

  it("renders one card per row with version, icon, phase, progress, and score", async () => {
    const row = makeRow();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      jsonResponse([row]),
    );
    render(<LiveRunsGrid />);
    const card = await screen.findByTestId("run-card-evolve");

    // Harness icon — emoji
    expect(within(card).getByTestId("run-icon-evolve")).toHaveTextContent("🧬");
    // Header line: harness label + @ version
    expect(card).toHaveTextContent(/evolve/i);
    expect(card).toHaveTextContent(/@ v7/);
    // Phase + current_imp
    expect(card).toHaveTextContent("fitness");
    expect(card).toHaveTextContent("Splash readiness");
    // Progress
    expect(within(card).getByTestId("run-progress-evolve")).toHaveTextContent(
      "6/10 games",
    );
    expect(within(card).getByTestId("run-score-evolve")).toHaveTextContent(
      "cand 4 vs parent 2",
    );
    expect(within(card).getByTestId("run-progress-bar")).toBeInTheDocument();
    // Empty card NOT rendered.
    expect(screen.queryByTestId("run-card-empty")).not.toBeInTheDocument();
  });

  it("renders multiple cards when the endpoint returns several rows", async () => {
    const rows: RunRow[] = [
      makeRow({ harness: "training-daemon", current_imp: "" }),
      makeRow({ harness: "advised", current_imp: "iter1" }),
      makeRow({ harness: "evolve" }),
    ];
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      jsonResponse(rows),
    );
    render(<LiveRunsGrid />);
    await screen.findByTestId("run-card-training-daemon");
    expect(screen.getByTestId("run-card-advised")).toBeInTheDocument();
    expect(screen.getByTestId("run-card-evolve")).toBeInTheDocument();
    // Per-harness icon mapping
    expect(screen.getByTestId("run-icon-training-daemon")).toHaveTextContent(
      "🤖",
    );
    expect(screen.getByTestId("run-icon-advised")).toHaveTextContent("💡");
    expect(screen.getByTestId("run-icon-evolve")).toHaveTextContent("🧬");
  });

  it("expand summary toggles open and reveals the full row JSON", async () => {
    const row = makeRow();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      jsonResponse([row]),
    );
    render(<LiveRunsGrid />);
    const details = (await screen.findByTestId(
      "run-details-evolve",
    )) as HTMLDetailsElement;
    expect(details.open).toBe(false);

    fireEvent.click(screen.getByTestId("run-expand-evolve"));
    // jsdom + native <details>: the click on the summary flips .open.
    expect(details.open).toBe(true);

    const json = screen.getByTestId("run-state-json-evolve");
    expect(json).toBeInTheDocument();
    // Every row field should appear verbatim in the JSON.
    expect(json.textContent).toContain('"harness": "evolve"');
    expect(json.textContent).toContain('"version": "v7"');
    expect(json.textContent).toContain('"current_imp": "Splash readiness"');
    expect(json.textContent).toContain('"games_played": 6');
    expect(json.textContent).toContain('"games_total": 10');
  });

  it("formats updated_at relative to a mocked Date.now", async () => {
    // Pin Date.now so the card's "updated Ns ago" text is deterministic.
    // updated_at is 30s before our pinned now → expect "30s ago".
    const NOW = Date.parse("2026-05-01T12:00:30Z");
    vi.spyOn(Date, "now").mockReturnValue(NOW);
    const row = makeRow({ updated_at: "2026-05-01T12:00:00Z" });
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      jsonResponse([row]),
    );
    render(<LiveRunsGrid />);
    const updated = await screen.findByTestId("run-updated-evolve");
    expect(updated).toHaveTextContent("updated 30s ago");
  });

  it("hides progress + score when games_total is 0 (e.g. claude_prompt phase)", async () => {
    const row = makeRow({
      harness: "advised",
      phase: "validating",
      games_total: 0,
      games_played: 0,
      score_cand: 0,
      score_parent: 0,
      current_imp: "iter1",
    });
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      jsonResponse([row]),
    );
    render(<LiveRunsGrid />);
    await screen.findByTestId("run-card-advised");
    expect(screen.queryByTestId("run-progress-bar")).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-progress-advised")).not.toBeInTheDocument();
  });
});
