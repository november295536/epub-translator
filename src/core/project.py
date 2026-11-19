"""Project manager - handles project state, progress tracking, and persistence."""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from dataclasses import dataclass, field, fields, asdict
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.segmenter import Segment
    from src.core.translator import TranslationOutcome


class SegmentStatus(str, Enum):
    """Segment translation status."""
    PENDING = 'pending'
    TRANSLATED = 'translated'
    REVIEWED = 'reviewed'
    # Translation attempted and failed; not queued again unless asked for.
    # A project holding this value cannot be loaded by v1.0.0.
    FAILED = 'failed'

    def __str__(self):
        return self.value


# Segmenter behaviour recorded on projects created now (see ProjectData)
SEGMENTER_VERSION = 2


@dataclass
class Issue:
    """A typed problem found with a segment's translation."""
    kind: str        # structure | quality | glossary | backend
    code: str        # e.g. empty_reply, transient, refusal
    detail: str
    blocking: bool


# Per-segment fields written to project.json only when they differ from
# these defaults, so an existing project round-trips without new keys
_SEGMENT_DEFAULTS = {'issues': [], 'revision': 0, 'model': '', 'usage': None}


@dataclass
class SegmentState:
    """State of a single segment in the project."""
    segment_id: str
    chapter_id: str
    html: str
    text: str
    word_count: int
    segment_type: str
    status: SegmentStatus = SegmentStatus.PENDING
    translated_text: str = ''
    translated_at: str = ''
    issues: list[Issue] = field(default_factory=list)
    # Incremented by every manual edit; a translation result computed for an
    # older revision is dropped (see apply_outcome)
    revision: int = 0
    model: str = ''              # model that produced translated_text
    usage: dict | None = None    # token usage of that call, when reported


@dataclass
class ProjectData:
    """Full project data structure."""
    epub_path: str
    project_dir: str
    title: str = ''
    author: str = ''
    created_at: str = ''
    updated_at: str = ''
    model: str = 'sonnet'
    # 1: segmented before comments were skipped; never written to project.json,
    # so projects that existed before Phase 0 remain loadable by pre-Phase-0
    # code. 2: comments skipped; always written, so projects created after
    # Phase 0 are not. The builder walks chapters to match.
    segmenter_version: int = 1
    segments: list[SegmentState] = field(default_factory=list)


