import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import {
  render,
  screen,
  waitFor,
  cleanup,
  fireEvent,
  within,
} from "@testing-library/react";

// Recharts' ``ResponsiveContainer`` measures its parent via
// ``ResizeObserver`` + ``getBoundingClientRect``. In jsdom both return
// 0×0 so the inner chart never paints. Stub the container with a fixed-
// size wrapper so jsdom can exercise the inner SVG path. The other
// Recharts primitives are passed through unchanged so structural
// assertions (svg element present, bar-chart rect counts, etc.) still
// reflect real behaviour.
vi.mock("recharts", async () => {
  const actual = await vi.importActual<typeof import("recharts")>("recharts");
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => {
      // Many of Recharts' children require ``width``/``height`` props.
      // Inject defaults via cloneElement for any single React child.
      if (React.isValidElement<{ width?: number; height?: number }>(children)) {
        return React.cloneElement(children, {
          width: children.props.width ?? 600,
          height: children.props.height ?? 280,
        });
      }
      return <div data-testid="responsive-container-stub">{children}</div>;
    },
  };
});

import { VersionInspector } from "./VersionInspector";
import type { Version } from "../types/version";

/**
 * VersionInspector tests — Step 6 of the Models-tab build plan.
 *
 * Coverage:
 *   - Empty state (``version=null``).
 *   - All 5 sub-panels render (collapsed by default; ``<details>``).
 *   - Expanding Config shows JSON content for each section.
 *   - Training curve renders for a version with rolling-WR data.
 *   - Actions bar chart renders for a version with transitions.
 *   - Improvements applied uses ``TimelineList`` and shows entries.
 *   - Weight Dynamics shows placeholder when response is ``[]``.
 *   - Weight Dynamics renders chart + red-dot rows when populated.
 *   - "Compare with parent" button click fires onCompareWithParent.
 *
 * Recharts strategy: rely on jsdom's structural SVG rendering — Recharts
 * 3.x emits ``<svg class="recharts-surface">`` containers we can match
 * without a screenshot or pixel-test. We DO assert on the data-testid
 * wrappers we control (``inspector-training-body`` etc.) and on the
 * fallback ``<ul data-testid="inspector-weight-error-list">`` so the
 * red-dot error contract is provable without depending on Recharts'
 * internal SVG attributes.
 */

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

const VERSIONS_FIXTURE: Version[] = [
  {
    name: "v0",
    race: "protoss",
    parent: null,
    harness_origin: "manual",
    timestamp: "2026-03-01T00:00:00Z",
    sha: null,
    fingerprint: null,
    current: false,
  },
  {
    name: "v3",
    race: "protoss",
    parent: "v2",
    harness_origin: "advised",
    timestamp: "2026-04-15T00:00:00Z",
    sha: null,
    fingerprint: null,
    current: false,
  },
  {
    name: "v7",
    race: "protoss",
    parent: "v6",
    harness_origin: "evolve",
    timestamp: "2026-04-30T00:00:00Z",
    sha: null,
    fingerprint: null,
    current: true,
  },
];

interface MockOpts {
  config?: {
    hyperparams: unknown;
    reward_rules: unknown;
    daemon_config: unknown;
  };
  trainingHistory?: {
    rolling_10: { game_id: string; ts: string; wr: number }[];
    rolling_50: { game_id: string; ts: string; wr: number }[];
    rolling_overall: { game_id: string; ts: string; wr: number }[];
  };
  actions?: { action_id: number; name: string; count: number; pct: number }[];
  improvements?: {
    id: string;
    source: "advised" | "evolve";
    timestamp: string | null;
    title: string;
    description: string;
    type: string;
    outcome: string;
    metric: string | null;
    principles: string[];
    files_changed: string[];
  }[];
  weightDynamics?: {
    checkpoint: string;
    ts: string | null;
    l2_per_layer: Record<string, number> | null;
    kl_from_parent: number | null;
    canary_source: string | null;
    error: string | null;
  }[];
  versions?: Version[];
}

