import { Fragment, useState, useMemo } from "react";
import { useApi } from "../hooks/useApi";
import { StaleDataBanner } from "./StaleDataBanner";

/**
 * Unified Improvements timeline — Step 3 of the dashboard refactor.
 *
 * Replaces the old split between RecentImprovements (advised) and
 * AdvisedImprovements with a single source-tagged timeline driven by
 * `GET /api/improvements/unified`. Both `data/improvement_log.json`
 * (advised) and `data/evolve_results.jsonl` (evolve) are normalised on
 * the backend into the shared shape below; the frontend just renders.
 *
 * No polling — the data is append-only on the order of minutes-to-hours.
 * Manual refresh button + reload-on-mount mirrors LadderTab + ProcessMonitor.
 *
 * Source filtering is client-side: the unified endpoint already returns
 * both sources in a single response, the dataset is small (default 50
 * entries, max 500), and refetching on every pill-click would burn the
 * cache for no benefit.
 */

export type ImprovementSource = "advised" | "evolve";

export interface UnifiedImprovement {
  id: string;
  source: ImprovementSource;
  timestamp: string | null;
  title: string;
  description: string;
  type: "training" | "dev" | string;
  outcome: string;
  metric: string | null;
  principles: string[];
  files_changed: string[];
}

export interface ImprovementsResponse {
  improvements: UnifiedImprovement[];
}

type Filter = "all" | "advised" | "evolve";

const SUCCESS_OUTCOMES = new Set([
  "promoted",
  "regression-pass",
  "fitness-pass",
  "stack-apply-pass",
]);
const FAILURE_OUTCOMES = new Set([
  "discarded",
  "regression-rollback",
  "fitness-fail",
  "crash",
]);
const WARNING_OUTCOMES = new Set([
  "stack-apply-commit-fail",
  "stack-apply-import-fail",
]);

function classifyOutcome(
  outcome: string,
): "success" | "failure" | "warning" | "neutral" {
  if (SUCCESS_OUTCOMES.has(outcome)) return "success";
  if (FAILURE_OUTCOMES.has(outcome)) return "failure";
  if (WARNING_OUTCOMES.has(outcome)) return "warning";
  return "neutral";
}

function formatRelativeTime(then: Date, now: Date = new Date()): string {
  const deltaSec = Math.round((now.getTime() - then.getTime()) / 1000);
  if (deltaSec < 0) return "just now";
  if (deltaSec < 60) return `${deltaSec}s ago`;
  if (deltaSec < 3600) {
    const m = Math.floor(deltaSec / 60);
    return `${m} min ago`;
  }
  if (deltaSec < 86400) {
    const h = Math.floor(deltaSec / 3600);
    return `${h} hr ago`;
  }
  const days = Math.floor(deltaSec / 86400);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function truncateList(
  items: string[],
  limit = 2,
): { shown: string[]; extra: number } {
  if (items.length <= limit) return { shown: items, extra: 0 };
  return { shown: items.slice(0, limit), extra: items.length - limit };
}

function TruncatedList({ items }: { items: string[] }) {
  if (items.length === 0) {
    return <span style={{ color: "#888" }}>—</span>;
  }
  const { shown, extra } = truncateList(items);
  return (
    <span>
      {shown.join(", ")}
      {extra > 0 ? (
        <span style={{ color: "#888" }}>{` +${extra} more`}</span>
      ) : null}
    </span>
  );
}

function SourceBadge({ source }: { source: ImprovementSource }) {
  return (
    <span
      className={`improvements-source-badge ${source}`}
      data-testid={`source-badge-${source}`}
    >
      {source}
    </span>
  );
}

function OutcomeBadge({ outcome }: { outcome: string }) {
  const tone = classifyOutcome(outcome);
  return (
    <span
      className={`improvements-outcome-badge ${tone}`}
      data-testid={`outcome-badge-${outcome}`}
    >
      {outcome}
    </span>
  );
}

function TimestampCell({ value }: { value: string | null }) {
  if (!value) return <span style={{ color: "#888" }}>—</span>;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return <span title={value}>{value}</span>;
  }
  return (
    <span title={parsed.toLocaleString()}>{formatRelativeTime(parsed)}</span>
  );
}

