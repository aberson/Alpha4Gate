# Phase EJ — Evolve judging noise-floor shortlist (5 cheapest high-value gate fixes)

**Track:** 10 (Statistical robustness / evolve substrate). **Status:** Planned 2026-07-05.
**Prerequisites:** Phase 9 (improve-bot-evolve) operational; Phase EL shipped (the panel
floor consumes EL's baseline gauntlet). No other phase gates it.

> Slots into the master plan as **Phase EJ** on Track 10, alongside Phase R. Step IDs are
> `EJ.1 … EJ.8` (letter.number form, matching Phases B/D/E/G/EL) so they never collide
> with numeric-track step numbering.
>
> **Relationship to Phase R (Wilson CIs + SPRT):** the 2026-07-05 investigation
> ([documentation/investigations/evolve-judging-alternatives.md](../investigations/evolve-judging-alternatives.md))
> partially supersedes Phase R's scope: **SPRT is rejected** for this project (its
> indifference zone has negative LLR drift at the modest true win rates ~0.6 the evolve
> loop actually produces, so it systematically kills the gains we want, at ~3× the game
> cost of the posterior rule shipped here as EJ.3). Phase R should be re-scoped after
> Phase EJ ships; its Wilson-CI reporting idea remains valid backlog.

## 1. What This Feature Does

Ships the five cheapest high-value changes from the 2026-07-05 multi-agent investigation
into `/improve-bot-evolve`'s judging steps, all flag-gated with byte-identical defaults:

1. **EJ.1 Promoted-title priors exclusion** — stop the proposal prompt re-suggesting
   improvements that are already baked into the parent (the documented
   `feedback_evolve_priors_diminishing_returns` leak: "Splash-readiness" burned ~9 games
   re-proposed against v7, which already contained it).
2. **EJ.2 Mechanical null-diff screen** — stop playing ~4 games on a candidate whose
   dev-apply produced **zero semantic change** (today a zero-edit sub-agent run is
   silently treated as success and the byte-identical candidate passes fitness ~50% of
   the time).
3. **EJ.3 One-sided posterior rollback bar** — the regression gate currently rolls back
   a truly-neutral promotion 50% of the time (strict majority on 5 games). The one-sided
   rule rolls back only on positive evidence of harm: null false-rollback 50% → 18.75%,
   improvement-kill at p=0.60 from 31.7% → 8.7%, mean regression games 4.12 → 3.50.
4. **EJ.4 Frozen-baseline panel floor** — the mandatory pairing for EJ.3: a sweep-loss
   floor against frozen anchor versions (via the Phase EL gauntlet, which today is
   log-only) so the relaxed regression bar cannot admit sustained silent drift. Frozen
   anchors cannot cycle — this is the AlphaStar max-min rationale.
5. **EJ.5 Refresh-time proposal dedup** — the pool-refresh site appends fresh imps with
   zero dedup today; add mechanical title/similarity dedup against the in-run pool.
6. **EJ.6 Budget-aware final-generation fit** — shrink the last generation's active set
   to what actually completes (fitness + stack + regression) inside the remaining
   `--hours` budget, instead of stranding up to ~25 games of half-finished work a night.

Why: the fitness gate's strict-majority bar means a **no-effect improvement passes ~61%
of the time eventually** (through the retry/bench ladder), and truly-neutral stacks get
destroyed by coin-flip rollbacks. EJ.1/EJ.2/EJ.5 cut the null inflow before games are
spent; EJ.3/EJ.4 stop the regression gate destroying whole generations on coin flips
while keeping a hard drift backstop; EJ.6 reclaims stranded wall-clock. These compound:
fewer nulls entering means the relaxed regression bar's lower catch rate matters less.

Empirical games remain the final authority for promotion throughout — nothing here adds
an LLM judge to the gate path (safety-rail constraint from the SKILL.md).

## 2. Existing Context

