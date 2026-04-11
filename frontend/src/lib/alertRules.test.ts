import { describe, it, expect } from "vitest";
import {
  evaluateAlertRules,
  ruleWinRateDropped,
  ruleTrainingFailed,
  ruleBackendErrors,
  ruleDaemonStoppedUnexpectedly,
  ruleDiskUsageHigh,
  ruleRollbackFired,
  ruleNoTrainingInHours,
  WIN_RATE_DROP_THRESHOLD,
  DISK_USAGE_WARNING_GB,
  NO_TRAINING_HOURS,
  type AlertState,
  type BackendErrorRecord,
  type TrainingStatusResponse,
} from "./alertRules";
import type { DaemonStatus, TriggerState } from "../hooks/useDaemonStatus";

const NOW = "2026-04-09T12:00:00Z";

function mkDaemon(overrides: Partial<DaemonStatus> = {}): DaemonStatus {
  return {
    running: true,
    state: "idle",
    last_run: "2026-04-09T11:00:00Z",
    next_check: null,
    runs_completed: 5,
    last_result: null,
    last_error: null,
    last_rollback: null,
    config: {
      check_interval_seconds: 60,
      min_transitions: 1000,
      min_hours_since_last: 1,
      cycles_per_run: 5,
      current_difficulty: 3,
      max_difficulty: 5,
      win_rate_threshold: 0.6,
    },
    ...overrides,
  };
}

function mkTriggers(overrides: Partial<TriggerState> = {}): TriggerState {
  return {
    transitions_since_last: 0,
    hours_since_last: 1,
    would_trigger: false,
    reason: "",
    ...overrides,
  };
}

function mkState(overrides: Partial<AlertState> = {}): AlertState {
  return {
    daemon: null,
    previousDaemon: null,
    triggers: null,
    history: null,
    status: null,
    promotions: null,
    now: NOW,
    ...overrides,
  };
}

const BYTES_PER_GB = 1024 * 1024 * 1024;

describe("ruleWinRateDropped", () => {
  it("fires when last_10 is more than threshold below last_50", () => {
    const state = mkState({
      history: { win_rates: { last_10: 0.4, last_50: 0.6, last_100: 0.55, overall: 0.5 } },
    });
    const alert = ruleWinRateDropped(state);
    expect(alert).not.toBeNull();
    expect(alert?.ruleId).toBe("win_rate_drop");
    expect(alert?.severity).toBe("warning");
    expect(alert?.id).toBe("win_rate_drop:0.60\u21920.40");
  });

  it("does not fire when drop is below threshold (just-under edge)", () => {
    // drop = 0.15 exactly — threshold is strict inequality (>) so does NOT fire.
    const state = mkState({
      history: { win_rates: { last_10: 0.45, last_50: 0.6, last_100: 0.5, overall: 0.5 } },
    });
    expect(ruleWinRateDropped(state)).toBeNull();
  });

  it("fires at just-above-threshold edge", () => {
    // 0.6 - 0.44 = 0.16 > 0.15.
    const state = mkState({
      history: { win_rates: { last_10: 0.44, last_50: 0.6, last_100: 0.5, overall: 0.5 } },
    });
    expect(ruleWinRateDropped(state)).not.toBeNull();
  });

  it("produces a stable ID for identical state", () => {
    const state = mkState({
      history: { win_rates: { last_10: 0.4, last_50: 0.6, last_100: 0.5, overall: 0.5 } },
    });
    const a = ruleWinRateDropped(state);
    const b = ruleWinRateDropped(state);
    expect(a?.id).toBe(b?.id);
  });

  it("returns null when history is missing", () => {
    expect(ruleWinRateDropped(mkState())).toBeNull();
  });

  it("uses WIN_RATE_DROP_THRESHOLD = 0.15", () => {
    expect(WIN_RATE_DROP_THRESHOLD).toBe(0.15);
  });
});

