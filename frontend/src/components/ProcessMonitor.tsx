import { useState, useCallback } from "react";
import { useApi } from "../hooks/useApi";
import { StaleDataBanner } from "./StaleDataBanner";

interface ProcessEntry {
  name: string;
  pid: number | null;
  status: string;
  role: string;
  start_time: string | null;
  details: string;
}

interface PortEntry {
  port: number;
  label: string;
  bound: boolean;
}

interface StateFileEntry {
  file: string;
  exists: boolean;
  key_field?: string;
  value?: unknown;
  updated_at?: string;
  error?: boolean;
}

interface ProcessStatus {
  processes: ProcessEntry[];
  ports: PortEntry[];
  state_files: StateFileEntry[];
  temp_files: Record<string, number>;
  scanned_at: string;
}

const ROLE_COLORS: Record<string, string> = {
  "backend-server": "#3498db",
  "backend-runner": "#2980b9",
  "backend-wrapper": "#2471a3",
  daemon: "#9b59b6",
  advisor: "#e67e22",
  frontend: "#2ecc71",
  sc2: "#e74c3c",
  "game-runner": "#f39c12",
  runner: "#f39c12",
  "lock-file": "#95a5a6",
  python: "#888",
  unknown: "#666",
  orphan: "#e74c3c",
};

const STATUS_COLORS: Record<string, string> = {
  running: "#2ecc71",
  stopped: "#888",
  stale: "#e74c3c",
  unknown: "#f39c12",
};

function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: "4px",
        backgroundColor: color,
        color: "#fff",
        fontWeight: 600,
        fontSize: "0.75em",
        textTransform: "uppercase",
      }}
    >
      {label}
    </span>
  );
}

