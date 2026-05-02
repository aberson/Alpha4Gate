import { useMemo } from "react";
import { useApi } from "../hooks/useApi";
import { useVersions } from "../hooks/useVersions";
import { useVersionDetail } from "../hooks/useVersionDetail";
import { StaleDataBanner } from "./StaleDataBanner";
import { deepDiff, type DiffResult } from "../utils/deepDiff";

/**
 * CompareView — Step 7 of the Models-tab build plan.
 *
 * A vs B side-by-side version comparison with four diff panels:
 *
 *   1. **Elo delta** — single line. Reads ``/api/ladder``; pulls the
 *      ``elo`` field from each version's standings row. Renders
 *      ``Elo: vA <ratingA> vs vB <ratingB> (Δ +/- N)``. If either
 *      version is absent from the ladder we surface a "no rating"
 *      placeholder for that side.
 *
 *   2. **Hyperparams diff** — deep-diff renderer. Pulls ``hyperparams``
 *      out of the per-version ``/api/versions/{v}/config`` response
 *      (Step 1a) for both A and B and runs ``deepDiff``. Added keys
 *      render in green, removed in red, modified as a side-by-side
 *      ``a → b`` pair. Unchanged keys are rolled up into a count to
 *      keep the panel scannable.
 *
 *   3. **Reward rules diff** — same renderer as panel 2, scoped to the
 *      ``reward_rules`` sub-object. Each rule is keyed by id with
 *      values like ``{enabled, weight, condition}`` — diff descends
 *      into per-field changes for shared rules, but a wholly added or
 *      removed rule reports as a single subtree entry (per the
 *      ``deepDiff`` semantics — see ``utils/deepDiff.ts``).
 *
 *   4. **Weight KL divergence** — single number. Reads each version's
 *      ``/api/versions/{v}/weight-dynamics`` response (Step 1c). The
 *      KL is stored on the CHILD's row as ``kl_from_parent`` (parent
 *      → child relationship from the version registry's ``parent``
 *      field). Sibling comparisons show "no direct lineage". When the
 *      child's rows are empty (Step 9 hasn't shipped data yet) we
 *      show a "Pending — run scripts/compute_weight_dynamics.py"
 *      placeholder.
 *
 * Initial A/B values come from the parent ``ModelsTab``: the
 * Inspector's "Compare with parent" button (Step 6) sets
 * ``compareA=current, compareB=parent`` and switches to the Compare
 * sub-view. When the operator navigates here directly with both still
 * ``null``, the parent defaults A=current version and B=current.parent
 * (or B=null if no parent). The two top-of-panel selects let the
 * operator pick any A/B pair from the registry.
 */

export interface CompareViewProps {
  /** Currently selected version A. ``null`` when no selection yet. */
  compareA: string | null;
  /** Currently selected version B. ``null`` when no selection yet. */
  compareB: string | null;
  /** Fires whenever the operator changes either side via the dropdowns. */
  onChange: (a: string | null, b: string | null) => void;
}

interface LadderStandingRow {
  version: string;
  elo?: number;
  games?: number;
  wins?: number;
  losses?: number;
}

interface LadderResponse {
  standings: LadderStandingRow[];
  head_to_head: Record<string, unknown>;
}

// --- Diff renderer (shared by Hyperparams + Reward rules panels) -------

interface DiffRendererProps {
  diff: DiffResult;
  /** Used in test ids and for the unchanged-count caption. */
  testIdPrefix: string;
}

/**
 * Pretty-print any diff value for the renderer rows. Strings, numbers,
 * booleans, and ``null`` use their natural toString (with ``null``
 * spelled "null"); objects and arrays use ``JSON.stringify`` so the
 * renderer can fit them into a single highlighted row.
 */
