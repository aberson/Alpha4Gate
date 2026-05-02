import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  cleanup,
  fireEvent,
  within,
} from "@testing-library/react";
import { ModelsTab } from "./ModelsTab";
import type { Version } from "../types/version";

/**
 * ModelsTab shell tests — Step 3 of the Models-tab build plan.
 *
 * These tests exercise the FRAME only:
 *   - Renders without crashing on an empty ``/api/versions`` response.
 *   - Version dropdown populates from the registry; default snaps to the
 *     ``current: true`` row.
 *   - Race filter is hidden when every version coerces to the same race
 *     (today: protoss); shown when a fixture mixes races.
 *   - Harness chips render all 4 origins and toggle on click.
 *   - Sub-view router cycles through 5 placeholder panels.
 *   - ``onNodeSelect`` from the Lineage placeholder both selects a
 *     version and switches the sub-view to ``inspector``.
 *   - Manual refresh button triggers a refetch of ``/api/versions``.
 */

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

const ELEVEN_LINEAGE_NODES = {
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
    { from: "v3", to: "v4", harness: "evolve", improvement_title: "Defend timeout", ts: "2026-04-29T00:00:00Z", outcome: "promoted" },
    { from: "v4", to: "v5", harness: "evolve", improvement_title: "Splash 2", ts: "2026-04-30T00:00:00Z", outcome: "promoted" },
    { from: "v5", to: "v6", harness: "evolve", improvement_title: "Observer", ts: "2026-04-30T01:00:00Z", outcome: "promoted" },
    { from: "v6", to: "v7", harness: "evolve", improvement_title: "Chrono", ts: "2026-04-30T02:00:00Z", outcome: "promoted" },
    { from: "v7", to: "v8", harness: "manual", improvement_title: "manual", ts: "2026-04-30T03:00:00Z", outcome: "promoted" },
    { from: "v8", to: "v9", harness: "self-play", improvement_title: "—", ts: "2026-04-30T04:00:00Z", outcome: "promoted" },
    { from: "v9", to: "v10", harness: "manual", improvement_title: "manual", ts: "2026-04-30T05:00:00Z", outcome: "promoted" },
  ],
};

const ELEVEN_PROTOSS_VERSIONS: Version[] = [
  { name: "v0", race: "protoss", parent: null, harness_origin: "manual", timestamp: "2026-03-01T00:00:00Z", sha: null, fingerprint: null, current: false },
  { name: "v1", race: "protoss", parent: "v0", harness_origin: "evolve", timestamp: "2026-03-15T00:00:00Z", sha: null, fingerprint: null, current: false },
  { name: "v2", race: "protoss", parent: "v1", harness_origin: "evolve", timestamp: "2026-03-22T00:00:00Z", sha: null, fingerprint: null, current: false },
  { name: "v3", race: "protoss", parent: "v2", harness_origin: "advised", timestamp: "2026-04-15T00:00:00Z", sha: null, fingerprint: null, current: false },
  { name: "v4", race: "protoss", parent: "v3", harness_origin: "evolve", timestamp: "2026-04-29T00:00:00Z", sha: null, fingerprint: null, current: false },
  { name: "v5", race: "protoss", parent: "v4", harness_origin: "evolve", timestamp: "2026-04-30T00:00:00Z", sha: null, fingerprint: null, current: false },
  { name: "v6", race: "protoss", parent: "v5", harness_origin: "evolve", timestamp: "2026-04-30T01:00:00Z", sha: null, fingerprint: null, current: false },
  { name: "v7", race: "protoss", parent: "v6", harness_origin: "evolve", timestamp: "2026-04-30T02:00:00Z", sha: null, fingerprint: null, current: true },
  { name: "v8", race: "protoss", parent: "v7", harness_origin: "manual", timestamp: "2026-04-30T03:00:00Z", sha: null, fingerprint: null, current: false },
  { name: "v9", race: "protoss", parent: "v8", harness_origin: "self-play", timestamp: "2026-04-30T04:00:00Z", sha: null, fingerprint: null, current: false },
  { name: "v10", race: "protoss", parent: "v9", harness_origin: "manual", timestamp: "2026-04-30T05:00:00Z", sha: null, fingerprint: null, current: false },
];

