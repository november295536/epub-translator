"""Translation engine - calls Claude CLI to translate text segments."""

from __future__ import annotations

import hashlib
import os
import re
import logging
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field

import src.llm as llm
from src.core.project import Issue, SegmentStatus
from src.core.segmenter import SegmentType
from src.llm import (  # noqa: F401  (RateLimitError, AuthError re-exported)
    AuthError, BackendUnavailableError, CancelledError, LLMError, LLMRequest,
    Message, RateLimitError, RefusalError, TransientError, Usage,
)

logger = logging.getLogger(__name__)


def available_models() -> list[dict]:
    """The models of the backend in use, as dicts (alias, label, description)."""
    return [asdict(m) for m in llm.default_backend().list_models()]


def default_model() -> str:
    """The model of the backend in use for a project that names none."""
    return llm.default_backend().default_model

_SHARED_RULES = """1. 使用台灣慣用的繁體中文用語
2. 專業術語請保留原文並用括號標註，例如：機器學習（machine learning）
3. LaTeX 公式（$...$、$$...$$）保持不變，不要翻譯
4. 英文中的引號強調（"quoted term"）翻譯時使用「」括起來，並注意其他中英標點符號的使用方法，像是逗點的轉換
5. 人名保持英文原文，嚴格禁止翻譯，例如：Orla Vintermark、Tobin Quayle, Miss Ferrowind, Lord Halvarth, Miss Dunmere 等。應該被翻譯成 Ferrowind 小姐、Halvarth 男爵、Dunmere 小姐等。
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

# Blank lines at the very start of a response (indentation of the first
# non-blank line is not part of the match)
_LEADING_BLANK_LINES = re.compile(r'\A(?:[ \t]*\r?\n)+')

# A response that is entirely wrapped in one code fence (the body may be empty)
_CODE_FENCE_PATTERN = re.compile(r'\A```[^\n]*\n(?:(.*)\n)?```\Z', re.DOTALL)


def _starts_with_commentary(text: str) -> bool:
    """True when the first line of text matches the first two _START_PATTERNS
    (e.g. "看起來…原樣輸出：" or "Here is…")."""
    return any(re.match(p, text + '\n') for p in _START_PATTERNS[:2])


def _is_empty_reply(translated: str, source: str) -> bool:
    """True when a cleaned reply holds no translation: nothing, or only
    backticks and whitespace unless the source holds nothing else. A reply
    that is only a commentary line is not judged here (as on main)."""
    t = translated.strip()
    if re.sub(r'[`\s]', '', t):
        return False
    return not t or bool(re.sub(r'[`\s]', '', source))


# Number of characters of each neighbouring segment used as context
CONTEXT_CHARS = 200


def get_context(segments, segment_id: str) -> tuple:
    """Return (context_before, context_after) source texts for a segment.

    On each side, code segments are stepped over to the nearest text
    segment; the search never crosses into another chapter. A side with no
    such segment is an empty string.

    Args:
        segments: All project segments in reading order (objects with
                  segment_id, chapter_id, segment_type and text).
        segment_id: ID of the segment about to be translated.
    """
    ids = [s.segment_id for s in segments]
    if segment_id not in ids:
        return '', ''
    idx = ids.index(segment_id)
    chapter_id = segments[idx].chapter_id

    def nearest_text(step: int) -> str:
        i = idx + step
        while 0 <= i < len(segments) and segments[i].chapter_id == chapter_id:
            if segments[i].segment_type != SegmentType.CODE:
                return segments[i].text
            i += step
        return ''

    return nearest_text(-1), nearest_text(1)


class TranslationError(Exception):
    """Raised when translation fails after all attempts.

    `code` names the last failure (transient, empty_reply, refusal, error);
    `prompt` is the request sent and `attempts` the raw output of each
    failed attempt, for failure diagnostics. Rate-limit, auth and cancel
    errors are not TranslationErrors: they stop or pause a run instead of
    failing one segment.
    """

    def __init__(self, message: str, *, code: str, prompt: str, attempts: list):
        super().__init__(message)
        self.code = code
        self.prompt = prompt
        self.attempts = attempts


@dataclass
class Reply:
    text: str
    usage: Usage | None


@dataclass
class TranslationOutcome:
    """Result of one translate_segment() call."""
    text: str | None          # accepted translation, or None
    status: SegmentStatus     # TRANSLATED or FAILED
    issues: list[Issue]
    model: str
    usage: Usage | None
    # The error of a failure, for its diagnostics (see record_failure_log)
    failure: TranslationError | None = field(default=None, repr=False, compare=False)


# Characters of raw output kept per failed attempt in a diagnostics file
DIAGNOSTIC_RAW_CHARS = 20000

# Characters kept of an error message, in the diagnostics file and in the
# segment's stored issue
ERROR_MESSAGE_CHARS = 2000


class TranslationEngine:
    """Translates text segments through an LLM backend."""

    def __init__(self, max_retries: int = 3, model: str | None = None,
                 backend: llm.LLMBackend | None = None, timeout: float = 120):
        self.max_retries = max_retries
        self.backend = backend if backend is not None else llm.default_backend()
        self.model = model if model is not None else self.backend.default_model
        self.timeout = timeout

    def translate(self, text: str, segment_type: SegmentType = SegmentType.TEXT,
                  glossary_text: str = '',
                  context_before: str = '', context_after: str = '',
                  html: str = '') -> str:
        """Translate a text segment; see translate_reply()."""
        return self.translate_reply(text, segment_type, glossary_text,
                                    context_before, context_after, html).text

    def translate_reply(self, text: str, segment_type: SegmentType, glossary_text: str,
                        context_before: str, context_after: str, html: str,
                        cancel: threading.Event | None = None) -> Reply:
        """Translate a text segment through the backend.

        Args:
            text: The English text to translate.
            segment_type: Type of segment (SegmentType.TEXT or SegmentType.CODE).
            glossary_text: Formatted glossary entries for the prompt.
            context_before: Previous segment text for context.
            context_after: Next segment text for context.
            html: Original HTML content. When provided for non-code types,
                  the HTML-preserving prompt is used instead of the plain text prompt.

            cancel: Set to abandon the call (raises CancelledError).

        Returns:
            The translated Traditional Chinese text and the usage the
            backend reported (None when it reports none).

        Raises:
            TranslationError: If every attempt failed (timeout, error exit,
                empty reply) or the model refused.
            RateLimitError, AuthError, BackendUnavailableError, CancelledError:
                at once, no retry.
        """
        is_code = segment_type == SegmentType.CODE
        use_html_mode = bool(html) and not is_code
        prompt = self._build_prompt(text, segment_type, glossary_text,
                                    context_before, context_after, html=html)
        request = LLMRequest(system='', messages=[Message('user', prompt)],
                             model=self.model, timeout=self.timeout)
        if cancel is None:
            cancel = threading.Event()

        attempts = []
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.backend.complete(request, cancel)
            except (RateLimitError, AuthError, BackendUnavailableError, CancelledError):
                raise
            except TransientError as e:
                logger.warning("Translation attempt %d/%d failed: %s",
                               attempt, self.max_retries, str(e)[:ERROR_MESSAGE_CHARS])
                attempts.append((str(e), e.raw))
                code = 'transient'
                continue
            except LLMError as e:
                # A refusal or an error that a retry cannot fix
                attempts.append((str(e), e.raw))
                code = 'refusal' if isinstance(e, RefusalError) else 'error'
                break

            # Code keeps its leading whitespace (first-line indentation)
            raw = response.text if is_code else response.text.strip()
            translated = self._clean_response(raw, html_mode=use_html_mode,
                                              code_mode=is_code, source=text)
            if _is_empty_reply(translated, text):
                logger.warning(
                    "Empty translation result (attempt %d/%d)",
                    attempt, self.max_retries,
                )
                attempts.append(('empty reply', response.text))
                code = 'empty_reply'
                continue

            return Reply(translated, response.usage)

        if code == 'empty_reply':
            message = f"Empty translation after {len(attempts)} attempts"
        else:
            message = f"Translation failed after {len(attempts)} attempts: {attempts[-1][0]}"
        raise TranslationError(message, code=code, prompt=prompt, attempts=attempts)

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
                parts.append(f'前文參考（不需翻譯）：\n{context_before[-CONTEXT_CHARS:]}')
            if context_after:
                parts.append(f'後文參考（不需翻譯）：\n{context_after[:CONTEXT_CHARS]}')
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
    def _clean_response(text: str, html_mode: bool = False,
                        code_mode: bool = False, source: str = '') -> str:
        """Remove common Claude response prefixes, formatting artifacts, and markdown syntax.

        Args:
            text: The raw response from Claude.
            html_mode: If True, only strip prefix/suffix commentary but preserve
                       all HTML tags and structure (no markdown stripping).
            code_mode: If True, the response is source code: only a wrapping
                       code fence, surrounding blank lines and trailing
                       whitespace are removed. Indentation and characters
                       that look like markdown (#, *, _) are kept.
            source: The source code (code mode only). When it contains a
                    code fence itself, the response is not unwrapped.

        In code mode, a first line matching the first two start patterns is
        removed unless the source's first non-blank line matches them too.
        Then, when the source has no fence, a response that is one fenced
        block is reduced to the fenced body (an empty fence gives '').
        """
        if code_mode:
            cleaned = _LEADING_BLANK_LINES.sub('', text).rstrip()
            if (_starts_with_commentary(cleaned)
                    and not _starts_with_commentary(_LEADING_BLANK_LINES.sub('', source))):
                cleaned = _LEADING_BLANK_LINES.sub('', cleaned.partition('\n')[2])
            if '```' not in source:
                fenced = _CODE_FENCE_PATTERN.match(cleaned)
                if fenced:
                    body = fenced.group(1) or ''
                    # Only a single block: a fence inside the body means several
                    if '```' not in body:
                        cleaned = _LEADING_BLANK_LINES.sub('', body).rstrip()
            return cleaned

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


def translate_segment(engine: TranslationEngine, seg, segments,
                      glossary=None,
                      cancel: threading.Event | None = None) -> TranslationOutcome:
    """Translate one project segment: the single entry point for the web
    worker, both CLI modes and the re-translate endpoint.

    Builds the request (glossary terms, same-chapter context, HTML for text
    segments), calls the engine and returns the outcome. A failed segment
    is an outcome with status FAILED and a backend issue, not an exception;
    it carries the error for the diagnostics, which the caller writes with
    record_failure_log once it has accepted and saved the outcome. Any
    other exception also gives a FAILED outcome (issue code `unexpected`).
    RateLimitError, AuthError, BackendUnavailableError and CancelledError
    are raised: they concern the run, not the segment. Writes no file.
    """
    model = engine.model
    context = None  # (before, after) once known: the request can then be rebuilt
    try:
        glossary_text = ''
        if glossary is not None and glossary.entries:
            found = glossary.terms_for_prompt(seg.text)
            if found:
                glossary_text = glossary.format_for_prompt(found)

        context = context_before, context_after = get_context(segments, seg.segment_id)
        reply = engine.translate_reply(
            seg.text,
            segment_type=seg.segment_type,
            glossary_text=glossary_text,
            context_before=context_before,
            context_after=context_after,
            html=seg.html,  # ignored for code segments
            cancel=cancel,
        )
    except (RateLimitError, AuthError, BackendUnavailableError, CancelledError):
        raise
    except TranslationError as e:
        return TranslationOutcome(
            text=None, status=SegmentStatus.FAILED, model=model, usage=None,
            issues=[Issue(kind='backend', code=e.code,
                          detail=str(e)[:ERROR_MESSAGE_CHARS], blocking=True)],
            failure=e)
    except Exception as e:  # noqa: BLE001 - the segment fails, the run goes on
        logger.exception("未預期的錯誤: %s", seg.segment_id)
        prompt = _request_text(engine, seg, glossary_text, context) if context else ''
        return unexpected_error_outcome(model, e, prompt)
    return TranslationOutcome(text=reply.text, status=SegmentStatus.TRANSLATED,
                              issues=[], model=model, usage=reply.usage)


def _request_text(engine: TranslationEngine, seg, glossary_text: str, context) -> str:
    """The request of a call that raised, for its diagnostics ('' when it
    cannot be built)."""
    try:
        return engine._build_prompt(seg.text, seg.segment_type, glossary_text,
                                    *context, html=seg.html)
    except Exception:  # noqa: BLE001 - diagnostics only
        return ''


def unexpected_error_outcome(model: str, error: BaseException,
                             prompt: str) -> TranslationOutcome:
    """The outcome a worker reports when translating a segment raised an
    exception nobody expected: the segment fails, the run goes on. Its
    diagnostics hold the request (`prompt`, '' when unknown) and the
    traceback."""
    detail = f'未預期的錯誤：{type(error).__name__}: {error}'[:ERROR_MESSAGE_CHARS]
    trace = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
    return TranslationOutcome(
        text=None, status=SegmentStatus.FAILED, model=model, usage=None,
        issues=[Issue(kind='backend', code='unexpected', detail=detail, blocking=True)],
        failure=TranslationError(detail, code='unexpected', prompt=prompt,
                                 attempts=[(f'{type(error).__name__}: {error}', trace)]))


# A run stops after this many unexpected failures in a row: one defect
# would otherwise spend a call on, and fail, every remaining segment
MAX_CONSECUTIVE_UNEXPECTED = 5


class UnexpectedStreak:
    """Counts `unexpected` failures in a row, in the order a run receives
    its results; any other result (also a skipped one, None) resets it."""

    def __init__(self):
        self.count = 0

    def add(self, outcome: TranslationOutcome | None) -> str | None:
        """Count one result. Returns the run-level error message once
        MAX_CONSECUTIVE_UNEXPECTED are reached, else None."""
        if (outcome is not None and outcome.status == SegmentStatus.FAILED
                and outcome.issues[0].code == 'unexpected'):
            self.count += 1
        else:
            self.count = 0
        if self.count < MAX_CONSECUTIVE_UNEXPECTED:
            return None
        return (f'連續 {self.count} 個段落發生未預期的錯誤，翻譯已停止'
                f'（最後一個：{outcome.issues[0].detail}）')


def failure_log_path(log_dir: str, segment_id: str) -> str:
    """One file per segment, replaced by each failed run of that segment.
    The name is the id with characters other than ASCII letters, digits and
    `_.-` replaced (at most 100 of them, so the name stays within 255
    bytes) and a digest of the exact id, so distinct ids never share a file."""
    safe_id = re.sub(r'[^A-Za-z0-9_.-]', '_', segment_id)[:100]
    digest = hashlib.sha256(segment_id.encode('utf-8')).hexdigest()[:10]
    return os.path.join(log_dir, 'failed', f'{safe_id}-{digest}.txt')


def write_failure_log(log_dir: str, segment_id: str, model: str,
                      error: TranslationError) -> None:
    """Keep the raw request and the raw output of each failed attempt.
    Every field that comes from a reply is bounded."""
    path = failure_log_path(log_dir, segment_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    parts = [
        f'segment: {segment_id}',
        f'model: {model}',
        f'time: {time.strftime("%Y-%m-%d %H:%M:%S")}',
        f'error: {str(error)[:ERROR_MESSAGE_CHARS]}',
        '',
        '=== request ===',
        error.prompt,
    ]
    for i, (message, raw) in enumerate(error.attempts, 1):
        parts += ['', f'=== attempt {i}: {message[:ERROR_MESSAGE_CHARS]} ===',
                  raw[:DIAGNOSTIC_RAW_CHARS]]
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(parts) + '\n')


def record_failure_log(log_dir: str, segment_id: str, outcome: TranslationOutcome) -> None:
    """Where an outcome has been accepted and saved (and only there): a
    failure's diagnostics replace the segment's earlier ones, a success
    removes them. A failure to write is logged, not raised."""
    if outcome.status != SegmentStatus.FAILED:
        remove_failure_log(log_dir, segment_id)
    elif outcome.failure:
        try:
            write_failure_log(log_dir, segment_id, outcome.model, outcome.failure)
        except OSError as e:
            logger.error("無法寫入失敗紀錄 %s: %s", segment_id, e)


def remove_failure_log(log_dir: str, segment_id: str) -> None:
    """Remove a segment's failure diagnostics (it translated, or a person
    edited or approved it). A failure to remove is logged, not raised."""
    try:
        os.remove(failure_log_path(log_dir, segment_id))
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.error("無法移除失敗紀錄 %s: %s", segment_id, e)
