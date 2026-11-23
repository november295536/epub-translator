"""Model backends. Callers use the LLMBackend protocol only; the backend of
a project comes from backend_for(its backend name)."""

from __future__ import annotations

from src.llm.base import (  # noqa: F401
    EFFORT_LEVELS, KNOWN_LEVELS, AuthError, BackendUnavailableError, CallTimeoutError, CancelledError, Capabilities, LLMBackend, LLMError, LLMRequest,
    LLMResponse, Message, ModelInfo, RateLimitError, RefusalError,
    TransientError, Usage,
)
from src.llm.claude_cli import ClaudeCLI
from src.llm.copilot_cli import CopilotCLI
from src.llm.copilot_sdk import CopilotSDK

# The backends a project can name (project.json `backend`), by name
BACKENDS = {ClaudeCLI.name: ClaudeCLI, CopilotCLI.name: CopilotCLI, CopilotSDK.name: CopilotSDK}


def default_backend() -> LLMBackend:
    """The backend of a project that names none (tests replace this)."""
    return ClaudeCLI()


def backend_for(name: str | None) -> LLMBackend:
    """The backend named `name`; None: default_backend(). An unknown name
    raises ValueError."""
    if name is None:
        return default_backend()
    if name not in BACKENDS:
        raise ValueError(f'unknown backend: {name!r}')
    return BACKENDS[name]()
