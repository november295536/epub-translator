"""Tests for the block walker, schema 2 segments and the raw package reader
(Phase 2). Fixtures are raw ZIP archives with invented text."""

import zipfile

import pytest

from src.core.segmenter import Segmenter, SegmentType, MAX_WORDS
from src.epub.blocks import fingerprint, normalize, serialize, walk_blocks
from src.epub.extractor import (
    EPUBError, EPUBExtractor, find_body, parse_xml, unresolved_entities,
)
from tests.conftest import body_of, write_raw_epub, xhtml


def _texts(html):
    return [normalize(b.text) for b in walk_blocks(body_of(html))]


def _htmls(html):
    return [b.html for b in walk_blocks(body_of(html))]


class TestWalk:

    def test_br_splits_runs(self):
        html = '<div>Line one<br/>Line <em>two</em><br/>Line three</div>'
        assert _htmls(html) == ['Line one', 'Line <em>two</em>', 'Line three']

    def test_br_directly_under_body(self):
        assert _texts('First run<br/>Second run') == ['Firstrun', 'Secondrun']

    def test_br_inside_inline_element_does_not_split(self):
        assert _htmls('<p>a</p><span>b<br/>c</span>') == ['<p>a</p>', '<span>b<br/>c</span>']

    def test_descend_on_text_mixed_with_block(self):
        html = '<div class="x">Intro <b>bold</b> text<p>Para.</p>Tail text</div>'
        assert _htmls(html) == ['Intro <b>bold</b> text', '<p>Para.</p>', 'Tail text']

    def test_descend_container_div(self):
        assert _htmls('<section><h1>T</h1><p>P</p></section>') == ['<h1>T</h1>', '<p>P</p>']

    def test_content_div_is_one_block(self):
        html = '<div class="note"><em>Note:</em> keep the lamp lit.</div>'
        assert _htmls(html) == [html]

    def test_other_block_elements_not_descended(self):
        html = '<blockquote><p>A</p><p>B</p></blockquote><ul><li>x</li><li>y</li></ul>'
        assert _htmls(html) == ['<blockquote><p>A</p><p>B</p></blockquote>',
                                '<ul><li>x</li><li>y</li></ul>']

    def test_body_level_comment_ignored(self):
        html = '<p>One</p><!-- hidden remark --><p>Two</p>'
        assert _texts(html) == ['One', 'Two']

    def test_comment_inside_run_not_text_nor_html(self):
        blocks = walk_blocks(body_of('Bare <!-- hidden --> text'))
        assert [b.text for b in blocks] == ['Bare  text']
        assert blocks[0].html == 'Bare  text'

    def test_comment_only_run_only_with_comment_runs(self):
        body = body_of('<div><!-- hidden --><p>One</p></div>')
        assert [b.index for b in walk_blocks(body)] == [0]
        with_comments = walk_blocks(body, with_comment_runs=True)
        assert [(b.index, b.legacy_text) for b in with_comments] == [(None, 'hidden'), (0, 'One')]

    def test_processing_instruction_ignored(self):
        assert _texts('<p>Seen</p><?marker unseen?>') == ['Seen']

    def test_script_and_style_text_excluded(self):
        blocks = walk_blocks(body_of('<p>Kept<script>dropped()</script></p>Run<style>x{}</style>'))
        assert [b.text for b in blocks] == ['Kept', 'Run']

    def test_blocks_without_text_dropped(self):
        assert _texts('<div class="spacer"></div><hr/><p>Real</p>  <br/>  ') == ['Real']

    def test_indexes_and_fingerprints(self):
        blocks = walk_blocks(body_of('<p>A  b</p>Run<p>C</p>'))
        assert [b.index for b in blocks] == [0, 1, 2]
        assert blocks[0].fingerprint == fingerprint('Ab') == fingerprint(' A\nb ')
        assert len(blocks[0].fingerprint) == 16
        assert blocks[0].fingerprint != blocks[2].fingerprint

    def test_text_escaped_in_run_html(self):
        assert _htmls('Fish &amp; chips &lt;3') == ['Fish &amp; chips &lt;3']

    def test_inherited_namespace_declarations_left_out(self):
        doc = parse_xml(xhtml(
            '<p class="a"><a epub:type="noteref" href="#n1">1</a></p>'
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text>S</text></svg>'
        ).encode())
        blocks = walk_blocks(find_body(doc))
        assert blocks[0].html == '<p class="a"><a epub:type="noteref" href="#n1">1</a></p>'
        assert blocks[1].html == ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                                  '<text>S</text></svg>')

    def test_serialize_root_unchanged(self):
        root = parse_xml(b'<r xmlns="urn:x"><c/></r>')
        assert serialize(root) == '<r xmlns="urn:x"><c/></r>'


