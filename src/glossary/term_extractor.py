"""Term extractor - uses Claude CLI to extract technical terms from text."""

from __future__ import annotations

import json
import re
import subprocess
import logging

logger = logging.getLogger(__name__)

_AUTH_ERROR_PATTERN = re.compile(
    r'authentication_error|OAuth token has expired|please run /login',
    re.IGNORECASE,
)


class ExtractionError(Exception):
    """Raised when term extraction fails due to a non-recoverable error."""
    pass

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
    """Extracts technical terms from text using Claude CLI."""

    def __init__(self, timeout: int = 180):
        self.timeout = timeout

    def extract_from_text(self, text: str) -> list[dict]:
        """Extract terms from a single text chunk.

        Returns list of dicts with 'term', 'translation', 'category' keys.
        """
        prompt = EXTRACT_PROMPT.format(text=text[:MAX_CHARS_PER_BATCH])

        try:
            result = subprocess.run(
                ['claude', '-p', prompt],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            if result.returncode != 0:
                combined = (result.stdout or '') + (result.stderr or '')
                if _AUTH_ERROR_PATTERN.search(combined):
                    raise ExtractionError('Claude CLI 認證已過期，請在終端執行 claude /login 重新登入')
                logger.warning("Term extraction failed: %s", combined.strip())
                return []

            output = result.stdout.strip()
            return self._parse_response(output)

        except subprocess.TimeoutExpired:
            logger.warning("Term extraction timed out")
            return []
        except ExtractionError:
            raise
        except Exception as e:
            logger.warning("Term extraction error: %s", e)
            return []

    def extract_from_segments(self, segments: list[dict]) -> list[dict]:
        """Extract terms from multiple segments in batches.

        Args:
            segments: List of dicts with 'text' key.

        Returns:
            Deduplicated list of term dicts.
        """
        all_terms = {}
        current_batch = []
        current_len = 0

        # A text longer than one batch is split into bounded chunks first, so
        # extract_from_text() never has to truncate it.
        texts = (chunk for seg in segments
                 for chunk in _split_text(seg.get('text', ''), MAX_CHARS_PER_BATCH))
        for text in texts:
            if current_len + len(text) > MAX_CHARS_PER_BATCH and current_batch:
                batch_text = '\n\n'.join(current_batch)
                terms = self.extract_from_text(batch_text)
                for t in terms:
                    key = t['term'].lower()
                    if key not in all_terms:
                        all_terms[key] = t
                current_batch = []
                current_len = 0

            current_batch.append(text)
            current_len += len(text) + 2  # the '\n\n' separator counts too

        if current_batch:
            batch_text = '\n\n'.join(current_batch)
            terms = self.extract_from_text(batch_text)
            for t in terms:
                key = t['term'].lower()
                if key not in all_terms:
                    all_terms[key] = t

        return sorted(all_terms.values(), key=lambda x: x['term'].lower())

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
