"""Translation blueprint for translation control and SSE progress."""

from __future__ import annotations

import json
from flask import Blueprint, Response, jsonify, current_app

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
        task = tm.start_translation(project_name, projects_dir)
        return jsonify({
            'status': 'ok',
            'task_id': task.task_id,
            'state': task.state,
            'total': task.total,
        })
    except ValueError as e:
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
        # Resume
        if tm.resume_translation(project_name):
            return jsonify({'status': 'ok', 'state': 'running'})
        return jsonify({'error': '無法繼續翻譯'}), 500
    else:
        # Pause
        if tm.pause_translation(project_name):
            return jsonify({'status': 'ok', 'state': 'paused'})
        return jsonify({'error': '無法暫停翻譯'}), 500


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
            'current_segment': None,
        })

    return jsonify({
        'state': task.state,
        'total': task.total,
        'completed': task.completed,
        'current_segment': task.current_segment,
        'error': task.error,
        'percent': round(task.completed / task.total * 100, 1) if task.total > 0 else 0,
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

        while True:
            try:
                msg = task.progress_queue.get(timeout=30)
                event = msg['event']
                data = json.dumps(msg['data'], ensure_ascii=False)
                yield f"event: {event}\ndata: {data}\n\n"

                if event in ('complete', 'error'):
                    break
            except Exception:
                # Send keepalive
                yield f": keepalive\n\n"

                # Check if task is done
                if task.state in ('completed', 'failed'):
                    break

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )
