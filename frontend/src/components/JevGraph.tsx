import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import { hierarchy, tree } from "d3-hierarchy";
import {
  JEV_NODE_RUNTIME_LABELS,
  isCurrentRuntime,
  nodeRuntimeLabel,
  type JevNodeRuntime,
  type JevPolicy,
  type JevPolicyNode,
} from "../types/jev";

/**
 * Jev decision graph — the run's archived policy forest drawn as an SVG tree.
 *
 * ``d3-hierarchy`` lays the policy roots out beneath one synthetic display
 * root (never drawn), left to right by depth. Only structural child edges
 * are drawn: runtime transitions are shown as labeled history by the
 * caller, never as extra edges, so the structure stays a forest.
 *
 * Interaction (all presentation state; nothing is sent anywhere):
 *   - pan by dragging the background, the arrow buttons, or by keyboard
 *     focus moving to a node outside the view;
 *   - zoom with the wheel, the +/− buttons, or the + / − / 0 keys, bounded
 *     to ``MIN_SCALE``..``MAX_SCALE``;
 *   - collapse/expand a branch with its +/− toggle, ArrowLeft/ArrowRight,
 *     or Expand all / Collapse all. Composite nodes below the roots start
 *     collapsed, so the first view is an overview of each lane; a collapsed
 *     node names how many hidden nodes are active or waiting;
 *   - nodes form an ARIA tree with one tab stop: arrow keys move focus,
 *     Enter/Space select.
 *
 * Every node carries a textual status (glyph and words) and a distinct
 * outline style, so active / waiting / failed nodes never differ by colour
 * alone. Only a live state (``live``) draws active and waiting nodes as
 * current, and only its active nodes pulse. A stale, offline or final state
 * draws them muted and labels them "(last known)": cached nodes never look
 * live. Each tree item exposes ``data-status`` and ``data-live``.
 */

const NODE_WIDTH = 220;
const NODE_HEIGHT = 52;
const COLUMN_GAP = 280;
const ROW_GAP = 64;
const MARGIN = 24;
const TOGGLE_RADIUS = 8;
const MIN_SCALE = 0.25;
const MAX_SCALE = 2.5;
const ZOOM_STEP = 1.25;
const PAN_STEP = 80;
const SHORT_ID_CHARS = 26;
/** Text lines inside a node box are cut to fit it (the full text is in its name and tooltip). */
const SUMMARY_CHARS = 36;
/** Used when the viewport has no measured size yet (and under jsdom). */
const FALLBACK_VIEWPORT = { width: 760, height: 520 };

const RUNTIME_GLYPHS: Record<JevNodeRuntime, string> = {
  active: "▶",
  waiting: "⏸",
  running: "↻",
  success: "✓",
  failure: "✕",
  idle: "·",
};

const RUNTIME_ORDER: JevNodeRuntime[] = [
  "active",
  "waiting",
  "running",
  "success",
  "failure",
  "idle",
];

interface Transform {
  x: number;
  y: number;
  k: number;
}

const IDENTITY: Transform = { x: 0, y: 0, k: 1 };

interface Structure {
  byId: Map<string, JevPolicyNode>;
  parentOf: Map<string, string | null>;
  depthOf: Map<string, number>;
}

interface PlacedNode {
  node: JevPolicyNode;
  /** Top-left corner of the node box, in graph coordinates. */
  x: number;
  y: number;
  level: number;
  setSize: number;
  posInSet: number;
  firstChild: string | null;
}

interface Layout {
  /** Visible nodes in display (pre-)order: the order arrow keys move through. */
  order: PlacedNode[];
  byId: Map<string, PlacedNode>;
  edges: Array<{ id: string; path: string }>;
  width: number;
  height: number;
}

interface DisplayNode {
  node: JevPolicyNode | null; // null: the synthetic display root
  children: DisplayNode[];
}

interface DragStart {
  pointerId: number;
  clientX: number;
  clientY: number;
  origin: Transform;
}

function describeStructure(policy: JevPolicy): Structure {
  const byId = new Map(policy.nodes.map((node) => [node.id, node]));
  const parentOf = new Map<string, string | null>();
  const depthOf = new Map<string, number>();
  const pending: Array<[string, string | null, number]> = policy.roots.map((id) => [id, null, 1]);
  while (pending.length > 0) {
    const [id, parent, depth] = pending.pop() as [string, string | null, number];
    parentOf.set(id, parent);
    depthOf.set(id, depth);
    for (const child of byId.get(id)?.children ?? []) pending.push([child, id, depth + 1]);
  }
  return { byId, parentOf, depthOf };
}

