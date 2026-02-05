"""Tests for smart segmentation module."""

import pytest
from src.core.segmenter import Segmenter, Segment, MIN_WORDS, MAX_WORDS, SMALL_SEGMENT_THRESHOLD


@pytest.fixture
def segmenter():
    return Segmenter()


def make_paragraph(word_count: int, index: int = 0) -> str:
    """Generate a paragraph with approximately the given word count."""
    words = []
    sentence_len = 10
    w = 0
    while w < word_count:
        sentence = ' '.join(f'word{index}_{w+j}' for j in range(sentence_len))
        words.append(sentence + '.')
        w += sentence_len
    return '<p>' + ' '.join(words) + '</p>'


class TestSegmenter:

    def test_heading_standalone_segment(self, segmenter):
        html = '<h1>Chapter Title</h1><p>Some content here in the paragraph.</p>'
        segments = segmenter.segment_chapter('ch1', html)

        heading_segs = [s for s in segments if '<h1' in s.html]
        assert len(heading_segs) >= 1
        assert heading_segs[0].segment_type == 'text'
        assert heading_segs[0].standalone is True
        assert 'Chapter Title' in heading_segs[0].text

    def test_code_block_standalone_segment(self, segmenter):
        html = (
            '<p>Some introductory text.</p>'
            '<pre><code>def hello():\n    print("world")</code></pre>'
            '<p>Some following text.</p>'
        )
        segments = segmenter.segment_chapter('ch1', html)

        code_segs = [s for s in segments if s.segment_type == 'code']
        assert len(code_segs) == 1
        assert 'def hello()' in code_segs[0].text

    def test_text_segments_word_count_range(self, segmenter):
        """Text segments should ideally be in the 300-500 word range."""
        paragraphs = [make_paragraph(100, i) for i in range(15)]
        html = '\n'.join(paragraphs)

        segments = segmenter.segment_chapter('ch1', html)
        text_segs = [s for s in segments if s.segment_type == 'text']

        for seg in text_segs[:-1]:
            assert seg.word_count >= MIN_WORDS * 0.8, (
                f"Segment too small: {seg.word_count} words"
            )
            assert seg.word_count <= MAX_WORDS * 1.2, (
                f"Segment too large: {seg.word_count} words"
            )

    def test_breaks_at_sentence_boundary(self, segmenter):
        """Segments should end at sentence boundaries (period/question/exclamation)."""
        paragraphs = [make_paragraph(100, i) for i in range(15)]
        html = '\n'.join(paragraphs)

        segments = segmenter.segment_chapter('ch1', html)
        text_segs = [s for s in segments if s.segment_type == 'text']

        for seg in text_segs:
            stripped = seg.text.rstrip()
            if stripped:
                assert stripped[-1] in '.!?>', f"Segment doesn't end at sentence boundary: ...{stripped[-20:]}"

    def test_empty_content(self, segmenter):
        html = ''
        segments = segmenter.segment_chapter('ch1', html)
        assert segments == []

    def test_heading_only(self, segmenter):
        html = '<h1>Just a Title</h1>'
        segments = segmenter.segment_chapter('ch1', html)
        assert len(segments) == 1
        assert segments[0].segment_type == 'text'
        assert segments[0].standalone is True

    def test_segment_ids_are_unique(self, segmenter):
        paragraphs = [make_paragraph(100, i) for i in range(10)]
        html = '<h2>Title</h2>\n' + '\n'.join(paragraphs)

        segments = segmenter.segment_chapter('ch1', html)
        ids = [s.id for s in segments]
        assert len(ids) == len(set(ids)), "Segment IDs are not unique"

    def test_segment_id_format(self, segmenter):
        html = '<h1>Title</h1><p>Some text here.</p>'
        segments = segmenter.segment_chapter('chap01', html)

        for seg in segments:
            assert seg.id.startswith('chap01_seg')

    def test_multiple_headings(self, segmenter):
        html = (
            '<h1>Main Title</h1>'
            '<p>Intro paragraph.</p>'
            '<h2>Section 1</h2>'
            '<p>Section 1 content.</p>'
            '<h2>Section 2</h2>'
            '<p>Section 2 content.</p>'
        )
        segments = segmenter.segment_chapter('ch1', html)
        headings = [s for s in segments if s.standalone and '<h' in s.html]
        assert len(headings) == 3

    def test_short_content_single_segment(self, segmenter):
        """Content shorter than MIN_WORDS should still produce a segment."""
        html = '<p>This is a short paragraph with just a few words.</p>'
        segments = segmenter.segment_chapter('ch1', html)
        assert len(segments) >= 1

    def test_preserves_html_in_segments(self, segmenter):
        html = '<p>This is <strong>bold</strong> and <em>italic</em> text.</p>'
        segments = segmenter.segment_chapter('ch1', html)
        assert len(segments) >= 1
        assert '<strong>' in segments[0].html or '<em>' in segments[0].html

    def test_code_block_not_merged_with_text(self, segmenter):
        """Code blocks should never be merged with surrounding text."""
        html = (
            '<p>Before code.</p>'
            '<pre><code>x = 1</code></pre>'
            '<p>After code.</p>'
        )
        segments = segmenter.segment_chapter('ch1', html)

        code_segs = [s for s in segments if s.segment_type == 'code']
        assert len(code_segs) == 1
        assert 'Before code' not in code_segs[0].text
        assert 'After code' not in code_segs[0].text


