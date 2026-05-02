/**
 * Per-game forensics response — matches the response shape of
 * ``GET /api/versions/{v}/forensics/{game_id}``.
 *
 * Backend impl: ``bots/v10/api.py::_forensics_sync`` reads the
 * ``transitions`` rows for ``(version, game_id)`` from the per-version
 * ``training.db`` and assembles the trajectory + give-up + (placeholder)
 * expert-dispatch fields. The endpoint NEVER 500s on missing data — when
 * the game id is not found it returns an empty trajectory with
 * ``give_up_fired=False`` and null fields.
 *
 * ``expert_dispatch`` is always ``null`` until Phase O (Hydra meta-
 * controller) writes the column. The Forensics view shows a "Phase O
 * pending" placeholder card regardless of the value, so consumers don't
 * need to distinguish ``null`` from "not yet shipped".
 */
export interface ForensicsTrajectoryPoint {
  /** Step index inside the game; monotonically increasing. */
  step: number;
  /**
   * Win-probability in ``[0, 1]`` from the give-up heuristic. ``null`` for
   * steps that pre-date the heuristic snapshot or were dropped by the
   * sampling cadence (the heuristic only logs every Nth step — see
   * ``bots/v10/learning/winprob_heuristic.py``).
   */
  win_prob: number | null;
  /** ISO-8601 timestamp when the row was inserted. */
  ts: string;
}

export interface ForensicsResponse {
  trajectory: ForensicsTrajectoryPoint[];
  /** True when the give-up trigger fired during the game. */
  give_up_fired: boolean;
  /** Step index where the give-up trigger fired; ``null`` when it didn't. */
  give_up_step: number | null;
  /**
   * Per-expert dispatch summary; always ``null`` pre-Phase-O. The
   * Forensics view renders a "Phase O pending" placeholder regardless.
   */
  expert_dispatch: unknown | null;
}

/**
 * Client-side game-id validation regex — mirrors the backend's
 * ``_validate_game_id`` (``^[A-Za-z0-9_-]{1,128}$``). Used by
 * ``ForensicsView`` before issuing the fetch so a malformed id never
 * makes it past the dropdown.
 */
export const GAME_ID_RE: RegExp = /^[A-Za-z0-9_-]{1,128}$/;

/** True when ``game_id`` matches the backend's validator. */
export function isValidGameId(game_id: string): boolean {
  return GAME_ID_RE.test(game_id);
}
