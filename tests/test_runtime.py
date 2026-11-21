"""Phase 7 part A: runtime control (cancel, run-level errors, debounced
saves), the second backend (CopilotCLI) and the backend contract."""

import json
import os
import sys
import threading
import time

import pytest
from click.testing import CliRunner

import src.llm as llm
import translate
from src.core.project import ProjectManager, SegmentStatus
from src.core.runcontrol import (
    RATE_LIMIT_WAIT_SECONDS, SAVE_EVERY_RESULTS, SAVE_INTERVAL_SECONDS, DebouncedSaver,
    resume_delay,
)
from src.core.segmenter import Segment, SegmentType
from src.core.translator import (
    TRANSIENT_BACKOFF_SECONDS, TranslationEngine, translate_segment, wait_or_cancel,
)
from src.llm import (
    AuthError, BackendUnavailableError, CancelledError, LLMError, LLMRequest, LLMResponse,
    Message, RateLimitError, RefusalError, TransientError, Usage,
)
from src.llm.claude_cli import ClaudeCLI
from src.llm.cli import CLIBackend, ProcessResult
from src.llm.cli import run_process as real_run_process
from src.llm.copilot_cli import CopilotCLI
from src.llm.fake import FakeBackend
from src.web import tasks as tasks_module
from src.web.tasks import TaskManager
from tests.conftest import like, use_fake_backend

PROJECT = 'p'


# --- helpers ---------------------------------------------------------------------

def _make_project(projects_dir, n=3, name=PROJECT):
    """An invented project of n one-paragraph segments, `<p>Line N.</p>`."""
    segments = [Segment(id=f'c1_seg{i:04d}', html=f'<p>Line {i}.</p>', text=f'Line {i}.',
                        word_count=2, segment_type=SegmentType.TEXT)
                for i in range(1, n + 1)]
    path = os.path.join(str(projects_dir), name)
    ProjectManager(path).create('/nowhere/book.epub', 'Invented', 'Nobody', segments)
    return path


def _statuses(path):
    pm = ProjectManager(path)
    pm.load()
    return [s.status for s in pm.data.segments]


