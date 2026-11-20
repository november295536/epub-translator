"""Projects blueprint for project management APIs and detail pages."""

from __future__ import annotations

import os
import re
import shutil
import uuid
from flask import Blueprint, render_template, redirect, request, jsonify, current_app

from src.core.project import (
    ProjectLock, ProjectLockedError, ProjectManager, project_mutex, sha256_file,
)
from src.core.segmenter import Segmenter
from src.epub.extractor import EPUBError, EPUBExtractor
from src.web.tasks import TaskManager


def sanitize_project_name(name: str) -> str:
    """Sanitize a string into a safe project directory name.

    - Replace spaces and underscores with hyphens
    - Remove any character that isn't alphanumeric, hyphen, or dot
    - Collapse consecutive hyphens
    - Strip leading/trailing hyphens and dots
    """
    name = name.replace(' ', '-').replace('_', '-')
    name = re.sub(r'[^a-zA-Z0-9\-.]', '', name)
    name = re.sub(r'-{2,}', '-', name)
    name = name.strip('-.')
    return name or 'untitled'


projects_bp = Blueprint('projects', __name__)


def get_pm(project_name: str) -> ProjectManager:
    """Get ProjectManager: reuse running task's PM if available, else load from disk."""
    tm = TaskManager()
    projects_dir = current_app.config['PROJECTS_DIR']
    return tm.get_or_load_pm(project_name, projects_dir)


def get_uploads_dir() -> str:
    """Get the uploads directory path."""
    uploads_dir = os.path.join(
        os.path.dirname(current_app.config['PROJECTS_DIR']),
        'uploads'
    )
    os.makedirs(uploads_dir, exist_ok=True)
    return uploads_dir


@projects_bp.route('/api/upload', methods=['POST'])
def upload_file():
    """Upload an EPUB file."""
    if 'file' not in request.files:
        return jsonify({'error': '未提供檔案'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '未選擇檔案'}), 400

    if not file.filename.lower().endswith('.epub'):
        return jsonify({'error': '請上傳 EPUB 檔案'}), 400

    # Save file with unique name
    uploads_dir = get_uploads_dir()
    unique_name = f"{uuid.uuid4()}_{file.filename}"
    file_path = os.path.join(uploads_dir, unique_name)
    file.save(file_path)

    return jsonify({
        'status': 'ok',
        'path': file_path,
        'filename': file.filename
    })


@projects_bp.route('/api/epub/metadata', methods=['POST'])
def get_epub_metadata():
    """Get metadata from an uploaded EPUB file."""
    data = request.get_json()
    if not data or 'path' not in data:
        return jsonify({'error': '未提供檔案路徑'}), 400

    epub_path = data['path']
    if not os.path.exists(epub_path):
        return jsonify({'error': '檔案不存在'}), 404

    try:
        extractor = EPUBExtractor(epub_path)
        extractor.load()
        metadata = extractor.get_metadata()
        chapters = extractor.extract_chapters()

        # Calculate word count
        total_words = sum(len(ch.text_content.split()) for ch in chapters)

        # Check if project already exists
        projects_dir = current_app.config['PROJECTS_DIR']
        # Use original filename for project name
        original_name = os.path.splitext(os.path.basename(epub_path))[0]
        # Remove UUID prefix if present
        if '_' in original_name:
            original_name = '_'.join(original_name.split('_')[1:])
        project_name = sanitize_project_name(original_name)
        project_dir = os.path.join(projects_dir, project_name)
        pm = ProjectManager(project_dir)

        return jsonify({
            'title': metadata.get('title', '未知書名'),
            'author': metadata.get('author', '未知作者'),
            'language': metadata.get('language', 'en'),
            'chapters': len(chapters),
            'word_count': total_words,
            'project_name': project_name,
            'project_exists': pm.exists()
        })
    except Exception as e:
        return jsonify({'error': f'無法解析 EPUB：{str(e)}'}), 400


@projects_bp.route('/api/projects', methods=['POST'])
def create_project():
    """Create a new translation project from an uploaded EPUB."""
    data = request.get_json()
    if not data or 'epub_path' not in data:
        return jsonify({'error': '未提供 EPUB 路徑'}), 400

    epub_path = data['epub_path']
    if not os.path.exists(epub_path):
        return jsonify({'error': 'EPUB 檔案不存在'}), 404

    try:
        # Extract EPUB
        extractor = EPUBExtractor(epub_path)
        extractor.load()
        metadata = extractor.get_metadata()
        chapters = extractor.extract_chapters()

        # Segment content
        segmenter = Segmenter()
        all_segments = []
        for chapter in chapters:
            segments = segmenter.segment_chapter(chapter.id, chapter.body)
            all_segments.extend(segments)

        # Create project
        projects_dir = current_app.config['PROJECTS_DIR']
        # Derive project name from original filename
        original_name = os.path.splitext(os.path.basename(epub_path))[0]
        if '_' in original_name:
            original_name = '_'.join(original_name.split('_')[1:])
        project_name = sanitize_project_name(data.get('project_name') or original_name)
        project_dir = os.path.join(projects_dir, project_name)

        # A project of the same name is replaced: as for a delete, no task,
        # other process or request may use it meanwhile, and nothing of it
        # is overwritten before that is known
        with project_mutex(project_dir):
            if TaskManager().task_active(project_name):
                return jsonify({'error': '專案正在翻譯中，請先停止翻譯再重新建立'}), 409
            # Under the mutex: a delete cannot remove it before the lock
            os.makedirs(project_dir, exist_ok=True)
            try:
                lock = ProjectLock(project_dir)
                lock.acquire()
            except ProjectLockedError as e:
                return jsonify({'error': str(e)}), 409
            try:
                final_epub_path = os.path.join(project_dir, 'source.epub')
                # The new book takes the old one's place only once its
                # project is saved: a failure leaves the old pair intact
                tmp_epub_path = f'{final_epub_path}.{uuid.uuid4().hex}.tmp'
                try:
                    shutil.copy2(epub_path, tmp_epub_path)
                    pm = ProjectManager(project_dir)
                    pm.create(
                        epub_path=final_epub_path,
                        title=metadata.get('title', '未知書名'),
                        author=metadata.get('author', '未知作者'),
                        segments=all_segments,
                        source_sha256=sha256_file(tmp_epub_path),
                        segmentation=segmenter.rules,
                    )
                    os.replace(tmp_epub_path, final_epub_path)
                except BaseException:
                    if os.path.exists(tmp_epub_path):
                        os.remove(tmp_epub_path)
                    raise
                # The old book's failure diagnostics
                shutil.rmtree(os.path.join(project_dir, 'log', 'failed'), ignore_errors=True)
            finally:
                lock.release()

        # Clean up uploaded file
        if epub_path.startswith(get_uploads_dir()):
            os.remove(epub_path)

        return jsonify({
            'status': 'ok',
            'name': project_name,
            'title': pm.data.title,
            'segments': len(all_segments),
            # Chapters that could not be parsed and were not segmented
            'unparsed_chapters': [chapter_id for chapter_id, _ in extractor.failed],
        })
    except EPUBError as e:
        return jsonify({'error': f'無法解析 EPUB：{e}'}), 400
    except Exception as e:
        return jsonify({'error': f'建立專案失敗：{str(e)}'}), 500


