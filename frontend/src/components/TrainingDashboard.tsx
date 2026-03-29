import { useState, useEffect } from "react";

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
  const [status, setStatus] = useState<TrainingStatus | null>(null);
  const [history, setHistory] = useState<TrainingHistory | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchData = async () => {
    try {
      const [statusRes, historyRes] = await Promise.all([
        fetch("/api/training/status"),
        fetch("/api/training/history"),
      ]);
      setStatus(await statusRes.json());
      setHistory(await historyRes.json());
      setError(null);
    } catch (e) {
      setError("Failed to fetch training data");
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, []);

  if (error) return <div className="error">{error}</div>;
  if (!status || !history) return <div>Loading...</div>;

  const dbSizeMB = (status.db_size_bytes / (1024 * 1024)).toFixed(1);

  return (
    <div className="training-dashboard">
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
