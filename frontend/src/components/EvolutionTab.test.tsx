import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  cleanup,
  fireEvent,
} from "@testing-library/react";
import { EvolutionTab } from "./EvolutionTab";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

type FetchFn = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

interface MockFixture {
  state: Record<string, unknown>;
  control?: Record<string, unknown>;
  pool?: Record<string, unknown>;
  results?: Record<string, unknown>;
  currentRound?: Record<string, unknown>;
}

// Capture every PUT /api/evolve/control call for assertion.
interface PutCapture {
  url: string;
  body: Record<string, unknown>;
}

function mockFetch(
  fixture: MockFixture,
  putCapture?: PutCapture[],
): FetchFn {
  const state = fixture.state;
  const control = fixture.control ?? {
    stop_run: false,
    pause_after_round: false,
  };
  const pool = fixture.pool ?? {
    parent: null,
    generated_at: null,
    pool: [],
  };
  const results = fixture.results ?? { rounds: [] };
  const currentRound = fixture.currentRound ?? {
    active: false,
    round_index: null,
    imp_a_title: null,
    imp_b_title: null,
    phase: null,
    cand_a: null,
    cand_b: null,
    games_played: null,
    games_total: null,
    score_a: null,
    score_b: null,
    gate_candidate: null,
    updated_at: null,
  };

  return async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/evolve/current-round")) {
      return jsonResponse(currentRound);
    }
    if (url.includes("/api/evolve/state")) {
      return jsonResponse(state);
    }
    if (url.includes("/api/evolve/control")) {
      if (init?.method === "PUT") {
        const body = JSON.parse(init.body as string) as Record<string, unknown>;
        if (putCapture !== undefined) {
          putCapture.push({ url, body });
        }
        return jsonResponse({ ...control, ...body });
      }
      return jsonResponse(control);
    }
    if (url.includes("/api/evolve/pool")) {
      return jsonResponse(pool);
    }
    if (url.includes("/api/evolve/results")) {
      return jsonResponse(results);
    }
    // Any other endpoint polled by ancillary hooks — return empty JSON.
    return jsonResponse({});
  };
}

const idleState = {
  status: "idle",
  parent_start: null,
  parent_current: null,
  started_at: null,
  wall_budget_hours: null,
  rounds_completed: null,
  rounds_promoted: null,
  no_progress_streak: null,
  pool_remaining_count: null,
  last_result: null,
};

const runningLastResult = {
  round_index: 1,
  candidate_a: "v0-aaa",
  candidate_b: "v0-bbb",
  imp_a_title: "Reward scouting",
  imp_b_title: "Fix supply block",
  ab_score: [3, 2],
  gate_score: [4, 1],
  outcome: "promoted",
  reason: "cand won gate 4-1",
};

const runningState = {
  status: "running",
  parent_start: "v0",
  parent_current: "v0-aaa",
  started_at: "2026-04-19T10:00:00+00:00",
  wall_budget_hours: 4.0,
  rounds_completed: 1,
  rounds_promoted: 1,
  no_progress_streak: 0,
  pool_remaining_count: 8,
  last_result: runningLastResult,
};

const completedState = {
  ...runningState,
  status: "completed",
  stop_reason: "wall-clock",
  run_log_path: "documentation/evolve-runs/2026-04-19.md",
};

const runningPool = {
  parent: "v0",
  generated_at: "2026-04-19T09:55:00+00:00",
  pool: [
    {
      rank: 1,
      title: "Reward scouting",
      type: "training",
      description: "…",
      principle_ids: [],
      expected_impact: "+5% WR",
      concrete_change: "{}",
      status: "consumed-won",
    },
    {
      rank: 2,
      title: "Fix supply block",
      type: "training",
      description: "…",
      principle_ids: [],
      expected_impact: "+3% WR",
      concrete_change: "{}",
      status: "consumed-lost",
    },
    {
      rank: 3,
      title: "Forward pylon",
      type: "dev",
      description: "…",
      principle_ids: [],
      expected_impact: "+2% WR",
      concrete_change: "Place proxy pylon at 3:30",
      status: "consumed-tie",
    },
    {
      rank: 4,
      title: "Robo first",
      type: "training",
      description: "…",
      principle_ids: [],
      expected_impact: "+4% WR",
      concrete_change: "{}",
      status: "active",
    },
    {
      rank: 5,
      title: "Blink upgrade",
      type: "training",
      description: "…",
      principle_ids: [],
      expected_impact: "+2% WR",
      concrete_change: "{}",
      status: "active",
    },
    {
      rank: 6,
      title: "Third base timing",
      type: "training",
      description: "…",
      principle_ids: [],
      expected_impact: "+1% WR",
      concrete_change: "{}",
      status: "active",
    },
    {
      rank: 7,
      title: "Archon morph",
      type: "dev",
      description: "…",
      principle_ids: [],
      expected_impact: "+2% WR",
      concrete_change: "…",
      status: "active",
    },
    {
      rank: 8,
      title: "Cannon defence",
      type: "training",
      description: "…",
      principle_ids: [],
      expected_impact: "+1% WR",
      concrete_change: "{}",
      status: "active",
    },
    {
      rank: 9,
      title: "Observer scouting",
      type: "training",
      description: "…",
      principle_ids: [],
      expected_impact: "+1% WR",
      concrete_change: "{}",
      status: "active",
    },
    {
      rank: 10,
      title: "Attack-walk tactic",
      type: "dev",
      description: "…",
      principle_ids: [],
      expected_impact: "+3% WR",
      concrete_change: "…",
      status: "active",
    },
  ],
};

