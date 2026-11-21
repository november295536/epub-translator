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


def _ticks(task, n=3):
    """Wait until the coordinator has looked at the task n more times."""
    start = task.ticks
    _wait(lambda: task.ticks >= start + n)


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
            _ticks(task)  # not yet
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
            _ticks(task)
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

    @pytest.mark.parametrize('workers', ['1'])
    @pytest.mark.parametrize('retry_after, wait', [(7, 7), (None, RATE_LIMIT_WAIT_SECONDS)])
    def test_resumes_without_user(self, tmp_path, clock, monkeypatch, workers, retry_after, wait):
        path = _make_project(tmp_path, n=3)
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            clock.now += seconds

        monkeypatch.setattr(translate, '_sleep', sleep)

        respond = _limited_once(RateLimitError('limit reached', retry_after=retry_after))
        with use_fake_backend(FakeBackend(responder=respond)):
            result = CliRunner().invoke(translate.cli, ['start', '--project', path,
                                                        '--workers', workers])
        assert result.exit_code == 0, result.output
        assert ('  ✗ limit reached\n'
                f'  使用量已達上限：{wait:.0f} 秒後自動繼續（Ctrl-C 停止）\n') in result.output
        assert _statuses(path) == [SegmentStatus.TRANSLATED] * 3
        assert len(respond.calls) == 4  # the limited call is made again
        assert slept == [wait]

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

        def wait(seconds, cancel, interrupted):
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
        assert 100 // SAVE_EVERY_RESULTS <= len(writes) <= 100 // SAVE_EVERY_RESULTS + 1  # in windows, not only at the end
        assert _statuses(path) == [SegmentStatus.TRANSLATED] * 100

    @pytest.mark.parametrize('workers', ['1', '4'])
    def test_cli(self, tmp_path, clock, monkeypatch, workers):
        path = _make_project(tmp_path, n=100)
        writes = _count_saves(monkeypatch)
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))):
            result = CliRunner().invoke(translate.cli, ['start', '--project', path,
                                                        '--workers', workers])
        assert result.exit_code == 0, result.output
        assert 100 // SAVE_EVERY_RESULTS <= len(writes) <= 100 // SAVE_EVERY_RESULTS + 1  # in windows, not only at the end
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
            _ticks(task)  # the window (1 of 20, no time passed) is not saved
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
        none = json.dumps({'type': 'session.usage_checkpoint', 'data': {'accountingSnapshot': {
            'modelMetrics': {}}}})
        assert CopilotCLI().parse_output(ProcessResult(0, none, ''), _req()).usage is None
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
        # review round 1, G: plain text on stdout is the message
        (ProcessResult(2, 'garbage', ''), TransientError, 'copilot CLI exited with code 2: garbage'),
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
        assert resume_delay(RateLimitError('x', retry_after=0.5)) == 1.0  # round 1: at least 1 s
        assert resume_delay(RateLimitError('x', retry_after=1.5)) == 1.5

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
            _ticks(task)
            assert _statuses(path) == [SegmentStatus.PENDING] * 2  # window open
            clock.now += SAVE_INTERVAL_SECONDS
            _ticks(task)  # the injected clock closed the window
            assert _statuses(path)[0] == SegmentStatus.TRANSLATED
            assert task.state == 'running'
            gate.set()
            task._coordinator.join(timeout=20)

    def test_resumed_event_and_log(self, tmp_path, fresh_tm, clock, caplog):
        import logging
        caplog.set_level(logging.INFO, logger='src.web.tasks')
        _make_project(tmp_path / 'projects', n=3)
        calls = []

        def respond(request):  # the second call is limited
            calls.append(1)
            if len(calls) == 2:
                raise RateLimitError('limit', retry_after=5)
            return like(request, '譯')

        with use_fake_backend(FakeBackend(responder=respond)):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            _wait(lambda: task.state == 'paused')
            _wait(lambda: task.completed == 1)
            clock.now += 5
            task._coordinator.join(timeout=20)
        resumed = [e for e in task.progress.get_all() if e['event'] == 'resumed']
        assert resumed == [{'event': 'resumed', 'data': {'completed': 1, 'remaining': 2}}]
        assert '翻譯自動繼續（使用量限制的等待結束）' in caplog.messages

    def test_failed_save_logged(self, tmp_path, fresh_tm, clock, monkeypatch, caplog):
        _make_project(tmp_path / 'projects', n=1)
        monkeypatch.setattr(ProjectManager, 'save',
                            lambda self: (_ for _ in ()).throw(OSError('disk full')))
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            task._coordinator.join(timeout=20)
        assert '無法儲存專案' in caplog.messages

    def test_backend_switched_mid_run(self, tmp_path, fresh_tm, monkeypatch, caplog):
        """The worker follows the project's backend from the next segment on."""
        import logging
        caplog.set_level(logging.INFO, logger='src.web.tasks')
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
        assert '[W0] 模型切換: sonnet → fake-model' in caplog.messages


# --- review round 1 ----------------------------------------------------------------

def _run_cli_in_thread(args):
    """CliRunner.invoke in a thread; returns (thread, holder of the result)."""
    holder = {}

    def run():
        holder['result'] = CliRunner().invoke(translate.cli, args)

    t = threading.Thread(target=run, daemon=True)  # a failing test does not hang
    t.start()
    return t, holder


class _CountingClock(FakeClock):
    def __init__(self, now=1000.0):
        super().__init__(now)
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return self.now


class TestRound1CancelGenerations:
    """Item 1: resume installs a new cancel event; a call from before the
    pause stays cancelled."""

    def test_quick_resume_does_not_revive_the_old_call(self, tmp_path, fresh_tm, monkeypatch):
        monkeypatch.setattr('src.llm.cli.run_process', real_run_process)
        pid_dir = tmp_path / 'pids'
        pid_dir.mkdir()
        sleeper = SleeperBackend(str(pid_dir))
        path = _make_project(tmp_path / 'projects', n=1)
        with use_fake_backend(sleeper):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            _wait(lambda: sleeper.pids())
            first = task.cancel_event
            assert fresh_tm.pause_translation(PROJECT)
            assert fresh_tm.resume_translation(PROJECT)  # at once
            assert first.is_set() and task.cancel_event is not first
            assert not task.cancel_event.is_set()
            assert _gone(sleeper.pids()[0])
            _wait(lambda: len(sleeper.pids()) == 2)  # sent again, new child
            fresh_tm.stop_translation(PROJECT)
            task._coordinator.join(timeout=20)
        assert all(_gone(p) for p in sleeper.pids())
        assert _statuses(path) == [SegmentStatus.PENDING]


