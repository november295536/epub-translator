"""EPUB builder - reconstructs translated EPUB files preserving original HTML format."""

from __future__ import annotations

import os
import copy

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, Tag, NavigableString
from bs4.element import PreformattedString

from src.core.project import ProjectManager, SegmentState
from src.core.segmenter import SegmentType, _is_container
from src.epub.blocks import BLOCK_TAGS, DESCEND_TAGS, NO_TEXT_TAGS, fingerprint, normalize

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
    """Builds translated EPUB files from project data, preserving original HTML formatting."""

    FORMATS = ('chinese-only', 'bilingual-horizontal', 'bilingual-vertical')

    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self.pm = ProjectManager(project_dir)

    def build(self, output_path: str, fmt: str = 'chinese-only') -> str:
        """Build the translated EPUB by modifying a deep copy of the source book.

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
        book = copy.deepcopy(source_book)
        self._fix_toc_uids(book.toc)

        segments_by_chapter = self._group_segments_by_chapter()

        # Add bilingual CSS if needed
        if fmt != 'chinese-only':
            css_item = epub.EpubItem(
                uid='bilingual_css',
                file_name='style/bilingual.css',
                media_type='text/css',
                content=BILINGUAL_CSS.encode('utf-8'),
            )
            book.add_item(css_item)

        # Transform each chapter in spine order
        for item_id, _ in book.spine:
            item = book.get_item_with_id(item_id)
            if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue

            chapter_segments = segments_by_chapter.get(item_id, [])
            if not chapter_segments:
                continue

            has_translation = any(s.translated_text for s in chapter_segments)
            if not has_translation:
                continue

            self._transform_chapter(item, chapter_segments, fmt)

        self._translate_toc(book, segments_by_chapter)

        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        epub.write_epub(output_path, book)
        return output_path

    def _group_segments_by_chapter(self) -> dict[str, list[SegmentState]]:
        """Group project segments by their chapter ID."""
        result: dict[str, list[SegmentState]] = {}
        for seg in self.pm.data.segments:
            chapter_id = seg.chapter_id
            if chapter_id not in result:
                result[chapter_id] = []
            result[chapter_id].append(seg)
        return result

    @staticmethod
    def _fix_toc_uids(toc: list, prefix: str = 'toc') -> None:
        """Fix None UIDs in TOC entries produced by read_epub + deepcopy."""
        for i, item in enumerate(toc):
            if isinstance(item, epub.Link):
                if item.uid is None:
                    item.uid = f'{prefix}_{i}'
            elif isinstance(item, tuple) and len(item) == 2:
                section, children = item
                if isinstance(section, epub.Link) and section.uid is None:
                    section.uid = f'{prefix}_{i}'
                if isinstance(children, (list, tuple)):
                    EPUBBuilder._fix_toc_uids(list(children), f'{prefix}_{i}')

    def _transform_chapter(self, item, segments: list[SegmentState], fmt: str) -> None:
        """Transform a chapter's HTML in place by matching segments to elements."""
        html = item.get_content().decode('utf-8')
        soup = BeautifulSoup(html, 'html.parser')
        body = soup.find('body')
        if not body:
            return

        if self.pm.data is not None and self.pm.data.schema == 2:
            placed = self._place_by_blocks(body, segments)
            if placed is None:
                return  # the chapter stays in the original language
        else:
            placed = [(seg, covered, covered, covered, True)
                      for seg, covered in self._place_by_count(body, segments)]

        for seg, covered, span, remove, pair in placed:
            if covered and seg.translated_text:
                if fmt == 'chinese-only':
                    self._apply_chinese_only(seg, covered, soup, remove, pair)
                elif fmt == 'bilingual-vertical':
                    self._apply_bilingual_vertical(seg, covered, soup, span, pair)
                elif fmt == 'bilingual-horizontal':
                    self._apply_bilingual_horizontal(seg, covered, soup, span, pair)

        item.set_content(str(soup).encode('utf-8'))

    def _place_by_count(self, body, segments: list[SegmentState]) -> list:
        """Schema 1: (segment, elements) pairs, each segment taking as many
        walked elements as its stored HTML has top-level nodes."""
        elements = self._walk_content_elements(body, legacy=self._legacy_walk())
        placed = []
        elem_idx = 0
        for seg in segments:  # past the last element a segment gets none
            end_idx = min(elem_idx + self._count_segment_blocks(seg), len(elements))
            placed.append((seg, elements[elem_idx:end_idx]))
            elem_idx = end_idx
        return placed

    @staticmethod
    def _place_by_blocks(body, segments: list[SegmentState]):
        """Schema 2: (segment, covered, span, remove, pair) per segment, or
        None when the chapter does not match what was stored: every
        segment's range must be valid, the ranges must cover the chapter's
        blocks exactly once, and every block's fingerprint must match.

        covered: the nodes with text of the segment's blocks. span: every
        node from its first block to its last (blocks without text
        included); bilingual output moves it into the original. remove: what
        a chinese-only replacement of the whole segment removes: the nodes
        of its blocks, and the <br> and whitespace between them; other
        blocks without text (images, anchors, <hr/>) stay. pair: the
        translation may be placed node by node, only when every block is a
        block element (as on main). A segment with an inline run whose
        translation is one node and whose blocks are the whole content of
        one element below <body> is placed on that element."""
        entries = []
        _soup_blocks(body, entries)
        blocks = [i for i, entry in enumerate(entries) if normalize(_nodes_text(entry))]
        ranges = []
        for seg in segments:
            start, end = seg.block_start, seg.block_end
            if not (type(start) is int and type(end) is int and 0 <= start < end <= len(blocks)):
                return None
            if [fingerprint(_nodes_text(entries[i])) for i in blocks[start:end]] \
                    != seg.fingerprints:
                return None
            ranges.append((start, end))
        ranges.sort()
        if [r[0] for r in ranges] != [0] + [r[1] for r in ranges[:-1]] \
                or ranges[-1][1] != len(blocks):
            return None

        placed = []
        for seg in segments:
            indexes = blocks[seg.block_start:seg.block_end]
            span, remove = [], []
            for i in range(indexes[0], indexes[-1] + 1):
                span.extend(entries[i])
                remove.extend(n for n in entries[i] if i in indexes or _is_separator(n))
            covered = [n for i in indexes for n in entries[i] if _nodes_text([n]).strip()]
            pair = all(len(entries[i]) == 1 and isinstance(entries[i][0], Tag)
                       and entries[i][0].name in BLOCK_TAGS for i in indexes)
            parent = span[0].parent
            in_span = {id(n) for n in span}
            if (not pair and seg.segment_type != SegmentType.CODE
                    and _top_level_count(seg.translated_text) == 1
                    and len(remove) == len(span) and parent is not body
                    and all(n.parent is parent for n in span)
                    and all(id(c) in in_span or isinstance(c, PreformattedString)
                            or not (isinstance(c, Tag) or c.strip())
                            for c in parent.children)):
                placed.append((seg, [parent], [parent], [parent], True))
            else:
                placed.append((seg, covered, span, remove, pair))
        return placed

    def _legacy_walk(self) -> bool:
        """True when chapters must be walked as the segmenter did before it
        skipped comments: projects with segmenter_version 1 (created before
        the field existed), and when no project data is loaded."""
        data = self.pm.data
        return data is None or data.segmenter_version < 2

    def _walk_content_elements(self, parent, legacy: bool) -> list:
        """Walk body elements in the same order as the segmenter, flattening div/section/article.

        With ``legacy=True`` comments are treated as text, as the segmenter
        did before it skipped them (segmenter_version 1).
        """
        elements = []
        for child in parent.children:
            if not legacy and isinstance(child, PreformattedString):
                continue
            if isinstance(child, NavigableString):
                if child.strip():
                    elements.append(child)
                continue
            if not isinstance(child, Tag):
                continue
            if child.name in ('div', 'section', 'article'):
                if _is_container(child, legacy):
                    elements.extend(self._walk_content_elements(child, legacy))
                else:
                    text = child.get_text(separator=' ', strip=True)
                    if text:
                        elements.append(child)
            else:
                text = child.get_text(separator=' ', strip=True)
                if text:
                    elements.append(child)
        return elements

    def _count_segment_blocks(self, seg: SegmentState) -> int:
        """Count how many body-level elements this segment corresponds to.

        For heading/code/table segments: always 1.
        For text segments: count top-level HTML tags in the segment's stored HTML.
        Falls back to 1 if no tags found (e.g. split segments with plain text).
        """
        if seg.segment_type == SegmentType.CODE:
            return 1

        frag = BeautifulSoup(seg.html, 'html.parser')
        block_count = sum(
            1 for c in frag.children
            if isinstance(c, Tag) or (isinstance(c, NavigableString) and c.strip())
        )
        return block_count if block_count > 0 else 1

    def _apply_chinese_only(self, seg: SegmentState, elements: list, soup,
                            remove: list, pair: bool) -> None:
        """Replace element content with translated text, preserving tag and attributes.

        `remove` is what a replacement of the whole segment removes (for
        schema 1: the elements); with `pair` false the elements are never
        paired 1:1 with the translation's nodes."""
        if not elements:
            return

        first = elements[0]
        rest = [n for n in remove if n is not first]
        translated = seg.translated_text

        if seg.segment_type == SegmentType.CODE:
            code_elem = first.find('code') if isinstance(first, Tag) else None
            if code_elem:
                code_elem.clear()
                code_elem.append(translated)
            else:
                if isinstance(first, NavigableString):
                    first.replace_with(NavigableString(translated))
                else:
                    first.clear()
                    first.append(translated)
            _remove(rest)
            return

        # Try HTML-aware replacement for text segments
        trans_frag = BeautifulSoup(translated, 'html.parser')
        trans_tags = [c for c in trans_frag.children
                      if isinstance(c, Tag) or (isinstance(c, NavigableString) and c.strip())]

        if pair and trans_tags and len(trans_tags) == len(elements):
            # HTML structure matches — replace content 1:1
            for orig_elem, trans_elem in zip(elements, trans_tags):
                if isinstance(orig_elem, NavigableString):
                    text = trans_elem.get_text() if isinstance(trans_elem, Tag) else str(trans_elem)
                    orig_elem.replace_with(NavigableString(text))
                else:
                    orig_elem.clear()
                    if isinstance(trans_elem, Tag):
                        for child in list(trans_elem.children):
                            orig_elem.append(child.extract())
                    else:
                        orig_elem.append(str(trans_elem))
            return

        if trans_tags:
            # Translation has HTML but count doesn't match — replace all with parsed HTML
            for child in list(trans_frag.children):
                first.insert_before(child.extract())
            _remove([first] + rest)
            return

        # Fallback: plain text replacement (backward compatible with old translations)
        if isinstance(first, NavigableString):
            first.replace_with(NavigableString(translated))
        else:
            first.clear()
            first.append(translated)

        # Remove remaining elements (they were merged into this segment)
        _remove(rest)

    def _apply_bilingual_vertical(self, seg: SegmentState, elements: list, soup,
                                  span: list, pair: bool) -> None:
        """Keep original elements, add translation block after them.

        `span` (for schema 1: the elements) is moved into the original as
        a whole when the translation is not paired 1:1 (never with `pair`
        false)."""
        if not elements:
            return

        # When translation has matching element count, pair them 1:1
        paired = self._try_pair_translations(seg, elements) if pair else None
        if paired is not None:
            for orig_elem, trans_elem in zip(elements, paired):
                wrapper = soup.new_tag('div', attrs={'class': 'bilingual-vertical'})
                orig_elem.insert_before(wrapper)

                original_div = soup.new_tag('div', attrs={'class': 'original'})
                original_div.append(orig_elem.extract())
                wrapper.append(original_div)

                trans_div = soup.new_tag('div', attrs={'class': 'translation'})
                if isinstance(trans_elem, Tag):
                    trans_div.append(trans_elem.extract())
                else:
                    p = soup.new_tag('p')
                    p.string = str(trans_elem).strip()
                    trans_div.append(p)
                wrapper.append(trans_div)
            return

        first = span[0]

        wrapper = soup.new_tag('div', attrs={'class': 'bilingual-vertical'})
        first.insert_before(wrapper)

        original_div = soup.new_tag('div', attrs={'class': 'original'})
        for elem in span:
            original_div.append(elem.extract())
        wrapper.append(original_div)

        trans_div = soup.new_tag('div', attrs={'class': 'translation'})
        self._fill_translation(trans_div, seg, elements, soup)
        wrapper.append(trans_div)

    def _apply_bilingual_horizontal(self, seg: SegmentState, elements: list, soup,
                                    span: list, pair: bool) -> None:
        """Wrap original + translation in a flex container (`span` and
        `pair` as for the vertical format)."""
        if not elements:
            return

        # When translation has matching element count, pair them 1:1
        paired = self._try_pair_translations(seg, elements) if pair else None
        if paired is not None:
            for orig_elem, trans_elem in zip(elements, paired):
                container = soup.new_tag('div', attrs={'class': 'bilingual-container'})
                orig_elem.insert_before(container)

                original_div = soup.new_tag('div', attrs={'class': 'original'})
                original_div.append(orig_elem.extract())
                container.append(original_div)

                trans_div = soup.new_tag('div', attrs={'class': 'translation'})
                if isinstance(trans_elem, Tag):
                    trans_div.append(trans_elem.extract())
                else:
                    p = soup.new_tag('p')
                    p.string = str(trans_elem).strip()
                    trans_div.append(p)
                container.append(trans_div)
            return

        first = span[0]

        container = soup.new_tag('div', attrs={'class': 'bilingual-container'})
        first.insert_before(container)

        original_div = soup.new_tag('div', attrs={'class': 'original'})
        for elem in span:
            original_div.append(elem.extract())
        container.append(original_div)

        trans_div = soup.new_tag('div', attrs={'class': 'translation'})
        self._fill_translation(trans_div, seg, elements, soup)
        container.append(trans_div)

    def _try_pair_translations(self, seg: SegmentState, elements: list):
        """Try to match translation elements 1:1 with original elements.

        Returns a list of translation elements if the count matches,
        or None to fall back to whole-segment wrapping.
        """
        if len(elements) <= 1 or seg.segment_type == SegmentType.CODE:
            return None

        trans_frag = BeautifulSoup(seg.translated_text, 'html.parser')
        trans_children = [c for c in trans_frag.children
                          if isinstance(c, Tag) or (isinstance(c, NavigableString) and c.strip())]

        if len(trans_children) == len(elements):
            return trans_children
        return None

    def _fill_translation(self, parent_div, seg: SegmentState,
                          original_elements: list, soup) -> None:
        """Fill a div with the translated content using appropriate HTML tags."""
        translated = seg.translated_text

        if seg.segment_type == SegmentType.CODE:
            pre = soup.new_tag('pre')
            code = soup.new_tag('code')
            code.string = translated
            pre.append(code)
            parent_div.append(pre)
            return

        # Try to parse translated text as HTML
        trans_frag = BeautifulSoup(translated, 'html.parser')
        trans_tags = [c for c in trans_frag.children if isinstance(c, Tag)]

        if trans_tags:
            # Translation contains HTML — use it directly
            for child in list(trans_frag.children):
                parent_div.append(child.extract())
            return

        # Fallback: wrap plain text in a <p> tag
        p = soup.new_tag('p')
        p.string = translated
        parent_div.append(p)

    def _translate_toc(self, book, segments_by_chapter: dict[str, list]) -> None:
        """Replace TOC entry titles with translated heading text."""
        import re

        # Build file_name → item_id mapping from spine
        name_to_id: dict[str, str] = {}
        for item_id, _ in book.spine:
            item = book.get_item_with_id(item_id)
            if item:
                name_to_id[item.get_name()] = item_id

        heading_re = re.compile(r'<h[1-6][\s>]', re.IGNORECASE)

        def update_toc_entries(toc_list: list) -> None:
            for entry in toc_list:
                if isinstance(entry, (epub.Link, epub.Section)):
                    self._update_link_title(entry, name_to_id, segments_by_chapter, heading_re)
                elif isinstance(entry, tuple) and len(entry) == 2:
                    section, children = entry
                    if isinstance(section, (epub.Link, epub.Section)):
                        self._update_link_title(section, name_to_id, segments_by_chapter, heading_re)
                    if isinstance(children, (list, tuple)):
                        update_toc_entries(list(children))

        update_toc_entries(book.toc)

    @staticmethod
    def _update_link_title(link, name_to_id, segments_by_chapter, heading_re) -> None:
        """Update a single TOC Link's title from translated heading segments."""
        href_parts = link.href.split('#', 1) if link.href else ['']
        file_name = href_parts[0]
        has_fragment = len(href_parts) > 1

        item_id = name_to_id.get(file_name)
        if not item_id:
            return

        chapter_segments = segments_by_chapter.get(item_id, [])
        headings = [s for s in chapter_segments if heading_re.search(s.html)]
        if not headings:
            return

        target = None
        if has_fragment and len(headings) > 1:
            # Multiple headings in same file — match by original TOC title
            toc_title = link.title.strip() if link.title else ''
            for seg in headings:
                if seg.text.strip() == toc_title:
                    target = seg
                    break
            # Fallback: containment match
            if not target:
                for seg in headings:
                    seg_text = seg.text.strip()
                    if toc_title in seg_text or seg_text in toc_title:
                        target = seg
                        break

        # Default: first heading with a translation
        if not target:
            target = headings[0]

        if target and target.translated_text:
            plain = BeautifulSoup(target.translated_text, 'html.parser').get_text()
            if plain.strip():
                link.title = plain.strip()



