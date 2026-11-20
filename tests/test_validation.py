"""Tests for protection and validation (src/core/validation.py, plan 4.1)."""

from types import SimpleNamespace

import pytest

from src.core import validation
from src.core.validation import (
    Protected, check_code, check_html, check_segment, code_lines, declarations, protect,
    restore, top_level_units,
)
from src.epub.blocks import fingerprint

SOURCE = ('<p class="c3" id="p12">See <a href="ch02.xhtml#fn3" id="r3"><sup>3</sup></a>\n'
          'for the <em class="italic">core idea</em>.</p>')


def codes(result):
    return [i.code for i in result.issues]


class TestProtect:

    def test_plan_example(self):
        p = protect(SOURCE)
        assert p.html == ('<p k="1">See <a k="2"><sup>3</sup></a>\n'
                          'for the <em k="3">core idea</em>.</p>')
        assert p.keys == {'1': ('p', ' class="c3" id="p12"'),
                          '2': ('a', ' href="ch02.xhtml#fn3" id="r3"'),
                          '3': ('em', ' class="italic"')}

    def test_bare_top_level_block_keyed_inner_bare_tag_not(self):
        p = protect('<h2>Title <b>x</b></h2>')
        assert p.html == '<h2 k="1">Title <b>x</b></h2>'
        assert p.keys == {'1': ('h2', '')}

    def test_runs_wrapped(self):
        html = '<p>One.</p>\nBare <em>run</em> text\n<p>Two.</p>\nLast run'
        p = protect(html)
        assert p.html == ('<p k="1">One.</p>\n<run k="2">Bare <em>run</em> text</run>\n'
                          '<p k="3">Two.</p>\n<run k="4">Last run</run>')
        assert p.keys['2'] == ('run', None) and p.keys['4'] == ('run', None)

    def test_run_starting_and_ending_with_an_element(self):
        p = protect('<a href="x">A</a> b <i>c</i>')
        assert p.html == '<run k="1"><a k="2">A</a> b <i>c</i></run>'

    def test_self_closing_and_namespaced_attributes(self):
        html = '<p>A<img src="i.png" alt="x"/> <span epub:type="noteref" xml:lang="en">b</span></p>'
        p = protect(html)
        assert p.html == '<p k="1">A<img k="2"/> <span k="3">b</span></p>'
        assert restore(p.html, p) == html

    def test_top_level_self_closing_block(self):
        p = protect('<p>a</p>\n<hr class="x"/>\n<hr/>')
        assert p.html == '<p k="1">a</p>\n<hr k="2"/>\n<hr k="3"/>'

    @pytest.mark.parametrize('html', [
        SOURCE,
        '<p>One.</p>\nBare <em>run</em> text\n<p>Two.</p>',
        "<div class='q' >x</div>\n<p a = \"1\"  b='2' >y</p>",
        '<p>a<!-- note <b> --><b>c</b><?pi x?></p>',
        '<pre><code>if a &lt; b:\n    pass</code></pre>',
        '<svg:svg xmlns:svg="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><svg:g/></svg:svg>',
        'text only, no markup',
        '',
        '<p>unbalanced</div></p>',
        '  <p>edges</p>  ',
        '<p><![CDATA[<b>]]></p>',
        '<p>x</p>\n  \n<p>y</p>',
    ])
    def test_round_trip_exact(self, html):
        assert restore(protect(html).html, protect(html)) == html

    def test_units(self):
        html = '<p>a</p>\n  Bare <em>x</em>  \n<hr/>\n<b>y</b>'
        assert top_level_units(html) == [(0, 8, False), (11, 26, True), (29, 34, False),
                                         (35, 43, True)]

    def test_comment_at_the_edge_of_a_run_stays_outside(self):
        assert top_level_units('<!--c-->text<!--d-->') == [(8, 12, True)]

    def test_unbalanced_end_tag_does_not_go_below_zero(self):
        assert top_level_units('</b><p>x</p>') == [(4, 12, False)]


