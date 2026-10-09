import { useCallback, useEffect, useRef, useState } from "react";
import {
  JevContractError,
  isTerminalLaunchState,
  isValidRunId,
  isValidSessionId,
  parseApiError,
  parseLaunchError,
  parseLaunchReceipt,
  parseLaunchSession,
  parsePolicy,
  parseRunList,
  parseRunState,
  type JevLaunchReceipt,
  type JevLaunchSession,
  type JevPolicy,
  type JevRunList,
  type JevRunState,
} from "../types/jev";

/**
 * Polling hook for the Jev tab (plan section 5, "Read-only API", and D7).
 *
 * While the hook is mounted it polls the run list every
 * ``JEV_RUN_LIST_POLL_MS`` and the selected run's state every
 * ``JEV_RUN_POLL_MS``; the selected run's archived policy is immutable, so
 * it is fetched until it loads once. A lane never starts a request while its
 * previous one is in flight, and every request (headers and body) is aborted
 * after ``JEV_REQUEST_TIMEOUT_MS``, so a hung backend surfaces as a network
 * error and the next poll retries. Unmounting clears the timers and aborts
 * every request in flight.
 *
 * Deliberately not built on ``useApi``: that hook neither validates nor
 * aborts, and it renders IndexedDB-cached data before the first fetch,
 * which would show a cached graph as if it were live. Here nothing renders
 * until a response for the current selection has been validated.
 *
 * Run switching never mixes runs: ``selectRun`` replaces the selected ID
 * and empties its state and policy in one update, and the previous run's
 * requests are aborted. A response that still arrives for a previous
 * selection is dropped twice over: its request's signal is aborted, and
 * the state update only applies while the selection is still that run.
 *
 * **Which run** (plan D7) comes from the page URL, parsed once:
 *
 * - no ``run``/``launch`` parameter: the newest listed run until the user
 *   picks one (unchanged);
 * - ``?run=<run_id>``: exactly that run, never the newest;
 * - ``?launch=<session_id>`` (wins over ``run``): the launch session is polled
 *   every ``JEV_LAUNCH_POLL_MS`` and the view follows the session's exact
 *   active run -- nothing is selected until the session names one, and the
 *   global newest run is never guessed. Once the run view reports (via
 *   ``reportRendered``) that the session's starting run and its matching
 *   archived policy are rendered, the hook acknowledges readiness with
 *   ``POST /api/jev/launches/{id}/ready``; only then may the launcher start
 *   SC2. Picking another run pauses following (no acknowledgment is sent
 *   while paused) until ``resumeLive``. A failing session poll is reported
 *   and retried, at most ``JEV_LAUNCH_MAX_FAILURES`` times in a row (an answer
 *   that cannot change, such as ``launch_not_found``, stops it at once); it
 *   never falls back to another run. The session is polled by
 *   ``startTicker``, which keeps following a hidden (occluded) page.
 * - an invalid ID in either parameter selects nothing.
 */

/** The selected run's state is polled this often while the tab is mounted. */
export const JEV_RUN_POLL_MS = 1000;
/** The run list is polled this often while the tab is mounted. */
export const JEV_RUN_LIST_POLL_MS = 5000;
/** A request that has not completed after this long is aborted and reported as a failure. */
export const JEV_REQUEST_TIMEOUT_MS = 4000;
/** A followed launch session is polled this often (plan D7: every second). */
export const JEV_LAUNCH_POLL_MS = 1000;
/** Consecutive failed session polls after which the page stops retrying. */
export const JEV_LAUNCH_MAX_FAILURES = 60;

/** Launch errors that a retry cannot change: polling stops at the first one. */
const PERMANENT_LAUNCH_ERRORS: ReadonlySet<string> = new Set([
  "launch_not_found",
  "invalid_launch_request",
]);

function isPermanent(error: JevLoadError): boolean {
  return error.kind === "http" && error.code !== null && PERMANENT_LAUNCH_ERRORS.has(error.code);
}

