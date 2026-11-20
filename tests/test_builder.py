"""Tests for EPUB builder module."""

import copy
import json
import os
import pytest
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, Comment, Tag

from src.epub.builder import EPUBBuilder, BILINGUAL_CSS
from src.core.project import ProjectManager
from src.core.segmenter import Segment, SegmentType


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
        segs = segmenter.segment_chapter(ch.id, ch.body)
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
    pm.save()

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
        segs = segmenter.segment_chapter(ch.id, ch.body)
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
    pm.save()

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
            segs = segmenter.segment_chapter(ch.id, ch.body)
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
            if seg.segment_type == SegmentType.TEXT:
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
        pm.save()

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
            segs = segmenter.segment_chapter(ch.id, ch.body)
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
        pm.save()

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
            segs = segmenter.segment_chapter(ch.id, ch.body)
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
            if seg.segment_type == SegmentType.TEXT:
                pm.mark_translated(seg.segment_id, '<p>翻譯段落</p>')
            else:
                pm.mark_translated(seg.segment_id, f'翻譯_{seg.segment_id}')
        pm.save()

        builder = EPUBBuilder(project_dir)
        output = str(tmp_path / 'bi_html_output.epub')
        builder.build(output, fmt='bilingual-vertical')

        soup = _get_chapter_html(output, 0)
        translation_divs = soup.find_all('div', class_='translation')
        # At least one translation div should have a <p> tag inside
        found_p = any(div.find('p') for div in translation_divs)
        assert found_p, "Translation div should contain <p> from HTML translation"

    def test_chinese_only_html_mismatched_count(self):
        """When translated HTML has different element count, it should still render as HTML."""
        from src.core.project import SegmentState

        builder = EPUBBuilder.__new__(EPUBBuilder)
        html = '<html><body><p>Paragraph one.</p><p>Paragraph two.</p></body></html>'
        soup = BeautifulSoup(html, 'html.parser')
        body = soup.find('body')
        elements = list(body.children)  # 2 <p> elements

        seg = SegmentState(
            segment_id='ch1_seg0001',
            chapter_id='ch1',
            html='<p>Paragraph one.</p><p>Paragraph two.</p>',
            text='Paragraph one. Paragraph two.',
            word_count=4,
            segment_type=SegmentType.TEXT,
            status='translated',
            translated_text='<p class="author">作者 <span>Name</span></p><p class="copyright">版權</p><p class="loc">地點</p>',
        )

        builder._apply_chinese_only(seg, elements, soup)
        result = str(body)
        # HTML tags should be rendered, not escaped
        assert '&lt;' not in result, "HTML should not be escaped"
        assert '<p class="author">' in result
        assert '<span>Name</span>' in result
        assert '<p class="copyright">' in result
        assert '<p class="loc">' in result


class TestNavigableStringCounting:
    """Tests for _count_segment_blocks with mixed NavigableString and Tag content."""

    def test_count_mixed_tag_and_text(self):
        """Segment HTML with mixed NavigableString + Tag should count both."""
        from src.core.project import SegmentState

        builder = EPUBBuilder.__new__(EPUBBuilder)
        seg = SegmentState(
            segment_id='ch1_seg0001',
            chapter_id='ch1',
            html='Some bare text\n<p>A paragraph</p>',
            text='Some bare text A paragraph',
            word_count=5,
            segment_type=SegmentType.TEXT,
        )
        count = builder._count_segment_blocks(seg)
        assert count == 2, f"Expected 2 (one text node + one <p> tag), got {count}"

    def test_count_only_tags(self):
        """Segment with only Tag children should count Tags."""
        from src.core.project import SegmentState

        builder = EPUBBuilder.__new__(EPUBBuilder)
        seg = SegmentState(
            segment_id='ch1_seg0002',
            chapter_id='ch1',
            html='<p>First</p><p>Second</p>',
            text='First Second',
            word_count=2,
            segment_type=SegmentType.TEXT,
        )
        count = builder._count_segment_blocks(seg)
        assert count == 2

    def test_count_whitespace_only_text_ignored(self):
        """Whitespace-only NavigableString nodes should not be counted."""
        from src.core.project import SegmentState

        builder = EPUBBuilder.__new__(EPUBBuilder)
        seg = SegmentState(
            segment_id='ch1_seg0003',
            chapter_id='ch1',
            html='\n<p>Paragraph</p>\n',
            text='Paragraph',
            word_count=1,
            segment_type=SegmentType.TEXT,
        )
        count = builder._count_segment_blocks(seg)
        assert count == 1, "Whitespace-only text nodes should not be counted"


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
            segs = segmenter.segment_chapter(ch.id, ch.body)
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
            pm.save()

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


