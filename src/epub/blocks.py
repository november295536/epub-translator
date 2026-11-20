"""Block walker: the translatable blocks of a chapter body (plan 4.2).

The walk starts at <body> and ignores comments and processing instructions.
A div, section or article is descended into when it has a block-level child
or a <br> child. Inside a walked element a block is either one block-level
child that is not descended into, or one maximal run of inline content (text
and inline elements) between block children and <br> elements. Lists stay
single blocks. Blocks without text are dropped.

Each block has an index within its chapter and a fingerprint: a hash of its
text with all whitespace removed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from lxml import etree

BLOCK_TAGS = frozenset({
    'address', 'article', 'aside', 'blockquote', 'details', 'dialog',
    'dd', 'div', 'dl', 'dt', 'fieldset', 'figcaption', 'figure',
    'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header',
    'hgroup', 'hr', 'li', 'main', 'nav', 'ol', 'p', 'pre', 'section',
    'summary', 'table', 'ul',
})
DESCEND_TAGS = frozenset({'div', 'section', 'article'})
NO_TEXT_TAGS = frozenset({'script', 'style'})

_WHITESPACE = re.compile(r'\s+')


def localname(node):
    """Local name of an element; None for a comment or processing instruction."""
    if not isinstance(node.tag, str):
        return None
    return etree.QName(node).localname


def normalize(text: str) -> str:
    """Text with all whitespace removed: the form blocks are compared in."""
    return _WHITESPACE.sub('', text)


def fingerprint(text: str) -> str:
    """Fingerprint of a block's text."""
    return hashlib.sha256(normalize(text).encode('utf-8')).hexdigest()[:16]


def element_text(el) -> str:
    """Text of an element without comments, processing instructions,
    script and style."""
    out = [el.text or '']
    for child in el:
        if localname(child) not in (None, *NO_TEXT_TAGS):
            out.append(element_text(child))
        out.append(child.tail or '')
    return ''.join(out)


def _escape(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def serialize(el) -> str:
    """The element as XML, without its tail and without the namespace
    declarations it only inherits from its parent."""
    out = etree.tostring(el, encoding='unicode', with_tail=False)
    parent = el.getparent()
    if parent is None:
        return out
    end = out.index('>')
    head = out[:end]
    for prefix, uri in parent.nsmap.items():
        name = 'xmlns' if prefix is None else f'xmlns:{prefix}'
        head = head.replace(f' {name}="{uri}"', '')
    return head + out[end:]


@dataclass
class Block:
    """One block of a chapter."""
    index: int | None   # position among the chapter's blocks; None: comment text only
    tag: str            # local name of a block element; '' for a run of inline content
    text: str           # text, comments excluded
    legacy_text: str    # normalized text with the walked elements' direct comments
    html: str
    nodes: list         # the element, or the strings and nodes of the run
    # A run only: the walked element that holds it, and the child whose
    # tail begins it (None: it begins with the parent's text)
    parent: object = None
    prev: object = None

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.text)


def _run_block(items: list, parent, prev) -> Block:
    text, legacy, html = [], [], []
    for item in items:
        if isinstance(item, str):
            text.append(item)
            legacy.append(item)
            html.append(_escape(item))
        elif item.tag is etree.Comment:
            legacy.append(item.text or '')
        elif localname(item) is not None:
            t = '' if localname(item) in NO_TEXT_TAGS else element_text(item)
            text.append(t)
            legacy.append(t)
            html.append(serialize(item))
    return Block(None, '', ''.join(text), normalize(''.join(legacy)),
                 ''.join(html).strip(), list(items), parent, prev)


def _walk(el, out: list) -> None:
    run = [el.text or '']
    prev = None

    def flush():
        out.append(_run_block(run, el, prev))
        run.clear()

    for child in el:
        name = localname(child)
        if name == 'br':
            flush()
        elif name in BLOCK_TAGS:
            flush()
            if name in DESCEND_TAGS and any(
                    localname(c) in BLOCK_TAGS or localname(c) == 'br' for c in child):
                _walk(child, out)
            else:
                text = element_text(child)
                out.append(Block(None, name, text, normalize(text), serialize(child), [child]))
        else:
            run.append(child)
        run.append(child.tail or '')
        if name == 'br' or name in BLOCK_TAGS:
            prev = child  # the next run begins at its tail
    flush()


def walk_blocks(body, with_comment_runs: bool = False) -> list[Block]:
    """The blocks of a chapter body, in document order, with their indexes.

    With `with_comment_runs` the result also holds the runs whose only text
    is comment text (index None), as segmenters before comments were
    skipped saw them; the migration needs them to match old segments.
    """
    walked: list[Block] = []
    _walk(body, walked)
    result = []
    index = 0
    for block in walked:
        if normalize(block.text):
            block.index = index
            index += 1
            result.append(block)
        elif with_comment_runs and block.legacy_text:
            result.append(block)
    return result

