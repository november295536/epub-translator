"""ZIP export (plan 4.5): a new EPUB written from the project's own source archive.

The source archive's hash is checked first. `mimetype` is written first and
stored; every other member is written once, in the source's order. Only
these members may differ from the source: chapters holding a translation
that passed the checks below, the OPF, the NCX, the navigation document and,
for the bilingual formats, one added stylesheet. Every other member is
copied with identical uncompressed bytes.

Per chapter the blocks (src/epub/blocks.py) must match the stored segments:
every range valid, the ranges covering all blocks exactly once, every
fingerprint equal. Otherwise all of its segments stay in the original
language. Per segment, until Phase 4 brings keyed validation, a reduced
structural check decides (see `_pieces`); a segment that fails it stays in
the original language. The translation is filled into the source elements
on the parsed tree: a block element keeps its own tag and attributes and
gets the translation's content; a run of inline content is replaced by the
translation's inline content. The chapter head is not touched (a bilingual
chapter gets one more <link>).

The report lists, per chapter with translated segments, how many were
exported as translation and which stayed in the original language, and why.
"""

from __future__ import annotations

import copy
import os
import posixpath
import re
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urlsplit
from xml.sax.saxutils import quoteattr

from lxml import etree

from src.core.project import (
    ProjectLock, ProjectLockedError, ProjectManager, SegmentStatus, sha256_file,
)
from src.core.segmenter import SegmentType, _code_text
from src.epub.blocks import BLOCK_TAGS, NO_TEXT_TAGS, element_text, fingerprint, localname, walk_blocks
from src.epub.extractor import (
    XHTML_MEDIA_TYPE, EPUBError, find_body, open_archive, package_path, parse_xml,
    read_member, read_package, unresolved_entities,
)

FORMATS = ('chinese-only', 'bilingual-horizontal', 'bilingual-vertical')
TARGET_LANG = 'zh-TW'
XML_LANG = '{http://www.w3.org/XML/1998/namespace}lang'
EPUB_TYPE = '{http://www.idpf.org/2007/ops}type'
NOTE_TYPES = frozenset({'footnote', 'endnote', 'rearnote', 'note'})
NCX_MEDIA_TYPE = 'application/x-dtbncx+xml'
HEADINGS = frozenset({'h1', 'h2', 'h3', 'h4', 'h5', 'h6'})
WRAPPER_CLASS = {'bilingual-horizontal': 'bilingual-container',
                 'bilingual-vertical': 'bilingual-vertical'}

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

# Why a translated segment was exported in the original language
CHAPTER_UNREADABLE = 'chapter_unreadable'  # chapter not in the spine, missing or not parsed
CHAPTER_MISMATCH = 'chapter_mismatch'      # blocks differ from the stored ranges/fingerprints
NOT_WELL_FORMED = 'not_well_formed'        # the translation is not well-formed XML
STRUCTURE_MISMATCH = 'structure_mismatch'  # its top-level structure differs from the blocks
AMBIGUOUS_SOURCE = 'ambiguous_source'      # the mapping rule cannot split the source itself
CODE_MARKUP = 'code_markup'                # the code element holds markup (e.g. highlighting)
ANCHOR_MISMATCH = 'anchor_mismatch'        # the ids (and a@name) inside a block differ in its translation
CHAPTER_DOCTYPE = 'chapter_doctype'        # the chapter's DOCTYPE has an internal subset

REASON_TEXT = {
    CHAPTER_UNREADABLE: '章節無法讀取',
    CHAPTER_MISMATCH: '章節內容與分段紀錄不符',
    NOT_WELL_FORMED: '譯文不是格式正確的 XML',
    STRUCTURE_MISMATCH: '譯文的頂層結構與原文區塊不符',
    AMBIGUOUS_SOURCE: '原文區塊無法明確對應',
    CODE_MARKUP: '程式碼區塊含標記',
    ANCHOR_MISMATCH: '譯文的錨點（id 或 a 的 name）與原文區塊不同',
    CHAPTER_DOCTYPE: '章節的 DOCTYPE 含內部子集，無法原樣寫回',
}

SCHEMA_1_MESSAGE = ('此專案是舊格式（schema 1），無法匯出。'
                    '請先執行 python3 translate.py migrate <專案目錄> 再匯出。')


class ExportError(Exception):
    """The project cannot be exported (nothing was written)."""