def _wait(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'timed out waiting'
        time.sleep(0.01)


def _gone(pid, wait=10):
    """True when pid is no process within `wait` s (killed and reaped)."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def fresh_tm(monkeypatch):
    TaskManager._instance = None
    monkeypatch.setattr(tasks_module, 'COORDINATOR_TICK_SECONDS', 0.02)
    tm = TaskManager()
    yield tm
    for task in list(tm.tasks.values()):
        tm.stop_translation(task.project_name)
        if task._coordinator is not None:
            task._coordinator.join(timeout=20)
    TaskManager._instance = None


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(TaskManager, 'clock', staticmethod(c))
    monkeypatch.setattr(translate, '_clock', c)
    return c


class SleeperBackend(CLIBackend):
    """Runs a real short-lived child: a Python interpreter that writes its
    PID and sleeps (stands in for a model call in flight)."""
    name = 'sleeper'
    default_model = 'm'
    auth_hint = ''
    capabilities = ClaudeCLI.capabilities

    def __init__(self, pid_dir, seconds=60):
        self.pid_dir = pid_dir
        self.seconds = seconds
        self.started = 0
        self.lock = threading.Lock()

    def build_command(self, prompt, request):
        with self.lock:
            self.started += 1
            pid_file = os.path.join(self.pid_dir, f'{self.started}.pid')
        return [sys.executable, '-c',
                f'import os,time; open({pid_file!r},"w").write(str(os.getpid())); '
                f'time.sleep({self.seconds})']

    def parse_output(self, result, request):
        return LLMResponse(text=result.stdout, stop_reason='complete', usage=None,
                           model=request.model)

    def classify_error(self, result, prompt=''):
        return None

    def list_models(self):
        return []

    def pids(self):
        out = []
        for name in sorted(os.listdir(self.pid_dir)):
            with open(os.path.join(self.pid_dir, name)) as f:
                text = f.read()
            if text:
                out.append(int(text))
        return out


# --- 1. stop and pause end the call in flight ------------------------------------

class TestStopKillsCallInFlight:

    @pytest.fixture
    def sleeper(self, tmp_path, monkeypatch):
        monkeypatch.setattr('src.llm.cli.run_process', real_run_process)  # a real child
        pid_dir = tmp_path / 'pids'
        pid_dir.mkdir()
        return SleeperBackend(str(pid_dir))

    def test_stop(self, tmp_path, fresh_tm, sleeper):
        path = _make_project(tmp_path / 'projects', n=2)
        with use_fake_backend(sleeper):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            _wait(lambda: sleeper.pids())
            pid = sleeper.pids()[0]
            fresh_tm.stop_translation(PROJECT)
            task._coordinator.join(timeout=20)
        assert not task.threads_alive()  # returned within the bound
        assert _gone(pid)
        assert task.state == 'stopped'
        assert sleeper.started == 1
        # the segment in flight is pending again, nothing stored
        assert _statuses(path) == [SegmentStatus.PENDING, SegmentStatus.PENDING]
        assert not os.path.exists(os.path.join(path, 'log', 'failed'))

    def test_pause_kills_and_resume_goes_on(self, tmp_path, fresh_tm, sleeper):
        path = _make_project(tmp_path / 'projects', n=1)
        with use_fake_backend(sleeper):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            _wait(lambda: sleeper.pids())
            pid = sleeper.pids()[0]
            assert fresh_tm.pause_translation(PROJECT)
            assert _gone(pid)
            assert task.state == 'paused' and task.completed == 0
            assert _statuses(path) == [SegmentStatus.PENDING]
            assert task.segment_queue.qsize() == 1  # back in the queue
            # resumed: the segment is sent again (a new child)
            assert fresh_tm.resume_translation(PROJECT)
            _wait(lambda: len(sleeper.pids()) == 2)
            assert not task.cancel_event.is_set()
            fresh_tm.stop_translation(PROJECT)
            task._coordinator.join(timeout=20)
        assert all(_gone(p) for p in sleeper.pids())
        assert _statuses(path) == [SegmentStatus.PENDING]

    def test_cli_interrupt_in_parallel_run(self, tmp_path, sleeper, monkeypatch):
        """Ctrl-C (here: raised while the first result is applied) kills
        the other worker's call in flight; its segment stays pending."""
        path = _make_project(tmp_path, n=2)
        answered = FakeBackend(responder=lambda r: like(r, '譯'))

        class Mixed:
            name, default_model, auth_hint = 'mixed', 'm', ''
            capabilities = answered.capabilities

            def list_models(self):
                return []

            def complete(self, request, cancel):
                if '>Line 1.<' in request.messages[0].content:
                    _wait(lambda: sleeper.pids())  # answer once the other is in flight
                    return answered.complete(request, cancel)
                return sleeper.complete(request, cancel)

        real_apply = ProjectManager.apply_outcome

        def interrupt(self, *a, **kw):
            real_apply(self, *a, **kw)
            raise KeyboardInterrupt

        monkeypatch.setattr(ProjectManager, 'apply_outcome', interrupt)
        with use_fake_backend(Mixed()):
            result = CliRunner().invoke(translate.cli, ['start', '--project', path,
                                                        '--workers', '2'])
        assert result.exit_code == 1 and 'Aborted!' in result.output  # click's Ctrl-C
        assert '正在停止進行中的段落' in result.output
        assert _gone(sleeper.pids()[0])
        # the received result was saved; the one in flight stays pending
        assert _statuses(path) == [SegmentStatus.TRANSLATED, SegmentStatus.PENDING]

    def test_run_process_kills_on_cancel_set_later(self, tmp_path, monkeypatch, sleeper):
        cancel = threading.Event()
        errors = []

        def call():
            try:
                sleeper.complete(LLMRequest('', [Message('user', 'x')], 'm', 60), cancel)
            except CancelledError as e:
                errors.append(e)

        t = threading.Thread(target=call)
        t.start()
        _wait(lambda: sleeper.pids())
        cancel.set()
        t.join(timeout=20)
        assert not t.is_alive() and len(errors) == 1
        assert _gone(sleeper.pids()[0])


# --- 2. run-level errors -----------------------------------------------------------

class TestResumeDelay:

    def test_values(self):
        assert resume_delay(RateLimitError('x', retry_after=12)) == 12.0
        assert resume_delay(RateLimitError('x', retry_after=None)) == RATE_LIMIT_WAIT_SECONDS
        assert resume_delay(RateLimitError('x', retry_after=0)) == RATE_LIMIT_WAIT_SECONDS
        assert resume_delay(RateLimitError('x', retry_after=-3)) == RATE_LIMIT_WAIT_SECONDS
        assert resume_delay(AuthError('x')) is None
        assert resume_delay(BackendUnavailableError('x')) is None
        assert RATE_LIMIT_WAIT_SECONDS == 300
        assert isinstance(resume_delay(RateLimitError('x', retry_after=12)), float)
        assert isinstance(resume_delay(RateLimitError('x')), float)


def _limited_once(error):
    """Responder: the first call raises `error`, every other one answers."""
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            raise error
        return like(request, '譯')
    respond.calls = calls
    return respond


class TestWebRateLimitResumes:

    @pytest.mark.parametrize('retry_after, wait', [(30, 30), (None, RATE_LIMIT_WAIT_SECONDS)])
    def test_resumes_without_user(self, tmp_path, fresh_tm, clock, retry_after, wait):
        path = _make_project(tmp_path / 'projects', n=2)
        respond = _limited_once(RateLimitError('limit reached', retry_after=retry_after))
        with use_fake_backend(FakeBackend(responder=respond)):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            _wait(lambda: task.state == 'paused')
            assert task.resume_at == clock.now + wait
            event = next(e for e in task.progress.get_all() if e['event'] == 'rate_limit')
            assert event['data']['resume_in'] == wait
            clock.now += wait - 1
            time.sleep(0.1)  # several ticks: not yet
            assert task.state == 'paused'
            clock.now += 1
            task._coordinator.join(timeout=20)
        assert task.state == 'completed'
        assert [e['event'] for e in task.progress.get_all()].count('resumed') == 1
        assert _statuses(path) == [SegmentStatus.TRANSLATED, SegmentStatus.TRANSLATED]
        assert task.resume_at is None and task.error is None

    @pytest.mark.parametrize('error', [AuthError('login expired'),
                                       BackendUnavailableError('no program')])
    def test_auth_and_unavailable_wait_for_user(self, tmp_path, fresh_tm, clock, error):
        _make_project(tmp_path / 'projects', n=1)
        with use_fake_backend(FakeBackend(responder=_limited_once(error))):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            _wait(lambda: task.state == 'paused')
            assert task.resume_at is None
            clock.now += 10 ** 6
            time.sleep(0.1)
            assert task.state == 'paused'
            assert fresh_tm.resume_translation(PROJECT)  # the user
            task._coordinator.join(timeout=20)
        assert task.state == 'completed'

    def test_user_pause_is_not_resumed(self, tmp_path, fresh_tm, clock):
        _make_project(tmp_path / 'projects', n=1)
        task = tasks_module.TranslationTask('t', PROJECT, str(tmp_path / 'projects'),
                                            state='paused')
        task.resume_at = clock.now  # left over: a user pause clears it
        fresh_tm.tasks[PROJECT] = task
        task.state = 'running'
        assert fresh_tm.pause_translation(PROJECT)
        assert task.resume_at is None and task.cancel_event.is_set()


class TestCliRateLimitResumes:

    @pytest.mark.parametrize('workers', ['1', '3'])
    @pytest.mark.parametrize('retry_after, wait', [(7, 7), (None, RATE_LIMIT_WAIT_SECONDS)])
    def test_resumes_without_user(self, tmp_path, clock, monkeypatch, workers, retry_after, wait):
        path = _make_project(tmp_path, n=3)
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            clock.now += seconds

        monkeypatch.setattr(translate, '_sleep', sleep)

        def advance():  # parallel: the main loop's clock moves on by itself
            clock.now += 1000
            return clock.now

        if workers != '1':
            monkeypatch.setattr(translate, '_clock', advance)
        respond = _limited_once(RateLimitError('limit reached', retry_after=retry_after))
        with use_fake_backend(FakeBackend(responder=respond)):
            result = CliRunner().invoke(translate.cli, ['start', '--project', path,
                                                        '--workers', workers])
        assert result.exit_code == 0, result.output
        if workers == '1':
            assert ('  ✗ limit reached\n'
                    f'  使用量已達上限：{wait:.0f} 秒後自動繼續（Ctrl-C 停止）\n') in result.output
        else:  # announced by the main loop, which has moved its clock on
            assert '  ✗ limit reached\n  使用量已達上限：' in result.output
        assert _statuses(path) == [SegmentStatus.TRANSLATED] * 3
        assert len(respond.calls) == 4  # the limited call is made again
        if workers == '1':
            assert slept == [wait]
        else:
            assert slept == [] and '繼續翻譯' in result.output

    @pytest.mark.parametrize('workers', ['1', '3'])
    def test_auth_stops(self, tmp_path, workers, monkeypatch):
        monkeypatch.setattr(translate, '_sleep', lambda s: pytest.fail('no wait'))
        path = _make_project(tmp_path, n=2)
        with use_fake_backend(FakeBackend(responder=_limited_once(AuthError('expired')))):
            result = CliRunner().invoke(translate.cli, ['start', '--project', path,
                                                        '--workers', workers])
        assert result.exit_code == 1
        assert 'expired' in result.output and '翻譯中斷' in result.output
        assert SegmentStatus.PENDING in _statuses(path)


class TestTransientBackoff:

    def _seg(self):
        return Segment(id='c1_seg0001', html='<p>Line.</p>', text='Line.', word_count=1,
                       segment_type=SegmentType.TEXT)

    def _translate(self, replies, wait=None):
        fake = FakeBackend(replies)
        engine = TranslationEngine(model='m', backend=fake, wait=wait)
        from src.core.project import SegmentState
        seg = SegmentState('c1_seg0001', 'c1', '<p>Line.</p>', 'Line.', 1, 'text')
        return fake, translate_segment(engine, seg, [seg])

    def test_transient_is_retried_after_a_wait(self, backoff_waits):
        fake, outcome = self._translate([TransientError('exit 1'), '<p k="1">譯</p>'])
        assert outcome.status == SegmentStatus.TRANSLATED
        assert len(fake.requests) == 2
        assert backoff_waits == [5]

    def test_backoff_grows_and_budget_is_shared(self, backoff_waits):
        fake, outcome = self._translate([TransientError('t')] * 4)
        assert outcome.status == SegmentStatus.FAILED
        assert outcome.issues[0].code == 'transient'
        assert len(fake.requests) == 4  # MAX_CALLS
        assert backoff_waits == [5, 15, 45]  # none after the last call
        assert TRANSIENT_BACKOFF_SECONDS == (5, 15, 45)

    def test_last_backoff_repeats(self, backoff_waits, monkeypatch):
        monkeypatch.setattr('src.core.translator.TRANSIENT_BACKOFF_SECONDS', (1, 2))
        fake = FakeBackend([TransientError('t')] * 4)
        engine = TranslationEngine(model='m', backend=fake)
        with pytest.raises(Exception):
            engine.translate('Line.')
        assert backoff_waits == [1, 2, 2]

    @pytest.mark.parametrize('error, code', [(LLMError('bad request'), 'error'),
                                             (RefusalError('no'), 'refusal')])
    def test_permanent_is_not_retried(self, backoff_waits, error, code):
        fake, outcome = self._translate([error])
        assert outcome.status == SegmentStatus.FAILED and outcome.issues[0].code == code
        assert len(fake.requests) == 1
        assert backoff_waits == []

    def test_injected_wait_and_cancel_during_backoff(self):
        waits = []

        def wait(seconds, cancel):
            waits.append(seconds)
            return True  # cancelled meanwhile

        fake = FakeBackend([TransientError('t')])
        engine = TranslationEngine(model='m', backend=fake, wait=wait)
        with pytest.raises(CancelledError):
            engine.translate('Line.')
        assert waits == [5] and len(fake.requests) == 1

    def test_default_wait_uses_the_cancel_event(self):
        cancel = threading.Event()
        cancel.set()
        assert wait_or_cancel(30, cancel) is True  # returns at once when set
        assert wait_or_cancel(0, threading.Event()) is False


# --- 3. debounced saves ------------------------------------------------------------

class _CountingPM:
    def __init__(self, fail=False):
        self.saves = 0
        self.fail = fail
        self.mutex = threading.RLock()

    def save(self):
        if self.fail:
            raise OSError('disk full')
        self.saves += 1


class TestDebouncedSaver:

    def test_count_window(self):
        pm, clock = _CountingPM(), FakeClock()
        saver = DebouncedSaver(pm, clock=clock)
        for i in range(SAVE_EVERY_RESULTS - 1):
            saver.add(i)
            assert not saver.maybe_save()
        saver.add('last')
        assert saver.maybe_save() and pm.saves == 1 and saver.pending == []
        assert SAVE_EVERY_RESULTS == 20 and SAVE_INTERVAL_SECONDS == 10

    def test_time_window(self):
        pm, clock = _CountingPM(), FakeClock()
        saver = DebouncedSaver(pm, clock=clock)
        assert not saver.maybe_save()  # nothing waiting: never a save
        saver.add(1)
        clock.now += SAVE_INTERVAL_SECONDS - 0.5
        assert not saver.maybe_save()
        clock.now += 0.5
        assert saver.maybe_save() and pm.saves == 1
        saver.add(2)
        assert not saver.maybe_save()  # the window starts at the last save
        clock.now += SAVE_INTERVAL_SECONDS
        assert saver.maybe_save() and pm.saves == 2

    def test_flush_and_after_save(self):
        pm, seen = _CountingPM(), []
        saver = DebouncedSaver(pm, after_save=seen.append, clock=FakeClock())
        assert not saver.flush() and pm.saves == 0
        saver.add('a')
        saver.add('b')
        assert saver.flush() and seen == [['a', 'b']] and saver.saves == 1

    def test_failed_save_keeps_the_items(self):
        pm, seen = _CountingPM(fail=True), []
        saver = DebouncedSaver(pm, after_save=seen.append, clock=FakeClock())
        saver.add('a')
        with pytest.raises(OSError):
            saver.flush()
        assert saver.pending == ['a'] and seen == [] and saver.saves == 0


def _count_saves(monkeypatch):
    writes = []
    real = ProjectManager.save

    def save(self):
        writes.append(1)
        real(self)

    monkeypatch.setattr(ProjectManager, 'save', save)
    return writes


class TestHundredResults:
    """100 results: far fewer than 100 writes of project.json, every result on disk."""

    def test_web(self, tmp_path, fresh_tm, clock, monkeypatch):
        path = _make_project(tmp_path / 'projects', n=100)
        writes = _count_saves(monkeypatch)
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'), num_workers=4)
            task._coordinator.join(timeout=60)
        assert task.state == 'completed'
        assert len(writes) <= 100 // SAVE_EVERY_RESULTS + 1
        assert _statuses(path) == [SegmentStatus.TRANSLATED] * 100

    @pytest.mark.parametrize('workers', ['1', '4'])
    def test_cli(self, tmp_path, clock, monkeypatch, workers):
        path = _make_project(tmp_path, n=100)
        writes = _count_saves(monkeypatch)
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))):
            result = CliRunner().invoke(translate.cli, ['start', '--project', path,
                                                        '--workers', workers])
        assert result.exit_code == 0, result.output
        assert len(writes) <= 100 // SAVE_EVERY_RESULTS + 1
        assert _statuses(path) == [SegmentStatus.TRANSLATED] * 100

    def test_web_pause_saves_the_window(self, tmp_path, fresh_tm, clock):
        """A result waiting in the window is on disk once the run pauses."""
        path = _make_project(tmp_path / 'projects', n=2)
        gate = threading.Event()

        def respond(request):
            if '>Line 2.<' in request.messages[0].content:
                gate.wait(20)
                raise AuthError('expired')  # pauses the run
            return like(request, '譯')

        with use_fake_backend(FakeBackend(responder=respond)):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            _wait(lambda: task.completed == 1)
            time.sleep(0.1)  # ticks: the window (1 of 20, no time passed) is not saved
            assert _statuses(path) == [SegmentStatus.PENDING] * 2
            gate.set()
            _wait(lambda: task.state == 'paused')
            _wait(lambda: _statuses(path)[0] == SegmentStatus.TRANSLATED)

    def test_failure_log_waits_for_the_save_and_an_edit_wins(self, tmp_path, fresh_tm, clock):
        """A failure's diagnostics are written when it is saved, and not
        for a segment edited before that save."""
        path = _make_project(tmp_path / 'projects', n=2)

        def respond(request):
            raise LLMError('bad request')  # both fail

        with use_fake_backend(FakeBackend(responder=respond)):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            _wait(lambda: not task.threads_alive() or len(task.progress.get_all()) >= 2)
            task._coordinator.join(timeout=20)
        failed = os.path.join(path, 'log', 'failed')
        assert len(os.listdir(failed)) == 2

    def test_edited_before_save_gets_no_failure_log(self, tmp_path):
        from src.core.translator import TranslationOutcome, TranslationError
        from src.core.project import Issue
        path = _make_project(tmp_path, n=1)
        pm = ProjectManager(path)
        pm.load()
        issue = Issue('backend', 'error', 'x', True)
        outcome = TranslationOutcome(None, SegmentStatus.FAILED, [issue], 'm', None,
                                     failure=TranslationError('x', code='error', prompt='P',
                                                              attempts=[]))
        written = []

        def after_save(items):
            for segment_id, out, revision in items:
                if pm._find(segment_id).revision == revision:
                    written.append(segment_id)

        saver = DebouncedSaver(pm, after_save, clock=FakeClock())
        pm.apply_outcome('c1_seg0001', outcome, revision=0)
        saver.add(('c1_seg0001', outcome, 0))
        pm.update_translation('c1_seg0001', '<p>人工</p>')  # the person's edit
        saver.flush()
        assert written == []