function isComposite(node: JevPolicyNode): boolean {
  return node.children.length > 0;
}

/** Composite nodes below the roots: collapsed in the first (overview) view. */
function overviewCollapsed(structure: Structure): Set<string> {
  const collapsed = new Set<string>();
  for (const [id, node] of structure.byId) {
    if (isComposite(node) && (structure.depthOf.get(id) ?? 1) > 1) collapsed.add(id);
  }
  return collapsed;
}

function allCollapsed(structure: Structure): Set<string> {
  return new Set([...structure.byId.values()].filter(isComposite).map((node) => node.id));
}

function computeLayout(
  policy: JevPolicy,
  structure: Structure,
  collapsed: ReadonlySet<string>,
): Layout {
  const build = (id: string): DisplayNode => {
    const node = structure.byId.get(id) as JevPolicyNode;
    return { node, children: collapsed.has(id) ? [] : node.children.map(build) };
  };
  const root = hierarchy<DisplayNode>(
    { node: null, children: policy.roots.map(build) },
    (display) => display.children,
  );
  const positioned = tree<DisplayNode>()
    .nodeSize([ROW_GAP, COLUMN_GAP])
    .separation((a, b) => (a.parent === b.parent ? 1 : 1.3))(root);
  const real = positioned.descendants().filter((d) => d.data.node !== null);
  const minRow = Math.min(...real.map((d) => d.x));
  const maxRow = Math.max(...real.map((d) => d.x));
  const maxDepth = Math.max(...real.map((d) => d.depth));

  const order: PlacedNode[] = [];
  const byId = new Map<string, PlacedNode>();
  const edges: Layout["edges"] = [];
  positioned.eachBefore((d) => {
    const node = d.data.node;
    if (node === null) return;
    const siblings = d.parent?.children ?? [d];
    const placed: PlacedNode = {
      node,
      x: MARGIN + (d.depth - 1) * COLUMN_GAP,
      y: MARGIN + d.x - minRow,
      level: d.depth,
      setSize: siblings.length,
      posInSet: siblings.indexOf(d) + 1,
      firstChild: d.children?.[0]?.data.node?.id ?? null,
    };
    order.push(placed);
    byId.set(node.id, placed);
  });
  for (const placed of order) {
    if (collapsed.has(placed.node.id)) continue;
    for (const childId of placed.node.children) {
      const child = byId.get(childId);
      if (child === undefined) continue;
      const startX = placed.x + NODE_WIDTH;
      const startY = placed.y + NODE_HEIGHT / 2;
      const endY = child.y + NODE_HEIGHT / 2;
      const middleX = (startX + child.x) / 2;
      edges.push({
        id: `${placed.node.id}>${childId}`,
        path: `M${startX},${startY} C${middleX},${startY} ${middleX},${endY} ${child.x},${endY}`,
      });
    }
  }
  return {
    order,
    byId,
    edges,
    width:
      2 * MARGIN +
      (maxDepth - 1) * COLUMN_GAP +
      NODE_WIDTH +
      TOGGLE_RADIUS,
    height: 2 * MARGIN + maxRow - minRow + NODE_HEIGHT,
  };
}

function clampScale(scale: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
}

/** Scale by ``factor`` (bounded) keeping the viewport point (``px``, ``py``) fixed. */
function zoomAt(transform: Transform, factor: number, px: number, py: number): Transform {
  const k = clampScale(transform.k * factor);
  const ratio = k / transform.k;
  return { k, x: px - (px - transform.x) * ratio, y: py - (py - transform.y) * ratio };
}

function viewportSize(svg: SVGSVGElement | null): { width: number; height: number } {
  return {
    width: svg?.clientWidth || FALLBACK_VIEWPORT.width,
    height: svg?.clientHeight || FALLBACK_VIEWPORT.height,
  };
}

