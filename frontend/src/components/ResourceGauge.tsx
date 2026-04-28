/**
 * Resource gauge — Windows host RAM / disk + WSL VM RAM + load avg.
 *
 * Surfaces the host-RAM-starvation condition that caused 2026-04-28's
 * SC2-spawn timeouts (free RAM dropped to 0.3 GB) before it bites
 * again. Each bar is colored by a simple band: green < 70 %, amber
 * 70-90 %, red > 90 %. Disk uses the same band.
 */
import { useResourceGauges } from "../hooks/useSystemInfo";

function bandColor(pct: number | null): string {
  if (pct === null) return "#666";
  if (pct >= 90) return "#e74c3c";
  if (pct >= 70) return "#fbbc04";
  return "#2ecc71";
}

interface BarProps {
  label: string;
  pctUsed: number | null;
  detail: string;
}

function GaugeBar({ label, pctUsed, detail }: BarProps) {
  const color = bandColor(pctUsed);
  const widthPct = pctUsed === null ? 0 : Math.min(100, Math.max(0, pctUsed));
  return (
    <div style={{ marginBottom: "10px" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontSize: "0.85em",
          marginBottom: "3px",
        }}
      >
        <span style={{ fontWeight: 600 }}>{label}</span>
        <span style={{ color: "#aaa", fontFamily: "monospace" }}>{detail}</span>
      </div>
      <div
        style={{
          height: "10px",
          backgroundColor: "#222",
          borderRadius: "3px",
          overflow: "hidden",
        }}
        role="progressbar"
        aria-valuenow={pctUsed ?? 0}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${label} ${pctUsed === null ? "unknown" : `${pctUsed.toFixed(0)}% used`}`}
      >
        <div
          style={{
            height: "100%",
            width: `${widthPct}%`,
            backgroundColor: color,
            transition: "width 0.3s ease, background-color 0.3s ease",
          }}
        />
      </div>
    </div>
  );
}

export function ResourceGauge() {
  const { data, isStale, isLoading } = useResourceGauges();

  if (isLoading && data === null) {
    return (
      <section className="resource-gauge">
        <h3>System Resources</h3>
        <p style={{ color: "#888" }}>Loading…</p>
      </section>
    );
  }

  if (!data) {
    return (
      <section className="resource-gauge">
        <h3>System Resources</h3>
        <p style={{ color: "#888" }}>Unavailable.</p>
      </section>
    );
  }

  const host = data.host;
  const wsl = data.wsl;

  return (
    <section className="resource-gauge">
      <h3>
        System Resources
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
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "20px",
        }}
      >
        <div>
          <h4 style={{ margin: "4px 0 8px", fontSize: "0.95em" }}>Windows host</h4>
          <GaugeBar
            label="RAM"
            pctUsed={host.ram_pct_used}
            detail={`${host.ram_used_gb.toFixed(1)} / ${host.ram_total_gb.toFixed(1)} GB (${host.ram_free_gb.toFixed(1)} free)`}
          />
          <GaugeBar
            label="Disk (repo drive)"
            pctUsed={host.disk_pct_used}
            detail={
              host.disk_total_gb !== null && host.disk_free_gb !== null
                ? `${(host.disk_total_gb - host.disk_free_gb).toFixed(0)} / ${host.disk_total_gb.toFixed(0)} GB (${host.disk_free_gb.toFixed(0)} free)`
                : "unavailable"
            }
          />
        </div>
        <div>
          <h4 style={{ margin: "4px 0 8px", fontSize: "0.95em" }}>WSL VM</h4>
          {wsl.available ? (
            <>
              <GaugeBar
                label="RAM"
                pctUsed={wsl.ram_pct_used}
                detail={
                  wsl.ram_used_gb !== null && wsl.ram_total_gb !== null
                    ? `${wsl.ram_used_gb.toFixed(1)} / ${wsl.ram_total_gb.toFixed(1)} GB`
                    : "unavailable"
                }
              />
              <div style={{ fontSize: "0.85em", color: "#aaa", marginTop: "8px" }}>
                Swap:{" "}
                <span style={{ fontFamily: "monospace" }}>
                  {wsl.swap_used_gb !== null && wsl.swap_total_gb !== null
                    ? `${wsl.swap_used_gb.toFixed(2)} / ${wsl.swap_total_gb.toFixed(2)} GB`
                    : "—"}
                </span>
                {"  "}
                Load (5m):{" "}
                <span style={{ fontFamily: "monospace" }}>
                  {wsl.load_avg_5m !== null ? wsl.load_avg_5m.toFixed(2) : "—"}
                </span>
              </div>
            </>
          ) : (
            <p style={{ color: "#888" }}>WSL not available.</p>
          )}
        </div>
      </div>
    </section>
  );
}
