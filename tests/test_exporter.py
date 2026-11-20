"""Tests for the ZIP exporter (Phase 3). Books are raw ZIP archives with
invented text; every assertion reads the output members from the ZIP."""

import json
import os
import zipfile

import pytest
from click.testing import CliRunner
from lxml import etree

from src.core.migrate import migrate_project
from src.core.project import ProjectManager, SegmentStatus, sha256_file
from src.core.segmenter import Segmenter
from src.epub import exporter
from src.epub.blocks import serialize
from src.epub.exporter import (
    AMBIGUOUS_SOURCE, BILINGUAL_CSS, CHAPTER_MISMATCH, CHAPTER_UNREADABLE, CODE_MARKUP,
    FORMATS, MISSING_ANCHOR, NOT_WELL_FORMED, SCHEMA_1_MESSAGE, STRUCTURE_MISMATCH, ExportError,
    export_project,
)
from src.epub.extractor import EPUBExtractor, parse_xml
from tests.conftest import RAW_CHAPTER_ONE, RAW_CHAPTER_TWO, RAW_NAV, RAW_NCX, write_raw_epub, xhtml

XHTML_NS = '{http://www.w3.org/1999/xhtml}'
XML_LANG = '{http://www.w3.org/XML/1998/namespace}lang'
BILINGUAL = ('bilingual-horizontal', 'bilingual-vertical')

# Translations of the raw_epub fixture's segments (see test_segments_of_the_fixture)
RAW_TRANSLATIONS = {
    'one_seg0001': '<h1>守燈人</h1>',
    'one_seg0002': ('<p class="other" id="moved">米拉在黃昏爬上<em>螺旋</em>樓梯。</p>\n'
                    '身體下的一行文字。<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                    '<rect width="5" height="5"/></svg>\n歌的第一行\n歌的第二行\n歌的第三行'),
    'two_seg0001': '<h2>第二晚</h2>',
    'two_seg0002': '<p>港口的鐘響了兩次。</p>',
}


def _raw_book(tmp_path, chapters=None, nav=RAW_NAV, ncx=RAW_NCX, extra=None, version='3.0',
              name='book.epub'):
    """A raw EPUB: chapters maps an item id to (href under OEBPS/Text, XHTML)."""
    if chapters is None:
        chapters = {'one': ('one.xhtml', RAW_CHAPTER_ONE),
                    'two': ('chapter%20two.xhtml', RAW_CHAPTER_TWO)}
    items, members, spine = [], {}, []
    if nav is not None:
        items.append(('nav', 'nav.xhtml', 'application/xhtml+xml', 'nav'))
        members['OEBPS/nav.xhtml'] = nav
        spine.append('nav')
    if ncx is not None:
        items.append(('ncx', 'toc.ncx', 'application/x-dtbncx+xml', None))
        members['OEBPS/toc.ncx'] = ncx
    items.append(('css', 'Styles/book.css', 'text/css', None))
    members['OEBPS/Styles/book.css'] = 'p { margin: 0; }'
    for cid, (href, content) in chapters.items():
        items.append((cid, f'Text/{href}', 'application/xhtml+xml', None))
        members['OEBPS/Text/' + href.replace('%20', ' ')] = content
        spine.append(cid)
    for member, (item, content) in (extra or {}).items():
        if item is not None:
            items.append(item)
        members[member] = content
    return write_raw_epub(tmp_path / name, items, spine + spine[-1:], members, version=version)


def _project(tmp_path, epub, translations, statuses=None, name='proj'):
    """A schema 2 project of `epub` with the given translations (status
    translated unless `statuses` says otherwise)."""
    ex = EPUBExtractor(epub)
    ex.load()
    segments = [s for ch in ex.extract_chapters()
                for s in Segmenter().segment_chapter(ch.id, ch.body)]
    project_dir = str(tmp_path / name)
    pm = ProjectManager(project_dir)
    pm.create(epub, 'T', 'A', segments, source_sha256=sha256_file(epub))
    for seg in pm.data.segments:
        if seg.segment_id in translations:
            pm.mark_translated(seg.segment_id, translations[seg.segment_id])
            if statuses and seg.segment_id in statuses:
                seg.status = statuses[seg.segment_id]
    pm.save()
    return project_dir


def _export(tmp_path, project_dir, fmt, name=None):
    out = str(tmp_path / (name or f'{fmt}.epub'))
    return export_project(project_dir, out, fmt), zipfile.ZipFile(out)


def _root(z, member):
    return parse_xml(z.read(member))


def _body(z, member):
    return _root(z, member).find(f'{XHTML_NS}body')


def _inner(el):
    """Serialization of an element's content."""
    out = serialize(el)
    return out[out.index('>') + 1:out.rindex('<')]


@pytest.fixture
def raw_project(tmp_path):
    return _project(tmp_path, _raw_book(tmp_path), RAW_TRANSLATIONS)


class TestFixture:

    def test_segments_of_the_fixture(self, raw_project):
        segments = ProjectManager(raw_project).load().segments
        assert [(s.segment_id, s.block_start, s.block_end) for s in segments] == [
            ('one_seg0001', 0, 1), ('one_seg0002', 1, 6),
            ('two_seg0001', 0, 1), ('two_seg0002', 1, 2)]


class TestArchive:

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_mimetype_first_and_stored_members_once(self, tmp_path, raw_project, fmt):
        _, z = _export(tmp_path, raw_project, fmt)
        infos = z.infolist()
        assert infos[0].filename == 'mimetype'
        assert infos[0].compress_type == zipfile.ZIP_STORED
        assert z.read('mimetype') == b'application/epub+zip'
        names = [i.filename for i in infos]
        assert len(names) == len(set(names))
        src = zipfile.ZipFile(ProjectManager(raw_project).load().epub_path)
        expected = src.namelist() + (['OEBPS/bilingual.css'] if fmt in BILINGUAL else [])
        assert names == expected

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_members_outside_the_allowed_set_identical(self, tmp_path, fmt):
        extra = {'OEBPS/Images/pic.png': (('pic', 'Images/pic.png', 'image/png', None),
                                          b'\x89PNG invented bytes'),
                 'OEBPS/Text/three.xhtml': (('three', 'Text/three.xhtml',
                                             'application/xhtml+xml', None),
                                            xhtml('<p>Never translated.</p>'))}
        epub = _raw_book(tmp_path, extra=extra)
        project_dir = _project(tmp_path, epub, RAW_TRANSLATIONS)
        _, z = _export(tmp_path, project_dir, fmt)
        src = zipfile.ZipFile(epub)
        allowed = {'OEBPS/Text/one.xhtml', 'OEBPS/Text/chapter two.xhtml',
                   'OEBPS/content.opf', 'OEBPS/toc.ncx', 'OEBPS/nav.xhtml', 'mimetype'}
        for info in src.infolist():
            if info.filename not in allowed:
                assert z.read(info.filename) == src.read(info.filename), info.filename
                assert z.getinfo(info.filename).compress_type == info.compress_type
        assert z.read('OEBPS/Text/one.xhtml') != src.read('OEBPS/Text/one.xhtml')

    def test_stored_member_stays_stored(self, tmp_path):
        epub = _raw_book(tmp_path)
        path = str(tmp_path / 'stored.epub')
        with zipfile.ZipFile(epub) as src, zipfile.ZipFile(path, 'w') as dst:
            for info in src.infolist():
                dst.writestr(info.filename, src.read(info.filename),
                             compress_type=zipfile.ZIP_STORED)
        project_dir = _project(tmp_path, path, RAW_TRANSLATIONS)
        _, z = _export(tmp_path, project_dir, 'chinese-only')
        assert z.getinfo('OEBPS/Styles/book.css').compress_type == zipfile.ZIP_STORED
        assert z.getinfo('OEBPS/Text/one.xhtml').compress_type == zipfile.ZIP_DEFLATED

    def test_duplicate_member_written_once(self, tmp_path):
        epub = _raw_book(tmp_path)
        with zipfile.ZipFile(epub, 'a') as z:
            with pytest.warns(UserWarning):
                z.writestr('OEBPS/Styles/book.css', 'p { margin: 1em; }')
        project_dir = _project(tmp_path, epub, RAW_TRANSLATIONS)
        _, z = _export(tmp_path, project_dir, 'chinese-only')
        names = z.namelist()
        assert names.count('OEBPS/Styles/book.css') == 1
        assert z.read('OEBPS/Styles/book.css') == b'p { margin: 1em; }'

    def test_missing_mimetype_written(self, tmp_path):
        epub = _raw_book(tmp_path)
        path = str(tmp_path / 'nomime.epub')
        with zipfile.ZipFile(epub) as src, zipfile.ZipFile(path, 'w') as dst:
            for info in src.infolist():
                if info.filename != 'mimetype':
                    dst.writestr(info, src.read(info.filename))
        project_dir = _project(tmp_path, path, RAW_TRANSLATIONS)
        _, z = _export(tmp_path, project_dir, 'chinese-only')
        assert z.infolist()[0].filename == 'mimetype'
        assert z.read('mimetype') == b'application/epub+zip'

    def test_output_directory_created_and_no_temporary_file(self, tmp_path, raw_project):
        out = tmp_path / 'nested' / 'dir' / 'out.epub'
        export_project(raw_project, str(out), 'chinese-only')
        assert os.listdir(out.parent) == ['out.epub']

    def test_failed_write_leaves_nothing(self, tmp_path, raw_project, monkeypatch):
        out = tmp_path / 'out'
        out.mkdir()

        def boom(*a, **k):
            raise RuntimeError('disk full')
        monkeypatch.setattr(exporter.os, 'replace', boom)
        with pytest.raises(RuntimeError):
            export_project(raw_project, str(out / 'x.epub'), 'chinese-only')
        assert os.listdir(out) == []

    def test_project_files_untouched(self, tmp_path, raw_project):
        before = sorted(os.listdir(raw_project))
        data = open(os.path.join(raw_project, 'project.json'), 'rb').read()
        _export(tmp_path, raw_project, 'bilingual-horizontal')
        assert sorted(os.listdir(raw_project)) == before
        assert open(os.path.join(raw_project, 'project.json'), 'rb').read() == data


class TestChineseOnly:

    def test_chapter_filled_in_place(self, tmp_path, raw_project):
        _, z = _export(tmp_path, raw_project, 'chinese-only')
        body = _body(z, 'OEBPS/Text/one.xhtml')
        assert _inner(body) == (
            '<h1 id="c1">守燈人</h1><p class="first">米拉在黃昏爬上<em>螺旋</em>樓梯。</p>'
            '身體下的一行文字。<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<rect width="5" height="5"/></svg><div class="verse">歌的第一行<br/>歌的第二行<br/>'
            '歌的第三行</div>')

    def test_head_unchanged(self, tmp_path, raw_project):
        _, z = _export(tmp_path, raw_project, 'chinese-only')
        src = parse_xml(RAW_CHAPTER_ONE.encode())
        assert serialize(_root(z, 'OEBPS/Text/one.xhtml')[0]) == serialize(src[0])

    def test_language(self, tmp_path, raw_project):
        _, z = _export(tmp_path, raw_project, 'chinese-only')
        root = _root(z, 'OEBPS/Text/one.xhtml')
        assert root.get(XML_LANG) == 'zh-TW' and root.get('lang') == 'zh-TW'
        opf = z.read('OEBPS/content.opf').decode()
        assert '<dc:language>zh-TW</dc:language>' in opf and '>en<' not in opf

    def test_explicit_source_language_rewritten(self, tmp_path):
        chapter = xhtml('<p lang="en-GB">Plain <span xml:lang="EN">words</span> here.</p>'
                        '<p lang="fr">Bonjour le monde.</p>')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', chapter)}, nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {
            'c_seg0001': '<p>普通<span xml:lang="en">字</span>。</p>\n<p>你好。</p>'})
        _, z = _export(tmp_path, project_dir, 'chinese-only')
        assert _inner(_body(z, 'OEBPS/Text/c.xhtml')) == (
            '<p lang="zh-TW">普通<span xml:lang="zh-TW">字</span>。</p><p lang="fr">你好。</p>')

    def test_epub2_root_gets_only_xml_lang(self, tmp_path):
        epub = _raw_book(tmp_path, version='2.0')
        project_dir = _project(tmp_path, epub, RAW_TRANSLATIONS)
        _, z = _export(tmp_path, project_dir, 'chinese-only')
        root = _root(z, 'OEBPS/Text/one.xhtml')
        assert root.get(XML_LANG) == 'zh-TW' and root.get('lang') is None

    def test_existing_root_lang_set_in_epub2(self, tmp_path):
        chapter = RAW_CHAPTER_TWO.replace('<html ', '<html lang="en" ')
        epub = _raw_book(tmp_path, {'two': ('two.xhtml', chapter)}, nav=None, ncx=None,
                         version='2.0')
        project_dir = _project(tmp_path, epub, {'two_seg0001': '<h2>晚</h2>'})
        _, z = _export(tmp_path, project_dir, 'chinese-only')
        assert _root(z, 'OEBPS/Text/two.xhtml').get('lang') == 'zh-TW'

    def test_untranslated_chapter_and_package_kept(self, tmp_path):
        epub = _raw_book(tmp_path)
        project_dir = _project(tmp_path, epub, {})
        report, z = _export(tmp_path, project_dir, 'chinese-only')
        src = zipfile.ZipFile(epub)
        assert [z.read(n) for n in src.namelist()] == [src.read(n) for n in src.namelist()]
        assert report.chapters == [] and report.untranslated == 4

    def test_attributes_from_the_source_element(self, tmp_path, raw_project):
        _, z = _export(tmp_path, raw_project, 'chinese-only')
        p = _body(z, 'OEBPS/Text/one.xhtml')[1]
        assert dict(p.attrib) == {'class': 'first'}

    def test_xhtml_10_doctype_gets_no_meta(self, tmp_path):
        doctype = ('<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" '
                   '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">\n')
        chapter = RAW_CHAPTER_TWO.replace('\n<html', '\n' + doctype + '<!-- kept -->\n<html', 1)
        epub = _raw_book(tmp_path, {'two': ('two.xhtml', chapter)}, nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {'two_seg0001': '<h2>晚</h2>'})
        _, z = _export(tmp_path, project_dir, 'chinese-only')
        out = z.read('OEBPS/Text/two.xhtml').decode()
        assert '<meta' not in out
        assert out.startswith('<?xml version="1.0" encoding="utf-8"?>\n' + doctype
                              + '<!-- kept -->\n<html')


