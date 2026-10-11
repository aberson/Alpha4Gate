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

export type DotShape = "cone" | "sphere" | "hollow" | "faces";

/** Presentation only: retain IDs and ancestry while distributing branches in 3D. */
export function dotPositions(policy: JevPolicy, collapsed: ReadonlySet<string>, shape: DotShape): SpatialNode[] {
  const points = radialPositions(policy, collapsed);
  if (shape === "cone") return points;
  const groups: SpatialNode[][] = [];
  for (const point of points) {
    if (point.depth === 1) groups.push([]);
    groups[groups.length - 1].push(point);
  }
  let start = -Math.PI;
  return groups.flatMap((group, branch) => {
    // Sphere sectors grow with branch size, so Army is not squeezed into a fixed slot.
    const span = Math.PI * 2 * group.length / points.length;
    const levels = Math.max(...group.map((point) => point.depth));
    const result = group.map((point, index) => {
      const progress = (point.depth - 1) / Math.max(1, levels - 1);
      if (shape === "faces") {
        // One planar side of an N-sided prism per branch. Depth runs vertically;
        // siblings spread horizontally on that face, retaining their tree order.
        const sides = Math.max(3, groups.length);
        const angle = branch * Math.PI * 2 / sides;
        const atDepth = group.filter((other) => other.depth === point.depth);
        const rank = atDepth.indexOf(point);
        const u = ((rank + 0.5) / atDepth.length - 0.5) * 560 * Math.tan(Math.PI / sides);
        return { ...point, x: 300 * Math.cos(angle) - u * Math.sin(angle),
          y: (progress - 0.5) * 600, z: 300 * Math.sin(angle) + u * Math.cos(angle) };
      }
      // On the shell, depth travels from the upper cap toward the bottom and
      // siblings keep their tree order. This exposes paths instead of scattering
      // ancestors across the globe. Filled spheres retain volume distribution.
      const peers = group.filter((other) => other.depth === point.depth);
      const longitude = shape === "hollow"
        ? start + span * (peers.indexOf(point) + 0.5) / peers.length
        : start + span * (((index + 0.5) * 0.61803398875) % 1);
      const vertical = shape === "hollow"
        ? -Math.cos(Math.PI * (0.18 + progress * 0.7))
        : 1 - 2 * (index + 0.5) / group.length;
      const radius = shape === "hollow" ? 330 : 330 * (0.3 + 0.7 * progress);
      const ring = radius * Math.sqrt(1 - vertical * vertical);
      return { ...point, x: ring * Math.cos(longitude), y: radius * vertical,
        z: ring * Math.sin(longitude) };
    });
    start += span;
    return result;
  });
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
