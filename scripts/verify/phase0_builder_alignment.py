#!/usr/bin/env python3
"""Phase 0 check: existing projects resolve to the builder's legacy walk.

Before Phase 0 the builder always walked a chapter body treating comments
(and other non-text strings) as text, matching the segmenter of that time.
Phase 0 skips comments in the segmenter and records this on new projects as
segmenter_version 2; the builder walks chapters of a project without the
field (segmenter_version 1) as main did (EPUBBuilder._legacy_walk).

For each chapter that has stored segments, the script compares the element
list the builder chooses for the project with the element list of the
branch's legacy walk. A project PASSes when no chapter differs, which shows
that the project resolves to the legacy walk. It does not compare with
main's code: that the legacy walk gives main's output rests on the golden
tests (tests/test_builder.py::TestVersion1ExportMatchesMain). A project
created after Phase 0 (segmenter_version 2) whose source has comments is
expected to FAIL: for it the new walk is the correct one, and this check is
meant for projects created before Phase 0.

Read-only. It loads each project's project.json and its source EPUB, writes
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
import os
import sys
import warnings

# At index 0, ahead of everything; with default interpreter path settings
# sys.path[0] is this script's directory (no src package), so index 1 would
# import the same src.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

warnings.filterwarnings('ignore')

import ebooklib  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402
from ebooklib import epub  # noqa: E402

from src.epub.builder import EPUBBuilder  # noqa: E402

PROJECT_FILE = 'project.json'
SOURCE_FILE = 'source.epub'


def _source_path(project_dir: str, epub_path: str) -> str:
    local = os.path.join(project_dir, SOURCE_FILE)
    if os.path.isfile(local):
        return local
    return epub_path


def check_project(project_dir: str) -> tuple[int, int, int, list[str]]:
    """Return (segmenter_version, chapters checked, chapters whose walk
    differs between the two rules at all, IDs of chapters whose chosen walk
    differs from the legacy walk)."""
    builder = EPUBBuilder(project_dir)
    data = builder.pm.load()  # reads project.json; never saves
    legacy = builder._legacy_walk()
    by_chapter = builder._group_segments_by_chapter()
    book = epub.read_epub(_source_path(project_dir, data.epub_path))

    checked = 0
    affected = 0
    changed = []
    for item_id, _ in book.spine:
        if item_id not in by_chapter:
            continue
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        soup = BeautifulSoup(item.get_content().decode('utf-8'), 'html.parser')
        body = soup.find('body')
        if not body:
            continue
        checked += 1

        legacy_walk = builder._walk_content_elements(body, legacy=True)
        new_walk = builder._walk_content_elements(body, legacy=False)
        chosen = legacy_walk if legacy else new_walk
        if [id(e) for e in legacy_walk] != [id(e) for e in new_walk]:
            affected += 1
        if [id(e) for e in chosen] != [id(e) for e in legacy_walk]:
            changed.append(item_id)
    return data.segmenter_version, checked, affected, changed


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
            version, checked, affected, changed = check_project(
                os.path.join(args.projects_dir, name))
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
        line = (f'{status} {label}: segmenter_version {version}, chapters {checked}, '
                f'with comment-dependent walk '
                f'{affected}, not on legacy walk {len(changed)}')
        if changed and not args.anonymize:
            line += ': ' + ' '.join(changed)
        print(line)

    print(f'TOTAL projects {len(names)}  chapters {total_checked}  '
          f'comment-dependent {total_affected}  not on legacy walk {total_changed}  '
          f'failed projects {failed}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
