/**
 * Wire contract of the read-only Jev run-evidence API (``/api/jev``) and its
 * runtime validation.
 *
 * Shapes mirror what ``src/jev/api.py`` serves: the run list, a run's
 * ``RunState`` (``jev.contracts.RunState.to_dict``) plus the computed
 * ``stale`` flag and the run's ``metadata``, and the archived policy
 * (``jev.contracts.Policy.to_document``) plus its ``policy_hash``. Error
 * responses are ``{schema_version, error: {code, message}}``.
 *
 * Every response is validated here before anything renders it: the
 * ``parse*`` functions return typed copies holding only the known fields,
 * or throw ``JevContractError`` naming the offending field (never echoing
 * response content). Fields the API adds later are ignored, not rejected.
 * Unit tags are decimal strings on the wire and stay strings here: a
 * 64-bit tag does not survive a conversion to a JavaScript number.
 */

/** The only schema version this dashboard understands. */
export const JEV_SCHEMA_VERSION = 1;

/**
 * Size caps the API never exceeds; a response beyond them is invalid. Each
 * mirrors the backend constant named beside it.
 */
export const JEV_LIMITS = {
  runs: 50, // jev.api.RUN_LIST_LIMIT
  recentEvents: 200, // jev.contracts.RECENT_EVENT_LIMIT
  tasks: 128, // jev.contracts.MAX_ACTIVE_TASKS
  nodes: 4096, // jev.policy.MAX_NODES
  roots: 16, // jev.policy.MAX_ROOTS
  children: 128, // jev.policy.MAX_CHILDREN
  depth: 128, // jev.policy.MAX_DEPTH
} as const;

const RUN_STATUSES = [
  "starting",
  "running",
  "finished",
  "stopped",
  "failed",
] as const;
export type JevRunStatus = (typeof RUN_STATUSES)[number];

/** Statuses after which the producer never writes again (``TERMINAL_RUN_STATUSES``). */
const TERMINAL_RUN_STATUSES: ReadonlySet<JevRunStatus> = new Set([
  "finished",
  "stopped",
  "failed",
]);

export function isTerminalRunStatus(status: JevRunStatus): boolean {
  return TERMINAL_RUN_STATUSES.has(status);
}

const RUN_RESULTS = ["win", "loss", "draw", "timeout"] as const;
export type JevRunResult = (typeof RUN_RESULTS)[number];

const NODE_KINDS = [
  "sequence",
  "selector",
  "condition",
  "select",
  "action",
  "wait",
] as const;
export type JevNodeKind = (typeof NODE_KINDS)[number];

const TASK_STATUSES = [
  "pending",
  "issued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
] as const;
export type JevTaskStatus = (typeof TASK_STATUSES)[number];

const EVENT_KINDS = ["node", "command", "task", "diagnostic"] as const;
export type JevEventKind = (typeof EVENT_KINDS)[number];

const EVENT_STATUSES = [
  "success",
  "failure",
  "running",
  "pending",
  "issued",
  "succeeded",
  "failed",
  "cancelled",
  "warning",
] as const;
export type JevEventStatus = (typeof EVENT_STATUSES)[number];

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

/** A task/command target: a unit tag (decimal string), an ``[x, y]`` point, or none. */
export type JevTarget = string | [number, number] | null;

export interface JevRunSummary {
  run_id: string;
  family: string;
  version: number;
  policy_hash: string;
  status: JevRunStatus;
  updated_at: string;
}

export interface JevRunList {
  runs: JevRunSummary[];
  /** More runs exist than are listed (the API lists the newest 50). */
  truncated: boolean;
  /** Runs left out because a stored record is malformed. */
  omitted: number;
}

export interface JevTask {
  id: string;
  node_id: string;
  intent_key: string;
  actor_tag: string | null;
  target: JevTarget;
  status: JevTaskStatus;
  created_game_seconds: number;
  deadline_game_seconds: number | null;
  attempts: number;
  last_progress_game_seconds: number | null;
  reason: string;
}

