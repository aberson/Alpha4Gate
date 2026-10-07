import { hierarchy, tree as d3tree } from "d3-hierarchy";
import type { HierarchyPointNode } from "d3-hierarchy";
import type {
  LineageDAG,
  LineageEdge,
  LineageNode,
} from "../types/lineage";

/**
 * Tree layout for the Lineage view (``LineageView``'s Tree mode).
 *
 * Lives in its own non-component module so ``LineageView.tsx`` exports
 * only components (react-refresh fast-refresh boundary). ``NODE_RADIUS``
 * is shared: the layout uses it for the outer margin and the renderer
 * uses it for the node circle radius — one source of truth.
 */

export const NODE_RADIUS = 18;
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

export interface LayoutResult {
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
