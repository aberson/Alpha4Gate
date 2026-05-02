import { Fragment, useMemo } from "react";

/**
 * Reusable improvements timeline list — Step 6 of the Models-tab build
 * plan. Extracted verbatim from ``LineageView.tsx``'s ``TimelineMode``
 * so the same row layout, badge styling, expanded-section ordering, and
 * filter pill UX can be reused by the per-version Inspector panel
 * (Step 6 §"Improvements applied" sub-panel) without duplicating code.
 *
 * Two source modes:
 *
 *   - **Whole-feed mode** (``LineageView`` timeline tab): pass an
 *     ``entries`` array straight from the unified-improvements feed.
 *     Filter pills toggle between ``all / advised / evolve``.
 *   - **Filtered mode** (Inspector "Improvements applied" sub-panel):
 *     pass the per-version filtered list. Filter pills CAN be hidden
 *     by setting ``showFilter={false}`` since the entries are already
 *     filtered upstream.
 *
 * State (filter + expanded id) is fully lifted to the parent so a tree
 * → timeline → tree mode toggle in ``LineageView`` doesn't reset the
 * pill — see iter-2 review §5 ("Option A: lift state, don't double-
 * mount"). The inspector sub-panel uses local ``useState`` instead.
 *
 * No data fetching here — callers pass ``entries`` directly. This is
 * deliberate: the LineageView timeline mode hits
 * ``/api/improvements/unified``, the Inspector hits
 * ``/api/versions/{v}/improvements`` (different endpoint, same row
 * shape), so the shared list component stays endpoint-agnostic.
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

export type TimelineFilter = "all" | "advised" | "evolve";

export interface TimelineListProps {
  /** Entries to render. Already loaded by the parent. */
  entries: UnifiedImprovement[];
  /** Filter state — ``"all"`` shows every entry; ``"advised"`` /
   * ``"evolve"`` filter by source. Lifted to parent so toggling the
   * surrounding mode doesn't reset the pill. */
  filter: TimelineFilter;
  setFilter: (next: TimelineFilter) => void;
  /** Currently-expanded row id, or ``null`` when nothing is expanded.
   * Lifted to parent for the same reason as ``filter``. */
  expandedId: string | null;
  setExpandedId: (next: string | null) => void;
  /** Optional refresh callback wired to a ``Refresh`` button in the
   * header strip. Omitted callers (e.g. the per-version Inspector)
   * suppress the button entirely. */
  onRefresh?: () => void;
  /** Optional title above the table. Defaults to ``"Improvements"``. */
  title?: string;
  /** Optional caption shown beneath the header strip. Pass JSX so the
   * caller can include ``<code>`` tags for skill names etc. */
  caption?: React.ReactNode;
  /** Show the all/advised/evolve filter pill row. Defaults to ``true``;
   * the per-version Inspector hides it (entries are already filtered
   * upstream by the backend's ``/api/versions/{v}/improvements``
   * endpoint). */
  showFilter?: boolean;
  /** Empty-state message when ``entries.length === 0``. Defaults to a
   * generic "No improvements yet" line. */
  emptyMessage?: string;
  /** Optional ``data-testid`` override for the outer container so two
   * different timeline lists on the same page can be selected
   * independently in tests (e.g. inspector + lineage timeline). */
  testIdPrefix?: string;
}

/**
 * Render an improvements timeline as a sortable table with click-to-
 * expand rows, filter pills, and badge cells.
 *
 * No fetching, no caching. Pure-presentation component — the parent
 * owns data and filter state.
 */
export function TimelineList({
  entries,
  filter,
  setFilter,
  expandedId,
  setExpandedId,
  onRefresh,
  title,
  caption,
  showFilter = true,
  emptyMessage,
  testIdPrefix = "improvements",
}: TimelineListProps) {
  const allEntries = entries;
  const visibleEntries = useMemo<UnifiedImprovement[]>(() => {
    if (!showFilter || filter === "all") return allEntries;
    return allEntries.filter((e) => e.source === filter);
  }, [allEntries, filter, showFilter]);

  const totalCount = allEntries.length;
  const visibleCount = visibleEntries.length;
  const isFiltered = showFilter && filter !== "all";
  const countLabel = isFiltered
    ? `${visibleCount} of ${totalCount} (filtered)`
    : `${totalCount} improvement${totalCount === 1 ? "" : "s"}`;

  const finalEmptyMessage =
    emptyMessage ??
    "No improvements yet — run /improve-bot-advised or /improve-bot-evolve.";
  const finalTitle = title ?? "Improvements";

  return (
    <div className="improvements-tab" data-testid={testIdPrefix}>
      <div className="improvements-header">
        <h3 style={{ margin: 0 }}>{finalTitle}</h3>
        {showFilter ? (
          <div className="improvements-filter-row">
            {(["all", "advised", "evolve"] as TimelineFilter[]).map((value) => {
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
        ) : null}
        <div className="improvements-count" data-testid="improvements-count">
          {countLabel}
        </div>
        {onRefresh ? (
          <button
            type="button"
            onClick={() => onRefresh()}
            className="improvements-refresh"
            data-testid="improvements-refresh"
          >
            Refresh
          </button>
        ) : null}
      </div>

      {caption ? (
        <p style={{ color: "#888", fontSize: "0.85em", margin: "8px 0 16px" }}>
          {caption}
        </p>
      ) : null}

      {totalCount === 0 ? (
        <p
          className="improvements-empty"
          data-testid="improvements-empty"
          style={{ color: "#888", fontStyle: "italic" }}
        >
          {finalEmptyMessage}
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
                            <span style={{ color: "#888", marginLeft: "8px" }}>
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
                            <span style={{ color: "#888", marginLeft: "8px" }}>
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

export default TimelineList;