export interface JevEvent {
  sequence: number;
  game_loop: number;
  game_seconds: number;
  node_id: string;
  task_id: string | null;
  kind: JevEventKind;
  status: JevEventStatus;
  reason: string;
  facts: JsonObject;
  action: JsonObject | null;
}

export interface JevError {
  code: string;
  message: string;
}

export interface JevTraceStats {
  segment: number;
  events: number;
  rotated_segments: number;
  dropped_segments: number;
  dropped_events: number;
  complete: boolean;
}

export interface JevRunMetadata {
  created_at: string;
  source_commit: string | null;
  map: string;
  opponent_race: string;
  difficulty: number;
  seed: number;
  max_game_seconds: number;
  max_wall_seconds: number;
  replay_path: string | null;
}

export interface JevRunState extends JevRunSummary {
  game_seconds: number;
  last_sequence: number;
  active_nodes: string[];
  waiting_nodes: string[];
  tasks: JevTask[];
  /** Oldest first, in strictly increasing ``sequence`` order. */
  recent_events: JevEvent[];
  result: JevRunResult | null;
  error: JevError | null;
  tasks_omitted: number;
  trace: JevTraceStats;
  /** A nonterminal run whose producer stopped writing heartbeats (computed by the API). */
  stale: boolean;
  metadata: JevRunMetadata;
}

export interface JevPolicyNode {
  id: string;
  label: string;
  kind: JevNodeKind;
  children: string[];
  operation: string | null;
  args: JsonObject;
}

export interface JevPolicy {
  family: string;
  version: number;
  roots: string[];
  parameters: JsonObject;
  nodes: JevPolicyNode[];
  policy_hash: string;
}

/** A response that breaks the contract (wrong schema version, shape or bounds). */
export class JevContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "JevContractError";
  }
}

// --- Field readers: each checks one value and names its path on failure ---

type Fields = Record<string, unknown>;

const RUN_ID_RE = /^[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$/;
const HASH_RE = /^[0-9a-f]{64}$/;
const TAG_RE = /^(0|[1-9][0-9]{0,19})$/;

/**
 * True for a lowercase UUID4 hex run ID (32 hex digits, version 4, RFC 4122
 * variant): the only IDs the API accepts (``jev.contracts.is_valid_run_id``)
 * and the only ones this dashboard puts in a URL.
 */
export function isValidRunId(value: string): boolean {
  return RUN_ID_RE.test(value);
}

function invalid(path: string, expected: string): never {
  throw new JevContractError(`${path} is not ${expected}`);
}

function readObject(value: unknown, path: string): Fields {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    invalid(path, "a JSON object");
  }
  return value as Fields;
}

function field(fields: Fields, name: string, path: string): unknown {
  if (!Object.hasOwn(fields, name)) {
    throw new JevContractError(`${path}.${name} is missing`);
  }
  return fields[name];
}

function readSchemaVersion(fields: Fields, path: string): void {
  if (field(fields, "schema_version", path) !== JEV_SCHEMA_VERSION) {
    throw new JevContractError(
      `${path} has an unsupported schema_version (expected ${JEV_SCHEMA_VERSION})`,
    );
  }
}

function readString(value: unknown, path: string): string {
  if (typeof value !== "string") invalid(path, "a string");
  return value;
}

function readMatching(value: unknown, pattern: RegExp, path: string, expected: string): string {
  const text = readString(value, path);
  if (!pattern.test(text)) invalid(path, expected);
  return text;
}

function readNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) invalid(path, "a finite number");
  return value;
}

function readCount(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    invalid(path, "a non-negative integer");
  }
  return value;
}

function readBoolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") invalid(path, "a boolean");
  return value;
}

function readChoice<T extends string>(value: unknown, allowed: readonly T[], path: string): T {
  const match = allowed.find((option) => option === value);
  if (match === undefined) invalid(path, `one of ${allowed.join(", ")}`);
  return match;
}

function readNullable<T>(
  value: unknown,
  read: (value: unknown, path: string) => T,
  path: string,
): T | null {
  return value === null ? null : read(value, path);
}

