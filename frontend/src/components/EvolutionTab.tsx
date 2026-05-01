import { useState, useCallback } from "react";
import { useEvolveRun } from "../hooks/useEvolveRun";
import type {
  EvolveCurrentRound,
  EvolvePoolItem,
  EvolvePoolStatus,
  EvolveRoundResult,
  EvolveRunState,
  EvolveLastResult,
  RunningRound,
} from "../hooks/useEvolveRun";
import { StaleDataBanner } from "./StaleDataBanner";
import { ConfirmDialog } from "./ConfirmDialog";

// Project a per-worker RunningRound (Step 5 of the evolve-parallelization
// plan; 11 fields) into the legacy EvolveCurrentRound shape (14 fields)
// so the existing CurrentPhaseCard renderer can be reused per-worker
// inside the Step 6 grid without branching on field presence. Fields
// that are dispatcher-level (imp_rank/imp_index, stacked_titles for
// stack-apply, new_parent/prior_parent for regression) are filled in as
// nulls/[] -- they are never populated in per-worker round files because
// stack-apply and regression run on the dispatcher, not on workers.
function projectRunningRoundToCurrent(
  rr: RunningRound,
): EvolveCurrentRound & { worker_id: number } {
  return {
    active: rr.active,
    generation: null,
    phase: rr.phase,
    imp_title: rr.imp_title,
    imp_rank: null,
    imp_index: null,
    candidate: rr.candidate,
    stacked_titles: [],
    new_parent: null,
    prior_parent: null,
    games_played: rr.games_played,
    games_total: rr.games_total,
    score_cand: rr.score_cand,
    score_parent: rr.score_parent,
    updated_at: rr.updated_at,
    worker_id: rr.worker_id,
  };
}

// Small "[W<id>]" worker badge shown in the top-left corner of every
// per-worker card in the grid. Differentiates which fan-out slot a
// given card is rendering when concurrency >= 2.
function WorkerBadge({ workerId }: { workerId: number }) {
  return (
    <span
      data-testid={`worker-badge-${workerId}`}
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: "3px",
        backgroundColor: "rgba(255,255,255,0.08)",
        color: "#aaa",
        fontFamily: "monospace",
        fontSize: "0.8em",
        fontWeight: 600,
      }}
    >
      W{workerId}
    </span>
  );
}

// Dim placeholder card used in the grid when a worker slot has no
// active round yet (idle skeleton from /api/evolve/running-rounds).
// Renders with the same outer dimensions as a populated card so the
// grid doesn't reflow when a worker picks up its first round. Always
// rendered inside the grid (gap: 16px owns row spacing), so we drop
// the per-card vertical margins -- otherwise gap+margins double-space
// row-to-row in multi-row layouts (Step 6 review finding #2).
function idleCopyForPhase(phase: string | null | undefined): string {
  switch (phase) {
    case "mirror_games":
      return "Mirror games done — waiting for advisor";
    case "claude_prompt":
      return "Waiting for advisor to propose imps…";
    case "pool_refresh":
      return "Waiting for advisor to refill pool…";
    case "stack_apply":
      return "Waiting — dispatcher applying stack";
    case "regression":
      return "Waiting — dispatcher running regression check";
    case "fitness":
      return "Waiting for next imp…";
    default:
      return "Idle";
  }
}

function IdleWorkerCard({
  workerId,
  dispatcherPhase,
}: {
  workerId: number;
  dispatcherPhase?: string | null;
}) {
  return (
    <section
      className="stat-card"
      aria-label={`worker ${workerId} idle`}
      data-testid={`worker-card-idle-${workerId}`}
      style={{
        padding: "20px 24px",
        borderLeft: "4px solid #555",
        backgroundColor: "rgba(255,255,255,0.01)",
        borderRadius: "6px",
        opacity: 0.55,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "12px",
          marginBottom: "12px",
        }}
      >
        <WorkerBadge workerId={workerId} />
        <span
          style={{
            color: "#888",
            fontSize: "0.95em",
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "1px",
          }}
        >
          idle
        </span>
      </div>
      <div style={{ color: "#666", fontSize: "0.9em" }}>
        {idleCopyForPhase(dispatcherPhase)}
      </div>
    </section>
  );
}

// --- Helper components ---

function StatusBadge({ status }: { status: EvolveRunState["status"] }) {
  const colors: Record<string, string> = {
    idle: "#888",
    running: "#2ecc71",
    completed: "#3498db",
    failed: "#e74c3c",
  };
  return (
    <span
      className="state-badge"
      style={{
        display: "inline-block",
        padding: "4px 10px",
        borderRadius: "4px",
        backgroundColor: colors[status] ?? "#888",
        color: "#fff",
        fontWeight: 600,
        textTransform: "uppercase",
        fontSize: "0.85em",
      }}
    >
      {status}
    </span>
  );
}

