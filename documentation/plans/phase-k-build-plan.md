# Phase K — Observable pool organisation + metadata (build plan)

## 1. What this phase ships

Searchable metadata on the version registry so Phase L (exhibition
viewer) and Phase M (NL-prompt seed selector) have something to query.
Today's registry stores manifest lineage + git SHA + fingerprints; it
has no themed labels, no notable-moments record, no build-order
signature. This phase adds those fields, exposes them via a new API
endpoint, and renders them in a dashboard view.

This is the foundation of the **Observable Stack** — without metadata,
the only way to pick a matchup is "random" or "by version number,"
neither of which produces interesting exhibits.

## 2. Existing context

**Code refs.**

- `src/orchestrator/registry.py` — current registry. `list_versions()`,
  `get_version_dir()`, `get_manifest()`. Manifest fields: version,
  parent, git_sha, fingerprints, lineage. No themed labels, no
  build-order signature, no notable-moments.
- `bots/<version>/manifest.json` — per-version manifest file. Owned
  by `snapshot_current()`.
- `src/orchestrator/ladder.py` — Elo ladder. Cross-version WR matrix
  already exists here (`GET /api/ladder` returns it); Phase K wires
  it into the metadata view, no recomputation.
- `frontend/src/components/dashboard/LadderTab.tsx` — existing 10th
  dashboard tab with standings + head-to-head grid. Phase K either
  adds an 11th tab ("Pool") or extends LadderTab.

**Memory refs.**

- `feedback_useapi_cache_schema_break.md` — bump `cacheKey` whenever
  response shape changes. Applies to any new useApi consumer.
- `feedback_per_version_vs_cross_version_data_dir.md` — pool metadata
  is **cross-version** state; must NOT live under `bots/<version>/`.
  Lives at `data/pool_metadata.json` (or a SQLite table) at repo
  root.

## 3. Scope

**In scope.**

- New cross-version metadata file: `data/pool_metadata.json`. Schema
  per §5.
- Manifest extension: add `themes: list[str]` and `notable_moments:
  list[str]` fields. Backwards-compat default `[]`.
- Build-order signature: capture top-3 unit-production-order from
  the first 5 minutes of a representative game per version.
  Computation lives in `src/orchestrator/build_order_signature.py`.
- `GET /api/pool/metadata` endpoint returning the merged
  registry+ladder+metadata view.
- Dashboard surface: extend LadderTab with a per-version-card view OR
  add a new Pool tab. Decision deferred to step K.4.
