"""Run records: the persistent process log (src/core/proclog.py), the
per-project event record (src/core/events.py), its summary
(src/core/stats.py) through `translate.py stats` and the web statistics
page. All text is invented."""

import json
import logging
import os
import subprocess
import sys
import threading
import time

import pytest
from click.testing import CliRunner

import src.core.events as events
import translate
from src.core import proclog
from src.core.events import RunEvents, append_record, error_outcome, events_path
from src.core.project import ProjectManager, SegmentState, SegmentStatus
from src.core.segmenter import Segment, SegmentType
from src.core.stats import parse_since, percentile, project_stats, read_records, summarize, \
    text_report
from src.core.translator import TranslationEngine, translate_segment
from src.glossary.term_extractor import ExtractionError, TermExtractor
from src.llm import (
    AuthError, BackendUnavailableError, CallTimeoutError, CancelledError, LLMError,
    LLMResponse, RateLimitError, RefusalError, TransientError, Usage,
)
from src.llm.fake import FakeBackend
from src.web import tasks as tasks_module
from src.web.server import create_app
from src.web.tasks import TaskManager
from tests.conftest import _REPO_ROOT, like, use_fake_backend

PROJECT = 'p'

# Invented words that must never reach the event record
SOURCE_WORD = 'Zorblaxian'
REPLY_WORD = '佐布拉星'
TERM_WORD = 'Quillfeather'


