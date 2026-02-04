"""EPUB builder - reconstructs translated EPUB files with multiple output formats."""

from __future__ import annotations

import os
import copy

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

from src.core.project import ProjectManager, SegmentState

BILINGUAL_CSS = """
.bilingual-container {
    display: flex;
    gap: 1em;
    margin-bottom: 1em;
    border-bottom: 1px solid #eee;
    padding-bottom: 1em;
}
.bilingual-container .original {
    flex: 1;
    color: #666;
    font-size: 0.9em;
}
.bilingual-container .translation {
    flex: 1;
}
.bilingual-vertical .original {
    color: #666;
    font-size: 0.9em;
    margin-bottom: 0.5em;
}
.bilingual-vertical .translation {
    margin-bottom: 1em;
    border-bottom: 1px solid #eee;
    padding-bottom: 1em;
}
"""


class EPUBBuilder:
    """Builds translated EPUB files from project data."""

    FORMATS = ('chinese-only', 'bilingual-horizontal', 'bilingual-vertical')

    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self.pm = ProjectManager(project_dir)

    def build(self, output_path: str, fmt: str = 'chinese-only') -> str:
        """Build the translated EPUB.

        Args:
            output_path: Path for the output EPUB file.
            fmt: Output format - one of 'chinese-only', 'bilingual-horizontal', 'bilingual-vertical'.

        Returns:
            The output file path.
        """
        if fmt not in self.FORMATS:
            raise ValueError(f"Invalid format: {fmt}. Must be one of {self.FORMATS}")

        self.pm.load()
        source_epub_path = self.pm.data.epub_path

        source_book = epub.read_epub(source_epub_path)
        new_book = epub.EpubBook()

        # Copy metadata
        self._copy_metadata(source_book, new_book)

        # Copy non-document items (CSS, images, fonts)
        self._copy_resources(source_book, new_book)

        # Add bilingual CSS if needed
        if fmt != 'chinese-only':
            css_item = epub.EpubItem(
                uid='bilingual_css',
                file_name='style/bilingual.css',
                media_type='text/css',
                content=BILINGUAL_CSS.encode('utf-8'),
            )
            new_book.add_item(css_item)

        # Build translated chapters
        spine_items = []
        toc_items = []

        segments_by_chapter = self._group_segments_by_chapter()
        spine_ids = [item_id for item_id, _ in source_book.spine]

        for item_id in spine_ids:
            item = source_book.get_item_with_id(item_id)
            if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue

            chapter_segments = segments_by_chapter.get(item_id, [])

            if chapter_segments:
                new_html = self._build_chapter_html(
                    item, chapter_segments, fmt)
            else:
                # No translations for this chapter - keep original
                new_html = item.get_content().decode('utf-8')

            new_chapter = epub.EpubHtml(
                title=item.get_name(),
                file_name=item.get_name(),
                lang='zh-TW',
            )
            new_chapter.set_content(new_html.encode('utf-8'))

            if fmt != 'chinese-only':
                new_chapter.add_link(
                    href='style/bilingual.css', rel='stylesheet', type='text/css')

            new_book.add_item(new_chapter)
            spine_items.append(new_chapter)

            # Extract title for TOC
            soup = BeautifulSoup(new_html, 'lxml')
            heading = soup.find(['h1', 'h2', 'h3'])
            title = heading.get_text(strip=True) if heading else item.get_name()
            toc_items.append(
                epub.Link(item.get_name(), title, item_id))

        new_book.toc = toc_items
        new_book.add_item(epub.EpubNcx())
        new_book.add_item(epub.EpubNav())
        new_book.spine = ['nav'] + spine_items

        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        epub.write_epub(output_path, new_book)
        return output_path

    def _copy_metadata(self, source: epub.EpubBook, target: epub.EpubBook) -> None:
        """Copy metadata from source to target book."""
        title_meta = source.get_metadata('DC', 'title')
        title = title_meta[0][0] if title_meta else 'Translated Book'
        target.set_title(title)

        author_meta = source.get_metadata('DC', 'creator')
        if author_meta:
            target.add_author(author_meta[0][0])

        target.set_language('zh-TW')
        target.set_identifier(f'translated-{id(source)}')

    def _copy_resources(self, source: epub.EpubBook, target: epub.EpubBook) -> None:
        """Copy non-document items (images, CSS, fonts) from source."""
        for item in source.get_items():
            if item.get_type() in (ebooklib.ITEM_IMAGE, ebooklib.ITEM_STYLE,
                                   ebooklib.ITEM_FONT):
                target.add_item(item)

    def _group_segments_by_chapter(self) -> dict[str, list[SegmentState]]:
        """Group project segments by their chapter ID."""
        result: dict[str, list[SegmentState]] = {}
        for seg in self.pm.data.segments:
            chapter_id = seg.chapter_id
            if chapter_id not in result:
                result[chapter_id] = []
            result[chapter_id].append(seg)
        return result

    def _build_chapter_html(self, item, segments: list[SegmentState],
                            fmt: str) -> str:
        """Build the HTML content for a chapter with translated segments."""
        parts = []
        parts.append('<html><head><meta charset="utf-8"/></head><body>')

        for seg in segments:
            translated = seg.translated_text or seg.text
            original = seg.text

            if fmt == 'chinese-only':
                parts.append(self._wrap_content(translated, seg.segment_type))
            elif fmt == 'bilingual-horizontal':
                parts.append(
                    '<div class="bilingual-container">'
                    f'<div class="original">{self._wrap_content(original, seg.segment_type)}</div>'
                    f'<div class="translation">{self._wrap_content(translated, seg.segment_type)}</div>'
                    '</div>'
                )
            elif fmt == 'bilingual-vertical':
                parts.append(
                    '<div class="bilingual-vertical">'
                    f'<div class="original">{self._wrap_content(original, seg.segment_type)}</div>'
                    f'<div class="translation">{self._wrap_content(translated, seg.segment_type)}</div>'
                    '</div>'
                )

        parts.append('</body></html>')
        return '\n'.join(parts)

    @staticmethod
    def _wrap_content(text: str, segment_type: str) -> str:
        """Wrap text in appropriate HTML tags based on segment type."""
        if segment_type == 'heading':
            return f'<h2>{text}</h2>'
        elif segment_type == 'code':
            return f'<pre><code>{text}</code></pre>'
        elif segment_type == 'table':
            # Tables are already HTML
            if '<table' in text.lower():
                return text
            return f'<p>{text}</p>'
        else:
            return f'<p>{text}</p>'
