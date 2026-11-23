"""Run records, review round 1: each test reproduces one finding (it failed
before the fix). All text is invented."""

import os
import subprocess
import sys

from src.core import proclog
from tests.conftest import _REPO_ROOT
from tests.test_run_records import _make_project


def _run_cli(tmp_path, prelude, args):
    """translate.py in a fresh process (no pytest handlers on the root
    logger), with `prelude` run first; returns (stderr, process log dir)."""
    code = ('import sys, logging; sys.path.insert(0, sys.argv[1])\n'
            'import translate\n' + prelude +
            'translate.cli(sys.argv[2:])\n')
    logs = tmp_path / 'logs'
    env = dict(os.environ, **{proclog.LOG_DIR_ENV: str(logs)})
    result = subprocess.run([sys.executable, '-c', code, _REPO_ROOT, *args], env=env,
                            capture_output=True, text=True, timeout=60)
    return result, logs


class TestTerminalOutput:
    def test_cli_warning_on_stderr(self, tmp_path):
        """A src.* warning during a CLI command reaches the terminal (as
        logging.lastResort printed it before the file handler); INFO does not."""
        path = _make_project(tmp_path / 'projects')
        prelude = ('find = translate._find_project\n'
                   'def noisy(p):\n'
                   '    logging.getLogger("src.x").info("INFO-MARK")\n'
                   '    logging.getLogger("src.x").warning("WARN-MARK")\n'
                   '    return find(p)\n'
                   'translate._find_project = noisy\n')
        result, logs = _run_cli(tmp_path, prelude, ['status', '-p', path])
        assert result.returncode == 0, result.stderr
        assert 'WARN-MARK' in result.stderr and 'INFO-MARK' not in result.stderr
        log = (logs / 'cli.log').read_text(encoding='utf-8')
        assert 'WARN-MARK' in log and 'INFO-MARK' in log

    def test_review_keeps_terminal_and_cli_log(self, tmp_path):
        """`translate.py review`: the server's request lines reach the
        terminal as before, the command's start and end lines stay in
        cli.log, and no web.log (nor web end line) is written."""
        path = _make_project(tmp_path / 'projects')
        prelude = ('import flask, webbrowser\n'
                   'webbrowser.open = lambda url: None\n'
                   'def run(self, **kw):\n'
                   '    logging.getLogger("werkzeug").info("REQ-MARK")\n'
                   'flask.Flask.run = run\n')
        result, logs = _run_cli(tmp_path, prelude, ['review', '-p', path])
        assert result.returncode == 0, result.stderr
        assert 'REQ-MARK' in result.stderr
        log = (logs / 'cli.log').read_text(encoding='utf-8')
        assert 'start: cli review' in log and 'REQ-MARK' in log
        assert log.splitlines()[-1].split(': ', 1)[-1].startswith('end: exit 0 after')
        assert 'end: web server stopped' not in log
        assert not (logs / 'web.log').exists()

    def test_log_path_absolute(self, tmp_path, monkeypatch):
        monkeypatch.setenv(proclog.LOG_DIR_ENV, 'relative-logs')
        monkeypatch.chdir(tmp_path)
        proclog.teardown()
        try:
            assert proclog.setup('projects', 'cli', 'cli status') == \
                str(tmp_path / 'relative-logs' / 'cli.log')
        finally:
            proclog.teardown()


class TestCallLostWhenCheckRaises:
    """A reply whose cleaning or check raises is still a call made: it is
    recorded, and the unexpected outcome counts it."""

    def _run(self, tmp_path, monkeypatch, where, error, module=None):
        import src.core.translator as translator
        from src.core.events import RunEvents
        from src.core.translator import TranslationEngine, translate_segment
        from src.llm.fake import FakeBackend
        from tests.test_run_records import _of, _records, _seg

        def explode(*a, **k):
            raise error
        monkeypatch.setattr(module or translator, where, explode)
        ev = RunEvents(str(tmp_path), 'cli', run_id='r')
        engine = TranslationEngine(backend=FakeBackend(['<p k="1">你好。</p>']), events=ev,
                                   wait=lambda *a: False)
        try:
            outcome = translate_segment(engine, _seg(), [_seg()])
        except BaseException as e:  # noqa: BLE001 - Ctrl-C goes through
            outcome = e
        return outcome, _of(_records(str(tmp_path)), 'call')

    def test_check_raises(self, tmp_path, monkeypatch):
        from src.core import validation
        outcome, calls = self._run(tmp_path, monkeypatch, 'check_keys',
                                   ZeroDivisionError('invented'), module=validation)
        assert outcome.issues[0].code == 'unexpected' and outcome.calls == 1
        [c] = calls
        assert (c['outcome'], c['error']) == ('unexpected', 'ZeroDivisionError')

    def test_clean_raises(self, tmp_path, monkeypatch):
        outcome, calls = self._run(tmp_path, monkeypatch, '_clean_html',
                                   ZeroDivisionError('invented'))
        assert outcome.issues[0].code == 'unexpected'
        assert outcome.calls == 1 and outcome.failure.calls == 1
        [c] = calls
        assert (c['outcome'], c['error'], c['attempt']) == ('unexpected', 'ZeroDivisionError', 1)
        assert c['chars_out'] > 0 and c['kind'] == 'request'

    def test_ctrl_c_in_clean(self, tmp_path, monkeypatch):
        outcome, calls = self._run(tmp_path, monkeypatch, '_clean_html', KeyboardInterrupt())
        assert isinstance(outcome, KeyboardInterrupt)
        [c] = calls
        assert c['outcome'] == 'cancelled'