@dataclass
class ChapterReport:
    chapter_id: str
    translated: int = 0                          # segments exported as translation
    kept: list = field(default_factory=list)     # (segment id, reason) kept original


@dataclass
class ExportReport:
    output_path: str
    untranslated: int                              # segments without an accepted translation
    chapters: list = field(default_factory=list)  # ChapterReport per chapter with translations

    @property
    def translated(self) -> int:
        return sum(c.translated for c in self.chapters)

    def reasons(self) -> Counter:
        return Counter(reason for c in self.chapters for _, reason in c.kept)

    def summary(self) -> dict:
        reasons = self.reasons()
        return {'translated': self.translated, 'kept': sum(reasons.values()),
                'untranslated': self.untranslated, 'reasons': dict(sorted(reasons.items()))}

    def lines(self) -> list[str]:
        """The report as text lines (the CLI prints them, the web shows them)."""
        s = self.summary()
        out = [f"以譯文匯出 {s['translated']} 段，保留原文 {s['kept']} 段"
               f"（另有 {s['untranslated']} 段未翻譯或翻譯失敗，以原文匯出）"]
        out += [f'  {REASON_TEXT[r]}（{r}）：{n}' for r, n in s['reasons'].items()]
        for c in self.chapters:
            if c.kept:
                out.append(f'  章節 {c.chapter_id}：譯文 {c.translated}，原文 {len(c.kept)}'
                           f'（{", ".join(sorted({r for _, r in c.kept}))}）')
        return out


