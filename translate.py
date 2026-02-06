"""CLI entry point for the EPUB translator."""

from __future__ import annotations

import os
import sys
import click

from src.epub.extractor import EPUBExtractor
from src.core.segmenter import Segmenter, SegmentType
from src.core.project import ProjectManager
from src.core.translator import TranslationEngine, TranslationError
from src.glossary.glossary_manager import GlossaryManager
from src.epub.builder import EPUBBuilder


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
def cli():
    """EPUB 智能翻譯系統 - Translate English EPUB books to Traditional Chinese."""
    pass


@cli.command()
@click.argument('epub_file', type=click.Path(exists=True))
def init(epub_file):
    """Initialize a translation project from an EPUB file.

    Extracts chapters, segments content, and creates a project file.
    """
    epub_path = os.path.abspath(epub_file)
    project_dir = _get_project_dir(epub_file)

    pm = ProjectManager(project_dir)
    if pm.exists():
        click.echo(f"專案已存在：{project_dir}")
        click.echo("如需重新初始化，請先刪除專案目錄。")
        sys.exit(1)

    click.echo(f"正在解析 EPUB：{epub_file}")
    extractor = EPUBExtractor(epub_path)
    extractor.load()
    metadata = extractor.get_metadata()

    click.echo(f"書名：{metadata['title']}")
    click.echo(f"作者：{metadata['author']}")

    chapters = extractor.extract_chapters()
    click.echo(f"提取了 {len(chapters)} 個章節")

    segmenter = Segmenter()
    all_segments = []
    for chapter in chapters:
        segs = segmenter.segment_chapter(chapter.id, chapter.html_content)
        all_segments.extend(segs)

    click.echo(f"分段完成：共 {len(all_segments)} 個段落")

    pm.create(
        epub_path=epub_path,
        title=metadata['title'],
        author=metadata['author'],
        segments=all_segments,
    )

    click.echo(f"專案已建立：{project_dir}")
    click.echo("使用 'python translate.py start' 開始翻譯")


@cli.command()
@click.option('--project', '-p', default=None, help='Project directory path')
def start(project):
    """Start or resume translation of pending segments."""
    project_dir = _find_project(project)
    if not project_dir:
        click.echo("找不到專案。請先執行 init 指令，或用 --project 指定專案目錄。")
        sys.exit(1)

    pm = ProjectManager(project_dir)
    pm.load()

    pending = pm.get_pending_segments()
    if not pending:
        click.echo("所有段落已翻譯完成！")
        return

    progress = pm.get_progress()
    click.echo(f"繼續翻譯：{progress['translated']}/{progress['total']} 已完成 ({progress['percent']}%)")
    click.echo(f"剩餘 {len(pending)} 個段落待翻譯")
    click.echo()

    # Load glossary if available
    gm = GlossaryManager(_get_glossary_path(project_dir))
    gm.load()

    engine = TranslationEngine()
    all_segments = pm.data.segments

    for i, seg in enumerate(pending, 1):
        click.echo(f"[{progress['translated'] + i}/{progress['total']}] 翻譯段落 {seg.segment_id}...")

        # Find glossary terms relevant to this segment
        glossary_text = ''
        if gm.entries:
            found = gm.find_terms_in_text(seg.text)
            if found:
                glossary_text = gm.format_for_prompt(found)

        # Get context from adjacent segments
        seg_idx = next((j for j, s in enumerate(all_segments)
                        if s.segment_id == seg.segment_id), -1)
        context_before = all_segments[seg_idx - 1].text if seg_idx > 0 else ''
        context_after = all_segments[seg_idx + 1].text if seg_idx < len(all_segments) - 1 else ''

        try:
            html_arg = seg.html if seg.segment_type != SegmentType.CODE else ''
            translated = engine.translate(
                seg.text,
                segment_type=seg.segment_type,
                glossary_text=glossary_text,
                context_before=context_before,
                context_after=context_after,
                html=html_arg,
            )
            pm.mark_translated(seg.segment_id, translated)
            click.echo(f"  ✓ 完成（{seg.word_count} 字）")
        except TranslationError as e:
            click.echo(f"  ✗ 翻譯失敗：{e}")
            click.echo("翻譯中斷。已完成的進度已儲存，可用 start 繼續。")
            sys.exit(1)

    click.echo()
    click.echo("全部翻譯完成！")


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
    click.echo(f"已審閱：  {progress['reviewed']}")
    click.echo(f"待翻譯：  {progress['pending']}")
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

    progress = pm.get_progress()
    if progress['translated'] + progress['reviewed'] == 0:
        click.echo("尚未翻譯任何段落。請先執行 start 指令。")
        sys.exit(1)

    output_dir = os.path.join(project_dir, 'output')
    base_name = os.path.splitext(os.path.basename(pm.data.epub_path))[0]
    output_path = os.path.join(output_dir, f'{base_name}-{fmt}.epub')

    click.echo(f"正在建構 {fmt} 格式的 EPUB...")
    builder = EPUBBuilder(project_dir)
    result_path = builder.build(output_path, fmt=fmt)
    click.echo(f"已輸出：{result_path}")


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

    from src.glossary.term_extractor import TermExtractor
    extractor = TermExtractor()

    segments = [{'text': s.text} for s in pm.data.segments if s.segment_type == SegmentType.TEXT]
    click.echo(f"正在從 {len(segments)} 個文字段落中提取術語...")

    terms = extractor.extract_from_segments(segments)
    click.echo(f"提取到 {len(terms)} 個術語")

    if terms:
        gm = GlossaryManager(_get_glossary_path(project_dir))
        gm.load()
        for t in terms:
            gm.add_term(t['term'], t['translation'], category=t.get('category', ''))
        click.echo(f"已儲存到 {_get_glossary_path(project_dir)}")


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
