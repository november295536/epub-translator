"""Base class for backends that run a command-line program per call.

This module and its subclasses are the only place where the application
starts processes for model calls.
"""

from __future__ import annotations

import atexit
import errno
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

from src.llm.base import (
    BackendUnavailableError, CancelledError, LLMError, LLMRequest, LLMResponse,
    ModelInfo, TransientError,
)

# How often a running process is checked for cancellation (latency only:
# each wait is also bounded by the deadline)
POLL_SECONDS = 0.1

# How long to wait for a killed process's pipes to close: a descendant that
# left the process group can keep them open
KILL_WAIT_SECONDS = 2

# Characters of output kept from a call that timed out or was killed
KEPT_OUTPUT_CHARS = 20000
# An option the installed CLI does not know (an older version): no retry helps
UNKNOWN_OPTION_PATTERN = re.compile(r'unknown option|unrecogni[sz]ed (?:option|argument)',
                                    re.IGNORECASE)

# Replaces the CLI's own (coding agent) system prompt in every CLI backend:
# Claude through --system-prompt, Copilot through its plugin (copilot_cli.py)
SYSTEM_PROMPT = '你是一位專業的英文翻譯。'

# Start errors caused by a temporary shortage of system resources
RESOURCE_ERRNOS = (errno.EAGAIN, errno.ENOMEM, errno.EMFILE, errno.ENFILE)

# Process groups of calls in progress, killed at interpreter exit
_live_groups: set = set()
_live_groups_guard = threading.Lock()
# Set by the exit hook (under the guard): no process is started after it
_shutting_down = False


@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