function readList<T>(
  value: unknown,
  limit: number,
  read: (value: unknown, path: string) => T,
  path: string,
): T[] {
  if (!Array.isArray(value) || value.length > limit) {
    invalid(path, `a list of at most ${limit} items`);
  }
  return value.map((item, index) => read(item, `${path}[${index}]`));
}

function readTimestamp(value: unknown, path: string): string {
  const text = readString(value, path);
  if (Number.isNaN(Date.parse(text))) invalid(path, "an ISO 8601 timestamp");
  return text;
}

function readTag(value: unknown, path: string): string {
  return readMatching(value, TAG_RE, path, "a decimal unit-tag string");
}

function readJsonObject(value: unknown, path: string): JsonObject {
  // A decoded JSON body holds only JSON values; the shape is what matters.
  return readObject(value, path) as JsonObject;
}

function readTarget(value: unknown, path: string): JevTarget {
  if (value === null) return null;
  if (typeof value === "string") return readTag(value, path);
  if (Array.isArray(value) && value.length === 2) {
    return [readNumber(value[0], `${path}[0]`), readNumber(value[1], `${path}[1]`)];
  }
  return invalid(path, "null, a unit-tag string or an [x, y] point");
}

// --- Records ----------------------------------------------------------------

function readSummary(value: unknown, path: string): JevRunSummary {
  const fields = readObject(value, path);
  const at = (name: string) => field(fields, name, path);
  return {
    run_id: readMatching(at("run_id"), RUN_ID_RE, `${path}.run_id`, "a lowercase UUID4 hex run id"),
    family: readString(at("family"), `${path}.family`),
    version: readCount(at("version"), `${path}.version`),
    policy_hash: readMatching(
      at("policy_hash"),
      HASH_RE,
      `${path}.policy_hash`,
      "a SHA-256 hex digest",
    ),
    status: readChoice(at("status"), RUN_STATUSES, `${path}.status`),
    updated_at: readTimestamp(at("updated_at"), `${path}.updated_at`),
  };
}

function readTask(value: unknown, path: string): JevTask {
  const fields = readObject(value, path);
  const at = (name: string) => field(fields, name, path);
  return {
    id: readString(at("id"), `${path}.id`),
    node_id: readString(at("node_id"), `${path}.node_id`),
    intent_key: readString(at("intent_key"), `${path}.intent_key`),
    actor_tag: readNullable(at("actor_tag"), readTag, `${path}.actor_tag`),
    target: readTarget(at("target"), `${path}.target`),
    status: readChoice(at("status"), TASK_STATUSES, `${path}.status`),
    created_game_seconds: readNumber(at("created_game_seconds"), `${path}.created_game_seconds`),
    deadline_game_seconds: readNullable(
      at("deadline_game_seconds"),
      readNumber,
      `${path}.deadline_game_seconds`,
    ),
    attempts: readCount(at("attempts"), `${path}.attempts`),
    last_progress_game_seconds: readNullable(
      at("last_progress_game_seconds"),
      readNumber,
      `${path}.last_progress_game_seconds`,
    ),
    reason: readString(at("reason"), `${path}.reason`),
  };
}

function readEvent(value: unknown, path: string, runId: string): JevEvent {
  const fields = readObject(value, path);
  const at = (name: string) => field(fields, name, path);
  readSchemaVersion(fields, path);
  if (at("run_id") !== runId) {
    throw new JevContractError(`${path} belongs to another run`);
  }
  return {
    sequence: readCount(at("sequence"), `${path}.sequence`),
    game_loop: readCount(at("game_loop"), `${path}.game_loop`),
    game_seconds: readNumber(at("game_seconds"), `${path}.game_seconds`),
    node_id: readString(at("node_id"), `${path}.node_id`),
    task_id: readNullable(at("task_id"), readString, `${path}.task_id`),
    kind: readChoice(at("kind"), EVENT_KINDS, `${path}.kind`),
    status: readChoice(at("status"), EVENT_STATUSES, `${path}.status`),
    reason: readString(at("reason"), `${path}.reason`),
    facts: readJsonObject(at("facts"), `${path}.facts`),
    action: readNullable(at("action"), readJsonObject, `${path}.action`),
  };
}

