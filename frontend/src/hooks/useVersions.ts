import { useApi } from "./useApi";
import type { Version } from "../types/version";

/**
 * Fetches the version registry from ``GET /api/versions`` (Step 3 of the
 * Models-tab plan).
 *
 * Mirrors the ``useEvolveRun`` consumption pattern: a thin wrapper around
 * ``useApi`` that pins the cache key + endpoint and returns a typed
 * surface. The endpoint response is a JSON array of ``Version`` rows
 * (NOT an object envelope), so the generic argument is ``Version[]``.
 *
 * Cache key: bumped to ``v1`` so a future schema change (e.g. Phase G
 * adding ``race`` variants beyond ``"protoss"``, or a forensics field
 * being added) can simply bump the suffix without colliding with cached
 * data — see ``feedback_useapi_cache_schema_break.md`` in MEMORY.md.
 *
 * No polling for now: the registry only changes when a promotion happens
 * (sub-hour cadence even on overnight evolve soaks); the Models tab
 * exposes a manual ``Refresh`` button that calls ``refetch`` instead.
 *
 * The hook forwards ``isStale`` + ``lastSuccess`` from ``useApi`` so the
 * Models tab can render ``<StaleDataBanner />`` when the backend is
 * unreachable — same pattern every other tab uses (see EvolutionTab).
 */
export interface UseVersionsResult {
  versions: Version[];
  loading: boolean;
  /** True when the displayed versions are not known to be fresh — e.g.
   * loaded from IndexedDB cache or after a failed refetch. Drives the
   * stale-data banner. */
  isStale: boolean;
  /** Date of the last successful fetch (or cache read), or ``null`` if
   * neither has happened yet. Passed to ``<StaleDataBanner />``. */
  lastSuccess: Date | null;
  /** Last fetch error message (debug only — do NOT render directly).
   * Type matches ``useApi``'s ``string | null``. */
  error: string | null;
  /** Force an immediate refetch of ``/api/versions``. */
  refetch: () => void;
}

const CACHE_KEY = "/api/versions::versions-v1";

export function useVersions(): UseVersionsResult {
  const { data, isLoading, isStale, lastSuccess, error, refresh } = useApi<
    Version[]
  >("/api/versions", { cacheKey: CACHE_KEY });

  return {
    versions: data ?? [],
    loading: isLoading,
    isStale,
    lastSuccess,
    error,
    refetch: refresh,
  };
}