class TestWebFlushBeforeLockRelease:

    def test_save_failure_reports_unsaved(self, tmp_path, fresh_tm, clock, monkeypatch):
        _make_project(tmp_path / 'projects', n=2)

        def broken(self):
            raise OSError('disk full')

        monkeypatch.setattr(ProjectManager, 'save', broken)
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            task._coordinator.join(timeout=20)
        assert task.state == 'failed'
        assert 'disk full' in task.error
        assert '2 個段落的結果未儲存：c1_seg0001, c1_seg0002' in task.error
        event = task.progress.get_all()[-1]
        assert event['event'] == 'error' and event['data']['unsaved'] == ['c1_seg0001',
                                                                            'c1_seg0002']


# --- 4. the backend contract (every backend, fake process) ------------------------

def _copilot_ok(text, tokens=(10, 3)):
    events = [
        {'type': 'session.start', 'data': {'context': {'cwd': '/tmp'}}},
        {'type': 'assistant.message', 'data': {'content': 'draft'}},
        {'type': 'assistant.message', 'data': {'content': text, 'model': 'm'}},
        {'type': 'session.usage_checkpoint', 'data': {'accountingSnapshot': {'modelMetrics': {
            'm': {'usage': {'inputTokens': tokens[0], 'outputTokens': tokens[1]}}}}}},
        {'type': 'result', 'exitCode': 0},
    ]
    return ProcessResult(0, '\n'.join(json.dumps(e) for e in events) + '\n', '')