class TestBilingual:

    @pytest.mark.parametrize('fmt', BILINGUAL)
    def test_rows_per_block(self, tmp_path, raw_project, fmt):
        _, z = _export(tmp_path, raw_project, fmt)
        cls = {'bilingual-horizontal': 'bilingual-container',
               'bilingual-vertical': 'bilingual-vertical'}[fmt]
        body = _body(z, 'OEBPS/Text/one.xhtml')

        def row(original, translation, lang=' xml:lang="zh-TW" lang="zh-TW"'):
            return (f'<div class="{cls}"><div class="original">{original}</div>'
                    f'<div class="translation"{lang}>{translation}</div></div>')
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
               '<rect width="5" height="5"/></svg>')
        assert _inner(body) == ''.join([
            row('<h1 id="c1">The Lantern Keeper</h1>',
                '<h1 xml:lang="zh-TW" lang="zh-TW">守燈人</h1>', ''),
            row('<p class="first">Mira climbed the <em>spiral</em> stairs at dusk.</p>',
                '<p class="first" xml:lang="zh-TW" lang="zh-TW">米拉在黃昏爬上<em>螺旋</em>'
                '樓梯。</p>', ''),
            row('<!-- editor note: check spelling -->A line of bare text under the body.' + svg,
                '身體下的一行文字。' + svg),
            '<div class="verse">',
            row('First line of the song', '歌的第一行'), '<br/>',
            row('Second line of the song', '歌的第二行'), '<br/>',
            row('Third line of the song', '歌的第三行'),
            '</div>'])

    @pytest.mark.parametrize('fmt', BILINGUAL)
    def test_identity_ids_only_on_the_original(self, tmp_path, fmt):
        chapter = xhtml('<p id="p1" class="x">See <a id="r1" href="#n1">note</a>.</p>'
                        '<p id="n1"><a name="anchor"/>The note text.</p>')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', chapter)}, nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {
            'c_seg0001': '<p id="p1">見<a id="r1" href="#n1">註</a>。</p>\n'
                         '<p id="n1"><a name="anchor"/>註文。</p>'})
        _, z = _export(tmp_path, project_dir, fmt)
        root = _root(z, 'OEBPS/Text/c.xhtml')
        ids = [e.get('id') for e in root.iter() if e.get('id')]
        assert ids == ['p1', 'r1', 'n1']
        names = [e.get('name') for e in root.iter() if e.get('name')]
        assert names == ['anchor']
        copies = root.findall(f'.//{XHTML_NS}div[@class="translation"]')
        assert [c[0].get(XML_LANG) for c in copies] == ['zh-TW', 'zh-TW']
        assert copies[0][0].get('class') == 'x'
        assert copies[0][0][0].get('href') == '#n1'

    @pytest.mark.parametrize('fmt', BILINGUAL)
    def test_stylesheet_added_registered_and_linked(self, tmp_path, raw_project, fmt):
        _, z = _export(tmp_path, raw_project, fmt)
        assert z.read('OEBPS/bilingual.css').decode() == BILINGUAL_CSS
        opf = z.read('OEBPS/content.opf').decode()
        assert ('<item id="bilingual-css" href="bilingual.css" media-type="text/css"/>'
                '</manifest>') in opf
        assert '<dc:language>en</dc:language>' in opf
        for member in ('OEBPS/Text/one.xhtml', 'OEBPS/Text/chapter two.xhtml'):
            head = _root(z, member)[0]
            assert serialize(head[-1]) == ('<link rel="stylesheet" type="text/css" '
                                           'href="../bilingual.css"/>')
        src = parse_xml(RAW_CHAPTER_ONE.encode())
        head = _root(z, 'OEBPS/Text/one.xhtml')[0]
        assert [serialize(e) for e in head[:-1]] == [serialize(e) for e in src[0]]
        assert _root(z, 'OEBPS/Text/one.xhtml').get(XML_LANG) is None

    def test_stylesheet_name_and_id_do_not_collide(self, tmp_path):
        extra = {'OEBPS/bilingual.css': (('bilingual-css', 'bilingual.css', 'text/css', None),
                                         'p {}'),
                 'OEBPS/Bilingual-2.css': (('x', 'Bilingual-2.css', 'text/css', None), 'p {}'),
                 'OEBPS/other.css': (('bilingual-css-2', 'other.css', 'text/css', None), 'p {}')}
        epub = _raw_book(tmp_path, extra=extra)
        project_dir = _project(tmp_path, epub, RAW_TRANSLATIONS)
        _, z = _export(tmp_path, project_dir, 'bilingual-vertical')
        assert z.read('OEBPS/bilingual.css') == b'p {}'
        assert z.read('OEBPS/bilingual-3.css').decode() == BILINGUAL_CSS
        assert ('<item id="bilingual-css-3" href="bilingual-3.css" media-type="text/css"/>'
                in z.read('OEBPS/content.opf').decode())

    def test_stylesheet_href_relative_and_encoded(self, tmp_path):
        epub = write_raw_epub(
            tmp_path / 'b.epub',
            [('c', '../My%20Text/c.xhtml', 'application/xhtml+xml', None)], ['c'],
            {'My Text/c.xhtml': xhtml('<p>Words here.</p>')}, opf_path='OPS/content.opf')
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<p>字。</p>'})
        _, z = _export(tmp_path, project_dir, 'bilingual-horizontal')
        head = _root(z, 'My Text/c.xhtml')[0]
        assert head[-1].get('href') == '../OPS/bilingual.css'
        assert 'OPS/bilingual.css' in z.namelist()

    def test_head_created_when_missing(self, tmp_path):
        chapter = ('<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/'
                   'xhtml"><body><p>Words here.</p></body></html>')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', chapter)}, nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<p>字。</p>'})
        _, z = _export(tmp_path, project_dir, 'bilingual-vertical')
        root = _root(z, 'OEBPS/Text/c.xhtml')
        assert serialize(root[0]) == ('<head><link rel="stylesheet" type="text/css" '
                                      'href="../bilingual.css"/></head>')

    def test_no_stylesheet_when_nothing_translated(self, tmp_path):
        epub = _raw_book(tmp_path)
        project_dir = _project(tmp_path, epub, {})
        _, z = _export(tmp_path, project_dir, 'bilingual-horizontal')
        assert 'OEBPS/bilingual.css' not in z.namelist()
        src = zipfile.ZipFile(epub)
        assert z.read('OEBPS/content.opf') == src.read('OEBPS/content.opf')

    def test_epub2_copy_gets_only_xml_lang(self, tmp_path):
        epub = _raw_book(tmp_path, version='2.0')
        project_dir = _project(tmp_path, epub, RAW_TRANSLATIONS)
        _, z = _export(tmp_path, project_dir, 'bilingual-horizontal')
        copies = _root(z, 'OEBPS/Text/chapter two.xhtml').findall(
            f'.//{XHTML_NS}div[@class="translation"]')
        assert [(c[0].get(XML_LANG), c[0].get('lang')) for c in copies] == [('zh-TW', None)] * 2

    def test_copy_source_language_rewritten(self, tmp_path):
        chapter = xhtml('<p lang="en" class="k">Plain words.</p>')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', chapter)}, nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<p>字。</p>'})
        _, z = _export(tmp_path, project_dir, 'bilingual-horizontal')
        copy = _root(z, 'OEBPS/Text/c.xhtml').find(f'.//{XHTML_NS}div[@class="translation"]')[0]
        assert dict(copy.attrib) == {'lang': 'zh-TW', 'class': 'k', XML_LANG: 'zh-TW'}


