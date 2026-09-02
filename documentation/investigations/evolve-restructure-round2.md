# Evolve restructure — adversarial round 2 (adjudication)

---
provenance: AGENT — 11-agent adversarial workflow, 2026-09-02. Round 1's five propositions,
  each argued by an independent DEFENDER and an independent REFUTER against data on disk, then
  adjudicated. ~1.9M subagent tokens, 0 errors. This round was run under a standing rule that
  arguments must COMPUTE rather than assert; the decisive moves are all measurements over the
  existing corpus, at zero SC2 cost.
status: ROUND 2 COMPLETE — the adjudicator's explicit finding is that the conversation has
  converged and **round 3 is unnecessary**. Next step is `/plan-feature` on section 4.
supersedes: this document is AUTHORITATIVE over `evolve-restructure-round1.md` wherever the two
  disagree. Section 3 below is the ONE owner of file:line truth for both documents — 19 of
  round 1's citations were re-checked and found wrong.
blocked_on: one letter from the operator on idea (ii) — see section 5, item 1.
---

## The two findings that decided this round

**1. Gate 1 selects improvement *texts*, not code — 100% of the time, at every stack size.**
Round 1 left this open as its first fault line. Round 2 closed it with a natural experiment
already in the repo's history: commits `f2eb564` and `ce8545f` both create `bots/v4` from parent
`v3`, hours apart on 2026-04-29, from the **same improvement text**. The two resulting
`decision_engine.py` bodies are materially different bots — different guard, different condition,
a new state transition in one that the other could never reach. One of them was a **single-imp
stack**, which is exactly what a stack-size cap produces. So the fitness games score a candidate
that is deleted, and a second independent authoring is what actually ships.

A separate check found the composition hole is not hypothetical either: in the shipped `v8`→`v9`
diff, a later stacked improvement **deleted an earlier stacked improvement's fitness-tested code**
and replaced it with different semantics. `v9` was not rolled back. It is an ancestor of today's
production `v13`.

**2. Round 1's "zero added games" claim was wrong on one row, and right for the wrong reason on
several others.** Two independent computations — an 87-eval replay and a 400,000-trial Monte Carlo
— converged on the same number: round 1's A8 early-stop change adds about **four games per
generation**, not zero, and it changes the pass/close bucket on roughly one eval in ten. A source
comment asserting otherwise is false on both halves. That single correction is what moves A8
behind a flag and keeps the phase's zero-added-games claim honest.

---

**Role:** final synthesizer. Ten arguments (5 propositions × defender/refuter) closed against the round‑1 record. Every file:line below was re‑checked in this session against `c:/Users/abero/dev/Alpha4Gate` @ `master-plan/phase-ev`; anchors I could not confirm are marked UNVERIFIED.

---

## 1. Verdicts on the five propositions

### P1 — `--stack-size 1` vs patch capture — **HOLDS NARROWED** (two of its four clauses fail)

**Decided by:** the `f2eb564` / `ce8545f` natural experiment, which I re-verified from git rather than accepting from either side.

Both commits create `bots/v4` from parent v3, hours apart on 2026‑04‑29, from the **same imp title** (`- DEFEND/FORTIFY stuck-state timeout in decision engine`). `f2eb564` is a **1‑imp stack** — literally what K=1 produces. The two `bots/v4/decision_engine.py` bodies are materially different bots:

| | `f2eb564` (N=1) | `ce8545f` (N=5) |
|---|---|---|
| guard | `self._state in (DEFEND, FORTIFY)` | none — wraps a new `_compute_next_state_inner` |
| condition | `enemy_army_supply_visible == 0`, `>=` | no visibility term, `>` |
| exit | `EXPAND` only | `ATTACK if army_supply >= 15 else EXPAND` |

Clause by clause:

- *"more completely"* — **FAILS.** K=1 closes the **composition** hole (61.1% of promotions, 23/41 = 56% of promoted imps) by construction. It does not touch the **re-authoring** hole, which is 18/18 promotions and 41/41 imps: the scratch is `_safe_rmtree`'d at `src/orchestrator/evolve.py:681` and the imp is re-written from `Improvement.concrete_change` free text at `scripts/evolve.py:2220-2221`. Round‑1 row A6's FOR text — *"at K=1 the promoted tree **is** the tested tree"* — is false; it has the same **shape**, never the same bytes.
- *"more cheaply"* — **HOLDS.** `winner_idxs` at `scripts/evolve.py:4559-4562` sits downstream of the serial/parallel fitness fork; A6 is `winner_idxs[:K]` plus one flag. N1 must cross the subprocess boundary the codebase itself warns about (`scripts/evolve.py:3818-3824`: *"this wrap is serial-only"*).
- *"its only real cost — promotions per hour"* — **FAILS twice, both found by its own defender.** The unit is wrong (promotion **events**/h rise ~4.4%; **imps**/h fall ~53%, because promotion is at most one event per generation regardless of K — `if not winner_idxs:` at `scripts/evolve.py:4583`). And it is not the only cost: `_RETRY_CAP = 3` (`:125`) + `retry_count += 1` (`:1930`) + `_apply_retry_bookkeeping` (`:1917-1918`) mean a deferred winner is **evicted after two more evals despite having passed**. Verified at source. A6 is unshippable without a retry-cap exemption.
- *"measurable from games already being played"* — **HOLDS for throughput, FAILS for value.** Both sides measured the throughput from disk at zero machine cost. Nobody can price the −53%: the defender's own regression stratification is N=1 0.528 (n=53) vs N≥2 0.582 (n=67), Fisher p=0.156, needing ~1329 decided games per arm.