def _copilot_error(message, error_type='query', code=1):
    events = [{'type': 'session.start', 'data': {}},
              {'type': 'session.error', 'data': {'errorType': error_type, 'message': message}},
              {'type': 'result', 'exitCode': code}]
    return ProcessResult(code, '\n'.join(json.dumps(e) for e in events), '')


# Per backend: the program, a successful run answering 'ANSWER', and runs
# that stand for each failure kind, in the backend's own format
CONTRACT = {
    'claude-cli': {
        'cls': ClaudeCLI, 'program': 'claude',
        'ok': ProcessResult(0, 'ANSWER', ''),
        'long_ok_quoting_limit': ProcessResult(0, 'ANSWER ' + 'x' * 400 + " You've hit your limit", ''),
        'rate_limit': ProcessResult(1, '', "You've hit your limit · resets 8pm"),
        'auth': ProcessResult(1, '', 'OAuth token has expired'),
        'transient': ProcessResult(1, '', 'connection reset'),
    },
    'copilot-cli': {
        'cls': CopilotCLI, 'program': 'copilot',
        'ok': _copilot_ok('ANSWER'),
        'long_ok_quoting_limit': _copilot_ok("ANSWER You've hit your rate limit."),
        'rate_limit': _copilot_error("You've hit the rate limit for this model."),
        'auth': _copilot_error('Not authenticated: run copilot login', 'authentication'),
        'transient': _copilot_error('Execution failed: socket hang up'),
    },
}


