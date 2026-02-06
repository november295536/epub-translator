"""Review blueprint for translation review interface."""

from __future__ import annotations

import logging
import os
from flask import Blueprint, render_template, request, jsonify, current_app

from src.core.project import ProjectManager
from src.core.segmenter import SegmentType
from src.core.translator import TranslationEngine, TranslationError, AuthError
from src.glossary.glossary_manager import GlossaryManager

logger = logging.getLogger(__name__)

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
    return render_template('project/review.html',
                           project_name=project_name,
                           title=pm.data.title,
                           author=pm.data.author,
                           progress=progress,
                           chapters=chapters,
                           active_tab='review')


@review_bp.route('/project/<project_name>/review/chapter/<chapter_id>')
def chapter_view(project_name: str, chapter_id: str):
    """Show segments for a specific chapter."""
    pm = get_pm(project_name)
    segments = pm.get_segments_by_chapter(chapter_id)
    progress = pm.get_progress()
    chapters = pm.get_chapters()
    return render_template('project/review.html',
                           project_name=project_name,
                           title=pm.data.title,
                           author=pm.data.author,
                           progress=progress,
                           chapters=chapters,
                           current_chapter=chapter_id,
                           segments=segments,
                           active_tab='review')


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


@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/retranslate', methods=['POST'])
def retranslate_segment(project_name: str, segment_id: str):
    """Re-translate a single segment using Claude CLI."""
    projects_dir = current_app.config['PROJECTS_DIR']
    project_dir = os.path.join(projects_dir, project_name)
    pm = ProjectManager(project_dir)
    pm.load()

    # Find the segment
    seg = next((s for s in pm.data.segments if s.segment_id == segment_id), None)
    if not seg:
        return jsonify({'error': f'Segment {segment_id} not found'}), 404

    # Load glossary
    glossary_path = os.path.join(project_dir, 'glossary.json')
    gm = GlossaryManager(glossary_path)
    gm.load()

    # Find glossary terms
    glossary_text = ''
    if gm.entries:
        found = gm.find_terms_in_text(seg.text)
        if found:
            glossary_text = gm.format_for_prompt(found)

    # Get context (previous and next segments)
    all_segments = pm.data.segments
    seg_idx = next((j for j, s in enumerate(all_segments) if s.segment_id == segment_id), -1)
    context_before = all_segments[seg_idx - 1].text if seg_idx > 0 else ''
    context_after = all_segments[seg_idx + 1].text if seg_idx < len(all_segments) - 1 else ''

    try:
        engine = TranslationEngine()
        html_arg = seg.html if seg.segment_type != SegmentType.CODE else ''
        translated = engine.translate(
            seg.text,
            segment_type=seg.segment_type,
            glossary_text=glossary_text,
            context_before=context_before,
            context_after=context_after,
            html=html_arg,
        )
        pm.mark_translated(segment_id, translated)
        logger.info("Retranslated segment %s", segment_id)
        return jsonify({
            'status': 'ok',
            'segment_id': segment_id,
            'translated_text': translated,
        })
    except AuthError as e:
        logger.warning("Auth error during retranslation: %s", e)
        return jsonify({'error': str(e), 'auth_error': True}), 401
    except TranslationError as e:
        logger.warning("Translation error for %s: %s", segment_id, e)
        return jsonify({'error': str(e)}), 500


@review_bp.route('/api/projects/<project_name>/progress')
def api_progress(project_name: str):
    """Get current translation progress."""
    pm = get_pm(project_name)
    return jsonify(pm.get_progress())
