#!/usr/bin/env python3
"""Run records check: the event records of real projects hold no book text
and add up.

For each directory of --projects-dir that contains a project.json and a
log/events.jsonl, reads the record (src/core/stats.read_records, the reader
of `translate.py stats`) and checks every record:

  - per event kind, its required fields (REQUIRED); the type of every
    field (src/core/stats.py shape_problem, which `stats` shares: a record
    of the wrong shape is a skipped line there);
  - keys: only those the record format defines (an unknown key is counted,
    never printed: it could be text);
  - string values only from closed sets: enum values (event, source, mode,
    kind, outcome, result, reason, state, effort, backend), issue codes
    (src/core/validation.py, the session's and the extraction's failure
    codes), an exception class name, a model alias, a record time, a run
    id; a segment id must be one OF THIS PROJECT (project.json). Segment
    ids are built from the EPUB's chapter ids (known-issues.md). A value
    outside these FAILs with its field and length, never its text;
  - per run with a run_end: its translated/failed/dropped counts equal the
    number of its segment records of each result.

Prints per project: records, skipped (unparsable) lines, runs, and the
result. Unparsable lines are counted and printed, not failed (a crash
mid-write).

Read-only: it reads project.json and log/events.jsonl only; it writes
nothing and takes no lock, so it can run while a translation appends.
Without --anonymize it prints project directory names; with --anonymize
project-N labels (alphabetical position).

Usage:
    python3 scripts/verify/run_records_check.py [--projects-dir projects] [--anonymize]

Exit code: 0 when every project with a record PASSes, 1 when any FAILs, 2
when the projects directory cannot be listed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.core.events import events_path  # noqa: E402
from src.core import validation  # noqa: E402
from src.core.stats import FIELDS, read_records, shape_problem  # noqa: E402
from src.glossary.term_extractor import UNUSABLE_REPLY  # noqa: E402
from src.llm import BACKENDS, KNOWN_LEVELS  # noqa: E402

ALLOWED_KEYS = FIELDS  # the fields src/core/stats.py knows the types of
ENUMS = {
    'event': {'run_start', 'run_end', 'call', 'segment', 'pause', 'resume', 'wait_extended'},
    'source': {'web', 'cli', 'retranslate', 'extract'},
    'mode': {'pending', 'retry_failed', 'selected'},
    'kind': {'request', 'retry', 'split', 'extract', 'consolidate'},
    'outcome': {'accepted', 'rejected', 'truncated', 'cancelled', 'rate_limit', 'auth',
                'backend_unavailable', 'refusal', 'timeout', 'transient', 'error', 'unexpected'},
    'result': {'translated', 'failed', 'dropped'},
    'reason': {'user', 'auto', 'rate_limit', 'auth', 'backend_unavailable',
               'edited_before_call', 'edited_during_call', 'project_replaced'},
    'state': {'completed', 'stopped', 'failed', 'interrupted'},
    'effort': set(KNOWN_LEVELS),
    'backend': set(BACKENDS) | {'fake'},
}
# Issue and warning codes: the check's (src/core/validation.py), the
# failure codes of the translation session and of term extraction
CODES = (set(validation.STRUCTURAL_CODES) | set(validation.QUALITY_CODES)
         | {validation.TERM_NOT_APPLIED, validation.NOT_HTML, validation.EMPTY_REPLY,
            'refusal', 'error', 'transient', 'unexpected', UNUSABLE_REPLY})
# The fields each event must have
REQUIRED = {
    'run_start': ('source',),
    'run_end': ('source', 'state'),
    'call': ('source', 'kind', 'backend', 'model', 'timeout', 'seconds', 'outcome'),
    'segment': ('source', 'segment', 'result'),
    'pause': ('source', 'reason'),
    'resume': ('source', 'reason'),
    'wait_extended': ('source',),
}
# An exception class name (src/core/events.py records type(e).__name__;
# the CLI's stop after too many unexpected failures: UnexpectedStreak)
CLASS = re.compile(r'^[A-Z][A-Za-z0-9]{0,60}(Error|Exception|Interrupt|Exit|Streak)$')
MODEL = re.compile(r'^[a-z0-9][a-z0-9.\-]{1,40}$')      # a model alias
TS = re.compile(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$')
RUN = re.compile(r'^\d{8}T\d{6}Z-[0-9a-f]{6}$')


def _known(key: str, value: str) -> bool:
    """Whether a string value is one the format defines for `key` (so it
    is no text of the book)."""
    if key in ENUMS:
        return value in ENUMS[key]
    if key in ('issues', 'warnings'):
        return value in CODES
    return bool({'error': CLASS, 'model': MODEL, 'ts': TS, 'run': RUN}[key].match(value)) \
        if key in ('error', 'model', 'ts', 'run') else False


def record_problems(record: dict, segment_ids: set) -> list[str]:
    """What is wrong with one parsed record: its shape (src/core/stats.py
    shape_problem), its event's required fields, unknown keys (counted, not
    named: a key could be text), and string values outside the closed sets
    (only the field and length are shown)."""
    shape = shape_problem(record)
    if shape is not None:
        return [shape]
    problems = []
    event = record['event']
    if event not in REQUIRED:
        return ['an unknown event']
    missing = [k for k in REQUIRED[event] if record.get(k) is None]
    if missing:
        problems.append(f'{event} without {", ".join(missing)}')
    extra = len(set(record) - ALLOWED_KEYS)
    if extra:
        problems.append(f'{extra} unknown keys')
    for key, value in record.items():
        if key not in ALLOWED_KEYS or key == 'segment':
            continue
        for v in value if isinstance(value, list) else [value]:
            if isinstance(v, str) and not _known(key, v):
                problems.append(f'{key}: a string that is not a known value ({len(v)} chars)')
    return problems


def check_project(project_dir: str) -> tuple[list[str], dict]:
    """(problems, counts) of one project's record."""
    with open(os.path.join(project_dir, 'project.json'), encoding='utf-8') as f:
        data = json.load(f)
    segment_ids = {s.get('segment_id') or s.get('id') for s in data.get('segments', [])}
    records, skipped = read_records(events_path(project_dir))
    problems = []
    runs: dict[str, dict] = {}
    unknown_segments = 0

    for i, r in enumerate(records, 1):
        for message in record_problems(r, segment_ids):
            if len(problems) < 20:
                problems.append(f'record {i}: {message}')
        if r.get('segment') is not None and r['segment'] not in segment_ids:
            unknown_segments += 1
        info = runs.setdefault(r['run'], {'translated': 0, 'failed': 0, 'dropped': 0, 'end': None})
        if r['event'] == 'segment' and r.get('result') in info:
            info[r['result']] += 1
        elif r['event'] == 'run_end':
            info['end'] = r
    for run_id, info in runs.items():
        end = info['end']
        if end is None or end.get('source') == 'extract':
            continue
        for field in ('translated', 'failed', 'dropped'):
            if end.get(field, 0) != info[field]:
                problems.append(f'run {run_id}: run_end {field}={end.get(field, 0)}, '
                                f'segment records {info[field]}')
    if unknown_segments:
        problems.append(f'{unknown_segments} records name a segment id not in project.json')
    return problems, {'records': len(records), 'skipped': skipped, 'runs': len(runs)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--projects-dir', default='projects')
    parser.add_argument('--anonymize', action='store_true')
    args = parser.parse_args(argv)
    try:
        names = sorted(n for n in os.listdir(args.projects_dir)
                       if os.path.isfile(os.path.join(args.projects_dir, n, 'project.json')))
    except OSError as e:
        print(f'cannot list the projects directory: {type(e).__name__}')
        return 2
    failed = False
    for k, name in enumerate(names, 1):
        label = f'project-{k}' if args.anonymize else name
        project_dir = os.path.join(args.projects_dir, name)
        if not os.path.isfile(events_path(project_dir)):
            print(f'{label}: no event record')
            continue
        problems, counts = check_project(project_dir)
        verdict = 'FAIL' if problems else 'PASS'
        print(f'{label}: {verdict}  records {counts["records"]}, skipped lines '
              f'{counts["skipped"]}, runs {counts["runs"]}')
        for p in problems:
            print(f'  {p}')
        failed = failed or bool(problems)
    print('FAIL' if failed else 'PASS')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
