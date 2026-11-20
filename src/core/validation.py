"""Protection and validation of translations (plan 4.1).

Protection. The model sees a segment's HTML with every attribute list
replaced by a short numeric key: `<p class="a" id="b">` becomes
`<p k="1">`. Every top-level block gets a key; every other tag that has
attributes gets one; tags without attributes are sent as they are. A
top-level run of inline content (text between block elements) is wrapped in
a keyed `<run>` element. Both directions work on tokens of the string, not
on a parsed tree, so `restore(protect(html)) == html` exactly.

Validation. One comparison serves the three places that validate: a model
reply (restored first), a text saved or approved in the review, and the
export. Both sides are parsed as XML fragments and cut into one piece per
block (`split_pieces`); the translation must then have, per block, the same
elements as the source: an element is compared by tag name and attributes,
so every attribute-bearing element (a keyed tag) appears exactly once in
its block with its attributes, every tag without attributes appears as many
times per tag name as in the source, and no other tag or attribute appears.
The order of elements inside a block is not compared. Structural rules
block (the translation is not used); quality rules only warn.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from xml.sax.saxutils import quoteattr

from lxml import etree

from src.core.project import Issue
from src.epub.blocks import BLOCK_TAGS, NO_TEXT_TAGS, element_text, fingerprint, localname, normalize
from src.epub.extractor import parse_xml

# --- Tokens --------------------------------------------------------------------

_TOKEN = re.compile(r'''
    <!--.*?-->
  | <!\[CDATA\[.*?\]\]>
  | <\?.*?\?>
  | <![^>]*>
  | </(?P<end>[^\s/>]+)\s*>
  | <(?P<name>[A-Za-z_][\w:.\-]*)
     (?P<attrs>(?:\s+[^\s=/>]+\s*=\s*(?:"[^"]*"|'[^']*'))*\s*)
     (?P<close>/?)>
''', re.S | re.X)

# The attribute list of a protected tag: exactly one attribute, k
_KEY_ATTRS = re.compile(r'''\s+k\s*=\s*(?:"(\d+)"|'(\d+)')\s*''')

# The neutral element that holds a top-level run of inline content
WRAPPER = 'run'


def _tokens(text: str):
    """(start, end, match) of every markup token, and (start, end, None) of
    the text between them, in order; together they cover `text`."""
    pos = 0
    for m in _TOKEN.finditer(text):
        if m.start() > pos:
            yield pos, m.start(), None
        yield m.start(), m.end(), m
        pos = m.end()
    if pos < len(text):
        yield pos, len(text), None


def _local(name: str) -> str:
    return name.rpartition(':')[2]


def top_level_units(html: str) -> list:
    """(start, end, is_run) of the top-level units of `html`, in order: a
    block element (start tag to end tag), or a run of inline content (from
    its first to its last character that is text or an element; comments
    and whitespace at its edges are left out)."""
    units, run, depth, block_start = [], None, 0, None
    for start, end, m in _tokens(html):
        text = html[start:end]
        if m is None:
            if depth == 0 and text.strip():
                lead = len(text) - len(text.lstrip())
                tail = len(text) - len(text.rstrip())
                run = [run[0] if run else start + lead, end - tail]
        elif m.group('name'):
            closing = m.group('close') == '/'
            if depth == 0:
                if _local(m.group('name')) in BLOCK_TAGS:
                    if run:
                        units.append((run[0], run[1], True))
                        run = None
                    if closing:
                        units.append((start, end, False))
                    else:
                        block_start = start
                else:
                    run = [run[0] if run else start, end]
            if not closing:
                depth += 1
        elif m.group('end'):
            depth = max(depth - 1, 0)
            if depth == 0:
                if block_start is not None:
                    units.append((block_start, end, False))
                    block_start = None
                elif run:
                    run[1] = end
    if run:
        units.append((run[0], run[1], True))
    return units


@dataclass
class Protected:
    """A segment's HTML in the form the model sees."""
    html: str                                  # the prompt form
    keys: dict = field(default_factory=dict)   # key -> (tag name, attribute text); WRAPPER: None


