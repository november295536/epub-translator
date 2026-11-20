"""Mutation testing over the lines changed on this branch (mutmut 3.8.1).

Run through scripts/verify/mutation.sh, on an interpreter that has mutmut
3.8.1 (Python 3.10 or later) and the requirements. argv: BASE.

Mutates only the lines added or changed versus the merge base with BASE in
FILES, in a copy of the working tree under a temporary directory (the
repository is never edited, so work can go on while it runs; the copy is
removed at the end, also on Ctrl-C). mutmut 3 runs the whole suite once to
record which tests reach which function, then runs each mutant against only
those tests, on all cores.

Three adjustments make mutmut 3 fit this repository; all live in the copy:
- mutmut assumes a src layout and strips or refuses module names starting
  with `src.`, but this repository's package is named `src`: in the copy it
  is renamed (RENAMED) and every reference to it rewritten; reports name
  `src` again. Tests load the check scripts under their module path
  (`scripts.verify.<name>`), so the scripts' mutants can be activated.
- mutmut has no "changed lines" option; its coverage hook
  (`get_covered_lines_for_file`) is answered with the changed lines.
  mutmut forks its workers, so the replacement reaches them.
- tests start subprocesses (scripts, CLI, lock holders) that run the mutated
  code with a test's temporary directory as working directory, where
  mutmut finds no configuration: a sitecustomize loads it from the copy.

Known gap: a test links to a function only when the function runs in the
test's own process. Code a test reaches only through a subprocess (the
check scripts' --help and command-line tests) is not linked to that test,
so such mutants are run without it and survive unless an in-process test
kills them (mutmut 2 ran every test of the group and killed them).

Prints every surviving, timed-out, suspicious or untested mutant with its
diff, then a TOTAL line. Exit code: 0 when every mutant was killed, 1 when
some were not, 2 when mutmut failed or generated no mutants.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from importlib.metadata import version

MUTMUT_VERSION = '3.8.1'
RENAMED = 'epubtr'
FILES = [
    'src/__init__.py', 'src/core/project.py', 'src/core/segmenter.py', 'src/core/translator.py',
    'src/core/migrate.py', 'src/epub/blocks.py', 'src/epub/extractor.py', 'src/epub/exporter.py',
    'src/glossary/glossary_manager.py', 'src/glossary/term_extractor.py',
    'src/llm/__init__.py', 'src/llm/base.py', 'src/llm/cli.py', 'src/llm/claude_cli.py',
    'src/llm/fake.py', 'src/web/tasks.py', 'src/web/server.py', 'src/web/blueprints/export.py',
    'src/web/blueprints/glossary.py', 'src/web/blueprints/projects.py',
    'src/web/blueprints/review.py', 'src/web/blueprints/search.py',
    'src/web/blueprints/translation.py', 'translate.py',
    'scripts/verify/phase0_code_segments.py', 'scripts/verify/phase1_load_projects.py',
    'scripts/verify/phase2_roundtrip.py', 'scripts/verify/phase2_migrate_copies.py',
    'scripts/verify/phase3_export_check.py',
]
MODULE_REF = re.compile(r'\bsrc\.(core|epub|web|glossary|llm)\b')
BAD = ('survived', 'timeout', 'suspicious', 'no tests')

RUNNER = '''
import json, os, sys
import mutmut.__main__ as main
with open('changed.json') as f:
    CHANGED = {os.path.abspath(p): set(lines) for p, lines in json.load(f).items()}
main.get_covered_lines_for_file = lambda filename, covered: CHANGED.get(
    os.path.abspath(filename), set())
sys.argv = ['mutmut', 'run']
main.cli()
'''

SITECUSTOMIZE = '''
import os
if os.environ.get('MUTMUT_ROOT'):
    import warnings
    _cwd = os.getcwd()
    os.chdir(os.environ['MUTMUT_ROOT'])
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            import mutmut.configuration
            mutmut.configuration.config()
    finally:
        os.chdir(_cwd)
'''


def git(*args: str) -> str:
    return subprocess.run(['git', *args], capture_output=True, text=True, check=True).stdout


def changed_lines(merge_base: str) -> dict:
    """{file: [line numbers]} of the working tree's lines added or changed
    versus the merge base."""
    out = {}
    for f in FILES:
        lines = set()
        for m in re.finditer(r'^@@ -\S+ \+(\d+)(?:,(\d+))? @@', git('diff', '-U0', merge_base, '--', f), re.M):
            start, n = int(m.group(1)), int(m.group(2) or 1)
            lines.update(range(start, start + n))
        if lines:
            out[f] = sorted(lines)
    return out


def renamed(path: str) -> str:
    return RENAMED + path[3:] if path.startswith('src/') else path


def back(text: str) -> str:
    """mutmut's names and paths with the package named `src` again."""
    return re.sub(rf'\b{RENAMED}([./])', r'src\1', text)


