import { useMemo, useState } from "react";
import type { Alert, AlertSeverity } from "../lib/alertRules";

export type AlertFilter = "all" | "error" | "warning" | "info";

interface AlertsPanelProps {
  alerts: Alert[];
  ackedIds?: string[];
  onAck: (id: string) => void;
  onDismiss: (id: string) => void;
  onMarkAllRead: () => void;
  onClearHistory: () => void;
}

function severityClass(severity: AlertSeverity): string {
  if (severity === "error") return "severity-error";
  if (severity === "warning") return "severity-warning";
  return "severity-info";
}

function formatTimestamp(value: string): string {
  if (!value) return "\u2014";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

/**
 * Full alerts list with severity filter, per-alert ack/dismiss, and bulk
 * "Mark all read" / "Clear history" controls. Acked alerts remain visible
 * but de-emphasized via the `.acked` class. Dismissed alerts never reach
 * this component — the `useAlerts` hook filters them out upstream.
 */
export function AlertsPanel({
  alerts,
  ackedIds = [],
  onAck,
  onDismiss,
  onMarkAllRead,
  onClearHistory,
}: AlertsPanelProps) {
  const [filter, setFilter] = useState<AlertFilter>("all");

  const ackedSet = useMemo(() => new Set(ackedIds), [ackedIds]);

  const sorted = useMemo<Alert[]>(() => {
    const copy = [...alerts];
    copy.sort((a, b) => {
      const ta = new Date(a.timestamp).getTime();
      const tb = new Date(b.timestamp).getTime();
      if (Number.isNaN(ta) || Number.isNaN(tb)) return 0;
      return tb - ta;
    });
    return copy;
  }, [alerts]);

  const filtered = useMemo<Alert[]>(() => {
    if (filter === "all") return sorted;
    return sorted.filter((a) => a.severity === filter);
  }, [sorted, filter]);

  return (
    <div className="alerts-panel training-dashboard">
      <h2>Alerts</h2>

      <div
        className="alerts-controls"
        role="group"
        aria-label="Alert controls"
        style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}
      >
        <button
          type="button"
          onClick={() => setFilter("all")}
          className={filter === "all" ? "active" : ""}
          aria-pressed={filter === "all"}
        >
          All
        </button>
        <button
          type="button"
          onClick={() => setFilter("error")}
          className={filter === "error" ? "active" : ""}
          aria-pressed={filter === "error"}
        >
          Errors
        </button>
        <button
          type="button"
          onClick={() => setFilter("warning")}
          className={filter === "warning" ? "active" : ""}
          aria-pressed={filter === "warning"}
        >
          Warnings
        </button>
        <button
          type="button"
          onClick={() => setFilter("info")}
          className={filter === "info" ? "active" : ""}
          aria-pressed={filter === "info"}
        >
          Info
        </button>
        <span style={{ flex: 1 }} />
        <button type="button" onClick={onMarkAllRead}>
          Mark all read
        </button>
        <button type="button" onClick={onClearHistory}>
          Clear history
        </button>
      </div>

      {filtered.length === 0 ? (
        <div className="alerts-empty" style={{ color: "#888", padding: "12px 0" }}>
          {sorted.length === 0
            ? "No active alerts."
            : "No alerts match the current filter."}
        </div>
      ) : (
        <ul className="alerts-list" style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {filtered.map((alert) => {
            const acked = ackedSet.has(alert.id);
            return (
              <li
                key={alert.id}
                className={`alert-entry ${severityClass(alert.severity)}${
                  acked ? " acked" : ""
                }`}
                data-testid={`alert-entry-${alert.ruleId}`}
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr auto",
                  columnGap: 12,
                  rowGap: 4,
                  padding: "10px 0",
                  borderBottom: "1px solid rgba(255,255,255,0.08)",
                  opacity: acked ? 0.55 : 1,
                  background: acked ? "rgba(255,255,255,0.04)" : "transparent",
                }}
              >
                <div style={{ fontWeight: 600 }}>{alert.title}</div>
                <div style={{ color: "#888", fontSize: "0.85em" }}>
                  {formatTimestamp(alert.timestamp)}
                </div>
                <div style={{ gridColumn: "1 / -1", color: "#bbb", fontSize: "0.9em" }}>
                  {alert.message}
                </div>
                <div style={{ gridColumn: "1 / -1", display: "flex", gap: 8 }}>
                  <button
                    type="button"
                    onClick={() => onAck(alert.id)}
                    disabled={acked}
                  >
                    {acked ? "Acked" : "Ack"}
                  </button>
                  <button type="button" onClick={() => onDismiss(alert.id)}>
                    Dismiss
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default AlertsPanel;
