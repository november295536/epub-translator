"""One-time migration of a schema 1 project to schema 2 (plan 4.2).

For each chapter, the text of the legacy segments (taken from each segment's
stored `html`) must equal the text of the chapter's blocks, ignoring
whitespace; the comparison is tried without, then with, the comments that
are direct children of walked elements (segmenters before Phase 0 stored
them as text). Legacy segments are then grouped: the smallest groups that
start and end on block boundaries. A group of one segment keeps it; a larger
group becomes one segment (translations joined in order, first ID kept,
status the lowest of the group). A larger group that is not wholly
translated (lowest status pending or failed) keeps no translation, and a
group that mixes code and text segments makes its chapter unmatched.

Fail-closed: if any chapter cannot be matched nothing is written, and an
existing `project.schema1.json` is never overwritten. On success
the previous project.json is kept next to it as `project.schema1.json`, the
project gets its own copy of the source EPUB when it has none, and the new
project.json is written atomically. Never run from load().
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, NavigableString, Tag
from lxml import etree

from src.core.project import (
    ProjectLock, ProjectManager, SegmentState, SegmentStatus, sha256_file,
)
from src.core.segmenter import SegmentType, segment_from_blocks
from src.epub.blocks import normalize, walk_blocks
from src.epub.extractor import (
    XHTML_MEDIA_TYPE, EPUBError, find_body, open_archive, parse_xml, read_member,
    read_package, unresolved_entities,
)

SOURCE_FILE = 'source.epub'
BACKUP_FILE = 'project.schema1.json'

# Lowest first: a merged segment takes the lowest status of its group
_STATUS_ORDER = [SegmentStatus.PENDING, SegmentStatus.FAILED,
                 SegmentStatus.TRANSLATED, SegmentStatus.REVIEWED]


class MigrationError(Exception):
    """The project cannot be migrated (nothing was written)."""


@dataclass
class MigrationReport:
    chapters: int
    segments: int
    chapters_matched: int = 0
    chapters_with_comment_text: int = 0
    segments_matched: int = 0
    merged: list = field(default_factory=list)       # [legacy segment ids] per merge group
    notes: list = field(default_factory=list)        # groups of mixed status or cleared
    unmatched: list = field(default_factory=list)    # (chapter id, reason)
    count_differs: int = 0
    written: bool = False

    def lines(self) -> list[str]:
        out = [
            f'章節對應：{self.chapters_matched}/{self.chapters}'
            f'（其中 {self.chapters_with_comment_text} 章含註解文字）',
            f'段落對應：{self.segments_matched}/{self.segments}',
            f'合併群組：{len(self.merged)}（{sum(len(g) for g in self.merged)} 個段落）',
        ]
        out += [f'  合併 {" + ".join(ids)}' for ids in self.merged]
        out += [f'  注意 {note}' for note in self.notes]
        out += [f'無法對應的章節 {cid}：{why}' for cid, why in self.unmatched]
        out.append(f'已翻譯段落的頂層元素數與新區塊 HTML 不同：{self.count_differs}'
                   '（僅供參考，不阻擋）')
        out.append('已寫入 project.json（舊檔保留為 project.schema1.json）'
                   if self.written else '未寫入任何檔案')
        return out


def _top_level_count(html: str) -> int:
    frag = BeautifulSoup(html, 'html.parser')
    return sum(1 for c in frag.children
               if isinstance(c, Tag) or (isinstance(c, NavigableString) and c.strip()))


def _groups(seg_texts: list[str], unit_texts: list[str]) -> list:
    """(segment indexes, first unit, end unit) for the smallest groups of
    segments that start and end on unit boundaries. The concatenations of
    both lists are equal."""
    unit_end = {}
    offset = 0
    for i, text in enumerate(unit_texts):
        offset += len(text)
        unit_end[offset] = i + 1
    groups = []
    current: list[int] = []
    offset = start = done = 0
    for i, text in enumerate(seg_texts):
        current.append(i)
        offset += len(text)
        if offset > done and offset in unit_end:
            groups.append((current, start, unit_end[offset]))
            start, done, current = unit_end[offset], offset, []
    if current and groups:   # trailing segments without text
        groups[-1][0].extend(current)
    return groups


def _merge(olds: list[SegmentState], blocks: list) -> SegmentState:
    first = olds[0]
    seg = segment_from_blocks(first.segment_id, blocks, SegmentType(first.segment_type),
                              standalone=False)
    return SegmentState(
        segment_id=first.segment_id,
        chapter_id=first.chapter_id,
        html=seg.html,
        text=seg.text,
        word_count=seg.word_count,
        segment_type=first.segment_type,
        status=min((s.status for s in olds), key=_STATUS_ORDER.index),
        translated_text=''.join(s.translated_text for s in olds),
        translated_at=max(s.translated_at for s in olds),
        issues=[i for s in olds for i in s.issues],
        revision=max(s.revision for s in olds),
        model=next((s.model for s in olds if s.model), ''),
        usage=first.usage if len(olds) == 1 else None,
        block_start=seg.block_start,
        block_end=seg.block_end,
        fingerprints=seg.fingerprints,
    )


def _match_chapter(chapter_id: str, segs: list[SegmentState], body, report: MigrationReport):
    """New segments for one chapter, or None (recorded in the report)."""
    seg_texts = [normalize(BeautifulSoup(s.html, 'html.parser').get_text()) for s in segs]
    for with_comments in (False, True):
        units = walk_blocks(body, with_comment_runs=with_comments)
        unit_texts = [u.legacy_text if with_comments else normalize(u.text) for u in units]
        if ''.join(unit_texts) == ''.join(seg_texts):
            break
    else:
        report.unmatched.append((chapter_id, 'text differs'))
        return None
    report.chapters_with_comment_text += with_comments

    new = []
    for indexes, start, end in _groups(seg_texts, unit_texts):
        olds = [segs[i] for i in indexes]
        blocks = [u for u in units[start:end] if u.index is not None]
        if not blocks:
            report.unmatched.append((chapter_id, f'{olds[0].segment_id} holds only comment text'))
            return None
        merged = _merge(olds, blocks)
        if len(olds) > 1:
            if len({s.segment_type for s in olds}) > 1:
                report.unmatched.append(
                    (chapter_id, f'{olds[0].segment_id} merges code and text segments'))
                return None
            report.merged.append([s.segment_id for s in olds])
            # A group not wholly translated keeps no partial translation:
            # export would place it as the whole group's text
            cleared = (merged.status in (SegmentStatus.PENDING, SegmentStatus.FAILED)
                       and merged.translated_text != '')
            if cleared:
                merged.translated_text = ''
            if cleared or len({s.status for s in olds}) > 1:
                report.notes.append(
                    f'{merged.segment_id}：{len(olds)} 個段落的狀態為 '
                    f'{", ".join(str(s.status) for s in olds)}；合併後為 {merged.status}，'
                    + ('部分譯文已清除（保留於 project.schema1.json）' if cleared
                       else '譯文依序接合保留'))
        new.append(merged)
    if not new:
        report.unmatched.append((chapter_id, 'no text'))
        return None
    return new


def plan_migration(pm: ProjectManager, source: str):
    """(new segment list, report) for a loaded schema 1 project; reads only.
    Raises EPUBError when the source cannot be read."""
    zf = open_archive(source)
    _, _, spine = read_package(zf)
    paths = {item.id: item.path for item in spine if item.media_type == XHTML_MEDIA_TYPE}

    by_chapter: dict[str, list[SegmentState]] = {}
    for seg in pm.data.segments:
        by_chapter.setdefault(seg.chapter_id, []).append(seg)
    report = MigrationReport(chapters=len(by_chapter), segments=len(pm.data.segments))

    new_segments = []
    for chapter_id, segs in by_chapter.items():
        if chapter_id not in paths:
            report.unmatched.append((chapter_id, 'not in the spine'))
            continue
        try:
            body = find_body(parse_xml(read_member(zf, paths[chapter_id])))
        except (KeyError, etree.XMLSyntaxError):
            report.unmatched.append((chapter_id, 'missing or not well-formed XML'))
            continue
        if body is None:
            report.unmatched.append((chapter_id, 'no body'))
            continue
        if unresolved_entities(body):
            report.unmatched.append((chapter_id, 'unresolved entity reference'))
            continue
        new = _match_chapter(chapter_id, segs, body, report)
        if new is None:
            continue
        report.chapters_matched += 1
        report.segments_matched += len(segs)
        report.count_differs += sum(
            1 for s in new
            if s.translated_text and s.segment_type == SegmentType.TEXT
            and _top_level_count(s.translated_text) != _top_level_count(s.html))
        new_segments.extend(new)
    return new_segments, report


def migrate_project(project_dir: str, dry_run: bool = False) -> MigrationReport:
    """Migrate one project. Raises MigrationError (or ProjectLockedError) when
    it cannot start; returns the report otherwise. Writes nothing on a dry
    run or when a chapter is unmatched."""
    pm = ProjectManager(project_dir)
    if not pm.exists():  # before the lock: no lock file in a non-project
        raise MigrationError(f'不是專案目錄（沒有 project.json）：{project_dir}')
    lock = ProjectLock(project_dir)
    if not dry_run:
        lock.acquire()  # refuses while another process (a web task) holds it
    try:
        try:
            pm.load()
        except (OSError, ValueError, TypeError, KeyError) as e:
            raise MigrationError(f'無法讀取 project.json：{e}') from e
        if pm.data.schema != 1:
            raise MigrationError(f'專案已是 schema {pm.data.schema}')
        backup = os.path.join(project_dir, BACKUP_FILE)
        if os.path.exists(backup):
            raise MigrationError(f'{BACKUP_FILE} 已存在，不會覆寫；請先移走它')
        own = os.path.join(project_dir, SOURCE_FILE)
        source = own if os.path.isfile(own) else pm.data.epub_path
        if not os.path.isfile(source):
            raise MigrationError(f'找不到來源 EPUB：{source}')

        try:
            new_segments, report = plan_migration(pm, source)
        except EPUBError as e:
            raise MigrationError(f'來源 EPUB 無法讀取：{e}') from e
        if dry_run or report.unmatched:
            return report

        if source != own:
            # A partial copy never takes the name a later run would prefer
            tmp = f'{own}.{uuid.uuid4().hex}.tmp'
            try:
                shutil.copy2(source, tmp)
                os.replace(tmp, own)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        shutil.copy2(pm.project_file, backup)
        with pm.mutex:
            pm.data.segments = new_segments
            pm.data.schema = 2
            pm.data.epub_path = os.path.abspath(own)
            pm.data.source_sha256 = sha256_file(own)
        pm.save()
        report.written = True
        return report
    finally:
        lock.release()
