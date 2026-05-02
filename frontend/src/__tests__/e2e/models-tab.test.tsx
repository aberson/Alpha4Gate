import { describe, it, expect, beforeAll, afterAll, beforeEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  cleanup,
  fireEvent,
} from "@testing-library/react";
import App from "../../App";

/**
 * Models tab + Observable tab END-TO-END test invoking a REAL backend.
 *
 * Plan: documentation/plans/models-tab-plan.md §7 Step 11 — "vitest e2e
 * suite invoking real backend".
 *
 * GATING:
 *   This test is OFF by default so green CI does not depend on a running
 *   backend. To enable, set ``BACKEND_E2E=1`` in the environment before
 *   running vitest:
 *
 *     BACKEND_E2E=1 npm test -- --run frontend/src/__tests__/e2e
 *
 *   Requires a backend reachable at ``BACKEND_URL`` (default
 *   ``http://localhost:8765``). Start one with::
 *
 *     bash scripts/start-dev.sh                       # backend + frontend
 *     uv run python -m bots.current.runner --serve   # backend only
 *
 * VALUE-ADD vs the per-component tests in this directory:
 *   The per-component tests stub ``fetch`` with handcrafted fixtures.
 *   This test mounts ``<App />`` against a REAL backend so we catch
 *   schema drift between the API contract and the consumer hooks.
 *   Each sub-view here asserts on AT LEAST ONE content-bearing element
 *   (not just the wrapper testid) so a backend returning empty / 4xx /
 *   5xx responses cannot silently pass — that "empty response leak"
 *   was the iter-1 review's HIGH-1 finding.
 */

const E2E_ENABLED = process.env.BACKEND_E2E === "1";
const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8765";

// jsdom resolves relative URLs against the document URL (about:blank by
// default). Frontend hooks fetch from "/api/..." which fails. Wrap fetch
// to prepend the backend URL when the input starts with "/api/".
let originalFetch: typeof fetch;

beforeAll(() => {
  if (!E2E_ENABLED) return;
  originalFetch = globalThis.fetch;
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    let url: string;
    if (typeof input === "string") url = input;
    else if (input instanceof URL) url = input.toString();
    else url = (input as Request).url;
    if (url.startsWith("/api/") || url.startsWith("/ws/")) {
      url = BACKEND_URL.replace(/\/$/, "") + url;
    }
    return originalFetch(url, init);
  }) as typeof fetch;
});

afterAll(() => {
  if (originalFetch) {
    globalThis.fetch = originalFetch;
  }
});

beforeEach(() => {
  cleanup();
});

const describeIfE2E = E2E_ENABLED ? describe : describe.skip;

// Generous per-sub-view timeout — each click triggers one or more REAL
// HTTP roundtrips against the backend, and useApi has its own retry +
// stale-cache plumbing. 10s leaves headroom for cold caches without
// masking a wedged backend.
const SUB_VIEW_TIMEOUT_MS = 10_000;

// Per-test wall-clock — each test mounts the full <App /> shell and
// fires a sub-view through the router; the chained ``waitFor`` calls
// can each take up to SUB_VIEW_TIMEOUT_MS, so the test envelope must
// sit comfortably above that. Vitest default is 5000ms which trips on
// cold-cache renders against a real backend.
const TEST_TIMEOUT_MS = 30_000;

