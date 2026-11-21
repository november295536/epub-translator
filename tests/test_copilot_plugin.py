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
    NOT_REPLACED_MESSAGE, PLUGIN_DIR, PLUGIN_LOCATION, REPLACED_SEGMENT, SYSTEM_PROMPT_ENV,
    CopilotCLI,
)
from src.llm import AuthError, TransientError
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
        # review round 1, A: every checkpoint must report (real runs: one call each)
        [_checkpoint({'m': REPLACED}),
         {'type': 'session.usage_checkpoint', 'data': {'promptCacheBreakState': []}}],
        [{'type': 'session.usage_checkpoint', 'data': {'promptCacheBreakState': []}},
         _checkpoint({'m': REPLACED})],
        [_checkpoint({'m': REPLACED}), _checkpoint({})],
        [_checkpoint({}), _checkpoint({'m': REPLACED})],
        [{'type': 'session.usage_checkpoint', 'data': {'promptCacheBreakState': [
            {'models': {'m': {'system_segments': REPLACED}}}, {'models': {}}]}}],
        [{'type': 'session.usage_checkpoint', 'data': {'promptCacheBreakState': {}}}],
    ])
    def test_not_replaced_stops(self, process, checkpoints):
        process(lambda cmd: _output('答', *checkpoints))
        with pytest.raises(BackendUnavailableError) as info:
            CopilotCLI().complete(_req(), threading.Event())
        assert str(info.value) == NOT_REPLACED_MESSAGE
        assert '"assistant.message"' in info.value.raw  # set by CLIBackend.complete

    def test_message(self):
        assert NOT_REPLACED_MESSAGE == (
            '無法確認 Copilot CLI 已套用翻譯用的系統提示，為免每次呼叫都送出完整的內建提示，已停止。'
            '可能原因：外掛沒有載入（請確認 copilot 支援 --experimental 與 --plugin-dir，且 '
            'src/llm/copilot_plugin 資料夾存在），或 Copilot 更新後改變了擴充功能的介面或 '
            'system_segments 的回報。')
        assert PLUGIN_LOCATION == 'src/llm/copilot_plugin'
        # the package's own folder name is not checked: mutation testing renames it
        assert PLUGIN_DIR.endswith(os.path.join('llm', 'copilot_plugin'))
        assert os.path.isfile(os.path.join(PLUGIN_DIR, 'plugin.json'))

    def test_answer_beside_an_ignored_error_is_checked_too(self):
        error = {'type': 'session.error', 'data': {'errorType': 'x', 'message': 'minor'}}
        replaced = _output('答', _checkpoint({'m': REPLACED}), extra=[error])
        assert CopilotCLI().classify_error(replaced) is None
        built_in = _output('答', _checkpoint({'m': BUILT_IN}), extra=[error])
        assert str(CopilotCLI().classify_error(built_in)) == NOT_REPLACED_MESSAGE + 'CLI 訊息：x minor'

    def test_a_failed_run_keeps_its_class(self):
        limit = {'type': 'session.error', 'data': {'errorType': 'query',
                                                  'message': "You've hit your rate limit."}}
        found = CopilotCLI().classify_error(_output('', code=1, extra=[limit]))
        assert type(found) is RateLimitError
        # a reply with a rate limit beside it is a rate limit, replaced or not
        found = CopilotCLI().classify_error(_output('答', extra=[limit]))
        assert type(found) is RateLimitError


# Review round 1, B: what Copilot CLI 1.0.114 printed when it rejected the
# extension's system message (the extension without its environment variable)
REJECTED = ProcessResult(1, json.dumps({'type': 'result', 'exitCode': 1}) + '\n',
                         'Error executing prompt: Error: GenericFailure, data did not match any '
                         'variant of untagged enum ProtocolSystemMessageConfig\n')
PLUGIN_WARNING = ('Warning: [plugin-dir] no plugin.json or SKILL.md found in '
                  '/somewhere/copilot_plugin-missing')


