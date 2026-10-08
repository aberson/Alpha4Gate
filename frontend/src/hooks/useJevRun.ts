import { useCallback, useEffect, useState } from "react";
import {
  JevContractError,
  isValidRunId,
  parseApiError,
  parsePolicy,
  parseRunList,
  parseRunState,
  type JevPolicy,
  type JevRunList,
  type JevRunState,
} from "../types/jev";

/**
 * Polling hook for the Jev tab (plan section 5, "Read-only API").
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
 */

/** The selected run's state is polled this often while the tab is mounted. */
export const JEV_RUN_POLL_MS = 1000;
/** The run list is polled this often while the tab is mounted. */
export const JEV_RUN_LIST_POLL_MS = 5000;
/** A request that has not completed after this long is aborted and reported as a failure. */
export const JEV_REQUEST_TIMEOUT_MS = 4000;

const RUNS_URL = "/api/jev/runs";

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

export interface UseJevRunResult {
  runs: JevResource<JevRunList>;
  selectedRunId: string | null;
  /** Select a run by ID; IDs that are not lowercase UUID4 hex are ignored. */
  selectRun: (runId: string) => void;
  /** The selected run's state (always the selected run's, never a previous one's). */
  run: JevResource<JevRunState>;
  /** The selected run's archived policy. */
  policy: JevResource<JevPolicy>;
}

type Outcome<T> = { ok: true; data: T } | { ok: false; error: JevLoadError };

interface Selection {
  runId: string | null;
  run: JevResource<JevRunState>;
  policy: JevResource<JevPolicy>;
}

const EMPTY = { data: null, error: null, lastSuccess: null } as const;

function freshSelection(runId: string | null): Selection {
  return { runId, run: EMPTY, policy: EMPTY };
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

/**
 * GET ``url`` and validate the body with ``parse``. Resolves to null once
 * ``signal`` is aborted, so a superseded request never yields a result. A
 * request still incomplete after ``JEV_REQUEST_TIMEOUT_MS`` is aborted and
 * reported as a network error.
 */
async function request<T>(
  url: string,
  parse: (body: unknown) => T,
  signal: AbortSignal,
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
    return await requestOnce(url, parse, signal, attempt.signal, () => timedOut);
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
    response = await untilAborted(fetch(url, { signal: attempt, cache: "no-store" }), attempt);
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
    const apiError = parseApiError(body);
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

export function useJevRun(): UseJevRunResult {
  const [runs, setRuns] = useState<JevResource<JevRunList>>(EMPTY);
  const [selection, setSelection] = useState<Selection>(() => freshSelection(null));

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
      if (outcome.ok && outcome.data.runs.length > 0) {
        // Follow the newest run until the user picks one.
        const newest = outcome.data.runs[0].run_id;
        setSelection((previous) =>
          previous.runId === null ? freshSelection(newest) : previous,
        );
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), JEV_RUN_LIST_POLL_MS);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, []);

  const runId = selection.runId;
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
      setSelection((previous) => (previous.runId === runId ? apply(previous) : previous));

    const loadState = async () => {
      stateInFlight = true;
      let outcome;
      try {
        outcome = await request(base, (body) => parseRunState(body, runId), controller.signal);
      } finally {
        stateInFlight = false;
      }
      if (outcome === null) return;
      update((current) => ({ ...current, run: applyOutcome(current.run, outcome) }));
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

  const selectRun = useCallback((next: string) => {
    if (!isValidRunId(next)) return;
    setSelection((previous) => (previous.runId === next ? previous : freshSelection(next)));
  }, []);

  return {
    runs,
    selectedRunId: runId,
    selectRun,
    run: selection.run,
    policy: selection.policy,
  };
}
