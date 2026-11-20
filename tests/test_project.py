"""Tests for project manager module."""

import json
import os
import re
import threading
import pytest

from src.core.project import ProjectManager, ProjectData, SegmentState, SegmentStatus
from src.core.segmenter import Segment, SegmentType


@pytest.fixture
def sample_segments():
    """Create sample segments for testing."""
    return [
        Segment(id='ch1_seg0001', html='<h1>Title</h1>', text='Title',
                word_count=1, segment_type=SegmentType.TEXT),
        Segment(id='ch1_seg0002', html='<p>First paragraph.</p>',
                text='First paragraph.', word_count=2, segment_type=SegmentType.TEXT),
        Segment(id='ch1_seg0003', html='<p>Second paragraph.</p>',
                text='Second paragraph.', word_count=2, segment_type=SegmentType.TEXT),
    ]


class TestProjectManager:

    def test_create_project(self, project_dir, sample_segments):
        pm = ProjectManager(project_dir)
        data = pm.create(
            epub_path='/path/to/book.epub',
            title='Test Book',
            author='Test Author',
            segments=sample_segments,
        )

        assert data.title == 'Test Book'
        assert data.author == 'Test Author'
        assert len(data.segments) == 3
        assert data.segments[0].status == SegmentStatus.PENDING

    def test_project_file_created(self, project_dir, sample_segments):
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)

        assert os.path.isfile(os.path.join(project_dir, 'project.json'))

    def test_save_and_load(self, project_dir, sample_segments):
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test Book', 'Author', sample_segments)

        # Load in a new manager instance
        pm2 = ProjectManager(project_dir)
        data = pm2.load()

        assert data.title == 'Test Book'
        assert len(data.segments) == 3
        assert data.segments[0].segment_id == 'ch1_seg0001'

    def test_mark_translated(self, project_dir, sample_segments):
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)

        pm.mark_translated('ch1_seg0002', '第一段。')

        seg = [s for s in pm.data.segments if s.segment_id == 'ch1_seg0002'][0]
        assert seg.status == SegmentStatus.TRANSLATED
        assert seg.translated_text == '第一段。'
        assert seg.translated_at != ''

    def test_mark_translated_persists(self, project_dir, sample_segments):
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)
        pm.mark_translated('ch1_seg0002', '第一段。')
        pm.save()

        # Reload and verify
        pm2 = ProjectManager(project_dir)
        pm2.load()
        seg = [s for s in pm2.data.segments if s.segment_id == 'ch1_seg0002'][0]
        assert seg.status == SegmentStatus.TRANSLATED
        assert seg.translated_text == '第一段。'

    def test_mark_translated_does_not_save(self, project_dir, sample_segments):
        """mark_translated modifies memory but does not write to disk."""
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)

        # Read disk state before mutation
        with open(os.path.join(project_dir, 'project.json'), 'r', encoding='utf-8') as f:
            before = json.load(f)

        pm.mark_translated('ch1_seg0002', '第一段。')

        # Disk should be unchanged
        with open(os.path.join(project_dir, 'project.json'), 'r', encoding='utf-8') as f:
            after = json.load(f)
        assert before['segments'] == after['segments']

    def test_mutations_then_save_persists(self, project_dir, sample_segments):
        """Batch mutations followed by a single save() should persist all changes."""
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)

        pm.mark_translated('ch1_seg0001', '標題')
        pm.mark_translated('ch1_seg0002', '第一段。')
        pm.mark_translated('ch1_seg0003', '第二段。')
        pm.save()

        pm2 = ProjectManager(project_dir)
        pm2.load()
        translated = [s for s in pm2.data.segments if s.status == SegmentStatus.TRANSLATED]
        assert len(translated) == 3

    def test_mark_translated_invalid_id(self, project_dir, sample_segments):
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)

        with pytest.raises(ValueError, match="Segment not found"):
            pm.mark_translated('nonexistent', 'text')

    def test_get_pending_segments(self, project_dir, sample_segments):
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)

        pending = pm.get_pending_segments()
        assert len(pending) == 3

        pm.mark_translated('ch1_seg0001', '標題')
        pending = pm.get_pending_segments()
        assert len(pending) == 2

    def test_get_progress(self, project_dir, sample_segments):
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)

        progress = pm.get_progress()
        assert progress['total'] == 3
        assert progress['translated'] == 0
        assert progress['pending'] == 3
        assert progress['percent'] == 0.0

        pm.mark_translated('ch1_seg0001', '標題')
        progress = pm.get_progress()
        assert progress['translated'] == 1
        assert progress['pending'] == 2
        assert progress['percent'] == pytest.approx(33.3, abs=0.1)

    def test_resume_from_correct_position(self, project_dir, sample_segments):
        """After translating some segments, resume should skip completed ones."""
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)

        # Translate first segment
        pm.mark_translated('ch1_seg0001', '標題')
        pm.save()

        # Reload (simulating restart)
        pm2 = ProjectManager(project_dir)
        pm2.load()

        pending = pm2.get_pending_segments()
        assert len(pending) == 2
        assert pending[0].segment_id == 'ch1_seg0002'

    def test_exists(self, project_dir, sample_segments):
        pm = ProjectManager(project_dir)
        assert not pm.exists()

        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)
        assert pm.exists()

    def test_project_json_valid(self, project_dir, sample_segments):
        """The saved JSON should be valid and readable."""
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)

        with open(os.path.join(project_dir, 'project.json'), 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert 'segments' in data
        assert 'title' in data
        assert len(data['segments']) == 3

    def test_operations_without_load(self, project_dir):
        pm = ProjectManager(project_dir)
        with pytest.raises(RuntimeError, match="No project"):
            pm.get_pending_segments()
        with pytest.raises(RuntimeError, match="No project"):
            pm.get_progress()
        with pytest.raises(RuntimeError, match="No project"):
            pm.mark_translated('x', 'y')


class TestModelField:

    def test_default_model_is_sonnet(self, project_dir, sample_segments):
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)
        assert pm.data.model == 'sonnet'

    def test_model_persists_through_save_load(self, project_dir, sample_segments):
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)
        pm.set_model('opus')
        pm.save()

        pm2 = ProjectManager(project_dir)
        pm2.load()
        assert pm2.data.model == 'opus'

    def test_set_model(self, project_dir, sample_segments):
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)

        pm.set_model('haiku')
        assert pm.data.model == 'haiku'

        # Verify persisted to disk after save
        pm.save()
        with open(os.path.join(project_dir, 'project.json'), 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert data['model'] == 'haiku'

    def test_legacy_project_without_model(self, project_dir, sample_segments):
        """Old project.json files without model field should default to sonnet."""
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)

        # Manually remove model from JSON to simulate legacy project
        with open(os.path.join(project_dir, 'project.json'), 'r', encoding='utf-8') as f:
            data = json.load(f)
        del data['model']
        with open(os.path.join(project_dir, 'project.json'), 'w', encoding='utf-8') as f:
            json.dump(data, f)

        pm2 = ProjectManager(project_dir)
        pm2.load()
        assert pm2.data.model == 'sonnet'

    @staticmethod
    def _as_schema_1(project_dir, segmenter_version=None):
        """Rewrite a created project.json as a schema 1 project wrote it:
        no schema, no source hash, no block fields; segmenter_version only
        when given (Phase 0 projects have 2, older ones none)."""
        path = os.path.join(project_dir, 'project.json')
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        del data['schema'], data['source_sha256']
        for seg in data['segments']:
            for key in ('block_start', 'block_end', 'fingerprints'):
                seg.pop(key, None)
        if segmenter_version is not None:
            data['segmenter_version'] = segmenter_version
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        return path

    # Phase 2: schema 2 supersedes segmenter_version; these four tests replace
    # the Phase 0 segmenter_version tests with the same intent.
    def test_new_project_records_schema_2(self, project_dir, sample_segments):
        ProjectManager(project_dir).create('/path/to/book.epub', 'T', 'A', sample_segments,
                                           source_sha256='ab' * 32)
        with open(os.path.join(project_dir, 'project.json'), 'r', encoding='utf-8') as f:
            raw = json.load(f)
        assert raw['schema'] == 2
        assert raw['source_sha256'] == 'ab' * 32
        assert 'segmenter_version' not in raw
        pm = ProjectManager(project_dir)
        pm.load()
        assert pm.data.schema == 2
        assert pm.data.source_sha256 == 'ab' * 32

    def test_project_without_schema_loads_as_1_unchanged(
            self, project_dir, sample_segments):
        ProjectManager(project_dir).create('/path/to/book.epub', 'T', 'A', sample_segments)
        path = self._as_schema_1(project_dir)
        with open(path, 'rb') as f:
            before = f.read()

        pm = ProjectManager(project_dir)
        pm.load()

        assert pm.data.schema == 1
        assert pm.data.segmenter_version == 1
        with open(path, 'rb') as f:
            assert f.read() == before

    def test_saving_schema_1_project_adds_no_keys(self, project_dir, sample_segments):
        # Pre-Phase-0 code cannot load a project.json that has these fields
        ProjectManager(project_dir).create('/path/to/book.epub', 'T', 'A', sample_segments)
        path = self._as_schema_1(project_dir)
        with open(path, 'r', encoding='utf-8') as f:
            before = json.load(f)

        pm = ProjectManager(project_dir)
        pm.load()
        pm.set_model('opus')
        pm.save()

        with open(path, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        assert set(saved) == set(before)
        assert [set(s) for s in saved['segments']] == [set(s) for s in before['segments']]
        assert saved['model'] == 'opus'
        pm2 = ProjectManager(project_dir)
        pm2.load()
        assert (pm2.data.schema, pm2.data.segmenter_version) == (1, 1)

    def test_phase_0_project_keeps_segmenter_version_through_save(
            self, project_dir, sample_segments):
        ProjectManager(project_dir).create('/path/to/book.epub', 'T', 'A', sample_segments)
        self._as_schema_1(project_dir, segmenter_version=2)
        pm = ProjectManager(project_dir)
        pm.load()
        pm.set_model('opus')
        pm.save()

        with open(os.path.join(project_dir, 'project.json'), 'r', encoding='utf-8') as f:
            raw = json.load(f)
        assert raw['segmenter_version'] == 2
        assert 'schema' not in raw
        pm2 = ProjectManager(project_dir)
        pm2.load()
        assert (pm2.data.schema, pm2.data.segmenter_version) == (1, 2)

    def test_schema_2_project_keeps_block_fields_through_save(self, project_dir):
        from src.core.segmenter import Segment
        seg = Segment(id='ch1_seg0001', html='<p>A</p>', text='A', word_count=1,
                      segment_type=SegmentType.TEXT, block_start=0, block_end=1,
                      fingerprints=['0123456789abcdef'])
        ProjectManager(project_dir).create('/path/to/book.epub', 'T', 'A', [seg])
        pm = ProjectManager(project_dir)
        pm.load()
        pm.save()

        with open(os.path.join(project_dir, 'project.json'), 'r', encoding='utf-8') as f:
            raw = json.load(f)
        assert raw['schema'] == 2
        stored = raw['segments'][0]
        assert (stored['block_start'], stored['block_end'], stored['fingerprints']) == \
            (0, 1, ['0123456789abcdef'])
        assert pm.data.segments[0].block_start == 0

    def test_set_model_without_load(self, project_dir):
        pm = ProjectManager(project_dir)
        with pytest.raises(RuntimeError, match="No project"):
            pm.set_model('opus')

    def test_model_in_json(self, project_dir, sample_segments):
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)

        with open(os.path.join(project_dir, 'project.json'), 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert data['model'] == 'sonnet'


class TestConcurrentSave:

    def test_concurrent_save_no_data_loss(self, project_dir):
        """N threads each mark_translated + save() concurrently; all must persist."""
        n = 20
        segments = [
            Segment(id=f'ch1_seg{i:04d}', html=f'<p>Seg {i}</p>', text=f'Seg {i}',
                    word_count=2, segment_type=SegmentType.TEXT)
            for i in range(n)
        ]
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', segments)

        barrier = threading.Barrier(n)

        def worker(idx):
            barrier.wait()
            pm.mark_translated(f'ch1_seg{idx:04d}', f'翻譯 {idx}')
            pm.save()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Reload from disk and verify all translations persisted
        pm2 = ProjectManager(project_dir)
        pm2.load()
        translated = [s for s in pm2.data.segments if s.status == SegmentStatus.TRANSLATED]
        assert len(translated) == n


class TestAtomicSave:

    def test_atomic_save_writes_via_replace(self, project_dir, sample_segments):
        """save() should write to a tmp file then os.replace, not truncate directly."""
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)

        # Verify file is valid after save
        pm2 = ProjectManager(project_dir)
        pm2.load()
        assert pm2.data.title == 'Test'

    def test_original_intact_if_write_fails(self, project_dir, sample_segments):
        """If save() fails before the replace, original project.json stays intact
        and the temporary file is removed."""
        from unittest.mock import patch
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)
        pm.mark_translated('ch1_seg0001', '標題')
        pm.save()

        # Now mutate in memory but make save fail
        pm.mark_translated('ch1_seg0002', '第一段')

        # The temporary file name is unique per save: fail the replace instead
        with patch('src.core.project.os.replace', side_effect=OSError('disk full')):
            with pytest.raises(OSError):
                pm.save()
        assert not [n for n in os.listdir(project_dir) if n.endswith('.tmp')]

        # Original file should still be valid with only the first translation
        pm2 = ProjectManager(project_dir)
        pm2.load()
        seg1 = [s for s in pm2.data.segments if s.segment_id == 'ch1_seg0001'][0]
        assert seg1.status == SegmentStatus.TRANSLATED
        assert seg1.translated_text == '標題'
        # Second translation should NOT have been persisted
        seg2 = [s for s in pm2.data.segments if s.segment_id == 'ch1_seg0002'][0]
        assert seg2.status == SegmentStatus.PENDING