def _kill_group(proc: subprocess.Popen) -> tuple:
    """Kill the process and anything it started (it leads its own session),
    reap it and return the (stdout, stderr) it wrote, each bounded."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        stdout, stderr = proc.communicate(timeout=KILL_WAIT_SECONDS)
    except subprocess.TimeoutExpired as e:
        # A descendant outside the group holds the pipes: give up on them,
        # keeping what was captured so far (bytes, even in text mode)
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
        proc.wait()
        stdout, stderr = _decoded(e.output), _decoded(e.stderr)
    return _text(stdout)[:KEPT_OUTPUT_CHARS], _text(stderr)[:KEPT_OUTPUT_CHARS]


def _decoded(value) -> str:
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return _text(value)


def _kill_live_groups() -> None:
    """At interpreter exit: kill the process groups of calls still running
    (e.g. in daemon worker threads), and let no new one start."""
    global _shutting_down
    with _live_groups_guard:
        _shutting_down = True
        groups = list(_live_groups)
    for pgid in groups:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


atexit.register(_kill_live_groups)


def run_process(cmd: List[str], *, timeout: float, cwd: str, env: dict,
                cancel: Optional[threading.Event] = None) -> ProcessResult:
    """Run cmd to completion and capture its output.

    Raises subprocess.TimeoutExpired (with the output written so far) when
    the process has not finished within `timeout` seconds, and
    CancelledError when `cancel` is set before or during the call; the
    output of such a call is never returned. On these and on any other
    exception (e.g. KeyboardInterrupt) the process and its children are
    killed and reaped first. stdin is empty, so the program never waits for
    input.
    """
    if cancel is not None and cancel.is_set():
        raise CancelledError('cancelled')
    deadline = time.monotonic() + timeout
    # Started and registered under the guard: the exit hook either sees the
    # process or stops it from starting
    with _live_groups_guard:
        if _shutting_down:
            raise CancelledError('shutting down')
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, encoding='utf-8', errors='replace',
            cwd=cwd, env=env,
            start_new_session=True,
        )
        _live_groups.add(proc.pid)
    try:
        while True:
            wait = min(POLL_SECONDS, max(0.0, deadline - time.monotonic()))
            try:
                # Retrying communicate() after a timeout loses no output
                stdout, stderr = proc.communicate(timeout=wait)
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    raise CancelledError('cancelled')
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(cmd, timeout)  # output added below
                continue
            # Finished: still refuse the output of a cancelled or late call
            if cancel is not None and cancel.is_set():
                raise CancelledError('cancelled')
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(cmd, timeout,
                                                output=stdout[:KEPT_OUTPUT_CHARS],
                                                stderr=stderr[:KEPT_OUTPUT_CHARS])
            return ProcessResult(proc.returncode, stdout, stderr)
    except BaseException as e:
        # Also when the program itself has ended: what it started may still run
        stdout, stderr = _kill_group(proc)
        if isinstance(e, subprocess.TimeoutExpired) and e.output is None:
            e.output, e.stderr = stdout, stderr  # timed out while running
        raise
    finally:
        with _live_groups_guard:
            _live_groups.discard(proc.pid)


def flatten_prompt(request: LLMRequest) -> str:
    """One prompt for a program that takes neither a separate system prompt
    nor several turns. A single message with no system prompt is passed
    through unchanged."""
    parts = [request.system] if request.system else []
    if len(request.messages) == 1:
        parts.append(request.messages[0].content)
    else:
        parts.extend(f'[{m.role}]\n{m.content}' for m in request.messages)
    return '\n\n'.join(parts)


class CLIBackend:
    """Owns process start, timeout, cancellation, environment and working
    directory. Subclasses supply build_command, parse_output,
    classify_error, list_models, default_model and auth_hint."""

    # Environment variables removed before starting the program
    env_remove = ()

    def redact(self, text: str) -> str:
        """Output kept for the diagnostics, with what must not be kept
        removed (subclasses; the base class keeps everything)."""
        return text

    def environment(self) -> dict:
        return {k: v for k, v in os.environ.items() if k not in self.env_remove}

    def working_directory(self) -> str:
        # Neutral directory: keeps the program away from the caller's
        # repository and project settings
        return tempfile.gettempdir()

    def complete(self, request: LLMRequest,
                 cancel: threading.Event) -> LLMResponse:
        prompt = flatten_prompt(request)
        cmd = self.build_command(prompt, request)
        try:
            result = run_process(cmd, timeout=request.timeout,
                                 cwd=self.working_directory(),
                                 env=self.environment(), cancel=cancel)
        except subprocess.TimeoutExpired as e:
            # What the program wrote before it hung, for the diagnostics
            raise TransientError(f'{self.name} timed out after {request.timeout:g} s',
                                 raw=self.redact(_text(e.output) + _text(e.stderr))[:KEPT_OUTPUT_CHARS])
        except OSError as e:
            message = f'{self.name} could not be started: {str(e)[:ERROR_CHARS]}'
            if isinstance(e, (FileNotFoundError, PermissionError)) or e.errno == errno.ENOEXEC:
                # Missing or not executable: every call would fail the same way
                raise BackendUnavailableError(message)
            if e.errno in RESOURCE_ERRNOS:
                # Out of processes, memory or file descriptors for now: retried
                raise TransientError(message)
            # Anything else (e.g. E2BIG for a very long prompt) concerns this
            # request: not retried, the segment fails
            raise LLMError(message)
        error = self.classify_error(result, prompt)
        if error is not None:
            error.raw = self.redact(_text(result.stdout) + _text(result.stderr))[:KEPT_OUTPUT_CHARS]
            raise error
        return self.parse_output(result, request)

    def build_command(self, prompt: str, request: LLMRequest) -> List[str]:
        raise NotImplementedError

    def parse_output(self, result: ProcessResult, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError

    def classify_error(self, result: ProcessResult, prompt: str = '') -> Optional[LLMError]:
        """Return the error a finished run represents, or None on success.
        `prompt` is the request text that was sent."""
        raise NotImplementedError

    def list_models(self) -> List[ModelInfo]:
        raise NotImplementedError


def _text(value) -> str:
    return value if isinstance(value, str) else ''


# Characters of program output quoted in an error message
ERROR_CHARS = 500
