"""Protection and validation of translations (plan 4.1).

Protection. The model sees a segment's HTML with every attribute list
replaced by a short numeric key: `<p class="a" id="b">` becomes
`<p k="1">`. Attributes that people read (TRANSLATABLE_ATTRIBUTES: alt,
title, aria-label) stay visible next to the key, to be translated:
`<img src="x.png" alt="A map"/>` becomes `<img k="2" alt="A map"/>`. Every
top-level block gets a key; every other tag that has attributes gets one;
tags without attributes are sent as they are. A top-level run of inline
content (text between block elements) is wrapped in a keyed `<run>`
element. Both directions work on tokens of the string, not on a parsed
tree, so `restore(protect(html)) == html` exactly.

Validation. A model reply is first checked in its keyed form
(`check_keys`: every key exactly once on its tag, the top-level keys in the
source's order, `<run>` only at the top level, no attribute but `k` and the
translatable ones), then restored. From there one comparison serves the
three places that validate: a model reply, a text saved or approved in the
review, and the export (`check_html`). Both sides are parsed as XML
fragments and cut into one piece per block; per block, the block element
itself must keep its tag and attributes, and the elements inside it must be
the same multiset of (tag name, attributes), so every attribute-bearing
element appears exactly once in its block, every tag without attributes as
often per tag name, and nothing else. The values of translatable attributes
are free (an empty one stays empty). The order of elements inside a block
is not compared. Structural rules block (the translation is not used);
quality rules only warn.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from xml.sax.saxutils import quoteattr

from lxml import etree

from src.core.project import Issue
from src.epub.blocks import BLOCK_TAGS, NO_TEXT_TAGS, element_text, fingerprint, localname, normalize
from src.epub.extractor import parse_xml
from src.glossary.glossary_manager import term_pattern

# --- Tokens --------------------------------------------------------------------

# An XML name (letters of any script; no leading digit, dot or hyphen)
_NAME = r'[^\W\d][\w:.\-]*'

_TOKEN = re.compile(rf'''
    <!--.*?-->
  | <!\[CDATA\[.*?\]\]>
  | <\?.*?\?>
  | <![^>]*>
  | </(?P<end>{_NAME})\s*>
  | <(?P<name>{_NAME})
     (?P<attrs>(?:\s+[^\s=/>]+\s*=\s*(?:"[^"]*"|'[^']*'))*\s*)
     (?P<close>/?)>
''', re.S | re.X)

# One attribute of an attribute text: name, quote, raw value
_ATTR = re.compile(r'''\s+([^\s=/>]+)\s*=\s*(?:"([^"]*)"|'([^']*)')''')

# Attributes people read: shown to the model and translated; every other
# attribute is hidden behind the key
TRANSLATABLE_ATTRIBUTES = frozenset({'alt', 'title', 'aria-label'})

# The neutral element that holds a top-level run of inline content
WRAPPER = 'run'


def _attributes(text: str) -> list:
    """(name, quote, raw value, start, end of the raw value) of every
    attribute in an attribute text."""
    out = []
    for m in _ATTR.finditer(text):
        group = 2 if m.group(2) is not None else 3
        out.append((m.group(1), '"' if group == 2 else "'", m.group(group),
                    m.start(group), m.end(group)))
    return out


_ENTITY = re.compile(r'&(#x[0-9a-fA-F]+|#[0-9]+|amp|lt|gt|quot|apos);')
_NAMED = {'amp': '&', 'lt': '<', 'gt': '>', 'quot': '"', 'apos': "'"}


def _unescape(raw: str) -> str:
    """The value of a raw attribute value: literal tabs and line ends are
    spaces (XML attribute-value normalization), then references decoded."""
    raw = re.sub(r'\r\n|[\t\n\r]', ' ', raw)

    def one(m):
        ref = m.group(1)
        if ref.startswith('#x'):
            return chr(int(ref[2:], 16))
        if ref.startswith('#'):
            return chr(int(ref[1:]))
        return _NAMED[ref]
    return _ENTITY.sub(one, raw)


def _escape(value: str, quote: str) -> str:
    """`value` as a raw attribute value in `quote`; tabs and line ends as
    numeric references, so normalization keeps them."""
    out = value.replace('&', '&amp;').replace('<', '&lt;')
    out = out.replace('\t', '&#9;').replace('\n', '&#10;').replace('\r', '&#13;')
    return out.replace(quote, '&quot;' if quote == '"' else '&apos;')


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
class Key:
    """What a key stands for: the tag name, the attribute text (None for the
    run wrapper), the translatable attributes in it, as name -> (quote, raw
    value, start, end within the attribute text), and the top-level key of
    the unit (block or run) it is in (its own key at the top level)."""
    name: str
    attrs: str | None
    shown: dict = field(default_factory=dict)
    owner: str | None = None


@dataclass
class Protected:
    """A segment's HTML in the form the model sees."""
    html: str                                  # the prompt form
    keys: dict = field(default_factory=dict)   # key (str) -> Key
    top: list = field(default_factory=list)    # the top-level keys, in order


