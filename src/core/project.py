"""Project manager - handles project state, progress tracking, and persistence."""

from __future__ import annotations

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


class SegmentStatus(str, Enum):
    """Segment translation status."""
    PENDING = 'pending'
    TRANSLATED = 'translated'
    REVIEWED = 'reviewed'

    def __str__(self):
        return self.value


# Segmenter behaviour recorded on projects created now (see ProjectData)
SEGMENTER_VERSION = 2


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
            tmp_file = self.project_file + '.tmp'
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

    def get_pending_segments(self) -> list[SegmentState]:
        """Get all segments that haven't been translated yet."""
        if not self.data:
            raise RuntimeError("No project loaded.")
        return [s for s in self.data.segments if s.status == SegmentStatus.PENDING]

    def get_progress(self) -> dict:
        """Get translation progress statistics."""
        if not self.data:
            raise RuntimeError("No project loaded.")

        total = len(self.data.segments)
        translated = sum(1 for s in self.data.segments if s.status == SegmentStatus.TRANSLATED)
        reviewed = sum(1 for s in self.data.segments if s.status == SegmentStatus.REVIEWED)
        pending = sum(1 for s in self.data.segments if s.status == SegmentStatus.PENDING)

        return {
            'total': total,
            'translated': translated,
            'reviewed': reviewed,
            'pending': pending,
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
                return
        raise ValueError(f"Segment not found: {segment_id}")

    def update_translation(self, segment_id: str, text: str) -> None:
        """Update the translated text without changing status (in-memory; call save() to persist)."""
        if not self.data:
            raise RuntimeError("No project loaded.")
        for seg in self.data.segments:
            if seg.segment_id == segment_id:
                seg.translated_text = text
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
                chapters[cid] = {'chapter_id': cid, 'total': 0, 'translated': 0, 'reviewed': 0}
            chapters[cid]['total'] += 1
            if seg.status == SegmentStatus.TRANSLATED:
                chapters[cid]['translated'] += 1
            elif seg.status == SegmentStatus.REVIEWED:
                chapters[cid]['reviewed'] += 1
        return list(chapters.values())

    def exists(self) -> bool:
        """Check if a project file exists in the project directory."""
        return os.path.isfile(self.project_file)
