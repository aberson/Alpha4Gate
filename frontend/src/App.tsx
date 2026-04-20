import { useState } from "react";
import { LiveView } from "./components/LiveView";
import { Stats } from "./components/Stats";
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
import { AdvisedControlPanel } from "./components/AdvisedControlPanel";
import { AdvisedImprovements } from "./components/AdvisedImprovements";
import { ProcessMonitor } from "./components/ProcessMonitor";
import { AlertsPanel } from "./components/AlertsPanel";
import { LadderTab } from "./components/LadderTab";
import { EvolutionTab } from "./components/EvolutionTab";
import { AlertToast } from "./components/AlertToast";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { useAdvisedRun } from "./hooks/useAdvisedRun";
import { useAlerts } from "./hooks/useAlerts";
import "./App.css";

type Tab =
  | "live"
  | "stats"
| "processes"
  | "decisions"
  | "training"
  | "loop"
  | "advisor"
  | "improvements"
  | "alerts"
  | "ladder"
  | "evolution";

function App() {
  const [tab, setTab] = useState<Tab>("live");
  const { state: advisedState } = useAdvisedRun();
  const advisedActive = advisedState.data?.status === "running" || advisedState.data?.status === "paused";
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
            onClick={() => setTab("advisor")}
            className={tab === "advisor" ? "active" : ""}
          >
            Advisor
            {advisedActive ? (
              <span
                aria-label="Advised run active"
                style={{
                  display: "inline-block",
                  width: "8px",
                  height: "8px",
                  borderRadius: "50%",
                  backgroundColor: "#2ecc71",
                  marginLeft: "6px",
                  verticalAlign: "middle",
                }}
              />
            ) : null}
          </button>
          <button
            onClick={() => setTab("improvements")}
            className={tab === "improvements" ? "active" : ""}
          >
            Improvements
          </button>
          <button
            onClick={() => setTab("processes")}
            className={tab === "processes" ? "active" : ""}
          >
            Processes
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
          <button
            onClick={() => setTab("ladder")}
            className={tab === "ladder" ? "active" : ""}
          >
            Ladder
          </button>
          <button
            onClick={() => setTab("evolution")}
            className={tab === "evolution" ? "active" : ""}
          >
            Evolution
          </button>
        </nav>
        <ConnectionStatus />
        </div>
      </header>
      <main>
        {tab === "live" && <LiveView />}
        {tab === "stats" && <Stats />}
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
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "24px", alignItems: "start" }}>
            <LoopStatus />
            <TriggerControls />
          </div>
        )}
        {tab === "advisor" && <AdvisedControlPanel />}
        {tab === "improvements" && (
          <>
            <RewardTrends />
            <RecentImprovements />
            <AdvisedImprovements />
          </>
        )}
        {tab === "processes" && <ProcessMonitor />}
        {tab === "ladder" && <LadderTab />}
        {tab === "evolution" && <EvolutionTab />}
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