# --- Phase 1: FAILED status, issues, revision, compatibility, lock -------------

from src.core.project import Issue, ProjectLock, ProjectLockedError
from src.core.translator import TranslationOutcome
from src.llm import Usage
from tests.conftest import hold_lock_in_child, lock_free_for_other_process


def _ok(text='譯文', model='haiku', usage=None):
    return TranslationOutcome(text=text, status=SegmentStatus.TRANSLATED, issues=[],
                              model=model, usage=usage)


def _failed(detail='boom'):
    return TranslationOutcome(text=None, status=SegmentStatus.FAILED, model='haiku',
                              usage=None, issues=[Issue('backend', 'transient', detail, True)])


@pytest.fixture
def pm(project_dir, sample_segments):
    pm = ProjectManager(project_dir)
    pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)
    return pm


class TestApplyOutcome:

    def test_success_records_text_model_and_usage(self, pm):
        assert pm.apply_outcome('ch1_seg0001', _ok(usage=Usage(10, 20))) is True
        seg = pm.data.segments[0]
        assert seg.status == SegmentStatus.TRANSLATED
        assert seg.translated_text == '譯文'
        assert seg.model == 'haiku'
        assert seg.usage == {'input_tokens': 10, 'output_tokens': 20}
        assert re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d', seg.translated_at)
        assert seg.issues == []

    def test_failure_of_pending_segment_is_failed_with_issue(self, pm):
        assert pm.apply_outcome('ch1_seg0001', _failed('timed out')) is True
        seg = pm.data.segments[0]
        assert seg.status == SegmentStatus.FAILED
        assert seg.translated_text == ''
        assert seg.issues == [Issue('backend', 'transient', 'timed out', True)]

    @pytest.mark.parametrize('status', [SegmentStatus.TRANSLATED, SegmentStatus.REVIEWED])
    def test_failed_retranslation_keeps_text_and_status(self, pm, status):
        seg = pm.data.segments[0]
        pm.apply_outcome('ch1_seg0001', _ok('舊譯文'))
        seg.status = status
        pm.apply_outcome('ch1_seg0001', _failed())
        assert seg.status == status
        assert seg.translated_text == '舊譯文'
        assert seg.model == 'haiku'
        assert [i.code for i in seg.issues] == ['transient']

    def test_success_after_failure_clears_issues(self, pm):
        pm.apply_outcome('ch1_seg0001', _failed())
        pm.apply_outcome('ch1_seg0001', _ok())
        seg = pm.data.segments[0]
        assert seg.status == SegmentStatus.TRANSLATED
        assert seg.issues == []

    def test_result_for_older_revision_is_dropped(self, pm):
        seg = pm.data.segments[0]
        dispatched = seg.revision
        pm.update_translation('ch1_seg0001', '人工譯文')  # edited mid-flight
        assert pm.apply_outcome('ch1_seg0001', _ok('機器譯文'), revision=dispatched) is False
        assert seg.translated_text == '人工譯文'
        assert seg.status == SegmentStatus.PENDING

    def test_failure_for_older_revision_is_dropped(self, pm):
        seg = pm.data.segments[0]
        dispatched = seg.revision
        pm.mark_reviewed('ch1_seg0001', '人工譯文')
        assert pm.apply_outcome('ch1_seg0001', _failed(), revision=dispatched) is False
        assert seg.status == SegmentStatus.REVIEWED
        assert seg.issues == []

    def test_result_for_current_revision_is_applied(self, pm):
        pm.update_translation('ch1_seg0001', '人工譯文')
        seg = pm.data.segments[0]
        assert pm.apply_outcome('ch1_seg0001', _ok(), revision=seg.revision) is True
        assert seg.translated_text == '譯文'

    def test_manual_edits_increment_revision(self, pm):
        seg = pm.data.segments[0]
        assert seg.revision == 0
        pm.update_translation('ch1_seg0001', 'a')
        pm.update_translation('ch1_seg0001', 'b')
        assert seg.revision == 2
        pm.mark_reviewed('ch1_seg0001')
        pm.mark_reviewed('ch1_seg0001', 'c')
        assert seg.revision == 4
        pm.apply_outcome('ch1_seg0001', _ok())
        assert seg.revision == 4

    def test_unknown_segment(self, pm):
        with pytest.raises(ValueError, match='^Segment not found: nope$'):
            pm.apply_outcome('nope', _ok())

    def test_without_load(self, project_dir):
        with pytest.raises(RuntimeError, match='^No project loaded.$'):
            ProjectManager(project_dir).apply_outcome('x', _ok())
        with pytest.raises(RuntimeError, match='^No project loaded.$'):
            ProjectManager(project_dir).get_pending_segments(include_failed=True)


