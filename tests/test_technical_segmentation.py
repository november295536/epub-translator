"""Phase 6: segmentation for technical books (plan section 5, Phase 6).

Rules 2 (new projects): a heading joins the following text segment, a code
block without a comment line is `passthrough` (no model call, left as it
is by the export), code blocks stay segment boundaries, and the prompt's
context before is the previous segment's translation when it has one.
Projects of rules 1 (created before Phase 6) behave as before. Books are
raw ZIP archives with invented text.
"""

import json
import os
import zipfile

import pytest
from click.testing import CliRunner

from src.core.project import ProjectManager, SegmentState, SegmentStatus
from src.core.segmenter import (
    MAX_WORDS, MIN_WORDS, SEGMENTATION, Segment, Segmenter, SegmentType, has_comment_line,
)
from src.core.translator import TranslationEngine, get_context, translate_segment, translation_text
from src.epub.blocks import serialize, walk_blocks
from src.epub.exporter import FORMATS
from src.epub.extractor import find_body, parse_xml
from src.llm.fake import FakeBackend
from tests.conftest import body_of, use_fake_backend, xhtml
from tests.test_exporter import _export, _inner, _project, _raw_book, _root

XHTML_NS = '{http://www.w3.org/1999/xhtml}'
NCX_TEXT = '{http://www.daisy.org/z3986/2005/ncx/}text'


def _segments(html, rules=2):
    return Segmenter(rules=rules).segment_chapter('ch', body_of(html))


def _ranges(segs):
    return [(s.block_start, s.block_end) for s in segs]


def _words(n, mark):
    return ' '.join(f'{mark}{i}' for i in range(n))


# --- Segmentation rules ----------------------------------------------------------

class TestRules2:

    def test_new_projects_use_rules_2(self):
        assert SEGMENTATION == 2
        assert Segmenter().rules == 2

    @pytest.mark.parametrize('rules', [0, 3, '2'])
    def test_unknown_rules_refused(self, rules):
        with pytest.raises(ValueError):
            Segmenter(rules=rules)

    def test_heading_joins_the_following_text(self):
        segs = _segments('<h1>Tide Tables</h1><p>Read them twice.</p><p>Then sail.</p>')
        assert _ranges(segs) == [(0, 3)]
        assert segs[0].html == '<h1>Tide Tables</h1>\n<p>Read them twice.</p>\n<p>Then sail.</p>'
        assert segs[0].text == 'Tide Tables\nRead them twice.\nThen sail.'
        assert segs[0].segment_type == SegmentType.TEXT and not segs[0].standalone

    def test_heading_ends_the_previous_segment(self):
        segs = _segments('<p>Short text one.</p><h2>Break</h2><p>Short text two.</p>')
        assert _ranges(segs) == [(0, 1), (1, 3)]

    def test_each_heading_starts_a_segment(self):
        segs = _segments('<h1>Main</h1><p>Intro.</p><h2>One</h2><p>First.</p>'
                         '<h2>Two</h2><p>Second.</p>')
        assert _ranges(segs) == [(0, 2), (2, 4), (4, 6)]
        assert not any(s.standalone for s in segs)

    def test_consecutive_headings_share_a_segment(self):
        segs = _segments('<p>Before.</p><h1>Part</h1><h2>Chapter</h2><h3>Section</h3><p>Text.</p>')
        assert _ranges(segs) == [(0, 1), (1, 5)]

    @pytest.mark.parametrize('follower', ['<pre>x = 1</pre>', '<table><tr><td>c</td></tr></table>',
                                          '<code>y = 2</code>'])
    def test_heading_before_code_or_table_stays_alone(self, follower):
        segs = _segments(f'<h2>Listing</h2><h3>Sub</h3>{follower}<p>After.</p>')
        assert _ranges(segs) == [(0, 2), (2, 3), (3, 4)]
        assert segs[0].standalone and segs[0].segment_type == SegmentType.TEXT
        assert segs[1].standalone

    def test_heading_at_chapter_end_stays_alone(self):
        segs = _segments('<p>Text.</p><h2>Notes</h2>')
        assert _ranges(segs) == [(0, 1), (1, 2)]
        assert [s.standalone for s in segs] == [False, True]

    def test_first_block_after_a_heading_always_joins(self):
        """The heading's words never leave it alone: the next block joins
        even when both together pass MAX_WORDS."""
        segs = _segments(f'<h2>Long one</h2><p>{_words(MAX_WORDS, "a")}</p><p>tail</p>')
        assert _ranges(segs) == [(0, 2), (2, 3)]
        assert segs[0].word_count == MAX_WORDS + 2

    def test_size_rules_after_the_first_block(self):
        # 2 + 250 words, then 260 more would pass MAX_WORDS: a new segment
        segs = _segments(f'<h2>Two words</h2><p>{_words(250, "a")}</p><p>{_words(260, "b")}</p>')
        assert [s.word_count for s in segs] == [252, 260]
        # 2 + 100 + 198 words reach MIN_WORDS: the segment ends there
        segs = _segments(f'<h2>Two words</h2><p>{_words(100, "a")}</p><p>{_words(198, "b")}</p>'
                         '<p>next</p>')
        assert [s.word_count for s in segs] == [MIN_WORDS, 1]

    def test_heading_block_inside_a_container(self):
        segs = _segments('<div class="sect"><h2>Inside</h2><p>Words.</p></div><p>More.</p>')
        assert _ranges(segs) == [(0, 3)]

    def test_large_heading_still_joined_by_the_next_block(self):
        segs = _segments(f'<h1>{_words(MIN_WORDS, "h")}</h1><p>After.</p><p>Next.</p>')
        assert _ranges(segs) == [(0, 2), (2, 3)]

    def test_code_stays_a_boundary(self):
        segs = _segments('<p>Before.</p><pre># c\nx = 1</pre><p>After.</p>'
                         '<h2>H</h2><pre>y = 2</pre>')
        assert _ranges(segs) == [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)]
        assert [s.segment_type for s in segs] == [SegmentType.TEXT, SegmentType.CODE,
                                                  SegmentType.TEXT, SegmentType.TEXT,
                                                  SegmentType.CODE]

    def test_blocks_covered_once_with_their_fingerprints(self):
        html = ('<h1>T</h1>' + ''.join(f'<p>{_words(120, chr(97 + i))}</p>' for i in range(7))
                + '<h2>S</h2><pre>x = 1</pre><table><tr><td>c</td></tr></table>Tail<h3>End</h3>')
        blocks = walk_blocks(body_of(html))
        for rules in (1, 2):
            segs = _segments(html, rules)
            assert [s.block_start for s in segs] == [0] + [s.block_end for s in segs[:-1]]
            assert segs[-1].block_end == len(blocks)
            assert [f for s in segs for f in s.fingerprints] == [b.fingerprint for b in blocks]
            assert '\n'.join(s.html for s in segs) == '\n'.join(b.html for b in blocks)
        assert len(_segments(html, 2)) < len(_segments(html, 1))


