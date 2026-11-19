"""Tests for the read-only check scripts under scripts/verify/."""

import importlib.util
import json
import os

import pytest
from ebooklib import epub

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    path = os.path.join(ROOT, 'scripts', 'verify', f'{name}.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


code_check = _load('phase0_code_segments')
align_check = _load('phase0_builder_alignment')


def _code_seg(seg_id, text, translated, status='translated', seg_type='code'):
    return {'segment_id': seg_id, 'segment_type': seg_type, 'status': status,
            'text': text, 'translated_text': translated}


def _write_project(directory, segments):
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, 'project.json'), 'w', encoding='utf-8') as f:
        json.dump({'segments': segments}, f)


class TestCodeLines:

    def test_comment_lines_dropped(self):
        text = '# note\n#!/bin/sh\n#\n// note\n    # indented note\nx = 1'
        assert code_check.code_lines(text) == ['x = 1']

    def test_hash_directives_are_code(self):
        text = '#define FOO_H\n#include <x.h>\n#[derive(Debug)]'
        assert code_check.code_lines(text) == text.splitlines()

    def test_indentation_kept_trailing_space_and_blank_ends_ignored(self):
        assert code_check.code_lines('\n\n    x = 1   \n\n') == ['    x = 1']


class TestCheckProject:

    def test_classification(self, tmp_path):
        source = '    # load\n    x = 1\n#define FOO_H'
        segments = [
            _code_seg('same', source, source),
            _code_seg('comment_only', source, source.replace('# load', '# 載入')),
            _code_seg('indent_lost', source, source.replace('    x = 1', 'x = 1')),
            _code_seg('define_lost', source, source.replace('#define', 'define')),
            _code_seg('empty', source, ''),
            _code_seg('blank', source, '  \n', status='reviewed'),
            _code_seg('missing', source, None),
            _code_seg('pending', source, 'garbage', status='pending'),
            _code_seg('text', 'prose', 'other', seg_type='text'),
        ]
        _write_project(str(tmp_path), segments)

        translated, differing, empty = code_check.check_project(
            str(tmp_path / 'project.json'))

        assert translated == 7
        assert differing == ['indent_lost', 'define_lost']
        assert empty == ['empty', 'blank', 'missing']


class TestCodeCheckMain:

    def test_labels_count_only_project_directories(self, tmp_path, capsys):
        _write_project(str(tmp_path / 'b-proj'), [_code_seg('s1', 'x = 1', 'y = 1')])
        os.makedirs(tmp_path / 'a-not-a-project')
        _write_project(str(tmp_path / 'c-proj'), [])

        code = code_check.main(['--projects-dir', str(tmp_path), '--anonymize'])

        out = capsys.readouterr().out
        assert code == 0
        assert 'reporting only, not a pass/fail check' in out.splitlines()[0]
        assert 'project-1: translated code segments 1, differ outside comment lines 1' in out
        assert 'project-2: translated code segments 0' in out
        assert 'b-proj' not in out and 'c-proj' not in out
        assert 's1' not in out

    def test_names_and_ids_without_anonymize(self, tmp_path, capsys):
        _write_project(str(tmp_path / 'b-proj'), [_code_seg('s1', 'x = 1', '')])
        code_check.main(['--projects-dir', str(tmp_path)])
        out = capsys.readouterr().out
        assert 'b-proj:' in out and 'empty: s1' in out

    def test_unreadable_project_is_reported_and_fails(self, tmp_path, capsys):
        os.makedirs(tmp_path / 'secret-name')
        (tmp_path / 'secret-name' / 'project.json').write_text('{not json')

        code = code_check.main(['--projects-dir', str(tmp_path), '--anonymize'])

        out = capsys.readouterr().out
        assert code == 1
        assert 'ERROR project-1: cannot read project' in out
        assert 'secret-name' not in out

    def test_missing_directory_path_not_printed_when_anonymized(self, tmp_path, capsys):
        missing = str(tmp_path / 'private-dir-name')
        code = code_check.main(['--projects-dir', missing, '--anonymize'])
        out = capsys.readouterr().out
        assert code == 2
        assert 'private-dir-name' not in out
        assert '<projects-dir>' in out


def _write_epub_project(directory, body, segments):
    """A project dir with source.epub (one chapter) and project.json."""
    os.makedirs(directory, exist_ok=True)
    book = epub.EpubBook()
    book.set_identifier('align-test')
    book.set_title('Align Test')
    book.set_language('en')
    chapter = epub.EpubHtml(title='One', file_name='one.xhtml', lang='en')
    chapter.content = f'<html><body>{body}</body></html>'
    book.add_item(chapter)
    book.spine = [chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(os.path.join(directory, 'source.epub'), book)
    for seg in segments:
        seg['chapter_id'] = chapter.id
    _write_project(directory, segments)


class TestAlignmentCheck:

    BODY = '<div class="box"><!-- note --><p>One</p><p>Two</p></div><p>Three</p>'

    def test_legacy_project_passes(self, tmp_path, capsys):
        _write_epub_project(str(tmp_path / 'old'), self.BODY, [{
            'segment_type': 'text',
            'html': '<div class="box"><!-- note --><p>One</p><p>Two</p></div>\n<p>Three</p>',
        }])

        code = align_check.main(['--projects-dir', str(tmp_path), '--anonymize'])

        out = capsys.readouterr().out
        assert code == 0
        assert ('PASS project-1: chapters 1, with comment-dependent walk 1, '
                'walk changed vs main 0') in out

    def test_new_style_segments_fail(self, tmp_path, capsys):
        _write_epub_project(str(tmp_path / 'new'), self.BODY, [
            {'segment_type': 'text', 'html': '<p>One</p>\n<p>Two</p>\n<p>Three</p>'},
        ])

        code = align_check.main(['--projects-dir', str(tmp_path)])

        out = capsys.readouterr().out
        assert code == 1
        assert 'FAIL new: chapters 1' in out
        assert 'walk changed vs main 1' in out

    def test_unreadable_source_fails_without_path(self, tmp_path, capsys):
        _write_project(str(tmp_path / 'secret-name'),
                       [{'chapter_id': 'c', 'segment_type': 'text', 'html': '<p>x</p>'}])

        code = align_check.main(['--projects-dir', str(tmp_path), '--anonymize'])

        out = capsys.readouterr().out
        assert code == 1
        assert 'FAIL project-1: cannot read project or source' in out
        assert 'secret-name' not in out

    def test_missing_directory(self, tmp_path, capsys):
        code = align_check.main(['--projects-dir', str(tmp_path / 'nope'), '--anonymize'])
        assert code == 2
        assert 'nope' not in capsys.readouterr().out