/** The ticker's worker: one message per interval, from the worker's own timer. */
const TICKER_SOURCE =
  "let t; onmessage = (e) => { clearInterval(t); t = setInterval(() => postMessage(0), e.data); };";

/**
 * Call ``onTick`` every ``intervalMs``, robustly while the page is hidden.
 *
 * During a batch the dashboard sits behind a fullscreen SC2 window for a whole
 * game; Chromium treats an occluded window as hidden, and after five minutes
 * hidden it applies intensive wake-up throttling to the page's chained
 * timers, which then fire about once a minute ("Heavy throttling of chained JS
 * timers beginning in Chrome 88", developer.chrome.com). That would let the
 * next game's 60-second rendered-ready deadline pass. The tick therefore
 * comes from a dedicated worker's timer (the throttling policy targets the
 * page's own timers, not a dedicated worker's), and the page also ticks at
 * once whenever it becomes visible again. Without ``Worker`` (tests, very old
 * browsers) a plain ``setInterval`` is used. Returns the stop function.
 */
export function startTicker(intervalMs: number, onTick: () => void): () => void {
  let stopTimer: () => void = () => {};
  const fallBack = () => {
    const timer = window.setInterval(onTick, intervalMs);
    stopTimer = () => window.clearInterval(timer);
  };
  try {
    if (typeof Worker !== "function" || typeof URL.createObjectURL !== "function") {
      throw new Error("no dedicated workers here");
    }
    const source = URL.createObjectURL(new Blob([TICKER_SOURCE], { type: "text/javascript" }));
    const worker = new Worker(source);
    const stopWorker = () => {
      worker.terminate();
      URL.revokeObjectURL(source);
    };
    worker.onmessage = () => onTick();
    // A worker that is created but cannot load (a refused script, for example)
    // reports it asynchronously: drop it and tick from the page's own timer.
    worker.onerror = () => {
      stopWorker();
      fallBack();
    };
    worker.postMessage(intervalMs);
    stopTimer = stopWorker;
  } catch {
    fallBack();
  }
  const onVisible = () => {
    if (document.visibilityState === "visible") onTick();
  };
  document.addEventListener("visibilitychange", onVisible);
  return () => {
    stopTimer();
    document.removeEventListener("visibilitychange", onVisible);
  };
}

const RUNS_URL = "/api/jev/runs";
const LAUNCHES_URL = "/api/jev/launches";

export type JevLoadError =
  | { kind: "network"; message: string }
  | { kind: "http"; status: number; code: string | null; message: string }
  | { kind: "contract"; message: string };

export interface JevResource<T> {
  /** The latest valid response, or null before the first one. */
  data: T | null;
  /** Why the most recent attempt failed; null once one succeeds. */
  error: JevLoadError | null;
  /** When ``data`` was received. */
  lastSuccess: Date | null;
}

/** What the page URL asks the tab to show. */
export type JevLink =
  | { kind: "newest" }
  | { kind: "run"; runId: string }
  | { kind: "launch"; sessionId: string }
  | { kind: "invalid"; param: "run" | "launch" };

/** ``?launch=`` wins over ``?run=``; an invalid ID never falls back to another run. */
export function parseJevLink(search: string): JevLink {
  const params = new URLSearchParams(search);
  const launch = params.get("launch");
  if (launch !== null) {
    return isValidSessionId(launch)
      ? { kind: "launch", sessionId: launch }
      : { kind: "invalid", param: "launch" };
  }
  const run = params.get("run");
  if (run !== null) {
    return isValidRunId(run) ? { kind: "run", runId: run } : { kind: "invalid", param: "run" };
  }
  return { kind: "newest" };
}