class TestWebRunEnd:
    def test_pause_after_the_last_result(self, tmp_path, monkeypatch):
        """A pause that arrives after the last result, while the workers
        leave: the run is completed (nothing is left to resume), and its
        run_end line is written before the end is published."""
        from src.core.events import events_path
        from src.llm.fake import FakeBackend
        from src.web import tasks as tasks_module
        from src.web.tasks import TaskManager
        from tests.conftest import like, use_fake_backend
        from tests.test_run_records import PROJECT, _of, _records

        TaskManager._instance = None
        monkeypatch.setattr(tasks_module, 'COORDINATOR_TICK_SECONDS', 0.02)
        tm = TaskManager()
        path = _make_project(tmp_path, n=1)
        done = tm._workers_done

        def workers_done(task):
            if done(task):
                tm.pause_translation(PROJECT)  # the forced interleaving
                return True
            return False
        monkeypatch.setattr(tm, '_workers_done', workers_done)
        published = []
        put = tasks_module.EventBroadcaster.put

        def spy(self, event):
            if event['event'] in ('complete', 'stopped', 'error'):
                published.append((event['event'], [
                    r['event'] for r in _of(_records(path), 'run_end')]))
            put(self, event)
        monkeypatch.setattr(tasks_module.EventBroadcaster, 'put', spy)
        try:
            with use_fake_backend(FakeBackend(responder=lambda r: like(r, '好'))):
                task = tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
                task._coordinator.join(timeout=20)
            assert task.state == 'completed'
            [end] = _of(_records(path), 'run_end')
            assert end['state'] == 'completed' and end['translated'] == 1
            assert published == [('complete', ['run_end'])]
            assert os.path.exists(events_path(path))
        finally:
            TaskManager._instance = None


def _no_thread(monkeypatch, module):
    """Threads of `module` cannot start (as when the process has too many)."""
    import threading

    class Refused(threading.Thread):
        def start(self):
            raise RuntimeError("can't start new thread")
    monkeypatch.setattr(module.threading, 'Thread', Refused)


class TestExtractionEnd:
    def test_run_end_before_status_says_ended(self, tmp_path, monkeypatch):
        import json
        from src.core import events as events_module
        from src.llm.fake import FakeBackend
        from src.web.blueprints import glossary as glossary_module
        from src.web.server import create_app
        from tests.conftest import use_fake_backend
        from tests.test_run_records import PROJECT, _of, _records, _wait

        path = _make_project(tmp_path, n=1)
        running_at_end = []
        emit = events_module.RunEvents.emit

        def spy(self, event, **fields):
            if event == 'run_end':
                running_at_end.append(glossary_module._extraction_tasks[PROJECT]['running'])
            return emit(self, event, **fields)
        monkeypatch.setattr(events_module.RunEvents, 'emit', spy)
        client = create_app(projects_dir=str(tmp_path)).test_client()
        reply = json.dumps([{'term': 'Quillfeather', 'translation': '羽筆', 'category': 'term'}])
        with use_fake_backend(FakeBackend([reply])):
            assert client.post(f'/api/projects/{PROJECT}/glossary/extract').status_code == 200
            _wait(lambda: client.get(f'/api/projects/{PROJECT}/glossary/extract/status')
                  .get_json()['status'] != 'running')
        assert running_at_end == [True]
        assert _of(_records(path), 'run_end')  # there as soon as the status is not running

    def test_thread_start_failure(self, tmp_path, monkeypatch):
        from src.web.blueprints import glossary as glossary_module
        from src.web.server import create_app
        from tests.test_run_records import PROJECT, _of, _records

        path = _make_project(tmp_path, n=1)
        client = create_app(projects_dir=str(tmp_path)).test_client()
        _no_thread(monkeypatch, glossary_module)
        assert client.post(f'/api/projects/{PROJECT}/glossary/extract').status_code == 500
        assert glossary_module._extraction_tasks[PROJECT]['running'] is False
        [end] = _of(_records(path), 'run_end')
        assert (end['state'], end['error']) == ('failed', 'RuntimeError')


