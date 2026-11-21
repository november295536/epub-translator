"""Tests for the LLM backend interface (src/llm)."""

import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.llm import (
    AuthError, CancelledError, LLMError, LLMRequest, LLMResponse, Message,
    RateLimitError, TransientError, Usage, default_backend,
)
from src.llm.claude_cli import LEAN_FLAGS, ClaudeCLI
from src.llm.cli import CLIBackend, ProcessResult, flatten_prompt, run_process
from src.llm.fake import FakeBackend


def _request(prompt='Translate this.', model='haiku', timeout=30, system=''):
    return LLMRequest(system=system, messages=[Message('user', prompt)],
                      model=model, timeout=timeout)


def _result(returncode=0, stdout='', stderr=''):
    return ProcessResult(returncode, stdout, stderr)


class TestClaudeCLIClassification:
    """classify_error: the failure kinds the old engine detected, and more."""

    @pytest.mark.parametrize('result', [
        _result(0, "You've hit your limit · resets 8pm"),
        _result(1, '', "You've hit your limit · resets 8pm"),
        _result(0, "you've HIT your LIMIT"),
    ])
    def test_rate_limit(self, result):
        error = ClaudeCLI().classify_error(result)
        assert isinstance(error, RateLimitError)
        assert error.retry_after is None
        assert "hit your limit" in str(error).lower()

    @pytest.mark.parametrize('text', [
        'API Error: 401 authentication_error',
        'OAuth token has expired',
        'Invalid API key · Please run /login',
    ])
    @pytest.mark.parametrize('returncode', [0, 1])
    def test_auth(self, text, returncode):
        error = ClaudeCLI().classify_error(_result(returncode, '', text))
        assert isinstance(error, AuthError)
        assert 'claude /login' in str(error)

    def test_rate_limit_wins_over_auth(self):
        error = ClaudeCLI().classify_error(
            _result(1, "You've hit your limit", 'please run /login'))
        assert isinstance(error, RateLimitError)

    def test_non_zero_exit_is_transient(self):
        error = ClaudeCLI().classify_error(_result(2, 'partial', ' boom \n'))
        assert type(error) is TransientError
        assert str(error) == 'claude CLI exited with code 2: boom'

    def test_success(self):
        assert ClaudeCLI().classify_error(_result(0, '譯文', 'a warning')) is None

    def test_non_string_streams_are_ignored(self):
        result = MagicMock(returncode=0, stdout='ok')  # stderr is a MagicMock
        assert ClaudeCLI().classify_error(result) is None


class TestClaudeCLICommand:

    def test_command(self):
        cmd = ClaudeCLI().build_command('PROMPT', _request(model='opus'))
        # Phase 7: the measured lean flags follow (phase7_cli_overhead.py)
        assert cmd == ['claude', '-p', 'PROMPT', '--model', 'opus',
                       '--system-prompt', '你是一位專業的英文翻譯。', '--tools', '',
                       '--strict-mcp-config', '--setting-sources', '',
                       '--no-session-persistence']

    def test_command_without_model(self):
        assert ClaudeCLI().build_command('PROMPT', _request(model='')) == \
            ['claude', '-p', 'PROMPT'] + list(LEAN_FLAGS)

    def test_parse_output(self):
        response = ClaudeCLI().parse_output(_result(0, '  譯文\n'), _request(model='haiku'))
        assert response == LLMResponse(text='  譯文\n', stop_reason='complete',
                                       usage=None, model='haiku')

    def test_list_models(self):
        assert [m.alias for m in ClaudeCLI().list_models()] == ['sonnet', 'opus', 'haiku']

    def test_capabilities(self):
        caps = ClaudeCLI().capabilities
        assert not caps.separate_system and not caps.multi_turn
        assert not caps.reports_usage

    def test_default_backend_is_claude_cli(self):
        assert isinstance(default_backend(), ClaudeCLI)


class TestCLIBackendComplete:
    """CLIBackend.complete through the run_process seam."""

    def test_environment_and_working_directory(self, monkeypatch):
        import tempfile
        monkeypatch.setenv('CLAUDECODE', '1')
        monkeypatch.setenv('KEEP_ME', 'yes')
        with patch('src.llm.cli.run_process', return_value=_result(0, 'ok')) as run:
            ClaudeCLI().complete(_request(timeout=7), threading.Event())
        kwargs = run.call_args[1]
        assert 'CLAUDECODE' not in kwargs['env']
        assert kwargs['env']['KEEP_ME'] == 'yes'
        assert kwargs['cwd'] == tempfile.gettempdir()
        assert kwargs['timeout'] == 7

    def test_cancel_event_passed_through(self):
        cancel = threading.Event()
        with patch('src.llm.cli.run_process', return_value=_result(0, 'ok')) as run:
            ClaudeCLI().complete(_request(), cancel)
        assert run.call_args[1]['cancel'] is cancel

    def test_error_carries_raw_output(self):
        with patch('src.llm.cli.run_process', return_value=_result(3, 'OUT', 'ERR')):
            with pytest.raises(TransientError) as info:
                ClaudeCLI().complete(_request(), threading.Event())
        assert info.value.raw == 'OUTERR'

    def test_timeout_is_transient(self):
        import subprocess
        with patch('src.llm.cli.run_process',
                   side_effect=subprocess.TimeoutExpired('claude', 5)):
            with pytest.raises(TransientError, match='timed out after 5 s'):
                ClaudeCLI().complete(_request(timeout=5), threading.Event())

    def test_missing_program_is_not_transient(self):
        from src.llm import BackendUnavailableError
        with patch('src.llm.cli.run_process', side_effect=FileNotFoundError('claude')):
            with pytest.raises(LLMError) as info:
                ClaudeCLI().complete(_request(), threading.Event())
        # Review round 1, item 15: a run-level error, not a per-segment one
        assert type(info.value) is BackendUnavailableError
        assert 'could not be started' in str(info.value)

    def test_base_class_methods_must_be_supplied(self):
        backend = CLIBackend()
        with pytest.raises(NotImplementedError):
            backend.build_command('p', _request())
        with pytest.raises(NotImplementedError):
            backend.parse_output(_result(), _request())
        with pytest.raises(NotImplementedError):
            backend.classify_error(_result())
        with pytest.raises(NotImplementedError):
            backend.list_models()


