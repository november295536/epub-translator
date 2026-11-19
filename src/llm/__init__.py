"""Model backends. Callers use the LLMBackend protocol only; the backend in
use comes from default_backend()."""

from src.llm.base import (  # noqa: F401
    AuthError, BackendUnavailableError, CancelledError, Capabilities, LLMBackend, LLMError, LLMRequest,
    LLMResponse, Message, ModelInfo, RateLimitError, RefusalError,
    TransientError, Usage,
)
from src.llm.claude_cli import ClaudeCLI


def default_backend() -> LLMBackend:
    """The backend for new engines and extractors (tests replace this)."""
    return ClaudeCLI()