// Fixture mixing protoss + a future zerg version — exercises the race
// filter visibility branch (Phase G state).
const MIXED_RACE_VERSIONS: Version[] = [
  { name: "v0", race: "protoss", parent: null, harness_origin: "manual", timestamp: "2026-03-01T00:00:00Z", sha: null, fingerprint: null, current: true },
  { name: "v_zerg_0", race: "zerg", parent: null, harness_origin: "manual", timestamp: "2026-05-15T00:00:00Z", sha: null, fingerprint: null, current: false },
];

// Coercion edge case: legacy manifests with race=null AND race="" should
// both collapse to "protoss" and keep the filter hidden. The empty-string
// row exercises the second predicate in ``coerceRace`` — a Phase G race
// migration that wrote ``race: ""`` instead of ``null`` would otherwise
// silently flip the filter on.
const NULL_RACE_VERSIONS: Version[] = [
  { name: "v0", race: null, parent: null, harness_origin: "manual", timestamp: null, sha: null, fingerprint: null, current: true },
  { name: "v1", race: "protoss", parent: "v0", harness_origin: "evolve", timestamp: null, sha: null, fingerprint: null, current: false },
  { name: "v2", race: "", parent: "v1", harness_origin: "evolve", timestamp: null, sha: null, fingerprint: null, current: false },
];

function mockVersionsFetch(body: Version[]) {
  const fn = vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    // Step 6 wired the real VersionInspector into the Inspector
    // sub-view, so any test that toggles to it now sees five extra
    // per-version fetches. Match the per-version endpoints BEFORE the
    // generic ``/api/versions`` registry endpoint so the inner pattern
    // (``/api/versions/v3/config``) doesn't get hijacked by the
    // registry response above.
    if (/\/api\/versions\/v\d+\/config$/.test(url)) {
      return jsonResponse({
        hyperparams: {},
        reward_rules: {},
        daemon_config: {},
      });
    }
    if (/\/api\/versions\/v\d+\/training-history$/.test(url)) {
      return jsonResponse({
        rolling_10: [],
        rolling_50: [],
        rolling_overall: [],
      });
    }
    if (/\/api\/versions\/v\d+\/actions$/.test(url)) {
      return jsonResponse([]);
    }
    if (/\/api\/versions\/v\d+\/improvements$/.test(url)) {
      return jsonResponse([]);
    }
    if (/\/api\/versions\/v\d+\/weight-dynamics$/.test(url)) {
      return jsonResponse([]);
    }
    // Step 7 wired the real CompareView into the Compare sub-view, so
    // toggling to it now hits /api/ladder. Return an empty ladder so
    // shell tests that visit Compare don't blow up.
    if (url.includes("/api/ladder")) {
      return jsonResponse({ standings: [], head_to_head: {} });
    }
    // Step 8 wired the real ForensicsView into the Forensics sub-view,
    // so toggling to it now hits /api/versions/{v}/forensics/{game}
    // when a game id is selected. Return an empty forensics body so
    // shell tests that visit Forensics don't blow up.
    if (/\/api\/versions\/v\d+\/forensics\//.test(url)) {
      return jsonResponse({
        trajectory: [],
        give_up_fired: false,
        give_up_step: null,
        expert_dispatch: null,
      });
    }
    if (url.includes("/api/versions")) {
      return jsonResponse(body);
    }
    // Step 4 wired the real LineageView into the Lineage sub-view, so
    // the shell tests now also see /api/lineage and
    // /api/improvements/unified fetches when that sub-view is mounted.
    // Return safe empty bodies for those — the shell tests don't
    // exercise lineage rendering, just the FRAME and the wiring.
    if (url.includes("/api/lineage")) {
      return jsonResponse(ELEVEN_LINEAGE_NODES);
    }
    if (url.includes("/api/improvements/unified")) {
      return jsonResponse({ improvements: [] });
    }
    // Step 5 wires the real LiveRunsGrid into the Live sub-view, so
    // the shell tests now also see /api/runs/active when the operator
    // toggles to that sub-view. Empty list keeps the existing tests
    // (which only assert on FRAME wiring) on their happy path.
    if (url.includes("/api/runs/active")) {
      return jsonResponse([]);
    }
    throw new Error(`Unexpected fetch: ${url}`);
  });
  return fn;
}

