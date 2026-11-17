# PPO.0 P2: design-space survey

Date: 2026-11-16. Status: **survey complete; P1 measurement and P3 operator decision pending**.
Source baseline: HEAD `1d6a4ecaef5dd2fff32af574028b2233d0d25684`, with a dirty shared
checkout; current pointer `v13`. Source claims below describe inspected working-tree
files, not a clean release. No SC2 game, training, paid call or checkpoint load was run.
Parent: [PPO.0](../plans/master_plan.md), [seed](../seeds/ppo-direction.md).

## Recommendation

Measure the existing six-state policy first when Stage 0 is available. For a new
control-surface experiment, prefer **army intent with rules-owned macro and execution**:
a small choice of attack, hold and retreat among currently legal intents. This combines
question 1c's separation with 1e's engagement authority and 1f's legality boundary.
It is a proposed experiment, not a decision to port Jev or replace the current bot.

The case is causal clarity: the current strategic state influences several systems at
once, while a combat-only experiment can hold production and expansion behavior fixed.
This recommendation is an inference from local architecture and modular RL literature;
there is no evidence yet that it will beat v13. A per-game opening selector is a useful
cheap comparator, but cannot repair mid-game engagement errors and need not use PPO.

## Evidence ledger

| Evidence | Verified observation | Consequence |
|---|---|---|
| `bots/v13/learning/features.py`: `FEATURE_DIM`, `BASE_GAME_FEATURE_DIM`, `encode` | 55 = 40 scalar + 8 build-order + 7 advisor; base is 48 | Corrected the seed's double-counted build-order block; name schemas, not just widths |
| `bots/v13/learning/neural_engine.py`: constructor and `predict` | `PPO.load`; deterministic prediction defaults true; six strategic states; hybrid may force DEFEND before inference | Hybrid is a safety override, not a probabilistic blend of rules and PPO |
| `bots/v13/bot.py`: constructor, `on_step`, `_record_transition` | A non-rules mode creates a neural engine only when a model path is supplied; rules evaluate before the local state is overridden; probabilities can be recorded in transitions | Require evidence that the model was actually consulted; a mode label alone is insufficient |
| `bots/v13/runner.py`: `_run_batch` | The batch label reads mode/model, but bot construction omits both | Never use this batch path as P1 evidence |
| `bots/v13/learning/trainer.py`: `run`, `_init_or_resume_model`, `_pick_model_class` | Online `model.learn` through SC2Env; optional imitation, KL and recurrent class selection already exist | Reuse and validate these paths; do not plan them as wholly absent |
| `bots/v13/data/hyperparams.json` | MlpPolicy, KL 0, imitation false, build-order reward false | Available mechanisms are not evidence of enabled mechanisms |
| `bots/v13/learning/environment.py`, `rewards.py` | Discrete six-action environment; win-probability is recorded in info; terminal and JSON-shaped rewards are computed separately | Do not describe the recorded win-probability as an already-wired delta reward |
| Checkpoint ZIP `data` JSON, read without executing serialized payloads | `v15.zip`: observation shape [55], action count 6, 23,040 timesteps; `v0_pretrain.zip`: [55], 6, 0 timesteps | Both pass a static shape precheck; neither has passed this survey's inference or scoreboard validation |
| `scripts/evolve.py`: `start_post_training_daemon`, final post-training hook | Optional training daemon start exists | A training trigger is not checkpoint compatibility validation or policy promotion |

Recurrent support needs special care: training dispatches recurrent classes, but the
inspected inference engine loads plain PPO and does not pass recurrent state between
predictions. Do not infer recurrent deployment parity from trainer support. This is a
source-level integration concern, not a reproduced failure in the two feed-forward checkpoints.

## Comparable work and its limits

These are primary author papers or repositories, checked 2026-11-16. Published win rates
are not comparable to our scoreboard: versions, maps, races, observations and difficulty
numbering differ. In particular, do not translate an old paper's level 7 into our named
VeryHard panel without checking its environment.