@pytest.fixture(params=sorted(CONTRACT))
def contract(request, monkeypatch):
    case = CONTRACT[request.param]
    calls = []

    def run(result):
        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return result
        monkeypatch.setattr('src.llm.cli.run_process', fake_run)

    return case, case['cls'](), run, calls


def _req(prompt='PROMPT TEXT', model='some-model', system=''):
    return LLMRequest(system=system, messages=[Message('user', prompt)], model=model, timeout=9)


class TestBackendContract:
    """What every backend does, checked on each with a fake process."""

    def test_registered_with_models(self, contract):
        case, backend, _, _ = contract
        assert llm.BACKENDS[backend.name] is case['cls']
        aliases = [m.alias for m in backend.list_models()]
        assert aliases and backend.default_model in aliases
        assert backend.auth_hint

    def test_success(self, contract):
        case, backend, run, calls = contract
        run(case['ok'])
        cancel = threading.Event()
        response = backend.complete(_req(), cancel)
        assert response.text == 'ANSWER' and response.model == 'some-model'
        assert response.stop_reason == 'complete'
        assert (response.usage is not None) == backend.capabilities.reports_usage
        cmd, kwargs = calls[0]
        assert cmd[0] == case['program']
        assert 'PROMPT TEXT' in cmd and cmd[cmd.index('--model') + 1] == 'some-model'
        assert kwargs['cancel'] is cancel and kwargs['timeout'] == 9
        assert kwargs['cwd'] and isinstance(kwargs['env'], dict)

    def test_system_prompt_flattened(self, contract):
        case, backend, run, calls = contract
        run(case['ok'])
        backend.complete(_req(system='RULES'), threading.Event())
        assert 'RULES\n\nPROMPT TEXT' in calls[0][0]

    def test_reply_quoting_a_limit_is_a_reply(self, contract):
        case, backend, run, _ = contract
        run(case['long_ok_quoting_limit'])
        assert backend.complete(_req(), threading.Event()).text.startswith('ANSWER')

    @pytest.mark.parametrize('kind, error', [('rate_limit', RateLimitError), ('auth', AuthError),
                                             ('transient', TransientError)])
    def test_failures_classified(self, contract, kind, error):
        case, backend, run, _ = contract
        run(case[kind])
        with pytest.raises(LLMError) as info:
            backend.complete(_req(), threading.Event())
        assert type(info.value) is error
        assert info.value.raw  # the output, for the diagnostics

    def test_timeout_and_missing_program(self, contract, monkeypatch):
        import subprocess
        _, backend, _, _ = contract

        def timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 9)

        monkeypatch.setattr('src.llm.cli.run_process', timeout)
        with pytest.raises(TransientError):
            backend.complete(_req(), threading.Event())

        def missing(cmd, **kwargs):
            raise FileNotFoundError(cmd[0])

        monkeypatch.setattr('src.llm.cli.run_process', missing)
        with pytest.raises(BackendUnavailableError):
            backend.complete(_req(), threading.Event())


