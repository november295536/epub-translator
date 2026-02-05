"""Tests for Flask web review server."""

import json
import os
import threading
import pytest
from unittest.mock import patch, MagicMock

from src.web.server import create_app
from src.core.project import ProjectManager
from src.core.segmenter import Segment
from src.core.translator import RateLimitError
from src.web.tasks import TaskManager, TranslationTask


PROJECT_NAME = 'test_project'


@pytest.fixture
def project_with_translations(tmp_path):
    """Create a project with some translated segments for testing."""
    # Create projects directory structure: tmp_path/test_project/
    projects_dir = str(tmp_path)
    project_dir = os.path.join(projects_dir, PROJECT_NAME)
    os.makedirs(project_dir, exist_ok=True)

    segments = [
        Segment(id='ch1_seg0001', html='<h1>Title</h1>', text='Title',
                word_count=1, segment_type='text'),
        Segment(id='ch1_seg0002', html='<p>First paragraph.</p>',
                text='First paragraph.', word_count=2, segment_type='text'),
        Segment(id='ch1_seg0003', html='<p>Second paragraph.</p>',
                text='Second paragraph.', word_count=2, segment_type='text'),
        Segment(id='ch2_seg0001', html='<h2>Chapter 2</h2>',
                text='Chapter 2', word_count=2, segment_type='text'),
        Segment(id='ch2_seg0002', html='<p>Chapter 2 content.</p>',
                text='Chapter 2 content.', word_count=3, segment_type='text'),
    ]

    pm = ProjectManager(project_dir)
    pm.create('/path/to/book.epub', 'Test Book', 'Author', segments)

    # Translate some segments
    pm.mark_translated('ch1_seg0001', '標題')
    pm.mark_translated('ch1_seg0002', '第一段。')

    return projects_dir


@pytest.fixture
def client(project_with_translations):
    app = create_app(projects_dir=project_with_translations)
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