| Work | What RL controls / reported resources | Transferable lesson and limitation |
|---|---|---|
| [Lee et al., Modular Architecture (2018)](https://arxiv.org/html/1811.03555v1) | Build-order and tactics modules; other responsibilities scripted; modules optimized separately | Closest architectural analogy: retain dependable executors and learn one responsibility. Zerg-focused experiments, not evidence for our Protoss controller or desktop throughput; no single-machine budget verified here |
| [Liu et al., Efficient RL / mini-AlphaStar (2022)](https://arxiv.org/abs/2209.11553) | Hierarchical macro-actions and curriculum; reported 4 GPUs and 48 CPU threads; separate scaled-down AlphaStar baseline | Hierarchy reduces the search burden. “Single machine” can still mean a workstation far beyond CPU-only training; restricted-unit/map results cannot justify an AlphaStar rebuild |
| [StarTrain, author repository](https://github.com/MichalOp/StarTrain) | PySC2 feature-interface neural agent, supervised then RL; one GPU; documented default RL setup uses 32 games and 64 GB CPU RAM | A public smaller-scale route exists, but the author reports about 65% against Medium after roughly two days and documents resume limitations. General low-level action learning is not an obviously economical upgrade over v13 |
| [DanieleMosh, SB3 SC2 project](https://github.com/DanieleMosh/Starcraft-2-Reinforcement-Learning) | Handwritten expand/build/recruit/scout/attack/retreat macros selected by RL | Implementation analogue for compact action sets. README describes an approach; no controlled fixed-panel strength result or hardware budget verified here |

The evidence supports modularity as a credible approach, not a proven ranking of the
six options. This is a targeted survey, not an exhaustive competitive-bot census.

## 1. What should PPO decide?

| Candidate | Opportunity | Cost or confound | Proposed disposition |
|---|---|---|---|
| a. Six strategic states | Zero new surface; directly tests accumulated training | Broad states couple macro and combat; hybrid may hide model impact | Keep as P1 baseline and regression control |
| b. State plus target | More spatial authority | Target legality, observations, serialization, teacher labels and migration all change; 10 vs 12 remains unresolved | Hold E until P1/P3; do not silently select cardinality |
| c. Separate macro and army heads | Removes competing meanings from one state variable | Two heads trained together make attribution harder | Start with army only and frozen rules macro; defer learned macro head |
| d. Opening/build-order per game | Small action space and clear terminal outcome | Only one decision per game, still expensive samples; map/race leakage; weak mid-game authority | Optional bandit baseline, not a prerequisite or mandatory PPO project |
| e. Engagement thresholds | Direct influence on costly fights and retreats | Requires credible local-force, distance and retreat observations; oscillation risk | Express as discrete legal intents initially, not continuous threshold tuning |
| f. Legal branch selection | Excludes impossible actions; preserves execution ownership | Mask and executor can disagree; poor decisions remain legal; porting Jev is unnecessary | Use a small legality interface for the chosen surface, not Jev runtime adoption |

If masks are chosen, use the same legality calculation during rollout, inference and
evaluation, with an always-legal hold fallback and execution-time recheck. Log the mask,
requested intent, accepted intent and veto reason. PPO rollout log probabilities must
refer to the sampled action under the rollout distribution, not a fabricated replacement
label after a veto. Abort on systematic action/rollout mismatch.

[Huang and Ontañón](https://arxiv.org/abs/2006.14171) justify masking in policy gradients
and examine its importance as invalid actions grow. The [SB3-Contrib documentation](https://sb3-contrib.readthedocs.io/en/master/modules/ppo_mask.html)
requires mask-aware evaluation and environment-owned masks for SubprocVecEnv; its current
MaskablePPO does not support recurrent policies. Pin and check the installed version before
implementation. Do not assume MaskablePPO, the local KL subclass and recurrent PPO compose.

## 2. How should it learn?

First preserve the exact existing reward/config for P1. For the new surface, warm-start
from rules decisions on that same surface, then use online PPO rollouts from a frozen
candidate tree. Old six-state records are not ground truth for new army-intent labels.
Do not relabel old database actions as if those counterfactual actions had been executed.

Proposed experiment reward: terminal win/loss (ties/timeouts treated consistently with
ES) plus an optional, separately ablated bounded potential term. Keep all 63 existing
shaped rules as the legacy baseline, not as 63 simultaneous tuning dimensions. Verify
the actual rule count/config in the experiment manifest. Build-order trajectory shaping
stays off for the army experiment. Imitation and KL should be separate recorded factors;
start with the compatible existing imitation path and a declared KL schedule only if the
teacher mapping is valid. A teacher prevents early collapse but can also cap improvement.

For a win-probability potential, use `lambda * (gamma * Phi(next) - Phi(current))` with
terminal potential defined consistently, not arbitrary repeated bonuses for being ahead.
The [Ng, Harada and Russell shaping result](https://people.eecs.berkeley.edu/~russell/papers/icml99-shaping.pdf)
motivates this form under its MDP assumptions. Our partial observations, heuristic score,
truncation handling and function approximation mean this is not a practical guarantee.
Run terminal-only versus potential shaping with otherwise identical budgets before
crediting the shaping. Give-up behavior must be frozen and logged in both arms.

## 3. Who should it train against?

Use the built-in AI curriculum on the ES Linux substrate first. Choose training difficulty
from observed learning signal; do not train only against a stage where both agents always
lose. Keep evaluation at the declared fixed ES stage even when training uses easier games.
Separate training, checkpoint-selection and final evaluation seeds/manifests. Never adjust
the evaluation mix because a new opponent happens to flatter a policy.

After measurable built-in-AI progress, add one frozen rules opponent to test robustness.
PFSP-lite is a later response to demonstrated specialization or cycling, not a prerequisite
for the first gain. Frozen-opponent curricula need alternating seats and explicit opponent
policy/config hashes. They double the player processes and change throughput; measure that
cost before widening the pool. No archived self-play win rate substitutes for ES results.

## 4. How is it judged?

P1 starts only after the Stage 0 scoreboard is ready and calibrated (ES.2–ES.4). Compare
v13 rules, v13 hybrid/v15, and v13 hybrid/v0_pretrain on the same frozen code, exact maps,
race/difficulty cells and paired seeds. Ten cases per race gives 30 cases per arm, 90 games
total if both checkpoints qualify. This is a proposed small screening panel, not a
power calculation or authorization to run. Keep its manifest separate from the six-version
180-game Stage 0 baseline. Do not use `runner --batch`.

Before a game, validate checkpoint hash, observation names/order/scales, build-order slot
registry, action semantics, policy class and real inference. Fail closed on missing model
or fallback. Record the loaded mode, override rate and evidence of model invocation.
Freeze advisor off, fog normal, map hashes, SC2 build, timeout/resign settings and all
rules/config files. Deterministic action selection plus paired seeds reduces variation;
it does not make SC2 outcomes mathematically deterministic.

Report ES intention-to-test rates and completed-only rates, per-race results, individual
paired outcomes and missing scheduled games. Use uncertainty on the paired difference;
separate Wilson intervals on two marginal rates are not a paired test. A null or wide
interval means “no demonstrated gain at this budget,” not “PPO can never help.” Two
checkpoint comparisons are exploratory; confirm a selected winner on unused seeds.

### Proposed P3 preregistration (for operator selection after P1)

- **Claim:** with rules macro/execution held fixed, learned legal army intent improves
  intention-to-test win rate by at least 10 percentage points over the same tree's rules
  controller on a declared ES panel.
- **Budget:** proposed cap of three training seeds, at most six training hours per seed
  on one reserved machine; separate fixed evaluation budget of 60 paired cases for the
  selected checkpoint versus rules (120 games). No automatic extension; runtime limits
  and any smaller operator cap win. Selection uses training/validation data only.
- **Advance:** observed paired gain at least +10 points, one-sided 95% lower bound on
  paired gain above zero, no race's observed regression exceeding 10 points, and valid
  action/execution evidence. This conservative screen is not production promotion.
- **Kill this spike:** any persistent provenance/legality failure; or at the cap, gain
  fails the advance rule. Record negative versus statistically inconclusive separately.
  If valid baseline results are all-win/all-loss, the panel cannot discriminate: return
  for panel/stage selection before training, never move the bar after seeing treatment.
- **Controls:** same-tree rules, existing six-state P1 result, and a rules army adapter
  parity check. A random legal policy is a calibration candidate, not an extra paid run
  authorized by this document. New policy evaluation does not alter the ES promotion gate.

Exact army intents, observation fields, cadence, hysteresis, masking implementation,
teacher mapping and these budgets remain P3 choices. No experiment is dispatched here.

## 5. How does it combine with evolve?

Treat `(code + configs + observation schema + action schema + policy hash)` as the tested
artifact. Evolve may change semantics without changing tensor width. Stamp feature names,
normalization, z-slot mapping, policy class and library versions in a checkpoint manifest.
Reuse weights only after semantic compatibility and a small fixed regression panel pass;
otherwise retrain or explicitly opt into a separately tested migration. Never zero-pad a
model and label it equivalent. Avoid updating code while collecting a PPO rollout.

Promote rules code and evaluate a newly trained policy as distinct steps. The existing
post-training hook may request training; it must not make an unevaluated checkpoint the
deployed winner. Preserve the last tested policy/code pair and make rollback explicit.
The current hook and trainer are reuse candidates, not proof this contract is implemented.

## 6. Where does it train?

D16 already settles the platform: headless WSL Linux SC2 4.10 for training and measurement;
Windows live games only for spot checks. Pin the lockfile, SC2 binary and maps. Benchmark
environment steps/game-hours and learner update time separately before proposing GPU
work or more workers. An MLP learner may be cheap while SC2 simulation dominates; the
seed's “CPU PPO limits games/hour” remains a hypothesis. Historical four-way throughput
is not present-machine capacity or authorization. Do not extrapolate Linux measured
strength to a newer Windows patch; label Windows demonstrations separately.

## Held-step disposition proposals

These are recommendations to main for P3, not status changes. PPO.0 remains incomplete.

| Existing step | Proposed disposition | Reason / trigger |
|---|---|---|
| M1 | Rewrite if shaping selected; otherwise retire from this experiment | Reward variance is diagnostic, not the success measure; use ES gain |
| M2 | Rewrite | Promote the tested code/policy pair after ES evidence; remove a shaping-specific Elo shortcut |
| E.0 | Rewrite | Decide the actual selected action semantics/cardinality at P3; neither 10 nor 12 is presumed |
| E.1 | Rewrite if army intent accepted | Isolate army authority instead of automatically crossing all states with targets |
| E.2 | Hold / rewrite only if multiple heads are selected | A sequential target head is unnecessary for the proposed first slice |
| E.3 | Keep intent, rewrite scope | Version observation/action semantics and checkpoint provenance together |
| E.4 | Rewrite | Teacher and execution mapping for the chosen surface; reuse existing optional KL implementation |
| E.5 | Retire automatic 6-to-12 relabeling proposal | Preserve historical data; collect actual actions for the new schema |
| E.6 | Rewrite | Same tested-artifact promotion contract as M2; avoid duplicate promotion steps |
| 6.1 | Rewrite later | Frozen-opponent curriculum after built-in-AI evidence; preserve ES evaluation |
| 6.2 | Hold | PFSP only after evidence that a small fixed pool is insufficient |
| 6.3 | Keep, rewrite criterion | Demonstrate gain on the fixed ES panel, with cross-version games secondary |
| 7.6 | Hold | Revisit only if the advised loop is retained in the chosen PPO workflow |
| ER.8 | Keep conditional intent | Seat-alternation correctness is required before activating PFSP, not optional once active |

## Handoff and validation

P2 answers all six seed questions. Next is P1 after scoreboard readiness, then an operator
choice of surface/reward/curriculum/budget and held-step dispositions. No recommendation
above changes a canonical plan or authorizes games. Validation commands and results are
recorded in [the companion validation receipt](ppo-validation-2026-11-16.md).
