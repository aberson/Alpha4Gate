import { useMemo, useState } from "react";
import { useJevRun, type JevLoadError, type JevResource } from "../hooks/useJevRun";
import { JevGraph } from "./JevGraph";
import {
  isTerminalRunStatus,
  nodeRuntimeLabel,
  type JevEvent,
  type JevNodeRuntime,
  type JevPolicy,
  type JevPolicyNode,
  type JevRunList,
  type JevRunState,
  type JevTarget,
  type JevTask,
  type JsonValue,
} from "../types/jev";
import "./JevTab.css";

/**
 * Jev tab — read-only browser for Jev runs (plan Step 205, design D5).
 *
 * Pick a run, follow its archived policy as a graph, and inspect a node:
 * its policy definition (from the run's archived policy, never a source
 * policy), its latest evaluations and command events, and its tasks with
 * their deadlines. The recent trace lists the run's latest events and labels
 * each status change as a runtime transition.
 *
 * Every run state is a recorded snapshot, so the tab says what it shows:
 * live (a running producer, refreshed while the tab is open), stale (the producer
 * stopped writing heartbeats), offline (the dashboard API cannot be
 * reached), or final. Only a live run animates. All stored text renders as
 * plain text, and every list is capped below.
 */

/** Events per node, tasks per node, waiting IDs named, and trace rows shown. */
const NODE_EVENT_ROWS = 5;
const NODE_TASK_ROWS = 10;
const WAITING_NAMED = 10;
const TRACE_ROWS = 40;
/** A JSON value longer than this is cut when shown. */
const JSON_PREVIEW_CHARS = 2000;
/** The events of a run whose state has not loaded (one stable identity). */
const NO_EVENTS: JevEvent[] = [];

function describeError(error: JevLoadError): string {
  switch (error.kind) {
    case "network":
      return error.message;
    case "http":
      return `HTTP ${error.status}${error.code === null ? "" : ` ${error.code}`}: ${error.message}`;
    case "contract":
      return `invalid or unsupported response: ${error.message}`;
  }
}

function formatTime(timestamp: string | Date): string {
  return new Date(timestamp).toLocaleTimeString();
}

function formatSeconds(seconds: number): string {
  return `${seconds.toFixed(1)} s`;
}

function formatTarget(target: JevTarget): string {
  if (target === null) return "none";
  if (typeof target === "string") return `unit ${target}`;
  return `point (${target[0]}, ${target[1]})`;
}

function formatJson(value: JsonValue): string {
  const text = JSON.stringify(value, null, 2);
  return text.length > JSON_PREVIEW_CHARS
    ? `${text.slice(0, JSON_PREVIEW_CHARS)}\n… (cut at ${JSON_PREVIEW_CHARS} characters)`
    : text;
}

/** Runtime status per node: the latest node evaluation, overridden by active/waiting. */
function nodeRuntimes(state: JevRunState): Map<string, JevNodeRuntime> {
  const runtime = new Map<string, JevNodeRuntime>();
  for (const event of state.recent_events) {
    if (
      event.kind === "node" &&
      (event.status === "success" || event.status === "failure" || event.status === "running")
    ) {
      runtime.set(event.node_id, event.status);
    }
  }
  for (const id of state.active_nodes) runtime.set(id, "active");
  for (const id of state.waiting_nodes) runtime.set(id, "waiting");
  return runtime;
}

/**
 * Runtime transitions: ``previous → current`` for each node event (task
 * event) whose status differs from the same node's (task's) previous event
 * in the trace. Events that changed nothing have no entry.
 */
function transitions(events: JevEvent[]): Map<JevEvent, string> {
  const labels = new Map<JevEvent, string>();
  const last = new Map<string, string>();
  for (const event of events) {
    let key: string | null = null;
    if (event.kind === "node") key = `node:${event.node_id}`;
    else if (event.kind === "task" && event.task_id !== null) key = `task:${event.task_id}`;
    if (key === null) continue;
    const previous = last.get(key);
    if (previous !== undefined && previous !== event.status) {
      labels.set(event, `${previous} → ${event.status}`);
    }
    last.set(key, event.status);
  }
  return labels;
}

export function JevTab() {
  const { runs, selectedRunId, selectRun, run, policy } = useJevRun();
  return (
    <div className="jev-tab" data-testid="jev-tab">
      <h2>Jev decision graph</h2>
      <p className="jev-tab-intro">
        Read-only view of Jev runs: the policy each run archived, and the latest state its game
        process recorded.
      </p>
      <RunPicker runs={runs} selectedRunId={selectedRunId} onSelect={selectRun} />
      {selectedRunId !== null && (
        <RunView key={selectedRunId} runId={selectedRunId} run={run} policy={policy} />
      )}
    </div>
  );
}

