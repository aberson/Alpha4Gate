import { describe, it, expect } from "vitest";
import { deepDiff } from "./deepDiff";

describe("deepDiff", () => {
  it("empty objects produce all-empty diffs", () => {
    const out = deepDiff({}, {});
    expect(out.added).toEqual({});
    expect(out.removed).toEqual({});
    expect(out.modified).toEqual({});
    expect(out.unchanged).toEqual({});
  });

  it("identical objects produce all-unchanged", () => {
    const a = { x: 1, y: "two", z: true };
    const b = { x: 1, y: "two", z: true };
    const out = deepDiff(a, b);
    expect(out.added).toEqual({});
    expect(out.removed).toEqual({});
    expect(out.modified).toEqual({});
    expect(out.unchanged).toEqual({ x: 1, y: "two", z: true });
  });

  it("identical primitive values at the same key go into unchanged", () => {
    const out = deepDiff({ x: 1 }, { x: 1 });
    expect(out.unchanged).toEqual({ x: 1 });
    expect(out.modified).toEqual({});
  });

  it("single addition", () => {
    const out = deepDiff({ x: 1 }, { x: 1, y: 2 });
    expect(out.added).toEqual({ y: 2 });
    expect(out.removed).toEqual({});
    expect(out.modified).toEqual({});
    expect(out.unchanged).toEqual({ x: 1 });
  });

  it("single removal", () => {
    const out = deepDiff({ x: 1, y: 2 }, { x: 1 });
    expect(out.removed).toEqual({ y: 2 });
    expect(out.added).toEqual({});
    expect(out.modified).toEqual({});
    expect(out.unchanged).toEqual({ x: 1 });
  });

  it("single modification", () => {
    const out = deepDiff({ x: 1 }, { x: 2 });
    expect(out.modified).toEqual({ x: { a: 1, b: 2 } });
    expect(out.added).toEqual({});
    expect(out.removed).toEqual({});
    expect(out.unchanged).toEqual({});
  });

  it("nested object diff descends into sub-keys with dotted paths", () => {
    const a = {
      hp: { lr: 0.001, batch_size: 64 },
      shared: 1,
    };
    const b = {
      hp: { lr: 0.0005, batch_size: 64, gamma: 0.99 },
      shared: 1,
    };
    const out = deepDiff(a, b);
    expect(out.modified).toEqual({ "hp.lr": { a: 0.001, b: 0.0005 } });
    expect(out.added).toEqual({ "hp.gamma": 0.99 });
    expect(out.removed).toEqual({});
    expect(out.unchanged).toEqual({
      "hp.batch_size": 64,
      shared: 1,
    });
  });

  it("treats arrays as values, NOT as deep-diff candidates", () => {
    // Array equal contents → unchanged
    const same = deepDiff({ layers: [128, 128] }, { layers: [128, 128] });
    expect(same.unchanged).toEqual({ layers: [128, 128] });
    expect(same.modified).toEqual({});

    // Array differs → single modified entry, not per-index
    const diff = deepDiff({ layers: [128, 128] }, { layers: [256, 128] });
    expect(diff.modified).toEqual({
      layers: { a: [128, 128], b: [256, 128] },
    });
    // No nested keys like "layers.0" should appear.
    expect(Object.keys(diff.unchanged)).toEqual([]);
  });

  it("handles mixed object→primitive at same key as modified", () => {
    const out = deepDiff({ k: { x: 1 } }, { k: 5 });
    // The key flips from object to primitive; we report it as a single
    // modified entry, not as added/removed children.
    expect(out.modified).toEqual({ k: { a: { x: 1 }, b: 5 } });
  });

  it("handles primitive→object at same key as modified", () => {
    const out = deepDiff({ k: 5 }, { k: { x: 1 } });
    expect(out.modified).toEqual({ k: { a: 5, b: { x: 1 } } });
  });

  it("treats null as a value, not absent", () => {
    const out = deepDiff({ k: null }, { k: 1 });
    expect(out.modified).toEqual({ k: { a: null, b: 1 } });

    const same = deepDiff({ k: null }, { k: null });
    expect(same.unchanged).toEqual({ k: null });
  });

  it("reward-rules-shaped diff (typical Step 7 use)", () => {
    const a = {
      base_step_reward: { enabled: true, weight: 1.0 },
      shield_battery: { enabled: false, weight: 0.5 },
      old_rule: { enabled: true, weight: 2.0 },
    };
    const b = {
      base_step_reward: { enabled: true, weight: 1.0 },
      shield_battery: { enabled: true, weight: 0.5 },
      new_rule: { enabled: true, weight: 3.0, condition: "supply > 100" },
    };
    const out = deepDiff(a, b);
    // In-place modifications recurse into the rule's sub-keys.
    expect(out.modified).toEqual({
      "shield_battery.enabled": { a: false, b: true },
    });
    // A wholly added or removed rule reports as a single subtree entry —
    // the renderer treats "added rule" as one row, not one row per field.
    expect(out.added).toEqual({
      new_rule: { enabled: true, weight: 3.0, condition: "supply > 100" },
    });
    expect(out.removed).toEqual({
      old_rule: { enabled: true, weight: 2.0 },
    });
    expect(out.unchanged).toEqual({
      "base_step_reward.enabled": true,
      "base_step_reward.weight": 1.0,
      "shield_battery.weight": 0.5,
    });
  });
});