class _Kept(Exception):
    """A segment stays in the original language."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def accepted(seg) -> bool:
    """A segment whose translation is exported (when it passes the checks)."""
    return (seg.status in (SegmentStatus.TRANSLATED, SegmentStatus.REVIEWED)
            and bool(seg.translated_text.strip()))


def export_project(project_dir: str, output_path: str, fmt: str) -> ExportReport:
    """Export the project as an EPUB at `output_path` (written atomically).

    Raises ValueError for an unknown format and ExportError when the project
    cannot be exported (schema 1, project locked by another process,
    source missing or changed, package unreadable, write failure). Holds
    the project lock while it reads the project."""
    if fmt not in FORMATS:
        raise ValueError(f"Invalid format: {fmt}. Must be one of {FORMATS}")
    try:
        with ProjectLock(project_dir):
            return _export(project_dir, output_path, fmt)
    except ProjectLockedError as e:
        raise ExportError(str(e)) from e


def _export(project_dir: str, output_path: str, fmt: str) -> ExportReport:
    data = ProjectManager(project_dir).load()
    if data.schema != 2:
        raise ExportError(SCHEMA_1_MESSAGE)
    try:
        digest = sha256_file(data.epub_path)
    except OSError as e:
        raise ExportError(f'無法讀取來源 EPUB：{e}') from e
    if not data.source_sha256 or digest != data.source_sha256:
        raise ExportError('來源 EPUB 與專案記錄的雜湊值不符，無法匯出')
    if os.path.exists(output_path) and os.path.samefile(output_path, data.epub_path):
        raise ExportError('輸出檔不能是來源 EPUB 本身')
    try:
        zf = open_archive(data.epub_path)
        opf_path = package_path(zf)
        opf, manifest, spine = read_package(zf)
        opf_subset = has_internal_subset(read_member(zf, opf_path))
    except EPUBError as e:
        raise ExportError(str(e)) from e

    epub3 = str(opf.get('version')).startswith('3')
    languages = [e for e in opf.iter() if localname(e) == 'language']
    src_lang = (languages[0].text or '').strip() if languages else ''
    names = {info.filename for info in zf.infolist()}
    css_path = None
    if fmt != 'chinese-only':
        css_path = _free_name(names, posixpath.join(posixpath.dirname(opf_path), 'bilingual.css'))

    by_chapter: dict = {}
    for seg in data.segments:
        by_chapter.setdefault(seg.chapter_id, []).append(seg)
    chapters = chapter_items(spine)
    report = ExportReport(output_path, sum(not accepted(s) for s in data.segments))
    replaced: dict = {}   # member -> new bytes
    labels: dict = {}     # chapter member -> {fragment or None: TOC label}
    for chapter_id, segments in by_chapter.items():
        todo = [s for s in segments if accepted(s)]
        if not todo:
            continue
        chapter = ChapterReport(chapter_id)
        report.chapters.append(chapter)
        item = chapters.get(chapter_id)
        if item is None:
            chapter.kept = [(s.segment_id, CHAPTER_UNREADABLE) for s in todo]
            continue
        out = _export_chapter(zf, item.path, segments, todo, fmt, src_lang, epub3,
                              css_path, chapter)
        if out is not None:
            replaced[item.path], labels[item.path] = out

    css_used = bool(css_path) and bool(replaced)
    for path, media_type, is_nav in manifest.values():
        if is_nav or media_type == NCX_MEDIA_TYPE:  # never a chapter; read errors leave it
            toc = _relabel(zf, path, labels, ncx=not is_nav)
            if toc is not None:
                replaced[path] = toc
    if opf_subset and css_used:  # the stylesheet must be registered in the OPF
        raise ExportError('OPF 的 DOCTYPE 含內部子集，無法登錄雙語樣式表')
    if replaced and not opf_subset:  # else dc:language stays as it is
        replaced[opf_path] = _package(opf, fmt, css_path if css_used else None, opf_path)

    try:
        _write_archive(zf, output_path, replaced, css_path if css_used else None)
    except (EPUBError, OSError) as e:
        raise ExportError(f'無法寫出 EPUB：{e}') from e
    return report


def _free_name(names: set, wanted: str) -> str:
    """`wanted`, or `<stem>-N<ext>` when a member of that name exists
    (compared without case, as EPUB requires)."""
    taken = {n.lower() for n in names}
    stem, ext = posixpath.splitext(wanted)
    candidate, n = wanted, 1
    while candidate.lower() in taken:
        n += 1
        candidate = f'{stem}-{n}{ext}'
    return candidate


_INTERNAL_SUBSET = re.compile(rb'<!DOCTYPE[^>\[]*\[')


def has_internal_subset(data: bytes) -> bool:
    """True when the document's DOCTYPE has an internal subset, which
    `_serialize` cannot write back."""
    return _INTERNAL_SUBSET.search(data) is not None


def _serialize(root) -> bytes:
    """A parsed document (without internal DTD subset) as bytes: XML
    declaration, DOCTYPE, the comments and processing instructions before
    the root, the root element serialized in a document of its own
    (serializing the parsed document itself makes libxml2 add a <meta> to
    XHTML 1.0 documents), then those after the root."""
    doctype = root.getroottree().docinfo.doctype
    out = ['<?xml version="1.0" encoding="utf-8"?>\n']
    if doctype:
        out.append(doctype + '\n')
    for node in reversed(list(root.itersiblings(preceding=True))):
        out.append(etree.tostring(node, encoding='unicode', with_tail=False) + '\n')
    out.append(etree.tostring(copy.deepcopy(root), encoding='unicode'))
    for node in root.itersiblings():
        out.append('\n' + etree.tostring(node, encoding='unicode', with_tail=False))
    return ''.join(out).encode('utf-8')


class _ZipInfo(zipfile.ZipInfo):
    """A ZipInfo that writes the member name as the given raw bytes, with
    the source's UTF-8 flag (zipfile resets flag_bits when writing)."""
    __slots__ = ('raw_name', 'name_flag')

    def _encodeFilenameFlags(self):
        return self.raw_name, self.flag_bits | self.name_flag


def _member_info(info, compress_type: int):
    """A ZipInfo for writing `info` again: name bytes (UTF-8 when the source
    flags it, else code page 437, as zipfile decoded it), date, attributes,
    system, versions, extra field and comment of the source."""
    out = _ZipInfo(info.filename, date_time=info.date_time)
    try:
        out.raw_name = info.filename.encode('utf-8' if info.flag_bits & 0x800 else 'cp437')
    except UnicodeEncodeError as e:
        raise ExportError(f'無法原樣寫回成員名稱：{info.filename!r}') from e
    out.name_flag = info.flag_bits & 0x800
    out.compress_type = compress_type
    for attr in ('external_attr', 'internal_attr', 'create_system', 'create_version',
                 'extract_version', 'extra', 'comment'):
        setattr(out, attr, getattr(info, attr))
    return out


