---
name: a4g-motion
description: Alpha4Gate project adapter for the generic /judge-motion engine — supplies the dashboard's bring-up, readiness probes, auth posture, and the curated critical-transition flow spec (tab switch, loading-to-content scan swap, live-status flip, help-doc swap) with source-grounded selectors and motion rubrics. Invoke as "/judge-motion --adapter a4g-motion --flow dashboard-critical".
user-invocable: true
argument: No arguments of its own — flags belong to /judge-motion (e.g. --flow dashboard-critical, --transition <name>, --dry-run, --calibrate).
---

# Alpha4Gate Motion Adapter (a4g-motion)

The Alpha4Gate half of the adapter/engine split defined by
[`/judge-motion`](../../../../.claude/skills/judge-motion/SKILL.md): the engine owns capture,
frame selection, mechanical gates, vision-judge dispatch, calibration, and the verdict
contract — **none of that is restated here**. This adapter owns exactly what the engine's
flow-spec contract asks a project for: bring-up, readiness, auth, and the transition list
with motion rubrics. Sibling precedent: [`a4g-dashboard-check`](../a4g-dashboard-check/SKILL.md)
(the static-screenshot tier of the same dashboard).

## Bring-up (operator-started — never auto-spawn)

Both servers are **operator-started** per the workspace preference (report + suggest + wait;
do not start them from this skill). The facts this adapter contributes:

- **Frontend base URL:** `http://localhost:3000` (Vite; proxies `/api` + `/ws` to the backend).
- **Backend:** FastAPI on `http://localhost:8765`.
- **Readiness:** probe `http://localhost:3000` (expect HTTP 200) and
  `http://localhost:8765/api/training/status` (expect JSON 200) before any capture. Probe
  **`localhost`, never `127.0.0.1`** — Vite binds IPv6-only on Windows
  ([`frontend-ui.md`](../../rules/frontend-ui.md) § IPv6-only loopback). If either probe
  fails, report which server is down with its start command and stop — the same discipline
  as a4g-dashboard-check's prerequisites section.

The full run-book (exact start commands, probe commands) is stated ONCE, in
[`README.md`](README.md) § Operator run-book — it is not restated here.

## Auth

**None.** The dashboard API (`bots/v13/api.py`, served via the `bots/current` alias) is an
unauthenticated loopback FastAPI app — no auth middleware, no `Depends` guards, no
login/PIN gate on any route the flow touches. The engine's auth-gate discipline
(probe-first, UNREVIEWABLE) therefore never triggers for this adapter; no reach-state
step is needed beyond the readiness probes above.

## How to invoke

```text
/judge-motion --adapter a4g-motion --flow dashboard-critical
/judge-motion --adapter a4g-motion --flow dashboard-critical --transition tab-switch-models
/judge-motion --adapter a4g-motion --flow dashboard-critical --dry-run
```

Flow specs live in [`flows/`](flows/) (currently one: `dashboard-critical.json`). Calibration
(`--calibrate`), verdict semantics, run artifacts, and every other mechanic are the engine's —
see its SKILL.md. Run artifacts land in the Alpha4Gate project root at `.judge-motion/<run-id>/`
(gitignored via this project's `.gitignore`).

## Transitions overview (dashboard-critical)

Full rubrics live in [`flows/dashboard-critical.json`](flows/dashboard-critical.json);
selector + rubric provenance (source file:line for every value) and the per-transition
`budgetMs` capture-budget derivation (the budget ↔ rubric ↔ ambient-poll coupling) in
[`README.md`](README.md).

| Transition | Shape | Trigger | Judged motion |
|---|---|---|---|
| `tab-switch-models` | tab switch | click nav "Models" | instant conditional-render swap to the Models tab (no tab-panel transition CSS exists) |
| `processes-scan-swap` | loading→content | click nav "Processes" | "Scanning..." / "Loading..." text placeholders swap to process table + resource gauges as the live scan APIs resolve (staggered sections intended) |
| `connection-status-live-flip` | live-update pop-in | inert `body` click (focusIntent: unmanaged) | header ConnectionStatus flips gray "Connecting..." → green "Live" on first poll success; WSL badge pops in |
| `help-doc-swap` | loading→content | click nav "Help" | "Loading help…" swaps to the one-commit rendered operator-commands document |

**SPA constraint that shaped this list:** the dashboard is a `useState`-driven SPA with no
router — every capture starts on the default Advisor tab, so each transition must be exactly
one trigger-click from that state. The flow spec carries no `loading` selector arrays because
the app's loading placeholders are conditionally rendered (they mount only after the trigger),
and the engine's loading watcher can only attach to elements that exist at page load —
rationale and evidence in [`README.md`](README.md) § Why no loading selectors.
