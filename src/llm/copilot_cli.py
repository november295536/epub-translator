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

System prompt (Phase 9, plan/03-acceptance-log.md): the CLI has no flag for
it, but an extension can replace it. `--experimental --plugin-dir
PLUGIN_DIR` loads the plugin kept next to this module; its extension reads
the prompt from SYSTEM_PROMPT_ENV, which `environment()` sets to the one
SYSTEM_PROMPT of every CLI backend. An extension that does not load is
silent (exit 0, the full built-in prompt of about 2000 tokens is sent), so
every successful run is checked: the usage checkpoint lists the system
prompt segments sent with each model call (`promptCacheBreakState[].models.
<model>.system_segments`), and with the replacement in effect that is one
segment named REPLACED_SEGMENT (the built-in prompt is 18 segments). A run
without that report, or with any other segment, raises
BackendUnavailableError: the run stops instead of paying for the full prompt
on every call. The report does not depend on the prompt's length. A run in
which Copilot rejected the extension's system message
(`ProtocolSystemMessageConfig`) raises the same error.
"""

from __future__ import annotations

import json
import os
import re
from typing import List, Optional

from src.llm.base import (
    EFFORT_LEVELS, AuthError, BackendUnavailableError, Capabilities, LLMError, LLMRequest,
    LLMResponse, ModelInfo, RateLimitError, RefusalError, TransientError, Usage,
)
from src.llm.cli import (
    ERROR_CHARS, SYSTEM_PROMPT, UNKNOWN_OPTION_PATTERN, CLIBackend, ProcessResult, _text,
)

# From the account's model catalog when this backend was written; the
# multiplier is what Copilot counts per request. Effort levels: the ones the
# CLI (1.0.114) accepted for `--reasoning-effort` with each model (no command
# lists them; they can change with the CLI); Claude Haiku 4.5 accepts none.
MODELS = [
    ModelInfo('claude-sonnet-5.5', 'Claude Sonnet 5.5', '品質與速度兼顧（1x）', EFFORT_LEVELS),
    ModelInfo('gpt-6.2-sol', 'GPT-6.2 Sol', 'OpenAI 模型（1x）', EFFORT_LEVELS),
    ModelInfo('gemini-4-carbon', 'Gemini 4 Carbon', 'Google 模型，較慢（1x）',
              ('low', 'medium', 'high')),
    ModelInfo('claude-haiku-4.5', 'Claude Haiku 4.5', '最便宜，適合簡單內容（0.33x）'),
]

AUTH_HINT = '請在終端執行 copilot login 重新登入'

# No tool for the model: an unknown tool name leaves none available
# (`--available-tools` with no name keeps all of them)
NO_TOOLS = 'none'

# The plugin whose extension replaces the system prompt, and the variable
# it reads the prompt from
PLUGIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'copilot_plugin')
# PLUGIN_DIR as named to the user: relative to the checkout
PLUGIN_LOCATION = 'src/llm/copilot_plugin'
SYSTEM_PROMPT_ENV = 'EPUB_TRANSLATOR_SYSTEM_PROMPT'
# The one system prompt segment the CLI reports when an extension replaced it
REPLACED_SEGMENT = 'agent_replacement'
# The start of the message of every run stopped because the replacement could
# not be verified; what the CLI printed follows it
NOT_REPLACED_MESSAGE = (
    '無法確認 Copilot CLI 已套用翻譯用的系統提示，為免每次呼叫都送出完整的內建提示，已停止。'
    f'可能原因：外掛沒有載入（請確認 copilot 支援 --experimental 與 --plugin-dir，且 {PLUGIN_LOCATION} '
    '資料夾存在），或 Copilot 更新後改變了擴充功能的介面或 system_segments 的回報。')
# Copilot refused the system message the extension passed (observed with the
# extension's environment variable removed, exit 1): the mechanism itself is
# broken, every call would fail
_EXTENSION_REJECTED_PATTERN = re.compile(r'ProtocolSystemMessageConfig')

# Every documented message says "rate limit" ("Rate limited." too)
_RATE_LIMIT_PATTERN = re.compile(r'rate.?limit', re.IGNORECASE)
_AUTH_PATTERN = re.compile(r'not (?:logged in|authenticated)|copilot login|unauthori[sz]ed',
                           re.IGNORECASE)
_UNAVAILABLE_PATTERN = re.compile(r'Model "[^"]*" is not available', re.IGNORECASE)
_REFUSAL_PATTERN = re.compile(r'content.?filter', re.IGNORECASE)
# The model does not take the reasoning effort passed (exit 1 before any model
# call): every call with this setting would fail the same way
_EFFORT_REJECTED_PATTERN = re.compile(
    r'Reasoning effort "[^"]*" is not supported for model'
    r'|does not support reasoning effort configuration', re.IGNORECASE)
EFFORT_REJECTED_MESSAGE = 'Copilot CLI 不接受此模型的推理強度設定，請改選推理強度或模型'

# What looks like an e-mail address (an account name) in kept error text
_EMAIL_PATTERN = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+')


def redact(text: str) -> str:
    """`text` with every e-mail-like string replaced by <redacted>."""
    return _EMAIL_PATTERN.sub('<redacted>', text)


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


def _is_json(line: str) -> bool:
    try:
        json.loads(line)
    except ValueError:
        return False
    return True


def _data(event: dict) -> dict:
    data = event.get('data')
    return data if isinstance(data, dict) else {}


def _usage(events: list) -> Optional[Usage]:
    """The tokens of the last usage checkpoint, summed over its models."""
    checkpoints = [e for e in events if e.get('type') == 'session.usage_checkpoint']
    if not checkpoints:
        return None
    try:
        metrics = _data(checkpoints[-1])['accountingSnapshot']['modelMetrics']
        usages = [m['usage'] for m in metrics.values()]
        return Usage(sum(int(u['inputTokens']) for u in usages),
                     sum(int(u['outputTokens']) for u in usages)) if usages else None
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _replaced(events: list) -> bool:
    """Whether the run has a usage checkpoint and every checkpoint reports at
    least one model call, each sent only the replacement (every real run
    seen reported one call in each checkpoint)."""
    checkpoints = [_data(e) for e in events if e.get('type') == 'session.usage_checkpoint']
    try:
        return bool(checkpoints) and all(
            states and all(
                state['models'] and all(
                    [s['segment'] for s in model['system_segments']] == [REPLACED_SEGMENT]
                    for model in state['models'].values())
                for state in states)
            for states in (c['promptCacheBreakState'] for c in checkpoints))
    except (AttributeError, KeyError, TypeError):
        return False


def _not_replaced(joined: str) -> LLMError:
    return BackendUnavailableError(NOT_REPLACED_MESSAGE + (f'CLI 訊息：{joined}' if joined else ''))


def _replacement_error(events: list, joined: str) -> Optional[LLMError]:
    """None when the system prompt was replaced, else the error that stops the run."""
    return None if _replaced(events) else _not_replaced(joined)


def _reply(events: list) -> str:
    """The content of the last `assistant.message` event ('' when none)."""
    messages = [e for e in events if e.get('type') == 'assistant.message']
    return _text(_data(messages[-1]).get('content')) if messages else ''


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
        if request.effort:
            cmd += ['--reasoning-effort', request.effort]
        return cmd + ['--output-format', 'json', '--available-tools', NO_TOOLS,
                      '--no-custom-instructions', '--disable-builtin-mcps', '--no-ask-user',
                      '--no-auto-update', '--experimental', '--plugin-dir', PLUGIN_DIR]

    def environment(self) -> dict:
        return {**super().environment(), SYSTEM_PROMPT_ENV: SYSTEM_PROMPT}

    def parse_output(self, result: ProcessResult, request: LLMRequest) -> LLMResponse:
        events = _events(result.stdout)
        return LLMResponse(text=_reply(events), stop_reason='complete', usage=_usage(events),
                           model=request.model)

    def redact(self, text: str) -> str:
        return redact(text)

    def classify_error(self, result: ProcessResult, prompt: str = '') -> Optional[LLMError]:
        events = _events(result.stdout)
        errors = [_data(e) for e in events if e.get('type') == 'session.error']
        stderr = _text(result.stderr).strip()
        texts = [redact(f"{_text(d.get('errorType'))} {_text(d.get('message'))}".strip())
                 for d in errors] + ([redact(stderr)] if stderr else [])
        if not errors and (result.returncode != 0 or not _reply(events)):
            # No error event and no reply: a plain-text message on stdout is the error
            plain = ' '.join(line.strip() for line in _text(result.stdout).splitlines()
                             if line.strip() and not _is_json(line))
            if plain:
                texts.insert(0, redact(plain))
        joined = ' | '.join(texts)[:ERROR_CHARS]
        # A successful run with a reply fails only for a rate limit or
        # expired credentials; any other error event beside the reply is ignored
        answered = result.returncode == 0 and bool(_reply(events))
        if answered and not errors:
            return _replacement_error(events, joined)

        def named(cls):
            return lambda: cls(f'copilot CLI: {joined}')

        # (pattern, the error it means), the first match wins
        patterns = ((_RATE_LIMIT_PATTERN, named(RateLimitError)),
                    (_AUTH_PATTERN, lambda: AuthError(f'Copilot CLI 認證失敗，{AUTH_HINT}：{joined}')))
        if not answered:
            patterns += ((_EFFORT_REJECTED_PATTERN,
                          lambda: BackendUnavailableError(f'{EFFORT_REJECTED_MESSAGE}：{joined}')),
                         (_UNAVAILABLE_PATTERN, named(BackendUnavailableError)),
                         (UNKNOWN_OPTION_PATTERN, named(BackendUnavailableError)),
                         (_EXTENSION_REJECTED_PATTERN, lambda: _not_replaced(joined)),
                         (_REFUSAL_PATTERN, named(RefusalError)))
        for pattern, error in patterns:
            if any(pattern.search(t) for t in texts):
                return error()
        # A reply, or a clean exit without one and without a recognized message
        if answered or (result.returncode == 0 and not errors):
            return _replacement_error(events, joined)
        return TransientError(f'copilot CLI exited with code {result.returncode}: {joined}')
