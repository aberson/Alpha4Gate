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

// --- Hook return type ---

export interface UseEvolveRunResult {
  state: UseApiResult<EvolveRunState>;
  control: UseApiResult<EvolveRunControl>;
  pool: UseApiResult<EvolvePoolData>;
  results: UseApiResult<EvolveResultsData>;
  currentRound: UseApiResult<EvolveCurrentRound>;
  sendControl: (patch: Partial<EvolveRunControl>) => Promise<void>;
}

// Cache-key suffix bumped to v3 because the 2-gate schema removes the
// pre-regression stacked-games phase and the fallback-variant fields.
// See documentation/plans/evolve-gate-reduction-plan.md and
// feedback_useapi_cache_schema_break.md — without bumping the cache
// key, browsers with cached v2 responses would feed stale shapes into
// the new component renderers and crash.
const CACHE_KEY_SUFFIX = "evolve-v4";

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

  return { state, control, pool, results, currentRound, sendControl };
}
