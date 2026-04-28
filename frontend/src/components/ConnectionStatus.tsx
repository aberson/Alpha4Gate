/**
 * Small connection-status indicator rendered in the dashboard header
 * next to the nav. Shows a dot + label reflecting backend reachability.
 *
 * Part of Phase 1a of the offline-first dashboard work. Polls a cheap
 * health endpoint (`/api/training/status`) via `useApi` and maps the
 * hook's state to one of three visible states:
 *
 *  - `live` (green): last fetch succeeded recently.
 *  - `stale` (amber): we have cached data but the last fetch failed or
 *    we're still waiting on the first fetch after a cache read.
 *  - `disconnected` (red): no cached data and no successful fetch yet.
 *
 * The indicator is purely informational — no interactions. Operators
 * use it as a quick "is the backend up?" glance before diving into a
 * specific tab.
 */

import { useApi } from "../hooks/useApi";
import type { AdvisedRunState } from "../hooks/useAdvisedRun";
import { useSubstrateInfo } from "../hooks/useSystemInfo";

interface TrainingStatusPing {
  // We don't actually care about the body — just that the call
  // succeeded. Typed as a permissive record so we can share the
  // endpoint with other components without a forced cast.
  [key: string]: unknown;
}

const POLL_MS = 5000;

function formatRelativeTime(then: Date, now: Date = new Date()): string {
  const deltaSec = Math.round((now.getTime() - then.getTime()) / 1000);
  if (deltaSec < 5) return "just now";
  if (deltaSec < 60) return `${deltaSec}s ago`;
  if (deltaSec < 3600) {
    const minutes = Math.floor(deltaSec / 60);
    return `${minutes}m ago`;
  }
  const hours = Math.floor(deltaSec / 3600);
  return `${hours}h ago`;
}

export function ConnectionStatus() {
  const { data, isStale, isLoading, lastSuccess } = useApi<TrainingStatusPing>(
    "/api/training/status",
    { pollMs: POLL_MS }
  );
  const { data: advisedData } = useApi<AdvisedRunState>(
    "/api/advised/state",
    { pollMs: POLL_MS }
  );
  const advisedActive = advisedData?.status === "running" || advisedData?.status === "paused";
  const { data: substrate } = useSubstrateInfo();
  const wslReady =
    substrate?.wsl.available === true && substrate?.wsl.sc2_binary_present === true;
  const wslLabel = substrate?.wsl.distro ?? "WSL";

  let color: string;
  let label: string;
  let title: string;

  if (isLoading && data === null) {
    color = "#888";
    label = "Connecting...";
    title = "Contacting backend for the first time.";
  } else if (!isStale) {
    color = "#2ecc71";
    label = "Live";
    title = lastSuccess
      ? `Backend responding. Last update: ${formatRelativeTime(lastSuccess)}.`
      : "Backend responding.";
  } else if (data !== null) {
    color = "#fbbc04";
    label = `Stale${lastSuccess ? ` (${formatRelativeTime(lastSuccess)})` : ""}`;
    title = lastSuccess
      ? `Backend unreachable. Showing cached data from ${lastSuccess.toLocaleString()}.`
      : "Backend unreachable. Showing cached data.";
  } else {
    color = "#e74c3c";
    label = "Disconnected";
    title = "Backend unreachable and no cached data available.";
  }

  return (
    <div
      className="connection-status"
      role="status"
      aria-live="polite"
      title={title}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        marginLeft: "12px",
        fontSize: "0.85em",
        color,
      }}
    >
      <span
        aria-hidden="true"
        style={{
          display: "inline-block",
          width: "8px",
          height: "8px",
          borderRadius: "50%",
          backgroundColor: color,
        }}
      />
      <span>{label}</span>
      {advisedActive ? (
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "4px",
            marginLeft: "8px",
            padding: "2px 8px",
            borderRadius: "4px",
            backgroundColor: "rgba(46, 204, 113, 0.15)",
            color: "#2ecc71",
            fontSize: "0.85em",
            fontWeight: 600,
          }}
        >
          <span
            aria-hidden="true"
            style={{
              display: "inline-block",
              width: "6px",
              height: "6px",
              borderRadius: "50%",
              backgroundColor: "#2ecc71",
            }}
          />
          Advisor
        </span>
      ) : null}
      {wslReady ? (
        <span
          title={
            substrate?.wsl.kernel
              ? `WSL substrate ready: ${wslLabel} (kernel ${substrate.wsl.kernel}); SC2 binary at ${substrate.wsl.sc2_path}`
              : `WSL substrate ready: ${wslLabel}`
          }
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "4px",
            marginLeft: "8px",
            padding: "2px 8px",
            borderRadius: "4px",
            backgroundColor: "rgba(99, 102, 241, 0.15)",
            color: "#6366f1",
            fontSize: "0.85em",
            fontWeight: 600,
          }}
        >
          <span
            aria-hidden="true"
            style={{
              display: "inline-block",
              width: "6px",
              height: "6px",
              borderRadius: "50%",
              backgroundColor: "#6366f1",
            }}
          />
          {wslLabel}
        </span>
      ) : null}
    </div>
  );
}