def test_translation_thread_start_failure(tmp_path, monkeypatch):
    import pytest
    from src.web import tasks as tasks_module
    from src.web.tasks import TaskManager
    from tests.test_run_records import PROJECT, _of, _records

    TaskManager._instance = None
    try:
        tm = TaskManager()
        path = _make_project(tmp_path, n=1)
        _no_thread(monkeypatch, tasks_module)
        with pytest.raises(RuntimeError):
            tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
        [end] = _of(_records(path), 'run_end')
        assert (end['state'], end['error']) == ('failed', 'RuntimeError')
        assert PROJECT not in tm.tasks
    finally:
        TaskManager._instance = None


class TestRetiredRecord:
    """A run still going when its project is replaced or deleted (a
    retranslation, a web extraction) stops writing: no line in the new
    book's record, no project directory created again."""

    def test_replaced(self, tmp_path, sample_epub):
        from src.core.events import RunEvents, events_path
        from src.web.server import create_app
        client = create_app(projects_dir=str(tmp_path)).test_client()
        res = client.post('/api/projects', json={'epub_path': sample_epub})
        path = os.path.join(str(tmp_path), res.get_json()['name'])
        old_run = RunEvents(path, 'retranslate')
        assert old_run.emit('run_start', segments=1)
        assert client.post('/api/projects', json={'epub_path': sample_epub}).status_code == 200
        assert old_run.emit('run_end', state='completed') is False
        assert not os.path.exists(events_path(path))
        assert RunEvents(path, 'web').emit('run_start', segments=1)  # a new run writes

    def test_deleted(self, tmp_path):
        from src.core.events import RunEvents
        from src.web.server import create_app
        from tests.test_run_records import PROJECT
        path = _make_project(tmp_path, n=1)
        old_run = RunEvents(path, 'extract')
        assert old_run.emit('run_start', segments=1)
        client = create_app(projects_dir=str(tmp_path)).test_client()
        assert client.delete(f'/api/projects/{PROJECT}').status_code == 200
        assert old_run.emit('run_end', state='completed') is False
        assert not os.path.exists(path)

    def test_never_creates_the_project_directory(self, tmp_path):
        from src.core.events import RunEvents
        gone = tmp_path / 'gone'
        assert RunEvents(str(gone), 'cli').emit('run_start') is False
        assert not gone.exists()


def test_short_write_is_reported(tmp_path, monkeypatch, caplog):
    import logging
    from src.core import events as events_module
    monkeypatch.setattr(events_module.os, 'write', lambda fd, data: 3)
    with caplog.at_level(logging.WARNING, logger='src.core.events'):
        assert events_module.RunEvents(str(tmp_path), 'cli').emit('pause') is False
    assert '只寫入 3 /' in caplog.text


class TestExtractionRecordedAfterParsing:
    """An extraction or consolidation reply that cannot be read is
    recorded rejected (issue code unusable_reply), not accepted."""

    def test_unusable_replies(self, tmp_path):
        import json
        from src.core.events import RunEvents
        from src.glossary.term_extractor import UNUSABLE_REPLY, TermExtractor
        from src.llm.fake import FakeBackend
        from tests.test_run_records import _of, _records
        word = 'Quillfeather'
        replies = [json.dumps([{'term': word, 'translation': '羽筆', 'category': 'term'}]),
                   json.dumps([{'term': word, 'translation': '鵝毛筆', 'category': 'term'}]),
                   'I cannot help with that.',            # third batch: no JSON
                   '{"term": "x", "translation": "y"}']  # consolidation: not a list
        extractor = TermExtractor(model='m', backend=FakeBackend(replies),
                                  events=RunEvents(str(tmp_path), 'extract'))
        extractor.extract_from_segments([{'text': f'{word} ' * 300} for _ in range(3)])
        calls = _of(_records(str(tmp_path)), 'call')
        assert [(c['kind'], c['outcome'], c.get('issues')) for c in calls] == [
            ('extract', 'accepted', None), ('extract', 'accepted', None),
            ('extract', 'rejected', [UNUSABLE_REPLY]),
            ('consolidate', 'rejected', [UNUSABLE_REPLY])]

    def test_empty_list_is_accepted(self, tmp_path):
        from src.core.events import RunEvents
        from src.glossary.term_extractor import TermExtractor
        from src.llm.fake import FakeBackend
        from tests.test_run_records import _of, _records
        extractor = TermExtractor(model='m', backend=FakeBackend(['[]']),
                                  events=RunEvents(str(tmp_path), 'extract'))
        assert extractor.extract_from_text('Nothing here.') == []
        [c] = _of(_records(str(tmp_path)), 'call')
        assert c['outcome'] == 'accepted'


