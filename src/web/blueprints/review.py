"""Review blueprint for translation review interface."""

from __future__ import annotations

import os
from flask import Blueprint, render_template, request, jsonify, current_app

from src.core.project import ProjectManager


review_bp = Blueprint('review', __name__)


def get_pm(project_name: str) -> ProjectManager:
    """Get ProjectManager instance for the specified project."""
    projects_dir = current_app.config['PROJECTS_DIR']
    project_dir = os.path.join(projects_dir, project_name)
    pm = ProjectManager(project_dir)
    pm.load()
    return pm


@review_bp.route('/project/<project_name>/review')
def index(project_name: str):
    """Show review index with chapter list."""
    pm = get_pm(project_name)
    progress = pm.get_progress()
    chapters = pm.get_chapters()
    return render_template('review.html',
                           project_name=project_name,
                           title=pm.data.title,
                           author=pm.data.author,
                           progress=progress,
                           chapters=chapters)


@review_bp.route('/project/<project_name>/review/chapter/<chapter_id>')
def chapter_view(project_name: str, chapter_id: str):
    """Show segments for a specific chapter."""
    pm = get_pm(project_name)
    segments = pm.get_segments_by_chapter(chapter_id)
    progress = pm.get_progress()
    chapters = pm.get_chapters()
    return render_template('review.html',
                           project_name=project_name,
                           title=pm.data.title,
                           author=pm.data.author,
                           progress=progress,
                           chapters=chapters,
                           current_chapter=chapter_id,
                           segments=segments)


@review_bp.route('/api/projects/<project_name>/segments/<segment_id>', methods=['POST'])
def update_segment(project_name: str, segment_id: str):
    """Update translation text for a segment."""
    pm = get_pm(project_name)
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'Missing text field'}), 400

    try:
        pm.update_translation(segment_id, data['text'])
        return jsonify({'status': 'ok', 'segment_id': segment_id})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404


@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/approve', methods=['POST'])
def approve_segment(project_name: str, segment_id: str):
    """Mark a segment as reviewed."""
    pm = get_pm(project_name)
    data = request.get_json() or {}
    text = data.get('text', '')

    try:
        pm.mark_reviewed(segment_id, text=text)
        return jsonify({'status': 'ok', 'segment_id': segment_id})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404


@review_bp.route('/api/projects/<project_name>/progress')
def api_progress(project_name: str):
    """Get current translation progress."""
    pm = get_pm(project_name)
    return jsonify(pm.get_progress())
