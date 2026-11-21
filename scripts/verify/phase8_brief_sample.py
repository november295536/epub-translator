#!/usr/bin/env python3
"""Phase 8 sample: 10 segments of one real project with and without a brief.

Makes REAL model calls: it sends the text of 10 segments of the chosen book
to the model, twice each. Run it only when that is intended.

Copies one project of --projects-dir (its project.json, source.epub and
glossary.json; the --project-index-th directory holding a project.json, in
alphabetical order, from 1) into a new temporary directory outside every
git work tree and outside --projects-dir, migrates the copy when it is
schema 1, and picks a fixed sample: of the text segments (not code) with at
least MIN_WORDS words, in book order, the ones at positions
i * count // 10 for i = 0..9. Each is translated twice with --model, as a
translation run would (its glossary, its context), once without a brief
and once with the brief of --brief-file. Nothing is stored in the copy.

Writes `brief-sample.html` to --out: the brief, then per segment the source
and both translations side by side as plain text (one line per top-level
block; no book markup is rendered), for the owner to judge. Prints counts
only: the sample size, the model calls, and translated / failed per variant.
The copy is removed at the end, also on an error or Ctrl-C (a failure to
remove it is reported, without its path). The projects directory is only
read: the run FAILs if any file under it changed (name, size, modification
time). --out may not lie inside the projects directory or a git work tree.

With --anonymize the project is shown as project-N; otherwise by its
directory name. No book text and no brief are printed either way.

Usage:
    python3 scripts/verify/phase8_brief_sample.py --projects-dir projects
        --project-index N --brief-file FILE --out DIR [--model haiku] [--anonymize]

Exit code: 0 when all ten segments have both translations and the projects
directory is unchanged, 1 otherwise (the file and the counts are still
written when some translation failed; nothing when a rate limit or a login
stopped the run), 2 when the setup is refused (no such project, fewer than
10 such segments, an empty brief or one longer than the project's limit of
2000 characters, a copy that migration leaves at schema 1,
a place refused above).
"""

from __future__ import annotations

import argparse
import html
import logging
import os
import re
import shutil
import sys
import tempfile
import threading

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
# Run as a file, sys.path[0] is this script's directory (no src there)
sys.path.insert(0, HERE)

import src.core.migrate as migrate  # noqa: E402
import src.llm as llm  # noqa: E402
import src.core.validation as validation  # noqa: E402
from src.core.project import MAX_BRIEF_CHARS, ProjectManager  # noqa: E402
from src.core.translator import TranslationEngine, translate_segment  # noqa: E402
from src.glossary.glossary_manager import load_glossary  # noqa: E402
PROJECT_FILE = 'project.json'
COPIED = (PROJECT_FILE, 'source.epub', 'glossary.json')
SAMPLE = 10
# Shorter segments (a heading, a caption) say little about tone
MIN_WORDS = 50
OUT_FILE = 'brief-sample.html'


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


def in_work_tree(path: str) -> bool:
    """Whether the resolved `path` lies in a git work tree."""
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