class TestRound1PauseResumeActions:
    """Item 2: explicit, idempotent pause/resume; the tick's deadline check."""

    def _client(self, tmp_path):
        from src.web.server import create_app
        return create_app(projects_dir=str(tmp_path / 'projects')).test_client()

    def test_user_pause_during_the_automatic_wait(self, tmp_path, fresh_tm, clock):
        path = _make_project(tmp_path / 'projects', n=2)
        client = self._client(tmp_path)
        url = f'/api/projects/{PROJECT}/translate/pause'
        respond = _limited_once(RateLimitError('limit', retry_after=30))
        with use_fake_backend(FakeBackend(responder=respond)):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            _wait(lambda: task.state == 'paused')
            status = client.get(f'/api/projects/{PROJECT}/translate/status').get_json()
            assert status['auto_resume'] is True
            for _ in range(2):  # idempotent
                answer = client.post(url, json={'action': 'pause'})
                assert answer.status_code == 200 and answer.get_json()['state'] == 'paused'
            assert task.resume_at is None
            assert client.get(f'/api/projects/{PROJECT}/translate/status'
                              ).get_json()['auto_resume'] is False
            clock.now += 10 ** 6
            _ticks(task)
            assert task.state == 'paused'  # a lasting user pause
            for _ in range(2):
                answer = client.post(url, json={'action': 'resume'})
                assert answer.status_code == 200 and answer.get_json()['state'] == 'running'
            task._coordinator.join(timeout=20)
        assert task.state == 'completed'
        assert _statuses(path) == [SegmentStatus.TRANSLATED] * 2
        assert client.post(url, json={}).status_code == 400
        assert client.post(url, json={'action': 'toggle'}).get_json() == {
            'error': 'action 必須是 pause 或 resume'}

    def test_automatic_resume_only_for_the_deadline_that_was_due(self, tmp_path, fresh_tm):
        task = tasks_module.TranslationTask('t', PROJECT, str(tmp_path), state='paused')
        alive = threading.Event()
        keeper = threading.Thread(target=alive.wait, daemon=True)
        keeper.start()
        task._coordinator = keeper
        fresh_tm.tasks[PROJECT] = task
        task.resume_at = 50.0
        assert not fresh_tm.resume_translation(PROJECT, due=40.0)  # a newer deadline
        assert task.state == 'paused'
        task.resume_at = None  # a user pause meanwhile
        assert not fresh_tm.resume_translation(PROJECT, due=50.0)
        task.resume_at = 50.0
        assert fresh_tm.resume_translation(PROJECT, due=50.0) and task.state == 'running'
        assert not fresh_tm.resume_translation(PROJECT, due=50.0)  # running: no second resume
        alive.set()
        keeper.join()

    def test_page_sends_actions_and_offers_pause_during_the_wait(self, tmp_path):
        _make_project(tmp_path / 'projects', n=1)
        html = self._client(tmp_path).get(f'/project/{PROJECT}/translation').get_data(as_text=True)
        assert "x-if=\"state === 'running' || (state === 'paused' && autoResume)\"" in html
        assert "@click=\"pauseTranslation('pause')\"" in html
        assert "@click=\"pauseTranslation('resume')\"" in html
        assert 'const body = { action: action };' in html
        # item E: the banner is cleared once running again
        assert "if (data.state === 'running') this.error = null;" in html


class TestRound1ErrorPrecedence:
    """Item 3: an error that waits for the user wins over a rate-limit
    wait; a later, longer limit extends it; a user pause stays."""

    def _task(self, tmp_path, state, resume_at):
        task = tasks_module.TranslationTask('t', PROJECT, str(tmp_path), state=state)
        task.resume_at = resume_at
        return task

    def test_web(self, tmp_path, fresh_tm, clock):
        task = self._task(tmp_path, 'paused', clock.now + 10)
        fresh_tm._pause_for(task, RateLimitError('later', retry_after=60), 'rate_limit')
        assert task.resume_at == clock.now + 60  # extended
        fresh_tm._pause_for(task, RateLimitError('sooner', retry_after=5), 'rate_limit')
        assert task.resume_at == clock.now + 60  # never shortened
        fresh_tm._pause_for(task, AuthError('expired'), 'auth_error')
        assert task.resume_at is None and task.error == 'expired'
        assert task.progress.get_all()[-1]['event'] == 'auth_error'
        fresh_tm._pause_for(task, RateLimitError('again', retry_after=5), 'rate_limit')
        assert task.resume_at is None and task.error == 'expired'  # waits for the user
        user = self._task(tmp_path, 'paused', None)
        fresh_tm._pause_for(user, RateLimitError('x', retry_after=5), 'rate_limit')
        assert user.resume_at is None and user.error is None

    def test_cli_auth_during_the_wait_stops(self, tmp_path, monkeypatch):
        clock = _CountingClock()
        monkeypatch.setattr(translate, '_clock', clock)
        path = _make_project(tmp_path, n=2)
        announced = threading.Event()
        real_message = translate._rate_limit_message

        def message(e, delay):
            announced.set()
            return real_message(e, delay)

        monkeypatch.setattr(translate, '_rate_limit_message', message)

        both = threading.Barrier(2)

        def respond(request):
            both.wait(20)  # both calls in flight before the gate closes
            if '>Line 1.<' in request.messages[0].content:
                raise RateLimitError('limit', retry_after=30)
            assert announced.wait(20)  # during the wait
            raise AuthError('expired')

        with use_fake_backend(FakeBackend(responder=respond)):
            result = CliRunner().invoke(translate.cli, ['start', '--project', path,
                                                        '--workers', '2'])
        assert result.exit_code == 1 and 'expired' in result.output
        assert '  繼續翻譯\n' not in result.output  # the gate never reopened

    def test_cli_later_limit_extends_the_wait(self, tmp_path, monkeypatch):
        clock = _CountingClock()
        monkeypatch.setattr(translate, '_clock', clock)
        path = _make_project(tmp_path, n=2)
        announced, calls = threading.Event(), []
        real_message = translate._rate_limit_message
        monkeypatch.setattr(translate, '_rate_limit_message',
                            lambda e, d: (announced.set(), real_message(e, d))[1])

        both = threading.Barrier(2)

        def respond(request):
            calls.append(request)
            if len(calls) <= 2:
                both.wait(20)  # both calls in flight before the gate closes
            if request is calls[0]:
                raise RateLimitError('first', retry_after=7)
            if request is calls[1]:
                assert announced.wait(20)
                raise RateLimitError('second', retry_after=20)
            return like(request, '譯')

        with use_fake_backend(FakeBackend(responder=respond)):
            thread, holder = _run_cli_in_thread(['start', '--project', path, '--workers', '2'])
            _wait(lambda: len(calls) == 2)
            start = clock.reads
            _wait(lambda: clock.reads > start + 4)
            clock.now += 7  # the first deadline: the wait was extended
            start = clock.reads
            _wait(lambda: clock.reads > start + 4)
            assert len(calls) == 2
            clock.now += 13
            thread.join(timeout=30)
        assert holder['result'].exit_code == 0, holder['result'].output
        assert len(calls) == 4


