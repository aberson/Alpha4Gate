/**
 * Alert rule evaluation for the transparency dashboard.
 *
 * Pure functions only (no React, no DOM, no fetch). Given a snapshot of the
 * polled backend state plus a small amount of caller-maintained history
 * (previous daemon state), each rule returns zero or one Alert. The
 * orchestrator (useAlerts) calls `evaluateAlertRules` and diffs the result
 * against the previous poll to decide which alerts are "new" (toast-worthy).
 *
 * Alert IDs are stable and state-hashed per Phase 4 plan D3, so the same
 * condition with identical state does NOT spawn a fresh alert on every
 * poll, but a recurrence with different state (e.g. a new rollback for a
 * different checkpoint) DOES produce a new alert.
 */

import type {
  DaemonStatus,
  TriggerState,
} from "../hooks/useDaemonStatus";

export type AlertSeverity = "info" | "warning" | "error";

export interface Alert {
  id: string;
  ruleId: string;
  severity: AlertSeverity;
  title: string;
  message: string;
  timestamp: string;
}

/**
 * Response shape from GET /api/training/history.
 * Mirrors src/alpha4gate/api.py `get_training_history`.
 */
export interface TrainingHistoryWinRates {
  last_10: number;
  last_50: number;
  last_100: number;
  overall: number;
}

export interface TrainingHistoryResponse {
  total_games?: number;
  win_rates?: Partial<TrainingHistoryWinRates>;
  games?: unknown[];
}

/**
 * Response shape from GET /api/training/status.
 * Mirrors src/alpha4gate/api.py `get_training_status`.
 */
export interface TrainingStatusResponse {
  training_active: boolean;
  current_checkpoint: string | null;
  total_checkpoints: number;
  total_games: number;
  total_transitions: number;
  db_size_bytes: number;
  reward_logs_size_bytes: number;
}

/**
 * Subset of promotion-history entry needed by alert rules. The canonical
 * type lives in RecentImprovements.tsx; we re-declare the fields we read
 * here so the alert module does not depend on a UI component.
 */
export interface PromotionHistoryLike {
  timestamp: string;
  new_checkpoint: string;
  promoted: boolean;
  reason: string;
}

/**
 * Bundle of all data the alert rules need on a single poll. `previousDaemon`
 * is the daemon status from the previous tick; rule (c) compares against it
 * to detect a training -> idle transition.
 */
export interface AlertState {
  daemon: DaemonStatus | null;
  previousDaemon: DaemonStatus | null;
  triggers: TriggerState | null;
  history: TrainingHistoryResponse | null;
  status: TrainingStatusResponse | null;
  promotions: PromotionHistoryLike[] | null;
  /** Supplied by the caller so alert timestamps are deterministic in tests. */
  now: string;
}

// ---------------------------------------------------------------------------
// Threshold constants (tunable via plan D2)
// ---------------------------------------------------------------------------

export const WIN_RATE_DROP_THRESHOLD = 0.15;
export const DISK_USAGE_WARNING_GB = 50;
export const NO_TRAINING_HOURS = 24;

const BYTES_PER_GB = 1024 * 1024 * 1024;

// ---------------------------------------------------------------------------
// Individual rule functions. Each returns a single Alert or null.
// ---------------------------------------------------------------------------

/** Rule (a): recent (last_10) win rate dropped well below the baseline (last_50). */
export function ruleWinRateDropped(state: AlertState): Alert | null {
  const winRates = state.history?.win_rates;
  if (!winRates) return null;
  const last10 = winRates.last_10;
  const last50 = winRates.last_50;
  if (typeof last10 !== "number" || typeof last50 !== "number") return null;
  if (!Number.isFinite(last10) || !Number.isFinite(last50)) return null;
  if (last10 >= last50 - WIN_RATE_DROP_THRESHOLD) return null;
  const oldStr = last50.toFixed(2);
  const newStr = last10.toFixed(2);
  return {
    id: `win_rate_drop:${oldStr}\u2192${newStr}`,
    ruleId: "win_rate_drop",
    severity: "warning",
    title: "Win rate dropped",
    message: `Recent win rate ${(last10 * 100).toFixed(1)}% is more than ${(
      WIN_RATE_DROP_THRESHOLD * 100
    ).toFixed(0)}% below the 50-game baseline ${(last50 * 100).toFixed(1)}%.`,
    timestamp: state.now,
  };
}