class TestStatisticsRound1:
    def test_split_is_a_part_of_more_calls(self):
        from src.core.stats import summarize
        from tests.test_run_records import _call, _rec
        records = [_call('s1', 1), _rec('segment', segment='s1', result='failed', calls=4,
                                        split=True, issues=['tag_count']),
                   _call('s2', 1), _rec('segment', segment='s2', result='translated', calls=4,
                                        split=True)]
        [g] = summarize(records)['models']
        assert (g['more_calls'], g['split'], g['failed']) == (1, 1, 1)

    def test_extraction_calls_apart(self):
        from src.core.stats import summarize, text_report
        from tests.test_run_records import _call, _rec

        def extraction(kind, **fields):
            return _rec('call', kind=kind, backend='b', model='m1', effort='low',
                        outcome='accepted', seconds=1.0, **{**fields})
        records = [_call('s1', 1), extraction('extract'),
                   {**extraction('consolidate'), 'outcome': 'rejected',
                    'issues': ['unusable_reply']}]
        s = summarize(records)
        assert [(g['work'], g['calls']) for g in s['models']] == [('translation', 1),
                                                                 ('extraction', 2)]
        assert any(line.startswith('  術語提取：b / m1 / low') for line in
                   text_report({**s, 'exists': True}))

    def test_since_out_of_range(self, tmp_path):
        import pytest
        from click.testing import CliRunner
        import translate
        from src.core.stats import parse_since
        from src.web.server import create_app
        from tests.test_run_records import PROJECT
        with pytest.raises(ValueError):
            parse_since('0001-01-01T00:00:00+14:00')
        path = _make_project(tmp_path)
        result = CliRunner().invoke(translate.cli, ['stats', '-p', path, '--since',
                                                    '0001-01-01T00:00:00+14:00'])
        assert result.exit_code == 2 and not isinstance(result.exception, OverflowError)
        client = create_app(projects_dir=str(tmp_path)).test_client()
        assert client.get(f'/api/projects/{PROJECT}/stats?since=0001-01-01T00:00:00%2B14:00') \
            .status_code == 400

    def test_wrong_types_are_skipped(self, tmp_path):
        import json
        from src.core.events import events_path
        from src.core.stats import project_stats
        from tests.test_run_records import _call, _rec
        lines = [_call('s1', 1, issues=7), _rec('segment', segment=['s1'], result='translated'),
                 _call('s2', 1, seconds='slow'), _rec('segment', segment='s2', split='yes'),
                 _call('s3', 1)]
        os.makedirs(os.path.join(tmp_path, 'log'))
        with open(events_path(str(tmp_path)), 'w', encoding='utf-8') as f:
            f.write(''.join(json.dumps(r) + '\n' for r in lines))
        s = project_stats(str(tmp_path))
        assert (s['records'], s['skipped_lines']) == (1, 4)

    def test_cli_records_workers_asked_for(self, tmp_path):
        from click.testing import CliRunner
        import translate
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        from tests.test_run_records import _of, _records
        path = _make_project(tmp_path, n=1)
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '好'))):
            assert CliRunner().invoke(translate.cli, ['start', '-p', path, '-w', '3']).exit_code == 0
        assert _of(_records(path), 'run_start')[0]['workers'] == 3  # as the web: asked for

    def test_page_lists_work_and_backend(self, tmp_path):
        from src.web.server import create_app
        from tests.test_run_records import PROJECT
        _make_project(tmp_path)
        page = create_app(projects_dir=str(tmp_path)).test_client() \
            .get(f'/project/{PROJECT}/stats').get_data(as_text=True)
        assert "g.backend + ' / ' + g.model" in page and '<th>用途</th>' in page
        assert '需多次呼叫' in page and '需重試或分割' not in page


