import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  cleanup,
  fireEvent,
} from "@testing-library/react";
import { CompareView } from "./CompareView";
import type { Version } from "../types/version";

/**
 * CompareView tests — Step 7 of the Models-tab build plan.
 *
 * Covers the four diff panels (Elo delta, Hyperparams diff, Reward
 * rules diff, Weight KL divergence), the A/B selector, the empty
 * state, and parent→child detection for KL.
 */

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

const VERSIONS: Version[] = [
  { name: "v0", race: "protoss", parent: null, harness_origin: "manual", timestamp: null, sha: null, fingerprint: null, current: false },
  { name: "v1", race: "protoss", parent: "v0", harness_origin: "evolve", timestamp: null, sha: null, fingerprint: null, current: false },
  { name: "v2", race: "protoss", parent: "v1", harness_origin: "evolve", timestamp: null, sha: null, fingerprint: null, current: true },
  { name: "v3", race: "protoss", parent: "v0", harness_origin: "advised", timestamp: null, sha: null, fingerprint: null, current: false },
];

const LADDER_RESP = {
  standings: [
    { version: "v0", elo: 1000, games: 20, wins: 10, losses: 10 },
    { version: "v1", elo: 1080, games: 20, wins: 14, losses: 6 },
    { version: "v2", elo: 1150, games: 20, wins: 17, losses: 3 },
    // v3 deliberately absent — exercises the "no rating" branch.
  ],
  head_to_head: {},
};

interface ConfigOverride {
  hyperparams: Record<string, unknown>;
  reward_rules: Record<string, unknown>;
  daemon_config: Record<string, unknown>;
}

interface WeightDynamicsOverride {
  rows: {
    checkpoint: string;
    ts: string | null;
    l2_per_layer: Record<string, number> | null;
    kl_from_parent: number | null;
    canary_source: string | null;
    error: string | null;
  }[];
}

interface FetchOverrides {
  configs?: Record<string, ConfigOverride>;
  weightDynamics?: Record<string, WeightDynamicsOverride>;
  ladder?: typeof LADDER_RESP;
}

