import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import {
  render,
  screen,
  waitFor,
  cleanup,
  fireEvent,
  within,
} from "@testing-library/react";

// Recharts' ``ResponsiveContainer`` measures its parent via
// ``ResizeObserver`` + ``getBoundingClientRect``. In jsdom both return
// 0×0 so the inner chart never paints. Stub the container with a fixed-
// size wrapper so jsdom can exercise the inner SVG path. The other
// Recharts primitives — including ``ReferenceLine`` — are passed through
// unchanged so structural assertions still reflect real behaviour. Same
// pattern as ``VersionInspector.test.tsx`` (Step 6).
vi.mock("recharts", async () => {
  const actual = await vi.importActual<typeof import("recharts")>("recharts");
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => {
      if (React.isValidElement<{ width?: number; height?: number }>(children)) {
        return React.cloneElement(children, {
          width: children.props.width ?? 600,
          height: children.props.height ?? 280,
        });
      }
      return <div data-testid="responsive-container-stub">{children}</div>;
    },
  };
});

import { ForensicsView } from "./ForensicsView";

/**
 * ForensicsView tests — Step 8 of the Models-tab build plan.
 *
 * Coverage:
 *   - Empty state when ``version`` is null.
 *   - Game-id selector populates from training-history rolling_overall.
 *   - Default selection snaps to the most-recent game.
 *   - Winprob trajectory line chart renders with seeded data.
 *   - Give-up vertical reference line appears when ``give_up_fired`` is
 *     true (mirrored by a text badge for contract testing).
 *   - Expert-dispatch placeholder always renders ("Phase O pending").
 *   - Empty trajectory shows "no transitions yet" message.
 *   - "No games yet" empty selector message when training-history is
 *     empty.
 */

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

interface MockOpts {
  trainingHistory?: {
    rolling_10: { game_id: string; ts: string; wr: number }[];
    rolling_50: { game_id: string; ts: string; wr: number }[];
    rolling_overall: { game_id: string; ts: string; wr: number }[];
  };
  forensics?: {
    trajectory: { step: number; win_prob: number | null; ts: string }[];
    give_up_fired: boolean;
    give_up_step: number | null;
    expert_dispatch: unknown | null;
  };
  // Per-game forensics responses keyed by game_id, when the test needs
  // to differentiate (e.g. selector-change test).
  forensicsByGame?: Record<
    string,
    {
      trajectory: { step: number; win_prob: number | null; ts: string }[];
      give_up_fired: boolean;
      give_up_step: number | null;
      expert_dispatch: unknown | null;
    }
  >;
}