describe("ruleTrainingFailed", () => {
  it("fires when daemon.last_error is set", () => {
    const state = mkState({
      daemon: mkDaemon({ last_error: "boom", last_run: "2026-04-09T10:00:00Z" }),
    });
    const alert = ruleTrainingFailed(state);
    expect(alert).not.toBeNull();
    expect(alert?.severity).toBe("error");
    expect(alert?.id).toBe("training_failed:2026-04-09T10:00:00Z");
    expect(alert?.message).toBe("boom");
  });

  it("does not fire when last_error is null", () => {
    const state = mkState({ daemon: mkDaemon({ last_error: null }) });
    expect(ruleTrainingFailed(state)).toBeNull();
  });

  it("does not fire when daemon is null", () => {
    expect(ruleTrainingFailed(mkState())).toBeNull();
  });
});

describe("ruleDaemonStoppedUnexpectedly", () => {
  it("fires when transitioning from training to idle with error", () => {
    const state = mkState({
      daemon: mkDaemon({ state: "idle", last_error: "crash", last_run: "2026-04-09T09:00:00Z" }),
      previousDaemon: mkDaemon({ state: "training", last_error: null }),
    });
    const alert = ruleDaemonStoppedUnexpectedly(state);
    expect(alert).not.toBeNull();
    expect(alert?.id).toBe("daemon_stopped:2026-04-09T09:00:00Z");
    expect(alert?.severity).toBe("error");
  });

  it("does not fire when already idle", () => {
    const state = mkState({
      daemon: mkDaemon({ state: "idle", last_error: "crash" }),
      previousDaemon: mkDaemon({ state: "idle", last_error: null }),
    });
    expect(ruleDaemonStoppedUnexpectedly(state)).toBeNull();
  });

  it("does not fire without last_error", () => {
    const state = mkState({
      daemon: mkDaemon({ state: "idle", last_error: null }),
      previousDaemon: mkDaemon({ state: "training" }),
    });
    expect(ruleDaemonStoppedUnexpectedly(state)).toBeNull();
  });

  it("does not fire when previousDaemon is missing", () => {
    const state = mkState({
      daemon: mkDaemon({ state: "idle", last_error: "crash" }),
      previousDaemon: null,
    });
    expect(ruleDaemonStoppedUnexpectedly(state)).toBeNull();
  });
});

describe("ruleDiskUsageHigh", () => {
  it("fires when db + reward logs exceed threshold", () => {
    const state = mkState({
      status: {
        training_active: false,
        current_checkpoint: null,
        total_checkpoints: 0,
        total_games: 0,
        total_transitions: 0,
        db_size_bytes: 60 * BYTES_PER_GB,
        reward_logs_size_bytes: 0,
      },
    });
    const alert = ruleDiskUsageHigh(state);
    expect(alert).not.toBeNull();
    expect(alert?.severity).toBe("warning");
    expect(alert?.id).toBe("disk_usage_high:60");
  });

  it("does not fire at exactly threshold (strict inequality)", () => {
    const state = mkState({
      status: {
        training_active: false,
        current_checkpoint: null,
        total_checkpoints: 0,
        total_games: 0,
        total_transitions: 0,
        db_size_bytes: DISK_USAGE_WARNING_GB * BYTES_PER_GB,
        reward_logs_size_bytes: 0,
      },
    });
    expect(ruleDiskUsageHigh(state)).toBeNull();
  });

  it("fires just above threshold", () => {
    const state = mkState({
      status: {
        training_active: false,
        current_checkpoint: null,
        total_checkpoints: 0,
        total_games: 0,
        total_transitions: 0,
        db_size_bytes: DISK_USAGE_WARNING_GB * BYTES_PER_GB + 1,
        reward_logs_size_bytes: 0,
      },
    });
    expect(ruleDiskUsageHigh(state)).not.toBeNull();
  });
});

