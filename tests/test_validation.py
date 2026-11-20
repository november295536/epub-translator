"""Tests for protection and validation (src/core/validation.py, plan 4.1)."""

from types import SimpleNamespace

import pytest

from src.core import validation
from src.core.validation import (
    Key, Protected, check_code, check_html, check_keys, check_segment, code_lines, declarations,
    protect,
    restore, top_level_units,
)
from src.epub.blocks import fingerprint

SOURCE = ('<p class="c3" id="p12">See <a href="ch02.xhtml#fn3" id="r3"><sup>3</sup></a>\n'
          'for the <em class="italic">core idea</em>.</p>')


def entries(protected):
    return {k: (key.name, key.attrs) for k, key in protected.keys.items()}


def codes(result):
    return [i.code for i in result.issues]


class TestProtect:

    def test_plan_example(self):
        p = protect(SOURCE)
        assert p.html == ('<p k="1">See <a k="2"><sup>3</sup></a>\n'
                          'for the <em k="3">core idea</em>.</p>')
        assert entries(p) == {'1': ('p', ' class="c3" id="p12"'),
                          '2': ('a', ' href="ch02.xhtml#fn3" id="r3"'),
                          '3': ('em', ' class="italic"')}

    def test_bare_top_level_block_keyed_inner_bare_tag_not(self):
        p = protect('<h2>Title <b>x</b></h2>')
        assert p.html == '<h2 k="1">Title <b>x</b></h2>'
        assert entries(p) == {'1': ('h2', '')}

    def test_runs_wrapped(self):
        html = '<p>One.</p>\nBare <em>run</em> text\n<p>Two.</p>\nLast run'
        p = protect(html)
        assert p.html == ('<p k="1">One.</p>\n<run k="2">Bare <em>run</em> text</run>\n'
                          '<p k="3">Two.</p>\n<run k="4">Last run</run>')
        assert entries(p)['2'] == ('run', None) and entries(p)['4'] == ('run', None)
        assert p.top == ['1', '2', '3', '4']

    def test_run_starting_and_ending_with_an_element(self):
        p = protect('<a href="x">A</a> b <i>c</i>')
        assert p.html == '<run k="1"><a k="2">A</a> b <i>c</i></run>'

    def test_self_closing_and_namespaced_attributes(self):
        html = '<p>A<img src="i.png" alt="x"/> <span epub:type="noteref" xml:lang="en">b</span></p>'
        p = protect(html)
        assert p.html == '<p k="1">A<img k="2" alt="x"/> <span k="3">b</span></p>'
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
        p = Protected('', {'1': Key('p', ' id="a"')})
        if reply == "<p k='1' >x</p>":  # single quotes and a space are still the key
            assert restore(reply, p) == '<p id="a">x</p>'
        else:
            assert restore(reply, p) == reply

    def test_self_closing_reply_keeps_its_form(self):
        p = Protected('', {'1': Key('img', ' src="a"')})
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
        assert codes(check_html('<p class="a">x</p>', '<p>甲</p>')) == ['key_mismatch']
        assert codes(check_html('<p>x</p>', '<p class="a">甲</p>')) == ['extra_attributes']

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
            '第 1 個區塊的 <p> 本身的屬性與原文不同'

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
        text.fingerprints = ['0' * 16]
        assert codes(check_segment(text, '<p>甲</p>')) == ['ambiguous_source']

    def test_segment_fingerprints_cut_the_runs(self):
        seg = SimpleNamespace(segment_type='text', html='Line one\nLine two', text='',
                              fingerprints=[fingerprint('Line one'), fingerprint('Line two')])
        assert codes(check_segment(seg, '一二')) == ['structure_mismatch']
        assert check_segment(seg, '一\n二').blocking == []

    def test_schema_1_segment_without_fingerprints(self):
        seg = SimpleNamespace(segment_type='text', html='A\nB', text='A B', fingerprints=[])
        assert check_segment(seg, '甲乙').blocking == []


