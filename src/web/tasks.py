"""Background task manager for long-running translation jobs."""

from __future__ import annotations

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

MAX_WORKERS = 20


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


@dataclass
class EventBroadcaster:
    """Thread-safe event broadcasting for multiple SSE consumers.

    Unlike queue.Queue (single-consumer), all consumers see all events.
    Each SSE generator tracks its own read cursor.
    """
    _events: list = field(default_factory=list)
    _condition: threading.Condition = field(default_factory=threading.Condition)

    def put(self, event: dict):
        """Broadcast an event to all consumers."""
        with self._condition:
            self._events.append(event)
            self._condition.notify_all()

    def iter_from(self, cursor: int = 0, timeout: float = 30):
        """Return events from cursor position, blocking if none available.

        Returns (events_list, new_cursor).
        """
        with self._condition:
            if cursor < len(self._events):
                new_events = self._events[cursor:]
                return new_events, len(self._events)
            # Wait for new events
            self._condition.wait(timeout=timeout)
            if cursor < len(self._events):
                new_events = self._events[cursor:]
                return new_events, len(self._events)
            return [], cursor  # timeout

    @property
    def cursor_end(self) -> int:
        """Return cursor pointing past the last event."""
        with self._condition:
            return len(self._events)

    def get_all(self):
        """Return all events (for testing)."""
        with self._condition:
            return list(self._events)


@dataclass
class TranslationResult:
    """Result from a worker thread."""
    segment_id: str
    translated_text: str = ''
    model: str = ''
    success: bool = True
    error: str | None = None


