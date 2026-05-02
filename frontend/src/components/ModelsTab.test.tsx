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
    if (url.includes("/api/versions")) {
      return jsonResponse(body);
    }
    throw new Error(`Unexpected fetch: ${url}`);
  });
  return fn;
}

beforeEach(() => {
  // Default: empty registry. Individual tests override.
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    jsonResponse([] as Version[]),
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

  it("onNodeSelect from Lineage placeholder selects version and switches to inspector", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockVersionsFetch(ELEVEN_PROTOSS_VERSIONS),
    );
    render(<ModelsTab />);
    // Wait for the simulate-select button to be enabled (versions loaded).
    const simulateBtn = (await screen.findByTestId(
      "models-lineage-simulate-select",
    )) as HTMLButtonElement;
    await waitFor(() => {
      expect(simulateBtn).not.toBeDisabled();
    });
    expect(simulateBtn.textContent).toContain("v3");

    fireEvent.click(simulateBtn);

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

  it("manual refresh button triggers a refetch of /api/versions", async () => {
    const fetchMock = mockVersionsFetch(ELEVEN_PROTOSS_VERSIONS);
    vi.spyOn(globalThis, "fetch").mockImplementation(fetchMock);
    render(<ModelsTab />);
    await waitFor(() => {
      expect(screen.getByTestId("models-version-select")).toBeInTheDocument();
    });

    // useApi triggers one fetch on mount; capture that count then click.
    const callsBefore = fetchMock.mock.calls.length;
    expect(callsBefore).toBeGreaterThanOrEqual(1);

    fireEvent.click(screen.getByTestId("models-refresh"));

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBefore);
    });
  });
});
