#!/usr/bin/env python3
"""Phase 7 measurement: per-call overhead of the Claude CLI with candidate flags.

Makes REAL `claude -p` calls (they cost money and quota). The prompt is the
translator's real text prompt (rules, glossary, context sections as the
engine builds them) around one invented paragraph: no book text.

Every configuration adds flags to the base command
`claude -p <prompt> --model <model> --output-format json` (json, so that the
CLI reports its usage; the text output that ClaudeCLI read before this phase
comes from the same run). The calls run round-robin over the configurations
(one call of each, then the next repetition), so a slow minute of the
service is spread over all of them. Each call runs as ClaudeCLI runs it:
`CLAUDECODE` removed from the environment, the system temporary directory
as working directory, stdin empty, killed after --timeout seconds.

Per configuration it prints the median wall time (seconds, the whole
process), the median time the CLI reports for the API (duration_api_ms), the
median input tokens (uncached + cache creation + cache read), of which the
median cache read, the median output tokens, the median cost the CLI
reports, the number of failed calls, and how many calls left a new session
transcript (`*.jsonl`) in the CLI's projects folder for the working
directory (`$CLAUDE_CONFIG_DIR` or `~/.claude`, then `projects/<the
directory's path with every character other than a letter or digit
replaced by ->`). With --raw it also prints one line per call. The script
itself writes nothing.

Usage:
    python3 scripts/verify/phase7_cli_overhead.py [--model haiku] [--repetitions 5]
        [--timeout 120] [--raw] [--only NAME ...]

Exit code: 0 when every call succeeded, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)

from src.core.segmenter import SegmentType  # noqa: E402
from src.core.translator import TranslationEngine  # noqa: E402
from src.llm.cli import run_process  # noqa: E402
from src.llm.fake import FakeBackend  # noqa: E402

# Invented: no book text
PARAGRAPH = ('<p>The small robot watered the tomato plants every morning before sunrise. '
             'When the rain came, it rolled under the porch and counted the drops on the '
             'window, keeping a tidy list of numbers that nobody ever asked for.</p>')
GLOSSARY = '- robot → 機器人 [術語]'
BEFORE = 'The garden behind the bakery was small but carefully kept.'
AFTER = 'By noon the list was long enough to fill a whole notebook.'

SYSTEM_PROMPT = '你是一位專業的英文翻譯。'

# name -> flags added to the base command
CONFIGS = [
    ('base', []),
    ('system-prompt', ['--system-prompt', SYSTEM_PROMPT]),
    ('tools-none', ['--tools', '']),
    ('strict-mcp', ['--strict-mcp-config']),
    ('setting-sources-none', ['--setting-sources', '']),
    ('no-session-persistence', ['--no-session-persistence']),
    ('all', ['--system-prompt', SYSTEM_PROMPT, '--tools', '', '--strict-mcp-config',
             '--setting-sources', '', '--no-session-persistence']),
]


def prompt_text() -> str:
    engine = TranslationEngine(model='x', backend=FakeBackend())
    from src.core import validation
    return engine._build_prompt('', SegmentType.TEXT, GLOSSARY, BEFORE, AFTER,
                                html=validation.protect(PARAGRAPH).html)


def transcript_dir(cwd: str) -> str:
    config = os.environ.get('CLAUDE_CONFIG_DIR') or os.path.expanduser('~/.claude')
    return os.path.join(config, 'projects', re.sub(r'[^A-Za-z0-9]', '-', os.path.realpath(cwd)))


def transcripts(cwd: str) -> set:
    try:
        return {n for n in os.listdir(transcript_dir(cwd)) if n.endswith('.jsonl')}
    except OSError:
        return set()


def one_call(flags: list, prompt: str, model: str, timeout: float) -> dict:
    before = transcripts(tempfile.gettempdir())
    call = _call(flags, prompt, model, timeout)
    call['transcript'] = bool(transcripts(tempfile.gettempdir()) - before)
    return call


def _call(flags: list, prompt: str, model: str, timeout: float) -> dict:
    cmd = ['claude', '-p', prompt, '--model', model, '--output-format', 'json'] + flags
    env = {k: v for k, v in os.environ.items() if k != 'CLAUDECODE'}
    start = time.monotonic()
    try:
        result = run_process(cmd, timeout=timeout, cwd=tempfile.gettempdir(), env=env,
                             cancel=threading.Event())
    except Exception as e:  # noqa: BLE001 - a failed call is counted, never printed in full
        return {'ok': False, 'wall': time.monotonic() - start, 'error': type(e).__name__}
    wall = time.monotonic() - start
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return {'ok': False, 'wall': wall, 'error': f'exit {result.returncode}, not JSON'}
    if result.returncode != 0 or data.get('is_error'):
        return {'ok': False, 'wall': wall, 'error': f'exit {result.returncode}'}
    usage = data.get('usage') or {}
    cache_read = usage.get('cache_read_input_tokens', 0)
    tokens_in = (usage.get('input_tokens', 0) + usage.get('cache_creation_input_tokens', 0)
                 + cache_read)
    return {'ok': True, 'wall': wall, 'api': data.get('duration_api_ms', 0) / 1000,
            'in': tokens_in, 'cache_read': cache_read,
            'out': usage.get('output_tokens', 0), 'cost': data.get('total_cost_usd', 0.0)}


def median(values: list) -> float:
    return statistics.median(values) if values else float('nan')


def summary_line(name: str, calls: list) -> str:
    ok = [c for c in calls if c['ok']]
    return (f'{name:<24} n={len(calls)} failed={len(calls) - len(ok)}  '
            f'wall {median([c["wall"] for c in ok]):6.2f} s  '
            f'api {median([c["api"] for c in ok]):6.2f} s  '
            f'in {median([c["in"] for c in ok]):8.0f}  '
            f'cache_read {median([c["cache_read"] for c in ok]):8.0f}  '
            f'out {median([c["out"] for c in ok]):5.0f}  '
            f'cost ${median([c["cost"] for c in ok]):.5f}  '
            f'transcripts {sum(c["transcript"] for c in calls)}/{len(calls)}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--model', default='haiku', help='Model alias (default: haiku)')
    parser.add_argument('--repetitions', type=int, default=5, help='Calls per configuration')
    parser.add_argument('--timeout', type=float, default=120, help='Seconds per call')
    parser.add_argument('--raw', action='store_true', help='Also print every call')
    parser.add_argument('--only', nargs='*', default=None,
                        help='Run only these configurations (names as printed)')
    args = parser.parse_args(argv)
    configs = [c for c in CONFIGS if args.only is None or c[0] in args.only]
    prompt = prompt_text()
    print(f'model {args.model}  repetitions {args.repetitions}  '
          f'prompt {len(prompt)} characters (invented paragraph)')
    calls = {name: [] for name, _ in configs}
    for rep in range(1, args.repetitions + 1):
        for name, flags in configs:
            call = one_call(flags, prompt, args.model, args.timeout)
            calls[name].append(call)
            if args.raw:
                detail = (f'wall {call["wall"]:.2f} api {call["api"]:.2f} in {call["in"]} '
                          f'cache_read {call["cache_read"]} out {call["out"]} '
                          f'transcript {int(call["transcript"])}'
                          if call['ok'] else f'FAILED {call["error"]}')
                print(f'  {rep} {name:<24} {detail}', flush=True)
    for name, _ in configs:
        print(summary_line(name, calls[name]))
    return 0 if all(c['ok'] for cs in calls.values() for c in cs) else 1


if __name__ == '__main__':
    sys.exit(main())
