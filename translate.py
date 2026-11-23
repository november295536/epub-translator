"""CLI entry point for the EPUB translator."""
# Keep this file parseable by Python 3.9, so an old interpreter reaches the version message.

from __future__ import annotations

import os
import shutil
import sys
import time
from collections import Counter

from src import __version__  # first: stops an unsupported Python (src/__init__.py)

import json
import logging

import click

import src.llm as llm

from src.epub.extractor import EPUBError, EPUBExtractor
from src.core.segmenter import Segmenter, SegmentType
from src.core.project import (
    MAX_TIMEOUT, MIN_TIMEOUT, ProjectLock, ProjectLockedError, ProjectManager, SegmentStatus,
    SettingError, effort_label, effort_levels, effort_reset_note, sha256_file, source_epub_path,
)
from src.core.runcontrol import DebouncedSaver, resume_delay
from src.core.translator import (
    AuthError, BackendUnavailableError, CancelledError, RateLimitError, TranslationEngine,
    available_models, UnexpectedStreak, record_failure_log, translate_segment,
)
from src.glossary.glossary_manager import CATEGORY_LABELS, GlossaryManager, load_glossary
from src.core.migrate import MigrationError, migrate_project
from src.core import proclog
from src.core.events import RunEvents, segment_result, settings_fields
from src.core.stats import parse_since, project_stats, text_report
from src.core.validation import check_segment
from src.epub.exporter import SCHEMA_1_MESSAGE, ExportError, export_project


def _sanitize_name(name: str) -> str:
    """Sanitize a string into a safe project directory name."""
    import re
    name = name.replace(' ', '-').replace('_', '-')
    name = re.sub(r'[^a-zA-Z0-9\-.]', '', name)
    name = re.sub(r'-{2,}', '-', name)
    name = name.strip('-.')
    return name or 'untitled'


def _get_project_dir(epub_path: str) -> str:
    """Derive project directory from EPUB filename."""
    base = os.path.splitext(os.path.basename(epub_path))[0]
    return os.path.join('projects', _sanitize_name(base))


def _get_glossary_path(project_dir: str) -> str:
    """Get the glossary file path for a project."""
    return os.path.join(project_dir, 'glossary.json')


logger = logging.getLogger('src.cli')

# Parameters whose value is book text or a glossary entry: the process log
# gets their length only
_TEXT_PARAMS = frozenset({'text', 'term', 'translation', 'note', 'gender'})


def _command_line(ctx) -> str:
    """The command and its parameters for the process log."""
    parts = [ctx.command_path.split(' ', 1)[-1]]
    for name, value in ctx.params.items():
        if value is None or value is False:
            continue
        if name in _TEXT_PARAMS or (name == 'category' and value not in CATEGORY_LABELS):
            # a category outside the known set is free text too
            value = f'<{len(str(value))} 字元>'
        parts.append(f'{name}={value}')
    return 'cli ' + ' '.join(parts)


def _projects_dir_of(params: dict) -> str:
    """The projects directory of a command: the parent of its project
    directory when it names one, else ./projects (as _find_project)."""
    project = params.get('project') or params.get('project_dir')
    if project:
        return os.path.dirname(os.path.abspath(project))
    return 'projects'


class _LoggedCommand(click.Command):
    """A command whose run goes to the process log (src/core/proclog.py):
    a start line, its end with the exit code and duration, and the
    traceback of an exception it does not handle."""

    def invoke(self, ctx):
        proclog.setup(_projects_dir_of(ctx.params), 'cli', _command_line(ctx))
        proclog.terminal(logging.WARNING)  # warnings on stderr, as without the file
        started = time.monotonic()
        code = 0
        try:
            return super().invoke(ctx)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
            raise
        except click.exceptions.Exit as e:
            code = e.exit_code
            raise
        except click.ClickException as e:
            code = e.exit_code
            logger.info("usage error: %s", e.format_message())
            raise
        except (KeyboardInterrupt, click.exceptions.Abort):
            code = 1  # click's exit status for an interrupted command ("Aborted!")
            logger.warning("interrupted (Ctrl-C)")
            raise
        except BaseException:
            code = 1
            logger.exception("uncaught exception in command")
            raise
        finally:
            logger.info("end: exit %s after %.1f s", code, time.monotonic() - started)
            proclog.command_ended()


class _LoggedGroup(click.Group):
    command_class = _LoggedCommand
    group_class = type  # subgroups are logged groups too


@click.group(cls=_LoggedGroup)
@click.version_option(__version__, prog_name='epub-translator')
def cli():
    """EPUB 智能翻譯系統 - Translate English EPUB books to Traditional Chinese."""
    pass