- **`src/orchestrator/evolve.py`** — phase primitives.
  - `_fitness_bucket` (L504-519): pass = strict majority `games // 2 + 1`, close = one
    short, else fail. Fitness early-stop (L614-646) locks the bucket once either side
    hits majority (~4.1 games mean per null eval).
  - `run_regression_eval` (L687-808): `needed = games // 2 + 1` (L746),
    `rolled_back = wins_new < needed` (L782). `_count_wins` (L450-463) excludes
    draws/crashes from both tallies, so undecided games count **against** the new parent
    (fewer decided games → majority harder to reach) — the draw/crash-forces-rollback
    bias EJ.3 fixes by conditioning on decided games.
  - `run_baseline_gauntlet` (L847-976): plays the candidate vs each registered
    `Baseline` for `games_each`, returns `GauntletResult.per_baseline` win rates +
    `mean_win_rate`. Skips a baseline whose version IS the candidate; empty registry →
    `mean_win_rate=0.0`.
  - Priors block builder `_format_priors_block` (L1456-1499): renders
    `data/evolve_favorites.json` into the prompt, including the line "You may include
    them verbatim" (L1484). `_rewrite_version_paths` (L1440-1453) retargets stale
    `bots/vN/` paths.
  - `generate_pool` retry semantics: short-pool retry **raises** on persistent shortfall
    (L1740-1744, L1760-1767) — any new mechanical filter must accept-short rather than
    re-enter this path, or an unattended soak aborts.
- **`src/orchestrator/evolve_dev_apply.py`** — dev-apply sub-agent loop. Zero-edit runs
  are treated as SUCCESS: `if not validate or not changed_py: return` (L309-313, comment
  "the sub-agent made no .py edits … Treat as success; the round will play as-is").
  `changed_py` is a content-snapshot diff (L300), so comment-only edits count as
  "changed". Ruff/mypy validate changed files only (L316-317).
- **`scripts/evolve.py`** — the generation loop.
  - Priors auto-load: defaults to `data/evolve_favorites.json` when present
    (L3274-3278); `--priors-path` flag (L252-261). That file EXISTS on this machine, so
    priors are active on every run today.
  - Fresh runs truncate `data/evolve_results.jsonl` (`_clear_fresh_run_state`,
    L1308-1324) — favorites mined per-run lose history; exclusion lists must merge
    cumulatively.
  - Fitness crash handler (L2932-2958) flips the imp to `_EVICTED` and increments
    `retry_count` — the null-diff outcome must NOT reuse this path unchanged.
  - Wall-clock: `_budget_exceeded` (L1274-1283); mid-fitness break (L3542-3549) strands
    the generation when the budget dies mid-pool.
  - Gauntlet is log-only today: runs post-promotion when `--fitness-mode` ∈
    {baseline, both} and baselines are registered, with an explicit comment "The
    gauntlet does NOT change the promotion gate in this step — it LOGS + records … so a
    later step can drive gating off it" (L3825-3832). EJ.4 is that later step.
  - Regression rollback consumption (L4030-4063): `git revert` first on a clean tree,
    pointer-restore fallback.
  - Pool refresh (L4077-4132): `generate_pool(skip_mirror=True)` for the delta, appends
    with **zero dedup** (L4128-4132).
- **`scripts/curate_evolve_favorites.py`** (135 lines, read in full) — mines
  `evolve_results.jsonl` for fitness-pass imps (L52-74), annotates
  `stack_apply_observations` (L76-90), dedupes by exact title (L39-40), writes
  `data/evolve_favorites.json`. It records promotion evidence but never filters on it.
- **`data/baselines.json` is ABSENT** (checked 2026-07-05 — the EL.6 smoke cleaned up
  its seeds), and default `--fitness-mode` is `parent`, so the gauntlet path is inert
  until an operator registers anchors (EJ.7 does this).
- **Conventions that bind this phase:** flag-gated byte-identical defaults (Phase EL
  precedent: `--lineages 1` / `--fitness-mode parent` / `--population-cap 0`); evolve
  runs are unattended overnight (no mid-run prompts, accept-short over raise);
  `frontend/src/hooks/useEvolveRun.ts` cacheKey must bump if the results-row schema the
  dashboard parses changes shape (`feedback_useapi_cache_schema_break`).

## 3. Scope

**In:**
- The six code changes above (EJ.1–EJ.6), each behind its own flag, defaults
  byte-identical.
- A closed-form posterior helper in a new leaf module (no scipy — binomial-tail
  identity via `math.comb`).
- Curator upgrades (`--exclude-promoted`, `--merge-existing`).
- SKILL.md flag documentation.
- A flags-on real-SC2 smoke gate (EJ.7) and one overnight flags-on A/B soak (EJ.8).

**Out (explicitly):**
- Flipping any default ON — that is a post-EJ.8 operator decision.
- The binomial-alpha fitness bar (`--fitness-alpha`, investigation fitness-A1) — next
  candidate after this phase, deliberately excluded to keep this phase to the shortlist.
- SPRT anywhere (rejected — see Phase R note above).
- LLM/local-model judges in any gate path (switchboard local_judge pre-filters scored
  6/10 — deferred).