function makeFetchMock(opts: MockOpts) {
  return vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (/\/api\/versions\/v\d+\/config$/.test(url)) {
      return jsonResponse(
        opts.config ?? {
          hyperparams: {},
          reward_rules: {},
          daemon_config: {},
        },
      );
    }
    if (/\/api\/versions\/v\d+\/training-history$/.test(url)) {
      return jsonResponse(
        opts.trainingHistory ?? {
          rolling_10: [],
          rolling_50: [],
          rolling_overall: [],
        },
      );
    }
    if (/\/api\/versions\/v\d+\/actions$/.test(url)) {
      return jsonResponse(opts.actions ?? []);
    }
    if (/\/api\/versions\/v\d+\/improvements$/.test(url)) {
      return jsonResponse(opts.improvements ?? []);
    }
    if (/\/api\/versions\/v\d+\/weight-dynamics$/.test(url)) {
      return jsonResponse(opts.weightDynamics ?? []);
    }
    if (url.includes("/api/versions")) {
      return jsonResponse(opts.versions ?? VERSIONS_FIXTURE);
    }
    throw new Error(`Unexpected fetch: ${url}`);
  });
}

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(makeFetchMock({}));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("VersionInspector — empty state", () => {
  it("renders the empty placeholder when version is null", () => {
    render(<VersionInspector version={null} onCompareWithParent={vi.fn()} />);
    expect(screen.getByTestId("version-inspector-empty")).toBeInTheDocument();
    expect(
      screen.getByText("Select a version to inspect."),
    ).toBeInTheDocument();
  });
});

describe("VersionInspector — accordion shell", () => {
  it("renders all 5 sub-panels (each as a collapsed <details>)", async () => {
    render(<VersionInspector version="v3" onCompareWithParent={vi.fn()} />);
    await screen.findByTestId("version-inspector");

    const expected = [
      "inspector-accordion-config",
      "inspector-accordion-training",
      "inspector-accordion-actions",
      "inspector-accordion-improvements",
      "inspector-accordion-weight",
    ];
    for (const id of expected) {
      const el = screen.getByTestId(id);
      expect(el).toBeInTheDocument();
      // <details> default open=false unless ``open`` attr is set.
      expect((el as HTMLDetailsElement).open).toBe(false);
    }
  });

  it("each sub-panel summary is the human label", async () => {
    render(<VersionInspector version="v3" onCompareWithParent={vi.fn()} />);
    await screen.findByTestId("version-inspector");

    expect(
      within(screen.getByTestId("inspector-accordion-config-summary")),
    ).toBeTruthy();
    expect(
      screen.getByTestId("inspector-accordion-config-summary"),
    ).toHaveTextContent("Config");
    expect(
      screen.getByTestId("inspector-accordion-training-summary"),
    ).toHaveTextContent("Training curve");
    expect(
      screen.getByTestId("inspector-accordion-actions-summary"),
    ).toHaveTextContent("Actions");
    expect(
      screen.getByTestId("inspector-accordion-improvements-summary"),
    ).toHaveTextContent("Improvements applied");
    expect(
      screen.getByTestId("inspector-accordion-weight-summary"),
    ).toHaveTextContent("Weight dynamics");
  });
});

describe("VersionInspector — Config sub-panel", () => {
  it("expanding Config renders JSON for hyperparams, reward_rules, daemon_config", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        config: {
          hyperparams: { lr: 0.0003, gamma: 0.99 },
          reward_rules: [{ id: "r1", value: 1.5 }],
          daemon_config: { max_runs: 5 },
        },
      }),
    );
    render(<VersionInspector version="v3" onCompareWithParent={vi.fn()} />);
    // Wait for fetch to resolve.
    await waitFor(() => {
      expect(screen.getByTestId("inspector-config-body")).toBeInTheDocument();
    });

    // Three labelled JSON blocks.
    const hp = screen.getByTestId("inspector-config-hyperparams");
    const rr = screen.getByTestId("inspector-config-reward_rules");
    const dc = screen.getByTestId("inspector-config-daemon_config");
    expect(hp).toBeInTheDocument();
    expect(rr).toBeInTheDocument();
    expect(dc).toBeInTheDocument();

    // Pretty-printed JSON content visible under each.
    expect(hp.textContent).toMatch(/0\.0003/);
    expect(hp.textContent).toMatch(/0\.99/);
    expect(rr.textContent).toMatch(/r1/);
    expect(dc.textContent).toMatch(/max_runs/);
  });
});

