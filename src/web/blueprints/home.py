"""Home blueprint for project listing."""

from __future__ import annotations

import os
from flask import Blueprint, render_template, jsonify, current_app

from src.core.project import ProjectManager


home_bp = Blueprint('home', __name__)


def get_all_projects() -> list[dict]:
    """Get all projects from the projects directory."""
    projects_dir = current_app.config['PROJECTS_DIR']
    projects = []

    if not os.path.isdir(projects_dir):
        return projects

    for name in sorted(os.listdir(projects_dir)):
        project_path = os.path.join(projects_dir, name)
        if not os.path.isdir(project_path):
            continue

        pm = ProjectManager(project_path)
        if not pm.exists():
            continue

        pm.load()
        progress = pm.get_progress()

        projects.append({
            'name': name,
            'title': pm.data.title,
            'author': pm.data.author,
            'total': progress['total'],
            'translated': progress['translated'],
            'reviewed': progress['reviewed'],
            'percent': progress['percent'],
            'status': _get_status(progress),
        })

    return projects


def _get_status(progress: dict) -> str:
    """Determine project status from progress. Passthrough segments (code
    without comments: translated at creation, never reviewed) are left out:
    a project is in progress once something is really translated, and
    completed when every other segment is reviewed."""
    total = progress['total'] - progress['passthrough']
    translated = progress['translated'] - progress['passthrough']
    if progress['reviewed'] == total:
        return 'completed'
    if translated > 0 or progress['reviewed'] > 0:
        return 'in_progress'
    return 'pending'


@home_bp.route('/')
def index():
    """Show the home page with project listing."""
    projects = get_all_projects()
    return render_template('index.html', projects=projects)


@home_bp.route('/api/projects')
def api_projects():
    """Get all projects as JSON."""
    projects = get_all_projects()
    return jsonify(projects)


@home_bp.route('/new')
def new_project():
    """Show the new project upload page."""
    return render_template('new_project.html')
