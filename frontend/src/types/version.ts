/**
 * Version registry row — matches the response shape of ``GET /api/versions``.
 *
 * Backend impl: ``bots/v10/api.py::_scan_versions_sync`` walks each
 * ``bots/vN/manifest.json`` and joins the row against the cross-version
 * advised / evolve / self-play logs to derive ``harness_origin``.
 *
 * ``race`` is the literal ``"protoss"`` until Phase G introduces multi-race
 * versions. The Models tab race-filter dropdown collapses to hidden when all
 * versions resolve to the same race after coercion (today: always protoss).
 */
export interface Version {
  /** Version directory name, e.g. ``"v0"``, ``"v7"``. */
  name: string;
  /**
   * Coerced race string; ``null`` when a manifest pre-dates the multi-race
   * field. Consumers should treat ``null`` as ``"protoss"`` (the historical
   * default — see memory ``feedback_phase_n_dormant_in_current.md``).
   */
  race: string | null;
  /** Parent version name, or ``null`` for the genesis version. */
  parent: string | null;
  /** Harness that promoted this version. */
  harness_origin: "advised" | "evolve" | "manual" | "self-play";
  /** ISO-8601 timestamp written into the manifest at promotion time. */
  timestamp: string | null;
  /** Git sha at the moment of promotion, when the manifest captured one. */
  sha: string | null;
  /** Optional behavioural fingerprint (forensics). */
  fingerprint: string | null;
  /** True for the version pointed to by ``bots/current/current.txt``. */
  current: boolean;
}

/** Set of harness origins; used for the harness-filter chips in the Models tab. */
export const HARNESS_ORIGINS: ReadonlyArray<Version["harness_origin"]> = [
  "advised",
  "evolve",
  "manual",
  "self-play",
] as const;