def _wait(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'timed out waiting'
        time.sleep(0.01)


def _make_project(projects_dir, n=3, name=PROJECT, word=SOURCE_WORD):
    segments = [Segment(id=f'c1_seg{i:04d}', html=f'<p>{word} line {i}.</p>',
                        text=f'{word} line {i}.', word_count=3, segment_type=SegmentType.TEXT)
                for i in range(1, n + 1)]
    path = os.path.join(str(projects_dir), name)
    ProjectManager(path).create('/nowhere/book.epub', 'Invented', 'Nobody', segments)
    return path


def _records(project_dir):
    records, skipped = read_records(events_path(project_dir))
    assert skipped == 0
    return records


def _of(records, event):
    return [r for r in records if r['event'] == event]


def _seg(seg_id='s1', html='<p>Hello.</p>', text='Hello.'):
    return SegmentState(segment_id=seg_id, chapter_id='c1', html=html, text=text,
                        word_count=1, segment_type=SegmentType.TEXT)


def _reply(text, usage=None, stop='complete'):
    return LLMResponse(text=text, stop_reason=stop, usage=usage, model='fake-model')


@pytest.fixture
def fresh_tm(monkeypatch):
    TaskManager._instance = None
    monkeypatch.setattr(tasks_module, 'COORDINATOR_TICK_SECONDS', 0.02)
    tm = TaskManager()
    yield tm
    for task in list(tm.tasks.values()):
        tm.stop_translation(task.project_name)
        if task._coordinator is not None:
            task._coordinator.join(timeout=20)
    TaskManager._instance = None


# --- the record file -----------------------------------------------------------

class TestAppend:
    def test_one_json_object_per_line(self, tmp_path):
        ev = RunEvents(str(tmp_path), 'cli', run_id='r1')
        assert ev.emit('run_start', workers=2, effort=None)
        ev.emit('run_end', state='completed')
        lines = open(events_path(str(tmp_path)), encoding='utf-8').read().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first['event'] == 'run_start' and first['run'] == 'r1'
        assert first['source'] == 'cli' and first['workers'] == 2 and first['v'] == 1
        assert 'effort' not in first  # None is left out
        assert first['ts'].endswith('Z') and len(first['ts']) == 24

    def test_cut_off_line_is_ended(self, tmp_path):
        path = events_path(str(tmp_path))
        os.makedirs(os.path.dirname(path))
        with open(path, 'w') as f:
            f.write('{"event": "call", "ru')  # a crash mid-write
        assert append_record(path, {'v': 1, 'ts': 'x', 'run': 'r', 'event': 'pause'})
        records, skipped = read_records(path)
        assert skipped == 1 and [r['event'] for r in records] == ['pause']

    def test_write_failure_is_logged_not_raised(self, tmp_path, caplog):
        blocker = tmp_path / 'log'
        blocker.write_text('a file where the directory should be')
        with caplog.at_level(logging.WARNING, logger='src.core.events'):
            assert RunEvents(str(tmp_path), 'cli').emit('pause') is False
        assert '無法寫入事件紀錄' in caplog.text

    def test_unwritable_record_does_not_fail_a_translation(self, tmp_path):
        (tmp_path / 'log').write_text('not a directory')
        fake = FakeBackend(responder=lambda r: like(r, '你好。'))
        engine = TranslationEngine(backend=fake, events=RunEvents(str(tmp_path), 'cli'))
        outcome = translate_segment(engine, _seg(), [_seg()])
        assert outcome.status == SegmentStatus.TRANSLATED

    def test_threads_and_another_process_append_whole_lines(self, tmp_path):
        """20 threads here and a second process append at once: every line
        is one whole record."""
        path = events_path(str(tmp_path))
        child = subprocess.Popen([sys.executable, '-c', (
            'import sys; sys.path.insert(0, sys.argv[1])\n'
            'from src.core.events import RunEvents\n'
            'ev = RunEvents(sys.argv[2], "web", run_id="child")\n'
            'for i in range(500): ev.emit("call", n=i, pad="x" * 300)\n'),
            _REPO_ROOT, str(tmp_path)])

        def work(k):
            ev = RunEvents(str(tmp_path), 'cli', run_id=f't{k}')
            for i in range(50):
                ev.emit('call', n=i, pad='y' * 300)
        threads = [threading.Thread(target=work, args=(k,)) for k in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert child.wait(timeout=60) == 0
        records, skipped = read_records(path)
        assert skipped == 0 and len(records) == 20 * 50 + 500
        assert sum(r['run'] == 'child' for r in records) == 500


@pytest.mark.parametrize('error, outcome', [
    (CancelledError('c'), 'cancelled'), (KeyboardInterrupt(), 'cancelled'),
    (RateLimitError('r'), 'rate_limit'),
    (AuthError('a'), 'auth'), (BackendUnavailableError('b'), 'backend_unavailable'),
    (RefusalError('n'), 'refusal'), (CallTimeoutError('t'), 'timeout'),
    (TransientError('x'), 'transient'), (LLMError('e'), 'error'), (ValueError('v'), 'unexpected'),
])
def test_error_outcome(error, outcome):
    assert error_outcome(error) == outcome


def test_backends_raise_call_timeout_on_timeout():
    """Both process and SDK backends' timeouts are CallTimeoutError (a
    TransientError, retried as before)."""
    import inspect
    from src.llm import cli, copilot_sdk
    assert issubclass(CallTimeoutError, TransientError)
    assert 'raise CallTimeoutError(' in inspect.getsource(cli.CLIBackend.complete)
    assert inspect.getsource(copilot_sdk.CopilotSDK.complete).count('raise CallTimeoutError(') == 2
    assert isinstance(copilot_sdk._error(TimeoutError('slow'), []), CallTimeoutError)


# --- model calls of a segment ----------------------------------------------------

class TestCallRecords:
    def _run(self, tmp_path, replies, seg=None, **engine):
        seg = seg or _seg()
        ev = RunEvents(str(tmp_path), 'cli', run_id='r')
        fake = FakeBackend(replies)
        outcome = translate_segment(
            TranslationEngine(backend=fake, events=ev, effort='low', timeout=60,
                              wait=lambda *a: False, **engine), seg, [seg])
        return outcome, _of(_records(str(tmp_path)), 'call')

    def test_accepted_first(self, tmp_path):
        outcome, calls = self._run(tmp_path, [_reply('<p k="1">你好。</p>', Usage(120, 30))])
        assert outcome.calls == 1 and not outcome.split
        [c] = calls
        assert (c['kind'], c['attempt'], c['outcome'], c['segment']) == ('request', 1, 'accepted', 's1')
        assert (c['backend'], c['model'], c['effort'], c['timeout']) == ('fake', 'fake-model', 'low', 60)
        assert (c['tokens_in'], c['tokens_out']) == (120, 30)
        assert c['chars_out'] == len('<p k="1">你好。</p>') and c['chars_in'] > 0
        assert c['seconds'] >= 0 and 'issues' not in c

    def test_rejected_then_retry_accepted(self, tmp_path):
        outcome, calls = self._run(tmp_path, ['<p>a</p><p>b</p>', '<p k="1">你好。</p>'])
        assert outcome.status == SegmentStatus.TRANSLATED and outcome.calls == 2
        assert [(c['kind'], c['attempt'], c['outcome']) for c in calls] == [
            ('request', 1, 'rejected'), ('retry', 2, 'accepted')]
        assert calls[0]['issues'] and 'tokens_in' not in calls[0]  # no usage reported

    def test_split_halves(self, tmp_path):
        seg = _seg(html='<p>One.</p><p>Two.</p>', text='One. Two.')
        bad = '<p>x</p>'
        outcome, calls = self._run(tmp_path, [bad, bad, '<p k="1">一。</p>', '<p k="1">二。</p>'], seg=seg)
        assert outcome.status == SegmentStatus.TRANSLATED and outcome.split and outcome.calls == 4
        assert [c['kind'] for c in calls] == ['request', 'retry', 'split', 'split']
        assert [c['attempt'] for c in calls] == [1, 2, 3, 4]

    def test_timeout_then_accepted(self, tmp_path):
        outcome, calls = self._run(tmp_path, [CallTimeoutError('timed out after 60 s'),
                                              '<p k="1">你好。</p>'])
        assert [(c['kind'], c['outcome']) for c in calls] == [('request', 'timeout'),
                                                              ('request', 'accepted')]
        assert calls[0]['error'] == 'CallTimeoutError' and outcome.calls == 2

    def test_failure_records_codes_only(self, tmp_path):
        outcome, calls = self._run(tmp_path, [RefusalError(f'I will not say {REPLY_WORD}')])
        assert outcome.status == SegmentStatus.FAILED and outcome.calls == 1
        assert calls[0]['outcome'] == 'refusal' and calls[0]['error'] == 'RefusalError'
        assert REPLY_WORD not in open(events_path(str(tmp_path)), encoding='utf-8').read()

    @pytest.mark.parametrize('error, outcome', [
        (RateLimitError('r'), 'rate_limit'), (CancelledError('c'), 'cancelled')])
    def test_run_errors_recorded_then_raised(self, tmp_path, error, outcome):
        with pytest.raises(type(error)):
            self._run(tmp_path, [error])
        [c] = _of(_records(str(tmp_path)), 'call')
        assert c['outcome'] == outcome

    def test_truncated(self, tmp_path):
        _, calls = self._run(tmp_path, [_reply('<p k="1">你好。</p>', stop='truncated')])
        assert calls[0]['outcome'] == 'truncated'

    def test_unexpected_backend_error(self, tmp_path):
        outcome, calls = self._run(tmp_path, [KeyError('boom')])
        assert outcome.issues[0].code == 'unexpected'
        assert calls[0]['outcome'] == 'unexpected' and calls[0]['error'] == 'KeyError'

    def test_no_engine_record_without_events(self, tmp_path):
        fake = FakeBackend(['<p k="1">你好。</p>'])
        translate_segment(TranslationEngine(backend=fake), _seg(), [_seg()])
        assert not os.path.exists(events_path(str(tmp_path)))


class TestExtractionRecords:
    def test_batches_and_consolidation(self, tmp_path):
        ev = RunEvents(str(tmp_path), 'extract')
        replies = [json.dumps([{'term': TERM_WORD, 'translation': '羽筆', 'category': 'term'}]),
                   json.dumps([{'term': TERM_WORD, 'translation': '鵝毛筆', 'category': 'term'}]),
                   json.dumps([{'term': TERM_WORD, 'translation': '羽筆', 'category': 'term'}])]
        extractor = TermExtractor(model='m', backend=FakeBackend(replies), events=ev)
        segments = [{'text': f'{TERM_WORD} ' * 250}, {'text': f'{TERM_WORD} ' * 250}]
        extractor.extract_from_segments(segments)
        calls = _of(_records(str(tmp_path)), 'call')
        assert [c['kind'] for c in calls] == ['extract', 'extract', 'consolidate']
        assert all(c['outcome'] == 'accepted' and 'segment' not in c for c in calls)
        assert TERM_WORD not in open(events_path(str(tmp_path)), encoding='utf-8').read()

    def test_stop_recorded(self, tmp_path):
        ev = RunEvents(str(tmp_path), 'extract')
        extractor = TermExtractor(model='m', backend=FakeBackend([AuthError('x')]), events=ev)
        with pytest.raises(ExtractionError):
            extractor.extract_from_segments([{'text': 'Some words.'}])
        assert _of(_records(str(tmp_path)), 'call')[0]['outcome'] == 'auth'


# --- runs ---------------------------------------------------------------------------

def _no_book_text(project_dir):
    text = open(events_path(project_dir), encoding='utf-8').read()
    for word in (SOURCE_WORD, REPLY_WORD, TERM_WORD, 'Invented', 'Nobody'):
        assert word not in text, word


class TestWebRun:
    def test_run_records(self, tmp_path, fresh_tm):
        path = _make_project(tmp_path, n=3)
        fake = FakeBackend(responder=lambda r: like(r, REPLY_WORD))
        with use_fake_backend(fake):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path), num_workers=2)
            _wait(lambda: task.state == 'completed' and not task.threads_alive())
        records = _records(path)
        _wait(lambda: _of(_records(path), 'run_end'))
        records = _records(path)
        assert {r['run'] for r in records} == {task.events.run_id}
        assert all(r['source'] == 'web' for r in records)
        [start] = _of(records, 'run_start')
        assert (start['workers'], start['segments'], start['mode']) == (2, 3, 'pending')
        assert start['backend'] == 'fake'  # the default backend (none stored)
        assert len(_of(records, 'call')) == 3
        segs = _of(records, 'segment')
        assert sorted(s['segment'] for s in segs) == ['c1_seg0001', 'c1_seg0002', 'c1_seg0003']
        assert all(s['result'] == 'translated' and s['calls'] == 1 for s in segs)
        [end] = _of(records, 'run_end')
        assert (end['state'], end['translated'], end['failed'], end['dropped']) == (
            'completed', 3, 0, 0)
        _no_book_text(path)

    def test_pause_and_resume(self, tmp_path, fresh_tm):
        path = _make_project(tmp_path, n=2)
        gate = threading.Event()

        def responder(request):
            gate.wait(10)
            return like(request, '好')
        with use_fake_backend(FakeBackend(responder=responder)):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
            assert fresh_tm.pause_translation(PROJECT)
            assert fresh_tm.pause_translation(PROJECT)  # again: no second record
            assert fresh_tm.resume_translation(PROJECT)
            gate.set()
            _wait(lambda: task.state == 'completed' and not task.threads_alive())
        _wait(lambda: _of(_records(path), 'run_end'))
        records = _records(path)
        assert [r['reason'] for r in _of(records, 'pause')] == ['user']
        assert [r['reason'] for r in _of(records, 'resume')] == ['user']

    def test_rate_limit_pause(self, tmp_path, fresh_tm):
        path = _make_project(tmp_path, n=1)
        with use_fake_backend(FakeBackend([RateLimitError('limit')])):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
            _wait(lambda: task.state == 'paused')
            fresh_tm.stop_translation(PROJECT)
            _wait(lambda: not task.threads_alive())
        _wait(lambda: _of(_records(path), 'run_end'))
        records = _records(path)
        [pause] = _of(records, 'pause')
        assert pause['reason'] == 'rate_limit' and pause['resume_in'] == 300
        assert pause['error'] == 'RateLimitError'
        assert _of(records, 'call')[0]['outcome'] == 'rate_limit'
        assert _of(records, 'run_end')[0]['state'] == 'stopped'

    def test_dropped_when_edited_during_call(self, tmp_path, fresh_tm):
        path = _make_project(tmp_path, n=1)
        started, go = threading.Event(), threading.Event()

        def responder(request):
            started.set()
            go.wait(10)
            return like(request, '好')
        with use_fake_backend(FakeBackend(responder=responder)):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
            assert started.wait(10)
            with fresh_tm.transaction(PROJECT, str(tmp_path)) as pm:
                pm.update_translation('c1_seg0001', '人工譯文')
                pm.save()
            go.set()
            _wait(lambda: task.state == 'completed' and not task.threads_alive())
        _wait(lambda: _of(_records(path), 'run_end'))
        [seg] = _of(_records(path), 'segment')
        assert (seg['result'], seg['reason']) == ('dropped', 'edited_during_call')
        assert (seg['segment'], seg['calls']) == ('c1_seg0001', 1)  # the outcome's fields
        assert _of(_records(path), 'run_end')[0]['dropped'] == 1

    def test_retranslate_endpoint(self, tmp_path):
        path = _make_project(tmp_path, n=2)
        client = create_app(projects_dir=str(tmp_path)).test_client()
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, REPLY_WORD))):
            res = client.post(f'/api/projects/{PROJECT}/segments/c1_seg0002/retranslate')
        assert res.status_code == 200
        records = _records(path)
        assert [r['event'] for r in records] == ['run_start', 'call', 'segment', 'run_end']
        assert {r['source'] for r in records} == {'retranslate'}
        assert records[2]['segment'] == 'c1_seg0002' and records[2]['result'] == 'translated'
        assert records[3]['state'] == 'completed' and records[3]['translated'] == 1
        _no_book_text(path)

    def test_web_extraction(self, tmp_path):
        path = _make_project(tmp_path, n=2)
        client = create_app(projects_dir=str(tmp_path)).test_client()
        reply = json.dumps([{'term': TERM_WORD, 'translation': '羽筆', 'category': 'term'}])
        with use_fake_backend(FakeBackend([reply])):
            assert client.post(f'/api/projects/{PROJECT}/glossary/extract').status_code == 200
            _wait(lambda: client.get(f'/api/projects/{PROJECT}/glossary/extract/status')
                  .get_json()['status'] != 'running')
        _wait(lambda: _of(_records(path), 'run_end'))
        records = _records(path)
        assert [r['event'] for r in records] == ['run_start', 'call', 'run_end']
        assert records[2]['state'] == 'completed' and records[2]['added'] == 1
        _no_book_text(path)


