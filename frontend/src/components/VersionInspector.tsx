import { useState } from "react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  BarChart,
  Bar,
  Cell,
  ScatterChart,
  Scatter,
} from "recharts";
import {
  useVersionDetail,
  type WeightDynamicsRow,
} from "../hooks/useVersionDetail";
import { StaleDataBanner } from "./StaleDataBanner";
import { TimelineList, type TimelineFilter } from "./TimelineList";
import { useVersions } from "../hooks/useVersions";

/**
 * VersionInspector — Step 6 of the Models-tab build plan.
 *
 * Drill-down panel rendered inside the Inspector sub-view of the Models
 * tab. Five collapsible accordion panels (native ``<details>``) each
 * targeting a single per-version data slice:
 *
 *   1. **Config** — hyperparams + reward rules + daemon config (raw JSON).
 *   2. **Training curve** — rolling WR series (10/50/overall) as a line
 *      chart of rolling_overall, rolling_50, rolling_10 vs ts.
 *   3. **Actions** — action-id histogram as a horizontal bar chart.
 *   4. **Improvements applied** — filtered improvement timeline (re-uses
 *      ``TimelineList`` extracted from ``LineageView``).
 *   5. **Weight dynamics** — L2 layer norms over checkpoints. When the
 *      per-checkpoint row carries an ``error``, that checkpoint is
 *      surfaced as a red dot. Pre-Step-9 the response is ``[]`` and
 *      the panel renders a placeholder pointing at
 *      ``scripts/compute_weight_dynamics.py``.
 *
 * "Compare with parent" button at the top — fires
 * ``onCompareWithParent(parentVersionName)`` so the parent
 * (``ModelsTab``) can pre-fill the Compare sub-view's A/B selectors and
 * switch to it. Disabled when the version has no parent.
 *
 * Empty state — when ``version=null`` the panel renders a single
 * placeholder line ("Select a version to inspect"). The Inspector
 * sub-view button can still be navigated to without a selection (e.g.
 * the registry was empty or the user cleared the dropdown).
 */

export interface VersionInspectorProps {
  /** Currently selected version, or ``null`` when nothing is selected. */
  version: string | null;
  /** Fires when the operator clicks "Compare with parent". The parent
   * (``ModelsTab``) pre-fills A=current, B=parent and switches to the
   * Compare sub-view. Step 7 builds the actual Compare view; Step 6
   * just wires the navigation. */
  onCompareWithParent: (parentVersion: string) => void;
}

// --- Helper: pretty-printed JSON pre-block ------------------------------

function JsonBlock({
  label,
  value,
}: {
  label: string;
  value: unknown;
}) {
  // ``JSON.stringify`` emits ``undefined`` for top-level ``undefined``
  // values; coerce so the block always shows something useful.
  const text =
    value === undefined || value === null
      ? "null"
      : JSON.stringify(value, null, 2);
  return (
    <div
      className="version-inspector-json-block"
      data-testid={`inspector-config-${label}`}
      style={{ marginBottom: "12px" }}
    >
      <h4 style={jsonHeaderStyle}>{label}</h4>
      <pre style={preStyle}>
        <code>{text}</code>
      </pre>
    </div>
  );
}

// --- Sub-panel: Config -------------------------------------------------