function readError(value: unknown, path: string): JevError {
  const fields = readObject(value, path);
  const at = (name: string) => field(fields, name, path);
  return {
    code: readString(at("code"), `${path}.code`),
    message: readString(at("message"), `${path}.message`),
  };
}

function readTrace(value: unknown, path: string): JevTraceStats {
  const fields = readObject(value, path);
  const at = (name: string) => field(fields, name, path);
  return {
    segment: readCount(at("segment"), `${path}.segment`),
    events: readCount(at("events"), `${path}.events`),
    rotated_segments: readCount(at("rotated_segments"), `${path}.rotated_segments`),
    dropped_segments: readCount(at("dropped_segments"), `${path}.dropped_segments`),
    dropped_events: readCount(at("dropped_events"), `${path}.dropped_events`),
    complete: readBoolean(at("complete"), `${path}.complete`),
  };
}

function readMetadata(value: unknown, path: string, summary: JevRunSummary): JevRunMetadata {
  const fields = readObject(value, path);
  const at = (name: string) => field(fields, name, path);
  readSchemaVersion(fields, path);
  if (at("run_id") !== summary.run_id || at("policy_hash") !== summary.policy_hash) {
    throw new JevContractError(`${path} does not match the run`);
  }
  return {
    created_at: readTimestamp(at("created_at"), `${path}.created_at`),
    source_commit: readNullable(at("source_commit"), readString, `${path}.source_commit`),
    map: readString(at("map"), `${path}.map`),
    opponent_race: readString(at("opponent_race"), `${path}.opponent_race`),
    difficulty: readCount(at("difficulty"), `${path}.difficulty`),
    seed: readCount(at("seed"), `${path}.seed`),
    max_game_seconds: readNumber(at("max_game_seconds"), `${path}.max_game_seconds`),
    max_wall_seconds: readNumber(at("max_wall_seconds"), `${path}.max_wall_seconds`),
    replay_path: readNullable(at("replay_path"), readString, `${path}.replay_path`),
  };
}

function readNode(value: unknown, path: string): JevPolicyNode {
  const fields = readObject(value, path);
  const at = (name: string) => field(fields, name, path);
  return {
    id: readString(at("id"), `${path}.id`),
    label: readString(at("label"), `${path}.label`),
    kind: readChoice(at("kind"), NODE_KINDS, `${path}.kind`),
    children: readList(at("children"), JEV_LIMITS.children, readString, `${path}.children`),
    operation: readNullable(at("operation"), readString, `${path}.operation`),
    args: readJsonObject(at("args"), `${path}.args`),
  };
}

/**
 * The policy's roots and child edges must form a forest over every node:
 * unique IDs, known references, each node reached exactly once, bounded
 * depth. The graph walks this structure, so a cycle or a shared child would
 * otherwise hang or duplicate the layout.
 */
function checkForest(roots: string[], nodes: JevPolicyNode[]): void {
  const byId = new Map<string, JevPolicyNode>();
  for (const node of nodes) {
    if (byId.has(node.id)) throw new JevContractError("policy node ids are not unique");
    byId.set(node.id, node);
  }
  const reached = new Set<string>();
  const pending: Array<[string, number]> = roots.map((id) => [id, 1]);
  while (pending.length > 0) {
    const [id, depth] = pending.pop() as [string, number];
    const node = byId.get(id);
    if (node === undefined) throw new JevContractError("policy references an unknown node");
    if (reached.has(id)) throw new JevContractError("policy nodes do not form a forest");
    if (depth > JEV_LIMITS.depth) throw new JevContractError("policy is nested too deeply");
    reached.add(id);
    for (const child of node.children) pending.push([child, depth + 1]);
  }
  if (reached.size !== nodes.length) {
    throw new JevContractError("policy has nodes no root reaches");
  }
}

// --- Responses ----------------------------------------------------------------

