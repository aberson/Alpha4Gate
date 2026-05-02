import { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ReferenceLine,
} from "recharts";
import { useApi } from "../hooks/useApi";
import { useGameForensics } from "../hooks/useGameForensics";
import { StaleDataBanner } from "./StaleDataBanner";
import type { TrainingHistory } from "../hooks/useVersionDetail";
import { isValidGameId } from "../types/forensics";

/**
 * ForensicsView — Step 8 of the Models-tab build plan.
 *
 * Per-game replay-style insight panel rendered inside the Forensics
 * sub-view of the Models tab. Three sub-sections:
 *
 *   1. Game-id selector — populates from
 *      ``/api/versions/{v}/training-history`` (Step 1b's ``rolling_overall``
 *      array carries the per-game ``{game_id, ts, wr}`` rows we need).
 *      Default selection snaps to ``rolling_overall[length-1]`` (most
 *      recent). Operators can pick an older game from the dropdown.
 *   2. Winprob trajectory — line chart of ``win_prob`` vs ``step`` for
 *      every row in the response trajectory. When ``give_up_fired`` is
 *      true the give-up step is marked as a vertical ``ReferenceLine``
 *      annotated "give-up".
 *   3. Expert-dispatch placeholder — always renders "Phase O pending".
 *      The ``expert_dispatch`` column is null until Phase O (Hydra
 *      meta-controller) writes it.
 *
 * Empty state — when ``version=null`` we render a single placeholder
 * line. When the version has no rolling-overall games yet we render a
 * "no games yet" message in place of the selector. When the selected
 * game's trajectory is empty (e.g. the game id was found but has zero
 * transition rows) the trajectory chart is replaced with a "no
 * transitions yet" message.
 */
export interface ForensicsViewProps {
  /** Currently selected version, or ``null`` when nothing is selected. */
  version: string | null;
}

export function ForensicsView({ version }: ForensicsViewProps) {
  // --- Empty-state short-circuit ---------------------------------------
  if (version === null) {
    return (
      <div
        data-testid="forensics-empty"
        style={{ color: "#888", fontStyle: "italic", padding: "16px 0" }}
      >
        Select a version to inspect game forensics.
      </div>
    );
  }
  return <ForensicsViewActive version={version} />;
}

// Mounted only when ``version`` is non-null so the hooks below never see
// a sentinel value (keeps useApi's effective key stable across renders).
function ForensicsViewActive({ version }: { version: string }) {
  // Training history powers the game-id selector. We only need the
  // ``rolling_overall`` array — the other rolling windows would
  // duplicate game ids. Cache key matches ``useVersionDetail`` so the
  // two sub-views share a hit when the operator toggles between them.
  const historyRes = useApi<TrainingHistory>(
    `/api/versions/${version}/training-history`,
    {
      cacheKey: `/api/versions/${version}/training-history::version-training-history-v1`,
    },
  );

  // Selected game id; ``null`` until the history fetch resolves and we
  // pick the most-recent row. The operator can override via the
  // dropdown. ``null`` also resets every time ``version`` changes (the
  // useEffect below repicks the most-recent game for the new version).
  const [selectedGameId, setSelectedGameId] = useState<string | null>(null);

  const games = useMemo(() => {
    const overall = historyRes.data?.rolling_overall ?? [];
    // Filter out malformed ids defensively — the backend already
    // validates on insert, but defending the dropdown means a corrupt
    // row can't crash the chart.
    return overall.filter((g) => isValidGameId(g.game_id));
  }, [historyRes.data]);

  // Default to the most-recent game once history resolves. We pick
  // ``games[games.length-1]`` because ``rolling_overall`` is appended in
  // chronological order (oldest first). The selector resets to ``null``
  // and re-snaps when ``version`` changes.
  useEffect(() => {
    setSelectedGameId(null);
  }, [version]);
  useEffect(() => {
    if (selectedGameId === null && games.length > 0) {
      setSelectedGameId(games[games.length - 1].game_id);
    }
  }, [selectedGameId, games]);

  const forensicsRes = useGameForensics(version, selectedGameId);

  return (
    <div
      className="forensics-view"
      data-testid="forensics-view"
      style={{ padding: "8px 0" }}
    >
      {historyRes.isStale || forensicsRes.isStale ? (
        <StaleDataBanner
          lastSuccess={
            forensicsRes.lastSuccess ?? historyRes.lastSuccess ?? null
          }
          label={`Forensics (${version})`}
        />
      ) : null}

      <div style={headerStyle}>
        <h3
          data-testid="forensics-title"
          style={{ margin: 0, color: "#fff" }}
        >
          {version}
        </h3>
        <GameIdSelector
          games={games}
          selectedGameId={selectedGameId}
          onChange={setSelectedGameId}
        />
      </div>

      <TrajectorySection
        version={version}
        gameId={selectedGameId}
        forensics={forensicsRes.data}
      />

      <ExpertDispatchPlaceholder />
    </div>
  );
}

// --- Game-id selector ---------------------------------------------------

interface GameIdSelectorProps {
  games: ReadonlyArray<{ game_id: string; ts: string; wr: number }>;
  selectedGameId: string | null;
  onChange: (gameId: string | null) => void;
}

