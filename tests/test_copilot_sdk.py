"""The Copilot SDK backend (src/llm/copilot_sdk.py) against a fake SDK client:
no runtime is started and no model is called (conftest.no_real_sdk_client)."""

import asyncio
import concurrent.futures
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import llm
from src.llm import copilot_sdk
from src.llm.base import (
    AuthError, BackendUnavailableError, CancelledError, LLMRequest, Message, RateLimitError,
    RefusalError, TransientError,
)
from src.llm.cli import SYSTEM_PROMPT
from src.llm.copilot_cli import AUTH_HINT, EFFORT_REJECTED_MESSAGE, MODELS, CopilotCLI
from src.llm.copilot_sdk import CopilotSDK

# Imported at collection, before conftest's guard replaces it
from src.llm.copilot_sdk import _new_client as real_new_client  # noqa: E402

WAIT = 10  # seconds a test waits for a condition before failing
ROOT = str(Path(__file__).resolve().parent.parent)


def _req(prompt='PROMPT', model='claude-haiku-5.5', effort=None, timeout=30, system=''):
    return LLMRequest(system=system, messages=[Message('user', prompt)], model=model,
                      timeout=timeout, effort=effort)


def _event(kind, **data):
    """An SDK session event: `type` is an enum member, `data` the payload
    (with the SDK's fields the backend reads, defaulted)."""
    defaults = {'assistant.usage': {'finish_reason': 'stop', 'content_filter_triggered': False},
                'session.error': {'error_type': 'query'}}.get(kind, {})
    return SimpleNamespace(type=SimpleNamespace(value=kind),
                           data=SimpleNamespace(**{**defaults, **data}))


class ProcessExitedError(Exception):
    """Named like the SDK's error for a runtime that exited."""


class FakeSession:
    def __init__(self, client, session_id, options):
        self.client, self.session_id, self.options = client, session_id, options
        self.handlers = []
        self.aborted = threading.Event()
        self.sent = threading.Event()
        self._stop = None

    def on(self, handler):
        self.handlers.append(handler)

    def emit(self, event):
        for handler in self.handlers:
            handler(event)

    async def send_and_wait(self, prompt, timeout):
        self.prompt, self.timeout = prompt, timeout
        self._stop = asyncio.Event()
        self.sent.set()
        return await self.client.respond(self, prompt)

    async def abort(self):
        """As the SDK's: returns before the runtime reports the session idle."""
        self.aborted.set()
        self.client.log.append(('abort', self.session_id))
        asyncio.get_running_loop().call_soon(self.idle)

    def idle(self):
        self.client.log.append(('idle', self.session_id))
        self.emit(_event('session.idle', aborted=True))


class FakeClient:
    """Answers each prompt with `respond` (a coroutine function of session
    and prompt); default: the reply 'ANSWER <prompt>' with one usage event."""

    def __init__(self, respond=None, authenticated=True):
        self.sessions = []
        self.log = []
        self.starts = 0
        self.runtimes = 0
        self.dead = True  # no runtime yet
        self.authenticated = authenticated
        self.deleted = threading.Event()
        self._respond = respond

    async def start(self):
        self.starts += 1
        if self.dead:
            self.runtimes += 1
            self.dead = False

    async def get_auth_status(self):
        return SimpleNamespace(is_authenticated=self.authenticated)

    async def create_session(self, session_id, **options):
        session = FakeSession(self, session_id, options)
        self.sessions.append(session)
        self.log.append(('create', session_id))
        return session

    async def delete_session(self, session_id):
        self.log.append(('delete', session_id))
        self.deleted.set()

    async def stop(self):
        self.log.append(('stop',))

    async def force_stop(self):
        self.log.append(('force_stop',))

    async def respond(self, session, prompt):
        if self._respond is not None:
            return await self._respond(session, prompt)
        session.emit(_event('assistant.usage', input_tokens=7, output_tokens=3))
        session.emit(_event('session.idle', aborted=False))
        return _event('assistant.message', content=f'ANSWER {prompt}')


@pytest.fixture
def client(monkeypatch):
    """A fake client in place of the SDK's; a fresh runtime for the test."""
    fake = FakeClient()
    monkeypatch.setattr(copilot_sdk, '_new_client', lambda: fake)
    return fake


def _wait(condition):
    done = threading.Event()
    for _ in range(WAIT * 100):
        if condition():
            return True
        done.wait(0.01)
    return condition()


async def _forever(session, prompt):
    await asyncio.Event().wait()


class TestRegistration:

    def test_backend_for(self):
        backend = llm.backend_for('copilot-sdk')
        assert isinstance(backend, CopilotSDK)
        assert llm.BACKENDS['copilot-sdk'] is CopilotSDK

    def test_models_are_copilot_clis(self):
        backend = CopilotSDK()
        assert backend.list_models() == MODELS
        assert all(a is b for a, b in zip(backend.list_models(), MODELS))
        assert backend.default_model == CopilotCLI.default_model == 'claude-sonnet-5.5'
        assert backend.auth_hint == AUTH_HINT

    def test_other_backends_work_without_the_sdk(self):
        """The SDK is imported only when a client is made."""
        code = ("import sys; sys.modules['copilot'] = None\n"
                "from src.llm import BACKENDS, backend_for\n"
                "print(sorted(BACKENDS), backend_for('claude-cli').name,"
                " backend_for('copilot-sdk').name)")
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                                cwd=ROOT)
        assert result.returncode == 0, result.stderr
        assert result.stdout.split() == ["['claude-cli',", "'copilot-cli',", "'copilot-sdk']",
                                         'claude-cli', 'copilot-sdk']


