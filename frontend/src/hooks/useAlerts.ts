import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  evaluateAlertRules,
  type Alert,
  type AlertState,
  type PromotionHistoryLike,
  type TrainingHistoryResponse,
  type TrainingStatusResponse,
} from "../lib/alertRules";
import {
  ackAlert as storageAck,
  clearHistory as storageClear,
  dismissAlert as storageDismiss,
  loadAlertState,
  markAllRead as storageMarkAllRead,
  type AlertPersistedState,
} from "../lib/alertStorage";
import {
  useDaemonStatus,
  type DaemonStatus,
} from "./useDaemonStatus";
import { useApi } from "./useApi";
import type { AdvisedRunState } from "./useAdvisedRun";

export const ALERTS_POLL_INTERVAL_MS = 5000;

export interface UseAlertsResult {
  alerts: Alert[];
  ackedIds: string[];
  unreadCount: number;
  newAlertsThisPoll: Alert[];
  ackAlert: (id: string) => void;
  dismissAlert: (id: string) => void;
  markAllRead: () => void;
  clearHistory: () => void;
}

interface PromotionHistoryResponseShape {
  history?: PromotionHistoryLike[];
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${url} returned ${response.status}`);
  }
  return (await response.json()) as T;
}

/**
 * Orchestrator hook for the alert system.
 *
 * Reuses `useDaemonStatus` for the daemon + triggers polling (already at 2s)
 * and polls the remaining three endpoints every ALERTS_POLL_INTERVAL_MS
 * (5s). Runs `evaluateAlertRules` on every render cycle against the latest
 * cached state, filters out dismissed alerts, diffs the current IDs against
 * the previous cache to produce `newAlertsThisPoll` (used by the toast), and
 * exposes ack/dismiss/markAllRead/clearHistory backed by localStorage.
 *
 * Previous-daemon state for rule (c) is tracked here in a ref so
 * `evaluateAlertRules` stays pure.
 */
export function useAlerts(pollIntervalMs: number = ALERTS_POLL_INTERVAL_MS): UseAlertsResult {
  const { status: daemon, triggers } = useDaemonStatus();
  const { data: advisedState } = useApi<AdvisedRunState>("/api/advised/state", { pollMs: pollIntervalMs });
  const advisedRunActive = advisedState?.status === "running" || advisedState?.status === "paused";

  const [history, setHistory] = useState<TrainingHistoryResponse | null>(null);
  const [statusSnapshot, setStatusSnapshot] = useState<TrainingStatusResponse | null>(null);
  const [promotions, setPromotions] = useState<PromotionHistoryLike[] | null>(null);

  const [persisted, setPersisted] = useState<AlertPersistedState>(() => loadAlertState());

  // Previous daemon snapshot for rule (c). Updated on every daemon change.
  const previousDaemonRef = useRef<DaemonStatus | null>(null);

  // IDs seen on the prior poll, used to compute newAlertsThisPoll.
  const previousIdsRef = useRef<Set<string>>(new Set());

  const mountedRef = useRef<boolean>(true);

  const fetchAll = useCallback(async () => {
    try {
      const [h, s, p] = await Promise.all([
        fetchJson<TrainingHistoryResponse>("/api/training/history"),
        fetchJson<TrainingStatusResponse>("/api/training/status"),
        fetchJson<PromotionHistoryResponseShape>("/api/training/promotions/history"),
      ]);
      if (!mountedRef.current) return;
      setHistory(h);
      setStatusSnapshot(s);
      setPromotions(Array.isArray(p.history) ? p.history : []);
    } catch {
      // Swallow — alerts are derived state, failing poll should not break UI.
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void fetchAll();
    const interval = setInterval(() => {
      void fetchAll();
    }, pollIntervalMs);
    return () => {
      mountedRef.current = false;
      clearInterval(interval);
    };
  }, [fetchAll, pollIntervalMs]);

  // Evaluate rules against the current snapshot. `previousDaemonRef` is the
  // daemon as observed on the previous render where `daemon` actually changed.
  const rawAlerts = useMemo<Alert[]>(() => {
    const state: AlertState = {
      daemon,
      previousDaemon: previousDaemonRef.current,
      triggers,
      history,
      status: statusSnapshot,
      promotions,
      now: new Date().toISOString(),
      advisedRunActive,
    };
    return evaluateAlertRules(state);
  }, [daemon, triggers, history, statusSnapshot, promotions, advisedRunActive]);

  // Update the previous-daemon ref AFTER evaluation so rule (c) compares
  // against the prior snapshot, not the current one. We only overwrite when
  // the daemon object actually changes.
  useEffect(() => {
    previousDaemonRef.current = daemon;
  }, [daemon]);

  const alerts = useMemo<Alert[]>(() => {
    const dismissed = new Set(persisted.dismissed);
    return rawAlerts.filter((a) => !dismissed.has(a.id));
  }, [rawAlerts, persisted.dismissed]);

  // Compute new-this-poll by diffing against the previous id snapshot, then
  // cache the current ids for the next comparison. This runs once per render
  // where `alerts` is a new reference (i.e. the rules produced a new list).
  const newAlertsThisPoll = useMemo<Alert[]>(() => {
    const prev = previousIdsRef.current;
    const fresh: Alert[] = [];
    for (const a of alerts) {
      if (!prev.has(a.id)) fresh.push(a);
    }
    previousIdsRef.current = new Set(alerts.map((a) => a.id));
    return fresh;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [alerts]);

  const unreadCount = useMemo<number>(() => {
    const acked = new Set(persisted.acked);
    return alerts.filter((a) => !acked.has(a.id)).length;
  }, [alerts, persisted.acked]);

  const ackAlert = useCallback((id: string) => {
    const next = storageAck(id);
    setPersisted(next);
  }, []);

  const dismissAlert = useCallback((id: string) => {
    const next = storageDismiss(id);
    setPersisted(next);
  }, []);

  const markAllRead = useCallback(() => {
    const ids = alerts.map((a) => a.id);
    const next = storageMarkAllRead(ids);
    setPersisted(next);
  }, [alerts]);

  const clearHistory = useCallback(() => {
    const next = storageClear();
    setPersisted(next);
  }, []);

  return {
    alerts,
    ackedIds: persisted.acked,
    unreadCount,
    newAlertsThisPoll,
    ackAlert,
    dismissAlert,
    markAllRead,
    clearHistory,
  };
}
