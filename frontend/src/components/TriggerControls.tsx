import { useCallback, useEffect, useMemo, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { useDaemonStatus } from "../hooks/useDaemonStatus";
import type { DaemonConfigShape } from "../hooks/useDaemonStatus";
import { ConfirmDialog } from "./ConfirmDialog";

/**
 * Default daemon config shape. Mirrors the dataclass in
 * src/alpha4gate/learning/daemon.py (DaemonConfig). Used when the hook has
 * not yet returned any status so the form stays rendered.
 */
const DEFAULT_CONFIG: DaemonConfigShape = {
  check_interval_seconds: 60,
  min_transitions: 500,
  min_hours_since_last: 1.0,
  cycles_per_run: 5,
  games_per_cycle: 10,
  current_difficulty: 1,
  max_difficulty: 10,
  win_rate_threshold: 0.8,
};

/**
 * All editable DaemonConfig fields with their display labels and validation
 * rules. Order drives the rendered form. Reading the backend dataclass
 * (DaemonConfig in daemon.py) confirms these eight fields are the full set.
 */
type ConfigKey =
  | "check_interval_seconds"
  | "min_transitions"
  | "min_hours_since_last"
  | "cycles_per_run"
  | "games_per_cycle"
  | "current_difficulty"
  | "max_difficulty"
  | "win_rate_threshold";

interface ConfigFieldSpec {
  key: ConfigKey;
  label: string;
  /**
   * validation: "positive_int" (>=1 integer), "positive_float" (>0), or
   * "unit_interval" (0<=x<=1).
   */
  validation: "positive_int" | "positive_float" | "unit_interval";
  step: string;
}

const CONFIG_FIELDS: ConfigFieldSpec[] = [
  { key: "check_interval_seconds", label: "Check interval (seconds)", validation: "positive_int", step: "1" },
  { key: "min_transitions", label: "Min transitions", validation: "positive_int", step: "1" },
  { key: "min_hours_since_last", label: "Min hours since last run", validation: "positive_float", step: "0.1" },
  { key: "cycles_per_run", label: "Cycles per run", validation: "positive_int", step: "1" },
  { key: "games_per_cycle", label: "Games per cycle", validation: "positive_int", step: "1" },
  { key: "current_difficulty", label: "Current difficulty", validation: "positive_int", step: "1" },
  { key: "max_difficulty", label: "Max difficulty", validation: "positive_int", step: "1" },
  { key: "win_rate_threshold", label: "Win rate threshold (0-1)", validation: "unit_interval", step: "0.01" },
];

function validateField(spec: ConfigFieldSpec, value: number): string | null {
  if (!Number.isFinite(value)) return `${spec.label}: must be a number`;
  switch (spec.validation) {
    case "positive_int":
      if (!Number.isInteger(value) || value < 1) {
        return `${spec.label}: must be a positive integer`;
      }
      return null;
    case "positive_float":
      if (value <= 0) return `${spec.label}: must be greater than 0`;
      return null;
    case "unit_interval":
      if (value < 0 || value > 1) {
        return `${spec.label}: must be between 0 and 1`;
      }
      return null;
  }
}

function validateConfig(cfg: DaemonConfigShape): string | null {
  for (const spec of CONFIG_FIELDS) {
    const raw = cfg[spec.key];
    if (typeof raw !== "number") {
      return `${spec.label}: must be a number`;
    }
    const err = validateField(spec, raw);
    if (err) return err;
  }
  return null;
}

interface CheckpointSummary {
  name: string;
  file?: string;
  metadata?: Record<string, unknown>;
}

interface CheckpointListResponse {
  checkpoints: CheckpointSummary[];
  best: string | null;
}

interface StartStopResponse {
  status: string;
  message?: string;
}

interface UpdateConfigResponse {
  status: string;
  config?: DaemonConfigShape;
  message?: string;
}

interface EvaluateResponse {
  job_id?: string;
  status?: string;
  error?: string;
}

interface PromoteResponse {
  status?: string;
  checkpoint?: string;
  old_best?: string;
  error?: string;
}

interface RollbackResponse {
  status?: string;
  old_best?: string;
  new_best?: string;
  error?: string;
}

interface CurriculumResponse {
  current_difficulty: number;
  max_difficulty: number;
  win_rate_threshold: number;
  last_advancement: string | null;
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const parsed = (await response.json()) as T;
  if (!response.ok) {
    const message =
      (parsed as { error?: string; message?: string }).error ??
      (parsed as { error?: string; message?: string }).message ??
      `${url} returned ${response.status}`;
    throw new Error(message);
  }
  return parsed;
}

async function putJson<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const parsed = (await response.json()) as T;
  if (!response.ok) {
    const message =
      (parsed as { error?: string; message?: string }).error ??
      (parsed as { error?: string; message?: string }).message ??
      `${url} returned ${response.status}`;
    throw new Error(message);
  }
  return parsed;
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${url} returned ${response.status}`);
  }
  return (await response.json()) as T;
}

export function TriggerControls() {
  const { status, loading, refresh } = useDaemonStatus();

  // --- Daemon control ---
  const [daemonMessage, setDaemonMessage] = useState<string>("");

  // --- Config form state ---
  const [configDraft, setConfigDraft] = useState<DaemonConfigShape>(DEFAULT_CONFIG);
  const [configError, setConfigError] = useState<string | null>(null);
  const [configSaved, setConfigSaved] = useState<boolean>(false);
  const [configDirty, setConfigDirty] = useState<boolean>(false);

  // Pre-populate from hook on first status arrival (and any time config changes
  // server-side, as long as the user hasn't started editing locally).
  useEffect(() => {
    if (!status?.config) return;
    if (configDirty) return;
    setConfigDraft({ ...DEFAULT_CONFIG, ...status.config });
  }, [status, configDirty]);

  // Clear "saved" banner after 2 seconds.
  useEffect(() => {
    if (!configSaved) return;
    const t = setTimeout(() => setConfigSaved(false), 2000);
    return () => clearTimeout(t);
  }, [configSaved]);

  const handleConfigChange = useCallback(
    (key: ConfigKey) =>
      (e: ChangeEvent<HTMLInputElement>) => {
        const raw = e.target.value;
        const parsed = raw === "" ? Number.NaN : Number(raw);
        setConfigDraft((prev) => ({ ...prev, [key]: parsed }));
        setConfigDirty(true);
        setConfigSaved(false);
      },
    [],
  );

  const handleConfigSave = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      const err = validateConfig(configDraft);
      if (err) {
        setConfigError(err);
        return;
      }
      setConfigError(null);
      try {
        await putJson<UpdateConfigResponse>("/api/training/daemon/config", configDraft);
        setConfigSaved(true);
        setConfigDirty(false);
        refresh();
      } catch (ex) {
        setConfigError(ex instanceof Error ? ex.message : "Failed to save config");
      }
    },
    [configDraft, refresh],
  );

  // --- Daemon start/stop ---
  const running = status?.running ?? false;
  const handleStart = useCallback(async () => {
    setDaemonMessage("");
    try {
      const res = await postJson<StartStopResponse>("/api/training/start", {});
      setDaemonMessage(res.status);
      refresh();
    } catch (ex) {
      setDaemonMessage(ex instanceof Error ? ex.message : "start failed");
    }
  }, [refresh]);

  const handleStop = useCallback(async () => {
    setDaemonMessage("");
    try {
      const res = await postJson<StartStopResponse>("/api/training/stop", {});
      setDaemonMessage(res.status);
      refresh();
    } catch (ex) {
      setDaemonMessage(ex instanceof Error ? ex.message : "stop failed");
    }
  }, [refresh]);

  // --- Checkpoint list + manual evaluation ---
  const [checkpoints, setCheckpoints] = useState<string[]>([]);
  const [bestCheckpoint, setBestCheckpoint] = useState<string | null>(null);
  const [selectedCheckpoint, setSelectedCheckpoint] = useState<string>("");
  const [checkpointError, setCheckpointError] = useState<string | null>(null);

  const fetchCheckpoints = useCallback(async () => {
    try {
      const data = await getJson<CheckpointListResponse>("/api/training/checkpoints");
      // Backend returns an evaluation history which may contain multiple entries
      // per checkpoint name (one per evaluation run). The dropdown only needs
      // unique names since promote/rollback/evaluate all take a name. Dedupe
      // here while preserving backend order (keep first occurrence).
      const seen = new Set<string>();
      const names: string[] = [];
      for (const cp of data.checkpoints) {
        if (seen.has(cp.name)) continue;
        seen.add(cp.name);
        names.push(cp.name);
      }
      setCheckpoints(names);
      setBestCheckpoint(data.best);
      setCheckpointError(null);
      setSelectedCheckpoint((prev) => {
        if (prev && names.includes(prev)) return prev;
        return data.best ?? names[0] ?? "";
      });
    } catch (ex) {
      setCheckpointError(ex instanceof Error ? ex.message : "Failed to load checkpoints");
    }
  }, []);

  useEffect(() => {
    void fetchCheckpoints();
  }, [fetchCheckpoints]);

  const [evalGames, setEvalGames] = useState<number>(10);
  const [evalDifficulty, setEvalDifficulty] = useState<number>(1);
  const [evalResult, setEvalResult] = useState<string>("");

  const handleEvaluate = useCallback(async () => {
    setEvalResult("");
    if (!selectedCheckpoint) {
      setEvalResult("Select a checkpoint first");
      return;
    }
    try {
      const res = await postJson<EvaluateResponse>("/api/training/evaluate", {
        checkpoint: selectedCheckpoint,
        games: evalGames,
        difficulty: evalDifficulty,
      });
      if (res.job_id) {
        setEvalResult(`job ${res.job_id} (${res.status ?? "pending"})`);
      } else {
        setEvalResult(res.status ?? "submitted");
      }
    } catch (ex) {
      setEvalResult(ex instanceof Error ? ex.message : "evaluate failed");
    }
  }, [selectedCheckpoint, evalGames, evalDifficulty]);

  // --- Promote / rollback with ConfirmDialog ---
  const [promoteOpen, setPromoteOpen] = useState<boolean>(false);
  const [rollbackOpen, setRollbackOpen] = useState<boolean>(false);
  const [promoteResult, setPromoteResult] = useState<string>("");
  const [rollbackResult, setRollbackResult] = useState<string>("");

  const handlePromoteConfirm = useCallback(async () => {
    setPromoteOpen(false);
    setPromoteResult("");
    if (!selectedCheckpoint) {
      setPromoteResult("Select a checkpoint first");
      return;
    }
    try {
      const res = await postJson<PromoteResponse>("/api/training/promote", {
        checkpoint: selectedCheckpoint,
      });
      setPromoteResult(
        `${res.status ?? "ok"}: ${res.checkpoint ?? selectedCheckpoint}` +
          (res.old_best ? ` (was ${res.old_best})` : ""),
      );
      refresh();
    } catch (ex) {
      setPromoteResult(ex instanceof Error ? ex.message : "promote failed");
    }
  }, [selectedCheckpoint, refresh]);

  const handleRollbackConfirm = useCallback(async () => {
    setRollbackOpen(false);
    setRollbackResult("");
    if (!selectedCheckpoint) {
      setRollbackResult("Select a checkpoint first");
      return;
    }
    try {
      const res = await postJson<RollbackResponse>("/api/training/rollback", {
        checkpoint: selectedCheckpoint,
      });
      setRollbackResult(
        `${res.status ?? "ok"}: ${res.old_best ?? "?"} -> ${res.new_best ?? selectedCheckpoint}`,
      );
      refresh();
    } catch (ex) {
      setRollbackResult(ex instanceof Error ? ex.message : "rollback failed");
    }
  }, [selectedCheckpoint, refresh]);

  // --- Curriculum override ---
  const [curriculum, setCurriculum] = useState<CurriculumResponse | null>(null);
  const [curriculumDraft, setCurriculumDraft] = useState<number>(1);
  const [curriculumOpen, setCurriculumOpen] = useState<boolean>(false);
  const [curriculumResult, setCurriculumResult] = useState<string>("");

  const fetchCurriculum = useCallback(async () => {
    try {
      const data = await getJson<CurriculumResponse>("/api/training/curriculum");
      setCurriculum(data);
      setCurriculumDraft(data.current_difficulty);
    } catch {
      // non-fatal; show nothing
    }
  }, []);

  useEffect(() => {
    void fetchCurriculum();
  }, [fetchCurriculum]);

  const handleCurriculumConfirm = useCallback(async () => {
    setCurriculumOpen(false);
    setCurriculumResult("");
    if (!Number.isInteger(curriculumDraft) || curriculumDraft < 1) {
      setCurriculumResult("difficulty must be a positive integer");
      return;
    }
    try {
      const res = await putJson<CurriculumResponse>("/api/training/curriculum", {
        current_difficulty: curriculumDraft,
      });
      setCurriculum(res);
      setCurriculumResult(`difficulty set to ${res.current_difficulty}`);
      refresh();
    } catch (ex) {
      setCurriculumResult(ex instanceof Error ? ex.message : "curriculum update failed");
    }
  }, [curriculumDraft, refresh]);

  const bestLabel = useMemo(() => {
    if (!bestCheckpoint) return "";
    return ` (current best: ${bestCheckpoint})`;
  }, [bestCheckpoint]);

  return (
    <div className="trigger-controls training-dashboard">
      <h2>Loop Controls</h2>

      {/* A. Daemon control */}
      <section className="control-panel" aria-labelledby="panel-daemon">
        <h3 id="panel-daemon">Daemon</h3>
        <div className="control-row">
          <button
            type="button"
            onClick={handleStart}
            disabled={loading || running}
            aria-label="Start daemon"
          >
            Start
          </button>
          <button
            type="button"
            onClick={handleStop}
            disabled={loading || !running}
            aria-label="Stop daemon"
          >
            Stop
          </button>
          {daemonMessage ? (
            <span className="control-message" role="status">
              {daemonMessage}
            </span>
          ) : null}
        </div>
      </section>

      {/* B. Daemon config form */}
      <section className="control-panel" aria-labelledby="panel-config">
        <h3 id="panel-config">Daemon config</h3>
        <form onSubmit={handleConfigSave} className="config-form">
          <div
            className="config-grid"
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "12px 24px",
              maxWidth: "700px",
            }}
          >
            {CONFIG_FIELDS.map((spec) => {
              const raw = configDraft[spec.key];
              const value =
                typeof raw === "number" && Number.isFinite(raw) ? String(raw) : "";
              return (
                <label
                  key={spec.key}
                  className="config-field"
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "4px",
                  }}
                >
                  <span style={{ fontSize: "0.85em", color: "#aaa" }}>{spec.label}</span>
                  <input
                    type="number"
                    step={spec.step}
                    value={value}
                    onChange={handleConfigChange(spec.key)}
                    name={spec.key}
                    style={{ padding: "6px 8px" }}
                  />
                </label>
              );
            })}
          </div>
          <div className="control-row">
            <button type="submit">Save config</button>
            {configSaved ? (
              <span className="control-message" role="status">
                saved
              </span>
            ) : null}
            {configError ? (
              <span className="control-error" role="alert">
                {configError}
              </span>
            ) : null}
          </div>
        </form>
      </section>

      {/* C. Manual evaluation */}
      <section className="control-panel" aria-labelledby="panel-evaluate">
        <h3 id="panel-evaluate">Manual evaluation</h3>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr auto",
            gap: "12px 24px",
            maxWidth: "700px",
            alignItems: "end",
          }}
        >
          <label className="config-field" style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
            <span style={{ fontSize: "0.85em", color: "#aaa" }}>Checkpoint{bestLabel}</span>
            <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
              <select
                value={selectedCheckpoint}
                onChange={(e) => setSelectedCheckpoint(e.target.value)}
                aria-label="Checkpoint"
                style={{ padding: "6px 8px", flex: 1 }}
              >
                {checkpoints.length === 0 ? (
                  <option value="">(none)</option>
                ) : null}
                {checkpoints.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              <button type="button" onClick={() => void fetchCheckpoints()}>
                Refresh
              </button>
            </div>
          </label>
          <div />
          <label className="config-field" style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
            <span style={{ fontSize: "0.85em", color: "#aaa" }}>Games</span>
            <input
              type="number"
              min="1"
              step="1"
              value={evalGames}
              onChange={(e) => setEvalGames(Number(e.target.value))}
              aria-label="Games"
              style={{ padding: "6px 8px" }}
            />
          </label>
          <label className="config-field" style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
            <span style={{ fontSize: "0.85em", color: "#aaa" }}>Difficulty</span>
            <input
              type="number"
              min="1"
              step="1"
              value={evalDifficulty}
              onChange={(e) => setEvalDifficulty(Number(e.target.value))}
              aria-label="Difficulty"
              style={{ padding: "6px 8px" }}
            />
          </label>
        </div>
        <div style={{ marginTop: "8px" }}>
          <button type="button" onClick={handleEvaluate}>
            Evaluate
          </button>
        </div>
        {checkpointError ? (
          <div className="control-error" role="alert">
            {checkpointError}
          </div>
        ) : null}
        {evalResult ? (
          <div className="control-message" role="status">
            {evalResult}
          </div>
        ) : null}
      </section>

      {/* D. Manual promote / rollback */}
      <section className="control-panel" aria-labelledby="panel-promote">
        <h3 id="panel-promote">Promote / rollback</h3>
        <div className="control-row">
          <button
            type="button"
            onClick={() => setPromoteOpen(true)}
            disabled={!selectedCheckpoint}
          >
            Promote
          </button>
          <button
            type="button"
            className="destructive"
            onClick={() => setRollbackOpen(true)}
            disabled={!selectedCheckpoint}
          >
            Rollback
          </button>
        </div>
        {promoteResult ? (
          <div className="control-message" role="status">
            promote: {promoteResult}
          </div>
        ) : null}
        {rollbackResult ? (
          <div className="control-message" role="status">
            rollback: {rollbackResult}
          </div>
        ) : null}
        <ConfirmDialog
          open={promoteOpen}
          title="Promote checkpoint?"
          message={`Promote ${selectedCheckpoint || "(none)"} to current best.`}
          confirmLabel="Promote"
          onConfirm={() => void handlePromoteConfirm()}
          onCancel={() => setPromoteOpen(false)}
        />
        <ConfirmDialog
          open={rollbackOpen}
          title="Rollback checkpoint?"
          message={`Roll back current best to ${selectedCheckpoint || "(none)"}. This is destructive.`}
          confirmLabel="Rollback"
          destructive
          onConfirm={() => void handleRollbackConfirm()}
          onCancel={() => setRollbackOpen(false)}
        />
      </section>

      {/* E. Curriculum override */}
      <section className="control-panel" aria-labelledby="panel-curriculum">
        <h3 id="panel-curriculum">Curriculum override</h3>
        <div className="control-row">
          <div className="control-message">
            Current difficulty:{" "}
            <strong>{curriculum ? curriculum.current_difficulty : "—"}</strong>
            {curriculum ? ` / ${curriculum.max_difficulty}` : null}
          </div>
        </div>
        <div className="control-row">
          <label className="config-field">
            <span>New difficulty</span>
            <input
              type="number"
              min="1"
              step="1"
              value={curriculumDraft}
              onChange={(e) => setCurriculumDraft(Number(e.target.value))}
              aria-label="New difficulty"
            />
          </label>
          <button
            type="button"
            className="destructive"
            onClick={() => setCurriculumOpen(true)}
          >
            Set
          </button>
        </div>
        {curriculumResult ? (
          <div className="control-message" role="status">
            {curriculumResult}
          </div>
        ) : null}
        <ConfirmDialog
          open={curriculumOpen}
          title="Override curriculum?"
          message={`Set current difficulty to ${curriculumDraft}. This overrides the autonomous loop.`}
          confirmLabel="Set"
          destructive
          onConfirm={() => void handleCurriculumConfirm()}
          onCancel={() => setCurriculumOpen(false)}
        />
      </section>
    </div>
  );
}

export default TriggerControls;