/** The smallest pan that brings ``placed``'s box fully into the viewport. */
function reveal(
  transform: Transform,
  placed: PlacedNode,
  viewport: { width: number; height: number },
): Transform {
  const pad = MARGIN;
  const left = transform.x + placed.x * transform.k;
  const top = transform.y + placed.y * transform.k;
  const right = left + NODE_WIDTH * transform.k;
  const bottom = top + NODE_HEIGHT * transform.k;
  let { x, y } = transform;
  if (left < pad) x += pad - left;
  else if (right > viewport.width - pad) x -= right - (viewport.width - pad);
  if (top < pad) y += pad - top;
  else if (bottom > viewport.height - pad) y -= bottom - (viewport.height - pad);
  return { ...transform, x, y };
}

function clip(text: string, chars: number): string {
  return text.length > chars ? `${text.slice(0, chars - 1)}…` : text;
}

function shortId(id: string): string {
  return clip(id.slice(id.lastIndexOf(".") + 1), SHORT_ID_CHARS);
}

function hiddenSummary(
  id: string,
  structure: Structure,
  runtime: ReadonlyMap<string, JevNodeRuntime>,
  live: boolean,
): string {
  let hidden = 0;
  let active = 0;
  let waiting = 0;
  const pending = [...(structure.byId.get(id)?.children ?? [])];
  while (pending.length > 0) {
    const current = pending.pop() as string;
    hidden += 1;
    const status = runtime.get(current);
    if (status === "active") active += 1;
    if (status === "waiting") waiting += 1;
    pending.push(...(structure.byId.get(current)?.children ?? []));
  }
  const parts = [`${hidden} hidden`];
  if (active > 0) parts.push(`${active} active`);
  if (waiting > 0) parts.push(`${waiting} waiting`);
  const summary = parts.join(" · ");
  return live || (active === 0 && waiting === 0) ? summary : `${summary} (last known)`;
}

export interface JevGraphProps {
  policy: JevPolicy;
  /** Runtime status by node ID; a node without an entry is drawn as ``idle``. */
  runtime: ReadonlyMap<string, JevNodeRuntime>;
  /** Whether the state is live: only then are active/waiting nodes drawn as current. */
  live: boolean;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
}

