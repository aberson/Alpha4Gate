import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import { hierarchy, tree } from "d3-hierarchy";
import {
  JEV_NODE_RUNTIME_LABELS,
  JEV_TRAIL_SECONDS,
  isCurrentRuntime,
  laneToneClass,
  nodeRuntimeLabel,
  type JevNodeRuntime,
  type JevPolicy,
  type JevPolicyNode,
} from "../types/jev";
import { nodeCodes, NO_ACTIVITY, type JevGraphActivity, type JevNodeSource } from "./jevEvidence";

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
 *     or Expand all / Fold nodes. Composite nodes below the roots start
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
 *
 * Lanes and observed activity (plan J2 Step 221, D5):
 *   - colour identifies a node's root lane (a coloured band behind each lane,
 *     a stripe on each node, and the lane legend with its active/waiting
 *     counts); status stays outline, glyph and words;
 *   - ``activity.edges`` are the only edges drawn as activity, each one
 *     recorded by the caller from the run's evidence: ``current`` (the latest
 *     state's running path) solid, ``recent`` (an event that named the edge
 *     within ``JEV_TRAIL_SECONDS`` game seconds) dashed and fading, labeled
 *     recent. Nothing between two polls is invented. Only a live state
 *     animates the current path, never with reduced motion; otherwise the
 *     activity is labeled last known;
 *   - "Follow activity" (off by default) brings the newest active node into
 *     view, expanding its branch, and keeps that node until it stops being a
 *     target; it acts only while live, never changes the selection, and a manual
 *     pan stays until the followed node changes. The view always jumps, never
 *     animates;
 *   - ``activity.sources`` names, per node, the decision its newest recorded
 *     event carries (a Typesafe request, a fallback or the local graph);
 *   - ``reveal`` asks the graph (from outside, e.g. the plan panel) to expand
 *     a node's branch and bring it into view.
 */

const NODE_WIDTH = 220;
const NODE_HEIGHT = 52;
/** Width of the lane-colour stripe at a node's left edge. */
const LANE_STRIPE = 6;
/** Space a lane band leaves around its nodes. */
const BAND_PAD = 6;
const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";
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


/** The key of a structural edge (the same one the layout gives its path). */
function edgeId(parent: string, child: string): string {
  return `${parent}>${child}`;
}

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
  /** Each node's root lane. */
  laneOf: Map<string, string>;
  /** Each root lane's colour class. */
  toneOf: Map<string, string>;
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
  const laneOf = new Map<string, string>();
  const toneOf = new Map(policy.roots.map((root, index) => [root, laneToneClass(root, index)]));
  const pending: Array<[string, string | null, number, string]> = policy.roots.map((id) => [
    id,
    null,
    1,
    id,
  ]);
  while (pending.length > 0) {
    const [id, parent, depth, lane] = pending.pop() as [string, string | null, number, string];
    parentOf.set(id, parent);
    depthOf.set(id, depth);
    laneOf.set(id, lane);
    for (const child of byId.get(id)?.children ?? []) pending.push([child, id, depth + 1, lane]);
  }
  return { byId, parentOf, depthOf, laneOf, toneOf };
}

/** ``collapsed`` without ``id``'s ancestors (the same set when none was collapsed). */
function expandedTo(
  id: string,
  structure: Structure,
  collapsed: ReadonlySet<string>,
): ReadonlySet<string> {
  const ancestors: string[] = [];
  for (let at = structure.parentOf.get(id); at; at = structure.parentOf.get(at)) {
    ancestors.push(at);
  }
  if (!ancestors.some((ancestor) => collapsed.has(ancestor))) return collapsed;
  const next = new Set(collapsed);
  for (const ancestor of ancestors) next.delete(ancestor);
  return next;
}