/** ``GET /api/jev/runs``. */
export function parseRunList(body: unknown): JevRunList {
  const doc = "run list";
  const fields = readObject(body, doc);
  const at = (name: string) => field(fields, name, doc);
  readSchemaVersion(fields, doc);
  return {
    runs: readList(at("runs"), JEV_LIMITS.runs, readSummary, `${doc}.runs`),
    truncated: readBoolean(at("truncated"), `${doc}.truncated`),
    omitted: readCount(at("omitted"), `${doc}.omitted`),
  };
}

/** ``GET /api/jev/runs/{runId}``; the state must be the requested run's. */
export function parseRunState(body: unknown, runId: string): JevRunState {
  const doc = "run state";
  const fields = readObject(body, doc);
  const at = (name: string) => field(fields, name, doc);
  readSchemaVersion(fields, doc);
  const summary = readSummary(fields, doc);
  if (summary.run_id !== runId) {
    throw new JevContractError(`${doc} belongs to another run`);
  }
  const recentEvents = readList(
    at("recent_events"),
    JEV_LIMITS.recentEvents,
    (value, path) => readEvent(value, path, runId),
    `${doc}.recent_events`,
  );
  if (recentEvents.some((event, i) => i > 0 && event.sequence <= recentEvents[i - 1].sequence)) {
    throw new JevContractError(`${doc}.recent_events are not in increasing sequence order`);
  }
  return {
    ...summary,
    game_seconds: readNumber(at("game_seconds"), `${doc}.game_seconds`),
    last_sequence: readCount(at("last_sequence"), `${doc}.last_sequence`),
    active_nodes: readList(at("active_nodes"), JEV_LIMITS.nodes, readString, `${doc}.active_nodes`),
    waiting_nodes: readList(
      at("waiting_nodes"),
      JEV_LIMITS.nodes,
      readString,
      `${doc}.waiting_nodes`,
    ),
    tasks: readList(at("tasks"), JEV_LIMITS.tasks, readTask, `${doc}.tasks`),
    recent_events: recentEvents,
    result: readNullable(
      at("result"),
      (value, path) => readChoice(value, RUN_RESULTS, path),
      `${doc}.result`,
    ),
    error: readNullable(at("error"), readError, `${doc}.error`),
    tasks_omitted: readCount(at("tasks_omitted"), `${doc}.tasks_omitted`),
    trace: readTrace(at("trace"), `${doc}.trace`),
    stale: readBoolean(at("stale"), `${doc}.stale`),
    metadata: readMetadata(at("metadata"), `${doc}.metadata`, summary),
  };
}

/** ``GET /api/jev/runs/{runId}/policy``: the run's archived policy. */
export function parsePolicy(body: unknown): JevPolicy {
  const doc = "policy";
  const fields = readObject(body, doc);
  const at = (name: string) => field(fields, name, doc);
  readSchemaVersion(fields, doc);
  const roots = readList(at("roots"), JEV_LIMITS.roots, readString, `${doc}.roots`);
  if (roots.length === 0) throw new JevContractError(`${doc} has no roots`);
  const nodes = readList(at("nodes"), JEV_LIMITS.nodes, readNode, `${doc}.nodes`);
  checkForest(roots, nodes);
  return {
    family: readString(at("family"), `${doc}.family`),
    version: readCount(at("version"), `${doc}.version`),
    roots,
    parameters: readJsonObject(at("parameters"), `${doc}.parameters`),
    nodes,
    policy_hash: readMatching(
      at("policy_hash"),
      HASH_RE,
      `${doc}.policy_hash`,
      "a SHA-256 hex digest",
    ),
  };
}

/** The ``error`` of an API error response, or null when the body is not one. */
export function parseApiError(body: unknown): JevError | null {
  const doc = "error response";
  try {
    const fields = readObject(body, doc);
    readSchemaVersion(fields, doc);
    return readError(field(fields, "error", doc), `${doc}.error`);
  } catch (error) {
    if (error instanceof JevContractError) return null;
    throw error;
  }
}

// --- Launch sessions (plan D7): a separate contract from run evidence -----------

/**
 * ``jev.launch.LAUNCH_STATES``: where a dashboard-first launch stands. A
 * launch session names one attended launch (a single match or one benchmark
 * batch), never "the newest run".
 */
