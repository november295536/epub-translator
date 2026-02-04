"""Tests for Flask web review server."""

import json
import os
import pytest

from src.web.server import create_app
from src.core.project import ProjectManager
from src.core.segmenter import Segment


@pytest.fixture
def project_with_translations(tmp_path):
    """Create a project with some translated segments for testing."""
    project_dir = str(tmp_path / 'web_project')
    os.makedirs(project_dir, exist_ok=True)

    segments = [
        Segment(id='ch1_seg0001', html='<h1>Title</h1>', text='Title',
                word_count=1, segment_type='heading'),
        Segment(id='ch1_seg0002', html='<p>First paragraph.</p>',
                text='First paragraph.', word_count=2, segment_type='text'),
        Segment(id='ch1_seg0003', html='<p>Second paragraph.</p>',
                text='Second paragraph.', word_count=2, segment_type='text'),
        Segment(id='ch2_seg0001', html='<h2>Chapter 2</h2>',
                text='Chapter 2', word_count=2, segment_type='heading'),
        Segment(id='ch2_seg0002', html='<p>Chapter 2 content.</p>',
                text='Chapter 2 content.', word_count=3, segment_type='text'),
    ]

    pm = ProjectManager(project_dir)
    pm.create('/path/to/book.epub', 'Test Book', 'Author', segments)

    # Translate some segments
    pm.mark_translated('ch1_seg0001', '標題')
    pm.mark_translated('ch1_seg0002', '第一段。')

    return project_dir


@pytest.fixture
def client(project_with_translations):
    app = create_app(project_with_translations)
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


class TestWebServer:

    def test_index_page(self, client):
        response = client.get('/')
        assert response.status_code == 200
        assert 'Test Book' in response.data.decode()

    def test_chapter_view(self, client):
        response = client.get('/chapter/ch1')
        assert response.status_code == 200
        data = response.data.decode()
        assert 'ch1_seg0001' in data
        assert '標題' in data

    def test_update_segment(self, client):
        response = client.post(
            '/api/segment/ch1_seg0002',
            data=json.dumps({'text': '更新的翻譯'}),
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'

    def test_update_segment_missing_text(self, client):
        response = client.post(
            '/api/segment/ch1_seg0002',
            data=json.dumps({}),
            content_type='application/json',
        )
        assert response.status_code == 400

    def test_update_segment_not_found(self, client):
        response = client.post(
            '/api/segment/nonexistent',
            data=json.dumps({'text': 'test'}),
            content_type='application/json',
        )
        assert response.status_code == 404

    def test_approve_segment(self, client):
        response = client.post(
            '/api/segment/ch1_seg0002/approve',
            data=json.dumps({'text': '審閱後的翻譯'}),
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'

    def test_approve_segment_not_found(self, client):
        response = client.post(
            '/api/segment/nonexistent/approve',
            data=json.dumps({}),
            content_type='application/json',
        )
        assert response.status_code == 404

    def test_api_progress(self, client):
        response = client.get('/api/progress')
        assert response.status_code == 200
        data = response.get_json()
        assert 'total' in data
        assert 'translated' in data
        assert data['total'] == 5

    def test_segment_persists_after_update(self, client, project_with_translations):
        client.post(
            '/api/segment/ch1_seg0003',
            data=json.dumps({'text': '永久翻譯'}),
            content_type='application/json',
        )

        # Verify by loading the project directly
        pm = ProjectManager(project_with_translations)
        pm.load()
        seg = [s for s in pm.data.segments if s.segment_id == 'ch1_seg0003'][0]
        assert seg.translated_text == '永久翻譯'

    def test_approve_persists_status(self, client, project_with_translations):
        client.post(
            '/api/segment/ch1_seg0002/approve',
            data=json.dumps({}),
            content_type='application/json',
        )

        pm = ProjectManager(project_with_translations)
        pm.load()
        seg = [s for s in pm.data.segments if s.segment_id == 'ch1_seg0002'][0]
        assert seg.status == 'reviewed'