class TestCopilotCLI:

    def test_command(self):
        cmd = CopilotCLI().build_command('PROMPT', _req(model='gpt-x'))
        assert cmd == ['copilot', '-p', 'PROMPT', '--model', 'gpt-x', '--output-format', 'json',
                       '--available-tools', 'none', '--no-custom-instructions',
                       '--disable-builtin-mcps', '--no-ask-user', '--no-auto-update']
        assert '--model' not in CopilotCLI().build_command('P', _req(model=''))

    def test_identity(self):
        from src.llm import Capabilities
        backend = CopilotCLI()
        assert backend.name == 'copilot-cli' and backend.default_model == 'claude-sonnet-5.5'
        assert backend.capabilities == Capabilities(
            separate_system=False, multi_turn=False, reports_usage=True,
            sampling_params=False, suggested_concurrency=1)
        assert [m.alias for m in backend.list_models()] == [
            'claude-sonnet-5.5', 'gpt-6.2-sol', 'gemini-4-carbon', 'claude-haiku-4.5']
        assert backend.auth_hint == '請在終端執行 copilot login 重新登入'

    def test_parse_last_message_and_usage(self):
        result = _copilot_ok('最後', tokens=(2160, 214))
        response = CopilotCLI().parse_output(result, _req(model='m'))
        assert response == LLMResponse('最後', 'complete', Usage(2160, 214), 'm')

    def test_usage_summed_over_models_and_missing(self):
        events = [{'type': 'session.usage_checkpoint', 'data': {'accountingSnapshot': {
            'modelMetrics': {'a': {'usage': {'inputTokens': 1, 'outputTokens': 2}},
                             'b': {'usage': {'inputTokens': 10, 'outputTokens': 20}}}}}},
                  'not json', {'type': 'assistant.message', 'data': {'content': 'x'}}]
        text = '\n'.join(e if isinstance(e, str) else json.dumps(e) for e in events)
        response = CopilotCLI().parse_output(ProcessResult(0, text, ''), _req())
        assert response.usage == Usage(11, 22) and response.text == 'x'
        bad = json.dumps({'type': 'session.usage_checkpoint', 'data': {'accountingSnapshot': {
            'modelMetrics': {'a': {'usage': {'inputTokens': 'many'}}}}}})
        assert CopilotCLI().parse_output(ProcessResult(0, bad, ''), _req()).usage is None
        empty = json.dumps({'type': 'session.usage_checkpoint', 'data': {}})
        assert CopilotCLI().parse_output(ProcessResult(0, empty, ''), _req()).usage is None
        assert CopilotCLI().parse_output(ProcessResult(0, '[1]\n', ''), _req()).text == ''

    def test_no_message_is_an_empty_reply(self):
        response = CopilotCLI().parse_output(ProcessResult(0, '', ''), _req())
        assert response.text == '' and response.usage is None

    @pytest.mark.parametrize('result, error, message', [
        (_copilot_error('Execution failed: Model "x" is not available.'),
         BackendUnavailableError, 'copilot CLI: query Execution failed: Model "x" is not available.'),
        (_copilot_error('Response was filtered by the content filter'), RefusalError,
         'copilot CLI: query Response was filtered by the content filter'),
        (_copilot_error("You've reached your weekly rate limit."), RateLimitError,
         "copilot CLI: query You've reached your weekly rate limit."),
        (ProcessResult(1, '', '\nExecution failed: Rate limited.\n'), RateLimitError,
         'copilot CLI: Execution failed: Rate limited.'),
        (_copilot_error('Unauthorized', 'authentication'), AuthError,
         'Copilot CLI 認證失敗，請在終端執行 copilot login 重新登入：authentication Unauthorized'),
        (ProcessResult(2, 'garbage', ''), TransientError, 'copilot CLI exited with code 2: '),
        (_copilot_error('boom', code=0), TransientError,
         'copilot CLI exited with code 0: query boom'),
    ])
    def test_classification(self, result, error, message):
        found = CopilotCLI().classify_error(result, 'prompt')
        assert type(found) is error and str(found) == message

    def test_success_is_no_error(self):
        assert CopilotCLI().classify_error(_copilot_ok('rate limit in the reply')) is None

    def test_error_text_bounded(self):
        found = CopilotCLI().classify_error(_copilot_error('y' * 2000))
        assert len(str(found)) < 600