export const LAUNCH_STATES = [
  "preparing",
  "starting",
  "running",
  "between_games",
  "finished",
  "failed",
  "stopped",
] as const;
export type JevLaunchState = (typeof LAUNCH_STATES)[number];

const TERMINAL_LAUNCH_STATES: ReadonlySet<JevLaunchState> = new Set([
  "finished",
  "failed",
  "stopped",
]);

export function isTerminalLaunchState(state: JevLaunchState): boolean {
  return TERMINAL_LAUNCH_STATES.has(state);
}

// --- THE liveness invariant (consumer side of the table in src/jev/launch.py) -----
//
// What a session's ``updated_at`` means, and the ONE liveness question the page
// asks, per state (one writer at a time; it changes hands only at a transition):
//
//   preparing      launcher, once (a stamp)        older than launchSilenceSeconds?
//   starting       barrier, every 5 s (heartbeat)  older than startingSilenceSeconds?
//   running        game process, once at release   never the session's age: the
//                  (a stamp)                       active RUN's record is the heartbeat,
//                                                  asked only while that run is shown
//                                                  (stale; or still "starting" with a
//                                                  record older than
//                                                  firstObservationSilenceSeconds);
//                                                  not shown: no claim
//   between_games  launcher, once (a stamp)        older than launchSilenceSeconds?
//   terminal       whoever ended it, once          none
//
// ``launchLiveness`` is that table; ``launchPhase`` is the only caller that turns it
// into what the page shows, and it decides "following paused" before "silent".

/** The followed launch's active run as this page shows it (null: not on screen). */
export interface JevShownRun {
  status: JevRunStatus;
  /** The API's flag: a nonterminal run whose own heartbeat stopped. */
  stale: boolean;
  /** Seconds since the run's record was last written, as of the latest poll. */
  ageSeconds: number | null;
}

/**
 * The page's one liveness verdict for a launch session (the table above):
 * ``launcher_silent`` (the session's writer stopped), ``game_silent`` (the shown
 * running game, or its SC2 launch, stopped), ``unknown`` (running, but its run is
 * not on screen: no claim either way), ``none`` (the session ended), else ``alive``.
 */
export type JevLaunchLiveness = "alive" | "launcher_silent" | "game_silent" | "unknown" | "none";

export function launchLiveness(
  session: JevLaunchSession,
  sessionAgeSeconds: number | null,
  shownRun: JevShownRun | null,
): JevLaunchLiveness {
  const olderThan = (limit: number) => sessionAgeSeconds !== null && sessionAgeSeconds > limit;
  switch (session.state) {
    case "finished":
    case "failed":
    case "stopped":
      return "none";
    case "starting":
      return olderThan(JEV_LAUNCH_LIMITS.startingSilenceSeconds) ? "launcher_silent" : "alive";
    case "preparing":
    case "between_games":
      return olderThan(JEV_LAUNCH_LIMITS.launchSilenceSeconds) ? "launcher_silent" : "alive";
    case "running": {
      if (shownRun === null) return "unknown";
      if (isTerminalRunStatus(shownRun.status)) return "alive";
      if (shownRun.status === "starting") {
        const age = shownRun.ageSeconds;
        return age !== null && age > JEV_LAUNCH_LIMITS.firstObservationSilenceSeconds
          ? "game_silent"
          : "alive";
      }
      return shownRun.stale ? "game_silent" : "alive";
    }
  }
}

/** Where a followed launch stands, as the page shows it. */
export type JevLaunchPhase =
  | "preparing"
  | "waiting"
  | "paused"
  | "starting"
  | "live"
  | "stale"
  | "silent"
  | "unavailable"
  | "finished"
  | "failed"
  | "stopped";

/** What the page knows about its followed launch session (``useJevRun``'s view). */
export interface JevLaunchFacts {
  session: JevLaunchSession | null;
  /** The latest session poll failed. */
  sessionError: boolean;
  /** Polling stopped (an answer that cannot change, or too many failures). */
  gaveUp: boolean;
  following: boolean;
  acknowledgedRunId: string | null;
  sessionAgeSeconds: number | null;
}