class TestPassthrough:

    @pytest.mark.parametrize('code, passthrough', [
        ('x = 1\ny = 2', True),
        ('#include &lt;stdio.h&gt;\nint x;', True),           # a directive, not a comment
        ('x = 1  # trailing comment', True),            # trailing comments are not translated
        ('#!/bin/sh\necho hi', True),                   # review round 1: a shebang is code
        ('#!/bin/sh\n# say hi\necho hi', False),
        ('echo hi\n#! not first', False),
        ('# a comment\nx = 1', False),
        ('    // indented comment\nf();', False),
        ('x = 1\n#', False),
        ('\n\n x', True),
    ])
    def test_comment_lines_decide(self, code, passthrough):
        assert has_comment_line(code) is not passthrough
        segs = _segments(f'<p>Intro.</p><pre>{code}</pre>')
        assert segs[-1].segment_type == SegmentType.CODE
        assert segs[-1].passthrough is passthrough
        assert segs[0].passthrough is False

    def test_run_of_one_code_element(self):
        segs = _segments('<p>Intro.</p> <code>x = 2</code> ')
        assert segs[1].segment_type == SegmentType.CODE and segs[1].passthrough

    def test_highlighted_code_uses_its_text(self):
        segs = _segments('<pre><span class="c"># note</span>\n<span>x</span> = 1</pre>'
                         '<pre><span class="k">def</span> f(): pass</pre>')
        assert [s.passthrough for s in segs] == [False, True]

    def test_rules_1_has_no_passthrough(self):
        segs = _segments('<pre>x = 1</pre>', rules=1)
        assert segs[0].segment_type == SegmentType.CODE and not segs[0].passthrough

    def test_segment_default(self):
        assert Segment('i', '', '', 0, SegmentType.CODE).passthrough is False


# --- Project state ---------------------------------------------------------------

BOOK = xhtml('<h1 id="t">Tide Tables</h1><p>Read the tables twice.</p>'
             '<pre class="src">x = 1\ny = 2</pre>'
             '<h2 id="s">Reading</h2><p>Read the <em>first</em> row.</p>'
             '<pre># read it\nload()</pre><h2>Tail</h2>')
CODE_ONLY = xhtml('<pre>plain()</pre>')
NAV = xhtml('<nav epub:type="toc"><ol><li><a href="Text/c.xhtml">Tide</a></li>'
            '<li><a href="Text/c.xhtml#s">Reading</a></li></ol></nav>',
            head='<title>Contents</title>')
NCX = ('<?xml version="1.0" encoding="utf-8"?>\n'
       '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>'
       '<navPoint id="a"><navLabel><text>Tide</text></navLabel>'
       '<content src="Text/c.xhtml"/></navPoint>'
       '<navPoint id="b"><navLabel><text>Reading</text></navLabel>'
       '<content src="Text/c.xhtml#s"/></navPoint></navMap></ncx>')
# rules 2: [h1 p] [pre: passthrough] [h2 p] [pre with a comment] [h2 alone]
TRANSLATIONS = {
    'c_seg0001': '<h1 id="t">潮汐表</h1>\n<p>把表讀兩次。</p>',
    'c_seg0003': '<h2 id="s">閱讀</h2>\n<p>讀<em>第一</em>列。</p>',
    'c_seg0004': '# 讀它\nload()',
    'c_seg0005': '<h2>尾</h2>',
}


def _book(tmp_path, chapters=None):
    return _raw_book(tmp_path, chapters or {'c': ('c.xhtml', BOOK), 'k': ('k.xhtml', CODE_ONLY)},
                     nav=NAV, ncx=NCX)


def _new_project(tmp_path, translations=None, name='proj'):
    return _project(tmp_path, _book(tmp_path), TRANSLATIONS if translations is None
                    else translations, name=name, rules=2)


def _load(project_dir):
    pm = ProjectManager(project_dir)
    pm.load()
    return pm


class TestProjectState:

    def test_segments_of_the_book(self, tmp_path):
        pm = _load(_new_project(tmp_path, {}))
        segs = pm.data.segments
        assert [(s.segment_id, s.block_start, s.block_end, s.passthrough) for s in segs] == [
            ('c_seg0001', 0, 2, False), ('c_seg0002', 2, 3, True), ('c_seg0003', 3, 5, False),
            ('c_seg0004', 5, 6, False), ('c_seg0005', 6, 7, False), ('k_seg0001', 0, 1, True)]
        assert pm.data.segmentation == 2

    def test_passthrough_created_translated_with_its_code(self, tmp_path):
        pm = _load(_new_project(tmp_path, {}))
        seg = pm.data.segments[1]
        assert seg.status == SegmentStatus.TRANSLATED
        assert seg.translated_text == seg.text == 'x = 1\ny = 2'
        assert seg.model == '' and seg.issues == [] and seg.revision == 0
        assert pm.data.segments[3].status == SegmentStatus.PENDING  # code with a comment line

    def test_save_load_round_trip(self, tmp_path):
        project_dir = _new_project(tmp_path, {})
        pm = _load(project_dir)
        with open(pm.project_file, encoding='utf-8') as f:
            raw = json.load(f)
        assert raw['segmentation'] == 2
        assert [s.get('passthrough') for s in raw['segments']] == [None, True, None, None, None,
                                                                  True]
        text = pm.to_json()
        pm.save()
        again = _load(project_dir)
        assert again.to_json() == text
        assert again.data.segments == pm.data.segments

    def test_rules_1_project_writes_no_new_keys(self, tmp_path):
        project_dir = _project(tmp_path, _book(tmp_path), {}, rules=1)
        with open(os.path.join(project_dir, 'project.json'), encoding='utf-8') as f:
            raw = json.load(f)
        assert 'segmentation' not in raw
        assert not any('passthrough' in s for s in raw['segments'])
        pm = _load(project_dir)
        assert pm.data.segmentation is None  # rules 1
        assert [s.segment_id for s in pm.data.segments] == [
            'c_seg0001', 'c_seg0002', 'c_seg0003', 'c_seg0004', 'c_seg0005', 'c_seg0006',
            'c_seg0007', 'k_seg0001']
        assert all(s.status == SegmentStatus.PENDING for s in pm.data.segments)

    def test_existing_project_file_saved_as_before(self, tmp_path):
        # a project.json written before Phase 6: no segmentation, no passthrough
        project_dir = _project(tmp_path, _book(tmp_path), {'c_seg0001': '<h1 id="t">潮汐表</h1>'},
                               rules=1)
        path = os.path.join(project_dir, 'project.json')
        with open(path, encoding='utf-8') as f:
            text = f.read()
        assert _load(project_dir).to_json() == text

    def test_never_queued(self, tmp_path):
        pm = _load(_new_project(tmp_path, {}))
        assert [s.segment_id for s in pm.get_pending_segments(include_failed=True)] == [
            'c_seg0001', 'c_seg0003', 'c_seg0004', 'c_seg0005']

    def test_progress_counts_it_done(self, tmp_path):
        pm = _load(_new_project(tmp_path, {}))
        assert pm.get_progress() == {'total': 6, 'translated': 2, 'reviewed': 0, 'pending': 4,
                                     'failed': 0, 'passthrough': 2, 'percent': 33.3}
        assert pm.get_chapters() == [
            {'chapter_id': 'c', 'total': 5, 'translated': 1, 'reviewed': 0, 'failed': 0},
            {'chapter_id': 'k', 'total': 1, 'translated': 1, 'reviewed': 0, 'failed': 0}]


