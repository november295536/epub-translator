"""Tests for Flask web review server."""

import json
import os
import threading
import pytest
from unittest.mock import patch, MagicMock

from src.web.server import create_app
from src.core.project import ProjectManager, SegmentStatus
from src.core.segmenter import Segment, SegmentType
from src.core.translator import RateLimitError
from src.web.tasks import TaskManager, TranslationTask


PROJECT_NAME = 'test_project'


def _setup_task_pm(task, projects_dir):
    """Load PM and fill segment queue on a task — mimics start_translation setup."""
    project_dir = os.path.join(projects_dir, task.project_name)
    pm = ProjectManager(project_dir)
    pm.load()
    task.pm = pm
    pending = pm.get_pending_segments()
    for seg in pending:
        task.segment_queue.put(seg)


@pytest.fixture
def project_with_translations(tmp_path):
    """Create a project with some translated segments for testing."""
    # Create projects directory structure: tmp_path/test_project/
    projects_dir = str(tmp_path)
    project_dir = os.path.join(projects_dir, PROJECT_NAME)
    os.makedirs(project_dir, exist_ok=True)

    segments = [
        Segment(id='ch1_seg0001', html='<h1>Title</h1>', text='Title',
                word_count=1, segment_type=SegmentType.TEXT),
        Segment(id='ch1_seg0002', html='<p>First paragraph.</p>',
                text='First paragraph.', word_count=2, segment_type=SegmentType.TEXT),
        Segment(id='ch1_seg0003', html='<p>Second paragraph.</p>',
                text='Second paragraph.', word_count=2, segment_type=SegmentType.TEXT),
        Segment(id='ch2_seg0001', html='<h2>Chapter 2</h2>',
                text='Chapter 2', word_count=2, segment_type=SegmentType.TEXT),
        Segment(id='ch2_seg0002', html='<p>Chapter 2 content.</p>',
                text='Chapter 2 content.', word_count=3, segment_type=SegmentType.TEXT),
    ]

    pm = ProjectManager(project_dir)
    pm.create('/path/to/book.epub', 'Test Book', 'Author', segments)

    # Translate some segments
    pm.mark_translated('ch1_seg0001', '標題')
    pm.mark_translated('ch1_seg0002', '第一段。')
    pm.save()

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
        assert seg.status == SegmentStatus.REVIEWED


class TestSharedProjectManager:

    def test_review_uses_running_task_pm(self, project_with_translations):
        """When a translation task is running, review endpoints should use its PM."""
        TaskManager._instance = None
        tm = TaskManager()

        # Create a fake running task with a PM
        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        shared_pm = ProjectManager(project_dir)
        shared_pm.load()

        task = TranslationTask(
            task_id='test_shared',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='running',
            total=3,
        )
        task.pm = shared_pm
        tm.tasks[PROJECT_NAME] = task

        # get_project_manager should return the shared PM
        result = tm.get_project_manager(PROJECT_NAME)
        assert result is shared_pm

        # get_or_load_pm should also return the shared PM
        result2 = tm.get_or_load_pm(PROJECT_NAME, project_with_translations)
        assert result2 is shared_pm

        TaskManager._instance = None

    def test_review_creates_own_pm_when_idle(self, project_with_translations):
        """When no task is running, review endpoints should create their own PM."""
        TaskManager._instance = None
        tm = TaskManager()

        result = tm.get_project_manager(PROJECT_NAME)
        assert result is None

        TaskManager._instance = None

    def test_review_creates_own_pm_when_completed(self, project_with_translations):
        """When task is completed, review should create its own PM."""
        TaskManager._instance = None
        tm = TaskManager()

        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        shared_pm = ProjectManager(project_dir)
        shared_pm.load()

        task = TranslationTask(
            task_id='test_done',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='completed',
            total=3,
        )
        task.pm = shared_pm
        tm.tasks[PROJECT_NAME] = task

        result = tm.get_project_manager(PROJECT_NAME)
        assert result is None

        TaskManager._instance = None


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
        _setup_task_pm(task, project_with_translations)

        def fake_translate(*args, **kwargs):
            raise RateLimitError("You've hit your limit · resets 8pm (Asia/Taipei)")

        with patch.object(TaskManager, '__new__', return_value=tm):
            with patch('src.web.tasks.TranslationEngine') as MockEngine:
                MockEngine.return_value.translate.side_effect = fake_translate

                # Run _run_translation in a thread so we can inspect state while it blocks
                thread = threading.Thread(target=tm._run_translation, args=(task,))
                thread.start()

                # Wait for task to pause (workers block on pause_event.wait)
                thread.join(timeout=5)
                assert task.state == 'paused'

                # Check for rate_limit event
                events = task.progress.get_all()

                rate_limit_events = [e for e in events if e['event'] == 'rate_limit']
                assert len(rate_limit_events) == 1
                assert "You've hit your limit" in rate_limit_events[0]['data']['error']

                # Stop the task so the thread exits
                task.stop_event.set()
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
        _setup_task_pm(task, project_with_translations)

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
                with task._lock:
                    task.state = 'running'
                task.pause_event.set()
                thread.join(timeout=5)

                assert task.state == 'completed'
                assert task.completed == 3  # All 3 pending segments translated

    def test_resume_dead_thread(self, project_with_translations):
        """Resuming a task whose thread has died should return False and set state to failed."""
        tm = TaskManager()

        task = TranslationTask(
            task_id='test_dead',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='paused',
            total=3,
        )

        # Create a thread that immediately exits
        dead_thread = threading.Thread(target=lambda: None)
        dead_thread.start()
        dead_thread.join()  # Wait for it to finish
        task._coordinator = dead_thread

        tm.tasks[PROJECT_NAME] = task

        result = tm.resume_translation(PROJECT_NAME)
        assert result is False
        assert task.state == 'failed'
        assert '翻譯執行緒已終止' in task.error


