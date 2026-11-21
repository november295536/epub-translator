#!/usr/bin/env python3
"""Phase 9 measurement: Copilot CLI input tokens per call with and without the system prompt replacement.

Makes REAL `copilot -p` calls (they count against the Copilot quota). The
prompt is the translator's real text prompt (rules, glossary, context
sections as the engine builds them) around invented paragraphs: no book
text. Configurations, each run as CopilotCLI runs it (its environment and
working directory, stdin empty, killed after --timeout seconds):

- `built-in`: CopilotCLI's command without `--experimental --plugin-dir`,
  i.e. Copilot's own system prompt (the command before this phase);
- `replaced`: CopilotCLI's command, the short prompt;
- `replaced-long`: CopilotCLI's command, a long prompt (8 paragraphs and a
  60-entry glossary, about the largest a segment makes);
- `no-experimental`: CopilotCLI's command with `--no-experimental`;
- `wrong-plugin-dir`: CopilotCLI's command with a plugin folder that does
  not exist.

Each call is judged by `CopilotCLI.classify_error`: a `replaced*` call
passes when it raises nothing; every other call passes when it is detected,
i.e. raises the BackendUnavailableError of a system prompt that was not
replaced. Per configuration it prints the median wall time (seconds, the
whole process), the median input and output tokens, the median number of
system prompt segments the CLI reports and their tokens, and how many calls
passed. With --raw also one line per call. The script writes nothing.

Usage:
    python3 scripts/verify/phase9_copilot_overhead.py [--model claude-haiku-4.5]
        [--repetitions 3] [--timeout 120] [--raw] [--only NAME ...]

Exit code: 0 when every call passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)

from src.core import validation  # noqa: E402
from src.core.segmenter import SegmentType  # noqa: E402
from src.core.translator import TranslationEngine  # noqa: E402
from src.llm import BackendUnavailableError, LLMRequest, Message  # noqa: E402
from src.llm import copilot_cli  # noqa: E402
from src.llm.cli import run_process  # noqa: E402
from src.llm.fake import FakeBackend  # noqa: E402

# Invented: no book text
PARAGRAPHS = [
    'The small robot watered the tomato plants every morning before sunrise.',
    'When the rain came, it rolled under the porch and counted the drops on the window.',
    'Its list of numbers grew longer each week, although nobody ever asked to read it.',
    'On market days the baker next door borrowed it to carry baskets of warm bread.',
    'The children of the street taught it a song about seven yellow umbrellas.',
    'In winter it swept the snow from the steps and polished the brass door handle.',
    'One evening a lost pigeon landed on its head and stayed there until spring.',
    'By the end of the year the robot knew the name of every cat in the village.',
]
SHORT = '<p>' + ' '.join(PARAGRAPHS[:2]) + '</p>'
LONG = ''.join(f'<p>{" ".join(PARAGRAPHS)} {p}</p>' for p in PARAGRAPHS)
GLOSSARY_SHORT = '- robot → 機器人 [術語]'
GLOSSARY_LONG = '\n'.join(f'- lantern-{i} → 燈籠{i}號 [術語]' for i in range(60))
BEFORE = 'The garden behind the bakery was small but carefully kept.'
AFTER = 'By noon the list was long enough to fill a whole notebook.'

# name -> (expected to be replaced, long prompt)
CONFIGS = [
    ('built-in', False, False),
    ('replaced', True, False),
    ('replaced-long', True, True),
    ('no-experimental', False, False),
    ('wrong-plugin-dir', False, False),
]


def prompt_text(long: bool) -> str:
    engine = TranslationEngine(backend=FakeBackend())
    html, glossary = (LONG, GLOSSARY_LONG) if long else (SHORT, GLOSSARY_SHORT)
    return engine._build_prompt('', SegmentType.TEXT, glossary, BEFORE, AFTER,
                                html=validation.protect(html).html)


def command(name: str, prompt: str, model: str) -> list:
    """CopilotCLI's command for `prompt`, changed as configuration `name` says."""
    cmd = copilot_cli.CopilotCLI().build_command(
        prompt, LLMRequest(system='', messages=[Message('user', prompt)], model=model, timeout=0))
    plugin = cmd.index('--plugin-dir')
    if name == 'built-in':
        del cmd[plugin - 1:plugin + 2]  # --experimental --plugin-dir <dir>
    elif name == 'no-experimental':
        cmd[plugin - 1] = '--no-experimental'
    elif name == 'wrong-plugin-dir':
        cmd[plugin + 1] = copilot_cli.PLUGIN_DIR + '-missing'
    return cmd


