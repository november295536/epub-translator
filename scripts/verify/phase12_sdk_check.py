#!/usr/bin/env python3
"""Phase 12 check: the Copilot SDK backend (copilot-sdk) against the real runtime.

Makes REAL model calls (they count against the Copilot quota); run it only
on purpose. Every prompt is invented (no book text). All calls go through
the product's own backend class (`CopilotSDK().complete`, so its session
options, abort and delete); the script only watches: it wraps the backend's
SDK client so it can listen to each session's events and see which
sessions are created and deleted.

Steps (default models: --model claude-haiku-4.5, 0.33x, takes no effort
level; --effort-model claude-haiku-5.5, 1x):
  1. refusals without a model call: an unknown model and an effort level for
     a model that takes none, each BackendUnavailableError, no usage event;
  2. three concurrent short translations (they overlap in time);
  3. the runtime process is killed between calls; the next call (the
     translation of an invented paragraph with the translator's real text
     prompt) succeeds on a new runtime;
  4. one call with --level on --effort-model: the model call reports that
     level (`assistant.usage` reasoningEffort);
  5. a long reply cancelled 1 s into the model call (CancelledError) and one
     timed out after --timeout-s seconds (TransientError, CallTimeoutError): each session went
     idle as aborted with no `assistant.usage` event (nothing reported
     billed), and the call returned promptly;
  6. the session of every request is deleted (no
     `~/.copilot/session-state/<id>/` left for any session the script saw);
  7. after the client is stopped (as at interpreter exit) no runtime
     process is left.
Per call it prints the step, wall time, tokens and the cost multiplier the
SDK reports (`assistant.usage` cost, premium requests). Also printed, for
information: the cost reported in total, and whether the runtime's own logs
of this run hold the prompt text. It prints no account data (login, e-mail, token, home path), no reply
text and no session ids; it writes no file (the runtime keeps its own logs).

Usage:
    python3 scripts/verify/phase12_sdk_check.py [--model claude-haiku-4.5]
        [--effort-model claude-haiku-5.5] [--level low] [--timeout-s 6]

Exit code: 0 when every check passed, 1 otherwise; 2 for invalid options.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

from src.core import validation  # noqa: E402
from src.core.project import effort_levels  # noqa: E402
from src.core.segmenter import SegmentType  # noqa: E402
from src.core.translator import TranslationEngine  # noqa: E402
from src.llm import (  # noqa: E402
    BackendUnavailableError, CancelledError, LLMRequest, Message, TransientError,
)
from src.llm import copilot_sdk  # noqa: E402

# Invented: no book text
PARAGRAPH = ('The ferry left the harbour at dawn, and the baker on the pier waved a loaf '
             'of warm bread at it as if the boat could stop for breakfast.')
SENTENCES = ['The red kite flew over the quiet field.',
             'A small boat waited at the end of the pier.',
             'Snow covered the narrow mountain road.']
LONG = ('Write a 700-word story in Traditional Chinese about a lighthouse keeper who '
        'paints every door of the village blue.')
MARKER = 'lighthouse keeper who paints every door'
SESSION_STATE = os.path.expanduser('~/.copilot/session-state')
RUNTIME_LOGS = os.path.expanduser('~/.copilot/logs/process-*.log')
HAN = re.compile(r'[一-鿿]')
# Seconds to wait for the runtime's cleanup of an aborted call
SETTLE_SECONDS = 15
# Timeout of every call but the timed-out one (the cancelled one must not end by itself first)
CALL_TIMEOUT = 120
# Every fixed wait of the script (tests replace it)
_pause = time.sleep


class Watch:
    """Events per session (time, type, data) and created/deleted sessions."""

    def __init__(self):
        self.created, self.deleted = [], set()
        self.events = defaultdict(list)
        self.changed = threading.Condition()

    def install(self):
        real = copilot_sdk._new_client
        watch = self

        class Watched:
            def __init__(self):
                self._client = real()

            def __getattr__(self, name):
                return getattr(self._client, name)

            async def create_session(self, session_id, **options):
                with watch.changed:
                    watch.created.append(session_id)
                    watch.changed.notify_all()
                session = await self._client.create_session(session_id=session_id, **options)
                session.on(lambda event: watch.record(session_id, event))
                return session

            async def delete_session(self, session_id):
                # Recorded when asked: deleting a session whose creation
                # failed can raise (its directory is checked in step 6)
                with watch.changed:
                    watch.deleted.add(session_id)
                    watch.changed.notify_all()
                await self._client.delete_session(session_id)

        copilot_sdk._new_client = Watched
        return self

    def record(self, session_id, event):
        with self.changed:
            self.events[session_id].append((time.monotonic(), event.type.value, event.data))
            self.changed.notify_all()

    def wait(self, condition, timeout) -> bool:
        """Wait until condition() holds (at most `timeout` seconds)."""
        with self.changed:
            return self.changed.wait_for(condition, timeout)

    def of(self, session_id, kind):
        """The (time, data) of the session's events of this kind ([] for None)."""
        with self.changed:
            return [(t, d) for t, k, d in self.events[session_id] if k == kind]

    def first_since(self, n):
        """The first session created after the first n, or None."""
        with self.changed:
            return self.created[n] if len(self.created) > n else None