function makeFetchMock(opts: MockOpts) {
  return vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (/\/api\/versions\/v\d+\/training-history$/.test(url)) {
      return jsonResponse(
        opts.trainingHistory ?? {
          rolling_10: [],
          rolling_50: [],
          rolling_overall: [],
        },
      );
    }
    const m = url.match(/\/api\/versions\/v\d+\/forensics\/([A-Za-z0-9_-]+)$/);
    if (m) {
      const gameId = m[1];
      if (opts.forensicsByGame && opts.forensicsByGame[gameId]) {
        return jsonResponse(opts.forensicsByGame[gameId]);
      }
      return jsonResponse(
        opts.forensics ?? {
          trajectory: [],
          give_up_fired: false,
          give_up_step: null,
          expert_dispatch: null,
        },
      );
    }
    throw new Error(`Unexpected fetch: ${url}`);
  });
}

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(makeFetchMock({}));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ForensicsView — empty state", () => {
  it("renders the empty placeholder when version is null", () => {
    render(<ForensicsView version={null} />);
    expect(screen.getByTestId("forensics-empty")).toBeInTheDocument();
    expect(
      screen.getByText(/select a version/i),
    ).toBeInTheDocument();
  });

  it("does NOT issue any fetches when version is null", () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(makeFetchMock({}));
    render(<ForensicsView version={null} />);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("ForensicsView — game-id selector", () => {
  it("populates from training-history rolling_overall", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        trainingHistory: {
          rolling_10: [],
          rolling_50: [],
          rolling_overall: [
            { game_id: "g_001", ts: "2026-04-30T00:00:00Z", wr: 0.3 },
            { game_id: "g_002", ts: "2026-04-30T01:00:00Z", wr: 0.5 },
            { game_id: "g_003", ts: "2026-04-30T02:00:00Z", wr: 0.7 },
          ],
        },
      }),
    );
    render(<ForensicsView version="v7" />);
    const select = (await screen.findByTestId(
      "forensics-game-select",
    )) as HTMLSelectElement;
    // 3 game options.
    expect(select.querySelectorAll("option").length).toBe(3);
    // Default snaps to the MOST-RECENT (rolling_overall[length-1]).
    await waitFor(() => {
      expect(select.value).toBe("g_003");
    });
    // Selector lists all three game ids.
    expect(within(select).getByText(/g_001/)).toBeInTheDocument();
    expect(within(select).getByText(/g_002/)).toBeInTheDocument();
    expect(within(select).getByText(/g_003/)).toBeInTheDocument();
  });

  it("shows 'no games yet' empty message when rolling_overall is empty", async () => {
    render(<ForensicsView version="v7" />);
    await waitFor(() => {
      expect(screen.getByTestId("forensics-no-games")).toBeInTheDocument();
    });
    expect(screen.getByTestId("forensics-no-games")).toHaveTextContent(
      /no training games/i,
    );
    // No selector rendered.
    expect(
      screen.queryByTestId("forensics-game-select"),
    ).not.toBeInTheDocument();
  });

  it("filters out malformed game ids before populating the dropdown", async () => {
    // The backend already validates on insert, but the client guards
    // defensively. A row with shell metacharacters or > 128 chars must
    // not appear in the dropdown.
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        trainingHistory: {
          rolling_10: [],
          rolling_50: [],
          rolling_overall: [
            { game_id: "good_one", ts: "2026-04-30T00:00:00Z", wr: 0.3 },
            { game_id: "bad;rm -rf", ts: "2026-04-30T01:00:00Z", wr: 0.5 },
            { game_id: "good_two", ts: "2026-04-30T02:00:00Z", wr: 0.7 },
          ],
        },
      }),
    );
    render(<ForensicsView version="v7" />);
    const select = (await screen.findByTestId(
      "forensics-game-select",
    )) as HTMLSelectElement;
    expect(select.querySelectorAll("option").length).toBe(2);
    expect(within(select).queryByText(/bad;rm/)).not.toBeInTheDocument();
  });
});

describe("ForensicsView — winprob trajectory chart", () => {
  it("renders a chart with seeded trajectory data", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        trainingHistory: {
          rolling_10: [],
          rolling_50: [],
          rolling_overall: [
            { game_id: "g_001", ts: "2026-04-30T00:00:00Z", wr: 0.3 },
          ],
        },
        forensics: {
          trajectory: [
            { step: 0, win_prob: 0.5, ts: "2026-04-30T00:00:00Z" },
            { step: 10, win_prob: 0.55, ts: "2026-04-30T00:00:30Z" },
            { step: 20, win_prob: 0.6, ts: "2026-04-30T00:01:00Z" },
            { step: 30, win_prob: 0.4, ts: "2026-04-30T00:01:30Z" },
            { step: 40, win_prob: 0.2, ts: "2026-04-30T00:02:00Z" },
          ],
          give_up_fired: false,
          give_up_step: null,
          expert_dispatch: null,
        },
      }),
    );
    render(<ForensicsView version="v7" />);
    await waitFor(() => {
      expect(
        screen.getByTestId("forensics-trajectory-body"),
      ).toBeInTheDocument();
    });
    const body = screen.getByTestId("forensics-trajectory-body");
    expect(body.querySelector("svg")).not.toBeNull();
    // Give-up badge is NOT rendered when give_up_fired is false.
    expect(
      screen.queryByTestId("forensics-give-up-badge"),
    ).not.toBeInTheDocument();
  });

  it("shows 'no transitions yet' when trajectory array is empty", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        trainingHistory: {
          rolling_10: [],
          rolling_50: [],
          rolling_overall: [
            { game_id: "g_empty", ts: "2026-04-30T00:00:00Z", wr: 0.0 },
          ],
        },
        forensics: {
          trajectory: [],
          give_up_fired: false,
          give_up_step: null,
          expert_dispatch: null,
        },
      }),
    );
    render(<ForensicsView version="v7" />);
    await waitFor(() => {
      expect(
        screen.getByTestId("forensics-trajectory-empty"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("forensics-trajectory-empty"),
    ).toHaveTextContent(/no transitions/i);
  });
});