def _write_archive(zf, output_path: str, replaced: dict, css_path) -> None:
    """Write the archive (see the module docstring). A name that occurs more
    than once is written once, at its first position, with the last
    entry's data and metadata (the entry the chapter reader uses)."""
    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix='.export-', suffix='.tmp')
    umask = os.umask(0)
    os.umask(umask)
    try:
        os.fchmod(fd, 0o666 & ~umask)
        last = {info.filename: info for info in zf.infolist()}
        with os.fdopen(fd, 'wb') as f, zipfile.ZipFile(f, 'w') as out:
            if 'mimetype' in last:
                zi = _member_info(last['mimetype'], zipfile.ZIP_STORED)
                zi.extra = b''  # OCF: no extra field in the mimetype header
                out.writestr(zi, read_member(zf, last['mimetype']))
            else:
                out.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip',
                             compress_type=zipfile.ZIP_STORED)
            written = {'mimetype'}
            for name in [info.filename for info in zf.infolist()]:
                if name in written:
                    continue
                written.add(name)
                info = last[name]
                if name in replaced:
                    out.writestr(_member_info(info, zipfile.ZIP_DEFLATED), replaced[name])
                else:
                    compress = (info.compress_type if info.compress_type
                                in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                                else zipfile.ZIP_DEFLATED)
                    out.writestr(_member_info(info, compress), read_member(zf, info))
            if css_path:
                out.writestr(css_path, BILINGUAL_CSS, compress_type=zipfile.ZIP_DEFLATED)
        os.replace(tmp, output_path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# --- Package and TOC ----------------------------------------------------------

def _package(opf, fmt: str, css_path, opf_path: str) -> bytes:
    """The OPF: dc:language set for chinese-only; the bilingual stylesheet
    registered in the manifest."""
    if fmt == 'chinese-only':
        for el in opf.iter():
            if localname(el) == 'language':
                el.text = TARGET_LANG
                break
    if css_path:
        manifest = next(el for el in opf.iter() if localname(el) == 'manifest')
        ids = {el.get('id') for el in opf.iter() if el.get('id') is not None}
        item_id, n = 'bilingual-css', 1
        while item_id in ids:
            n += 1
            item_id = f'bilingual-css-{n}'
        href = quote(posixpath.relpath(css_path, posixpath.dirname(opf_path) or '.'))
        item = etree.SubElement(manifest, etree.QName(manifest, 'item'))
        item.set('id', item_id)
        item.set('href', href)
        item.set('media-type', 'text/css')
    return _serialize(opf)


def _relabel(zf, path: str, labels: dict, ncx: bool):
    """The NCX (`ncx`) or navigation document with the labels of its table
    of contents taken from the translated headings, or None when no label
    changed or the file cannot be read or written back. Only the NCX
    navMap and the nav whose epub:type holds `toc` are relabelled; page
    lists, landmarks and other lists keep their labels. A label that holds
    an element with an id is left as it is."""
    try:
        data = read_member(zf, path)
        root = parse_xml(data)
    except (KeyError, EPUBError, etree.XMLSyntaxError):
        return None
    if has_internal_subset(data):
        return None
    entries = []  # (element holding the label, href)
    if ncx:
        for nav_map in (c for c in root if localname(c) == 'navMap'):
            for point in nav_map.iter():
                if localname(point) != 'navPoint':
                    continue
                content = next((c for c in point if localname(c) == 'content'), None)
                nav_label = next((c for c in point if localname(c) == 'navLabel'), None)
                text = None if nav_label is None else next(
                    (c for c in nav_label if localname(c) == 'text'), None)
                if content is not None and text is not None:
                    entries.append((text, content.get('src')))
    else:
        for nav in root.iter():
            if localname(nav) == 'nav' and 'toc' in (nav.get(EPUB_TYPE) or '').split():
                entries += [(a, a.get('href')) for a in nav.iter() if localname(a) == 'a']
    changed = 0
    for el, href in entries:
        label = _label_for(path, href, labels)
        if (label is None or ' '.join(''.join(el.itertext()).split()) == label
                or any(e.get('id') is not None for e in el.iterdescendants(etree.Element))):
            continue
        for child in list(el):
            el.remove(child)
        el.text = label
        changed += 1
    return _serialize(root) if changed else None


def _label_for(base: str, href, labels: dict):
    """The label for a TOC entry of the file `base` pointing at `href`:
    the target resolved relative to `base`, then its fragment (None: the
    file's first heading)."""
    if not href:
        return None
    parts = urlsplit(href)
    if parts.scheme or parts.netloc:
        return None
    target = base
    if parts.path:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(parts.path)))
    return labels.get(target, {}).get(unquote(parts.fragment) or None)


