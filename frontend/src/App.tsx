import { useState } from "react";
import { AdvisedControlPanel } from "./components/AdvisedControlPanel";
import { EvolutionTab } from "./components/EvolutionTab";
import { ImprovementsTab } from "./components/ImprovementsTab";
import { ProcessMonitor } from "./components/ProcessMonitor";
import { ResourceGauge } from "./components/ResourceGauge";
import { WslProcessesPanel } from "./components/WslProcessesPanel";
import { AlertsPanel } from "./components/AlertsPanel";
import { HelpTab } from "./components/HelpTab";
import { AlertToast } from "./components/AlertToast";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { useAdvisedRun } from "./hooks/useAdvisedRun";
import { useAlerts } from "./hooks/useAlerts";
import "./App.css";

type Tab =
  | "advisor"
  | "evolution"
  | "improvements"
  | "processes"
  | "help";

function App() {
  const [tab, setTab] = useState<Tab>("advisor");
  const { state: advisedState } = useAdvisedRun();
  const advisedActive = advisedState.data?.status === "running" || advisedState.data?.status === "paused";
  const {
    alerts,
    ackedIds,
    newAlertsThisPoll,
    ackAlert,
    dismissAlert,
    markAllRead,
    clearHistory,
  } = useAlerts();

  return (
    <div className="app">
      <AlertToast newAlerts={newAlertsThisPoll} onView={() => setTab("processes")} />
      <header>
        <h1>Alpha4Gate Dashboard</h1>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "8px", flexWrap: "wrap" }}>
        <nav>
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
            onClick={() => setTab("evolution")}
            className={tab === "evolution" ? "active" : ""}
          >
            Evolution
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
            onClick={() => setTab("help")}
            className={tab === "help" ? "active" : ""}
          >
            Help
          </button>
        </nav>
        <ConnectionStatus />
        </div>
      </header>
      <main>
        {tab === "advisor" && <AdvisedControlPanel />}
        {tab === "evolution" && <EvolutionTab />}
        {tab === "improvements" && <ImprovementsTab />}
        {tab === "processes" && (
          <>
            <ProcessMonitor />
            <ResourceGauge />
            <WslProcessesPanel />
            <AlertsPanel
              alerts={alerts}
              ackedIds={ackedIds}
              onAck={ackAlert}
              onDismiss={dismissAlert}
              onMarkAllRead={markAllRead}
              onClearHistory={clearHistory}
            />
          </>
        )}
        {tab === "help" && <HelpTab />}
      </main>
    </div>
  );
}

export default App;