# --- 5. backend selection ----------------------------------------------------------

class TestBackendSelection:

    def test_backend_for(self):
        assert isinstance(llm.backend_for('copilot-cli'), CopilotCLI)
        assert isinstance(llm.backend_for('claude-cli'), ClaudeCLI)
        assert isinstance(llm.backend_for(None), ClaudeCLI)
        with pytest.raises(ValueError, match="unknown backend: 'x'"):
            llm.backend_for('x')
        fake = FakeBackend()
        with use_fake_backend(fake):
            assert llm.backend_for(None) is fake

    def test_project_field_round_trip(self, tmp_path):
        path = _make_project(tmp_path, n=1)
        pm = ProjectManager(path)
        pm.load()
        assert pm.data.backend is None
        assert 'backend' not in json.loads(pm.to_json())  # existing files unchanged
        pm.set_model('gpt-6.2-sol', backend='copilot-cli')
        pm.save()
        assert json.loads(pm.to_json())['backend'] == 'copilot-cli'
        pm.load()
        assert (pm.data.backend, pm.data.model) == ('copilot-cli', 'gpt-6.2-sol')
        pm.set_model('claude-haiku-4.5')  # the backend stays
        assert pm.data.backend == 'copilot-cli'
        with pytest.raises(ValueError):
            pm.set_model('m', backend='nope')
        assert pm.data.model == 'claude-haiku-4.5'

    @pytest.mark.parametrize('value', ['nope', 1, None, True])
    def test_unknown_backend_refused_on_load(self, tmp_path, value):
        path = _make_project(tmp_path, n=1)
        with open(os.path.join(path, 'project.json'), encoding='utf-8') as f:
            raw = json.load(f)
        raw['backend'] = value
        with open(os.path.join(path, 'project.json'), 'w', encoding='utf-8') as f:
            json.dump(raw, f)
        with pytest.raises(ValueError, match='unknown backend'):
            ProjectManager(path).load()

    def test_default_model_of_the_backend_when_none_stored(self, tmp_path):
        path = _make_project(tmp_path, n=1)
        with open(os.path.join(path, 'project.json'), encoding='utf-8') as f:
            raw = json.load(f)
        del raw['model']
        raw['backend'] = 'copilot-cli'
        with open(os.path.join(path, 'project.json'), 'w', encoding='utf-8') as f:
            json.dump(raw, f)
        assert ProjectManager(path).load().model == 'claude-sonnet-5.5'

    def test_cli_backend_option(self, tmp_path, monkeypatch):
        path = _make_project(tmp_path, n=1)
        fake = FakeBackend(responder=lambda r: like(r, '譯'))
        monkeypatch.setitem(llm.BACKENDS, 'copilot-cli', lambda: fake)
        runner = CliRunner()
        result = runner.invoke(translate.cli, ['start', '--project', path, '--backend',
                                               'copilot-cli', '--model', 'sonnet'])
        assert result.exit_code == 2
        assert "Invalid value for '--model' / '-m': 'sonnet' is not one of fake-model" \
            in result.output
        result = runner.invoke(translate.cli, ['start', '--project', path,
                                               '--backend', 'copilot-cli'])
        assert result.exit_code == 0, result.output
        assert '使用模型：fake-model（copilot-cli）' in result.output
        assert [r.model for r in fake.requests] == ['fake-model']
        pm = ProjectManager(path)
        pm.load()
        assert (pm.data.backend, pm.data.model) == ('copilot-cli', 'fake-model')
        # a later --model is checked against the stored backend
        result = runner.invoke(translate.cli, ['start', '--project', path, '--model', 'opus'])
        assert result.exit_code == 2 and "'opus' is not one of fake-model" in result.output

    def test_cli_backend_choices(self):
        result = CliRunner().invoke(translate.cli, ['start', '--backend', 'nope'])
        assert result.exit_code == 2 and "'claude-cli', 'copilot-cli'" in result.output

    def test_web_model_api(self, tmp_path):
        from src.web.server import create_app
        _make_project(tmp_path, n=1)
        client = create_app(projects_dir=str(tmp_path)).test_client()
        data = client.get(f'/api/projects/{PROJECT}/model').get_json()
        assert data['backend'] == 'claude-cli' and data['current'] == 'sonnet'
        assert [b['name'] for b in data['backends']] == ['claude-cli', 'copilot-cli']
        assert data['backends'][1]['models'][0]['alias'] == 'claude-sonnet-5.5'
        answer = client.put(f'/api/projects/{PROJECT}/model',
                            json={'model': 'gpt-6.2-sol', 'backend': 'copilot-cli'})
        assert answer.status_code == 200
        assert answer.get_json() == {'status': 'ok', 'model': 'gpt-6.2-sol',
                                     'backend': 'copilot-cli'}
        stored = ProjectManager(os.path.join(str(tmp_path), PROJECT))
        stored.load()
        assert (stored.data.backend, stored.data.model) == ('copilot-cli', 'gpt-6.2-sol')
        data = client.get(f'/api/projects/{PROJECT}/model').get_json()
        assert data['backend'] == 'copilot-cli'
        assert data['available'][0]['alias'] == 'claude-sonnet-5.5'
        # a Claude alias is not a Copilot model; the stored backend is kept
        answer = client.put(f'/api/projects/{PROJECT}/model', json={'model': 'sonnet'})
        assert answer.status_code == 400 and 'claude-sonnet-5.5' in answer.get_json()['error']
        answer = client.put(f'/api/projects/{PROJECT}/model',
                            json={'model': 'sonnet', 'backend': 'nope'})
        assert answer.status_code == 400 and answer.get_json()['error'] == '無效的後端：nope'
        answer = client.put(f'/api/projects/{PROJECT}/model',
                            json={'model': 'sonnet', 'backend': 'claude-cli'})
        assert answer.get_json()['backend'] == 'claude-cli'

    def test_worker_uses_the_project_backend(self, tmp_path, fresh_tm, monkeypatch):
        path = _make_project(tmp_path / 'projects', n=1)
        pm = ProjectManager(path)
        pm.load()
        pm.set_model('fake-model', backend='copilot-cli')
        pm.save()
        fake = FakeBackend(responder=lambda r: like(r, '譯'))
        monkeypatch.setitem(llm.BACKENDS, 'copilot-cli', lambda: fake)
        task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
        task._coordinator.join(timeout=20)
        assert task.state == 'completed' and len(fake.requests) == 1