function PoolStatusBadge({ status }: { status: EvolvePoolStatus | string }) {
  const palette: Record<string, { bg: string; fg: string }> = {
    active: { bg: "rgba(46, 204, 113, 0.2)", fg: "#2ecc71" },
    "fitness-pass": { bg: "rgba(52, 152, 219, 0.2)", fg: "#3498db" },
    "fitness-close": { bg: "rgba(241, 196, 15, 0.2)", fg: "#f1c40f" },
    evicted: { bg: "rgba(127, 140, 141, 0.2)", fg: "#95a5a6" },
    promoted: { bg: "rgba(46, 204, 113, 0.3)", fg: "#27ae60" },
    "regression-rollback": { bg: "rgba(231, 76, 60, 0.2)", fg: "#e74c3c" },
  };
  const tone = palette[status] ?? { bg: "rgba(136,136,136,0.2)", fg: "#888" };
  return (
    <span
      className={`pool-status-badge pool-status-${status}`}
      data-testid={`pool-status-${status}`}
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: "3px",
        backgroundColor: tone.bg,
        color: tone.fg,
        fontSize: "0.8em",
        fontWeight: 600,
        textTransform: "uppercase",
      }}
    >
      {status}
    </span>
  );
}

function OutcomeBadge({ outcome }: { outcome: string }) {
  const palette: Record<string, { bg: string; fg: string }> = {
    "fitness-pass": { bg: "rgba(52, 152, 219, 0.2)", fg: "#3498db" },
    "fitness-close": { bg: "rgba(241, 196, 15, 0.2)", fg: "#f1c40f" },
    "fitness-fail": { bg: "rgba(127, 140, 141, 0.2)", fg: "#95a5a6" },
    "stack-apply-pass": { bg: "rgba(46, 204, 113, 0.3)", fg: "#27ae60" },
    "stack-apply-import-fail": {
      bg: "rgba(230, 126, 34, 0.25)",
      fg: "#e67e22",
    },
    "stack-apply-commit-fail": {
      bg: "rgba(230, 126, 34, 0.25)",
      fg: "#e67e22",
    },
    "regression-pass": { bg: "rgba(26, 188, 156, 0.25)", fg: "#16a085" },
    "regression-rollback": { bg: "rgba(231, 76, 60, 0.2)", fg: "#e74c3c" },
    crash: { bg: "rgba(155, 89, 182, 0.2)", fg: "#9b59b6" },
  };
  const tone = palette[outcome] ?? { bg: "rgba(136,136,136,0.2)", fg: "#888" };
  return (
    <span
      data-testid={`outcome-${outcome}`}
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: "3px",
        backgroundColor: tone.bg,
        color: tone.fg,
        fontSize: "0.8em",
        fontWeight: 600,
      }}
    >
      {outcome}
    </span>
  );
}

