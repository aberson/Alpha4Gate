import { useCallback } from "react";
import { useApi } from "./useApi";
import type { UseApiResult } from "./useApi";

// --- Pool item (from data/evolve_pool.json) ---

// Six-status vocabulary for the gate-reduced (2-gate) evolve algorithm.
// See documentation/plans/evolve-gate-reduction-plan.md (2026-04-23);
// the two legacy promotion statuses collapsed into a single `promoted`
// status when the composition phase was removed.
export type EvolvePoolStatus =
  | "active"
  | "fitness-pass"
  | "fitness-close"
  | "evicted"
  | "promoted"
  | "regression-rollback";

export interface EvolvePoolItem {
  rank: number;
  title: string;
  type: string;
  description: string;
  principle_ids: string[];
  expected_impact: string;
  concrete_change: string;
  files_touched?: string[];
  status: EvolvePoolStatus;
  fitness_score: [number, number] | null;
  retry_count: number;
  first_evaluated_against: string | null;
  last_evaluated_against: string | null;
}

export interface EvolvePoolData {
  parent: string | null;
  generated_at: string | null;
  generation: number;
  pool: EvolvePoolItem[];
}

// --- Last-result snapshot (nested inside state.last_result) ---

export type EvolveOutcome =
  | "fitness-pass"
  | "fitness-close"
  | "fitness-fail"
  | "stack-apply-pass"
  | "stack-apply-import-fail"
  | "stack-apply-commit-fail"
  | "regression-pass"
  | "regression-rollback"
  | "crash";

export type EvolvePhase =
  | "starting"
  | "mirror_games"
  | "claude_prompt"
  | "fitness"
  | "stack_apply"
  | "regression"
  | "pool_refresh";

export interface EvolveLastResult {
  generation_index: number;
  phase: EvolvePhase | string;
  imp_title: string | null;
  stacked_titles: string[] | null;
  new_version?: string | null;
  score: [number, number];
  outcome: EvolveOutcome | string;
  reason: string;
}

// --- Top-level run state (from data/evolve_run_state.json) ---

export type EvolveStatus = "idle" | "running" | "completed" | "failed";

export interface EvolveRunState {
  status: EvolveStatus | string;
  parent_start: string | null;
  parent_current: string | null;
  started_at: string | null;
  wall_budget_hours: number | null;
  generation_index: number | null;
  generations_completed: number | null;
  generations_promoted: number | null;
  evictions: number | null;
  resurrections_remaining: number | null;
  pool_remaining_count: number | null;
  last_result: EvolveLastResult | null;
  // Optional extras the skill may append post-run:
  stop_reason?: string | null;
  run_log_path?: string | null;
  // Step 4 of the evolve-parallelization plan: the run-state idle skeleton
  // and live state both carry these so the running-rounds endpoint can
  // join per-worker files by ``run_id`` and pad to ``concurrency``.
  run_id?: string | null;
  concurrency?: number | null;
  // Snapshot of ``sys.argv[1:]`` from the dispatcher — surfaced on the
  // dashboard so the operator can see what flags this run was launched
  // with. ``null`` for runs that started before this field existed.
  cli_argv?: string[] | null;
  // One float (seconds) appended each time a generation completes. The
  // dashboard reads this to render a time-remaining range from the
  // observed per-generation min/max. ``null`` for legacy runs.
  gen_durations_seconds?: number[] | null;
  // ``args.generations`` from the dispatcher (0 = unbounded). Lets the
  // dashboard compute remaining = target - completed. ``null`` for
  // legacy runs that didn't persist this field.
  generations_target?: number | null;
}

// --- Round-history row (one phase row per line of evolve_results.jsonl) ---
//
// Discriminated by `phase`. Fitness rows carry one imp; stack-apply rows
// carry the full winning-imp stack + the new version; regression rows
// carry two parents; crash rows carry an error field.

export interface EvolveRoundImprovement {
  rank: number;
  title: string;
  type: string;
  description: string;
  principle_ids: string[];
  expected_impact: string;
  concrete_change: string;
  files_touched?: string[];
}

export interface EvolveSelfPlayRecord {
  winner?: string | null;
  [key: string]: unknown;
}

interface EvolveRowBase {
  phase: string;
  generation: number;
  outcome: string;
  reason: string;
  // Crash rows include these; normal rows have error=null/absent.
  error?: string | null;
  error_type?: string | null;
  error_message?: string | null;
}

export interface EvolveFitnessRow extends EvolveRowBase {
  phase: "fitness";
  parent: string;
  imp: EvolveRoundImprovement;
  candidate: string;
  record: EvolveSelfPlayRecord[];
  wins_cand: number;
  wins_parent: number;
  games: number;
}

export interface EvolveStackApplyRow extends EvolveRowBase {
  phase: "stack_apply";
  parent: string;
  new_version: string;
  stacked_imps: EvolveRoundImprovement[];
  stacked_titles: string[];
}

export interface EvolveRegressionRow extends EvolveRowBase {
  phase: "regression";
  new_parent: string;
  prior_parent: string;
  record: EvolveSelfPlayRecord[];
  wins_new: number;
  wins_prior: number;
  games: number;
  rolled_back: boolean;
}

export interface EvolveCrashRow extends EvolveRowBase {
  phase: string; // "fitness" | "stack_apply" | "regression"
  parent?: string;
  imp?: EvolveRoundImprovement | null;
  outcome: "crash";
}

