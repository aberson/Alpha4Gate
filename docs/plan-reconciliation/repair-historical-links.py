"""One-time, audited reference repairs after the documentation migration."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
audit = json.loads((WORKSPACE / 'docs/plan-reconciliation-2026-10-14/link-audit.json').read_text())
repairs = {
    '../headless-sc2-investigation.md': '../investigations/headless-sc2-investigation.md',
    '../win-probability-forecast-investigation.md': '../investigations/win-probability-forecast-investigation.md',
    '../plans/evolve-gate-reduction-plan.md': '../completed/evolve-gate-reduction-plan.md',
    '../plans/models-tab-plan.md': '../completed/models-tab-plan.md',
    '../plans/phase-8-build-plan.md': '../completed/phase-8-build-plan.md',
    'tests/commands/test_dispatch_guard.py': '../../tests/commands/test_dispatch_guard.py',
    'docs/improvements/self-improve/20260414-1127-retry-storm-dedup.md': 'improve-2026-04-14-retry-storm-dedup.md',
    '../../.claude/rules/security.md': '../../../.claude/rules/security.md',
    '../../../dev/.claude/rules/security.md': '../../../.claude/rules/security.md',
    'idea-graphical-flow-page.md': '../archived-plans/legacy/idea-graphical-flow-page.md',
}
records = []
for file in sorted({x['file'] for x in audit if x['project'] in {'Alpha4Gate', 'void_furnace'}}):
    path = WORKSPACE / file
    old = path.read_text(encoding='utf-8')
    text = old
    changes = []
    for item in [x for x in audit if x['file'] == file]:
        target = item['target']
        if f']({target})' not in text or (path.parent / target.split('#')[0]).exists():
            continue
        base, sep, anchor = target.partition('#')
        if base in repairs:
            new = repairs[base] + (sep + anchor if sep else '')
            assert (path.parent / new.split('#')[0]).exists(), (path, new)
            text = text.replace(f']({target})', f']({new})')
            changes.append({'old_target': target, 'new_target': new, 'reason': 'resolved existing document or source'})
        elif 'src/alpha4gate/' in target or '/memory/' in target or target == '../../memory/' or target.startswith('logs/'):
            reason = 'historical source path, absent from this checkout'
            if 'memory/' in target:
                reason = 'historical personal memory, not distributed with this checkout'
            elif target.startswith('logs/'):
                reason = 'historical runtime artifact, absent from this checkout'
            pattern = r'\[([^\]\n]+)\]\(' + re.escape(target) + r'\)'
            text = re.sub(pattern, lambda m: m.group(1) + ' (`' + target + '`; ' + reason + ')', text)
            changes.append({'old_target': target, 'reason': reason, 'path_retained_as_code': True})
        else:
            raise ValueError((file, target))
    if text != old:
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        path.write_text(text, encoding='utf-8', newline='')
        records.append({'file': file, 'before_sha256': before, 'after_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'changes': changes})
(ROOT / 'docs/plan-reconciliation/historical-link-repairs.json').write_text(json.dumps(records, indent=2) + '\n')
print(json.dumps({'files_repaired': len(records), 'target_repairs': sum(len(x['changes']) for x in records)}))