@cli.command()
@click.argument('epub_file', type=click.Path(exists=True))
def init(epub_file):
    """Initialize a translation project from an EPUB file.

    Copies the EPUB into the project, extracts chapters, segments content,
    and creates a project file.
    """
    project_dir = _get_project_dir(epub_file)

    pm = ProjectManager(project_dir)
    if pm.exists():
        click.echo(f"專案已存在：{project_dir}")
        click.echo("如需重新初始化，請先刪除專案目錄。")
        sys.exit(1)

    # The project owns its copy of the book
    os.makedirs(project_dir, exist_ok=True)
    epub_path = os.path.abspath(os.path.join(project_dir, 'source.epub'))
    shutil.copy2(epub_file, epub_path)

    click.echo(f"正在解析 EPUB：{epub_file}")
    extractor = EPUBExtractor(epub_path)
    try:
        extractor.load()
    except EPUBError as e:
        os.remove(epub_path)
        click.echo(f"無法解析 EPUB：{e}")
        sys.exit(1)
    metadata = extractor.get_metadata()

    click.echo(f"書名：{metadata['title']}")
    click.echo(f"作者：{metadata['author']}")

    chapters = extractor.extract_chapters()
    click.echo(f"提取了 {len(chapters)} 個章節")
    for chapter_id, reason in extractor.failed:
        click.echo(f"無法解析的章節（不分段）：{chapter_id}：{reason}")

    segmenter = Segmenter()
    all_segments = []
    for chapter in chapters:
        segs = segmenter.segment_chapter(chapter.id, chapter.body)
        all_segments.extend(segs)

    click.echo(f"分段完成：共 {len(all_segments)} 個段落")
    passthrough = sum(s.passthrough for s in all_segments)
    if passthrough:
        click.echo(f"其中 {passthrough} 個是沒有註解的程式碼，原樣保留，不需翻譯")

    pm.create(
        epub_path=epub_path,
        title=metadata['title'],
        author=metadata['author'],
        segments=all_segments,
        source_sha256=sha256_file(epub_path),
        segmentation=segmenter.rules,
    )

    click.echo(f"專案已建立：{project_dir}")
    click.echo("使用 'python translate.py start' 開始翻譯")


# The option each refused setting is reported against
_SETTING_OPTIONS = {'backend': "'--backend' / '-b'", 'model': "'--model' / '-m'",
                    'effort': "'--effort' / '-e'", 'timeout': "'--timeout' / '-t'"}


def _select_model(pm, backend, model, effort=None, timeout=None) -> bool:
    """pm.select_model, a refused value being a usage error of its option.
    Echoes the note when a stored effort was dropped."""
    previous = pm.data.effort
    try:
        changed = pm.select_model(backend, model, effort=effort, timeout=timeout)
    except SettingError as e:
        raise click.BadParameter(str(e), param_hint=_SETTING_OPTIONS[e.setting]) from e
    note = effort_reset_note(previous, pm.data)
    if note:
        click.echo(note)
    return changed


def _settings_line(pm) -> str:
    settings = pm.call_settings()
    return f"推理強度：{effort_label(settings.effort)}，呼叫逾時：{settings.timeout} 秒"


@cli.command()
@click.option('--project', '-p', default=None, help='Project directory path')
@click.option('--backend', '-b', default=None, type=click.Choice(sorted(llm.BACKENDS)),
              help="Backend for this and later runs (default: the project's, else claude-cli)")
@click.option('--model', '-m', default=None,
              help="Translation model: one of the backend's models")
@click.option('--workers', '-w', type=click.IntRange(1, 20), default=1,
              help='Number of parallel workers (1-20)')
@click.option('--retry-failed', is_flag=True, default=False,
              help='Also translate segments whose earlier translation failed')
def start(project, backend, model, workers, retry_failed):
    """Start or resume translation of pending segments."""
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。請先執行 init 指令，或用 --project 指定專案目錄。")
        sys.exit(1)

    # One writer per project: held for the whole run, released on any exit
    lock = ProjectLock(project_dir)
    try:
        lock.acquire()
    except ProjectLockedError as e:
        click.echo(str(e))
        sys.exit(1)
    try:
        _start(project_dir, backend, model, workers, retry_failed)
    finally:
        lock.release()


def _start(project_dir, backend, model, workers, retry_failed):
    pm = ProjectManager(project_dir)
    pm.load()  # under the lock: the backend and model are checked against this
    if _select_model(pm, backend, model):
        pm.save()

    pending = pm.get_pending_segments(include_failed=retry_failed)
    if not pending:
        click.echo("所有段落已翻譯完成！")
        failed = pm.get_progress()['failed']
        if failed:
            click.echo(f"{failed} 個段落先前翻譯失敗，可用 start --retry-failed 重試。")
        return

    progress = pm.get_progress()
    click.echo(f"使用模型：{pm.data.model}"
               + (f"（{pm.data.backend}）" if pm.data.backend else ''))
    click.echo(_settings_line(pm))
    click.echo(f"繼續翻譯：{progress['translated']}/{progress['total']} 已完成 ({progress['percent']}%)")
    click.echo(f"剩餘 {len(pending)} 個段落待翻譯")
    if workers > 1:
        click.echo(f"並行 Workers：{workers}")
    click.echo()

    # Load glossary if available
    gm = GlossaryManager(_get_glossary_path(project_dir))
    gm.load()

    all_segments = pm.data.segments

    log_dir = os.path.join(project_dir, 'log')
    run = _Run(project_dir, pm, workers, len(pending), retry_failed)  # workers asked for, as the web
    with run:
        if workers > 1:
            failed = _start_parallel(pm, gm, pending, all_segments, progress, workers, log_dir,
                                     run)
        else:
            failed = _start_sequential(pm, gm, pending, all_segments, progress, log_dir, run)

    click.echo()
    if failed:
        click.echo(f"翻譯結束：{failed} 個段落翻譯失敗，其餘已完成。"
                   f"失敗紀錄在 {os.path.join(log_dir, 'failed')}，可用 start --retry-failed 重試。")
        sys.exit(1)
    click.echo("全部翻譯完成！")


