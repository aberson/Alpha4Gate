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
    generation: 0,
    pool: [],
  };
  const results = fixture.results ?? { rounds: [] };
  const currentRound = fixture.currentRound ?? {
    active: false,
    generation: null,
    phase: null,
    imp_title: null,
    imp_rank: null,
    imp_index: null,
    candidate: null,
    stacked_titles: [],
    is_fallback: false,
    new_parent: null,
    prior_parent: null,
    games_played: null,
    games_total: null,
    score_cand: null,
    score_parent: null,
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
    return jsonResponse({});
  };
}

const idleState = {
  status: "idle",
  parent_start: null,
  parent_current: null,
  started_at: null,
  wall_budget_hours: null,
  generation_index: null,
  generations_completed: null,
  generations_promoted: null,
  evictions: null,
  resurrections_remaining: null,
  pool_remaining_count: null,
  last_result: null,
};

const runningState = {
  status: "running",
  parent_start: "v0",
  parent_current: "v1",
  started_at: "2026-04-21T21:33:00+00:00",
  wall_budget_hours: 4.0,
  generation_index: 3,
  generations_completed: 2,
  generations_promoted: 1,
  evictions: 4,
  resurrections_remaining: 3,
  pool_remaining_count: 6,
  last_result: {
    generation_index: 2,
    phase: "composition",
    imp_title: null,
    stacked_titles: ["Chrono boost", "Forward pylon"],
    is_fallback: false,
    score: [3, 5],
    outcome: "composition-pass",
    reason: "composition pass: stacked_parent (v1, 2 imps) beat v0 3-2",
  },
};

const completedState = {
  ...runningState,
  status: "completed",
  stop_reason: "wall-clock",
  run_log_path: "documentation/soak-test-runs/evolve-2026-04-21.md",
};

