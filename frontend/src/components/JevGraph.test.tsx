import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react";
import { JevGraph } from "./JevGraph";
import type { JevNodeRuntime, JevPolicy, JevPolicyNode } from "../types/jev";

/**
 * JevGraph tests: structure, branch collapse, keyboard selection and
 * navigation, textual status, and bounded pan/zoom. The graph receives an
 * already-validated policy, so a small hand-made forest keeps each behavior
 * visible:
 *
 *   economy (sequence)          army (selector)
 *   ├─ economy.check            └─ army.attack
 *   └─ economy.build (selector)
 *      ├─ economy.build.pylon
 *      └─ economy.build.wait
 */

function node(
  id: string,
  kind: JevPolicyNode["kind"],
  children: string[] = [],
): JevPolicyNode {
  return {
    id,
    label: `Label of ${id}`,
    kind,
    children,
    operation: children.length > 0 ? null : "count_compare",
    args: {},
  };
}

const POLICY: JevPolicy = {
  family: "jev",
  version: 1,
  roots: ["economy", "army"],
  parameters: {},
  policy_hash: "c".repeat(64),
  nodes: [
    node("economy", "sequence", ["economy.check", "economy.build"]),
    node("economy.check", "condition"),
    node("economy.build", "selector", ["economy.build.pylon", "economy.build.wait"]),
    node("economy.build.pylon", "action"),
    node("economy.build.wait", "wait"),
    node("army", "selector", ["army.attack"]),
    node("army.attack", "action"),
  ],
};

interface RenderOptions {
  runtime?: Record<string, JevNodeRuntime>;
  live?: boolean;
  onSelectNode?: (id: string) => void;
}

function renderGraph({ runtime = {}, live = true, onSelectNode = () => {} }: RenderOptions = {}) {
  return render(
    <JevGraph
      policy={POLICY}
      runtime={new Map(Object.entries(runtime))}
      live={live}
      selectedNodeId={null}
      onSelectNode={onSelectNode}
    />,
  );
}

function item(id: string): HTMLElement {
  return screen.getByRole("treeitem", { name: new RegExp(`^${id.replace(/\./g, "\\.")},`) });
}

function itemNames(): string[] {
  return screen.getAllByRole("treeitem").map((el) => el.getAttribute("aria-label")?.split(",")[0] ?? "");
}

function canvasTransform(): string | null {
  return screen.getByTestId("jev-graph-canvas").getAttribute("transform");
}

function numbers(text: string | null | undefined): number[] {
  return (text ?? "").match(/-?\d+(\.\d+)?/g)?.map(Number) ?? [];
}