LONG_BARE = ' '.join(f'word{i}' for i in range(1200))


class TestSchema2Segments:

    @staticmethod
    def _segments(html):
        return Segmenter().segment_chapter('ch', body_of(html))

    def test_bare_text_with_br(self):
        segs = self._segments('<div>First line<br/>Second line</div><p>After.</p>')
        assert len(segs) == 1
        assert segs[0].html == 'First line\nSecond line\n<p>After.</p>'
        assert segs[0].text == 'First line\nSecond line\nAfter.'
        assert (segs[0].block_start, segs[0].block_end) == (0, 3)
        assert segs[0].fingerprints == [fingerprint('First line'), fingerprint('Second line'),
                                        fingerprint('After.')]

    def test_long_bare_text_is_one_block(self):
        """No sentence splitting: bare text is never divided."""
        segs = self._segments(LONG_BARE)
        assert len(segs) == 1
        assert segs[0].html == LONG_BARE
        assert segs[0].word_count == 1200

    def test_long_bare_text_with_br_grouped_by_blocks(self):
        lines = [' '.join(f'w{j}x{i}' for i in range(60)) for j in range(20)]
        segs = self._segments('<br/>'.join(lines))
        assert [(s.block_start, s.block_end) for s in segs] == [
            (0, 5), (5, 10), (10, 15), (15, 20)]
        assert all(s.word_count <= MAX_WORDS for s in segs)
        assert '\n'.join(s.html for s in segs) == '\n'.join(lines)

    def test_body_level_comment(self):
        segs = self._segments('<p>One.</p><!-- remark -->Bare text.<p>Two.</p>')
        assert len(segs) == 1
        assert 'remark' not in segs[0].html and 'remark' not in segs[0].text
        assert segs[0].block_end == 3

    def test_ranges_contiguous_and_cover_all_blocks(self):
        html = ('<h1>T</h1>' + ''.join(f'<p>{" ".join(["w"] * 120)}</p>' for _ in range(7))
                + '<pre>x = 1  # one</pre><table><tr><td>c</td></tr></table>Tail')
        segs = self._segments(html)
        ends = [0] + [s.block_end for s in segs]
        assert [s.block_start for s in segs] == ends[:-1]
        assert ends[-1] == len(walk_blocks(body_of(html)))
        assert [s.segment_type for s in segs].count(SegmentType.CODE) == 1
        assert [s.standalone for s in segs] == [True, False, False, False, True, True, False]

    def test_code_text_kept_verbatim(self):
        segs = self._segments('<pre>  def f():\n      return 1\n</pre>')
        assert segs[0].segment_type == SegmentType.CODE
        assert segs[0].text == '  def f():\n      return 1\n'

    def test_run_of_one_code_element_is_code(self):
        segs = self._segments('<p>Intro.</p> <code>x = 2</code> ')
        assert [s.segment_type for s in segs] == [SegmentType.TEXT, SegmentType.CODE]
        assert segs[1].text == 'x = 2'

    def test_run_with_code_and_text_is_text(self):
        segs = self._segments('Call <code>f()</code> twice.')
        assert [s.segment_type for s in segs] == [SegmentType.TEXT]

    def test_resegmenting_is_deterministic(self, raw_epub):
        def run():
            ex = EPUBExtractor(raw_epub)
            ex.load()
            out = []
            for ch in ex.extract_chapters():
                for b in walk_blocks(ch.body):
                    out.append((ch.id, b.index, b.fingerprint, b.html))
                for s in Segmenter().segment_chapter(ch.id, ch.body):
                    out.append((s.id, s.block_start, s.block_end, tuple(s.fingerprints), s.html))
            return out
        first, second = run(), run()
        assert first == second
        assert len(first) > 5