class TestCliRun:
    def test_start_records(self, tmp_path):
        path = _make_project(tmp_path, n=2)
        fake = FakeBackend([_reply(f'<p k="1">{REPLY_WORD}</p>', Usage(10, 5)), RefusalError('no')])
        with use_fake_backend(fake):
            result = CliRunner().invoke(translate.cli, ['start', '-p', path])
        assert result.exit_code == 1  # one segment failed
        records = _records(path)
        assert [r['event'] for r in records] == ['run_start', 'call', 'segment', 'call',
                                                 'segment', 'run_end']
        assert {r['source'] for r in records} == {'cli'}
        assert [s['result'] for s in _of(records, 'segment')] == ['translated', 'failed']
        assert _of(records, 'segment')[1]['issues'] == ['refusal']
        end = _of(records, 'run_end')[0]
        assert (end['state'], end['translated'], end['failed']) == ('completed', 1, 1)
        _no_book_text(path)

    def test_parallel_start_records(self, tmp_path):
        path = _make_project(tmp_path, n=4)
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '好'))):
            result = CliRunner().invoke(translate.cli, ['start', '-p', path, '-w', '3'])
        assert result.exit_code == 0, result.output
        records = _records(path)
        assert _of(records, 'run_start')[0]['workers'] == 3
        assert len(_of(records, 'segment')) == 4 and len(_of(records, 'call')) == 4

    def test_stopped_by_auth_error(self, tmp_path):
        path = _make_project(tmp_path, n=2)
        with use_fake_backend(FakeBackend([AuthError('expired')])):
            result = CliRunner().invoke(translate.cli, ['start', '-p', path])
        assert result.exit_code == 1
        end = _of(_records(path), 'run_end')[0]
        assert (end['state'], end['error']) == ('stopped', 'AuthError')
        assert project_stats(path)['runs'][0]['error'] == 'AuthError'
        assert '（AuthError）' in '\n'.join(text_report(project_stats(path)))

    def test_ctrl_c_is_interrupted(self, tmp_path):
        path = _make_project(tmp_path, n=2)
        with use_fake_backend(FakeBackend(['<p k="1">好</p>', KeyboardInterrupt()])):
            result = CliRunner().invoke(translate.cli, ['start', '-p', path])
        # click turns Ctrl-C into Abort: "Aborted!" and exit status 1
        assert result.exit_code == 1 and result.output.endswith('Aborted!\n')
        end = _of(_records(path), 'run_end')[0]
        assert (end['state'], end['error'], end['translated']) == (
            'interrupted', 'KeyboardInterrupt', 1)
        assert _of(_records(path), 'call')[1]['outcome'] == 'cancelled'
        log = open(os.path.join(os.environ[proclog.LOG_DIR_ENV], 'cli.log'),
                   encoding='utf-8').read()
        assert 'interrupted (Ctrl-C)' in log
        assert 'end: exit 1 after' in log.rstrip().split('\n')[-1]

    def test_rate_limit_pause_and_resume(self, tmp_path, monkeypatch):
        path = _make_project(tmp_path, n=1)
        monkeypatch.setattr(translate, '_sleep', lambda s: None)
        with use_fake_backend(FakeBackend([RateLimitError('limit'), '<p k="1">好</p>'])):
            result = CliRunner().invoke(translate.cli, ['start', '-p', path])
        assert result.exit_code == 0, result.output
        records = _records(path)
        assert [r['event'] for r in records] == ['run_start', 'call', 'pause', 'resume', 'call',
                                                 'segment', 'run_end']
        assert _of(records, 'pause')[0]['reason'] == 'rate_limit'

    def test_cli_extract(self, tmp_path):
        path = _make_project(tmp_path, n=1)
        reply = json.dumps([{'term': TERM_WORD, 'translation': '羽筆', 'category': 'term'}])
        with use_fake_backend(FakeBackend([reply])):
            result = CliRunner().invoke(translate.cli, ['glossary', 'extract', '-p', path])
        assert result.exit_code == 0, result.output
        records = _records(path)
        assert [r['event'] for r in records] == ['run_start', 'call', 'run_end']
        assert records[2]['terms'] == 1
        _no_book_text(path)


# --- summary -------------------------------------------------------------------------

def _rec(event, run='r1', ts='2026-01-01T00:00:00.000Z', **fields):
    return {'v': 1, 'ts': ts, 'run': run, 'source': 'web', 'event': event, **fields}


def _call(segment, attempt, outcome='accepted', model='m1', seconds=1.0, **fields):
    return _rec('call', kind='request', segment=segment, attempt=attempt, backend='b',
                model=model, effort='low', outcome=outcome, seconds=seconds, **fields)


SAMPLE = [
    _rec('run_start', ts='2026-01-01T00:00:00.000Z', workers=2, segments=3),
    _call('s1', 1, seconds=2.0, tokens_in=100, tokens_out=40),
    _rec('segment', segment='s1', result='translated', calls=1, warnings=['length_ratio']),
    _call('s2', 1, 'rejected', seconds=4.0, issues=['tag_count']),
    _call('s2', 2, seconds=6.0),
    _rec('segment', segment='s2', result='translated', calls=2),
    _call('s3', 1, 'timeout', seconds=10.0),
    _rec('segment', segment='s3', result='failed', calls=4, issues=['transient']),
    _rec('pause', reason='user'),
    _rec('run_end', ts='2026-01-01T00:05:00.000Z', state='completed'),
    _call(None, 1, model='m2', run='r2', ts='2026-02-01T00:00:00.000Z'),
]