class TestNewClient:

    def test_guarded_in_tests(self):
        with pytest.raises(BackendUnavailableError, match='tests do not start'):
            copilot_sdk._new_client()

    def test_missing_package(self, monkeypatch):
        monkeypatch.setitem(sys.modules, 'copilot', None)
        with pytest.raises(BackendUnavailableError) as e:
            real_new_client()
        assert 'microsoft-copilot-sdk' in str(e.value)
        assert 'pip install -r requirements.txt' in str(e.value)

    def test_missing_program(self, monkeypatch):
        monkeypatch.setattr(copilot_sdk.shutil, 'which', lambda name: None)
        with pytest.raises(BackendUnavailableError) as e:
            real_new_client()
        assert str(e.value) == copilot_sdk.MISSING_CLI_MESSAGE
        assert 'Copilot CLI' in str(e.value)

    def test_sdk_never_downloads_in_tests(self):
        """Without a runtime path the SDK would download one; in tests it
        refuses (conftest sets COPILOT_SKIP_CLI_DOWNLOAD)."""
        from copilot import CopilotClient
        with pytest.raises(RuntimeError):
            CopilotClient(mode='coding-agent')

    def test_uses_the_installed_program(self, monkeypatch, tmp_path):
        """The runtime is the `copilot` on PATH, spawned over stdio, with the
        default mode and Copilot home; constructing starts nothing."""
        from copilot import SpawnedStdioRuntimeConnection
        program = tmp_path / 'copilot'
        monkeypatch.setattr(copilot_sdk.shutil, 'which',
                            lambda name: str(program) if name == 'copilot' else None)
        built = real_new_client()
        connection = built._connection
        assert isinstance(connection, SpawnedStdioRuntimeConnection)
        assert connection.path == str(program)
        assert connection.base_directory is None and connection.env is None
        assert connection.working_directory == copilot_sdk.tempfile.gettempdir()
        assert built._options.mode == 'coding-agent'
        assert built._options.github_token is None
        assert built._process is None


class TestSessionOptions:

    def test_lean_session(self):
        options = copilot_sdk.session_options(_req(model='gpt-6-sol'))
        assert options['model'] == 'gpt-6-sol'
        assert options['system_message'] == {'mode': 'replace', 'content': SYSTEM_PROMPT}
        assert options['tools'] == {'available_tools': []}
        assert options['instructions'] == {'skip_custom_instructions': True}
        assert options['mcp'] == {'disabled_servers': ['github-mcp-server', 'githubiq']}
        assert options['skills'] == {'enabled': False}
        assert options['enable_config_discovery'] is False
        assert options['github'] == {'telemetry_enabled': False}
        assert options['state'] == {'infinite_sessions': {'enabled': False},
                                    'enable_cross_session_store': False}
        assert options['working_directory'] == copilot_sdk.tempfile.gettempdir()
        assert 'inference' not in options

    def test_effort(self):
        options = copilot_sdk.session_options(_req(effort='high'))
        assert options['inference'] == {'reasoning_effort': 'high'}