class TestRound1ParallelCliGate:
    """Item 13: a stationary clock; the gate stays closed before the
    deadline and opens after it; the printed seconds."""

    @pytest.mark.parametrize('retry_after, wait', [(7, 7), (None, RATE_LIMIT_WAIT_SECONDS)])
    def test_gate(self, tmp_path, monkeypatch, retry_after, wait):
        clock = _CountingClock()
        monkeypatch.setattr(translate, '_clock', clock)
        path = _make_project(tmp_path, n=4)
        calls, release = [], threading.Event()

        both = threading.Barrier(2)

        def respond(request):
            calls.append(request)
            if len(calls) <= 2:
                both.wait(20)  # both calls in flight before the gate closes
            if request is calls[0]:
                raise RateLimitError('limit reached', retry_after=retry_after)
            if request is calls[1]:
                assert release.wait(20)  # in flight while the gate closes
            return like(request, '譯')

        with use_fake_backend(FakeBackend(responder=respond)):
            thread, holder = _run_cli_in_thread(['start', '--project', path, '--workers', '2'])
            _wait(lambda: len(calls) == 2)
            release.set()
            start = clock.reads
            _wait(lambda: clock.reads > start + 6)  # the main loop keeps ticking
            assert len(calls) == 2  # closed: no new call before the deadline
            clock.now += wait
            thread.join(timeout=30)
        result = holder['result']
        assert result.exit_code == 0, result.output
        assert len(calls) == 5
        assert (f'  ✗ limit reached\n  使用量已達上限：{wait:.0f} 秒後自動繼續（Ctrl-C 停止）\n'
                in result.output)
        assert '  繼續翻譯' in result.output
        assert _statuses(path) == [SegmentStatus.TRANSLATED] * 4


class _HookQueue:
    """A queue whose first successful get() runs `hook` before returning."""

    def __init__(self, inner, hook):
        self.inner, self.hook, self.done = inner, hook, False

    def get(self, *a, **kw):
        item = self.inner.get(*a, **kw)
        if not self.done:
            self.done = True
            self.hook()
        return item

    def __getattr__(self, name):
        return getattr(self.inner, name)


class TestRound1RecheckAfterGet:
    """Items 4 and C: a segment taken from the queue while the run pauses
    is put back, not sent."""

    def test_web(self, tmp_path, fresh_tm):
        from src.glossary.glossary_manager import GlossaryManager
        path = _make_project(tmp_path / 'projects', n=1)
        task = tasks_module.TranslationTask('t', PROJECT, str(tmp_path / 'projects'),
                                            state='running', total=1)
        task.pm = ProjectManager(path)
        task.pm.load()
        task._gm = GlossaryManager(os.path.join(path, 'glossary.json'))
        task.active_workers = 1
        task.segment_queue.put(task.pm.data.segments[0])
        task.segment_queue = _HookQueue(task.segment_queue, task.pause_event.clear)
        fake = FakeBackend(responder=lambda r: like(r, '譯'))
        with use_fake_backend(fake):
            worker = threading.Thread(target=fresh_tm._worker_loop, args=(task, 0), daemon=True)
            worker.start()
            try:
                _wait(lambda: task.segment_queue.done and task.segment_queue.qsize() == 1)
            finally:
                task.stop_event.set()
                task.pause_event.set()
                worker.join(timeout=20)
        assert fake.requests == []
        assert task.segment_queue.qsize() == 1

    def test_cli(self, tmp_path, monkeypatch):
        """A worker waiting in get() when the segment is put back does not
        send it while the gate is closed (gate checked after get)."""
        import queue
        clock = _CountingClock()
        monkeypatch.setattr(translate, '_clock', clock)
        path = _make_project(tmp_path, n=2)
        calls, made = [], []

        class Watched(queue.Queue):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.waiting = 0
                made.append(self)

            def get(self, *a, **kw):
                self.waiting += 1
                try:
                    return super().get(*a, **kw)
                finally:
                    self.waiting -= 1

        monkeypatch.setattr(queue, 'Queue', Watched)

        limited = []

        def respond(request):
            calls.append(request)
            if '>Line 1.<' in request.messages[0].content and not limited:
                limited.append(1)
                # the other worker has done Line 2 and waits in get()
                _wait(lambda: made[0].waiting > 0)
                raise RateLimitError('limit', retry_after=7)
            return like(request, '譯')

        with use_fake_backend(FakeBackend(responder=respond)):
            thread, holder = _run_cli_in_thread(['start', '--project', path, '--workers', '2'])
            _wait(lambda: limited and len(calls) == 2)
            start = clock.reads
            _wait(lambda: clock.reads > start + 6)
            assert len(calls) == 2  # the waiting worker got Line 1 back and did not send it
            clock.now += 7
            thread.join(timeout=30)
        assert holder['result'].exit_code == 0 and len(calls) == 3


class TestRound1BackoffInterrupted:
    """Item 5: a retry's backoff ends on a run-level stop or pause and no
    call follows."""

    def test_engine(self, backoff_waits):
        stopped = []
        fake = FakeBackend([TransientError('t'), '<p k="1">譯</p>'])

        def interrupted():
            return bool(stopped)

        engine = TranslationEngine(model='m', backend=fake, interrupted=interrupted)
        stopped.append(1)
        with pytest.raises(CancelledError):
            engine.translate_reply('Line.', SegmentType.TEXT, '', '', '', '<p>Line.</p>')
        assert len(fake.requests) == 1 and backoff_waits == [5]
        FakeBackend.overcalls.clear()

    def test_default_wait_ends_on_interrupted(self):
        assert wait_or_cancel(30, threading.Event(), lambda: True) is True
        assert wait_or_cancel(0, threading.Event(), lambda: False) is False

    def test_web_worker_interrupted_by_a_pause(self, tmp_path, fresh_tm):
        """The worker's check: stop, or the pause gate closed."""
        _make_project(tmp_path / 'projects', n=1)
        seen = {}
        real = tasks_module.TranslationEngine

        def engine(**kw):
            seen['interrupted'] = kw['interrupted']
            return real(**kw)

        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))), \
                pytest.MonkeyPatch.context() as mp:
            mp.setattr(tasks_module, 'TranslationEngine', engine)
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            task._coordinator.join(timeout=20)
        check = seen['interrupted']
        task.stop_event.clear()
        task.pause_event.set()
        assert check() is False
        task.pause_event.clear()
        assert check() is True
        task.pause_event.set()
        task.stop_event.set()
        assert check() is True

    def test_cli_backoff_during_a_rate_limit_pause(self, tmp_path, monkeypatch, backoff_waits):
        clock = _CountingClock()
        monkeypatch.setattr(translate, '_clock', clock)
        path = _make_project(tmp_path, n=2)
        announced, calls = threading.Event(), []
        real_message = translate._rate_limit_message
        monkeypatch.setattr(translate, '_rate_limit_message',
                            lambda e, d: (announced.set(), real_message(e, d))[1])

        both = threading.Barrier(2)

        def respond(request):
            calls.append(request)
            if len(calls) <= 2:
                both.wait(20)  # both calls in flight before the gate closes
            if request is calls[0]:
                raise RateLimitError('limit', retry_after=7)
            if request is calls[1]:
                assert announced.wait(20)
                raise TransientError('exit 1')  # its backoff falls in the pause
            return like(request, '譯')

        with use_fake_backend(FakeBackend(responder=respond)):
            thread, holder = _run_cli_in_thread(['start', '--project', path, '--workers', '2'])
            _wait(lambda: backoff_waits)
            start = clock.reads
            _wait(lambda: clock.reads > start + 6)
            assert len(calls) == 2  # no retry during the pause
            clock.now += 7
            thread.join(timeout=30)
        assert holder['result'].exit_code == 0, holder['result'].output
        assert len(calls) == 4
        assert _statuses(path) == [SegmentStatus.TRANSLATED] * 2