class TestSummary:
    def test_numbers(self):
        s = summarize(SAMPLE, skipped=2)
        assert s['records'] == len(SAMPLE) and s['skipped_lines'] == 2
        m1 = next(g for g in s['models'] if g['model'] == 'm1')
        assert m1['calls'] == 4 and m1['outcomes'] == {'accepted': 2, 'rejected': 1, 'timeout': 1}
        assert (m1['segments'], m1['first_attempt'], m1['more_calls'], m1['failed']) == (3, 1, 1, 1)
        assert m1['first_attempt_share'] == round(1 / 3, 3)
        # replies only (the timeout's 10 s is not a reply), nearest rank for both
        assert m1['seconds_median'] == 4.0 and m1['seconds_p90'] == 6.0
        assert (m1['seconds_calls'], m1['timeouts']) == (3, 1)
        assert (m1['tokens_in'], m1['tokens_out'], m1['calls_with_tokens']) == (100, 40, 1)
        m2 = next(g for g in s['models'] if g['model'] == 'm2')
        assert m2['tokens_in'] is None and m2['segments'] == 0
        assert s['rejected_issues'] == {'tag_count': 1}
        assert s['failure_issues'] == {'transient': 1}
        assert s['warnings'] == {'length_ratio': 1}
        r1, r2 = s['runs']
        assert (r1['run'], r1['duration'], r1['state'], r1['pauses']) == ('r1', 300.0, 'completed', 1)
        assert (r1['translated'], r1['failed'], r1['calls'], r1['workers']) == (2, 1, 4, 2)
        assert r2['state'] == 'unfinished' and r2['end'] is None and r1['error'] is None

    def test_filters(self):
        assert [r['run'] for r in summarize(SAMPLE, run='r2')['runs']] == ['r2']
        assert summarize(SAMPLE, since='2026-01-15T00:00:00.000Z')['records'] == 1

    def test_percentile(self):
        assert percentile([], 0.9) is None
        assert percentile([3, 1, 2], 0.5) == 2
        assert percentile(list(range(1, 11)), 0.9) == 9

    def test_parse_since(self):
        assert parse_since('2026-03-04T05:06:07+00:00') == '2026-03-04T05:06:07.000Z'
        with pytest.raises(ValueError):
            parse_since('yesterday')

    def test_unparsable_lines_skipped(self, tmp_path):
        path = events_path(str(tmp_path))
        os.makedirs(os.path.dirname(path))
        with open(path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(SAMPLE[0]) + '\nnot json\n[1]\n{"event": "x"}\n'
                    + json.dumps({**SAMPLE[1], 'v': 99}) + '\n\n' + json.dumps(SAMPLE[1]) + '\n')
            f.write('\xff\n')
        records, skipped = read_records(path)
        assert len(records) == 2 and skipped == 5
        with open(path, 'ab') as f:
            f.write(b'\xff\xfe\n')
        assert read_records(path)[1] == 6

    def test_text_report(self):
        text = '\n'.join(text_report({**summarize(SAMPLE, skipped=1), 'exists': True}))
        assert '略過 1 行' in text and 'b / m1 / low' in text and 'tag_count 1' in text
        assert '中位數 4.0 秒' in text and 'p90 6.0 秒' in text and '逾時 1 次' in text and '後端未回報' in text
        assert '未結束' in text


class TestStatsCommand:
    def _write(self, path, lines):
        os.makedirs(os.path.join(path, 'log'), exist_ok=True)
        with open(events_path(path), 'w', encoding='utf-8') as f:
            f.write(lines)

    def test_missing_record(self, tmp_path):
        path = _make_project(tmp_path)
        result = CliRunner().invoke(translate.cli, ['stats', '-p', path])
        assert result.exit_code == 0 and '還沒有事件紀錄' in result.output

    def test_empty_record(self, tmp_path):
        path = _make_project(tmp_path)
        self._write(path, '')
        result = CliRunner().invoke(translate.cli, ['stats', '-p', path])
        assert result.exit_code == 0 and '沒有符合條件' in result.output

    def test_json_and_text(self, tmp_path):
        path = _make_project(tmp_path)
        self._write(path, ''.join(json.dumps(r) + '\n' for r in SAMPLE) + '{"cut')
        result = CliRunner().invoke(translate.cli, ['stats', '-p', path, '--json'])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == project_stats(path) and data['skipped_lines'] == 1
        text = CliRunner().invoke(translate.cli, ['stats', '-p', path])
        assert text.exit_code == 0 and '執行紀錄' in text.output

    def test_run_and_since(self, tmp_path):
        path = _make_project(tmp_path)
        self._write(path, ''.join(json.dumps(r) + '\n' for r in SAMPLE))
        data = json.loads(CliRunner().invoke(
            translate.cli, ['stats', '-p', path, '--json', '--run', 'r2']).output)
        assert [r['run'] for r in data['runs']] == ['r2']
        data = json.loads(CliRunner().invoke(
            translate.cli, ['stats', '-p', path, '--json', '--since',
                            '2026-01-15T00:00:00+00:00']).output)
        assert data['records'] == 1
        bad = CliRunner().invoke(translate.cli, ['stats', '-p', path, '--since', 'soon'])
        assert bad.exit_code == 2


class TestStatsWeb:
    def test_endpoint_matches_cli(self, tmp_path):
        path = _make_project(tmp_path)
        os.makedirs(os.path.join(path, 'log'))
        with open(events_path(path), 'w', encoding='utf-8') as f:
            f.write(''.join(json.dumps(r) + '\n' for r in SAMPLE) + 'garbage\n')
        client = create_app(projects_dir=str(tmp_path)).test_client()
        res = client.get(f'/api/projects/{PROJECT}/stats')
        assert res.status_code == 200
        cli = json.loads(CliRunner().invoke(translate.cli, ['stats', '-p', path, '--json']).output)
        assert res.get_json() == cli and cli['skipped_lines'] == 1
        assert client.get(f'/api/projects/{PROJECT}/stats?run=r2').get_json()['runs'][0]['run'] == 'r2'
        assert client.get(f'/api/projects/{PROJECT}/stats?since=soon').status_code == 400
        assert client.get('/api/projects/none/stats').status_code == 404

    def test_page(self, tmp_path):
        _make_project(tmp_path)
        client = create_app(projects_dir=str(tmp_path)).test_client()
        page = client.get(f'/project/{PROJECT}/stats')
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert 'statsController' in html and f'/api/projects/{PROJECT}/stats' in html
        assert f'href="/project/{PROJECT}/stats"' in html  # the tab
        assert client.get(f'/project/{PROJECT}/overview').get_data(as_text=True).count(
            f'/project/{PROJECT}/stats') == 1
        assert client.get('/project/none/stats').status_code == 404
        empty = client.get(f'/api/projects/{PROJECT}/stats').get_json()
        assert empty['exists'] is False and empty['records'] == 0

    def test_while_a_task_runs(self, tmp_path, fresh_tm):
        path = _make_project(tmp_path, n=2)
        started, go = threading.Event(), threading.Event()

        def responder(request):
            started.set()
            go.wait(10)
            return like(request, '好')
        client = create_app(projects_dir=str(tmp_path)).test_client()
        with use_fake_backend(FakeBackend(responder=responder)):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
            assert started.wait(10)
            assert client.get(f'/project/{PROJECT}/stats').status_code == 200
            data = client.get(f'/api/projects/{PROJECT}/stats').get_json()
            assert data['runs'][0]['state'] == 'unfinished'
            go.set()
            _wait(lambda: task.state == 'completed' and not task.threads_alive())
        _wait(lambda: _of(_records(path), 'run_end'))
        assert client.get(f'/api/projects/{PROJECT}/stats').get_json()['runs'][0]['state'] \
            == 'completed'


# --- process log ----------------------------------------------------------------------

