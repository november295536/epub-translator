"""Translation blueprint for translation control and SSE progress."""

from __future__ import annotations

import json
import logging
import os
from flask import Blueprint, Response, jsonify, request, current_app

from src.core.project import (
    MAX_TIMEOUT, MIN_TIMEOUT, ProjectLockedError, ProjectManager, effort_levels,
    effort_reset_note,
)
import src.llm as llm
from src.core.translator import available_backends, available_models
from src.web.tasks import TaskManager


logger = logging.getLogger(__name__)

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
    except FileNotFoundError:
        return jsonify({'error': '專案不存在'}), 404
    except (ValueError, ProjectLockedError) as e:
        return jsonify({'error': str(e)}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@translation_bp.route('/api/projects/<project_name>/translate/pause', methods=['POST'])
def pause_translation(project_name: str):
    """Pause or resume a translation: body {"action": "pause" | "resume",
    "workers": N (resume only, optional)}. Both are idempotent; a pause of a
    task waiting after a rate limit makes it a lasting user pause."""
    tm = _get_task_manager()
    task = tm.get_task(project_name)

    if not task:
        return jsonify({'error': '沒有執行中的翻譯任務'}), 404

    data = request.get_json(silent=True) or {}
    action = data.get('action')
    if action not in ('pause', 'resume'):
        return jsonify({'error': 'action 必須是 pause 或 resume'}), 400
    def ending():
        # The run is ending (its last result is in, it failed, or it was
        # stopped): nothing to pause or resume. No state: the page keeps
        # what the stream told it
        return jsonify({'error': '翻譯正在結束', 'ending': True}), 409
    if task.finishing or task.stop_event.is_set():
        return ending()
    if action == 'resume':
        # Resume — optionally adjust workers
        num_workers = None
        if 'workers' in data:
            try:
                num_workers = int(data['workers'])
            except (TypeError, ValueError):
                pass
        if tm.resume_translation(project_name, num_workers=num_workers):
            return jsonify({'status': 'ok', 'state': 'running'})
        if task.finishing or task.stop_event.is_set():
            return ending()  # it began ending meanwhile
        # Resume failed (e.g. thread died) — return actual state so frontend can recover
        return jsonify({'error': task.error or '無法繼續翻譯', 'state': task.state}), 500
    else:
        # Pause
        if tm.pause_translation(project_name):
            return jsonify({'status': 'ok', 'state': 'paused'})
        if task.finishing or task.stop_event.is_set():
            return ending()  # it began ending meanwhile
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
        'auto_resume': task.resume_at is not None,  # paused, resumes by itself
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

        # The cursor before the snapshot: an event sent in between is
        # delivered (possibly repeating what the snapshot shows), not lost
        cursor = task.progress.cursor_end
        # Initial status, with a run-level error that paused the task before
        # this page connected
        # and an automatic wait in progress (seconds left), so the page offers
        # the pause button and shows when the run goes on
        resume_at = task.resume_at
        status = {'state': task.state, 'completed': task.completed,
                  'total': task.total, 'error': task.error,
                  'resume_in': None if resume_at is None else max(0.0, resume_at - tm.clock())}
        yield f"event: status\ndata: {json.dumps(status, ensure_ascii=False)}\n\n"
        if status['state'] in ('completed', 'failed', 'stopped'):
            return  # ended before this stream opened: its last event is gone

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

    with pm.mutex:  # one reading: a switch in between cannot mix two states
        settings = pm.call_settings()
    return jsonify({
        **_settings(settings),
        'available': available_models(settings.backend),  # of the project's backend
        'backends': available_backends(),  # each model with its effort levels
        'timeout_min': MIN_TIMEOUT,
        'timeout_max': MAX_TIMEOUT,
    })


def _settings(settings) -> dict:
    """The project's backend, model, the effort and timeout its calls use
    (CallSettings), and the effort levels of its model ([]: not
    configurable)."""
    return {
        'current': settings.model,
        'backend': settings.backend or llm.default_backend().name,
        'effort': settings.effort,  # None: the model takes no level
        'efforts': list(effort_levels(settings.backend, settings.model)),
        'timeout': settings.timeout,
    }


# The settings a PUT of the model route may carry; None (or left out): kept
_SETTING_KEYS = ('model', 'backend', 'effort', 'timeout')


@translation_bp.route('/api/projects/<project_name>/model', methods=['PUT'])
def set_model(project_name: str):
    """Set any of the translation model, its backend, the reasoning effort
    and the call timeout of a project: what the body leaves out (or gives as
    null) stays as stored, so a page sends only what the user changed. A body
    with none of them is refused (400)."""
    projects_dir = current_app.config['PROJECTS_DIR']

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or all(data.get(key) is None for key in _SETTING_KEYS):
        return jsonify({'error': '未提供要設定的項目（model、backend、effort 或 timeout）'}), 400

    backend = data.get('backend')  # None: the project's backend stays
    if backend is not None and (not isinstance(backend, str) or backend not in llm.BACKENDS):
        return jsonify({'error': f'無效的後端：{backend}'}), 400

    tm = _get_task_manager()
    try:
        with tm.transaction(project_name, projects_dir) as pm:
            with pm.mutex:  # a running task reads them under the mutex
                previous = (pm.data.backend, pm.data.model, pm.data.effort, pm.data.timeout)
                try:
                    pm.select_model(backend, data.get('model'), effort=data.get('effort'),
                                    timeout=data.get('timeout'))
                except ValueError as e:  # nothing changed
                    return jsonify({'error': str(e)}), 400
                try:
                    pm.save()
                except BaseException:
                    # not saved: the workers keep the old ones
                    (pm.data.backend, pm.data.model, pm.data.effort,
                     pm.data.timeout) = previous
                    raise
                answer = _settings(pm.call_settings())
                note = effort_reset_note(previous[2], pm.data)
                logger.info("模型設定已變更: %s → 後端 %s, 模型 %s, 推理強度 %s, 逾時 %s 秒",
                            project_name, answer['backend'], answer['current'],
                            answer['effort'], answer['timeout'])
    except (FileNotFoundError, ValueError) as e:
        return jsonify({'error': str(e)}), 404

    return jsonify({'status': 'ok', 'model': answer.pop('current'), **answer, 'note': note})


@translation_bp.route('/api/projects/<project_name>/brief')
def get_brief(project_name: str):
    """The book's brief ('' when there is none)."""
    try:
        pm = _get_task_manager().get_or_load_pm(project_name, current_app.config['PROJECTS_DIR'])
    except Exception as e:
        return jsonify({'error': str(e)}), 404
    with pm.mutex:  # a PUT in progress may still restore the previous one
        brief = pm.data.brief or ''
    return jsonify({'brief': brief})


@translation_bp.route('/api/projects/<project_name>/brief', methods=['PUT'])
def set_brief(project_name: str):
    """Set the book's brief; an empty one removes it. A running translation
    uses it from its next segment."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('brief'), str):
        return jsonify({'error': '請提供 brief 文字'}), 400
    try:
        with _get_task_manager().transaction(project_name,
                                             current_app.config['PROJECTS_DIR']) as pm:
            with pm.mutex:  # a running task reads it under the mutex
                previous = pm.data.brief
                try:
                    pm.set_brief(data['brief'])
                except ValueError as e:  # too long: nothing changed
                    return jsonify({'error': str(e)}), 400
                try:
                    pm.save()
                except BaseException:
                    pm.data.brief = previous  # not saved: the workers keep the old one
                    raise
                brief = pm.data.brief or ''
                logger.info("書籍簡介已變更: %s (%d 字元)", project_name, len(brief))
    except (FileNotFoundError, ValueError) as e:
        return jsonify({'error': str(e)}), 404
    return jsonify({'status': 'ok', 'brief': brief})
