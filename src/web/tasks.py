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
from src.core.runcontrol import DebouncedSaver, resume_delay
from src.core.translator import (
    AuthError, BackendUnavailableError, CancelledError, RateLimitError, TranslationEngine,
    TranslationOutcome, UnexpectedStreak, record_failure_log, translate_segment,
)
from src.glossary.glossary_manager import GlossaryManager

logger = logging.getLogger(__name__)

MAX_WORKERS = 20

# How long a new start waits for the threads of a stopped task to end
STOP_WAIT_SECONDS = 5

# How often the coordinator looks at the task when no result arrives (saves
# a waiting window, resumes after a rate limit)
COORDINATOR_TICK_SECONDS = 1

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
    # Set by stop and pause: the calls in flight are killed and their
    # segments go back to the queue. Never cleared: resume installs a new
    # one, so a call still holding the old event is cancelled for good
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    # Clock time (TaskManager.clock) at which a run paused by a rate limit
    # resumes by itself; None: no automatic resume (a user pause, an auth or
    # backend error)
    resume_at: float | None = None
    # Coordinator ticks so far (tests wait for ticks instead of sleeping)
    ticks: int = 0
    # A pause asks the coordinator to save at its next tick
    flush_due: bool = False
    # The coordinator's saver while it runs (the export flushes it)
    saver: DebouncedSaver | None = field(default=None, repr=False)

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
    # Monotonic seconds; tests replace it
    clock = staticmethod(time.monotonic)

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
            task.resume_at = None  # a stopping run does not resume by itself
            task.cancel_event.set()  # the calls in flight end now
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
                    # (a passthrough segment never: it needs no model call)
                    to_translate = [s for s in pm.data.segments
                                    if s.segment_id in id_set and not s.passthrough]
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
        """Pause a running task, or turn the automatic wait of a task paused
        by a rate limit into a lasting user pause. True also when it is
        paused already (idempotent)."""
        task = self.tasks.get(project_name)
        if not task:
            return False
        with task._lock:
            if task.state not in ('running', 'paused'):
                return False
            task.state = 'paused'
            task.resume_at = None
            task.flush_due = True  # saved at the next tick, also if resumed before it
            task.pause_event.clear()
            task.cancel_event.set()  # the calls in flight end; their segments wait
        task.progress.put({'event': 'paused', 'data': {
            'completed': task.completed,
            'remaining': task.total - task.completed
        }})
        return True

    def resume_translation(self, project_name: str, num_workers: int | None = None,
                           due: float | None = None) -> bool:
        """Resume a paused translation task, optionally adjusting worker
        count. True also when it is running (idempotent). `due`: an
        automatic resume for that deadline; refused when the task's deadline
        has changed since (a user pause, an error, a later rate limit)."""
        task = self.tasks.get(project_name)
        if not task:
            return False
        with task._lock:
            if task.stop_event.is_set():
                return False  # stopping
            if due is not None and task.resume_at != due:
                return False
            if task.state == 'running' and due is None:
                return True
            if task.state != 'paused':
                return False
            # Check coordinator or any worker thread is alive
            any_alive = (task._coordinator is not None and task._coordinator.is_alive()) or \
                        any(t.is_alive() for t in task._threads)
            if not any_alive:
                task.state = 'failed'
                task.error = '翻譯執行緒已終止，請重新開始翻譯'
                return False

            # Dynamic worker adjustment; also brings back workers that
            # ended while the queue was empty and a segment was in flight
            if num_workers is not None:
                task.num_workers = max(1, min(num_workers, MAX_WORKERS))
            if not self._top_up_workers(task):
                return False  # the task stays paused with the workers it has

            task.state = 'running'
            task.error = None  # the run-level error that paused it
            task.resume_at = None
            # A new generation: the retiring event is set first, so no call
            # can go on holding an event that nothing will set any more
            task.cancel_event.set()
            task.cancel_event = threading.Event()  # before the workers go on
            if due is not None:  # automatic: announced before anything can pause again
                task.progress.put({'event': 'resumed', 'data': {
                    'completed': task.completed,
                    'remaining': task.total - task.completed,
                }})
            task.pause_event.set()
        return True

    def _top_up_workers(self, task: TranslationTask) -> bool:
        """Start workers until task.num_workers run (under task._lock).
        False, with task.error set, when one cannot be started."""
        # Counted, not alive: a worker that has ended its loop is not one
        additional = task.num_workers - task.active_workers
        if additional <= 0 or not (task.pm and task._gm):
            return True
        for _ in range(additional):
            t = threading.Thread(target=self._worker_loop, args=(task, len(task._threads)),
                                 daemon=True)
            task.active_workers += 1
            try:
                t.start()
            except Exception as e:
                # Not started: neither listed nor counted
                task.active_workers -= 1
                task.error = f'無法增加 worker：{e}'
                return False
            task._threads.append(t)
        logger.info("動態增加 %d workers (共 %d)", additional, task.num_workers)
        return True

    def _run_translation(self, task: TranslationTask):
        """Coordinator: set up resources, launch workers, process results."""
        project_dir = os.path.join(task.projects_dir, task.project_name)
        handler = None
        log_dir = os.path.join(project_dir, 'log')
        pm = task.pm

        def after_save(items):
            # Under the mutex, right after the save: the diagnostics of each
            # result saved, unless its segment changed since it was applied
            # (edited, or translated again, as from the review page)
            for segment_id, outcome, revision, status in items:
                seg = pm._find(segment_id)
                if seg.revision == revision and seg.status == status:
                    record_failure_log(log_dir, segment_id, outcome)

        saver = DebouncedSaver(pm, after_save, clock=self.clock)
        task.saver = saver
        try:
            handler = setup_project_logging(project_dir)

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
            streak_stop = None  # the streak's message once it stopped the run

            # Result processing loop
            while True:
                # Get first result (blocking with timeout)
                try:
                    result = task.result_queue.get(timeout=COORDINATOR_TICK_SECONDS)
                except queue.Empty:
                    self._tick(task, saver)
                    # Done when all workers have exited and nothing is left
                    # (a worker puts its last result before it exits)
                    if self._workers_done(task):
                        break
                    continue

                # Drain remaining results (batch)
                batch = [result]
                while True:
                    try:
                        batch.append(task.result_queue.get_nowait())
                    except queue.Empty:
                        break

                # Apply the batch in memory under the mutex, so an edit
                # cannot come between; saved by the window (_tick)
                with pm.mutex:
                    for i, res in enumerate(batch):
                        if self._apply_result(task, pm, res):
                            # with the status it has now, for after_save
                            saver.add((res.segment_id, res.outcome, res.revision,
                                       pm._find(res.segment_id).status))
                            try:
                                saver.maybe_save()  # the window, after every result
                            except Exception:
                                # The rest of the batch is reported as not saved
                                for rest in batch[i + 1:]:
                                    task.result_queue.put(rest)
                                raise

                # Too many unexpected failures in a row: a run-level error
                stop = None
                for res in batch:
                    stop = streak.add(res.outcome) or stop
                if stop and streak_stop is None:
                    # Stop dispatching; the calls in flight are still
                    # received and saved, then the run fails
                    streak_stop = stop
                    with task._lock:
                        task.stop_event.set()  # resume_translation refuses it now
                        task.pause_event.set()
                self._tick(task, saver)

                # Results that arrived meanwhile are taken by the next round
                if self._workers_done(task):
                    break

            # Every worker has left its loop (active_workers is 0); wait for
            # the threads themselves, so an ended run has no thread left
            for t in list(task._threads):
                t.join()
            saver.flush()  # before the final state: readers then load the file
            if streak_stop:
                raise RuntimeError(streak_stop)

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
            self._fail_task(task, e, saver)
        finally:
            if task.project_lock is not None:
                task.project_lock.release()
            teardown_project_logging(handler)

    def _workers_done(self, task: TranslationTask) -> bool:
        """Every worker has ended and every result was taken. A segment
        still queued (put back by a worker after the others had ended on an
        empty queue) gets workers again instead, unless the run is stopping."""
        with task._lock:
            if task.active_workers != 0 or not task.result_queue.empty():
                return False
            if task.segment_queue.empty() or task.stop_event.is_set():
                return True
            if not self._top_up_workers(task):
                raise RuntimeError(task.error)
            return False

    def _tick(self, task: TranslationTask, saver: DebouncedSaver) -> None:
        """Save a full or expired window, everything when the task is not
        running (paused, stopping); resume a run paused by a rate limit once
        its time has come."""
        if task.state == 'running' and not task.stop_event.is_set() and not task.flush_due:
            saver.maybe_save()
        else:
            task.flush_due = False  # before the flush: a pause meanwhile asks again
            saver.flush()
        with task._lock:
            task.ticks += 1
            due = task.resume_at
            if task.state != 'paused' or due is None or self.clock() < due:
                due = None
        # resume_translation checks the deadline again, under the lock
        # (and publishes 'resumed' with the state change)
        if due is not None and self.resume_translation(task.project_name, due=due):
            logger.info("翻譯自動繼續（使用量限制的等待結束）")

    def _fail_task(self, task: TranslationTask, error: Exception,
                   saver: DebouncedSaver) -> None:
        """The coordinator failed: stop dispatching, end the calls in
        flight, save what can be saved and report every result not saved,
        before the caller releases the lock."""
        with task._lock:
            task.state = 'failed'
            task.resume_at = None  # a failed run does not resume
            task.stop_event.set()
            task.cancel_event.set()
            task.pause_event.set()
        for t in list(task._threads):
            t.join()
        unsaved = []
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
            try:
                saver.flush()
            except Exception:  # noqa: BLE001 - reported below as not saved
                logger.exception("無法儲存專案")
            unsaved = [item[0] for item in saver.pending] + unsaved
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

    def _pause_for(self, task: TranslationTask, error: Exception, event_name: str) -> None:
        """A run-level error pauses the task (under task._lock). A rate limit
        resumes by itself after resume_delay; any other error waits for the
        user and wins over an automatic wait in progress. A later rate limit
        during an automatic wait extends it. A user pause stays as it is."""
        delay = resume_delay(error)
        if task.state == 'running':
            pass
        elif task.state == 'paused' and task.resume_at is not None:
            if delay is not None:  # another rate limit during the wait
                task.resume_at = max(task.resume_at, self.clock() + delay)
                task.progress.put({'event': 'rate_limit', 'data': {
                    'error': task.error,
                    'completed': task.completed,
                    'remaining': task.total - task.completed,
                    'resume_in': task.resume_at - self.clock(),
                }})
                return
        else:
            return  # paused by the user, or by an error that waits for the user
        task.state = 'paused'
        task.flush_due = True  # saved at the next tick, also if resumed before it
        task.error = str(error)  # for a page opened later
        task.resume_at = None if delay is None else self.clock() + delay
        task.progress.put({'event': event_name, 'data': {
            'error': str(error),
            'completed': task.completed,
            'remaining': task.total - task.completed,
            'resume_in': delay,
        }})

    def _worker_loop(self, task: TranslationTask, worker_id: int):
        """Single worker loop: fetch segments from queue and translate."""
        tag = f"W{worker_id}"
        pm = task.pm
        gm = task._gm
        engine, current = None, None  # made for the project's (backend, model)

        def interrupted() -> bool:
            # A retry's backoff wait ends, and no call is made, once the
            # run stops or pauses (also for a rate limit another worker hit)
            return task.stop_event.is_set() or not task.pause_event.is_set()

        all_segments = pm.data.segments
        translations = pm.data.segmentation == 2  # context from translations
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
                if not task.pause_event.is_set() or task.stop_event.is_set():
                    # Paused (or stopped) while this worker waited for it
                    task.segment_queue.put(seg)
                    continue

                # The project's backend, model, effort and timeout, read
                # again for every segment (as one, under the mutex): a
                # switch during the run applies to the next one
                with pm.mutex:
                    wanted = pm.call_settings()
                    brief = pm.data.brief or ''  # an edit applies to the next segment
                if wanted != current:
                    if current is not None and wanted.model != current.model:
                        logger.info("[%s] 模型切換: %s → %s", tag, current.model, wanted.model)
                    current = wanted
                    engine = TranslationEngine(**current.call_args(), interrupted=interrupted)

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

                generation = task.cancel_event  # replaced by every resume
                try:
                    # A failed segment, also one with an unexpected error, is
                    # an outcome: recorded as FAILED
                    outcome = translate_segment(engine, seg, all_segments, gm,
                                                cancel=generation,
                                                translations=translations, mutex=pm.mutex,
                                                brief=brief)
                    task.result_queue.put(TranslationResult(
                        segment_id=seg.segment_id, outcome=outcome, revision=revision))

                except CancelledError:
                    # Stopped or paused while in flight: the process is
                    # gone, nothing was stored; the segment waits again
                    task.segment_queue.put(seg)
                    continue

                except RUN_ERRORS as e:
                    event_name = next(name for cls, name in RUN_ERROR_EVENTS
                                      if isinstance(e, cls))
                    logger.warning("[%s] %s, pausing: %s", tag, event_name, e)

                    with task._lock:
                        # Stopped meanwhile: do not pause a task that is ending
                        if task.stop_event.is_set():
                            break
                        if task.cancel_event is generation:
                            self._pause_for(task, e, event_name)
                            task.pause_event.clear()
                            # Like a user pause: the other calls in flight
                            # end; their segments go back to the queue
                            task.cancel_event.set()
                        # else: a call from before a pause and resume; that
                        # pause already decided, so its error pauses nothing
                        # again (the segment is just sent again)
                    # Back in the queue once the pause is in place, so no
                    # other worker starts it meanwhile
                    task.segment_queue.put(seg)
                    # The top waits for the resume, checks stop and the
                    # worker count (a new pause meanwhile: it waits again)
                    continue

        finally:
            if not reduced_exit:
                with task._lock:
                    task.active_workers -= 1