class ProjectManager:
    """Manages translation project state with JSON persistence."""

    PROJECT_FILE = 'project.json'

    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self.project_file = os.path.join(project_dir, self.PROJECT_FILE)
        self.data: ProjectData | None = None
        self._save_lock = threading.Lock()

    def create(self, epub_path: str, title: str, author: str,
               segments: list[Segment], chapter_ids: dict[str, str] | None = None) -> ProjectData:
        """Create a new translation project.

        Args:
            epub_path: Path to the source EPUB file.
            title: Book title.
            author: Book author.
            segments: List of segments from the segmenter.
            chapter_ids: Optional mapping of segment prefix to chapter id.
        """
        os.makedirs(self.project_dir, exist_ok=True)

        now = time.strftime('%Y-%m-%d %H:%M:%S')
        segment_states = []
        for seg in segments:
            chapter_id = seg.id.rsplit('_seg', 1)[0] if '_seg' in seg.id else seg.id
            segment_states.append(SegmentState(
                segment_id=seg.id,
                chapter_id=chapter_id,
                html=seg.html,
                text=seg.text,
                word_count=seg.word_count,
                segment_type=seg.segment_type,
            ))

        self.data = ProjectData(
            epub_path=os.path.abspath(epub_path),
            project_dir=self.project_dir,
            title=title,
            author=author,
            created_at=now,
            updated_at=now,
            segmenter_version=SEGMENTER_VERSION,
            segments=segment_states,
        )
        self.save()
        return self.data

    def load(self) -> ProjectData:
        """Load project from disk."""
        with open(self.project_file, 'r', encoding='utf-8') as f:
            raw = json.load(f)

        raw_segments = raw.pop('segments', [])
        valid_fields = {f.name for f in fields(SegmentState)}
        for s in raw_segments:
            # Normalize legacy segment types: heading/table → text
            if s.get('segment_type') in ('heading', 'table'):
                s['segment_type'] = 'text'
            # Convert status string to enum
            s['status'] = SegmentStatus(s.get('status', 'pending'))
            s['issues'] = [Issue(**i) for i in s.get('issues', [])]
            # Strip unknown fields from older/experimental schemas
            for key in list(s.keys()):
                if key not in valid_fields:
                    del s[key]
        segments = [SegmentState(**s) for s in raw_segments]
        self.data = ProjectData(**raw, segments=segments)
        return self.data

    def save(self) -> None:
        """Save project to disk atomically via temp file + os.replace()."""
        if not self.data:
            raise RuntimeError("No project data to save.")

        with self._save_lock:
            self.data.updated_at = time.strftime('%Y-%m-%d %H:%M:%S')
            raw = asdict(self.data)
            if raw['segmenter_version'] == 1:
                # Leave the field out, so pre-Phase-0 code can still load it
                del raw['segmenter_version']
            for seg in raw['segments']:
                for key, default in _SEGMENT_DEFAULTS.items():
                    if seg[key] == default:
                        del seg[key]
            # Unique per process: the lock below excludes other processes,
            # the name keeps a stray writer from sharing the file anyway
            tmp_file = f'{self.project_file}.{os.getpid()}.tmp'
            with ProjectLock(self.project_dir):
                with open(tmp_file, 'w', encoding='utf-8') as f:
                    json.dump(raw, f, ensure_ascii=False, indent=2)
                os.replace(tmp_file, self.project_file)

    def set_model(self, model: str) -> None:
        """Set the translation model (in-memory only; call save() to persist)."""
        if not self.data:
            raise RuntimeError("No project loaded.")
        self.data.model = model

    def mark_translated(self, segment_id: str, translated_text: str) -> None:
        """Mark a segment as translated (in-memory only; call save() to persist)."""
        if not self.data:
            raise RuntimeError("No project loaded.")
        for seg in self.data.segments:
            if seg.segment_id == segment_id:
                seg.status = SegmentStatus.TRANSLATED
                seg.translated_text = translated_text
                seg.translated_at = time.strftime('%Y-%m-%d %H:%M:%S')
                return
        raise ValueError(f"Segment not found: {segment_id}")

    def apply_outcome(self, segment_id: str, outcome: TranslationOutcome,
                      revision: int | None = None) -> bool:
        """Store a translation outcome (in-memory; call save() to persist).

        Returns False, changing nothing, when `revision` is given and the
        segment has been edited since (its revision differs). A failure
        keeps the text and status of a translated or reviewed segment and
        only attaches the issues; any other segment becomes FAILED.
        """
        seg = self._find(segment_id)
        if revision is not None and seg.revision != revision:
            return False
        seg.issues = list(outcome.issues)
        if outcome.status == SegmentStatus.FAILED:
            if seg.status == SegmentStatus.PENDING:
                seg.status = SegmentStatus.FAILED
            return True
        seg.status = SegmentStatus.TRANSLATED
        seg.translated_text = outcome.text
        seg.translated_at = time.strftime('%Y-%m-%d %H:%M:%S')
        seg.model = outcome.model
        seg.usage = asdict(outcome.usage) if outcome.usage else None
        return True

    def _find(self, segment_id: str) -> SegmentState:
        if not self.data:
            raise RuntimeError("No project loaded.")
        for seg in self.data.segments:
            if seg.segment_id == segment_id:
                return seg
        raise ValueError(f"Segment not found: {segment_id}")

    def get_pending_segments(self, include_failed: bool = False) -> list[SegmentState]:
        """Get all segments that haven't been translated yet, in order.
        FAILED segments are included only when asked for."""
        if not self.data:
            raise RuntimeError("No project loaded.")
        wanted = {SegmentStatus.PENDING}
        if include_failed:
            wanted.add(SegmentStatus.FAILED)
        return [s for s in self.data.segments if s.status in wanted]

    def get_progress(self) -> dict:
        """Get translation progress statistics."""
        if not self.data:
            raise RuntimeError("No project loaded.")

        total = len(self.data.segments)
        translated = sum(1 for s in self.data.segments if s.status == SegmentStatus.TRANSLATED)
        reviewed = sum(1 for s in self.data.segments if s.status == SegmentStatus.REVIEWED)
        pending = sum(1 for s in self.data.segments if s.status == SegmentStatus.PENDING)
        failed = sum(1 for s in self.data.segments if s.status == SegmentStatus.FAILED)

        return {
            'total': total,
            'translated': translated,
            'reviewed': reviewed,
            'pending': pending,
            'failed': failed,
            'percent': round(((translated + reviewed) / total * 100) if total > 0 else 0, 1),
        }

    def mark_reviewed(self, segment_id: str, text: str = '') -> None:
        """Mark a segment as reviewed (in-memory only; call save() to persist)."""
        if not self.data:
            raise RuntimeError("No project loaded.")
        for seg in self.data.segments:
            if seg.segment_id == segment_id:
                if text:
                    seg.translated_text = text
                seg.status = SegmentStatus.REVIEWED
                seg.revision += 1
                return
        raise ValueError(f"Segment not found: {segment_id}")

    def update_translation(self, segment_id: str, text: str) -> None:
        """Update the translated text without changing status (in-memory; call save() to persist)."""
        if not self.data:
            raise RuntimeError("No project loaded.")
        for seg in self.data.segments:
            if seg.segment_id == segment_id:
                seg.translated_text = text
                seg.revision += 1
                return
        raise ValueError(f"Segment not found: {segment_id}")

    def get_segments_by_chapter(self, chapter_id: str) -> list[SegmentState]:
        """Get all segments belonging to a specific chapter."""
        if not self.data:
            raise RuntimeError("No project loaded.")
        return [s for s in self.data.segments if s.chapter_id == chapter_id]

    def get_chapters(self) -> list[dict]:
        """Get a list of unique chapter IDs with segment counts."""
        if not self.data:
            raise RuntimeError("No project loaded.")

        chapters: dict[str, dict] = {}
        for seg in self.data.segments:
            cid = seg.chapter_id
            if cid not in chapters:
                chapters[cid] = {'chapter_id': cid, 'total': 0, 'translated': 0,
                                 'reviewed': 0, 'failed': 0}
            chapters[cid]['total'] += 1
            if seg.status == SegmentStatus.TRANSLATED:
                chapters[cid]['translated'] += 1
            elif seg.status == SegmentStatus.REVIEWED:
                chapters[cid]['reviewed'] += 1
            elif seg.status == SegmentStatus.FAILED:
                chapters[cid]['failed'] += 1
        return list(chapters.values())

    def exists(self) -> bool:
        """Check if a project file exists in the project directory."""
        return os.path.isfile(self.project_file)


