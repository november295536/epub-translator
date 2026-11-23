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


def test_retranslate_keeps_counts_when_the_answer_fails(tmp_path, monkeypatch):
    from src.llm.fake import FakeBackend
    from src.web.blueprints import review as review_module
    from src.web.server import create_app
    from tests.conftest import like, use_fake_backend
    from tests.test_run_records import PROJECT, _of, _records
    path = _make_project(tmp_path, n=1)

    def explode(*a, **k):
        raise ZeroDivisionError('invented')
    monkeypatch.setattr(review_module, 'record_failure_log', explode)
    client = create_app(projects_dir=str(tmp_path)).test_client()
    with use_fake_backend(FakeBackend(responder=lambda r: like(r, '好'))):
        assert client.post(f'/api/projects/{PROJECT}/segments/c1_seg0001/retranslate') \
            .status_code == 500
    records = _records(path)
    [seg], [end] = _of(records, 'segment'), _of(records, 'run_end')
    assert seg['result'] == 'translated'
    assert (end['state'], end['error'], end['translated']) == ('failed', 'ZeroDivisionError', 1)


class TestCliExtractLifecycle:
    def _run(self, tmp_path, monkeypatch, patch=None):
        import json
        from click.testing import CliRunner
        import translate
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        from tests.test_run_records import _of, _records
        path = _make_project(tmp_path, n=1)
        if patch:
            patch()
        reply = json.dumps([{'term': 'Quillfeather', 'translation': '羽筆', 'category': 'term'}])
        with use_fake_backend(FakeBackend([reply])):
            CliRunner().invoke(translate.cli, ['glossary', 'extract', '-p', path])
        return _of(_records(path), 'run_end')

    def test_completed_after_the_save(self, tmp_path, monkeypatch):
        [end] = self._run(tmp_path, monkeypatch)
        assert (end['state'], end['terms'], end['added']) == ('completed', 1, 1)

    def test_failed_save(self, tmp_path, monkeypatch):
        from src.glossary.glossary_manager import GlossaryManager

        def explode(self, *a, **k):
            raise OSError('disk full')
        [end] = self._run(tmp_path, monkeypatch,
                          lambda: monkeypatch.setattr(GlossaryManager, 'save', explode))
        assert (end['state'], end['error'], end['terms']) == ('failed', 'OSError', 1)
        assert 'added' not in end

    def test_failure_before_the_calls(self, tmp_path, monkeypatch):
        import src.glossary.term_extractor as te

        def explode(*a, **k):
            raise ZeroDivisionError('invented')
        [end] = self._run(tmp_path, monkeypatch,
                          lambda: monkeypatch.setattr(te.TermExtractor, '__init__', explode))
        assert (end['state'], end['error']) == ('failed', 'ZeroDivisionError')


class TestExtractionRound2:
    def test_ctrl_c_while_reading_a_reply(self, tmp_path, monkeypatch):
        import pytest
        from src.glossary import term_extractor as te
        from src.llm.fake import FakeBackend
        from tests.test_run_records import _of, _records

        def interrupt(*a, **k):
            raise KeyboardInterrupt()
        monkeypatch.setattr(te.TermExtractor, '_parse_reply', staticmethod(interrupt))
        extractor = te.TermExtractor(model='m', backend=FakeBackend(['[]']),
                                     events=RunEvents(str(tmp_path), 'extract'))
        with pytest.raises(KeyboardInterrupt):
            extractor.extract_from_text('Nothing.')
        [c] = _of(_records(str(tmp_path)), 'call')
        assert (c['outcome'], c['error']) == ('cancelled', 'KeyboardInterrupt')

    def test_ctrl_c_while_reading_a_consolidation(self, tmp_path, monkeypatch):
        import pytest
        from src.glossary import term_extractor as te
        from src.llm.fake import FakeBackend
        from tests.test_run_records import _of, _records

        def interrupt(*a, **k):
            raise KeyboardInterrupt()
        monkeypatch.setattr(te, '_top_level_list', interrupt)
        extractor = te.TermExtractor(model='m', backend=FakeBackend(['[]']),
                                     events=RunEvents(str(tmp_path), 'extract'))
        group = {'term': 'Quill', 'translations': {'羽': 1, '筆': 1}, 'categories': {}}
        from collections import Counter
        group = {**group, 'translations': Counter(group['translations']), 'categories': Counter()}
        with pytest.raises(KeyboardInterrupt):
            extractor.consolidate([group])
        [c] = _of(_records(str(tmp_path)), 'call')
        assert (c['kind'], c['outcome']) == ('consolidate', 'cancelled')

    def test_default_model_is_the_backends(self, tmp_path):
        from src.glossary.term_extractor import TermExtractor
        from src.llm.fake import FakeBackend
        from tests.test_run_records import _of, _records
        backend = FakeBackend(['[]'])
        extractor = TermExtractor(backend=backend, events=RunEvents(str(tmp_path), 'extract'))
        extractor.extract_from_text('Nothing.')
        [c] = _of(_records(str(tmp_path)), 'call')
        assert c['model'] == backend.default_model and c['model']




