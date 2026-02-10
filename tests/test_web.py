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

        def fake_translate(*args, **kwargs):
            raise RateLimitError("You've hit your limit · resets 8pm (Asia/Taipei)")

        with patch.object(TaskManager, '__new__', return_value=tm):
            with patch('src.web.tasks.TranslationEngine') as MockEngine:
                MockEngine.return_value.translate.side_effect = fake_translate

                # Run _run_translation in a thread so we can inspect state while it blocks
                thread = threading.Thread(target=tm._run_translation, args=(task,))
                thread.start()

                # Wait for task to pause (the thread will block on _condition.wait)
                thread.join(timeout=5)
                assert task.state == 'paused'

                # Drain queue and check for rate_limit event
                events = []
                while not task.progress_queue.empty():
                    events.append(task.progress_queue.get_nowait())

                rate_limit_events = [e for e in events if e['event'] == 'rate_limit']
                assert len(rate_limit_events) == 1
                assert "You've hit your limit" in rate_limit_events[0]['data']['error']

                # Stop the task so the thread exits
                with task._lock:
                    task.state = 'stopped'
                    task._condition.notify_all()
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
                with task._lock:
                    task.state = 'running'
                    task._condition.notify_all()
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
        task._thread = dead_thread

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

        # Create a fake stopped task with a thread
        old_thread = MagicMock()
        old_thread.is_alive.return_value = False

        task = TranslationTask(
            task_id='test_old',
            project_name=PROJECT_NAME,
            projects_dir=project_with_translations,
            state='stopped',
            total=3,
        )
        task._thread = old_thread
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
        """set_model API should update the running task's PM so mark_translated won't overwrite it."""
        TaskManager._instance = None
        app = create_app(projects_dir=project_with_translations)
        app.config['TESTING'] = True
        tm = TaskManager()

        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        save_lock = threading.Lock()
        shared_pm = ProjectManager(project_dir, save_lock=save_lock)
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

            # Simulate mark_translated (which saves pm.data to disk)
            shared_pm.mark_translated('ch1_seg0003', '測試翻譯')

            # Verify model is still opus on disk after save
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

        call_count = 0

        def fake_translate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Stop the task after first call
                with task._lock:
                    task.state = 'stopped'
                    task._condition.notify_all()
            return '翻譯結果'

        with patch.object(TaskManager, '__new__', return_value=tm):
            with patch('src.web.tasks.TranslationEngine') as MockEngine:
                MockEngine.return_value.translate.side_effect = fake_translate
                MockEngine.return_value.model = 'sonnet'

                thread = threading.Thread(target=tm._run_translation, args=(task,))
                thread.start()
                thread.join(timeout=10)

                # Drain queue and check for stopped event
                events = []
                while not task.progress_queue.empty():
                    events.append(task.progress_queue.get_nowait())

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

        # Push a stopped event
        task.progress_queue.put({'event': 'stopped', 'data': {
            'completed': 1, 'remaining': 2
        }})

        with app.test_client() as client:
            response = client.get(f'/api/projects/{PROJECT_NAME}/translate/stream')
            data = response.data.decode()
            assert 'event: stopped' in data

        TaskManager._instance = None


class TestRetranslateModel:

    def test_retranslate_uses_project_model(self, project_with_translations):
        """Retranslate endpoint should use the project's configured model, not the default."""
        # Set project model to opus
        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        pm = ProjectManager(project_dir)
        pm.load()
        pm.set_model('opus')

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
        """When project.json model changes on disk during translation, engine should pick it up."""
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

        project_dir = os.path.join(project_with_translations, PROJECT_NAME)
        models_seen = []

        # Patch mark_translated to inject a model change on disk after first save
        original_mark = ProjectManager.mark_translated
        mark_count = [0]

        def mark_with_model_switch(self, segment_id, translated_text):
            original_mark(self, segment_id, translated_text)
            mark_count[0] += 1
            # After first mark_translated, change model on disk
            if mark_count[0] == 1:
                with open(self.project_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                data['model'] = 'opus'
                with open(self.project_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False)

        def tracking_translate(self_engine, *args, **kwargs):
            """Track which model the engine has when translate is called."""
            models_seen.append(self_engine.model)
            return '翻譯結果'

        with patch.object(TaskManager, '__new__', return_value=tm):
            with patch.object(ProjectManager, 'mark_translated', mark_with_model_switch):
                with patch('src.web.tasks.TranslationEngine') as MockEngine:
                    # Make each TranslationEngine() call return a real-ish mock
                    # that tracks its own model attribute
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

        with patch.object(TaskManager, '__new__', return_value=tm):
            with patch('src.web.tasks.TranslationEngine') as MockEngine:
                MockEngine.return_value.translate.return_value = '翻譯結果'
                # model is 'sonnet' on disk; engine reads from disk each iteration
                MockEngine.return_value.model = 'sonnet'

                thread = threading.Thread(target=tm._run_translation, args=(task,))
                thread.start()
                thread.join(timeout=10)

                assert task.state == 'completed'

                events = []
                while not task.progress_queue.empty():
                    events.append(task.progress_queue.get_nowait())

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
                task._thread = thread
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
                task._thread = thread
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

        # Create a thread that stays alive (blocked on condition)
        alive_event = threading.Event()
        def keep_alive():
            alive_event.wait()
        alive_thread = threading.Thread(target=keep_alive, daemon=True)
        alive_thread.start()
        task._thread = alive_thread
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

    def test_start_workers_capped_at_8(self, project_with_translations):
        """Workers should be capped at MAX_WORKERS (8)."""
        app = create_app(projects_dir=project_with_translations)
        app.config['TESTING'] = True
        TaskManager._instance = None

        with app.test_client() as client:
            response = client.post(
                f'/api/projects/{PROJECT_NAME}/translate/start',
                data=json.dumps({'workers': 20}),
                content_type='application/json',
            )
            assert response.status_code == 200
            data = response.get_json()
            assert data['workers'] == 8

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
                events = []
                while not task.progress_queue.empty():
                    events.append(task.progress_queue.get_nowait())
                rate_limit_events = [e for e in events if e['event'] == 'rate_limit']
                assert len(rate_limit_events) >= 1

                # Resume
                with task._lock:
                    task.state = 'running'
                    task._condition.notify_all()
                thread.join(timeout=10)

                assert task.state == 'completed'