def system_segments(events: list) -> tuple:
    """(segments, tokens) of the system prompt of the last model call reported."""
    found = (0, 0)
    for event in events:
        if event.get('type') == 'session.usage_checkpoint':
            for state in event['data']['promptCacheBreakState']:
                for model in state['models'].values():
                    segments = model['system_segments']
                    found = (len(segments), sum(s['tokens'] for s in segments))
    return found


def one_call(name: str, replaced: bool, prompt: str, model: str, timeout: float) -> dict:
    backend = copilot_cli.CopilotCLI()
    start = time.monotonic()
    try:
        result = run_process(command(name, prompt, model), timeout=timeout,
                             cwd=backend.working_directory(), env=backend.environment())
    except Exception as e:  # noqa: BLE001 - a failed call is counted, never printed in full
        return {'ok': False, 'wall': time.monotonic() - start, 'error': type(e).__name__}
    wall = time.monotonic() - start
    events = copilot_cli._events(result.stdout)
    usage = copilot_cli._usage(events)
    if usage is None:
        return {'ok': False, 'wall': wall, 'error': f'no usage in the output (exit {result.returncode})'}
    try:
        segments, tokens = system_segments(events)
    except (KeyError, TypeError, AttributeError):
        segments, tokens = 0, 0
    error = backend.classify_error(result)
    detected = (isinstance(error, BackendUnavailableError)
                and str(error) == copilot_cli.NOT_REPLACED_MESSAGE)
    return {'ok': error is None if replaced else detected, 'wall': wall,
            'in': usage.input_tokens, 'out': usage.output_tokens,
            'segments': segments, 'system_tokens': tokens,
            'error': 'not replaced' if detected else type(error).__name__ if error else 'replaced'}


def median(values: list) -> float:
    return statistics.median(values) if values else float('nan')


def summary_line(name: str, calls: list) -> str:
    done = [c for c in calls if 'in' in c]
    return (f'{name:<18} n={len(calls)} passed={sum(c["ok"] for c in calls)}  '
            f'wall {median([c["wall"] for c in done]):6.2f} s  '
            f'in {median([c["in"] for c in done]):7.0f}  '
            f'out {median([c["out"] for c in done]):5.0f}  '
            f'system segments {median([c["segments"] for c in done]):4.0f} '
            f'({median([c["system_tokens"] for c in done]):5.0f} tokens)')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--model', default='claude-haiku-4.5', help='Model (default: claude-haiku-4.5)')
    parser.add_argument('--repetitions', type=int, default=3, help='Calls per configuration')
    parser.add_argument('--timeout', type=float, default=120, help='Seconds per call')
    parser.add_argument('--raw', action='store_true', help='Also print every call')
    parser.add_argument('--only', nargs='*', default=None,
                        help='Run only these configurations (names as printed)')
    args = parser.parse_args(argv)
    configs = [c for c in CONFIGS if args.only is None or c[0] in args.only]
    prompts = {False: prompt_text(False), True: prompt_text(True)}
    print(f'model {args.model}  repetitions {args.repetitions}  prompts {len(prompts[False])} '
          f'and {len(prompts[True])} characters (invented paragraphs)')
    calls = {name: [] for name, _, _ in configs}
    for rep in range(1, args.repetitions + 1):
        for name, replaced, long in configs:
            call = one_call(name, replaced, prompts[long], args.model, args.timeout)
            calls[name].append(call)
            if args.raw:
                detail = (f'wall {call["wall"]:.2f} in {call["in"]} out {call["out"]} '
                          f'system segments {call["segments"]} ({call["system_tokens"]} tokens)'
                          if 'in' in call else '')
                verdict = 'pass' if call['ok'] else 'FAIL'
                print(f'  {rep} {name:<18} {verdict} {call["error"]} {detail}'.rstrip(), flush=True)
    for name, _, _ in configs:
        print(summary_line(name, calls[name]))
    return 0 if all(c['ok'] for cs in calls.values() for c in cs) else 1


if __name__ == '__main__':
    sys.exit(main())
