"""One-shot final reference review and reconciliation corrections."""
from pathlib import Path
import json,re,os,hashlib,subprocess,datetime,html,ast
R=Path(__file__).resolve().parents[2]; D=R/'docs/plan-reconciliation'
def write(p,t): p.write_text(t,encoding='utf-8',newline='\n')
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
m=json.loads((D/'migration.json').read_text()); mapping={x['original_path']:x['new_path'] for x in m['sources']}; inverse={v:k for k,v in mapping.items()}; immutable={x['new_path'] for x in m['sources'] if x['immutable']}
aliases={}
for old,new in mapping.items():
    if old.startswith('documentation/archived/'):
        aliases['docs/plans/'+Path(old).name]=new
aliases['docs/plans/selfplay-viewer-plan.md']='docs/archived-plans/legacy/selfplay-viewer-observer-plan.md'
aliases['docs/archived-plans/master_plan.md']='docs/plans/master_plan.md'
def rebase(text,old,new):
    def replace(mt):
        target=mt.group(1)
        if re.match(r'\w+://|#|mailto:',target): return mt.group(0)
        bare,sep,frag=target.partition('#'); clean=re.sub(r':\d+(?:-\d+)?$','',bare); suffix=bare[len(clean):]
        resolved=os.path.normpath(str(Path(old).parent/clean)).replace('\\','/')
        dest=mapping.get(resolved)
        if dest is None and (R/resolved).exists(): dest=resolved
        if dest:
            value=os.path.relpath(dest,str(Path(new).parent)).replace('\\','/')+suffix+(sep+frag if sep else '')
            return mt.group(0).replace(target,value)
        return mt.group(0)
    return re.sub(r'\]\(([^)\s]+)\)',replace,text)
changed=[]
for rel in subprocess.check_output(['git','-C',str(R),'ls-files','--cached','--others','--exclude-standard'],text=True).splitlines():
    p=R/rel
    if not p.is_file() or rel in immutable or rel.startswith(('.claude/task-state/','docs/plans/')) or (rel.startswith('docs/plan-reconciliation/') and not rel.startswith('docs/plan-reconciliation/source-reviews/')): continue
    if p.suffix.lower() not in {'.md','.html','.py','.ps1','.sh','.json','.toml','.yml','.yaml','.ts','.tsx','.css'} and p.name!='.gitignore': continue
    try: text=p.read_text(encoding='utf-8')
    except UnicodeError: continue
    new=text
    if rel.startswith('docs/plan-reconciliation/source-reviews/'):
        new=rebase(new,inverse[rel],rel)
        for old,target in sorted(mapping.items(),key=lambda x:-len(x[0])): new=new.replace(old,target)
        new=new.replace('documentation/','docs/')
    if p.name=='.gitignore': new=new.replace('documentation/','docs/')
    for old,target in aliases.items(): new=new.replace(old,target)
    # Relative links to the old master are now canonical, except immutable provenance.
    new=new.replace('../archived-plans/master_plan.md','../plans/master_plan.md')
    new=new.replace('docs/plans/alpha4gate-master-plan.md','docs/plans/master_plan.md')
    if 'improve-bot-advised' in rel:
        new=new.replace('`## Tactical refinements backlog`','`### Step T.` headings')
        new=new.replace('extract the section bounded by `### Step T.` headings and the next `## ` heading','select the canonical `### Step T.` blocks through their next step heading; skip DONE/COMPLETE/CUT and do not dispatch DEFERRED/HELD work without its activation decision')
        new=new.replace('parsing the `### Step T.` headings section','parsing the `### Step T.` blocks')
    if rel=='README.md':
        new=new.replace('Active sub-plans (see docs/plans/master_plan.md)','Canonical master and per-step forecast estimates')
    if rel=='CLAUDE.md':
        new=new.replace('single spine + plan index (active sub-plan pointers + archived list)','single canonical outstanding-work plan; original sources in docs/archived-plans and docs/completed')
    if new!=text: write(p,new); changed.append(rel)

