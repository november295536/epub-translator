#!/usr/bin/env python3
"""Run records check: the event records of real projects hold no book text
and add up.

For each directory of --projects-dir that contains a project.json and a
log/events.jsonl, reads the record (src/core/stats.read_records, the reader
of `translate.py stats`) and checks every record:

  - keys: only those the record format defines (ALLOWED_KEYS);
  - values: numbers, booleans, lists of issue codes, and strings that are
    enum values (event, source, kind, outcome, result, reason, state), an
    exception class name, a backend, model or effort the project format
    knows, a record time, a run id, or a segment id OF THIS PROJECT
    (project.json); any other string FAILs (it could be text);
  - no word of 7 or more letters from the project's source text appears in
    any string value other than a segment id (segment ids are built from
    the EPUB's chapter ids, see known-issues.md; they are listed apart);
  - per run with a run_end: its translated/failed/dropped counts equal the
    number of its segment records of each result.

Prints per project: records, skipped lines, runs, and the result. Lines that
cannot be parsed are counted, not failed (a crash mid-write).

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
from src.core.stats import read_records  # noqa: E402
from src.llm import BACKENDS, KNOWN_LEVELS  # noqa: E402

ALLOWED_KEYS = frozenset({
    'v', 'ts', 'run', 'source', 'event', 'workers', 'segments', 'mode', 'backend', 'model',
    'effort', 'timeout', 'kind', 'segment', 'attempt', 'seconds', 'tokens_in', 'tokens_out',
    'outcome', 'issues', 'warnings', 'error', 'chars_in', 'chars_out', 'result', 'reason',
    'calls', 'split', 'state', 'translated', 'failed', 'dropped', 'completed', 'resume_in',
    'terms', 'added',
})
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
}
CODE = re.compile(r'^[a-z][a-z0-9_]{1,40}$')          # an issue code
CLASS = re.compile(r'^[A-Z][A-Za-z0-9_]{1,60}$')       # an exception class name
MODEL = re.compile(r'^[a-z0-9][a-z0-9.\-]{1,40}$')      # a model alias
TS = re.compile(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$')
RUN = re.compile(r'^\d{8}T\d{6}Z-[0-9a-f]{6}$')
WORD = re.compile(r'[A-Za-z]{7,}')


def check_project(project_dir: str) -> tuple[list[str], dict]:
    """(problems, counts) of one project's record."""
    with open(os.path.join(project_dir, 'project.json'), encoding='utf-8') as f:
        data = json.load(f)
    segment_ids = {s.get('segment_id') or s.get('id') for s in data.get('segments', [])}
    words = set()
    for s in data.get('segments', []):
        words.update(w.lower() for w in WORD.findall(s.get('text', '')))
    records, skipped = read_records(events_path(project_dir))
    problems = []
    runs: dict[str, dict] = {}
    unknown_segments = 0

    def bad(i, message):
        if len(problems) < 20:
            problems.append(f'record {i}: {message}')

    for i, r in enumerate(records, 1):
        extra = set(r) - ALLOWED_KEYS
        if extra:
            bad(i, f'unknown keys {sorted(extra)}')
        for key, value in r.items():
            if key == 'segment':
                if value not in segment_ids:
                    unknown_segments += 1
                continue
            values = value if isinstance(value, list) else [value]
            for v in values:
                if not isinstance(v, str):
                    if not isinstance(v, (int, float, bool)) and v is not None:
                        bad(i, f'{key}: unexpected type {type(v).__name__}')
                    continue
                ok = (v in ENUMS[key] if key in ENUMS
                      else key in ('issues', 'warnings') and bool(CODE.match(v))
                      or key == 'error' and bool(CLASS.match(v))
                      or key == 'backend' and (v in BACKENDS or v == 'fake')
                      or key == 'model' and bool(MODEL.match(v))
                      or key == 'ts' and bool(TS.match(v))
                      or key == 'run' and bool(RUN.match(v)))
                if not ok:
                    bad(i, f'{key}: a string that is not a known value ({len(v)} chars)')
                elif v.lower() in words and key not in ('model', 'backend'):
                    bad(i, f'{key}: a word of the source text')
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
