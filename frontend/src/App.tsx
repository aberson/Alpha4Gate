import { useState } from "react";
import { LiveView } from "./components/LiveView";
import { Stats } from "./components/Stats";
import { BuildOrderEditor } from "./components/BuildOrderEditor";
import { ReplayBrowser } from "./components/ReplayBrowser";
import { DecisionQueue } from "./components/DecisionQueue";
import { TrainingDashboard } from "./components/TrainingDashboard";
import { CheckpointList } from "./components/CheckpointList";
import { RewardRuleEditor } from "./components/RewardRuleEditor";
import { ModelComparison } from "./components/ModelComparison";
import { ImprovementTimeline } from "./components/ImprovementTimeline";
import { LoopStatus } from "./components/LoopStatus";
import { TriggerControls } from "./components/TriggerControls";
import { RecentImprovements } from "./components/RecentImprovements";
import { RewardTrends } from "./components/RewardTrends";
import { AlertsPanel } from "./components/AlertsPanel";
import { AlertToast } from "./components/AlertToast";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { useAlerts } from "./hooks/useAlerts";
import "./App.css";

type Tab =
  | "live"
  | "stats"
  | "builds"
  | "replays"
  | "decisions"
  | "training"
  | "loop"
  | "improvements"
  | "alerts";

function App() {
  const [tab, setTab] = useState<Tab>("live");
  const {
    alerts,
    ackedIds,
    unreadCount,
    newAlertsThisPoll,
    ackAlert,
    dismissAlert,
    markAllRead,
    clearHistory,
  } = useAlerts();

  return (
    <div className="app">
      <AlertToast newAlerts={newAlertsThisPoll} onView={() => setTab("alerts")} />
      <header>
        <h1>Alpha4Gate Dashboard</h1>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "8px", flexWrap: "wrap" }}>
        <nav>
          <button onClick={() => setTab("live")} className={tab === "live" ? "active" : ""}>
            Live
          </button>
          <button onClick={() => setTab("stats")} className={tab === "stats" ? "active" : ""}>
            Stats
          </button>
          <button onClick={() => setTab("builds")} className={tab === "builds" ? "active" : ""}>
            Build Orders
          </button>
          <button onClick={() => setTab("replays")} className={tab === "replays" ? "active" : ""}>
            Replays
          </button>
          <button
            onClick={() => setTab("decisions")}
            className={tab === "decisions" ? "active" : ""}
          >
            Decisions
          </button>
          <button
            onClick={() => setTab("training")}
            className={tab === "training" ? "active" : ""}
          >
            Training
          </button>
          <button
            onClick={() => setTab("loop")}
            className={tab === "loop" ? "active" : ""}
          >
            Loop
          </button>
          <button
            onClick={() => setTab("improvements")}
            className={tab === "improvements" ? "active" : ""}
          >
            Improvements
          </button>
          <button
            onClick={() => setTab("alerts")}
            className={tab === "alerts" ? "active" : ""}
          >
            Alerts
            {unreadCount > 0 ? (
              <span className="unread-badge" aria-label={`${unreadCount} unread alerts`}>
                {unreadCount}
              </span>
            ) : null}
          </button>
        </nav>
        <ConnectionStatus />
        </div>
      </header>
      <main>
        {tab === "live" && <LiveView />}
        {tab === "stats" && <Stats />}
        {tab === "builds" && <BuildOrderEditor />}
        {tab === "replays" && <ReplayBrowser />}
        {tab === "decisions" && <DecisionQueue />}
        {tab === "training" && (
          <>
            <TrainingDashboard />
            <ModelComparison />
            <ImprovementTimeline />
            <CheckpointList />
            <RewardRuleEditor />
          </>
        )}
        {tab === "loop" && (
          <>
            <LoopStatus />
            <TriggerControls />
          </>
        )}
        {tab === "improvements" && (
          <>
            <RecentImprovements />
            <RewardTrends />
          </>
        )}
        {tab === "alerts" && (
          <AlertsPanel
            alerts={alerts}
            ackedIds={ackedIds}
            onAck={ackAlert}
            onDismiss={dismissAlert}
            onMarkAllRead={markAllRead}
            onClearHistory={clearHistory}
          />
        )}
      </main>
    </div>
  );
}

export default App;
