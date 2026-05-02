import { useCallback, useEffect, useMemo, useState } from "react";
import { useVersions } from "../hooks/useVersions";
import { StaleDataBanner } from "./StaleDataBanner";
import { HARNESS_ORIGINS } from "../types/version";
import type { Version } from "../types/version";

/**
 * Models tab SHELL — Step 3 of the Models-tab build plan.
 *
 * This file delivers the FRAME only:
 *   - Header strip: version dropdown, race filter (auto-hidden when single),
 *     harness chips, manual refresh.
 *   - Sub-view router: 5 buttons that switch among placeholder panels.
 *
 * Subsequent steps replace each placeholder with real content:
 *   Step 4: Lineage tree (subsumes the Improvements tab)
 *   Step 5: Live Runs grid
 *   Step 6: Inspector
 *   Step 7: Compare
 *   Step 8: Forensics
 *
 * The ``onNodeSelect`` callback is wired here so Step 4's tree can drop
 * in without restructuring the parent's state. For Step 3 a placeholder
 * "Simulate v3 select (test)" button inside the Lineage placeholder
 * exercises the callback end-to-end (selected version + sub-view switch
 * to Inspector) — the same code path the real tree will use.
 */

export type SubView = "lineage" | "live" | "inspector" | "compare" | "forensics";

const SUB_VIEWS: ReadonlyArray<{ id: SubView; label: string }> = [
  { id: "lineage", label: "Lineage" },
  { id: "live", label: "Live Runs" },
  { id: "inspector", label: "Inspector" },
  { id: "compare", label: "Compare" },
  { id: "forensics", label: "Forensics" },
] as const;

/**
 * Coerce a ``race`` field to its canonical string. ``null`` and ``""``
 * both map to ``"protoss"`` (the historical default — every manifest
 * before Phase G omits the field). The race-filter visibility check
 * uses this so a fixture mixing ``race: null`` rows with ``race:
 * "protoss"`` rows still resolves to a single race and stays hidden.
 */
function coerceRace(raw: string | null | undefined): string {
  if (raw === null || raw === undefined || raw === "") return "protoss";
  return raw;
}

interface LineagePlaceholderProps {
  versions: Version[];
  onNodeSelect: (versionName: string) => void;
}

function LineagePlaceholder({ versions, onNodeSelect }: LineagePlaceholderProps) {
  // Pick a stable test target: prefer "v3" if present, else the first
  // version, else ``null`` (button disabled when there is nothing to
  // simulate against).
  const target = useMemo<string | null>(() => {
    const v3 = versions.find((v) => v.name === "v3");
    if (v3) return v3.name;
    if (versions.length > 0) return versions[0].name;
    return null;
  }, [versions]);

  return (
    <div data-testid="models-subview-lineage" className="models-subview">
      <p>Lineage view (Step 4)</p>
      <button
        type="button"
        data-testid="models-lineage-simulate-select"
        disabled={target === null}
        onClick={() => {
          if (target !== null) onNodeSelect(target);
        }}
      >
        {target === null
          ? "Simulate v3 select (test)"
          : `Simulate ${target} select (test)`}
      </button>
    </div>
  );
}

function LiveRunsPlaceholder() {
  return (
    <div data-testid="models-subview-live" className="models-subview">
      <p>Live Runs grid (Step 5)</p>
    </div>
  );
}

function InspectorPlaceholder({ selectedVersion }: { selectedVersion: string | null }) {
  return (
    <div data-testid="models-subview-inspector" className="models-subview">
      <p>Inspector (Step 6)</p>
      <p data-testid="models-inspector-selected">
        Selected: {selectedVersion ?? "(none)"}
      </p>
    </div>
  );
}

function ComparePlaceholder() {
  return (
    <div data-testid="models-subview-compare" className="models-subview">
      <p>Compare (Step 7)</p>
    </div>
  );
}

function ForensicsPlaceholder() {
  return (
    <div data-testid="models-subview-forensics" className="models-subview">
      <p>Forensics (Step 8)</p>
    </div>
  );
}