class TestSvg:

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_viewbox_case_kept(self, tmp_path, fmt):
        chapter = xhtml('<div class="fig"><svg xmlns="http://www.w3.org/2000/svg" '
                        'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 4 4" '
                        'preserveAspectRatio="none"><image xlink:href="a.png"/></svg></div>'
                        '<p>Caption words.</p>')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', chapter)}, nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<p>圖說。</p>'})
        _, z = _export(tmp_path, project_dir, fmt)
        out = z.read('OEBPS/Text/c.xhtml').decode()
        assert 'viewBox="0 0 4 4" preserveAspectRatio="none"' in out
        assert 'viewbox' not in out and 'xlink:href="a.png"' in out


class TestReducedCheck:
    """Segments failing the reduced structural check stay in the original."""

    CHAPTER = xhtml('<p>Alpha words.</p><p>Beta words.</p>')

    def _kept(self, tmp_path, translation, chapter=None, fmt='chinese-only'):
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', chapter or self.CHAPTER)},
                         nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': translation})
        report, z = _export(tmp_path, project_dir, fmt)
        return report, z, epub

    @pytest.mark.parametrize('translation, reason', [
        ('<p>甲。</p>\n<p>乙。', NOT_WELL_FORMED),
        ('<p>甲&nbsp;。</p>\n<p>乙。</p>', NOT_WELL_FORMED),
        ('<p>甲。</p>', STRUCTURE_MISMATCH),                       # one block fewer
        ('<p>甲。</p><p>乙。</p><p>丙。</p>', STRUCTURE_MISMATCH),  # one more
        ('<p>甲。</p>\n<div>乙。</div>', STRUCTURE_MISMATCH),       # other tag
        ('<p>甲。</p>\n乙。', STRUCTURE_MISMATCH),                  # run for an element
        ('<p>甲。</p>\n<p>乙。</p>\n多出的字', STRUCTURE_MISMATCH),  # text outside
        ('<p>甲。</p><br/><p>乙。</p>', STRUCTURE_MISMATCH),        # top-level <br>
        ('甲。乙。', STRUCTURE_MISMATCH),
    ])
    @pytest.mark.parametrize('fmt', FORMATS)
    def test_kept_original_and_reported(self, tmp_path, translation, reason, fmt):
        report, z, epub = self._kept(tmp_path, translation, fmt=fmt)
        assert [(c.chapter_id, c.translated, c.kept) for c in report.chapters] == [
            ('c', 0, [('c_seg0001', reason)])]
        src = zipfile.ZipFile(epub)
        assert z.read('OEBPS/Text/c.xhtml') == src.read('OEBPS/Text/c.xhtml')
        assert z.read('OEBPS/content.opf') == src.read('OEBPS/content.opf')

    def test_whitespace_and_comments_between_blocks_accepted(self, tmp_path):
        report, z, _ = self._kept(tmp_path, '\n <!-- x --> <p>甲。</p>\n\n<?pi x?><p>乙。</p>\n')
        assert report.translated == 1
        assert _inner(_body(z, 'OEBPS/Text/c.xhtml')) == '<p>甲。</p><p>乙。</p>'

    def test_run_wrapped_in_a_paragraph_kept(self, tmp_path):
        chapter = xhtml('<div class="v">Line one<br/>Line two</div>')
        report, _, _ = self._kept(tmp_path, '<p>一</p>\n<p>二</p>', chapter)
        assert report.reasons() == {STRUCTURE_MISMATCH: 1}

    @pytest.mark.parametrize('translation, expected', [
        ('第一行\n第二行', '第一行<br/>第二行'),
        ('  第一行 \n\n 第二<em>行</em>\n', ' 第一行 <br/> 第二<em>行</em>'),
    ])
    def test_br_runs_split_at_newlines(self, tmp_path, translation, expected):
        chapter = xhtml('<div class="v">Line one<br/>Line <em>two</em></div>')
        report, z, _ = self._kept(tmp_path, translation, chapter)
        assert report.translated == 1
        div = _body(z, 'OEBPS/Text/c.xhtml')[0]
        assert div.get('class') == 'v'
        assert ''.join(_inner(div).split('\n')).strip() == expected.strip()

    @pytest.mark.parametrize('translation', ['第一行第二行', '一\n二\n三', '一\n<em>二</em>\n三',
                                             '一\n\n   \n'])
    def test_br_runs_need_one_line_each(self, tmp_path, translation):
        chapter = xhtml('<div class="v">Line one<br/>Line <em>two</em></div>')
        report, _, _ = self._kept(tmp_path, translation, chapter)
        assert report.reasons() == {STRUCTURE_MISMATCH: 1}

    def test_newline_inside_an_inline_element_does_not_split(self, tmp_path):
        chapter = xhtml('<div class="v">Line one<br/>Line two</div>')
        report, z, _ = self._kept(tmp_path, '第一<em>行\n還是</em>第一\n第二行', chapter)
        assert report.translated == 1
        assert _inner(_body(z, 'OEBPS/Text/c.xhtml')[0]) == '第一<em>行\n還是</em>第一<br/>第二行'

    def test_single_run_keeps_its_newlines(self, tmp_path):
        chapter = xhtml('<p>Lead.</p>Bare\ntext here.<p>End.</p>')
        report, z, _ = self._kept(tmp_path, '<p>引。</p>\n裸\n文。\n<p>完。</p>', chapter)
        assert report.translated == 1
        assert _inner(_body(z, 'OEBPS/Text/c.xhtml')) == '<p>引。</p>\n裸\n文。\n<p>完。</p>'

    def test_ambiguous_source_kept(self, tmp_path):
        # two runs, the first with a newline of its own: the stored HTML does
        # not split into its blocks, whatever the translation
        chapter = xhtml('<div class="v">Line\none<br/>Line two</div>')
        report, _, _ = self._kept(tmp_path, '第一行\n第二行', chapter)
        assert report.reasons() == {AMBIGUOUS_SOURCE: 1}

    def test_runs_and_elements_mixed(self, tmp_path):
        chapter = xhtml('<div><p>Head words.</p>First run<br/>Second run<p>Tail words.</p>'
                        'Last run</div>')
        report, z, _ = self._kept(tmp_path, '<p>首。</p>\n一\n二\n<p>尾。</p>\n末', chapter)
        assert report.translated == 1
        # the newlines that split the runs are not kept; a single run keeps its text
        assert _inner(_body(z, 'OEBPS/Text/c.xhtml')[0]) == (
            '<p>首。</p>一<br/>二<p>尾。</p>\n末')

    def test_inline_attributes_come_from_the_translation(self, tmp_path):
        # only block elements keep their source attributes until Phase 4
        chapter = xhtml('<p class="s">A <a href="x.xhtml" class="k">link</a> here.</p>')
        report, z, _ = self._kept(tmp_path, '<p class="t">一個<a href="x.xhtml">連結</a>。</p>',
                                  chapter)
        assert _inner(_body(z, 'OEBPS/Text/c.xhtml')) == (
            '<p class="s">一個<a href="x.xhtml">連結</a>。</p>')

    def test_epub_prefix_in_translation_parses(self, tmp_path):
        chapter = xhtml('<p>See <span epub:type="noteref">1</span> here.</p>')
        report, z, _ = self._kept(
            tmp_path, '<p>見<span epub:type="noteref">1</span>。</p>', chapter)
        assert report.translated == 1
        assert 'epub:type="noteref"' in z.read('OEBPS/Text/c.xhtml').decode()

    def test_one_failing_segment_does_not_stop_the_chapter(self, tmp_path):
        chapter = xhtml('<h1>Title words</h1><p>Body words.</p>')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', chapter)}, nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<h1>標題',
                                                'c_seg0002': '<p>內文。</p>'})
        report, z = _export(tmp_path, project_dir, 'chinese-only')
        assert [(c.translated, c.kept) for c in report.chapters] == [
            (1, [('c_seg0001', NOT_WELL_FORMED)])]
        assert _inner(_body(z, 'OEBPS/Text/c.xhtml')) == '<h1>Title words</h1><p>內文。</p>'


