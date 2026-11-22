"""An interpreter older than src.MIN_PYTHON stops with one message, before
any other project module is imported (the version is injected)."""

import ast
import os
import subprocess
import sys

import pytest

from src import MIN_PYTHON, require_python

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = require_python.__module__  # `src`, renamed in the mutation runner's copy
MESSAGE = '需要 Python 3.12 以上版本（目前是 3.11.9）；請以 python3.12 重建虛擬環境（見 README.md「安裝」）。'


@pytest.mark.parametrize('version', [(3, 11, 9), (3, 11, 9, 'final', 0)])
def test_older_interpreter_stops_with_the_message(version):
    with pytest.raises(SystemExit) as e:
        require_python(version)
    assert e.value.code == MESSAGE


@pytest.mark.parametrize('version', [(3, 12), (3, 12, 0), (3, 14, 7), (4, 0, 0)])
def test_supported_interpreter_passes(version):
    assert require_python(version) is None


def test_running_interpreter_passes():
    assert MIN_PYTHON == (3, 12)
    assert require_python() is None


@pytest.mark.parametrize('entry, requested', [
    ("import runpy; sys.argv = ['translate.py', '--help']; runpy.run_path('translate.py', run_name='__main__')",
     [PACKAGE]),
    # the package of a requested module is imported first: src fails there
    ('from src.web.server import create_app; create_app()',
     [f'{PACKAGE}.web.server', f'{PACKAGE}.web', PACKAGE]),
])
def test_entry_points_stop_first(entry, requested):
    # The version is replaced before the entry point runs; the message is
    # all that is printed. Every import started is recorded (the audit event
    # is raised when an import starts): before the first project import only
    # the standard library (a third-party import there would fail first on an
    # old interpreter), and `src` is the last project import: nothing after it runs
    code = ("import sys; sys.version_info = (3, 11, 9, 'final', 0)\n"
            "sys.addaudithook(lambda event, args: event == 'import' and print(args[0]))\n" + entry)
    proc = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 1
    assert proc.stderr == MESSAGE + '\n'
    started = proc.stdout.split()
    first = next(i for i, name in enumerate(started) if name.split('.')[0] == PACKAGE)
    assert [n for n in started[:first] if n.split('.')[0] not in sys.stdlib_module_names] == []
    assert [n for n in started if n.split('.')[0] == PACKAGE] == requested


@pytest.mark.parametrize('path', [os.path.join(ROOT, 'translate.py'),
                                  sys.modules[PACKAGE].__file__])  # src/__init__.py
def test_readable_by_an_old_interpreter(path):
    # the message can only appear if an old interpreter can compile these
    with open(path, encoding='utf-8') as f:
        ast.parse(f.read(), path, feature_version=(3, 9))


@pytest.mark.skipif(subprocess.run(['git', '-C', ROOT, 'rev-parse'], capture_output=True).returncode != 0,
                    reason='needs a git checkout (not in the mutation runner\'s copy)')
@pytest.mark.parametrize('version, message', [
    ((3, 11, 9), MESSAGE),
    # a supported interpreter goes on to the next check (mutmut is hidden here)
    ((3, 12, 0), 'mutmut not found: pip install -r requirements.txt -r requirements-dev.txt\n'
                 'in the project environment, and activate it'),
])
def test_mutation_script_checks_the_interpreter_first(tmp_path, version, message):
    # `python3` on PATH: this interpreter reporting `version`, without mutmut
    fake = tmp_path / 'python3'
    fake.write_text(f'#!{sys.executable}\n'
                    'import os, sys\n'
                    f'sys.version_info = {version!r}\n'
                    'sys.path.insert(0, os.getcwd())\n'
                    "sys.modules['mutmut'] = None\n"
                    "exec(compile(sys.argv[2], '-c', 'exec'))\n")
    fake.chmod(0o755)
    proc = subprocess.run(['bash', os.path.join(ROOT, 'scripts', 'verify', 'mutation.sh'), 'main'],
                          cwd=ROOT, capture_output=True, text=True, timeout=60,
                          env=dict(os.environ, PATH=f'{tmp_path}:{os.environ["PATH"]}'))
    assert (proc.returncode, proc.stdout, proc.stderr) == (2, '', message + '\n')