function LastResultCard({ last }: { last: EvolveLastResult }) {
  const stackedTitles = Array.isArray(last.stacked_titles)
    ? last.stacked_titles
    : [];
  const titleNode =
    last.phase === "stack_apply" && stackedTitles.length > 0
      ? (
          <>
            <strong>Stack of {stackedTitles.length}</strong>
            <span style={{ color: "#aaa", fontSize: "0.9em" }}>
              {" "}— {stackedTitles.join(", ")}
            </span>
          </>
        )
      : last.phase === "regression"
      ? <strong>Regression check</strong>
      : <strong>{last.imp_title ?? "(unknown)"}</strong>;

  // Defensive: cached payloads from pre-v2 schema may lack `score`.
  const score = Array.isArray(last.score) ? last.score : null;

  return (
    <div
      className="stat-card"
      style={{
        marginBottom: "16px",
        borderLeft: "3px solid #3498db",
        paddingLeft: "12px",
      }}
    >
      <label>
        Last Phase — Generation #{last.generation_index ?? "?"} ({last.phase ?? "?"})
      </label>
      <div style={{ marginTop: "4px" }}>
        <div style={{ fontSize: "0.95em" }}>{titleNode}</div>
        <div style={{ fontSize: "0.85em", marginTop: "6px", color: "#aaa" }}>
          Score:{" "}
          <code>
            {score !== null ? `${score[0]}-${score[1]}` : "—"}
          </code>
          {" | "}
          Outcome: <OutcomeBadge outcome={last.outcome ?? "?"} />
        </div>
        {last.reason ? (
          <div style={{ fontSize: "0.85em", marginTop: "4px", color: "#888" }}>
            Reason: {last.reason}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function PhaseBadge({ phase }: { phase: string | null }) {
  const label = phase ?? "?";
  const palette: Record<
    string,
    { bg: string; fg: string; display: string }
  > = {
    starting: {
      bg: "rgba(136,136,136,0.2)",
      fg: "#888",
      display: "STARTING",
    },
    mirror_games: {
      bg: "rgba(155, 89, 182, 0.25)",
      fg: "#9b59b6",
      display: "SEEDING",
    },
    claude_prompt: {
      bg: "rgba(46, 204, 113, 0.2)",
      fg: "#2ecc71",
      display: "ADVISOR",
    },
    fitness: {
      bg: "rgba(52, 152, 219, 0.2)",
      fg: "#3498db",
      display: "FITNESS",
    },
    stack_apply: {
      bg: "rgba(26, 188, 156, 0.25)",
      fg: "#16a085",
      display: "STACK APPLY",
    },
    regression: {
      bg: "rgba(230, 126, 34, 0.25)",
      fg: "#e67e22",
      display: "REGRESSION",
    },
    pool_refresh: {
      bg: "rgba(155, 89, 182, 0.2)",
      fg: "#8e44ad",
      display: "POOL REFRESH",
    },
  };
  const tone = palette[label] ?? {
    bg: "rgba(136,136,136,0.2)",
    fg: "#888",
    display: label,
  };
  return (
    <span
      data-testid={`round-phase-${label}`}
      style={{
        display: "inline-block",
        padding: "4px 12px",
        borderRadius: "4px",
        backgroundColor: tone.bg,
        color: tone.fg,
        fontSize: "0.95em",
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "1px",
      }}
    >
      {tone.display}
    </span>
  );
}

function ProgressBar({ pct, color }: { pct: number; color: string }) {
  return (
    <div
      style={{
        marginTop: "12px",
        height: "10px",
        backgroundColor: "rgba(255,255,255,0.08)",
        borderRadius: "5px",
        overflow: "hidden",
      }}
    >
      <div
        data-testid="current-round-progress-bar"
        style={{
          width: `${pct}%`,
          height: "100%",
          backgroundColor: color,
          transition: "width 300ms ease-out",
        }}
      />
    </div>
  );
}

function IndefiniteBar({ color }: { color: string }) {
  return (
    <div
      data-testid="current-round-indefinite-bar"
      style={{
        marginTop: "12px",
        display: "flex",
        gap: "6px",
        height: "10px",
      }}
    >
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          style={{
            flex: 1,
            backgroundColor: color,
            opacity: 0.35,
            borderRadius: "5px",
            animation: `pulse 1.2s ease-in-out ${i * 0.2}s infinite`,
          }}
        />
      ))}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 0.25; }
          50% { opacity: 0.85; }
        }
      `}</style>
    </div>
  );
}

function MatchupGrid({
  leftTitle,
  leftId,
  rightTitle,
  rightId,
  scoreLeft,
  scoreRight,
  accentColor,
}: {
  leftTitle: string;
  leftId: string;
  rightTitle: string;
  rightId: string;
  scoreLeft: number;
  scoreRight: number;
  accentColor: string;
}) {
  return (
    <div
      data-testid="current-round-matchup"
      style={{
        display: "grid",
        gridTemplateColumns: "1fr auto 1fr",
        alignItems: "center",
        gap: "16px",
        marginTop: "8px",
        fontSize: "0.9em",
      }}
    >
      <div style={{ textAlign: "right" }}>
        <div style={{ fontWeight: 600, fontSize: "1em", lineHeight: 1.25 }}>
          {leftTitle}
        </div>
        <div style={{ color: "#888", fontSize: "0.8em", marginTop: "2px" }}>
          <code>{leftId}</code>
        </div>
      </div>
      <div
        style={{
          fontSize: "1.6em",
          fontWeight: 700,
          color: accentColor,
          whiteSpace: "nowrap",
        }}
        data-testid="current-round-score"
      >
        {scoreLeft}
        <span style={{ color: "#555", margin: "0 8px" }}>–</span>
        {scoreRight}
      </div>
      <div style={{ textAlign: "left" }}>
        <div style={{ fontWeight: 600, fontSize: "1em", lineHeight: 1.25 }}>
          {rightTitle}
        </div>
        <div style={{ color: "#888", fontSize: "0.8em", marginTop: "2px" }}>
          <code>{rightId}</code>
        </div>
      </div>
    </div>
  );
}

function CurrentPhaseCard({
  round,
  runParent,
  workerId,
}: {
  round: EvolveCurrentRound;
  runParent: string | null;
  // When set (Step 6 grid layout, concurrency>=2), a "[W<id>]" badge
  // is rendered in the card header, the data-testid is keyed by
  // worker_id (so multiple cards in the same DOM don't collide on
  // getByTestId), and the per-card vertical margins are dropped so
  // the grid `gap` is the sole source of row spacing. When undefined
  // (legacy single-card N=1 path), the card is byte-identical to
  // pre-parallel master.
  workerId?: number;
}) {
  const inGrid = workerId !== undefined;
  const phase = round.phase ?? "starting";
  const total = round.games_total ?? 0;
  const played = round.games_played ?? 0;
  const pct = total > 0 ? Math.min(100, (played / total) * 100) : 0;
  const scoreCand = round.score_cand ?? 0;
  const scoreParent = round.score_parent ?? 0;

  const accent: Record<string, string> = {
    starting: "#888",
    mirror_games: "#9b59b6",
    claude_prompt: "#2ecc71",
    fitness: "#3498db",
    stack_apply: "#16a085",
    regression: "#e67e22",
    pool_refresh: "#8e44ad",
  };
  const accentColor = accent[phase] ?? "#888";

  let headline: React.ReactNode;
  let subline: React.ReactNode = null;
  let progressNode: React.ReactNode = null;

  if (phase === "mirror_games") {
    headline = (
      <span>
        Seeding Claude advisor —{" "}
        <strong>parent-vs-parent mirror games</strong>
      </span>
    );
    subline = (
      <span>
        <code>{runParent ?? round.candidate ?? "parent"}</code> vs{" "}
        <code>{runParent ?? round.candidate ?? "parent"}</code>
        {" · "}
        Game{" "}
        <code data-testid="current-round-progress">
          {played}/{total}
        </code>
      </span>
    );
    progressNode = <ProgressBar pct={pct} color={accentColor} />;
  } else if (phase === "claude_prompt") {
    headline = (
      <span>
        Claude advisor proposing{" "}
        <strong>{total > 0 ? total : "10"} improvements</strong>…
      </span>
    );
    subline = (
      <span style={{ color: "#888" }}>
        Building prompt from mirror-game outcomes + source tree + guiding
        principles. Typical latency 30–120s.
      </span>
    );
    progressNode = <IndefiniteBar color={accentColor} />;
  } else if (phase === "pool_refresh") {
    headline = (
      <span>
        Pool refresh — {round.imp_title ?? "generating replacement imps"}
      </span>
    );
    subline = (
      <span style={{ color: "#888" }}>
        Generation {round.generation ?? "?"} — Claude is filling the pool back
        to size. Typical latency 30–120s.
      </span>
    );
    progressNode = <IndefiniteBar color={accentColor} />;
  } else if (phase === "starting") {
    headline = (
      <span>
        Preparing generation{" "}
        {round.generation != null ? `#${round.generation}` : ""}…
      </span>
    );
    progressNode = <IndefiniteBar color={accentColor} />;
  } else if (phase === "fitness") {
    const impTitle = round.imp_title ?? "(unknown imp)";
    const candId = round.candidate ?? "?";
    headline = (
      <span>
        Generation{" "}
        {round.generation != null ? `#${round.generation}` : ""} — Fitness
        eval
      </span>
    );
    subline = (
      <MatchupGrid
        leftTitle={impTitle}
        leftId={candId}
        rightTitle="parent baseline"
        rightId={runParent ?? "parent"}
        scoreLeft={scoreCand}
        scoreRight={scoreParent}
        accentColor={accentColor}
      />
    );
    progressNode = (
      <>
        <div
          style={{
            marginTop: "10px",
            fontSize: "0.9em",
            color: "#aaa",
            textAlign: "center",
          }}
        >
          Game{" "}
          <code data-testid="current-round-progress">
            {played}/{total}
          </code>
        </div>
        <ProgressBar pct={pct} color={accentColor} />
      </>
    );
  } else if (phase === "stack_apply") {
    const stacked = round.stacked_titles ?? [];
    headline = (
      <span>
        Generation{" "}
        {round.generation != null ? `#${round.generation}` : ""} — Stack
        apply + import check
      </span>
    );
    subline = (
      <>
        <div
          style={{
            marginTop: "8px",
            fontSize: "0.95em",
            textAlign: "center",
          }}
        >
          Applying <strong>{stacked.length} imps</strong> to a fresh
          snapshot of{" "}
          <code>{runParent ?? "parent"}</code>, then running{" "}
          <code>python -c "import bots.&lt;new&gt;.bot"</code>.
        </div>
        {stacked.length > 0 ? (
          <ul
            data-testid="stack-apply-list"
            style={{
              listStyle: "none",
              padding: 0,
              margin: "10px 0 0",
              fontSize: "0.85em",
              color: "#aaa",
              textAlign: "center",
            }}
          >
            {stacked.map((t, i) => (
              <li key={`${i}-${t}`}>• {t}</li>
            ))}
          </ul>
        ) : null}
      </>
    );
    // Stack-apply doesn't play games — use an indefinite bar.
    progressNode = <IndefiniteBar color={accentColor} />;
  } else if (phase === "regression") {
    headline = (
      <span>
        Generation{" "}
        {round.generation != null ? `#${round.generation}` : ""} —
        Regression check
      </span>
    );
    subline = (
      <MatchupGrid
        leftTitle="new parent"
        leftId={round.new_parent ?? "?"}
        rightTitle="prior parent"
        rightId={round.prior_parent ?? "?"}
        scoreLeft={scoreCand}
        scoreRight={scoreParent}
        accentColor={accentColor}
      />
    );
    progressNode = (
      <>
        <div
          style={{
            marginTop: "10px",
            fontSize: "0.9em",
            color: "#aaa",
            textAlign: "center",
          }}
        >
          Game{" "}
          <code data-testid="current-round-progress">
            {played}/{total}
          </code>
        </div>
        <ProgressBar pct={pct} color={accentColor} />
      </>
    );
  } else {
    headline = <span>Running…</span>;
  }

  return (
    <section
      className="stat-card"
      aria-label="current phase"
      data-testid={
        inGrid ? `worker-card-active-${workerId}` : "current-round-card"
      }
      style={{
        // Drop top/bottom margins inside the grid -- the grid `gap`
        // (16px) is the sole source of row spacing. Outside the grid
        // (legacy single-card N=1 path) keep the original margins.
        marginTop: inGrid ? 0 : "20px",
        marginBottom: inGrid ? 0 : "24px",
        padding: "20px 24px",
        borderLeft: `4px solid ${accentColor}`,
        backgroundColor: "rgba(255,255,255,0.02)",
        borderRadius: "6px",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "12px",
          marginBottom: "12px",
        }}
      >
        {inGrid ? <WorkerBadge workerId={workerId} /> : null}
        <PhaseBadge phase={phase} />
        <h3 style={{ margin: 0, fontSize: "1.15em", fontWeight: 600 }}>
          {headline}
        </h3>
      </div>
      {subline}
      {progressNode}
    </section>
  );
}

