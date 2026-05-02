import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  cleanup,
  fireEvent,
} from "@testing-library/react";
import {
  LineageView,
  computeTreeLayout,
  type ImprovementsResponse,
  type UnifiedImprovement,
} from "./LineageView";
import type { LineageDAG } from "../types/lineage";

/**
 * LineageView tests — Step 4 of the Models-tab build plan.
 *
 * Two render modes share one shell:
 *   - Tree (default) — renders 11 nodes + 10 edges via d3-hierarchy.
 *     Click → ``onNodeSelect`` callback fires with the version string.
 *   - Timeline — port of the legacy ImprovementsTab table.
 *
 * The d3-hierarchy layout is deterministic in jsdom for fixed inputs,
 * but to keep tests independent of d3 internals we inject a synthetic
 * ``layoutFn`` for click + colour assertions. ``computeTreeLayout`` is
 * exercised separately on the eleven-version fixture so it is tested
 * end-to-end without coupling DOM tests to layout precision.
 */

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

const ELEVEN_NODE_LINEAGE: LineageDAG = {
  nodes: [
    { id: "v0", version: "v0", race: "protoss", harness_origin: "manual", parent: null },
    { id: "v1", version: "v1", race: "protoss", harness_origin: "evolve", parent: "v0" },
    { id: "v2", version: "v2", race: "protoss", harness_origin: "evolve", parent: "v1" },
    { id: "v3", version: "v3", race: "protoss", harness_origin: "advised", parent: "v2" },
    { id: "v4", version: "v4", race: "protoss", harness_origin: "evolve", parent: "v3" },
    { id: "v5", version: "v5", race: "protoss", harness_origin: "evolve", parent: "v4" },
    { id: "v6", version: "v6", race: "protoss", harness_origin: "evolve", parent: "v5" },
    { id: "v7", version: "v7", race: "protoss", harness_origin: "evolve", parent: "v6" },
    { id: "v8", version: "v8", race: "protoss", harness_origin: "manual", parent: "v7" },
    { id: "v9", version: "v9", race: "protoss", harness_origin: "self-play", parent: "v8" },
    { id: "v10", version: "v10", race: "protoss", harness_origin: "manual", parent: "v9" },
  ],
  edges: [
    { from: "v0", to: "v1", harness: "evolve", improvement_title: "Splash readiness", ts: "2026-03-15T00:00:00Z", outcome: "promoted" },
    { from: "v1", to: "v2", harness: "evolve", improvement_title: "Shield battery", ts: "2026-03-22T00:00:00Z", outcome: "promoted" },
    { from: "v2", to: "v3", harness: "advised", improvement_title: "Anti-float", ts: "2026-04-15T00:00:00Z", outcome: "promoted" },
    { from: "v3", to: "v4", harness: "evolve", improvement_title: "DEFEND timeout", ts: "2026-04-29T00:00:00Z", outcome: "promoted" },
    { from: "v4", to: "v5", harness: "evolve", improvement_title: "Splash 2", ts: "2026-04-30T00:00:00Z", outcome: "promoted" },
    { from: "v5", to: "v6", harness: "evolve", improvement_title: "Observer", ts: "2026-04-30T01:00:00Z", outcome: "promoted" },
    { from: "v6", to: "v7", harness: "evolve", improvement_title: "Chrono Boost", ts: "2026-04-30T02:00:00Z", outcome: "promoted" },
    { from: "v7", to: "v8", harness: "manual", improvement_title: "manual", ts: "2026-04-30T03:00:00Z", outcome: "promoted" },
    { from: "v8", to: "v9", harness: "self-play", improvement_title: "—", ts: "2026-04-30T04:00:00Z", outcome: "promoted" },
    { from: "v9", to: "v10", harness: "manual", improvement_title: "manual", ts: "2026-04-30T05:00:00Z", outcome: "promoted" },
  ],
};

const ADVISED_ENTRY: UnifiedImprovement = {
  id: "advised-20260412-2007-iter1",
  source: "advised",
  timestamp: "2026-04-12T20:50:00Z",
  title: "Stronger mineral floating penalties",
  description: "Add a sharper per-step penalty for mineral floats.",
  type: "training",
  outcome: "promoted",
  metric: "7/10 wins (validation)",
  principles: ["§4.2 Resource Spending"],
  files_changed: ["data/reward_rules.json"],
};