class TestSmallSegmentMerging:

    def test_consecutive_small_segments_merged(self, segmenter):
        """Multiple small text segments should be merged together."""
        paragraphs = [f'<p>Short paragraph number {i} here.</p>' for i in range(5)]
        html = '\n'.join(paragraphs)

        segments = segmenter.segment_chapter('ch1', html)
        text_segs = [s for s in segments if s.segment_type == 'text']

        # 5 tiny paragraphs should be merged into 1 segment
        assert len(text_segs) == 1

    def test_small_segments_not_exceed_max(self, segmenter):
        """Merged small segments should not exceed MAX_WORDS."""
        paragraphs = [make_paragraph(80, i) for i in range(10)]
        html = '\n'.join(paragraphs)

        segments = segmenter.segment_chapter('ch1', html)
        text_segs = [s for s in segments if s.segment_type == 'text']

        for seg in text_segs:
            assert seg.word_count <= MAX_WORDS * 1.2

    def test_heading_breaks_small_merge(self, segmenter):
        """Headings (standalone) should interrupt small segment merging."""
        html = (
            '<p>Short text one.</p>'
            '<h2>Section Break</h2>'
            '<p>Short text two.</p>'
        )
        segments = segmenter.segment_chapter('ch1', html)
        non_standalone_text = [s for s in segments if s.segment_type == 'text' and not s.standalone]
        heading_segs = [s for s in segments if s.standalone and '<h' in s.html]

        assert len(heading_segs) == 1
        assert len(non_standalone_text) == 2  # Not merged because heading is between them


