import { useDaemonStatus } from "../hooks/useDaemonStatus";
import type { DaemonStatus, TriggerState } from "../hooks/useDaemonStatus";

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatHours(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (!Number.isFinite(value)) return "∞";
  return `${value.toFixed(1)}h`;
}

interface StateBadgeProps {
  state: string;
}

function StateBadge({ state }: StateBadgeProps) {
  // gray=idle, yellow=checking, green=training
  let color = "#888";
  let className = "state-badge status-idle";
  if (state === "training") {
    color = "#2ecc71";
    className = "state-badge status-active";
  } else if (state === "checking") {
    color = "#f1c40f";
    className = "state-badge status-checking";
  }
  return (
    <span
      className={className}
      style={{
        display: "inline-block",
        padding: "4px 10px",
        borderRadius: "4px",
        backgroundColor: color,
        color: "#fff",
        fontWeight: 600,
        textTransform: "uppercase",
        fontSize: "0.85em",
      }}
    >
      {state}
    </span>
  );
}

interface TriggerCardProps {
  triggers: TriggerState | null;
}

function TriggerCard({ triggers }: TriggerCardProps) {
  if (!triggers) {
    return (
      <div className="stat-card">
        <label>Trigger Evaluation</label>
        <span>—</span>
      </div>
    );
  }
  const wouldTrigger = triggers.would_trigger;
  const badgeColor = wouldTrigger ? "#2ecc71" : "#888";
  const badgeLabel = wouldTrigger ? "YES" : "NO";
  return (
    <div className="stat-card trigger-card">
      <label>Would Trigger?</label>
      <span>
        <span
          className={wouldTrigger ? "status-active" : "status-idle"}
          style={{
            display: "inline-block",
            padding: "2px 8px",
            borderRadius: "4px",
            backgroundColor: badgeColor,
            color: "#fff",
            fontWeight: 600,
            marginRight: "8px",
          }}
        >
          {badgeLabel}
        </span>
        <span className="trigger-reason">{triggers.reason}</span>
      </span>
    </div>
  );
}

interface LastResultCardProps {
  lastResult: DaemonStatus["last_result"];
}

function LastResultCard({ lastResult }: LastResultCardProps) {
  if (!lastResult) return null;
  return (
    <div className="stat-card last-result-card">
      <label>Last Training Result</label>
      <span>
        cycles={lastResult.cycles ?? "—"}, win_rate=
        {lastResult.win_rate !== undefined && lastResult.win_rate !== null
          ? `${(lastResult.win_rate * 100).toFixed(1)}%`
          : "—"}
        , final_difficulty={lastResult.final_difficulty ?? "—"}
      </span>
    </div>
  );
}

export function LoopStatus() {
  const { status, triggers, loading, error } = useDaemonStatus();

  if (loading && !status && !error) {
    return <div className="loop-status">Loading...</div>;
  }

  if (error && !status) {
    return (
      <div className="loop-status error" style={{ color: "#e74c3c" }}>
        Error: {error}
      </div>
    );
  }

  if (!status) {
    return <div className="loop-status">No daemon data.</div>;
  }

  return (
    <div className="loop-status training-dashboard">
      <h2>Training Loop</h2>

      <div className="status-grid">
        <div className="stat-card">
          <label>Daemon State</label>
          <span>
            <StateBadge state={status.state} />
            {status.running ? null : (
              <span style={{ marginLeft: "8px", color: "#888" }}>(stopped)</span>
            )}
          </span>
        </div>
        <div className="stat-card">
          <label>Runs Completed</label>
          <span>{status.runs_completed}</span>
        </div>
        <div className="stat-card">
          <label>Last Run</label>
          <span>{formatTimestamp(status.last_run)}</span>
        </div>
        <div className="stat-card">
          <label>Next Check</label>
          <span>{formatTimestamp(status.next_check)}</span>
        </div>
        <div className="stat-card">
          <label>Transitions Since Last</label>
          <span>{triggers ? triggers.transitions_since_last.toLocaleString() : "—"}</span>
        </div>
        <div className="stat-card">
          <label>Hours Since Last</label>
          <span>{triggers ? formatHours(triggers.hours_since_last) : "—"}</span>
        </div>
      </div>

      <h3>Trigger Evaluation</h3>
      <div className="status-grid">
        <TriggerCard triggers={triggers} />
      </div>

      {status.last_error ? (
        <>
          <h3>Last Error</h3>
          <div
            className="last-error"
            role="alert"
            style={{
              color: "#e74c3c",
              backgroundColor: "rgba(231, 76, 60, 0.08)",
              padding: "8px 12px",
              borderRadius: "4px",
              border: "1px solid rgba(231, 76, 60, 0.3)",
              whiteSpace: "pre-wrap",
            }}
          >
            {status.last_error}
          </div>
        </>
      ) : null}

      {status.last_result ? (
        <>
          <h3>Last Result</h3>
          <div className="status-grid">
            <LastResultCard lastResult={status.last_result} />
          </div>
        </>
      ) : null}
    </div>
  );
}

export default LoopStatus;
