import { useState, useEffect } from "react";

interface Checkpoint {
  name: string;
  file: string;
  metadata?: {
    type?: string;
    epochs?: number;
    agreement?: number;
    cycle?: number;
    difficulty?: number;
    win_rate?: number;
    final_loss?: number;
  };
}

interface CheckpointData {
  checkpoints: Checkpoint[];
  best: string | null;
}

export function CheckpointList() {
  const [data, setData] = useState<CheckpointData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/training/checkpoints")
      .then((r) => r.json())
      .then(setData)
      .catch(() => setError("Failed to load checkpoints"));
  }, []);

  if (error) return <div className="error">{error}</div>;
  if (!data) return <div>Loading...</div>;
  if (data.checkpoints.length === 0)
    return <div className="empty">No checkpoints yet</div>;

  return (
    <div className="checkpoint-list">
      <h2>Checkpoints</h2>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Details</th>
            <th>Best</th>
          </tr>
        </thead>
        <tbody>
          {data.checkpoints.map((cp) => (
            <tr key={cp.name} className={cp.name === data.best ? "best" : ""}>
              <td>{cp.name}</td>
              <td>{cp.metadata?.type || "unknown"}</td>
              <td>
                {cp.metadata?.agreement !== undefined &&
                  `Agreement: ${(cp.metadata.agreement * 100).toFixed(1)}%`}
                {cp.metadata?.win_rate !== undefined &&
                  ` | Win: ${(cp.metadata.win_rate * 100).toFixed(1)}%`}
                {cp.metadata?.difficulty !== undefined &&
                  ` | Diff: ${cp.metadata.difficulty}`}
              </td>
              <td>{cp.name === data.best ? "★" : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