**Games-saved test (presumed false until shown):** K=1 **is games-neutral**, and I side with the defender. Promotion events per generation are unchanged (`:4583`), the regression eval is `games=args.games_per_eval` once per promoting generation (`scripts/evolve.py:4944-4947`), and the pool refills to `--pool-size` (`:5052`, `:5054-5056`). The refuter's **+6.94 games / +20.7 min per promotion event** is a correctly measured per-event price, but their "+160 games" totals a counterfactual (*absorb the same 41 imps ⇒ 41 promotion events*) that no run bound produces — runs are bounded by `--hours`/`--generations`, not by imps absorbed. **Δgames for A6 stays 0.**

---

### P2 — nothing can reduce games / the currency is LLM time — **FAILS as stated; the narrow form HOLDS**

**Decided by:** `scripts/evolve.py:4437-4455` — the source comment is verbatim *"Fires before run_batch, so ZERO games play for this imp"* — plus `_handle_screen_null_diff` at `:1998`, `st.status = _EVICTED if evicted else _ACTIVE`. A pre-fitness screen leaves the imp **ACTIVE**, so `_count_active` (`:1856-1859`) is unchanged, `delta` does not rise, no refill fires, and the generation plays fewer evals. The defender conceded the mechanism outright.

Three findings, all convergent across the two arguments:

1. **Clause 1 fails.** Round 1's own option C (idea (ii) reading C) sits in that exact seat and round 1 itself costed it at −4 games per catch. The already-shipped, default-OFF `--screen-null-diff` (`scripts/evolve.py:363-376`) occupies the same seat.
2. **It also fails from inside the ADOPT set.** A8 raises games: defender **+4.1/generation**, refuter **+4.0/generation**, computed by independent methods (87-eval uncensored replay vs 400k-trial Monte Carlo at the measured p=0.4853). I believe both — the mechanism is plain: requiring `close` to be unreachable moves the reject-lock from 3 parent wins to 4. Round‑1's A8 row ("Δgames 0", "byte-identical under `majority`") is **wrong**, and the in-source comment it rests on is wrong too: `src/orchestrator/evolve.py:618` claims *"Saves ~2-3 games per evaluation (no effect on bucket assignment)"*; it saves ~0.93, and it **does** change buckets, because `_fitness_bucket(wins_cand, games)` at `:658` is passed the **declared** `games`, not games played.
3. **Clause 2 inverts.** SC2 is the majority of the one `--hours` budget (`_budget_exceeded`, `scripts/evolve.py:1844-1853`): 61.9% in-game on the 2026‑04‑28 serial segment (defender) and 66.8–72.7% over three consecutive generations of the 2026‑05‑01 soak (refuter). LLM time is real (~29–36%) but not dominant, so `--stack-model` / `--stack-mode unified` do **not** become the highest-leverage items by that argument.

**What survives, verified line by line:** post-fitness reallocation — eviction, deferral, rollback granularity, re-composition — is exactly games-neutral. That is the theorem that legitimately killed R4, R5 and D3's eviction half. Restate the proposition as: *post-fitness reallocation is games-neutral by the refill at `:5052`/`:5054-5056`; pre-fitness interception and the per-eval stopping rule are not.*

---

### P3 — no semantic signal can be calibrated / mechanical classes are the safe harbour — **HOLDS NARROWED, prescription clause STRUCK**

Both roles returned HOLDS NARROWED, both computed, and their **leakage-free tests converge** — that convergence is what decides it.

- **Mechanism clause: directionally right, quantitatively wrong by ~4–5×.** Round 1's 0.484 who-won spread is the bit measured against a label it is a *summand of*. Clean leave-own-game-out: refuter 0.100 spread (χ²=14.08, p=0.0002, n=1270); defender +0.082 (p=0.0035). A semantic feature has to beat 0.100, not 0.484.
- **Operative clause holds.** On a leakage-free label every semantic feature dies: defender's own LOO run shows `ever_attacked` +0.247 → +0.044 (p=0.200) → −0.011 (p=0.79) after conditioning on who-won, with `max_supply` and `max_units` **sign-flipping**. And the fitness bucket itself is ~82% sampling noise (I reproduce: pooled candidate WR **0.4989** over 93 uncensored fully-decided 5-game evals; accept≥3 = 48.4% vs a 50% null).
- **Prescription clause FAILS — this is round 2's second decisive finding.** P3's own recommended safe harbour is refuted by the identical measurement: on the uncontaminated stratum the refuter measured `gametime` AUC 0.467 (p=0.445), `n_steps` 0.467, terminal-state marker 0.471, all Holm 1.000, against a shuffled-label anchor of 0.502 — indistinguishable from the semantic features they were meant to replace. The defender's own LOO table agrees. **Neither side rebutted this.** "Scope idea (ii) to tautology-free mechanical classes" must not go into the plan's first paragraph.
- *"Advisory forever"* — **UNDECIDABLE and unnecessary.** Defender: *"'forever' is not established."* Refuter: advisory-vs-gate is set by `SKILL.md:574`, not by calibration. Replace with a falsifiable bar (§4).