class TestProcessLog:
    @pytest.fixture
    def default_location(self, monkeypatch):
        monkeypatch.delenv(proclog.LOG_DIR_ENV, raising=False)
        yield
        proclog.teardown()

    def test_default_location_beside_projects(self, tmp_path, default_location):
        projects = tmp_path / 'projects'
        projects.mkdir()
        assert proclog.log_dir_for(str(projects)) == str(tmp_path / 'logs')
        create_app(projects_dir=str(projects))
        log = (tmp_path / 'logs' / 'web.log').read_text(encoding='utf-8')
        assert 'start: web server | epub-translator ' in log
        assert f'pid {os.getpid()}' in log and 'Python 3.' in log

    def test_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv(proclog.LOG_DIR_ENV, str(tmp_path / 'elsewhere'))
        assert proclog.log_dir_for('/any/projects') == str(tmp_path / 'elsewhere')

    def test_version_has_commit(self, monkeypatch):
        """The git describe output when there is one (asked once), the
        package version alone when git fails or cannot run."""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            out = ' M f\n' if 'status' in cmd else 'abc123def456\n'
            return subprocess.CompletedProcess(cmd, 0, out, '')
        monkeypatch.setattr(proclog, '_commit', None)
        monkeypatch.setattr(proclog.subprocess, 'run', fake_run)
        assert proclog.program_version() == '1.0.0 (abc123def456-dirty)'
        assert proclog.program_version() == '1.0.0 (abc123def456-dirty)'  # asked once
        assert len(calls) == 2 and calls[0][:3] == ['git', '-C', proclog._REPO]
        assert 'describe' in calls[0] and '--dirty' not in calls[0] and 'status' in calls[1]
        monkeypatch.setattr(proclog, '_commit', None)
        monkeypatch.setattr(proclog.subprocess, 'run', lambda cmd, **k: subprocess.CompletedProcess(
            cmd, 128, '', 'not a git repository'))
        assert proclog.program_version() == '1.0.0'

        def missing(cmd, **kwargs):
            raise FileNotFoundError('git')
        monkeypatch.setattr(proclog, '_commit', None)
        monkeypatch.setattr(proclog.subprocess, 'run', missing)
        assert proclog.program_version() == '1.0.0'

    def test_cli_lines_and_traceback(self, tmp_path, default_location, monkeypatch):
        projects = tmp_path / 'projects'
        path = _make_project(projects)

        def explode(*a, **k):
            raise ZeroDivisionError('invented failure')
        monkeypatch.setattr(translate, '_validate', explode)
        result = CliRunner().invoke(translate.cli, ['validate', path])
        assert isinstance(result.exception, ZeroDivisionError)
        CliRunner().invoke(translate.cli, ['brief', 'set', f'{SOURCE_WORD} brief', '-p', path])
        log = (tmp_path / 'logs' / 'cli.log').read_text(encoding='utf-8')
        assert 'start: cli validate project_dir=' in log
        assert 'Traceback' in log and 'ZeroDivisionError: invented failure' in log
        assert 'end: exit 1 after' in log
        assert 'start: cli brief set ' in log and 'text=<16 字元>' in log
        assert SOURCE_WORD not in log
        assert 'end: exit 0 after' in log

    def test_request_traceback(self, tmp_path, monkeypatch):
        logfile = os.path.join(os.environ[proclog.LOG_DIR_ENV], 'web.log')
        _make_project(tmp_path)
        app = create_app(projects_dir=str(tmp_path))
        from src.web.blueprints import stats as stats_module

        def explode(*a, **k):
            raise ZeroDivisionError('invented request failure')
        monkeypatch.setattr(stats_module, 'project_stats', explode)
        assert app.test_client().get(f'/api/projects/{PROJECT}/stats').status_code == 500
        log = open(logfile, encoding='utf-8').read()
        assert 'ZeroDivisionError: invented request failure' in log and 'Traceback' in log

    def test_thread_exception_logged(self, monkeypatch):
        """The hook setup() installs logs a thread's uncaught exception, then
        hands it on (here to a no-op instead of the default printer)."""
        import types
        logfile = os.path.join(os.environ[proclog.LOG_DIR_ENV], 'web.log')
        create_app(projects_dir='/nowhere/projects')
        handed = []
        monkeypatch.setattr(proclog, '_previous_excepthook', handed.append)
        try:
            raise ZeroDivisionError('invented thread failure')
        except ZeroDivisionError as e:
            args = types.SimpleNamespace(exc_type=type(e), exc_value=e,
                                         exc_traceback=e.__traceback__,
                                         thread=types.SimpleNamespace(name='invented-thread'))
        proclog._thread_excepthook(args)
        log = open(logfile, encoding='utf-8').read()
        assert 'Uncaught exception in thread invented-thread' in log
        assert 'ZeroDivisionError: invented thread failure' in log
        assert handed == [args]

    def test_rotation_bound(self, tmp_path, default_location, monkeypatch):
        monkeypatch.setattr(proclog, 'MAX_BYTES', 2000)
        proclog.setup(str(tmp_path / 'projects'), 'web', 'web server')
        for i in range(200):
            logging.getLogger('src.test').info('line %d %s', i, 'z' * 50)
        files = sorted(os.listdir(tmp_path / 'logs'))
        assert files[0] == 'web.log' and len(files) == 1 + proclog.BACKUP_COUNT
        assert all(os.path.getsize(tmp_path / 'logs' / f) <= 2200 for f in files)

    def test_unopenable_file_goes_on(self, tmp_path, monkeypatch, capsys):
        (tmp_path / 'logs').write_text('a file where the directory should be')
        monkeypatch.setenv(proclog.LOG_DIR_ENV, str(tmp_path / 'logs'))
        proclog.teardown()
        assert proclog.setup('/x/projects', 'cli', 'cli status') is None
        assert '無法開啟紀錄檔' in capsys.readouterr().err


class TestRunRecordsCheckScript:
    """scripts/verify/run_records_check.py: passes a clean record, fails on
    text in it and on counts that do not add up."""

    def _check(self, projects):
        import importlib.util
        path = os.path.join(_REPO_ROOT, 'scripts', 'verify', 'run_records_check.py')
        # under its module path, so the mutation run links these tests to it
        spec = importlib.util.spec_from_file_location('scripts.verify.run_records_check', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.main(['--projects-dir', str(projects)])

    def test_clean_record_passes(self, tmp_path, capsys):
        path = _make_project(tmp_path, n=2)
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, REPLY_WORD))):
            assert CliRunner().invoke(translate.cli, ['start', '-p', path]).exit_code == 0
        assert self._check(tmp_path) == 0
        assert 'PASS' in capsys.readouterr().out

    @pytest.mark.parametrize('record', [
        {'outcome': 'accepted', 'note': 'x'},              # unknown key
        {'outcome': f'{SOURCE_WORD} said'},                # text in a value
        {'issues': [f'{SOURCE_WORD.lower()}']},            # a source word as a code
    ])
    def test_text_fails(self, tmp_path, capsys, record):
        path = _make_project(tmp_path, n=1)
        RunEvents(path, 'cli', run_id='20260101T000000Z-abcdef').emit('call', **record)
        assert self._check(tmp_path) == 1

    def test_counts_must_add_up(self, tmp_path):
        path = _make_project(tmp_path, n=1)
        ev = RunEvents(path, 'cli', run_id='20260101T000000Z-abcdef')
        ev.emit('run_end', state='completed', translated=2, failed=0)
        assert self._check(tmp_path) == 1


class TestReplacedProject:
    def test_old_record_set_aside(self, tmp_path, sample_epub):
        client = create_app(projects_dir=str(tmp_path)).test_client()

        def create():
            res = client.post('/api/projects', json={'epub_path': sample_epub})
            assert res.status_code == 200, res.get_json()
            return os.path.join(str(tmp_path), res.get_json()['name'])
        path = create()
        RunEvents(path, 'cli', run_id='old').emit('run_start', segments=9)
        old = open(events_path(path), encoding='utf-8').read()
        assert create() == path  # the same name: replaced
        assert not os.path.exists(events_path(path))
        [aside] = [n for n in os.listdir(os.path.join(path, 'log'))
                   if n.startswith('events-') and n.endswith('.jsonl')]
        assert open(os.path.join(path, 'log', aside), encoding='utf-8').read() == old
        assert client.get(f'/api/projects/{os.path.basename(path)}/stats').get_json()['records'] == 0
        create()  # replaced again without a record: nothing to set aside
        assert len([n for n in os.listdir(os.path.join(path, 'log'))
                    if n.startswith('events-')]) == 1

    def test_set_aside_without_record(self, tmp_path):
        assert events.set_aside(str(tmp_path)) is None


