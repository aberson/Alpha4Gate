import { useEffect, useMemo, useRef, useState } from "react";
import {
  useJevRun,
  type JevLaunchView,
  type JevLink,
  type JevLoadError,
  type JevResource,
} from "../hooks/useJevRun";
import { JevGraph } from "./JevGraph";
import { PlanPanel } from "./JevPlanPanel";
import {
  NO_ACTIVITY,
  eventSource,
  factBoolean,
  factNumber,
  factString,
  formatSeconds,
  graphActivity,
  pathGuards,
  plansEvidence,
  type JevNodeSource,
} from "./jevEvidence";
import {
  JEV_DECISION_REASONS,
  isTerminalRunStatus,
  laneToneClass,
  launchLiveness,
  launchPhase,
  nodeRuntimeLabel,
  policyLanes,
  type JevLaunchFacts,
  type JevLaunchPhase,
  type JevShownRun,
  type JevEvent,
  type JevLaunchState,
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
 *
 * Opened by a dashboard-first launch (``?tab=jev&launch=<session_id>``, plan
 * D7) the tab follows that launch session's exact run: it shows Preparing
 * until the session names its run, acknowledges readiness only after that run
 * and its archived policy are rendered, then Starting until the game reports
 * live observations, Live, and Finished with the result; a batch moves on to
 * the next game in the same tab. Picking another run pauses following until
 * Resume live. ``?tab=jev&run=<run_id>`` shows exactly that run.
 *
 * Plan J2 Step 221 (D5) explains what Jev is doing and why progress waits: the
 * graph colours each root lane and draws only the edge activity the evidence
 * records (the latest state's running path, and node events of the last two game
 * seconds as a fading "recent" trail), with an opt-in "Follow activity"; a v2 run
 * adds a Plans and decisions panel (each plan's stage, blocker or outcome, the
 * slot's turn and what may start next, each question's source and request); and
 * the node inspector names the request, fallback or local decision behind a node.
 * Older and v1 runs record no plan or v2 decision facts: they get lanes and recorded
 * edges, but no plans panel and no source beyond their own army diagnostic.
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
const ARMY_DECISION_REASON = JEV_DECISION_REASONS.army_mode;
/** Recorded source evidence listed per node in the inspector. */
const NODE_SOURCE_ROWS = 5;

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

function sourceLabel(source: string | null): string {
  if (source === "typesafe") return "Typesafe response";
  if (source === "local_fallback") return "local fallback";
  if (source === "scripted_fallback") return "Scripted fallback";
  if (source === "scripted") return "Scripted";
  return source ?? "unknown";
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

interface BranchEvidence {
  root: string;
  active: string[];
  waiting: string[];
  blocker: JevEvent | null;
}

/** Summarize a root only from the latest state and recorded events. */
function branchEvidence(
  policy: JevPolicy,
  state: JevRunState,
  lanes: ReadonlyMap<string, string>,
): BranchEvidence[] {
  return policy.roots.map((root) => {
    const active = state.active_nodes.filter((id) => lanes.get(id) === root);
    const waiting = state.waiting_nodes.filter((id) => lanes.get(id) === root);
    const waitingSet = new Set(waiting);
    const blocker =
      state.recent_events.findLast(
        (event) => waitingSet.has(event.node_id) && event.reason.trim().length > 0,
      ) ?? null;
    return { root, active, waiting, blocker };
  });
}

function BranchOverview({
  branches,
  selectedRoot,
  live,
  ended,
  onSelect,
}: {
  branches: BranchEvidence[];
  selectedRoot: string;
  live: boolean;
  ended: boolean;
  onSelect: (root: string) => void;
}) {
  const evidenceLabel = live ? "Live" : ended ? "When run ended" : "Last known";
  const activeBranches = branches.filter(
    (branch) => branch.active.length + branch.waiting.length > 0,
  );
  const laneWhen = live ? "" : ended ? " when the run ended" : " (last known)";
  return (
    <section className="jev-branches" aria-labelledby="jev-branches-heading">
      <div className="jev-branches-title">
        <h3 id="jev-branches-heading">Branch overview</h3>
        <span className="jev-badge">{evidenceLabel} evidence</span>
      </div>
      <p className="jev-branch-summary" data-testid="jev-graph-active-lanes">
        {activeBranches.length === 0
          ? `No lane active${laneWhen}`
          : `${activeBranches.length} lane(s) active${laneWhen}: ${activeBranches
              .map((branch) => branch.root)
              .join(", ")}`}
      </p>
      <div className="jev-branch-grid">
        {branches.map((branch, index) => {
          const current = branch.active.filter((id) => id !== branch.root);
          const action = current.at(-1) ?? branch.active.at(-1) ?? null;
          const blockerNode = branch.blocker?.node_id ?? branch.waiting.at(-1) ?? null;
          const status = branch.active.length > 0 ? "active" : branch.waiting.length > 0 ? "waiting" : "idle";
          return (
            <button
              key={branch.root}
              type="button"
              className={`jev-branch-card ${laneToneClass(branch.root, index)}`}
              data-testid="jev-graph-lane"
              data-lane={branch.root}
              data-status={status}
              data-live={live && status !== "idle"}
              aria-pressed={branch.root === selectedRoot}
              onClick={() => onSelect(branch.root)}
            >
              <span className="jev-branch-card-title">
                <span className="jev-graph-lane-swatch" aria-hidden="true" />
                {branch.root}
                <span className={`jev-branch-state jev-branch-state-${status}`}>{status}</span>
              </span>
              <span className="jev-branch-card-line">
                {action === null ? "No current action recorded" : `Action: ${action}`}
              </span>
              <span className="jev-branch-card-line">
                {blockerNode === null
                  ? "No blocker recorded"
                  : `Blocked: ${blockerNode}${branch.blocker === null ? "" : ` — ${branch.blocker.reason}`}`}
              </span>
              <span className="jev-branch-card-counts">
                {branch.active.length} active · {branch.waiting.length} waiting
                {live ? "" : ended ? " when ended" : " (last known)"}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function RecentDecisions({ events, live }: { events: JevEvent[]; live: boolean }) {
  const decisions = events
    .filter((event) => {
      const question = factString(event.facts, "question_id");
      const choice = factString(event.facts, "choice");
      const source = factString(event.facts, "source");
      return question !== null && (choice !== null || source !== null);
    })
    .slice(-6)
    .reverse();
  return (
    <section className="jev-recent-decisions" aria-labelledby="jev-recent-decisions-heading">
      <div className="jev-branches-title">
        <h3 id="jev-recent-decisions-heading">Recent decisions</h3>
        <span className="jev-badge">{live ? "Live evidence" : "Last known evidence"}</span>
      </div>
      {decisions.length === 0 ? (
        <p className="jev-note">No decision events recorded for this run.</p>
      ) : (
        <ol>
          {decisions.map((event) => (
            <li key={event.sequence} data-testid="jev-recent-decision">
              <strong>{factString(event.facts, "question_id")}</strong>
              {factString(event.facts, "choice") === null
                ? " — no choice recorded"
                : ` → ${factString(event.facts, "choice")}`}
              {factString(event.facts, "source") === null
                ? ""
                : ` · ${sourceLabel(factString(event.facts, "source"))}`}
              <span> · {formatSeconds(event.game_seconds)} · event {event.sequence}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
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
  const {
    runs,
    selectedRunId,
    selectRun,
    run,
    policy,
    runAgeSeconds,
    link,
    launch,
    reportRendered,
    resumeLive,
  } = useJevRun();
  const session = launch?.session.data ?? null;
  const shown = launch === null ? null : shownLaunchRun(launch, run, runAgeSeconds);
  // The session's run is still starting: its game process is paused at launch or
  // SC2 is launching, so a missing heartbeat is expected, not a stopped bot -- as
  // long as the liveness rule (launchLiveness) still reads the launch as alive.
  // Such a run is never shown as live.
  const launchStarting =
    launch !== null &&
    session !== null &&
    shown !== null &&
    (session.state === "starting" || session.state === "running") &&
    shown.status === "starting" &&
    launchLiveness(session, launch.sessionAgeSeconds, shown) === "alive";
  return (
    <div className="jev-tab" data-testid="jev-tab">
      <h2>Jev decision graph</h2>
      <p className="jev-tab-intro">
        Read-only view of Jev runs: the policy each run archived, and the latest state its game
        process recorded.
      </p>
      <LinkNotice link={link} />
      {launch !== null && (
        <LaunchPanel
          launch={launch}
          run={run}
          runAgeSeconds={runAgeSeconds}
          selectedRunId={selectedRunId}
          onResume={resumeLive}
        />
      )}
      <RunPicker runs={runs} selectedRunId={selectedRunId} onSelect={selectRun} />
      {selectedRunId !== null && (
        <RunView
          key={selectedRunId}
          runId={selectedRunId}
          run={run}
          policy={policy}
          launchStarting={launchStarting}
          onRendered={launch === null ? undefined : reportRendered}
        />
      )}
    </div>
  );
}

function LinkNotice({ link }: { link: JevLink }) {
  if (link.kind !== "invalid") return null;
  return (
    <p role="alert" className="jev-notice jev-notice-error" data-testid="jev-link-error">
      This link names an invalid {link.param === "launch" ? "launch session" : "run"} id, so no
      run is selected (a link never falls back to another run). Pick a run below.
    </p>
  );
}

const LAUNCH_PHASE_LABELS: Record<JevLaunchPhase, string> = {
  preparing: "Preparing",
  waiting: "Waiting for this page",
  paused: "Following paused",
  starting: "Starting",
  live: "Live",
  stale: "Stale",
  silent: "Launcher not responding",
  unavailable: "Launch unavailable",
  finished: "Finished",
  failed: "Failed",
  stopped: "Stopped",
};

/** The page's facts about its followed launch, for ``launchPhase``. */
function launchFacts(launch: JevLaunchView): JevLaunchFacts {
  return {
    session: launch.session.data,
    sessionError: launch.session.error !== null,
    gaveUp: launch.gaveUp,
    following: launch.following,
    acknowledgedRunId: launch.acknowledgedRunId,
    sessionAgeSeconds: launch.sessionAgeSeconds,
  };
}

/**
 * The launch's active run as this page shows it: only while following and holding
 * that run's state (otherwise null, and the liveness rule makes no claim about it).
 */
function shownLaunchRun(
  launch: JevLaunchView,
  run: JevResource<JevRunState>,
  runAgeSeconds: number | null,
): JevShownRun | null {
  const activeRunId = launch.session.data?.active_run_id ?? null;
  if (!launch.following || activeRunId === null || run.data?.run_id !== activeRunId) {
    return null;
  }
  return { status: run.data.status, stale: run.data.stale, ageSeconds: runAgeSeconds };
}

interface LaunchPanelProps {
  launch: JevLaunchView;
  run: JevResource<JevRunState>;
  runAgeSeconds: number | null;
  selectedRunId: string | null;
  onResume: () => void;
}

/** The followed launch session: its phase, game position, and the following control. */
function LaunchPanel({ launch, run, runAgeSeconds, selectedRunId, onResume }: LaunchPanelProps) {
  const session = launch.session.data;
  const activeRunId = session?.active_run_id ?? null;
  // Only the session's own run says anything about the launch; never another run.
  const state =
    activeRunId !== null && run.data !== null && run.data.run_id === activeRunId
      ? run.data
      : null;
  const phase = launchPhase(launchFacts(launch), shownLaunchRun(launch, run, runAgeSeconds));
  // While paused the page shows no live verdict, but a launcher that stopped writing
  // the session is still worth saying (it does not depend on what is on screen).
  const pausedButSilent =
    session !== null &&
    !launch.following &&
    launchLiveness(session, launch.sessionAgeSeconds, null) === "launcher_silent";
  // The launcher's last message ("starting SC2") is out of date once the game runs,
  // and a failure or stop already quotes it in the detail line.
  const showMessage =
    session !== null &&
    session.message !== "" &&
    phase !== "failed" &&
    phase !== "stopped" &&
    !(session.state === "running" && (phase === "live" || phase === "finished"));
  const decision =
    state?.recent_events.findLast(
      (event) => event.node_id === "army" && event.reason === ARMY_DECISION_REASON,
    ) ?? null;
  const choice = decision === null ? null : factString(decision.facts, "choice");
  return (
    <section
      className={`jev-launch jev-launch-${phase}`}
      aria-label="Launch session"
      data-testid="jev-launch"
    >
      <div className="jev-launch-title">
        <span className="jev-badge jev-launch-phase" data-testid="jev-launch-phase">
          {LAUNCH_PHASE_LABELS[phase]}
        </span>
        {session !== null && (
          <span className="jev-badge" data-testid="jev-launch-game">
            Game {session.case_index + 1} of {session.case_count}
          </span>
        )}
        {activeRunId !== null && (
          <span className="jev-note">
            run <code data-testid="jev-launch-run">{activeRunId}</code>
          </span>
        )}
      </div>
      <p className="jev-launch-detail" data-testid="jev-launch-detail">
        {launchDetail(phase, launch, state, choice, runAgeSeconds)}
      </p>
      {showMessage && (
        <p className="jev-note" data-testid="jev-launch-message">
          Launcher: {session.message}
        </p>
      )}
      {launch.session.error !== null && (
        <p role="alert" className="jev-notice jev-notice-error" data-testid="jev-launch-error">
          {launch.gaveUp
            ? `Stopped following launch ${launch.sessionId}`
            : `Could not refresh launch ${launch.sessionId} (attempt ${launch.failures}, retrying)`}
          : {describeError(launch.session.error)}. No other run is shown in its place.
        </p>
      )}
      {launch.ackError !== null && session?.state === "starting" && (
        <p role="alert" className="jev-notice jev-notice-error" data-testid="jev-launch-ack-error">
          Could not tell the launcher this run is shown: {describeError(launch.ackError)}{" "}
          (retrying).
        </p>
      )}
      {!launch.following && (
        <div className="jev-launch-paused" data-testid="jev-launch-paused">
          <p className="jev-notice jev-notice-warning">
            Following paused: you are viewing{" "}
            {selectedRunId === null ? "no run" : <code>{selectedRunId.slice(0, 8)}</code>}, not the
            launch's current game.
            {session?.state === "starting" &&
              " The launcher is waiting for this page to show its new game before it starts SC2."}
          </p>
          {pausedButSilent && (
            <p className="jev-notice jev-notice-error" data-testid="jev-launch-paused-silent">
              The launcher has not updated this launch for{" "}
              {Math.round(launch.sessionAgeSeconds ?? 0)} s; it may have been closed.
            </p>
          )}
          <button type="button" data-testid="jev-resume-live" onClick={onResume}>
            Resume live
          </button>
        </div>
      )}
    </section>
  );
}

const SESSION_STATE_TEXT: Record<JevLaunchState, string> = {
  preparing: "being prepared",
  starting: "waiting for its page",
  running: "in progress",
  between_games: "between games",
  finished: "finished",
  failed: "failed",
  stopped: "stopped",
};

function launchDetail(
  phase: JevLaunchPhase,
  launch: JevLaunchView,
  state: JevRunState | null,
  choice: string | null,
  runAgeSeconds: number | null,
): string {
  const session = launch.session.data;
  const game =
    session === null ? "the game" : `game ${session.case_index + 1} of ${session.case_count}`;
  switch (phase) {
    case "preparing":
      return session?.active_run_id
        ? `Preparing ${game}: showing its recorded run before SC2 starts.`
        : `Preparing ${game}: waiting for the launcher to record its run.`;
    case "waiting":
      return `The launcher is waiting for this page to show ${game} before it starts SC2.`;
    case "paused":
      return `This page shows another run; the launch is at ${game} (${
        session === null ? "unknown" : SESSION_STATE_TEXT[session.state]
      }).`;
    case "starting":
      return `Starting ${game}: the page shows this run; waiting for SC2 to report its first observation.`;
    case "live": {
      if (state === null) return `Live: ${game}.`;
      const { metadata } = state;
      return [
        `Live: game time ${formatSeconds(state.game_seconds)}`,
        `vs ${metadata.opponent_race} (difficulty ${metadata.difficulty}) on ${metadata.map}`,
        `${state.active_nodes.length} active decision node(s)`,
        `army intent ${choice ?? "none yet"}`,
      ].join(" · ");
    }
    case "stale":
      return state?.status === "starting"
        ? `SC2 has not reported a first game observation ${Math.round(
            runAgeSeconds ?? 0,
          )} s after the run was recorded; the game process may have failed to start it.`
        : "The game process stopped writing heartbeats; showing its last recorded state.";
    case "silent":
      return `The launcher has not updated this launch for ${Math.round(
        launch.sessionAgeSeconds ?? 0,
      )} s at ${game}; it may have been closed or have crashed. Showing the last recorded state; nothing here is live.`;
    case "unavailable":
      return launch.session.error === null
        ? `This page stopped following launch ${launch.sessionId}.`
        : `This launch cannot be followed (${describeError(launch.session.error)}). No run is shown in its place.`;
    case "finished": {
      const result = state === null ? null : (state.result ?? state.status);
      const next =
        session?.state === "between_games" ? " The next game is being prepared in this tab." : "";
      return `Finished ${game}${result === null ? "" : `: ${result}`}.${next}`;
    }
    case "failed":
      return `Failed: ${session?.message || "the launcher stopped"}.`;
    case "stopped":
      return `Stopped: ${session?.message || "the launch was stopped"}.`;
  }
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
            {selectedRunId === null && (
              <option value="" disabled>
                No run selected
              </option>
            )}
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
  /** A launch is starting this run: no heartbeat yet is expected, not stale. */
  launchStarting?: boolean;
  /** Told once this run's state and its matching archived policy are on screen. */
  onRendered?: (runId: string, policyHash: string) => void;
}

/** One run's view; keyed by run ID, so switching runs discards all of its state. */
function RunView({ runId, run, policy, launchStarting = false, onRendered }: RunViewProps) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedBranchId, setSelectedBranchId] = useState<string | null>(null);
  const [fullGraph, setFullGraph] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [reveal, setReveal] = useState<{ nodeId: string; token: number } | null>(null);
  const graphWorkspaceRef = useRef<HTMLDivElement>(null);
  const state = run.data;
  const events = state?.recent_events ?? NO_EVENTS;
  const runtime = useMemo(
    () => (state === null ? new Map<string, JevNodeRuntime>() : nodeRuntimes(state)),
    [state],
  );
  const archivedPolicy = policy.data;
  const matching =
    state !== null && archivedPolicy !== null && archivedPolicy.policy_hash === state.policy_hash
      ? archivedPolicy
      : null;
  const activity = useMemo(
    () => (state === null || matching === null ? NO_ACTIVITY : graphActivity(state, matching)),
    [state, matching],
  );
  const lanes = useMemo(
    () => (matching === null ? new Map<string, string>() : policyLanes(matching)),
    [matching],
  );
  const evidence = useMemo(() => plansEvidence(events), [events]);
  const selectedBranch =
    matching !== null && selectedBranchId !== null && matching.roots.includes(selectedBranchId)
      ? selectedBranchId
      : (matching?.roots.find((root) =>
          [...(state?.active_nodes ?? []), ...(state?.waiting_nodes ?? [])].some(
            (nodeId) => lanes.get(nodeId) === root,
          ),
        ) ?? matching?.roots[0] ?? "");
  const branches = useMemo(
    () =>
      matching === null || state === null ? [] : branchEvidence(matching, state, lanes),
    [matching, state, lanes],
  );
  const visibleActivity = useMemo(
    () =>
      fullGraph || selectedBranch.length === 0
        ? activity
        : {
            ...activity,
            followTargets: activity.followTargets.filter(
              (nodeId) => lanes.get(nodeId) === selectedBranch,
            ),
          },
    [activity, fullGraph, lanes, selectedBranch],
  );
  // Establish the default once as policy data arrives. React immediately retries this
  // render with the branch stored, so later polling cannot move the user's view.
  if (matching !== null && selectedBranchId === null) setSelectedBranchId(selectedBranch);
  useEffect(() => {
    const changed = () => setFullscreen(document.fullscreenElement === graphWorkspaceRef.current);
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, []);
  const showNode = (nodeId: string) => {
    const branch = lanes.get(nodeId);
    if (branch !== undefined) {
      setSelectedBranchId(branch);
      setFullGraph(false);
    }
    setSelectedNodeId(nodeId);
    setReveal((previous) => ({ nodeId, token: (previous?.token ?? 0) + 1 }));
  };
  const selectBranch = (root: string) => {
    setSelectedBranchId(root);
    setFullGraph(false);
    setSelectedNodeId(root);
    setReveal((previous) => ({ nodeId: root, token: (previous?.token ?? 0) + 1 }));
  };
  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement === graphWorkspaceRef.current) await document.exitFullscreen();
      else await graphWorkspaceRef.current?.requestFullscreen();
    } catch {
      setFullscreen(false);
    }
  };
  // The graph below is drawn exactly when this holds (same run, matching archive).
  const renderedHash =
    state !== null &&
    state.run_id === runId &&
    policy.data !== null &&
    policy.data.policy_hash === state.policy_hash
      ? state.policy_hash
      : null;
  useEffect(() => {
    // Effects run after the commit: the summary and graph for this run are in the DOM.
    if (renderedHash !== null) onRendered?.(runId, renderedHash);
  }, [runId, renderedHash, onRendered]);

  if (state === null) {
    if (run.error === null) return <p role="status">Loading run {runId}…</p>;
    return (
      <p role="alert" className="jev-notice jev-notice-error">
        Could not load run {runId}: {describeError(run.error)}
      </p>
    );
  }
  // A run still starting under a launch has no game observations yet: never live.
  const live =
    !isTerminalRunStatus(state.status) && !state.stale && run.error === null && !launchStarting;
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
      <div
        className={`jev-graph-workspace${fullscreen ? " jev-graph-workspace-fullscreen" : ""}`}
        ref={graphWorkspaceRef}
        data-testid="jev-graph-workspace"
      >
        <div className="jev-view-controls" role="group" aria-label="Graph scope controls">
          <button
            type="button"
            aria-pressed={fullGraph}
            data-testid="jev-full-graph"
            onClick={() => setFullGraph((current) => !current)}
          >
            {fullGraph ? `Show ${selectedBranch} branch` : "Full graph (debugging)"}
          </button>
          {typeof document.documentElement.requestFullscreen === "function" && (
            <button type="button" onClick={() => void toggleFullscreen()}>
              {fullscreen ? "Exit fullscreen" : "Fullscreen"}
            </button>
          )}
          <span>
            {fullGraph ? "All archived roots" : `${selectedBranch} branch`}
          </span>
        </div>
        <div className="jev-run-body">
          <JevGraph
            key={`${archived.policy_hash}:${fullGraph ? "full" : selectedBranch}`}
            policy={archived}
            displayRootIds={fullGraph ? undefined : [selectedBranch]}
            showLaneLegend={false}
            fitOnMount={fullGraph}
            runtime={runtime}
            live={live}
            selectedNodeId={selectedNodeId}
            onSelectNode={(nodeId) => {
              setSelectedNodeId(nodeId);
              const branch = lanes.get(nodeId);
              if (branch !== undefined) setSelectedBranchId(branch);
            }}
            activity={visibleActivity}
            reveal={reveal}
            ended={isTerminalRunStatus(state.status)}
          />
          <NodeInspector
            node={selectedNode}
            live={live}
            runtime={selectedNode === null ? "idle" : (runtime.get(selectedNode.id) ?? "idle")}
            events={events}
            tasks={state.tasks}
            policy={archived}
            lane={selectedNode === null ? null : (lanes.get(selectedNode.id) ?? null)}
            onShowNode={showNode}
          />
        </div>
      </div>
    );
  }

  // The plans panel is for policies that keep plans or hosted v2 questions (Jev v2);
  // an older or v1 run keeps its presentation.
  const plansShown =
    matching !== null &&
    (matching.nodes.some((node) => node.operation === "start_plan") ||
      evidence.plans.length > 0 ||
      evidence.decisions.some((view) => view.question !== "army_mode"));
  return (
    <>
      <RunSummary state={state} live={live} run={run} launchStarting={launchStarting} />
      {matching !== null && (
        <BranchOverview
          branches={branches}
          selectedRoot={selectedBranch}
          live={live}
          ended={isTerminalRunStatus(state.status)}
          onSelect={selectBranch}
        />
      )}
      {graph}
      <ArmyDecisionPanel events={events} live={live} />
      {plansShown && (
        <PlanPanel
          evidence={evidence}
          lanes={lanes}
          roots={matching.roots}
          live={live}
          gameSeconds={state.game_seconds}
          onShowNode={showNode}
        />
      )}
      <RecentDecisions events={events} live={live} />
      <RecentTrace events={events} />
    </>
  );
}

function ArmyDecisionPanel({ events, live }: { events: JevEvent[]; live: boolean }) {
  const decision =
    events.findLast(
      (event) => event.node_id === "army" && event.reason === ARMY_DECISION_REASON,
    ) ?? null;

  if (decision === null) {
    return (
      <section className="jev-army-decision" aria-labelledby="jev-army-decision-heading">
        <h3 id="jev-army-decision-heading">Army decision</h3>
        <p className="jev-note" data-testid="jev-decision-unavailable">
          Provider evidence unavailable for this run. Older runs may not contain army decision
          diagnostics.
        </p>
      </section>
    );
  }

  const { facts } = decision;
  const provider = factString(facts, "decision_provider");
  const source = factString(facts, "source");
  const choice = factString(facts, "choice");
  const reason = factString(facts, "reason");
  const pending = factBoolean(facts, "pending");
  const requestedModel = factString(facts, "requested_model");
  const actualModel = factString(facts, "model");
  const question = factString(facts, "question");
  const answer = factString(facts, "answer");
  const confidence = factNumber(facts, "confidence");
  const latency = factNumber(facts, "latency_ms");
  const calls = factNumber(facts, "calls");
  const inputTokens = factNumber(facts, "input_tokens");
  const outputTokens = factNumber(facts, "output_tokens");
  const maxRequests = factNumber(facts, "max_requests");
  const observationSeconds = factNumber(facts, "observation_game_seconds");
  const responseAge = factNumber(facts, "response_age_game_seconds");
  const optionsValue = facts.options;
  const options =
    Array.isArray(optionsValue) && optionsValue.every((value) => typeof value === "string")
      ? optionsValue
      : null;
  const probabilities = facts.probabilities;
  const hasProbabilities =
    probabilities !== null && typeof probabilities === "object" && !Array.isArray(probabilities);
  const hasServiceDetails =
    requestedModel !== null ||
    actualModel !== null ||
    question !== null ||
    options !== null ||
    answer !== null ||
    confidence !== null ||
    hasProbabilities ||
    latency !== null ||
    inputTokens !== null ||
    outputTokens !== null ||
    maxRequests !== null ||
    observationSeconds !== null ||
    responseAge !== null ||
    pending !== null;

  return (
    <section
      className="jev-army-decision"
      aria-labelledby="jev-army-decision-heading"
      data-testid="jev-army-decision"
    >
      <div className="jev-army-decision-title">
        <h3 id="jev-army-decision-heading">Army decision</h3>
        <span className="jev-badge" data-testid="jev-decision-age">
          {live ? "Live evidence" : "Last known evidence"}
        </span>
        <span className={`jev-badge jev-decision-source-${source ?? "unknown"}`}>
          Source: {sourceLabel(source)}
        </span>
      </div>
      <dl className="jev-facts">
        <dt>Configured provider</dt>
        <dd>{provider ?? "not recorded"}</dd>
        <dt>Current applied intent</dt>
        <dd data-testid="jev-decision-choice">{choice ?? "none recorded"}</dd>
        <dt>Decision reason</dt>
        <dd>{reason ?? "not recorded"}</dd>
        <dt>Requests made</dt>
        <dd>{calls ?? "not recorded"}</dd>
        {pending !== null && (
          <>
            <dt>Request state</dt>
            <dd>{pending ? (live ? "Pending now" : "Pending when recorded") : "Settled"}</dd>
          </>
        )}
      </dl>
      {hasServiceDetails && (
        <details className="jev-decision-details">
          <summary>Typesafe request and last response details</summary>
          <dl className="jev-facts">
            <dt>Requested model</dt>
            <dd>{requestedModel ?? "not recorded"}</dd>
            <dt>Actual response model</dt>
            <dd data-testid="jev-decision-model">{actualModel ?? "not recorded"}</dd>
            <dt>Question</dt>
            <dd>{question ?? "not recorded"}</dd>
            <dt>Options</dt>
            <dd>{options === null ? "not recorded" : options.join(", ")}</dd>
            <dt>Last service answer</dt>
            <dd data-testid="jev-decision-answer">{answer ?? "none recorded"}</dd>
            <dt>Confidence</dt>
            <dd>{confidence ?? "not recorded"}</dd>
            <dt>Latency</dt>
            <dd>{latency === null ? "not recorded" : `${latency} ms`}</dd>
            <dt>Token use</dt>
            <dd>
              {inputTokens === null && outputTokens === null
                ? "not recorded"
                : `${inputTokens ?? "?"} input / ${outputTokens ?? "?"} output`}
            </dd>
            <dt>Request budget</dt>
            <dd>{maxRequests ?? "not recorded"}</dd>
            <dt>Observation game time</dt>
            <dd>
              {observationSeconds === null ? "not recorded" : formatSeconds(observationSeconds)}
            </dd>
            <dt>Last response age</dt>
            <dd>{responseAge === null ? "none recorded" : formatSeconds(responseAge)}</dd>
          </dl>
          {hasProbabilities && (
            <>
              <h4>Last response probabilities</h4>
              <pre>{formatJson(probabilities)}</pre>
            </>
          )}
        </details>
      )}
    </section>
  );
}

interface RunSummaryProps {
  state: JevRunState;
  live: boolean;
  run: JevResource<JevRunState>;
  launchStarting: boolean;
}

function RunSummary({ state, live, run, launchStarting }: RunSummaryProps) {
  const { metadata, trace } = state;
  const waitingShown = state.waiting_nodes.slice(0, WAITING_NAMED);
  // Paused at launch (or SC2 still launching) is not a stopped producer.
  const stale = state.stale && !launchStarting;
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
        {stale && (
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
      {stale && (
        <p role="status" className="jev-notice jev-notice-warning">
          Stale: this run's game process stopped writing heartbeats without recording a final
          state. Showing its last recorded state; nothing here is live.
        </p>
      )}
      {launchStarting && (
        <p role="status" className="jev-notice jev-notice-waiting" data-testid="jev-launch-starting">
          Starting: this run is recorded and its game is being launched; it has no live
          observations yet.
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
  policy: JevPolicy;
  lane: string | null;
  onShowNode: (nodeId: string) => void;
}

function NodeInspector({
  node,
  live,
  runtime,
  events,
  tasks,
  policy,
  lane,
  onShowNode,
}: NodeInspectorProps) {
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
  // The newest recorded event of each distinct attribution (a plan summarized every
  // game second names the same decision each time), newest first.
  const distinct = new Map<string, { event: JevEvent; source: JevNodeSource }>();
  for (const event of nodeEvents) {
    const source = eventSource(event);
    if (source === null) continue;
    distinct.delete(source.detail);
    distinct.set(source.detail, { event, source });
  }
  const sources = [...distinct.values()].slice(-NODE_SOURCE_ROWS).reverse();
  const guards = pathGuards(policy, node.id);
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
        <dt>Lane</dt>
        <dd data-testid="jev-node-lane">{lane ?? "unknown"}</dd>
      </dl>
      <h4>Source evidence</h4>
      {sources.length === 0 ? (
        <p className="jev-note" data-testid="jev-node-sources-none">
          No recorded event of this node names a decision (a Typesafe request, a fallback or the
          local graph) among the run's recent events. Older and v1 runs record none.
        </p>
      ) : (
        <ul className="jev-source-list" data-testid="jev-node-sources">
          {sources.map(({ event, source }) => (
            <li key={event.sequence} data-testid="jev-node-source" data-source={source.tone}>
              #{event.sequence} · {formatSeconds(event.game_seconds)} · {source.detail}
              {live ? "" : " (last known)"}
            </li>
          ))}
        </ul>
      )}
      {guards.length > 0 && (
        <>
          <h4>Guards on this node's path</h4>
          <p className="jev-note">
            This node runs only after each guard below passes in the same tick; each shows its
            latest recorded evaluation.
          </p>
          <ul className="jev-source-list">
            {guards.map((guard) => {
              const latestGuard =
                events.findLast((event) => event.node_id === guard && event.kind === "node") ??
                null;
              const guardSource = latestGuard === null ? null : eventSource(latestGuard);
              return (
                <li key={guard} data-testid="jev-node-guard">
                  <button type="button" onClick={() => onShowNode(guard)}>
                    {guard}
                  </button>{" "}
                  {latestGuard === null
                    ? "no recent evaluation recorded"
                    : `${latestGuard.status} at ${formatSeconds(latestGuard.game_seconds)} (event ${
                        latestGuard.sequence
                      })${guardSource === null ? "" : ` — ${guardSource.detail}`}`}
                </li>
              );
            })}
          </ul>
        </>
      )}
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