function makeFetchMock(overrides: FetchOverrides = {}) {
  const ladder = overrides.ladder ?? LADDER_RESP;
  const configs = overrides.configs ?? {};
  const weightDynamics = overrides.weightDynamics ?? {};
  return vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/api/ladder")) {
      return jsonResponse(ladder);
    }
    const cfgMatch = url.match(/\/api\/versions\/(v\d+)\/config$/);
    if (cfgMatch) {
      const v = cfgMatch[1];
      return jsonResponse(
        configs[v] ?? {
          hyperparams: {},
          reward_rules: {},
          daemon_config: {},
        },
      );
    }
    if (/\/api\/versions\/v\d+\/training-history$/.test(url)) {
      return jsonResponse({ rolling_10: [], rolling_50: [], rolling_overall: [] });
    }
    if (/\/api\/versions\/v\d+\/actions$/.test(url)) {
      return jsonResponse([]);
    }
    if (/\/api\/versions\/v\d+\/improvements$/.test(url)) {
      return jsonResponse([]);
    }
    const wdMatch = url.match(/\/api\/versions\/(v\d+)\/weight-dynamics$/);
    if (wdMatch) {
      const v = wdMatch[1];
      return jsonResponse(weightDynamics[v]?.rows ?? []);
    }
    if (url.endsWith("/api/versions") || url.includes("/api/versions?")) {
      return jsonResponse(VERSIONS);
    }
    throw new Error(`Unexpected fetch: ${url}`);
  });
}

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(makeFetchMock());
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CompareView", () => {
  it("renders all four diff panels when A and B are populated", async () => {
    render(
      <CompareView compareA="v2" compareB="v1" onChange={() => undefined} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-panel-elo")).toBeInTheDocument();
      expect(screen.getByTestId("compare-panel-hyperparams")).toBeInTheDocument();
      expect(screen.getByTestId("compare-panel-reward-rules")).toBeInTheDocument();
      expect(screen.getByTestId("compare-panel-kl")).toBeInTheDocument();
    });
  });

  it("renders empty state when both A and B are null", async () => {
    render(
      <CompareView compareA={null} compareB={null} onChange={() => undefined} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-empty")).toBeInTheDocument();
    });
    // Panels should NOT render in the empty state.
    expect(screen.queryByTestId("compare-panels")).not.toBeInTheDocument();
  });

  it("A/B selectors populate from versions registry and fire onChange", async () => {
    const onChange = vi.fn();
    render(
      <CompareView compareA="v2" compareB="v1" onChange={onChange} />,
    );
    const selectA = (await screen.findByTestId(
      "compare-select-a",
    )) as HTMLSelectElement;
    const selectB = (await screen.findByTestId(
      "compare-select-b",
    )) as HTMLSelectElement;
    await waitFor(() => {
      // 4 versions + 1 placeholder option each.
      expect(selectA.querySelectorAll("option").length).toBe(5);
      expect(selectB.querySelectorAll("option").length).toBe(5);
    });
    expect(selectA.value).toBe("v2");
    expect(selectB.value).toBe("v1");

    fireEvent.change(selectA, { target: { value: "v3" } });
    expect(onChange).toHaveBeenCalledWith("v3", "v1");

    fireEvent.change(selectB, { target: { value: "v0" } });
    expect(onChange).toHaveBeenCalledWith("v2", "v0");
  });

  it("Elo delta computes correctly when both versions are on the ladder", async () => {
    render(
      <CompareView compareA="v2" compareB="v1" onChange={() => undefined} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-elo-a")).toHaveTextContent("1150");
    });
    expect(screen.getByTestId("compare-elo-b")).toHaveTextContent("1080");
    expect(screen.getByTestId("compare-elo-delta")).toHaveTextContent("Δ +70");
  });

  it("Elo panel falls back to '(no rating)' when one side is missing from ladder", async () => {
    render(
      <CompareView compareA="v2" compareB="v3" onChange={() => undefined} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-elo-delta-missing")).toBeInTheDocument();
    });
    // v3 has no entry → render dash.
    expect(screen.getByTestId("compare-elo-b")).toHaveTextContent("—");
  });

  it("Hyperparams diff highlights added / removed / modified", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        configs: {
          v1: {
            hyperparams: { lr: 0.001, batch_size: 64, gamma: 0.99 },
            reward_rules: {},
            daemon_config: {},
          },
          v2: {
            hyperparams: { lr: 0.0005, batch_size: 64, lambda_kl: 0.1 },
            reward_rules: {},
            daemon_config: {},
          },
        },
      }),
    );
    render(
      <CompareView compareA="v2" compareB="v1" onChange={() => undefined} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-hyperparams-body")).toBeInTheDocument();
    });
    // A=v2 has lambda_kl that v1 (B) doesn't → REMOVED from A→B
    // direction (A had it, B does not).
    expect(screen.getByTestId("compare-hyperparams-removed-lambda_kl")).toBeInTheDocument();
    // B=v1 has gamma that v2 (A) doesn't → ADDED.
    expect(screen.getByTestId("compare-hyperparams-added-gamma")).toBeInTheDocument();
    // lr changed from 0.0005 (A) to 0.001 (B) → modified.
    expect(screen.getByTestId("compare-hyperparams-modified-lr")).toBeInTheDocument();
  });

  it("Reward rules diff lists added / modified / removed", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        configs: {
          v1: {
            hyperparams: {},
            reward_rules: {
              base_step_reward: { enabled: true, weight: 1.0 },
              shield_battery: { enabled: false, weight: 0.5 },
              old_rule: { enabled: true, weight: 2.0 },
            },
            daemon_config: {},
          },
          v2: {
            hyperparams: {},
            reward_rules: {
              base_step_reward: { enabled: true, weight: 1.0 },
              shield_battery: { enabled: true, weight: 0.5 },
              new_rule: { enabled: true, weight: 3.0 },
            },
            daemon_config: {},
          },
        },
      }),
    );
    render(
      <CompareView compareA="v1" compareB="v2" onChange={() => undefined} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-reward-rules-body")).toBeInTheDocument();
    });
    // new_rule added (in B only).
    expect(screen.getByTestId("compare-reward-rules-added-new_rule")).toBeInTheDocument();
    // old_rule removed (in A only).
    expect(screen.getByTestId("compare-reward-rules-removed-old_rule")).toBeInTheDocument();
    // shield_battery.enabled modified.
    expect(
      screen.getByTestId("compare-reward-rules-modified-shield_battery.enabled"),
    ).toBeInTheDocument();
  });

  it("KL panel shows 'no direct lineage' for sibling pairs", async () => {
    // v1's parent is v0, v3's parent is also v0 — they're siblings.
    render(
      <CompareView compareA="v1" compareB="v3" onChange={() => undefined} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-kl-no-lineage")).toBeInTheDocument();
    });
  });

  it("KL panel shows pending placeholder when child rows are empty", async () => {
    // v1's parent is v0 → A=v0, B=v1 puts v1 as the child. Empty
    // weight-dynamics rows surface the pending placeholder.
    render(
      <CompareView compareA="v0" compareB="v1" onChange={() => undefined} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-kl-pending")).toBeInTheDocument();
    });
  });

  it("KL panel shows the kl_from_parent value when present on the child row", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        weightDynamics: {
          v1: {
            rows: [
              {
                checkpoint: "ckpt_001",
                ts: "2026-04-30T00:00:00Z",
                l2_per_layer: null,
                kl_from_parent: 0.0234,
                canary_source: null,
                error: null,
              },
            ],
          },
        },
      }),
    );
    render(
      <CompareView compareA="v0" compareB="v1" onChange={() => undefined} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-kl-number")).toHaveTextContent("0.0234");
    });
  });

  it("KL panel works in either A→B or B→A direction (B=child)", async () => {
    // A=v1 (child), B=v0 (parent) — same lineage, opposite ordering.
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchMock({
        weightDynamics: {
          v1: {
            rows: [
              {
                checkpoint: "ckpt_001",
                ts: null,
                l2_per_layer: null,
                kl_from_parent: 0.0567,
                canary_source: null,
                error: null,
              },
            ],
          },
        },
      }),
    );
    render(
      <CompareView compareA="v1" compareB="v0" onChange={() => undefined} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-kl-number")).toHaveTextContent("0.0567");
    });
  });
});
