"""Summary of a project's event record (src/core/events.py): the one
function behind `translate.py stats` and the web statistics page."""

from __future__ import annotations

import datetime
import json
import math
import os
from collections import Counter

from src.core.events import EVENTS_VERSION, events_path

# Outcomes of a call that returned a reply: the only ones whose duration
# says how fast a model answers (not a pause's cancel, a quick error, a timeout)
_REPLIED = ('accepted', 'rejected', 'truncated')

# The segment results that count as a segment's end (dropped ones do not)
_FINAL = ('translated', 'failed')


def parse_since(text: str) -> str:
    """The UTC record time (as in `ts`) of a --since value: an ISO date or
    date and time, read as local time unless it names an offset. Raises
    ValueError."""
    moment = datetime.datetime.fromisoformat(text.strip())
    try:
        moment = moment.astimezone(datetime.timezone.utc)
    except OverflowError as e:  # e.g. 0001-01-01 east of UTC
        raise ValueError(f'out of range: {text}') from e
    return moment.strftime('%Y-%m-%dT%H:%M:%S.') + f'{moment.microsecond // 1000:03d}Z'


# The type of each field the record format defines (src/core/events.py);
# a record with a field of another type is skipped (and FAILs the check
# script, which shares this)
_TEXT_FIELDS = frozenset({'ts', 'run', 'source', 'event', 'mode', 'backend', 'model', 'effort',
                          'kind', 'segment', 'outcome', 'error', 'result', 'reason', 'state'})
_NUMBER_FIELDS = frozenset({'v', 'workers', 'segments', 'timeout', 'attempt', 'seconds',
                            'tokens_in', 'tokens_out', 'chars_in', 'chars_out', 'calls',
                            'translated', 'failed', 'dropped', 'completed', 'resume_in',
                            'terms', 'added'})
_FLAG_FIELDS = frozenset({'split'})
_CODE_LIST_FIELDS = frozenset({'issues', 'warnings'})
FIELDS = _TEXT_FIELDS | _NUMBER_FIELDS | _FLAG_FIELDS | _CODE_LIST_FIELDS


def shape_problem(record) -> str | None:
    """Why `record` is not a record of this format (not an object; no
    event/run/ts; a later version; a known field of the wrong type: null
    counts as left out), or
    None. Unknown fields pass (the check script reports them)."""
    if not isinstance(record, dict):
        return 'not an object'
    if not all(isinstance(record.get(k), str) for k in ('event', 'run', 'ts')):
        return 'no event, run or ts'
    if record.get('v', EVENTS_VERSION) != EVENTS_VERSION:
        return 'another version'
    for key, value in record.items():
        if value is None:
            continue  # as if left out
        if key in _TEXT_FIELDS:
            ok = isinstance(value, str)
        elif key in _NUMBER_FIELDS:
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif key in _FLAG_FIELDS:
            ok = isinstance(value, bool)
        elif key in _CODE_LIST_FIELDS:
            ok = isinstance(value, list) and all(isinstance(c, str) for c in value)
        else:
            continue
        if not ok:
            return f'{key}: wrong type'
    return None


def read_records(path: str) -> tuple[list[dict], int]:
    """The records of an event file and the number of lines skipped (not
    JSON, or shape_problem). ([], 0) when the file does not exist."""
    records, skipped = [], 0
    try:
        f = open(path, 'rb')
    except FileNotFoundError:
        return records, skipped
    with f:
        for raw in f:
            if not raw.strip():
                continue
            try:
                record = json.loads(raw.decode('utf-8'))
            except (UnicodeDecodeError, ValueError):
                skipped += 1
                continue
            if shape_problem(record) is not None:
                skipped += 1
                continue
            records.append(record)
    return records, skipped


def _seconds_between(start: str, end: str) -> float | None:
    try:
        a = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
        b = datetime.datetime.fromisoformat(end.replace('Z', '+00:00'))
    except ValueError:
        return None
    return round((b - a).total_seconds(), 3)


