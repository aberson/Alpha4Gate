import { useEffect, useMemo, useState } from "react";
import { useRunsActive } from "../hooks/useRunsActive";
import { StaleDataBanner } from "./StaleDataBanner";
import {
  HARNESS_ICONS,
  formatRelativeTime,
  type RunRow,
} from "../types/runs";

/**
 * Live Runs grid — Step 5 of the Models-tab build plan.
 *
 * Replaces the placeholder body of the ``"live"`` Models sub-view with
 * one card per active harness:
 *
 *     [icon] [HARNESS @ version]               [updated 3s ago]
 *     Phase: regression  ·  Imp: "Splash readiness"   [▶ expand]
 *     ████████░░░░░░  6/10 games  ·  cand 4 vs parent 2
 *
 * Behaviour:
 *   - Polls ``/api/runs/active`` every 2s via ``useRunsActive``. The
 *     hook also emits ``isStale`` / ``lastSuccess`` so the grid can
 *     render ``<StaleDataBanner />`` above the cards when the backend
 *     goes offline (uniform with EvolutionTab + ModelsTab shell).
 *   - Per-card relative-time text (``"updated 3s ago"``) is recomputed
 *     locally on a 2s tick so the wording moves between polls instead
 *     of freezing for two seconds at a time. ``formatRelativeTime``
 *     itself is a pure helper exported from ``types/runs.ts`` and is
 *     unit-tested directly with a mocked ``Date.now``.
 *   - Each card has a native ``<details>`` disclosure that reveals the
 *     full row JSON. Native HTML keeps the keyboard / a11y story right
 *     and avoids tracking another piece of per-card state.
 *   - Empty list renders ONE muted "No active runs." card so the panel
 *     is never visually blank — operator can distinguish "loading" from
 *     "nothing happening."
 *
 * Self-contained: takes no props. The grid is mounted inside the
 * Models-tab sub-view router; ``ModelsTab`` is the only caller and it
 * doesn't need to inject any state.
 */

// Per-harness accent border colour. Mirrors the EvolutionTab phase
// palette so the same harness reads visually consistent across tabs.
// Falls back to a neutral grey for unknown harnesses.
const HARNESS_ACCENT: Record<string, string> = {
  "training-daemon": "#3498db",
  advised: "#f1c40f",
  evolve: "#16a085",
  "self-play": "#9b59b6",
};

function harnessIcon(harness: string): string {
  return HARNESS_ICONS[harness] ?? "?";
}

function harnessAccent(harness: string): string {
  return HARNESS_ACCENT[harness] ?? "#888";
}

function ProgressBar({ played, total }: { played: number; total: number }) {
  const pct = total > 0 ? Math.min(100, (played / total) * 100) : 0;
  return (
    <div
      data-testid="run-progress-bar"
      style={{
        marginTop: "6px",
        height: "8px",
        backgroundColor: "rgba(255,255,255,0.08)",
        borderRadius: "4px",
        overflow: "hidden",
      }}
    >
      <div
        data-testid="run-progress-bar-fill"
        style={{
          width: `${pct}%`,
          height: "100%",
          backgroundColor: "#3182ce",
          transition: "width 300ms ease-out",
        }}
      />
    </div>
  );
}

interface RunCardProps {
  row: RunRow;
  // Anchor for relative-time formatting. Driven by a 2s ticker in the
  // grid so every card refreshes its "updated 3s ago" text on the same
  // schedule, in lockstep with the polling refresh.
  nowMs: number;
}

