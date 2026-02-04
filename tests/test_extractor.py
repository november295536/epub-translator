"""Tests for EPUB extraction module."""

import pytest
from src.epub.extractor import EPUBExtractor


class TestEPUBExtractor:

    def test_load_valid_epub(self, sample_epub):
        extractor = EPUBExtractor(sample_epub)
        extractor.load()
        assert extractor.book is not None

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

        # HTML content should contain tags
        for chapter in chapters:
            assert '<' in chapter.html_content
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