export interface JevLaunchView {
  sessionId: string;
  session: JevResource<JevLaunchSession>;
  /** Consecutive failed session polls (reset by a success). */
  failures: number;
  /** Polling stopped after ``JEV_LAUNCH_MAX_FAILURES`` failures in a row. */
  gaveUp: boolean;
  /** Whether the view follows the session's run; a manual pick pauses it. */
  following: boolean;
  /** The run this page acknowledged as rendered (the launcher may start its game). */
  acknowledgedRunId: string | null;
  /** Why the last readiness acknowledgment failed (retried on the next poll). */
  ackError: JevLoadError | null;
  /** Seconds since the launcher last wrote the session, as of the latest poll. */
  sessionAgeSeconds: number | null;
}

export interface UseJevRunResult {
  runs: JevResource<JevRunList>;
  selectedRunId: string | null;
  /** Select a run by ID; IDs that are not lowercase UUID4 hex are ignored. */
  selectRun: (runId: string) => void;
  /** The selected run's state (always the selected run's, never a previous one's). */
  run: JevResource<JevRunState>;
  /** The selected run's archived policy. */
  policy: JevResource<JevPolicy>;
  /** Seconds since the selected run's record was last written (its own heartbeat). */
  runAgeSeconds: number | null;
  /** What the page URL asked for. */
  link: JevLink;
  /** The followed launch session (``?launch=``), else null. */
  launch: JevLaunchView | null;
  /** The run view rendered ``runId`` with its archived policy ``policyHash``. */
  reportRendered: (runId: string, policyHash: string) => void;
  /** Follow the launch session's current run again after a manual pick. */
  resumeLive: () => void;
}

type Outcome<T> = { ok: true; data: T } | { ok: false; error: JevLoadError };

interface Selection {
  runId: string | null;
  run: JevResource<JevRunState>;
  policy: JevResource<JevPolicy>;
  /** Seconds since the run's record was last written, as of its latest poll. */
  runAgeSeconds: number | null;
}

interface View {
  selection: Selection;
  launch: JevLaunchView | null;
}

interface Rendered {
  runId: string;
  policyHash: string;
}

const EMPTY = { data: null, error: null, lastSuccess: null } as const;

function freshSelection(runId: string | null): Selection {
  return { runId, run: EMPTY, policy: EMPTY, runAgeSeconds: null };
}

function initialView(link: JevLink): View {
  const launch: JevLaunchView | null =
    link.kind === "launch"
      ? {
          sessionId: link.sessionId,
          session: EMPTY,
          failures: 0,
          gaveUp: false,
          following: true,
          acknowledgedRunId: null,
          ackError: null,
          sessionAgeSeconds: null,
        }
      : null;
  return { selection: freshSelection(link.kind === "run" ? link.runId : null), launch };
}

function applyOutcome<T>(previous: JevResource<T>, outcome: Outcome<T>): JevResource<T> {
  if (outcome.ok) return { data: outcome.data, error: null, lastSuccess: new Date() };
  return { ...previous, error: outcome.error };
}

/** ``promise``, or a rejection as soon as ``signal`` aborts (whichever comes first). */
function untilAborted<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(new DOMException("aborted", "AbortError"));
    if (signal.aborted) onAbort();
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener("abort", onAbort));
  });
}

/** How an error body is read: run errors and launch errors have separate parsers. */
type ErrorParser = (body: unknown) => { code: string; message: string } | null;

interface RequestOptions {
  parseError?: ErrorParser;
  init?: RequestInit;
}

/**
 * GET (or ``options.init``) ``url`` and validate the body with ``parse``.
 * Resolves to null once ``signal`` is aborted, so a superseded request never
 * yields a result. A request still incomplete after ``JEV_REQUEST_TIMEOUT_MS``
 * is aborted and reported as a network error.
 */
