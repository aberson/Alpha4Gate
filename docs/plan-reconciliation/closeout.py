"""Finalize reviewed residual mapping and durable reconciliation reports once."""
from pathlib import Path
import json,re,hashlib,datetime,html,os,subprocess,ast
R=Path(__file__).resolve().parents[2]; D=R/'docs/plan-reconciliation'
def write(p,t): p.write_text(t,encoding='utf-8',newline='\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
m=json.loads((D/'migration.json').read_text()); p=R/'docs/plans/master_plan.md'; text=p.read_text(encoding='utf-8').replace('T.2A','T.2.1')
text=text.replace('(src/orchestrator/ladder.py)','(../../src/orchestrator/ladder.py)')
text=text.replace('Two to five focused maps, such as concave formation and worker defense','Two to five focused maps from the investigation priority list: blink-stalker micro, attack-concave, force-field choke, storm-clump and phoenix-vs-mutas')
text=text.replace('score reliability before further maps.','score reliability before further maps. Preserve per-race subdirectories for G extensions; kill if custom-map cost is unjustified versus PySC2 alone.')
text=text.replace('implement only that role with cost and empirical validation, not three parallel architectures.','wire only the chosen role(s), with cost and empirical validation: scorecard into per-version manifest, gate into fitness/pre-gate, reward into curriculum pretraining. If none is warranted, collapse into H.')
rows=json.loads((D/'steps.json').read_text()); data=json.loads((R/'docs/plans/build-estimates.json').read_text())
for r in rows:
    if r['step_id']=='T.2A': r['step_id']='T.2.1'
for u in data['units']:
    if u['step_id']=='T.2A': u['step_id']='T.2.1'
extra=[
('SV.1','Resolve shared self-play detach and orphan cleanup','The April18 #147 soak had only7records after Esc atgame8, an orphan, and partial detach-continuation. EV intentionally did not change scripts/selfplay.py/shared run_with_batch contract, which still sets stop_event. Preserve the deferred detach_only API improvement and remove misleading orphan warning only with real owned-lifetime proof. Revalidate against current implementation before change; never reproduce historical manual SC2 killing.',(6,14,.75,2),[]),
('SV.2','Repair threaded viewer interrupt ownership','EV explicitly leaves worker-thread Ctrl+C/SC2 cleanup unsafe; proper fix requires orchestrator selfplay outside its original read-only scope. Design owned graceful interruption without installing main-thread signals from workers or killing arbitrary SC2. Test actual lifetime boundary; keep current documented no-Ctrl+C rule until proven.',(8,18,1,2),['SV.1']),
('SV.3','Revisit viewer sizing on the operator display','April18 operator found even small preset overwhelming. Current presets still have1024x768panes. Preserve smaller/parametric sizing discussion, layout/input/reparenting validation; no automatic geometry preference selected by migration.',(5,12,1,2),[]),
('SV.4','Audit viewer surrender-label cosmetic leak','April18 screenshot/text records V0 surrendered broodwar. Find current message producer and source enum; remove leaked implementation label if still present, preserve flavor text. Close only with current evidence or explicit supersession.',(1,3,.25,.5),[]),
('SV.5','Consider nonblocking HWND discovery','The 15s GAME_START_HWND_TIMEOUT_SECONDS hotfix blocks pygame per game. Deferred per-frame lookup must preserve bounded discovery, pane ownership, callback chaining and exit cleanup; verify no UI stall and no stale HWND attachment.',(5,12,.5,1.5),['SV.1']),
('SV.6','Resolve legacy self-play viewer acceptance residuals','Original2screenStep9/#147 was superseded as a design, but April18 evidence is4PASS/1PARTIAL/1FAIL/1N/A, not seven passes. Map current detach/orphan fixes and observe HWND count over10reparent cycles (+/-20UserObjects), resumed batch completion and no orphan; preserve actual prior pass evidence for panes/overlay/resize/placeholder. A triage record with follow-up identities is an allowed source completion alternative. Do not invent a new requirement to kill SC2.',(1,3,1,3),['SV.1','SV.2']),
('UI.7','Resolve historical live-smoke acceptance provenance','Old ui-live-test plan Step7 records real commands/live-data verification and runtime reviews but no final per-check smoke verdict. Locate durable PASS or explicit retirement under later dashboard redesign; otherwise preserve the unresolved end-to-end gate against current equivalent surfaces. Do not resurrect removed frontend components or old5173port.',(.5,2,.5,1),[]),
('UI.8','Resolve historical visual-check acceptance provenance','Old ui-live-test Step8 checklist includes stats, command history, build/attack execution, human_only and ai_assisted switching, mute. Some actual live fixes are recorded, but not a complete final checklist. Resolve each through later evidence or explicit supersession; remaining genuine human checks stay pending rather than inferred from521unit tests.',(.5,2,.5,1),['UI.7']),
]
chunks=[]
for ident,title,body,hrs,deps in extra:
    source='documentation/archived/selfplay-viewer-2-screen-plan.md' if ident.startswith('SV') else 'documentation/archived/ui-live-test-plan.md'
    dest=next(x['new_path'] for x in m['sources'] if x['original_path']==source)
    status='HELD — historical residual; current closure or activation decision required'
    chunks.append(f"### Step {ident}: {title}\n\n- **Status:** {status}\n- **Type:** {'operator' if ident in {'SV.6','UI.7','UI.8'} else 'code'}\n- **Issue:** {'#147 (legacy provenance)' if ident.startswith('SV') else 'not assigned'}\n- **Depends on:** {', '.join(deps) or 'none outstanding'}\n- **Source:** [{Path(dest).name}](../{dest.removeprefix('docs/')}); also [April18 soak](../soak-test-runs/soak-2026-04-18-selfplay-viewer.md).\n\n{body}\n\n")
    row=dict(step_id=ident,original_step_id='2screen Step9/#147 residual' if ident.startswith('SV') else 'UI Step'+ident[-1],title=title,type='operator' if ident in {'SV.6','UI.7','UI.8'} else 'code',status=status,dependencies=deps,hours=hrs,rationale=f"Range covers {title.lower()}; existing evidence and current shared-viewer/legacy-UI drift determine whether this is closure review or corrective implementation. No unattended game hours counted.",issue=147 if ident.startswith('SV') else None,hold_reason='Historical partial/unverified acceptance; revalidate current code and later evidence',source=source)
    rows.append(row); a,b,c,d=hrs
    data['units'].append({k:row[k] for k in ('step_id','original_step_id','title','status','dependencies','hold_reason')}|dict(enabled=False,basis='model',model_id='Codex session; runtime model not exposed',estimated_at=data['estimated_at'],confidence='low',worker_hours={'low':a,'high':b},attention_hours={'low':c,'high':d},rationale=row['rationale'],source_path=dest,source_step_id=row['original_step_id']))
text=text.replace('## 8. Jev campaign contracts',''.join(chunks)+'## 8. Jev campaign contracts')
write(p,text)
for name in ['linux-dev-environment.md']:
    f=R/'docs/wiki'/name; t=f.read_text(encoding='utf-8')
    t=re.sub(r'\[`(feedback_wsl_[^`]+)`\]\([^)]*\)',r'`\1` (historical personal-memory reference; not distributed with this repository)',t)
    write(f,t)
# Live HTML documentation links must resolve from their moved location, including support reports.
for entry in m['sources']:
    f=R/entry['new_path']
    if entry['immutable'] or f.suffix!='.html': continue
    t=f.read_text(encoding='utf-8'); old=entry['original_path']; new=entry['new_path']
    def fix(mt):
        url=mt.group(2)
        if re.match(r'\w+://|#|data:',url):return mt.group(0)
        bare,sep,frag=url.partition('#')
        # First resolve original coordinate; already corrected root literals resolve directly.
        resolved=os.path.normpath(str(Path(old).parent/bare)).replace('\\','/')
        dst=next((x['new_path'] for x in m['sources'] if x['original_path']==resolved),None)
        if dst is None and (R/Path(new).parent/bare).exists():return mt.group(0)
        if dst is None and (R/resolved).exists():dst=resolved
        if dst:
            target=os.path.relpath(dst,str(Path(new).parent)).replace('\\','/')+(sep+frag if sep else '')
            return mt.group(1)+target+mt.group(3)
        return mt.group(0)
    t=re.sub(r'((?:href|src)=["\'])([^"\']+)(["\'])',fix,t)
    write(f,t)
digest=sha(p); data['plan_sha256']=digest
for u in data['units']:u['plan_sha256']=digest
data['excluded_completed']+=' Browser fixture repair03c552a: nine opt-in tests passed; old alpha4gate.runner skill refs already absent.'
write(R/'docs/plans/build-estimates.json',json.dumps(data,indent=2));write(D/'steps.json',json.dumps(rows,indent=2))
write(R/'docs/plans/build-estimates.md','# Alpha4Gate estimates\n\nProspective active worker/attention hours, not measured history or elapsed game time. Held/deferred work disabled. Each leaf has its own range/rationale in JSON. Raw master SHA256: `'+digest+'`.\n\n| Step | Worker h | Attention h | Status |\n|---|---:|---:|---|\n'+'\n'.join(f"| {u['step_id']} | {u['worker_hours']['low']}–{u['worker_hours']['high']} | {u['attention_hours']['low']}–{u['attention_hours']['high']} | {u['status']} |" for u in data['units'])+'\n')
m['master_sha256']=digest;m['pending_steps']=len(rows);write(D/'migration.json',json.dumps(m,indent=2))
inventory=[]
for entry in m['sources']:
    if not entry['immutable']:continue
    mapped=[r['step_id'] for r in rows if r['source']==entry['original_path']]
    inventory.append(f"| {entry['original_path']} | {entry['new_path']} | {', '.join(mapped) or 'completed/superseded historical evidence; see disposition below'} |")
report='''# Plan merge reconciliation

Scope: documentation reconciliation, not a build. Source revisions were read from the working tree, preserved byte-for-byte in completed/archived-plans, and mapped by SHA256. The existing dirty derived task-state/current.md and dev.code-workspace remain untouched. Source-map data covers every migrated file, including untracked raw media; .gitignore now follows those media paths.

## Four overlap classes and disposition

1. Exact duplication: old master phase summaries and detailed plans resolve to one canonical leaf; original numeric/phase/issue identities retained. Manual UAT duplicates JV207/208, J2 222 and EV4 map to those leaves, not extra copies.
2. Subsumption: PhaseR SPRT was rejected and superseded by shipped EJ posterior; Wilson reporting remains R1. Baseline corpus does not subsume PFSP ancestry regression (ER4). EH1/EH2 already close lineage-writer/persist work, so EH9 is narrowed to real loop→disk→API/unscheduled-third-record proof. Browser follow-up03c552a has later actual9/9 evidence and is excluded. alpha4gate.runner skill references no longer exist and are excluded.
3. Partial overlap: JDI236 reuses JON225–227 budget/execution substrate; JDI237 depends228 and prerequisite spending shares its single$5. JDI never closes J2/JV acceptance. EI14 may share EL7 machine time using capture-only flags while owing separate instrumentation evidence; no extra EH soak was invented. Historical2screen #147 partial/fail/N/A evidence stays held in SV1–6; EV integration does not prove shared selfplay cleanup.
4. Sequencing/conflict: EH before EI; EH6 afterEH8; EV5 after launcher/control doc fixes. E0 preserves10-enumerated-versus12-required action conflict. J2.0 retains four unowned feature groups. EI0 retains product-default review/issue-sync; ER1 truncated idea remains independent. Branch landing policy remains separate; onbrand containment is already satisfied. The historical global freeze is superseded by currentmaster's lifted state.

## Source normalization and requirements

Each source was reconciled for goal, current implementation/status, leaf requirements, hard dependencies/constraints and acceptance/rollback. Pending source bodies are retained inline; long shipped execution logs and original design histories remain exact archives. Future master-only phases are decomposed into original scoped substeps and explicit investigation/acceptance leaves; no new capability is approved. Source keys and issue IDs are not reassigned. Added IDs denote previously unowned residuals and decisions, not new features.

Completed originals: only sources the old canonical master explicitly marked complete (phases1–5/8/9/B/N, D counts, dashboard/models, evolve parallel/gate-reduction) plus JI actual hosted211 acceptance. Mixed/held plans stay archived-plans even when all code is shipped. Legacy source plans retain conservative historical disposition; UI7/8 acceptance provenance is held rather than inferred from unit tests. Cut F and superseded observer-as-third-API-client designs do not reactivate. Phase8 ceremonial close/cloud cut takes precedence over stale CLAUDE narrative.

Operational corrections: correct bare snapshot command and dynamically emitted version; skill invocation not nonexistent uv command; generations0 for soaks; actual disk-seeded lineages and baseline registry; no single-lineage fallback credited as multi-lineage; no killSC2 fault recreation. EI nine+two=eleven fields and detailed early-stop existing-test preservation supersede arithmetic/stale summary errors. Ground-only units cannot attack flying producers (tactical source correction).

## Source→canonical mapping

| Original | Preserved source | Outstanding leaf mapping |
|---|---|---|
'''+ '\n'.join(inventory)+'''

## Unresolved product decisions

- E0: ten listed legal pairs versus twelve declared actions. Choose ten and adjust all contracts, or explicitly define extra/reserved slots and masks.
- J2.0: descope or give owning implementation steps to maxdurability/regroup, availableabilities/mineral facts, worker saturation and second Assimilator.
- EI0: approve defaults and synchronize missing issues; not a revival of the lifted mesh freeze.
- ER1: clarify truncated idea(ii); evidence layer itself does not need that answer.
- BL1: land branch now versus after EV acceptance; cleanup publishes neither.
- Historical SV/UI residuals: current closure evidence or explicit retirement before renewed implementation/live work.

## Reference-only frozen-version exception

The user's explicit request to repair affected references covers shared documentation resource paths in all frozen RL versions. Only path strings/comments change; original bytes are in Git history. No gameplay AST is altered; no Jev frozen captured source, runtime data or policy is changed. Resource loads must pass for v0 through v13.
'''
write(D/'merge-decisions.md',report)
write(D/'review.md','''# Plan-review

Mode: standard. Verdict: READY WITH GAPS for reconciliation; held product work is not dispatch-ready.

Blockers to documentation consolidation: none. Significant unresolved product matters are E0, J2.0, EI0, ER1, BL1, JON225 and JDI232; historical SV/UI partial acceptance retained. Minor: archived source-relative links and line numbers remain historical, and installed-version behavior must be re-anchored before building. No new external issue state asserted.

27-check review: 1 objective retained; 2 current state dated; 3 full scope boundaries; 4 actual module ownership; 5 shared Toolkit status authority; 6 explicit source identities; 7 duplication map; 8 completed-vs-live acceptance separated; 9 hold propagation via dependencies; 10 no invented product choice; 11 prerequisites/serialization; 12 shape/producer-consumer contracts; 13 schema rollback limitations; 14 default-preservation; 15 meaningful integration tests; 16 real smoke separated; 17 timed observation separate from labor; 18 operator gates not automated away; 19 partial/invalid measurements honest; 20 credential and cumulative-budget boundaries; 21 actual model attribution unknown; 22 per-leaf ranges and confidence; 23 rollback/teardown; 24 source SHA provenance; 25 supported unit grammar; 26 append-only decisions; 27 deep routing retained for trust/persistence boundaries, never downgraded because of stale freeze prose.

Code verification is limited to present documentation-resource consumers, browser-fixture closure evidence, snapshot command shape and shared-viewer residual seams. This pass does not recertify every historical file:line claim or run live games. See validation.json for actual migration checks/test outcomes; original test counts remain historical.
''')
write(D/'wrap.md','''# Plan-wrap

Verdict: READY WITH GAPS. Completion-first gate: not complete; outstanding implementation, human acceptance, held architecture and historical residuals remain.

1 Purpose: one project master. 2 Current state: held232/225 and independent acceptance. 3 Scope: shipped/cut excluded. 4 Architecture: frozen fullstack and independent Jev ownership. 5 Interfaces: inline campaign/budget/evidence/schema contracts. 6 Dependencies: namespaced and actual shared authority. 7 Decisions: operator-picked versus cleanup defaults; unresolved scopes held. 8 Steps: exact original identities and issue aliases. 9 Tests: source Done-when retained, current baselines remeasured at build. 10 Environment: project instructions and operator guides. 11 Recovery: backups, owned-lifetime cleanup and no forcedSC2kill. 12 References: source map plus exact archive copies. 13 Handoff: master<256KiB, estimate coverage exact, historical logs separated.

Open gaps are genuine product decisions and unperformed acceptance, not missing migration work. No product-readiness PASS or fresh gameplay result is claimed. No commit, push, merge, paid launch or issue mutation occurred.
''')
cards=''.join(f'<tr><td>{html.escape(x[0])}</td><td>{html.escape(x[1])}</td></tr>' for x in [('Operator-picked','One master, docs consolidation, preserve all requirements and shared Toolkit authority'),('Agent-defaulted','Exact source archives; conservative held residuals; per-step active-hour ranges'),('Preserved hold','JDI232 and JON225 independent structural review failures'),('Product decision','E0 cardinality; J2 unowned scope; EI defaults; truncated idea; branch landing'),('No change to authority','Existing product approvals keep their original safety/acceptance boundaries')])
write(D/'proposal.html','<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Alpha4Gate plan reconciliation</title><style>:root{--color-semantic-bg:#0d1520;--color-semantic-surface:#142235;--color-semantic-text:#e6eef8;--color-semantic-accent:#66c8e8}body{background:var(--color-semantic-bg);color:var(--color-semantic-text);font:17px/1.55 system-ui;margin:3rem auto;max-width:950px;padding:0 1rem}a{color:var(--color-semantic-accent)}table{border-collapse:collapse;width:100%;background:var(--color-semantic-surface)}td{padding:1rem;border-bottom:1px solid #38516b}h1{font-size:2rem}</style><h1>Alpha4Gate: one master, preserved evidence</h1><p>Documentation consolidation is ready for review. Outstanding gameplay gates and substantive design decisions remain held.</p><p><a href="../plans/master_plan.md">Master plan</a> · <a href="merge-decisions.md">Source reconciliation</a> · <a href="review.md">Review</a> · <a href="wrap.md">Wrap</a> · <a href="../plans/build-estimates.md">Estimates</a></p><table>'+cards+'</table><p>Archived inventories remain append-only historical evidence. This proposal neither publishes the branch nor authorizes a new live run.</p></html>')
print(json.dumps({'leaves':len(rows),'master_bytes':p.stat().st_size,'master_sha256':digest}))