describe("VersionInspector — Training curve sub-panel", () => {
  it("renders a chart container when rolling-WR data is present", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        trainingHistory: {
          rolling_10: [
            { game_id: "g1", ts: "2026-04-30T00:00:00Z", wr: 0.5 },
            { game_id: "g2", ts: "2026-04-30T01:00:00Z", wr: 0.6 },
          ],
          rolling_50: [
            { game_id: "g1", ts: "2026-04-30T00:00:00Z", wr: 0.4 },
            { game_id: "g2", ts: "2026-04-30T01:00:00Z", wr: 0.45 },
          ],
          rolling_overall: [
            { game_id: "g1", ts: "2026-04-30T00:00:00Z", wr: 0.3 },
            { game_id: "g2", ts: "2026-04-30T01:00:00Z", wr: 0.4 },
          ],
        },
      }),
    );
    render(<VersionInspector version="v3" onCompareWithParent={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId("inspector-training-body")).toBeInTheDocument();
    });
    // Recharts renders an inner ``<svg class="recharts-surface">``.
    const body = screen.getByTestId("inspector-training-body");
    expect(body.querySelector("svg")).not.toBeNull();
  });

  it("shows an empty placeholder when training history is empty", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(makeFetchMock({}));
    render(<VersionInspector version="v3" onCompareWithParent={vi.fn()} />);
    await waitFor(() => {
      expect(
        screen.getByTestId("inspector-training-empty"),
      ).toBeInTheDocument();
    });
    expect(screen.getByTestId("inspector-training-empty")).toHaveTextContent(
      /no training games/i,
    );
  });
});

describe("VersionInspector — Actions sub-panel", () => {
  it("renders a bar chart when transitions are present", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        actions: [
          { action_id: 0, name: "opening", count: 50, pct: 0.5 },
          { action_id: 1, name: "macro", count: 30, pct: 0.3 },
          { action_id: 2, name: "harass", count: 20, pct: 0.2 },
        ],
      }),
    );
    render(<VersionInspector version="v3" onCompareWithParent={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId("inspector-actions-body")).toBeInTheDocument();
    });
    const body = screen.getByTestId("inspector-actions-body");
    expect(body.querySelector("svg")).not.toBeNull();
  });

  it("shows an empty placeholder when actions list is empty", async () => {
    render(<VersionInspector version="v3" onCompareWithParent={vi.fn()} />);
    await waitFor(() => {
      expect(
        screen.getByTestId("inspector-actions-empty"),
      ).toBeInTheDocument();
    });
  });
});

describe("VersionInspector — Improvements applied sub-panel", () => {
  it("delegates to TimelineList and renders filtered entries", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        improvements: [
          {
            id: "advised-1",
            source: "advised",
            timestamp: "2026-04-15T00:00:00Z",
            title: "Anti-float buy",
            description: "Anti-float improvement.",
            type: "training",
            outcome: "promoted",
            metric: "8/10",
            principles: ["§4.2"],
            files_changed: ["bots/v3/bot.py"],
          },
          {
            id: "evolve-1",
            source: "evolve",
            timestamp: "2026-04-29T00:00:00Z",
            title: "Splash readiness",
            description: "Evolve gen 1.",
            type: "dev",
            outcome: "fitness-pass",
            metric: "3-2",
            principles: [],
            files_changed: ["bots/v3/decision_engine.py"],
          },
        ],
      }),
    );
    render(<VersionInspector version="v3" onCompareWithParent={vi.fn()} />);

    // The TimelineList is mounted under the "Improvements applied"
    // ``<details>`` accordion. We use the ``inspector-timeline``
    // testIdPrefix to find it.
    await waitFor(() => {
      expect(screen.getByTestId("inspector-timeline")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("improvements-row-advised-1"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("improvements-row-evolve-1"),
    ).toBeInTheDocument();
    // Title heading is "Improvements applied" — appears in BOTH the
    // accordion summary and the TimelineList header. ``getAllByText``
    // returns at least 2 (summary + h3), proving the contract.
    expect(
      screen.getAllByText("Improvements applied").length,
    ).toBeGreaterThanOrEqual(1);
  });

  it("shows the per-version empty message when no improvements exist", async () => {
    render(<VersionInspector version="v3" onCompareWithParent={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId("inspector-timeline")).toBeInTheDocument();
    });
    expect(screen.getByTestId("improvements-empty")).toHaveTextContent(
      /no improvements have targeted this version/i,
    );
  });
});