class TestChapterVerification:

    def _project(self, tmp_path, change):
        epub = _raw_book(tmp_path)
        project_dir = _project(tmp_path, epub, RAW_TRANSLATIONS)
        path = os.path.join(project_dir, 'project.json')
        raw = json.load(open(path, encoding='utf-8'))
        change(raw['segments'])
        json.dump(raw, open(path, 'w', encoding='utf-8'))
        return project_dir, epub

    @pytest.mark.parametrize('change', [
        lambda s: s[1]['fingerprints'].__setitem__(0, '0' * 16),
        lambda s: s[1].__setitem__('block_end', 5),
        lambda s: s[1].__setitem__('block_end', 7),
        lambda s: s[0].__setitem__('block_start', None),
        lambda s: s[0].__setitem__('block_start', True),
        lambda s: s[1].__setitem__('block_start', 0),
        lambda s: s.pop(0),
    ])
    @pytest.mark.parametrize('fmt', FORMATS)
    def test_mismatched_chapter_kept_and_reported(self, tmp_path, change, fmt):
        project_dir, epub = self._project(tmp_path, change)
        report, z = _export(tmp_path, project_dir, fmt)
        one = next(c for c in report.chapters if c.chapter_id == 'one')
        assert one.translated == 0
        assert {r for _, r in one.kept} == {CHAPTER_MISMATCH}
        src = zipfile.ZipFile(epub)
        assert z.read('OEBPS/Text/one.xhtml') == src.read('OEBPS/Text/one.xhtml')
        two = next(c for c in report.chapters if c.chapter_id == 'two')
        assert (two.translated, two.kept) == (2, [])  # one bad chapter never fails the export

    def test_chapter_not_in_the_spine(self, tmp_path):
        def change(segments):
            for s in segments[:2]:
                s['chapter_id'] = 'gone'
        project_dir, _ = self._project(tmp_path, change)
        report, _ = _export(tmp_path, project_dir, 'chinese-only')
        assert [(c.chapter_id, c.translated, c.kept) for c in report.chapters] == [
            ('gone', 0, [('one_seg0001', CHAPTER_UNREADABLE), ('one_seg0002', CHAPTER_UNREADABLE)]),
            ('two', 2, [])]

    @pytest.mark.parametrize('content', [b'<html><body><p>broken', b'<html/>',
                                         b'<!DOCTYPE html [<!ENTITY e "x">]><html xmlns='
                                         b'"http://www.w3.org/1999/xhtml"><body><p>&e;</p>'
                                         b'</body></html>'])
    def test_unreadable_chapter_kept(self, tmp_path, content):
        epub = _raw_book(tmp_path)
        project_dir = _project(tmp_path, epub, RAW_TRANSLATIONS)
        damaged = str(tmp_path / 'damaged.epub')
        with zipfile.ZipFile(epub) as src, zipfile.ZipFile(damaged, 'w') as dst:
            for info in src.infolist():
                data = content if info.filename == 'OEBPS/Text/one.xhtml' else src.read(info)
                dst.writestr(info, data)
        os.replace(damaged, epub)
        pm = ProjectManager(project_dir)
        pm.load()
        pm.data.source_sha256 = sha256_file(epub)
        pm.save()
        report, z = _export(tmp_path, project_dir, 'chinese-only')
        assert report.chapters[0].kept == [('one_seg0001', CHAPTER_UNREADABLE),
                                           ('one_seg0002', CHAPTER_UNREADABLE)]
        assert z.read('OEBPS/Text/one.xhtml') == content
        assert report.chapters[1].translated == 2


class TestCode:

    def _code(self, tmp_path, chapter, translation, fmt='chinese-only'):
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', xhtml(chapter))}, nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': translation})
        report, z = _export(tmp_path, project_dir, fmt)
        return report, _body(z, 'OEBPS/Text/c.xhtml'), z, epub

    @pytest.mark.parametrize('chapter, expected', [
        ('<pre class="c"><code>x = 1  # one\n</code></pre>',
         '<pre class="c"><code>x = 1  # 一 &lt;&amp;&gt;\n</code></pre>'),
        ('<pre> <code>x = 1  # one\n</code> </pre>',
         '<pre> <code>x = 1  # 一 &lt;&amp;&gt;\n</code> </pre>'),
        ('<pre>x = 1  # one\n</pre>', '<pre>x = 1  # 一 &lt;&amp;&gt;\n</pre>'),
        ('<code>x = 1  # one\n</code>', '<code>x = 1  # 一 &lt;&amp;&gt;\n</code>'),
        (' <code>x = 1  # one\n</code> ', '<code>x = 1  # 一 &lt;&amp;&gt;\n</code>'),
    ])
    def test_code_text_filled(self, tmp_path, chapter, expected):
        report, body, _, _ = self._code(tmp_path, chapter, 'x = 1  # 一 <&>\n')
        assert report.translated == 1
        assert _inner(body) == expected

    @pytest.mark.parametrize('fmt', BILINGUAL)
    def test_code_bilingual_copy(self, tmp_path, fmt):
        report, body, _, _ = self._code(tmp_path, '<pre id="k"><code>x = 1  # one</code></pre>',
                                        'x = 1  # 一', fmt)
        copy = body.find(f'.//{XHTML_NS}div[@class="translation"]')[0]
        assert serialize(copy) == ('<pre xml:lang="zh-TW" lang="zh-TW"><code>x = 1  # 一'
                                   '</code></pre>')

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_identical_code_left_alone_once(self, tmp_path, fmt):
        report, _, z, epub = self._code(tmp_path, '<pre><code>x = 1</code></pre>', 'x = 1', fmt)
        assert report.translated == 1 and report.reasons() == {}
        src = zipfile.ZipFile(epub)
        assert z.read('OEBPS/Text/c.xhtml') == src.read('OEBPS/Text/c.xhtml')

    @pytest.mark.parametrize('chapter', [
        '<pre><code><span class="k">def</span> f():  # one</code></pre>',
        '<pre>x = 1<code>y</code></pre>',
        '<pre><code>a</code><code>b</code></pre>',
        '<pre>lead <code>x = 1  # one</code></pre>',
        '<pre><code>x = 1  # one</code> trail</pre>',
    ])
    def test_code_with_markup_kept(self, tmp_path, chapter):
        report, _, z, epub = self._code(tmp_path, chapter, 'def f():  # 一')
        assert report.reasons() == {CODE_MARKUP: 1}
        src = zipfile.ZipFile(epub)
        assert z.read('OEBPS/Text/c.xhtml') == src.read('OEBPS/Text/c.xhtml')

    def test_code_segment_on_a_text_block_kept(self, tmp_path):
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', xhtml('<pre>x = 1</pre>'))},
                         nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': 'y = 2'})
        path = os.path.join(project_dir, 'project.json')
        raw = json.load(open(path, encoding='utf-8'))
        raw['segments'][0]['segment_type'] = 'text'
        json.dump(raw, open(path, 'w', encoding='utf-8'))
        report, _ = _export(tmp_path, project_dir, 'chinese-only')
        assert report.reasons() == {STRUCTURE_MISMATCH: 1}
        assert exporter._code_piece is not None

    def test_code_type_on_a_paragraph_kept(self, tmp_path):
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', xhtml('<p>Words here.</p>'))},
                         nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<p>字。</p>'})
        path = os.path.join(project_dir, 'project.json')
        raw = json.load(open(path, encoding='utf-8'))
        raw['segments'][0]['segment_type'] = 'code'
        json.dump(raw, open(path, 'w', encoding='utf-8'))
        report, _ = _export(tmp_path, project_dir, 'chinese-only')
        assert report.reasons() == {STRUCTURE_MISMATCH: 1}


