import { useCallback } from "react";
import { useApi } from "./useApi";
import type { UseApiResult } from "./useApi";

// --- State types (read from backend) ---

export interface IterationResult {
  num: number;
  title: string;
  result: "pass" | "fail" | "borderline";
  delta: string;
}

export interface AdvisedRunState {
  status: "idle" | "running" | "paused" | "stopped" | "completed";
  run_id?: string;
  phase?: number;
  phase_name?: string;
  iteration?: number;
  max_iterations?: number | null;
  games_per_cycle?: number;
  difficulty?: number;
  mode?: string;
  hours_budget?: number;
  elapsed_seconds?: number;
  baseline_win_rate?: number;
  current_win_rate?: number;
  iterations?: IterationResult[];
  current_improvement?: string | null;
  fail_streak?: number;
  updated_at?: string;
}

// --- Control types (written to backend) ---

export interface RewardRuleAdd {
  id: string;
  description: string;
  reward: number;
  active: boolean;
}

export interface AdvisedRunControl {
  games_per_cycle: number | null;
  user_hint: string | null;
  stop_run: boolean;
  reset_loop: boolean;
  difficulty: number | null;
  fail_threshold: number | null;
  reward_rule_add: RewardRuleAdd | null;
  updated_at: string | null;
}

// --- Hook return type ---

export interface UseAdvisedRunResult {
  state: UseApiResult<AdvisedRunState>;
  control: UseApiResult<AdvisedRunControl>;
  sendControl: (patch: Partial<AdvisedRunControl>) => Promise<void>;
}

/**
 * Hook for monitoring and controlling an improve-bot-advised run.
 *
 * Polls the state file every 3 seconds and provides mutation functions
 * for writing control signals.
 */
export function useAdvisedRun(): UseAdvisedRunResult {
  const state = useApi<AdvisedRunState>("/api/advised/state", { pollMs: 3000 });
  const control = useApi<AdvisedRunControl>("/api/advised/control", { pollMs: 10000 });

  const sendControl = useCallback(async (patch: Partial<AdvisedRunControl>) => {
    await fetch("/api/advised/control", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    // Refresh control state after mutation
    control.refresh();
  }, [control]);

  return { state, control, sendControl };
}
