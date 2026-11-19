"""Base class for backends that run a command-line program per call.

This module and its subclasses are the only place where the application
starts processes for model calls.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

from src.llm.base import (
    CancelledError, LLMError, LLMRequest, LLMResponse, ModelInfo, TransientError,
)

# How often a running process is checked for cancellation and the deadline
POLL_SECONDS = 0.1


@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill the process and anything it started (it leads its own session)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    proc.communicate()


def run_process(cmd: List[str], *, timeout: float, cwd: str, env: dict,
                cancel: Optional[threading.Event] = None) -> ProcessResult:
    """Run cmd to completion and capture its output.

    Raises subprocess.TimeoutExpired after `timeout` seconds and
    CancelledError as soon as `cancel` is set; in both cases the process and
    its children are killed first. stdin is empty, so the program never
    waits for input.
    """
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, cwd=cwd, env=env,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            # Retrying communicate() after a timeout loses no output
            stdout, stderr = proc.communicate(timeout=POLL_SECONDS)
            return ProcessResult(proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            if cancel is not None and cancel.is_set():
                _kill_group(proc)
                raise CancelledError('cancelled')
            if time.monotonic() >= deadline:
                _kill_group(proc)
                raise subprocess.TimeoutExpired(cmd, timeout)


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
    classify_error and list_models."""

    name = 'cli'
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
        except subprocess.TimeoutExpired:
            raise TransientError(f'{self.name} timed out after {request.timeout:g} s')
        except OSError as e:
            raise LLMError(f'{self.name} could not be started: {e}')
        error = self.classify_error(result)
        if error is not None:
            error.raw = _text(result.stdout) + _text(result.stderr)
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