describe("ForensicsView — give-up reference line", () => {
  it("renders the give-up annotation when give_up_fired is true", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        trainingHistory: {
          rolling_10: [],
          rolling_50: [],
          rolling_overall: [
            { game_id: "g_001", ts: "2026-04-30T00:00:00Z", wr: 0.0 },
          ],
        },
        forensics: {
          trajectory: [
            { step: 0, win_prob: 0.5, ts: "2026-04-30T00:00:00Z" },
            { step: 10, win_prob: 0.3, ts: "2026-04-30T00:00:30Z" },
            { step: 20, win_prob: 0.1, ts: "2026-04-30T00:01:00Z" },
            { step: 25, win_prob: 0.05, ts: "2026-04-30T00:01:15Z" },
          ],
          give_up_fired: true,
          give_up_step: 25,
          expert_dispatch: null,
        },
      }),
    );
    render(<ForensicsView version="v7" />);
    await waitFor(() => {
      expect(
        screen.getByTestId("forensics-trajectory-body"),
      ).toBeInTheDocument();
    });
    // Plain-text mirror of the reference line — proves the contract
    // without depending on Recharts' internal SVG attributes.
    const badge = screen.getByTestId("forensics-give-up-badge");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent(/give-up/i);
    expect(badge).toHaveTextContent(/step 25/);
  });

  it("does NOT render the give-up annotation when fire flag is false", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        trainingHistory: {
          rolling_10: [],
          rolling_50: [],
          rolling_overall: [
            { game_id: "g_001", ts: "2026-04-30T00:00:00Z", wr: 1.0 },
          ],
        },
        forensics: {
          trajectory: [
            { step: 0, win_prob: 0.5, ts: "2026-04-30T00:00:00Z" },
            { step: 100, win_prob: 0.95, ts: "2026-04-30T00:05:00Z" },
          ],
          give_up_fired: false,
          give_up_step: null,
          expert_dispatch: null,
        },
      }),
    );
    render(<ForensicsView version="v7" />);
    await waitFor(() => {
      expect(
        screen.getByTestId("forensics-trajectory-body"),
      ).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("forensics-give-up-badge"),
    ).not.toBeInTheDocument();
  });
});

describe("ForensicsView — expert dispatch placeholder", () => {
  it("always renders the Phase O placeholder card", async () => {
    render(<ForensicsView version="v7" />);
    await waitFor(() => {
      expect(
        screen.getByTestId("forensics-expert-dispatch"),
      ).toBeInTheDocument();
    });
    expect(screen.getByTestId("forensics-expert-dispatch")).toHaveTextContent(
      /phase o pending/i,
    );
  });

  it("renders the placeholder even when forensics has no trajectory", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        trainingHistory: {
          rolling_10: [],
          rolling_50: [],
          rolling_overall: [
            { game_id: "g_001", ts: "2026-04-30T00:00:00Z", wr: 0.0 },
          ],
        },
        forensics: {
          trajectory: [],
          give_up_fired: false,
          give_up_step: null,
          expert_dispatch: null,
        },
      }),
    );
    render(<ForensicsView version="v7" />);
    await waitFor(() => {
      expect(
        screen.getByTestId("forensics-expert-dispatch"),
      ).toBeInTheDocument();
    });
  });
});

describe("ForensicsView — selector change", () => {
  it("changing the selected game refetches forensics for the new id", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        trainingHistory: {
          rolling_10: [],
          rolling_50: [],
          rolling_overall: [
            { game_id: "g_old", ts: "2026-04-30T00:00:00Z", wr: 0.3 },
            { game_id: "g_new", ts: "2026-04-30T01:00:00Z", wr: 0.7 },
          ],
        },
        forensicsByGame: {
          g_old: {
            trajectory: [
              { step: 0, win_prob: 0.4, ts: "2026-04-30T00:00:00Z" },
            ],
            give_up_fired: true,
            give_up_step: 0,
            expert_dispatch: null,
          },
          g_new: {
            trajectory: [
              { step: 0, win_prob: 0.6, ts: "2026-04-30T01:00:00Z" },
            ],
            give_up_fired: false,
            give_up_step: null,
            expert_dispatch: null,
          },
        },
      }),
    );
    render(<ForensicsView version="v7" />);
    // Default selects g_new (most recent). give-up badge absent.
    await waitFor(() => {
      expect(
        screen.getByTestId("forensics-trajectory-body"),
      ).toBeInTheDocument();
    });
    const select = screen.getByTestId(
      "forensics-game-select",
    ) as HTMLSelectElement;
    expect(select.value).toBe("g_new");
    expect(
      screen.queryByTestId("forensics-give-up-badge"),
    ).not.toBeInTheDocument();

    // Switch to g_old — give-up badge should appear.
    fireEvent.change(select, { target: { value: "g_old" } });
    await waitFor(() => {
      expect(
        screen.getByTestId("forensics-give-up-badge"),
      ).toBeInTheDocument();
    });
  });
});