# Errors that stop or pause a whole run, leaving the segment pending
RUN_ERRORS = (RateLimitError, AuthError, BackendUnavailableError)


class _Run:
    """The event record of one CLI translation run (log/events.jsonl):
    run_start on entry, each segment's result, rate-limit pauses, and
    run_end with the counts on any way out."""

    def __init__(self, project_dir, pm, workers, segments, retry_failed):
        self.events = RunEvents(project_dir, 'cli')
        self.counts = {'translated': 0, 'failed': 0}
        self.started = time.monotonic()
        self.events.emit('run_start', workers=workers, segments=segments,
                         mode='retry_failed' if retry_failed else 'pending',
                         **settings_fields(pm.call_settings()))
        self.state = 'completed'
        self.error = None

    def segment(self, segment_id, outcome):
        failed = outcome.status == SegmentStatus.FAILED
        self.counts['failed' if failed else 'translated'] += 1
        segment_result(self.events, segment_id, outcome)

    def stopped(self, error):
        """A run-level error (or too many unexpected failures) stops the run."""
        self.state = 'stopped'
        # A message (the streak's) may quote an error: the record gets a code
        self.error = 'UnexpectedStreak' if isinstance(error, str) else type(error).__name__

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None and exc_type is not SystemExit:
            self.state = 'interrupted' if issubclass(exc_type, KeyboardInterrupt) else 'failed'
            self.error = exc_type.__name__
        self.events.emit('run_end', state=self.state, **self.counts, error=self.error,
                         seconds=round(time.monotonic() - self.started, 3))
        return False

# Injectable (tests): sleeping through a rate-limit pause, and the clock
_sleep = time.sleep
_clock = time.monotonic


def _stop_run(e):
    """An expired login or a backend that cannot be started stops the whole
    run (so do too many unexpected failures in a row)."""
    logger.warning("run stopped: %s", e)
    click.echo(f"  ✗ {e}")
    click.echo("翻譯中斷。已完成的進度已儲存，可用 start 繼續。")
    sys.exit(1)


def _rate_limit_message(e, delay: float) -> str:
    return f"  ✗ {e}\n  使用量已達上限：{delay:.0f} 秒後自動繼續（Ctrl-C 停止）"


def _saver(pm, log_dir):
    """Debounced saves of a CLI run; each result's diagnostics are written
    once it is on disk (the CLI holds the lock: nobody edits meanwhile)."""
    def after_save(items):
        for segment_id, outcome in items:
            record_failure_log(log_dir, segment_id, outcome)
    return DebouncedSaver(pm, after_save, clock=_clock)


def _report_unsaved(saver):
    if saver.pending:
        click.echo(f"未儲存的結果（{len(saver.pending)} 個段落，下次 start 會重新翻譯）："
                   + ', '.join(item[0] for item in saver.pending))


class _RunStopped(Exception):
    """A run-level stop inside the sequential loop, reported after the save
    (args[0]: the error)."""


def _start_sequential(pm, gm, pending, all_segments, progress, log_dir, run):
    """Single-worker sequential translation. Returns the number of failed segments."""
    engine = TranslationEngine(**pm.call_settings().call_args(), events=run.events)
    failed = 0
    streak = UnexpectedStreak()
    saver = _saver(pm, log_dir)

    stopped = None
    try:
        for i, seg in enumerate(pending, 1):
            click.echo(f"[{progress['translated'] + i}/{progress['total']}] 翻譯段落 {seg.segment_id}...")
            while True:
                try:
                    outcome = translate_segment(engine, seg, all_segments, gm,
                                                translations=pm.data.segmentation == 2,
                                                brief=pm.data.brief or '')
                    break
                except RUN_ERRORS as e:
                    delay = resume_delay(e)
                    if delay is None:
                        raise _RunStopped(e)
                    saver.flush()  # a pause saves what is waiting
                    click.echo(_rate_limit_message(e, delay))
                    run.events.emit('pause', reason='rate_limit', error=type(e).__name__,
                                    resume_in=delay)
                    _sleep(delay)
                    run.events.emit('resume', reason='auto')
            pm.apply_outcome(seg.segment_id, outcome)
            run.segment(seg.segment_id, outcome)
            saver.add((seg.segment_id, outcome))
            saver.maybe_save()
            if outcome.status == SegmentStatus.FAILED:
                failed += 1
                click.echo(f"  ✗ 翻譯失敗：{outcome.issues[0].detail}")
            else:
                click.echo(f"  ✓ 完成（{seg.word_count} 字）")
            stop = streak.add(outcome)
            if stop:
                raise _RunStopped(stop)
    except _RunStopped as s:
        stopped = s.args[0]  # reported once the save below succeeded
    finally:
        # Also on Ctrl-C (the call in flight was killed, its segment stays
        # pending; one applied between apply_outcome and add is saved too:
        # forced) and on a stopped run
        try:
            saver.flush(force=True)
        except BaseException:
            _report_unsaved(saver)
            raise
    if stopped is not None:
        run.stopped(stopped)
        _stop_run(stopped)  # "已完成的進度已儲存": now true
    return failed