class TestRound1ResumeDelay:
    """Item 6."""

    @pytest.mark.parametrize('value, expected', [
        (0.2, 1.0), (1, 1.0), (90, 90.0), (10 ** 9, 3600.0), (float('inf'), 300.0),
        (float('nan'), 300.0), (True, 300.0), ('12', 300.0), (-1, 300.0), (None, 300.0),
        (object(), 300.0)])
    def test_values(self, value, expected):
        error = RateLimitError('x')
        error.retry_after = value
        assert resume_delay(error) == expected


class TestRound1ModelReadUnderMutex:
    """Item 7: the worker reads (backend, model) as one, under the mutex."""

    def test_read_under_mutex(self, tmp_path, fresh_tm):
        from src.core.project import ProjectData
        _make_project(tmp_path / 'projects', n=1)
        held, box = [], {}

        class Watched(ProjectData):
            @property
            def backend(self):
                held.append(box['mutex']._is_owned())
                return self.__dict__.get('backend')

        original = tasks_module.TaskManager._worker_loop

        def worker(self, task, wid):
            box['mutex'] = task.pm.mutex
            task.pm.data.__class__ = Watched
            return original(self, task, wid)

        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))), \
                pytest.MonkeyPatch.context() as mp:
            mp.setattr(tasks_module.TaskManager, '_worker_loop', worker)
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            task._coordinator.join(timeout=20)
        assert held and all(held)


class TestRound1SequentialForcedFlush:
    """Item 8: Ctrl-C between apply_outcome and the saver keeps the result."""

    def test_interrupt_after_apply(self, tmp_path, monkeypatch):
        path = _make_project(tmp_path, n=2)
        real_apply = ProjectManager.apply_outcome

        def interrupt(self, *a, **kw):
            real_apply(self, *a, **kw)
            raise KeyboardInterrupt

        monkeypatch.setattr(ProjectManager, 'apply_outcome', interrupt)
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))):
            result = CliRunner().invoke(translate.cli, ['start', '--project', path])
        assert result.exit_code == 1
        assert _statuses(path) == [SegmentStatus.TRANSLATED, SegmentStatus.PENDING]


class TestRound1ExportFlushes:
    """Item 9: an export during a run first saves the window."""

    def test_export_sees_the_window(self, tmp_path, fresh_tm, clock, monkeypatch):
        from src.web.server import create_app
        path = _make_project(tmp_path / 'projects', n=2)
        gate = threading.Event()
        read = []
        import src.web.blueprints.export as export_bp

        def fake_export(project_dir, output_path, fmt):
            read.append(_statuses(project_dir))
            raise RuntimeError('stop here')

        monkeypatch.setattr(export_bp, 'export_project', fake_export)

        def respond(request):
            if '>Line 2.<' in request.messages[0].content:
                assert gate.wait(20)
            return like(request, '譯')

        client = create_app(projects_dir=str(tmp_path / 'projects')).test_client()
        with use_fake_backend(FakeBackend(responder=respond)):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            _wait(lambda: task.completed == 1)
            assert _statuses(path)[0] == SegmentStatus.PENDING  # in the window
            answer = client.post(f'/api/projects/{PROJECT}/export',
                                 json={'format': 'chinese-only'})
            assert answer.status_code == 200
            _wait(lambda: read)
            gate.set()
            task._coordinator.join(timeout=20)
        assert read[0][0] == SegmentStatus.TRANSLATED

    def test_failed_flush_answers_500(self, tmp_path, fresh_tm, monkeypatch):
        from src.web.server import create_app
        _make_project(tmp_path / 'projects', n=1)
        pm = ProjectManager(os.path.join(str(tmp_path / 'projects'), PROJECT))
        pm.load()
        pm.mark_translated('c1_seg0001', '<p>譯</p>')
        pm.save()
        task = tasks_module.TranslationTask('t', PROJECT, str(tmp_path / 'projects'),
                                            state='running')
        task.pm = pm

        class Broken:
            def flush(self):
                raise OSError('disk full')

        task.saver = Broken()
        fresh_tm.tasks[PROJECT] = task
        client = create_app(projects_dir=str(tmp_path / 'projects')).test_client()
        answer = client.post(f'/api/projects/{PROJECT}/export', json={'format': 'chinese-only'})
        assert answer.status_code == 500
        assert answer.get_json() == {'error': '無法儲存專案：OSError: disk full'}
        task.state = 'completed'


class TestRound1BackendKey:
    """Item 10: no backend key for the project's own backend."""

    def test_web_claude_model_change_writes_no_key(self, tmp_path):
        from src.web.server import create_app
        path = _make_project(tmp_path, n=1)
        client = create_app(projects_dir=str(tmp_path)).test_client()
        answer = client.put(f'/api/projects/{PROJECT}/model',
                            json={'model': 'opus', 'backend': 'claude-cli'})
        assert answer.get_json() == {'status': 'ok', 'model': 'opus', 'backend': 'claude-cli'}
        with open(os.path.join(path, 'project.json'), encoding='utf-8') as f:
            raw = json.load(f)
        assert raw['model'] == 'opus' and 'backend' not in raw

    def test_web_non_string_backend(self, tmp_path):
        """Item F."""
        from src.web.server import create_app
        _make_project(tmp_path, n=1)
        client = create_app(projects_dir=str(tmp_path)).test_client()
        for value in (['claude-cli'], {'a': 1}, 3):
            answer = client.put(f'/api/projects/{PROJECT}/model',
                                json={'model': 'opus', 'backend': value})
            assert answer.status_code == 400

    def test_cli_backend_option_keeps_the_stored_model(self, tmp_path, monkeypatch):
        path = _make_project(tmp_path, n=1)
        pm = ProjectManager(path)
        pm.load()
        pm.set_model('gpt-6.2-sol', backend='copilot-cli')
        pm.save()
        pm.mark_translated('c1_seg0001', '<p>譯</p>')  # nothing to translate
        pm.save()
        result = CliRunner().invoke(translate.cli, ['start', '--project', path,
                                                    '-b', 'copilot-cli'])
        assert result.exit_code == 0, result.output
        pm.load()
        assert (pm.data.backend, pm.data.model) == ('copilot-cli', 'gpt-6.2-sol')

    def test_cli_implicit_backend_writes_no_key(self, tmp_path):
        path = _make_project(tmp_path, n=1)
        pm = ProjectManager(path)
        pm.load()
        pm.mark_translated('c1_seg0001', '<p>譯</p>')
        pm.save()
        result = CliRunner().invoke(translate.cli, ['start', '--project', path,
                                                    '-b', 'claude-cli', '-m', 'opus'])
        assert result.exit_code == 0, result.output
        with open(os.path.join(path, 'project.json'), encoding='utf-8') as f:
            raw = json.load(f)
        assert raw['model'] == 'opus' and 'backend' not in raw


