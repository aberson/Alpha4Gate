# a4g-motion — provenance + run-book

Grounding record for [`flows/dashboard-critical.json`](flows/dashboard-critical.json). Every
selector and every rubric number below is traced to the source that produced it (files
verified 2026-07-19 against the working tree; the backend is `bots/v13/api.py` because
`bots/current/current.txt` → `v13`). The engine contract itself lives in
[`/judge-motion`'s SKILL.md](../../../../.claude/skills/judge-motion/SKILL.md).

## Selector provenance

| Selector (flow spec) | Used by | Source | Evidence |
|---|---|---|---|
| `nav button:has-text("Models")` | `tab-switch-models` trigger | `frontend/src/App.tsx:45` (the `<nav>`), `:72-77` (the button, label "Models" at `:76`) | Top-level tab nav is plain `<button>`s inside `<nav>` with no ids/testids; text is the only stable hook (Playwright `:has-text()`; the sibling `a4g-dashboard-check/capture.mjs:56-61` also falls back to a text-based click — via `getByText` — for the same reason: no stable id/testid on nav items). Unique at trigger time: the only other `<nav>` (Models sub-view router, `ModelsTab.tsx:378`) mounts after the switch and contains no "Models" button. |
| `nav button:has-text("Processes")` | `processes-scan-swap` trigger | `frontend/src/App.tsx:84-89` (label at `:88`) | Same nav; unique in the document. |
| `nav button:has-text("Help")` | `help-doc-swap` trigger | `frontend/src/App.tsx:90-95` (label at `:94`) | Same nav; unique in the document. |
| `body` | `connection-status-live-flip` trigger (inert click) | `frontend/src/App.tsx:39-99` | The app root `div.app` / header defines no click handler on background regions — a body click changes nothing and focuses nothing (hence `focusIntent: unmanaged`). |
| `[data-testid=models-version-select]` | `tab-switch-models` readback note (settled-state cross-check) | `frontend/src/components/ModelsTab.tsx:303` | Version dropdown testid. |

Settled-state regions the vision judge will see (named in rubric text, not used as
mechanical selectors): `.connection-status` (`ConnectionStatus.tsx:87`), the "Scanning..."
placeholder (`ProcessMonitor.tsx:118`) and "Active Processes" heading
(`ProcessMonitor.tsx:247`), the "Loading…" placeholder (`ResourceGauge.tsx:73`),
"Loading help…" (`HelpTab.tsx:28`) and `.help-tab` (`HelpTab.tsx:32`).

## Why no `loading` selector arrays

The engine's loading watcher attaches `document.querySelector` + MutationObserver **to an
element that exists at page load** (`judge-motion/scripts/capture_motion.mjs:380-384`,
attach at DOMContentLoaded `:396-400`) and then logs its visibility toggles. Alpha4Gate's
loading placeholders are **conditionally rendered React branches** that mount only *after*
the trigger click switches the tab (`ProcessMonitor.tsx:114-124`, `ResourceGauge.tsx:69-76`,
`HelpTab.tsx:27-28`) — at watcher-attach time they don't exist, so every candidate selector
would just log `loading selector not found`. The loading-state signal is instead carried by
each rubric's `loadingStateIntent` and the judge's Q-loading question over the frames.

## Rubric derivation

The full CSS-motion inventory of `frontend/src` (grep for `transition|animation|@keyframes`,
2026-07-19) is: `App.css:84` (`.alert-toast` opacity/transform 0.25s), `App.css:295`
(`.improvements-row` hover background 0.12s), `AdvisedControlPanel.tsx:56` (progress width
0.3s), `EvolutionTab.tsx:378` (progress width 300ms) + `:404-409` (`pulse` 1.2s indefinite
bar), `LiveRunsGrid.tsx:76` (progress width 300ms), `ResourceGauge.tsx:58` (gauge width +
color 0.3s). **Nothing else animates — in particular, no tab panel, nav, or page container
has any transition CSS.** That inventory is the backbone of every "easing: none /
instant-swap" rubric.

| Transition | Rubric value | Derived from |
|---|---|---|
| `tab-switch-models` | `expectedDurationMs [0, 250]` | No transition CSS applies to tab mount (inventory above); the swap is one React commit + one paint (`App.tsx:100-121` conditional render). 250ms = generous single-commit budget, no animation to wait out. |
| `tab-switch-models` | dropdown "(no versions)" note | `ModelsTab.tsx:307-309` renders the placeholder option until `/api/versions` resolves. |
| `processes-scan-swap` | `expectedDurationMs [300, 12000]` | `/api/processes` performs a live host process scan (`useApi` consumer at `ProcessMonitor.tsx:80-81`); the sibling skill budgets up to 15s for the "Active Processes" heading (`a4g-dashboard-check/capture.mjs:64-65`). Lower bound: a real Win32 process enumeration is never instant. The 12000ms ceiling is deliberately paired with an 18s capture window (`budgetMs: 1800`) — see § Capture budgets for the coupling. |
| `processes-scan-swap` | staggered-sections-are-intended easing note | The Processes view is four independent fetch consumers: `/api/processes` 5s poll (`ProcessMonitor.tsx:80-81`), `/api/system/resources` 3s poll (`useSystemInfo.ts:81-84`), `/api/system/wsl-processes` 3s poll (`useSystemInfo.ts:75-79`), alerts via `useAlerts` (`App.tsx:110-117`). Each section swaps in when its own API lands. |
| `connection-status-live-flip` | `expectedDurationMs [0, 5500]` | `useApi` fires the FIRST `/api/training/status` fetch **on mount** (`useApi.ts:190-191`, "Always trigger the first network fetch on mount"), so on a loopback backend the gray→green flip typically lands at/near page load — often BEFORE the inert body-click trigger even fires. `POLL_MS = 5000` (`ConnectionStatus.tsx:30`) governs only *subsequent* ticks. The 5500ms ceiling is deliberately generous headroom for the rescue case (a cold/slow/failed first fetch caught by the next 5s tick) — it is NOT a "wait for the first poll cycle" calculation. |
| `connection-status-live-flip` | gray→green instant swap, WSL badge pop-in | "Connecting..." branch `ConnectionStatus.tsx:63-66`; "Live" branch `:67-72`; WSL badge `:139-171` gated on `/api/system/substrate` (30s poll, `useSystemInfo.ts:69-73`) — no transition CSS on any of it (inventory above). |
| `connection-status-live-flip` | pre-trigger-resolution caveat (readback note) | The screencast starts **before** navigation (`capture_motion.mjs:615`), so the flip is on film even when the first poll beats the inert body click; the duration gate alone must not decide this transition. |
| `help-doc-swap` | `expectedDurationMs [0, 2000]` | Single mount-time fetch of a static markdown doc, no poll (`HelpTab.tsx:22-24`), rendered in one react-markdown commit (`HelpTab.tsx:32`). Floor is 0, not a small positive number: the doc is ~27KB and a sub-50ms fetch→parse→paint round trip is plausible on a fast loopback box; `gate_duration`'s normalization can only *shrink* a reading, so a nonzero floor has no low-end rescue path and would false-FAIL a faster-than-expected (strictly better) swap. |

## Capture budgets (`budgetMs` ↔ rubric ↔ ambient polls)

The engine stops each screencast a **fixed** wall-clock time after the trigger:
`budgetMs / playbackRate` (`capture_motion.mjs` `budgetOf` `:454-458`; without a spec
override it falls to `DEFAULT_BUDGET_MS = 1500` `:79` → 15s at the default 0.1 rate; there
is **no completion-signal early stop**, header `:47-51`). `measuredDurationMs` is
trigger → **last real frame anywhere in that window** (`:737`) — so a paint late in the
window, even one unrelated to the transition, sets the duration reading. Alpha4Gate's
header machinery (`ConnectionStatus` + `useAlerts` + `AlertToast`) is mounted on **every**
tab (`App.tsx:12,14,37,41,97`) and polls on 5s clocks (`useAlerts.ts:98-108`,
`ConnectionStatus.tsx:30`), with 3s gauge polls and a 30s substrate poll behind the
Processes view (`useSystemInfo.ts:69-84`) — ambient paints that a naive 15s default window
would let land inside every capture. Every transition therefore pins an explicit
`budgetMs`, sized against BOTH its rubric ceiling and those poll clocks.

All wall-clock numbers below assume the default `--playback-rate 0.1`; network + JS timers
do **not** dilate under slow-mo (only CSS/WAAPI content does). "Normalized rescue ceiling"
= the largest `measuredDurationMs` the second `gate_duration` reading still passes
(`rubric hi / 0.1`); the gate FAILs only when raw AND normalized are both out of range.

| Transition | `budgetMs` | Wall window | Rubric hi (raw) | Normalized rescue ceiling | Why this window is safe |
|---|---|---|---|---|---|
| `tab-switch-models` | 300 | 3.0s | 250ms | 2500ms | SHORT: window closes before the app's first ambient 5s tick can paint. The 5s clocks start at page mount, which precedes the trigger by only the nav-ready wait (well under 2s on loopback), so the first tick lands ≥ 3s after the trigger — at or past the window's close; alert ticks additionally repaint only on a state *change*. The only in-window paints are the swap itself + the mount-time `/api/versions` resolve (loopback-fast, far inside 2.5s), so a correct run passes on the normalized reading at worst. |
| `processes-scan-swap` | 1800 | 18.0s | 12000ms | 120000ms | LONG on purpose: the window must OUTLAST the slowest real scan (sibling evidence: up to 15s) with margin, so a slow scan is captured to settle instead of truncated mid-"Scanning..." — a stranded placeholder in the final frames would read to the vision judge as an app defect when the real cause is a short window. Ambient 3s/5s repaints inside the long window inflate the raw reading past 12000ms, but the normalized reading (≤ 18000 × 0.1 = 1800ms) stays in range → no mechanical false-FAIL. |
| `connection-status-live-flip` | 800 | 8.0s | 5500ms | 55000ms | CONTAIN: timers don't dilate, so the window must contain one full 5s poll period + fetch slack — the rescue case where the mount-time fetch fails and the flip lands on the first 5s tick. A flip anywhere in the window passes: raw ≤ 5500ms directly, later ambient paints via the normalized reading (≤ 800ms). |
| `help-doc-swap` | 300 | 3.0s | 2000ms | 20000ms | SHORT, same shape as the tab switch: closes before any ambient 5s tick; the single mount-time doc fetch resolves loopback-fast, and even a slow 2-3s land is rescued by the normalized reading (≤ 300ms). |

**Coupling invariants — re-derive this table if you touch ANY of: a `budgetMs`, a rubric
`expectedDurationMs`, the app's poll intervals, or the default playback rate:**

- A **SHORT** window must END before the first ambient 5s poll tick can inject an
  unrelated late paint; with a tight rubric ceiling, such a paint pushes BOTH duration
  readings out of range and false-FAILs a correct run (the exact trust-killer the
  dual-reading rule exists to avoid).
- A **LONG** window must EXCEED the transition's own worst-case settle (rubric ceiling +
  real margin) so the capture never truncates mid-loading, AND keep
  `window × playbackRate ≤ rubric hi` so ambient repaints stay rescued.