class TestRound1:

    def test_rejected_system_message_stops(self):
        found = CopilotCLI().classify_error(REJECTED)
        assert type(found) is BackendUnavailableError
        assert str(found) == (NOT_REPLACED_MESSAGE + 'CLI 訊息：Error executing prompt: Error: '
                              'GenericFailure, data did not match any variant of untagged enum '
                              'ProtocolSystemMessageConfig')

    def test_rejected_beside_a_reply_is_the_check(self):
        # a reply is judged by the segments, not by the text beside it
        error = {'type': 'session.error', 'data': {'message': 'ProtocolSystemMessageConfig'}}
        assert CopilotCLI().classify_error(
            _output('答', _checkpoint({'m': REPLACED}), extra=[error])) is None

    @pytest.mark.parametrize('stderr, error', [
        ('Execution failed: Rate limited.', RateLimitError),
        ('Error: not logged in', AuthError),
    ])
    def test_clean_exit_without_reply_keeps_a_recognized_class(self, stderr, error):
        result = ProcessResult(0, json.dumps({'type': 'result', 'exitCode': 0}), stderr)
        assert type(CopilotCLI().classify_error(result)) is error

    def test_clean_exit_without_reply_or_report_names_what_was_printed(self):
        result = _output('', _checkpoint({'m': BUILT_IN}))
        result = ProcessResult(0, result.stdout, PLUGIN_WARNING + '\n')
        found = CopilotCLI().classify_error(result)
        assert type(found) is BackendUnavailableError
        assert str(found) == NOT_REPLACED_MESSAGE + 'CLI 訊息：' + PLUGIN_WARNING

    def test_clean_exit_without_reply_but_replaced_is_an_empty_reply(self):
        assert CopilotCLI().classify_error(_output('', _checkpoint({'m': REPLACED}))) is None

    def test_answered_with_warning_on_stderr_quotes_it(self):
        result = _output('答', _checkpoint({'m': BUILT_IN}))
        found = CopilotCLI().classify_error(ProcessResult(0, result.stdout, PLUGIN_WARNING))
        assert str(found) == NOT_REPLACED_MESSAGE + 'CLI 訊息：' + PLUGIN_WARNING
        replaced = _output('答', _checkpoint({'m': REPLACED}))
        assert CopilotCLI().classify_error(ProcessResult(0, replaced.stdout, PLUGIN_WARNING)) is None

    def test_message_redacts_and_is_bounded(self):
        result = ProcessResult(0, _output('答').stdout, 'user someone@example.com ' + 'y' * 2000)
        found = str(CopilotCLI().classify_error(result))
        assert 'someone@example.com' not in found and '<redacted>' in found
        assert len(found) <= len(NOT_REPLACED_MESSAGE) + len('CLI 訊息：') + 500

    def test_unrecognized_failure_is_still_transient(self):
        result = ProcessResult(1, '', 'socket hang up')
        assert type(CopilotCLI().classify_error(result)) is TransientError

    @pytest.mark.parametrize('workers', ['1', '2'])
    def test_cli_run_stops_without_retries(self, tmp_path, process, workers):
        path = _copilot_project(tmp_path, n=3)
        calls = process(lambda cmd: REJECTED)
        result = CliRunner().invoke(translate.cli, ['start', '--project', path,
                                                    '--workers', workers])
        assert result.exit_code == 1, result.output
        assert 'ProtocolSystemMessageConfig' in result.output
        assert 1 <= len(calls) <= int(workers)  # not four calls per segment
        pm = ProjectManager(path)
        pm.load()
        assert [s.status for s in pm.data.segments] == [SegmentStatus.PENDING] * 3


class TestRound2:
    """Review round 2, C: plain text on stdout of a clean exit without a reply."""

    @pytest.mark.parametrize('stdout, error', [
        ('Execution failed: Rate limited.\n', RateLimitError),
        ('Error: not logged in\n', AuthError),
    ])
    def test_plain_stdout_classified(self, stdout, error):
        found = CopilotCLI().classify_error(ProcessResult(0, stdout, ''))
        assert type(found) is error and stdout.strip() in str(found)

    def test_plain_stdout_quoted_when_not_recognized(self):
        found = CopilotCLI().classify_error(ProcessResult(0, 'something odd\n', ''))
        assert str(found) == NOT_REPLACED_MESSAGE + 'CLI 訊息：something odd'

    @pytest.mark.parametrize('code', [1, 2])
    def test_plain_stdout_of_a_failed_run_with_a_reply_is_quoted(self, code):
        replied = _output('答', code=code)
        found = CopilotCLI().classify_error(ProcessResult(code, 'boom\n' + replied.stdout, ''))
        assert str(found) == f'copilot CLI exited with code {code}: boom'

    def test_plain_stdout_beside_a_reply_is_not_an_error_text(self):
        replied = _output('答', _checkpoint({'m': REPLACED}))
        result = ProcessResult(0, 'Rate limited.\n' + replied.stdout, '')
        assert CopilotCLI().classify_error(result) is None


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
