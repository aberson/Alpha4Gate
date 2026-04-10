import { useEffect, useMemo, useState } from "react";

/**
 * Response shape from GET /api/training/promotions/history.
 *
 * Source of truth: src/alpha4gate/learning/promotion.py
 *   PromotionLogger._decision_to_dict produces these exact keys.
 * API wrapper: src/alpha4gate/api.py `get_promotion_history` returns
 *   {"history": logger.get_history()}.
 *
 * Rollbacks are written by RollbackMonitor (src/alpha4gate/learning/rollback.py)
 * using the same schema, with promoted=false and reason prefixed with
 * "rollback:". Failed promotions use promoted=false without that prefix.
 */
export interface PromotionHistoryEntry {
  timestamp: string;
  new_checkpoint: string;
  old_best: string | null;
  new_win_rate: number | null;
  old_win_rate: number | null;
  delta: number | null;
  eval_games_played: number;
  promoted: boolean;
  reason: string;
  difficulty: number;
  action_distribution_shift: number | null;
}

interface PromotionHistoryResponse {
  history: PromotionHistoryEntry[];
}

export type ImprovementKind = "promotion" | "rollback" | "rejected";
export type ImprovementFilter = "all" | "promotions" | "rollbacks";

const POLL_INTERVAL_MS = 5000;
const DEFAULT_LIMIT = 20;

/**
 * Classify a history entry into one of three categories.
 *
 * - "rollback"   : promoted=false AND reason starts with "rollback:"
 *                  (written by RollbackMonitor._execute_rollback)
 * - "promotion"  : promoted=true (PromotionManager approved the change)
 * - "rejected"   : promoted=false AND reason does NOT start with "rollback:"
 *                  (PromotionManager evaluated and declined to promote)
 */
export function classifyEntry(entry: PromotionHistoryEntry): ImprovementKind {
  if (entry.promoted) return "promotion";
  if (typeof entry.reason === "string" && entry.reason.startsWith("rollback:")) {
    return "rollback";
  }
  return "rejected";
}

/**
 * Filter policy: "Promotions" shows successful promotions only. Rejected
 * attempts (promoted=false without rollback: prefix) are ONLY shown in the
 * "All" view -- they are neither rollbacks nor successful promotions, so
 * they would be misleading under either specific filter.
 */
function matchesFilter(kind: ImprovementKind, filter: ImprovementFilter): boolean {
  if (filter === "all") return true;
  if (filter === "promotions") return kind === "promotion";
  if (filter === "rollbacks") return kind === "rollback";
  return false;
}

function formatTimestamp(value: string): string {
  if (!value) return "\u2014";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatWinRate(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "\u2014";
  }
  return `${(value * 100).toFixed(1)}%`;
}

interface DeltaDisplay {
  text: string;
  color: string;
  arrow: string;
}

/**
 * Compute the delta display. Prefers the backend-provided `delta` field,
 * falls back to computing (new - old) if delta is null but both win rates
 * are present. Returns a neutral em-dash when delta is not computable.
 */
export function computeDeltaDisplay(entry: PromotionHistoryEntry): DeltaDisplay {
  let delta: number | null = null;
  if (typeof entry.delta === "number" && Number.isFinite(entry.delta)) {
    delta = entry.delta;
  } else if (
    typeof entry.new_win_rate === "number" &&
    typeof entry.old_win_rate === "number" &&
    Number.isFinite(entry.new_win_rate) &&
    Number.isFinite(entry.old_win_rate)
  ) {
    delta = entry.new_win_rate - entry.old_win_rate;
  }

  if (delta === null) {
    return { text: "\u2014", color: "#888", arrow: "" };
  }
  if (delta > 0) {
    return {
      text: `+${(delta * 100).toFixed(1)}%`,
      color: "#2ecc71",
      arrow: "\u2191",
    };
  }
  if (delta < 0) {
    return {
      text: `${(delta * 100).toFixed(1)}%`,
      color: "#e74c3c",
      arrow: "\u2193",
    };
  }
  return { text: "0.0%", color: "#888", arrow: "" };
}

interface ActionBadgeProps {
  kind: ImprovementKind;
}

function ActionBadge({ kind }: ActionBadgeProps) {
  let color = "#888";
  let label = "rejected";
  if (kind === "promotion") {
    color = "#2ecc71";
    label = "promote";
  } else if (kind === "rollback") {
    color = "#e74c3c";
    label = "rollback";
  }
  return (
    <span
      className={`improvement-badge improvement-${kind}`}
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: "4px",
        backgroundColor: color,
        color: "#fff",
        fontWeight: 600,
        fontSize: "0.8em",
        textTransform: "uppercase",
      }}
    >
      {label}
    </span>
  );
}

interface RecentImprovementsProps {
  limit?: number;
  pollIntervalMs?: number;
}