- A **CONTAIN** window must cover one full period of the timer that drives the transition,
  plus fetch slack.

## Read-back handles

Direct backend hits on `:8765` (bypassing the Vite proxy, `vite.config.ts:10-15`). The
dashboard API is unauthenticated loopback FastAPI — no auth middleware or route guards in
`bots/v13/api.py` — so every handle is a bare GET. Routes verified:

| Handle | Route definition |
|---|---|
| `GET /api/versions` | `bots/v13/api.py:2474` |
| `GET /api/processes` | `bots/v13/api.py:1816` |
| `GET /api/training/status` | `bots/v13/api.py:575` |
| `GET /api/operator-commands` | `bots/v13/api.py:554` |

Not used but adjacent (for future transitions): `/api/system/resources`
(`bots/v13/api.py:1804`), `/api/system/substrate` (`:1778`), `/api/advised/state` (`:1302`),
`/api/runs/active` (`:3413`).

## Deliberately out of scope

- **Alert-toast pop-in** — the app's only true CSS-transition entrance (`.alert-toast`,
  `App.css:67-90`, 0.25s opacity/transform) fires only when `useAlerts` detects a new alert;
  at a healthy T1 baseline that's non-deterministic, so it can't be a curated flow
  transition. Judge it ad hoc with an inline spec when an alert-producing state is staged.
