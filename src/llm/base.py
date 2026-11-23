"""LLM backend interface: request, response, capabilities and errors.

Shaped around what an HTTP API can do; CLI backends degrade where they
cannot (see CLIBackend in src/llm/cli.py).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import List, Optional, Protocol, Tuple

# The reasoning effort levels both CLIs know, lowest first (each model
# declares the ones it takes in ModelInfo.efforts)
EFFORT_LEVELS = ('low', 'medium', 'high', 'xhigh', 'max')
# Every level a model may declare: also Copilot's `none`, which some of its
# models take (Claude Code knows no such value)
KNOWN_LEVELS = ('none',) + EFFORT_LEVELS


@dataclass
class Message:
    role: str      # user | assistant
    content: str


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class LLMRequest:
    system: str                       # stable instructions; backend may cache
    messages: List[Message]           # one message, or three for retry-with-feedback
    model: str
    timeout: float
    temperature: Optional[float] = None  # hints; ignored if unsupported
    max_output_tokens: Optional[int] = None
    effort: Optional[str] = None      # a level the model accepts; None: pass none


@dataclass
class LLMResponse:
    text: str
    stop_reason: str                  # complete | truncated | refusal
    usage: Optional[Usage]            # None when the backend cannot report it
    model: str


@dataclass
class Capabilities:
    separate_system: bool
    multi_turn: bool
    reports_usage: bool
    sampling_params: bool
    suggested_concurrency: int


@dataclass
class ModelInfo:
    alias: str
    label: str
    description: str
    # The reasoning effort levels the model accepts (of KNOWN_LEVELS);
    # empty: it cannot be configured, and no level is passed
    efforts: Tuple[str, ...] = ()


class LLMError(Exception):
    """A model call failed. `raw` holds the backend's raw output, if any,
    for failure diagnostics."""

    def __init__(self, message: str, raw: str = ''):
        super().__init__(message)
        self.raw = raw


class RateLimitError(LLMError):
    """Usage limit reached. `retry_after` in seconds when the backend knows it."""

    def __init__(self, message: str, raw: str = '',
                 retry_after: Optional[float] = None):
        super().__init__(message, raw)
        self.retry_after = retry_after


class AuthError(LLMError):
    """Credentials missing or expired."""


class TransientError(LLMError):
    """A failure worth retrying: timeout, non-zero exit, connection error."""


class CallTimeoutError(TransientError):
    """The call did not finish within the request's timeout (a
    TransientError: retried like one; told apart only in the event record)."""


class BackendUnavailableError(LLMError):
    """The backend cannot be used at all (e.g. its program is missing):
    concerns the run, not one request."""


class RefusalError(LLMError):
    """The model declined to answer."""


class CancelledError(LLMError):
    """The call was cancelled through its cancel event."""


class LLMBackend(Protocol):
    name: str
    capabilities: Capabilities
    default_model: str     # alias used when a project names none
    auth_hint: str         # what the user should do after an AuthError

    def list_models(self) -> List[ModelInfo]: ...

    def complete(self, request: LLMRequest,
                 cancel: threading.Event) -> LLMResponse: ...
