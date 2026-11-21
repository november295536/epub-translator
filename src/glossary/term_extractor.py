"""Term extractor - uses Claude CLI to extract technical terms from text."""

from __future__ import annotations

import json
import logging
import threading
from collections import Counter

import src.llm as llm
from src.llm import (
    AuthError, BackendUnavailableError, LLMError, LLMRequest, Message, RateLimitError,
)

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Raised when term extraction stops on an error that every further
    batch would hit too (rate limit, expired login, backend unavailable).
    `terms` holds what the batches finished before it found."""

    def __init__(self, message: str, terms: list[dict] | None = None):
        super().__init__(message)
        self.terms = terms if terms is not None else []


EXTRACT_PROMPT = """你是一位專業的術語提取專家。請從以下英文文本中提取「需要統一翻譯」的核心術語。

提取標準（嚴格遵守）：
- 只提取需要全書統一翻譯、且一般譯者可能拿捏不定的詞
- 包含人名、地名、作品名、組織名、專業術語、慣用語等
- 不要提取常見詞彙或一般英文片語（如 at scale、case study、stakeholders 等）
- 不要提取同一術語的不同形態（如 trace/traces/tracing 只取一個原形）
- 不要提取上位詞與下位詞重複
- 目標數量：寧精勿濫，通常一本書 20-40 個核心術語就夠了

輸出要求：
1. 輸出 JSON 陣列格式
2. 每個術語包含 term（英文術語，用原形單數）、translation（繁體中文翻譯，使用台灣用語）、category（類別）
3. 類別可選：person, place, work, organization, term, expression, other
4. 直接輸出 JSON，不要加任何說明或 markdown 格式

範例輸出：
[{{"term": "The Quiet Ledger", "translation": "靜默帳本", "category": "work"}}, {{"term": "heuristics", "translation": "捷思法", "category": "term"}}]

文本：
{text}"""

MAX_CHARS_PER_BATCH = 5000


CONSOLIDATE_PROMPT = """你是一位專業的術語編輯。以下術語是從同一本書的不同段落分別提取的，每個術語都得到了不只一種翻譯建議（數字是該翻譯被提出的次數）。請為每個術語決定全書統一使用的一個翻譯（繁體中文，台灣用語）和一個類別。

輸出要求：
1. 輸出 JSON 陣列，每個術語一個物件，包含 term（照抄下列的 term）、translation、category
2. 類別可選：person, place, work, organization, term, expression, other
3. 直接輸出 JSON，不要加任何說明或 markdown 格式

