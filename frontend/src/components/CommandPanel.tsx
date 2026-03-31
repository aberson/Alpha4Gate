import { useState, useEffect, useCallback, useRef } from "react";
import { useWebSocket } from "../hooks/useWebSocket";
import type {
  CommandModeValue,
  CommandHistoryEntry,
  CommandPrimitives,
  CommandEvent,
} from "../types/game";

const MODE_LABELS: Record<CommandModeValue, string> = {
  ai_assisted: "AI-Assisted",
  human_only: "Human Only",
  hybrid_cmd: "Hybrid",
};

export function CommandPanel() {
  // --- State ---
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<CommandModeValue>("ai_assisted");
  const [muted, setMuted] = useState(false);
  const [claudeInterval, setClaudeInterval] = useState(30);
  const [lockoutDuration, setLockoutDuration] = useState(5);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [history, setHistory] = useState<CommandHistoryEntry[]>([]);
  const [primitives, setPrimitives] = useState<CommandPrimitives | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [selectedSuggestion, setSelectedSuggestion] = useState(-1);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // --- Initial data fetch ---
  useEffect(() => {
    fetch("/api/commands/mode")
      .then((r) => r.json())
      .then((data) => {
        setMode(data.mode);
        setMuted(data.muted);
      })
      .catch(() => {});

    fetch("/api/commands/history")
      .then((r) => r.json())
      .then((data) => setHistory(data.commands || []))
      .catch(() => {});

    fetch("/api/commands/primitives")
      .then((r) => r.json())
      .then(setPrimitives)
      .catch(() => {});

    fetch("/api/commands/settings")
      .then((r) => r.json())
      .then((data) => {
        if (data.claude_interval != null) setClaudeInterval(data.claude_interval);
        if (data.lockout_duration != null) setLockoutDuration(data.lockout_duration);
        if (data.muted != null) setMuted(data.muted);
      })
      .catch(() => {});
  }, []);

  // --- WebSocket for real-time command events ---
  const onWsMessage = useCallback((data: unknown) => {
    const event = data as CommandEvent;
    setHistory((prev) => {
      const updated = prev.map((entry) =>
        entry.id === event.id ? { ...entry, status: event.type } : entry,
      );
      // If the event is "queued" and we don't have this entry, add it
      if (event.type === "queued" && !prev.some((e) => e.id === event.id)) {
        updated.push({
          id: event.id,
          text: "",
          parsed: event.parsed ?? null,
          source: event.source ?? "unknown",
          status: "queued",
          game_time: null,
          timestamp_utc: new Date().toISOString(),
        });
      }
      return updated;
    });
    if (event.type === "rejected" && event.reason) {
      setError(`Rejected: ${event.reason}`);
      setTimeout(() => setError(null), 5000);
    }
  }, []);

  useWebSocket({
    url: `ws://${window.location.host}/ws/commands`,
    onMessage: onWsMessage,
  });

  // --- Autocomplete logic ---
  const updateSuggestions = useCallback(
    (value: string) => {
      if (!primitives) {
        setSuggestions([]);
        return;
      }
      const parts = value.trim().toLowerCase().split(/\s+/);
      if (parts.length === 1 && parts[0] !== "") {
        // Suggest matching actions
        const matches = primitives.actions.filter((a) => a.startsWith(parts[0]));
        setSuggestions(matches);
      } else if (parts.length === 2) {
        // Suggest targets for the given action
        const action = parts[0];
        const partial = parts[1];
        const targets = primitives.targets[action] ?? [];
        const matches = targets.filter((t) => t.startsWith(partial));
        setSuggestions(matches.map((t) => `${action} ${t}`));
      } else if (parts.length >= 3) {
        // Suggest locations
        const partial = parts[parts.length - 1];
        const prefix = parts.slice(0, -1).join(" ");
        const matches = primitives.locations.filter((l) => l.startsWith(partial));
        setSuggestions(matches.map((l) => `${prefix} ${l}`));
      } else {
        setSuggestions([]);
      }
      setSelectedSuggestion(-1);
    },
    [primitives],
  );

  const handleInputChange = (value: string) => {
    setInput(value);
    updateSuggestions(value);
  };

  const applySuggestion = (suggestion: string) => {
    setInput(suggestion + " ");
    setSuggestions([]);
    setSelectedSuggestion(-1);
    inputRef.current?.focus();
  };

  // --- Submit command ---
  const submitCommand = async () => {
    const text = input.trim();
    if (!text) return;
    setSubmitting(true);
    setError(null);
    setSuggestions([]);
    try {
      const res = await fetch("/api/commands", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || `Error ${res.status}`);
      } else {
        // Add to local history immediately
        setHistory((prev) => [
          ...prev,
          {
            id: data.id,
            text,
            parsed: data.parsed ?? null,
            source: "human",
            status: data.status,
            game_time: null,
            timestamp_utc: new Date().toISOString(),
          },
        ]);
        setInput("");
      }
    } catch {
      setError("Failed to send command");
    } finally {
      setSubmitting(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (suggestions.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedSuggestion((prev) => Math.min(prev + 1, suggestions.length - 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedSuggestion((prev) => Math.max(prev - 1, -1));
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && selectedSuggestion >= 0)) {
        e.preventDefault();
        const idx = selectedSuggestion >= 0 ? selectedSuggestion : 0;
        applySuggestion(suggestions[idx]);
        return;
      }
      if (e.key === "Escape") {
        setSuggestions([]);
        setSelectedSuggestion(-1);
        return;
      }
    }
    if (e.key === "Enter") {
      void submitCommand();
    }
  };

  // --- Mode change ---
  const handleModeChange = async (newMode: CommandModeValue) => {
    try {
      const res = await fetch("/api/commands/mode", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: newMode }),
      });
      const data = await res.json();
      if (res.ok) {
        setMode(data.mode);
      }
    } catch {
      // Ignore network errors
    }
  };

  // --- Mute toggle ---
  const toggleMute = async () => {
    const newMuted = !muted;
    try {
      const res = await fetch("/api/commands/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ muted: newMuted }),
      });
      if (res.ok) {
        setMuted(newMuted);
      }
    } catch {
      // Ignore network errors
    }
  };

  // --- Settings update ---
  const updateSettings = async (key: string, value: number) => {
    try {
      await fetch("/api/commands/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: value }),
      });
    } catch {
      // Ignore network errors
    }
  };

  // --- Render ---
  const recentHistory = history.slice(-20).reverse();

  return (
    <div className="command-panel">
      <h3>Command Panel</h3>

      {/* Error display */}
      {error && <div className="command-error">{error}</div>}

      {/* Command input */}
      <div className="command-input-row">
        <div className="command-input-wrapper">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => handleInputChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a command (e.g. build stalkers)"
            disabled={submitting}
            className="command-input"
          />
          {suggestions.length > 0 && (
            <ul className="command-suggestions">
              {suggestions.map((s, i) => (
                <li
                  key={s}
                  className={i === selectedSuggestion ? "selected" : ""}
                  onMouseDown={() => applySuggestion(s)}
                >
                  {s}
                </li>
              ))}
            </ul>
          )}
        </div>
        <button onClick={() => void submitCommand()} disabled={submitting || !input.trim()}>
          Send
        </button>
      </div>

      {/* Controls row: mode selector + mute toggle */}
      <div className="command-controls">
        <label>
          Mode:{" "}
          <select
            value={mode}
            onChange={(e) => void handleModeChange(e.target.value as CommandModeValue)}
          >
            {Object.entries(MODE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>

        <button
          onClick={() => void toggleMute()}
          className={muted ? "mute-btn muted" : "mute-btn"}
        >
          {muted ? "Claude Muted" : "Mute Claude"}
        </button>

        <button
          onClick={() => setSettingsOpen(!settingsOpen)}
          className="settings-toggle"
        >
          {settingsOpen ? "Hide Settings" : "Settings"}
        </button>
      </div>

      {/* Collapsible settings */}
      {settingsOpen && (
        <div className="command-settings">
          <label>
            Claude Interval: {claudeInterval}s
            <input
              type="range"
              min={10}
              max={120}
              value={claudeInterval}
              onChange={(e) => {
                const v = Number(e.target.value);
                setClaudeInterval(v);
                void updateSettings("claude_interval", v);
              }}
            />
          </label>
          <label>
            Lockout Duration: {lockoutDuration}s
            <input
              type="range"
              min={1}
              max={30}
              value={lockoutDuration}
              onChange={(e) => {
                const v = Number(e.target.value);
                setLockoutDuration(v);
                void updateSettings("lockout_duration", v);
              }}
            />
          </label>
        </div>
      )}

      {/* Command history feed */}
      <div className="command-history">
        <h4>Command History</h4>
        {recentHistory.length === 0 ? (
          <p>No commands yet.</p>
        ) : (
          <ul className="command-history-list">
            {recentHistory.map((entry) => (
              <li key={entry.id} className={`command-entry status-${entry.status}`}>
                <div className="command-entry-header">
                  <span className={`source-badge source-${entry.source}`}>
                    {entry.source === "ai" ? "AI" : "Human"}
                  </span>
                  <span className={`status-badge status-${entry.status}`}>
                    {entry.status}
                  </span>
                  <span className="command-time">
                    {new Date(entry.timestamp_utc).toLocaleTimeString()}
                  </span>
                </div>
                {entry.text && <div className="command-text">{entry.text}</div>}
                {entry.parsed && (
                  <div className="command-parsed">
                    {entry.parsed.map((p, i) => (
                      <span key={i} className="parsed-primitive">
                        {p.action} {p.target}
                        {p.location ? ` @ ${p.location}` : ""}
                      </span>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