def test_web_server_end_line(tmp_path):
    """A web server process that exits normally writes its end line."""
    code = ('import sys; sys.path.insert(0, sys.argv[1])\n'
            'from src.web.server import create_app\n'
            'create_app(projects_dir=sys.argv[2])\n')
    env = dict(os.environ, **{proclog.LOG_DIR_ENV: str(tmp_path / 'logs')})
    subprocess.run([sys.executable, '-c', code, _REPO_ROOT, str(tmp_path / 'projects')],
                   env=env, check=True, timeout=60)
    log = (tmp_path / 'logs' / 'web.log').read_text(encoding='utf-8')
    lines = log.splitlines()
    assert 'start: web server' in lines[0] and 'end: web server stopped' in lines[-1]



# A record that reaches every branch of summarize (a split segment, a
# dropped one, a failure whose calls are not in the selection, an unknown
# result, a bool duration, a non-text issue code, tokens out only)
RICH = [
    _rec('run_start', run='a', ts='2026-03-01T10:00:00.000Z', workers=3, segments=4,
         backend='b', model='m1', effort='high'),
    _call('s1', 1, run='a', seconds=2.0, tokens_out=7),
    _call('s1', 2, 'rejected', run='a', seconds=True, issues=['tag_count', 5]),
    _call('s1', 3, run='a', seconds=4.0),
    _rec('segment', run='a', segment='s1', result='translated', calls=3, split=True),
    _rec('segment', run='a', segment='s2', result='dropped', reason='edited_before_call'),
    _rec('segment', run='a', segment='s9', result='failed', calls=0, model='m3',
         issues=['not_html']),
    _rec('segment', run='a', segment='s8', result='skipped'),
    _rec('pause', run='a', reason='rate_limit'),
    _rec('wait_extended', run='a'),
    _rec('resume', run='a', reason='auto'),
    _rec('run_end', run='a', ts='2026-03-01T10:01:30.500Z', state='stopped',
         error='AuthError'),
]

RICH_SUMMARY = {'records': 12,
 'skipped_lines': 0,
 'since': None,
 'run': None,
 'models': [{'work': 'translation',
             'backend': '?',
             'model': 'm3',
             'effort': None,
             'calls': 0,
             'outcomes': {},
             'issues': {},
             'tokens_in': None,
             'tokens_out': None,
             'calls_with_tokens': 0,
             'segments': 1,
             'first_attempt': 0,
             'more_calls': 0,
             'split': 0,
             'failed': 1,
             'seconds_calls': 0,
             'timeouts': 0,
             'seconds_median': None,
             'seconds_p90': None,
             'first_attempt_share': 0.0,
             'more_calls_share': 0.0,
             'failed_share': 1.0},
            {'work': 'translation',
             'backend': 'b',
             'model': 'm1',
             'effort': 'low',
             'calls': 3,
             'outcomes': {'accepted': 2, 'rejected': 1},
             'issues': {'tag_count': 1},
             'tokens_in': 0,
             'tokens_out': 7,
             'calls_with_tokens': 1,
             'segments': 1,
             'first_attempt': 0,
             'more_calls': 1,
             'split': 1,
             'failed': 0,
             'seconds_calls': 2,
             'timeouts': 0,
             'seconds_median': 2.0,
             'seconds_p90': 4.0,
             'first_attempt_share': 0.0,
             'more_calls_share': 1.0,
             'failed_share': 0.0}],
 'rejected_issues': {'tag_count': 1},
 'failure_issues': {'not_html': 1},
 'warnings': {},
 'runs': [{'run': 'a',
           'source': 'web',
           'start': '2026-03-01T10:00:00.000Z',
           'end': '2026-03-01T10:01:30.500Z',
           'state': 'stopped',
           'workers': 3,
           'segments': 4,
           'translated': 1,
           'failed': 1,
           'dropped': 1,
           'pauses': 1,
           'calls': 3,
           'backend': 'b',
           'model': 'm1',
           'effort': 'high',
           'error': 'AuthError',
           'duration': 90.5}]}

RICH_TEXT = ('事件紀錄：12 筆\n'
 '\n'
 '依後端／模型／推理強度：\n'
 '  翻譯：? / m3 / -\n'
 '    模型呼叫 0 次：無\n'
 '    段落 1：首次即通過 0（0.0%），需多次呼叫 0（0.0%；其中分割 0），最終失敗 1（100.0%）\n'
 '    回覆耗時（0 次有回覆的呼叫）：中位數 -，p90 -\n'
 '    tokens：後端未回報\n'
 '  翻譯：b / m1 / low\n'
 '    模型呼叫 3 次：accepted 2、rejected 1\n'
 '    段落 1：首次即通過 0（0.0%），需多次呼叫 1（100.0%；其中分割 1），最終失敗 0（0.0%）\n'
 '    回覆被退回的原因：tag_count 1\n'
 '    回覆耗時（2 次有回覆的呼叫）：中位數 2.0 秒，p90 4.0 秒\n'
 '    tokens：輸入 0，輸出 7（1 次呼叫有回報）\n'
 '\n'
 '問題代碼（依次數）：\n'
 '  回覆被退回：tag_count 1\n'
 '  段落最終失敗：not_html 1\n'
 '  已翻譯段落的警告：無\n'
 '\n'
 '執行紀錄：\n'
 '  a  web  2026-03-01 10:00:00 → 2026-03-01 10:01:30（90.5 秒）  停止（AuthError）\n'
 '    已翻譯 1、失敗 1、捨棄 1、暫停 1 次、模型呼叫 3 次（共 4 段待處理）')


@pytest.fixture
def utc(monkeypatch):
    """Local time is UTC (text_report prints local times)."""
    monkeypatch.setenv('TZ', 'UTC')
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


class TestWholeSummary:
    def test_summarize(self):
        assert summarize(RICH) == RICH_SUMMARY

    def test_text_report(self, utc):
        assert '\n'.join(text_report({**summarize(RICH), 'exists': True})) == RICH_TEXT


def _limited_twice(replies_after):
    """A responder whose first two calls meet (both in flight) and then
    raise RateLimitError; later calls answer like() the request."""
    barrier = threading.Barrier(2, timeout=10)
    count = {'n': 0}
    lock = threading.Lock()

    def respond(request):
        with lock:
            count['n'] += 1
            n = count['n']
        if n <= 2:
            barrier.wait()
            raise RateLimitError('limit')
        return like(request, replies_after)
    return respond


class TestMorePauseRecords:
    def test_cli_parallel_rate_limit(self, tmp_path, monkeypatch):
        path = _make_project(tmp_path, n=2)
        now = {'t': 0.0}

        def clock():  # every look at the clock is 200 s later
            now['t'] += 200.0
            return now['t']
        monkeypatch.setattr(translate, '_clock', clock)
        with use_fake_backend(FakeBackend(responder=_limited_twice('好'))):
            result = CliRunner().invoke(translate.cli, ['start', '-p', path, '-w', '2'])
        assert result.exit_code == 0, result.output
        records = _records(path)
        events_ = [r['event'] for r in records if r['event'] != 'call']
        assert events_[:4] == ['run_start', 'pause', 'wait_extended', 'resume']
        pause, extended = _of(records, 'pause')[0], _of(records, 'wait_extended')[0]
        assert (pause['reason'], pause['error'], pause['resume_in']) == (
            'rate_limit', 'RateLimitError', 300.0)
        assert extended['reason'] == 'rate_limit' and extended['resume_in'] > 0
        assert _of(records, 'resume')[0]['reason'] == 'auto'
        assert [c['outcome'] for c in _of(records, 'call')][:2] == ['rate_limit'] * 2
        assert _of(records, 'run_end')[0]['translated'] == 2

    def test_web_wait_extended(self, tmp_path, fresh_tm):
        path = _make_project(tmp_path, n=2)
        with use_fake_backend(FakeBackend(responder=_limited_twice('好'))):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path), num_workers=2)
            _wait(lambda: len(_of(_records(path), 'call')) == 2)
            # the second worker's rate limit extends the wait: not before
            # it is recorded may the run stop (it could stop first)
            _wait(lambda: _of(_records(path), 'wait_extended'))
            _wait(lambda: task.state == 'paused')
            fresh_tm.stop_translation(PROJECT)
            _wait(lambda: not task.threads_alive())
        _wait(lambda: _of(_records(path), 'run_end'))
        records = _records(path)
        assert [r['reason'] for r in _of(records, 'pause')] == ['rate_limit']
        [extended] = _of(records, 'wait_extended')
        assert extended['reason'] == 'rate_limit' and 0 < extended['resume_in'] <= 300

    @pytest.mark.parametrize('error, reason', [
        (AuthError('expired'), 'auth'),
        (BackendUnavailableError('missing'), 'backend_unavailable')])
    def test_web_pause_for_user_errors(self, tmp_path, fresh_tm, error, reason):
        path = _make_project(tmp_path, n=1)
        with use_fake_backend(FakeBackend([error])):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
            _wait(lambda: task.state == 'paused')
            fresh_tm.stop_translation(PROJECT)
            _wait(lambda: not task.threads_alive())
        _wait(lambda: _of(_records(path), 'run_end'))
        records = _records(path)
        [pause] = _of(records, 'pause')
        assert (pause['reason'], pause['error'], pause['completed']) == (
            reason, type(error).__name__, 0)
        assert 'resume_in' not in pause  # waits for the user
        end = _of(records, 'run_end')[0]
        assert (end['state'], end['translated'], end['failed'], end['dropped']) == (
            'stopped', 0, 0, 0)

    def test_user_resume_records_workers(self, tmp_path, fresh_tm):
        path = _make_project(tmp_path, n=1)
        with use_fake_backend(FakeBackend([AuthError('expired'), '<p k="1">好</p>'])):
            task = fresh_tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
            _wait(lambda: task.state == 'paused')
            assert fresh_tm.resume_translation(PROJECT, num_workers=3)
            _wait(lambda: task.state == 'completed' and not task.threads_alive())
        _wait(lambda: _of(_records(path), 'run_end'))
        [resume] = _of(_records(path), 'resume')
        assert (resume['reason'], resume['workers']) == ('user', 3)


