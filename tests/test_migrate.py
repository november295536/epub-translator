"""Tests for the schema 1 -> 2 migration (Phase 2). Legacy projects are
written by hand over raw-ZIP EPUBs with invented text."""

import json
import os
import shutil

import pytest
from bs4 import BeautifulSoup
from click.testing import CliRunner

from src.core.migrate import MigrationError, migrate_project, _groups
from src.core.project import ProjectLockedError, ProjectManager, SegmentStatus, sha256_file
from src.epub.blocks import fingerprint
from tests.conftest import (
    hold_lock_in_child, lock_free_for_other_process, write_raw_epub, xhtml,
)

CHAPTERS = {
    'c1': '<h1>Harbour</h1><p>The boats came home.</p><p>Nets were dried.</p>',
    'c2': '<p>Intro line.</p>First bare sentence. Second bare sentence.',
}


def _segment(chapter_id, n, html, translated='', status='pending', segment_type='text'):
    text = BeautifulSoup(html, 'html.parser').get_text()
    return {'segment_id': f'{chapter_id}_seg{n:04d}', 'chapter_id': chapter_id, 'html': html,
            'text': text, 'word_count': len(text.split()), 'segment_type': segment_type,
            'status': status, 'translated_text': translated,
            'translated_at': '2026-01-01 00:00:00' if translated else ''}


DEFAULT_SEGMENTS = [
    _segment('c1', 1, '<h1>Harbour</h1>', '<h1>港</h1>', 'translated'),
    _segment('c1', 2, '<p>The boats came home.</p>\n<p>Nets were dried.</p>',
             '<p>船回來了。</p>\n<p>網晾乾了。</p>', 'reviewed'),
    _segment('c2', 1, '<p>Intro line.</p>\nFirst bare sentence.', '<p>引言。</p>\n第一句。',
             'translated'),
    _segment('c2', 2, 'Second bare sentence.', '第二句。', 'translated'),
]


def _legacy_project(tmp_path, chapters=None, segments=None, source_inside=True, extra=None,
                    name='proj'):
    """A schema 1 project.json (as main wrote it) in tmp_path/<name>, and its
    source EPUB (tmp_path/<name>.epub, copied into the project unless
    source_inside is false)."""
    chapters = CHAPTERS if chapters is None else chapters
    items = [(cid, f'Text/{cid}.xhtml', 'application/xhtml+xml', None) for cid in chapters]
    members = {f'OEBPS/Text/{cid}.xhtml': (body if body.startswith('<?xml') else xhtml(body))
               for cid, body in chapters.items()}
    epub_path = write_raw_epub(tmp_path / f'{name}.epub', items, list(chapters), members)
    project_dir = tmp_path / name
    project_dir.mkdir()
    if source_inside:
        shutil.copy(epub_path, project_dir / 'source.epub')
        epub_path = str(project_dir / 'source.epub')
    raw = {'epub_path': epub_path, 'project_dir': str(project_dir), 'title': 'T',
           'author': 'A', 'created_at': '2026-01-01 00:00:00',
           'updated_at': '2026-01-01 00:00:00', 'model': 'sonnet',
           'segments': DEFAULT_SEGMENTS if segments is None else segments}
    raw.update(extra or {})
    (project_dir / 'project.json').write_text(json.dumps(raw, ensure_ascii=False, indent=2),
                                              encoding='utf-8')
    return str(project_dir)


def _files(project_dir):
    return {name: open(os.path.join(project_dir, name), 'rb').read()
            for name in sorted(os.listdir(project_dir))}


