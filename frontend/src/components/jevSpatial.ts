import type { JevPolicy } from "../types/jev";

export interface SpatialNode {
  id: string;
  x: number;
  y: number;
  z: number;
  depth: number;
  siblings: number;
  position: number;
}

/** Leaf-weighted sectors: a growing branch gets more room automatically.
 * Coordinates depend only on the archived structure, never on polling/activity.
 * The forest has an implicit center, but no invented node or connecting edge.
 */
export function radialPositions(policy: JevPolicy, collapsed: ReadonlySet<string>): SpatialNode[] {
  const nodes = new Map(policy.nodes.map((node) => [node.id, node]));
  const weights = new Map<string, number>();
  const heights = new Map<string, number>();
  const children = (id: string) => collapsed.has(id) ? [] : nodes.get(id)?.children ?? [];
  const weight = (id: string): number => {
    const cached = weights.get(id);
    if (cached !== undefined) return cached;
    const result = Math.max(1, children(id).reduce((sum, child) => sum + weight(child), 0));
    weights.set(id, result);
    return result;
  };
  const result: SpatialNode[] = [];
  const height = (id: string): number => {
    const cached = heights.get(id);
    if (cached !== undefined) return cached;
    const result = 1 + Math.max(0, ...children(id).map(height));
    heights.set(id, result);
    return result;
  };
  const visit = (ids: string[], start: number, span: number, depth: number, levels = 1, reach = 0) => {
    const total = ids.reduce((sum, id) => sum + weight(id), 0);
    let cursor = start;
    ids.forEach((id, index) => {
      const sector = span * weight(id) / total;
      const angle = cursor + sector / 2;
      const branchLevels = depth === 1 ? height(id) : levels;
      const branchReach = depth === 1 ? 200 + Math.sqrt(weight(id)) * 55 : reach;
      const progress = (depth - 1) / Math.max(1, branchLevels - 1);
      const radius = 72 + progress * branchReach;
      result.push({
        id, x: radius * Math.cos(angle), y: radius * Math.sin(angle),
        // Depth fans into a cone. Rotation exposes hierarchy and separates crossings.
        z: progress * 300, depth, siblings: ids.length, position: index + 1,
      });
      visit(children(id), cursor, sector, depth + 1, branchLevels, branchReach);
      cursor += sector;
    });
  };
  visit(policy.roots, -Math.PI / 2, Math.PI * 2, 1);
  return result;
}

/** Orthographic camera: real XYZ rotation, stable scale, no forced auto-orbit. */
export function projectSpatial(node: SpatialNode, yaw: number, pitch: number) {
  const x = node.x * Math.cos(yaw) + node.z * Math.sin(yaw);
  const z = -node.x * Math.sin(yaw) + node.z * Math.cos(yaw);
  return {
    x,
    y: node.y * Math.cos(pitch) - z * Math.sin(pitch),
    depth: node.y * Math.sin(pitch) + z * Math.cos(pitch),
  };
}
