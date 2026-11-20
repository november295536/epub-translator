#!/usr/bin/env python3
"""Phase 5 check: glossary compliance of the existing translations.

Copies every project of --projects-dir (its project.json, source.epub and
glossary.json) into a new temporary directory outside the repository and
outside --projects-dir, migrates the copies that are schema 1 (the code of
the migrate command), and then, per project, runs the check of
`translate.py validate` (src/core/validation.py, check_segment with the
project's glossary) over every translated or reviewed segment with text.
It prints the number of glossary entries, translated segments, segments
that pass the structural check (only those get glossary warnings), segments
with at least one term_not_applied warning, the number of distinct terms
involved, and per glossary category the number of warnings (one per
segment and term) and of distinct terms.

Only counts and category names are printed, never book text or terms.
Everything in the temporary directory is removed at the end. The projects
directory is only read: a project FAILs if any file in it changed during
the run.

Without --anonymize it prints project directory names. With --anonymize it
prints project-N labels (alphabetical position among the directories of
--projects-dir that contain a project.json) and no names or paths.

--sample-out FILE writes a sample of SAMPLE_SIZE warnings (seed
SAMPLE_SEED, taken in turn from each project) as Markdown, for a person to
judge: project label, segment id, the term, its category and expected
translation, and an excerpt of source and translation around the term.
That file holds book text: it is written only to FILE, which must lie
outside the repository and outside --projects-dir, and its path and content
are never printed.

Usage:
    python3 scripts/verify/phase5_glossary_check.py [--projects-dir projects] [--anonymize]
        [--sample-out FILE]

Exit code: 0 when every project PASSes, 1 when any FAILs (a migration that
does not match, an error, a changed project directory), 2 when the projects
directory cannot be listed, or the temporary directory or the sample file
would be inside the repository or the projects directory (checked before
anything is written).
"""

from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import sys
import tempfile
from collections import Counter
from html import unescape
from itertools import zip_longest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
# At index 0, ahead of everything (see phase2_roundtrip.py)
sys.path.insert(0, ROOT)

from src.core.migrate import migrate_project  # noqa: E402
from src.core.project import ProjectManager, SegmentStatus  # noqa: E402
from src.core.validation import check_segment, terms_not_applied  # noqa: E402
from src.glossary.glossary_manager import CATEGORY_LABELS, GlossaryManager, term_pattern  # noqa: E402

PROJECT_FILE = 'project.json'
COPIED = (PROJECT_FILE, 'source.epub', 'glossary.json')
COUNTS = ('entries', 'translated', 'passing', 'with_issue')
SAMPLE_SIZE = 20
SAMPLE_SEED = 5
EXCERPT = 60  # characters on each side of the term
_TAG = re.compile(r'<[^>]*>')


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


def check_copy(copy_dir: str) -> tuple[dict, list[str]]:
    """The counts of one migrated copy, its warnings as (segment, entry)
    pairs, and its problems."""
    pm = ProjectManager(copy_dir)
    if pm.load().schema != 2:
        migration = migrate_project(copy_dir)
        if migration.unmatched:
            return {}, [f'{len(migration.unmatched)} chapters unmatched by the migration']
        pm.load()
    gm = GlossaryManager(os.path.join(copy_dir, 'glossary.json'))
    gm.load()
    r = dict.fromkeys(COUNTS, 0)
    r.update(entries=len(gm.entries), found=[])
    for seg in pm.data.segments:
        if (seg.status not in (SegmentStatus.TRANSLATED, SegmentStatus.REVIEWED)
                or not seg.translated_text.strip()):
            continue
        r['translated'] += 1
        if check_segment(seg, seg.translated_text).blocking:
            continue
        r['passing'] += 1
        if seg.segment_type == 'code':  # code gets no glossary (check_segment)
            continue
        missing = terms_not_applied(seg.text, seg.translated_text, gm)
        r['with_issue'] += bool(missing)
        r['found'] += [(seg, entry) for entry in missing]
    return r, []


def category_text(found: list) -> str:
    """Per category: warnings and distinct terms ('none' without any)."""
    issues, terms = Counter(), {}
    for _, entry in found:
        category = entry.category or 'none'
        issues[category] += 1
        terms.setdefault(category, set()).add(entry.term.lower())
    return ', '.join(f'{c}: {issues[c]} warnings, {len(terms[c])} terms'
                     for c in sorted(issues)) or 'none'


def distinct_terms(found: list) -> int:
    return len({entry.term.lower() for _, entry in found})


def counts_line(r: dict, terms: int) -> str:
    return (f"glossary entries {r['entries']}, translated {r['translated']}, "
            f"passing the structural check {r['passing']}, "
            f"with term_not_applied {r['with_issue']}, terms {terms}, "
            f"by category ({category_text(r['found'])})")