async function request<T>(
  url: string,
  parse: (body: unknown) => T,
  signal: AbortSignal,
  options: RequestOptions = {},
): Promise<Outcome<T> | null> {
  const attempt = new AbortController();
  let timedOut = false;
  const timer = window.setTimeout(() => {
    timedOut = true;
    attempt.abort();
  }, JEV_REQUEST_TIMEOUT_MS);
  const cancel = () => attempt.abort();
  signal.addEventListener("abort", cancel, { once: true });
  try {
    return await requestOnce(url, parse, signal, attempt.signal, () => timedOut, options);
  } finally {
    window.clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
  }
}

/** One attempt of ``request``, cancelled through ``attempt`` (by the timeout or by ``signal``). */
async function requestOnce<T>(
  url: string,
  parse: (body: unknown) => T,
  signal: AbortSignal,
  attempt: AbortSignal,
  timedOut: () => boolean,
  options: RequestOptions,
): Promise<Outcome<T> | null> {
  const unanswered = (): Outcome<T> | null => {
    if (signal.aborted) return null;
    const message = timedOut()
      ? `the dashboard API did not answer within ${JEV_REQUEST_TIMEOUT_MS / 1000} s`
      : "the dashboard API did not respond";
    return { ok: false, error: { kind: "network", message } };
  };
  let response: Response;
  try {
    const init: RequestInit = { ...options.init, signal: attempt, cache: "no-store" };
    response = await untilAborted(fetch(url, init), attempt);
  } catch {
    return unanswered();
  }
  let body: unknown = undefined;
  try {
    body = await untilAborted(response.json(), attempt);
  } catch {
    // Not JSON (an error page from a proxy, a truncated body), or aborted.
  }
  if (attempt.aborted) return unanswered();
  if (!response.ok) {
    const apiError = (options.parseError ?? parseApiError)(body);
    return {
      ok: false,
      error: {
        kind: "http",
        status: response.status,
        code: apiError?.code ?? null,
        message: apiError?.message ?? `HTTP ${response.status}`,
      },
    };
  }
  try {
    return { ok: true, data: parse(body) };
  } catch (error) {
    if (error instanceof JevContractError) {
      return { ok: false, error: { kind: "contract", message: error.message } };
    }
    throw error;
  }
}

/** Whether ``view`` shows the session's exact starting run, rendered, and not yet acknowledged. */
function readyToAcknowledge(view: View, rendered: Rendered | null): Rendered | null {
  const launch = view.launch;
  const session = launch?.session.data ?? null;
  if (launch === null || session === null || !launch.following) return null;
  const runId = session.active_run_id;
  if (session.state !== "starting" || runId === null || launch.acknowledgedRunId === runId) {
    return null;
  }
  const { selection } = view;
  const state = selection.run.data;
  const policy = selection.policy.data;
  if (selection.runId !== runId || state === null || policy === null) return null;
  if (state.run_id !== runId || policy.policy_hash !== state.policy_hash) return null;
  if (rendered === null || rendered.runId !== runId) return null;
  if (rendered.policyHash !== state.policy_hash) return null;
  return rendered;
}

