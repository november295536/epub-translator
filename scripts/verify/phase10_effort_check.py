#!/usr/bin/env python3
"""Phase 10 measurement: does the project's reasoning effort reach the CLI?

Makes REAL calls (they count against the Claude or Copilot quota). For one
backend and model it translates one invented paragraph (no book text; an
idiom, a pun and an ambiguous "he" make the model think) once per level
given, through the product's own command construction
(`build_command` of the backend, with an LLMRequest carrying the level, as
TranslationEngine builds it), the product's environment and working
directory (the system temp dir), stdin empty, killed after --timeout
seconds. The prompt is the translator's real text prompt around that
paragraph.

The Claude backend's command is the product's with `--output-format json`
added at the end, so the CLI reports its usage (the product reads plain
text); Copilot's command is the product's as it is (it already asks for
JSON). Per call it prints: level, wall time (seconds, the whole process),
input tokens (Claude: input + cache creation + cache read), output tokens,
the reasoning tokens inside them, and for Copilot the level the CLI says
the session used (`session.start` `reasoningEffort`; Claude reports none,
"-"). Nothing else of the output is printed (no reply text, no account
data); the script writes no file (the CLIs keep their own session logs).

A level the model does not accept is refused before any call (exit 2), as
the product refuses it.

Usage:
    python3 scripts/verify/phase10_effort_check.py --backend claude-cli --model opus
        [--levels low high] [--timeout 180]

Exit code: 0 when every call succeeded (no error by the backend's own
classification, a non-empty reply), 1 otherwise; 2 for invalid options.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)

import src.llm as llm  # noqa: E402
from src.core import validation  # noqa: E402
from src.core.project import DEFAULT_TIMEOUT, effort_levels  # noqa: E402
from src.core.segmenter import SegmentType  # noqa: E402
from src.core.translator import TranslationEngine  # noqa: E402
from src.llm import LLMRequest, Message  # noqa: E402
from src.llm import copilot_cli  # noqa: E402
from src.llm.cli import run_process  # noqa: E402
from src.llm.fake import FakeBackend  # noqa: E402

# Invented: no book text
PARAGRAPH = (
    'When Priya told her uncle that the new ferry service was "making waves", he assumed she '
    'meant the wake it left in the harbour. She did not. The operator, a former lighthouse '
    'keeper named Tomas, had added two routes in a single summer, and by six each morning the '
    'queue for the first boat stretched past the fish market. Priya\'s uncle rode it with his '
    'neighbour Ines, who had insisted that the crossing was a waste of a good morning until '
    'the dolphins appeared beside the bow; after that she said nothing at all. Later Tomas '
    'asked him what he thought, and he said it beat walking on water, which made Tomas laugh '
    'so hard he nearly dropped the mooring line. Nobody was sure whether he was joking. Priya, '
    'for her part, said the whole thing had been plain sailing.')


def prompt_text() -> str:
    engine = TranslationEngine(backend=FakeBackend())
    return engine._build_prompt('', SegmentType.TEXT,
                                html=validation.protect(f'<p>{PARAGRAPH}</p>').html)


def command(backend, prompt: str, model: str, level: str) -> list:
    """The product's command for this level (Claude: plus JSON output)."""
    request = LLMRequest(system='', messages=[Message('user', prompt)], model=model,
                         timeout=0, effort=level)
    cmd = backend.build_command(prompt, request)
    if backend.name == 'claude-cli':
        cmd += ['--output-format', 'json']
    return cmd


def claude_values(stdout: str) -> dict:
    """Reply and tokens of Claude Code's JSON result."""
    result = json.loads(stdout)
    usage = result['usage']
    return {
        'reply': result.get('result') or '',
        'in': (usage['input_tokens'] + usage.get('cache_creation_input_tokens', 0)
               + usage.get('cache_read_input_tokens', 0)),
        'out': usage['output_tokens'],
        'reasoning': (usage.get('output_tokens_details') or {}).get('thinking_tokens', 0),
        'reported': '-',
        'error': 'error result' if result.get('is_error') else None,
    }