class CountingBackend:
    """Passes every call to `inner` and counts it."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0
        self.lock = threading.Lock()
        for attr in ('name', 'capabilities', 'default_model', 'auth_hint'):
            setattr(self, attr, getattr(inner, attr))

    def list_models(self):
        return self.inner.list_models()

    def complete(self, request, cancel):
        with self.lock:
            self.calls += 1
        return self.inner.complete(request, cancel)


def pick(segments: list) -> list:
    """The fixed sample (see the module doc); [] when there are too few."""
    eligible = [s for s in segments
                if s.segment_type != 'code' and not s.passthrough and s.word_count >= MIN_WORDS]
    if len(eligible) < SAMPLE:
        return []
    return [eligible[i * len(eligible) // SAMPLE] for i in range(SAMPLE)]


# A line break or the end of a table cell: a space follows in the text
_SPACED = re.compile(r'(<br\b[^>]*>|</t[dh]\s*>)', re.IGNORECASE)


def readable(markup: str) -> str:
    """The text of book markup, escaped: one line per top-level block
    (validation.top_level_units), its text as a reader sees it
    (validation.plain_text: parsed, tags, script and style removed, entities
    decoded; a space at a line break and between table cells). The page shows text only, so nothing of the book is run or
    loaded by the browser. Markup that does not parse is shown as it is."""
    markup = _SPACED.sub(r'\1 ', markup)  # one two, not onetwo
    units = [unit[:2] for unit in validation.top_level_units(markup)] or [(0, len(markup))]
    lines = (' '.join(validation.plain_text(markup[start:end]).split()) for start, end in units)
    return html.escape('\n'.join(line for line in lines if line))


def cell(outcome) -> str:
    """A translation as readable text, or the reason it failed."""
    if outcome.text is None:
        return f'<span class="failed">（翻譯失敗：{html.escape(outcome.issues[0].code)}）</span>'
    return readable(outcome.text)


def page(brief: str, rows: list) -> str:
    """The side-by-side file: rows of (segment id, source html, without, with)."""
    body = ''.join(
        f'<tr><th colspan="3">{html.escape(seg_id)}</th></tr>'
        f'<tr><td>{readable(source)}</td><td>{cell(without)}</td><td>{cell(with_brief)}</td></tr>\n'
        for seg_id, source, without, with_brief in rows)
    return ('<!doctype html>\n<html><head><meta charset="utf-8"><title>Brief sample</title>'
            '<style>table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;'
            'padding:.5em;vertical-align:top;width:33%;white-space:pre-wrap}'
            'th{background:#eee;text-align:left}'
            '.failed{color:#a00}</style></head><body>\n'
            f'<h1>Brief</h1><pre>{html.escape(brief)}</pre>\n'
            '<table><tr><th>原文</th><th>無簡介</th><th>有簡介</th></tr>\n'
            f'{body}</table></body></html>\n')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--projects-dir', required=True, help='Directory of the project folders')
    parser.add_argument('--project-index', type=int, required=True,
                        help='1-based position among the directories holding a project.json')
    parser.add_argument('--brief-file', required=True, help='File holding the brief')
    parser.add_argument('--out', required=True, help='Directory for the side-by-side file')
    parser.add_argument('--model', default='haiku', help='Model (default: haiku)')
    parser.add_argument('--anonymize', action='store_true', help='Print project-N, no names or paths')
    args = parser.parse_args(argv)
    try:
        return sample(args)
    except BaseException as e:  # noqa: BLE001 - also Ctrl-C: no path, text or traceback
        print(f'ERROR the sample stopped ({type(e).__name__})')
        return 1


def sample(args) -> int:
    try:
        with open(args.brief_file, encoding='utf-8') as f:
            brief = f.read().strip()
    except OSError as e:
        print(f'ERROR cannot read the brief file ({type(e).__name__})')
        return 2
    if not brief:
        print('ERROR the brief file is empty')
        return 2
    if len(brief) > MAX_BRIEF_CHARS:  # the rule of ProjectManager.set_brief
        print(f'ERROR the brief is longer than {MAX_BRIEF_CHARS} characters')
        return 2
    try:
        names = sorted(n for n in os.listdir(args.projects_dir)
                       if os.path.isfile(os.path.join(args.projects_dir, n, PROJECT_FILE)))
    except OSError as e:
        print(f'ERROR cannot list the projects directory ({type(e).__name__})')
        return 2
    if not 1 <= args.project_index <= len(names):
        print(f'ERROR no project {args.project_index} (there are {len(names)})')
        return 2
    name = names[args.project_index - 1]
    label = f'project-{args.project_index}' if args.anonymize else name
    project_dir = os.path.join(args.projects_dir, name)

    parent = os.path.realpath(tempfile.gettempdir())
    for place, what in ((parent, 'the temporary directory'), (args.out, '--out')):
        if in_work_tree(place):
            print(f'ERROR {what} would be inside a git work tree')
            return 2
        if inside(place, args.projects_dir):
            print(f'ERROR {what} would be inside the projects directory')
            return 2

    before = snapshot(args.projects_dir)
    work = None
    try:
        work = tempfile.mkdtemp(prefix='phase8-brief-')  # in `parent`
        code = run(args, brief, label, project_dir, work)
    finally:
        if work is not None:  # first: a failing snapshot below cannot skip it
            try:
                shutil.rmtree(work)
            except OSError as e:
                print(f'ERROR cannot remove the temporary directory ({type(e).__name__})')
                code = 1
    if snapshot(args.projects_dir) != before:
        print(f'FAIL {label}: files in the projects directory changed')
        return 1
    return code


def run(args, brief, label, project_dir, work) -> int:
    """The sample on a copy in `work`."""
    copy = os.path.join(work, str(args.project_index))  # no project name in a path
    os.makedirs(copy)
    for file in COPIED:
        if os.path.isfile(os.path.join(project_dir, file)):
            shutil.copy2(os.path.join(project_dir, file), copy)
    pm = ProjectManager(copy)
    if pm.load().schema != 2:
        migrate.migrate_project(copy)
        if pm.load().schema != 2:  # a chapter that did not match: nothing written
            print(f'ERROR {label}: the copy could not be migrated')
            return 2
    gm = load_glossary(os.path.join(copy, 'glossary.json'))
    chosen = pick(pm.data.segments)
    if not chosen:
        print(f'ERROR {label} has fewer than {SAMPLE} text segments of {MIN_WORDS} words or more')
        return 2

    backend = CountingBackend(llm.backend_for(pm.data.backend))
    engine = TranslationEngine(model=args.model, backend=backend)
    translations = pm.data.segmentation == 2
    rows = []
    quiet = logging.NullHandler()  # no log line (segment ids, model output) on stderr
    logging.getLogger().addHandler(quiet)
    try:
        for seg in chosen:
            without = translate_segment(engine, seg, pm.data.segments, gm,
                                        translations=translations)
            with_brief = translate_segment(engine, seg, pm.data.segments, gm,
                                           translations=translations, brief=brief)
            rows.append((seg.segment_id, seg.html, without, with_brief))
    finally:
        logging.getLogger().removeHandler(quiet)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, OUT_FILE), 'w', encoding='utf-8') as f:
        f.write(page(brief, rows))
    counts = []
    for i, variant in ((2, 'without'), (3, 'with')):
        done = sum(row[i].text is not None for row in rows)
        counts.append(f'{variant} brief: translated {done} failed {len(rows) - done}')
    print(f'{label}: sampled {len(rows)}  calls {backend.calls}  ' + '  '.join(counts))
    print(f'wrote {OUT_FILE}')
    if not all(row[2].text is not None and row[3].text is not None for row in rows):
        print(f'FAIL {label}: not every sampled segment has both translations')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