- Auto-tagging fallback: when a new version snapshots without explicit
  themes, infer 1-2 themes from build-order signature (e.g., "≥3 Stargate
  in first 5 min" → `skytoss`). Lives in
  `src/orchestrator/auto_theme.py`.

**Out of scope.**

- NL-prompt selector (Phase M).
- Live exhibition viewer (Phase L).
- Cross-version reward schema or other capability changes (other
  tracks).

## 4. Build steps

### Step K.1: Pool metadata schema + file

- **Issue:** #223
- **Problem:** Define `data/pool_metadata.json` schema (§5). Write the
  schema as Python TypedDict in `src/orchestrator/pool_metadata.py`.
  Add load/save helpers with file-locking (concurrent evolve runs
  could write simultaneously).
- **Type:** code
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** `src/orchestrator/pool_metadata.py`,
  `tests/test_pool_metadata.py`, `data/pool_metadata.json` (empty
  initial).
- **Done when:** Round-trip load/save tests pass; file-locking test
  passes (two writers serialize correctly); mypy strict + ruff
  clean.

### Step K.2: Manifest extension

- **Issue:** #224
- **Problem:** Add `themes` and `notable_moments` to manifest schema.
  `snapshot_current()` writes `themes: []` and `notable_moments: []`
  by default. Add `set_themes()` and `add_notable_moment()` helpers
  in `registry.py`.
- **Type:** code
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** Updates to `src/orchestrator/registry.py`,
  `src/orchestrator/snapshot.py`, `tests/test_registry.py`,
  `tests/test_snapshot.py`.
- **Done when:** Snapshotting `v0 → v1` produces a manifest with the
  new fields; reading an OLD manifest (no `themes` field) doesn't
  crash (default `[]`).
- **Depends on:** K.1.

### Step K.3: Build-order signature

- **Issue:** #225
- **Problem:** `extract_build_order_signature(game_id) ->
  list[tuple[str, int]]` — returns the first 5 minutes of unit/structure
  production as `[(unit_name, count), ...]` ranked by frequency. Read
  from `transitions` or `decision_audit.json`. Auto-theme function:
  `infer_themes(signature) -> list[str]` with 3-5 hardcoded rules
  (skytoss, ground, cheese-rush, defensive, tech-rush).
- **Type:** code
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** `src/orchestrator/build_order_signature.py`,
  `src/orchestrator/auto_theme.py`,
  `tests/test_build_order_signature.py`,
  `tests/test_auto_theme.py`.
- **Done when:** Signature extraction tests pass on a fixture game
  ID; auto-theme produces sensible labels on 5 hand-crafted
  signatures.
- **Depends on:** K.2.

### Step K.4: API endpoint + dashboard surface

- **Issue:** #226
- **Problem:** `GET /api/pool/metadata` returns merged
  registry+ladder+metadata view. Decide tab-vs-extend during this
  step (read LadderTab; if extension is clean, extend; otherwise
  new Pool tab as 11th tab). Frontend hook `usePoolMetadata.ts` with
  `cacheKey: 'pool-v1'`.
- **Type:** code
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** Backend endpoint, `frontend/src/hooks/usePoolMetadata.ts`,
  dashboard surface (tab or extension).
- **Done when:** Endpoint returns valid JSON for `v0`-only pool;
  frontend renders without console errors;
  `npm --prefix frontend run test` passes; `npm --prefix frontend
  run build` passes.
- **Depends on:** K.3.

### Step K.5: Operator smoke gate

- **Issue:** #227
- **Problem:** Run `python -m orchestrator list`, verify themes +
  notable_moments fields surface; manually tag `v0` with
  `["baseline"]` theme via `set_themes("v0",
  ["baseline"])`; verify dashboard updates; verify NL-prompt-shaped
  query (`themes contains "baseline"`) returns `v0`. No real
  matchups yet — that's Phase L.
- **Type:** operator
- **Produces:** Manual verification.
- **Done when:** All smoke checks green.
- **Depends on:** K.4.

## 5. Pool metadata schema

```python
class PoolMetadata(TypedDict):
    version: str
    themes: list[str]                # ["skytoss", "anti-air-heavy"]
    notable_moments: list[str]       # ["first promotion 2026-04-19", "beat-v0-9-0"]
    build_order_signature: list[tuple[str, int]]
    created_at: str                  # ISO timestamp
    inferred_themes: list[str]       # auto-tagged, separate from explicit themes
```

`data/pool_metadata.json` is `{"versions": {"v0": PoolMetadata, "v1": ...}}`.

## 6. Tests

- `tests/test_pool_metadata.py` — schema, file-locking, load/save.
- `tests/test_build_order_signature.py` — extraction from fixture
  games.
- `tests/test_auto_theme.py` — rule-based theme inference.
- `tests/test_registry.py` (extended) — manifest backwards-compat.
- `tests/test_snapshot.py` (extended) — new fields populated.
- `frontend/src/hooks/usePoolMetadata.test.ts` — hook + cache key.

## 7. Effort

~1-2 days. Schema + manifest extension is ~0.5 day; build-order
signature + auto-theme is ~0.5 day; API + dashboard surface is
~0.5-1 day.

## 8. Validation

- Smoke gate K.5 passes end-to-end.
- A pool-pick API call (`/api/pool/metadata?themes=baseline`)
  returns a sensible matchup given text-or-tag input. Stub for
  Phase M but observable as JSON now.

## 9. Gate

K.5 smoke gate green AND no regression on existing 10 dashboard tabs
(LadderTab still renders; api/ladder unaffected).

## 10. Kill criterion

Registry metadata gets stale faster than it gets useful — concretely:
within 5 promoted versions, more than 50% of versions land without
explicit themes (auto-tag rate < 50%) AND the surface is unused.
Switch to **auto-tagging only**, drop the explicit-themes API surface,
collapse Phase K to a 1-step migration.

## 11. Rollback

`git revert` the build-step commits; `data/pool_metadata.json` stays
on disk (cheap; ignored by future code paths if the module is gone).
Manifest fields stay (backwards-compat default `[]` was always there).

## 12. Cross-references

- Master plan Phase K pointer: `documentation/plans/alpha4gate-master-plan.md`
- Phase L (viewer) — depends on this phase
- Phase M (NL-prompt selector) — depends on this phase + Phase L
- Existing Ladder tab: `frontend/src/components/dashboard/LadderTab.tsx`
