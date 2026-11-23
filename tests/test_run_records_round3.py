"""Run records, review round 3: each test reproduces one finding (it failed
before the fix). All text is invented."""

import os
import threading

from src.core.events import RunEvents, events_path
from tests.test_run_records import PROJECT, _make_project, _of, _records, _wait


def _tm(monkeypatch):
    from src.web import tasks as tasks_module
    from src.web.tasks import TaskManager
    TaskManager._instance = None
    monkeypatch.setattr(tasks_module, 'COORDINATOR_TICK_SECONDS', 0.02)
    return TaskManager()


def _reset():
    from src.web.tasks import TaskManager
    TaskManager._instance = None


class GatedEvent(threading.Event):
    """The task's pause gate; its `nth` wait() from a worker first blocks
    until `release` is set: a worker between its stop check and the gate."""

    def __init__(self, nth):
        super().__init__()
        self.nth, self.waits = nth, 0
        self.reached, self.release = threading.Event(), threading.Event()

    def wait(self, timeout=None):
        if threading.current_thread().name.startswith('Thread') and not self.reached.is_set():
            self.waits += 1
            if self.waits == self.nth:
                self.reached.set()
                assert self.release.wait(20)
        return super().wait(timeout)


def test_pause_after_a_streak_stop_cannot_hang_the_run(tmp_path, monkeypatch):
    """Five unexpected failures stop the run; a worker is between its stop
    check and the pause gate; a user pause arrives then. Before: accepted,
    the gate closed, the worker waited for ever and the run never ended."""
    from dataclasses import field
    from src.llm.fake import FakeBackend
    from src.web import tasks as tasks_module
    from tests.conftest import use_fake_backend
    tm = _tm(monkeypatch)
    _make_project(tmp_path, n=7)
    gate = GatedEvent(nth=6)  # the wait before the sixth segment

    original = tasks_module.TranslationTask.__init__

    def init(self, *a, **k):
        original(self, *a, **k)
        gate.set()  # as the task's own gate starts (open)
        self.pause_event = gate
    monkeypatch.setattr(tasks_module.TranslationTask, '__init__', init)

    def boom(request):
        raise KeyError('invented')
    try:
        with use_fake_backend(FakeBackend(responder=boom)):
            task = tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
            assert gate.reached.wait(20)
            _wait(lambda: task.stop_event.is_set())  # the streak stopped the run
            paused = tm.pause_translation(PROJECT)
            gate.release.set()
            task._coordinator.join(10)
            alive = task._coordinator.is_alive()
            if alive:  # free the worker, so the test leaves no thread behind
                tm.stop_translation(PROJECT)
                task._coordinator.join(20)
    finally:
        _reset()
    assert paused is False and not alive
    assert task.state == 'failed'


def _run(tm, tmp_path, responder, n=1, workers=1):
    from src.llm.fake import FakeBackend
    from tests.conftest import use_fake_backend
    path = _make_project(tmp_path, n=n)
    with use_fake_backend(FakeBackend(responder=responder)):
        task = tm.start_translation(PROJECT, str(tmp_path), num_workers=workers)
        _wait(lambda: task.state in ('completed', 'stopped', 'failed') and not task.threads_alive())
    _wait(lambda: _of(_records(path), 'run_end'))
    return task, path