interface RunPickerProps {
  runs: JevResource<JevRunList>;
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
}

function RunPicker({ runs, selectedRunId, onSelect }: RunPickerProps) {
  const list = runs.data;
  if (list === null) {
    if (runs.error === null) {
      return <p role="status">Loading Jev runs…</p>;
    }
    return (
      <p role="alert" className="jev-notice jev-notice-error">
        Could not load the Jev run list: {describeError(runs.error)}
      </p>
    );
  }
  const listed = list.runs.some((summary) => summary.run_id === selectedRunId);
  return (
    <section className="jev-run-picker" aria-label="Jev runs">
      {runs.error !== null && (
        <p role="status" className="jev-notice jev-notice-warning">
          The run list could not be refreshed ({describeError(runs.error)}); showing the list
          received at {runs.lastSuccess === null ? "an unknown time" : formatTime(runs.lastSuccess)}.
        </p>
      )}
      {list.runs.length === 0 ? (
        <div className="jev-empty" data-testid="jev-empty">
          <p>
            <strong>No Jev runs yet.</strong>
          </p>
          <p>
            A run appears here once the Jev runner starts a match (
            <code>uv run python -m bots.jev.v1</code>) and records it under{" "}
            <code>data/jev/runs</code>.
          </p>
        </div>
      ) : (
        <label className="jev-run-select">
          Run{" "}
          <select
            data-testid="jev-run-select"
            value={selectedRunId ?? ""}
            onChange={(event) => onSelect(event.target.value)}
          >
            {selectedRunId !== null && !listed && (
              <option value={selectedRunId}>{selectedRunId} (no longer listed)</option>
            )}
            {list.runs.map((summary) => (
              <option key={summary.run_id} value={summary.run_id}>
                {[
                  summary.run_id.slice(0, 8),
                  `v${summary.version}.${summary.family}`,
                  summary.status,
                  `updated ${formatTime(summary.updated_at)}`,
                ].join(" · ")}
              </option>
            ))}
          </select>
        </label>
      )}
      {list.truncated && (
        <p className="jev-note">Showing the newest {list.runs.length} runs; older runs exist.</p>
      )}
      {list.omitted > 0 && (
        <p className="jev-note">
          {list.omitted} run(s) not listed because a stored record is malformed.
        </p>
      )}
    </section>
  );
}

interface RunViewProps {
  runId: string;
  run: JevResource<JevRunState>;
  policy: JevResource<JevPolicy>;
}

/** One run's view; keyed by run ID, so switching runs discards all of its state. */
function RunView({ runId, run, policy }: RunViewProps) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const state = run.data;
  const events = state?.recent_events ?? NO_EVENTS;
  const runtime = useMemo(
    () => (state === null ? new Map<string, JevNodeRuntime>() : nodeRuntimes(state)),
    [state],
  );

  if (state === null) {
    if (run.error === null) return <p role="status">Loading run {runId}…</p>;
    return (
      <p role="alert" className="jev-notice jev-notice-error">
        Could not load run {runId}: {describeError(run.error)}
      </p>
    );
  }
  const live = !isTerminalRunStatus(state.status) && !state.stale && run.error === null;
  const archived = policy.data;
  const selectedNode =
    archived?.nodes.find((node) => node.id === selectedNodeId) ?? null;

  let graph;
  if (archived === null) {
    graph =
      policy.error === null ? (
        <p role="status">Loading the archived policy…</p>
      ) : (
        <p role="alert" className="jev-notice jev-notice-error">
          Could not load the run's archived policy: {describeError(policy.error)}
        </p>
      );
  } else if (archived.policy_hash !== state.policy_hash) {
    graph = (
      <p role="alert" className="jev-notice jev-notice-error" data-testid="jev-hash-mismatch">
        Policy hash mismatch: the run state names <code>{state.policy_hash}</code> but its archived
        policy is <code>{archived.policy_hash}</code>. The graph is not shown, because its nodes
        might not be the ones this run executed.
      </p>
    );
  } else {
    graph = (
      <div className="jev-run-body">
        <JevGraph
          policy={archived}
          runtime={runtime}
          live={live}
          selectedNodeId={selectedNodeId}
          onSelectNode={setSelectedNodeId}
        />
        <NodeInspector
          node={selectedNode}
          live={live}
          runtime={selectedNode === null ? "idle" : (runtime.get(selectedNode.id) ?? "idle")}
          events={events}
          tasks={state.tasks}
        />
      </div>
    );
  }

  return (
    <>
      <RunSummary state={state} live={live} run={run} />
      {graph}
      <RecentTrace events={events} />
    </>
  );
}