class TestRound1Copilot:
    """Items 11 and G."""

    def _events(self, *events, code=0, stderr=''):
        return ProcessResult(code, '\n'.join(json.dumps(e) for e in events), stderr)

    REPLY = {'type': 'assistant.message', 'data': {'content': '譯文'}}

    def test_error_beside_a_reply(self):
        result = self._events(self.REPLY, {'type': 'session.error', 'data': {
            'errorType': 'query', 'message': 'telemetry upload failed'}})
        assert CopilotCLI().classify_error(result) is None
        result = self._events(self.REPLY, {'type': 'session.error', 'data': {
            'errorType': 'query', 'message': 'Model "x" is not available'}})
        assert CopilotCLI().classify_error(result) is None
        limited = self._events(self.REPLY, {'type': 'session.error', 'data': {
            'errorType': 'query', 'message': "You've hit your rate limit."}})
        assert type(CopilotCLI().classify_error(limited)) is RateLimitError
        auth = self._events(self.REPLY, {'type': 'session.error', 'data': {
            'errorType': 'authentication', 'message': 'not authenticated'}})
        assert type(CopilotCLI().classify_error(auth)) is AuthError

    def test_error_without_a_reply_is_classified(self):
        result = self._events({'type': 'session.error', 'data': {'message': 'socket hang up'}})
        found = CopilotCLI().classify_error(result)
        assert type(found) is TransientError
        assert str(found) == 'copilot CLI exited with code 0: socket hang up'

    def test_filtered_alone_is_not_a_refusal(self):
        result = self._events({'type': 'session.error', 'data': {
            'message': 'results filtered by policy'}}, code=1)
        assert type(CopilotCLI().classify_error(result)) is TransientError

    def test_plain_stdout_only_without_error_events(self):
        result = ProcessResult(1, 'plain text\n{"type": "x"}\nmore', '')
        assert str(CopilotCLI().classify_error(result)) == \
            'copilot CLI exited with code 1: plain text more'
        with_event = self._events({'type': 'session.error', 'data': {'message': 'boom'}},
                                  code=1)
        with_event = ProcessResult(1, with_event.stdout + '\nnoise', '')
        assert str(CopilotCLI().classify_error(with_event)) == \
            'copilot CLI exited with code 1: boom'

    def test_emails_redacted_in_message_and_raw(self, monkeypatch):
        output = self._events({'type': 'session.error', 'data': {
            'message': 'Account someone.x@example.org cannot use this'}}, code=1,
            stderr='signed in as a-b@mail.example.com')
        monkeypatch.setattr('src.llm.cli.run_process', lambda cmd, **kw: output)
        with pytest.raises(TransientError) as info:
            CopilotCLI().complete(_req(), threading.Event())
        assert '@' not in str(info.value) and '@' not in info.value.raw
        assert str(info.value) == ('copilot CLI exited with code 1: Account <redacted> cannot '
                                   'use this | signed in as <redacted>')
        assert '<redacted>' in info.value.raw

    def test_timeout_output_redacted(self, monkeypatch):
        import subprocess

        def timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 9, output='x@y.io', stderr='')
        monkeypatch.setattr('src.llm.cli.run_process', timeout)
        with pytest.raises(TransientError) as info:
            CopilotCLI().complete(_req(), threading.Event())
        assert info.value.raw == '<redacted>'

    def test_claude_keeps_its_output(self):
        assert ClaudeCLI().redact('a@b.io') == 'a@b.io'


class TestRound1WorkersComeBack:
    """Item B: no completion while a segment is queued; resume tops up."""

    def test_queued_segment_gets_a_worker(self, tmp_path, fresh_tm):
        from src.glossary.glossary_manager import GlossaryManager
        path = _make_project(tmp_path / 'projects', n=1)
        task = tasks_module.TranslationTask('t', PROJECT, str(tmp_path / 'projects'),
                                            state='running', total=1, num_workers=1)
        task.pm = ProjectManager(path)
        task.pm.load()
        task._gm = GlossaryManager(os.path.join(path, 'glossary.json'))
        task.segment_queue.put(task.pm.data.segments[0])
        fake = FakeBackend(responder=lambda r: like(r, '譯'))
        with use_fake_backend(fake):
            assert fresh_tm._workers_done(task) is False  # a worker was started
            assert len(task._threads) == 1
            task._threads[0].join(timeout=20)
        assert len(fake.requests) == 1
        assert task.result_queue.qsize() == 1
        assert fresh_tm._workers_done(task) is False  # a result is waiting
        task.result_queue.get()
        assert fresh_tm._workers_done(task) is True
        task.segment_queue.put(task.pm.data.segments[0])
        task.stop_event.set()
        assert fresh_tm._workers_done(task) is True  # stopping: queued is fine

    def test_resume_tops_up_without_a_count(self, tmp_path, fresh_tm):
        task = tasks_module.TranslationTask('t', PROJECT, str(tmp_path), state='paused',
                                            num_workers=2)
        alive = threading.Event()
        keeper = threading.Thread(target=alive.wait, daemon=True)
        keeper.start()
        task._coordinator = keeper
        task.pm, task._gm = object(), object()
        fresh_tm.tasks[PROJECT] = task
        started = []

        def worker(self, t, wid):
            started.append(wid)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(tasks_module.TaskManager, '_worker_loop', worker)
            assert fresh_tm.resume_translation(PROJECT)
        for t in task._threads:
            t.join()
        assert sorted(started) == [0, 1]
        alive.set()
        keeper.join()


class TestRound1StaleRunError:
    """Item D: a call from before a pause and resume that ends in a
    rate limit does not pause the resumed run."""

    def test_stale_rate_limit(self, tmp_path, fresh_tm, clock):
        path = _make_project(tmp_path / 'projects', n=1)
        in_flight, release, calls = threading.Event(), threading.Event(), []

        def respond(request):  # the fake ignores cancel: the call goes on
            calls.append(1)
            if len(calls) == 1:
                in_flight.set()
                assert release.wait(20)
                raise RateLimitError('old limit', retry_after=30)
            return like(request, '譯')

        with use_fake_backend(FakeBackend(responder=respond)):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            assert in_flight.wait(20)
            assert fresh_tm.pause_translation(PROJECT)
            assert fresh_tm.resume_translation(PROJECT)
            release.set()
            task._coordinator.join(timeout=20)
        assert task.state == 'completed'
        assert not [e for e in task.progress.get_all() if e['event'] == 'rate_limit']
        assert _statuses(path) == [SegmentStatus.TRANSLATED]


class TestRound1HomeListing:
    """Item H: the listing shows a running task's project from memory."""

    def test_running_task_in_memory(self, tmp_path, fresh_tm, clock):
        from src.web.server import create_app
        _make_project(tmp_path / 'projects', n=2)
        gate = threading.Event()

        def respond(request):
            if '>Line 2.<' in request.messages[0].content:
                assert gate.wait(20)
            return like(request, '譯')

        client = create_app(projects_dir=str(tmp_path / 'projects')).test_client()
        with use_fake_backend(FakeBackend(responder=respond)):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            _wait(lambda: task.completed == 1)
            listed = client.get('/api/projects').get_json()
            gate.set()
            task._coordinator.join(timeout=20)
        entry = listed[0] if isinstance(listed, list) else listed['projects'][0]
        assert entry['translated'] == 1


