---
description: Vite + Windows dev-server traps for Alpha4Gate's frontend.
paths:
  - "frontend/**"
  - "scripts/start-dev.sh"
---

# Alpha4Gate frontend UI rules

Frontend dev server runs on **port 3000** (proxies to backend on **8765**). Pin in `frontend/vite.config.ts` via `server.port: 3000` and `server.strictPort: true` — Vite's default of 5173 will silently take over otherwise.

## Server cleanup

`/build-step --ui` leaves both backend and frontend bound on Windows. Between steps, kill anything on ports 3000 and 8765:

```powershell
Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

## IPv6-only loopback

Vite binds `::1` on Windows by default, not `127.0.0.1`. Readiness probes that hit `http://127.0.0.1:3000/` will fail. Use `http://localhost:3000/` (resolves both stacks) or pin `--host 127.0.0.1` on launch.

## Backend launch

Always launch the backend from the project root, not from a worktree:

```powershell
cd Alpha4Gate
$env:WEB_UI_PORT="8766"
uv run python -m bots.current.runner --serve
```

Worktree-launched backend dies on argparse because `sys.argv[0]` is `None`.

## E2E gating

Backend-mode e2e tests (`frontend/src/__tests__/e2e/*.test.tsx`) are gated behind `BACKEND_E2E=1`. Set it explicitly when validating Models tab and similar real-backend flows.
