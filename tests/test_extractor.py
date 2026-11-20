"""Tests for EPUB extraction module."""

import pytest
from ebooklib import epub
from src.epub.extractor import EPUBExtractor


class TestEPUBExtractor:

    def test_load_valid_epub(self, sample_epub):
        extractor = EPUBExtractor(sample_epub)
        extractor.load()
        assert extractor.opf is not None  # Phase 2: no ebooklib book any more

    def test_load_nonexistent_file(self, tmp_path):
        extractor = EPUBExtractor(str(tmp_path / 'nonexistent.epub'))
        with pytest.raises(Exception):
            extractor.load()

    def test_get_metadata(self, sample_epub):
        extractor = EPUBExtractor(sample_epub)
        extractor.load()
        meta = extractor.get_metadata()

        assert meta['title'] == 'Test Book'
        assert meta['author'] == 'Test Author'
        assert meta['language'] == 'en'

    def test_get_metadata_without_load(self, sample_epub):
        extractor = EPUBExtractor(sample_epub)
        with pytest.raises(RuntimeError, match="not loaded"):
            extractor.get_metadata()

    def test_extract_chapters_count(self, sample_epub):
        extractor = EPUBExtractor(sample_epub)
        extractor.load()
        chapters = extractor.extract_chapters()

        # Should have 3 content chapters (nav may or may not be filtered)
        assert len(chapters) >= 3

    def test_extract_chapters_content(self, sample_epub):
        extractor = EPUBExtractor(sample_epub)
        extractor.load()
        chapters = extractor.extract_chapters()

        # Find chapter with "Introduction" in title
        intro = [c for c in chapters if 'Introduction' in c.title]
        assert len(intro) >= 1
        assert 'introduction' in intro[0].text_content.lower()

    def test_chapter_has_html_structure(self, sample_epub):
        extractor = EPUBExtractor(sample_epub)
        extractor.load()
        chapters = extractor.extract_chapters()

        # Phase 2: the chapter holds its parsed body instead of an HTML string
        for chapter in chapters:
            assert len(chapter.body) > 0
            assert len(chapter.text_content) > 0

    def test_chapter_order(self, sample_epub):
        extractor = EPUBExtractor(sample_epub)
        extractor.load()
        chapters = extractor.extract_chapters()

        orders = [c.order for c in chapters]
        assert orders == sorted(orders)

    def test_extract_title_from_headings(self, sample_epub):
        extractor = EPUBExtractor(sample_epub)
        extractor.load()
        chapters = extractor.extract_chapters()

        # At least some chapters should have proper titles from headings
        titled = [c for c in chapters if c.title != 'Untitled']
        assert len(titled) > 0

    def test_extract_chapters_without_load(self, sample_epub):
        extractor = EPUBExtractor(sample_epub)
        with pytest.raises(RuntimeError, match="not loaded"):
            extractor.extract_chapters()

    def test_extract_chapters_skips_duplicate_spine(self, epub_with_duplicate_spine):
        """Verify that duplicate spine entries are skipped to avoid duplicate segments."""
        extractor = EPUBExtractor(epub_with_duplicate_spine)
        extractor.load()
        chapters = extractor.extract_chapters()

        # Spine has: nav, chapter_0, chapter_1, chapter_0 (duplicate)
        # Should skip the duplicate, resulting in unique chapter IDs
        chapter_ids = [c.id for c in chapters]
        assert len(chapter_ids) == len(set(chapter_ids)), "Duplicate chapters should be skipped"

        # Verify we got both content chapters (excluding nav)
        content_chapters = [c for c in chapters if c.id != 'nav']
        assert len(content_chapters) == 2
        assert any('Chapter One' in c.title for c in content_chapters)
        assert any('Chapter Two' in c.title for c in content_chapters)


class TestCommentsThroughPipeline:
    """Body-level comments must not reach the segmenter as visible text."""

    def test_body_comment_not_in_any_segment(self, tmp_path):
        from src.core.segmenter import Segmenter

        book = epub.EpubBook()
        book.set_identifier('comment-test')
        book.set_title('Comment Test')
        book.set_language('en')
        chapter = epub.EpubHtml(title='One', file_name='one.xhtml', lang='en')
        chapter.content = ('<html><body><p>First paragraph.</p>'
                           '<!-- hiddennote -->'
                           '<p>Second paragraph.</p></body></html>')
        book.add_item(chapter)
        book.spine = [chapter]
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        path = str(tmp_path / 'comment.epub')
        epub.write_epub(path, book)

        extractor = EPUBExtractor(path)
        extractor.load()
        chapters = extractor.extract_chapters()
        assert len(chapters) == 1
        assert 'hiddennote' not in chapters[0].text_content

        segments = Segmenter().segment_chapter(chapters[0].id, chapters[0].body)
        assert segments
        for seg in segments:
            assert 'hiddennote' not in seg.text
        joined = ' '.join(seg.text for seg in segments)
        assert 'First paragraph.' in joined and 'Second paragraph.' in joined