# --- Context ---------------------------------------------------------------------

def _state(segment_id, text, status=SegmentStatus.PENDING, translated='', chapter='a',
           segment_type=SegmentType.TEXT):
    return SegmentState(segment_id=segment_id, chapter_id=chapter, html=f'<p>{text}</p>',
                        text=text, word_count=1, segment_type=segment_type, status=status,
                        translated_text=translated)


class TestContextFromTranslations:

    @pytest.mark.parametrize('status', [SegmentStatus.TRANSLATED, SegmentStatus.REVIEWED])
    def test_previous_translation_tags_stripped(self, status):
        segments = [_state('a_1', 'One.', status, '<h2 id="x">一</h2>\n<p>甲 &amp; <em>乙</em></p>'),
                    _state('a_2', 'Two.'), _state('a_3', 'Three.', status, '<p>三</p>')]
        assert get_context(segments, 'a_2', translations=True) == ('一 甲 & 乙', 'Three.')
        assert get_context(segments, 'a_2') == ('One.', 'Three.')

    @pytest.mark.parametrize('status, text', [
        (SegmentStatus.PENDING, ''),
        (SegmentStatus.FAILED, '<p>壞掉的譯文</p>'),    # a failed translation is no context
        (SegmentStatus.TRANSLATED, ''),                # being stored by another worker
        (SegmentStatus.TRANSLATED, '<p> </p>'),
    ])
    def test_source_without_a_translation(self, status, text):
        segments = [_state('a_1', 'One.', status, text), _state('a_2', 'Two.')]
        assert get_context(segments, 'a_2', translations=True) == ('One.', '')

    def test_code_stepped_over_also_passthrough(self):
        code = _state('a_2', 'x = 1', SegmentStatus.TRANSLATED, 'x = 1',
                      segment_type=SegmentType.CODE)
        segments = [_state('a_1', 'One.', SegmentStatus.TRANSLATED, '<p>一</p>'), code,
                    _state('a_3', 'Three.')]
        assert get_context(segments, 'a_3', translations=True) == ('一', '')

    def test_not_across_chapters(self):
        segments = [_state('a_1', 'One.', SegmentStatus.TRANSLATED, '<p>一</p>'),
                    _state('b_1', 'Two.', chapter='b')]
        assert get_context(segments, 'b_1', translations=True) == ('', '')

    def test_translation_text(self):
        assert translation_text(_state('a', 'x', SegmentStatus.REVIEWED, '  <p>a&lt;b</p> ')) == 'a<b'
        assert translation_text(_state('a', 'x', SegmentStatus.PENDING, '<p>a</p>')) == ''

    def test_prompt_holds_the_tail_of_the_translation(self):
        head, tail = '開頭' * 30, '結尾' * 120
        prev = _state('a_1', 'Source words.', SegmentStatus.TRANSLATED, f'<p>{head}{tail}</p>')
        seg = _state('a_2', 'Current.')
        fake = FakeBackend(['<p k="1">現在。</p>'] * 2)
        engine = TranslationEngine(model='haiku', backend=fake)
        translate_segment(engine, seg, [prev, seg], translations=True)
        translate_segment(engine, seg, [prev, seg])
        new, old = fake.prompts
        assert '前文參考（不需翻譯）：\n' + (head + tail)[-200:] + '\n' in new
        assert head not in new and 'Source words.' not in new
        assert '前文參考（不需翻譯）：\nSource words.\n' in old


# --- Export ----------------------------------------------------------------------

def _source_pre(epub, member='OEBPS/Text/c.xhtml'):
    body = find_body(parse_xml(zipfile.ZipFile(epub).read(member)))
    return [serialize(e) for e in body.iter(f'{XHTML_NS}pre')]


class TestExport:

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_passthrough_code_identical_and_once(self, tmp_path, fmt):
        project_dir = _new_project(tmp_path)
        epub = _load(project_dir).data.epub_path
        report, z = _export(tmp_path, project_dir, fmt)
        body = find_body(parse_xml(z.read('OEBPS/Text/c.xhtml')))
        pres = [e for e in body.iter(f'{XHTML_NS}pre')]
        source = _source_pre(epub)
        plain = [serialize(e) for e in pres if e.get('class') == 'src']
        assert plain == [source[0]]                     # once, unchanged
        if fmt == 'chinese-only':
            assert pres[0].getparent() is body
        else:                                           # not wrapped as original + translation
            assert pres[0].getparent() is body
            assert len(pres) == 3                       # the commented code has two copies
        # a chapter of passthrough code only is the source's member, byte for byte
        assert z.read('OEBPS/Text/k.xhtml') == zipfile.ZipFile(epub).read('OEBPS/Text/k.xhtml')
        assert report.summary() == {'translated': 4, 'kept': 0, 'untranslated': 0, 'reasons': {}}
        assert [c.chapter_id for c in report.chapters] == ['c']

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_only_passthrough_leaves_the_book(self, tmp_path, fmt):
        project_dir = _new_project(tmp_path, {})
        epub = _load(project_dir).data.epub_path
        report, z = _export(tmp_path, project_dir, fmt)
        src = zipfile.ZipFile(epub)
        for name in ('OEBPS/Text/c.xhtml', 'OEBPS/Text/k.xhtml', 'OEBPS/nav.xhtml',
                     'OEBPS/toc.ncx'):
            assert z.read(name) == src.read(name)
        assert report.chapters == [] and report.summary()['untranslated'] == 4

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_merged_heading_labels_the_toc(self, tmp_path, fmt):
        _, z = _export(tmp_path, _new_project(tmp_path), fmt)
        nav = [_inner(a) for a in _root(z, 'OEBPS/nav.xhtml').iter(f'{XHTML_NS}a')]
        ncx = [''.join(t.itertext()) for t in _root(z, 'OEBPS/toc.ncx').iter(NCX_TEXT)]
        assert nav == ncx == ['潮汐表', '閱讀']

    def test_merged_segment_failing_the_check_kept_whole(self, tmp_path):
        # one block dropped: the whole merged segment (heading included) stays original
        translations = dict(TRANSLATIONS, c_seg0003='<h2 id="s">閱讀</h2>')
        report, z = _export(tmp_path, _new_project(tmp_path, translations), 'chinese-only')
        assert report.reasons() == {'structure_mismatch': 1}
        nav = [_inner(a) for a in _root(z, 'OEBPS/nav.xhtml').iter(f'{XHTML_NS}a')]
        assert nav == ['潮汐表', 'Reading']
        assert '<h2 id="s">Reading</h2>' in z.read('OEBPS/Text/c.xhtml').decode()

    def test_merged_segment_with_an_inline_tag_dropped_kept(self, tmp_path):
        translations = dict(TRANSLATIONS, c_seg0003='<h2 id="s">閱讀</h2>\n<p>讀第一列。</p>')
        report, _ = _export(tmp_path, _new_project(tmp_path, translations), 'chinese-only')
        assert report.reasons() == {'tag_count': 1}