def children() -> list:
    """The runtime processes this script started (`copilot --headless ...`)."""
    out = subprocess.run(['pgrep', '-P', str(os.getpid()), '-f', 'copilot.* --headless'],
                         capture_output=True).stdout
    return [int(p) for p in out.split()]


def until(condition, seconds) -> bool:
    """Whether condition() holds within `seconds` (polled every 0.1 s)."""
    for _ in range(round(seconds * 10)):
        if condition():
            return True
        _pause(0.1)
    return condition()


def children_gone() -> bool:
    """Whether the runtime processes are gone (within 5 s)."""
    return until(lambda: not children(), 5)


def folder(sid) -> str:
    return os.path.join(SESSION_STATE, str(sid))


def request(prompt, model, effort=None, timeout=CALL_TIMEOUT):
    return LLMRequest(system='', messages=[Message('user', prompt)], model=model,
                      timeout=timeout, effort=effort)


def call(backend, req, cancel=None):
    start = time.monotonic()
    try:
        result = {'ok': True, 'response': backend.complete(req, cancel or threading.Event())}
    except Exception as e:  # noqa: BLE001 - counted and named, never printed in full
        result = {'ok': False, 'error': e}
    end = time.monotonic()
    return {**result, 'start': start, 'end': end, 'wall': end - start}


def costs(watch, session_id):
    usages = [d for _, d in watch.of(session_id, 'assistant.usage')]
    return ([u.cost for u in usages], [u.reasoning_effort for u in usages],
            sum(u.input_tokens or 0 for u in usages), sum(u.output_tokens or 0 for u in usages))


class Checks:
    def __init__(self):
        self.failed = []

    def check(self, name, ok, detail=''):
        print(f'  {"ok  " if ok else "FAIL"} {name}{"  " + detail if detail else ""}', flush=True)
        if not ok:
            self.failed.append(name)


def describe(watch, label, sid, result):
    cost, levels, tokens_in, tokens_out = costs(watch, sid)
    outcome = 'reply' if result['ok'] else type(result['error']).__name__
    print(f'  {label:<12} {outcome:<24} wall {result["wall"]:6.2f} s  in {tokens_in:5d}  '
          f'out {tokens_out:5d}  cost {cost}  effort {levels}', flush=True)


