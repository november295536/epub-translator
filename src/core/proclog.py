"""Persistent process log: every web server and every CLI command appends
to a rotating file, besides what the terminal shows.

Location: `logs/` beside the projects directory (`<parent of projects
dir>/logs/web.log` for the web server, `…/cli.log` for CLI commands; in
normal use the repository's `logs/`, ignored by git, next to `projects/`
and `uploads/`). EPUB_TRANSLATOR_LOG_DIR, when set, replaces the directory
(the test suite points it at a temporary one).

The file gets a start line (program version: the git commit when there is
one; Python version; the command or "web server"; pid), every record of the
`src.*` loggers and of the web server's request lines (werkzeug), and the
traceback of an uncaught exception in a request, a CLI command or a thread.
Unlike the project's event record (src/core/events.py) it may contain book
text (error messages, project names in request lines): it stays local.
"""

from __future__ import annotations

import atexit
import logging
import logging.handlers
import os
import platform
import subprocess
import sys
import threading

from src import __version__

LOG_DIR_ENV = 'EPUB_TRANSLATOR_LOG_DIR'

# Rotation: at most this many bytes per file, and this many older files
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

FORMAT = '%(asctime)s %(process)d [%(levelname)s] %(name)s: %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_handler: logging.Handler | None = None
_handler_name: str | None = None
_terminal: logging.Handler | None = None
_in_command = False  # between a CLI command's setup() and command_ended()
_previous_excepthook = None
_commit: str | None = None
_end_registered = False


def log_dir_for(projects_dir: str) -> str:
    """The directory of the process log for a projects directory."""
    override = os.environ.get(LOG_DIR_ENV)
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(projects_dir)), 'logs')


def program_version() -> str:
    """The package version and the git commit (`git describe --always
    --dirty`) when the code runs from a checkout; computed once."""
    global _commit
    if _commit is None:
        try:
            result = subprocess.run(
                ['git', '-C', _REPO, 'describe', '--always', '--dirty', '--abbrev=12'],
                capture_output=True, text=True, timeout=5, check=False)
            _commit = result.stdout.strip() if result.returncode == 0 else ''
        except (OSError, subprocess.SubprocessError):
            _commit = ''
    return f'{__version__} ({_commit})' if _commit else __version__


def _thread_excepthook(args):
    """An uncaught exception in a thread goes to the log too."""
    if args.exc_type is not SystemExit:
        logging.getLogger('src.thread').error(
            "Uncaught exception in thread %s",
            getattr(args.thread, 'name', '?'),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    if _previous_excepthook is not None:
        _previous_excepthook(args)


def _web_end() -> None:
    """The end line of a web server that stops normally (atexit; a killed
    process writes none). Only into the web log: not when the file is a CLI
    command's (`translate.py review`) or was removed."""
    if _handler_name == 'web':
        logger.info("end: web server stopped | pid %d", os.getpid())


def terminal(level: int, fmt: str = '%(message)s', datefmt: str | None = None) -> None:
    """Records of `level` and above also go to stderr, as they did before the
    file handler: a CLI command WARNING with the bare message (what
    `logging.lastResort` printed while the root logger had no handler), the
    web server INFO with time and logger. Nothing when the root logger has a
    handler of someone else (an embedding program, pytest)."""
    global _terminal
    root = logging.getLogger()
    with _lock:
        if any(h is not _handler and h is not _terminal for h in root.handlers):
            return
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
        if _terminal is not None:
            root.removeHandler(_terminal)
        root.addHandler(handler)
        _terminal = handler


def setup(projects_dir: str, name: str, what: str) -> str | None:
    """Make the root logger append to `<log dir>/<name>.log` (replacing an
    earlier file of this module in the same process), make sure INFO
    records pass, write the start line naming `what` (the command, or "web
    server"). The web server's log also gets an end line at a normal exit
    (CLI commands write their own). Returns the file's path; None, after a
    warning on stderr, when the file cannot be opened (the program goes on
    without it)."""
    global _handler, _handler_name, _previous_excepthook, _end_registered, _in_command
    path = os.path.abspath(os.path.join(log_dir_for(projects_dir), f'{name}.log'))
    root = logging.getLogger()
    with _lock:
        if _handler is not None and getattr(_handler, 'baseFilename', None) == path:
            handler = _handler  # the same file again (e.g. a second app): kept
        elif _in_command and name == 'web':
            # `translate.py review`: the web server runs inside a CLI
            # command, whose start and end lines stay in its file
            path = _handler.baseFilename
        else:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                handler = logging.handlers.RotatingFileHandler(
                    path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding='utf-8')
            except OSError as e:
                print(f'無法開啟紀錄檔 {path}：{e}', file=sys.stderr)
                return None
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter(FORMAT, datefmt=DATE_FORMAT))
            if _handler is not None:
                root.removeHandler(_handler)
                _handler.close()
            root.addHandler(handler)
            _handler = handler
            _handler_name = name
        if name == 'cli':
            _in_command = True
        if root.level == logging.NOTSET or root.level > logging.INFO:
            root.setLevel(logging.INFO)
        if _previous_excepthook is None:
            _previous_excepthook = threading.excepthook
            threading.excepthook = _thread_excepthook
        if _handler_name == 'web' and not _end_registered:
            _end_registered = True
            atexit.register(_web_end)
    logger.info("start: %s | epub-translator %s | Python %s | %s | pid %d",
                what, program_version(), platform.python_version(), sys.platform, os.getpid())
    return path


def command_ended() -> None:
    """A CLI command has written its end line."""
    global _in_command
    _in_command = False


def teardown() -> None:
    """Remove the file and terminal handlers (tests)."""
    global _handler, _handler_name, _terminal
    with _lock:
        if _handler is not None:
            logging.getLogger().removeHandler(_handler)
            _handler.close()
            _handler = None
            _handler_name = None
        command_ended()
        if _terminal is not None:
            logging.getLogger().removeHandler(_terminal)
            _terminal = None