class TestCheckScriptRound1:
    def _check(self, projects, capsys):
        from tests.test_run_records import TestRunRecordsCheckScript
        code = TestRunRecordsCheckScript._check(None, projects)
        return code, capsys.readouterr().out

    def test_book_with_enum_words_passes(self, tmp_path, capsys):
        """A book whose text has the record's own words (request, accepted,
        completed, pending, timeout...) passes: closed sets, no word list."""
        from click.testing import CliRunner
        import translate
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        word = 'Request accepted, completed, pending timeout rejected selected'
        path = _make_project(tmp_path, n=2, word=word)
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '好'))):
            assert CliRunner().invoke(translate.cli, ['start', '-p', path]).exit_code == 0
        code, out = self._check(tmp_path, capsys)
        assert code == 0, out

    def test_unknown_key_and_text_not_printed(self, tmp_path, capsys):
        from src.core.events import RunEvents
        path = _make_project(tmp_path, n=1)
        RunEvents(path, 'cli', run_id='20260101T000000Z-abcdef').emit(
            'call', kind='request', backend='fake', model='m', timeout=60, seconds=1.0,
            outcome='accepted', Zorblaxian='x', error='Zorblaxian')
        code, out = self._check(tmp_path, capsys)
        assert code == 1 and '1 unknown keys' in out and 'error: a string' in out
        assert 'Zorblaxian' not in out

    def test_required_fields_and_skipped_lines(self, tmp_path, capsys):
        from src.core.events import RunEvents, events_path
        path = _make_project(tmp_path, n=1)
        RunEvents(path, 'cli', run_id='20260101T000000Z-abcdef').emit('segment', result='failed')
        with open(events_path(path), 'a', encoding='utf-8') as f:
            f.write('{"cut\n')
        code, out = self._check(tmp_path, capsys)
        assert code == 1 and 'segment without segment' in out and 'skipped lines 1' in out


# --- survivors of the round-1 mutation pass that pointed at gaps in the tests ---------

def _tm(monkeypatch):
    from src.web import tasks as tasks_module
    from src.web.tasks import TaskManager
    TaskManager._instance = None
    monkeypatch.setattr(tasks_module, 'COORDINATOR_TICK_SECONDS', 0.02)
    return TaskManager()