const EVOLVE_ENTRY: UnifiedImprovement = {
  id: "evolve-gen2-cand_2e57ef46",
  source: "evolve",
  timestamp: "2026-04-29T21:34:32Z",
  title: "Gas-dump warp priority",
  description: "Switch warp queue to gas units when vespene > 600.",
  type: "dev",
  outcome: "fitness-pass",
  metric: "3-2 vs v3",
  principles: ["4.2"],
  files_changed: ["bots/v3/bot.py"],
};

const DISCARDED_EVOLVE_ENTRY: UnifiedImprovement = {
  id: "evolve-gen3-cand_deadbeef",
  source: "evolve",
  timestamp: "2026-04-29T22:10:00Z",
  title: "Aggressive proxy pylon cheese",
  description: "Build a forward pylon at the natural before any Gateway.",
  type: "dev",
  outcome: "discarded",
  metric: "0-5 vs v3",
  principles: ["§7"],
  files_changed: [
    "bots/v3/bot.py",
    "bots/v3/strategy.py",
    "bots/v3/build_orders.py",
  ],
};

const POPULATED_IMPROVEMENTS: ImprovementsResponse = {
  improvements: [EVOLVE_ENTRY, ADVISED_ENTRY],
};

// Fixture matching the legacy ImprovementsTab tests — a 3-entry payload
// with one promoted (advised), one fitness-pass (evolve), and one
// discarded (evolve) so badge-class + filter-pill assertions can
// distinguish all source/outcome categories.
const POPULATED_LEGACY: ImprovementsResponse = {
  improvements: [DISCARDED_EVOLVE_ENTRY, EVOLVE_ENTRY, ADVISED_ENTRY],
};

function makeFetchMock(opts: {
  lineage?: LineageDAG | null;
  improvements?: ImprovementsResponse;
}) {
  return vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/lineage")) {
      return jsonResponse(opts.lineage ?? { nodes: [], edges: [] });
    }
    if (url.includes("/api/improvements/unified")) {
      return jsonResponse(opts.improvements ?? { improvements: [] });
    }
    throw new Error(`Unexpected fetch: ${url}`);
  });
}

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(
    makeFetchMock({ lineage: { nodes: [], edges: [] } }),
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("LineageView — tree mode", () => {
  it("renders 11 nodes and 10 edges from the lineage DAG", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({ lineage: ELEVEN_NODE_LINEAGE }),
    );
    render(<LineageView onNodeSelect={vi.fn()} />);

    // Wait for the SVG to materialise (post /api/lineage fetch).
    await screen.findByTestId("lineage-tree-svg");

    // 11 nodes — one focusable <g> per version.
    for (const v of [
      "v0", "v1", "v2", "v3", "v4", "v5",
      "v6", "v7", "v8", "v9", "v10",
    ]) {
      expect(screen.getByTestId(`lineage-tree-node-${v}`)).toBeInTheDocument();
    }

    // 10 promoted edges — one <line> per parent → child link.
    const edgePairs: ReadonlyArray<[string, string]> = [
      ["v0", "v1"],
      ["v1", "v2"],
      ["v2", "v3"],
      ["v3", "v4"],
      ["v4", "v5"],
      ["v5", "v6"],
      ["v6", "v7"],
      ["v7", "v8"],
      ["v8", "v9"],
      ["v9", "v10"],
    ];
    for (const [from, to] of edgePairs) {
      expect(
        screen.getByTestId(`lineage-tree-edge-${from}-${to}`),
      ).toBeInTheDocument();
    }
  });

  it("colours nodes by harness origin", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({ lineage: ELEVEN_NODE_LINEAGE }),
    );
    render(<LineageView onNodeSelect={vi.fn()} />);
    await screen.findByTestId("lineage-tree-svg");

    expect(
      screen.getByTestId("lineage-tree-node-v0"),
    ).toHaveAttribute("data-harness", "manual");
    expect(
      screen.getByTestId("lineage-tree-node-v1"),
    ).toHaveAttribute("data-harness", "evolve");
    expect(
      screen.getByTestId("lineage-tree-node-v3"),
    ).toHaveAttribute("data-harness", "advised");
    expect(
      screen.getByTestId("lineage-tree-node-v9"),
    ).toHaveAttribute("data-harness", "self-play");
  });

  it("clicking a tree node fires onNodeSelect with the version name", async () => {
    // Use the layoutFn injection so this test never depends on d3
    // measuring DOM in jsdom — the layout returns three trivial
    // positioned nodes covering each click path.
    const onSelect = vi.fn();
    const stubLayout = (dag: LineageDAG) => ({
      nodes: dag.nodes.map((n, i) => ({ id: n.id, data: n, x: i * 50, y: 0 })),
      edges: [],
      width: 500,
      height: 100,
    });
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({ lineage: ELEVEN_NODE_LINEAGE }),
    );
    render(<LineageView onNodeSelect={onSelect} layoutFn={stubLayout} />);

    const v3Node = await screen.findByTestId("lineage-tree-node-v3");
    fireEvent.click(v3Node);

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith("v3");
  });

  it("Enter and Space keys on a focused node fire onNodeSelect", async () => {
    const onSelect = vi.fn();
    const stubLayout = (dag: LineageDAG) => ({
      nodes: dag.nodes.map((n, i) => ({ id: n.id, data: n, x: i * 50, y: 0 })),
      edges: [],
      width: 500,
      height: 100,
    });
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({ lineage: ELEVEN_NODE_LINEAGE }),
    );
    render(<LineageView onNodeSelect={onSelect} layoutFn={stubLayout} />);

    const v5Node = await screen.findByTestId("lineage-tree-node-v5");
    // SVG <g> elements expose tabIndex via the DOM property, not as
    // a string-valued attribute (React translates ``tabIndex={0}`` to
    // ``tabindex="0"``-the-attribute on HTML, but on SVG some
    // jsdom/React combinations only set the property). Assert on the
    // property instead so the test stays portable.
    expect((v5Node as unknown as { tabIndex: number }).tabIndex).toBe(0);
    fireEvent.keyDown(v5Node, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("v5");
    fireEvent.keyDown(v5Node, { key: " " });
    expect(onSelect).toHaveBeenCalledTimes(2);
  });

  it("renders a placeholder when the lineage DAG is empty", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({ lineage: { nodes: [], edges: [] } }),
    );
    render(<LineageView onNodeSelect={vi.fn()} />);
    await screen.findByTestId("lineage-empty-tree");
    expect(
      screen.getByTestId("lineage-empty-tree"),
    ).toHaveTextContent("No lineage yet");
    // No SVG when empty.
    expect(screen.queryByTestId("lineage-tree-svg")).not.toBeInTheDocument();
  });
});

