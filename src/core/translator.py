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
7. 英文中的引號強調（"quoted term"）翻譯時使用「」括起來，例如："observability" → 「可觀測性」
8. 英文中的斜體或粗體強調，翻譯時用「」標示，不要使用 markdown 的 * 或 ** 語法
9. 人名保持英文原文，不要翻譯，例如：Nassim Taleb、Charity Majors
10. 書名、地名、物種名稱等專有名詞：如有明確的中文翻譯就使用翻譯並加括號標註原文，例如：伊利亞德（Iliad）、紐約（New York）；如無確定翻譯則保留原文
{glossary_section}
{context_section}
嚴格禁止：
- 不要加任何說明、前綴或後綴（例如「以下是翻譯」「翻譯如下」「看起來」等）
- 不要使用任何 markdown 語法（禁止 #、##、**、*、__、```、---、> 等所有 markdown 標記）
- 輸出純文字，不要加任何格式標記
- 不要加分隔線
- 直接輸出翻譯結果，第一個字就是翻譯內容

原文：
{text}"""

CODE_PROMPT = """以下是程式碼區塊，請只翻譯其中的註解（# 或 // 開頭的行），保持程式碼本身完全不變。直接輸出結果，不加任何說明。

{text}"""

TEXT_HTML_PROMPT = """你是一位專業的英文翻譯，請將以下 HTML 內容中的英文文字翻譯成繁體中文。

翻譯要求：
1. 保持所有 HTML 標籤結構完全不變（<p>, <blockquote>, <ul>, <li>, <em>, <strong> 等）
2. 保持所有 HTML 屬性不變（class, id, href 等）
3. 只翻譯標籤內的文字內容
4. 使用台灣慣用的繁體中文用語
5. 專業術語請保留原文並用括號標註，例如：機器學習（machine learning）
6. LaTeX 公式（$...$、$$...$$）保持不變
7. 英文中的引號強調（"quoted term"）翻譯時使用「」括起來
8. 人名保持英文原文，不要翻譯
9. 書名、地名、物種名稱等專有名詞：如有明確的中文翻譯就使用翻譯並加括號標註原文；如無確定翻譯則保留原文
{glossary_section}
{context_section}
嚴格禁止：
- 不要加任何說明、前綴或後綴（例如「以下是翻譯」「翻譯如下」等）
- 不要使用 markdown 語法
- 不要改變 HTML 結構
- 直接輸出翻譯後的 HTML，第一個字元就是 <

{html}"""

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

# Markdown heading pattern: lines starting with one or more # followed by space
_MD_HEADING_PATTERN = re.compile(r'^(#{1,6})\s+', re.MULTILINE)

# Markdown bold: **text** or __text__
_MD_BOLD_PATTERN = re.compile(r'\*\*(.+?)\*\*|__(.+?)__')

# Markdown italic: *text* or _text_ (but not inside ASCII words like 3*5 or foo_bar)
_MD_ITALIC_PATTERN = re.compile(r'(?<![0-9a-zA-Z])\*(.+?)\*(?![0-9a-zA-Z])|(?<![0-9a-zA-Z])_(.+?)_(?![0-9a-zA-Z])')


class TranslationError(Exception):
    """Raised when translation fails."""
    pass


class RateLimitError(TranslationError):
    """Raised when API usage limit is reached."""
    pass


class AuthError(TranslationError):
    """Raised when Claude CLI authentication has expired."""
    pass


_RATE_LIMIT_PATTERN = re.compile(r"You've hit your limit", re.IGNORECASE)
_AUTH_ERROR_PATTERN = re.compile(
    r'authentication_error|OAuth token has expired|please run /login',
    re.IGNORECASE,
)


class TranslationEngine:
    """Translates text segments using Claude CLI."""

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries

    def translate(self, text: str, segment_type: str = 'text',
                  glossary_text: str = '',
                  context_before: str = '', context_after: str = '',
                  html: str = '') -> str:
        """Translate a text segment using Claude CLI.

        Args:
            text: The English text to translate.
            segment_type: Type of segment ('text' or 'code').
            glossary_text: Formatted glossary entries for the prompt.
            context_before: Previous segment text for context.
            context_after: Next segment text for context.
            html: Original HTML content. When provided for non-code types,
                  the HTML-preserving prompt is used instead of the plain text prompt.

        Returns:
            The translated Traditional Chinese text.

        Raises:
            TranslationError: If translation fails after all retries.
        """
        use_html_mode = bool(html) and segment_type != 'code'
        prompt = self._build_prompt(text, segment_type, glossary_text,
                                    context_before, context_after, html=html)

        for attempt in range(1, self.max_retries + 1):
            try:
                result = subprocess.run(
                    ['claude', '-p', prompt],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )

                # Check for API usage limit in stdout or stderr
                stdout = result.stdout if isinstance(result.stdout, str) else ''
                stderr = result.stderr if isinstance(result.stderr, str) else ''
                combined = stdout + stderr
                if _RATE_LIMIT_PATTERN.search(combined):
                    raise RateLimitError(combined.strip())
                if _AUTH_ERROR_PATTERN.search(combined):
                    raise AuthError('Claude CLI 認證已過期，請在終端執行 claude /login 重新登入')

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

                return self._clean_response(translated, html_mode=use_html_mode)

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
                      context_before: str = '', context_after: str = '',
                      html: str = '') -> str:
        """Build the appropriate prompt based on segment type."""
        glossary_section = ''
        if glossary_text:
            glossary_section = f'\n術語表（請遵循以下翻譯）：\n{glossary_text}\n'

        context_section = ''
        if context_before or context_after:
            parts = []
            if context_before:
                parts.append(f'前文參考（不需翻譯）：\n{context_before[:200]}')
            if context_after:
                parts.append(f'後文參考（不需翻譯）：\n{context_after[:200]}')
            context_section = '\n'.join(parts) + '\n'

        if segment_type == 'code':
            return CODE_PROMPT.format(text=text)
        elif html:
            return TEXT_HTML_PROMPT.format(
                html=html,
                glossary_section=glossary_section,
                context_section=context_section,
            )
        else:
            return TRANSLATION_PROMPT.format(
                text=text,
                glossary_section=glossary_section,
                context_section=context_section,
            )

    @staticmethod
    def _clean_response(text: str, html_mode: bool = False) -> str:
        """Remove common Claude response prefixes, formatting artifacts, and markdown syntax.

        Args:
            text: The raw response from Claude.
            html_mode: If True, only strip prefix/suffix commentary but preserve
                       all HTML tags and structure (no markdown stripping).
        """
        cleaned = text
        for pattern in _START_PATTERNS:
            cleaned = re.sub(pattern, '', cleaned)
        for pattern in _END_PATTERNS:
            cleaned = re.sub(pattern, '', cleaned)

        if not html_mode:
            # Strip markdown heading markers (# Title → Title)
            cleaned = _MD_HEADING_PATTERN.sub('', cleaned)

            # Strip markdown bold markers (**text** → text)
            cleaned = _MD_BOLD_PATTERN.sub(lambda m: m.group(1) or m.group(2), cleaned)

            # Strip markdown italic markers (*text* → text)
            cleaned = _MD_ITALIC_PATTERN.sub(lambda m: m.group(1) or m.group(2), cleaned)

        return cleaned.strip()