function GameIdSelector({
  games,
  selectedGameId,
  onChange,
}: GameIdSelectorProps) {
  if (games.length === 0) {
    return (
      <div
        data-testid="forensics-no-games"
        style={{ color: "#888", fontStyle: "italic" }}
      >
        No training games recorded for this version yet.
      </div>
    );
  }
  // Render newest first so the dropdown's first option matches the
  // default selection (``games[length-1]``); makes the wired-up
  // selection visually obvious without scrolling.
  const ordered = [...games].reverse();
  return (
    <label style={fieldStyle}>
      <span style={labelStyle}>Game</span>
      <select
        data-testid="forensics-game-select"
        value={selectedGameId ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
      >
        {ordered.map((g) => (
          <option key={g.game_id} value={g.game_id}>
            {g.game_id} — {g.ts}
          </option>
        ))}
      </select>
    </label>
  );
}

// --- Trajectory chart ---------------------------------------------------

interface TrajectorySectionProps {
  version: string;
  gameId: string | null;
  forensics: import("../types/forensics").ForensicsResponse | null;
}

function TrajectorySection({
  version: _version,
  gameId,
  forensics,
}: TrajectorySectionProps) {
  if (gameId === null) {
    return (
      <p data-testid="forensics-trajectory-pending" style={emptyStyle}>
        Select a game to view its win-probability trajectory.
      </p>
    );
  }
  if (forensics === null) {
    return (
      <p data-testid="forensics-trajectory-loading" style={emptyStyle}>
        Loading forensics for <code>{gameId}</code>…
      </p>
    );
  }
  if (forensics.trajectory.length === 0) {
    return (
      <p data-testid="forensics-trajectory-empty" style={emptyStyle}>
        No transitions yet for <code>{gameId}</code>.
      </p>
    );
  }
  // Recharts plays nicest when ``win_prob: null`` rows are kept (rather
  // than filtered out) and ``connectNulls`` joins the gaps, so the X
  // axis still spans the full step range even if the heuristic skipped
  // rows. The give-up reference line is rendered when ``give_up_fired``
  // is true; the y-axis domain is clamped to ``[0, 1]`` to match the
  // win-prob range, and ``ReferenceLine x=`` plots the vertical at the
  // give-up step.
  return (
    <div
      data-testid="forensics-trajectory-body"
      style={{ width: "100%", height: 280 }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={forensics.trajectory}
          margin={{ top: 10, right: 20, left: 0, bottom: 8 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#333" />
          <XAxis
            dataKey="step"
            type="number"
            stroke="#888"
            tick={{ fontSize: 10 }}
            domain={["dataMin", "dataMax"]}
          />
          <YAxis
            domain={[0, 1]}
            stroke="#888"
            tick={{ fontSize: 10 }}
            tickFormatter={(v: number) => v.toFixed(1)}
          />
          <Tooltip
            contentStyle={{ background: "#1a1a1a", border: "1px solid #333" }}
            labelStyle={{ color: "#ccc" }}
            formatter={(v: unknown) =>
              typeof v === "number" ? v.toFixed(3) : String(v ?? "")
            }
          />
          <Legend />
          <Line
            type="monotone"
            dataKey="win_prob"
            stroke="#3182ce"
            dot={false}
            isAnimationActive={false}
            connectNulls={true}
            name="win_prob"
          />
          {forensics.give_up_fired && forensics.give_up_step !== null ? (
            <ReferenceLine
              x={forensics.give_up_step}
              stroke="#e53e3e"
              strokeDasharray="4 2"
              label={{
                value: "give-up",
                position: "top",
                fill: "#e53e3e",
                fontSize: 11,
              }}
              data-testid="forensics-give-up-line"
            />
          ) : null}
        </LineChart>
      </ResponsiveContainer>
      {/* Plain-text mirror of the give-up annotation so the contract is
          provable without depending on Recharts' SVG layout. The
          dashed-line overlay above is the visual; the badge below is the
          accessible / test-friendly fallback. */}
      {forensics.give_up_fired && forensics.give_up_step !== null ? (
        <div
          data-testid="forensics-give-up-badge"
          style={{
            color: "#e53e3e",
            fontSize: "0.85em",
            marginTop: 4,
            fontWeight: 600,
          }}
        >
          Give-up triggered at step {forensics.give_up_step}.
        </div>
      ) : null}
    </div>
  );
}

// --- Expert-dispatch placeholder ---------------------------------------

function ExpertDispatchPlaceholder() {
  return (
    <div
      data-testid="forensics-expert-dispatch"
      style={dispatchCardStyle}
    >
      <h4 style={{ margin: 0, color: "#bbb", fontSize: "0.9em" }}>
        Expert dispatch
      </h4>
      <p style={{ margin: "4px 0 0 0", color: "#888", fontStyle: "italic" }}>
        Phase O pending — the Hydra meta-controller writes per-expert
        dispatch counts here once the scripted v1 ships.
      </p>
    </div>
  );
}

// --- Inline styles ------------------------------------------------------

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 16,
  marginBottom: 12,
  paddingBottom: 8,
  borderBottom: "1px solid #333",
  flexWrap: "wrap",
};

const fieldStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const labelStyle: React.CSSProperties = {
  fontSize: "0.8em",
  color: "#888",
};

const emptyStyle: React.CSSProperties = {
  color: "#888",
  fontStyle: "italic",
  margin: "8px 0",
};

const dispatchCardStyle: React.CSSProperties = {
  border: "1px solid #333",
  borderRadius: 4,
  padding: "10px 12px",
  marginTop: 12,
  background: "#0f0f0f",
};

export default ForensicsView;