p=R/'docs/plans/master_plan.md'; text=p.read_text(encoding='utf-8')
text=re.sub(r'^### Step J2\.1:.*?(?=^### Step |^## )','',text,flags=re.M|re.S)
text=text.replace('Separate early/mid/late experts with appropriate training distributions','Separate themed experts (Skytoss, Ground, Cheese-rush) with appropriate training distributions')
text=text.replace('### Step S.1: Investigate entropy reward bonus','### Step S.1: Investigate build-order entropy metric')
text=text.replace('Deferred statistical follow-up; scoped experiment and regression criteria before changing rewards.','Deferred build-order entropy measurement; define a diagnostic metric and scoped validation before any policy/reward change.')
for ident in ['L.0','ER.1']:
    pattern=r'(^### Step '+re.escape(ident)+r':.*?\*\*Depends on:\*\* )[^\n]*'
    text=re.sub(pattern,r'\1none outstanding; retain shipped prerequisites',text,flags=re.M|re.S)
text=text.replace('not operator acceptance.','not operator acceptance.')
text=text.replace('the original Done-when','the original Done-when')
text=text.replace('These corrections govern this master.','These corrections govern this master. Browser fixture repair is already complete at 03c552a: all nine opt-in browser tests passed (64 deselected), recorded in source-reviews/jev-v2-validation.md. It is excluded from outstanding forecasts. The interrupted Step222 run e06637818c1b4341a213e99082b91ffd is not a win despite raw Victory: the operator surrendered; no second Nexus or pending-request cleanup was proven. Remaining two development matches are held pending recovery/attack investigation, independently of deferred213.')
text=re.sub(r'(^### Step 222:.*?\*\*Status:\*\* )[^\n]*',r'\1HELD — remaining development matches paused for recovery/attack investigation',text,flags=re.M|re.S)
# Section5 corrections must be visible at the individual gate rather than contradicted by old command text.
text=text.replace('(`--lineages 3 --population-cap 3 --hours 6 --fitness-mode both`)','(`--lineages 4 --population-cap 3 --hours 6 --generations 0 --fitness-mode both`, after seeding four actual registry records)')
text=text.replace('or is justified as not-needed under cap','or is explicitly documented as not-triggered-by-design; multi-lineage scheduling must still be observed')
text=text.replace('uv run python scripts/evolve.py --pool-size 3 --games-per-eval 3 --hours 0.75 --priors','uv run python scripts/evolve.py --pool-size 3 --games-per-eval 3 --hours 0.75 --generations 0 --priors')
text=text.replace('The two new fields are additive optional-with-default, making a ten-key row.','The two new fields are additive optional-with-default, making an eleven-key row (nine plus two).')
text=text.replace('accepts a ten-key payload','accepts a wider eleven-key payload')
text=text.replace('**Depends on.** Step E.1.','**Historical dependency:** E.1 (canonical dependency field above).')
text=text.replace('Phase F might be needed first. Mark phase "deferred pending F".','Revisit the signal and keep this work deferred if collapse persists. Phase F was explicitly cut; this does not reactivate it.')
extra='''### Step T.2A: Revisit proactive elevation-aware rally placement

- **Status:** DEFERRED — reactive low-ground-bleeding fix shipped; proactive alternative remains future
- **Type:** code
- **Issue:** not assigned
- **Depends on:** none outstanding
- **Source:** [tactical backlog](../archived-plans/tactical-bugs.md), original T.2 deferred tail.

Reactive centroid/HP-loss detection shipped c5bd90d/#138. The separately deferred proactive elevation-aware rally-point selection remains a distinct requirement: investigate whether current placement avoids low-ground bleeding before proposing it. Do not reopen the shipped reactive fix. Accept only actual terrain-aware placement with regression evidence; no new gameplay work in this migration.

'''
text=text.replace('## 8. Jev campaign contracts',extra+'## 8. Jev campaign contracts')
text=text.replace('## 10. Risks and holds','''## 10. Risks and holds

Reference-only exception: this authorized documentation migration repairs the shared-doc resource paths in frozen RL bots/v0 through v12 as well as current v13. Original bytes remain in Git history; gameplay logic, stored data, Jev captured source snapshots and policy bytes are untouched. Historical source coordinates still need the migration map. The earlier alpha4gate.runner skill cleanup is already absent from current skills; it is not reopened.
''')
write(p,text)
rows=json.loads((D/'steps.json').read_text()); rows=[r for r in rows if r['step_id']!='J2.1']
for r in rows:
    if r['step_id'] in {'L.0','ER.1'}: r['dependencies']=[]
    if r['step_id']=='222': r['status']='HELD — remaining development matches paused for recovery/attack investigation'; r['hold_reason']='Interrupted development match did not prove acceptance; recovery/attack investigation pending'
    if r['step_id']=='S.1': r['title']='Investigate build-order entropy metric'