class TestToc:

    NAV = xhtml('<nav epub:type="toc"><ol>'
                '<li><a href="Text/one.xhtml">One</a></li>'
                '<li><a href="Text/one.xhtml#s2"><span>Two</span></a></li>'
                '<li><a href="Text/one.xhtml#plain">Three</a></li>'
                '<li><a href="Text/one.xhtml#nowhere">Four</a></li>'
                '<li><a href="Text/one.xhtml#box">Five</a></li>'
                '<li><a href="http://example.invalid/x#s2">Six</a></li>'
                '<li><a href="Text/two.xhtml">Seven</a></li>'
                '<li><a>Eight</a></li>'
                '</ol></nav><p><a href="Text/one.xhtml">Outside a nav</a></p>',
                head='<title>Contents</title>')
    NCX = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>'
           '<navPoint id="a"><navLabel><text>One</text></navLabel>'
           '<content src="Text/one.xhtml"/>'
           '<navPoint id="b"><navLabel><text>Two</text></navLabel>'
           '<content src="Text/one.xhtml#s2"/></navPoint></navPoint>'
           '<navPoint id="c"><navLabel><text>Three</text></navLabel>'
           '<content src="Text/one.xhtml#plain"/></navPoint>'
           '<navPoint id="d"><navLabel><text>Seven</text></navLabel>'
           '<content src="Text/two.xhtml"/></navPoint>'
           '<navPoint id="e"><navLabel/><content src="Text/one.xhtml"/></navPoint>'
           '</navMap><pageList><pageTarget id="p1" type="normal" value="1">'
           '<navLabel><text>1</text></navLabel><content src="Text/one.xhtml#s2"/>'
           '</pageTarget></pageList></ncx>')
    ONE = xhtml('<h1>First Part</h1><p id="plain">Plain words.</p>'
                '<div class="h" id="box"><h2 id="s2"><a id="inner"/>Second <em>Part</em></h2></div>'
                '<p>More words.</p><header><h3>Third Part</h3></header>')
    TWO = xhtml('<h2>Other File</h2>')
    ONE_TRANSLATED = {'one_seg0001': '<h1>第一部</h1>',
                      'one_seg0002': '<p>普通的字。</p>',
                      'one_seg0003': '<h2><a id="inner"/>第二<em>部</em></h2>',
                      'one_seg0004': '<p>更多。</p>\n<header><h3>第三部</h3></header>'}

    def _toc(self, tmp_path, translations, fmt='chinese-only'):
        epub = _raw_book(tmp_path, {'one': ('one.xhtml', self.ONE),
                                    'two': ('two.xhtml', self.TWO)},
                         nav=self.NAV, ncx=self.NCX)
        project_dir = _project(tmp_path, epub, translations)
        _, z = _export(tmp_path, project_dir, fmt)
        nav = [_inner(a) for a in _root(z, 'OEBPS/nav.xhtml').iter(f'{XHTML_NS}a')]
        ncx = [''.join(t.itertext()) for t in _root(z, 'OEBPS/toc.ncx').iter(
            '{http://www.daisy.org/z3986/2005/ncx/}text')]
        return nav, ncx, z, epub

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_labels_from_translated_headings(self, tmp_path, fmt):
        nav, ncx, _, _ = self._toc(tmp_path, self.ONE_TRANSLATED, fmt)
        # One: first heading; Two: target is the heading; Three: a <p> block
        # has no heading; Four: no such id; Five: the target contains blocks,
        # the first a filled heading; Six: external; Seven: chapter not
        # translated; Eight: no href
        assert nav == ['第一部', '第二部', 'Three', 'Four', '第二部', 'Six', 'Seven', 'Eight',
                       'Outside a nav']
        assert ncx == ['第一部', '第二部', 'Three', 'Seven', '第二部']

    def test_target_inside_a_heading_and_header_block(self, tmp_path):
        # the entry after one whose <span> is replaced is still relabelled
        nav = self.NAV.replace('#s2', '#inner').replace(
            '<a href="Text/one.xhtml#nowhere">', '<a href="Text/one.xhtml#h3">')
        one = self.ONE.replace('<h3>', '<h3 id="h3">')
        epub = _raw_book(tmp_path, {'one': ('one.xhtml', one), 'two': ('two.xhtml', self.TWO)},
                         nav=nav, ncx=None)
        translations = dict(self.ONE_TRANSLATED,
                            one_seg0004='<p>更多。</p>\n<header><h3 id="h3">第三部</h3></header>')
        project_dir = _project(tmp_path, epub, translations)
        _, z = _export(tmp_path, project_dir, 'chinese-only')
        labels = [_inner(a) for a in _root(z, 'OEBPS/nav.xhtml').iter(f'{XHTML_NS}a')]
        assert labels[1] == '第二部' and labels[3] == '第三部'

    def test_label_unchanged_when_the_heading_segment_fails(self, tmp_path):
        translations = dict(self.ONE_TRANSLATED, one_seg0001='<h1>第一部', one_seg0003='第二部')
        nav, ncx, _, _ = self._toc(tmp_path, translations)
        assert nav[:2] == ['One', '<span>Two</span>'] and ncx[:2] == ['One', 'Two']

    def test_label_unchanged_for_an_empty_heading(self, tmp_path):
        translations = dict(self.ONE_TRANSLATED, one_seg0001='<h1> <em/> </h1>')
        nav, _, _, _ = self._toc(tmp_path, translations)
        assert nav[0] == 'One'

    def test_label_from_a_heading_whitespace_normalized(self, tmp_path):
        translations = dict(self.ONE_TRANSLATED, one_seg0001='<h1>\n 第一 \n 部 </h1>')
        nav, _, _, _ = self._toc(tmp_path, translations)
        assert nav[0] == '第一 部'

    def test_toc_files_kept_when_no_label_changes(self, tmp_path):
        nav, ncx, z, epub = self._toc(tmp_path, {'one_seg0002': '<p>普通的字。</p>'})
        src = zipfile.ZipFile(epub)
        assert z.read('OEBPS/nav.xhtml') == src.read('OEBPS/nav.xhtml')
        assert z.read('OEBPS/toc.ncx') == src.read('OEBPS/toc.ncx')

    def test_unreadable_toc_kept(self, tmp_path):
        epub = _raw_book(tmp_path, {'one': ('one.xhtml', self.ONE)},
                         nav='<html><body><nav>', ncx='<ncx>')
        project_dir = _project(tmp_path, epub, self.ONE_TRANSLATED)
        _, z = _export(tmp_path, project_dir, 'chinese-only')
        assert z.read('OEBPS/nav.xhtml') == b'<html><body><nav>'
        assert z.read('OEBPS/toc.ncx') == b'<ncx>'

    def test_raw_fixture_labels(self, tmp_path, raw_project):
        _, z = _export(tmp_path, raw_project, 'bilingual-vertical')
        assert b'<a href="Text/one.xhtml">\xe5\xae\x88\xe7\x87\x88\xe4\xba\xba</a>' in z.read(
            'OEBPS/nav.xhtml')
        assert '<text>守燈人</text>' in z.read('OEBPS/toc.ncx').decode()

    def test_relative_to_the_toc_file(self, tmp_path):
        nav = xhtml('<nav><ol><li><a href="../Text/c.xhtml">C</a></li></ol></nav>')
        epub = write_raw_epub(
            tmp_path / 'b.epub',
            [('nav', 'Nav/nav.xhtml', 'application/xhtml+xml', 'nav'),
             ('c', 'Text/c.xhtml', 'application/xhtml+xml', None)], ['c'],
            {'OEBPS/Nav/nav.xhtml': nav, 'OEBPS/Text/c.xhtml': xhtml('<h1>Cee</h1>')})
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<h1>西</h1>'})
        _, z = _export(tmp_path, project_dir, 'chinese-only')
        assert '<a href="../Text/c.xhtml">西</a>' in z.read('OEBPS/Nav/nav.xhtml').decode()

    def test_label_for_helper(self):
        labels = {'O/T/a b.xhtml': {None: 'A', 'x y': 'X'}}
        assert exporter._label_for('O/nav.xhtml', 'T/a%20b.xhtml#x%20y', labels) == 'X'
        assert exporter._label_for('O/nav.xhtml', 'T/a%20b.xhtml', labels) == 'A'
        assert exporter._label_for('O/T/a b.xhtml', '#x%20y', labels) == 'X'
        assert exporter._label_for('O/nav.xhtml', '', labels) is None
        assert exporter._label_for('O/nav.xhtml', 'file:T/a%20b.xhtml', labels) is None
        assert exporter._label_for('O/nav.xhtml', '//h/T/a%20b.xhtml', labels) is None