class TestStopThenStartJoin:

    def test_start_joins_old_thread(self, project_with_translations):
        """Starting a new translation should join the old coordinator thread."""
        TaskManager._instance = None
        tm = TaskManager()

        # Create a fake stopped task with a coordinator
        old_thread = MagicMock()
        old_thread.is_alive.return_value = False

        task = TranslationTask(
            task_id='test_old',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='stopped',
            total=3,
        )
        task._coordinator = old_thread
        tm.tasks[PROJECT_NAME] = task

        # Start a new translation — should join old thread
        with patch.object(tm, '_run_translation'):
            tm.start_translation(PROJECT_NAME, project_with_translations)

        old_thread.join.assert_called_once_with(timeout=5)

        # Clean up
        tm.stop_translation(PROJECT_NAME)
        TaskManager._instance = None


class TestModelAPI:

    def test_get_model(self, client):
        response = client.get(f'/api/projects/{PROJECT_NAME}/model')
        assert response.status_code == 200
        data = response.get_json()
        assert data['current'] == 'sonnet'
        assert len(data['available']) == 3

    def test_set_model(self, client, project_with_translations):
        response = client.put(
            f'/api/projects/{PROJECT_NAME}/model',
            data=json.dumps({'model': 'opus'}),
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert data['model'] == 'opus'

        # Verify persisted
        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        pm = ProjectManager(project_dir)
        pm.load()
        assert pm.data.model == 'opus'

    def test_set_invalid_model(self, client):
        response = client.put(
            f'/api/projects/{PROJECT_NAME}/model',
            data=json.dumps({'model': 'invalid'}),
            content_type='application/json',
        )
        assert response.status_code == 400
        data = response.get_json()
        assert '無效的模型' in data['error']

    def test_set_model_missing_body(self, client):
        response = client.put(
            f'/api/projects/{PROJECT_NAME}/model',
            data=json.dumps({}),
            content_type='application/json',
        )
        assert response.status_code == 400

    def test_get_model_nonexistent_project(self, client):
        response = client.get('/api/projects/nonexistent/model')
        assert response.status_code == 404

    def test_project_detail_includes_model(self, client):
        response = client.get(f'/api/projects/{PROJECT_NAME}')
        assert response.status_code == 200
        data = response.get_json()
        assert 'model' in data
        assert data['model'] == 'sonnet'


class TestSetModelDuringTranslation:

    def test_set_model_updates_running_task_pm(self, project_with_translations):
        """set_model API should update the running task's PM so workers pick it up."""
        TaskManager._instance = None
        app = create_app(projects_dir=project_with_translations)
        app.config['TESTING'] = True
        tm = TaskManager()

        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        shared_pm = ProjectManager(project_dir)
        shared_pm.load()

        task = TranslationTask(
            task_id='test_set_model_running',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='running',
            total=3,
        )
        task.pm = shared_pm
        tm.tasks[PROJECT_NAME] = task

        with app.test_client() as client:
            # Change model via API while task is running
            response = client.put(
                f'/api/projects/{PROJECT_NAME}/model',
                data=json.dumps({'model': 'opus'}),
                content_type='application/json',
            )
            assert response.status_code == 200

            # The running task's PM should have the updated model
            assert shared_pm.data.model == 'opus'

            # Verify model is persisted on disk
            reload_pm = ProjectManager(project_dir)
            reload_pm.load()
            assert reload_pm.data.model == 'opus'

        TaskManager._instance = None

    def test_get_model_reads_running_task_pm(self, project_with_translations):
        """get_model API should read from the running task's PM for accurate model."""
        TaskManager._instance = None
        app = create_app(projects_dir=project_with_translations)
        app.config['TESTING'] = True
        tm = TaskManager()

        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        shared_pm = ProjectManager(project_dir)
        shared_pm.load()
        shared_pm.data.model = 'opus'  # In-memory only, not saved to disk

        task = TranslationTask(
            task_id='test_get_model_running',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='running',
            total=3,
        )
        task.pm = shared_pm
        tm.tasks[PROJECT_NAME] = task

        with app.test_client() as client:
            response = client.get(f'/api/projects/{PROJECT_NAME}/model')
            data = response.get_json()
            assert data['current'] == 'opus'

        TaskManager._instance = None


class TestStoppedStateSSE:

    def test_stopped_pushes_event(self, project_with_translations):
        """When task is stopped, a 'stopped' event should be pushed to the queue."""
        tm = TaskManager()

        task = TranslationTask(
            task_id='test_stopped',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='running',
            total=3,
            num_workers=1,
        )
        _setup_task_pm(task, project_with_translations)

        call_count = 0

        def fake_translate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Stop the task after first call
                task.stop_event.set()
            return '翻譯結果'

        with patch.object(TaskManager, '__new__', return_value=tm):
            with patch('src.web.tasks.TranslationEngine') as MockEngine:
                MockEngine.return_value.translate.side_effect = fake_translate
                MockEngine.return_value.model = 'sonnet'

                thread = threading.Thread(target=tm._run_translation, args=(task,))
                thread.start()
                thread.join(timeout=10)

                # Check for stopped event
                events = task.progress.get_all()

                stopped_events = [e for e in events if e['event'] == 'stopped']
                assert len(stopped_events) == 1
                assert 'completed' in stopped_events[0]['data']
                assert 'remaining' in stopped_events[0]['data']

    def test_sse_terminates_on_stopped(self, project_with_translations):
        """SSE stream should terminate when it receives a 'stopped' event."""
        TaskManager._instance = None
        app = create_app(projects_dir=project_with_translations)
        app.config['TESTING'] = True

        tm = TaskManager()

        task = TranslationTask(
            task_id='test_sse_stopped',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='stopped',
            total=3,
        )
        tm.tasks[PROJECT_NAME] = task

        # Push stopped event after a short delay so it arrives after SSE connects
        def push_stopped():
            import time
            time.sleep(0.3)
            task.progress.put({'event': 'stopped', 'data': {
                'completed': 1, 'remaining': 2
            }})

        t = threading.Thread(target=push_stopped, daemon=True)
        t.start()

        with app.test_client() as client:
            response = client.get(f'/api/projects/{PROJECT_NAME}/translate/stream')
            data = response.data.decode()
            assert 'event: stopped' in data

        t.join(timeout=3)
        TaskManager._instance = None

    def test_sse_does_not_replay_history(self, project_with_translations):
        """SSE stream should NOT replay old events — only new events after connect."""
        TaskManager._instance = None
        app = create_app(projects_dir=project_with_translations)
        app.config['TESTING'] = True

        tm = TaskManager()

        task = TranslationTask(
            task_id='test_sse_no_replay',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='running',
            total=5,
            completed=2,
        )
        tm.tasks[PROJECT_NAME] = task

        # Push historical events BEFORE SSE connection
        task.progress.put({'event': 'rate_limit', 'data': {
            'error': 'old rate limit', 'completed': 1, 'remaining': 4,
        }})
        task.progress.put({'event': 'segment_complete', 'data': {
            'segment_id': 'old_seg', 'translated_text': 'old', 'model': 'sonnet',
        }})

        # Push a new event AFTER we know cursor_end, to simulate post-connect event
        # We'll push a 'complete' event so the SSE terminates
        import time
        def push_complete():
            time.sleep(0.3)
            task.state = 'completed'
            task.progress.put({'event': 'complete', 'data': {
                'total_translated': 5,
            }})

        t = threading.Thread(target=push_complete, daemon=True)
        t.start()

        with app.test_client() as client:
            response = client.get(f'/api/projects/{PROJECT_NAME}/translate/stream')
            data = response.data.decode()

            # Should have initial status event
            assert 'event: status' in data
            # Should have the post-connect complete event
            assert 'event: complete' in data
            # Should NOT have old rate_limit or segment_complete events
            assert 'event: rate_limit' not in data
            assert 'event: segment_complete' not in data

        t.join(timeout=3)
        TaskManager._instance = None


class TestRetranslateModel:

    def test_retranslate_uses_project_model(self, project_with_translations):
        """Retranslate endpoint should use the project's configured model, not the default."""
        # Set project model to opus
        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        pm = ProjectManager(project_dir)
        pm.load()
        pm.set_model('opus')
        pm.save()

        TaskManager._instance = None
        app = create_app(projects_dir=project_with_translations)
        app.config['TESTING'] = True

        with app.test_client() as client:
            with patch('src.web.blueprints.review.TranslationEngine') as MockEngine:
                MockEngine.return_value.translate.return_value = '重新翻譯結果'
                response = client.post(
                    f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0001/retranslate',
                )
                assert response.status_code == 200
                MockEngine.assert_called_once_with(model='opus')

        TaskManager._instance = None


class TestMidTranslationModelSwitch:

    def test_model_switch_during_translation(self, project_with_translations):
        """When pm.data.model is changed via set_model, workers should pick it up."""
        # The fixture has 5 segments, 2 translated → 3 pending
        tm = TaskManager()

        task = TranslationTask(
            task_id='test_model_switch',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='running',
            total=3,
            num_workers=1,
        )
        _setup_task_pm(task, project_with_translations)

        models_seen = []

        def tracking_translate(self_engine, *args, **kwargs):
            """Track which model the engine has when translate is called,
            and switch pm.data.model after first call."""
            models_seen.append(self_engine.model)
            # After first translation, change model (simulates set_model API)
            if len(models_seen) == 1:
                task.pm.data.model = 'opus'
            return '翻譯結果'

        with patch.object(TaskManager, '__new__', return_value=tm):
            with patch('src.web.tasks.TranslationEngine') as MockEngine:
                mock_engine = MagicMock()
                mock_engine.model = 'sonnet'
                mock_engine.translate = lambda *a, **kw: tracking_translate(mock_engine, *a, **kw)
                MockEngine.return_value = mock_engine

                thread = threading.Thread(target=tm._run_translation, args=(task,))
                thread.start()
                thread.join(timeout=10)

                assert task.state == 'completed'
                assert task.completed == 3
                assert len(models_seen) == 3
                # First segment uses sonnet, subsequent segments should use opus
                assert models_seen[0] == 'sonnet'
                assert all(m == 'opus' for m in models_seen[1:])


class TestSegmentCompleteModel:

    def test_segment_complete_includes_model(self, project_with_translations):
        """segment_complete event should include the model field."""
        tm = TaskManager()

        task = TranslationTask(
            task_id='test_model_event',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='running',
            total=3,
            num_workers=1,
        )
        _setup_task_pm(task, project_with_translations)

        with patch.object(TaskManager, '__new__', return_value=tm):
            with patch('src.web.tasks.TranslationEngine') as MockEngine:
                MockEngine.return_value.translate.return_value = '翻譯結果'
                # model is 'sonnet' on disk; engine reads from disk each iteration
                MockEngine.return_value.model = 'sonnet'

                thread = threading.Thread(target=tm._run_translation, args=(task,))
                thread.start()
                thread.join(timeout=10)

                assert task.state == 'completed'

                events = task.progress.get_all()

                seg_events = [e for e in events if e['event'] == 'segment_complete']
                assert len(seg_events) == 3
                for e in seg_events:
                    assert 'model' in e['data']
                    assert e['data']['model'] == 'sonnet'


class TestDynamicWorkers:

    def test_resume_increases_workers(self, project_with_translations):
        """Resuming with more workers should spawn additional threads."""
        TaskManager._instance = None
        tm = TaskManager()

        task = TranslationTask(
            task_id='test_inc_workers',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='running',
            total=3,
            num_workers=1,
        )
        _setup_task_pm(task, project_with_translations)
        tm.tasks[PROJECT_NAME] = task

        call_count = 0
        call_lock = threading.Lock()

        def fake_translate(*args, **kwargs):
            nonlocal call_count
            with call_lock:
                call_count += 1
                current = call_count
            if current == 1:
                raise RateLimitError("Rate limited")
            return '翻譯結果'

        with patch.object(TaskManager, '__new__', return_value=tm):
            with patch('src.web.tasks.TranslationEngine') as MockEngine:
                MockEngine.return_value.translate.side_effect = fake_translate
                MockEngine.return_value.model = 'sonnet'

                thread = threading.Thread(target=tm._run_translation, args=(task,))
                task._coordinator = thread
                thread.start()

                # Wait for pause
                thread.join(timeout=5)
                assert task.state == 'paused'

                # Resume with 2 workers (was 1)
                tm.resume_translation(PROJECT_NAME, num_workers=2)
                thread.join(timeout=10)

                assert task.state == 'completed'
                assert task.completed == 3
                assert task.num_workers == 2

        TaskManager._instance = None

    def test_resume_decreases_workers(self, project_with_translations):
        """Resuming with fewer workers should signal excess workers to exit."""
        TaskManager._instance = None
        tm = TaskManager()

        task = TranslationTask(
            task_id='test_dec_workers',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='running',
            total=3,
            num_workers=3,
        )
        _setup_task_pm(task, project_with_translations)
        tm.tasks[PROJECT_NAME] = task

        call_count = 0
        call_lock = threading.Lock()

        def fake_translate(*args, **kwargs):
            nonlocal call_count
            with call_lock:
                call_count += 1
                current = call_count
            if current == 1:
                raise RateLimitError("Rate limited")
            return '翻譯結果'

        with patch.object(TaskManager, '__new__', return_value=tm):
            with patch('src.web.tasks.TranslationEngine') as MockEngine:
                MockEngine.return_value.translate.side_effect = fake_translate
                MockEngine.return_value.model = 'sonnet'

                thread = threading.Thread(target=tm._run_translation, args=(task,))
                task._coordinator = thread
                thread.start()

                # Wait for pause
                thread.join(timeout=5)
                assert task.state == 'paused'

                # Resume with 1 worker (was 3)
                tm.resume_translation(PROJECT_NAME, num_workers=1)
                thread.join(timeout=10)

                assert task.state == 'completed'
                assert task.completed == 3
                assert task.num_workers == 1

        TaskManager._instance = None

    def test_resume_api_passes_workers(self, project_with_translations):
        """Pause/resume API should pass workers parameter to resume_translation."""
        TaskManager._instance = None
        app = create_app(projects_dir=project_with_translations)
        app.config['TESTING'] = True
        tm = TaskManager()

        # Create a fake paused task with alive thread
        task = TranslationTask(
            task_id='test_api_workers',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='paused',
            total=3,
            num_workers=1,
        )

        # Create a thread that stays alive (blocked on event)
        alive_event = threading.Event()
        def keep_alive():
            alive_event.wait()
        alive_thread = threading.Thread(target=keep_alive, daemon=True)
        alive_thread.start()
        task._coordinator = alive_thread
        tm.tasks[PROJECT_NAME] = task

        with app.test_client() as client:
            with patch.object(tm, 'resume_translation', return_value=True) as mock_resume:
                response = client.post(
                    f'/api/projects/{PROJECT_NAME}/translate/pause',
                    data=json.dumps({'workers': 4}),
                    content_type='application/json',
                )
                assert response.status_code == 200
                mock_resume.assert_called_once_with(PROJECT_NAME, num_workers=4)

        alive_event.set()
        alive_thread.join(timeout=1)
        TaskManager._instance = None


class TestMultiWorker:

    def test_start_with_workers_param(self, project_with_translations):
        """Start API should accept workers parameter and cap it."""
        app = create_app(projects_dir=project_with_translations)
        app.config['TESTING'] = True
        # Reset singleton for isolation
        TaskManager._instance = None

        with app.test_client() as client:
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/translate/start',
                data=json.dumps({'workers': 3}),
                content_type='application/json',
            )
            assert response.status_code == 200
            data = response.get_json()
            assert data['workers'] == 3

            # Stop the task
            tm = TaskManager()
            tm.stop_translation(PROJECT_NAME)

        TaskManager._instance = None

    def test_start_workers_capped_at_max(self, project_with_translations):
        """Workers should be capped at MAX_WORKERS (20)."""
        app = create_app(projects_dir=project_with_translations)
        app.config['TESTING'] = True
        TaskManager._instance = None

        with app.test_client() as client:
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/translate/start',
                data=json.dumps({'workers': 50}),
                content_type='application/json',
            )
            assert response.status_code == 200
            data = response.get_json()
            assert data['workers'] == 20

            tm = TaskManager()
            tm.stop_translation(PROJECT_NAME)

        TaskManager._instance = None

    def test_status_returns_worker_info(self, project_with_translations):
        """Status endpoint should include workers and active_workers."""
        app = create_app(projects_dir=project_with_translations)
        app.config['TESTING'] = True
        TaskManager._instance = None

        with app.test_client() as client:
            # Start with 2 workers
            client.post(
                f'/api/projects/{PROJECT_NAME}/translate/start',
                data=json.dumps({'workers': 2}),
                content_type='application/json',
            )

            response = client.get(f'/api/projects/{PROJECT_NAME}/translate/status')
            data = response.get_json()
            assert 'workers' in data
            assert 'active_workers' in data
            assert data['workers'] == 2

            tm = TaskManager()
            tm.stop_translation(PROJECT_NAME)

        TaskManager._instance = None

    def test_multi_worker_completes_all_segments(self, project_with_translations):
        """Multiple workers should translate all pending segments."""
        tm = TaskManager()

        task = TranslationTask(
            task_id='test_mw',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='running',
            total=3,
            num_workers=2,
        )
        _setup_task_pm(task, project_with_translations)

        with patch.object(TaskManager, '__new__', return_value=tm):
            with patch('src.web.tasks.TranslationEngine') as MockEngine:
                MockEngine.return_value.translate.return_value = '翻譯結果'
                MockEngine.return_value.model = 'sonnet'

                thread = threading.Thread(target=tm._run_translation, args=(task,))
                thread.start()
                thread.join(timeout=10)

                assert task.state == 'completed'
                assert task.completed == 3

    def test_rate_limit_pauses_all_workers(self, project_with_translations):
        """When one worker hits rate limit, all workers should pause."""
        tm = TaskManager()

        task = TranslationTask(
            task_id='test_mw_rl',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='running',
            total=3,
            num_workers=2,
        )
        _setup_task_pm(task, project_with_translations)

        call_count = 0
        call_lock = threading.Lock()

        def fake_translate(*args, **kwargs):
            nonlocal call_count
            with call_lock:
                call_count += 1
                current = call_count
            if current == 1:
                raise RateLimitError("You've hit your limit")
            return '翻譯結果'

        with patch.object(TaskManager, '__new__', return_value=tm):
            with patch('src.web.tasks.TranslationEngine') as MockEngine:
                MockEngine.return_value.translate.side_effect = fake_translate
                MockEngine.return_value.model = 'sonnet'

                thread = threading.Thread(target=tm._run_translation, args=(task,))
                thread.start()

                # Wait for pause
                thread.join(timeout=5)
                assert task.state == 'paused'

                # Check rate_limit event emitted
                events = task.progress.get_all()
                rate_limit_events = [e for e in events if e['event'] == 'rate_limit']
                assert len(rate_limit_events) >= 1

                # Resume
                with task._lock:
                    task.state = 'running'
                task.pause_event.set()
                thread.join(timeout=10)

                assert task.state == 'completed'


