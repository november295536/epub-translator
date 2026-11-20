"""Review blueprint for translation review interface."""

from __future__ import annotations

import copy
import logging
import os
from flask import Blueprint, render_template, request, jsonify, current_app

from dataclasses import asdict

from src.core.project import ProjectManager, SegmentStatus
from src.core.validation import check_segment
from src.core.translator import (
    AuthError, BackendUnavailableError, RateLimitError, TranslationEngine,
    record_failure_log, remove_failure_log, translate_segment,
)
from src.glossary.glossary_manager import GlossaryManager, load_glossary
from src.web.tasks import TaskManager, setup_project_logging, teardown_project_logging

logger = logging.getLogger(__name__)

review_bp = Blueprint('review', __name__)

# The label a reviewer reads for each kind of issue
ISSUE_KINDS = {'structure': '結構', 'quality': '品質', 'glossary': '術語', 'backend': '模型回覆'}


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
    """Show review index with chapter list; with ?issues=1, every segment
    that has issues."""
    pm = get_pm(project_name)
    issues_only = _issues_only()
    return _render(pm, project_name, segments=with_issues(pm.data.segments) if issues_only else None,
                   issues_only=issues_only)


@review_bp.route('/project/<project_name>/review/chapter/<chapter_id>')
def chapter_view(project_name: str, chapter_id: str):
    """Show segments for a specific chapter (?issues=1: only those with issues)."""
    pm = get_pm(project_name)
    segments = pm.get_segments_by_chapter(chapter_id)
    issues_only = _issues_only()
    return _render(pm, project_name, segments=with_issues(segments) if issues_only else segments,
                   issues_only=issues_only, current_chapter=chapter_id)


def _issues_only() -> bool:
    return request.args.get('issues') == '1'


def with_issues(segments: list) -> list:
    """The segments that have stored issues: a failure, quality or glossary
    warnings."""
    return [s for s in segments if s.issues]


def _render(pm: ProjectManager, project_name: str, **page):
    return render_template('project/review.html',
                           project_name=project_name,
                           title=pm.data.title,
                           author=pm.data.author,
                           progress=pm.get_progress(),
                           chapters=pm.get_chapters(),
                           issue_count=len(with_issues(pm.data.segments)),
                           issue_kinds=ISSUE_KINDS,
                           active_tab='review',
                           **page)


def transaction(project_name: str):
    """Load, change and save the project as one step (see TaskManager.transaction)."""
    return TaskManager().transaction(project_name, current_app.config['PROJECTS_DIR'])


def _log_dir(pm: ProjectManager) -> str:
    return os.path.join(pm.project_dir, 'log')


