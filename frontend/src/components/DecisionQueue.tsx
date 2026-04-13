import { useState, useEffect, useCallback } from "react";
import type { DecisionEntry } from "../types/game";
import { useWebSocket } from "../hooks/useWebSocket";
import { useApi } from "../hooks/useApi";
import { StaleDataBanner } from "./StaleDataBanner";

interface DecisionLogResponse {
  entries: DecisionEntry[];
}

export function DecisionQueue() {
  // Historical entries come from /api/decision-log via the offline-first
  // useApi hook, so we render the last-known-good set even when the
  // backend is down. The WebSocket stream still feeds live updates on
  // top of the cached history whenever a connection is available.
  const {
    data: historyResponse,
    isStale,
    isLoading,
    lastSuccess,
  } = useApi<DecisionLogResponse>("/api/decision-log", { pollMs: 5000 });

  const [entries, setEntries] = useState<DecisionEntry[]>([]);

  // Seed the local entries list from whatever useApi currently holds
  // (cache on first paint, then live values). We intentionally replace
  // `entries` rather than merging because the historical endpoint
  // returns the authoritative list; WS deltas append on top.
  useEffect(() => {
    if (historyResponse && Array.isArray(historyResponse.entries)) {
      setEntries(historyResponse.entries);
    }
  }, [historyResponse]);

  // Listen for live decision events and append.
  const onMessage = useCallback((data: unknown) => {
    const event = data as { event: string; detail: DecisionEntry };
    if (event.event === "state_change") {
      setEntries((prev) => [...prev, event.detail]);
    }
  }, []);

  useWebSocket({
    url: `ws://${window.location.host}/ws/decisions`,
    onMessage,
  });

  const noData = entries.length === 0 && historyResponse === null;

  return (
    <div className="decision-queue">
      {isStale && historyResponse !== null ? (
        <StaleDataBanner lastSuccess={lastSuccess} label="Decision Log" />
      ) : null}
      <h2>Decision Log</h2>
      <p style={{ color: "#888", fontSize: "0.85em", margin: "0 0 16px" }}>
        Live and historical record of strategic state transitions during games. Each row shows which state the bot moved from/to, the triggering reason, and any Claude advisor suggestion that influenced the decision.
      </p>
      {noData ? (
        <p>
          {isLoading
            ? "Loading..."
            : "No cached decisions yet — open this tab once while the backend is up."}
        </p>
      ) : entries.length === 0 ? (
        <p>No decisions recorded.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Step</th>
              <th>From</th>
              <th>To</th>
              <th>Reason</th>
              <th>Claude</th>
            </tr>
          </thead>
          <tbody>
            {entries
              .slice(-20)
              .reverse()
              .map((e, i) => (
                <tr key={i}>
                  <td>{e.game_step}</td>
                  <td>{e.from_state}</td>
                  <td>{e.to_state}</td>
                  <td>{e.reason}</td>
                  <td>{e.claude_advice || "—"}</td>
                </tr>
              ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