class TestFailedStatus:

    def test_failed_not_pending_unless_asked(self, pm):
        pm.apply_outcome('ch1_seg0002', _failed())
        ids = [s.segment_id for s in pm.get_pending_segments()]
        assert ids == ['ch1_seg0001', 'ch1_seg0003']
        ids = [s.segment_id for s in pm.get_pending_segments(include_failed=True)]
        assert ids == ['ch1_seg0001', 'ch1_seg0002', 'ch1_seg0003']

    def test_progress_and_chapters_count_failed(self, pm):
        pm.apply_outcome('ch1_seg0002', _failed())
        pm.apply_outcome('ch1_seg0001', _ok())
        progress = pm.get_progress()
        assert progress['failed'] == 1
        assert progress['pending'] == 1
        assert progress['translated'] == 1
        assert progress['percent'] == 33.3
        assert pm.get_chapters() == [{'chapter_id': 'ch1', 'total': 3, 'translated': 1,
                                      'reviewed': 0, 'failed': 1}]

    def test_chapters_count_each_failed_and_not_pending(self, pm):
        pm.apply_outcome('ch1_seg0001', _failed())
        pm.apply_outcome('ch1_seg0002', _failed())
        assert pm.get_chapters()[0]['failed'] == 2
        pm.apply_outcome('ch1_seg0001', _ok())
        pm.apply_outcome('ch1_seg0002', _ok())
        assert pm.get_chapters()[0]['failed'] == 0  # the pending one is not failed

    def test_failed_status_and_issues_persist(self, pm, project_dir):
        pm.apply_outcome('ch1_seg0002', _failed('timed out'))
        pm.save()
        raw = json.load(open(os.path.join(project_dir, 'project.json')))
        assert raw['segments'][1]['status'] == 'failed'
        assert raw['segments'][1]['issues'] == [
            {'kind': 'backend', 'code': 'transient', 'detail': 'timed out', 'blocking': True}]
        pm2 = ProjectManager(project_dir)
        pm2.load()
        seg = pm2.data.segments[1]
        assert seg.status == SegmentStatus.FAILED
        assert seg.issues == [Issue('backend', 'transient', 'timed out', True)]