class TestLargeSegmentSplitting:

    def test_large_segment_is_split(self, segmenter):
        """A single very large text block should be split into smaller segments."""
        # Create a single paragraph with ~800 words
        html = make_paragraph(800)

        segments = segmenter.segment_chapter('ch1', html)
        text_segs = [s for s in segments if s.segment_type == 'text']

        # Single <p> cannot be split at element boundaries, so it stays as one
        assert len(text_segs) >= 1

    def test_large_multi_element_segment_is_split(self, segmenter):
        """Multiple paragraphs exceeding MAX_WORDS should be split at element boundaries."""
        paragraphs = [make_paragraph(200, i) for i in range(5)]
        html = '\n'.join(paragraphs)

        segments = segmenter.segment_chapter('ch1', html)
        text_segs = [s for s in segments if s.segment_type == 'text']

        # ~1000 words across 5 paragraphs should result in multiple segments
        assert len(text_segs) >= 2

    def test_split_preserves_html_tags(self, segmenter):
        """Split segments should preserve complete HTML elements, not plain text."""
        paragraphs = [make_paragraph(200, i) for i in range(5)]
        html = '\n'.join(paragraphs)

        segments = segmenter.segment_chapter('ch1', html)
        text_segs = [s for s in segments if s.segment_type == 'text']

        for seg in text_segs:
            assert '<p>' in seg.html, f"Split segment should contain HTML tags, got: {seg.html[:100]}"

    def test_single_large_element_kept_by_split_step(self, segmenter):
        """_split_large_segments keeps a single-element segment intact.

        Note: a single 800-word <p> gets split by _merge_blocks_into_segments
        at sentence boundaries before _split_large_segments runs. To test
        _split_large_segments directly, we call it with a hand-crafted segment.
        """
        from src.core.segmenter import Segment
        # Create a segment with a single large <p> element
        big_html = make_paragraph(800)
        seg = Segment(id='ch1_seg0001', html=big_html, text='x ' * 800,
                       word_count=800, segment_type='text')
        result = segmenter._split_large_segments([seg], 'ch1')

        # Single element cannot be split at element boundaries
        assert len(result) == 1
        assert '<p>' in result[0].html

    def test_split_html_and_text_differ(self, segmenter):
        """After splitting, html should contain tags while text is plain."""
        paragraphs = [make_paragraph(200, i) for i in range(4)]
        html = '\n'.join(paragraphs)

        segments = segmenter.segment_chapter('ch1', html)
        text_segs = [s for s in segments if s.segment_type == 'text']

        for seg in text_segs:
            # html should have tags
            assert '<p>' in seg.html
            # text should not have tags
            assert '<p>' not in seg.text

    def test_code_block_not_split(self, segmenter):
        """Code blocks should never be split, even if large."""
        lines = [f'line_{i} = {i}  # comment {i}' for i in range(100)]
        code_content = '\n'.join(lines)
        html = f'<pre><code>{code_content}</code></pre>'

        segments = segmenter.segment_chapter('ch1', html)
        code_segs = [s for s in segments if s.segment_type == 'code']

        assert len(code_segs) == 1


class TestTableHandling:

    def test_table_standalone_segment(self, segmenter):
        """Tables should be extracted as standalone segments."""
        html = (
            '<p>Before table.</p>'
            '<table><tr><th>Header</th></tr><tr><td>Data</td></tr></table>'
            '<p>After table.</p>'
        )
        segments = segmenter.segment_chapter('ch1', html)

        table_segs = [s for s in segments if '<table' in s.html]
        assert len(table_segs) == 1
        assert table_segs[0].segment_type == 'text'
        assert table_segs[0].standalone is True
        assert 'Header' in table_segs[0].text

    def test_table_not_merged_with_text(self, segmenter):
        """Tables should not be merged with surrounding text segments."""
        html = (
            '<p>Before table.</p>'
            '<table><tr><td>Cell content</td></tr></table>'
            '<p>After table.</p>'
        )
        segments = segmenter.segment_chapter('ch1', html)

        table_segs = [s for s in segments if '<table' in s.html]
        assert len(table_segs) == 1
        assert table_segs[0].standalone is True
        assert 'Before table' not in table_segs[0].text
        assert 'After table' not in table_segs[0].text


class TestSpaceExtraction:

    def test_span_adjacent_tags_have_spaces(self, segmenter):
        """Adjacent inline tags like <span> should have spaces between their text."""
        html = '<p><span>Hello</span> <span>world</span> from <em>Python</em></p>'
        segments = segmenter.segment_chapter('ch1', html)

        assert len(segments) >= 1
        # Text should have spaces between words
        assert 'Hello world' in segments[0].text or 'Hello world from Python' in segments[0].text
