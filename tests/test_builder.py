"""Tests for EPUB builder module."""

import os
import pytest
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

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


@pytest.fixture
def rich_translated_project(tmp_path, rich_sample_epub):
    """Create a project from the rich EPUB with all segments translated."""
    from src.epub.extractor import EPUBExtractor
    from src.core.segmenter import Segmenter

    project_dir = str(tmp_path / 'rich_project')
    os.makedirs(project_dir, exist_ok=True)

    extractor = EPUBExtractor(rich_sample_epub)
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
        epub_path=rich_sample_epub,
        title=metadata['title'],
        author=metadata['author'],
        segments=all_segments,
    )

    for seg in pm.data.segments:
        pm.mark_translated(seg.segment_id, f'翻譯_{seg.segment_id}')

    return project_dir


def _get_chapter_html(epub_path, index=0):
    """Helper to read a chapter's HTML from a built EPUB."""
    book = epub.read_epub(epub_path)
    docs = [item for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)
            if 'nav' not in (item.get_name() or '').lower()]
    content = docs[index].get_content().decode('utf-8')
    return BeautifulSoup(content, 'html.parser')


class TestEPUBBuilder:

    def test_build_chinese_only(self, translated_project, tmp_path):
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'output.epub')
        result = builder.build(output, fmt='chinese-only')

        assert os.path.isfile(result)
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
        items = list(book.get_items())
        assert len(items) > 0

        # Verify translated text appears in document content
        found_translation = False
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            content = item.get_content().decode('utf-8', errors='ignore')
            if '翻譯_' in content:
                found_translation = True
                break
        assert found_translation, "No translated text found in output EPUB"

    def test_bilingual_horizontal_has_both_texts(self, translated_project, tmp_path):
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'output.epub')
        builder.build(output, fmt='bilingual-horizontal')

        book = epub.read_epub(output)
        found_bilingual = False
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            content = item.get_content().decode('utf-8', errors='ignore')
            if 'bilingual-container' in content:
                found_bilingual = True
                assert 'class="original"' in content
                assert 'class="translation"' in content
                break

        assert found_bilingual, "No bilingual content found in output EPUB"

    def test_bilingual_vertical_has_both_texts(self, translated_project, tmp_path):
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'output.epub')
        builder.build(output, fmt='bilingual-vertical')

        book = epub.read_epub(output)
        found_bilingual = False
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            content = item.get_content().decode('utf-8', errors='ignore')
            if 'bilingual-vertical' in content:
                found_bilingual = True
                assert 'class="original"' in content
                assert 'class="translation"' in content
                break

        assert found_bilingual, "No bilingual-vertical content found in output EPUB"

    def test_output_directory_created(self, translated_project, tmp_path):
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'nested' / 'dir' / 'output.epub')
        result = builder.build(output, fmt='chinese-only')

        assert os.path.isfile(result)

    def test_metadata_preserved(self, translated_project, tmp_path):
        """Deepcopy preserves original book metadata (title, author, language)."""
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'output.epub')
        builder.build(output, fmt='chinese-only')

        book = epub.read_epub(output)
        title = book.get_metadata('DC', 'title')
        assert title[0][0] == 'Test Book'

        author = book.get_metadata('DC', 'creator')
        assert author[0][0] == 'Test Author'

        lang = book.get_metadata('DC', 'language')
        assert lang[0][0] == 'en'


