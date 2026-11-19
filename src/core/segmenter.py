"""Segmentation: groups the blocks of a chapter into translatable segments.

A segment is a contiguous half-open range of blocks in one chapter
(`block_start`, `block_end`) and stores the fingerprints of those blocks. A
block is never divided; segment HTML is the serialization of its blocks,
one per line. Headings, tables and code are segments of their own; other
blocks are grouped until a segment reaches MIN_WORDS, without passing
MAX_WORDS unless a single block is larger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from bs4 import NavigableString, Tag
from bs4.element import PreformattedString

from src.epub.blocks import BLOCK_TAGS, Block, element_text, localname, walk_blocks


class SegmentType(str, Enum):
    """Segment content type."""
    TEXT = 'text'
    CODE = 'code'

    def __str__(self):
        return self.value


@dataclass
class Segment:
    """A single translatable segment."""
    id: str
    html: str
    text: str
    word_count: int
    segment_type: SegmentType
    standalone: bool = False
    block_start: int | None = None
    block_end: int | None = None
    fingerprints: list[str] = field(default_factory=list)


MIN_WORDS = 300
MAX_WORDS = 500
STANDALONE_TAGS = frozenset({'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table'})


# Used by the builder's walk for schema 1 projects
def _is_container(tag: Tag, legacy: bool = False) -> bool:
    """Container = only block-level children + whitespace. Content = has text or inline tags.

    Comments and other non-text strings (CData, ProcessingInstruction,
    Doctype) are ignored. ``legacy=True`` restores the old behaviour of
    counting them as text; the builder needs it for projects that were
    segmented before comments were skipped.
    """
    for child in tag.children:
        if not legacy and isinstance(child, PreformattedString):
            continue
        if isinstance(child, NavigableString):
            if child.strip():
                return False
        elif isinstance(child, Tag):
            if child.name not in BLOCK_TAGS:
                return False
    return True


class Segmenter:
    """Groups a chapter's blocks into segments of 300-500 words."""

    def segment_chapter(self, chapter_id: str, body) -> list[Segment]:
        """Segment the parsed <body> element of a chapter."""
        segments: list[Segment] = []
        pending: list[Block] = []

        def add(blocks: list[Block], segment_type: SegmentType, standalone: bool) -> None:
            segments.append(segment_from_blocks(
                f"{chapter_id}_seg{len(segments) + 1:04d}", blocks, segment_type, standalone))

        def flush() -> None:
            if pending:
                add(pending[:], SegmentType.TEXT, False)
                pending.clear()

        for block in walk_blocks(body):
            if _code_text(block) is not None:
                flush()
                add([block], SegmentType.CODE, True)
            elif block.tag in STANDALONE_TAGS:
                flush()
                add([block], SegmentType.TEXT, True)
            else:
                words = len(block.text.split())
                if sum(len(b.text.split()) for b in pending) + words > MAX_WORDS:
                    flush()
                pending.append(block)
                if sum(len(b.text.split()) for b in pending) >= MIN_WORDS:
                    flush()
        flush()
        return segments


def segment_from_blocks(segment_id: str, blocks: list[Block], segment_type: SegmentType,
                        standalone: bool) -> Segment:
    """The segment covering `blocks` (consecutive blocks of one chapter)."""
    code = _code_text(blocks[0]) if segment_type == SegmentType.CODE else None
    if code is not None:
        text = code
    else:
        text = '\n'.join(' '.join(b.text.split()) for b in blocks)
    return Segment(
        id=segment_id,
        html='\n'.join(b.html for b in blocks),
        text=text,
        word_count=len(text.split()),
        segment_type=segment_type,
        standalone=standalone,
        block_start=blocks[0].index,
        block_end=blocks[-1].index + 1,
        fingerprints=[b.fingerprint for b in blocks],
    )


def _code_text(block: Block) -> str | None:
    """The code of a code block (a <pre>, or a run that is one <code>
    element), or None for any other block."""
    if block.tag == 'pre':
        return element_text(block.nodes[0])
    if block.tag:
        return None
    elements = [n for n in block.nodes if not isinstance(n, str) and localname(n)]
    strings = [n for n in block.nodes if isinstance(n, str)]
    if (len(elements) == 1 and localname(elements[0]) == 'code'
            and not ''.join(strings).strip()):
        return element_text(elements[0])
    return None
