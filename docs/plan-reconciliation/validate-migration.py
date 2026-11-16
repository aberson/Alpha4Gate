"""Recheck local migration invariants; recorded pytest/resource results are separate."""
from pathlib import Path
import ast
import hashlib
import json
import re
import subprocess
import urllib.parse

r = Path(__file__).resolve().parents[2]
root = r.parent
d = r / 'docs/plan-reconciliation'
m = json.loads((d / 'migration.json').read_text())
before = json.loads((d / 'before.json').read_text())
immutable = [x for x in m['sources'] if x['immutable']]
assert all(hashlib.sha256((r / x['new_path']).read_bytes()).hexdigest() == x['sha256'] for x in immutable)
py = [x for x in m['references_updated'] if x.endswith('.py')]
for x in py:
    ast.parse((r / x).read_text(encoding='utf-8-sig'))
assert hashlib.sha256((r / '.claude/task-state/current.md').read_bytes()).hexdigest() == before['dirty_derived_sha256']
raw = (r / 'docs/plans/master_plan.md').read_bytes()
ids = re.findall(r'^### Step ([^\s:]+)', raw.decode('utf-8'), re.M)
estimates = json.loads((r / 'docs/plans/build-estimates.json').read_text())
rows = estimates.get('estimates', estimates['units'])
assert len(ids) == 131 and len(set(ids)) == 131 and set(ids) == {x['step_id'] for x in rows}
assert all(re.fullmatch(r'(?:[Mm]?\d+(?:\.\d+)*[A-Z]?|[A-Z]{1,4}\d*(?:\.\d+)*)', x) for x in ids)
missing = []
checked = 0
legacy_encoding = []
for project in ['Alpha4Gate', 'void_furnace']:
    pr = root / project
    for p in list((pr / 'docs').rglob('*.md')) + [pr / 'README.md', pr / 'CLAUDE.md']:
        if not p.exists() or any(x in p.parts for x in ['completed', 'archived-plans', 'archive']):
            continue
        try:
            text = p.read_text(encoding='utf-8-sig')
        except UnicodeDecodeError:
            text = p.read_text(encoding='cp1252')
            legacy_encoding.append(p.relative_to(root).as_posix())
        text = re.sub(r'```.*?```', '', text, flags=re.S)
        for match in re.finditer(r'(?<!!)\[[^\]\n]*\]\(([^)]+)\)', text):
            target = match.group(1).strip().split(' "')[0].strip('<>')
            if re.match(r'^[a-zA-Z][a-zA-Z+.-]*:', target) or target.startswith('#'):
                continue
            path = urllib.parse.unquote(target.split('#')[0].split('?')[0])
            path = re.sub(r':\d+(?:-\d+)?$', '', path)
            if not path:
                continue
            checked += 1
            if not (p.parent / path).exists():
                missing.append({'file': p.relative_to(root).as_posix(), 'target': target})
check = subprocess.run(['git', '-c', 'core.safecrlf=false', 'diff', '--check'], cwd=r, capture_output=True, text=True)
result = {
    'master_bytes': len(raw), 'master_sha256': hashlib.sha256(raw).hexdigest(),
    'pending_leaf_steps': len(ids), 'estimate_exact_leaf_coverage': True, 'unit_id_grammar': 'PASS',
    'immutable_originals_verified': len(immutable), 'moved_documents': len(m['sources']),
    'modified_python_files_parsed': len(py),
    'frozen_reference_ast_check': '83 modified frozen Python files: no differences beyond documented path-string mapping; see frozen-reference-check.json',
    'preserved_initial_dirty_task_state': True, 'git_diff_check_exit': check.returncode,
    'targeted_tests': {'command': 'uv run pytest tests/test_api.py::TestOperatorCommandsEndpoint tests/test_advisor_bridge.py tests/test_jev_benchmark.py tests/test_jev_validation.py -q', 'passed': 176, 'warnings': 101, 'elapsed_seconds': 70.34, 'note': 'Previously executed result, not rerun by this audit script.'},
    'resource_loads': '14 versions load 34 principles; 8 original operator endpoints return exact wiki bytes; 6 versions never had endpoint. See resource-loads.json.',
    'markdown_link_audit': {'scope': 'Alpha4Gate and void_furnace docs plus root README/CLAUDE; excludes completed, archived-plans, legacy archive, and fenced code; relative target existence only, not heading anchors or remote URLs; renderer line suffixes normalized', 'checked': checked, 'missing': missing, 'legacy_cp1252_read_without_conversion': legacy_encoding},
    'limitations': ['No live SC2 matches, provider requests, browser UAT, or full-suite claim.', 'Historical unavailable private memory, deleted source, and runtime artifacts are labeled; immutable originals retain original link bytes.', 'Product architecture conflicts and existing human acceptance holds remain pending.', 'Coordinator owns final Observatory fingerprint binding and cross-workspace integration.'],
}
(d / 'validation.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