**Corpus defect I confirmed myself, which invalidates round 1's FL3 numbers:** **185 of 504** candidate traces (36.7%) contain **no `state=` line at all** — the log line landed 2026‑04‑27 (`04d3e04`), so `ever_attacked` is silently `False` on more than a third of the corpus round 1 measured. Every FL3 spread in the record mixes *"did not attack"* with *"the log format did not exist yet."*

**Where the two disagreed on corpus size (504 vs 377), I believe 504.** I counted 504 `logs/selfplay_cand_*.log` and 30 `logs/selfplay_v*_p*.log` (534 total). The defender joins by the candidate uuid embedded in the filename, which is exact; the refuter's mtime-±90 s join dropped 127 files, which is also why their no-result-line rate (6.9%) undershoots. I measured the true rates myself: **42/504 = 8.3%** candidate, **10/30 = 33.3%** parent — confirming round‑1's N4 figures, which the record carried as *unverified*.

---

### P4 — the proposer evidence block — **HOLDS NARROWED; the ranking clause is UNDECIDABLE**

I read the prompt construction rather than accepting either account.

| Clause | Verdict | Anchor |
|---|---|---|
| "a 4000-byte tail of **one** overwritten log, **parent seat only**" | **FAILS on both halves** | `src/orchestrator/evolve.py:1381` globs `selfplay_{parent}_*.log` and tails **each** match — up to two files, both seats the parent **version** occupied, ~8 KB |
| "overwritten" | **HOLDS** | `src/orchestrator/selfplay.py:292` keys the path on `{version}_{role}` only; burnysc2 opens it `"w+"` |
| "spliced across workers under concurrency" | **HOLDS** | Mechanism is in source; the 2026‑05‑01 soak header records `concurrency=2` (I verified); defender measured 88 concurrent same-path collisions, **88/88 on parent-version files** — candidate paths carry a uuid and cannot collide. Refuter's 0/534 surviving splice fingerprints is consistent (last-writer-wins destroys the interleaved bytes) |
| "presented as **mirror evidence** on refresh generations" | **FAILS** | `:1429` prints `Mirror games run (parent vs parent): {mirror_games}`, and `:1853` passes `0` on refresh; the mirror summary renders all zeros; the tails sit under their own `## Self-play log tails` heading at `:1435`. The defect is **missing provenance**, not a false label. The defender conceded this |
| "Fixing it outranks every gate change" | **UNDECIDABLE** | No A/B exists and none can be run without a soak slot — the defender says so in its own concession |

On the ranking clause the numbers genuinely conflict and I decline to pick a winner. The defender's *98.1% of everything that varies between two consecutive refresh prompts* is correct arithmetic; the refuter's *8.1% of compressed prompt information vs 88.1% for guiding-principles + priors*, and *log-citing imps score 3.500 vs 3.625 wins (n=18)*, are also correct and point the other way. Neither is a causal measurement. **Neither computed the thing the clause asserts, so it stays unresolved and must not be used to reorder the scope.**

