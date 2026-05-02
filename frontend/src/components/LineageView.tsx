import { Fragment, useCallback, useMemo, useState } from "react";
import { hierarchy, tree as d3tree } from "d3-hierarchy";
import type { HierarchyPointNode } from "d3-hierarchy";
import { useApi } from "../hooks/useApi";
import { useLineage } from "../hooks/useLineage";
import { StaleDataBanner } from "./StaleDataBanner";
import type {
  LineageDAG,
  LineageEdge,
  LineageNode,
} from "../types/lineage";

/**
 * Lineage view — Step 4 of the Models-tab build plan.
 *
 * Two modes share the lineage shell:
 *   - **Tree** (default): family-tree visualization rendered to native
 *     SVG via ``d3-hierarchy``'s cluster/tree layout. Nodes are coloured
 *     by ``harness_origin`` (advised=blue, evolve=green, manual=grey,
 *     self-play=purple) and edges are labelled with the
 *     ``improvement_title`` from ``data/lineage.json``.
 *   - **Timeline**: subsumes the legacy Improvements tab — table of
 *     unified advised + evolve improvements with source badges,
 *     outcome chips, and click-to-expand description / principles /
 *     files-changed sections. Uses ``GET /api/improvements/unified``.
 *
 * The mode toggle is local React state (not URL state — that's
 * Step 7's compare-view concern).
 *
 * Tree-node click → ``onNodeSelect(version)`` callback fires; the
 * parent (``ModelsTab``) wires this so the selected version snaps and
 * the sub-view switches to the Inspector.
 *
 * Keyboard accessibility: each node is a focusable ``<g>`` with
 * ``tabIndex={0}``; Enter/Space dispatches the same callback as the
 * click handler.
 */

// --- Unified-improvements types (verbatim port from the legacy
// ImprovementsTab so the timeline-mode shape stays stable) ---------------

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

// --- Tree-mode rendering ------------------------------------------------

const HARNESS_FILL: Record<LineageNode["harness_origin"], string> = {
  advised: "#3182ce", // blue
  evolve: "#38a169", // green
  manual: "#9aa0a6", // grey
  "self-play": "#805ad5", // purple
};

const NODE_RADIUS = 18;
const HORIZONTAL_GAP = 90;
const VERTICAL_GAP = 60;

interface PositionedNode {
  id: string;
  data: LineageNode;
  x: number;
  y: number;
}

interface PositionedEdge {
  from: PositionedNode;
  to: PositionedNode;
  edge: LineageEdge | null;
}

interface LayoutResult {
  nodes: PositionedNode[];
  edges: PositionedEdge[];
  width: number;
  height: number;
}

/**
 * Run the d3-hierarchy tree layout against a lineage DAG.
 *
 * Exported so tests can mock the layout to produce stable coordinates
 * without depending on jsdom's layout edge cases. The component calls
 * ``computeTreeLayout`` exactly once per (lineage, dimensions) pair.
 *
 * Synthesises a virtual root for forests (multiple parent-less nodes)
 * so d3-hierarchy still produces a single tree. Edges from the
 * synthetic root are filtered out before rendering.
 */
