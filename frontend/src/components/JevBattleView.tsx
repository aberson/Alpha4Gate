import { useEffect, useMemo, useRef, useState } from "react";
import { useJevRun } from "../hooks/useJevRun";
import { JevGraph } from "./JevGraph";
import { factNumber, factString, graphActivity } from "./jevEvidence";
import { isTerminalRunStatus, type JevNodeRuntime } from "../types/jev";
import "./JevTab.css";
import "./JevBattleView.css";

interface CasterLine { text: string; source: string; kind: string; game_seconds: number; node_ids?: string[] }
interface NativeState { recording: boolean; locked: boolean; automatic?: boolean; message: string; caster?: CasterLine }

declare global {
  interface Window {
    battleAction?: (action: string) => Promise<NativeState>;
  }
}

/** Windows feasibility surface. The probe attaches SC2 over the game slot. */
export function JevBattleView() {
  const { run, policy, reportRendered } = useJevRun();
  const [casterRotate, setCasterRotate] = useState(true);
  const [casterHighlight, setCasterHighlight] = useState(true);
  const [followDecisions, setFollowDecisions] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [native, setNative] = useState<NativeState>({ recording: false, locked: true, automatic: false, message: "Connecting viewer…" });
  const state = run.data;
  const archive = policy.data;
  const matching = state && archive?.policy_hash === state.policy_hash ? archive : null;
  const live = !!state && state.status === "running" && !state.stale && run.error === null;
  const activity = useMemo(() => state && matching ? graphActivity(state, matching) : undefined, [state, matching]);
  const previousActivity = useRef({ run: "", lit: new Set<string>() });
  useEffect(() => {
    if (!state || !matching || !activity) return;
    const identity = `${state.run_id}:${matching.policy_hash}`;
    const previous = previousActivity.current.run === identity ? previousActivity.current.lit : new Set<string>();
    const lit = new Set<string>();
    const changed = new Set<string>();
    const children = new Map<string, string[]>();
    for (const edge of activity.edges) {
      const key = `${edge.parent}>${edge.child}:${edge.kind}`;
      lit.add(key);
      if (!previous.has(key) && !(edge.kind === "recent" && previous.has(`${edge.parent}>${edge.child}:current`))) changed.add(edge.child);
      children.set(edge.parent, [...(children.get(edge.parent) ?? []), edge.child]);
    }
    for (const id of activity.followTargets) {
      lit.add(id);
      if (!previous.has(id)) changed.add(id);
    }
    if (!live || !followDecisions) {
      previousActivity.current = { run: identity, lit };
      return;
    }
    const ends = new Set<string>();
    const visited = new Set<string>();
    const visit = (id: string) => {
      if (visited.has(id)) return;
      visited.add(id);
      const next = children.get(id) ?? [];
      if (!next.length) ends.add(id);
      else next.forEach(visit);
    };
    changed.forEach(visit);
    const candidates = [...ends];
    // Coalesce incoming evidence into the next painted frame. Cancel on a newer
    // snapshot, without consuming its activity before it reaches the screen.
    const frame = requestAnimationFrame(() => {
      previousActivity.current = { run: identity, lit };
      if (candidates.length) setSelected(candidates[Math.floor(Math.random() * candidates.length)]);
    });
    return () => cancelAnimationFrame(frame);
  }, [state, matching, activity, live, followDecisions]);
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
        try { const value = await window.battleAction("status"); if (!disposed) setNative({ ...value, automatic: value.automatic ?? false }); }
        catch { if (!disposed) setNative((value) => ({ ...value, message: "Viewer disconnected" })); }
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, []);
  const action = async (name: string) => {
    try { if (window.battleAction) { const value = await window.battleAction(name); setNative({ ...value, automatic: value.automatic ?? false }); } }
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
  const recovery = decisions.get("strategy")?.facts.recovery_progress;
  const recoveryFacts = recovery && typeof recovery === "object" && !Array.isArray(recovery) ? recovery : null;
  const nodePath = useMemo(() => {
    if (!matching || !selected) return [];
    const byId = new Map(matching.nodes.map((node) => [node.id, node]));
    const parents = new Map<string, string>();
    for (const node of matching.nodes) for (const child of node.children) parents.set(child, node.id);
    const path = [];
    for (let id: string | undefined = selected; id; id = parents.get(id)) {
      const node = byId.get(id);
      if (!node) break;
      path.unshift(node);
    }
    return path;
  }, [matching, selected]);
  return <main className="jev-tab battle-view">
    <header className="battle-header">
      <strong>Alpha4Gate · Observer Rush</strong>
      <span>{state ? `${state.game_seconds.toFixed(1)}s · ${live ? "Live" : run.error ? "Offline" : state.stale ? "Stale" : state.status}` : "Preparing game"}</span>
      <span className="battle-native-status">{native.message}</span>
      <button aria-pressed={native.automatic} onClick={() => void action("auto")}>Auto camera</button>
      <button onClick={() => void action("lock")}>{native.locked ? "Unlock camera" : "Lock camera"}</button>
      <button className={native.recording ? "battle-recording" : ""} onClick={() => void action("record")}>
        {native.recording ? "Stop recording" : "Record Battle"}
      </button>
      <button onClick={() => void action("stop")}>Stop battle</button>
    </header>
    <section className="battle-game" id="battle-game-slot" aria-label="SC2 game">
      <p>Waiting for the SC2 window…</p>
    </section>
    <section className="battle-graph" aria-label="Live decision graph">
      {matching && state ? <JevGraph key={matching.policy_hash} policy={matching} compact
        layoutMode="dots3d" dotShape="faces" initiallyExpanded fitOnMount showLaneLegend={false}
        autoRotate={casterRotate} highlightedNodeIds={casterHighlight && live && native.caster?.kind === "commentary" ? native.caster.node_ids : undefined}
        runtime={runtime} live={live} selectedNodeId={selected} onSelectNode={(id) => { setSelected(id); setFollowDecisions(false); }}
        activity={activity} ended={isTerminalRunStatus(state.status)} />
        : <p>{run.error || policy.error ? "Run evidence unavailable" : "Loading this run’s graph…"}</p>}
      <div className="battle-inspection" aria-label="Selected node path">
        {nodePath.length ? <ol>{nodePath.map((node, index) => <li key={node.id}>
          {index > 0 && <span aria-hidden="true">→ </span>}<strong>{node.id}:</strong> {node.label}
        </li>)}</ol> : <p>Click a dot to inspect its path · Drag to rotate · Shift-drag to pan</p>}
      </div>
    </section>
    <div className="battle-summary">
    <section className="battle-log"><h3>Recent game events</h3>
      {events.length ? events.map((event) => <p key={event.sequence}>
        <time>{event.game_seconds.toFixed(1)}s</time> {event.node_id} · {event.reason}
      </p>) : <p>No command or task events recorded yet.</p>}
      <small>Run {state?.run_id ?? "preparing"} · {native.automatic ? "Automatic observer camera" : native.locked ? "Camera locked" : "Camera unlocked; auto-locks when the pointer leaves"}</small>
    </section>
    <section className="battle-decisions"><h3>{live ? "Current decisions" : "Last recorded decisions"}</h3>
      {[...decisions].map(([question, event]) => <p key={question}><strong>{question.replaceAll("_", " ")}</strong>
        <span>{factString(event.facts, "choice") ?? factString(event.facts, "answer") ?? (["local_graph", "scripted"].includes(factString(event.facts, "source") ?? "") ? "Local graph control" : "Waiting for a choice")}</span>
        <small>{factString(event.facts, "source") ?? "Source not recorded"}
          {factString(event.facts, "model") ? ` · ${factString(event.facts, "model")}` : ""}
          {factNumber(event.facts, "calls") !== null ? ` · ${factNumber(event.facts, "calls")} requests` : ""}
        </small></p>)}
      {recoveryFacts && <p><strong>Recovery</strong>
        <span>{factString(recoveryFacts, "status")?.replaceAll("_", " ")}</span>
        <small>{factNumber(recoveryFacts, "units_at_home")} / {factNumber(recoveryFacts, "ready_combat_units")} units home
          {` · ${factNumber(recoveryFacts, "settled_game_seconds")?.toFixed(1) ?? "0"}s settled`}</small>
      </p>}
    </section>
    </div>
    <section className="battle-caster" aria-label="Archon Auto Caster">
      <div className="battle-caster-heading"><h3>Archon Auto Caster <small>Two minds. Zero appearance fees.</small></h3>
        <button aria-pressed={followDecisions} title="Inspect the end of a newly lit path. Clicking a node pauses following." onClick={() => { previousActivity.current.lit.clear(); setFollowDecisions((v) => !v); }}>Follow decisions: {followDecisions ? "On" : "Off"}</button>
        <button aria-pressed={casterRotate} title="Slow rotation; pauses while inspecting the graph. Respects reduced motion." onClick={() => setCasterRotate((v) => !v)}>Rotate graph: {casterRotate ? "On" : "Off"}</button>
        <button aria-pressed={casterHighlight} title="Purple rings mark the commentary subject, not current execution." onClick={() => setCasterHighlight((v) => !v)}>Highlight nodes: {casterHighlight ? "On" : "Off"}</button></div>
      <p>{native.caster?.text ?? "Tuning into the Khala. Waiting for the local commentary feed."}</p>
      <small>{native.caster ? `${native.caster.source} \u00b7 ${native.caster.game_seconds.toFixed(1)}s` : "Local commentary only"} &middot; Entertainment, not gameplay control{!live && native.caster ? " \u00b7 Last broadcast" : ""}</small>
    </section>
  </main>;
}
