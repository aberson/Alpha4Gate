import { useState, useEffect, useCallback } from "react";
import type { DecisionEntry } from "../types/game";
import { useWebSocket } from "../hooks/useWebSocket";

export function DecisionQueue() {
  const [entries, setEntries] = useState<DecisionEntry[]>([]);

  // Load historical entries
  useEffect(() => {
    fetch("/api/decision-log")
      .then((r) => r.json())
      .then((data) => setEntries(data.entries || []))
      .catch(() => {});
  }, []);

  // Listen for live decision events
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

  return (
    <div className="decision-queue">
      <h2>Decision Log</h2>
      {entries.length === 0 ? (
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