class TestFlattenPrompt:

    def test_single_message_unchanged(self):
        assert flatten_prompt(_request('just this')) == 'just this'

    def test_system_and_turns(self):
        request = LLMRequest(system='RULES', model='m', timeout=1, messages=[
            Message('user', 'Q1'), Message('assistant', 'A1'), Message('user', 'Q2')])
        assert flatten_prompt(request) == \
            'RULES\n\n[user]\nQ1\n\n[assistant]\nA1\n\n[user]\nQ2'

    def test_system_with_one_message(self):
        assert flatten_prompt(_request('Q', system='S')) == 'S\n\nQ'


class TestRunProcess:
    """run_process with real (non-model) child processes."""

    def _env(self):
        return dict(os.environ)

    def test_captures_output_and_exit_code(self, tmp_path):
        cmd = [sys.executable, '-c',
               'import os,sys; print(os.getcwd()); print("err", file=sys.stderr); sys.exit(3)']
        result = run_process(cmd, timeout=30, cwd=str(tmp_path), env=self._env())
        assert result.returncode == 3
        assert os.path.realpath(result.stdout.strip()) == os.path.realpath(str(tmp_path))
        assert result.stderr == 'err\n'

    def test_stdin_is_empty(self, tmp_path):
        cmd = [sys.executable, '-c', 'import sys; print(repr(sys.stdin.read()))']
        result = run_process(cmd, timeout=30, cwd=str(tmp_path), env=self._env())
        assert result.stdout.strip() == "''"

    def test_cancel_stops_in_flight_call(self, tmp_path):
        pid_file = tmp_path / 'pid'
        cmd = [sys.executable, '-c',
               f'import os,time; open({str(pid_file)!r},"w").write(str(os.getpid())); '
               'time.sleep(60)']
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        started = time.monotonic()
        with pytest.raises(CancelledError):
            run_process(cmd, timeout=60, cwd=str(tmp_path), env=self._env(), cancel=cancel)
        assert time.monotonic() - started < 5
        pid = int(pid_file.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    def test_cancel_kills_grandchildren(self, tmp_path):
        pid_file = tmp_path / 'pid'
        child = (f'import os,time; open({str(pid_file)!r},"w").write(str(os.getpid())); '
                 'time.sleep(60)')
        cmd = [sys.executable, '-c',
               f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]); '
               'time.sleep(60)']
        cancel = threading.Event()
        threading.Timer(1.0, cancel.set).start()
        with pytest.raises(CancelledError):
            run_process(cmd, timeout=60, cwd=str(tmp_path), env=self._env(), cancel=cancel)
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail('grandchild still running')

    def test_timeout(self, tmp_path):
        import subprocess
        cmd = [sys.executable, '-c', 'import time; time.sleep(60)']
        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            run_process(cmd, timeout=0.5, cwd=str(tmp_path), env=self._env())
        assert time.monotonic() - started < 5

    def test_cancel_before_start_of_wait(self, tmp_path):
        cancel = threading.Event()
        cancel.set()
        cmd = [sys.executable, '-c', 'import time; time.sleep(60)']
        with pytest.raises(CancelledError):
            run_process(cmd, timeout=60, cwd=str(tmp_path), env=self._env(), cancel=cancel)


class TestFakeBackend:

    def test_scripted_replies_and_errors(self):
        fake = FakeBackend(['one', RateLimitError('limit'),
                            LLMResponse('two', 'complete', Usage(3, 4), 'm')])
        assert fake.complete(_request('a'), threading.Event()).text == 'one'
        with pytest.raises(RateLimitError):
            fake.complete(_request('b'), threading.Event())
        response = fake.complete(_request('c'), threading.Event())
        assert response.usage == Usage(3, 4)
        assert fake.prompts == ['a', 'b', 'c']
        from src.llm.fake import FakeBackendExhausted
        with pytest.raises(FakeBackendExhausted):
            fake.complete(_request('d'), threading.Event())
        FakeBackend.overcalls.clear()  # intended

    def test_responder(self):
        fake = FakeBackend(responder=lambda request: request.model.upper())
        response = fake.complete(_request(model='haiku'), threading.Event())
        assert response.text == 'HAIKU'
        assert response.model == 'haiku'
        assert response.usage is None