class TestProjectLevel:

    def test_schema_1_message(self, tmp_path):
        from tests.test_migrate import _legacy_project
        project_dir = _legacy_project(tmp_path)
        with pytest.raises(ExportError) as e:
            export_project(project_dir, str(tmp_path / 'o.epub'), 'chinese-only')
        assert str(e.value) == SCHEMA_1_MESSAGE
        assert 'python3 translate.py migrate' in SCHEMA_1_MESSAGE
        assert not os.path.exists(tmp_path / 'o.epub')

    def test_migrated_legacy_project_exports(self, tmp_path):
        from tests.test_migrate import _legacy_project
        project_dir = _legacy_project(tmp_path)
        migrate_project(project_dir)
        report, z = _export(tmp_path, project_dir, 'chinese-only')
        assert report.summary() == {'translated': 3, 'kept': 0, 'untranslated': 0,
                                    'reasons': {}}
        assert '網晾乾了' in z.read('OEBPS/Text/c1.xhtml').decode()

    @pytest.mark.parametrize('change', ['modified', 'missing', 'no hash'])
    def test_source_hash_verified(self, tmp_path, raw_project, change):
        pm = ProjectManager(raw_project)
        pm.load()
        if change == 'modified':
            with open(pm.data.epub_path, 'ab') as f:
                f.write(b'\0')
        elif change == 'missing':
            os.remove(pm.data.epub_path)
        else:
            pm.data.source_sha256 = ''
            pm.save()
        with pytest.raises(ExportError) as e:
            export_project(raw_project, str(tmp_path / 'o.epub'), 'chinese-only')
        assert ('雜湊' in str(e.value)) if change != 'missing' else ('無法讀取' in str(e.value))
        assert not os.path.exists(tmp_path / 'o.epub')

    def test_unreadable_package_is_an_export_error(self, tmp_path, raw_project):
        pm = ProjectManager(raw_project)
        pm.load()
        with open(pm.data.epub_path, 'wb') as f:
            f.write(b'not a zip')
        pm.data.source_sha256 = sha256_file(pm.data.epub_path)
        pm.save()
        with pytest.raises(ExportError, match='無法讀取 EPUB'):
            export_project(raw_project, str(tmp_path / 'o.epub'), 'chinese-only')

    def test_invalid_format(self, tmp_path, raw_project):
        with pytest.raises(ValueError, match='Invalid format'):
            export_project(raw_project, str(tmp_path / 'o.epub'), 'invalid')

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_failed_and_pending_segments_exported_in_the_original(self, tmp_path, fmt):
        epub = _raw_book(tmp_path)
        statuses = {'two_seg0001': SegmentStatus.FAILED, 'one_seg0001': SegmentStatus.REVIEWED}
        translations = dict(RAW_TRANSLATIONS, two_seg0002='   ')
        project_dir = _project(tmp_path, epub, translations, statuses)
        report, z = _export(tmp_path, project_dir, fmt)
        assert report.untranslated == 2
        assert [(c.chapter_id, c.translated, c.kept) for c in report.chapters] == [
            ('one', 2, [])]
        src = zipfile.ZipFile(epub)
        assert (z.read('OEBPS/Text/chapter two.xhtml')
                == src.read('OEBPS/Text/chapter two.xhtml'))
        assert '守燈人' in z.read('OEBPS/Text/one.xhtml').decode()

    def test_report_lines(self, tmp_path):
        epub = _raw_book(tmp_path)
        translations = dict(RAW_TRANSLATIONS, two_seg0001='<h2>晚', two_seg0002='字')
        project_dir = _project(tmp_path, epub, translations)
        report, _ = _export(tmp_path, project_dir, 'chinese-only')
        assert report.summary() == {
            'translated': 2, 'kept': 2, 'untranslated': 0,
            'reasons': {NOT_WELL_FORMED: 1, STRUCTURE_MISMATCH: 1}}
        assert report.lines() == [
            '以譯文匯出 2 段，保留原文 2 段（另有 0 段未翻譯或翻譯失敗，以原文匯出）',
            '  譯文不是格式正確的 XML（not_well_formed）：1',
            '  譯文的頂層結構與原文區塊不符（structure_mismatch）：1',
            '  章節 two：譯文 0，原文 2（not_well_formed, structure_mismatch）',
        ]

    def test_reason_texts_cover_every_reason(self):
        assert set(exporter.REASON_TEXT) == {
            CHAPTER_UNREADABLE, CHAPTER_MISMATCH, NOT_WELL_FORMED, STRUCTURE_MISMATCH,
            AMBIGUOUS_SOURCE, CODE_MARKUP, MISSING_ANCHOR}

    def test_no_duplicate_ids_in_raw_fixture(self, tmp_path, raw_project):
        for fmt in FORMATS:
            _, z = _export(tmp_path, raw_project, fmt)
            for member in ('OEBPS/Text/one.xhtml', 'OEBPS/Text/chapter two.xhtml'):
                ids = [e.get('id') for e in _root(z, member).iter() if e.get('id')]
                assert len(ids) == len(set(ids))

    def test_every_output_chapter_well_formed(self, tmp_path, raw_project):
        for fmt in FORMATS:
            _, z = _export(tmp_path, raw_project, fmt)
            for name in z.namelist():
                if name.endswith(('.xhtml', '.opf', '.ncx')):
                    etree.fromstring(z.read(name))


class TestCLIAndWeb:

    def test_cli_prints_the_summary(self, tmp_path, raw_project):
        from translate import cli
        result = CliRunner().invoke(cli, ['export', '-p', raw_project, '-f', 'chinese-only'])
        assert result.exit_code == 0, result.output
        out = os.path.join(raw_project, 'output', 'proj-chinese-only.epub')
        assert result.output.splitlines() == [
            '正在建構 chinese-only 格式的 EPUB...', f'已輸出：{out}',
            '以譯文匯出 4 段，保留原文 0 段（另有 0 段未翻譯或翻譯失敗，以原文匯出）']
        assert zipfile.ZipFile(out).namelist()[0] == 'mimetype'

    def test_cli_export_error(self, tmp_path, raw_project):
        from translate import cli
        os.remove(ProjectManager(raw_project).load().epub_path)
        result = CliRunner().invoke(cli, ['export', '-p', raw_project, '-f', 'chinese-only'])
        assert result.exit_code == 1
        assert result.output.splitlines()[-1].startswith('無法匯出：無法讀取來源 EPUB')

    def _web_export(self, project_dir, fmt='bilingual-horizontal'):
        import time
        from src.web.blueprints import export as export_bp
        from src.web.server import create_app
        client = create_app(projects_dir=os.path.dirname(project_dir)).test_client()
        name = os.path.basename(project_dir)
        export_bp._export_tasks.pop(name, None)
        assert client.post(f'/api/projects/{name}/export',
                           json={'format': fmt}).status_code == 200
        for _ in range(200):
            status = client.get(f'/api/projects/{name}/export/status').get_json()
            if status['state'] != 'running':
                return status
            time.sleep(0.05)
        raise AssertionError('export did not finish')

    def test_web_status_has_the_summary(self, tmp_path, raw_project):
        status = self._web_export(raw_project)
        assert status == {'state': 'completed', 'format': 'bilingual-horizontal',
                          'filename': 'proj_bilingual-horizontal.epub', 'error': None,
                          'summary': {'translated': 4, 'kept': 0, 'untranslated': 0,
                                      'reasons': {}}}

    def test_web_schema_1_message(self, tmp_path):
        from tests.test_migrate import _legacy_project
        project_dir = _legacy_project(tmp_path)
        status = self._web_export(project_dir)
        assert status['state'] == 'failed' and status['error'] == SCHEMA_1_MESSAGE
        assert status['summary'] is None

    def test_web_rejects_unknown_format(self, tmp_path, raw_project):
        from src.web.server import create_app
        client = create_app(projects_dir=os.path.dirname(raw_project)).test_client()
        response = client.post('/api/projects/proj/export', json={'format': 'x'})
        assert response.status_code == 400
        assert response.get_json()['error'] == (
            '無效的格式。可選: chinese-only, bilingual-horizontal, bilingual-vertical')

    def test_export_page_shows_the_summary(self, tmp_path, raw_project):
        from src.web.server import create_app
        client = create_app(projects_dir=os.path.dirname(raw_project)).test_client()
        page = client.get('/project/proj/export').get_data(as_text=True)
        assert 'exportSummary.translated' in page and 'exportSummary = data.summary' in page


