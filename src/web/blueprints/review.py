"""Review blueprint for translation review interface."""

from __future__ import annotations

import copy
import logging
import os
from flask import Blueprint, render_template, request, jsonify, current_app

from src.core.project import ProjectManager, SegmentStatus
from src.core.translator import (
    AuthError, BackendUnavailableError, RateLimitError, TranslationEngine,
    record_failure_log, remove_failure_log, translate_segment,
)
from src.glossary.glossary_manager import GlossaryManager
from src.web.tasks import TaskManager, setup_project_logging, teardown_project_logging

logger = logging.getLogger(__name__)

review_bp = Blueprint('review', __name__)


@review_bp.errorhandler(FileNotFoundError)
def project_missing(e):
    # No project.json: never existed, or deleted (also during a re-translation)
    return jsonify({'error': '專案不存在'}), 404


def get_pm(project_name: str) -> ProjectManager:
    """Get ProjectManager: reuse running task's PM if available, else load from disk."""
    tm = TaskManager()
    projects_dir = current_app.config['PROJECTS_DIR']
    return tm.get_or_load_pm(project_name, projects_dir)


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


def transaction(project_name: str):
    """Load, change and save the project as one step (see TaskManager.transaction)."""
    return TaskManager().transaction(project_name, current_app.config['PROJECTS_DIR'])


def _log_dir(pm: ProjectManager) -> str:
    return os.path.join(pm.project_dir, 'log')


@review_bp.route('/api/projects/<project_name>/segments/<segment_id>', methods=['POST'])
def update_segment(project_name: str, segment_id: str):
    """Update translation text for a segment."""
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'Missing text field'}), 400

    try:
        with transaction(project_name) as pm:
            pm.update_translation(segment_id, data['text'])
            pm.save()
            # A person's version replaces the failed one: its diagnostics go too
            remove_failure_log(_log_dir(pm), segment_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    return jsonify({'status': 'ok', 'segment_id': segment_id})


@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/approve', methods=['POST'])
def approve_segment(project_name: str, segment_id: str):
    """Mark a segment as reviewed."""
    data = request.get_json() or {}
    text = data.get('text', '')

    try:
        with transaction(project_name) as pm:
            pm.mark_reviewed(segment_id, text=text)
            pm.save()
            remove_failure_log(_log_dir(pm), segment_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    return jsonify({'status': 'ok', 'segment_id': segment_id})


@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/retranslate', methods=['POST'])
def retranslate_segment(project_name: str, segment_id: str):
    """Re-translate a single segment using the backend.

    The model call runs outside the project mutex, on a snapshot; its result
    is then applied to the current project state (the running task's
    manager, otherwise a fresh load) only if the segment was not edited
    meanwhile."""
    with transaction(project_name) as pm:
        seg = next((s for s in pm.data.segments if s.segment_id == segment_id), None)
        if not seg:
            return jsonify({'error': f'Segment {segment_id} not found'}), 404
        revision = seg.revision
        source = (seg.html, seg.text)  # a replaced project has other text
        model = pm.data.model
        segments = copy.deepcopy(pm.data.segments)
        seg = next(s for s in segments if s.segment_id == segment_id)
    project_dir = pm.project_dir

    # Load glossary
    glossary_path = os.path.join(project_dir, 'glossary.json')
    gm = GlossaryManager(glossary_path)
    gm.load()

    handler = setup_project_logging(project_dir)
    try:
        engine = TranslationEngine(model=model)
        logger.info("重新翻譯段落: %s (模型: %s)", segment_id, engine.model)
        outcome = translate_segment(engine, seg, segments, gm)
        with transaction(project_name) as pm:
            current = next((s for s in pm.data.segments if s.segment_id == segment_id), None)
            if current is None or (current.html, current.text) != source:
                return jsonify({'error': '專案已在翻譯期間被重新建立，未套用新譯文'}), 409
            if not pm.apply_outcome(segment_id, outcome, revision=revision):
                return jsonify({'error': '段落已在翻譯期間被編輯，未套用新譯文'}), 409
            pm.save()
            record_failure_log(_log_dir(pm), segment_id, outcome)
        if outcome.status == SegmentStatus.FAILED:
            detail = outcome.issues[0].detail
            logger.warning("Translation error for %s: %s", segment_id, detail)
            return jsonify({'error': detail}), 500
        logger.info("重新翻譯完成: %s (%s)", segment_id, engine.model)
        return jsonify({
            'status': 'ok',
            'segment_id': segment_id,
            'translated_text': outcome.text,
        })
    except AuthError as e:
        logger.warning("Auth error during retranslation: %s", e)
        return jsonify({'error': str(e), 'auth_error': True}), 401
    except RateLimitError as e:
        logger.warning("Rate limit during retranslation: %s", e)
        return jsonify({'error': str(e)}), 500
    except BackendUnavailableError as e:
        logger.warning("Backend unavailable during retranslation: %s", e)
        return jsonify({'error': str(e)}), 503
    finally:
        teardown_project_logging(handler)


@review_bp.route('/api/projects/<project_name>/progress')
def api_progress(project_name: str):
    """Get current translation progress."""
    pm = get_pm(project_name)
    return jsonify(pm.get_progress())
