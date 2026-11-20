#!/usr/bin/env python3
"""Phase 6 check: segmentation rules 1 (before Phase 6) and 2 (new projects) on the real books.

For every project of --projects-dir, copies its source EPUB into a new
temporary directory outside every git work tree and outside --projects-dir,
and segments every chapter of the copy twice, with the rules of projects
created before Phase 6 (Segmenter(rules=1)) and with the rules of new
projects (Segmenter(rules=2)). Nothing is written to the project; the
project's stored segments are not read. The source is the project's own
`source.epub`; a project without one is read from the path its project.json
stores (schema 1 projects), which may lie outside the project directory and
is then not covered by the snapshot below.

Per project it prints: segments under both rules; model calls needed under
both rules (segments that are not passthrough); passthrough code segments;
headings merged into a text segment, of all heading blocks; the ratio of
new to old calls; and whether both segmentations cover the chapter's blocks
exactly: every segment's range starts where the previous one ended, the
last one ends at the last block, and the fingerprints of all segments, in
order, are the fingerprints of the blocks.

A project PASSes when every chapter of its spine could be read and
segmented (the extractor's failures: missing, unreadable or not well-formed
chapters, chapters that are not XHTML, unresolved entities; a book with no
chapter at all also FAILs), both segmentations of every chapter cover its
blocks exactly (contiguous ranges from the first block to the last, each
segment's fingerprints equal to those of its own blocks), and the new rules
need no more calls than the old ones. The plan's acceptance
(project-3: fewer than half the calls) is read from the printed ratio.

Only counts are printed, never book text. Everything in the temporary
directory is removed at the end. The projects directory is only read: a
project FAILs if any file in it changed during the run. With --anonymize it
prints project-N labels (alphabetical position among the directories of
--projects-dir that contain a project.json) and no names or paths.

Usage:
    python3 scripts/verify/phase6_segmentation_check.py [--projects-dir projects] [--anonymize]

Exit code: 0 when every project PASSes, 1 when any FAILs or the check stops
on an error, 2 when the projects directory cannot be listed or the
temporary directory would be inside a git work tree or the projects
directory (checked before it is created).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
# At index 0, ahead of everything (see phase2_roundtrip.py)
sys.path.insert(0, ROOT)

from src.core.segmenter import HEADING_TAGS, Segmenter  # noqa: E402
from src.epub.blocks import walk_blocks  # noqa: E402
from src.epub.extractor import EPUBExtractor  # noqa: E402

PROJECT_FILE = 'project.json'
SOURCE_FILE = 'source.epub'
COUNTS = ('old_segments', 'new_segments', 'old_calls', 'new_calls', 'passthrough',
          'merged', 'headings')


def snapshot(project_dir: str) -> list:
    """Name, size and modification time of every file under the directory."""
    out = []
    for root, dirs, files in os.walk(project_dir):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            st = os.stat(path)
            out.append((os.path.relpath(path, project_dir), st.st_size, st.st_mtime_ns))
    return out


def source_of(project_dir: str) -> str:
    """The project's source EPUB: its own copy, else the stored path."""
    own = os.path.join(project_dir, SOURCE_FILE)
    if os.path.isfile(own):
        return own
    with open(os.path.join(project_dir, PROJECT_FILE), encoding='utf-8') as f:
        return json.load(f)['epub_path']


def covers(blocks: list, segments: list) -> bool:
    """The segments' ranges follow each other from the first block to the
    last, and each segment's fingerprints are those of its own blocks."""
    position = 0
    for seg in segments:
        if seg.block_start != position or seg.block_end <= seg.block_start:
            return False
        if seg.fingerprints != [b.fingerprint for b in blocks[seg.block_start:seg.block_end]]:
            return False
        position = seg.block_end
    return position == len(blocks)


