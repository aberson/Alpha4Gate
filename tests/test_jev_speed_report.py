"""Do not confuse response counts with time a hosted army choice stays in force."""

import json
import runpy
from pathlib import Path


def test_source_time_clips_the_window_and_accepts_suppressed_sequence_gaps(tmp_path):
    report = runpy.run_path(str(Path(__file__).parents[1] / 'scripts/report_jev_speed.py'))
    (tmp_path / 'state.json').write_text(
        json.dumps({'trace': {'complete': True, 'dropped_events': 0}})
    )
    events = []
    for seq, second, question, source in [
        (1, 0, 'army_mode', 'scripted_fallback'),
        (7, 5, 'army_mode', 'typesafe'),
        (15, 7, 'strategy', 'local_fallback'),
        (20, 9, 'army_mode', 'scripted_fallback'),
    ]:
        events.append(json.dumps({
            'sequence': seq, 'game_seconds': second, 'kind': 'diagnostic',
            'facts': {'decision_provider': 'typesafe', 'question_id': question, 'source': source},
        }))
    events.extend(json.dumps({
        'sequence': seq, 'game_seconds': second, 'kind': 'node', 'node_id': 'army',
        'facts': {'child': branch},
    }) for seq, second, branch in [(3, 2, 'army.recover'), (17, 8, 'army.attack')])
    events.sort(key=lambda line: json.loads(line)['sequence'])
    (tmp_path / 'events.1.jsonl').write_text('\n'.join(events))
    result = report['army_source_time'](tmp_path, 4, 12)
    assert result['seconds_by_source'] == {'scripted_fallback': 4, 'typesafe': 4}
    assert result['hosted_percent'] == 50
    assert result['army_branch_seconds'] == {'army.recover': 4, 'army.attack': 4}
    (tmp_path / 'state.json').write_text(
        json.dumps({'trace': {'complete': False, 'dropped_events': 1}})
    )
    assert report['army_source_time'](tmp_path, 4, 12) == {'status': 'incomplete_trace'}
