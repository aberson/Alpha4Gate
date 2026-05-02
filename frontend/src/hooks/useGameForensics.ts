import { useApi, type UseApiResult } from "./useApi";
import type { ForensicsResponse } from "../types/forensics";
import { isValidGameId } from "../types/forensics";

/**
 * Per-game forensics hook — Step 8 of the Models-tab build plan.
 *
 * Wraps ``useApi<ForensicsResponse>`` for the
 * ``GET /api/versions/{v}/forensics/{game_id}`` endpoint (Step 1c).
 *
 * Cache key: ``forensics-v1`` per the build-plan convention so a future
 * schema bump can invalidate the IDB entry without touching unrelated
 * caches (see ``feedback_useapi_cache_schema_break.md`` in MEMORY.md).
 *
 * Refresh policy: load-once on game-id change. ``useApi`` already kicks
 * off a fresh fetch every time the endpoint URL changes (its mount
 * effect depends on ``effectiveKey``), so flipping ``game_id`` is enough
 * to retrigger. We do NOT poll — a completed game's forensics is
 * immutable, and live games are surfaced via Live Runs instead.
 *
 * When ``version`` or ``game_id`` is ``null`` (or the id fails the
 * client-side regex) we still mount ``useApi`` (rule of hooks) but with
 * a sentinel empty URL, then return a stable idle ``UseApiResult`` so
 * the caller sees ``data=null`` / ``isStale=false`` until both inputs
 * resolve to a valid pair. This matches ``useVersionDetail``'s pattern.
 */

const IDLE_RESULT: UseApiResult<ForensicsResponse> = {
  data: null,
  isStale: false,
  isLoading: false,
  lastSuccess: null,
  error: null,
  refresh: () => undefined,
};

export function useGameForensics(
  version: string | null,
  game_id: string | null,
): UseApiResult<ForensicsResponse> {
  const isActive =
    version !== null &&
    version !== "" &&
    game_id !== null &&
    game_id !== "" &&
    isValidGameId(game_id);

  const endpoint = isActive
    ? `/api/versions/${version}/forensics/${game_id}`
    : "";
  const cacheKey = isActive
    ? `/api/versions/${version}/forensics/${game_id}::forensics-v1`
    : "";

  // ``useApi`` is mounted unconditionally so the rule-of-hooks holds
  // even when the inputs flip null -> populated mid-render.
  const result = useApi<ForensicsResponse>(endpoint, { cacheKey });

  if (!isActive) return IDLE_RESULT;
  return result;
}