**One thing round 2 did settle mechanically, resolving operator decision point 7:** there are **no prompt goldens**. Zero hits for `_read_log_tails` / `log_tails` / `_build_prompt` / `_PROMPT_HEADER` across `tests/`; the entire assertion surface is `tests/test_evolve.py:1841` (`"no logs"`) and `:1872` (`"parent vs parent): 3"`, both preserved by a corrected block. Round‑1 row A3's sole *Strongest AGAINST* is withdrawn.

---

### P5 — `--fitness-alpha` must be sequenced behind patch agreement — **FAILS as an argument; its conclusion survives on replacement grounds**

- **The doctrinal citation is the wrong section.** § *Score the production artifact* prohibits scoring a **proxy** — a transcript, plan or self-report. Gate 1 plays real SC2 with a compiled tree built by the production callable: one binding, `dev_apply_fn = spawn_dev_subagent` at `scripts/evolve.py:3816`, passed to fitness at `:4434` and to stack-apply at `:4612` → `:2221`. That satisfies § *Assemble through the production code path*. The rule that **does** bite is the defender's better citation, § *Match measurement scope to decision scope*: the decision (promote vN+1) includes a re-authoring and N-way composition step the measurement never includes.
- **N1 is the wrong gatekeeper, on two unrebutted grounds.** (a) Round‑1's own pre-registered rule sends both branches the same way — high agreement ⇒ tighten; low agreement ⇒ gate 1 is *only* a text filter over the sole surviving object, which is *more* reason to filter better. A measurement whose branches agree is not a gate. (b) `git apply --check` has no calibratable anchor, and the `f2eb564`/`ce8545f` pair proves the failure mode concretely: two **legitimate** implementations of one imp text, neither of which would apply against the other. N1's histogram will read near-zero agreement for benign reasons.
- **The cost figure is pool-size dependent.** I reproduced round 1's corpus exactly — 240 evals, majority accept **92/240 = 38.3%** — but the next-rung accept is method-dependent (round 1: 12.9%; P5-defender: 12.7%; my reconstruction: **14.6%**; refuter's ten definitions: 8.8–16.2%). At `--pool-size 10` the dead-generation model gives 0.0083 → 0.212 (~25×, close to round 1's 30×). But all three multi-generation soaks on disk ran **`pool_size=4`** (verified in their run headers: `evolve: starting run (parent=v7, pool_size=4, budget=9.0h, concurrency=2, run_id=319c3945)`), where the same model gives 0.145 → ~0.53 and the **observed** dead-generation rate at today's bar is already **4/18 = 22.2%**. The "30× rise from near-zero" is a pool-10 artifact; dead generations are a routine, already-logged state (`scripts/evolve.py:4583-4588`).
- **The real prerequisite is A8, not N1.** Both sides converge: the corpus was collected under the very stopping rule the bar would move, so k is **not identifiable** from it.

**Ruling: P5 fails; D2 still stays out of this phase, for replaced reasons.** Not "30× dead generations" and not "behind patch agreement", but: *(i)* k is unidentifiable until A8 lands and a post-A8 corpus exists, and *(ii)* gate 1's accept rate is statistically indistinguishable from a pure-null pool (my own reconstruction, above). D2 becomes a ~1-function follow-on after one post-A8 soak.

---

## 2. Fault lines — resolved or still open

### FL1 — is gate 1 measuring the shipped artifact, or only selecting imp texts? — **RESOLVED**

**Answer: only imp texts, 100% of the time, at every stack size.** The only object crossing the gate is `winning_imps = [pool[i] for i in winner_idxs]` (`scripts/evolve.py:4591`) — frozen `Improvement` dataclasses whose payload is free text. The `f2eb564`/`ce8545f` pair is the proof: same text, same parent, two authorings, two different bots — and one of them is an N=1 stack.

Two corollaries that change the plan:

1. **Round‑1's "A6 and N1 are substitutes, not complements" is wrong.** They address disjoint sub-holes: A6@K=1 closes composition (61%); only capture-plus-**replay** (D1) closes re-authoring (100%). The P1 defender conceded this in its own concession.
2. **N1 as specced measures something whose interpretation is undefined.** Its pre-registered X-threshold rule (`round1.md:198`) must be **deleted**, and N1 re-scoped to a log-only artifact rather than a decision input.

### FL2 — is "games saved" a real currency? — **RESOLVED**

Yes, through exactly two channels, neither of which round 1 was arguing about:

- **Post-fitness reallocation: exactly zero.** Verified at `:5052` / `:5054-5056` / `:1856-1859`. R4, R5, D3's eviction half stay dead.
- **Pre-fitness interception: genuinely negative.** `:4437-4455` + `:1998`. Round 1's option C and the shipped-but-disarmed `--screen-null-diff`.
- **The per-eval stopping rule: movable in both directions**, and round 1's A8 moves it **up** by ~4 games/generation.

And SC2 is 62–73% of generation wall clock against one `--hours` budget, so games are the dominant term, not a side currency. Round‑1's *"the Δgames column is 0 on 28 of 30 rows"* pattern is real for reallocation rows and false for A8.

### FL3 — can any label calibrate a semantic signal? — **STILL OPEN**, but bounded and with its blocker named

On a leakage-free label, **everything** is null at ~20% power — semantic and mechanical alike. Nothing on disk distinguishes *"no signal"* from *"no power"*, and both sides say so explicitly.

**What would settle it:** A1 + N2 producing one trace **per game**, so a full-eval feature vector can be tested against a **different** eval's outcome. That is exactly items 1–3 of the recommended scope, so the phase is being built in the right order. Two extra requirements this round adds: the label must be **held out** (leave-own-game-out within an eval, or a different eval entirely) — never the same eval's own bucket, which is where the 0.484 came from; and any historical feature study must exclude the 36.7% of traces that predate the `state=` log line.

---

## 3. Anchor corrections (consolidated — the record will be scraped)

Everything below was re-checked this session. ✅ = I verified it personally.

| # | Round‑1 citation | Correction |
|---|---|---|
| 1 | `scripts/evolve.py:5053-5056` (pool refill) | ✅ **5053 is blank.** Block is `:5051` comment, **`:5052` `_apply_retry_bookkeeping`**, `:5054` `active_after_refresh`, `:5055` `delta`, `:5056` `if delta > 0:`. Omitting `:5052` is what makes the refill look universal — it is the line that flips benched imps back to ACTIVE before the count |
| 2 | `evolve_dev_apply.py:234` — `model: str = "opus"` | ✅ **Wrong.** `def spawn_dev_subagent(` at `:233`, `version_dir: Path` at `:234`; the default is at **`:237`**. (The seed doc has it right; the round‑1 record drifted) |
| 3 | `scripts/evolve.py:2219-2220` (FL1, re-authoring loop) | ✅ **Off by one.** `:2219` is `snapshot_current(new_version)`; the loop is **`:2220-2221`** |
| 4 | `src/orchestrator/evolve.py:622-631` (early stop) | ✅ **One short.** `pass_threshold` at `:622`, **`stop_event.set()` at `:632`** |
| 5 | §0 "Proposer sees a 4KB tail of **one** shared log"; P4's "parent seat only" | ✅ **Wrong.** `:1381` globs `selfplay_{parent}_*.log` and tails **each** match — up to two files (both seats of the parent version), ~8 KB. Only the candidate's own `selfplay_cand_<uuid>_*.log` is excluded |
| 6 | Row A3 — "presented as mirror evidence on refresh generations" | ✅ **Not supported.** `:1429` + `:1853` print `Mirror games run (parent vs parent): 0`; tails sit under `## Self-play log tails` (`:1435`). Defect = missing provenance |
| 7 | Row A3 *Strongest AGAINST* — "every prompt golden updates" | ✅ **False.** Zero hits for `_read_log_tails`/`log_tails`/`_build_prompt`/`_PROMPT_HEADER` in `tests/`; only `tests/test_evolve.py:1841` and `:1872`, both preserved |
| 8 | Row A6 *Strongest FOR* — "at K=1 the promoted tree **is** the tested tree" | ✅ **False.** Scratch rmtree'd at `:681`; imp re-authored from text at `:2220-2221`. Proven by `f2eb564` vs `ce8545f` |
| 9 | Row A8 — "Δgames 0", "byte-identical under `majority`" | **Wrong: +4.0 to +4.1 games/generation** (two independent computations). Also fold in: the source comment at `src/orchestrator/evolve.py:618` is false on both halves — ✅ `_fitness_bucket(wins_cand, games)` at `:658` takes **declared** games |
| 10 | Row D2 — "0.008 → 0.251, a 30× rise" | Computed at `--pool-size 10`. ✅ All three multi-generation soaks ran **`pool_size=4`**, where the model gives 0.145 → ~0.53 and the **observed** dead-generation rate today is already **4/18 = 22.2%** |
| 11 | Rows A9/D2 — "k=4/5-equivalent 31/240 = 12.9%" | ✅ Companion figure **majority 92/240 = 38.3% reproduces exactly**; the k=4 figure does not. My reconstruction: **35/240 = 14.6%**; refuter's ten definitions span 8.8–16.2%. **Report as a bracket** |
| 12 | §2 FL3 — `ever_attacked` 0.225 spread; who-won 0.484; stratified +0.302/−0.036 | Not reproducible by either round‑2 agent, and computed over a corpus where ✅ **185/504 (36.7%) of candidate traces have no `state=` line at all** |
| 13 | Row N4 — "~30% of parent traces *(unverified)*" | ✅ **Now verified: 10/30 = 33.3% parent, 42/504 = 8.3% candidate** (both measured this session) |
| 14 | §0 — "`data/lineages.json` absent" | ✅ True, but a confusable sibling **`data/lineage.json`** (4,658 B, 2026‑08‑10) exists. `baselines.json` and `fingerprints.json` are genuinely absent; `evolve_results.jsonl` is ✅ 0 bytes |
| 15 | §0 stack sizes | ✅ **Correct and reproduced exactly**: 18 stacks {1×7, 2×4, 3×4, 4×1, 5×2}, mean 2.278, 11/18 = 61.1%, 41 imps. Two caveats: a bare `git log --grep="promoted stack"` returns **19** (the extra is `137ce12`, a code commit); and **2 of the 18 list the same imp title twice** (`648b036`, `68df901`) |
| 16 | *(missing from round 1, load-bearing)* | `_fitness_bucket` at `src/orchestrator/evolve.py:506-521`; the `fitness outcome:` log line at **`:659-663`**, which **does** carry `wins_parent`. A4's "not persisted" is true of `PerItemState` but wins_parent **is recoverable from run logs** |
| 17 | §0 `PerItemState` `scripts/evolve.py:826-839` | ✅ Decorator `:826`, `class` `:827`, fields `:829-838`. Field list correct; no `wins_parent` |
| 18 | `evolve-judging-alternatives.md:58` (cited by round 1 without flagging) | Its own anchors are stale: `evolve.py:673-679` → `675-681`; `scripts/evolve.py:1555-1556` → `2220-2221` (+665); `evolve_dev_apply.py:618-671` → `_run_mypy` at ✅ `:835`, argv `:846-848` |
| 19 | Seed `:66` / round‑1 `:3821-3828` (`functools.partial`) | ✅ Comment block `:3818-3824`, `screen_null_diff = bool(...)` `:3825`, `functools.partial(` **`:3827-3829`**. Both citations bracket it imperfectly |

**One round‑2 correction that is itself wrong — do not apply it.** The P3 defender claims `SelfPlayRecord`'s "decorator is at `contracts.py:118` and the nine fields run `:127-135`." ✅ Actual: decorator `:120`, `class` `:121`, fields `:129-137`. **Round‑1's original `:121-137` is correct.**

**One task-prompt correction:** `Alpha4Gate/.claude/rules/measurement-validity.md` and `.../code-quality.md` **do not exist**. Alpha4Gate's rules dir holds only `bot-runtime.md`, `evolve.md`, `frontend-ui.md`, `wsl-evolve.md`. Both rules are workspace-level: `C:/Users/abero/dev/.claude/rules/`, long form at `C:/Users/abero/dev/.claude/references/measurement-validity.md`.

---

## 4. FINAL SCOPE for `/plan-feature`

### Feature brief — "Evolve evidence layer" (suggested phase letter **EI**; operator assigns)

**Problem statement.** `/improve-bot-evolve` spends roughly two hours of SC2 per generation and keeps almost nothing from it. Nine fields per game reach `data/selfplay_results.jsonl`; the per-seat console log is truncated by the next game and, under concurrency, spliced between workers; the fitness-winning candidate tree is deleted in a `finally` before anything reads it; `data/evolve_results.jsonl` is zero bytes because it self-truncates every run; and the most expensive call in the loop — the pool proposer — is handed two 4,000-byte tails of two unrelated games with no label saying which games they were.

Round 2 established the consequence precisely: **gate 1 selects improvement *texts*, not code.** Two commits that promote the same imp text against the same parent produced materially different bots, and one of them was a single-imp stack. So the fitness games score an artifact that is thrown away, and a second, independent LLM authoring is what ships — every time, at every stack size. On top of that, 61% of promotions stack multiple imps onto a tree no game ever scored, and two of eighteen stacks applied the same imp twice.

This phase does not fix that. It buys the instruments that make it decidable, fixes the one outright correctness defect in the proposer's inputs, and wires the zero-game structural levers whose value does not depend on an unmeasured base rate. It deliberately settles nothing about gate-1 bars, judges, or replay.

### In scope — ordered, each by its round‑1 row id

**Group A — evidence capture (the phase's thesis; rides an existing soak's flag-OFF shadow half)**

1. **A1** — per-game self-play log paths, discriminated by game **and worker**, plus a retention cap. `src/orchestrator/selfplay.py:292`. *Round 2 upgraded this from hygiene to a correctness fix: 88 measured concurrent same-path collisions, 88/88 on the parent-version files the proposer reads.*
2. **A2** — `src/orchestrator/trace.py`, pure-stdlib trace parser + mechanical feature extraction. Leaf module, no `bots.*` import. Both P3 agents prototyped one; the feature list and the `Result for player N` terminal marker can be named in the plan.
3. **N2** — trace capture at the two existing `on_game_end` seams (fitness `:626`, regression `:827`), fail-open, retention-capped. **Narrowing changed:** round 1 said *hard-disable under `--concurrency > 1`*; that would make the layer inert on the most recently recorded soak config. With A1's per-worker paths landing first, the splice cause is removed — so the constraint becomes **"sequence after A1"**, not "disable".
4. **A3a** — honest provenance labelling of the **existing** evidence block: name each tail's file, seat, opponent and outcome from a `selfplay_results.jsonl` join, and say in words when no mirror games ran this generation. Ships standalone with **no** dependency on A1/A2/N2 and **zero** test cost (item 7 of §3). *P4's "mirror evidence" framing is struck; the defect is missing provenance.*
5. **N1 (re-scoped, log-only)** — capture the fitness-time patch (reusing `_collect_candidate_py_content`, `evolve_dev_apply.py:629`) **and** the stack-time patch, plus a mechanical similarity score. **No longer a decision input:** drop the `git apply --check` verdict as the headline metric and delete the pre-registered X-threshold rule at `round1.md:198`. Capture the patch in an **applicable** form so D1 becomes a follow-on rather than a rebuild. Concurrency threading may be deferred exactly as N2's was — this cuts N1 from M toward S.

**Group B — cheap normalizations that make FL3 answerable**

6. **A4** — persist `wins_parent` in `PerItemState`. Justification corrected: it is censored in state but **recoverable from run logs** (`evolve.py:659-663`), so this is normalization, not new information.
7. **A4+ (new row)** — add a `fitness_eval_id` + game index to `SelfPlayRecord` (`contracts.py:121-137`, nine fields, no eval key). *This is the cheapest thing in the whole scope that makes FL3 answerable: it turns the leave-own-game-out label both P3 agents had to reconstruct by timestamp clustering into a first-class artifact.* S, additive, zero games.

**Group C — structural levers, flags OFF by default**

8. **A6** — `--stack-size K`, default 0 = today. **Mandatory sub-item, verified at source:** deferred winners must be exempted from — or given a raised — `_RETRY_CAP` (`scripts/evolve.py:125`; `_apply_retry_bookkeeping` `:1917-1918`; `retry_count += 1` at `:1930`), or K=1 silently evicts imps that passed gate 1. **The flag is unshippable without it.**
9. **A7** — `--stack-model MODEL`, default = today (operator idea (i), literal). One `functools.partial` at `scripts/evolve.py:4609`.
10. **A5** — `--stack-static-gate`, whole-package `mypy --strict` before the `[evo-auto]` commit, with the drop-lowest-rank rule. *First item to cut if the phase needs trimming: its motivation is cross-imp signature drift, which cannot occur at K=1.*
11. **A8** — rule-derived gate-1 early stop, **now behind a flag** (`--fitness-stop rule|majority`, default `majority` = today's hardcoded behaviour). **This is a change round 2 forced:** A8 is not byte-identical under `majority`; armed it adds ~4 games/generation and changes the fail↔close bucket on ~10% of evals. Flagging it is what keeps the phase's "zero added SC2 games" claim true. Fold in the correction of the false comment at `src/orchestrator/evolve.py:618` in the same step.

**Step-0 analysis input (not a build step)**

- **A9** — the corrected replay over `data/selfplay_results.jsonl`, printed into the plan **as a bracket, not a point**: 240 evals; majority accept 92/240 = 38.3%; next-rung accept 8.8–16.2% (my reconstruction 14.6%, round 1's 12.9%); dead generations 0.0083 → ~0.21 at `--pool-size 10` but 0.145 → ~0.53 modelled and **22.2% observed** at the `pool_size=4` the recorded soaks used; pooled candidate WR 0.4989 over 93 uncensored fully-decided 5-game evals.

**Optional adjunct (decision point 6)**

- **N6** — `scripts/lineage.py` (create/list/retire/**reset**) + launcher `--generations 0 --budget-fit`. `scripts/launch-evolve.ps1:72` is verified to omit `--generations`, so the observatory run-evolution button truncates every soak to one generation.

### Explicitly OUT, and why

| Item | Why out |
|---|---|
| **D2 `--fitness-alpha`** | **Reason replaced, not the verdict.** Not "30× dead generations" (a pool-10 artifact) and not "behind patch agreement" (wrong gatekeeper — P5's argument fails). Out because *k is unidentifiable* until A8 lands and a post-A8 corpus is collected, and because gate 1's accept rate is indistinguishable from a null pool. Becomes a ~1-function follow-on after one post-A8 soak |
| **D1 deterministic replay** | Still out **but promoted to the phase's declared successor.** FL1's resolution shows K=1 + replay is the only pair that closes both holes; the trigger changes from "N1's histogram says X" (now known uninformative) to "the operator wants the re-authoring hole closed and the capture exists" |
| **Arming any flag as default** | Post-EJ.8 operator decision, per `evolve-judging-plan.md` §3 |
| **Any LLM judge in a gate path** | R7, R11 — `SKILL.md:574`, verified verbatim |
| **Semantic play-quality metrics (R6)** | Now on stronger grounds: `ever_attacked` is degenerate (constant on the winning stratum) and null on a held-out label |
| **"Tautology-free mechanical classes" as the prescribed judge scope** | **Newly out.** P3's own prescription, refuted by measurement: duration/step-count/terminal-marker are AUC 0.467/0.467/0.471, Holm 1.000 — the same null as the semantic classes |
| **The pre-registered X-threshold rule (`round1.md:198`)** | Deleted. Both branches point the same way, and `git apply --check` cannot separate "same idea, different implementation" from "different idea" |
| R1, R4, R5, R8, R9, R10, R12, N3 | Unchanged from round 1 |

### The one addition round 2 argues for that round 1 listed neither in nor out

**Arm the already-shipped `--screen-null-diff`** (`scripts/evolve.py:363-376`, EJ.2, default OFF). Both P2 agents recommended it independently. It is the only lever on the board that **reduces** SC2 games; its rows are exactly the pre-fitness fidelity evidence N1 wants; and it is the control that `measurement-validity.md` § *Score the production artifact* names as its own positive exemplar (`no_diff` force-scored 0 before any judge call). It has **never run armed** — `data/evolve_results.jsonl` is 0 bytes and no log on disk shows it firing, so its catch rate is **UNVERIFIED**.
*Placement:* it is a **behaviour change** (a null imp is re-rolled instead of playing), so it belongs in the arming half with A6, not in the shadow half.

### Acceptance criterion (operator-checkable)

After the next queued soak completes with the capture flags armed in their **log-only** forms:

1. `logs/` contains one trace file per **game and worker**, not one per (version, role).
2. `data/evolve_results.jsonl` carries, per stacked imp, both the fitness-time and stack-time patch plus a similarity score — **read as an artifact, not scored against a threshold**.
3. The pool prompt for at least one refresh generation names each evidence tail's game, seat, opponent and outcome. *(Round 1's second half — "says in words that no mirror games were played" — is already true in shipped code at `:1429`/`:1853` and must be dropped, or it passes with no change.)*
4. Every fitness row carries a non-null `wins_parent` **and** a `fitness_eval_id`.
5. At least one whole-package `mypy --strict` result is recorded for a promoted vN+1 (pass or fail — either is evidence).
6. **New:** the run reports gate-1 accept rate with an exact binomial p-value against the pure-null 0.5 baseline, and — if `--screen-null-diff` is armed — its catch count. Without those two numbers there is no known-garbage anchor for gate 1 at all.

### SC2 game budget

- **Shadow half (items 1–7, 10, and A8 shipped flag-off): zero added SC2 games.** Capture, patch recording and static analysis have no effect on stack composition, gating, or games per generation, so none of EJ.8's five declared comparison metrics move. This is only true because A8 is flagged — armed, it adds ~4 games/generation (~+11 min), which is the change round 2 forced.
- **Arming half (A6 at K=1, `--screen-null-diff`, `--stack-static-gate` as a real gate): needs its own slot.** Net budget roughly flat — K=1 is games-neutral (verified), `--screen-null-diff` **reduces** games, A8's rule adds ~4/generation. One generation at the launcher's config (`--pool-size 10`) is ~41 fitness + 5 regression ≈ 46 games ≈ 2.1 h of SC2 at the measured 164.8 s/game.

### Dependency order

```
A1 ──► A2 ──► N2 ──► A3b (richer evidence)
A3a  (standalone, no deps — ship first, it is the cheapest correctness fix)
A4 ─┬► A4+ (fitness_eval_id)
    └► A6 (rank tie-break becomes load-bearing at K=1)
A6  requires the _RETRY_CAP exemption in the same step
A8  (flagged) ──► [post-soak corpus] ──► D2, a later phase
N1  (capture, applicable form) ──► D1, the declared successor
A7, A5, N6  independent
```

### What round 2 changed in round 1's recommendation, and which argument changed it

| Change | Changed by |
|---|---|
| A6 and N1 are **complements, not substitutes**; FL1 resolved as "text filter, 100%" | P1-refuter's `f2eb564`/`ce8545f` natural experiment (I re-verified it from git) |
| A6 gains a **mandatory `_RETRY_CAP` exemption**; its cost restated as imps/h not promotions/h | P1-defender, found while defending its own proposition |
| A8 moves **behind a flag**; Δgames 0 → +4/generation; the false comment at `:618` folded in | P2-defender **and** P2-refuter, converging at +4.1 and +4.0 |
| **`--screen-null-diff` added** as the arming half's game-reducing lever and gate-1's first garbage anchor | Both P2 agents |
| N2's `--concurrency > 1` disable **replaced** by "sequence after A1" | P4-defender's 88 measured collisions + P5-refuter's note that the disable would make the layer inert on the recorded config |
| N1 **re-scoped to log-only artifact**; the X-threshold decision rule **deleted** | P5-refuter (both branches agree) + FL1 (no anchor for `git apply --check`) |
| P3's prescription — "scope idea (ii) to mechanical classes" — **struck from the plan's first paragraph** | P3-refuter's Holm-corrected null on duration/step-count/terminal-marker, unrebutted, and corroborated by P3-defender's own LOO table |
| A3 **split into A3a/A3b**; decision point 7 answered mechanically (no prompt goldens exist) | P4-defender's test-surface grep, which I verified |
| **A4+ (`fitness_eval_id`)** added | P3-defender |
| D2's deferral **reason replaced**; D1 promoted from "defer" to "declared successor" | P5-refuter (doctrine + gatekeeping) and P1's pair together |

---

## 5. What still needs the operator

Each answerable in one line. **Idea (ii) first — it is still the only hard blocker.**

1. **Idea (ii) — code or game?** *"Use a SC2 version of the judge"*: **C** (judge each candidate's diff before it plays), **F** (judge the merged stack at step 7), **A** (rendered frames), **B** (per-step telemetry), **D** (anti-degenerate referee) — one letter, or two for a pair. *Contingent:* **A/B/D** add a mandatory Step 0 (attach a per-seat telemetry sink at `bots/v13/__main__.py:276`, currently `bot = Alpha4GateBot()` with no `logger=`) — which is already scope items 1–3. **C** now has a measured SC2 return (it is the pre-fitness seat, and `--screen-null-diff` already occupies it). **D** additionally needs `data/baselines.json`, which is absent. Round 1's best guess remains **F + B**, ~45%.
2. **Soak slot.** Ride the flag-OFF shadow half of a queued soak (zero new machine time), or grant this restructure its own slot? *Contingent:* items 8 and 11's arming, plus `--screen-null-diff`, cannot ride the shadow half — they perturb four of EJ.8's five metrics.
3. **Arm `--stack-size 1`?** Yes/no. *Contingent:* if yes, item 8's `_RETRY_CAP` exemption is mandatory and item 10 (A5) loses most of its motivation. If no, A6 still ships as a flag.
4. **Idea (i) shape.** `--stack-model` (escalate the model on the existing N-sequential-rewrite path) or `--stack-mode unified` (one high-tier model writes one merged change)? *Contingent:* decides whether item 9 stays A7 or becomes D5.
5. **Interruptions.** Has an evolve run ever been interrupted *after* it promoted a stack in that same run? *Contingent:* a single yes re-opens N5 and the durability lane; three abnormal terminations are on disk, all with zero promotions.
6. **Adjunct.** Ship N6 (`scripts/lineage.py` + launcher `--generations 0 --budget-fit`) inside this plan, accepting that a non-empty lineage registry latches multi-lineage scheduling on for every subsequent bare run — or keep it separate?

*Withdrawn from round 1's list:* **decision point 7 (prompt golden)** — answered mechanically, there are none. **Decision point 2 (currency)** — answered by measurement: 30–45% of slots are already re-tests, post-fitness reallocation is games-neutral, and SC2 is 62–73% of generation wall clock.

---

## 6. Does round 3 have an agenda?

**No. The conversation has converged enough to plan, and round 3 is unnecessary.**

Three of the five propositions were decided by facts that neither side disputes once stated (the `f2eb564`/`ce8545f` pair, the pre-fitness screen's own source comment, the prompt's own `Mirror games run: 0` line). The two remaining disagreements are not resolvable by more argument:

- **P4's ranking clause** needs an A/B of corrected-vs-current proposer prompts, which costs a soak slot. Both sides said so.
- **FL3** needs one trace per game, which is scope items 1–3.

Everything else that round 3 could argue is a question the plan is designed to answer with data rather than debate. The one thing worth doing before `/plan-feature` is mechanical, not adversarial: **apply the anchor corrections in §3 to `documentation/investigations/evolve-restructure-round1.md` and `docs/seeds/evolve-restructure-operator-notes.md`**, because the record is about to be scraped and nineteen of its citations are now known to be wrong, one of them (`evolve_dev_apply.py:234`) in a place the seed doc already had right.