/**
 * THE phase of a followed launch. ``shownRun`` is the session's active run only
 * while this page follows it and has its state; pass null otherwise. Order: no
 * session; ended; unreadable; following paused (before any liveness verdict);
 * the liveness verdict; then the session's own state.
 */
export function launchPhase(facts: JevLaunchFacts, shownRun: JevShownRun | null): JevLaunchPhase {
  const { session } = facts;
  // No session: an error is never "preparing" (the launcher creates the session
  // before it opens this page, so it cannot be on its way).
  if (session === null) return facts.sessionError ? "unavailable" : "preparing";
  if (session.state === "failed" || session.state === "stopped") return session.state;
  if (facts.gaveUp) return "unavailable";
  if (!facts.following) return session.state === "starting" ? "waiting" : "paused";
  const liveness = launchLiveness(session, facts.sessionAgeSeconds, shownRun);
  if (liveness === "launcher_silent") return "silent";
  switch (session.state) {
    case "preparing":
      return "preparing";
    case "starting":
      return facts.acknowledgedRunId === session.active_run_id ? "starting" : "preparing";
    case "running":
      if (shownRun === null) return "starting";
      if (isTerminalRunStatus(shownRun.status)) return "finished";
      if (liveness === "game_silent") return "stale";
      return shownRun.status === "starting" ? "starting" : "live";
    case "between_games":
    case "finished":
      return "finished";
  }
}

/**
 * ``jev.launch.LAUNCH_ERROR_CODES``: the launch API's own error codes. They are
 * deliberately not run error codes, and run errors are never parsed as these.
 */
export const LAUNCH_ERROR_CODES = [
  "launch_forbidden",
  "invalid_launch_request",
  "launch_not_found",
  "launch_not_ready",
  "corrupt_launch",
] as const;
export type JevLaunchErrorCode = (typeof LAUNCH_ERROR_CODES)[number];

/**
 * ``jev.launch.MAX_LAUNCH_MESSAGE_CHARS`` (code points) / ``MAX_CASE_COUNT`` /
 * ``STARTING_SILENCE_SECONDS`` / ``LAUNCH_SILENCE_SECONDS`` /
 * ``FIRST_OBSERVATION_SILENCE_SECONDS``.
 */
export const JEV_LAUNCH_LIMITS = {
  message: 200,
  cases: 64,
  startingSilenceSeconds: 20,
  launchSilenceSeconds: 120,
  firstObservationSilenceSeconds: 300,
} as const;

/** ``GET /api/jev/launches/{sessionId}`` (``jev.launch.LaunchSession``). */
export interface JevLaunchSession {
  session_id: string;
  active_run_id: string | null;
  state: JevLaunchState;
  /** Zero-based position of the current game; ``case_count`` games in all. */
  case_index: number;
  case_count: number;
  updated_at: string;
  message: string;
}

/** ``POST /api/jev/launches/{sessionId}/ready``'s answer. */
export interface JevLaunchReceipt {
  session_id: string;
  run_id: string;
}

export interface JevLaunchError {
  code: JevLaunchErrorCode;
  message: string;
}

/** True for a lowercase UUID4 hex launch session ID (the same shape as run IDs). */
export function isValidSessionId(value: string): boolean {
  return RUN_ID_RE.test(value);
}

