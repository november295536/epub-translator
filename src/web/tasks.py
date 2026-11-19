"""Background task manager for long-running translation jobs."""

from __future__ import annotations

import os
import queue
import threading
import time
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field

from src.core.project import ProjectLock, ProjectManager, SegmentStatus, project_mutex
from src.core.translator import (
    AuthError, BackendUnavailableError, RateLimitError, TranslationEngine,
    TranslationOutcome, UnexpectedStreak, record_failure_log, translate_segment,
)
from src.glossary.glossary_manager import GlossaryManager

logger = logging.getLogger(__name__)

MAX_WORKERS = 20

# How long a new start waits for the threads of a stopped task to end
STOP_WAIT_SECONDS = 5

# Errors that stop (pause) a whole run, leaving the segment pending, and the
# event each one sends
RUN_ERROR_EVENTS = (
    (AuthError, 'auth_error'),
    (RateLimitError, 'rate_limit'),
    (BackendUnavailableError, 'backend_error'),
)
RUN_ERRORS = tuple(cls for cls, _ in RUN_ERROR_EVENTS)


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
    outcome: TranslationOutcome | None  # None: skipped, edited since queued
    revision: int  # segment revision when the segment was queued


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
    project_lock: ProjectLock | None = None
    _gm: GlossaryManager | None = field(default=None, repr=False)
    _threads: list = field(default_factory=list, repr=False)
    _coordinator: threading.Thread | None = field(default=None, repr=False)
    # segment id -> (revision, status) when the segment was queued
    queued: dict = field(default_factory=dict, repr=False)

    def threads_alive(self) -> bool:
        """True while the coordinator or any worker of this task runs."""
        return ((self._coordinator is not None and self._coordinator.is_alive())
                or any(t.is_alive() for t in self._threads))

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
                    # Serializes starting tasks (check, load, register)
                    cls._instance._start_lock = threading.Lock()
        return cls._instance

    def get_task(self, project_name: str) -> TranslationTask | None:
        """Get the current task for a project."""
        return self.tasks.get(project_name)

    @staticmethod
    def _owning_pm(task: TranslationTask | None) -> ProjectManager | None:
        """The task's in-memory manager while the task may still save it
        (running, paused, or stopped with its coordinator still ending)."""
        if task is None or task.pm is None:
            return None
        if task.state in ('running', 'paused') or task.threads_alive():
            return task.pm
        return None

    def get_project_manager(self, project_name: str) -> ProjectManager | None:
        """Return the running task's ProjectManager, or None if no active task."""
        return self._owning_pm(self.tasks.get(project_name))

    def get_or_load_pm(self, project_name: str, projects_dir: str) -> ProjectManager:
        """Return running task's PM (in-memory, latest) or load from disk."""
        pm = self._owning_pm(self.tasks.get(project_name))
        if pm is not None:
            return pm
        pm = ProjectManager(os.path.join(projects_dir, project_name))
        pm.load()
        return pm

    @contextmanager
    def transaction(self, project_name: str, projects_dir: str):
        """Load, change and save a project as one step: holds the project's
        mutex and its file lock (other processes; ProjectLockedError when
        another process has it) and yields the authoritative manager (the
        task's, otherwise a fresh load from disk). The caller saves inside
        the block. FileNotFoundError, before any lock file is created, when
        the project does not exist."""
        project_dir = os.path.join(projects_dir, project_name)
        with project_mutex(project_dir):
            if not os.path.isfile(os.path.join(project_dir, ProjectManager.PROJECT_FILE)):
                raise FileNotFoundError('專案不存在')
            with ProjectLock(project_dir):
                yield self.get_or_load_pm(project_name, projects_dir)

    def task_active(self, project_name: str) -> bool:
        """True while a task of the project runs, is paused or is ending."""
        task = self.tasks.get(project_name)
        return task is not None and (task.state in ('running', 'paused')
                                     or task.threads_alive())

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
                          segment_ids: list[str] | None = None,
                          retry_failed: bool = False) -> TranslationTask:
        """Start a new translation task for a project.

        Args:
            segment_ids: Optional list of segment IDs to retranslate.
                         When provided, queues those specific segments regardless of status.
                         When None, queues all pending segments (normal translation).
            retry_failed: With segment_ids None, also queue FAILED segments.

        Raises:
            FileNotFoundError: the project does not exist.
            ValueError: a task is running.
            ProjectLockedError: another process holds the project lock
                (this task holds it until it ends).
        """
        with self._start_lock:
            return self._start_locked(project_name, projects_dir, num_workers,
                                      segment_ids, retry_failed)

    def _start_locked(self, project_name, projects_dir, num_workers,
                      segment_ids, retry_failed) -> TranslationTask:
        existing = self.tasks.get(project_name)
        if existing and existing.state == 'running':
            raise ValueError('翻譯任務已在執行中')
        # Stop any lingering paused task before creating a new one
        if existing and existing.state == 'paused':
            self.stop_translation(project_name)
        # The old task's threads must have ended: a timed-out wait is not an end
        if existing is not None:
            if existing._coordinator is not None:
                existing._coordinator.join(timeout=STOP_WAIT_SECONDS)
            if existing.threads_alive():
                raise ValueError('上一個翻譯任務仍在結束中（等待進行中的段落），請稍後再試')

        num_workers = max(1, min(num_workers, MAX_WORKERS))

        task = TranslationTask(
            task_id=f"{project_name}_{int(time.time())}",
            project_name=project_name,
            projects_dir=projects_dir,
            num_workers=num_workers,
        )

        # Get segments to translate
        project_dir = os.path.join(projects_dir, project_name)
        lock = ProjectLock(project_dir)
        # The mutex, then the file lock, as everywhere. Under the mutex until
        # the task is registered: an edit either lands before the load or
        # goes to the task's manager, and a delete sees the task
        with project_mutex(project_dir):
            # No lock file in a directory that is not (or no longer) a project
            if not os.path.isfile(os.path.join(project_dir, ProjectManager.PROJECT_FILE)):
                raise FileNotFoundError('專案不存在')
            lock.acquire()  # raises ProjectLockedError
            try:
                pm = ProjectManager(project_dir)
                pm.load()

                if segment_ids is not None:
                    # Retranslate mode: queue specified segments regardless of status
                    id_set = set(segment_ids)
                    to_translate = [s for s in pm.data.segments if s.segment_id in id_set]
                else:
                    to_translate = pm.get_pending_segments(include_failed=retry_failed)

                task.total = len(to_translate)
                if task.total == 0:
                    lock.release()
                    task.state = 'completed'
                    self.tasks[project_name] = task
                    return task

                task.state = 'running'
                task.pm = pm
                task.project_lock = lock  # released by the coordinator when it ends

                # Fill segment queue; a later edit of a queued segment wins
                for seg in to_translate:
                    task.queued[seg.segment_id] = (seg.revision, seg.status)
                    task.segment_queue.put(seg)

                coordinator = threading.Thread(
                    target=self._run_translation,
                    args=(task,),
                    daemon=True,
                )
                task._coordinator = coordinator
                coordinator.start()
                self.tasks[project_name] = task
            except BaseException:
                if task.project_lock is None or not task.threads_alive():
                    # Nothing started that would release it
                    task.state = 'failed'
                    lock.release()
                raise

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
            task.error = None  # the run-level error that paused it
            task.pause_event.set()
        return True

    def _run_translation(self, task: TranslationTask):
        """Coordinator: set up resources, launch workers, process results."""
        project_dir = os.path.join(task.projects_dir, task.project_name)
        handler = None
        unsaved = []  # results accepted in memory since the last save
        log_dir = os.path.join(project_dir, 'log')
        try:
            handler = setup_project_logging(project_dir)
            pm = task.pm

            # Load glossary
            glossary_path = os.path.join(project_dir, 'glossary.json')
            gm = GlossaryManager(glossary_path)
            gm.load()
            task._gm = gm

            num_workers = min(task.num_workers, task.total)
            logger.info("翻譯開始: %s (共 %d 段待翻譯, %d workers, 模型: %s)",
                        task.project_name, task.total, num_workers, pm.data.model)

            # Start worker threads; _threads lists only those that started
            with task._lock:
                task.active_workers = num_workers
            for i in range(num_workers):
                t = threading.Thread(
                    target=self._worker_loop,
                    args=(task, i),
                    daemon=True,
                )
                try:
                    t.start()
                except BaseException:
                    with task._lock:
                        task.active_workers -= num_workers - i  # never started
                    raise
                task._threads.append(t)

            streak = UnexpectedStreak()

            # Result processing loop
            while True:
                # Get first result (blocking with timeout)
                try:
                    result = task.result_queue.get(timeout=1)
                except queue.Empty:
                    # Done when all workers have exited and nothing is left
                    # (a worker puts its last result before it exits)
                    with task._lock:
                        workers_done = task.active_workers == 0
                    if workers_done and task.result_queue.empty():
                        break
                    continue

                # Drain remaining results (batch)
                batch = [result]
                while True:
                    try:
                        batch.append(task.result_queue.get_nowait())
                    except queue.Empty:
                        break

                # Process batch, save once, then the diagnostics of what was
                # accepted: one step under the mutex, so an edit cannot
                # come between
                with pm.mutex:
                    accepted = [res for res in batch if self._apply_result(task, pm, res)]
                    unsaved.extend(res.segment_id for res in accepted)
                    pm.save()
                    unsaved.clear()
                    for res in accepted:
                        record_failure_log(log_dir, res.segment_id, res.outcome)

                # Too many unexpected failures in a row: a run-level error
                stop = None
                for res in batch:
                    stop = streak.add(res.outcome) or stop
                if stop:
                    raise RuntimeError(stop)

                # Results that arrived meanwhile are taken by the next round
                with task._lock:
                    workers_done = task.active_workers == 0
                if workers_done and task.result_queue.empty():
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
            self._fail_task(task, e, unsaved)
        finally:
            if task.project_lock is not None:
                task.project_lock.release()
            teardown_project_logging(handler)

    def _fail_task(self, task: TranslationTask, error: Exception, unsaved: list) -> None:
        """The coordinator failed: stop dispatching, wake paused workers,
        wait for the calls in flight and report every result not saved,
        before the caller releases the lock."""
        with task._lock:
            task.state = 'failed'
            task.stop_event.set()
            task.pause_event.set()
        for t in list(task._threads):
            t.join()
        with task.pm.mutex:
            while True:
                try:
                    res = task.result_queue.get_nowait()
                except queue.Empty:
                    break
                # A skipped segment had nothing to save; a stale result
                # (edited meanwhile) would have been dropped
                if (res.outcome is not None
                        and task.pm._find(res.segment_id).revision == res.revision):
                    unsaved.append(res.segment_id)
        message = str(error)
        if unsaved:
            message += f'（{len(unsaved)} 個段落的結果未儲存：{", ".join(unsaved)}）'
        logger.error("Translation task failed: %s", message)
        task.error = message
        task.progress.put({'event': 'error', 'data': {
            'error': message,
            'unsaved': unsaved,
        }})

    def _apply_result(self, task: TranslationTask, pm: ProjectManager,
                      res: TranslationResult) -> bool:
        """Store one worker result in memory and report it (coordinator only).
        True when the outcome was accepted (not skipped, not stale)."""
        outcome = res.outcome
        if outcome is None:
            # Skipped by the worker: edited or approved after it was queued
            logger.info("段落已在排隊期間被編輯，略過: %s", res.segment_id)
        elif not pm.apply_outcome(res.segment_id, outcome, revision=res.revision):
            # Edited while its translation was in flight: the edit wins
            logger.info("段落已在翻譯期間被編輯，捨棄結果: %s", res.segment_id)
            outcome = None
        elif outcome.status == SegmentStatus.FAILED:
            detail = outcome.issues[0].detail
            logger.warning("段落翻譯失敗: %s — %s", res.segment_id, detail)
            task.progress.put({'event': 'segment_error', 'data': {
                'segment_id': res.segment_id,
                'error': detail,
            }})
            return True
        with task._lock:
            task.completed += 1
            completed = task.completed
        task.progress.put({'event': 'progress', 'data': {
            'completed': completed,
            'total': task.total,  # > 0: a task with nothing to do has no workers
            'percent': round(completed / task.total * 100, 1),
        }})
        if outcome is None:
            return False  # nothing of the machine's was kept: no success event
        logger.info("段落完成 [%d/%d]: %s (%s)",
                    completed, task.total, res.segment_id, outcome.model)
        task.progress.put({'event': 'segment_complete', 'data': {
            'segment_id': res.segment_id,
            'translated_text': outcome.text[:200],
            'model': outcome.model,
        }})
        return True

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

                revision, queued_status = task.queued.get(
                    seg.segment_id, (seg.revision, seg.status))
                with pm.mutex:
                    # Edited or approved since it was queued: the person's
                    # version wins, so do not spend a call on it
                    skip = seg.revision != revision or (
                        seg.status == SegmentStatus.REVIEWED
                        and queued_status != SegmentStatus.REVIEWED)
                if skip:
                    task.result_queue.put(TranslationResult(
                        segment_id=seg.segment_id, outcome=None, revision=revision))
                    continue

                try:
                    # A failed segment, also one with an unexpected error, is
                    # an outcome: recorded as FAILED
                    outcome = translate_segment(engine, seg, all_segments, gm)
                    task.result_queue.put(TranslationResult(
                        segment_id=seg.segment_id, outcome=outcome, revision=revision))

                except RUN_ERRORS as e:
                    event_name = next(name for cls, name in RUN_ERROR_EVENTS
                                      if isinstance(e, cls))
                    logger.warning("[%s] %s, pausing: %s", tag, event_name, e)

                    # Return segment to queue for retry
                    task.segment_queue.put(seg)

                    with task._lock:
                        # Stopped meanwhile: do not pause a task that is ending
                        if task.stop_event.is_set():
                            break
                        # Only first worker to hit this sends the event
                        if task.state == 'running':
                            task.state = 'paused'
                            task.error = str(e)  # for a page opened later
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

        finally:
            if not reduced_exit:
                with task._lock:
                    task.active_workers -= 1
