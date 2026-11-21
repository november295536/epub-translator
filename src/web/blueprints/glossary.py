"""Glossary blueprint for terminology management."""

from __future__ import annotations

import os
import threading
from dataclasses import asdict
from flask import Blueprint, request, jsonify, current_app

import src.llm as llm
from src.core.project import ProjectManager
from src.core.segmenter import SegmentType
from src.glossary.glossary_manager import GlossaryManager
from src.glossary.term_extractor import ExtractionError, TermExtractor


glossary_bp = Blueprint('glossary', __name__)

# Track extraction tasks
_extraction_tasks: dict[str, dict] = {}


def _get_glossary_manager(project_name: str) -> GlossaryManager:
    """Get GlossaryManager for the specified project."""
    projects_dir = current_app.config['PROJECTS_DIR']
    glossary_path = os.path.join(projects_dir, project_name, 'glossary.json')
    gm = GlossaryManager(glossary_path)
    gm.load()
    return gm


def _bad_person_fields(data: dict) -> bool:
    """`gender` or `note` given and not text."""
    return any(not isinstance(data[k], str) for k in ('gender', 'note') if k in data)


def _get_pm(project_name: str) -> ProjectManager:
    """Get ProjectManager for the specified project."""
    projects_dir = current_app.config['PROJECTS_DIR']
    project_dir = os.path.join(projects_dir, project_name)
    pm = ProjectManager(project_dir)
    pm.load()
    return pm


@glossary_bp.route('/api/projects/<project_name>/glossary')
def list_terms(project_name: str):
    """List all glossary terms."""
    gm = _get_glossary_manager(project_name)
    terms = [asdict(e) for e in gm.get_all_terms()]
    return jsonify(terms)


@glossary_bp.route('/api/projects/<project_name>/glossary', methods=['POST'])
def add_term(project_name: str):
    """Add a new glossary term."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or 'term' not in data or 'translation' not in data:
        return jsonify({'error': '請提供 term 和 translation'}), 400
    if _bad_person_fields(data):
        return jsonify({'error': 'gender 和 note 必須是文字'}), 400

    gm = _get_glossary_manager(project_name)
    entry = gm.add_term(
        term=data['term'],
        translation=data['translation'],
        category=data.get('category', ''),
        frequency=data.get('frequency', 0),
        gender=data.get('gender', ''),
        note=data.get('note', ''),
    )
    return jsonify({'status': 'ok', 'entry': asdict(entry)})


@glossary_bp.route('/api/projects/<project_name>/glossary/<term>', methods=['PUT'])
def update_term(project_name: str, term: str):
    """Update a glossary term."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return jsonify({'error': '請提供更新資料'}), 400
    if _bad_person_fields(data):
        return jsonify({'error': 'gender 和 note 必須是文字'}), 400

    gm = _get_glossary_manager(project_name)
    entry = gm.update_term(
        term=term,
        translation=data.get('translation'),
        category=data.get('category'),
        gender=data.get('gender'),
        note=data.get('note'),
    )
    if entry is None:
        return jsonify({'error': f'找不到術語：{term}'}), 404
    return jsonify({'status': 'ok', 'entry': asdict(entry)})


@glossary_bp.route('/api/projects/<project_name>/glossary/<term>', methods=['DELETE'])
def delete_term(project_name: str, term: str):
    """Delete a glossary term."""
    gm = _get_glossary_manager(project_name)
    if gm.remove_term(term):
        return jsonify({'status': 'ok'})
    return jsonify({'error': f'找不到術語：{term}'}), 404


@glossary_bp.route('/api/projects/<project_name>/glossary/batch', methods=['DELETE'])
def batch_delete_terms(project_name: str):
    """Delete multiple glossary terms at once."""
    data = request.get_json()
    if not data or 'terms' not in data or not isinstance(data['terms'], list):
        return jsonify({'error': '請提供 terms 陣列'}), 400

    gm = _get_glossary_manager(project_name)
    deleted = 0
    for term in data['terms']:
        if gm.remove_term(term):
            deleted += 1

    return jsonify({'status': 'ok', 'deleted': deleted})


@glossary_bp.route('/api/projects/<project_name>/glossary/extract', methods=['POST'])
def extract_terms(project_name: str):
    """Start auto term extraction in background."""
    if project_name in _extraction_tasks and _extraction_tasks[project_name].get('running'):
        return jsonify({'error': '已有提取任務正在執行'}), 409

    # Get project segments for extraction
    pm = _get_pm(project_name)
    # All text segments; TermExtractor splits them into bounded batches
    segments = [{'text': s.text} for s in pm.data.segments
                if s.segment_type != SegmentType.CODE]
    model, backend = pm.data.model, pm.data.backend

    _extraction_tasks[project_name] = {
        'running': True,
        'total_batches': 0,
        'completed_batches': 0,
        'extracted_count': 0,
        'error': None
    }

    # Get config for thread
    projects_dir = current_app.config['PROJECTS_DIR']

    def run_extraction():
        try:
            extractor = TermExtractor(model=model, backend=llm.backend_for(backend))
            stopped = None
            try:
                terms = extractor.extract_from_segments(segments)
            except ExtractionError as e:
                # Keep the terms of the batches finished before the stop
                stopped, terms = e, e.terms

            # Add extracted terms to glossary
            glossary_path = os.path.join(projects_dir, project_name, 'glossary.json')
            gm = GlossaryManager(glossary_path)
            gm.load()
            count = 0
            for t in terms:
                if not gm.get_term(t['term']):
                    gm.add_term(
                        term=t['term'],
                        translation=t['translation'],
                        category=t.get('category', '')
                    )
                    count += 1

            _extraction_tasks[project_name]['extracted_count'] = count
            if stopped is not None:
                _extraction_tasks[project_name]['error'] = (
                    f'提取中斷：{stopped}（已加入先前批次的 {count} 個術語）')
        except Exception as e:
            _extraction_tasks[project_name]['error'] = str(e)
        finally:
            _extraction_tasks[project_name]['running'] = False

    thread = threading.Thread(target=run_extraction, daemon=True)
    thread.start()

    return jsonify({'status': 'started'})


@glossary_bp.route('/api/projects/<project_name>/glossary/extract/status')
def extraction_status(project_name: str):
    """Get term extraction status."""
    task = _extraction_tasks.get(project_name)
    if task is None:
        return jsonify({'status': 'idle'})

    if task['running']:
        return jsonify({'status': 'running'})

    if task['error']:
        return jsonify({'status': 'error', 'error': task['error']})

    return jsonify({
        'status': 'completed',
        'extracted_count': task['extracted_count']
    })