class TestSurvivors:
    """Mutation testing, run 1: rules spelled out."""

    def test_report_codes_texts_and_messages(self, tmp_path, raw_project):
        assert (CHAPTER_UNREADABLE, CHAPTER_MISMATCH, NOT_WELL_FORMED, STRUCTURE_MISMATCH,
                AMBIGUOUS_SOURCE, CODE_MARKUP, MISSING_ANCHOR) == (
            'chapter_unreadable', 'chapter_mismatch', 'not_well_formed', 'structure_mismatch',
            'ambiguous_source', 'code_markup', 'missing_anchor')
        assert exporter.REASON_TEXT == {
            'chapter_unreadable': '章節無法讀取',
            'chapter_mismatch': '章節內容與分段紀錄不符',
            'not_well_formed': '譯文不是格式正確的 XML',
            'structure_mismatch': '譯文的頂層結構與原文區塊不符',
            'ambiguous_source': '原文區塊無法明確對應',
            'code_markup': '程式碼區塊含標記',
            'missing_anchor': '譯文缺少原文區塊內的錨點（id 或 a 的 name）',
        }
        assert SCHEMA_1_MESSAGE == ('此專案是舊格式（schema 1），無法匯出。'
                                    '請先執行 python3 translate.py migrate <專案目錄> 再匯出。')
        with pytest.raises(ValueError) as e:
            export_project(raw_project, str(tmp_path / 'o.epub'), 'x')
        assert str(e.value) == ("Invalid format: x. Must be one of ('chinese-only', "
                                "'bilingual-horizontal', 'bilingual-vertical')")
        with open(ProjectManager(raw_project).load().epub_path, 'ab') as f:
            f.write(b'\0')
        with pytest.raises(ExportError) as e:
            export_project(raw_project, str(tmp_path / 'o.epub'), 'chinese-only')
        assert str(e.value) == '來源 EPUB 與專案記錄的雜湊值不符，無法匯出'

    @pytest.mark.parametrize('tag', ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
    def test_every_heading_level_labels(self, tmp_path, tag):
        nav = xhtml('<nav><ol><li><a href="Text/c.xhtml">C</a></li></ol></nav>')
        chapter = xhtml(f'<p>Lead words.</p><{tag}>Heading words</{tag}>')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', chapter)}, nav=nav, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<p>引。</p>',
                                                'c_seg0002': f'<{tag}>標題</{tag}>'})
        _, z = _export(tmp_path, project_dir, 'chinese-only')
        assert '<a href="Text/c.xhtml">標題</a>' in z.read('OEBPS/nav.xhtml').decode()

    def test_source_language_with_region(self, tmp_path):
        chapter = xhtml('<p lang="en" class="k">Plain words.</p>')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', chapter)}, nav=None, ncx=None)
        with zipfile.ZipFile(epub) as src:
            members = [(i, src.read(i)) for i in src.infolist()]
        with zipfile.ZipFile(epub, 'w') as dst:
            for info, data in members:
                if info.filename == 'OEBPS/content.opf':
                    data = data.replace(b'<dc:language>en</dc:language>',
                                        b'<dc:language>en-US</dc:language>'
                                        b'<dc:language>fr</dc:language>')
                dst.writestr(info, data)
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<p>字。</p>'})
        _, z = _export(tmp_path, project_dir, 'chinese-only')
        assert _body(z, 'OEBPS/Text/c.xhtml')[0].get('lang') == 'zh-TW'
        opf = z.read('OEBPS/content.opf').decode()
        assert '<dc:language>zh-TW</dc:language><dc:language>fr</dc:language>' in opf

    @pytest.mark.parametrize('chapter_id', ['nav', 'css'])
    def test_segments_of_a_navigation_or_non_xhtml_item(self, tmp_path, chapter_id):
        epub = _raw_book(tmp_path)
        project_dir = _project(tmp_path, epub, RAW_TRANSLATIONS)
        path = os.path.join(project_dir, 'project.json')
        raw = json.load(open(path, encoding='utf-8'))
        for s in raw['segments'][:2]:
            s['chapter_id'] = chapter_id
        json.dump(raw, open(path, 'w', encoding='utf-8'))
        report, _ = _export(tmp_path, project_dir, 'chinese-only')
        assert report.chapters[0].kept == [('one_seg0001', CHAPTER_UNREADABLE),
                                           ('one_seg0002', CHAPTER_UNREADABLE)]

    def test_chapter_without_translation_before_a_translated_one(self, tmp_path):
        epub = _raw_book(tmp_path)
        project_dir = _project(tmp_path, epub, {k: v for k, v in RAW_TRANSLATIONS.items()
                                                if k.startswith('two')})
        report, z = _export(tmp_path, project_dir, 'chinese-only')
        assert [(c.chapter_id, c.translated) for c in report.chapters] == [('two', 2)]
        assert '第二晚' in z.read('OEBPS/Text/chapter two.xhtml').decode()

    def test_package_and_chapter_at_the_archive_root(self, tmp_path):
        epub = write_raw_epub(tmp_path / 'b.epub',
                              [('c', 'c.xhtml', 'application/xhtml+xml', None)], ['c'],
                              {'c.xhtml': xhtml('<p>Words here.</p>')}, opf_path='content.opf')
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<p>字。</p>'})
        _, z = _export(tmp_path, project_dir, 'bilingual-horizontal')
        assert _root(z, 'c.xhtml')[0][-1].get('href') == 'bilingual.css'
        assert 'href="bilingual.css" media-type="text/css"' in z.read('content.opf').decode()
        assert z.read('bilingual.css').decode() == BILINGUAL_CSS

    def test_first_free_names(self, tmp_path):
        extra = {'OEBPS/bilingual.css': (('bilingual-css', 'bilingual.css', 'text/css', None),
                                         'p {}')}
        epub = _raw_book(tmp_path, extra=extra)
        project_dir = _project(tmp_path, epub, RAW_TRANSLATIONS)
        _, z = _export(tmp_path, project_dir, 'bilingual-vertical')
        assert z.read('OEBPS/bilingual-2.css').decode() == BILINGUAL_CSS
        assert ('<item id="bilingual-css-2" href="bilingual-2.css" media-type="text/css"/>'
                in z.read('OEBPS/content.opf').decode())

    def test_mimetype_bytes_and_member_attributes_copied(self, tmp_path):
        epub = _raw_book(tmp_path)
        path = str(tmp_path / 'attrs.epub')
        with zipfile.ZipFile(epub) as src, zipfile.ZipFile(path, 'w') as dst:
            for info in src.infolist():
                info.external_attr = 0o100640 << 16
                data = b'application/epub+zip ' if info.filename == 'mimetype' \
                    else src.read(info.filename)
                dst.writestr(info, data)
        project_dir = _project(tmp_path, path, RAW_TRANSLATIONS)
        _, z = _export(tmp_path, project_dir, 'chinese-only')
        assert z.read('mimetype') == b'application/epub+zip '
        assert {i.external_attr for i in z.infolist() if i.filename != 'mimetype'} == {
            0o100640 << 16}

    def test_nav_list_targets_relabelled(self, tmp_path):
        ncx = ('<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navList>'
               '<navTarget id="t"><navLabel><text>X</text></navLabel>'
               '<content src="Text/c.xhtml"/></navTarget></navList></ncx>')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', xhtml('<h1>Cee</h1>'))}, nav=None, ncx=ncx)
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<h1>西</h1>'})
        _, z = _export(tmp_path, project_dir, 'chinese-only')
        assert '<text>西</text>' in z.read('OEBPS/toc.ncx').decode()

    def test_nav_labels_only_on_links(self, tmp_path):
        nav = xhtml('<nav><ol><li><span href="Text/c.xhtml">Keep</span>'
                    '<a href="Text/c.xhtml">C</a></li></ol></nav>')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', xhtml('<h1>Cee</h1>'))}, nav=nav, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<h1>西</h1>'})
        _, z = _export(tmp_path, project_dir, 'chinese-only')
        assert ('<span href="Text/c.xhtml">Keep</span><a href="Text/c.xhtml">西</a>'
                in z.read('OEBPS/nav.xhtml').decode())

    @pytest.mark.parametrize('fmt', BILINGUAL)
    def test_prefixed_xhtml_namespace(self, tmp_path, fmt):
        chapter = ('<?xml version="1.0" encoding="utf-8"?>\n<h:html xmlns:h="http://www.w3.org/'
                   '1999/xhtml"><h:head><h:title>T</h:title></h:head><h:body><h:p>Words here.'
                   '</h:p></h:body></h:html>')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', chapter)}, nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<h:p>字。</h:p>'})
        _, z = _export(tmp_path, project_dir, fmt)
        out = z.read('OEBPS/Text/c.xhtml').decode()
        assert '<h:div class="original"><h:p>Words here.</h:p></h:div>' in out
        assert '<h:link rel="stylesheet" type="text/css" href="../bilingual.css"/>' in out

    def test_empty_range_is_a_mismatch(self, tmp_path):
        epub = _raw_book(tmp_path)
        project_dir = _project(tmp_path, epub, RAW_TRANSLATIONS)
        path = os.path.join(project_dir, 'project.json')
        raw = json.load(open(path, encoding='utf-8'))
        empty = dict(raw['segments'][1], segment_id='one_seg0003', block_start=1, block_end=1,
                     fingerprints=[])
        raw['segments'].insert(2, empty)
        json.dump(raw, open(path, 'w', encoding='utf-8'))
        report, _ = _export(tmp_path, project_dir, 'chinese-only')
        assert {r for _, r in report.chapters[0].kept} == {CHAPTER_MISMATCH}

    def test_br_in_a_single_run_refused(self, tmp_path):
        chapter = xhtml('<p>Lead.</p>Bare text.')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', chapter)}, nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<p>引。</p>\n裸<br/>文。'})
        report, _ = _export(tmp_path, project_dir, 'chinese-only')
        assert report.reasons() == {STRUCTURE_MISMATCH: 1}

    def test_run_with_inline_script(self, tmp_path):
        chapter = xhtml('<div class="v">Line <script>var x;</script>one<br/>Line two</div>')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', chapter)}, nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': '第一行\n第二行'})
        report, _ = _export(tmp_path, project_dir, 'chinese-only')
        assert report.translated == 1

    def test_malformed_stored_html_cannot_be_mapped(self, tmp_path):
        epub = _raw_book(tmp_path)
        project_dir = _project(tmp_path, epub, RAW_TRANSLATIONS)
        path = os.path.join(project_dir, 'project.json')
        raw = json.load(open(path, encoding='utf-8'))
        raw['segments'][3]['html'] = '<p>The harbour bells rang twice.'
        json.dump(raw, open(path, 'w', encoding='utf-8'))
        report, _ = _export(tmp_path, project_dir, 'chinese-only')
        assert report.chapters[1].kept == [('two_seg0002', AMBIGUOUS_SOURCE)]

    @pytest.mark.parametrize('fmt', BILINGUAL)
    def test_text_after_a_bilingual_element_stays(self, tmp_path, fmt):
        chapter = xhtml('<h1>Title words</h1>Tail text after it.')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', chapter)}, nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<h1>標題</h1>'})
        _, z = _export(tmp_path, project_dir, fmt)
        body = _body(z, 'OEBPS/Text/c.xhtml')
        assert body[0].tail == 'Tail text after it.'

    @pytest.mark.parametrize('fmt', BILINGUAL)
    def test_run_starting_with_an_element(self, tmp_path, fmt):
        chapter = xhtml('<div class="v"><em>Line</em> one<br/>Line two</div>')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', chapter)}, nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<em>第一</em>行\n第二行'})
        _, z = _export(tmp_path, project_dir, fmt)
        original = _body(z, 'OEBPS/Text/c.xhtml')[0][0][0]
        assert _inner(original) == '<em>Line</em> one'


