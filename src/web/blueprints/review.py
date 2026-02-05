"""Review blueprint for translation review interface."""

from __future__ import annotations

from flask import Blueprint, render_template, request, jsonify, current_app

from src.core.project import ProjectManager


review_bp = Blueprint('review', __name__)


def get_pm() -> ProjectManager:
    """Get ProjectManager instance for current project."""
    pm = ProjectManager(current_app.config['PROJECT_DIR'])
    pm.load()
    return pm


@review_bp.route('/')
def index():
    """Show review index with chapter list."""
    pm = get_pm()
    progress = pm.get_progress()
    chapters = pm.get_chapters()
    return render_template('review.html',
                           title=pm.data.title,
                           author=pm.data.author,
                           progress=progress,
                           chapters=chapters)


@review_bp.route('/chapter/<chapter_id>')
def chapter_view(chapter_id):
    """Show segments for a specific chapter."""
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


@review_bp.route('/api/segment/<segment_id>', methods=['POST'])
def update_segment(segment_id):
    """Update translation text for a segment."""
    pm = get_pm()
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'Missing text field'}), 400

    try:
        pm.update_translation(segment_id, data['text'])
        return jsonify({'status': 'ok', 'segment_id': segment_id})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404


@review_bp.route('/api/segment/<segment_id>/approve', methods=['POST'])
def approve_segment(segment_id):
    """Mark a segment as reviewed."""
    pm = get_pm()
    data = request.get_json() or {}
    text = data.get('text', '')

    try:
        pm.mark_reviewed(segment_id, text=text)
        return jsonify({'status': 'ok', 'segment_id': segment_id})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404


@review_bp.route('/api/progress')
def api_progress():
    """Get current translation progress."""
    pm = get_pm()
    return jsonify(pm.get_progress())