def _start_parallel(pm, gm, pending, all_segments, progress, num_workers, log_dir, run):
    """Multi-worker parallel translation. Returns the number of failed segments."""
    import threading
    import queue as queue_mod

    seg_queue = queue_mod.Queue()
    for seg in pending:
        seg_queue.put(seg)

    result_queue = queue_mod.Queue()
    error_event = threading.Event()  # an error that stops the run
    cancel = threading.Event()       # Ctrl-C: the calls in flight are killed
    gate = threading.Event()         # cleared while a rate limit pauses the run
    gate.set()
    pause = {}  # until, error, shown: set when the gate closes, read only while closed
    pause_lock = threading.Lock()
    stop_errors = []
    total = progress['total']
    base_translated = progress['translated']

    def worker(worker_id):
        tag = f"W{worker_id}"
        # A retry's backoff wait ends, and no call is made, once the run
        # stops or a rate limit pauses it
        engine = TranslationEngine(
            **pm.call_settings().call_args(), events=run.events,
            interrupted=lambda: error_event.is_set() or not gate.is_set())

        while not error_event.is_set():
            if not gate.wait(timeout=0.5):
                continue  # paused by a rate limit
            try:
                seg = seg_queue.get(timeout=1)
            except queue_mod.Empty:
                break
            if not gate.is_set():  # paused while this worker waited for it
                seg_queue.put(seg)
                continue

            try:
                outcome = translate_segment(engine, seg, all_segments, gm,
                                            cancel=cancel,
                                            translations=pm.data.segmentation == 2,
                                            mutex=pm.mutex, brief=pm.data.brief or '')
            except CancelledError:
                if cancel.is_set() or error_event.is_set():
                    break  # interrupted or stopped: the segment stays pending
                seg_queue.put(seg)  # a backoff ended by a rate-limit pause
                continue
            except RUN_ERRORS as e:
                delay = resume_delay(e)
                if delay is None:
                    stop_errors.append(e)
                    error_event.set()  # wins over a rate-limit wait: the run stops
                    cancel.set()  # the other calls in flight end; their segments stay pending
                    break
                with pause_lock:
                    if gate.is_set():  # the first worker to hit it
                        gate.clear()
                        pause.update(until=_clock() + delay, error=e, shown=False)
                        run.events.emit('pause', reason='rate_limit', error=type(e).__name__,
                                        resume_in=delay)
                    else:  # a later limit during the wait extends it
                        pause['until'] = max(pause['until'], _clock() + delay)
                        run.events.emit('wait_extended', reason='rate_limit',
                                        resume_in=round(pause['until'] - _clock(), 3))
                seg_queue.put(seg)  # pending again, once the gate is closed
                continue
            result_queue.put((seg.segment_id, outcome, seg.word_count, tag))

    actual_workers = min(num_workers, len(pending))
    threads = []  # the workers that started

    # Process results in main thread
    completed = 0
    failed = 0
    saver = _saver(pm, log_dir)
    streak = UnexpectedStreak()

    def apply(seg_id, outcome, word_count, tag):
        nonlocal completed, failed
        stop = streak.add(outcome)
        if stop:
            # Like a run-level error: no new segment is started
            stop_errors.append(stop)
            error_event.set()
        pm.apply_outcome(seg_id, outcome)
        run.segment(seg_id, outcome)
        saver.add((seg_id, outcome))
        saver.maybe_save()  # the window, after every result
        if outcome.status == SegmentStatus.FAILED:
            failed += 1
            click.echo(f"  [{tag}] ✗ {seg_id} 翻譯失敗：{outcome.issues[0].detail}")
        else:
            completed += 1
            click.echo(f"  [{tag}] [{base_translated + completed}/{total}] ✓ {seg_id}（{word_count} 字）")

    def tick():
        """Save the window; announce a rate-limit pause, end it when due."""
        if not gate.is_set():
            saver.flush()  # a pause saves what is waiting
            if error_event.is_set():
                return  # stopping: no announcement, the gate stays closed
            with pause_lock:
                if not pause['shown']:
                    pause['shown'] = True
                    click.echo(_rate_limit_message(pause['error'],
                                                   pause['until'] - _clock()))
                if _clock() >= pause['until']:
                    click.echo("  繼續翻譯")
                    run.events.emit('resume', reason='auto')
                    gate.set()
        else:
            saver.maybe_save()

    try:
        # Started inside the try: if a later start fails or is interrupted,
        # the workers already running are stopped and joined below
        for i in range(actual_workers):
            # Daemon: a second Ctrl-C ends the process without waiting for them
            t = threading.Thread(target=worker, args=(i,), daemon=True)
            t.start()
            threads.append(t)

        while any(t.is_alive() for t in threads) or not result_queue.empty():
            try:
                apply(*result_queue.get(timeout=0.5))
            except queue_mod.Empty:
                tick()
                continue

            # Batch: drain any immediately available results
            while True:
                try:
                    apply(*result_queue.get_nowait())
                except queue_mod.Empty:
                    break
            tick()
        saver.flush()
    except BaseException:
        # The supervisor failed (a save raised, Ctrl-C): stop dispatching,
        # kill the calls in flight (their segments stay pending), save what
        # was received, else report it, before the caller releases the lock
        error_event.set()
        cancel.set()
        click.echo("翻譯中斷，正在停止進行中的段落……")
        try:
            for t in threads:
                t.join()
        finally:  # also on a second Ctrl-C during the join
            while True:
                try:
                    seg_id, outcome, _, _ = result_queue.get_nowait()
                except queue_mod.Empty:
                    break
                pm.apply_outcome(seg_id, outcome)
                run.segment(seg_id, outcome)
                saver.add((seg_id, outcome))
            try:
                saver.flush(force=True)
            except BaseException:  # noqa: BLE001 - reported below (the save itself may be what failed)
                pass
            _report_unsaved(saver)
        raise

    for t in threads:
        t.join()

    if error_event.is_set():
        run.stopped(stop_errors[0])
        _stop_run(stop_errors[0])
    return failed


