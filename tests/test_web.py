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
from src.llm.fake import FakeBackend
from tests.conftest import use_fake_backend


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
            with use_fake_backend(FakeBackend(responder=lambda request: fake_translate())):

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
            with use_fake_backend(FakeBackend(responder=lambda request: fake_translate())):

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
            with use_fake_backend(FakeBackend(responder=lambda request: fake_translate())):

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
            fake = FakeBackend(['重新翻譯結果'])
            with use_fake_backend(fake):
                response = client.post(
                    f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0001/retranslate',
                )
                assert response.status_code == 200
                assert [r.model for r in fake.requests] == ['opus']

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

        def tracking_translate(request):
            """Track the model of each request, and switch pm.data.model
            after the first call."""
            models_seen.append(request.model)
            # After first translation, change model (simulates set_model API)
            if len(models_seen) == 1:
                task.pm.data.model = 'opus'
            return '翻譯結果'

        with patch.object(TaskManager, '__new__', return_value=tm):
            with use_fake_backend(FakeBackend(responder=tracking_translate)):

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
            with use_fake_backend(FakeBackend(responder=lambda request: '翻譯結果')):

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
            with use_fake_backend(FakeBackend(responder=lambda request: fake_translate())):

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
            with use_fake_backend(FakeBackend(responder=lambda request: fake_translate())):

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
            with use_fake_backend(FakeBackend(responder=lambda request: '翻譯結果')):

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
            with use_fake_backend(FakeBackend(responder=lambda request: fake_translate())):

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
        with patch('src.llm.cli.run_process',
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

    def test_previous_chapter_not_used_as_context(self, client, project_with_translations):
        from src.glossary.glossary_manager import GlossaryManager
        GlossaryManager(os.path.join(project_with_translations, PROJECT_NAME,
                                     'glossary.json')).add_term('GLOSSMARK', '詞彙')
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '<h2>第二章</h2>'
        mock_result.stderr = ''

        with patch('src.llm.cli.run_process',
                   return_value=mock_result) as mock_run:
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/segments/ch2_seg0001/retranslate')

        assert response.status_code == 200
        prompt = mock_run.call_args[0][0][2]
        assert 'Second paragraph.' not in prompt   # last segment of chapter 1
        assert 'Chapter 2 content.' in prompt      # next segment, same chapter
        assert 'GLOSSMARK → 詞彙' in prompt         # small glossary: all entries


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

        with patch('src.llm.cli.run_process',
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


# --- Phase 1: failed segments, revision, lock, extraction model ----------------

import time as _time

from src.core.project import Issue
from src.core.translator import TranslationOutcome
from src.llm import TransientError
from tests.conftest import hold_lock_in_child, lock_free_for_other_process


def _fail_on(*texts):
    """Responder: fail every attempt for segments whose HTML holds one of texts."""
    def respond(request):
        prompt = request.messages[-1].content
        if any(f'>{t}<' in prompt for t in texts):
            raise TransientError('exit 1', raw='RAW')
        return '<p>譯文</p>'
    return respond


def _wait(predicate, timeout=10):
    deadline = _time.time() + timeout
    while not predicate():
        assert _time.time() < deadline, 'timed out waiting'
        _time.sleep(0.01)


def _load(projects_dir):
    pm = ProjectManager(os.path.join(projects_dir, PROJECT_NAME))
    pm.load()
    return {s.segment_id: s for s in pm.data.segments}


class TestWorkerFailedSegment:

    @pytest.mark.parametrize('num_workers', [1, 3])
    def test_failing_segment_recorded_and_rest_translated(self, project_with_translations,
                                                          num_workers):
        TaskManager._instance = None
        tm = TaskManager()
        with use_fake_backend(FakeBackend(responder=_fail_on('Second paragraph.'))):
            task = tm.start_translation(PROJECT_NAME, project_with_translations,
                                        num_workers=num_workers)
            task._coordinator.join(timeout=20)

        assert task.state == 'completed'
        segs = _load(project_with_translations)
        assert segs['ch1_seg0003'].status == SegmentStatus.FAILED
        assert segs['ch1_seg0003'].issues[0].code == 'transient'
        assert segs['ch2_seg0001'].status == SegmentStatus.TRANSLATED
        assert segs['ch2_seg0002'].status == SegmentStatus.TRANSLATED
        assert segs['ch2_seg0002'].model == 'sonnet'
        errors = [e for e in task.progress.get_all() if e['event'] == 'segment_error']
        assert [e['data']['segment_id'] for e in errors] == ['ch1_seg0003']
        assert 'exit 1' in errors[0]['data']['error']
        assert task.completed == 2
        log_dir = os.path.join(project_with_translations, PROJECT_NAME, 'log', 'failed')
        assert os.listdir(log_dir) == ['ch1_seg0003-5e7e0f7e3d.txt']
        TaskManager._instance = None

    def test_failed_queued_only_with_retry_failed(self, client, project_with_translations):
        TaskManager._instance = None
        with use_fake_backend(FakeBackend(responder=_fail_on('Second paragraph.'))):
            client.post(f'/api/projects/{PROJECT_NAME}/translate/start', json={})
            TaskManager().get_task(PROJECT_NAME)._coordinator.join(timeout=20)

        progress = client.get(f'/api/projects/{PROJECT_NAME}/progress').get_json()
        assert progress['failed'] == 1

        fake = FakeBackend(responder=_fail_on())
        with use_fake_backend(fake):
            data = client.post(f'/api/projects/{PROJECT_NAME}/translate/start',
                               json={}).get_json()
            assert data['total'] == 0
            data = client.post(f'/api/projects/{PROJECT_NAME}/translate/start',
                               json={'retry_failed': True}).get_json()
            assert data['total'] == 1
            TaskManager().get_task(PROJECT_NAME)._coordinator.join(timeout=20)
        assert len(fake.requests) == 1
        assert _load(project_with_translations)['ch1_seg0003'].status == \
            SegmentStatus.TRANSLATED
        TaskManager._instance = None


class TestEditDuringTranslation:

    def test_result_for_segment_edited_mid_flight_is_dropped(self, client,
                                                            project_with_translations):
        TaskManager._instance = None
        in_flight = threading.Event()
        release = threading.Event()

        def respond(request):
            if '>Second paragraph.<' in request.messages[-1].content:
                in_flight.set()
                assert release.wait(10)
            return '<p>機器譯文</p>'

        with use_fake_backend(FakeBackend(responder=respond)):
            client.post(f'/api/projects/{PROJECT_NAME}/translate/start', json={})
            assert in_flight.wait(10)
            response = client.post(f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003',
                                   json={'text': '人工譯文'})
            assert response.status_code == 200
            release.set()
            task = TaskManager().get_task(PROJECT_NAME)
            task._coordinator.join(timeout=20)

        assert task.state == 'completed'
        seg = _load(project_with_translations)['ch1_seg0003']
        assert seg.translated_text == '人工譯文'
        assert seg.revision == 1
        assert _load(project_with_translations)['ch2_seg0001'].translated_text == \
            '<p>機器譯文</p>'
        TaskManager._instance = None


class TestRetranslateOutcome:

    def test_failed_retranslation_keeps_previous_text(self, client, project_with_translations):
        TaskManager._instance = None
        with use_fake_backend(FakeBackend([TransientError('exit 1')] * 3)):
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0002/retranslate')
        assert response.status_code == 500
        assert 'exit 1' in response.get_json()['error']
        seg = _load(project_with_translations)['ch1_seg0002']
        assert seg.status == SegmentStatus.TRANSLATED
        assert seg.translated_text == '第一段。'
        assert [i.code for i in seg.issues] == ['transient']

    def test_failed_retranslation_of_pending_segment(self, client, project_with_translations):
        with use_fake_backend(FakeBackend([TransientError('exit 1')] * 3)):
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003/retranslate')
        assert response.status_code == 500
        assert _load(project_with_translations)['ch1_seg0003'].status == \
            SegmentStatus.FAILED

    def test_retranslation_records_model(self, client, project_with_translations):
        with use_fake_backend(FakeBackend(['<p>新譯文</p>'])):
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003/retranslate')
        assert response.status_code == 200
        assert response.get_json()['translated_text'] == '<p>新譯文</p>'
        seg = _load(project_with_translations)['ch1_seg0003']
        assert (seg.status, seg.model) == (SegmentStatus.TRANSLATED, 'sonnet')

    def test_rate_limit(self, client, project_with_translations):
        with use_fake_backend(FakeBackend([RateLimitError("You've hit your limit")])):
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003/retranslate')
        assert response.status_code == 500
        assert 'hit your limit' in response.get_json()['error']
        assert _load(project_with_translations)['ch1_seg0003'].status == \
            SegmentStatus.PENDING


class TestWebProjectLock:

    def _project_dir(self, projects_dir):
        return os.path.join(projects_dir, PROJECT_NAME)

    def test_start_refused_while_other_process_holds_lock(self, client,
                                                         project_with_translations):
        TaskManager._instance = None
        child = hold_lock_in_child(self._project_dir(project_with_translations))
        try:
            response = client.post(f'/api/projects/{PROJECT_NAME}/translate/start', json={})
        finally:
            child.stdin.close()
            child.wait(timeout=10)
        assert response.status_code == 409
        assert '另一個程序' in response.get_json()['error']
        assert lock_free_for_other_process(self._project_dir(project_with_translations))
        TaskManager._instance = None

    def test_edit_refused_while_other_process_holds_lock(self, client,
                                                        project_with_translations):
        TaskManager._instance = None
        child = hold_lock_in_child(self._project_dir(project_with_translations))
        try:
            response = client.post(f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0001',
                                   json={'text': '改'})
        finally:
            child.stdin.close()
            child.wait(timeout=10)
        assert response.status_code == 409
        assert _load(project_with_translations)['ch1_seg0001'].translated_text == '標題'

    def test_lock_held_during_task_and_released_after(self, project_with_translations):
        TaskManager._instance = None
        project_dir = self._project_dir(project_with_translations)
        seen = []

        def respond(request):
            seen.append(lock_free_for_other_process(project_dir))
            return '<p>譯文</p>'

        tm = TaskManager()
        with use_fake_backend(FakeBackend(responder=respond)):
            task = tm.start_translation(PROJECT_NAME, project_with_translations)
            task._coordinator.join(timeout=30)
        assert task.state == 'completed'
        assert seen == [False, False, False]
        assert lock_free_for_other_process(project_dir)
        TaskManager._instance = None

    def test_lock_released_when_nothing_to_translate(self, project_with_translations):
        TaskManager._instance = None
        task = TaskManager().start_translation(PROJECT_NAME, project_with_translations,
                                               segment_ids=[])
        assert task.state == 'completed'
        assert lock_free_for_other_process(self._project_dir(project_with_translations))
        TaskManager._instance = None

    def test_lock_released_when_loading_fails(self, tmp_path):
        TaskManager._instance = None
        project_dir = tmp_path / PROJECT_NAME
        project_dir.mkdir()
        (project_dir / 'project.json').write_text('not json')
        with pytest.raises(ValueError):
            TaskManager().start_translation(PROJECT_NAME, str(tmp_path))
        assert lock_free_for_other_process(str(project_dir))
        TaskManager._instance = None

    def test_lock_released_when_coordinator_fails(self, project_with_translations):
        TaskManager._instance = None
        tm = TaskManager()
        with patch('src.web.tasks.GlossaryManager', side_effect=RuntimeError('broken')):
            task = tm.start_translation(PROJECT_NAME, project_with_translations)
            task._coordinator.join(timeout=10)
        assert task.state == 'failed'
        assert lock_free_for_other_process(self._project_dir(project_with_translations))
        TaskManager._instance = None


class TestFailedInPages:

    def _fail_one(self, projects_dir):
        pm = ProjectManager(os.path.join(projects_dir, PROJECT_NAME))
        pm.load()
        pm.apply_outcome('ch1_seg0003', TranslationOutcome(
            text=None, status=SegmentStatus.FAILED, model='sonnet', usage=None,
            issues=[Issue('backend', 'transient', 'FAILDETAIL', True)]))
        pm.save()

    def test_search_includes_failed(self, client, project_with_translations):
        self._fail_one(project_with_translations)
        data = client.post(f'/api/projects/{PROJECT_NAME}/search-segments',
                           json={'query': 'paragraph'}).get_json()
        found = {s['segment_id']: s['status'] for s in data['segments']}
        assert found == {'ch1_seg0002': 'translated', 'ch1_seg0003': 'failed'}

    def test_search_excludes_pending(self, client):
        data = client.post(f'/api/projects/{PROJECT_NAME}/search-segments',
                           json={'query': 'Chapter 2'}).get_json()
        assert data['segments'] == []

    def test_pages_show_failed(self, client, project_with_translations):
        self._fail_one(project_with_translations)
        html = client.get(f'/project/{PROJECT_NAME}/review/chapter/ch1').get_data(as_text=True)
        assert 'status-failed' in html
        assert 'FAILDETAIL' in html
        html = client.get(f'/project/{PROJECT_NAME}/overview').get_data(as_text=True)
        assert '翻譯失敗' in html
        html = client.get(f'/project/{PROJECT_NAME}/translation').get_data(as_text=True)
        assert '重試失敗段落（1）' in html

    def test_retry_button_hidden_without_failed(self, client):
        # The button is always in the page (review round 1, item 22) and
        # hidden by its x-show while the failed count is 0
        html = client.get(f'/project/{PROJECT_NAME}/translation').get_data(as_text=True)
        assert '重試失敗段落（0）' in html
        assert 'failedCount: 0,' in html
        html = client.get(f'/project/{PROJECT_NAME}/overview').get_data(as_text=True)
        assert '翻譯失敗' not in html


class TestTermExtractionModel:

    def test_web_extraction_uses_project_model(self, client, project_with_translations):
        from src.web.blueprints.glossary import _extraction_tasks
        pm = ProjectManager(os.path.join(project_with_translations, PROJECT_NAME))
        pm.load()
        pm.set_model('haiku')
        pm.save()
        _extraction_tasks.pop(PROJECT_NAME, None)
        fake = FakeBackend(responder=lambda request: '[]')
        with use_fake_backend(fake):
            assert client.post(
                f'/api/projects/{PROJECT_NAME}/glossary/extract').status_code == 200
            _wait(lambda: not _extraction_tasks[PROJECT_NAME]['running'])
        _extraction_tasks.pop(PROJECT_NAME, None)
        assert fake.requests and all(r.model == 'haiku' for r in fake.requests)


class TestWorkerEventsAndLogs:
    """Exact events and log lines of the coordinator (mutation survivors)."""

    def _run(self, projects_dir, responder, segment_ids=None):
        TaskManager._instance = None
        tm = TaskManager()
        with use_fake_backend(FakeBackend(responder=responder)):
            task = tm.start_translation(PROJECT_NAME, projects_dir, segment_ids=segment_ids)
            task._coordinator.join(timeout=20)
        TaskManager._instance = None
        return task

    def test_events_for_one_success_and_one_failure(self, project_with_translations, caplog):
        import logging
        caplog.set_level(logging.INFO, logger='src.web.tasks')
        long_text = '<p>' + '譯' * 300 + '</p>'

        def respond(request):
            if '>Chapter 2<' in request.messages[-1].content:
                raise TransientError('boom')
            return long_text

        task = self._run(project_with_translations, respond,
                         segment_ids=['ch1_seg0003', 'ch2_seg0001'])
        events = [e for e in task.progress.get_all()
                  if e['event'] in ('progress', 'segment_complete', 'segment_error')]
        assert events == [
            {'event': 'progress', 'data': {'completed': 1, 'total': 2, 'percent': 50.0}},
            {'event': 'segment_complete', 'data': {'segment_id': 'ch1_seg0003',
                                                   'translated_text': long_text[:200],
                                                   'model': 'sonnet'}},
            {'event': 'segment_error', 'data': {
                'segment_id': 'ch2_seg0001',
                'error': 'Translation failed after 3 attempts: boom'}},
        ]
        messages = [r.getMessage() for r in caplog.records]
        assert '段落完成 [1/2]: ch1_seg0003 (sonnet)' in messages
        assert ('段落翻譯失敗: ch2_seg0001 — Translation failed after 3 attempts: boom'
                in messages)

    def test_percent_rounding_with_three_segments(self, project_with_translations):
        task = self._run(project_with_translations, lambda request: '<p>譯</p>')
        percents = [e['data']['percent'] for e in task.progress.get_all()
                    if e['event'] == 'progress']
        assert percents == [33.3, 66.7, 100.0]

    def test_single_segment_is_100_percent(self, project_with_translations):
        task = self._run(project_with_translations, lambda request: '<p>譯</p>',
                         segment_ids=['ch1_seg0003'])
        assert [e['data'] for e in task.progress.get_all() if e['event'] == 'progress'] == \
            [{'completed': 1, 'total': 1, 'percent': 100.0}]

    def test_dropped_result_logged(self, client, project_with_translations, caplog):
        import logging
        caplog.set_level(logging.INFO, logger='src.web.tasks')
        TaskManager._instance = None
        in_flight = threading.Event()
        release = threading.Event()

        def respond(request):
            in_flight.set()
            assert release.wait(10)
            return '<p>機器譯文</p>'

        with use_fake_backend(FakeBackend(responder=respond)):
            client.post(f'/api/projects/{PROJECT_NAME}/translate/start',
                        json={'segment_ids': ['ch1_seg0003']})
            assert in_flight.wait(10)
            client.post(f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003/approve',
                        json={'text': '人工譯文'})
            release.set()
            TaskManager().get_task(PROJECT_NAME)._coordinator.join(timeout=20)
        assert '段落已在翻譯期間被編輯，捨棄結果: ch1_seg0003' in \
            [r.getMessage() for r in caplog.records]
        seg = _load(project_with_translations)['ch1_seg0003']
        assert (seg.status, seg.translated_text) == (SegmentStatus.REVIEWED, '人工譯文')
        TaskManager._instance = None

    def test_start_without_retry_failed_skips_failed(self, project_with_translations):
        pm = ProjectManager(os.path.join(project_with_translations, PROJECT_NAME))
        pm.load()
        pm.apply_outcome('ch1_seg0003', TranslationOutcome(
            text=None, status=SegmentStatus.FAILED, model='sonnet', usage=None,
            issues=[Issue('backend', 'transient', 'x', True)]))
        pm.save()
        TaskManager._instance = None
        tm = TaskManager()
        with patch.object(tm, '_run_translation'):
            task = tm.start_translation(PROJECT_NAME, project_with_translations)
        assert task.total == 2
        task.project_lock.release()
        TaskManager._instance = None


class TestRetranslateDetails:

    def test_edit_during_retranslation_is_kept(self, client, project_with_translations):
        """Two real requests, no shared manager patched in (review round 1, item 2)."""
        TaskManager._instance = None
        edited = threading.Event()

        def respond(request):
            response = client.application.test_client().post(
                f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0002', json={'text': '人工譯文'})
            assert response.status_code == 200
            edited.set()
            return '<p>機器譯文</p>'

        with use_fake_backend(FakeBackend(responder=respond)):
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0002/retranslate')
        assert edited.is_set()
        assert response.status_code == 409
        assert response.get_json() == {'error': '段落已在翻譯期間被編輯，未套用新譯文'}
        assert _load(project_with_translations)['ch1_seg0002'].translated_text == '人工譯文'

    def test_failure_diagnostics_and_logs(self, client, project_with_translations, caplog):
        import logging
        caplog.set_level(logging.WARNING, logger='src.web.blueprints.review')
        with use_fake_backend(FakeBackend([TransientError('boom')] * 3)):
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003/retranslate')
        assert response.get_json() == {'error': 'Translation failed after 3 attempts: boom'}
        log_dir = os.path.join(project_with_translations, PROJECT_NAME, 'log', 'failed')
        assert os.listdir(log_dir) == ['ch1_seg0003-5e7e0f7e3d.txt']
        assert 'Translation error for ch1_seg0003: Translation failed after 3 attempts: boom' \
            in [r.getMessage() for r in caplog.records]

    def test_rate_limit_logged(self, client, caplog):
        import logging
        caplog.set_level(logging.WARNING, logger='src.web.blueprints.review')
        with use_fake_backend(FakeBackend([RateLimitError('limit hit')])):
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003/retranslate')
        assert response.get_json() == {'error': 'limit hit'}
        assert 'Rate limit during retranslation: limit hit' in \
            [r.getMessage() for r in caplog.records]


class TestLockedSaveResponse:

    def test_exact_409_body(self, client, project_with_translations):
        from src.core.project import ProjectLockedError
        with patch('src.web.blueprints.review.ProjectManager.save',
                   side_effect=ProjectLockedError('held elsewhere')):
            response = client.post(f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0001',
                                   json={'text': '改'})
        assert response.status_code == 409
        assert response.get_json() == {'error': 'held elsewhere'}


class TestTaskWithoutLock:

    def test_coordinator_of_task_built_directly_ends_cleanly(self, project_with_translations):
        """A task that did not come from start_translation holds no lock;
        its coordinator must not try to release one."""
        TaskManager._instance = None
        tm = TaskManager()
        task = TranslationTask(task_id='direct', project_name=PROJECT_NAME,
                               projects_dir=project_with_translations, state='running',
                               total=3)
        _setup_task_pm(task, project_with_translations)
        with use_fake_backend(FakeBackend(responder=lambda request: '<p>譯</p>')):
            tm._run_translation(task)  # in this thread: an exception would fail the test
        assert task.state == 'completed'
        TaskManager._instance = None


# --- Review round 1 ---------------------------------------------------------------

from src.core.translator import failure_log_path
from src.llm import AuthError, BackendUnavailableError


def _start(projects_dir, responder, **kwargs):
    """Start a task with a fake backend that stays in place while it runs."""
    TaskManager._instance = None
    patcher = use_fake_backend(FakeBackend(responder=responder))
    patcher.start()
    try:
        task = TaskManager().start_translation(PROJECT_NAME, projects_dir, **kwargs)
    except BaseException:
        patcher.stop()
        raise
    return task, patcher


def _finish(task, patcher, timeout=20):
    task._coordinator.join(timeout=timeout)
    alive = task._coordinator.is_alive()
    patcher.stop()
    TaskManager._instance = None
    assert not alive, 'coordinator did not end'


class TestRetranslateTransaction:
    """Item 2: re-translate applies its result to the current state."""

    def test_edit_of_other_segment_not_reverted(self, client, project_with_translations):
        TaskManager._instance = None

        def respond(request):
            response = client.application.test_client().post(
                f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0001', json={'text': '人工標題'})
            assert response.status_code == 200
            return '<p>機器譯文</p>'

        with use_fake_backend(FakeBackend(responder=respond)):
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0002/retranslate')
        assert response.status_code == 200
        segs = _load(project_with_translations)
        assert segs['ch1_seg0001'].translated_text == '人工標題'
        assert segs['ch1_seg0002'].translated_text == '<p>機器譯文</p>'

    def test_edit_from_other_thread_during_retranslation(self, client,
                                                         project_with_translations):
        """The edit runs in a second request thread while the call is held."""
        TaskManager._instance = None
        in_flight, release = threading.Event(), threading.Event()
        result = {}

        def respond(request):
            in_flight.set()
            assert release.wait(10)
            return '<p>機器譯文</p>'

        def retranslate():
            result['r'] = client.application.test_client().post(
                f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0002/retranslate')

        with use_fake_backend(FakeBackend(responder=respond)):
            t = threading.Thread(target=retranslate)
            t.start()
            assert in_flight.wait(10)
            assert client.post(f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0002/approve',
                               json={'text': '人工'}).status_code == 200
            assert client.post(f'/api/projects/{PROJECT_NAME}/segments/ch2_seg0001',
                               json={'text': '人工二'}).status_code == 200
            release.set()
            t.join(10)
        assert result['r'].status_code == 409
        segs = _load(project_with_translations)
        assert (segs['ch1_seg0002'].status, segs['ch1_seg0002'].translated_text) == \
            (SegmentStatus.REVIEWED, '人工')
        assert segs['ch2_seg0001'].translated_text == '人工二'

    def test_edit_while_stopped_task_is_ending_is_kept(self, client,
                                                      project_with_translations):
        """A stopped task whose coordinator still runs owns the project state."""
        in_flight, release = threading.Event(), threading.Event()

        def respond(request):
            if '>Second paragraph.<' in request.messages[-1].content:
                in_flight.set()
                assert release.wait(10)
            return '<p>機器譯文</p>'

        task, patcher = _start(project_with_translations, respond,
                               segment_ids=['ch1_seg0003'])
        assert in_flight.wait(10)
        TaskManager().stop_translation(PROJECT_NAME)
        assert client.post(f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0001',
                           json={'text': '人工標題'}).status_code == 200
        release.set()
        _finish(task, patcher)
        assert _load(project_with_translations)['ch1_seg0001'].translated_text == '人工標題'


class TestOverlappingTasks:
    """Item 4: a start never overlaps a task whose threads are still alive."""

    def test_start_rejected_while_old_worker_in_flight(self, client,
                                                      project_with_translations,
                                                      monkeypatch):
        monkeypatch.setattr('src.web.tasks.STOP_WAIT_SECONDS', 0.2)
        in_flight, release = threading.Event(), threading.Event()

        def respond(request):
            in_flight.set()
            assert release.wait(10)
            return '<p>譯</p>'

        task, patcher = _start(project_with_translations, respond)
        assert in_flight.wait(10)
        assert TaskManager().pause_translation(PROJECT_NAME)
        response = client.post(f'/api/projects/{PROJECT_NAME}/translate/start', json={})
        assert response.status_code == 409
        assert '仍在結束中' in response.get_json()['error']
        assert TaskManager().get_task(PROJECT_NAME) is task
        release.set()
        _finish(task, patcher)
        assert task.state == 'stopped'

    def test_simultaneous_starts(self, project_with_translations):
        TaskManager._instance = None
        tm = TaskManager()
        loading, go_on = threading.Event(), threading.Event()
        real_load = ProjectManager.load
        calls = []

        def slow_load(self):
            calls.append(1)
            if len(calls) == 1:
                loading.set()
                assert go_on.wait(10)
            return real_load(self)

        outcomes = []

        def start():
            try:
                outcomes.append(tm.start_translation(PROJECT_NAME, project_with_translations))
            except ValueError as e:
                outcomes.append(e)

        hold = threading.Event()
        with use_fake_backend(FakeBackend(responder=lambda r: hold.wait(10) and '<p>譯</p>')), \
                patch.object(ProjectManager, 'load', slow_load):
            first = threading.Thread(target=start)
            first.start()
            assert loading.wait(10)
            second = threading.Thread(target=start)
            second.start()
            second.join(0.3)
            go_on.set()
            first.join(10)
            second.join(10)
            hold.set()
            tasks = [o for o in outcomes if isinstance(o, TranslationTask)]
            for t in tasks:
                t._coordinator.join(20)
        assert len(tasks) == 1
        assert [str(o) for o in outcomes if isinstance(o, ValueError)] == ['翻譯任務已在執行中']
        TaskManager._instance = None


class TestStartFailures:
    """Item 5: failures while a task starts release the project lock."""

    def _project_dir(self, projects_dir):
        return os.path.join(projects_dir, PROJECT_NAME)

    def test_logging_setup_failure(self, project_with_translations):
        TaskManager._instance = None
        with patch('src.web.tasks.setup_project_logging', side_effect=OSError('read-only')):
            task = TaskManager().start_translation(PROJECT_NAME, project_with_translations)
            task._coordinator.join(10)
        assert task.state == 'failed'
        assert 'read-only' in task.error
        assert lock_free_for_other_process(self._project_dir(project_with_translations))
        TaskManager._instance = None

    def test_coordinator_start_failure(self, client, project_with_translations):
        TaskManager._instance = None
        with patch.object(threading.Thread, 'start', side_effect=RuntimeError('no threads')):
            response = client.post(f'/api/projects/{PROJECT_NAME}/translate/start', json={})
        assert response.status_code == 500
        assert lock_free_for_other_process(self._project_dir(project_with_translations))
        with use_fake_backend(FakeBackend(responder=lambda r: '<p>譯</p>')):
            response = client.post(f'/api/projects/{PROJECT_NAME}/translate/start', json={})
            assert response.status_code == 200
            TaskManager().get_task(PROJECT_NAME)._coordinator.join(20)
        TaskManager._instance = None


class TestCoordinatorFailure:
    """Item 6: a failing coordinator stops and joins its workers first."""

    def test_save_failure_joins_workers_and_reports_unsaved(self, project_with_translations):
        threads = []

        def respond(request):
            threads.append(threading.current_thread())
            if '>Chapter 2<' in request.messages[-1].content:
                _time.sleep(1)  # still in flight when the save fails
            return '<p>譯</p>'

        TaskManager._instance = None
        with patch.object(ProjectManager, 'save', side_effect=OSError('disk full')):
            task, patcher = _start(project_with_translations, respond, num_workers=3)
            _finish(task, patcher)
        assert task.state == 'failed'
        assert not any(t.is_alive() for t in threads + task._threads)
        assert 'disk full' in task.error and '未儲存' in task.error
        error = [e for e in task.progress.get_all() if e['event'] == 'error'][0]
        assert set(error['data']['unsaved']) == {'ch1_seg0003', 'ch2_seg0001', 'ch2_seg0002'}
        assert lock_free_for_other_process(os.path.join(project_with_translations,
                                                        PROJECT_NAME))

    def test_save_failure_wakes_paused_worker(self, project_with_translations):
        paused = threading.Event()

        def respond(request):
            prompt = request.messages[-1].content
            if '>Chapter 2<' in prompt:
                raise RateLimitError('limit')
            if '>Second paragraph.<' in prompt:
                assert paused.wait(10)
            return '<p>譯</p>'

        TaskManager._instance = None
        with patch.object(ProjectManager, 'save', side_effect=OSError('disk full')):
            task, patcher = _start(project_with_translations, respond,
                                   segment_ids=['ch1_seg0003', 'ch2_seg0001'],
                                   num_workers=2)
            _wait(lambda: task.state == 'paused')
            paused.set()
            _finish(task, patcher)
        assert task.state == 'failed'
        assert not any(t.is_alive() for t in task._threads)


class TestStopThenLateRunError:
    """Item 7: a run error after stop does not pause the ending task."""

    @pytest.mark.parametrize('error', [AuthError('expired'), RateLimitError('limit')])
    def test_stop_then_error(self, project_with_translations, error):
        in_flight, release = threading.Event(), threading.Event()

        def respond(request):
            in_flight.set()
            assert release.wait(10)
            raise error

        task, patcher = _start(project_with_translations, respond)
        assert in_flight.wait(10)
        TaskManager().stop_translation(PROJECT_NAME)
        release.set()
        _finish(task, patcher, timeout=10)
        assert task.state == 'stopped'
        assert lock_free_for_other_process(os.path.join(project_with_translations,
                                                        PROJECT_NAME))


class TestWorkerUnexpectedErrors:
    """Item 9: diagnostics I/O errors and unexpected exceptions fail one segment."""

    def test_unwritable_failure_log(self, project_with_translations):
        log_dir = os.path.join(project_with_translations, PROJECT_NAME, 'log')
        os.makedirs(log_dir)
        with open(os.path.join(log_dir, 'failed'), 'w') as f:
            f.write('a file where the directory should be')
        task, patcher = _start(project_with_translations, _fail_on('Second paragraph.'),
                               num_workers=2)
        _finish(task, patcher)
        assert task.state == 'completed'
        segs = _load(project_with_translations)
        assert segs['ch1_seg0003'].status == SegmentStatus.FAILED
        assert segs['ch2_seg0002'].status == SegmentStatus.TRANSLATED

    def test_unexpected_exception(self, project_with_translations):
        def respond(request):
            if '>Second paragraph.<' in request.messages[-1].content:
                raise KeyError('surprise')
            return '<p>譯</p>'

        task, patcher = _start(project_with_translations, respond, num_workers=2)
        _finish(task, patcher)
        assert task.state == 'completed'
        segs = _load(project_with_translations)
        assert segs['ch1_seg0003'].status == SegmentStatus.FAILED
        assert segs['ch1_seg0003'].issues[0].code == 'unexpected'
        assert 'surprise' in segs['ch1_seg0003'].issues[0].detail
        assert segs['ch2_seg0001'].status == SegmentStatus.TRANSLATED


class TestBackendUnavailable:
    """Item 15: a missing program pauses the run; no segment becomes FAILED."""

    def test_task_pauses_and_segments_stay_pending(self, project_with_translations):
        task, patcher = _start(project_with_translations,
                               lambda r: (_ for _ in ()).throw(
                                   BackendUnavailableError('could not be started')),
                               num_workers=2)
        _wait(lambda: task.state == 'paused')
        TaskManager().stop_translation(PROJECT_NAME)
        _finish(task, patcher)
        events = [e for e in task.progress.get_all() if e['event'] == 'backend_error']
        assert len(events) == 1 and 'could not be started' in events[0]['data']['error']
        assert {s.status for s in _load(project_with_translations).values()} == \
            {SegmentStatus.TRANSLATED, SegmentStatus.PENDING}

    def test_retranslate(self, client, project_with_translations):
        with use_fake_backend(FakeBackend([BackendUnavailableError('could not be started')])):
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003/retranslate')
        assert response.status_code == 503
        assert _load(project_with_translations)['ch1_seg0003'].status == SegmentStatus.PENDING


class TestQueuedEdits:
    """Items 16, 17, 25: edits of queued or in-flight segments win."""

    def test_dropped_result_sends_no_success_event(self, client, project_with_translations):
        in_flight, release = threading.Event(), threading.Event()

        def respond(request):
            in_flight.set()
            assert release.wait(10)
            return '<p>機器譯文</p>'

        task, patcher = _start(project_with_translations, respond,
                               segment_ids=['ch1_seg0003'])
        assert in_flight.wait(10)
        client.post(f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003',
                    json={'text': '人工譯文'})
        release.set()
        _finish(task, patcher)
        names = [e['event'] for e in task.progress.get_all()]
        assert 'segment_complete' not in names
        assert names.count('progress') == 1

    @pytest.mark.parametrize('action', ['edit', 'approve'])
    def test_queued_segment_edited_before_pickup(self, client, project_with_translations,
                                                 action):
        in_flight, release = threading.Event(), threading.Event()
        prompts = []

        def respond(request):
            prompts.append(request.messages[-1].content)
            if '>Second paragraph.<' in request.messages[-1].content:
                in_flight.set()
                assert release.wait(10)
            return '<p>機器譯文</p>'

        task, patcher = _start(project_with_translations, respond,
                               segment_ids=['ch1_seg0003', 'ch2_seg0001'], num_workers=1)
        assert in_flight.wait(10)
        url = f'/api/projects/{PROJECT_NAME}/segments/ch2_seg0001'
        if action == 'approve':
            url += '/approve'
        assert client.post(url, json={'text': '人工'}).status_code == 200
        release.set()
        _finish(task, patcher)
        assert _load(project_with_translations)['ch2_seg0001'].translated_text == '人工'
        # Item 25: no call spent on the edited segment
        assert len(prompts) == 1
        assert task.state == 'completed' and task.completed == 2

    def test_queued_reviewed_segment_is_translated_when_asked(self, client,
                                                              project_with_translations):
        client.post(f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0002/approve', json={})
        task, patcher = _start(project_with_translations, lambda r: '<p>新譯</p>',
                               segment_ids=['ch1_seg0002'])
        _finish(task, patcher)
        assert _load(project_with_translations)['ch1_seg0002'].translated_text == '<p>新譯</p>'


class TestEditRemovesFailureLog:
    """Item 24: a manual edit or approval removes the segment's failure log."""

    @pytest.mark.parametrize('suffix', ['', '/approve'])
    def test_failure_log_removed(self, client, project_with_translations, suffix):
        with use_fake_backend(FakeBackend([TransientError('exit 1')] * 3)):
            client.post(f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003/retranslate')
        log_dir = os.path.join(project_with_translations, PROJECT_NAME, 'log')
        assert os.path.exists(failure_log_path(log_dir, 'ch1_seg0003'))
        response = client.post(f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003{suffix}',
                               json={'text': '人工'})
        assert response.status_code == 200
        assert not os.path.exists(failure_log_path(log_dir, 'ch1_seg0003'))
        seg = _load(project_with_translations)['ch1_seg0003']
        assert seg.issues == [] and seg.status != SegmentStatus.FAILED


class TestDeleteGuard:
    """Item 18: no delete under a running task or another process's lock."""

    def test_delete_refused_while_task_runs(self, client, project_with_translations):
        in_flight, release = threading.Event(), threading.Event()

        def respond(request):
            in_flight.set()
            assert release.wait(10)
            return '<p>譯</p>'

        task, patcher = _start(project_with_translations, respond)
        assert in_flight.wait(10)
        response = client.delete(f'/api/projects/{PROJECT_NAME}')
        release.set()
        _finish(task, patcher)
        assert response.status_code == 409
        assert os.path.isdir(os.path.join(project_with_translations, PROJECT_NAME))

    def test_delete_refused_while_other_process_holds_lock(self, client,
                                                          project_with_translations):
        TaskManager._instance = None
        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        child = hold_lock_in_child(project_dir)
        try:
            response = client.delete(f'/api/projects/{PROJECT_NAME}')
        finally:
            child.stdin.close()
            child.wait(timeout=10)
        assert response.status_code == 409
        assert '另一個程序' in response.get_json()['error']
        assert os.path.isfile(os.path.join(project_dir, 'project.json'))


class TestBackendModels:
    """Item 12: models, default model and auth hint come from the backend."""

    def test_model_api_uses_backend_models(self, client, project_with_translations):
        fake = FakeBackend(replies=[])
        with use_fake_backend(fake):
            data = client.get(f'/api/projects/{PROJECT_NAME}/model').get_json()
            assert data['available'] == [{'alias': 'fake-model', 'label': 'Fake',
                                          'description': ''}]
            assert client.put(f'/api/projects/{PROJECT_NAME}/model',
                              json={'model': 'sonnet'}).status_code == 400
            assert client.put(f'/api/projects/{PROJECT_NAME}/model',
                              json={'model': 'fake-model'}).status_code == 200
        pm = ProjectManager(os.path.join(project_with_translations, PROJECT_NAME))
        pm.load()
        assert pm.data.model == 'fake-model'

    def test_new_project_gets_backend_default_model(self, tmp_path):
        with use_fake_backend(FakeBackend(replies=[])):
            pm = ProjectManager(str(tmp_path / 'p'))
            pm.create('/x.epub', 'T', 'A', [])
        assert pm.data.model == 'fake-model'

    def test_stored_model_kept(self, project_with_translations):
        """An existing project's stored model is used as it is."""
        pm = ProjectManager(os.path.join(project_with_translations, PROJECT_NAME))
        pm.load()
        pm.set_model('opus')
        pm.save()
        fake = FakeBackend(responder=lambda r: '<p>譯</p>')
        TaskManager._instance = None
        with use_fake_backend(fake):
            task = TaskManager().start_translation(PROJECT_NAME, project_with_translations)
            task._coordinator.join(20)
        assert {r.model for r in fake.requests} == {'opus'}
        TaskManager._instance = None

    def test_pages_have_no_claude_specifics(self, client):
        with use_fake_backend(FakeBackend(replies=[])):
            for page in ('translation', 'review/chapter/ch1'):
                html = client.get(f'/project/{PROJECT_NAME}/{page}').get_data(as_text=True)
                assert 'claude' not in html.lower()
                assert 'sonnet' not in html
                assert 'fake: sign in again' in html


class TestTemplatesHandleErrors:
    """Items 11 and 22: rejected saves are visible; the retry button is reactive."""

    def test_review_save_and_approve_check_the_response(self, client):
        html = client.get(f'/project/{PROJECT_NAME}/review/chapter/ch1').get_data(as_text=True)
        assert html.count('showSaveError(segId, r, data);') == 2
        assert 'function showSaveError' in html
        assert '未儲存' in html

    def test_model_select_reverts_on_error(self, client):
        html = client.get(f'/project/{PROJECT_NAME}/translation').get_data(as_text=True)
        assert 'this.model = previous;' in html
        assert "this.error = data.error || '模型設定失敗'" in html

    def test_retry_button_reactive(self, client):
        html = client.get(f'/project/{PROJECT_NAME}/translation').get_data(as_text=True)
        assert 'failedCount: 0,' in html
        assert 'x-show="failedCount > 0' in html
        assert html.count('this.refreshFailedCount();') == 3  # complete, stopped, error

    def test_backend_error_event_handled(self, client):
        html = client.get(f'/project/{PROJECT_NAME}/translation').get_data(as_text=True)
        assert "addEventListener('backend_error'" in html


class TestTermExtractionStop:
    """Item 19: a rate limit keeps the terms of finished batches."""

    def test_partial_terms_kept_and_reported(self, client, project_with_translations,
                                             monkeypatch):
        from src.web.blueprints.glossary import _extraction_tasks
        monkeypatch.setattr('src.glossary.term_extractor.MAX_CHARS_PER_BATCH', 20)
        _extraction_tasks.pop(PROJECT_NAME, None)
        fake = FakeBackend(['[{"term": "Alpha", "translation": "甲", "category": "term"}]',
                            RateLimitError("You've hit your limit")])
        with use_fake_backend(fake):
            client.post(f'/api/projects/{PROJECT_NAME}/glossary/extract')
            _wait(lambda: not _extraction_tasks[PROJECT_NAME]['running'])
        status = client.get(f'/api/projects/{PROJECT_NAME}/glossary/extract/status').get_json()
        _extraction_tasks.pop(PROJECT_NAME, None)
        assert status['status'] == 'error'
        assert 'hit your limit' in status['error'] and '1 個術語' in status['error']
        terms = client.get(f'/api/projects/{PROJECT_NAME}/glossary').get_json()
        assert [t['term'] for t in terms] == ['Alpha']


class TestRound1Exact:
    """Exact behaviour of the round 1 web code (mutation survivors)."""

    def _task(self, projects_dir, state='running'):
        task = TranslationTask(task_id='t', project_name=PROJECT_NAME,
                               projects_dir=projects_dir, state=state)
        task.pm = ProjectManager(os.path.join(projects_dir, PROJECT_NAME))
        task.pm.load()
        return task

    def test_threads_alive_with_any_worker(self, project_with_translations):
        task = self._task(project_with_translations)
        assert task.threads_alive() is False
        done = threading.Thread(target=lambda: None)
        done.start()
        done.join()
        stop = threading.Event()
        running = threading.Thread(target=stop.wait)
        running.start()
        task._threads = [done, running]
        try:
            assert task.threads_alive() is True
        finally:
            stop.set()
            running.join()
        assert task.threads_alive() is False

    @pytest.mark.parametrize('state,owns', [('running', True), ('paused', True),
                                            ('stopped', False), ('completed', False)])
    def test_task_owns_state_by_state_alone(self, project_with_translations, state, owns):
        TaskManager._instance = None
        tm = TaskManager()
        task = self._task(project_with_translations, state)
        tm.tasks[PROJECT_NAME] = task
        assert (tm.get_project_manager(PROJECT_NAME) is task.pm) is owns
        assert (tm.get_or_load_pm(PROJECT_NAME, project_with_translations) is task.pm) is owns
        assert tm.task_active(PROJECT_NAME) is owns
        TaskManager._instance = None

    def test_auth_error_event(self, project_with_translations):
        task, patcher = _start(project_with_translations,
                               lambda r: (_ for _ in ()).throw(AuthError('expired')))
        _wait(lambda: task.state == 'paused')
        TaskManager().stop_translation(PROJECT_NAME)
        _finish(task, patcher)
        events = [e for e in task.progress.get_all() if e['event'] == 'auth_error']
        assert [e['data']['error'] for e in events] == ['expired']

    def test_exact_messages_and_registration(self, project_with_translations, monkeypatch):
        monkeypatch.setattr('src.web.tasks.STOP_WAIT_SECONDS', 0.1)
        in_flight, release = threading.Event(), threading.Event()

        def respond(request):
            in_flight.set()
            assert release.wait(10)
            return '<p>譯</p>'

        task, patcher = _start(project_with_translations, respond)
        assert task._coordinator.daemon is True
        assert in_flight.wait(10)
        TaskManager().stop_translation(PROJECT_NAME)
        with pytest.raises(ValueError) as info:
            TaskManager().start_translation(PROJECT_NAME, project_with_translations)
        assert str(info.value) == '上一個翻譯任務仍在結束中（等待進行中的段落），請稍後再試'
        release.set()
        _finish(task, patcher)
        TaskManager._instance = None
        empty = TaskManager().start_translation(PROJECT_NAME, project_with_translations,
                                                segment_ids=[])
        assert TaskManager().get_task(PROJECT_NAME) is empty
        TaskManager._instance = None

    def test_failure_report_exact(self, project_with_translations, caplog):
        import logging
        caplog.set_level(logging.INFO, logger='src.web.tasks')
        TaskManager._instance = None
        with patch.object(ProjectManager, 'save', side_effect=OSError('disk full')):
            task, patcher = _start(project_with_translations, lambda r: '<p>譯</p>',
                                   segment_ids=['ch1_seg0003'])
            _finish(task, patcher)
        message = 'disk full（1 個段落的結果未儲存：ch1_seg0003）'
        assert task.error == message
        assert [e for e in task.progress.get_all() if e['event'] == 'error'] == [
            {'event': 'error', 'data': {'error': message, 'unsaved': ['ch1_seg0003']}}]
        assert f'Translation task failed: {message}' in [r.getMessage() for r in caplog.records]

    def test_skip_logged(self, client, project_with_translations, caplog):
        import logging
        caplog.set_level(logging.INFO, logger='src.web.tasks')
        in_flight, release = threading.Event(), threading.Event()

        def respond(request):
            in_flight.set()
            assert release.wait(10)
            return '<p>譯</p>'

        task, patcher = _start(project_with_translations, respond,
                               segment_ids=['ch1_seg0003', 'ch2_seg0001'])
        assert in_flight.wait(10)
        client.post(f'/api/projects/{PROJECT_NAME}/segments/ch2_seg0001', json={'text': '人工'})
        release.set()
        _finish(task, patcher)
        assert '段落已在排隊期間被編輯，略過: ch2_seg0001' in [r.getMessage() for r in caplog.records]

    def test_worker_goes_on_after_a_skip(self, client, project_with_translations):
        in_flight, release = threading.Event(), threading.Event()

        def respond(request):
            if '>Second paragraph.<' in request.messages[-1].content:
                in_flight.set()
                assert release.wait(10)
            return '<p>機器譯文</p>'

        task, patcher = _start(project_with_translations, respond,
                               segment_ids=['ch1_seg0003', 'ch2_seg0001', 'ch2_seg0002'])
        assert in_flight.wait(10)
        client.post(f'/api/projects/{PROJECT_NAME}/segments/ch2_seg0001', json={'text': '人工'})
        release.set()
        _finish(task, patcher)
        assert _load(project_with_translations)['ch2_seg0002'].translated_text == '<p>機器譯文</p>'

    def test_unexpected_exception_logged(self, project_with_translations, caplog):
        import logging
        # Review round 2, item 5: logged by the shared entry point
        caplog.set_level(logging.ERROR, logger='src.core.translator')
        task, patcher = _start(project_with_translations,
                               lambda r: (_ for _ in ()).throw(KeyError('surprise')),
                               segment_ids=['ch1_seg0003'])
        _finish(task, patcher)
        assert '未預期的錯誤: ch1_seg0003' in [r.getMessage() for r in caplog.records]

    def test_review_responses_exact(self, client):
        url = f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0002'
        assert client.post(url, json={'text': '改'}).get_json() == \
            {'status': 'ok', 'segment_id': 'ch1_seg0002'}
        assert client.post(url + '/approve', json={}).get_json() == \
            {'status': 'ok', 'segment_id': 'ch1_seg0002'}
        response = client.post(f'/api/projects/{PROJECT_NAME}/segments/nope/retranslate')
        assert response.status_code == 404
        assert response.get_json() == {'error': 'Segment nope not found'}

    def test_retranslate_sends_its_own_segment(self, client):
        fake = FakeBackend(['<p>新譯文</p>'])
        with use_fake_backend(fake):
            client.post(f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003/retranslate')
        assert '>Second paragraph.<' in fake.prompts[0]

    def test_backend_unavailable_retranslate_exact(self, client, caplog):
        import logging
        caplog.set_level(logging.WARNING, logger='src.web.blueprints.review')
        with use_fake_backend(FakeBackend([BackendUnavailableError('gone')])):
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003/retranslate')
        assert response.get_json() == {'error': 'gone'}
        assert 'Backend unavailable during retranslation: gone' in \
            [r.getMessage() for r in caplog.records]

    def test_delete_refused_message_exact(self, client, project_with_translations):
        TaskManager._instance = None
        task = self._task(project_with_translations)
        TaskManager().tasks[PROJECT_NAME] = task
        response = client.delete(f'/api/projects/{PROJECT_NAME}')
        TaskManager._instance = None
        assert response.status_code == 409
        assert response.get_json() == {'error': '專案正在翻譯中，請先停止翻譯再刪除'}

    def test_extraction_status_exact(self, client, project_with_translations, monkeypatch):
        from src.web.blueprints.glossary import _extraction_tasks
        _extraction_tasks.pop(PROJECT_NAME, None)
        with use_fake_backend(FakeBackend(responder=lambda r: '[]')):
            client.post(f'/api/projects/{PROJECT_NAME}/glossary/extract')
            _wait(lambda: not _extraction_tasks[PROJECT_NAME]['running'])
        assert client.get(f'/api/projects/{PROJECT_NAME}/glossary/extract/status').get_json() \
            == {'status': 'completed', 'extracted_count': 0}
        monkeypatch.setattr('src.glossary.term_extractor.MAX_CHARS_PER_BATCH', 20)
        _extraction_tasks.pop(PROJECT_NAME, None)
        fake = FakeBackend(['[{"term": "Alpha", "translation": "甲", "category": "term"}]',
                            RateLimitError('limit')])
        with use_fake_backend(fake):
            client.post(f'/api/projects/{PROJECT_NAME}/glossary/extract')
            _wait(lambda: not _extraction_tasks[PROJECT_NAME]['running'])
        status = client.get(f'/api/projects/{PROJECT_NAME}/glossary/extract/status').get_json()
        _extraction_tasks.pop(PROJECT_NAME, None)
        assert status == {'status': 'error', 'error': '提取中斷：limit（已加入先前批次的 1 個術語）'}

    def test_failure_report_lists_several_ids(self, project_with_translations):
        TaskManager._instance = None
        both = threading.Barrier(2)

        def respond(request):
            both.wait(10)
            return '<p>譯</p>'

        with patch.object(ProjectManager, 'save', side_effect=OSError('disk full')):
            task, patcher = _start(project_with_translations, respond, num_workers=2,
                                   segment_ids=['ch1_seg0003', 'ch2_seg0001'])
            _finish(task, patcher)
        assert task.error in (
            'disk full（2 個段落的結果未儲存：ch1_seg0003, ch2_seg0001）',
            'disk full（2 個段落的結果未儲存：ch2_seg0001, ch1_seg0003）')


# --- Review round 2 ---------------------------------------------------------------

import subprocess as _subprocess
import sys as _sys

from src.core.project import project_mutex
from src.web.tasks import TranslationResult

_EDIT_IN_CHILD = '''
import sys
sys.path.insert(0, sys.argv[1])
from src.core.project import ProjectManager, ProjectLockedError
pm = ProjectManager(sys.argv[2])
pm.load()
pm.update_translation('ch1_seg0002', 'other process')
try:
    pm.save()
    print('saved')
except ProjectLockedError:
    print('locked')
'''


def _edit_in_child(project_dir):
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return _subprocess.run([_sys.executable, '-c', _EDIT_IN_CHILD, repo, project_dir],
                           capture_output=True, text=True, timeout=30).stdout.strip()


class TestRound2TransactionAcrossProcesses:
    """Item 1: a transaction holds the file lock from load to save."""

    def test_other_process_cannot_save_inside_a_transaction(self, project_with_translations):
        TaskManager._instance = None
        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        with TaskManager().transaction(PROJECT_NAME, project_with_translations) as pm:
            child = _edit_in_child(project_dir)
            pm.update_translation('ch1_seg0001', 'this process')
            pm.save()
        segs = _load(project_with_translations)
        # Without the lock the child saves and this save overwrites its edit
        assert child == 'locked'
        assert segs['ch1_seg0001'].translated_text == 'this process'
        assert segs['ch1_seg0002'].translated_text == '第一段。'
        assert lock_free_for_other_process(project_dir)

    def test_transaction_refused_while_other_process_holds_lock(self,
                                                                project_with_translations):
        from src.core.project import ProjectLockedError
        TaskManager._instance = None
        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        child = hold_lock_in_child(project_dir)
        try:
            with pytest.raises(ProjectLockedError):
                with TaskManager().transaction(PROJECT_NAME, project_with_translations):
                    pytest.fail('entered without the lock')
        finally:
            child.stdin.close()
            child.wait(timeout=10)

    def test_retranslate_refused_before_the_call(self, client, project_with_translations):
        TaskManager._instance = None
        fake = FakeBackend(['<p>譯</p>'])
        child = hold_lock_in_child(os.path.join(project_with_translations, PROJECT_NAME))
        try:
            with use_fake_backend(fake):
                response = client.post(
                    f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003/retranslate')
        finally:
            child.stdin.close()
            child.wait(timeout=10)
        assert response.status_code == 409
        assert fake.requests == []


class TestRound2DeleteGuardInsideMutex:
    """Item 2: the task check and the deletion are one step under the mutex."""

    def test_task_registered_while_delete_waits(self, client, project_with_translations):
        TaskManager._instance = None
        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        in_flight, release = threading.Event(), threading.Event()

        def respond(request):
            in_flight.set()
            assert release.wait(10)
            return '<p>譯</p>'

        result = {}
        patcher = use_fake_backend(FakeBackend(responder=respond))
        patcher.start()
        try:
            mutex = project_mutex(project_dir)
            with mutex:
                t = threading.Thread(target=lambda: result.setdefault(
                    'r', client.application.test_client().delete(
                        f'/api/projects/{PROJECT_NAME}')))
                t.start()
                _time.sleep(0.5)  # the delete request is now waiting for the mutex
                assert 'r' not in result
                # A start registers its task meanwhile (the mutex is re-entrant)
                task = TaskManager().start_translation(PROJECT_NAME, project_with_translations)
            t.join(timeout=20)
            assert in_flight.wait(10)
        finally:
            release.set()
        task._coordinator.join(timeout=20)
        patcher.stop()
        TaskManager._instance = None
        assert result['r'].status_code == 409
        assert os.path.isfile(os.path.join(project_dir, 'project.json'))


class TestRound2FailureLogAtAcceptance:
    """Items 10, 17: a failure log is written or removed only where an outcome
    is accepted and saved, or an edit is saved."""

    def _log(self, projects_dir, seg_id='ch1_seg0003'):
        return failure_log_path(os.path.join(projects_dir, PROJECT_NAME, 'log'), seg_id)

    def test_stale_success_keeps_newer_failure_log(self, client, project_with_translations):
        in_flight, release = threading.Event(), threading.Event()
        calls = []

        def respond(request):
            if '>Second paragraph.<' in request.messages[-1].content:
                calls.append(1)
                if len(calls) == 1:  # the task's call: succeeds late
                    in_flight.set()
                    assert release.wait(10)
                    return '<p>機器譯文</p>'
                raise TransientError('exit 1', raw='RAW')  # the newer retranslation
            return '<p>譯</p>'

        task, patcher = _start(project_with_translations, respond,
                               segment_ids=['ch1_seg0003'])
        assert in_flight.wait(10)
        url = f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003'
        assert client.post(url, json={'text': '人工'}).status_code == 200
        assert client.post(url + '/retranslate').status_code == 500
        assert os.path.exists(self._log(project_with_translations))
        release.set()
        _finish(task, patcher)
        seg = _load(project_with_translations)['ch1_seg0003']
        assert seg.translated_text == '人工' and seg.issues[0].code == 'transient'
        assert os.path.exists(self._log(project_with_translations))

    def test_stale_failure_writes_no_log(self, client, project_with_translations):
        in_flight, release = threading.Event(), threading.Event()

        def respond(request):
            in_flight.set()
            assert release.wait(10)
            raise TransientError('exit 1', raw='RAW')

        task, patcher = _start(project_with_translations, respond,
                               segment_ids=['ch1_seg0003'])
        assert in_flight.wait(10)
        assert client.post(f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003',
                           json={'text': '人工'}).status_code == 200
        release.set()
        _finish(task, patcher)
        seg = _load(project_with_translations)['ch1_seg0003']
        assert seg.translated_text == '人工' and seg.issues == []
        assert not os.path.exists(self._log(project_with_translations))

    def test_accepted_outcomes_record_the_log(self, project_with_translations):
        task, patcher = _start(project_with_translations, _fail_on('Second paragraph.'))
        _finish(task, patcher)
        assert os.path.exists(self._log(project_with_translations))
        task, patcher = _start(project_with_translations, lambda r: '<p>譯</p>',
                               retry_failed=True)
        _finish(task, patcher)
        assert not os.path.exists(self._log(project_with_translations))

    def test_success_whose_save_is_refused_keeps_the_log(self, client,
                                                          project_with_translations):
        TaskManager._instance = None
        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        url = f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003/retranslate'
        with use_fake_backend(FakeBackend([TransientError('exit 1')] * 3)):
            assert client.post(url).status_code == 500
        assert os.path.exists(self._log(project_with_translations))
        children = []

        def respond(request):
            # Another process takes the lock while the call is in flight
            children.append(hold_lock_in_child(project_dir))
            return '<p>機器譯文</p>'

        try:
            with use_fake_backend(FakeBackend(responder=respond)):
                response = client.post(url)
        finally:
            for child in children:
                child.stdin.close()
                child.wait(timeout=10)
        assert response.status_code == 409
        assert os.path.exists(self._log(project_with_translations))
        assert _load(project_with_translations)['ch1_seg0003'].status == SegmentStatus.FAILED

    def test_stale_retranslation_writes_no_log(self, client, project_with_translations):
        TaskManager._instance = None
        url = f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003'

        def respond(request):
            # An edit lands while the call is in flight
            assert client.application.test_client().post(
                url, json={'text': '人工'}).status_code == 200
            raise RefusalError('no')

        from src.llm import RefusalError
        with use_fake_backend(FakeBackend(responder=respond)):
            assert client.post(url + '/retranslate').status_code == 409
        assert not os.path.exists(self._log(project_with_translations))

    @pytest.mark.parametrize('suffix', ['', '/approve'])
    def test_edit_removes_the_log_inside_the_transaction(self, client,
                                                         project_with_translations, suffix):
        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        seen = []

        def remove(log_dir, segment_id):
            seen.append((segment_id, project_mutex(project_dir)._is_owned(),
                         not lock_free_for_other_process(project_dir)))

        with patch('src.web.blueprints.review.remove_failure_log', side_effect=remove):
            client.post(f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003{suffix}',
                        json={'text': '人工'})
        assert seen == [('ch1_seg0003', True, True)]


class TestRound2UnsavedReport:
    """Item 20: only accepted results that were not saved are reported."""

    def test_stale_result_not_reported(self, client, project_with_translations):
        in_flight, release = threading.Event(), threading.Event()

        def respond(request):
            in_flight.set()
            assert release.wait(10)
            return '<p>機器譯文</p>'

        task, patcher = _start(project_with_translations, respond,
                               segment_ids=['ch1_seg0003'])
        assert in_flight.wait(10)
        assert client.post(f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0003',
                           json={'text': '人工'}).status_code == 200
        with patch.object(ProjectManager, 'save', side_effect=OSError('disk full')):
            release.set()
            _finish(task, patcher)
        assert task.state == 'failed'
        error = [e for e in task.progress.get_all() if e['event'] == 'error'][0]
        assert error['data'] == {'error': 'disk full', 'unsaved': []}

    def test_skipped_result_in_queue_not_reported(self, project_with_translations):
        task = TranslationTask(task_id='t', project_name=PROJECT_NAME,
                               projects_dir=project_with_translations)
        _setup_task_pm(task, project_with_translations)  # a coordinator's task has one
        task.result_queue.put(TranslationResult('ch1_seg0002', None, 0))
        outcome = TranslationOutcome(text='x', status=SegmentStatus.TRANSLATED, issues=[],
                                     model='m', usage=None)
        task.result_queue.put(TranslationResult('ch1_seg0003', outcome, 0))
        TaskManager()._fail_task(task, RuntimeError('boom'), [])
        assert task.error == 'boom（1 個段落的結果未儲存：ch1_seg0003）'


class TestRound2RunErrorVisible:
    """Item 11: the status endpoint gives the run-level error after the pause."""

    def test_status_after_pause(self, client, project_with_translations):
        task, patcher = _start(project_with_translations,
                               lambda r: (_ for _ in ()).throw(
                                   BackendUnavailableError('could not be started')))
        _wait(lambda: task.state == 'paused')
        status = client.get(f'/api/projects/{PROJECT_NAME}/translate/status').get_json()
        assert status['state'] == 'paused'
        assert status['error'] == 'could not be started'
        TaskManager().stop_translation(PROJECT_NAME)
        _finish(task, patcher)

    def test_resume_clears_it(self, client, project_with_translations):
        calls = []

        def respond(request):
            calls.append(1)
            if len(calls) == 1:
                raise RateLimitError("You've hit your limit")
            return '<p>譯</p>'

        task, patcher = _start(project_with_translations, respond)
        _wait(lambda: task.state == 'paused')
        assert task.error == "You've hit your limit"
        assert TaskManager().resume_translation(PROJECT_NAME)
        assert task.error is None
        _finish(task, patcher)
        assert task.state == 'completed'

    def test_page_shows_the_status_error(self, client):
        html = client.get(f'/project/{PROJECT_NAME}/translation').get_data(as_text=True)
        assert 'this.error = data.error;' in html


class TestRound2StoredModelOption:
    """Item 12: a stored model the backend does not list is shown as an option."""

    def test_unlisted_stored_model_shown(self, client):
        html = client.get(f'/project/{PROJECT_NAME}/translation').get_data(as_text=True)
        assert ("if (!this.availableModels.some(m => m.alias === this.model))" in html)
        assert "{ alias: this.model, label: this.model, description: '目前設定，後端未列出' }" \
            in html

    def test_model_unchanged_by_reading(self, client, project_with_translations):
        TaskManager._instance = None
        with use_fake_backend(FakeBackend()):
            data = client.get(f'/api/projects/{PROJECT_NAME}/model').get_json()
        assert data['current'] == 'sonnet'
        assert [m['alias'] for m in data['available']] == ['fake-model']
        assert _load(project_with_translations)  # loads; nothing saved
        pm = ProjectManager(os.path.join(project_with_translations, PROJECT_NAME))
        pm.load()
        assert pm.data.model == 'sonnet'


class TestRound2StartChecks:
    """Item 16: mutex before file lock; a missing project is 404, no lock file."""

    def test_missing_project(self, client, project_with_translations):
        TaskManager._instance = None
        response = client.post('/api/projects/nope/translate/start', json={})
        assert response.status_code == 404
        assert response.get_json() == {'error': '專案不存在'}
        assert not os.path.exists(os.path.join(project_with_translations, 'nope'))

    def test_directory_without_project_file(self, client, project_with_translations):
        TaskManager._instance = None
        os.makedirs(os.path.join(project_with_translations, 'empty'))
        response = client.post('/api/projects/empty/translate/start', json={})
        assert response.status_code == 404
        assert os.listdir(os.path.join(project_with_translations, 'empty')) == []

    def test_lock_taken_under_the_mutex(self, project_with_translations):
        from src.core.project import ProjectLock
        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        owned = []
        real = ProjectLock.acquire

        def acquire(self):
            owned.append(project_mutex(project_dir)._is_owned())
            real(self)

        with patch.object(ProjectLock, 'acquire', acquire):
            task, patcher = _start(project_with_translations, lambda r: '<p>譯</p>')
            _finish(task, patcher)
        assert owned and owned[0] is True


# --- Review round 3 ---------------------------------------------------------------

from src.core.translator import MAX_CONSECUTIVE_UNEXPECTED


def _dir_snapshot(path):
    """Name, size and modification time of every file under path."""
    out = {}
    for root, _, files in os.walk(path):
        for name in files:
            st = os.stat(os.path.join(root, name))
            out[os.path.relpath(os.path.join(root, name), path)] = (st.st_size, st.st_mtime_ns)
    return out


class TestRound3ReplaceProjectUnderLock:
    """Item 1: replacing a project of the same name takes the mutex and the
    lock first; a refused request leaves its files untouched."""

    NAME = 'replaceme'

    def _create(self, client, sample_epub):
        response = client.post('/api/projects', json={
            'epub_path': sample_epub, 'project_name': self.NAME})
        assert response.status_code == 200, response.get_json()
        return response

    def test_refused_while_other_process_holds_lock(self, client, project_with_translations,
                                                    sample_epub):
        TaskManager._instance = None
        self._create(client, sample_epub)
        project_dir = os.path.join(project_with_translations, self.NAME)
        before = _dir_snapshot(project_dir)
        child = hold_lock_in_child(project_dir)
        try:
            response = client.post('/api/projects', json={
                'epub_path': sample_epub, 'project_name': self.NAME})
        finally:
            child.stdin.close()
            child.wait(timeout=10)
        assert response.status_code == 409
        assert '另一個程序' in response.get_json()['error']
        after = _dir_snapshot(project_dir)
        # The child wrote its PID into the lock file; nothing else changed
        before.pop('project.lock'), after.pop('project.lock')
        assert after == before

    def test_refused_while_task_active(self, client, project_with_translations, sample_epub):
        TaskManager._instance = None
        self._create(client, sample_epub)
        project_dir = os.path.join(project_with_translations, self.NAME)
        before = _dir_snapshot(project_dir)
        in_flight, release = threading.Event(), threading.Event()

        def respond(request):
            in_flight.set()
            assert release.wait(10)
            return '<p>譯</p>'

        with use_fake_backend(FakeBackend(responder=respond)):
            task = TaskManager().start_translation(self.NAME, project_with_translations)
            try:
                assert in_flight.wait(10)
                response = client.post('/api/projects', json={
                    'epub_path': sample_epub, 'project_name': self.NAME})
                during = _dir_snapshot(project_dir)
            finally:
                TaskManager().stop_translation(self.NAME)
                release.set()
                task._coordinator.join(20)
        TaskManager._instance = None
        assert response.status_code == 409
        assert response.get_json() == {'error': '專案正在翻譯中，請先停止翻譯再重新建立'}
        # The task's log and lock file aside, nothing changed
        kept = ('log', 'project.lock')
        assert {k: v for k, v in during.items() if not k.startswith(kept)} == \
            {k: v for k, v in before.items() if not k.startswith(kept)}

    def test_lock_held_while_files_are_written(self, client, project_with_translations,
                                               sample_epub):
        TaskManager._instance = None
        self._create(client, sample_epub)
        project_dir = os.path.join(project_with_translations, self.NAME)
        seen = []
        import shutil as _shutil
        real_copy = _shutil.copy2

        def copy(src, dst):
            seen.append((project_mutex(project_dir)._is_owned(),
                         lock_free_for_other_process(project_dir)))
            return real_copy(src, dst)

        with patch('src.web.blueprints.projects.shutil.copy2', copy):
            self._create(client, sample_epub)
        assert seen == [(True, False)]
        assert lock_free_for_other_process(project_dir)


class TestRound3StoppedTaskRestartable:
    """Item 2: a stopped task offers Start; the page reacts to `stopped`."""

    def test_start_after_stopped(self, client, project_with_translations):
        in_flight, release = threading.Event(), threading.Event()

        def respond(request):
            in_flight.set()
            assert release.wait(10)
            return '<p>譯</p>'

        task, patcher = _start(project_with_translations, respond)
        assert in_flight.wait(10)
        TaskManager().stop_translation(PROJECT_NAME)
        release.set()
        task._coordinator.join(20)
        assert task.state == 'stopped'
        tm = TaskManager()
        response = client.post(f'/api/projects/{PROJECT_NAME}/translate/start', json={})
        assert response.status_code == 200
        tm.get_task(PROJECT_NAME)._coordinator.join(20)
        patcher.stop()
        TaskManager._instance = None

    def test_page_offers_start_when_stopped(self, client):
        html = client.get(f'/project/{PROJECT_NAME}/translation').get_data(as_text=True)
        assert ("<template x-if=\"state === 'idle' || state === 'completed' "
                "|| state === 'failed' || state === 'stopped'\">") in html
        assert "'stopped': '已停止'," in html

    def test_page_handles_stopped_event(self, client):
        html = client.get(f'/project/{PROJECT_NAME}/translation').get_data(as_text=True)
        start = html.index("addEventListener('stopped'")
        handler = html[start:html.index('});', start)]
        assert "this.state = 'stopped';" in handler
        assert 'this.eventSource.close();' in handler
        assert 'this.eventSource = null;' in handler


class TestRound3WorkerStartFailure:
    """Item 5: a failing worker start joins only the started workers."""

    def test_second_worker_start_fails(self, project_with_translations):
        real_start = threading.Thread.start
        calls = []

        def start(self):
            calls.append(self)
            if len(calls) == 3:  # coordinator, first worker, then this one
                raise RuntimeError('no more threads')
            return real_start(self)

        hold = threading.Event()

        def respond(request):
            assert hold.wait(10)
            return '<p>譯</p>'

        with patch.object(threading.Thread, 'start', start):
            task, patcher = _start(project_with_translations, respond, num_workers=3)
            hold.set()
            _finish(task, patcher)
        assert task.state == 'failed'
        assert task.error.startswith('no more threads')
        assert task.active_workers == 0
        assert len(task._threads) == 1 and not task._threads[0].is_alive()
        assert lock_free_for_other_process(os.path.join(project_with_translations,
                                                        PROJECT_NAME))


class TestRound3InitialStatusError:
    """Item 6: the initial SSE status carries the retained error, and no event
    sent after the snapshot is lost."""

    def _task(self, projects_dir, **kwargs):
        TaskManager._instance = None
        task = TranslationTask(task_id='t', project_name=PROJECT_NAME,
                               projects_dir=projects_dir, total=3, **kwargs)
        TaskManager().tasks[PROJECT_NAME] = task
        return task

    def test_status_includes_error(self, client, project_with_translations):
        task = self._task(project_with_translations, state='paused',
                          error='找不到程式')
        response = client.get(f'/api/projects/{PROJECT_NAME}/translate/stream',
                              buffered=False)
        first = next(iter(response.response)).decode('utf-8')
        response.close()
        assert first == ('event: status\ndata: {"state": "paused", "completed": 0, '
                         '"total": 3, "error": "找不到程式"}\n\n')
        TaskManager._instance = None

    def test_event_after_snapshot_delivered(self, client, project_with_translations):
        task = self._task(project_with_translations, state='running')
        response = client.get(f'/api/projects/{PROJECT_NAME}/translate/stream',
                              buffered=False)
        chunks = iter(response.response)
        assert next(chunks).startswith(b'event: status')
        task.state = 'completed'
        task.progress.put({'event': 'complete', 'data': {'total_translated': 3}})
        rest = b''.join(chunks).decode('utf-8')
        assert 'event: complete' in rest
        TaskManager._instance = None

    def test_page_shows_initial_error(self, client):
        html = client.get(f'/project/{PROJECT_NAME}/translation').get_data(as_text=True)
        start = html.index("addEventListener('status'")
        handler = html[start:html.index('});', start)]
        assert 'if (data.error) this.error = data.error;' in handler


class TestRound3StaleResultInFailureDrain:
    """Item 8: a stale result drained after a coordinator failure is not
    reported as unsaved."""

    def test_edited_in_flight_segment_not_unsaved(self, project_with_translations):
        in_flight, release = threading.Event(), threading.Event()

        def respond(request):
            if '>Chapter 2<' in request.messages[-1].content:
                in_flight.set()
                assert release.wait(10)
            return '<p>譯</p>'

        TaskManager._instance = None
        with patch.object(ProjectManager, 'save', side_effect=OSError('disk full')):
            task, patcher = _start(project_with_translations, respond,
                                   segment_ids=['ch1_seg0003', 'ch2_seg0001'],
                                   num_workers=2)
            assert in_flight.wait(10)
            task.pm.update_translation('ch2_seg0001', '人工')  # edited while in flight
            release.set()
            _finish(task, patcher)
        assert task.state == 'failed'
        error = [e for e in task.progress.get_all() if e['event'] == 'error'][0]
        assert error['data'] == {'error': 'disk full（1 個段落的結果未儲存：ch1_seg0003）',
                                 'unsaved': ['ch1_seg0003']}


class TestRound3UnexpectedStreakWeb:
    """Item 9: five unexpected failures in a row end the web run."""

    def _project(self, projects_dir, n):
        project_dir = os.path.join(projects_dir, PROJECT_NAME)
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test Book', 'Author', [
            Segment(id=f'ch1_seg{i:04d}', html=f'<p>Line {i}.</p>', text=f'Line {i}.',
                    word_count=2, segment_type=SegmentType.TEXT) for i in range(1, n + 1)])

    def test_run_stops_after_five(self, project_with_translations):
        self._project(project_with_translations, 8)
        holder, calls = {}, []

        def respond(request):
            calls.append(1)
            if len(calls) > MAX_CONSECUTIVE_UNEXPECTED:
                # The sixth call ends only once the run has been stopped
                _wait(lambda: 'task' in holder and holder['task'].stop_event.is_set())
            return {}['missing']

        task, patcher = _start(project_with_translations, respond)
        holder['task'] = task
        _finish(task, patcher)
        assert len(calls) == 6
        assert task.state == 'failed'
        assert task.error == ("連續 5 個段落發生未預期的錯誤，翻譯已停止"
                              "（最後一個：未預期的錯誤：KeyError: 'missing'）"
                              "（1 個段落的結果未儲存：ch1_seg0006）")
        segs = _load(project_with_translations)
        statuses = [segs[f'ch1_seg{i:04d}'].status for i in range(1, 9)]
        assert statuses[:5] == [SegmentStatus.FAILED] * 5
        assert statuses[5:] == [SegmentStatus.PENDING] * 3
        error = [e for e in task.progress.get_all() if e['event'] == 'error'][0]
        assert error['data'] == {'error': task.error, 'unsaved': ['ch1_seg0006']}

    def test_success_resets_the_count(self, project_with_translations):
        self._project(project_with_translations, 9)

        def respond(request):
            if '>Line 5.<' in request.messages[-1].content:
                return '<p>譯</p>'
            return {}['missing']

        task, patcher = _start(project_with_translations, respond)
        _finish(task, patcher)
        assert task.state == 'completed'
        segs = _load(project_with_translations)
        assert [s.status for s in segs.values()].count(SegmentStatus.FAILED) == 8


class TestRound3MissingProject:
    """Third reviewer: no lock file and 404 for a directory without
    project.json, also when it is deleted during a re-translation."""

    @pytest.mark.parametrize('method,path,body', [
        ('post', '/segments/ch1_seg0001', {'text': 'x'}),
        ('post', '/segments/ch1_seg0001/approve', {}),
        ('post', '/segments/ch1_seg0001/retranslate', None),
        ('delete', '', None),
    ])
    def test_directory_without_project_file(self, client, project_with_translations,
                                            method, path, body):
        TaskManager._instance = None
        os.makedirs(os.path.join(project_with_translations, 'empty'))
        response = getattr(client, method)(f'/api/projects/empty{path}', json=body)
        assert response.status_code == 404
        assert response.get_json() == {'error': '專案不存在'}
        assert os.listdir(os.path.join(project_with_translations, 'empty')) == []

    def test_set_model_creates_no_lock_file(self, client, project_with_translations):
        TaskManager._instance = None
        os.makedirs(os.path.join(project_with_translations, 'empty'))
        response = client.put('/api/projects/empty/model', json={'model': 'sonnet'})
        assert response.status_code == 404
        assert os.listdir(os.path.join(project_with_translations, 'empty')) == []

    def test_deleted_during_retranslation(self, client, project_with_translations):
        import shutil as _shutil
        TaskManager._instance = None
        project_dir = os.path.join(project_with_translations, PROJECT_NAME)

        def respond(request):
            _shutil.rmtree(project_dir)
            return '<p>譯</p>'

        with use_fake_backend(FakeBackend(responder=respond)):
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/segments/ch1_seg0002/retranslate')
        assert response.status_code == 404
        assert response.get_json() == {'error': '專案不存在'}
        assert not os.path.exists(os.path.join(project_dir, 'project.json'))
        assert not os.path.exists(os.path.join(project_dir, 'project.lock'))