interface RunSummaryProps {
  state: JevRunState;
  live: boolean;
  run: JevResource<JevRunState>;
}

function RunSummary({ state, live, run }: RunSummaryProps) {
  const { metadata, trace } = state;
  const waitingShown = state.waiting_nodes.slice(0, WAITING_NAMED);
  return (
    <section className="jev-run-summary" aria-label="Run summary" data-testid="jev-run-summary">
      <div className="jev-badges">
        <span className={`jev-badge jev-badge-${state.status}`} data-testid="jev-run-status">
          Status: {state.status}
        </span>
        {live && (
          <span className="jev-badge jev-badge-live" data-testid="jev-live-badge">
            Live
          </span>
        )}
        {state.stale && (
          <span className="jev-badge jev-badge-stale" data-testid="jev-stale-badge">
            Stale
          </span>
        )}
        {run.error !== null && (
          <span className="jev-badge jev-badge-offline" data-testid="jev-offline-badge">
            Offline
          </span>
        )}
        {state.result !== null && (
          <span className="jev-badge" data-testid="jev-run-result">
            Result: {state.result}
          </span>
        )}
      </div>
      {state.stale && (
        <p role="status" className="jev-notice jev-notice-warning">
          Stale: this run's game process stopped writing heartbeats without recording a final
          state. Showing its last recorded state; nothing here is live.
        </p>
      )}
      {run.error !== null && (
        <p role="status" className="jev-notice jev-notice-warning">
          Offline: the run could not be refreshed ({describeError(run.error)}). Showing the state
          received at {run.lastSuccess === null ? "an unknown time" : formatTime(run.lastSuccess)};
          nothing here is live.
        </p>
      )}
      {state.error !== null && (
        <p role="alert" className="jev-notice jev-notice-error" data-testid="jev-run-error">
          Run {state.status === "failed" ? "failed" : "error"}: <code>{state.error.code}</code>{" "}
          {state.error.message}
        </p>
      )}
      {state.waiting_nodes.length > 0 && (
        <p className="jev-notice jev-notice-waiting" data-testid="jev-waiting">
          {isTerminalRunStatus(state.status)
            ? `Waiting when the run ended: ${state.waiting_nodes.length} node(s) —`
            : `Waiting: ${state.waiting_nodes.length} node(s) cannot proceed yet —`}{" "}
          {waitingShown.join(", ")}
          {state.waiting_nodes.length > waitingShown.length &&
            ` and ${state.waiting_nodes.length - waitingShown.length} more`}
          .
        </p>
      )}
      <dl className="jev-facts">
        <dt>Run</dt>
        <dd>
          <code data-testid="jev-run-id">{state.run_id}</code>
        </dd>
        <dt>Player</dt>
        <dd>
          v{state.version}.{state.family}
        </dd>
        <dt>Policy hash</dt>
        <dd>
          <code data-testid="jev-run-hash">{state.policy_hash}</code>
        </dd>
        <dt>Recorded</dt>
        <dd>
          {formatTime(state.updated_at)} · game time {formatSeconds(state.game_seconds)} · event{" "}
          {state.last_sequence}
        </dd>
        <dt>Nodes</dt>
        <dd>
          {state.active_nodes.length} active · {state.waiting_nodes.length} waiting
          {live ? "" : " (last known)"} · {state.tasks.length + state.tasks_omitted} active
          task(s)
          {state.tasks_omitted > 0 && ` (${state.tasks_omitted} not listed)`}
        </dd>
        <dt>Match</dt>
        <dd>
          {metadata.map} vs {metadata.opponent_race}, difficulty {metadata.difficulty}, seed{" "}
          {metadata.seed}; limits {metadata.max_game_seconds} game s / {metadata.max_wall_seconds}{" "}
          wall s
        </dd>
        <dt>Replay</dt>
        <dd>{metadata.replay_path ?? "none recorded"}</dd>
        <dt>Trace</dt>
        <dd>
          {trace.events} event(s) traced
          {trace.complete
            ? ", complete"
            : `, incomplete (${trace.dropped_events} event(s) dropped)`}
        </dd>
      </dl>
    </section>
  );
}