class TestRawPackage:

    def test_spine_order_nav_skipped_duplicates_removed(self, raw_epub):
        ex = EPUBExtractor(raw_epub)
        ex.load()
        assert [(s.id, s.is_nav) for s in ex.spine] == [('nav', True), ('one', False), ('two', False)]
        chapters = ex.extract_chapters()
        assert [c.id for c in chapters] == ['one', 'two']
        assert [c.order for c in chapters] == [1, 2]
        assert ex.failed == []

    def test_subfolder_and_percent_encoded_href(self, raw_epub):
        ex = EPUBExtractor(raw_epub)
        ex.load()
        assert [s.path for s in ex.spine] == [
            'OEBPS/nav.xhtml', 'OEBPS/Text/one.xhtml', 'OEBPS/Text/chapter two.xhtml']
        chapters = ex.extract_chapters()
        assert chapters[1].title == 'Second Evening'
        assert chapters[1].path == 'OEBPS/Text/chapter two.xhtml'

    def test_metadata(self, raw_epub):
        ex = EPUBExtractor(raw_epub)
        ex.load()
        assert ex.get_metadata() == {'title': 'The Lantern Keeper',
                                     'author': 'Invented Author', 'language': 'en'}

    def test_chapter_one_blocks(self, raw_epub):
        ex = EPUBExtractor(raw_epub)
        ex.load()
        one = ex.extract_chapters()[0]
        assert one.title == 'The Lantern Keeper'
        texts = [b.text for b in walk_blocks(one.body)]
        assert texts == ['The Lantern Keeper', 'Mira climbed the spiral stairs at dusk.',
                         'A line of bare text under the body.', 'First line of the song',
                         'Second line of the song', 'Third line of the song']
        assert 'editor note' not in one.text_content
        # inline SVG has no text: no block, and its attribute case is kept
        assert one.body.xpath('.//*[local-name()="svg"]')[0].get('viewBox') == '0 0 10 10'

    def test_unparseable_and_missing_chapters_reported(self, tmp_path):
        items = [('a', 'a.xhtml', 'application/xhtml+xml', None),
                 ('b', 'b.xhtml', 'application/xhtml+xml', None),
                 ('c', 'c.xhtml', 'application/xhtml+xml', None),
                 ('img', 'i.png', 'image/png', None)]
        members = {'OEBPS/a.xhtml': xhtml('<p>Fine.</p>'),
                   'OEBPS/b.xhtml': xhtml('<p>Broken &nbsp; entity.</p>'),
                   'OEBPS/i.png': b'png'}
        path = write_raw_epub(tmp_path / 'b.epub', items, ['a', 'b', 'c', 'img'], members)
        ex = EPUBExtractor(path)
        ex.load()
        assert [c.id for c in ex.extract_chapters()] == ['a']
        # review round 1: the image in the spine is reported, not dropped
        assert [(cid, why.split(':')[0]) for cid, why in ex.failed] == [
            ('img', 'media type image/png is not XHTML'),
            ('b', 'not well-formed XML'), ('c', 'missing from the archive')]

    def test_chapter_without_body_or_text_skipped(self, tmp_path):
        items = [('a', 'a.xhtml', 'application/xhtml+xml', None),
                 ('b', 'b.xhtml', 'application/xhtml+xml', None),
                 ('c', 'c.xhtml', 'application/xhtml+xml', None)]
        members = {'OEBPS/a.xhtml': '<html xmlns="http://www.w3.org/1999/xhtml"/>',
                   'OEBPS/b.xhtml': xhtml('<div><img src="x.png" alt=""/></div>'),
                   'OEBPS/c.xhtml': xhtml('<p>Text.</p>')}
        path = write_raw_epub(tmp_path / 'b.epub', items, ['a', 'b', 'c'], members)
        ex = EPUBExtractor(path)
        ex.load()
        assert [c.id for c in ex.extract_chapters()] == ['c']
        assert ex.failed == []

    def test_opf_at_archive_root(self, tmp_path):
        items = [('a', 'a.xhtml', 'application/xhtml+xml', None)]
        path = write_raw_epub(tmp_path / 'r.epub', items, ['a'],
                              {'a.xhtml': xhtml('<p>Root.</p>')}, opf_path='content.opf')
        ex = EPUBExtractor(path)
        ex.load()
        assert [s.path for s in ex.spine] == ['a.xhtml']

    def test_missing_metadata_is_unknown(self, tmp_path):
        items = [('a', 'a.xhtml', 'application/xhtml+xml', None)]
        path = write_raw_epub(tmp_path / 'r.epub', items, ['a'],
                              {'OEBPS/a.xhtml': xhtml('<p>x</p>')}, title='', author=' ')
        ex = EPUBExtractor(path)
        ex.load()
        assert ex.get_metadata()['title'] == 'Unknown'
        assert ex.get_metadata()['author'] == 'Unknown'

    def test_not_loaded(self, raw_epub):
        ex = EPUBExtractor(raw_epub)
        with pytest.raises(RuntimeError, match='not loaded'):
            ex.get_metadata()
        with pytest.raises(RuntimeError, match='not loaded'):
            ex.extract_chapters()

    def test_written_archive_is_raw(self, raw_epub):
        with zipfile.ZipFile(raw_epub) as z:
            assert z.namelist()[0] == 'mimetype'
            assert z.read('OEBPS/Text/one.xhtml').decode().count('<link rel="stylesheet"') == 1


