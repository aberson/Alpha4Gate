# Typesafe Jev implementation verification

Date: 2026-10-08. Working-tree implementation of Steps 209/210; Step 211 live
acceptance remains pending. No Typesafe key was configured, no paid inference
request was sent, and no SC2 match was launched during this work.

## Changes verified

- `--decision-provider typesafe` engages the actual hosted HTTP provider.
  `scripted` remains the default. Missing credentials fail before SC2 preparation.
- One async request at a time, total timeout, dispatch cadence and per-match cap;
  invalid, low-confidence or stale answers fall back without blocking gameplay.
- Model attack/defend/regroup choices gate the existing army graph. First attack
  still requires four ready Zealots. Conflicting tasks cannot retry after an
  explicit intent switch. Defense choices exclude abandoned/unreachable targets.
- Existing diagnostic events preserve provider, requested and returned model,
  current intent, last answer, probabilities, latency, request IDs and token usage.
- Dashboard distinguishes model decisions, scripted fallback, pending requests
  and last-known evidence. Older runs remain readable.

## Final checks

| Command/check | Result |
|---|---|
| `uv run pytest -m "not sc2" -q --disable-warnings --tb=short` | **2774 passed, 3 skipped, 2 deselected**, 242.25 seconds; includes installed-wheel validation |
| `uv run pytest tests/test_jev_decision.py -q --disable-warnings --tb=short` | **30 passed** before final full suite |
| `uv run ruff check .` | Passed |
| `uv run mypy src bots --strict` | Passed, 822 source files |
| `uv run python -m bots.jev.v1 --validate-policy` | Passed, 84 nodes |
| `npm run test:run` in `frontend/` | **283 passed, 6 skipped** |
| `npm run lint` in `frontend/` | Zero errors; existing unused eslint-disable warning in `useAlerts.ts:149` |
| `npm run build` in `frontend/` | Passed; bundle-size advisory remains |
| `git diff --check` | Passed |
| Independent code review and focused delta reviews | [PASS](jev-typesafe-code-review.md), all three findings resolved |

Final policy hash:
`510a72dfb167b7200123cbc8436c912f02d3b0fb5798657db3c62e3f5f8f68ee`.

The first targeted run found an eager httpx import broke dependency-free packaged
policy validation. The import is now lazy; the final full suite includes that
installed-wheel regression. The full suite was repeated after final eligibility
fixes and added regressions; frontend results were reused because its code had
not changed.

## Browser and boundary evidence

Real Chromium smoke used the production runtime and recorder, then the actual
FastAPI Jev router via TestClient-backed browser route interception. It verified
live fallback and an archived model decision, graph visibility, model/source
labels and last-known status. All fixture API responses were HTTP 200; no browser
console errors. Existing user servers were neither stopped nor restarted.

Local evidence (git-ignored, intentionally synthetic):

- [Browser log](../../data/jev/evidence/typesafe-browser/20261008-100006/smoke-log.json)
- [Live fallback screenshot](../../data/jev/evidence/typesafe-browser/20261008-100006/screenshots/fallback-live.png)
- [Archived model screenshot](../../data/jev/evidence/typesafe-browser/20261008-100006/screenshots/typesafe-archived.png)
- [Full backend log](../../data/jev-typesafe-pytest-final.log)
- [Frontend log](../../data/jev-typesafe-vitest.log)

The runner integration test uses the real CLI, Typesafe client and response parser
over a mock HTTP transport, then drives controller steps, checks a graph-attributed
army command and reads recorded evidence. Synthetic response models in these
tests/screenshots are not actual hosted model versions.

## Remaining live gate

Configure `TYPESAFE_API_KEY` locally, then follow
[operator guide section 13](../operator/jev-validation.md#13-typesafe-jev-integration-phase-ji).
It covers a realtime smoke after the army becomes eligible, accepted response to
command evidence, stop during a pending request, and a full match. Real service
access, latency, strategic performance and cost remain unmeasured.

Changes are not committed or merged by this task. Existing unrelated
`.claude/task-state/current.md` and `dev.code-workspace` edits were left intact.


## Live acceptance ? 2026-10-08

Step 211 passed after account funding and local credential entry. The earlier
no-live-service statements above describe the offline checkpoint.

- Actual service probe: returned `jev-1.13.0` for `jev-latest`, 840 ms,
  494 input / 33 output tokens.
- Full realtime match: `aa57512dd51745c2bcf7fb47e2c15f23`, Simple64,
  Terran difficulty 1, seed 1. **Win at 317.54 game seconds**, 64 accepted
  commands, zero rejected. Replay saved; disk/API verifier passed with all
  2,348 trace events retained.
- Policy SHA-256: `510a72dfb167b7200123cbc8436c912f02d3b0fb5798657db3c62e3f5f8f68ee`.
- 61 model calls; 124,595 input / 2,029 output tokens. Of 61 responses,
  57 were initially accepted and 4 initially stale (6.6%). This counts unique
  answer request IDs, not repeated diagnostic samples. Later state changes can
  also invalidate an initially accepted answer. Median latency 416.1 ms;
  range 364.0?838.4 ms. No authentication or request failures observed.
- Accepted model evidence spans game seconds 190.54?317.54. Six issued army
  commands correlate with an applied Typesafe attack choice, including sequence
  27705, task `aa57512dd51745c2bcf7fb47e2c15f23:36`, request 21,
  node `army.attack.go`. This proves the connection, not superiority over the
  scripted strategy; the model often agreed with its attack intent.
- Real Chromium against the running dashboard/API passed during gameplay and
  after completion, with no console errors. Screenshots show live Typesafe
  source and archived last-known evidence; no intercepted API fixtures.
- Pending-request cleanup: separate accelerated real-SC2 run
  `068948b1b0d346f6a350fe3d4fb604ab` stopped at 187.86 game seconds.
  A diagnostic launcher called the production `request_stop()` path while its
  first actual provider task was pending. Task cancelled and cleared, expected
  runner exit 130, replay saved, terminal status stopped, disk/API verifier PASS.
  Physical keyboard Ctrl+C delivery was not tested. PowerShell treated the
  runner's expected stderr stop message as a shell error; the recorded runner
  outcome and cancellation proof both passed.

Local evidence: `data/jev/evidence/typesafe-setup/`: `api-probe.json`,
`model-trace-summary.json`, `verify-final.json`, `verify-stop.json`,
`stop-proof.json`, `live-131609.png` (live), `live-131919.png` (archive),
and associated logs/browser reports. Run archives include replays.

Findings: no functional blocker observed. Staleness from changing army membership
is expected fallback behavior; cadence and freshness tuning remain future work.
One easy-opponent match does not establish a win rate. Evolution and broader
model control remain deferred. This does not complete Phase JV's separate
multi-match acceptance requirements. No merge or release performed.

The credential is Windows-DPAPI encrypted under the current account at
`%LOCALAPPDATA%\Alpha4Gate\typesafe-key.dpapi`; only child gameplay processes
received its plaintext environment value. No key was written to the repository.
See the operator guide for loading this local credential into a future session.
