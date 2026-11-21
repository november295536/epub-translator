"""Phase 9: Copilot's system prompt replaced through the plugin, and a run
stopped when the replacement did not take effect (fake process, no model)."""

import json
import os
import threading

import pytest
from click.testing import CliRunner

import src.llm as llm
import translate
from src.core.project import ProjectManager, SegmentStatus
from src.llm import BackendUnavailableError, LLMRequest, Message, RateLimitError, Usage
from src.llm import claude_cli, copilot_cli
from src.llm.cli import SYSTEM_PROMPT, ProcessResult
from src.llm.copilot_cli import (
    NOT_REPLACED_MESSAGE, PLUGIN_DIR, REPLACED_SEGMENT, SYSTEM_PROMPT_ENV, CopilotCLI,
)
from tests.conftest import echo_reply
from tests.test_runtime import _make_project

REPLACED = [{'segment': REPLACED_SEGMENT, 'hash': '66b1b00b4dea', 'tokens': 14}]
# The names of the built-in prompt's segments, as Copilot CLI 1.0.114 reports them
BUILT_IN = [{'segment': name, 'hash': 'h', 'tokens': 100} for name in (
    'customized_identity_preamble', 'interaction_mode', 'tone_and_style', 'search_and_delegation',
    'tool_efficiency', 'version_information', 'model_information', 'environment_context',
    'identity_task_instructions', 'code_change_instructions', 'dynamic_guidelines',
    'environment_limitations', 'tool_intro', 'tool_instructions', 'system_notifications',
    'host_additional_instructions', 'workspace_context', 'final_instructions')]


def _checkpoint(models, tokens=(90, 20)):
    """A usage checkpoint whose cache state reports `models` {name: segments}."""
    return {'type': 'session.usage_checkpoint', 'data': {
        'accountingSnapshot': {'modelMetrics': {'m': {'usage': {
            'inputTokens': tokens[0], 'outputTokens': tokens[1]}}}},
        'promptCacheBreakState': [{'conversation': 'main', 'models': {
            name: {'model': name, 'system_segments': segments}
            for name, segments in models.items()}}]}}


def _output(reply, *checkpoints, code=0, extra=()):
    events = [{'type': 'session.start', 'data': {}}, *extra,
              {'type': 'assistant.message', 'data': {'content': reply}}, *checkpoints,
              {'type': 'result', 'exitCode': code}]
    return ProcessResult(code, '\n'.join(json.dumps(e) for e in events) + '\n', '')


def _req(prompt='PROMPT', model='claude-haiku-4.5'):
    return LLMRequest(system='', messages=[Message('user', prompt)], model=model, timeout=9)


@pytest.fixture
def process(monkeypatch):
    """Replace the process: `respond(cmd)` gives its result; calls are recorded."""
    calls = []
    holder = {}

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return holder['respond'](cmd)

    monkeypatch.setattr('src.llm.cli.run_process', run)

    def use(respond):
        holder['respond'] = respond
        return calls
    return use


class TestPluginAndCommand:

    def test_command_and_environment(self, process):
        calls = process(lambda cmd: _output('答', _checkpoint({'m': REPLACED})))
        CopilotCLI().complete(_req('THE PROMPT'), threading.Event())
        cmd, kwargs = calls[0]
        assert cmd == ['copilot', '-p', 'THE PROMPT', '--model', 'claude-haiku-4.5',
                       '--output-format', 'json', '--available-tools', 'none',
                       '--no-custom-instructions', '--disable-builtin-mcps', '--no-ask-user',
                       '--no-auto-update', '--experimental', '--plugin-dir', PLUGIN_DIR]
        # the prompt the extension hands to Copilot
        assert kwargs['env'][SYSTEM_PROMPT_ENV] == SYSTEM_PROMPT == '你是一位專業的英文翻譯。'
        assert SYSTEM_PROMPT_ENV == 'EPUB_TRANSLATOR_SYSTEM_PROMPT'

    def test_environment_keeps_the_rest(self, monkeypatch):
        monkeypatch.setenv('SOME_VARIABLE', 'kept')
        env = CopilotCLI().environment()
        assert env['SOME_VARIABLE'] == 'kept' and env[SYSTEM_PROMPT_ENV] == SYSTEM_PROMPT

    def test_one_prompt_for_both_backends(self):
        assert claude_cli.SYSTEM_PROMPT is SYSTEM_PROMPT is copilot_cli.SYSTEM_PROMPT
        assert claude_cli.LEAN_FLAGS[:2] == ('--system-prompt', SYSTEM_PROMPT)

    def test_plugin_in_the_repository(self):
        assert os.path.isabs(PLUGIN_DIR)
        assert PLUGIN_DIR == os.path.join(os.path.dirname(os.path.abspath(copilot_cli.__file__)),
                                          'copilot_plugin')
        with open(os.path.join(PLUGIN_DIR, 'plugin.json')) as f:
            manifest = json.load(f)
        assert set(manifest) == {'name', 'version', 'description'}
        with open(os.path.join(PLUGIN_DIR, 'extensions', 'system-prompt', 'extension.mjs')) as f:
            source = f.read()
        assert 'import { joinSession } from "@microsoft/copilot-sdk/extension";' in source
        assert (f'systemMessage: {{ mode: "replace", content: process.env.{SYSTEM_PROMPT_ENV} }}'
                in source)


