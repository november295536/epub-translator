"""Project manager - handles project state, progress tracking, and persistence."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field, fields, asdict
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import src.llm as llm

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


# project.json layout written for projects created now (see ProjectData)
SCHEMA = 2

# The segmentation rules there are (src/core/segmenter.py)
SEGMENTATION_RULES = (1, 2)


def check_rules(value) -> int:
    """`value` when it is one of the segmentation rules (the integer 1 or 2:
    not a string, a bool, a float or None); raises ValueError otherwise."""
    if type(value) is not int or value not in SEGMENTATION_RULES:
        raise ValueError(f'unknown segmentation rules: {value!r}')
    return value


# Upper bound of a book's brief: it goes into every text prompt, which the
# CLI backends pass as one command-line argument
MAX_BRIEF_CHARS = 2000


def check_backend(value) -> str:
    """`value` when it names a backend (src.llm.BACKENDS); raises ValueError
    otherwise."""
    if type(value) is not str or value not in llm.BACKENDS:
        raise ValueError(f'unknown backend: {value!r}')
    return value


def sha256_file(path: str) -> str:
    """SHA-256 of a file, hex."""
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class Issue:
    """A typed problem found with a segment's translation."""
    kind: str        # structure | quality | glossary | backend
    code: str        # e.g. empty_reply, transient, refusal
    detail: str
    blocking: bool


# Per-segment fields written to project.json only when they differ from
# these defaults, so an existing project round-trips without new keys
_SEGMENT_DEFAULTS = {'issues': [], 'revision': 0, 'model': '', 'usage': None,
                     'block_start': None, 'block_end': None, 'fingerprints': [],
                     'passthrough': False}


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
    # Schema 2: the half-open range of the chapter's blocks the segment
    # covers, and their fingerprints
    block_start: int | None = None
    block_end: int | None = None
    fingerprints: list[str] = field(default_factory=list)
    # Segmentation rules 2: a code block without a comment line. Created
    # TRANSLATED with the code itself as its text; never sent to a model,
    # never edited, left as it is by the export
    passthrough: bool = False


@dataclass
class ProjectData:
    """Full project data structure."""
    epub_path: str
    project_dir: str
    title: str = ''
    author: str = ''
    created_at: str = ''
    updated_at: str = ''
    # Filled with the backend's default model on create, and on load when
    # the file has none (projects from before the field)
    model: str = ''
    # Schema 1 only. 1: segmented before comments were skipped; never written
    # to project.json, so projects that existed before Phase 0 remain loadable
    # by pre-Phase-0 code. 2: comments skipped; always written, so projects
    # created after Phase 0 are not. Kept so schema 1 files round-trip.
    segmenter_version: int = 1
    # 1: legacy segments; cannot be exported until migrated (never
    # written). 2: segments are block ranges with fingerprints (plan 4.2);
    # the field is written, and segmenter_version is not.
    schema: int = 1
    # SHA-256 of the project's copy of the source EPUB (schema 2)
    source_sha256: str = ''
    # The segmentation rules the segments were cut by (src/core/segmenter.py),
    # 1 or 2; None: the file has no such key (projects from before Phase 6),
    # which means rules 1 and is never written, so those files round-trip
    # unchanged. Projects created since have 2. With 2 the prompt's context is
    # the previous segment's translation when it has one (translator.get_context)
    segmentation: int | None = None
    # The backend `model` belongs to (a name of src.llm.BACKENDS); None: the
    # file has no such key, which means the Claude CLI and is never written,
    # so existing files round-trip unchanged
    backend: str | None = None
    # The book's brief (genre, tone, audience), placed at the top of the
    # translation prompt; None: the file has no such key, which is never
    # written, so existing files round-trip unchanged
    brief: str | None = None
    segments: list[SegmentState] = field(default_factory=list)


# One mutex per project directory, shared by every ProjectManager of this
# process: real project path -> RLock
_project_mutexes: dict[str, threading.RLock] = {}
_project_mutexes_guard = threading.Lock()


def project_mutex(project_dir: str) -> threading.RLock:
    """The in-process mutex of a project. Every in-memory change to a
    project and every save hold it, so a save never sees a half-applied
    change and two managers of one project never write at the same time.
    Re-entrant: a caller holding it may call the manager's methods."""
    key = os.path.realpath(project_dir)
    with _project_mutexes_guard:
        mutex = _project_mutexes.get(key)
        if mutex is None:
            mutex = _project_mutexes[key] = threading.RLock()
        return mutex