/** Each lane's band: the vertical extent of its visible nodes, in graph coordinates. */
function laneBands(
  layout: Layout,
  structure: Structure,
  nodeHeight = NODE_HEIGHT,
  nodeWidth = NODE_WIDTH,
): Array<{ lane: string; top: number; bottom: number; left: number; right: number }> {
  const extent = new Map<string, { top: number; bottom: number; left: number; right: number }>();
  for (const placed of layout.order) {
    const lane = structure.laneOf.get(placed.node.id) ?? placed.node.id;
    const band = extent.get(lane);
    const top = placed.y - BAND_PAD;
    const bottom = placed.y + nodeHeight + BAND_PAD;
    const left = placed.x - BAND_PAD;
    const right = placed.x + nodeWidth + TOGGLE_RADIUS + BAND_PAD;
    if (band === undefined) extent.set(lane, { top, bottom, left, right });
    else extent.set(lane, {
      top: Math.min(band.top, top), bottom: Math.max(band.bottom, bottom),
      left: Math.min(band.left, left), right: Math.max(band.right, right),
    });
  }
  return [...extent].map(([lane, band]) => ({ lane, ...band }));
}

function subscribeReducedMotion(onChange: () => void): () => void {
  if (typeof window.matchMedia !== "function") return () => {};
  const query = window.matchMedia(REDUCED_MOTION_QUERY);
  query.addEventListener?.("change", onChange);
  return () => query.removeEventListener?.("change", onChange);
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(REDUCED_MOTION_QUERY).matches;
}

/** Whether the viewer asked for reduced motion (kept current while mounted). */
function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribeReducedMotion, prefersReducedMotion, () => false);
}

