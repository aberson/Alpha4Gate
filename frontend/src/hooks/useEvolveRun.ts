import { useCallback } from "react";
import { useApi } from "./useApi";
import type { UseApiResult } from "./useApi";

// --- Pool item (from data/evolve_pool.json) ---

export type EvolvePoolStatus =
  | "active"
  | "consumed-won"
  | "consumed-lost"
  | "consumed-tie";

export interface EvolvePoolItem {
  rank: number;
  title: string;
  type: string;
  description: string;
  principle_ids: string[];
  expected_impact: string;
  concrete_change: string;
  status: EvolvePoolStatus;
}

export interface EvolvePoolData {
  parent: string | null;
  generated_at: string | null;
  pool: EvolvePoolItem[];
}

// --- Last-round snapshot (nested inside state.last_result) ---

export type EvolveOutcome =
  | "promoted"
  | "discarded-tie"
  | "discarded-gate"
  | "discarded-crash";

export interface EvolveLastResult {
  round_index: number;
  candidate_a: string;
  candidate_b: string;
  imp_a_title: string;
  imp_b_title: string;
  ab_score: [number, number];
  gate_score: [number, number];
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
  rounds_completed: number | null;
  rounds_promoted: number | null;
  no_progress_streak: number | null;
  pool_remaining_count: number | null;
  last_result: EvolveLastResult | null;
  // Optional extras the skill may append post-run:
  stop_reason?: string | null;
  run_log_path?: string | null;
}

// --- Round history row (one RoundResult per line of evolve_results.jsonl) ---

export interface EvolveRoundImprovement {
  rank: number;
  title: string;
  type: string;
  description: string;
  principle_ids: string[];
  expected_impact: string;
  concrete_change: string;
}

export interface EvolveSelfPlayRecord {
  winner?: string | null;
  [key: string]: unknown;
}

export interface EvolveRoundResult {
  parent: string;
  candidate_a: string;
  candidate_b: string;
  imp_a: EvolveRoundImprovement;
  imp_b: EvolveRoundImprovement;
  ab_record: EvolveSelfPlayRecord[];
  gate_record: EvolveSelfPlayRecord[] | null;
  winner: string | null;
  promoted: boolean;
  reason: string;
  // Present on crashed-round entries written by scripts/evolve.py's
  // exception handler. Truncated to the last traceback line; the full
  // traceback lives in data/evolve_crashes.jsonl.
  error?: string | null;
}

export interface EvolveResultsData {
  rounds: EvolveRoundResult[];
}

// --- Control signals (written via PUT) ---

export interface EvolveRunControl {
  stop_run: boolean;
  pause_after_round: boolean;
}

// --- Current round live state (from data/evolve_current_round.json) ---

export type EvolveRoundPhase =
  | "starting"
  | "mirror_games"
  | "claude_prompt"
  | "ab"
  | "gate";

export interface EvolveCurrentRound {
  active: boolean;
  round_index: number | null;
  imp_a_title: string | null;
  imp_b_title: string | null;
  phase: EvolveRoundPhase | string | null;
  cand_a: string | null;
  cand_b: string | null;
  games_played: number | null;
  games_total: number | null;
  score_a: number | null;
  score_b: number | null;
  gate_candidate: string | null;
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

/**
 * Hook for monitoring and controlling an improve-bot-evolve run.
 *
 * Polls the four evolve endpoints at different cadences — the run state
 * updates every round (~minutes), the pool updates on round outcomes,
 * and the results JSONL is append-only — and exposes a mutation helper
 * for writing control signals.
 */
export function useEvolveRun(): UseEvolveRunResult {
  const state = useApi<EvolveRunState>("/api/evolve/state", { pollMs: 3000 });
  const control = useApi<EvolveRunControl>("/api/evolve/control", {
    pollMs: 10000,
  });
  const pool = useApi<EvolvePoolData>("/api/evolve/pool", { pollMs: 10000 });
  const results = useApi<EvolveResultsData>("/api/evolve/results", {
    pollMs: 10000,
  });
  // Live per-game progress — polled faster than state/pool so the score
  // ticker feels responsive between round boundaries.
  const currentRound = useApi<EvolveCurrentRound>(
    "/api/evolve/current-round",
    { pollMs: 2000 },
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
