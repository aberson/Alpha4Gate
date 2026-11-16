# Jev v2 recovery stall - 2026-10-11

## Diagnosis

**Symptom (verbatim):**
"Gathered at our base, with winning numbers, all it had to do was attack move to
the enemy to win. However it was just amassing forces."

**Reproduction:**
The live observation is run `e06637818c1b4341a213e99082b91ffd`, v2, Typesafe,
Terran difficulty 3, seed 11. The operator ended this match; its raw Victory
result is excluded from performance acceptance.

An offline coordinator reproduction uses the existing decision-test fixtures,
16 ready Zealots at home, no visible enemies, a scripted provider repeatedly
answering army intent `attack` and strategy `recover`. It polls the real
`HostedQuestions` coordinator for 140 game seconds. This isolates arbitration
and renewal; it does not reproduce SC2 movement or establish battlefield strength.

Command: `uv run python .build-step/jev-live-validation/repro_recovery.py`

Observed output:

```text
ready_combat_units=16; position=home; visible_enemies=0
recovery_contiguous=True
recovery_duration_game_seconds=137.5
final_army_intent=attack
final_strategic_posture=recover
posture_hold_until=162.5
network_requests=0 (scripted provider)
```

**Primary sources:**

- `src/jev/strategy.py:78`: an army posture persists until a newer answer,
  loss of executability, or 30 game seconds **without renewal**.
- `src/jev/strategy.py:953`: `_strategy_options` always offers implemented
  army postures after the first attack launches.
- `src/jev/strategy.py:1248`: `_apply` creates a new `_Held` with the current
  game time on every accepted strategy answer, including repeated recover.
- `src/jev/strategy.py:1301`: expiration measures time since that latest
  acceptance. There is no recovery-completed condition here.
- `bots/jev/v2/policy.json:3563`: `army.recover` precedes attack, succeeds when
  the hosted posture says recover regardless of army size, and moves all combat
  units home with preemption. The few-units condition is an alternative trigger,
  not a condition limiting a hosted recovery choice.
- `tests/test_jev_decision.py:1147`: existing hold-expiry coverage explicitly
  supplies only one recovery answer, with no renewal afterward.
- Archived `events.1.jsonl` lines 3914, 4280, 4603, 4935, 5458, 6171, 6760 and
  7319 record accepted recover requests 28/34/43/51/61/71/81/90. The accepted
  times span 206.205 to 339.286 game seconds, with all gaps below 30 seconds.
  The last hold deadline becomes 369.286, after the interrupted match ends.

**Suspected root cause:**
Recovery is a renewable strategic posture, not an activity with a completion
condition. Repeated answers can keep the army home indefinitely, even after it
has regrouped and grown. The graph intentionally gives that posture priority over
an attack intent. This is consistent with the documented implementation; changing
it requires a gameplay-policy improvement rather than repairing a broken timer.
Defense also issued commands during the live interval (126 defense commands,
92 recovery moves), so repeated recovery is not proof that every moment at home
was unjustified. The archive does not prove that an attack would have won.

**Confidence:** high for the renewal/arbitration mechanism; medium that changing
recovery alone resolves all of the observed passivity.

**Ruled out (limited to this mechanism):**

- A stalled service is unnecessary to reproduce it: the offline provider answers
  immediately, and the live archive recorded 95 answers with no service failures.
- Army intent `attack` alone cannot resolve a held recovery posture: the offline
  reproduction has both simultaneously, and the policy explicitly prioritizes
  recovery. Simply changing the movement primitive would not release that posture.

**Memory entries that apply:**

- `feedback_attack_walking_vs_moving.md`: check actual movement versus attack
  orders before treating passivity as model quality alone.
- `feedback_verify_primary_source_in_writing.md`: use source and raw events,
  distinguishing operator observation from measured evidence.

## Independent confirmation and verification

A fresh, no-history, read-only diagnostic agent independently identified the same
renewal and graph-precedence mechanism, with high confidence. Before dispatch,
a non-mutating probe reported no parent messages, diagnosis or challenge token
visible; this is a context boundary, not OS isolation. SHA-256 snapshots of the
allowed source, test, policy and archived-run files matched after diagnosis.

The independent trace inspection also located homeward moves for 15 combat-unit
tags at game second 337.054 (`events.1.jsonl` lines 7202-7230). The terminal tick's
attack attempt followed a stale strategy answer releasing the posture; it does
not establish a normal recovery completion or a successful attack.

Existing behavior checks:
`uv run pytest tests/test_jev_decision.py -k 'applied_posture or currently_executable_choices' -q -p no:cacheprovider`
returned **5 passed, 93 deselected**. These confirm current semantics, not a fix.
No gameplay source or policy was changed and no additional hosted request was made.

## Next-work seed

Plan a bounded recovery lifecycle for Jev v2 before resuming hosted acceptance:
define observable recovery progress and completion, expose elapsed recovery and
readiness to the model, and prevent equivalent repeated answers from indefinitely
renewing a completed recovery. Reconcile strategic posture with tactical intent
explicitly. Preserve immediate defense and the local small-army safety rule;
do not force attacks solely because a timer expires or treat fog as proof of safety.
Show why recovery continues or ends in the existing decision trace/viewer.

Acceptance should cover repeated recover answers with a rebuilt army, genuine
home threats, losses during recovery, and conflicting attack/recover answers.
First use offline fixtures; then resume a bounded live validation with the
dashboard ready before SC2. Keep Step 222 pending, the frozen v1 baseline intact,
and the held-out panel unstarted.

This is a planning seed, not an approved detailed design or an implemented fix.


## Follow-up, 2026-10-12

The bounded recovery lifecycle is now implemented and tested; see
[recovery completion evidence](jev-recovery-completion-2026-10-12.md).
The original diagnosis and raw-run limitations above remain historical evidence.