class ProjectLockedError(Exception):
    """Another process holds the project lock."""


# Locks held by this process: lock path -> [file descriptor, hold count]
_held_locks: dict[str, list] = {}
_held_locks_guard = threading.Lock()


class ProjectLock:
    """One writer process per project, shared by the web server and the CLI.

    An exclusive flock on `<project>/project.lock`. Threads of the holding
    process share it (counted), other processes get ProjectLockedError. The
    kernel drops the lock when the holder exits, however it exits, so a
    lock left by a dead process never blocks. The file holds the holder's
    PID for the error message and is left in place on release.
    """

    FILE = 'project.lock'

    def __init__(self, project_dir: str):
        self.path = os.path.realpath(os.path.join(project_dir, self.FILE))
        self._held = False

    def acquire(self) -> None:
        if self._held:
            return
        with _held_locks_guard:
            entry = _held_locks.get(self.path)
            if entry is not None:
                entry[1] += 1
            else:
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    holder = os.read(fd, 32).decode().strip()
                    os.close(fd)
                    raise ProjectLockedError(
                        f'專案正由另一個程序使用（PID {holder}）：{self.path}')
                os.ftruncate(fd, 0)
                os.write(fd, str(os.getpid()).encode())
                _held_locks[self.path] = [fd, 1]
        self._held = True

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        with _held_locks_guard:
            entry = _held_locks[self.path]
            entry[1] -= 1
            if entry[1] == 0:
                del _held_locks[self.path]
                os.close(entry[0])  # closing the only descriptor drops the flock

    def __enter__(self) -> ProjectLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