class TestTOCTranslation:
    """Tests for TOC title translation during EPUB export."""

    def test_toc_titles_translated(self, tmp_path, sample_epub):
        """TOC titles are replaced with translated heading text after build."""
        from src.epub.extractor import EPUBExtractor
        from src.core.segmenter import Segmenter

        project_dir = str(tmp_path / 'toc_project')
        os.makedirs(project_dir, exist_ok=True)

        extractor = EPUBExtractor(sample_epub)
        extractor.load()
        metadata = extractor.get_metadata()
        chapters = extractor.extract_chapters()

        segmenter = Segmenter()
        all_segments = []
        for ch in chapters:
            segs = segmenter.segment_chapter(ch.id, ch.body)
            all_segments.extend(segs)

        pm = ProjectManager(project_dir)
        pm.create(
            epub_path=sample_epub,
            title=metadata['title'],
            author=metadata['author'],
            segments=all_segments,
        )

        # Translate heading segments with Chinese titles based on original text
        heading_map = {
            'Introduction': '<h1>導論</h1>',
            'Chapter 1: Main Content': '<h2>第一章：主要內容</h2>',
            'Advanced Topics': '<h2>進階主題</h2>',
        }
        for seg in pm.data.segments:
            if '<h1' in seg.html or '<h2' in seg.html:
                # Match by original text content
                for orig_title, zh_title in heading_map.items():
                    if orig_title in seg.text:
                        pm.mark_translated(seg.segment_id, zh_title)
                        break
                else:
                    pm.mark_translated(seg.segment_id, seg.html)
            else:
                pm.mark_translated(seg.segment_id, f'翻譯_{seg.segment_id}')
        pm.save()

        builder = EPUBBuilder(project_dir)
        output = str(tmp_path / 'toc_output.epub')
        builder.build(output, fmt='chinese-only')

        book = epub.read_epub(output)
        toc_titles = [link.title for link in book.toc if isinstance(link, epub.Link)]
        assert '導論' in toc_titles
        assert '第一章：主要內容' in toc_titles
        assert '進階主題' in toc_titles

    def test_toc_untranslated_stays_original(self, tmp_path, sample_epub):
        """TOC titles stay as original when headings are not translated."""
        from src.epub.extractor import EPUBExtractor
        from src.core.segmenter import Segmenter

        project_dir = str(tmp_path / 'toc_orig_project')
        os.makedirs(project_dir, exist_ok=True)

        extractor = EPUBExtractor(sample_epub)
        extractor.load()
        metadata = extractor.get_metadata()
        chapters = extractor.extract_chapters()

        segmenter = Segmenter()
        all_segments = []
        for ch in chapters:
            segs = segmenter.segment_chapter(ch.id, ch.body)
            all_segments.extend(segs)

        pm = ProjectManager(project_dir)
        pm.create(
            epub_path=sample_epub,
            title=metadata['title'],
            author=metadata['author'],
            segments=all_segments,
        )

        # Only translate non-heading segments; leave headings untranslated
        for seg in pm.data.segments:
            if '<h1' not in seg.html and '<h2' not in seg.html:
                pm.mark_translated(seg.segment_id, f'翻譯_{seg.segment_id}')
        pm.save()

        builder = EPUBBuilder(project_dir)
        output = str(tmp_path / 'toc_orig_output.epub')
        builder.build(output, fmt='chinese-only')

        book = epub.read_epub(output)
        toc_titles = [link.title for link in book.toc if isinstance(link, epub.Link)]
        assert 'Introduction' in toc_titles
        assert 'Main Content' in toc_titles
        assert 'Advanced Topics' in toc_titles

    def test_toc_nested_structure(self, tmp_path):
        """Nested TOC entries are recursively translated."""
        # Create an EPUB with nested TOC
        book = epub.EpubBook()
        book.set_identifier('nested-toc-001')
        book.set_title('Nested TOC Book')
        book.set_language('en')
        book.add_author('Test Author')

        c1 = epub.EpubHtml(title='Part One', file_name='part01.xhtml', lang='en')
        c1.content = b'<html><body><h1>Part One</h1><p>Content of part one.</p></body></html>'

        c2 = epub.EpubHtml(title='Chapter A', file_name='chap_a.xhtml', lang='en')
        c2.content = b'<html><body><h2>Chapter A</h2><p>Content of chapter A.</p></body></html>'

        book.add_item(c1)
        book.add_item(c2)

        # Nested TOC: Part One contains Chapter A as child
        part_link = epub.Link('part01.xhtml', 'Part One', 'part1')
        child_link = epub.Link('chap_a.xhtml', 'Chapter A', 'chap_a')
        book.toc = [(part_link, [child_link])]

        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ['nav', c1, c2]

        epub_path = str(tmp_path / 'nested_toc.epub')
        epub.write_epub(epub_path, book)

        # Set up project
        from src.epub.extractor import EPUBExtractor
        from src.core.segmenter import Segmenter

        project_dir = str(tmp_path / 'nested_project')
        os.makedirs(project_dir, exist_ok=True)

        extractor = EPUBExtractor(epub_path)
        extractor.load()
        metadata = extractor.get_metadata()
        chapters = extractor.extract_chapters()

        segmenter = Segmenter()
        all_segments = []
        for ch in chapters:
            segs = segmenter.segment_chapter(ch.id, ch.body)
            all_segments.extend(segs)

        pm = ProjectManager(project_dir)
        pm.create(
            epub_path=epub_path,
            title=metadata['title'],
            author=metadata['author'],
            segments=all_segments,
        )

        # Translate all segments with Chinese headings
        for seg in pm.data.segments:
            if '<h1' in seg.html:
                pm.mark_translated(seg.segment_id, '<h1>第一部分</h1>')
            elif '<h2' in seg.html:
                pm.mark_translated(seg.segment_id, '<h2>章節 A</h2>')
            else:
                pm.mark_translated(seg.segment_id, f'翻譯_{seg.segment_id}')
        pm.save()

        builder = EPUBBuilder(project_dir)
        output = str(tmp_path / 'nested_output.epub')
        builder.build(output, fmt='chinese-only')

        book = epub.read_epub(output)
        # Nested TOC: first entry is a tuple (Link, [children])
        assert len(book.toc) >= 1
        entry = book.toc[0]
        if isinstance(entry, tuple):
            parent_link, children = entry
            assert parent_link.title == '第一部分'
            assert len(children) >= 1
            child = children[0]
            if isinstance(child, epub.Link):
                assert child.title == '章節 A'
        else:
            # If ebooklib flattened the TOC, check flat titles
            toc_titles = [l.title for l in book.toc if isinstance(l, epub.Link)]
            assert '第一部分' in toc_titles
            assert '章節 A' in toc_titles

    def test_toc_multi_heading_same_file(self, tmp_path):
        """Multiple TOC entries pointing to same file with fragments get correct titles."""
        book = epub.EpubBook()
        book.set_identifier('multi-heading-001')
        book.set_title('Multi Heading Book')
        book.set_language('en')
        book.add_author('Test Author')

        # Single file with multiple sections identified by div IDs
        c1 = epub.EpubHtml(title='Preface', file_name='preface.xhtml', lang='en')
        c1.content = (
            '<html><body>'
            '<div id="preface"><h1>Preface</h1>'
            '<p>Welcome to this book about testing.</p></div>'
            '<div id="who"><h1>Who This Book Is For</h1>'
            '<p>This book is for developers.</p></div>'
            '<div id="why"><h1>Why We Wrote This Book</h1>'
            '<p>We wrote this book because testing matters.</p></div>'
            '</body></html>'
        ).encode('utf-8')

        book.add_item(c1)

        # TOC with fragment links to same file
        parent = epub.Link('preface.xhtml#preface', 'Preface', 'preface')
        child1 = epub.Link('preface.xhtml#who', 'Who This Book Is For', 'who')
        child2 = epub.Link('preface.xhtml#why', 'Why We Wrote This Book', 'why')
        book.toc = [(parent, [child1, child2])]

        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ['nav', c1]

        epub_path = str(tmp_path / 'multi_heading.epub')
        epub.write_epub(epub_path, book)

        from src.epub.extractor import EPUBExtractor
        from src.core.segmenter import Segmenter

        project_dir = str(tmp_path / 'multi_heading_project')
        os.makedirs(project_dir, exist_ok=True)

        extractor = EPUBExtractor(epub_path)
        extractor.load()
        metadata = extractor.get_metadata()
        chapters = extractor.extract_chapters()

        segmenter = Segmenter()
        all_segments = []
        for ch in chapters:
            segs = segmenter.segment_chapter(ch.id, ch.body)
            all_segments.extend(segs)

        pm = ProjectManager(project_dir)
        pm.create(
            epub_path=epub_path,
            title=metadata['title'],
            author=metadata['author'],
            segments=all_segments,
        )

        # Translate headings with distinct Chinese titles
        heading_map = {
            'Preface': '<h1>前言</h1>',
            'Who This Book Is For': '<h1>本書適合誰閱讀</h1>',
            'Why We Wrote This Book': '<h1>我們為什麼寫這本書</h1>',
        }
        for seg in pm.data.segments:
            matched = False
            for orig, zh in heading_map.items():
                if orig in seg.text and '<h1' in seg.html:
                    pm.mark_translated(seg.segment_id, zh)
                    matched = True
                    break
            if not matched:
                pm.mark_translated(seg.segment_id, f'翻譯_{seg.segment_id}')
        pm.save()

        builder = EPUBBuilder(project_dir)
        output = str(tmp_path / 'multi_heading_output.epub')
        builder.build(output, fmt='chinese-only')

        result_book = epub.read_epub(output)

        # Collect all TOC titles (including nested)
        def collect_titles(toc):
            titles = []
            for entry in toc:
                if isinstance(entry, (epub.Link, epub.Section)):
                    titles.append(entry.title)
                elif isinstance(entry, tuple):
                    section, children = entry
                    if isinstance(section, (epub.Link, epub.Section)):
                        titles.append(section.title)
                    if isinstance(children, (list, tuple)):
                        titles.extend(collect_titles(children))
            return titles

        titles = collect_titles(result_book.toc)
        assert '前言' in titles
        assert '本書適合誰閱讀' in titles
        assert '我們為什麼寫這本書' in titles
        # Ensure they are NOT all the same
        unique_zh = [t for t in titles if t != '前言']
        assert len(unique_zh) >= 2, f"Sub-entries should have distinct titles, got: {titles}"


