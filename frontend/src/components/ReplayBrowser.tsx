import { useState, useEffect } from "react";
import type { ReplaySummary, ReplayDetail } from "../types/game";

export function ReplayBrowser() {
  const [replays, setReplays] = useState<ReplaySummary[]>([]);
  const [selected, setSelected] = useState<ReplayDetail | null>(null);

  useEffect(() => {
    fetch("/api/replays")
      .then((r) => r.json())
      .then((data) => setReplays(data.replays || []))
      .catch(() => {});
  }, []);

  const viewReplay = async (id: string) => {
    const resp = await fetch(`/api/replays/${id}`);
    const data = await resp.json();
    setSelected(data);
  };

  return (
    <div className="replay-browser">
      <h2>Replays</h2>
      {replays.length === 0 ? (
        <p>No replays available.</p>
      ) : (
        <ul>
          {replays.map((r) => (
            <li key={r.id}>
              <button onClick={() => viewReplay(r.id)}>{r.filename}</button>
            </li>
          ))}
        </ul>
      )}

      {selected && (
        <div className="replay-detail">
          <h3>Replay: {selected.id}</h3>
          <p>
            Minerals: {selected.stats.minerals_collected} | Gas:{" "}
            {selected.stats.gas_collected} | Units produced:{" "}
            {selected.stats.units_produced} | Units lost: {selected.stats.units_lost}
          </p>
          {selected.timeline.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Event</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {selected.timeline.map((e, i) => (
                  <tr key={i}>
                    <td>{e.game_time_seconds.toFixed(1)}s</td>
                    <td>{e.event}</td>
                    <td>{e.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
