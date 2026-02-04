"""Term extractor - uses Claude CLI to extract technical terms from text."""

from __future__ import annotations

import json
import subprocess
import logging

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = """你是一位專業的技術術語提取專家。請從以下英文文本中提取所有技術術語和專業名詞。

輸出要求：
1. 輸出 JSON 陣列格式
2. 每個術語包含 term（英文術語）、translation（繁體中文翻譯，使用台灣用語）、category（類別）
3. 只提取專業術語，不要提取常見英文詞彙
4. 類別可選：technology, programming, data, networking, security, cloud, ai, other
5. 直接輸出 JSON，不要加任何說明或 markdown 格式

範例輸出：
[{{"term": "machine learning", "translation": "機器學習", "category": "ai"}}, {{"term": "API", "translation": "應用程式介面", "category": "programming"}}]

文本：
{text}"""

MAX_CHARS_PER_BATCH = 5000


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
                logger.warning("Term extraction failed: %s", result.stderr.strip())
                return []

            output = result.stdout.strip()
            return self._parse_response(output)

        except subprocess.TimeoutExpired:
            logger.warning("Term extraction timed out")
            return []
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

        for seg in segments:
            text = seg.get('text', '')
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
            current_len += len(text)

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