// Step 6 of the evolve-parallelization plan: per-worker fan-out grid.
// Plan §347 specifies discrete breakpoints driven by viewport width:
//   <800px       -> 1 column
//   800-1199px   -> 2 columns
//   >=1200px     -> 4 columns
// We use explicit @media queries (not auto-fill) so the column count
// matches the spec exactly at every viewport width -- auto-fill +
// minmax(320px, 1fr) yields 3 columns at ~1199px, which violates
// §347. The CSS lives in a sibling <style> tag scoped via the
// `evolve-running-rounds-grid` class. `align-items: start` keeps
// heterogeneous card heights (active vs idle, fitness vs stack_apply)
// from stretching to the tallest sibling.
//
// Inactive worker slots render as a dim IdleWorkerCard so the grid
// doesn't reflow as workers pick up imps asynchronously.
const WORKER_ROUNDS_GRID_STYLE = `
.evolve-running-rounds-grid {
  display: grid;
  gap: 12px;
  align-items: start;
  margin-top: 20px;
  margin-bottom: 24px;
  grid-template-columns: 1fr;
}
`;

function WorkerRoundsGrid({
  rounds,
  runParent,
  dispatcherPhase,
}: {
  rounds: RunningRound[];
  runParent: string | null;
  dispatcherPhase?: string | null;
}) {
  const activeCount = rounds.filter((r) => r.active).length;
  const totalCount = rounds.length;
  return (
    <>
      <style>{WORKER_ROUNDS_GRID_STYLE}</style>
      <div
        data-testid="worker-grid-header"
        style={{
          marginTop: "16px",
          marginBottom: "8px",
          padding: "8px 12px",
          borderRadius: "4px",
          backgroundColor: "rgba(52, 152, 219, 0.08)",
          border: "1px solid rgba(52, 152, 219, 0.25)",
          color: "#bbb",
          fontSize: "0.9em",
          display: "flex",
          alignItems: "center",
          gap: "10px",
        }}
      >
        <span
          style={{
            color: "#3498db",
            fontWeight: 700,
            fontSize: "1.1em",
            fontFamily: "monospace",
          }}
        >
          ×{totalCount}
        </span>
        <span>
          <strong style={{ color: "#eee" }}>
            {totalCount} workers running in parallel
          </strong>
          {" — "}
          <span data-testid="worker-grid-active-count">
            {activeCount} playing
          </span>
          {" · "}
          {totalCount - activeCount} idle
        </span>
      </div>
      <div
        className="evolve-running-rounds-grid"
        data-testid="worker-rounds-grid"
      >
        {rounds.map((rr) => {
          if (!rr.active) {
            return (
              <IdleWorkerCard
                key={rr.worker_id}
                workerId={rr.worker_id}
                dispatcherPhase={dispatcherPhase}
              />
            );
          }
          const projected = projectRunningRoundToCurrent(rr);
          return (
            <CurrentPhaseCard
              key={rr.worker_id}
              round={projected}
              runParent={rr.parent ?? runParent}
              workerId={rr.worker_id}
            />
          );
        })}
      </div>
    </>
  );
}