describe("LineageView — timeline mode", () => {
  it("renders the unified improvements table", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        lineage: ELEVEN_NODE_LINEAGE,
        improvements: POPULATED_IMPROVEMENTS,
      }),
    );
    render(<LineageView onNodeSelect={vi.fn()} />);

    fireEvent.click(await screen.findByTestId("lineage-mode-timeline"));

    // Timeline mode active.
    await screen.findByTestId("lineage-timeline");

    // Both rows render.
    expect(
      screen.getByTestId(`improvements-row-${EVOLVE_ENTRY.id}`),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId(`improvements-row-${ADVISED_ENTRY.id}`),
    ).toBeInTheDocument();

    // Source badges rendered for both sources.
    expect(screen.getAllByTestId(/^source-badge-/).length).toBeGreaterThan(0);

    // Click a row to expand it (verifies the legacy ImprovementsTab
    // expand-row interaction is preserved verbatim).
    fireEvent.click(screen.getByTestId(`improvements-row-${EVOLVE_ENTRY.id}`));
    expect(
      screen.getByTestId(`improvements-row-expanded-${EVOLVE_ENTRY.id}`),
    ).toBeInTheDocument();
  });

  it("shows the empty placeholder when no improvements exist", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        lineage: ELEVEN_NODE_LINEAGE,
        improvements: { improvements: [] },
      }),
    );
    render(<LineageView onNodeSelect={vi.fn()} />);
    fireEvent.click(await screen.findByTestId("lineage-mode-timeline"));
    await screen.findByTestId("improvements-empty");
  });
});

describe("LineageView — mode toggle", () => {
  it("defaults to tree mode and switches to timeline on click", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        lineage: ELEVEN_NODE_LINEAGE,
        improvements: POPULATED_IMPROVEMENTS,
      }),
    );
    render(<LineageView onNodeSelect={vi.fn()} />);

    // Tree visible first.
    await screen.findByTestId("lineage-tree-svg");
    expect(screen.queryByTestId("lineage-timeline")).not.toBeInTheDocument();

    // Toggle button aria state — tree active by default.
    expect(screen.getByTestId("lineage-mode-tree")).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("lineage-mode-timeline")).toHaveAttribute(
      "aria-selected",
      "false",
    );

    fireEvent.click(screen.getByTestId("lineage-mode-timeline"));
    await screen.findByTestId("lineage-timeline");
    expect(screen.queryByTestId("lineage-tree-svg")).not.toBeInTheDocument();
    expect(screen.getByTestId("lineage-mode-timeline")).toHaveAttribute(
      "aria-selected",
      "true",
    );

    // And back.
    fireEvent.click(screen.getByTestId("lineage-mode-tree"));
    await screen.findByTestId("lineage-tree-svg");
  });
});

