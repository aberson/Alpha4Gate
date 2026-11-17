"""Read-only speed-case comparison, including time with a hosted army choice.

This reports recorded coordinator sources, not causal control of every unit order.
Persistent macro plans can retain an earlier decision after its question resets.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def army_source_time(run: Path, start: float, end: float) -> dict[str, Any]:
    state = json.loads((run / 'state.json').read_text(encoding='utf-8'))
    if not state['trace']['complete'] or state['trace']['dropped_events']:
        return {'status': 'incomplete_trace'}
    previous: tuple[float, str] | None = None
    seconds: defaultdict[str, float] = defaultdict(float)
    branch_previous: tuple[float, str] | None = None
    branch_seconds: defaultdict[str, float] = defaultdict(float)
    sequence = 0
    for segment in sorted(run.glob('events.*.jsonl'), key=lambda p: int(p.name.split('.')[1])):
        for line in segment.open(encoding='utf-8'):
            event = json.loads(line)
            # Trace suppression can leave sequence gaps; complete/dropped flags
            # describe retention. Reversed/duplicate events cannot be integrated.
            if event['sequence'] <= sequence:
                return {'status': 'incomplete_trace'}
            sequence = event['sequence']
            facts = event.get('facts') or {}
            current = float(event['game_seconds'])
            if event['kind'] == 'node' and event.get('node_id') == 'army':
                if branch_previous is not None:
                    at, branch = branch_previous
                    branch_seconds[branch] += max(0, min(end, current) - max(start, at))
                branch_previous = current, facts.get('child') or 'no_child_selected'
            if (event['kind'] != 'diagnostic' or facts.get('question_id') != 'army_mode'
                    or facts.get('decision_provider') != 'typesafe' or not facts.get('source')):
                continue
            if previous is not None:
                at, source = previous
                seconds[source] += max(0, min(end, current) - max(start, at))
            previous = current, facts['source']
    if previous is None:
        return {'status': 'not_observed'}
    at, source = previous
    seconds[source] += max(0, end - max(start, at))
    if branch_previous is not None:
        at, branch = branch_previous
        branch_seconds[branch] += max(0, end - max(start, at))
    covered = sum(seconds.values())
    return {
        'status': 'measured', 'window_start_game_seconds': start,
        'window_end_game_seconds': end,
        'seconds_by_source': {k: round(v, 3) for k, v in seconds.items()},
        'hosted_percent': round(100 * seconds['typesafe'] / covered, 2) if covered else None,
        'army_branch_seconds': {k: round(v, 3) for k, v in branch_seconds.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('experiment_root', type=Path)
    args = parser.parse_args()
    report = []
    for path in sorted(args.experiment_root.glob('*/case.json')):
        case = json.loads(path.read_text(encoding='utf-8'))
        if case.get('status') != 'finished' or 'diagnostics' not in case:
            continue
        diagnostics = Path(case['diagnostics'])
        d = json.loads(diagnostics.read_text(encoding='utf-8'))
        m = d['metrics']
        first = m['first_attack'].get('game_seconds')
        end = d['outcome']['game_seconds']
        report.append({
            'case': case['label'], 'viewer': case.get('viewer', 'separate_windows'),
            'run_id': d['run_id'], 'outcome': d['outcome'], 'metrics': m,
            'army_source_after_first_attack': (
                army_source_time(diagnostics.parent, first, end) if first is not None
                else {'status': 'first_attack_not_observed'}
            ),
        })
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