# A project.json as written by the code at tag v1.0.0 (no Phase 0/1 fields)
_V1_PROJECT = {
    'epub_path': '/books/book.epub',
    'project_dir': 'projects/book',
    'title': 'Invented Title',
    'author': 'Invented Author',
    'created_at': '2026-01-01 10:00:00',
    'updated_at': '2026-01-02 10:00:00',
    'model': 'sonnet',
    'segments': [
        {'segment_id': 'c1_seg0001', 'chapter_id': 'c1', 'html': '<p>One.</p>',
         'text': 'One.', 'word_count': 1, 'segment_type': 'text',
         'status': 'reviewed', 'translated_text': '一。',
         'translated_at': '2026-01-01 11:00:00'},
        {'segment_id': 'c1_seg0002', 'chapter_id': 'c1', 'html': '<p>Two.</p>',
         'text': 'Two.', 'word_count': 1, 'segment_type': 'text',
         'status': 'translated', 'translated_text': '二。',
         'translated_at': '2026-01-01 11:00:00'},
        {'segment_id': 'c1_seg0003', 'chapter_id': 'c1', 'html': '<p>Three.</p>',
         'text': 'Three.', 'word_count': 1, 'segment_type': 'text',
         'status': 'pending', 'translated_text': '', 'translated_at': ''},
    ],
}