/** Rule (b): daemon reported a non-null last_error. */
export function ruleTrainingFailed(state: AlertState): Alert | null {
  const daemon = state.daemon;
  if (!daemon) return null;
  const err = daemon.last_error;
  if (!err) return null;
  // Daemon has no dedicated last_error_timestamp; fall back to last_run.
  const stamp = daemon.last_run ?? "unknown";
  return {
    id: `training_failed:${stamp}`,
    ruleId: "training_failed",
    severity: "error",
    title: "Training failed",
    message: err,
    timestamp: state.now,
  };
}

/**
 * Rule (c): the daemon was previously training/checking and is now idle with
 * a last_error set. Requires the previous daemon snapshot, passed in via
 * AlertState.previousDaemon.
 */
export function ruleDaemonStoppedUnexpectedly(state: AlertState): Alert | null {
  const daemon = state.daemon;
  const prev = state.previousDaemon;
  if (!daemon || !prev) return null;
  const wasActive =
    prev.state === "training" ||
    prev.state === "checking" ||
    prev.state === "evaluating";
  if (!wasActive) return null;
  if (daemon.state !== "idle") return null;
  if (!daemon.last_error) return null;
  const stamp = daemon.last_run ?? "unknown";
  return {
    id: `daemon_stopped:${stamp}`,
    ruleId: "daemon_stopped",
    severity: "error",
    title: "Daemon stopped unexpectedly",
    message: `Daemon transitioned from ${prev.state} to idle with error: ${daemon.last_error}`,
    timestamp: state.now,
  };
}

/** Rule (d): combined DB + reward-log storage exceeds the warning threshold. */
export function ruleDiskUsageHigh(state: AlertState): Alert | null {
  const status = state.status;
  if (!status) return null;
  const bytes = (status.db_size_bytes ?? 0) + (status.reward_logs_size_bytes ?? 0);
  if (bytes <= DISK_USAGE_WARNING_GB * BYTES_PER_GB) return null;
  const gb = bytes / BYTES_PER_GB;
  const gbRounded = Math.round(gb);
  return {
    id: `disk_usage_high:${gbRounded}`,
    ruleId: "disk_usage_high",
    severity: "warning",
    title: "Disk usage high",
    message: `Training data is using ${gb.toFixed(1)} GB (warning threshold ${DISK_USAGE_WARNING_GB} GB).`,
    timestamp: state.now,
  };
}

/**
 * Rule (e): the most recent promotion-history entry is a rollback.
 * Rollback detection mirrors RecentImprovements.tsx classifyEntry().
 */
export function ruleRollbackFired(state: AlertState): Alert | null {
  const promos = state.promotions;
  if (!promos || promos.length === 0) return null;
  // Latest = newest timestamp.
  const sorted = [...promos].sort((a, b) => {
    const ta = new Date(a.timestamp).getTime();
    const tb = new Date(b.timestamp).getTime();
    if (Number.isNaN(ta) || Number.isNaN(tb)) return 0;
    return tb - ta;
  });
  const latest = sorted[0];
  if (!latest) return null;
  if (latest.promoted) return null;
  if (typeof latest.reason !== "string" || !latest.reason.startsWith("rollback:")) {
    return null;
  }
  return {
    id: `rollback_fired:${latest.new_checkpoint}`,
    ruleId: "rollback_fired",
    severity: "warning",
    title: "Model regressed (rollback fired)",
    message: `Rolled back to ${latest.new_checkpoint}: ${latest.reason}`,
    timestamp: state.now,
  };
}

/** Rule (f): daemon is running but no training has occurred in N hours. */
export function ruleNoTrainingInHours(state: AlertState): Alert | null {
  const daemon = state.daemon;
  const triggers = state.triggers;
  if (!daemon || !triggers) return null;
  if (!daemon.running) return null;
  const hours = triggers.hours_since_last;
  if (typeof hours !== "number" || !Number.isFinite(hours)) return null;
  if (hours <= NO_TRAINING_HOURS) return null;
  const hoursRounded = Math.round(hours);
  return {
    id: `no_training:${hoursRounded}`,
    ruleId: "no_training",
    severity: "info",
    title: "No training in a while",
    message: `Daemon has been running but no training has happened for ${hoursRounded} hours.`,
    timestamp: state.now,
  };
}

/** Evaluate every rule and return the non-null results. */
export function evaluateAlertRules(state: AlertState): Alert[] {
  const rules: Array<(s: AlertState) => Alert | null> = [
    ruleWinRateDropped,
    ruleTrainingFailed,
    ruleDaemonStoppedUnexpectedly,
    ruleDiskUsageHigh,
    ruleRollbackFired,
    ruleNoTrainingInHours,
  ];
  const out: Alert[] = [];
  for (const rule of rules) {
    const alert = rule(state);
    if (alert) out.push(alert);
  }
  return out;
}