- Winprob-trajectory triage, patch capture/replay, sequential promotion, A/A calibration
  harness — all investigation follow-ups, not shortlist items.
- Phase R re-scope itself (one-line pointer added to master plan only).

## 4. Impact Analysis

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `scripts/curate_evolve_favorites.py` | extend | `--exclude-promoted` (drop favorites whose imp was stacked in a stack-apply-pass generation whose regression passed) + `--merge-existing` (merge track records with the existing favorites file since results are truncated per run) | read in full (135 lines); mines only `phase=="fitness" && outcome=="fitness-pass"` rows (L52-74); `stack_apply_observations` recorded L76-90 but never filtered on; results truncation confirmed at scripts/evolve.py L1308-1324 |
| `src/orchestrator/evolve.py` | extend | (a) `_format_priors_block`: accept an exclusion set, drop excluded titles, replace the "You may include them verbatim" line (L1484) with anti-verbatim wording; (b) `generate_pool`: new optional `exclude_titles` kwarg threaded to the priors block (default None = byte-identical); (c) `run_regression_eval`: new `rule: str = "majority"` param implementing the one-sided posterior bar with rule-derived early-stop | priors block read L1456-1499; generate_pool retry-raise hazard confirmed L1740-1744 + L1760-1767 (mechanical filters must accept-short); run_regression_eval read in full L687-808; grep'd callers of `run_regression_eval`: scripts/evolve.py (1 call site) + tests — no other consumers |
| `src/orchestrator/gate_stats.py` | new | leaf module: `posterior_prob_worse(wins, losses)` (Beta(1+w,1+l) tail at 0.5 via binomial-tail identity, `math.comb`, no scipy) + `one_sided_rollback(wins_new, wins_prior, min_decided=4, threshold=0.85)` — single source of truth for both the gate and its tests | new file; one-source-of-truth rule (`.claude/rules/code-quality.md`); Phase R can extend this module later |
| `src/orchestrator/evolve_dev_apply.py` | extend | flag-driven null-diff detection: when `changed_py` is empty OR every changed file is AST-equivalent to its before-snapshot, retry with feedback; on final attempt raise new `DevApplyNullDiffError` instead of silent success (L309-313) | zero-edit-as-success read at L300-324; ruff/mypy changed-files-only at L316-317; existing retry-with-feedback machinery at L325-339 is reusable |
| `scripts/evolve.py` | extend | six new flags (`--priors-exclude-promoted`, `--screen-null-diff`, `--regression-rule {majority,one-sided}`, `--panel-floor`, `--refresh-dedup`, `--budget-fit`); catch `DevApplyNullDiffError` → `screen-null-diff` results row, no retry increment, evict on 2nd consecutive; thread `rule` into `run_regression_eval`; consume `GauntletResult.per_baseline` as a sweep-loss floor in the rollback decision; title-normalize + difflib dedup at the refresh append; trim active set to remaining budget before fitness dispatch | argparse surface grep'd (build_parser L132-339; `--priors-path` L252, `--fitness-mode` L295); crash-handler semantics L2932-2958; gauntlet log-only block L3825-3923; rollback consumption L4030-4063; refresh append L4128-4132; budget check L1274-1283 + mid-fitness break L3542-3549; priors auto-load L3274-3278 |
| `scripts/evolve_worker.py` | modify | propagate `DevApplyNullDiffError` across the concurrency>1 worker boundary (serialize error type so the parent dispatcher can emit `screen-null-diff` instead of a generic crash) | file exists (glob); exact serialization shape to be read during EJ.2 — flagged in Risks |
| `.claude/skills/improve-bot-evolve/SKILL.md` | modify | document the six flags in the Flags table + a "pairing" warning that `--regression-rule one-sided` should not run without `--panel-floor` in production soaks | read in full this session; Flags table at SKILL.md §Flags |
| `frontend/src/hooks/useEvolveRun.ts` | possibly modify | new results-row `phase`/`outcome` string values (`screen-null-diff`, `pool_dedup`, `budget_fit`, `panel-floor-rollback`); bump `cacheKey` only if the hook enumerates outcome values strictly | to verify during EJ.2/EJ.5 — grep whether outcome strings are enumerated or passed through; `feedback_useapi_cache_schema_break` applies |
| `tests/test_evolve.py`, `tests/test_evolve_cli.py`, new `tests/test_gate_stats.py`, new `tests/test_curate_favorites.py` | extend/new | per-step unit + integration tests; byte-identical-default regression tests | existing test files grep'd; 1799-test baseline per CLAUDE.md |

