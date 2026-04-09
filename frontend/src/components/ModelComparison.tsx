import { useState, useEffect } from "react";

interface ModelStats {
  model_version: string;
  wins: number;
  losses: number;
  total: number;
  win_rate: number;
  first_game: string;
  last_game: string;
}

export function ModelComparison() {
  const [models, setModels] = useState<ModelStats[]>([]);
  const [best, setBest] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchData = async () => {
    try {
      const [modelsRes, cpRes] = await Promise.all([
        fetch("/api/training/models"),
        fetch("/api/training/checkpoints"),
      ]);
      const modelsData = await modelsRes.json();
      const cpData = await cpRes.json();
      setModels(modelsData.models || []);
      setBest(cpData.best || null);
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
  if (models.length === 0) return null;

  return (
    <div className="model-comparison">
      <h2>Model Comparison</h2>
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