describe("ruleRollbackFired", () => {
  it("fires for a rollback entry", () => {
    const state = mkState({
      promotions: [
        {
          timestamp: "2026-04-09T10:00:00Z",
          new_checkpoint: "checkpoint_v23",
          promoted: false,
          reason: "rollback: win rate regressed",
        },
      ],
    });
    const alert = ruleRollbackFired(state);
    expect(alert).not.toBeNull();
    expect(alert?.id).toBe("rollback_fired:checkpoint_v23");
    expect(alert?.severity).toBe("warning");
  });

  it("does not fire for a promotion entry", () => {
    const state = mkState({
      promotions: [
        {
          timestamp: "2026-04-09T10:00:00Z",
          new_checkpoint: "v5",
          promoted: true,
          reason: "win_rate 0.72 > 0.60",
        },
      ],
    });
    expect(ruleRollbackFired(state)).toBeNull();
  });

  it("does not fire for rejected (non-rollback) entry", () => {
    const state = mkState({
      promotions: [
        {
          timestamp: "2026-04-09T10:00:00Z",
          new_checkpoint: "v5",
          promoted: false,
          reason: "below threshold",
        },
      ],
    });
    expect(ruleRollbackFired(state)).toBeNull();
  });

  it("uses the most recent rollback entry for the ID", () => {
    const state = mkState({
      promotions: [
        {
          timestamp: "2026-04-09T09:00:00Z",
          new_checkpoint: "old_rollback",
          promoted: false,
          reason: "rollback: first",
        },
        {
          timestamp: "2026-04-09T11:00:00Z",
          new_checkpoint: "latest_rollback",
          promoted: false,
          reason: "rollback: latest",
        },
      ],
    });
    const alert = ruleRollbackFired(state);
    expect(alert?.id).toBe("rollback_fired:latest_rollback");
  });
});

describe("ruleNoTrainingInHours", () => {
  it("fires when hours_since_last > threshold and daemon is running", () => {
    const state = mkState({
      daemon: mkDaemon({ running: true }),
      triggers: mkTriggers({ hours_since_last: 30 }),
    });
    const alert = ruleNoTrainingInHours(state);
    expect(alert).not.toBeNull();
    expect(alert?.severity).toBe("info");
    expect(alert?.id).toBe("no_training:30");
  });

  it("does not fire at exactly threshold", () => {
    const state = mkState({
      daemon: mkDaemon({ running: true }),
      triggers: mkTriggers({ hours_since_last: NO_TRAINING_HOURS }),
    });
    expect(ruleNoTrainingInHours(state)).toBeNull();
  });

  it("does not fire when daemon stopped", () => {
    const state = mkState({
      daemon: mkDaemon({ running: false }),
      triggers: mkTriggers({ hours_since_last: 48 }),
    });
    expect(ruleNoTrainingInHours(state)).toBeNull();
  });
});

