"""CLI entry point for the EPUB translator."""

from __future__ import annotations

import os
import shutil
import sys
from collections import Counter

import click

from src import __version__
from src.epub.extractor import EPUBError, EPUBExtractor
from src.core.segmenter import Segmenter, SegmentType
from src.core.project import (
    ProjectLock, ProjectLockedError, ProjectManager, SegmentStatus, sha256_file,
)
from src.core.translator import (
    AuthError, BackendUnavailableError, RateLimitError, TranslationEngine, available_models,
    UnexpectedStreak, record_failure_log, translate_segment,
)
from src.glossary.glossary_manager import GlossaryManager, load_glossary
from src.core.migrate import MigrationError, migrate_project
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


@click.group()
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


def _check_model(ctx, param, value):
    """--model must be one of the backend's models (asked when used)."""
    if value is None:
        return value
    aliases = [m['alias'] for m in available_models()]
    if value not in aliases:
        raise click.BadParameter(f"'{value}' is not one of {', '.join(aliases)}")
    return value


@cli.command()
@click.option('--project', '-p', default=None, help='Project directory path')
@click.option('--model', '-m', default=None, callback=_check_model,
              help="Translation model: one of the backend's models")
@click.option('--workers', '-w', type=click.IntRange(1, 20), default=1,
              help='Number of parallel workers (1-20)')
@click.option('--retry-failed', is_flag=True, default=False,
              help='Also translate segments whose earlier translation failed')
def start(project, model, workers, retry_failed):
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
        _start(project_dir, model, workers, retry_failed)
    finally:
        lock.release()


def _start(project_dir, model, workers, retry_failed):
    pm = ProjectManager(project_dir)
    pm.load()

    if model:
        pm.set_model(model)
        pm.save()

    pending = pm.get_pending_segments(include_failed=retry_failed)
    if not pending:
        click.echo("所有段落已翻譯完成！")
        failed = pm.get_progress()['failed']
        if failed:
            click.echo(f"{failed} 個段落先前翻譯失敗，可用 start --retry-failed 重試。")
        return

    progress = pm.get_progress()
    click.echo(f"使用模型：{pm.data.model}")
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
    if workers > 1:
        failed = _start_parallel(pm, gm, pending, all_segments, progress, workers, log_dir)
    else:
        failed = _start_sequential(pm, gm, pending, all_segments, progress, log_dir)

    click.echo()
    if failed:
        click.echo(f"翻譯結束：{failed} 個段落翻譯失敗，其餘已完成。"
                   f"失敗紀錄在 {os.path.join(log_dir, 'failed')}，可用 start --retry-failed 重試。")
        sys.exit(1)
    click.echo("全部翻譯完成！")


# Errors that stop a whole run, leaving the segment pending
RUN_ERRORS = (RateLimitError, AuthError, BackendUnavailableError)


def _stop_run(e):
    """A rate limit, an expired login or a backend that cannot be started
    stops the whole run."""
    click.echo(f"  ✗ {e}")
    click.echo("翻譯中斷。已完成的進度已儲存，可用 start 繼續。")
    sys.exit(1)


def _start_sequential(pm, gm, pending, all_segments, progress, log_dir):
    """Single-worker sequential translation. Returns the number of failed segments."""
    engine = TranslationEngine(model=pm.data.model)
    failed = 0
    streak = UnexpectedStreak()

    for i, seg in enumerate(pending, 1):
        click.echo(f"[{progress['translated'] + i}/{progress['total']}] 翻譯段落 {seg.segment_id}...")
        try:
            outcome = translate_segment(engine, seg, all_segments, gm,
                                        translations=pm.data.segmentation == 2)
        except RUN_ERRORS as e:
            _stop_run(e)
        pm.apply_outcome(seg.segment_id, outcome)
        pm.save()
        record_failure_log(log_dir, seg.segment_id, outcome)
        if outcome.status == SegmentStatus.FAILED:
            failed += 1
            click.echo(f"  ✗ 翻譯失敗：{outcome.issues[0].detail}")
        else:
            click.echo(f"  ✓ 完成（{seg.word_count} 字）")
        stop = streak.add(outcome)
        if stop:
            _stop_run(stop)
    return failed


