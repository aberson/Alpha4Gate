import { useState } from "react";
import type { ReplaySummary, ReplayDetail } from "../types/game";
import { useApi } from "../hooks/useApi";
import { StaleDataBanner } from "./StaleDataBanner";

interface ReplaysResponse {
  replays: ReplaySummary[];
}

export function ReplayBrowser() {
  const { data: replaysData, isStale, lastSuccess } = useApi<ReplaysResponse>("/api/replays");
  const replays = replaysData?.replays ?? [];
  const [selected, setSelected] = useState<ReplayDetail | null>(null);

  const viewReplay = async (id: string) => {
    const resp = await fetch(`/api/replays/${id}`);
    const data = await resp.json();
    setSelected(data);
  };

  return (
    <div className="replay-browser">
      {isStale && replays.length > 0 ? <StaleDataBanner lastSuccess={lastSuccess} label="Replays" /> : null}
      <h2>Replays</h2>
      <p style={{ color: "#888", fontSize: "0.85em", margin: "0 0 16px" }}>
        SC2 replay files saved from completed games. Select a replay to inspect the game timeline, resource totals, and key events — useful for post-mortem analysis of specific wins or losses.
      </p>
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