def percentile(values: list[float], share: float) -> float | None:
    """Nearest-rank percentile (share in (0, 1]); None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(share * len(ordered))) - 1]


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


# Call kinds of term extraction; every other kind is a translation call
_EXTRACTION_KINDS = ('extract', 'consolidate')


def _key(record: dict) -> tuple:
    work = 'extraction' if record.get('kind') in _EXTRACTION_KINDS else 'translation'
    return (work, str(record.get('backend') or '?'), str(record.get('model') or '?'),
            str(record.get('effort') or '-'))


def _by_count(counts: Counter) -> dict:
    """Most frequent first, equal counts by code (the page sorts the same
    way: JSON object keys do not keep an order)."""
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _share(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def summarize(records: list[dict], skipped: int = 0, since: str | None = None,
              run: str | None = None) -> dict:
    """The numbers of `records` (in file order), optionally only those at
    or after `since` (a record time, parse_since) and of run `run`.

    Per work (translation; extraction: calls of kind extract/consolidate)
    and backend/model/effort: model calls, their outcomes, issue codes of
    rejected replies, duration median and p90 (nearest rank, over the calls
    that returned a reply: `seconds_calls`), timeouts, tokens in/out (the calls
    that report them); the segments that ended there (the model of their
    last call): translated with one call, translated with more (retries,
    transient retries, a split; `split` counts those split), failed. Per run: source, start, end, duration, segments
    translated/failed/dropped, pauses, calls, end state."""
    groups: dict[tuple, dict] = {}
    runs: dict[str, dict] = {}
    last_call: dict[tuple, tuple] = {}  # (run, segment) -> key of its last call
    failures, warnings, rejections = Counter(), Counter(), Counter()
    used = 0

    def group(key):
        return groups.setdefault(key, {
            'work': key[0], 'backend': key[1], 'model': key[2],
            'effort': None if key[3] == '-' else key[3],
            'calls': 0, 'outcomes': Counter(), 'issues': Counter(), 'seconds': [],
            'tokens_in': 0, 'tokens_out': 0, 'calls_with_tokens': 0, 'timeouts': 0,
            'segments': 0, 'first_attempt': 0, 'more_calls': 0, 'split': 0, 'failed': 0,
        })

    for r in records:
        if since is not None and r['ts'] < since:
            continue
        if run is not None and r['run'] != run:
            continue
        used += 1
        info = runs.setdefault(r['run'], {
            'run': r['run'], 'source': r.get('source'), 'start': r['ts'], 'end': None,
            'last': r['ts'], 'state': None, 'workers': None, 'segments': None,
            'translated': 0, 'failed': 0, 'dropped': 0, 'pauses': 0, 'calls': 0,
            'backend': None, 'model': None, 'effort': None, 'error': None,
        })
        info['last'] = max(info['last'], r['ts'])
        event = r['event']
        if event == 'run_start':
            info['start'] = r['ts']
            for field in ('workers', 'segments', 'backend', 'model', 'effort'):
                info[field] = r.get(field)
        elif event == 'run_end':
            info['end'] = r['ts']
            info['seconds'] = _number(r.get('seconds'))
            info['state'] = r.get('state')
            info['error'] = r.get('error')
        elif event == 'pause':
            info['pauses'] += 1
        elif event == 'call':
            key = _key(r)
            g = group(key)
            info['calls'] += 1
            g['calls'] += 1
            g['outcomes'][str(r.get('outcome'))] += 1
            codes = [str(c) for c in r.get('issues') or [] if isinstance(c, str)]
            g['issues'].update(codes)
            rejections.update(codes)
            seconds = _number(r.get('seconds'))
            if seconds is not None and r.get('outcome') in _REPLIED:
                g['seconds'].append(seconds)  # a call cancelled or failed fast would skew it
            if r.get('outcome') == 'timeout':
                g['timeouts'] += 1
            tokens_in, tokens_out = _number(r.get('tokens_in')), _number(r.get('tokens_out'))
            if tokens_in is not None or tokens_out is not None:
                g['calls_with_tokens'] += 1
                g['tokens_in'] += tokens_in or 0
                g['tokens_out'] += tokens_out or 0
            if r.get('segment') is not None:
                last_call[(r['run'], r['segment'])] = key
        elif event == 'segment':
            result = r.get('result')
            if result in ('translated', 'failed', 'dropped'):
                info[result] += 1
            if result not in _FINAL:
                continue
            key = last_call.get((r['run'], r.get('segment')))
            if key is None:  # its calls are not in the selection
                key = ('translation', '?', str(r.get('model') or '?'), '-')
            g = group(key)
            g['segments'] += 1
            calls = _number(r.get('calls')) or 0
            if result == 'failed':
                g['failed'] += 1
                failures.update(str(c) for c in r.get('issues') or [])
            else:
                warnings.update(str(c) for c in r.get('warnings') or [])
                if calls <= 1:
                    g['first_attempt'] += 1
                else:
                    g['more_calls'] += 1
                    if r.get('split'):  # a part of more_calls
                        g['split'] += 1

    models = []
    for g in sorted(groups.values(), key=lambda g: (g['work'] != 'translation', g['backend'],
                                                    g['model'], g['effort'] or '')):
        seconds = g.pop('seconds')
        g['outcomes'] = _by_count(g['outcomes'])
        g['issues'] = _by_count(g['issues'])
        g['seconds_calls'] = len(seconds)  # the calls the two durations are based on
        g['seconds_median'] = percentile(seconds, 0.5)
        g['seconds_p90'] = percentile(seconds, 0.9)
        g['first_attempt_share'] = _share(g['first_attempt'], g['segments'])
        g['more_calls_share'] = _share(g['more_calls'], g['segments'])
        g['failed_share'] = _share(g['failed'], g['segments'])
        if not g['calls_with_tokens']:
            g['tokens_in'] = g['tokens_out'] = None  # the backend reports none
        models.append(g)

    run_list = []
    for info in sorted(runs.values(), key=lambda i: i['start']):
        # A finished run: the duration it measured itself (monotonic clock);
        # else from the record times (an unfinished run: up to its last line)
        seconds = info.pop('seconds', None)
        info['duration'] = (seconds if seconds is not None and info['end'] else
                            _seconds_between(info['start'], info['end'] or info['last']))
        if info['state'] is None:
            info['state'] = 'unfinished'  # no run_end: still running, or the process died
        del info['last']
        run_list.append(info)

    return {
        'records': used,
        'skipped_lines': skipped,
        'since': since,
        'run': run,
        'models': models,
        'rejected_issues': _by_count(rejections),
        'failure_issues': _by_count(failures),
        'warnings': _by_count(warnings),
        'runs': run_list,
    }


def project_stats(project_dir: str, since: str | None = None, run: str | None = None) -> dict:
    """summarize() of a project's event record, with `exists`: whether the
    record file exists. Reads only that file (safe while a run appends)."""
    path = events_path(project_dir)
    records, skipped = read_records(path)
    stats = summarize(records, skipped, since=since, run=run)
    stats['exists'] = os.path.isfile(path)
    return stats


def _local(ts: str | None) -> str:
    """A record time as local time (YYYY-MM-DD HH:MM:SS); '' for none."""
    if not ts:
        return ''
    try:
        moment = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return ts
    return moment.astimezone().strftime('%Y-%m-%d %H:%M:%S')


def _pct(share: float | None) -> str:
    return '-' if share is None else f'{share * 100:.1f}%'


def _secs(value: float | None) -> str:
    return '-' if value is None else f'{value:.1f} 秒'


def _codes(counts: dict) -> str:
    return '、'.join(f'{code} {n}' for code, n in counts.items()) or '無'


# How the kinds of work read in the text report
WORK_TEXT = {'translation': '翻譯', 'extraction': '術語提取'}

# How the run states read in the text report
STATE_TEXT = {'completed': '完成', 'stopped': '停止', 'failed': '失敗',
              'interrupted': '中斷', 'unfinished': '未結束（執行中或程序已終止）'}


def text_report(stats: dict) -> list[str]:
    """The summary as text lines (translate.py stats)."""
    if not stats['records']:
        if not stats.get('exists', True):
            lines = ['此專案還沒有事件紀錄（log/events.jsonl）：翻譯或提取術語後才會產生。']
        else:
            lines = ['事件紀錄中沒有符合條件的紀錄。']
        if stats['skipped_lines']:
            lines.append(f'略過 {stats["skipped_lines"]} 行無法解析的紀錄。')
        return lines
    lines = [f'事件紀錄：{stats["records"]} 筆'
             + (f'（略過 {stats["skipped_lines"]} 行無法解析的紀錄）'
                if stats['skipped_lines'] else '')]
    lines.append('')
    lines.append('依後端／模型／推理強度：')
    for g in stats['models']:
        lines.append(f'  {WORK_TEXT.get(g["work"], g["work"])}：'
                     f'{g["backend"]} / {g["model"]} / {g["effort"] or "-"}')
        lines.append(f'    模型呼叫 {g["calls"]} 次：{_codes(g["outcomes"])}')
        if g['segments']:
            lines.append(
                f'    段落 {g["segments"]}：首次即通過 {g["first_attempt"]}'
                f'（{_pct(g["first_attempt_share"])}），需多次呼叫 {g["more_calls"]}'
                f'（{_pct(g["more_calls_share"])}；其中分割 {g["split"]}），'
                f'最終失敗 {g["failed"]}（{_pct(g["failed_share"])}）')
        if g['issues']:
            lines.append(f'    回覆被退回的原因：{_codes(g["issues"])}')
        lines.append(f'    回覆耗時（{g["seconds_calls"]} 次有回覆的呼叫）：中位數 '
                     f'{_secs(g["seconds_median"])}，p90 {_secs(g["seconds_p90"])}'
                     + (f'；逾時 {g["timeouts"]} 次' if g['timeouts'] else ''))
        if g['tokens_in'] is None:
            lines.append('    tokens：後端未回報')
        else:
            lines.append(f'    tokens：輸入 {g["tokens_in"]}，輸出 {g["tokens_out"]}'
                         f'（{g["calls_with_tokens"]} 次呼叫有回報）')
    lines.append('')
    lines.append('問題代碼（依次數）：')
    lines.append(f'  回覆被退回：{_codes(stats["rejected_issues"])}')
    lines.append(f'  段落最終失敗：{_codes(stats["failure_issues"])}')
    lines.append(f'  已翻譯段落的警告：{_codes(stats["warnings"])}')
    lines.append('')
    lines.append('執行紀錄：')
    for r in stats['runs']:
        end = _local(r['end']) or '-'
        lines.append(
            f'  {r["run"]}  {r["source"]}  {_local(r["start"])} → {end}'
            f'（{_secs(r["duration"])}）  {STATE_TEXT.get(r["state"], r["state"])}'
            + (f'（{r["error"]}）' if r['error'] else ''))
        lines.append(
            f'    已翻譯 {r["translated"]}、失敗 {r["failed"]}、捨棄 {r["dropped"]}、'
            f'暫停 {r["pauses"]} 次、模型呼叫 {r["calls"]} 次'
            + (f'（共 {r["segments"]} 段待處理）' if r['segments'] is not None else ''))
    return lines
