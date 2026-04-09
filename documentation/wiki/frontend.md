# Frontend Dashboard

React SPA for live game observation, training metrics, and command input.

> **At a glance:** 6-tab SPA (Live, Stats, Builds, Replays, Decisions, Training) built
> with React + TypeScript + Vite. Live game data via WebSocket (real-time). Training
> metrics via REST polling (5s). Three custom hooks handle WebSocket connections and
> API calls. All frontend code is domain-agnostic — it renders whatever JSON the backend
> sends.

## Purpose & Design

The dashboard provides transparency into what the bot is doing and how it's performing.
Today it's strongest at live game observation and weakest at training visibility (see
[monitoring.md](monitoring.md) for gaps).

### Tab layout

| Tab | Component(s) | Data source | Refresh |
|-----|-------------|-------------|---------|
| **Live** | LiveView + CommandPanel | `/ws/game` + `/ws/commands` | Real-time WebSocket |
| **Stats** | Stats | `/api/stats` | One-time fetch |
| **Builds** | BuildOrderEditor | `/api/build-orders` | One-time fetch |
| **Replays** | ReplayBrowser | `/api/replays` | One-time fetch (stub) |
| **Decisions** | DecisionQueue | `/api/decision-log` + `/ws/decisions` | Initial fetch + live |
| **Training** | TrainingDashboard + CheckpointList + RewardRuleEditor | `/api/training/*` + `/api/reward-rules` | 5s poll + one-time |

### What each component shows

**LiveView:** Game time, minerals, gas, supply, score, strategic state. Unit and
structure lists. Claude advice (when present). Side panel: CommandPanel.

**CommandPanel:** Text input with autocomplete from primitives vocabulary. Command
history (last 20) with status badges (queued/executed/failed/rejected). Mode selector
(AI-Assisted/Human Only/Hybrid). Mute toggle. Collapsible settings (Claude interval,
lockout duration).

**TrainingDashboard:** Current checkpoint name, total games, total transitions, DB size,
checkpoint count. Win rate table: last 10/50/100/overall.

**CheckpointList:** Table of all checkpoints with metadata (type, agreement, win rate,
difficulty). Best checkpoint marked with indicator.

**RewardRuleEditor:** Editable table of reward rules — ID, description, reward value
(number input), active toggle. Save button persists via PUT.

**Stats:** Total wins/losses, by-map breakdown, last 10 games table.

**DecisionQueue:** Last 20 state transitions: game step, from/to state, reason, Claude
advice.

**BuildOrderEditor:** Build order list with supply-threshold steps. Create/delete.

**ReplayBrowser:** File listing with basic stats on click (stub — parsing not implemented).

---

## Key Interfaces

### Custom hooks

| Hook | Purpose | Details |
|------|---------|---------|
| `useWebSocket({url, onMessage, reconnectInterval})` | Generic WS client | Auto-reconnect (3s default), JSON parsing, cleanup on unmount |
| `useGameState()` | Live game state | Wraps useWebSocket for `/ws/game`, returns `{gameState, connected}` |
| `useBuildOrders()` | Build order CRUD | GET/POST/DELETE via `/api/build-orders`, returns `{orders, loading, createOrder, deleteOrder, refresh}` |

### Polling intervals

| Component | Interval | Method |
|-----------|----------|--------|
| LiveView | Real-time | WebSocket |
| CommandPanel | Real-time | WebSocket + initial REST |
| TrainingDashboard | 5000ms | setInterval + fetch |
| Everything else | One-time | useEffect fetch on mount |
| WebSocket reconnect | 3000ms | useWebSocket hook |

---

## Implementation Notes

**Stack:** React 18 + TypeScript + Vite. Dev server on `:3000`, proxies to backend `:8765`.

**Routing:** Tab-based via `useState<Tab>("live")` — no React Router, just conditional
rendering based on active tab.

**Frontend is domain-agnostic:** Components render whatever JSON the API returns. Unit
type names, strategic states, and command vocabulary come from the backend. No SC2
concepts are hardcoded in the frontend.

| File | Purpose |
|------|---------|
| `frontend/src/App.tsx` | Tab routing (67 lines) |
| `frontend/src/components/LiveView.tsx` | Live game display |
| `frontend/src/components/CommandPanel.tsx` | Command input + history |
| `frontend/src/components/TrainingDashboard.tsx` | Training metrics |
| `frontend/src/components/CheckpointList.tsx` | Model checkpoints |
| `frontend/src/components/RewardRuleEditor.tsx` | Reward rule editing |
| `frontend/src/components/Stats.tsx` | Game statistics |
| `frontend/src/components/DecisionQueue.tsx` | Decision log |
| `frontend/src/components/BuildOrderEditor.tsx` | Build order CRUD |
| `frontend/src/components/ReplayBrowser.tsx` | Replay browser (stub) |
| `frontend/src/hooks/useWebSocket.ts` | Generic WS hook |
| `frontend/src/hooks/useGameState.ts` | Game state WS hook |
| `frontend/src/hooks/useBuildOrders.ts` | Build order API hook |
