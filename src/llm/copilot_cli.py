"""GitHub Copilot CLI backend: `copilot -p <prompt> --model <id> --output-format json`.

Output and error formats as observed with Copilot CLI 1.0.114 on invented
prompts (plan/03-acceptance-log.md, Phase 7 part A):

- `--output-format json` prints one JSON object per line. The reply is the
  `content` of the last `assistant.message` event; the token usage is in the
  last `session.usage_checkpoint` event
  (`data.accountingSnapshot.modelMetrics.<model>.usage`). A successful run
  exits 0 with nothing on stderr.
- A failed run exits non-zero and reports `session.error` events
  (`data.errorType`, `data.message`, e.g. `Execution failed: Model "x" is not
  available.`); stderr may hold a message too.
- Rate limits were not observed. The messages below are the ones
  `copilot help limits` documents ("You've hit the rate limit for this
  model.", "You've hit your session rate limit.", "You've reached your
  weekly rate limit.", "You've hit your rate limit."). Expired or missing
  credentials and refusals could not be produced; their patterns are
  guesses kept narrow, and anything else that fails is transient.

Only error messages and stderr are searched, never the reply itself, so a
translation that mentions a rate limit is never taken for one.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional

from src.llm.base import (
    AuthError, BackendUnavailableError, Capabilities, LLMError, LLMRequest,
    LLMResponse, ModelInfo, RateLimitError, RefusalError, TransientError, Usage,
)
from src.llm.cli import ERROR_CHARS, CLIBackend, ProcessResult, _text

# From the account's model catalog when this backend was written; the
# multiplier is what Copilot counts per request
MODELS = [
    ModelInfo('claude-sonnet-5.5', 'Claude Sonnet 5.5', '品質與速度兼顧（1x）'),
    ModelInfo('gpt-6.2-sol', 'GPT-6.2 Sol', 'OpenAI 模型（1x）'),
    ModelInfo('gemini-4-carbon', 'Gemini 4 Carbon', 'Google 模型，較慢（1x）'),
    ModelInfo('claude-haiku-4.5', 'Claude Haiku 4.5', '最便宜，適合簡單內容（0.33x）'),
]

AUTH_HINT = '請在終端執行 copilot login 重新登入'

# No tool for the model: an unknown tool name leaves none available
# (`--available-tools` with no name keeps all of them)
NO_TOOLS = 'none'

# Every documented message says "rate limit" ("Rate limited." too)
_RATE_LIMIT_PATTERN = re.compile(r'rate.?limit', re.IGNORECASE)
_AUTH_PATTERN = re.compile(r'not (?:logged in|authenticated)|copilot login|unauthori[sz]ed',
                           re.IGNORECASE)
_UNAVAILABLE_PATTERN = re.compile(r'Model "[^"]*" is not available', re.IGNORECASE)
_REFUSAL_PATTERN = re.compile(r'content.?filter|filtered', re.IGNORECASE)


def _events(stdout) -> list:
    """The JSON objects of the output, one per line; other lines skipped."""
    events = []
    for line in _text(stdout).splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _data(event: dict) -> dict:
    data = event.get('data')
    return data if isinstance(data, dict) else {}


def _usage(events: list) -> Optional[Usage]:
    """The tokens of the last usage checkpoint, summed over its models."""
    checkpoints = [e for e in events if e.get('type') == 'session.usage_checkpoint']
    if not checkpoints:
        return None
    metrics = _data(checkpoints[-1]).get('accountingSnapshot', {}).get('modelMetrics', {})
    try:
        usages = [m['usage'] for m in metrics.values()]
        return Usage(sum(int(u['inputTokens']) for u in usages),
                     sum(int(u['outputTokens']) for u in usages)) if usages else None
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


class CopilotCLI(CLIBackend):
    name = 'copilot-cli'
    default_model = 'claude-sonnet-5.5'
    auth_hint = AUTH_HINT
    capabilities = Capabilities(
        separate_system=False, multi_turn=False, reports_usage=True,
        sampling_params=False, suggested_concurrency=1,
    )

    def list_models(self) -> List[ModelInfo]:
        return list(MODELS)

    def build_command(self, prompt: str, request: LLMRequest) -> List[str]:
        cmd = ['copilot', '-p', prompt]
        if request.model:
            cmd += ['--model', request.model]
        return cmd + ['--output-format', 'json', '--available-tools', NO_TOOLS,
                      '--no-custom-instructions', '--disable-builtin-mcps', '--no-ask-user',
                      '--no-auto-update']

    def parse_output(self, result: ProcessResult, request: LLMRequest) -> LLMResponse:
        events = _events(result.stdout)
        messages = [e for e in events if e.get('type') == 'assistant.message']
        text = _text(_data(messages[-1]).get('content')) if messages else ''
        return LLMResponse(text=text, stop_reason='complete', usage=_usage(events),
                           model=request.model)

    def classify_error(self, result: ProcessResult, prompt: str = '') -> Optional[LLMError]:
        errors = [_data(e) for e in _events(result.stdout) if e.get('type') == 'session.error']
        stderr = _text(result.stderr).strip()
        texts = [f"{_text(d.get('errorType'))} {_text(d.get('message'))}".strip()
                 for d in errors] + ([stderr] if stderr else [])
        joined = ' | '.join(texts)[:ERROR_CHARS]
        if result.returncode == 0 and not errors:
            return None
        for pattern, cls in ((_RATE_LIMIT_PATTERN, RateLimitError), (_AUTH_PATTERN, AuthError),
                             (_UNAVAILABLE_PATTERN, BackendUnavailableError),
                             (_REFUSAL_PATTERN, RefusalError)):
            if any(pattern.search(t) for t in texts):
                if cls is AuthError:
                    return AuthError(f'Copilot CLI 認證失敗，{AUTH_HINT}：{joined}')
                return cls(f'copilot CLI: {joined}')
        return TransientError(f'copilot CLI exited with code {result.returncode}: {joined}')