function RunCard({ row, nowMs }: RunCardProps) {
  const accent = harnessAccent(row.harness);
  const icon = harnessIcon(row.harness);
  const showProgress = row.games_total > 0;
  const showScore =
    row.score_cand > 0 || row.score_parent > 0 || row.games_played > 0;
  const versionLabel = row.version ? `@ ${row.version}` : "";
  const relTime = formatRelativeTime(row.updated_at, nowMs);

  return (
    <section
      className="stat-card"
      aria-label={`run ${row.harness}`}
      data-testid={`run-card-${row.harness}`}
      style={{
        padding: "12px 16px",
        borderLeft: `4px solid ${accent}`,
        backgroundColor: "rgba(255,255,255,0.02)",
        borderRadius: "6px",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: "10px",
          marginBottom: "6px",
        }}
      >
        <span
          aria-hidden="true"
          style={{ fontSize: "1.2em" }}
          data-testid={`run-icon-${row.harness}`}
        >
          {icon}
        </span>
        <strong style={{ fontSize: "0.95em", textTransform: "uppercase" }}>
          {row.harness}
        </strong>
        {versionLabel ? (
          <span style={{ color: "#aaa", fontSize: "0.9em" }}>
            {versionLabel}
          </span>
        ) : null}
        <span
          data-testid={`run-updated-${row.harness}`}
          style={{
            marginLeft: "auto",
            color: "#888",
            fontSize: "0.8em",
          }}
        >
          updated {relTime}
        </span>
      </header>

      <div
        style={{
          fontSize: "0.9em",
          color: "#ddd",
          marginBottom: "4px",
        }}
      >
        Phase: <code>{row.phase || "—"}</code>
        {row.current_imp ? (
          <>
            {" · "}
            Imp: <em>"{row.current_imp}"</em>
          </>
        ) : null}
      </div>

      {showProgress ? (
        <>
          <ProgressBar
            played={row.games_played}
            total={row.games_total}
          />
          <div
            style={{
              marginTop: "4px",
              fontSize: "0.85em",
              color: "#aaa",
            }}
          >
            <span data-testid={`run-progress-${row.harness}`}>
              {row.games_played}/{row.games_total} games
            </span>
            {showScore ? (
              <>
                {" · "}
                <span data-testid={`run-score-${row.harness}`}>
                  cand {row.score_cand} vs parent {row.score_parent}
                </span>
              </>
            ) : null}
          </div>
        </>
      ) : null}

      <details
        style={{ marginTop: "8px", fontSize: "0.85em" }}
        data-testid={`run-details-${row.harness}`}
      >
        <summary
          style={{ cursor: "pointer", color: "#888" }}
          data-testid={`run-expand-${row.harness}`}
        >
          expand
        </summary>
        <pre
          data-testid={`run-state-json-${row.harness}`}
          style={{
            marginTop: "6px",
            padding: "8px",
            backgroundColor: "rgba(0,0,0,0.3)",
            borderRadius: "4px",
            fontSize: "0.85em",
            overflowX: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
          }}
        >
          {JSON.stringify(row, null, 2)}
        </pre>
      </details>
    </section>
  );
}

function EmptyCard() {
  return (
    <section
      className="stat-card"
      aria-label="no active runs"
      data-testid="run-card-empty"
      style={{
        padding: "16px 20px",
        borderLeft: "4px solid #555",
        backgroundColor: "rgba(255,255,255,0.01)",
        borderRadius: "6px",
        opacity: 0.55,
        color: "#888",
        fontSize: "0.95em",
      }}
    >
      No active runs.
    </section>
  );
}

export interface LiveRunsGridProps {
  // #267: harness chips on the Models tab filter the runs grid by
  // ``row.harness``. ``training-daemon`` rows are not chip-able and
  // always pass through; the chip-able subset is governed by
  // ``passesHarnessFilter`` in ModelsTab.
  harnessFilter?: Set<string>;
}

const CHIPPABLE_RUN_HARNESSES = new Set<string>([
  "advised",
  "evolve",
  "manual",
  "self-play",
]);

export function LiveRunsGrid({ harnessFilter }: LiveRunsGridProps = {}) {
  const { runs, isStale, lastSuccess } = useRunsActive();

  // Local 2s ticker so each card's "updated Ns ago" wording stays in
  // step with the polling refresh, instead of freezing between polls.
  // Initialised from Date.now() so first paint shows a sensible value
  // even before the first tick fires.
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => {
      setNowMs(Date.now());
    }, 2000);
    return () => window.clearInterval(id);
  }, []);

  // Apply chip filter when provided. Non-chip-able harnesses (e.g.
  // ``training-daemon``) always pass through so the daemon row never
  // disappears just because none of the four version-origin chips
  // match.
  const visibleRuns = useMemo(() => {
    if (harnessFilter === undefined) return runs;
    return runs.filter((row) => {
      if (!CHIPPABLE_RUN_HARNESSES.has(row.harness)) return true;
      return harnessFilter.has(row.harness);
    });
  }, [runs, harnessFilter]);

  return (
    <div className="live-runs-grid" data-testid="live-runs-grid">
      {isStale ? (
        <StaleDataBanner lastSuccess={lastSuccess} label="Live Runs" />
      ) : null}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          gap: "12px",
        }}
      >
        {visibleRuns.length === 0 ? (
          <EmptyCard />
        ) : (
          visibleRuns.map((row, i) => (
            <RunCard
              key={`${row.harness}-${row.version}-${i}`}
              row={row}
              nowMs={nowMs}
            />
          ))
        )}
      </div>
    </div>
  );
}

export default LiveRunsGrid;