**Shared-file constraint:** EJ.1, EJ.2, EJ.3, EJ.5, EJ.6 all touch `scripts/evolve.py`
(argparse + loop). Run the steps **serially** (no parallel worktrees) to avoid surgical-
edit merge conflicts.

## 5. New Components

- **`src/orchestrator/gate_stats.py`** — pure-function statistics leaf module.
  `posterior_prob_worse(wins: int, losses: int) -> float` returns
  P(p < 0.5 | Beta(1+wins, 1+losses)) using the exact identity
  I_0.5(a, b) = Σ_{k=a}^{a+b-1} C(a+b-1, k) · 0.5^(a+b-1) (integer params, `math.comb`,
  no new deps). `one_sided_rollback(wins_new, wins_prior, *, min_decided=4,
  threshold=0.85) -> tuple[bool, str]` returns (rollback?, reason). At n=5 decided this
  yields rollback only on 0-5 (P≈0.984) and 1-4 (P≈0.891); 2-3 (P≈0.656) keeps.
  Fewer than `min_decided` decided games → keep (fail-open) with an explanatory reason.
- **`DevApplyNullDiffError`** (in `evolve_dev_apply.py`) — terminal signal that the
  sub-agent produced no semantic change after retries; carries the attempt count and
  whether the null was zero-edit or AST-equivalent.
- **`PerItemState.consecutive_null_diffs`** (additive optional field, default 0) —
  livelock guard counter for the null-diff screen.
- **New results-row values** — `outcome: "screen-null-diff"` (phase `fitness`),
  `phase: "pool_dedup"`, `phase: "budget_fit"`, and rollback reason prefix
  `panel-floor:` — all additive rows in `evolve_results.jsonl`.

## 6. Design Decisions

**Flag-gated, byte-identical defaults (all six flags).** Phase EL precedent. With every
flag at its default, every code path taken today is taken unchanged — enforced by
regression tests, not just review. Defaults flip only after EJ.8's A/B soak, as an
operator decision outside this phase.

**One-sided posterior instead of SPRT (and instead of a tighter symmetric bar).** The
regression gate is a safety net downstream of a fitness gate that already produced
per-imp evidence; rollback should therefore require positive evidence of harm, not mere
absence of a majority. The posterior rule has exact closed-form error control
(null false-rollback 18.75% at n=5, improvement-kill 8.7% at p=0.60) and *reduces* games
(mean 4.12 → 3.50 via rule-derived early-stop). SPRT was adversarially rejected: at the
modest true win rates this loop produces (~0.55–0.65), the Wald LLR drifts negative
inside the indifference zone and kills real gains at ~3× the game cost. The honest cost:
regression catch at p=0.35 drops 76.5% → 42.8% — which is why EJ.4 is mandatory pairing,
not an optional extra.

**Panel floor = consume the existing gauntlet, don't add games.** Under
`--fitness-mode both` the EL gauntlet already plays the promoted version vs each frozen
anchor and throws the result away (log-only by design, scripts/evolve.py L3825-3832).
EJ.4 makes a sweep loss (0 wins vs ANY anchor) trigger the existing rollback machinery.
Joint null false-positive with the posterior bar ≈ 26% at K=3 anchors (each anchor
sweep-loss ≈ 3.1% under fair coin, but anchors are frozen so sustained drift cannot
cycle past them). Fail-open on gauntlet crash or empty registry — a monitoring layer
must never block an already-committed promotion on its own failure.