@cli.command()
@click.option('--project', '-p', default=None, help='Project directory path')
def status(project):
    """Show translation progress."""
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。請先執行 init 指令，或用 --project 指定專案目錄。")
        sys.exit(1)

    pm = ProjectManager(project_dir)
    pm.load()

    progress = pm.get_progress()
    click.echo(f"書名：{pm.data.title}")
    click.echo(f"作者：{pm.data.author}")
    click.echo(f"來源：{source_epub_path(project_dir, pm.data.epub_path)}")
    click.echo()
    click.echo(f"總段落數：{progress['total']}")
    click.echo(f"已翻譯：  {progress['translated']}")
    if progress['passthrough']:
        click.echo(f"  其中原樣保留的程式碼（沒有註解，不需翻譯）：{progress['passthrough']}")
    click.echo(f"已審閱：  {progress['reviewed']}")
    click.echo(f"待翻譯：  {progress['pending']}")
    click.echo(f"失敗：    {progress['failed']}")
    click.echo(f"進度：    {progress['percent']}%")


@cli.command()
@click.option('--project', '-p', default=None, help='Project directory path')
@click.option('--port', default=5000, help='Port number for the review server')
def review(project, port):
    """Launch the web-based translation review interface."""
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。請先執行 init 指令，或用 --project 指定專案目錄。")
        sys.exit(1)

    from src.web.server import create_app
    import webbrowser

    # Extract project name and parent directory
    projects_dir = os.path.dirname(project_dir)
    project_name = os.path.basename(project_dir)

    app = create_app(projects_dir=projects_dir)
    url = f'http://localhost:{port}/project/{project_name}/review'
    click.echo(f"啟動審閱伺服器：{url}")
    click.echo("按 Ctrl+C 停止")
    webbrowser.open(url)
    app.run(host='127.0.0.1', port=port, debug=False)


@cli.command()
@click.option('--project', '-p', default=None, help='Project directory path')
@click.option('--format', '-f', 'fmt',
              type=click.Choice(['chinese-only', 'bilingual-horizontal', 'bilingual-vertical']),
              default='bilingual-horizontal', help='Output format')
def export(project, fmt):
    """Export translated EPUB in the chosen format."""
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。請先執行 init 指令，或用 --project 指定專案目錄。")
        sys.exit(1)

    pm = ProjectManager(project_dir)
    pm.load()
    if pm.data.schema != 2:
        click.echo(SCHEMA_1_MESSAGE)
        sys.exit(1)

    progress = pm.get_progress()
    # passthrough code is translated without a model: it is no translation
    if progress['translated'] + progress['reviewed'] - progress['passthrough'] == 0:
        click.echo("尚未翻譯任何段落。請先執行 start 指令。")
        sys.exit(1)

    base_name = os.path.basename(os.path.abspath(project_dir))
    output_path = os.path.join(project_dir, 'output', f'{base_name}-{fmt}.epub')

    click.echo(f"正在建構 {fmt} 格式的 EPUB...")
    try:
        report = export_project(project_dir, output_path, fmt)
    except ExportError as e:
        click.echo(f"無法匯出：{e}")
        sys.exit(1)
    except Exception as e:  # anything unexpected: a message, no traceback
        click.echo(f"匯出失敗（{type(e).__name__}）：{e}")
        sys.exit(1)
    click.echo(f"已輸出：{report.output_path}")
    for line in report.lines():
        click.echo(line)