class TestSurvivorsRun1:
    """Mutation testing, Phase 4 run 1."""

    def test_run_at_the_very_start_wrapped_once(self):
        assert protect('Bare text<p>x</p>').html == '<run k="1">Bare text</run><p k="2">x</p>'

    @pytest.mark.parametrize('html, expected', [
        ('<div><ul><li>a</li></ul><p>b</p></div>', '<div k="1"><ul><li>a</li></ul><p>b</p></div>'),
        ('<div><p>a</p><p>b</p></div>', '<div k="1"><p>a</p><p>b</p></div>'),
    ])
    def test_only_top_level_blocks_keyed(self, html, expected):
        assert protect(html).html == expected

    def test_prefixed_block_name(self):
        assert protect('<h:p>a</h:p>').html == '<h:p k="1">a</h:p>'
        assert validation._local('svg:g') == 'g'

    def test_run_after_the_last_fingerprint(self):
        assert codes(check_html('A\n<p>B</p>\nC', '甲\n<p>乙</p>\n丙',
                                [fingerprint('A'), fingerprint('B')])) == ['ambiguous_source']

    def test_ambiguous_source_issue(self):
        [issue] = check_html('<p>A', '<p>甲</p>').issues
        assert (issue.kind, issue.code, issue.detail, issue.blocking) == \
            ('structure', 'ambiguous_source', '原文無法切成各個區塊', True)

    def test_declarations_of_both_texts_used(self):
        # the source's prefix: the translation without it still parses
        source = '<p>A <span epub:type="noteref">1</span></p>'
        assert codes(check_html(source, '<p>甲<span>1</span></p>')) == \
            ['key_mismatch', 'tag_count']
        # the translation's prefix: an added attribute is reported, not a parse error
        assert codes(check_html('<p>A <span>1</span></p>',
                                '<p>甲<span epub:type="x">1</span></p>')) == \
            ['extra_attributes', 'tag_count']

    def test_own_declarations_in_the_html(self):
        html = '<p>A <svg:svg xmlns:svg="http://www.w3.org/2000/svg"><svg:g/></svg:svg></p>'
        assert declarations(html) == {None: validation.XHTML_NS, 'svg': 'urn:x-prefix:svg'}
        assert check_html(html, html.replace('A', '甲')).blocking == []

    def test_pieces_in_the_xhtml_namespace(self):
        assert check_html('<p>A</p>', '<p>甲</p>').pieces[0].tag == \
            '{http://www.w3.org/1999/xhtml}p'

    def test_comment_inside_an_inline_element_ignored(self):
        assert check_html('A <b>b</b>', '甲<b>乙<!-- c --></b>').blocking == []

    def test_details_list_every_item(self):
        result = check_html('<p>A <b>b</b> <i>c</i></p>\n<p>D</p>\n<p>E</p>',
                            '<p>甲<s>乙</s><u>丙</u></p>\n<p> </p>\n<p> </p>')
        assert [i.detail for i in result.issues] == [
            '第 2、3 個區塊沒有文字',
            '出現原文沒有的標籤：<s>、<u>',
            '第 1 個區塊不帶屬性的 <b>：原文 1 個，譯文 0 個；'
            '第 1 個區塊不帶屬性的 <i>：原文 1 個，譯文 0 個',
        ]
        assert check_html('<p>A</p>\n<p>B</p>', '<p>甲</p>\n<h2>乙</h2>\n<p>丙</p>').issues[0] \
            .detail == '頂層區塊與原文不符：原文為 p、p，譯文為 p、h2、p'

    def test_untranslated_detail(self):
        [issue] = check_html('<p>Some words</p>', '<p>Some words</p>').issues
        assert issue.detail == '譯文與原文相同'

    def test_quality_over_all_blocks(self):
        n = validation.LENGTH_RATIO_MIN_CHARS
        # two blocks, 100 + 100 source characters, 30 + 30 translated: 0.30
        source = f'<p>{"a" * 100}</p>\n<p>{"b" * 100}</p>'
        assert n == 200
        assert check_html(source, f'<p>{"字" * 30}</p>\n<p>{"字" * 30}</p>').issues == []
        assert check_html(source, f'<p>{"字" * 25}</p>\n<p>{"字" * 25}</p>').issues == []
        [issue] = check_html(source, f'<p>{"字" * 24}</p>\n<p>{"字" * 24}</p>').issues
        assert issue.detail == '譯文長度是原文的 0.24 倍（預期 0.25–0.8）'
        # identical over two blocks
        assert codes(check_html('<p>Some</p>\n<p>words</p>', '<p>Some</p>\n<p>words</p>')) \
            == ['untranslated']

    def test_check_code_result(self):
        assert check_code('x', 'x') == validation.Result([])


# --- Phase 4 review round 1 ------------------------------------------------------

class TestTranslatableAttributes:
    """alt, title and aria-label are shown to the model and translated."""

    def test_shown_next_to_the_key(self):
        html = ('<p class="a" title="Tip">See <img src="m.png" alt="A map"/> '
                '<span aria-label="Close" id="x">x</span> <a href="#n">n</a></p>')
        p = protect(html)
        assert p.html == ('<p k="1" title="Tip">See <img k="2" alt="A map"/> '
                          '<span k="3" aria-label="Close">x</span> <a k="4">n</a></p>')
        assert restore(p.html, p) == html

    def test_element_with_only_translatable_attributes_keyed(self):
        p = protect('<p>A <abbr title="Hyper Text">HT</abbr></p>')
        assert p.html == '<p k="1">A <abbr k="2" title="Hyper Text">HT</abbr></p>'
        assert entries(p)['2'] == ('abbr', ' title="Hyper Text"')

    @pytest.mark.parametrize('html', [
        "<p title='a &amp; \"b\"'>x<img alt='' src='s'/></p>",
        '<p>A <img alt="x &lt; y &#38; z" src="s"/></p>',
        '<p>A <img src = "s"  alt = "q" /></p>',
    ])
    def test_round_trip_keeps_quoting_and_entities(self, html):
        p = protect(html)
        assert restore(p.html, p) == html

    @pytest.mark.parametrize('source, reply_alt, restored', [
        # double-quoted source: " and & and < escaped
        ('<p>A <img src="s" alt="A map"/></p>', '地圖 &quot;甲&quot; &amp; &lt;乙',
         '<p>甲 <img src="s" alt="地圖 &quot;甲&quot; &amp; &lt;乙"/></p>'),
        # single-quoted source: the reply's value re-escaped for single quotes
        ("<p>A <img src='s' alt='A map'/></p>", "地圖 &apos;甲&apos; \"乙\"",
         "<p>甲 <img src='s' alt='地圖 &apos;甲&apos; \"乙\"'/></p>"),
        # the reply repeats the value: the stored text is kept as it is
        ('<p>A <img src="s" alt="x &#38; y"/></p>', 'x &#38; y',
         '<p>甲 <img src="s" alt="x &#38; y"/></p>'),
        # numeric references decoded, then escaped
        ('<p>A <img src="s" alt="A"/></p>', '&#x5716;&#22294;>',
         '<p>甲 <img src="s" alt="圖圖>"/></p>'),
    ])
    def test_translated_value_restored(self, source, reply_alt, restored):
        p = protect(source)
        q = "'" if "alt='" in p.html else '"'
        reply = f'<p k="1">甲 <img k="2" alt={q}{reply_alt}{q}/></p>'
        assert check_keys(reply, p) == []
        assert restore(reply, p) == restored
        assert codes(check_html(source, restored)) == []

    def test_reply_with_single_quotes_for_a_double_quoted_source(self):
        p = protect('<p>A <img src="s" alt="A"/></p>')
        assert restore("<p k=\"1\">甲 <img k='2' alt='a \"b\"'/></p>", p) == \
            '<p>甲 <img src="s" alt="a &quot;b&quot;"/></p>'

    def test_two_translatable_attributes(self):
        source = '<p>A <a href="#x" title="T1" aria-label="L1">x</a></p>'
        p = protect(source)
        assert p.html == '<p k="1">A <a k="2" title="T1" aria-label="L1">x</a></p>'
        assert restore('<p k="1">甲<a k="2" aria-label="標" title="題">乙</a></p>', p) == \
            '<p>甲<a href="#x" title="題" aria-label="標">乙</a></p>'

    @pytest.mark.parametrize('reply, code, detail', [
        ('<p k="1">甲<img k="2"/></p>', 'extra_attributes',
         '屬性與原文不符：<img k="2"> 少了 alt 屬性'),
        ('<p k="1">甲<img k="2" alt="x" src="t"/></p>', 'extra_attributes',
         '屬性與原文不符：<img k="2"> 多了 src 屬性'),
        ('<p k="1">甲<img k="2" alt="x" title="t"/></p>', 'extra_attributes',
         '屬性與原文不符：<img k="2"> 多了 title 屬性'),
        ('<p k="1" title="t">甲<img k="2" alt="x"/></p>', 'extra_attributes',
         '屬性與原文不符：<p k="1"> 多了 title 屬性'),
        ('<p k="1">甲<img k="2" alt="x"/><b class="c">乙</b></p>', 'extra_attributes',
         '屬性與原文不符：<b> 帶有原文沒有的屬性'),
    ])
    def test_added_or_removed_attribute_refused(self, reply, code, detail):
        p = protect('<p>A <img src="s" alt="A"/><b>b</b></p>')
        issues = check_keys(reply.replace('</p>', '<b>乙</b></p>') if '<b' not in reply
                            else reply, p)
        assert [(i.code, i.detail) for i in issues] == [(code, detail)]

    def test_empty_value_stays_empty(self):
        source = '<p>A <img src="s" alt=""/></p>'
        p = protect(source)
        assert p.html == '<p k="1">A <img k="2" alt=""/></p>'
        assert check_keys('<p k="1">甲<img k="2" alt=""/></p>', p) == []
        assert [i.detail for i in check_keys('<p k="1">甲<img k="2" alt="圖"/></p>', p)] == \
            ['屬性與原文不符：<img k="2"> 的 alt 原文是空的']
        assert codes(check_html(source, '<p>甲<img src="s" alt="圖"/></p>')) == \
            ['key_mismatch', 'extra_attributes']
        assert [(i.code, i.detail) for i in check_html('<p>A <img src="s" alt="x"/></p>',
                                                       '<p>甲<img src="s" alt=""/></p>').issues] \
            == [('extra_attributes', '第 1 個區塊的 <img> 的 alt 不可為空')]

    @pytest.mark.parametrize('translation', [
        '<p title="提示">甲<img src="m.png" alt="地圖"/><span aria-label="關">乙</span></p>',
        '<p title="Tip">甲<img src="m.png" alt="A map"/><span aria-label="Close">乙</span></p>',
    ])
    def test_stored_form_values_free(self, translation):
        source = '<p title="Tip">A <img src="m.png" alt="A map"/><span aria-label="Close">b</span></p>'
        assert check_html(source, translation).issues == []

    def test_stored_form_other_attributes_still_compared(self):
        source = '<p>A <img src="m.png" alt="A map"/></p>'
        assert codes(check_html(source, '<p>甲<img src="n.png" alt="地圖"/></p>')) == \
            ['key_mismatch', 'extra_attributes']
        assert codes(check_html(source, '<p>甲<img src="m.png"/></p>')) == \
            ['key_mismatch', 'extra_attributes']


