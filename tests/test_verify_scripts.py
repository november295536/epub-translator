"""Tests for the read-only check scripts under scripts/verify/."""

import importlib.util
import json
import os
import subprocess
import sys

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
            _code_seg('pending', source, 'garbage', status='pending'),
            _code_seg('text', 'prose', 'other', seg_type='text'),
            _code_seg('same', source, source),
            _code_seg('comment_only', source, source.replace('# load', '# 載入')),
            _code_seg('indent_lost', source, source.replace('    x = 1', 'x = 1')),
            _code_seg('define_lost', source, source.replace('#define', 'define')),
            _code_seg('empty', source, ''),
            _code_seg('blank', source, '  \n', status='reviewed'),
            _code_seg('missing', source, None),
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


def _assert_help(stdout, description, anonymize_help):
    """The description is exactly the docstring's first line and each option's
    help text is exactly as written (whitespace normalised)."""
    import re
    text = ' '.join(stdout.split())
    assert re.search(re.escape(description) + r' (optional arguments|options):', text), text
    assert ('--projects-dir PROJECTS_DIR Directory that holds the project folders '
            '(default: projects) --anonymize ' + anonymize_help) in text, text
    assert text.endswith(anonymize_help), text


def _run_script(name, args, cwd):
    """Run a check script as a separate process from a directory outside the repo."""
    path = os.path.join(ROOT, 'scripts', 'verify', f'{name}.py')
    return subprocess.run([sys.executable, path] + args, cwd=cwd,
                          capture_output=True, text=True)


class TestCodeCheckOutput:

    def _make(self, root):
        (root / 'a-broken').mkdir()
        (root / 'a-broken' / 'project.json').write_text('{not json')
        _write_project(str(root / 'b-proj'), [
            _code_seg('b1', 'x = 1', 'y = 1'),
            _code_seg('b2', 'x = 2', 'y = 2'),
            _code_seg('b3', 'x = 3', ''),
            _code_seg('b4', 'x = 4', 'x = 4'),
            _code_seg('b5', 'x = 5', None),
        ])
        _write_project(str(root / 'c-proj'), [
            _code_seg('c1', 'x = 1', 'z = 1'),
            _code_seg('c2', 'x = 2', ''),
        ])

    def test_exact_output_named(self, tmp_path, capsys):
        self._make(tmp_path)
        assert code_check.main(['--projects-dir', str(tmp_path)]) == 1
        assert capsys.readouterr().out.splitlines() == [
            code_check.HEADER,
            'ERROR a-broken: cannot read project (JSONDecodeError)',
            'b-proj: translated code segments 5, differ outside comment lines 2, '
            'empty translation 2; differ: b1 b2; empty: b3 b5',
            'c-proj: translated code segments 2, differ outside comment lines 1, '
            'empty translation 1; differ: c1; empty: c2',
            'TOTAL projects 3  translated code segments 7  differ outside comment lines 3  '
            'empty translation 3  unreadable 1',
        ]
        assert code_check.HEADER == (
            'Phase 0 code segment report (candidates for re-translation; '
            'reporting only, not a pass/fail check)')

    def test_two_unreadable_projects_counted(self, tmp_path, capsys):
        for name in ('p1', 'p2'):
            (tmp_path / name).mkdir()
            (tmp_path / name / 'project.json').write_text('[]')
        assert code_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 1
        out = capsys.readouterr().out
        assert out.splitlines()[-1].endswith('unreadable 2')

    def test_missing_directory_named(self, tmp_path, capsys):
        missing = str(tmp_path / 'nope')
        assert code_check.main(['--projects-dir', missing]) == 2
        assert capsys.readouterr().out.splitlines()[-1] == (
            f'ERROR cannot list projects directory {missing} (FileNotFoundError)')

    def test_missing_directory_anonymized(self, tmp_path, capsys):
        assert code_check.main(['--projects-dir', str(tmp_path / 'nope'), '--anonymize']) == 2
        assert capsys.readouterr().out.splitlines()[-1] == (
            'ERROR cannot list projects directory <projects-dir> (FileNotFoundError)')

    def test_runs_as_script_with_default_directory(self, tmp_path):
        _write_project(str(tmp_path / 'projects' / 'p'), [_code_seg('s', 'x', 'y')])
        result = _run_script('phase0_code_segments', ['--anonymize'], str(tmp_path))
        assert result.returncode == 0
        assert result.stdout.splitlines()[1].startswith('project-1: translated code segments 1')

    def test_help(self, tmp_path):
        result = _run_script('phase0_code_segments', ['--help'], str(tmp_path))
        _assert_help(result.stdout,
                     'Phase 0 check: list translated code segments that may have been corrupted.',
                     'Print project-N labels; omit names, segment IDs and paths')


def _write_multi_chapter_project(directory, segments_by_chapter, epub_path=None):
    """source.epub with chapters c0 (no segments), c1 (div with comment) and
    c2 (body comment); project.json with the given segments per chapter."""
    os.makedirs(directory, exist_ok=True)
    book = epub.EpubBook()
    book.set_identifier('multi')
    book.set_title('Multi')
    book.set_language('en')
    bodies = {
        'c0': '<p>Zero</p>',
        'c1': '<div class="box"><!-- note --><p>One</p><p>Two</p></div>',
        'c2': '<p>Three</p><!-- c --><p>Four</p>',
    }
    chapters = []
    for uid, body in bodies.items():
        chapter = epub.EpubHtml(uid=uid, title=uid, file_name=f'{uid}.xhtml', lang='en')
        chapter.content = f'<html><body>{body}</body></html>'
        book.add_item(chapter)
        chapters.append(chapter)
    book.spine = chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    target = epub_path or os.path.join(directory, 'source.epub')
    epub.write_epub(target, book)
    segments = [dict(seg, chapter_id=cid)
                for cid, segs in segments_by_chapter.items() for seg in segs]
    with open(os.path.join(directory, 'project.json'), 'w', encoding='utf-8') as f:
        json.dump({'segments': segments, 'epub_path': target}, f)