class TestWebServer:

    def test_index_page(self, client):
        response = client.get(f'/project/{PROJECT_NAME}/review')
        assert response.status_code == 200
        assert 'Test Book' in response.data.decode()

    def test_chapter_view(self, client):
        response = client.get(f'/project/{PROJECT_NAME}/review/chapter/ch1')
        assert response.status_code == 200
        data = response.data.decode()
        assert 'ch1_seg0001' in data
        assert '標題' in data

    def test_update_segment(self, client):
        response = client.post(
            f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0002',
            data=json.dumps({'text': '更新的翻譯'}),
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'

    def test_update_segment_missing_text(self, client):
        response = client.post(
            f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0002',
            data=json.dumps({}),
            content_type='application/json',
        )
        assert response.status_code == 400

    def test_update_segment_not_found(self, client):
        response = client.post(
            f'/api/projects/{PROJECT_NAME}/segments/nonexistent',
            data=json.dumps({'text': 'test'}),
            content_type='application/json',
        )
        assert response.status_code == 404

    def test_approve_segment(self, client):
        response = client.post(
            f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0002/approve',
            data=json.dumps({'text': '審閱後的翻譯'}),
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'

    def test_approve_segment_not_found(self, client):
        response = client.post(
            f'/api/projects/{PROJECT_NAME}/segments/nonexistent/approve',
            data=json.dumps({}),
            content_type='application/json',
        )
        assert response.status_code == 404

    def test_api_progress(self, client):
        response = client.get(f'/api/projects/{PROJECT_NAME}/progress')
        assert response.status_code == 200
        data = response.get_json()
        assert 'total' in data
        assert 'translated' in data
        assert data['total'] == 5

    def test_segment_persists_after_update(self, client, project_with_translations):
        client.post(
            f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003',
            data=json.dumps({'text': '永久翻譯'}),
            content_type='application/json',
        )

        # Verify by loading the project directly
        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        pm = ProjectManager(project_dir)
        pm.load()
        seg = [s for s in pm.data.segments if s.segment_id == 'ch1_seg0003'][0]
        assert seg.translated_text == '永久翻譯'

    def test_approve_persists_status(self, client, project_with_translations):
        client.post(
            f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0002/approve',
            data=json.dumps({}),
            content_type='application/json',
        )

        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        pm = ProjectManager(project_dir)
        pm.load()
        seg = [s for s in pm.data.segments if s.segment_id == 'ch1_seg0002'][0]
        assert seg.status == 'reviewed'


class TestDeleteProjectAPI:

    def test_delete_project(self, client, project_with_translations):
        response = client.delete(f'/api/projects/{PROJECT_NAME}')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'

        # Verify project directory is removed
        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        assert not os.path.exists(project_dir)

    def test_delete_nonexistent_project(self, client):
        response = client.delete('/api/projects/nonexistent')
        assert response.status_code == 404


class TestGlossaryAPI:

    @pytest.fixture(autouse=True)
    def setup_glossary(self, client, project_with_translations):
        """Add some glossary terms for testing."""
        self.client = client
        self.projects_dir = project_with_translations
        for term, translation in [
            ('observability', '可觀測性'),
            ('telemetry', '遙測'),
            ('tracing', '追蹤'),
        ]:
            client.post(
                f'/api/projects/{PROJECT_NAME}/glossary',
                data=json.dumps({'term': term, 'translation': translation}),
                content_type='application/json',
            )

    def test_list_glossary_terms(self):
        response = self.client.get(f'/api/projects/{PROJECT_NAME}/glossary')
        assert response.status_code == 200
        terms = response.get_json()
        assert len(terms) == 3

    def test_batch_delete_terms(self):
        response = self.client.delete(
            f'/api/projects/{PROJECT_NAME}/glossary/batch',
            data=json.dumps({'terms': ['observability', 'tracing']}),
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['deleted'] == 2

        # Verify only telemetry remains
        response = self.client.get(f'/api/projects/{PROJECT_NAME}/glossary')
        terms = response.get_json()
        assert len(terms) == 1
        assert terms[0]['term'] == 'telemetry'

    def test_batch_delete_nonexistent_terms(self):
        response = self.client.delete(
            f'/api/projects/{PROJECT_NAME}/glossary/batch',
            data=json.dumps({'terms': ['nonexistent1', 'nonexistent2']}),
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['deleted'] == 0

    def test_batch_delete_mixed(self):
        response = self.client.delete(
            f'/api/projects/{PROJECT_NAME}/glossary/batch',
            data=json.dumps({'terms': ['observability', 'nonexistent']}),
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['deleted'] == 1

    def test_batch_delete_missing_body(self):
        response = self.client.delete(
            f'/api/projects/{PROJECT_NAME}/glossary/batch',
            data=json.dumps({}),
            content_type='application/json',
        )
        assert response.status_code == 400

    def test_batch_delete_all(self):
        response = self.client.delete(
            f'/api/projects/{PROJECT_NAME}/glossary/batch',
            data=json.dumps({'terms': ['observability', 'telemetry', 'tracing']}),
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['deleted'] == 3

        response = self.client.get(f'/api/projects/{PROJECT_NAME}/glossary')
        assert len(response.get_json()) == 0


class TestRateLimitPause:

    def test_rate_limit_pauses_task(self, project_with_translations):
        """When RateLimitError is raised, the task should pause and emit a rate_limit event."""
        tm = TaskManager()

        task = TranslationTask(
            task_id='test_rl',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='running',
            total=3,
        )

        def fake_translate(*args, **kwargs):
            raise RateLimitError("You've hit your limit · resets 8pm (Asia/Taipei)")

        with patch.object(TaskManager, '__new__', return_value=tm):
            with patch('src.web.tasks.TranslationEngine') as MockEngine:
                MockEngine.return_value.translate.side_effect = fake_translate

                # Run _run_translation in a thread so we can inspect state while it blocks
                thread = threading.Thread(target=tm._run_translation, args=(task,))
                thread.start()

                # Wait for task to pause (the thread will block on pause_event.wait)
                thread.join(timeout=5)
                assert task.state == 'paused'
                assert not task.pause_event.is_set()

                # Drain queue and check for rate_limit event
                events = []
                while not task.progress_queue.empty():
                    events.append(task.progress_queue.get_nowait())

                rate_limit_events = [e for e in events if e['event'] == 'rate_limit']
                assert len(rate_limit_events) == 1
                assert "You've hit your limit" in rate_limit_events[0]['data']['error']

                # Stop the task so the thread exits
                task.state = 'stopped'
                task.pause_event.set()
                thread.join(timeout=5)

    def test_rate_limit_resume_retries_segment(self, project_with_translations):
        """After resuming from rate limit, the same segment should be retried."""
        tm = TaskManager()

        task = TranslationTask(
            task_id='test_rl_resume',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='running',
            total=3,
        )

        call_count = 0

        def fake_translate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RateLimitError("You've hit your limit · resets 8pm (Asia/Taipei)")
            return '翻譯結果'

        with patch.object(TaskManager, '__new__', return_value=tm):
            with patch('src.web.tasks.TranslationEngine') as MockEngine:
                MockEngine.return_value.translate.side_effect = fake_translate

                thread = threading.Thread(target=tm._run_translation, args=(task,))
                thread.start()

                # Wait for task to pause
                thread.join(timeout=5)
                assert task.state == 'paused'

                # Resume — should retry the same segment and succeed
                task.state = 'running'
                task.pause_event.set()
                thread.join(timeout=5)

                assert task.state == 'completed'
                assert task.completed == 3  # All 3 pending segments translated
