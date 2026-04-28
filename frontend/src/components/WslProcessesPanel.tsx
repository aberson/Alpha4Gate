/**
 * WSL processes panel — lists SC2_x64 + python (bots.v* / evolve / selfplay)
 * processes running inside the WSL VM.
 *
 * Companion to the existing /api/processes panel which only enumerates
 * Windows-host processes. Before this addition, an evolve run on the WSL
 * substrate produced a "Processes tab looks idle" mismatch with reality
 * (added 2026-04-28 as part of the WSL-aware dashboard surfaces).
 */
import { useWslProcesses } from "../hooks/useSystemInfo";

function formatRss(rssKb: number): string {
  if (rssKb < 1024) return `${rssKb} KB`;
  if (rssKb < 1024 * 1024) return `${(rssKb / 1024).toFixed(1)} MB`;
  return `${(rssKb / (1024 * 1024)).toFixed(2)} GB`;
}

export function WslProcessesPanel() {
  const { data, isStale, isLoading } = useWslProcesses();

  if (isLoading && data === null) {
    return (
      <section className="wsl-processes-panel">
        <h3>WSL Processes</h3>
        <p style={{ color: "#888" }}>Loading…</p>
      </section>
    );
  }

  if (!data || data.available === false) {
    return (
      <section className="wsl-processes-panel">
        <h3>WSL Processes</h3>
        <p style={{ color: "#888" }}>
          WSL not available on this host. (Backend probe returned
          unreachable.)
        </p>
      </section>
    );
  }

  return (
    <section className="wsl-processes-panel">
      <h3>
        WSL Processes
        {isStale ? (
          <span
            style={{
              marginLeft: "8px",
              fontSize: "0.7em",
              color: "#fbbc04",
              fontWeight: "normal",
            }}
          >
            (stale)
          </span>
        ) : null}
      </h3>
      {data.processes.length === 0 ? (
        <p style={{ color: "#888" }}>
          No SC2 / bots / evolve / selfplay processes detected inside the WSL VM.
        </p>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9em" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid #444" }}>
              <th style={{ padding: "4px 8px" }}>PID</th>
              <th style={{ padding: "4px 8px" }}>Label</th>
              <th style={{ padding: "4px 8px" }}>Comm</th>
              <th style={{ padding: "4px 8px" }}>Elapsed</th>
              <th style={{ padding: "4px 8px", textAlign: "right" }}>RSS</th>
            </tr>
          </thead>
          <tbody>
            {data.processes.map((p) => (
              <tr key={p.pid} style={{ borderBottom: "1px solid #2a2a2a" }}>
                <td style={{ padding: "4px 8px", fontFamily: "monospace" }}>{p.pid}</td>
                <td style={{ padding: "4px 8px", fontWeight: 600 }}>{p.label}</td>
                <td style={{ padding: "4px 8px", color: "#aaa" }}>{p.comm}</td>
                <td style={{ padding: "4px 8px", fontFamily: "monospace" }}>{p.etime}</td>
                <td
                  style={{
                    padding: "4px 8px",
                    fontFamily: "monospace",
                    textAlign: "right",
                  }}
                >
                  {formatRss(p.rss_kb)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
