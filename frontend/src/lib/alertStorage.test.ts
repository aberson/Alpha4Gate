import { describe, it, expect, beforeEach } from "vitest";
import {
  ALERT_STORAGE_KEY,
  ackAlert,
  clearHistory,
  dismissAlert,
  loadAlertState,
  markAllRead,
} from "./alertStorage";

describe("alertStorage", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("loadAlertState returns an empty state when nothing is stored", () => {
    const state = loadAlertState();
    expect(state).toEqual({ acked: [], dismissed: [] });
  });

  it("ackAlert persists the ID and is idempotent", () => {
    ackAlert("alert-1");
    ackAlert("alert-1");
    const state = loadAlertState();
    expect(state.acked).toEqual(["alert-1"]);
    expect(state.dismissed).toEqual([]);
  });

  it("dismissAlert persists the ID and is idempotent", () => {
    dismissAlert("alert-2");
    dismissAlert("alert-2");
    const state = loadAlertState();
    expect(state.dismissed).toEqual(["alert-2"]);
    expect(state.acked).toEqual([]);
  });

  it("markAllRead adds all IDs to acked without duplicates", () => {
    ackAlert("a");
    markAllRead(["a", "b", "c"]);
    const state = loadAlertState();
    expect(state.acked.sort()).toEqual(["a", "b", "c"]);
  });

  it("clearHistory wipes both arrays", () => {
    ackAlert("a");
    dismissAlert("b");
    clearHistory();
    const state = loadAlertState();
    expect(state).toEqual({ acked: [], dismissed: [] });
  });

  it("uses the documented localStorage key", () => {
    ackAlert("x");
    const raw = window.localStorage.getItem(ALERT_STORAGE_KEY);
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw ?? "{}");
    expect(parsed).toEqual({ acked: ["x"], dismissed: [] });
  });

  it("tolerates corrupted JSON in storage", () => {
    window.localStorage.setItem(ALERT_STORAGE_KEY, "{not json");
    const state = loadAlertState();
    expect(state).toEqual({ acked: [], dismissed: [] });
  });

  it("isolates between tests via beforeEach clearing", () => {
    // If another test leaked state this will fail.
    expect(loadAlertState()).toEqual({ acked: [], dismissed: [] });
  });
});
