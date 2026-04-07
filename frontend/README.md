# Alpha4Gate Dashboard

React + TypeScript frontend for live game visualization, strategic command input, and training metrics.

## Setup

```bash
npm install
npm start        # Dev server on http://localhost:3000, proxies API to :8765
npm run build    # Production build to dist/
```

The backend must be running (`uv run python -m alpha4gate.runner --serve`) for WebSocket and REST endpoints.

## Components

| Component | Purpose |
|---|---|
| LiveView | Real-time game state: resources, supply, units, structures, strategic state |
| CommandPanel | Submit strategic commands (build, attack, defend) in 3 modes |
| Stats | Win/loss records, game duration, cross-game statistics |
| DecisionQueue | Live decision queue with strategic reasoning display |
| BuildOrderEditor | Create and edit build order sequences |
| ReplayBrowser | Browse and inspect past game replays |
| TrainingDashboard | PPO training metrics: loss curves, reward progression, episode stats |
| CheckpointList | Model checkpoint browser with version history |
| RewardRuleEditor | Configure reward shaping rules for training |

## WebSocket Endpoints

| Endpoint | Data |
|---|---|
| `/ws/game` | Live game state snapshots (resources, units, structures) |
| `/ws/decisions` | Strategic state changes and decision queue updates |
| `/ws/commands` | Command submission events and execution results |

## REST API

Commands: `POST /api/commands`, `GET /api/commands/history`
Settings: `GET /api/settings`, `PUT /api/settings`