def protect(html: str, first_key: int = 1) -> Protected:
    """The prompt form of `html` (see the module docstring); keys are
    numbered from `first_key`."""
    runs = [(s, e) for s, e, is_run in top_level_units(html) if is_run]
    starts = {s for s, _ in runs}
    ends = {e for _, e in runs}
    cuts = sorted(starts | ends)
    out, keys, top, depth = [], {}, [], 0

    def key_for(key: Key, at_top: bool) -> str:
        k = str(first_key + len(keys))
        keys[k] = key
        if at_top:
            top.append(k)
        key.owner = top[-1]  # inside the latest top-level unit, or that unit
        return k

    for start, end, m in _tokens(html):
        pieces = [(start, end)]
        if m is None:  # a run may begin or end inside a text token
            inner = [c for c in cuts if start < c < end]
            bounds = [start, *inner, end]
            pieces = list(zip(bounds, bounds[1:]))
        for s, e in pieces:
            if s in starts:
                out.append(f'<{WRAPPER} k="{key_for(Key(WRAPPER, None), True)}">')
            if m is None:
                out.append(html[s:e])
            elif m.group('name'):
                name, attrs, close = m.group('name'), m.group('attrs'), m.group('close')
                block = depth == 0 and _local(name) in BLOCK_TAGS
                if block or attrs.strip():
                    shown = {n: (q, raw, a, b) for n, q, raw, a, b in _attributes(attrs)
                             if n in TRANSLATABLE_ATTRIBUTES}
                    k = key_for(Key(name, attrs, shown), block)
                    visible = ''.join(f' {n}={q}{raw}{q}' for n, (q, raw, _, _) in shown.items())
                    out.append(f'<{name} k="{k}"{visible}{close}>')
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
    return Protected(''.join(out), keys, top)


def _restored_attrs(key: Key, reply_attrs: list):
    """The attribute text for a keyed tag of a reply: the stored text, with
    the values of the translatable attributes taken from the reply
    (escaped for the stored quote; untouched when the reply repeats the
    stored value). None when the reply's attributes are not `k` plus
    translatable attributes of this key, each once."""
    other = [n for n, *_ in reply_attrs if n != 'k']  # the caller found one k
    if len(set(other)) != len(other) or not set(other) <= set(key.shown):
        return None
    text = key.attrs
    replies = {n: (q, raw) for n, q, raw, _, _ in reply_attrs}
    for name, (quote, raw, a, b) in sorted(key.shown.items(), key=lambda kv: -kv[1][2]):
        if name not in replies or replies[name] == (quote, raw):
            continue
        text = text[:a] + _escape(_unescape(replies[name][1]), quote) + text[b:]
    return text


def restore(text: str, protected: Protected) -> str:
    """`text` (a reply to the prompt form, or the prompt form itself) with
    each keyed tag given back its attribute text (translatable values from
    the reply) and the run wrappers removed. A tag whose key is unknown, or
    names another tag, or that has other attributes, is left as it is
    (validation refuses it)."""
    out, stack = [], []
    for start, end, m in _tokens(text):
        token = text[start:end]
        if m is None or not (m.group('name') or m.group('end')):
            out.append(token)
            continue
        if m.group('end'):
            if stack and stack[-1][0] == m.group('end'):
                if stack.pop()[1]:
                    continue  # the end of a dropped wrapper
            out.append(token)
            continue
        name = m.group('name')
        reply_attrs = _attributes(m.group('attrs'))
        ks = [raw for n, _, raw, _, _ in reply_attrs if n == 'k']
        key = protected.keys.get(ks[0]) if len(ks) == 1 else None
        ours = key is not None and key.name == name
        drop = ours and key.attrs is None
        attrs = _restored_attrs(key, reply_attrs) if ours and not drop else None
        if attrs is not None:
            token = f'<{name}{attrs}{m.group("close")}>'
        if not drop:
            out.append(token)
        if not m.group('close'):
            stack.append((name, drop))
    return ''.join(out)