class TestAnchorsInTheGate:
    """Fix round: ids (and names of <a>) inside a block must survive."""

    def _report(self, tmp_path, chapter, translation, fmt='chinese-only'):
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', xhtml(chapter))}, nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': translation})
        report, z = _export(tmp_path, project_dir, fmt)
        return report, z, epub

    @pytest.mark.parametrize('chapter, translation', [
        ('<p>See <a id="r1" href="#n1">note</a>.</p><p id="n1">Text.</p>',
         '<p>見<a href="#n1">註</a>。</p>\n<p>文。</p>'),                       # inner id lost
        ('<p><a name="fn"/>Note text.</p>', '<p>註文。</p>'),                     # a@name lost
        ('<p><a id="x" name="y"/>Words.</p>', '<p><b name="x"/><a id="y"/>字。</p>'),  # b@name no anchor
        ('<p>Plain <span id="s">words</span>.</p>', '<p>普通<span id="t">字</span>。</p>'),
        ('<div class="v"><a id="l1"/>Line one<br/>Line two</div>', '第一行\n第二行'),  # in a run
        ('<div class="v">Line one<br/><a id="l2"/>Line two</div>',
         '<a id="l2"/>第一行\n第二行'),                                         # wrong line
    ])
    @pytest.mark.parametrize('fmt', FORMATS)
    def test_missing_anchor_kept_original(self, tmp_path, chapter, translation, fmt):
        report, z, epub = self._report(tmp_path, chapter, translation, fmt)
        assert report.chapters[0].kept == [('c_seg0001', MISSING_ANCHOR)]
        src = zipfile.ZipFile(epub)
        assert z.read('OEBPS/Text/c.xhtml') == src.read('OEBPS/Text/c.xhtml')

    @pytest.mark.parametrize('chapter, translation', [
        ('<p>See <a id="r1" href="#n1">note</a>.</p><p id="n1">Text.</p>',
         '<p>見<a id="r1" href="#n1">註</a>。</p>\n<p>文。</p>'),                # block's own id from the source
        ('<p><a name="fn"/>Note text.</p>', '<p><a name="fn"/>註文。</p>'),
        ('<p><span name="x"/>Words.</p>', '<p>字。</p>'),                        # name only counts on <a>
        ('<p><a name="fn"/>Note text.</p>', '<p><a id="fn"/>註文。</p>'),         # the value counts
        ('<p>Words.</p>', '<p><a id="new"/>字。</p>'),                           # extra ids allowed
        ('<div class="v">Line one<br/><a id="l2"/>Line two</div>', '第一行\n<a id="l2"/>第二行'),
    ])
    def test_anchor_present_accepted(self, tmp_path, chapter, translation):
        report, _, _ = self._report(tmp_path, chapter, translation)
        assert report.translated == 1 and report.reasons() == {}

    def test_code_not_checked(self, tmp_path):
        report, _, _ = self._report(tmp_path, '<pre id="k"><code>x = 1  # one</code></pre>',
                                    'x = 1  # 一')
        assert report.translated == 1


class TestTocContainerTarget:
    """Fix round: a target that contains blocks takes its first block's
    label when that block is a filled heading."""

    NAV = xhtml('<nav><ol>'
                '<li><a href="Text/c.xhtml#sec">A</a></li>'
                '<li><a href="Text/c.xhtml#body">B</a></li>'
                '<li><a href="Text/c.xhtml#lead">C</a></li>'
                '<li><a href="Text/c.xhtml#outer">D</a></li>'
                '<li><a href="Text/c.xhtml#runbox">E</a></li>'
                '</ol></nav>')
    CHAPTER = ('<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/'
               'xhtml"><head><title>T</title></head><body id="body">'
               '<div id="outer"><div id="sec"><h2>Part words</h2><p>Body words.</p></div>'
               '<div id="lead"><p>Lead words.</p><h3>Late heading</h3></div></div>'
               '<div id="runbox">Bare text<br/>More text</div></body></html>')

    def _labels(self, tmp_path, translations, fmt='chinese-only'):
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', self.CHAPTER)}, nav=self.NAV, ncx=None)
        project_dir = _project(tmp_path, epub, translations)
        _, z = _export(tmp_path, project_dir, fmt)
        return [_inner(a) for a in _root(z, 'OEBPS/nav.xhtml').iter(f'{XHTML_NS}a')]

    def test_segments(self, tmp_path):
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', self.CHAPTER)}, nav=None, ncx=None)
        project_dir = _project(tmp_path, epub, {})
        assert [s.html for s in ProjectManager(project_dir).load().segments] == [
            '<h2>Part words</h2>', '<p>Body words.</p>\n<p>Lead words.</p>',
            '<h3>Late heading</h3>', 'Bare text\nMore text']

    @pytest.mark.parametrize('fmt', FORMATS)
    def test_first_block_heading(self, tmp_path, fmt):
        labels = self._labels(tmp_path, {'c_seg0001': '<h2>部分</h2>',
                                         'c_seg0002': '<p>內文。</p>\n<p>引言。</p>',
                                         'c_seg0003': '<h3>後標題</h3>',
                                         'c_seg0004': '裸文\n更多'}, fmt)
        # A, B (the body) and D: first block is the filled h2; C: first block
        # is a <p>; E: first block is a run
        assert labels == ['部分', '部分', 'C', '部分', 'E']

    def test_unfilled_heading_leaves_labels(self, tmp_path):
        labels = self._labels(tmp_path, {'c_seg0001': '<h2>部分',
                                         'c_seg0003': '<h3>後標題</h3>'})
        assert labels == ['A', 'B', 'C', 'D', 'E']

    def test_target_inside_a_block_wins(self, tmp_path):
        chapter = xhtml('<div id="d"><h2 id="h">Part words</h2></div>')
        nav = xhtml('<nav><ol><li><a href="Text/c.xhtml#d">D</a></li>'
                    '<li><a href="Text/c.xhtml#h">H</a></li></ol></nav>')
        epub = _raw_book(tmp_path, {'c': ('c.xhtml', chapter)}, nav=nav, ncx=None)
        project_dir = _project(tmp_path, epub, {'c_seg0001': '<h2>部分</h2>'})
        _, z = _export(tmp_path, project_dir, 'bilingual-vertical')
        assert [_inner(a) for a in _root(z, 'OEBPS/nav.xhtml').iter(f'{XHTML_NS}a')] == [
            '部分', '部分']