beforeEach(() => {
  // Default: empty registry + safe empty bodies for the lineage and
  // unified-improvements endpoints (Step 4 wired LineageView in, so
  // the default sub-view (``lineage``) now hits both on every render).
  // Individual tests override.
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async (input: RequestInfo | URL): Promise<Response> => {
      const url =
        typeof input === "string" ? input : (input as URL).toString();
      if (url.includes("/api/lineage")) {
        return jsonResponse({ nodes: [], edges: [] });
      }
      if (url.includes("/api/improvements/unified")) {
        return jsonResponse({ improvements: [] });
      }
      if (url.includes("/api/runs/active")) {
        return jsonResponse([]);
      }
      // Step 6 — per-version inspector endpoints. Default to empty
      // payloads so tests that toggle to the Inspector sub-view don't
      // need their own per-version mock setup.
      if (/\/api\/versions\/v\d+\/config$/.test(url)) {
        return jsonResponse({
          hyperparams: {},
          reward_rules: {},
          daemon_config: {},
        });
      }
      if (/\/api\/versions\/v\d+\/training-history$/.test(url)) {
        return jsonResponse({
          rolling_10: [],
          rolling_50: [],
          rolling_overall: [],
        });
      }
      if (/\/api\/versions\/v\d+\/actions$/.test(url)) {
        return jsonResponse([]);
      }
      if (/\/api\/versions\/v\d+\/improvements$/.test(url)) {
        return jsonResponse([]);
      }
      if (/\/api\/versions\/v\d+\/weight-dynamics$/.test(url)) {
        return jsonResponse([]);
      }
      if (url.includes("/api/ladder")) {
        return jsonResponse({ standings: [], head_to_head: {} });
      }
      // Step 8 — per-game forensics endpoint. Default to an empty
      // payload so tests that toggle to the Forensics sub-view don't
      // need their own per-game mock setup.
      if (/\/api\/versions\/v\d+\/forensics\//.test(url)) {
        return jsonResponse({
          trajectory: [],
          give_up_fired: false,
          give_up_step: null,
          expert_dispatch: null,
        });
      }
      return jsonResponse([] as Version[]);
    },
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ModelsTab — shell", () => {
  it("renders without crashing when versions list is empty", async () => {
    render(<ModelsTab />);
    await waitFor(() => {
      expect(screen.getByTestId("models-tab")).toBeInTheDocument();
    });
    // Empty-registry placeholder option visible.
    const select = screen.getByTestId(
      "models-version-select",
    ) as HTMLSelectElement;
    expect(select).toBeInTheDocument();
    expect(select.value).toBe("");
    expect(
      within(select).getByText("(no versions)"),
    ).toBeInTheDocument();
  });

  it("populates version dropdown with 11 versions and selects the current one", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockVersionsFetch(ELEVEN_PROTOSS_VERSIONS),
    );
    render(<ModelsTab />);
    const select = (await screen.findByTestId(
      "models-version-select",
    )) as HTMLSelectElement;
    await waitFor(() => {
      // 11 real options once the fetch resolves (no placeholder).
      expect(select.querySelectorAll("option").length).toBe(11);
    });
    // Default selection snaps to v7 (the ``current: true`` row).
    await waitFor(() => {
      expect(select.value).toBe("v7");
    });
    // Sanity check a few labels.
    expect(within(select).getByText("v0")).toBeInTheDocument();
    expect(within(select).getByText("v7 (current)")).toBeInTheDocument();
    expect(within(select).getByText("v10")).toBeInTheDocument();
  });

  it("hides the race filter when every version coerces to a single race", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockVersionsFetch(ELEVEN_PROTOSS_VERSIONS),
    );
    render(<ModelsTab />);
    await waitFor(() => {
      expect(screen.getByTestId("models-version-select")).toBeInTheDocument();
    });
    // Race filter NOT in the DOM.
    expect(screen.queryByTestId("models-race-filter")).not.toBeInTheDocument();
  });

  it("hides the race filter when null races coerce to protoss alongside explicit protoss", async () => {
    // Coercion edge case: race=null should map to "protoss" so the
    // filter stays hidden when the only other rows are also protoss.
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockVersionsFetch(NULL_RACE_VERSIONS),
    );
    render(<ModelsTab />);
    await waitFor(() => {
      expect(screen.getByTestId("models-version-select")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("models-race-filter")).not.toBeInTheDocument();
  });

  it("shows the race filter when fixtures include a non-protoss race", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockVersionsFetch(MIXED_RACE_VERSIONS),
    );
    render(<ModelsTab />);
    await waitFor(() => {
      expect(screen.getByTestId("models-race-filter")).toBeInTheDocument();
    });
    const raceSelect = screen.getByTestId(
      "models-race-select",
    ) as HTMLSelectElement;
    // Two distinct races plus the "All" option.
    expect(raceSelect.querySelectorAll("option").length).toBe(3);
  });

  it("renders 4 harness chips and toggles inclusion on click", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockVersionsFetch(ELEVEN_PROTOSS_VERSIONS),
    );
    render(<ModelsTab />);
    await waitFor(() => {
      expect(screen.getByTestId("models-harness-chips")).toBeInTheDocument();
    });

    const advised = screen.getByTestId("models-harness-chip-advised");
    const evolve = screen.getByTestId("models-harness-chip-evolve");
    const manual = screen.getByTestId("models-harness-chip-manual");
    const selfplay = screen.getByTestId("models-harness-chip-self-play");

    // All four start active.
    expect(advised).toHaveAttribute("aria-pressed", "true");
    expect(evolve).toHaveAttribute("aria-pressed", "true");
    expect(manual).toHaveAttribute("aria-pressed", "true");
    expect(selfplay).toHaveAttribute("aria-pressed", "true");

    // Toggle one off — only it changes.
    fireEvent.click(advised);
    expect(advised).toHaveAttribute("aria-pressed", "false");
    expect(evolve).toHaveAttribute("aria-pressed", "true");
    expect(manual).toHaveAttribute("aria-pressed", "true");
    expect(selfplay).toHaveAttribute("aria-pressed", "true");

    // Toggle it back on.
    fireEvent.click(advised);
    expect(advised).toHaveAttribute("aria-pressed", "true");
  });

  it("sub-view router switches between 5 placeholder panels", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockVersionsFetch(ELEVEN_PROTOSS_VERSIONS),
    );
    render(<ModelsTab />);
    await waitFor(() => {
      // Lineage is the default sub-view.
      expect(screen.getByTestId("models-subview-lineage")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("models-subview-button-live"));
    expect(screen.getByTestId("models-subview-live")).toBeInTheDocument();
    expect(
      screen.queryByTestId("models-subview-lineage"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("models-subview-button-inspector"));
    expect(screen.getByTestId("models-subview-inspector")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("models-subview-button-compare"));
    expect(screen.getByTestId("models-subview-compare")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("models-subview-button-forensics"));
    expect(screen.getByTestId("models-subview-forensics")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("models-subview-button-lineage"));
    expect(screen.getByTestId("models-subview-lineage")).toBeInTheDocument();
  });

  it("onNodeSelect from real LineageView selects version and switches to inspector", async () => {
    // Step 4: the placeholder simulate-button is gone; clicking a real
    // tree node fires onNodeSelect with the version string. The
    // LineageView's d3-hierarchy layout emits one
    // ``data-testid="lineage-tree-node-vN"`` ``<g>`` per node, so we
    // just click that surface directly.
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockVersionsFetch(ELEVEN_PROTOSS_VERSIONS),
    );
    render(<ModelsTab />);
    // Wait for the lineage tree to render (post /api/lineage fetch).
    const v3Node = await screen.findByTestId("lineage-tree-node-v3");
    fireEvent.click(v3Node);

    // Sub-view switched to inspector AND inspector reports selected v3.
    expect(screen.getByTestId("models-subview-inspector")).toBeInTheDocument();
    expect(screen.getByTestId("models-inspector-selected")).toHaveTextContent(
      "Selected: v3",
    );

    // Version dropdown also reflects the selection.
    const select = screen.getByTestId(
      "models-version-select",
    ) as HTMLSelectElement;
    expect(select.value).toBe("v3");
  });

  it("Compare sub-view renders the CompareView with A/B prefill", async () => {
    // Step 7: clicking "Compare with parent" inside the Inspector
    // pre-fills compareA=current, compareB=parent and switches to
    // Compare. The wrapper still carries ``models-compare-prefill``
    // for the legacy assertion; the real CompareView panels render
    // beneath.
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockVersionsFetch(ELEVEN_PROTOSS_VERSIONS),
    );
    render(<ModelsTab />);
    await waitFor(() => {
      expect(screen.getByTestId("models-version-select")).toBeInTheDocument();
    });
    // Toggle to Inspector for v7 (current).
    fireEvent.click(screen.getByTestId("models-subview-button-inspector"));
    const compareWithParent = await screen.findByTestId(
      "inspector-compare-with-parent",
    );
    fireEvent.click(compareWithParent);
    // Compare sub-view active with prefill A=v7 / B=v6.
    await waitFor(() => {
      expect(screen.getByTestId("models-subview-compare")).toBeInTheDocument();
    });
    expect(screen.getByTestId("models-compare-prefill")).toHaveTextContent(
      "A: v7 / B: v6",
    );
    // Real CompareView mounted.
    expect(screen.getByTestId("compare-view")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("compare-panel-elo")).toBeInTheDocument();
    });
  });

  it("Compare sub-view defaults A=current, B=current.parent on direct nav", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockVersionsFetch(ELEVEN_PROTOSS_VERSIONS),
    );
    render(<ModelsTab />);
    await waitFor(() => {
      expect(screen.getByTestId("models-version-select")).toBeInTheDocument();
    });
    // Direct nav to Compare without going through Inspector first.
    fireEvent.click(screen.getByTestId("models-subview-button-compare"));
    await waitFor(() => {
      expect(screen.getByTestId("models-compare-prefill")).toHaveTextContent(
        "A: v7 / B: v6",
      );
    });
  });

  it("Forensics sub-view mounts the real ForensicsView with selected version", async () => {
    // Step 8: clicking the Forensics sub-view replaces the placeholder
    // with the real ForensicsView. The wrapper preserves the legacy
    // ``models-subview-forensics`` testid; the inner ``forensics-view``
    // testid proves the real component is wired up. With v7
    // (``current``) the selector defaults to the most-recent game id.
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockVersionsFetch(ELEVEN_PROTOSS_VERSIONS),
    );
    render(<ModelsTab />);
    await waitFor(() => {
      expect(screen.getByTestId("models-version-select")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("models-subview-button-forensics"));
    expect(
      screen.getByTestId("models-subview-forensics"),
    ).toBeInTheDocument();
    // Inner real component mounted with v7 (the current version).
    await waitFor(() => {
      expect(screen.getByTestId("forensics-view")).toBeInTheDocument();
    });
    expect(screen.getByTestId("forensics-title")).toHaveTextContent("v7");
    // Phase O placeholder always present.
    expect(
      screen.getByTestId("forensics-expert-dispatch"),
    ).toBeInTheDocument();
  });

  it("manual refresh button triggers a refetch of /api/versions", async () => {
    const fetchMock = mockVersionsFetch(ELEVEN_PROTOSS_VERSIONS);
    vi.spyOn(globalThis, "fetch").mockImplementation(fetchMock);
    render(<ModelsTab />);
    await waitFor(() => {
      expect(screen.getByTestId("models-version-select")).toBeInTheDocument();
    });

    // Step 4: lineage + improvements fetches now also pass through this
    // mock; filter to versions-only calls so the assertion still tests
    // what it claims to test (the manual refresh re-fetched
    // /api/versions specifically).
    const versionsCalls = (): number =>
      fetchMock.mock.calls.filter((args) => {
        const url =
          typeof args[0] === "string" ? args[0] : (args[0] as URL).toString();
        return url.includes("/api/versions");
      }).length;
    const callsBefore = versionsCalls();
    expect(callsBefore).toBeGreaterThanOrEqual(1);

    fireEvent.click(screen.getByTestId("models-refresh"));

    await waitFor(() => {
      expect(versionsCalls()).toBeGreaterThan(callsBefore);
    });
  });
});
