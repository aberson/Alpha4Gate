import {
  JEV_DECISION_REASONS,
  JEV_PLAN_SLOT_SUMMARY_REASON,
  JEV_TRAIL_SECONDS,
  type JevDecisionQuestion,
  type JevEvent,
  type JevPolicy,
  type JevRunState,
  type JsonObject,
  type JsonValue,
} from "../types/jev";

/**
 * What the Jev tab derives from one run's recorded evidence (plan J2 Step 221, D5):
 * the graph's observed edge activity, the decision behind each node, and the plans,
 * plan slots and decision sources of the Plans and decisions panel.
 *
 * Pure functions of the latest RunState (its ``active_nodes`` / ``waiting_nodes`` and
 * bounded ``recent_events``) and the run's archived policy. Nothing is interpolated
 * between two polls, and an absent fact (an older or v1 run) means "not recorded",
 * never a guess.
 */

// --- Shapes the graph draws -------------------------------------------------------

/** Stable within an archived policy, independent of visible branches and runtime state. */
export function nodeCodes(policy: JevPolicy): Map<string, string> {
  const prefixes: Record<string, string> = {
    economy: "E", supply: "S", construction: "C", tech: "T", expansion: "X",
    scouting: "SC", production: "P", army: "A", reserve: "R",
  };
  const byId = new Map(policy.nodes.map((node) => [node.id, node]));
  const codes = new Map<string, string>();
  policy.roots.forEach((root, index) => {
    let number = 0;
    const visit = (id: string) => {
      if (codes.has(id)) return;
      codes.set(id, `${prefixes[root] ?? `B${index + 1}-`}${++number}`);
      byId.get(id)?.children.forEach(visit);
    };
    visit(root);
  });
  return codes;
}

/** One edge the run's evidence shows as activity (never an invented traversal). */
export interface JevEdgeActivity {
  parent: string;
  child: string;
  /** ``current``: both ends run in the latest state; ``recent``: an event named it. */
  kind: "current" | "recent";
  /** Game seconds between the recording event and the latest state (0 for current). */
  ageSeconds: number;
  /** The recording event (null for the latest state's running path). */
  sequence: number | null;
}

/** The decision a node's newest recorded event carries. */
export interface JevNodeSource {
  tone: "hosted" | "fallback" | "local" | "awaiting" | "unknown";
  /** Short text shown on the node ("Typesafe req #12", "local fallback"). */
  badge: string;
  /** Full text for its name and tooltip. */
  detail: string;
  sequence: number;
}

export interface JevGraphActivity {
  edges: JevEdgeActivity[];
  sources: ReadonlyMap<string, JevNodeSource>;
  /** Nodes "Follow activity" may bring into view, the newest first. */
  followTargets: string[];
}

/** No recorded activity (a run whose state or archived policy is not loaded). */
export const NO_ACTIVITY: JevGraphActivity = { edges: [], sources: new Map(), followTargets: [] };

// --- Fact readers and formatting ----------------------------------------------------

export function factString(facts: JsonObject, key: string): string | null {
  const value = facts[key];
  return typeof value === "string" ? value : null;
}