def copilot_values(stdout: str) -> dict:
    """Reply, tokens and the session's reported level of Copilot's JSON events."""
    events = copilot_cli._events(stdout)
    reported = '-'
    for event in events:
        if event.get('type') == 'session.start':
            reported = str(copilot_cli._data(event).get('reasoningEffort'))
    checkpoints = [e for e in events if e.get('type') == 'session.usage_checkpoint']
    usages = [m['usage'] for m in copilot_cli._data(checkpoints[-1])['accountingSnapshot']
              ['modelMetrics'].values()]
    return {
        'reply': copilot_cli._reply(events),
        'in': sum(int(u['inputTokens']) for u in usages),
        'out': sum(int(u['outputTokens']) for u in usages),
        'reasoning': sum(int(u.get('reasoningTokens', 0)) for u in usages),
        'reported': reported,
        'error': None,
    }


def one_call(backend, prompt: str, model: str, level: str, timeout: float) -> dict:
    start = time.monotonic()
    try:
        result = run_process(command(backend, prompt, model, level), timeout=timeout,
                             cwd=backend.working_directory(), env=backend.environment())
    except Exception as e:  # noqa: BLE001 - a failed call is counted, never printed in full
        return {'level': level, 'ok': False, 'error': type(e).__name__,
                'wall': time.monotonic() - start}
    wall = time.monotonic() - start
    error = backend.classify_error(result, prompt)
    if error is not None:
        return {'level': level, 'ok': False, 'error': type(error).__name__, 'wall': wall}
    try:
        values = (claude_values if backend.name == 'claude-cli' else copilot_values)(result.stdout)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        return {'level': level, 'ok': False, 'error': 'unreadable output', 'wall': wall}
    ok = values['error'] is None and bool(values['reply'].strip())
    return {'level': level, 'ok': ok, 'wall': wall, **values,
            'error': values['error'] or ('' if ok else 'empty reply')}


def line(backend: str, model: str, call: dict) -> str:
    if 'in' not in call:
        return (f'{backend:<12} {model:<18} {call["level"]:<7} FAIL {call["error"]} '
                f'after {call["wall"]:.1f} s')
    verdict = 'ok' if call['ok'] else f'FAIL {call["error"]}'
    return (f'{backend:<12} {model:<18} {call["level"]:<7} reported {call["reported"]:<7} '
            f'wall {call["wall"]:7.2f} s  in {call["in"]:6d}  out {call["out"]:6d}  '
            f'reasoning {call["reasoning"]:6d}  {verdict}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--backend', required=True, choices=sorted(llm.BACKENDS))
    parser.add_argument('--model', required=True, help="One of the backend's models")
    parser.add_argument('--levels', nargs='+', default=['low', 'high'], metavar='LEVEL',
                        help='Levels to call with, in order (default: low high)')
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT,
                        help=f'Seconds per call (default {DEFAULT_TIMEOUT}, the product default)')
    args = parser.parse_args(argv)
    accepted = effort_levels(args.backend, args.model)
    refused = [level for level in args.levels if level not in accepted]
    if refused:
        parser.error(f'{args.model} ({args.backend}) does not take {", ".join(refused)}; '
                     f'it takes: {", ".join(accepted) or "none"}')
    backend = llm.backend_for(args.backend)
    prompt = prompt_text()
    print(f'{args.backend} {args.model}  levels {" ".join(args.levels)}  timeout {args.timeout:g} s  '
          f'prompt {len(prompt)} characters (invented paragraph)')
    calls = []
    for level in args.levels:
        call = one_call(backend, prompt, args.model, level, args.timeout)
        calls.append(call)
        print(line(args.backend, args.model, call), flush=True)
    return 0 if calls and all(c['ok'] for c in calls) else 1


if __name__ == '__main__':
    sys.exit(main())