function sourceText(source: JevNodeSource | undefined): string {
  return source === undefined ? "" : `, source: ${source.detail}`;
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
  compact = false,
): Layout {
  const nodeWidth = compact ? 88 : NODE_WIDTH;
  const nodeHeight = compact ? 32 : NODE_HEIGHT;
  const rowGap = compact ? 42 : ROW_GAP;
  const columnGap = compact ? 132 : COLUMN_GAP;
  if (policy.roots.length === 0) {
    return { order: [], byId: new Map(), edges: [], width: MARGIN * 2, height: MARGIN * 2 };
  }
  // Pack independent branches across the available canvas instead of one tall strip.
  if (policy.roots.length > 1) {
    const columns = compact ? 3 : 2;
    const parts = policy.roots.map((root) =>
      computeLayout({ ...policy, roots: [root] }, structure, collapsed, compact),
    );
    const widths = Array.from({ length: columns }, (_, column) =>
      Math.max(0, ...parts.filter((_, i) => i % columns === column).map((part) => part.width)),
    );
    const order: PlacedNode[] = [];
    const byId = new Map<string, PlacedNode>();
    let top = 0;
    for (let row = 0; row < parts.length; row += columns) {
      let left = 0;
      for (let column = 0; column < columns && row + column < parts.length; column++) {
        for (const placed of parts[row + column].order) {
          const shifted = {
            ...placed, x: placed.x + left, y: placed.y + top,
            ...(placed.level === 1 ? { setSize: policy.roots.length, posInSet: row + column + 1 } : {}),
          };
          order.push(shifted);
          byId.set(shifted.node.id, shifted);
        }
        left += widths[column];
      }
      top += Math.max(...parts.slice(row, row + columns).map((part) => part.height));
    }
    const edges: Layout["edges"] = [];
    for (const placed of order) {
      for (const childId of placed.node.children) {
        const child = byId.get(childId);
        if (!child) continue;
        const startX = placed.x + nodeWidth;
        const startY = placed.y + nodeHeight / 2;
        const endY = child.y + nodeHeight / 2;
        const middleX = (startX + child.x) / 2;
        edges.push({
          id: edgeId(placed.node.id, childId),
          path: `M${startX},${startY} C${middleX},${startY} ${middleX},${endY} ${child.x},${endY}`,
        });
      }
    }
    return { order, byId, edges, width: widths.reduce((sum, width) => sum + width, 0), height: top };
  }
  const build = (id: string): DisplayNode => {
    const node = structure.byId.get(id) as JevPolicyNode;
    return { node, children: collapsed.has(id) ? [] : node.children.map(build) };
  };
  const root = hierarchy<DisplayNode>(
    { node: null, children: policy.roots.map(build) },
    (display) => display.children,
  );
  const positioned = tree<DisplayNode>()
    .nodeSize([rowGap, columnGap])
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
      x: MARGIN + (d.depth - 1) * columnGap,
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
      const startX = placed.x + nodeWidth;
      const startY = placed.y + nodeHeight / 2;
      const endY = child.y + nodeHeight / 2;
      const middleX = (startX + child.x) / 2;
      edges.push({
        id: edgeId(placed.node.id, childId),
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
      (maxDepth - 1) * columnGap +
      nodeWidth +
      TOGGLE_RADIUS,
    height: 2 * MARGIN + maxRow - minRow + nodeHeight,
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
function revealBox(
  transform: Transform,
  placed: PlacedNode,
  viewport: { width: number; height: number },
  compact = false,
): Transform {
  const pad = MARGIN;
  const left = transform.x + placed.x * transform.k;
  const top = transform.y + placed.y * transform.k;
  const right = left + (compact ? 88 : NODE_WIDTH) * transform.k;
  const bottom = top + (compact ? 32 : NODE_HEIGHT) * transform.k;
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
  compact?: boolean;
  /** Roots to draw. The archived policy remains intact; this only limits presentation. */
  displayRootIds?: readonly string[];
  /** The parent branch overview already carries this information in the focused view. */
  showLaneLegend?: boolean;
  /** Fit on mount and when the user changes the visible branch set, never on polling. */
  fitOnMount?: boolean;
  /** Runtime status by node ID; a node without an entry is drawn as ``idle``. */
  runtime: ReadonlyMap<string, JevNodeRuntime>;
  /** Whether the state is live: only then are active/waiting nodes drawn as current. */
  live: boolean;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
  /** Recorded activity (edges, node sources, follow targets); none by default. */
  activity?: JevGraphActivity;
  /** Expand ``nodeId``'s branch and bring it into view, once per new ``token``. */
  reveal?: { nodeId: string; token: number } | null;
  /** The run has ended (finished, stopped or failed): its last state is when it ended. */
  ended?: boolean;
}

export function JevGraph({
  policy,
  compact = false,
  displayRootIds,
  showLaneLegend = true,
  fitOnMount = false,
  runtime,
  live,
  selectedNodeId,
  onSelectNode,
  activity = NO_ACTIVITY,
  reveal = null,
  ended = false,
}: JevGraphProps) {
  const nodeWidth = compact ? 88 : NODE_WIDTH;
  const nodeHeight = compact ? 32 : NODE_HEIGHT;
  const codes = useMemo(() => nodeCodes(policy), [policy]);
  const structure = useMemo(() => describeStructure(policy), [policy]);
  const displayPolicy = useMemo(
    () =>
      displayRootIds === undefined
        ? policy
        : { ...policy, roots: policy.roots.filter((root) => displayRootIds.includes(root)) },
    [policy, displayRootIds],
  );
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() =>
    overviewCollapsed(structure),
  );
  const [transform, setTransform] = useState<Transform>(IDENTITY);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [following, setFollowing] = useState(false);
  // What the view last brought into view for "Follow activity" and for ``reveal``
  // (state, so a target is acted on once, in the render that first sees it).
  const [followed, setFollowed] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<number | null>(null);
  const [viewport, setViewport] = useState(FALLBACK_VIEWPORT);
  const reducedMotion = useReducedMotion();
  const svgRef = useRef<SVGSVGElement>(null);
  const nodeElements = useRef(new Map<string, SVGGElement>());
  const drag = useRef<DragStart | null>(null);
  const initiallyFitted = useRef<string | null>(null);
  const scopeKey = `${compact ? "nodes" : "boxes"}:${displayPolicy.roots.join("|")}`;
  const hintId = useId();
  const followId = useId();

  const layout = useMemo(
    () => computeLayout(displayPolicy, structure, collapsed, compact),
    [displayPolicy, structure, collapsed, compact],
  );
  const tabStop =
    focusedId !== null && layout.byId.has(focusedId) ? focusedId : layout.order[0]?.node.id ?? null;

  /** Expand ``id``'s branch and pan just enough to show it (a jump, never animated). */
  const bringIntoView = (id: string) => {
    if (!structure.byId.has(id)) return;
    const nextCollapsed = expandedTo(id, structure, collapsed);
    const nextLayout =
      nextCollapsed === collapsed ? layout : computeLayout(displayPolicy, structure, nextCollapsed, compact);
    if (nextCollapsed !== collapsed) setCollapsed(nextCollapsed);
    const placed = nextLayout.byId.get(id);
    if (placed !== undefined) setTransform(revealBox(transform, placed, viewport, compact));
  };

  // "Follow activity" acts once per new target and only while live; ``reveal`` once
  // per token. Both adjust state while rendering (React's pattern for responding to
  // changed props), so nothing moves between those changes. The followed node is kept
  // while it is still a target, so the view does not hop between lanes every poll.
  const candidates = activity.followTargets;
  const followTarget =
    following && live
      ? followed !== null && candidates.includes(followed)
        ? followed
        : (candidates[0] ?? null)
      : null;
  if (followTarget !== followed) {
    setFollowed(followTarget);
    if (followTarget !== null) bringIntoView(followTarget);
  }
  if (reveal !== null && reveal.token !== revealed) {
    setRevealed(reveal.token);
    bringIntoView(reveal.nodeId);
  }

  // The measured viewport, for following (event handlers measure it directly).
  useEffect(() => {
    const svg = svgRef.current;
    if (svg === null || typeof ResizeObserver !== "function") return;
    const observer = new ResizeObserver(() => setViewport(viewportSize(svg)));
    observer.observe(svg);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!fitOnMount || initiallyFitted.current === scopeKey) return;
    initiallyFitted.current = scopeKey;
    const { width, height } = viewportSize(svgRef.current);
    const k = clampScale(Math.min(width / layout.width, height / layout.height));
    setTransform({ k, x: (width - layout.width * k) / 2, y: (height - layout.height * k) / 2 });
  }, [fitOnMount, layout.height, layout.width, scopeKey]);

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
    const size = viewportSize(svgRef.current);
    setTransform((current) => revealBox(current, placed, size, compact));
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
        moveFocus(layout.order[0]?.node.id ?? null);
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

  // Lanes: what each runs in the latest state, for the legend.
  const laneCounts = new Map(policy.roots.map((root) => [root, { active: 0, waiting: 0 }]));
  for (const [id, status] of runtime) {
    const counts = laneCounts.get(structure.laneOf.get(id) ?? "");
    if (counts === undefined) continue;
    if (status === "active") counts.active += 1;
    if (status === "waiting") counts.waiting += 1;
  }
  const activeLanes = policy.roots.filter((root) => {
    const counts = laneCounts.get(root);
    return counts !== undefined && counts.active + counts.waiting > 0;
  });
  const edgePaths = new Map(layout.edges.map((edge) => [edge.id, edge.path]));
  const displayedRoots = new Set(displayPolicy.roots);
  const scopedActivity = activity.edges.filter((edge) =>
    displayedRoots.has(structure.laneOf.get(edge.parent) ?? ""),
  );
  const shownActivity = scopedActivity.filter((edge) =>
    edgePaths.has(edgeId(edge.parent, edge.child)),
  );
  const animate = live && !reducedMotion;
  const lastKnown = live ? "" : " (last known)";
  const laneWhen = live ? "" : ended ? " when the run ended" : " (last known)";
  const currentEdges = scopedActivity.filter((edge) => edge.kind === "current").length;
  const recentEdges = scopedActivity.length - currentEdges;
  let followStatus = "off: the view moves only when you move it";
  if (following && !live) followStatus = "paused: this state is not live";
  else if (following && followed === null) followStatus = "on: no active node to follow";
  else if (following) followStatus = `on: showing ${followed}`;

  return (
    <div
      className="jev-graph"
      data-testid="jev-graph"
      data-reduced-motion={reducedMotion}
      data-following={following}
    >
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
          Fold nodes
        </button>
        <label className="jev-graph-follow">
          <input
            type="checkbox"
            data-testid="jev-follow"
            checked={following}
            onChange={(event) => {
              // Measure now, so the first follow never reveals against a guessed size.
              if (event.target.checked) setViewport(viewportSize(svgRef.current));
              setFollowing(event.target.checked);
            }}
            aria-describedby={followId}
          />
          Follow activity
        </label>
        <span className="jev-graph-follow-status" id={followId} data-testid="jev-follow-status">
          {followStatus}
        </span>
      </div>
      <details className="jev-disclosure"><summary>Graph help and legend</summary>
      <p className="jev-graph-legend">
        {RUNTIME_ORDER.map((status) => (
          <span key={status}>
            {RUNTIME_GLYPHS[status]} {JEV_NODE_RUNTIME_LABELS[status]}
          </span>
        ))}
        {!live && <span>(last known): from the last recorded state, not live</span>}
      </p>
      {showLaneLegend && (
        <p className="jev-graph-legend jev-graph-lanes" data-testid="jev-graph-lanes">
          <span data-testid="jev-graph-active-lanes">
            {activeLanes.length === 0
              ? `No lane active${laneWhen}`
              : `${activeLanes.length} lane(s) active${laneWhen}: ${activeLanes.join(", ")}`}
          </span>
          {policy.roots.map((root) => {
            const counts = laneCounts.get(root) ?? { active: 0, waiting: 0 };
            return (
              <span
                key={root}
                className={`jev-graph-lane-chip ${structure.toneOf.get(root) ?? ""}`}
                data-testid="jev-graph-lane"
                data-lane={root}
                data-active={counts.active}
                data-waiting={counts.waiting}
              >
                <span className="jev-graph-lane-swatch" aria-hidden="true" />
                {root} lane · {counts.active} active · {counts.waiting} waiting
              </span>
            );
          })}
        </p>
      )}
      <p className="jev-graph-legend" data-testid="jev-graph-edge-legend">
        <span>
          ━ current path ({currentEdges}): both ends run in the latest state{lastKnown}
        </span>
        <span>
          ┅ recent ({recentEdges}): recorded in the last {JEV_TRAIL_SECONDS} game s, fading; not
          current
        </span>
        <span>
          Only recorded edges are drawn; nothing between two refreshes is invented.
          {reducedMotion ? " Reduced motion: nothing animates." : ""}
        </span>
      </p>
      <p className="jev-graph-hint" id={hintId}>
        Arrow keys move between nodes · Enter or Space selects · Left / Right collapse and
        expand · + / − zoom · drag the background to pan
      </p>
      </details>
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
          <g className="jev-graph-bands" aria-hidden="true">
            {laneBands(layout, structure, nodeHeight, nodeWidth).map((band) => (
              <rect
                key={band.lane}
                className={`jev-graph-lane-band ${structure.toneOf.get(band.lane) ?? ""}`}
                data-lane={band.lane}
                x={band.left}
                y={band.top}
                width={band.right - band.left}
                height={band.bottom - band.top}
                rx={8}
              />
            ))}
          </g>
          <g className="jev-graph-edges" data-testid="jev-graph-edges">
            {layout.edges.map((edge) => (
              <path key={edge.id} className="jev-graph-edge" d={edge.path} />
            ))}
          </g>
          <g
            className={`jev-graph-activity${animate ? " jev-graph-activity-animated" : ""}`}
            data-testid="jev-graph-activity"
            aria-hidden="true"
          >
            {shownActivity.map((edge) => {
              const id = edgeId(edge.parent, edge.child);
              const flowing = animate && edge.kind === "current";
              const classes = [
                "jev-graph-edge-activity",
                `jev-graph-edge-${edge.kind}`,
                structure.toneOf.get(structure.laneOf.get(edge.parent) ?? "") ?? "",
              ];
              if (flowing) classes.push("jev-graph-edge-flow");
              if (!live) classes.push("jev-graph-edge-last-known");
              const fade =
                edge.kind === "recent"
                  ? 1 - 0.7 * Math.min(1, edge.ageSeconds / JEV_TRAIL_SECONDS)
                  : 1;
              const age = edge.ageSeconds.toFixed(1);
              const label =
                edge.kind === "current"
                  ? `current path ${edge.parent} → ${edge.child}${lastKnown}`
                  : `recent: ${edge.parent} → ${edge.child} recorded ${age} game s before ` +
                    `the latest state (event ${edge.sequence}); not current${lastKnown}`;
              return (
                <path
                  key={id}
                  className={classes.join(" ")}
                  d={edgePaths.get(id)}
                  style={{ opacity: live ? fade : fade * 0.6 }}
                  data-edge={id}
                  data-activity={edge.kind}
                  data-lane={structure.laneOf.get(edge.parent)}
                  data-age={edge.ageSeconds.toFixed(2)}
                  data-animated={flowing}
                >
                  <title>{label}</title>
                </path>
              );
            })}
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
              const lane = structure.laneOf.get(node.id) ?? node.id;
              const tone = structure.toneOf.get(lane) ?? "";
              const source = activity.sources.get(node.id);
              const name = summary === null ? statusText : `${statusText}, collapsed: ${summary}`;
              const classes = ["jev-graph-node", `jev-graph-node-${status}`];
              if (node.id === selectedNodeId) classes.push("jev-graph-node-selected");
              if (drawnLive && status === "active") classes.push("jev-graph-node-live");
              if (!live && isCurrentRuntime(status)) classes.push("jev-graph-node-last-known");
              const sourceLine = source === undefined ? null : `⚑ ${source.badge}${lastKnown}`;
              const thirdLine = summary ?? sourceLine;
              const title =
                `${node.id} — ${node.label} (lane ${lane})` +
                (summary === null ? "" : ` (${summary})`) +
                (source === undefined ? "" : ` — source: ${source.detail}`);
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
                    data-lane={lane}
                    data-source={source?.tone}
                    aria-label={`${node.id}, ${name}`}
                    aria-description={`lane ${lane}${sourceText(source)}`}
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
                    <title>{title}</title>
                    <rect width={nodeWidth} height={nodeHeight} rx={6} />
                    <rect
                      className={`jev-graph-node-lane ${tone}`}
                      width={LANE_STRIPE}
                      height={nodeHeight}
                      rx={2}
                    />
                    <text x={10} y={compact ? 21 : 15} className={compact ? "jev-graph-node-code" : "jev-graph-node-name"}>
                      {compact ? `${codes.get(node.id)} ${RUNTIME_GLYPHS[status]}` : shortId(node.id)}
                    </text>
                    {!compact && <text x={10} y={30} className="jev-graph-node-status">
                      {`${RUNTIME_GLYPHS[status]} ${statusText}`}
                    </text>}
                    {!compact && thirdLine !== null && (
                      <text
                        x={10}
                        y={44}
                        className={
                          summary === null
                            ? `jev-graph-node-source jev-source-${source?.tone ?? "unknown"}`
                            : "jev-graph-node-hidden"
                        }
                      >
                        {clip(thirdLine, SUMMARY_CHARS)}
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
                    transform={`translate(${x + nodeWidth},${y + nodeHeight / 2})`}
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