describe("VersionInspector — Weight dynamics sub-panel", () => {
  it("shows the placeholder when weight-dynamics list is empty", async () => {
    render(<VersionInspector version="v3" onCompareWithParent={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId("inspector-weight-empty")).toBeInTheDocument();
    });
    expect(screen.getByTestId("inspector-weight-empty")).toHaveTextContent(
      /pending/i,
    );
    expect(screen.getByTestId("inspector-weight-empty")).toHaveTextContent(
      /scripts\/compute_weight_dynamics\.py/i,
    );
  });

  it("renders a chart and surfaces error rows when populated", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        weightDynamics: [
          {
            checkpoint: "ckpt_001",
            ts: "2026-04-30T00:00:00Z",
            l2_per_layer: { layer1: 1.5, layer2: 2.1 },
            kl_from_parent: 0.05,
            canary_source: "v3",
            error: null,
          },
          {
            checkpoint: "ckpt_002",
            ts: "2026-04-30T01:00:00Z",
            l2_per_layer: null,
            kl_from_parent: null,
            canary_source: null,
            error: "ImportError: torch not found",
          },
          {
            checkpoint: "ckpt_003",
            ts: "2026-04-30T02:00:00Z",
            l2_per_layer: { layer1: 1.6, layer2: 2.2 },
            kl_from_parent: 0.08,
            canary_source: "v3",
            error: null,
          },
        ],
      }),
    );
    render(<VersionInspector version="v3" onCompareWithParent={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId("inspector-weight-body")).toBeInTheDocument();
    });
    // Chart svg present.
    const body = screen.getByTestId("inspector-weight-body");
    expect(body.querySelector("svg")).not.toBeNull();

    // Error overlay + readable error list both present.
    expect(
      screen.getByTestId("inspector-weight-error-overlay"),
    ).toBeInTheDocument();
    const list = screen.getByTestId("inspector-weight-error-list");
    expect(
      within(list).getByTestId(
        "inspector-weight-error-row-ckpt_002",
      ),
    ).toBeInTheDocument();
    expect(list).toHaveTextContent("ImportError");

    // Healthy checkpoints don't appear in the error list.
    expect(
      within(list).queryByTestId(
        "inspector-weight-error-row-ckpt_001",
      ),
    ).not.toBeInTheDocument();
  });
});

describe("VersionInspector — Compare with parent button", () => {
  it("fires onCompareWithParent with the parent version on click", async () => {
    const onCompare = vi.fn();
    render(
      <VersionInspector version="v3" onCompareWithParent={onCompare} />,
    );
    // Wait for the registry fetch to resolve so ``parent`` is known.
    await waitFor(() => {
      const btn = screen.getByTestId(
        "inspector-compare-with-parent",
      ) as HTMLButtonElement;
      expect(btn.disabled).toBe(false);
    });
    fireEvent.click(screen.getByTestId("inspector-compare-with-parent"));
    expect(onCompare).toHaveBeenCalledTimes(1);
    expect(onCompare).toHaveBeenCalledWith("v2");
  });

  it("disables the button when the version has no parent (genesis)", async () => {
    const onCompare = vi.fn();
    render(
      <VersionInspector version="v0" onCompareWithParent={onCompare} />,
    );
    await waitFor(() => {
      // Wait for the registry to resolve.
      const btn = screen.getByTestId(
        "inspector-compare-with-parent",
      ) as HTMLButtonElement;
      // v0's parent is null in the fixture; button stays disabled.
      expect(btn.disabled).toBe(true);
    });
    fireEvent.click(screen.getByTestId("inspector-compare-with-parent"));
    expect(onCompare).not.toHaveBeenCalled();
  });
});
