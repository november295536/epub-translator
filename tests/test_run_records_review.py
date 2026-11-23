"""Run records, review round 1: each test reproduces one finding (it failed
before the fix). All text is invented."""

import os
import subprocess
import sys

from src.core import proclog
from tests.conftest import _REPO_ROOT
from tests.test_run_records import _make_project


def _run_cli(tmp_path, prelude, args):
    """translate.py in a fresh process (no pytest handlers on the root
    logger), with `prelude` run first; returns (stderr, process log dir)."""
    code = ('import sys, logging; sys.path.insert(0, sys.argv[1])\n'
            'import translate\n' + prelude +
            'translate.cli(sys.argv[2:])\n')
    logs = tmp_path / 'logs'
    env = dict(os.environ, **{proclog.LOG_DIR_ENV: str(logs)})
    result = subprocess.run([sys.executable, '-c', code, _REPO_ROOT, *args], env=env,
                            capture_output=True, text=True, timeout=60)
    return result, logs


class TestTerminalOutput:
    def test_cli_warning_on_stderr(self, tmp_path):
        """A src.* warning during a CLI command reaches the terminal (as
        logging.lastResort printed it before the file handler); INFO does not."""
        path = _make_project(tmp_path / 'projects')
        prelude = ('find = translate._find_project\n'
                   'def noisy(p):\n'
                   '    logging.getLogger("src.x").info("INFO-MARK")\n'
                   '    logging.getLogger("src.x").warning("WARN-MARK")\n'
                   '    return find(p)\n'
                   'translate._find_project = noisy\n')
        result, logs = _run_cli(tmp_path, prelude, ['status', '-p', path])
        assert result.returncode == 0, result.stderr
        assert 'WARN-MARK' in result.stderr and 'INFO-MARK' not in result.stderr
        log = (logs / 'cli.log').read_text(encoding='utf-8')
        assert 'WARN-MARK' in log and 'INFO-MARK' in log

    def test_review_keeps_terminal_and_cli_log(self, tmp_path):
        """`translate.py review`: the server's request lines reach the
        terminal as before, the command's start and end lines stay in
        cli.log, and no web.log (nor web end line) is written."""
        path = _make_project(tmp_path / 'projects')
        prelude = ('import flask, webbrowser\n'
                   'webbrowser.open = lambda url: None\n'
                   'def run(self, **kw):\n'
                   '    logging.getLogger("werkzeug").info("REQ-MARK")\n'
                   'flask.Flask.run = run\n')
        result, logs = _run_cli(tmp_path, prelude, ['review', '-p', path])
        assert result.returncode == 0, result.stderr
        assert 'REQ-MARK' in result.stderr
        log = (logs / 'cli.log').read_text(encoding='utf-8')
        assert 'start: cli review' in log and 'REQ-MARK' in log
        assert log.splitlines()[-1].split(': ', 1)[-1].startswith('end: exit 0 after')
        assert 'end: web server stopped' not in log
        assert not (logs / 'web.log').exists()

    def test_log_path_absolute(self, tmp_path, monkeypatch):
        monkeypatch.setenv(proclog.LOG_DIR_ENV, 'relative-logs')
        monkeypatch.chdir(tmp_path)
        proclog.teardown()
        try:
            assert proclog.setup('projects', 'cli', 'cli status') == \
                str(tmp_path / 'relative-logs' / 'cli.log')
        finally:
            proclog.teardown()
