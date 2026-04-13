import { useApi } from "../hooks/useApi";
import { StaleDataBanner } from "./StaleDataBanner";

interface ImprovementEntry {
  id: string;
  timestamp: string;
  run_id: string;
  iteration: number | null;
  title: string;
  type: "training" | "dev";
  description: string;
  principles: string[];
  result: "pass" | "fail" | "stopped" | "pending";
  metrics: {
    observation_wins?: string;
    validation_wins?: string;
    duration_delta_pct?: number;
  };
  files_changed: string[];
}

interface ImprovementsResponse {
  improvements: ImprovementEntry[];
}

const RESULT_COLORS: Record<string, string> = {
  pass: "#22c55e",
  fail: "#ef4444",
  stopped: "#f59e0b",
  pending: "#3b82f6",
};

const TYPE_COLORS: Record<string, string> = {
  training: "#a78bfa",
  dev: "#38bdf8",
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
        marginRight: "6px",
      }}
    >
      {label}
    </span>
  );
}

export function AdvisedImprovements() {
  const { data, isStale, isLoading, lastSuccess } = useApi<ImprovementsResponse>(
    "/api/improvements",
    { pollMs: 10000 },
  );

  if (!data) {
    return (
      <div className="advised-improvements">
        {isLoading ? "Loading..." : "No improvement log yet."}
      </div>
    );
  }

  const items = [...data.improvements].reverse();

  return (
    <div className="advised-improvements training-dashboard">
      {isStale ? (
        <StaleDataBanner lastSuccess={lastSuccess} label="Advised Improvements" />
      ) : null}
      <h2>Advised Improvement Log</h2>
      <p style={{ color: "#888", fontSize: "0.85em", margin: "0 0 12px" }}>
        Changes made by <code>/improve-bot-advised</code> runs
      </p>

      {items.length === 0 ? (
        <div style={{ color: "#888", padding: "12px 0" }}>No improvements recorded yet.</div>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {items.map((entry) => (
            <li
              key={entry.id}
              style={{
                borderBottom: "1px solid rgba(255,255,255,0.08)",
                padding: "12px 0",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                <Badge label={entry.type} color={TYPE_COLORS[entry.type] ?? "#888"} />
                <Badge label={entry.result} color={RESULT_COLORS[entry.result] ?? "#888"} />
                <strong style={{ fontSize: "1em" }}>{entry.title}</strong>
              </div>

              <div style={{ color: "#bbb", fontSize: "0.85em", marginBottom: "4px" }}>
                {entry.description}
              </div>

              <div
                style={{
                  display: "flex",
                  gap: "16px",
                  flexWrap: "wrap",
                  fontSize: "0.8em",
                  color: "#888",
                }}
              >
                <span>
                  Run: <code>{entry.run_id}</code>
                  {entry.iteration !== null ? ` iter ${entry.iteration}` : ""}
                </span>
                <span>{new Date(entry.timestamp).toLocaleString()}</span>
                {entry.principles.length > 0 && (
                  <span>{entry.principles.join(", ")}</span>
                )}
                {entry.metrics.validation_wins && (
                  <span>
                    Validation: {entry.metrics.validation_wins}
                    {entry.metrics.duration_delta_pct !== undefined &&
                      ` (+${entry.metrics.duration_delta_pct}% duration)`}
                  </span>
                )}
              </div>

              {entry.files_changed.length > 0 && (
                <div style={{ fontSize: "0.75em", color: "#666", marginTop: "4px" }}>
                  Files: {entry.files_changed.join(", ")}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
