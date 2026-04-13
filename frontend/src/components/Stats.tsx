import { useState, useCallback, useEffect, Fragment } from "react";
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

interface StatsResponse {
  total_games: number;
  overall: { wins: number; losses: number; win_rate: number };
  by_difficulty: DifficultyStats[];
  recent_games: never[]; // still returned by API but unused now
  win_trend: { index: number; win_rate: number; timestamp: string }[];
}

interface GameRow {
  game_id: string;
  map_name: string;
  difficulty: number;
  result: string;
  duration: number;
  reward: number;
  model_version: string;
  created_at: string;
}

interface GamesResponse {
  games: GameRow[];
  total: number;
}

interface RewardStep {
  game_time: number;
  total_reward: number;
  fired_rules: { id: string; reward: number }[];
  is_terminal?: boolean;
  result?: string | null;
}

interface GameDetailResponse {
  game: GameRow | null;
  reward_steps: RewardStep[];
}

type DifficultyFilter = "all" | number;

const RESULT_COLORS: Record<string, string> = {
  win: "#2ecc71",
  loss: "#e74c3c",
  timeout: "#f39c12",
};

const PAGE_SIZE = 30;

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

function RewardTimeline({ gameId }: { gameId: string }) {
  const [detail, setDetail] = useState<GameDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(`/api/games/${gameId}`);
        const data = (await resp.json()) as GameDetailResponse;
        if (!cancelled) setDetail(data);
      } catch {
        if (!cancelled) setDetail(null);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [gameId]);

  if (loading) return <tr><td colSpan={7} style={{ color: "#888", padding: "8px 0" }}>Loading reward timeline...</td></tr>;
  if (!detail?.reward_steps?.length) return <tr><td colSpan={7} style={{ color: "#666", padding: "8px 0", fontSize: "0.85em" }}>No per-step reward log available.</td></tr>;

  return (
    <tr>
      <td colSpan={7} style={{ padding: "8px 16px 16px" }}>
        <div style={{ maxHeight: "300px", overflowY: "auto" }}>
          <table style={{ width: "100%", fontSize: "0.8em" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "right" }}>Time</th>
                <th style={{ textAlign: "right" }}>Reward</th>
                <th style={{ textAlign: "left" }}>Fired Rules</th>
              </tr>
            </thead>
            <tbody>
              {detail.reward_steps.map((step, i) => (
                <tr
                  key={i}
                  style={{
                    backgroundColor: step.is_terminal ? "rgba(255,255,255,0.05)" : undefined,
                  }}
                >
                  <td style={{ textAlign: "right", fontFamily: "monospace" }}>
                    {step.game_time.toFixed(1)}s
                  </td>
                  <td
                    style={{
                      textAlign: "right",
                      fontFamily: "monospace",
                      color: step.total_reward >= 0 ? "#2ecc71" : "#e74c3c",
                    }}
                  >
                    {step.total_reward > 0 ? "+" : ""}
                    {step.total_reward.toFixed(2)}
                  </td>
                  <td style={{ color: "#aaa" }}>
                    {step.fired_rules.length > 0
                      ? step.fired_rules
                          .map((r) => `${r.id} (${r.reward > 0 ? "+" : ""}${r.reward})`)
                          .join(", ")
                      : "\u2014"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </td>
    </tr>
  );
}

export function Stats() {
  const { data: statsData, isStale: statsStale, isLoading, lastSuccess } = useApi<StatsResponse>(
    "/api/stats",
    { pollMs: 10000, cacheKey: "/api/stats/v2" },
  );

  // Game history state
  const [filterDifficulty, setFilterDifficulty] = useState<DifficultyFilter>("all");
  const [filterResult, setFilterResult] = useState<string>("");
  const [page, setPage] = useState(0);
  const [expandedGameId, setExpandedGameId] = useState<string | null>(null);

  const gamesParams = new URLSearchParams();
  gamesParams.set("limit", String(PAGE_SIZE));
  gamesParams.set("offset", String(page * PAGE_SIZE));
  if (filterDifficulty !== "all") gamesParams.set("difficulty", String(filterDifficulty));
  if (filterResult) gamesParams.set("result", filterResult);

  const { data: gamesData, isStale: gamesStale, lastSuccess: gamesLastSuccess } = useApi<GamesResponse>(
    `/api/games?${gamesParams.toString()}`,
    { pollMs: 10000, cacheKey: `games-${filterDifficulty}-${filterResult}-${page}` },
  );

  const games = gamesData?.games ?? [];
  const total = gamesData?.total ?? 0;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  const handleFilterChange = useCallback(() => {
    setPage(0);
    setExpandedGameId(null);
  }, []);

  if (!statsData) {
    return (
      <div className="stats training-dashboard">
        <h2>Statistics</h2>
        <p>{isLoading ? "Loading..." : "No game data available."}</p>
      </div>
    );
  }

  return (
    <div className="stats training-dashboard">
      {statsStale ? <StaleDataBanner lastSuccess={lastSuccess} label="Stats" /> : null}
      <h2>Statistics</h2>
      <p style={{ color: "#888", fontSize: "0.85em", margin: "0 0 16px" }}>
        Aggregate results from training.db: overall win/loss record, per-difficulty breakdown,
        and full game history with per-step reward timeline.
      </p>

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
          <span style={{ fontSize: "1.5em", fontWeight: 700 }}>{statsData.total_games}</span>
        </div>
        <div className="stat-card">
          <label>Overall Record</label>
          <span>
            <span style={{ color: "#2ecc71", fontWeight: 600 }}>{statsData.overall.wins}W</span>
            {" / "}
            <span style={{ color: "#e74c3c", fontWeight: 600 }}>{statsData.overall.losses}L</span>
          </span>
        </div>
        <div className="stat-card">
          <label>Win Rate</label>
          <WinRateBar rate={statsData.overall.win_rate} size="large" />
        </div>
        <div className="stat-card">
          <label>Difficulties Played</label>
          <span style={{ fontSize: "1.2em", fontWeight: 600 }}>
            {statsData.by_difficulty.map((d) => d.difficulty).join(", ")}
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
            {statsData.by_difficulty.map((d) => (
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

      {/* Game History (merged from Games tab) */}
      <section>
        {gamesStale && games.length > 0 ? (
          <StaleDataBanner lastSuccess={gamesLastSuccess} label="Games" />
        ) : null}

        <h3>
          Game History{" "}
          <span style={{ fontSize: "0.7em", color: "#888", fontWeight: 400 }}>
            ({total} total)
          </span>
        </h3>

        {/* Filters */}
        <div style={{ display: "flex", gap: "12px", marginBottom: "12px", alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ display: "flex", gap: "4px" }}>
            <button
              type="button"
              onClick={() => { setFilterDifficulty("all"); handleFilterChange(); }}
              className={filterDifficulty === "all" ? "active" : ""}
              style={{ padding: "3px 10px", fontSize: "0.8em" }}
            >
              All
            </button>
            {statsData.by_difficulty.map((d) => (
              <button
                key={d.difficulty}
                type="button"
                onClick={() => { setFilterDifficulty(d.difficulty); handleFilterChange(); }}
                className={filterDifficulty === d.difficulty ? "active" : ""}
                style={{ padding: "3px 10px", fontSize: "0.8em" }}
              >
                Diff {d.difficulty}
              </button>
            ))}
          </div>
          <label style={{ fontSize: "0.85em" }}>
            Result:{" "}
            <select
              value={filterResult}
              onChange={(e) => {
                setFilterResult(e.target.value);
                handleFilterChange();
              }}
              style={{ padding: "4px 8px" }}
            >
              <option value="">All</option>
              <option value="win">Win</option>
              <option value="loss">Loss</option>
              <option value="timeout">Timeout</option>
            </select>
          </label>
        </div>

        {/* Game table */}
        {games.length === 0 ? (
          <p style={{ color: "#888" }}>
            {total === 0 ? "No games recorded yet." : "No games match the current filters."}
          </p>
        ) : (
          <>
            <table style={{ width: "100%", fontSize: "0.85em" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left" }}>Date</th>
                  <th style={{ textAlign: "left" }}>Map</th>
                  <th style={{ textAlign: "center" }}>Diff</th>
                  <th style={{ textAlign: "center" }}>Result</th>
                  <th style={{ textAlign: "right" }}>Duration</th>
                  <th style={{ textAlign: "right" }}>Reward</th>
                  <th style={{ textAlign: "left" }}>Model</th>
                </tr>
              </thead>
              <tbody>
                {games.map((g) => {
                  const isExpanded = expandedGameId === g.game_id;
                  return (
                    <Fragment key={g.game_id}>
                      <tr
                        onClick={() => setExpandedGameId(isExpanded ? null : g.game_id)}
                        style={{
                          cursor: "pointer",
                          backgroundColor: isExpanded ? "rgba(255,255,255,0.05)" : undefined,
                        }}
                        onMouseEnter={(e) =>
                          (e.currentTarget.style.backgroundColor = "rgba(255,255,255,0.03)")
                        }
                        onMouseLeave={(e) =>
                          (e.currentTarget.style.backgroundColor =
                            isExpanded ? "rgba(255,255,255,0.05)" : "")
                        }
                      >
                        <td style={{ color: "#aaa", fontSize: "0.9em" }}>
                          {new Date(g.created_at).toLocaleString()}
                        </td>
                        <td>{g.map_name}</td>
                        <td style={{ textAlign: "center" }}>{g.difficulty}</td>
                        <td style={{ textAlign: "center" }}>
                          <span
                            style={{
                              color: RESULT_COLORS[g.result] ?? "#888",
                              fontWeight: 600,
                              textTransform: "uppercase",
                              fontSize: "0.85em",
                            }}
                          >
                            {g.result}
                          </span>
                        </td>
                        <td style={{ textAlign: "right", fontFamily: "monospace" }}>
                          {formatDuration(g.duration)}
                        </td>
                        <td
                          style={{
                            textAlign: "right",
                            fontFamily: "monospace",
                            color: g.reward >= 0 ? "#2ecc71" : "#e74c3c",
                          }}
                        >
                          {g.reward > 0 ? "+" : ""}
                          {g.reward}
                        </td>
                        <td style={{ color: "#aaa", fontSize: "0.9em" }}>{g.model_version}</td>
                      </tr>
                      {isExpanded && <RewardTimeline gameId={g.game_id} />}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>

            {/* Pagination */}
            {totalPages > 1 && (
              <div
                style={{
                  display: "flex",
                  justifyContent: "center",
                  gap: "8px",
                  marginTop: "12px",
                }}
              >
                <button
                  type="button"
                  disabled={page === 0}
                  onClick={() => setPage((p) => p - 1)}
                  style={{ padding: "4px 12px", fontSize: "0.85em" }}
                >
                  Prev
                </button>
                <span style={{ color: "#888", fontSize: "0.85em", lineHeight: "28px" }}>
                  Page {page + 1} of {totalPages}
                </span>
                <button
                  type="button"
                  disabled={page >= totalPages - 1}
                  onClick={() => setPage((p) => p + 1)}
                  style={{ padding: "4px 12px", fontSize: "0.85em" }}
                >
                  Next
                </button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