class TestContentDivAlignment:
    """Tests that content divs are correctly aligned between segmenter and builder."""

    def test_content_div_preserves_css_class_chinese_only(self, tmp_path):
        """Content div with CSS class is preserved in chinese-only output."""
        book = epub.EpubBook()
        book.set_identifier('content-div-001')
        book.set_title('Content Div Test')
        book.set_language('en')
        book.add_author('Test Author')

        c1 = epub.EpubHtml(title='Chapter 1', file_name='ch01.xhtml', lang='en')
        c1.content = (
            '<html><body>'
            '<h1>Chapter One</h1>'
            '<div class="lsl">• "No, no, you can\'t do that."</div>'
            '<div class="lsl">• "Yes, I can."</div>'
            '<p>Regular paragraph here.</p>'
            '</body></html>'
        ).encode('utf-8')

        book.add_item(c1)
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ['nav', c1]

        epub_path = str(tmp_path / 'content_div.epub')
        epub.write_epub(epub_path, book)

        from src.epub.extractor import EPUBExtractor
        from src.core.segmenter import Segmenter

        project_dir = str(tmp_path / 'cdiv_project')
        os.makedirs(project_dir, exist_ok=True)

        extractor = EPUBExtractor(epub_path)
        extractor.load()
        metadata = extractor.get_metadata()
        chapters = extractor.extract_chapters()

        segmenter = Segmenter()
        all_segments = []
        for ch in chapters:
            segs = segmenter.segment_chapter(ch.id, ch.body)
            all_segments.extend(segs)

        pm = ProjectManager(project_dir)
        pm.create(
            epub_path=epub_path,
            title=metadata['title'],
            author=metadata['author'],
            segments=all_segments,
        )

        # Translate each segment
        for seg in pm.data.segments:
            if '<h1' in seg.html:
                pm.mark_translated(seg.segment_id, '<h1>第一章</h1>')
            elif 'class="lsl"' in seg.html:
                pm.mark_translated(seg.segment_id, seg.html.replace('No, no', '不不不').replace('Yes, I can', '是的，我可以'))
            else:
                pm.mark_translated(seg.segment_id, f'<p>翻譯段落</p>')
        pm.save()

        builder = EPUBBuilder(project_dir)
        output = str(tmp_path / 'cdiv_output.epub')
        builder.build(output, fmt='chinese-only')

        soup = _get_chapter_html(output, 0)

        # Content divs with class="lsl" should be preserved
        lsl_divs = soup.find_all('div', class_='lsl')
        assert len(lsl_divs) >= 1, f"div.lsl should be preserved, got: {soup}"

    def test_container_div_recurse_heading_correct(self, tmp_path):
        """Container div recurses, and heading inside is correctly replaced."""
        book = epub.EpubBook()
        book.set_identifier('container-div-001')
        book.set_title('Container Div Test')
        book.set_language('en')
        book.add_author('Test Author')

        c1 = epub.EpubHtml(title='Preface', file_name='preface.xhtml', lang='en')
        c1.content = (
            '<html><body>'
            '<div id="preface"><h1>Preface</h1><p>Welcome to this book.</p></div>'
            '</body></html>'
        ).encode('utf-8')

        book.add_item(c1)
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ['nav', c1]

        epub_path = str(tmp_path / 'container_div.epub')
        epub.write_epub(epub_path, book)

        from src.epub.extractor import EPUBExtractor
        from src.core.segmenter import Segmenter

        project_dir = str(tmp_path / 'cntdiv_project')
        os.makedirs(project_dir, exist_ok=True)

        extractor = EPUBExtractor(epub_path)
        extractor.load()
        metadata = extractor.get_metadata()
        chapters = extractor.extract_chapters()

        segmenter = Segmenter()
        all_segments = []
        for ch in chapters:
            segs = segmenter.segment_chapter(ch.id, ch.body)
            all_segments.extend(segs)

        pm = ProjectManager(project_dir)
        pm.create(
            epub_path=epub_path,
            title=metadata['title'],
            author=metadata['author'],
            segments=all_segments,
        )

        for seg in pm.data.segments:
            if '<h1' in seg.html:
                pm.mark_translated(seg.segment_id, '<h1>前言</h1>')
            else:
                pm.mark_translated(seg.segment_id, '<p>歡迎閱讀本書。</p>')
        pm.save()

        builder = EPUBBuilder(project_dir)
        output = str(tmp_path / 'cntdiv_output.epub')
        builder.build(output, fmt='chinese-only')

        soup = _get_chapter_html(output, 0)

        h1 = soup.find('h1')
        assert h1 is not None, "h1 should exist after container div recurse"
        assert '前言' in h1.get_text()