describe("ruleBackendErrors", () => {
  const BASE_STATUS: TrainingStatusResponse = {
    training_active: false,
    current_checkpoint: null,
    total_checkpoints: 0,
    total_games: 0,
    total_transitions: 0,
    db_size_bytes: 0,
    reward_logs_size_bytes: 0,
  };

  function mkRecord(overrides: Partial<BackendErrorRecord> = {}): BackendErrorRecord {
    return {
      ts: "2026-04-10T12:34:56+00:00",
      level: "ERROR",
      logger: "alpha4gate.daemon",
      message: "Game thread crashed",
      ...overrides,
    };
  }

  it("returns null when error_count_since_start is undefined", () => {
    const state = mkState({ status: BASE_STATUS });
    expect(ruleBackendErrors(state)).toBeNull();
  });

  it("returns null when error_count_since_start is 0", () => {
    const state = mkState({
      status: { ...BASE_STATUS, error_count_since_start: 0, recent_errors: [] },
    });
    expect(ruleBackendErrors(state)).toBeNull();
  });

  it("returns null when status itself is missing", () => {
    const state = mkState();
    expect(ruleBackendErrors(state)).toBeNull();
  });

  it("fires with a single error present", () => {
    const record = mkRecord();
    const state = mkState({
      status: {
        ...BASE_STATUS,
        error_count_since_start: 1,
        recent_errors: [record],
      },
    });
    const alert = ruleBackendErrors(state);
    expect(alert).not.toBeNull();
    expect(alert?.ruleId).toBe("backend_error");
    expect(alert?.severity).toBe("error");
    expect(alert?.title).toBe("Backend errors (1)");
    expect(alert?.id).toBe(`backend_error:1:${record.ts}`);
  });

  it("uses the latest (last) record for the message body", () => {
    const older = mkRecord({
      ts: "2026-04-10T11:00:00+00:00",
      logger: "alpha4gate.daemon",
      message: "older",
    });
    const newer = mkRecord({
      ts: "2026-04-10T12:00:00+00:00",
      logger: "alpha4gate.evaluator",
      message: "Game thread crashed",
    });
    const state = mkState({
      status: {
        ...BASE_STATUS,
        error_count_since_start: 2,
        recent_errors: [older, newer],
      },
    });
    const alert = ruleBackendErrors(state);
    expect(alert?.message).toBe("alpha4gate.evaluator: Game thread crashed");
    expect(alert?.id).toBe(`backend_error:2:${newer.ts}`);
  });

  it("produces a different id when the count increases from 3 to 4", () => {
    const rec3 = mkRecord({ ts: "2026-04-10T12:00:00+00:00" });
    const rec4 = mkRecord({ ts: "2026-04-10T12:05:00+00:00" });
    const state3 = mkState({
      status: {
        ...BASE_STATUS,
        error_count_since_start: 3,
        recent_errors: [rec3],
      },
    });
    const state4 = mkState({
      status: {
        ...BASE_STATUS,
        error_count_since_start: 4,
        recent_errors: [rec3, rec4],
      },
    });
    const a = ruleBackendErrors(state3);
    const b = ruleBackendErrors(state4);
    expect(a).not.toBeNull();
    expect(b).not.toBeNull();
    expect(a?.id).not.toBe(b?.id);
  });

  it("marks the alert as persistent (skips toast auto-dismiss)", () => {
    const state = mkState({
      status: {
        ...BASE_STATUS,
        error_count_since_start: 1,
        recent_errors: [mkRecord()],
      },
    });
    const alert = ruleBackendErrors(state);
    expect(alert?.persistent).toBe(true);
  });

  it("falls back to a count-derived message when recent_errors is empty", () => {
    const state = mkState({
      status: {
        ...BASE_STATUS,
        error_count_since_start: 7,
        recent_errors: [],
      },
    });
    const alert = ruleBackendErrors(state);
    expect(alert).not.toBeNull();
    expect(alert?.message).toBe("Backend logged 7 ERROR-level events.");
    expect(alert?.id).toBe("backend_error:7:count:7");
  });
});

describe("evaluateAlertRules", () => {
  it("returns empty array for empty state", () => {
    expect(evaluateAlertRules(mkState())).toEqual([]);
  });

  it("aggregates alerts from multiple rules", () => {
    const state = mkState({
      daemon: mkDaemon({ last_error: "oops", last_run: "2026-04-09T09:00:00Z" }),
      history: { win_rates: { last_10: 0.3, last_50: 0.6, last_100: 0.5, overall: 0.5 } },
    });
    const alerts = evaluateAlertRules(state);
    expect(alerts.map((a) => a.ruleId).sort()).toEqual([
      "training_failed",
      "win_rate_drop",
    ]);
  });

  it("includes ruleBackendErrors when status has backend errors", () => {
    const state = mkState({
      status: {
        training_active: false,
        current_checkpoint: null,
        total_checkpoints: 0,
        total_games: 0,
        total_transitions: 0,
        db_size_bytes: 0,
        reward_logs_size_bytes: 0,
        error_count_since_start: 2,
        recent_errors: [
          {
            ts: "2026-04-10T12:00:00+00:00",
            level: "ERROR",
            logger: "alpha4gate.daemon",
            message: "Game thread crashed",
          },
        ],
      },
    });
    const alerts = evaluateAlertRules(state);
    const ruleIds = alerts.map((a) => a.ruleId);
    expect(ruleIds).toContain("backend_error");
  });
});
