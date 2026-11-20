"""Search blueprint for segment search and batch retranslation."""

from __future__ import annotations

import os
from flask import Blueprint, render_template, request, jsonify, current_app

from src.core.project import ProjectManager, SegmentStatus
from src.web.tasks import TaskManager


search_bp = Blueprint('search', __name__)

MAX_RESULTS = 200
PREVIEW_LENGTH = 120


def get_pm(project_name: str) -> ProjectManager:
    """Get ProjectManager: reuse running task's PM if available, else load from disk."""
    tm = TaskManager()
    projects_dir = current_app.config['PROJECTS_DIR']
    return tm.get_or_load_pm(project_name, projects_dir)


@search_bp.route('/project/<project_name>/search')
def search_page(project_name: str):
    """Render the search & retranslate page."""
    pm = get_pm(project_name)
    progress = pm.get_progress()
    return render_template('project/search.html',
                           project_name=project_name,
                           title=pm.data.title,
                           author=pm.data.author,
                           progress=progress,
                           active_tab='search')


@search_bp.route('/api/projects/<project_name>/search-segments', methods=['POST'])
def search_segments(project_name: str):
    """Search translated, reviewed and failed segments by text or translation content.

    Request: { "query": "machine learning" }
    Response: { "segments": [...], "total": N, "truncated": bool }
    """
    data = request.get_json(silent=True) or {}
    query = (data.get('query') or '').strip()
    if not query:
        return jsonify({'error': '請輸入搜尋關鍵字'}), 400

    pm = get_pm(project_name)
    query_lower = query.lower()

    results = []
    for seg in pm.data.segments:
        # FAILED segments are included so they can be found and re-translated
        if seg.status == SegmentStatus.PENDING:
            continue

        text_match = query_lower in seg.text.lower()
        translation_match = query_lower in seg.translated_text.lower()

        if text_match or translation_match:
            match_in = 'both' if (text_match and translation_match) else (
                'text' if text_match else 'translation')
            results.append({
                'segment_id': seg.segment_id,
                'chapter_id': seg.chapter_id,
                'status': str(seg.status),
                'text_preview': seg.text[:PREVIEW_LENGTH],
                'translation_preview': seg.translated_text[:PREVIEW_LENGTH],
                'text': seg.text,
                'translated_text': seg.translated_text,
                'match_in': match_in,
                # code without comments: shown, never re-translated
                'passthrough': seg.passthrough,
            })

    truncated = len(results) > MAX_RESULTS
    if truncated:
        results = results[:MAX_RESULTS]

    return jsonify({
        'segments': results,
        'total': len(results),
        'truncated': truncated,
    })
