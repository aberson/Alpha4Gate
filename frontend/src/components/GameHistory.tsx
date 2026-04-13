import { useState, useCallback } from "react";
import { useApi } from "../hooks/useApi";
import { StaleDataBanner } from "./StaleDataBanner";

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

const RESULT_COLORS: Record<string, string> = {
  win: "#2ecc71",
  loss: "#e74c3c",
  timeout: "#f39c12",
};

const PAGE_SIZE = 30;

export function GameHistory() {
  const [filterDifficulty, setFilterDifficulty] = useState<string>("");
  const [filterResult, setFilterResult] = useState<string>("");
  const [page, setPage] = useState(0);
  const [selectedGame, setSelectedGame] = useState<GameDetailResponse | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const params = new URLSearchParams();
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(page * PAGE_SIZE));
  if (filterDifficulty) params.set("difficulty", filterDifficulty);
  if (filterResult) params.set("result", filterResult);

  const { data, isStale, lastSuccess } = useApi<GamesResponse>(
    `/api/games?${params.toString()}`,
    { pollMs: 10000, cacheKey: `games-${filterDifficulty}-${filterResult}-${page}` },
  );

  const games = data?.games ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  const viewGame = useCallback(async (gameId: string) => {
    setLoadingDetail(true);
    try {
      const resp = await fetch(`/api/games/${gameId}`);
      const detail = (await resp.json()) as GameDetailResponse;
      setSelectedGame(detail);
    } catch {
      setSelectedGame(null);
    }
    setLoadingDetail(false);
  }, []);

  const handleFilterChange = useCallback(() => {
    setPage(0);
    setSelectedGame(null);
  }, []);

  return (
    <div className="game-history training-dashboard">
      {isStale && games.length > 0 ? (
        <StaleDataBanner lastSuccess={lastSuccess} label="Games" />
      ) : null}

      <h2>
        Game History{" "}
        <span style={{ fontSize: "0.7em", color: "#888", fontWeight: 400 }}>
          ({total} total)
        </span>
      </h2>
      <p style={{ color: "#888", fontSize: "0.85em", margin: "0 0 16px" }}>
        Browse all completed games from the training database. Filter by difficulty or
        result, and click a game to see its per-step reward breakdown.
      </p>

      {/* Filters */}
      <div style={{ display: "flex", gap: "16px", marginBottom: "16px", alignItems: "center" }}>
        <label style={{ fontSize: "0.85em" }}>
          Difficulty:{" "}
          <select
            value={filterDifficulty}
            onChange={(e) => {
              setFilterDifficulty(e.target.value);
              handleFilterChange();
            }}
            style={{ padding: "4px 8px" }}
          >
            <option value="">All</option>
            {Array.from({ length: 10 }, (_, i) => (
              <option key={i + 1} value={String(i + 1)}>
                {i + 1}
              </option>
            ))}
          </select>
        </label>
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
              {games.map((g) => (
                <tr
                  key={g.game_id}
                  onClick={() => void viewGame(g.game_id)}
                  style={{
                    cursor: "pointer",
                    backgroundColor:
                      selectedGame?.game?.game_id === g.game_id
                        ? "rgba(255,255,255,0.05)"
                        : undefined,
                  }}
                  onMouseEnter={(e) =>
                    (e.currentTarget.style.backgroundColor = "rgba(255,255,255,0.03)")
                  }
                  onMouseLeave={(e) =>
                    (e.currentTarget.style.backgroundColor =
                      selectedGame?.game?.game_id === g.game_id
                        ? "rgba(255,255,255,0.05)"
                        : "")
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
                    {Math.floor(g.duration / 60)}:{String(g.duration % 60).padStart(2, "0")}
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
              ))}
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

      {/* Game detail panel */}
      {loadingDetail && <p style={{ color: "#888", marginTop: "16px" }}>Loading...</p>}
      {selectedGame?.game && !loadingDetail && (
        <section
          style={{
            marginTop: "24px",
            padding: "16px",
            borderRadius: "6px",
            backgroundColor: "rgba(255,255,255,0.03)",
            border: "1px solid rgba(255,255,255,0.08)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>
              Game Detail{" "}
              <span
                style={{
                  color: RESULT_COLORS[selectedGame.game.result] ?? "#888",
                  textTransform: "uppercase",
                }}
              >
                {selectedGame.game.result}
              </span>
            </h3>
            <button
              type="button"
              onClick={() => setSelectedGame(null)}
              style={{ padding: "2px 8px", fontSize: "0.8em" }}
            >
              Close
            </button>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
              gap: "12px",
              marginTop: "12px",
            }}
          >
            <div className="stat-card">
              <label>Map</label>
              <span>{selectedGame.game.map_name}</span>
            </div>
            <div className="stat-card">
              <label>Difficulty</label>
              <span>{selectedGame.game.difficulty}</span>
            </div>
            <div className="stat-card">
              <label>Duration</label>
              <span>
                {Math.floor(selectedGame.game.duration / 60)}:
                {String(selectedGame.game.duration % 60).padStart(2, "0")}
              </span>
            </div>
            <div className="stat-card">
              <label>Reward</label>
              <span
                style={{
                  color: selectedGame.game.reward >= 0 ? "#2ecc71" : "#e74c3c",
                }}
              >
                {selectedGame.game.reward}
              </span>
            </div>
            <div className="stat-card">
              <label>Model</label>
              <span>{selectedGame.game.model_version}</span>
            </div>
            <div className="stat-card">
              <label>Game ID</label>
              <span style={{ fontSize: "0.75em", wordBreak: "break-all" }}>
                {selectedGame.game.game_id}
              </span>
            </div>
          </div>

          {/* Per-step reward breakdown */}
          {selectedGame.reward_steps.length > 0 ? (
            <div style={{ marginTop: "16px" }}>
              <h4>Reward Timeline ({selectedGame.reward_steps.length} steps)</h4>
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
                    {selectedGame.reward_steps.map((step, i) => (
                      <tr
                        key={i}
                        style={{
                          backgroundColor: step.is_terminal
                            ? "rgba(255,255,255,0.05)"
                            : undefined,
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
            </div>
          ) : (
            <p style={{ color: "#666", fontSize: "0.85em", marginTop: "12px" }}>
              No per-step reward log available for this game.
            </p>
          )}
        </section>
      )}
    </div>
  );
}