class TestRestore:

    def test_keys_get_their_attributes_back(self):
        p = protect(SOURCE)
        reply = '<p k="1">見 <a k="2"><sup>3</sup></a> 的<em k="3">核心概念</em>。</p>'
        assert restore(reply, p) == ('<p class="c3" id="p12">見 <a href="ch02.xhtml#fn3" '
                                     'id="r3"><sup>3</sup></a> 的<em class="italic">核心概念</em>。</p>')

    def test_wrapper_removed_with_its_end_tag(self):
        p = protect('<p>A</p>\nB <b>c</b>')
        assert restore('<p k="1">甲</p>\n<run k="2">乙<b>丙</b></run>', p) == \
            '<p>甲</p>\n乙<b>丙</b>'
        assert restore('<run k="2"/>x', p) == 'x'

    @pytest.mark.parametrize('reply', [
        '<p k="9">x</p>',             # unknown key
        '<b k="1">x</b>',             # the key of another tag
        '<p k="1" class="z">x</p>',   # another attribute
        "<p k='1' >x</p>",
    ])
    def test_tags_that_are_not_a_plain_key_stay(self, reply):
        p = Protected('', {'1': ('p', ' id="a"')})
        if reply == "<p k='1' >x</p>":  # single quotes and a space are still the key
            assert restore(reply, p) == '<p id="a">x</p>'
        else:
            assert restore(reply, p) == reply

    def test_self_closing_reply_keeps_its_form(self):
        p = Protected('', {'1': ('img', ' src="a"')})
        assert restore('<img k="1"/>', p) == '<img src="a"/>'
        assert restore('<img k="1"></img>', p) == '<img src="a"></img>'

    def test_end_without_start_kept(self):
        assert restore('a</b>', Protected('', {})) == 'a</b>'


class TestDeclarations:

    def test_prefixes_of_both_texts(self):
        nsmap = declarations('<p epub:type="x">a</p>', '<m:math/>')
        assert nsmap == {None: validation.XHTML_NS, 'epub': 'urn:x-prefix:epub',
                         'm': 'urn:x-prefix:m'}

    def test_xml_prefix_not_declared(self):
        assert declarations('<p xml:lang="en">a</p>') == {None: validation.XHTML_NS}