class TestStopWhileFinishing:
    def test_stop_after_the_last_result(self, tmp_path, monkeypatch):
        from tests.conftest import like
        tm = _tm(monkeypatch)
        done = tm._workers_done

        def workers_done(task):
            if done(task):
                tm.stop_translation(PROJECT)  # forced: right after the last result
                return True
            return False
        monkeypatch.setattr(tm, '_workers_done', workers_done)
        try:
            task, path = _run(tm, tmp_path, lambda r: like(r, '好'))
        finally:
            _reset()
        [end] = _of(_records(path), 'run_end')
        assert (task.state, end['state'], end['translated']) == ('completed', 'completed', 1)

    def test_stop_during_a_failure_drain(self, tmp_path, monkeypatch):
        from src.core import runcontrol
        from src.web import tasks as tasks_module
        from tests.conftest import like
        tm = _tm(monkeypatch)
        states = []
        flush = runcontrol.DebouncedSaver.flush

        def flush_and_stop(self):
            task = tm.tasks.get(PROJECT)
            if task is not None and task.finishing and not states:
                tm.stop_translation(PROJECT)  # forced: a stop in the drain
                states.append(task.state)
            return flush(self)
        monkeypatch.setattr(runcontrol.DebouncedSaver, 'flush', flush_and_stop)

        def explode(self, task, pm, res):
            raise ZeroDivisionError('invented')
        monkeypatch.setattr(tasks_module.TaskManager, '_apply_result', explode)
        try:
            task, path = _run(tm, tmp_path, lambda r: like(r, '好'))
        finally:
            _reset()
        assert states == ['running'] and task.state == 'failed'
        assert _of(_records(path), 'run_end')[0]['state'] == 'failed'


def test_a_failing_failure_handler_still_ends_the_run(tmp_path, monkeypatch):
    from src.web import tasks as tasks_module
    from tests.conftest import like
    tm = _tm(monkeypatch)

    def explode(self, task, pm, res):
        raise ZeroDivisionError('invented')
    real = tasks_module.TaskManager._fail_task

    def fail_task(self, task, error, saver):
        with task._lock:
            task.finishing = True
            task.stop_event.set()
            task.pause_event.set()
        raise OSError('the handler fails too')
    monkeypatch.setattr(tasks_module.TaskManager, '_apply_result', explode)
    monkeypatch.setattr(tasks_module.TaskManager, '_fail_task', fail_task)
    try:
        task, path = _run(tm, tmp_path, lambda r: like(r, '好'))
    finally:
        _reset()
    [end] = _of(_records(path), 'run_end')
    assert (task.state, end['state']) == ('failed', 'failed')
    assert end['error'] == 'ZeroDivisionError' and real


def test_pause_or_resume_while_finishing_answers_409(tmp_path, monkeypatch):
    from src.web.server import create_app
    from src.web.tasks import TaskManager
    from tests.conftest import like
    tm = _tm(monkeypatch)
    answers = []
    client_box = {}
    done = tm._workers_done

    def workers_done(task):
        if done(task):
            client = client_box['client']
            for action in ('pause', 'resume'):
                res = client.post(f'/api/projects/{PROJECT}/translate/pause', json={'action': action})
                answers.append((res.status_code, res.get_json()))
            return True
        return False
    monkeypatch.setattr(tm, '_workers_done', workers_done)
    try:
        app = create_app(projects_dir=str(tmp_path))
        monkeypatch.setattr('src.web.blueprints.translation._get_task_manager', lambda: tm)
        client_box['client'] = app.test_client()
        task, path = _run(tm, tmp_path, lambda r: like(r, '好'))
    finally:
        _reset()
    assert [code for code, _ in answers] == [409, 409]
    assert all('state' not in body and body['ending'] for _, body in answers)
    assert task.state == 'completed'


def test_page_keeps_completed_after_a_refused_pause(tmp_path):
    from src.web.server import create_app
    _make_project(tmp_path)
    page = create_app(projects_dir=str(tmp_path)).test_client() \
        .get(f'/project/{PROJECT}/translation').get_data(as_text=True)
    assert 'if (data.ending) {' in page
    assert "this.state = 'completed';\n                this.error = null;" in page


