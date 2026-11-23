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
        assert c['chars_out'] > 0

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