def prepare(copy: str, changed: dict) -> None:
    """The working tree copied to `copy`, `src` renamed, mutmut configured."""
    for f in git('ls-files', '-co', '--exclude-standard', '-z').split('\0'):
        if f and os.path.isfile(f):
            os.makedirs(os.path.join(copy, os.path.dirname(renamed(f))), exist_ok=True)
            shutil.copy2(f, os.path.join(copy, renamed(f)))
    for root, _, names in os.walk(copy):
        for name in names:
            if not name.endswith('.py'):
                continue
            path = os.path.join(root, name)
            with open(path, encoding='utf-8') as fh:
                text = fh.read()
            new = MODULE_REF.sub(rf'{RENAMED}.\1', text).replace('from src import', f'from {RENAMED} import')
            new = new.replace("ROOT, 'src')", f"ROOT, '{RENAMED}')").replace("ROOT + '/src'", f"ROOT + '/{RENAMED}'")
            if name == 'test_verify_scripts.py':
                new = new.replace('spec_from_file_location(name, path)',
                                  "spec_from_file_location(f'scripts.verify.{name}', path)")
            if new != text:
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(new)
    with open(os.path.join(copy, 'changed.json'), 'w') as fh:
        json.dump({renamed(f): lines for f, lines in changed.items()}, fh)
    only = '\n    '.join(renamed(f) for f in changed)
    with open(os.path.join(copy, 'setup.cfg'), 'w') as fh:
        fh.write(f'[mutmut]\nsource_paths={RENAMED}\n    scripts\n    translate.py\n'
                 f'only_mutate={only}\npytest_add_cli_args_test_selection=tests/\n'
                 'also_copy=tests\npytest_add_cli_args=-p no:cacheprovider\n')
    os.makedirs(os.path.join(copy, 'site'))
    with open(os.path.join(copy, 'site', 'sitecustomize.py'), 'w') as fh:
        fh.write(SITECUSTOMIZE)


def main(base: str) -> int:
    if version('mutmut') != MUTMUT_VERSION:
        print(f'mutmut {MUTMUT_VERSION} required (found {version("mutmut")}): '
              'pip install -r requirements-dev.txt', file=sys.stderr)
        return 2
    os.chdir(git('rev-parse', '--show-toplevel').strip())
    changed = changed_lines(git('merge-base', base, 'HEAD').strip())
    if not changed:
        print('no changed lines')
        return 2
    work = tempfile.mkdtemp(prefix='mutation-')
    try:
        copy = os.path.join(work, 'repo')
        prepare(copy, changed)
        os.makedirs(os.path.join(work, 'tmp'))
        env = dict(os.environ, MUTMUT_ROOT=copy, PYTHONPATH=os.path.join(copy, 'site'),
                   TMPDIR=os.path.join(work, 'tmp'), PYTHONDONTWRITEBYTECODE='1')
        print('mutating the changed lines of ' + ', '.join(changed), flush=True)
        with open(os.path.join(work, 'run.log'), 'w') as log:
            run = subprocess.run([sys.executable, '-c', RUNNER], cwd=copy, env=env,
                                 stdout=log, stderr=subprocess.STDOUT)
        if run.returncode != 0:
            with open(os.path.join(work, 'run.log'), encoding='utf-8', errors='replace') as log:
                print(back(log.read()[-4000:]))
            print('ERROR mutmut failed', file=sys.stderr)
            return 2
        mutmut = [sys.executable, '-m', 'mutmut']
        listing = subprocess.run(mutmut + ['results', '--all', 'true'], cwd=copy, env=env,
                                 capture_output=True, text=True, check=True).stdout
        statuses = dict(line.strip().rsplit(': ', 1) for line in listing.splitlines()
                        if ': ' in line)
        for status in BAD:
            for name, st in sorted(statuses.items()):
                if st == status:
                    diff = subprocess.run(mutmut + ['show', name], cwd=copy, env=env,
                                          capture_output=True, text=True).stdout
                    print(f'{status} {back(name)}')
                    print(''.join('    ' + back(line) + '\n' for line in diff.splitlines()
                                  if not line.startswith('#')))
        counts = Counter(statuses.values())
        print(f"TOTAL mutants {len(statuses)}  killed {counts['killed']}  "
              f"survived {counts['survived']}  timeout {counts['timeout']}  "
              f"suspicious {counts['suspicious']}  untested {counts['no tests']}")
        if not statuses:
            print('ERROR no mutants were generated')
            return 2
        return 1 if any(counts[s] for s in BAD) else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'main'))
