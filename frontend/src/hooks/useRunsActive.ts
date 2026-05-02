import { useApi } from "./useApi";
import type { RunRow } from "../types/runs";

/**
 * Live-runs hook — wraps ``useApi`` for ``GET /api/runs/active`` (Step
 * 5 of the Models-tab build plan, backed by the Step 1c aggregator).
 *
 * Polls every 2s like the other live surfaces in the project — same
 * cadence used by ``useEvolveRun`` for ``/api/evolve/current-round``
 * and ``/api/evolve/running-rounds``. The grid renders zero-to-many
 * cards from ``data ?? []``, so the empty / first-paint render is
 * naturally an empty list rather than ``null``.
 *
 * Cache key bumped to ``runs-active-v1`` per
 * ``feedback_useapi_cache_schema_break.md``: future iterations may add
 * new harness types or extend ``RunRow`` (e.g. forensics game id), and
 * keying on the endpoint alone would let returning visitors render a
 * stale shape before the network round-trip.
 *
 * Surface convention: forward every ``UseApiResult`` field — renaming
 * ``data → runs`` and ``isLoading → loading``, but preserving
 * ``isStale``, ``lastSuccess``, and ``error`` verbatim. Mirrors
 * ``useVersions`` / ``useLineage`` so the consumer's prop wiring is
 * uniform across Models-tab sub-views.
 */

const CACHE_KEY = "/api/runs/active::runs-active-v1";

export interface UseRunsActiveResult {
  /** Live-runs rows. Empty array (NOT ``null``) when nothing is active
   * or before the first fetch resolves — lets ``LiveRunsGrid`` render
   * its empty-state card without a null-guard. */
  runs: RunRow[];
  /** True when no data is on screen yet. */
  loading: boolean;
  /** True when displayed data is not known to be fresh (cache hit
   * pre-fetch, or last fetch failed). Drives ``<StaleDataBanner />``. */
  isStale: boolean;
  /** Date of the last successful fetch / cache read, or ``null``. */
  lastSuccess: Date | null;
  /** Last fetch error (debug only — never render directly). */
  error: string | null;
  /** Force an immediate refetch and reset the poll timer. */
  refetch: () => void;
}

export function useRunsActive(): UseRunsActiveResult {
  const { data, isLoading, isStale, lastSuccess, error, refresh } = useApi<
    RunRow[]
  >("/api/runs/active", { cacheKey: CACHE_KEY, pollMs: 2000 });
  return {
    runs: data ?? [],
    loading: isLoading,
    isStale,
    lastSuccess,
    error,
    refetch: refresh,
  };
}
