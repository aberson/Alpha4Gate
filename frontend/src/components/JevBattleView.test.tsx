import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { JevBattleView } from "./JevBattleView";
import type { JevRunState } from "../types/jev";
import policyText from "../../../bots/jev/v2/policy.json?raw";

const mocked = vi.hoisted(() => ({ view: {} as object, rendered: vi.fn() }));
vi.mock("../hooks/useJevRun", () => ({ useJevRun: () => mocked.view }));
afterEach(() => { cleanup(); mocked.rendered.mockClear(); vi.restoreAllMocks(); delete window.battleAction; });

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
  it("follows new path endpoints, keeps unchanged polls stable, and lets manual inspection pause it", async () => {
    const random = vi.spyOn(Math, "random").mockReturnValue(0);
    const view = setup("running");
    const update = (active_nodes: string[], stale = false) => {
      const current = mocked.view as { run: { data: JevRunState; error: null } };
      current.run = { ...current.run, data: { ...current.run.data, active_nodes, stale } };
      view.rerender(<JevBattleView />);
    };
    update(["scouting", "scouting.scout", "scouting.move"]);
    const path = screen.getByLabelText("Selected node path");
    await waitFor(() => expect(path).toHaveTextContent("scouting.move:"));
    random.mockClear();
    update(["scouting", "scouting.scout", "scouting.move"]);
    expect(random).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("treeitem", { name: /^army,/ }));
    expect(screen.getByRole("button", { name: "Follow decisions: Off" })).toBeInTheDocument();
    update(["production"]);
    expect(path).toHaveTextContent("army:");
    fireEvent.click(screen.getByRole("button", { name: "Follow decisions: Off" }));
    await waitFor(() => expect(path).toHaveTextContent("production:"));
    update(["scouting", "scouting.scout", "scouting.move"], true);
    expect(path).toHaveTextContent("production:");
  });

  it("randomly chooses among simultaneous path ends, never their common parent", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0.99);
    const view = setup("running");
    const current = mocked.view as { run: { data: JevRunState; error: null } };
    current.run.data = { ...current.run.data, active_nodes: ["scouting", "scouting.scout", "scouting.move", "production"] };
    view.rerender(<JevBattleView />);
    await waitFor(() => expect(screen.getByLabelText("Selected node path")).toHaveTextContent("production:"));
  });

  it("independently toggles caster rotation and evidence highlights without selecting a node", async () => {
    window.battleAction = vi.fn(async () => ({
      recording: false, locked: true, message: "Ready",
      caster: { text: "The scout is on the move.", source: "Local Qwen",
        kind: "commentary", game_seconds: 42, node_ids: ["scouting.move"] },
    }));
    setup("running");
    const node = screen.getByRole("treeitem", { name: /^scouting.move,/ });
    await waitFor(() => expect(node).toHaveAttribute("data-caster-highlight", "true"));
    expect(node).toHaveAttribute("aria-selected", "false");
    expect(node.querySelector(".jev-caster-halo")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Highlight nodes: On" }));
    expect(node).not.toHaveAttribute("data-caster-highlight");
    expect(screen.getByRole("button", { name: "Rotate graph: On" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Rotate graph: On" }));
    expect(screen.getByTestId("jev-graph")).toHaveAttribute("data-auto-rotating", "false");
  });

  it("attributes caster commentary separately and marks ended broadcasts", async () => {
    window.battleAction = vi.fn(async () => ({
      recording: false, locked: true, message: "Ready",
      caster: { text: "A Gateway is on the agenda.", source: "Local Qwen",
        kind: "commentary", game_seconds: 42 },
    }));
    setup("completed");
    const caster = screen.getByLabelText("Archon Auto Caster");
    await within(caster).findByText("A Gateway is on the agenda.");
    expect(caster).toHaveTextContent("Local Qwen · 42.0s");
    expect(caster).toHaveTextContent("Last broadcast");
  });

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
