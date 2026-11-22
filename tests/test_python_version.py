"""An interpreter older than src.MIN_PYTHON stops with one message, before
any other project module is imported (the version is injected)."""

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


@pytest.mark.parametrize('version', [(3, 12, 0), (3, 14, 7), (4, 0, 0)])
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
    # all that is printed, and `src` is the last project import started (the
    # audit event is raised when an import starts): nothing after it runs
    code = ("import sys; sys.version_info = (3, 11, 9, 'final', 0)\n"
            "sys.addaudithook(lambda event, args: event == 'import' and "
            f"args[0].split('.')[0] == {PACKAGE!r} and print(args[0]))\n" + entry)
    proc = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 1
    assert proc.stderr == MESSAGE + '\n'
    assert proc.stdout.split() == requested
