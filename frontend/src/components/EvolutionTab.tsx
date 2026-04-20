import { useState, useCallback } from "react";
import { useEvolveRun } from "../hooks/useEvolveRun";
import type {
  EvolvePoolItem,
  EvolvePoolStatus,
  EvolveRoundResult,
  EvolveRunState,
  EvolveLastResult,
} from "../hooks/useEvolveRun";
import { StaleDataBanner } from "./StaleDataBanner";
import { ConfirmDialog } from "./ConfirmDialog";

// --- Helper components ---

function StatusBadge({ status }: { status: EvolveRunState["status"] }) {
  const colors: Record<string, string> = {
    idle: "#888",
    running: "#2ecc71",
    completed: "#3498db",
    failed: "#e74c3c",
  };
  return (
    <span
      className="state-badge"
      style={{
        display: "inline-block",
        padding: "4px 10px",
        borderRadius: "4px",
        backgroundColor: colors[status] ?? "#888",
        color: "#fff",
        fontWeight: 600,
        textTransform: "uppercase",
        fontSize: "0.85em",
      }}
    >
      {status}
    </span>
  );
}

function PoolStatusBadge({ status }: { status: EvolvePoolStatus | string }) {
  const palette: Record<string, { bg: string; fg: string }> = {
    active: { bg: "rgba(46, 204, 113, 0.2)", fg: "#2ecc71" },
    "consumed-won": { bg: "rgba(52, 152, 219, 0.2)", fg: "#3498db" },
    "consumed-lost": { bg: "rgba(231, 76, 60, 0.2)", fg: "#e74c3c" },
    "consumed-tie": { bg: "rgba(241, 196, 15, 0.2)", fg: "#f1c40f" },
  };
  const tone = palette[status] ?? { bg: "rgba(136,136,136,0.2)", fg: "#888" };
  return (
    <span
      className={`pool-status-badge pool-status-${status}`}
      data-testid={`pool-status-${status}`}
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: "3px",
        backgroundColor: tone.bg,
        color: tone.fg,
        fontSize: "0.8em",
        fontWeight: 600,
        textTransform: "uppercase",
      }}
    >
      {status}
    </span>
  );
}

function OutcomeBadge({ outcome }: { outcome: string }) {
  const palette: Record<string, { bg: string; fg: string }> = {
    promoted: { bg: "rgba(46, 204, 113, 0.2)", fg: "#2ecc71" },
    "discarded-tie": { bg: "rgba(241, 196, 15, 0.2)", fg: "#f1c40f" },
    "discarded-gate": { bg: "rgba(231, 76, 60, 0.2)", fg: "#e74c3c" },
    "discarded-crash": { bg: "rgba(155, 89, 182, 0.2)", fg: "#9b59b6" },
  };
  const tone = palette[outcome] ?? { bg: "rgba(136,136,136,0.2)", fg: "#888" };
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: "3px",
        backgroundColor: tone.bg,
        color: tone.fg,
        fontSize: "0.8em",
        fontWeight: 600,
      }}
    >
      {outcome}
    </span>
  );
}