export function factNumber(facts: JsonObject, key: string): number | null {
  const value = facts[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function factBoolean(facts: JsonObject, key: string): boolean | null {
  const value = facts[key];
  return typeof value === "boolean" ? value : null;
}

function isObject(value: JsonValue | undefined): value is JsonObject {
  if (value === null || value === undefined) return false;
  return typeof value === "object" && !Array.isArray(value);
}

export function formatSeconds(seconds: number): string {
  return `${seconds.toFixed(1)} s`;
}

// --- Node sources ---------------------------------------------------------------------

/** The guard operations whose evaluation decides which branch below them may run. */
const GUARD_OPERATIONS: ReadonlySet<string> = new Set([
  "army_mode_is",
  "strategy_is",
  "strategy_local",
  "expansion_response_is",
  "expansion_response_local",
  "production_mix_is",
  "production_mix_local",
]);

const SOURCE_TONES: Record<string, JevNodeSource["tone"]> = {
  typesafe: "hosted",
  local_fallback: "fallback",
  scripted_fallback: "fallback",
  fallback: "fallback",
  local_graph: "local",
  scripted: "local",
  awaiting_answer: "awaiting",
};

const SOURCE_NAMES: Record<string, string> = {
  typesafe: "Typesafe",
  local_fallback: "local fallback",
  scripted_fallback: "scripted fallback",
  fallback: "fallback",
  local_graph: "local graph",
  scripted: "scripted",
  awaiting_answer: "awaiting the hosted answer",
};

/** How a recorded decision source reads ("typesafe" -> "Typesafe"). */
export function sourceName(source: string): string {
  return SOURCE_NAMES[source] ?? source;
}

function describeSource(
  source: string,
  request: number | null,
  what: string,
  sequence: number,
): JevNodeSource {
  const tone = SOURCE_TONES[source] ?? "unknown";
  const name = sourceName(source);
  const badge = tone === "hosted" && request !== null ? `Typesafe req #${request}` : name;
  const detail = `${what}: ${name}${request === null ? "" : ` (request ${request})`}`;
  return { tone, badge, detail, sequence };
}

/**
 * The decision one recorded event attributes to its node, if any: a task or command
 * created under a hosted execution guard (its request), a plan event (the decision
 * that started the plan, or the hosted response that applies to it), a guard or lane
 * decision diagnostic (its ``source`` and request), or an army-mode guard (the army
 * intent it read).
 */
export function eventSource(event: JevEvent): JevNodeSource | null {
  const facts = event.facts;
  if (event.kind === "task" || event.kind === "command") {
    const request = factNumber(facts, "decision_request_id");
    if (request === null) return null;
    const question = factString(facts, "decision_question_id") ?? "a hosted decision";
    const choice = factString(facts, "decision_choice") ?? "?";
    const plan = factString(facts, "decision_plan_id");
    const forPlan = plan === null ? "" : ` for plan ${plan}`;
    const made = `${event.kind} ${event.task_id ?? ""}`;
    const what = `${made} created under ${question} = ${choice}${forPlan}`;
    return describeSource("typesafe", request, what, event.sequence);
  }
  if (event.kind !== "node" && event.kind !== "diagnostic") return null;
  const planId = factString(facts, "plan_id");
  const planKind = factString(facts, "plan_kind");
  if (planId !== null && planKind !== null) {
    const responseRequest = factNumber(facts, "response_request_id");
    if (responseRequest !== null) {
      const response = factString(facts, "response") ?? "?";
      const what = `plan ${planId} (${planKind}) expansion_response = ${response}`;
      return describeSource("typesafe", responseRequest, what, event.sequence);
    }
    const decidedBy = factString(facts, "decided_by");
    if (decidedBy === null) return null;
    const request = factNumber(facts, "decision_request_id");
    const what = `plan ${planId} (${planKind}) started by`;
    return describeSource(decidedBy, request, what, event.sequence);
  }
  const source = factString(facts, "source");
  if (source !== null) {
    const request =
      factNumber(facts, "request_id") ??
      factNumber(facts, "in_force_request_id") ??
      (source === "typesafe" ? factNumber(facts, "answer_request_id") : null);
    const question = factString(facts, "question_id") ?? factString(facts, "question") ?? "";
    const choice = factString(facts, "choice");
    const chose = choice === null ? "" : ` = ${choice}`;
    const what =
      event.kind === "node"
        ? `guard ${event.status}${question === "" ? "" : ` (${question}${chose})`}`
        : `${event.reason}${choice === null ? "" : ` (choice ${choice})`}`;
    return describeSource(source, request, what, event.sequence);
  }
  // An army-mode guard (v1 and v2): the hosted army intent it read, if any.
  const branch = factString(facts, "branch");
  if (event.kind === "node" && branch !== null && Object.hasOwn(facts, "mode")) {
    const mode = factString(facts, "mode");
    if (mode === null) {
      return {
        tone: "local",
        badge: "graph default",
        detail:
          `guard ${event.status}: no hosted army intent in force; ` +
          `branch ${branch} by the graph default`,
        sequence: event.sequence,
      };
    }
    return {
      tone: "hosted",
      badge: `army ${mode}`,
      detail: `guard ${event.status}: hosted army intent ${mode}; branch ${branch}`,
      sequence: event.sequence,
    };
  }
  return null;
}

/**
 * The guards on ``nodeId``'s path, nearest first: for each sequence above it (whose
 * later children run only once its first child passed) a first child that is a guard,
 * or that is a selector of guards (one of which passed).
 */
export function pathGuards(policy: JevPolicy, nodeId: string): string[] {
  const byId = new Map(policy.nodes.map((node) => [node.id, node]));
  const parentOf = new Map<string, string>();
  for (const node of policy.nodes) for (const child of node.children) parentOf.set(child, node.id);
  const isGuard = (id: string) => GUARD_OPERATIONS.has(byId.get(id)?.operation ?? "");
  const guards: string[] = [];
  let below = nodeId;
  for (let at = parentOf.get(nodeId); at !== undefined; at = parentOf.get(at)) {
    const node = byId.get(at);
    const first = node?.kind === "sequence" ? node.children[0] : undefined;
    if (first !== undefined && first !== below) {
      const head = byId.get(first);
      const guardSelector =
        head?.kind === "selector" && head.children.length > 0 && head.children.every(isGuard);
      if (isGuard(first)) guards.push(first);
      else if (guardSelector) guards.push(...head.children);
    }
    below = at;
  }
  return guards;
}

// --- Graph activity ---------------------------------------------------------------------

/**
 * The graph's recorded activity (plan D5): ``current`` edges join a node and its
 * child that both run in the latest state (a running composite runs through exactly
 * one child, so this is that tick's path); ``recent`` edges are the deciding child
 * (``facts.child``) of a node event recorded at most ``JEV_TRAIL_SECONDS`` game
 * seconds before the latest state. No other edge is ever drawn as activity.
 */
export function graphActivity(state: JevRunState, policy: JevPolicy): JevGraphActivity {
  const childrenOf = new Map(policy.nodes.map((node) => [node.id, new Set(node.children)]));
  const current = new Set([...state.active_nodes, ...state.waiting_nodes]);
  const edges = new Map<string, JevEdgeActivity>();
  for (const parent of current) {
    for (const child of childrenOf.get(parent) ?? []) {
      if (current.has(child)) {
        edges.set(`${parent}>${child}`, {
          parent,
          child,
          kind: "current",
          ageSeconds: 0,
          sequence: null,
        });
      }
    }
  }
  const sources = new Map<string, JevNodeSource>();
  const newest = new Map<string, number>();
  for (const event of state.recent_events) {
    newest.set(event.node_id, event.sequence);
    const source = eventSource(event);
    if (source !== null) sources.set(event.node_id, source);
    const child = event.facts.child;
    if (event.kind !== "node" || typeof child !== "string") continue;
    if (!childrenOf.get(event.node_id)?.has(child)) continue;
    const age = state.game_seconds - event.game_seconds;
    const key = `${event.node_id}>${child}`;
    if (age < 0 || age > JEV_TRAIL_SECONDS || edges.get(key)?.kind === "current") continue;
    edges.set(key, {
      parent: event.node_id,
      child,
      kind: "recent",
      ageSeconds: age,
      sequence: event.sequence,
    });
  }
  // Follow the deepest running nodes, the one with the newest recorded event first.
  const order = new Map(policy.nodes.map((node, index) => [node.id, index]));
  const isLeaf = (id: string) =>
    ![...(childrenOf.get(id) ?? [])].some((child) => current.has(child));
  const followTargets = [...current]
    .filter(isLeaf)
    .sort(
      (a, b) =>
        (newest.get(b) ?? -1) - (newest.get(a) ?? -1) || (order.get(a) ?? 0) - (order.get(b) ?? 0),
    );
  return { edges: [...edges.values()], sources, followTargets };
}

// --- Plans, plan slots and decision sources ---------------------------------------------

/** One plan, merged from every recorded event about it (see :func:`plansEvidence`). */
export interface PlanView {
  planId: string;
  kind: string;
  status: string;
  node: string | null;
  lane: string | null;
  stage: string;
  reasonCode: string;
  blocker: string | null;
  decidedBy: string | null;
  decisionRequestId: number | null;
  response: string | null;
  responseRequestId: number | null;
  startedAt: number | null;
  lastProgressAt: number | null;
  finishedAt: number | null;
  deadline: number | null;
  reservedMinerals: number | null;
  reservedVespene: number | null;
  /** The newest event that described the plan. */
  sequence: number;
}

/** A plan's view from its (merged) PlanRecord facts; null without its identity. */
export function readPlan(facts: JsonObject, sequence: number): PlanView | null {
  const planId = factString(facts, "plan_id");
  const kind = factString(facts, "plan_kind");
  const status = factString(facts, "plan_status");
  const stage = factString(facts, "stage_node_id");
  if (planId === null || kind === null || status === null || stage === null) return null;
  return {
    planId,
    kind,
    status,
    node: factString(facts, "plan_node"),
    lane: factString(facts, "plan_root"),
    stage,
    reasonCode: factString(facts, "reason_code") ?? "not recorded",
    blocker: factString(facts, "blocker"),
    decidedBy: factString(facts, "decided_by"),
    decisionRequestId: factNumber(facts, "decision_request_id"),
    response: factString(facts, "response"),
    responseRequestId: factNumber(facts, "response_request_id"),
    startedAt: factNumber(facts, "started_game_seconds"),
    lastProgressAt: factNumber(facts, "last_progress_game_seconds"),
    finishedAt: factNumber(facts, "finished_game_seconds"),
    deadline: factNumber(facts, "deadline_game_seconds"),
    reservedMinerals: factNumber(facts, "reserved_minerals"),
    reservedVespene: factNumber(facts, "reserved_vespene"),
    sequence,
  };
}

export function isOpenPlan(plan: PlanView): boolean {
  return plan.status === "active" || plan.status === "waiting";
}

/** One plan slot's turn state, from its newest summary or plan event. */
export interface SlotView {
  slot: string;
  openPlanId: string | null;
  openKind: string | null;
  cooldownUntil: number | null;
  yieldFrom: string | null;
  yieldTo: string[];
  yieldUntil: number | null;
  disabled: Array<[string, string]>;
  /** Per kind the slot can start, whether it may start now (only from a summary). */
  kinds: Array<{ kind: string; mayStart: boolean; refusal: string | null }> | null;
  sequence: number;
}

export function readSlotTurn(value: JsonValue | undefined, sequence: number): SlotView | null {
  if (!isObject(value)) return null;
  const slot = factString(value, "slot");
  if (slot === null) return null;
  const yieldTo = Array.isArray(value.yield_to)
    ? value.yield_to.filter((kind): kind is string => typeof kind === "string")
    : [];
  const disabled = isObject(value.disabled)
    ? Object.entries(value.disabled).filter(
        (entry): entry is [string, string] => typeof entry[1] === "string",
      )
    : [];
  return {
    slot,
    openPlanId: factString(value, "open_plan_id"),
    openKind: factString(value, "open_kind"),
    cooldownUntil: factNumber(value, "cooldown_until"),
    yieldFrom: factString(value, "yield_from"),
    yieldTo,
    yieldUntil: factNumber(value, "yield_until"),
    disabled,
    kinds: null,
    sequence,
  };
}

function readKinds(value: JsonValue | undefined): SlotView["kinds"] {
  if (!Array.isArray(value)) return null;
  const kinds: NonNullable<SlotView["kinds"]> = [];
  for (const item of value) {
    if (!isObject(item)) continue;
    const kind = factString(item, "kind");
    const mayStart = factBoolean(item, "may_start");
    if (kind === null || mayStart === null) continue;
    kinds.push({ kind, mayStart, refusal: factString(item, "refusal") });
  }
  return kinds;
}

/** One hosted-or-local decision family as its newest diagnostic describes it. */
export interface DecisionView {
  question: JevDecisionQuestion;
  source: string | null;
  choice: string | null;
  request: number | null;
  reason: string | null;
  pending: boolean | null;
  pendingRequest: number | null;
  model: string | null;
  available: string[] | null;
  sequence: number;
}

export interface PlansEvidence {
  /** Open plans first, then finished ones; each group the most recent first. */
  plans: PlanView[];
  slots: SlotView[];
  decisions: DecisionView[];
}

const DECISION_QUESTIONS = Object.entries(JEV_DECISION_REASONS) as Array<
  [JevDecisionQuestion, string]
>;

/** The game time a plan last changed: when it finished, else its last progress. */
function planTime(plan: PlanView): number {
  return plan.finishedAt ?? plan.lastProgressAt ?? plan.startedAt ?? Number.NEGATIVE_INFINITY;
}

/** The plan counter of ``<run_id>:plan:<n>`` (the store's start order). */
function planCounter(plan: PlanView): number {
  const match = /:plan:(\d+)$/.exec(plan.planId);
  return match === null ? -1 : Number(match[1]);
}

/**
 * Plans, plan slots and decision sources from a run's recent events.
 *
 * **One rule per plan (the producer-consumer contract):** a plan's view is the
 * merge of every recorded event about it -- its own transition and summary events
 * (``PlanRecord`` facts at the top level) and each ``Plan slot summary``'s ``plans``
 * entries (also ``PlanRecord`` facts) -- in recorded order: each fact keeps the newest
 * value any event recorded, and an event that does not carry a fact never erases it.
 * The runtime emits a plan's summary and then, in the same tick, the slot summary, so
 * a summary entry missing a fact (an archive written before the entries carried every
 * ``PlanRecord`` fact) can never blank the deadline or the holds its plan event gave.
 *
 * Plans are ordered open first, then by the plan's own game time (its finish, else its
 * last progress) newest first, then by start order -- never by which event repeated
 * them last.
 */
export function plansEvidence(events: JevEvent[]): PlansEvidence {
  const merged = new Map<string, { facts: JsonObject; sequence: number }>();
  const slots = new Map<string, SlotView>();
  const decisions = new Map<JevDecisionQuestion, DecisionView>();
  const mergePlan = (facts: JsonObject, sequence: number) => {
    const planId = factString(facts, "plan_id");
    if (planId === null || factString(facts, "plan_kind") === null) return; // not a plan record
    const previous = merged.get(planId)?.facts ?? {};
    const next: JsonObject = { ...previous };
    for (const [key, value] of Object.entries(facts)) {
      if (value !== undefined) next[key] = value; // an absent fact keeps its last value
    }
    merged.set(planId, { facts: next, sequence });
  };
  for (const event of events) {
    if (event.kind !== "diagnostic") continue;
    const { facts, sequence } = event;
    mergePlan(facts, sequence);
    const turn = readSlotTurn(facts.slot_turn, sequence);
    if (turn !== null) {
      const previous = slots.get(turn.slot);
      const kinds = readKinds(facts.kinds);
      slots.set(turn.slot, { ...turn, kinds: kinds ?? previous?.kinds ?? null });
    }
    if (event.reason === JEV_PLAN_SLOT_SUMMARY_REASON && Array.isArray(facts.plans)) {
      for (const item of facts.plans) if (isObject(item)) mergePlan(item, sequence);
    }
    const question = DECISION_QUESTIONS.find(([, reason]) => reason === event.reason)?.[0];
    if (question !== undefined) {
      const available = facts.available_options;
      const source = factString(facts, "source");
      decisions.set(question, {
        question,
        source,
        choice: factString(facts, "choice"),
        request:
          factNumber(facts, "in_force_request_id") ??
          (source === "typesafe" ? factNumber(facts, "answer_request_id") : null),
        reason: factString(facts, "reason"),
        pending: factBoolean(facts, "pending"),
        pendingRequest: factNumber(facts, "pending_request_id"),
        model: factString(facts, "model"),
        available: Array.isArray(available)
          ? available.filter((option): option is string => typeof option === "string")
          : null,
        sequence,
      });
    }
  }
  const plans = [...merged.values()]
    .map(({ facts, sequence }) => readPlan(facts, sequence))
    .filter((plan): plan is PlanView => plan !== null);
  const group = (plan: PlanView) => (isOpenPlan(plan) ? 0 : 1);
  plans.sort(
    (a, b) =>
      group(a) - group(b) || planTime(b) - planTime(a) || planCounter(b) - planCounter(a),
  );
  return {
    plans,
    slots: [...slots.values()],
    decisions: DECISION_QUESTIONS.map(([question]) => decisions.get(question)).filter(
      (view): view is DecisionView => view !== undefined,
    ),
  };
}