describe("LineageView — timeline mode — legacy parity", () => {
  // These four assertions are ported verbatim (adapted for the new
  // component name + ``mode="timeline"`` toggle) from the deleted
  // ``ImprovementsTab.test.tsx`` so the LineageView's timeline mode
  // continues to enforce the same loading-state, stale-banner,
  // filter-pill, and outcome-badge contracts the legacy tab did.

  it("shows loading state before first fetch resolves", async () => {
    // Lineage fetch resolves (so the toggle is mountable) but the
    // unified-improvements fetch never resolves — exposing the
    // timeline-mode loading state.
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL): Promise<Response> => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/api/lineage")) {
          return jsonResponse({ nodes: [], edges: [] });
        }
        if (url.includes("/api/improvements/unified")) {
          return new Promise<Response>(() => undefined);
        }
        throw new Error(`Unexpected fetch: ${url}`);
      },
    );
    render(<LineageView onNodeSelect={vi.fn()} />);
    fireEvent.click(screen.getByTestId("lineage-mode-timeline"));
    await screen.findByTestId("lineage-timeline-loading");
    expect(screen.getByText(/loading improvements/i)).toBeInTheDocument();
  });

  it("shows stale-data banner on fetch error in timeline mode", async () => {
    // Lineage fetch resolves OK (don't want the lineage stale banner
    // colliding with the timeline-mode banner). Unified fetch throws.
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL): Promise<Response> => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/api/lineage")) {
          return jsonResponse({ nodes: [], edges: [] });
        }
        if (url.includes("/api/improvements/unified")) {
          throw new Error("network down");
        }
        throw new Error(`Unexpected fetch: ${url}`);
      },
    );
    render(<LineageView onNodeSelect={vi.fn()} />);
    fireEvent.click(screen.getByTestId("lineage-mode-timeline"));
    await waitFor(() => {
      expect(
        screen.getByText(/Improvements .* backend offline/i),
      ).toBeInTheDocument();
    });
  });

  it("filter pills change displayed entries", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        lineage: { nodes: [], edges: [] },
        improvements: POPULATED_LEGACY,
      }),
    );
    render(<LineageView onNodeSelect={vi.fn()} />);
    fireEvent.click(screen.getByTestId("lineage-mode-timeline"));

    await waitFor(() => {
      expect(
        screen.getByText("Stronger mineral floating penalties"),
      ).toBeInTheDocument();
    });

    // Click "Advised" — only advised entry remains.
    fireEvent.click(screen.getByTestId("filter-pill-advised"));
    expect(
      screen.getByText("Stronger mineral floating penalties"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Gas-dump warp priority")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Aggressive proxy pylon cheese"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("improvements-count")).toHaveTextContent(
      "1 of 3 (filtered)",
    );

    // Click "Evolve" — only evolve entries remain.
    fireEvent.click(screen.getByTestId("filter-pill-evolve"));
    expect(
      screen.queryByText("Stronger mineral floating penalties"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Gas-dump warp priority")).toBeInTheDocument();
    expect(
      screen.getByText("Aggressive proxy pylon cheese"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("improvements-count")).toHaveTextContent(
      "2 of 3 (filtered)",
    );

    // Back to "All".
    fireEvent.click(screen.getByTestId("filter-pill-all"));
    expect(
      screen.getByText("Stronger mineral floating penalties"),
    ).toBeInTheDocument();
    expect(screen.getByText("Gas-dump warp priority")).toBeInTheDocument();
    expect(
      screen.getByText("Aggressive proxy pylon cheese"),
    ).toBeInTheDocument();
  });

  it("outcome badge classes differ for promoted vs discarded", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        lineage: { nodes: [], edges: [] },
        improvements: POPULATED_LEGACY,
      }),
    );
    render(<LineageView onNodeSelect={vi.fn()} />);
    fireEvent.click(screen.getByTestId("lineage-mode-timeline"));

    await waitFor(() => {
      expect(
        screen.getByText("Stronger mineral floating penalties"),
      ).toBeInTheDocument();
    });

    const promoted = screen.getByTestId("outcome-badge-promoted");
    const discarded = screen.getByTestId("outcome-badge-discarded");
    expect(promoted.className).toContain("success");
    expect(discarded.className).toContain("failure");
    expect(promoted.className).not.toEqual(discarded.className);
  });
});