class TestWebRecordFields:
    def _run(self, tmp_path, monkeypatch, responder, n=2, **start):
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        from tests.test_run_records import PROJECT, _of, _records, _wait
        tm = _tm(monkeypatch)
        path = _make_project(tmp_path, n=n)
        try:
            with use_fake_backend(FakeBackend(responder=responder)):
                task = tm.start_translation(PROJECT, str(tmp_path), **start)
                _wait(lambda: task.state in ('completed', 'stopped', 'failed')
                      and not task.threads_alive())
            _wait(lambda: _of(_records(path), 'run_end'))
            return tm, task, path, _records(path)
        finally:
            from src.web.tasks import TaskManager
            TaskManager._instance = None

    def test_modes_and_failed_result(self, tmp_path, monkeypatch):
        from src.llm import RefusalError
        from tests.test_run_records import _of

        def refuse(request):
            raise RefusalError('no')
        _, _, path, records = self._run(tmp_path, monkeypatch, refuse, n=1)
        [seg] = _of(records, 'segment')
        assert (seg['segment'], seg['result'], seg['issues']) == ('c1_seg0001', 'failed', ['refusal'])
        assert _of(records, 'run_end')[0]['failed'] == 1
        from tests.conftest import like
        _, _, _, again = self._run(tmp_path / 'b', monkeypatch, lambda r: like(r, '好'), n=1,
                                   retry_failed=True)
        assert _of(again, 'run_start')[0]['mode'] == 'retry_failed'
        _, _, _, chosen = self._run(tmp_path / 'c', monkeypatch, lambda r: like(r, '好'), n=1,
                                    segment_ids=['c1_seg0001'])
        assert _of(chosen, 'run_start')[0]['mode'] == 'selected'

    def test_run_end_seconds_and_failure(self, tmp_path, monkeypatch):
        from tests.conftest import like
        from tests.test_run_records import _of
        from src.web import tasks as tasks_module
        clock = iter(range(1000, 100000, 7))
        monkeypatch.setattr(tasks_module.TaskManager, 'clock', staticmethod(lambda: next(clock)))
        _, task, _, records = self._run(tmp_path, monkeypatch, lambda r: like(r, '好'), n=1)
        [end] = _of(records, 'run_end')
        assert end['seconds'] > 0 and end['seconds'] % 7 == 0  # clock steps of 7 s

        def explode(self, task, pm, res):
            raise ZeroDivisionError('invented')
        monkeypatch.setattr(tasks_module.TaskManager, '_apply_result', explode)
        _, task, _, records = self._run(tmp_path / 'b', monkeypatch, lambda r: like(r, '好'), n=1)
        [end] = _of(records, 'run_end')
        assert (end['state'], end['error']) == ('failed', 'ZeroDivisionError')

    def test_dropped_before_call(self, tmp_path, monkeypatch):
        import threading
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        from tests.test_run_records import PROJECT, _of, _records, _wait
        tm = _tm(monkeypatch)
        path = _make_project(tmp_path, n=2)
        started, go = threading.Event(), threading.Event()

        def responder(request):
            started.set()
            go.wait(10)
            return like(request, '好')
        try:
            with use_fake_backend(FakeBackend(responder=responder)):
                task = tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
                assert started.wait(10)
                with tm.transaction(PROJECT, str(tmp_path)) as pm:
                    pm.update_translation('c1_seg0002', '人工譯文')  # still queued
                    pm.save()
                go.set()
                _wait(lambda: task.state == 'completed' and not task.threads_alive())
            _wait(lambda: _of(_records(path), 'run_end'))
        finally:
            from src.web.tasks import TaskManager
            TaskManager._instance = None
        dropped = [s for s in _of(_records(path), 'segment') if s['result'] == 'dropped']
        assert [(s['segment'], s['reason']) for s in dropped] == [
            ('c1_seg0002', 'edited_before_call')]

    def test_user_pause_record(self, tmp_path, monkeypatch):
        import threading
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        from tests.test_run_records import PROJECT, _of, _records, _wait
        tm = _tm(monkeypatch)
        path = _make_project(tmp_path, n=2)
        first = threading.Event()

        def responder(request):
            first.set()
            return like(request, '好')
        try:
            with use_fake_backend(FakeBackend(responder=responder)):
                task = tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
                _wait(lambda: task.completed >= 1)
                assert tm.pause_translation(PROJECT)
                assert tm.pause_translation(PROJECT)  # a repeated click: not recorded
                assert tm.resume_translation(PROJECT)
                _wait(lambda: task.state == 'completed' and not task.threads_alive())
            _wait(lambda: _of(_records(path), 'run_end'))
        finally:
            from src.web.tasks import TaskManager
            TaskManager._instance = None
        [pause] = _of(_records(path), 'pause')
        assert pause['reason'] == 'user' and pause['completed'] >= 1

    def test_auto_resume_and_extension_time(self, tmp_path, monkeypatch):
        from src.llm import RateLimitError
        from src.llm.fake import FakeBackend
        from src.web import tasks as tasks_module
        from tests.conftest import like, use_fake_backend
        from tests.test_run_records import PROJECT, _of, _records, _wait
        now = {'t': 1000.0}
        monkeypatch.setattr(tasks_module.TaskManager, 'clock', staticmethod(lambda: now['t']))
        tm = _tm(monkeypatch)
        path = _make_project(tmp_path, n=1)
        calls = {'n': 0}

        def responder(request):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RateLimitError('limit')
            return like(request, '好')
        try:
            with use_fake_backend(FakeBackend(responder=responder)):
                task = tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
                _wait(lambda: task.state == 'paused')
                now['t'] += 120.0
                with task._lock:  # a second rate limit 120 s into the 300 s wait
                    tm._pause_for(task, RateLimitError('again'), 'rate_limit')
                now['t'] += 400.0  # the wait is over
                _wait(lambda: task.state == 'completed' and not task.threads_alive())
            _wait(lambda: _of(_records(path), 'run_end'))
        finally:
            from src.web.tasks import TaskManager
            TaskManager._instance = None
        records = _records(path)
        assert _of(records, 'wait_extended')[0]['resume_in'] == 300.0
        assert _of(records, 'resume')[0]['reason'] == 'auto'