export function ProcessMonitor() {
  const { data, isStale, isLoading, lastSuccess, refresh } =
    useApi<ProcessStatus>("/api/processes", { pollMs: 5000 });
  const [message, setMessage] = useState("");

  const backendOnline = data !== null && !isStale;

  const handleRestart = useCallback(async () => {
    try {
      await fetch("/api/restart", { method: "POST" });
      setMessage("Restarting backend\u2026 will reconnect in a few seconds");
    } catch {
      setMessage("Failed to send restart signal");
    }
    setTimeout(() => setMessage(""), 5000);
  }, []);

  if (!data) {
    return (
      <div className="process-monitor training-dashboard">
        <h2>Processes</h2>
        <p>{isLoading ? "Scanning..." : "Backend offline \u2014 cannot scan processes."}</p>
        <p style={{ color: "#888", fontSize: "0.85em" }}>
          Start the backend with: <code>uv run python -m alpha4gate.runner --serve</code>
        </p>
      </div>
    );
  }

  const runningCount = data.processes.filter((p) => p.status === "running").length;

  return (
    <div className="process-monitor training-dashboard">
      {isStale ? (
        <StaleDataBanner lastSuccess={lastSuccess} label="Processes" />
      ) : null}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2>
          Processes{" "}
          <span style={{ fontSize: "0.7em", color: "#888", fontWeight: 400 }}>
            ({runningCount} running)
          </span>
        </h2>
        <button
          type="button"
          onClick={() => refresh()}
          style={{ padding: "4px 12px", fontSize: "0.85em" }}
        >
          Refresh
        </button>
      </div>
      <p style={{ color: "#888", fontSize: "0.85em", margin: "0 0 16px" }}>
        Live inventory of all Alpha4Gate processes (backend, daemon, advisor, game runners, SC2), port binding status, state-file contents, and accumulated log/temp file counts. Stale or orphan entries here indicate a process that crashed without cleaning up.
      </p>

      {/* Backend controls */}
      <section
        style={{
          marginBottom: "24px",
          padding: "12px 16px",
          borderRadius: "6px",
          backgroundColor: "rgba(255,255,255,0.03)",
          border: "1px solid rgba(255,255,255,0.08)",
          display: "flex",
          alignItems: "center",
          gap: "16px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span
            style={{
              width: "12px",
              height: "12px",
              borderRadius: "50%",
              backgroundColor: backendOnline ? "#2ecc71" : "#e74c3c",
              display: "inline-block",
            }}
          />
          <strong>Backend</strong>
          <span style={{ color: backendOnline ? "#2ecc71" : "#e74c3c", fontSize: "0.85em" }}>
            {backendOnline ? "ON" : "OFF"}
          </span>
          {!backendOnline && (
            <span style={{ color: "#888", fontSize: "0.8em", marginLeft: "8px" }}>
              Start with: <code>uv run python -m alpha4gate.runner --serve</code>
            </span>
          )}
        </div>
        <div style={{ marginLeft: "auto" }}>
          <button
            type="button"
            onClick={() => void handleRestart()}
            style={{
              padding: "6px 14px",
              fontSize: "0.85em",
              backgroundColor: "#3498db",
              color: "#fff",
              border: "none",
              borderRadius: "4px",
              cursor: "pointer",
              fontWeight: 600,
            }}
          >
            Restart
          </button>
        </div>
      </section>

      {/* Feedback toast */}
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

      {/* Process table */}
      <section style={{ marginBottom: "24px" }}>
        <h3>Active Processes</h3>
        {data.processes.length === 0 ? (
          <div style={{ color: "#888" }}>No known processes detected</div>
        ) : (
          <table style={{ width: "100%", fontSize: "0.85em" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left" }}>Role</th>
                <th style={{ textAlign: "left" }}>Name</th>
                <th style={{ textAlign: "center" }}>PID</th>
                <th style={{ textAlign: "center" }}>Status</th>
                <th style={{ textAlign: "left" }}>Details</th>
                <th style={{ textAlign: "left" }}>Started</th>
              </tr>
            </thead>
            <tbody>
              {data.processes.map((p, i) => (
                <tr key={`${p.pid}-${p.name}-${i}`}>
                  <td>
                    <Badge label={p.role} color={ROLE_COLORS[p.role] ?? "#666"} />
                  </td>
                  <td style={{ fontFamily: "monospace" }}>{p.name}</td>
                  <td style={{ textAlign: "center", fontFamily: "monospace" }}>
                    {p.pid ?? "\u2014"}
                  </td>
                  <td style={{ textAlign: "center" }}>
                    <Badge
                      label={p.status}
                      color={STATUS_COLORS[p.status] ?? "#666"}
                    />
                  </td>
                  <td
                    style={{
                      color: "#aaa",
                      fontSize: "0.9em",
                      maxWidth: "300px",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={p.details}
                  >
                    {p.details || "\u2014"}
                  </td>
                  <td style={{ color: "#888", fontSize: "0.85em" }}>
                    {p.start_time
                      ? new Date(p.start_time).toLocaleTimeString()
                      : "\u2014"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Ports */}
      <section style={{ marginBottom: "24px" }}>
        <h3>Ports</h3>
        <div style={{ display: "flex", gap: "16px" }}>
          {data.ports.map((p) => (
            <div
              key={p.port}
              className="stat-card"
              style={{ display: "flex", alignItems: "center", gap: "8px" }}
            >
              <span
                style={{
                  width: "10px",
                  height: "10px",
                  borderRadius: "50%",
                  backgroundColor: p.bound ? "#2ecc71" : "#888",
                  display: "inline-block",
                }}
              />
              <span>
                <strong>{p.port}</strong>{" "}
                <span style={{ color: "#aaa", fontSize: "0.85em" }}>{p.label}</span>
              </span>
              <span style={{ color: p.bound ? "#2ecc71" : "#888", fontSize: "0.8em" }}>
                {p.bound ? "bound" : "free"}
              </span>
            </div>
          ))}
        </div>
      </section>

      {/* State files */}
      <section style={{ marginBottom: "24px" }}>
        <h3>State Files</h3>
        <table style={{ width: "100%", fontSize: "0.85em" }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left" }}>File</th>
              <th style={{ textAlign: "center" }}>Exists</th>
              <th style={{ textAlign: "left" }}>Key</th>
              <th style={{ textAlign: "left" }}>Value</th>
              <th style={{ textAlign: "left" }}>Updated</th>
            </tr>
          </thead>
          <tbody>
            {data.state_files.map((sf) => (
              <tr key={sf.file}>
                <td style={{ fontFamily: "monospace", fontSize: "0.9em" }}>{sf.file}</td>
                <td style={{ textAlign: "center" }}>
                  {sf.exists ? "\u2705" : "\u274c"}
                </td>
                <td style={{ color: "#aaa" }}>{sf.key_field ?? "\u2014"}</td>
                <td>
                  {sf.error ? (
                    <span style={{ color: "#e74c3c" }}>parse error</span>
                  ) : (
                    <code>{String(sf.value ?? "\u2014")}</code>
                  )}
                </td>
                <td style={{ color: "#888", fontSize: "0.85em" }}>
                  {sf.updated_at
                    ? new Date(sf.updated_at).toLocaleString()
                    : "\u2014"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* Temp files */}
      <section>
        <h3>Accumulated Files</h3>
        <div style={{ display: "flex", gap: "16px" }}>
          {Object.entries(data.temp_files).map(([label, count]) => (
            <div key={label} className="stat-card">
              <label>{label.replace(/_/g, " ")}</label>
              <span style={{ fontSize: "1.2em", fontWeight: 600 }}>{count}</span>
            </div>
          ))}
        </div>
      </section>

      <div style={{ color: "#666", fontSize: "0.75em", marginTop: "16px" }}>
        Last scan: {new Date(data.scanned_at).toLocaleTimeString()}
      </div>
    </div>
  );
}
