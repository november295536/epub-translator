#!/usr/bin/env python3
"""Phase 0 check: list translated code segments that may have been corrupted.

Before the Phase 0 fix, the output of a code segment went through markdown
cleanup, which could alter the code itself (Bug 3). This script lists the
candidates for re-translation, per project:

- already-translated CODE segments whose translation differs from the
  source outside the wording of comment lines;
- CODE segments marked translated or reviewed whose translation is empty.

It is a reporting script, not a pass/fail gate: the candidates still need a
human look (a translated end-of-line comment also counts as a difference).

A line is a comment line when its first non-blank characters are `//`, or
`#` followed by whitespace, `!` or the end of the line. So `#define`,
`#include` and `#[derive]` are code and are compared. Only a comment line's
wording is ignored; its leading whitespace is compared.

Read-only. It opens each project's project.json, writes nothing, and never
prints book text. Without --anonymize it prints project directory names and
segment IDs. With --anonymize it prints project-N labels, where N is the
alphabetical position of the directory among the directories of
--projects-dir that contain a project.json, and no names, IDs or paths
(also not in error messages).

Usage:
    python3 scripts/verify/phase0_code_segments.py [--projects-dir projects]
                                                   [--anonymize]

Exit code: 0 when every project could be read (whatever the counts),
1 when at least one project.json could not be read, 2 when the projects
directory cannot be listed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

PROJECT_FILE = 'project.json'
TRANSLATED_STATUSES = ('translated', 'reviewed')
COMMENT_LINE = re.compile(r'^(\s*)(?://|#(?:\s|!|$)).*')

HEADER = ('Phase 0 code segment report (candidates for re-translation; '
          'reporting only, not a pass/fail check)')


def code_lines(text: str) -> list[str]:
    """Return the lines of a code block, normalised for comparison.

    A comment line (see COMMENT_LINE) becomes its leading whitespace plus
    '#', so its wording is ignored but lost indentation is not. Trailing
    whitespace and blank lines at either end are ignored. Leading
    indentation is kept: losing it is one of the defects this check looks
    for.
    """
    lines = [COMMENT_LINE.sub(r'\1#', line.rstrip()) for line in text.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def check_project(project_file: str) -> tuple[int, list[str], list[str]]:
    """Return (translated code segment count, IDs that differ, IDs with an
    empty translation) for one project.json."""
    with open(project_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    translated = 0
    differing = []
    empty = []
    for seg in data['segments']:
        if seg.get('segment_type') != 'code':
            continue
        if seg.get('status') not in TRANSLATED_STATUSES:
            continue
        translated += 1
        source = seg['text']  # a missing or non-string source makes the project unreadable
        if not isinstance(source, str):
            raise TypeError  # only the exception type is printed
        output = seg.get('translated_text') or ''
        if not output.strip():
            empty.append(seg['segment_id'])
        elif code_lines(source) != code_lines(output):
            differing.append(seg['segment_id'])
    return translated, differing, empty


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--projects-dir', default='projects',
                        help='Directory that holds the project folders (default: projects)')
    parser.add_argument('--anonymize', action='store_true',
                        help='Print project-N labels; omit names, segment IDs and paths')
    args = parser.parse_args(argv)

    print(HEADER)
    shown_dir = '<projects-dir>' if args.anonymize else args.projects_dir
    try:
        entries = os.listdir(args.projects_dir)
    except OSError as e:
        print(f'ERROR cannot list projects directory {shown_dir} ({type(e).__name__})')
        return 2

    names = sorted(name for name in entries
                   if os.path.isfile(os.path.join(args.projects_dir, name, PROJECT_FILE)))

    total_translated = 0
    total_differing = 0
    total_empty = 0
    unreadable = 0

    for index, name in enumerate(names, 1):
        label = f'project-{index}' if args.anonymize else name
        try:
            translated, differing, empty = check_project(
                os.path.join(args.projects_dir, name, PROJECT_FILE))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            unreadable += 1
            print(f'ERROR {label}: cannot read project ({type(e).__name__})')
            continue

        total_translated += translated
        total_differing += len(differing)
        total_empty += len(empty)
        line = (f'{label}: translated code segments {translated}, '
                f'differ outside comment lines {len(differing)}, '
                f'empty translation {len(empty)}')
        if not args.anonymize:
            if differing:
                line += '; differ: ' + ' '.join(differing)
            if empty:
                line += '; empty: ' + ' '.join(empty)
        print(line)

    print(f'TOTAL projects {len(names)}  translated code segments {total_translated}  '
          f'differ outside comment lines {total_differing}  '
          f'empty translation {total_empty}  unreadable {unreadable}')
    return 1 if unreadable else 0


if __name__ == '__main__':
    sys.exit(main())