def protect(html: str) -> Protected:
    """The prompt form of `html` (see the module docstring)."""
    runs = [(s, e) for s, e, is_run in top_level_units(html) if is_run]
    starts = {s for s, _ in runs}
    ends = {e for _, e in runs}
    cuts = sorted(starts | ends)
    out, keys, depth = [], {}, 0

    def key_for(name, attrs):
        k = str(len(keys) + 1)
        keys[k] = (name, attrs)
        return k

    for start, end, m in _tokens(html):
        pieces = [(start, end)]
        if m is None:  # a run may begin or end inside a text token
            inner = [c for c in cuts if start < c < end]
            bounds = [start, *inner, end]
            pieces = list(zip(bounds, bounds[1:]))
        for s, e in pieces:
            if s in starts:
                out.append(f'<{WRAPPER} k="{key_for(WRAPPER, None)}">')
            if m is None:
                out.append(html[s:e])
            elif m.group('name'):
                name, attrs, close = m.group('name'), m.group('attrs'), m.group('close')
                if (depth == 0 and _local(name) in BLOCK_TAGS) or attrs.strip():
                    out.append(f'<{name} k="{key_for(name, attrs)}"{close}>')
                else:
                    out.append(html[s:e])
                if not close:
                    depth += 1
            else:
                if m.group('end'):
                    depth = max(depth - 1, 0)
                out.append(html[s:e])
            if e in ends:
                out.append(f'</{WRAPPER}>')
    return Protected(''.join(out), keys)


def restore(text: str, protected: Protected) -> str:
    """`text` (a reply to the prompt form, or the prompt form itself) with
    each keyed tag given back its attribute text and the run wrappers
    removed. A tag whose key is unknown, or names another tag, or that has
    other attributes, is left as it is (validation then refuses it)."""
    out, stack = [], []
    for start, end, m in _tokens(text):
        token = text[start:end]
        if m is None:
            out.append(token)
            continue
        if m.group('end'):
            if stack and stack.pop():
                continue  # the end of a dropped wrapper
            out.append(token)
            continue
        name = m.group('name')
        if not name:
            out.append(token)
            continue
        drop = False
        km = _KEY_ATTRS.fullmatch(m.group('attrs'))
        if km:
            entry = protected.keys.get(km.group(1) or km.group(2))
            if entry is not None and entry[0] == name:
                if entry[1] is None:
                    drop = True
                else:
                    token = f'<{name}{entry[1]}{m.group("close")}>'
        if not drop:
            out.append(token)
        if not m.group('close'):
            stack.append(drop)
    return ''.join(out)


# --- Fragments and pieces --------------------------------------------------------

XHTML_NS = 'http://www.w3.org/1999/xhtml'
_PREFIX = re.compile(r'</?([A-Za-z_][\w.\-]*):|\s([A-Za-z_][\w.\-]*):[\w.\-]+\s*=')


def declarations(*texts: str) -> dict:
    """Namespace declarations under which stored HTML can be parsed without
    its chapter: the XHTML default namespace, and a placeholder namespace
    for every prefix used (source and translation are parsed under the same
    declarations, so only the prefix matters)."""
    nsmap = {None: XHTML_NS}
    for text in texts:
        for m in _PREFIX.finditer(text):
            prefix = m.group(1) or m.group(2)
            if prefix not in ('xml', 'xmlns'):
                nsmap[prefix] = f'urn:x-prefix:{prefix}'
    return nsmap


def fragment(html: str, nsmap: dict):
    """`html` parsed as the content of an element declaring `nsmap`
    (raises etree.XMLSyntaxError)."""
    decls = ''.join(f' xmlns={quoteattr(uri)}' if prefix is None
                    else f' xmlns:{prefix}={quoteattr(uri)}' for prefix, uri in nsmap.items())
    return parse_xml(f'<fragment{decls}>{html}</fragment>'.encode('utf-8'))