def check_keys(reply: str, protected: Protected) -> list:
    """The rules of the keyed form, for a well-formed reply: every key
    exactly once, on the tag it was given to; the top-level keys in the
    source's order (nothing else at the top level but whitespace); <run>
    only at the top level; no attribute but `k` and the key's translatable
    attributes, each present when the source has it, empty when it was
    empty."""
    seen, wrong, attrs, misplaced = Counter(), [], [], []
    top, stray, depth = [], False, 0
    for start, end, m in _tokens(reply):
        if m is None:
            if depth == 0 and reply[start:end].strip():
                stray = True
            continue
        if m.group('end'):
            depth = max(depth - 1, 0)
            continue
        name = m.group('name')
        if not name:
            continue
        reply_attrs = _attributes(m.group('attrs'))
        ks = [raw for n, _, raw, _, _ in reply_attrs if n == 'k']
        k = ks[0] if ks else None
        if depth == 0:
            top.append(k)
        key = protected.keys.get(k)
        if k is None:
            if reply_attrs:
                attrs.append(f'<{name}> 帶有原文沒有的屬性')
        elif key is None or key.name != name:
            wrong.append(f'<{name} k="{k}"> 不是原文的標記')
        else:
            seen[k] += 1
            if key.attrs is None and depth:
                misplaced.append(f'<{WRAPPER} k="{k}"> 不在最外層')
            elif depth and top[-1] != key.owner:  # depth > 0: a top-level unit is open
                misplaced.append(f'<{name} k="{k}"> 不在原來的區塊（原文在 k="{key.owner}" 內）')
            given = {n: raw for n, _, raw, _, _ in reply_attrs if n != 'k'}
            for n in sorted(set(given) - set(key.shown)):
                attrs.append(f'<{name} k="{k}"> 多了 {n} 屬性')
            for n in sorted(set(key.shown) - set(given)):
                attrs.append(f'<{name} k="{k}"> 少了 {n} 屬性')
            for n in sorted(set(given) & set(key.shown)):
                source_blank = not _unescape(key.shown[n][1]).strip()
                if source_blank != (not _unescape(given[n]).strip()):
                    attrs.append(f'<{name} k="{k}"> 的 {n} 原文是空的' if source_blank
                                 else f'<{name} k="{k}"> 的 {n} 不可為空')
        if not m.group('close'):
            depth += 1
    issues = []
    if stray or top != protected.top:
        shown_src = '、'.join(f'k="{k}"' for k in protected.top)
        shown_tr = '、'.join('（無 k）' if k is None else f'k="{k}"' for k in top) or '（無）'
        issues.append(_issue(STRUCTURE, STRUCTURE_MISMATCH,
                             f'最外層的標記與原文不同：原文為 {shown_src}，譯文為 {shown_tr}'
                             + ('，且最外層有 <run> 以外的文字' if stray else '')))
    counts = [f'k="{k}"（<{key.name}>）出現 {seen[k]} 次' for k, key in protected.keys.items()
              if seen[k] != 1]
    found = wrong + misplaced + counts
    if found:
        issues.append(_issue(STRUCTURE, KEY_MISMATCH,
                             '；'.join(found) + '（每個 k 必須在原來的標籤上出現一次）'))
    if attrs:
        issues.append(_issue(STRUCTURE, EXTRA_ATTRIBUTES, '屬性與原文不符：' + '；'.join(attrs)))
    return issues


# --- Fragments and pieces --------------------------------------------------------

