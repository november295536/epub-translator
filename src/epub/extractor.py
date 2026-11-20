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
import zlib
from dataclasses import dataclass
from urllib.parse import unquote

from lxml import etree

from src.epub.blocks import element_text, localname, walk_blocks

# Strict XML: no entity expansion, no network, no recovery
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)

XHTML_MEDIA_TYPE = 'application/xhtml+xml'


class EPUBError(Exception):
    """The archive or its package document cannot be read."""


def parse_xml(data: bytes):
    """Parse a member as XML (raises etree.XMLSyntaxError)."""
    return etree.fromstring(data, PARSER)


def open_archive(path: str) -> zipfile.ZipFile:
    """The EPUB at `path` as an in-memory ZIP archive (raises EPUBError)."""
    try:
        with open(path, 'rb') as f:
            return zipfile.ZipFile(io.BytesIO(f.read()))
    except (OSError, zipfile.BadZipFile) as e:
        raise EPUBError(f'無法讀取 EPUB：{e}') from e


def read_member(zf: zipfile.ZipFile, name: str) -> bytes:
    """A member's bytes. KeyError when it is missing; EPUBError when it
    cannot be read (encrypted, unsupported compression, corrupt)."""
    try:
        return zf.read(name)
    except KeyError:
        raise
    except (RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error, EOFError) as e:
        raise EPUBError(f'無法讀取 {name}：{e}') from e


def unresolved_entities(root) -> list:
    """Names of the entity references left unexpanded under `root` (entities
    are never expanded and no DTD is loaded, so `&mdash;` under an XHTML
    DOCTYPE stays a reference whose text is unknown)."""
    return sorted({node.name for node in root.iter(etree.Entity)})


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
    media_type: str


@dataclass
class Chapter:
    """Represents a single chapter extracted from an EPUB."""
    id: str
    title: str
    path: str          # member name inside the archive
    body: object       # lxml <body> element of the parsed chapter
    text_content: str
    order: int


def package_path(zf: zipfile.ZipFile) -> str:
    """Member name of the OPF, from META-INF/container.xml (raises EPUBError)."""
    try:
        container = parse_xml(read_member(zf, 'META-INF/container.xml'))
    except KeyError as e:
        raise EPUBError(f'EPUB 缺少檔案：{e}') from e
    except etree.XMLSyntaxError as e:
        raise EPUBError(f'EPUB 的套件文件不是格式正確的 XML：{e}') from e
    rootfiles = _xpath_local(container, 'rootfile')
    if not rootfiles or not rootfiles[0].get('full-path'):
        raise EPUBError('container.xml 沒有指出 OPF（rootfile）')
    return unquote(rootfiles[0].get('full-path'))


def read_package(zf: zipfile.ZipFile) -> tuple:
    """Return (OPF root element, manifest, spine) of an open archive: the
    manifest maps item id -> (member name, media type, is navigation
    document); the spine lists its entries, duplicates removed, with their
    media types. Raises EPUBError when the package cannot be read."""
    opf_path = package_path(zf)
    try:
        opf = parse_xml(read_member(zf, opf_path))
    except KeyError as e:
        raise EPUBError(f'EPUB 缺少檔案：{e}') from e
    except etree.XMLSyntaxError as e:
        raise EPUBError(f'EPUB 的套件文件不是格式正確的 XML：{e}') from e
    base = posixpath.dirname(opf_path)

    manifest = {}
    for item in _xpath_local(opf, 'manifest', 'item'):
        if item.get('href') is None:
            raise EPUBError(f'manifest 項目 {item.get("id")} 沒有 href')
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
        spine.append(SpineItem(id=item_id, path=path, is_nav=is_nav, media_type=media_type))
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
        self.spine: list[SpineItem] = []     # XHTML entries
        self.not_xhtml: list[SpineItem] = []
        self.failed: list[tuple] = []     # (chapter id, reason) not segmented: set by extract_chapters
        self._zip: zipfile.ZipFile | None = None

    def load(self) -> None:
        """Open the archive and read its package document (raises EPUBError)."""
        self._zip = open_archive(self.epub_path)
        self.opf, _, spine = read_package(self._zip)
        self.spine = [s for s in spine if s.media_type == XHTML_MEDIA_TYPE]
        self.not_xhtml = [s for s in spine if s.media_type != XHTML_MEDIA_TYPE]

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
        without text. A spine entry that is not XHTML, and a chapter that is
        missing from the archive, unreadable, not well-formed XML or holds
        an unresolved entity reference, is skipped and recorded in
        `self.failed`.
        """
        if self._zip is None:
            raise RuntimeError("EPUB not loaded. Call load() first.")

        chapters = []
        self.failed = [(item.id, f'media type {item.media_type} is not XHTML')
                       for item in self.not_xhtml]
        for order, item in enumerate(self.spine):
            if item.is_nav:
                continue
            try:
                root = parse_xml(read_member(self._zip, item.path))
            except KeyError:
                self.failed.append((item.id, 'missing from the archive'))
                continue
            except EPUBError as e:
                self.failed.append((item.id, f'unreadable: {e}'))
                continue
            except etree.XMLSyntaxError as e:
                self.failed.append((item.id, f'not well-formed XML: {e}'))
                continue
            body = find_body(root)
            if body is None:
                continue
            entities = unresolved_entities(body)
            if entities:
                self.failed.append((item.id, 'unresolved entity reference: '
                                    + ', '.join(f'&{n};' for n in entities)))
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