class TestRound1Survivors:
    """Mutants left by the round 1 mutation run."""

    def test_wait_slices(self, monkeypatch):
        import src.core.translator as translator_module

        class Time:
            now = 0.0

            def monotonic(self):
                return self.now

        fake_time = Time()

        class Cancel:
            waits = []

            def wait(self, seconds):
                self.waits.append(seconds)
                fake_time.now += seconds
                return False

        monkeypatch.setattr(translator_module, 'time', fake_time)
        monkeypatch.setattr(translator_module, 'WAIT_SLICE_SECONDS', 1)
        cancel = Cancel()
        assert wait_or_cancel(3, cancel) is False
        assert cancel.waits == [1, 1, 1]
        cancel.waits.clear()
        assert wait_or_cancel(2.5, cancel) is False
        assert cancel.waits == [1, 1, 0.5]

    def test_plain_text_before_stderr(self):
        result = ProcessResult(1, 'plain', 'err')
        assert str(CopilotCLI().classify_error(result)) == \
            'copilot CLI exited with code 1: plain | err'

    def test_pause_event_data(self, tmp_path, fresh_tm, clock):
        task = tasks_module.TranslationTask('t', PROJECT, str(tmp_path), state='running',
                                            total=5)
        task.completed = 2
        fresh_tm._pause_for(task, RateLimitError('limit', retry_after=30), 'rate_limit')
        assert task.progress.get_all()[-1] == {'event': 'rate_limit', 'data': {
            'error': 'limit', 'completed': 2, 'remaining': 3, 'resume_in': 30.0}}

    def test_tick_passes_the_due_deadline(self, tmp_path, fresh_tm, clock, monkeypatch):
        task = tasks_module.TranslationTask('t', PROJECT, str(tmp_path), state='paused')
        task.resume_at = clock.now - 1
        seen = []
        monkeypatch.setattr(fresh_tm, 'resume_translation',
                            lambda name, due=None, **kw: seen.append(due) or False)

        class Saver:
            def flush(self):
                pass

        fresh_tm._tick(task, Saver())
        assert seen == [clock.now - 1] and task.ticks == 1

    def _paused_task(self, tmp_path, fresh_tm, workers):
        task = tasks_module.TranslationTask('t', PROJECT, str(tmp_path), state='paused',
                                            num_workers=workers)
        task.pm, task._gm = object(), object()
        return task

    def test_top_up(self, tmp_path, fresh_tm, caplog, monkeypatch):
        import logging
        caplog.set_level(logging.INFO, logger='src.web.tasks')
        release = threading.Event()
        monkeypatch.setattr(tasks_module.TaskManager, '_worker_loop',
                            lambda self, t, wid: release.wait(20))
        task = self._paused_task(tmp_path, fresh_tm, 3)
        busy = threading.Thread(target=release.wait, args=(20,), daemon=True)
        busy.start()
        task._threads.append(busy)  # one counted already
        task.active_workers = 1
        ending = threading.Thread(target=release.wait, args=(20,), daemon=True)
        ending.start()
        task._threads.append(ending)  # alive, but it has ended its loop (round 2, E)
        assert fresh_tm._top_up_workers(task)
        assert len(task._threads) == 4 and task.active_workers == 3
        assert all(t.daemon for t in task._threads[2:])
        assert '動態增加 2 workers (共 3)' in caplog.messages
        caplog.clear()
        assert fresh_tm._top_up_workers(task)  # enough: nothing started, nothing logged
        assert len(task._threads) == 4 and caplog.messages == []
        release.set()
        for t in task._threads:
            t.join()
        for pm, gm in ((None, object()), (object(), None)):
            empty = self._paused_task(tmp_path, fresh_tm, 2)
            empty.pm, empty._gm = pm, gm
            assert fresh_tm._top_up_workers(empty) and empty._threads == []

    def test_workers_done_when_a_worker_cannot_start(self, tmp_path, fresh_tm, monkeypatch):
        task = self._paused_task(tmp_path, fresh_tm, 1)
        task.segment_queue.put('seg')

        def refuse(self):
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(threading.Thread, 'start', refuse)
        with pytest.raises(RuntimeError) as info:
            fresh_tm._workers_done(task)
        assert str(info.value) == "無法增加 worker：can't start new thread"

    def _worker_task(self, tmp_path, fresh_tm):
        from src.glossary.glossary_manager import GlossaryManager
        path = _make_project(tmp_path / 'projects', n=1)
        task = tasks_module.TranslationTask('t', PROJECT, str(tmp_path / 'projects'),
                                            state='running', total=1)
        task.pm = ProjectManager(path)
        task.pm.load()
        task._gm = GlossaryManager(os.path.join(path, 'glossary.json'))
        task.active_workers = 1
        task.segment_queue.put(task.pm.data.segments[0])
        return task

    def test_worker_goes_on_after_putting_back_a_segment(self, tmp_path, fresh_tm):
        """Paused after get(): the worker puts it back and waits; resumed, it
        sends it."""
        task = self._worker_task(tmp_path, fresh_tm)
        task.segment_queue = _HookQueue(task.segment_queue, task.pause_event.clear)
        fake = FakeBackend(responder=lambda r: like(r, '譯'))
        with use_fake_backend(fake):
            worker = threading.Thread(target=fresh_tm._worker_loop, args=(task, 0), daemon=True)
            worker.start()
            try:
                _wait(lambda: task.segment_queue.done and task.segment_queue.qsize() == 1)
                assert fake.requests == []
                task.pause_event.set()
                _wait(lambda: task.result_queue.qsize() == 1)
            finally:
                task.stop_event.set()
                task.pause_event.set()
                worker.join(timeout=20)
        assert len(fake.requests) == 1

    def test_worker_goes_on_after_a_cancelled_call(self, tmp_path, fresh_tm):
        task = self._worker_task(tmp_path, fresh_tm)
        calls = []

        def respond(request):
            calls.append(1)
            if len(calls) == 1:
                task.pause_event.clear()
                raise CancelledError('paused')
            return like(request, '譯')

        with use_fake_backend(FakeBackend(responder=respond)):
            worker = threading.Thread(target=fresh_tm._worker_loop, args=(task, 0), daemon=True)
            worker.start()
            try:
                _wait(lambda: calls and task.segment_queue.qsize() == 1)
                task.pause_event.set()
                _wait(lambda: task.result_queue.qsize() == 1)
            finally:
                task.stop_event.set()
                task.pause_event.set()
                worker.join(timeout=20)
        assert len(calls) == 2


# --- review round 2 ----------------------------------------------------------------

class _LimitedSleeper(SleeperBackend):
    """Line 1's first call hits a rate limit once another call's child runs;
    every other call is a real sleeping child."""

    def __init__(self, pid_dir):
        super().__init__(pid_dir)
        self.limited = False

    def complete(self, request, cancel):
        if '>Line 1.<' in request.messages[0].content and not self.limited:
            self.limited = True
            _wait(lambda: self.pids())
            raise RateLimitError('limit', retry_after=30)
        return super().complete(request, cancel)


