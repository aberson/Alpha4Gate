import { useGameState } from "../hooks/useGameState";
import { CommandPanel } from "./CommandPanel";

export function LiveView() {
  const { gameState, connected } = useGameState();

  if (!connected) {
    return (
      <div className="live-view-layout">
        <div className="live-view"><p>Connecting to game...</p></div>
        <CommandPanel />
      </div>
    );
  }

  if (!gameState) {
    return (
      <div className="live-view-layout">
        <div className="live-view"><p>Waiting for game data...</p></div>
        <CommandPanel />
      </div>
    );
  }

  const minutes = Math.floor(gameState.game_time_seconds / 60);
  const seconds = Math.floor(gameState.game_time_seconds % 60);
  const timeStr = `${minutes}:${seconds.toString().padStart(2, "0")}`;

  return (
    <div className="live-view-layout">
      <div className="live-view">
        <h2>Live Game — {timeStr}</h2>
        <div className="stats-row">
          <span>Step: {gameState.game_step}</span>
          <span>Minerals: {gameState.minerals}</span>
          <span>Gas: {gameState.vespene}</span>
          <span>Supply: {gameState.supply_used}/{gameState.supply_cap}</span>
          <span>Score: {gameState.score}</span>
          <span>State: {gameState.strategic_state}</span>
        </div>
        <div className="units">
          <h3>Units</h3>
          <ul>
            {gameState.units.map((u) => (
              <li key={u.type}>{u.type}: {u.count}</li>
            ))}
          </ul>
        </div>
        {gameState.claude_advice && (
          <div className="advice">
            <h3>Claude Advice</h3>
            <p>{gameState.claude_advice}</p>
          </div>
        )}
      </div>
      <CommandPanel />
    </div>
  );
}