class TestExtractionEnds:
    def _cli(self, tmp_path, monkeypatch, patch):
        import json
        from click.testing import CliRunner
        import translate
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        path = _make_project(tmp_path, n=1)
        patch()
        reply = json.dumps([{'term': 'Quillfeather', 'translation': '羽筆', 'category': 'term'}])
        with use_fake_backend(FakeBackend([reply])):
            CliRunner().invoke(translate.cli, ['glossary', 'extract', '-p', path])
        return path, _of(_records(path), 'run_end')

    def test_ctrl_c_during_the_save(self, tmp_path, monkeypatch):
        from src.glossary.glossary_manager import GlossaryManager

        def interrupt(self, *a, **k):
            raise KeyboardInterrupt()
        _, [end] = self._cli(tmp_path, monkeypatch,
                             lambda: monkeypatch.setattr(GlossaryManager, 'save', interrupt))
        assert (end['state'], end['error'], end['terms']) == ('interrupted', 'KeyboardInterrupt', 1)

    def test_closed_pipe_after_the_save_keeps_added(self, tmp_path, monkeypatch):
        import click
        import translate
        echo = click.echo

        def closed(message=None, *a, **k):
            if message and str(message).startswith('已儲存到'):
                raise BrokenPipeError()
            return echo(message, *a, **k)
        path, [end] = self._cli(tmp_path, monkeypatch,
                                lambda: monkeypatch.setattr(translate.click, 'echo', closed))
        assert (end['added'], end['terms']) == (1, 1) and end['error'] == 'BrokenPipeError'
        assert os.path.exists(os.path.join(path, 'glossary.json'))

    def test_web_save_failure_keeps_terms(self, tmp_path, monkeypatch):
        import json
        from src.glossary.glossary_manager import GlossaryManager
        from src.llm.fake import FakeBackend
        from src.web.server import create_app
        from tests.conftest import use_fake_backend
        path = _make_project(tmp_path, n=1)

        def explode(self, *a, **k):
            raise OSError('disk full')
        monkeypatch.setattr(GlossaryManager, 'save', explode)
        client = create_app(projects_dir=str(tmp_path)).test_client()
        reply = json.dumps([{'term': 'Quillfeather', 'translation': '羽筆', 'category': 'term'}])
        with use_fake_backend(FakeBackend([reply])):
            assert client.post(f'/api/projects/{PROJECT}/glossary/extract').status_code == 200
            _wait(lambda: _of(_records(path), 'run_end'))
        [end] = _of(_records(path), 'run_end')
        assert (end['state'], end['error'], end['terms']) == ('failed', 'OSError', 1)
        assert 'added' not in end


def test_run_started_before_since():
    from src.core.stats import START_UNKNOWN, summarize, text_report
    from tests.test_run_records import _call, _rec
    records = [_rec('run_start', ts='2026-01-01T09:00:00.000Z'),
               _call('s1', 1, ts='2026-01-01T09:30:00.000Z'),
               _call('s2', 1, ts='2026-01-01T11:00:00.000Z'),
               _rec('run_end', ts='2026-01-01T11:00:05.000Z', state='completed', seconds=7205.0),
               _rec('run_start', run='r2', ts='2026-01-01T11:10:00.000Z'),
               _call('s3', 1, run='r2', ts='2026-01-01T11:20:00.000Z')]
    s = summarize(records, since='2026-01-01T10:00:00.000Z')
    first, second = s['runs']
    assert (first['start'], first['duration']) == (None, 7205.0)  # its own measure, start unknown
    assert (second['start'], second['duration']) == ('2026-01-01T11:10:00.000Z', 600.0)
    unfinished = summarize(records[:3], since='2026-01-01T10:00:00.000Z')['runs'][0]
    assert (unfinished['start'], unfinished['duration']) == (None, None)
    text = '\n'.join(text_report({**s, 'exists': True}))
    assert f'{START_UNKNOWN} → ' in text


def test_page_shows_unknown_start(tmp_path):
    from src.web.server import create_app
    _make_project(tmp_path)
    page = create_app(projects_dir=str(tmp_path)).test_client() \
        .get(f'/project/{PROJECT}/stats').get_data(as_text=True)
    assert "r.start ? time(r.start) : '（開始不在所選紀錄中）'" in page