# --- Chapters -----------------------------------------------------------------

def chapter_items(spine: list) -> dict:
    """The spine entries that can be chapters (XHTML, not the navigation
    document), by id."""
    return {item.id: item for item in spine
            if item.media_type == XHTML_MEDIA_TYPE and not item.is_nav}


def load_chapter(zf, path: str):
    """(raw bytes, root, body, blocks) of a chapter as the segmenter reads
    it, or None when it is missing, unreadable, not well-formed, without
    body or holds an unresolved entity reference."""
    try:
        data = read_member(zf, path)
        root = parse_xml(data)
    except (KeyError, EPUBError, etree.XMLSyntaxError):
        return None
    body = find_body(root)
    if body is None or unresolved_entities(body):
        return None
    return data, root, body, walk_blocks(body)


def _export_chapter(zf, path: str, segments: list, todo: list, fmt: str, src_lang: str,
                    epub3: bool, css_path, chapter: ChapterReport):
    """(new bytes, TOC labels) of a chapter, or None when it stays as it is."""
    loaded = load_chapter(zf, path)
    if loaded is None:
        chapter.kept = [(s.segment_id, CHAPTER_UNREADABLE) for s in todo]
        return None
    data, root, body, blocks = loaded
    if not _matches(blocks, segments):
        chapter.kept = [(s.segment_id, CHAPTER_MISMATCH) for s in todo]
        return None
    if has_internal_subset(data):
        chapter.kept = [(s.segment_id, CHAPTER_DOCTYPE) for s in todo]
        return None

    fills = []
    for seg in todo:
        seg_blocks = blocks[seg.block_start:seg.block_end]
        try:
            pieces = _pieces(seg, seg_blocks)
        except _Kept as e:
            chapter.kept.append((seg.segment_id, e.reason))
            continue
        chapter.translated += 1
        fills += [(b, p) for b, p in zip(seg_blocks, pieces) if p is not None]
    if not fills:
        return None

    targets = _toc_targets(body, blocks)  # on the source tree, before it changes
    ns = etree.QName(body).namespace
    results = {}
    for block, piece in sorted(fills, key=lambda f: f[0].index, reverse=True):
        results[id(block)] = _fill(block, piece, fmt, src_lang, epub3, ns)
    labels = {}
    for key, (block, k, n) in targets.items():
        result = results.get(id(block))
        headings = [] if result is None else [
            e for e in result.iter() if localname(e) in HEADINGS]
        if len(headings) == n:  # the translated block's k-th heading
            label = ' '.join(element_text(headings[k]).split())
            if label:
                labels[key] = label

    if fmt == 'chinese-only':
        for block, _ in fills:  # source-language lang on the elements around the blocks
            for el in block.parent.iterancestors():
                _relang_one(el, src_lang)
            _relang_one(block.parent, src_lang)
        root.set(XML_LANG, TARGET_LANG)
        if epub3 or root.get('lang') is not None:
            root.set('lang', TARGET_LANG)
    elif css_path:
        _link_stylesheet(root, ns, quote(posixpath.relpath(
            css_path, posixpath.dirname(path) or '.')))
    return _serialize(root), labels


def _toc_targets(body, blocks: list) -> dict:
    """The TOC targets of a chapter: id (None for the file's first heading)
    -> (block, index of the target's heading among the block's headings,
    number of the block's headings), all in the source. An id inside a
    block element maps to the one heading that is it, holds it or that it
    holds (else no target); an id on an element that directly holds blocks
    (the innermost container) takes its first block when this is a
    heading. The first occurrence of an id wins."""
    owner, first_block, headings = {}, {}, {}
    for block in blocks:
        if block.tag:
            headings[id(block)] = [e for e in block.nodes[0].iter() if localname(e) in HEADINGS]
            for e in block.nodes[0].iter(etree.Element):
                owner[e] = block
        for el in [block.parent, *block.parent.iterancestors()]:
            first_block.setdefault(el, block)
    targets = {}
    first_heading = True
    for el in body.iter(etree.Element):
        key = el.get('id')
        if localname(el) in HEADINGS and first_heading:
            first_heading = False
            if el in owner:
                found = headings[id(owner[el])]
                targets[None] = (owner[el], found.index(el), len(found))
        if key is None or key in targets:
            continue
        targets[key] = None  # taken: a later duplicate never wins
        block = owner.get(el)
        if block is not None:
            hs = headings[id(block)]
            found = ([h for h in hs if h is el or h in el.iterancestors()]
                     or [h for h in hs if el in h.iterancestors()])
            if len(found) == 1:
                targets[key] = (block, hs.index(found[0]), len(hs))
        elif el in first_block:
            block = first_block[el]
            if block.parent is el and block.tag in HEADINGS:
                targets[key] = (block, 0, 1)
    return {k: v for k, v in targets.items() if v is not None}


