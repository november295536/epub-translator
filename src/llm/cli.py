"""Base class for backends that run a command-line program per call.

This module and its subclasses are the only place where the application
starts processes for model calls.
"""

from __future__ import annotations

import atexit
import os
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

# Process groups of calls in progress, killed at interpreter exit
_live_groups: set = set()
_live_groups_guard = threading.Lock()


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
    except subprocess.TimeoutExpired:
        # A descendant outside the group holds the pipes: give up on them
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
        proc.wait()
        stdout = stderr = ''
    return _text(stdout)[:KEPT_OUTPUT_CHARS], _text(stderr)[:KEPT_OUTPUT_CHARS]


def _kill_live_groups() -> None:
    """At interpreter exit: kill the process groups of calls still running
    (e.g. in daemon worker threads)."""
    with _live_groups_guard:
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
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, cwd=cwd, env=env,
        start_new_session=True,
    )
    with _live_groups_guard:
        _live_groups.add(proc.pid)
    try:
        while True:
            wait = min(POLL_SECONDS, max(0.0, deadline - time.monotonic()))
            try:
                # Retrying communicate() after a timeout loses no output
                stdout, stderr = proc.communicate(timeout=wait)
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    _kill_group(proc)
                    raise CancelledError('cancelled')
                if time.monotonic() >= deadline:
                    stdout, stderr = _kill_group(proc)
                    raise subprocess.TimeoutExpired(cmd, timeout, output=stdout,
                                                    stderr=stderr)
                continue
            # Finished: still refuse the output of a cancelled or late call
            if cancel is not None and cancel.is_set():
                raise CancelledError('cancelled')
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)
            return ProcessResult(proc.returncode, stdout, stderr)
    except BaseException:
        if proc.returncode is None:
            _kill_group(proc)
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

    def environment(self) -> dict:
        return {k: v for k, v in os.environ.items() if k not in self.env_remove}

    def working_directory(self) -> str:
        # Neutral directory: keeps the program away from the caller's
        # repository and project settings
        return tempfile.gettempdir()

    def complete(self, request: LLMRequest,
                 cancel: threading.Event) -> LLMResponse:
        cmd = self.build_command(flatten_prompt(request), request)
        try:
            result = run_process(cmd, timeout=request.timeout,
                                 cwd=self.working_directory(),
                                 env=self.environment(), cancel=cancel)
        except subprocess.TimeoutExpired as e:
            # What the program wrote before it hung, for the diagnostics
            raise TransientError(f'{self.name} timed out after {request.timeout:g} s',
                                 raw=_text(e.output) + _text(e.stderr))
        except OSError as e:
            # Missing or not executable: every call would fail the same way
            raise BackendUnavailableError(
                f'{self.name} could not be started: {str(e)[:ERROR_CHARS]}')
        error = self.classify_error(result)
        if error is not None:
            error.raw = (_text(result.stdout) + _text(result.stderr))[:KEPT_OUTPUT_CHARS]
            raise error
        return self.parse_output(result, request)

    def build_command(self, prompt: str, request: LLMRequest) -> List[str]:
        raise NotImplementedError

    def parse_output(self, result: ProcessResult, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError

    def classify_error(self, result: ProcessResult) -> Optional[LLMError]:
        """Return the error a finished run represents, or None on success."""
        raise NotImplementedError

    def list_models(self) -> List[ModelInfo]:
        raise NotImplementedError


def _text(value) -> str:
    return value if isinstance(value, str) else ''


# Characters of program output quoted in an error message
ERROR_CHARS = 500
