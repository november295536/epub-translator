"""Tests for translation engine module."""

import subprocess
import pytest
from unittest.mock import patch, MagicMock

from src.core.segmenter import SegmentType
from src.core.translator import (
    TranslationEngine, TranslationError, RateLimitError,
    TRANSLATION_PROMPT, CODE_PROMPT,
    TEXT_HTML_PROMPT,
    AVAILABLE_MODELS, DEFAULT_MODEL,
)
from src.core.project import SegmentState


class TestTranslationEngine:

    def test_successful_translation(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '這是翻譯結果。'

        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
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

        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
            engine.translate('test')

        prompt = mock_run.call_args[0][0][2]
        assert '台灣' in prompt
        assert '括號' in prompt
        assert 'LaTeX' in prompt

    def test_retry_on_nonzero_exit(self):
        engine = TranslationEngine(max_retries=3)

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stderr = 'error'

        success_result = MagicMock()
        success_result.returncode = 0
        success_result.stdout = '翻譯結果'

        with patch('src.core.translator.subprocess.run',
                   side_effect=[fail_result, success_result]) as mock_run:
            result = engine.translate('test')

        assert result == '翻譯結果'
        assert mock_run.call_count == 2

    def test_fail_after_max_retries(self):
        engine = TranslationEngine(max_retries=2)

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stderr = 'persistent error'

        with patch('src.core.translator.subprocess.run', return_value=fail_result):
            with pytest.raises(TranslationError, match="failed after 2 attempts"):
                engine.translate('test')

    def test_retry_on_empty_output(self):
        engine = TranslationEngine(max_retries=2)

        empty_result = MagicMock()
        empty_result.returncode = 0
        empty_result.stdout = ''

        success_result = MagicMock()
        success_result.returncode = 0
        success_result.stdout = '翻譯結果'

        with patch('src.core.translator.subprocess.run',
                   side_effect=[empty_result, success_result]):
            result = engine.translate('test')

        assert result == '翻譯結果'

    def test_fail_on_all_empty(self):
        engine = TranslationEngine(max_retries=2)

        empty_result = MagicMock()
        empty_result.returncode = 0
        empty_result.stdout = '   '

        with patch('src.core.translator.subprocess.run', return_value=empty_result):
            with pytest.raises(TranslationError, match="Empty translation"):
                engine.translate('test')

    def test_timeout_handling(self):
        engine = TranslationEngine(max_retries=2)

        with patch('src.core.translator.subprocess.run',
                   side_effect=subprocess.TimeoutExpired(cmd='claude', timeout=120)):
            with pytest.raises(TranslationError, match="timed out"):
                engine.translate('test')

    def test_timeout_then_success(self):
        engine = TranslationEngine(max_retries=3)

        success_result = MagicMock()
        success_result.returncode = 0
        success_result.stdout = '成功'

        with patch('src.core.translator.subprocess.run',
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

        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
            engine.translate('test')

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs['timeout'] == 120
        assert call_kwargs['capture_output'] is True
        assert call_kwargs['text'] is True


class TestSubprocessIsolation:

    def test_subprocess_runs_in_temp_dir(self):
        """subprocess.run should use cwd=tempfile.gettempdir() to isolate from git context."""
        import tempfile

        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯'

        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
            engine.translate('test')

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs['cwd'] == tempfile.gettempdir()


class TestRateLimitDetection:

    def test_rate_limit_in_stdout(self):
        engine = TranslationEngine(max_retries=3)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "You've hit your limit · resets 8pm (Asia/Taipei)"
        mock_result.stderr = ''

        with patch('src.core.translator.subprocess.run', return_value=mock_result):
            with pytest.raises(RateLimitError):
                engine.translate('test')

    def test_rate_limit_in_stderr(self):
        engine = TranslationEngine(max_retries=3)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ''
        mock_result.stderr = "You've hit your limit · resets 8pm (Asia/Taipei)"

        with patch('src.core.translator.subprocess.run', return_value=mock_result):
            with pytest.raises(RateLimitError):
                engine.translate('test')

    def test_rate_limit_no_retry(self):
        """Rate limit should raise immediately without retrying."""
        engine = TranslationEngine(max_retries=3)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "You've hit your limit · resets 8pm (Asia/Taipei)"
        mock_result.stderr = ''

        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
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

        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
            engine.translate('def hello():\n    # This is a comment\n    print("Hello")',
                           segment_type=SegmentType.CODE)

        prompt = mock_run.call_args[0][0][2]
        assert '註解' in prompt

    def test_text_with_html_uses_html_prompt(self):
        """Text segments with HTML should use TEXT_HTML_PROMPT."""
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '<h1>標題</h1>'

        html = '<h1>Introduction</h1>'
        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
            engine.translate('Introduction', segment_type=SegmentType.TEXT, html=html)

        prompt = mock_run.call_args[0][0][2]
        assert 'HTML 標籤結構' in prompt
        assert '<h1>Introduction</h1>' in prompt

    def test_glossary_included_in_prompt(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯'

        glossary = 'machine learning → 機器學習\ndeep learning → 深度學習'
        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
            engine.translate('test', glossary_text=glossary)

        prompt = mock_run.call_args[0][0][2]
        assert '術語表' in prompt
        assert 'machine learning' in prompt

    def test_context_included_in_prompt(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯'

        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
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
        mock_result.stdout = '<p>翻譯結果</p>'

        html = '<p>Source text</p>'
        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
            result = engine.translate('Source text', segment_type=SegmentType.TEXT, html=html)

        prompt = mock_run.call_args[0][0][2]
        assert 'HTML 標籤結構' in prompt
        assert '<p>Source text</p>' in prompt
        # Should preserve HTML in response (no markdown stripping)
        assert result == '<p>翻譯結果</p>'

    def test_text_without_html_uses_plain_prompt(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯結果'

        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
            engine.translate('Source text', segment_type=SegmentType.TEXT)

        prompt = mock_run.call_args[0][0][2]
        assert '原文：' in prompt

    def test_code_ignores_html_param(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'code'

        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
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
        mock_result.stdout = '<p>翻譯</p>'

        glossary = 'machine learning → 機器學習'
        html = '<p>Machine learning is important.</p>'
        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
            engine.translate('Machine learning is important.',
                           segment_type=SegmentType.TEXT, html=html, glossary_text=glossary)

        prompt = mock_run.call_args[0][0][2]
        assert '術語表' in prompt
        assert 'machine learning' in prompt
        assert 'HTML 標籤結構' in prompt


class TestModelSelection:

    def test_default_model_is_sonnet(self):
        assert DEFAULT_MODEL == 'sonnet'
        engine = TranslationEngine()
        assert engine.model == 'sonnet'

    def test_available_models_list(self):
        aliases = [m['alias'] for m in AVAILABLE_MODELS]
        assert 'sonnet' in aliases
        assert 'opus' in aliases
        assert 'haiku' in aliases

    def test_custom_model_passed_to_subprocess(self):
        engine = TranslationEngine(model='opus')
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯'

        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
            engine.translate('test')

        cmd = mock_run.call_args[0][0]
        assert '--model' in cmd
        assert 'opus' in cmd

    def test_default_model_passed_to_subprocess(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯'

        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
            engine.translate('test')

        cmd = mock_run.call_args[0][0]
        assert cmd == ['claude', '-p', mock_run.call_args[0][0][2], '--model', 'sonnet']

    def test_model_changeable_between_calls(self):
        engine = TranslationEngine(model='sonnet')
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '翻譯'

        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
            engine.translate('test')
            assert mock_run.call_args[0][0][4] == 'sonnet'

            engine.model = 'haiku'
            engine.translate('test')
            assert mock_run.call_args[0][0][4] == 'haiku'


# A one-line source of more than 15 words that ends in a colon
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

        with patch('src.core.translator.subprocess.run', return_value=mock_result):
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
        with patch('src.core.translator.subprocess.run', return_value=mock_result):
            return engine.translate(text, **kwargs)

    def test_text_without_html_gets_markdown_stripped(self):
        assert self._translate('**粗體**與*斜體*') == '粗體與斜體'

    def test_text_with_html_keeps_markdown_like_characters(self):
        reply = '<p>*重要*與**強調**</p>'
        assert self._translate(reply, html='<p>x</p>') == reply

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

        with patch('src.core.translator.subprocess.run', return_value=mock_result):
            result = engine.translate(CODE_SAMPLE, segment_type=SegmentType.CODE)

        assert result == CODE_SAMPLE

    @pytest.mark.parametrize('reply', [
        '```python\n\n```\n',
        '```python\n```\n',
        '以下是翻譯結果：\n```python\n  \n```\n',
        '以下是翻譯結果：\n```\n```',
    ])
    def test_code_reply_empty_after_cleanup_is_retried(self, reply):
        engine = TranslationEngine(max_retries=2)
        empty, good = MagicMock(), MagicMock()
        empty.returncode = good.returncode = 0
        empty.stderr = good.stderr = ''
        empty.stdout = reply
        good.stdout = 'x = 1\n'

        with patch('src.core.translator.subprocess.run',
                   side_effect=[empty, good]) as mock_run:
            assert engine.translate('x = 1', segment_type=SegmentType.CODE) == 'x = 1'
        assert mock_run.call_count == 2

    def test_code_reply_of_fence_markers_on_two_lines_is_retried(self):
        # The source holds a fence, so the reply is not unwrapped; backticks
        # and a newline remain
        engine = TranslationEngine(max_retries=2)
        empty, good = MagicMock(), MagicMock()
        empty.returncode = good.returncode = 0
        empty.stderr = good.stderr = ''
        empty.stdout = '```\n```'
        good.stdout = 'print("```")\n'

        with patch('src.core.translator.subprocess.run',
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
        engine = TranslationEngine(max_retries=1)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = source
        mock_result.stderr = ''

        with patch('src.core.translator.subprocess.run', return_value=mock_result):
            assert engine.translate(source, segment_type=segment_type) == source

    def test_text_reply_empty_after_cleanup_raises(self):
        engine = TranslationEngine(max_retries=1)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '** **\n'   # markdown cleanup leaves only a space
        mock_result.stderr = ''

        with patch('src.core.translator.subprocess.run', return_value=mock_result):
            with pytest.raises(TranslationError, match='Empty translation'):
                engine.translate('Some text.')

    @pytest.mark.parametrize('reply', [
        '```html\n```\n',
        '```\n```',
        '以下是翻譯結果：\n',
        'Here is the translation:\n',
        'Here is the translation\n',
    ])
    @pytest.mark.parametrize('html', ['', '<p>Some text.</p>'])
    def test_text_reply_only_fence_or_commentary_is_retried(self, reply, html):
        engine = TranslationEngine(max_retries=2)
        empty, good = MagicMock(), MagicMock()
        empty.returncode = good.returncode = 0
        empty.stderr = good.stderr = ''
        empty.stdout = reply
        good.stdout = '一些文字。\n'

        with patch('src.core.translator.subprocess.run',
                   side_effect=[empty, good]) as mock_run:
            assert engine.translate(LONG_SOURCE, html=html) == '一些文字。'
        assert mock_run.call_count == 2

    @pytest.mark.parametrize('source', [
        LONG_SOURCE,
        'Some text.\nMore text.',
        ' '.join(['word'] * 15) + ' this:',   # 16 words, ends in a colon
    ])
    @pytest.mark.parametrize('reply', ['```html\n```\n', '以下是翻譯結果：\n'])
    def test_text_reply_only_fence_or_commentary_exhausts(self, reply, source):
        engine = TranslationEngine(max_retries=2)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = reply
        mock_result.stderr = ''

        with patch('src.core.translator.subprocess.run',
                   return_value=mock_result) as mock_run:
            with pytest.raises(TranslationError, match='Empty translation'):
                engine.translate(source)
        assert mock_run.call_count == 2

    @pytest.mark.parametrize('source, reply', [
        ('OK', '好的\n'),
        ('Of course', '當然\n'),
        ('Here are the steps:', '以下是步驟：\n'),
        ("Here's an example.", '以下是一個範例：\n'),
        ('Here is the translation', 'Here is the translation\n'),
        (' '.join(['word'] * 14) + ' end.', '以下是翻譯結果：\n'),   # 15 words
        ('以下是：', '以下是：\n'),   # source left untranslated
        ('X', 'X\n'),
    ])
    def test_short_text_reply_accepted(self, source, reply):
        engine = TranslationEngine(max_retries=1)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = reply
        mock_result.stderr = ''

        with patch('src.core.translator.subprocess.run', return_value=mock_result):
            assert engine.translate(source) == reply.strip()

    def test_multi_line_reply_ending_in_colon_accepted(self):
        # Only a single commentary line counts as empty; here the first line
        # is removed by the start pattern and two lines remain
        engine = TranslationEngine(max_retries=1)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '當然。\n以下是項目清單\n例如：\n'
        mock_result.stderr = ''

        with patch('src.core.translator.subprocess.run', return_value=mock_result):
            assert engine.translate('Sure.\nHere is a list\nfor example') == '以下是項目清單\n例如：'

    def test_code_whitespace_only_output_is_empty(self):
        engine = TranslationEngine(max_retries=1)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '  \n\n'
        mock_result.stderr = ''

        with patch('src.core.translator.subprocess.run', return_value=mock_result):
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