const runningResults = {
  rounds: [
    {
      parent: "v0",
      candidate_a: "v0-aaa",
      candidate_b: "v0-bbb",
      imp_a: {
        rank: 1,
        title: "Reward scouting",
        type: "training",
        description: "",
        principle_ids: [],
        expected_impact: "",
        concrete_change: "{}",
      },
      imp_b: {
        rank: 2,
        title: "Fix supply block",
        type: "training",
        description: "",
        principle_ids: [],
        expected_impact: "",
        concrete_change: "{}",
      },
      ab_record: [],
      gate_record: [],
      winner: "v0-aaa",
      promoted: true,
      reason: "cand won gate 4-1",
    },
  ],
};

const defaultControl = { stop_run: false, pause_after_round: false };

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async () => jsonResponse({}),
  );
});

describe("EvolutionTab", () => {
  it("renders idle state with launch guidance", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch({ state: idleState, control: defaultControl }),
    );
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByText(/No evolve run active/i)).toBeInTheDocument();
    });
    // Launch hint mentions the CLI invocation and the evolve skill.
    expect(
      screen.getByText(/python scripts\/evolve\.py/),
    ).toBeInTheDocument();
    // The slash-command reference appears at least once (header text
    // also mentions /improve-bot-evolve, so use getAllByText).
    expect(
      screen.getAllByText(/improve-bot-evolve/).length,
    ).toBeGreaterThanOrEqual(1);
    // Header badge is present in idle mode too.
    expect(screen.getByText(/^idle$/i)).toBeInTheDocument();
  });

  it("renders running state with parent header, last result and pool", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch({
        state: runningState,
        control: defaultControl,
        pool: runningPool,
        results: runningResults,
      }),
    );
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByText(/^running$/i)).toBeInTheDocument();
    });
    // Header shows parent versions; parent_current v0-aaa appears both
    // in the header line and as candidate_a in the round history table.
    expect(screen.getAllByText("v0").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("v0-aaa").length).toBeGreaterThanOrEqual(1);
    // Last-result card shows both imp titles.
    expect(screen.getAllByText("Reward scouting").length).toBeGreaterThanOrEqual(
      1,
    );
    expect(
      screen.getAllByText("Fix supply block").length,
    ).toBeGreaterThanOrEqual(1);
    // Pool view shows all 10 items by title.
    expect(screen.getByText("Robo first")).toBeInTheDocument();
    expect(screen.getByText("Attack-walk tactic")).toBeInTheDocument();
  });

  it("renders completed state with run-ended reason and disabled stop button", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch({
        state: completedState,
        control: defaultControl,
        pool: runningPool,
        results: runningResults,
      }),
    );
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByText(/^completed$/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/Run ended/)).toBeInTheDocument();
    expect(screen.getByText(/wall-clock/)).toBeInTheDocument();
    // Stop button exists but is disabled when the run is no longer running.
    const stopButton = screen.getByRole("button", { name: /Stop Run/i });
    expect(stopButton).toBeDisabled();
  });

  it("stop-run button opens confirm dialog and PUTs stop_run true on confirm", async () => {
    const captured: PutCapture[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(
        {
          state: runningState,
          control: defaultControl,
          pool: runningPool,
          results: runningResults,
        },
        captured,
      ),
    );
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Stop Run/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /Stop Run/i }));
    // Confirmation dialog opens.
    await waitFor(() => {
      expect(screen.getByText("Stop evolve run?")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /^Stop$/ }));

    await waitFor(() => {
      const puts = captured.filter((c) => c.body.stop_run === true);
      expect(puts.length).toBeGreaterThan(0);
    });
    const puts = captured.filter((c) => c.body.stop_run === true);
    expect(puts[0]!.url).toContain("/api/evolve/control");
    expect(puts[0]!.body).toEqual({ stop_run: true });
  });

  it("pause-after-round checkbox PUTs pause_after_round true when toggled on", async () => {
    const captured: PutCapture[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch(
        {
          state: runningState,
          control: defaultControl,
          pool: runningPool,
          results: runningResults,
        },
        captured,
      ),
    );
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(
        screen.getByLabelText(/Pause after current round/i),
      ).toBeInTheDocument();
    });
    fireEvent.click(screen.getByLabelText(/Pause after current round/i));
    await waitFor(() => {
      const puts = captured.filter(
        (c) => c.body.pause_after_round === true,
      );
      expect(puts.length).toBeGreaterThan(0);
    });
    const puts = captured.filter((c) => c.body.pause_after_round === true);
    expect(puts[0]!.body).toEqual({ pause_after_round: true });
  });

  it("renders a pool-status badge for every status value", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch({
        state: runningState,
        control: defaultControl,
        pool: runningPool,
        results: runningResults,
      }),
    );
    render(<EvolutionTab />);
    await waitFor(() => {
      // Wait for the pool to render.
      expect(screen.getByText("Robo first")).toBeInTheDocument();
    });
    // The fixture pool contains one of each status, so each badge
    // variant must appear at least once.
    expect(
      screen.getAllByTestId("pool-status-active").length,
    ).toBeGreaterThanOrEqual(1);
    expect(
      screen.getAllByTestId("pool-status-consumed-won").length,
    ).toBeGreaterThanOrEqual(1);
    expect(
      screen.getAllByTestId("pool-status-consumed-lost").length,
    ).toBeGreaterThanOrEqual(1);
    expect(
      screen.getAllByTestId("pool-status-consumed-tie").length,
    ).toBeGreaterThanOrEqual(1);
  });

  it("hides the Current Round card when no round is active", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch({
        state: runningState,
        control: defaultControl,
        pool: runningPool,
        results: runningResults,
        // Default currentRound is active:false.
      }),
    );
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByText("Robo first")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("current-round-card")).not.toBeInTheDocument();
  });

  it("shows Current Round card with phase, score, and progress during an AB phase", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch({
        state: runningState,
        control: defaultControl,
        pool: runningPool,
        results: runningResults,
        currentRound: {
          active: true,
          round_index: 2,
          imp_a_title: "Chrono Boost",
          imp_b_title: "Blink micro",
          phase: "ab",
          cand_a: "cand_abc_a",
          cand_b: "cand_abc_b",
          games_played: 3,
          games_total: 10,
          score_a: 2,
          score_b: 1,
          gate_candidate: null,
          updated_at: "2026-04-20T19:15:00+00:00",
        },
      }),
    );
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByTestId("current-round-card")).toBeInTheDocument();
    });
    expect(screen.getByText(/Chrono Boost/)).toBeInTheDocument();
    expect(screen.getByText(/Blink micro/)).toBeInTheDocument();
    expect(screen.getByTestId("round-phase-ab")).toBeInTheDocument();
    // Score uses an en-dash visual separator; match both digits regardless.
    expect(screen.getByTestId("current-round-score")).toHaveTextContent(
      /^\s*2\s*[–-]\s*1\s*$/,
    );
    expect(screen.getByTestId("current-round-progress")).toHaveTextContent(
      "3/10",
    );
  });

  it("labels gate phase with candidate vs parent", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch({
        state: runningState,
        control: defaultControl,
        pool: runningPool,
        results: runningResults,
        currentRound: {
          active: true,
          round_index: 2,
          imp_a_title: "Chrono Boost",
          imp_b_title: "Blink micro",
          phase: "gate",
          cand_a: "cand_abc_a",
          cand_b: "cand_abc_b",
          games_played: 1,
          games_total: 5,
          score_a: 1,
          score_b: 0,
          gate_candidate: "cand_abc_a",
          updated_at: "2026-04-20T19:18:00+00:00",
        },
      }),
    );
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByTestId("round-phase-gate")).toBeInTheDocument();
    });
    // Both labels should live inside the round card, not elsewhere on the page.
    const card = screen.getByTestId("current-round-card");
    expect(card.textContent).toContain("cand_abc_a");
    expect(card.textContent).toContain("parent");
  });

  it("shows mirror_games phase with X/Y progress during pool seeding", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch({
        state: runningState,
        control: defaultControl,
        pool: runningPool,
        results: runningResults,
        currentRound: {
          active: true,
          round_index: 0,
          imp_a_title: "parent-vs-parent mirror games",
          imp_b_title: "Claude advisor",
          phase: "mirror_games",
          cand_a: "v0",
          cand_b: "v0",
          games_played: 1,
          games_total: 3,
          score_a: 0,
          score_b: 0,
          gate_candidate: null,
          updated_at: "2026-04-20T23:10:00+00:00",
        },
      }),
    );
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByTestId("round-phase-mirror_games")).toBeInTheDocument();
    });
    const card = screen.getByTestId("current-round-card");
    expect(card.textContent).toMatch(/Seeding Claude advisor/i);
    expect(card.textContent).toMatch(/mirror games/i);
    expect(screen.getByTestId("current-round-progress")).toHaveTextContent(
      "1/3",
    );
  });

  it("shows claude_prompt phase with indefinite progress indicator", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch({
        state: runningState,
        control: defaultControl,
        pool: runningPool,
        results: runningResults,
        currentRound: {
          active: true,
          round_index: 0,
          imp_a_title: "parent-vs-parent mirror games",
          imp_b_title: "Claude advisor",
          phase: "claude_prompt",
          cand_a: "v0",
          cand_b: "v0",
          games_played: 0,
          games_total: 10,
          score_a: 0,
          score_b: 0,
          gate_candidate: null,
          updated_at: "2026-04-20T23:22:00+00:00",
        },
      }),
    );
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(
        screen.getByTestId("round-phase-claude_prompt"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("current-round-indefinite-bar"),
    ).toBeInTheDocument();
    const card = screen.getByTestId("current-round-card");
    expect(card.textContent).toMatch(/Claude advisor proposing/i);
  });

  it("renders a CRASH row in Round History for entries carrying an error field", async () => {
    const crashResults = {
      rounds: [
        {
          parent: "v0",
          candidate_a: "(crash — no candidate)",
          candidate_b: "(crash — no candidate)",
          imp_a: {
            rank: 1,
            title: "Implement Chrono Boost",
            type: "dev",
            description: "",
            principle_ids: [],
            expected_impact: "",
            concrete_change: "{}",
          },
          imp_b: {
            rank: 2,
            title: "Reward Chrono Boost usage",
            type: "training",
            description: "",
            principle_ids: [],
            expected_impact: "",
            concrete_change: "{}",
          },
          ab_record: [],
          gate_record: null,
          winner: null,
          promoted: false,
          reason:
            "crashed: RuntimeError: dev sub-agent timed out after 900s",
          error:
            "RuntimeError: dev sub-agent timed out after 900s",
        },
      ],
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch({
        state: runningState,
        control: defaultControl,
        pool: runningPool,
        results: crashResults,
      }),
    );
    render(<EvolutionTab />);
    // Wait for the crash row to render.
    const crashRow = await screen.findByTestId("round-history-row-crash");
    expect(crashRow).toBeInTheDocument();
    // The OutcomeBadge shows the "discarded-crash" label.
    expect(crashRow.textContent).toMatch(/discarded-crash/);
    // The error line is visible in red code styling.
    const errorEl = screen.getByTestId("round-history-error");
    expect(errorEl.textContent).toContain("dev sub-agent timed out");
  });

  it("renders Run Stats in inline 'Label: value' format below the pool", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockFetch({
        state: runningState,
        control: defaultControl,
        pool: runningPool,
        results: runningResults,
      }),
    );
    render(<EvolutionTab />);
    const statsList = await screen.findByTestId("run-stats-list");
    // Inline format — strong label immediately followed by value.
    expect(statsList.textContent).toMatch(/Rounds Completed:\s*1/);
    expect(statsList.textContent).toMatch(/Rounds Promoted:\s*1/);
    expect(statsList.textContent).toMatch(/Pool Remaining:\s*8/);
    expect(statsList.textContent).toMatch(/Wall Budget:\s*4h/);

    // Ordering: Pool section should appear BEFORE Run Stats in the DOM.
    const poolHeading = screen.getByRole("heading", { name: /^Pool$/ });
    const statsHeading = screen.getByRole("heading", { name: /^Run Stats$/ });
    const order = poolHeading.compareDocumentPosition(statsHeading);
    // DOCUMENT_POSITION_FOLLOWING === 0x04
    expect(order & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
