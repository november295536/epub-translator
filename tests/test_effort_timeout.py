"""Phase 10: the project's reasoning effort and call timeout.

Stored and chosen like the backend and model (ProjectManager.select_model),
passed with every translation and extraction call (ProjectManager.
call_settings), shown and set through `model show|set` and the web model
route. Fake processes and backends only."""

import json
import os
import threading

import pytest
from click.testing import CliRunner

import src.llm as llm
import translate
from src.core.project import (
    DEFAULT_EFFORT, DEFAULT_TIMEOUT, MAX_TIMEOUT, MIN_TIMEOUT, CallSettings, ProjectManager,
    SegmentStatus, SettingError, effort_levels, effort_reset_note,
)
from src.core.translator import TranslationEngine
from src.glossary.term_extractor import TermExtractor
from src.llm import EFFORT_LEVELS, BackendUnavailableError, LLMRequest, Message, ModelInfo
from src.llm.claude_cli import LEAN_FLAGS
from src.llm.cli import ProcessResult
from src.llm.copilot_cli import EFFORT_REJECTED_MESSAGE, PLUGIN_DIR, CopilotCLI
from src.llm.fake import FakeBackend
from tests.conftest import like, use_fake_backend
from tests.test_runtime import PROJECT, _copilot_ok, _make_project, _wait, fresh_tm  # noqa: F401

COPILOT_TAIL = ['--output-format', 'json', '--available-tools', 'none', '--no-custom-instructions',
                '--disable-builtin-mcps', '--no-ask-user', '--no-auto-update',
                '--experimental', '--plugin-dir', PLUGIN_DIR]

# What Copilot CLI 1.0.114 printed for a level the model does not take
# (exit 1 before any model call; stdout holds the session.start event only)
REFUSALS = [
    'Error: Reasoning effort "none" is not supported for model "claude-sonnet-5.5".',
    'Error: Reasoning effort "max" is not supported for model "gemini-4-carbon".',
    'Error: Model "claude-haiku-4.5" does not support reasoning effort configuration '
    '(requested: "low").',
]


def _refused(text):
    return ProcessResult(1, json.dumps({'type': 'session.start', 'data': {}}) + '\n', text)


def _bytes(path):
    with open(os.path.join(path, 'project.json'), 'rb') as f:
        return f.read()


def _raw(path):
    return json.loads(_bytes(path))


def _pm(path):
    pm = ProjectManager(path)
    pm.load()
    return pm


