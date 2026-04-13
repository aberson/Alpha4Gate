import { useCallback, useMemo, useState } from "react";
import { useApi } from "../hooks/useApi";
import { StaleDataBanner } from "./StaleDataBanner";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/**
 * Response shape from GET /api/training/reward-trends.
 *
 * Source of truth:
 *   src/alpha4gate/learning/reward_aggregator.py :: aggregate_reward_trends
 *
 * Each rule has a `points` list ordered by the aggregator (most-recent-game
 * first, since the backend sorts files by mtime descending). Each point has
 * a `game_id` (string, from the filename stem), a `timestamp` (ISO UTC from
 * file mtime), and the rule's summed `contribution` for that single game.
 *
 * `total_contribution` is the sum across all returned games for the rule;
 * `contribution_per_game` is total_contribution / (games_with_data_for_rule).
 */
export interface RewardTrendPoint {
  game_id: string;
  timestamp: string;
  contribution: number;
}

export interface RewardTrendRule {
  rule_id: string;
  total_contribution: number;
  contribution_per_game: number;
  points: RewardTrendPoint[];
}

export interface RewardTrendsResponse {
  rules: RewardTrendRule[];
  n_games: number;
  generated_at: string;
}

export type SortColumn =
  | "rule_id"
  | "total_contribution"
  | "contribution_per_game";
export type SortDirection = "asc" | "desc";

const POLL_INTERVAL_MS = 5000;
const DEFAULT_GAMES = 100;
const GAMES_OPTIONS: readonly number[] = [50, 100, 200, 500] as const;

// Distinct-ish palette for rule lines. Cycles if more rules than colors.
const LINE_COLORS: readonly string[] = [
  "#2ecc71",
  "#3498db",
  "#e74c3c",
  "#f1c40f",
  "#9b59b6",
  "#1abc9c",
  "#e67e22",
  "#ecf0f1",
];

function colorForIndex(index: number): string {
  return LINE_COLORS[index % LINE_COLORS.length]!;
}

/**
 * Build the x-axis chart series. Each row is { game: <index>, <rule_id>: number | null }.
 *
 * The backend returns one `points` list per rule, but rules may have different
 * numbers of points (a rule only has a point for games where it fired). We
 * build a row-major table keyed by a shared game index 0..n-1 where n is the
 * max length across all rules' points arrays. For rules with fewer points we
 * write `null` so recharts breaks the line rather than zero-filling.
 *
 * NOTE: game index is NOT a stable identifier across rules (rule A's game 0
 * may be a different game than rule B's game 0 since their points lists are
 * independent). This is an acceptable simplification for a trend view — we
 * only need rough per-rule shapes. Tooltip shows the raw contribution value.
 */
export function buildChartData(
  rules: RewardTrendRule[],
): Array<Record<string, number | string | null>> {
  if (rules.length === 0) return [];
  const maxLen = rules.reduce((max, r) => Math.max(max, r.points.length), 0);
  const rows: Array<Record<string, number | string | null>> = [];
  for (let i = 0; i < maxLen; i += 1) {
    const row: Record<string, number | string | null> = { game: i };
    for (const rule of rules) {
      const point = rule.points[i];
      row[rule.rule_id] =
        point && typeof point.contribution === "number"
          ? point.contribution
          : null;
    }
    rows.push(row);
  }
  return rows;
}

/**
 * Sort helper for the summary table. Pure function so it can be tested
 * without mounting the component.
 */
export function sortRules(
  rules: RewardTrendRule[],
  column: SortColumn,
  direction: SortDirection,
): RewardTrendRule[] {
  const copy = [...rules];
  copy.sort((a, b) => {
    let cmp = 0;
    if (column === "rule_id") {
      cmp = a.rule_id.localeCompare(b.rule_id);
    } else if (column === "total_contribution") {
      cmp = a.total_contribution - b.total_contribution;
    } else {
      cmp = a.contribution_per_game - b.contribution_per_game;
    }
    return direction === "asc" ? cmp : -cmp;
  });
  return copy;
}

interface RewardTrendsProps {
  pollIntervalMs?: number;
  defaultGames?: number;
}