LEGACY_SEGMENTS = {
    'c1': [{'segment_type': 'text',
            'html': '<div class="box"><!-- note --><p>One</p><p>Two</p></div>'}],
    'c2': [{'segment_type': 'text', 'html': '<p>Three</p>\n c \n<p>Four</p>'}],
}
NEW_SEGMENTS = {
    'c1': [{'segment_type': 'text', 'html': '<p>One</p>\n<p>Two</p>'}],
    'c2': [{'segment_type': 'text', 'html': '<p>Three</p>\n<p>Four</p>'}],
}


class TestAlignmentOutput:

    def _make(self, root):
        _write_project(str(root / 'a-broken'), [])            # no source.epub
        _write_multi_chapter_project(str(root / 'b-old'), LEGACY_SEGMENTS)
        _write_multi_chapter_project(str(root / 'c-new'), NEW_SEGMENTS)
        _write_multi_chapter_project(str(root / 'd-new'), NEW_SEGMENTS)

    def test_exact_output_named(self, tmp_path, capsys):
        self._make(tmp_path)
        assert align_check.main(['--projects-dir', str(tmp_path)]) == 1
        assert capsys.readouterr().out.splitlines() == [
            'FAIL a-broken: cannot read project or source (FileNotFoundError)',
            'PASS b-old: chapters 2, with comment-dependent walk 2, walk changed vs main 0',
            'FAIL c-new: chapters 2, with comment-dependent walk 2, walk changed vs main 2: c1 c2',
            'FAIL d-new: chapters 2, with comment-dependent walk 2, walk changed vs main 2: c1 c2',
            'TOTAL projects 4  chapters 6  comment-dependent 6  changed vs main 4  '
            'failed projects 3',
        ]

    def test_exact_output_anonymized(self, tmp_path, capsys):
        self._make(tmp_path)
        assert align_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 1
        lines = capsys.readouterr().out.splitlines()
        assert lines[2] == ('FAIL project-3: chapters 2, with comment-dependent walk 2, '
                            'walk changed vs main 2')

    def test_two_unreadable_projects_counted(self, tmp_path, capsys):
        _write_project(str(tmp_path / 'p1'), [])
        _write_project(str(tmp_path / 'p2'), [])
        assert align_check.main(['--projects-dir', str(tmp_path)]) == 1
        assert capsys.readouterr().out.splitlines()[-1].endswith('failed projects 2')

    def test_all_pass_exits_zero(self, tmp_path, capsys):
        _write_multi_chapter_project(str(tmp_path / 'old'), LEGACY_SEGMENTS)
        assert align_check.main(['--projects-dir', str(tmp_path)]) == 0
        assert capsys.readouterr().out.splitlines()[-1].endswith('failed projects 0')

    def test_source_from_epub_path_when_no_local_copy(self, tmp_path, capsys):
        elsewhere = str(tmp_path / 'book.epub')
        _write_multi_chapter_project(str(tmp_path / 'projects' / 'old'), LEGACY_SEGMENTS,
                                     epub_path=elsewhere)
        assert align_check.main(['--projects-dir', str(tmp_path / 'projects')]) == 0

    def test_code_segments_are_not_legacy_evidence(self, tmp_path, capsys):
        code_only = {'c1': [{'segment_type': 'code',
                             'html': '<div class="box"><!-- note --><p>One</p></div>'}]}
        _write_multi_chapter_project(str(tmp_path / 'p'), code_only)
        assert align_check.main(['--projects-dir', str(tmp_path)]) == 1

    def test_missing_directory_named(self, tmp_path, capsys):
        missing = str(tmp_path / 'nope')
        assert align_check.main(['--projects-dir', missing]) == 2
        assert capsys.readouterr().out.splitlines() == [
            f'ERROR cannot list projects directory {missing} (FileNotFoundError)']

    def test_missing_directory_anonymized(self, tmp_path, capsys):
        assert align_check.main(['--projects-dir', str(tmp_path / 'x'), '--anonymize']) == 2
        assert capsys.readouterr().out.splitlines() == [
            'ERROR cannot list projects directory <projects-dir> (FileNotFoundError)']

    def test_runs_as_script_quietly_with_default_directory(self, tmp_path):
        _write_multi_chapter_project(str(tmp_path / 'projects' / 'old'), LEGACY_SEGMENTS)
        result = _run_script('phase0_builder_alignment', ['--anonymize'], str(tmp_path))
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith('PASS project-1: chapters 2')
        assert result.stderr == ''

    def test_help(self, tmp_path):
        result = _run_script('phase0_builder_alignment', ['--help'], str(tmp_path))
        _assert_help(result.stdout,
                     'Phase 0 check: existing projects keep the builder walk they had on main.',
                     'Print project-N labels; omit names, chapter IDs and paths')
