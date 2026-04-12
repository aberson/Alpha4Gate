/**
 * Small strip rendered at the top of any tab that is currently showing
 * cached / offline data rather than fresh live data.
 *
 * Part of Phase 1a of the offline-first dashboard work. Replaces the
 * bare `Error: HTTP 502` fallback that every tab used to show when the
 * backend was unreachable. Now tabs render their last-known-good data
 * via `useApi()` and show this banner above the content so the operator
 * can distinguish stale from live at a glance.
 *
 * Design notes:
 *  - Amber / warning color, not red. This is not an error — the tab is
 *    functioning correctly, just showing older data than ideal.
 *  - Inline styles (no CSS class) because the rest of the codebase uses
 *    inline styles for per-component visual tweaks. Keeps diffs small
 *    and the component self-contained.
 *  - Relative-time helper is kept private to this component — if other
 *    components need it later, extract to `lib/time.ts`.
 */

interface StaleDataBannerProps {
  /**
   * When the data currently on screen was last successfully fetched.
   * `null` means "we never successfully fetched anything and we're
   * showing data read from IDB with no known fetchedAt" — which
   * shouldn't happen in practice (readCache returns `fetchedAt`) but we
   * handle it gracefully.
   */
  lastSuccess: Date | null;

  /**
   * Optional tab / section name to include in the banner text so an
   * operator scrolling past knows which data is stale. If omitted the
   * banner uses a generic "Backend offline — showing cached data."
   */
  label?: string;
}

function formatRelativeTime(then: Date, now: Date = new Date()): string {
  const deltaSec = Math.round((now.getTime() - then.getTime()) / 1000);
  if (deltaSec < 0) return "just now"; // clock skew / race
  if (deltaSec < 5) return "just now";
  if (deltaSec < 60) return `${deltaSec}s ago`;
  if (deltaSec < 3600) {
    const minutes = Math.floor(deltaSec / 60);
    return `${minutes} min ago`;
  }
  if (deltaSec < 86400) {
    const hours = Math.floor(deltaSec / 3600);
    return `${hours} hr ago`;
  }
  const days = Math.floor(deltaSec / 86400);
  return `${days}d ago`;
}

function formatAbsoluteTime(then: Date): string {
  return then.toLocaleString();
}

export function StaleDataBanner({ lastSuccess, label }: StaleDataBannerProps) {
  const headerText = label
    ? `${label} — backend offline, showing cached data`
    : "Backend offline — showing cached data";

  const timeText = lastSuccess
    ? `Last connected: ${formatRelativeTime(lastSuccess)} (${formatAbsoluteTime(lastSuccess)})`
    : "No cached data available";

  return (
    <div
      className="stale-data-banner"
      role="status"
      aria-live="polite"
      style={{
        backgroundColor: "rgba(251, 188, 4, 0.15)",
        borderLeft: "3px solid #fbbc04",
        color: "#fbbc04",
        padding: "8px 12px",
        marginBottom: "12px",
        fontSize: "0.85em",
        borderRadius: "4px",
      }}
    >
      <div style={{ fontWeight: 600 }}>{headerText}</div>
      <div style={{ color: "#c89e1e", marginTop: "2px" }}>{timeText}</div>
    </div>
  );
}