class TestComplete:

    def test_reply_usage_and_session(self, client):
        response = CopilotSDK().complete(_req('HELLO', effort='medium', timeout=40),
                                         threading.Event())
        assert response.text == 'ANSWER HELLO'
        assert response.stop_reason == 'complete' and response.model == 'claude-haiku-5.5'
        assert (response.usage.input_tokens, response.usage.output_tokens) == (7, 3)
        session = client.sessions[0]
        assert session.prompt == 'HELLO' and session.timeout == 40
        assert session.options == copilot_sdk.session_options(_req('HELLO', effort='medium'))
        assert session.options['inference'] == {'reasoning_effort': 'medium'}
        assert client.log == [('create', session.session_id), ('delete', session.session_id)]
        assert not session.aborted.is_set()

    def test_system_and_messages_flattened(self, client):
        CopilotSDK().complete(LLMRequest(system='RULES', messages=[
            Message('user', 'A'), Message('assistant', 'B'), Message('user', 'C')],
            model='m', timeout=30), threading.Event())
        assert client.sessions[0].prompt == 'RULES\n\n[user]\nA\n\n[assistant]\nB\n\n[user]\nC'

    def test_usage_summed_and_absent(self, client):
        async def two_calls(session, prompt):
            session.emit(_event('assistant.usage', input_tokens=5, output_tokens=1))
            session.emit(_event('assistant.usage', input_tokens=None, output_tokens=2))
            session.emit(_event('assistant.usage', input_tokens=4, output_tokens=None))
            return _event('assistant.message', content='X')

        async def none(session, prompt):
            return None

        client._respond = two_calls
        usage = CopilotSDK().complete(_req(), threading.Event()).usage
        assert (usage.input_tokens, usage.output_tokens) == (9, 3)
        client._respond = none
        response = CopilotSDK().complete(_req(), threading.Event())
        assert response.text == '' and response.usage is None

    def test_cancelled_before(self, client):
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(CancelledError, match='^cancelled$'):
            CopilotSDK().complete(_req(), cancel)
        assert client.starts == 0

    def test_timeout_aborts_and_deletes(self, client):
        client._respond = _forever
        with pytest.raises(TransientError, match='timed out after 0.3 s'):
            CopilotSDK().complete(_req(timeout=0.3), threading.Event())
        session = client.sessions[0]
        assert session.aborted.wait(WAIT)
        assert client.deleted.wait(WAIT)
        assert client.log == [('create', session.session_id), ('abort', session.session_id),
                              ('idle', session.session_id), ('delete', session.session_id)]

    def test_sdk_timeout_is_transient(self, client):
        async def timeout(session, prompt):
            raise TimeoutError('Timeout after 30s waiting for session.idle')

        client._respond = timeout
        with pytest.raises(TransientError, match='timed out'):
            CopilotSDK().complete(_req(), threading.Event())
        assert client.sessions[0].aborted.is_set()
        assert client.log[-1] == ('delete', client.sessions[0].session_id)

    def test_cancel_aborts_and_deletes(self, client):
        client._respond = _forever
        cancel = threading.Event()
        errors = []

        def call():
            try:
                CopilotSDK().complete(_req(timeout=600), cancel)
            except BaseException as e:
                errors.append(e)

        thread = threading.Thread(target=call)
        thread.start()
        assert _wait(lambda: client.sessions and client.sessions[0].sent.is_set())
        cancel.set()
        thread.join(WAIT)
        assert not thread.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], CancelledError)
        assert str(errors[0]) == 'cancelled'
        session = client.sessions[0]
        assert session.aborted.wait(WAIT) and client.deleted.wait(WAIT)
        assert client.log[-1] == ('delete', session.session_id)

    def test_abort_without_idle_still_deletes(self, client, monkeypatch):
        """A session that does not go idle after the abort is deleted after
        ABORT_WAIT_SECONDS."""
        monkeypatch.setattr(copilot_sdk, 'ABORT_WAIT_SECONDS', 0.05)
        client._respond = _forever

        async def silent_abort(self):
            self.aborted.set()

        monkeypatch.setattr(FakeSession, 'abort', silent_abort)
        with pytest.raises(TransientError):
            CopilotSDK().complete(_req(timeout=0.2), threading.Event())
        assert client.deleted.wait(WAIT)

    def test_delete_waits_for_idle(self, client, monkeypatch):
        """After the abort the session is deleted once it went idle, not
        before (other events do not count), and not only after the wait."""
        monkeypatch.setattr(copilot_sdk, 'ABORT_WAIT_SECONDS', 1000)
        client._respond = _forever
        idle_now = threading.Event()

        async def abort_then_idle(self):
            self.aborted.set()
            self.emit(_event('assistant.message_delta', delta='x'))
            await asyncio.to_thread(idle_now.wait, WAIT)
            self.emit(_event('session.idle', aborted=True))

        monkeypatch.setattr(FakeSession, 'abort', abort_then_idle)
        with pytest.raises(TransientError):
            CopilotSDK().complete(_req(timeout=0.2), threading.Event())
        assert client.sessions[0].aborted.wait(WAIT)
        assert not client.deleted.is_set()
        idle_now.set()
        assert client.deleted.wait(WAIT)

    def test_cleanup_bounded(self, client, monkeypatch):
        """A runtime that no longer answers cannot hold a cleanup forever."""
        monkeypatch.setattr(copilot_sdk, 'CLEANUP_SECONDS', 0.1)
        client._respond = _forever

        async def hang(session_id):
            await asyncio.Event().wait()

        monkeypatch.setattr(client, 'delete_session', hang)
        with pytest.raises(TransientError):
            CopilotSDK().complete(_req(timeout=0.2), threading.Event())
        loop = copilot_sdk._runtime._loop

        async def others():
            return len(asyncio.all_tasks()) - 1

        assert _wait(lambda: asyncio.run_coroutine_threadsafe(others(), loop).result(WAIT) == 0)

    def test_cleanup_bounded_in_this_thread(self, client, monkeypatch):
        """The same bound, with the cleanup run here (on this thread's own loop)."""
        monkeypatch.setattr(copilot_sdk, 'CLEANUP_SECONDS', 0.1)

        async def hang(session_id):
            await asyncio.Event().wait()

        monkeypatch.setattr(client, 'delete_session', hang)

        async def clean_up():
            await asyncio.wait_for(
                CopilotSDK._clean_up(client, None, 'id', asyncio.Event()), WAIT)

        asyncio.run(clean_up())

    def test_reply_refused_after_cancel(self, client):
        cancel = threading.Event()

        async def cancel_then_answer(session, prompt):
            cancel.set()
            return _event('assistant.message', content='LATE')

        client._respond = cancel_then_answer
        with pytest.raises(CancelledError, match='^cancelled$'):
            CopilotSDK().complete(_req(), cancel)

    def test_concurrent_calls(self, client):
        """Twenty calls at once share one client and run at the same time:
        no reply is given before all twenty have been sent."""
        count = 20
        all_sent = asyncio.Event()
        sent = []

        async def together(session, prompt):
            sent.append(prompt)
            if len(sent) == count:
                all_sent.set()
            await all_sent.wait()
            return _event('assistant.message', content=f'ANSWER {prompt}')

        client._respond = together
        results = {}

        def call(i):
            results[i] = CopilotSDK().complete(_req(f'P{i}', timeout=WAIT), threading.Event()).text

        threads = [threading.Thread(target=call, args=(i,)) for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(WAIT * 2)
        assert results == {i: f'ANSWER P{i}' for i in range(count)}
        assert len({s.session_id for s in client.sessions}) == count
        assert sorted(e[0] for e in client.log).count('delete') == count

    def test_one_client_started_per_call(self, client):
        backend = CopilotSDK()
        backend.complete(_req(), threading.Event())
        backend.complete(_req(), threading.Event())
        assert client.starts == 2 and client.runtimes == 1

    def test_runtime_death_then_new_runtime(self, client):
        """The call in flight fails as transient; the next call's start()
        brings up a new runtime on the same client."""
        async def die(session, prompt):
            client.dead = True
            raise ProcessExitedError("CLI process exited with code -9: You've hit your rate limit.")

        client._respond = die
        with pytest.raises(TransientError, match='exited'):
            CopilotSDK().complete(_req(), threading.Event())
        client._respond = None
        assert CopilotSDK().complete(_req('AGAIN'), threading.Event()).text == 'ANSWER AGAIN'
        assert client.runtimes == 2

    @pytest.mark.parametrize('error', [RuntimeError('Client not connected'),
                                       EOFError('Unexpected end of stream')])
    def test_connection_errors_transient(self, client, error):
        async def fail(session, prompt):
            raise error

        client._respond = fail
        with pytest.raises(TransientError, match=str(error)):
            CopilotSDK().complete(_req(), threading.Event())


class TestNothingCutShort:
    """The cleanup of a turn (abort, idle wait, delete) is never interrupted:
    `complete` returns at once and the turn finishes it on the loop. The
    fake's abort, idle and delete really yield, and its send honours a
    timeout (one shorter than the request's, so the loop times out first)."""

    @staticmethod
    def _gate():
        """An awaitable gate the test opens from its thread."""
        gate = threading.Event()

        async def passed():
            await asyncio.to_thread(gate.wait, WAIT)
        return gate, passed

    def test_loop_timeout_first(self, client, monkeypatch):
        gate, passed = self._gate()

        async def times_out(session, prompt):
            await asyncio.wait_for(asyncio.Event().wait(), session.timeout / 2)

        async def slow_abort(self):
            self.aborted.set()
            await passed()
            self.client.log.append(('abort', self.session_id))
            self.idle()

        client._respond = times_out
        monkeypatch.setattr(FakeSession, 'abort', slow_abort)
        with pytest.raises(TransientError):
            CopilotSDK().complete(_req(timeout=0.4), threading.Event())
        assert client.sessions[0].aborted.wait(WAIT)
        gate.set()
        assert client.deleted.wait(WAIT)

    def test_pause_while_deleting(self, client, monkeypatch):
        gate, passed = self._gate()
        deleting = threading.Event()
        cancel = threading.Event()

        async def slow_delete(session_id):
            deleting.set()
            await passed()
            client.log.append(('delete', session_id))
            client.deleted.set()

        monkeypatch.setattr(client, 'delete_session', slow_delete)
        errors = []
        thread = threading.Thread(target=lambda: errors.append(_raises(
            lambda: CopilotSDK().complete(_req(), cancel))))
        thread.start()
        assert deleting.wait(WAIT)
        cancel.set()
        thread.join(WAIT)
        assert isinstance(errors[0], CancelledError)
        gate.set()
        assert client.deleted.wait(WAIT)

    def test_cancel_while_creating_sends_nothing(self, client, monkeypatch):
        gate, passed = self._gate()
        creating = threading.Event()
        cancel = threading.Event()
        create = client.create_session

        async def slow_create(session_id, **options):
            creating.set()
            await passed()
            return await create(session_id=session_id, **options)

        monkeypatch.setattr(client, 'create_session', slow_create)
        errors = []
        thread = threading.Thread(target=lambda: errors.append(_raises(
            lambda: CopilotSDK().complete(_req(), cancel))))
        thread.start()
        assert creating.wait(WAIT)
        cancel.set()
        thread.join(WAIT)
        assert isinstance(errors[0], CancelledError)
        gate.set()
        assert client.deleted.wait(WAIT)
        session = client.sessions[0]
        assert not session.sent.is_set() and not session.aborted.is_set()
        assert client.log == [('create', session.session_id), ('delete', session.session_id)]

    def test_close_waits_for_cleanups(self, client, monkeypatch):
        """At exit the turns still running are stopped and their sessions
        deleted before the client is stopped."""
        gate, passed = self._gate()
        client._respond = _forever
        sent = threading.Event()
        cancel = threading.Event()

        async def slow_delete(session_id):
            await passed()
            client.log.append(('delete', session_id))

        monkeypatch.setattr(client, 'delete_session', slow_delete)
        thread = threading.Thread(target=lambda: _raises(
            lambda: CopilotSDK().complete(_req(), cancel)), daemon=True)
        thread.start()
        assert _wait(lambda: client.sessions and client.sessions[0].sent.is_set())
        closed = threading.Thread(target=copilot_sdk._runtime.close)
        closed.start()
        assert client.sessions[0].aborted.wait(WAIT)
        assert ('stop',) not in client.log
        gate.set()
        closed.join(WAIT)
        session_id = client.sessions[0].session_id
        assert client.log[-3:] == [('idle', session_id), ('delete', session_id), ('stop',)]
        cancel.set()
        thread.join(WAIT)

    def test_finished_turns_forgotten(self, client):
        CopilotSDK().complete(_req(), threading.Event())
        assert _wait(lambda: copilot_sdk._runtime._turns == {})

    def test_close_bounded_by_a_stuck_turn(self, client, monkeypatch):
        """A turn stuck where the stop event cannot reach it (a session
        creation that never answers) does not hold the exit for ever."""
        monkeypatch.setattr(copilot_sdk, 'CLEANUP_SECONDS', 0.2)
        creating = threading.Event()

        async def stuck(session_id, **options):
            creating.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(client, 'create_session', stuck)
        cancel = threading.Event()
        thread = threading.Thread(target=lambda: _raises(
            lambda: CopilotSDK().complete(_req(), cancel)), daemon=True)
        thread.start()
        assert creating.wait(WAIT)
        done = threading.Event()
        threading.Thread(target=lambda: (copilot_sdk._runtime.close(), done.set()),
                         daemon=True).start()
        assert done.wait(WAIT)
        cancel.set()
        thread.join(WAIT)

    def test_reply_after_the_deadline_refused(self, client, monkeypatch):
        """The reply is there (the wait for it is long, the fake answers at
        once), but the clock says the deadline passed: refused."""
        monkeypatch.setattr(copilot_sdk, 'POLL_SECONDS', WAIT)
        clock = iter([0.0, 0.0])
        monkeypatch.setattr(copilot_sdk, 'time', SimpleNamespace(
            monotonic=lambda: next(clock, 1000.0)))
        started = time.monotonic()
        with pytest.raises(TransientError, match='timed out after 30 s'):
            CopilotSDK().complete(_req(timeout=30), threading.Event())
        assert time.monotonic() - started < WAIT / 2  # not through a wait that ran out
        assert client.log[-1][0] == 'delete' and not client.sessions[0].aborted.is_set()

    def test_poll_never_waits_past_the_deadline(self, client, monkeypatch):
        waits = []
        real = concurrent.futures.Future.result

        def result(self, timeout=None):
            waits.append(timeout)
            return real(self, timeout)

        monkeypatch.setattr(concurrent.futures.Future, 'result', result)
        client._respond = _forever
        with pytest.raises(TransientError):
            CopilotSDK().complete(_req(timeout=0.25), threading.Event())
        assert all(w <= copilot_sdk.POLL_SECONDS for w in waits)
        assert any(0 < w < copilot_sdk.POLL_SECONDS for w in waits)


def _raises(call):
    try:
        call()
    except BaseException as e:  # noqa: BLE001 - returned to the test
        return e
    return None


class TestRoundTwo:
    """Review round 2: the delete after a failed abort, the close race at a
    cold start, a cancel between steps, a wedged setup, a hanging stop."""

    def test_abort_raises_delete_still_happens(self, client, monkeypatch):
        client._respond = _forever

        async def broken(self):
            self.aborted.set()
            raise Exception('JSON-RPC Error -32603: session is idle')

        monkeypatch.setattr(FakeSession, 'abort', broken)
        with pytest.raises(TransientError):
            CopilotSDK().complete(_req(timeout=0.2), threading.Event())
        assert client.deleted.wait(WAIT)

    def test_abort_hangs_delete_still_happens(self, client, monkeypatch):
        monkeypatch.setattr(copilot_sdk, 'ABORT_WAIT_SECONDS', 0.1)
        client._respond = _forever

        async def hangs(self):
            self.aborted.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(FakeSession, 'abort', hangs)
        with pytest.raises(TransientError):
            CopilotSDK().complete(_req(timeout=0.2), threading.Event())
        assert client.deleted.wait(WAIT)

    def test_close_before_the_first_turn_reaches_the_client(self, monkeypatch):
        """A first turn submitted but not yet run (the loop is held busy)
        when close() is called: close waits for it, and it makes no client,
        so no runtime is left started; client() refuses once closed."""
        made = []
        monkeypatch.setattr(copilot_sdk, '_new_client', lambda: made.append(1) or FakeClient())
        runtime = copilot_sdk._runtime
        hold = threading.Event()

        async def busy():
            hold.wait(WAIT)  # blocks the loop itself

        runtime.submit(busy(), asyncio.Event())
        errors = []
        caller = threading.Thread(target=lambda: errors.append(_raises(
            lambda: CopilotSDK().complete(_req(), threading.Event()))))
        caller.start()
        assert _wait(lambda: len(runtime._turns) == 2)
        closer = threading.Thread(target=runtime.close)
        closer.start()
        assert _wait(lambda: runtime._closed)
        hold.set()
        closer.join(WAIT)
        caller.join(WAIT)
        assert not closer.is_alive() and not caller.is_alive()
        assert made == [] and isinstance(errors[0], CancelledError)
        with pytest.raises(CancelledError):
            asyncio.run(runtime.client())

    def test_close_while_the_first_turn_starts_the_runtime(self, client, monkeypatch):
        """close() during the first start(): the start is given up, and the
        client made is stopped all the same."""
        starting = threading.Event()

        async def slow_start():
            starting.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(client, 'start', slow_start)
        caller = threading.Thread(target=lambda: _raises(
            lambda: CopilotSDK().complete(_req(), threading.Event())))
        caller.start()
        assert starting.wait(WAIT)
        copilot_sdk._runtime.close()
        caller.join(WAIT)
        assert client.log == [('stop',)] and client.sessions == []

    def test_cancel_between_creation_and_send(self, client, monkeypatch):
        """The cancel comes while the session is created, and creation ends
        before the caller's next poll: nothing is sent."""
        monkeypatch.setattr(copilot_sdk, 'POLL_SECONDS', 2)
        gate = threading.Event()
        creating = threading.Event()
        create = client.create_session

        async def slow_create(session_id, **options):
            creating.set()
            await asyncio.to_thread(gate.wait, WAIT)
            return await create(session_id=session_id, **options)

        monkeypatch.setattr(client, 'create_session', slow_create)
        cancel = threading.Event()
        errors = []
        caller = threading.Thread(target=lambda: errors.append(_raises(
            lambda: CopilotSDK().complete(_req(), cancel))))
        caller.start()
        assert creating.wait(WAIT)
        cancel.set()
        gate.set()  # before the caller is joined (and before its next poll)
        assert client.deleted.wait(WAIT)
        caller.join(WAIT)
        assert isinstance(errors[0], CancelledError)
        assert not client.sessions[0].sent.is_set()

    @pytest.mark.parametrize('step', ['start', 'get_auth_status', 'create_session'])
    def test_wedged_setup_ends_and_a_later_call_works(self, client, monkeypatch, step):
        real = getattr(client, step)
        wedged = [True]

        async def stuck(*args, **kwargs):
            if wedged[0]:
                await asyncio.Event().wait()
            return await real(*args, **kwargs)

        monkeypatch.setattr(client, step, stuck)
        with pytest.raises(TransientError):
            CopilotSDK().complete(_req(timeout=0.2), threading.Event())
        assert _wait(lambda: copilot_sdk._runtime._turns == {})
        wedged[0] = False
        assert CopilotSDK().complete(_req('AGAIN'), threading.Event()).text == 'ANSWER AGAIN'

    def test_hanging_stop_is_forced(self, client, monkeypatch):
        monkeypatch.setattr(copilot_sdk, 'CLEANUP_SECONDS', 0.1)

        async def hangs():
            await asyncio.Event().wait()

        CopilotSDK().complete(_req(), threading.Event())
        monkeypatch.setattr(client, 'stop', hangs)
        copilot_sdk._runtime.close()
        assert client.log[-1] == ('force_stop',)

    def test_close_bounded_when_force_stop_hangs_too(self, client, monkeypatch):
        monkeypatch.setattr(copilot_sdk, 'CLEANUP_SECONDS', 0.1)

        async def hangs():
            await asyncio.Event().wait()

        CopilotSDK().complete(_req(), threading.Event())
        monkeypatch.setattr(client, 'stop', hangs)
        monkeypatch.setattr(client, 'force_stop', hangs)
        closer = threading.Thread(target=copilot_sdk._runtime.close)
        closer.start()
        closer.join(WAIT / 2)
        assert not closer.is_alive()

    @pytest.mark.parametrize('filtered, text, refused', [
        ([True, False], 'reply', False), ([False, True], 'reply', True),
        ([True, False], '', True), ([False, False], '', False)])
    def test_filter_only_when_it_cost_the_reply(self, client, filtered, text, refused):
        async def respond(session, prompt):
            for flag in filtered:
                session.emit(_event('assistant.usage', input_tokens=1, output_tokens=1,
                                    content_filter_triggered=flag))
            return _event('assistant.message', content=text)

        client._respond = respond
        if refused:
            with pytest.raises(RefusalError):
                CopilotSDK().complete(_req(), threading.Event())
        else:
            assert CopilotSDK().complete(_req(), threading.Event()).text == text


class TestErrors:

    @pytest.mark.parametrize('message, error, text', [
        ('JSON-RPC Error -32603: Request session.create failed with message: '
         'Model "no-such-model" is not available.', BackendUnavailableError, 'not available'),
        ("JSON-RPC Error -32603: Request session.create failed with message: session "
         "construction failed: Model 'claude-haiku-4.5' does not support reasoning effort "
         "configuration.", BackendUnavailableError, EFFORT_REJECTED_MESSAGE),
        ("JSON-RPC Error -32603: Execution failed: Reasoning effort 'xhigh' is not supported "
         "for model 'gemini-4-argon'.", BackendUnavailableError, EFFORT_REJECTED_MESSAGE),
        ("Session error: You've hit the rate limit for this model.", RateLimitError, 'rate limit'),
        ('Session error: Not authenticated', AuthError, AUTH_HINT),
        ('Session error: blocked by content filter', RefusalError, 'content filter'),
        ('Session error: socket hang up', TransientError, 'socket hang up'),
    ])
    def test_create_error(self, client, monkeypatch, message, error, text):
        async def fail(session_id, **options):
            client.log.append(('create', session_id))
            raise Exception(message)

        monkeypatch.setattr(client, 'create_session', fail)
        with pytest.raises(error) as e:
            CopilotSDK().complete(_req(), threading.Event())
        assert type(e.value) is error and text in str(e.value)
        # The half-created session is deleted, and nothing was aborted
        assert client.log == [('create', client.log[0][1]), ('delete', client.log[0][1])]

    def test_message_and_events_joined(self, client):
        async def failed(session, prompt):
            session.emit(_event('session.error', message='first'))
            session.emit(_event('session.error', message='second'))
            raise Exception('Session error: see event')

        client._respond = failed
        with pytest.raises(TransientError) as e:
            CopilotSDK().complete(_req(), threading.Event())
        assert str(e.value) == 'copilot SDK: Session error: see event | query first | query second'

    def test_pattern_after_a_long_preamble(self, client):
        """Matched on the whole text; only the message is cut."""
        async def failed(session, prompt):
            raise Exception('x' * 2000 + " You've hit the rate limit for this model.")

        client._respond = failed
        with pytest.raises(RateLimitError) as e:
            CopilotSDK().complete(_req(), threading.Event())
        assert str(e.value) == 'copilot SDK: ' + 'x' * copilot_sdk.ERROR_CHARS

    def test_filtered_reply_refused(self, client):
        async def filtered(session, prompt):
            session.emit(_event('assistant.usage', input_tokens=1, output_tokens=1,
                                content_filter_triggered=True))
            return _event('assistant.message', content='partial')

        client._respond = filtered
        with pytest.raises(RefusalError) as e:
            CopilotSDK().complete(_req(), threading.Event())
        assert str(e.value) == 'copilot SDK: the reply was filtered (content filter)'

    @pytest.mark.parametrize('reasons, stop_reason', [
        (['stop', 'length'], 'truncated'), (['length', 'stop'], 'complete'),
        ([None], 'complete')])
    def test_length_cutoff_truncated(self, client, reasons, stop_reason):
        async def cut(session, prompt):
            for reason in reasons:
                session.emit(_event('assistant.usage', input_tokens=1, output_tokens=1,
                                    finish_reason=reason))
            return _event('assistant.message', content='part')

        client._respond = cut
        assert CopilotSDK().complete(_req(), threading.Event()).stop_reason == stop_reason

    def test_runtime_exit_never_a_pattern(self, client):
        async def died(session, prompt):
            raise ProcessExitedError('CLI process exited with code 1: Model "x" is not available.')

        client._respond = died
        with pytest.raises(TransientError) as e:
            CopilotSDK().complete(_req(), threading.Event())
        assert type(e.value) is TransientError

    def test_session_error_events_classified_and_redacted(self, client):
        async def failed(session, prompt):
            session.emit(_event('session.error',
                                message="You've hit your weekly rate limit. (user@example.com)"))
            raise Exception('Session error: see event')

        client._respond = failed
        with pytest.raises(RateLimitError) as e:
            CopilotSDK().complete(_req(), threading.Event())
        assert 'user@example.com' not in str(e.value) and '<redacted>' in str(e.value)

    def test_reply_never_searched(self, client):
        async def quoting(session, prompt):
            return _event('assistant.message', content="You've hit the rate limit. Not authenticated.")

        client._respond = quoting
        text = CopilotSDK().complete(_req(), threading.Event()).text
        assert text == "You've hit the rate limit. Not authenticated."

    def test_not_logged_in(self, client):
        client.authenticated = False
        with pytest.raises(AuthError) as e:
            CopilotSDK().complete(_req(), threading.Event())
        assert AUTH_HINT in str(e.value) and CopilotSDK().auth_hint == AUTH_HINT
        assert client.sessions == []
        # Checked again until it succeeds, then once per client
        client.authenticated = True
        CopilotSDK().complete(_req(), threading.Event())
        client.authenticated = False
        CopilotSDK().complete(_req(), threading.Event())

    def test_start_failure_unavailable(self, client, monkeypatch):
        async def broken():
            raise RuntimeError('Copilot CLI not found at /x (alice@example.com)')

        monkeypatch.setattr(client, 'start', broken)
        with pytest.raises(BackendUnavailableError) as e:
            CopilotSDK().complete(_req(), threading.Event())
        assert str(e.value) == ('無法啟動 Copilot SDK 的執行環境（copilot）：'
                                'Copilot CLI not found at /x (<redacted>)')

    def test_missing_sdk_reaches_the_caller(self, monkeypatch):
        def missing():
            raise BackendUnavailableError(copilot_sdk.MISSING_SDK_MESSAGE)

        monkeypatch.setattr(copilot_sdk, '_new_client', missing)
        with pytest.raises(BackendUnavailableError, match='microsoft-copilot-sdk'):
            CopilotSDK().complete(_req(), threading.Event())


class TestShutdown:

    def test_close_stops_the_client_and_refuses_calls(self, client):
        CopilotSDK().complete(_req(), threading.Event())
        copilot_sdk._runtime.close()
        assert client.log[-1] == ('stop',)
        with pytest.raises(CancelledError, match='^shutting down$'):
            CopilotSDK().complete(_req(), threading.Event())

    def test_loop_thread(self, client):
        CopilotSDK().complete(_req(), threading.Event())
        thread = copilot_sdk._runtime._thread
        assert thread.daemon and thread.name == 'copilot-sdk' and thread.is_alive()

    def test_close_bounded(self, client, monkeypatch):
        monkeypatch.setattr(copilot_sdk, 'CLEANUP_SECONDS', 0.1)

        async def hang():
            await asyncio.Event().wait()

        CopilotSDK().complete(_req(), threading.Event())
        monkeypatch.setattr(client, 'stop', hang)
        done = threading.Event()
        threading.Thread(target=lambda: (copilot_sdk._runtime.close(), done.set()),
                         daemon=True).start()
        assert done.wait(WAIT)

    def test_close_force_stops_when_stop_fails(self, client, monkeypatch):
        async def failing_stop():
            raise RuntimeError('stop failed')

        CopilotSDK().complete(_req(), threading.Event())
        monkeypatch.setattr(client, 'stop', failing_stop)
        copilot_sdk._runtime.close()
        assert client.log[-1] == ('force_stop',)

    def test_close_bounded_when_force_stop_hangs_too(self, client, monkeypatch):
        monkeypatch.setattr(copilot_sdk, 'CLEANUP_SECONDS', 0.1)

        async def hangs():
            await asyncio.Event().wait()

        CopilotSDK().complete(_req(), threading.Event())
        monkeypatch.setattr(client, 'stop', hangs)
        monkeypatch.setattr(client, 'force_stop', hangs)
        closer = threading.Thread(target=copilot_sdk._runtime.close)
        closer.start()
        closer.join(WAIT / 2)
        assert not closer.is_alive()

    def test_close_without_client(self):
        runtime = copilot_sdk._Runtime()
        runtime.close()  # nothing started: nothing to stop
        assert runtime._loop is None

    def test_stopped_at_interpreter_exit(self):
        """In a child process: a call still running when the main thread
        ends. The exit hook stops it, its session is deleted and the client
        stopped, both through asyncio.to_thread as the SDK's are: from a
        plain atexit hook (or with concurrent.futures' hook running first)
        the executor is already shut down and neither happens."""
        code = """
import asyncio, threading
from types import SimpleNamespace
from src.llm import copilot_sdk
from src.llm.base import LLMRequest, Message

sent = threading.Event()

class Session:
    def on(self, handler): self.handler = handler
    async def send_and_wait(self, prompt, timeout):
        sent.set()
        await asyncio.Event().wait()
    async def abort(self):
        self.handler(SimpleNamespace(type=SimpleNamespace(value='session.idle'),
                                     data=SimpleNamespace(aborted=True)))

class Client:
    async def start(self): pass
    async def get_auth_status(self): return SimpleNamespace(is_authenticated=True)
    async def create_session(self, session_id, **options): return Session()
    async def delete_session(self, session_id):
        await asyncio.to_thread(print, 'DELETED', flush=True)
    async def stop(self):
        await asyncio.to_thread(print, 'STOPPED', flush=True)
    async def force_stop(self):
        print('FORCED', flush=True)

copilot_sdk._new_client = Client
request = LLMRequest(system='', messages=[Message('user', 'x')], model='m', timeout=600)
def call():
    try:
        copilot_sdk.CopilotSDK().complete(request, threading.Event())
    except BaseException:
        pass  # stopped at exit; a traceback printed while the interpreter
              # finalizes can abort it (stderr's lock)

threading.Thread(target=call, daemon=True).start()
sent.wait(10)
print('EXIT', flush=True)
"""
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                                cwd=ROOT, timeout=60)
        assert result.returncode == 0, result.stderr
        assert result.stdout.split() == ['EXIT', 'DELETED', 'STOPPED']