function LastResultCard({ last }: { last: EvolveLastResult }) {
  return (
    <div
      className="stat-card"
      style={{
        marginBottom: "16px",
        borderLeft: "3px solid #3498db",
        paddingLeft: "12px",
      }}
    >
      <label>Last Round (#{last.round_index})</label>
      <div style={{ marginTop: "4px" }}>
        <div style={{ fontSize: "0.95em" }}>
          <strong>{last.imp_a_title}</strong>
          <span style={{ color: "#888" }}> vs </span>
          <strong>{last.imp_b_title}</strong>
        </div>
        <div style={{ fontSize: "0.85em", marginTop: "6px", color: "#aaa" }}>
          AB score: <code>{last.ab_score[0]}-{last.ab_score[1]}</code>
          {" | "}
          Gate score: <code>{last.gate_score[0]}-{last.gate_score[1]}</code>
          {" | "}
          Outcome: <OutcomeBadge outcome={last.outcome} />
        </div>
        {last.reason ? (
          <div style={{ fontSize: "0.85em", marginTop: "4px", color: "#888" }}>
            Reason: {last.reason}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function PoolView({ pool }: { pool: EvolvePoolItem[] }) {
  if (pool.length === 0) {
    return (
      <div style={{ color: "#888", fontSize: "0.85em" }}>
        Pool not yet generated
      </div>
    );
  }
  return (
    <ul
      className="evolve-pool"
      style={{ listStyle: "none", padding: 0, margin: 0 }}
    >
      {pool.map((item, i) => (
        <li
          key={`${item.rank}-${item.title}-${i}`}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "12px",
            padding: "6px 0",
            borderBottom: "1px solid rgba(255,255,255,0.05)",
          }}
        >
          <span
            style={{
              fontFamily: "monospace",
              color: "#888",
              minWidth: "32px",
            }}
          >
            #{item.rank}
          </span>
          <span style={{ flex: 1 }}>{item.title}</span>
          <span
            style={{
              fontSize: "0.8em",
              color: "#888",
              minWidth: "60px",
              textAlign: "right",
            }}
          >
            {item.type}
          </span>
          <PoolStatusBadge status={item.status} />
        </li>
      ))}
    </ul>
  );
}

function RoundHistoryTable({ rounds }: { rounds: EvolveRoundResult[] }) {
  if (rounds.length === 0) {
    return (
      <div style={{ color: "#888", fontSize: "0.85em" }}>No rounds yet</div>
    );
  }
  return (
    <table style={{ width: "100%", fontSize: "0.85em" }}>
      <thead>
        <tr>
          <th style={{ textAlign: "left" }}>#</th>
          <th style={{ textAlign: "left" }}>Candidate A</th>
          <th style={{ textAlign: "left" }}>Candidate B</th>
          <th style={{ textAlign: "left" }}>Outcome</th>
          <th style={{ textAlign: "left" }}>Winner</th>
          <th style={{ textAlign: "left" }}>Reason</th>
        </tr>
      </thead>
      <tbody>
        {rounds.map((r, i) => {
          const outcome = r.promoted
            ? "promoted"
            : r.winner === null
              ? "discarded-tie"
              : "discarded-gate";
          return (
            <tr key={`${r.candidate_a}-${r.candidate_b}-${i}`}>
              <td>{i + 1}</td>
              <td>{r.candidate_a}</td>
              <td>{r.candidate_b}</td>
              <td>
                <OutcomeBadge outcome={outcome} />
              </td>
              <td>{r.winner ?? "---"}</td>
              <td style={{ color: "#888" }}>{r.reason}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// --- Main component ---

export function EvolutionTab() {
  const { state, control, pool, results, sendControl } = useEvolveRun();
  const run: EvolveRunState = state.data ?? {
    status: "idle",
    parent_start: null,
    parent_current: null,
    started_at: null,
    wall_budget_hours: null,
    rounds_completed: null,
    rounds_promoted: null,
    no_progress_streak: null,
    pool_remaining_count: null,
    last_result: null,
  };
  const ctrl = control.data;
  const isRunning = run.status === "running";
  const isCompleted =
    run.status === "completed" || run.status === "failed";

  const [stopOpen, setStopOpen] = useState(false);
  const [message, setMessage] = useState<string>("");

  const showMessage = useCallback((msg: string) => {
    setMessage(msg);
    setTimeout(() => setMessage(""), 3000);
  }, []);

  const handleStopConfirm = useCallback(async () => {
    setStopOpen(false);
    await sendControl({ stop_run: true });
    showMessage(
      "Stop requested \u2014 run will end at the next round boundary",
    );
  }, [sendControl, showMessage]);

  const handleTogglePause = useCallback(
    async (next: boolean) => {
      await sendControl({ pause_after_round: next });
      showMessage(
        next
          ? "Will pause after the current round"
          : "Pause-after-round cleared",
      );
    },
    [sendControl, showMessage],
  );

  return (
    <div className="evolution-tab training-dashboard">
      {state.isStale && run.status !== "idle" ? (
        <StaleDataBanner lastSuccess={state.lastSuccess} label="Evolve State" />
      ) : null}

      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "12px",
          marginBottom: "16px",
        }}
      >
        <h2 style={{ margin: 0 }}>Evolution</h2>
        <StatusBadge status={run.status} />
      </div>

      <p style={{ color: "#888", fontSize: "0.85em", margin: "0 0 16px" }}>
        Monitor an active <code>/improve-bot-evolve</code> sibling-tournament
        run. Pairs of Claude-proposed improvements play head-to-head; decisive
        winners gate against the current parent and promote to the new
        baseline.
      </p>

      {run.status === "idle" ? (
        <div className="stat-card" style={{ textAlign: "center", padding: "32px" }}>
          <p style={{ color: "#888", fontSize: "1.1em" }}>
            No evolve run active.
          </p>
          <p style={{ color: "#666", fontSize: "0.9em", marginTop: "8px" }}>
            Launch with{" "}
            <code>python scripts/evolve.py --hours 4 --pool-size 10</code>
          </p>
          <p style={{ color: "#666", fontSize: "0.85em", marginTop: "8px" }}>
            See the <code>/improve-bot-evolve</code> skill for the full
            autonomous loop.
          </p>
        </div>
      ) : (
        <>
          {/* Header line: parents */}
          <div
            style={{
              fontSize: "0.9em",
              color: "#aaa",
              marginBottom: "12px",
            }}
          >
            Started{" "}
            <code>{run.started_at ?? "---"}</code>
            {" \u2014 parent "}
            <code>{run.parent_start ?? "---"}</code>
            {" \u2192 current "}
            <code>{run.parent_current ?? "---"}</code>
            {isCompleted && run.stop_reason ? (
              <>
                {" \u2014 Run ended \u2014 "}
                <strong>{run.stop_reason}</strong>
              </>
            ) : isCompleted ? (
              <> {" \u2014 Run ended"}</>
            ) : null}
          </div>

          {isCompleted && run.run_log_path ? (
            <div
              style={{ fontSize: "0.85em", color: "#888", marginBottom: "12px" }}
            >
              Run log: <code>{run.run_log_path}</code>
            </div>
          ) : null}

          {/* Cumulative stats */}
          <div className="status-grid">
            <div className="stat-card">
              <label>Rounds Completed</label>
              <span>{run.rounds_completed ?? 0}</span>
            </div>
            <div className="stat-card">
              <label>Rounds Promoted</label>
              <span>{run.rounds_promoted ?? 0}</span>
            </div>
            <div className="stat-card">
              <label>Pool Remaining</label>
              <span>{run.pool_remaining_count ?? 0}</span>
            </div>
            <div className="stat-card">
              <label>No-Progress Streak</label>
              <span>{run.no_progress_streak ?? 0}</span>
            </div>
            <div className="stat-card">
              <label>Wall Budget</label>
              <span>
                {run.wall_budget_hours !== null
                  ? `${run.wall_budget_hours}h`
                  : "---"}
              </span>
            </div>
          </div>

          {/* Last-round card */}
          {run.last_result ? (
            <section style={{ marginTop: "16px" }}>
              <LastResultCard last={run.last_result} />
            </section>
          ) : null}

          {/* Pool view */}
          <section style={{ marginBottom: "24px" }}>
            <h3>Pool</h3>
            <PoolView pool={pool.data?.pool ?? []} />
          </section>

          {/* Round history */}
          <section style={{ marginBottom: "24px" }}>
            <h3>Round History</h3>
            <RoundHistoryTable rounds={results.data?.rounds ?? []} />
          </section>

          {/* Run actions */}
          <section className="control-panel" aria-labelledby="evolve-actions">
            <h3 id="evolve-actions">Run Actions</h3>
            <div
              className="control-row"
              style={{
                display: "flex",
                gap: "12px",
                alignItems: "center",
                flexWrap: "wrap",
              }}
            >
              <button
                type="button"
                style={{
                  backgroundColor: "#e67e22",
                  color: "#fff",
                  border: "none",
                  padding: "8px 16px",
                  borderRadius: "4px",
                  cursor: isRunning ? "pointer" : "not-allowed",
                  fontWeight: 600,
                  opacity: isRunning ? 1 : 0.5,
                }}
                onClick={() => setStopOpen(true)}
                disabled={!isRunning}
              >
                Stop Run
              </button>
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  fontSize: "0.9em",
                  color: isRunning ? "#eee" : "#666",
                }}
              >
                <input
                  type="checkbox"
                  checked={ctrl?.pause_after_round ?? false}
                  disabled={!isRunning}
                  onChange={(e) => void handleTogglePause(e.target.checked)}
                />
                Pause after current round
              </label>
            </div>
          </section>

          <ConfirmDialog
            open={stopOpen}
            title="Stop evolve run?"
            message="The run will stop gracefully at the next round boundary. In-progress games will complete first."
            confirmLabel="Stop"
            onConfirm={() => void handleStopConfirm()}
            onCancel={() => setStopOpen(false)}
          />
        </>
      )}

      {message ? (
        <div
          role="status"
          style={{
            position: "fixed",
            bottom: "24px",
            right: "24px",
            padding: "12px 20px",
            borderRadius: "6px",
            backgroundColor: "#2c3e50",
            color: "#ecf0f1",
            boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
            fontSize: "0.9em",
            zIndex: 1000,
          }}
        >
          {message}
        </div>
      ) : null}
    </div>
  );
}

export default EvolutionTab;
