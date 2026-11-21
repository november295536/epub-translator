"""Tests for translation engine module."""

import subprocess
import pytest
from unittest.mock import patch, MagicMock

from src.core.segmenter import SegmentType
from src.core.translator import (
    TranslationEngine, TranslationError, RateLimitError,
    TRANSLATION_PROMPT, CODE_PROMPT,
    TEXT_HTML_PROMPT,
    available_models, default_model,
)
from src.core.project import SegmentState
from src.glossary.glossary_manager import GlossaryEntry


class TestTranslationEngine:

    def test_successful_translation(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '這是翻譯結果。'

        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            result = engine.translate('This is the source text.')

        assert result == '這是翻譯結果。'
        mock_run.assert_called_once()

        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == 'claude'
        assert cmd[1] == '-p'
        prompt = cmd[2]
        assert '繁體中文' in prompt
        assert '台灣' in prompt
        assert 'This is the source text.' in prompt

    def test_prompt_contains_key_instructions(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯'

        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            engine.translate('test')

        prompt = mock_run.call_args[0][0][2]
        assert '台灣' in prompt
        assert '括號' in prompt
        assert 'LaTeX' in prompt

    def test_retry_on_nonzero_exit(self):
        engine = TranslationEngine(max_calls=3)

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stderr = 'error'

        success_result = MagicMock()
        success_result.returncode = 0
        success_result.stdout = '翻譯結果'

        with patch('src.llm.cli.run_process',
                   side_effect=[fail_result, success_result]) as mock_run:
            result = engine.translate('test')

        assert result == '翻譯結果'
        assert mock_run.call_count == 2

    def test_fail_after_max_calls(self):
        engine = TranslationEngine(max_calls=2)

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stderr = 'persistent error'

        with patch('src.llm.cli.run_process', return_value=fail_result):
            with pytest.raises(TranslationError, match="failed after 2 attempts"):
                engine.translate('test')

    def test_retry_on_empty_output(self):
        engine = TranslationEngine(max_calls=2)

        empty_result = MagicMock()
        empty_result.returncode = 0
        empty_result.stdout = ''

        success_result = MagicMock()
        success_result.returncode = 0
        success_result.stdout = '翻譯結果'

        with patch('src.llm.cli.run_process',
                   side_effect=[empty_result, success_result]):
            result = engine.translate('test')

        assert result == '翻譯結果'

    def test_fail_on_all_empty(self):
        engine = TranslationEngine(max_calls=2)

        empty_result = MagicMock()
        empty_result.returncode = 0
        empty_result.stdout = '   '

        with patch('src.llm.cli.run_process', return_value=empty_result):
            with pytest.raises(TranslationError, match="Empty translation"):
                engine.translate('test')

    def test_timeout_handling(self):
        engine = TranslationEngine(max_calls=2)

        with patch('src.llm.cli.run_process',
                   side_effect=subprocess.TimeoutExpired(cmd='claude', timeout=120)):
            with pytest.raises(TranslationError, match="timed out"):
                engine.translate('test')

    def test_timeout_then_success(self):
        engine = TranslationEngine(max_calls=3)

        success_result = MagicMock()
        success_result.returncode = 0
        success_result.stdout = '成功'

        with patch('src.llm.cli.run_process',
                   side_effect=[
                       subprocess.TimeoutExpired(cmd='claude', timeout=120),
                       success_result,
                   ]):
            result = engine.translate('test')

        assert result == '成功'

    def test_subprocess_called_with_timeout(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯'

        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            engine.translate('test')

        # run_process always captures text output; only the timeout is passed
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs['timeout'] == 120


class TestSubprocessIsolation:

    def test_subprocess_runs_in_temp_dir(self):
        """subprocess.run should use cwd=tempfile.gettempdir() to isolate from git context."""
        import tempfile

        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯'

        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            engine.translate('test')

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs['cwd'] == tempfile.gettempdir()


class TestRateLimitDetection:

    def test_rate_limit_in_stdout(self):
        engine = TranslationEngine(max_calls=3)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "You've hit your limit · resets 8pm (Asia/Taipei)"
        mock_result.stderr = ''

        with patch('src.llm.cli.run_process', return_value=mock_result):
            with pytest.raises(RateLimitError):
                engine.translate('test')

    def test_rate_limit_in_stderr(self):
        engine = TranslationEngine(max_calls=3)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ''
        mock_result.stderr = "You've hit your limit · resets 8pm (Asia/Taipei)"

        with patch('src.llm.cli.run_process', return_value=mock_result):
            with pytest.raises(RateLimitError):
                engine.translate('test')

    def test_rate_limit_no_retry(self):
        """Rate limit should raise immediately without retrying."""
        engine = TranslationEngine(max_calls=3)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "You've hit your limit · resets 8pm (Asia/Taipei)"
        mock_result.stderr = ''

        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            with pytest.raises(RateLimitError):
                engine.translate('test')

        assert mock_run.call_count == 1


class TestResponseCleaning:

    def test_removes_chinese_prefix(self):
        assert TranslationEngine._clean_response('以下是翻譯結果：\n\n這是正文') == '這是正文'
        assert TranslationEngine._clean_response('翻譯如下：\n這是正文') == '這是正文'
        assert TranslationEngine._clean_response('看起來這段文字是關於...\n這是正文') == '這是正文'

    def test_removes_english_prefix(self):
        assert TranslationEngine._clean_response('Here is the translation:\n\n這是正文') == '這是正文'

    def test_removes_markdown_fences(self):
        assert TranslationEngine._clean_response('```\n這是正文\n```') == '這是正文'

    def test_removes_separator_lines(self):
        assert TranslationEngine._clean_response('---\n這是正文\n---') == '這是正文'

    def test_clean_text_passes_through(self):
        assert TranslationEngine._clean_response('這是完全正常的翻譯文本。') == '這是完全正常的翻譯文本。'

    def test_removes_markdown_heading_prefix(self):
        assert TranslationEngine._clean_response('# 霧港測量學（Harbor Fog Metrics）') == '霧港測量學（Harbor Fog Metrics）'
        assert TranslationEngine._clean_response('## 第一章 簡介') == '第一章 簡介'
        assert TranslationEngine._clean_response('### 小節標題') == '小節標題'

    def test_removes_markdown_bold(self):
        assert TranslationEngine._clean_response('霧港測量更是一個**跨領域**概念') == '霧港測量更是一個跨領域概念'
        assert TranslationEngine._clean_response('這是__粗體__文字') == '這是粗體文字'

    def test_removes_markdown_italic(self):
        assert TranslationEngine._clean_response('這是一個*重要*概念') == '這是一個重要概念'
        assert TranslationEngine._clean_response('這是一個_重要_概念') == '這是一個重要概念'

    def test_preserves_normal_asterisks_and_underscores(self):
        # Asterisk in multiplication should not be stripped
        assert TranslationEngine._clean_response('計算 3*5 的結果') == '計算 3*5 的結果'
        # Underscores in variable names should not be stripped
        assert TranslationEngine._clean_response('使用 my_variable_name 變數') == '使用 my_variable_name 變數'

    def test_removes_multiple_markdown_in_same_text(self):
        text = '# 標題\n\n這是**粗體**和*斜體*混合的文字'
        expected = '標題\n\n這是粗體和斜體混合的文字'
        assert TranslationEngine._clean_response(text) == expected

    def test_removes_markdown_heading_multiline(self):
        text = '第一行\n## 第二行標題\n第三行'
        expected = '第一行\n第二行標題\n第三行'
        assert TranslationEngine._clean_response(text) == expected


class TestSegmentTypePrompts:

    def test_code_uses_code_prompt(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'def hello():\n    # 這是註解\n    print("Hello")'

        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            engine.translate('def hello():\n    # This is a comment\n    print("Hello")',
                           segment_type=SegmentType.CODE)

        prompt = mock_run.call_args[0][0][2]
        assert '註解' in prompt

    def test_text_with_html_uses_html_prompt(self):
        """Text segments with HTML should use TEXT_HTML_PROMPT."""
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '<h1 k="1">標題</h1>'

        html = '<h1>Introduction</h1>'
        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            engine.translate('Introduction', segment_type=SegmentType.TEXT, html=html)

        prompt = mock_run.call_args[0][0][2]
        assert 'HTML 標籤結構' in prompt
        assert '<h1 k="1">Introduction</h1>' in prompt

    def test_glossary_included_in_prompt(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯'

        glossary = 'machine learning → 機器學習\ndeep learning → 深度學習'
        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            engine.translate('test', glossary_text=glossary)

        prompt = mock_run.call_args[0][0][2]
        assert '術語表' in prompt
        assert 'machine learning' in prompt

    def test_context_included_in_prompt(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯'

        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            engine.translate('current text',
                           context_before='previous text',
                           context_after='next text')

        prompt = mock_run.call_args[0][0][2]
        assert '前文參考' in prompt
        assert 'previous text' in prompt
        assert '後文參考' in prompt
        assert 'next text' in prompt


class TestHTMLMode:

    def test_text_with_html_uses_html_prompt(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '<p k="1">翻譯結果</p>'

        html = '<p>Source text</p>'
        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            result = engine.translate('Source text', segment_type=SegmentType.TEXT, html=html)

        prompt = mock_run.call_args[0][0][2]
        assert 'HTML 標籤結構' in prompt
        assert '<p k="1">Source text</p>' in prompt
        # Should preserve HTML in response (no markdown stripping)
        assert result == '<p>翻譯結果</p>'

    def test_text_without_html_uses_plain_prompt(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯結果'

        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            engine.translate('Source text', segment_type=SegmentType.TEXT)

        prompt = mock_run.call_args[0][0][2]
        assert '原文：' in prompt

    def test_code_ignores_html_param(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'code'

        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            engine.translate('code', segment_type=SegmentType.CODE, html='<pre><code>code</code></pre>')

        prompt = mock_run.call_args[0][0][2]
        assert '註解' in prompt

    def test_html_mode_preserves_html_tags(self):
        """HTML mode should not strip bold/italic HTML tags."""
        result = TranslationEngine._clean_response(
            '<p>這是<strong>粗體</strong>和<em>斜體</em>文字</p>',
            html_mode=True,
        )
        assert '<strong>' in result
        assert '<em>' in result

    def test_html_mode_still_strips_prefixes(self):
        """HTML mode should still remove Claude's commentary prefixes."""
        result = TranslationEngine._clean_response(
            '以下是翻譯結果：\n\n<p>翻譯內容</p>',
            html_mode=True,
        )
        assert result == '<p>翻譯內容</p>'

    def test_html_prompt_includes_glossary(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '<p k="1">翻譯</p>'

        glossary = 'machine learning → 機器學習'
        html = '<p>Machine learning is important.</p>'
        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            engine.translate('Machine learning is important.',
                           segment_type=SegmentType.TEXT, html=html, glossary_text=glossary)

        prompt = mock_run.call_args[0][0][2]
        assert '術語表' in prompt
        assert 'machine learning' in prompt
        assert 'HTML 標籤結構' in prompt


class TestModelSelection:

    def test_default_model_is_sonnet(self):
        assert default_model() == 'sonnet'
        engine = TranslationEngine()
        assert engine.model == 'sonnet'

    def test_available_models_list(self):
        aliases = [m['alias'] for m in available_models()]
        assert 'sonnet' in aliases
        assert 'opus' in aliases
        assert 'haiku' in aliases

    def test_custom_model_passed_to_subprocess(self):
        engine = TranslationEngine(model='opus')
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯'

        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            engine.translate('test')

        cmd = mock_run.call_args[0][0]
        assert '--model' in cmd
        assert 'opus' in cmd

    def test_default_model_passed_to_subprocess(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯'

        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            engine.translate('test')

        cmd = mock_run.call_args[0][0]
        from src.llm.claude_cli import LEAN_FLAGS
        assert cmd == ['claude', '-p', mock_run.call_args[0][0][2], '--model', 'sonnet'] + list(LEAN_FLAGS)

    def test_model_changeable_between_calls(self):
        engine = TranslationEngine(model='sonnet')
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯'

        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            engine.translate('test')
            assert mock_run.call_args[0][0][4] == 'sonnet'

            engine.model = 'haiku'
            engine.translate('test')
            assert mock_run.call_args[0][0][4] == 'haiku'


# A long one-line source, used where the reply is only a commentary line
LONG_SOURCE = ('After the configuration file has been loaded and checked, '
               'the output of the first run looks like this:')

CODE_SAMPLE = (
    '    # load config\n'
    '    def __init__(self, *args, **kwargs):\n'
    '        self.__dict__ = {}\n'
    '        # second comment\n'
    '        return a * b * c'
)


class TestCodeResponseCleaning:
    """Bug 3: code output must not go through markdown cleanup."""

    def test_code_sample_unchanged(self):
        result = TranslationEngine._clean_response(CODE_SAMPLE, code_mode=True)
        assert result == CODE_SAMPLE

    def test_code_translate_keeps_code_and_first_line_indent(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = CODE_SAMPLE + '\n'
        mock_result.stderr = ''

        with patch('src.llm.cli.run_process', return_value=mock_result):
            result = engine.translate(CODE_SAMPLE, segment_type=SegmentType.CODE)

        assert result == CODE_SAMPLE

    def test_code_wrapping_fence_removed(self):
        fenced = '```python\n' + CODE_SAMPLE + '\n```\n'
        result = TranslationEngine._clean_response(fenced, code_mode=True)
        assert result == CODE_SAMPLE

    def test_code_surrounding_blank_lines_trimmed(self):
        result = TranslationEngine._clean_response(
            '\n\n' + CODE_SAMPLE + '  \n\n', code_mode=True)
        assert result == CODE_SAMPLE

    def test_code_without_fence_left_alone(self):
        # Prose commentary patterns are not applied to unfenced code
        code = 'Here is = 1\n---\nx = 2'
        assert TranslationEngine._clean_response(
            code, code_mode=True, source=code) == code

    @pytest.mark.parametrize('preface', [
        '看起來這段程式碼沒有需要翻譯的註解，原樣輸出：',
        '以下是翻譯後的程式碼：',
        "Here's the code with translated comments:",
    ])
    def test_code_preface_and_fence_removed(self, preface):
        reply = preface + '\n```python\n' + CODE_SAMPLE + '\n```\n'
        result = TranslationEngine._clean_response(
            reply, code_mode=True, source=CODE_SAMPLE)
        assert result == CODE_SAMPLE

    def test_code_preface_kept_when_source_has_fence(self):
        source = 'Here is an example:\n```\nx = 1\n```'
        assert TranslationEngine._clean_response(
            source, code_mode=True, source=source) == source

    def test_code_fence_kept_when_source_has_fence(self):
        source = '```\nx = 1\n```'
        assert TranslationEngine._clean_response(
            source, code_mode=True, source=source) == source

    @pytest.mark.parametrize('preface', [
        '以下是翻譯後的程式碼：', "Here's the code:", 'The translation:'])
    def test_code_preface_before_unfenced_code_removed(self, preface):
        reply = preface + '\n\n# 載入設定\nx = 1'
        assert TranslationEngine._clean_response(
            reply, code_mode=True, source='# load config\nx = 1') == '# 載入設定\nx = 1'

    def test_code_preface_kept_when_source_starts_like_one(self):
        source = '\n以下是 = 1\nx = 2'
        reply = '以下是 = 1\nx = 2'
        assert TranslationEngine._clean_response(
            reply, code_mode=True, source=source) == reply

    def test_code_only_first_line_can_be_a_preface(self):
        reply = 'x = 1\nHere is = 2'
        assert TranslationEngine._clean_response(
            reply, code_mode=True, source='x = 1\ny = 2') == reply

    def test_code_preface_removed_but_fence_with_trailing_text_kept(self):
        reply = '以下是：\n```\nx = 1\n```\ntrailing remark'
        assert TranslationEngine._clean_response(
            reply, code_mode=True, source='x = 1') == '```\nx = 1\n```\ntrailing remark'

    @pytest.mark.parametrize('prefix', ['', '以下是：\n'])
    def test_code_two_fenced_blocks_not_unwrapped(self, prefix):
        blocks = '```\nx = 1\n```\nnote\n```\ny = 2\n```'
        assert TranslationEngine._clean_response(
            prefix + blocks, code_mode=True, source='x = 1\ny = 2') == blocks

    @pytest.mark.parametrize('reply', [
        '```python\n\n```',
        '```python\n```',
        '```\n```\n',
        '以下是翻譯結果：\n```python\n  \n```\n',
        '以下是翻譯結果：\n```python\n```',
        '以下是翻譯結果：\n',
    ])
    def test_code_empty_fence_cleans_to_empty(self, reply):
        assert TranslationEngine._clean_response(
            reply, code_mode=True, source='x = 1') == ''

    def test_code_fence_body_leading_blank_lines_trimmed(self):
        reply = '```python\n\n' + CODE_SAMPLE + '\n```'
        assert TranslationEngine._clean_response(
            reply, code_mode=True, source=CODE_SAMPLE) == CODE_SAMPLE

    def test_code_separator_line_is_not_a_preface(self):
        # Only commentary lines count as a preface, not a '---' separator
        reply = '---\n```\nx = 1\n```'
        assert TranslationEngine._clean_response(
            reply, code_mode=True, source='x = 1') == reply

    def _translate(self, stdout, text='x', **kwargs):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = stdout
        mock_result.stderr = ''
        with patch('src.llm.cli.run_process', return_value=mock_result):
            return engine.translate(text, **kwargs)

    def test_text_without_html_gets_markdown_stripped(self):
        assert self._translate('**粗體**與*斜體*') == '粗體與斜體'

    def test_text_with_html_keeps_markdown_like_characters(self):
        reply = '<p k="1">*重要*與**強調**</p>'
        assert self._translate(reply, html='<p>x</p>') == '<p>*重要*與**強調**</p>'

    def test_code_with_html_argument_still_code_mode(self):
        assert self._translate('    **kwargs\n', text='    **kwargs',
                               segment_type=SegmentType.CODE,
                               html='<pre>x</pre>') == '    **kwargs'

    def test_code_translate_strips_preface_through_engine(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '看起來沒有註解需要翻譯：\n```python\n' + CODE_SAMPLE + '\n```\n'
        mock_result.stderr = ''

        with patch('src.llm.cli.run_process', return_value=mock_result):
            result = engine.translate(CODE_SAMPLE, segment_type=SegmentType.CODE)

        assert result == CODE_SAMPLE

    @pytest.mark.parametrize('reply', [
        '```python\n\n```\n',
        '```python\n```\n',
        '以下是翻譯結果：\n```python\n  \n```\n',
        '以下是翻譯結果：\n```\n```',
    ])
    def test_code_reply_empty_after_cleanup_is_retried(self, reply):
        engine = TranslationEngine(max_calls=2)
        empty, good = MagicMock(), MagicMock()
        empty.returncode = good.returncode = 0
        empty.stderr = good.stderr = ''
        empty.stdout = reply
        good.stdout = 'x = 1\n'

        with patch('src.llm.cli.run_process',
                   side_effect=[empty, good]) as mock_run:
            assert engine.translate('x = 1', segment_type=SegmentType.CODE) == 'x = 1'
        assert mock_run.call_count == 2

    def test_code_reply_of_fence_markers_on_two_lines_is_retried(self):
        # The source holds a fence, so the reply is not unwrapped; backticks
        # and a newline remain
        engine = TranslationEngine(max_calls=2)
        empty, good = MagicMock(), MagicMock()
        empty.returncode = good.returncode = 0
        empty.stderr = good.stderr = ''
        empty.stdout = '```\n```'
        good.stdout = 'print("```")\n'

        with patch('src.llm.cli.run_process',
                   side_effect=[empty, good]) as mock_run:
            assert engine.translate('print("```")',
                                    segment_type=SegmentType.CODE) == 'print("```")'
        assert mock_run.call_count == 2

    @pytest.mark.parametrize('source, segment_type', [
        ('`', SegmentType.TEXT),
        ('`', SegmentType.CODE),
        ('```\n```', SegmentType.CODE),
    ])
    def test_backtick_only_source_and_matching_reply_accepted(self, source, segment_type):
        engine = TranslationEngine(max_calls=1)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = source
        mock_result.stderr = ''

        with patch('src.llm.cli.run_process', return_value=mock_result):
            assert engine.translate(source, segment_type=segment_type) == source

    def test_text_reply_empty_after_cleanup_raises(self):
        engine = TranslationEngine(max_calls=1)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '** **\n'   # markdown cleanup leaves only a space
        mock_result.stderr = ''

        with patch('src.llm.cli.run_process', return_value=mock_result):
            with pytest.raises(TranslationError, match='Empty translation'):
                engine.translate('Some text.')

    @pytest.mark.parametrize('reply', ['```html\n```\n', '```\n```', '```\n  \n```'])
    @pytest.mark.parametrize('html', ['', '<p>Some text.</p>'])
    def test_text_reply_only_fence_is_retried(self, reply, html):
        engine = TranslationEngine(max_calls=2)
        empty, good = MagicMock(), MagicMock()
        empty.returncode = good.returncode = 0
        empty.stderr = good.stderr = ''
        empty.stdout = reply
        good.stdout = '<p k="1">一些文字。</p>\n' if html else '一些文字。\n'

        with patch('src.llm.cli.run_process',
                   side_effect=[empty, good]) as mock_run:
            assert engine.translate('Some text.', html=html) == \
                ('<p>一些文字。</p>' if html else '一些文字。')
        assert mock_run.call_count == 2

    def test_text_reply_only_fence_exhausts(self):
        engine = TranslationEngine(max_calls=2)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '```html\n```\n'
        mock_result.stderr = ''

        with patch('src.llm.cli.run_process',
                   return_value=mock_result) as mock_run:
            with pytest.raises(TranslationError, match='Empty translation'):
                engine.translate('Some text.')
        assert mock_run.call_count == 2

    @pytest.mark.parametrize('html', ['', '<p>x</p>'])
    def test_text_commentary_only_reply_is_stored(self, html):
        # Accepted limitation without HTML: a reply that is only a commentary
        # line is not treated as empty (as on main). Phase 4: with HTML it
        # is not HTML, so it is refused
        engine = TranslationEngine(max_calls=1)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '以下是翻譯結果：\n'
        mock_result.stderr = ''

        with patch('src.llm.cli.run_process', return_value=mock_result):
            if html:
                with pytest.raises(TranslationError) as e:
                    engine.translate(LONG_SOURCE, html=html)
                assert e.value.code == 'not_html'
            else:
                assert engine.translate(LONG_SOURCE, html=html) == '以下是翻譯結果：'

    def test_long_one_line_translation_starting_with_commentary_word_accepted(self):
        engine = TranslationEngine(max_calls=1)
        reply = '當然，這條規則也有例外，我們會在下一章更詳細地討論這些例外情況。'
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = reply + '\n'
        mock_result.stderr = ''

        with patch('src.llm.cli.run_process', return_value=mock_result):
            assert engine.translate(
                'Of course, there are exceptions to this rule, and we will discuss '
                'them in more detail in the next chapter.') == reply

    @pytest.mark.parametrize('source, reply', [
        ('OK', '好的\n'),
        ('Of course', '當然\n'),
        ('Here are the steps:', '以下是步驟：\n'),
        ("Here's an example.", '以下是一個範例：\n'),
        ('以下是：', '以下是：\n'),   # source left untranslated
        ('X', 'X\n'),
    ])
    def test_short_text_reply_accepted(self, source, reply):
        engine = TranslationEngine(max_calls=1)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = reply
        mock_result.stderr = ''

        with patch('src.llm.cli.run_process', return_value=mock_result):
            assert engine.translate(source) == reply.strip()

    def test_code_whitespace_only_output_is_empty(self):
        engine = TranslationEngine(max_calls=1)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '  \n\n'
        mock_result.stderr = ''

        with patch('src.llm.cli.run_process', return_value=mock_result):
            with pytest.raises(TranslationError, match='Empty translation'):
                engine.translate('x = 1', segment_type=SegmentType.CODE)


def _state(segment_id, chapter_id, text, segment_type=SegmentType.TEXT):
    return SegmentState(segment_id=segment_id, chapter_id=chapter_id,
                        html=f'<p>{text}</p>', text=text,
                        word_count=len(text.split()), segment_type=segment_type)


def get_context(segments, segment_id):
    # Imported lazily so each test reports on its own if the helper is missing
    from src.core.translator import get_context as _get_context
    return _get_context(segments, segment_id)


class TestContext:
    """Bug 4: context comes from the right end, chapter and segment type."""

    def test_prompt_uses_tail_of_previous_segment(self):
        engine = TranslationEngine()
        before = 'HEADMARK ' + 'filler ' * 60 + 'TAILMARK'
        assert len(before) > 200

        prompt = engine._build_prompt('Current text', SegmentType.TEXT,
                                      context_before=before)

        assert 'TAILMARK' in prompt
        assert 'HEADMARK' not in prompt
        assert '前文參考（不需翻譯）：\n' + before[-200:] + '\n' in prompt
        assert before[-201:] not in prompt

    def test_prompt_uses_head_of_next_segment(self):
        engine = TranslationEngine()
        after = 'HEADMARK ' + 'filler ' * 60 + 'TAILMARK'

        prompt = engine._build_prompt('Current text', SegmentType.TEXT,
                                      context_after=after)

        assert 'HEADMARK' in prompt
        assert 'TAILMARK' not in prompt
        assert '後文參考（不需翻譯）：\n' + after[:200] + '\n' in prompt
        assert after[:201] not in prompt

    def test_neighbours_in_same_chapter_used(self):
        segments = [
            _state('a_1', 'a', 'Before text.'),
            _state('a_2', 'a', 'Current text.'),
            _state('a_3', 'a', 'After text.'),
        ]
        assert get_context(segments, 'a_2') == ('Before text.', 'After text.')

    def test_neighbour_from_other_chapter_not_used(self):
        segments = [
            _state('a_1', 'a', 'End of chapter a.'),
            _state('b_1', 'b', 'Only segment of chapter b.'),
            _state('c_1', 'c', 'Start of chapter c.'),
        ]
        assert get_context(segments, 'b_1') == ('', '')

    def test_code_neighbour_not_used(self):
        segments = [
            _state('a_1', 'a', 'x = 1', SegmentType.CODE),
            _state('a_2', 'a', 'Current text.'),
            _state('a_3', 'a', 'y = 2', SegmentType.CODE),
        ]
        assert get_context(segments, 'a_2') == ('', '')

    def test_code_neighbours_stepped_over_to_text(self):
        segments = [
            _state('a_1', 'a', 'Text before.'),
            _state('a_2', 'a', 'x = 1', SegmentType.CODE),
            _state('a_3', 'a', 'z = 0', SegmentType.CODE),
            _state('a_4', 'a', 'Current text.'),
            _state('a_5', 'a', 'y = 2', SegmentType.CODE),
            _state('a_6', 'a', 'Text after.'),
        ]
        assert get_context(segments, 'a_4') == ('Text before.', 'Text after.')

    def test_stepping_over_code_stops_at_chapter_boundary(self):
        segments = [
            _state('a_1', 'a', 'Previous chapter text.'),
            _state('b_1', 'b', 'x = 1', SegmentType.CODE),
            _state('b_2', 'b', 'Current text.'),
            _state('b_3', 'b', 'y = 2', SegmentType.CODE),
            _state('c_1', 'c', 'Next chapter text.'),
        ]
        assert get_context(segments, 'b_2') == ('', '')

    def test_first_last_and_unknown_segment(self):
        segments = [_state('a_1', 'a', 'One.'), _state('a_2', 'a', 'Two.')]
        assert get_context(segments, 'a_1') == ('', 'Two.')
        assert get_context(segments, 'a_2') == ('One.', '')
        assert get_context(segments, 'missing') == ('', '')


# --- Phase 1: translate_segment, outcome, diagnostics --------------------------

import os
import threading

from src.core.project import SegmentStatus
from src.core.translator import (
    TranslationOutcome, failure_log_path, record_failure_log, translate_segment,
)
from src.llm import (
    AuthError, CancelledError, LLMError, LLMResponse, RefusalError, TransientError, Usage,
)
from src.llm.fake import FakeBackend


def _seg(seg_id='c1_seg0001', text='One sentence.', seg_type='text'):
    return SegmentState(segment_id=seg_id, chapter_id='c1', html=f'<p>{text}</p>',
                        text=text, word_count=2, segment_type=seg_type)


def _engine(fake, model='haiku', max_calls=4):
    return TranslationEngine(model=model, backend=fake, max_calls=max_calls)


def _translated(engine, seg, segments, log_dir):
    """translate_segment, then what a caller does once the outcome is
    accepted and saved (review round 2)."""
    outcome = translate_segment(engine, seg, segments)
    record_failure_log(log_dir, seg.segment_id, outcome)
    return outcome


class TestTranslateSegment:

    def test_success(self, tmp_path):
        fake = FakeBackend([LLMResponse('<p k="1">一句話。</p>', 'complete', Usage(5, 7), 'haiku')])
        seg = _seg()
        outcome = _translated(_engine(fake), seg, [seg], str(tmp_path))
        assert outcome == TranslationOutcome(text='<p>一句話。</p>',
                                             status=SegmentStatus.TRANSLATED, issues=[],
                                             model='haiku', usage=Usage(5, 7))
        assert fake.requests[0].model == 'haiku'
        assert fake.requests[0].timeout == 120
        assert '<p k="1">One sentence.</p>' in fake.prompts[0]
        assert not os.path.exists(tmp_path / 'failed')

    def test_usage_none_when_backend_reports_none(self):
        seg = _seg()
        outcome = translate_segment(_engine(FakeBackend(['<p k="1">譯文</p>'])), seg, [seg])
        assert outcome.usage is None

    def test_glossary_and_context(self):
        class Glossary:
            entries = {'x': 1}

            def terms_for_prompt(self, text):  # entries since Phase 5 (glossary check)
                return [GlossaryEntry('GLOSSMARK', '詞彙')] if 'One' in text else []

            def format_for_prompt(self, found):
                return 'GLOSSMARK → 詞彙'

        segs = [_seg('c1_seg0001', 'Before it.'), _seg('c1_seg0002', 'One here.'),
                _seg('c1_seg0003', 'After it.')]
        fake = FakeBackend(['<p k="1">譯文</p>'])
        translate_segment(_engine(fake), segs[1], segs, Glossary())
        prompt = fake.prompts[0]
        assert 'GLOSSMARK → 詞彙' in prompt
        assert 'Before it.' in prompt and 'After it.' in prompt

    def test_code_segment_sent_as_code(self):
        seg = _seg(text='# set\nx = 1', seg_type='code')
        fake = FakeBackend(['# 設定\nx = 1'])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.text == '# 設定\nx = 1'
        assert '<p>' not in fake.prompts[0]

    @pytest.mark.parametrize('replies,code,calls', [
        # Phase 4: four calls per segment; an empty reply gets one retry
        ([TransientError('exit 1')] * 4, 'transient', 4),
        (['', '```\n```'], 'empty_reply', 2),
        ([RefusalError('no')], 'refusal', 1),
        ([LLMError('could not be started')], 'error', 1),
        ([TransientError('exit 1'), TransientError('exit 1'), '  ', '  '], 'empty_reply', 4),
        ([TransientError('exit 1')] * 3 + ['  '], 'empty_reply', 4),
    ])
    def test_failure_is_an_outcome(self, replies, code, calls):
        fake = FakeBackend(list(replies))
        seg = _seg()
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.status == SegmentStatus.FAILED
        assert outcome.text is None
        assert outcome.model == 'haiku'
        assert len(outcome.issues) == 1
        issue = outcome.issues[0]
        assert (issue.kind, issue.code, issue.blocking) == ('backend', code, True)
        assert len(fake.requests) == calls

    def test_transient_then_success_retries(self):
        fake = FakeBackend([TransientError('exit 1'), '<p k="1">譯文</p>'])
        seg = _seg()
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.status == SegmentStatus.TRANSLATED
        assert len(fake.requests) == 2

    @pytest.mark.parametrize('error', [RateLimitError('limit'), AuthError('login'),
                                       CancelledError('cancelled')])
    def test_run_level_errors_raised_without_retry(self, error, tmp_path):
        fake = FakeBackend([error, '<p k="1">譯文</p>'])
        seg = _seg()
        with pytest.raises(type(error)):
            _translated(_engine(fake), seg, [seg], str(tmp_path))
        assert len(fake.requests) == 1
        assert not os.path.exists(tmp_path / 'failed')

    def test_cancel_event_reaches_backend(self):
        seen = []

        def responder(request):
            return '<p k="1">譯文</p>'

        class Recording(FakeBackend):
            def complete(self, request, cancel):
                seen.append(cancel)
                return super().complete(request, cancel)

        cancel = threading.Event()
        seg = _seg()
        translate_segment(_engine(Recording(responder=responder)), seg, [seg], cancel=cancel)
        assert seen == [cancel]

    def test_engine_without_cancel_gets_an_unset_event(self):
        seen = []

        class Recording(FakeBackend):
            def complete(self, request, cancel):
                seen.append(cancel)
                return super().complete(request, cancel)

        _engine(Recording(['<p k="1">譯文</p>'])).translate('x')
        assert isinstance(seen[0], threading.Event) and not seen[0].is_set()

    def test_engine_uses_default_backend(self):
        from tests.conftest import use_fake_backend
        fake = FakeBackend(['<p k="1">譯文</p>'])
        with use_fake_backend(fake):
            engine = TranslationEngine(model='opus')
        assert engine.backend is fake


class TestFailureDiagnostics:

    def _fail(self, tmp_path, replies, seg_id='c1_seg0001'):
        seg = _seg(seg_id)
        return _translated(_engine(FakeBackend(replies)), seg, [seg], str(tmp_path))

    def test_request_and_raw_replies_written(self, tmp_path):
        errors = [TransientError(f'exit {i}', raw=f'RAW-{i}') for i in (1, 2)]
        self._fail(tmp_path, errors + ['   ', '  '])
        files = os.listdir(tmp_path / 'failed')
        assert files == ['c1_seg0001-1f098b794d.txt']
        content = (tmp_path / 'failed' / 'c1_seg0001-1f098b794d.txt').read_text(encoding='utf-8')
        assert 'segment: c1_seg0001' in content
        assert 'model: haiku' in content
        assert '=== request ===' in content
        assert '<p k="1">One sentence.</p>' in content    # the prompt sent
        assert '=== attempt 1: exit 1 ===\nRAW-1' in content
        assert '=== attempt 2: exit 2 ===\nRAW-2' in content
        assert '=== attempt 3: empty_reply: 回覆是空的 ===\n   \n' in content
        assert '=== attempt 4: empty_reply: 回覆是空的 ===\n  \n' in content
        assert 'error: Empty translation after 4 attempts' in content

    def test_bounded_one_file_per_segment(self, tmp_path):
        for _ in range(3):
            self._fail(tmp_path, [TransientError('x', raw='y' * 50000)] * 4)
        files = os.listdir(tmp_path / 'failed')
        assert files == ['c1_seg0001-1f098b794d.txt']
        size = os.path.getsize(tmp_path / 'failed' / 'c1_seg0001-1f098b794d.txt')
        assert size < 4 * 20000 + 6000

    def test_success_writes_nothing_and_removes_old_record(self, tmp_path):
        self._fail(tmp_path, [RefusalError('no')])
        assert os.listdir(tmp_path / 'failed') == ['c1_seg0001-1f098b794d.txt']
        self._fail(tmp_path, ['<p k="1">譯文</p>'])
        assert os.listdir(tmp_path / 'failed') == []

    def test_unsafe_characters_in_id(self, tmp_path):
        path = failure_log_path(str(tmp_path), 'OEBPS/ch 1.xhtml_seg0001')
        assert path == os.path.join(str(tmp_path), 'failed', 'OEBPS_ch_1.xhtml_seg0001-b7bd8e88e1.txt')

    def test_nothing_written_without_log_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        seg = _seg()
        outcome = translate_segment(_engine(FakeBackend([RefusalError('no')])), seg, [seg])
        assert outcome.status == SegmentStatus.FAILED
        assert os.listdir(tmp_path) == []


class TestRequestAndMessages:
    """Exact request, error messages and log lines (Phase 1 mutation survivors)."""

    def test_request_is_one_user_message_without_system(self):
        from src.llm import Message
        fake = FakeBackend(['<p k="1">譯文</p>'])
        engine = _engine(fake)
        engine.translate('Some text.', SegmentType.TEXT, '', '', '', '<p>Some text.</p>')
        request = fake.requests[0]
        prompt = engine._build_prompt('Some text.', SegmentType.TEXT, html='<p k="1">Some text.</p>')
        assert request.system == ''
        assert request.messages == [Message('user', prompt)]

    def test_prompt_passed_to_cli_unchanged(self):
        mock_result = MagicMock(returncode=0, stdout='<p k="1">譯文</p>', stderr='')
        engine = TranslationEngine()
        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            engine.translate('Some text.')
        assert mock_run.call_args[0][0][2] == engine._build_prompt('Some text.', SegmentType.TEXT)

    def test_default_four_calls(self):
        fake = FakeBackend([TransientError('x')] * 4)
        with pytest.raises(TranslationError):
            TranslationEngine(backend=fake).translate('x')
        assert len(fake.requests) == 4

    def test_exhaustion_messages(self):
        with pytest.raises(TranslationError) as info:
            _engine(FakeBackend([TransientError('a'), TransientError('boom')]),
                    max_calls=2).translate('x')
        assert str(info.value) == 'Translation failed after 2 attempts: boom'
        with pytest.raises(TranslationError) as info:
            _engine(FakeBackend(['', '']), max_calls=2).translate('x')
        assert str(info.value) == 'Empty translation after 2 attempts'

    def test_log_lines(self, caplog):
        import logging
        caplog.set_level(logging.WARNING, logger='src.core.translator')
        with pytest.raises(TranslationError):
            _engine(FakeBackend([TransientError('boom'), '']), max_calls=2).translate('x')
        assert [r.getMessage() for r in caplog.records] == [
            'Translation attempt 1/2 failed: boom',
            'Empty translation result (attempt 2/2)',
        ]

    def test_no_glossary_section_without_glossary(self):
        fake = FakeBackend(['<p k="1">譯文</p>'])
        seg = _seg()
        translate_segment(_engine(fake), seg, [seg])
        assert '術語表' not in fake.prompts[0]


class TestFailureLogFormat:

    def test_exact_content(self, tmp_path, monkeypatch):
        import src.core.translator as translator
        monkeypatch.setattr(translator.time, 'strftime',
                            lambda fmt: 'STAMP' if fmt == '%Y-%m-%d %H:%M:%S' else 'WRONG')
        error = TranslationError('final message', code='transient', prompt='THE PROMPT',
                                 attempts=[('first', 'R1'), ('second', 'x' * 20001)])
        translator.write_failure_log(str(tmp_path), 'c1_seg0001', 'haiku', error)
        content = (tmp_path / 'failed' / 'c1_seg0001-1f098b794d.txt').read_text(encoding='utf-8')
        assert content == (
            'segment: c1_seg0001\nmodel: haiku\ntime: STAMP\nerror: final message\n\n'
            '=== request ===\nTHE PROMPT\n\n'
            '=== attempt 1: first ===\nR1\n\n'
            '=== attempt 2: second ===\n' + 'x' * 20000 + '\n')


# --- Review round 1 ---------------------------------------------------------------

from src.core.translator import remove_failure_log
from src.llm import BackendUnavailableError
from src.llm.claude_cli import ClaudeCLI
from src.llm.cli import ProcessResult


class TestFailureLogNames:
    """Item 21: distinct ids never share a failure log file."""

    def test_ids_with_same_sanitized_form(self, tmp_path):
        a = failure_log_path(str(tmp_path), 'a/b_seg0001')
        b = failure_log_path(str(tmp_path), 'a_b_seg0001')
        assert a != b
        assert os.path.basename(a).startswith('a_b_seg0001-')

    def test_success_of_one_keeps_the_other_log(self, tmp_path):
        seg_a, seg_b = _seg('a/b_seg0001'), _seg('a_b_seg0001')
        _translated(_engine(FakeBackend([RefusalError('no')])), seg_a, [seg_a],
                          str(tmp_path))
        _translated(_engine(FakeBackend(['<p k="1">譯文</p>'])), seg_b, [seg_b], str(tmp_path))
        assert os.path.exists(failure_log_path(str(tmp_path), 'a/b_seg0001'))

    def test_long_id_bounded(self, tmp_path):
        name = os.path.basename(failure_log_path(str(tmp_path), 'x' * 1000))
        assert len(name) == 100 + 1 + 10 + 4


class TestDiagnosticWriteFailure:
    """Item 9: a failure log that cannot be written does not lose the outcome."""

    def test_outcome_returned_and_error_logged(self, tmp_path, caplog):
        (tmp_path / 'failed').write_text('a file where the directory should be')
        seg = _seg()
        with caplog.at_level('ERROR', logger='src.core.translator'):
            outcome = _translated(_engine(FakeBackend([RefusalError('no')])), seg, [seg],
                                        str(tmp_path))
        assert outcome.status == SegmentStatus.FAILED
        assert any('無法寫入失敗紀錄 c1_seg0001' in r.getMessage() for r in caplog.records)

    def test_removal_failure_logged(self, tmp_path, caplog):
        os.makedirs(failure_log_path(str(tmp_path), 'c1_seg0001'))  # a directory: cannot remove
        seg = _seg()
        with caplog.at_level('ERROR', logger='src.core.translator'):
            outcome = _translated(_engine(FakeBackend(['<p k="1">譯文</p>'])), seg, [seg], str(tmp_path))
        assert outcome.status == SegmentStatus.TRANSLATED
        assert any('無法移除失敗紀錄 c1_seg0001' in r.getMessage() for r in caplog.records)

    def test_remove_failure_log_missing_file(self, tmp_path):
        remove_failure_log(str(tmp_path), 'c1_seg0001')  # nothing to remove: no error


class TestBoundedErrorText:
    """Item 13: response-derived text is bounded in the log and in the issue."""

    def test_huge_stderr_through_claude_classification(self, tmp_path):
        huge = 'E' * 3_000_000
        seg = _seg()
        engine = TranslationEngine(model='haiku', backend=ClaudeCLI())
        with patch('src.llm.cli.run_process', return_value=ProcessResult(1, '', huge)):
            outcome = _translated(engine, seg, [seg], str(tmp_path))
        assert outcome.status == SegmentStatus.FAILED
        assert len(outcome.issues[0].detail) <= 2000
        size = os.path.getsize(failure_log_path(str(tmp_path), 'c1_seg0001'))
        assert size < 4 * (20000 + 2000) + 20000  # four calls

    def test_huge_error_message_from_any_backend(self, tmp_path):
        seg = _seg()
        errors = [TransientError('M' * 1_000_000, raw='R')] * 4
        outcome = _translated(_engine(FakeBackend(errors)), seg, [seg], str(tmp_path))
        assert len(outcome.issues[0].detail) == 2000
        # the prompt plus 4 bounded messages
        assert os.path.getsize(failure_log_path(str(tmp_path), 'c1_seg0001')) < 20000


class TestRunLevelErrors:
    """Item 15: BackendUnavailableError is raised, never a FAILED outcome."""

    def test_raised(self, tmp_path):
        seg = _seg()
        fake = FakeBackend([BackendUnavailableError('could not be started')])
        with pytest.raises(BackendUnavailableError):
            _translated(_engine(fake), seg, [seg], str(tmp_path))
        assert len(fake.requests) == 1
        assert not os.path.exists(tmp_path / 'failed')


class TestEngineDefaultModel:
    """Item 12: the engine's default model is the backend's."""

    def test_backend_default(self):
        assert TranslationEngine(backend=FakeBackend()).model == 'fake-model'


class TestRound1Exact:
    """Exact values of the round 1 translator code (mutation survivors)."""

    def test_unexpected_error_outcome(self):
        from src.core.project import Issue
        from src.core.translator import unexpected_error_outcome
        outcome = unexpected_error_outcome('m', KeyError('k'), '')
        assert (outcome.text, outcome.status, outcome.model, outcome.usage) == \
            (None, SegmentStatus.FAILED, 'm', None)
        assert outcome.issues == [Issue(kind='backend', code='unexpected',
                                        detail="未預期的錯誤：KeyError: 'k'", blocking=True)]
        long = unexpected_error_outcome('m', ValueError('v' * 5000), '')
        assert len(long.issues[0].detail) == 2000

    def test_log_messages_exact(self, tmp_path, caplog):
        (tmp_path / 'failed').write_text('not a directory')
        seg = _seg()
        with caplog.at_level('ERROR', logger='src.core.translator'):
            _translated(_engine(FakeBackend([RefusalError('no')])), seg, [seg],
                              str(tmp_path))
        assert caplog.records[-1].getMessage().startswith('無法寫入失敗紀錄 c1_seg0001: ')
        caplog.clear()
        os.remove(tmp_path / 'failed')
        os.makedirs(failure_log_path(str(tmp_path), 'c1_seg0001'))
        with caplog.at_level('ERROR', logger='src.core.translator'):
            _translated(_engine(FakeBackend(['<p k="1">譯文</p>'])), seg, [seg], str(tmp_path))
        assert caplog.records[-1].getMessage().startswith('無法移除失敗紀錄 c1_seg0001: ')

    def test_second_failure_replaces_the_log(self, tmp_path):
        seg = _seg()
        for detail in ('first', 'second'):
            _translated(_engine(FakeBackend([RefusalError(detail)])), seg, [seg],
                              str(tmp_path))
        content = open(failure_log_path(str(tmp_path), 'c1_seg0001'), encoding='utf-8').read()
        assert '=== attempt 1: second ===' in content


# --- Review round 2 ---------------------------------------------------------------

import errno


def _claude_engine(monkeypatch, run):
    """An engine on the real ClaudeCLI backend with run_process replaced."""
    monkeypatch.setattr('src.llm.cli.run_process', run)
    return TranslationEngine(model='haiku', backend=ClaudeCLI())


class TestRound2StartErrorFailsSegment:
    """Item 4: a start failure other than a missing program fails the segment."""

    def test_e2big_is_a_failed_outcome(self, monkeypatch, tmp_path):
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            raise OSError(errno.E2BIG, 'Argument list too long')

        seg = _seg()
        outcome = _translated(_claude_engine(monkeypatch, run), seg, [seg], str(tmp_path))
        assert outcome.status == SegmentStatus.FAILED
        assert outcome.issues[0].code == 'error'
        assert 'could not be started' in outcome.issues[0].detail
        assert len(calls) == 1  # not retried
        assert os.path.exists(failure_log_path(str(tmp_path), 'c1_seg0001'))


class TestRound2UnexpectedErrorInEntryPoint:
    """Item 5: an unexpected exception is a FAILED outcome for every caller."""

    def test_unexpected_exception_from_backend(self):
        seg = _seg()
        fake = FakeBackend(responder=lambda r: {}['missing'])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.status == SegmentStatus.FAILED
        assert outcome.model == 'haiku'
        assert outcome.issues[0].code == 'unexpected'
        assert "KeyError: 'missing'" in outcome.issues[0].detail

    def test_undecodable_output(self, monkeypatch):
        def run(cmd, **kwargs):
            raise UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid start byte')

        seg = _seg()
        outcome = translate_segment(_claude_engine(monkeypatch, run), seg, [seg])
        assert outcome.status == SegmentStatus.FAILED
        assert outcome.issues[0].code == 'unexpected'

    def test_unexpected_exception_logged(self, caplog):
        seg = _seg()
        with caplog.at_level('ERROR', logger='src.core.translator'):
            translate_segment(_engine(FakeBackend([KeyError('k')])), seg, [seg])
        assert caplog.records[-1].getMessage() == '未預期的錯誤: c1_seg0001'

    @pytest.mark.parametrize('error', [AuthError('a'), CancelledError('c'),
                                       BackendUnavailableError('b')])
    def test_run_level_errors_still_raised(self, error):
        seg = _seg()
        with pytest.raises(type(error)):
            translate_segment(_engine(FakeBackend([error])), seg, [seg])


class TestRound2EntryPointWritesNoFile:
    """Items 10, 17: translate_segment writes and removes no failure log; the
    caller records it where it accepts and saves the outcome."""

    def test_failure_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        seg = _seg()
        outcome = translate_segment(_engine(FakeBackend([RefusalError('no')])), seg, [seg])
        assert outcome.status == SegmentStatus.FAILED
        assert outcome.failure.code == 'refusal' and outcome.failure.attempts == [('no', '')]
        assert os.listdir(tmp_path) == []

    def test_success_keeps_an_existing_log(self, tmp_path):
        seg = _seg()
        _translated(_engine(FakeBackend([RefusalError('no')])), seg, [seg], str(tmp_path))
        outcome = translate_segment(_engine(FakeBackend(['<p k="1">譯文</p>'])), seg, [seg])
        assert outcome.status == SegmentStatus.TRANSLATED and outcome.failure is None
        assert os.path.exists(failure_log_path(str(tmp_path), 'c1_seg0001'))

    def test_record_unexpected_failure_replaces_older_log(self, tmp_path):
        # Review round 3, item 7: was "keeps older log" (the unexpected
        # outcome had no diagnostics); it now writes its own
        from src.core.translator import unexpected_error_outcome
        seg = _seg()
        _translated(_engine(FakeBackend([RefusalError('first')])), seg, [seg], str(tmp_path))
        record_failure_log(str(tmp_path), 'c1_seg0001',
                           unexpected_error_outcome('m', KeyError('k'), ''))
        content = open(failure_log_path(str(tmp_path), 'c1_seg0001'), encoding='utf-8').read()
        assert "error: 未預期的錯誤：KeyError: 'k'" in content
        assert 'first' not in content

    def test_outcomes_compare_without_the_error(self):
        seg = _seg()
        a = translate_segment(_engine(FakeBackend([RefusalError('no')])), seg, [seg])
        b = translate_segment(_engine(FakeBackend([RefusalError('no')])), seg, [seg])
        assert a == b and a.failure is not b.failure
        assert 'failure' not in repr(a)


class TestRound2FailureLogNameBytes:
    """Item 13: the readable prefix is ASCII, so the name fits 255 bytes."""

    def test_cjk_id(self, tmp_path):
        seg_id = '章' * 100 + '_seg0001'
        name = os.path.basename(failure_log_path(str(tmp_path), seg_id))
        assert name.isascii()
        assert len(name.encode('utf-8')) <= 255
        assert name.startswith('_' * 100 + '-')

    def test_cjk_id_log_written(self, tmp_path):
        seg = _seg('章' * 100 + '_seg0001')
        _translated(_engine(FakeBackend([RefusalError('no')])), seg, [seg],
                          str(tmp_path))
        assert os.path.exists(failure_log_path(str(tmp_path), seg.segment_id))

    def test_ascii_kept(self, tmp_path):
        name = os.path.basename(failure_log_path(str(tmp_path), 'OEBPS/ch-1.x_seg0001'))
        assert name.startswith('OEBPS_ch-1.x_seg0001-')


class TestRound2CodeEchoingAnErrorPhrase:
    """Item 15: a short code segment that mentions an error phrase translates."""

    CODE = 'if err["type"] == "authentication_error":\n    refresh()'

    def test_code_segment_translated(self, monkeypatch):
        def run(cmd, **kwargs):
            return ProcessResult(0, self.CODE, '')

        seg = _seg(text=self.CODE, seg_type='code')
        outcome = translate_segment(_claude_engine(monkeypatch, run), seg, [seg])
        assert outcome.status == SegmentStatus.TRANSLATED
        assert outcome.text == self.CODE


# --- Review round 3 ---------------------------------------------------------------

from src.core.translator import MAX_CONSECUTIVE_UNEXPECTED, UnexpectedStreak
from src.llm.cli import run_process as _real_run_process  # before the autouse stub


class TestRound3UnexpectedFailureLog:
    """Item 7: an unexpected failure writes a short failure log."""

    def test_log_has_type_message_traceback_and_request(self, tmp_path):
        seg = _seg(text='Unique source words.')
        fake = FakeBackend(responder=lambda r: {}['missing'])
        outcome = _translated(_engine(fake), seg, [seg], str(tmp_path))
        assert outcome.failure.code == 'unexpected'
        content = open(failure_log_path(str(tmp_path), 'c1_seg0001'), encoding='utf-8').read()
        assert "error: 未預期的錯誤：KeyError: 'missing'" in content
        assert '=== request ===\n' + fake.prompts[0] + '\n' in content
        assert "=== attempt 1: KeyError: 'missing' ===" in content
        assert 'Traceback (most recent call last):\n  File ' in content
        assert content.rstrip().endswith("KeyError: 'missing'")

    def test_request_empty_when_not_built(self, tmp_path, monkeypatch):
        monkeypatch.setattr('src.core.translator.get_context',
                            lambda *a: (_ for _ in ()).throw(ValueError('broken')))
        seg = _seg()
        outcome = _translated(_engine(FakeBackend([])), seg, [seg], str(tmp_path))
        assert outcome.issues[0].code == 'unexpected'
        assert outcome.failure.prompt == ''
        assert os.path.exists(failure_log_path(str(tmp_path), 'c1_seg0001'))

    def test_request_empty_when_it_cannot_be_rebuilt(self, monkeypatch):
        seg = _seg()
        engine = _engine(FakeBackend(responder=lambda r: {}['missing']))
        real = engine._build_prompt
        calls = []

        def build(*args, **kwargs):
            calls.append(1)
            if len(calls) > 1:
                raise RuntimeError('no')
            return real(*args, **kwargs)

        monkeypatch.setattr(engine, '_build_prompt', build)
        outcome = translate_segment(engine, seg, [seg])
        assert outcome.failure.prompt == ''

    def test_undecodable_output_through_the_real_process(self, monkeypatch, tmp_path):
        import sys

        def run(cmd, **kwargs):
            return _real_run_process(
                [sys.executable, '-c', 'import sys; sys.stdout.buffer.write(b\'<p k="1">\\xff\\xe8\\xad\\xaf</p>\')'],
                **kwargs)

        seg = _seg()
        outcome = translate_segment(_claude_engine(monkeypatch, run), seg, [seg])
        assert outcome.status == SegmentStatus.TRANSLATED
        assert outcome.text == '<p>\ufffd譯</p>'


class TestRound3UnexpectedStreak:
    """Item 9: five unexpected failures in a row stop the run."""

    def _unexpected(self):
        from src.core.translator import unexpected_error_outcome
        return unexpected_error_outcome('m', KeyError('k'), '')

    def test_threshold(self):
        assert MAX_CONSECUTIVE_UNEXPECTED == 5
        streak = UnexpectedStreak()
        for _ in range(4):
            assert streak.add(self._unexpected()) is None
        assert streak.add(self._unexpected()) == (
            "連續 5 個段落發生未預期的錯誤，翻譯已停止（最後一個：未預期的錯誤：KeyError: 'k'）")

    @pytest.mark.parametrize('other', ['translated', 'failed', None])
    def test_other_result_resets(self, other):
        if other == 'translated':
            reset = TranslationOutcome(text='x', status=SegmentStatus.TRANSLATED, issues=[],
                                       model='m', usage=None)
        elif other == 'failed':
            seg = _seg()
            reset = translate_segment(_engine(FakeBackend([RefusalError('no')])), seg, [seg])
        else:
            reset = None
        streak = UnexpectedStreak()
        for _ in range(4):
            streak.add(self._unexpected())
        assert streak.add(reset) is None
        for _ in range(4):
            assert streak.add(self._unexpected()) is None
        assert streak.add(self._unexpected()) is not None


# --- Phase 4: protection, validation, retry ------------------------------------------

from src.core.translator import FEEDBACK_PROMPT, MAX_CALLS, Reply
from src.epub.blocks import fingerprint

SOURCE_HTML = ('<p class="c3" id="p12">See <a href="ch02.xhtml#fn3" id="r3"><sup>3</sup></a> '
               'for the <em class="i">core idea</em>.</p>\n<p>Second <b>bold</b> one.</p>')
PROTECTED = ('<p k="1">See <a k="2"><sup>3</sup></a> for the <em k="3">core idea</em>.</p>\n'
             '<p k="4">Second <b>bold</b> one.</p>')
GOOD = ('<p k="1">見 <a k="2"><sup>3</sup></a> 的<em k="3">核心概念</em>。</p>\n'
        '<p k="4">第二個<b>粗體</b>。</p>')
RESTORED = ('<p class="c3" id="p12">見 <a href="ch02.xhtml#fn3" id="r3"><sup>3</sup></a> '
            '的<em class="i">核心概念</em>。</p>\n<p>第二個<b>粗體</b>。</p>')
DROPPED_B = GOOD.replace('<b>粗體</b>', '粗體')


def _html_seg(html=SOURCE_HTML, seg_type='text', text='See 3 for the core idea.\nSecond bold one.'):
    seg = SegmentState(segment_id='c1_seg0001', chapter_id='c1', html=html, text=text,
                       word_count=8, segment_type=seg_type)
    return seg


class TestPhase4Protection:

    def test_prompt_has_keys_and_reply_is_restored(self):
        fake = FakeBackend([GOOD])
        seg = _html_seg()
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert fake.prompts[0].endswith('\n\n' + PROTECTED)
        assert 'class=' not in fake.prompts[0] and 'href=' not in fake.prompts[0]
        assert (outcome.status, outcome.text, outcome.issues) == \
            (SegmentStatus.TRANSLATED, RESTORED, [])

    def test_bare_run_wrapped_for_the_prompt(self):
        seg = _html_seg('<p>Lead.</p>\nBare <em>text</em> here', text='Lead.\nBare text here')
        fake = FakeBackend(['<p k="1">引。</p>\n<run k="2">裸<em>文字</em></run>'])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert fake.prompts[0].endswith('<p k="1">Lead.</p>\n<run k="2">Bare <em>text</em> here</run>')
        assert outcome.text == '<p>引。</p>\n裸<em>文字</em>'

    def test_prompt_rules(self):
        assert 'k 屬性是標記編號' in TEXT_HTML_PROMPT
        assert '每種標籤的數量必須與原文相同' in TEXT_HTML_PROMPT
        assert '<run>' in TEXT_HTML_PROMPT
        assert FEEDBACK_PROMPT.format(problems='- x') == (
            '你上一次的輸出沒有通過檢查：\n- x\n\n'
            '請修正以上問題，重新輸出完整的譯文。只輸出譯文本身，不要加任何說明。')

    def test_quality_warning_kept_with_the_translation(self):
        seg = _html_seg('<p>Some words</p>', text='Some words')
        outcome = translate_segment(_engine(FakeBackend(['<p k="1">Some words</p>'])), seg, [seg])
        assert outcome.status == SegmentStatus.TRANSLATED
        assert [(i.kind, i.code, i.blocking) for i in outcome.issues] == \
            [('quality', 'untranslated', False)]

    def test_fingerprints_used_for_br_runs(self):
        seg = _html_seg('Line one\nLine two', text='Line one\nLine two')
        seg.fingerprints = [fingerprint('Line one'), fingerprint('Line two')]
        fake = FakeBackend(['<run k="1">第一行第二行</run>', '<run k="1">第一行\n第二行</run>'])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.text == '第一行\n第二行'
        assert len(fake.requests) == 2


class TestPhase4Retry:

    def test_retry_with_the_errors_as_a_follow_up(self):
        fake = FakeBackend([DROPPED_B, GOOD])
        seg = _html_seg()
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.text == RESTORED
        retry = fake.requests[1].messages
        assert [m.role for m in retry] == ['user', 'assistant', 'user']
        assert retry[0].content == fake.requests[0].messages[0].content
        assert retry[1].content == DROPPED_B
        assert retry[2].content == FEEDBACK_PROMPT.format(
            problems='- 第 2 個區塊不帶屬性的 <b>：原文 1 個，譯文 0 個')

    def test_split_after_two_failures(self):
        halves = ['<p k="1">見 <a k="2"><sup>3</sup></a> 的<em k="3">核心概念</em>。</p>',
                  '<p k="1">第二個<b>粗體</b>。</p>']
        fake = FakeBackend([DROPPED_B, DROPPED_B] + halves)
        seg = _html_seg()
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert (outcome.status, outcome.text) == (SegmentStatus.TRANSLATED, RESTORED)
        assert len(fake.requests) == 4
        first, second = fake.requests[2].messages, fake.requests[3].messages
        assert len(first) == len(second) == 1
        assert first[0].content.endswith('\n\n' + PROTECTED.split('\n')[0])
        assert second[0].content.endswith('\n\n<p k="1">Second <b>bold</b> one.</p>')

    def test_failing_half_fails_the_segment(self):
        fake = FakeBackend([DROPPED_B, DROPPED_B, '<p k="1">見 <a k="2"><sup>3</sup></a> 的'
                            '<em k="3">核心</em>。</p>', '<p k="1">第二個粗體。</p>'])
        seg = _html_seg()
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.status == SegmentStatus.FAILED and outcome.text is None
        assert [(i.kind, i.code, i.blocking) for i in outcome.issues] == \
            [('structure', 'tag_count', True)]
        assert outcome.issues[0].detail == '第 2 個區塊不帶屬性的 <b>：原文 1 個，譯文 0 個'
        assert len(fake.requests) == 4
        assert outcome.failure.code == 'tag_count'
        assert [a[0] for a in outcome.failure.attempts] == [
            'tag_count: 第 2 個區塊不帶屬性的 <b>：原文 1 個，譯文 0 個'] * 3  # half 2 counts on
        assert str(outcome.failure) == \
            'Translation failed after 4 attempts: 第 2 個區塊不帶屬性的 <b>：原文 1 個，譯文 0 個'

    def test_first_half_failing_stops_before_the_second(self):
        fake = FakeBackend([DROPPED_B, DROPPED_B, '<p k="1">壞'])
        seg = _html_seg()
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.issues[0].code == 'not_well_formed'
        assert len(fake.requests) == 3

    def test_joined_halves_checked_against_the_whole(self):
        # each half passes on its own; joined, the runs cut by fingerprint do not
        html = '<p>Lead.</p>\nLine one\nLine two'
        seg = _html_seg(html, text='Lead.\nLine one\nLine two')
        seg.fingerprints = [fingerprint('Lead.'), fingerprint('Line one'), fingerprint('Line two')]
        bad = '<p k="1">引。</p>\n<run k="2">一二</run>'
        fake = FakeBackend([bad, bad, '<p k="1">引。</p>', '<run k="1">一二</run>'])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.status == SegmentStatus.FAILED
        assert outcome.issues[0].code == 'structure_mismatch'
        assert len(fake.requests) == 4

    def test_no_split_for_one_block(self):
        fake = FakeBackend(['<p k="1">一</p><p>二</p>', '<p k="1">一</p><p>二</p>'])
        seg = _html_seg('<p>One.</p>', text='One.')
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.issues[0].code == 'structure_mismatch'
        assert len(fake.requests) == 2

    def test_no_split_without_two_calls_left(self):
        fake = FakeBackend([TransientError('x'), DROPPED_B, DROPPED_B])
        seg = _html_seg()
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.issues[0].code == 'tag_count'
        assert len(fake.requests) == 3

    def test_no_retry_when_no_call_is_left(self):
        fake = FakeBackend([TransientError('x')] * 3 + [DROPPED_B])
        seg = _html_seg()
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.issues[0].code == 'tag_count'
        assert len(fake.requests) == MAX_CALLS == 4

    def test_transient_during_split_fails_as_transient(self):
        fake = FakeBackend([DROPPED_B, DROPPED_B, TransientError('t1'), TransientError('t2')])
        seg = _html_seg()
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert [i.code for i in outcome.issues] == ['transient']
        assert outcome.issues[0].kind == 'backend'
        assert len(fake.requests) == 4

    @pytest.mark.parametrize('error', [RateLimitError('limit'), AuthError('login')])
    def test_run_level_errors_raised_mid_retry(self, error):
        fake = FakeBackend([DROPPED_B, error])
        seg = _html_seg()
        with pytest.raises(type(error)):
            translate_segment(_engine(fake), seg, [seg])

    def test_usage_summed_over_the_calls(self):
        fake = FakeBackend([LLMResponse(DROPPED_B, 'complete', Usage(10, 3), 'haiku'),
                            LLMResponse(GOOD, 'complete', Usage(20, 4), 'haiku')])
        seg = _html_seg()
        assert translate_segment(_engine(fake), seg, [seg]).usage == Usage(30, 7)

    def test_usage_none_unless_reported(self):
        fake = FakeBackend([DROPPED_B, LLMResponse(GOOD, 'complete', Usage(2, 1), 'haiku')])
        seg = _html_seg()
        assert translate_segment(_engine(fake), seg, [seg]).usage == Usage(2, 1)

    def test_rejected_replies_logged(self, caplog):
        import logging
        caplog.set_level(logging.WARNING, logger='src.core.translator')
        seg = _html_seg()
        translate_segment(_engine(FakeBackend([DROPPED_B, GOOD])), seg, [seg])
        assert [r.getMessage() for r in caplog.records] == [
            'Translation reply rejected (attempt 1/4): tag_count']

    def test_reply_object(self):
        fake = FakeBackend([GOOD])
        reply = _engine(fake).translate_reply('x', SegmentType.TEXT, '', '', '', SOURCE_HTML)
        assert reply == Reply(RESTORED, None, [])


class TestPhase4BackendIssues:

    @pytest.mark.parametrize('reply', ['I cannot help with that.', '抱歉，我無法翻譯這段內容。',
                                       '譯文沒有標籤'])
    def test_not_html_is_a_backend_issue(self, reply):
        fake = FakeBackend([reply, reply])
        seg = _html_seg('<p>One.</p>', text='One.')
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.status == SegmentStatus.FAILED and outcome.text is None
        assert [(i.kind, i.code, i.blocking) for i in outcome.issues] == \
            [('backend', 'not_html', True)]
        assert outcome.issues[0].detail == '回覆不是 HTML（第一個字元必須是 <）'
        assert len(fake.requests) == 2

    def test_refusal_is_a_backend_issue_without_retry(self):
        fake = FakeBackend([RefusalError('declined')])
        seg = _html_seg()
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert [(i.kind, i.code) for i in outcome.issues] == [('backend', 'refusal')]
        assert len(fake.requests) == 1

    def test_failed_retranslation_keeps_the_previous_text(self):
        from src.core.project import ProjectManager
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            pm = ProjectManager(d)
            from src.core.segmenter import Segment
            pm.create('/x.epub', 'T', 'A', [Segment('c1_seg0001', '<p>One.</p>', 'One.', 1, 'text')])
            pm.mark_translated('c1_seg0001', '<p>舊譯。</p>')
            seg = pm.data.segments[0]
            outcome = translate_segment(_engine(FakeBackend(['nope', 'nope'])), seg, [seg])
            assert pm.apply_outcome('c1_seg0001', outcome)
            assert (seg.status, seg.translated_text) == (SegmentStatus.TRANSLATED, '<p>舊譯。</p>')
            assert seg.issues[0].code == 'not_html'


class TestPhase4Code:

    def test_changed_code_retried_with_feedback(self):
        seg = _html_seg('<pre># set\nx = 1</pre>', 'code', '# set\nx = 1')
        fake = FakeBackend(['# 設定\nx = 2', '# 設定\nx = 1'])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.text == '# 設定\nx = 1'
        assert fake.requests[1].messages[2].content == FEEDBACK_PROMPT.format(
            problems='- 程式碼在註解行以外與原文不同（只能翻譯 # 或 // 開頭的註解行）')

    def test_code_never_split(self):
        seg = _html_seg('<pre>a\n\nb</pre>', 'code', 'a\n\nb')
        fake = FakeBackend(['c', 'c'])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.issues[0].code == 'code_changed'
        assert len(fake.requests) == 2

    def test_commentary_around_code_refused(self):
        seg = _html_seg('<pre>x = 1</pre>', 'code', 'x = 1')
        reply = 'x = 1\n```\n\nThis block has no comments, so it stays unchanged.'
        outcome = translate_segment(_engine(FakeBackend([reply, reply])), seg, [seg])
        assert outcome.issues[0].code == 'code_changed'


class TestPhase4SurvivorsRun1:
    """Mutation testing, Phase 4 run 1."""

    def test_reply_well_formed_before_restore(self):
        # restored, this reply would be well-formed: the wrapper's end tag
        # takes the place of </run>
        seg = _html_seg('<p>A.</p>\nB', text='A.\nB')
        bad = '<p k="1">甲。</p>\n<run k="2">乙</p>'
        outcome = translate_segment(_engine(FakeBackend([bad, bad]), max_calls=2), seg, [seg])
        assert [(i.kind, i.code, i.blocking) for i in outcome.issues] == \
            [('structure', 'not_well_formed', True)]
        assert outcome.issues[0].detail.startswith('不是格式正確的 XML：')

    def test_prefixed_elements_in_a_reply_parse(self):
        seg = _html_seg('<p>A <m:math>x</m:math></p>', text='A x')
        outcome = translate_segment(_engine(FakeBackend(['<p k="1">甲<m:math>x</m:math></p>'])),
                                    seg, [seg])
        assert outcome.text == '<p>甲<m:math>x</m:math></p>'

    def test_html_reply_keeps_markdown_characters(self):
        seg = _html_seg('<p>A.</p>\n<p>B.</p>', text='A.\nB.')
        reply = '<p k="1">3*5 是**甲**</p>\n<p k="2">_乙_</p>'
        outcome = translate_segment(_engine(FakeBackend([reply])), seg, [seg])
        assert outcome.text == '<p>3*5 是**甲**</p>\n<p>_乙_</p>'
        # also in the halves of a split
        bad = '<p k="1">甲</p>'
        fake = FakeBackend([bad, bad, '<p k="1">**甲**</p>', '<p k="1">*乙*</p>'])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.text == '<p>**甲**</p>\n<p>*乙*</p>'

    def test_halves_are_not_retried(self):
        seg = _html_seg('<p>A.</p>\n<p>B.</p>', text='A.\nB.')
        bad = '<p k="1">甲</p>'
        fake = FakeBackend([bad, bad, '<p k="1">甲</p><p>多</p>', '<p k="1">乙</p>'])
        outcome = translate_segment(_engine(fake, max_calls=5), seg, [seg])
        assert outcome.issues[0].code == 'structure_mismatch'
        assert len(fake.requests) == 3

    def test_feedback_lists_every_problem(self):
        seg = _html_seg('<p>A <b>b</b> <i>c</i></p>', text='A b c')
        bad = '<p k="1">甲</p>'
        fake = FakeBackend([bad, '<p k="1">甲<b>乙</b><i>丙</i></p>'])
        translate_segment(_engine(fake), seg, [seg])
        assert fake.requests[1].messages[2].content == FEEDBACK_PROMPT.format(problems=(
            '- 第 1 個區塊不帶屬性的 <b>：原文 1 個，譯文 0 個；'
            '第 1 個區塊不帶屬性的 <i>：原文 1 個，譯文 0 個'))
        seg = _html_seg('<p>A</p>\n<p>B <b>b</b></p>', text='A\nB b')
        fake = FakeBackend(['<p k="1"> </p>\n<p k="2">乙<s>丙</s></p>',
                            '<p k="1">甲</p>\n<p k="2">乙<b>丙</b></p>'])
        translate_segment(_engine(fake), seg, [seg])
        assert fake.requests[1].messages[2].content == FEEDBACK_PROMPT.format(problems=(
            '- 第 1 個區塊沒有文字\n- 出現原文沒有的標籤：<s>\n'
            '- 第 2 個區塊不帶屬性的 <b>：原文 1 個，譯文 0 個'))

    def test_codes_joined_in_log_and_diagnostics(self, caplog):
        import logging
        caplog.set_level(logging.WARNING, logger='src.core.translator')
        seg = _html_seg('<p>A <b>b</b></p>', text='A b')
        bad = '<p k="1">甲<s>乙</s></p>'
        outcome = translate_segment(_engine(FakeBackend([bad, bad])), seg, [seg])
        assert caplog.records[0].getMessage() == \
            'Translation reply rejected (attempt 1/4): unknown_tag, tag_count'
        assert outcome.failure.attempts[0] == (
            'unknown_tag: 出現原文沒有的標籤：<s>; '
            'tag_count: 第 1 個區塊不帶屬性的 <b>：原文 1 個，譯文 0 個', bad)

    def test_last_transient_error_named(self):
        errors = [TransientError(f'error {i}') for i in (1, 2, 3, 4)]
        with pytest.raises(TranslationError) as info:
            _engine(FakeBackend(errors)).translate('x')
        assert str(info.value) == 'Translation failed after 4 attempts: error 4'

    def test_diagnostics_request_for_an_unexpected_error_is_protected(self):
        seg = _html_seg('<p class="a">A.</p>', text='A.')
        outcome = translate_segment(_engine(FakeBackend([KeyError('k')])), seg, [seg])
        assert outcome.failure.prompt.endswith('\n\n<p k="1">A.</p>')
        code = _html_seg('<pre>x = 1</pre>', 'code', 'x = 1')
        outcome = translate_segment(_engine(FakeBackend([KeyError('k')])), code, [code])
        assert outcome.failure.prompt == CODE_PROMPT.format(text='x = 1')


# --- Phase 4 review round 1 ----------------------------------------------------------

class TestRound1Translator:

    def test_prompts(self):
        assert '可合併、拆分或調整語序' in TRANSLATION_PROMPT
        assert '必要時可合併、拆分或調整語序' not in TEXT_HTML_PROMPT
        assert '可以在同一個區塊內合併、拆分句子或調整語序，但不可合併或拆分區塊' in TEXT_HTML_PROMPT
        assert '同一個區塊內每種標籤的數量必須與原文相同' in TEXT_HTML_PROMPT
        assert 'alt、title、aria-label 屬性是給讀者看的文字，也要翻譯' in TEXT_HTML_PROMPT
        assert '{' not in TEXT_HTML_PROMPT.replace('{glossary_section}', '').replace(
            '{context_section}', '').replace('{html}', '')
        assert ('註解行是去掉行首空白後以 // 開頭，或以 # 開頭且 # 後面是空白、! 或行尾的行'
                in CODE_PROMPT)

    def test_swapped_blocks_rejected_before_restore(self):
        source = '<p class="a">First.</p>\n<p class="a">Second.</p>'
        seg = _html_seg(source, text='First.\nSecond.')
        swapped = '<p k="2">第二。</p>\n<p k="1">第一。</p>'
        fake = FakeBackend([swapped, '<p k="1">第一。</p>\n<p k="2">第二。</p>'])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.text == '<p class="a">第一。</p>\n<p class="a">第二。</p>'
        assert fake.requests[1].messages[-1].content.startswith(
            '你上一次的輸出沒有通過檢查：\n- 最外層的標記與原文不同：原文為 k="1"、k="2"，譯文為 k="2"、k="1"')

    def test_alt_sent_and_translated(self):
        source = '<p>See <img src="m.png" alt="A map"/> here.</p>'
        seg = _html_seg(source, text='See here.')
        fake = FakeBackend(['<p k="1">見<img k="2" alt="地圖 &amp; 圖例"/>此處。</p>'])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert fake.prompts[0].endswith('\n\n<p k="1">See <img k="2" alt="A map"/> here.</p>')
        assert 'src=' not in fake.prompts[0]
        assert outcome.text == '<p>見<img src="m.png" alt="地圖 &amp; 圖例"/>此處。</p>'

    def test_ambiguous_source_fails_without_a_call(self):
        seg = _html_seg('<p>A</p>', text='A')
        seg.fingerprints = ['0' * 16]
        fake = FakeBackend(['<p k="1">甲</p>'])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert fake.requests == []
        assert outcome.status == SegmentStatus.FAILED
        assert [(i.code, i.detail) for i in outcome.issues] == \
            [('ambiguous_source', '原文無法切成各個區塊')]
        assert str(outcome.failure) == 'Translation failed after 0 attempts: 原文無法切成各個區塊'
        assert outcome.failure.prompt.endswith('\n\n<p k="1">A</p>')
        assert outcome.failure.attempts == []
        assert outcome.failure.code == 'ambiguous_source'

    def test_failed_outcome_carries_usage(self):
        fake = FakeBackend([LLMResponse(DROPPED_B, 'complete', Usage(10, 3), 'haiku')] * 2)
        seg = _html_seg()
        outcome = translate_segment(_engine(fake, max_calls=2), seg, [seg])
        assert outcome.status == SegmentStatus.FAILED
        assert outcome.usage == Usage(20, 6) and outcome.failure.usage == Usage(20, 6)

    def test_second_half_issues_numbered_in_the_segment(self):
        source = '<p>One <b>b</b>.</p>\n<p>Two <b>b</b>.</p>\n<p>Three <b>b</b>.</p>'
        seg = _html_seg(source, text='One b.\nTwo b.\nThree b.')
        bad = '<p k="1">一<b>乙</b>。</p>\n<p k="2">二乙。</p>\n<p k="3">三<b>乙</b>。</p>'
        fake = FakeBackend([bad, bad, '<p k="1">一<b>乙</b>。</p>',
                            '<p k="1">二乙。</p>\n<p k="2">三<b>乙</b>。</p>'])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.issues[0].detail == '第 2 個區塊不帶屬性的 <b>：原文 1 個，譯文 0 個'
        assert [a[0] for a in outcome.failure.attempts][-1] == \
            'tag_count: 第 2 個區塊不帶屬性的 <b>：原文 1 個，譯文 0 個'
        bad3 = bad.replace('二乙', '二<b>乙</b>').replace('三<b>乙</b>', '三乙')
        fake = FakeBackend([bad3, bad3, '<p k="1">一<b>乙</b>。</p>',
                            '<p k="1">二<b>乙</b>。</p>\n<p k="2">三乙。</p>'])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.issues[0].detail == '第 3 個區塊不帶屬性的 <b>：原文 1 個，譯文 0 個'
        bad1 = bad.replace('一<b>乙</b>', '一乙').replace('二乙', '二<b>乙</b>')
        fake = FakeBackend([bad1, bad1, '<p k="1">一乙。</p>', ''])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.issues[0].detail == '第 1 個區塊不帶屬性的 <b>：原文 1 個，譯文 0 個'
        assert len(fake.requests) == 3


class TestRound2Split:

    def test_second_half_numbered_after_a_cut_run(self):
        # blocks: A, L1, L2, B (the run is two blocks separated by <br> in
        # the chapter); units: p, run, p -> the second half is run + p
        source = '<p>A <b>x</b></p>\nL1\nL2\n<p>B <b>y</b></p>'
        seg = _html_seg(source, text='A x\nL1\nL2\nB y')
        seg.fingerprints = [fingerprint(t) for t in ('A x', 'L1', 'L2', 'B y')]
        bad = '<p k="1">甲<b>乙</b></p>\n<run k="2">一\n二</run>\n<p k="3">丙乙</p>'
        fake = FakeBackend([bad, bad, '<p k="1">甲<b>乙</b></p>',
                            '<run k="1">一\n二</run>\n<p k="2">丙乙</p>'])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.status == SegmentStatus.FAILED
        assert outcome.issues[0].detail == '第 4 個區塊不帶屬性的 <b>：原文 1 個，譯文 0 個'

    def test_first_half_checked_with_its_fingerprints(self):
        source = '<p>A</p>\nL1\nL2\n<p>B</p>\n<p>C</p>'
        seg = _html_seg(source, text='A\nL1\nL2\nB\nC')
        seg.fingerprints = [fingerprint(t) for t in ('A', 'L1', 'L2', 'B', 'C')]
        bad = '<p k="1">甲</p>\n<run k="2">一二</run>\n<p k="3">乙</p>\n<p k="4">丙</p>'
        # the first half's run joined into one line: refused like the whole
        fake = FakeBackend([bad, bad, '<p k="1">甲</p>\n<run k="2">一二</run>', ''])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.status == SegmentStatus.FAILED
        assert outcome.issues[0].code == 'structure_mismatch'
        assert len(fake.requests) == 3
        fake = FakeBackend([bad, bad, '<p k="1">甲</p>\n<run k="2">一\n二</run>',
                            '<p k="1">乙</p>\n<p k="2">丙</p>'])
        outcome = translate_segment(_engine(fake), seg, [seg])
        assert outcome.text == '<p>甲</p>\n一\n二\n<p>乙</p>\n<p>丙</p>'


class TestRound2JoinedFailure:
    """Both halves pass but the joined text fails: recorded as an attempt."""

    def test_joined_failure_recorded(self, monkeypatch, tmp_path, caplog):
        import logging
        from src.core import validation as v
        real = v.check_html
        joined = RESTORED

        def check(source, translation, *args, **kwargs):
            if translation == joined:  # only the whole, joined text fails
                return v.Result([v._issue(v.STRUCTURE, v.TAG_COUNT, 'joined fails')])
            return real(source, translation, *args, **kwargs)
        monkeypatch.setattr(v, 'check_html', check)
        halves = ['<p k="1">見 <a k="2"><sup>3</sup></a> 的<em k="3">核心概念</em>。</p>',
                  '<p k="1">第二個<b>粗體</b>。</p>']
        fake = FakeBackend([DROPPED_B, DROPPED_B] + halves)
        seg = _html_seg()
        caplog.set_level(logging.WARNING, logger='src.core.translator')
        outcome = _translated(_engine(fake), seg, [seg], str(tmp_path))
        assert outcome.status == SegmentStatus.FAILED and len(fake.requests) == 4
        assert str(outcome.failure) == 'Translation failed after 4 attempts: joined fails'
        assert [a[0] for a in outcome.failure.attempts][-1] == 'tag_count: joined fails'
        assert outcome.failure.attempts[-1][1] == joined
        assert 'Translation reply rejected (attempt 4/4): tag_count' in caplog.messages
        log = open(failure_log_path(str(tmp_path), seg.segment_id), encoding='utf-8').read()
        assert 'joined fails' in log


class TestRound3Translator:

    def test_not_well_formed_reply_detail(self):
        seg = _html_seg('<p>A</p>', text='A')
        fake = FakeBackend(['<p k="1">甲', '<p k="1">甲'])
        outcome = translate_segment(_engine(fake, max_calls=2), seg, [seg])
        assert [(i.code, i.detail) for i in outcome.issues] == [
            ('not_well_formed', '不是格式正確的 XML：Opening and ending tag mismatch: p and fragment')]


# --- Phase 5: glossary warnings of a translated segment ---------------------------

class TestTranslateSegmentGlossary:

    def _gm(self):
        from tests.test_validation import glossary
        return glossary(('drip line', '滴水線', 'term'))

    def test_term_not_applied_is_a_warning(self):
        seg = _seg(text='The drip line moved.')
        outcome = translate_segment(_engine(FakeBackend(['<p k="1">滴線移動了。</p>'])),
                                    seg, [seg], self._gm())
        assert outcome.status == SegmentStatus.TRANSLATED
        assert outcome.text == '<p>滴線移動了。</p>'
        assert [(i.kind, i.code, i.blocking) for i in outcome.issues] == \
            [('glossary', 'term_not_applied', False)]

    def test_after_the_quality_warnings(self):
        seg = _seg(text='The drip line moved.')
        outcome = translate_segment(_engine(FakeBackend(['<p k="1">The drip line moved.</p>'])),
                                    seg, [seg], self._gm())
        assert [i.code for i in outcome.issues] == ['untranslated', 'term_not_applied']

    def test_applied_term_gives_none(self):
        seg = _seg(text='The drip line moved.')
        outcome = translate_segment(_engine(FakeBackend(['<p k="1">滴水線移動了。</p>'])),
                                    seg, [seg], self._gm())
        assert outcome.issues == []

    def test_code_segment_not_checked(self):
        seg = _seg(text='# drip line\nx = 1', seg_type='code')
        outcome = translate_segment(_engine(FakeBackend(['# 滴線\nx = 1'])), seg, [seg],
                                    self._gm())
        assert outcome.status == SegmentStatus.TRANSLATED and outcome.issues == []


class TestTranslateSegmentGlossaryRound1:
    """Item 13: a failing glossary check drops the warnings, never the reply."""

    def test_check_failure_keeps_the_translation(self, caplog):
        from tests.test_validation import glossary

        class Broken:
            def __init__(self):
                self.real = glossary(('drip line', '滴水線'))

            @property
            def entries(self):
                if getattr(self, 'prompt_built', False):
                    raise RuntimeError('damaged')
                return self.real.entries

            def terms_for_prompt(self, text):
                self.prompt_built = True
                return self.real.terms_for_prompt(text)

            def format_for_prompt(self, found):
                return self.real.format_for_prompt(found)

        seg = _seg(text='The drip line moved.')
        outcome = translate_segment(_engine(FakeBackend(['<p k="1">滴線移動了。</p>'])),
                                    seg, [seg], Broken())
        assert outcome.status == SegmentStatus.TRANSLATED
        assert (outcome.text, outcome.issues) == ('<p>滴線移動了。</p>', [])
        assert any('術語檢查失敗' in r.getMessage() for r in caplog.records)

    def test_bad_entries_do_not_fail(self):
        from tests.test_validation import glossary
        gm = glossary(('drip line', '滴水線', 'term'))
        gm.entries['x'] = GlossaryEntry('moved', None, 'term')
        seg = _seg(text='The drip line moved.')
        outcome = translate_segment(_engine(FakeBackend(['<p k="1">滴線移動了。</p>'])),
                                    seg, [seg], gm)
        assert outcome.status == SegmentStatus.TRANSLATED
        assert [i.detail for i in outcome.issues] == ['術語「drip line」未譯為「滴水線」']

    def test_check_failure_logged(self, caplog):
        class Broken:
            entries = property(lambda self: (_ for _ in ()).throw(RuntimeError('x')))
        from src.core.translator import _glossary_warnings
        seg = _seg(seg_id='c1_seg0042', text='Words.')
        assert _glossary_warnings(seg, '<p>字</p>', Broken()) == []
        record = caplog.records[-1]
        assert record.getMessage() == '術語檢查失敗，略過術語警告: c1_seg0042'
        assert record.exc_info is not None and record.exc_info[0] is RuntimeError
