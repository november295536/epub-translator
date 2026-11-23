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


def test_durations_over_replies_only():
    """A call cancelled by a pause, a quick error and a timeout do not count
    in the median and p90; timeouts are counted apart."""
    from src.core.stats import summarize, text_report
    from tests.test_run_records import _call
    records = [_call('s1', 1, seconds=8.0), _call('s2', 1, 'rejected', seconds=10.0),
               _call('s3', 1, 'cancelled', seconds=0.1), _call('s4', 1, 'rate_limit', seconds=0.2),
               _call('s5', 1, 'timeout', seconds=60.0), _call('s6', 1, 'truncated', seconds=12.0)]
    [g] = summarize(records)['models']
    assert (g['seconds_calls'], g['seconds_median'], g['seconds_p90'], g['timeouts']) == (
        3, 10.0, 12.0, 1)
    text = '\n'.join(text_report({**summarize(records), 'exists': True}))
    assert '回覆耗時（3 次有回覆的呼叫）：中位數 10.0 秒，p90 12.0 秒；逾時 1 次' in text


def _tm(monkeypatch):
    from src.web import tasks as tasks_module
    from src.web.tasks import TaskManager
    TaskManager._instance = None
    monkeypatch.setattr(tasks_module, 'COORDINATOR_TICK_SECONDS', 0.02)
    return TaskManager()


def _run_web(tmp_path, monkeypatch, tm, responder, n=1, **start):
    from src.llm.fake import FakeBackend
    from tests.conftest import use_fake_backend
    from tests.test_run_records import PROJECT, _of, _records, _wait
    path = _make_project(tmp_path, n=n)
    with use_fake_backend(FakeBackend(responder=responder)):
        task = tm.start_translation(PROJECT, str(tmp_path), num_workers=1, **start)
        _wait(lambda: task.state in ('completed', 'stopped', 'failed') and not task.threads_alive())
    _wait(lambda: _of(_records(path), 'run_end'))
    return task, path


class TestPauseRecord:
    def test_no_pause_or_resume_once_the_run_ends(self, tmp_path, monkeypatch):
        from tests.conftest import like
        from tests.test_run_records import PROJECT, _of, _records
        tm = _tm(monkeypatch)
        done = tm._workers_done
        answers = []

        def workers_done(task):
            if done(task):  # forced: a pause and a resume right after the last result
                answers.append((tm.pause_translation(PROJECT), tm.resume_translation(PROJECT)))
                return True
            return False
        monkeypatch.setattr(tm, '_workers_done', workers_done)
        try:
            task, path = _run_web(tmp_path, monkeypatch, tm, lambda r: like(r, '好'))
        finally:
            from src.web.tasks import TaskManager
            TaskManager._instance = None
        records = _records(path)
        assert answers == [(False, False)] and task.state == 'completed'
        assert not _of(records, 'pause') and not _of(records, 'resume')
        assert records[-1]['event'] == 'run_end' and len(task._threads) == 1

    def test_pause_line_written_in_the_transition(self, tmp_path, monkeypatch):
        import threading
        from src.core import events as events_module
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        from tests.test_run_records import PROJECT, _wait
        tm = _tm(monkeypatch)
        path = _make_project(tmp_path, n=2)
        held = []
        emit = events_module.RunEvents.emit

        def spy(self, event, **fields):
            if event == 'pause':
                held.append(tm.tasks[PROJECT]._lock.locked())
            return emit(self, event, **fields)
        monkeypatch.setattr(events_module.RunEvents, 'emit', spy)
        go = threading.Event()

        def responder(request):
            go.wait(10)
            return like(request, '好')
        try:
            with use_fake_backend(FakeBackend(responder=responder)):
                task = tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
                assert tm.pause_translation(PROJECT)
                go.set()
                tm.stop_translation(PROJECT)
                _wait(lambda: not task.threads_alive())
        finally:
            from src.web.tasks import TaskManager
            TaskManager._instance = None
        assert held == [True]


def test_failed_state_published_after_run_end(tmp_path, monkeypatch):
    from src.core import events as events_module
    from src.web import tasks as tasks_module
    from tests.conftest import like
    from tests.test_run_records import PROJECT, _of, _records
    tm = _tm(monkeypatch)
    seen = []
    emit = events_module.RunEvents.emit

    def spy(self, event, **fields):
        if event == 'run_end':
            seen.append(tm.tasks[PROJECT].state)
        return emit(self, event, **fields)
    monkeypatch.setattr(events_module.RunEvents, 'emit', spy)

    def explode(self, task, pm, res):
        raise ZeroDivisionError('invented')
    monkeypatch.setattr(tasks_module.TaskManager, '_apply_result', explode)
    try:
        task, path = _run_web(tmp_path, monkeypatch, tm, lambda r: like(r, '好'))
    finally:
        from src.web.tasks import TaskManager
        TaskManager._instance = None
    assert seen == ['running'] and task.state == 'failed'
    assert _of(_records(path), 'run_end')[0]['state'] == 'failed'


def test_stopped_event_after_run_end(tmp_path, monkeypatch):
    import threading
    from src.llm.fake import FakeBackend
    from src.web import tasks as tasks_module
    from tests.conftest import like, use_fake_backend
    from tests.test_run_records import PROJECT, _of, _records, _wait
    tm = _tm(monkeypatch)
    path = _make_project(tmp_path, n=2)
    published = []
    put = tasks_module.EventBroadcaster.put

    def spy(self, event):
        if event['event'] == 'stopped':
            published.append(len(_of(_records(path), 'run_end')))
        put(self, event)
    monkeypatch.setattr(tasks_module.EventBroadcaster, 'put', spy)
    go = threading.Event()

    def responder(request):
        go.wait(10)
        return like(request, '好')
    try:
        with use_fake_backend(FakeBackend(responder=responder)):
            task = tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
            tm.stop_translation(PROJECT)
            go.set()
            _wait(lambda: not task.threads_alive())
    finally:
        from src.web.tasks import TaskManager
        TaskManager._instance = None
    assert published == [1]
