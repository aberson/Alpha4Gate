# VW.0 draft validation receipt

Date: 2026-11-16. Scope: planning and existing reuse seams. No candidate game, native
window-host launch, training, model call or implementation was performed.

| Check | Exact command / selected files | Result |
|---|---|---|
| Existing Python reuse tests | `.venv/Scripts/python.exe -m pytest tests/test_overlay.py tests/test_reparent_portable.py tests/test_web_socket.py tests/test_audit_log.py -q -p no:cacheprovider --basetemp $env:TEMP/alpha4gate-direction-viewer-pytest` | Exit 0; **50 passed in 3.82s** |
| Existing Jev page tests, from `frontend/` | `npm run test:run -- src/components/JevBattleView.test.tsx` | Exit 0; **1 file, 10 tests passed**, duration 12.29s |
| Frontend compile/type check without emit | `frontend/node_modules/.bin/tsc.cmd -p frontend/tsconfig.app.json --noEmit --incremental false` | Exit 0, no diagnostics |
| Python in-memory compilation | `compile(Path(name).read_bytes(), name, 'exec')` for the ten files below | Exit 0; 10 files compiled without imports or bytecode writes |
| Quick repository invariant (shared same-session check) | `tests/test_repo_hygiene.py` included in the earlier PPO validation command | Exit 0; complete 36-test selection/result in [PPO receipt](ppo-validation-2026-11-16.md); not rerun |
| Scoped patch whitespace | `git diff --check -- docs/seeds/ppo-direction.md docs/seeds/strength-viewer.md` | Exit 0; only Git LF-to-CRLF advisories |

Compiled sources: `src/selfplay_viewer/container.py`, `overlay.py`, `reparent.py`;
`src/jev/launch.py`; `scripts/probes/jev_battle_view.py`; `src/orchestrator/selfplay.py`;
`scripts/selfplay.py`; `bots/v13/observer.py`, `web_socket.py`, `audit_log.py`.

The Python WS/audit suites exercise historical bot-tree modules, not an implemented new
candidate bridge. JevBattleView tests likewise prove existing Jev UI behavior, not the
proposed candidate route. TypeScript passed on the shared dirty tree as observed; no
claim is made about other lanes' changes or a clean release build. No full pytest,
Vite bundle build, lint sweep or real SC2 integration suite was run for this docs-only
draft. Native lifecycle, DPI, old-version game compatibility, new telemetry and ES.10
round-trip acceptance remain unimplemented/unverified.

Source discovery used targeted `rg` across `bots/v13`, `src`, `scripts`, `frontend/src`
and relevant tests, plus direct producer reads. The installed burnysc2 `run_game`
signature was inspected for its `random_seed` input. The draft labels new paths, public
interfaces and launch commands as proposals, retains three code slices and separates
human acceptance. It is HELD, not independently reviewed or approved for build.
