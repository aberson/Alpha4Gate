import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { JevBattleView } from "./JevBattleView";
import type { JevRunState } from "../types/jev";
import policyText from "../../../bots/jev/v2/policy.json?raw";

const mocked = vi.hoisted(() => ({ view: {} as object, rendered: vi.fn() }));
vi.mock("../hooks/useJevRun", () => ({ useJevRun: () => mocked.view }));
afterEach(() => { cleanup(); mocked.rendered.mockClear(); delete window.battleAction; });

function setup(status: string, stale = false, mismatch = false, recentEvents: JevRunState["recent_events"] = []) {
  const policy = { ...JSON.parse(policyText), policy_hash: "a".repeat(64) };
  const state = {
    run_id: "a".repeat(32), policy_hash: mismatch ? "b".repeat(64) : policy.policy_hash,
    status, stale, game_seconds: 0, recent_events: recentEvents, active_nodes: [], waiting_nodes: [],
  } as unknown as JevRunState;
  mocked.view = { run: { data: state, error: null }, policy: { data: policy, error: null }, reportRendered: mocked.rendered };
  return render(<JevBattleView />);
}

describe("Battle feasibility view", () => {
  it("shows observer status and switches to manual control through the native bridge", async () => {
    window.battleAction = vi.fn(async (name) => ({
      recording: false, locked: name !== "lock", automatic: name !== "lock",
      message: name === "lock" ? "Camera unlocked" : "Auto camera · Scouting",
    }));
    setup("running");
    await waitFor(() => expect(screen.getByRole("button", { name: "Auto camera" })).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getByText("Auto camera · Scouting")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Unlock camera" }));
    await screen.findByRole("button", { name: "Lock camera" });
    expect(screen.getByRole("button", { name: "Auto camera" })).toHaveAttribute("aria-pressed", "false");
  });

  it("shows the real parent path even when node IDs do not encode that ancestry", () => {
    setup("running");
    fireEvent.click(screen.getByRole("treeitem", { name: /^scouting.move,/ }));
    const path = screen.getByLabelText("Selected node path");
    expect(within(path).getAllByRole("listitem").map((item) => item.querySelector("strong")?.textContent))
      .toEqual(["scouting:", "scouting.scout:", "scouting.move:"]);
    expect(path).not.toHaveTextContent("scouting.retreat:");
  });

  it("shows the recorded hosted model and calls, and keeps fallback attribution distinct", () => {
    const event: JevRunState["recent_events"][number] = {
      sequence: 1, game_loop: 1, game_seconds: 1, node_id: "army", task_id: null,
      status: "success", action: null, kind: "diagnostic", reason: "Army decision source", facts: {
      question_id: "army_mode", source: "typesafe", model: "jev-1.13.0", calls: 3, choice: "attack",
    } };
    setup("running", false, false, [event]);
    expect(screen.getByText("typesafe · jev-1.13.0 · 3 requests")).toBeInTheDocument();
    expect(screen.getByText("attack")).toBeInTheDocument();
    cleanup();
    setup("running", false, false, [{ ...event, facts: { ...event.facts, source: "scripted_fallback", model: null, choice: null } }]);
    expect(screen.getByText("scripted_fallback · 3 requests")).toBeInTheDocument();
    expect(screen.queryByText("typesafe · jev-1.13.0 · 3 requests")).not.toBeInTheDocument();
  });

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
