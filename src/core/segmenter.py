"""Segmentation: groups the blocks of a chapter into translatable segments.

A segment is a contiguous half-open range of blocks in one chapter
(`block_start`, `block_end`) and stores the fingerprints of those blocks. A
block is never divided; segment HTML is the serialization of its blocks,
one per line. Code blocks and tables are segments of their own; other
blocks are grouped until a segment reaches MIN_WORDS, without passing
MAX_WORDS unless a single block is larger.

Headings and code depend on the rules (`rules`, stored as the project's
`segmentation`):

1. (projects created before Phase 6) a heading is a segment of its own.
2. (new projects) a heading starts a new segment and the blocks after it
   join that segment under the usual size rules, except that the first
   text block after a heading always joins, whatever its size: a heading
   is left alone only when a code block, a table or the end of the chapter
   follows it. Consecutive headings share one segment. A code block
   without a comment line (validation.COMMENT_LINE) is `passthrough`: it
   needs no model call and is left as it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.core.validation import COMMENT_LINE
from src.epub.blocks import Block, element_text, localname, walk_blocks


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
    # A code block without a comment line (rules 2): no model call needed
    passthrough: bool = False


MIN_WORDS = 300
MAX_WORDS = 500
HEADING_TAGS = frozenset({'h1', 'h2', 'h3', 'h4', 'h5', 'h6'})
STANDALONE_TAGS = HEADING_TAGS | {'table'}
# The segmentation rules of projects created now (see the module docstring)
SEGMENTATION = 2


def has_comment_line(code: str) -> bool:
    """Whether the code has a comment line, the only lines a model translates."""
    return any(COMMENT_LINE.match(line) for line in code.splitlines())


class Segmenter:
    """Groups a chapter's blocks into segments of 300-500 words, by the
    segmentation rules `rules` (1 or 2, see the module docstring)."""

    def __init__(self, rules: int = SEGMENTATION):
        if rules not in (1, 2):
            raise ValueError(f'unknown segmentation rules: {rules}')
        self.rules = rules

    def segment_chapter(self, chapter_id: str, body) -> list[Segment]:
        """Segment the parsed <body> element of a chapter."""
        segments: list[Segment] = []
        pending: list[Block] = []
        new_rules = self.rules == 2

        def add(blocks: list[Block], segment_type: SegmentType, standalone: bool) -> None:
            segments.append(segment_from_blocks(
                f"{chapter_id}_seg{len(segments) + 1:04d}", blocks, segment_type, standalone))

        def flush() -> None:
            if pending:
                # headings left alone are standalone, as under rules 1
                add(pending[:], SegmentType.TEXT, all(b.tag in HEADING_TAGS for b in pending))
                pending.clear()

        for block in walk_blocks(body):
            code = _code_text(block)
            if code is not None:
                flush()
                add([block], SegmentType.CODE, True)
                segments[-1].passthrough = new_rules and not has_comment_line(code)
            elif new_rules and block.tag in HEADING_TAGS:
                if any(b.tag not in HEADING_TAGS for b in pending):
                    flush()  # a heading starts a segment
                pending.append(block)
            elif block.tag in STANDALONE_TAGS:
                flush()
                add([block], SegmentType.TEXT, True)
            else:
                words = len(block.text.split())
                after_heading = bool(pending) and pending[-1].tag in HEADING_TAGS
                if (not after_heading
                        and sum(len(b.text.split()) for b in pending) + words > MAX_WORDS):
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
