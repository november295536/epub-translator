"""Run-level control shared by the web task and both CLI modes: when a run
paused by a run-level error goes on by itself, and when results are saved."""

from __future__ import annotations

import math
import time

from src.llm import RateLimitError

# A rate limit without a usable `retry_after`: the run resumes after this long
RATE_LIMIT_WAIT_SECONDS = 300
# A backend's `retry_after` is kept within these bounds (seconds)
RETRY_AFTER_BOUNDS = (1, 3600)

# Results are saved at most this often, or after this many results,
# whichever comes first; and always when a run pauses, stops, ends or
# fails. A crash loses at most the results of the last window.
SAVE_INTERVAL_SECONDS = 10
SAVE_EVERY_RESULTS = 20


def resume_delay(error: Exception) -> float | None:
    """Seconds after which a run paused by `error` goes on by itself: a rate
    limit's `retry_after` when it is a finite positive number (an int or a
    float, not a bool), kept within RETRY_AFTER_BOUNDS; otherwise
    RATE_LIMIT_WAIT_SECONDS. Never raises. None for any other error (an
    expired login, a backend that cannot be started): the run waits for
    the user."""
    if not isinstance(error, RateLimitError):
        return None
    value = error.retry_after
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and (isinstance(value, int) or math.isfinite(value)) and value > 0):
        low, high = RETRY_AFTER_BOUNDS
        return float(min(max(value, low), high))  # clamped first: a huge int fits
    return float(RATE_LIMIT_WAIT_SECONDS)


class DebouncedSaver:
    """Saves a project's results in windows (single writer: the one thread
    that applies results calls it).

    `add(item)` records one result already applied to the manager in
    memory; `maybe_save()` saves when SAVE_EVERY_RESULTS are waiting or
    SAVE_INTERVAL_SECONDS have passed since the last save (or since the
    saver was made); `flush()` saves whatever is waiting. After each save,
    under the project mutex, `after_save(items)` gets the items it saved
    (their failure diagnostics are written only once they are on disk). A
    save that raises keeps the items waiting (`pending`). `clock` is
    injectable (seconds, monotonic)."""

    def __init__(self, pm, after_save=None, clock=time.monotonic,
                 interval: float = SAVE_INTERVAL_SECONDS, every: int = SAVE_EVERY_RESULTS):
        self.pm = pm
        self.after_save = after_save
        self.clock = clock
        self.interval = interval
        self.every = every
        self.pending: list = []
        self.saves = 0
        self._last = clock()

    def add(self, item) -> None:
        self.pending.append(item)

    def maybe_save(self) -> bool:
        """Save if the window is full or over. True when it saved."""
        if self.pending and (len(self.pending) >= self.every
                             or self.clock() - self._last >= self.interval):
            return self.flush()
        return False

    def flush(self, force: bool = False) -> bool:
        """Save what is waiting (with `force`, also when nothing is: after
        an interruption, a change may have been applied without being
        added). True when it saved."""
        # Decided under the mutex: the applier adds a result under it, so a
        # result applied in memory is also in `pending` here
        with self.pm.mutex:
            if not self.pending and not force:
                return False
            self.pm.save()
            items, self.pending = self.pending, []
            self.saves += 1
            self._last = self.clock()
            if self.after_save is not None:
                self.after_save(items)
        return True