class TestCheckKeys:
    """The keyed reply is checked before restore erases key violations."""

    SOURCE = '<p class="a">A</p>\n<p class="a">B</p>'

    def _issues(self, reply, source=SOURCE):
        return [(i.code, i.detail) for i in check_keys(reply, protect(source))]

    def test_prompt_form_passes(self):
        p = protect(SOURCE + '\nrun <b>x</b> text')
        assert check_keys(p.html, p) == []

    def test_swapped_blocks_with_equal_attributes(self):
        reply = '<p k="2">乙</p>\n<p k="1">甲</p>'
        assert self._issues(reply) == [
            ('structure_mismatch', '最外層的標記與原文不同：原文為 k="1"、k="2"，譯文為 k="2"、k="1"')]
        # restore alone would have erased it
        p = protect(self.SOURCE)
        assert check_html(self.SOURCE, restore(reply, p)).issues == []

    def test_key_used_twice(self):
        assert self._issues('<p k="1">甲</p>\n<p k="1">乙</p>') == [
            ('structure_mismatch', '最外層的標記與原文不同：原文為 k="1"、k="2"，譯文為 k="1"、k="1"'),
            ('key_mismatch', 'k="1"（<p>）出現 2 次；k="2"（<p>）出現 0 次'
                             '（每個 k 必須在原來的標籤上出現一次）')]

    def test_missing_key_replaced_by_a_bare_tag(self):
        assert self._issues('<p k="1">甲</p>\n<p>乙</p>') == [
            ('structure_mismatch', '最外層的標記與原文不同：原文為 k="1"、k="2"，譯文為 k="1"、（無 k）'),
            ('key_mismatch', 'k="2"（<p>）出現 0 次（每個 k 必須在原來的標籤上出現一次）')]

    def test_inline_key_moved_to_another_tag_or_unknown(self):
        source = '<p>A <a id="x">b</a></p>'
        assert self._issues('<p k="1">甲<b k="2">乙</b></p>', source) == [
            ('key_mismatch', '<b k="2"> 不是原文的標記；k="2"（<a>）出現 0 次'
                             '（每個 k 必須在原來的標籤上出現一次）')]
        assert self._issues('<p k="1">甲<a k="2">乙</a><a k="9">丙</a></p>', source) == [
            ('key_mismatch', '<a k="9"> 不是原文的標記（每個 k 必須在原來的標籤上出現一次）')]

    def test_run_nested_or_repeated(self):
        source = '<p>A</p>\nrun text'
        assert self._issues('<p k="1">甲<run k="2">乙</run></p>', source) == [
            ('structure_mismatch', '最外層的標記與原文不同：原文為 k="1"、k="2"，譯文為 k="1"'),
            ('key_mismatch', '<run k="2"> 不在最外層（每個 k 必須在原來的標籤上出現一次）')]
        assert self._issues('<p k="1">甲</p><run k="2">乙</run><run k="2">丙</run>', source) == [
            ('structure_mismatch',
             '最外層的標記與原文不同：原文為 k="1"、k="2"，譯文為 k="1"、k="2"、k="2"'),
            ('key_mismatch', 'k="2"（<run>）出現 2 次（每個 k 必須在原來的標籤上出現一次）')]

    def test_text_outside_the_blocks(self):
        assert self._issues('<p k="1">甲</p>\n<p k="2">乙</p>丙') == [
            ('structure_mismatch', '最外層的標記與原文不同：原文為 k="1"、k="2"，'
                                   '譯文為 k="1"、k="2"，且最外層有 <run> 以外的文字')]

    def test_inline_keys_inside_a_run_are_not_top_level(self):
        p = protect('x <em class="i">y</em>')
        assert p.html == '<run k="1">x <em k="2">y</em></run>' and p.top == ['1']
        assert check_keys('<run k="1">甲<em k="2">乙</em></run>', p) == []

    def test_key_in_single_quotes(self):
        assert self._issues("<p k='1'>甲</p>\n<p k='2'>乙</p>") == []

    def test_bare_tag_with_attributes(self):
        assert self._issues('<p k="1">甲<b x="1">乙</b></p>\n<p k="2">丙</p>') == [
            ('extra_attributes', '屬性與原文不符：<b> 帶有原文沒有的屬性')]

    def test_keys_numbered_from(self):
        p = protect('<p class="a">A <b class="c">b</b></p>', first_key=5)
        assert p.html == '<p k="5">A <b k="6">b</b></p>' and p.top == ['5']


