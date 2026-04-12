import { useState, useCallback } from "react";
import { useAdvisedRun } from "../hooks/useAdvisedRun";
import type { AdvisedRunState, IterationResult } from "../hooks/useAdvisedRun";
import { StaleDataBanner } from "./StaleDataBanner";
import { ConfirmDialog } from "./ConfirmDialog";

// --- Helper components ---

function StatusBadge({ status }: { status: AdvisedRunState["status"] }) {
  const colors: Record<string, string> = {
    idle: "#888",
    running: "#2ecc71",
    paused: "#f1c40f",
    stopped: "#e67e22",
    completed: "#3498db",
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

function ProgressBar({ elapsed, budget }: { elapsed: number; budget: number }) {
  const pct = budget > 0 ? Math.min(100, (elapsed / (budget * 3600)) * 100) : 0;
  const elapsedMin = Math.floor(elapsed / 60);
  const budgetMin = Math.floor(budget * 60);
  return (
    <div style={{ width: "100%" }}>
      <div
        style={{
          height: "8px",
          borderRadius: "4px",
          backgroundColor: "rgba(255,255,255,0.1)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${pct}%`,
            backgroundColor: pct > 90 ? "#e74c3c" : "#2ecc71",
            borderRadius: "4px",
            transition: "width 0.3s ease",
          }}
        />
      </div>
      <div style={{ fontSize: "0.8em", color: "#aaa", marginTop: "4px" }}>
        {elapsedMin}m / {budgetMin}m ({pct.toFixed(0)}%)
      </div>
    </div>
  );
}

function IterationTable({ iterations }: { iterations: IterationResult[] }) {
  if (iterations.length === 0) {
    return <div style={{ color: "#888", fontSize: "0.85em" }}>No iterations yet</div>;
  }
  return (
    <table style={{ width: "100%", fontSize: "0.85em" }}>
      <thead>
        <tr>
          <th style={{ textAlign: "left" }}>#</th>
          <th style={{ textAlign: "left" }}>Improvement</th>
          <th style={{ textAlign: "center" }}>Result</th>
          <th style={{ textAlign: "right" }}>Delta</th>
        </tr>
      </thead>
      <tbody>
        {iterations.map((it) => (
          <tr key={it.num}>
            <td>{it.num}</td>
            <td>{it.title}</td>
            <td style={{ textAlign: "center" }}>
              <span
                style={{
                  padding: "2px 6px",
                  borderRadius: "3px",
                  fontSize: "0.85em",
                  fontWeight: 600,
                  backgroundColor:
                    it.result === "pass"
                      ? "rgba(46, 204, 113, 0.2)"
                      : it.result === "fail"
                        ? "rgba(231, 76, 60, 0.2)"
                        : "rgba(241, 196, 15, 0.2)",
                  color:
                    it.result === "pass"
                      ? "#2ecc71"
                      : it.result === "fail"
                        ? "#e74c3c"
                        : "#f1c40f",
                }}
              >
                {it.result}
              </span>
            </td>
            <td style={{ textAlign: "right", fontFamily: "monospace" }}>{it.delta}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// --- Main component ---

export function AdvisedControlPanel() {
  const { state, control, sendControl } = useAdvisedRun();
  const run = state.data ?? { status: "idle" as const };
  const ctrl = control.data;
  const isActive = run.status === "running" || run.status === "paused";

  // --- Local form state ---
  const [gamesInput, setGamesInput] = useState<string>("");
  const [difficultyInput, setDifficultyInput] = useState<string>("");
  const [failThresholdInput, setFailThresholdInput] = useState<string>("30");
  const [hintText, setHintText] = useState<string>("");
  const [rewardId, setRewardId] = useState<string>("");
  const [rewardDesc, setRewardDesc] = useState<string>("");
  const [rewardValue, setRewardValue] = useState<string>("0.01");

  // --- Confirm dialogs ---
  const [stopOpen, setStopOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);

  // --- Feedback messages ---
  const [message, setMessage] = useState<string>("");

  const showMessage = useCallback((msg: string) => {
    setMessage(msg);
    setTimeout(() => setMessage(""), 3000);
  }, []);

  // --- Handlers ---

  const handleApplyGames = useCallback(async () => {
    const val = parseInt(gamesInput, 10);
    if (!val || val < 1 || val > 50) {
      showMessage("Games must be 1-50");
      return;
    }
    await sendControl({ games_per_cycle: val });
    showMessage(`Games per cycle set to ${val} (applies next iteration)`);
  }, [gamesInput, sendControl, showMessage]);

  const handleApplyDifficulty = useCallback(async () => {
    const val = parseInt(difficultyInput, 10);
    if (!val || val < 1 || val > 10) {
      showMessage("Difficulty must be 1-10");
      return;
    }
    await sendControl({ difficulty: val });
    showMessage(`Difficulty set to ${val} (applies next iteration)`);
  }, [difficultyInput, sendControl, showMessage]);

  const handleApplyFailThreshold = useCallback(async () => {
    const val = parseInt(failThresholdInput, 10);
    if (!val || val < 5 || val > 80) {
      showMessage("Fail threshold must be 5-80%");
      return;
    }
    await sendControl({ fail_threshold: val });
    showMessage(`Fail threshold set to ${val}%`);
  }, [failThresholdInput, sendControl, showMessage]);

  const handleSendHint = useCallback(async () => {
    if (!hintText.trim()) {
      showMessage("Enter a hint first");
      return;
    }
    await sendControl({ user_hint: hintText.trim() });
    showMessage("Hint queued for next advisor analysis");
  }, [hintText, sendControl, showMessage]);

  const handleClearHint = useCallback(async () => {
    await sendControl({ user_hint: null });
    setHintText("");
    showMessage("Hint cleared");
  }, [sendControl, showMessage]);

  const handleAddReward = useCallback(async () => {
    if (!rewardId.trim() || !rewardDesc.trim()) {
      showMessage("Reward ID and description required");
      return;
    }
    const val = parseFloat(rewardValue);
    if (!Number.isFinite(val)) {
      showMessage("Invalid reward value");
      return;
    }
    await sendControl({
      reward_rule_add: {
        id: rewardId.trim(),
        description: rewardDesc.trim(),
        reward: val,
        active: true,
      },
    });
    showMessage(`Reward rule "${rewardId.trim()}" queued`);
    setRewardId("");
    setRewardDesc("");
    setRewardValue("0.01");
  }, [rewardId, rewardDesc, rewardValue, sendControl, showMessage]);

  const handleStopConfirm = useCallback(async () => {
    setStopOpen(false);
    await sendControl({ stop_run: true });
    // Also shut down the server process so the daemon, game runners,
    // and uv wrapper all exit cleanly.
    try {
      await fetch("/api/shutdown", { method: "POST" });
    } catch {
      // Server may already be gone — that's fine.
    }
    showMessage("Stop signal sent — daemon stopped, server shutting down");
  }, [sendControl, showMessage]);

  const handleResetConfirm = useCallback(async () => {
    setResetOpen(false);
    await sendControl({ reset_loop: true });
    showMessage("Reset signal sent (reverts to baseline at next phase boundary)");
  }, [sendControl, showMessage]);

  return (
    <div className="advised-control-panel training-dashboard">
      {state.isStale && run.status !== "idle" ? (
        <StaleDataBanner lastSuccess={state.lastSuccess} label="Advisor State" />
      ) : null}

      {/* Header: status + progress */}
      <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "16px" }}>
        <h2 style={{ margin: 0 }}>Advisor Control Panel</h2>
        <StatusBadge status={run.status} />
      </div>

      {run.status === "idle" ? (
        <div className="stat-card" style={{ textAlign: "center", padding: "32px" }}>
          <p style={{ color: "#888", fontSize: "1.1em" }}>
            No advised run active. Start one with <code>/improve-bot-advised</code>
          </p>
          <p style={{ color: "#666", fontSize: "0.85em", marginTop: "8px" }}>
            Once running, this panel shows live status and lets you control the loop.
          </p>
        </div>
      ) : (
        <>
          {/* Section 1: Status cards */}
          <div className="status-grid">
            <div className="stat-card">
              <label>Phase</label>
              <span>
                {run.phase !== undefined ? `${run.phase}. ` : ""}
                {run.phase_name ?? "---"}
              </span>
            </div>
            <div className="stat-card">
              <label>Iteration</label>
              <span>
                {run.iteration ?? 0}
                {run.fail_streak ? ` (${run.fail_streak} fails)` : ""}
              </span>
            </div>
            <div className="stat-card">
              <label>Mode</label>
              <span>{run.mode ?? "---"}</span>
            </div>
            <div className="stat-card">
              <label>Games / Cycle</label>
              <span>{run.games_per_cycle ?? "---"}</span>
            </div>
            <div className="stat-card">
              <label>Difficulty</label>
              <span>{run.difficulty ?? "---"}</span>
            </div>
            <div className="stat-card">
              <label>Win Rate</label>
              <span>
                {run.baseline_win_rate !== undefined
                  ? `${(run.baseline_win_rate * 100).toFixed(0)}%`
                  : "---"}
                {" -> "}
                {run.current_win_rate !== undefined
                  ? `${(run.current_win_rate * 100).toFixed(0)}%`
                  : "---"}
              </span>
            </div>
          </div>

          {/* Progress bar */}
          {run.elapsed_seconds !== undefined && run.hours_budget !== undefined ? (
            <div style={{ margin: "16px 0" }}>
              <ProgressBar elapsed={run.elapsed_seconds} budget={run.hours_budget} />
            </div>
          ) : null}

          {/* Current improvement */}
          {run.current_improvement ? (
            <div
              className="stat-card"
              style={{
                marginBottom: "16px",
                borderLeft: "3px solid #3498db",
                paddingLeft: "12px",
              }}
            >
              <label>Current Improvement</label>
              <span>{run.current_improvement}</span>
            </div>
          ) : null}

          {/* Iteration history */}
          {run.iterations && run.iterations.length > 0 ? (
            <section style={{ marginBottom: "24px" }}>
              <h3>Iteration History</h3>
              <IterationTable iterations={run.iterations} />
            </section>
          ) : null}

          {/* Section 2: Loop controls */}
          <section className="control-panel" aria-labelledby="advised-loop-controls">
            <h3 id="advised-loop-controls">Loop Controls</h3>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr 1fr",
                gap: "12px",
                alignItems: "end",
              }}
            >
              <div>
                <label style={{ fontSize: "0.85em", color: "#aaa", display: "block", marginBottom: "4px" }}>
                  Games per cycle (1-50)
                </label>
                <div style={{ display: "flex", gap: "8px" }}>
                  <input
                    type="number"
                    min="1"
                    max="50"
                    step="1"
                    value={gamesInput}
                    placeholder={String(run.games_per_cycle ?? 10)}
                    onChange={(e) => setGamesInput(e.target.value)}
                    style={{ padding: "6px 8px", width: "80px" }}
                  />
                  <button type="button" onClick={() => void handleApplyGames()}>
                    Apply
                  </button>
                </div>
              </div>
              <div>
                <label style={{ fontSize: "0.85em", color: "#aaa", display: "block", marginBottom: "4px" }}>
                  Difficulty (1-10)
                </label>
                <div style={{ display: "flex", gap: "8px" }}>
                  <input
                    type="number"
                    min="1"
                    max="10"
                    step="1"
                    value={difficultyInput}
                    placeholder={String(run.difficulty ?? 1)}
                    onChange={(e) => setDifficultyInput(e.target.value)}
                    style={{ padding: "6px 8px", width: "80px" }}
                  />
                  <button type="button" onClick={() => void handleApplyDifficulty()}>
                    Apply
                  </button>
                </div>
              </div>
              <div>
                <label style={{ fontSize: "0.85em", color: "#aaa", display: "block", marginBottom: "4px" }}>
                  Fail threshold (5-80%)
                </label>
                <div style={{ display: "flex", gap: "8px" }}>
                  <input
                    type="number"
                    min="5"
                    max="80"
                    step="5"
                    value={failThresholdInput}
                    placeholder="30"
                    onChange={(e) => setFailThresholdInput(e.target.value)}
                    style={{ padding: "6px 8px", width: "80px" }}
                  />
                  <button type="button" onClick={() => void handleApplyFailThreshold()}>
                    Apply
                  </button>
                </div>
              </div>
            </div>
          </section>

          {/* Section 3: Strategic guidance */}
          <section className="control-panel" aria-labelledby="advised-guidance">
            <h3 id="advised-guidance">Strategic Guidance</h3>
            {ctrl?.user_hint ? (
              <div
                style={{
                  marginBottom: "12px",
                  padding: "8px 12px",
                  borderRadius: "4px",
                  backgroundColor: "rgba(52, 152, 219, 0.1)",
                  border: "1px solid rgba(52, 152, 219, 0.3)",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div>
                  <span style={{ fontSize: "0.8em", color: "#3498db", fontWeight: 600 }}>
                    PENDING HINT:
                  </span>{" "}
                  <span style={{ fontSize: "0.9em" }}>{ctrl.user_hint}</span>
                </div>
                <button
                  type="button"
                  onClick={() => void handleClearHint()}
                  style={{ padding: "4px 8px", fontSize: "0.8em" }}
                >
                  Clear
                </button>
              </div>
            ) : null}
            <textarea
              value={hintText}
              onChange={(e) => setHintText(e.target.value)}
              placeholder="e.g. You don't use attack walk, try attack-walking your army at 4:00..."
              rows={3}
              style={{
                width: "100%",
                padding: "8px",
                borderRadius: "4px",
                border: "1px solid #444",
                backgroundColor: "#1e1e1e",
                color: "#eee",
                resize: "vertical",
                fontFamily: "inherit",
                fontSize: "0.9em",
              }}
            />
            <div style={{ marginTop: "8px" }}>
              <button type="button" onClick={() => void handleSendHint()}>
                Send to Advisor
              </button>
              <span style={{ fontSize: "0.8em", color: "#888", marginLeft: "12px" }}>
                Injected into the next strategic analysis phase
              </span>
            </div>
          </section>

          {/* Section 4: Reward shaping */}
          <section className="control-panel" aria-labelledby="advised-rewards">
            <h3 id="advised-rewards">Add Reward Rule</h3>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 2fr auto",
                gap: "8px",
                alignItems: "end",
              }}
            >
              <div>
                <label style={{ fontSize: "0.85em", color: "#aaa", display: "block", marginBottom: "4px" }}>
                  Rule ID
                </label>
                <input
                  type="text"
                  value={rewardId}
                  onChange={(e) => setRewardId(e.target.value)}
                  placeholder="e.g. attack-walk-reward"
                  style={{ padding: "6px 8px", width: "100%" }}
                />
              </div>
              <div>
                <label style={{ fontSize: "0.85em", color: "#aaa", display: "block", marginBottom: "4px" }}>
                  Description
                </label>
                <input
                  type="text"
                  value={rewardDesc}
                  onChange={(e) => setRewardDesc(e.target.value)}
                  placeholder="e.g. Reward for using attack-walk command"
                  style={{ padding: "6px 8px", width: "100%" }}
                />
              </div>
              <div>
                <label style={{ fontSize: "0.85em", color: "#aaa", display: "block", marginBottom: "4px" }}>
                  Reward
                </label>
                <div style={{ display: "flex", gap: "8px" }}>
                  <input
                    type="number"
                    step="0.01"
                    value={rewardValue}
                    onChange={(e) => setRewardValue(e.target.value)}
                    style={{ padding: "6px 8px", width: "80px" }}
                  />
                  <button type="button" onClick={() => void handleAddReward()}>
                    Add
                  </button>
                </div>
              </div>
            </div>
            <div style={{ fontSize: "0.8em", color: "#888", marginTop: "8px" }}>
              Rule is queued and applied before the next training cycle
            </div>
          </section>

          {/* Section 5: Danger zone */}
          <section className="control-panel" aria-labelledby="advised-actions">
            <h3 id="advised-actions">Run Actions</h3>
            <div className="control-row" style={{ gap: "12px" }}>
              <button
                type="button"
                style={{
                  backgroundColor: "#e67e22",
                  color: "#fff",
                  border: "none",
                  padding: "8px 16px",
                  borderRadius: "4px",
                  cursor: "pointer",
                  fontWeight: 600,
                }}
                onClick={() => setStopOpen(true)}
                disabled={!isActive}
              >
                Stop Run
              </button>
              <button
                type="button"
                className="destructive"
                style={{
                  backgroundColor: "#e74c3c",
                  color: "#fff",
                  border: "none",
                  padding: "8px 16px",
                  borderRadius: "4px",
                  cursor: "pointer",
                  fontWeight: 600,
                }}
                onClick={() => setResetOpen(true)}
                disabled={!isActive}
              >
                Reset Loop
              </button>
            </div>
          </section>

          <ConfirmDialog
            open={stopOpen}
            title="Stop advised run?"
            message="The run will stop gracefully at the next phase boundary. In-progress games will complete first."
            confirmLabel="Stop"
            onConfirm={() => void handleStopConfirm()}
            onCancel={() => setStopOpen(false)}
          />
          <ConfirmDialog
            open={resetOpen}
            title="Reset training loop?"
            message="This reverts to the baseline git tag, clears iteration context, and restarts from Phase 1. Changes from this run will be lost."
            confirmLabel="Reset"
            destructive
            onConfirm={() => void handleResetConfirm()}
            onCancel={() => setResetOpen(false)}
          />
        </>
      )}

      {/* Feedback toast */}
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

export default AdvisedControlPanel;
