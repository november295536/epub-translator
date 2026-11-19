"""Scripted backend for tests: no process, no network."""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

from src.llm.base import Capabilities, LLMRequest, LLMResponse, ModelInfo


class FakeBackendExhausted(BaseException):
    """A scripted FakeBackend was called more often than it has replies. A
    BaseException, so translate_segment's catch-all cannot turn it into a
    FAILED outcome and the test fails."""


class FakeBackend:
    """Answers each call with the next scripted reply, or with
    `responder(request)` when given. A reply that is an exception is raised.
    Every request is recorded in `requests`. A call after the last scripted
    reply raises FakeBackendExhausted and is recorded in `overcalls` (class
    level: a test fixture checks it also for calls in worker threads)."""

    overcalls: List[LLMRequest] = []

    name = 'fake'
    default_model = 'fake-model'
    auth_hint = 'fake: sign in again'
    capabilities = Capabilities(
        separate_system=True, multi_turn=True, reports_usage=True,
        sampling_params=True, suggested_concurrency=4,
    )

    # A reply is a str, an LLMResponse or an exception to raise
    def __init__(self, replies: Optional[list] = None,
                 responder: Optional[Callable[[LLMRequest], object]] = None):
        self.replies = list(replies or [])
        self.responder = responder
        self.requests: List[LLMRequest] = []
        self._lock = threading.Lock()

    def list_models(self) -> List[ModelInfo]:
        return [ModelInfo('fake-model', 'Fake', '')]

    def complete(self, request: LLMRequest,
                 cancel: threading.Event) -> LLMResponse:
        with self._lock:
            self.requests.append(request)
            if self.responder is None:
                if not self.replies:
                    FakeBackend.overcalls.append(request)
                    raise FakeBackendExhausted('FakeBackend: no scripted reply left')
                reply = self.replies.pop(0)
        if self.responder is not None:
            reply = self.responder(request)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, str):
            return LLMResponse(text=reply, stop_reason='complete', usage=None,
                               model=request.model)
        return reply

    @property
    def prompts(self) -> List[str]:
        """The last message of every request, in call order."""
        with self._lock:
            return [r.messages[-1].content for r in self.requests]
