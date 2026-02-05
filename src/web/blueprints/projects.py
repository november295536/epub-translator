"""Projects blueprint for project management APIs and detail pages."""

from __future__ import annotations

import os
import shutil
import uuid
from flask import Blueprint, render_template, redirect, request, jsonify, current_app

from src.core.project import ProjectManager
from src.core.segmenter import Segmenter
from src.epub.extractor import EPUBExtractor


projects_bp = Blueprint('projects', __name__)


def get_pm(project_name: str) -> ProjectManager:
    """Get ProjectManager instance for the specified project."""
    projects_dir = current_app.config['PROJECTS_DIR']
    project_dir = os.path.join(projects_dir, project_name)
    pm = ProjectManager(project_dir)
    pm.load()
    return pm


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
        project_name = original_name.replace(' ', '-')
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
            segments = segmenter.segment_chapter(chapter.id, chapter.html_content)
            all_segments.extend(segments)

        # Create project
        projects_dir = current_app.config['PROJECTS_DIR']
        # Derive project name from original filename
        original_name = os.path.splitext(os.path.basename(epub_path))[0]
        if '_' in original_name:
            original_name = '_'.join(original_name.split('_')[1:])
        project_name = data.get('project_name') or original_name.replace(' ', '-')
        project_dir = os.path.join(projects_dir, project_name)

        # Copy EPUB to project directory
        os.makedirs(project_dir, exist_ok=True)
        final_epub_path = os.path.join(project_dir, 'source.epub')

        import shutil
        shutil.copy2(epub_path, final_epub_path)

        # Create project
        pm = ProjectManager(project_dir)
        pm.create(
            epub_path=final_epub_path,
            title=metadata.get('title', '未知書名'),
            author=metadata.get('author', '未知作者'),
            segments=all_segments
        )

        # Clean up uploaded file
        if epub_path.startswith(get_uploads_dir()):
            os.remove(epub_path)

        return jsonify({
            'status': 'ok',
            'name': project_name,
            'title': pm.data.title,
            'segments': len(all_segments)
        })
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
    translated_words = sum(s.word_count for s in pm.data.segments
                          if s.status in ('translated', 'reviewed'))

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

    shutil.rmtree(project_dir)
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