class TestExtractionCallFields:
    def test_every_field(self, tmp_path, monkeypatch):
        ev = RunEvents(str(tmp_path), 'extract', run_id='x')
        clock = iter([100.0, 102.5])
        monkeypatch.setattr('src.glossary.term_extractor.time.monotonic', lambda: next(clock))
        reply = _reply('[]', Usage(11, 3), stop='truncated')
        extractor = TermExtractor(model='m9', backend=FakeBackend([reply]), effort='high',
                                  timeout=77, events=ev)
        assert extractor._call('prompt of 19 chars.', 'consolidate') == '[]'
        extractor._parsed(True)  # recorded once its reply is read
        [c] = _of(_records(str(tmp_path)), 'call')
        assert {k: v for k, v in c.items() if k != 'ts'} == {
            'v': 1, 'run': 'x', 'source': 'extract', 'event': 'call', 'kind': 'consolidate',
            'backend': 'fake', 'model': 'm9', 'effort': 'high', 'timeout': 77,
            'seconds': 2.5, 'tokens_in': 11, 'tokens_out': 3, 'outcome': 'truncated',
            'chars_in': 19, 'chars_out': 2}

    def test_error_fields(self, tmp_path):
        ev = RunEvents(str(tmp_path), 'extract', run_id='x')
        extractor = TermExtractor(model='m9', backend=FakeBackend([CallTimeoutError('slow')]),
                                  events=ev)
        assert extractor._call('abc') is None  # logged, not raised
        [c] = _of(_records(str(tmp_path)), 'call')
        assert (c['kind'], c['outcome'], c['error'], c['chars_in']) == (
            'extract', 'timeout', 'CallTimeoutError', 3)
        assert 'chars_out' not in c and 'tokens_in' not in c

    def test_no_events_no_record(self, tmp_path):
        extractor = TermExtractor(model='m9', backend=FakeBackend(['[]']))
        assert extractor._call('abc') == '[]'
        assert not os.path.exists(events_path(str(tmp_path)))

    def test_recorder_failure_does_not_stop_extraction(self, tmp_path, monkeypatch):
        ev = RunEvents(str(tmp_path), 'extract')
        monkeypatch.setattr(ev, 'call', lambda **k: 1 / 0)
        extractor = TermExtractor(model='m9', backend=FakeBackend(['[]']), events=ev)
        assert extractor._call('abc') == '[]'


class TestWholeRunLines:
    def _strip(self, record):
        return {k: v for k, v in record.items() if k not in ('ts', 'run', 'seconds')}

    def test_cli_run_start_and_end(self, tmp_path):
        path = _make_project(tmp_path, n=2)
        with use_fake_backend(FakeBackend(['<p k="1">好</p>', RefusalError('no')])):
            CliRunner().invoke(translate.cli, ['start', '-p', path, '--retry-failed'])
        records = _records(path)
        assert self._strip(records[0]) == {
            'v': 1, 'source': 'cli', 'event': 'run_start', 'workers': 1, 'segments': 2,
            'mode': 'retry_failed', 'backend': 'fake', 'model': 'sonnet', 'timeout': 180}
        assert self._strip(records[-1]) == {
            'v': 1, 'source': 'cli', 'event': 'run_end', 'state': 'completed',
            'translated': 1, 'failed': 1}
        assert records[-1]['seconds'] >= 0
        assert self._strip(_of(records, 'segment')[1]) == {
            'v': 1, 'source': 'cli', 'event': 'segment', 'segment': 'c1_seg0002',
            'result': 'failed', 'model': 'sonnet', 'calls': 1, 'issues': ['refusal']}

    def test_cli_streak_stop(self, tmp_path):
        path = _make_project(tmp_path, n=6)
        with use_fake_backend(FakeBackend([KeyError('x')] * 5)):
            result = CliRunner().invoke(translate.cli, ['start', '-p', path])
        assert result.exit_code == 1
        end = _of(_records(path), 'run_end')[0]
        assert (end['state'], end['error'], end['failed']) == ('stopped', 'UnexpectedStreak', 5)

    def test_set_aside_name(self, tmp_path):
        ev = RunEvents(str(tmp_path), 'cli')
        ev.emit('pause')
        target = events.set_aside(str(tmp_path))
        import re
        assert re.fullmatch(r'events-\d{8}T\d{12}Z\.jsonl', os.path.basename(target))
        assert os.path.dirname(target) == os.path.join(str(tmp_path), 'log')
        assert len(read_records(target)[0]) == 1

    def test_thread_hook_ignores_system_exit(self, monkeypatch, caplog):
        import types
        handed = []
        monkeypatch.setattr(proclog, '_previous_excepthook', handed.append)
        args = types.SimpleNamespace(exc_type=SystemExit, exc_value=SystemExit(0),
                                     exc_traceback=None, thread=None)
        with caplog.at_level(logging.ERROR):
            proclog._thread_excepthook(args)
        assert not caplog.records and handed == [args]