class TestOldProjectCompatibility:

    def _write(self, project_dir):
        path = os.path.join(project_dir, 'project.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(_V1_PROJECT, f, ensure_ascii=False, indent=2)
        return path

    def test_round_trip_adds_no_keys(self, project_dir):
        path = self._write(project_dir)
        pm = ProjectManager(project_dir)
        pm.load()
        pm.save()
        saved = json.load(open(path, encoding='utf-8'))
        expected = dict(_V1_PROJECT, updated_at=saved['updated_at'])
        assert saved == expected

    def test_load_does_not_write(self, project_dir):
        path = self._write(project_dir)
        before = open(path, 'rb').read()
        os.utime(path, (1_000_000, 1_000_000))
        pm = ProjectManager(project_dir)
        pm.load()
        assert open(path, 'rb').read() == before
        assert os.stat(path).st_mtime == 1_000_000
        assert not os.path.exists(os.path.join(project_dir, ProjectLock.FILE))

    def test_new_fields_written_only_when_not_default(self, project_dir):
        path = self._write(project_dir)
        pm = ProjectManager(project_dir)
        pm.load()
        pm.apply_outcome('c1_seg0003', _ok(model='haiku'))
        pm.update_translation('c1_seg0001', '一！')
        pm.save()
        segs = json.load(open(path, encoding='utf-8'))['segments']
        base = set(_V1_PROJECT['segments'][0])
        assert set(segs[0]) == base | {'revision'}
        assert set(segs[1]) == base
        assert set(segs[2]) == base | {'model'}
        assert segs[0]['revision'] == 1


class TestProjectLock:

    def test_second_process_cannot_take_lock(self, project_dir):
        child = hold_lock_in_child(project_dir)
        try:
            with pytest.raises(ProjectLockedError, match=str(child.pid)):
                ProjectLock(project_dir).acquire()
        finally:
            child.stdin.close()
            child.wait(timeout=10)
        # Released when the holder exits normally
        with ProjectLock(project_dir):
            pass

    def test_save_refused_while_other_process_holds_lock(self, pm, project_dir):
        child = hold_lock_in_child(project_dir)
        try:
            pm.mark_translated('ch1_seg0001', 'x')
            with pytest.raises(ProjectLockedError):
                pm.save()
        finally:
            child.stdin.close()
            child.wait(timeout=10)
        pm2 = ProjectManager(project_dir)
        pm2.load()
        assert pm2.data.segments[0].status == SegmentStatus.PENDING

    def test_lock_of_killed_process_is_recoverable(self, project_dir):
        child = hold_lock_in_child(project_dir)
        child.kill()
        child.wait(timeout=10)
        assert os.path.exists(os.path.join(project_dir, ProjectLock.FILE))
        with ProjectLock(project_dir):
            assert not lock_free_for_other_process(project_dir)
        assert lock_free_for_other_process(project_dir)

    def test_same_process_shares_lock(self, project_dir):
        outer = ProjectLock(project_dir)
        outer.acquire()
        try:
            with ProjectLock(project_dir):
                pass
            # The inner release does not free the lock held by `outer`
            assert not lock_free_for_other_process(project_dir)
        finally:
            outer.release()
        assert lock_free_for_other_process(project_dir)

    def test_acquire_and_release_are_idempotent(self, project_dir):
        lock = ProjectLock(project_dir)
        lock.acquire()
        lock.acquire()
        lock.release()
        assert lock_free_for_other_process(project_dir)
        lock.release()
        assert lock_free_for_other_process(project_dir)

    def test_save_does_not_hold_lock_afterwards(self, pm, project_dir):
        pm.save()
        assert lock_free_for_other_process(project_dir)

    def test_lock_file_records_holder_pid(self, project_dir):
        path = os.path.join(project_dir, 'project.lock')
        with open(path, 'w') as f:
            f.write('stale content longer than a pid')
        old_umask = os.umask(0o022)
        try:
            with ProjectLock(project_dir):
                content = open(path).read()
        finally:
            os.umask(old_umask)
        assert content == str(os.getpid())

    def test_new_lock_file_mode(self, tmp_path):
        old_umask = os.umask(0)
        try:
            with ProjectLock(str(tmp_path)):
                pass
        finally:
            os.umask(old_umask)
        assert os.stat(tmp_path / 'project.lock').st_mode & 0o777 == 0o644

    def test_error_message_names_holder_and_file(self, project_dir):
        child = hold_lock_in_child(project_dir)
        try:
            with pytest.raises(ProjectLockedError) as info:
                ProjectLock(project_dir).acquire()
        finally:
            child.stdin.close()
            child.wait(timeout=10)
        path = os.path.realpath(os.path.join(project_dir, 'project.lock'))
        assert str(info.value) == f'專案正由另一個程序使用（PID {child.pid}）：{path}'

    def test_release_closes_only_the_lock_file(self, project_dir):
        # Keep descriptor 0 open, so the lock file never gets number 0
        try:
            os.fstat(0)
        except OSError:
            os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
        with ProjectLock(project_dir):
            pass
        os.fstat(0)  # still open
        assert lock_free_for_other_process(project_dir)


class TestSaveFormat:

    def test_utf8_and_two_space_indent(self, pm, project_dir):
        pm.mark_translated('ch1_seg0001', '標題')
        pm.save()
        text = open(os.path.join(project_dir, 'project.json'), encoding='utf-8').read()
        assert '"translated_text": "標題"' in text
        assert '\n  "epub_path"' in text


# --- Review round 1 ---------------------------------------------------------------

class TestSharedProjectMutex:
    """Item 1: managers of one project share one mutex; saves never collide."""

    def test_managers_of_one_project_share_the_mutex(self, pm, project_dir):
        from src.core.project import project_mutex
        other = ProjectManager(os.path.join(project_dir, '.', ''))
        assert other.mutex is pm.mutex is project_mutex(project_dir)
        assert ProjectManager(project_dir + '-other').mutex is not pm.mutex

    def test_concurrent_saves_from_several_managers(self, pm, project_dir):
        managers = []
        for _ in range(4):
            m = ProjectManager(project_dir)
            m.load()
            managers.append(m)
        barrier = threading.Barrier(len(managers) * 2)
        errors = []

        def run(m):
            barrier.wait()
            try:
                for _ in range(30):
                    m.save()
            except Exception as e:  # noqa: BLE001 - reported below
                errors.append(e)

        threads = [threading.Thread(target=run, args=(m,))
                   for m in managers for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        with open(pm.project_file, encoding='utf-8') as f:
            assert json.load(f)['title'] == 'Test'
        assert not [n for n in os.listdir(project_dir) if n.endswith('.tmp')]

    def test_save_waits_for_the_project_mutex(self, pm, project_dir):
        other = ProjectManager(project_dir)
        other.load()
        other.mark_translated('ch1_seg0001', '後來')
        with pm.mutex:
            t = threading.Thread(target=other.save)
            t.start()
            t.join(timeout=0.3)
            assert t.is_alive()  # blocked while the mutex is held
        t.join(timeout=10)
        assert not t.is_alive()


class _EditWhileIterated:
    """Issues list whose iteration (inside apply_outcome, after the revision
    check) runs `action` in another thread and waits up to 0.5 s for it."""

    def __init__(self, action):
        self.action = action
        self.finished_during_apply = None

    def __iter__(self):
        t = threading.Thread(target=self.action)
        t.start()
        t.join(timeout=0.5)
        self.finished_during_apply = not t.is_alive()
        self.thread = t
        return iter([])


class TestApplyOutcomeAtomic:
    """Item 3: the revision check and the write are one step."""

    def test_edit_cannot_slip_between_check_and_write(self, pm):
        rev = pm._find('ch1_seg0002').revision
        issues = _EditWhileIterated(lambda: pm.update_translation('ch1_seg0002', '人工'))
        outcome = TranslationOutcome(text='機器', status=SegmentStatus.TRANSLATED,
                                     issues=issues, model='haiku', usage=None)
        pm.apply_outcome('ch1_seg0002', outcome, revision=rev)
        issues.thread.join(timeout=10)
        assert issues.finished_during_apply is False
        seg = pm._find('ch1_seg0002')
        assert (seg.translated_text, seg.revision) == ('人工', rev + 1)

    def test_save_does_not_see_half_applied_outcome(self, pm, project_dir):
        issues = _EditWhileIterated(pm.save)
        outcome = TranslationOutcome(text='機器', status=SegmentStatus.TRANSLATED,
                                     issues=issues, model='haiku', usage=None)
        pm.apply_outcome('ch1_seg0002', outcome)
        issues.thread.join(timeout=10)
        assert issues.finished_during_apply is False
        other = ProjectManager(project_dir)
        other.load()
        assert other._find('ch1_seg0002').translated_text == '機器'

    def test_mark_reviewed_waits_for_the_mutex(self, pm):
        with pm.mutex:
            t = threading.Thread(target=pm.mark_reviewed, args=('ch1_seg0001',))
            t.start()
            t.join(timeout=0.3)
            assert t.is_alive()
        t.join(timeout=10)
        assert pm._find('ch1_seg0001').status == SegmentStatus.REVIEWED


class TestManualEditClearsFailure:
    """Item 17: a manual edit clears issues and takes a segment out of FAILED."""

    def test_edit_of_failed_segment(self, pm):
        pm.apply_outcome('ch1_seg0002', _failed())
        pm.update_translation('ch1_seg0002', '人工')
        seg = pm._find('ch1_seg0002')
        assert (seg.status, seg.issues) == (SegmentStatus.TRANSLATED, [])

    def test_edit_keeps_other_statuses(self, pm):
        pm.update_translation('ch1_seg0001', '人工')
        assert pm._find('ch1_seg0001').status == SegmentStatus.PENDING
        pm.apply_outcome('ch1_seg0002', _ok())
        pm.apply_outcome('ch1_seg0002', _failed())  # issue attached, still translated
        pm.update_translation('ch1_seg0002', '人工')
        seg = pm._find('ch1_seg0002')
        assert (seg.status, seg.issues) == (SegmentStatus.TRANSLATED, [])

    def test_approve_clears_issues(self, pm):
        pm.apply_outcome('ch1_seg0002', _failed())
        pm.mark_reviewed('ch1_seg0002', text='人工')
        seg = pm._find('ch1_seg0002')
        assert (seg.status, seg.issues) == (SegmentStatus.REVIEWED, [])


class TestLockAcquireFailure:
    """Item 5: a failure after flock() closes the descriptor."""

    def test_pid_write_failure_releases_lock(self, project_dir):
        from unittest.mock import patch
        os.makedirs(project_dir, exist_ok=True)
        with patch('src.core.project.os.write', side_effect=OSError('no space')):
            with pytest.raises(OSError):
                ProjectLock(project_dir).acquire()
        assert lock_free_for_other_process(project_dir)
        lock = ProjectLock(project_dir)
        lock.acquire()  # a leaked descriptor would make this fail in-process
        lock.release()


class TestRound1Exact:
    """Exact values of the round 1 project code (mutation survivors)."""

    def test_project_data_model_default_is_empty(self):
        assert ProjectData(epub_path='', project_dir='').model == ''

    def test_messages_without_data(self, project_dir):
        pm = ProjectManager(project_dir)
        with pytest.raises(RuntimeError) as info:
            pm.to_json()
        assert str(info.value) == 'No project data to save.'
        with pytest.raises(RuntimeError) as info:
            pm.mark_translated('x', 'y')
        assert str(info.value) == 'No project loaded.'

    def test_temporary_file_name(self, pm, project_dir):
        from unittest.mock import patch
        names = []
        real_replace = os.replace

        def replace(src, dst):
            names.append(os.path.basename(src))
            return real_replace(src, dst)

        with patch('src.core.project.os.replace', side_effect=replace):
            pm.save()
        assert len(names) == 1
        assert re.fullmatch(r'project\.json\.[^.]+\.tmp', names[0]), names[0]

    def test_file_mode_kept_or_0644(self, pm, project_dir):
        import stat
        path = pm.project_file
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o644  # created by create()
        os.chmod(path, 0o640)
        pm.save()
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o640

    def test_mark_translated_sets_time(self, pm):
        pm.mark_translated('ch1_seg0001', '標題')
        assert re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d',
                            pm._find('ch1_seg0001').translated_at)

    def test_mark_reviewed_with_text(self, pm):
        pm.mark_reviewed('ch1_seg0001', text='人工')
        assert pm._find('ch1_seg0001').translated_text == '人工'


# --- Review round 2 ---------------------------------------------------------------

from unittest.mock import patch as _patch


def _closed(fd):
    try:
        os.fstat(fd)
    except OSError:
        return True
    return False


class TestRound2DescriptorLeaks:
    """Item 19: no descriptor is left open on an error path."""

    def test_save_closes_temporary_descriptor_when_fchmod_fails(self, project_dir):
        import tempfile
        pm = ProjectManager(project_dir)
        pm.create('/b.epub', 'T', 'A', [])
        made = []
        real = tempfile.mkstemp

        def mkstemp(**kwargs):
            fd, path = real(**kwargs)
            made.append(fd)
            return fd, path

        with _patch('src.core.project.tempfile.mkstemp', side_effect=mkstemp), \
                _patch('src.core.project.os.fchmod', side_effect=OSError('denied')):
            with pytest.raises(OSError):
                pm.save()
        assert made and _closed(made[0])
        assert [f for f in os.listdir(project_dir) if f.endswith('.tmp')] == []

    def test_save_still_closes_once_on_write_failure(self, project_dir):
        pm = ProjectManager(project_dir)
        pm.create('/b.epub', 'T', 'A', [])
        with _patch('src.core.project.os.replace', side_effect=OSError('denied')):
            with pytest.raises(OSError, match='denied'):
                pm.save()
        pm.save()  # nothing left in a bad state

    def test_holder_pid_read_failure_closes_descriptor(self, project_dir):
        os.makedirs(project_dir, exist_ok=True)
        child = hold_lock_in_child(project_dir)
        opened = []
        real_open = os.open

        def record(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            opened.append(fd)
            return fd

        try:
            with _patch('src.core.project.os.open', side_effect=record), \
                    _patch('src.core.project.os.read', side_effect=OSError('io')):
                with pytest.raises(OSError, match='io'):
                    ProjectLock(project_dir).acquire()
        finally:
            child.stdin.close()
            child.wait(timeout=10)
        assert opened and _closed(opened[0])

    def test_undecodable_holder(self, project_dir):
        os.makedirs(project_dir, exist_ok=True)
        child = hold_lock_in_child(project_dir)
        try:
            with open(os.path.join(project_dir, 'project.lock'), 'wb') as f:
                f.write(b'\xff12')
            with pytest.raises(ProjectLockedError) as info:
                ProjectLock(project_dir).acquire()
        finally:
            child.stdin.close()
            child.wait(timeout=10)
        assert '（PID �12）' in str(info.value)


class TestPhase2ProjectFields:
    """Mutation survivors of the Phase 2 project fields."""

    def test_sha256_file_of_a_large_file(self, tmp_path):
        import hashlib
        from src.core.project import sha256_file
        data = os.urandom(3 * (1 << 20) + 17)
        path = tmp_path / 'big.bin'
        path.write_bytes(data)
        assert sha256_file(str(path)) == hashlib.sha256(data).hexdigest()

    def test_schema_1_project_has_no_source_hash(self, project_dir):
        with open(os.path.join(project_dir, 'project.json'), 'w', encoding='utf-8') as f:
            json.dump({'epub_path': '/b.epub', 'project_dir': project_dir, 'segments': []}, f)
        pm = ProjectManager(project_dir)
        pm.load()
        assert (pm.data.schema, pm.data.source_sha256) == (1, '')

    def test_create_without_hash_or_ranges(self, project_dir, sample_segments):
        ProjectManager(project_dir).create('/path/to/book.epub', 'T', 'A', sample_segments)
        with open(os.path.join(project_dir, 'project.json'), encoding='utf-8') as f:
            raw = json.load(f)
        assert raw['source_sha256'] == ''
        assert all(not ({'block_start', 'block_end', 'fingerprints'} & set(s))
                   for s in raw['segments'])