def _has_content(items: list) -> bool:
    return any(item.strip() if isinstance(item, str) else localname(item) is not None
               for item in items)


def groups(frag):
    """The fragment's top-level nodes grouped: each block element is a
    group; the text and inline nodes between them form one group (a list of
    strings and nodes) when they hold content. None when a top-level <br>
    occurs."""
    out, region = [], [frag.text or '']
    for child in frag:
        name = localname(child)
        if name == 'br':
            return None
        if name in BLOCK_TAGS:
            if _has_content(region):
                out.append(region)
            out.append(child)
            region = []
        else:
            region.append(child)
        region.append(child.tail or '')
    if _has_content(region):
        out.append(region)
    return out


def lines(items: list) -> list:
    """`items` split at the newlines of its strings."""
    result = [[]]
    for item in items:
        if isinstance(item, str):
            first, *rest = item.split('\n')
            result[-1].append(first)
            result += [[part] for part in rest]
        else:
            result[-1].append(item)
    return result


def piece_text(piece) -> str:
    if not isinstance(piece, list):
        return element_text(piece)
    return ''.join(item if isinstance(item, str)
                   else '' if localname(item) in (None, *NO_TEXT_TAGS) else element_text(item)
                   for item in piece)


def _content_lines(group: list) -> list:
    return [p for p in lines(group) if _has_content(p)]


def source_shape(source_groups: list, fingerprints):
    """The source's groups cut into blocks: (pieces, shape), where shape
    has the tag of each block element and, for each run group, the number
    of runs it holds. Without fingerprints every run group is one run. With
    them, a run group whose text is not the next block's is cut at its
    newlines (runs separated by <br> in the chapter are joined by newlines),
    and every piece must have its block's fingerprint. None when the source
    cannot be cut that way."""
    pieces, shape = [], []
    for group in source_groups:
        if not isinstance(group, list):
            pieces.append(group)
            shape.append(group.tag)
            continue
        cut = [group]
        if fingerprints is not None and len(pieces) < len(fingerprints) and \
                fingerprint(piece_text(group)) != fingerprints[len(pieces)]:
            cut = _content_lines(group)
        pieces += cut
        shape.append(len(cut))
    if fingerprints is not None and \
            [fingerprint(piece_text(p)) for p in pieces] != list(fingerprints):
        return None
    return pieces, shape


def translation_pieces(translation_groups: list, shape: list):
    """The translation's groups cut like the source (see source_shape), or
    None when they do not correspond: the same number of groups, a block
    element for a block element of the same tag, a run group for a run
    group, cut at its newlines into exactly as many lines with content as
    the source has runs there."""
    if len(translation_groups) != len(shape):
        return None
    pieces = []
    for want, group in zip(shape, translation_groups):
        if isinstance(want, int):
            if not isinstance(group, list):
                return None
            cut = [group] if want == 1 else _content_lines(group)
            if len(cut) != want:
                return None
            pieces += cut
        else:
            if isinstance(group, list) or group.tag != want:
                return None
            pieces.append(group)
    return pieces


def _elements(piece) -> list:
    if isinstance(piece, list):
        return [e for n in piece if not isinstance(n, str) and localname(n) is not None
                for e in n.iter(etree.Element)]
    return list(piece.iter(etree.Element))


def _signature(el) -> tuple:
    return (el.tag, tuple(sorted(el.attrib.items())))


def _shown(tag: str) -> str:
    return f'<{etree.QName(tag).localname}>'


def _describe(group) -> str:
    return WRAPPER if isinstance(group, list) else etree.QName(group).localname


# --- Rules --------------------------------------------------------------------------

STRUCTURE, QUALITY, BACKEND = 'structure', 'quality', 'backend'