export function useJevRun(search: string = window.location.search): UseJevRunResult {
  const [link] = useState<JevLink>(() => parseJevLink(search));
  const [runs, setRuns] = useState<JevResource<JevRunList>>(EMPTY);
  const [view, setView] = useState<View>(() => initialView(link));
  const [rendered, setRendered] = useState<Rendered | null>(null);
  // Bumped when a followed session names a new run: the run list is re-read at once,
  // so the picker lists that run instead of waiting for its next five-second poll.
  const [listRefresh, setListRefresh] = useState(0);
  const ackInFlight = useRef(false);
  const ackControllers = useRef(new Set<AbortController>());

  useEffect(() => {
    const controller = new AbortController();
    let inFlight = false;
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      let outcome;
      try {
        outcome = await request(RUNS_URL, parseRunList, controller.signal);
      } finally {
        inFlight = false;
      }
      if (outcome === null) return;
      setRuns((previous) => applyOutcome(previous, outcome));
      if (link.kind === "newest" && outcome.ok && outcome.data.runs.length > 0) {
        // Follow the newest run until the user picks one (only without a link).
        const newest = outcome.data.runs[0].run_id;
        setView((previous) =>
          previous.selection.runId === null
            ? { ...previous, selection: freshSelection(newest) }
            : previous,
        );
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), JEV_RUN_LIST_POLL_MS);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, [link, listRefresh]);

  const sessionId = link.kind === "launch" ? link.sessionId : null;
  useEffect(() => {
    if (sessionId === null || !isValidSessionId(sessionId)) return;
    const controller = new AbortController();
    const url = `${LAUNCHES_URL}/${sessionId}`;
    let inFlight = false;
    let failures = 0;
    let lastActiveRun: string | null = null;
    let stopTicker: (() => void) | null = null;
    const stop = () => {
      stopTicker?.();
      stopTicker = null;
    };
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      let outcome;
      try {
        outcome = await request(
          url,
          (body) => parseLaunchSession(body, sessionId),
          controller.signal,
          { parseError: parseLaunchError },
        );
      } finally {
        inFlight = false;
      }
      if (outcome === null) return;
      // Bounded retries: polling stops after too many failures in a row, at once
      // for an answer that cannot change (no such session, an invalid id), and once
      // the session has ended (it never changes again).
      failures = outcome.ok ? 0 : failures + 1;
      const gaveUp =
        failures >= JEV_LAUNCH_MAX_FAILURES || (!outcome.ok && isPermanent(outcome.error));
      if (gaveUp || (outcome.ok && isTerminalLaunchState(outcome.data.state))) stop();
      if (outcome.ok && outcome.data.active_run_id !== lastActiveRun) {
        lastActiveRun = outcome.data.active_run_id;
        if (lastActiveRun !== null) setListRefresh((count) => count + 1);
      }
      // How long ago the launcher last wrote the session (same computer, same clock).
      const age = outcome.ok
        ? Math.max(0, (Date.now() - Date.parse(outcome.data.updated_at)) / 1000)
        : null;
      setView((previous) => {
        const launch = previous.launch;
        if (launch === null || launch.sessionId !== sessionId) return previous;
        const next = {
          ...launch,
          session: applyOutcome(launch.session, outcome),
          failures,
          gaveUp,
          sessionAgeSeconds: age ?? launch.sessionAgeSeconds,
        };
        if (!outcome.ok) return { ...previous, launch: next };
        const active = outcome.data.active_run_id;
        const selection =
          launch.following && active !== null && previous.selection.runId !== active
            ? freshSelection(active)
            : previous.selection;
        return { selection, launch: next };
      });
    };
    void load();
    stopTicker = startTicker(JEV_LAUNCH_POLL_MS, () => void load());
    return () => {
      stop();
      controller.abort();
    };
  }, [sessionId]);

  const runId = view.selection.runId;
  useEffect(() => {
    // Every way into the selection validates the ID; this check keeps the
    // URL below safe on its own.
    if (runId === null || !isValidRunId(runId)) return;
    const controller = new AbortController();
    const base = `${RUNS_URL}/${runId}`;
    let stateInFlight = false;
    let policyInFlight = false;
    let policyLoaded = false;
    const update = (apply: (current: Selection) => Selection) =>
      setView((previous) =>
        previous.selection.runId === runId
          ? { ...previous, selection: apply(previous.selection) }
          : previous,
      );

    const loadState = async () => {
      stateInFlight = true;
      let outcome;
      try {
        outcome = await request(base, (body) => parseRunState(body, runId), controller.signal);
      } finally {
        stateInFlight = false;
      }
      if (outcome === null) return;
      // The run's own heartbeat age (same computer, same clock).
      const age = outcome.ok
        ? Math.max(0, (Date.now() - Date.parse(outcome.data.updated_at)) / 1000)
        : null;
      update((current) => ({
        ...current,
        run: applyOutcome(current.run, outcome),
        runAgeSeconds: age ?? current.runAgeSeconds,
      }));
    };
    const loadPolicy = async () => {
      policyInFlight = true;
      let outcome;
      try {
        outcome = await request(`${base}/policy`, parsePolicy, controller.signal);
      } finally {
        policyInFlight = false;
      }
      if (outcome === null) return;
      policyLoaded = outcome.ok;
      update((current) => ({ ...current, policy: applyOutcome(current.policy, outcome) }));
    };
    const poll = () => {
      if (!stateInFlight) void loadState();
      if (!policyLoaded && !policyInFlight) void loadPolicy();
    };
    poll();
    const timer = window.setInterval(poll, JEV_RUN_POLL_MS);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, [runId]);

  // Acknowledge readiness once the session's exact starting run is rendered.
  const acknowledge = readyToAcknowledge(view, rendered);
  const ackRunId = acknowledge?.runId ?? null;
  const ackHash = acknowledge?.policyHash ?? null;
  const ackSessionPoll = view.launch?.session.lastSuccess ?? null;
  useEffect(() => {
    if (sessionId === null || ackRunId === null || ackHash === null || ackInFlight.current) {
      return;
    }
    ackInFlight.current = true;
    const controller = new AbortController();
    const controllers = ackControllers.current;
    controllers.add(controller);
    const send = async () => {
      let outcome;
      try {
        outcome = await request<JevLaunchReceipt>(
          `${LAUNCHES_URL}/${sessionId}/ready`,
          (body) => parseLaunchReceipt(body, sessionId, ackRunId),
          controller.signal,
          {
            parseError: parseLaunchError,
            init: {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ run_id: ackRunId, policy_hash: ackHash }),
            },
          },
        );
      } finally {
        ackInFlight.current = false;
        controllers.delete(controller);
      }
      if (outcome === null) return;
      setView((previous) => {
        const launch = previous.launch;
        if (launch === null || launch.session.data?.active_run_id !== ackRunId) return previous;
        return outcome.ok
          ? { ...previous, launch: { ...launch, acknowledgedRunId: ackRunId, ackError: null } }
          : { ...previous, launch: { ...launch, ackError: outcome.error } };
      });
    };
    void send();
    // A new session poll re-runs this effect, which retries a failed acknowledgment.
  }, [sessionId, ackRunId, ackHash, ackSessionPoll]);

  useEffect(() => {
    // Unmounting aborts an acknowledgment in flight (its answer is then ignored).
    const controllers = ackControllers.current;
    return () => {
      for (const controller of controllers) controller.abort();
      controllers.clear();
    };
  }, []);

  const selectRun = useCallback((next: string) => {
    if (!isValidRunId(next)) return;
    setView((previous) => {
      const launch = previous.launch;
      const following =
        launch === null ? true : launch.session.data?.active_run_id === next;
      const selection =
        previous.selection.runId === next ? previous.selection : freshSelection(next);
      if (launch === null) return { ...previous, selection };
      return { selection, launch: { ...launch, following } };
    });
  }, []);

  const resumeLive = useCallback(() => {
    setView((previous) => {
      const launch = previous.launch;
      if (launch === null) return previous;
      const active = launch.session.data?.active_run_id ?? null;
      const selection =
        active !== null && previous.selection.runId === active
          ? previous.selection
          : freshSelection(active);
      return { selection, launch: { ...launch, following: true } };
    });
  }, []);

  const reportRendered = useCallback((renderedRunId: string, policyHash: string) => {
    setRendered((previous) =>
      previous !== null && previous.runId === renderedRunId && previous.policyHash === policyHash
        ? previous
        : { runId: renderedRunId, policyHash },
    );
  }, []);

  return {
    runs,
    selectedRunId: runId,
    selectRun,
    run: view.selection.run,
    policy: view.selection.policy,
    runAgeSeconds: view.selection.runAgeSeconds,
    link,
    launch: view.launch,
    reportRendered,
    resumeLive,
  };
}