class TestBlockRootSeparate:
    """A block's own attributes cannot move to a descendant and back."""

    @pytest.mark.parametrize('source, translation', [
        ('<blockquote id="q"><blockquote>A</blockquote></blockquote>',
         '<blockquote><blockquote id="q">甲</blockquote></blockquote>'),
        ('<blockquote><blockquote id="q">A</blockquote></blockquote>',
         '<blockquote id="q"><blockquote>甲</blockquote></blockquote>'),
    ])
    def test_moved_both_directions(self, source, translation):
        result = check_html(source, translation)
        assert codes(result) and all(i.blocking for i in result.issues)
        assert any(i.detail.startswith('第 1 個區塊的 <blockquote> 本身的屬性與原文不同')
                   for i in result.issues)

    def test_root_kept_descendant_order_free(self):
        assert check_html('<div class="d"><p>A</p><p class="x">B</p></div>',
                          '<div class="d"><p class="x">乙</p><p>甲</p></div>').issues == []


class TestTokenNames:
    """Start and end tags match the same names; the wrapper ends by name."""

    @pytest.mark.parametrize('html', [
        '<p><é>x</é> y</p>',
        '<span class=x>t</span>',
        '<p>a <span class=x>t</span> b</p>',
        'x <é a="1">y</é>',
        '<p>A</p>\n<ns:tag ns:a="1">B</ns:tag>',
    ])
    def test_round_trip(self, html):
        p = protect(html)
        assert restore(p.html, p) == html

    def test_non_ascii_tag_is_a_token(self):
        p = protect('<p class="a">A</p>')
        assert p.html == '<p k="1">A</p>'
        p = protect('<é class="a">A</é>')
        assert p.html == '<run k="1"><é k="2">A</é></run>'

    def test_invented_tag_inside_the_wrapper(self):
        p = protect('<p>Hi</p>\nfoo')
        assert restore('<p k="1">嗨</p>\n<run k="2">甲<é>乙</é>丙</run>', p) == \
            '<p>嗨</p>\n甲<é>乙</é>丙'
        assert restore('<p k="1">嗨</p>\n<run k="2">甲<x>乙</x></run>', p) == \
            '<p>嗨</p>\n甲<x>乙</x>'


class TestRound1Checks:

    def test_source_issues(self):
        assert validation.source_issues('<p>A</p>') == []
        assert [i.code for i in validation.source_issues('<p>A')] == ['ambiguous_source']
        assert [i.code for i in validation.source_issues('<p>A</p>', ['0' * 16])] == \
            ['ambiguous_source']

    @pytest.mark.parametrize('text', ['<p>甲\ud800</p>', '<p>\x01</p>'])
    def test_encoding_failure_not_well_formed(self, text):
        result = check_html('<p>A</p>', text)
        assert codes(result) == ['not_well_formed']

    @pytest.mark.parametrize('bad', ['\x01', '\x1b', '\ud800', '￾'])
    def test_code_with_characters_xml_cannot_hold(self, bad):
        result = check_code('x = 1', 'x = 1' + bad)
        assert [(i.code, i.detail) for i in result.issues] == \
            [('not_well_formed', '譯文含有 XML 不允許的字元')]
        assert check_code('x = 1', '# 註\tx\nx = 1\r\n').issues == []

    def test_superscript_colon_is_no_prefix(self):
        assert declarations('x²:y a', 'café:b="1"') == {None: validation.XHTML_NS}
        assert check_html('<p>x²:y</p>', '<p>x²：乙</p>').issues == []

    def test_word_test_before_whitespace_removed(self):
        assert [i.code for i in validation._quality('A B', 'A B')] == []
        assert [i.code for i in validation._quality('Ab', 'Ab')] == ['untranslated']

    def test_first_block_numbering(self):
        result = check_html('<p>A <b>b</b></p>\n<p>C</p>', '<p>甲</p>\n<p> </p>', first_block=4)
        assert [i.detail for i in result.issues] == [
            '第 5 個區塊沒有文字', '第 4 個區塊不帶屬性的 <b>：原文 1 個，譯文 0 個']