def _start_parallel(pm, gm, pending, all_segments, progress, num_workers, log_dir):
    """Multi-worker parallel translation. Returns the number of failed segments."""
    import threading
    import queue as queue_mod

    seg_queue = queue_mod.Queue()
    for seg in pending:
        seg_queue.put(seg)

    result_queue = queue_mod.Queue()
    error_event = threading.Event()  # rate limit or auth error: stop the run
    stop_errors = []
    total = progress['total']
    base_translated = progress['translated']

    def worker(worker_id):
        tag = f"W{worker_id}"
        engine = TranslationEngine(model=pm.data.model)

        while not error_event.is_set():
            try:
                seg = seg_queue.get(timeout=1)
            except queue_mod.Empty:
                break

            try:
                outcome = translate_segment(engine, seg, all_segments, gm,
                                            translations=pm.data.segmentation == 2)
            except RUN_ERRORS as e:
                stop_errors.append(e)
                error_event.set()
                break
            result_queue.put((seg.segment_id, outcome, seg.word_count, tag))

    actual_workers = min(num_workers, len(pending))
    threads = []  # the workers that started

    # Process results in main thread
    completed = 0
    failed = 0
    unsaved = []  # applied in memory since the last successful save
    applied = []  # (segment id, outcome) of the same, for their diagnostics
    streak = UnexpectedStreak()

    def apply(seg_id, outcome, word_count, tag):
        nonlocal completed, failed
        stop = streak.add(outcome)
        if stop:
            # Like a run-level error: no new segment is started
            stop_errors.append(stop)
            error_event.set()
        pm.apply_outcome(seg_id, outcome)
        unsaved.append(seg_id)
        applied.append((seg_id, outcome))
        if outcome.status == SegmentStatus.FAILED:
            failed += 1
            click.echo(f"  [{tag}] ✗ {seg_id} 翻譯失敗：{outcome.issues[0].detail}")
        else:
            completed += 1
            click.echo(f"  [{tag}] [{base_translated + completed}/{total}] ✓ {seg_id}（{word_count} 字）")

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
                continue

            # Batch: drain any immediately available results
            while True:
                try:
                    apply(*result_queue.get_nowait())
                except queue_mod.Empty:
                    break
            pm.save()
            unsaved.clear()
            for seg_id, outcome in applied:
                record_failure_log(log_dir, seg_id, outcome)
            applied.clear()
    except BaseException:
        # The supervisor failed (a save raised, Ctrl-C): stop dispatching,
        # wait for the calls in flight, then report what was not saved
        # before the caller releases the lock
        error_event.set()
        click.echo("翻譯中斷，等待進行中的段落結束……")
        for t in threads:
            t.join()
        while True:
            try:
                unsaved.append(result_queue.get_nowait()[0])
            except queue_mod.Empty:
                break
        if unsaved:
            click.echo(f"未儲存的結果（{len(unsaved)} 個段落，下次 start 會重新翻譯）："
                       + ', '.join(unsaved))
        raise

    for t in threads:
        t.join()

    if error_event.is_set():
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
    click.echo(f"來源：{pm.data.epub_path}")
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
    if progress['translated'] + progress['reviewed'] == 0:
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
        click.echo(line)


@glossary.command('add')
@click.argument('term')
@click.argument('translation')
@click.option('--category', '-c', default='', help='Term category')
@click.option('--project', '-p', default=None, help='Project directory path')
def glossary_add(term, translation, category, project):
    """Add a term to the glossary."""
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。")
        sys.exit(1)

    gm = GlossaryManager(_get_glossary_path(project_dir))
    gm.load()
    gm.add_term(term, translation, category=category)
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
    extractor = TermExtractor(model=pm.data.model)

    segments = [{'text': s.text} for s in pm.data.segments if s.segment_type == SegmentType.TEXT]
    click.echo(f"正在從 {len(segments)} 個文字段落中提取術語...")

    stopped = None
    try:
        terms = extractor.extract_from_segments(segments)
    except ExtractionError as e:
        # Keep what the finished batches found
        stopped, terms = e, e.terms
        click.echo(f"提取中斷：{e}")
    click.echo(f"提取到 {len(terms)} 個術語")

    if terms:
        gm = GlossaryManager(_get_glossary_path(project_dir))
        gm.load()
        for t in terms:
            gm.add_term(t['term'], t['translation'], category=t.get('category', ''))
        click.echo(f"已儲存到 {_get_glossary_path(project_dir)}")
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
    count = gm.import_json(json_file)
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