describe("EvolutionTab", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn> | null = null;

  beforeEach(() => {
    // Default fetch so hooks polling never-intercepted URLs don't throw.
    fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => jsonResponse({}));
  });

  afterEach(() => {
    fetchSpy?.mockRestore();
    fetchSpy = null;
    cleanup();
  });

  function installFetch(
    fixture: MockFixture,
    putCapture?: PutCapture[],
  ): void {
    fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(mockFetch(fixture, putCapture));
  }

  // --- Basic rendering ---

  it("renders idle state with launch guidance", async () => {
    installFetch({ state: idleState });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByText(/No evolve run active/i)).toBeTruthy();
    });
    expect(screen.getByText(/scripts\/evolve.py/)).toBeTruthy();
  });

  it("renders running state with parent header and last-phase card", async () => {
    installFetch({ state: runningState });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByText(/Started/)).toBeTruthy();
    });
    // Parent -> current line shows both versions.
    const headerText = document.body.textContent ?? "";
    expect(headerText).toContain("v0");
    expect(headerText).toContain("v1");
    // Last-phase card shows the generation index + phase.
    expect(
      screen.getByText(/Generation #2/i, { exact: false }),
    ).toBeTruthy();
  });

  it("renders completed state with run-ended reason and disabled stop button", async () => {
    installFetch({ state: completedState });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByText(/wall-clock/i)).toBeTruthy();
    });
    const stopBtn = screen.getByRole("button", { name: /stop run/i });
    expect(stopBtn.hasAttribute("disabled")).toBe(true);
  });

  // --- Actions ---

  it("stop-run button opens confirm dialog and PUTs stop_run true on confirm", async () => {
    const puts: PutCapture[] = [];
    installFetch({ state: runningState }, puts);
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByText(/Run Actions/i)).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: /stop run/i }));
    // Confirm dialog appears; click Stop in dialog.
    const confirm = await screen.findByRole("button", { name: /^stop$/i });
    fireEvent.click(confirm);
    await waitFor(() => {
      expect(puts.length).toBeGreaterThan(0);
    });
    expect(puts.find((p) => p.body.stop_run === true)).toBeTruthy();
  });

  it("pause-after-round checkbox PUTs pause_after_round true when toggled on", async () => {
    const puts: PutCapture[] = [];
    installFetch({ state: runningState }, puts);
    render(<EvolutionTab />);
    const checkbox = await screen.findByLabelText(
      /Pause after current generation/i,
    );
    fireEvent.click(checkbox);
    await waitFor(() => {
      expect(
        puts.find((p) => p.body.pause_after_round === true),
      ).toBeTruthy();
    });
  });

  // --- Pool status vocabulary ---

  it("renders a pool-status badge for every status value in the new vocabulary", async () => {
    const pool = {
      parent: "v0",
      generated_at: "2026-04-21T10:00:00+00:00",
      generation: 3,
      pool: [
        {
          rank: 1,
          title: "Active imp",
          type: "dev",
          description: "",
          principle_ids: [],
          expected_impact: "",
          concrete_change: "",
          status: "active",
          fitness_score: null,
          retry_count: 0,
          first_evaluated_against: null,
          last_evaluated_against: null,
        },
        {
          rank: 2,
          title: "Passed fitness",
          type: "dev",
          description: "",
          principle_ids: [],
          expected_impact: "",
          concrete_change: "",
          status: "fitness-pass",
          fitness_score: [3, 5],
          retry_count: 1,
          first_evaluated_against: "v0",
          last_evaluated_against: "v0",
        },
        {
          rank: 3,
          title: "Close loss",
          type: "dev",
          description: "",
          principle_ids: [],
          expected_impact: "",
          concrete_change: "",
          status: "fitness-close",
          fitness_score: [2, 5],
          retry_count: 1,
          first_evaluated_against: "v0",
          last_evaluated_against: "v0",
        },
        {
          rank: 4,
          title: "Blowout",
          type: "dev",
          description: "",
          principle_ids: [],
          expected_impact: "",
          concrete_change: "",
          status: "evicted",
          fitness_score: [0, 5],
          retry_count: 1,
          first_evaluated_against: "v0",
          last_evaluated_against: "v0",
        },
        {
          rank: 5,
          title: "Promoted in stack",
          type: "dev",
          description: "",
          principle_ids: [],
          expected_impact: "",
          concrete_change: "",
          status: "promoted-stack",
          fitness_score: [3, 5],
          retry_count: 1,
          first_evaluated_against: "v0",
          last_evaluated_against: "v0",
        },
        {
          rank: 6,
          title: "Promoted single",
          type: "dev",
          description: "",
          principle_ids: [],
          expected_impact: "",
          concrete_change: "",
          status: "promoted-single",
          fitness_score: [3, 5],
          retry_count: 1,
          first_evaluated_against: "v0",
          last_evaluated_against: "v0",
        },
        {
          rank: 7,
          title: "Rolled back",
          type: "dev",
          description: "",
          principle_ids: [],
          expected_impact: "",
          concrete_change: "",
          status: "regression-rollback",
          fitness_score: [3, 5],
          retry_count: 2,
          first_evaluated_against: "v0",
          last_evaluated_against: "v1",
        },
      ],
    };
    installFetch({ state: runningState, pool });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getAllByTestId("pool-status-active").length).toBe(1);
    });
    expect(
      screen.getAllByTestId("pool-status-fitness-pass").length,
    ).toBe(1);
    expect(
      screen.getAllByTestId("pool-status-fitness-close").length,
    ).toBe(1);
    expect(screen.getAllByTestId("pool-status-evicted").length).toBe(1);
    expect(
      screen.getAllByTestId("pool-status-promoted-stack").length,
    ).toBe(1);
    expect(
      screen.getAllByTestId("pool-status-promoted-single").length,
    ).toBe(1);
    expect(
      screen.getAllByTestId("pool-status-regression-rollback").length,
    ).toBe(1);
  });

  // --- Current phase card ---

  it("hides the Current Phase card when no phase is active", async () => {
    installFetch({
      state: runningState,
      currentRound: {
        active: false,
        generation: null,
        phase: null,
        imp_title: null,
        imp_rank: null,
        imp_index: null,
        candidate: null,
        stacked_titles: [],
        is_fallback: false,
        new_parent: null,
        prior_parent: null,
        games_played: null,
        games_total: null,
        score_cand: null,
        score_parent: null,
        updated_at: null,
      },
    });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByText(/Run Stats/i)).toBeTruthy();
    });
    expect(screen.queryByTestId("current-round-card")).toBeNull();
  });

  it("shows the Current Phase card for a fitness phase with score + progress", async () => {
    installFetch({
      state: runningState,
      currentRound: {
        active: true,
        generation: 3,
        phase: "fitness",
        imp_title: "Chrono Boost",
        imp_rank: 1,
        imp_index: 0,
        candidate: "cand_abc",
        stacked_titles: [],
        is_fallback: false,
        new_parent: null,
        prior_parent: null,
        games_played: 2,
        games_total: 5,
        score_cand: 1,
        score_parent: 1,
        updated_at: "2026-04-21T19:15:00+00:00",
      },
    });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByTestId("current-round-card")).toBeTruthy();
    });
    expect(screen.getByTestId("round-phase-fitness")).toBeTruthy();
    expect(screen.getByTestId("current-round-progress").textContent).toContain(
      "2/5",
    );
    expect(screen.getByTestId("current-round-score").textContent).toContain("1");
    const matchup = screen.getByTestId("current-round-matchup");
    expect(matchup.textContent).toContain("Chrono Boost");
    expect(matchup.textContent).toContain("parent baseline");
  });

  it("shows the composition phase with a stacked-imps list", async () => {
    installFetch({
      state: runningState,
      currentRound: {
        active: true,
        generation: 3,
        phase: "composition",
        imp_title: null,
        imp_rank: null,
        imp_index: null,
        candidate: "cand_stk",
        stacked_titles: ["Chrono", "Forward pylon", "Archon morph"],
        is_fallback: false,
        new_parent: null,
        prior_parent: null,
        games_played: 3,
        games_total: 5,
        score_cand: 2,
        score_parent: 1,
        updated_at: "2026-04-21T19:20:00+00:00",
      },
    });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByTestId("round-phase-composition")).toBeTruthy();
    });
    const stackList = screen.getByTestId("composition-stack-list");
    expect(stackList.textContent).toContain("Chrono");
    expect(stackList.textContent).toContain("Forward pylon");
    expect(stackList.textContent).toContain("Archon morph");
  });

  it("shows the regression phase with new_parent vs prior_parent", async () => {
    installFetch({
      state: runningState,
      currentRound: {
        active: true,
        generation: 3,
        phase: "regression",
        imp_title: null,
        imp_rank: null,
        imp_index: null,
        candidate: null,
        stacked_titles: [],
        is_fallback: false,
        new_parent: "v1",
        prior_parent: "v0",
        games_played: 4,
        games_total: 5,
        score_cand: 2,
        score_parent: 2,
        updated_at: "2026-04-21T19:25:00+00:00",
      },
    });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByTestId("round-phase-regression")).toBeTruthy();
    });
    const matchup = screen.getByTestId("current-round-matchup");
    expect(matchup.textContent).toContain("new parent");
    expect(matchup.textContent).toContain("prior parent");
    expect(matchup.textContent).toContain("v1");
    expect(matchup.textContent).toContain("v0");
  });

  it("shows the mirror_games phase with X/Y progress during pool seeding", async () => {
    installFetch({
      state: { ...runningState, parent_current: "v0" },
      currentRound: {
        active: true,
        generation: 0,
        phase: "mirror_games",
        imp_title: "parent-vs-parent mirror games",
        imp_rank: null,
        imp_index: null,
        candidate: "v0",
        stacked_titles: [],
        is_fallback: false,
        new_parent: null,
        prior_parent: null,
        games_played: 2,
        games_total: 3,
        score_cand: 0,
        score_parent: 0,
        updated_at: "2026-04-21T19:00:00+00:00",
      },
    });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByTestId("round-phase-mirror_games")).toBeTruthy();
    });
    expect(screen.getByTestId("current-round-progress").textContent).toContain(
      "2/3",
    );
  });

  it("shows the claude_prompt phase with an indefinite progress indicator", async () => {
    installFetch({
      state: runningState,
      currentRound: {
        active: true,
        generation: 0,
        phase: "claude_prompt",
        imp_title: "Claude advisor",
        imp_rank: null,
        imp_index: null,
        candidate: null,
        stacked_titles: [],
        is_fallback: false,
        new_parent: null,
        prior_parent: null,
        games_played: 0,
        games_total: 10,
        score_cand: 0,
        score_parent: 0,
        updated_at: "2026-04-21T19:05:00+00:00",
      },
    });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByTestId("round-phase-claude_prompt")).toBeTruthy();
    });
    expect(screen.getByTestId("current-round-indefinite-bar")).toBeTruthy();
  });

  // --- Phase history (results JSONL) ---

  it("renders a CRASH row in Phase History for entries carrying an error field", async () => {
    const results = {
      rounds: [
        {
          phase: "fitness",
          generation: 1,
          parent: "v0",
          imp: {
            rank: 1,
            title: "Boom imp",
            type: "dev",
            description: "",
            principle_ids: [],
            expected_impact: "",
            concrete_change: "",
          },
          candidate: "cand_boom",
          record: [],
          wins_cand: 0,
          wins_parent: 0,
          games: 5,
          outcome: "crash",
          reason: "crashed: RuntimeError: OOM",
          error: "RuntimeError: OOM",
        },
      ],
    };
    installFetch({ state: runningState, results });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByText(/Phase History/i)).toBeTruthy();
    });
    const crashRow = screen.getByTestId("round-history-row-crash");
    expect(crashRow.textContent).toContain("Boom imp");
    expect(crashRow.textContent).toMatch(/crash/);
    const err = screen.getByTestId("round-history-error");
    expect(err.textContent).toContain("RuntimeError: OOM");
  });

  // --- Run Stats ---

  it("renders the new Run Stats fields inline", async () => {
    installFetch({ state: runningState });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByTestId("run-stats-list")).toBeTruthy();
    });
    const statsText = screen.getByTestId("run-stats-list").textContent ?? "";
    expect(statsText).toContain("Generation:");
    expect(statsText).toContain("Generations Completed:");
    expect(statsText).toContain("Generations Promoted:");
    expect(statsText).toContain("Evictions:");
    expect(statsText).toContain("Resurrections Left:");
    expect(statsText).toContain("Pool Active:");
    expect(statsText).toContain("Wall Budget:");
  });
});