export function ModelsTab() {
  const { versions, isStale, lastSuccess, refetch } = useVersions();

  // Single-select dropdown — defaults to whichever version is flagged
  // ``current: true`` once the registry resolves. ``null`` until the
  // first non-empty fetch completes; the dropdown shows a placeholder
  // "(no versions)" option in that window.
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  const [activeSubView, setActiveSubView] = useState<SubView>("lineage");
  const [harnessFilter, setHarnessFilter] = useState<Set<string>>(
    () => new Set(HARNESS_ORIGINS),
  );

  // When the registry loads (or refetches) and we don't yet have a
  // selection, snap to the current version. Avoids clobbering an
  // operator's manual selection on subsequent refreshes. Done in
  // ``useEffect`` (not inline-during-render) so the empty -> populated
  // transition that follows a manual clear still re-snaps.
  useEffect(() => {
    if (selectedVersion === null && versions.length > 0) {
      const current = versions.find((v) => v.current);
      setSelectedVersion((current ?? versions[0]).name);
    }
  }, [selectedVersion, versions]);

  // Race-filter visibility: HIDDEN when every version coerces to the
  // same race (today: always protoss). Coercion handles ``race: null``
  // legacy manifests so they don't artificially inflate the set.
  const distinctRaces = useMemo<string[]>(() => {
    const seen = new Set<string>();
    for (const v of versions) seen.add(coerceRace(v.race));
    return Array.from(seen);
  }, [versions]);
  const showRaceFilter = distinctRaces.length > 1;
  const [raceFilter, setRaceFilter] = useState<string>("all");

  const toggleHarness = useCallback((origin: string) => {
    setHarnessFilter((prev) => {
      const next = new Set(prev);
      if (next.has(origin)) {
        next.delete(origin);
      } else {
        next.add(origin);
      }
      return next;
    });
  }, []);

  const onNodeSelect = useCallback((versionName: string) => {
    setSelectedVersion(versionName);
    setActiveSubView("inspector");
  }, []);

  return (
    <div className="models-tab" data-testid="models-tab">
      {isStale ? (
        <StaleDataBanner lastSuccess={lastSuccess} label="Models" />
      ) : null}
      <div className="models-header" style={headerStyle}>
        <label style={fieldStyle}>
          <span style={labelStyle}>Version</span>
          <select
            data-testid="models-version-select"
            value={selectedVersion ?? ""}
            onChange={(e) => setSelectedVersion(e.target.value || null)}
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

        {showRaceFilter ? (
          <label style={fieldStyle} data-testid="models-race-filter">
            <span style={labelStyle}>Race</span>
            <select
              data-testid="models-race-select"
              value={raceFilter}
              onChange={(e) => setRaceFilter(e.target.value)}
            >
              <option value="all">All</option>
              {distinctRaces.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        <div
          data-testid="models-harness-chips"
          role="group"
          aria-label="Harness filter"
          style={chipRowStyle}
        >
          {HARNESS_ORIGINS.map((origin) => {
            const active = harnessFilter.has(origin);
            // Reuse the existing ``improvements-filter-pill`` rule
            // (App.css ~line 210) so the chips inherit theme tokens
            // (``--accent-bg`` / ``--accent-border`` etc.) instead of
            // hard-coded hex. ImprovementsTab still uses this class, so
            // it survives until at least Step 4 of the Models-tab plan.
            return (
              <button
                key={origin}
                type="button"
                data-testid={`models-harness-chip-${origin}`}
                onClick={() => toggleHarness(origin)}
                aria-pressed={active}
                className={
                  "improvements-filter-pill" +
                  (active ? " improvements-filter-pill-active" : "")
                }
              >
                {origin}
              </button>
            );
          })}
        </div>

        <button
          type="button"
          data-testid="models-refresh"
          onClick={() => refetch()}
          style={refreshStyle}
        >
          Refresh
        </button>
      </div>

      <nav
        data-testid="models-subview-router"
        role="tablist"
        aria-label="Models sub-views"
        style={subViewRouterStyle}
      >
        {SUB_VIEWS.map(({ id, label }) => {
          const isActive = activeSubView === id;
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={isActive}
              data-testid={`models-subview-button-${id}`}
              onClick={() => setActiveSubView(id)}
              style={isActive ? subViewActiveStyle : subViewInactiveStyle}
            >
              {label}
            </button>
          );
        })}
      </nav>

      <div className="models-subview-body" style={subViewBodyStyle}>
        {activeSubView === "lineage" ? (
          <LineagePlaceholder versions={versions} onNodeSelect={onNodeSelect} />
        ) : null}
        {activeSubView === "live" ? <LiveRunsPlaceholder /> : null}
        {activeSubView === "inspector" ? (
          <InspectorPlaceholder selectedVersion={selectedVersion} />
        ) : null}
        {activeSubView === "compare" ? <ComparePlaceholder /> : null}
        {activeSubView === "forensics" ? <ForensicsPlaceholder /> : null}
      </div>
    </div>
  );
}

// Inline styles — avoids tacking another section onto App.css for a
// shell that the next 5 steps will heavily restyle anyway.
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

const chipRowStyle: React.CSSProperties = {
  display: "flex",
  gap: "6px",
  alignItems: "center",
};

const refreshStyle: React.CSSProperties = {
  marginLeft: "auto",
  padding: "6px 12px",
};

const subViewRouterStyle: React.CSSProperties = {
  display: "flex",
  gap: "4px",
  borderBottom: "1px solid #333",
  marginBottom: "12px",
};

const subViewBaseStyle: React.CSSProperties = {
  padding: "8px 16px",
  background: "transparent",
  border: "none",
  borderBottom: "2px solid transparent",
  cursor: "pointer",
};

const subViewActiveStyle: React.CSSProperties = {
  ...subViewBaseStyle,
  borderBottomColor: "#3182ce",
  color: "#fff",
};

const subViewInactiveStyle: React.CSSProperties = {
  ...subViewBaseStyle,
  color: "#888",
};

const subViewBodyStyle: React.CSSProperties = {
  padding: "8px 0",
};

export default ModelsTab;
