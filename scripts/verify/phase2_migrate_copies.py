#!/usr/bin/env python3
"""Phase 2 check: the migration succeeds on copies of the existing projects.

Copies every project of --projects-dir (its project.json, source.epub and
glossary.json) into a new temporary directory outside the repository and
outside --projects-dir, migrates each copy with the migration command's code (src/core/migrate.py),
and checks per project:

- every chapter and every segment matched, and the copy was written;
- the total number of characters of translated text is unchanged;
- the exporter's chapter check (src/epub/exporter.py: the chapter's blocks
  match the stored ranges and fingerprints) accepts every chapter that has
  segments (since Phase 3; the Phase 2 export bridge and its "export
  fallback" and "cross-parent" counts are gone with the old builder);
- the migrated project loads through the web app (Flask test client on the
  copies: home page, project JSON with the migrated segment count, review
  page).

It prints one line per project and a TOTAL line with the chapters and
segments matched, the merge groups and the non-blocking count of translated
segments whose top-level element count differs from the new block HTML.
The copies are removed at the end. The projects directory is only read: a
project FAILs if any file in it changed during the run.

Without --anonymize it prints project directory names. With --anonymize it
prints project-N labels (alphabetical position among the directories of
--projects-dir that contain a project.json) and no names or paths.

Usage:
    python3 scripts/verify/phase2_migrate_copies.py [--projects-dir projects] [--anonymize]

Exit code: 0 when every project PASSes, 1 when any FAILs, 2 when the projects
directory cannot be listed or the temporary directory would be inside the
repository or the projects directory (checked before it is created).
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

from src.core.migrate import migrate_project  # noqa: E402
from src.core.project import ProjectManager  # noqa: E402
from src.epub.exporter import _matches, chapter_items, load_chapter  # noqa: E402
from src.epub.extractor import open_archive, read_package  # noqa: E402
from src.web.server import create_app  # noqa: E402

PROJECT_FILE = 'project.json'
COPIED = (PROJECT_FILE, 'source.epub', 'glossary.json')


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


def read_segments(project_file: str) -> tuple[int, int]:
    """(number of segments, characters of translated text)."""
    with open(project_file, encoding='utf-8') as f:
        segments = json.load(f)['segments']
    return len(segments), sum(len(s['translated_text']) for s in segments)


def placed_chapters(project_dir: str) -> tuple[int, int]:
    """(chapters with segments, chapters the exporter accepts: a chapter
    entry of the spine that loads and whose blocks match its segments)."""
    data = ProjectManager(project_dir).load()
    zf = open_archive(data.epub_path)
    _, _, spine = read_package(zf)
    items = chapter_items(spine)
    by_chapter: dict = {}
    for seg in data.segments:
        by_chapter.setdefault(seg.chapter_id, []).append(seg)
    placed = 0
    for chapter_id, segments in by_chapter.items():
        loaded = load_chapter(zf, items[chapter_id].path) if chapter_id in items else None
        placed += loaded is not None and _matches(loaded[3], segments)
    return len(by_chapter), placed


def web_loads(client, name: str, segments: int) -> bool:
    detail = client.get(f'/api/projects/{name}')
    return (client.get('/').status_code == 200
            and detail.status_code == 200
            and detail.get_json()['progress']['total'] == segments
            and client.get(f'/project/{name}/review').status_code == 200)


def check_copy(copy_dir: str, client, name: str) -> tuple[dict, list[str]]:
    project_file = os.path.join(copy_dir, PROJECT_FILE)
    _, before = read_segments(project_file)
    report = migrate_project(copy_dir)
    migrated, after = read_segments(project_file)
    with_segments, placed = placed_chapters(copy_dir)
    loads = web_loads(client, name, migrated)
    result = {
        'chapters': report.chapters, 'chapters_matched': report.chapters_matched,
        'segments': report.segments, 'segments_matched': report.segments_matched,
        'groups': len(report.merged), 'grouped': sum(len(g) for g in report.merged),
        'count_differs': report.count_differs,
        'chars': (before, after),
        'placed': (placed, with_segments), 'loads': loads,
    }
    problems = []
    if report.unmatched:
        problems.append(f'{len(report.unmatched)} chapters unmatched, nothing written')
    if after != before:
        problems.append('translated characters changed')
    if placed != with_segments:
        problems.append(f'{with_segments - placed} chapters not placed by the export')
    if not loads:
        problems.append('does not load in the web app')
    return result, problems


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

    parent = os.path.realpath(tempfile.gettempdir())
    for place, what in ((ROOT, 'the repository'),
                        (os.path.realpath(args.projects_dir), 'the projects directory')):
        if os.path.commonpath([parent, place]) == place:
            print(f'ERROR the temporary directory would be inside {what}')
            return 2
    work = tempfile.mkdtemp(prefix='phase2-migrate-', dir=parent)
    try:
        return run(args, names, work)
    finally:
        shutil.rmtree(work)


def run(args, names: list[str], copies: str) -> int:
    client = create_app(projects_dir=copies).test_client()
    keys = ('chapters_matched', 'chapters', 'segments_matched', 'segments', 'groups',
            'grouped', 'count_differs')
    totals = dict.fromkeys(keys + ('chars_equal', 'placed', 'with_segments', 'loads'), 0)
    failed = 0
    for index, name in enumerate(names, 1):
        label = f'project-{index}' if args.anonymize else name
        project_dir = os.path.join(args.projects_dir, name)
        before = snapshot(project_dir)
        copy_dir = os.path.join(copies, name)
        os.makedirs(copy_dir)
        try:
            for file in COPIED:
                if os.path.isfile(os.path.join(project_dir, file)):
                    shutil.copy2(os.path.join(project_dir, file), copy_dir)
            r, problems = check_copy(copy_dir, client, name)
        except Exception as e:  # any failure is a FAIL, never a crash
            failed += 1
            print(f'FAIL {label}: migration check raised {type(e).__name__}')
            continue
        if snapshot(project_dir) != before:
            problems.append('files in the project directory changed')
        for key in keys:
            totals[key] += r[key]
        totals['chars_equal'] += r['chars'][0] == r['chars'][1]
        totals['placed'] += r['placed'][0]
        totals['with_segments'] += r['placed'][1]
        totals['loads'] += r['loads']
        line = (f"{label}: chapters {r['chapters_matched']}/{r['chapters']}, "
                f"segments {r['segments_matched']}/{r['segments']}, "
                f"merge groups {r['groups']} ({r['grouped']} segments), "
                f"count differs {r['count_differs']}, "
                f"translated chars {r['chars'][0]} -> {r['chars'][1]}, "
                f"export places {r['placed'][0]}/{r['placed'][1]}, "
                f"web load {'ok' if r['loads'] else 'failed'}")
        if problems:
            failed += 1
            print(f'FAIL {line}; ' + '; '.join(problems))
        else:
            print(f'PASS {line}')

    print(f"TOTAL projects {len(names)}  chapters {totals['chapters_matched']}/{totals['chapters']}"
          f"  segments {totals['segments_matched']}/{totals['segments']}"
          f"  merge groups {totals['groups']} ({totals['grouped']} segments)"
          f"  count differs {totals['count_differs']}"
          f"  translated chars unchanged {totals['chars_equal']}"
          f"  export places {totals['placed']}/{totals['with_segments']}"
          f"  web loads {totals['loads']}  failed projects {failed}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
