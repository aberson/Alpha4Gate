import { useState, useCallback } from "react";
import type { GameState } from "../types/game";
import { useWebSocket } from "./useWebSocket";

export function useGameState() {
  const [gameState, setGameState] = useState<GameState | null>(null);

  const onMessage = useCallback((data: unknown) => {
    setGameState(data as GameState);
  }, []);

  const { connected } = useWebSocket({
    url: `ws://${window.location.host}/ws/game`,
    onMessage,
  });

  return { gameState, connected };
}