class TestRound1Survivors:
    """Survivors of the review round 1 mutation run."""

    def test_replacements_from_the_end(self):
        # alt comes first in the text and first by name: replacing it first
        # would shift the position of title
        source = '<p>A <img alt="A" title="T" src="s"/></p>'
        p = protect(source)
        assert restore('<p k="1">甲<img k="2" alt="長的替代文字" title="題"/></p>', p) == \
            '<p>甲<img alt="長的替代文字" title="題" src="s"/></p>'

    def test_unchanged_value_then_a_changed_one(self):
        source = '<p>A <img alt="A" title="T" src="s"/></p>'
        p = protect(source)
        assert restore('<p k="1">甲<img k="2" alt="圖" title="T"/></p>', p) == \
            '<p>甲<img alt="圖" title="T" src="s"/></p>'

    def test_repeated_attribute_left_as_written(self):
        p = protect('<p>A <img alt="A" src="s"/></p>')
        reply = '<p k="1">甲<img k="2" alt="x" alt="y"/></p>'
        assert restore(reply, p) == '<p>甲<img k="2" alt="x" alt="y"/></p>'

    def test_kinds_are_structure(self):
        p = protect('<p class="a">A <img alt="A" src="s"/></p>\n<p>B</p>')
        issues = check_keys('<p k="2">乙<img k="2"/></p>\n<p k="2" x="1">甲</p>', p)
        assert [(i.kind, i.code, i.blocking) for i in issues] == [
            ('structure', 'structure_mismatch', True), ('structure', 'key_mismatch', True),
            ('structure', 'extra_attributes', True)]
        result = check_code('x', 'x\x01')
        assert [(i.kind, i.blocking) for i in result.issues] == [('structure', True)]

    def test_nothing_at_the_top_level(self):
        p = protect('<p>A</p>')
        assert [i.detail for i in check_keys('<!-- c -->', p)][0] == \
            '最外層的標記與原文不同：原文為 k="1"，譯文為 （無）'

    def test_attribute_problems_joined(self):
        p = protect('<p>A <img alt="A" src="s"/></p>')
        assert [i.detail for i in check_keys('<p k="1">甲<img k="2" src="t"/></p>', p)] == \
            ['屬性與原文不符：<img k="2"> 多了 src 屬性；<img k="2"> 少了 alt 屬性']

    def test_tags_after_a_comment_still_checked(self):
        p = protect('<p class="a">A</p>\n<p class="a">B</p>')
        assert [i.code for i in check_keys('<!-- c --><p k="2">乙</p>\n<p k="1">甲</p>', p)] == \
            ['structure_mismatch']
        assert [i.code for i in check_keys('<p k="1">甲<?pi x?><b x="1">乙</b></p>\n'
                                           '<p k="2">丙</p>', p)] == ['extra_attributes']


# --- Phase 4 review round 2 ------------------------------------------------------

class TestRound2Keys:

    def test_key_moved_to_another_block(self):
        source = '<p><em class="i">First</em></p>\n<p><em class="i">Second</em></p>'
        p = protect(source)
        assert p.html == '<p k="1"><em k="2">First</em></p>\n<p k="3"><em k="4">Second</em></p>'
        assert [(k, key.owner) for k, key in p.keys.items()] == \
            [('1', '1'), ('2', '1'), ('3', '3'), ('4', '3')]
        reply = '<p k="1"><em k="4">第一</em></p>\n<p k="3"><em k="2">第二</em></p>'
        assert [(i.code, i.detail) for i in check_keys(reply, p)] == [
            ('key_mismatch', '<em k="4"> 不在原來的區塊（原文在 k="3" 內）；'
                             '<em k="2"> 不在原來的區塊（原文在 k="1" 內）'
                             '（每個 k 必須在原來的標籤上出現一次）')]
        # restored, the two blocks look right: only the keyed check sees it
        assert check_html(source, restore(reply, p)).issues == []

    def test_key_moved_into_or_out_of_a_run(self):
        source = '<p>A <b class="x">b</b></p>\nrun <i class="y">i</i>'
        p = protect(source)
        assert p.html == ('<p k="1">A <b k="2">b</b></p>\n'
                          '<run k="3">run <i k="4">i</i></run>')
        assert p.keys['4'].owner == '3'
        assert check_keys('<p k="1">甲<b k="2">乙</b></p>\n<run k="3">丙<i k="4">丁</i></run>',
                          p) == []
        assert [i.detail for i in check_keys(
            '<p k="1">甲<i k="4">丁</i></p>\n<run k="3">丙<b k="2">乙</b></run>', p)] == [
            '<i k="4"> 不在原來的區塊（原文在 k="3" 內）；<b k="2"> 不在原來的區塊（原文在 k="1" 內）'
            '（每個 k 必須在原來的標籤上出現一次）']

    def test_nested_key_stays_in_its_block(self):
        p = protect('<div class="d"><p class="a">A</p></div>\n<p>B</p>')
        assert p.keys['2'].owner == '1'
        assert check_keys('<div k="1"><p k="2">甲</p></div>\n<p k="3">乙</p>', p) == []


class TestRound2Values:

    @pytest.mark.parametrize('value', ['', ' ', '&#32;', '&#10;'])
    def test_non_empty_source_value_cannot_be_emptied(self, value):
        p = protect('<p>A <img src="s" alt="A map"/></p>')
        assert [(i.code, i.detail) for i in check_keys(
            f'<p k="1">甲<img k="2" alt="{value}"/></p>', p)] == \
            [('extra_attributes', '屬性與原文不符：<img k="2"> 的 alt 不可為空')]

    @pytest.mark.parametrize('source_value', ['', ' '])
    def test_blank_source_value_stays_blank(self, source_value):
        p = protect(f'<p>A <img src="s" alt="{source_value}"/></p>')
        assert check_keys('<p k="1">甲<img k="2" alt=" "/></p>', p) == []
        assert [i.detail for i in check_keys('<p k="1">甲<img k="2" alt="圖"/></p>', p)] == \
            ['屬性與原文不符：<img k="2"> 的 alt 原文是空的']

    def test_whitespace_only_is_empty_in_the_stored_form(self):
        source = '<p>A <img src="s" alt="A"/></p>'
        assert codes(check_html(source, '<p>甲<img src="s" alt="  "/></p>')) == \
            ['extra_attributes']
        assert check_html('<p>A <img src="s" alt=""/></p>',
                          '<p>甲<img src="s" alt="  "/></p>').issues == []

    def test_whitespace_references_survive_restore(self):
        source = '<p>A <img src="s" alt="A"/></p>'
        p = protect(source)
        restored = restore('<p k="1">甲<img k="2" alt="一&#10;二&#9;三&#13;四"/></p>', p)
        assert restored == '<p>甲<img src="s" alt="一&#10;二&#9;三&#13;四"/></p>'
        img = validation.fragment(restored, declarations(restored))[0][0]
        assert img.get('alt') == '一\n二\t三\r四'

    def test_literal_line_breaks_are_spaces(self):
        p = protect('<p>A <img src="s" alt="A"/></p>')
        assert restore('<p k="1">甲<img k="2" alt="一\n二\t三\r\n四"/></p>', p) == \
            '<p>甲<img src="s" alt="一 二 三 四"/></p>'

    def test_unchanged_value_keeps_its_exact_token(self):
        source = '<p>A <img src="s" alt="a&#10;b"/></p>'
        p = protect(source)
        assert restore(p.html, p) == source


