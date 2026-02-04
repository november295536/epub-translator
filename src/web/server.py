"""Flask web server for translation review."""

from __future__ import annotations

import os
from flask import Flask, render_template, request, jsonify

from src.core.project import ProjectManager


def create_app(project_dir: str) -> Flask:
    """Create and configure the Flask app for reviewing translations."""
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
        static_folder=os.path.join(os.path.dirname(__file__), 'static'),
    )
    app.config['PROJECT_DIR'] = project_dir

    def get_pm() -> ProjectManager:
        pm = ProjectManager(app.config['PROJECT_DIR'])
        pm.load()
        return pm

    @app.route('/')
    def index():
        pm = get_pm()
        progress = pm.get_progress()
        chapters = pm.get_chapters()
        return render_template('review.html',
                               title=pm.data.title,
                               author=pm.data.author,
                               progress=progress,
                               chapters=chapters)

    @app.route('/chapter/<chapter_id>')
    def chapter_view(chapter_id):
        pm = get_pm()
        segments = pm.get_segments_by_chapter(chapter_id)
        progress = pm.get_progress()
        chapters = pm.get_chapters()
        return render_template('review.html',
                               title=pm.data.title,
                               author=pm.data.author,
                               progress=progress,
                               chapters=chapters,
                               current_chapter=chapter_id,
                               segments=segments)

    @app.route('/api/segment/<segment_id>', methods=['POST'])
    def update_segment(segment_id):
        pm = get_pm()
        data = request.get_json()
        if not data or 'text' not in data:
            return jsonify({'error': 'Missing text field'}), 400

        try:
            pm.update_translation(segment_id, data['text'])
            return jsonify({'status': 'ok', 'segment_id': segment_id})
        except ValueError as e:
            return jsonify({'error': str(e)}), 404

    @app.route('/api/segment/<segment_id>/approve', methods=['POST'])
    def approve_segment(segment_id):
        pm = get_pm()
        data = request.get_json() or {}
        text = data.get('text', '')

        try:
            pm.mark_reviewed(segment_id, text=text)
            return jsonify({'status': 'ok', 'segment_id': segment_id})
        except ValueError as e:
            return jsonify({'error': str(e)}), 404

    @app.route('/api/progress')
    def api_progress():
        pm = get_pm()
        return jsonify(pm.get_progress())

    return app
