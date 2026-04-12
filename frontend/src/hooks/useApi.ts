import { useCallback, useEffect, useRef, useState } from "react";
import { readCache, writeCache } from "../lib/idbCache";

/**
 * Offline-first data-fetching hook for API endpoints.
 *
 * Part of Phase 1a of the offline-first dashboard work (see
 * `documentation/improvements/offline-first.md`). Replaces the existing
 * per-tab `fetch` + `useEffect` + polling pattern with a single shared
 * hook that:
 *
 *  1. Reads the most recent cached response from IndexedDB on mount.
 *     The cached `data` is available BEFORE the first network fetch
 *     completes, so tabs render from cache even if the backend is down.
 *  2. Kicks off a network fetch in the background. On success, updates
 *     the returned `data` and writes back to IDB.
 *  3. On fetch error, keeps the existing `data` (cached or previously-
 *     fetched) and flips `isStale` to `true`. The component renders
 *     stale data plus a `<StaleDataBanner />` instead of a bare error.
 *  4. Optionally polls at `pollMs` intervals. Each tick either refreshes
 *     the data (success) or marks it stale (failure).
 *
 * Semantic notes:
 *
 *  - `isStale === false` means "the last successful fetch is recent and
 *    (if polling) we haven't missed a poll since." It is true when: the
 *    most recent fetch failed, OR we loaded from IDB cache and haven't
 *    completed a fresh fetch yet.
 *  - `isLoading === true` means "we have NO data to show yet" — neither
 *    cache nor a completed fetch. Use this to show a spinner. Once any
 *    data (even stale) is available, flip to false and render it.
 *  - `lastSuccess` is the Date of the last successful fetch or cache
 *    read, whichever is newer. Used by `<StaleDataBanner />` to show
 *    "Backend offline — showing data from 2 min ago."
 *  - `error` is the most recent error message (for debugging). UI should
 *    NOT render this directly — use `isStale` + a banner instead.
 *  - `refresh()` forces an immediate refetch and resets the poll timer.
 *
 * Generic parameter `T` is the parsed JSON shape. Callers cast the
 * response at the hook boundary.
 */

export interface UseApiOptions {
  /**
   * Poll interval in milliseconds. If omitted, the hook fetches once on
   * mount and does not refetch until `refresh()` is called.
   */
  pollMs?: number;

  /**
   * Override the cache key. Defaults to the endpoint path. Useful when
   * two components hit the same endpoint but want independent caches
   * (rare — in practice you'd just share state).
   */
  cacheKey?: string;
}

export interface UseApiResult<T> {
  /**
   * The most recent data available — either from cache (on mount, before
   * first fetch completes) or from the latest successful fetch. `null`
   * means "no data anywhere yet" — show a loading state.
   */
  data: T | null;

  /**
   * True when the displayed data is not known to be fresh. Specifically
   * true when:
   *   - We loaded from IDB cache and haven't yet completed a fresh fetch.
   *   - The most recent fetch failed (backend unreachable, HTTP error,
   *     parse error).
   * False only when the most recent fetch succeeded.
   */
  isStale: boolean;

  /**
   * True when there is nothing to show yet — neither cached data nor a
   * completed fetch. Use this to distinguish "first paint" from "data
   * available but stale."
   */
  isLoading: boolean;

  /**
   * Date of the last successful fetch, or the cache's `fetchedAt` when
   * we've only loaded from cache so far, or `null` if neither. Used by
   * `<StaleDataBanner />` for the relative-time display.
   */
  lastSuccess: Date | null;

  /**
   * Most recent error message (for logging / debugging). Never render
   * this directly to the user — use `isStale` + banner instead.
   */
  error: string | null;

  /**
   * Force an immediate refetch. Resets the poll timer if pollMs is set.
   */
  refresh: () => void;
}

/**
 * Internal helper: fetch + parse JSON, throw on non-2xx.
 *
 * Kept simple on purpose — Phase 1a scope does not include auth, cancel
 * tokens, or retry with backoff. The hook handles polling, and fetch
 * errors naturally manifest as the browser's own AbortError / TypeError
 * when the backend is unreachable.
 */
async function fetchJson<T>(endpoint: string): Promise<T> {
  const response = await fetch(endpoint);
  if (!response.ok) {
    throw new Error(`${endpoint} returned ${response.status}`);
  }
  return (await response.json()) as T;
}

export function useApi<T>(endpoint: string, options: UseApiOptions = {}): UseApiResult<T> {
  const { pollMs, cacheKey } = options;
  const effectiveKey = cacheKey ?? endpoint;

  const [data, setData] = useState<T | null>(null);
  const [isStale, setIsStale] = useState<boolean>(true);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [lastSuccess, setLastSuccess] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Track mount state so async callbacks don't setState after unmount.
  const mountedRef = useRef<boolean>(true);
  // Track the latest endpoint so in-flight fetches for a previous
  // endpoint don't overwrite state for the new one (rare but possible
  // if a parent re-renders with a different URL).
  const endpointRef = useRef<string>(endpoint);
  endpointRef.current = endpoint;

  const doFetch = useCallback(async () => {
    const url = endpointRef.current;
    try {
      const result = await fetchJson<T>(url);
      if (!mountedRef.current || endpointRef.current !== url) return;
      setData(result);
      setIsStale(false);
      setIsLoading(false);
      setLastSuccess(new Date());
      setError(null);
      // Fire-and-forget the cache write. We don't await it because the
      // UI should update as soon as React state does; the cache write
      // is a durability optimization, not a render-blocking concern.
      void writeCache(effectiveKey, result);
    } catch (e) {
      if (!mountedRef.current || endpointRef.current !== url) return;
      setIsStale(true);
      setIsLoading(false); // We're done loading even though we failed —
      // if cache had data we already rendered it; if it didn't, the
      // caller should check `data === null` and show a first-time
      // error / empty state.
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [effectiveKey]);

  const refresh = useCallback(() => {
    void doFetch();
  }, [doFetch]);

  // Mount: load cache synchronously-ish, then kick off the fetch.
  useEffect(() => {
    mountedRef.current = true;

    // Async cache read. On first paint `data` is null and `isLoading`
    // is true; as soon as the cache resolves we update both. If cache
    // is empty we stay in the loading state until the fetch completes
    // (or errors).
    void (async () => {
      const cached = await readCache<T>(effectiveKey);
      if (!mountedRef.current) return;
      if (cached !== null) {
        setData(cached.data);
        setLastSuccess(new Date(cached.fetchedAt));
        // Even if we have cache, we're still "loading" a fresh value
        // in the background. `isLoading` stays true briefly; consumers
        // who want to render cached data immediately should check
        // `data !== null` instead of `!isLoading`.
        setIsLoading(false);
        // isStale stays true until doFetch succeeds. This is the
        // correct semantics: cached data is always stale relative to
        // the live backend.
      }
    })();

    // Always trigger the first network fetch on mount.
    void doFetch();

    // Optional polling.
    let intervalId: number | undefined;
    if (typeof pollMs === "number" && pollMs > 0) {
      intervalId = window.setInterval(() => {
        void doFetch();
      }, pollMs);
    }

    return () => {
      mountedRef.current = false;
      if (intervalId !== undefined) {
        window.clearInterval(intervalId);
      }
    };
  }, [effectiveKey, doFetch, pollMs]);

  return { data, isStale, isLoading, lastSuccess, error, refresh };
}