class TestRound2Segments:

    def test_schema_1_text_segment_without_html(self):
        seg = SimpleNamespace(segment_type='text', html='', text='Some words.', fingerprints=[])
        assert validation.check_segment(seg, '一些字。').issues == []
        assert validation.check_segment(seg, '<p>一些字。</p>').issues == []

    def test_pieces_per_group(self):
        source = '<p>A</p>\nL1\nL2\n<p>B</p>'
        fps = [fingerprint(t) for t in ('A', 'L1', 'L2', 'B')]
        assert validation.pieces_per_group(source, fps) == [1, 2, 1]
        assert validation.pieces_per_group(source) == [1, 1, 1]
        assert validation.pieces_per_group('<p>A') is None



# --- Phase 4 review round 3 ------------------------------------------------------

class TestRound3:

    def test_one_letter_blocks_are_not_a_word(self):
        assert check_html('<p>A</p>\n<p>B</p>', '<p>A</p>\n<p>B</p>').issues == []
        assert check_html('<p>1</p>\nA\nB', '<p>1</p>\nA\nB',
                          [fingerprint('1'), fingerprint('A'), fingerprint('B')]).issues == []
        assert codes(check_html('<p>Ab</p>\n<p>B</p>', '<p>Ab</p>\n<p>B</p>')) == \
            ['untranslated']

    @pytest.mark.parametrize('translation, reason', [
        ('<p>甲', 'Opening and ending tag mismatch: p and fragment'),
        ('<p>&nbsp;</p>', "Entity 'nbsp' not defined"),
    ])
    def test_not_well_formed_without_position(self, translation, reason):
        assert [i.detail for i in check_html('<p>A</p>', translation).issues] == \
            [f'不是格式正確的 XML：{reason}']

    def test_xml_error_without_msg(self):
        assert validation.xml_error(ValueError('bad, line 3, column 4')) == '不是格式正確的 XML：bad'

    @pytest.mark.parametrize('source, translation, detail', [
        ('<p>A <img src="s" alt="x"/></p>', '<p>甲<img src="s" alt=" "/></p>',
         '第 1 個區塊的 <img> 的 alt 不可為空'),
        ('<p title="T">A</p>', '<p title="">甲</p>', '第 1 個區塊的 <p> 的 title 不可為空'),
        ('<p>A <a href="#x" title="T" aria-label="L">b</a></p>',
         '<p>甲<a href="#x" title="" aria-label="">乙</a></p>',
         '第 1 個區塊的 <a> 的 aria-label、title 不可為空'),
        ('<p>A <img src="s" alt="x"/><img src="s" alt="y"/></p>',
         '<p>甲<img src="s" alt="z"/><img src="s" alt=""/></p>',
         '第 1 個區塊的 <img> 的 alt 不可為空'),
    ])
    def test_emptied_value_is_one_issue(self, source, translation, detail):
        assert [(i.code, i.detail) for i in check_html(source, translation).issues] == \
            [('extra_attributes', detail)]

    @pytest.mark.parametrize('translation', [
        '<p>甲<img src="t" alt=""/></p>',     # another machine value too
        '<p>甲<img alt=""/></p>',             # an attribute dropped
        '<p>甲<span src="s" alt=""/></p>',    # another tag
    ])
    def test_emptied_and_another_change_is_not_only_emptied(self, translation):
        result = check_html('<p>A <img src="s" alt="x"/><span/></p>', translation)
        assert all('不可為空' not in i.detail for i in result.issues) and result.blocking


class TestRound3Survivors:
    """Survivors of the review round 3 mutation run: the pairing of emptied values."""

    def _details(self, source, translation):
        return [(i.code, i.detail) for i in check_html(source, translation).issues]

    def test_a_source_element_is_paired_once(self):
        assert self._details(
            '<p>A <img src="s" alt="a" title="t"/></p>',
            '<p>甲<img src="s" alt="" title="t"/><img src="s" alt="a" title=""/></p>') == [
            ('extra_attributes', '第 1 個區塊的 <img> 的 alt 不可為空；'
                                 '第 1 個區塊的 <img> 帶有原文沒有的屬性')]

    def test_a_translation_element_is_paired_once(self):
        assert self._details(
            '<p>A <img src="s" alt="a" title=""/><img src="s" alt="" title="t"/></p>',
            '<p>甲<img src="s" alt="" title=""/></p>') == [
            ('key_mismatch', '第 1 個區塊帶 k 屬性的 <img>：原文 1 個，譯文 0 個'),
            ('extra_attributes', '第 1 個區塊的 <img> 的 title 不可為空')]

    def test_pairing_adds_to_an_existing_count(self):
        assert self._details('<p>A <img src="s" alt=""/><img src="s" alt="x"/></p>',
                             '<p>甲<img src="s" alt=""/><img src="s" alt=""/></p>') == [
            ('extra_attributes', '第 1 個區塊的 <img> 的 alt 不可為空')]

    def test_block_element_with_two_emptied_values(self):
        assert self._details('<p title="T" aria-label="L">A</p>',
                             '<p title="" aria-label=" ">甲</p>') == [
            ('extra_attributes', '第 1 個區塊的 <p> 的 aria-label、title 不可為空')]

    def test_a_balanced_element_is_not_paired(self):
        assert self._details('<p>A <img src="s" alt="x"/><img src="s" alt=""/></p>',
                             '<p>甲<img src="s" alt=""/></p>') == [
            ('key_mismatch', '第 1 個區塊帶 k 屬性的 <img>：原文 1 個，譯文 0 個')]


# --- Phase 5: glossary compliance --------------------------------------------------

from src.glossary.glossary_manager import FULL_GLOSSARY_MAX_TERMS, GlossaryEntry, GlossaryManager  # noqa: E402