class TestCliRecordFields:
    def test_rate_limit_pause_fields(self, tmp_path, monkeypatch):
        from click.testing import CliRunner
        import translate
        from src.llm import RateLimitError
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        from tests.test_run_records import _of, _records
        path = _make_project(tmp_path, n=1)
        monkeypatch.setattr(translate, '_sleep', lambda s: None)
        with use_fake_backend(FakeBackend([RateLimitError('limit'), '<p k="1">好</p>'])):
            assert CliRunner().invoke(translate.cli, ['start', '-p', path]).exit_code == 0
        records = _records(path)
        [pause] = _of(records, 'pause')
        assert (pause['error'], pause['resume_in']) == ('RateLimitError', 300)
        assert _of(records, 'resume')[0]['reason'] == 'auto'

    def test_parallel_segment_ids_and_stop_error(self, tmp_path):
        from click.testing import CliRunner
        import translate
        from src.llm import BackendUnavailableError
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        from tests.test_run_records import _of, _records
        path = _make_project(tmp_path, n=2)
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '好'))):
            assert CliRunner().invoke(translate.cli, ['start', '-p', path, '-w', '2']).exit_code == 0
        assert sorted(s['segment'] for s in _of(_records(path), 'segment')) == [
            'c1_seg0001', 'c1_seg0002']

        def gone(request):
            raise BackendUnavailableError('missing')
        other = _make_project(tmp_path / 'o', n=2)
        with use_fake_backend(FakeBackend(responder=gone)):
            CliRunner().invoke(translate.cli, ['start', '-p', other, '-w', '2'])
        [end] = _of(_records(other), 'run_end')
        assert (end['state'], end['error']) == ('stopped', 'BackendUnavailableError')

    def test_failed_run_and_retry_mode(self, tmp_path, monkeypatch):
        from click.testing import CliRunner
        import translate
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        from tests.test_run_records import _of, _records
        path = _make_project(tmp_path, n=1)

        def explode(*a, **k):
            raise ZeroDivisionError('invented')
        monkeypatch.setattr(translate, '_start_sequential', explode)
        with use_fake_backend(FakeBackend([])):
            CliRunner().invoke(translate.cli, ['start', '-p', path, '--retry-failed'])
        [start] = _of(_records(path), 'run_start')
        [end] = _of(_records(path), 'run_end')
        assert start['mode'] == 'retry_failed'
        assert (end['state'], end['error']) == ('failed', 'ZeroDivisionError')
        assert 0 <= end['seconds'] < 60

    def test_command_line_and_exit_codes(self, tmp_path, monkeypatch):
        import re
        import sys
        from click.testing import CliRunner
        import translate
        from tests.test_run_records import PROJECT
        log_file = os.path.join(os.environ[proclog.LOG_DIR_ENV], 'cli.log')
        path = _make_project(tmp_path)

        def tail():
            with open(log_file, encoding='utf-8') as f:
                return f.read().splitlines()[-3:]
        CliRunner().invoke(translate.cli, ['validate', path, '--mark-failed'])
        start = [l for l in tail() if 'start: cli validate' in l][-1]
        assert 'mark_failed=True' in start and f'project_dir={path}' in start
        CliRunner().invoke(translate.cli, ['validate', path])  # a False flag, then nothing
        CliRunner().invoke(translate.cli, ['stats', '--since', 'soon', '-p', path])
        lines = tail()
        assert any('usage error: ' in l and 'soon' in l for l in lines)
        assert lines[-1].endswith(lines[-1].split('end: ')[-1]) and 'end: exit 2 after' in lines[-1]
        assert float(re.search(r'after ([\d.]+) s', lines[-1]).group(1)) < 60
        for code, logged in ((None, 0), ('a message', 1), (3, 3)):
            def leave(*a, code=code, **k):
                sys.exit(code)
            monkeypatch.setattr(translate, '_find_project', leave)
            CliRunner().invoke(translate.cli, ['status', '-p', path])
            assert f'end: exit {logged} after' in tail()[-1]
        assert PROJECT


def test_outcome_defaults():
    from src.core.translator import TranslationError, unexpected_error_outcome
    e = TranslationError('x', code='error', prompt='', attempts=[])
    assert e.calls == 0 and e.split is False
    outcome = unexpected_error_outcome('m', ValueError('v'), '')
    assert (outcome.calls, outcome.split) == (0, False)
    assert (outcome.failure.calls, outcome.failure.split) == (0, False)


# --- survivors of pass B that pointed at gaps -----------------------------------------

def test_set_aside_retires_a_record_never_retired(tmp_path):
    from src.core import events as events_module
    run = events_module.RunEvents(str(tmp_path), 'cli')
    assert run.emit('run_start')
    events_module.set_aside(str(tmp_path))
    assert run.emit('run_end', state='completed') is False


def test_shape_checks_every_field():
    from src.core.stats import shape_problem
    base = {'v': 1, 'ts': 't', 'run': 'r', 'event': 'call'}
    assert shape_problem({**base, 'segment': None, 'issues': 7}) == 'issues: wrong type'
    assert shape_problem({**base, 'other': 1, 'seconds': 'slow'}) == 'seconds: wrong type'