export function RecentImprovements({
  limit = DEFAULT_LIMIT,
  pollIntervalMs = POLL_INTERVAL_MS,
}: RecentImprovementsProps) {
  const [history, setHistory] = useState<PromotionHistoryEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<ImprovementFilter>("all");

  useEffect(() => {
    let cancelled = false;

    async function load(): Promise<void> {
      try {
        const response = await fetch("/api/training/promotions/history");
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const data = (await response.json()) as PromotionHistoryResponse;
        if (cancelled) return;
        const items = Array.isArray(data?.history) ? data.history : [];
        setHistory(items);
        setError(null);
      } catch (ex) {
        if (cancelled) return;
        setError(ex instanceof Error ? ex.message : "failed to load history");
      }
    }

    void load();
    const handle = setInterval(() => {
      void load();
    }, pollIntervalMs);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [pollIntervalMs]);

  const sortedLatest = useMemo<PromotionHistoryEntry[]>(() => {
    if (!history) return [];
    // Sort newest first by timestamp, fall back to original order when equal.
    const copy = [...history];
    copy.sort((a, b) => {
      const ta = new Date(a.timestamp).getTime();
      const tb = new Date(b.timestamp).getTime();
      if (Number.isNaN(ta) || Number.isNaN(tb)) return 0;
      return tb - ta;
    });
    return copy.slice(0, limit);
  }, [history, limit]);

  const filtered = useMemo<PromotionHistoryEntry[]>(() => {
    return sortedLatest.filter((entry) => matchesFilter(classifyEntry(entry), filter));
  }, [sortedLatest, filter]);

  if (error && history === null) {
    return (
      <div className="recent-improvements error" style={{ color: "#e74c3c" }}>
        Error: {error}
      </div>
    );
  }

  if (history === null) {
    return <div className="recent-improvements">Loading...</div>;
  }

  return (
    <div className="recent-improvements training-dashboard">
      <h2>Recent Improvements</h2>

      <div className="improvement-filters" role="group" aria-label="Filter improvements">
        <button
          type="button"
          onClick={() => setFilter("all")}
          className={filter === "all" ? "active" : ""}
          aria-pressed={filter === "all"}
        >
          All
        </button>
        <button
          type="button"
          onClick={() => setFilter("promotions")}
          className={filter === "promotions" ? "active" : ""}
          aria-pressed={filter === "promotions"}
        >
          Promotions
        </button>
        <button
          type="button"
          onClick={() => setFilter("rollbacks")}
          className={filter === "rollbacks" ? "active" : ""}
          aria-pressed={filter === "rollbacks"}
        >
          Rollbacks
        </button>
      </div>

      {error ? (
        <div
          className="control-error"
          role="alert"
          style={{ color: "#e74c3c", marginBottom: "8px" }}
        >
          {error}
        </div>
      ) : null}

      {sortedLatest.length === 0 ? (
        <div className="improvement-empty" style={{ color: "#888", padding: "12px 0" }}>
          No promotion or rollback events yet.
        </div>
      ) : filtered.length === 0 ? (
        <div className="improvement-empty" style={{ color: "#888", padding: "12px 0" }}>
          No events match the current filter.
        </div>
      ) : (
        <ul
          className="improvement-list"
          style={{ listStyle: "none", padding: 0, margin: 0 }}
        >
          {filtered.map((entry, index) => {
            const kind = classifyEntry(entry);
            const delta = computeDeltaDisplay(entry);
            return (
              <li
                key={`${entry.timestamp}-${entry.new_checkpoint}-${index}`}
                className={`improvement-entry improvement-entry-${kind}`}
                style={{
                  borderBottom: "1px solid rgba(255,255,255,0.08)",
                  padding: "10px 0",
                  display: "grid",
                  gridTemplateColumns: "auto auto 1fr auto",
                  columnGap: "12px",
                  rowGap: "4px",
                  alignItems: "baseline",
                }}
              >
                <span className="improvement-timestamp" style={{ color: "#888", fontSize: "0.85em" }}>
                  {formatTimestamp(entry.timestamp)}
                </span>
                <ActionBadge kind={kind} />
                <span className="improvement-checkpoint">
                  <strong>{entry.new_checkpoint}</strong>
                  <span style={{ color: "#888" }}> vs {entry.old_best ?? "\u2014"}</span>
                </span>
                <span
                  className="improvement-delta"
                  style={{ color: delta.color, fontWeight: 600 }}
                  aria-label={`win rate delta ${delta.text}`}
                >
                  {delta.arrow} {delta.text}
                </span>

                <span
                  className="improvement-winrate"
                  style={{ gridColumn: "1 / -1", color: "#aaa", fontSize: "0.85em" }}
                >
                  win rate: {formatWinRate(entry.new_win_rate)} (prior best:{" "}
                  {formatWinRate(entry.old_win_rate)})
                </span>
                <span
                  className="improvement-reason"
                  style={{ gridColumn: "1 / -1", color: "#bbb", fontSize: "0.85em" }}
                >
                  {entry.reason}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default RecentImprovements;
