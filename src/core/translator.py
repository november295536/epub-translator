"""Translation engine - calls Claude CLI to translate text segments."""

import re
import subprocess
import logging

logger = logging.getLogger(__name__)

TRANSLATION_PROMPT = """你是一位專業的英文翻譯，請將以下英文內容翻譯成繁體中文。

翻譯要求：
1. 使用台灣慣用的繁體中文用語
2. 專業術語請保留原文並用括號標註，例如：機器學習（machine learning）
3. 保持原文的段落結構和格式
4. 程式碼區塊中只翻譯註解，不翻譯程式碼本身
5. 翻譯要自然流暢，不要逐字翻譯
6. LaTeX 公式（$...$、$$...$$）保持不變，不要翻譯
{glossary_section}
{context_section}
嚴格禁止：
- 不要加任何說明、前綴或後綴（例如「以下是翻譯」「翻譯如下」「看起來」等）
- 不要加 markdown 格式標記（如 ```、---、** 等）
- 不要加分隔線
- 直接輸出翻譯結果，第一個字就是翻譯內容

原文：
{text}"""

HEADING_PROMPT = """將以下英文標題翻譯成繁體中文（台灣用語）。專業術語保留原文用括號標註。只輸出翻譯結果，不加任何格式或說明。
{glossary_section}
{text}"""

CODE_PROMPT = """以下是程式碼區塊，請只翻譯其中的註解（# 或 // 開頭的行），保持程式碼本身完全不變。直接輸出結果，不加任何說明。

{text}"""

TABLE_PROMPT = """將以下 HTML 表格中的英文文字內容翻譯成繁體中文（台灣用語）。保持 HTML 標籤結構完全不變，只翻譯文字部分。專業術語保留原文用括號標註。直接輸出結果。
{glossary_section}
{text}"""

# Patterns to clean from Claude's response
# Patterns applied at the start of the response only (no MULTILINE)
_START_PATTERNS = [
    r'^(?:以下是|看起來|翻譯如下|翻譯結果|好的|當然)[^\n]*\n+',
    r'^(?:Here is|Here\'s|The translation)[^\n]*\n+',
    r'^---+\s*\n',
    r'^```[^\n]*\n',
]

# Patterns applied at the end of the response only (no MULTILINE)
_END_PATTERNS = [
    r'\n---+\s*$',
    r'\n```\s*$',
]


class TranslationError(Exception):
    """Raised when translation fails."""
    pass


class TranslationEngine:
    """Translates text segments using Claude CLI."""

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries

    def translate(self, text: str, segment_type: str = 'text',
                  glossary_text: str = '',
                  context_before: str = '', context_after: str = '') -> str:
        """Translate a text segment using Claude CLI.

        Args:
            text: The English text to translate.
            segment_type: Type of segment ('text', 'heading', 'code', 'table').
            glossary_text: Formatted glossary entries for the prompt.
            context_before: Previous segment text for context.
            context_after: Next segment text for context.

        Returns:
            The translated Traditional Chinese text.

        Raises:
            TranslationError: If translation fails after all retries.
        """
        prompt = self._build_prompt(text, segment_type, glossary_text,
                                    context_before, context_after)

        for attempt in range(1, self.max_retries + 1):
            try:
                result = subprocess.run(
                    ['claude', '-p', prompt],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )

                if result.returncode != 0:
                    logger.warning(
                        "claude CLI returned code %d (attempt %d/%d): %s",
                        result.returncode, attempt, self.max_retries,
                        result.stderr.strip(),
                    )
                    if attempt == self.max_retries:
                        raise TranslationError(
                            f"claude CLI failed after {self.max_retries} attempts: {result.stderr.strip()}"
                        )
                    continue

                translated = result.stdout.strip()
                if not translated:
                    logger.warning(
                        "Empty translation result (attempt %d/%d)",
                        attempt, self.max_retries,
                    )
                    if attempt == self.max_retries:
                        raise TranslationError(
                            f"Empty translation after {self.max_retries} attempts"
                        )
                    continue

                return self._clean_response(translated)

            except subprocess.TimeoutExpired:
                logger.warning(
                    "Translation timed out (attempt %d/%d)",
                    attempt, self.max_retries,
                )
                if attempt == self.max_retries:
                    raise TranslationError(
                        f"Translation timed out after {self.max_retries} attempts"
                    )

        raise TranslationError("Translation failed unexpectedly")

    def _build_prompt(self, text: str, segment_type: str,
                      glossary_text: str = '',
                      context_before: str = '', context_after: str = '') -> str:
        """Build the appropriate prompt based on segment type."""
        glossary_section = ''
        if glossary_text:
            glossary_section = f'\n術語表（請遵循以下翻譯）：\n{glossary_text}\n'

        if segment_type == 'heading':
            return HEADING_PROMPT.format(
                text=text, glossary_section=glossary_section)
        elif segment_type == 'code':
            return CODE_PROMPT.format(text=text)
        elif segment_type == 'table':
            return TABLE_PROMPT.format(
                text=text, glossary_section=glossary_section)
        else:
            context_section = ''
            if context_before or context_after:
                parts = []
                if context_before:
                    parts.append(f'前文參考（不需翻譯）：\n{context_before[:200]}')
                if context_after:
                    parts.append(f'後文參考（不需翻譯）：\n{context_after[:200]}')
                context_section = '\n'.join(parts) + '\n'

            return TRANSLATION_PROMPT.format(
                text=text,
                glossary_section=glossary_section,
                context_section=context_section,
            )

    @staticmethod
    def _clean_response(text: str) -> str:
        """Remove common Claude response prefixes and formatting artifacts."""
        cleaned = text
        for pattern in _START_PATTERNS:
            cleaned = re.sub(pattern, '', cleaned)
        for pattern in _END_PATTERNS:
            cleaned = re.sub(pattern, '', cleaned)
        return cleaned.strip()