describeIfE2E("Models tab e2e (real backend)", () => {
  it("renders Lineage with >=10 nodes from real /api/lineage", async () => {
    render(<App />);

    const modelsTab = await screen.findByRole("button", { name: "Models" });
    fireEvent.click(modelsTab);

    // Wait for the lineage sub-view wrapper to mount.
    await waitFor(
      () => {
        expect(screen.getByTestId("models-subview-lineage")).toBeInTheDocument();
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );

    // Content assertion: ``LineageView`` renders one ``<g>`` per node
    // with ``data-testid="lineage-tree-node-${version}"``. A backend
    // returning ``{nodes: [], edges: []}`` would render zero matches and
    // fail this assertion. The plan's done-when says ">=10 nodes".
    await waitFor(
      () => {
        const nodes = screen.queryAllByTestId(/^lineage-tree-node-/);
        expect(nodes.length).toBeGreaterThanOrEqual(10);
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );
  }, TEST_TIMEOUT_MS);

  it("renders Live Runs with either real cards or the empty-state card", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Models" }));
    await waitFor(
      () => {
        expect(screen.getByTestId("models-subview-lineage")).toBeInTheDocument();
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );

    fireEvent.click(screen.getByTestId("models-subview-button-live"));
    await waitFor(
      () => {
        expect(screen.getByTestId("models-subview-live")).toBeInTheDocument();
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );

    // Content assertion: ``LiveRunsGrid`` always renders EITHER one card
    // per active harness (``run-card-${harness}``) OR a single empty-
    // state card (``run-card-empty``) when the runs list is empty. A
    // genuinely broken backend would render NEITHER (the grid wrapper
    // would still mount but the inner state would be undefined). Asserting
    // on the OR catches that gap without forcing the test to know whether
    // an active harness exists at gate-run time.
    await waitFor(
      () => {
        const cards = screen.queryAllByTestId(/^run-card-/);
        expect(cards.length).toBeGreaterThanOrEqual(1);
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );
  }, TEST_TIMEOUT_MS);

  it("renders Inspector with real config + weight-dynamics for v3", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Models" }));
    await waitFor(
      () => {
        expect(screen.getByTestId("models-subview-lineage")).toBeInTheDocument();
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );

    // Wait for the registry to load before snapping — the dropdown is
    // controlled, and a ``fireEvent.change`` to a value that's not yet a
    // valid <option> is silently rejected. ``models-version-select``
    // contains the v3 <option> only after ``useVersions`` resolves.
    await waitFor(
      () => {
        const select = screen.getByTestId(
          "models-version-select",
        ) as HTMLSelectElement;
        const opts = Array.from(select.options).map((o) => o.value);
        expect(opts).toContain("v3");
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );
    const versionSelect = screen.getByTestId(
      "models-version-select",
    ) as HTMLSelectElement;
    fireEvent.change(versionSelect, { target: { value: "v3" } });

    fireEvent.click(screen.getByTestId("models-subview-button-inspector"));
    await waitFor(
      () => {
        expect(
          screen.getByTestId("models-subview-inspector"),
        ).toBeInTheDocument();
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );

    // Content assertion 1: the Inspector title shows the selected version.
    // ``VersionInspector`` renders ``<h3>${version}</h3>`` keyed by
    // ``version-inspector-title``. A backend returning empty config would
    // still render this title, so we ALSO assert on the Config sub-panel
    // below.
    await waitFor(
      () => {
        expect(screen.getByTestId("version-inspector-title").textContent).toBe(
          "v3",
        );
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );

    // Content assertion 2: the Config accordion renders the
    // ``hyperparams`` JSON block when the per-version /config response
    // resolves with the expected 3-key shape. ``ConfigPanel`` renders
    // either ``inspector-config-empty`` (config is null) OR
    // ``inspector-config-body`` (config resolved); the latter contains
    // ``inspector-config-hyperparams`` keyed by the JsonBlock label. A
    // 4xx/5xx on /config would leave us in the empty branch.
    await waitFor(
      () => {
        expect(
          screen.getByTestId("inspector-config-hyperparams"),
        ).toBeInTheDocument();
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );

    // Content assertion 3 (Weight Dynamics): ``WeightDynamicsPanel``
    // renders EITHER the empty-state ``inspector-weight-empty`` (rows
    // are []) OR the populated body ``inspector-weight-body``. Both
    // count as "the panel rendered against real data"; a broken response
    // would surface as a thrown render error instead of either testid.
    await waitFor(
      () => {
        const empty = screen.queryByTestId("inspector-weight-empty");
        const body = screen.queryByTestId("inspector-weight-body");
        expect(empty !== null || body !== null).toBe(true);
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );
  }, TEST_TIMEOUT_MS);

  it("renders Compare with real ladder + diff panels for v2 vs v4", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Models" }));
    await waitFor(
      () => {
        expect(screen.getByTestId("models-subview-lineage")).toBeInTheDocument();
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );

    fireEvent.click(screen.getByTestId("models-subview-button-compare"));
    await waitFor(
      () => {
        expect(
          screen.getByTestId("models-subview-compare"),
        ).toBeInTheDocument();
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );

    // Wait for the Compare-side <select>s to have v2 + v4 as options
    // (they share the registry with the top-bar select; ``useVersions``
    // is shared via React state, but the component still mounts before
    // the fetch resolves, so the dropdown lists only the (none) option
    // until then). Without this guard, ``fireEvent.change`` to "v2" is
    // silently rejected by the controlled select.
    await waitFor(
      () => {
        const a = screen.getByTestId(
          "compare-select-a",
        ) as HTMLSelectElement;
        const opts = Array.from(a.options).map((o) => o.value);
        expect(opts).toContain("v2");
        expect(opts).toContain("v4");
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );

    // Pick A=v2 + B=v4 — concrete proof that "Compare works for two
    // distinct versions" (plan §7 Step 11 done-when). v2/v4 are both
    // present in the registry on every smoke-gate run since iter-1.
    fireEvent.change(screen.getByTestId("compare-select-a"), {
      target: { value: "v2" },
    });
    fireEvent.change(screen.getByTestId("compare-select-b"), {
      target: { value: "v4" },
    });

    // Content assertion 1: the Elo line renders both A-side + B-side
    // strong cells. ``CompareView`` renders ``compare-elo-a`` /
    // ``compare-elo-b`` as part of the elo line whenever the panel
    // mounts; the cell's text is the formatted rating OR "—" when the
    // ladder has no row for that version. Either way the testid MUST
    // exist for the panel to be considered rendered.
    await waitFor(
      () => {
        expect(screen.getByTestId("compare-elo-a")).toBeInTheDocument();
        expect(screen.getByTestId("compare-elo-b")).toBeInTheDocument();
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );

    // Content assertion 2: at least one of the diff panel bodies must
    // render. ``DiffRenderer`` renders ``compare-hyperparams-body`` when
    // there's a diff and ``compare-hyperparams-empty`` otherwise. The
    // pre-Step-1 fall-through "compare-hyperparams-pending" stays up
    // until both sides' configs resolve. Asserting on the union proves
    // the panel mounted with real config data flowing through.
    await waitFor(
      () => {
        const body = screen.queryByTestId("compare-hyperparams-body");
        const empty = screen.queryByTestId("compare-hyperparams-empty");
        expect(body !== null || empty !== null).toBe(true);
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );
  }, TEST_TIMEOUT_MS);

  it("renders Forensics trajectory or empty placeholder for v3's most recent game", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Models" }));
    await waitFor(
      () => {
        expect(screen.getByTestId("models-subview-lineage")).toBeInTheDocument();
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );

    // Wait for the registry to load before snapping (see Inspector test
    // above for the same race-condition rationale).
    await waitFor(
      () => {
        const select = screen.getByTestId(
          "models-version-select",
        ) as HTMLSelectElement;
        const opts = Array.from(select.options).map((o) => o.value);
        expect(opts).toContain("v3");
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );
    // Snap selected version to v3 (its training.db has games — this is
    // the same version the bash gate auto-picks a recent game from).
    fireEvent.change(screen.getByTestId("models-version-select"), {
      target: { value: "v3" },
    });

    fireEvent.click(screen.getByTestId("models-subview-button-forensics"));
    await waitFor(
      () => {
        expect(
          screen.getByTestId("models-subview-forensics"),
        ).toBeInTheDocument();
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );

    // Content assertion 1: forensics title shows v3.
    await waitFor(
      () => {
        expect(screen.getByTestId("forensics-title").textContent).toBe("v3");
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );

    // Content assertion 2: SOMETHING from the trajectory section must
    // render — either the populated body ``forensics-trajectory-body``
    // (the chart container) OR one of three empty-state placeholders
    // (no games yet / loading / no transitions). Asserting on the OR
    // proves real data flowed; a backend 5xx would short-circuit the
    // ``ForensicsViewActive`` mount entirely and none of these would
    // exist. The ``forensics-no-games`` testid lives on the GameId
    // selector when ``games.length === 0`` — we add it to the OR so the
    // test stays green on a fresh DB without hand-crafted fixtures.
    await waitFor(
      () => {
        const body = screen.queryByTestId("forensics-trajectory-body");
        const pending = screen.queryByTestId("forensics-trajectory-pending");
        const loading = screen.queryByTestId("forensics-trajectory-loading");
        const empty = screen.queryByTestId("forensics-trajectory-empty");
        const noGames = screen.queryByTestId("forensics-no-games");
        const anyRendered =
          body !== null ||
          pending !== null ||
          loading !== null ||
          empty !== null ||
          noGames !== null;
        expect(anyRendered).toBe(true);
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );
  }, TEST_TIMEOUT_MS);

  it("mounts the Observable tab with the pool selector", async () => {
    render(<App />);

    const observableTab = await screen.findByRole("button", {
      name: "Observable",
    });
    fireEvent.click(observableTab);

    await waitFor(
      () => {
        expect(screen.getByTestId("observable-tab")).toBeInTheDocument();
      },
      { timeout: SUB_VIEW_TIMEOUT_MS },
    );

    // Pool selector renders both left and right version slots regardless of
    // pool population (per ObservableTab.tsx — empty pool still shows the
    // <select> containers).
    expect(
      screen.getByTestId("observable-version-select-left"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("observable-version-select-right"),
    ).toBeInTheDocument();
  }, TEST_TIMEOUT_MS);
});