function ConfigPanel({
  config,
}: {
  config: ReturnType<typeof useVersionDetail>["config"];
}) {
  if (config === null) {
    return (
      <p data-testid="inspector-config-empty" style={emptyStyle}>
        No config data — ``bots/{`{v}`}/data/*.json`` files are empty or absent.
      </p>
    );
  }
  return (
    <div data-testid="inspector-config-body">
      <JsonBlock label="hyperparams" value={config.hyperparams} />
      <JsonBlock label="reward_rules" value={config.reward_rules} />
      <JsonBlock label="daemon_config" value={config.daemon_config} />
    </div>
  );
}

// --- Sub-panel: Training curve -----------------------------------------

interface MergedTrainingPoint {
  game_id: string;
  ts: string;
  rolling_10?: number;
  rolling_50?: number;
  rolling_overall?: number;
}

function mergeTrainingHistory(
  history: ReturnType<typeof useVersionDetail>["trainingHistory"],
): MergedTrainingPoint[] {
  if (!history) return [];
  const byKey = new Map<string, MergedTrainingPoint>();
  const merge = (
    series: { game_id: string; ts: string; wr: number }[] | undefined,
    key: "rolling_10" | "rolling_50" | "rolling_overall",
  ): void => {
    if (!series) return;
    for (const p of series) {
      const k = p.ts || p.game_id;
      const prev = byKey.get(k);
      if (prev) {
        prev[key] = p.wr;
      } else {
        byKey.set(k, {
          game_id: p.game_id,
          ts: p.ts,
          [key]: p.wr,
        });
      }
    }
  };
  merge(history.rolling_overall, "rolling_overall");
  merge(history.rolling_50, "rolling_50");
  merge(history.rolling_10, "rolling_10");
  return Array.from(byKey.values()).sort((a, b) => {
    const at = a.ts || "";
    const bt = b.ts || "";
    if (at === bt) return 0;
    return at < bt ? -1 : 1;
  });
}

function TrainingCurvePanel({
  history,
}: {
  history: ReturnType<typeof useVersionDetail>["trainingHistory"];
}) {
  const merged = mergeTrainingHistory(history);
  if (merged.length === 0) {
    return (
      <p data-testid="inspector-training-empty" style={emptyStyle}>
        No training games recorded for this version yet.
      </p>
    );
  }
  return (
    <div
      data-testid="inspector-training-body"
      style={{ width: "100%", height: 280 }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={merged} margin={{ top: 10, right: 20, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#333" />
          <XAxis dataKey="ts" stroke="#888" tick={{ fontSize: 10 }} />
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
            dataKey="rolling_overall"
            stroke="#3182ce"
            dot={false}
            isAnimationActive={false}
            name="rolling_overall"
          />
          <Line
            type="monotone"
            dataKey="rolling_50"
            stroke="#38a169"
            dot={false}
            isAnimationActive={false}
            name="rolling_50"
          />
          <Line
            type="monotone"
            dataKey="rolling_10"
            stroke="#e53e3e"
            dot={false}
            isAnimationActive={false}
            name="rolling_10"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// --- Sub-panel: Actions ------------------------------------------------

function ActionsPanel({
  actions,
}: {
  actions: ReturnType<typeof useVersionDetail>["actions"];
}) {
  if (!actions || actions.length === 0) {
    return (
      <p data-testid="inspector-actions-empty" style={emptyStyle}>
        No transitions recorded for this version yet.
      </p>
    );
  }
  // Recharts renders horizontal bars when ``layout="vertical"`` and
  // X is numeric, Y is categorical.
  const data = actions.map((a) => ({
    label: `${a.action_id} ${a.name}`,
    count: a.count,
    pct: a.pct,
    action_id: a.action_id,
  }));
  // Tweak height so each bar gets ~24px row.
  const height = Math.max(160, data.length * 24 + 40);
  return (
    <div
      data-testid="inspector-actions-body"
      style={{ width: "100%", height }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          layout="vertical"
          data={data}
          margin={{ top: 8, right: 20, left: 80, bottom: 8 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#333" />
          <XAxis type="number" stroke="#888" tick={{ fontSize: 10 }} />
          <YAxis
            type="category"
            dataKey="label"
            stroke="#888"
            tick={{ fontSize: 10 }}
            width={150}
          />
          <Tooltip
            contentStyle={{ background: "#1a1a1a", border: "1px solid #333" }}
            labelStyle={{ color: "#ccc" }}
          />
          <Bar dataKey="count" fill="#3182ce" isAnimationActive={false}>
            {data.map((entry) => (
              <Cell key={entry.label} fill="#3182ce" />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// --- Sub-panel: Improvements applied -----------------------------------

function ImprovementsPanel({
  improvements,
}: {
  improvements: ReturnType<typeof useVersionDetail>["improvements"];
}) {
  // Local filter + expanded-row state (parent panels lift state for
  // mode-toggle survival; here the panel is mounted only when the
  // ``<details>`` is open, but we still want the operator's filter +
  // expand survival across re-renders within the inspector).
  const [filter, setFilter] = useState<TimelineFilter>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const entries = improvements ?? [];
  return (
    <div data-testid="inspector-improvements-body">
      <TimelineList
        entries={entries}
        filter={filter}
        setFilter={setFilter}
        expandedId={expandedId}
        setExpandedId={setExpandedId}
        title="Improvements applied"
        showFilter={true}
        emptyMessage="No improvements have targeted this version yet."
        testIdPrefix="inspector-timeline"
      />
    </div>
  );
}

// --- Sub-panel: Weight dynamics ----------------------------------------

interface WeightDynamicsChartPoint {
  checkpoint: string;
  ts: string | null;
  error: string | null;
  // dynamic per-layer keys + a constant ``__error_y`` for the error
  // scatter overlay.
  [layer: string]: number | string | null;
}

/**
 * Flatten weight-dynamics rows into a chart-friendly shape.
 *
 * Each row carries either populated ``l2_per_layer`` (success) OR an
 * ``error`` string (failure). On the chart, success rows contribute
 * one numeric value per layer; failure rows contribute a single point
 * to a separate "error" overlay scatter (rendered red).
 */
function flattenWeightDynamics(rows: WeightDynamicsRow[]): {
  data: WeightDynamicsChartPoint[];
  layers: string[];
} {
  const layerSet = new Set<string>();
  for (const r of rows) {
    if (r.l2_per_layer) {
      for (const k of Object.keys(r.l2_per_layer)) layerSet.add(k);
    }
  }
  const layers = Array.from(layerSet).sort();
  const data: WeightDynamicsChartPoint[] = rows.map((r) => {
    const point: WeightDynamicsChartPoint = {
      checkpoint: r.checkpoint,
      ts: r.ts,
      error: r.error,
    };
    if (r.l2_per_layer) {
      for (const k of layers) {
        const v = r.l2_per_layer[k];
        point[k] = typeof v === "number" ? v : null;
      }
    } else {
      for (const k of layers) point[k] = null;
    }
    // Error overlay y-coordinate: pin to 0 so the red dot sits on the
    // X-axis. The hover tooltip carries the actual error text.
    point.__error_y = r.error !== null ? 0 : null;
    return point;
  });
  return { data, layers };
}

const LAYER_PALETTE = [
  "#3182ce",
  "#38a169",
  "#e53e3e",
  "#805ad5",
  "#ed8936",
  "#319795",
  "#d53f8c",
  "#48bb78",
];

function WeightDynamicsPanel({
  weightDynamics,
}: {
  weightDynamics: ReturnType<typeof useVersionDetail>["weightDynamics"];
}) {
  if (!weightDynamics || weightDynamics.length === 0) {
    return (
      <p
        data-testid="inspector-weight-empty"
        style={emptyStyle}
      >
        Pending — run <code>scripts/compute_weight_dynamics.py</code>
      </p>
    );
  }

  const { data, layers } = flattenWeightDynamics(weightDynamics);
  const errorRows = weightDynamics.filter((r) => r.error !== null);

  return (
    <div
      data-testid="inspector-weight-body"
      style={{ width: "100%", height: 280 }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 10, right: 20, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#333" />
          <XAxis dataKey="checkpoint" stroke="#888" tick={{ fontSize: 10 }} />
          <YAxis stroke="#888" tick={{ fontSize: 10 }} />
          <Tooltip
            contentStyle={{ background: "#1a1a1a", border: "1px solid #333" }}
            labelStyle={{ color: "#ccc" }}
          />
          <Legend />
          {layers.map((layer, i) => (
            <Line
              key={layer}
              type="monotone"
              dataKey={layer}
              stroke={LAYER_PALETTE[i % LAYER_PALETTE.length]}
              dot={false}
              isAnimationActive={false}
              connectNulls={true}
              name={layer}
            />
          ))}
          {/* Red-dot scatter overlay for error checkpoints. Recharts
              composes a ``Scatter`` series inside a ``LineChart`` only
              when wrapped in a ``ComposedChart`` — but for our needs the
              simpler approach is a separate ``ScatterChart`` rendered
              below. */}
        </LineChart>
      </ResponsiveContainer>
      {errorRows.length > 0 ? (
        <div
          data-testid="inspector-weight-error-overlay"
          style={{ width: "100%", height: 80, marginTop: 4 }}
        >
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 4, right: 20, left: 0, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#333" />
              <XAxis
                dataKey="checkpoint"
                type="category"
                stroke="#888"
                tick={{ fontSize: 10 }}
                allowDuplicatedCategory={false}
              />
              <YAxis hide />
              <Tooltip
                contentStyle={{ background: "#1a1a1a", border: "1px solid #333" }}
                labelStyle={{ color: "#e53e3e" }}
                formatter={(_v: unknown, _n: unknown, item: { payload?: { error?: string | null } }) => {
                  const err = item?.payload?.error ?? "(error)";
                  return [err, "error"];
                }}
              />
              <Scatter
                name="error"
                data={errorRows.map((r) => ({
                  checkpoint: r.checkpoint,
                  y: 0,
                  error: r.error,
                }))}
                fill="#e53e3e"
                isAnimationActive={false}
                dataKey="y"
              >
                {errorRows.map((r) => (
                  <Cell
                    key={r.checkpoint}
                    fill="#e53e3e"
                    data-testid={`inspector-weight-error-${r.checkpoint}`}
                  />
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      ) : null}
      {/* Plain-text fallback so tests (and screen-readers) can verify
          error rows without depending on SVG hit-testing. Only renders
          when there's at least one error row. */}
      {errorRows.length > 0 ? (
        <ul
          data-testid="inspector-weight-error-list"
          style={{ marginTop: 8, color: "#e53e3e", fontSize: "0.85em" }}
        >
          {errorRows.map((r) => (
            <li
              key={r.checkpoint}
              data-testid={`inspector-weight-error-row-${r.checkpoint}`}
              title={r.error ?? ""}
            >
              <strong>{r.checkpoint}</strong>: {r.error}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// --- Outer accordion ---------------------------------------------------

interface AccordionProps {
  testId: string;
  summary: string;
  children: React.ReactNode;
}

function Accordion({ testId, summary, children }: AccordionProps) {
  return (
    <details
      data-testid={testId}
      style={{
        border: "1px solid #333",
        borderRadius: 4,
        marginBottom: 8,
        padding: "8px 12px",
      }}
    >
      <summary
        data-testid={`${testId}-summary`}
        style={{
          cursor: "pointer",
          fontWeight: 600,
          color: "#ddd",
          userSelect: "none",
        }}
      >
        {summary}
      </summary>
      <div style={{ marginTop: 8 }}>{children}</div>
    </details>
  );
}

// --- Top-level component -----------------------------------------------

export function VersionInspector({
  version,
  onCompareWithParent,
}: VersionInspectorProps) {
  const detail = useVersionDetail(version);
  const { versions } = useVersions();

  if (version === null) {
    return (
      <div
        data-testid="version-inspector-empty"
        style={{ color: "#888", fontStyle: "italic", padding: "16px 0" }}
      >
        Select a version to inspect.
      </div>
    );
  }

  // Look up parent from the registry — the version detail endpoint
  // doesn't carry the lineage edge, but ``/api/versions`` already
  // exposes ``parent`` per row. Fall back to ``null`` when the version
  // isn't found yet (registry still loading).
  const versionRow = versions.find((v) => v.name === version);
  const parent = versionRow?.parent ?? null;

  return (
    <div className="version-inspector" data-testid="version-inspector">
      {detail.isStale ? (
        <StaleDataBanner
          lastSuccess={detail.lastSuccess}
          label={`Inspector (${version})`}
        />
      ) : null}

      <div style={headerStyle}>
        <h3
          data-testid="version-inspector-title"
          style={{ margin: 0, color: "#fff" }}
        >
          {version}
        </h3>
        <button
          type="button"
          data-testid="inspector-compare-with-parent"
          disabled={parent === null}
          onClick={() => {
            if (parent !== null) onCompareWithParent(parent);
          }}
          style={parent === null ? disabledButtonStyle : compareButtonStyle}
          title={
            parent === null
              ? "No parent — this is a genesis version."
              : `Compare ${version} against ${parent}`
          }
        >
          Compare with parent
          {parent !== null ? ` (${parent})` : ""}
        </button>
        <button
          type="button"
          data-testid="version-inspector-refresh"
          onClick={() => detail.refetch()}
          style={refreshButtonStyle}
        >
          Refresh
        </button>
      </div>

      {detail.loading && detail.config === null ? (
        <p data-testid="version-inspector-loading">Loading…</p>
      ) : null}

      <Accordion testId="inspector-accordion-config" summary="Config">
        <ConfigPanel config={detail.config} />
      </Accordion>
      <Accordion
        testId="inspector-accordion-training"
        summary="Training curve"
      >
        <TrainingCurvePanel history={detail.trainingHistory} />
      </Accordion>
      <Accordion testId="inspector-accordion-actions" summary="Actions">
        <ActionsPanel actions={detail.actions} />
      </Accordion>
      <Accordion
        testId="inspector-accordion-improvements"
        summary="Improvements applied"
      >
        <ImprovementsPanel improvements={detail.improvements} />
      </Accordion>
      <Accordion
        testId="inspector-accordion-weight"
        summary="Weight dynamics"
      >
        <WeightDynamicsPanel weightDynamics={detail.weightDynamics} />
      </Accordion>
    </div>
  );
}

// --- Inline styles -----------------------------------------------------

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  marginBottom: 12,
  paddingBottom: 8,
  borderBottom: "1px solid #333",
};

const compareButtonStyle: React.CSSProperties = {
  padding: "6px 12px",
  background: "var(--accent-bg, #1a3a5c)",
  border: "1px solid var(--accent-border, #3182ce)",
  color: "var(--accent, #fff)",
  cursor: "pointer",
  borderRadius: 4,
};

const disabledButtonStyle: React.CSSProperties = {
  ...compareButtonStyle,
  background: "transparent",
  borderColor: "#333",
  color: "#555",
  cursor: "not-allowed",
};

const refreshButtonStyle: React.CSSProperties = {
  marginLeft: "auto",
  padding: "6px 12px",
  background: "transparent",
  border: "1px solid #444",
  color: "#aaa",
  cursor: "pointer",
  borderRadius: 4,
};

const jsonHeaderStyle: React.CSSProperties = {
  margin: "4px 0",
  fontSize: "0.85em",
  color: "#bbb",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
};

const preStyle: React.CSSProperties = {
  background: "#0f0f0f",
  border: "1px solid #2a2a2a",
  borderRadius: 4,
  padding: "8px 12px",
  fontSize: "0.85em",
  color: "#cfcfcf",
  overflow: "auto",
  maxHeight: 240,
};

const emptyStyle: React.CSSProperties = {
  color: "#888",
  fontStyle: "italic",
  margin: "8px 0",
};

export default VersionInspector;
