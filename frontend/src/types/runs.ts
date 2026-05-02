/**
 * Live-runs row — matches the response shape of ``GET /api/runs/active``.
 *
 * Backend impl: ``bots/v10/api.py::get_runs_active`` aggregates over the
 * three active harnesses (training-daemon in-process, advised state file,
 * per-worker evolve round files) and emits a flat list of rows. One row
 * per harness instance: at most one daemon row, at most one advised row,
 * up to N evolve rows (one per parallel worker).
 *
 * Numeric fields default to ``0`` (not ``null``) when the source file
 * lacks them; ``current_imp`` defaults to ``""``. The endpoint never
 * 500s — empty list on missing files.
 */
export interface RunRow {
  /** Which harness produced this row. New harnesses can extend the
   * literal union; the icon map below should be updated in lockstep. */
  harness: "training-daemon" | "advised" | "evolve" | "self-play" | string;
  /** Bot version label (``"v7"`` etc.). Empty string when the daemon
   * row's data dir doesn't match ``^v\d+$``. */
  version: string;
  /** Phase / status string from the source file. Free-form per-harness
   * vocabulary — this UI does not enumerate them, just renders verbatim. */
  phase: string;
  /** Current improvement title or per-worker label. May be empty. */
  current_imp: string;
  /** Games played in the current eval (0 when not in a games-driven
   * phase like ``claude_prompt`` or ``stack_apply``). */
  games_played: number;
  /** Games total for the current eval (0 when not games-driven). */
  games_total: number;
  /** Current candidate / new-parent score. */
  score_cand: number;
  /** Current parent / prior-parent score. */
  score_parent: number;
  /** ISO-8601 run start; empty string when the source omitted it. */
  started_at: string;
  /** ISO-8601 last-update; sort key. Empty string when the source
   * omitted it (those rows sort last). */
  updated_at: string;
}

/**
 * Per-harness display icon. Emoji rather than an icon library (project
 * has none today, and the build-step problem statement asks for emoji
 * or a library that's already wired in — there isn't one).
 *
 * Unknown harnesses render as ``"?"`` rather than blowing up; new
 * harnesses just need an entry here and the type-union extension above.
 */
export const HARNESS_ICONS: Record<string, string> = {
  "training-daemon": "🤖",
  advised: "💡",
  evolve: "🧬",
  "self-play": "⚔️",
};

/**
 * Pure function — produces the relative-time string the Live-Runs cards
 * show in their top-right corner. ``now`` is injectable so unit tests
 * can pin a deterministic clock without touching ``Date.now``.
 *
 * Vocabulary mirrors ``StaleDataBanner`` so the dashboard reads with one
 * consistent voice:
 *   - <5s        → "just now"
 *   - <60s       → "Ns ago"
 *   - <60min     → "Nm ago"
 *   - <24h       → "Nh ago"
 *   - else       → "Nd ago"
 *
 * Negative deltas (clock skew, race during refresh) collapse to "just
 * now" rather than something nonsensical.
 *
 * Empty / unparseable ``updatedAt`` returns ``"—"``: distinguishes
 * "no source timestamp" from "source said T=now" so an operator can
 * tell when a row's freshness is genuinely unknown.
 */
export function formatRelativeTime(updatedAt: string, now?: number): string {
  if (!updatedAt) return "—";
  const then = Date.parse(updatedAt);
  if (Number.isNaN(then)) return "—";
  const nowMs = typeof now === "number" ? now : Date.now();
  const deltaSec = Math.round((nowMs - then) / 1000);
  if (deltaSec < 5) return "just now";
  if (deltaSec < 60) return `${deltaSec}s ago`;
  if (deltaSec < 3600) {
    const minutes = Math.floor(deltaSec / 60);
    return `${minutes}m ago`;
  }
  if (deltaSec < 86400) {
    const hours = Math.floor(deltaSec / 3600);
    return `${hours}h ago`;
  }
  const days = Math.floor(deltaSec / 86400);
  return `${days}d ago`;
}
