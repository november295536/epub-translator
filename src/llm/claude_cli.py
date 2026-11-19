"""Claude Code CLI backend: `claude -p <prompt> --model <alias>`."""

from __future__ import annotations

import re
from typing import List, Optional

from src.llm.base import (
    AuthError, Capabilities, LLMError, LLMRequest, LLMResponse, ModelInfo,
    RateLimitError, TransientError,
)
from src.llm.cli import CLIBackend, ProcessResult, _text

MODELS = [
    ModelInfo('sonnet', 'Sonnet', '快速且高品質，適合大多數翻譯'),
    ModelInfo('opus', 'Opus', '最高品質，費用較高'),
    ModelInfo('haiku', 'Haiku', '最快速度，適合簡單內容'),
]

_RATE_LIMIT_PATTERN = re.compile(r"You've hit your limit", re.IGNORECASE)
_AUTH_ERROR_PATTERN = re.compile(
    r'authentication_error|OAuth token has expired|please run /login',
    re.IGNORECASE,
)

AUTH_MESSAGE = 'Claude CLI 認證已過期，請在終端執行 claude /login 重新登入'


class ClaudeCLI(CLIBackend):
    name = 'claude-cli'
    # Set inside a Claude Code session; the CLI refuses to start nested
    env_remove = ('CLAUDECODE',)
    # Plain-text output: no usage report, system prompt and turns flattened
    capabilities = Capabilities(
        separate_system=False, multi_turn=False, reports_usage=False,
        sampling_params=False, suggested_concurrency=1,
    )

    def list_models(self) -> List[ModelInfo]:
        return list(MODELS)

    def build_command(self, prompt: str, request: LLMRequest) -> List[str]:
        cmd = ['claude', '-p', prompt]
        if request.model:
            cmd += ['--model', request.model]
        return cmd

    def parse_output(self, result: ProcessResult, request: LLMRequest) -> LLMResponse:
        return LLMResponse(text=result.stdout, stop_reason='complete',
                           usage=None, model=request.model)

    def classify_error(self, result: ProcessResult) -> Optional[LLMError]:
        # The CLI may report a limit or an expired login on stdout with
        # exit code 0, so both streams are checked whatever the exit code.
        combined = _text(result.stdout) + _text(result.stderr)
        if _RATE_LIMIT_PATTERN.search(combined):
            return RateLimitError(combined.strip())
        if _AUTH_ERROR_PATTERN.search(combined):
            return AuthError(AUTH_MESSAGE)
        if result.returncode != 0:
            return TransientError(
                f'claude CLI exited with code {result.returncode}: '
                f'{_text(result.stderr).strip()}')
        return None
