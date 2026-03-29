/** Game state entry matching the JSONL log schema. */
export interface GameState {
  timestamp: string;
  game_step: number;
  game_time_seconds: number;
  minerals: number;
  vespene: number;
  supply_used: number;
  supply_cap: number;
  units: UnitCount[];
  structures?: UnitCount[];
  actions_taken: Action[];
  strategic_state: string;
  decision_queue?: string[];
  claude_advice?: string | null;
  score: number;
}

export interface UnitCount {
  type: string;
  count: number;
}

export interface Action {
  action: string;
  target: string;
  location?: [number, number];
}

/** Build order types matching build_orders.json schema. */
export interface BuildStep {
  supply: number;
  action: string;
  target: string;
}

export interface BuildOrder {
  id: string;
  name: string;
  source: string;
  steps: BuildStep[];
}

/** Stats types matching stats.json schema. */
export interface GameResult {
  timestamp: string;
  map: string;
  opponent: string;
  result: "win" | "loss";
  duration_seconds: number;
  build_order_used: string;
  score: number;
}

export interface StatsAggregates {
  total_wins: number;
  total_losses: number;
  by_map: Record<string, { wins: number; losses: number }>;
  by_opponent: Record<string, { wins: number; losses: number }>;
  by_build_order: Record<string, { wins: number; losses: number }>;
}

export interface Stats {
  games: GameResult[];
  aggregates: StatsAggregates;
}

/** Decision log entry. */
export interface DecisionEntry {
  timestamp: string;
  game_step: number;
  from_state: string;
  to_state: string;
  reason: string;
  claude_advice: string | null;
}

/** Replay summary. */
export interface ReplaySummary {
  id: string;
  timestamp: string;
  map?: string;
  result?: string;
  duration_seconds?: number;
  filename: string;
}

/** Replay detail. */
export interface ReplayDetail {
  id: string;
  timeline: { game_time_seconds: number; event: string; detail: string }[];
  stats: {
    minerals_collected: number;
    gas_collected: number;
    units_produced: number;
    units_lost: number;
    structures_built: number;
  };
}

/** Status response. */
export interface StatusResponse {
  state: "playing" | "idle";
  game_step: number | null;
  game_time_seconds: number | null;
  minerals: number | null;
  vespene: number | null;
  supply_used: number | null;
  supply_cap: number | null;
  strategic_state: string | null;
}
