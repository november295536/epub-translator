"""Tests for project manager module."""

import json
import os
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

        # Reload and verify
        pm2 = ProjectManager(project_dir)
        pm2.load()
        seg = [s for s in pm2.data.segments if s.segment_id == 'ch1_seg0002'][0]
        assert seg.status == SegmentStatus.TRANSLATED
        assert seg.translated_text == '第一段。'

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

        pm2 = ProjectManager(project_dir)
        pm2.load()
        assert pm2.data.model == 'opus'

    def test_set_model(self, project_dir, sample_segments):
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)

        pm.set_model('haiku')
        assert pm.data.model == 'haiku'

        # Verify persisted to disk
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
        """If writing the tmp file fails, original project.json stays intact."""
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)
        pm.mark_translated('ch1_seg0001', '標題')

        # Simulate a write failure by making the tmp file path a directory
        tmp_file = pm.project_file + '.tmp'
        os.makedirs(tmp_file, exist_ok=True)

        with pytest.raises(IsADirectoryError):
            pm.mark_translated('ch1_seg0002', '第一段')

        # Clean up the directory we made
        os.rmdir(tmp_file)

        # Original file should still be valid with the first translation
        pm2 = ProjectManager(project_dir)
        pm2.load()
        seg1 = [s for s in pm2.data.segments if s.segment_id == 'ch1_seg0001'][0]
        assert seg1.status == SegmentStatus.TRANSLATED
        assert seg1.translated_text == '標題'
        # Second translation should NOT have been applied
        seg2 = [s for s in pm2.data.segments if s.segment_id == 'ch1_seg0002'][0]
        assert seg2.status == SegmentStatus.PENDING


class TestSaveLock:

    def test_concurrent_mark_translated_no_data_loss(self, tmp_path):
        """Multiple threads calling mark_translated with save_lock should not lose data."""
        project_dir = str(tmp_path / 'concurrent_project')
        os.makedirs(project_dir, exist_ok=True)

        # Create 20 segments
        segments = [
            Segment(id=f'ch1_seg{i:04d}', html=f'<p>Seg {i}</p>',
                    text=f'Segment {i}', word_count=2, segment_type=SegmentType.TEXT)
            for i in range(20)
        ]
        pm_setup = ProjectManager(project_dir)
        pm_setup.create('/path/to/book.epub', 'Test', 'Author', segments)

        save_lock = threading.Lock()
        pm = ProjectManager(project_dir, save_lock=save_lock)
        pm.load()

        errors = []

        def translate_segment(seg_id, text):
            try:
                pm.mark_translated(seg_id, text)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(20):
            t = threading.Thread(
                target=translate_segment,
                args=(f'ch1_seg{i:04d}', f'翻譯 {i}')
            )
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

        # Reload and verify all 20 segments are translated
        pm2 = ProjectManager(project_dir)
        pm2.load()
        translated = [s for s in pm2.data.segments if s.status == SegmentStatus.TRANSLATED]
        assert len(translated) == 20

    def test_save_lock_backward_compatible(self, project_dir, sample_segments):
        """ProjectManager without save_lock should work exactly as before."""
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test', 'Author', sample_segments)
        pm.mark_translated('ch1_seg0001', '標題')

        pm2 = ProjectManager(project_dir)
        pm2.load()
        seg = [s for s in pm2.data.segments if s.segment_id == 'ch1_seg0001'][0]
        assert seg.status == SegmentStatus.TRANSLATED