class TestCheckHtml:

    def test_accepted_with_pieces(self):
        source = '<p class="a">One <em>x</em>.</p>\nBare <b>y</b>'
        result = check_html(source, '<p class="a"><em>甲</em>一。</p>\n裸<b>乙</b>')
        assert result.issues == [] and result.blocking == []
        assert len(result.pieces) == 2

    def test_inline_order_not_checked(self):
        assert codes(check_html('<p><b>a</b> <i>b</i></p>', '<p><i>乙</i><b>甲</b></p>')) == []

    @pytest.mark.parametrize('translation, code', [
        ('<p>未關閉', 'not_well_formed'),
        ('<p>甲</p><p>乙</p>', 'structure_mismatch'),
        ('<div>甲</div>', 'structure_mismatch'),
        ('<p>甲<br/></p><br/>', 'structure_mismatch'),
        ('<p><b/></p>', 'empty_block'),
        ('<p><b>甲</b><span>乙</span></p>', 'unknown_tag'),
        ('<p>甲乙</p>', 'tag_count'),
        ('<p><b>甲</b><b>乙</b></p>', 'tag_count'),
        ('<p><b class="x">甲</b></p>', 'extra_attributes'),
    ])
    def test_structural_rules(self, translation, code):
        result = check_html('<p>One <b>two</b></p>', translation)
        assert codes(result)[0] == code
        assert result.pieces is None and all(i.blocking for i in result.issues)
        assert all(i.kind == 'structure' for i in result.issues)

    def test_key_missing_moved_or_repeated(self):
        source = '<p>A <a id="x">b</a></p>\n<p>C</p>'
        assert codes(check_html(source, '<p>甲乙</p>\n<p>丙</p>')) == ['key_mismatch']
        assert codes(check_html(source, '<p>甲乙</p>\n<p><a id="x">丙</a></p>')) == \
            ['key_mismatch', 'extra_attributes']
        assert codes(check_html(source, '<p><a id="x">甲</a><a id="x">乙</a></p>\n<p>丙</p>')) \
            == ['key_mismatch']

    def test_block_attributes_compared(self):
        assert codes(check_html('<p class="a">x</p>', '<p>甲</p>')) == \
            ['key_mismatch', 'tag_count']

    def test_details(self):
        result = check_html('<p>A <a id="x">b</a> <b>c</b></p>', '<p>甲 <span>乙</span></p>')
        assert [i.detail for i in result.issues] == [
            '出現原文沒有的標籤：<span>',
            '第 1 個區塊帶 k 屬性的 <a>：原文 1 個，譯文 0 個',
            '第 1 個區塊不帶屬性的 <b>：原文 1 個，譯文 0 個',
        ]
        result = check_html('<p>A</p>\nB', '<p>甲</p>')
        assert result.issues[0].detail == '頂層區塊與原文不符：原文為 p、run，譯文為 p'
        assert check_html('<p>A</p>', '').issues[0].detail == \
            '頂層區塊與原文不符：原文為 p，譯文為 （無）'
        assert check_html('<p>A</p>', 'x<br/>').issues[0].detail == \
            '頂層區塊與原文不符：原文為 p，譯文為 頂層有 <br>'
        assert check_html('<p>A</p>\n<p>B</p>', '<p>甲</p>\n<p> </p>').issues[0].detail == \
            '第 2 個區塊沒有文字'
        assert check_html('<p>A</p>', '<p>甲<b/></p>').issues[0].detail == \
            '出現原文沒有的標籤：<b>'
        assert check_html('<p>A <b class="q">x</b></p>', '<p>甲<b>乙</b></p>').issues[1].detail \
            == '第 1 個區塊不帶屬性的 <b>：原文 0 個，譯文 1 個'
        assert check_html('<p>A</p>', '<p class="z">甲</p>').issues[0].detail == \
            '第 1 個區塊的 <p> 帶有原文沒有的屬性'

    def test_not_well_formed_detail(self):
        detail = check_html('<p>A</p>', '<p>甲').issues[0].detail
        assert detail.startswith('不是格式正確的 XML：')

    def test_runs_cut_by_fingerprints(self):
        # Two runs separated by <br> in the chapter are stored one per line
        source = 'Line <b>one</b>\nLine two\n<p>Para.</p>'
        fps = [fingerprint('Line one'), fingerprint('Line two'), fingerprint('Para.')]
        good = check_html(source, '第<b>一</b>行\n第二行\n<p>段。</p>', fps)
        assert good.blocking == [] and len(good.pieces) == 3
        merged = check_html(source, '第<b>一</b>行第二行\n<p>段。</p>', fps)
        assert codes(merged) == ['structure_mismatch']
        assert merged.issues[0].detail.endswith('（各行對應原文的各行）')
        # The <b> moved to the other line
        assert codes(check_html(source, '第一行\n第<b>二</b>行\n<p>段。</p>', fps)) == \
            ['tag_count']

    def test_one_run_with_newlines_is_one_piece(self):
        source = 'A long\nrun'
        result = check_html(source, '一段\n文字', [fingerprint('A long run')])
        assert result.blocking == [] and len(result.pieces) == 1

    def test_source_that_cannot_be_cut(self):
        assert codes(check_html('<p>A</p>', '<p>甲</p>', ['0' * 16])) == ['ambiguous_source']
        assert codes(check_html('<p>A', '<p>甲</p>')) == ['ambiguous_source']
        assert codes(check_html('A<br/>B', 'x')) == ['ambiguous_source']
        assert codes(check_html('<p>A</p>\n<p>B</p>', '<p>甲</p>\n<p>乙</p>',
                                [fingerprint('A')])) == ['ambiguous_source']

    def test_element_where_a_run_was(self):
        source = 'Line one\nLine two'
        fps = [fingerprint('Line one'), fingerprint('Line two')]
        assert codes(check_html(source, '<p>一</p>', fps)) == ['structure_mismatch']
        assert codes(check_html('<p>A</p>', '甲')) == ['structure_mismatch']

    def test_namespaced_names_compared(self):
        source = '<p>A <span epub:type="noteref">1</span></p>'
        assert check_html(source, '<p>甲<span epub:type="noteref">1</span></p>').blocking == []
        nsmap = {None: validation.XHTML_NS, 'epub': 'http://www.idpf.org/2007/ops'}
        assert check_html(source, '<p>甲<span epub:type="noteref">1</span></p>',
                          nsmap=nsmap).blocking == []

    def test_comments_ignored(self):
        assert check_html('<p>A <b>b</b></p>', '<p>甲<!-- x --><b>乙</b></p>').blocking == []
        assert check_html('A <b>b</b>', '<!-- x -->甲<b>乙</b>').blocking == []