interface NodeInspectorProps {
  node: JevPolicyNode | null;
  live: boolean;
  runtime: JevNodeRuntime;
  events: JevEvent[];
  tasks: JevTask[];
}

function NodeInspector({ node, live, runtime, events, tasks }: NodeInspectorProps) {
  if (node === null) {
    return (
      <aside className="jev-inspector" aria-label="Node details" data-testid="jev-node-panel">
        <p className="jev-note">Select a node in the graph to inspect it.</p>
      </aside>
    );
  }
  const nodeEvents = events.filter((event) => event.node_id === node.id);
  const latest = nodeEvents.findLast((event) => event.kind === "node") ?? null;
  const recent = nodeEvents.slice(-NODE_EVENT_ROWS).reverse();
  const nodeTasks = tasks.filter((task) => task.node_id === node.id);
  return (
    <aside className="jev-inspector" aria-label="Node details" data-testid="jev-node-panel">
      <h3>{node.label}</h3>
      <dl className="jev-facts">
        <dt>Node ID</dt>
        <dd>
          <code data-testid="jev-node-id">{node.id}</code>
        </dd>
        <dt>Kind</dt>
        <dd>{node.kind}</dd>
        <dt>Operation</dt>
        <dd>{node.operation ?? "none (composite)"}</dd>
        <dt>Status</dt>
        <dd>{nodeRuntimeLabel(runtime, live)}</dd>
      </dl>
      <h4>Arguments</h4>
      <pre>{formatJson(node.args)}</pre>
      <h4>Tasks</h4>
      {nodeTasks.length === 0 ? (
        <p className="jev-note">No active task for this node.</p>
      ) : (
        <ul className="jev-node-tasks">
          {nodeTasks.slice(0, NODE_TASK_ROWS).map((task) => (
            <li key={task.id} data-testid="jev-node-task">
              <code>{task.id}</code> · <strong>{task.status}</strong> · actor{" "}
              {task.actor_tag ?? "none"} · target {formatTarget(task.target)} ·{" "}
              {task.deadline_game_seconds === null
                ? "no deadline"
                : `deadline ${formatSeconds(task.deadline_game_seconds)}`}{" "}
              · attempt {task.attempts} — {task.reason}
            </li>
          ))}
        </ul>
      )}
      <h4>Latest evaluation</h4>
      {latest === null ? (
        <p className="jev-note">No evaluation of this node among the run's recent events.</p>
      ) : (
        <>
          <p>
            {latest.status} at {formatSeconds(latest.game_seconds)} (event {latest.sequence}):{" "}
            {latest.reason}
          </p>
          <pre>{formatJson(latest.facts)}</pre>
        </>
      )}
      <h4>Recent events</h4>
      {recent.length === 0 ? (
        <p className="jev-note">No recent events for this node.</p>
      ) : (
        <ol className="jev-node-events">
          {recent.map((event) => (
            <li key={event.sequence} data-testid="jev-node-event">
              <p>
                #{event.sequence} · {formatSeconds(event.game_seconds)} · {event.kind} ·{" "}
                <strong>{event.status}</strong> — {event.reason}
              </p>
              <pre>{`facts: ${formatJson(event.facts)}`}</pre>
              {event.action !== null && <pre>{`action: ${formatJson(event.action)}`}</pre>}
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}

function RecentTrace({ events }: { events: JevEvent[] }) {
  const labels = useMemo(() => transitions(events), [events]);
  const rows = events.slice(-TRACE_ROWS).reverse();
  return (
    <section className="jev-trace" aria-label="Recent trace" data-testid="jev-trace">
      <h3>Recent trace</h3>
      <p className="jev-note">
        The run's latest events, newest first. A status change since the same node's or task's
        previous event is labeled as a runtime transition.
      </p>
      {rows.length === 0 ? (
        <p className="jev-note">No events recorded yet.</p>
      ) : (
        <div className="jev-trace-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Event</th>
                <th scope="col">Game time</th>
                <th scope="col">Kind</th>
                <th scope="col">Node</th>
                <th scope="col">Status</th>
                <th scope="col">Transition</th>
                <th scope="col">Reason</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((event) => (
                <tr key={event.sequence} data-testid="jev-trace-row">
                  <td>{event.sequence}</td>
                  <td>{formatSeconds(event.game_seconds)}</td>
                  <td>{event.kind}</td>
                  <td>
                    <code>{event.node_id}</code>
                  </td>
                  <td>{event.status}</td>
                  <td>{labels.get(event) ?? ""}</td>
                  <td>{event.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
