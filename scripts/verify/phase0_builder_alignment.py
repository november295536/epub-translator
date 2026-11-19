#!/usr/bin/env python3
"""Phase 0 check: existing projects keep the builder walk they had on main.

Before Phase 0 the builder always walked a chapter body treating comments
(and other non-text strings) as text, matching the segmenter of that time.
Phase 0 skips comments in the segmenter, so the builder now chooses its walk
per chapter from the stored segments (EPUBBuilder._select_elements). For a
project created before Phase 0 every chapter must still get the old walk,
otherwise translations would land on different elements than on main.

For each chapter that has stored segments, the script compares the element
list chosen now with the element list main would use. A project PASSes when
no chapter differs. A project created after Phase 0 whose source has
comments is expected to FAIL: for it the new walk is the correct one, and
this check is meant for projects created before Phase 0.

Read-only. It opens each project's project.json and its source EPUB, writes
nothing, and never prints book text. Without --anonymize it prints project
directory names and chapter IDs; with --anonymize it prints project-N labels,
where N is the alphabetical position of the directory among the directories
of --projects-dir that contain a project.json, and no IDs or paths.

Usage:
    python3 scripts/verify/phase0_builder_alignment.py [--projects-dir projects]
                                                       [--anonymize]

Exit code: 0 when every project PASSes, 1 when any project FAILs or cannot
be read, 2 when the projects directory cannot be listed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

warnings.filterwarnings('ignore')

import ebooklib  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402
from ebooklib import epub  # noqa: E402

from src.epub.builder import EPUBBuilder  # noqa: E402

PROJECT_FILE = 'project.json'
SOURCE_FILE = 'source.epub'


def _source_path(project_dir: str, data: dict) -> str:
    local = os.path.join(project_dir, SOURCE_FILE)
    if os.path.isfile(local):
        return local
    return data.get('epub_path', '')


def check_project(project_dir: str) -> tuple[int, int, list[str]]:
    """Return (chapters checked, chapters whose walk differs between the two
    rules at all, IDs of chapters whose chosen walk differs from main's)."""
    with open(os.path.join(project_dir, PROJECT_FILE), 'r', encoding='utf-8') as f:
        data = json.load(f)

    by_chapter: dict[str, list] = {}
    for seg in data['segments']:
        by_chapter.setdefault(seg['chapter_id'], []).append(SimpleNamespace(
            segment_type=seg.get('segment_type', 'text'),
            html=seg.get('html', ''),
        ))

    book = epub.read_epub(_source_path(project_dir, data))
    builder = EPUBBuilder.__new__(EPUBBuilder)  # no ProjectManager needed

    checked = 0
    affected = 0
    changed = []
    for item_id, _ in book.spine:
        segments = by_chapter.get(item_id)
        if not segments:
            continue
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        soup = BeautifulSoup(item.get_content().decode('utf-8'), 'html.parser')
        body = soup.find('body')
        if not body:
            continue
        checked += 1

        main_walk = builder._walk_content_elements(body, legacy=True)
        new_walk = builder._walk_content_elements(body, legacy=False)
        chosen, _ = builder._select_elements(body, segments)
        if [id(e) for e in main_walk] != [id(e) for e in new_walk]:
            affected += 1
        if [id(e) for e in chosen] != [id(e) for e in main_walk]:
            changed.append(item_id)
    return checked, affected, changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--projects-dir', default='projects',
                        help='Directory that holds the project folders (default: projects)')
    parser.add_argument('--anonymize', action='store_true',
                        help='Print project-N labels; omit names, chapter IDs and paths')
    args = parser.parse_args(argv)

    shown_dir = '<projects-dir>' if args.anonymize else args.projects_dir
    try:
        entries = os.listdir(args.projects_dir)
    except OSError as e:
        print(f'ERROR cannot list projects directory {shown_dir} ({type(e).__name__})')
        return 2

    names = sorted(name for name in entries
                   if os.path.isfile(os.path.join(args.projects_dir, name, PROJECT_FILE)))

    failed = 0
    total_checked = 0
    total_affected = 0
    total_changed = 0
    for index, name in enumerate(names, 1):
        label = f'project-{index}' if args.anonymize else name
        try:
            checked, affected, changed = check_project(os.path.join(args.projects_dir, name))
        except Exception as e:  # report and continue; never print paths
            failed += 1
            print(f'FAIL {label}: cannot read project or source ({type(e).__name__})')
            continue

        total_checked += checked
        total_affected += affected
        total_changed += len(changed)
        status = 'PASS' if not changed else 'FAIL'
        if changed:
            failed += 1
        line = (f'{status} {label}: chapters {checked}, with comment-dependent walk '
                f'{affected}, walk changed vs main {len(changed)}')
        if changed and not args.anonymize:
            line += ': ' + ' '.join(changed)
        print(line)

    print(f'TOTAL projects {len(names)}  chapters {total_checked}  '
          f'comment-dependent {total_affected}  changed vs main {total_changed}  '
          f'failed projects {failed}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