def test_delete_waits_for_an_append_in_progress(tmp_path, monkeypatch):
    """An append that passed its checks when the delete starts: the delete
    waits for it, so the directory is not recreated behind the delete."""
    import threading
    from src.core import events as events_module
    from src.web.server import create_app
    from tests.test_run_records import PROJECT
    path = _make_project(tmp_path, n=1)
    run = RunEvents(path, 'extract')
    inside, go = threading.Event(), threading.Event()
    makedirs = os.makedirs

    def slow_makedirs(name, *a, **k):
        if threading.current_thread().name == 'appender':
            inside.set()
            go.wait(10)
        return makedirs(name, *a, **k)
    monkeypatch.setattr(events_module.os, 'makedirs', slow_makedirs)
    appender = threading.Thread(target=run.emit, args=('run_end',), name='appender')
    appender.start()
    assert inside.wait(10)
    client = create_app(projects_dir=str(tmp_path)).test_client()
    deleter = threading.Thread(target=lambda: client.delete(f'/api/projects/{PROJECT}'))
    deleter.start()
    deleter.join(0.5)  # before the fix the delete finishes here, the append still pending
    go.set()
    appender.join(10)
    deleter.join(10)
    assert not os.path.exists(path)


def test_codes_by_count_and_measured_duration():
    from src.core.stats import summarize
    from tests.test_run_records import _call, _rec
    records = [_rec('run_start', ts='2026-01-01T00:00:00.000Z'),
               _call('s1', 1, 'rejected', issues=['tag_count']),
               _call('s1', 2, 'rejected', issues=['not_html']),
               _call('s1', 3, 'rejected', issues=['not_html']),
               _call('s2', 1, 'rejected', issues=['empty_reply']),
               _rec('run_end', ts='2026-01-01T00:00:09.000Z', state='completed', seconds=7.25)]
    s = summarize(records)
    assert list(s['rejected_issues']) == ['not_html', 'empty_reply', 'tag_count']
    assert s['runs'][0]['duration'] == 7.25  # the run's own measure, not the 9 s between lines


def test_page_sorts_codes_by_count(tmp_path):
    from src.web.server import create_app
    from tests.test_run_records import PROJECT
    _make_project(tmp_path)
    page = create_app(projects_dir=str(tmp_path)).test_client() \
        .get(f'/project/{PROJECT}/stats').get_data(as_text=True)
    assert 'this.byCount(counts)' in page and 'b[1] - a[1]' in page


def test_no_run_end_without_run_start(tmp_path, monkeypatch):
    import pytest
    from src.web import tasks as tasks_module
    from tests.test_run_records import PROJECT
    tm = _tm(monkeypatch)
    path = _make_project(tmp_path, n=1)

    def explode(settings):
        raise ZeroDivisionError('invented')
    monkeypatch.setattr(tasks_module, 'settings_fields', explode)
    try:
        with pytest.raises(ZeroDivisionError):
            tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
    finally:
        from src.web.tasks import TaskManager
        TaskManager._instance = None
    assert not os.path.exists(events_path(path))  # neither a run_start nor a lone run_end


def test_free_text_category_logged_by_length(tmp_path):
    from click.testing import CliRunner
    import translate
    from src.core import proclog
    path = _make_project(tmp_path, n=1)
    CliRunner().invoke(translate.cli, ['glossary', 'add', 'Quill', '羽', '-c', 'Zorblaxian lore',
                                       '-p', path])
    CliRunner().invoke(translate.cli, ['glossary', 'add', 'Mira', '米拉', '-c', 'person',
                                       '-p', path])
    with open(os.path.join(os.environ[proclog.LOG_DIR_ENV], 'cli.log'), encoding='utf-8') as f:
        log = f.read()
    assert 'Zorblaxian' not in log and 'category=<15 字元>' in log
    assert 'category=person' in log  # a known category is no text
