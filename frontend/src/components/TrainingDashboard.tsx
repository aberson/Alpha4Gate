import { useApi } from "../hooks/useApi";
import { StaleDataBanner } from "./StaleDataBanner";

interface TrainingStatus {
  training_active: boolean;
  current_checkpoint: string | null;
  total_checkpoints: number;
  total_games: number;
  total_transitions: number;
  db_size_bytes: number;
}

interface WinRates {
  last_10: number;
  last_50: number;
  last_100: number;
  overall: number;
}

interface TrainingHistory {
  total_games: number;
  win_rates: WinRates;
}

export function TrainingDashboard() {
  const {
    data: status,
    isStale: statusStale,
    isLoading: statusLoading,
    lastSuccess: statusLastSuccess,
  } = useApi<TrainingStatus>("/api/training/status", { pollMs: 5000 });
  const {
    data: history,
    isStale: historyStale,
    isLoading: historyLoading,
    lastSuccess: historyLastSuccess,
  } = useApi<TrainingHistory>("/api/training/history", { pollMs: 5000 });

  if (!status || !history) {
    const anyLoading = statusLoading || historyLoading;
    return (
      <div>
        {anyLoading
          ? "Loading..."
          : "No cached training data available — open this tab once while the backend is up."}
      </div>
    );
  }

  // Stale if either underlying feed is stale. Prefer the older of the
  // two lastSuccess values so the banner is honest about the oldest
  // piece of data on screen.
  const isStale = statusStale || historyStale;
  const lastSuccess =
    statusLastSuccess && historyLastSuccess
      ? statusLastSuccess < historyLastSuccess
        ? statusLastSuccess
        : historyLastSuccess
      : statusLastSuccess ?? historyLastSuccess;

  const dbSizeMB = (status.db_size_bytes / (1024 * 1024)).toFixed(1);

  return (
    <div className="training-dashboard">
      {isStale ? (
        <StaleDataBanner lastSuccess={lastSuccess} label="Training Status" />
      ) : null}
      <h2>Training Status</h2>

      <div className="status-grid">
        <div className="stat-card">
          <label>Current Checkpoint</label>
          <span>{status.current_checkpoint || "None"}</span>
        </div>
        <div className="stat-card">
          <label>Total Games</label>
          <span>{status.total_games}</span>
        </div>
        <div className="stat-card">
          <label>Total Transitions</label>
          <span>{status.total_transitions.toLocaleString()}</span>
        </div>
        <div className="stat-card">
          <label>DB Size</label>
          <span>{dbSizeMB} MB</span>
        </div>
        <div className="stat-card">
          <label>Checkpoints</label>
          <span>{status.total_checkpoints}</span>
        </div>
      </div>

      <h3>Win Rates</h3>
      <table>
        <thead>
          <tr>
            <th>Window</th>
            <th>Win Rate</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Last 10 games</td>
            <td>{(history.win_rates.last_10 * 100).toFixed(1)}%</td>
          </tr>
          <tr>
            <td>Last 50 games</td>
            <td>{(history.win_rates.last_50 * 100).toFixed(1)}%</td>
          </tr>
          <tr>
            <td>Last 100 games</td>
            <td>{(history.win_rates.last_100 * 100).toFixed(1)}%</td>
          </tr>
          <tr>
            <td>Overall</td>
            <td>{(history.win_rates.overall * 100).toFixed(1)}%</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