XHTML_NS = 'http://www.w3.org/1999/xhtml'
_PREFIX = re.compile(r'</?([A-Za-z_][\w.\-]*):|\s([A-Za-z_][\w.\-]*):[\w.\-]+\s*=', re.ASCII)


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
    (raises etree.XMLSyntaxError, or ValueError when `html` cannot be
    encoded, e.g. a lone surrogate)."""
    decls = ''.join(f' xmlns={quoteattr(uri)}' if prefix is None
                    else f' xmlns:{prefix}={quoteattr(uri)}' for prefix, uri in nsmap.items())
    return parse_xml(f'<fragment{decls}>{html}</fragment>'.encode())


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


# The value a translatable attribute is compared by: free, except that an
# empty value stays empty
_FREE = '\x00translated'


def _signature(el) -> tuple:
    return (el.tag, tuple(sorted((name, ('' if not value.strip() else _FREE)
                                  if name in TRANSLATABLE_ATTRIBUTES else value)
                                 for name, value in el.attrib.items())))


def _shown(tag: str) -> str:
    return f'<{etree.QName(tag).localname}>'


def _describe(group) -> str:
    return WRAPPER if isinstance(group, list) else etree.QName(group).localname


# --- Rules --------------------------------------------------------------------------

STRUCTURE, QUALITY, GLOSSARY, BACKEND = 'structure', 'quality', 'glossary', 'backend'

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
# Glossary (warning)
TERM_NOT_APPLIED = 'term_not_applied'      # a glossary term of the source without its translation
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
    return Issue(kind=kind, code=code, detail=detail, blocking=kind not in (QUALITY, GLOSSARY))


@dataclass
class Result:
    """The outcome of a check: the issues and, when nothing blocks, the
    translation of each block (for the exporter)."""
    issues: list
    pieces: list | None = None

    @property
    def blocking(self) -> list:
        return [i for i in self.issues if i.blocking]


def _source(source: str, fingerprints, nsmap):
    """(groups, pieces, shape) of a source, or None when it cannot be cut
    into its blocks."""
    try:
        src_groups = groups(fragment(source, nsmap))
    except (etree.XMLSyntaxError, ValueError):
        return None
    shaped = None if src_groups is None else source_shape(src_groups, fingerprints)
    return None if shaped is None else (src_groups, *shaped)


_POSITION = re.compile(r',? line \d+(?:, column \d+)?')


def xml_error(e: Exception) -> str:
    """The detail of a not_well_formed issue: the parser's reason without
    line and column (they count inside the fragment wrapper, not in the
    text the person or the model wrote)."""
    reason = getattr(e, 'msg', None) or str(e)
    return f'不是格式正確的 XML：{_POSITION.sub("", reason)}'


def _ambiguous() -> Issue:
    return _issue(STRUCTURE, AMBIGUOUS_SOURCE, '原文無法切成各個區塊')


def pieces_per_group(source: str, fingerprints=None):
    """How many blocks each top-level group of `source` holds (a run group
    cut by the fingerprints holds several), or None when it cannot be cut."""
    shaped = _source(source, fingerprints, declarations(source))
    return None if shaped is None else [w if isinstance(w, int) else 1 for w in shaped[2]]


def source_issues(source: str, fingerprints=None) -> list:
    """[the ambiguous_source issue] when no translation of `source` can pass
    check_html, else []."""
    return [] if _source(source, fingerprints, declarations(source)) else [_ambiguous()]


def check_html(source: str, translation: str, fingerprints=None, nsmap=None,
               first_block: int = 1) -> Result:
    """Compare a translation (with real attributes) to its source HTML.

    `fingerprints`: the blocks' fingerprints (schema 2), to cut runs that
    were separated by <br>; `nsmap`: the declarations in scope in the
    chapter (default: `declarations` of both texts); `first_block`: the
    number the issues give the first block (a part of a segment)."""
    if nsmap is None:
        nsmap = declarations(source, translation)
    shaped = _source(source, fingerprints, nsmap)
    if shaped is None:
        return Result([_ambiguous()])
    src_groups, src_pieces, shape = shaped
    try:
        frag = fragment(translation, nsmap)
    except (etree.XMLSyntaxError, ValueError) as e:
        return Result([_issue(STRUCTURE, NOT_WELL_FORMED, xml_error(e))])
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
    empty = [str(i) for i, (s, p) in enumerate(zip(src_pieces, pieces), first_block)
             if piece_text(s).strip() and not piece_text(p).strip()]
    if empty:
        issues.append(_issue(STRUCTURE, EMPTY_BLOCK, f'第 {"、".join(empty)} 個區塊沒有文字'))
    issues += _compare_elements(src_pieces, pieces, first_block)
    if issues:
        return Result(issues)
    # blocks joined by a line break: two one-letter blocks are not a word
    return Result(_quality('\n'.join(map(piece_text, src_pieces)),
                           '\n'.join(map(piece_text, pieces))), pieces)


def _compare_elements(src_pieces: list, pieces: list, first_block: int) -> list:
    """Per block: the block element's own attributes, then the multiset of
    the elements inside it."""
    known = {e.tag for p in src_pieces for e in _elements(p)}
    unknown, missing, extra, counts = set(), [], [], []
    for index, (s, p) in enumerate(zip(src_pieces, pieces), first_block):
        inner_s, inner_p = _elements(s), _elements(p)
        if not isinstance(s, list):  # a block element: same tag (translation_pieces)
            inner_s, inner_p = inner_s[1:], inner_p[1:]
            emptied = _emptied(_signature(s), _signature(p))
            if emptied:
                extra.append(f'第 {index} 個區塊的 {_shown(s.tag)} 的 {"、".join(emptied)} 不可為空')
            elif _signature(s) != _signature(p):
                (missing if s.attrib else extra).append(
                    f'第 {index} 個區塊的 {_shown(s.tag)} 本身的屬性與原文不同')
        want = Counter(_signature(e) for e in inner_s)
        got = Counter(_signature(e) for e in inner_p)
        for sw in sorted(want - got):  # an element whose readable value was emptied
            for sg in sorted(got - want):  # each still in excess: paired once per sw
                emptied = _emptied(sw, sg)
                if emptied and want[sw] > got[sw]:
                    extra.append(f'第 {index} 個區塊的 {_shown(sw[0])} 的 {"、".join(emptied)} 不可為空')
                    want[sw] -= 1
                    want[sg] += 1
        for sig in sorted(want.keys() | got.keys()):
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


def _emptied(want: tuple, got: tuple) -> list:
    """The translatable attributes that `got` has blank where `want` has a
    value, when that is the only difference between the two signatures."""
    if want[0] != got[0] or [n for n, _ in want[1]] != [n for n, _ in got[1]]:
        return []
    emptied = []
    for (name, w), (_, g) in zip(want[1], got[1]):
        if w == _FREE and g == '' and name in TRANSLATABLE_ATTRIBUTES:
            emptied.append(name)
        elif w != g:
            return []
    return emptied


def _quality(source_text: str, translation_text: str) -> list:
    src, tr = normalize(source_text), normalize(translation_text)
    issues = []
    if src == tr and _WORD.search(source_text):
        issues.append(_issue(QUALITY, UNTRANSLATED, '譯文與原文相同'))
    elif len(src) >= LENGTH_RATIO_MIN_CHARS:
        ratio = len(tr) / len(src)
        low, high = LENGTH_RATIO_BAND
        if not low <= ratio <= high:
            issues.append(_issue(QUALITY, LENGTH_RATIO,
                                 f'譯文長度是原文的 {ratio:.2f} 倍（預期 {low}–{high}）'))
    return issues


# --- Glossary -------------------------------------------------------------------------

# Han characters (CJK unified ideographs, extensions and compatibility
# ideographs, also outside the basic plane)
_HAN = re.compile('[㐀-䶿一-鿿豈-﫿\U00020000-\U0003ffff]')
# Middle dots in names (艾拉·維恩 / 艾拉・維恩 / 艾拉‧維恩)
_DOTS = str.maketrans('・‧•', '···')
# Categories whose terms are names: only a plural or possessive ending is
# an inflection of them
NAME_CATEGORIES = frozenset({'person', 'place', 'organization', 'work'})
_POSSESSIVE = r"(?:['’]s?)?"


def plain_text(translation: str) -> str:
    """The text a reader sees in a translation: parsed as a fragment (it has
    passed the structural check), without tags, comments, script and style,
    entities decoded. A text that does not parse (plain text of a legacy
    segment) is taken as it is."""
    try:
        frag = fragment(translation, declarations(translation))
    except (etree.XMLSyntaxError, ValueError):
        return translation
    return element_text(frag)


def _without_originals(text: str) -> str:
    """`text` without the balanced parenthetical groups that hold no Han
    character (the original a translation may add after a term: 滴水線（drip
    line）, 介面（API (v2)）); a group with Han stays, an unmatched parenthesis
    stays."""
    removed = [False] * len(text)
    opened = []
    for i, ch in enumerate(text):
        if ch == '(':
            opened.append(i)
        elif ch == ')' and opened:
            start = opened.pop()
            if not _HAN.search(text, start, i):
                removed[start:i + 1] = [True] * (i + 1 - start)
    return ''.join(ch for ch, gone in zip(text, removed) if not gone)


def _comparable(text: str) -> str:
    """Plain `text` in NFKC (full-width parentheses become ASCII), one kind of
    middle dot, parenthetical originals and whitespace removed, case-folded."""
    text = unicodedata.normalize('NFKC', text).translate(_DOTS)
    return re.sub(r'\s+', '', _without_originals(text)).casefold()


def occurrence_pattern(entry) -> re.Pattern:
    """Where the check takes a term as occurring in a source text: the
    prompt's start (`term_pattern`), then at most a short inflection and a
    word boundary (the prompt's matching has no end boundary: `Eve` would
    match "every"). A name (NAME_CATEGORIES) may take a plural and a
    possessive; another term also -ed / -ing (a final e dropped or kept).
    A `work` entry is case-sensitive: the prompt applies its translation only
    to the title of a work, taken as written with the glossary's capitals."""
    term = entry.term.strip()
    category = entry.category if isinstance(entry.category, str) else ''
    if category in NAME_CATEGORIES:
        body = term_pattern(term) + '(?:e?s)?'
    elif len(term) > 1 and term[-1] in 'eE' and term[-2].isalpha():  # activate -> activating
        body = term_pattern(term[:-1]) + '(?:e(?:s|d)?|ing)'
    else:
        body = term_pattern(term) + '(?:e?s|ed|ing)?'
    flags = 0 if category == 'work' else re.IGNORECASE
    return re.compile(body + _POSSESSIVE + r'(?!\w)', flags)