rows.append(dict(step_id='T.2A',original_step_id='T.2 deferred proactive tail',title='Revisit proactive elevation-aware rally placement',type='code',status='DEFERRED — reactive fix shipped, proactive placement future',dependencies=[],hours=[5,12,.5,1.5],rationale='Terrain-aware placement needs current-code investigation and real map/rally regression evidence; lower bound reuses existing placement seams, upper bound covers pathing and tactical regressions.',issue=None,hold_reason='Deferred proactive alternative',source='documentation/sc2/protoss/tactical-bugs.md'))
write(D/'steps.json',json.dumps(rows,indent=2))
data=json.loads((R/'docs/plans/build-estimates.json').read_text()); data['units']=[u for u in data['units'] if u['step_id']!='J2.1']
for u in data['units']:
    r=next(r for r in rows if r['step_id']==u['step_id'])
    for key in ['title','dependencies','status','hold_reason']: u[key]=r[key]
    u['enabled']=not u['hold_reason']
    # Cost drivers are step-specific; ranges were assigned individually during semantic review.
    block=re.search(r'^### Step '+re.escape(u['step_id'])+r':.*?(?=^### Step |^## |\Z)',text,re.M|re.S).group(0)
    detail=re.search(r'\*\*Problem:\*\*\s*(.*)',block)
    evidence=detail.group(1) if detail else next((x for x in block.splitlines() if x and not x.startswith(('#','-'))),'')
    evidence=re.sub(r'[`*]','',evidence)[:350]
    u['rationale']=f"Active effort for {u['title']}: {evidence} Range includes review and producer/consumer validation; uncertainty is current-code drift and acceptance evidence, with unattended match time excluded."
new=rows[-1]; a,b,c,d=new['hours']
data['units'].append({k:new[k] for k in ('step_id','original_step_id','title','status','dependencies','hold_reason')}|dict(enabled=False,basis='model',model_id='Codex session; runtime model not exposed',estimated_at=data['estimated_at'],confidence='low',worker_hours={'low':a,'high':b},attention_hours={'low':c,'high':d},rationale=new['rationale'],source_path=mapping[new['source']],source_step_id=new['original_step_id']))
sha=digest(p); data['plan_sha256']=sha
for u in data['units']: u['plan_sha256']=sha
write(R/'docs/plans/build-estimates.json',json.dumps(data,indent=2))
write(R/'docs/plans/build-estimates.md','# Alpha4Gate estimates\n\nProspective model ranges in active hours; no measured history. Unattended game duration is excluded and remains specified at each gate. Held/deferred work stays disabled. Raw master SHA256: `'+sha+'`.\n\n| Step | Worker h | Attention h | Status |\n|---|---:|---:|---|\n'+'\n'.join(f"| {u['step_id']} | {u['worker_hours']['low']}–{u['worker_hours']['high']} | {u['attention_hours']['low']}–{u['attention_hours']['high']} | {u['status']} |" for u in data['units'])+'\n')
m['master_sha256']=sha; m['references_updated']=sorted(set(m['references_updated']+changed)); write(D/'migration.json',json.dumps(m,indent=2))
print(json.dumps({'master_sha256':sha,'bytes':p.stat().st_size,'leaves':len(rows),'references_updated':len(m['references_updated'])}))