/** The launch session; it must be the requested session's. */
export function parseLaunchSession(body: unknown, sessionId: string): JevLaunchSession {
  const doc = "launch session";
  const fields = readObject(body, doc);
  const at = (name: string) => field(fields, name, doc);
  readSchemaVersion(fields, doc);
  const id = readMatching(at("session_id"), RUN_ID_RE, `${doc}.session_id`, "a UUID4 hex id");
  if (id !== sessionId) throw new JevContractError(`${doc} belongs to another session`);
  const state = readChoice(at("state"), LAUNCH_STATES, `${doc}.state`);
  const activeRunId = readNullable(
    at("active_run_id"),
    (value, path) => readMatching(value, RUN_ID_RE, path, "a UUID4 hex run id"),
    `${doc}.active_run_id`,
  );
  if ((state === "starting" || state === "running") && activeRunId === null) {
    throw new JevContractError(`${doc} is ${state} without a run`);
  }
  const caseCount = readCount(at("case_count"), `${doc}.case_count`);
  if (caseCount < 1 || caseCount > JEV_LAUNCH_LIMITS.cases) {
    invalid(`${doc}.case_count`, `between 1 and ${JEV_LAUNCH_LIMITS.cases}`);
  }
  const caseIndex = readCount(at("case_index"), `${doc}.case_index`);
  if (caseIndex >= caseCount) invalid(`${doc}.case_index`, "below case_count");
  const message = readString(at("message"), `${doc}.message`);
  // Counted in code points, as the backend counts them (not UTF-16 units).
  if ([...message].length > JEV_LAUNCH_LIMITS.message) {
    invalid(`${doc}.message`, `at most ${JEV_LAUNCH_LIMITS.message} characters`);
  }
  return {
    session_id: id,
    active_run_id: activeRunId,
    state,
    case_index: caseIndex,
    case_count: caseCount,
    updated_at: readTimestamp(at("updated_at"), `${doc}.updated_at`),
    message,
  };
}

/** The readiness receipt; it must acknowledge exactly this session and run. */
export function parseLaunchReceipt(
  body: unknown,
  sessionId: string,
  runId: string,
): JevLaunchReceipt {
  const doc = "launch readiness";
  const fields = readObject(body, doc);
  const at = (name: string) => field(fields, name, doc);
  readSchemaVersion(fields, doc);
  if (at("ready") !== true) invalid(`${doc}.ready`, "true");
  const session = readString(at("session_id"), `${doc}.session_id`);
  const run = readString(at("run_id"), `${doc}.run_id`);
  if (session !== sessionId || run !== runId) {
    throw new JevContractError(`${doc} acknowledges another session or run`);
  }
  return { session_id: session, run_id: run };
}

/**
 * The launch ``error`` of an error response (its own code set), or null when
 * the body is not a launch error -- a run error code is never accepted here.
 */
export function parseLaunchError(body: unknown): JevLaunchError | null {
  const doc = "launch error response";
  try {
    const fields = readObject(body, doc);
    readSchemaVersion(fields, doc);
    const error = readObject(field(fields, "error", doc), `${doc}.error`);
    return {
      code: readChoice(field(error, "code", doc), LAUNCH_ERROR_CODES, `${doc}.error.code`),
      message: readString(field(error, "message", doc), `${doc}.error.message`),
    };
  } catch (error) {
    if (error instanceof JevContractError) return null;
    throw error;
  }
}

// --- Display vocabulary shared by the graph and the node inspector -------------

/**
 * A node's runtime status as the viewer shows it: ``active`` / ``waiting``
 * come from the state's ``active_nodes`` / ``waiting_nodes``; otherwise the
 * status of the node's latest evaluation in ``recent_events``, or ``idle``
 * when none is recent.
 */
export type JevNodeRuntime = "active" | "waiting" | "running" | "success" | "failure" | "idle";

export const JEV_NODE_RUNTIME_LABELS: Record<JevNodeRuntime, string> = {
  active: "active",
  waiting: "waiting",
  running: "last: running",
  success: "last: success",
  failure: "last: failure",
  idle: "no recent evaluation",
};

/** Whether ``status`` comes from the state's ``active_nodes`` / ``waiting_nodes``. */
export function isCurrentRuntime(status: JevNodeRuntime): boolean {
  return status === "active" || status === "waiting";
}

/**
 * A node's status label. Unless the state is live, ``active`` and
 * ``waiting`` describe the last recorded state, not the present, and say so:
 * ``active (last known)``.
 */
export function nodeRuntimeLabel(status: JevNodeRuntime, live: boolean): string {
  const label = JEV_NODE_RUNTIME_LABELS[status];
  return live || !isCurrentRuntime(status) ? label : `${label} (last known)`;
}
