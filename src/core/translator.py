"""Translation engine - calls Claude CLI to translate text segments."""

import re
import subprocess
import logging
import tempfile

from src.core.segmenter import SegmentType

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = [
    {'alias': 'sonnet', 'label': 'Sonnet', 'description': '快速且高品質，適合大多數翻譯'},
    {'alias': 'opus', 'label': 'Opus', 'description': '最高品質，費用較高'},
    {'alias': 'haiku', 'label': 'Haiku', 'description': '最快速度，適合簡單內容'},
]

DEFAULT_MODEL = 'sonnet'

_SHARED_RULES = """1. 使用台灣慣用的繁體中文用語
2. 專業術語請保留原文並用括號標註，例如：機器學習（machine learning）
3. LaTeX 公式（$...$、$$...$$）保持不變，不要翻譯
4. 英文中的引號強調（"quoted term"）翻譯時使用「」括起來，並注意其他中英標點符號的使用方法，像是逗點的轉換
5. 人名保持英文原文，嚴格禁止翻譯，例如：Nassim Taleb、Charity Majors, Miss Hyde, Lord Voldemort, Miss Pendleton 等。應該被翻譯成 Hyde 小姐、Voldemort 男爵、Pendleton 小姐等。
6. 書名、文章名、論文名、組織名、電影名等：如果使用翻譯，請使用廣泛使用的翻譯版本，並務必加括號標注原文
7. 地名、物種名稱等專有名詞：如有明確翻譯就用翻譯並加括號標註原文；如無確定翻譯則保留原文
8. 翻譯名稱時不要出現什麼乖乖等錯誤翻譯的字眼

中英文句型差異——翻譯時請特別注意：
- 不要逐句對譯，先理解整段語意，再用自然的中文重新表達；必要時可合併、拆分或調整語序
- 英文被動句（was done by...）應盡量轉為中文主動句，除非「被」在中文語境中自然（如被騙、被開除）
- 英文每句都需要主詞，中文在主詞不變時應省略，避免反覆出現「他」「它」「這」
- 英文的名詞化表達（the implementation of...）應轉為中文動詞句（實作……）
- 英文的長關係子句（the method that was proposed by X and adopted by Y...）不要硬塞在名詞前面，應拆成多個短句
- 英文靠 however/therefore/although 等連接詞串聯，中文語意銜接時可省略，不要每個都硬譯
- 英文的 this/that/it 等回指代詞，中文應視情況重複關鍵詞或直接省略，避免指代不明
- 英文介系詞片語可以層層堆疊（the increase in the rate of errors in...），中文必須拆開重組"""

_SHARED_PROHIBITIONS = """嚴格禁止：
- 不要加任何說明、前綴或後綴（例如「以下是翻譯」「翻譯如下」等）
- 不要使用 markdown 語法"""

TRANSLATION_PROMPT = f"""你是一位專業的英文翻譯，請將以下英文內容翻譯成繁體中文。

翻譯要求：
{_SHARED_RULES}
9. 保持原文的段落結構和格式
10. 英文中的斜體或粗體強調，翻譯時用「」標示，不要使用 markdown 的 * 或 ** 語法

{{glossary_section}}
{{context_section}}
{_SHARED_PROHIBITIONS}
- 輸出純文字，不要加任何格式標記
- 直接輸出翻譯結果，第一個字就是翻譯內容

原文：
{{text}}"""

CODE_PROMPT = """以下是程式碼區塊，請只翻譯其中的註解（# 或 // 開頭的行），保持程式碼本身完全不變。直接輸出結果，不加任何說明。

{text}"""

TEXT_HTML_PROMPT = f"""你是一位專業的英文翻譯，請將以下 HTML 內容中的英文文字翻譯成繁體中文。

翻譯要求：
1. 保持所有 HTML 標籤結構完全不變
2. 保持所有 HTML 屬性不變
3. 只翻譯標籤內的文字內容
{_SHARED_RULES}
{{glossary_section}}
{{context_section}}
{_SHARED_PROHIBITIONS}
- 不要改變 HTML 結構
- 直接輸出翻譯後的 HTML，第一個字元就是 <

{{html}}"""

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

    def __init__(self, max_retries: int = 3, model: str = DEFAULT_MODEL):
        self.max_retries = max_retries
        self.model = model

    def translate(self, text: str, segment_type: SegmentType = SegmentType.TEXT,
                  glossary_text: str = '',
                  context_before: str = '', context_after: str = '',
                  html: str = '') -> str:
        """Translate a text segment using Claude CLI.

        Args:
            text: The English text to translate.
            segment_type: Type of segment (SegmentType.TEXT or SegmentType.CODE).
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
        use_html_mode = bool(html) and segment_type != SegmentType.CODE
        prompt = self._build_prompt(text, segment_type, glossary_text,
                                    context_before, context_after, html=html)

        for attempt in range(1, self.max_retries + 1):
            try:
                result = subprocess.run(
                    ['claude', '-p', prompt, '--model', self.model],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=tempfile.gettempdir(),
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

    def _build_prompt(self, text: str, segment_type: SegmentType,
                      glossary_text: str = '',
                      context_before: str = '', context_after: str = '',
                      html: str = '') -> str:
        """Build the appropriate prompt based on segment type."""
        glossary_section = ''
        if glossary_text:
            glossary_section = f'''
術語表（請遵循以下翻譯，注意類別標記）：
{glossary_text}

術語類別說明：
- [人名]：人物名稱，依照術語表翻譯
- [地名]：地理名稱，依照術語表翻譯
- [作品名]：書名、論文、電影等，僅在作為作品名稱出現時使用術語表翻譯，加書名號《》
- [組織]：機構組織名稱，依照術語表翻譯
- [術語]：專業術語，依照術語表翻譯
- [慣用語]：固定搭配表達，依照術語表翻譯
'''

        context_section = ''
        if context_before or context_after:
            parts = []
            if context_before:
                parts.append(f'前文參考（不需翻譯）：\n{context_before[:200]}')
            if context_after:
                parts.append(f'後文參考（不需翻譯）：\n{context_after[:200]}')
            context_section = '\n'.join(parts) + '\n'

        if segment_type == SegmentType.CODE:
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
