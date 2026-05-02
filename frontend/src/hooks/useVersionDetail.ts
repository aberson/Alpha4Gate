import { useCallback, useMemo } from "react";
import { useApi } from "./useApi";
import type { UnifiedImprovement } from "../components/TimelineList";

/**
 * Per-version aggregator hook — Step 6 of the Models-tab build plan.
 *
 * Issues five parallel ``useApi`` calls for the selected ``version``:
 *
 *   - ``GET /api/versions/{v}/config``           (Step 1a)
 *   - ``GET /api/versions/{v}/training-history`` (Step 1b)
 *   - ``GET /api/versions/{v}/actions``          (Step 1b)
 *   - ``GET /api/versions/{v}/improvements``     (Step 1b)
 *   - ``GET /api/versions/{v}/weight-dynamics``  (Step 1c)
 *
 * Each individual fetch wraps ``useApi`` with a v1 cache key so a future
 * schema bump can invalidate the IDB entry without touching unrelated
 * caches (see ``feedback_useapi_cache_schema_break.md`` in MEMORY.md).
 *
 * The five fetches kick off in parallel — ``useApi`` schedules the
 * network call inside its own ``useEffect`` so by mounting all five at
 * once we get parallelism without an explicit ``Promise.all`` waterfall.
 *
 * The aggregated ``loading`` flag is true ONLY when every endpoint is
 * still on its first paint (no cache, no completed fetch). Any one
 * endpoint having data is enough to flip ``loading`` to false — this
 * matches the "render what you have" pattern used elsewhere on the
 * dashboard. ``isStale`` is the OR of every endpoint's ``isStale`` so
 * a single backend hiccup surfaces the banner.
 *
 * When ``version`` is ``null`` the hook returns an empty result with
 * ``loading=false`` and never issues fetches. ``useApi`` requires a
 * stable endpoint string, so we pass a sentinel ``""`` URL and the
 * caller checks for null upstream — see ``VersionInspector.tsx``.
 *
 * Cache keys (per memory: schema-bump pattern):
 *   - ``version-config-v1``
 *   - ``version-training-history-v1``
 *   - ``version-actions-v1``
 *   - ``version-improvements-v1``
 *   - ``version-weight-dynamics-v1``
 */

// --- Response shapes ----------------------------------------------------

export interface VersionConfig {
  hyperparams: unknown;
  reward_rules: unknown;
  daemon_config: unknown;
}

export interface TrainingHistoryPoint {
  game_id: string;
  ts: string;
  wr: number;
}

export interface TrainingHistory {
  rolling_10: TrainingHistoryPoint[];
  rolling_50: TrainingHistoryPoint[];
  rolling_overall: TrainingHistoryPoint[];
}

export interface ActionDistributionRow {
  action_id: number;
  name: string;
  count: number;
  pct: number;
}

export interface WeightDynamicsRow {
  checkpoint: string;
  ts: string | null;
  l2_per_layer: Record<string, number> | null;
  kl_from_parent: number | null;
  canary_source: string | null;
  error: string | null;
}

// --- Hook surface -------------------------------------------------------

export interface UseVersionDetailResult {
  config: VersionConfig | null;
  trainingHistory: TrainingHistory | null;
  actions: ActionDistributionRow[] | null;
  improvements: UnifiedImprovement[] | null;
  weightDynamics: WeightDynamicsRow[] | null;
  loading: boolean;
  /** Aggregated stale flag — OR of every endpoint's ``isStale``. */
  isStale: boolean;
  /** Most recent ``lastSuccess`` across the five fetches. ``null`` when
   * none have completed. */
  lastSuccess: Date | null;
  /** First non-null error across the five fetches (debug only). */
  error: string | null;
  /** Re-issue every fetch in parallel. */
  refetch: () => void;
}

const NULL_RESULT: UseVersionDetailResult = {
  config: null,
  trainingHistory: null,
  actions: null,
  improvements: null,
  weightDynamics: null,
  loading: false,
  isStale: false,
  lastSuccess: null,
  error: null,
  refetch: () => undefined,
};

/**
 * Sentinel endpoint used when the caller passes ``version=null``.
 * ``useApi`` accepts any string but won't actually fetch when the
 * endpoint resolves to nothing meaningful — we always check the
 * sentinel and short-circuit at the parent (``VersionInspector``).
 */
