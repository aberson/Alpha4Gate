import { useState } from "react";
import { useApi } from "../hooks/useApi";
import { StaleDataBanner } from "./StaleDataBanner";

interface DifficultyStats {
  difficulty: number;
  total: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_duration_secs: number;
  avg_reward: number;
  min_reward: number;
  max_reward: number;
}

interface RecentGame {
  game_id: string;
  map_name: string;
  difficulty: number;
  result: string;
  duration: number;
  reward: number;
  model_version: string;
  created_at: string;
}

interface StatsResponse {
  total_games: number;
  overall: { wins: number; losses: number; win_rate: number };
  by_difficulty: DifficultyStats[];
  recent_games: RecentGame[];
  win_trend: { index: number; win_rate: number; timestamp: string }[];
}

type DifficultyFilter = "all" | number;

function formatDuration(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

function WinRateBar({ rate, size = "normal" }: { rate: number; size?: "normal" | "large" }) {
  const pct = Math.round(rate * 100);
  const color = pct >= 50 ? "#2ecc71" : pct >= 25 ? "#f39c12" : "#e74c3c";
  const height = size === "large" ? "16px" : "8px";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
      <div
        style={{
          flex: 1,
          height,
          borderRadius: "4px",
          backgroundColor: "rgba(255,255,255,0.1)",
          overflow: "hidden",
          minWidth: "60px",
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${pct}%`,
            backgroundColor: color,
            borderRadius: "4px",
            transition: "width 0.3s",
          }}
        />
      </div>
      <span style={{ fontWeight: 600, color, minWidth: "40px" }}>{pct}%</span>
    </div>
  );
}

export function Stats() {
  const { data, isStale, isLoading, lastSuccess } = useApi<StatsResponse>(
    "/api/stats",
    { pollMs: 10000, cacheKey: "/api/stats/v2" },
  );
  const [diffFilter, setDiffFilter] = useState<DifficultyFilter>("all");

  if (!data) {
    return (
      <div className="stats training-dashboard">
        <h2>Statistics</h2>
        <p>{isLoading ? "Loading..." : "No game data available."}</p>
      </div>
    );
  }

  const filteredGames =
    diffFilter === "all"
      ? data.recent_games
      : data.recent_games.filter((g) => g.difficulty === diffFilter);

  return (
    <div className="stats training-dashboard">
      {isStale ? <StaleDataBanner lastSuccess={lastSuccess} label="Stats" /> : null}
      <h2>Statistics</h2>

      {/* Overall summary cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: "12px",
          marginBottom: "24px",
        }}
      >
        <div className="stat-card">
          <label>Total Games</label>
          <span style={{ fontSize: "1.5em", fontWeight: 700 }}>{data.total_games}</span>
        </div>
        <div className="stat-card">
          <label>Overall Record</label>
          <span>
            <span style={{ color: "#2ecc71", fontWeight: 600 }}>{data.overall.wins}W</span>
            {" / "}
            <span style={{ color: "#e74c3c", fontWeight: 600 }}>{data.overall.losses}L</span>
          </span>
        </div>
        <div className="stat-card">
          <label>Win Rate</label>
          <WinRateBar rate={data.overall.win_rate} size="large" />
        </div>
        <div className="stat-card">
          <label>Difficulties Played</label>
          <span style={{ fontSize: "1.2em", fontWeight: 600 }}>
            {data.by_difficulty.map((d) => d.difficulty).join(", ")}
          </span>
        </div>
      </div>

      {/* Per-difficulty breakdown */}
      <section style={{ marginBottom: "24px" }}>
        <h3>By Difficulty</h3>
        <table style={{ width: "100%", fontSize: "0.9em" }}>
          <thead>
            <tr>
              <th style={{ textAlign: "center" }}>Diff</th>
              <th style={{ textAlign: "center" }}>Games</th>
              <th style={{ textAlign: "center" }}>W / L</th>
              <th style={{ textAlign: "left", minWidth: "120px" }}>Win Rate</th>
              <th style={{ textAlign: "center" }}>Avg Duration</th>
              <th style={{ textAlign: "center" }}>Avg Reward</th>
              <th style={{ textAlign: "center" }}>Reward Range</th>
            </tr>
          </thead>
          <tbody>
            {data.by_difficulty.map((d) => (
              <tr key={d.difficulty}>
                <td style={{ textAlign: "center", fontWeight: 700, fontSize: "1.1em" }}>
                  {d.difficulty}
                </td>
                <td style={{ textAlign: "center" }}>{d.total}</td>
                <td style={{ textAlign: "center" }}>
                  <span style={{ color: "#2ecc71" }}>{d.wins}</span>
                  {" / "}
                  <span style={{ color: "#e74c3c" }}>{d.losses}</span>
                </td>
                <td>
                  <WinRateBar rate={d.win_rate} />
                </td>
                <td style={{ textAlign: "center", fontFamily: "monospace" }}>
                  {formatDuration(d.avg_duration_secs)}
                </td>
                <td
                  style={{
                    textAlign: "center",
                    fontFamily: "monospace",
                    color: d.avg_reward >= 0 ? "#2ecc71" : "#e74c3c",
                  }}
                >
                  {d.avg_reward}
                </td>
                <td
                  style={{ textAlign: "center", fontFamily: "monospace", color: "#888" }}
                >
                  {d.min_reward} to {d.max_reward}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* Recent games */}
      <section>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "12px" }}>
          <h3 style={{ margin: 0 }}>Recent Games</h3>
          <div style={{ display: "flex", gap: "4px" }}>
            <button
              type="button"
              onClick={() => setDiffFilter("all")}
              className={diffFilter === "all" ? "active" : ""}
              style={{ padding: "3px 10px", fontSize: "0.8em" }}
            >
              All
            </button>
            {data.by_difficulty.map((d) => (
              <button
                key={d.difficulty}
                type="button"
                onClick={() => setDiffFilter(d.difficulty)}
                className={diffFilter === d.difficulty ? "active" : ""}
                style={{ padding: "3px 10px", fontSize: "0.8em" }}
              >
                Diff {d.difficulty}
              </button>
            ))}
          </div>
        </div>
        {filteredGames.length === 0 ? (
          <p style={{ color: "#888" }}>No games match the filter.</p>
        ) : (
          <table style={{ width: "100%", fontSize: "0.85em" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "center" }}>Diff</th>
                <th style={{ textAlign: "center" }}>Result</th>
                <th style={{ textAlign: "center" }}>Duration</th>
                <th style={{ textAlign: "center" }}>Reward</th>
                <th style={{ textAlign: "left" }}>Model</th>
                <th style={{ textAlign: "left" }}>Time</th>
              </tr>
            </thead>
            <tbody>
              {filteredGames.map((g) => (
                <tr key={g.game_id}>
                  <td style={{ textAlign: "center", fontWeight: 600 }}>{g.difficulty}</td>
                  <td style={{ textAlign: "center" }}>
                    <span
                      style={{
                        padding: "2px 8px",
                        borderRadius: "3px",
                        fontWeight: 600,
                        fontSize: "0.85em",
                        backgroundColor:
                          g.result === "win"
                            ? "rgba(46, 204, 113, 0.2)"
                            : "rgba(231, 76, 60, 0.2)",
                        color: g.result === "win" ? "#2ecc71" : "#e74c3c",
                      }}
                    >
                      {g.result}
                    </span>
                  </td>
                  <td style={{ textAlign: "center", fontFamily: "monospace" }}>
                    {formatDuration(g.duration)}
                  </td>
                  <td
                    style={{
                      textAlign: "center",
                      fontFamily: "monospace",
                      color: g.reward >= 0 ? "#2ecc71" : g.reward === 0 ? "#888" : "#e74c3c",
                    }}
                  >
                    {g.reward === 0 ? "\u2014" : g.reward.toFixed(1)}
                  </td>
                  <td style={{ color: "#aaa" }}>{g.model_version}</td>
                  <td style={{ color: "#888", fontSize: "0.9em" }}>
                    {new Date(g.created_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
