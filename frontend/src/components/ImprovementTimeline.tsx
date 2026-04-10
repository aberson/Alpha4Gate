import { useState, useEffect, type ReactNode } from "react";

interface ModelStats {
  model_version: string;
  wins: number;
  losses: number;
  total: number;
  win_rate: number;
  first_game: string;
  last_game: string;
}

interface ModelsResponse {
  models: ModelStats[];
}

export function ImprovementTimeline() {
  const [data, setData] = useState<ModelsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchData = async () => {
    try {
      const res = await fetch("/api/training/models");
      setData(await res.json());
      setError(null);
    } catch {
      setError("Failed to fetch model data");
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, []);

  if (error) return <div className="error">{error}</div>;
  if (!data) return <div>Loading...</div>;
  if (data.models.length === 0)
    return <div className="empty">No model history yet</div>;

  return (
    <div className="improvement-timeline">
      <h2>Improvement Timeline</h2>
      <table>
        <thead>
          <tr>
            <th>Model</th>
            <th>Win Rate</th>
            <th>Change</th>
            <th>Games Played</th>
          </tr>
        </thead>
        <tbody>
          {data.models.map((model, idx) => {
            const prevRate = idx > 0 ? data.models[idx - 1].win_rate : null;
            const delta = prevRate !== null ? model.win_rate - prevRate : null;

            let changeDisplay: ReactNode;
            if (delta === null) {
              changeDisplay = <span>—</span>;
            } else if (delta > 0) {
              changeDisplay = (
                <span style={{ color: "#22c55e" }}>
                  ▲ +{(delta * 100).toFixed(1)}%
                </span>
              );
            } else if (delta < 0) {
              changeDisplay = (
                <span style={{ color: "#ef4444" }}>
                  ▼ {(delta * 100).toFixed(1)}%
                </span>
              );
            } else {
              changeDisplay = <span>—</span>;
            }

            return (
              <tr key={model.model_version}>
                <td>{model.model_version}</td>
                <td>{(model.win_rate * 100).toFixed(1)}%</td>
                <td>{changeDisplay}</td>
                <td>{model.total}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