def _remove(nodes: list) -> None:
    for node in nodes:
        if isinstance(node, Tag):
            node.decompose()
        else:
            node.extract()


def _top_level_count(html: str) -> int:
    frag = BeautifulSoup(html, 'html.parser')
    return sum(1 for c in frag.children
               if isinstance(c, Tag) or (isinstance(c, NavigableString) and c.strip()))


def _is_separator(node) -> bool:
    """A <br> or a whitespace string."""
    if isinstance(node, Tag):
        return node.name == 'br'
    return not node.strip()


def _nodes_text(nodes: list) -> str:
    """Text as the XML walker counts it: no comments, processing
    instructions or CDATA, nothing inside script or style."""
    out = []
    for node in nodes:
        if isinstance(node, Tag):
            if node.name not in NO_TEXT_TAGS:
                out.append(_nodes_text(list(node.children)))
        elif not isinstance(node, PreformattedString):
            out.append(str(node))
    return ''.join(out)


def _soup_blocks(parent, out: list) -> None:
    """The block walk of src/epub/blocks.py on a BeautifulSoup tree: appends
    each block (a list of nodes) under `parent`, empty ones included, and
    each <br> as an entry of its own."""
    run: list = []
    for child in list(parent.children):
        if isinstance(child, PreformattedString):
            continue  # comment, processing instruction
        if isinstance(child, Tag) and child.name == 'br':
            out.append(run)
            out.append([child])
            run = []
        elif isinstance(child, Tag) and child.name in BLOCK_TAGS:
            out.append(run)
            run = []
            if child.name in DESCEND_TAGS and any(
                    isinstance(c, Tag) and (c.name in BLOCK_TAGS or c.name == 'br')
                    for c in child.children):
                _soup_blocks(child, out)
            else:
                out.append([child])
        else:
            run.append(child)
    out.append(run)