@cli.command()
@click.argument('project_dir', type=click.Path(exists=True, file_okay=False))
@click.option('--dry-run', is_flag=True, default=False, help='Only report; write nothing')
def migrate(project_dir, dry_run):
    """Migrate a project to the block model (schema 2), once.

    Writes nothing unless every chapter matches; keeps the previous
    project.json as project.schema1.json.
    """
    try:
        report = migrate_project(project_dir, dry_run=dry_run)
    except (MigrationError, ProjectLockedError) as e:
        click.echo(str(e))
        sys.exit(1)
    for line in report.lines():
        click.echo(line)
    if report.unmatched:
        sys.exit(1)


VALIDATE_SCHEMA_1_MESSAGE = ('此專案是舊格式（schema 1），無法檢查譯文。'
                             '請先執行 python3 translate.py migrate <專案目錄> 再檢查。')


@cli.command()
@click.argument('project_dir', type=click.Path(exists=True, file_okay=False))
@click.option('--mark-failed', is_flag=True, default=False,
              help='Set the segments that fail the structural check to failed')
def validate(project_dir, mark_failed):
    """Check the stored translations with the full check of the export.

    Read-only by default: prints, per rule, how many translated or reviewed
    segments break it, and how many have each quality or glossary warning. With --mark-failed the segments that fail a
    structural rule become failed, with their issues; their text is kept,
    so start --retry-failed translates them again and a retry that fails
    leaves the old text in place. A schema 1 project is refused (run
    migrate first), with or without the flag.
    """
    if not os.path.isfile(os.path.join(project_dir, 'project.json')):
        click.echo(f"找不到專案：{project_dir} 裡沒有 project.json。")
        sys.exit(1)
    lock = ProjectLock(project_dir)
    try:
        if mark_failed:
            lock.acquire()
        report = _validate(project_dir, mark_failed)
    except ProjectLockedError as e:
        click.echo(str(e))
        sys.exit(1)
    finally:
        lock.release()
    if report is None:
        click.echo(VALIDATE_SCHEMA_1_MESSAGE)
        sys.exit(1)
    for line in report:
        click.echo(line)


def _validate(project_dir: str, mark_failed: bool) -> list[str] | None:
    """The report lines; None for a schema 1 project (its segments have no
    block ranges: marking one failed and then migrating would clear the
    translations of its whole merge group)."""
    pm = ProjectManager(project_dir)
    pm.load()
    if pm.data.schema != 2:
        return None
    gm = load_glossary(_get_glossary_path(project_dir))  # None: unreadable, no glossary warnings
    checked, failing = 0, []
    blocking, warnings = Counter(), Counter()
    for seg in pm.data.segments:
        if seg.status not in (SegmentStatus.TRANSLATED, SegmentStatus.REVIEWED) \
                or not seg.translated_text.strip() or seg.passthrough:
            continue  # (passthrough: code left as it is, nothing to check)
        checked += 1
        result = check_segment(seg, seg.translated_text, gm)
        blocking.update({i.code for i in result.blocking})
        warnings.update({i.code for i in result.issues if not i.blocking})
        if result.blocking:
            failing.append((seg.segment_id, result.blocking))
    lines = [f"已檢查 {checked} 段譯文：{len(failing)} 段未通過結構檢查（匯出時以原文輸出），"
             f"{checked - len(failing)} 段通過"]
    lines += [f'  {code}：{n}' for code, n in sorted(blocking.items())]
    if gm is None:
        lines.append('術語表無法讀取（glossary.json 損壞或格式不符），未檢查術語。')
    if warnings:
        lines.append('品質與術語警告（不影響匯出）：')
        lines += [f'  {code}：{n}' for code, n in sorted(warnings.items())]
    if mark_failed and failing:
        for segment_id, issues in failing:
            pm.mark_failed(segment_id, issues)
        pm.save()
        lines.append(f"已將 {len(failing)} 段標為翻譯失敗（原譯文保留），"
                     f"可用 start --retry-failed 重新翻譯。")
    return lines


# --- Glossary CLI commands ---

@cli.group()
def glossary():
    """Manage translation glossary (terminology)."""
    pass


@glossary.command('show')
@click.option('--project', '-p', default=None, help='Project directory path')
def glossary_show(project):
    """Show all glossary entries."""
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。")
        sys.exit(1)

    gm = GlossaryManager(_get_glossary_path(project_dir))
    gm.load()

    entries = gm.get_all_terms()
    if not entries:
        click.echo("術語表為空。使用 'glossary add' 新增術語，或 'glossary extract' 自動提取。")
        return

    click.echo(f"術語表共 {len(entries)} 筆：")
    click.echo()
    for e in entries:
        line = f"  {e.term} → {e.translation}"
        if e.category:
            line += f"  [{e.category}]"
        if e.gender:
            line += f"  性別：{e.gender}"
        if e.note:
            line += f"  備註：{e.note}"
        click.echo(line)


