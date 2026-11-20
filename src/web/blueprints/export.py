"""Export blueprint for EPUB generation and download."""

from __future__ import annotations

import os
import threading
import time
from flask import Blueprint, Response, jsonify, current_app, send_file

from src.epub.exporter import FORMATS, export_project

export_bp = Blueprint('export', __name__)

# Simple in-memory store for export tasks
_export_tasks: dict[str, dict] = {}
_export_lock = threading.Lock()


@export_bp.route('/api/projects/<project_name>/export', methods=['POST'])
def start_export(project_name: str):
    """Start EPUB export for a project."""
    from flask import request
    data = request.get_json(silent=True) or {}
    fmt = data.get('format', 'chinese-only')

    if fmt not in FORMATS:
        return jsonify({'error': f'無效的格式。可選: {", ".join(FORMATS)}'}), 400

    projects_dir = current_app.config['PROJECTS_DIR']
    project_dir = os.path.join(projects_dir, project_name)

    if not os.path.isdir(project_dir):
        return jsonify({'error': '專案不存在'}), 404

    # Check if already exporting
    with _export_lock:
        existing = _export_tasks.get(project_name)
        if existing and existing['state'] == 'running':
            return jsonify({'error': '匯出任務已在執行中'}), 409

    # Build output path
    output_dir = os.path.join(project_dir, 'output')
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{project_name}_{fmt}.epub"
    output_path = os.path.join(output_dir, filename)

    task = {
        'state': 'running',
        'format': fmt,
        'filename': filename,
        'output_path': output_path,
        'error': None,
        'started_at': time.time(),
    }

    with _export_lock:
        _export_tasks[project_name] = task

    # Run in background thread
    thread = threading.Thread(
        target=_run_export,
        args=(project_dir, output_path, fmt, project_name),
        daemon=True,
    )
    thread.start()

    return jsonify({
        'status': 'ok',
        'state': 'running',
        'format': fmt,
        'filename': filename,
    })


@export_bp.route('/api/projects/<project_name>/export/status')
def export_status(project_name: str):
    """Get current export task status."""
    task = _export_tasks.get(project_name)
    if not task:
        return jsonify({'state': 'idle'})

    result = {
        'state': task['state'],
        'format': task['format'],
        'filename': task['filename'],
        'error': task['error'],
        'summary': task.get('summary'),
    }
    return jsonify(result)


@export_bp.route('/api/projects/<project_name>/export/files')
def list_export_files(project_name: str):
    """List available export files for a project."""
    projects_dir = current_app.config['PROJECTS_DIR']
    output_dir = os.path.join(projects_dir, project_name, 'output')

    if not os.path.isdir(output_dir):
        return jsonify({'files': []})

    files = []
    for f in sorted(os.listdir(output_dir)):
        if f.endswith('.epub'):
            fpath = os.path.join(output_dir, f)
            stat = os.stat(fpath)
            files.append({
                'filename': f,
                'size': stat.st_size,
                'modified': stat.st_mtime,
            })

    return jsonify({'files': files})


@export_bp.route('/api/projects/<project_name>/download/<filename>')
def download_file(project_name: str, filename: str):
    """Download an exported EPUB file."""
    projects_dir = current_app.config['PROJECTS_DIR']
    output_dir = os.path.join(projects_dir, project_name, 'output')
    file_path = os.path.join(output_dir, filename)

    # Security: ensure filename doesn't escape the output directory
    real_output = os.path.realpath(output_dir)
    real_file = os.path.realpath(file_path)
    if not real_file.startswith(real_output):
        return jsonify({'error': '無效的檔案路徑'}), 400

    if not os.path.isfile(file_path):
        return jsonify({'error': '檔案不存在'}), 404

    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename,
        mimetype='application/epub+zip',
    )


def _run_export(project_dir: str, output_path: str, fmt: str, project_name: str):
    """Execute EPUB export in background thread."""
    try:
        report = export_project(project_dir, output_path, fmt)

        with _export_lock:
            task = _export_tasks.get(project_name)
            if task:
                task['state'] = 'completed'
                task['summary'] = {**report.summary(), 'lines': report.lines()}
    except Exception as e:
        with _export_lock:
            task = _export_tasks.get(project_name)
            if task:
                task['state'] = 'failed'
                task['error'] = str(e)