export function RewardTrends({
  pollIntervalMs = POLL_INTERVAL_MS,
  defaultGames = DEFAULT_GAMES,
}: RewardTrendsProps) {
  const [games, setGames] = useState<number>(defaultGames);
  const [sortColumn, setSortColumn] = useState<SortColumn>("total_contribution");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [hiddenRules, setHiddenRules] = useState<Set<string>>(() => new Set());
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [resetStatus, setResetStatus] = useState<string | null>(null);

  const {
    data: rawData,
    isStale,
    isLoading,
    lastSuccess,
  } = useApi<RewardTrendsResponse>(
    `/api/training/reward-trends?games=${games}`,
    { pollMs: pollIntervalMs },
  );

  const data: RewardTrendsResponse | null = rawData
    ? {
        rules: Array.isArray(rawData.rules) ? rawData.rules : [],
        n_games: typeof rawData.n_games === "number" ? rawData.n_games : 0,
        generated_at: typeof rawData.generated_at === "string" ? rawData.generated_at : "",
      }
    : null;

  const sortedRules = useMemo<RewardTrendRule[]>(() => {
    if (!data) return [];
    return sortRules(data.rules, sortColumn, sortDirection);
  }, [data, sortColumn, sortDirection]);

  const chartData = useMemo(() => {
    if (!data) return [];
    return buildChartData(data.rules);
  }, [data]);

  function handleSortClick(column: SortColumn): void {
    if (column === sortColumn) {
      setSortDirection((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSortColumn(column);
      setSortDirection("desc");
    }
  }

  function handleLegendClick(data: { dataKey?: unknown }): void {
    const key = typeof data?.dataKey === "string" ? data.dataKey : null;
    if (!key) return;
    setHiddenRules((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }

  function sortIndicator(column: SortColumn): string {
    if (column !== sortColumn) return "";
    return sortDirection === "asc" ? " \u25B2" : " \u25BC";
  }

  const handleReset = useCallback(async () => {
    setShowResetConfirm(false);
    setResetStatus("Resetting...");
    try {
      const res = await fetch("/api/training/reset", { method: "POST" });
      const body = await res.json();
      setResetStatus(
        `Reset complete: ${(body.results as string[]).join("; ")}`,
      );
      setTimeout(() => setResetStatus(null), 8000);
    } catch (err) {
      setResetStatus(`Reset failed: ${err}`);
      setTimeout(() => setResetStatus(null), 8000);
    }
  }, []);

  if (data === null) {
    return (
      <div className="reward-trends">
        {isLoading ? "Loading..." : "No cached reward trends yet."}
      </div>
    );
  }

  const isEmpty = data.rules.length === 0 || data.n_games === 0;

  return (
    <div className="reward-trends training-dashboard">
      {isStale ? <StaleDataBanner lastSuccess={lastSuccess} label="Reward Trends" /> : null}
      <h2>Reward Trends</h2>
      <p style={{ color: "#888", fontSize: "0.85em", margin: "0 0 16px" }}>
        Per-rule reward contribution over recent games, shown as a summary table (total and per-game averages) and a line chart. Rules with flat or zero contribution may be misfiring or redundant; high-variance rules are worth inspecting for condition correctness.
      </p>

      <div
        className="reward-trends-controls"
        role="group"
        aria-label="Reward trends controls"
        style={{ marginBottom: "12px" }}
      >
        <label htmlFor="reward-trends-games" style={{ marginRight: "8px" }}>
          Games window:
        </label>
        <select
          id="reward-trends-games"
          value={games}
          onChange={(e) => setGames(Number(e.target.value))}
          aria-label="Games window"
        >
          {GAMES_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
        <span style={{ marginLeft: "12px", color: "#888" }}>
          Scanned {data.n_games} game{data.n_games === 1 ? "" : "s"}
        </span>
        <button
          onClick={() => setShowResetConfirm(true)}
          style={{
            marginLeft: "auto",
            padding: "4px 12px",
            background: "#c0392b",
            color: "#fff",
            border: "none",
            borderRadius: "4px",
            cursor: "pointer",
            fontSize: "0.85em",
          }}
        >
          Reset Training Data
        </button>
      </div>
      {showResetConfirm && (
        <div
          style={{
            background: "rgba(192, 57, 43, 0.15)",
            border: "1px solid #c0392b",
            borderRadius: "6px",
            padding: "12px 16px",
            marginBottom: "12px",
            display: "flex",
            alignItems: "center",
            gap: "12px",
          }}
        >
          <span style={{ color: "#e74c3c", fontWeight: "bold" }}>
            This will delete training.db and all reward logs. A timestamped backup will be created. Continue?
          </span>
          <button
            onClick={handleReset}
            style={{
              padding: "4px 16px",
              background: "#c0392b",
              color: "#fff",
              border: "none",
              borderRadius: "4px",
              cursor: "pointer",
              fontWeight: "bold",
            }}
          >
            Yes, Reset
          </button>
          <button
            onClick={() => setShowResetConfirm(false)}
            style={{
              padding: "4px 16px",
              background: "#555",
              color: "#fff",
              border: "none",
              borderRadius: "4px",
              cursor: "pointer",
            }}
          >
            Cancel
          </button>
        </div>
      )}
      {resetStatus && (
        <div style={{ color: "#f1c40f", marginBottom: "12px", fontSize: "0.85em" }}>
          {resetStatus}
        </div>
      )}

      {isEmpty ? (
        <div
          className="reward-trends-empty"
          style={{ color: "#888", padding: "12px 0" }}
        >
          No reward logs yet. Play some games to populate reward trends.
        </div>
      ) : (
        <>
          <div
            className="reward-trends-chart"
            style={{ width: "100%", height: 640, marginBottom: "16px" }}
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                <XAxis
                  dataKey="game"
                  label={{ value: "game index", position: "insideBottom", offset: -4 }}
                />
                <YAxis
                  label={{
                    value: "contribution",
                    angle: -90,
                    position: "insideLeft",
                  }}
                />
                <Tooltip />
                <Legend onClick={handleLegendClick} />
                {data.rules.map((rule, index) => (
                  <Line
                    key={rule.rule_id}
                    type="monotone"
                    dataKey={rule.rule_id}
                    stroke={colorForIndex(index)}
                    dot={false}
                    connectNulls
                    hide={hiddenRules.has(rule.rule_id)}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>

          <table
            className="reward-trends-table"
            style={{
              width: "100%",
              borderCollapse: "collapse",
            }}
          >
            <thead>
              <tr>
                <th
                  onClick={() => handleSortClick("rule_id")}
                  style={{
                    cursor: "pointer",
                    textAlign: "left",
                    borderBottom: "1px solid rgba(255,255,255,0.2)",
                    padding: "6px 8px",
                  }}
                  aria-sort={
                    sortColumn === "rule_id"
                      ? sortDirection === "asc"
                        ? "ascending"
                        : "descending"
                      : "none"
                  }
                >
                  rule_id{sortIndicator("rule_id")}
                </th>
                <th
                  onClick={() => handleSortClick("total_contribution")}
                  style={{
                    cursor: "pointer",
                    textAlign: "right",
                    borderBottom: "1px solid rgba(255,255,255,0.2)",
                    padding: "6px 8px",
                  }}
                  aria-sort={
                    sortColumn === "total_contribution"
                      ? sortDirection === "asc"
                        ? "ascending"
                        : "descending"
                      : "none"
                  }
                >
                  total_contribution{sortIndicator("total_contribution")}
                </th>
                <th
                  onClick={() => handleSortClick("contribution_per_game")}
                  style={{
                    cursor: "pointer",
                    textAlign: "right",
                    borderBottom: "1px solid rgba(255,255,255,0.2)",
                    padding: "6px 8px",
                  }}
                  aria-sort={
                    sortColumn === "contribution_per_game"
                      ? sortDirection === "asc"
                        ? "ascending"
                        : "descending"
                      : "none"
                  }
                >
                  contribution_per_game{sortIndicator("contribution_per_game")}
                </th>
              </tr>
            </thead>
            <tbody>
              {sortedRules.map((rule) => (
                <tr key={rule.rule_id}>
                  <td style={{ padding: "6px 8px" }}>{rule.rule_id}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>
                    {rule.total_contribution.toFixed(2)}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>
                    {rule.contribution_per_game.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

export default RewardTrends;
