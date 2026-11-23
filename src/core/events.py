"""Per-project event record: one JSON object per line in
`<project>/log/events.jsonl`, one line per model call and per run event.

The record holds ids, codes, counts, character counts, settings and
durations only: never source text, a translation, a prompt, a reply, a
glossary term or an error message (an error is recorded by its class name).
It is the file that can be handed to a developer.

Appends are safe across threads and processes: each line is written with
one os.write on a file opened O_APPEND (POSIX appends of one write do not
interleave on a local file system), under a lock per path inside the
process. A record that cannot be written is logged and dropped: recording
never fails or blocks a translation beyond the write itself.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import secrets
import threading

from src.llm import (
    AuthError, BackendUnavailableError, CallTimeoutError, CancelledError, LLMError,
    RateLimitError, RefusalError, TransientError,
)

logger = logging.getLogger(__name__)

EVENTS_FILE = 'events.jsonl'

# Record format; a reader skips lines of a later version it does not know
EVENTS_VERSION = 1

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def events_path(project_dir: str) -> str:
    """The project's event record."""
    return os.path.join(project_dir, 'log', EVENTS_FILE)


def set_aside(project_dir: str) -> str | None:
    """Move the project's event record to `events-<UTC time>.jsonl` beside
    it (kept, not deleted), so the next run starts a fresh one: for a
    project replaced by another book. Returns the new path; None when there
    was no record. Raises OSError when it cannot be moved."""
    path = events_path(project_dir)
    if not os.path.exists(path):
        return None
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = os.path.join(os.path.dirname(path), f'events-{stamp}.jsonl')
    with _lock_for(path):
        os.rename(path, target)
    return target


def _lock_for(path: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(path, threading.Lock())


def now_stamp() -> str:
    """UTC time with milliseconds, e.g. 2026-11-23T10:15:02.123Z."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now.microsecond // 1000:03d}Z'


def new_run_id() -> str:
    """A run id: UTC start time and random hex (no machine identifier)."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return f'{stamp}-{secrets.token_hex(3)}'


def append_record(path: str, record: dict) -> bool:
    """Append one record as a line. False (logged) when it could not be
    written; never raises."""
    try:
        line = (json.dumps(record, ensure_ascii=False, separators=(',', ':'),
                           allow_nan=False) + '\n').encode('utf-8')
        with _lock_for(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                size = os.fstat(fd).st_size
                if size and os.pread(fd, 1, size - 1) != b'\n':
                    # The last line was cut off (a crash mid-write): end it,
                    # so that only it is unreadable, not this one too
                    line = b'\n' + line
                os.write(fd, line)
            finally:
                os.close(fd)
        return True
    except Exception as e:  # noqa: BLE001 - a record never stops a run
        logger.warning("無法寫入事件紀錄 (%s): %s", type(e).__name__, e)
        return False


# The outcome class of a call that raised, by exception class (first match)
_ERROR_OUTCOMES = (
    (CancelledError, 'cancelled'),
    (KeyboardInterrupt, 'cancelled'),  # Ctrl-C ends the call in flight
    (RateLimitError, 'rate_limit'),
    (AuthError, 'auth'),
    (BackendUnavailableError, 'backend_unavailable'),
    (RefusalError, 'refusal'),
    (CallTimeoutError, 'timeout'),
    (TransientError, 'transient'),
    (LLMError, 'error'),
)


def error_outcome(error: BaseException) -> str:
    """The outcome class of a call that raised `error`."""
    for cls, name in _ERROR_OUTCOMES:
        if isinstance(error, cls):
            return name
    return 'unexpected'


class RunEvents:
    """The records of one run (a CLI or web translation run, a single
    retranslation, a term extraction) of one project: each line gets the
    time, the run id and the run's source."""

    def __init__(self, project_dir: str, source: str, run_id: str | None = None):
        self.path = events_path(project_dir)
        self.source = source
        self.run_id = run_id or new_run_id()

    def emit(self, event: str, **fields) -> bool:
        """Append one record; fields that are None are left out."""
        record = {'v': EVENTS_VERSION, 'ts': now_stamp(), 'run': self.run_id,
                  'source': self.source, 'event': event}
        record.update((k, v) for k, v in fields.items() if v is not None)
        return append_record(self.path, record)

    def call(self, *, kind: str, backend: str, model: str, effort: str | None,
             timeout: float, seconds: float, outcome: str, segment: str | None = None,
             attempt: int | None = None, usage=None, issues: list | None = None,
             warnings: list | None = None, error: BaseException | None = None,
             chars_in: int | None = None, chars_out: int | None = None) -> bool:
        """One model call. `issues`: the blocking issue codes of a rejected
        reply; `warnings`: the warning codes of an accepted one; `error`:
        only its class name is recorded."""
        return self.emit(
            'call', kind=kind, segment=segment, attempt=attempt, backend=backend,
            model=model, effort=effort, timeout=timeout, seconds=round(seconds, 3),
            tokens_in=None if usage is None else usage.input_tokens,
            tokens_out=None if usage is None else usage.output_tokens,
            outcome=outcome, issues=issues or None, warnings=warnings or None,
            error=None if error is None else type(error).__name__,
            chars_in=chars_in, chars_out=chars_out)


def settings_fields(settings) -> dict:
    """The run_start fields of a project's CallSettings."""
    import src.llm as llm
    return {'backend': settings.backend or llm.default_backend().name,
            'model': settings.model, 'effort': settings.effort,
            'timeout': settings.timeout}


def segment_result(events: RunEvents | None, segment_id: str, outcome,
                   result: str | None = None, reason: str | None = None) -> None:
    """The final result of one segment in a run: `translated` / `failed`
    from its outcome, or `result` (`dropped`, with `reason`) when the outcome
    was not applied. Codes only: blocking issue codes of a failure, warning
    codes of a translation."""
    if events is None:
        return
    fields = {}
    if outcome is not None:
        failed = outcome.status.value == 'failed'
        fields = {'model': outcome.model, 'calls': outcome.calls,
                  'split': outcome.split or None,
                  'issues' if failed else 'warnings':
                      [i.code for i in outcome.issues if i.blocking == failed] or None}
        if result is None:
            result = 'failed' if failed else 'translated'
    events.emit('segment', segment=segment_id, result=result, reason=reason, **fields)