function renderValue(v: unknown): string {
  if (v === null) return "null";
  if (v === undefined) return "undefined";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function DiffRenderer({ diff, testIdPrefix }: DiffRendererProps) {
  const addedKeys = Object.keys(diff.added).sort();
  const removedKeys = Object.keys(diff.removed).sort();
  const modifiedKeys = Object.keys(diff.modified).sort();
  const unchangedCount = Object.keys(diff.unchanged).length;
  const total =
    addedKeys.length + removedKeys.length + modifiedKeys.length;

  if (total === 0) {
    return (
      <p
        data-testid={`${testIdPrefix}-empty`}
        style={{ color: "#888", fontStyle: "italic" }}
      >
        No differences ({unchangedCount} unchanged keys).
      </p>
    );
  }

  return (
    <div data-testid={`${testIdPrefix}-body`}>
      {addedKeys.length > 0 ? (
        <ul
          data-testid={`${testIdPrefix}-added`}
          style={listStyle}
          aria-label="Added keys"
        >
          {addedKeys.map((k) => (
            <li
              key={k}
              data-testid={`${testIdPrefix}-added-${k}`}
              style={addedRowStyle}
            >
              <span style={pathStyle}>+ {k}</span>
              <span style={valueStyle}>{renderValue(diff.added[k])}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {removedKeys.length > 0 ? (
        <ul
          data-testid={`${testIdPrefix}-removed`}
          style={listStyle}
          aria-label="Removed keys"
        >
          {removedKeys.map((k) => (
            <li
              key={k}
              data-testid={`${testIdPrefix}-removed-${k}`}
              style={removedRowStyle}
            >
              <span style={pathStyle}>- {k}</span>
              <span style={valueStyle}>{renderValue(diff.removed[k])}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {modifiedKeys.length > 0 ? (
        <ul
          data-testid={`${testIdPrefix}-modified`}
          style={listStyle}
          aria-label="Modified keys"
        >
          {modifiedKeys.map((k) => {
            const m = diff.modified[k];
            return (
              <li
                key={k}
                data-testid={`${testIdPrefix}-modified-${k}`}
                style={modifiedRowStyle}
              >
                <span style={pathStyle}>~ {k}</span>
                <span style={modifiedValueStyle}>
                  <span style={modifiedAStyle}>{renderValue(m.a)}</span>
                  <span style={arrowStyle}> → </span>
                  <span style={modifiedBStyle}>{renderValue(m.b)}</span>
                </span>
              </li>
            );
          })}
        </ul>
      ) : null}

      <p
        data-testid={`${testIdPrefix}-summary`}
        style={{ color: "#888", fontSize: "0.8em", marginTop: 4 }}
      >
        {addedKeys.length} added, {removedKeys.length} removed,{" "}
        {modifiedKeys.length} modified, {unchangedCount} unchanged.
      </p>
    </div>
  );
}

// --- Top-level CompareView ---------------------------------------------

export function CompareView({
  compareA,
  compareB,
  onChange,
}: CompareViewProps) {
  const { versions } = useVersions();

  // Per-version detail for both sides. Each call short-circuits to a
  // ``NULL_RESULT`` when its arg is null, so passing ``null`` is safe.
  const detailA = useVersionDetail(compareA);
  const detailB = useVersionDetail(compareB);

  // Cross-version Elo standings.
  const ladderRes = useApi<LadderResponse>("/api/ladder", {
    cacheKey: "/api/ladder::ladder-v1",
  });

  // Header staleness is the OR of the three feeds (ladder + both
  // version-detail aggregators). Banner picks the most recent
  // ``lastSuccess`` so the relative-time string isn't wildly off when
  // one feed is much fresher than the other.
  const isStale =
    ladderRes.isStale || detailA.isStale || detailB.isStale;
  const lastSuccess = useMemo<Date | null>(() => {
    const cands = [
      ladderRes.lastSuccess,
      detailA.lastSuccess,
      detailB.lastSuccess,
    ].filter((d): d is Date => d !== null);
    if (cands.length === 0) return null;
    return cands.reduce(
      (acc, cur) => (cur.getTime() > acc.getTime() ? cur : acc),
      cands[0],
    );
  }, [ladderRes.lastSuccess, detailA.lastSuccess, detailB.lastSuccess]);

  // Empty state — no A and no B. Show selectors only.
  const bothEmpty = compareA === null && compareB === null;

  // Resolve parent→child relationship for the KL panel. The version
  // registry carries each row's ``parent`` field, so we look up both
  // sides and check both directions (A is parent of B, OR B is parent
  // of A). Sibling pairs return null which surfaces the "no direct
  // lineage" placeholder.
  const lineage = useMemo<{
    childVersion: string | null;
    childRows: typeof detailA.weightDynamics;
  }>(() => {
    if (compareA === null || compareB === null) {
      return { childVersion: null, childRows: null };
    }
    const rowA = versions.find((v) => v.name === compareA);
    const rowB = versions.find((v) => v.name === compareB);
    // A is the parent of B → child is B → use detailB's rows.
    if (rowB && rowB.parent === compareA) {
      return { childVersion: compareB, childRows: detailB.weightDynamics };
    }
    // B is the parent of A → child is A → use detailA's rows.
    if (rowA && rowA.parent === compareB) {
      return { childVersion: compareA, childRows: detailA.weightDynamics };
    }
    return { childVersion: null, childRows: null };
  }, [compareA, compareB, versions, detailA.weightDynamics, detailB.weightDynamics]);

  // --- Elo panel computation -------------------------------------------
  const eloA = useMemo<number | null>(() => {
    if (compareA === null) return null;
    const row = ladderRes.data?.standings.find((s) => s.version === compareA);
    return typeof row?.elo === "number" ? row.elo : null;
  }, [compareA, ladderRes.data]);
  const eloB = useMemo<number | null>(() => {
    if (compareB === null) return null;
    const row = ladderRes.data?.standings.find((s) => s.version === compareB);
    return typeof row?.elo === "number" ? row.elo : null;
  }, [compareB, ladderRes.data]);
  const eloDelta = eloA !== null && eloB !== null ? eloA - eloB : null;

  // --- Hyperparams + Reward rules diffs --------------------------------
  // Pull the sub-objects out of the ``/api/versions/{v}/config``
  // response. ``deepDiff`` handles non-object inputs gracefully (one
  // side null collapses to add/remove at empty-key) but for cleaner
  // panel output we coerce missing configs to ``{}``.
  const configA = detailA.config;
  const configB = detailB.config;
  const hyperparamsDiff = useMemo<DiffResult>(
    () =>
      deepDiff(
        (configA?.hyperparams as Record<string, unknown> | undefined) ?? {},
        (configB?.hyperparams as Record<string, unknown> | undefined) ?? {},
      ),
    [configA, configB],
  );
  const rewardRulesDiff = useMemo<DiffResult>(
    () =>
      deepDiff(
        (configA?.reward_rules as Record<string, unknown> | undefined) ?? {},
        (configB?.reward_rules as Record<string, unknown> | undefined) ?? {},
      ),
    [configA, configB],
  );

  // --- Render -----------------------------------------------------------

  const handleAChange = (e: React.ChangeEvent<HTMLSelectElement>): void => {
    const next = e.target.value || null;
    onChange(next, compareB);
  };
  const handleBChange = (e: React.ChangeEvent<HTMLSelectElement>): void => {
    const next = e.target.value || null;
    onChange(compareA, next);
  };

  return (
    <div className="compare-view" data-testid="compare-view">
      {isStale ? (
        <StaleDataBanner lastSuccess={lastSuccess} label="Compare" />
      ) : null}

      <div style={selectorRowStyle} data-testid="compare-selector-row">
        <label style={fieldStyle}>
          <span style={labelStyle}>A</span>
          <select
            data-testid="compare-select-a"
            value={compareA ?? ""}
            onChange={handleAChange}
          >
            <option value="">(none)</option>
            {versions.map((v) => (
              <option key={v.name} value={v.name}>
                {v.name}
                {v.current ? " (current)" : ""}
              </option>
            ))}
          </select>
        </label>
        <label style={fieldStyle}>
          <span style={labelStyle}>B</span>
          <select
            data-testid="compare-select-b"
            value={compareB ?? ""}
            onChange={handleBChange}
          >
            <option value="">(none)</option>
            {versions.map((v) => (
              <option key={v.name} value={v.name}>
                {v.name}
                {v.current ? " (current)" : ""}
              </option>
            ))}
          </select>
        </label>
      </div>

      {bothEmpty ? (
        <p
          data-testid="compare-empty"
          style={{ color: "#888", fontStyle: "italic", padding: "16px 0" }}
        >
          Select two versions to compare.
        </p>
      ) : (
        <div data-testid="compare-panels">
          {/* Panel 1 — Elo delta ----------------------------------- */}
          <section
            data-testid="compare-panel-elo"
            style={panelStyle}
          >
            <h4 style={panelHeaderStyle}>Elo delta</h4>
            <p data-testid="compare-elo-line">
              Elo: {compareA ?? "?"}{" "}
              <strong data-testid="compare-elo-a">
                {eloA !== null ? eloA.toFixed(0) : "—"}
              </strong>{" "}
              vs {compareB ?? "?"}{" "}
              <strong data-testid="compare-elo-b">
                {eloB !== null ? eloB.toFixed(0) : "—"}
              </strong>{" "}
              {eloDelta !== null ? (
                <span data-testid="compare-elo-delta">
                  (Δ {eloDelta >= 0 ? "+" : ""}
                  {eloDelta.toFixed(0)})
                </span>
              ) : (
                <span
                  data-testid="compare-elo-delta-missing"
                  style={{ color: "#888" }}
                >
                  (no rating)
                </span>
              )}
            </p>
          </section>

          {/* Panel 2 — Hyperparams diff ---------------------------- */}
          <section
            data-testid="compare-panel-hyperparams"
            style={panelStyle}
          >
            <h4 style={panelHeaderStyle}>Hyperparams diff</h4>
            {configA === null || configB === null ? (
              <p
                data-testid="compare-hyperparams-pending"
                style={{ color: "#888", fontStyle: "italic" }}
              >
                Loading config…
              </p>
            ) : (
              <DiffRenderer
                diff={hyperparamsDiff}
                testIdPrefix="compare-hyperparams"
              />
            )}
          </section>

          {/* Panel 3 — Reward rules diff --------------------------- */}
          <section
            data-testid="compare-panel-reward-rules"
            style={panelStyle}
          >
            <h4 style={panelHeaderStyle}>Reward rules diff</h4>
            {configA === null || configB === null ? (
              <p
                data-testid="compare-reward-rules-pending"
                style={{ color: "#888", fontStyle: "italic" }}
              >
                Loading config…
              </p>
            ) : (
              <DiffRenderer
                diff={rewardRulesDiff}
                testIdPrefix="compare-reward-rules"
              />
            )}
          </section>

          {/* Panel 4 — Weight KL divergence ------------------------ */}
          <section
            data-testid="compare-panel-kl"
            style={panelStyle}
          >
            <h4 style={panelHeaderStyle}>Weight KL divergence</h4>
            <KLPanel
              compareA={compareA}
              compareB={compareB}
              childVersion={lineage.childVersion}
              childRows={lineage.childRows}
            />
          </section>
        </div>
      )}
    </div>
  );
}

interface KLPanelProps {
  compareA: string | null;
  compareB: string | null;
  childVersion: string | null;
  childRows: ReturnType<typeof useVersionDetail>["weightDynamics"];
}

function KLPanel({
  compareA,
  compareB,
  childVersion,
  childRows,
}: KLPanelProps) {
  if (compareA === null || compareB === null) {
    return (
      <p
        data-testid="compare-kl-empty"
        style={{ color: "#888", fontStyle: "italic" }}
      >
        Select both A and B to compare.
      </p>
    );
  }
  if (childVersion === null) {
    return (
      <p
        data-testid="compare-kl-no-lineage"
        style={{ color: "#888", fontStyle: "italic" }}
      >
        No direct lineage between {compareA} and {compareB} — KL divergence
        requires parent→child.
      </p>
    );
  }
  // ``childRows === null`` means the per-version detail hook returned
  // ``null`` (request errored OR ``version=null``). With a non-null
  // ``childVersion`` we expect rows; the loading window from useApi
  // will resolve to ``[]`` (Step-9-pending) or a populated array.
  if (childRows === null) {
    return (
      <p
        data-testid="compare-kl-loading"
        style={{ color: "#888", fontStyle: "italic" }}
      >
        Loading weight dynamics…
      </p>
    );
  }
  // Find the most recent row that carries a non-null ``kl_from_parent``.
  // Step-9 will populate this; until then the rows are ``[]`` and we
  // surface the placeholder.
  const klRow = childRows.find((r) => r.kl_from_parent !== null);
  if (childRows.length === 0 || klRow === undefined) {
    return (
      <p
        data-testid="compare-kl-pending"
        style={{ color: "#888", fontStyle: "italic" }}
      >
        Pending — run <code>scripts/compute_weight_dynamics.py</code>
      </p>
    );
  }
  // ``kl_from_parent`` is non-null per the find predicate above; the
  // ``?? 0`` fallback is type-narrowing only.
  const kl = klRow.kl_from_parent ?? 0;
  return (
    <p data-testid="compare-kl-value">
      KL({childVersion} ← parent) ={" "}
      <strong data-testid="compare-kl-number">{kl.toFixed(4)}</strong>{" "}
      <span style={{ color: "#888", fontSize: "0.85em" }}>
        (checkpoint: {klRow.checkpoint})
      </span>
    </p>
  );
}

// --- Inline styles -----------------------------------------------------

const selectorRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 16,
  alignItems: "flex-end",
  paddingBottom: 12,
  borderBottom: "1px solid #333",
  marginBottom: 12,
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

const panelStyle: React.CSSProperties = {
  border: "1px solid #333",
  borderRadius: 4,
  padding: "12px 16px",
  marginBottom: 12,
  background: "#0f0f0f",
};

const panelHeaderStyle: React.CSSProperties = {
  margin: "0 0 8px 0",
  fontSize: "0.95em",
  color: "#ddd",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
};

const listStyle: React.CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
};

const baseRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 12,
  padding: "2px 6px",
  fontFamily: "monospace",
  fontSize: "0.85em",
  borderRadius: 2,
};

const addedRowStyle: React.CSSProperties = {
  ...baseRowStyle,
  background: "rgba(56, 161, 105, 0.12)",
  color: "#9ae6b4",
};

const removedRowStyle: React.CSSProperties = {
  ...baseRowStyle,
  background: "rgba(229, 62, 62, 0.12)",
  color: "#feb2b2",
};

const modifiedRowStyle: React.CSSProperties = {
  ...baseRowStyle,
  background: "rgba(49, 130, 206, 0.10)",
  color: "#cfcfcf",
};

const pathStyle: React.CSSProperties = {
  flex: "0 0 auto",
  minWidth: 200,
  fontWeight: 600,
};

const valueStyle: React.CSSProperties = {
  flex: "1 1 auto",
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
};

const modifiedValueStyle: React.CSSProperties = {
  ...valueStyle,
  display: "flex",
  gap: 4,
  alignItems: "center",
  flexWrap: "wrap",
};

const modifiedAStyle: React.CSSProperties = {
  color: "#feb2b2",
  textDecoration: "line-through",
  textDecorationColor: "#e53e3e",
};

const modifiedBStyle: React.CSSProperties = {
  color: "#9ae6b4",
};

const arrowStyle: React.CSSProperties = {
  color: "#888",
};

export default CompareView;
