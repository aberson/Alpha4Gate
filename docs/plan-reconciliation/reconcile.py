"""One-shot source-preserving reconciliation. Do not rerun after migration.json exists."""
from pathlib import Path
import re, os, json, hashlib, subprocess, datetime, html
ROOT=Path(__file__).resolve().parents[2]
REPORT=ROOT/'docs/plan-reconciliation'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,s):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(s,encoding='utf-8',newline='\n')
def git(*a): return subprocess.check_output(['git','-C',str(ROOT),*a],text=True).strip()
if (REPORT/'migration.json').exists(): raise SystemExit('Already migrated; do not rerun')
def read_source(p):
    try: return p.read_text(encoding='utf-8-sig')
    except UnicodeDecodeError: return p.read_text(encoding='cp1252')
sources={p.relative_to(ROOT).as_posix():read_source(p) for p in (ROOT/'documentation').rglob('*.md')}
before=json.loads((REPORT/'before.json').read_text())
before['all_documentation_files']=[{'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p),'bytes':p.stat().st_size} for p in (ROOT/'documentation').rglob('*') if p.is_file()]
before['dirty_derived_sha256']=sha(ROOT/'.claude/task-state/current.md')
write(REPORT/'before.json',json.dumps(before,indent=2))
def section(txt,heading):
    start=txt.index(heading)
    end=re.search(r'^## ',txt[start+len(heading):],re.M)
    return txt[start:start+len(heading)+end.start() if end else len(txt)].strip()
def steps(txt):
    lines=txt.splitlines(keepends=True); marks=[]; pos=0; fence=False
    for line in lines:
        if line.lstrip().startswith('```'): fence=not fence
        if not fence:
            m=re.match(r'^### Step (\S+?)(?::|\s)',line)
            if m: marks.append((pos,m.group(1),line.strip()))
            elif line.startswith('## '): marks.append((pos,None,line.strip()))
        pos+=len(line)
    out={}
    for i,(start,ident,heading) in enumerate(marks):
        if ident and ident not in out:
            end=marks[i+1][0] if i+1<len(marks) else len(txt)
            out[ident]=(heading,txt[start+len(heading):end].strip())
    return out
active=sorted(s for s in sources if s.startswith('documentation/plans/') and s.endswith('-plan.md'))
master_old='documentation/master_plan.md'
completed_names={'phase-1-build-plan.md','phase-2-build-plan.md','phase-3-build-plan.md','phase-4-build-plan.md','phase-5-build-plan.md','phase-8-build-plan.md','phase-9-build-plan.md','phase-b-build-plan.md','phase-n-build-plan.md','phase-d-snapshot-counts-plan.md','dashboard-refactor-plan.md','models-tab-plan.md','evolve-parallelization-plan.md','evolve-gate-reduction-plan.md','jev-typesafe-plan.md'}
mapping={}
for p in (ROOT/'documentation').rglob('*'):
    if not p.is_file(): continue
    old=p.relative_to(ROOT).as_posix()
    if old in active or old==master_old or p.name=='branch-landing-phase-ev.md' or p.name=='tactical-bugs.md':
        new=('docs/completed/' if p.name in completed_names else 'docs/archived-plans/')+p.name
    elif old.startswith('documentation/archived/'):
        if 'plan' in p.name:
            new=('docs/completed/' if p.name in completed_names else 'docs/archived-plans/legacy/')+p.name
        else: new='docs/history/'+old.removeprefix('documentation/archived/')
    elif old.startswith('documentation/plans/'):
        new='docs/plan-reconciliation/source-reviews/'+p.name
    else: new='docs/'+old.removeprefix('documentation/')
    if (ROOT/new).exists(): raise ValueError('Collision '+new)
    mapping[old]=new
immutable={v for v in mapping.values() if v.startswith(('docs/completed/','docs/archived-plans/'))}
inverse={v:k for k,v in mapping.items()}
def repair(txt,oldrel,newrel):
    def target(m):
        value=m.group(1)
        if re.match(r'\w+://|#|mailto:',value): return m.group(0)
        bare,sep,frag=value.partition('#')
        # Preserve source line suffixes but resolve the file itself.
        clean=re.sub(r':\d+(?:-\d+)?$','',bare)
        suffix=bare[len(clean):]
        resolved=os.path.normpath(str(Path(oldrel).parent/clean)).replace('\\','/')
        dest=mapping.get(resolved)
        if dest is None and (ROOT/resolved).exists(): dest=resolved
        if dest:
            rel=os.path.relpath(dest,str(Path(newrel).parent)).replace('\\','/')+suffix+(sep+frag if sep else '')
            return m.group(0).replace(value,rel)
        return m.group(0)
    txt=re.sub(r'\]\(([^)\s]+)\)',target,txt)
    # Longest path first; never replace bare plan.md substrings.
    for old,new in sorted(mapping.items(),key=lambda x:-len(x[0])):
        txt=txt.replace(old,new).replace(old.replace('/','\\'),new.replace('/','\\'))
    txt=txt.replace('documentation/','docs/').replace('documentation\\','docs\\')
    return txt
rows=[]
def add(ident,title,body,source,original=None,kind='code',status='PENDING',deps=(),hours=(2,6,.3,1),reason='',issue=None,hold=None):
    assert re.fullmatch(r'(?:[Mm]?\d+(?:\.\d+)*[A-Z]?|[A-Z]{1,4}\d*(?:\.\d+)*)',ident),ident
    if any(x['step_id']==ident for x in rows): raise ValueError('Duplicate '+ident)
    original=original or ident
    if issue is None:
        m=re.search(r'\*\*Issue:\*\*\s*#(\d+)',body)
        issue=int(m.group(1)) if m else None
    if not reason: reason='Scope-specific model estimate for '+title.lower()+'. Includes implementation/review or active evidence collection; excludes unattended game duration.'
    body=re.sub(r'^-?\s*\*\*(Status|Type|Issue|Depends on):\*\*[^\n]*(?:\n|$)','',body,flags=re.M)
    body=re.sub(r'^\*\*Depends on\.\*\*[^\n]*(?:\n|$)','',body,flags=re.M)
    body=repair(body,source,'docs/plans/master_plan.md')
    # Embedded original headings are descriptive; only canonical unit headings are parsed.
    body=re.sub(r'^(#{1,6}) Step ',r'\1 Source requirement ',body,flags=re.M)
    rows.append(dict(step_id=ident,original_step_id=original,title=title,type=kind,status=status,dependencies=list(deps),hours=hours,rationale=reason,issue=issue,hold_reason=hold,source=source,body=body))
def take(file,ids,hours,deps=None,status=None):
    src='documentation/plans/'+file; bs=steps(sources[src])
    for ident in ids:
        heading,body=bs[ident]
        m=re.search(r'\*\*Type:\*\*\s*(\w+)',body)
        kind=m.group(1) if m else ('operator' if ident=='E.6' else 'code')
        st=(status or {}).get(ident,'PENDING')
        ds=(deps or {}).get(ident,[])
        add(ident,heading.split(':',1)[1].strip() if ':' in heading else heading,body,src,kind=kind,status=st,deps=ds,hours=hours[ident],hold=st if st.startswith(('HELD','DEFERRED','BLOCKED')) else None)

take('jev-decision-integration-plan.md',[str(i) for i in range(232,241)],{
'232':(12,28,1.5,4),'233':(10,18,1,2),'234':(12,22,1,3),'235':(14,26,1.5,3),'236':(14,24,1,3),'237':(1,3,1,3),'238':(1,2,.5,1.5),'239':(2,4,1,2),'240':(3,7,.5,1.5)},
deps={'233':['232'],'234':['233'],'235':['234'],'236':['235','225','226','227'],'237':['236','228'],'238':['237'],'239':['238'],'240':['239']},status={'232':'HELD — three failed review rounds; structural audit, no fourth patch round'})
take('jev-overnight-improvement-plan.md',[str(i) for i in range(225,232)],{
'225':(10,24,1,3),'226':(10,20,1,2),'227':(16,32,1.5,4),'228':(1.5,3,1,2.5),'229':(1,3,.5,1.5),'230':(14,28,1,3),'231':(1.5,4,.5,2)},
deps={'226':['225'],'227':['226'],'228':['227'],'229':['228'],'230':['229'],'231':['230']},status={'225':'HELD — original three-review authenticity/execution-authority failure; bounded prospective round only after reorientation'})
take('jev-player-plan.md',['207','208'],{'207':(.5,1.5,.5,1.5),'208':(1,3,1,3)},deps={'208':['207']})
take('jev-v2-plan.md',['213','222','223'],{'213':(1,3,1,2),'222':(1.5,4,1.5,4),'223':(2,5,1,3)},deps={'223':['222','J2.0']},status={'213':'DEFERRED — operator stopped baseline; new testing request required'})
for ident,title,body,hrs in [
('J2.0','Decide ownership of unbuilt J2 scope','The J2 build outcome explicitly leaves max health/shield facts and 40% durability regroup, available_abilities and mineral_contents facts, saturation-based workers beyond probe_target 16, and an Assimilator at the natural without an owning step. Decide descope versus separate owning implementation before 223; preserve each requirement. Existing code completion is not evidence these shipped.',(1,3,1,2)),
('J2.1','Reconcile branch-focused browser fixture follow-up','Source 57f7274 leaves 7/9 opt-in browser checks: two fixtures use removed legend counts. Inspect subsequent changes for actual closure evidence; otherwise adapt focused/full-graph selection, simultaneous activity, cross-branch reveal, old v1, live/offline/stale/final and reduced-motion checks. Run all nine mocked-game/service browser checks; no paid match. Default-suite success does not close this item.',(2,5,.25,1))]:
    add(ident,title,body,'documentation/plans/jev-v2-plan.md',kind='operator' if ident=='J2.0' else 'code',status='HELD — explicit scope decision' if ident=='J2.0' else 'PENDING',hours=hrs,hold='Unowned design requirements need operator disposition' if ident=='J2.0' else None)
take('evolve-operational-hardening-plan.md',['EH.'+str(i) for i in range(3,11)],{
'EH.3':(2,5,.25,.75),'EH.4':(8,16,.75,2),'EH.5':(3,7,.5,1),'EH.6':(3,6,.25,1),'EH.7':(8,16,.75,2),'EH.8':(8,18,1,2.5),'EH.9':(4,9,.5,1),'EH.10':(1,2,1,2)},deps={'EH.5':['EH.4'],'EH.6':['EH.8'],'EH.10':['EH.3','EH.4','EH.5','EH.6','EH.7','EH.9']})
take('evolve-evidence-layer-plan.md',['EI.'+str(i) for i in range(1,15)],{
'EI.1':(8,15,.5,1.5),'EI.2':(3,6,.25,.75),'EI.3':(5,9,.5,1),'EI.4':(3,6,.25,1),'EI.5':(7,14,.75,1.5),'EI.6':(6,11,.5,1.5),'EI.7':(5,10,.5,1),'EI.8':(6,12,.5,1.5),'EI.9':(6,12,.5,1.5),'EI.10':(3,6,.25,.75),'EI.11':(7,14,.5,1.5),'EI.12':(4,8,.5,1),'EI.13':(6,12,.5,1),'EI.14':(1.5,3,.5,2)},deps={
'EI.1':['EI.0','EH.10'],'EI.2':['EI.0','EH.10'],'EI.3':['EI.1','EI.2'],'EI.4':['EI.1'],'EI.5':['EI.0','EH.10'],'EI.6':['EI.0','EH.10'],'EI.7':['EI.0','EH.10'],'EI.8':['EI.6'],'EI.9':['EI.6'],'EI.10':['EI.0','EH.10'],'EI.11':['EI.9'],'EI.12':['EI.1','EI.3','EI.11'],'EI.13':['EI.1','EI.2','EI.3','EI.4','EI.5','EI.6','EI.7'],'EI.14':['EI.11','EI.12','EI.13','EL.7']})
add('EI.0','Approve evidence-layer defaults and synchronize issues','EI remains planned, not product-approved by this cleanup. Review/redline the existing choices and mint its fourteen missing issue IDs before implementation. The historical mesh freeze was lifted; it is not a present blocker. Do not downgrade EI.1/EI.5/EI.6 deep review. Resolve reach of stack-model into evolve_inject_one explicitly. This planning decision is independent of the truncated idea (ii).','documentation/plans/evolve-evidence-layer-plan.md',kind='operator',status='HELD — product decisions and issue synchronization pending',hours=(1,3,.75,2),hold='EI defaults not operator-ratified')
take('evolution-lines-plan.md',['EL.7'],{'EL.7':(1,3,.75,2)},deps={'EL.7':['EH.10']})
take('evolve-judging-plan.md',['EJ.7','EJ.8'],{'EJ.7':(1,2,1,2),'EJ.8':(1.5,3,.5,1.5)},deps={'EJ.8':['EJ.7','EH.6','EH.7']})
take('evolve-viewer-plan.md',['EV.4','EV.5'],{'EV.4':(1.5,3,1.5,3),'EV.5':(1,3,.5,2)},deps={'EV.5':['EV.4','EH.6','EH.7','EV.6']})
add('EV.6','Refresh viewer stop instructions after EH reader lands','Sibling issue #302 owns the five legacy not-wired claims. Update live operator/wiki instructions to the implemented stop behavior only after EH.4/EH.5; preserve archived pre-change evidence. EV.5 requires EH.6. Viewer close still detaches, and never use Ctrl+C for the threaded evolve viewer. EH P-7 prevents rewriting old gate evidence.','documentation/plans/evolve-operational-hardening-plan.md',deps=['EH.4','EH.5','EH.6'],hours=(1,2,.25,.5),issue=302)
take('phase-d-build-plan.md',['M1','M2'],{'M1':(1,3,1,2),'M2':(.75,2,.5,1.5)},deps={'M2':['M1']})
for r in rows:
    if r['step_id']=='M2':
        r['body']=r['body'].replace('uv run python scripts/snapshot_bot.py --from current','uv run python scripts/snapshot_bot.py')
        r['body']=r['body'].replace("Set-Content bots/current/current.txt 'v14'",'# Do not manually repoint current: bare snapshot resolves the actual version and performs its pointer update.')
        r['body']+='\n\nCanonical correction from the operator runbook: use the actual emitted new version and previous version in the ladder command; v14/v13 are historical examples, never reserved identities. Never pass --from current (that is the four-file shim). Snapshot only after M1 passes; preserve source stack and reward backup.'
take('phase-6-build-plan.md',['6.1','6.2'],{'6.1':(6,12,.5,1.5),'6.2':(6,14,.5,1.5)},deps={'6.2':['6.1']},status={'6.2':'DEFERRED — optional PFSP stretch'})
add('6.3','Demonstrate cross-version PPO improvement','After 6.1 (and 6.2 only if chosen), run the multi-hour first cycle: vN → vN+1 → vN+2 with increasing Elo and at least one higher SC2-AI difficulty than v0. Stop/re-audit curriculum and reward if ladder cycles across three snapshots. This is PPO-driven and independent of discrete evolve; exclusive use of current working tree. Phase7 establishes there is no advised_run_state.json; re-anchor any future telemetry writer, do not pretend the older Phase6 path is implemented.','documentation/plans/phase-6-build-plan.md',kind='wait',deps=['6.1'],hours=(1,3,.5,2))
heading,body=steps(sources['documentation/plans/phase-7-build-plan.md'])['6']
add('7.6','End-to-end advised staleness/soak validation',body.replace('uv run improve-bot-advised','/improve-bot-advised'),'documentation/plans/phase-7-build-plan.md',original='Phase7 Step6',kind='operator',hours=(1.5,3,1,2),issue=280)
add('E.0','Resolve action-space cardinality before implementation','The source enumerates ATTACK main/natural/third (3), EXPAND own_natural/own_third/own_fourth (3), and DEFEND/FORTIFY/OPENING/LATE_GAME singleton states (4), while requiring Discrete(12), 12 ActionTarget entries and range 0..11. These total ten legal pairs. Decide ten legal actions versus twelve with explicitly defined extra/reserved slots and masking; then update migration/teacher/checkpoint contracts together. No additional actions are invented by reconciliation. Phase F is cut; target collapse means revisit signal or defer, not reactivate F.','documentation/plans/phase-e-build-plan.md',kind='operator',status='HELD — conflicting action-space specification',hours=(1,3,.75,2),hold='10 enumerated pairs versus required12')
take('phase-e-build-plan.md',['E.'+str(i) for i in range(1,7)],{'E.1':(5,10,.5,1),'E.2':(10,20,.75,2),'E.3':(4,8,.5,1),'E.4':(7,14,.5,1.5),'E.5':(7,14,.75,2),'E.6':(1.5,4,1,3)},deps={'E.1':['E.0'],'E.2':['E.1'],'E.3':['E.1'],'E.4':['E.1','E.2'],'E.5':['E.3','E.4'],'E.6':['E.5']})
take('phase-k-build-plan.md',['K.'+str(i) for i in range(1,6)],{'K.1':(4,8,.25,1),'K.2':(3,6,.25,.75),'K.3':(5,10,.5,1),'K.4':(6,12,.5,1.5),'K.5':(.5,1.5,.5,1.5)},deps={'K.2':['K.1'],'K.3':['K.2'],'K.4':['K.3'],'K.5':['K.4']})
# Future tracks preserve their actual leaf obligations rather than collapsing whole phases into estimates.
future=[
('G.1','Extract race interfaces','RaceConfig, ProductionAdapter, MicroAdapter, FeatureSpec and RewardTemplate; no Protoss behavior regression. Reconcile original v0-refactor proposal with frozen-version rule: build on a new working version, never rewrite frozen history. Kill/defer if >5% tests break or >500 adapter lines needed.',['6.3'],(24,56,2,5)),
('G.2','Implement and validate Zerg','Larva/inject, creep, Overlords, morph roster, Bile/Fungal/Burrow/Abduct, hatch/pool/12-pool builds, rewards/features. Add evolve --race and per-race parent chain, same-race mirrors. Train and demonstrate >=50% at difficulty3 over20 games.',['G.1'],(80,160,5,12)),
('G.3','Implement and validate Terran','SCVs/MULEs, depot walls, addons, siege/stim/medivacs, snipe/EMP/nuke, 1-1-1/2-1-1/mech/bio, rewards/features. Train and demonstrate >=50% at difficulty3 over20 games.',['G.2'],(64,120,4,10)),
('G.4','Enable cross-race informational ladder','Within-race promotion; separate cross-race informational Elo with stable rankings. Optional cross-race safety gate remains a future decision, not silently required.',['G.3'],(12,24,1,3)),
('H.0','Conclude mini-game role investigation','Resolve scorecard versus gate/reward and Windows PySC2 feasibility before activating H/I/J. Defer all if scorecard-only value cannot justify substrate cost.',[],(8,20,1,3)),
('H.1','Pin PySC2 and verify imports','Pinned dependency; import-clean with version stack.',['H.0'],(3,8,.25,1)),
('H.2','Define MinigameResult and storage','Finite score/result contracts, per-version training.db minigame_results migration and round-trip compatibility.',['H.1'],(4,8,.5,1)),
('H.3','Implement minigame runner','Run PySC2 path for selected version/map, structured score, bounded parallel-safe SC2 lifetime.',['H.2'],(8,18,.5,2)),
('H.4','Expose minigame CLI','scripts/run_minigame.py --version v0 --map MoveToBeacon; argument/errors/output tests.',['H.3'],(2,5,.25,.75)),
('H.5','Observe mini-game end-to-end smoke','MoveToBeacon finite numeric score and stored row, compare published baseline at implementation time; no invented minimum guarantee.',['H.4'],(1,3,1,3)),
('I.1','Build custom Protoss mini-game corpus','Two to five focused maps, such as concave formation and worker defense, scoped by H investigation; score reliability before further maps.',['H.5'],(24,60,2,6)),
('J.1','Wire approved mini-game role','Investigation selects gate/reward/scorecard; implement only that role with cost and empirical validation, not three parallel architectures.',['H.5','H.0'],(10,28,1,4)),
('L.0','Prove replay-tail observer spike','Spike C: third SC2 process loads an actively written replay, not a third API PlayerBot. Keep normal fog/full observation limitations explicit; >2 days failure kills viewer path.',['K.5'],(8,16,1,3)),
('L.1','Implement replay exhibition pipeline','After spike success: replay tailing, game-state bridge/viewer, finite callback/process lifetime, own-process cleanup and recorded fixture. Preserve shipped EV two-pane mode; do not refactor it blindly to a single pane.',['L.0'],(20,48,2,5)),
('L.2','Observe exhibition fidelity','Live visual matchup, reliable follow/termination and bounded latency; compare recorded fixture, no gameplay authority for observer.',['L.1'],(2,5,2,5)),
('M.1','Build NL matchup selector','Use K metadata and L exhibitions, parse NL prompts into tagged pool matchups with explicit fallback. Validate three sample prompts; kill/defer if fallback >50%.',['K.5','L.2'],(8,20,1,3)),
('O.0','Prove four-way evolve soak reliability','Parallelization is code-complete but the scripted Hydra prerequisite is actual four-way soak reliability; record complete evidence before multi-expert architecture.',[],(2,5,1,2)),
('O.1','Train themed Hydra experts','Separate early/mid/late experts with appropriate training distributions, manifests and reproducible checkpoints. Learned Hydra remains cut.',['O.0'],(16,36,2,5)),
('O.2','Implement scripted expert switching','Threshold/hysteresis controller, inference integration and dashboard expert trace; preserve same observation/action interfaces.',['O.1'],(12,28,1,3)),
('O.3','Validate expert use and Elo','20 self-play games, >=+10 Elo, no expert collapse: each active >=10% steps in >=50% games. Observe comparable replay/use; defer if switching is effectively one expert.',['O.2'],(2,5,1,3)),
('P.1','Investigate licensed replay pretraining','Corpus/license/feasibility investigation before distillation or foundational pretraining. No unlicensed corpus assumption; kill if unavailable or no value at current scale.',[],(8,24,1,3)),
('Q.0','Conclude harvest skill scope investigation','Compare paper intake versus existing advised workflow; docs-only default, auto-commit only explicit later decision. Collapse to advised extension if indistinguishable.',[],(6,12,1,2)),
('Q.1','Draft Mode A paper intake','Paper paste/URL/localPDF -> scoped investigation with context, questions, method, findings, criteria, constraints, downstream decisions and references; docs-only, no code commit.',['Q.0'],(4,10,.5,1.5)),
('Q.2','Wire conditional harvest sandbox','Only if scope investigation explicitly selects auto-commit. Otherwise retain as not-applicable conditional, not work silently authorized.',['Q.0','Q.1'],(4,10,.5,1.5)),
('Q.3','Review three retroactive harvest cases','Compare outputs to three prior investigations using source evidence.',['Q.1'],(2,5,1,3)),
('Q.4','Observe one fresh paper intake','Fresh input yields useful investigation passing review; no cron or auto-PR scope.',['Q.1'],(1,3,1,2)),
('Q.5','Consider Mode B suggestion wrapper','Optional extension only after ModeA live acceptance; keep v1.1 if not a thin natural wrapper.',['Q.4'],(3,7,.5,1)),
('R.1','Rescope Wilson confidence reporting','SPRT was explicitly rejected by July5 EJ investigation and must not be revived. Preserve Wilson-CI reporting as remaining work, additive to shipped one-sided posterior.',['EJ.8'],(5,12,.5,2)),
('S.1','Investigate entropy reward bonus','Deferred statistical follow-up; scoped experiment and regression criteria before changing rewards.',['R.1'],(6,18,1,2)),
('S.2','Investigate time-to-win signal','Deferred signal study; separate wins from speed effects, avoid rewarding invalid/short matches.',['R.1'],(6,16,1,2)),
('S.3','Measure higher-n per-difficulty evaluations','Deferred higher sample count with per-difficulty breakdown and real machine-time cost.',['R.1'],(3,8,1,3)),
('S.4','Investigate informative priors','Deferred prior calibration; retain current Beta(1,1) until evidence justifies change.',['R.1'],(8,20,1,3)),
('ER.1','Clarify truncated restructure idea ii','Operator seed has six possible readings of SC2 version judge. Obtain intended meaning; do not treat EI as gated by this answer.',['EI.0'],(1,3,1,2)),
('ER.2','Plan deterministic replay of tested patches','Declared successor to EI patch capture: stack cap plus replay addresses candidate scored versus regenerated code shipped. Design exact artifact/replay acceptance before implementation.',['EI.7','EI.9','EI.14'],(10,24,1,3)),
('ER.3','Consider rule-derived fitness bar after evidence','Binomial-alpha or posterior conditioning only after selectable early-stop and post-change corpus; no current gate change.',['EI.8','EI.14'],(5,14,.5,2)),
('ER.4','Retain PFSP ancestor regression follow-up','Sample ancestor distribution for regression beyond immediate parent; baseline DB supplies corpus but does not implement ancestral sampling.',['EL.7'],(8,18,1,2)),
('ER.5','Enrich fingerprints after v1 evidence','Build-order/army-composition refinement remains deferred until baseline-result fingerprint proves useful.',['EL.7'],(8,20,1,2)),
('ER.6','Revisit species knobs after scored mini-games','Task ordering/time-in-task are mini-game species concepts, not proxy knobs to invent now.',['H.5','J.1'],(8,20,1,3)),
('ER.7','Consider true concurrent lineage execution','Deferred separate from intra-generation worker concurrency; preserve global pointer/commit ownership and SC2 two-client constraints.',['EL.7','EH.7','EH.8'],(16,40,2,4)),
('ER.8','Repair PFSP seat alternation when activated','game_index always0 in one-game PFSP batches means seat_swap alwaysFalse. Retain as latent correctness follow-up with actual caller test.',['6.2'],(3,7,.25,1)),
('ER.9','Repair result-out collision when consumer exists','Concurrent workers collide on logs/selfplay_tmp/game{i}_p*.json, but result-out has no reader at source audit. Conditional follow-up when real consumer is introduced; do not invent usage.',['EI.1'],(3,7,.25,1)),
('JV.9','Complete production Observer Rush lifecycle','Preview delivered; production lifecycle integration, multi-monitor/DPI behavior and long-session validation remain separate. Preserve exact before/after source, graceful owned-client stop, no gameplay/camera authority added.',['222'],(16,40,2,6)),
('JV.10','Consider v13 grouping adaptation and decision flattening','v13 centroid/staging/hysteresis not yet copied. Preserve frozen before/after, avoid unseen-enemy=attack/random thresholds. Flattening explicitly deferred; JDI safety controls precede broader changes.',['232'],(10,28,1,4)),
('BL.1','Decide and execute branch landing when authorized','onbrand-pilot already contained; no mechanical blocker requiring its separate merge. Decide landing with EV acceptance open versus after EV.4. Recheck branches/CI, prefer PR for Linux/Docker signal; merge commit preserves step history, tag actual merged commit, retain phase branch until gates close. No publication authorized by this cleanup.',['EV.4'],(2,5,1,2)),
]
for ident,title,body,deps,hrs in future:
    src='documentation/plans/phase-'+ident[0].lower()+'-build-plan.md'
    if src not in sources: src=master_old
    kind='operator' if ident in {'H.0','H.5','Q.0','Q.3','Q.4','ER.1','BL.1','L.2'} else 'wait' if ident in {'O.0','O.3'} else 'code'
    add(ident,title,body,src,kind=kind,status='DEFERRED — future or investigation-gated scope',deps=deps,hours=hrs,hold='Preserved future scope; prerequisites and product decision before implementation')
tactical='documentation/sc2/protoss/tactical-bugs.md'
for ident in ['T.1','T.3','T.4','T.7','T.8','T.9','T.10','T.11','T.12']:
    match=re.search(r'^### '+re.escape(ident)+r'[^\n]*\n',sources[tactical],re.M)
    end=re.search(r'^#{1,3} ',sources[tactical][match.end():],re.M)
    body=sources[tactical][match.end():match.end()+end.start() if end else len(sources[tactical])]
    body+='\n\nRevalidate current code and recorded evidence before implementation: this is a historical unresolved backlog, not a new claim every symptom persists. Capability correction: ground-only Zealots/Immortals cannot attack flying producers; use actual anti-air capability, never the source assertion about low-ground air.' if ident=='T.4' else '\n\nRevalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.'
    hrs={'T.1':(4,10,.5,1.5),'T.3':(5,12,.5,1.5),'T.4':(4,10,.5,1),'T.7':(6,14,.5,1.5),'T.8':(4,10,.5,1),'T.9':(4,9,.5,1),'T.10':(5,12,.5,1.5),'T.11':(5,12,.5,1.5),'T.12':(1,3,1,2)}[ident]
    add(ident,match.group(0).strip().split('—',1)[-1].strip(),body,tactical,kind='operator' if ident=='T.12' else 'code',status='DEFERRED — historical residual; revalidate current state',hours=hrs,hold='Historical backlog lacks current closure evidence')

# Capture exact originals using cautious project-confined moves.
tracked=set(git('ls-files').splitlines())
for old,new in mapping.items():
    src,dst=ROOT/old,ROOT/new
    if not dst.resolve().is_relative_to(ROOT.resolve()): raise ValueError(new)
    dst.parent.mkdir(parents=True,exist_ok=True)
    if old in tracked: subprocess.run(['git','-C',str(ROOT),'mv','--',old,new],check=True,capture_output=True)
    else: src.rename(dst)
for p in sorted((ROOT/'documentation').rglob('*'),key=lambda p:len(p.parts),reverse=True):
    if p.is_dir() and not any(p.iterdir()): p.rmdir()
if not any((ROOT/'documentation').iterdir()): (ROOT/'documentation').rmdir()
changed=[]
for rel in git('ls-files','--cached','--others','--exclude-standard').splitlines():
    p=ROOT/rel
    if not p.is_file() or rel in immutable or rel.startswith('docs/plan-reconciliation/') or rel.startswith('.claude/task-state/') or rel=='dev.code-workspace': continue
    if p.suffix.lower() not in {'.md','.html','.py','.ps1','.sh','.json','.toml','.yaml','.yml','.ts','.tsx','.css'}: continue
    try: text=p.read_text(encoding='utf-8')
    except UnicodeError: continue
    new=repair(text,inverse.get(rel,rel),rel)
    new=new.replace('/ "documentation"','/ "docs"').replace("/ 'documentation'","/ 'docs'")
    if new!=text: write(p,new); changed.append(rel)

intro='''# Alpha4Gate master plan

Status: outstanding work consolidated 2026-10-14. This is the single project execution inventory. Completed code is not operator acceptance. No gameplay, paid dispatch, publication, issue closure or implementation occurred during this migration.

## 1. Purpose and authority

Build an inspectable SC2 platform: frozen full-stack RL versions and a separate Jev graph player. Preserve empirical promotion/acceptance gates, honest measurement and local command legality. Workspace Toolkit scheduling/status remains authoritative where mapped: [shared program](../../../plan.md). This project plan owns requirements, not a replacement shared schedule. Source statements and original issue identities remain auditable in [source map](../plan-reconciliation/migration.json).

## 2. Current context

Read [CLAUDE.md](../../CLAUDE.md) and project rules before implementation. Current baseline at migration is c0836c3; de-facto branch master-plan/phase-ev. JDI0/9 accepted; 232 held after three reviews, candidate00bc515 unmerged. JON225 also held; no accepted campaign implementation. JDI $5 cumulative approval includes necessary JON prerequisite probes/smokes, no new process/stage resets it. The old overnight work window is history, not a recurring schedule. Spending remains gated on durable budget, safety and real smoke. Frozen v1 v1-b53039a693cadf75 must never be recaptured. JV207/208, J2 213/222/223 and Evolve gates are independently open. JI209–211 actual hosted acceptance is complete. The standalone Observer Rush preview/showcase is complete without closing JV/J2.

## 3. Scope and exclusions

Only outstanding leaf work appears below. Shipped phases A/0–5/8/9/B/N, D code/counts, EL1–6, EJ1–6, EV1–3, EH1–2, JV201–206, JI209–211, J2 code212/224/214–221 are excluded from estimates. Phase8 ceremonial validation was explicitly closed and cloud step12 cut; stale historical TODOs do not reopen it. PhaseF transformer, checkpoint-only opponent pool, domain abstraction and learned Hydra remain cut. No autonomous merges, recurring jobs or broader model gameplay authority is added here.

## 4. Architecture and dependencies

Python>=3.12, uv, burnysc2, FastAPI, React/TypeScript/Vite. RL versions are full frozen physical stacks; current is a pointer. src/orchestrator must not import bots.current/version modules. Jev owns its graph/runtime, no RL-stack dependency. Backend8765/frontend3000; verify actual health and IPv6 localhost behavior. Windows local CPU PPO; WSL Ubuntu-22.04 with SC2PATH and SC2_WSL_DETECT=0 for Linux work. Follow existing installed dependency locks, not historical test counts.

Serialize shared JON/JDI producers. 236 needs225–227;237 needs228. JDI232 is held independently. EH before EI shared-file edits; re-derive anchors after each landing. EH6 dependsEH8; do not enable long auto-committing launch while index scoping remains unfixed. Deep review remains required for trust/persistence boundaries; historical toolkit freeze has lifted, not a reason to downgrade. All Evolve/PhaseD/Phase6/7 live gates serialize machine-wide around one pointer and SC2 resources.

## 5. Operational corrections and contracts

Archived plans preserve original bytes and may contain superseded instructions. These corrections govern this master. Register frozen baselines before gauntlet/floor gates; verify list output. --lineages N never creates N records: seed through scripts/lineage.py; EH2 persists only disk-loaded registries. Four seeded records/cap3 makes culling possible, not guaranteed: dominance plus fingerprint distance<0.15 still required. A single-lineage fallback cannot satisfy multi-lineage acceptance. Record not-triggered-by-design separately; >=2 generations per actual lineage and nontrivial diversity remain required. Remove seeded lineages after gates unless explicitly retained; baselines persist. EH has no extra six-hour acceptance step: EH10 is terminal by operator decision.

Use --generations0 for soaks; argparse default1 remains quick-test behavior. EV5 uses fixed production launcher only after EH6. Until EH4/EH5 land, dashboard Stop is unwired; viewer close only detaches. Never Ctrl+C the threaded evolve viewer or kill SC2 manually; use documented console closure/owned graceful mechanisms. Jev has its own graceful Stop battle/control contract; do not confuse the two. No live tests during docs migration. EVO_AUTO currently sweeps staged work; never run it over this migration.

PhaseD M1: take baseline before flag, three PPO cycles, >=30% early reward standard-deviation reduction and nonregressing difficulty3 WR, then M2 >=+10 Elo/20 games. Bare snapshot_bot.py resolves current version; --from current and hardcoded pointer v14 are invalid. Use actual emitted versions. Restore reward backup and leave flag off on failure. Phase7 is a skill invocation /improve-bot-advised, not a uv console script. PhaseE10-versus12 actions is unresolved E0; no implicit choice.

EI log identity is match_id, not PFSP game_index; one producer/consumer helper, newest-N byte cap and retention at every generation including serial/viewer/resume, never the concurrency-only stale sweeper. Trace fields: match_id/version/role/terminal marker/n_steps(nullable)/duration_s(nullable)/status_chunk_count/parse_errors; mechanical evidence only. Six flags preserve defaults, fitness flags cross worker argv. SelfPlayRecord adds two optional fields to nine existing keys (eleven total, correcting source arithmetic); all readers tolerate unknown fields from this release onward, not genuine pre-EI checkouts. PerItemState serializers and load_pool_state pop tuple change together; score pairs remain pairs. EI8 preserves existing clean-sweep tests and adds near-miss characterization; its late contradictory sentence is superseded by the detailed Done-when. EI14 co-tenants EL7 with trace-capture/capture-patch and optional static observe only, not enforce/stack-size/rule-stop; it claims no throughput or EL outcome, zero added games.

## 6. Design decisions

Operator-picked historical approvals retain their scope: EH pause wiring cut and checkbox disabled, resume clears pending stop with INFO, EH7/EH8 retained, no EH unattended-observation obligation, EH9 depends only shippedEH1/EH2, EV sibling correction #302. JDI integration/experiment choices approved with flexible single$5 cap; no stage subcap or renewed approval within that cap. Agent-defaulted cleanup: one master, source-preserving archive, unchanged IDs except explicit aliases, independent gates, conservative unresolved holds and active-hour estimates. EI product defaults remain unratified; J2 residual scope, E0 and branch landing remain decisions. Original append-only inventories are preserved in exact archives; this reconciliation appends to them rather than rewriting history.

## 7. Outstanding build and acceptance steps

Status PENDING can still have dependencies. HELD/DEFERRED are not dispatch-ready. Historical source bodies below retain exact requirements; canonical corrections above take precedence. Completed dependencies are listed in the provenance report rather than reintroduced as forecast work.
'''
parts=[intro]
for r in rows:
    source=mapping[r['source']]
    parts.append(f"### Step {r['step_id']}: {r['title']}\n\n- **Status:** {r['status']}\n- **Type:** {r['type']}\n- **Issue:** {'#'+str(r['issue']) if r['issue'] else 'not yet assigned; no issue created by cleanup'}\n- **Depends on:** {', '.join(r['dependencies']) or 'none outstanding; retain shipped prerequisites'}\n- **Source:** [{Path(source).name}](../{source.removeprefix('docs/')}); original {r['original_step_id']}.\n\n{r['body']}\n\n")
parts.append('''## 8. Jev campaign contracts

JON: freeze exact source bytes before evaluation, no second authoring on promotion. One atomic durable ledger reserves worst-case cost before every gameplay/probe/advice/coder request, counts missing usage at maximum, no hidden retry, unknown price/model blocks dispatch. Rates in sources are dated evidence, reverify before dispatch; managed cap is not a claim about account taxes/provider changes. Integer nanodollars, cumulative5e9. Offline225 structures cannot authorize execution: 227 issues and verifies real controller evidence binding campaign/window/attempt/case/run/source/result/critical test execution. Caller labels, booleans, hashes or seals alone are not authority. Preserve original three-round hold; prospective round4/10 bounded<=2h plus one review batch after reorientation, not field-by-field patching or unrelated225 scope.

Weighted JON references parent50/original25/distinct25 (coalesce duplicate original to50). Freeze before testing. Shared built-in-AI cases, not direct Jev H2H. Three-race Medium paired screen6games total; candidate wins>=parent admits only. Promotion18cases/version: Simple64 Medium/MediumHard,3races3new sealed seeds, alternate order; candidate>parent, weightedreference>=.10,no lower race/difficulty wins, full critical suite. New promotion panel after prior exposure, exclude11/101/202 and lineage exposed seeds. One assessment per candidate fingerprint, no result-driven reruns. Nightly parent controls one per race, disagreement invalidates window/cache, not equivalence claim. Missing/incomplete/invalid evidence is no promotion. Critical suite includes recovery completion/renewal, defense, production while pending, stale answers and reservation cancellation; skipped/xfail/zero-collected invalid. First campaign six hours, fixed2xcap,900game/1200wall/450requests, enough remaining time before dispatch, graceful stop and honest overshoot; camera/caster on,recordingoff. Advisor one hypothesis in isolated allowlisted worktree; budget/evaluator/tests/credentials protected, no merge/push/default switch. Prepare performs no network/process; explicit run/resume retains ledger; report read-only. UUID4 lowerhex identities,SHA256bytes,UTCpersisted/monotonicelapsed,atomic records/exclusive ownership; failed retries retain conservative allowance. JON planned commands/modules must not be represented as shipped.

JDI: graph owns shared feasibility and same-tick fallback; emergency defense first, strategic recovery second, army intent third, local fallback last. No recover without living eligible combat unit, no attack without group/target. Outcome proof must inspect exact actor post-adapter order, not merely selected branch or any command. 232 audit shows recovery success while eight priorATTACKorders survive; do not infer fixed from cancelled bookkeeping. Current/improved profiles, one global request budget; prepared army intent at3readyZealots but no earlyattack, at4 wait<=2gameSeconds once, defense immediate, semantic signatures preserve harmless reinforcement transitions and revalidate current eligibility. History31samples<=1/gameSecond,30s count/durability proxy, sighting ages/coverage; vision loss is not death. Independent macro priority tech/expand/hold10s, own slot/expiry, fair question scheduling after2nonemergencyarmyrequests.

JDI arms S scripted-local, F scripted-fixedStalkerwhenfeasible, H hostedcurrent, I hostedimproved use identical repairedsource/policy. Simple64 three races Medium/MediumHard seeds303/404/505 unless exposed (nextunused>=606),18cases/arm,72evaluationgames36hosted;3development smokes yield75baselinegames38hosted. Native realtime, no game-speed override;900game/1200wall/450requests. First3Mediumcases12games then60remaining; stop/report/allocation within same$5, prerequisiteJONcalls also count. Positive conclusion needs I>=2wins/18 above bothS/F,no race regression,no defense suppression/avoidableemptyarmyfailure,zeroinfrastructurefailure; macroclaim>=10appliedchoices across>=3cases includingtech+expand. All attempts visible;missingpanelinconclusive;draw/timeouts/infra0 in intention-to-test, completed-only separately, discordant paired counts+descriptiveClopperPearson95% interval,no population/causalbreadthclaim. If source/prompts change, new sealedexperiment/seeds. More/same/less model role allowed, no automaticpromotion.

J2 remains separate: six-case deferredv1baseline races3/4seed11;222<=3Terrandevelopmentmatches3/4/5seed11;223freezev2once then hostedv1/v2 races atMediumHard seeds101/202 (12games) +6scriptedv2attribution. Positive gatev2>=4/6,moretotal thanv1,no lower race count;v1=6/6 ceilinginconclusive. Realacceptedhosteddecisions/provenance required, no fallback-as-hostedwin. Perinvocation6games/7200wall/2700requests, firstinfra/auth/provenancefailurestops; allcasesretained, explicitinterruptedretry. 222 needssecondNexus,firstStalker,actualstrategy+execution influence,worker/site recovery,pendingstop; absentnatural evidence remainspending. Browserexactsession/run ready beforeSC2, no newest-run guessing; input readiness separate from fullrealgameacceptance. JV requires its own three seeds1/2/3Terrandiff1completeoutcomes,rushandtraceevidence; JI historicalservicepass does not substitute.

## 9. Tests and validation

Future code steps retain original review flags, production-caller integration, negative/default-preservation tests and exact Done-when. Re-measure baseline on the actual worktree; historical counts are evidence, not timeless assertions. Run scoped checks while iterating and required full suites at implementation acceptance: uv run pytest, uv run ruff check ., uv run mypy src bots --strict; scripts checks explicitly where EH/EI/JON own them, report pre-existing errors. Frontend npm run test:run (not watch), lint,typecheck,build; actual browser tests separately. Offline proof never closes live gates. Migration validation/source hashes are in ../plan-reconciliation/validation.json.

## 10. Risks and holds

Held225/232 are structural review failures, not unfinished paperwork. E0,J2.0,EI.0,BL1 need product decisions. Investigation-gated future work remains disabled. No command here should bypass current local safety rules. Historical code anchors must be rederived. Archives preserve links verbatim for provenance, so historical relative links may be stale; use migration.json to resolve old paths.

## 11. Estimates

[Build estimates](build-estimates.md) are prospective model ranges for every outstanding leaf, active worker and operator attention hours. They exclude unattendedSC2 wall duration, separately named in gate requirements; no measured calibration history exists. Held work is forecast-only, not approved dispatch. Raw master SHA256 binds exact plan bytes; Observatory binds its own evidence work-item fingerprints.

## 12. Completion and rollback

A phase closes only when its original acceptance gates and actual evidence pass; negative experiments remain recorded with target-unmet status when specified. Completed original plans reside under ../completed/, mixed or superseded originals under ../archived-plans/. Source bytes/hash history are preserved, and migration.json records all file paths. Reconciliation is reversible file moves plus references; no runtime data or frozen baseline changed. Implementation rollback follows phase-specific backups/version isolation and never deletes a real promoted version merely because it appears untracked.

## 13. Quickstart for a fresh session

Read project instructions, this master and the exact source for the selected leaf. Inspect live git status/branch, preserved pending worktrees and issue evidence. Choose an unheld ready leaf, re-anchor producer/consumer paths, execute its normal review workflow. Do not use stale overnight/session goals as new authorization. For operator work read docs/operator guides and docs/operator-gate-runbook.md after applying section5 corrections. Stop/report at actual gate boundaries.

## 14. Source reconciliation

[Merge mapping and review](../plan-reconciliation/merge-decisions.md) describes duplicates, partial overlap, sequencing and intentionally retained scope. [Immutable migration map](../plan-reconciliation/migration.json) includes source hashes and targets. Source reviews/proposals/validation reports are reference evidence under ../plan-reconciliation/source-reviews/, not competing plans. All original append-only decisions and build history remain in full archived sources.

## 15. Appended decision inventory (2026-10-14)

| ID | Origin | Decision | Status |
|---|---|---|---|
| RC1 | Operator | All active-project docs consolidated; one master; preserve distinct requirements | Applied |
| RC2 | Operator | Shared Toolkit scheduling authority retained | Applied |
| RC3 | Agent default | Keep JDI/JON and every live gate separate; source hashes preserve history | Applied |
| RC4 | Agent default | Hold contradictory E cardinality and unowned J2 residual scope | Unresolved product choices |
| RC5 | Agent default | Forecast ranges are model estimates; no invented measured history | Applied |
''')
master='\n'.join(parts)
assert len(master.encode())<262144,len(master.encode())
write(ROOT/'docs/plans/master_plan.md',master)
digest=sha(ROOT/'docs/plans/master_plan.md')
stamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
est=[]
for r in rows:
    a,b,c,d=r['hours']
    est.append({k:r[k] for k in ('step_id','original_step_id','title','status','dependencies','hold_reason')}|{'enabled':not r['hold_reason'],'basis':'model','model_id':'Codex session; runtime model not exposed','estimated_at':stamp,'confidence':'low' if r['hold_reason'] or b>=20 else 'medium','worker_hours':{'low':a,'high':b},'attention_hours':{'low':c,'high':d},'rationale':r['rationale'],'plan_sha256':digest,'source_path':mapping[r['source']],'source_step_id':r['original_step_id']})
write(ROOT/'docs/plans/build-estimates.json',json.dumps({'schema_version':1,'project':'Alpha4Gate','plan_path':'docs/plans/master_plan.md','plan_sha256':digest,'estimated_at':stamp,'basis':'model','model_id':'Codex session; runtime model not exposed','units':est,'excluded_completed':'A/0–5/8/9/B/N; D code/counts; EL1–6; EJ1–6; EV1–3; EH1–2; JV201–206; JI209–211; J2 code212/224/214–221. Cut scope is not forecast.'},indent=2))
write(ROOT/'docs/plans/build-estimates.md','# Alpha4Gate build estimates\n\nProspective active hours, not measured history or elapsed SC2 time. Held/deferred rows remain disabled. Worker and attention columns are separate, not additive by assumption. Per-leaf uncertainty is explicit in JSON. SHA256: `'+digest+'`.\n\n| Step | Worker h | Attention h | Status |\n|---|---:|---:|---|\n'+'\n'.join(f"| {r['step_id']} | {r['hours'][0]}–{r['hours'][1]} | {r['hours'][2]}–{r['hours'][3]} | {r['status']} |" for r in rows)+'\n')
migration=[{'original_path':x['path'],'new_path':mapping[x['path']],'sha256':x['sha256'],'bytes':x['bytes'],'immutable':mapping[x['path']] in immutable} for x in before['all_documentation_files']]
write(REPORT/'migration.json',json.dumps({'sources':migration,'references_updated':changed,'master_sha256':digest,'pending_steps':len(rows)},indent=2))
write(REPORT/'steps.json',json.dumps([{k:v for k,v in r.items() if k!='body'} for r in rows],indent=2))
write(REPORT/'step-aliases.json',json.dumps({'Phase D M1 / D.7':'M1','Phase D M2 / D.8':'M2','Phase7 Step6':'7.6','JDI.1–JDI.9':'232–240','JON.1–JON.7':'225–231','JV.7/JV.8':'207/208','J2 Manual UAT M1':'222','EV Manual UAT M1':'EV.4','Phase E decision':'E.0'},indent=2))
assert sha(ROOT/'.claude/task-state/current.md')==before['dirty_derived_sha256']
assert all(sha(ROOT/x['new_path'])==x['sha256'] for x in migration if x['immutable'])
print(json.dumps({'steps':len(rows),'master_bytes':len(master.encode()),'master_sha256':digest,'moved':len(mapping),'immutable':len(immutable),'reference_files':len(changed)}))