def _checkable(entry) -> bool:
    return all(isinstance(v, str) and v.strip() for v in (entry.term, entry.translation))


def terms_not_applied(source_text: str, translation: str, glossary) -> list:
    """The glossary entries that occur in `source_text` (occurrence_pattern)
    and whose translation is not in the text of `translation` (both
    compared as `_comparable`). The prompt holds every such entry
    (`terms_for_prompt`: the whole glossary, or above its size limit the
    entries whose start matches, which an occurrence implies). An entry
    whose term or translation is not a non-empty string is skipped."""
    if glossary is None or not glossary.entries:
        return []
    text = _comparable(plain_text(translation))
    missing = []
    for entry in sorted(filter(_checkable, glossary.entries.values()),
                        key=lambda e: e.term.lower()):
        expected = _comparable(entry.translation)
        if expected and expected not in text and occurrence_pattern(entry).search(source_text):
            missing.append(entry)
    return missing


def glossary_issues(source_text: str, translation: str, glossary) -> list:
    """One term_not_applied warning per entry of terms_not_applied."""
    return [_issue(GLOSSARY, TERM_NOT_APPLIED, f'術語「{e.term}」未譯為「{e.translation}」')
            for e in terms_not_applied(source_text, translation, glossary)]


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


# Characters XML text cannot hold (control characters, lone surrogates)
_NOT_XML_TEXT = re.compile('[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]')