def _matches(blocks: list, segments: list) -> bool:
    """Every segment's range is valid and its fingerprints equal its
    blocks'; the ranges cover the blocks exactly once."""
    ranges = []
    for seg in segments:
        start, end = seg.block_start, seg.block_end
        if not (type(start) is int and type(end) is int and 0 <= start < end <= len(blocks)):
            return False
        if [b.fingerprint for b in blocks[start:end]] != seg.fingerprints:
            return False
        ranges.append((start, end))
    ranges.sort()
    return ([r[0] for r in ranges] == [0] + [r[1] for r in ranges[:-1]]
            and ranges[-1][1] == len(blocks))


def _link_stylesheet(root, ns, href: str) -> None:
    head = next((c for c in root if localname(c) == 'head'), None)
    if head is None:
        head = etree.Element(etree.QName(ns, 'head'))
        root.insert(0, head)
    link = etree.SubElement(head, etree.QName(ns, 'link'))
    link.set('rel', 'stylesheet')
    link.set('type', 'text/css')
    link.set('href', href)


# --- The reduced structural check (until Phase 4) -----------------------------

def _pieces(seg, blocks: list) -> list:
    """The translation of each block of the segment (raises _Kept).

    The rule. The segment's stored HTML is its blocks' serializations
    joined by newlines. The translation must be well-formed XML (parsed as
    the content of an element holding the chapter's namespace
    declarations). Its top-level nodes are grouped: each element whose name
    is a block tag is a group of its own; a top-level <br> is refused; the
    text and inline elements between them form one inline group (comments
    and whitespace alone form none). The groups must correspond one to one
    to the blocks, taking each maximal sequence of consecutive inline runs
    of the source as one group: a block element to an element of the same
    name, a sequence of runs to one inline group. A sequence of one run
    takes the whole group; a sequence of k > 1 runs (lines separated by
    <br> in the source) takes the group split at the newlines in its
    top-level text, which must give exactly k pieces with content. A
    piece whose source block has text must have text. The
    stored HTML must itself split into its blocks by the same rule
    (checked by fingerprint), else the segment cannot be mapped. Finally
    the ids (and names of <a>) inside a source block, below the block
    element itself, must be the same values, as many times each, in that
    block's translation; inner tags and other attributes are not checked
    (Phase 4).

    A code segment's translation is plain text, filled into the code
    element; a translation equal to the source code leaves the block as
    it is (piece None).
    """
    if seg.segment_type == SegmentType.CODE:
        return [_code_piece(seg, blocks)]
    nsmap = {}  # the declarations in scope for every block of the segment
    for block in blocks:
        nsmap.update(block.parent.nsmap)
    try:
        source = _split(_fragment(seg.html, nsmap), blocks)
    except etree.XMLSyntaxError:
        raise _Kept(AMBIGUOUS_SOURCE)
    if source is None or [fingerprint(_text(p)) for p in source] != [b.fingerprint for b in blocks]:
        raise _Kept(AMBIGUOUS_SOURCE)
    try:
        fragment = _fragment(seg.translated_text, nsmap)
    except etree.XMLSyntaxError:
        raise _Kept(NOT_WELL_FORMED)
    pieces = _split(fragment, blocks)
    if pieces is None:
        raise _Kept(STRUCTURE_MISMATCH)
    if any(_text(s).strip() and not _text(p).strip() for s, p in zip(source, pieces)):
        raise _Kept(STRUCTURE_MISMATCH)  # a block with text translated to none
    if any(_anchors(s) != _anchors(p) for s, p in zip(source, pieces)):
        raise _Kept(ANCHOR_MISMATCH)
    return pieces