# --- Project detail pages ---

@projects_bp.route('/project/<project_name>')
def project_detail(project_name: str):
    """Redirect to overview tab."""
    return redirect(f'/project/{project_name}/overview')


@projects_bp.route('/project/<project_name>/overview')
def project_overview(project_name: str):
    """Show project overview tab."""
    pm = get_pm(project_name)
    progress = pm.get_progress()
    chapters = pm.get_chapters()

    # Calculate word count statistics
    total_words = sum(s.word_count for s in pm.data.segments)
    # passthrough code is done but not translated: not counted
    translated_words = sum(s.word_count for s in pm.data.segments
                           if s.status in ('translated', 'reviewed') and not s.passthrough)

    return render_template('project/overview.html',
                           project_name=project_name,
                           title=pm.data.title,
                           author=pm.data.author,
                           progress=progress,
                           chapters=chapters,
                           total_words=total_words,
                           translated_words=translated_words,
                           active_tab='overview')


@projects_bp.route('/project/<project_name>/glossary')
def project_glossary(project_name: str):
    """Show glossary management tab."""
    pm = get_pm(project_name)
    progress = pm.get_progress()
    return render_template('project/glossary.html',
                           project_name=project_name,
                           title=pm.data.title,
                           author=pm.data.author,
                           progress=progress,
                           active_tab='glossary')


@projects_bp.route('/project/<project_name>/translation')
def project_translation(project_name: str):
    """Show translation control tab."""
    pm = get_pm(project_name)
    progress = pm.get_progress()
    return render_template('project/translation.html',
                           project_name=project_name,
                           title=pm.data.title,
                           author=pm.data.author,
                           progress=progress,
                           active_tab='translation')


@projects_bp.route('/project/<project_name>/export')
def project_export(project_name: str):
    """Show export tab."""
    pm = get_pm(project_name)
    progress = pm.get_progress()
    return render_template('project/export.html',
                           project_name=project_name,
                           title=pm.data.title,
                           author=pm.data.author,
                           progress=progress,
                           active_tab='export')


# --- Project detail APIs ---

@projects_bp.route('/api/projects/<project_name>')
def api_project_detail(project_name: str):
    """Get project details as JSON."""
    try:
        pm = get_pm(project_name)
        progress = pm.get_progress()
        return jsonify({
            'name': project_name,
            'title': pm.data.title,
            'author': pm.data.author,
            'model': pm.data.model,
            'progress': progress
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 404


@projects_bp.route('/api/projects/<project_name>', methods=['DELETE'])
def delete_project(project_name: str):
    """Delete a project and all its files."""
    projects_dir = current_app.config['PROJECTS_DIR']
    project_dir = os.path.join(projects_dir, project_name)

    if not os.path.isdir(project_dir):
        return jsonify({'error': '專案不存在'}), 404

    # Holding the mutex (under which a start registers its task) and the
    # lock: no task, other process or request writes to the project while
    # it is removed
    with project_mutex(project_dir):
        if TaskManager().task_active(project_name):
            return jsonify({'error': '專案正在翻譯中，請先停止翻譯再刪除'}), 409
        # Not a project (no project.json): no lock file is created in it
        if not os.path.isfile(os.path.join(project_dir, ProjectManager.PROJECT_FILE)):
            return jsonify({'error': '專案不存在'}), 404
        try:
            lock = ProjectLock(project_dir)
            lock.acquire()
        except ProjectLockedError as e:
            return jsonify({'error': str(e)}), 409
        try:
            shutil.rmtree(project_dir)
        finally:
            lock.release()
    return jsonify({'status': 'ok'})


@projects_bp.route('/api/projects/<project_name>/chapters')
def api_project_chapters(project_name: str):
    """Get chapter list for a project."""
    try:
        pm = get_pm(project_name)
        chapters = pm.get_chapters()
        return jsonify(chapters)
    except Exception as e:
        return jsonify({'error': str(e)}), 404
