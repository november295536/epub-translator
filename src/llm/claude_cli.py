"""Claude Code CLI backend: `claude -p <prompt> --model <alias>`."""

from __future__ import annotations

import re
from typing import List, Optional

from src.llm.base import (
    AuthError, Capabilities, LLMError, LLMRequest, LLMResponse, ModelInfo,
    RateLimitError, TransientError,
)
from src.llm.cli import ERROR_CHARS, CLIBackend, ProcessResult, _text

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

AUTH_HINT = '請在終端執行 claude /login 重新登入'
AUTH_MESSAGE = f'Claude CLI 認證已過期，{AUTH_HINT}'

# A successful run (exit 0) is searched for the limit and login patterns
# only when its stdout is at most this long. main searched stdout + stderr
# whatever the exit code, because the CLI can print these errors as a short
# reply with exit code 0; that is kept. But a translation can contain the
# same words, so a longer reply is not searched, and a phrase that the
# request itself contains (e.g. code returned unchanged) is not taken for an
# error in a successful run. Accepted risk: a short reply that quotes one of
# the phrases on its own is still taken for an error.
SHORT_REPLY_CHARS = 300


class ClaudeCLI(CLIBackend):
    name = 'claude-cli'
    default_model = 'sonnet'
    auth_hint = AUTH_HINT
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

    def classify_error(self, result: ProcessResult, prompt: str = '') -> Optional[LLMError]:
        stdout, stderr = _text(result.stdout), _text(result.stderr)
        # stderr and any failed run: both streams. A successful run: only a
        # short reply (see SHORT_REPLY_CHARS), so translated text that
        # mentions e.g. "authentication_error" is not taken for an error.
        if result.returncode != 0 or len(stdout) <= SHORT_REPLY_CHARS:
            searched = stdout + stderr
        else:
            searched = stderr
        # A successful run that only repeats a phrase of the request
        echoed = prompt.lower() if result.returncode == 0 else ''

        def found(pattern) -> bool:
            return any(m.group(0).lower() not in echoed for m in pattern.finditer(searched))

        if found(_RATE_LIMIT_PATTERN):
            # Quoted from the stream that holds it: stdout can be long
            source = stderr if _RATE_LIMIT_PATTERN.search(stderr) else stdout
            return RateLimitError(source.strip()[:ERROR_CHARS])
        if found(_AUTH_ERROR_PATTERN):
            return AuthError(AUTH_MESSAGE)
        if result.returncode != 0:
            return TransientError(
                f'claude CLI exited with code {result.returncode}: '
                f'{stderr.strip()[:ERROR_CHARS]}')
        return None