@dataclass
class TranslationTask:
    """Represents a background translation job."""
    task_id: str
    project_name: str
    projects_dir: str
    state: str = 'pending'  # pending/running/paused/completed/failed/stopped

    # Progress
    total: int = 0
    completed: int = 0
    error: str | None = None

    # Worker management
    num_workers: int = 1
    active_workers: int = 0

    # Synchronization
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    pause_event: threading.Event = field(default_factory=threading.Event, repr=False)
    stop_event: threading.Event = field(default_factory=threading.Event, repr=False)

    # Queues
    segment_queue: queue.Queue = field(default_factory=queue.Queue, repr=False)
    result_queue: queue.Queue = field(default_factory=queue.Queue, repr=False)
    progress: EventBroadcaster = field(default_factory=EventBroadcaster, repr=False)

    # References
    pm: ProjectManager | None = field(default=None, repr=False)
    _gm: GlossaryManager | None = field(default=None, repr=False)
    _threads: list = field(default_factory=list, repr=False)
    _coordinator: threading.Thread | None = field(default=None, repr=False)

    def __post_init__(self):
        # Running by default — cleared to pause
        self.pause_event.set()


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

    def get_project_manager(self, project_name: str) -> ProjectManager | None:
        """Return the running task's ProjectManager, or None if no active task."""
        task = self.tasks.get(project_name)
        if task and task.state in ('running', 'paused') and task.pm is not None:
            return task.pm
        return None

    def get_or_load_pm(self, project_name: str, projects_dir: str) -> ProjectManager:
        """Return running task's PM (in-memory, latest) or load from disk."""
        task = self.tasks.get(project_name)
        if task and task.state in ('running', 'paused') and task.pm:
            return task.pm
        pm = ProjectManager(os.path.join(projects_dir, project_name))
        pm.load()
        return pm

    def stop_translation(self, project_name: str):
        """Stop a running or paused translation task."""
        task = self.tasks.get(project_name)
        if not task:
            return
        with task._lock:
            if task.state in ('running', 'paused'):
                task.state = 'stopped'
            task.stop_event.set()
            task.pause_event.set()  # unblock any paused workers

    def start_translation(self, project_name: str, projects_dir: str,
                          num_workers: int = 1,
                          segment_ids: list[str] | None = None) -> TranslationTask:
        """Start a new translation task for a project.

        Args:
            segment_ids: Optional list of segment IDs to retranslate.
                         When provided, queues those specific segments regardless of status.
                         When None, queues all pending segments (normal translation).
        """
        existing = self.tasks.get(project_name)
        if existing and existing.state == 'running':
            raise ValueError('翻譯任務已在執行中')
        # Stop any lingering paused task before creating a new one
        if existing and existing.state == 'paused':
            self.stop_translation(project_name)
        # Wait for old coordinator thread to finish before starting a new one
        if existing and existing._coordinator is not None:
            existing._coordinator.join(timeout=5)

        num_workers = max(1, min(num_workers, MAX_WORKERS))

        task = TranslationTask(
            task_id=f"{project_name}_{int(time.time())}",
            project_name=project_name,
            projects_dir=projects_dir,
            num_workers=num_workers,
        )

        # Get segments to translate
        project_dir = os.path.join(projects_dir, project_name)
        pm = ProjectManager(project_dir)
        pm.load()

        if segment_ids is not None:
            # Retranslate mode: queue specified segments regardless of status
            id_set = set(segment_ids)
            to_translate = [s for s in pm.data.segments if s.segment_id in id_set]
        else:
            to_translate = pm.get_pending_segments()

        task.total = len(to_translate)

        if task.total == 0:
            task.state = 'completed'
            self.tasks[project_name] = task
            return task

        task.state = 'running'
        task.pm = pm

        # Fill segment queue
        for seg in to_translate:
            task.segment_queue.put(seg)

        self.tasks[project_name] = task

        coordinator = threading.Thread(
            target=self._run_translation,
            args=(task,),
            daemon=True,
        )
        task._coordinator = coordinator
        coordinator.start()

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
            task.pause_event.clear()
        task.progress.put({'event': 'paused', 'data': {
            'completed': task.completed,
            'remaining': task.total - task.completed
        }})
        return True

    def resume_translation(self, project_name: str, num_workers: int | None = None) -> bool:
        """Resume a paused translation task, optionally adjusting worker count."""
        task = self.tasks.get(project_name)
        if not task:
            return False
        with task._lock:
            if task.state != 'paused':
                return False
            # Check coordinator or any worker thread is alive
            any_alive = (task._coordinator is not None and task._coordinator.is_alive()) or \
                        any(t.is_alive() for t in task._threads)
            if not any_alive:
                task.state = 'failed'
                task.error = '翻譯執行緒已終止，請重新開始翻譯'
                return False

            # Dynamic worker adjustment
            if num_workers is not None:
                num_workers = max(1, min(num_workers, MAX_WORKERS))
                current_alive = sum(1 for t in task._threads if t.is_alive())
                task.num_workers = num_workers

                if num_workers > current_alive:
                    # Spawn additional workers
                    additional = num_workers - current_alive
                    if task.pm and task._gm:
                        project_dir = os.path.join(task.projects_dir, task.project_name)
                        for i in range(additional):
                            wid = len(task._threads)
                            t = threading.Thread(
                                target=self._worker_loop,
                                args=(task, wid),
                                daemon=True,
                            )
                            task._threads.append(t)
                            task.active_workers += 1
                            t.start()
                        logger.info("動態增加 %d workers (共 %d)", additional, num_workers)

            task.state = 'running'
            task.pause_event.set()
        return True

    def _run_translation(self, task: TranslationTask):
        """Coordinator: set up resources, launch workers, process results."""
        project_dir = os.path.join(task.projects_dir, task.project_name)
        handler = setup_project_logging(project_dir)
        try:
            pm = task.pm

            # Load glossary
            glossary_path = os.path.join(project_dir, 'glossary.json')
            gm = GlossaryManager(glossary_path)
            gm.load()
            task._gm = gm

            num_workers = min(task.num_workers, task.total)
            logger.info("翻譯開始: %s (共 %d 段待翻譯, %d workers, 模型: %s)",
                        task.project_name, task.total, num_workers, pm.data.model)

            # Start worker threads
            workers = []
            for i in range(num_workers):
                t = threading.Thread(
                    target=self._worker_loop,
                    args=(task, i),
                    daemon=True,
                )
                workers.append(t)
            task._threads = workers

            with task._lock:
                task.active_workers = num_workers

            for t in workers:
                t.start()

            # Result processing loop
            while True:
                # Get first result (blocking with timeout)
                try:
                    result = task.result_queue.get(timeout=1)
                except queue.Empty:
                    # Check if all workers are dead
                    with task._lock:
                        if task.active_workers == 0:
                            break
                    continue

                # Drain remaining results (batch)
                batch = [result]
                while True:
                    try:
                        batch.append(task.result_queue.get_nowait())
                    except queue.Empty:
                        break

                # Process batch
                any_success = False
                for res in batch:
                    if res.success:
                        # Update segment in memory
                        pm.mark_translated(res.segment_id, res.translated_text)
                        with task._lock:
                            task.completed += 1
                            completed = task.completed

                        any_success = True
                        logger.info("段落完成 [%d/%d]: %s (%s)",
                                    completed, task.total, res.segment_id, res.model)

                        percent = round(completed / task.total * 100, 1) if task.total > 0 else 0
                        task.progress.put({'event': 'progress', 'data': {
                            'completed': completed,
                            'total': task.total,
                            'percent': percent,
                        }})
                        task.progress.put({'event': 'segment_complete', 'data': {
                            'segment_id': res.segment_id,
                            'translated_text': res.translated_text[:200],
                            'model': res.model,
                        }})
                    else:
                        logger.warning("段落翻譯失敗: %s — %s", res.segment_id, res.error)
                        task.progress.put({'event': 'segment_error', 'data': {
                            'segment_id': res.segment_id,
                            'error': res.error or '',
                        }})

                # Batch save
                if any_success:
                    pm.save()

                # Check if all workers are dead after processing
                with task._lock:
                    if task.active_workers == 0:
                        # Drain any remaining results
                        while True:
                            try:
                                remaining = task.result_queue.get_nowait()
                                if remaining.success:
                                    pm.mark_translated(remaining.segment_id, remaining.translated_text)
                                    task.completed += 1
                                    completed = task.completed
                                    percent = round(completed / task.total * 100, 1) if task.total > 0 else 0
                                    task.progress.put({'event': 'progress', 'data': {
                                        'completed': completed,
                                        'total': task.total,
                                        'percent': percent,
                                    }})
                                    task.progress.put({'event': 'segment_complete', 'data': {
                                        'segment_id': remaining.segment_id,
                                        'translated_text': remaining.translated_text[:200],
                                        'model': remaining.model,
                                    }})
                            except queue.Empty:
                                break
                        pm.save()
                        break

            # Set final state
            if task.stop_event.is_set() or task.state == 'stopped':
                task.state = 'stopped'
                logger.info("翻譯已停止: 已完成 %d 段", task.completed)
                task.progress.put({'event': 'stopped', 'data': {
                    'completed': task.completed,
                    'remaining': task.total - task.completed,
                }})
            elif task.state == 'running':
                task.state = 'completed'
                logger.info("翻譯完成: 共翻譯 %d 段", task.completed)
                task.progress.put({'event': 'complete', 'data': {
                    'total_translated': task.completed,
                }})

        except Exception as e:
            logger.error("Translation task failed: %s", e)
            task.state = 'failed'
            task.error = str(e)
            task.progress.put({'event': 'error', 'data': {
                'error': str(e),
            }})
        finally:
            teardown_project_logging(handler)

    def _worker_loop(self, task: TranslationTask, worker_id: int):
        """Single worker loop: fetch segments from queue and translate."""
        tag = f"W{worker_id}"
        pm = task.pm
        gm = task._gm
        engine = TranslationEngine(model=pm.data.model)
        all_segments = pm.data.segments
        reduced_exit = False

        try:
            while True:
                # Check stop event
                if task.stop_event.is_set():
                    break

                # Block if paused
                task.pause_event.wait()

                # Check stop again (might have been set during pause)
                if task.stop_event.is_set():
                    break

                # Dynamic reduction: if active_workers > num_workers, exit
                with task._lock:
                    if task.active_workers > task.num_workers:
                        task.active_workers -= 1
                        reduced_exit = True
                        logger.info("[%s] Worker 退出 (動態減少)", tag)
                        return

                # Get next segment
                try:
                    seg = task.segment_queue.get(timeout=1)
                except queue.Empty:
                    break  # No more segments

                # Detect model switch
                if pm.data.model != engine.model:
                    logger.info("[%s] 模型切換: %s → %s", tag, engine.model, pm.data.model)
                    engine.model = pm.data.model

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
                    task.result_queue.put(TranslationResult(
                        segment_id=seg.segment_id,
                        translated_text=translated,
                        model=engine.model,
                        success=True,
                    ))

                except (AuthError, RateLimitError) as e:
                    event_name = 'auth_error' if isinstance(e, AuthError) else 'rate_limit'
                    logger.warning("[%s] %s, pausing: %s", tag, event_name, e)

                    # Return segment to queue for retry
                    task.segment_queue.put(seg)

                    with task._lock:
                        # Only first worker to hit this sends the event
                        if task.state == 'running':
                            task.state = 'paused'
                            task.progress.put({'event': event_name, 'data': {
                                'error': str(e),
                                'completed': task.completed,
                                'remaining': task.total - task.completed,
                            }})
                        task.pause_event.clear()

                    # Block until resumed
                    task.pause_event.wait()

                    # Check if we should stop after resuming
                    if task.stop_event.is_set():
                        break
                    if task.state not in ('running',):
                        break

                    # Check dynamic reduction after resume
                    with task._lock:
                        if task.active_workers > task.num_workers:
                            task.active_workers -= 1
                            reduced_exit = True
                            logger.info("[%s] Worker 退出 (動態減少)", tag)
                            return

                    logger.info("[%s] 翻譯繼續", tag)
                    continue

                except TranslationError as e:
                    logger.warning("[%s] Translation failed for %s: %s", tag, seg.segment_id, e)
                    task.result_queue.put(TranslationResult(
                        segment_id=seg.segment_id,
                        success=False,
                        error=str(e),
                    ))

        finally:
            if not reduced_exit:
                with task._lock:
                    task.active_workers -= 1