@glossary.command('add')
@click.argument('term')
@click.argument('translation')
@click.option('--category', '-c', default='', help='Term category')
@click.option('--gender', '-g', default=None,
              help="Gender of a person (shown in the prompt); omitted: the entry's stays")
@click.option('--note', '-n', default=None,
              help="Note on a person (shown in the prompt); omitted: the entry's stays")
@click.option('--project', '-p', default=None, help='Project directory path')
def glossary_add(term, translation, category, gender, note, project):
    """Add a term to the glossary, or replace it."""
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。")
        sys.exit(1)

    gm = GlossaryManager(_get_glossary_path(project_dir))
    gm.load()
    old = gm.get_term(term)  # an omitted -g / -n keeps its value; '' clears it
    if gender is None:
        gender = old.gender if old else ''
    if note is None:
        note = old.note if old else ''
    gm.add_term(term, translation, category=category, gender=gender, note=note)
    click.echo(f"已新增：{term} → {translation}")


@glossary.command('extract')
@click.option('--project', '-p', default=None, help='Project directory path')
def glossary_extract(project):
    """Auto-extract technical terms from the book using Claude."""
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。")
        sys.exit(1)

    pm = ProjectManager(project_dir)
    pm.load()

    from src.glossary.term_extractor import ExtractionError, TermExtractor
    settings = pm.call_settings()
    segments = [{'text': s.text} for s in pm.data.segments if s.segment_type == SegmentType.TEXT]
    events = RunEvents(project_dir, 'extract')
    events.emit('run_start', segments=len(segments), **settings_fields(settings))
    stopped = None
    started = time.monotonic()
    end = {'state': 'interrupted'}  # Ctrl-C; replaced on every other way out
    # One lifecycle: run_end after the glossary is saved, with what was stored
    try:
        extractor = TermExtractor(**settings.call_args(), events=events)
        click.echo(f"正在從 {len(segments)} 個文字段落中提取術語...")
        try:
            terms = extractor.extract_from_segments(segments)
        except ExtractionError as e:
            # Keep what the finished batches found
            stopped, terms = e, e.terms
        # the count first: a closed stdout at a notice below still records it
        end = {'state': 'interrupted', 'terms': len(terms)}  # until the save went through
        if stopped is not None:
            click.echo(f"提取中斷：{stopped}")
        click.echo(f"提取到 {len(terms)} 個術語")
        if terms:
            gm = GlossaryManager(_get_glossary_path(project_dir))
            gm.load()
            for t in terms:
                old = gm.get_term(t['term'])  # a person's gender and note stay
                gm.add_term(t['term'], t['translation'], category=t.get('category', ''),
                            gender=old.gender if old else '', note=old.note if old else '',
                            save=False)
            gm.save()  # once, right after the glossary was read
        # Saved: recorded before the confirmation, which can fail (a closed pipe)
        # (a stop's cause is in the record of the call that hit it)
        end = {'state': 'completed' if stopped is None else 'stopped', 'terms': len(terms),
               'added': len(terms)}
        if terms:
            click.echo(f"已儲存到 {_get_glossary_path(project_dir)}")
    except KeyboardInterrupt as e:
        end = {**end, 'state': 'interrupted', 'error': type(e).__name__}
        raise
    except Exception as e:
        end = {**end, 'state': 'failed', 'error': type(e).__name__}
        raise
    finally:
        events.emit('run_end', seconds=round(time.monotonic() - started, 3), **end)
    if stopped is not None:
        sys.exit(1)


@glossary.command('import')
@click.argument('json_file', type=click.Path(exists=True))
@click.option('--project', '-p', default=None, help='Project directory path')
def glossary_import(json_file, project):
    """Import glossary entries from a JSON file."""
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。")
        sys.exit(1)

    gm = GlossaryManager(_get_glossary_path(project_dir))
    gm.load()
    try:
        count = gm.import_json(json_file)
    except ValueError as e:  # also JSON that does not decode; nothing was imported
        click.echo(f"無法匯入：{e}")
        sys.exit(1)
    click.echo(f"已匯入 {count} 筆術語")


@glossary.command('export')
@click.argument('json_file', type=click.Path())
@click.option('--project', '-p', default=None, help='Project directory path')
def glossary_export(json_file, project):
    """Export glossary entries to a JSON file."""
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。")
        sys.exit(1)

    gm = GlossaryManager(_get_glossary_path(project_dir))
    gm.load()
    count = gm.export_json(json_file)
    click.echo(f"已匯出 {count} 筆術語到 {json_file}")


# --- Book brief ---

@cli.group()
def brief():
    """Show or set the book's brief (genre, tone, audience) for the prompt."""


@brief.command('show')
@click.option('--project', '-p', default=None, help='Project directory path')
def brief_show(project):
    """Show the book's brief."""
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。")
        sys.exit(1)
    pm = ProjectManager(project_dir)
    pm.load()
    click.echo(pm.data.brief or "尚未設定書籍簡介。")