class TestInterfaceDetails:
    """Exact values of the interface types and backends (mutation survivors)."""

    def test_request_hints_default_to_none(self):
        request = _request()
        assert (request.temperature, request.max_output_tokens, request.effort) == \
            (None, None, None)

    def test_error_fields(self):
        assert LLMError('m').raw == ''
        assert str(LLMError('m', raw='r')) == 'm'
        error = RateLimitError('limit', raw='r', retry_after=5.0)
        assert (str(error), error.raw, error.retry_after) == ('limit', 'r', 5.0)
        assert RateLimitError('limit').raw == ''

    def test_claude_cli_identity(self):
        from src.llm import Capabilities, ModelInfo
        backend = ClaudeCLI()
        assert backend.name == 'claude-cli'
        assert backend.capabilities == Capabilities(
            separate_system=False, multi_turn=False, reports_usage=False,
            sampling_params=False, suggested_concurrency=1)
        assert backend.list_models() == [
            ModelInfo('sonnet', 'Sonnet', '快速且高品質，適合大多數翻譯'),
            ModelInfo('opus', 'Opus', '最高品質，費用較高'),
            ModelInfo('haiku', 'Haiku', '最快速度，適合簡單內容'),
        ]

    def test_auth_message(self):
        error = ClaudeCLI().classify_error(_result(1, '', 'OAuth token has expired'))
        assert str(error) == 'Claude CLI 認證已過期，請在終端執行 claude /login 重新登入'

    def test_non_string_stderr_in_message(self):
        error = ClaudeCLI().classify_error(ProcessResult(1, None, None))
        assert str(error) == 'claude CLI exited with code 1: '

    def test_base_environment_keeps_everything(self, monkeypatch):
        monkeypatch.setenv('CLAUDECODE', '1')
        assert CLIBackend().environment() == dict(os.environ)

    def test_messages(self):
        import subprocess
        with patch('src.llm.cli.run_process', side_effect=subprocess.TimeoutExpired('c', 2)):
            with pytest.raises(TransientError) as info:
                ClaudeCLI().complete(_request(timeout=2.5), threading.Event())
        assert str(info.value) == 'claude-cli timed out after 2.5 s'
        with patch('src.llm.cli.run_process', side_effect=FileNotFoundError('nope')):
            with pytest.raises(LLMError) as info:
                ClaudeCLI().complete(_request(), threading.Event())
        assert str(info.value) == 'claude-cli could not be started: nope'

    def test_cancel_message(self, tmp_path):
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(CancelledError) as info:
            run_process([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=30,
                        cwd=str(tmp_path), env=dict(os.environ), cancel=cancel)
        assert str(info.value) == 'cancelled'

    def test_fake_identity(self):
        from src.llm import Capabilities, ModelInfo
        fake = FakeBackend()
        assert fake.name == 'fake'
        assert fake.capabilities == Capabilities(
            separate_system=True, multi_turn=True, reports_usage=True,
            sampling_params=True, suggested_concurrency=4)
        assert fake.list_models() == [ModelInfo('fake-model', 'Fake', '')]
        from src.llm.fake import FakeBackendExhausted
        with pytest.raises(FakeBackendExhausted, match='^FakeBackend: no scripted reply left$'):
            fake.complete(_request(), threading.Event())
        FakeBackend.overcalls.clear()  # intended

    def test_fake_string_reply_is_complete(self):
        response = FakeBackend(['x']).complete(_request(model='m'), threading.Event())
        assert response == LLMResponse(text='x', stop_reason='complete', usage=None, model='m')


# --- Review round 1 ---------------------------------------------------------------

import subprocess as _subprocess

import src.llm.cli as cli_module
from src.llm import BackendUnavailableError


def _sleeper(pid_file, seconds=60, before=''):
    """A child that records its PID, runs `before`, then sleeps."""
    return [sys.executable, '-c',
            f'import os,sys,time; open({str(pid_file)!r},"w").write(str(os.getpid())); '
            f'{before}time.sleep({seconds})']


def _gone(pid, wait=5):
    """True when pid is no process (killed and reaped) within `wait` s."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


class TestRunProcessExceptionalExit:
    """Item 8: any exception after the start kills and reaps the process."""

    def test_keyboard_interrupt_kills_child(self, tmp_path):
        pid_file = tmp_path / 'pid'

        class InterruptOnceStarted:
            def is_set(self):
                if pid_file.exists() and pid_file.read_text():
                    raise KeyboardInterrupt
                return False

        with pytest.raises(KeyboardInterrupt):
            run_process(_sleeper(pid_file), timeout=60, cwd=str(tmp_path),
                        env=dict(os.environ), cancel=InterruptOnceStarted())
        pid = int(pid_file.read_text())
        with pytest.raises(ProcessLookupError):  # killed and reaped, not a zombie
            os.kill(pid, 0)
        assert cli_module._live_groups == set()

    @pytest.mark.parametrize('ending', ['cancel', 'timeout'])
    def test_kill_wait_is_bounded_when_a_descendant_keeps_the_pipes(self, tmp_path, ending,
                                                                    monkeypatch):
        made = []

        class RecordingPopen(_subprocess.Popen):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                made.append(self)

        monkeypatch.setattr(cli_module.subprocess, 'Popen', RecordingPopen)
        pid_file = tmp_path / 'pid'
        # The grandchild leaves the process group (setsid) and inherits the pipes
        grandchild = (f'import os,time; os.setsid(); '
                      f'open({str(pid_file)!r},"w").write(str(os.getpid())); time.sleep(60)')
        cmd = [sys.executable, '-c',
               'import subprocess,sys,time; print("partial", flush=True); '
               f'subprocess.Popen([sys.executable,"-c",{grandchild!r}]); time.sleep(60)']
        cancel = threading.Event()
        outcome = []

        def call():
            try:
                run_process(cmd, timeout=60 if ending == 'cancel' else 1, cwd=str(tmp_path),
                            env=dict(os.environ), cancel=cancel)
            except CancelledError:
                outcome.append('cancelled')
            except _subprocess.TimeoutExpired as e:
                outcome.append((e.output, e.stderr))

        t = threading.Thread(target=call)
        t.start()
        deadline = time.monotonic() + 10
        while not (pid_file.exists() and pid_file.read_text()):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        if ending == 'cancel':
            cancel.set()
        t.join(timeout=20)  # the default KILL_WAIT_SECONDS, not the grandchild's 60 s
        os.kill(int(pid_file.read_text()), 9)
        assert not t.is_alive()
        # The pipes are given up; what was written before is kept (review round 2)
        assert outcome == (['cancelled'] if ending == 'cancel' else [('partial\n', '')])
        assert made[0].stdout.closed and made[0].stderr.closed and made[0].returncode is not None

    def test_atexit_hook_kills_live_groups(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli_module, '_shutting_down', False)  # restored afterwards
        pid_file = tmp_path / 'pid'
        errors = []

        def call():
            try:
                run_process(_sleeper(pid_file), timeout=60, cwd=str(tmp_path),
                            env=dict(os.environ))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t = threading.Thread(target=call)
        t.start()
        deadline = time.monotonic() + 10
        while not (pid_file.exists() and pid_file.read_text()):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert len(cli_module._live_groups) == 1
        cli_module._kill_live_groups()
        t.join(timeout=10)
        assert not t.is_alive()
        assert _gone(int(pid_file.read_text()))
        assert cli_module._live_groups == set()

    def test_children_killed_when_interpreter_exits(self, tmp_path):
        """A call left running in a daemon thread is killed by the exit hook."""
        pid_file = tmp_path / 'pid'
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = (
            'import os, sys, threading, time\n'
            f'sys.path.insert(0, {repo!r})\n'
            'from src.llm.cli import run_process\n'
            f'cmd = {_sleeper(pid_file)!r}\n'
            'threading.Thread(target=run_process, args=(cmd,), daemon=True,\n'
            '                 kwargs=dict(timeout=60, cwd=".", env=dict(os.environ))).start()\n'
            f'while not os.path.exists({str(pid_file)!r}) or not open({str(pid_file)!r}).read():\n'
            '    time.sleep(0.05)\n'
        )
        _subprocess.run([sys.executable, '-c', script], cwd=str(tmp_path), timeout=30,
                        check=True)
        assert _gone(int(pid_file.read_text()))


class TestRunProcessCancelAndDeadline:
    """Item 10: cancel before start; the deadline bounds every wait."""

    def test_cancelled_before_start_starts_nothing(self, tmp_path):
        cancel = threading.Event()
        cancel.set()
        with patch('src.llm.cli.subprocess.Popen', side_effect=AssertionError('started')):
            with pytest.raises(CancelledError):
                run_process(['true'], timeout=30, cwd=str(tmp_path), env=dict(os.environ),
                            cancel=cancel)

    def test_deadline_not_stretched_by_poll_interval(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli_module, 'POLL_SECONDS', 5)
        cmd = [sys.executable, '-c', 'import time; time.sleep(1.5); print("late")']
        with pytest.raises(_subprocess.TimeoutExpired):
            run_process(cmd, timeout=0.5, cwd=str(tmp_path), env=dict(os.environ))

    def test_output_of_call_cancelled_meanwhile_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli_module, 'POLL_SECONDS', 5)
        cmd = [sys.executable, '-c', 'import time; time.sleep(1); print("done")']
        cancel = threading.Event()
        timer = threading.Timer(0.2, cancel.set)
        timer.start()
        with pytest.raises(CancelledError):
            run_process(cmd, timeout=30, cwd=str(tmp_path), env=dict(os.environ),
                        cancel=cancel)
        timer.join()


class TestTimeoutKeepsOutput:
    """Item 14: output written before a timeout reaches the diagnostics."""

    def test_timeout_expired_carries_output(self, tmp_path, monkeypatch):
        cmd = [sys.executable, '-c',
               'import sys,time; print("partial reply", flush=True); '
               'print("an error line", file=sys.stderr, flush=True); '
               f'open({str(tmp_path / "ready")!r}, "w").close(); time.sleep(60)']
        _deadline_once_written(monkeypatch, tmp_path / 'ready')
        with pytest.raises(_subprocess.TimeoutExpired) as info:
            run_process(cmd, timeout=1, cwd=str(tmp_path), env=dict(os.environ))
        assert 'partial reply' in info.value.output
        assert 'an error line' in info.value.stderr

    def test_transient_error_raw(self):
        expired = _subprocess.TimeoutExpired(['claude'], 5, output='partial reply',
                                             stderr='an error line')
        with patch('src.llm.cli.run_process', side_effect=expired):
            with pytest.raises(TransientError) as info:
                ClaudeCLI().complete(_request(), threading.Event())
        assert info.value.raw == 'partial replyan error line'

    def test_output_kept_is_bounded(self, tmp_path, monkeypatch):
        cmd = [sys.executable, '-c',
               'import sys,time; print("x" * 30000, flush=True); '
               'print("y" * 30000, file=sys.stderr, flush=True); '
               f'open({str(tmp_path / "ready")!r}, "w").close(); time.sleep(60)']
        _deadline_once_written(monkeypatch, tmp_path / 'ready')
        with pytest.raises(_subprocess.TimeoutExpired) as info:
            run_process(cmd, timeout=1, cwd=str(tmp_path), env=dict(os.environ))
        assert info.value.output == 'x' * 20000
        assert info.value.stderr == 'y' * 20000


def _deadline_once_written(monkeypatch, ready):
    """run_process's clock stands still until the child has written its
    output (it then creates `ready`), and is past any deadline after: the
    timeout fires after the output, also on a loaded machine (pre-merge)."""
    import src.llm.cli as cli_module

    class Clock:
        def monotonic(self):
            return 1e9 if os.path.exists(ready) else 0.0

    monkeypatch.setattr(cli_module, 'time', Clock())


class TestBackendUnavailable:
    """Item 15: a program that cannot start is a run-level error."""

    @pytest.mark.parametrize('error', [FileNotFoundError('claude'), PermissionError('denied')])
    def test_start_failure(self, error):
        with patch('src.llm.cli.run_process', side_effect=error):
            with pytest.raises(BackendUnavailableError):
                ClaudeCLI().complete(_request(), threading.Event())


class TestClassificationOfReplies:
    """Item 19: ordinary text that quotes an error phrase is not an error."""

    LONG = '譯文' * 200

    @pytest.mark.parametrize('phrase', ['authentication_error', 'please run /login',
                                        "You've hit your limit"])
    def test_long_successful_reply_quoting_a_phrase(self, phrase):
        result = _result(0, f'{self.LONG} {phrase} {self.LONG}')
        assert ClaudeCLI().classify_error(result) is None

    def test_short_successful_reply_still_detected(self):
        assert isinstance(ClaudeCLI().classify_error(_result(0, 'Please run /login')),
                          AuthError)

    def test_stderr_and_failed_runs_always_searched(self):
        assert isinstance(ClaudeCLI().classify_error(
            _result(0, self.LONG, 'OAuth token has expired')), AuthError)
        assert isinstance(ClaudeCLI().classify_error(
            _result(1, self.LONG + "You've hit your limit")), RateLimitError)

    def test_error_messages_bounded(self):
        error = ClaudeCLI().classify_error(_result(1, '', 'e' * 100000))
        assert isinstance(error, TransientError)
        assert len(str(error)) < 1000
        error = ClaudeCLI().classify_error(_result(1, '', "You've hit your limit" + 'x' * 100000))
        assert len(str(error)) <= 500


class TestBackendDefaults:
    """Item 12: backends name their default model and auth hint."""

    def test_claude(self):
        backend = ClaudeCLI()
        assert backend.default_model in [m.alias for m in backend.list_models()]
        assert 'claude /login' in backend.auth_hint

    def test_fake(self):
        backend = FakeBackend()
        assert backend.default_model == 'fake-model'
        assert [m.alias for m in backend.list_models()] == ['fake-model']


class TestRound1Exact:
    """Exact values of the round 1 backend code (mutation survivors)."""

    def test_short_reply_boundary(self):
        phrase = 'please run /login'
        at_limit = phrase + 'x' * (300 - len(phrase))
        assert isinstance(ClaudeCLI().classify_error(_result(0, at_limit)), AuthError)
        assert ClaudeCLI().classify_error(_result(0, at_limit + 'x')) is None

    def test_cancel_messages(self, tmp_path):
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(CancelledError) as info:
            run_process(['true'], timeout=30, cwd=str(tmp_path), env=dict(os.environ),
                        cancel=cancel)
        assert str(info.value) == 'cancelled'

    def test_cancel_message_after_finish(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli_module, 'POLL_SECONDS', 5)
        cancel = threading.Event()
        timer = threading.Timer(0.2, cancel.set)
        timer.start()
        with pytest.raises(CancelledError) as info:
            run_process([sys.executable, '-c', 'import time; time.sleep(1)'], timeout=30,
                        cwd=str(tmp_path), env=dict(os.environ), cancel=cancel)
        timer.join()
        assert str(info.value) == 'cancelled'

    def test_cancel_message_while_running(self, tmp_path):
        cancel = threading.Event()
        timer = threading.Timer(0.3, cancel.set)
        timer.start()
        with pytest.raises(CancelledError) as info:
            run_process([sys.executable, '-c', 'import time; time.sleep(60)'], timeout=60,
                        cwd=str(tmp_path), env=dict(os.environ), cancel=cancel)
        timer.join()
        assert str(info.value) == 'cancelled'

    def test_fake_auth_hint(self):
        assert FakeBackend().auth_hint == 'fake: sign in again'


# --- Review round 2 ---------------------------------------------------------------

import errno
from types import SimpleNamespace


class TestRound2StartErrors:
    """Item 4: only a missing or non-executable program concerns the run."""

    @pytest.mark.parametrize('error', [
        FileNotFoundError(errno.ENOENT, 'No such file'),
        PermissionError(errno.EACCES, 'Permission denied'),
        OSError(errno.ENOEXEC, 'Exec format error'),
    ])
    def test_program_unusable_is_run_level(self, error):
        with patch('src.llm.cli.run_process', side_effect=error):
            with pytest.raises(BackendUnavailableError):
                ClaudeCLI().complete(_request(), threading.Event())

    # EAGAIN and ENOMEM moved to TestRound3ResourceErrors (review round 3)
    @pytest.mark.parametrize('code', [errno.E2BIG])
    def test_other_start_failure_concerns_the_request(self, code):
        error = OSError(code, os.strerror(code))
        with patch('src.llm.cli.run_process', side_effect=error):
            with pytest.raises(LLMError) as info:
                ClaudeCLI().complete(_request(), threading.Event())
        # Neither run-level nor retried
        assert type(info.value) is LLMError
        assert str(info.value) == f'claude-cli could not be started: {error}'


class TestRound2GroupKilledAfterParentExit:
    """Item 6: an exceptional end kills the group even if the parent has exited."""

    def test_cancel_after_parent_exit_kills_its_child(self, tmp_path, monkeypatch):
        pid_file = tmp_path / 'pid'
        cancel = threading.Event()

        class CancelOnceFinished(_subprocess.Popen):
            def communicate(self, *args, **kwargs):
                result = super().communicate(*args, **kwargs)
                cancel.set()  # noticed only after the parent has exited
                return result

        monkeypatch.setattr(cli_module.subprocess, 'Popen', CancelOnceFinished)
        # The parent starts a child in its group with the pipes redirected,
        # waits until the child has recorded its PID, and exits
        child = _sleeper(pid_file)
        cmd = [sys.executable, '-c',
               'import os,subprocess,time; '
               f'subprocess.Popen({child!r}, stdout=subprocess.DEVNULL, '
               'stderr=subprocess.DEVNULL)\n'
               f'while not (os.path.exists({str(pid_file)!r}) '
               f'and open({str(pid_file)!r}).read()): time.sleep(0.02)']
        with pytest.raises(CancelledError):
            run_process(cmd, timeout=60, cwd=str(tmp_path), env=dict(os.environ),
                        cancel=cancel)
        pid = int(pid_file.read_text())
        try:
            assert _gone(pid)
        finally:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
        assert cli_module._live_groups == set()


class TestRound2ExitHook:
    """Item 7: after the exit hook no process is started."""

    def test_no_start_after_hook(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli_module, '_shutting_down', False)
        cli_module._kill_live_groups()
        assert cli_module._shutting_down is True
        with patch('src.llm.cli.subprocess.Popen', side_effect=AssertionError('started')):
            with pytest.raises(CancelledError) as info:
                run_process(['true'], timeout=30, cwd=str(tmp_path), env=dict(os.environ))
        assert str(info.value) == 'shutting down'

    def test_backend_call_after_hook_is_not_retried(self, monkeypatch):
        monkeypatch.setattr(cli_module, '_shutting_down', False)
        monkeypatch.setattr(cli_module, 'run_process', run_process)  # the real one
        cli_module._kill_live_groups()
        from src.core.translator import TranslationEngine
        engine = TranslationEngine(model='haiku', backend=ClaudeCLI())
        with patch('src.llm.cli.subprocess.Popen', side_effect=AssertionError('started')):
            with pytest.raises(CancelledError):
                engine.translate('One sentence.')

    def test_hook_takes_the_guard(self, monkeypatch):
        monkeypatch.setattr(cli_module, '_shutting_down', False)
        cli_module._live_groups_guard.acquire()
        t = threading.Thread(target=cli_module._kill_live_groups)
        try:
            t.start()
            t.join(timeout=0.5)
            assert t.is_alive() and cli_module._shutting_down is False
        finally:
            cli_module._live_groups_guard.release()
        t.join(timeout=10)
        assert cli_module._shutting_down is True


class _GivingUpProc:
    """A killed process whose pipes a descendant keeps open."""
    pid = 4194301
    returncode = -9

    def __init__(self, output, stderr):
        self._expired = _subprocess.TimeoutExpired(['claude'], 2, output=output, stderr=stderr)
        self.stdout, self.stderr = MagicMock(), MagicMock()
        self.timeouts = []

    def communicate(self, timeout=None):
        self.timeouts.append(timeout)
        raise self._expired

    def wait(self, timeout=None):
        return self.returncode


class TestRound2PartialOutputAfterKill:
    """Item 9: giving up on held pipes keeps the output captured so far."""

    def test_captured_output_kept(self, monkeypatch):
        monkeypatch.setattr(cli_module.os, 'killpg', lambda pid, sig: None)
        proc = _GivingUpProc('partial reply\n'.encode('utf-8'), 'an error 錯誤'.encode('utf-8'))
        assert cli_module._kill_group(proc) == ('partial reply\n', 'an error 錯誤')
        proc.stdout.close.assert_called_once_with()
        proc.stderr.close.assert_called_once_with()
        assert proc.timeouts == [2]  # KILL_WAIT_SECONDS

    def test_kept_output_bounded_and_decoded(self, monkeypatch):
        monkeypatch.setattr(cli_module.os, 'killpg', lambda pid, sig: None)
        proc = _GivingUpProc(b'x' * 30000, b'\xff' + b'y' * 30000)
        stdout, stderr = cli_module._kill_group(proc)
        assert stdout == 'x' * 20000
        assert stderr == '�' + 'y' * 19999

    def test_nothing_captured(self, monkeypatch):
        monkeypatch.setattr(cli_module.os, 'killpg', lambda pid, sig: None)
        assert cli_module._kill_group(_GivingUpProc(None, None)) == ('', '')

    def test_kill_wait_seconds(self):
        assert cli_module.KILL_WAIT_SECONDS == 2


class _FinishedProc:
    pid = 4194302
    returncode = 0
    stdout = stderr = None

    def __init__(self, *args, **kwargs):
        pass

    def communicate(self, timeout=None):
        return 'out', ''


class TestRound2DeadlineBoundary:
    """Item 14: a call that ends exactly at the deadline is accepted."""

    def test_finished_exactly_at_deadline(self, monkeypatch):
        readings = iter([100.0])
        monkeypatch.setattr(cli_module, 'time',
                            SimpleNamespace(monotonic=lambda: next(readings, 105.0)))
        monkeypatch.setattr(cli_module.subprocess, 'Popen', _FinishedProc)
        result = run_process(['claude'], timeout=5, cwd='.', env={})
        assert (result.returncode, result.stdout) == (0, 'out')

    def test_finished_after_deadline(self, monkeypatch):
        readings = iter([100.0, 104.0])
        monkeypatch.setattr(cli_module, 'time',
                            SimpleNamespace(monotonic=lambda: next(readings, 105.5)))
        monkeypatch.setattr(cli_module.subprocess, 'Popen', _FinishedProc)
        monkeypatch.setattr(cli_module.os, 'killpg', lambda pid, sig: None)
        with pytest.raises(_subprocess.TimeoutExpired) as info:
            run_process(['claude'], timeout=5, cwd='.', env={})
        assert info.value.output == 'out'


class TestRound2RequestEcho:
    """Item 15: an exit-0 reply that echoes a phrase of the request is no error."""

    CODE = 'if err["type"] == "authentication_error":\n    refresh()'

    def test_phrase_in_request_not_an_error(self):
        result = _result(0, self.CODE)
        assert ClaudeCLI().classify_error(result, prompt=f'Translate:\n{self.CODE}') is None
        limit = "print(\"You've hit your limit\")"
        assert ClaudeCLI().classify_error(_result(0, limit), prompt=limit) is None

    def test_phrase_not_in_request_still_an_error(self):
        result = _result(0, self.CODE)
        assert isinstance(ClaudeCLI().classify_error(result, prompt='Translate: x'), AuthError)

    def test_other_phrase_than_the_echoed_one_is_an_error(self):
        result = _result(0, self.CODE + '\nPlease run /login')
        assert isinstance(ClaudeCLI().classify_error(result, prompt=self.CODE), AuthError)

    def test_failed_run_always_an_error(self):
        result = _result(1, self.CODE)
        assert isinstance(ClaudeCLI().classify_error(result, prompt=self.CODE), AuthError)

    def test_case_insensitive_echo(self):
        result = _result(0, 'AUTHENTICATION_ERROR')
        assert ClaudeCLI().classify_error(result, prompt='authentication_error') is None

    def test_complete_passes_the_prompt(self):
        with patch('src.llm.cli.run_process', return_value=_result(0, self.CODE)):
            response = ClaudeCLI().complete(_request(prompt=self.CODE), threading.Event())
        assert response.text == self.CODE


class TestRound2RateLimitMessage:
    """Item 21: the rate-limit message comes from the stream that holds it."""

    def test_long_stdout_does_not_hide_the_message(self):
        error = ClaudeCLI().classify_error(
            _result(1, 'x' * 1000, "You've hit your limit · resets 8pm\n"))
        assert str(error) == "You've hit your limit · resets 8pm"

    def test_message_in_stdout(self):
        error = ClaudeCLI().classify_error(_result(1, " You've hit your limit ", 'warning'))
        assert str(error) == "You've hit your limit"


# --- Review round 3 ---------------------------------------------------------------

from src.llm.fake import FakeBackendExhausted


class TestRound3StderrNotEchoSuppressed:
    """Item 3: the echo rule covers stdout only; stderr always counts."""

    def test_auth_on_stderr_with_phrase_in_request(self):
        result = _result(0, '', 'API Error: 401 authentication_error')
        error = ClaudeCLI().classify_error(result, prompt='handle authentication_error here')
        assert isinstance(error, AuthError)

    def test_limit_on_stderr_with_phrase_in_request(self):
        result = _result(0, 'x' * 1000, "You've hit your limit · resets 8pm")
        error = ClaudeCLI().classify_error(result, prompt="print(\"You've hit your limit\")")
        assert isinstance(error, RateLimitError)
        assert str(error) == "You've hit your limit · resets 8pm"

    def test_echo_on_stdout_still_ignored(self):
        result = _result(0, 'authentication_error', '')
        assert ClaudeCLI().classify_error(result, prompt='authentication_error') is None


class TestRound3ResourceErrors:
    """Item 4: a temporary shortage of system resources at start is retried."""

    @pytest.mark.parametrize('code', [errno.EAGAIN, errno.ENOMEM, errno.EMFILE, errno.ENFILE])
    def test_resource_error_is_transient(self, code):
        error = OSError(code, os.strerror(code))
        with patch('src.llm.cli.run_process', side_effect=error):
            with pytest.raises(TransientError) as info:
                ClaudeCLI().complete(_request(), threading.Event())
        assert str(info.value) == f'claude-cli could not be started: {error}'

    def test_blocking_io_error_is_transient(self):
        # What a failing fork raises
        with patch('src.llm.cli.run_process',
                   side_effect=BlockingIOError(errno.EAGAIN, 'Resource temporarily unavailable')):
            with pytest.raises(TransientError):
                ClaudeCLI().complete(_request(), threading.Event())


class TestRound3UndecodableOutput:
    """Item 7: undecodable output is decoded with replacement, not raised."""

    def test_invalid_utf8_replaced(self, tmp_path):
        cmd = [sys.executable, '-c',
               'import sys; sys.stdout.buffer.write(b"ok \\xff end"); '
               'sys.stderr.buffer.write(b"\\xfe")']
        result = run_process(cmd, timeout=30, cwd=str(tmp_path), env=dict(os.environ))
        assert (result.returncode, result.stdout, result.stderr) == (0, 'ok \ufffd end', '\ufffd')


class TestRound3FakeOverCall:
    """Item 10: a FakeBackend called beyond its script is not swallowed."""

    def test_exhausted_is_a_base_exception(self):
        fake = FakeBackend(['one'])
        assert fake.complete(_request(), threading.Event()).text == 'one'
        with pytest.raises(FakeBackendExhausted):
            fake.complete(_request(), threading.Event())
        assert not isinstance(FakeBackendExhausted(), Exception)
        assert len(FakeBackend.overcalls) == 1
        FakeBackend.overcalls.clear()  # this test's over-call is intended

    def test_not_turned_into_an_outcome(self):
        from src.core.translator import TranslationEngine, translate_segment
        from src.core.project import SegmentState
        seg = SegmentState(segment_id='c1_seg0001', chapter_id='c1', html='<p>A.</p>',
                           text='A.', word_count=1, segment_type='text')
        engine = TranslationEngine(model='m', backend=FakeBackend([]))
        with pytest.raises(FakeBackendExhausted):
            translate_segment(engine, seg, [seg])
        FakeBackend.overcalls.clear()


class TestRound3TimeoutOutputBounded:
    """Item 12: output kept on a late finish or a timeout is bounded."""

    def test_late_finish_output_bounded(self, monkeypatch):
        class BigProc(_FinishedProc):
            def communicate(self, timeout=None):
                return 'o' * 30000, 'e' * 30000

        readings = iter([100.0, 104.0])
        monkeypatch.setattr(cli_module, 'time',
                            SimpleNamespace(monotonic=lambda: next(readings, 105.5)))
        monkeypatch.setattr(cli_module.subprocess, 'Popen', BigProc)
        monkeypatch.setattr(cli_module.os, 'killpg', lambda pid, sig: None)
        with pytest.raises(_subprocess.TimeoutExpired) as info:
            run_process(['claude'], timeout=5, cwd='.', env={})
        assert len(info.value.output) == cli_module.KEPT_OUTPUT_CHARS
        assert len(info.value.stderr) == cli_module.KEPT_OUTPUT_CHARS

    def test_transient_raw_bounded(self):
        expired = _subprocess.TimeoutExpired(['claude'], 5, output='o' * 20000,
                                             stderr='e' * 20000)
        with patch('src.llm.cli.run_process', side_effect=expired):
            with pytest.raises(TransientError) as info:
                ClaudeCLI().complete(_request(), threading.Event())
        assert info.value.raw == 'o' * cli_module.KEPT_OUTPUT_CHARS