export function computeTreeLayout(dag: LineageDAG): LayoutResult {
  // Defensive: stale IDB cache (or a pre-Step-2 backend) may return a
  // payload missing one of the DAG keys. Treat absence as an empty
  // list so the layout never throws on first render.
  const nodesIn = Array.isArray(dag.nodes) ? dag.nodes : [];
  const edgesIn = Array.isArray(dag.edges) ? dag.edges : [];
  if (nodesIn.length === 0) {
    return { nodes: [], edges: [], width: 0, height: 0 };
  }

  const nodeById = new Map<string, LineageNode>();
  for (const n of nodesIn) nodeById.set(n.id, n);

  // Identify roots (parents that don't exist in the node set, OR
  // explicit null parents). Synthesise a virtual root linking all of
  // them so d3-hierarchy can produce a single tree.
  const childrenByParent = new Map<string, LineageNode[]>();
  const VIRTUAL_ROOT = "__lineage_virtual_root__";
  for (const node of nodesIn) {
    const parent =
      node.parent && nodeById.has(node.parent) ? node.parent : VIRTUAL_ROOT;
    const list = childrenByParent.get(parent);
    if (list) {
      list.push(node);
    } else {
      childrenByParent.set(parent, [node]);
    }
  }

  interface Synthetic {
    id: string;
    real: LineageNode | null;
  }
  const root: Synthetic = { id: VIRTUAL_ROOT, real: null };

  const h = hierarchy<Synthetic>(root, (n) => {
    const kids = childrenByParent.get(n.id) ?? [];
    return kids.map((k) => ({ id: k.id, real: k }));
  });

  const layout = d3tree<Synthetic>().nodeSize([HORIZONTAL_GAP, VERTICAL_GAP]);
  const positioned = layout(h);
  const allNodes = positioned.descendants();

  // Filter out the virtual root from output.
  const real = allNodes.filter((d) => d.data.real !== null);

  if (real.length === 0) {
    return { nodes: [], edges: [], width: 0, height: 0 };
  }

  // d3-hierarchy uses ``x`` for horizontal (sibling) axis and ``y`` for
  // depth. Normalise so the tree is rooted at the top-left with a
  // small margin and the deepest node sets the height.
  const xs = real.map((d) => d.x);
  const ys = real.map((d) => d.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const margin = NODE_RADIUS + 24;

  const positionedNodes: PositionedNode[] = real.map((d) => ({
    id: d.data.id,
    // ``d.data.real`` is non-null after the filter above; cast.
    data: d.data.real as LineageNode,
    x: d.x - minX + margin,
    y: d.y - minY + margin,
  }));

  // Build a ``positioned-by-id`` map so we can resolve edge endpoints.
  const positionedById = new Map<string, PositionedNode>();
  for (const p of positionedNodes) positionedById.set(p.id, p);

  // Edge index keyed by (from, to) for label lookup.
  const edgeByEndpoint = new Map<string, LineageEdge>();
  for (const e of edgesIn) {
    edgeByEndpoint.set(`${e.from}>>${e.to}`, e);
  }

  const positionedEdges: PositionedEdge[] = [];
  for (const link of (positioned as HierarchyPointNode<Synthetic>).links()) {
    const sourceReal = link.source.data.real;
    if (sourceReal === null) continue; // virtual-root edges
    const fromId = link.source.data.id;
    const toId = link.target.data.id;
    const from = positionedById.get(fromId);
    const to = positionedById.get(toId);
    if (!from || !to) continue;
    positionedEdges.push({
      from,
      to,
      edge: edgeByEndpoint.get(`${fromId}>>${toId}`) ?? null,
    });
  }

  const width = maxX - minX + margin * 2;
  const height = maxY - minY + margin * 2;
  return { nodes: positionedNodes, edges: positionedEdges, width, height };
}

interface TreeModeProps {
  lineage: LineageDAG;
  onNodeSelect: (version: string) => void;
  // Test seam — production callers pass nothing and the component falls
  // back to the real ``computeTreeLayout`` import. Letting tests inject
  // a stub avoids the jsdom layout-precision rabbit hole.
  layoutFn?: (dag: LineageDAG) => LayoutResult;
}

function TreeMode({ lineage, onNodeSelect, layoutFn }: TreeModeProps) {
  const layout = useMemo(
    () => (layoutFn ?? computeTreeLayout)(lineage),
    [lineage, layoutFn],
  );

  const nodeCount = Array.isArray(lineage.nodes) ? lineage.nodes.length : 0;
  if (nodeCount === 0) {
    return (
      <p
        data-testid="lineage-empty-tree"
        style={{ color: "#888", fontStyle: "italic" }}
      >
        No lineage yet — first version will appear after promotion.
      </p>
    );
  }

  const handleSelect = (versionName: string) => {
    onNodeSelect(versionName);
  };

  return (
    <svg
      data-testid="lineage-tree-svg"
      role="tree"
      aria-label="Version lineage tree"
      width={layout.width}
      height={layout.height}
      style={{ background: "transparent" }}
    >
      {/* Edges first so nodes paint on top */}
      <g data-testid="lineage-tree-edges">
        {layout.edges.map(({ from, to, edge }) => {
          const labelX = (from.x + to.x) / 2;
          const labelY = (from.y + to.y) / 2;
          const label = edge?.improvement_title ?? "—";
          return (
            <g key={`${from.id}>>${to.id}`}>
              <line
                data-testid={`lineage-tree-edge-${from.id}-${to.id}`}
                x1={from.x}
                y1={from.y}
                x2={to.x}
                y2={to.y}
                stroke="#555"
                strokeWidth={1.5}
              />
              <text
                data-testid={`lineage-tree-edge-label-${from.id}-${to.id}`}
                x={labelX}
                y={labelY - 4}
                fontSize={10}
                fill="#bbb"
                textAnchor="middle"
                style={{ pointerEvents: "none" }}
              >
                {label}
              </text>
            </g>
          );
        })}
      </g>
      <g data-testid="lineage-tree-nodes">
        {layout.nodes.map((p) => {
          const fill = HARNESS_FILL[p.data.harness_origin] ?? "#9aa0a6";
          return (
            <g
              key={p.id}
              data-testid={`lineage-tree-node-${p.data.version}`}
              data-harness={p.data.harness_origin}
              transform={`translate(${p.x},${p.y})`}
              role="treeitem"
              aria-label={`${p.data.version} (${p.data.harness_origin})`}
              tabIndex={0}
              style={{ cursor: "pointer", outline: "none" }}
              onClick={() => handleSelect(p.data.version)}
              onKeyDown={(ev) => {
                if (ev.key === "Enter" || ev.key === " ") {
                  ev.preventDefault();
                  handleSelect(p.data.version);
                }
              }}
            >
              <circle r={NODE_RADIUS} fill={fill} stroke="#222" strokeWidth={2} />
              <text
                textAnchor="middle"
                dy="0.35em"
                fontSize={11}
                fill="#fff"
                style={{ pointerEvents: "none", fontWeight: 600 }}
              >
                {p.data.version}
              </text>
            </g>
          );
        })}
      </g>
    </svg>
  );
}

// --- Timeline-mode rendering (port of ImprovementsTab) ------------------

export type TimelineFilter = "all" | "advised" | "evolve";

interface TimelineModeProps {
  // Filter + expanded-row state are lifted into ``LineageView`` so they
  // survive a tree → timeline → tree mode toggle. Receiving them as
  // props (rather than re-deriving via ``useState`` here) is the cheap
  // half of "Option A" from the iter-2 review (less DOM than the
  // alternative of always-rendering both modes hidden via display:none).
  filter: TimelineFilter;
  setFilter: (next: TimelineFilter) => void;
  expandedId: string | null;
  setExpandedId: (next: string | null) => void;
}

function TimelineMode({
  filter,
  setFilter,
  expandedId,
  setExpandedId,
}: TimelineModeProps) {
  const { data, isStale, isLoading, lastSuccess, refresh } = useApi<
    ImprovementsResponse
  >("/api/improvements/unified", {
    cacheKey: "/api/improvements/unified::improvements-unified-v1",
  });

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
      <div className="improvements-tab" data-testid="lineage-timeline-loading">
        <p>Loading improvements…</p>
      </div>
    );
  }

  return (
    <div className="improvements-tab" data-testid="lineage-timeline">
      {isStale ? (
        <StaleDataBanner lastSuccess={lastSuccess} label="Improvements" />
      ) : null}

      <div className="improvements-header">
        <h3 style={{ margin: 0 }}>Improvements</h3>
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

// --- Top-level component ------------------------------------------------

export type LineageMode = "tree" | "timeline";

export interface LineageViewProps {
  onNodeSelect: (version: string) => void;
  // Test seam — see ``TreeMode`` above. Production callers omit; tests
  // can inject a deterministic layout function.
  layoutFn?: (dag: LineageDAG) => LayoutResult;
}

// Stable IDs wired through ``aria-controls``/``aria-labelledby`` for
// the WAI-ARIA tablist pattern. Static (per-mode) — no DOM collisions
// since LineageView is mounted at most once per dashboard render.
const MODE_TAB_ID: Record<LineageMode, string> = {
  tree: "lineage-mode-tab-tree",
  timeline: "lineage-mode-tab-timeline",
};
const MODE_PANEL_ID: Record<LineageMode, string> = {
  tree: "lineage-mode-panel-tree",
  timeline: "lineage-mode-panel-timeline",
};

export function LineageView({ onNodeSelect, layoutFn }: LineageViewProps) {
  const [mode, setMode] = useState<LineageMode>("tree");
  const { lineage, loading, error, refetch, isStale, lastSuccess } =
    useLineage();

  // Lifted timeline-mode state — survives a tree → timeline → tree
  // toggle so the operator's filter + expanded row don't reset every
  // time they peek at the tree (Option A from iter-2 review §5: less
  // DOM than mounting both modes hidden via display:none).
  const [timelineFilter, setTimelineFilter] = useState<TimelineFilter>("all");
  const [timelineExpandedId, setTimelineExpandedId] = useState<string | null>(
    null,
  );

  const handleModeChange = useCallback((next: LineageMode) => {
    setMode(next);
  }, []);

  const activePanelId = MODE_PANEL_ID[mode];
  const activeTabId = MODE_TAB_ID[mode];

  return (
    <div className="lineage-view" data-testid="lineage-view">
      {isStale ? (
        <StaleDataBanner lastSuccess={lastSuccess} label="Lineage" />
      ) : null}
      <div
        className="lineage-mode-toggle"
        role="tablist"
        aria-label="Lineage mode"
        data-testid="lineage-mode-toggle"
        style={modeToggleStyle}
      >
        {(["tree", "timeline"] as LineageMode[]).map((value) => {
          const isActive = mode === value;
          return (
            <button
              key={value}
              id={MODE_TAB_ID[value]}
              type="button"
              role="tab"
              aria-selected={isActive}
              aria-controls={MODE_PANEL_ID[value]}
              data-testid={`lineage-mode-${value}`}
              onClick={() => handleModeChange(value)}
              style={isActive ? modeButtonActive : modeButtonInactive}
            >
              {value === "tree" ? "Tree" : "Timeline"}
            </button>
          );
        })}
        <button
          type="button"
          data-testid="lineage-refresh"
          onClick={() => refetch()}
          style={refreshButtonStyle}
        >
          Refresh
        </button>
      </div>

      <div
        role="tabpanel"
        id={activePanelId}
        aria-labelledby={activeTabId}
        data-testid={`lineage-mode-panel-${mode}`}
      >
        {mode === "tree" ? (
          loading && !lineage ? (
            <p data-testid="lineage-tree-loading">Loading lineage…</p>
          ) : error && !lineage ? (
            <p
              data-testid="lineage-tree-error"
              style={{ color: "#888", fontStyle: "italic" }}
            >
              Failed to load lineage.
            </p>
          ) : (
            <TreeMode
              lineage={lineage ?? { nodes: [], edges: [] }}
              onNodeSelect={onNodeSelect}
              layoutFn={layoutFn}
            />
          )
        ) : (
          <TimelineMode
            filter={timelineFilter}
            setFilter={setTimelineFilter}
            expandedId={timelineExpandedId}
            setExpandedId={setTimelineExpandedId}
          />
        )}
      </div>
    </div>
  );
}

const modeToggleStyle: React.CSSProperties = {
  display: "flex",
  gap: "4px",
  alignItems: "center",
  marginBottom: "12px",
  borderBottom: "1px solid #333",
  paddingBottom: "8px",
};

const modeButtonBase: React.CSSProperties = {
  padding: "6px 12px",
  background: "transparent",
  border: "1px solid #444",
  cursor: "pointer",
  borderRadius: "4px",
};

const modeButtonActive: React.CSSProperties = {
  ...modeButtonBase,
  background: "var(--accent-bg, #1a3a5c)",
  borderColor: "var(--accent-border, #3182ce)",
  color: "var(--accent, #fff)",
};

const modeButtonInactive: React.CSSProperties = {
  ...modeButtonBase,
  color: "#aaa",
};

const refreshButtonStyle: React.CSSProperties = {
  marginLeft: "auto",
  padding: "6px 12px",
};

export default LineageView;
