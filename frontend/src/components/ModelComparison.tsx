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

interface CheckpointsResponse {
  best: string | null;
  [key: string]: unknown;
}

export function ModelComparison() {
  const { data: modelsData, isStale: modelsStale, lastSuccess: modelsLast } =
    useApi<ModelsResponse>("/api/training/models", { pollMs: 5000 });
  const { data: cpData, isStale: cpStale, lastSuccess: cpLast } =
    useApi<CheckpointsResponse>("/api/training/checkpoints", { pollMs: 5000 });

  const models = modelsData?.models ?? [];
  const best = cpData?.best ?? null;

  if (models.length === 0) return null;

  const isStale = modelsStale || cpStale;
  const lastSuccess =
    modelsLast && cpLast
      ? modelsLast < cpLast ? modelsLast : cpLast
      : modelsLast ?? cpLast;

  return (
    <div className="model-comparison">
      {isStale ? <StaleDataBanner lastSuccess={lastSuccess} label="Model Comparison" /> : null}
      <h2>Model Comparison</h2>
      <p style={{ color: "#888", fontSize: "0.85em", margin: "0 0 16px" }}>
        Head-to-head win rate comparison across all trained model versions. The starred row is the current promoted best. Use this to judge whether recent training cycles actually moved the needle.
      </p>
      <table>
        <thead>
          <tr>
            <th>Model Version</th>
            <th>Games</th>
            <th>Wins</th>
            <th>Losses</th>
            <th>Win Rate</th>
            <th>First Game</th>
            <th>Last Game</th>
          </tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={m.model_version} className={m.model_version === best ? "best" : ""}>
              <td>
                {m.model_version}
                {m.model_version === best && " \u2605"}
              </td>
              <td>{m.total}</td>
              <td>{m.wins}</td>
              <td>{m.losses}</td>
              <td>{(m.win_rate * 100).toFixed(1)}%</td>
              <td>{m.first_game}</td>
              <td>{m.last_game}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