# Structural (blocking)
NOT_WELL_FORMED = 'not_well_formed'        # the translation is not well-formed XML
STRUCTURE_MISMATCH = 'structure_mismatch'  # its top-level blocks differ from the source's
EMPTY_BLOCK = 'empty_block'                # a block with text has none in the translation
UNKNOWN_TAG = 'unknown_tag'                # a tag name the source segment does not have
KEY_MISMATCH = 'key_mismatch'              # an attribute-bearing element missing or repeated in its block
EXTRA_ATTRIBUTES = 'extra_attributes'      # an element with attributes no source element of its block has
TAG_COUNT = 'tag_count'                    # a tag without attributes occurs more or less often
AMBIGUOUS_SOURCE = 'ambiguous_source'      # the stored source cannot be cut into its blocks
CODE_CHANGED = 'code_changed'              # code differs from the source outside comment lines
# Quality (warning)
UNTRANSLATED = 'untranslated'              # identical to the source, which has words
LENGTH_RATIO = 'length_ratio'              # length ratio outside LENGTH_RATIO_BAND
# Backend (the reply is not a translation at all)
NOT_HTML = 'not_html'
EMPTY_REPLY = 'empty_reply'

STRUCTURAL_CODES = (NOT_WELL_FORMED, STRUCTURE_MISMATCH, EMPTY_BLOCK, UNKNOWN_TAG, KEY_MISMATCH,
                    EXTRA_ATTRIBUTES, TAG_COUNT, AMBIGUOUS_SOURCE, CODE_CHANGED)
QUALITY_CODES = (UNTRANSLATED, LENGTH_RATIO)

# Translation characters per source character (whitespace removed),
# outside of which a segment gets a length_ratio warning; checked only for
# sources of at least LENGTH_RATIO_MIN_CHARS characters. Tuned on the
# existing translations (plan/03-acceptance-log.md, Phase 4).
LENGTH_RATIO_BAND = (0.25, 0.8)
LENGTH_RATIO_MIN_CHARS = 200

_WORD = re.compile(r'[A-Za-z]{2,}')


def _issue(kind: str, code: str, detail: str) -> Issue:
    return Issue(kind=kind, code=code, detail=detail, blocking=kind != QUALITY)


@dataclass
class Result:
    """The outcome of a check: the issues and, when nothing blocks, the
    translation of each block (for the exporter)."""
    issues: list
    pieces: list | None = None

    @property
    def blocking(self) -> list:
        return [i for i in self.issues if i.blocking]


def check_html(source: str, translation: str, fingerprints=None, nsmap=None) -> Result:
    """Compare a translation (with real attributes) to its source HTML.

    `fingerprints`: the blocks' fingerprints (schema 2), to cut runs that
    were separated by <br>; `nsmap`: the declarations in scope in the
    chapter (default: `declarations` of both texts)."""
    if nsmap is None:
        nsmap = declarations(source, translation)
    try:
        src_groups = groups(fragment(source, nsmap))
    except etree.XMLSyntaxError:
        src_groups = None
    shaped = None if src_groups is None else source_shape(src_groups, fingerprints)
    if shaped is None:
        return Result([_issue(STRUCTURE, AMBIGUOUS_SOURCE, '原文無法切成各個區塊')])
    src_pieces, shape = shaped
    try:
        frag = fragment(translation, nsmap)
    except etree.XMLSyntaxError as e:
        return Result([_issue(STRUCTURE, NOT_WELL_FORMED, f'不是格式正確的 XML：{e}')])
    tr_groups = groups(frag)
    pieces = None if tr_groups is None else translation_pieces(tr_groups, shape)
    if pieces is None:
        shown_src = '、'.join(_describe(g) for g in src_groups)
        shown_tr = ('頂層有 <br>' if tr_groups is None
                    else '、'.join(_describe(g) for g in tr_groups) or '（無）')
        return Result([_issue(
            STRUCTURE, STRUCTURE_MISMATCH,
            f'頂層區塊與原文不符：原文為 {shown_src}，譯文為 {shown_tr}'
            + ('（各行對應原文的各行）' if any(isinstance(w, int) and w > 1 for w in shape) else ''))])

    issues = []
    empty = [str(i) for i, (s, p) in enumerate(zip(src_pieces, pieces), 1)
             if piece_text(s).strip() and not piece_text(p).strip()]
    if empty:
        issues.append(_issue(STRUCTURE, EMPTY_BLOCK, f'第 {"、".join(empty)} 個區塊沒有文字'))
    issues += _compare_elements(src_pieces, pieces)
    if issues:
        return Result(issues)
    return Result(_quality(''.join(map(piece_text, src_pieces)),
                           ''.join(map(piece_text, pieces))), pieces)