# --- Validate, CLI ---------------------------------------------------------------

class TestCli:

    def test_validate_ignores_passthrough(self, tmp_path):
        from translate import cli
        project_dir = _new_project(tmp_path)
        pm = _load(project_dir)
        pm.data.segments[1].translated_text = 'changed'  # would fail code_changed if checked
        pm.save()
        result = CliRunner().invoke(cli, ['validate', project_dir, '--mark-failed'])
        assert result.exit_code == 0, result.output
        assert result.output.splitlines()[0].startswith('已檢查 4 段譯文：0 段未通過')
        assert _load(project_dir).data.segments[1].status == SegmentStatus.TRANSLATED

    def test_start_sends_no_passthrough_and_context_from_translations(self, tmp_path):
        from translate import cli
        project_dir = _new_project(tmp_path, {})
        # keyed by the segment's own HTML (the context is plain text)
        replies = {'Tide Tables</h1>': '<h1 k="1">潮汐表</h1>\n<p k="2">把表讀兩次。</p>',
                   'Reading</h2>': '<h2 k="1">閱讀</h2>\n<p k="2">讀<em>第一</em>列。</p>',
                   '# read it': '# 讀它\nload()', 'Tail</h2>': '<h2 k="1">尾</h2>'}

        def respond(request):
            prompt = request.messages[-1].content
            return next(v for k, v in replies.items() if k in prompt)
        fake = FakeBackend(responder=respond)
        with use_fake_backend(fake):
            result = CliRunner().invoke(cli, ['start', '--project', project_dir])
        assert result.exit_code == 0, result.output
        assert len(fake.requests) == 4
        assert not any('x = 1' in p for p in fake.prompts)
        # sequential: the previous text segment is translated when the next one starts
        assert '前文參考（不需翻譯）：\n潮汐表 把表讀兩次。\n' in fake.prompts[1]
        assert '前文參考（不需翻譯）：\n閱讀 讀第一列。\n' in fake.prompts[3]
        progress = _load(project_dir).get_progress()
        assert progress['translated'] == 6 and progress['percent'] == 100.0

    def test_rules_1_project_keeps_source_context(self, tmp_path):
        from translate import cli
        project_dir = _project(tmp_path, _book(tmp_path),
                               {'c_seg0001': '<h1 id="t">潮汐表</h1>'}, rules=1)
        fake = FakeBackend(responder=lambda r: '<p k="1">譯</p>')
        with use_fake_backend(fake):
            CliRunner().invoke(cli, ['start', '--project', project_dir])
        # rules 1: the code without a comment line is sent too, the context is the source
        assert any('x = 1\ny = 2' in p for p in fake.prompts)
        assert '前文參考（不需翻譯）：\nTide Tables\n' in fake.prompts[0]
        assert '潮汐表' not in ''.join(fake.prompts)

    def test_status_and_init_report_passthrough(self, tmp_path, monkeypatch):
        from translate import cli
        monkeypatch.chdir(tmp_path)
        epub = _book(tmp_path)
        result = CliRunner().invoke(cli, ['init', epub])
        assert result.exit_code == 0, result.output
        assert '分段完成：共 6 個段落' in result.output
        assert '其中 2 個是沒有註解的程式碼，原樣保留，不需翻譯' in result.output
        project_dir = os.path.join('projects', 'book')
        assert _load(project_dir).data.segmentation == 2
        result = CliRunner().invoke(cli, ['status', '--project', project_dir])
        assert '已翻譯：  2\n  其中原樣保留的程式碼（沒有註解，不需翻譯）：2\n' in result.output

    def test_status_without_passthrough(self, tmp_path):
        from translate import cli
        project_dir = _project(tmp_path, _book(tmp_path), {}, rules=1)
        result = CliRunner().invoke(cli, ['status', '--project', project_dir])
        assert '已翻譯：  0\n已審閱' in result.output


# --- Web -------------------------------------------------------------------------

@pytest.fixture
def web(tmp_path):
    from src.web.server import create_app
    from src.web.tasks import TaskManager
    TaskManager._instance = None
    projects = tmp_path / 'projects'
    projects.mkdir()
    _project(projects, _book(tmp_path), {'c_seg0001': TRANSLATIONS['c_seg0001']},
             name='p', rules=2)
    app = create_app(projects_dir=str(projects))
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client, str(projects / 'p')
    TaskManager._instance = None