@brief.command('set')
@click.argument('text')
@click.option('--project', '-p', default=None, help='Project directory path')
def brief_set(text, project):
    """Set the book's brief; an empty TEXT removes it."""
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。")
        sys.exit(1)
    try:
        with ProjectLock(project_dir):
            pm = ProjectManager(project_dir)
            pm.load()
            pm.set_brief(text)
            pm.save()
    except (ProjectLockedError, ValueError) as e:  # ValueError: too long, nothing saved
        click.echo(str(e))
        sys.exit(1)
    click.echo("已設定書籍簡介。" if pm.data.brief else "已移除書籍簡介。")


# --- Backend and model ---

@cli.group()
def model():
    """Show or set the project's backend, model, reasoning effort and call timeout
    (without translating)."""


def _model_lines(aliases) -> str:
    """The aliases one line per family (the part before the first '-':
    claude-…, gpt-…), the later lines indented under the first."""
    lines = {}
    for alias in aliases:
        lines.setdefault(alias.split('-')[0] if '-' in alias else '', []).append(alias)
    return ('\n' + ' ' * 10).join(', '.join(group) for group in lines.values())


@model.command('show')
@click.option('--project', '-p', default=None, help='Project directory path')
def model_show(project):
    """Show the project's backend, model, reasoning effort and call timeout."""
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。")
        sys.exit(1)
    pm = ProjectManager(project_dir)
    pm.load()
    settings = pm.call_settings()
    click.echo(f"後端：{pm.data.backend or llm.default_backend().name}")
    click.echo(f"模型：{pm.data.model}")
    click.echo('可用模型：' + _model_lines(m['alias'] for m in available_models(pm.data.backend)))
    click.echo(f"推理強度：{effort_label(settings.effort)}")
    levels = effort_levels(pm.data.backend, pm.data.model)
    click.echo(f"可用推理強度：{', '.join(levels) if levels else '無'}")
    click.echo(f"呼叫逾時：{settings.timeout} 秒")


@model.command('set')
@click.option('--backend', '-b', default=None, type=click.Choice(sorted(llm.BACKENDS)),
              help="Backend; when it differs from the project's and --model is not given, "
                   "that backend's default model is taken")
@click.option('--model', '-m', 'model_name', default=None,
              help="Model: one of the backend's models")
@click.option('--effort', '-e', default=None,
              help="Reasoning effort: one of the model's levels (model show lists them); "
                   "a stored level the new model does not take goes back to the default")
@click.option('--timeout', '-t', default=None, type=int,
              help=f"Seconds per model call ({MIN_TIMEOUT}-{MAX_TIMEOUT}), "
                   "for translation and glossary extract")
@click.option('--project', '-p', default=None, help='Project directory path')
def model_set(backend, model_name, effort, timeout, project):
    """Set the backend, model, reasoning effort and/or call timeout used by
    later runs and glossary extract."""
    if backend is None and model_name is None and effort is None and timeout is None:
        raise click.UsageError("請指定 --backend/-b、--model/-m、--effort/-e 或 --timeout/-t（可合併）")
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。")
        sys.exit(1)
    try:
        with ProjectLock(project_dir):
            pm = ProjectManager(project_dir)
            pm.load()
            if _select_model(pm, backend, model_name, effort, timeout):
                pm.save()
    except ProjectLockedError as e:
        click.echo(str(e))
        sys.exit(1)
    click.echo(f"已設定模型：{pm.data.model}（{pm.data.backend or llm.default_backend().name}）")
    click.echo(_settings_line(pm))


# --- Statistics ---

@cli.command()
@click.option('--project', '-p', default=None, help='Project directory path')
@click.option('--since', default=None,
              help='Only records at or after this time (ISO date or date and time, local time)')
@click.option('--run', 'run_id', default=None, help='Only the records of this run id')
@click.option('--json', 'as_json', is_flag=True, default=False, help='Print the numbers as JSON')
def stats(project, since, run_id, as_json):
    """Summarize the project's event record (log/events.jsonl): model calls,
    retries, failures, durations and tokens per backend/model/effort, and runs."""
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。")
        sys.exit(1)
    try:
        since_ts = parse_since(since) if since else None
    except ValueError as e:
        raise click.BadParameter(f'無法解讀時間：{since}', param_hint="'--since'") from e
    numbers = project_stats(project_dir, since=since_ts, run=run_id)
    if as_json:
        click.echo(json.dumps(numbers, ensure_ascii=False, indent=2))
        return
    for line in text_report(numbers):
        click.echo(line)


def _find_project(project_dir: str | None) -> str | None:
    """Find a project directory. If none specified, look in projects/."""
    if project_dir:
        pm = ProjectManager(project_dir)
        return project_dir if pm.exists() else None

    projects_dir = 'projects'
    if not os.path.isdir(projects_dir):
        return None

    for name in os.listdir(projects_dir):
        candidate = os.path.join(projects_dir, name)
        if os.path.isdir(candidate):
            pm = ProjectManager(candidate)
            if pm.exists():
                return candidate
    return None


if __name__ == '__main__':
    cli()