# --- Mutation survivors: every rule spelled out --------------------------------

ALL_BLOCK_TAGS = [
    'address', 'article', 'aside', 'blockquote', 'details', 'dialog', 'dd', 'div', 'dl', 'dt',
    'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'header', 'hgroup', 'hr', 'li', 'main', 'nav', 'ol', 'p', 'pre', 'section', 'summary',
    'table', 'ul']


class TestRules:

    @pytest.mark.parametrize('tag', ALL_BLOCK_TAGS)
    def test_block_tag_ends_a_run(self, tag):
        assert _htmls(f'a<{tag}>b</{tag}>c') == ['a', f'<{tag}>b</{tag}>', 'c']

    @pytest.mark.parametrize('tag', ['span', 'em', 'a', 'code', 'sup', 'small'])
    def test_inline_tag_stays_in_the_run(self, tag):
        assert _htmls(f'a<{tag}>b</{tag}>c') == [f'a<{tag}>b</{tag}>c']

    @pytest.mark.parametrize('tag', ['div', 'section', 'article'])
    def test_descended_tags(self, tag):
        assert _htmls(f'<{tag}><p>a</p><p>b</p></{tag}>') == ['<p>a</p>', '<p>b</p>']

    def test_greater_than_escaped(self):
        assert _htmls('a &gt; b') == ['a &gt; b']

    def test_empty_comment_is_no_comment_run(self):
        blocks = walk_blocks(body_of('<div><!----><p>x</p></div>'), with_comment_runs=True)
        assert [b.index for b in blocks] == [0]

    @pytest.mark.parametrize('tag', ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table'])
    def test_standalone_tags(self, tag):
        segs = Segmenter().segment_chapter('ch', body_of(f'<p>a</p><{tag}>b</{tag}><p>c</p>'))
        assert [(s.block_start, s.standalone) for s in segs] == [(0, False), (1, True),
                                                                  (2, False)]

    @staticmethod
    def _words(n, mark):
        return ' '.join(f'{mark}{i}' for i in range(n))

    def test_block_that_would_pass_max_starts_a_new_segment(self):
        html = f'<p>{self._words(250, "a")}</p><p>{self._words(260, "b")}</p>'
        segs = Segmenter().segment_chapter('ch', body_of(html))
        assert [s.word_count for s in segs] == [250, 260]

    def test_block_reaching_exactly_max_joins(self):
        html = f'<p>{self._words(250, "a")}</p><p>{self._words(250, "b")}</p>'
        segs = Segmenter().segment_chapter('ch', body_of(html))
        assert [s.word_count for s in segs] == [MAX_WORDS]

    def test_entities_not_expanded(self):
        # Review round 1: the reference stays an unexpanded node, which the
        # extractor reports (TestEntities) instead of segmenting without it
        doc = parse_xml(b'<?xml version="1.0"?><!DOCTYPE html [<!ENTITY x "EXPANDED">]>'
                        b'<html><body><p>a &x; b</p></body></html>')
        assert unresolved_entities(doc) == ['x']
        assert 'EXPANDED' not in ''.join(doc.itertext())

    def test_text_content_and_title(self, tmp_path):
        items = [('a', 'a.xhtml', 'application/xhtml+xml', None)]
        body = '<h3>A\n  small   title</h3><p>One  two</p>Three<!-- x -->'
        path = write_raw_epub(tmp_path / 'b.epub', items, ['a'],
                              {'OEBPS/a.xhtml': xhtml(body)})
        ex = EPUBExtractor(path)
        ex.load()
        chapter = ex.extract_chapters()[0]
        assert chapter.title == 'A small title'
        assert chapter.text_content == 'A small title One two Three'

    def test_spine_entries_after_skipped_ones_kept(self, tmp_path):
        items = [('a', 'a.xhtml', 'application/xhtml+xml', None),
                 ('img', 'i.png', 'image/png', None),
                 ('b', 'b.xhtml', 'application/xhtml+xml', None)]
        members = {'OEBPS/a.xhtml': xhtml('<p>A.</p>'), 'OEBPS/b.xhtml': xhtml('<p>B.</p>'),
                   'OEBPS/i.png': b'png'}
        path = write_raw_epub(tmp_path / 'b.epub', items, ['a', 'a', 'unknown', 'img', 'b'],
                              members)
        ex = EPUBExtractor(path)
        ex.load()
        assert [s.id for s in ex.spine] == ['a', 'b']

    def test_fresh_extractor(self):
        ex = EPUBExtractor('unused.epub')
        assert (ex.spine, ex.failed) == ([], [])


# --- Review round 1 -------------------------------------------------------------

ENTITY_DOCS = {
    'internal subset': ('<?xml version="1.0" encoding="utf-8"?>\n'
                        '<!DOCTYPE html [<!ENTITY term "harbour">]>\n'
                        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c</title></head>'
                        '<body><p>Fine.</p>The &term; opened.</body></html>'),
    'xhtml 1.1 doctype': ('<?xml version="1.0" encoding="utf-8"?>\n'
                          '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
                          '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n'
                          '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c</title></head>'
                          '<body><p>a&mdash;b and &rsquo;c</p></body></html>'),
}


class TestEntities:
    """An unresolved entity reference anywhere in the body: the chapter is
    not segmented and is reported (entities are never expanded)."""

    @pytest.mark.parametrize('case, names', [('internal subset', '&term;'),
                                             ('xhtml 1.1 doctype', '&mdash;, &rsquo;')])
    def test_chapter_reported(self, tmp_path, case, names):
        items = [('a', 'a.xhtml', 'application/xhtml+xml', None),
                 ('b', 'b.xhtml', 'application/xhtml+xml', None)]
        members = {'OEBPS/a.xhtml': xhtml('<p>Clean.</p>'), 'OEBPS/b.xhtml': ENTITY_DOCS[case]}
        path = write_raw_epub(tmp_path / 'e.epub', items, ['a', 'b'], members)
        ex = EPUBExtractor(path)
        ex.load()
        assert [c.id for c in ex.extract_chapters()] == ['a']
        assert ex.failed == [('b', f'unresolved entity reference: {names}')]

    def test_entity_outside_body_ignored(self, tmp_path):
        doc = ('<?xml version="1.0"?>\n<!DOCTYPE html [<!ENTITY t "x">]>\n'
               '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>&t;</title></head>'
               '<body><p>Clean.</p></body></html>')
        items = [('a', 'a.xhtml', 'application/xhtml+xml', None)]
        path = write_raw_epub(tmp_path / 'e.epub', items, ['a'], {'OEBPS/a.xhtml': doc})
        ex = EPUBExtractor(path)
        ex.load()
        assert [c.id for c in ex.extract_chapters()] == ['a'] and ex.failed == []


def _encrypted(path):
    """Mark every member of a ZIP as encrypted (general purpose bit 0)."""
    data = bytearray(open(path, 'rb').read())
    for sig, offset in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):
        start = 0
        while (start := data.find(sig, start)) != -1:
            data[start + offset] |= 1
            start += 4
    open(path, 'wb').write(bytes(data))
    return path