class TestWeb:

    def test_review_page(self, web):
        client, _ = web
        page = client.get('/project/p/review/chapter/c').data.decode()
        seg = page[page.index('id="seg-c_seg0002"'):page.index('id="seg-c_seg0003"')]
        assert 'status-passthrough' in seg and '原樣保留' in seg
        assert 'textarea' not in seg and 'retranslateSegment' not in seg
        other = page[page.index('id="seg-c_seg0003"'):]
        assert 'textarea' in other and 'status-passthrough' not in other

    @pytest.mark.parametrize('path, body', [
        ('', {'text': 'x = 2'}), ('/approve', {}), ('/approve', {'text': 'x = 1\ny = 2'}),
        ('/retranslate', None)])
    def test_changes_refused(self, web, path, body):
        client, project_dir = web
        before = open(os.path.join(project_dir, 'project.json'), 'rb').read()
        fake = FakeBackend([])
        with use_fake_backend(fake):
            r = client.post(f'/api/projects/p/segments/c_seg0002{path}', json=body)
        assert r.status_code == 409
        assert '不需翻譯' in r.get_json()['error']
        assert open(os.path.join(project_dir, 'project.json'), 'rb').read() == before
        assert fake.requests == []

    def test_other_segment_still_editable(self, web):
        client, _ = web
        r = client.post('/api/projects/p/segments/c_seg0001/approve', json={})
        assert r.status_code == 200

    def test_start_with_segment_ids_skips_passthrough(self, web):
        from unittest.mock import patch
        from src.web.tasks import TaskManager
        client, _ = web
        tm = TaskManager()
        with patch.object(tm, '_run_translation'):
            task = tm.start_translation('p', os.path.dirname(web[1]),
                                        segment_ids=['c_seg0002', 'c_seg0003', 'k_seg0001'])
        assert task.total == 1
        assert task.segment_queue.get_nowait().segment_id == 'c_seg0003'
        tm.stop_translation('p')

    def test_progress_api(self, web):
        client, _ = web
        progress = client.get('/api/projects/p/progress').get_json()
        assert progress['translated'] == 3 and progress['passthrough'] == 2

    def test_created_project_uses_rules_2(self, tmp_path):
        from src.web.server import create_app
        app = create_app(projects_dir=str(tmp_path / 'made'))
        app.config['TESTING'] = True
        with app.test_client() as client:
            r = client.post('/api/projects', json={'epub_path': _book(tmp_path),
                                                   'project_name': 'new'})
        assert r.status_code == 200, r.get_json()
        pm = _load(str(tmp_path / 'made' / 'new'))
        assert pm.data.segmentation == 2
        assert [s.passthrough for s in pm.data.segments] == [False, True, False, False, False,
                                                             True]


class TestReaderSeesTheSame:

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_rules_1_and_2_export_alike(self, tmp_path, fmt):
        """The same translations, cut by rules 1 and by rules 2, give the same
        chapter in every format: merging a heading changes the requests, not
        what a reader sees (each block keeps its own original/translation pair)."""
        rules_1 = {'c_seg0001': '<h1 id="t">潮汐表</h1>', 'c_seg0002': '<p>把表讀兩次。</p>',
                   'c_seg0004': '<h2 id="s">閱讀</h2>', 'c_seg0005': '<p>讀<em>第一</em>列。</p>',
                   'c_seg0006': '# 讀它\nload()', 'c_seg0007': '<h2>尾</h2>'}
        old = _project(tmp_path, _book(tmp_path), rules_1, name='old', rules=1)
        new = _project(tmp_path, _book(tmp_path), TRANSLATIONS, name='new', rules=2)
        _, z_old = _export(tmp_path, old, fmt, name='old.epub')
        _, z_new = _export(tmp_path, new, fmt, name='new.epub')
        for member in ('OEBPS/Text/c.xhtml', 'OEBPS/Text/k.xhtml', 'OEBPS/nav.xhtml',
                       'OEBPS/toc.ncx'):
            assert z_old.read(member) == z_new.read(member)


class TestContextOnEveryPath:
    """Mutation testing, Phase 6 run 1: every caller passes the project's rules."""

    @staticmethod
    def _prompt_of(fake, mark):
        return next(p for p in fake.prompts if mark in p)

    def _rules_1(self, tmp_path):
        return _project(tmp_path, _book(tmp_path), {'c_seg0002': '<p>把表讀兩次。</p>'},
                        name='old', rules=1)

    def _rules_2(self, tmp_path):
        return _project(tmp_path, _book(tmp_path), {'c_seg0001': TRANSLATIONS['c_seg0001']},
                        name='new', rules=2)

    @staticmethod
    def _reply(request):
        prompt = request.messages[-1].content
        if '# read it' in prompt:
            return '# 讀它\nload()'
        if 'x = 1\ny = 2' in prompt:  # the code without comments (rules 1 sends it)
            return 'x = 1\ny = 2'
        return '<h2 k="1">閱讀</h2>\n<p k="2">讀<em>第一</em>列。</p>' if 'Reading</h2>' in prompt \
            else '<h2 k="1">標</h2>'

    @pytest.mark.parametrize('workers', ['1', '2'])
    def test_cli(self, tmp_path, workers):
        from translate import cli
        new, old = self._rules_2(tmp_path), self._rules_1(tmp_path)
        for project_dir, before in ((new, '前文參考（不需翻譯）：\n潮汐表 把表讀兩次。\n'),
                                    (old, '前文參考（不需翻譯）：\nRead the tables twice.\n')):
            fake = FakeBackend(responder=self._reply)
            with use_fake_backend(fake):
                CliRunner().invoke(cli, ['start', '--project', project_dir, '-w', workers])
            assert before in self._prompt_of(fake, 'Reading</h2>')

    def test_web(self, tmp_path):
        from src.web.tasks import TaskManager
        projects = tmp_path / 'projects'
        projects.mkdir()
        for rules, name, translations, before in (
                (2, 'new', {'c_seg0001': TRANSLATIONS['c_seg0001']},
                 '前文參考（不需翻譯）：\n潮汐表 把表讀兩次。\n'),
                (1, 'old', {'c_seg0002': '<p>把表讀兩次。</p>'},
                 '前文參考（不需翻譯）：\nRead the tables twice.\n')):
            _project(projects, _book(tmp_path), translations, name=name, rules=rules)
            TaskManager._instance = None
            tm = TaskManager()
            fake = FakeBackend(responder=self._reply)
            with use_fake_backend(fake):
                task = tm.start_translation(name, str(projects))
                task._coordinator.join(timeout=30)
            assert task.state == 'completed'
            assert before in self._prompt_of(fake, 'Reading</h2>')
            TaskManager._instance = None

    def test_unknown_rules_message(self):
        with pytest.raises(ValueError, match='^unknown segmentation rules: 3$'):
            Segmenter(rules=3)


# --- Review round 1 ----------------------------------------------------------------