function PoolView({ pool }: { pool: EvolvePoolItem[] }) {
  if (pool.length === 0) {
    return (
      <div style={{ color: "#888", fontSize: "0.85em" }}>
        Pool not yet generated
      </div>
    );
  }
  return (
    <ul
      className="evolve-pool"
      style={{ listStyle: "none", padding: 0, margin: 0 }}
    >
      {pool.map((item, i) => (
        <li
          key={`${item.rank}-${item.title}-${i}`}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "12px",
            padding: "6px 0",
            borderBottom: "1px solid rgba(255,255,255,0.05)",
          }}
        >
          <span
            style={{
              fontFamily: "monospace",
              color: "#888",
              minWidth: "32px",
            }}
          >
            #{item.rank}
          </span>
          <span style={{ flex: 1 }}>{item.title}</span>
          {item.fitness_score ? (
            <span
              style={{
                fontSize: "0.8em",
                color: "#aaa",
                minWidth: "40px",
                textAlign: "right",
              }}
            >
              {item.fitness_score[0]}/{item.fitness_score[1]}
            </span>
          ) : null}
          <span
            style={{
              fontSize: "0.75em",
              color: "#666",
              minWidth: "30px",
              textAlign: "right",
            }}
            title={`retries: ${item.retry_count}`}
          >
            r{item.retry_count}
          </span>
          <PoolStatusBadge status={item.status} />
        </li>
      ))}
    </ul>
  );
}