class ProjectManager:
    """Manages translation project state with JSON persistence."""

    PROJECT_FILE = 'project.json'

    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self.project_file = os.path.join(project_dir, self.PROJECT_FILE)
        self.data: ProjectData | None = None
        self.mutex = project_mutex(project_dir)

    def create(self, epub_path: str, title: str, author: str,
               segments: list[Segment], source_sha256: str = '',
               segmentation: int | None = None) -> ProjectData:
        """Create a new translation project (schema 2).

        Args:
            epub_path: Path to the project's copy of the source EPUB.
            title: Book title.
            author: Book author.
            segments: List of segments from the segmenter.
            source_sha256: SHA-256 of that copy.
            segmentation: The rules `segments` were cut by (Segmenter.rules),
                written to project.json; None: rules 1, not written (as
                projects created before Phase 6). Anything else raises ValueError.

        A passthrough segment is created translated, its code as its text.
        """
        if segmentation is not None:
            check_rules(segmentation)
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
                block_start=seg.block_start,
                block_end=seg.block_end,
                fingerprints=list(seg.fingerprints),
            ))
            if seg.passthrough:
                state = segment_states[-1]
                state.passthrough = True
                state.status = SegmentStatus.TRANSLATED
                state.translated_text = seg.text

        self.data = ProjectData(
            epub_path=os.path.abspath(epub_path),
            project_dir=self.project_dir,
            title=title,
            author=author,
            created_at=now,
            updated_at=now,
            model=llm.default_backend().default_model,
            schema=SCHEMA,
            source_sha256=source_sha256,
            segmentation=segmentation,
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
        if 'backend' in raw:  # absent: the default backend (None)
            check_backend(raw['backend'])
        if 'model' not in raw:
            raw['model'] = llm.backend_for(raw.get('backend')).default_model
        if 'segmentation' in raw:  # absent: rules 1 (None)
            check_rules(raw['segmentation'])
        if not isinstance(raw.get('brief', ''), str):
            raise ValueError('the brief is not text')
        self.data = ProjectData(**raw, segments=segments)
        return self.data

    def to_json(self) -> str:
        """The text save() writes for the current data (writes nothing)."""
        if not self.data:
            raise RuntimeError("No project data to save.")
        with self.mutex:
            raw = asdict(self.data)
        if raw['schema'] == 1:
            # Leave the new fields out: a schema 1 project saves as before
            del raw['schema'], raw['source_sha256']
            if raw['segmenter_version'] == 1:
                # Leave the field out, so pre-Phase-0 code can still load it
                del raw['segmenter_version']
        else:
            del raw['segmenter_version']  # superseded by the schema
        if raw['segmentation'] is None:
            del raw['segmentation']  # the file had none: saved as before
        if raw['backend'] is None:
            del raw['backend']  # the default backend: saved as before
        if raw['brief'] is None:
            del raw['brief']  # no brief: saved as before
        for seg in raw['segments']:
            for key, default in _SEGMENT_DEFAULTS.items():
                if seg[key] == default:
                    del seg[key]
        return json.dumps(raw, ensure_ascii=False, indent=2)

    def save(self) -> None:
        """Save project to disk atomically via temp file + os.replace()."""
        if not self.data:
            raise RuntimeError("No project data to save.")

        with self.mutex, ProjectLock(self.project_dir):
            self.data.updated_at = time.strftime('%Y-%m-%d %H:%M:%S')
            text = self.to_json()
            # A temporary file of its own for every save, removed on failure
            fd, tmp_file = tempfile.mkstemp(
                dir=self.project_dir, prefix=f'{self.PROJECT_FILE}.', suffix='.tmp')
            try:
                with open(fd, 'w', encoding='utf-8') as f:  # closes fd on any error
                    # mkstemp creates the file 0600: keep the mode project.json had
                    try:
                        mode = os.stat(self.project_file).st_mode & 0o777
                    except FileNotFoundError:
                        mode = 0o644
                    os.fchmod(fd, mode)
                    f.write(text)
                os.replace(tmp_file, self.project_file)
            except BaseException:
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass
                raise

    def set_model(self, model: str, backend: str | None = None) -> None:
        """Set the translation model and, when given, the backend it belongs
        to (in-memory only; call save() to persist). An unknown backend
        raises ValueError."""
        if not self.data:
            raise RuntimeError("No project loaded.")
        if backend is not None:
            check_backend(backend)
        with self.mutex:
            if backend is not None:
                self.data.backend = backend
            self.data.model = model

    def set_brief(self, text: str) -> None:
        """Set the book's brief (in-memory only; call save() to persist).
        Surrounding whitespace is dropped; an empty brief removes it. One
        longer than MAX_BRIEF_CHARS raises ValueError and changes nothing."""
        if not self.data:
            raise RuntimeError("No project loaded.")
        text = text.strip()
        if len(text) > MAX_BRIEF_CHARS:
            raise ValueError(f'書籍簡介最多 {MAX_BRIEF_CHARS} 字（目前 {len(text)} 字）')
        with self.mutex:
            self.data.brief = text or None

    def mark_translated(self, segment_id: str, translated_text: str) -> None:
        """Mark a segment as translated (in-memory only; call save() to persist)."""
        if not self.data:
            raise RuntimeError("No project loaded.")
        with self.mutex:
            seg = self._changeable(segment_id)
            # the text before the status: a reader without the mutex never
            # sees the new status with the old text
            seg.translated_text = translated_text
            seg.status = SegmentStatus.TRANSLATED
            seg.translated_at = time.strftime('%Y-%m-%d %H:%M:%S')

    def apply_outcome(self, segment_id: str, outcome: TranslationOutcome,
                      revision: int | None = None) -> bool:
        """Store a translation outcome (in-memory; call save() to persist).

        Returns False, changing nothing, when `revision` is given and the
        segment has been edited since (its revision differs), and for a
        passthrough segment. A failure
        keeps the text and status of a translated or reviewed segment and
        only attaches the issues; any other segment becomes FAILED.
        """
        with self.mutex:  # the check and the write are one step
            seg = self._find(segment_id)
            if seg.passthrough:  # never a model's result (e.g. the project was re-created)
                return False
            if revision is not None and seg.revision != revision:
                return False
            seg.issues = list(outcome.issues)
            if outcome.status == SegmentStatus.FAILED:
                if seg.status == SegmentStatus.PENDING:
                    seg.status = SegmentStatus.FAILED
                return True
            seg.translated_text = outcome.text  # before the status (mark_translated)
            seg.status = SegmentStatus.TRANSLATED
            seg.translated_at = time.strftime('%Y-%m-%d %H:%M:%S')
            seg.model = outcome.model
            seg.usage = asdict(outcome.usage) if outcome.usage else None
            return True

    def _changeable(self, segment_id: str) -> SegmentState:
        """The segment, for a change of its translation or status; a
        passthrough segment raises PassthroughError (its code stays)."""
        seg = self._find(segment_id)
        if seg.passthrough:
            raise PassthroughError(f'passthrough segment cannot be changed: {segment_id}')
        return seg

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
        # of the translated ones: code without comment lines, done without a model call
        passthrough = sum(1 for s in self.data.segments if s.passthrough)

        return {
            'total': total,
            'translated': translated,
            'reviewed': reviewed,
            'pending': pending,
            'failed': failed,
            'passthrough': passthrough,
            'percent': round(((translated + reviewed) / total * 100) if total > 0 else 0, 1),
        }

    def mark_reviewed(self, segment_id: str, text: str = '') -> None:
        """Mark a segment as reviewed (in-memory only; call save() to persist).
        A manual decision: earlier issues are cleared."""
        with self.mutex:
            seg = self._changeable(segment_id)
            if text:
                seg.translated_text = text
            seg.status = SegmentStatus.REVIEWED
            seg.issues = []
            seg.revision += 1

    def update_translation(self, segment_id: str, text: str,
                           issues: list[Issue] | None = None) -> None:
        """Update the translated text (in-memory; call save() to persist).

        The status is kept, except that a FAILED segment becomes TRANSLATED:
        it now has a translation. Earlier issues are replaced by `issues`
        (the new text's warnings; none by default)."""
        with self.mutex:
            seg = self._changeable(segment_id)
            seg.translated_text = text
            if seg.status == SegmentStatus.FAILED:
                seg.status = SegmentStatus.TRANSLATED
            seg.issues = list(issues or [])
            seg.revision += 1

    def mark_failed(self, segment_id: str, issues: list[Issue]) -> None:
        """A stored translation found broken (in-memory; call save()): the
        segment becomes FAILED with `issues`. Its text is kept: a later
        re-translation replaces it, a failed one leaves it (apply_outcome)."""
        with self.mutex:
            seg = self._changeable(segment_id)
            seg.status = SegmentStatus.FAILED
            seg.issues = list(issues)

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


class PassthroughError(ValueError):
    """A change of a passthrough segment (raised by ProjectManager)."""


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
                    try:
                        holder = os.read(fd, 32).decode(errors='replace').strip()
                    finally:
                        os.close(fd)
                    raise ProjectLockedError(
                        f'專案正由另一個程序使用（PID {holder}）：{self.path}')
                try:
                    os.ftruncate(fd, 0)
                    os.write(fd, str(os.getpid()).encode())
                except BaseException:
                    os.close(fd)  # drops the flock taken above
                    raise
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