def test_summary_sums_tokens_and_segments():
    from src.core.stats import summarize
    from tests.test_run_records import _call, _rec
    records = [_call('s1', 1, tokens_in=10), _call('s2', 1, tokens_in=20),
               _rec('segment', segment='s1', result='translated', calls=1),
               _rec('segment', segment='s2', result='translated', calls=1),
               {**_call(None, 1), 'kind': 'extract', 'backend': 'a'}]
    s = summarize(records)
    assert [g['work'] for g in s['models']] == ['translation', 'extraction']
    g = s['models'][0]
    assert (g['tokens_in'], g['tokens_out'], g['first_attempt']) == (30, 0, 2)


class TestSessionRecordFields:
    def _calls(self, tmp_path, replies, seg=None):
        from src.core.events import RunEvents
        from src.core.translator import TranslationEngine, translate_segment
        from src.llm.fake import FakeBackend
        from tests.test_run_records import _of, _records, _seg
        seg = seg or _seg()
        engine = TranslationEngine(backend=FakeBackend(replies), wait=lambda *a: False,
                                   events=RunEvents(str(tmp_path), 'cli', run_id='r'))
        translate_segment(engine, seg, [seg])
        return _of(_records(str(tmp_path)), 'call')

    def test_error_call_fields_and_warnings(self, tmp_path):
        from src.llm import TransientError
        calls = self._calls(tmp_path, [TransientError('x'), '<p k="1">Hello.</p>'])
        assert calls[0]['chars_in'] > 0 and 0 <= calls[0]['seconds'] < 60
        assert calls[1]['warnings'] == ['untranslated'] and 0 <= calls[1]['seconds'] < 60


def test_extraction_error_call_fields(tmp_path):
    from src.core.events import RunEvents
    from src.glossary.term_extractor import TermExtractor
    from src.llm import TransientError
    from src.llm.fake import FakeBackend
    from tests.test_run_records import _of, _records
    extractor = TermExtractor(model='m7', backend=FakeBackend([TransientError('x')]), effort='low',
                              timeout=45, events=RunEvents(str(tmp_path), 'extract'))
    extractor.extract_from_text('Nothing.')
    [c] = _of(_records(str(tmp_path)), 'call')
    assert (c['backend'], c['model'], c['effort'], c['timeout']) == ('fake', 'm7', 'low', 45)
    assert c['outcome'] == 'transient' and 0 <= c['seconds'] < 60


class TestCheckScriptPassB:
    def _check(self, projects, capsys):
        from tests.test_run_records import TestRunRecordsCheckScript
        code = TestRunRecordsCheckScript._check(None, projects)
        return code, capsys.readouterr().out

    def test_known_codes_and_classes_pass(self, tmp_path, capsys):
        from src.core.events import RunEvents
        path = _make_project(tmp_path, n=1)
        RunEvents(path, 'cli', run_id='20260101T000000Z-abcdef').emit(
            'call', kind='request', segment='c1_seg0001', backend='fake', model='m1', timeout=60,
            seconds=1.0, outcome='rejected', issues=['tag_count'], warnings=['length_ratio'],
            error='RateLimitError')
        code, out = self._check(tmp_path, capsys)
        assert code == 0, out

    def test_counts_checked_after_an_extraction_run(self, tmp_path, capsys):
        from src.core.events import RunEvents
        path = _make_project(tmp_path, n=1)
        RunEvents(path, 'extract', run_id='20260101T000000Z-aaaaaa').emit('run_end', state='completed')
        RunEvents(path, 'cli', run_id='20260101T000001Z-bbbbbb').emit(
            'run_end', state='completed', translated=1)
        for seg in ('x1', 'x2'):
            RunEvents(path, 'cli', run_id='20260101T000001Z-bbbbbb').emit(
                'segment', segment=seg, result='dropped')
        code, out = self._check(tmp_path, capsys)
        assert code == 1 and 'run_end translated=1, segment records 0' in out
        assert '2 records name a segment id not in project.json' in out

    def test_old_segment_ids(self, tmp_path, capsys):
        import json
        from src.core.events import RunEvents
        path = _make_project(tmp_path, n=1)
        with open(os.path.join(path, 'project.json'), encoding='utf-8') as f:
            data = json.load(f)
        data['segments'] = [{'id': 'old1', 'text': 'x'}]  # schema 1 names it `id`
        with open(os.path.join(path, 'project.json'), 'w', encoding='utf-8') as f:
            json.dump(data, f)
        RunEvents(path, 'cli', run_id='20260101T000000Z-abcdef').emit(
            'segment', segment='old1', result='dropped')
        code, out = self._check(tmp_path, capsys)
        assert 'segment id not in project.json' not in out
