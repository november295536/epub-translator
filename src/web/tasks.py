"""Background task manager for long-running translation jobs."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import logging
from dataclasses import dataclass, field

from src.core.project import ProjectManager
from src.core.segmenter import SegmentType
from src.core.translator import TranslationEngine, TranslationError, RateLimitError, AuthError
from src.glossary.glossary_manager import GlossaryManager

logger = logging.getLogger(__name__)

MAX_WORKERS = 8


def setup_project_logging(project_dir: str) -> logging.Handler:
    """Add a FileHandler to root logger for project-specific logging."""
    log_dir = os.path.join(project_dir, 'log')
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, f"translation_{time.strftime('%Y%m%d_%H%M%S')}.log")
    handler = logging.FileHandler(log_file, encoding='utf-8')
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    logging.getLogger().addHandler(handler)
    return handler


def teardown_project_logging(handler: logging.Handler | None):
    """Remove project FileHandler from root logger."""
    if handler:
        logging.getLogger().removeHandler(handler)
        handler.close()


class SegmentDispatcher:
    """Thread-safe iterator that hands out segments to workers."""

    def __init__(self, segments):
        self._segments = list(segments)
        self._index = 0
        self._lock = threading.Lock()

    def next(self):
        """Return the next segment, or None if exhausted."""
        with self._lock:
            if self._index >= len(self._segments):
                return None
            seg = self._segments[self._index]
            self._index += 1
            return seg


@dataclass
class TranslationTask:
    """Represents a background translation job."""
    task_id: str
    project_name: str
    projects_dir: str
    state: str = 'pending'  # pending/running/paused/completed/failed
    total: int = 0
    completed: int = 0
    current_segment: str | None = None
    error: str | None = None
    num_workers: int = 1
    active_workers: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _condition: threading.Condition = field(default=None, repr=False)
    progress_queue: queue.Queue = field(default_factory=queue.Queue)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _threads: list = field(default_factory=list, repr=False)

    def __post_init__(self):
        self._condition = threading.Condition(self._lock)


class TaskManager:
    """Singleton manager for background translation tasks."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance.tasks: dict[str, TranslationTask] = {}
        return cls._instance

    def get_task(self, project_name: str) -> TranslationTask | None:
        """Get the current task for a project."""
        return self.tasks.get(project_name)

    def stop_translation(self, project_name: str):
        """Stop a running or paused translation task, terminating its thread."""
        task = self.tasks.get(project_name)
        if not task:
            return
        with task._lock:
            if task.state in ('running', 'paused'):
                task.state = 'stopped'
                task._condition.notify_all()

    def start_translation(self, project_name: str, projects_dir: str,
                          num_workers: int = 1) -> TranslationTask:
        """Start a new translation task for a project."""
        existing = self.tasks.get(project_name)
        if existing and existing.state == 'running':
            raise ValueError('翻譯任務已在執行中')
        # Stop any lingering paused thread before creating a new task
        if existing and existing.state == 'paused':
            self.stop_translation(project_name)

        num_workers = max(1, min(num_workers, MAX_WORKERS))

        task = TranslationTask(
            task_id=f"{project_name}_{int(time.time())}",
            project_name=project_name,
            projects_dir=projects_dir,
            num_workers=num_workers,
        )

        # Get total pending count
        project_dir = os.path.join(projects_dir, project_name)
        pm = ProjectManager(project_dir)
        pm.load()
        pending = pm.get_pending_segments()
        task.total = len(pending)

        if task.total == 0:
            task.state = 'completed'
            self.tasks[project_name] = task
            return task

        task.state = 'running'
        self.tasks[project_name] = task

        thread = threading.Thread(
            target=self._run_translation,
            args=(task,),
            daemon=True,
        )
        task._thread = thread
        thread.start()

        return task

    def pause_translation(self, project_name: str) -> bool:
        """Pause a running translation task."""
        task = self.tasks.get(project_name)
        if not task:
            return False
        with task._lock:
            if task.state != 'running':
                return False
            task.state = 'paused'
        task.progress_queue.put({'event': 'paused', 'data': {
            'completed': task.completed,
            'remaining': task.total - task.completed
        }})
        return True

    def resume_translation(self, project_name: str) -> bool:
        """Resume a paused translation task."""
        task = self.tasks.get(project_name)
        if not task:
            return False
        with task._lock:
            if task.state != 'paused':
                return False
            # Check coordinator thread or any worker thread is alive
            any_alive = (task._thread is not None and task._thread.is_alive()) or \
                        any(t.is_alive() for t in task._threads)
            if not any_alive:
                task.state = 'failed'
                task.error = '翻譯執行緒已終止，請重新開始翻譯'
                return False
            task.state = 'running'
            task._condition.notify_all()
        return True

    def _run_translation(self, task: TranslationTask):
        """Coordinator: set up resources, launch workers, wait for completion."""
        project_dir = os.path.join(task.projects_dir, task.project_name)
        handler = setup_project_logging(project_dir)
        try:
            save_lock = threading.Lock()
            pm = ProjectManager(project_dir, save_lock=save_lock)
            pm.load()

            # Load glossary
            glossary_path = os.path.join(project_dir, 'glossary.json')
            gm = GlossaryManager(glossary_path)
            gm.load()

            pending = pm.get_pending_segments()
            dispatcher = SegmentDispatcher(pending)

            num_workers = min(task.num_workers, task.total)
            logger.info("翻譯開始: %s (共 %d 段待翻譯, %d workers, 模型: %s)",
                        task.project_name, task.total, num_workers, pm.data.model)

            workers = []
            for i in range(num_workers):
                t = threading.Thread(
                    target=self._worker_loop,
                    args=(task, pm, gm, dispatcher, project_dir, i),
                    daemon=True,
                )
                workers.append(t)
            task._threads = workers

            for t in workers:
                t.start()

            for t in workers:
                t.join()

            if task.state == 'running':
                task.state = 'completed'
                task.current_segment = None
                logger.info("翻譯完成: 共翻譯 %d 段", task.completed)
                task.progress_queue.put({'event': 'complete', 'data': {
                    'total_translated': task.completed,
                }})

        except Exception as e:
            logger.error("Translation task failed: %s", e)
            task.state = 'failed'
            task.error = str(e)
            task.progress_queue.put({'event': 'error', 'data': {
                'error': str(e),
            }})
        finally:
            teardown_project_logging(handler)

    def _worker_loop(self, task: TranslationTask, pm: ProjectManager,
                     gm: GlossaryManager, dispatcher: SegmentDispatcher,
                     project_dir: str, worker_id: int):
        """Single worker loop: fetch segments from dispatcher and translate."""
        tag = f"W{worker_id}"
        engine = TranslationEngine(model=pm.data.model)
        all_segments = pm.data.segments

        with task._lock:
            task.active_workers += 1

        try:
            while True:
                seg = dispatcher.next()
                if seg is None:
                    break  # No more segments

                # Re-read model from disk to support mid-translation model switch
                try:
                    with open(os.path.join(project_dir, 'project.json'), 'r', encoding='utf-8') as f:
                        disk_model = json.load(f).get('model', 'sonnet')
                    if disk_model != engine.model:
                        logger.info("[%s] 模型切換: %s → %s", tag, engine.model, disk_model)
                        engine.model = disk_model
                        pm.data.model = disk_model
                except Exception:
                    pass  # Keep current model on read error

                # Check for pause — blocks until resumed
                with task._lock:
                    while task.state == 'paused':
                        logger.info("[%s] 翻譯暫停: 已完成 %d/%d", tag, task.completed, task.total)
                        task._condition.wait()
                    if task.state not in ('running',):
                        break

                # Find glossary terms
                glossary_text = ''
                if gm.entries:
                    found = gm.find_terms_in_text(seg.text)
                    if found:
                        glossary_text = gm.format_for_prompt(found)

                # Get context
                seg_idx = next((j for j, s in enumerate(all_segments)
                                if s.segment_id == seg.segment_id), -1)
                context_before = all_segments[seg_idx - 1].text if seg_idx > 0 else ''
                context_after = all_segments[seg_idx + 1].text if seg_idx < len(all_segments) - 1 else ''

                should_stop = False
                while True:
                    try:
                        html_arg = seg.html if seg.segment_type != SegmentType.CODE else ''
                        translated = engine.translate(
                            seg.text,
                            segment_type=seg.segment_type,
                            glossary_text=glossary_text,
                            context_before=context_before,
                            context_after=context_after,
                            html=html_arg,
                        )
                        pm.mark_translated(seg.segment_id, translated)
                        with task._lock:
                            task.completed += 1
                            completed = task.completed
                        logger.info("[%s] 段落完成 [%d/%d]: %s", tag, completed, task.total, seg.segment_id)

                        # Push progress event
                        percent = round(completed / task.total * 100, 1) if task.total > 0 else 0
                        task.progress_queue.put({'event': 'progress', 'data': {
                            'completed': completed,
                            'total': task.total,
                            'percent': percent,
                        }})
                        task.progress_queue.put({'event': 'segment_complete', 'data': {
                            'segment_id': seg.segment_id,
                            'translated_text': translated[:200],
                        }})
                        break  # Success, move to next segment

                    except (AuthError, RateLimitError) as e:
                        event_name = 'auth_error' if isinstance(e, AuthError) else 'rate_limit'
                        logger.warning("[%s] %s, pausing: %s", tag, event_name, e)
                        with task._lock:
                            # Only first worker to hit this sends the event
                            if task.state == 'running':
                                task.state = 'paused'
                                task.progress_queue.put({'event': event_name, 'data': {
                                    'error': str(e),
                                    'completed': task.completed,
                                    'remaining': task.total - task.completed,
                                }})
                        # All workers wait here
                        with task._lock:
                            while task.state == 'paused':
                                task._condition.wait()
                            if task.state not in ('running',):
                                should_stop = True
                                break
                        logger.info("[%s] 翻譯繼續", tag)
                        continue

                    except TranslationError as e:
                        logger.warning("[%s] Translation failed for %s: %s", tag, seg.segment_id, e)
                        task.progress_queue.put({'event': 'segment_error', 'data': {
                            'segment_id': seg.segment_id,
                            'error': str(e),
                        }})
                        break  # Skip to next segment

                if should_stop:
                    break
        finally:
            with task._lock:
                task.active_workers -= 1