def test_version_leaves_the_index_alone(tmp_path, monkeypatch):
    """In a scratch repository whose index is stat-dirty (a file touched):
    the version commands do not rewrite .git/index (`describe --dirty`
    did, with or without GIT_OPTIONAL_LOCKS), and a content change still
    gives -dirty."""
    import subprocess
    import time
    from src.core import proclog
    env = {'GIT_AUTHOR_NAME': 'n', 'GIT_AUTHOR_EMAIL': 'n@example.invalid',
           'GIT_COMMITTER_NAME': 'n', 'GIT_COMMITTER_EMAIL': 'n@example.invalid',
           'PATH': os.environ['PATH'], 'HOME': str(tmp_path)}

    def git(*args):
        subprocess.run(['git', '-C', str(tmp_path), *args], check=True, env=env,
                       capture_output=True)
    git('init', '-q')
    (tmp_path / 'f').write_text('a')
    git('add', 'f')
    git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'x')
    index = tmp_path / '.git' / 'index'

    def version():
        monkeypatch.setattr(proclog, '_REPO', str(tmp_path))
        monkeypatch.setattr(proclog, '_commit', None)
        return proclog.program_version()
    time.sleep(1.1)  # a later mtime than the index records: the index is stale
    os.utime(tmp_path / 'f')
    before = (index.stat().st_mtime_ns, index.read_bytes())
    assert not version().endswith('-dirty)')
    assert (index.stat().st_mtime_ns, index.read_bytes()) == before
    (tmp_path / 'f').write_text('b')
    assert version().endswith('-dirty)')
    assert (index.stat().st_mtime_ns, index.read_bytes()) == before


def _replacement_now(project_dir):
    """A replacement of the project from another thread, as the web create
    does it (set_aside under the project mutex). Returns once it is done,
    or once it is blocked because this thread holds the mutex."""
    from src.core import events as events_module
    from src.core.project import project_mutex
    mutex = project_mutex(project_dir)
    done, blocked = threading.Event(), threading.Event()

    def replace():
        if not mutex.acquire(blocking=False):
            blocked.set()
            mutex.acquire()
        try:
            events_module.set_aside(project_dir)
        finally:
            mutex.release()
            done.set()
    threading.Thread(target=replace, daemon=True).start()
    _wait(lambda: done.is_set() or blocked.is_set())
    return done


def test_retranslation_recorder_from_the_snapshot(tmp_path, monkeypatch):
    """A replacement between the snapshot and the call: the old work must
    not get the new book's record."""
    from src.llm.fake import FakeBackend
    from src.web.blueprints import review as review_module
    from src.web.server import create_app
    from tests.conftest import like, use_fake_backend
    path = _make_project(tmp_path, n=1)
    RunEvents(path, 'cli').emit('run_start')  # the old book's record exists
    glossary = review_module._glossary
    replaced = []

    def replace_then_glossary(project_dir):
        # right after the snapshot's transaction, before the model call
        replaced.append(_replacement_now(path))
        return glossary(project_dir)
    monkeypatch.setattr(review_module, '_glossary', replace_then_glossary)
    client = create_app(projects_dir=str(tmp_path)).test_client()
    with use_fake_backend(FakeBackend(responder=lambda r: like(r, '好'))):
        client.post(f'/api/projects/{PROJECT}/segments/c1_seg0001/retranslate')
    assert replaced[0].wait(10)
    assert not os.path.exists(events_path(path))  # nothing in the new book's record


def test_extraction_recorder_from_the_snapshot(tmp_path, monkeypatch):
    import json
    from src.core.project import ProjectManager
    from src.llm.fake import FakeBackend
    from src.web.blueprints import glossary as glossary_module
    from src.web.server import create_app
    from tests.conftest import use_fake_backend
    path = _make_project(tmp_path, n=1)
    RunEvents(path, 'cli').emit('run_start')
    settings = ProjectManager.call_settings
    replaced = []

    def settings_then_replace(self):
        value = settings(self)
        replaced.append(_replacement_now(path))
        return value
    monkeypatch.setattr(ProjectManager, 'call_settings', settings_then_replace)
    client = create_app(projects_dir=str(tmp_path)).test_client()
    reply = json.dumps([{'term': 'Quill', 'translation': '羽', 'category': 'term'}])
    with use_fake_backend(FakeBackend([reply])):
        assert client.post(f'/api/projects/{PROJECT}/glossary/extract').status_code == 200
        _wait(lambda: not glossary_module._extraction_tasks[PROJECT]['running'])
    assert replaced[0].wait(10)
    assert not os.path.exists(events_path(path))
