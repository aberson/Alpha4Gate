import { useEffect, useMemo, useState } from "react";
import { useJevRun } from "../hooks/useJevRun";
import { JevGraph } from "./JevGraph";
import { factString, graphActivity } from "./jevEvidence";
import { isTerminalRunStatus, type JevNodeRuntime } from "../types/jev";
import "./JevTab.css";
import "./JevBattleView.css";

declare global {
  interface Window {
    battleAction?: (action: string) => Promise<{ recording: boolean; locked: boolean; message: string }>;
  }
}

/** Windows feasibility surface. The probe attaches SC2 over the game slot. */
export function JevBattleView() {
  const { run, policy, reportRendered } = useJevRun();
  const [selected, setSelected] = useState<string | null>(null);
  const [native, setNative] = useState({ recording: false, locked: true, message: "Connecting viewer…" });
  const state = run.data;
  const archive = policy.data;
  const matching = state && archive?.policy_hash === state.policy_hash ? archive : null;
  const live = !!state && state.status === "running" && !state.stale && run.error === null;
  const runtime = useMemo(() => {
    const result = new Map<string, JevNodeRuntime>();
    for (const event of state?.recent_events ?? []) {
      if (event.kind === "node" && ["success", "failure", "running"].includes(event.status ?? "")) {
        result.set(event.node_id, event.status as JevNodeRuntime);
      }
    }
    for (const id of state?.active_nodes ?? []) result.set(id, "active");
    for (const id of state?.waiting_nodes ?? []) result.set(id, "waiting");
    return result;
  }, [state]);
  useEffect(() => {
    if (state && matching) reportRendered(state.run_id, matching.policy_hash);
  }, [state, matching, reportRendered]);
  useEffect(() => {
    let disposed = false;
    const poll = async () => {
      if (window.battleAction) {
        try { const value = await window.battleAction("status"); if (!disposed) setNative(value); }
        catch { if (!disposed) setNative((value) => ({ ...value, message: "Viewer disconnected" })); }
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, []);
  const action = async (name: string) => {
    try { if (window.battleAction) setNative(await window.battleAction(name)); }
    catch { setNative((value) => ({ ...value, message: "Viewer action failed" })); }
  };
  const decisions = new Map<string, NonNullable<typeof state>["recent_events"][number]>();
  for (const event of state?.recent_events ?? []) {
    const question = factString(event.facts, "question_id") ?? (event.reason === "Army decision source" ? "army_mode" : null);
    if (question && event.kind === "diagnostic") decisions.set(question, event);
  }
  const events = (state?.recent_events ?? []).filter((event) =>
    event.kind === "command" || event.kind === "task",
  ).slice(-6).reverse();
  const node = matching?.nodes.find((item) => item.id === selected);
  return <main className="jev-tab battle-view">
    <header className="battle-header">
      <strong>Alpha4Gate · Jev Battle</strong>
      <span>{state ? `${state.game_seconds.toFixed(1)}s · ${live ? "Live" : run.error ? "Offline" : state.stale ? "Stale" : state.status}` : "Preparing game"}</span>
      <span className="battle-native-status">{native.message}</span>
      <button onClick={() => void action("lock")}>{native.locked ? "Unlock camera" : "Lock camera"}</button>
      <button className={native.recording ? "battle-recording" : ""} onClick={() => void action("record")}>
        {native.recording ? "Stop recording" : "Record Battle"}
      </button>
    </header>
    <section className="battle-game" id="battle-game-slot" aria-label="SC2 game">
      <p>Waiting for the SC2 window…</p>
    </section>
    <section className="battle-graph" aria-label="Live decision graph">
      {matching && state ? <JevGraph key={matching.policy_hash} policy={matching} compact
        layoutMode="dots3d" dotShape="faces" initiallyExpanded fitOnMount showLaneLegend={false}
        runtime={runtime} live={live} selectedNodeId={selected} onSelectNode={setSelected}
        activity={graphActivity(state, matching)} ended={isTerminalRunStatus(state.status)} />
        : <p>{run.error || policy.error ? "Run evidence unavailable" : "Loading this run’s graph…"}</p>}
      <p className="battle-inspection">{node ? `${node.id}: ${node.label}` : "Click a dot to inspect · Drag to rotate · Shift-drag to pan"}</p>
    </section>
    <section className="battle-log"><h3>Recent game events</h3>
      {events.length ? events.map((event) => <p key={event.sequence}>
        <time>{event.game_seconds.toFixed(1)}s</time> {event.node_id} · {event.reason}
      </p>) : <p>No command or task events recorded yet.</p>}
      <small>Run {state?.run_id ?? "preparing"} · {native.locked ? "Camera locked" : "Camera unlocked; auto-locks when the pointer leaves"}</small>
    </section>
    <section className="battle-decisions"><h3>{live ? "Current decisions" : "Last recorded decisions"}</h3>
      {[...decisions].map(([question, event]) => <p key={question}><strong>{question.replaceAll("_", " ")}</strong>
        <span>{factString(event.facts, "choice") ?? factString(event.facts, "answer") ?? (["local_graph", "scripted"].includes(factString(event.facts, "source") ?? "") ? "Local graph control" : "Waiting for a choice")}</span>
        <small>{factString(event.facts, "source") ?? "Source not recorded"}</small></p>)}
    </section>
  </main>;
}
