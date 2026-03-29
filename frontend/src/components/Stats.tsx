import { useState, useEffect } from "react";
import type { Stats as StatsType } from "../types/game";

export function Stats() {
  const [stats, setStats] = useState<StatsType | null>(null);

  useEffect(() => {
    fetch("/api/stats")
      .then((r) => r.json())
      .then(setStats)
      .catch(() => {});
  }, []);

  if (!stats) return <p>Loading stats...</p>;

  const { aggregates } = stats;

  return (
    <div className="stats">
      <h2>Statistics</h2>
      <p>
        Wins: {aggregates.total_wins} / Losses: {aggregates.total_losses}
      </p>

      {Object.keys(aggregates.by_map || {}).length > 0 && (
        <>
          <h3>By Map</h3>
          <ul>
            {Object.entries(aggregates.by_map).map(([map, record]) => (
              <li key={map}>
                {map}: {record.wins}W / {record.losses}L
              </li>
            ))}
          </ul>
        </>
      )}

      <h3>Recent Games</h3>
      {stats.games.length === 0 ? (
        <p>No games played yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Map</th>
              <th>Opponent</th>
              <th>Result</th>
              <th>Duration</th>
              <th>Build</th>
            </tr>
          </thead>
          <tbody>
            {stats.games.slice(-10).reverse().map((g, i) => (
              <tr key={i}>
                <td>{g.map}</td>
                <td>{g.opponent}</td>
                <td>{g.result}</td>
                <td>{Math.floor(g.duration_seconds / 60)}m</td>
                <td>{g.build_order_used}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