export function JevGraph({ policy, runtime, live, selectedNodeId, onSelectNode }: JevGraphProps) {
  const structure = useMemo(() => describeStructure(policy), [policy]);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() =>
    overviewCollapsed(structure),
  );
  const [transform, setTransform] = useState<Transform>(IDENTITY);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const nodeElements = useRef(new Map<string, SVGGElement>());
  const drag = useRef<DragStart | null>(null);
  const hintId = useId();

  const layout = useMemo(
    () => computeLayout(policy, structure, collapsed),
    [policy, structure, collapsed],
  );
  const tabStop =
    focusedId !== null && layout.byId.has(focusedId) ? focusedId : layout.order[0].node.id;

  // React registers wheel listeners as passive, which cannot stop the page
  // from scrolling; zooming needs a non-passive native listener.
  useEffect(() => {
    const svg = svgRef.current;
    if (svg === null) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const bounds = svg.getBoundingClientRect();
      const factor = event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
      setTransform((current) =>
        zoomAt(current, factor, event.clientX - bounds.left, event.clientY - bounds.top),
      );
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  const zoomCentered = (factor: number) => {
    const { width, height } = viewportSize(svgRef.current);
    setTransform((current) => zoomAt(current, factor, width / 2, height / 2));
  };
  const pan = (dx: number, dy: number) =>
    setTransform((current) => ({ ...current, x: current.x + dx, y: current.y + dy }));
  const fitToView = () => {
    const { width, height } = viewportSize(svgRef.current);
    const k = clampScale(Math.min(width / layout.width, height / layout.height));
    setTransform({ k, x: (width - layout.width * k) / 2, y: (height - layout.height * k) / 2 });
  };
  const toggle = (id: string) =>
    setCollapsed((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  const select = (id: string) => {
    setFocusedId(id);
    onSelectNode(id);
  };
  const moveFocus = (id: string | null | undefined) => {
    if (id === null || id === undefined) return;
    const placed = layout.byId.get(id);
    if (placed === undefined) return;
    setFocusedId(id);
    nodeElements.current.get(id)?.focus({ preventScroll: true });
    const viewport = viewportSize(svgRef.current);
    setTransform((current) => reveal(current, placed, viewport));
  };

  const onNodeKeyDown = (event: KeyboardEvent<SVGGElement>, placed: PlacedNode) => {
    const id = placed.node.id;
    const index = layout.order.indexOf(placed);
    const expanded = isComposite(placed.node) && !collapsed.has(id);
    switch (event.key) {
      case "Enter":
      case " ":
        select(id);
        break;
      case "ArrowDown":
        moveFocus(layout.order[index + 1]?.node.id);
        break;
      case "ArrowUp":
        moveFocus(layout.order[index - 1]?.node.id);
        break;
      case "Home":
        moveFocus(layout.order[0].node.id);
        break;
      case "End":
        moveFocus(layout.order[layout.order.length - 1].node.id);
        break;
      case "ArrowRight":
        if (expanded) moveFocus(placed.firstChild);
        else if (isComposite(placed.node)) toggle(id);
        break;
      case "ArrowLeft":
        if (expanded) toggle(id);
        else moveFocus(structure.parentOf.get(id));
        break;
      default:
        return;
    }
    event.preventDefault();
  };

  const onViewportKeyDown = (event: KeyboardEvent<SVGSVGElement>) => {
    if (event.key === "+" || event.key === "=") zoomCentered(ZOOM_STEP);
    else if (event.key === "-") zoomCentered(1 / ZOOM_STEP);
    else if (event.key === "0") setTransform(IDENTITY);
    else return;
    event.preventDefault();
  };

  const onPointerDown = (event: PointerEvent<SVGRectElement>) => {
    event.currentTarget.setPointerCapture?.(event.pointerId);
    drag.current = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      origin: transform,
    };
  };
  const onPointerMove = (event: PointerEvent<SVGRectElement>) => {
    const start = drag.current;
    if (start === null || start.pointerId !== event.pointerId) return;
    setTransform({
      ...start.origin,
      x: start.origin.x + event.clientX - start.clientX,
      y: start.origin.y + event.clientY - start.clientY,
    });
  };
  const onPointerEnd = (event: PointerEvent<SVGRectElement>) => {
    if (drag.current?.pointerId === event.pointerId) drag.current = null;
  };

  return (
    <div className="jev-graph" data-testid="jev-graph">
      <div className="jev-graph-toolbar" role="toolbar" aria-label="Graph view controls">
        <button
          type="button"
          onClick={() => zoomCentered(ZOOM_STEP)}
          disabled={transform.k >= MAX_SCALE}
          aria-label="Zoom in"
        >
          +
        </button>
        <button
          type="button"
          onClick={() => zoomCentered(1 / ZOOM_STEP)}
          disabled={transform.k <= MIN_SCALE}
          aria-label="Zoom out"
        >
          −
        </button>
        <span className="jev-graph-zoom" aria-live="polite" data-testid="jev-graph-zoom">
          Zoom {Math.round(transform.k * 100)}%
        </span>
        <button type="button" onClick={() => pan(PAN_STEP, 0)} aria-label="Pan left">
          ←
        </button>
        <button type="button" onClick={() => pan(0, PAN_STEP)} aria-label="Pan up">
          ↑
        </button>
        <button type="button" onClick={() => pan(0, -PAN_STEP)} aria-label="Pan down">
          ↓
        </button>
        <button type="button" onClick={() => pan(-PAN_STEP, 0)} aria-label="Pan right">
          →
        </button>
        <button type="button" onClick={fitToView}>
          Fit
        </button>
        <button type="button" onClick={() => setTransform(IDENTITY)}>
          Reset view
        </button>
        <button type="button" onClick={() => setCollapsed(new Set())}>
          Expand all
        </button>
        <button type="button" onClick={() => setCollapsed(allCollapsed(structure))}>
          Collapse all
        </button>
      </div>
      <p className="jev-graph-legend">
        {RUNTIME_ORDER.map((status) => (
          <span key={status}>
            {RUNTIME_GLYPHS[status]} {JEV_NODE_RUNTIME_LABELS[status]}
          </span>
        ))}
        {!live && <span>(last known): from the last recorded state, not live</span>}
      </p>
      <p className="jev-graph-hint" id={hintId}>
        Arrow keys move between nodes · Enter or Space selects · Left / Right collapse and
        expand · + / − zoom · drag the background to pan
      </p>
      <svg
        ref={svgRef}
        className="jev-graph-viewport"
        aria-label="Policy graph"
        aria-describedby={hintId}
        onKeyDown={onViewportKeyDown}
      >
        <rect
          className="jev-graph-background"
          data-testid="jev-graph-background"
          width="100%"
          height="100%"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerEnd}
          onPointerCancel={onPointerEnd}
        />
        <g
          data-testid="jev-graph-canvas"
          transform={`translate(${transform.x},${transform.y}) scale(${transform.k})`}
        >
          <g className="jev-graph-edges" data-testid="jev-graph-edges">
            {layout.edges.map((edge) => (
              <path key={edge.id} className="jev-graph-edge" d={edge.path} />
            ))}
          </g>
          <g role="tree" aria-label="Policy nodes">
            {layout.order.map((placed) => {
              const { node } = placed;
              const status = runtime.get(node.id) ?? "idle";
              const composite = isComposite(node);
              const isCollapsed = composite && collapsed.has(node.id);
              const summary = isCollapsed
                ? hiddenSummary(node.id, structure, runtime, live)
                : null;
              const statusText = `${node.kind} · ${nodeRuntimeLabel(status, live)}`;
              const drawnLive = live && isCurrentRuntime(status);
              const name = summary === null ? statusText : `${statusText}, collapsed: ${summary}`;
              const classes = ["jev-graph-node", `jev-graph-node-${status}`];
              if (node.id === selectedNodeId) classes.push("jev-graph-node-selected");
              if (drawnLive && status === "active") classes.push("jev-graph-node-live");
              if (!live && isCurrentRuntime(status)) classes.push("jev-graph-node-last-known");
              return (
                <g key={node.id} transform={`translate(${placed.x},${placed.y})`}>
                  <g
                    ref={(element) => {
                      if (element === null) nodeElements.current.delete(node.id);
                      else nodeElements.current.set(node.id, element);
                    }}
                    className={classes.join(" ")}
                    role="treeitem"
                    data-status={status}
                    data-live={drawnLive}
                    aria-label={`${node.id}, ${name}`}
                    aria-level={placed.level}
                    aria-setsize={placed.setSize}
                    aria-posinset={placed.posInSet}
                    aria-expanded={composite ? !isCollapsed : undefined}
                    aria-selected={node.id === selectedNodeId}
                    tabIndex={node.id === tabStop ? 0 : -1}
                    onClick={() => select(node.id)}
                    onFocus={() => setFocusedId(node.id)}
                    onKeyDown={(event) => onNodeKeyDown(event, placed)}
                  >
                    <title>
                      {`${node.id} — ${node.label}${summary === null ? "" : ` (${summary})`}`}
                    </title>
                    <rect width={NODE_WIDTH} height={NODE_HEIGHT} rx={6} />
                    <text x={10} y={15} className="jev-graph-node-name">
                      {shortId(node.id)}
                    </text>
                    <text x={10} y={30} className="jev-graph-node-status">
                      {`${RUNTIME_GLYPHS[status]} ${statusText}`}
                    </text>
                    {summary !== null && (
                      <text x={10} y={44} className="jev-graph-node-hidden">
                        {clip(summary, SUMMARY_CHARS)}
                      </text>
                    )}
                  </g>
                </g>
              );
            })}
          </g>
          {/* Branch toggles sit outside the tree: an ARIA tree owns only tree items. */}
          <g>
            {layout.order
              .filter((placed) => isComposite(placed.node))
              .map(({ node, x, y }) => {
                const isCollapsed = collapsed.has(node.id);
                return (
                  <g
                    key={node.id}
                    className="jev-graph-toggle"
                    role="button"
                    aria-label={`${isCollapsed ? "Expand" : "Collapse"} ${node.id}`}
                    tabIndex={-1}
                    transform={`translate(${x + NODE_WIDTH},${y + NODE_HEIGHT / 2})`}
                    onClick={() => toggle(node.id)}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      event.preventDefault();
                      toggle(node.id);
                    }}
                  >
                    <circle r={TOGGLE_RADIUS} />
                    <text dy="0.35em" textAnchor="middle">
                      {isCollapsed ? "+" : "−"}
                    </text>
                  </g>
                );
              })}
          </g>
        </g>
      </svg>
    </div>
  );
}
