import { type ReactNode } from "react";
import { useApi } from "../hooks/useApi";
import { StaleDataBanner } from "./StaleDataBanner";

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
  const { data, isStale, isLoading, lastSuccess } = useApi<ModelsResponse>(
    "/api/training/models",
    { pollMs: 5000 },
  );

  if (!data) {
    return <div>{isLoading ? "Loading..." : "No cached model data yet."}</div>;
  }

  if (data.models.length === 0) {
    return <div className="empty">No model history yet</div>;
  }

  return (
    <div className="improvement-timeline">
      {isStale ? <StaleDataBanner lastSuccess={lastSuccess} label="Improvement Timeline" /> : null}
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
