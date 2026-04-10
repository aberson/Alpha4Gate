import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Shape returned by GET /api/training/daemon. Mirrors
 * `TrainingDaemon.get_status()` in src/alpha4gate/learning/daemon.py.
 *
 * `last_result` is the orchestrator run summary (cycles, win_rate,
 * final_difficulty); shape is loose on the backend so we type the known
 * fields and leave the rest permissive.
 */
export interface DaemonLastResult {
  cycles?: number;
  win_rate?: number;
  final_difficulty?: number;
}

export interface DaemonLastRollback {
  current_model: string;
  revert_to: string;
  reason: string;
  timestamp: string;
}

export interface DaemonConfigShape {
  check_interval_seconds: number;
  min_transitions: number;
  min_hours_since_last: number;
  cycles_per_run: number;
  current_difficulty: number;
  max_difficulty: number;
  win_rate_threshold: number;
  [key: string]: number | string | boolean | null;
}

export interface DaemonStatus {
  running: boolean;
  state: string;
  last_run: string | null;
  next_check: string | null;
  runs_completed: number;
  last_result: DaemonLastResult | null;
  last_error: string | null;
  last_rollback: DaemonLastRollback | null;
  config: DaemonConfigShape;
}

/**
 * Shape returned by GET /api/training/triggers. Mirrors
 * `TrainingDaemon._evaluate_triggers()` in
 * src/alpha4gate/learning/daemon.py.
 */
export interface TriggerState {
  transitions_since_last: number;
  hours_since_last: number;
  would_trigger: boolean;
  reason: string;
}

export interface UseDaemonStatusResult {
  status: DaemonStatus | null;
  triggers: TriggerState | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

const POLL_INTERVAL_MS = 2000;

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${url} returned ${response.status}`);
  }
  return (await response.json()) as T;
}

/**
 * Polls the training daemon status and trigger evaluation endpoints in
 * parallel every 2000ms. Returns the latest values along with a loading
 * flag, error message, and a `refresh` function that forces an immediate
 * refetch.
 */
export function useDaemonStatus(): UseDaemonStatusResult {
  const [status, setStatus] = useState<DaemonStatus | null>(null);
  const [triggers, setTriggers] = useState<TriggerState | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Track mount state so in-flight fetches don't set state after unmount.
  const mountedRef = useRef<boolean>(true);

  const fetchBoth = useCallback(async () => {
    try {
      const [statusResult, triggerResult] = await Promise.all([
        fetchJson<DaemonStatus>("/api/training/daemon"),
        fetchJson<TriggerState>("/api/training/triggers"),
      ]);
      if (!mountedRef.current) return;
      setStatus(statusResult);
      setTriggers(triggerResult);
      setError(null);
    } catch (e) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : "Failed to fetch daemon status");
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, []);

  const refresh = useCallback(() => {
    void fetchBoth();
  }, [fetchBoth]);

  useEffect(() => {
    mountedRef.current = true;
    void fetchBoth();
    const interval = setInterval(() => {
      void fetchBoth();
    }, POLL_INTERVAL_MS);
    return () => {
      mountedRef.current = false;
      clearInterval(interval);
    };
  }, [fetchBoth]);

  return { status, triggers, loading, error, refresh };
}