/** Each visible node's box in viewport pixels, from the drawn transforms and node rects. */
function nodeBoxes(): Array<{ left: number; top: number; right: number; bottom: number }> {
  const [tx, ty, k] = numbers(canvasTransform());
  return screen.getAllByRole("treeitem").map((element) => {
    const [x, y] = numbers(element.parentElement?.getAttribute("transform"));
    const rect = element.querySelector("rect");
    const width = Number(rect?.getAttribute("width"));
    const height = Number(rect?.getAttribute("height"));
    return {
      left: tx + x * k,
      top: ty + y * k,
      right: tx + (x + width) * k,
      bottom: ty + (y + height) * k,
    };
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

describe("JevGraph", () => {
  it("draws the visible policy forest with only structural edges", () => {
    renderGraph();
    expect(itemNames()).toEqual(["economy", "economy.check", "economy.build", "army", "army.attack"]);
    // economy→check, economy→build, army→attack: no edge to the undrawn display root.
    expect(screen.getByTestId("jev-graph-edges").querySelectorAll("path")).toHaveLength(3);
  });

  it("starts branches below the roots collapsed and summarizes their hidden runtime", () => {
    renderGraph({ runtime: { "economy.build.pylon": "active", "economy.build.wait": "waiting" } });
    const build = item("economy.build");
    expect(build).toHaveAttribute("aria-expanded", "false");
    expect(build).toHaveAccessibleName(/collapsed: 2 hidden · 1 active · 1 waiting$/);
  });

  it("expands and collapses a branch with its toggle", () => {
    renderGraph();
    fireEvent.click(screen.getByRole("button", { name: "Expand economy.build" }));
    expect(itemNames()).toContain("economy.build.wait");
    fireEvent.click(screen.getByRole("button", { name: "Collapse economy" }));
    expect(itemNames()).toEqual(["economy", "army", "army.attack"]);
  });

  it("selects a node with Enter, Space or a click", () => {
    const onSelectNode = vi.fn();
    renderGraph({ onSelectNode });
    fireEvent.keyDown(item("economy.check"), { key: "Enter" });
    fireEvent.keyDown(item("army"), { key: " " });
    fireEvent.click(item("army.attack"));
    expect(onSelectNode.mock.calls).toEqual([["economy.check"], ["army"], ["army.attack"]]);
  });

  it("moves focus with the arrow keys and collapses or expands with Left and Right", () => {
    renderGraph();
    const economy = item("economy");
    expect(economy).toHaveAttribute("tabindex", "0");
    economy.focus();
    fireEvent.keyDown(economy, { key: "ArrowDown" });
    expect(item("economy.check")).toHaveFocus();
    fireEvent.keyDown(item("economy.check"), { key: "ArrowLeft" });
    expect(economy).toHaveFocus();
    fireEvent.keyDown(item("economy.build"), { key: "ArrowRight" });
    expect(item("economy.build")).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(item("economy.build"), { key: "ArrowLeft" });
    expect(item("economy.build")).toHaveAttribute("aria-expanded", "false");
    expect(screen.getAllByRole("treeitem").filter((el) => el.tabIndex === 0)).toHaveLength(1);
  });

  it("words failed and unevaluated nodes and shows each status as visible text", () => {
    renderGraph({ runtime: { economy: "active", army: "failure" } });
    expect(item("army")).toHaveAccessibleName("army, selector · last: failure");
    expect(item("army.attack")).toHaveAccessibleName("army.attack, action · no recent evaluation");
    expect(within(item("economy")).getByText("▶ sequence · active")).toBeInTheDocument();
  });

  it.each([
    [true, "", "true"],
    [false, " (last known)", "false"],
  ])("with live=%s labels active and waiting nodes%s", (live, suffix, drawnLive) => {
    renderGraph({
      runtime: { economy: "active", army: "waiting", "economy.build.pylon": "active" },
      live,
    });
    expect(item("economy")).toHaveAccessibleName(`economy, sequence · active${suffix}`);
    expect(item("army")).toHaveAccessibleName(`army, selector · waiting${suffix}`);
    expect(item("economy.build")).toHaveAccessibleName(
      `economy.build, selector · no recent evaluation, collapsed: 2 hidden · 1 active${suffix}`,
    );
    expect(item("economy")).toHaveAttribute("data-live", drawnLive);
    expect(item("army")).toHaveAttribute("data-live", drawnLive);
  });

  it("zooms with the buttons, the wheel and the keyboard within fixed bounds", () => {
    renderGraph();
    const zoomIn = screen.getByRole("button", { name: "Zoom in" });
    fireEvent.click(zoomIn);
    expect(screen.getByTestId("jev-graph-zoom")).toHaveTextContent("Zoom 125%");
    for (let i = 0; i < 10; i += 1) fireEvent.click(zoomIn);
    expect(screen.getByTestId("jev-graph-zoom")).toHaveTextContent("Zoom 250%");
    expect(zoomIn).toBeDisabled();
    const viewport = screen.getByLabelText("Policy graph");
    for (let i = 0; i < 20; i += 1) fireEvent.wheel(viewport, { deltaY: 100 });
    expect(screen.getByTestId("jev-graph-zoom")).toHaveTextContent("Zoom 25%");
    expect(screen.getByRole("button", { name: "Zoom out" })).toBeDisabled();
    fireEvent.keyDown(viewport, { key: "+" });
    expect(screen.getByTestId("jev-graph-zoom")).toHaveTextContent("Zoom 31%");
  });

  it("pans by dragging the background and with the pan buttons", () => {
    renderGraph();
    const background = screen.getByTestId("jev-graph-background");
    fireEvent.pointerDown(background, { pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(background, { pointerId: 1, clientX: 140, clientY: 70 });
    fireEvent.pointerUp(background, { pointerId: 1, clientX: 140, clientY: 70 });
    expect(canvasTransform()).toBe("translate(40,-30) scale(1)");
    fireEvent.pointerMove(background, { pointerId: 1, clientX: 400, clientY: 400 });
    expect(canvasTransform()).toBe("translate(40,-30) scale(1)");
    fireEvent.click(screen.getByRole("button", { name: "Pan right" }));
    expect(canvasTransform()).toBe("translate(-40,-30) scale(1)");
  });

  it("fits every node inside the viewport and resets the view", () => {
    const viewport = { width: 600, height: 300 };
    vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(viewport.width);
    vi.spyOn(Element.prototype, "clientHeight", "get").mockReturnValue(viewport.height);
    const inside = (box: ReturnType<typeof nodeBoxes>[number]) =>
      box.left >= 0 && box.top >= 0 && box.right <= viewport.width && box.bottom <= viewport.height;
    renderGraph();
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    expect(nodeBoxes().every(inside)).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Fit" }));
    const boxes = nodeBoxes();
    expect(boxes.every(inside)).toBe(true);
    // A fit, not an arbitrary shrink: the forest spans most of the width.
    const span = Math.max(...boxes.map((b) => b.right)) - Math.min(...boxes.map((b) => b.left));
    expect(span).toBeGreaterThan(viewport.width / 2);

    fireEvent.click(screen.getByRole("button", { name: "Reset view" }));
    expect(canvasTransform()).toBe("translate(0,0) scale(1)");
  });
});