function RoundHistoryTable({ rounds }: { rounds: EvolveRoundResult[] }) {
  if (rounds.length === 0) {
    return (
      <div style={{ color: "#888", fontSize: "0.85em" }}>No phases yet</div>
    );
  }
  return (
    <table style={{ width: "100%", fontSize: "0.85em" }}>
      <thead>
        <tr>
          <th style={{ textAlign: "left" }}>Gen</th>
          <th style={{ textAlign: "left" }}>Phase</th>
          <th style={{ textAlign: "left" }}>Subject</th>
          <th style={{ textAlign: "left" }}>Score</th>
          <th style={{ textAlign: "left" }}>Outcome</th>
          <th style={{ textAlign: "left" }}>Reason</th>
        </tr>
      </thead>
      <tbody>
        {rounds.map((r, i) => {
          const isCrash = r.outcome === "crash";
          let subject = "";
          let scoreText = "";
          if (r.phase === "fitness" && "imp" in r) {
            subject = r.imp?.title ?? "?";
            if ("wins_cand" in r) {
              scoreText = `${r.wins_cand}-${r.wins_parent}`;
            }
          } else if (r.phase === "stack_apply" && "stacked_titles" in r) {
            subject = `stack (${r.stacked_titles.length}) → ${r.new_version || "?"}`;
            scoreText = "";
          } else if (r.phase === "regression" && "new_parent" in r) {
            subject = `${r.new_parent} vs ${r.prior_parent}`;
            scoreText = `${r.wins_new}-${r.wins_prior}`;
          } else {
            subject = "(phase data unavailable)";
          }
          return (
            <tr
              key={`${r.phase}-${r.generation}-${i}`}
              data-testid={isCrash ? "round-history-row-crash" : undefined}
            >
              <td>{r.generation}</td>
              <td>{r.phase}</td>
              <td>{subject}</td>
              <td>{scoreText}</td>
              <td>
                <OutcomeBadge outcome={r.outcome} />
              </td>
              <td style={{ color: "#888" }}>
                {r.reason}
                {r.error ? (
                  <>
                    <br />
                    <code
                      style={{ color: "#e74c3c", fontSize: "0.85em" }}
                      data-testid="round-history-error"
                    >
                      {r.error}
                    </code>
                  </>
                ) : null}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// --- Main component ---

export function EvolutionTab() {
  const {
    state,
    control,
    pool,
    results,
    currentRound,
    runningRounds,
    sendControl,
  } = useEvolveRun();
  const run: EvolveRunState = state.data ?? {
    status: "idle",
    parent_start: null,
    parent_current: null,
    started_at: null,
    wall_budget_hours: null,
    generation_index: null,
    generations_completed: null,
    generations_promoted: null,
    evictions: null,
    resurrections_remaining: null,
    pool_remaining_count: null,
    last_result: null,
  };
  const ctrl = control.data;
  const isRunning = run.status === "running";
  const isCompleted =
    run.status === "completed" || run.status === "failed";

  const [stopOpen, setStopOpen] = useState(false);
  const [message, setMessage] = useState<string>("");

  const showMessage = useCallback((msg: string) => {
    setMessage(msg);
    setTimeout(() => setMessage(""), 3000);
  }, []);

  const handleStopConfirm = useCallback(async () => {
    setStopOpen(false);
    await sendControl({ stop_run: true });
    showMessage(
      "Stop requested \u2014 run will end at the next generation boundary",
    );
  }, [sendControl, showMessage]);

  const handleTogglePause = useCallback(
    async (next: boolean) => {
      await sendControl({ pause_after_round: next });
      showMessage(
        next
          ? "Will pause after the current generation"
          : "Pause-after-generation cleared",
      );
    },
    [sendControl, showMessage],
  );

  return (
    <div className="evolution-tab training-dashboard">
      {state.isStale && run.status !== "idle" ? (
        <StaleDataBanner lastSuccess={state.lastSuccess} label="Evolve State" />
      ) : null}

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "12px",
          marginBottom: "16px",
        }}
      >
        <h2 style={{ margin: 0 }}>Evolution</h2>
        <StatusBadge status={run.status} />
      </div>

      <p style={{ color: "#888", fontSize: "0.85em", margin: "0 0 16px" }}>
        Monitor an active <code>/improve-bot-evolve</code> run. Each generation
        fitness-tests every pool imp vs the current parent, stacks the winners
        into a new snapshot with an import-check gate, and regression-checks
        the promotion against the prior parent.
      </p>

      {run.status === "idle" ? (
        <div className="stat-card" style={{ textAlign: "center", padding: "32px" }}>
          <p style={{ color: "#888", fontSize: "1.1em" }}>
            No evolve run active.
          </p>
          <p style={{ color: "#666", fontSize: "0.9em", marginTop: "8px" }}>
            Launch with{" "}
            <code>uv run python scripts/evolve.py --generations 0 --hours 4 --pool-size 10</code>
          </p>
          <p style={{ color: "#666", fontSize: "0.85em", marginTop: "8px" }}>
            See the <code>/improve-bot-evolve</code> skill for the full
            autonomous loop.
          </p>
        </div>
      ) : (
        <>
          <div
            style={{
              fontSize: "0.9em",
              color: "#aaa",
              marginBottom: "12px",
            }}
          >
            Started{" "}
            <code>{run.started_at ?? "---"}</code>
            {" \u2014 parent "}
            <code>{run.parent_start ?? "---"}</code>
            {" \u2192 current "}
            <code>{run.parent_current ?? "---"}</code>
            {isCompleted && run.stop_reason ? (
              <>
                {" \u2014 Run ended \u2014 "}
                <strong>{run.stop_reason}</strong>
              </>
            ) : isCompleted ? (
              <> {" \u2014 Run ended"}</>
            ) : null}
          </div>

          {isCompleted && run.run_log_path ? (
            <div
              style={{ fontSize: "0.85em", color: "#888", marginBottom: "12px" }}
            >
              Run log: <code>{run.run_log_path}</code>
            </div>
          ) : null}

          {run.last_result ? (
            <section style={{ marginTop: "16px" }}>
              <LastResultCard last={run.last_result} />
            </section>
          ) : null}

          {/*
            Step 6 of the evolve-parallelization plan: per-worker grid
            when a parallel run is active (concurrency>=2). At
            concurrency<=1 we keep the legacy single-card path verbatim
            so the N=1 layout is byte-identical to pre-parallel
            master -- this is the visual-parity promise that pairs
            with Decision D-1 on the engine side. When the
            running-rounds endpoint is inactive (no parallel run, or
            the per-worker files haven't been written yet), we also
            fall back to the legacy current-round path. Decision: see
            documentation/plans/evolve-parallelization-plan.md §7
            Step 6.
          */}
          {runningRounds.data?.active &&
          (runningRounds.data.concurrency ?? 1) >= 2 ? (
            <>
              {currentRound.data?.active &&
              currentRound.data.phase !== "fitness" ? (
                <CurrentPhaseCard
                  round={currentRound.data}
                  runParent={run.parent_current ?? run.parent_start}
                />
              ) : null}
              <WorkerRoundsGrid
                rounds={runningRounds.data.rounds}
                runParent={run.parent_current ?? run.parent_start}
                dispatcherPhase={currentRound.data?.phase ?? null}
              />
            </>
          ) : currentRound.data?.active ? (
            <CurrentPhaseCard
              round={currentRound.data}
              runParent={run.parent_current ?? run.parent_start}
            />
          ) : null}

          <section style={{ marginBottom: "24px" }}>
            <h3>Pool</h3>
            <PoolView pool={pool.data?.pool ?? []} />
          </section>

          <section style={{ marginBottom: "24px" }}>
            <h3>Run Stats</h3>
            <ul
              data-testid="run-stats-list"
              style={{
                listStyle: "none",
                padding: 0,
                margin: 0,
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                gap: "6px 24px",
                fontSize: "0.95em",
                color: "#ddd",
              }}
            >
              <li>
                <strong>Generation:</strong> {run.generation_index ?? 0}
              </li>
              <li>
                <strong>Generations Completed:</strong>{" "}
                {run.generations_completed ?? 0}
              </li>
              <li>
                <strong>Generations Promoted:</strong>{" "}
                {run.generations_promoted ?? 0}
              </li>
              <li>
                <strong>Pool Active:</strong>{" "}
                {run.pool_remaining_count ?? 0}
              </li>
              <li>
                <strong>Evictions:</strong> {run.evictions ?? 0}
              </li>
              <li>
                <strong>Resurrections Left:</strong>{" "}
                {run.resurrections_remaining ?? 0}
              </li>
              <li>
                <strong>Wall Budget:</strong>{" "}
                {run.wall_budget_hours !== null
                  ? `${run.wall_budget_hours}h`
                  : "---"}
              </li>
            </ul>
          </section>

          <section style={{ marginBottom: "24px" }}>
            <h3>Phase History</h3>
            <RoundHistoryTable rounds={results.data?.rounds ?? []} />
          </section>

          <section className="control-panel" aria-labelledby="evolve-actions">
            <h3 id="evolve-actions">Run Actions</h3>
            <div
              className="control-row"
              style={{
                display: "flex",
                gap: "12px",
                alignItems: "center",
                flexWrap: "wrap",
              }}
            >
              <button
                type="button"
                style={{
                  backgroundColor: "#e67e22",
                  color: "#fff",
                  border: "none",
                  padding: "8px 16px",
                  borderRadius: "4px",
                  cursor: isRunning ? "pointer" : "not-allowed",
                  fontWeight: 600,
                  opacity: isRunning ? 1 : 0.5,
                }}
                onClick={() => setStopOpen(true)}
                disabled={!isRunning}
              >
                Stop Run
              </button>
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  fontSize: "0.9em",
                  color: isRunning ? "#eee" : "#666",
                }}
              >
                <input
                  type="checkbox"
                  checked={ctrl?.pause_after_round ?? false}
                  disabled={!isRunning}
                  onChange={(e) => void handleTogglePause(e.target.checked)}
                />
                Pause after current generation
              </label>
            </div>
          </section>

          <ConfirmDialog
            open={stopOpen}
            title="Stop evolve run?"
            message="The run will stop gracefully at the next generation boundary. In-progress games will complete first."
            confirmLabel="Stop"
            onConfirm={() => void handleStopConfirm()}
            onCancel={() => setStopOpen(false)}
          />
        </>
      )}

      {message ? (
        <div
          role="status"
          style={{
            position: "fixed",
            bottom: "24px",
            right: "24px",
            padding: "12px 20px",
            borderRadius: "6px",
            backgroundColor: "#2c3e50",
            color: "#ecf0f1",
            boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
            fontSize: "0.9em",
            zIndex: 1000,
          }}
        >
          {message}
        </div>
      ) : null}
    </div>
  );
}

export default EvolutionTab;