class TestHTMLAwareTranslation:
    """Tests that the builder handles HTML-format translated text."""

    def test_chinese_only_html_translation_preserves_structure(self, tmp_path, rich_sample_epub):
        """When translated_text is HTML matching original structure, elements are preserved."""
        from src.epub.extractor import EPUBExtractor
        from src.core.segmenter import Segmenter

        project_dir = str(tmp_path / 'html_project')
        os.makedirs(project_dir, exist_ok=True)

        extractor = EPUBExtractor(rich_sample_epub)
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
            epub_path=rich_sample_epub,
            title=metadata['title'],
            author=metadata['author'],
            segments=all_segments,
        )

        # Provide HTML-format translations for text segments
        for seg in pm.data.segments:
            if seg.segment_type == 'text':
                # Simulate what the new HTML prompt would return
                from bs4 import BeautifulSoup as BS, Tag as BTag
                frag = BS(seg.html, 'html.parser')
                tags = [c for c in frag.children if isinstance(c, BTag)]
                html_parts = []
                for tag in tags:
                    tag.clear()
                    tag.append(f'翻譯_{tag.name}')
                    html_parts.append(str(tag))
                pm.mark_translated(seg.segment_id, '\n'.join(html_parts) if html_parts else f'翻譯_{seg.segment_id}')
            else:
                pm.mark_translated(seg.segment_id, f'翻譯_{seg.segment_id}')

        builder = EPUBBuilder(project_dir)
        output = str(tmp_path / 'html_output.epub')
        builder.build(output, fmt='chinese-only')

        soup = _get_chapter_html(output, 0)
        # blockquote should still exist because HTML-aware replacement matched
        bq = soup.find('blockquote')
        assert bq is not None, "blockquote should be preserved when HTML translation matches"

    def test_chinese_only_plaintext_fallback(self, tmp_path, rich_sample_epub):
        """When translated_text is plain text, fallback to old behavior."""
        from src.epub.extractor import EPUBExtractor
        from src.core.segmenter import Segmenter

        project_dir = str(tmp_path / 'plain_project')
        os.makedirs(project_dir, exist_ok=True)

        extractor = EPUBExtractor(rich_sample_epub)
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
            epub_path=rich_sample_epub,
            title=metadata['title'],
            author=metadata['author'],
            segments=all_segments,
        )

        # Plain text translations (old style)
        for seg in pm.data.segments:
            pm.mark_translated(seg.segment_id, f'翻譯_{seg.segment_id}')

        builder = EPUBBuilder(project_dir)
        output = str(tmp_path / 'plain_output.epub')
        builder.build(output, fmt='chinese-only')

        # Should still work without errors
        assert os.path.isfile(output)
        book = epub.read_epub(output)
        assert book is not None

    def test_bilingual_html_translation_in_translation_div(self, tmp_path, rich_sample_epub):
        """Bilingual mode should use HTML translation directly in the translation div."""
        from src.epub.extractor import EPUBExtractor
        from src.core.segmenter import Segmenter

        project_dir = str(tmp_path / 'bi_html_project')
        os.makedirs(project_dir, exist_ok=True)

        extractor = EPUBExtractor(rich_sample_epub)
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
            epub_path=rich_sample_epub,
            title=metadata['title'],
            author=metadata['author'],
            segments=all_segments,
        )

        # Set HTML-format translations
        for seg in pm.data.segments:
            if seg.segment_type == 'text':
                pm.mark_translated(seg.segment_id, '<p>翻譯段落</p>')
            else:
                pm.mark_translated(seg.segment_id, f'翻譯_{seg.segment_id}')

        builder = EPUBBuilder(project_dir)
        output = str(tmp_path / 'bi_html_output.epub')
        builder.build(output, fmt='bilingual-vertical')

        soup = _get_chapter_html(output, 0)
        translation_divs = soup.find_all('div', class_='translation')
        # At least one translation div should have a <p> tag inside
        found_p = any(div.find('p') for div in translation_divs)
        assert found_p, "Translation div should contain <p> from HTML translation"


