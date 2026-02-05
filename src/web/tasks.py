"""Background task manager for long-running translation jobs."""

from __future__ import annotations

import os
import queue
import threading
import time
import logging
from dataclasses import dataclass, field

from src.core.project import ProjectManager
from src.core.translator import TranslationEngine, TranslationError
from src.glossary.glossary_manager import GlossaryManager

logger = logging.getLogger(__name__)


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
    pause_event: threading.Event = field(default_factory=lambda: threading.Event())
    progress_queue: queue.Queue = field(default_factory=queue.Queue)
    _thread: threading.Thread | None = field(default=None, repr=False)

    def __post_init__(self):
        # pause_event is SET when running, CLEARED when paused
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

    def start_translation(self, project_name: str, projects_dir: str) -> TranslationTask:
        """Start a new translation task for a project."""
        existing = self.tasks.get(project_name)
        if existing and existing.state == 'running':
            raise ValueError('翻譯任務已在執行中')

        task = TranslationTask(
            task_id=f"{project_name}_{int(time.time())}",
            project_name=project_name,
            projects_dir=projects_dir,
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
        if not task or task.state != 'running':
            return False
        task.state = 'paused'
        task.pause_event.clear()  # Block the translation loop
        task.progress_queue.put({'event': 'paused', 'data': {
            'completed': task.completed,
            'remaining': task.total - task.completed
        }})
        return True

    def resume_translation(self, project_name: str) -> bool:
        """Resume a paused translation task."""
        task = self.tasks.get(project_name)
        if not task or task.state != 'paused':
            return False
        task.state = 'running'
        task.pause_event.set()  # Unblock the translation loop
        return True

    def _run_translation(self, task: TranslationTask):
        """Execute translation in background thread."""
        try:
            project_dir = os.path.join(task.projects_dir, task.project_name)
            pm = ProjectManager(project_dir)
            pm.load()

            # Load glossary
            glossary_path = os.path.join(project_dir, 'glossary.json')
            gm = GlossaryManager(glossary_path)
            gm.load()

            engine = TranslationEngine()
            all_segments = pm.data.segments
            pending = pm.get_pending_segments()

            for seg in pending:
                # Check for pause
                task.pause_event.wait()

                # Check if task was stopped while paused
                if task.state not in ('running',):
                    break

                task.current_segment = seg.segment_id

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
                    html_arg = seg.html if seg.segment_type in ('text', 'table') else ''
                    translated = engine.translate(
                        seg.text,
                        segment_type=seg.segment_type,
                        glossary_text=glossary_text,
                        context_before=context_before,
                        context_after=context_after,
                        html=html_arg,
                    )
                    pm.mark_translated(seg.segment_id, translated)
                    task.completed += 1

                    # Push progress event
                    percent = round(task.completed / task.total * 100, 1) if task.total > 0 else 0
                    task.progress_queue.put({'event': 'progress', 'data': {
                        'completed': task.completed,
                        'total': task.total,
                        'percent': percent,
                    }})
                    task.progress_queue.put({'event': 'segment_complete', 'data': {
                        'segment_id': seg.segment_id,
                        'translated_text': translated[:200],
                    }})

                except TranslationError as e:
                    logger.warning("Translation failed for %s: %s", seg.segment_id, e)
                    task.progress_queue.put({'event': 'segment_error', 'data': {
                        'segment_id': seg.segment_id,
                        'error': str(e),
                    }})
                    # Continue to next segment instead of stopping

            if task.state == 'running':
                task.state = 'completed'
                task.current_segment = None
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