@review_bp.route('/api/projects/<project_name>/segments/<segment_id>', methods=['POST'])
def update_segment(project_name: str, segment_id: str):
    """Update translation text for a segment."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or 'text' not in data:
        return jsonify({'error': 'Missing text field'}), 400
    if not isinstance(data['text'], str):
        return jsonify({'error': 'text must be a string'}), 400

    try:
        with transaction(project_name) as pm:
            seg = pm._find(segment_id)
            if seg.passthrough:
                return _passthrough_refused()
            gm = _glossary(pm.project_dir)
            issues = []
            if data['text'].strip():  # an empty text clears the translation
                result = check_segment(seg, data['text'], gm)
                if result.blocking:
                    return _refused(result, pm, gm)
                issues = result.issues
            pm.update_translation(segment_id, data['text'], issues=issues)
            pm.save()
            # A person's version replaces the failed one: its diagnostics go too
            remove_failure_log(_log_dir(pm), segment_id)
            answer = _answer(pm, gm, issues, status='ok', segment_id=segment_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    return jsonify(answer)


def _glossary(project_dir: str) -> GlossaryManager | None:
    """The project's glossary, for the glossary warnings; None when it
    cannot be read (logged): the check and the save go on without them."""
    return load_glossary(os.path.join(project_dir, 'glossary.json'))


def _issues(issues: list) -> list:
    return [asdict(i) for i in issues]


def _answer(pm: ProjectManager, gm, issues: list, **fields) -> dict:
    """A review response: `fields`, the issues of the text, the number of
    segments with stored issues (for the page's count), and
    glossary_unavailable when the glossary could not be read."""
    fields.update(issues=_issues(issues), issue_count=len(with_issues(pm.data.segments)))
    if gm is None:
        fields['glossary_unavailable'] = True
    return fields


PASSTHROUGH_MESSAGE = '此段是沒有註解的程式碼，原樣保留，不需翻譯，也不能編輯或審閱'


def _passthrough_refused():
    """The answer to a change of a passthrough segment (a code block
    without comment lines, src/core/segmenter.py): nothing is changed."""
    return jsonify({'error': PASSTHROUGH_MESSAGE}), 409


def _refused(result, pm: ProjectManager, gm):
    """The answer to a save or approval whose text fails the structural
    check: nothing is changed."""
    return jsonify(_answer(pm, gm, result.issues, error='譯文未通過結構檢查：'
                           + '；'.join(i.detail for i in result.blocking))), 422


@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/approve', methods=['POST'])
def approve_segment(project_name: str, segment_id: str):
    """Mark a segment as reviewed."""
    data = request.get_json(silent=True) if request.get_data() else {}
    if not isinstance(data, dict):  # a body that is not a JSON object
        return jsonify({'error': 'Body must be a JSON object'}), 400
    text = data.get('text', '')
    if text is None:
        text = ''
    if not isinstance(text, str):
        return jsonify({'error': 'text must be a string'}), 400
    if not text.strip():
        text = ''  # whitespace only: no text supplied, the stored one is approved

    try:
        with transaction(project_name) as pm:
            seg = pm._find(segment_id)
            if seg.passthrough:
                return _passthrough_refused()
            approved = text or seg.translated_text
            if not approved.strip():
                return jsonify({'error': '沒有譯文可以核可'}), 422
            gm = _glossary(pm.project_dir)
            result = check_segment(seg, approved, gm)
            if result.blocking:
                return _refused(result, pm, gm)
            pm.mark_reviewed(segment_id, text=text)  # a person's decision: stored issues cleared
            pm.save()
            remove_failure_log(_log_dir(pm), segment_id)
            # the warnings the approved text still has, for the reviewer to see
            answer = _answer(pm, gm, result.issues, status='ok', segment_id=segment_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    return jsonify(answer)


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
        if seg.passthrough:
            return _passthrough_refused()
        model = pm.data.model
        translations = pm.data.segmentation == 2
        segments = copy.deepcopy(pm.data.segments)
        seg = next(s for s in segments if s.segment_id == segment_id)
    project_dir = pm.project_dir

    gm = _glossary(project_dir)  # None: translated without the glossary, and said so

    handler = setup_project_logging(project_dir)
    try:
        engine = TranslationEngine(model=model)
        logger.info("重新翻譯段落: %s (模型: %s)", segment_id, engine.model)
        outcome = translate_segment(engine, seg, segments, gm, translations=translations)
        with transaction(project_name) as pm:
            current = next((s for s in pm.data.segments if s.segment_id == segment_id), None)
            if current is None or (current.html, current.text) != source:
                return jsonify({'error': '專案已在翻譯期間被重新建立，未套用新譯文'}), 409
            if not pm.apply_outcome(segment_id, outcome, revision=revision):
                return jsonify({'error': '段落已在翻譯期間被編輯，未套用新譯文'}), 409
            pm.save()
            record_failure_log(_log_dir(pm), segment_id, outcome)
            # the stored issues: a failure keeps the old text and status
            answer = _answer(pm, gm, outcome.issues)
        if outcome.status == SegmentStatus.FAILED:
            detail = outcome.issues[0].detail
            logger.warning("Translation error for %s: %s", segment_id, detail)
            return jsonify({'error': detail, **answer}), 500
        logger.info("重新翻譯完成: %s (%s)", segment_id, engine.model)
        return jsonify({'status': 'ok', 'segment_id': segment_id,
                        'translated_text': outcome.text, **answer})
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