class TestRound1ContextText:

    @pytest.mark.parametrize('translated, text', [
        ('<p title="x > y">正文</p>', '正文'),                     # `>` inside an attribute
        ('<p alt="a>b" title=\'c > d\'>甲<em class="e>f">乙</em></p>', '甲乙'),
        ('<p><![CDATA[a<b & c]]>一</p>', 'a<b & c一'),
        ('<p>\n      一\n      二</p>\n\n  <p>三</p>', '一 二 三'),  # whitespace collapsed
        ('<p>a&#160;b&amp;c</p>', 'a b&c'),
    ])
    def test_parsed_text(self, translated, text):
        assert translation_text(_state('a', 'x', SegmentStatus.TRANSLATED, translated)) == text

    def test_mutex_held_only_while_the_context_is_read(self, monkeypatch):
        import src.core.translator as translator
        events = []

        class Recording:
            def __enter__(self):
                events.append('lock')

            def __exit__(self, *exc):
                events.append('unlock')
        real = translator.get_context

        def context(*args):
            events.append('context')
            return real(*args)
        monkeypatch.setattr(translator, 'get_context', context)
        fake = FakeBackend(responder=lambda r: events.append('call') or '<p k="1">譯</p>')
        seg = _state('a_1', 'One.')
        translate_segment(TranslationEngine(model='haiku', backend=fake), seg, [seg],
                          translations=True, mutex=Recording())
        assert events == ['lock', 'context', 'unlock', 'call']

    def test_callers_pass_the_project_mutex(self, tmp_path, monkeypatch):
        """The web worker and the parallel CLI read the context under pm.mutex."""
        import src.core.translator as translator
        from translate import cli
        from src.web.tasks import TaskManager
        seen = []
        real = translator.translate_segment

        def recording(*args, **kwargs):
            seen.append(kwargs.get('mutex'))
            return real(*args, **kwargs)
        monkeypatch.setattr('translate.translate_segment', recording)
        monkeypatch.setattr('src.web.tasks.translate_segment', recording)
        project_dir = _new_project(tmp_path, {})
        with use_fake_backend(FakeBackend(responder=lambda r: '<h2 k="1">標</h2>')):
            CliRunner().invoke(cli, ['start', '--project', project_dir, '-w', '2'])
        assert seen and all(m is ProjectManager(project_dir).mutex for m in seen)
        seen.clear()
        projects = tmp_path / 'projects'
        projects.mkdir()
        _project(projects, _book(tmp_path), {}, name='w', rules=2)
        TaskManager._instance = None
        tm = TaskManager()
        with use_fake_backend(FakeBackend(responder=lambda r: '<h2 k="1">標</h2>')):
            task = tm.start_translation('w', str(projects))
            task._coordinator.join(timeout=30)
        assert seen and all(m is ProjectManager(str(projects / 'w')).mutex for m in seen)
        TaskManager._instance = None


class TestRound1Marker:

    def _file(self, tmp_path, **changes):
        project_dir = _new_project(tmp_path, {})
        path = os.path.join(project_dir, 'project.json')
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
        for key, value in changes.items():
            if value is KeyError:
                raw.pop(key)
            else:
                raw[key] = value
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        return project_dir, path

    @pytest.mark.parametrize('value', ['2', 3, 0, True, False, 1.0, 2.0, None, [2]])
    def test_load_rejects(self, tmp_path, value):
        project_dir, _ = self._file(tmp_path, segmentation=value)
        with pytest.raises(ValueError, match='unknown segmentation rules'):
            ProjectManager(project_dir).load()

    @pytest.mark.parametrize('value', ['2', 3, True, 1.0, None])
    def test_create_and_segmenter_reject(self, tmp_path, value):
        if value is not None:
            with pytest.raises(ValueError):
                ProjectManager(str(tmp_path / 'x')).create('b.epub', 'T', 'A', [],
                                                           segmentation=value)
            assert not os.path.exists(tmp_path / 'x')
        with pytest.raises(ValueError, match='^unknown segmentation rules: '):
            Segmenter(rules=value)

    @pytest.mark.parametrize('value', [1, 2])
    def test_explicit_value_saved_as_it_is(self, tmp_path, value):
        project_dir, path = self._file(tmp_path, segmentation=value)
        with open(path, encoding='utf-8') as f:
            text = f.read()
        pm = _load(project_dir)
        assert pm.data.segmentation == value
        assert pm.to_json() == text

    def test_absent_is_rules_1(self, tmp_path):
        project_dir, path = self._file(tmp_path, segmentation=KeyError)
        with open(path, encoding='utf-8') as f:
            text = f.read()
        pm = _load(project_dir)
        assert pm.data.segmentation is None
        assert pm.to_json() == text and '"segmentation"' not in text

    def test_create_writes_the_given_rules(self, tmp_path):
        for value in (1, 2):
            pm = ProjectManager(str(tmp_path / f'p{value}'))
            pm.create('b.epub', 'T', 'A', [], segmentation=value)
            with open(pm.project_file, encoding='utf-8') as f:
                assert json.load(f)['segmentation'] == value


class TestRound1StateGuards:

    def test_state_layer_refuses_passthrough(self, tmp_path):
        from src.core.project import Issue, PassthroughError
        from src.core.translator import TranslationOutcome
        pm = _load(_new_project(tmp_path, {}))
        seg = pm.data.segments[1]
        before = pm.to_json()
        outcome = TranslationOutcome(text='y = 0', status=SegmentStatus.TRANSLATED, issues=[],
                                     model='haiku', usage=None)
        assert pm.apply_outcome('c_seg0002', outcome) is False
        assert pm.apply_outcome('c_seg0002', outcome, revision=0) is False
        for change in (lambda: pm.update_translation('c_seg0002', 'y = 0'),
                       lambda: pm.mark_reviewed('c_seg0002'),
                       lambda: pm.mark_reviewed('c_seg0002', 'y = 0'),
                       lambda: pm.mark_failed('c_seg0002', [Issue('structure', 'x', 'd', True)]),
                       lambda: pm.mark_translated('c_seg0002', 'y = 0')):
            with pytest.raises(PassthroughError, match='c_seg0002'):
                change()
        assert pm.to_json() == before
        assert seg.translated_text == 'x = 1\ny = 2' and seg.status == SegmentStatus.TRANSLATED
        # other segments still change
        assert pm.apply_outcome('c_seg0004', outcome) is True


class TestRound1Search:

    def test_search_marks_and_page_blocks_passthrough(self, web):
        client, _ = web
        found = client.post('/api/projects/p/search-segments', json={'query': 'x = 1'}).get_json()
        assert [(s['segment_id'], s['passthrough']) for s in found['segments']] == [
            ('c_seg0002', True)]
        found = client.post('/api/projects/p/search-segments', json={'query': 'Tide'}).get_json()
        assert [(s['segment_id'], s['passthrough']) for s in found['segments']] == [
            ('c_seg0001', False)]
        page = client.get('/project/p/search').data.decode()
        assert ':disabled="seg.passthrough"' in page
        assert 'x-show="seg.passthrough"' in page and '原樣保留' in page
        assert '.filter(s => !s.passthrough)' in page           # select all skips them
        assert 'if (data.total === 0)' in page                  # a run of nothing is explained