export function ImprovementsTab() {
  const { data, isStale, isLoading, lastSuccess, refresh } =
    useApi<ImprovementsResponse>("/api/improvements/unified");

  const [filter, setFilter] = useState<Filter>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const allEntries = useMemo<UnifiedImprovement[]>(
    () => data?.improvements ?? [],
    [data],
  );

  const visibleEntries = useMemo<UnifiedImprovement[]>(() => {
    if (filter === "all") return allEntries;
    return allEntries.filter((e) => e.source === filter);
  }, [allEntries, filter]);

  const totalCount = allEntries.length;
  const visibleCount = visibleEntries.length;
  const isFiltered = filter !== "all";

  const countLabel = isFiltered
    ? `${visibleCount} of ${totalCount} (filtered)`
    : `${totalCount} improvement${totalCount === 1 ? "" : "s"}`;

  if (isLoading && !data) {
    return (
      <div className="improvements-tab">
        <p>Loading improvements…</p>
      </div>
    );
  }

  return (
    <div className="improvements-tab">
      {isStale ? (
        <StaleDataBanner lastSuccess={lastSuccess} label="Improvements" />
      ) : null}

      <div className="improvements-header">
        <h2 style={{ margin: 0 }}>Improvements</h2>
        <div className="improvements-filter-row">
          {(["all", "advised", "evolve"] as Filter[]).map((value) => {
            const label =
              value === "all"
                ? "All"
                : value === "advised"
                  ? "Advised"
                  : "Evolve";
            const isActive = filter === value;
            return (
              <button
                key={value}
                type="button"
                onClick={() => setFilter(value)}
                className={
                  "improvements-filter-pill" +
                  (isActive ? " improvements-filter-pill-active" : "")
                }
                aria-pressed={isActive}
                data-testid={`filter-pill-${value}`}
              >
                {label}
              </button>
            );
          })}
        </div>
        <div className="improvements-count" data-testid="improvements-count">
          {countLabel}
        </div>
        <button
          type="button"
          onClick={() => refresh()}
          className="improvements-refresh"
          data-testid="improvements-refresh"
        >
          Refresh
        </button>
      </div>

      <p style={{ color: "#888", fontSize: "0.85em", margin: "8px 0 16px" }}>
        Unified timeline of <code>/improve-bot-advised</code> and{" "}
        <code>/improve-bot-evolve</code> outcomes. Click any row for the full
        description, principle list, and files-changed manifest.
      </p>

      {totalCount === 0 ? (
        <p
          className="improvements-empty"
          data-testid="improvements-empty"
          style={{ color: "#888", fontStyle: "italic" }}
        >
          No improvements yet — run /improve-bot-advised or /improve-bot-evolve.
        </p>
      ) : (
        <table className="improvements-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Source</th>
              <th>Title</th>
              <th>Outcome</th>
              <th>Metric</th>
              <th>Principles</th>
              <th>Files</th>
            </tr>
          </thead>
          <tbody>
            {visibleEntries.map((entry) => {
              const isExpanded = expandedId === entry.id;
              return (
                <Fragment key={entry.id}>
                  <tr
                    className={
                      "improvements-row" +
                      (isExpanded ? " improvements-row-active" : "")
                    }
                    onClick={() =>
                      setExpandedId(isExpanded ? null : entry.id)
                    }
                    data-testid={`improvements-row-${entry.id}`}
                  >
                    <td>
                      <TimestampCell value={entry.timestamp} />
                    </td>
                    <td>
                      <SourceBadge source={entry.source} />
                    </td>
                    <td>{entry.title}</td>
                    <td>
                      <OutcomeBadge outcome={entry.outcome} />
                    </td>
                    <td>
                      {entry.metric ?? (
                        <span style={{ color: "#888" }}>—</span>
                      )}
                    </td>
                    <td>
                      <TruncatedList items={entry.principles} />
                    </td>
                    <td>
                      <TruncatedList items={entry.files_changed} />
                    </td>
                  </tr>
                  {isExpanded ? (
                    <tr
                      className="improvements-row-expanded"
                      data-testid={`improvements-row-expanded-${entry.id}`}
                    >
                      <td colSpan={7}>
                        <div className="improvements-expanded-section">
                          <strong>Description</strong>
                          <p style={{ margin: "4px 0 0", whiteSpace: "pre-wrap" }}>
                            {entry.description || "(no description)"}
                          </p>
                        </div>
                        <div className="improvements-expanded-section">
                          <strong>Principles</strong>
                          {entry.principles.length === 0 ? (
                            <span
                              style={{ color: "#888", marginLeft: "8px" }}
                            >
                              (none)
                            </span>
                          ) : (
                            <ul>
                              {entry.principles.map((p, i) => (
                                <li key={`${entry.id}-p-${i}`}>{p}</li>
                              ))}
                            </ul>
                          )}
                        </div>
                        <div className="improvements-expanded-section">
                          <strong>Files changed</strong>
                          {entry.files_changed.length === 0 ? (
                            <span
                              style={{ color: "#888", marginLeft: "8px" }}
                            >
                              (none)
                            </span>
                          ) : (
                            <ul>
                              {entry.files_changed.map((f, i) => (
                                <li key={`${entry.id}-f-${i}`}>
                                  <code>{f}</code>
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default ImprovementsTab;