class TestPackageErrors:
    """A package that cannot be read gives one EPUBError with a message."""

    def _write(self, tmp_path, **kw):
        items = kw.pop('items', [('a', 'a.xhtml', 'application/xhtml+xml', None)])
        members = kw.pop('members', {'OEBPS/a.xhtml': xhtml('<p>A.</p>')})
        return write_raw_epub(tmp_path / 'p.epub', items, ['a'], members, **kw)

    def _error(self, path):
        with pytest.raises(EPUBError) as e:
            EPUBExtractor(str(path)).load()
        return str(e.value)

    def test_not_a_zip(self, tmp_path):
        path = tmp_path / 'x.epub'
        path.write_bytes(b'not a zip')
        assert self._error(path).startswith('無法讀取 EPUB：')

    def test_unreadable_file(self, tmp_path):
        assert self._error(tmp_path / 'missing.epub').startswith('無法讀取 EPUB：')

    def test_no_container(self, tmp_path):
        path = tmp_path / 'x.epub'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('mimetype', 'application/epub+zip')
        assert self._error(path) == "EPUB 缺少檔案：\"There is no item named " \
            "'META-INF/container.xml' in the archive\""

    def test_no_rootfile(self, tmp_path):
        path = tmp_path / 'x.epub'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('META-INF/container.xml', '<container xmlns="urn:x"><rootfiles/></container>')
        assert self._error(path) == 'container.xml 沒有指出 OPF（rootfile）'

    def test_rootfile_without_path(self, tmp_path):
        path = tmp_path / 'x.epub'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('META-INF/container.xml',
                       '<container xmlns="urn:x"><rootfiles><rootfile/></rootfiles></container>')
        assert self._error(path) == 'container.xml 沒有指出 OPF（rootfile）'

    def test_package_not_well_formed(self, tmp_path):
        path = tmp_path / 'x.epub'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('META-INF/container.xml', '<container><rootfiles>')
        assert self._error(path).startswith('EPUB 的套件文件不是格式正確的 XML：')

    def test_item_without_href(self, tmp_path):
        path = self._write(tmp_path)
        with zipfile.ZipFile(path) as z:
            opf = z.read('OEBPS/content.opf').decode()
        opf = opf.replace('href="a.xhtml"', '')
        rewritten = tmp_path / 'y.epub'
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(rewritten, 'w') as dst:
            for name in src.namelist():
                dst.writestr(name, opf if name == 'OEBPS/content.opf' else src.read(name))
        assert self._error(rewritten) == 'manifest 項目 a 沒有 href'

    def test_encrypted_archive(self, tmp_path):
        path = _encrypted(self._write(tmp_path))
        message = self._error(path)
        assert message.startswith('無法讀取 META-INF/container.xml：') and 'encrypted' in message

    def test_encrypted_chapter_reported(self, tmp_path):
        items = [('a', 'a.xhtml', 'application/xhtml+xml', None),
                 ('b', 'b.xhtml', 'application/xhtml+xml', None)]
        path = write_raw_epub(tmp_path / 'p.epub', items, ['a', 'b'],
                              {'OEBPS/a.xhtml': xhtml('<p>A.</p>'),
                               'OEBPS/b.xhtml': xhtml('<p>B.</p>')})
        ex = EPUBExtractor(path)
        ex.load()
        ex._zip.getinfo('OEBPS/b.xhtml').flag_bits |= 1
        assert [c.id for c in ex.extract_chapters()] == ['a']
        assert [(cid, why.split('：')[0]) for cid, why in ex.failed] == [
            ('b', 'unreadable: 無法讀取 OEBPS/b.xhtml')]

    def test_percent_encoded_opf_path(self, tmp_path):
        items = [('a', 'a.xhtml', 'application/xhtml+xml', None)]
        path = write_raw_epub(tmp_path / 'p.epub', items, ['a'],
                              {'My Book/a.xhtml': xhtml('<p>A.</p>')},
                              opf_path='My Book/content.opf')
        rewritten = tmp_path / 'q.epub'
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(rewritten, 'w') as dst:
            for name in src.namelist():
                data = src.read(name)
                if name == 'META-INF/container.xml':
                    data = data.replace(b'full-path="My Book/', b'full-path="My%20Book/')
                dst.writestr(name, data)
        ex = EPUBExtractor(str(rewritten))
        ex.load()
        assert [c.path for c in ex.extract_chapters()] == ['My Book/a.xhtml']

    def test_non_xhtml_spine_entry_reported(self, tmp_path):
        items = [('a', 'a.xhtml', 'application/xhtml+xml', None),
                 ('h', 'h.html', 'text/html', None)]
        path = write_raw_epub(tmp_path / 'p.epub', items, ['h', 'a'],
                              {'OEBPS/a.xhtml': xhtml('<p>A.</p>'),
                               'OEBPS/h.html': '<html><body><p>H.</p></body></html>'})
        ex = EPUBExtractor(path)
        ex.load()
        assert [c.id for c in ex.extract_chapters()] == ['a']
        assert ex.failed == [('h', 'media type text/html is not XHTML')]
