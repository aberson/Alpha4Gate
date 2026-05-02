import { useEffect, useMemo, useState } from "react";
import { useVersions } from "../hooks/useVersions";
import { StaleDataBanner } from "./StaleDataBanner";

/**
 * Observable tab — Step 10 of the Models-tab build plan.
 *
 * This is the wired-up SHELL for Phase L's exhibition mode. It lives as a
 * sibling to the Models tab to enforce the dashboard's organizing principle:
 * **Utility** (Models — training, lineage, forensics) vs **Observable**
 * (this tab — exhibition replay-stream-as-live, decoupled from rated
 * play). See ``project_two_stack_split.md`` in MEMORY.md.
 *
 * Today this tab shows:
 *   - Two version dropdowns (left vs right) populated from
 *     ``/api/versions``. The selection state is local — Phase L will use
 *     these picks to drive its replay-stream renderer.
 *   - A placeholder card framing what Phase L will deliver, with a wiki
 *     link for operators who land here before Phase L ships.
 *   - StaleDataBanner per dashboard convention when the registry is
 *     unreachable.
 *
 * No exhibition controls yet — those land with Phase L. Step 10 is shell +
 * docs only; no backend changes were added.
 */

const WIKI_LINK_HREF =
  "https://github.com/aberobison/Alpha4Gate/blob/master/documentation/wiki/models-tab.md";

export function ObservableTab() {
  const { versions, isStale, lastSuccess } = useVersions();

  // Two-version pool selector. The default snaps the LEFT slot to the
  // ``current`` version once the registry resolves, and the RIGHT slot
  // to ``current.parent`` (mirrors the Models-tab Compare default).
  // Both stay ``null`` until the first non-empty fetch.
  const [leftVersion, setLeftVersion] = useState<string | null>(null);
  const [rightVersion, setRightVersion] = useState<string | null>(null);

  const currentVersionName = useMemo<string | null>(() => {
    const cur = versions.find((v) => v.current);
    return cur?.name ?? versions[0]?.name ?? null;
  }, [versions]);

  const currentParentName = useMemo<string | null>(() => {
    const cur = versions.find((v) => v.current);
    return cur?.parent ?? null;
  }, [versions]);

  useEffect(() => {
    if (leftVersion === null && currentVersionName !== null) {
      setLeftVersion(currentVersionName);
    }
  }, [leftVersion, currentVersionName]);

  useEffect(() => {
    if (rightVersion === null && currentParentName !== null) {
      setRightVersion(currentParentName);
    }
  }, [rightVersion, currentParentName]);

  return (
    <div className="observable-tab" data-testid="observable-tab">
      {isStale ? (
        <StaleDataBanner lastSuccess={lastSuccess} label="Observable" />
      ) : null}

      <div className="observable-header" style={headerStyle}>
        <label style={fieldStyle}>
          <span style={labelStyle}>Left</span>
          <select
            data-testid="observable-version-select-left"
            value={leftVersion ?? ""}
            onChange={(e) => setLeftVersion(e.target.value || null)}
          >
            {versions.length === 0 ? (
              <option value="">(no versions)</option>
            ) : null}
            {versions.map((v) => (
              <option key={v.name} value={v.name}>
                {v.name}
                {v.current ? " (current)" : ""}
              </option>
            ))}
          </select>
        </label>

        <label style={fieldStyle}>
          <span style={labelStyle}>Right</span>
          <select
            data-testid="observable-version-select-right"
            value={rightVersion ?? ""}
            onChange={(e) => setRightVersion(e.target.value || null)}
          >
            {versions.length === 0 ? (
              <option value="">(no versions)</option>
            ) : null}
            {versions.map((v) => (
              <option key={v.name} value={v.name}>
                {v.name}
                {v.current ? " (current)" : ""}
              </option>
            ))}
          </select>
        </label>
      </div>

      <section
        data-testid="observable-phase-l-placeholder"
        style={placeholderCardStyle}
      >
        <h2 style={{ marginTop: 0 }}>Exhibition mode awaits Phase L</h2>
        <p style={{ marginBottom: "8px" }}>
          Exhibition mode awaits Phase L (replay-stream-as-live). This tab is
          the Observable half of the Utility / Observable split: rated play
          (Models tab) and exhibition (here) live on separate substrates so
          perception-affecting rendering (e.g.{" "}
          <code>disable_fog=True</code>) never bleeds into training.
        </p>
        <p style={{ marginBottom: "8px" }}>
          Until Phase L ships, the version pickers above are a no-op preview —
          they record which two versions an operator wants to watch but no
          replay stream is wired up yet.
        </p>
        <p style={{ marginBottom: 0 }}>
          See the{" "}
          <a
            data-testid="observable-wiki-link"
            href={WIKI_LINK_HREF}
            target="_blank"
            rel="noopener noreferrer"
          >
            Models tab wiki page
          </a>{" "}
          for the full Phase L / Phase O / Phase G context and operator
          recovery procedures.
        </p>
      </section>

      {versions.length === 0 ? (
        <p data-testid="observable-empty-state" style={emptyStateStyle}>
          No versions in the registry yet. Promote a version with{" "}
          <code>scripts/snapshot_bot.py</code> or run{" "}
          <code>/improve-bot-advised</code> /{" "}
          <code>/improve-bot-evolve</code> to populate the pool.
        </p>
      ) : null}
    </div>
  );
}

// Inline styles — matches the rest of the dashboard, which keeps small
// per-component visual tweaks close to the JSX rather than threading them
// through App.css.
const headerStyle: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "flex-end",
  gap: "16px",
  padding: "12px 0",
  borderBottom: "1px solid #333",
  marginBottom: "12px",
};

const fieldStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "4px",
};

const labelStyle: React.CSSProperties = {
  fontSize: "0.8em",
  color: "#888",
};

const placeholderCardStyle: React.CSSProperties = {
  background: "rgba(49, 130, 206, 0.08)",
  borderLeft: "3px solid #3182ce",
  padding: "16px",
  borderRadius: "4px",
  lineHeight: 1.5,
};

const emptyStateStyle: React.CSSProperties = {
  marginTop: "12px",
  color: "#888",
  fontStyle: "italic",
};

export default ObservableTab;