- **Sub-view transitions** (Models → Live Runs, Inspector, etc.) — the SPA has no router
  (`App.tsx:26` `useState<Tab>("advisor")`), so every capture starts on the Advisor tab and
  the harness supports exactly one trigger click; two-click states are unreachable until the
  engine grows a pre-steps concept.
- **EvolutionTab `pulse` skeleton** (`EvolutionTab.tsx:404-409`) — only renders during an
  active evolve run; same staged-state story as the toast.

## Operator run-book

The ONE canonical copy of the run-book ([`SKILL.md`](SKILL.md) § Bring-up keeps only the
facts — base URL, readiness targets, operator-started — plus a pointer here). Commands are
PowerShell-pastable (the operator's default shell): no `&&`, and `curl.exe` rather than the
bare `curl` → `Invoke-WebRequest` alias.

Start the servers (operator-run, two terminals, from the Alpha4Gate project root):

```powershell
uv run python -m bots.current.runner --serve      # backend (FastAPI) on :8765
```

```powershell
cd frontend; npm run dev                          # frontend (Vite) on :3000, proxies /api + /ws to :8765
```

(`bash scripts/start-dev.sh` starts both together, but it is built for `/build-step --ui`'s
foreground-wait lifecycle — for judge-motion runs prefer the two commands above in separate
terminals.) Port pinning (`server.port: 3000`, `strictPort: true`) lives in
`frontend/vite.config.ts`; Windows dev-server traps are owned by
[`frontend-ui.md`](../../rules/frontend-ui.md).

Verify readiness (`localhost`, never `127.0.0.1` — Vite binds IPv6-only on Windows):

```powershell
curl.exe -s -o NUL -w "%{http_code}" http://localhost:3000    # frontend -> expect 200
curl.exe -s http://localhost:8765/api/training/status         # backend  -> expect JSON 200
```

Then invoke the engine — the invocation forms (bare, `--transition`, `--dry-run`) and the
run-artifact location are owned by [`SKILL.md`](SKILL.md) § How to invoke. Calibration is
engine-mandated before the first real verdict of a session — see the engine's SKILL.md.
