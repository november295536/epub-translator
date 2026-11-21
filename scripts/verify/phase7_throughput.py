#!/usr/bin/env python3
"""Phase 7 measurement: translation throughput on a copy of one real project.

Makes REAL model calls: it sends the text of the chosen book's segments to
the model, as a translation run does. Run it only when that is intended.

Copies one project of --projects-dir (its project.json, source.epub and
glossary.json; the --project-index-th directory holding a project.json, in
alphabetical order, from 1) into a new temporary directory outside every
git work tree and outside --projects-dir, migrates the copy when it is
schema 1, and in the copy:

- takes the first --segments text segments (not code, not passthrough)
  that are pending or translated, and makes them pending with no
  translation (translated from scratch);
- marks every other pending segment failed, so that a normal start queues
  exactly those segments (a failed neighbour gives its source as context,
  as a pending one does);
- runs `translate.py start --project <copy> --model M --workers W` in this
  process, with the code of --code (default: this checkout). Pointing
  --code at a checkout of `main` measures the behaviour before Phase 7
  with the same script, the same segments and the same model.

Every model call goes through a counting wrapper around the backend: per
segment it records the calls and their duration. A segment "needed a
retry" when it took more than one call, "a split" when two different
single-message requests were sent for it (the halves of the split). Prints
counts and timings only: wall seconds of the run, seconds per segment,
segments per minute (translated segments per minute of wall time), the
median and total seconds per call, the number of calls, the segments by
final status, the failures by issue code, and the retry and split counts.
The run's own output (segment ids) and log messages are not shown, and no
book text is printed. The copy is removed at the end. The projects
directory is only read: the run FAILs if any file in it changed.

With --anonymize the project is shown as project-N; otherwise by its
directory name.

Usage:
    python3 scripts/verify/phase7_throughput.py --projects-dir projects --project-index N
        [--segments 50] [--model haiku] [--workers 1] [--anonymize]
        [--backend NAME] [--code DIR]

Exit code: 0 when the run translated or failed every chosen segment and
the projects directory is unchanged, 1 otherwise, 2 when the setup is
refused (no such project, too few segments, the temporary directory inside
a git work tree or the projects directory, --backend with a --code that
has no backends).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
import shutil
import statistics
import sys
import tempfile
import threading
import time
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


class Recorder:
    """Per segment: (number of messages, digest of the first message,
    seconds) of every call made while it was being translated."""

    def __init__(self):
        self.calls: dict = {}
        self.local = threading.local()
        self.lock = threading.Lock()

    def record(self, request, seconds: float) -> None:
        digest = hashlib.sha256(request.messages[0].content.encode()).hexdigest()
        key = getattr(self.local, 'segment', None)
        with self.lock:
            self.calls.setdefault(key, []).append((len(request.messages), digest, seconds))


class CountingBackend:
    """Passes every call to `inner` and records it."""

    def __init__(self, inner, recorder: Recorder):
        self.inner = inner
        self.recorder = recorder
        for attr in ('name', 'capabilities', 'default_model', 'auth_hint'):
            setattr(self, attr, getattr(inner, attr))

    def list_models(self):
        return self.inner.list_models()

    def complete(self, request, cancel):
        start = time.monotonic()
        try:
            return self.inner.complete(request, cancel)
        finally:
            self.recorder.record(request, time.monotonic() - start)


def segment_stats(calls: dict, chosen: list) -> dict:
    """Retry and split counts of the chosen segments."""
    retried = split = 0
    for segment_id in chosen:
        seg_calls = calls.get(segment_id, [])
        retried += len(seg_calls) > 1
        split += len({digest for n, digest, _ in seg_calls if n == 1}) > 1
    return {'retried': retried, 'split': split}


def prepare(pm, count: int, statuses) -> list:
    """Make the first `count` pending-or-translated text segments pending
    from scratch, every other pending one failed. Returns their ids."""
    chosen = []
    for seg in pm.data.segments:
        if (len(chosen) < count and seg.segment_type != 'code'
                and not getattr(seg, 'passthrough', False)
                and seg.status in (statuses.PENDING, statuses.TRANSLATED)):
            chosen.append(seg.segment_id)
            seg.status = statuses.PENDING
            seg.translated_text = ''
            seg.translated_at = ''
            seg.issues = []
            seg.model = ''
            seg.usage = None
        elif seg.status == statuses.PENDING:
            seg.status = statuses.FAILED
    return chosen


def report_lines(label: str, wall: float, chosen: list, final: dict, calls: dict,
                 failures: Counter) -> list:
    statuses = Counter(final[s] for s in chosen)
    translated = statuses.get('translated', 0)
    durations = [d for seg_calls in calls.values() for _, _, d in seg_calls]
    stats = segment_stats(calls, chosen)
    n = len(chosen)
    per_minute = translated / (wall / 60) if wall > 0 else 0.0
    lines = [
        f'{label}: segments {n}  wall {wall:.1f} s  seconds per segment {wall / n:.2f}  '
        f'segments per minute {per_minute:.2f}',
        f'calls {len(durations)}  per call median {statistics.median(durations) if durations else 0:.2f} s  '
        f'total {sum(durations):.1f} s',
        'status ' + ', '.join(f'{k} {v}' for k, v in sorted(statuses.items())),
        'failures by code ' + (', '.join(f'{k} {v}' for k, v in sorted(failures.items())) or 'none'),
        f'needed a retry {stats["retried"]}  needed a split {stats["split"]}',
    ]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--projects-dir', required=True, help='Directory of the project folders')
    parser.add_argument('--project-index', type=int, required=True,
                        help='1-based position among the directories holding a project.json')
    parser.add_argument('--segments', type=int, default=50, help='Segments to translate (default 50)')
    parser.add_argument('--model', default='haiku', help='Model (default: haiku)')
    parser.add_argument('--workers', type=int, default=1, help='Parallel workers (default 1)')
    parser.add_argument('--backend', default=None,
                        help='Backend name (default: the project\'s); not with a --code of main')
    parser.add_argument('--code', default=HERE,
                        help='Checkout whose code runs (default: this one); a checkout of main '
                             'measures the behaviour before Phase 7')
    parser.add_argument('--anonymize', action='store_true', help='Print project-N, no names or paths')
    args = parser.parse_args(argv)
    try:
        return measure(args)
    except Exception as e:  # noqa: BLE001 - no path or text may reach the output
        print(f'ERROR the measurement stopped ({type(e).__name__})')
        return 1


def measure(args) -> int:
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
    if in_work_tree(parent):
        print('ERROR the temporary directory would be inside a git work tree')
        return 2
    if inside(parent, args.projects_dir):
        print('ERROR the temporary directory would be inside the projects directory')
        return 2

    sys.path.insert(0, os.path.abspath(args.code))
    import src.llm as llm  # noqa: E402 - from --code
    import translate  # noqa: E402
    from src.core.migrate import migrate_project  # noqa: E402
    from src.core.project import ProjectManager, SegmentStatus  # noqa: E402
    if args.backend is not None and not hasattr(llm, 'backend_for'):
        print('ERROR --backend needs the code of Phase 7 or later')
        return 2

    work = tempfile.mkdtemp(prefix='phase7-throughput-', dir=parent)
    before = snapshot(project_dir)
    try:
        copy = os.path.join(work, str(args.project_index))  # no project name in a path
        os.makedirs(copy)
        for file in COPIED:
            if os.path.isfile(os.path.join(project_dir, file)):
                shutil.copy2(os.path.join(project_dir, file), copy)
        pm = ProjectManager(copy)
        if pm.load().schema != 2:
            migrate_project(copy)
            pm.load()
        chosen = prepare(pm, args.segments, SegmentStatus)
        if len(chosen) < args.segments:
            print(f'ERROR {label} has only {len(chosen)} such segments')
            return 2
        pm.save()

        recorder = Recorder()
        inner = (llm.backend_for(args.backend) if args.backend is not None
                 else llm.default_backend())
        counting = CountingBackend(inner, recorder)
        real_translate_segment = translate.translate_segment

        def translate_segment(engine, seg, *a, **kw):
            recorder.local.segment = seg.segment_id
            try:
                return real_translate_segment(engine, seg, *a, **kw)
            finally:
                recorder.local.segment = None

        cmd = ['start', '--project', copy, '--model', args.model, '--workers', str(args.workers)]
        if args.backend is not None:
            cmd += ['--backend', args.backend]
        replaced = [(translate, 'translate_segment', translate_segment),
                    # a project without a backend name uses default_backend
                    (llm, 'default_backend', lambda: counting)]
        if hasattr(llm, 'backend_for'):
            replaced.append((llm, 'backend_for', lambda name: counting))
        originals = [(module, attr, getattr(module, attr)) for module, attr, _ in replaced]
        quiet = logging.NullHandler()  # no log line (segment ids) on stderr
        logging.getLogger().addHandler(quiet)
        try:
            for module, attr, value in replaced:
                setattr(module, attr, value)
            start = time.monotonic()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                try:
                    translate.cli.main(cmd, standalone_mode=False)
                except SystemExit:
                    pass  # exit 1: some segments failed; counted below
            wall = time.monotonic() - start
        finally:
            for module, attr, value in originals:
                setattr(module, attr, value)
            logging.getLogger().removeHandler(quiet)

        with open(os.path.join(copy, PROJECT_FILE), encoding='utf-8') as f:
            stored = {s['segment_id']: s for s in json.load(f)['segments']}
        final = {s: stored[s].get('status', 'pending') for s in chosen}
        failures = Counter(stored[s]['issues'][0]['code'] for s in chosen
                           if final[s] == 'failed' and stored[s].get('issues'))
        for line in report_lines(label, wall, chosen, final, recorder.calls, failures):
            print(line)
        done = all(final[s] in ('translated', 'failed') for s in chosen)
        code = 0 if done else 1
    finally:
        changed = snapshot(project_dir) != before
        shutil.rmtree(work, ignore_errors=True)
    if changed:
        print(f'FAIL {label}: files in the project directory changed')
        return 1
    return code


if __name__ == '__main__':
    sys.exit(main())
