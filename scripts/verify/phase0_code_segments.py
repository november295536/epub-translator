#!/usr/bin/env python3
"""Phase 0 check: list translated code segments that may have been corrupted.

Before the Phase 0 fix, the output of a code segment went through markdown
cleanup, which could alter the code itself (Bug 3). This script finds the
candidates for re-translation: already-translated CODE segments whose
translation differs from the source once comment lines are removed.

Read-only. It opens each project's project.json, writes nothing, and never
prints book text; only project names, counts and segment IDs.

Usage:
    python3 scripts/verify/phase0_code_segments.py [--projects-dir projects]
                                                   [--anonymize]

Exit code: 0 when every project could be read (whatever the counts),
1 when at least one project.json could not be read.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

PROJECT_FILE = 'project.json'
COMMENT_MARKERS = ('#', '//')
TRANSLATED_STATUSES = ('translated', 'reviewed')


def code_lines(text: str) -> list[str]:
    """Return the non-comment lines of a code block, normalised for comparison.

    Comment lines (first non-blank characters are # or //) are dropped,
    trailing whitespace is ignored, and blank lines at either end are
    ignored. Leading indentation is kept: losing it is one of the defects
    this check looks for.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    lines = [line for line in lines
             if not line.lstrip().startswith(COMMENT_MARKERS)]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def check_project(project_file: str) -> tuple[int, list[str]]:
    """Return (translated code segment count, IDs of segments that differ)."""
    with open(project_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    translated = 0
    differing = []
    for seg in data['segments']:
        if seg.get('segment_type') != 'code':
            continue
        if seg.get('status') not in TRANSLATED_STATUSES:
            continue
        if not seg.get('translated_text'):
            continue
        translated += 1
        if code_lines(seg.get('text', '')) != code_lines(seg['translated_text']):
            differing.append(seg['segment_id'])
    return translated, differing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--projects-dir', default='projects',
                        help='Directory that holds the project folders (default: projects)')
    parser.add_argument('--anonymize', action='store_true',
                        help='Print project-N labels instead of names and omit segment IDs')
    args = parser.parse_args(argv)

    if not os.path.isdir(args.projects_dir):
        print(f'ERROR projects directory not found: {args.projects_dir}')
        return 1

    names = sorted(
        name for name in os.listdir(args.projects_dir)
        if os.path.isfile(os.path.join(args.projects_dir, name, PROJECT_FILE))
    )

    total_translated = 0
    total_differing = 0
    unreadable = 0

    for index, name in enumerate(names, 1):
        label = f'project-{index}' if args.anonymize else name
        try:
            translated, differing = check_project(
                os.path.join(args.projects_dir, name, PROJECT_FILE))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            unreadable += 1
            print(f'ERROR {label}: cannot read project ({type(e).__name__})')
            continue

        total_translated += translated
        total_differing += len(differing)
        line = (f'{label}: translated code segments {translated}, '
                f'differ outside comment lines {len(differing)}')
        if differing and not args.anonymize:
            line += ': ' + ' '.join(differing)
        print(line)

    print(f'TOTAL projects {len(names)}  translated code segments {total_translated}  '
          f'differ outside comment lines {total_differing}  unreadable {unreadable}')
    return 1 if unreadable else 0


if __name__ == '__main__':
    sys.exit(main())