class TestReplacementChecked:

    def test_replaced_reply_is_returned(self, process):
        process(lambda cmd: _output('答', _checkpoint({'m': REPLACED})))
        response = CopilotCLI().complete(_req(), threading.Event())
        assert response.text == '答' and response.usage == Usage(90, 20)

    def test_long_prompt_is_no_alarm(self, process):
        """The check reads the reported system segments, not the input
        tokens: a long prompt (many tokens) with the replacement passes."""
        prompt = 'Lorem ipsum dolor sit amet. ' * 4000
        process(lambda cmd: _output('答', _checkpoint({'m': REPLACED}, tokens=(30000, 900))))
        response = CopilotCLI().complete(_req(prompt), threading.Event())
        assert response.text == '答' and response.usage.input_tokens == 30000

    @pytest.mark.parametrize('checkpoints', [
        [_checkpoint({'m': BUILT_IN}, tokens=(2127, 200))],    # extension not loaded
        [_checkpoint({'m': REPLACED + BUILT_IN[:1]})],        # replaced and more
        [_checkpoint({'m': REPLACED, 'n': BUILT_IN})],        # one model call not replaced
        [_checkpoint({'m': REPLACED}), _checkpoint({'m': BUILT_IN})],
        [_checkpoint({'m': []})],                             # no system prompt reported
        [_checkpoint({})],                                    # no model call reported
        [{'type': 'session.usage_checkpoint', 'data': {}}],   # no cache state
        [{'type': 'session.usage_checkpoint', 'data': {'promptCacheBreakState': []}}],
        [{'type': 'session.usage_checkpoint', 'data': {'promptCacheBreakState': [{}]}}],
        [{'type': 'session.usage_checkpoint', 'data': {'promptCacheBreakState': [
            {'models': {'m': {}}}]}}],
        [{'type': 'session.usage_checkpoint', 'data': {'promptCacheBreakState': [
            {'models': ['m']}]}}],
        [{'type': 'session.usage_checkpoint', 'data': {'promptCacheBreakState': [
            {'models': {'m': {'system_segments': [{'name': REPLACED_SEGMENT}]}}}]}}],
        [{'type': 'session.usage_checkpoint', 'data': {'promptCacheBreakState': 5}}],
        [],                                                   # no checkpoint at all
    ])
    def test_not_replaced_stops(self, process, checkpoints):
        process(lambda cmd: _output('答', *checkpoints))
        with pytest.raises(BackendUnavailableError) as info:
            CopilotCLI().complete(_req(), threading.Event())
        assert str(info.value) == NOT_REPLACED_MESSAGE
        assert '"assistant.message"' in info.value.raw  # the output, for the diagnostics

    def test_message(self):
        assert NOT_REPLACED_MESSAGE == (
            'Copilot CLI 沒有套用翻譯用的系統提示（擴充功能未載入），為免每次呼叫都送出完整的內建提示，'
            f'已停止。請確認 copilot 版本支援 --experimental 與 --plugin-dir，且外掛資料夾存在：{PLUGIN_DIR}')

    def test_answer_beside_an_ignored_error_is_checked_too(self):
        error = {'type': 'session.error', 'data': {'errorType': 'x', 'message': 'minor'}}
        replaced = _output('答', _checkpoint({'m': REPLACED}), extra=[error])
        assert CopilotCLI().classify_error(replaced) is None
        built_in = _output('答', _checkpoint({'m': BUILT_IN}), extra=[error])
        assert str(CopilotCLI().classify_error(built_in)) == NOT_REPLACED_MESSAGE

    def test_a_failed_run_keeps_its_class(self):
        limit = {'type': 'session.error', 'data': {'errorType': 'query',
                                                  'message': "You've hit your rate limit."}}
        found = CopilotCLI().classify_error(_output('', code=1, extra=[limit]))
        assert type(found) is RateLimitError
        # a reply with a rate limit beside it is a rate limit, replaced or not
        found = CopilotCLI().classify_error(_output('答', extra=[limit]))
        assert type(found) is RateLimitError


def _copilot_project(tmp_path, n):
    path = _make_project(tmp_path, n=n)
    pm = ProjectManager(path)
    pm.load()
    pm.set_model('claude-haiku-4.5', backend='copilot-cli')
    pm.save()
    return path


class TestRunStops:
    """A run through the real CopilotCLI (fake process) stops at the first
    reply without the replacement and stores nothing for it."""

    @pytest.mark.parametrize('workers', ['1', '2'])
    def test_cli_run_stops_and_stores_nothing(self, tmp_path, process, workers):
        path = _copilot_project(tmp_path, n=3)
        calls = process(lambda cmd: _output(echo_reply(cmd[2]), _checkpoint({'m': BUILT_IN})))
        result = CliRunner().invoke(translate.cli, ['start', '--project', path,
                                                    '--workers', workers])
        assert result.exit_code == 1, result.output
        assert NOT_REPLACED_MESSAGE in result.output
        assert 1 <= len(calls) <= int(workers)  # no retry; at most one call per worker
        pm = ProjectManager(path)
        pm.load()
        assert [s.status for s in pm.data.segments] == [SegmentStatus.PENDING] * 3
        assert all(s.translated_text in (None, '') for s in pm.data.segments)

    def test_cli_run_with_replacement_completes(self, tmp_path, process):
        path = _copilot_project(tmp_path, n=3)
        calls = process(lambda cmd: _output(echo_reply(cmd[2]), _checkpoint({'m': REPLACED})))
        result = CliRunner().invoke(translate.cli, ['start', '--project', path, '--workers', '2'])
        assert result.exit_code == 0, result.output
        assert len(calls) == 3
        assert all(c[0][-2:] == ['--plugin-dir', PLUGIN_DIR] for c in calls)
        pm = ProjectManager(path)
        pm.load()
        assert [s.status for s in pm.data.segments] == [SegmentStatus.TRANSLATED] * 3

    def test_backend_registered(self):
        assert llm.BACKENDS['copilot-cli'] is CopilotCLI
