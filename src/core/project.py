"""Project manager - handles project state, progress tracking, and persistence."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.segmenter import Segment


@dataclass
class SegmentState:
    """State of a single segment in the project."""
    segment_id: str
    chapter_id: str
    html: str
    text: str
    word_count: int
    segment_type: str
    status: str = 'pending'  # 'pending', 'translated', 'reviewed'
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
    segments: list[SegmentState] = field(default_factory=list)


class ProjectManager:
    """Manages translation project state with JSON persistence."""

    PROJECT_FILE = 'project.json'

    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self.project_file = os.path.join(project_dir, self.PROJECT_FILE)
        self.data: ProjectData | None = None

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
            segments=segment_states,
        )
        self.save()
        return self.data

    def load(self) -> ProjectData:
        """Load project from disk."""
        with open(self.project_file, 'r', encoding='utf-8') as f:
            raw = json.load(f)

        segments = [SegmentState(**s) for s in raw.pop('segments', [])]
        self.data = ProjectData(**raw, segments=segments)
        return self.data

    def save(self) -> None:
        """Save project to disk."""
        if not self.data:
            raise RuntimeError("No project data to save.")

        self.data.updated_at = time.strftime('%Y-%m-%d %H:%M:%S')

        raw = asdict(self.data)
        with open(self.project_file, 'w', encoding='utf-8') as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)

    def mark_translated(self, segment_id: str, translated_text: str) -> None:
        """Mark a segment as translated and save immediately."""
        if not self.data:
            raise RuntimeError("No project loaded.")

        for seg in self.data.segments:
            if seg.segment_id == segment_id:
                seg.status = 'translated'
                seg.translated_text = translated_text
                seg.translated_at = time.strftime('%Y-%m-%d %H:%M:%S')
                self.save()
                return

        raise ValueError(f"Segment not found: {segment_id}")

    def get_pending_segments(self) -> list[SegmentState]:
        """Get all segments that haven't been translated yet."""
        if not self.data:
            raise RuntimeError("No project loaded.")
        return [s for s in self.data.segments if s.status == 'pending']

    def get_progress(self) -> dict:
        """Get translation progress statistics."""
        if not self.data:
            raise RuntimeError("No project loaded.")

        total = len(self.data.segments)
        translated = sum(1 for s in self.data.segments if s.status == 'translated')
        reviewed = sum(1 for s in self.data.segments if s.status == 'reviewed')
        pending = sum(1 for s in self.data.segments if s.status == 'pending')

        return {
            'total': total,
            'translated': translated,
            'reviewed': reviewed,
            'pending': pending,
            'percent': round(((translated + reviewed) / total * 100) if total > 0 else 0, 1),
        }

    def exists(self) -> bool:
        """Check if a project file exists in the project directory."""
        return os.path.isfile(self.project_file)