def _compare_elements(src_pieces: list, pieces: list) -> list:
    known = {e.tag for p in src_pieces for e in _elements(p)}
    unknown, missing, extra, counts = set(), [], [], []
    for index, (s, p) in enumerate(zip(src_pieces, pieces), 1):
        want = Counter(_signature(e) for e in _elements(s))
        got = Counter(_signature(e) for e in _elements(p))
        for sig in sorted(want.keys() | got.keys(), key=repr):
            tag, attrs = sig
            if want[sig] == got[sig]:
                continue
            if tag not in known:
                unknown.add(_shown(tag))
            elif attrs and want[sig]:
                missing.append(f'第 {index} 個區塊帶 k 屬性的 {_shown(tag)}：'
                               f'原文 {want[sig]} 個，譯文 {got[sig]} 個')
            elif attrs:
                extra.append(f'第 {index} 個區塊的 {_shown(tag)} 帶有原文沒有的屬性')
            else:
                counts.append(f'第 {index} 個區塊不帶屬性的 {_shown(tag)}：'
                              f'原文 {want[sig]} 個，譯文 {got[sig]} 個')
    issues = []
    if unknown:
        issues.append(_issue(STRUCTURE, UNKNOWN_TAG,
                             f'出現原文沒有的標籤：{"、".join(sorted(unknown))}'))
    for code, found in ((KEY_MISMATCH, missing), (EXTRA_ATTRIBUTES, extra), (TAG_COUNT, counts)):
        if found:
            issues.append(_issue(STRUCTURE, code, '；'.join(found)))
    return issues


def _quality(source_text: str, translation_text: str) -> list:
    src, tr = normalize(source_text), normalize(translation_text)
    issues = []
    if src == tr and _WORD.search(src):
        issues.append(_issue(QUALITY, UNTRANSLATED, '譯文與原文相同'))
    elif len(src) >= LENGTH_RATIO_MIN_CHARS:
        ratio = len(tr) / len(src)
        low, high = LENGTH_RATIO_BAND
        if not low <= ratio <= high:
            issues.append(_issue(QUALITY, LENGTH_RATIO,
                                 f'譯文長度是原文的 {ratio:.2f} 倍（預期 {low}–{high}）'))
    return issues


# --- Code ----------------------------------------------------------------------------

# A comment line: first non-blank characters `//`, or `#` followed by
# whitespace, `!` or the end of the line (so `#include` is code)
COMMENT_LINE = re.compile(r'^\s*(?://|#(?:\s|!|$))')


def code_lines(code: str) -> list:
    """The lines of code that are not comment lines, trailing whitespace
    removed, without blank lines at either end."""
    kept = [line.rstrip() for line in code.splitlines() if not COMMENT_LINE.match(line)]
    while kept and not kept[0]:
        kept.pop(0)
    while kept and not kept[-1]:
        kept.pop()
    return kept


def check_code(source: str, translation: str) -> Result:
    """A code translation is accepted only when it equals the source after
    comment lines are removed from both."""
    if code_lines(source) == code_lines(translation):
        return Result([], [])
    return Result([_issue(STRUCTURE, CODE_CHANGED,
                          '程式碼在註解行以外與原文不同（只能翻譯 # 或 // 開頭的註解行）')])


def check_segment(seg, text: str, fingerprints=None, nsmap=None) -> Result:
    """The check for a stored segment (SegmentState) and a translation of
    it: code or HTML by the segment's type."""
    if seg.segment_type == 'code':
        return check_code(seg.text, text)
    return check_html(seg.html, text, fingerprints if fingerprints is not None
                      else (seg.fingerprints or None), nsmap)
