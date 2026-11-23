"""EPUB translator."""
# Keep this file parseable by Python 3.9, so an old interpreter reaches the version message.

import sys

__version__ = "2.0.0"

# Oldest supported interpreter. Checked when the package is imported, i.e.
# before any module that needs a newer Python is compiled: translate.py and
# the Flask app factory both import it first.
MIN_PYTHON = (3, 12)


def require_python(version=None) -> None:
    """Exit with one message when `version` (default: the running
    interpreter's) is older than MIN_PYTHON."""
    version = tuple(sys.version_info if version is None else version)
    if version < MIN_PYTHON:  # (3, 12, 0, ...) > (3, 12)
        need = '.'.join(map(str, MIN_PYTHON))
        found = '.'.join(map(str, version[:3]))
        raise SystemExit(f'需要 Python {need} 以上版本（目前是 {found}）；'
                         f'請以 python{need} 重建虛擬環境（見 README.md「安裝」）。')


# Called only when it will stop: an unconditional call would run mutmut's
# trampoline on every import, so in its forced-fail run every child process
# importing src would fail (and a test could leave that child unreaped)
if sys.version_info < MIN_PYTHON:
    require_python()
