"""Tests for translation engine module."""

import subprocess
import pytest
from unittest.mock import patch, MagicMock

from src.core.translator import (
    TranslationEngine, TranslationError,
    TRANSLATION_PROMPT, CODE_PROMPT,
    TEXT_HTML_PROMPT,
)


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
        assert '程式碼' in prompt

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
        assert TranslationEngine._clean_response('# 可觀測性工程（Observability Engineering）') == '可觀測性工程（Observability Engineering）'
        assert TranslationEngine._clean_response('## 第一章 簡介') == '第一章 簡介'
        assert TranslationEngine._clean_response('### 小節標題') == '小節標題'

    def test_removes_markdown_bold(self):
        assert TranslationEngine._clean_response('可觀測性更是一個**社會技術**概念') == '可觀測性更是一個社會技術概念'
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
                           segment_type='code')

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
            engine.translate('Introduction', segment_type='text', html=html)

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
            result = engine.translate('Source text', segment_type='text', html=html)

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
            engine.translate('Source text', segment_type='text')

        prompt = mock_run.call_args[0][0][2]
        assert '原文：' in prompt

    def test_code_ignores_html_param(self):
        engine = TranslationEngine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'code'

        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
            engine.translate('code', segment_type='code', html='<pre><code>code</code></pre>')

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
                           segment_type='text', html=html, glossary_text=glossary)

        prompt = mock_run.call_args[0][0][2]
        assert '術語表' in prompt
        assert 'machine learning' in prompt
        assert 'HTML 標籤結構' in prompt
