import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react";
import { JevGraph } from "./JevGraph";
import type { JevGraphActivity, JevNodeSource } from "./jevEvidence";
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
  activity?: JevGraphActivity;
  reveal?: { nodeId: string; token: number } | null;
}

function graph({
  runtime = {},
  live = true,
  onSelectNode = () => {},
  activity,
  reveal = null,
}: RenderOptions = {}) {
  return (
    <JevGraph
      policy={POLICY}
      runtime={new Map(Object.entries(runtime))}
      live={live}
      selectedNodeId={null}
      onSelectNode={onSelectNode}
      activity={activity}
      reveal={reveal}
    />
  );
}

function renderGraph(options: RenderOptions = {}) {
  return render(graph(options));
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

/** A controllable ``prefers-reduced-motion`` answer for the graph's media query. */
function stubReducedMotion(reduce: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: reduce && query === "(prefers-reduced-motion: reduce)",
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

function activityPaths(): HTMLElement[] {
  return Array.from(
    screen.getByTestId("jev-graph-activity").querySelectorAll<HTMLElement>("path"),
  );
}

const ACTIVITY: JevGraphActivity = {
  edges: [
    { parent: "economy", child: "economy.build", kind: "current", ageSeconds: 0, sequence: null },
    { parent: "army", child: "army.attack", kind: "recent", ageSeconds: 1.5, sequence: 41 },
    // Recorded, but its child is hidden in a collapsed branch: nothing to draw it on.
    {
      parent: "economy.build",
      child: "economy.build.pylon",
      kind: "current",
      ageSeconds: 0,
      sequence: null,
    },
  ],
  sources: new Map(),
  followTargets: [],
};

describe("JevGraph lanes and observed activity (plan J2 Step 221)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("colours every node and band by its root lane and names the lanes active now", () => {
    renderGraph({ runtime: { economy: "active", "economy.check": "waiting", army: "active" } });
    expect(item("economy.check")).toHaveAttribute("data-lane", "economy");
    expect(item("army.attack")).toHaveAttribute("data-lane", "army");
    expect(item("economy.build").querySelector(".jev-graph-node-lane")).toHaveClass(
      "jev-lane-economy",
    );
    const bands = document.querySelectorAll(".jev-graph-lane-band");
    expect(Array.from(bands).map((band) => band.getAttribute("data-lane"))).toEqual([
      "economy",
      "army",
    ]);
    expect(bands[1]).toHaveClass("jev-lane-army");
    // Simultaneous lanes: both are listed, each with its own counts.
    expect(screen.getByTestId("jev-graph-active-lanes")).toHaveTextContent(
      "2 lane(s) active: economy, army",
    );
    const chips = screen.getAllByTestId("jev-graph-lane");
    expect(chips.map((chip) => chip.textContent)).toEqual([
      "economy lane · 1 active · 1 waiting",
      "army lane · 1 active · 0 waiting",
    ]);
    expect(item("economy")).toHaveAccessibleDescription("lane economy");
  });

  it("draws only the recorded edges: the current path solid, recent ones fading", () => {
    renderGraph({ runtime: { economy: "active", "economy.build": "active" }, activity: ACTIVITY });
    // The structural edges are unchanged; activity is drawn over them, never added to them.
    expect(screen.getByTestId("jev-graph-edges").querySelectorAll("path")).toHaveLength(3);
    const drawn = activityPaths();
    expect(drawn.map((path) => [path.dataset.edge, path.dataset.activity])).toEqual([
      ["economy>economy.build", "current"],
      ["army>army.attack", "recent"],
    ]);
    const [current, recent] = drawn;
    expect([current.dataset.lane, recent.dataset.lane]).toEqual(["economy", "army"]);
    expect(Number(recent.style.opacity)).toBeLessThan(Number(current.style.opacity));
    expect(recent.querySelector("title")).toHaveTextContent(
      "recent: army → army.attack recorded 1.5 game s before the latest state (event 41); " +
        "not current",
    );
    expect(screen.getByTestId("jev-graph-edge-legend")).toHaveTextContent(
      "current path (2): both ends run in the latest state",
    );
  });

  it("scopes activity counts to displayed roots while retaining collapsed branch evidence", () => {
    render(
      <JevGraph
        policy={POLICY}
        displayRootIds={["economy"]}
        runtime={new Map()}
        live
        selectedNodeId={null}
        onSelectNode={() => {}}
        activity={ACTIVITY}
      />,
    );
    expect(itemNames()).not.toContain("army");
    expect(screen.getByTestId("jev-graph-edge-legend")).toHaveTextContent("current path (2)");
    expect(screen.getByTestId("jev-graph-edge-legend")).toHaveTextContent("recent (0)");
  });

  it("animates the current path only while live, and never with reduced motion", () => {
    stubReducedMotion(false);
    const { rerender } = renderGraph({ activity: ACTIVITY });
    const current = () => activityPaths()[0];
    expect(screen.getByTestId("jev-graph")).toHaveAttribute("data-reduced-motion", "false");
    expect(current()).toHaveAttribute("data-animated", "true");

    rerender(graph({ activity: ACTIVITY, live: false })); // stale, offline or final
    expect(current()).toHaveAttribute("data-animated", "false");
    expect(current().querySelector("title")).toHaveTextContent(
      "current path economy → economy.build (last known)",
    );
    cleanup();

    stubReducedMotion(true);
    renderGraph({ activity: ACTIVITY });
    expect(screen.getByTestId("jev-graph")).toHaveAttribute("data-reduced-motion", "true");
    expect(current()).toHaveAttribute("data-animated", "false");
    expect(screen.getByTestId("jev-graph-edge-legend")).toHaveTextContent(
      "Reduced motion: nothing animates.",
    );
  });

  it("follows activity only when asked, only while live, and never selects anything", () => {
    const onSelectNode = vi.fn();
    const following: JevGraphActivity = { ...ACTIVITY, followTargets: ["economy.build.pylon"] };
    const { rerender } = renderGraph({ activity: following, onSelectNode });
    const toggleFollow = screen.getByTestId("jev-follow");
    expect(toggleFollow).not.toBeChecked(); // off by default: nothing moves
    expect(itemNames()).not.toContain("economy.build.pylon");
    expect(canvasTransform()).toBe("translate(0,0) scale(1)");
    expect(screen.getByTestId("jev-follow-status")).toHaveTextContent("off");

    fireEvent.click(toggleFollow);
    expect(itemNames()).toContain("economy.build.pylon"); // its branch was expanded
    expect(screen.getByTestId("jev-follow-status")).toHaveTextContent(
      "on: showing economy.build.pylon",
    );
    expect(onSelectNode).not.toHaveBeenCalled();

    // A manual pan stays while the target is unchanged (another poll, same activity).
    fireEvent.click(screen.getByRole("button", { name: "Pan right" }));
    const panned = canvasTransform();
    rerender(graph({ activity: { ...following }, onSelectNode }));
    expect(canvasTransform()).toBe(panned);

    // The followed node stays followed while it is still a target: no hopping.
    rerender(
      graph({
        activity: { ...following, followTargets: ["army.attack", "economy.build.pylon"] },
        onSelectNode,
      }),
    );
    expect(screen.getByTestId("jev-follow-status")).toHaveTextContent(
      "on: showing economy.build.pylon",
    );
    expect(canvasTransform()).toBe(panned);

    // Not live: following pauses and the view stays.
    rerender(
      graph({ activity: { ...following, followTargets: ["army.attack"] }, live: false }),
    );
    expect(screen.getByTestId("jev-follow-status")).toHaveTextContent(
      "paused: this state is not live",
    );
    expect(canvasTransform()).toBe(panned);
    expect(onSelectNode).not.toHaveBeenCalled();
  });

  it("brings a newly followed target into view with the smallest pan", () => {
    const { rerender } = renderGraph({ activity: ACTIVITY });
    fireEvent.click(screen.getByTestId("jev-follow"));
    expect(screen.getByTestId("jev-follow-status")).toHaveTextContent(
      "on: no active node to follow",
    );
    const panRight = screen.getByRole("button", { name: "Pan right" });
    for (let i = 0; i < 20; i += 1) fireEvent.click(panRight);
    const target = () => nodeBoxes()[itemNames().indexOf("army.attack")];
    expect(target().right).toBeLessThan(0); // panned far off to the left
    rerender(graph({ activity: { ...ACTIVITY, followTargets: ["army.attack"] } }));
    // Without a measured viewport (no ResizeObserver here) the graph assumes 760 x 520.
    const box = target();
    expect(box.left).toBeGreaterThanOrEqual(0);
    expect(box.right).toBeLessThanOrEqual(760);
    expect(box.top).toBeGreaterThanOrEqual(0);
    expect(box.bottom).toBeLessThanOrEqual(520);
    expect(box.left).toBeLessThan(40); // the smallest pan: just inside the left margin
    expect(screen.getByTestId("jev-follow-status")).toHaveTextContent("on: showing army.attack");
  });

  it("says when the lanes were active: live, last known, or when the run ended", () => {
    const runtime = { economy: "active" as const };
    const { rerender } = renderGraph({ runtime });
    const lanes = () => screen.getByTestId("jev-graph-active-lanes");
    expect(lanes()).toHaveTextContent("1 lane(s) active: economy");
    rerender(graph({ runtime, live: false }));
    expect(lanes()).toHaveTextContent("1 lane(s) active (last known): economy");
    rerender(
      <JevGraph
        policy={POLICY}
        runtime={new Map(Object.entries(runtime))}
        live={false}
        ended
        selectedNodeId={null}
        onSelectNode={() => {}}
      />,
    );
    expect(lanes()).toHaveTextContent("1 lane(s) active when the run ended: economy");
  });

  it("names the decision a node's newest recorded event carries", () => {
    const source: JevNodeSource = {
      tone: "hosted",
      badge: "Typesafe req #7",
      detail: "command created under production_mix = balanced: Typesafe (request 7)",
      sequence: 12,
    };
    renderGraph({
      activity: { ...ACTIVITY, sources: new Map([["army.attack", source]]) },
      live: false,
    });
    const attack = item("army.attack");
    expect(attack).toHaveAttribute("data-source", "hosted");
    expect(within(attack).getByText("⚑ Typesafe req #7 (last known)")).toBeInTheDocument();
    expect(attack).toHaveAccessibleDescription(
      "lane army, source: command created under production_mix = balanced: Typesafe (request 7)",
    );
    expect(item("army")).not.toHaveAttribute("data-source");
  });

  it("reveals a hidden node once per request, expanding its branch", () => {
    const { rerender } = renderGraph();
    expect(itemNames()).not.toContain("economy.build.wait");
    rerender(graph({ reveal: { nodeId: "economy.build.wait", token: 1 } }));
    expect(itemNames()).toContain("economy.build.wait");
    fireEvent.click(screen.getByRole("button", { name: "Collapse economy.build" }));
    rerender(graph({ reveal: { nodeId: "economy.build.wait", token: 1 } })); // same request
    expect(itemNames()).not.toContain("economy.build.wait");
    rerender(graph({ reveal: { nodeId: "economy.build.wait", token: 2 } }));
    expect(itemNames()).toContain("economy.build.wait");
  });
});
