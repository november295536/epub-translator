"""GitHub Copilot backend through the Copilot Python SDK (`microsoft-copilot-sdk`).

The SDK drives a Copilot runtime over JSON-RPC. The runtime is the installed
`copilot` program (found on PATH), started by the SDK as a child process
over stdio; the SDK's own runtime is never downloaded. One SDK client per
Python process (`_Runtime`): its event loop runs in a daemon thread, it is
started on the first call and stopped at interpreter exit. Before every call
`client.start()` runs, which does nothing while the runtime is connected and
starts a new runtime when the last one died.

One request is one fresh session: SYSTEM_PROMPT replaces the system message,
no tools, built-in MCP servers, skills or custom instructions; the reply is
the final assistant message, the usage the session's `assistant.usage`
events. Every session is deleted after its request (`client.delete_session`
removes `~/.copilot/session-state/<id>/`, which holds the prompt and reply).

Timeout and cancel: `send_and_wait`'s own timeout does not stop the turn (it
keeps running and is billed), so a call that times out or is cancelled
aborts the session's turn (`session.abort()`), waits briefly for the session
to go idle, then deletes it. `complete` returns at once; the abort and the
delete finish on the event loop.

Authentication: the Copilot CLI's logged-in account (mode `coding-agent`,
default COPILOT_HOME). Mode `empty` or another COPILOT_HOME silently use a
different token (the GitHub CLI's), so neither is set. A runtime that reports
no authenticated user raises AuthError.

Error messages as observed with SDK 2.0.1 and Copilot CLI 1.0.114
(plan/03-acceptance-log.md, Phase 12): an unknown model (`Model "x" is not
available.`) and a refused effort (`Model 'x' does not support reasoning
effort configuration`, `Reasoning effort 'x' is not supported for model
'y'`) are JSON-RPC errors before any model call; a runtime that died fails
the call in flight with ProcessExitedError. Rate limits, expired logins and
refusals were not observed: the patterns are copilot_cli's (documented rate
limit messages, guessed login and refusal ones). Only error messages are
searched, never a reply.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import shutil
import tempfile
import threading
import time
import uuid
from typing import List, Optional

from src.llm.base import (
    AuthError, BackendUnavailableError, CancelledError, Capabilities, LLMError, LLMRequest,
    LLMResponse, ModelInfo, RateLimitError, RefusalError, TransientError, Usage,
)
from src.llm.cli import ERROR_CHARS, POLL_SECONDS, SYSTEM_PROMPT, flatten_prompt
from src.llm.copilot_cli import (
    _AUTH_PATTERN, _EFFORT_REJECTED_PATTERN, _RATE_LIMIT_PATTERN, _REFUSAL_PATTERN,
    _UNAVAILABLE_PATTERN, AUTH_HINT, EFFORT_REJECTED_MESSAGE, MODELS, CopilotCLI, redact,
)

# How long an aborted session may take to go idle before it is deleted anyway
ABORT_WAIT_SECONDS = 5
# Bound on abort + delete, and on stopping the client at exit: a runtime that
# no longer answers cannot hold them forever
CLEANUP_SECONDS = 10

# The built-in MCP servers of the coding-agent mode (as listed by the
# session's `session.mcp_servers_loaded` event), not started
BUILTIN_MCP_SERVERS = ['github-mcp-server', 'githubiq']

MISSING_SDK_MESSAGE = ('找不到 Copilot SDK（Python 套件 microsoft-copilot-sdk），'
                       '請在專案的虛擬環境執行 pip install -r requirements.txt')
MISSING_CLI_MESSAGE = ('找不到 copilot 程式：Copilot SDK 以已安裝的 GitHub Copilot CLI 作為執行環境，'
                       '請先安裝 Copilot CLI 並執行 copilot login')
NOT_LOGGED_IN_MESSAGE = f'Copilot SDK 沒有已登入的帳號，{AUTH_HINT}'

# The SDK logs failures it also raises (with tracebacks) and the runtime's
# stderr as warnings; the errors this backend raises carry what matters
logging.getLogger('copilot').setLevel(logging.ERROR)


def _new_client():
    """A new SDK client on the installed `copilot` program. The only place
    that creates one (tests replace it)."""
    try:
        from copilot import CopilotClient, RuntimeConnection
    except ImportError:
        raise BackendUnavailableError(MISSING_SDK_MESSAGE)
    path = shutil.which('copilot')
    if path is None:
        raise BackendUnavailableError(MISSING_CLI_MESSAGE)
    # A neutral working directory, as for the CLI backends
    return CopilotClient(mode='coding-agent', runtime=RuntimeConnection.for_spawned_stdio(
        path=path, working_directory=tempfile.gettempdir()))


def session_options(request: LLMRequest) -> dict:
    """The keyword arguments of `create_session` for one request."""
    options = dict(
        model=request.model,
        system_message={'mode': 'replace', 'content': SYSTEM_PROMPT},
        tools={'available_tools': []},
        instructions={'skip_custom_instructions': True},
        mcp={'disabled_servers': list(BUILTIN_MCP_SERVERS)},
        skills={'enabled': False},
        enable_config_discovery=False,
        state={'infinite_sessions': {'enabled': False}},
        github={'telemetry_enabled': False},
        working_directory=tempfile.gettempdir(),
    )
    if request.effort:
        options['inference'] = {'reasoning_effort': request.effort}
    return options


def _error(error: Exception, messages: List[str]) -> LLMError:
    """The LLMError a failed call represents; `messages` are the session's
    `session.error` messages."""
    if isinstance(error, LLMError):
        return error
    joined = redact(' | '.join([str(error)] + messages))[:ERROR_CHARS]
    if isinstance(error, TimeoutError):
        return TransientError(f'copilot SDK timed out: {joined}')
    if type(error).__name__ == 'ProcessExitedError':
        # The runtime exited (its message can quote its stderr): never a pattern
        return TransientError(f'copilot SDK: {joined}')
    # (pattern, the error it means), the first match wins
    patterns = ((_RATE_LIMIT_PATTERN, lambda: RateLimitError(f'copilot SDK: {joined}')),
                (_AUTH_PATTERN, lambda: AuthError(f'Copilot SDK 認證失敗，{AUTH_HINT}：{joined}')),
                (_EFFORT_REJECTED_PATTERN,
                 lambda: BackendUnavailableError(f'{EFFORT_REJECTED_MESSAGE}：{joined}')),
                (_UNAVAILABLE_PATTERN, lambda: BackendUnavailableError(f'copilot SDK: {joined}')),
                (_REFUSAL_PATTERN, lambda: RefusalError(f'copilot SDK: {joined}')))
    for pattern, make in patterns:
        if pattern.search(joined):
            return make()
    # Anything else, also a connection that went away (EOF, "not connected")
    return TransientError(f'copilot SDK: {joined}')


class _Runtime:
    """The SDK client of this process and the event loop it runs on (a
    daemon thread), both created on first use. Everything that touches the
    client runs on that loop."""

    def __init__(self):
        self._guard = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client = None
        self._authenticated = False
        self._closed = False

    def submit(self, coro):
        """Run `coro` on the loop; a concurrent.futures.Future."""
        with self._guard:
            if self._closed:
                coro.close()
                raise CancelledError('shutting down')
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(target=self._loop.run_forever,
                                                name='copilot-sdk', daemon=True)
                self._thread.start()
            return asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def client(self):
        """The started client (on the loop): a new runtime when the last one
        died; AuthError when it has no logged-in account."""
        if self._client is None:
            self._client = _new_client()
        client = self._client
        try:
            await client.start()
            if not self._authenticated:
                status = await client.get_auth_status()
                if not status.is_authenticated:
                    raise AuthError(NOT_LOGGED_IN_MESSAGE)
                self._authenticated = True
        except LLMError:
            raise
        except Exception as e:
            raise BackendUnavailableError(
                f'無法啟動 Copilot SDK 的執行環境（copilot）：{redact(str(e))[:ERROR_CHARS]}')
        return client

    def close(self) -> None:
        """At interpreter exit: stop the client (and so its runtime), and
        let no call start after it."""
        with self._guard:
            self._closed = True
            loop, client = self._loop, self._client
        if client is None:  # a client exists only on a running loop
            return
        try:
            asyncio.run_coroutine_threadsafe(_stop(client), loop).result(CLEANUP_SECONDS)
        except BaseException:
            pass


async def _stop(client) -> None:
    try:
        await client.stop()
    except Exception:
        await client.force_stop()


_runtime = _Runtime()


@atexit.register
def _close_runtime() -> None:
    _runtime.close()


class CopilotSDK:
    name = 'copilot-sdk'
    default_model = CopilotCLI.default_model
    auth_hint = AUTH_HINT
    capabilities = CopilotCLI.capabilities

    def list_models(self) -> List[ModelInfo]:
        return list(MODELS)

    def complete(self, request: LLMRequest,
                 cancel: threading.Event) -> LLMResponse:
        if cancel.is_set():
            raise CancelledError('cancelled')
        deadline = time.monotonic() + request.timeout
        future = _runtime.submit(self._turn(request, flatten_prompt(request)))
        try:
            while True:
                try:
                    response = future.result(timeout=POLL_SECONDS)
                except TimeoutError:
                    # Still running (the turn raises only LLMErrors)
                    if cancel.is_set():
                        raise CancelledError('cancelled')
                    if time.monotonic() >= deadline:
                        raise TransientError(f'{self.name} timed out after {request.timeout:g} s')
                    continue
                # Finished: still refuse the reply of a cancelled call
                if cancel.is_set():
                    raise CancelledError('cancelled')
                return response
        finally:
            # A turn still running is aborted and its session deleted (on the loop)
            future.cancel()

    async def _turn(self, request: LLMRequest, prompt: str) -> LLMResponse:
        """One request in a fresh session, deleted afterwards. Raises only
        LLMErrors (and asyncio.CancelledError when `complete` gave up)."""
        usages, messages = [], []
        idle = asyncio.Event()

        def record(event) -> None:
            kind = event.type.value
            if kind == 'assistant.usage':
                usages.append(event.data)
            elif kind == 'session.error':
                messages.append(event.data.message)
            elif kind == 'session.idle':
                idle.set()

        client = await _runtime.client()
        # The id is chosen here, so a session whose creation failed half-way
        # is deleted too
        session_id = str(uuid.uuid4())
        session = None
        finished = False
        try:
            session = await client.create_session(session_id=session_id, **session_options(request))
            session.on(record)
            reply = await session.send_and_wait(prompt, timeout=request.timeout)
            finished = True
        except Exception as e:
            raise _error(e, messages)
        finally:
            await self._clean_up(client, session, session_id, finished, idle)
        text = getattr(getattr(reply, 'data', None), 'content', None) or ''
        return LLMResponse(text=text, stop_reason='complete', usage=_usage(usages),
                           model=request.model)

    @staticmethod
    async def _clean_up(client, session, session_id: str, finished: bool,
                        idle: asyncio.Event) -> None:
        """Abort an unfinished turn (waiting briefly for the session to go
        idle), then delete the session. Failures are ignored: a runtime that
        died took its turns with it."""
        try:
            async with asyncio.timeout(CLEANUP_SECONDS):
                if session is not None and not finished:
                    await session.abort()
                    try:
                        await asyncio.wait_for(idle.wait(), ABORT_WAIT_SECONDS)
                    except TimeoutError:
                        pass
                await client.delete_session(session_id)
        except Exception:
            pass


def _usage(usages: list) -> Optional[Usage]:
    """The tokens of the session's model calls, summed; None when it reported none."""
    if not usages:
        return None
    return Usage(sum(u.input_tokens or 0 for u in usages),
                 sum(u.output_tokens or 0 for u in usages))