describe("LineageView — mode toggle preserves timeline state", () => {
  it("filter + expanded-row state survive a tree → timeline → tree → timeline cycle", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        lineage: ELEVEN_NODE_LINEAGE,
        improvements: POPULATED_LEGACY,
      }),
    );
    render(<LineageView onNodeSelect={vi.fn()} />);

    // Switch to timeline.
    fireEvent.click(screen.getByTestId("lineage-mode-timeline"));
    await screen.findByTestId("lineage-timeline");

    // Set a filter (advised) and expand the advised row.
    fireEvent.click(screen.getByTestId("filter-pill-advised"));
    expect(screen.getByTestId("filter-pill-advised")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(
      screen.getByTestId(`improvements-row-${ADVISED_ENTRY.id}`),
    );
    expect(
      screen.getByTestId(`improvements-row-expanded-${ADVISED_ENTRY.id}`),
    ).toBeInTheDocument();

    // Toggle back to tree mode.
    fireEvent.click(screen.getByTestId("lineage-mode-tree"));
    await screen.findByTestId("lineage-tree-svg");
    expect(screen.queryByTestId("lineage-timeline")).not.toBeInTheDocument();

    // And back to timeline — the lifted state should still hold.
    fireEvent.click(screen.getByTestId("lineage-mode-timeline"));
    await screen.findByTestId("lineage-timeline");

    // Filter pill is still "advised".
    expect(screen.getByTestId("filter-pill-advised")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    // Filtered count still reflects 1-of-3.
    expect(screen.getByTestId("improvements-count")).toHaveTextContent(
      "1 of 3 (filtered)",
    );
    // Expanded row is still expanded.
    expect(
      screen.getByTestId(`improvements-row-expanded-${ADVISED_ENTRY.id}`),
    ).toBeInTheDocument();
  });
});

describe("LineageView — ARIA tablist wiring", () => {
  it("tabs reference panels via aria-controls / aria-labelledby", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        lineage: ELEVEN_NODE_LINEAGE,
        improvements: POPULATED_IMPROVEMENTS,
      }),
    );
    render(<LineageView onNodeSelect={vi.fn()} />);

    const treeTab = screen.getByTestId("lineage-mode-tree");
    const timelineTab = screen.getByTestId("lineage-mode-timeline");
    expect(treeTab.id).toBe("lineage-mode-tab-tree");
    expect(timelineTab.id).toBe("lineage-mode-tab-timeline");
    expect(treeTab.getAttribute("aria-controls")).toBe(
      "lineage-mode-panel-tree",
    );
    expect(timelineTab.getAttribute("aria-controls")).toBe(
      "lineage-mode-panel-timeline",
    );

    // Active panel reflects the current mode and labels the panel
    // back to the active tab id.
    const treePanel = screen.getByTestId("lineage-mode-panel-tree");
    expect(treePanel).toHaveAttribute("role", "tabpanel");
    expect(treePanel).toHaveAttribute("id", "lineage-mode-panel-tree");
    expect(treePanel).toHaveAttribute(
      "aria-labelledby",
      "lineage-mode-tab-tree",
    );

    fireEvent.click(timelineTab);
    const timelinePanel = await screen.findByTestId(
      "lineage-mode-panel-timeline",
    );
    expect(timelinePanel).toHaveAttribute("role", "tabpanel");
    expect(timelinePanel).toHaveAttribute(
      "aria-labelledby",
      "lineage-mode-tab-timeline",
    );
  });
});

describe("computeTreeLayout", () => {
  it("produces 11 positioned nodes for the eleven-version chain", () => {
    const result = computeTreeLayout(ELEVEN_NODE_LINEAGE);
    expect(result.nodes.length).toBe(11);
    // 10 real edges (the synthetic-root edge to v0 is filtered out).
    expect(result.edges.length).toBe(10);
    // Every positioned node has finite x/y.
    for (const n of result.nodes) {
      expect(Number.isFinite(n.x)).toBe(true);
      expect(Number.isFinite(n.y)).toBe(true);
    }
  });

  it("returns an empty layout for an empty DAG", () => {
    const result = computeTreeLayout({ nodes: [], edges: [] });
    expect(result.nodes).toEqual([]);
    expect(result.edges).toEqual([]);
  });
});
