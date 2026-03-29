import { useState } from "react";
import { LiveView } from "./components/LiveView";
import { Stats } from "./components/Stats";
import { BuildOrderEditor } from "./components/BuildOrderEditor";
import { ReplayBrowser } from "./components/ReplayBrowser";
import { DecisionQueue } from "./components/DecisionQueue";
import { TrainingDashboard } from "./components/TrainingDashboard";
import { CheckpointList } from "./components/CheckpointList";
import { RewardRuleEditor } from "./components/RewardRuleEditor";
import "./App.css";

type Tab = "live" | "stats" | "builds" | "replays" | "decisions" | "training";

function App() {
  const [tab, setTab] = useState<Tab>("live");

  return (
    <div className="app">
      <header>
        <h1>Alpha4Gate Dashboard</h1>
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
        </nav>
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
            <CheckpointList />
            <RewardRuleEditor />
          </>
        )}
      </main>
    </div>
  );
}

export default App;
