"""Statistics blueprint: the numbers of a project's event record
(src/core/stats.py, the same function as `translate.py stats --json`)."""

from __future__ import annotations

import os

from flask import Blueprint, current_app, jsonify, render_template, request

from src.core.project import ProjectManager
from src.core.stats import parse_since, project_stats
from src.web.tasks import TaskManager

stats_bp = Blueprint('stats', __name__)


def _project_dir(project_name: str) -> str | None:
    """The project's directory; None when it is not a project."""
    project_dir = os.path.join(current_app.config['PROJECTS_DIR'], project_name)
    if not os.path.isfile(os.path.join(project_dir, ProjectManager.PROJECT_FILE)):
        return None
    return project_dir


@stats_bp.route('/api/projects/<project_name>/stats')
def api_stats(project_name: str):
    """The numbers of `translate.py stats --json` (query: since, run).
    Reads only the event record: works while a run appends to it."""
    project_dir = _project_dir(project_name)
    if project_dir is None:
        return jsonify({'error': '專案不存在'}), 404
    since = request.args.get('since') or None
    try:
        since_ts = parse_since(since) if since else None
    except ValueError:
        return jsonify({'error': f'無法解讀時間：{since}'}), 400
    return jsonify(project_stats(project_dir, since=since_ts,
                                 run=request.args.get('run') or None))


@stats_bp.route('/project/<project_name>/stats')
def stats_page(project_name: str):
    """The statistics tab (the numbers come from api_stats)."""
    if _project_dir(project_name) is None:
        return '專案不存在', 404
    pm = TaskManager().get_or_load_pm(project_name, current_app.config['PROJECTS_DIR'])
    with pm.mutex:
        progress = pm.get_progress()
    return render_template('project/stats.html', project_name=project_name,
                           title=pm.data.title, author=pm.data.author,
                           progress=progress, active_tab='stats')