def check_code(source: str, translation: str) -> Result:
    """A code translation is accepted only when it can be XML text and
    equals the source after comment lines are removed from both."""
    if _NOT_XML_TEXT.search(translation):
        return Result([_issue(STRUCTURE, NOT_WELL_FORMED, '譯文含有 XML 不允許的字元')])
    if code_lines(source) == code_lines(translation):
        return Result([])
    return Result([_issue(STRUCTURE, CODE_CHANGED,
                          '程式碼在註解行以外與原文不同（只能翻譯 # 或 // 開頭的註解行）')])


def check_segment(seg, text: str, glossary=None) -> Result:
    """The check for a stored segment (SegmentState) and a translation of
    it: code or HTML by the segment's type; runs cut by the segment's
    fingerprints (none for schema 1). A text segment without HTML (legacy
    schema 1 data) has no structure to compare. A text translation that
    passes gets the glossary warnings (`glossary`: a GlossaryManager, or
    None for none); the export does not call this."""
    if seg.segment_type == 'code':
        return check_code(seg.text, text)
    if not seg.html.strip():  # a schema 1 text segment without HTML: nothing to compare
        result = Result([])
    else:
        result = check_html(seg.html, text, seg.fingerprints or None)
    if not result.blocking:
        result.issues += glossary_issues(seg.text, text, glossary)
    return result