def inside(path: str, place: str) -> bool:
    path, place = os.path.realpath(path), os.path.realpath(place)
    return os.path.commonpath([path, place]) == place


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--projects-dir', default='projects',
                        help='Directory that holds the project folders (default: projects)')
    parser.add_argument('--anonymize', action='store_true',
                        help='Print project-N labels; omit names and paths')
    parser.add_argument('--sample-out', metavar='FILE',
                        help='Write a sample of the warnings, with book text, to FILE '
                             '(outside the repository and the projects directory)')
    args = parser.parse_args(argv)

    shown_dir = '<projects-dir>' if args.anonymize else args.projects_dir
    try:
        entries = os.listdir(args.projects_dir)
    except OSError as e:
        print(f'ERROR cannot list projects directory {shown_dir} ({type(e).__name__})')
        return 2
    names = sorted(name for name in entries
                   if os.path.isfile(os.path.join(args.projects_dir, name, PROJECT_FILE)))

    places = ((ROOT, 'the repository'), (args.projects_dir, 'the projects directory'))
    parent = os.path.realpath(tempfile.gettempdir())
    for what_path, what in ((parent, 'the temporary directory'),
                            *(((args.sample_out, 'the sample file'),) if args.sample_out else ())):
        for place, where in places:
            if inside(what_path, place):
                print(f'ERROR {what} would be inside {where}')
                return 2
    work = tempfile.mkdtemp(prefix='phase5-glossary-', dir=parent)
    try:
        return run(args, names, work)
    finally:
        shutil.rmtree(work)


def run(args, names: list[str], work: str) -> int:
    totals = dict.fromkeys(COUNTS, 0)
    totals['found'] = []
    terms_total, failed, per_project = 0, 0, []
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
        for key in (*COUNTS, 'found'):
            totals[key] += r[key]
        terms = distinct_terms(r['found'])
        terms_total += terms  # glossaries are per project: a term counts once per project
        per_project.append((label, r['found']))
        line = f'{label}: {counts_line(r, terms)}'
        if problems:
            failed += 1
            print(f'FAIL {line}; ' + '; '.join(problems))
        else:
            print(f'PASS {line}')
    print(f'TOTAL projects {len(names)}  {counts_line(totals, terms_total)}  failed projects {failed}')
    if args.sample_out:
        sample = pick_sample(per_project)
        with open(args.sample_out, 'w', encoding='utf-8') as f:
            f.write(sample_markdown(sample))
        print(f'sample written: {len(sample)} warnings')
    return 1 if failed else 0


def pick_sample(per_project: list, size: int = SAMPLE_SIZE, seed: int = SAMPLE_SEED) -> list:
    """Up to `size` (label, segment, entry) taken in turn from each
    project's warnings, each project's in an order shuffled with `seed`."""
    rng = random.Random(seed)
    queues = []
    for label, found in per_project:
        shuffled = list(found)
        rng.shuffle(shuffled)
        queues.append([(label, seg, entry) for seg, entry in shuffled])
    in_turn = [item for row in zip_longest(*queues) for item in row if item is not None]
    return in_turn[:size]


def excerpt(text: str, start: int, end: int, width: int = EXCERPT) -> str:
    """text[start:end] with up to `width` characters on each side, on one
    line; an ellipsis marks a cut."""
    a, b = max(0, start - width), min(len(text), end + width)
    piece = ' '.join(text[a:b].split())
    return ('…' if a else '') + piece + ('…' if b < len(text) else '')


def plain(translation: str) -> str:
    """The text of a stored translation: tags removed, entities decoded."""
    return unescape(_TAG.sub('', translation))


def sample_item(number: int, label: str, seg, entry) -> str:
    source = seg.text
    m = re.search(term_pattern(entry.term), source, re.IGNORECASE)
    start, end = (m.start(), m.end()) if m else (0, 0)
    translation = plain(seg.translated_text)
    # the same relative place in the translation (the term itself is not there)
    middle = round(len(translation) * start / len(source)) if source else 0
    category = CATEGORY_LABELS.get(entry.category, entry.category or '（無）')
    return '\n'.join([
        f'## {number}. {label} · `{seg.segment_id}`',
        '',
        f'- 術語：{entry.term}（{category}）',
        f'- 術語表譯名：{entry.translation}',
        f'- 原文：{excerpt(source, start, end)}',
        f'- 譯文（對應位置附近）：{excerpt(translation, middle, middle)}',
        '- 判斷：☐ 真正的問題　☐ 誤報　備註：',
        '',
    ])


def sample_markdown(sample: list) -> str:
    head = [
        f'# 術語檢查樣本（{len(sample)} 筆）',
        '',
        '每一筆是一段譯文被判定「術語未依術語表翻譯」（term_not_applied）。'
        f'樣本以固定亂數種子（{SAMPLE_SEED}）從各專案輪流抽出。'
        '比對方式：去掉標籤與括號內的英文原文、忽略空白與大小寫後，'
        '檢查譯文是否含有術語表譯名。',
        '請逐筆判斷是否為真正的問題（應該改譯文），或是誤報（譯文其實可以接受）。',
        '',
    ]
    return '\n'.join(head + [sample_item(n, *item) for n, item in enumerate(sample, 1)])


if __name__ == '__main__':
    sys.exit(main())