class _FakeItem:
    """Minimal stand-in for an ebooklib document item."""

    def __init__(self, html: str):
        self.content = html.encode('utf-8')

    def get_content(self):
        return self.content

    def set_content(self, content):
        self.content = content


FORMATS = ('chinese-only', 'bilingual-horizontal', 'bilingual-vertical')


def _seg_state(segment_id, html, text, translated,
               segment_type=SegmentType.TEXT):
    from src.core.project import SegmentState
    return SegmentState(segment_id=segment_id, chapter_id='ch1', html=html,
                        text=text, word_count=len(text.split()),
                        segment_type=segment_type, translated_text=translated)


def _builder(tmp_path, segmenter_version):
    """A builder whose project data has the given segmenter_version
    (None: no project data loaded)."""
    from src.core.project import ProjectData
    builder = EPUBBuilder(str(tmp_path))
    if segmenter_version is not None:
        builder.pm.data = ProjectData(epub_path='', project_dir=str(tmp_path),
                                      segmenter_version=segmenter_version)
    return builder


def _transform(builder, chapter, segments, fmt):
    item = _FakeItem(chapter)
    builder._transform_chapter(item, segments, fmt)
    return item.get_content().decode('utf-8')


def _phase0_segments():
    """Per scenario of TestVersion2Export, the segments the Phase 0 extractor
    and segmenter stored (recorded from main before Phase 2 replaced them)."""
    path = os.path.join(os.path.dirname(__file__), 'data', 'phase0_segments.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _current_states(scenario, translations):
    """Segments as the Phase 0 extractor and segmenter stored them."""
    from src.core.project import SegmentState
    segs = _phase0_segments()[scenario]
    assert len(segs) == len(translations)
    return [SegmentState(**s, translated_text=t) for s, t in zip(segs, translations)]


def _pairs(soup):
    """(original text, translation text) per bilingual block."""
    return [(o.get_text(), t.get_text()) for o, t in
            zip(soup.find_all('div', class_='original'),
                soup.find_all('div', class_='translation'))]


def _words(prefix: str, n: int) -> str:
    return ' '.join(f'{prefix}{i}.' if i % 10 == 9 else f'{prefix}{i}'
                    for i in range(n))


class TestWalkByMarker:
    """The walk is chosen by the project's segmenter_version, nothing else."""

    def test_project_without_field_uses_legacy_walk(self, tmp_path, sample_epub):
        import json
        pm = ProjectManager(str(tmp_path))
        pm.create(sample_epub, 'T', 'A', [])
        with open(pm.project_file, encoding='utf-8') as f:
            raw = json.load(f)
        # Phase 2: create() writes schema 2; a pre-Phase-0 file has neither
        # schema nor segmenter_version
        del raw['schema'], raw['source_sha256']
        with open(pm.project_file, 'w', encoding='utf-8') as f:
            json.dump(raw, f)

        builder = EPUBBuilder(str(tmp_path))
        builder.pm.load()
        assert builder.pm.data.schema == 1
        assert builder._legacy_walk() is True

    def test_phase_0_project_uses_current_walk(self, tmp_path, sample_epub):
        # Phase 2: was "new project"; new projects are schema 2 now, so the
        # Phase 0 marker is written by hand
        pm = ProjectManager(str(tmp_path))
        pm.create(sample_epub, 'T', 'A', [])
        with open(pm.project_file, encoding='utf-8') as f:
            raw = json.load(f)
        del raw['schema'], raw['source_sha256']
        raw['segmenter_version'] = 2
        with open(pm.project_file, 'w', encoding='utf-8') as f:
            json.dump(raw, f)
        builder = EPUBBuilder(str(tmp_path))
        builder.pm.load()
        assert builder._legacy_walk() is False

    @pytest.mark.parametrize('version, legacy', [(None, True), (1, True), (2, False)])
    def test_walk_per_version(self, tmp_path, version, legacy):
        assert _builder(tmp_path, version)._legacy_walk() is legacy

    def test_walks_differ_only_in_comments(self, tmp_path):
        builder = _builder(tmp_path, None)
        body = BeautifulSoup('<body><!-- c --><p>One</p></body>', 'html.parser').body
        assert [e.name for e in builder._walk_content_elements(body, legacy=False)] == ['p']
        assert len(builder._walk_content_elements(body, legacy=True)) == 2


class TestVersion2Export:
    """Schema 1 projects segmented with comments skipped (segmenter_version 2,
    Phase 0) line up in every format, including the reviewers' scenarios.
    Phase 2: the segments are read from tests/data/phase0_segments.json, as
    the Phase 0 segmenter no longer exists; expectations unchanged."""

    @staticmethod
    def _export(tmp_path, chapter, states, fmt):
        html = _transform(_builder(tmp_path, 2), chapter, states, fmt)
        return BeautifulSoup(html, 'html.parser')

    CHAPTER = ('<html><body><h1>Title</h1><!-- note -->'
               '<p>Para one.</p><pre>x = 1</pre></body></html>')

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_comment_between_blocks(self, tmp_path, fmt):
        states = _current_states('CHAPTER', ['<h1>T0</h1>', '<p>T1</p>', 'T2'])
        soup = self._export(tmp_path, self.CHAPTER, states, fmt)

        if fmt == 'chinese-only':
            assert soup.find('h1').get_text() == 'T0'
            assert soup.find('p').get_text() == 'T1'
            assert soup.find('pre').get_text() == 'T2'
        else:
            assert _pairs(soup) == [('Title', 'T0'), ('Para one.', 'T1'), ('x = 1', 'T2')]

    # Round-2 reviewer A: a visible text equal to a comment's text.
    COMMENT_EQUALS_TEXT = '<html><body><!--note-->note<h1>Title</h1></body></html>'

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_comment_equal_to_visible_text(self, tmp_path, fmt):
        states = _current_states('COMMENT_EQUALS_TEXT', ['註', '<h1>標題</h1>'])
        soup = self._export(tmp_path, self.COMMENT_EQUALS_TEXT, states, fmt)

        assert [c.strip() for c in soup.find_all(string=lambda s: isinstance(s, Comment))] \
            == ['note']
        if fmt == 'chinese-only':
            assert soup.find('h1').get_text() == '標題'
            assert 'note' not in soup.get_text()
        else:
            assert _pairs(soup) == [('note', '註'), ('Title', '標題')]

    # Round-1 reviewer A: both walks have one element, but different ones.
    EQUAL_COUNT = ('<html><body><div><!-- note --><h2 id="s">Heading</h2></div>'
                   '</body></html>')

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_equal_count_nested_comment_keeps_heading(self, tmp_path, fmt):
        states = _current_states('EQUAL_COUNT', ['<h2 id="s">標題</h2>'])
        soup = self._export(tmp_path, self.EQUAL_COUNT, states, fmt)

        if fmt == 'chinese-only':
            h2 = soup.find('h2', id='s')
            assert h2 is not None and h2.get_text() == '標題'
        else:
            original = soup.find('div', class_='original')
            first_tag = next(c for c in original.children if isinstance(c, Tag))
            assert first_tag.name == 'h2' and first_tag.get('id') == 's'
            assert '標題' in soup.find('div', class_='translation').get_text()

    # Round-1 reviewer B, scenario A: body-level comment, bare text that is
    # sentence-split.
    SCENARIO_A = ('<html><body><!-- c --><p>' + _words('a', 350) + '</p><p>'
                  + _words('b', 50) + '</p>' + _words('x', 550) + '</body></html>')

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_scenario_a(self, tmp_path, fmt):
        states = _current_states('SCENARIO_A', ['<p>甲</p>', '<p>乙</p>丙', '丁'])
        soup = self._export(tmp_path, self.SCENARIO_A, states, fmt)

        comments = soup.find_all(string=lambda s: isinstance(s, Comment))
        assert [c.strip() for c in comments] == ['c']
        if fmt == 'chinese-only':
            assert [p.get_text() for p in soup.find_all('p')] == ['甲', '乙']
        else:
            assert 'a0' in soup.find('div', class_='original').get_text()
            assert soup.find('div', class_='translation').get_text() == '甲'

    # Round-1 reviewer B, scenario B: a wrapper div holding a comment.
    SCENARIO_B = ('<html><body><div class="chapter"><!-- c --><h1>Title</h1>'
                  '<p>Short para.</p></div>' + _words('x', 550) + '</body></html>')

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_scenario_b_wrapper_div_with_comment(self, tmp_path, fmt):
        n = len(_phase0_segments()['SCENARIO_B'])
        translations = ['<h1>標題</h1>'] + [f'<p>T{i}</p>' for i in range(1, n)]
        states = _current_states('SCENARIO_B', translations)
        soup = self._export(tmp_path, self.SCENARIO_B, states, fmt)

        assert soup.find('div', class_='chapter') is not None
        if fmt == 'chinese-only':
            assert soup.find('h1').get_text() == '標題'
            assert soup.find('div', class_='chapter').find('p') is not None
        else:
            pairs = _pairs(soup)
            assert pairs[0] == ('Title', '標題')
            assert pairs[1][0].startswith('Short para.') and pairs[1][1] == 'T1'


def _main_exports():
    path = os.path.join(os.path.dirname(__file__), 'data', 'legacy_walk_main.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


class TestVersion1ExportMatchesMain:
    """Projects created before the marker (segmenter_version 1) export exactly
    as on main.

    tests/data/legacy_walk_main.json holds, per scenario, a chapter, the
    segments main's extractor and segmenter stored for it (with made-up
    translations), and the output of main's builder for each format. The
    scenarios include the round-2 reviewers' cases: a comment that holds
    markup (old extractor stored it as a paragraph), a comment with an
    entity, and a comment equal to visible text.
    """

    CASES = [(name, fmt) for name in sorted(_main_exports()) for fmt in FORMATS]

    @pytest.mark.parametrize('version', [1, None])
    @pytest.mark.parametrize('name, fmt', CASES)
    def test_export_equals_main(self, tmp_path, name, fmt, version):
        from src.core.project import SegmentState
        case = _main_exports()[name]
        states = [SegmentState(**s) for s in case['segments']]
        html = _transform(_builder(tmp_path, version), case['chapter'], states, fmt)
        assert html == case['exports'][fmt]


# --- Phase 2: schema 2 projects are placed by block range ---------------------

def _schema2_states(chapter, translations):
    """Segments as the Phase 2 segmenter stores them, with translations."""
    from src.core.project import SegmentState
    from src.core.segmenter import Segmenter
    from tests.conftest import body_of
    segs = Segmenter().segment_chapter('ch1', body_of(chapter))
    assert len(segs) == len(translations)
    return [SegmentState(segment_id=s.id, chapter_id='ch1', html=s.html, text=s.text,
                         word_count=s.word_count, segment_type=s.segment_type,
                         translated_text=t, block_start=s.block_start,
                         block_end=s.block_end, fingerprints=s.fingerprints)
            for s, t in zip(segs, translations)]


def _schema2_builder(tmp_path):
    from src.core.project import ProjectData
    builder = EPUBBuilder(str(tmp_path))
    builder.pm.data = ProjectData(epub_path='', project_dir=str(tmp_path), schema=2)
    return builder


class TestSchema2Export:
    """The builder places schema 2 segments on their block ranges, in every
    format, and leaves a chapter whose blocks do not match untouched."""

    @staticmethod
    def _export(tmp_path, chapter, states, fmt):
        return BeautifulSoup(_transform(_schema2_builder(tmp_path), chapter, states, fmt),
                             'html.parser')

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_comment_between_blocks(self, tmp_path, fmt):
        chapter = TestVersion2Export.CHAPTER
        states = _schema2_states(chapter, ['<h1>T0</h1>', '<p>T1</p>', 'T2'])
        soup = self._export(tmp_path, chapter, states, fmt)
        if fmt == 'chinese-only':
            assert [soup.find(t).get_text() for t in ('h1', 'p', 'pre')] == ['T0', 'T1', 'T2']
        else:
            assert _pairs(soup) == [('Title', 'T0'), ('Para one.', 'T1'), ('x = 1', 'T2')]

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_comment_equal_to_visible_text(self, tmp_path, fmt):
        chapter = TestVersion2Export.COMMENT_EQUALS_TEXT
        states = _schema2_states(chapter, ['註', '<h1>標題</h1>'])
        soup = self._export(tmp_path, chapter, states, fmt)
        assert [c.strip() for c in soup.find_all(string=lambda s: isinstance(s, Comment))] \
            == ['note']
        if fmt == 'chinese-only':
            assert soup.find('h1').get_text() == '標題'
            assert 'note' not in soup.get_text()
        else:
            assert _pairs(soup) == [('note', '註'), ('Title', '標題')]

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_br_separated_runs(self, tmp_path, fmt):
        chapter = ('<html><body><div class="verse">Line one<br/>Line two</div>'
                   '<p>After.</p></body></html>')
        states = _schema2_states(chapter, ['<p>一</p><p>二</p><p>後</p>'])
        soup = self._export(tmp_path, chapter, states, fmt)
        assert soup.find('div', class_='verse').find('br') is not None
        if fmt == 'chinese-only':
            verse = soup.find('div', class_='verse')
            assert [str(c) for c in verse.children] == ['一', '<br/>', '二']
            assert soup.find('p').get_text() == '後'
        else:
            assert _pairs(soup) == [('Line one', '一'), ('Line two', '二'), ('After.', '後')]

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_text_mixed_with_blocks(self, tmp_path, fmt):
        chapter = ('<html><body><div class="box">Intro text<p>Inner para.</p></div>'
                   '<h2>Next</h2></body></html>')
        states = _schema2_states(chapter, ['<p>引</p><p>內</p>', '<h2>次</h2>'])
        soup = self._export(tmp_path, chapter, states, fmt)
        if fmt == 'chinese-only':
            box = soup.find('div', class_='box')
            assert box.contents[0] == '引' and box.find('p').get_text() == '內'
            assert soup.find('h2').get_text() == '次'
        else:
            assert _pairs(soup) == [('Intro text', '引'), ('Inner para.', '內'), ('Next', '次')]

    def test_untranslated_segment_left_alone(self, tmp_path):
        chapter = '<html><body><h1>Title</h1><p>Para.</p></body></html>'
        states = _schema2_states(chapter, ['', '<p>段</p>'])
        soup = self._export(tmp_path, chapter, states, 'chinese-only')
        assert soup.find('h1').get_text() == 'Title'
        assert soup.find('p').get_text() == '段'

    @pytest.mark.parametrize('change', ['fingerprint', 'range', 'source'])
    def test_mismatch_leaves_chapter_unchanged(self, tmp_path, change):
        chapter = '<html><body><h1>Title</h1><p>Para.</p></body></html>'
        states = _schema2_states(chapter, ['<h1>標題</h1>', '<p>段</p>'])
        if change == 'fingerprint':
            states[1].fingerprints = ['0' * 16]
        elif change == 'range':
            states[1].block_start, states[1].block_end = 2, 3
        else:
            chapter = chapter.replace('Para.', 'Changed para.')
        item = _FakeItem(chapter)
        _schema2_builder(tmp_path)._transform_chapter(item, states, 'chinese-only')
        assert item.get_content() == chapter.encode('utf-8')

    def test_raw_epub_end_to_end(self, tmp_path, raw_epub):
        from src.core.segmenter import Segmenter
        from src.epub.extractor import EPUBExtractor
        ex = EPUBExtractor(raw_epub)
        ex.load()
        segments = [s for ch in ex.extract_chapters()
                    for s in Segmenter().segment_chapter(ch.id, ch.body)]
        project_dir = str(tmp_path / 'p')
        pm = ProjectManager(project_dir)
        pm.create(raw_epub, 'T', 'A', segments)
        translations = {
            'one_seg0001': '<h1>守燈人</h1>',
            'one_seg0002': ('<p>米拉爬上螺旋梯。</p><p>正文下的一行。</p>'
                            '<p>歌一</p><p>歌二</p><p>歌三</p>'),
            'two_seg0001': '<h2>第二夜</h2>',
            'two_seg0002': '<p>港口鐘響兩次。</p>',
        }
        assert [s.segment_id for s in pm.data.segments] == list(translations)
        for seg_id, text in translations.items():
            pm.mark_translated(seg_id, text)
        pm.save()

        output = str(tmp_path / 'out.epub')
        EPUBBuilder(project_dir).build(output, fmt='chinese-only')
        book = epub.read_epub(output)
        one = BeautifulSoup(book.get_item_with_id('one').get_content().decode('utf-8'),
                            'html.parser')
        assert one.find('h1').get_text() == '守燈人'
        assert one.find('p', class_='first').get_text() == '米拉爬上螺旋梯。'
        verse = one.find('div', class_='verse')
        assert [c.get_text() if isinstance(c, Tag) else str(c) for c in verse.children] == \
            ['歌一', '', '歌二', '', '歌三']
        assert 'Mira' not in one.get_text() and 'song' not in one.get_text()
        assert '正文下的一行。' in one.get_text() and 'bare text' not in one.get_text()
        two = BeautifulSoup(book.get_item_with_id('two').get_content().decode('utf-8'),
                            'html.parser')
        assert two.find('h2').get_text() == '第二夜'

    def test_run_with_inline_element_placed(self, tmp_path):
        chapter = '<html><body>Bare <em>marked</em> words.<p>Para.</p></body></html>'
        states = _schema2_states(chapter, ['裸<em>標</em>字。\n<p>段</p>'])
        soup = self._export(tmp_path, chapter, states, 'chinese-only')
        assert soup.find('em').get_text() == '標'
        assert soup.find('p').get_text() == '段'
        assert 'Bare' not in soup.get_text()

    def test_content_div_placed_as_one_element(self, tmp_path):
        chapter = ('<html><body><div class="note"><em>Note:</em> keep it.</div>'
                   '</body></html>')
        states = _schema2_states(chapter, ['<div class="note">譯</div>'])
        soup = self._export(tmp_path, chapter, states, 'chinese-only')
        note = soup.find('div', class_='note')
        assert note.find('div') is None and note.get_text() == '譯'


class TestSchema2BridgeReview1:
    """Review round 1: a segment's whole node range is consumed once, a run
    is one unit, and a chapter is placed only when it matches what was
    stored as a whole."""

    @staticmethod
    def _export(tmp_path, chapter, states, fmt):
        return BeautifulSoup(_transform(_schema2_builder(tmp_path), chapter, states, fmt),
                             'html.parser')

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_image_in_a_run_kept_once(self, tmp_path, fmt):
        chapter = '<html><body><h1>T</h1>A<img src="x.png"/>B</body></html>'
        states = _schema2_states(chapter, ['<h1>標</h1>', '甲<img src="x.png"/>乙'])
        soup = self._export(tmp_path, chapter, states, fmt)
        if fmt == 'chinese-only':
            assert len(soup.find_all('img')) == 1
            assert 'A' not in soup.body.get_text() and '甲' in soup.body.get_text()
        else:
            original = soup.find_all('div', class_='original')[1]
            assert len(original.find_all('img')) == 1
            assert len(soup.find_all('img')) == 2   # one in the original, one in the translation
        assert soup.find('epub-translator-run') is None

    VERSE = '<html><body><div class="verse">L1<br/>L2<br/>L3</div></body></html>'

    def test_br_div_single_wrapper_translation_chinese_only(self, tmp_path):
        states = _schema2_states(self.VERSE, ['<div class="verse">一<br/>二<br/>三</div>'])
        soup = self._export(tmp_path, self.VERSE, states, 'chinese-only')
        verses = soup.find_all('div', class_='verse')
        assert len(verses) == 1
        assert [str(c) for c in verses[0].children] == ['一', '<br/>', '二', '<br/>', '三']

    @pytest.mark.parametrize('fmt', ['bilingual-horizontal', 'bilingual-vertical'])
    def test_br_div_single_wrapper_translation_bilingual(self, tmp_path, fmt):
        states = _schema2_states(self.VERSE, ['<div class="verse">一<br/>二<br/>三</div>'])
        soup = self._export(tmp_path, self.VERSE, states, fmt)
        original = soup.find('div', class_='original')
        translation = soup.find('div', class_='translation')
        assert [str(c) for c in original.find('div', class_='verse').children] == \
            ['L1', '<br/>', 'L2', '<br/>', 'L3']
        assert len(translation.find_all('br')) == 2
        assert len(soup.find_all('br')) == 4   # none left behind

    @pytest.mark.parametrize('fmt', ['bilingual-horizontal', 'bilingual-vertical'])
    def test_br_run_mismatch_keeps_brs_in_the_original(self, tmp_path, fmt):
        chapter = ('<html><body><div class="verse">L1<br/>L2<br/>L3</div>'
                   '<p>After.</p></body></html>')
        states = _schema2_states(chapter, ['<p>一二三</p><p>後</p>'])
        soup = self._export(tmp_path, chapter, states, fmt)
        original = soup.find('div', class_='original')
        assert len(original.find_all('br')) == 2
        assert len(soup.find_all('br')) == 2
        assert original.get_text() == 'L1L2L3After.'

    def test_br_run_mismatch_chinese_only_leaves_no_br(self, tmp_path):
        chapter = ('<html><body><div class="verse">L1<br/>L2<br/>L3</div>'
                   '<p>After.</p></body></html>')
        states = _schema2_states(chapter, ['<p>一二三</p><p>後</p>'])
        soup = self._export(tmp_path, chapter, states, 'chinese-only')
        assert soup.find('br') is None
        assert [p.get_text() for p in soup.find_all('p')] == ['一二三', '後']

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_br_div_one_to_one_still_placed_line_by_line(self, tmp_path, fmt):
        states = _schema2_states(self.VERSE, ['<p>一</p><p>二</p><p>三</p>'])
        soup = self._export(tmp_path, self.VERSE, states, fmt)
        if fmt == 'chinese-only':
            assert [str(c) for c in soup.find('div', class_='verse').children] == \
                ['一', '<br/>', '二', '<br/>', '三']
        else:
            assert _pairs(soup) == [('L1', '一'), ('L2', '二'), ('L3', '三')]

    def test_reordered_inline_tag(self, tmp_path):
        chapter = '<html><body>Read <em>carefully</em></body></html>'
        states = _schema2_states(chapter, ['<em>仔細</em>閱讀'])
        soup = self._export(tmp_path, chapter, states, 'chinese-only')
        assert soup.find('em').get_text() == '仔細'
        assert soup.body.get_text() == '仔細閱讀'

    @pytest.mark.parametrize('fmt', ['bilingual-horizontal', 'bilingual-vertical'])
    def test_run_and_paragraph_one_row_per_block(self, tmp_path, fmt):
        chapter = '<html><body>Bare <em>marked</em> words.<p>Para.</p></body></html>'
        states = _schema2_states(chapter, ['<p>裸標字。</p><p>段</p>'])
        soup = self._export(tmp_path, chapter, states, fmt)
        assert _pairs(soup) == [('Bare marked words.', '裸標字。'), ('Para.', '段')]
        assert soup.find('div', class_='original').find('em').get_text() == 'marked'

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_script_text_not_counted(self, tmp_path, fmt):
        chapter = '<html><body><script>run()</script><pre>run()</pre></body></html>'
        states = _schema2_states(chapter, ['run() # 譯'])
        soup = self._export(tmp_path, chapter, states, fmt)
        assert soup.find('script').get_text() == 'run()'
        if fmt == 'chinese-only':
            assert soup.find('pre').get_text() == 'run() # 譯'
        else:
            assert soup.find('div', class_='translation').get_text() == 'run() # 譯'
            assert soup.find('div', class_='original').find('pre') is not None
            assert soup.find('div', class_='original').find('script') is None

    CHAPTER = '<html><body><h1>Title</h1><p>Para.</p><p>More.</p></body></html>'

    def _unchanged(self, tmp_path, chapter, states):
        item = _FakeItem(chapter)
        _schema2_builder(tmp_path)._transform_chapter(item, states, 'chinese-only')
        return item.get_content() == chapter.encode('utf-8')

    def test_matching_chapter_is_placed(self, tmp_path):
        states = _schema2_states(self.CHAPTER, ['<h1>標</h1>', '<p>段</p><p>多</p>'])
        assert not self._unchanged(tmp_path, self.CHAPTER, states)

    def test_block_not_covered_by_any_segment(self, tmp_path):
        states = _schema2_states(self.CHAPTER, ['<h1>標</h1>', '<p>段</p><p>多</p>'])
        states[1].block_end, states[1].fingerprints = 2, states[1].fingerprints[:1]
        assert self._unchanged(tmp_path, self.CHAPTER, states)

    def test_extra_block_in_the_source(self, tmp_path):
        states = _schema2_states(self.CHAPTER, ['<h1>標</h1>', '<p>段</p><p>多</p>'])
        chapter = self.CHAPTER.replace('</body>', '<p>Added.</p></body>')
        assert self._unchanged(tmp_path, chapter, states)

    def test_overlapping_ranges(self, tmp_path):
        states = _schema2_states(self.CHAPTER, ['<h1>標</h1>', '<p>段</p><p>多</p>'])
        states[0].block_end = 2
        states[0].fingerprints = states[0].fingerprints + states[1].fingerprints[:1]
        assert self._unchanged(tmp_path, self.CHAPTER, states)

    def test_missing_segment(self, tmp_path):
        states = _schema2_states(self.CHAPTER, ['<h1>標</h1>', '<p>段</p><p>多</p>'])
        assert self._unchanged(tmp_path, self.CHAPTER, states[1:])

    @pytest.mark.parametrize('start, end, prints', [
        (None, None, 'none'), (-1, None, 'last'), (3, 3, 'none'), (2, 4, 'last'),
        (1, 1, 'none'), (True, 2, 'first'),
    ])
    def test_invalid_range(self, tmp_path, start, end, prints):
        states = _schema2_states(self.CHAPTER, ['<h1>標</h1>', '<p>段</p><p>多</p>'])
        fps = [fp for s in states for fp in s.fingerprints]
        states.append(copy.deepcopy(states[1]))
        states[-1].block_start, states[-1].block_end = start, end
        states[-1].fingerprints = {'last': fps[-1:], 'first': fps[1:2], 'none': []}[prints]
        assert self._unchanged(tmp_path, self.CHAPTER, states)

    def test_through_ebooklib_leading_body_text_stays_original(self, tmp_path):
        from src.core.segmenter import Segmenter
        from src.epub.extractor import EPUBExtractor
        from tests.conftest import RAW_NCX, write_raw_epub, xhtml
        items = [('ncx', 'toc.ncx', 'application/x-dtbncx+xml', None),
                 ('a', 'a.xhtml', 'application/xhtml+xml', None),
                 ('b', 'b.xhtml', 'application/xhtml+xml', None)]
        members = {'OEBPS/toc.ncx': RAW_NCX,
                   'OEBPS/a.xhtml': xhtml('Opening line<br/>Second line<p>Para one.</p>'),
                   'OEBPS/b.xhtml': xhtml('<h1>Next</h1><p>Clouds <em>gathered</em>.</p>'
                                          'Bare <em>tail</em> text.')}
        epub_path = write_raw_epub(tmp_path / 'lead.epub', items, ['a', 'b'], members)
        ex = EPUBExtractor(epub_path)
        ex.load()
        segments = [s for ch in ex.extract_chapters()
                    for s in Segmenter().segment_chapter(ch.id, ch.body)]
        project_dir = str(tmp_path / 'p')
        pm = ProjectManager(project_dir)
        pm.create(epub_path, 'T', 'A', segments)
        translations = {'a_seg0001': '<p>譯一</p><p>譯二</p><p>譯三</p>',
                        'b_seg0001': '<h1>次</h1>', 'b_seg0002': '<p>雲聚。</p>\n裸尾。'}
        assert [s.segment_id for s in pm.data.segments] == list(translations)
        for seg_id, text in translations.items():
            pm.mark_translated(seg_id, text)
        pm.save()
        output = str(tmp_path / 'out.epub')
        EPUBBuilder(project_dir).build(output, fmt='chinese-only')
        book = epub.read_epub(output)
        a = book.get_item_with_id('a').get_content().decode('utf-8')
        b = book.get_item_with_id('b').get_content().decode('utf-8')
        # ebooklib drops the text before the body's first child, so the
        # chapter no longer matches its blocks and stays in the original
        assert 'Second line' in a and 'Para one.' in a and '譯' not in a
        assert '次' in b and '雲聚。' in b and '裸尾。' in b
        assert 'Clouds' not in b and 'tail' not in b