class TestHTMLPreservation:
    """Tests that the new builder preserves original HTML structure."""

    def test_heading_tag_preserved(self, translated_project, tmp_path):
        """Heading tags (h1, h2) are preserved, only content replaced."""
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'output.epub')
        builder.build(output, fmt='chinese-only')

        soup = _get_chapter_html(output, 0)
        h1 = soup.find('h1')
        assert h1 is not None, "h1 tag should be preserved"
        assert '翻譯_' in h1.get_text()

    def test_code_block_preserved(self, translated_project, tmp_path):
        """pre/code structure is preserved in chinese-only output."""
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'output.epub')
        builder.build(output, fmt='chinese-only')

        # Chapter 2 has a code block
        soup = _get_chapter_html(output, 1)
        pre = soup.find('pre')
        assert pre is not None, "pre tag should be preserved"
        code = pre.find('code')
        assert code is not None, "code tag inside pre should be preserved"

    def test_css_classes_preserved_chinese_only(self, rich_translated_project, tmp_path):
        """CSS classes on elements are preserved in chinese-only format."""
        builder = EPUBBuilder(rich_translated_project)
        output = str(tmp_path / 'output.epub')
        builder.build(output, fmt='chinese-only')

        soup = _get_chapter_html(output, 0)
        # The heading should preserve its id
        h1 = soup.find('h1')
        assert h1 is not None
        assert h1.get('id') == 'ch1', "h1 id attribute should be preserved"

    def test_original_css_preserved(self, rich_translated_project, tmp_path):
        """Original CSS files from source EPUB are preserved."""
        builder = EPUBBuilder(rich_translated_project)
        output = str(tmp_path / 'output.epub')
        builder.build(output, fmt='chinese-only')

        book = epub.read_epub(output)
        styles = list(book.get_items_of_type(ebooklib.ITEM_STYLE))
        css_names = [s.get_name() for s in styles]
        assert any('main.css' in name for name in css_names), \
            f"Original CSS should be preserved. Found: {css_names}"

    def test_original_css_item_in_book(self, rich_translated_project, tmp_path):
        """Original CSS items are preserved in the output EPUB's manifest."""
        builder = EPUBBuilder(rich_translated_project)
        output = str(tmp_path / 'output.epub')
        builder.build(output, fmt='chinese-only')

        book = epub.read_epub(output)
        styles = list(book.get_items_of_type(ebooklib.ITEM_STYLE))
        css_names = [s.get_name() for s in styles]
        assert any('main.css' in n for n in css_names), \
            f"Original CSS should be in manifest. Found: {css_names}"

    def test_bilingual_css_item_in_book(self, translated_project, tmp_path):
        """Bilingual formats add bilingual.css to the EPUB manifest."""
        builder = EPUBBuilder(translated_project)
        output = str(tmp_path / 'output.epub')
        builder.build(output, fmt='bilingual-horizontal')

        book = epub.read_epub(output)
        styles = list(book.get_items_of_type(ebooklib.ITEM_STYLE))
        css_names = [s.get_name() for s in styles]
        assert any('bilingual' in n for n in css_names), \
            f"Bilingual CSS should be in manifest. Found: {css_names}"

    def test_untranslated_segments_unchanged(self, tmp_path, sample_epub):
        """Segments without translations remain as original HTML."""
        from src.epub.extractor import EPUBExtractor
        from src.core.segmenter import Segmenter

        project_dir = str(tmp_path / 'partial_project')
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

        # Only translate the first segment
        if pm.data.segments:
            pm.mark_translated(pm.data.segments[0].segment_id, '僅翻譯第一段')

        builder = EPUBBuilder(project_dir)
        output = str(tmp_path / 'partial.epub')
        builder.build(output, fmt='chinese-only')

        # Verify the output is valid
        book = epub.read_epub(output)
        assert book is not None

        # The original text should still exist for untranslated chapters
        found_original = False
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            content = item.get_content().decode('utf-8', errors='ignore')
            if 'Advanced Topics' in content:
                found_original = True
                break
        assert found_original, "Untranslated chapter content should be preserved"

    def test_bilingual_horizontal_preserves_original_structure(
            self, rich_translated_project, tmp_path):
        """Bilingual horizontal wraps original elements in flex container."""
        builder = EPUBBuilder(rich_translated_project)
        output = str(tmp_path / 'output.epub')
        builder.build(output, fmt='bilingual-horizontal')

        soup = _get_chapter_html(output, 0)
        containers = soup.find_all('div', class_='bilingual-container')
        assert len(containers) > 0, "Should have bilingual containers"

        # Check first container has original and translation divs
        container = containers[0]
        original = container.find('div', class_='original')
        translation = container.find('div', class_='translation')
        assert original is not None
        assert translation is not None

    def test_bilingual_vertical_preserves_original_structure(
            self, rich_translated_project, tmp_path):
        """Bilingual vertical keeps original + adds translation div."""
        builder = EPUBBuilder(rich_translated_project)
        output = str(tmp_path / 'output.epub')
        builder.build(output, fmt='bilingual-vertical')

        soup = _get_chapter_html(output, 0)
        wrappers = soup.find_all('div', class_='bilingual-vertical')
        assert len(wrappers) > 0, "Should have bilingual-vertical wrappers"

        wrapper = wrappers[0]
        original = wrapper.find('div', class_='original')
        translation = wrapper.find('div', class_='translation')
        assert original is not None
        assert translation is not None