術語與翻譯建議（每行一個 JSON 物件）：
{candidates}"""

# Upper bound of the candidate lines sent in one consolidating call
MAX_CHARS_PER_CONSOLIDATION = 8000


def _batches(segments: list[dict]) -> list[str]:
    """The texts of the extraction calls: the segments' texts, split into
    chunks of at most MAX_CHARS_PER_BATCH (_split_text) and packed into
    batches of at most that size, joined by a blank line."""
    batches = []
    current_batch: list[str] = []
    current_len = 0
    texts = (chunk for seg in segments
             for chunk in _split_text(seg.get('text', ''), MAX_CHARS_PER_BATCH))
    for text in texts:
        if current_len + len(text) > MAX_CHARS_PER_BATCH and current_batch:
            batches.append('\n\n'.join(current_batch))
            current_batch = []
            current_len = 0
        current_batch.append(text)
        current_len += len(text) + 2  # the '\n\n' separator counts too
    if current_batch:
        batches.append('\n\n'.join(current_batch))
    return batches


def _most_common(counter: Counter) -> str:
    """The most frequent key; the first counted on a tie."""
    return max(counter, key=counter.get)  # max keeps the first of equal ones


def _candidate_line(group: dict) -> str:
    return json.dumps({'term': group['term'], 'category': _most_common(group['categories']),
                       'translations': dict(group['translations'])}, ensure_ascii=False)


def _consolidation_batches(groups: list[dict]) -> list[list[dict]]:
    """`groups` in order, cut so that each part's candidate lines stay
    within MAX_CHARS_PER_CONSOLIDATION (a longer single line is a part)."""
    parts: list[list[dict]] = []
    for group in groups:
        length = len(_candidate_line(group)) + 1
        if parts and size + length <= MAX_CHARS_PER_CONSOLIDATION:
            parts[-1].append(group)
            size += length
        else:
            parts.append([group])
            size = length
    return parts


def _final(groups: dict, decided: dict) -> list[dict]:
    """One entry per group: the decided one, else the most frequent
    proposal; sorted by term."""
    terms = [decided.get(key) or {'term': g['term'],
                                  'translation': _most_common(g['translations']),
                                  'category': _most_common(g['categories'])}
             for key, g in groups.items()]
    return sorted(terms, key=lambda x: x['term'].lower())


def _split_text(text: str, limit: int) -> list[str]:
    """Split text into chunks of at most `limit` characters, without loss.

    ''.join(result) == text. Each chunk ends after the last whitespace
    before the limit when there is one, so words are not cut; a run of
    more than `limit` characters without whitespace is cut at the limit.
    """
    chunks = []
    start = 0
    while len(text) - start > limit:
        window = text[start:start + limit]
        cut = max(window.rfind(' '), window.rfind('\n'), window.rfind('\t')) + 1
        if cut <= 0:
            cut = limit
        chunks.append(text[start:start + cut])
        start += cut
    if start < len(text):
        chunks.append(text[start:])
    return chunks


class TermExtractor:
    """Extracts technical terms from text through an LLM backend."""

    def __init__(self, model: str = '', timeout: int = 180,
                 backend: llm.LLMBackend | None = None):
        self.model = model
        self.timeout = timeout
        self.backend = backend if backend is not None else llm.default_backend()

    def _call(self, prompt: str) -> str | None:
        """The reply to one prompt; None when the call failed (logged).
        Raises ExtractionError on an error every further call would hit too."""
        request = LLMRequest(system='', messages=[Message('user', prompt)],
                             model=self.model, timeout=self.timeout)
        try:
            return self.backend.complete(request, threading.Event()).text
        except (AuthError, RateLimitError, BackendUnavailableError) as e:
            # Every further call would fail the same way
            raise ExtractionError(str(e))
        except LLMError as e:
            logger.warning("Term extraction failed: %s", e)
            return None

    def extract_from_text(self, text: str) -> list[dict]:
        """Extract terms from a single text chunk.

        Returns list of dicts with 'term', 'translation', 'category' keys.
        """
        reply = self._call(EXTRACT_PROMPT.format(text=text[:MAX_CHARS_PER_BATCH]))
        if reply is None:
            return []
        try:
            return self._parse_response(reply.strip())
        except Exception as e:
            logger.warning("Term extraction error: %s", e)
            return []

    def extract_from_segments(self, segments: list[dict]) -> list[dict]:
        """Extract terms from multiple segments in batches, then consolidate.

        Every batch proposes terms; the proposals are grouped by term
        (case-insensitive). A term proposed with more than one translation
        is decided by one consolidating call (more than one only when the
        conflicts exceed MAX_CHARS_PER_CONSOLIDATION). A term the
        consolidation does not decide (no reply, an unusable reply, left
        out) keeps its most frequent proposal, the first proposed on a tie.

        Args:
            segments: List of dicts with 'text' key.

        Returns:
            One entry per term ('term', 'translation', 'category'), sorted.

        Raises:
            ExtractionError: a rate limit, an expired login or an unusable
                backend stopped the extraction; its `terms` are those of the
                batches finished before, each with its most frequent proposal.
        """
        groups = self.collect_candidates(segments)
        decided = {}
        try:
            for batch in _consolidation_batches(
                    [g for g in groups.values() if len(g['translations']) > 1]):
                decided.update(self.consolidate(batch))
        except ExtractionError as e:
            raise ExtractionError(str(e), terms=_final(groups, decided)) from e
        return _final(groups, decided)

    def collect_candidates(self, segments: list[dict]) -> dict:
        """The proposals of every batch, by lowercased term: {'term': the
        first spelling, 'translations': Counter, 'categories': Counter},
        counted once per batch that proposed them. Raises ExtractionError
        (with the terms of the batches before) like extract_from_segments."""
        groups: dict = {}
        for batch_text in _batches(segments):
            try:
                terms = self.extract_from_text(batch_text)
            except ExtractionError as e:
                # Stop, but keep the terms of the batches already done
                raise ExtractionError(str(e), terms=_final(groups, {})) from e
            for t in terms:
                term, translation = t['term'], t['translation']
                if not isinstance(term, str) or not isinstance(translation, str) \
                        or not term.strip() or not translation.strip():
                    continue
                category = t.get('category')
                group = groups.setdefault(term.lower(), {
                    'term': term, 'translations': Counter(), 'categories': Counter()})
                group['translations'][translation] += 1
                group['categories'][category if isinstance(category, str) else ''] += 1
        return groups

    def consolidate(self, groups: list[dict]) -> dict:
        """One call that decides a translation and a category for each of
        `groups` (see collect_candidates). Returns {lowercased term: entry}
        for the terms the reply decided with a non-empty translation; {}
        when the call or its reply is unusable. Raises ExtractionError on a
        rate limit, an expired login or an unusable backend."""
        lines = '\n'.join(_candidate_line(g) for g in groups)
        reply = self._call(CONSOLIDATE_PROMPT.format(candidates=lines))
        if reply is None:
            return {}
        wanted = {g['term'].lower(): g for g in groups}
        decided = {}
        for item in self._parse_response(reply.strip()):
            term, translation = item['term'], item['translation']
            if not isinstance(term, str) or term.lower() not in wanted \
                    or not isinstance(translation, str) or not translation.strip():
                continue
            group = wanted[term.lower()]
            category = item.get('category')
            if not isinstance(category, str):
                category = _most_common(group['categories'])
            decided[term.lower()] = {'term': group['term'], 'translation': translation,
                                     'category': category}
        if len(decided) < len(wanted):
            logger.warning("Term consolidation decided %d of %d terms",
                           len(decided), len(wanted))
        return decided

    @staticmethod
    def _parse_response(output: str) -> list[dict]:
        """Parse Claude's JSON response, handling potential formatting issues."""
        # Try to find JSON array in the output
        output = output.strip()

        # Remove markdown code fences if present
        if output.startswith('```'):
            lines = output.split('\n')
            lines = [l for l in lines if not l.strip().startswith('```')]
            output = '\n'.join(lines)

        # Try parsing directly
        try:
            data = json.loads(output)
            if isinstance(data, list):
                return [d for d in data
                        if isinstance(d, dict) and 'term' in d and 'translation' in d]
        except json.JSONDecodeError:
            pass

        # Try to find JSON array within the text
        start = output.find('[')
        end = output.rfind(']')
        if start != -1 and end != -1:
            try:
                data = json.loads(output[start:end + 1])
                if isinstance(data, list):
                    return [d for d in data
                            if isinstance(d, dict) and 'term' in d and 'translation' in d]
            except json.JSONDecodeError:
                pass

        logger.warning("Could not parse term extraction response")
        return []