class TestRound2RunLevelPauseCancels:
    """Item 1: a pause for a run-level error ends the other calls in
    flight; after resume, pause and stop reach every call."""

    def test_real_children(self, tmp_path, fresh_tm, clock, monkeypatch):
        monkeypatch.setattr('src.llm.cli.run_process', real_run_process)
        pid_dir = tmp_path / 'pids'
        pid_dir.mkdir()
        backend = _LimitedSleeper(str(pid_dir))
        path = _make_project(tmp_path / 'projects', n=2)
        with use_fake_backend(backend):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'),
                                              num_workers=2)
            _wait(lambda: task.state == 'paused' and task.resume_at is not None)
            assert _gone(backend.pids()[0])  # the other worker's child
            _wait(lambda: task.segment_queue.qsize() == 2)
            assert fresh_tm.resume_translation(PROJECT)
            _wait(lambda: len(backend.pids()) == 3)
            assert fresh_tm.pause_translation(PROJECT)
            assert all(_gone(p) for p in backend.pids())
            assert fresh_tm.resume_translation(PROJECT)
            _wait(lambda: len(backend.pids()) == 5)
            fresh_tm.stop_translation(PROJECT)
            task._coordinator.join(timeout=20)
            assert not task._coordinator.is_alive()  # stop did not hang
        assert all(_gone(p) for p in backend.pids())
        assert _statuses(path) == [SegmentStatus.PENDING] * 2


class TestRound2Values:

    def test_huge_retry_after(self):
        error = RateLimitError('x', retry_after=10 ** 400)
        assert resume_delay(error) == 3600.0

    def test_flush_decides_under_the_mutex(self):
        """Item 3: a result added while the mutex is held is saved by a
        flush that waited for the mutex."""
        flusher_waiting = threading.Event()
        main = threading.current_thread()

        class Mutex:
            def __init__(self):
                self.lock = threading.RLock()

            def __enter__(self):
                if threading.current_thread() is not main:
                    flusher_waiting.set()
                self.lock.acquire()

            def __exit__(self, *exc):
                self.lock.release()

        class PM:
            mutex = Mutex()
            saved = 0

            def save(self):
                self.saved += 1

        pm = PM()
        saver = DebouncedSaver(pm)
        result = {}
        flusher = threading.Thread(target=lambda: result.update(saved=saver.flush()))
        with pm.mutex:
            flusher.start()
            _wait(lambda: flusher_waiting.is_set() or not flusher.is_alive())
            saver.add('applied')
        flusher.join(timeout=20)
        assert result == {'saved': True} and pm.saved == 1 and saver.pending == []


class TestRound2Events:

    def test_extension_sends_the_new_wait(self, tmp_path, fresh_tm, clock):
        """Item 5."""
        task = tasks_module.TranslationTask('t', PROJECT, str(tmp_path), state='paused',
                                            total=4)
        task.resume_at = clock.now + 10
        task.error = 'first'
        task.completed = 1
        fresh_tm._pause_for(task, RateLimitError('later', retry_after=60), 'rate_limit')
        assert task.progress.get_all()[-1] == {'event': 'rate_limit', 'data': {
            'error': 'first', 'completed': 1, 'remaining': 3, 'resume_in': 60.0}}

    def test_stream_snapshot_has_the_wait(self, tmp_path, fresh_tm, clock):
        """Item 4: a rate limit before the stream opened."""
        from src.web.server import create_app
        _make_project(tmp_path / 'projects', n=1)
        task = tasks_module.TranslationTask('t', PROJECT, str(tmp_path / 'projects'),
                                            state='paused', total=1, error='limit')
        task.resume_at = clock.now + 42
        fresh_tm.tasks[PROJECT] = task
        client = create_app(projects_dir=str(tmp_path / 'projects')).test_client()
        response = client.get(f'/api/projects/{PROJECT}/translate/stream', buffered=False)
        first = next(iter(response.response)).decode('utf-8')
        response.close()
        assert json.loads(first.split('data: ', 1)[1]) == {
            'state': 'paused', 'completed': 0, 'total': 1, 'error': 'limit', 'resume_in': 42.0}
        task.resume_at = clock.now - 5  # due: not negative
        response = client.get(f'/api/projects/{PROJECT}/translate/stream', buffered=False)
        first = next(iter(response.response)).decode('utf-8')
        response.close()
        assert json.loads(first.split('data: ', 1)[1])['resume_in'] == 0.0
        task.state = 'completed'
        html = client.get(f'/project/{PROJECT}/translation').get_data(as_text=True)
        assert 'if (data.resume_in != null) {  // a rate limit before this stream opened' in html
        assert 'this.autoResume = true;' in html


def _record_saves(monkeypatch):
    """The number of translated segments in memory at each save."""
    counts = []
    real = ProjectManager.save

    def save(self):
        counts.append(sum(s.status == SegmentStatus.TRANSLATED for s in self.data.segments))
        real(self)

    monkeypatch.setattr(ProjectManager, 'save', save)
    return counts


def _most_unsaved(counts):
    return max(b - a for a, b in zip([0] + counts, counts))


class TestRound2WindowInBatches:
    """Item 6: a batch of more than 20 results is saved every 20."""

    def test_web(self, tmp_path, fresh_tm, clock, monkeypatch):
        path = _make_project(tmp_path / 'projects', n=60)
        counts = _record_saves(monkeypatch)
        real_done = tasks_module.TaskManager._workers_done
        held = []

        def done(self, task):
            if not held:  # the coordinator waits once until a big batch is queued
                held.append(1)
                _wait(lambda: task.result_queue.qsize() >= 45)
            return real_done(self, task)

        monkeypatch.setattr(tasks_module.TaskManager, '_workers_done', done)
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'), num_workers=4)
            task._coordinator.join(timeout=60)
        assert task.state == 'completed'
        assert _most_unsaved(counts) <= SAVE_EVERY_RESULTS
        assert _statuses(path) == [SegmentStatus.TRANSLATED] * 60

    def test_cli(self, tmp_path, clock, monkeypatch):
        import queue
        path = _make_project(tmp_path, n=60)
        counts = _record_saves(monkeypatch)
        made = []

        class Held(queue.Queue):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                made.append(self)
                self.held = False

            def get(self, *a, **kw):
                if self is made[1] and not self.held:  # the result queue
                    self.held = True
                    _wait(lambda: self.qsize() >= 45)
                return super().get(*a, **kw)

        monkeypatch.setattr(queue, 'Queue', Held)
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))):
            result = CliRunner().invoke(translate.cli, ['start', '--project', path,
                                                        '--workers', '4'])
        assert result.exit_code == 0, result.output
        assert _most_unsaved(counts) <= SAVE_EVERY_RESULTS
        assert _statuses(path) == [SegmentStatus.TRANSLATED] * 60


