#!/usr/bin/env python3
"""Phase 4 check: protection round trip and the full check over existing translations.

Copies every project of --projects-dir (its project.json, source.epub and
glossary.json) into a new temporary directory outside the repository and
outside --projects-dir, migrates the copies that are schema 1 (the code of
the migrate command), and then, per project:

(a) restore(protect(html)) == html for every stored segment's html. A
    project with any segment for which this does not hold FAILs.
(b) the full check (the one of `translate.py validate` and of the export:
    src/core/validation.py, check_segment) over every translated or
    reviewed segment with text: the number of segments breaking each
    structural rule (a segment can break several), the number that fail
    the structural check at all (these are exported in the original
    language), and the number with each quality warning.
(c) the length ratio (translation characters per source character,
    whitespace removed) of the segments that pass the structural check and
    whose source has at least LENGTH_RATIO_MIN_CHARS characters: its 1st,
    5th, 50th, 95th and 99th percentile, and how many of them fall outside
    LENGTH_RATIO_BAND.
(d) over every stored text segment (translated or not): how many sources
    cannot be cut into their blocks (ambiguous_source: such a segment
    fails before any model call), and how many carry an attribute people
    read (TRANSLATABLE_ATTRIBUTES), which the model now translates.
(e) of the segments failing the structural check, how many fail only on
    the tag rules (TAG_RULES: unknown_tag, key_mismatch, extra_attributes,
    tag_count).
(f) of the translated or reviewed code segments, how many have a shebang
    (`#!`) as the first non-blank line of their source: since Phase 6 that
    line is code and must stay unchanged (validation.comment_flags).

Only counts, rule names and ratios are printed, never book text. Everything
in the temporary directory is removed at the end. The projects directory
is only read: a project FAILs if any file in it changed during the run.

Without --anonymize it prints project directory names. With --anonymize it
prints project-N labels (alphabetical position among the directories of
--projects-dir that contain a project.json) and no names or paths.

Usage:
    python3 scripts/verify/phase4_validate_check.py [--projects-dir projects] [--anonymize]

Exit code: 0 when every project PASSes, 1 when any FAILs (a round trip that
does not hold, a migration that does not match, an error, a changed
project directory), 2 when the projects directory cannot be listed or the
temporary directory would be inside the repository or the projects
directory (checked before it is created).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
# At index 0, ahead of everything (see phase2_roundtrip.py)
sys.path.insert(0, ROOT)

from src.core.migrate import migrate_project  # noqa: E402
from src.core.project import ProjectManager, SegmentStatus  # noqa: E402
from src.core.validation import (  # noqa: E402
    EXTRA_ATTRIBUTES, KEY_MISMATCH, LENGTH_RATIO_BAND, LENGTH_RATIO_MIN_CHARS, TAG_COUNT,
    UNKNOWN_TAG, check_segment, piece_text, protect, restore, source_issues,
)
from src.epub.blocks import normalize  # noqa: E402

PROJECT_FILE = 'project.json'
COPIED = (PROJECT_FILE, 'source.epub', 'glossary.json')
PERCENTILES = (1, 5, 50, 95, 99)
TAG_RULES = frozenset({UNKNOWN_TAG, KEY_MISMATCH, EXTRA_ATTRIBUTES, TAG_COUNT})
COUNTS = ('segments', 'round_trip_failed', 'translated', 'failing', 'tags_only', 'ambiguous',
          'with_attributes', 'shebang')


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


def percentile(values: list, p: int) -> float:
    """The p-th percentile (nearest rank) of a non-empty list."""
    ordered = sorted(values)
    rank = max(1, -(-p * len(ordered) // 100))  # ceil(p * n / 100), at least 1
    return ordered[rank - 1]


def check_copy(copy_dir: str) -> tuple[dict, list[str]]:
    """The counts of one migrated copy, and its problems."""
    pm = ProjectManager(copy_dir)
    if pm.load().schema != 2:
        migration = migrate_project(copy_dir)
        if migration.unmatched:
            return {}, [f'{len(migration.unmatched)} chapters unmatched by the migration']
        pm.load()
    r = dict.fromkeys(COUNTS, 0)
    r.update(rules=Counter(), warnings=Counter(), ratios=[])
    for seg in pm.data.segments:
        r['segments'] += 1
        protected = protect(seg.html)
        if restore(protected.html, protected) != seg.html:
            r['round_trip_failed'] += 1
        if seg.segment_type != 'code':
            r['ambiguous'] += bool(source_issues(seg.html, seg.fingerprints or None))
            r['with_attributes'] += any(key.shown for key in protected.keys.values())
        if (seg.status not in (SegmentStatus.TRANSLATED, SegmentStatus.REVIEWED)
                or not seg.translated_text.strip()):
            continue
        r['translated'] += 1
        r['shebang'] += seg.segment_type == 'code' and starts_with_shebang(seg.text)
        result = check_segment(seg, seg.translated_text)
        r['rules'].update({i.code for i in result.blocking})
        r['warnings'].update({i.code for i in result.issues if not i.blocking})
        if result.blocking:
            r['failing'] += 1
            r['tags_only'] += {i.code for i in result.blocking} <= TAG_RULES
        elif seg.segment_type != 'code':
            source = normalize(seg.text)
            if len(source) >= LENGTH_RATIO_MIN_CHARS:
                text = normalize(''.join(map(piece_text, result.pieces)))
                r['ratios'].append(len(text) / len(source))
    problems = []
    if r['round_trip_failed']:
        problems.append(f"round trip fails for {r['round_trip_failed']} segments")
    return r, problems


def starts_with_shebang(code: str) -> bool:
    """Whether the first non-blank line of the code starts with `#!` after
    its indentation (a
    shebang: code since Phase 6, review round 1)."""
    lines = [line for line in code.splitlines() if line.strip()]
    return bool(lines) and lines[0].lstrip().startswith('#!')


def counts_text(counts: Counter) -> str:
    return ', '.join(f'{k} {n}' for k, n in sorted(counts.items())) or 'none'


def ratio_text(ratios: list) -> str:
    if not ratios:
        return 'length ratio: no segment'
    low, high = LENGTH_RATIO_BAND
    flagged = sum(not low <= x <= high for x in ratios)
    shown = ' '.join(f'p{p} {percentile(ratios, p):.3f}' for p in PERCENTILES)
    return (f'length ratio over {len(ratios)} segments: {shown}; outside {low}-{high}: '
            f'{flagged} ({100 * flagged / len(ratios):.1f}%)')


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
    work = tempfile.mkdtemp(prefix='phase4-validate-', dir=parent)
    try:
        return run(args, names, work)
    finally:
        shutil.rmtree(work)


def run(args, names: list[str], work: str) -> int:
    totals = dict.fromkeys(COUNTS, 0)
    totals.update(rules=Counter(), warnings=Counter(), ratios=[])
    failed = 0
    for index, name in enumerate(names, 1):
        label = f'project-{index}' if args.anonymize else name
        project_dir = os.path.join(args.projects_dir, name)
        # numbered scratch directories: no project name reaches a path
        copy_dir = os.path.join(work, f'{index}')
        before = None
        try:
            before = snapshot(project_dir)
            os.makedirs(copy_dir)
            for file in COPIED:
                if os.path.isfile(os.path.join(project_dir, file)):
                    shutil.copy2(os.path.join(project_dir, file), copy_dir)
            r, problems = check_copy(copy_dir)
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
        for key in (*COUNTS, 'rules', 'warnings', 'ratios'):
            totals[key] += r[key]
        line = (f"{label}: round trip {r['segments'] - r['round_trip_failed']}/{r['segments']}, "
                f"translated {r['translated']}, failing the structural check {r['failing']} "
                f"({counts_text(r['rules'])}; only tag rules {r['tags_only']}), "
                f"warnings ({counts_text(r['warnings'])}), {ratio_text(r['ratios'])}, "
                f"sources that cannot be cut {r['ambiguous']}, "
                f"with translatable attributes {r['with_attributes']}, "
                f"translated code starting with a shebang {r['shebang']}")
        if problems:
            failed += 1
            print(f'FAIL {line}; ' + '; '.join(problems))
        else:
            print(f'PASS {line}')
    print(f"TOTAL projects {len(names)}  round trip "
          f"{totals['segments'] - totals['round_trip_failed']}/{totals['segments']}  "
          f"translated {totals['translated']}  failing the structural check {totals['failing']} "
          f"({counts_text(totals['rules'])}; only tag rules {totals['tags_only']})  "
          f"warnings ({counts_text(totals['warnings'])})  {ratio_text(totals['ratios'])}  "
          f"sources that cannot be cut {totals['ambiguous']}  "
          f"with translatable attributes {totals['with_attributes']}  "
          f"translated code starting with a shebang {totals['shebang']}  failed projects {failed}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
