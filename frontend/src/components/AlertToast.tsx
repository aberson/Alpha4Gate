import { useEffect, useRef, useState } from "react";
import type { Alert } from "../lib/alertRules";

export const TOAST_AUTO_DISMISS_MS = 8000;
export const TOAST_MAX_VISIBLE = 3;

interface AlertToastProps {
  /**
   * New alerts produced on the most recent poll. Each new entry pushes a
   * toast onto the visible stack. Already-visible toasts are preserved.
   */
  newAlerts: Alert[];
  /** Invoked when the "View" button on a toast is clicked. */
  onView: () => void;
}

interface VisibleToast {
  alert: Alert;
  /** Monotonic sequence number so React keys stay stable across dedupes. */
  seq: number;
}

function severityClass(severity: Alert["severity"]): string {
  if (severity === "error") return "severity-error";
  if (severity === "warning") return "severity-warning";
  return "severity-info";
}

/**
 * Fixed-position toast stack in the top-right corner. Shows up to
 * `TOAST_MAX_VISIBLE` toasts at a time; when a new toast arrives with the
 * stack full, the oldest visible toast is auto-dismissed first (FIFO).
 * Each toast also auto-dismisses after `TOAST_AUTO_DISMISS_MS`.
 */
export function AlertToast({ newAlerts, onView }: AlertToastProps) {
  const [visible, setVisible] = useState<VisibleToast[]>([]);
  const seqRef = useRef<number>(0);
  const timersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  // Push new alerts onto the stack. `newAlerts` is a fresh array reference
  // on every poll where the parent hook detects new IDs, so we can safely
  // depend on it directly.
  useEffect(() => {
    if (newAlerts.length === 0) return;
    setVisible((current) => {
      const existingIds = new Set(current.map((v) => v.alert.id));
      const toAdd: VisibleToast[] = [];
      for (const alert of newAlerts) {
        if (existingIds.has(alert.id)) continue;
        seqRef.current += 1;
        toAdd.push({ alert, seq: seqRef.current });
      }
      if (toAdd.length === 0) return current;
      let next = [...current, ...toAdd];
      // Drop oldest first if over the cap.
      while (next.length > TOAST_MAX_VISIBLE) {
        next = next.slice(1);
      }
      return next;
    });
  }, [newAlerts]);

  // Manage one auto-dismiss timer per visible toast.
  useEffect(() => {
    const timers = timersRef.current;
    for (const v of visible) {
      if (timers.has(v.seq)) continue;
      const handle = setTimeout(() => {
        setVisible((current) => current.filter((x) => x.seq !== v.seq));
        timers.delete(v.seq);
      }, TOAST_AUTO_DISMISS_MS);
      timers.set(v.seq, handle);
    }
    // Clean up timers for toasts that have already been removed from state.
    const liveSeqs = new Set(visible.map((v) => v.seq));
    for (const [seq, handle] of timers) {
      if (!liveSeqs.has(seq)) {
        clearTimeout(handle);
        timers.delete(seq);
      }
    }
  }, [visible]);

  // Clean up all timers on unmount.
  useEffect(() => {
    return () => {
      for (const handle of timersRef.current.values()) {
        clearTimeout(handle);
      }
      timersRef.current.clear();
    };
  }, []);

  if (visible.length === 0) return null;

  return (
    <div
      className="alert-toast-container"
      role="region"
      aria-label="Alert notifications"
      style={{
        position: "fixed",
        top: 24,
        right: 24,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        zIndex: 100,
      }}
    >
      {visible.map((v) => (
        <div
          key={v.seq}
          className={`alert-toast alert-toast-enter ${severityClass(v.alert.severity)}`}
          role="alert"
          data-testid={`alert-toast-${v.alert.ruleId}`}
        >
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600 }}>{v.alert.title}</div>
            <div style={{ fontSize: "0.85em", color: "#bbb", marginTop: 2 }}>
              {v.alert.message}
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              onView();
              setVisible((current) => current.filter((x) => x.seq !== v.seq));
            }}
          >
            View
          </button>
        </div>
      ))}
    </div>
  );
}

export default AlertToast;
