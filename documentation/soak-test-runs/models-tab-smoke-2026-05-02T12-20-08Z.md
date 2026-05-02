# Models tab smoke gate — 2026-05-02T12-20-08Z

Plan: documentation/plans/models-tab-plan.md §7 Step 11.

- Backend: http://localhost:8766
- Repo root (data dir source): /c/Users/abero/dev/Alpha4Gate
- Version under test: v3
- Recent game id (auto-picked from training.db): 83ffe7faf71f

## Result: **PASS** (16/16 checks passed, 28s wall-clock (under 60s ceiling))

## Endpoint + assertion checks

| Check | Status | Latency (ms) | Size (bytes) | Detail |
|---|---|---|---|---|
| /api/versions | PASS | 214 | 3004 | list of versions |
| /api/lineage | PASS | 208 | 2654 | lineage DAG returned |
| /api/runs/active | PASS | 254 | 2 | list (may be empty) |
| /api/versions/v3/training-history (PER-VERSION) | PASS | 279 | 9713 | rolling windows populated |
| /api/versions/v3/actions | PASS | 243 | 426 | histogram returned |
| /api/versions/v3/improvements (CROSS-VERSION) | PASS | 217 | 2 | list (may be empty) |
| /api/versions/v3/config | PASS | 221 | 16032 | 3-key object |
| /api/versions/v3/weight-dynamics | PASS | 216 | 3235 | rows present |
| /api/versions/v3/forensics/83ffe7faf71f | PASS | 238 | 10113 | trajectory shape returned |
| resolver-mix (per-version + cross-version both exercised) | PASS |  |  | training-history (per) + improvements (cross) both 200 above |
| lazy-init self-rebuild | PASS | 675 | 2654 | lineage rebuilt with 11 nodes |
| reject /api/versions/v3@bad/config | PASS | 213 | 57 | HTTP 400 as expected |
| reject /api/versions/v3/forensics/bad@id | PASS | 209 | 72 | HTTP 400 as expected |
| /api/ladder (CROSS-VERSION; Compare data source) | PASS | 217 | 34 | ladder shape returned (may be empty) |
| /api/versions/v4/config (Compare B-side resolver) | PASS | 222 | 16032 | 3-key object (B-side) |
| wall-clock budget | PASS | 28000 |  | elapsed 28s within 60s ceiling |

## Coverage notes

- **Per-version resolver exercised:** `training-history`, `actions`, `config`, `weight-dynamics`, `forensics` all read from `bots/v3/data/`.
- **Cross-version resolver exercised:** `lineage`, `runs/active`, `improvements`, `ladder` all read from `data/`.
- **Cross-version compare proof:** TWO distinct version configs (`v3` + `v4`) both resolved with the same 3-key shape, proving the per-version resolver isolates per name (Compare's contract).
- **Lazy-init self-rebuild:** `lineage.json` was moved aside before the `/api/lineage` request and the response still had >=10 nodes, proving `_run_build_lineage_sync` was triggered.
- **Input validation:** both `/api/versions/v3@bad/config` and `/api/versions/v3/forensics/bad@id` returned HTTP 400 (per `_validate_version` / `_validate_game_id` in `bots/v10/api.py`).
- **Wall-clock budget:** recorded as a hard FAIL when elapsed > 60s; the gate exits non-zero on overrun.

## Manual verification — SKILL.md hook (improve-bot-advised)

The plan §7 Step 11 explicitly carves this out from the automated gate
(Claude follows the SKILL.md instruction non-deterministically). The
operator should run this once after a SKILL.md change to confirm the
post-iteration hook fires.

Procedure:

1. Note current `data/lineage.json` mtime (`stat -c %Y data/lineage.json` on
   bash, or `Get-Item data/lineage.json | Select LastWriteTime` on PowerShell).
2. Run a single advised iteration via `/improve-bot-advised` (a single
   dev-cycle suffices).
3. Within ~5s of the iteration commit landing, `data/lineage.json` mtime
   should advance — the SKILL.md instruction calls `scripts/build_lineage.py`.

Last verified by [operator] on [YYYY-MM-DD]: ___________

Result: [PASS / FAIL / NOT YET VERIFIED]
