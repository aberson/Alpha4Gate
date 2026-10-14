"""One Observer Rush hosted speed experiment from a verified source snapshot.

This is a development measurement, not a J2 acceptance/held-out benchmark. Never
finalizes a baseline or candidate. Load TYPESAFE_API_KEY before running; only the
game child inherits it. A case directory cannot be reused or overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from jev.benchmark import capture_source, verify_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment-root', type=Path, required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--speed', choices=('native', '1', '2', '4'), required=True)
    parser.add_argument('--seed', type=int, default=11)
    parser.add_argument('--difficulty', type=int, choices=range(1, 11), default=3)
    args = parser.parse_args()
    if not args.label or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in args.label):
        parser.error('label must contain only lowercase letters, numbers, - or _')
    if not os.environ.get('TYPESAFE_API_KEY', '').strip():
        parser.error('TYPESAFE_API_KEY is required; no game started')
    root = Path(__file__).resolve().parents[1]
    experiment = args.experiment_root.resolve()
    snapshot = (
        capture_source(root, 'v2', experiment / 'sources')
        if args.snapshot is None else verify_snapshot(args.snapshot.resolve())
    )
    if snapshot.version != 'v2' or snapshot.directory is None:
        parser.error('a captured v2 snapshot is required')
    source = snapshot.directory
    if args.speed != 'native' and '--game-speed' not in (source / 'src/jev/runner.py').read_text():
        parser.error('this snapshot predates controlled pacing; use --speed native')
    case = experiment / args.label
    case.mkdir(parents=True, exist_ok=False)
    record: dict[str, object] = {
        'schema_version': 1,
        'kind': 'jev_speed_development_case',
        'source_fingerprint': snapshot.fingerprint,
        'source_commit': snapshot.source_commit,
        'snapshot': str(source),
        'label': args.label,
        'requested_speed': args.speed,
        'seed': args.seed,
        'difficulty': args.difficulty,
        'status': 'preparing',
        'viewer': 'Observer Rush',
        'viewer_files': {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in (
                'scripts/probes/jev_battle_view.py', 'scripts/launch-jev-battle.ps1',
                'frontend/src/components/JevBattleView.tsx',
                'frontend/src/components/JevGraph.tsx',
            )
        },
    }

    def save() -> None:
        temp = case / 'case.tmp'
        temp.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        temp.replace(case / 'case.json')

    save()
    try:
        run_id = uuid.uuid4().hex
        command = [
            'powershell', '-NoProfile', '-File', str(root / 'scripts/launch-jev-battle.ps1'),
            '-Difficulty', str(args.difficulty), '-Seed', str(args.seed),
            '-MaxGameSeconds', '900', '-MaxWallSeconds', '1200',
            '-DecisionMaxRequests', '450', '-SourceSnapshot', str(source), '-RunId', run_id,
        ]
        if args.speed != 'native':
            command += ['-GameSpeed', args.speed]
        environment = dict(os.environ)
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        record.update(
            status='running', run_id=run_id,
            command=command, started_unix=time.time(),
        )
        save()
        with (case / 'process.log').open('w', encoding='utf-8') as log:
            completed = subprocess.run(
                command, cwd=root, env=environment, stdout=log, stderr=subprocess.STDOUT,
                check=False,
            )
        record.update(
            status='finished', exit_code=completed.returncode,
            ended_unix=time.time(),
        )
        if run_id:
            diagnostics = root / 'data/jev/runs' / run_id / 'diagnostics.json'
            data = json.loads(diagnostics.read_text(encoding='utf-8'))
            if Path(data['runtime_root']).resolve() != source.resolve():
                raise ValueError('run executed from a different source snapshot')
            record.update(
                outcome=data['outcome'], metrics=data['metrics'], policy_hash=data['policy_hash'],
                diagnostics=str(diagnostics),
            )
            if data['outcome']['error_code'] not in (None, 'game_timeout', 'wall_timeout'):
                record['status'] = 'invalid'
                return 1
        else:
            raise ValueError('launch session has no run evidence')
        return int(completed.returncode)
    except BaseException:
        record.update(status='interrupted_or_failed', ended_unix=time.time())
        raise
    finally:
        record['snapshot_verified_after'] = (
            verify_snapshot(source).fingerprint == snapshot.fingerprint
        )
        save()
        print(f'Speed evidence: {case / "case.json"}', flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
