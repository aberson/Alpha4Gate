import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  cleanup,
  within,
} from "@testing-library/react";
import { ObservableTab } from "./ObservableTab";
import type { Version } from "../types/version";

/**
 * ObservableTab tests — Step 10 of the Models-tab build plan.
 *
 * Coverage:
 *   - Two version dropdowns populate from a real 11-version fixture.
 *   - Phase L placeholder card renders with the wiki link.
 *   - Empty state renders when ``/api/versions`` returns no rows.
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

function mockVersionsFetch(body: Version[]) {
  return vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/versions")) {
      return jsonResponse(body);
    }
    throw new Error(`Unexpected fetch: ${url}`);
  });
}

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async (input: RequestInfo | URL): Promise<Response> => {
      const url =
        typeof input === "string" ? input : (input as URL).toString();
      if (url.includes("/api/versions")) {
        return jsonResponse([] as Version[]);
      }
      throw new Error(`Unexpected fetch: ${url}`);
    },
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ObservableTab", () => {
  it("renders without crashing when registry is empty and shows the empty state", async () => {
    render(<ObservableTab />);
    await waitFor(() => {
      expect(screen.getByTestId("observable-tab")).toBeInTheDocument();
    });
    // Both dropdowns rendered with the placeholder option.
    const left = screen.getByTestId(
      "observable-version-select-left",
    ) as HTMLSelectElement;
    const right = screen.getByTestId(
      "observable-version-select-right",
    ) as HTMLSelectElement;
    expect(left).toBeInTheDocument();
    expect(right).toBeInTheDocument();
    expect(left.value).toBe("");
    expect(right.value).toBe("");
    expect(within(left).getByText("(no versions)")).toBeInTheDocument();
    expect(within(right).getByText("(no versions)")).toBeInTheDocument();
    // Empty-state hint rendered.
    expect(screen.getByTestId("observable-empty-state")).toBeInTheDocument();
  });

  it("populates both dropdowns from the 11-version registry fixture", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockVersionsFetch(ELEVEN_PROTOSS_VERSIONS),
    );
    render(<ObservableTab />);

    const left = (await screen.findByTestId(
      "observable-version-select-left",
    )) as HTMLSelectElement;
    const right = (await screen.findByTestId(
      "observable-version-select-right",
    )) as HTMLSelectElement;

    await waitFor(() => {
      expect(left.querySelectorAll("option").length).toBe(11);
      expect(right.querySelectorAll("option").length).toBe(11);
    });

    // Default selection: left = current (v7), right = current.parent (v6).
    await waitFor(() => {
      expect(left.value).toBe("v7");
      expect(right.value).toBe("v6");
    });

    // Sanity check option labels on the left side; right shares the same
    // option set so checking one side is sufficient.
    expect(within(left).getByText("v0")).toBeInTheDocument();
    expect(within(left).getByText("v7 (current)")).toBeInTheDocument();
    expect(within(left).getByText("v10")).toBeInTheDocument();

    // Empty-state hint NOT rendered when there are versions.
    expect(
      screen.queryByTestId("observable-empty-state"),
    ).not.toBeInTheDocument();
  });

  it("renders the Phase L placeholder card with a wiki link", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      mockVersionsFetch(ELEVEN_PROTOSS_VERSIONS),
    );
    render(<ObservableTab />);

    await waitFor(() => {
      expect(
        screen.getByTestId("observable-phase-l-placeholder"),
      ).toBeInTheDocument();
    });

    // Placeholder copy mentions Phase L's mechanism. The phrase
    // "Exhibition mode awaits Phase L" appears in both the heading and
    // the body paragraph, so use ``getAllByText`` to assert presence
    // across both (don't pin to a count — the body wraps with inline
    // <code> nodes that can fragment match boundaries between markup
    // changes).
    expect(
      screen.getAllByText(/Exhibition mode awaits Phase L/i).length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/replay-stream-as-live/i)).toBeInTheDocument();

    // Wiki link points at documentation/wiki/models-tab.md.
    const link = screen.getByTestId(
      "observable-wiki-link",
    ) as HTMLAnchorElement;
    expect(link).toBeInTheDocument();
    expect(link.getAttribute("href")).toMatch(
      /documentation\/wiki\/models-tab\.md$/,
    );
  });
});