class TestQuality:

    def test_identical_to_a_source_with_words(self):
        result = check_html('<p>Some words</p>', '<p>Some  words</p>')
        assert [(i.code, i.kind, i.blocking) for i in result.issues] == \
            [('untranslated', 'quality', False)]
        assert result.pieces is not None
        assert check_html('<p>3.14</p>', '<p>3.14</p>').issues == []
        assert check_html('<p>A</p>', '<p>A</p>').issues == []

    def test_length_ratio(self):
        low, high = validation.LENGTH_RATIO_BAND
        n = validation.LENGTH_RATIO_MIN_CHARS
        source = f'<p>{"a" * n}</p>'
        assert codes(check_html(source, f'<p>{"字" * int(n * 0.4)}</p>')) == []
        assert codes(check_html(source, f'<p>{"字" * int(n * low)}</p>')) == []
        assert codes(check_html(source, f'<p>{"字" * int(n * high)}</p>')) == []
        short = check_html(source, f'<p>{"字" * (int(n * low) - 1)}</p>')
        assert codes(short) == ['length_ratio'] and not short.issues[0].blocking
        assert short.issues[0].detail == '譯文長度是原文的 0.24 倍（預期 0.25–0.8）'
        assert codes(check_html(source, f'<p>{"字" * (int(n * high) + 1)}</p>')) == \
            ['length_ratio']
        shorter = f'<p>{"a" * (n - 1)}</p>'
        assert codes(check_html(shorter, '<p>字</p>')) == []

    def test_band_values(self):
        assert validation.LENGTH_RATIO_BAND == (0.25, 0.8)
        assert validation.LENGTH_RATIO_MIN_CHARS == 200


class TestCode:

    def test_comment_lines_removed_from_both(self):
        source = '# load\nx = 1\n    // inner\nif x:\n    y()\n'
        assert check_code(source, '# 載入\nx = 1\n    // 內部\nif x:\n    y()').issues == []
        assert check_code(source, 'x = 1\nif x:\n    y()  \n\n').issues == []

    @pytest.mark.parametrize('translation', [
        'x = 1\nif x:\n  y()',                      # indentation
        'x = 1  # 一\nif x:\n    y()',             # an end-of-line comment is code
        'Here it is:\nx = 1\nif x:\n    y()',       # commentary
        'x = 1\nif x:\n    y()\n```',
    ])
    def test_changed_code_refused(self, translation):
        result = check_code('# c\nx = 1\nif x:\n    y()', translation)
        assert [(i.kind, i.code, i.blocking) for i in result.issues] == \
            [('structure', 'code_changed', True)]
        assert result.pieces is None

    def test_comment_line_rule(self):
        assert code_lines('#include <a>\n#!shebang\n#\n# x\n#define Y\n  //z\nend') == \
            ['#include <a>', '#define Y', 'end']
        assert code_lines('\n\n a \n\n') == [' a']


class TestCheckSegment:

    def test_by_type(self):
        code = SimpleNamespace(segment_type='code', html='<pre>x = 1</pre>', text='x = 1',
                               fingerprints=[])
        assert check_segment(code, 'x = 1').issues == []
        assert codes(check_segment(code, '<pre>x = 1</pre>')) == ['code_changed']
        text = SimpleNamespace(segment_type='text', html='<p>A</p>', text='A',
                               fingerprints=[fingerprint('A')])
        assert check_segment(text, '<p>甲</p>').blocking == []
        assert codes(check_segment(text, '<p>甲</p>', fingerprints=['0' * 16])) == \
            ['ambiguous_source']

    def test_schema_1_segment_without_fingerprints(self):
        seg = SimpleNamespace(segment_type='text', html='A\nB', text='A B', fingerprints=[])
        assert check_segment(seg, '甲乙').blocking == []
