"""Projects blueprint for project management APIs."""

from __future__ import annotations

import os
import uuid
from flask import Blueprint, request, jsonify, current_app

from src.core.project import ProjectManager
from src.core.segmenter import Segmenter
from src.epub.extractor import EPUBExtractor


projects_bp = Blueprint('projects', __name__)


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
