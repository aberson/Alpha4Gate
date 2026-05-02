import { useApi } from "./useApi";
import type { LineageDAG } from "../types/lineage";

/**
 * Lineage DAG hook — wraps ``useApi`` for ``GET /api/lineage``.
 *
 * The lineage data is append-only on the order of hours-to-days
 * (one new row per promotion), so no polling is configured. The hook
 * relies on the ``useApi`` mount-fetch + manual ``refetch`` semantics
 * — same shape as the rest of the Models-tab hooks.
 *
 * Cache key bumped to ``lineage-v1`` (defensive — ``feedback_useapi
 * _cache_schema_break.md``: future iterations may extend the
 * ``LineageNode`` shape with multi-race or fingerprint fields, and
 * keying on the endpoint alone would let returning visitors render
 * a stale shape before the network round-trip).
 *
 * Surface convention: forward every ``UseApiResult`` field — renaming
 * ``data → lineage``, ``isLoading → loading``, and ``refresh →
 * refetch``, but preserving ``isStale``, ``lastSuccess``, and ``error``
 * verbatim. Mirrors ``useVersions``: a thin wrapper that does NOT drop
 * fields from ``useApi`` (Step 5+ Inspector / Compare views will likely
 * consume ``isStale`` + ``lastSuccess`` for stale-banner integration).
 */

const CACHE_KEY = "/api/lineage::lineage-v1";

export interface UseLineageResult {
  /** Parsed ``GET /api/lineage`` payload, or ``null`` if neither cache
   * nor a completed fetch has yielded data yet. */
  lineage: LineageDAG | null;
  /** True when no data is on screen yet (renamed from ``isLoading`` to
   * match the older ``UseVersionsResult`` convention). */
  loading: boolean;
  /** True when the displayed lineage is not known to be fresh — e.g.
   * loaded from IndexedDB cache or after a failed refetch. Drives the
   * stale-data banner. */
  isStale: boolean;
  /** Date of the last successful fetch (or cache read), or ``null`` if
   * neither has happened yet. Passed to ``<StaleDataBanner />``. */
  lastSuccess: Date | null;
  /** Last fetch error message (debug only — do NOT render directly). */
  error: string | null;
  /** Force an immediate refetch of ``/api/lineage`` (renamed from
   * ``refresh`` to match ``UseVersionsResult``). */
  refetch: () => void;
}

export function useLineage(): UseLineageResult {
  const { data, isLoading, isStale, lastSuccess, error, refresh } =
    useApi<LineageDAG>("/api/lineage", { cacheKey: CACHE_KEY });
  return {
    lineage: data,
    loading: isLoading,
    isStale,
    lastSuccess,
    error,
    refetch: refresh,
  };
}
