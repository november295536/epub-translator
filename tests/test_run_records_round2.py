"""Run records, review round 2: each test reproduces one finding (it failed
before the fix). All text is invented."""

import json
import os

from src.core.events import RunEvents, events_path
from tests import test_run_records as trr
from tests.test_run_records import _make_project

RUN = '20260101T000000Z-abcdef'


def _write(path, lines):
    os.makedirs(os.path.dirname(events_path(path)), exist_ok=True)
    with open(events_path(path), 'a', encoding='utf-8') as f:
        f.write(''.join(line + '\n' for line in lines))


def _base(**fields):
    return json.dumps({'v': 1, 'ts': '2026-01-01T00:00:00.000Z', 'run': RUN, 'source': 'cli',
                       **fields})


class TestCheckScriptShape:
    def _check(self, tmp_path, capsys):
        code = trr.TestRunRecordsCheckScript._check(None, tmp_path)
        return code, capsys.readouterr().out

    def test_wrong_shape_fails_cut_line_tolerated(self, tmp_path, capsys):
        path = _make_project(tmp_path, n=1)
        _write(path, [_base(event='pause', reason='user'), '{"cut'])
        code, out = self._check(tmp_path, capsys)
        assert code == 0 and 'skipped lines 1' in out  # a cut-off line: reported only
        for line in (_base(event='pause', reason='user', seconds='Zorblaxian said'),
                     json.dumps({'leak': 'Zorblaxian said'})):
            _write(path, [line])
            code, out = self._check(tmp_path, capsys)
            assert code == 1 and 'Zorblaxian' not in out
        assert 'seconds: wrong type' in out and 'no event, run or ts' in out

    def test_translation_call_needs_segment_and_attempt(self, tmp_path, capsys):
        path = _make_project(tmp_path, n=1)
        RunEvents(path, 'cli', run_id=RUN).emit(
            'call', kind='request', backend='fake', model='m1', timeout=60, seconds=1.0,
            outcome='accepted')
        code, out = self._check(tmp_path, capsys)
        assert code == 1 and 'call without segment, attempt' in out

    def test_unknown_result_and_bad_run_id(self, tmp_path, capsys):
        path = _make_project(tmp_path, n=1)
        ev = RunEvents(path, 'cli', run_id='Zorblaxian run')
        ev.emit('segment', segment='c1_seg0001', result='end')
        ev.emit('run_end', state='completed', translated=1)
        code, out = self._check(tmp_path, capsys)
        assert code == 1 and 'result: a string' in out and 'run 1: run_end translated=1' in out
        assert 'Zorblaxian' not in out
