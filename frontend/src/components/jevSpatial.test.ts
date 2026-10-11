import { describe, expect, it } from "vitest";
import { radialPositions, dotPositions, projectSpatial } from "./jevSpatial";
import type { JevPolicy } from "../types/jev";

const node = (id: string, children: string[] = []) => ({
  id, children, label: id, kind: children.length ? "sequence" as const : "action" as const,
  operation: null, args: {},
});
const policy: JevPolicy = {
  family: "jev", version: 2, policy_hash: "a".repeat(64), parameters: {},
  roots: ["economy", "army"],
  nodes: [node("economy", ["e"]), node("e"), node("army", ["a", "b", "c"]), node("a"), node("b"), node("c")],
};

describe("spatial policy layout", () => {
  it("puts hollow-sphere nodes on the shell and sphere roots inside it", () => {
    const hollow = dotPositions(policy, new Set(), "hollow");
    for (const p of hollow) expect(Math.hypot(p.x, p.y, p.z)).toBeCloseTo(330);
    const sphere = dotPositions(policy, new Set(), "sphere");
    expect(Math.hypot(sphere[0].x, sphere[0].y, sphere[0].z)).toBeLessThan(330);
    expect(new Set(hollow.map((p) => `${p.x},${p.y},${p.z}`)).size).toBe(hollow.length);
    expect(dotPositions(policy, new Set(["army"]), "sphere").map((p) => p.id))
      .toEqual(["economy", "e", "army"]);
    const army = hollow.find((p) => p.id === "army")!;
    expect(hollow.find((p) => p.id === "a")!.y).toBeGreaterThan(army.y);
  });

  it("places each branch on its own prism face without changing ancestry", () => {
    const points = dotPositions(policy, new Set(), "faces");
    let branch = -1;
    for (const p of points) {
      if (p.depth === 1) branch++;
      const angle = branch * Math.PI * 2 / 3;
      expect(p.x * Math.cos(angle) + p.z * Math.sin(angle)).toBeCloseTo(300);
    }
    expect(points.map((p) => p.id)).toEqual(radialPositions(policy, new Set()).map((p) => p.id));
  });
  it("allocates a larger sector to a larger branch, with stable coordinates", () => {
    const points = radialPositions(policy, new Set());
    expect(points.map((p) => p.id)).toEqual(["economy", "e", "army", "a", "b", "c"]);
    const root = points.find((p) => p.id === "economy")!;
    expect(Math.atan2(root.y, root.x)).toBeCloseTo(-Math.PI / 4); // one of four leaves
    expect(radialPositions(policy, new Set())).toEqual(points);
    expect(new Set(points.map((p) => `${p.x},${p.y},${p.z}`)).size).toBe(points.length);
  });

  it("only includes visible descendants and preserves the full-policy structure", () => {
    const snapshot = JSON.stringify(policy);
    expect(radialPositions(policy, new Set(["army"])).map((p) => p.id)).toEqual(["economy", "e", "army"]);
    expect(radialPositions({ ...policy, roots: ["army"] }, new Set()).map((p) => p.id)).toEqual(["army", "a", "b", "c"]);
    expect(JSON.stringify(policy)).toBe(snapshot);
  });

  it("projects true XYZ rotation and keeps coordinates finite at extreme angles", () => {
    const point = { id: "a", x: 20, y: 10, z: 50, depth: 1, siblings: 1, position: 1 };
    expect(projectSpatial(point, 0, 0)).toEqual({ x: 20, y: 10, depth: 50 });
    expect(projectSpatial(point, Math.PI / 2, 0).x).toBeCloseTo(50);
    expect(projectSpatial(point, 0, Math.PI / 2).y).toBeCloseTo(-50);
  });
});
