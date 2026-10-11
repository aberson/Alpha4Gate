import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { JevBattleView } from "./JevBattleView";
import type { JevRunState } from "../types/jev";
import policyText from "../../../bots/jev/v2/policy.json?raw";

const mocked = vi.hoisted(() => ({ view: {} as object, rendered: vi.fn() }));
vi.mock("../hooks/useJevRun", () => ({ useJevRun: () => mocked.view }));
afterEach(() => { cleanup(); mocked.rendered.mockClear(); });

function setup(status: string, stale = false, mismatch = false) {
  const policy = { ...JSON.parse(policyText), policy_hash: "a".repeat(64) };
  const state = {
    run_id: "a".repeat(32), policy_hash: mismatch ? "b".repeat(64) : policy.policy_hash,
    status, stale, game_seconds: 0, recent_events: [], active_nodes: [], waiting_nodes: [],
  } as unknown as JevRunState;
  mocked.view = { run: { data: state, error: null }, policy: { data: policy, error: null }, reportRendered: mocked.rendered };
  return render(<JevBattleView />);
}

describe("Battle feasibility view", () => {
  it("renders all 292 branch-face nodes before acknowledging the exact starting run", () => {
    setup("starting");
    expect(screen.getAllByRole("treeitem")).toHaveLength(292);
    expect(screen.getByTestId("jev-graph")).toHaveAttribute("data-shape", "faces");
    expect(screen.getByText("0.0s · starting")).toBeInTheDocument();
    expect(screen.queryByText("0.0s · Live")).not.toBeInTheDocument();
    expect(mocked.rendered).toHaveBeenCalledWith("a".repeat(32), "a".repeat(64));
  });

  it("does not display or acknowledge a graph for the wrong policy hash", () => {
    setup("running", false, true);
    expect(screen.queryByTestId("jev-graph")).not.toBeInTheDocument();
    expect(mocked.rendered).not.toHaveBeenCalled();
  });

  it("labels a stalled producer as stale rather than live", () => {
    setup("running", true);
    expect(screen.getByText("0.0s · Stale")).toBeInTheDocument();
    expect(screen.getByText("Last recorded decisions")).toBeInTheDocument();
  });
});