def aborted_turn(watch, checks, backend, label, model, *, timeout, cancel_after=None):
    """One long reply stopped by cancel (cancel_after seconds into the
    model call) or by the call's timeout."""
    n = len(watch.created)
    cancel = threading.Event()
    cancelled_at = []
    if cancel_after is not None:
        def canceller():
            watch.wait(lambda: watch.of(watch.first_since(n), 'model.call_start'), SETTLE_SECONDS)
            _pause(cancel_after)
            cancelled_at.append(time.monotonic())
            cancel.set()
        threading.Thread(target=canceller).start()
    result = call(backend, request(LONG, model, timeout=timeout), cancel)
    sid = watch.first_since(n)
    # Once the folder is gone the session can report nothing more
    deleted = until(lambda: not os.path.exists(folder(sid)), SETTLE_SECONDS)
    describe(watch, label, sid, result)
    expected = CancelledError if cancel_after is not None else TransientError
    checks.check(f'{label}: {expected.__name__}',
                 not result['ok'] and isinstance(result['error'], expected))
    checks.check(f'{label}: the model call had started', bool(watch.of(sid, 'model.call_start')))
    # When the call returned: after the cancel, or after the timeout
    stop = cancelled_at[0] if cancelled_at else result['start'] + timeout
    latency = result['end'] - stop
    checks.check(f'{label}: returned within 1 s', 0 <= latency < 1, f'{latency:.2f} s')
    idles = [d for _, d in watch.of(sid, 'session.idle')]
    checks.check(f'{label}: session went idle as aborted', any(d.aborted for d in idles),
                 f'idle events {len(idles)}')
    checks.check(f'{label}: no assistant.usage (nothing reported billed)',
                 sid is not None and not watch.of(sid, 'assistant.usage'))
    checks.check(f'{label}: session deleted', sid is not None and deleted)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--model', default='claude-haiku-4.5',
                        help='Model of every call but the effort one (default %(default)s)')
    parser.add_argument('--effort-model', default='claude-haiku-5.5',
                        help='Model of the effort call (default %(default)s)')
    parser.add_argument('--level', default='low', help='Effort level of that call (default %(default)s)')
    parser.add_argument('--timeout-s', type=float, default=6,
                        help='Timeout of the timed-out call (default %(default)s s; the model call '
                             'must have started by then)')
    args = parser.parse_args(argv)
    if args.level not in effort_levels('copilot-sdk', args.effort_model):
        parser.error(f'{args.effort_model} does not take {args.level}')
    if effort_levels('copilot-sdk', args.model):
        parser.error(f'--model must take no effort level (step 1 uses it); {args.model} takes some')

    watch = Watch().install()
    checks = Checks()
    backend = copilot_sdk.CopilotSDK()
    run_start = time.time()

    print('1. refusals before any model call')
    for label, model, effort in (('unknown', 'no-such-model-phase12', None),
                                 ('effort', args.model, args.level)):
        n = len(watch.created)
        result = call(backend, request('Say OK.', model, effort=effort))
        checks.check(f'{label} model/effort: BackendUnavailableError',
                     not result['ok'] and type(result['error']) is BackendUnavailableError,
                     '' if result['ok'] else type(result['error']).__name__)
        checks.check(f'{label}: no model call',
                     not watch.of(watch.first_since(n), 'assistant.usage'))

    print('2. three concurrent calls')
    n = len(watch.created)
    results = [None] * len(SENTENCES)

    def one(i):
        results[i] = call(backend, request(f'Translate to Traditional Chinese: {SENTENCES[i]}',
                                           args.model))

    threads = [threading.Thread(target=one, args=(i,)) for i in range(len(SENTENCES))]
    for i, thread in enumerate(threads):
        thread.start()
        # The next starts once this one has its session, so the sessions are
        # created in the order of the results they are described with
        until(lambda: len(watch.created) > n + i or not thread.is_alive(), CALL_TIMEOUT)
    for thread in threads:
        thread.join()
    for i, (sid, result) in enumerate(zip(watch.created[n:], results)):
        describe(watch, f'concurrent {i}', sid, result)
    checks.check('all three answered in Chinese',
                 all(r['ok'] and HAN.search(r['response'].text) for r in results))
    overlap = max(r['start'] for r in results) < min(r['end'] for r in results)
    checks.check('the three calls overlapped in time', overlap)
    checks.check('one runtime process', len(children()) == 1, f'{len(children())}')

    print('3. runtime killed, then a translation with the product prompt')
    before = children()
    for pid in before:
        os.kill(pid, signal.SIGKILL)
    checks.check('the runtime is gone', children_gone())
    # The prompt does not depend on the engine's backend
    engine = TranslationEngine(backend=backend)
    prompt = engine._build_prompt('', SegmentType.TEXT,
                                  html=validation.protect(f'<p>{PARAGRAPH}</p>').html)
    n = len(watch.created)
    result = call(backend, request(prompt, args.model))
    describe(watch, 'translation', watch.first_since(n), result)
    checks.check('translation reply in Chinese',
                 result['ok'] and bool(HAN.search(result['response'].text)))
    after = children()
    checks.check('a new runtime process', len(after) == 1 and set(after).isdisjoint(before))
    checks.check('usage reported', result['ok'] and result['response'].usage is not None)

    print(f'4. effort {args.level} on {args.effort_model}')
    n = len(watch.created)
    result = call(backend, request('Translate to Traditional Chinese: The clock in the hall '
                                   'stopped at noon.', args.effort_model, effort=args.level))
    sid = watch.first_since(n)
    describe(watch, 'effort', sid, result)
    levels = costs(watch, sid)[1]
    checks.check(f'the model call reports effort {args.level}',
                 result['ok'] and bool(levels) and all(level == args.level for level in levels),
                 f'{levels}')

    print('5. cancel and timeout mid-turn')
    aborted_turn(watch, checks, backend, 'cancelled', args.model, timeout=CALL_TIMEOUT,
                 cancel_after=1)
    aborted_turn(watch, checks, backend, 'timed out', args.model, timeout=args.timeout_s)

    print('6. session files')
    left = [sid for sid in watch.created if os.path.exists(folder(sid))]
    checks.check(f'no session directory left of the {len(watch.created)} sessions', not left,
                 f'{len(left)} left')
    checks.check('a delete asked for every session', set(watch.created) <= watch.deleted)

    print('7. stop')
    copilot_sdk._runtime.close()
    checks.check('no runtime process left', children_gone(), f'{len(children())}')

    print('information')
    reported = sum(c for sid in watch.created for c in costs(watch, sid)[0] if c)
    print(f'  cost the SDK reported in total: {round(reported, 2)} premium requests')
    logs = [p for p in glob.glob(RUNTIME_LOGS) if os.path.getmtime(p) >= run_start]
    holds = any(MARKER in Path(p).read_text(encoding='utf-8', errors='replace') for p in logs)
    print(f'  runtime logs written during the run: {len(logs)}; hold prompt text: '
          f'{"yes" if holds else "no"}')

    print('PASS' if not checks.failed else f'FAIL: {", ".join(checks.failed)}')
    return 0 if not checks.failed else 1


if __name__ == '__main__':
    sys.exit(main())