def _anchors(piece) -> Counter:
    """The id values, and the name values of <a>, inside one block's piece,
    counted: below a block element (its own attributes come from the
    source) or in a run's nodes."""
    if isinstance(piece, list):
        elements = [e for n in piece if not isinstance(n, str) for e in n.iter(etree.Element)]
    else:
        elements = piece.iterdescendants(etree.Element)
    return Counter(v for e in elements
                   for v in (e.get('id'), e.get('name') if localname(e) == 'a' else None) if v)


def _fragment(html: str, nsmap: dict):
    """`html` parsed as the content of an element declaring `nsmap`."""
    decls = ''.join(f' xmlns={quoteattr(uri)}' if prefix is None
                    else f' xmlns:{prefix}={quoteattr(uri)}' for prefix, uri in nsmap.items())
    return parse_xml(f'<fragment{decls}>{html}</fragment>'.encode('utf-8'))


def _has_content(items: list) -> bool:
    return any(item.strip() if isinstance(item, str) else localname(item) is not None
               for item in items)


def _split(fragment, blocks: list):
    """The fragment's top-level nodes as one piece per block (an element,
    or a list of strings and nodes for a run), or None when the structure
    differs (see `_pieces`)."""
    groups, region = [], [fragment.text or '']
    for child in fragment:
        name = localname(child)
        if name == 'br':
            return None
        if name in BLOCK_TAGS:
            if _has_content(region):
                groups.append(region)
            groups.append(child)
            region = []
        else:
            region.append(child)
        region.append(child.tail or '')
    if _has_content(region):
        groups.append(region)

    # The blocks with each sequence of consecutive runs as one entry (a count)
    shape = []
    for block in blocks:
        if block.tag:
            shape.append(block.nodes[0])
        elif shape and isinstance(shape[-1], int):
            shape[-1] += 1
        else:
            shape.append(1)
    if len(groups) != len(shape):
        return None
    pieces = []
    for want, group in zip(shape, groups):
        if isinstance(want, int):
            if not isinstance(group, list):
                return None
            lines = [group] if want == 1 else [p for p in _lines(group) if _has_content(p)]
            if len(lines) != want:
                return None
            pieces += lines
        else:
            if isinstance(group, list) or group.tag != want.tag:
                return None
            pieces.append(group)
    return pieces


def _lines(items: list) -> list:
    """`items` split at the newlines of its strings."""
    lines = [[]]
    for item in items:
        if isinstance(item, str):
            first, *rest = item.split('\n')
            lines[-1].append(first)
            lines += [[part] for part in rest]
        else:
            lines[-1].append(item)
    return lines


def _text(piece) -> str:
    if not isinstance(piece, list):
        return element_text(piece)
    return ''.join(item if isinstance(item, str)
                   else '' if localname(item) in (None, *NO_TEXT_TAGS) else element_text(item)
                   for item in piece)


def _code_target(el):
    """The element whose text is the code: `el`, or its only child when that
    is a <code> with no text around it; None when it holds markup."""
    if (len(el) == 1 and localname(el[0]) == 'code'
            and not (el.text or '').strip() and not (el[0].tail or '').strip()):
        el = el[0]
    return None if len(el) else el


def _code_piece(seg, blocks: list):
    if len(blocks) != 1 or _code_text(blocks[0]) is None:
        raise _Kept(STRUCTURE_MISMATCH)
    block = blocks[0]
    if seg.translated_text == _code_text(block):
        return None
    element = block.nodes[0] if block.tag else next(
        n for n in block.nodes if not isinstance(n, str) and localname(n) == 'code')
    piece = copy.deepcopy(element)
    piece.tail = None
    target = _code_target(piece)
    # the stored code must be exactly the target's text, else text outside
    # the target would be written twice
    if target is None or element_text(target) != _code_text(block):
        raise _Kept(CODE_MARKUP)
    try:
        target.text = seg.translated_text
    except ValueError:  # characters XML does not allow
        raise _Kept(NOT_WELL_FORMED)
    return piece if block.tag else [piece]


# --- Filling the tree ---------------------------------------------------------

