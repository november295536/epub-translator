"""Translation blueprint for translation control and SSE progress."""

from __future__ import annotations

import json
import os
from flask import Blueprint, Response, jsonify, request, current_app

from src.core.project import ProjectLockedError, ProjectManager
from src.core.translator import available_models
from src.web.tasks import TaskManager


translation_bp = Blueprint('translation', __name__)


def _get_task_manager() -> TaskManager:
    """Get the singleton TaskManager."""
    return TaskManager()


@translation_bp.route('/api/projects/<project_name>/translate/start', methods=['POST'])
def start_translation(project_name: str):
    """Start translation for a project."""
    tm = _get_task_manager()
    projects_dir = current_app.config['PROJECTS_DIR']

    try:
        data = request.get_json(silent=True) or {}
        num_workers = data.get('workers', 1)
        try:
            num_workers = int(num_workers)
        except (TypeError, ValueError):
            num_workers = 1
        num_workers = max(1, min(num_workers, 20))

        segment_ids = data.get('segment_ids')  # list[str] | None
        # Normal start leaves FAILED segments alone unless asked to retry them
        retry_failed = bool(data.get('retry_failed', False))
        task = tm.start_translation(project_name, projects_dir,
                                    num_workers=num_workers,
                                    segment_ids=segment_ids,
                                    retry_failed=retry_failed)
        return jsonify({
            'status': 'ok',
            'task_id': task.task_id,
            'state': task.state,
            'total': task.total,
            'workers': task.num_workers,
        })
    except (ValueError, ProjectLockedError) as e:
        return jsonify({'error': str(e)}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@translation_bp.route('/api/projects/<project_name>/translate/pause', methods=['POST'])
def pause_translation(project_name: str):
    """Pause a running translation."""
    tm = _get_task_manager()
    task = tm.get_task(project_name)

    if not task:
        return jsonify({'error': '沒有執行中的翻譯任務'}), 404

    if task.state == 'paused':
        # Resume — optionally adjust workers
        data = request.get_json(silent=True) or {}
        num_workers = None
        if 'workers' in data:
            try:
                num_workers = int(data['workers'])
            except (TypeError, ValueError):
                pass
        if tm.resume_translation(project_name, num_workers=num_workers):
            return jsonify({'status': 'ok', 'state': 'running'})
        # Resume failed (e.g. thread died) — return actual state so frontend can recover
        return jsonify({'error': task.error or '無法繼續翻譯', 'state': task.state}), 500
    else:
        # Pause
        if tm.pause_translation(project_name):
            return jsonify({'status': 'ok', 'state': 'paused'})
        return jsonify({'error': '無法暫停翻譯', 'state': task.state}), 500


@translation_bp.route('/api/projects/<project_name>/translate/status')
def translation_status(project_name: str):
    """Get current translation status."""
    tm = _get_task_manager()
    task = tm.get_task(project_name)

    if not task:
        return jsonify({
            'state': 'idle',
            'total': 0,
            'completed': 0,
        })

    # Detect dead thread and update state
    any_alive = (task._coordinator is not None and task._coordinator.is_alive()) or \
                any(t.is_alive() for t in task._threads)
    if task.state in ('running', 'paused') and not any_alive:
        task.state = 'failed'
        task.error = task.error or '翻譯執行緒意外終止'

    return jsonify({
        'state': task.state,
        'total': task.total,
        'completed': task.completed,
        'error': task.error,
        'percent': round(task.completed / task.total * 100, 1) if task.total > 0 else 0,
        'workers': task.num_workers,
        'active_workers': task.active_workers,
    })


@translation_bp.route('/api/projects/<project_name>/translate/stream')
def translation_stream(project_name: str):
    """SSE endpoint for translation progress events."""
    tm = _get_task_manager()
    task = tm.get_task(project_name)

    def generate():
        if not task:
            yield f"event: error\ndata: {json.dumps({'error': '沒有翻譯任務'})}\n\n"
            return

        # Send initial status
        yield f"event: status\ndata: {json.dumps({'state': task.state, 'completed': task.completed, 'total': task.total})}\n\n"

        cursor = task.progress.cursor_end
        while True:
            events, cursor = task.progress.iter_from(cursor, timeout=30)

            if not events:
                # Send keepalive
                yield f": keepalive\n\n"

                # Check if task is done
                if task.state in ('completed', 'failed', 'stopped'):
                    break
                continue

            for msg in events:
                event = msg['event']
                data = json.dumps(msg['data'], ensure_ascii=False)
                yield f"event: {event}\ndata: {data}\n\n"

                if event in ('complete', 'error', 'stopped'):
                    return

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


@translation_bp.route('/api/projects/<project_name>/model')
def get_model(project_name: str):
    """Get current model and available models."""
    projects_dir = current_app.config['PROJECTS_DIR']
    tm = _get_task_manager()

    try:
        pm = tm.get_or_load_pm(project_name, projects_dir)
    except Exception as e:
        return jsonify({'error': str(e)}), 404

    return jsonify({
        'current': pm.data.model,
        'available': available_models(),
    })


@translation_bp.route('/api/projects/<project_name>/model', methods=['PUT'])
def set_model(project_name: str):
    """Set the translation model for a project."""
    projects_dir = current_app.config['PROJECTS_DIR']

    data = request.get_json()
    if not data or 'model' not in data:
        return jsonify({'error': '未提供模型名稱'}), 400

    model = data['model']
    valid_aliases = [m['alias'] for m in available_models()]
    if model not in valid_aliases:
        return jsonify({'error': f'無效的模型：{model}，可用模型：{", ".join(valid_aliases)}'}), 400

    tm = _get_task_manager()
    try:
        with tm.transaction(project_name, projects_dir) as pm:
            pm.set_model(model)
            pm.save()
    except (FileNotFoundError, ValueError) as e:
        return jsonify({'error': str(e)}), 404

    return jsonify({'status': 'ok', 'model': model})
