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
    process writes none)."""
    logger.info("end: web server stopped | pid %d", os.getpid())


def setup(projects_dir: str, name: str, what: str) -> str | None:
    """Make the root logger append to `<log dir>/<name>.log` (replacing an
    earlier file of this module in the same process), make sure INFO
    records pass, write the start line naming `what` (the command, or "web
    server"). The web server's log also gets an end line at a normal exit
    (CLI commands write their own). Returns the file's path; None, after a
    warning on stderr, when the file cannot be opened (the program goes on
    without it)."""
    global _handler, _previous_excepthook, _end_registered
    path = os.path.join(log_dir_for(projects_dir), f'{name}.log')
    root = logging.getLogger()
    with _lock:
        if _handler is not None and getattr(_handler, 'baseFilename', None) == path:
            handler = _handler  # the same file again (e.g. a second app): kept
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
        if root.level == logging.NOTSET or root.level > logging.INFO:
            root.setLevel(logging.INFO)
        if _previous_excepthook is None:
            _previous_excepthook = threading.excepthook
            threading.excepthook = _thread_excepthook
        if name == 'web' and not _end_registered:
            _end_registered = True
            atexit.register(_web_end)
    logger.info("start: %s | epub-translator %s | Python %s | %s | pid %d",
                what, program_version(), platform.python_version(), sys.platform, os.getpid())
    return path


def teardown() -> None:
    """Remove the file handler (tests)."""
    global _handler
    with _lock:
        if _handler is not None:
            logging.getLogger().removeHandler(_handler)
            _handler.close()
            _handler = None
