import { useCallback } from "react";
import { useApi } from "./useApi";
import type { UseApiResult } from "./useApi";

// --- Pool item (from data/evolve_pool.json) ---

// Seven-status vocabulary for the new (generation-phase) evolve algorithm.
// See documentation/investigations/evolve-algorithm-redesign-investigation.md.
export type EvolvePoolStatus =
  | "active"
  | "fitness-pass"
  | "fitness-close"
  | "evicted"
  | "promoted-stack"
  | "promoted-single"
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
  | "composition-pass"
  | "composition-fail"
  | "regression-pass"
  | "regression-rollback"
  | "crash";

export type EvolvePhase =
  | "starting"
  | "mirror_games"
  | "claude_prompt"
  | "fitness"
  | "composition"
  | "regression"
  | "pool_refresh";

export interface EvolveLastResult {
  generation_index: number;
  phase: EvolvePhase | string;
  imp_title: string | null;
  stacked_titles: string[] | null;
  is_fallback?: boolean;
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
// Discriminated by `phase`. Fitness rows carry one imp; composition rows
// carry a stack; regression rows carry two parents; crash rows carry an
// error field.

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

export interface EvolveCompositionRow extends EvolveRowBase {
  phase: "composition";
  parent: string;
  candidate: string;
  stacked_imps: EvolveRoundImprovement[];
  stacked_titles: string[];
  is_fallback: boolean;
  record: EvolveSelfPlayRecord[];
  wins_cand: number;
  wins_parent: number;
  games: number;
  promoted: boolean;
  promoted_version: string | null;
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
  phase: string; // "fitness" | "composition" | "regression"
  parent?: string;
  imp?: EvolveRoundImprovement | null;
  outcome: "crash";
}

// Union type for RoundHistoryTable rendering.
export type EvolveRoundResult =
  | EvolveFitnessRow
  | EvolveCompositionRow
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
  // Composition-phase payload:
  stacked_titles: string[];
  is_fallback: boolean;
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

// Cache-key suffix bumped to v2 because the schema for every endpoint
// changed (new status vocabulary, new field names, new phase rows).
// Without bumping the cache key, browsers with cached v1 responses would
// feed stale shapes into the new component renderers and crash.
const CACHE_KEY_SUFFIX = "evolve-v2";

/**
 * Hook for monitoring and controlling an improve-bot-evolve run
 * (generation-phase algorithm).
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