def glossary(*items):
    """A glossary of (term, translation[, category]) held in memory only."""
    gm = GlossaryManager('/nonexistent/glossary.json')
    for term, translation, *category in items:
        gm.entries[term.lower()] = GlossaryEntry(term, translation, *category)
    return gm


def missing(source, translation, gm):
    return [e.term for e in validation.terms_not_applied(source, translation, gm)]


class TestGlossaryCompliance:

    GM = glossary(('drip line', '滴水線', 'term'), ('Velmora', 'Velmora', 'place'))

    def test_other_rendering_gets_an_issue(self):
        issues = validation.glossary_issues('The drip line moved.', '<p>滴線移動了。</p>', self.GM)
        assert issues == [validation.Issue(kind='glossary', code='term_not_applied',
                                           detail='術語「drip line」未譯為「滴水線」',
                                           blocking=False)]

    def test_glossary_rendering_gets_none(self):
        assert validation.glossary_issues('The drip line moved.', '<p>滴水線移動了。</p>',
                                          self.GM) == []

    def test_parenthetical_original_ignored(self):
        assert missing('The drip line moved.', '滴水線（drip line）移動了。', self.GM) == []
        # the original in parentheses does not count as the translation
        assert missing('Near Velmora.', '在某城（Velmora）附近。', self.GM) == ['Velmora']
        assert missing('Near Velmora.', '在某城(Velmora)附近。', self.GM) == ['Velmora']
        assert missing('Near Velmora.', '在 Velmora 附近。', self.GM) == []
        # a parenthetical with Chinese is text, not an original
        assert missing('The drip line moved.', '線（滴水線）移動了。', self.GM) == []

    def test_only_terms_of_the_source(self):
        # a small glossary sends every entry; only those in the source are checked
        assert missing('Nothing here.', '這裡沒有。', self.GM) == []
        assert missing('Drip lines and VELMORA.', '無。', self.GM) == ['drip line', 'Velmora']

    def test_matching_of_the_prompt(self):
        gm = glossary(('render', '算繪'), ('AI', '人工智慧'))
        assert missing('We maintain rendering.', '我們維護。', gm) == ['render']  # AI not in maintain

    def test_large_glossary(self):
        gm = glossary(*[(f'word{i:03d}', f'詞{i}') for i in range(FULL_GLOSSARY_MAX_TERMS + 1)])
        assert missing('Only word007 here.', '只有這裡。', gm) == ['word007']

    def test_empty_translation_and_term_skipped(self):
        gm = glossary(('drip line', ' '), ('  ', '空'), ('', '空'))
        assert missing('The drip line moved.', '移動了。', gm) == []

    def test_no_glossary(self):
        assert validation.terms_not_applied('The drip line.', '無。', None) == []
        assert validation.terms_not_applied('The drip line.', '無。', glossary()) == []

    def test_work_only_as_a_title(self):
        gm = glossary(('Silver Atlas', '銀色地圖集', 'work'))
        # the prompt: a work's translation only where it is the work's title
        assert missing('She owned a silver atlas.', '她有一本銀色的地圖集。', gm) == []
        assert missing('She read Silver Atlas.', '她讀了一本地圖集。', gm) == ['Silver Atlas']
        assert missing('She read Silver Atlas.', '她讀了《銀色地圖集》。', gm) == []
        other = glossary(('Silver Atlas', '銀色地圖集', 'term'))
        assert missing('She owned a silver atlas.', '她有一本地圖集。', other) == ['Silver Atlas']

    @pytest.mark.parametrize('expected,translation', [
        ('Velmora 小姐', '<p>Velmora小姐笑了。</p>'),            # whitespace
        ('API', '<p>api 呼叫</p>'),                               # case
        ('艾拉·維恩', '<p>艾拉・維恩</p>'),                      # middle dots
        ('艾拉‧維恩', '<p>艾拉•維恩</p>'),
        ('Ｘ光', '<p>X光</p>'),                                  # NFKC
        ('甲&乙', '<p>甲&amp;乙</p>'),                           # entities
        ('滴水線', '<p><em>滴水</em>線</p>'),                     # tags
        ('滴水\n線', '<p>滴水 線</p>'),
    ])
    def test_normalization(self, expected, translation):
        gm = glossary(('drip line', expected))
        assert missing('The drip line.', translation, gm) == []

    def test_comparable(self):
        assert validation._comparable('A b（Orig）<Ｃ・d') == 'ab<c·d'

    def test_check_segment_adds_glossary_warnings(self):
        seg = SimpleNamespace(segment_type='text', html='<p>The drip line.</p>',
                              text='The drip line.', fingerprints=[])
        result = check_segment(seg, '<p>滴線。</p>', self.GM)
        assert codes(result) == ['term_not_applied'] and not result.blocking
        assert check_segment(seg, '<p>滴水線。</p>', self.GM).issues == []
        assert check_segment(seg, '<p>滴線。</p>').issues == []  # no glossary given
        # a failing translation gets only its structural issues
        assert codes(check_segment(seg, '<p>滴線。<b>x</b></p>', self.GM)) == ['unknown_tag']

    def test_check_segment_schema_1_and_code(self):
        seg = SimpleNamespace(segment_type='text', html='', text='The drip line.', fingerprints=[])
        assert codes(check_segment(seg, '滴線。', self.GM)) == ['term_not_applied']
        code = SimpleNamespace(segment_type='code', html='', text='# drip line\nx = 1',
                               fingerprints=[])
        assert check_segment(code, '# 滴線\nx = 1', self.GM).issues == []

    def test_glossary_kind_never_blocks(self):
        assert not validation._issue(validation.GLOSSARY, 'x', 'y').blocking
        assert not validation._issue(validation.QUALITY, 'x', 'y').blocking
        assert validation._issue(validation.STRUCTURE, 'x', 'y').blocking



# --- Phase 5 review round 1 -------------------------------------------------------

