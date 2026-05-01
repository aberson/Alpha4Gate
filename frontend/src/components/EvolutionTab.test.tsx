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
  runningRounds?: Record<string, unknown>;
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
  const runningRounds = fixture.runningRounds ?? {
    active: false,
    concurrency: null,
    run_id: null,
    rounds: [],
  };
  const currentRound = fixture.currentRound ?? {
    active: false,
    generation: null,
    phase: null,
    imp_title: null,
    imp_rank: null,
    imp_index: null,
    candidate: null,
    stacked_titles: [],
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
    if (url.includes("/api/evolve/running-rounds")) {
      return jsonResponse(runningRounds);
    }
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
    phase: "stack_apply",
    imp_title: null,
    stacked_titles: ["Chrono boost", "Forward pylon"],
    new_version: "v1",
    score: [0, 0],
    outcome: "stack-apply-pass",
    reason: "stack-apply pass: promoted v1 (2 imps) from parent v0",
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

  it("renders cli flags strip when run.cli_argv is present", async () => {
    installFetch({
      state: {
        ...runningState,
        cli_argv: ["--hours", "8", "--pool-size", "10", "--concurrency", "3"],
      },
    });
    render(<EvolutionTab />);
    const strip = await screen.findByTestId("evolve-cli-flags");
    expect(strip.textContent).toContain(
      "--hours 8 --pool-size 10 --concurrency 3",
    );
  });

  it("does not render cli flags strip when run.cli_argv is null (legacy run)", async () => {
    installFetch({ state: runningState });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByText(/Run Stats/i)).toBeTruthy();
    });
    expect(screen.queryByTestId("evolve-cli-flags")).toBeNull();
  });

  it("renders Time Remaining as a single value when only --hours is set (deterministic)", async () => {
    // started 1h ago, 4h budget, no gen cap → exactly 3h remaining,
    // rendered as a single value (no en-dash range).
    const oneHourAgo = new Date(Date.now() - 3600 * 1000).toISOString();
    installFetch({
      state: {
        ...runningState,
        started_at: oneHourAgo,
        wall_budget_hours: 4.0,
        cli_argv: ["--hours", "4"],
        generations_target: 0,
        gen_durations_seconds: [],
      },
    });
    render(<EvolutionTab />);
    const value = await screen.findByTestId("time-remaining-value");
    // Expect "3h" (or "2h 59m" depending on rounding) — assert the
    // hour magnitude and the absence of an en-dash range marker.
    expect(value.textContent ?? "").toMatch(/^\s*[23]h/);
    expect(value.textContent ?? "").not.toContain("–");
  });

  it("renders Time Remaining as a range when gen-cap variance is present", async () => {
    // 10 generations target, 2 completed in 60s and 120s respectively →
    // 8 generations remaining at min=60s … max=120s = 8m … 16m range.
    installFetch({
      state: {
        ...runningState,
        started_at: new Date(Date.now() - 180 * 1000).toISOString(),
        wall_budget_hours: 0, // no wall cap
        cli_argv: ["--generations", "10"],
        generations_target: 10,
        generations_completed: 2,
        gen_durations_seconds: [60, 120],
      },
    });
    render(<EvolutionTab />);
    const value = await screen.findByTestId("time-remaining-value");
    // 8m – 16m, formatted via formatDuration's "Nm" form.
    expect(value.textContent ?? "").toContain("–");
    expect(value.textContent ?? "").toContain("8m");
    expect(value.textContent ?? "").toContain("16m");
  });

  it("renders Time Remaining as 'indefinite' when neither --hours nor --generations is set", async () => {
    installFetch({
      state: {
        ...runningState,
        wall_budget_hours: 0,
        generations_target: 0,
        gen_durations_seconds: [],
      },
    });
    render(<EvolutionTab />);
    const value = await screen.findByTestId("time-remaining-value");
    expect(value.textContent).toBe("indefinite");
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
          status: "promoted",
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
      screen.getAllByTestId("pool-status-promoted").length,
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

  it("shows the stack_apply phase with a stacked-imps list", async () => {
    installFetch({
      state: runningState,
      currentRound: {
        active: true,
        generation: 3,
        phase: "stack_apply",
        imp_title: null,
        imp_rank: null,
        imp_index: null,
        candidate: null,
        stacked_titles: ["Chrono", "Forward pylon", "Archon morph"],
        new_parent: null,
        prior_parent: null,
        games_played: 0,
        games_total: 0,
        score_cand: 0,
        score_parent: 0,
        updated_at: "2026-04-21T19:20:00+00:00",
      },
    });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByTestId("round-phase-stack_apply")).toBeTruthy();
    });
    const stackList = screen.getByTestId("stack-apply-list");
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

  // --- Per-worker fan-out grid (Step 6 of evolve-parallelization) ---
  //
  // Visual-parity contract: at concurrency<=1 (or when running-rounds is
  // inactive) the legacy single-card path renders; at concurrency>=2
  // the new grid path renders one card per worker (active or idle).

  it("renders single-card layout at concurrency=1 (running-rounds inactive)", async () => {
    // running-rounds endpoint inactive -> falls through to the legacy
    // currentRound single-card path. This is the byte-identical N=1
    // visual-parity case that pairs with engine Decision D-1.
    installFetch({
      state: runningState,
      currentRound: {
        active: true,
        generation: 3,
        phase: "fitness",
        imp_title: "Solo Imp",
        imp_rank: 1,
        imp_index: 0,
        candidate: "cand_solo",
        stacked_titles: [],
        new_parent: null,
        prior_parent: null,
        games_played: 1,
        games_total: 5,
        score_cand: 1,
        score_parent: 0,
        updated_at: "2026-04-30T12:00:00+00:00",
      },
      runningRounds: {
        active: false,
        concurrency: 1,
        run_id: null,
        rounds: [],
      },
    });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByTestId("current-round-card")).toBeTruthy();
    });
    // Single card -- no grid container, no worker badge.
    expect(screen.queryByTestId("worker-rounds-grid")).toBeNull();
    expect(screen.queryByTestId("worker-badge-0")).toBeNull();
    // Card content matches the legacy single-card path verbatim.
    expect(screen.getByTestId("round-phase-fitness")).toBeTruthy();
    expect(screen.getByTestId("current-round-progress").textContent).toContain(
      "1/5",
    );
  });

  it("renders 2-card grid at concurrency=2", async () => {
    installFetch({
      state: runningState,
      runningRounds: {
        active: true,
        concurrency: 2,
        run_id: "20260430-1200",
        rounds: [
          {
            worker_id: 0,
            active: true,
            phase: "fitness",
            imp_title: "Chrono Boost",
            candidate: "cand_aaa",
            parent: "v1",
            games_played: 2,
            games_total: 5,
            score_cand: 1,
            score_parent: 1,
            updated_at: "2026-04-30T12:01:00+00:00",
          },
          {
            worker_id: 1,
            active: true,
            phase: "fitness",
            imp_title: "Forward Pylon",
            candidate: "cand_bbb",
            parent: "v1",
            games_played: 0,
            games_total: 5,
            score_cand: 0,
            score_parent: 0,
            updated_at: "2026-04-30T12:01:00+00:00",
          },
        ],
      },
    });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByTestId("worker-rounds-grid")).toBeTruthy();
    });
    // Two cards rendered; both have worker badges.
    expect(screen.getByTestId("worker-badge-0")).toBeTruthy();
    expect(screen.getByTestId("worker-badge-1")).toBeTruthy();
    // Worker cards in the grid use worker-keyed test-ids (not the
    // legacy `current-round-card`), so getByTestId on the legacy id
    // doesn't collide on multi-card layouts.
    expect(screen.getByTestId("worker-card-active-0")).toBeTruthy();
    expect(screen.getByTestId("worker-card-active-1")).toBeTruthy();
    expect(screen.queryByTestId("current-round-card")).toBeNull();
    // No idle slots in this fixture.
    expect(screen.queryByTestId("worker-card-idle-0")).toBeNull();
    expect(screen.queryByTestId("worker-card-idle-1")).toBeNull();
    // Both per-worker imp titles surface in the grid.
    const grid = screen.getByTestId("worker-rounds-grid");
    expect(grid.textContent).toContain("Chrono Boost");
    expect(grid.textContent).toContain("Forward Pylon");
    // Grid container carries the breakpoint-driven class (the
    // grid-template-columns rules live in the sibling <style>
    // block, not in inline style -- see plan §347 / Step 6
    // review finding #1).
    expect(grid.className).toContain("evolve-running-rounds-grid");
  });

  it("renders 4-card grid at concurrency=4 with mixed active and idle slots", async () => {
    installFetch({
      state: runningState,
      runningRounds: {
        active: true,
        concurrency: 4,
        run_id: "20260430-1300",
        rounds: [
          {
            worker_id: 0,
            active: true,
            phase: "fitness",
            imp_title: "Imp Alpha",
            candidate: "cand_a",
            parent: "v1",
            games_played: 3,
            games_total: 5,
            score_cand: 2,
            score_parent: 1,
            updated_at: "2026-04-30T13:00:00+00:00",
          },
          {
            worker_id: 1,
            active: true,
            phase: "fitness",
            imp_title: "Imp Beta",
            candidate: "cand_b",
            parent: "v1",
            games_played: 1,
            games_total: 5,
            score_cand: 0,
            score_parent: 1,
            updated_at: "2026-04-30T13:00:00+00:00",
          },
          {
            worker_id: 2,
            active: false,
            phase: null,
            imp_title: null,
            candidate: null,
            parent: null,
            games_played: null,
            games_total: null,
            score_cand: null,
            score_parent: null,
            updated_at: null,
          },
          {
            worker_id: 3,
            active: false,
            phase: null,
            imp_title: null,
            candidate: null,
            parent: null,
            games_played: null,
            games_total: null,
            score_cand: null,
            score_parent: null,
            updated_at: null,
          },
        ],
      },
    });
    render(<EvolutionTab />);
    await waitFor(() => {
      expect(screen.getByTestId("worker-rounds-grid")).toBeTruthy();
    });
    // Active workers 0 and 1 rendered as populated cards with badges
    // and worker-keyed test-ids.
    expect(screen.getByTestId("worker-badge-0")).toBeTruthy();
    expect(screen.getByTestId("worker-badge-1")).toBeTruthy();
    expect(screen.getByTestId("worker-card-active-0")).toBeTruthy();
    expect(screen.getByTestId("worker-card-active-1")).toBeTruthy();
    // The legacy single-card test-id MUST NOT appear when the grid
    // is active -- otherwise getByTestId in older tests would throw
    // "found multiple elements" if they ever activated the grid.
    expect(screen.queryByTestId("current-round-card")).toBeNull();
    // Idle workers 2 and 3 rendered as dim placeholder cards (NOT
    // hidden) -- the grid keeps a stable footprint as workers fan in.
    expect(screen.getByTestId("worker-card-idle-2")).toBeTruthy();
    expect(screen.getByTestId("worker-card-idle-3")).toBeTruthy();
    // Idle cards expose a worker badge too so the slot index is
    // still visible to the operator.
    expect(screen.getByTestId("worker-badge-2")).toBeTruthy();
    expect(screen.getByTestId("worker-badge-3")).toBeTruthy();
  });
});
