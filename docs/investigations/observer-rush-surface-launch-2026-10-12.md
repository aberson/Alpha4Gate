# Observer Rush browser surface launch failure

## Diagnosis

**Symptom:** `RuntimeError: Could not locate the browser content surface` from
`scripts/probes/jev_battle_view.py:505`, desktop run
`7fb69c8de90a43898cbd6ceb2930ebeb`.

**Reproduction:** The failed run archive contains before-game.png (page rendered),
ready marker, live camera observations and diagnostics stopped cleanly at
3.392857 game seconds. The source raises the exact reported error when its single
EnumChildWindows pass has no Chrome_RenderWidgetHostHWND descendant. A separate
real Edge/pygame attachment probe currently finds that child; the original
intermittent absence is not independently reproduced. Its measured client was
1186x793 versus viewport1178x662, showing residual horizontal chrome also matters.

**Primary sources:** probe lines397-505 load the graph, find/reparent the browser,
start SC2, then impose the internal renderer class requirement; cleanup lines750+
requests normal game stop. Failed run diagnostics and battle/before-game.png.

**Suspected root cause:** brittle dependency on a Chromium implementation window
class that need not be present at the one observation time. Global title-only
browser lookup is also ambiguous across multiple launches. Failure artifacts do
not distinguish renderer recreation from another missing-child cause.

**Confidence:** medium in underlying transient cause; high in the exact abort
condition. Independent read-only diagnosis agrees. Its suggestion that pygame has
an identical title is not exact: pygame includes a middle dot, while the lookup
string does not. Do not report wrong-host selection as proven.

**Memory entries:** feedback_sc2_process_management.md (never force-kill SC2),
feedback_dashboard_playwright_networkidle.md (wait for content, not network idle).

## Fix design

Use measured browser client/viewport geometry instead of a required internal
renderer child. Account for symmetric remaining side/bottom frame; reject invalid
or unsettled dimensions after bounded retries. Identify browser by unique run title
and Chromium top-level class, excluding the pygame host. Keep actual SC2 final
client-position/size verification and normal cleanup unchanged.

Files: scripts/probes/jev_battle_view.py and tests/test_jev_battle_launcher.py.
Regression scope: missing renderer child, horizontal frame geometry, invalid
geometry, unique title/class selection and production wiring. Risk: unusual Edge
chrome/DPI modes; final geometry verification and a real desktop-path launch are
required. Rollback: reverse only these fix hunks, preserving earlier workspace
changes.

The developer works in an isolated worktree seeded with the current viewer/pacing
prerequisites. Existing main changes remain untouched. The no-history conversation
challenge v2 positive control and both child comparisons passed; independent
investigation did not edit allowed source files.


## Verification

The original missing-child branch was executed with an empty child enumeration:
`RuntimeError: Could not locate the browser content surface` reproduced exactly.
The replacement functional tests intentionally provide no child-enumeration API;
measured origin succeeds, and invalid geometry retries/fails with dimensions.

18 launcher tests pass in the isolated worktree and main checkout; focused Ruff
passes. Scoped mypy has existing probe/dependency errors (baseline16/current15),
so no clean typecheck or full-suite claim is made. Independent correctness, bugs,
security and test reviews passed. A low style finding in new tests was corrected
by formatting only that added block, with equal Python AST before/after. The
browser-inset symmetry concern was investigated: a real Edge measurement gave
client origin1127,320, client1186x793, viewport1178x662 and renderer origin1131,447,
exactly matching computed offset4,127. Unusual future browser chrome remains a
platform limitation, not a proven current failure.

Actual desktop-script smoke: scripts/launch-jev-battle.ps1, original real-time
mode, with only safety caps30 game/120 wall seconds and1 maximum hosted request.
Run `352fde6653284b0e83253f885b54dd30` reached Live and logged:

```text
Game pane fit verified (28, 134) (1053, 788)
Battle ready. Use Stop battle to leave cleanly.
```

Native screenshot verified SC2 fits the game pane. The internal renderer-class
lookup is no longer on the launch path. The browser child title includes its run
ID; the outer Observer Rush name and both desktop shortcut targets stay unchanged.
Only the two fix files were applied from the isolated worktree; earlier gameplay,
viewer and documentation work is preserved.