class TestRound2CliBackendUnderTheLock:
    """Item 9: the backend and model are checked against the project read
    under the lock."""

    def test_switch_before_the_lock(self, tmp_path, monkeypatch):
        path = _make_project(tmp_path, n=1)
        pm = ProjectManager(path)
        pm.load()
        pm.mark_translated('c1_seg0001', '<p>譯</p>')
        pm.save()
        real_acquire = translate.ProjectLock.acquire

        switched = []

        def acquire(self):
            if not switched:  # the web switched meanwhile (its save takes the lock too)
                switched.append(1)
                other = ProjectManager(path)
                other.load()
                other.set_model('gpt-6.2-sol', backend='copilot-cli')
                other.save()
            return real_acquire(self)

        monkeypatch.setattr(translate.ProjectLock, 'acquire', acquire)
        result = CliRunner().invoke(translate.cli, ['start', '--project', path,
                                                    '-b', 'copilot-cli'])
        assert result.exit_code == 0, result.output
        pm.load()
        assert (pm.data.backend, pm.data.model) == ('copilot-cli', 'gpt-6.2-sol')


class TestRound2Minor:
    """The third reviewer's items A-F."""

    def test_cancelled_rejected_reply_is_not_a_failure(self, tmp_path):
        """A: a check that rejects the reply after cancel: CancelledError."""
        path = _make_project(tmp_path, n=1)
        pm = ProjectManager(path)
        pm.load()
        seg = pm.data.segments[0]
        fake = FakeBackend(responder=lambda r: 'no markup at all')
        engine = TranslationEngine(model='m', backend=fake)
        outcome = translate_segment(engine, seg, pm.data.segments, None)
        assert outcome.status == SegmentStatus.FAILED
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(CancelledError):
            translate_segment(engine, seg, pm.data.segments, None, cancel=cancel)
        FakeBackend.overcalls.clear()

    def test_stopping_task_does_not_resume(self, tmp_path, fresh_tm, clock):
        """B (web)."""
        task = tasks_module.TranslationTask('t', PROJECT, str(tmp_path), state='paused')
        alive = threading.Event()
        keeper = threading.Thread(target=alive.wait, daemon=True)
        keeper.start()
        task._coordinator = keeper
        fresh_tm.tasks[PROJECT] = task
        task.resume_at = clock.now
        task.stop_event.set()  # as the failure streak does
        assert not fresh_tm.resume_translation(PROJECT, due=clock.now)
        assert not fresh_tm.resume_translation(PROJECT)
        assert task.state == 'paused'
        task.stop_event.clear()
        fresh_tm.stop_translation(PROJECT)
        assert task.resume_at is None and task.state == 'stopped'
        alive.set()
        keeper.join()

    def test_cli_stopping_run_does_not_reopen_the_gate(self, tmp_path, monkeypatch):
        """B (CLI): once the run stops, the tick neither announces nor
        reopens, also when the deadline has passed."""
        import src.core.runcontrol as runcontrol
        clock = _CountingClock()
        monkeypatch.setattr(translate, '_clock', clock)
        path = _make_project(tmp_path, n=2)
        flushes = []
        real_flush = runcontrol.DebouncedSaver.flush
        monkeypatch.setattr(runcontrol.DebouncedSaver, 'flush',
                            lambda self, force=False: flushes.append(1) or real_flush(self, force))
        announced = threading.Event()
        real_message = translate._rate_limit_message
        monkeypatch.setattr(translate, '_rate_limit_message',
                            lambda e, d: (announced.set(), real_message(e, d))[1])
        real_event = threading.Event
        stop_events = []

        class Event(real_event):
            def __init__(self):
                super().__init__()
                if sys._getframe(1).f_code.co_name == '_start_parallel' and not stop_events:
                    stop_events.append(self)  # the first one made there: error_event

            def set(self):
                super().set()
                if stop_events and self is stop_events[0]:
                    clock.now += 100  # the deadline has passed
                    start = len(flushes)
                    _wait(lambda: len(flushes) >= start + 2)  # two ticks after the stop

        monkeypatch.setattr(threading, 'Event', Event)
        both = threading.Barrier(2)

        def respond(request):
            both.wait(20)
            if '>Line 1.<' in request.messages[0].content:
                raise RateLimitError('limit', retry_after=7)
            assert announced.wait(20)
            raise AuthError('expired')

        with use_fake_backend(FakeBackend(responder=respond)):
            result = CliRunner().invoke(translate.cli, ['start', '--project', path,
                                                        '--workers', '2'])
        assert result.exit_code == 1, result.output
        assert '  繼續翻譯\n' not in result.output

    def test_worker_goes_on_after_a_resume_and_a_new_pause(self, tmp_path, fresh_tm):
        """D: woken after a run-level error and finding the task paused
        again, the worker does not exit."""
        survivors = TestRound1Survivors()
        task = survivors._worker_task(tmp_path, fresh_tm)
        calls = []

        def respond(request):
            calls.append(1)
            if len(calls) == 1:
                raise AuthError('expired')
            return like(request, '譯')

        with use_fake_backend(FakeBackend(responder=respond)):
            worker = threading.Thread(target=fresh_tm._worker_loop, args=(task, 0), daemon=True)
            worker.start()
            try:
                _wait(lambda: task.state == 'paused' and task.segment_queue.qsize() == 1)
                task.state = 'paused'  # resumed and paused again before it woke
                task.pause_event.set()
                _wait(lambda: task.result_queue.qsize() == 1)
            finally:
                task.stop_event.set()
                task.pause_event.set()
                worker.join(timeout=20)
        assert len(calls) == 2

    def test_no_failure_log_for_a_segment_translated_again(self, tmp_path, fresh_tm, clock):
        """F: failed in the run, translated again (as from the review page)
        before the save: no diagnostics are written for it."""
        from src.core.translator import TranslationOutcome
        path = _make_project(tmp_path / 'projects', n=2)
        gate = threading.Event()

        def respond(request):
            if '>Line 2.<' in request.messages[0].content:
                assert gate.wait(20)
                return like(request, '譯')
            raise LLMError('bad request')

        with use_fake_backend(FakeBackend(responder=respond)):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path / 'projects'))
            _wait(lambda: task.pm.data.segments[0].status == SegmentStatus.FAILED)
            pm = task.pm
            with pm.mutex:
                seg = pm.data.segments[0]
                assert pm.apply_outcome(seg.segment_id, TranslationOutcome(
                    '<p>譯</p>', SegmentStatus.TRANSLATED, [], 'm', None),
                    revision=seg.revision)
            gate.set()
            task._coordinator.join(timeout=20)
        failed = os.path.join(path, 'log', 'failed')
        assert not os.path.isdir(failed) or os.listdir(failed) == []

    def test_page_resets_the_wait(self, tmp_path):
        """C: a new run, stopped, complete and a failed run end any wait."""
        from src.web.server import create_app
        _make_project(tmp_path / 'projects', n=1)
        client = create_app(projects_dir=str(tmp_path / 'projects')).test_client()
        html = client.get(f'/project/{PROJECT}/translation').get_data(as_text=True)
        assert 'this.autoResume = false;  // a new run: no wait' in html
        assert "this.state = 'stopped';\n                this.autoResume = false;" in html
        assert "this.state = 'completed';\n                this.autoResume = false;" in html
        assert ('if (e.data) {  // the run failed (not a dropped connection)\n'
                '                    this.autoResume = false;') in html