# --- 6. CLI lean flags ------------------------------------------------------------

class TestClaudeLeanFlags:

    def test_flags(self):
        from src.llm.claude_cli import LEAN_FLAGS, SYSTEM_PROMPT
        assert SYSTEM_PROMPT == '你是一位專業的英文翻譯。'
        assert LEAN_FLAGS == ('--system-prompt', SYSTEM_PROMPT, '--tools', '',
                              '--strict-mcp-config', '--setting-sources', '',
                              '--no-session-persistence')


# --- mutation survivors (run 1) ----------------------------------------------------

class TestPhase7Survivors:

    def test_short_positive_retry_after(self):
        assert resume_delay(RateLimitError('x', retry_after=0.5)) == 0.5

    def test_default_model_of_a_backend(self):
        from src.core.translator import default_model
        assert default_model('copilot-cli') == 'claude-sonnet-5.5'
        assert default_model() == 'sonnet'

    def test_copilot_data_that_is_not_an_object(self):
        events = [{'type': 'assistant.message', 'data': ['not', 'an', 'object']},
                  {'type': 'session.error', 'data': 'text'}]
        text = '\n'.join(json.dumps(e) for e in events)
        result = ProcessResult(1, text, '')
        assert CopilotCLI().parse_output(result, _req()).text == ''
        assert str(CopilotCLI().classify_error(result)) == 'copilot CLI exited with code 1: '

    def test_copilot_errors_joined(self):
        result = _copilot_error('first')
        result = ProcessResult(1, result.stdout, 'second')
        assert str(CopilotCLI().classify_error(result)) == \
            'copilot CLI exited with code 1: query first | second'

    def test_web_time_window_saves_while_running(self, tmp_path, fresh_tm, clock):
        path = _make_project(tmp_path / 'projects', n=2)
        gate = threading.Event()

        def respond(request):
            if '>Line 2.<' in request.messages[0].content:
                assert gate.wait(20)
            return like(request, '譯')

        with use_fake_backend(FakeBackend(responder=respond)):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            _wait(lambda: task.completed == 1)
            time.sleep(0.1)
            assert _statuses(path) == [SegmentStatus.PENDING] * 2  # window open
            clock.now += SAVE_INTERVAL_SECONDS
            _wait(lambda: _statuses(path)[0] == SegmentStatus.TRANSLATED)
            assert task.state == 'running'
            gate.set()
            task._coordinator.join(timeout=20)

    def test_resumed_event_and_log(self, tmp_path, fresh_tm, clock, caplog):
        import logging
        caplog.set_level(logging.INFO, logger='src.web.tasks')
        _make_project(tmp_path / 'projects', n=2)
        respond = _limited_once(RateLimitError('limit', retry_after=5))
        with use_fake_backend(FakeBackend(responder=respond)):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            _wait(lambda: task.state == 'paused')
            clock.now += 5
            task._coordinator.join(timeout=20)
        resumed = [e for e in task.progress.get_all() if e['event'] == 'resumed']
        assert resumed == [{'event': 'resumed', 'data': {'completed': 0, 'remaining': 2}}]
        assert '翻譯自動繼續（使用量限制的等待結束）' in caplog.messages

    def test_failed_save_logged(self, tmp_path, fresh_tm, clock, monkeypatch, caplog):
        _make_project(tmp_path / 'projects', n=1)
        monkeypatch.setattr(ProjectManager, 'save',
                            lambda self: (_ for _ in ()).throw(OSError('disk full')))
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            task._coordinator.join(timeout=20)
        assert '無法儲存專案' in caplog.messages

    def test_backend_switched_mid_run(self, tmp_path, fresh_tm, monkeypatch):
        """The worker follows the project's backend from the next segment on."""
        _make_project(tmp_path / 'projects', n=2)
        other = FakeBackend(responder=lambda r: like(r, '乙'))
        monkeypatch.setitem(llm.BACKENDS, 'copilot-cli', lambda: other)
        holder, ready = {}, threading.Event()

        def first(request):
            assert ready.wait(10)
            holder['task'].pm.set_model('fake-model', backend='copilot-cli')
            return like(request, '甲')

        default = FakeBackend(responder=first)
        with use_fake_backend(default):
            fresh_tm.tasks.clear()
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            holder['task'] = task
            ready.set()
            task._coordinator.join(timeout=20)
        assert task.state == 'completed'
        # one worker: the second segment went to the new backend
        assert (len(default.requests), len(other.requests)) == (1, 1)