class TestSearchSegments:

    def test_search_text_match(self, client):
        """Search should find segments matching original text."""
        response = client.post(
            f'/api/projects/{PROJECT_NAME}/search-segments',
            data=json.dumps({'query': 'First'}),
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['total'] >= 1
        ids = [s['segment_id'] for s in data['segments']]
        assert 'ch1_seg0002' in ids

    def test_search_translation_match(self, client):
        """Search should find segments matching translated text."""
        response = client.post(
            f'/api/projects/{PROJECT_NAME}/search-segments',
            data=json.dumps({'query': '第一段'}),
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['total'] >= 1
        ids = [s['segment_id'] for s in data['segments']]
        assert 'ch1_seg0002' in ids

    def test_search_case_insensitive(self, client):
        """Search should be case-insensitive."""
        response = client.post(
            f'/api/projects/{PROJECT_NAME}/search-segments',
            data=json.dumps({'query': 'TITLE'}),
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        ids = [s['segment_id'] for s in data['segments']]
        assert 'ch1_seg0001' in ids

    def test_search_empty_query(self, client):
        """Empty query should return 400."""
        response = client.post(
            f'/api/projects/{PROJECT_NAME}/search-segments',
            data=json.dumps({'query': ''}),
            content_type='application/json',
        )
        assert response.status_code == 400

    def test_search_no_results(self, client):
        """Query with no matches should return empty list."""
        response = client.post(
            f'/api/projects/{PROJECT_NAME}/search-segments',
            data=json.dumps({'query': 'xyznonexistent'}),
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['total'] == 0
        assert data['segments'] == []

    def test_search_excludes_pending(self, client):
        """Search should not return PENDING segments."""
        response = client.post(
            f'/api/projects/{PROJECT_NAME}/search-segments',
            data=json.dumps({'query': 'Second paragraph'}),
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        # ch1_seg0003 is pending, should not appear
        ids = [s['segment_id'] for s in data['segments']]
        assert 'ch1_seg0003' not in ids

    def test_search_includes_match_in(self, client):
        """Results should include match_in field."""
        response = client.post(
            f'/api/projects/{PROJECT_NAME}/search-segments',
            data=json.dumps({'query': 'Title'}),
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['total'] >= 1
        seg = data['segments'][0]
        assert seg['match_in'] in ('text', 'translation', 'both')

    def test_search_page_renders(self, client):
        """Search page should render successfully."""
        response = client.get(f'/project/{PROJECT_NAME}/search')
        assert response.status_code == 200
        assert '搜尋' in response.data.decode()


class TestStartWithSegmentIds:

    def test_start_with_segment_ids(self, project_with_translations):
        """start_translation with segment_ids should queue those specific segments."""
        TaskManager._instance = None
        tm = TaskManager()

        with patch.object(tm, '_run_translation'):
            task = tm.start_translation(
                PROJECT_NAME, project_with_translations,
                segment_ids=['ch1_seg0001', 'ch1_seg0002'],
            )

        assert task.total == 2
        assert task.state == 'running'

        tm.stop_translation(PROJECT_NAME)
        TaskManager._instance = None

    def test_start_with_empty_segment_ids(self, project_with_translations):
        """start_translation with empty segment_ids should complete immediately."""
        TaskManager._instance = None
        tm = TaskManager()

        task = tm.start_translation(
            PROJECT_NAME, project_with_translations,
            segment_ids=[],
        )

        assert task.total == 0
        assert task.state == 'completed'

        TaskManager._instance = None

    def test_start_with_invalid_segment_ids(self, project_with_translations):
        """start_translation with nonexistent segment_ids should complete with 0."""
        TaskManager._instance = None
        tm = TaskManager()

        task = tm.start_translation(
            PROJECT_NAME, project_with_translations,
            segment_ids=['nonexistent_id'],
        )

        assert task.total == 0
        assert task.state == 'completed'

        TaskManager._instance = None

    def test_start_api_with_segment_ids(self, project_with_translations):
        """Start API should accept segment_ids parameter."""
        TaskManager._instance = None
        app = create_app(projects_dir=project_with_translations)
        app.config['TESTING'] = True

        with app.test_client() as client:
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/translate/start',
                data=json.dumps({
                    'segment_ids': ['ch1_seg0001', 'ch1_seg0002'],
                    'workers': 1,
                }),
                content_type='application/json',
            )
            assert response.status_code == 200
            data = response.get_json()
            assert data['total'] == 2
            assert data['state'] == 'running'

            tm = TaskManager()
            tm.stop_translation(PROJECT_NAME)

        TaskManager._instance = None

    def test_retranslate_already_translated_segment(self, project_with_translations):
        """segment_ids should queue segments regardless of their current status."""
        TaskManager._instance = None
        tm = TaskManager()

        # ch1_seg0001 is already translated
        with patch.object(tm, '_run_translation'):
            task = tm.start_translation(
                PROJECT_NAME, project_with_translations,
                segment_ids=['ch1_seg0001'],
            )

        assert task.total == 1
        # Verify the queued segment is the translated one
        seg = task.segment_queue.get_nowait()
        assert seg.segment_id == 'ch1_seg0001'

        tm.stop_translation(PROJECT_NAME)
        TaskManager._instance = None


class TestTermExtractionCoverage:
    """Web term extraction reads every text segment and no code segment."""

    def test_term_in_last_of_60_text_segments_is_sent(self, tmp_path):
        import time
        from src.web.blueprints.glossary import _extraction_tasks

        projects_dir = str(tmp_path)
        project_dir = os.path.join(projects_dir, PROJECT_NAME)
        os.makedirs(project_dir, exist_ok=True)

        segments = [
            Segment(id=f'ch1_seg{i:04d}', html=f'<p>Plain filler sentence {i}.</p>',
                    text=f'Plain filler sentence {i}.', word_count=4,
                    segment_type=SegmentType.TEXT)
            for i in range(1, 60)
        ]
        segments.insert(0, Segment(
            id='ch1_seg0000', html='<pre>CODEONLYMARK = 1</pre>',
            text='CODEONLYMARK = 1', word_count=3,
            segment_type=SegmentType.CODE))
        # The term sits beyond the per-call character limit of its own segment
        last_text = 'Long filler text. ' * 400 + 'The zorblatt engine appears here.'
        assert last_text.index('zorblatt') > 5000
        segments.append(Segment(
            id='ch1_seg0060', html=f'<p>{last_text}</p>',
            text=last_text, word_count=len(last_text.split()),
            segment_type=SegmentType.TEXT))
        assert sum(1 for s in segments if s.segment_type == SegmentType.TEXT) == 60

        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test Book', 'Author', segments)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '[]'
        mock_result.stderr = ''

        app = create_app(projects_dir=projects_dir)
        app.config['TESTING'] = True
        _extraction_tasks.pop(PROJECT_NAME, None)
        with patch('src.glossary.term_extractor.subprocess.run',
                   return_value=mock_result) as mock_run:
            with app.test_client() as client:
                response = client.post(
                    f'/api/projects/{PROJECT_NAME}/glossary/extract')
                assert response.status_code == 200
            deadline = time.time() + 10
            while _extraction_tasks[PROJECT_NAME]['running'] and time.time() < deadline:
                time.sleep(0.01)
            assert not _extraction_tasks[PROJECT_NAME]['running']
        _extraction_tasks.pop(PROJECT_NAME, None)

        sent = '\n'.join(c[0][0][2] for c in mock_run.call_args_list)
        assert 'zorblatt' in sent
        assert 'CODEONLYMARK' not in sent


class TestRetranslateContext:
    """Retranslation takes context from the same chapter only."""

    def test_previous_chapter_not_used_as_context(self, client):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '<h2>第二章</h2>'
        mock_result.stderr = ''

        with patch('src.core.translator.subprocess.run',
                   return_value=mock_result) as mock_run:
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/segments/ch2_seg0001/retranslate')

        assert response.status_code == 200
        prompt = mock_run.call_args[0][0][2]
        assert 'Second paragraph.' not in prompt   # last segment of chapter 1
        assert 'Chapter 2 content.' in prompt      # next segment, same chapter


class TestWorkerContext:
    """The prompts built by the web worker use same-chapter, non-code context."""

    @pytest.mark.parametrize('num_workers', [1, 3])
    def test_worker_prompts(self, tmp_path, make_context_project, num_workers):
        from tests.conftest import fake_claude_run, assert_context_prompts
        projects_dir = str(tmp_path)
        project_dir = make_context_project(os.path.join(projects_dir, PROJECT_NAME))

        TaskManager._instance = None
        tm = TaskManager()
        task = TranslationTask(task_id='ctx', project_name=PROJECT_NAME,
                               projects_dir=projects_dir, state='running',
                               total=5, num_workers=num_workers)
        _setup_task_pm(task, projects_dir)

        with patch('src.core.translator.subprocess.run',
                   side_effect=fake_claude_run) as mock_run:
            thread = threading.Thread(target=tm._run_translation, args=(task,))
            thread.start()
            thread.join(timeout=20)

        assert task.state == 'completed'
        prompts = [c[0][0][2] for c in mock_run.call_args_list]
        assert len(prompts) == 5
        assert_context_prompts(prompts, project_dir)
        TaskManager._instance = None


class TestCreateProjectMarker:

    def test_web_created_project_records_segmenter_version_2(self, tmp_path, sample_epub):
        app = create_app(projects_dir=str(tmp_path / 'projects'))
        app.config['TESTING'] = True
        with app.test_client() as client:
            response = client.post('/api/projects', json={
                'epub_path': sample_epub, 'project_name': 'marker'})
        assert response.status_code == 200, response.get_json()
        path = tmp_path / 'projects' / 'marker' / 'project.json'
        assert json.loads(path.read_text(encoding='utf-8'))['segmenter_version'] == 2
