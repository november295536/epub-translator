"""Every Python source of the repository compiles without a warning.

A compile-time warning (e.g. an invalid escape sequence) is printed only
when Python compiles the file, i.e. on the first run after it changed and
not once its .pyc exists; compiling in memory here makes it deterministic
and writes no bytecode."""

import os
import warnings

import pytest

import src.llm as llm  # the package, found as the mutation runner renames it

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(llm.__file__)))


def _skipped(folder, name):
    return (name.startswith('.') or name == '__pycache__'
            or os.path.exists(os.path.join(folder, name, 'pyvenv.cfg')))  # a virtualenv


def _sources():
    """Every .py file under the repository root and the package (the same
    folder in a checkout), without hidden folders, bytecode and virtualenvs."""
    found = set()
    for top in (ROOT, PACKAGE):
        for folder, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if not _skipped(folder, d)]
            found.update(os.path.join(folder, f) for f in files if f.endswith('.py'))
    return sorted(found)


def test_known_sources_found():
    sources = _sources()
    for path in (os.path.join(ROOT, 'translate.py'), os.path.abspath(__file__),
                 os.path.join(ROOT, 'scripts', 'verify', 'mutation.py'),
                 os.path.join(PACKAGE, 'llm', 'copilot_cli.py'),
                 os.path.join(PACKAGE, 'glossary', 'glossary_manager.py')):
        assert path in sources, path
    experiment = os.path.join(ROOT, 'plan', 'experiments', 'migrate_dryrun.py')
    if os.path.isdir(os.path.join(ROOT, 'plan')):  # not in the mutation runner's copy
        assert experiment in sources


@pytest.mark.parametrize('path', _sources(), ids=lambda p: os.path.relpath(p, ROOT))
def test_compiles_without_warnings(path):
    with open(path, encoding='utf-8') as f:
        source = f.read()
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        compile(source, path, 'exec')  # a warning raises SyntaxError here