class Recorder:
    """run_process stand-in: records command, environment and timeout of
    every call and answers with `reply(cmd)`."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, cmd, *, timeout, cwd, env, cancel=None):
        with self.lock:
            self.calls.append({'cmd': cmd, 'env': env, 'timeout': timeout})
        return self.reply(cmd)


def _echo(cmd):
    """A reply that passes the check: the protected paragraph of the prompt."""
    prompt = cmd[2]
    start = prompt.rindex('<p k=')
    return ProcessResult(0, prompt[start:prompt.index('</p>', start) + 4], '')


def _copilot_echo(cmd):
    return _copilot_ok(_echo(cmd).stdout)


@pytest.fixture
def recorder(monkeypatch):
    def install(reply):
        rec = Recorder(reply)
        monkeypatch.setattr('src.llm.cli.run_process', rec)
        return rec
    return install


# --- the levels each model declares ------------------------------------------------

class TestModelLevels:

    def test_levels_per_model(self):
        assert {m.alias: m.efforts for m in llm.backend_for('claude-cli').list_models()} == {
            'sonnet': EFFORT_LEVELS, 'opus': EFFORT_LEVELS, 'haiku': EFFORT_LEVELS}
        assert {m.alias: m.efforts for m in llm.backend_for('copilot-cli').list_models()} == {
            'claude-sonnet-5.5': EFFORT_LEVELS, 'gpt-6.2-sol': EFFORT_LEVELS,
            'gemini-4-carbon': ('low', 'medium', 'high'), 'claude-haiku-4.5': ()}

    def test_effort_levels(self):
        assert effort_levels(None, 'opus') == EFFORT_LEVELS
        assert effort_levels('copilot-cli', 'gemini-4-carbon') == ('low', 'medium', 'high')
        assert effort_levels('copilot-cli', 'claude-haiku-4.5') == ()
        assert effort_levels('copilot-cli', 'opus') == ()  # not a model of that backend

    def test_constants(self):
        assert (DEFAULT_EFFORT, DEFAULT_TIMEOUT, MIN_TIMEOUT, MAX_TIMEOUT) == ('low', 180, 30, 3600)
        assert ModelInfo('a', 'b', 'c').efforts == ()


# --- the exact command line and environment ----------------------------------------

def _request(effort):
    return LLMRequest(system='', messages=[Message('user', 'P')], model='m', timeout=5,
                      effort=effort)


class TestCommandLines:

    def test_claude_flag(self):
        backend = llm.backend_for('claude-cli')
        assert backend.build_command('P', _request('xhigh')) == [
            'claude', '-p', 'P', '--model', 'm', '--effort', 'xhigh', *LEAN_FLAGS]
        assert backend.build_command('P', _request(None)) == [
            'claude', '-p', 'P', '--model', 'm', *LEAN_FLAGS]

    def test_copilot_flag(self):
        backend = CopilotCLI()
        assert backend.build_command('P', _request('high')) == [
            'copilot', '-p', 'P', '--model', 'm', '--reasoning-effort', 'high', *COPILOT_TAIL]
        assert backend.build_command('P', _request(None)) == [
            'copilot', '-p', 'P', '--model', 'm', *COPILOT_TAIL]

    def test_claude_environment(self, monkeypatch):
        monkeypatch.setenv('CLAUDE_CODE_EFFORT_LEVEL', 'max')
        monkeypatch.setenv('CLAUDECODE', '1')
        monkeypatch.setenv('KEPT_VARIABLE', 'yes')
        env = llm.backend_for('claude-cli').environment()
        assert 'CLAUDE_CODE_EFFORT_LEVEL' not in env and 'CLAUDECODE' not in env
        assert env['KEPT_VARIABLE'] == 'yes'
        assert env == {k: v for k, v in os.environ.items()
                       if k not in ('CLAUDE_CODE_EFFORT_LEVEL', 'CLAUDECODE')}

    @pytest.mark.parametrize('backend, model, effort, flags', [
        (None, 'sonnet', None, ['--effort', 'low']),          # the default
        (None, 'opus', 'max', ['--effort', 'max']),
        (None, 'haiku', 'medium', ['--effort', 'medium']),
        ('copilot-cli', 'claude-sonnet-5.5', None, ['--reasoning-effort', 'low']),
        ('copilot-cli', 'gpt-6.2-sol', 'xhigh', ['--reasoning-effort', 'xhigh']),
        ('copilot-cli', 'gemini-4-carbon', 'high', ['--reasoning-effort', 'high']),
        ('copilot-cli', 'claude-haiku-4.5', None, []),         # not configurable
    ])
    def test_project_call(self, tmp_path, recorder, monkeypatch, backend, model, effort, flags):
        """From the stored settings to the program's arguments, environment
        and timeout, through the engine a run builds."""
        monkeypatch.setenv('CLAUDE_CODE_EFFORT_LEVEL', 'max')
        path = _make_project(tmp_path, n=1)
        pm = _pm(path)
        assert pm.select_model(backend, model, effort=effort, timeout=45)
        rec = recorder(_echo if backend is None else _copilot_echo)
        engine = TranslationEngine(**pm.call_settings().call_args())
        engine.translate('Line 1.', html='<p>Line 1.</p>')
        call, = rec.calls
        program = 'claude' if backend is None else 'copilot'
        assert call['cmd'][:5] == [program, '-p', call['cmd'][2], '--model', model]
        tail = list(LEAN_FLAGS) if backend is None else COPILOT_TAIL
        assert call['cmd'][5:] == flags + tail
        assert call['timeout'] == 45
        assert 'CLAUDE_CODE_EFFORT_LEVEL' not in call['env'] or backend is not None

    def test_copilot_keeps_the_environment(self, monkeypatch):
        """Copilot has no effort variable: its environment is unchanged by
        this phase (the system prompt variable added)."""
        monkeypatch.setenv('CLAUDE_CODE_EFFORT_LEVEL', 'max')
        assert CopilotCLI().environment()['CLAUDE_CODE_EFFORT_LEVEL'] == 'max'


# --- Copilot's refusal of a level --------------------------------------------------

class TestCopilotRefusal:

    @pytest.mark.parametrize('text', REFUSALS)
    def test_is_backend_unavailable(self, text):
        error = CopilotCLI().classify_error(_refused(text))
        assert type(error) is BackendUnavailableError
        assert str(error) == f'{EFFORT_REJECTED_MESSAGE}：{text}'

    def test_also_as_error_event(self):
        events = [{'type': 'session.start', 'data': {}},
                  {'type': 'session.error', 'data': {'errorType': 'query', 'message': REFUSALS[1]}}]
        result = ProcessResult(1, '\n'.join(json.dumps(e) for e in events), '')
        assert type(CopilotCLI().classify_error(result)) is BackendUnavailableError

    def test_reply_with_the_words_is_not_a_refusal(self):
        """Only error texts are searched, never the reply."""
        assert CopilotCLI().classify_error(_copilot_ok(REFUSALS[0])) is None

    def test_cli_run_exits_1_with_nothing_stored(self, tmp_path, recorder):
        path = _make_project(tmp_path, n=2)
        pm = _pm(path)
        pm.select_model('copilot-cli', 'gemini-4-carbon')
        pm.save()
        before = _bytes(path)
        rec = recorder(lambda cmd: _refused(REFUSALS[1]))
        result = CliRunner().invoke(translate.cli, ['start', '-p', path])
        assert result.exit_code == 1
        assert EFFORT_REJECTED_MESSAGE in result.output and '翻譯中斷' in result.output
        assert len(rec.calls) == 1  # not retried
        assert _raw(path)['segments'] == json.loads(before)['segments']
        assert not os.path.exists(os.path.join(path, 'log', 'failed'))

    def test_web_task_pauses_with_nothing_stored(self, tmp_path, recorder, fresh_tm):
        path = _make_project(tmp_path, n=2)
        pm = _pm(path)
        pm.select_model('copilot-cli', 'claude-sonnet-5.5')
        pm.save()
        rec = recorder(lambda cmd: _refused(REFUSALS[0]))
        task = fresh_tm.start_translation(PROJECT, str(tmp_path))
        _wait(lambda: task.state == 'paused')
        events = [e for e in task.progress.get_all() if e['event'] == 'backend_error']
        assert len(events) == 1 and EFFORT_REJECTED_MESSAGE in events[0]['data']['error']
        assert len(rec.calls) == 1
        fresh_tm.stop_translation(PROJECT)
        task._coordinator.join(timeout=20)
        assert [s.status for s in _pm(path).data.segments] == [SegmentStatus.PENDING] * 2
        assert not os.path.exists(os.path.join(path, 'log', 'failed'))


# --- select_model with effort and timeout -------------------------------------------

@pytest.fixture
def pm(tmp_path):
    return _pm(_make_project(tmp_path, n=1))


def _state(pm):
    d = pm.data
    return (d.backend, d.model, d.effort, d.timeout)


class TestSelect:

    def test_defaults(self, pm):
        assert _state(pm) == (None, 'sonnet', None, None)
        assert pm.call_settings() == CallSettings(None, 'sonnet', 'low', 180)

    def test_effort_and_timeout_alone(self, pm):
        assert pm.select_model(None, None, effort='max') is True
        assert pm.select_model(None, None, timeout=MAX_TIMEOUT) is True
        assert pm.select_model(None, None, timeout=MIN_TIMEOUT) is True
        assert _state(pm) == (None, 'sonnet', 'max', 30)
        assert pm.call_settings() == CallSettings(None, 'sonnet', 'max', 30)
        assert pm.select_model(None, None) is False

    def test_all_at_once(self, pm):
        assert pm.select_model('copilot-cli', 'gemini-4-carbon', effort='high', timeout=600)
        assert _state(pm) == ('copilot-cli', 'gemini-4-carbon', 'high', 600)

    def test_kept_when_the_new_model_takes_it(self, pm):
        pm.select_model(None, 'opus', effort='xhigh')
        assert pm.select_model('copilot-cli', 'gpt-6.2-sol') is True
        assert _state(pm) == ('copilot-cli', 'gpt-6.2-sol', 'xhigh', None)
        assert effort_reset_note('xhigh', pm.data) == ''

    def test_reset_when_the_new_model_does_not_take_it(self, pm):
        pm.select_model(None, 'opus', effort='xhigh', timeout=90)
        assert pm.select_model('copilot-cli', 'gemini-4-carbon') is True
        assert _state(pm) == ('copilot-cli', 'gemini-4-carbon', None, 90)
        assert pm.call_settings().effort == 'low'
        assert effort_reset_note('xhigh', pm.data) == \
            '原推理強度 xhigh 不適用於 gemini-4-carbon，已改回預設：low'

    def test_reset_for_an_unconfigurable_model(self, pm):
        pm.select_model(None, None, effort='high')
        assert pm.select_model('copilot-cli', 'claude-haiku-4.5') is True
        assert _state(pm)[2] is None and pm.call_settings().effort is None
        assert effort_reset_note('high', pm.data) == \
            '原推理強度 high 不適用於 claude-haiku-4.5，已改回預設：此模型不可設定'
        # back to a configurable model: the default
        pm.select_model(None, 'gpt-6.2-sol')
        assert pm.call_settings().effort == 'low'

    def test_reset_with_a_new_backend_only(self, pm):
        """A new backend without a model: its default model, same rule."""
        pm.select_model(None, None, effort='max')
        pm.select_model('copilot-cli', None)
        assert _state(pm) == ('copilot-cli', 'claude-sonnet-5.5', 'max', None)

    def test_effort_with_the_model_change(self, pm):
        pm.select_model(None, None, effort='max')
        pm.select_model('copilot-cli', 'gemini-4-carbon', effort='medium')
        assert _state(pm)[2] == 'medium'

    @pytest.mark.parametrize('args, setting, message', [
        ((None, None, 'bogus', None), 'effort',
         '無效的推理強度：bogus，sonnet 可用：low, medium, high, xhigh, max'),
        ((None, None, 'none', None), 'effort',
         '無效的推理強度：none，sonnet 可用：low, medium, high, xhigh, max'),
        (('copilot-cli', 'gemini-4-carbon', 'xhigh', None), 'effort',
         '無效的推理強度：xhigh，gemini-4-carbon 可用：low, medium, high'),
        (('copilot-cli', 'claude-haiku-4.5', 'low', None), 'effort',
         '模型 claude-haiku-4.5 不可設定推理強度'),
        ((None, None, None, 29), 'timeout', '無效的逾時：29，須為 30–3600 的整數秒數'),
        ((None, None, None, 3601), 'timeout', '無效的逾時：3601，須為 30–3600 的整數秒數'),
        ((None, None, None, 60.0), 'timeout', '無效的逾時：60.0，須為 30–3600 的整數秒數'),
        ((None, None, None, True), 'timeout', '無效的逾時：True，須為 30–3600 的整數秒數'),
        ((None, None, None, '60'), 'timeout', "無效的逾時：'60'，須為 30–3600 的整數秒數"),
        # a valid part does not go through with a refused one
        ((None, 'opus', 'low', 10), 'timeout', '無效的逾時：10，須為 30–3600 的整數秒數'),
        ((None, 'nope', 'low', 60), 'model', '無效的模型：nope，可用模型：sonnet, opus, haiku'),
        (('nope', None, None, None), 'backend', "unknown backend: 'nope'"),
    ])
    def test_refused_changes_nothing(self, pm, args, setting, message):
        before = _state(pm)
        with pytest.raises(SettingError) as e:
            pm.select_model(*args[:2], effort=args[2], timeout=args[3])
        assert (e.value.setting, str(e.value)) == (setting, message)
        assert isinstance(e.value, ValueError)
        assert _state(pm) == before


# --- project files ---------------------------------------------------------------------

class TestProjectFile:

    def test_old_file_loads_as_defaults_and_round_trips(self, tmp_path):
        path = _make_project(tmp_path, n=1)
        raw = _raw(path)
        assert 'effort' not in raw and 'timeout' not in raw
        pm = _pm(path)
        assert (pm.data.effort, pm.data.timeout) == (None, None)
        assert pm.call_settings() == CallSettings(None, 'sonnet', 'low', 180)
        assert json.loads(pm.to_json()) == raw  # nothing added

    def test_set_values_written_and_read(self, tmp_path):
        path = _make_project(tmp_path, n=1)
        pm = _pm(path)
        pm.select_model(None, 'opus', effort='max', timeout=1200)
        pm.save()
        raw = _raw(path)
        assert (raw['effort'], raw['timeout']) == ('max', 1200)
        assert _pm(path).call_settings() == CallSettings(None, 'opus', 'max', 1200)

    def test_reset_level_leaves_the_file(self, tmp_path):
        path = _make_project(tmp_path, n=1)
        pm = _pm(path)
        pm.select_model(None, None, effort='max')
        pm.select_model('copilot-cli', 'claude-haiku-4.5')
        pm.save()
        assert 'effort' not in _raw(path)

    @pytest.mark.parametrize('key, value', [
        ('effort', 'bogus'), ('effort', None), ('effort', 1),
        ('timeout', 29), ('timeout', 3601), ('timeout', '180'), ('timeout', None),
    ])
    def test_bad_stored_value_refused(self, tmp_path, key, value):
        path = _make_project(tmp_path, n=1)
        raw = _raw(path)
        raw[key] = value
        with open(os.path.join(path, 'project.json'), 'w', encoding='utf-8') as f:
            json.dump(raw, f)
        with pytest.raises(ValueError):
            ProjectManager(path).load()

    def test_stored_level_of_another_model_passed_as_it_is(self, tmp_path):
        """A hand-edited level the model does not take: passed, so the CLI
        says so (Copilot refuses: BackendUnavailableError)."""
        path = _make_project(tmp_path, n=1)
        raw = _raw(path)
        raw.update(backend='copilot-cli', model='gemini-4-carbon', effort='max')
        with open(os.path.join(path, 'project.json'), 'w', encoding='utf-8') as f:
            json.dump(raw, f)
        assert _pm(path).call_settings().effort == 'max'

    def test_unlisted_model_gets_no_level(self, tmp_path):
        path = _make_project(tmp_path, n=1)
        raw = _raw(path)
        raw.update(model='retired-model', effort='high')
        with open(os.path.join(path, 'project.json'), 'w', encoding='utf-8') as f:
            json.dump(raw, f)
        assert _pm(path).call_settings() == CallSettings(None, 'retired-model', None, 180)


# --- the CLI ---------------------------------------------------------------------------

class TestCLI:

    def test_show(self, tmp_path):
        path = _make_project(tmp_path, n=1)
        pm = _pm(path)
        pm.select_model('copilot-cli', 'gemini-4-carbon', effort='medium', timeout=300)
        pm.save()
        result = CliRunner().invoke(translate.cli, ['model', 'show', '-p', path])
        assert (result.exit_code, result.output) == (0, (
            '後端：copilot-cli\n模型：gemini-4-carbon\n'
            '可用模型：claude-sonnet-5.5, gpt-6.2-sol, gemini-4-carbon, claude-haiku-4.5\n'
            '推理強度：medium\n可用推理強度：low, medium, high\n呼叫逾時：300 秒\n'))

    def test_show_unconfigurable(self, tmp_path):
        path = _make_project(tmp_path, n=1)
        pm = _pm(path)
        pm.select_model('copilot-cli', 'claude-haiku-4.5')
        pm.save()
        result = CliRunner().invoke(translate.cli, ['model', 'show', '-p', path])
        assert '推理強度：此模型不可設定\n可用推理強度：無\n呼叫逾時：180 秒\n' in result.output

    def test_set_effort_and_timeout(self, tmp_path):
        path = _make_project(tmp_path, n=1)
        result = CliRunner().invoke(translate.cli, ['model', 'set', '-e', 'xhigh', '-t', '240',
                                                    '-p', path])
        assert (result.exit_code, result.output) == (
            0, '已設定模型：sonnet（claude-cli）\n推理強度：xhigh，呼叫逾時：240 秒\n')
        assert (_raw(path)['effort'], _raw(path)['timeout']) == ('xhigh', 240)
        result = CliRunner().invoke(translate.cli, ['model', 'set', '--effort', 'low',
                                                    '--timeout', '30', '-p', path])
        assert result.exit_code == 0
        assert (_raw(path)['effort'], _raw(path)['timeout']) == ('low', 30)

    def test_model_change_reports_the_reset(self, tmp_path):
        path = _make_project(tmp_path, n=1)
        CliRunner().invoke(translate.cli, ['model', 'set', '-e', 'max', '-p', path])
        result = CliRunner().invoke(translate.cli, ['model', 'set', '-b', 'copilot-cli',
                                                    '-m', 'gemini-4-carbon', '-p', path])
        assert (result.exit_code, result.output) == (0, (
            '原推理強度 max 不適用於 gemini-4-carbon，已改回預設：low\n'
            '已設定模型：gemini-4-carbon（copilot-cli）\n推理強度：low，呼叫逾時：180 秒\n'))
        assert 'effort' not in _raw(path)

    @pytest.mark.parametrize('args, message', [
        (['-e', 'bogus'], "Invalid value for '--effort' / '-e': 無效的推理強度：bogus，"
                          "sonnet 可用：low, medium, high, xhigh, max"),
        (['-b', 'copilot-cli', '-m', 'gemini-4-carbon', '-e', 'max'],
         "Invalid value for '--effort' / '-e': 無效的推理強度：max，gemini-4-carbon 可用："),
        (['-b', 'copilot-cli', '-m', 'claude-haiku-4.5', '-e', 'low'],
         "Invalid value for '--effort' / '-e': 模型 claude-haiku-4.5 不可設定推理強度"),
        (['-t', '29'], "Invalid value for '--timeout' / '-t': 無效的逾時：29，須為 30–3600 的整數秒數"),
        (['-t', '3601'], "Invalid value for '--timeout' / '-t': 無效的逾時：3601"),
        (['-t', '60.5'], "Invalid value for '--timeout' / '-t': '60.5' is not a valid integer"),
        (['-m', 'opus', '-t', '5'], "Invalid value for '--timeout' / '-t'"),
    ])
    def test_refused_leaves_the_file(self, tmp_path, args, message):
        path = _make_project(tmp_path, n=1)
        before = _bytes(path)
        result = CliRunner().invoke(translate.cli, ['model', 'set', '-p', path] + args)
        assert result.exit_code == 2
        assert message in ' '.join(result.output.split())
        assert _bytes(path) == before

    def test_start_shows_the_settings(self, tmp_path, recorder):
        path = _make_project(tmp_path, n=1)
        recorder(_echo)
        result = CliRunner().invoke(translate.cli, ['start', '-p', path])
        assert '使用模型：sonnet\n推理強度：low，呼叫逾時：180 秒\n' in result.output

    @pytest.mark.parametrize('workers', ['1', '2'])
    def test_start_passes_effort_and_timeout(self, tmp_path, recorder, workers):
        path = _make_project(tmp_path, n=2)
        pm = _pm(path)
        pm.select_model(None, 'opus', effort='high', timeout=75)
        pm.save()
        rec = recorder(_echo)
        result = CliRunner().invoke(translate.cli, ['start', '-p', path, '-w', workers])
        assert result.exit_code == 0, result.output
        assert len(rec.calls) == 2
        for call in rec.calls:
            assert call['cmd'][3:7] == ['--model', 'opus', '--effort', 'high']
            assert call['timeout'] == 75

    def test_glossary_extract_passes_effort_and_timeout(self, tmp_path, recorder):
        path = _make_project(tmp_path, n=1)
        pm = _pm(path)
        pm.select_model('copilot-cli', 'gpt-6.2-sol', effort='medium', timeout=900)
        pm.save()
        rec = recorder(lambda cmd: _copilot_ok('[]'))
        result = CliRunner().invoke(translate.cli, ['glossary', 'extract', '-p', path])
        assert result.exit_code == 0, result.output
        call, = rec.calls
        assert call['cmd'][3:7] == ['--model', 'gpt-6.2-sol', '--reasoning-effort', 'medium']
        assert call['timeout'] == 900

    def test_help(self):
        output = ' '.join(CliRunner().invoke(translate.cli, ['model', 'set', '--help']).output.split())
        assert "-e, --effort TEXT Reasoning effort: one of the model's levels" in output
        assert '-t, --timeout INTEGER Seconds per model call (30-3600)' in output


# --- the extractor and the engine ------------------------------------------------------

class TestDefaults:

    def test_extractor_default_timeout_and_effort(self):
        fake = FakeBackend(['[]'])
        TermExtractor(model='m', backend=fake).extract_from_text('Some text.')
        assert (fake.requests[0].timeout, fake.requests[0].effort) == (180, None)
        fake = FakeBackend(['[]'])
        TermExtractor(model='m', backend=fake, effort='high', timeout=31).extract_from_text('x')
        assert (fake.requests[0].timeout, fake.requests[0].effort) == (31, 'high')

    def test_engine_passes_effort(self):
        fake = FakeBackend(['<p k="1">譯</p>'])
        TranslationEngine(backend=fake, effort='max', timeout=40).translate(
            'Line.', html='<p>Line.</p>')
        assert (fake.requests[0].timeout, fake.requests[0].effort) == (40, 'max')


# --- the web ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    from src.web.server import create_app
    _make_project(tmp_path, n=1)
    return create_app(projects_dir=str(tmp_path)).test_client()


URL = f'/api/projects/{PROJECT}/model'


class TestWeb:

    def test_get(self, client):
        data = client.get(URL).get_json()
        assert (data['current'], data['backend'], data['effort'], data['timeout']) == (
            'sonnet', 'claude-cli', 'low', 180)
        assert data['efforts'] == list(EFFORT_LEVELS)
        assert (data['timeout_min'], data['timeout_max']) == (30, 3600)
        copilot = {m['alias']: m['efforts'] for m in data['backends'][1]['models']}
        assert copilot['gemini-4-carbon'] == ['low', 'medium', 'high']
        assert copilot['claude-haiku-4.5'] == []
        assert data['available'][0]['efforts'] == list(EFFORT_LEVELS)

    def test_put_effort_and_timeout(self, client, tmp_path):
        answer = client.put(URL, json={'model': 'sonnet', 'effort': 'max', 'timeout': 600})
        assert answer.status_code == 200
        assert answer.get_json() == {'status': 'ok', 'model': 'sonnet', 'backend': 'claude-cli',
                                     'effort': 'max', 'efforts': list(EFFORT_LEVELS),
                                     'timeout': 600, 'note': ''}
        raw = _raw(os.path.join(str(tmp_path), PROJECT))
        assert (raw['effort'], raw['timeout']) == ('max', 600)

    def test_model_change_resets_and_says_so(self, client, tmp_path):
        client.put(URL, json={'model': 'sonnet', 'effort': 'xhigh'})
        answer = client.put(URL, json={'model': 'claude-haiku-4.5', 'backend': 'copilot-cli'})
        assert answer.get_json() == {
            'status': 'ok', 'model': 'claude-haiku-4.5', 'backend': 'copilot-cli',
            'effort': None, 'efforts': [], 'timeout': 180,
            'note': '原推理強度 xhigh 不適用於 claude-haiku-4.5，已改回預設：此模型不可設定'}
        assert 'effort' not in _raw(os.path.join(str(tmp_path), PROJECT))
        assert client.get(URL).get_json()['effort'] is None

    @pytest.mark.parametrize('body', [
        {'model': 'sonnet', 'effort': 'bogus'},
        {'model': 'sonnet', 'effort': ['low']},
        {'model': 'sonnet', 'effort': 3},
        {'model': 'gemini-4-carbon', 'backend': 'copilot-cli', 'effort': 'xhigh'},
        {'model': 'claude-haiku-4.5', 'backend': 'copilot-cli', 'effort': 'low'},
        {'model': 'sonnet', 'timeout': 29},
        {'model': 'sonnet', 'timeout': 3601},
        {'model': 'sonnet', 'timeout': 60.5},
        {'model': 'sonnet', 'timeout': '60'},
        {'model': 'sonnet', 'timeout': True},
        {'model': 'opus', 'effort': 'high', 'timeout': 1},
        {'effort': 'high'},  # no model: malformed, as before
    ])
    def test_refused_leaves_the_file(self, client, tmp_path, body):
        path = os.path.join(str(tmp_path), PROJECT)
        before = _bytes(path)
        answer = client.put(URL, json=body)
        assert answer.status_code == 400
        assert answer.get_json()['error']
        assert _bytes(path) == before

    def test_failed_save_restores_effort_and_timeout(self, client, tmp_path, monkeypatch):
        from src.web.tasks import TaskManager
        pm = _pm(os.path.join(str(tmp_path), PROJECT))
        pm.select_model(None, 'opus', effort='high', timeout=100)
        monkeypatch.setattr(TaskManager, 'get_or_load_pm', lambda self, name, d: pm)
        seen = []

        def broken():
            seen.append(_state(pm))
            raise OSError('disk full')

        monkeypatch.setattr(pm, 'save', broken)
        client.application.config['PROPAGATE_EXCEPTIONS'] = True
        with pytest.raises(OSError):
            client.put(URL, json={'model': 'gemini-4-carbon', 'backend': 'copilot-cli',
                                  'timeout': 200})
        assert seen == [('copilot-cli', 'gemini-4-carbon', 'high', 200)]
        assert _state(pm) == (None, 'opus', 'high', 100)

    def test_web_extraction_passes_effort_and_timeout(self, client, tmp_path, recorder):
        client.put(URL, json={'model': 'haiku', 'effort': 'medium', 'timeout': 333})
        rec = recorder(lambda cmd: ProcessResult(0, '[]', ''))
        assert client.post(f'/api/projects/{PROJECT}/glossary/extract').status_code == 200
        _wait(lambda: not client.get(
            f'/api/projects/{PROJECT}/glossary/extract/status').get_json().get('running'))
        call, = rec.calls
        assert call['cmd'][3:7] == ['--model', 'haiku', '--effort', 'medium']
        assert call['timeout'] == 333

    def test_retranslate_passes_effort_and_timeout(self, client, recorder):
        client.put(URL, json={'model': 'opus', 'effort': 'max', 'timeout': 99})
        rec = recorder(_echo)
        answer = client.post(f'/api/projects/{PROJECT}/segments/c1_seg0001/retranslate')
        assert answer.status_code == 200, answer.get_json()
        call, = rec.calls
        assert call['cmd'][3:7] == ['--model', 'opus', '--effort', 'max']
        assert call['timeout'] == 99

    def test_page(self, client):
        html = client.get(f'/project/{PROJECT}/translation').get_data(as_text=True)
        start = html.index('<select id="effort-select"')
        select = html[start:html.index('</select>', start)]
        assert 'x-model' not in select
        assert '@change="saveEffort($event.target.value)"' in select
        assert ':selected="level === effort"' in select
        assert '此模型不可設定' in select
        assert 'id="timeout-input"' in html and '@change="saveTimeout($event.target.value)"' in html
        assert 'x-init="init()"' not in html
        assert "document.getElementById('effort-select').value = previous;" in html
        assert "document.getElementById('timeout-input').value = previous;" in html


# --- a switch during a web run applies to the next segment -----------------------------

class TestMidRunSwitch:

    def test_effort_and_timeout_switch(self, tmp_path, fresh_tm, monkeypatch):
        path = _make_project(tmp_path, n=2)
        models = [ModelInfo('fake-model', 'Fake', '', ('low', 'high'))]
        holder, ready = {}, threading.Event()

        def respond(request):
            if len(fake.requests) == 1:
                assert ready.wait(10)
                with holder['task'].pm.mutex:
                    holder['task'].pm.select_model(None, None, effort='high', timeout=500)
            return like(request, '譯')

        fake = FakeBackend(responder=respond)
        monkeypatch.setattr(fake, 'list_models', lambda: models)
        with use_fake_backend(fake):
            pm = _pm(path)
            pm.set_model('fake-model')
            pm.save()
            task = fresh_tm.start_translation(PROJECT, str(tmp_path))
            holder['task'] = task
            ready.set()
            task._coordinator.join(timeout=20)
        assert task.state == 'completed'
        assert [(r.effort, r.timeout) for r in fake.requests] == [('low', 180), ('high', 500)]
