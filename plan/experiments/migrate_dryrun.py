"""Dry run of the schema 1 -> 2 migration matching (plan/02-improvement-plan.md, section 4.2).

Read-only: opens each project's project.json and source EPUB, writes nothing, prints no book text.

Usage (from the repository root, venv active):

    python3 plan/experiments/migrate_dryrun.py            # all projects under projects/
    python3 plan/experiments/migrate_dryrun.py --strict   # exit 1 unless every chapter matches

What it checks, per chapter: the concatenated text of the legacy segments (taken from each
segment's stored `html`) equals the concatenated text of the blocks produced by the new block
walker on the raw XHTML, ignoring whitespace. It then classifies how segment boundaries line up
with block boundaries.
"""
import bisect
import glob
import json
import os
import posixpath
import re
import sys
import warnings
import zipfile
from collections import Counter
from urllib.parse import unquote

from bs4 import BeautifulSoup
from lxml import etree

warnings.filterwarnings('ignore')

BLOCK_TAGS = frozenset({
    'address', 'article', 'aside', 'blockquote', 'details', 'dialog', 'dd', 'div', 'dl', 'dt',
    'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'header', 'hgroup', 'hr', 'li', 'main', 'nav', 'ol', 'p', 'pre', 'section', 'summary',
    'table', 'ul',
})
DESCEND = {'div', 'section', 'article'}
WS = re.compile(r'\s+')
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)


def norm(s):
    return WS.sub('', s or '')


def lname(el):
    return etree.QName(el).localname if isinstance(el.tag, str) else None


def el_text(el):
    """Text of an element, without comments, PIs, script and style."""
    out = [el.text or '']
    for c in el:
        n = lname(c)
        if n is not None and n not in ('script', 'style'):
            out.append(el_text(c))
        out.append(c.tail or '')
    return ''.join(out)


def descend_into(el):
    """True for a pure container, or for mixed content holding block children or <br>."""
    return any(lname(c) in BLOCK_TAGS or lname(c) == 'br' for c in el)


def walk(el, blocks, legacy_comments):
    """Append the normalized text of each block under `el`, in document order."""
    run = [norm(el.text)]

    def flush():
        t = ''.join(run)
        if t:
            blocks.append(t)
        run.clear()

    for c in el:
        n = lname(c)
        if n is None:                                   # comment or processing instruction
            if legacy_comments and c.tag is etree.Comment:
                run.append(norm(c.text))                # legacy segmenter stored it as text (Bug 5)
        elif n == 'br':
            flush()
        elif n in BLOCK_TAGS:
            flush()
            if n in DESCEND and descend_into(c):
                walk(c, blocks, legacy_comments)
            else:
                t = norm(el_text(c))
                if t:
                    blocks.append(t)
        elif n not in ('script', 'style'):
            run.append(norm(el_text(c)))
        run.append(norm(c.tail))
    flush()


def read_manifest(zf):
    cont = etree.fromstring(zf.read('META-INF/container.xml'), PARSER)
    opf_path = cont.xpath('//*[local-name()="rootfile"]/@full-path')[0]
    opf = etree.fromstring(zf.read(opf_path), PARSER)
    base = posixpath.dirname(opf_path)
    return {
        it.get('id'): posixpath.normpath(posixpath.join(base, unquote(it.get('href'))))
        for it in opf.xpath('//*[local-name()="manifest"]/*[local-name()="item"]')
    }


def ends(texts):
    out, off = [], 0
    for t in texts:
        off += len(t)
        out.append(off)
    return out


def check_project(pj):
    d = json.load(open(pj))
    zf = zipfile.ZipFile(d['epub_path'])
    manifest = read_manifest(zf)
    members = set(zf.namelist())
    by_ch = {}
    for s in d['segments']:
        by_ch.setdefault(s['chapter_id'], []).append(s)

    c = Counter()
    failed = []
    for cid, segs in by_ch.items():
        c['chapters'] += 1
        c['segments'] += len(segs)
        path = manifest.get(cid)
        if path not in members:
            failed.append((cid, 'not in manifest or archive'))
            continue
        try:
            doc = etree.fromstring(zf.read(path), PARSER)
        except etree.XMLSyntaxError:
            failed.append((cid, 'not well-formed XML'))
            continue
        body = doc.xpath('//*[local-name()="body"]')[0]
        seg_texts = [norm(BeautifulSoup(s['html'], 'html.parser').get_text()) for s in segs]
        blocks = None
        for legacy_comments in (False, True):
            cand = []
            walk(body, cand, legacy_comments)
            if ''.join(cand) == ''.join(seg_texts):
                blocks = cand
                if legacy_comments:
                    c['chapters_with_comment_text'] += 1
                break
        if blocks is None:
            failed.append((cid, 'text differs'))
            continue

        c['chapters_matched'] += 1
        c['segments_matched'] += len(segs)
        c['largest_block_chars'] = max([c['largest_block_chars']] + [len(b) for b in blocks])
        b_ends, s_ends = ends(blocks), ends(seg_texts)
        prev = 0
        for p in sorted(set(b_ends) & set(s_ends)):
            ns = bisect.bisect_right(s_ends, p) - bisect.bisect_right(s_ends, prev)
            if ns <= 1:
                c['segments_on_block_boundaries'] += ns
            else:
                c['segments_to_merge'] += ns
                c['merge_groups'] += 1
            prev = p
    return c, failed


def main():
    strict = '--strict' in sys.argv
    total = Counter()
    any_failed = False
    for pj in sorted(glob.glob('projects/*/project.json')):
        c, failed = check_project(pj)
        name = os.path.basename(os.path.dirname(pj))[:44]
        print(f"{name}")
        print(f"  chapters {c['chapters_matched']}/{c['chapters']}"
              f"  segments {c['segments_matched']}/{c['segments']}"
              f"  on-boundary {c['segments_on_block_boundaries']}"
              f"  to-merge {c['segments_to_merge']} in {c['merge_groups']} groups"
              f"  comment-text chapters {c['chapters_with_comment_text']}"
              f"  largest block {c['largest_block_chars']} chars")
        for cid, why in failed:
            any_failed = True
            print(f"  UNMATCHED {cid}: {why}")
        largest = max(total['largest_block_chars'], c['largest_block_chars'])
        total.update(c)
        total['largest_block_chars'] = largest
    print(f"TOTAL chapters {total['chapters_matched']}/{total['chapters']}"
          f"  segments {total['segments_matched']}/{total['segments']}"
          f"  on-boundary {total['segments_on_block_boundaries']}"
          f"  to-merge {total['segments_to_merge']} in {total['merge_groups']} groups"
          f"  largest block {total['largest_block_chars']} chars")
    if strict and any_failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
