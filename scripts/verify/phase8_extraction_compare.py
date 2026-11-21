#!/usr/bin/env python3
"""Phase 8 comparison: term extraction on a copy of one real project.

Makes REAL model calls: it sends the text of the chosen book to the model,
as `glossary extract` does. Run it only when that is intended.

Copies one project of --projects-dir (its project.json, source.epub and
glossary.json; the --project-index-th directory holding a project.json, in
alphabetical order, from 1) into a new temporary directory outside every
git work tree and outside --projects-dir, migrates the copy when it is
schema 1, and runs the term extraction of --code (default: this checkout)
on the copy's text segments with --model. Pointing --code at a checkout of
`main` gives the extraction before Phase 8 (first proposal wins), the
default the one after (proposals consolidated) - the same script, segments
and model.

--chunks N: only the first N extraction batches are sent; the later ones
get no call (a cheaper run on a long book; the same batches before and
after, since the batching did not change).

Counts every model call, and records the terms each batch proposed. Writes
`terms-<name>.json` to --out (name: --name, default `before` for a --code
other than this checkout, else `after`): {"terms": the extracted list,
"conflicts": {term: {translation: number of batches}} for every term
proposed with more than one translation}. Prints counts only: batches sent,
model calls, proposals, terms, and terms with conflicting translations
among the proposals. The copy is removed at the end, also on an error or
Ctrl-C (a failure to remove it is reported, without its path). The
projects directory is only read: the run FAILs if any file under it
changed (name, size, modification time). --out may not lie inside the
projects directory or a git work tree.

With --anonymize the project is shown as project-N; otherwise by its
directory name. No term, translation or book text is printed either way.

Usage:
    python3 scripts/verify/phase8_extraction_compare.py --projects-dir projects
        --project-index N --out DIR [--model haiku] [--chunks N] [--anonymize]
        [--code DIR] [--name before|after]

Exit code: 0 when the extraction ran to the end and the projects directory
is unchanged, 1 otherwise (also when a rate limit or a login stopped it),
2 when the setup is refused.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from collections import Counter

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
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


def conflicts(proposals: list) -> dict:
    """{term: {translation: batches}} for every term (case-insensitive, the
    first spelling) that the batches proposed with more than one translation."""
    seen: dict = {}
    for terms in proposals:
        for t in terms:
            term, translation = t.get('term'), t.get('translation')
            if isinstance(term, str) and isinstance(translation, str):
                spelling, counts = seen.setdefault(term.lower(), (term, Counter()))
                counts[translation] += 1
    return {spelling: dict(counts) for spelling, counts in seen.values() if len(counts) > 1}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--projects-dir', required=True, help='Directory of the project folders')
    parser.add_argument('--project-index', type=int, required=True,
                        help='1-based position among the directories holding a project.json')
    parser.add_argument('--out', required=True, help='Directory for the term list')
    parser.add_argument('--model', default='haiku', help='Model (default: haiku)')
    parser.add_argument('--chunks', type=int, default=None,
                        help='Send only the first N extraction batches (default: all)')
    parser.add_argument('--code', default=HERE,
                        help='Checkout whose code runs (default: this one); a checkout of main '
                             'gives the extraction before Phase 8')
    parser.add_argument('--name', choices=('before', 'after'), default=None,
                        help='Name of the term list (default: before for another --code, '
                             'else after)')
    parser.add_argument('--anonymize', action='store_true', help='Print project-N, no names or paths')
    args = parser.parse_args(argv)
    if args.name is None:
        args.name = 'after' if os.path.realpath(args.code) == os.path.realpath(HERE) else 'before'
    try:
        return compare(args)
    except BaseException as e:  # noqa: BLE001 - also Ctrl-C: no path, text or traceback
        print(f'ERROR the comparison stopped ({type(e).__name__})')
        return 1


def compare(args) -> int:
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

    sys.path.insert(0, os.path.abspath(args.code))
    import src.llm as llm  # noqa: E402 - from --code
    from src.core.migrate import migrate_project  # noqa: E402
    from src.core.project import ProjectManager  # noqa: E402
    from src.glossary.term_extractor import ExtractionError, TermExtractor  # noqa: E402

    before = snapshot(args.projects_dir)
    work = None
    try:
        work = tempfile.mkdtemp(prefix='phase8-extraction-')  # in `parent`
        code = run(args, label, project_dir, work, llm, migrate_project, ProjectManager,
                   ExtractionError, TermExtractor)
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


def run(args, label, project_dir, work, llm, migrate_project, ProjectManager,
        ExtractionError, TermExtractor) -> int:
    """The extraction on a copy in `work`."""
    copy = os.path.join(work, str(args.project_index))  # no project name in a path
    os.makedirs(copy)
    for file in COPIED:
        if os.path.isfile(os.path.join(project_dir, file)):
            shutil.copy2(os.path.join(project_dir, file), copy)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        pm = ProjectManager(copy)
        if pm.load().schema != 2:
            migrate_project(copy)
            pm.load()
    segments = [{'text': s.text} for s in pm.data.segments if s.segment_type != 'code']

    backend = CountingBackend(llm.backend_for(pm.data.backend))
    extractor = TermExtractor(model=args.model, backend=backend)
    real = extractor.extract_from_text
    proposals = []

    def extract_from_text(text):
        if args.chunks is not None and len(proposals) >= args.chunks:
            return []  # beyond --chunks: no call
        terms = real(text)
        proposals.append(terms)
        return terms

    extractor.extract_from_text = extract_from_text
    stopped = None
    quiet = logging.NullHandler()  # no log line (model output) on stderr
    logging.getLogger().addHandler(quiet)
    try:
        terms = extractor.extract_from_segments(segments)
    except ExtractionError as e:
        stopped, terms = e, e.terms
    finally:
        logging.getLogger().removeHandler(quiet)

    found = conflicts(proposals)
    os.makedirs(args.out, exist_ok=True)
    file = f'terms-{args.name}.json'
    with open(os.path.join(args.out, file), 'w', encoding='utf-8') as f:
        json.dump({'terms': terms, 'conflicts': found}, f, ensure_ascii=False, indent=2)
    print(f'{label} {args.name}: batches sent {len(proposals)}  calls {backend.calls}  '
          f'proposals {sum(len(p) for p in proposals)}  terms {len(terms)}  '
          f'terms with conflicting translations {len(found)}')
    print(f'wrote {file}')
    if stopped is not None:
        print(f'FAIL {label}: the extraction stopped ({type(stopped).__name__})')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
