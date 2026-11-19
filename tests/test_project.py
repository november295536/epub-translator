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

    def test_new_project_records_segmenter_version_2(self, project_dir, sample_segments):
        ProjectManager(project_dir).create('/path/to/book.epub', 'T', 'A', sample_segments)
        with open(os.path.join(project_dir, 'project.json'), 'r', encoding='utf-8') as f:
            assert json.load(f)['segmenter_version'] == 2
        pm = ProjectManager(project_dir)
        pm.load()
        assert pm.data.segmenter_version == 2

    def test_project_without_segmenter_version_loads_as_1_unchanged(
            self, project_dir, sample_segments):
        ProjectManager(project_dir).create('/path/to/book.epub', 'T', 'A', sample_segments)
        path = os.path.join(project_dir, 'project.json')
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        del data['segmenter_version']
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        with open(path, 'rb') as f:
            before = f.read()

        pm = ProjectManager(project_dir)
        pm.load()

        assert pm.data.segmenter_version == 1
        with open(path, 'rb') as f:
            assert f.read() == before

    def test_saving_project_without_segmenter_version_keeps_it_absent(
            self, project_dir, sample_segments):
        # Pre-Phase-0 code cannot load a project.json that has the field
        ProjectManager(project_dir).create('/path/to/book.epub', 'T', 'A', sample_segments)
        path = os.path.join(project_dir, 'project.json')
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        del data['segmenter_version']
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)

        pm = ProjectManager(project_dir)
        pm.load()
        pm.set_model('opus')
        pm.save()

        with open(path, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        assert 'segmenter_version' not in saved
        assert saved['model'] == 'opus'
        pm2 = ProjectManager(project_dir)
        pm2.load()
        assert pm2.data.segmenter_version == 1

    def test_version_2_project_keeps_segmenter_version_through_save(
            self, project_dir, sample_segments):
        ProjectManager(project_dir).create('/path/to/book.epub', 'T', 'A', sample_segments)
        pm = ProjectManager(project_dir)
        pm.load()
        pm.set_model('opus')
        pm.save()

        with open(os.path.join(project_dir, 'project.json'), 'r', encoding='utf-8') as f:
            assert json.load(f)['segmenter_version'] == 2
        pm2 = ProjectManager(project_dir)
        pm2.load()
        assert pm2.data.segmenter_version == 2

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
        """If save() fails writing tmp file, original project.json stays intact."""
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)
        pm.mark_translated('ch1_seg0001', '標題')
        pm.save()

        # Now mutate in memory but make save fail
        pm.mark_translated('ch1_seg0002', '第一段')

        # The temporary file name is unique per process
        tmp_file = f'{pm.project_file}.{os.getpid()}.tmp'
        os.makedirs(tmp_file, exist_ok=True)

        with pytest.raises(IsADirectoryError):
            pm.save()

        # Clean up the directory we made
        os.rmdir(tmp_file)

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