def _fill(block, piece, fmt: str, src_lang: str, epub3: bool, ns):
    """Put the translation of one block into the tree; returns the element
    that holds the translated block element (None for a run)."""
    if block.tag:
        el = block.nodes[0]
        if fmt == 'chinese-only':
            _replace_children(el, piece)
            _relang(el, src_lang)
            return el
        translated = copy.deepcopy(el)
        translated.tail = None
        _replace_children(translated, piece)
        _mark_translated(translated, src_lang, epub3)
        wrapper, original, translation = _bilingual(fmt, ns)
        tail, el.tail = el.tail, None
        el.addprevious(wrapper)
        wrapper.tail = tail
        original.append(el)
        translation.append(translated)
        return translated

    parent, prev = block.parent, block.prev
    index = 0 if prev is None else parent.index(prev) + 1
    taken = _take_run(block)
    if fmt == 'chinese-only':
        _put(parent, index, prev, piece)
        for item in piece:
            if not isinstance(item, str):
                _relang(item, src_lang)
        return None
    wrapper, original, translation = _bilingual(fmt, ns)
    _put(original, 0, None, taken)
    _put(translation, 0, None, piece)
    _mark_translated(translation, src_lang, epub3)
    parent.insert(index, wrapper)
    return None


def _bilingual(fmt: str, ns):
    def div(cls):
        el = etree.Element(etree.QName(ns, 'div'))
        el.set('class', cls)
        return el
    wrapper, original, translation = div(WRAPPER_CLASS[fmt]), div('original'), div('translation')
    wrapper.append(original)
    wrapper.append(translation)
    return wrapper, original, translation


def _replace_children(el, source) -> None:
    """`el` gets the text and children of `source` (its attributes stay)."""
    for child in list(el):
        el.remove(child)
    el.text = source.text
    for child in list(source):
        el.append(child)


def _take_run(block) -> list:
    """Remove a run from the tree; returns its strings and nodes."""
    if block.prev is None:
        items, block.parent.text = [block.parent.text or ''], None
    else:
        items, block.prev.tail = [block.prev.tail or ''], None
    for node in block.nodes:
        if not isinstance(node, str):
            tail = node.tail or ''
            block.parent.remove(node)
            node.tail = None
            items += [node, tail]
    return items


def _put(parent, index: int, prev, items: list) -> None:
    """Insert strings and nodes at child position `index` of `parent`, after
    `prev` (None: at the start of the parent's text)."""
    texts, nodes = [[]], []
    for item in items:
        if isinstance(item, str):
            texts[-1].append(item)
        else:
            nodes.append(item)
            texts.append([])
    if prev is None:
        parent.text = (parent.text or '') + ''.join(texts[0]) or None
    else:
        prev.tail = (prev.tail or '') + ''.join(texts[0]) or None
    for offset, (node, text) in enumerate(zip(nodes, texts[1:])):
        node.tail = ''.join(text) or None
        parent.insert(index + offset, node)


def _same_language(value: str, language: str) -> bool:
    return bool(language) and value.split('-')[0].lower() == language.split('-')[0].lower()


def _relang_one(e, src_lang: str) -> None:
    """An explicit source-language lang attribute of `e` becomes zh-TW."""
    for attr in ('lang', XML_LANG):
        value = e.get(attr)
        if value is not None and _same_language(value, src_lang):
            e.set(attr, TARGET_LANG)


def _relang(el, src_lang: str) -> None:
    """Explicit source-language lang attributes in `el`'s subtree become zh-TW."""
    for e in el.iter(etree.Element):
        _relang_one(e, src_lang)


def _mark_translated(el, src_lang: str, epub3: bool) -> None:
    """A bilingual translated copy: no id or name in its subtree, marked
    zh-TW (lang only in EPUB 3; XHTML 1.1 has only xml:lang)."""
    for e in el.iter(etree.Element):
        for attr in ('id', 'name'):
            if attr in e.attrib:
                del e.attrib[attr]
    _relang(el, src_lang)
    el.set(XML_LANG, TARGET_LANG)
    if epub3:
        el.set('lang', TARGET_LANG)
    # Readers may hide notes: the translated row of a note stays visible
    kinds = (el.get(EPUB_TYPE) or '').split()
    kept = [k for k in kinds if k not in NOTE_TYPES]
    if kept != kinds:
        if kept:
            el.set(EPUB_TYPE, ' '.join(kept))
        else:
            del el.attrib[EPUB_TYPE]
