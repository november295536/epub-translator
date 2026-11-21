"""Every Python source of the repository compiles without a warning.

A compile-time warning (e.g. an invalid escape sequence) is printed only
when Python compiles the file, i.e. on the first run after it changed and
not once its .pyc exists; compiling in memory here makes it deterministic
and writes no bytecode."""

import os
import warnings

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sources():
    paths = [os.path.join(ROOT, 'translate.py')]
    for top in ('src', 'scripts', 'tests'):
        for folder, dirs, files in os.walk(os.path.join(ROOT, top)):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            paths += [os.path.join(folder, f) for f in files if f.endswith('.py')]
    return sorted(paths)


def test_sources_found():
    names = {os.path.relpath(p, ROOT) for p in _sources()}
    assert 'translate.py' in names and len(names) > 50
    assert os.path.join('tests', 'test_sources_compile.py') in names


@pytest.mark.parametrize('path', _sources(), ids=lambda p: os.path.relpath(p, ROOT))
def test_compiles_without_warnings(path):
    with open(path, encoding='utf-8') as f:
        source = f.read()
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        compile(source, path, 'exec')  # a warning raises SyntaxError here
