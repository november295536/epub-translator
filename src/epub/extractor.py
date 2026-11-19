"""EPUB extraction module - reads the package straight from the archive.

The archive is read as a ZIP file: META-INF/container.xml names the OPF, the
OPF manifest maps item ids to members (hrefs URL-decoded, relative to the
OPF's directory), and the spine gives the reading order (duplicates
removed). The navigation document and the NCX are never chapters. Each
chapter is parsed with lxml in XML mode; a chapter that does not parse is
not returned and is listed in `EPUBExtractor.failed`.
"""

from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass
from urllib.parse import unquote

from lxml import etree

from src.epub.blocks import element_text, localname, walk_blocks

# Strict XML: no entity expansion, no network, no recovery
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)

XHTML_MEDIA_TYPE = 'application/xhtml+xml'


def parse_xml(data: bytes):
    """Parse a member as XML (raises etree.XMLSyntaxError)."""
    return etree.fromstring(data, PARSER)


def _xpath_local(node, *names: str) -> list:
    """Descendants of `node` along the given local names, any namespace."""
    path = '/'.join(f'*[local-name()="{n}"]' for n in names)
    return node.xpath(f'.//{path}')


@dataclass
class SpineItem:
    """One spine entry that is an XHTML content document."""
    id: str
    path: str          # member name inside the archive
    is_nav: bool


@dataclass
class Chapter:
    """Represents a single chapter extracted from an EPUB."""
    id: str
    title: str
    path: str          # member name inside the archive
    body: object       # lxml <body> element of the parsed chapter
    text_content: str
    order: int


def read_package(zf: zipfile.ZipFile) -> tuple:
    """Return (OPF root element, manifest, spine) of an open archive: the
    manifest maps item id -> (member name, media type, is navigation
    document); the spine lists its XHTML entries, duplicates removed."""
    container = parse_xml(zf.read('META-INF/container.xml'))
    opf_path = _xpath_local(container, 'rootfile')[0].get('full-path')
    opf = parse_xml(zf.read(opf_path))
    base = posixpath.dirname(opf_path)

    manifest = {}
    for item in _xpath_local(opf, 'manifest', 'item'):
        path = posixpath.normpath(posixpath.join(base, unquote(item.get('href'))))
        manifest[item.get('id')] = (path, item.get('media-type'),
                                    'nav' in (item.get('properties') or '').split())

    spine = []
    seen = set()
    for ref in _xpath_local(opf, 'spine', 'itemref'):
        item_id = ref.get('idref')
        if item_id in seen or item_id not in manifest:
            continue
        seen.add(item_id)
        path, media_type, is_nav = manifest[item_id]
        if media_type != XHTML_MEDIA_TYPE:
            continue
        spine.append(SpineItem(id=item_id, path=path, is_nav=is_nav))
    return opf, manifest, spine


def find_body(root):
    """The <body> element of a parsed chapter, or None."""
    bodies = _xpath_local(root, 'body')
    return bodies[0] if bodies else None


class EPUBExtractor:
    """Extracts chapters and content from EPUB files."""

    def __init__(self, epub_path: str):
        self.epub_path = epub_path
        self.opf = None
        self.spine: list[SpineItem] = []
        self.failed: list[tuple] = []     # (chapter id, reason) not segmented: set by extract_chapters
        self._zip: zipfile.ZipFile | None = None

    def load(self) -> None:
        """Open the archive and read its package document."""
        with open(self.epub_path, 'rb') as f:
            data = f.read()
        self._zip = zipfile.ZipFile(io.BytesIO(data))
        self.opf, _, self.spine = read_package(self._zip)

    def get_metadata(self) -> dict:
        """Extract book metadata."""
        if self.opf is None:
            raise RuntimeError("EPUB not loaded. Call load() first.")

        def first(name):
            found = _xpath_local(self.opf, 'metadata', name)
            text = found[0].text if found else None
            return text.strip() if text and text.strip() else 'Unknown'

        return {
            'title': first('title'),
            'author': first('creator'),
            'language': first('language'),
        }

    def extract_chapters(self) -> list[Chapter]:
        """Parse the spine's content documents, in spine order.

        The navigation document is skipped, as are chapters without a body or
        without text. A chapter that is missing from the archive or is not
        well-formed XML is skipped and recorded in `self.failed`.
        """
        if self._zip is None:
            raise RuntimeError("EPUB not loaded. Call load() first.")

        chapters = []
        self.failed = []
        for order, item in enumerate(self.spine):
            if item.is_nav:
                continue
            try:
                root = parse_xml(self._zip.read(item.path))
            except KeyError:
                self.failed.append((item.id, 'missing from the archive'))
                continue
            except etree.XMLSyntaxError as e:
                self.failed.append((item.id, f'not well-formed XML: {e}'))
                continue
            body = find_body(root)
            if body is None:
                continue
            text = ' '.join(' '.join(b.text.split()) for b in walk_blocks(body))
            if not text:
                continue
            chapters.append(Chapter(
                id=item.id,
                title=self._extract_title(body),
                path=item.path,
                body=body,
                text_content=text,
                order=order,
            ))
        return chapters

    @staticmethod
    def _extract_title(body) -> str:
        """Try to extract a chapter title from heading tags."""
        for tag in ('h1', 'h2', 'h3'):
            for el in body.iter():
                if localname(el) == tag:
                    title = ' '.join(element_text(el).split())
                    if title:
                        return title
        return 'Untitled'