def check_source(epub: str) -> tuple[dict, list[str]]:
    """The counts of one EPUB, and its problems."""
    extractor = EPUBExtractor(epub)
    extractor.load()
    r = dict.fromkeys(COUNTS, 0)
    uncovered = 0
    chapters = extractor.extract_chapters()
    for chapter in chapters:
        blocks = walk_blocks(chapter.body)
        old = Segmenter(rules=1).segment_chapter(chapter.id, chapter.body)
        new = Segmenter(rules=2).segment_chapter(chapter.id, chapter.body)
        uncovered += not covers(blocks, old)
        uncovered += not covers(blocks, new)
        r['old_segments'] += len(old)
        r['new_segments'] += len(new)
        r['old_calls'] += sum(not s.passthrough for s in old)
        r['new_calls'] += sum(not s.passthrough for s in new)
        r['passthrough'] += sum(s.passthrough for s in new)
        for seg in new:
            tags = [b.tag for b in blocks[seg.block_start:seg.block_end]]
            headings = sum(t in HEADING_TAGS for t in tags)
            r['headings'] += headings
            if headings < len(tags):
                r['merged'] += headings
    problems = []
    if extractor.failed:
        problems.append(f'{len(extractor.failed)} chapters could not be checked')
    if not chapters:
        problems.append('no chapter checked')
    if uncovered:
        problems.append(f'{uncovered} segmentations do not cover their chapter exactly')
    if r['new_calls'] > r['old_calls']:
        problems.append('the new rules need more calls')
    return r, problems


def counts_line(r: dict) -> str:
    ratio = f"{r['new_calls'] / r['old_calls']:.3f}" if r['old_calls'] else '-'
    return (f"segments {r['old_segments']} -> {r['new_segments']}, "
            f"calls {r['old_calls']} -> {r['new_calls']} (ratio {ratio}), "
            f"passthrough {r['passthrough']}, "
            f"headings merged {r['merged']} of {r['headings']}")


def in_work_tree(path: str) -> bool:
    """Whether the resolved `path` lies in a git work tree: it or an
    ancestor holds `.git` (a directory in a main checkout, a file in a
    linked worktree)."""
    current = os.path.realpath(path)
    while True:
        if os.path.lexists(os.path.join(current, '.git')):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def inside(path: str, place: str) -> bool:
    path, place = os.path.realpath(path), os.path.realpath(place)
    return os.path.commonpath([path, place]) == place


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--projects-dir', default='projects',
                        help='Directory that holds the project folders (default: projects)')
    parser.add_argument('--anonymize', action='store_true',
                        help='Print project-N labels; omit names and paths')
    args = parser.parse_args(argv)
    try:
        return check(args)
    except Exception as e:  # noqa: BLE001 - no path or text may reach the output
        print(f'ERROR the check stopped ({type(e).__name__})')
        return 1


def check(args) -> int:
    shown_dir = '<projects-dir>' if args.anonymize else args.projects_dir
    try:
        entries = os.listdir(args.projects_dir)
    except OSError as e:
        print(f'ERROR cannot list projects directory {shown_dir} ({type(e).__name__})')
        return 2
    names = sorted(name for name in entries
                   if os.path.isfile(os.path.join(args.projects_dir, name, PROJECT_FILE)))

    parent = os.path.realpath(tempfile.gettempdir())
    if in_work_tree(parent):
        print('ERROR the temporary directory would be inside a git work tree')
        return 2
    if inside(parent, args.projects_dir):
        print('ERROR the temporary directory would be inside the projects directory')
        return 2
    work = tempfile.mkdtemp(prefix='phase6-segmentation-', dir=parent)
    try:
        code = run(args, names, work)
    finally:
        try:
            shutil.rmtree(work)
        except OSError as e:
            print(f'ERROR cannot remove the temporary directory ({type(e).__name__})')
            code = 1
    return code


def run(args, names: list[str], work: str) -> int:
    totals = dict.fromkeys(COUNTS, 0)
    failed = 0
    for index, name in enumerate(names, 1):
        label = f'project-{index}' if args.anonymize else name
        project_dir = os.path.join(args.projects_dir, name)
        # numbered copies: no project name reaches a path
        copy = os.path.join(work, f'{index}.epub')
        before = None
        try:
            before = snapshot(project_dir)
            shutil.copyfile(source_of(project_dir), copy)
            r, problems = check_source(copy)
        except Exception as e:  # any failure is a FAIL, never a crash
            r, problems = {}, [f'check raised {type(e).__name__}']
        finally:
            try:
                if before is not None and snapshot(project_dir) != before:
                    problems.append('files in the project directory changed')
            except Exception as e:  # reported like any failure, by type only
                r, problems = {}, problems + [f'project snapshot raised {type(e).__name__}']
        if not r:
            failed += 1
            print(f'FAIL {label}: ' + '; '.join(problems))
            continue
        for key in COUNTS:
            totals[key] += r[key]
        line = f'{label}: {counts_line(r)}, blocks covered identically'
        if problems:
            failed += 1
            print(f'FAIL {label}: {counts_line(r)}; ' + '; '.join(problems))
        else:
            print(f'PASS {line}')
    print(f'TOTAL projects {len(names)}  {counts_line(totals)}  failed projects {failed}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