// Union type for RoundHistoryTable rendering.
export type EvolveRoundResult =
  | EvolveFitnessRow
  | EvolveStackApplyRow
  | EvolveRegressionRow
  | EvolveCrashRow;

export interface EvolveResultsData {
  rounds: EvolveRoundResult[];
}

// --- Control signals (written via PUT) ---

export interface EvolveRunControl {
  stop_run: boolean;
  pause_after_round: boolean;
}

// --- Current phase live state (from data/evolve_current_round.json) ---

export interface EvolveCurrentRound {
  active: boolean;
  generation: number | null;
  phase: EvolvePhase | string | null;
  // Fitness-phase payload:
  imp_title: string | null;
  imp_rank: number | null;
  imp_index: number | null;
  candidate: string | null;
  // Stack-apply-phase payload:
  stacked_titles: string[];
  // Regression-phase payload:
  new_parent: string | null;
  prior_parent: string | null;
  // Common per-game progress:
  games_played: number | null;
  games_total: number | null;
  score_cand: number | null;
  score_parent: number | null;
  updated_at: string | null;
}

// --- Per-worker running rounds (from /api/evolve/running-rounds) ---
//
// Step 5 of the evolve-parallelization plan. The backend returns a
// fixed-length array padded to ``concurrency``: each slot is either an
// active per-worker entry (when its ``evolve_round_<wid>.json`` exists
// with the current ``run_id``) or an all-null idle skeleton. The grid
// renderer in Step 6 falls through to ``currentRound`` for the legacy
// single-card N=1 case and other consumers that don't yet know about
// per-worker fan-out.
//
// Strict subset of ``EvolveCurrentRound`` — no ``imp_rank``/``imp_index``/
// ``stacked_titles``/``new_parent``/``prior_parent`` (those are
// dispatcher-level state, not per-worker).

export interface RunningRound {
  worker_id: number;
  active: boolean;
  phase: EvolvePhase | string | null;
  imp_title: string | null;
  candidate: string | null;
  parent: string | null;
  games_played: number | null;
  games_total: number | null;
  score_cand: number | null;
  score_parent: number | null;
  updated_at: string | null;
}

export interface RunningRoundsResponse {
  active: boolean;
  concurrency: number | null;
  run_id: string | null;
  rounds: RunningRound[];
}

// --- Hook return type ---

export interface UseEvolveRunResult {
  state: UseApiResult<EvolveRunState>;
  control: UseApiResult<EvolveRunControl>;
  pool: UseApiResult<EvolvePoolData>;
  results: UseApiResult<EvolveResultsData>;
  currentRound: UseApiResult<EvolveCurrentRound>;
  runningRounds: UseApiResult<RunningRoundsResponse>;
  sendControl: (patch: Partial<EvolveRunControl>) => Promise<void>;
}

// Cache-key suffix bumped to v6: ``/api/evolve/state`` now carries
// ``cli_argv``, ``gen_durations_seconds``, and ``generations_target``
// so the dashboard can show run flags + a remaining-time range.
// Without bumping the cache key, returning users have the v5 payload
// cached; the new render paths read these fields before the first
// network round-trip (feedback_useapi_cache_schema_break.md).
//
// Prior bumps:
//   v4->v5: Step 5 added per-worker running rounds + ``run_id`` /
//   ``concurrency`` on the run-state idle skeleton.
//   v3->v4: 2-gate schema removed the pre-regression stacked-games
//   phase and the fallback-variant fields (gate-reduction plan).
const CACHE_KEY_SUFFIX = "evolve-v6";

/**
 * Hook for monitoring and controlling an improve-bot-evolve run
 * (generation-phase algorithm, 2-gate pipeline).
 */
export function useEvolveRun(): UseEvolveRunResult {
  const state = useApi<EvolveRunState>("/api/evolve/state", {
    pollMs: 3000,
    cacheKey: `/api/evolve/state::${CACHE_KEY_SUFFIX}`,
  });
  const control = useApi<EvolveRunControl>("/api/evolve/control", {
    pollMs: 10000,
    cacheKey: `/api/evolve/control::${CACHE_KEY_SUFFIX}`,
  });
  const pool = useApi<EvolvePoolData>("/api/evolve/pool", {
    pollMs: 10000,
    cacheKey: `/api/evolve/pool::${CACHE_KEY_SUFFIX}`,
  });
  const results = useApi<EvolveResultsData>("/api/evolve/results", {
    pollMs: 10000,
    cacheKey: `/api/evolve/results::${CACHE_KEY_SUFFIX}`,
  });
  const currentRound = useApi<EvolveCurrentRound>(
    "/api/evolve/current-round",
    {
      pollMs: 2000,
      cacheKey: `/api/evolve/current-round::${CACHE_KEY_SUFFIX}`,
    },
  );
  const runningRounds = useApi<RunningRoundsResponse>(
    "/api/evolve/running-rounds",
    {
      pollMs: 2000,
      cacheKey: `/api/evolve/running-rounds::${CACHE_KEY_SUFFIX}`,
    },
  );

  const sendControl = useCallback(
    async (patch: Partial<EvolveRunControl>) => {
      await fetch("/api/evolve/control", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      control.refresh();
    },
    [control],
  );

  return {
    state,
    control,
    pool,
    results,
    currentRound,
    runningRounds,
    sendControl,
  };
}