class TestMigrate:

    def test_migrates_and_keeps_translations(self, tmp_path):
        project_dir = _legacy_project(tmp_path)
        before = open(os.path.join(project_dir, 'project.json'), 'rb').read()

        report = migrate_project(project_dir)

        assert (report.chapters, report.chapters_matched) == (2, 2)
        assert (report.segments, report.segments_matched) == (4, 4)
        assert report.merged == [['c2_seg0001', 'c2_seg0002']]
        assert report.unmatched == [] and report.notes == []
        assert report.written
        with open(os.path.join(project_dir, 'project.schema1.json'), 'rb') as f:
            assert f.read() == before

        pm = ProjectManager(project_dir)
        pm.load()
        assert pm.data.schema == 2
        assert pm.data.epub_path == os.path.join(project_dir, 'source.epub')
        assert pm.data.source_sha256 == sha256_file(pm.data.epub_path)
        segs = pm.data.segments
        assert [s.segment_id for s in segs] == ['c1_seg0001', 'c1_seg0002', 'c2_seg0001']
        assert [(s.block_start, s.block_end) for s in segs] == [(0, 1), (1, 3), (0, 2)]
        assert [s.status for s in segs] == [
            SegmentStatus.TRANSLATED, SegmentStatus.REVIEWED, SegmentStatus.TRANSLATED]
        assert segs[2].translated_text == '<p>引言。</p>\n第一句。第二句。'
        assert segs[2].html == '<p>Intro line.</p>\nFirst bare sentence. Second bare sentence.'
        assert segs[2].fingerprints == [fingerprint('Intro line.'),
                                        fingerprint('First bare sentence. Second bare sentence.')]
        assert segs[1].text == 'The boats came home.\nNets were dried.'
        with open(pm.project_file, encoding='utf-8') as f:
            raw = json.load(f)
        assert raw['schema'] == 2 and 'segmenter_version' not in raw

    def test_translated_characters_unchanged(self, tmp_path):
        project_dir = _legacy_project(tmp_path)
        total = sum(len(s['translated_text']) for s in DEFAULT_SEGMENTS)
        migrate_project(project_dir)
        pm = ProjectManager(project_dir)
        pm.load()
        assert sum(len(s.translated_text) for s in pm.data.segments) == total

    def test_dry_run_writes_nothing(self, tmp_path):
        project_dir = _legacy_project(tmp_path)
        before = _files(project_dir)
        report = migrate_project(project_dir, dry_run=True)
        assert report.chapters_matched == 2 and report.written is False
        assert _files(project_dir) == before   # not even a lock file

    @pytest.mark.parametrize('statuses, merged', [
        (('translated', 'pending'), SegmentStatus.PENDING),
        (('reviewed', 'translated'), SegmentStatus.TRANSLATED),
        (('translated', 'failed'), SegmentStatus.FAILED),
        (('failed', 'pending'), SegmentStatus.PENDING),
        (('reviewed', 'reviewed'), SegmentStatus.REVIEWED),
    ])
    def test_merged_status_is_lowest(self, tmp_path, statuses, merged):
        segments = [_segment('c2', 1, '<p>Intro line.</p>\nFirst bare sentence.',
                             '甲' if statuses[0] != 'pending' else '', statuses[0]),
                    _segment('c2', 2, 'Second bare sentence.',
                             '乙' if statuses[1] != 'pending' else '', statuses[1])]
        project_dir = _legacy_project(tmp_path, {'c2': CHAPTERS['c2']}, segments)
        report = migrate_project(project_dir)
        pm = ProjectManager(project_dir)
        pm.load()
        assert [s.status for s in pm.data.segments] == [merged]
        # Review round 1: a group not wholly translated keeps no translation
        cleared = merged in (SegmentStatus.PENDING, SegmentStatus.FAILED)
        assert pm.data.segments[0].translated_text == ('' if cleared else
                                                       ''.join(s['translated_text'] for s in segments))
        if len(set(statuses)) > 1:
            note = (f'c2_seg0001：2 個段落的狀態為 {statuses[0]}, {statuses[1]}；'
                    f'合併後為 {merged}，'
                    + ('部分譯文已清除（保留於 project.schema1.json）' if cleared
                       else '譯文依序接合保留'))
            assert report.notes == [note]
            assert f'  注意 {note}' in report.lines()
        else:
            assert report.notes == []

    def test_partial_group_loses_its_translation(self, tmp_path):
        # Review round 1 (was ..._keeps_its_translation): export applies any
        # non-empty translation, so a partial one would replace the group
        segments = [_segment('c2', 1, '<p>Intro line.</p>\nFirst bare sentence.', '甲',
                             'translated'),
                    _segment('c2', 2, 'Second bare sentence.')]
        project_dir = _legacy_project(tmp_path, {'c2': CHAPTERS['c2']}, segments)
        report = migrate_project(project_dir)
        assert report.notes == ['c2_seg0001：2 個段落的狀態為 translated, pending；'
                                '合併後為 pending，部分譯文已清除（保留於 project.schema1.json）']
        pm = ProjectManager(project_dir)
        pm.load()
        seg = pm.data.segments[0]
        assert (seg.status, seg.translated_text) == (SegmentStatus.PENDING, '')
        with open(os.path.join(project_dir, 'project.schema1.json'), encoding='utf-8') as f:
            assert json.load(f)['segments'][0]['translated_text'] == '甲'

    def test_failed_group_of_one_status_cleared_and_named(self, tmp_path):
        segments = [_segment('c2', 1, '<p>Intro line.</p>\nFirst bare sentence.', '甲', 'failed'),
                    _segment('c2', 2, 'Second bare sentence.', '', 'failed')]
        project_dir = _legacy_project(tmp_path, {'c2': CHAPTERS['c2']}, segments)
        report = migrate_project(project_dir)
        assert report.notes == ['c2_seg0001：2 個段落的狀態為 failed, failed；'
                                '合併後為 failed，部分譯文已清除（保留於 project.schema1.json）']
        pm = ProjectManager(project_dir)
        pm.load()
        assert pm.data.segments[0].translated_text == ''

    def test_untranslated_group_has_no_note(self, tmp_path):
        segments = [_segment('c2', 1, '<p>Intro line.</p>\nFirst bare sentence.'),
                    _segment('c2', 2, 'Second bare sentence.')]
        project_dir = _legacy_project(tmp_path, {'c2': CHAPTERS['c2']}, segments)
        assert migrate_project(project_dir).notes == []

    def test_partial_group_exports_in_the_original(self, tmp_path):
        import zipfile
        from src.epub.exporter import export_project
        segments = DEFAULT_SEGMENTS[:2] + [
            _segment('c2', 1, '<p>Intro line.</p>\nFirst bare sentence.', '<p>引言。</p>\n第一句。',
                     'translated'),
            _segment('c2', 2, 'Second bare sentence.', '', 'failed')]
        project_dir = _legacy_project(tmp_path, segments=segments)
        migrate_project(project_dir)
        output = str(tmp_path / 'out.epub')
        export_project(project_dir, output, 'chinese-only')
        with zipfile.ZipFile(output) as z:
            member = {n.rsplit('/', 1)[-1]: n for n in z.namelist()}
            c2 = z.read(member['c2.xhtml']).decode('utf-8')
        assert 'First bare sentence. Second bare sentence.' in c2 and '第一句' not in c2

    def test_group_mixing_code_and_text_unmatched(self, tmp_path):
        chapters = {'c1': '<p>Call <code>save()</code> before leaving.</p>'}
        segments = [_segment('c1', 1, '<code>save()</code>', 'save()', 'translated', 'code'),
                    _segment('c1', 2, 'before leaving.', '離開前。', 'translated')]
        segments[0]['html'] = '<p>Call <code>save()</code>'
        project_dir = _legacy_project(tmp_path, chapters, segments)
        before = _files(project_dir)
        report = migrate_project(project_dir)
        assert report.unmatched == [('c1', 'c1_seg0001 merges code and text segments')]
        assert not report.written and report.merged == []
        after = _files(project_dir)
        after.pop('project.lock')
        assert after == before

    def test_existing_backup_refused(self, tmp_path):
        project_dir = _legacy_project(tmp_path)
        backup = os.path.join(project_dir, 'project.schema1.json')
        with open(backup, 'w', encoding='utf-8') as f:
            f.write('older backup')
        before = _files(project_dir)
        with pytest.raises(MigrationError) as e:
            migrate_project(project_dir)
        assert str(e.value) == 'project.schema1.json 已存在，不會覆寫；請先移走它'
        after = _files(project_dir)
        after.pop('project.lock')
        assert after == before

    def test_restored_schema_1_after_migration_refused(self, tmp_path):
        project_dir = _legacy_project(tmp_path)
        migrate_project(project_dir)
        shutil.copy(os.path.join(project_dir, 'project.schema1.json'),
                    os.path.join(project_dir, 'project.json'))
        with pytest.raises(MigrationError, match='已存在'):
            migrate_project(project_dir)

    def test_source_copied_through_a_temporary_file(self, tmp_path, monkeypatch):
        import src.core.migrate as migrate_module
        project_dir = _legacy_project(tmp_path, source_inside=False)

        def broken_copy(src, dst):
            with open(dst, 'wb') as f:
                f.write(b'PK partial')
            raise OSError('disk full')
        monkeypatch.setattr(migrate_module.shutil, 'copy2', broken_copy)
        with pytest.raises(OSError):
            migrate_project(project_dir)
        assert sorted(os.listdir(project_dir)) == ['project.json', 'project.lock']
        monkeypatch.undo()
        assert migrate_project(project_dir).written

    def test_corrupt_source_is_a_migration_error(self, tmp_path):
        project_dir = _legacy_project(tmp_path)
        with open(os.path.join(project_dir, 'source.epub'), 'wb') as f:
            f.write(b'PK truncated')
        with pytest.raises(MigrationError) as e:
            migrate_project(project_dir, dry_run=True)
        assert str(e.value) == '來源 EPUB 無法讀取：無法讀取 EPUB：File is not a zip file'

    def test_not_a_project_refused_without_a_lock_file(self, tmp_path):
        with pytest.raises(MigrationError) as e:
            migrate_project(str(tmp_path))
        assert str(e.value) == f'不是專案目錄（沒有 project.json）：{tmp_path}'
        assert os.listdir(tmp_path) == []

    def test_corrupt_project_file_is_a_migration_error(self, tmp_path):
        (tmp_path / 'project.json').write_text('{"segments": [', encoding='utf-8')
        with pytest.raises(MigrationError, match='^無法讀取 project.json：'):
            migrate_project(str(tmp_path), dry_run=True)

    def test_chapter_with_entity_reference_unmatched(self, tmp_path):
        chapters = {'c1': ('<?xml version="1.0"?>\n<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
                           '"x.dtd">\n<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                           '<p>a&mdash;b</p></body></html>')}
        segments = [_segment('c1', 1, '<p>a—b</p>', '甲', 'translated')]
        project_dir = _legacy_project(tmp_path, chapters, segments)
        report = migrate_project(project_dir, dry_run=True)
        assert report.unmatched == [('c1', 'unresolved entity reference')]

    def test_chapters_after_an_entity_chapter_still_checked(self, tmp_path):
        chapters = {'c0': ('<?xml version="1.0"?>\n<!DOCTYPE html [<!ENTITY t "x">]>\n'
                           '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>&t;</p>'
                           '</body></html>'),
                    'c1': CHAPTERS['c1']}
        segments = [_segment('c0', 1, '<p>x</p>')] + DEFAULT_SEGMENTS[:2]
        project_dir = _legacy_project(tmp_path, chapters, segments)
        report = migrate_project(project_dir, dry_run=True)
        assert report.unmatched == [('c0', 'unresolved entity reference')]
        assert report.chapters_matched == 1

    def test_merge_keeps_fields(self, tmp_path):
        segments = [_segment('c2', 1, '<p>Intro line.</p>\nFirst bare sentence.', '甲',
                             'translated'),
                    _segment('c2', 2, 'Second bare sentence.', '乙', 'translated')]
        segments[0].update(model='haiku', revision=2, translated_at='2026-01-01 00:00:00',
                           issues=[{'kind': 'quality', 'code': 'a', 'detail': '', 'blocking': False}],
                           usage={'input_tokens': 1})
        segments[1].update(model='opus', revision=5, translated_at='2026-02-01 00:00:00',
                           issues=[{'kind': 'quality', 'code': 'b', 'detail': '', 'blocking': False}])
        project_dir = _legacy_project(tmp_path, {'c2': CHAPTERS['c2']}, segments)
        migrate_project(project_dir)
        pm = ProjectManager(project_dir)
        pm.load()
        seg = pm.data.segments[0]
        assert seg.model == 'haiku'
        assert seg.revision == 5
        assert seg.translated_at == '2026-02-01 00:00:00'
        assert [i.code for i in seg.issues] == ['a', 'b']
        assert seg.usage is None

    def test_single_segment_keeps_usage_and_model(self, tmp_path):
        segments = [_segment('c1', 1, '<h1>Harbour</h1>\n<p>The boats came home.</p>\n'
                             '<p>Nets were dried.</p>', '譯', 'translated')]
        segments[0].update(model='', usage={'input_tokens': 3})
        segments[0]['model'] = 'opus'
        project_dir = _legacy_project(tmp_path, {'c1': CHAPTERS['c1']}, segments)
        migrate_project(project_dir)
        pm = ProjectManager(project_dir)
        pm.load()
        assert (pm.data.segments[0].usage, pm.data.segments[0].model) == (
            {'input_tokens': 3}, 'opus')

    def test_body_level_comment_stored_as_text(self, tmp_path):
        # Bug 5: an old segmenter stored the comment's text
        chapters = {'c1': '<p>A one.</p><!-- printer note --><p>B two.</p>'}
        segments = [_segment('c1', 1, '<p>A one.</p>\nprinter note\n<p>B two.</p>', '譯',
                             'translated')]
        project_dir = _legacy_project(tmp_path, chapters, segments)
        report = migrate_project(project_dir)
        assert report.chapters_with_comment_text == 1
        pm = ProjectManager(project_dir)
        pm.load()
        seg = pm.data.segments[0]
        assert (seg.block_start, seg.block_end) == (0, 2)
        assert seg.html == '<p>A one.</p>\n<p>B two.</p>'

    def test_comment_in_wrapper_div_stored_as_text(self, tmp_path):
        chapters = {'c1': '<div><!-- note --><p>A one.</p><p>B two.</p></div>'}
        segments = [_segment('c1', 1, 'note\n<p>A one.</p>', '一', 'translated'),
                    _segment('c1', 2, '<p>B two.</p>', '二', 'translated')]
        project_dir = _legacy_project(tmp_path, chapters, segments)
        report = migrate_project(project_dir)
        assert report.chapters_matched == 1 and report.chapters_with_comment_text == 1
        pm = ProjectManager(project_dir)
        pm.load()
        assert [(s.block_start, s.block_end) for s in pm.data.segments] == [(0, 1), (1, 2)]

    def test_comment_without_text_matches_first(self, tmp_path):
        chapters = {'c1': '<p>note</p><!--note-->'}
        segments = [_segment('c1', 1, '<p>note</p>', '註', 'translated')]
        project_dir = _legacy_project(tmp_path, chapters, segments)
        assert migrate_project(project_dir).chapters_with_comment_text == 0

    def test_segment_of_only_comment_text_unmatched(self, tmp_path):
        chapters = {'c1': '<p>A one.</p><!-- note --><p>B two.</p>'}
        segments = [_segment('c1', 1, '<p>A one.</p>'), _segment('c1', 2, 'note'),
                    _segment('c1', 3, '<p>B two.</p>')]
        project_dir = _legacy_project(tmp_path, chapters, segments)
        before = _files(project_dir)
        report = migrate_project(project_dir)
        assert report.unmatched == [('c1', 'c1_seg0002 holds only comment text')]
        assert not report.written
        assert {k: v for k, v in _files(project_dir).items() if k != 'project.lock'} == before

    def test_unmatched_chapter_writes_nothing(self, tmp_path):
        segments = DEFAULT_SEGMENTS[:2] + [_segment('c2', 1, '<p>Different text.</p>', '異',
                                                    'translated')]
        project_dir = _legacy_project(tmp_path, segments=segments)
        before = _files(project_dir)
        report = migrate_project(project_dir)
        assert report.unmatched == [('c2', 'text differs')]
        assert report.chapters_matched == 1 and report.segments_matched == 2
        assert not report.written
        after = _files(project_dir)
        assert after.pop('project.lock', None) is not None   # taken, nothing else written
        assert after == before
        assert report.lines()[-2:] == [
            '已翻譯段落的頂層元素數與新區塊 HTML 不同：0（僅供參考，不阻擋）', '未寫入任何檔案']
        assert '無法對應的章節 c2：text differs' in report.lines()

    @pytest.mark.parametrize('chapters, reason', [
        ({'c1': CHAPTERS['c1']}, 'not in the spine'),
        ({'c1': CHAPTERS['c1'], 'c2': '<?xml version="1.0"?><html><body><p>x</div></html>'},
         'missing or not well-formed XML'),
        ({'c1': CHAPTERS['c1'],
          'c2': '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"/>'},
         'no body'),
    ])
    def test_chapter_problems_reported(self, tmp_path, chapters, reason):
        project_dir = _legacy_project(tmp_path, chapters)
        report = migrate_project(project_dir, dry_run=True)
        assert report.unmatched == [('c2', reason)]

    def test_chapter_without_text_unmatched(self, tmp_path):
        chapters = {'c1': '<div><img src="x.png" alt=""/></div>'}
        segments = [_segment('c1', 1, '<img src="x.png"/>')]
        project_dir = _legacy_project(tmp_path, chapters, segments)
        assert migrate_project(project_dir, dry_run=True).unmatched == [('c1', 'no text')]

    def test_count_differs_reported(self, tmp_path):
        segments = [DEFAULT_SEGMENTS[0],
                    _segment('c1', 2, '<p>The boats came home.</p>\n<p>Nets were dried.</p>',
                             '<p>船回來了，網晾乾了。</p>', 'translated')]
        project_dir = _legacy_project(tmp_path, {'c1': CHAPTERS['c1']}, segments)
        assert migrate_project(project_dir).count_differs == 1

    def test_code_segment_not_counted_and_keeps_type(self, tmp_path):
        chapters = {'c1': '<p>Run it.</p><pre>x = 1\ny = 2</pre>'}
        segments = [_segment('c1', 1, '<p>Run it.</p>', '<p>執行。</p>', 'translated'),
                    _segment('c1', 2, '<pre>x = 1\ny = 2</pre>', 'x = 1\ny = 2', 'translated',
                             'code')]
        project_dir = _legacy_project(tmp_path, chapters, segments)
        assert migrate_project(project_dir).count_differs == 0
        pm = ProjectManager(project_dir)
        pm.load()
        assert pm.data.segments[1].segment_type == 'code'
        assert pm.data.segments[1].text == 'x = 1\ny = 2'

    def test_external_source_copied_into_project(self, tmp_path):
        project_dir = _legacy_project(tmp_path, source_inside=False)
        external = str(tmp_path / 'proj.epub')
        migrate_project(project_dir)
        own = os.path.join(project_dir, 'source.epub')
        with open(own, 'rb') as a, open(external, 'rb') as b:
            assert a.read() == b.read()
        pm = ProjectManager(project_dir)
        pm.load()
        assert pm.data.epub_path == own

    def test_local_copy_preferred_over_stored_path(self, tmp_path):
        project_dir = _legacy_project(tmp_path, extra={'epub_path': '/moved/away.epub'})
        assert migrate_project(project_dir).written

    def test_missing_source_refused(self, tmp_path):
        project_dir = _legacy_project(tmp_path, source_inside=False,
                                      extra={'epub_path': str(tmp_path / 'gone.epub')})
        with pytest.raises(MigrationError) as e:
            migrate_project(project_dir)
        assert str(e.value) == f'找不到來源 EPUB：{tmp_path / "gone.epub"}'

    def test_schema_2_refused(self, tmp_path):
        project_dir = _legacy_project(tmp_path)
        migrate_project(project_dir)
        with pytest.raises(MigrationError, match='專案已是 schema 2'):
            migrate_project(project_dir)

    def test_refused_while_another_process_holds_the_lock(self, tmp_path):
        project_dir = _legacy_project(tmp_path)
        before = open(os.path.join(project_dir, 'project.json'), 'rb').read()
        child = hold_lock_in_child(project_dir)
        try:
            with pytest.raises(ProjectLockedError):
                migrate_project(project_dir)
        finally:
            child.stdin.close()
            child.wait()
        assert open(os.path.join(project_dir, 'project.json'), 'rb').read() == before
        assert not os.path.exists(os.path.join(project_dir, 'project.schema1.json'))

    def test_migrated_project_exports(self, tmp_path):
        import zipfile
        from src.epub.exporter import export_project
        project_dir = _legacy_project(tmp_path)
        migrate_project(project_dir)
        output = str(tmp_path / 'out.epub')
        export_project(project_dir, output, 'chinese-only')
        with zipfile.ZipFile(output) as z:
            member = {n.rsplit('/', 1)[-1]: n for n in z.namelist()}
            c1 = z.read(member['c1.xhtml']).decode('utf-8')
            c2 = z.read(member['c2.xhtml']).decode('utf-8')
        assert '第一句。第二句。' in c2 and 'bare sentence' not in c2
        assert '網晾乾了。' in c1 and 'boats' not in c1


class TestGroups:

    def test_whole_and_merged(self):
        assert _groups(['ab', 'c', 'd'], ['a', 'bcd']) == [([0, 1, 2], 0, 2)]
        assert _groups(['a', 'bc'], ['a', 'b', 'c']) == [([0], 0, 1), ([1], 1, 3)]

    def test_segments_without_text(self):
        # a text-less segment after a boundary joins the next group; trailing
        # ones join the last group
        assert _groups(['a', '', 'b', ''], ['a', 'b']) == [([0], 0, 1), ([1, 2, 3], 1, 2)]
        assert _groups(['', ''], []) == []


class TestMigrateCLI:

    def test_success_prints_report(self, tmp_path):
        from translate import cli
        project_dir = _legacy_project(tmp_path)
        result = CliRunner().invoke(cli, ['migrate', project_dir])
        assert result.exit_code == 0, result.output
        assert result.output.splitlines() == [
            '章節對應：2/2（其中 0 章含註解文字）',
            '段落對應：4/4',
            '合併群組：1（2 個段落）',
            '  合併 c2_seg0001 + c2_seg0002',
            '已翻譯段落的頂層元素數與新區塊 HTML 不同：0（僅供參考，不阻擋）',
            '已寫入 project.json（舊檔保留為 project.schema1.json）',
        ]

    def test_dry_run(self, tmp_path):
        from translate import cli
        project_dir = _legacy_project(tmp_path)
        before = _files(project_dir)
        result = CliRunner().invoke(cli, ['migrate', '--dry-run', project_dir])
        assert result.exit_code == 0
        assert result.output.splitlines()[-1] == '未寫入任何檔案'
        assert _files(project_dir) == before

    def test_unmatched_exits_non_zero(self, tmp_path):
        from translate import cli
        segments = DEFAULT_SEGMENTS[:2] + [_segment('c2', 1, '<p>Different.</p>')]
        project_dir = _legacy_project(tmp_path, segments=segments)
        result = CliRunner().invoke(cli, ['migrate', project_dir])
        assert result.exit_code == 1
        assert '無法對應的章節 c2：text differs' in result.output
        assert not os.path.exists(os.path.join(project_dir, 'project.schema1.json'))

    def test_not_a_project_gives_a_message(self, tmp_path):
        from translate import cli
        result = CliRunner().invoke(cli, ['migrate', str(tmp_path)])
        assert result.exit_code == 1
        assert result.output == f'不是專案目錄（沒有 project.json）：{tmp_path}\n'
        assert os.listdir(tmp_path) == []

    @pytest.mark.parametrize('case', ['schema2', 'locked'])
    def test_refusals_exit_non_zero(self, tmp_path, case):
        from translate import cli
        project_dir = _legacy_project(tmp_path)
        child = None
        if case == 'schema2':
            migrate_project(project_dir)
        else:
            child = hold_lock_in_child(project_dir)
        try:
            result = CliRunner().invoke(cli, ['migrate', project_dir])
        finally:
            if child:
                child.stdin.close()
                child.wait()
        assert result.exit_code == 1
        expected = '專案已是 schema 2' if case == 'schema2' else '專案正由另一個程序使用'
        assert result.output.startswith(expected)


class TestMigrateSurvivors:
    """Mutation survivors of the migration."""

    def test_top_level_count(self):
        from src.core.migrate import _top_level_count
        assert _top_level_count('<p>a</p>\n<p>b</p>') == 2
        assert _top_level_count('text <em>x</em> more') == 3
        assert _top_level_count('  ') == 0

    def test_trailing_segment_joins_the_last_of_several_groups(self):
        assert _groups(['a', 'b', 'c', ''], ['a', 'b', 'c']) == [
            ([0], 0, 1), ([1], 1, 2), ([2, 3], 2, 3)]

    def test_no_model_stays_empty(self, tmp_path):
        segments = [_segment('c2', 1, '<p>Intro line.</p>\nFirst bare sentence.', '甲',
                             'translated'),
                    _segment('c2', 2, 'Second bare sentence.', '乙', 'translated')]
        project_dir = _legacy_project(tmp_path, {'c2': CHAPTERS['c2']}, segments)
        migrate_project(project_dir)
        pm = ProjectManager(project_dir)
        pm.load()
        assert pm.data.segments[0].model == ''

    @pytest.mark.parametrize('first, reason', [
        ('<p>Other text.</p>', 'text differs'),
        (None, 'not in the spine'),
        ('<?xml version="1.0"?><html><body><p>x</div></html>', 'missing or not well-formed XML'),
        ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"/>', 'no body'),
    ])
    def test_chapters_after_an_unmatched_one_are_still_checked(self, tmp_path, first, reason):
        chapters = {'c1': CHAPTERS['c1']} if first is None else {'c0': first, 'c1': CHAPTERS['c1']}
        segments = [_segment('c0', 1, '<p>Stored text.</p>')] + DEFAULT_SEGMENTS[:2]
        project_dir = _legacy_project(tmp_path, chapters, segments)
        report = migrate_project(project_dir, dry_run=True)
        assert report.unmatched == [('c0', reason)]
        assert (report.chapters_matched, report.segments_matched) == (1, 2)

    def test_count_differs_summed_over_chapters(self, tmp_path):
        segments = [_segment('c1', 1, '<h1>Harbour</h1>', '港', 'translated'),
                    _segment('c1', 2, '<p>The boats came home.</p>\n<p>Nets were dried.</p>',
                             '<p>一</p>', 'translated'),
                    _segment('c2', 1, '<p>Intro line.</p>\nFirst bare sentence.', '<p>引</p>',
                             'translated'),
                    _segment('c2', 2, 'Second bare sentence.', '<p>乙</p><p>丙</p>', 'translated')]
        project_dir = _legacy_project(tmp_path, segments=segments)
        assert migrate_project(project_dir, dry_run=True).count_differs == 2


class TestMigrateReview2:
    """Review round 2: an empty member translation, wrong JSON shapes, a
    failing save."""

    def test_translation_cleared_in_the_review_editor(self, tmp_path):
        from src.web.server import create_app
        segments = [_segment('c2', 1, '<p>Intro line.</p>\nFirst bare sentence.', '甲',
                             'reviewed'),
                    _segment('c2', 2, 'Second bare sentence.', '乙', 'translated')]
        project_dir = _legacy_project(tmp_path, {'c2': CHAPTERS['c2']}, segments)
        app = create_app(projects_dir=str(tmp_path))
        app.config['TESTING'] = True
        with app.test_client() as client:
            response = client.post('/api/projects/proj/segments/c2_seg0002', json={'text': ''})
        assert response.status_code == 200
        with open(os.path.join(project_dir, 'project.json'), encoding='utf-8') as f:
            stored = json.load(f)['segments'][1]
        assert (stored['status'], stored['translated_text']) == ('translated', '')

        report = migrate_project(project_dir)
        assert report.notes == ['c2_seg0001：2 個段落的狀態為 reviewed, translated；'
                                '合併後為 pending，部分譯文已清除（保留於 project.schema1.json）']
        pm = ProjectManager(project_dir)
        pm.load()
        seg = pm.data.segments[0]
        assert (seg.status, seg.translated_text) == (SegmentStatus.PENDING, '')

    def test_empty_member_of_one_status_named(self, tmp_path):
        segments = [_segment('c2', 1, '<p>Intro line.</p>\nFirst bare sentence.', '甲',
                             'translated'),
                    _segment('c2', 2, 'Second bare sentence.', ' ', 'translated')]
        project_dir = _legacy_project(tmp_path, {'c2': CHAPTERS['c2']}, segments)
        report = migrate_project(project_dir, dry_run=True)
        assert report.notes == ['c2_seg0001：2 個段落的狀態為 translated, translated；'
                                '合併後為 pending，部分譯文已清除（保留於 project.schema1.json）']

    @pytest.mark.parametrize('content', [
        'null', '[]', '{"epub_path": "x.epub", "segments": [null]}',
        '{"epub_path": "x.epub", "segments": ["text"]}',
    ])
    def test_wrong_json_shape_is_a_migration_error(self, tmp_path, content):
        (tmp_path / 'project.json').write_text(content, encoding='utf-8')
        with pytest.raises(MigrationError, match='^無法讀取 project.json：'):
            migrate_project(str(tmp_path))
        assert sorted(os.listdir(tmp_path)) == ['project.json', 'project.lock']
        assert (tmp_path / 'project.json').read_text(encoding='utf-8') == content

    def test_null_epub_path_is_a_migration_error(self, tmp_path):
        project_dir = _legacy_project(tmp_path, source_inside=False, extra={'epub_path': None})
        before = _files(project_dir)
        with pytest.raises(MigrationError, match='^無法讀取 project.json：'):
            migrate_project(project_dir)
        after = _files(project_dir)
        after.pop('project.lock')
        assert after == before

    def test_wrong_shape_through_the_cli(self, tmp_path):
        from translate import cli
        (tmp_path / 'project.json').write_text('null', encoding='utf-8')
        result = CliRunner().invoke(cli, ['migrate', str(tmp_path)])
        assert result.exit_code == 1
        assert result.output.startswith('無法讀取 project.json：')

    def test_failed_save_removes_the_backup(self, tmp_path, monkeypatch):
        project_dir = _legacy_project(tmp_path)
        before = open(os.path.join(project_dir, 'project.json'), 'rb').read()

        def failing_save(self):
            raise OSError('disk full')
        monkeypatch.setattr(ProjectManager, 'save', failing_save)
        with pytest.raises(OSError):
            migrate_project(project_dir)
        assert not os.path.exists(os.path.join(project_dir, 'project.schema1.json'))
        assert open(os.path.join(project_dir, 'project.json'), 'rb').read() == before
        monkeypatch.undo()
        assert migrate_project(project_dir).written


class TestMigrateReview3:
    """Review round 3: malformed segment fields, the backup copy, a demoted
    group without text."""

    @pytest.mark.parametrize('index, field, value', [
        (0, 'translated_text', None), (0, 'html', 7), (0, 'segment_type', 'poem'),
        (1, 'translated_at', None),
    ])
    def test_malformed_segment_field_is_a_migration_error(self, tmp_path, index, field, value):
        segments = [dict(s) for s in DEFAULT_SEGMENTS]
        segments[2 + index][field] = value
        project_dir = _legacy_project(tmp_path, segments=segments)
        before = _files(project_dir)
        with pytest.raises(MigrationError, match='^project.json 的段落資料格式不正確：'):
            migrate_project(project_dir)
        after = _files(project_dir)
        after.pop('project.lock')
        assert after == before
        assert lock_free_for_other_process(project_dir)   # review round 4

    def test_malformed_segment_through_the_cli(self, tmp_path):
        from translate import cli
        segments = [dict(s) for s in DEFAULT_SEGMENTS]
        segments[2]['translated_text'] = None
        project_dir = _legacy_project(tmp_path, segments=segments)
        result = CliRunner().invoke(cli, ['migrate', project_dir])
        assert result.exit_code == 1
        assert result.output.startswith('project.json 的段落資料格式不正確：')

    def test_failed_backup_copy_leaves_no_backup(self, tmp_path, monkeypatch):
        import src.core.migrate as migrate_module
        project_dir = _legacy_project(tmp_path)
        real = migrate_module.shutil.copy2

        def copy2(src, dst):
            if 'project.schema1.json' in str(dst):
                with open(dst, 'w') as f:
                    f.write('{"partial')
                raise OSError('disk full')
            return real(src, dst)
        monkeypatch.setattr(migrate_module.shutil, 'copy2', copy2)
        with pytest.raises(OSError):
            migrate_project(project_dir)
        assert sorted(os.listdir(project_dir)) == ['project.json', 'project.lock', 'source.epub']
        assert lock_free_for_other_process(project_dir)   # review round 4
        monkeypatch.undo()
        assert migrate_project(project_dir).written

    def test_demoted_group_without_text_named(self, tmp_path):
        segments = [_segment('c2', 1, '<p>Intro line.</p>\nFirst bare sentence.', '', 'translated'),
                    _segment('c2', 2, 'Second bare sentence.', '', 'translated')]
        project_dir = _legacy_project(tmp_path, {'c2': CHAPTERS['c2']}, segments)
        report = migrate_project(project_dir, dry_run=True)
        assert report.notes == ['c2_seg0001：2 個段落的狀態為 translated, translated；'
                                '合併後為 pending，譯文為空']