class TestRound1Guards:

    def test_cli_export_refuses_without_a_model_translation(self, tmp_path):
        from translate import cli
        project_dir = _new_project(tmp_path, {})
        result = CliRunner().invoke(cli, ['export', '-p', project_dir, '-f', 'chinese-only'])
        assert result.exit_code == 1 and '尚未翻譯任何段落' in result.output
        assert not os.path.exists(os.path.join(project_dir, 'output'))
        pm = _load(project_dir)
        pm.mark_translated('c_seg0005', '<h2>尾</h2>')
        pm.save()
        result = CliRunner().invoke(cli, ['export', '-p', project_dir, '-f', 'chinese-only'])
        assert result.exit_code == 0, result.output

    def test_web_export_refuses_without_a_model_translation(self, tmp_path):
        from src.web.server import create_app
        from src.web.tasks import TaskManager
        TaskManager._instance = None
        projects = tmp_path / 'projects'
        projects.mkdir()
        _project(projects, _book(tmp_path), {}, name='p', rules=2)
        (projects / 'empty').mkdir()
        client = create_app(projects_dir=str(projects)).test_client()
        r = client.post('/api/projects/p/export', json={'format': 'chinese-only'})
        assert r.status_code == 400 and '尚未翻譯任何段落' in r.get_json()['error']
        assert client.post('/api/projects/empty/export', json={}).status_code == 404
        pm = _load(str(projects / 'p'))
        pm.mark_translated('c_seg0005', '<h2>尾</h2>')
        pm.save()
        r = client.post('/api/projects/p/export', json={'format': 'chinese-only'})
        assert r.status_code == 200
        TaskManager._instance = None

    def test_overview_and_home_status(self, tmp_path):
        from src.web.blueprints.home import _get_status
        from src.web.server import create_app
        from src.web.tasks import TaskManager
        TaskManager._instance = None
        projects = tmp_path / 'projects'
        projects.mkdir()
        project_dir = _project(projects, _book(tmp_path), {}, name='p', rules=2)
        client = create_app(projects_dir=str(projects)).test_client()
        page = client.get('/project/p/overview').data.decode()
        assert '已翻譯 0 字' in page and '含 2 段原樣保留的程式碼（不需翻譯）' in page
        assert client.get('/api/projects').get_json()[0]['status'] == 'pending'
        pm = _load(project_dir)
        for seg in pm.data.segments:
            if not seg.passthrough:
                pm.mark_translated(seg.segment_id, TRANSLATIONS.get(seg.segment_id, 'x'))
        pm.save()
        assert client.get('/api/projects').get_json()[0]['status'] == 'in_progress'
        words = sum(s.word_count for s in pm.data.segments if not s.passthrough)
        assert f'已翻譯 {words:,} 字' in client.get('/project/p/overview').data.decode()
        for seg in pm.data.segments:
            if not seg.passthrough:
                seg.status = SegmentStatus.REVIEWED
        pm.save()
        assert client.get('/api/projects').get_json()[0]['status'] == 'completed'
        base = {'total': 4, 'translated': 2, 'reviewed': 0, 'pending': 2, 'failed': 0,
                'passthrough': 2, 'percent': 50.0}
        assert _get_status(base) == 'pending'
        assert _get_status(dict(base, translated=3)) == 'in_progress'
        assert _get_status(dict(base, reviewed=2, translated=2, pending=0)) == 'completed'
        assert _get_status(dict(base, reviewed=1, translated=3, pending=0)) == 'in_progress'
        TaskManager._instance = None

    def test_export_report_never_counts_passthrough_untranslated(self, tmp_path):
        project_dir = _new_project(tmp_path, {'c_seg0005': '<h2>尾</h2>'})
        path = os.path.join(project_dir, 'project.json')
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
        raw['segments'][1]['translated_text'] = ''  # as for code with no text to take
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(raw, f)
        report, _ = _export(tmp_path, project_dir, 'chinese-only')
        # pending: c_seg0001, c_seg0003, c_seg0004; both passthrough segments left out
        assert report.summary() == {'translated': 1, 'kept': 0, 'untranslated': 3, 'reasons': {}}


class TestRound1Shebang:

    def test_check_code_keeps_the_shebang(self):
        from src.core.validation import check_code, code_lines
        source = '#!/bin/sh\n# say hi\necho hi'
        assert not check_code(source, '#!/bin/sh\n# 打招呼\necho hi').blocking
        assert [i.code for i in check_code(source, '#!/bin/bash\n# 打招呼\necho hi').blocking] == [
            'code_changed']
        assert [i.code for i in check_code(source, '#! 直譯器\n# 打招呼\necho hi').blocking] == [
            'code_changed']
        assert code_lines(source) == ['#!/bin/sh', 'echo hi']
        assert code_lines('echo\n#!x') == ['echo']      # not on the first line: a comment
        assert code_lines('  #!x\ny') == ['y']           # indented: no shebang

    def test_prompt_says_so(self):
        from src.core.translator import CODE_PROMPT
        assert '第一個非空白行的 #! 指定直譯器，也不是註解' in CODE_PROMPT


class TestRound1Retranslate:

    def test_endpoint_context(self, tmp_path):
        from src.web.server import create_app
        from src.web.tasks import TaskManager
        projects = tmp_path / 'projects'
        projects.mkdir()
        _project(projects, _book(tmp_path), {'c_seg0001': TRANSLATIONS['c_seg0001']},
                 name='new', rules=2)
        _project(projects, _book(tmp_path), {'c_seg0002': '<p>把表讀兩次。</p>'}, name='old',
                 rules=1)
        TaskManager._instance = None
        client = create_app(projects_dir=str(projects)).test_client()
        for name, seg_id, before in (('new', 'c_seg0003', '潮汐表 把表讀兩次。'),
                                     ('old', 'c_seg0004', 'Read the tables twice.')):
            fake = FakeBackend(responder=lambda r: '<h2 k="1">閱讀</h2>\n<p k="2">讀<em>第一</em>列。</p>'
                               if 'Read the' in r.messages[-1].content.split('原文')[-1]
                               else '<h2 k="1">閱讀</h2>')
            with use_fake_backend(fake):
                client.post(f'/api/projects/{name}/segments/{seg_id}/retranslate')
            assert f'前文參考（不需翻譯）：\n{before}\n' in fake.prompts[0]
        TaskManager._instance = None


BR_BOOK = xhtml('<h2 id="v">Verse</h2>First line<br/>Second line<br/>Third line'
                '<p>After the <em>verse</em>.</p><pre>plain()</pre>')