class TestCheapKills:
    """Mutants that survived the second mutation pass and are cheap to kill."""

    def test_record_line_is_compact_utc(self, tmp_path, monkeypatch):
        monkeypatch.setenv('TZ', 'Asia/Taipei')
        time.tzset()
        try:
            import datetime as dt
            before = dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None)
            ev = RunEvents(str(tmp_path), 'cli')
            ev.emit('pause', reason='user')
            line = open(events_path(str(tmp_path)), encoding='utf-8').read()
            stamp = json.loads(line)['ts']
            run_stamp = dt.datetime.strptime(ev.run_id.split('-')[0], '%Y%m%dT%H%M%SZ')
            aside = events.set_aside(str(tmp_path))
        finally:
            monkeypatch.undo()
            time.tzset()
        assert '"event":"pause","reason":"user"' in line  # no spaces
        ts = dt.datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%S.%fZ')
        assert abs((ts - before).total_seconds()) < 5          # UTC, not local (+8 h)
        assert abs((run_stamp - before).total_seconds()) < 5
        aside_stamp = dt.datetime.strptime(os.path.basename(aside)[7:22], '%Y%m%dT%H%M%S')
        assert abs((aside_stamp - before).total_seconds()) < 5

    def test_call_fields_and_settings(self, tmp_path):
        ev = RunEvents(str(tmp_path), 'cli', run_id='r')
        ev.call(kind='request', backend='b', model='m', effort=None, timeout=5,
                seconds=1.23456, outcome='accepted', warnings=['length_ratio'])
        [c] = _of(_records(str(tmp_path)), 'call')
        assert c['seconds'] == 1.235 and c['warnings'] == ['length_ratio']
        assert 'issues' not in c and 'effort' not in c
        from src.core.project import CallSettings
        assert events.settings_fields(CallSettings('copilot-sdk', 'mx', 'high', 60)) == {
            'backend': 'copilot-sdk', 'model': 'mx', 'effort': 'high', 'timeout': 60}

    def test_read_records_field_checks(self, tmp_path):
        path = events_path(str(tmp_path))
        os.makedirs(os.path.dirname(path))
        good = {'ts': 't', 'run': 'r', 'event': 'pause'}  # no v: version 1
        lines = [good, {**good, 'run': 1}, {**good, 'ts': None}, {**good, 'event': 2},
                 {**good, 'v': 2}]
        with open(path, 'w', encoding='utf-8') as f:
            f.write(''.join(json.dumps(r) + '\n' for r in lines))
        records, skipped = read_records(path)
        assert records == [good] and skipped == 4

    def test_summarize_counts_add_up(self):
        twice = RICH + [{**r, 'run': 'b'} for r in RICH] + [
            _rec('pause', run='a', reason='user')]
        s = summarize(twice)
        m1 = next(g for g in s['models'] if g['model'] == 'm1')
        assert (m1['calls'], m1['more_calls'], m1['split'], m1['calls_with_tokens'],
                m1['tokens_out']) == (6, 2, 2, 2, 14)
        assert next(g for g in s['models'] if g['model'] == 'm3')['failed'] == 2
        assert [r['pauses'] for r in s['runs']] == [2, 1]
        both = summarize([_call('s', 1, model=None, tokens_in=1),
                          {**_call('s', 1, tokens_in=2), 'backend': None, 'effort': None},
                          _rec('segment', segment='x', result='translated', calls=1)])
        assert [(g['backend'], g['model'], g['effort'], g['first_attempt'])
                for g in both['models']] == [('?', '?', None, 1), ('?', 'm1', None, 0),
                                             ('b', '?', 'low', 0)]
        mixed = summarize([_call('s', 1, seconds=1.23456),
                           {**_call('s', 1), 'effort': None}])
        assert [g['effort'] for g in mixed['models']] == [None, 'low']
        assert mixed['models'][1]['seconds_median'] == 1.23456

    def test_since_is_inclusive_and_unfinished_duration(self):
        records = [_rec('run_start', ts='2026-01-01T00:00:00.000Z'),
                   _rec('pause', ts='2026-01-01T00:00:10.000Z')]
        s = summarize(records, since='2026-01-01T00:00:00.000Z')
        assert s['records'] == 2 and s['runs'][0]['duration'] == 10.0
        # without its run_start (review round 3): no start, no duration
        assert summarize(records[1:])['runs'][0]['duration'] is None

    def test_report_helpers_and_states(self, utc):
        from src.core import stats as st
        assert st._local(None) == '' and st._pct(None) == '-'
        r = {**summarize(SAMPLE), 'exists': True}
        r['runs'][0]['state'] = 'weird'
        text = '\n'.join(text_report(r))
        assert '（300.0 秒）  weird\n' in text           # unknown state shown as is, no error
        assert '（開始不在所選紀錄中） → -（-）  未結束（執行中或程序已終止）\n' in text
        assert text.endswith('模型呼叫 1 次')            # no segment count without run_start

    def test_empty_reports(self):
        assert text_report({'records': 0, 'skipped_lines': 0, 'exists': False}) == [
            '此專案還沒有事件紀錄（log/events.jsonl）：翻譯或提取術語後才會產生。']
        assert text_report({'records': 0, 'skipped_lines': 3}) == [
            '事件紀錄中沒有符合條件的紀錄。', '略過 3 行無法解析的紀錄。']

    def test_extractor_logs_a_record_failure(self, tmp_path, monkeypatch, caplog):
        ev = RunEvents(str(tmp_path), 'extract')
        monkeypatch.setattr(ev, 'call', lambda **k: 1 / 0)
        extractor = TermExtractor(model='m9', backend=FakeBackend(['[]']), events=ev)
        with caplog.at_level(logging.WARNING, logger='src.glossary.term_extractor'):
            extractor._call('abc')
            extractor._parsed(True)
        [rec] = caplog.records
        assert rec.getMessage() == '無法記錄模型呼叫' and rec.exc_info[0] is ZeroDivisionError


class TestCheckScriptOutput:
    def _main(self, argv):
        import importlib.util
        path = os.path.join(_REPO_ROOT, 'scripts', 'verify', 'run_records_check.py')
        spec = importlib.util.spec_from_file_location('scripts.verify.run_records_check', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.main(argv)

    def test_whole_output(self, tmp_path, capsys):
        clean = _make_project(tmp_path, name='a-clean')
        RunEvents(clean, 'cli', run_id='20260101T000000Z-abcdef').emit(
            'segment', segment='c1_seg0001', result='translated')
        bad = _make_project(tmp_path, name='b-bad')
        ev = RunEvents(bad, 'cli', run_id='20260101T000000Z-abcdef')
        ev.emit('segment', segment='nowhere', result='failed', issues=['zorblaxian'])
        ev.emit('run_end', state='completed', translated=1)
        _make_project(tmp_path, name='c-none')
        assert self._main(['--projects-dir', str(tmp_path), '--anonymize']) == 1
        assert capsys.readouterr().out == (
            'project-1: PASS  records 1, skipped lines 0, runs 1\n'
            'project-2: FAIL  records 2, skipped lines 0, runs 1\n'
            '  line 1: issues: a string that is not a known value (10 chars)\n'
            '  run 1: run_end translated=1, segment records 0\n'
            '  run 1: run_end failed=0, segment records 1\n'
            '  1 records name a segment id not in project.json\n'
            'project-3: no event record\n'
            'FAIL\n')
        assert self._main(['--projects-dir', str(tmp_path)]) == 1
        assert capsys.readouterr().out.startswith('a-clean: PASS')

    def test_clean_and_missing(self, tmp_path, capsys):
        clean = _make_project(tmp_path, name='a')
        RunEvents(clean, 'cli', run_id='20260101T000000Z-abcdef').emit('pause', reason='user')
        assert self._main(['--projects-dir', str(tmp_path)]) == 0
        assert capsys.readouterr().out == (
            'a: PASS  records 1, skipped lines 0, runs 1\nPASS\n')
        assert self._main(['--projects-dir', str(tmp_path / 'missing')]) == 2
        assert capsys.readouterr().out == (
            'cannot list the projects directory: FileNotFoundError\n')


class TestLastSurvivors:
    def test_segment_record_split_and_warnings(self, tmp_path):
        from src.core.project import Issue
        from src.core.translator import TranslationOutcome
        ev = RunEvents(str(tmp_path), 'cli', run_id='r')
        outcome = TranslationOutcome(
            text='好', status=SegmentStatus.TRANSLATED, model='m', usage=None, calls=4,
            split=True, issues=[Issue(kind='quality', code='length_ratio', detail='d',
                                      blocking=False)])
        events.segment_result(ev, 's1', outcome)
        [s] = _of(_records(str(tmp_path)), 'segment')
        assert {k: v for k, v in s.items() if k != 'ts'} == {
            'v': 1, 'run': 'r', 'source': 'cli', 'event': 'segment', 'segment': 's1',
            'result': 'translated', 'model': 'm', 'calls': 4, 'split': True,
            'warnings': ['length_ratio']}

    def test_file_mode_and_nan(self, tmp_path, caplog):
        path = events_path(str(tmp_path))
        old = os.umask(0)
        try:
            assert append_record(path, {'event': 'pause'})
        finally:
            os.umask(old)
        assert os.stat(path).st_mode & 0o777 == 0o644
        with caplog.at_level(logging.WARNING, logger='src.core.events'):
            assert append_record(path, {'seconds': float('nan')}) is False
        [rec] = caplog.records
        assert rec.getMessage() == '無法寫入事件紀錄 (ValueError): Out of range float values are not JSON compliant: nan'