class TestGlossaryRound1:

    @pytest.mark.parametrize('text,expected', [
        # after NFKC (see _comparable) every parenthesis is ASCII
        ('介面(API (v2))', '介面'),                        # nested, no Han: all gone
        ('介面(說明 (v2))', '介面(說明 )'),                # Han outside: only the inner group
        ('(a (b) c)', ''),
        ('甲 (乙', '甲 (乙'),                               # unmatched: kept
        ('甲) 乙 (x)', '甲) 乙 '),
        ('值 (\U00020000)', '值 (\U00020000)'),            # a supplementary-plane Han character
        ('值 (一)', '值 (一)'),
        ('值 (鿿)', '值 (鿿)'),
        ('值 (㐀)', '值 (㐀)'),
        ('值 (豈)', '值 (豈)'),
        ('值 (、)', '值 '),                             # punctuation is not Han
    ])
    def test_without_originals(self, text, expected):
        assert validation._without_originals(text) == expected

    def test_nested_original_ignored(self):
        gm = glossary(('interface', '介面', 'term'))
        assert missing('The interface.', '<p>介面（API (v2)）。</p>', gm) == []
        gm = glossary(('Velmora', 'Velmora', 'place'))
        assert missing('Velmora.', '<p>某城（Velmora (old)）。</p>', gm) == ['Velmora']

    def test_greater_than_inside_an_attribute(self):
        gm = glossary(('drip line', '滴水線', 'term'))
        translation = '<p>甲<em title="a > b">滴水</em>線。</p>'
        assert missing('The drip line.', translation, gm) == []

    def test_plain_text(self):
        assert validation.plain_text('<p>甲<em title="a > b">乙</em>&amp;<!-- c -->丙</p>'
                                     '<style>x</style>') == '甲乙&丙'
        assert validation.plain_text('a < b & c') == 'a < b & c'  # does not parse: as it is
        assert validation.plain_text('<p>\ud800</p>') == '<p>\ud800</p>'

    @pytest.mark.parametrize('term,category,source,found', [
        ('Eve', 'person', 'Every event.', False),
        ('Eve', 'person', 'Eve came.', True),
        ('Eve', 'person', "Eve's hat.", True),               # possessive
        ('Eve', 'person', 'Eve’s hat.', True),
        ('Java', 'term', 'In JavaScript.', False),
        ('Java', 'term', 'In Java.', True),
        ('Mark', 'person', 'The market.', False),
        ('Q', 'term', 'Quick, quiet.', False),               # one letter
        ('Q', 'term', 'Ask Q.', True),
        ('API', 'term', 'Two APIs.', True),                  # plural
        ('box', 'term', 'Two boxes.', True),
        ('Jones', 'person', 'The Joneses came.', True),
        ('Velmora', 'place', 'Velmorans came.', False),
        ('render', 'term', 'Rendering and rendered.', True),  # inflected verbs
        ('render', 'term', 'Renders.', True),
        ('render', 'term', 'A renderer.', False),
        ('calibrate', 'term', 'It calibrates.', True),        # a final e
        ('calibrate', 'term', 'It calibrated.', True),
        ('calibrate', 'term', 'Calibrating it.', True),
        ('calibrate', 'term', 'A calibrator.', False),
        ('Bolt', 'person', 'Bolting.', False),               # names take no -ing
        ('Bolt', 'person', 'Bolted.', False),
        ('Bolt', 'organization', 'Bolts.', True),
        ('C#', 'term', 'In C# code.', True),
        ('e', 'term', 'The e here.', True),                 # one letter e: no e-stem
        ('e', 'term', 'The ing here.', False),
    ])
    def test_occurrence(self, term, category, source, found):
        entry = GlossaryEntry(term, '譯', category)
        assert bool(validation.occurrence_pattern(entry).search(source)) is found

    def test_work_case_sensitive_with_inflection(self):
        pattern = validation.occurrence_pattern(GlossaryEntry('Silver Atlas', '譯', 'work'))
        assert pattern.search("Silver Atlas's map")
        assert not pattern.search('silver atlas map')
        assert pattern.flags & validation.re.IGNORECASE == 0
        other = validation.occurrence_pattern(GlossaryEntry('Silver Atlas', '譯', 'place'))
        assert other.search('silver atlas map')

    def test_bad_entries_skipped(self):
        gm = glossary(('drip line', '滴水線', 'term'))
        gm.entries['a'] = GlossaryEntry('alpha', None, 'term')
        gm.entries['b'] = GlossaryEntry('beta', 5, 'term')
        gm.entries['c'] = GlossaryEntry(None, '丙', 'term')
        gm.entries['d'] = GlossaryEntry('delta', '丁', ['not', 'a', 'string'])
        assert missing('Alpha beta drip line delta.', '無。', gm) == ['delta', 'drip line']

    def test_sorted_by_term(self):
        gm = glossary(('beta', '乙'), ('Alpha', '甲'), ('gamma', '丙'))
        assert missing('Gamma beta alpha.', '無。', gm) == ['Alpha', 'beta', 'gamma']

    def test_large_glossary_needs_an_occurrence(self):
        # above the prompt's size limit the prompt holds the entries whose
        # start matches; the check needs the full occurrence
        gm = glossary(*[(f'word{i:03d}', f'詞{i}') for i in range(FULL_GLOSSARY_MAX_TERMS + 1)])
        assert missing('Only word007x here.', '無。', gm) == []


class TestGlossaryRound1Survivors:
    """Mutation testing, Phase 5 review round 1."""

    @pytest.mark.parametrize('term,source,found', [
        ('de', 'The ding.', True),            # a two-letter term with a final e
        ('TAX', 'The TAX.', True),            # a final X is not an e
        ('CODE', 'Coding it.', True),         # a capital final E
        ('X1 rate', 'X1 rating.', True),      # the letter before the e decides
        ('Q-te', 'Q-ting.', True),
    ])
    def test_final_e(self, term, source, found):
        entry = GlossaryEntry(term, '譯', 'term')
        assert bool(validation.occurrence_pattern(entry).search(source)) is found

    def test_work_plural_case_sensitive(self):
        entry = GlossaryEntry('Silver Atlas', '譯', 'work')
        assert validation.occurrence_pattern(entry).search('Two Silver Atlases.')

    def test_sorted_by_lower_case(self):
        # '_' sorts after capitals and before small letters
        gm = glossary(('ab', '甲'), ('_x', '乙'))
        assert missing('ab and _x.', '無。', gm) == ['_x', 'ab']