**Null-diff semantics: retry, then screen; never silently succeed.** Zero-edit is
currently success-by-omission (evolve_dev_apply.py L309-313). With `--screen-null-diff`:
null diffs get the existing retry-with-feedback treatment ("your edit produced no
semantic change — make the concrete change or fail loudly"); a still-null final attempt
raises `DevApplyNullDiffError`, which the loop records as `screen-null-diff` WITHOUT
incrementing `retry_count` (the imp goes back to active — the sub-agent is
non-deterministic and may land the edit next generation). Livelock guard: 2 consecutive
null-diffs → evict. AST-equivalence (not just zero-edit) is load-bearing: comment-only
and formatting-only edits must count as null or the screen is trivially evaded.

**Dedup is mechanical, not LLM.** Exact match on normalized titles (casefold, strip
punctuation) against in-run promoted titles, plus `difflib.SequenceMatcher` ratio ≥ 0.85
against all current pool titles. stdlib-only, deterministic, auditable (`pool_dedup`
rows). The local_judge semantic pre-filter scored 6/10 in the investigation and is
deferred. Accept-short after dedup — never re-call `generate_pool` to top up a deduped
batch (the short-pool retry path raises on persistent shortfall, evolve.py L1740-1744,
which would abort an unattended soak).

**Promoted-exclusion defined by promotion survival, not fitness-pass.** A favorite is
excluded only if it was stacked in a stack-apply-pass generation whose regression
passed (correlate `stack_apply` and `regression` rows by generation index). Non-promoted
fitness-passers stay in the priors — re-proposing those is legitimate (resurrection).
In-run promotions are excluded at refresh time via the `exclude_titles` kwarg —
this is exactly the Splash-readiness case (promoted mid-run, re-proposed same run).

**Budget fit trims before dispatch, never below 1.** Per-eval cost is estimated from
this run's own completed evals (mean wall-clock per fitness eval); reserve = one
regression (`games_per_eval`) + gauntlet cost when enabled. First generation has no
observed data → no trim (fit engages from generation 2). A trimmed generation completes
end-to-end instead of dying mid-fitness at L3542-3549 with stranded results.

## 7. Build Steps

### Step EJ.1: Promoted-title priors exclusion + prompt fix
- **Problem:** The priors pipeline re-proposes already-promoted improvements: the curator (`scripts/curate_evolve_favorites.py`) records promotion evidence (`stack_apply_observations`, L76-90) but never filters on it; priors auto-load whenever `data/evolve_favorites.json` exists (scripts/evolve.py L3274-3278); and the prompt explicitly invites "You may include them verbatim" (src/orchestrator/evolve.py L1484). Add: (a) curator `--exclude-promoted` (drop favorites stacked in a stack-apply-pass generation whose regression passed) and `--merge-existing` (cumulative merge with the existing favorites file, since `evolve_results.jsonl` is truncated per fresh run at scripts/evolve.py L1308-1324); (b) `generate_pool(exclude_titles=...)` kwarg (default None = byte-identical) that drops excluded titles from the priors block; (c) scripts/evolve.py `--priors-exclude-promoted` flag that accumulates in-run promoted titles and passes them at pool refresh (L4116-4118); (d) replace the L1484 prompt line with anti-verbatim wording for the remaining entries ("refine or supersede; do not re-propose verbatim — verbatim re-proposals of promoted work are no-ops"). All mechanical drops must accept-short, never re-raise through the short-pool retry path (evolve.py L1740-1744).
- **Type:** code
- **Issue:** #282
- **Flags:** --reviewers code
- **Produces:** extended `scripts/curate_evolve_favorites.py`; `exclude_titles` kwarg in `generate_pool` + `_format_priors_block`; `--priors-exclude-promoted` flag; new `tests/test_curate_favorites.py`; extended `tests/test_evolve.py` prompt-assembly cases
- **Done when:** unit tests cover: promoted favorite excluded / non-promoted fitness-passer retained / cumulative merge preserves prior-run observations / `exclude_titles=None` produces a prompt byte-identical to today EXCEPT the deliberately reworded L1484 line (the one default-visible change in this phase — golden update called out in the diff) / in-run promoted title absent from the refresh prompt. `uv run pytest`, `uv run mypy src --strict`, `uv run ruff check .` clean; test count ≥ baseline.
- **Depends on:** none
- **Status:** DONE (2026-07-06)

### Step EJ.2: Mechanical null-diff screen
- **Problem:** A dev-apply sub-agent run that makes zero `.py` edits is treated as SUCCESS (`if not validate or not changed_py: return`, src/orchestrator/evolve_dev_apply.py L309-313) — the byte-identical candidate then burns ~4 games and passes fitness ~50% of the time. Behind `--screen-null-diff`: detect null diffs (empty `changed_py` OR every changed file AST-equivalent to its before-snapshot — comment/formatting-only edits must count as null); route them through the existing retry-with-feedback machinery (L325-339) with a "no semantic change" feedback message; on final-attempt null raise `DevApplyNullDiffError`. In scripts/evolve.py, catch it (both the serial path and the `--concurrency`>1 worker path via `scripts/evolve_worker.py`) and emit a `phase: "fitness", outcome: "screen-null-diff"` results row WITHOUT playing games and WITHOUT incrementing `retry_count` (do not reuse the crash handler at L2932-2958 unchanged — it evicts and increments); imp returns to active; evict after 2 consecutive null-diffs via a new additive `PerItemState.consecutive_null_diffs` field. Verify whether `frontend/src/hooks/useEvolveRun.ts` enumerates outcome strings; bump `cacheKey` if so.
- **Type:** code
- **Issue:** #283
- **Flags:** --reviewers code
- **Produces:** `DevApplyNullDiffError` + AST-equivalence check in `evolve_dev_apply.py`; `--screen-null-diff` flag + outcome handling in `scripts/evolve.py` + worker propagation in `scripts/evolve_worker.py`; `PerItemState.consecutive_null_diffs`; tests
- **Done when:** unit tests cover: zero-edit → retry feedback → error; comment-only edit classified null; real edit passes through unchanged; screen outcome does not increment `retry_count`; 2nd consecutive null evicts; flag OFF → today's silent-success path byte-identical; worker path propagates the error type. Integration test drives the scripts/evolve.py loop (fake `run_batch`/claude fns, real dispatch code) with the flag on and asserts a null-diff imp produces a `screen-null-diff` row and zero games. Gates clean.
- **Depends on:** none
- **Status:** DONE (2026-07-06)

### Step EJ.3: One-sided posterior rollback bar
- **Problem:** `run_regression_eval` rolls back on `wins_new < games // 2 + 1` (src/orchestrator/evolve.py L746, L782) — a truly-neutral promotion is destroyed 50% of the time at n=5, and draws/crashes count against the new parent because `_count_wins` (L450-463) shrinks the decided count while the majority bar stays at 3. Build `src/orchestrator/gate_stats.py` (`posterior_prob_worse` via the exact binomial-tail identity with `math.comb`; `one_sided_rollback(wins_new, wins_prior, min_decided=4, threshold=0.85)`), add `rule: str = "majority"` to `run_regression_eval` with the early-stop threshold DERIVED from the active rule (the hardcoded stop-at-majority logic at L743-772 is wrong for the one-sided rule and at n≠5), and thread scripts/evolve.py `--regression-rule {majority,one-sided}` (default `majority`, byte-identical). One-sided semantics: rollback iff decided ≥ 4 AND P(p<0.5) ≥ 0.85 (at 5 decided: 0-5 and 1-4 only); fewer than 4 decided → keep, fail-open, reason string says why.
- **Type:** code
- **Issue:** #284
- **Flags:** --reviewers code
- **Produces:** `src/orchestrator/gate_stats.py`; `rule` param in `run_regression_eval`; `--regression-rule` flag; `tests/test_gate_stats.py`; extended `tests/test_evolve.py` regression cases
- **Done when:** exact-value tests for the posterior table (0-5→rollback, 1-4→rollback, 2-3→keep at n=5; correct behavior at n=3/7/9; draw-heavy records keep under the min-decided floor); early-stop fires at rule-correct thresholds in both rules; `rule="majority"` decisions byte-identical to today across a golden table of all (wins_new, wins_prior) pairs at n=5 and n=9. Gates clean.
- **Depends on:** none
- **Status:** DONE (2026-07-06)

### Step EJ.4: Frozen-baseline panel floor
- **Problem:** EJ.3's honest cost is regression catch at p=0.35 dropping 76.5% → 42.8%, and the "caught later" backstop is false (the next generation compares against the same weakened bar) — so the one-sided rule needs a drift backstop that cannot cycle. The EL gauntlet already plays the promoted version vs each frozen anchor under `--fitness-mode both` and is explicitly log-only ("a later step can drive gating off it", scripts/evolve.py L3825-3832). Behind `--panel-floor`: when the gauntlet ran and any `GauntletResult.per_baseline` win rate is 0.0 (sweep loss vs any anchor), trigger the existing rollback machinery (revert-first ordering per L4030-4063) with reason prefix `panel-floor:` and flip promoted imps to `regression-rollback`. Fail-open by construction: gauntlet crash, empty registry, or `--fitness-mode parent` → floor inert (the existing defense-in-depth try/except at L3837-3923 must keep protecting the committed promotion). Document in SKILL.md that production soaks should not enable `--regression-rule one-sided` without `--panel-floor` + registered baselines.
- **Type:** code
- **Issue:** #285
- **Flags:** --reviewers code
- **Produces:** `--panel-floor` flag + floor consumption in `scripts/evolve.py`; `panel-floor:` rollback rows; SKILL.md pairing warning; tests
- **Done when:** unit tests with fake gauntlet results cover: sweep loss vs one anchor → rollback via the same revert path; all anchors ≥ 1 win → no effect; gauntlet crash → promotion stands (fail-open); flag OFF → byte-identical. Integration test drives the loop with fitness-mode both + fake baselines and asserts floor rollback lands a `panel-floor:` reason row. Gates clean.
- **Depends on:** EJ.3
- **Status:** DONE (2026-07-06)

### Step EJ.5: Refresh-time proposal dedup
- **Problem:** The pool-refresh site appends `generate_pool` output with zero dedup (scripts/evolve.py L4128-4132) — near-duplicate titles re-enter and burn ~4-6 games each. Behind `--refresh-dedup`: normalize titles (casefold, strip punctuation/whitespace) and drop any fresh imp that (a) exact-matches an in-run promoted/stacked title, (b) has `difflib.SequenceMatcher` ratio ≥ 0.85 vs any existing pool title, or (c) duplicates another imp within the same fresh batch. One audit row per drop (`phase: "pool_dedup"`, the matched title + ratio in `reason`). Accept-short after dedup — never re-call `generate_pool` to top up (short-pool retry raises on persistent shortfall, evolve.py L1740-1744; pool-exhausted stop only triggers at 0 active, so a short pool is safe).
- **Type:** code
- **Issue:** #286
- **Flags:** --reviewers code
- **Produces:** `_normalize_title` + dedup filter + `--refresh-dedup` flag in `scripts/evolve.py`; `pool_dedup` audit rows; tests
- **Done when:** unit tests cover: exact promoted-title drop; 0.85-similar drop; sub-threshold survives; intra-batch duplicate drop; audit row shape; flag OFF → byte-identical append. Gates clean.
- **Depends on:** none
- **Status:** DONE (2026-07-06)

### Step EJ.6: Budget-aware final-generation fit + SKILL.md flags documentation
- **Problem:** A generation that starts with insufficient remaining budget dispatches the full pool and dies mid-fitness (`_budget_exceeded` break at scripts/evolve.py L3542-3549), stranding up to ~25 games of un-actionable results a night. Behind `--budget-fit`: before the fitness phase, estimate per-eval wall-clock from this run's completed evals (no observed data on generation 1 → no trim), reserve budget for stack-apply (0 games) + one regression (`games_per_eval` games) + the gauntlet when `--fitness-mode` ∈ {baseline, both}, and trim `active_idxs` to the top-rank prefix that fits — never below 1. Log one `phase: "budget_fit"` row with the dropped count (no silent caps). Also: document all six Phase EJ flags in `.claude/skills/improve-bot-evolve/SKILL.md`'s Flags table (EJ.4's pairing warning included).
- **Type:** code
- **Issue:** #287
- **Flags:** --reviewers code
- **Produces:** `--budget-fit` flag + trim logic in `scripts/evolve.py`; `budget_fit` rows; SKILL.md Flags table update; tests
- **Done when:** unit tests cover: trim to fitting prefix (rank order preserved); never trims below 1; generation-1 no-op; reserve accounts for regression + gauntlet; flag OFF → byte-identical dispatch. SKILL.md lists all six flags with defaults. Gates clean.
- **Depends on:** EJ.1, EJ.2, EJ.3, EJ.4, EJ.5 (docs roll-up; also serializes the shared-file edits)
- **Status:** DONE (2026-07-06)

### Step EJ.7: Flags-on smoke gate (real SC2)
- **Problem:** Verify the six flags compose end-to-end on real infrastructure before an overnight soak. Register 2 frozen anchors (e.g. v10, v13) in `data/baselines.json` via the EL baselines CLI, then run one short flags-on evolve: `uv run python scripts/evolve.py --pool-size 3 --games-per-eval 3 --hours 0.75 --priors-exclude-promoted --screen-null-diff --regression-rule one-sided --panel-floor --refresh-dedup --budget-fit --fitness-mode both`. Inspect `data/evolve_results.jsonl` for the new row types where triggered, confirm no crash, no orphaned `bots/cand_*` dirs, and pointer integrity. Then run one 15-minute defaults-off smoke and confirm behavior matches a pre-EJ run (no new row types, no flag effects).
- **Type:** operator
- **Issue:** #288
- **Produces:** smoke evidence appended to the run log under `documentation/soak-test-runs/`; pass/fail verdict per checklist item
- **Done when:** flags-on run completes without crash; every triggered EJ mechanism left its audit row; defaults-off run shows zero behavior change; verdicts recorded.
- **Depends on:** EJ.6

### Step EJ.8: Overnight flags-on A/B validation soak
- **Problem:** The investigation's numbers (null-rollback 50%→18.75%, ~5.9 games saved per avoided re-proposal, reclaimed final-generation games) are analytic; validate them against a real overnight run before any default flips. Run one standard 4h flags-on soak (`/improve-bot-evolve` flags as in EJ.7 minus the reduced sizes) and compare against the most recent flags-off soaks on: regression-rollback rate, `screen-null-diff`/`pool_dedup` catch counts, games per generation, generations completed, and promotions. Record the comparison table in the soak log; recommend (do not apply) default flips.
- **Type:** wait
- **Issue:** #289
- **Produces:** soak report under `documentation/soak-test-runs/` with the A/B comparison table + default-flip recommendation
- **Done when:** soak completes; comparison table filled from `evolve_results.jsonl` + run state; recommendation recorded. NOT gated on the soak showing improvement — a negative result is a valid outcome that blocks default flips.
- **Depends on:** EJ.7

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| One-sided bar weakens regression catch (76.5% → 42.8% at p=0.35) | Real regressions survive more often when the flag is on | EJ.4 pairing is mandatory in production soaks (SKILL.md warning); EJ.8 A/B soak before any default flip; frozen anchors cannot cycle |
| `evolve_worker.py` error propagation shape unknown | `DevApplyNullDiffError` could surface as a generic crash at `--concurrency` > 1 → wrong eviction path | EJ.2 reads the worker serialization first; explicit worker-path test required in Done-when |
| Frontend outcome-string handling | New `screen-null-diff` / `pool_dedup` / `budget_fit` rows could crash a strict parser in `useEvolveRun.ts` | EJ.2 verifies enumeration vs pass-through; bump `cacheKey` if strict (`feedback_useapi_cache_schema_break`) |
| AST-equivalence edge cases | Docstring edits are AST-visible (ast keeps docstrings) → docstring-only edits classified as real changes | Accepted: docstring-only edits are near-null but rare; do not over-engineer — zero-edit + comment/format-only are the observed failure modes |
| Baselines registry empty in production | `--panel-floor` silently inert (fail-open by design) → operator believes the backstop is armed | EJ.7 registers anchors and proves a floor row can fire; SKILL.md documents the prerequisite; startup log line when panel-floor is on but registry is empty |
| Six flags first compose in EJ.7 | Pairwise interactions (e.g. budget-fit trimming the eval a dedup just admitted) only visible flags-on | EJ.7 smoke + EJ.8 soak are dedicated observation steps; unit suites cover each flag against defaults |
| Curator promotion-correlation logic | `stack_apply` rows list stacked imps but promotion survival needs the same generation's `regression` row — miscorrelation excludes the wrong titles | Exact-generation join tested against a fixture results file with promote, rollback, and import-fail generations |
| Investigation throughput numbers | "3 min/game" vs "~55 games/hour" are inconsistent (implies concurrency ~3); budget-fit estimates could be off | EJ.6 estimates per-eval cost from THIS run's observed wall-clock, not constants — self-calibrating |

## 9. Testing Strategy

- **Unit tests per step** (see Done-when blocks): posterior exact-value table,
  early-stop thresholds per rule, title normalizer + similarity thresholds, curator
  exclusion/merge fixtures, AST-equivalence classifier, budget-fit arithmetic,
  consecutive-null-diff eviction.
- **Byte-identical-defaults regression tests** — for each flag: run the touched code
  path with the flag at its default and assert output identical to a pre-change golden
  (prompt text, dispatch order, regression decisions across the full (wins, losses)
  table, refresh append). These are `is`/exact-equality assertions per
  `.claude/rules/code-quality.md` (duplicate-shape-constants lesson).
- **Integration tests through the production caller** (code-quality rule): the
  scripts/evolve.py generation loop driven with fake `run_batch_fn`/`claude_fn`/
  `run_gauntlet_fn` and real dispatch/state code, asserting each new mechanism is
  reached end-to-end (null-diff row with zero games; panel-floor rollback row via the
  revert path; dedup audit row; budget-fit trim row). `tests/test_evolve_cli.py` already
  hosts this harness shape.
- **Existing tests that might break:** `tests/test_evolve.py` regression cases (new
  `rule` param — default keeps signatures compatible), pool-state round-trip tests
  (additive `consecutive_null_diffs` field), any test asserting the exact priors-block
  text (L1484 line changes only when exclusion is active — verify default path keeps
  old text, or update goldens deliberately with the anti-verbatim wording; decide at
  EJ.1: the prompt-line fix is the ONE deliberate default-visible change in this phase,
  so its golden update must be called out in the EJ.1 diff).
- **End-to-end:** EJ.7 real-SC2 smoke (flags on, then defaults off) + EJ.8 overnight A/B
  soak. Quality gates for every code step: `uv run pytest` (≥ 1799 baseline),
  `uv run mypy src bots --strict`, `uv run ruff check .`.