const NOOP_ENDPOINT = "";

export function useVersionDetail(
  version: string | null,
): UseVersionDetailResult {
  // ``useApi`` needs a non-changing endpoint string for its
  // ``useEffect`` dependency. When ``version`` flips between nulls and
  // real values we want the hook to discard prior state, so each
  // endpoint is keyed by ``version``. The hook always mounts the same
  // five ``useApi`` calls (rule-of-hooks), but ``version=null`` makes
  // every endpoint a sentinel.
  const isActive = version !== null && version !== "";
  const v = isActive ? version : "__inactive__";

  const configRes = useApi<VersionConfig>(
    isActive ? `/api/versions/${v}/config` : NOOP_ENDPOINT,
    { cacheKey: isActive ? `/api/versions/${v}/config::version-config-v1` : "" },
  );
  const trainingRes = useApi<TrainingHistory>(
    isActive ? `/api/versions/${v}/training-history` : NOOP_ENDPOINT,
    {
      cacheKey: isActive
        ? `/api/versions/${v}/training-history::version-training-history-v1`
        : "",
    },
  );
  const actionsRes = useApi<ActionDistributionRow[]>(
    isActive ? `/api/versions/${v}/actions` : NOOP_ENDPOINT,
    {
      cacheKey: isActive
        ? `/api/versions/${v}/actions::version-actions-v1`
        : "",
    },
  );
  const improvementsRes = useApi<UnifiedImprovement[]>(
    isActive ? `/api/versions/${v}/improvements` : NOOP_ENDPOINT,
    {
      cacheKey: isActive
        ? `/api/versions/${v}/improvements::version-improvements-v1`
        : "",
    },
  );
  const weightDynamicsRes = useApi<WeightDynamicsRow[]>(
    isActive ? `/api/versions/${v}/weight-dynamics` : NOOP_ENDPOINT,
    {
      cacheKey: isActive
        ? `/api/versions/${v}/weight-dynamics::version-weight-dynamics-v1`
        : "",
    },
  );

  // Aggregate flags. ``loading`` is true ONLY when every endpoint is
  // pre-first-paint; once any one returns we flip to false so the panel
  // can render partially.
  const allLoading =
    configRes.isLoading &&
    trainingRes.isLoading &&
    actionsRes.isLoading &&
    improvementsRes.isLoading &&
    weightDynamicsRes.isLoading;

  const anyStale =
    configRes.isStale ||
    trainingRes.isStale ||
    actionsRes.isStale ||
    improvementsRes.isStale ||
    weightDynamicsRes.isStale;

  const newestSuccess = useMemo<Date | null>(() => {
    const candidates = [
      configRes.lastSuccess,
      trainingRes.lastSuccess,
      actionsRes.lastSuccess,
      improvementsRes.lastSuccess,
      weightDynamicsRes.lastSuccess,
    ].filter((d): d is Date => d !== null);
    if (candidates.length === 0) return null;
    return candidates.reduce(
      (acc, cur) => (cur.getTime() > acc.getTime() ? cur : acc),
      candidates[0],
    );
  }, [
    configRes.lastSuccess,
    trainingRes.lastSuccess,
    actionsRes.lastSuccess,
    improvementsRes.lastSuccess,
    weightDynamicsRes.lastSuccess,
  ]);

  const firstError =
    configRes.error ??
    trainingRes.error ??
    actionsRes.error ??
    improvementsRes.error ??
    weightDynamicsRes.error;

  const refetch = useCallback(() => {
    configRes.refresh();
    trainingRes.refresh();
    actionsRes.refresh();
    improvementsRes.refresh();
    weightDynamicsRes.refresh();
  }, [
    configRes,
    trainingRes,
    actionsRes,
    improvementsRes,
    weightDynamicsRes,
  ]);

  if (!isActive) {
    return NULL_RESULT;
  }

  return {
    config: configRes.data,
    trainingHistory: trainingRes.data,
    actions: actionsRes.data,
    improvements: improvementsRes.data,
    weightDynamics: weightDynamicsRes.data,
    loading: allLoading,
    isStale: anyStale,
    lastSuccess: newestSuccess,
    error: firstError,
    refetch,
  };
}
