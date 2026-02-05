"""Smart segmentation module - splits chapter content into translatable segments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from bs4 import BeautifulSoup, Tag, NavigableString


@dataclass
class Segment:
    """A single translatable segment."""
    id: str
    html: str
    text: str
    word_count: int
    segment_type: str  # 'text' or 'code'
    standalone: bool = False


MIN_WORDS = 300
MAX_WORDS = 500
SMALL_SEGMENT_THRESHOLD = 100


class Segmenter:
    """Splits HTML content into segments of 300-500 words."""

    def segment_chapter(self, chapter_id: str, html_content: str) -> list[Segment]:
        """Split a chapter's HTML into translatable segments.

        Rules:
        - Headings (h1-h6) are always standalone segments
        - Code blocks (<pre>, <code> blocks) are always standalone segments
        - Tables are always standalone segments
        - Regular text is grouped into 300-500 word segments, breaking at sentence boundaries
        - Small consecutive text segments (<100 words) are merged
        - Large text segments (>500 words) are split at sentence boundaries
        """
        soup = BeautifulSoup(html_content, 'lxml')
        body = soup.find('body')
        if body is None:
            body = soup

        blocks = self._extract_blocks(body)
        segments = self._merge_blocks_into_segments(blocks, chapter_id)
        segments = self._post_merge_small_segments(segments, chapter_id)
        segments = self._split_large_segments(segments, chapter_id)
        return segments

    def _extract_blocks(self, element) -> list[dict]:
        """Extract logical blocks from HTML, classifying each as text or code."""
        blocks = []
        for child in element.children:
            if isinstance(child, NavigableString):
                text = str(child).strip()
                if text:
                    blocks.append({
                        'html': str(child),
                        'text': text,
                        'type': 'text',
                        'standalone': False,
                    })
                continue

            if not isinstance(child, Tag):
                continue

            tag_name = child.name

            if tag_name in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                text = child.get_text(separator=' ', strip=True)
                if text:
                    blocks.append({
                        'html': str(child),
                        'text': text,
                        'type': 'text',
                        'standalone': True,
                    })
            elif tag_name == 'table':
                text = child.get_text(separator=' ', strip=True)
                if text:
                    blocks.append({
                        'html': str(child),
                        'text': text,
                        'type': 'text',
                        'standalone': True,
                    })
            elif tag_name == 'pre' or (tag_name == 'code' and child.parent and child.parent.name != 'pre'):
                text = child.get_text()
                if text.strip():
                    blocks.append({
                        'html': str(child),
                        'text': text,
                        'type': 'code',
                        'standalone': True,
                    })
            elif tag_name in ('div', 'section', 'article'):
                inner_blocks = self._extract_blocks(child)
                blocks.extend(inner_blocks)
            else:
                text = child.get_text(separator=' ', strip=True)
                if text:
                    blocks.append({
                        'html': str(child),
                        'text': text,
                        'type': 'text',
                        'standalone': False,
                    })

        return blocks

    def _merge_blocks_into_segments(self, blocks: list[dict], chapter_id: str) -> list[Segment]:
        """Merge text blocks into segments of 300-500 words.

        Headings, code blocks, and tables are always standalone.
        Text blocks are accumulated until reaching 300-500 words,
        then split at sentence boundaries.
        """
        segments = []
        seg_counter = 0
        pending_html_parts = []
        pending_text_parts = []
        pending_word_count = 0

        def flush_pending():
            nonlocal seg_counter, pending_html_parts, pending_text_parts, pending_word_count
            if not pending_html_parts:
                return
            seg_counter += 1
            segments.append(Segment(
                id=f"{chapter_id}_seg{seg_counter:04d}",
                html='\n'.join(pending_html_parts),
                text='\n'.join(pending_text_parts),
                word_count=pending_word_count,
                segment_type='text',
            ))
            pending_html_parts = []
            pending_text_parts = []
            pending_word_count = 0

        for block in blocks:
            if block.get('standalone', False):
                flush_pending()
                seg_counter += 1
                wc = len(block['text'].split())
                segments.append(Segment(
                    id=f"{chapter_id}_seg{seg_counter:04d}",
                    html=block['html'],
                    text=block['text'],
                    word_count=wc,
                    segment_type=block['type'],
                    standalone=True,
                ))
                continue

            # Text block
            block_words = len(block['text'].split())

            if pending_word_count + block_words <= MAX_WORDS:
                pending_html_parts.append(block['html'])
                pending_text_parts.append(block['text'])
                pending_word_count += block_words
                if pending_word_count >= MIN_WORDS:
                    flush_pending()
            else:
                if pending_word_count >= MIN_WORDS:
                    flush_pending()
                    pending_html_parts.append(block['html'])
                    pending_text_parts.append(block['text'])
                    pending_word_count = block_words
                    if pending_word_count >= MIN_WORDS:
                        flush_pending()
                else:
                    sentences = self._split_sentences(block['text'])
                    sentence_htmls = self._split_sentences(block['html'])

                    for i, sentence in enumerate(sentences):
                        swc = len(sentence.split())
                        pending_text_parts.append(sentence)
                        if i < len(sentence_htmls):
                            pending_html_parts.append(sentence_htmls[i])
                        pending_word_count += swc

                        if pending_word_count >= MIN_WORDS:
                            flush_pending()

        flush_pending()

        return segments

    def _post_merge_small_segments(self, segments: list[Segment], chapter_id: str) -> list[Segment]:
        """Merge consecutive small text segments (<100 words) until they reach MAX_WORDS."""
        result = []
        pending_html = []
        pending_text = []
        pending_wc = 0

        def flush():
            nonlocal pending_html, pending_text, pending_wc
            if not pending_html:
                return
            result.append(Segment(
                id='',  # re-assigned later
                html='\n'.join(pending_html),
                text='\n'.join(pending_text),
                word_count=pending_wc,
                segment_type='text',
            ))
            pending_html = []
            pending_text = []
            pending_wc = 0

        for seg in segments:
            if seg.segment_type != 'text' or seg.standalone:
                flush()
                result.append(seg)
                continue

            if seg.word_count < SMALL_SEGMENT_THRESHOLD and pending_wc + seg.word_count <= MAX_WORDS:
                pending_html.append(seg.html)
                pending_text.append(seg.text)
                pending_wc += seg.word_count
            else:
                if pending_html:
                    if pending_wc + seg.word_count <= MAX_WORDS and seg.word_count < SMALL_SEGMENT_THRESHOLD:
                        pending_html.append(seg.html)
                        pending_text.append(seg.text)
                        pending_wc += seg.word_count
                    else:
                        flush()
                        result.append(seg)
                else:
                    result.append(seg)

        flush()

        # Re-assign sequential IDs
        counter = 0
        for seg in result:
            counter += 1
            seg.id = f"{chapter_id}_seg{counter:04d}"

        return result

    def _split_large_segments(self, segments: list[Segment], chapter_id: str) -> list[Segment]:
        """Split text segments larger than MAX_WORDS at HTML element boundaries.

        Unlike sentence-level splitting, this preserves complete HTML elements.
        If a segment contains only a single element, it is kept as-is even if
        it exceeds MAX_WORDS (better a large segment than broken HTML).
        """
        result = []

        for seg in segments:
            if seg.segment_type != 'text' or seg.standalone or seg.word_count <= MAX_WORDS:
                result.append(seg)
                continue

            # Parse HTML to get individual elements
            frag = BeautifulSoup(seg.html, 'html.parser')
            elements = [c for c in frag.children
                        if isinstance(c, Tag) or (isinstance(c, NavigableString) and c.strip())]

            # Single element or no elements — cannot split, keep as-is
            if len(elements) <= 1:
                result.append(seg)
                continue

            # Group elements so each group stays <= MAX_WORDS
            current_htmls = []
            current_texts = []
            current_wc = 0

            for elem in elements:
                elem_html = str(elem)
                elem_text = elem.get_text(separator=' ', strip=True) if isinstance(elem, Tag) else str(elem).strip()
                elem_wc = len(elem_text.split())

                if current_wc + elem_wc > MAX_WORDS and current_htmls:
                    result.append(Segment(
                        id='',
                        html='\n'.join(current_htmls),
                        text='\n'.join(current_texts),
                        word_count=current_wc,
                        segment_type='text',
                    ))
                    current_htmls = []
                    current_texts = []
                    current_wc = 0

                current_htmls.append(elem_html)
                current_texts.append(elem_text)
                current_wc += elem_wc

            if current_htmls:
                result.append(Segment(
                    id='',
                    html='\n'.join(current_htmls),
                    text='\n'.join(current_texts),
                    word_count=current_wc,
                    segment_type='text',
                ))

        # Re-assign sequential IDs
        counter = 0
        for seg in result:
            counter += 1
            seg.id = f"{chapter_id}_seg{counter:04d}"

        return result

    def _split_sentences(self, text: str) -> list[str]:
        """Split text at sentence boundaries (period, question mark, exclamation mark)."""
        parts = re.split(r'(?<=[.!?])\s+', text)
        return [p for p in parts if p.strip()]
