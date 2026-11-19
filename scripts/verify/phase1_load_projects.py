#!/usr/bin/env python3
"""Phase 1 check: every existing project loads with the new code, untouched.

For each directory of --projects-dir that contains a project.json, loads the
project with ProjectManager.load() (the code the web UI and the CLI use) and
prints the segment counts by status. A project FAILs when loading raises, or
when project.json's bytes or modification time changed, or when a file
appeared in or vanished from the project directory during the load (loading
must never write, and must not create the project lock file).

Read-only: it only loads; it never saves, translates or takes the project
lock. Without --anonymize it prints project directory names. With
--anonymize it prints project-N labels, where N is the alphabetical position
of the directory among the directories of --projects-dir that contain a
project.json, and no names or paths (also not in error messages).

Usage:
    python3 scripts/verify/phase1_load_projects.py [--projects-dir projects]
                                                   [--anonymize]

Exit code: 0 when every project PASSes, 1 when any FAILs, 2 when the projects
directory cannot be listed.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections import Counter

# At index 0, ahead of everything (see phase0_builder_alignment.py)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from src.core.project import ProjectManager, SegmentStatus  # noqa: E402

PROJECT_FILE = 'project.json'


def _snapshot(project_dir: str) -> tuple:
    path = os.path.join(project_dir, PROJECT_FILE)
    with open(path, 'rb') as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return digest, os.stat(path).st_mtime_ns, sorted(os.listdir(project_dir))


def check_project(project_dir: str) -> tuple[Counter, list[str]]:
    """Load one project. Returns (segment count per status, problems)."""
    before = _snapshot(project_dir)
    pm = ProjectManager(project_dir)
    pm.load()
    counts = Counter(str(seg.status) for seg in pm.data.segments)
    digest, mtime, listing = _snapshot(project_dir)
    problems = []
    if digest != before[0]:
        problems.append('project.json content changed')
    if mtime != before[1]:
        problems.append('project.json modification time changed')
    if listing != before[2]:
        problems.append('files in the project directory changed')
    return counts, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--projects-dir', default='projects',
                        help='Directory that holds the project folders (default: projects)')
    parser.add_argument('--anonymize', action='store_true',
                        help='Print project-N labels; omit names and paths')
    args = parser.parse_args(argv)

    shown_dir = '<projects-dir>' if args.anonymize else args.projects_dir
    try:
        entries = os.listdir(args.projects_dir)
    except OSError as e:
        print(f'ERROR cannot list projects directory {shown_dir} ({type(e).__name__})')
        return 2

    names = sorted(name for name in entries
                   if os.path.isfile(os.path.join(args.projects_dir, name, PROJECT_FILE)))
    statuses = [s.value for s in SegmentStatus]
    totals = Counter()
    failed = 0

    for index, name in enumerate(names, 1):
        label = f'project-{index}' if args.anonymize else name
        try:
            counts, problems = check_project(os.path.join(args.projects_dir, name))
        except Exception as e:  # any load failure is a FAIL, never a crash
            failed += 1
            print(f'FAIL {label}: cannot load ({type(e).__name__})')
            continue
        totals.update(counts)
        line = (f'{label}: segments {sum(counts.values())}, '
                + ', '.join(f'{s} {counts[s]}' for s in statuses))
        if problems:
            failed += 1
            print(f'FAIL {line}; ' + '; '.join(problems))
        else:
            print(f'PASS {line}')

    print(f'TOTAL projects {len(names)}  segments {sum(totals.values())}  '
          + '  '.join(f'{s} {totals[s]}' for s in statuses)
          + f'  failed projects {failed}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