class TestRound1MergedExport:

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_heading_with_br_runs(self, tmp_path, fmt):
        nav = xhtml('<nav epub:type="toc"><ol><li><a href="Text/c.xhtml#v">Verse</a></li>'
                    '</ol></nav>', head='<title>Contents</title>')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', BR_BOOK)}, nav=nav, ncx=None)
        merged = '<h2 id="v">詩</h2>\n第一行\n第二行\n第三行\n<p>詩<em>之後</em>。</p>'
        new = _project(tmp_path, epub, {'c_seg0001': merged}, name='new', rules=2)
        assert [(s.block_start, s.block_end, s.passthrough)
                for s in _load(new).data.segments] == [(0, 5, False), (5, 6, True)]
        old = _project(tmp_path, epub, {
            'c_seg0001': '<h2 id="v">詩</h2>',
            'c_seg0002': '第一行\n第二行\n第三行\n<p>詩<em>之後</em>。</p>'}, name='old', rules=1)
        report, z_new = _export(tmp_path, new, fmt, name='new.epub')
        _, z_old = _export(tmp_path, old, fmt, name='old.epub')
        assert report.summary()['translated'] == 1 and report.summary()['kept'] == 0
        chapter = z_new.read('OEBPS/Text/c.xhtml').decode()
        for text in ('詩', '第一行', '第二行', '第三行', '之後'):
            assert text in chapter
        assert chapter.count('First line') == (0 if fmt == 'chinese-only' else 1)
        assert chapter.count('plain()') == 1
        assert z_new.read('OEBPS/Text/c.xhtml') == z_old.read('OEBPS/Text/c.xhtml')
        assert _inner(next(_root(z_new, 'OEBPS/nav.xhtml').iter(f'{XHTML_NS}a'))) == '詩'


# --- Review round 2 ----------------------------------------------------------------

class TestRound2Shebang:
    """The shebang is the first non-blank line: the source keeps the newline
    after <pre>, a cleaned reply does not."""

    def test_after_a_leading_newline_passthrough(self):
        segs = _segments('<pre>\n#!/bin/sh\necho hi</pre>')
        assert segs[0].text == '\n#!/bin/sh\necho hi' and segs[0].passthrough
        assert has_comment_line('\n \n#!/bin/sh\n# c\nls')
        assert not has_comment_line('\n\t\n#!/bin/sh\nls')

    def test_check_code_with_leading_blank_lines(self):
        from src.core.validation import check_code, code_lines
        assert not check_code('\n#!/bin/sh\n# c\nls', '#!/bin/sh\n# 譯\nls').blocking
        assert not check_code('#!/bin/sh\nls', '\n\n#!/bin/sh\nls\n').blocking
        assert [i.code for i in check_code('\n#!/bin/sh\nls', '#!/bin/bash\nls').blocking] == [
            'code_changed']
        assert [i.code for i in check_code('\n#!/bin/sh\nls', '\n#! 譯\nls').blocking] == [
            'code_changed']
        assert code_lines('\n#!/bin/sh\nls') == ['#!/bin/sh', 'ls']
        assert code_lines('\n\necho\n#!x') == ['echo']        # later #!: a comment line
        assert code_lines('\n#!a\n#!b\nx') == ['#!a', 'x']     # only the first one is code

    @pytest.mark.parametrize('reply', ['#!/bin/sh\n# 譯\nls', '\n\n#!/bin/sh\n# 譯\nls\n',
                                       '```sh\n#!/bin/sh\n# 譯\nls\n```'])
    def test_through_the_reply_cleaning(self, reply):
        seg = SegmentState(segment_id='a_1', chapter_id='a', html='<pre>\n#!/bin/sh\n# c\nls</pre>',
                           text='\n#!/bin/sh\n# c\nls', word_count=3,
                           segment_type=SegmentType.CODE)
        fake = FakeBackend([reply])
        outcome = translate_segment(TranslationEngine(model='haiku', backend=fake), seg, [seg])
        assert outcome.status == SegmentStatus.TRANSLATED, outcome.issues
        assert outcome.text == '#!/bin/sh\n# 譯\nls' and len(fake.requests) == 1

    def test_changed_shebang_rejected_through_the_reply_cleaning(self):
        seg = SegmentState(segment_id='a_1', chapter_id='a', html='<pre>\n#!/bin/sh\n# c\nls</pre>',
                           text='\n#!/bin/sh\n# c\nls', word_count=3,
                           segment_type=SegmentType.CODE)
        fake = FakeBackend(responder=lambda r: '#!/bin/zsh\n# 譯\nls')
        outcome = translate_segment(TranslationEngine(model='haiku', backend=fake), seg, [seg])
        assert outcome.status == SegmentStatus.FAILED
        assert outcome.issues[0].code == 'code_changed'

    def test_code_of_blank_lines_only(self):
        from src.core.validation import check_code, code_lines, comment_flags
        assert comment_flags('') == [] and comment_flags('\n \n') == [('', False), (' ', False)]
        assert not has_comment_line('\n\n') and code_lines('\n \n') == []
        assert not check_code('\n', '').blocking


class TestRound2WebExportGuard:

    @pytest.mark.parametrize('content', ['{not json', '{"segmentation": "2"}'])
    def test_unreadable_project_is_a_json_error(self, tmp_path, content):
        from src.web.server import create_app
        from src.web.tasks import TaskManager
        TaskManager._instance = None
        projects = tmp_path / 'projects'
        project_dir = _project(projects.parent, _book(tmp_path), {}, name='projects/p', rules=2)
        path = os.path.join(project_dir, 'project.json')
        if content.startswith('{"'):
            with open(path, encoding='utf-8') as f:
                raw = json.load(f)
            raw['segmentation'] = '2'
            content = json.dumps(raw)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        client = create_app(projects_dir=str(projects)).test_client()
        r = client.post('/api/projects/p/export', json={'format': 'chinese-only'})
        assert r.status_code == 500 and r.is_json
        assert r.get_json()['error'].startswith('無法讀取專案')
        TaskManager._instance = None

    def test_project_gone_before_the_load(self, tmp_path, monkeypatch):
        from src.web.server import create_app
        from src.web.tasks import TaskManager
        TaskManager._instance = None
        projects = tmp_path / 'projects'
        projects.mkdir()
        _project(projects, _book(tmp_path), {}, name='p', rules=2)

        def gone(self, name, projects_dir):
            raise FileNotFoundError('project.json')
        monkeypatch.setattr(TaskManager, 'get_or_load_pm', gone)
        client = create_app(projects_dir=str(projects)).test_client()
        r = client.post('/api/projects/p/export', json={'format': 'chinese-only'})
        assert r.status_code == 404 and r.get_json() == {'error': '專案不存在'}
        TaskManager._instance = None
