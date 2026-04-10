/**
 * localStorage wrapper for alert ack/dismiss state.
 *
 * Per Phase 4 plan D5: single-device persistence, no backend. Key is
 * `alpha4gate.alerts.state`, value is `{acked, dismissed}` string[] arrays.
 * Acked alerts stay visible but de-emphasized; dismissed alerts are filtered
 * out of the rendered list until "Clear history" wipes both arrays.
 */

export const ALERT_STORAGE_KEY = "alpha4gate.alerts.state";

export interface AlertPersistedState {
  acked: string[];
  dismissed: string[];
}

function emptyState(): AlertPersistedState {
  return { acked: [], dismissed: [] };
}

function hasLocalStorage(): boolean {
  try {
    return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
  } catch {
    return false;
  }
}

function readRaw(): AlertPersistedState {
  if (!hasLocalStorage()) return emptyState();
  try {
    const raw = window.localStorage.getItem(ALERT_STORAGE_KEY);
    if (!raw) return emptyState();
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return emptyState();
    const obj = parsed as { acked?: unknown; dismissed?: unknown };
    const acked = Array.isArray(obj.acked)
      ? obj.acked.filter((x): x is string => typeof x === "string")
      : [];
    const dismissed = Array.isArray(obj.dismissed)
      ? obj.dismissed.filter((x): x is string => typeof x === "string")
      : [];
    return { acked, dismissed };
  } catch {
    return emptyState();
  }
}

function writeRaw(state: AlertPersistedState): void {
  if (!hasLocalStorage()) return;
  try {
    window.localStorage.setItem(ALERT_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Quota / privacy mode — swallow, we are a UX convenience.
  }
}

/** Load the current persisted ack/dismiss state. Always returns a valid object. */
export function loadAlertState(): AlertPersistedState {
  return readRaw();
}

/** Mark one alert as acknowledged. Idempotent. */
export function ackAlert(id: string): AlertPersistedState {
  const state = readRaw();
  if (!state.acked.includes(id)) {
    state.acked = [...state.acked, id];
  }
  writeRaw(state);
  return state;
}

/** Mark one alert as dismissed. Idempotent. */
export function dismissAlert(id: string): AlertPersistedState {
  const state = readRaw();
  if (!state.dismissed.includes(id)) {
    state.dismissed = [...state.dismissed, id];
  }
  writeRaw(state);
  return state;
}

/** Acknowledge a batch of alert IDs in one write. */
export function markAllRead(ids: string[]): AlertPersistedState {
  const state = readRaw();
  const set = new Set(state.acked);
  for (const id of ids) set.add(id);
  state.acked = Array.from(set);
  writeRaw(state);
  return state;
}

/** Wipe both ack and dismiss arrays. */
export function clearHistory(): AlertPersistedState {
  const state = emptyState();
  writeRaw(state);
  return state;
}
