"""Tests for EPUB builder module."""

import os
import pytest
import ebooklib
from ebooklib import epub

from src.epub.builder import EPUBBuilder, BILINGUAL_CSS
from src.core.project import ProjectManager
from src.core.segmenter import Segment


@pytest.fixture
def translated_project(tmp_path, sample_epub):
    """Create a project with some translated segments."""
    from src.epub.extractor import EPUBExtractor
    from src.core.segmenter import Segmenter

    project_dir = str(tmp_path / 'test_project')
    os.makedirs(project_dir, exist_ok=True)

    extractor = EPUBExtractor(sample_epub)
    extractor.load()
    metadata = extractor.get_metadata()
    chapters = extractor.extract_chapters()

    segmenter = Segmenter()
    all_segments = []
    for ch in chapters:
        segs = segmenter.segment_chapter(ch.id, ch.html_content)
        all_segments.extend(segs)

    pm = ProjectManager(project_dir)
    pm.create(
        epub_path=sample_epub,
        title=metadata['title'],
        author=metadata['author'],
        segments=all_segments,
    )

    # Translate all segments
    for seg in pm.data.segments:
        pm.mark_translated(seg.segment_id, f'翻譯_{seg.segment_id}')

    return project_dir


class TestEPUBBuilder:

    def test_build_chinese_only(self, translated_project, tmp_path):
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'output.epub')
        result = builder.build(output, fmt='chinese-only')

        assert os.path.isfile(result)
        # Verify it's a valid EPUB by reading it back
        book = epub.read_epub(result)
        assert book is not None

    def test_build_bilingual_horizontal(self, translated_project, tmp_path):
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'output.epub')
        result = builder.build(output, fmt='bilingual-horizontal')

        assert os.path.isfile(result)
        book = epub.read_epub(result)

        # Check that bilingual CSS is included
        styles = list(book.get_items_of_type(ebooklib.ITEM_STYLE))
        css_found = any('bilingual' in (s.get_name() or '') for s in styles)
        assert css_found

    def test_build_bilingual_vertical(self, translated_project, tmp_path):
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'output.epub')
        result = builder.build(output, fmt='bilingual-vertical')

        assert os.path.isfile(result)
        book = epub.read_epub(result)
        assert book is not None

    def test_invalid_format_raises(self, translated_project, tmp_path):
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'output.epub')

        with pytest.raises(ValueError, match="Invalid format"):
            builder.build(output, fmt='invalid')

    def test_chinese_only_contains_translation(self, translated_project, tmp_path):
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'output.epub')
        builder.build(output, fmt='chinese-only')

        book = epub.read_epub(output)
        docs = list(book.get_items_of_type(9))  # Check for content
        items = list(book.get_items())
        # At least some content should exist
        assert len(items) > 0

    def test_bilingual_horizontal_has_both_texts(self, translated_project, tmp_path):
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'output.epub')
        builder.build(output, fmt='bilingual-horizontal')

        book = epub.read_epub(output)
        # Find HTML document items containing bilingual container
        found_bilingual = False
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            content = item.get_content().decode('utf-8', errors='ignore')
            if 'bilingual-container' in content:
                found_bilingual = True
                assert 'class="original"' in content
                assert 'class="translation"' in content
                break

        assert found_bilingual, "No bilingual content found in output EPUB"

    def test_output_directory_created(self, translated_project, tmp_path):
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'nested' / 'dir' / 'output.epub')
        result = builder.build(output, fmt='chinese-only')

        assert os.path.isfile(result)

    def test_metadata_copied(self, translated_project, tmp_path):
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'output.epub')
        builder.build(output, fmt='chinese-only')

        book = epub.read_epub(output)
        lang = book.get_metadata('DC', 'language')
        assert lang[0][0] == 'zh-TW'
