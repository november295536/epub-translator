"""Tests for the read-only check scripts under scripts/verify/."""

import importlib.util
import json
import re
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    path = os.path.join(ROOT, 'scripts', 'verify', f'{name}.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


code_check = _load('phase0_code_segments')


def _code_seg(seg_id, text, translated, status='translated', seg_type='code'):
    return {'segment_id': seg_id, 'segment_type': seg_type, 'status': status,
            'text': text, 'translated_text': translated}


def _write_project(directory, segments):
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, 'project.json'), 'w', encoding='utf-8') as f:
        json.dump({'segments': segments}, f)


class TestCodeLines:

    def test_comment_wording_ignored(self):
        text = '# note\n#!/bin/sh\n#\n// note\n    # indented note\nx = 1'
        assert code_check.code_lines(text) == ['#', '#', '#', '#', '    #', 'x = 1']

    @pytest.mark.parametrize('source, translated', [
        ('    // load config\n    value();', '// 載入設定\n    value();'),
        ('    # init\n    x = 1', '# 初始化\n    x = 1'),
    ])
    def test_lost_comment_indentation_differs(self, source, translated):
        assert code_check.code_lines(source) != code_check.code_lines(translated)
        assert code_check.code_lines(source) == code_check.code_lines(
            source.replace('load config', '載入設定').replace('init', '初始化'))

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

    @pytest.mark.parametrize('translated', ['XXXX', ''])
    def test_code_segment_without_text_is_unreadable(self, tmp_path, capsys, translated):
        seg = _code_seg('s', 'unused', translated)
        del seg['text']
        _write_project(str(tmp_path / 'p'), [seg])

        assert code_check.main(['--projects-dir', str(tmp_path)]) == 1
        assert 'ERROR p: cannot read project (KeyError)' in capsys.readouterr().out

    @pytest.mark.parametrize('translated', ['XXXX', ''])
    def test_code_segment_with_null_text_is_unreadable(self, tmp_path, capsys, translated):
        _write_project(str(tmp_path / 'p'), [_code_seg('s', None, translated)])

        assert code_check.main(['--projects-dir', str(tmp_path)]) == 1
        assert 'ERROR p: cannot read project (TypeError)' in capsys.readouterr().out

    def test_missing_directory_path_not_printed_when_anonymized(self, tmp_path, capsys):
        missing = str(tmp_path / 'private-dir-name')
        code = code_check.main(['--projects-dir', missing, '--anonymize'])
        out = capsys.readouterr().out
        assert code == 2
        assert 'private-dir-name' not in out
        assert '<projects-dir>' in out


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


load_check = _load('phase1_load_projects')


def _write_loadable(directory, statuses, extra=None):
    os.makedirs(directory, exist_ok=True)
    segments = [{'segment_id': f'c1_seg{i:04d}', 'chapter_id': 'c1', 'html': '<p>x</p>',
                 'text': 'x', 'word_count': 1, 'segment_type': 'text', 'status': status,
                 'translated_text': '', 'translated_at': ''}
                for i, status in enumerate(statuses)]
    data = {'epub_path': '/b.epub', 'project_dir': directory, 'title': 'T',
            'author': 'A', 'created_at': '', 'updated_at': '', 'model': 'sonnet',
            'segments': segments}
    data.update(extra or {})
    with open(os.path.join(directory, 'project.json'), 'w') as f:
        json.dump(data, f)


class TestLoadCheck:

    def test_counts_and_pass(self, tmp_path, capsys):
        _write_loadable(str(tmp_path / 'zeta'), ['pending', 'translated', 'reviewed'])
        _write_loadable(str(tmp_path / 'alpha'), ['failed', 'failed'])
        (tmp_path / 'not-a-project').mkdir()
        assert load_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 0
        out = capsys.readouterr().out.splitlines()
        assert out == [
            'PASS project-1: segments 2, pending 0, translated 0, reviewed 0, failed 2,'
            ' round trip equal',
            'PASS project-2: segments 3, pending 1, translated 1, reviewed 1, failed 0,'
            ' round trip equal',
            'TOTAL projects 2  segments 5  pending 1  translated 1  reviewed 1  failed 2'
            '  round trip equal 2  model added 0  differs 0  failed projects 0',
        ]

    def test_names_without_anonymize(self, tmp_path, capsys):
        _write_loadable(str(tmp_path / 'alpha'), ['pending'])
        assert load_check.main(['--projects-dir', str(tmp_path)]) == 0
        assert capsys.readouterr().out.startswith('PASS alpha: segments 1')

    def test_load_error_fails(self, tmp_path, capsys):
        _write_loadable(str(tmp_path / 'alpha'), ['pending'], extra={'unknown_field': 1})
        _write_loadable(str(tmp_path / 'beta'), ['pending'])
        assert load_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 1
        out = capsys.readouterr().out
        assert 'FAIL project-1: cannot load (TypeError)' in out
        assert 'PASS project-2' in out
        assert 'failed projects 1' in out
        assert str(tmp_path) not in out

    def test_write_during_load_fails(self, tmp_path, capsys, monkeypatch):
        directory = str(tmp_path / 'alpha')
        _write_loadable(directory, ['pending'])
        original = load_check.ProjectManager.load

        def load_and_save(self):
            data = original(self)
            self.save()
            return data

        monkeypatch.setattr(load_check.ProjectManager, 'load', load_and_save)
        assert load_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 1
        out = capsys.readouterr().out
        assert out.startswith('FAIL project-1: segments 1')
        assert 'project.json content changed' in out
        assert 'files in the project directory changed' in out  # the lock file

    def test_touch_during_load_fails(self, tmp_path, capsys, monkeypatch):
        directory = str(tmp_path / 'alpha')
        _write_loadable(directory, ['pending'])
        original = load_check.ProjectManager.load

        def load_and_touch(self):
            data = original(self)
            os.utime(self.project_file, ns=(1, 1))
            return data

        monkeypatch.setattr(load_check.ProjectManager, 'load', load_and_touch)
        assert load_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 1
        out = capsys.readouterr().out
        assert 'project.json modification time changed' in out
        assert 'content changed' not in out

    def test_loading_leaves_files_untouched(self, tmp_path):
        directory = str(tmp_path / 'alpha')
        _write_loadable(directory, ['pending', 'failed'])
        path = os.path.join(directory, 'project.json')
        before = (open(path, 'rb').read(), os.stat(path).st_mtime_ns)
        load_check.main(['--projects-dir', str(tmp_path)])
        assert (open(path, 'rb').read(), os.stat(path).st_mtime_ns) == before
        assert os.listdir(directory) == ['project.json']

    def test_missing_directory(self, tmp_path, capsys):
        missing = str(tmp_path / 'nope')
        assert load_check.main(['--projects-dir', missing, '--anonymize']) == 2
        out = capsys.readouterr().out
        assert out == 'ERROR cannot list projects directory <projects-dir> (FileNotFoundError)\n'

    def test_runs_as_script(self, tmp_path):
        _write_loadable(str(tmp_path / 'alpha'), ['pending'])
        script = os.path.join(ROOT, 'scripts', 'verify', 'phase1_load_projects.py')
        result = subprocess.run([sys.executable, script, '--projects-dir', str(tmp_path)],
                                capture_output=True, text=True, cwd=str(tmp_path))
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith('PASS alpha')


class TestLoadCheckOutput:
    """Exact output of the Phase 1 check (mutation survivors)."""

    def _broken_load(self, monkeypatch, action):
        original = load_check.ProjectManager.load

        def load(self):
            data = original(self)
            action(self)
            return data

        monkeypatch.setattr(load_check.ProjectManager, 'load', load)

    def test_exact_fail_lines_and_count(self, tmp_path, capsys, monkeypatch):
        _write_loadable(str(tmp_path / 'a'), ['pending'])
        _write_loadable(str(tmp_path / 'b'), ['pending'])
        _write_loadable(str(tmp_path / 'c'), ['pending'], extra={'unknown_field': 1})
        self._broken_load(monkeypatch, lambda pm: pm.save())
        assert load_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 1
        out = capsys.readouterr().out.splitlines()
        problems = ('project.json content changed; project.json modification time changed; '
                    'files in the project directory changed')
        assert out == [
            'FAIL project-1: segments 1, pending 1, translated 0, reviewed 0, failed 0,'
            f' round trip equal; {problems}',
            'FAIL project-2: segments 1, pending 1, translated 0, reviewed 0, failed 0,'
            f' round trip equal; {problems}',
            'FAIL project-3: cannot load (TypeError)',
            'TOTAL projects 3  segments 2  pending 2  translated 0  reviewed 0  failed 0'
            '  round trip equal 2  model added 0  differs 0  failed projects 3',
        ]

    def test_default_projects_dir_and_help(self, tmp_path, monkeypatch, capsys):
        _write_loadable(str(tmp_path / 'projects' / 'alpha'), ['pending'])
        monkeypatch.chdir(tmp_path)
        assert load_check.main([]) == 0
        assert capsys.readouterr().out.startswith('PASS alpha')
        with pytest.raises(SystemExit):
            load_check.main(['--help'])
        help_text = ' '.join(capsys.readouterr().out.split())
        assert 'Phase 1 check: every existing project loads untouched and would save unchanged.' \
            in help_text
        assert 'For each directory' not in help_text
        assert ('--projects-dir PROJECTS_DIR Directory that holds the project folders '
                '(default: projects) --anonymize Print project-N labels; omit names and '
                'paths') in help_text
        assert help_text.endswith('omit names and paths')


# --- Review round 1, item 20: in-memory round trip ---------------------------------

class TestLoadCheckRoundTrip:

    def test_old_project_gains_only_model(self, tmp_path, capsys):
        _write_loadable(str(tmp_path / 'a'), ['translated'])
        directory = str(tmp_path / 'b')
        _write_loadable(directory, ['pending'])
        path = os.path.join(directory, 'project.json')
        with open(path) as f:
            data = json.load(f)
        del data['model']
        with open(path, 'w') as f:
            json.dump(data, f)
        before = open(path, 'rb').read()
        assert load_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[0].endswith(', round trip equal')
        assert out[1].startswith('PASS project-2') and out[1].endswith(', round trip model added')
        assert out[2].endswith('round trip equal 1  model added 1  differs 0  failed projects 0')
        assert open(path, 'rb').read() == before
        assert os.listdir(directory) == ['project.json']

    def test_other_difference_fails(self, tmp_path, capsys):
        directory = str(tmp_path / 'a')
        _write_loadable(directory, ['pending', 'pending'],
                        extra={'segmenter_version': 1})  # save() would drop it
        path = os.path.join(directory, 'project.json')
        with open(path) as f:
            data = json.load(f)
        data['segments'][1]['segment_type'] = 'heading'  # load() normalizes it
        with open(path, 'w') as f:
            json.dump(data, f)
        assert load_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[0] == ('FAIL project-1: segments 2, pending 2, translated 0, reviewed 0,'
                          " failed 0, round trip differs; round trip differs: top-level keys"
                          " ['segmenter_version'], segments 1")
        assert out[1].endswith('round trip equal 0  model added 0  differs 1  failed projects 1')

    def test_segment_count_difference_counted(self, tmp_path):
        directory = str(tmp_path / 'a')
        _write_loadable(directory, ['pending', 'pending', 'pending'])
        pm = load_check.ProjectManager(directory)
        pm.load()
        del pm.data.segments[1:]
        pm.data.title = 'changed'
        assert load_check.round_trip(pm) == ('differs', "top-level keys ['title'], segments 2")


class TestLoadCheckRoundTripExact:
    """round_trip() results (mutation survivors)."""

    def _pm(self, tmp_path, drop_model=False, title=None):
        directory = str(tmp_path / 'a')
        _write_loadable(directory, ['pending'])
        path = os.path.join(directory, 'project.json')
        with open(path) as f:
            data = json.load(f)
        if drop_model:
            del data['model']
        if title is not None:
            data['title'] = title
        with open(path, 'w') as f:
            json.dump(data, f)
        pm = load_check.ProjectManager(directory)
        pm.load()
        return pm

    def test_equal_and_model_added_have_no_detail(self, tmp_path):
        assert load_check.round_trip(self._pm(tmp_path)) == ('equal', '')
        assert load_check.round_trip(self._pm(tmp_path, drop_model=True)) == ('model added', '')

    def test_missing_model_and_another_difference(self, tmp_path):
        pm = self._pm(tmp_path, drop_model=True)
        pm.data.title = 'changed'
        assert load_check.round_trip(pm) == ('differs', "top-level keys ['model', 'title'], segments 0")


# --- Phase 2: round trip and migration of copies -------------------------------

roundtrip_check = _load('phase2_roundtrip')
migrate_check = _load('phase2_migrate_copies')

XHTML10 = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" '
           '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">\n'
           '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head>'
           '<body><!-- kept --><p>Old doctype.</p></body></html>')


def _roundtrip_project(directory, members=None, inside=True):
    """A project whose EPUB has a nav document, an XHTML item outside the
    spine, a chapter with an XHTML 1.0 DOCTYPE and a stylesheet."""
    from tests.conftest import RAW_NAV, write_raw_epub, xhtml
    os.makedirs(directory)
    # items after a non-XHTML item and after a broken one are still checked
    items = [('css', 's.css', 'text/css', None),
             ('nav', 'nav.xhtml', 'application/xhtml+xml', 'nav'),
             ('extra', 'extra.xhtml', 'application/xhtml+xml', None),
             ('a', 'a.xhtml', 'application/xhtml+xml', None),
             ('old', 'old.xhtml', 'application/xhtml+xml', None)]
    content = {'OEBPS/nav.xhtml': RAW_NAV, 'OEBPS/a.xhtml': xhtml('<p>A tide.</p>'),
               'OEBPS/old.xhtml': XHTML10, 'OEBPS/extra.xhtml': xhtml('<p>Extra.</p>'),
               'OEBPS/s.css': 'p {}'}
    content.update(members or {})
    content = {k: v for k, v in content.items() if v is not None}
    path = os.path.join(directory, 'source.epub' if inside else '../outside.epub')
    write_raw_epub(path, items, ['nav', 'a', 'old'], content)
    with open(os.path.join(directory, 'project.json'), 'w') as f:
        json.dump({'epub_path': os.path.abspath(path), 'segments': []}, f)


class TestRoundtripCheck:

    def test_pass_counts_every_xhtml_item(self, tmp_path, capsys):
        _roundtrip_project(str(tmp_path / 'zeta'))
        _roundtrip_project(str(tmp_path / 'alpha'), inside=False)
        (tmp_path / 'not-a-project').mkdir()
        assert roundtrip_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 0
        assert capsys.readouterr().out.splitlines() == [
            'PASS project-1: chapters 4, round trip equal 4, whole-document equal 3',
            'PASS project-2: chapters 4, round trip equal 4, whole-document equal 3',
            'TOTAL projects 2  chapters 8  round trip equal 8  whole-document equal 6  '
            'failed projects 0',
        ]

    def test_names_without_anonymize(self, tmp_path, capsys):
        _roundtrip_project(str(tmp_path / 'alpha'))
        roundtrip_check.main(['--projects-dir', str(tmp_path)])
        assert capsys.readouterr().out.startswith('PASS alpha: chapters 4')

    @pytest.mark.parametrize('members', [
        {'OEBPS/extra.xhtml': '<html><body><p>open</body></html>'},
        {'OEBPS/extra.xhtml': None},
    ])
    def test_unparseable_or_missing_item_fails(self, tmp_path, capsys, members):
        _roundtrip_project(str(tmp_path / 'p'), members)
        assert roundtrip_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 1
        assert capsys.readouterr().out.splitlines() == [
            'FAIL project-1: chapters 4, round trip equal 3, whole-document equal 2; '
            '1 chapters missing, unparseable or changed',
            'TOTAL projects 1  chapters 4  round trip equal 3  whole-document equal 2  '
            'failed projects 1',
        ]

    def test_changed_serialization_fails(self, tmp_path, capsys, monkeypatch):
        _roundtrip_project(str(tmp_path / 'p'))
        real = roundtrip_check.canonical
        calls = []

        def canonical(data):
            calls.append(data)
            return real(data) + ('x' if len(calls) == 2 else '')
        monkeypatch.setattr(roundtrip_check, 'canonical', canonical)
        assert roundtrip_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 1
        assert 'round trip equal 3, whole-document equal 3; 1 chapters' in capsys.readouterr().out

    def test_unreadable_source_fails(self, tmp_path, capsys):
        directory = tmp_path / 'secret-name'
        directory.mkdir()
        (directory / 'project.json').write_text(json.dumps(
            {'epub_path': str(directory / 'gone.epub')}))
        assert roundtrip_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 1
        out = capsys.readouterr().out
        assert out.splitlines() == [
            'FAIL project-1: cannot read the source (FileNotFoundError)',
            'TOTAL projects 1  chapters 0  round trip equal 0  whole-document equal 0  '
            'failed projects 1']
        assert 'secret-name' not in out

    def test_changed_project_directory_fails(self, tmp_path, capsys, monkeypatch):
        directory = str(tmp_path / 'p')
        _roundtrip_project(directory)
        real = roundtrip_check.check_epub

        def check_epub(path):
            with open(os.path.join(directory, 'project.json'), 'a') as f:
                f.write(' ')
            return real(path)
        monkeypatch.setattr(roundtrip_check, 'check_epub', check_epub)
        assert roundtrip_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 1
        assert capsys.readouterr().out.startswith(
            'FAIL project-1: chapters 4, round trip equal 4, whole-document equal 3; '
            'files in the project directory changed\n')

    def test_snapshot_sees_nested_files(self, tmp_path):
        (tmp_path / 'log').mkdir()
        (tmp_path / 'log' / 'b.txt').write_text('12')
        (tmp_path / 'a.txt').write_text('1')
        snap = roundtrip_check.snapshot(str(tmp_path))
        assert [(name, size) for name, size, _ in snap] == [('a.txt', 1), ('log/b.txt', 2)]
        assert snap[0][2] == os.stat(tmp_path / 'a.txt').st_mtime_ns

    def test_read_only(self, tmp_path):
        directory = str(tmp_path / 'p')
        _roundtrip_project(directory)
        before = roundtrip_check.snapshot(directory)
        roundtrip_check.main(['--projects-dir', str(tmp_path)])
        assert roundtrip_check.snapshot(directory) == before

    def test_missing_directory(self, tmp_path, capsys):
        assert roundtrip_check.main(['--projects-dir', str(tmp_path / 'nope'),
                                     '--anonymize']) == 2
        assert capsys.readouterr().out == (
            'ERROR cannot list projects directory <projects-dir> (FileNotFoundError)\n')

    def test_runs_as_script_with_help(self, tmp_path):
        _roundtrip_project(str(tmp_path / 'alpha'))
        result = _run_script('phase2_roundtrip', ['--projects-dir', str(tmp_path)],
                             str(tmp_path))
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith('PASS alpha')
        result = _run_script('phase2_roundtrip', ['--help'], str(tmp_path))
        _assert_help(result.stdout,
                     'Phase 2 check: every chapter file parses as XML and re-serializes '
                     'unchanged.', 'Print project-N labels; omit names and paths')


def _migratable(root, name, unmatched=False):
    from tests.test_migrate import DEFAULT_SEGMENTS, _legacy_project, _segment
    segments = list(DEFAULT_SEGMENTS)
    if unmatched:
        segments[3] = _segment('c2', 2, 'Another sentence.', '另。', 'translated')
    return _legacy_project(root, segments=segments, name=name)


class TestMigrateCopiesCheck:

    PASS_LINE = ('PASS project-1: chapters 2/2, segments 4/4, merge groups 1 (2 segments), '
                 'count differs 0, translated chars 54 -> 54, '
                 'export places 2/2, web load ok')

    def _main(self, root, anonymize=True):
        return migrate_check.main(['--projects-dir', str(root)]
                                  + (['--anonymize'] if anonymize else []))

    def test_pass_and_fail_lines(self, tmp_path, capsys):
        root = tmp_path / 'projects'
        root.mkdir()
        good = _migratable(root, 'alpha')
        bad = _migratable(root, 'beta', unmatched=True)
        (root / 'not-a-project').mkdir()
        before = {d: roundtrip_check.snapshot(d) for d in (good, bad)}

        assert self._main(root) == 1

        assert capsys.readouterr().out.splitlines() == [
            self.PASS_LINE,
            'FAIL project-2: chapters 1/2, segments 2/4, merge groups 0 (0 segments), '
            'count differs 0, translated chars 52 -> 52, '
            'export places 0/2, web load ok; '
            '1 chapters unmatched, nothing written; 2 chapters not placed by the export',
            'TOTAL projects 2  chapters 3/4  segments 6/8  merge groups 1 (2 segments)  '
            'count differs 0  translated chars unchanged 2  '
            'export places 2/4  web loads 2  failed projects 1',
        ]
        # the originals were only read
        assert {d: roundtrip_check.snapshot(d) for d in (good, bad)} == before
        assert sorted(os.listdir(good)) == ['project.json', 'source.epub']

    def test_copies_removed_and_outside_repository(self, tmp_path, capsys, monkeypatch):
        root = tmp_path / 'projects'
        root.mkdir()
        _migratable(root, 'alpha')
        made = []
        real = migrate_check.tempfile.mkdtemp

        def mkdtemp(**kwargs):
            made.append(real(**kwargs))
            return made[-1]
        monkeypatch.setattr(migrate_check.tempfile, 'mkdtemp', mkdtemp)
        assert self._main(root) == 0
        assert len(made) == 1 and not os.path.exists(made[0])
        assert os.path.basename(made[0]).startswith('phase2-migrate-')
        assert not os.path.realpath(made[0]).startswith(ROOT)

    def test_refuses_temporary_directory_inside_repository(self, tmp_path, capsys,
                                                           monkeypatch):
        # Review round 1: checked before anything is created
        root = tmp_path / 'projects'
        root.mkdir()
        _migratable(root, 'alpha')
        before = sorted(os.listdir(ROOT))
        monkeypatch.setattr(migrate_check.tempfile, 'gettempdir',
                            lambda: os.path.join(ROOT, 'src'))
        assert self._main(root) == 2
        assert capsys.readouterr().out == (
            'ERROR the temporary directory would be inside the repository\n')
        assert sorted(os.listdir(ROOT)) == before
        assert not any(n.startswith('phase2-migrate-') for n in os.listdir(ROOT + '/src'))

    @pytest.mark.parametrize('where', ['root', 'project'])
    def test_refuses_temporary_directory_inside_projects_dir(self, tmp_path, capsys,
                                                             monkeypatch, where):
        root = tmp_path / 'projects'
        root.mkdir()
        directory = _migratable(root, 'alpha')
        before = roundtrip_check.snapshot(str(root))
        monkeypatch.setattr(migrate_check.tempfile, 'gettempdir',
                            lambda: str(root) if where == 'root' else directory)
        assert self._main(root) == 2
        assert capsys.readouterr().out == (
            'ERROR the temporary directory would be inside the projects directory\n')
        assert roundtrip_check.snapshot(str(root)) == before

    def test_glossary_copied_and_names_shown(self, tmp_path, capsys, monkeypatch):
        root = tmp_path / 'projects'
        root.mkdir()
        directory = _migratable(root, 'alpha')
        with open(os.path.join(directory, 'glossary.json'), 'w') as f:
            f.write('{}')
        seen = []
        real = migrate_check.check_copy

        def check_copy(copy_dir, client, name):
            seen.append(sorted(os.listdir(copy_dir)))
            return real(copy_dir, client, name)
        monkeypatch.setattr(migrate_check, 'check_copy', check_copy)
        assert self._main(root, anonymize=False) == 0
        assert seen == [['glossary.json', 'project.json', 'source.epub']]
        assert capsys.readouterr().out.startswith('PASS alpha: chapters 2/2')

    def test_changed_translation_fails(self, tmp_path, capsys, monkeypatch):
        root = tmp_path / 'projects'
        root.mkdir()
        _migratable(root, 'alpha')
        real = migrate_check.migrate_project

        def migrate(copy_dir):
            report = real(copy_dir)
            path = os.path.join(copy_dir, 'project.json')
            with open(path, encoding='utf-8') as f:
                raw = json.load(f)
            raw['segments'][0]['translated_text'] += '!'
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(raw, f)
            return report
        monkeypatch.setattr(migrate_check, 'migrate_project', migrate)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[0] == (self.PASS_LINE.replace('PASS', 'FAIL').replace('54 -> 54', '54 -> 55')
                          + '; translated characters changed')
        assert 'translated chars unchanged 0' in out[1]

    def test_unplaced_chapter_fails(self, tmp_path, capsys, monkeypatch):
        root = tmp_path / 'projects'
        root.mkdir()
        _migratable(root, 'alpha')
        monkeypatch.setattr(migrate_check, '_matches', lambda blocks, segments: False)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[0] == (self.PASS_LINE.replace('PASS', 'FAIL')
                          .replace('export places 2/2', 'export places 0/2')
                          + '; 2 chapters not placed by the export')
        assert 'export places 0/2' in out[1]

    def test_chapter_missing_from_book_not_placed(self, tmp_path, monkeypatch):
        root = tmp_path / 'projects'
        root.mkdir()
        directory = _migratable(root, 'alpha')
        migrate_check.migrate_project(directory)
        real = migrate_check.read_package

        def read_package(zf):
            opf, manifest, spine = real(zf)
            return opf, manifest, [item for item in spine if item.id != 'c1']
        monkeypatch.setattr(migrate_check, 'read_package', read_package)
        assert migrate_check.placed_chapters(directory) == (2, 1)

    def test_placed_chapters_source_resolved(self, tmp_path):
        """placed_chapters reads the source as the exporter does
        (source_epub_path): the copy, else the stored path."""
        import shutil
        root = tmp_path / 'projects'
        root.mkdir()
        directory = _migratable(root, 'alpha')
        migrate_check.migrate_project(directory)
        own = os.path.join(directory, 'source.epub')
        elsewhere = str(tmp_path / 'elsewhere.epub')
        shutil.move(own, elsewhere)
        pm = migrate_check.ProjectManager(directory)
        pm.load()
        pm.data.epub_path = elsewhere
        pm.save()
        assert migrate_check.placed_chapters(directory) == (2, 2)
        moved = str(tmp_path / 'moved')
        shutil.move(elsewhere, own)
        os.rename(directory, moved)  # the stored path is stale now
        assert migrate_check.placed_chapters(moved) == (2, 2)

    @pytest.mark.parametrize('change', ['nav', 'media_type', 'entity', 'not_well_formed'])
    def test_chapters_the_exporter_refuses_not_placed(self, tmp_path, monkeypatch, change):
        # review round 1: the exporter's own chapter filter and loader
        import dataclasses
        from src.epub import exporter
        root = tmp_path / 'projects'
        root.mkdir()
        directory = _migratable(root, 'alpha')
        migrate_check.migrate_project(directory)
        real_package, real_read = migrate_check.read_package, exporter.read_member
        paths = {}

        def read_package(zf):
            opf, manifest, spine = real_package(zf)
            paths.update({item.id: item.path for item in spine})
            if change == 'nav':
                spine = [dataclasses.replace(item, is_nav=item.id == 'c1') for item in spine]
            elif change == 'media_type':
                spine = [dataclasses.replace(item, media_type='text/html') if item.id == 'c1'
                         else item for item in spine]
            return opf, manifest, spine

        def read_member(zf, name):
            data = real_read(zf, name)
            if name == paths.get('c1'):
                return (data.replace(b'</p>', b'&mdash;</p>', 1) if change == 'entity'
                        else data.replace(b'</body>', b'', 1))
            return data
        monkeypatch.setattr(migrate_check, 'read_package', read_package)
        if change in ('entity', 'not_well_formed'):
            monkeypatch.setattr(exporter, 'read_member', read_member)
        assert migrate_check.placed_chapters(directory) == (2, 1)

    def test_web_load_checked(self, tmp_path, capsys, monkeypatch):
        root = tmp_path / 'projects'
        root.mkdir()
        _migratable(root, 'alpha')
        monkeypatch.setattr(migrate_check, 'web_loads', lambda client, name, segments: False)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[0] == (self.PASS_LINE.replace('PASS', 'FAIL').replace('web load ok',
                                                                         'web load failed')
                          + '; does not load in the web app')
        assert 'web loads 0' in out[1]

    @pytest.mark.parametrize('case', ['ok', 'count', 'missing', 'review', 'home'])
    def test_web_loads(self, tmp_path, case, monkeypatch):
        root = tmp_path / 'projects'
        root.mkdir()
        directory = _migratable(root, 'alpha')
        migrate_check.migrate_project(directory)
        app = migrate_check.create_app(projects_dir=str(root))
        app.config['TESTING'] = True
        client = app.test_client()
        name, count = 'alpha', 3
        if case == 'count':
            count = 4
        elif case == 'missing':
            name = 'nope'
        elif case in ('review', 'home'):
            real = client.get
            path = '/project/alpha/review' if case == 'review' else '/'

            def get(url, *a, **k):
                response = real(url, *a, **k)
                if url == path:
                    response.status_code = 500
                return response
            monkeypatch.setattr(client, 'get', get)
        assert migrate_check.web_loads(client, name, count) is (case == 'ok')

    def test_exception_is_a_fail(self, tmp_path, capsys, monkeypatch):
        root = tmp_path / 'projects'
        root.mkdir()
        _migratable(root, 'alpha')
        _migratable(root, 'beta')

        def boom(copy_dir):
            if copy_dir.endswith('alpha'):
                raise RuntimeError('secret-name')
            return real(copy_dir)
        real = migrate_check.migrate_project
        monkeypatch.setattr(migrate_check, 'migrate_project', boom)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[0] == 'FAIL project-1: migration check raised RuntimeError'
        assert out[1].startswith('PASS project-2')
        assert out[2].startswith('TOTAL projects 2  chapters 2/2  segments 4/4')
        assert out[2].endswith('failed projects 1')

    def test_changed_project_directory_fails(self, tmp_path, capsys, monkeypatch):
        root = tmp_path / 'projects'
        root.mkdir()
        directory = _migratable(root, 'alpha')
        real = migrate_check.check_copy

        def check_copy(copy_dir, client, name):
            with open(os.path.join(directory, 'project.json'), 'a') as f:
                f.write(' ')
            return real(copy_dir, client, name)
        monkeypatch.setattr(migrate_check, 'check_copy', check_copy)
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[0] == (
            self.PASS_LINE.replace('PASS', 'FAIL') + '; files in the project directory changed')

    def test_missing_directory(self, tmp_path, capsys):
        assert self._main(tmp_path / 'nope') == 2
        assert capsys.readouterr().out == (
            'ERROR cannot list projects directory <projects-dir> (FileNotFoundError)\n')

    def test_runs_as_script_with_help(self, tmp_path):
        root = tmp_path / 'projects'
        root.mkdir()
        _migratable(root, 'alpha')
        result = _run_script('phase2_migrate_copies', ['--projects-dir', str(root)],
                             str(tmp_path))
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith('PASS alpha')
        result = _run_script('phase2_migrate_copies', ['--help'], str(tmp_path))
        _assert_help(result.stdout,
                     'Phase 2 check: the migration succeeds on copies of the existing '
                     'projects.', 'Print project-N labels; omit names and paths')



class TestPhase2ScriptSurvivors:
    """Mutation survivors of the Phase 2 check scripts."""

    def test_canonical_keeps_comments(self):
        assert roundtrip_check.canonical(b'<a><!--c--><b/></a>') == '<a><!--c--><b></b></a>'

    def test_own_copy_preferred(self, tmp_path, capsys):
        directory = str(tmp_path / 'p')
        _roundtrip_project(directory)
        with open(os.path.join(directory, 'project.json'), 'w') as f:
            json.dump({'epub_path': '/moved/away.epub'}, f)
        assert roundtrip_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 0

    @pytest.mark.parametrize('check', ['roundtrip', 'migrate'])
    def test_default_projects_dir(self, tmp_path, capsys, monkeypatch, check):
        monkeypatch.chdir(tmp_path)
        (tmp_path / 'projects').mkdir()
        if check == 'roundtrip':
            _roundtrip_project(str(tmp_path / 'projects' / 'alpha'))
            assert roundtrip_check.main([]) == 0
        else:
            _migratable(tmp_path / 'projects', 'alpha')
            assert migrate_check.main([]) == 0
        assert capsys.readouterr().out.startswith('PASS alpha')

    def test_roundtrip_counts_every_failure(self, tmp_path, capsys):
        for name in ('aaa', 'aab'):
            directory = tmp_path / name
            directory.mkdir()
            (directory / 'project.json').write_text(json.dumps({'epub_path': '/gone.epub'}))
        _roundtrip_project(str(tmp_path / 'bbb'), {'OEBPS/extra.xhtml': None})
        _roundtrip_project(str(tmp_path / 'ccc'), {'OEBPS/extra.xhtml': None})
        _roundtrip_project(str(tmp_path / 'ddd'))
        assert roundtrip_check.main(['--projects-dir', str(tmp_path), '--anonymize']) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[0] == 'FAIL project-1: cannot read the source (FileNotFoundError)'
        assert out[4].startswith('PASS project-5')
        assert out[5].endswith('failed projects 4')

    def test_roundtrip_two_problems_joined(self, tmp_path, capsys, monkeypatch):
        directory = str(tmp_path / 'p')
        _roundtrip_project(directory, {'OEBPS/extra.xhtml': None})
        real = roundtrip_check.check_epub

        def check_epub(path):
            with open(os.path.join(directory, 'project.json'), 'a') as f:
                f.write(' ')
            return real(path)
        monkeypatch.setattr(roundtrip_check, 'check_epub', check_epub)
        roundtrip_check.main(['--projects-dir', str(tmp_path), '--anonymize'])
        assert capsys.readouterr().out.splitlines()[0].endswith(
            '; 1 chapters missing, unparseable or changed; files in the project directory changed')

    def test_migrate_counts_every_failure(self, tmp_path, capsys, monkeypatch):
        root = tmp_path / 'projects'
        root.mkdir()
        for name in ('a', 'b', 'c', 'd'):
            _migratable(root, name, unmatched=name in ('c', 'd'))
        real = migrate_check.migrate_project

        def migrate(copy_dir):
            if os.path.basename(copy_dir) in ('a', 'b'):
                raise RuntimeError('boom')
            return real(copy_dir)
        monkeypatch.setattr(migrate_check, 'migrate_project', migrate)
        assert migrate_check.main(['--projects-dir', str(root), '--anonymize']) == 1
        assert capsys.readouterr().out.splitlines()[-1].endswith('failed projects 4')

    def test_partly_placed_count(self, tmp_path, capsys, monkeypatch):
        root = tmp_path / 'projects'
        root.mkdir()
        _migratable(root, 'alpha')
        real = migrate_check._matches

        def matches(blocks, segments):
            return False if segments[0].chapter_id == 'c1' else real(blocks, segments)
        monkeypatch.setattr(migrate_check, '_matches', matches)
        assert migrate_check.main(['--projects-dir', str(root), '--anonymize']) == 1
        assert capsys.readouterr().out.splitlines()[0].endswith(
            'export places 1/2, web load ok; 1 chapters not placed by the export')


# --- Phase 3: export check ------------------------------------------------------

export_check = _load('phase3_export_check')


def _export_projects(root, schema_1=True):
    """A projects directory with a schema 1 project (migrated by the check)
    and a schema 2 project of the raw fixture."""
    from tests.test_exporter import RAW_TRANSLATIONS, _project, _raw_book
    from tests.test_migrate import _legacy_project
    root.mkdir()
    if schema_1:
        _legacy_project(root, name='alpha')
    book_dir = root.parent / 'books'
    book_dir.mkdir()
    _project(root, _raw_book(book_dir), RAW_TRANSLATIONS, name='beta')
    return root


def _tamper(monkeypatch, change, formats=('chinese-only',)):
    """Make every export of the given formats pass through change(members)."""
    import zipfile
    real = export_check.export_project

    def export(project_dir, output, fmt):
        report = real(project_dir, output, fmt)
        if fmt in formats:
            with zipfile.ZipFile(output) as z:
                members = [(i, z.read(i.filename)) for i in z.infolist()]
            members = change(members)
            with zipfile.ZipFile(output, 'w') as z:
                for info, data in members:
                    z.writestr(info, data)
        return report
    monkeypatch.setattr(export_check, 'export_project', export)


def _edit(name, old, new):
    def change(members):
        return [(i, d.replace(old, new) if i.filename == name else d) for i, d in members]
    return change


class _ExportCheckBase:

    @pytest.fixture(autouse=True)
    def no_epubcheck(self, monkeypatch):
        real = export_check.shutil.which
        monkeypatch.setattr(export_check.shutil, 'which',
                            lambda name: None if name == 'epubcheck' else real(name))

    def _main(self, root, anonymize=True):
        return export_check.main(['--projects-dir', str(root)]
                                 + (['--anonymize'] if anonymize else []))

    def _fails_with(self, tmp_path, capsys, monkeypatch, change, problem,
                    formats=('chinese-only',)):
        root = _export_projects(tmp_path / 'projects', schema_1=False)
        _tamper(monkeypatch, change, formats)
        assert self._main(root) == 1
        line = capsys.readouterr().out.splitlines()[1]
        assert line.startswith('FAIL project-1: ')
        assert line.split('; ', 1)[1] == problem


class TestExportCheck(_ExportCheckBase):

    PASS = [
        'epubcheck not available (not on PATH): not run',
        'PASS project-1: exports 3, translated segments 3: as translation 3, kept original 0, '
        'toc labels changed 0/0 (0 into translated chapters)',
        'PASS project-2: exports 3, translated segments 4: as translation 4, kept original 0, '
        'toc labels changed 2/2 (2 into translated chapters)',
        'TOTAL projects 2  exports 6  translated segments 7  as translation 7  kept original 0  '
        'toc labels changed 2/2 (2 into translated chapters)  epubcheck not run  failed projects 0',
    ]

    def test_pass_lines_and_originals_untouched(self, tmp_path, capsys):
        root = _export_projects(tmp_path / 'projects')
        (root / 'not-a-project').mkdir()
        before = roundtrip_check.snapshot(str(root))
        assert self._main(root) == 0
        assert capsys.readouterr().out.splitlines() == self.PASS
        assert roundtrip_check.snapshot(str(root)) == before

    def test_names_without_anonymize(self, tmp_path, capsys):
        root = _export_projects(tmp_path / 'projects')
        assert self._main(root, anonymize=False) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[1].startswith('PASS alpha: ') and out[2].startswith('PASS beta: ')

    def test_kept_segments_counted_per_reason(self, tmp_path, capsys):
        from tests.test_exporter import RAW_TRANSLATIONS, _project, _raw_book
        root = tmp_path / 'projects'
        root.mkdir()
        (tmp_path / 'b').mkdir()
        _project(root, _raw_book(tmp_path / 'b'),
                 dict(RAW_TRANSLATIONS, two_seg0001='<h2>晚', two_seg0002='字'), name='p')
        assert self._main(root) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[1] == ('PASS project-1: exports 3, translated segments 4: as translation 2, '
                          'kept original 2 (not_well_formed 1, structure_mismatch 1), '
                          'toc labels changed 2/2 (2 into translated chapters)')
        assert out[2] == ('TOTAL projects 1  exports 3  translated segments 4  '
                          'as translation 2  kept original 2 (not_well_formed 1, '
                          'structure_mismatch 1)  toc labels changed 2/2 (2 into translated chapters)  epubcheck not run  '
                          'failed projects 0')

    def test_work_directory_removed_and_outside_repository(self, tmp_path, capsys, monkeypatch):
        root = _export_projects(tmp_path / 'projects', schema_1=False)
        made = []
        real = export_check.tempfile.mkdtemp

        def mkdtemp(**kwargs):
            made.append(real(**kwargs))
            return made[-1]
        monkeypatch.setattr(export_check.tempfile, 'mkdtemp', mkdtemp)
        outputs = []
        real_export = export_check.export_project

        def export(project_dir, output, fmt):
            outputs.append(output)
            return real_export(project_dir, output, fmt)
        monkeypatch.setattr(export_check, 'export_project', export)
        assert self._main(root) == 0
        assert len(made) == 1 and not os.path.exists(made[0])
        assert os.path.basename(made[0]).startswith('phase3-export-')
        assert not os.path.realpath(made[0]).startswith(ROOT)
        assert len(outputs) == 3 and all(o.startswith(made[0] + os.sep) for o in outputs)

    @pytest.mark.parametrize('where', ['repository', 'projects'])
    def test_refuses_temporary_directory_inside(self, tmp_path, capsys, monkeypatch, where):
        root = _export_projects(tmp_path / 'projects', schema_1=False)
        before = roundtrip_check.snapshot(str(root))
        place = os.path.join(ROOT, 'src') if where == 'repository' else str(root / 'beta')
        monkeypatch.setattr(export_check.tempfile, 'gettempdir', lambda: place)
        assert self._main(root) == 2
        what = 'the repository' if where == 'repository' else 'the projects directory'
        assert capsys.readouterr().out == f'ERROR the temporary directory would be inside {what}\n'
        assert roundtrip_check.snapshot(str(root)) == before
        assert not any(n.startswith('phase3-export-') for n in os.listdir(place))

    def test_missing_directory(self, tmp_path, capsys):
        assert self._main(tmp_path / 'nope') == 2
        assert capsys.readouterr().out == (
            'ERROR cannot list projects directory <projects-dir> (FileNotFoundError)\n')

    def test_exception_is_a_fail(self, tmp_path, capsys, monkeypatch):
        root = _export_projects(tmp_path / 'projects')

        def boom(*a):
            raise RuntimeError('x')
        monkeypatch.setattr(export_check, 'export_project', boom)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[1:3] == ['FAIL project-1: export check raised RuntimeError',
                            'FAIL project-2: export check raised RuntimeError']
        assert out[3].endswith('failed projects 2')

    def test_unmatched_migration_is_a_fail(self, tmp_path, capsys):
        from tests.test_migrate import DEFAULT_SEGMENTS, _legacy_project, _segment
        root = tmp_path / 'projects'
        root.mkdir()
        segments = list(DEFAULT_SEGMENTS)
        segments[3] = _segment('c2', 2, 'Another sentence.', '另。', 'translated')
        _legacy_project(root, segments=segments, name='alpha')
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[1] == (
            'FAIL project-1: 1 chapters unmatched by the migration')

    def test_changed_project_directory_fails(self, tmp_path, capsys, monkeypatch):
        root = _export_projects(tmp_path / 'projects', schema_1=False)
        real = export_check.check_copy

        def check_copy(copy_dir, out_dir):
            result = real(copy_dir, out_dir)
            with open(root / 'beta' / 'extra.txt', 'w') as f:
                f.write('x')
            return result
        monkeypatch.setattr(export_check, 'check_copy', check_copy)
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[1].endswith(
            '; files in the project directory changed')

    def test_mimetype_not_first(self, tmp_path, capsys, monkeypatch):
        self._fails_with(tmp_path, capsys, monkeypatch, lambda m: m[1:] + m[:1],
                         'chinese-only: mimetype not first and stored; '
                         'chinese-only: member order differs')

    def test_mimetype_compressed(self, tmp_path, capsys, monkeypatch):
        import zipfile

        def change(members):
            info = zipfile.ZipInfo('mimetype')
            info.compress_type = zipfile.ZIP_DEFLATED
            return [(info, members[0][1])] + members[1:]
        self._fails_with(tmp_path, capsys, monkeypatch, change,
                         'chinese-only: mimetype not first and stored; '
                         'chinese-only: 1 untouched members stored differently')

    @pytest.mark.filterwarnings('ignore:Duplicate name')
    def test_member_twice(self, tmp_path, capsys, monkeypatch):
        import zipfile

        def change(members):
            info, data = members[-1]
            return members + [(zipfile.ZipInfo(info.filename), data)]
        self._fails_with(tmp_path, capsys, monkeypatch, change,
                         'chinese-only: a member is written twice; '
                         'chinese-only: member order differs')

    def test_member_added(self, tmp_path, capsys, monkeypatch):
        import zipfile
        self._fails_with(tmp_path, capsys, monkeypatch,
                         lambda m: m + [(zipfile.ZipInfo('OEBPS/x.txt'), b'x')],
                         'chinese-only: 1 members added')

    def test_member_outside_the_allowed_set_differs(self, tmp_path, capsys, monkeypatch):
        self._fails_with(tmp_path, capsys, monkeypatch,
                         _edit('OEBPS/Styles/book.css', b'0', b'1'),
                         'chinese-only: 1 members outside the allowed set differ')

    @pytest.mark.parametrize('name', ['OEBPS/Styles/book.css', 'OEBPS/Text/one.xhtml'])
    def test_source_member_missing(self, tmp_path, capsys, monkeypatch, name):
        # review round 1: also a member the exporter may change
        def change(members):
            return [(i, d) for i, d in members if i.filename != name]
        self._fails_with(tmp_path, capsys, monkeypatch, change,
                         'chinese-only: 1 source members missing')

    def test_chapter_not_well_formed(self, tmp_path, capsys, monkeypatch):
        self._fails_with(tmp_path, capsys, monkeypatch,
                         _edit('OEBPS/Text/one.xhtml', b'</body>', b''),
                         'chinese-only: 1 chapters: not well-formed')

    @pytest.mark.parametrize('old, new', [
        (b'<style>p.first { text-indent: 0; }</style>', b''),
        (b'href="../Styles/book.css"', b'href="../Styles/other.css"'),
        (b'</head>', b'<style>p {}</style></head>'),
    ])
    def test_head_changed(self, tmp_path, capsys, monkeypatch, old, new):
        self._fails_with(tmp_path, capsys, monkeypatch, _edit('OEBPS/Text/one.xhtml', old, new),
                         'chinese-only: 1 chapters: head links or styles differ')

    def test_added_link_must_be_the_bilingual_stylesheet(self, tmp_path, capsys, monkeypatch):
        self._fails_with(
            tmp_path, capsys, monkeypatch,
            _edit('OEBPS/Text/one.xhtml', b'href="../bilingual.css"', b'href="../Styles/book.css"'),
            'bilingual-vertical: 1 chapters: bilingual chapter without its stylesheet; '
            'bilingual-vertical: 1 chapters: head links or styles differ',
            formats=('bilingual-vertical',))

    def test_duplicate_id(self, tmp_path, capsys, monkeypatch):
        self._fails_with(tmp_path, capsys, monkeypatch,
                         _edit('OEBPS/Text/one.xhtml', b'<p class="first">',
                               b'<p class="first" id="c1">'),
                         'chinese-only: 1 chapters: duplicate id')

    def test_svg_attribute_case(self, tmp_path, capsys, monkeypatch):
        self._fails_with(tmp_path, capsys, monkeypatch,
                         _edit('OEBPS/Text/one.xhtml', b'viewBox', b'viewbox'),
                         'chinese-only: 1 chapters: SVG attribute case changed')

    @pytest.mark.parametrize('old, new', [
        (b'href="../bilingual.css"', b'href="../missing.css"'),
        (b'href="../bilingual.css"', b'href="../Styles/book.css"'),
        (b'rel="stylesheet" type="text/css" href="../bilingual.css"',
         b'rel="alternate" type="text/css" href="../bilingual.css"'),
    ])
    def test_bilingual_chapter_without_its_stylesheet(self, tmp_path, capsys, monkeypatch,
                                                      old, new):
        root = _export_projects(tmp_path / 'projects', schema_1=False)
        _tamper(monkeypatch, _edit('OEBPS/Text/one.xhtml', old, new),
                ('bilingual-horizontal',))
        assert self._main(root) == 1
        line = capsys.readouterr().out.splitlines()[1]
        assert 'bilingual-horizontal: 1 chapters: bilingual chapter without its stylesheet' in line

    def test_stylesheet_not_in_the_manifest(self, tmp_path, capsys, monkeypatch):
        self._fails_with(
            tmp_path, capsys, monkeypatch,
            _edit('OEBPS/content.opf', b'<item id="bilingual-css" href="bilingual.css" '
                                       b'media-type="text/css"/>', b''),
            'bilingual-horizontal: 1 members added; bilingual-horizontal: 2 chapters: '
            'bilingual chapter without its stylesheet; bilingual-horizontal: 2 chapters: '
            'head links or styles differ', formats=('bilingual-horizontal',))

    def test_broken_fragment_link(self, tmp_path, capsys, monkeypatch):
        self._fails_with(tmp_path, capsys, monkeypatch,
                         _edit('OEBPS/toc.ncx', b'src="Text/one.xhtml"',
                               b'src="Text/one.xhtml#nowhere"'),
                         'chinese-only: 1 fragment links do not resolve')

    def test_links_broken_in_the_source_too_not_reported(self, tmp_path, capsys):
        from tests.test_exporter import _project, _raw_book
        from tests.conftest import xhtml
        root = tmp_path / 'projects'
        root.mkdir()
        (tmp_path / 'b').mkdir()
        chapter = xhtml('<p id="a">See <a href="#gone">here</a> and <a href="#a">there</a>, '
                        '<a href="c.xhtml#a">self</a>, <a href="http://x.invalid/#a">web</a>.'
                        '</p><p><a href="missing.xhtml#a">far</a> <a href="#">top</a></p>')
        epub = _raw_book(tmp_path / 'b', {'c': ('c.xhtml', chapter)}, nav=None, ncx=None)
        _project(root, epub, {'c_seg0001': '<p>見<a href="#gone">此</a>與<a href="#a">彼</a>、'
                              '<a href="c.xhtml#a">自</a>、<a href="http://x.invalid/#a">網</a>。'
                              '</p>\n<p><a href="missing.xhtml#a">遠</a><a href="#">頂</a></p>'},
                 name='p')
        assert self._main(root) == 0

    def test_counts_differ_between_formats(self, tmp_path, capsys, monkeypatch):
        root = _export_projects(tmp_path / 'projects', schema_1=False)
        real = export_check.export_project

        def export(project_dir, output, fmt):
            report = real(project_dir, output, fmt)
            if fmt == 'bilingual-vertical':
                report.chapters[0].translated += 1
            return report
        monkeypatch.setattr(export_check, 'export_project', export)
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[1].endswith(
            '; bilingual-vertical: segment counts differ from chinese-only')

    def test_runs_as_script_with_help(self, tmp_path):
        result = _run_script('phase3_export_check', ['--help'], str(tmp_path))
        assert result.returncode == 0
        _assert_help(result.stdout,
                     'Phase 3 check: exports of copies of the existing projects keep the '
                     'source intact.', 'Print project-N labels; omit names and paths')


class TestExportCheckEpubcheck:
    """epubcheck runs are replaced by canned JSON reports."""

    def _fake(self, monkeypatch, by_kind):
        """by_kind: 'source' or a format -> list of (ID, severity, locations)."""
        seen = []

        def run(cmd, **kwargs):
            path, report = cmd[1], cmd[3]
            kind = os.path.basename(path)[:-5]
            kind = kind if kind in export_check.FORMATS else 'source'
            seen.append(kind)
            messages = [{'ID': i, 'severity': sev, 'message': 'quoted book text',
                         'locations': [{}] * n} for i, sev, n in by_kind.get(kind, [])]
            with open(report, 'w') as f:
                json.dump({'messages': messages}, f)
        monkeypatch.setattr(export_check.shutil, 'which',
                            lambda name: '/fake/epubcheck' if name == 'epubcheck' else None)
        monkeypatch.setattr(export_check.subprocess, 'run', run)
        return seen

    def test_counts_and_new_errors(self, tmp_path, capsys, monkeypatch):
        root = _export_projects(tmp_path / 'projects', schema_1=False)
        seen = self._fake(monkeypatch, {
            'source': [('RSC-005', 'ERROR', 2), ('OPF-1', 'WARNING', 9)],
            'chinese-only': [('RSC-005', 'ERROR', 3), ('X-1', 'USAGE', 3)],
            'bilingual-horizontal': [('RSC-005', 'ERROR', 5), ('RSC-012', 'FATAL', 0)],
            'bilingual-vertical': [('RSC-005', 'ERROR', 4)]})
        assert export_check.main(['--projects-dir', str(root), '--anonymize']) == 1
        out = capsys.readouterr().out
        assert out.splitlines() == [
            'epubcheck found; errors compared by message ID '
            '(chinese-only, bilingual-horizontal, bilingual-vertical)',
            'FAIL project-1: exports 3, translated segments 4: as translation 4, '
            'kept original 0, toc labels changed 2/2 (2 into translated chapters), epubcheck errors source 2 (RSC-005:2), '
            'exports 3/6/4; chinese-only: epubcheck new errors RSC-005 1; '
            'bilingual-horizontal: epubcheck new errors RSC-005 1, RSC-012 1',
            'TOTAL projects 1  exports 3  translated segments 4  as translation 4  '
            'kept original 0  toc labels changed 2/2 (2 into translated chapters)  exports with new epubcheck errors 2  '
            'failed projects 1']
        assert 'quoted book text' not in out
        assert seen == ['source', 'chinese-only', 'bilingual-horizontal', 'bilingual-vertical']

    def test_no_errors(self, tmp_path, capsys, monkeypatch):
        root = _export_projects(tmp_path / 'projects', schema_1=False)
        self._fake(monkeypatch, {})
        assert export_check.main(['--projects-dir', str(root), '--anonymize']) == 0
        assert capsys.readouterr().out.splitlines()[1].endswith(
            'epubcheck errors source 0, exports 0/0/0')

    def test_report_file_removed_and_not_next_to_the_book(self, tmp_path, monkeypatch):
        reports = []

        def run(cmd, **kwargs):
            reports.append(cmd[3])
            with open(cmd[3], 'w') as f:
                json.dump({'messages': []}, f)
        monkeypatch.setattr(export_check.shutil, 'which', lambda name: '/fake/epubcheck')
        monkeypatch.setattr(export_check.subprocess, 'run', run)
        book = tmp_path / 'b' / 'book.epub'
        assert export_check.epubcheck_errors(str(book)) == {}
        assert not os.path.exists(reports[0])
        assert os.path.dirname(reports[0]) != str(book.parent)

    def test_new_errors(self):
        from collections import Counter
        assert export_check.new_errors(Counter(a=2, b=1), Counter(a=3, b=1, c=2), 1) == {
            'a': 1, 'c': 2}
        # bilingual: up to twice the source's count, no ID the source lacks
        assert export_check.new_errors(Counter(a=2, b=1), Counter(a=4, b=3, c=1), 2) == {
            'b': 1, 'c': 1}


def _edit_all(edits):
    """edits: member -> (old, new)."""
    def change(members):
        return [(i, d.replace(*edits[i.filename]) if i.filename in edits else d)
                for i, d in members]
    return change


class TestExportCheckSurvivors(_ExportCheckBase):
    """Mutation testing, run 1: the check script's rules spelled out."""

    def test_default_projects_dir(self, tmp_path, capsys, monkeypatch):
        _export_projects(tmp_path / 'projects')
        monkeypatch.chdir(tmp_path)
        assert export_check.main([]) == 0
        assert capsys.readouterr().out.splitlines()[1].startswith('PASS alpha: ')

    def test_project_files_copied(self, tmp_path, capsys, monkeypatch):
        from tests.test_migrate import _legacy_project
        root = tmp_path / 'projects'
        root.mkdir()
        directory = _legacy_project(root, name='alpha')
        with open(os.path.join(directory, 'glossary.json'), 'w') as f:
            f.write('{}')
        with open(os.path.join(directory, 'other.txt'), 'w') as f:
            f.write('x')
        seen = []
        real = export_check.check_copy

        def check_copy(copy_dir, out_dir):
            seen.append(sorted(os.listdir(copy_dir)))
            return real(copy_dir, out_dir)
        monkeypatch.setattr(export_check, 'check_copy', check_copy)
        assert self._main(root) == 0
        assert seen == [['glossary.json', 'project.json', 'source.epub']]

    def test_new_valid_links_and_ids_pass(self, tmp_path, capsys, monkeypatch):
        # links that resolve (same file, other path, an a@name anchor), an
        # external and a network-path link, a new distinct id; one link to a
        # missing member is the only failure
        added = (b'<p id="extra"><a href="#c1">a</a><a href="one.xhtml#c1">b</a>'
                 b'<a name="nm"/><a href="#nm">c</a><a href="urn:x#y">d</a>'
                 b'<a href="//h/x#y">e</a><a href="gone.xhtml#z">f</a></p></body>')
        self._fails_with(tmp_path, capsys, monkeypatch,
                         _edit('OEBPS/Text/one.xhtml', b'</body>', added),
                         'chinese-only: 1 fragment links do not resolve')

    def test_link_checked_after_an_unparsed_member(self, tmp_path, capsys, monkeypatch):
        # members are checked in name order: chapter two comes before one
        edits = {'OEBPS/Text/chapter two.xhtml': (b'</body>', b''),
                 'OEBPS/Text/one.xhtml': (b'</body>', b'<p><a href="#zz">x</a></p></body>')}
        self._fails_with(tmp_path, capsys, monkeypatch, _edit_all(edits),
                         'chinese-only: 1 chapters: not well-formed; '
                         'chinese-only: 1 fragment links do not resolve')

    @pytest.mark.parametrize('old, new, problem', [
        (b'</body>', b'', 'not well-formed'),
        (b'<h2>', b'<h2 id="d"/><h2 id="d">', 'duplicate id'),
        (b'</body>', b'<svg xmlns="http://www.w3.org/2000/svg" viewbox="0 0 1 1"/></body>',
         'SVG attribute case changed'),
    ])
    def test_two_chapters_counted(self, tmp_path, capsys, monkeypatch, old, new, problem):
        edits = {'OEBPS/Text/chapter two.xhtml': (old, new)}
        if problem == 'duplicate id':
            edits['OEBPS/Text/one.xhtml'] = (b'<p class="first">', b'<p class="first" id="c1">')
        elif problem == 'SVG attribute case changed':
            edits['OEBPS/Text/one.xhtml'] = (b'viewBox', b'viewbox')
        else:
            edits['OEBPS/Text/one.xhtml'] = (old, new)
        self._fails_with(tmp_path, capsys, monkeypatch, _edit_all(edits),
                         f'chinese-only: 2 chapters: {problem}')

    def test_toc_labels(self, tmp_path):
        from tests.conftest import write_raw_epub, xhtml
        # review round 1: only the toc nav and the navMap count; an external
        # href has no target
        nav = xhtml('<nav epub:type="toc"><ol><li><a href="c.xhtml"> <span>T</span>wo \n words</a></li>'
                    '<li><a>Three</a></li><li><a href="http://example.com/x">W</a></li></ol></nav>'
                    '<nav epub:type="landmarks"><ol><li><a href="c.xhtml">L</a></li></ol></nav>'
                    '<p><a href="c.xhtml">x</a></p>')
        ncx = ('<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">'
               '<docTitle><text>Title</text></docTitle>'
               '<navMap><navPoint><navLabel><text>One</text></navLabel></navPoint></navMap>'
               '<pageList><pageTarget><navLabel><text>1</text></navLabel>'
               '<content src="c.xhtml"/></pageTarget></pageList></ncx>')
        epub = write_raw_epub(
            tmp_path / 'b.epub',
            [('css', 'a.css', 'text/css', None), ('nav', 'nav.xhtml', 'application/xhtml+xml', 'nav'),
             ('ncx', 'toc.ncx', 'application/x-dtbncx+xml', None),
             ('gone', 'gone.ncx', 'application/x-dtbncx+xml', None)], ['nav'],
            {'OEBPS/a.css': 'p {}', 'OEBPS/nav.xhtml': nav, 'OEBPS/toc.ncx': ncx})
        archive = export_check.Archive(epub)
        assert export_check.toc_labels(archive) == [
            ('OEBPS/nav.xhtml', 0, 'Two words', 'OEBPS/c.xhtml'),
            ('OEBPS/nav.xhtml', 1, 'Three', None), ('OEBPS/nav.xhtml', 2, 'W', None),
            ('OEBPS/toc.ncx', 0, 'One', None)]
        assert export_check.other_labels(archive) == [
            ('OEBPS/nav.xhtml', 'L'), ('OEBPS/nav.xhtml', 'x'),
            ('OEBPS/toc.ncx', 'Title'), ('OEBPS/toc.ncx', '1')]

    def test_bilingual_stylesheet_read_after_a_non_utf8_one(self, tmp_path, capsys):
        from tests.test_exporter import RAW_TRANSLATIONS, _project, _raw_book
        root = tmp_path / 'projects'
        root.mkdir()
        (tmp_path / 'b').mkdir()
        epub = _raw_book(tmp_path / 'b', extra={'OEBPS/Styles/book.css': (None, b'p {} /* \xff */')})
        _project(root, epub, RAW_TRANSLATIONS, name='p')
        assert self._main(root) == 0

    def test_two_unmatched_projects_and_a_changed_directory(self, tmp_path, capsys, monkeypatch):
        from tests.test_migrate import DEFAULT_SEGMENTS, _legacy_project, _segment
        root = tmp_path / 'projects'
        root.mkdir()
        segments = list(DEFAULT_SEGMENTS)
        segments[3] = _segment('c2', 2, 'Another sentence.', '另。', 'translated')
        for name in ('alpha', 'beta'):
            _legacy_project(root, segments=segments, name=name)
        real = export_check.check_copy

        def check_copy(copy_dir, out_dir):
            result = real(copy_dir, out_dir)
            with open(root / 'alpha' / 'extra.txt', 'a') as f:
                f.write('x')
            return result
        monkeypatch.setattr(export_check, 'check_copy', check_copy)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[1:] == [
            'FAIL project-1: 1 chapters unmatched by the migration; '
            'files in the project directory changed',
            'FAIL project-2: 1 chapters unmatched by the migration',
            'TOTAL projects 2  exports 0  translated segments 0  as translation 0  '
            'kept original 0  toc labels changed 0/0 (0 into translated chapters)  epubcheck not run  failed projects 2']

    def test_totals_over_two_failing_projects(self, tmp_path, capsys, monkeypatch):
        from tests.test_exporter import RAW_TRANSLATIONS, _project, _raw_book
        root = tmp_path / 'projects'
        root.mkdir()
        (tmp_path / 'b').mkdir()
        translations = dict(RAW_TRANSLATIONS, two_seg0002='字')
        for name in ('p', 'q'):
            _project(root, _raw_book(tmp_path / 'b', name=f'{name}.epub'), translations, name=name)
        _tamper(monkeypatch, _edit('OEBPS/Styles/book.css', b'0', b'1'))
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[3] == (
            'TOTAL projects 2  exports 6  translated segments 8  as translation 6  '
            'kept original 2 (structure_mismatch 2)  toc labels changed 4/4 (4 into translated chapters)  '
            'epubcheck not run  failed projects 2')


class TestExportCheckEpubcheckSurvivors:

    def test_command_and_counts(self, tmp_path, monkeypatch):
        calls = []

        def run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            with open(cmd[3], 'w') as f:
                json.dump({'messages': [
                    {'ID': 'A', 'severity': 'ERROR', 'locations': [{}, {}]},
                    {'ID': 'A', 'severity': 'ERROR', 'locations': []},
                    {'ID': 'B', 'severity': 'FATAL', 'locations': [{}]}]}, f)
        monkeypatch.setattr(export_check.shutil, 'which', lambda name: '/fake/epubcheck')
        monkeypatch.setattr(export_check.subprocess, 'run', run)
        assert export_check.epubcheck_errors('/x/book.epub') == {'A': 3, 'B': 1}
        (cmd, kwargs), = calls
        assert cmd[:3] == ['/fake/epubcheck', '/x/book.epub', '--json']
        assert kwargs == {'capture_output': True, 'text': True}

    def test_source_ids_listed(self, tmp_path, capsys, monkeypatch):
        root = _export_projects(tmp_path / 'projects', schema_1=False)
        TestExportCheckEpubcheck._fake(self, monkeypatch,
                                       {'source': [('B-1', 'ERROR', 1), ('A-1', 'ERROR', 2)]})
        assert export_check.main(['--projects-dir', str(root), '--anonymize']) == 0
        assert capsys.readouterr().out.splitlines()[1].endswith(
            'epubcheck errors source 3 (A-1:2 B-1:1), exports 0/0/0')


class TestExportCheckTocDenominator(_ExportCheckBase):
    """Fix round: entries that point into a chapter exported with a translation."""

    def test_entries_into_translated_chapters(self, tmp_path, capsys):
        from tests.test_exporter import RAW_TRANSLATIONS, _project, _raw_book
        root = tmp_path / 'projects'
        root.mkdir()
        (tmp_path / 'b').mkdir()
        # the nav and NCX entries point at chapter one, which stays untranslated
        _project(root, _raw_book(tmp_path / 'b'),
                 {k: v for k, v in RAW_TRANSLATIONS.items() if k.startswith('two')}, name='p')
        assert self._main(root) == 0
        assert capsys.readouterr().out.splitlines()[1].endswith(
            'toc labels changed 0/2 (0 into translated chapters)')

    def test_kept_chapter_is_not_counted(self, tmp_path, capsys):
        from tests.test_exporter import RAW_TRANSLATIONS, _project, _raw_book
        root = tmp_path / 'projects'
        root.mkdir()
        (tmp_path / 'b').mkdir()
        translations = dict(RAW_TRANSLATIONS, one_seg0001='<h1>守', one_seg0002='字')
        _project(root, _raw_book(tmp_path / 'b'), translations, name='p')
        assert self._main(root) == 0
        assert capsys.readouterr().out.splitlines()[1].endswith(
            'toc labels changed 0/2 (0 into translated chapters)')


class TestExportCheckReviewRound1(_ExportCheckBase):
    """Review round 1: the check script's new rules."""

    def test_untouched_member_date_changed(self, tmp_path, capsys, monkeypatch):
        import zipfile

        def change(members):
            out = []
            for info, data in members:
                if info.filename == 'OEBPS/Styles/book.css':
                    new = zipfile.ZipInfo(info.filename, date_time=(1999, 1, 1, 0, 0, 0))
                    new.compress_type = info.compress_type
                    new.external_attr = info.external_attr
                    new.create_system = info.create_system
                    info = new
                out.append((info, data))
            return out
        self._fails_with(tmp_path, capsys, monkeypatch, change,
                         'chinese-only: 1 untouched members stored differently')

    def test_untouched_member_compression_changed(self, tmp_path, capsys, monkeypatch):
        import zipfile

        def change(members):
            for info, _ in members:
                if info.filename == 'OEBPS/Styles/book.css':
                    info.compress_type = zipfile.ZIP_STORED
            return members
        self._fails_with(tmp_path, capsys, monkeypatch, change,
                         'chinese-only: 1 untouched members stored differently')

    def test_member_order_changed(self, tmp_path, capsys, monkeypatch):
        def change(members):
            return members[:1] + members[2:] + members[1:2]
        self._fails_with(tmp_path, capsys, monkeypatch, change,
                         'chinese-only: member order differs')

    def test_label_outside_the_toc_changed(self, tmp_path, capsys, monkeypatch):
        from tests.conftest import xhtml
        from tests.test_exporter import RAW_TRANSLATIONS, _project, _raw_book
        root = tmp_path / 'projects'
        root.mkdir()
        (tmp_path / 'b').mkdir()
        nav = xhtml('<nav epub:type="toc"><ol><li><a href="Text/one.xhtml">One</a></li></ol></nav>'
                    '<nav epub:type="landmarks"><ol><li><a href="Text/one.xhtml">Start</a>'
                    '</li></ol></nav>')
        _project(root, _raw_book(tmp_path / 'b', nav=nav), RAW_TRANSLATIONS, name='p')
        _tamper(monkeypatch, _edit('OEBPS/nav.xhtml', b'>Start<', b'>Begin<'))
        assert self._main(root) == 1
        line = capsys.readouterr().out.splitlines()[1]
        assert line.split('; ', 1)[1] == 'chinese-only: labels outside the table of contents changed'

    def test_stylesheet_added_to_chinese_only(self, tmp_path, capsys, monkeypatch):
        import zipfile

        def change(members):
            out = []
            for info, data in members:
                if info.filename == 'OEBPS/content.opf':
                    data = data.replace(b'</manifest>', b'<item id="x" href="x.css" '
                                        b'media-type="text/css"/></manifest>')
                out.append((info, data))
            return out + [(zipfile.ZipInfo('OEBPS/x.css'), b'p {}')]
        self._fails_with(tmp_path, capsys, monkeypatch, change,
                         'chinese-only: 1 stylesheets added')

    def test_two_stylesheets_added_to_bilingual(self, tmp_path, capsys, monkeypatch):
        import zipfile

        def change(members):
            out = []
            for info, data in members:
                if info.filename == 'OEBPS/content.opf':
                    data = data.replace(b'</manifest>', b'<item id="x" href="x.css" '
                                        b'media-type="text/css"/></manifest>')
                out.append((info, data))
            return out + [(zipfile.ZipInfo('OEBPS/x.css'), b'p {}')]
        root = _export_projects(tmp_path / 'projects', schema_1=False)
        _tamper(monkeypatch, change, ('bilingual-vertical',))
        assert self._main(root) == 1
        line = capsys.readouterr().out.splitlines()[1]
        # (with two added stylesheets the head check may also fail: set order)
        assert line.split('; ')[1] == 'bilingual-vertical: 2 stylesheets added'

    def test_anchor_name_only_on_a(self, tmp_path):
        from tests.conftest import write_raw_epub, xhtml
        epub = write_raw_epub(
            tmp_path / 'b.epub', [('c', 'c.xhtml', 'application/xhtml+xml', None)], ['c'],
            {'OEBPS/c.xhtml': xhtml('<p id="i"><a name="n"/><span name="s"/></p>')})
        assert export_check.Archive(epub).anchors('OEBPS/c.xhtml') == {'i', 'n'}

    def test_new_duplicate_of_a_duplicated_id(self):
        from lxml import etree

        def root(body):
            return etree.fromstring(f'<html><body>{body}</body></html>')
        src = root('<p id="d"/><p id="d"/><p id="e"/>')
        assert export_check.new_duplicates(root('<p id="d"/><p id="d"/><p id="e"/>'), src) == set()
        assert export_check.new_duplicates(root('<p id="d"/><p id="d"/><p id="d"/>'), src) == {'d'}
        assert export_check.new_duplicates(root('<p id="e"/><p id="e"/>'), src) == {'e'}

    def test_project_change_detected_when_the_check_raises(self, tmp_path, capsys, monkeypatch):
        root = _export_projects(tmp_path / 'projects', schema_1=False)

        def check_copy(copy_dir, out_dir):
            (root / 'beta' / 'touched.txt').write_text('x')
            raise RuntimeError('invented')
        monkeypatch.setattr(export_check, 'check_copy', check_copy)
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[1] == (
            'FAIL project-1: export check raised RuntimeError; '
            'files in the project directory changed')

    def test_scratch_directories_numbered(self, tmp_path, capsys, monkeypatch):
        root = _export_projects(tmp_path / 'projects', schema_1=False)
        seen = []

        def check_copy(copy_dir, out_dir):
            seen.append((copy_dir, out_dir))
            raise RuntimeError('invented')
        monkeypatch.setattr(export_check, 'check_copy', check_copy)
        self._main(root)
        (copy_dir, out_dir), = seen
        assert copy_dir.endswith(os.path.join('1', 'project'))
        assert out_dir.endswith(os.path.join('1', 'exports'))
        assert 'beta' not in copy_dir and 'beta' not in out_dir

    def test_setup_failure_anonymized(self, tmp_path, capsys, monkeypatch):
        root = _export_projects(tmp_path / 'projects', schema_1=False)

        def copy2(src, dst):
            raise OSError(f'invented failure on {src}')
        monkeypatch.setattr(export_check.shutil, 'copy2', copy2)
        assert self._main(root) == 1
        out = capsys.readouterr().out
        assert 'FAIL project-1: export check raised OSError' in out and 'beta' not in out


class TestExportCheckRound1Survivors(_ExportCheckBase):
    """Mutation run 5: the check script's new rules spelled out."""

    def _mimetype_last_book(self, root, books):
        """A project whose source has mimetype last, deflated, with an extra field."""
        import zipfile
        from tests.test_exporter import RAW_TRANSLATIONS, _project, _raw_book
        epub = _raw_book(books)
        with zipfile.ZipFile(epub) as z:
            rest = [(i, z.read(i)) for i in z.infolist() if i.filename != 'mimetype']
        info = zipfile.ZipInfo('mimetype', date_time=(2002, 3, 4, 5, 6, 8))
        info.extra = b'\xfe\xca\x00\x00'
        with zipfile.ZipFile(epub, 'w') as z:
            for i, d in rest:
                z.writestr(i, d)
            z.writestr(info, 'application/epub+zip', compress_type=zipfile.ZIP_DEFLATED)
        _project(root, epub, RAW_TRANSLATIONS, name='p')

    def test_source_mimetype_last_deflated_with_extra_passes(self, tmp_path, capsys):
        root = tmp_path / 'projects'
        root.mkdir()
        (tmp_path / 'b').mkdir()
        self._mimetype_last_book(root, tmp_path / 'b')
        assert self._main(root) == 0, capsys.readouterr().out
        assert capsys.readouterr().out.splitlines()[1].startswith('PASS project-1: ')

    def test_toc_entries_differ(self, tmp_path, capsys, monkeypatch):
        self._fails_with(tmp_path, capsys, monkeypatch,
                         _edit('OEBPS/nav.xhtml', b'</li></ol>',
                               b'</li><li><a href="Text/one.xhtml">X</a></li></ol>'),
                         'chinese-only: table of contents entries differ')

    def test_nav_point_outside_the_nav_map_not_counted(self, tmp_path):
        from tests.conftest import write_raw_epub
        ncx = ('<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">'
               '<navMap><navPoint><navLabel><text>One</text></navLabel></navPoint></navMap>'
               '<navList><navPoint><navLabel><text>Odd</text></navLabel></navPoint></navList></ncx>')
        epub = write_raw_epub(tmp_path / 'b.epub', [('ncx', 'toc.ncx', 'application/x-dtbncx+xml', None)],
                              [], {'OEBPS/toc.ncx': ncx})
        archive = export_check.Archive(epub)
        assert [e[2] for e in export_check.toc_labels(archive)] == ['One']
        assert export_check.other_labels(archive) == [('OEBPS/toc.ncx', 'Odd')]

    def test_snapshot_failure_reported_once(self, tmp_path, capsys, monkeypatch):
        root = _export_projects(tmp_path / 'projects', schema_1=False)

        def snapshot(path):
            raise PermissionError('invented')
        monkeypatch.setattr(export_check, 'snapshot', snapshot)
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[1] == (
            'FAIL project-1: export check raised PermissionError')


class TestExportCheckReviewRound2(_ExportCheckBase):

    def test_final_snapshot_failure_reported_and_others_checked(self, tmp_path, capsys,
                                                                 monkeypatch):
        root = _export_projects(tmp_path / 'projects')
        real, calls = export_check.snapshot, []

        def snapshot(path):
            calls.append(path)
            if len(calls) == 2:  # the first project's final snapshot
                raise PermissionError(f'invented failure on {path}')
            return real(path)
        monkeypatch.setattr(export_check, 'snapshot', snapshot)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[1].startswith('FAIL project-1: ') and out[1].endswith(
            'project snapshot raised PermissionError')
        assert out[2].startswith('PASS project-2: ')
        assert 'alpha' not in '\n'.join(out) and 'invented failure' not in '\n'.join(out)


class TestExportCheckRound2Survivors(_ExportCheckBase):
    """Mutation run 7 (mutmut 3): the check script's rules spelled out."""

    def test_two_broken_links_counted(self, tmp_path, capsys, monkeypatch):
        self._fails_with(tmp_path, capsys, monkeypatch,
                         _edit_all({'OEBPS/toc.ncx': (b'src="Text/one.xhtml"',
                                                      b'src="Text/one.xhtml#nowhere"'),
                                    'OEBPS/nav.xhtml': (b'href="Text/one.xhtml"',
                                                        b'href="Text/one.xhtml#gone"')}),
                         'chinese-only: 2 fragment links do not resolve')

    def test_labels_only_from_nav_and_ncx_files(self, tmp_path):
        from tests.conftest import write_raw_epub, xhtml
        ncx = ('<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">'
               '<text>Loose</text><navMap/></ncx>')
        chapter = xhtml('<svg xmlns="http://www.w3.org/2000/svg"><text>Drawn</text></svg>')
        epub = write_raw_epub(
            tmp_path / 'b.epub', [('ncx', 'toc.ncx', 'application/x-dtbncx+xml', None),
                                  ('c', 'c.xhtml', 'application/xhtml+xml', None)], ['c'],
            {'OEBPS/toc.ncx': ncx, 'OEBPS/c.xhtml': chapter})
        archive = export_check.Archive(epub)
        assert export_check.other_labels(archive) == [('OEBPS/toc.ncx', 'Loose')]
        assert export_check.toc_labels(archive) == []

    def test_head_items_without_head(self):
        from lxml import etree
        assert export_check.head_items(etree.fromstring('<html><body/></html>')) == []

    @pytest.mark.parametrize('change', ['content', 'rename'])
    def test_changed_file_in_the_project_directory(self, tmp_path, capsys, monkeypatch, change):
        root = _export_projects(tmp_path / 'projects', schema_1=False)
        glossary = root / 'beta' / 'notes.txt'
        glossary.write_text('a')
        stat = os.stat(glossary)
        real = export_check.check_copy

        def check_copy(copy_dir, out_dir):
            result = real(copy_dir, out_dir)
            if change == 'content':
                glossary.write_text('bb')
            else:
                os.rename(glossary, root / 'beta' / 'other.txt')
                os.utime(root / 'beta' / 'other.txt', ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return result
        monkeypatch.setattr(export_check, 'check_copy', check_copy)
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[1].endswith(
            '; files in the project directory changed')

    def test_listing_error_shows_the_path_without_anonymize(self, tmp_path, capsys):
        missing = tmp_path / 'missing'
        assert self._main(missing, anonymize=False) == 2
        assert capsys.readouterr().out == (
            f'ERROR cannot list projects directory {missing} (FileNotFoundError)\n')


class TestExportCheckReviewRound3(_ExportCheckBase):

    def test_export_member_with_a_zip64_record_passes(self, tmp_path, capsys, monkeypatch):
        # review round 4: a ZIP64 record in the export's extra field (as zipfile
        # writes for large members) is no difference either
        def change(members):
            for info, _ in members:
                if info.filename == 'OEBPS/Styles/book.css':
                    info.extra = b'\x01\x00\x08\x00' + (8).to_bytes(8, 'little')
            return members
        root = _export_projects(tmp_path / 'projects', schema_1=False)
        _tamper(monkeypatch, change)
        assert self._main(root) == 0, capsys.readouterr().out

    def test_source_member_with_a_zip64_record_passes(self, tmp_path, capsys):
        import zipfile
        from tests.test_exporter import RAW_TRANSLATIONS, _project, _raw_book
        root = tmp_path / 'projects'
        root.mkdir()
        (tmp_path / 'b').mkdir()
        epub = _raw_book(tmp_path / 'b')
        info = zipfile.ZipInfo('OEBPS/Images/pic.png')
        info.extra = b'\x01\x00\x08\x00' + (8).to_bytes(8, 'little') + b'\xfe\xca\x02\x00xy'
        with zipfile.ZipFile(epub, 'a') as z:
            z.writestr(info, b'invented')
        _project(root, epub, RAW_TRANSLATIONS, name='p')
        assert self._main(root) == 0, capsys.readouterr().out
        assert capsys.readouterr().out.splitlines()[1].startswith('PASS project-1: ')

    # processing instructions have pseudo-attributes: never classes, ids or anchors
    def _root(self, body):
        from lxml import etree
        return etree.fromstring(f'<html><body>{body}</body></html>')

    def test_pi_class_is_no_bilingual_row(self):
        assert not export_check.uses_bilingual(self._root('<?pi class="bilingual-container"?>'))

    def test_pi_id_is_not_counted(self):
        root = self._root('<p id="d"/><?pi id="d"?><?pi id="e"?>')
        assert export_check.id_counts(root) == {'d': 1}

    def test_pi_id_is_no_anchor(self, tmp_path):
        from tests.conftest import write_raw_epub, xhtml
        epub = write_raw_epub(
            tmp_path / 'b.epub', [('c', 'c.xhtml', 'application/xhtml+xml', None)], ['c'],
            {'OEBPS/c.xhtml': xhtml('<p id="i"><?pi id="z" name="y"?></p>')})
        assert export_check.Archive(epub).anchors('OEBPS/c.xhtml') == {'i'}

    def test_pi_href_is_no_link(self, tmp_path):
        from tests.conftest import write_raw_epub, xhtml
        epub = write_raw_epub(
            tmp_path / 'b.epub', [('c', 'c.xhtml', 'application/xhtml+xml', None)], ['c'],
            {'OEBPS/c.xhtml': xhtml('<p><?pi href="#nowhere"?></p>')})
        archive = export_check.Archive(epub)
        assert export_check.broken_links(archive, []) == set()


class TestMutationScript:
    """Review round 3: the mutation run's cleanup."""

    def test_stop_group_after_the_leader_exited(self):
        import subprocess
        mutation = _load('mutation')
        proc = subprocess.Popen(['/bin/sh', '-c', 'sleep 60 & echo $!; exit 0'],
                                stdout=subprocess.PIPE, text=True, start_new_session=True)
        child = int(proc.stdout.readline())
        proc.wait()
        os.kill(child, 0)  # still running, in the leader's group
        proc.stdout.close()
        mutation.stop_group(proc)
        deadline = time.monotonic() + 10  # the reaper may take a moment
        while time.monotonic() < deadline:
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        with pytest.raises(ProcessLookupError):
            os.killpg(proc.pid, 0)


# --- Phase 4: protection round trip and the full check --------------------------

validate_check = _load('phase4_validate_check')

LONG_EN = 'abcdefghi ' * 20 + 'abcdefg'                       # 187 characters without spaces
LONG_CODE = 'value = compute(alpha, beta)\n' * 9                # 225 characters without spaces


def _validate_projects(root):
    """Projects for the Phase 4 check: the schema 1 legacy project (migrated
    by the check) and a schema 2 project with translations that fail and
    pass, one of them long enough for the length ratio."""
    from tests.conftest import xhtml
    from tests.test_exporter import _project, _raw_book
    from tests.test_migrate import _legacy_project
    root.mkdir()
    _legacy_project(root, name='alpha')
    book_dir = root.parent / 'books'
    book_dir.mkdir()
    chapter = xhtml(f'<h1>Head words</h1><p>A <em>b</em> c.</p><p>Plain one.</p>'
                    f'<p>{LONG_EN}</p><pre># note\nx = 1</pre><p>Same words.</p>')
    # a failed segment with text, then a long code segment and a paragraph
    # that are checked: none of them goes into the length ratio
    other = xhtml(f'<p>Skipped words.</p><pre>{LONG_CODE}</pre><p>Tail words.</p>')
    epub = _raw_book(book_dir, {'c': ('c.xhtml', chapter), 'd': ('d.xhtml', other)},
                     nav=None, ncx=None)
    project_dir = _project(root, epub, {
        'c_seg0001': '<h1>標題</h1>',
        'c_seg0002': '<p>甲乙丙。</p>\n<p>普通。</p>\n<p>' + '長' * 100 + '</p>',
        'c_seg0003': '# 註\nx = 2',
        'c_seg0004': '<p>Same words.</p>',
        'd_seg0001': '<p>壞',
        'd_seg0002': LONG_CODE,
        'd_seg0003': '<p>尾字。</p>'}, statuses={'d_seg0001': 'failed'}, name='beta')
    return root, project_dir


def validation_status(name):
    from src.core.project import SegmentStatus
    return SegmentStatus(name)


class TestValidateCheck:

    def _main(self, root, anonymize=True):
        return validate_check.main(['--projects-dir', str(root)]
                                   + (['--anonymize'] if anonymize else []))

    def test_lines(self, tmp_path, capsys):
        root, _ = _validate_projects(tmp_path / 'projects')
        (root / 'not-a-project').mkdir()
        before = roundtrip_check.snapshot(str(root))
        assert self._main(root) == 0
        assert capsys.readouterr().out.splitlines() == [
            'PASS project-1: round trip 3/3, translated 3, failing the structural check 0 '
            '(none; only tag rules 0), warnings (none), length ratio: no segment, '
            'sources that cannot be cut 0, with translatable attributes 0, '
            'translated code starting with a shebang 0',
            'PASS project-2: round trip 7/7, translated 6, failing the structural check 2 '
            '(code_changed 1, tag_count 1; only tag rules 1), warnings (untranslated 1), '
            'length ratio: no segment, sources that cannot be cut 0, '
            'with translatable attributes 0, translated code starting with a shebang 0',
            'TOTAL projects 2  round trip 10/10  translated 9  failing the structural check 2 '
            '(code_changed 1, tag_count 1; only tag rules 1)  warnings (untranslated 1)  '
            'length ratio: no segment  sources that cannot be cut 0  '
            'with translatable attributes 0  translated code starting with a shebang 0  '
            'failed projects 0',
        ]
        assert roundtrip_check.snapshot(str(root)) == before

    def test_length_ratio_counted(self, tmp_path, capsys):
        root, project_dir = _validate_projects(tmp_path / 'projects')
        pm = validate_check.ProjectManager(project_dir)
        pm.load()
        pm.mark_translated('c_seg0002', '<p>甲<em>乙</em>丙。</p>\n<p>普通。</p>\n<p>'
                           + '長' * 100 + '</p>')
        pm.save()
        assert self._main(root) == 0
        out = capsys.readouterr().out.splitlines()
        # the second segment: 3 paragraphs, 107 / 200 characters (the
        # smallest source counted); the others are shorter, code is left out
        ratio = ('length ratio over 1 segments: p1 0.535 p5 0.535 p50 0.535 '
                 'p95 0.535 p99 0.535; outside 0.25-0.8: 0 (0.0%)')
        assert out[1].endswith(f'{ratio}, sources that cannot be cut 0, '
                               'with translatable attributes 0, '
                               'translated code starting with a shebang 0')
        assert out[2].endswith(f'{ratio}  sources that cannot be cut 0  '
                               'with translatable attributes 0  '
                               'translated code starting with a shebang 0  failed projects 0')

    def test_failing_every_tag_rule_counts_as_tags_only(self, tmp_path, capsys):
        root, project_dir = _validate_projects(tmp_path / 'projects')
        pm = validate_check.ProjectManager(project_dir)
        pm.load()
        seg = pm._find('c_seg0004')
        seg.html = '<p>Same <a id="x">wo</a><b>rds</b>.</p>'
        pm.mark_translated('c_seg0004', '<p>甲<span>乙</span><b class="z">丙</b></p>')
        pm.save()
        assert self._main(root) == 0
        out = capsys.readouterr().out.splitlines()
        assert ('(code_changed 1, extra_attributes 1, key_mismatch 1, tag_count 2, '
                'unknown_tag 1; only tag rules 2)') in out[1]

    def test_sources_and_attributes_counted(self, tmp_path, capsys):
        root, project_dir = _validate_projects(tmp_path / 'projects')
        pm = validate_check.ProjectManager(project_dir)
        pm.load()
        segs = {s.segment_id: s for s in pm.data.segments}
        # counted whatever the status: a pending segment with alt, a failed
        # one whose source no longer matches its fingerprints; code is not
        segs['d_seg0003'].html = '<p>Tail words.<img src="t.png" alt="words"/></p>'
        segs['d_seg0003'].status = validation_status('pending')
        segs['d_seg0001'].fingerprints = ['0' * 16]
        segs['c_seg0002'].html = segs['c_seg0002'].html.replace('<p>', '<p title="">', 1)
        segs['d_seg0002'].html = '<pre title="t">x</pre>'
        pm.save()
        assert self._main(root) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[1].endswith('sources that cannot be cut 1, with translatable attributes 2, '
                               'translated code starting with a shebang 0')
        assert 'translated 5, ' in out[1]
        assert out[2].endswith('sources that cannot be cut 1  with translatable attributes 2  '
                               'translated code starting with a shebang 0  failed projects 0')

    def test_names_without_anonymize(self, tmp_path, capsys):
        root, _ = _validate_projects(tmp_path / 'projects')
        assert self._main(root, anonymize=False) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[0].startswith('PASS alpha: ') and out[1].startswith('PASS beta: ')

    def test_round_trip_failure_fails(self, tmp_path, capsys, monkeypatch):
        root, _ = _validate_projects(tmp_path / 'projects')
        monkeypatch.setattr(validate_check, 'restore', lambda text, p: text + ' ')
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[0].startswith('FAIL project-1: round trip 0/3, ')
        assert out[0].endswith('; round trip fails for 3 segments')
        assert out[2].startswith('TOTAL projects 2  round trip 0/10  ')
        assert out[2].endswith('failed projects 2')

    def test_unmatched_migration_fails(self, tmp_path, capsys, monkeypatch):
        root, _ = _validate_projects(tmp_path / 'projects')

        class Report:
            unmatched = ['x', 'y']
        monkeypatch.setattr(validate_check, 'migrate_project', lambda d: Report())
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[0] == 'FAIL project-1: 2 chapters unmatched by the migration'
        assert out[1].startswith('PASS project-2: ')

    def test_exception_is_a_fail(self, tmp_path, capsys, monkeypatch):
        root, _ = _validate_projects(tmp_path / 'projects')

        def boom(*a):
            raise KeyError('secret text')
        monkeypatch.setattr(validate_check, 'check_segment', boom)
        assert self._main(root) == 1
        out = capsys.readouterr().out
        assert 'FAIL project-1: check raised KeyError' in out and 'secret' not in out
        assert 'FAIL project-2: check raised KeyError' in out
        assert out.splitlines()[-1].endswith('failed projects 2')

    def test_changed_project_directory_fails(self, tmp_path, capsys, monkeypatch):
        root, project_dir = _validate_projects(tmp_path / 'projects')
        real = validate_check.check_copy

        def touch(copy_dir):
            with open(os.path.join(project_dir, 'extra.txt'), 'w') as f:
                f.write('x')
            return real(copy_dir)
        monkeypatch.setattr(validate_check, 'check_copy', touch)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[1].startswith('FAIL project-2: ')
        assert out[1].endswith('; files in the project directory changed')

    def test_snapshot_error_is_a_fail(self, tmp_path, capsys, monkeypatch):
        root, _ = _validate_projects(tmp_path / 'projects')
        calls = []

        def snapshot(path):
            calls.append(path)
            if len(calls) == 2:
                raise OSError('gone')
            return []
        monkeypatch.setattr(validate_check, 'snapshot', snapshot)
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[0] == \
            'FAIL project-1: project snapshot raised OSError'

    def test_work_directory_removed_and_outside_repository(self, tmp_path, capsys, monkeypatch):
        root, _ = _validate_projects(tmp_path / 'projects')
        made, copies = [], []
        real = validate_check.tempfile.mkdtemp
        real_check = validate_check.check_copy

        def mkdtemp(**kwargs):
            made.append(real(**kwargs))
            return made[-1]

        def check(copy_dir):
            copies.append((copy_dir, sorted(os.listdir(copy_dir))))
            return real_check(copy_dir)
        monkeypatch.setattr(validate_check.tempfile, 'mkdtemp', mkdtemp)
        monkeypatch.setattr(validate_check, 'check_copy', check)
        assert self._main(root) == 0
        assert len(made) == 1 and not os.path.exists(made[0])
        assert os.path.basename(made[0]).startswith('phase4-validate-')
        assert not os.path.realpath(made[0]).startswith(ROOT)
        assert [c[0] for c in copies] == [os.path.join(made[0], '1'), os.path.join(made[0], '2')]
        assert copies[0][1] == ['project.json', 'source.epub']
        assert copies[1][1] == ['project.json']  # the second has its source outside

    @pytest.mark.parametrize('where', ['repository', 'projects'])
    def test_refuses_temporary_directory_inside(self, tmp_path, capsys, monkeypatch, where):
        root, _ = _validate_projects(tmp_path / 'projects')
        place = os.path.join(ROOT, 'src') if where == 'repository' else str(root / 'beta')
        monkeypatch.setattr(validate_check.tempfile, 'gettempdir', lambda: place)
        assert self._main(root) == 2
        what = 'the repository' if where == 'repository' else 'the projects directory'
        assert capsys.readouterr().out == f'ERROR the temporary directory would be inside {what}\n'
        assert not any(n.startswith('phase4-validate-') for n in os.listdir(place))

    def test_missing_directory(self, tmp_path, capsys):
        assert self._main(tmp_path / 'nope') == 2
        assert capsys.readouterr().out == (
            'ERROR cannot list projects directory <projects-dir> (FileNotFoundError)\n')
        assert self._main(tmp_path / 'nope', anonymize=False) == 2
        assert str(tmp_path / 'nope') in capsys.readouterr().out

    def test_percentile_nearest_rank(self):
        values = [5, 1, 4, 2, 3]
        assert [validate_check.percentile(values, p) for p in (1, 20, 21, 50, 99, 100)] == \
            [1, 1, 2, 3, 5, 5]
        assert validate_check.percentile([7], 1) == 7

    def test_ratio_text(self):
        ratios = [0.1, 0.25, 0.5, 0.8, 0.9]
        assert validate_check.ratio_text(ratios) == (
            'length ratio over 5 segments: p1 0.100 p5 0.100 p50 0.500 p95 0.900 p99 0.900; '
            'outside 0.25-0.8: 2 (40.0%)')
        assert validate_check.ratio_text([]) == 'length ratio: no segment'

    def test_counts_text(self):
        from collections import Counter
        assert validate_check.counts_text(Counter({'b': 2, 'a': 1})) == 'a 1, b 2'
        assert validate_check.counts_text(Counter()) == 'none'

    def test_help(self, tmp_path):
        result = _run_script('phase4_validate_check', ['--help'], str(tmp_path))
        assert result.returncode == 0
        assert 'Phase 4 check: protection round trip' in result.stdout


class TestValidateCheckSurvivors:
    """Mutation testing, Phase 4 run 1: the check script in-process."""

    def test_help_in_process(self, capsys):
        with pytest.raises(SystemExit):
            validate_check.main(['--help'])
        out = ' '.join(capsys.readouterr().out.split())
        assert ('Phase 4 check: protection round trip and the full check over existing '
                'translations.') in out
        assert ('--projects-dir PROJECTS_DIR Directory that holds the project folders '
                '(default: projects)') in out
        assert '--anonymize Print project-N labels; omit names and paths' in out
        assert 'Copies every project' not in out  # only the docstring's first line

    def test_default_projects_directory(self, tmp_path, capsys, monkeypatch):
        _validate_projects(tmp_path / 'projects')
        monkeypatch.chdir(tmp_path)
        listed = []
        real = validate_check.os.listdir

        def listdir(path):
            listed.append(path)
            return real(path)
        monkeypatch.setattr(validate_check.os, 'listdir', listdir)
        assert validate_check.main(['--anonymize']) == 0
        assert listed == ['projects']
        assert capsys.readouterr().out.splitlines()[0].startswith('PASS project-1: ')

    def test_work_directory_under_the_resolved_temporary_directory(self, tmp_path, capsys,
                                                                   monkeypatch):
        root, _ = _validate_projects(tmp_path / 'projects')
        real_tmp = tmp_path / 'real-tmp'
        real_tmp.mkdir()
        link = tmp_path / 'tmp-link'
        link.symlink_to(real_tmp)
        made = []
        real = validate_check.tempfile.mkdtemp

        def mkdtemp(**kwargs):
            made.append(real(**kwargs))
            return made[-1]
        monkeypatch.setattr(validate_check.tempfile, 'gettempdir', lambda: str(link))
        monkeypatch.setattr(validate_check.tempfile, 'mkdtemp', mkdtemp)
        assert validate_check.main(['--projects-dir', str(root), '--anonymize']) == 0
        assert os.path.dirname(made[0]) == os.path.realpath(str(real_tmp))

    def test_percentile_rank(self):
        assert validate_check.percentile(list(range(1, 102)), 100) == 101
        assert validate_check.percentile(list(range(1, 201)), 99) == 198

    def test_two_problems_joined(self, tmp_path, capsys, monkeypatch):
        root, project_dir = _validate_projects(tmp_path / 'projects')
        real = validate_check.check_copy

        def touch(copy_dir):
            with open(os.path.join(project_dir, 'extra.txt'), 'w') as f:
                f.write('x')
            r, problems = real(copy_dir)
            return r, problems + ['first problem']
        monkeypatch.setattr(validate_check, 'check_copy', touch)
        assert validate_check.main(['--projects-dir', str(root), '--anonymize']) == 1
        assert capsys.readouterr().out.splitlines()[1].endswith(
            '; first problem; files in the project directory changed')

    def test_two_problems_joined_without_counts(self, tmp_path, capsys, monkeypatch):
        root, project_dir = _validate_projects(tmp_path / 'projects')

        def check(copy_dir):
            with open(os.path.join(project_dir, 'extra.txt'), 'w') as f:
                f.write('x')
            return {}, ['no counts']
        monkeypatch.setattr(validate_check, 'check_copy', check)
        assert validate_check.main(['--projects-dir', str(root), '--anonymize']) == 1
        assert capsys.readouterr().out.splitlines()[1] == \
            'FAIL project-2: no counts; files in the project directory changed'

    def test_first_snapshot_failing_is_not_a_change(self, tmp_path, capsys, monkeypatch):
        root, _ = _validate_projects(tmp_path / 'projects')
        calls = []

        def snapshot(path):
            calls.append(path)
            if len(calls) == 1:
                raise OSError('gone')
            return []
        monkeypatch.setattr(validate_check, 'snapshot', snapshot)
        assert validate_check.main(['--projects-dir', str(root), '--anonymize']) == 1
        assert capsys.readouterr().out.splitlines()[0] == \
            'FAIL project-1: check raised OSError'

    def test_snapshot_names_relative(self, tmp_path):
        (tmp_path / 'p' / 'log').mkdir(parents=True)
        (tmp_path / 'p' / 'project.json').write_text('{}')
        (tmp_path / 'p' / 'log' / 'a.txt').write_text('ab')
        assert [(n, size) for n, size, _ in validate_check.snapshot(str(tmp_path / 'p'))] == \
            [('project.json', 2), (os.path.join('log', 'a.txt'), 2)]


# --- Phase 5: glossary compliance --------------------------------------------------

glossary_check = _load('phase5_glossary_check')


def _glossary_projects(root):
    """alpha: the schema 1 legacy project (migrated by the check) with a
    glossary; beta: a schema 2 project with warnings of two categories, a
    complying parenthetical original, a code segment, a structural failure,
    a work title and an entry without translation."""
    from tests.conftest import xhtml
    from tests.test_exporter import _project, _raw_book
    from tests.test_migrate import _legacy_project
    root.mkdir()
    alpha = _legacy_project(root, name='alpha')
    _write_glossary(alpha, [('boats', '小船', 'term'), ('Harbour', '港', 'place')])
    book_dir = root.parent / 'books'
    book_dir.mkdir()
    chapter = xhtml('<h1>Drip Line</h1><p>The drip line moved.</p><p>Velmora is far.</p>'
                    '<pre># drip line\nx = 1</pre><h2>Other words</h2>')
    other = xhtml('<p>The Silver Atlas and the drip line.</p>')
    epub = _raw_book(book_dir, {'c': ('c.xhtml', chapter), 'd': ('d.xhtml', other)},
                     nav=None, ncx=None)
    beta = _project(root, epub, {
        'c_seg0001': '<h1>滴線</h1>',
        'c_seg0002': '<p>滴水線（drip line）移動了。</p>\n<p>某城（Velmora）很遠。</p>',
        'c_seg0003': '# 滴線\nx = 1',
        'c_seg0004': '<h2>其他字</h2>',
        'd_seg0001': '<p>地圖集與滴線。<b>x</b></p>'}, name='beta')
    _write_glossary(beta, [('drip line', '滴水線', 'term'), ('Velmora', '維爾摩拉', 'place'),
                           ('Silver Atlas', '銀色地圖集', 'work'), ('other', '', 'term')])
    return root, beta


def _write_glossary(project_dir, items):
    from src.glossary.glossary_manager import GlossaryManager
    gm = GlossaryManager(os.path.join(project_dir, 'glossary.json'))
    for term, translation, category in items:
        gm.add_term(term, translation, category)


GLOSSARY_LINES = [
    'PASS project-1: glossary entries 2, translated 3, passing the structural check 3, '
    'with term_not_applied 1, terms 1, by category (term: 1 warnings, 1 terms)',
    'PASS project-2: glossary entries 4, translated 5, passing the structural check 4, '
    'with term_not_applied 2, terms 2, by category (place: 1 warnings, 1 terms, '
    'term: 1 warnings, 1 terms)',
    'TOTAL projects 2  glossary entries 6, translated 8, passing the structural check 7, '
    'with term_not_applied 3, terms 3, by category (place: 1 warnings, 1 terms, '
    'term: 2 warnings, 2 terms)  failed projects 0',
]


class TestGlossaryCheck:

    def _main(self, root, *extra, anonymize=True):
        return glossary_check.main(['--projects-dir', str(root), *extra]
                                   + (['--anonymize'] if anonymize else []))

    def test_lines(self, tmp_path, capsys):
        root, _ = _glossary_projects(tmp_path / 'projects')
        (root / 'not-a-project').mkdir()
        before = roundtrip_check.snapshot(str(root))
        assert self._main(root) == 0
        assert capsys.readouterr().out.splitlines() == GLOSSARY_LINES
        assert roundtrip_check.snapshot(str(root)) == before

    def test_names_without_anonymize(self, tmp_path, capsys):
        root, _ = _glossary_projects(tmp_path / 'projects')
        assert self._main(root, anonymize=False) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[0].startswith('PASS alpha: ') and out[1].startswith('PASS beta: ')

    def test_sample_file(self, tmp_path, capsys):
        root, _ = _glossary_projects(tmp_path / 'projects')
        out_file = tmp_path / 'sample.md'
        assert self._main(root, '--sample-out', str(out_file)) == 0
        out = capsys.readouterr().out
        assert out.splitlines() == GLOSSARY_LINES + ['sample written: 3 warnings']
        assert str(out_file) not in out and '滴' not in out
        text = out_file.read_text(encoding='utf-8')
        assert text.startswith('# 術語檢查樣本（3 筆）\n')
        assert '固定亂數種子（5）' in text
        items = text.split('\n## ')[1:]
        # in turn from each project: project-1, project-2, project-2
        assert [i.split(' · ')[0] for i in items] == ['1. project-1', '2. project-2',
                                                      '3. project-2']
        assert items[0] == (
            '1. project-1 · `c1_seg0002`\n\n'
            '- 術語：boats（術語）\n'
            '- 術語表譯名：小船\n'
            '- 原文：The boats came home. Nets were dried.\n'
            '- 譯文（對應位置附近）：船回來了。 網晾乾了。\n'
            '- 判斷：☐ 真正的問題　☐ 誤報　備註：\n')
        assert {i.split('\n')[2] for i in items[1:]} == {'- 術語：drip line（術語）',
                                                         '- 術語：Velmora（地名）'}

    def test_failing_project(self, tmp_path, capsys, monkeypatch):
        root, _ = _glossary_projects(tmp_path / 'projects')

        def broken(copy_dir):
            raise OSError('no')
        monkeypatch.setattr(glossary_check, 'check_copy', broken)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[:2] == ['FAIL project-1: check raised OSError', 'FAIL project-2: check raised OSError']
        assert out[2] == ('TOTAL projects 2  glossary entries 0, translated 0, passing the '
                          'structural check 0, with term_not_applied 0, terms 0, by category '
                          '(none)  failed projects 2')

    def test_changed_project_directory_fails(self, tmp_path, capsys, monkeypatch):
        root, beta = _glossary_projects(tmp_path / 'projects')
        real = glossary_check.check_copy

        def touching(copy_dir):
            if copy_dir.endswith('2'):
                with open(os.path.join(beta, 'extra.txt'), 'w') as f:
                    f.write('x')
            return real(copy_dir)
        monkeypatch.setattr(glossary_check, 'check_copy', touching)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[1] == 'FAIL ' + GLOSSARY_LINES[1][len('PASS '):] + \
            '; files in the project directory changed'
        assert out[2].endswith('failed projects 1')

    def test_snapshot_error(self, tmp_path, capsys, monkeypatch):
        root, _ = _glossary_projects(tmp_path / 'projects')
        calls = []

        def snapshot(path):  # the one after the check fails
            calls.append(path)
            if len(calls) == 2:
                raise OSError('gone')
            return []
        monkeypatch.setattr(glossary_check, 'snapshot', snapshot)
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[0] == \
            'FAIL project-1: project snapshot raised OSError'

    def test_unmatched_migration(self, tmp_path, capsys):
        from tests.test_migrate import _legacy_project, _segment
        root = tmp_path / 'projects'
        root.mkdir()
        _legacy_project(root, segments=[_segment('c1', 1, '<p>Not in the book.</p>', '<p>無</p>',
                                                 'translated')], name='alpha')
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[0] == \
            'FAIL project-1: 1 chapters unmatched by the migration'

    def test_work_directory_removed_and_outside_repository(self, tmp_path, capsys, monkeypatch):
        root, _ = _glossary_projects(tmp_path / 'projects')
        made, copies = [], []
        real = glossary_check.tempfile.mkdtemp
        real_check = glossary_check.check_copy

        def mkdtemp(**kwargs):
            made.append(real(**kwargs))
            return made[-1]

        def check(copy_dir):
            copies.append((copy_dir, sorted(os.listdir(copy_dir))))
            return real_check(copy_dir)
        monkeypatch.setattr(glossary_check.tempfile, 'mkdtemp', mkdtemp)
        monkeypatch.setattr(glossary_check, 'check_copy', check)
        assert self._main(root) == 0
        assert len(made) == 1 and not os.path.exists(made[0])
        assert os.path.basename(made[0]).startswith('phase5-glossary-')
        assert not os.path.realpath(made[0]).startswith(ROOT)
        assert [c[0] for c in copies] == [os.path.join(made[0], '1'), os.path.join(made[0], '2')]
        assert copies[0][1] == ['glossary.json', 'project.json', 'source.epub']
        assert copies[1][1] == ['glossary.json', 'project.json']  # its source is outside

    @pytest.mark.parametrize('where', ['repository', 'projects'])
    def test_refuses_temporary_directory_inside(self, tmp_path, capsys, monkeypatch, where):
        root, _ = _glossary_projects(tmp_path / 'projects')
        # a checkout of its own: the mutation run's copy of this one has no .git
        (tmp_path / 'checkout' / '.git').mkdir(parents=True)
        (tmp_path / 'checkout' / 'src').mkdir()
        place = str(tmp_path / 'checkout' / 'src') if where == 'repository' \
            else str(root / 'beta')
        monkeypatch.setattr(glossary_check.tempfile, 'gettempdir', lambda: place)
        assert self._main(root) == 2
        what = 'a git work tree' if where == 'repository' else 'the projects directory'
        assert capsys.readouterr().out == \
            f'ERROR the temporary directory would be inside {what}\n'
        assert not any(n.startswith('phase5-glossary-') for n in os.listdir(place))

    @pytest.mark.parametrize('where', ['repository', 'projects'])
    def test_refuses_sample_file_inside(self, tmp_path, capsys, where):
        root, _ = _glossary_projects(tmp_path / 'projects')
        (tmp_path / 'checkout' / '.git').mkdir(parents=True)
        target = str(tmp_path / 'checkout' / 'sample-x.md') if where == 'repository' \
            else str(root / 'sample-x.md')
        assert self._main(root, '--sample-out', target) == 2
        what = 'a git work tree' if where == 'repository' else 'the projects directory'
        assert capsys.readouterr().out == f'ERROR the sample file would be inside {what}\n'
        assert not os.path.exists(target)

    def test_missing_directory(self, tmp_path, capsys):
        assert self._main(tmp_path / 'nope') == 2
        assert capsys.readouterr().out == (
            'ERROR cannot list projects directory <projects-dir> (FileNotFoundError)\n')
        assert self._main(tmp_path / 'nope', anonymize=False) == 2
        assert str(tmp_path / 'nope') in capsys.readouterr().out

    def test_pick_sample_in_turn_and_seeded(self):
        per_project = [('p1', [('s', i) for i in range(5)]), ('p2', []),
                       ('p3', [('t', i) for i in range(30)])]
        sample = glossary_check.pick_sample(per_project)
        assert len(sample) == glossary_check.SAMPLE_SIZE == 20
        assert [label for label, _, _ in sample[:10]] == ['p1', 'p3'] * 5
        assert [label for label, _, _ in sample[10:]] == ['p3'] * 10
        assert sorted(e for label, _, e in sample if label == 'p1') == [0, 1, 2, 3, 4]
        assert len({e for label, _, e in sample if label == 'p3'}) == 15
        assert sample == glossary_check.pick_sample(per_project)  # fixed seed
        assert sample != glossary_check.pick_sample(per_project, seed=6)
        assert glossary_check.pick_sample(per_project, size=3) == sample[:3]
        assert glossary_check.pick_sample([]) == []
        assert per_project[0][1] == [('s', i) for i in range(5)]  # not shuffled in place

    def test_excerpt(self):
        text = 'a' * 10 + 'TERM' + 'b\n\n c' + 'd' * 10
        assert glossary_check.excerpt(text, 10, 14, width=6) == '…aaaaaaTERMb cd…'
        assert glossary_check.excerpt(text, 10, 14, width=100) == text.replace('\n\n ', ' ')
        assert glossary_check.excerpt('abc', 0, 0, width=1) == 'a…'


    def test_sample_item_without_term_in_source(self):
        from types import SimpleNamespace
        from src.glossary.glossary_manager import GlossaryEntry
        seg = SimpleNamespace(segment_id='x_seg0001', text='', translated_text='<p>譯</p>')
        item = glossary_check.sample_item(4, 'project-9', seg, GlossaryEntry('t', '詞', ''))
        assert item.splitlines()[2:6] == ['- 術語：t（（無））', '- 術語表譯名：詞', '- 原文：',
                                          '- 譯文（對應位置附近）：譯']

    def test_middle_of_translation(self):
        from types import SimpleNamespace
        from src.glossary.glossary_manager import GlossaryEntry
        seg = SimpleNamespace(segment_id='x', text='x' * 100 + ' term ' + 'y' * 94,
                              translated_text='<p>' + '甲' * 150 + '乙' * 150 + '</p>')
        item = glossary_check.sample_item(1, 'p', seg, GlossaryEntry('term', '詞', 'term'))
        lines = item.splitlines()
        assert lines[4] == '- 原文：…' + 'x' * 59 + ' term ' + 'y' * 59 + '…'
        # the term starts at 101/200 of the source: 152 of the 300 characters of
        # the translation, with EXCERPT characters on each side
        assert lines[5] == '- 譯文（對應位置附近）：…' + '甲' * 58 + '乙' * 62 + '…'

    def test_help(self, tmp_path):
        result = _run_script('phase5_glossary_check', ['--help'], str(tmp_path))
        assert result.returncode == 0
        assert 'Phase 5 check: glossary compliance' in result.stdout

    def test_help_in_process(self, capsys):
        with pytest.raises(SystemExit):
            glossary_check.main(['--help'])
        out = ' '.join(capsys.readouterr().out.split())
        assert '--sample-out FILE' in out
        assert 'Write a sample of the warnings, with book text, to FILE' in out


class TestGlossaryCheckSurvivors:
    """Mutation testing, Phase 5 run 1: the check script in-process."""

    def _main(self, root):
        return glossary_check.main(['--projects-dir', str(root), '--anonymize'])

    def test_help_in_process(self, capsys):
        with pytest.raises(SystemExit):
            glossary_check.main(['--help'])
        out = ' '.join(capsys.readouterr().out.split())
        assert 'Phase 5 check: glossary compliance of the existing translations.' in out
        assert ('--projects-dir PROJECTS_DIR Directory that holds the project folders '
                '(default: projects)') in out
        assert '--anonymize Print project-N labels; omit names and paths' in out
        assert ('--sample-out FILE Write a sample of the warnings, with book text, to FILE '
                '(outside every git work tree and the projects directory)') in out
        assert 'Copies every project' not in out  # only the docstring's first line

    def test_default_projects_directory(self, tmp_path, capsys, monkeypatch):
        _glossary_projects(tmp_path / 'projects')
        monkeypatch.chdir(tmp_path)
        listed = []
        real = glossary_check.os.listdir

        def listdir(path):
            listed.append(path)
            return real(path)
        monkeypatch.setattr(glossary_check.os, 'listdir', listdir)
        assert glossary_check.main(['--anonymize']) == 0
        assert listed == ['projects']
        assert capsys.readouterr().out.splitlines() == GLOSSARY_LINES

    def test_snapshot(self, tmp_path):
        (tmp_path / 'sub').mkdir()
        (tmp_path / 'sub' / 'b.txt').write_text('bb')
        (tmp_path / 'a.txt').write_text('a')
        snap = glossary_check.snapshot(str(tmp_path))
        assert [(name, size) for name, size, _ in snap] == [('a.txt', 1), ('sub/b.txt', 2)]
        assert snap[0][2] == os.stat(tmp_path / 'a.txt').st_mtime_ns

    def test_first_snapshot_fails(self, tmp_path, capsys, monkeypatch):
        root, _ = _glossary_projects(tmp_path / 'projects')

        def snapshot(path):
            raise OSError('gone')
        monkeypatch.setattr(glossary_check, 'snapshot', snapshot)
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[0] == 'FAIL project-1: check raised OSError'

    def test_check_and_snapshot_fail(self, tmp_path, capsys, monkeypatch):
        root, _ = _glossary_projects(tmp_path / 'projects')
        calls = []

        def snapshot(path):
            calls.append(path)
            if len(calls) == 2:
                raise OSError('gone')
            return []

        def broken(copy_dir):
            raise ValueError('bad')
        monkeypatch.setattr(glossary_check, 'snapshot', snapshot)
        monkeypatch.setattr(glossary_check, 'check_copy', broken)
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[0] == \
            'FAIL project-1: check raised ValueError; project snapshot raised OSError'

    def test_two_failing_projects_with_two_problems(self, tmp_path, capsys, monkeypatch):
        root, _ = _glossary_projects(tmp_path / 'projects')
        real = glossary_check.check_copy

        def touch(copy_dir):
            project = root / ('alpha' if copy_dir.endswith('1') else 'beta')
            (project / 'extra.txt').write_text('x')
            r, problems = real(copy_dir)
            return r, problems + ['first problem']
        monkeypatch.setattr(glossary_check, 'check_copy', touch)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[0] == 'FAIL ' + GLOSSARY_LINES[0][len('PASS '):] + \
            '; first problem; files in the project directory changed'
        assert out[2].endswith('failed projects 2')

    def test_glossary_file_name(self, tmp_path, capsys, monkeypatch):
        # the file system may ignore case: the name is checked as given
        root, _ = _glossary_projects(tmp_path / 'projects')
        paths = []
        real = glossary_check.load_glossary

        def manager(path):
            paths.append(os.path.basename(path))
            return real(path)
        monkeypatch.setattr(glossary_check, 'load_glossary', manager)
        assert self._main(root) == 0
        assert paths == ['glossary.json', 'glossary.json']

    def test_sample_written_as_utf8(self, tmp_path, capsys, monkeypatch):
        root, _ = _glossary_projects(tmp_path / 'projects')
        opened = []
        real = open

        def recording(path, mode='r', **kwargs):
            opened.append((mode, kwargs))
            return real(path, mode, **kwargs)
        monkeypatch.setattr(glossary_check, 'open', recording, raising=False)
        assert glossary_check.main(['--projects-dir', str(root), '--anonymize',
                                    '--sample-out', str(tmp_path / 's.md')]) == 0
        assert opened == [('w', {'encoding': 'utf-8'})]

    def test_skipped_segments_do_not_end_the_count(self, tmp_path):
        from tests.conftest import xhtml
        from tests.test_exporter import _project, _raw_book
        book_dir = tmp_path / 'books'
        book_dir.mkdir()
        chapter = xhtml('<p>One drip line.</p><pre>x = 1</pre><h1>Two drip line.</h1>'
                        '<h2>Three drip line.</h2><h3>Four drip line.</h3><h4>Five.</h4>')
        epub = _raw_book(book_dir, {'c': ('c.xhtml', chapter)}, nav=None, ncx=None)
        # pending; failing the structure; translated without text; failed with
        # text; then two that count
        project = _project(tmp_path, epub, {
            'c_seg0002': 'y = 2', 'c_seg0003': ' ', 'c_seg0004': '<h2>三滴線</h2>',
            'c_seg0005': '<h3>四滴線</h3>', 'c_seg0006': '<h4>五。</h4>'},
            statuses={'c_seg0004': 'failed'})
        _write_glossary(project, [('drip line', '滴水線', 'term')])
        r, problems = glossary_check.check_copy(project)
        assert problems == []
        assert (r['translated'], r['passing'], r['with_issue']) == (3, 2, 1)
        assert [seg.segment_id for seg, _ in r['found']] == ['c_seg0005']

    def test_category_text(self):
        from src.glossary.glossary_manager import GlossaryEntry
        # a term counted once per project; the case differs between two
        # segments' entries only when the glossary was edited in between
        found = [(None, GlossaryEntry('Term', '詞', '')), (None, GlossaryEntry('term', '詞', '')),
                 (None, GlossaryEntry('x', '叉', 'person'))]
        issues, terms = glossary_check.category_counts(found)
        assert glossary_check.category_text(issues, terms) == \
            'other: 2 warnings, 1 terms, person: 1 warnings, 1 terms'
        assert glossary_check.category_text(*glossary_check.category_counts([])) == 'none'

    def test_sample_item_term_case_and_no_match(self):
        from types import SimpleNamespace
        from src.glossary.glossary_manager import GlossaryEntry
        entry = GlossaryEntry('term', '詞', 'term')
        seg = SimpleNamespace(segment_id='x', text='x' * 100 + ' Term ' + 'y' * 94,
                              translated_text='甲' * 300)
        lines = glossary_check.sample_item(1, 'p', seg, entry).splitlines()
        assert lines[4] == '- 原文：…' + 'x' * 59 + ' Term ' + 'y' * 59 + '…'
        # no match (not produced by the check): both excerpts from the start
        seg = SimpleNamespace(segment_id='x', text='z' * 100, translated_text='甲' * 300)
        lines = glossary_check.sample_item(1, 'p', seg, entry).splitlines()
        assert lines[4:6] == ['- 原文：' + 'z' * 60 + '…', '- 譯文（對應位置附近）：' + '甲' * 60 + '…']
        seg = SimpleNamespace(segment_id='x', text='', translated_text='甲' * 300)
        lines = glossary_check.sample_item(1, 'p', seg, entry).splitlines()
        assert lines[5] == '- 譯文（對應位置附近）：' + '甲' * 60 + '…'

    def test_sample_markdown_head(self):
        assert glossary_check.sample_markdown([]) == '\n'.join([
            '# 術語檢查樣本（0 筆）',
            '',
            '每一筆是一段譯文被判定「術語未依術語表翻譯」（term_not_applied）。'
            '樣本以固定亂數種子（5）從各專案輪流抽出。'
            '比對方式：去掉標籤與括號內的英文原文、忽略空白與大小寫後，檢查譯文是否含有術語表譯名。',
            '請逐筆判斷是否為真正的問題（應該改譯文），或是誤報（譯文其實可以接受）。',
            '',
        ])


class TestReviewSurvivors:
    """Mutation testing, Phase 5 run 1: the review blueprint."""

    def test_glossary_file_name(self, monkeypatch):
        from types import SimpleNamespace
        from src.web.blueprints import review
        paths = []
        monkeypatch.setattr(review, 'load_glossary', lambda path: paths.append(path))
        review._glossary('/p')
        assert paths == [os.path.join('/p', 'glossary.json')]

    def test_review_tab_active(self, tmp_path):
        from src.core.project import ProjectManager
        from src.core.segmenter import Segment
        from src.web.server import create_app
        pm = ProjectManager(str(tmp_path / 'demo'))
        pm.create('/x.epub', 'T', 'A', [Segment(id='c_seg0001', html='<p>A.</p>', text='A.',
                                                word_count=1, segment_type='text')])
        client = create_app(projects_dir=str(tmp_path)).test_client()
        for url in ('/project/demo/review', '/project/demo/review/chapter/c',
                    '/project/demo/review?issues=1'):
            html = client.get(url).get_data(as_text=True)
            assert 'class="tab active">審閱</a>' in ' '.join(html.split())


# --- Phase 5 review round 1 -------------------------------------------------------

class TestGlossaryCheckRound1:

    def _main(self, root, *extra):
        return glossary_check.main(['--projects-dir', str(root), '--anonymize', *extra])

    def _checkouts(self, tmp_path):
        """A main checkout (`.git` directory) with a linked worktree inside it
        (`.git` file), as `git worktree add .worktree/x` makes them."""
        main = tmp_path / 'main'
        (main / '.git').mkdir(parents=True)
        (main / 'docs').mkdir()
        worktree = main / '.worktree' / 'x'
        worktree.mkdir(parents=True)
        (worktree / '.git').write_text('gitdir: ../../.git/worktrees/x\n')
        (tmp_path / 'outside').mkdir()
        (tmp_path / 'link').symlink_to(main / 'docs')
        return main, worktree

    def test_in_work_tree(self, tmp_path):
        main, worktree = self._checkouts(tmp_path)
        assert glossary_check.in_work_tree(str(main / 'docs' / 'new.md'))
        assert glossary_check.in_work_tree(str(main))
        assert glossary_check.in_work_tree(str(worktree / 'a' / 'b.md'))
        assert glossary_check.in_work_tree(str(tmp_path / 'link' / 'new.md'))  # symlink resolved
        assert glossary_check.in_work_tree(str(tmp_path / 'outside' / '..' / 'main' / 'x.md'))
        assert not glossary_check.in_work_tree(str(tmp_path / 'outside' / 'x.md'))
        assert not glossary_check.in_work_tree('/')

    @pytest.mark.parametrize('target', ['main/docs/s.md', 'link/s.md', 'main/.worktree/x/s.md',
                                        'outside/../main/s.md'])
    def test_sample_refused_in_any_checkout(self, tmp_path, capsys, target):
        self._checkouts(tmp_path)
        root, _ = _glossary_projects(tmp_path / 'projects')
        assert self._main(root, '--sample-out', str(tmp_path / target)) == 2
        assert capsys.readouterr().out == 'ERROR the sample file would be inside a git work tree\n'
        assert not os.path.exists(tmp_path / target)

    def test_sample_refused_in_projects_directory_through_a_link(self, tmp_path, capsys):
        root, _ = _glossary_projects(tmp_path / 'projects')
        (tmp_path / 'plink').symlink_to(root)
        assert self._main(root, '--sample-out', str(tmp_path / 'plink' / 's.md')) == 2
        assert capsys.readouterr().out == \
            'ERROR the sample file would be inside the projects directory\n'

    def test_temporary_directory_refused_in_main_checkout(self, tmp_path, capsys, monkeypatch):
        main, _ = self._checkouts(tmp_path)
        root, _ = _glossary_projects(tmp_path / 'projects')
        monkeypatch.setattr(glossary_check.tempfile, 'gettempdir', lambda: str(main / 'docs'))
        assert self._main(root) == 2
        assert capsys.readouterr().out == \
            'ERROR the temporary directory would be inside a git work tree\n'
        assert os.listdir(main / 'docs') == []

    def test_unwritable_sample_path(self, tmp_path, capsys, monkeypatch):
        root, _ = _glossary_projects(tmp_path / 'projects')
        made = []
        real = glossary_check.tempfile.mkdtemp
        monkeypatch.setattr(glossary_check.tempfile, 'mkdtemp',
                            lambda **kw: made.append(real(**kw)) or made[-1])
        target = tmp_path / 'MARKERDIR' / 'missing' / 'MARKERFILE.md'
        assert self._main(root, '--sample-out', str(target)) == 1
        out, err = capsys.readouterr()
        assert out.splitlines() == GLOSSARY_LINES + ['ERROR the check stopped (FileNotFoundError)']
        assert err == ''
        assert 'MARKER' not in out
        assert not os.path.exists(made[0])  # removed all the same

    def test_temporary_directory_cannot_be_made(self, tmp_path, capsys, monkeypatch):
        root, _ = _glossary_projects(tmp_path / 'projects')

        def mkdtemp(**kwargs):
            raise PermissionError(13, 'denied', '/MARKER/tmp')
        monkeypatch.setattr(glossary_check.tempfile, 'mkdtemp', mkdtemp)
        assert self._main(root) == 1
        out, err = capsys.readouterr()
        assert (out, err) == ('ERROR the check stopped (PermissionError)\n', '')

    def test_temporary_directory_cannot_be_removed(self, tmp_path, capsys, monkeypatch):
        root, _ = _glossary_projects(tmp_path / 'projects')
        made = []
        real = glossary_check.tempfile.mkdtemp
        monkeypatch.setattr(glossary_check.tempfile, 'mkdtemp',
                            lambda **kw: made.append(real(**kw)) or made[-1])

        real_rmtree = shutil.rmtree

        def rmtree(path):
            raise OSError(16, 'busy', '/MARKER')
        monkeypatch.setattr(glossary_check.shutil, 'rmtree', rmtree)
        try:
            assert self._main(root) == 1
        finally:
            real_rmtree(made[0])
        out, err = capsys.readouterr()
        assert out.splitlines() == GLOSSARY_LINES + [
            'ERROR cannot remove the temporary directory (OSError)']
        assert err == '' and 'MARKER' not in out

    def test_error_in_run_after_cleanup_failure(self, tmp_path, capsys, monkeypatch):
        root, _ = _glossary_projects(tmp_path / 'projects')

        def run(args, names, work):
            shutil.rmtree(work)
            raise RuntimeError('/MARKER')
        monkeypatch.setattr(glossary_check, 'run', run)
        assert self._main(root) == 1
        assert capsys.readouterr() == (
            'ERROR cannot remove the temporary directory (FileNotFoundError)\n'
            'ERROR the check stopped (RuntimeError)\n', '')

    def test_custom_category_printed_as_other(self, tmp_path, capsys):
        root, beta = _glossary_projects(tmp_path / 'projects')
        _write_glossary(beta, [('drip line', '滴水線', 'MARKER secret')])
        assert self._main(root) == 0
        out = capsys.readouterr().out
        assert 'MARKER' not in out
        assert out.splitlines()[1].endswith('by category (other: 1 warnings, 1 terms)')

    def test_unreadable_glossary_fails_the_project(self, tmp_path, capsys):
        root, beta = _glossary_projects(tmp_path / 'projects')
        with open(os.path.join(beta, 'glossary.json'), 'w') as f:
            f.write('[{"term": ')
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[1] == \
            'FAIL project-2: the glossary cannot be read'

    def test_distinct_terms(self):
        from src.glossary.glossary_manager import GlossaryEntry
        found = [(1, GlossaryEntry('Term', '詞')), (2, GlossaryEntry('term', '詞')),
                 (3, GlossaryEntry('other', '他')),
                 (4, GlossaryEntry('straße', '街')), (5, GlossaryEntry('strasse', '路'))]
        # straße and strasse are two entries (keys straße, strasse); upper()
        # would merge them
        assert glossary_check.distinct_terms(found) == 4
        issues, terms = glossary_check.category_counts(found)
        assert (issues, terms) == ({'other': 5}, {'other': 4})

    def test_totals_sum_the_projects(self, tmp_path, capsys):
        # the same term in two projects counts in both, in "terms" and per category
        root, beta = _glossary_projects(tmp_path / 'projects')
        alpha = os.path.join(root, 'alpha')
        _write_glossary(alpha, [('drip line', '滴水線', 'term'), ('boats', '小船', 'term')])
        _write_glossary(beta, [('drip line', '滴水線', 'term')])
        assert self._main(root) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[-1] == (
            'TOTAL projects 2  glossary entries 3, translated 8, passing the structural check 7, '
            'with term_not_applied 2, terms 2, by category (term: 2 warnings, 2 terms)  '
            'failed projects 0')

    def test_sample_item_work_is_case_sensitive(self):
        from types import SimpleNamespace
        from src.glossary.glossary_manager import GlossaryEntry
        seg = SimpleNamespace(segment_id='x', text='a silver atlas, then Silver Atlas ' + 'z' * 80,
                              translated_text='甲' * 10)
        lines = glossary_check.sample_item(1, 'p', seg, GlossaryEntry('Silver Atlas', '銀', 'work'))
        assert lines.splitlines()[4] == '- 原文：a silver atlas, then Silver Atlas ' + 'z' * 59 + '…'
        # where the check found it: the capitalised title (at 91), not at 72
        seg.text = 'x' * 70 + seg.text
        lines = glossary_check.sample_item(1, 'p', seg, GlossaryEntry('Silver Atlas', '銀', 'work'))
        assert lines.splitlines()[4].startswith('- 原文：…' + 'x' * 39 + 'a silver atlas, then Silver')

    def test_sample_translation_text_parsed(self):
        from types import SimpleNamespace
        from src.glossary.glossary_manager import GlossaryEntry
        seg = SimpleNamespace(segment_id='x', text='term',
                              translated_text='<p>甲<em title="a > b">乙</em>&lt;5&gt;</p>')
        lines = glossary_check.sample_item(1, 'p', seg, GlossaryEntry('term', '詞', 'term'))
        assert lines.splitlines()[5] == '- 譯文（對應位置附近）：甲乙<5>'


class TestGlossaryCheckRound1Survivors:
    """Mutation testing, Phase 5 review round 1."""

    def test_work_directory_under_the_resolved_temporary_directory(self, tmp_path, capsys,
                                                                   monkeypatch):
        root, _ = _glossary_projects(tmp_path / 'projects')
        real_tmp = tmp_path / 'real-tmp'
        real_tmp.mkdir()
        link = tmp_path / 'tmp-link'
        link.symlink_to(real_tmp)
        made = []
        real = glossary_check.tempfile.mkdtemp

        def mkdtemp(**kwargs):
            made.append(real(**kwargs))
            return made[-1]
        monkeypatch.setattr(glossary_check.tempfile, 'gettempdir', lambda: str(link))
        monkeypatch.setattr(glossary_check.tempfile, 'mkdtemp', mkdtemp)
        assert glossary_check.main(['--projects-dir', str(root), '--anonymize']) == 0
        assert os.path.dirname(made[0]) == os.path.realpath(str(real_tmp))

    def test_looks_for_dot_git(self, tmp_path, monkeypatch):
        looked = []
        real = glossary_check.os.path.lexists

        def lexists(path):
            looked.append(os.path.basename(path))
            return real(path)
        monkeypatch.setattr(glossary_check.os.path, 'lexists', lexists)
        glossary_check.in_work_tree(str(tmp_path))
        assert looked and set(looked) == {'.git'}


class TestReviewRound1Survivors:

    def test_refused_with_a_glossary_is_not_flagged(self, tmp_path):
        from src.core.project import ProjectManager
        from src.core.segmenter import Segment
        from src.web.server import create_app
        pm = ProjectManager(str(tmp_path / 'demo'))
        pm.create('/x.epub', 'T', 'A', [Segment(id='c_seg0001', html='<p>A.</p>', text='A.',
                                                word_count=1, segment_type='text')])
        client = create_app(projects_dir=str(tmp_path)).test_client()
        response = client.post('/api/projects/demo/segments/c_seg0001', json={'text': '<p>甲<b>乙</b></p>'})
        assert response.status_code == 422
        assert 'glossary_unavailable' not in response.get_json()


# --- Phase 6: segmentation check -------------------------------------------------

segmentation_check = _load('phase6_segmentation_check')


def _segmentation_projects(root):
    """alpha: the Phase 6 test book, its own source.epub in the project;
    beta: prose only, its source outside the project (the stored path)."""
    from tests.conftest import xhtml
    from tests.test_exporter import _project, _raw_book
    from tests.test_technical_segmentation import _book
    root.mkdir()
    books = root.parent / 'books'
    books.mkdir()
    alpha_book = _book(books)
    alpha = _project(root, alpha_book, {}, name='alpha')
    shutil.copy(alpha_book, os.path.join(alpha, 'source.epub'))
    os.remove(alpha_book)
    beta_dir = books / 'b'
    beta_dir.mkdir()
    beta_book = _raw_book(beta_dir, {'p': ('p.xhtml', xhtml('<p>One.</p><p>Two.</p>'))},
                          nav=None, ncx=None)
    beta = _project(root, beta_book, {}, name='beta')
    return root, alpha, beta


SEGMENTATION_LINES = [
    'PASS project-1: segments 8 -> 6, calls 8 -> 4 (ratio 0.500), passthrough 2, '
    'headings merged 2 of 3, blocks covered identically',
    'PASS project-2: segments 1 -> 1, calls 1 -> 1 (ratio 1.000), passthrough 0, '
    'headings merged 0 of 0, blocks covered identically',
    'TOTAL projects 2  segments 9 -> 7, calls 9 -> 5 (ratio 0.556), passthrough 2, '
    'headings merged 2 of 3  failed projects 0',
]


class TestSegmentationCheck:

    def _main(self, root, anonymize=True):
        return segmentation_check.main(['--projects-dir', str(root)]
                                       + (['--anonymize'] if anonymize else []))

    def test_lines(self, tmp_path, capsys):
        root, _, _ = _segmentation_projects(tmp_path / 'projects')
        (root / 'not-a-project').mkdir()
        before = roundtrip_check.snapshot(str(root))
        assert self._main(root) == 0
        assert capsys.readouterr().out.splitlines() == SEGMENTATION_LINES
        assert roundtrip_check.snapshot(str(root)) == before

    def test_names_without_anonymize(self, tmp_path, capsys):
        root, _, _ = _segmentation_projects(tmp_path / 'projects')
        assert self._main(root, anonymize=False) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[0].startswith('PASS alpha: ') and out[1].startswith('PASS beta: ')

    def test_default_projects_directory(self, tmp_path, capsys, monkeypatch):
        _segmentation_projects(tmp_path / 'projects')
        monkeypatch.chdir(tmp_path)
        assert segmentation_check.main(['--anonymize']) == 0
        assert capsys.readouterr().out.splitlines() == SEGMENTATION_LINES

    def test_copies_in_one_removed_directory(self, tmp_path, capsys, monkeypatch):
        root, alpha, _ = _segmentation_projects(tmp_path / 'projects')
        made, checked = [], []
        real = segmentation_check.tempfile.mkdtemp
        real_check = segmentation_check.check_source

        def mkdtemp(**kwargs):
            made.append(real(**kwargs))
            return made[-1]

        def check(epub):
            checked.append((epub, sorted(os.listdir(os.path.dirname(epub)))))
            return real_check(epub)
        monkeypatch.setattr(segmentation_check.tempfile, 'mkdtemp', mkdtemp)
        monkeypatch.setattr(segmentation_check, 'check_source', check)
        assert self._main(root) == 0
        assert len(made) == 1 and not os.path.exists(made[0])
        assert os.path.basename(made[0]).startswith('phase6-segmentation-')
        assert not os.path.realpath(made[0]).startswith(ROOT)
        assert checked == [(os.path.join(made[0], '1.epub'), ['1.epub']),
                           (os.path.join(made[0], '2.epub'), ['1.epub', '2.epub'])]

    def test_source_of(self, tmp_path):
        _, alpha, beta = _segmentation_projects(tmp_path / 'projects')
        assert segmentation_check.source_of(alpha) == os.path.join(alpha, 'source.epub')
        with open(os.path.join(beta, 'project.json'), encoding='utf-8') as f:
            assert segmentation_check.source_of(beta) == json.load(f)['epub_path']

    def test_missing_source_fails(self, tmp_path, capsys):
        root, _, beta = _segmentation_projects(tmp_path / 'projects')
        os.remove(segmentation_check.source_of(beta))
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[1] == 'FAIL project-2: check raised FileNotFoundError'
        assert out[2] == ('TOTAL projects 2  segments 8 -> 6, calls 8 -> 4 (ratio 0.500), '
                          'passthrough 2, headings merged 2 of 3  failed projects 1')

    def test_uncovered_blocks_fail(self, tmp_path, capsys, monkeypatch):
        root, _, _ = _segmentation_projects(tmp_path / 'projects')
        monkeypatch.setattr(segmentation_check, 'covers', lambda blocks, segs: len(segs) > 2)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        # alpha: chapter k under both rules; beta: its chapter under both rules
        assert out[0] == ('FAIL project-1: ' + SEGMENTATION_LINES[0][len('PASS project-1: '):]
                          .replace(', blocks covered identically', '')
                          + '; 2 segmentations do not cover their chapter exactly')
        assert out[1].endswith('; 2 segmentations do not cover their chapter exactly')
        assert out[2].endswith('failed projects 2')

    def test_more_calls_fail(self, tmp_path, capsys, monkeypatch):
        root, _, _ = _segmentation_projects(tmp_path / 'projects')
        real = segmentation_check.Segmenter

        class Swapped:  # the new rules given the old ones' segments and back
            def __init__(self, rules):
                self.inner = real(rules=3 - rules)

            def segment_chapter(self, *args):
                return self.inner.segment_chapter(*args)
        monkeypatch.setattr(segmentation_check, 'Segmenter', Swapped)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[0] == ('FAIL project-1: segments 6 -> 8, calls 4 -> 8 (ratio 2.000), '
                          'passthrough 0, headings merged 0 of 3; the new rules need more calls')
        assert out[1].startswith('PASS project-2: ')  # equal calls pass

    def test_changed_project_directory_fails(self, tmp_path, capsys, monkeypatch):
        root, _, beta = _segmentation_projects(tmp_path / 'projects')
        real = segmentation_check.check_source

        def touching(epub):
            if epub.endswith('2.epub'):
                with open(os.path.join(beta, 'extra.txt'), 'w') as f:
                    f.write('x')
            return real(epub)
        monkeypatch.setattr(segmentation_check, 'check_source', touching)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[1] == ('FAIL project-2: segments 1 -> 1, calls 1 -> 1 (ratio 1.000), '
                          'passthrough 0, headings merged 0 of 0; '
                          'files in the project directory changed')

    def test_snapshot_error(self, tmp_path, capsys, monkeypatch):
        root, _, _ = _segmentation_projects(tmp_path / 'projects')
        calls = []

        def snapshot(path):
            calls.append(path)
            if len(calls) == 2:
                raise OSError('gone')
            return []
        monkeypatch.setattr(segmentation_check, 'snapshot', snapshot)
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[0] == \
            'FAIL project-1: project snapshot raised OSError'

    def test_snapshot(self, tmp_path):
        (tmp_path / 'sub').mkdir()
        (tmp_path / 'sub' / 'b').write_text('xy')
        (tmp_path / 'a').write_text('z')
        names = [(name, size) for name, size, _ in segmentation_check.snapshot(str(tmp_path))]
        assert names == [('a', 1), (os.path.join('sub', 'b'), 2)]

    def test_no_calls_ratio(self):
        r = dict.fromkeys(segmentation_check.COUNTS, 0)
        assert segmentation_check.counts_line(r) == (
            'segments 0 -> 0, calls 0 -> 0 (ratio -), passthrough 0, headings merged 0 of 0')

    def test_covers(self):
        from types import SimpleNamespace as N
        blocks = [N(fingerprint='a'), N(fingerprint='b'), N(fingerprint='c')]

        def seg(start, end, prints):
            return N(block_start=start, block_end=end, fingerprints=prints)
        covers = segmentation_check.covers
        assert covers(blocks, [seg(0, 2, ['a', 'b']), seg(2, 3, ['c'])])
        assert not covers(blocks, [seg(0, 2, ['a', 'b'])])                       # end missing
        assert not covers(blocks, [seg(0, 2, ['a', 'b']), seg(1, 3, ['b', 'c'])])  # overlap
        assert not covers(blocks, [seg(1, 3, ['b', 'c'])])                       # start missing
        assert not covers(blocks, [seg(0, 2, ['a', 'x']), seg(2, 3, ['c'])])     # fingerprint
        assert not covers(blocks, [seg(0, 0, []), seg(0, 3, ['a', 'b', 'c'])])   # empty range
        assert covers([], [])

    @pytest.mark.parametrize('where', ['repository', 'projects'])
    def test_refuses_temporary_directory_inside(self, tmp_path, capsys, monkeypatch, where):
        root, _, beta = _segmentation_projects(tmp_path / 'projects')
        (tmp_path / 'checkout' / '.git').mkdir(parents=True)
        (tmp_path / 'checkout' / 'src').mkdir()
        place = str(tmp_path / 'checkout' / 'src') if where == 'repository' else beta
        monkeypatch.setattr(segmentation_check.tempfile, 'gettempdir', lambda: place)
        assert self._main(root) == 2
        what = 'a git work tree' if where == 'repository' else 'the projects directory'
        assert capsys.readouterr().out == \
            f'ERROR the temporary directory would be inside {what}\n'
        assert not any(n.startswith('phase6-segmentation-') for n in os.listdir(place))

    def test_missing_directory(self, tmp_path, capsys):
        assert self._main(tmp_path / 'nope') == 2
        assert capsys.readouterr().out == (
            'ERROR cannot list projects directory <projects-dir> (FileNotFoundError)\n')
        assert self._main(tmp_path / 'nope', anonymize=False) == 2
        assert str(tmp_path / 'nope') in capsys.readouterr().out

    def test_temporary_directory_cannot_be_removed(self, tmp_path, capsys, monkeypatch):
        root, _, _ = _segmentation_projects(tmp_path / 'projects')
        made = []
        real = segmentation_check.tempfile.mkdtemp
        monkeypatch.setattr(segmentation_check.tempfile, 'mkdtemp',
                            lambda **kw: made.append(real(**kw)) or made[-1])
        real_rmtree = shutil.rmtree

        def rmtree(path):
            raise OSError(16, 'busy', '/MARKER')
        monkeypatch.setattr(segmentation_check.shutil, 'rmtree', rmtree)
        try:
            assert self._main(root) == 1
        finally:
            real_rmtree(made[0])
        out = capsys.readouterr().out
        assert out.splitlines() == SEGMENTATION_LINES + [
            'ERROR cannot remove the temporary directory (OSError)']
        assert 'MARKER' not in out

    def test_error_outside_a_project(self, tmp_path, capsys, monkeypatch):
        root, _, _ = _segmentation_projects(tmp_path / 'projects')

        def run(args, names, work):
            raise RuntimeError('/MARKER')
        monkeypatch.setattr(segmentation_check, 'run', run)
        assert self._main(root) == 1
        assert capsys.readouterr().out == 'ERROR the check stopped (RuntimeError)\n'

    def test_help(self, tmp_path):
        result = _run_script('phase6_segmentation_check', ['--help'], str(tmp_path))
        assert result.returncode == 0
        assert 'Phase 6 check: segmentation rules' in result.stdout

    def test_script_run(self, tmp_path):
        root, _, _ = _segmentation_projects(tmp_path / 'projects')
        result = _run_script('phase6_segmentation_check',
                             ['--projects-dir', str(root), '--anonymize'], str(tmp_path))
        assert result.returncode == 0
        assert result.stdout.splitlines() == SEGMENTATION_LINES


class TestSegmentationCheckSurvivors:
    """Mutation testing, Phase 6 run 1: the check script in-process."""

    def _main(self, root):
        return segmentation_check.main(['--projects-dir', str(root), '--anonymize'])

    def test_help_in_process(self, capsys):
        with pytest.raises(SystemExit):
            segmentation_check.main(['--help'])
        out = ' '.join(capsys.readouterr().out.split())
        assert ('Phase 6 check: segmentation rules 1 (before Phase 6) and 2 (new projects) '
                'on the real books.') in out
        assert ('--projects-dir PROJECTS_DIR Directory that holds the project folders '
                '(default: projects)') in out
        assert '--anonymize Print project-N labels; omit names and paths' in out
        assert 'For every project' not in out  # only the docstring's first line

    def test_default_projects_directory_listed(self, tmp_path, capsys, monkeypatch):
        _segmentation_projects(tmp_path / 'projects')
        monkeypatch.chdir(tmp_path)
        listed = []
        real = segmentation_check.os.listdir

        def listdir(path):
            listed.append(path)
            return real(path)
        monkeypatch.setattr(segmentation_check.os, 'listdir', listdir)
        assert segmentation_check.main(['--anonymize']) == 0
        assert listed == ['projects']

    def test_work_directory_under_the_resolved_temporary_directory(self, tmp_path, capsys,
                                                                   monkeypatch):
        root, _, _ = _segmentation_projects(tmp_path / 'projects')
        real_tmp = tmp_path / 'real-tmp'
        real_tmp.mkdir()
        link = tmp_path / 'tmp-link'
        link.symlink_to(real_tmp)
        made = []
        real = segmentation_check.tempfile.mkdtemp

        def mkdtemp(**kwargs):
            made.append(real(**kwargs))
            return made[-1]
        monkeypatch.setattr(segmentation_check.tempfile, 'gettempdir', lambda: str(link))
        monkeypatch.setattr(segmentation_check.tempfile, 'mkdtemp', mkdtemp)
        assert self._main(root) == 0
        assert os.path.dirname(made[0]) == os.path.realpath(str(real_tmp))

    def test_looks_for_dot_git(self, tmp_path, monkeypatch):
        looked = []
        real = segmentation_check.os.path.lexists

        def lexists(path):
            looked.append(os.path.basename(path))
            return real(path)
        monkeypatch.setattr(segmentation_check.os.path, 'lexists', lexists)
        segmentation_check.in_work_tree(str(tmp_path))
        assert looked and set(looked) == {'.git'}

    def test_every_uncovered_segmentation_counted(self, tmp_path, capsys, monkeypatch):
        root, _, _ = _segmentation_projects(tmp_path / 'projects')
        monkeypatch.setattr(segmentation_check, 'covers', lambda blocks, segs: False)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        # alpha: two chapters, each cut twice
        assert out[0].endswith('; 4 segmentations do not cover their chapter exactly')

    def test_first_snapshot_fails(self, tmp_path, capsys, monkeypatch):
        root, _, _ = _segmentation_projects(tmp_path / 'projects')
        calls = []

        def snapshot(path):
            calls.append(path)
            if len(calls) == 1:
                raise OSError('gone')
            return []
        monkeypatch.setattr(segmentation_check, 'snapshot', snapshot)
        assert self._main(root) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[0] == 'FAIL project-1: check raised OSError'
        assert out[1].startswith('PASS project-2: ')  # the loop goes on after a failure

    def test_two_failing_projects_with_two_problems(self, tmp_path, capsys, monkeypatch):
        root, _, _ = _segmentation_projects(tmp_path / 'projects')

        def broken(epub):
            raise ValueError('no')

        def snapshot(path):
            snapshot.calls += 1
            if snapshot.calls % 2 == 0:
                raise OSError('gone')
            return []
        snapshot.calls = 0
        monkeypatch.setattr(segmentation_check, 'check_source', broken)
        monkeypatch.setattr(segmentation_check, 'snapshot', snapshot)
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines() == [
            'FAIL project-1: check raised ValueError; project snapshot raised OSError',
            'FAIL project-2: check raised ValueError; project snapshot raised OSError',
            'TOTAL projects 2  segments 0 -> 0, calls 0 -> 0 (ratio -), passthrough 0, '
            'headings merged 0 of 0  failed projects 2']

    def test_counted_project_with_two_problems(self, tmp_path, capsys, monkeypatch):
        root, _, _ = _segmentation_projects(tmp_path / 'projects')
        real = segmentation_check.check_source

        def check(epub):
            r, problems = real(epub)
            return r, problems + ['one', 'two']
        monkeypatch.setattr(segmentation_check, 'check_source', check)
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[1] == (
            'FAIL project-2: segments 1 -> 1, calls 1 -> 1 (ratio 1.000), passthrough 0, '
            'headings merged 0 of 0; one; two')

    def test_project_file_read_as_utf8(self, tmp_path, monkeypatch):
        _, _, beta = _segmentation_projects(tmp_path / 'projects')
        opened = []
        real = open

        def recording(path, mode='r', **kwargs):
            opened.append((os.path.basename(path), mode, kwargs))
            return real(path, mode, **kwargs)
        monkeypatch.setattr(segmentation_check, 'open', recording, raising=False)
        segmentation_check.source_of(beta)
        assert opened == [('project.json', 'r', {'encoding': 'utf-8'})]


class TestSegmentationCheckRound1:
    """Review round 1: unreadable chapters, per-segment fingerprints, errors."""

    def _book_project(self, root, chapters):
        from tests.test_exporter import _project, _raw_book
        root.mkdir()
        books = root.parent / 'books'
        books.mkdir()
        epub = _raw_book(books, chapters, nav=None, ncx=None)
        return _project(root, epub, {}, name='alpha')

    def _main(self, root):
        return segmentation_check.main(['--projects-dir', str(root), '--anonymize'])

    def test_partly_unreadable_book_fails(self, tmp_path, capsys):
        from tests.conftest import xhtml
        root = tmp_path / 'projects'
        project_dir = self._book_project(root, {'a': ('a.xhtml', xhtml('<p>One.</p>'))})
        # a second spine chapter that is not well-formed, and one missing
        import zipfile
        epub = segmentation_check.source_of(project_dir)
        with zipfile.ZipFile(epub) as z:
            members = {n: z.read(n) for n in z.namelist()}
        opf = members['OEBPS/content.opf'].decode()
        opf = opf.replace('</manifest>', '<item id="b" href="Text/b.xhtml" '
                          'media-type="application/xhtml+xml"/><item id="c" href="Text/c.xhtml" '
                          'media-type="application/xhtml+xml"/></manifest>')
        opf = opf.replace('</spine>', '<itemref idref="b"/><itemref idref="c"/></spine>')
        members['OEBPS/content.opf'] = opf.encode()
        members['OEBPS/Text/b.xhtml'] = b'<html><body><p>broken</div></body></html>'
        with zipfile.ZipFile(epub, 'w') as z:
            for name, data in members.items():
                z.writestr(name, data)
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[0] == (
            'FAIL project-1: segments 1 -> 1, calls 1 -> 1 (ratio 1.000), passthrough 0, '
            'headings merged 0 of 0; 2 chapters could not be checked')

    def test_wholly_unreadable_book_fails(self, tmp_path, capsys):
        root = tmp_path / 'projects'
        self._book_project(root, {'a': ('a.xhtml', '<html><body><p>x</div></body></html>')})
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[0] == (
            'FAIL project-1: segments 0 -> 0, calls 0 -> 0 (ratio -), passthrough 0, '
            'headings merged 0 of 0; 1 chapters could not be checked; no chapter checked')

    def test_book_without_chapters_fails(self, tmp_path, capsys):
        from tests.conftest import xhtml
        root = tmp_path / 'projects'
        self._book_project(root, {'a': ('a.xhtml', xhtml(''))})  # no text: no chapter
        assert self._main(root) == 1
        assert capsys.readouterr().out.splitlines()[0].endswith('; no chapter checked')

    def test_covers_compares_each_segment_with_its_blocks(self):
        from types import SimpleNamespace as N
        blocks = [N(fingerprint='a'), N(fingerprint='b'), N(fingerprint='c')]
        segs = [N(block_start=0, block_end=1, fingerprints=['a', 'b']),
                N(block_start=1, block_end=3, fingerprints=['c'])]
        assert not segmentation_check.covers(blocks, segs)
        segs = [N(block_start=0, block_end=1, fingerprints=['a']),
                N(block_start=1, block_end=3, fingerprints=['b', 'c'])]
        assert segmentation_check.covers(blocks, segs)

    def test_value_error_of_the_path_check(self, tmp_path, capsys, monkeypatch):
        root, _, _ = _segmentation_projects(tmp_path / 'projects')

        def commonpath(paths):
            raise ValueError('/MARKER mixes absolute and relative')
        monkeypatch.setattr(segmentation_check.os.path, 'commonpath', commonpath)
        assert self._main(root) == 1
        out, err = capsys.readouterr()
        assert out == 'ERROR the check stopped (ValueError)\n' and err == ''


class TestValidateCheckShebang:
    """Phase 6 review round 2: translated code whose source starts with a shebang."""

    def test_counted(self, tmp_path, capsys):
        root, project_dir = _validate_projects(tmp_path / 'projects')
        pm = validate_check.ProjectManager(project_dir)
        pm.load()
        segs = {s.segment_id: s for s in pm.data.segments}
        segs['c_seg0003'].text = '\n\n  #!/bin/sh\n# note\nx = 1'  # blank lines, indented: counted
        segs['d_seg0002'].text = '#!/usr/bin/env python\n' + segs['d_seg0002'].text
        segs['c_seg0001'].text = '#! not code'                   # a text segment: not counted
        pm.save()
        assert validate_check.main(['--projects-dir', str(root), '--anonymize']) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[1].endswith('translated code starting with a shebang 2')
        assert out[2].endswith('translated code starting with a shebang 2  failed projects 0')

    def test_starts_with_shebang(self):
        f = validate_check.starts_with_shebang
        assert f('#!/bin/sh') and f('\n  \n#!x\ny')
        assert f(' #!x') and f('\n    #!/bin/sh\n    ls')  # round 3: indented
        assert not f('') and not f('x\n#!y') and not f('# !x')


class TestSegmentationCheckRound2:
    """Review round 2: spine documents the extractor skips silently."""

    def _book(self, tmp_path, chapters):
        from tests.test_exporter import _project, _raw_book
        root = tmp_path / 'projects'
        root.mkdir()
        books = tmp_path / 'books'
        books.mkdir()
        _project(root, _raw_book(books, chapters, nav=None, ncx=None), {}, name='alpha')
        return root

    def test_bodyless_document_fails(self, tmp_path, capsys):
        from tests.conftest import xhtml
        root = self._book(tmp_path, {'a': ('a.xhtml', xhtml('<p>One.</p>')),
                                     'b': ('b.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml"/>'),
                                     'c': ('c.xhtml', '<html/>')})
        assert segmentation_check.main(['--projects-dir', str(root), '--anonymize']) == 1
        assert capsys.readouterr().out.splitlines()[0] == (
            'FAIL project-1: segments 1 -> 1, calls 1 -> 1 (ratio 1.000), passthrough 0, '
            'headings merged 0 of 0; 2 spine documents without a body')

    def test_legitimate_skips_pass(self, tmp_path, capsys):
        from tests.conftest import RAW_NAV, xhtml
        from tests.test_exporter import _project, _raw_book
        root = tmp_path / 'projects'
        root.mkdir()
        books = tmp_path / 'books'
        books.mkdir()
        # the navigation document is in the spine; the cover's body has no text
        epub = _raw_book(books, {'a': ('a.xhtml', xhtml('<p>One.</p>')),
                                 'cover': ('cover.xhtml', xhtml('<img src="c.png" alt=""/>'))},
                         nav=RAW_NAV, ncx=None)
        _project(root, epub, {}, name='alpha')
        assert segmentation_check.main(['--projects-dir', str(root), '--anonymize']) == 0
        assert capsys.readouterr().out.splitlines()[0].startswith('PASS project-1: ')

    def test_without_body_counts_only_unseen_documents(self, tmp_path):
        from types import SimpleNamespace as N
        from tests.conftest import xhtml
        root = self._book(tmp_path, {'a': ('a.xhtml', '<html/>'), 'b': ('b.xhtml', '<html/>'),
                                     'c': ('c.xhtml', xhtml(''))})
        epub = segmentation_check.source_of(os.path.join(root, 'alpha'))
        ex = segmentation_check.EPUBExtractor(epub)
        ex.load()
        assert ex.extract_chapters() == []
        assert segmentation_check.without_body(ex, []) == 2
        assert segmentation_check.without_body(ex, [N(id='a')]) == 1
        ex.failed = [('b', 'x')]
        assert segmentation_check.without_body(ex, []) == 1
        ex.spine[0].is_nav = True
        assert segmentation_check.without_body(ex, []) == 0


# --- Phase 7: throughput and CLI overhead measurements ----------------------------

throughput = _load('phase7_throughput')
overhead = _load('phase7_cli_overhead')


def _invented_projects(projects_dir, n=4):
    """Two invented projects (a, b); b has n text segments and one code
    segment, the first one already translated."""
    from src.core.project import ProjectManager
    from src.core.segmenter import Segment, SegmentType
    for name in ('a', 'b'):
        segments = [Segment(id=f'c1_seg{i:04d}', html=f'<p>Line {i}.</p>', text=f'Line {i}.',
                            word_count=2, segment_type=SegmentType.TEXT)
                    for i in range(1, n + 1)]
        segments.insert(1, Segment(id='c1_code', html='<pre># c\nx</pre>', text='# c\nx',
                                   word_count=2, segment_type=SegmentType.CODE))
        pm = ProjectManager(os.path.join(projects_dir, name))
        pm.create('/nowhere/book.epub', 'Invented', 'Nobody', segments)
        pm.mark_translated('c1_seg0001', '<p>舊譯</p>')
        pm.save()


class TestThroughputScript:

    def _respond(self, calls):
        from src.llm import LLMError, TransientError
        from tests.conftest import like

        def respond(request):
            content = request.messages[0].content
            calls.append(content)
            if '>Line 2.<' in content and len([c for c in calls if '>Line 2.<' in c]) == 1:
                raise TransientError('exit 1')
            if '>Line 3.<' in content:
                raise LLMError('bad request')
            return like(request, '譯')
        return respond

    def test_measures_a_copy(self, tmp_path, capsys, monkeypatch):
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        projects = tmp_path / 'projects'
        _invented_projects(str(projects))
        made = _record_mkdtemp(monkeypatch)
        before = throughput.snapshot(str(projects))
        calls = []
        with use_fake_backend(FakeBackend(responder=self._respond(calls))):
            code = throughput.main(['--projects-dir', str(projects), '--project-index', '2',
                                    '--segments', '3', '--model', 'fake-model',
                                    '--workers', '2', '--anonymize'])
        out = capsys.readouterr().out
        assert code == 0, out
        lines = out.splitlines()
        assert lines[0].startswith('project-2: segments 3  wall ')
        assert 'seconds per segment ' in lines[0] and 'segments per minute ' in lines[0]
        assert lines[1].startswith('calls 4  per call median ')
        assert lines[2:] == ['status failed 1, translated 2', 'failures by code error 1',
                             'needed a retry 1  needed a split 0']
        assert 'Line' not in out and 'c1_seg' not in out
        # the first 3 text segments only (the translated one again), not segment 4
        assert sum('>Line 4.<' in c for c in calls) == 0
        assert sum('>Line 1.<' in c for c in calls) == 1
        assert throughput.snapshot(str(projects)) == before
        # its own copy is gone (not "no such directory at all": another run may have one)
        assert len(made) == 1 and not os.path.exists(made[0])
        from src.llm import default_backend
        assert default_backend().name == 'claude-cli'  # replaced names restored

    def test_named_without_anonymize_and_backend_option(self, tmp_path, capsys, monkeypatch):
        import src.llm as llm
        from src.llm.fake import FakeBackend
        from tests.conftest import like
        projects = tmp_path / 'projects'
        _invented_projects(str(projects))
        fake = FakeBackend(responder=lambda r: like(r, '譯'))
        monkeypatch.setitem(llm.BACKENDS, 'copilot-cli', lambda: fake)
        code = throughput.main(['--projects-dir', str(projects), '--project-index', '1',
                                '--segments', '2', '--model', 'fake-model',
                                '--backend', 'copilot-cli'])
        out = capsys.readouterr().out
        assert code == 0, out
        assert out.startswith('a: segments 2  ')
        assert 'status translated 2' in out and 'failures by code none' in out
        assert len(fake.requests) == 2

    @pytest.mark.parametrize('args, message', [
        (['--project-index', '3'], 'ERROR no project 3 (there are 2)'),
        (['--project-index', '0'], 'ERROR no project 0 (there are 2)'),
        (['--project-index', '2', '--segments', '9'], 'ERROR project-2 has only 4 such segments'),
    ])
    def test_refused(self, tmp_path, capsys, args, message):
        projects = tmp_path / 'projects'
        _invented_projects(str(projects))
        code = throughput.main(['--projects-dir', str(projects), '--anonymize'] + args)
        assert code == 2
        assert capsys.readouterr().out.strip() == message

    def test_refused_inside_a_work_tree_or_the_projects(self, tmp_path, capsys, monkeypatch):
        projects = tmp_path / 'projects'
        _invented_projects(str(projects))
        args = ['--projects-dir', str(projects), '--project-index', '1']
        tree = tmp_path / 'tree'
        (tree / '.git').mkdir(parents=True)  # a work tree (the test may run in a copy)
        monkeypatch.setattr(throughput.tempfile, 'gettempdir', lambda: str(tree))
        assert throughput.main(args) == 2
        assert 'inside a git work tree' in capsys.readouterr().out
        monkeypatch.setattr(throughput.tempfile, 'gettempdir', lambda: str(projects))
        assert throughput.main(args) == 2
        assert 'inside the projects directory' in capsys.readouterr().out
        assert throughput.main(['--projects-dir', str(tmp_path / 'none'),
                                '--project-index', '1']) == 2

    def test_segment_stats(self):
        calls = {'a': [(1, 'p', 1.0)], 'b': [(1, 'p', 1.0), (3, 'p', 1.0)],
                 'c': [(1, 'p', 1.0), (3, 'p', 1.0), (1, 'h1', 1.0), (1, 'h2', 1.0)],
                 'd': [(1, 'p', 1.0), (1, 'p', 1.0)]}
        assert throughput.segment_stats(calls, ['a', 'b', 'c', 'd', 'e']) == \
            {'retried': 3, 'split': 1}


class TestCliOverheadScript:

    def _fake_run(self, config_dir, payload, returncode=0, write_transcript=True):
        seen = []

        def run(cmd, **kwargs):
            seen.append((cmd, kwargs))
            if write_transcript and '--no-session-persistence' not in cmd:
                folder = overhead.transcript_dir(kwargs['cwd'])
                os.makedirs(folder, exist_ok=True)
                with open(os.path.join(folder, f'{len(seen)}.jsonl'), 'w') as f:
                    f.write('{}')
            from src.llm.cli import ProcessResult
            return ProcessResult(returncode, json.dumps(payload), '')
        return run, seen

    PAYLOAD = {'result': 'x', 'is_error': False, 'duration_api_ms': 1500, 'total_cost_usd': 0.0003,
               'usage': {'input_tokens': 10, 'cache_creation_input_tokens': 20,
                         'cache_read_input_tokens': 300, 'output_tokens': 40}}

    def test_run(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'config'))
        monkeypatch.setenv('CLAUDECODE', '1')
        run, seen = self._fake_run(tmp_path, self.PAYLOAD)
        monkeypatch.setattr(overhead, 'run_process', run)
        code = overhead.main(['--repetitions', '2', '--raw', '--only', 'base', 'all'])
        out = capsys.readouterr().out
        assert code == 0
        assert len(seen) == 4
        base_cmd = seen[0][0]
        assert base_cmd[:2] == ['claude', '-p'] and base_cmd[3:] == [
            '--model', 'haiku', '--output-format', 'json']
        assert seen[1][0][7:] == ['--system-prompt', overhead.SYSTEM_PROMPT, '--tools', '',
                                  '--strict-mcp-config', '--setting-sources', '',
                                  '--no-session-persistence']
        assert 'CLAUDECODE' not in seen[0][1]['env']
        assert seen[0][1]['timeout'] == 120
        assert 'robot' in base_cmd[2] and '術語表' in base_cmd[2]  # the engine's prompt
        assert 'in      330  cache_read      300  out    40  cost $0.00030' in out
        assert 'api   1.50 s' in out
        assert 'transcripts 2/2' in out.splitlines()[-2]
        assert 'transcripts 0/2' in out.splitlines()[-1]
        assert '  1 base ' in out and 'transcript 1' in out

    def test_failed_calls(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'config'))
        run, _ = self._fake_run(tmp_path, {'is_error': True}, write_transcript=False)
        monkeypatch.setattr(overhead, 'run_process', run)
        assert overhead.main(['--repetitions', '1', '--only', 'base', '--raw']) == 1
        out = capsys.readouterr().out
        assert 'FAILED no usage in the output' in out and 'n=1 failed=1' in out

        def broken(cmd, **kwargs):
            raise OSError('x')

        monkeypatch.setattr(overhead, 'run_process', broken)
        assert overhead.one_call([], 'p', 'haiku', 5)['error'] == 'OSError'

        from src.llm.cli import ProcessResult
        monkeypatch.setattr(overhead, 'run_process', lambda cmd, **kw: ProcessResult(1, 'no', ''))
        assert overhead.one_call([], 'p', 'haiku', 5)['error'] == 'exit 1'

    def test_transcript_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path))
        assert overhead.transcript_dir('/a/b.c') == os.path.join(str(tmp_path), 'projects',
                                                                 '-a-b-c')
        monkeypatch.delenv('CLAUDE_CONFIG_DIR')
        assert overhead.transcript_dir('/x').startswith(os.path.expanduser('~/.claude'))
        assert overhead.transcripts(str(tmp_path / 'missing')) == set()


class TestCliOverheadScriptSurvivors:
    """Mutation survivors of the overhead script (Phase 7 run 1)."""

    PAYLOAD = TestCliOverheadScript.PAYLOAD

    def _clock(self, monkeypatch, *times):
        ticks = iter(times)
        # the script's own `time`: the process-wide clock stays real for other threads
        monkeypatch.setattr(overhead, 'time', SimpleNamespace(monotonic=lambda: next(ticks)))

    def test_call_values(self, monkeypatch):
        from src.llm.cli import ProcessResult
        monkeypatch.setattr(overhead, 'run_process',
                            lambda cmd, **kw: ProcessResult(0, json.dumps(self.PAYLOAD), ''))
        self._clock(monkeypatch, 10.0, 12.5)
        assert overhead._call([], 'p', 'haiku', 5) == {
            'ok': True, 'wall': 2.5, 'api': 1.5, 'in': 330, 'cache_read': 300, 'out': 40,
            'cost': 0.0003, 'error': 'is_error'}

    def test_call_failures(self, monkeypatch):
        from src.llm.cli import ProcessResult
        results = iter([ProcessResult(3, '', ''), ProcessResult(0, 'not json', ''),
                        ProcessResult(0, json.dumps({'usage': {}}), ''),
                        ProcessResult(0, json.dumps(dict(self.PAYLOAD, is_error=True)), '')])
        monkeypatch.setattr(overhead, 'run_process', lambda cmd, **kw: next(results))
        self._clock(monkeypatch, 1.0, 3.0, 1.0, 4.0, 1.0, 5.0, 1.0, 6.0)
        assert overhead._call([], 'p', 'm', 5) == {'ok': False, 'wall': 2.0, 'error': 'exit 3'}
        assert overhead._call([], 'p', 'm', 5) == {'ok': False, 'wall': 3.0,
                                                   'error': 'no usage in the output'}
        assert overhead._call([], 'p', 'm', 5) == {'ok': False, 'wall': 4.0,
                                                   'error': 'no usage in the output'}
        assert overhead._call([], 'p', 'm', 5)['ok'] is False

        def broken(cmd, **kwargs):
            raise OSError('x')
        monkeypatch.setattr(overhead, 'run_process', broken)
        self._clock(monkeypatch, 1.0, 1.25)
        assert overhead._call([], 'p', 'm', 5) == {'ok': False, 'wall': 0.25, 'error': 'OSError'}

    def test_prompt_is_the_engines(self):
        from src.core import validation
        from src.core.segmenter import SegmentType
        from src.core.translator import TranslationEngine
        from src.llm.fake import FakeBackend
        expected = TranslationEngine(backend=FakeBackend())._build_prompt(
            'unused', SegmentType.TEXT, overhead.GLOSSARY, overhead.BEFORE, overhead.AFTER,
            html=validation.protect(overhead.PARAGRAPH).html)
        assert overhead.prompt_text() == expected

    def test_summary_counts_failures(self):
        calls = [{'ok': True, 'wall': 1.0, 'api': 0.5, 'in': 10, 'cache_read': 2, 'out': 3,
                  'cost': 0.1, 'transcript': True},
                 {'ok': False, 'wall': 9.0, 'error': 'exit 1', 'transcript': False}]
        line = overhead.summary_line('base', calls)
        assert line.startswith('base                     n=2 failed=1  wall   1.00 s')
        assert line.endswith('transcripts 1/2')

    def test_transcript_dir_keeps_capitals(self, monkeypatch, tmp_path):
        monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path))
        assert overhead.transcript_dir('/Ab/C') == os.path.join(str(tmp_path), 'projects', '-Ab-C')

    def test_defaults_help_and_header(self, monkeypatch, capsys):
        seen = []

        def one_call(flags, prompt, model, timeout):
            seen.append((model, timeout))
            return {'ok': False, 'wall': 0.0, 'error': 'x', 'transcript': False}

        monkeypatch.setattr(overhead, 'one_call', one_call)
        assert overhead.main([]) == 1
        assert len(seen) == 5 * len(overhead.CONFIGS)
        assert set(seen) == {('haiku', 120)}
        out = capsys.readouterr().out
        assert out.splitlines()[0] == (f'model haiku  repetitions 5  prompt '
                                       f'{len(overhead.prompt_text())} characters (invented paragraph)')
        seen.clear()
        overhead.main(['--timeout', '7.5', '--repetitions', '1', '--only', 'base'])
        assert seen == [('haiku', 7.5)]
        with pytest.raises(SystemExit):
            overhead.main(['--help'])
        help_text = ' '.join(capsys.readouterr().out.split())
        for text in ('Phase 7 measurement: per-call overhead of the Claude CLI with candidate flags.',
                     '--model MODEL Model alias (default: haiku)',
                     '--repetitions REPETITIONS Calls per configuration',
                     '--timeout TIMEOUT Seconds per call', '--raw Also print every call',
                     '--only [ONLY ...] Run only these configurations (names as printed)'):
            assert text in help_text
        assert 'Makes REAL' not in help_text  # the description is the first line only


class TestCliOverheadScriptRound1:
    """Review round 1, item 14: each --raw line is flushed as it is printed."""

    def test_raw_lines_flushed(self, monkeypatch):
        import io

        class Observed(io.StringIO):
            def __init__(self):
                super().__init__()
                self.flushed_at = []

            def flush(self):
                self.flushed_at.append(self.getvalue().count('\n'))
                super().flush()

        stream = Observed()
        monkeypatch.setattr(sys, 'stdout', stream)
        monkeypatch.setattr(overhead, 'one_call', lambda *a: {
            'ok': False, 'wall': 0.0, 'error': 'x', 'transcript': False})
        overhead.main(['--repetitions', '2', '--only', 'base', '--raw'])
        lines = stream.getvalue().splitlines()
        raw = [i + 1 for i, line in enumerate(lines) if line.startswith('  ')]
        assert raw == [2, 3]
        assert all(n in stream.flushed_at for n in raw)  # flushed right after each


class TestThroughputScriptRound1:
    """Review round 1, item 12."""

    def _args(self, projects, *extra):
        return ['--projects-dir', str(projects), '--project-index', '2', '--segments', '2',
                '--model', 'fake-model', '--anonymize', *extra]

    def test_interrupt_prints_only_the_type(self, tmp_path, capsys, monkeypatch):
        projects = tmp_path / 'projects'
        _invented_projects(str(projects))

        def interrupt(*a, **kw):
            raise KeyboardInterrupt

        monkeypatch.setattr(throughput, 'run', interrupt)
        made = _record_mkdtemp(monkeypatch)
        assert throughput.main(self._args(projects)) == 1
        assert capsys.readouterr().out == 'ERROR the measurement stopped (KeyboardInterrupt)\n'
        assert len(made) == 1 and not os.path.exists(made[0])

    def test_cleanup_failure_reported_without_a_path(self, tmp_path, capsys, monkeypatch):
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        projects = tmp_path / 'projects'
        _invented_projects(str(projects))
        made = []
        real_mkdtemp = throughput.tempfile.mkdtemp
        monkeypatch.setattr(throughput.tempfile, 'mkdtemp',
                            lambda **kw: made.append(real_mkdtemp(**kw)) or made[-1])

        real_rmtree = shutil.rmtree

        def broken(path):
            raise OSError('busy')

        monkeypatch.setattr(throughput.shutil, 'rmtree', broken)
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))):
            code = throughput.main(self._args(projects))
        out = capsys.readouterr().out
        assert code == 1
        assert out.splitlines()[-1] == 'ERROR cannot remove the temporary directory (OSError)'
        assert made[0] not in out
        real_rmtree(made[0])

    def test_mkdtemp_failure_inside_the_try(self, tmp_path, capsys, monkeypatch):
        projects = tmp_path / 'projects'
        _invented_projects(str(projects))

        def fail(**kw):
            raise OSError('no space')

        monkeypatch.setattr(throughput.tempfile, 'mkdtemp', fail)
        assert throughput.main(self._args(projects)) == 1
        assert capsys.readouterr().out == 'ERROR the measurement stopped (OSError)\n'

    def test_any_change_under_the_projects_directory_fails(self, tmp_path, capsys, monkeypatch):
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        projects = tmp_path / 'projects'
        _invented_projects(str(projects))

        def respond(request):
            with open(os.path.join(str(projects), 'a', 'note.txt'), 'w') as f:
                f.write('x')  # another project changes during the run
            return like(request, '譯')

        with use_fake_backend(FakeBackend(responder=respond)):
            code = throughput.main(self._args(projects))
        assert code == 1
        assert capsys.readouterr().out.splitlines()[-1] == \
            'FAIL project-2: files in the projects directory changed'

    def test_backend_of_the_copy(self, tmp_path, capsys, monkeypatch):
        import src.llm as llm
        from src.core.project import ProjectManager
        from src.llm.fake import FakeBackend
        from tests.conftest import like
        projects = tmp_path / 'projects'
        _invented_projects(str(projects))
        pm = ProjectManager(os.path.join(str(projects), 'b'))
        pm.load()
        pm.set_model('fake-model', backend='copilot-cli')
        pm.save()
        fake = FakeBackend(responder=lambda r: like(r, '譯'))
        monkeypatch.setitem(llm.BACKENDS, 'copilot-cli', lambda: fake)
        assert throughput.main(self._args(projects)) == 0, capsys.readouterr().out
        assert len(fake.requests) == 2

    def test_setup_output_suppressed(self, tmp_path, capsys, monkeypatch):
        projects = tmp_path / 'projects'
        _invented_projects(str(projects))
        real_prepare = throughput.prepare

        def noisy(pm, count, statuses):
            print('SEGMENT TEXT')
            return real_prepare(pm, count, statuses)

        monkeypatch.setattr(throughput, 'prepare', noisy)
        args = self._args(projects)
        args[args.index('--segments') + 1] = '9'  # too few: stops after the setup
        assert throughput.main(args) == 2
        assert 'SEGMENT TEXT' not in capsys.readouterr().out


class TestThroughputScriptRound1Survivors:

    def test_default_model_is_haiku(self, monkeypatch):
        seen = []
        monkeypatch.setattr(throughput, 'measure', lambda args: seen.append(args.model) or 0)
        assert throughput.main(['--projects-dir', 'p', '--project-index', '1']) == 0
        assert seen == ['haiku']

    def test_temporary_directory_in_a_work_tree(self, tmp_path, capsys, monkeypatch):
        projects = tmp_path / 'projects'
        _invented_projects(str(projects))
        tree = tmp_path / 'tree'
        (tree / '.git').mkdir(parents=True)
        monkeypatch.setattr(throughput.tempfile, 'gettempdir', lambda: str(tree))
        assert throughput.main(['--projects-dir', str(projects), '--project-index', '2',
                                '--segments', '2', '--anonymize']) == 2
        assert capsys.readouterr().out == \
            'ERROR the temporary directory would be inside a git work tree\n'


class TestThroughputScriptRound2:

    def test_failing_final_snapshot_still_removes_the_copy(self, tmp_path, capsys, monkeypatch):
        """Item 7."""
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        projects = tmp_path / 'projects'
        _invented_projects(str(projects))
        made, calls = [], []
        real_mkdtemp = throughput.tempfile.mkdtemp
        monkeypatch.setattr(throughput.tempfile, 'mkdtemp',
                            lambda **kw: made.append(real_mkdtemp(**kw)) or made[-1])
        real_snapshot = throughput.snapshot

        def snapshot(path):
            calls.append(path)
            if len(calls) == 2:
                raise FileNotFoundError('vanished')
            return real_snapshot(path)

        monkeypatch.setattr(throughput, 'snapshot', snapshot)
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))):
            code = throughput.main(['--projects-dir', str(projects), '--project-index', '2',
                                    '--segments', '2', '--model', 'fake-model', '--anonymize'])
        assert code == 1
        assert capsys.readouterr().out.splitlines()[-1] == \
            'ERROR the measurement stopped (FileNotFoundError)'
        assert len(made) == 1 and not os.path.exists(made[0])


def _record_mkdtemp(monkeypatch):
    """The temporary directories the throughput script makes."""
    made = []
    real = throughput.tempfile.mkdtemp
    monkeypatch.setattr(throughput.tempfile, 'mkdtemp',
                        lambda **kw: made.append(real(**kw)) or made[-1])
    return made


class TestThroughputScriptRound4:
    """Review round 4: the mutants that earlier runs counted as killed only
    because the measurement test failed when another mutmut worker had a
    phase7-throughput-* directory in the shared temporary directory."""

    def _args(self, projects, *extra):
        return ['--projects-dir', str(projects), '--project-index', '2', '--segments', '2',
                '--model', 'fake-model', '--anonymize', *extra]

    def test_help(self, capsys, monkeypatch):
        monkeypatch.setenv('COLUMNS', '300')
        with pytest.raises(SystemExit):
            throughput.main(['--help'])
        out = capsys.readouterr().out
        assert out.split('\n\n')[1] == throughput.__doc__.split('\n')[0]
        for text in ('Directory of the project folders',
                     '1-based position among the directories holding a project.json',
                     'Segments to translate (default 50)', 'Model (default: haiku)',
                     'Parallel workers (default 1)',
                     "Backend name (default: the project's); not with a --code of main",
                     'Checkout whose code runs (default: this one); a checkout of main '
                     'measures the behaviour before Phase 7',
                     'Print project-N, no names or paths'):
            assert f' {text}\n' in out  # the whole help text, nothing around it

    @pytest.mark.parametrize('argv', [['--project-index', '1'], ['--projects-dir', 'p']])
    def test_required(self, argv, capsys):
        with pytest.raises(SystemExit) as info:
            throughput.main(argv)
        assert info.value.code == 2

    def test_defaults_and_types(self, monkeypatch):
        seen = []
        monkeypatch.setattr(throughput, 'measure', lambda args: seen.append(vars(args)) or 0)
        throughput.main(['--projects-dir', 'p', '--project-index', '1'])
        throughput.main(['--projects-dir', 'p', '--project-index', '3', '--segments', '7',
                         '--workers', '2'])
        assert seen[0] == {'projects_dir': 'p', 'project_index': 1, 'segments': 50,
                           'model': 'haiku', 'workers': 1, 'backend': None,
                           'code': throughput.HERE, 'anonymize': False}
        assert (seen[1]['project_index'], seen[1]['segments'], seen[1]['workers']) == (3, 7, 2)

    def test_report_lines(self):
        from collections import Counter
        calls = {'a': [(1, 'd1', 1.0), (3, 'd1', 2.0)], 'b': [(1, 'd2', 0.5), (1, 'd3', 0.5)]}
        lines = throughput.report_lines('project-2', 30.0, ['a', 'b'],
                                        {'a': 'translated', 'b': 'failed'}, calls,
                                        Counter({'tag_count': 1, 'error': 2}))
        assert lines == [
            'project-2: segments 2  wall 30.0 s  seconds per segment 15.00  segments per minute 2.00',
            'calls 4  per call median 0.75 s  total 4.0 s',
            'status failed 1, translated 1',
            'failures by code error 2, tag_count 1',
            'needed a retry 2  needed a split 1',
        ]
        assert throughput.report_lines('p', 0.0, ['a'], {'a': 'failed'}, {}, Counter()) == [
            'p: segments 1  wall 0.0 s  seconds per segment 0.00  segments per minute 0.00',
            'calls 0  per call median 0.00 s  total 0.0 s',
            'status failed 1',
            'failures by code none',
            'needed a retry 0  needed a split 0',
        ]
        assert throughput.report_lines('p', 60.0, ['a'], {'a': 'failed'}, {}, Counter())[0] \
            == 'p: segments 1  wall 60.0 s  seconds per segment 60.00  segments per minute 0.00'
        assert throughput.report_lines('p', 0.5, ['a'], {'a': 'translated'}, {}, Counter())[0] \
            == 'p: segments 1  wall 0.5 s  seconds per segment 0.50  segments per minute 120.00'

    def test_prepare(self):
        from types import SimpleNamespace
        from src.core.project import SegmentStatus

        def seg(sid, status, kind='text', **kw):
            return SimpleNamespace(segment_id=sid, status=status, segment_type=kind,
                                   translated_text='t', translated_at='x', issues=['i'],
                                   model='m', usage='u', **kw)

        segments = [seg('pt', SegmentStatus.TRANSLATED, passthrough=True),
                    seg('code', SegmentStatus.PENDING, kind='code'),
                    seg('old', SegmentStatus.TRANSLATED),  # no passthrough attribute
                    seg('new', SegmentStatus.PENDING, passthrough=False),
                    seg('rest', SegmentStatus.PENDING, passthrough=False)]
        pm = SimpleNamespace(data=SimpleNamespace(segments=segments))
        assert throughput.prepare(pm, 2, SegmentStatus) == ['old', 'new']
        for s in segments[2:4]:
            assert (s.status, s.translated_text, s.translated_at, s.issues, s.model, s.usage) \
                == (SegmentStatus.PENDING, '', '', [], '', None)
        assert segments[0].status == SegmentStatus.TRANSLATED
        assert segments[1].status == SegmentStatus.FAILED  # pending, not chosen
        assert segments[4].status == SegmentStatus.FAILED

    def test_snapshot_and_work_tree(self, tmp_path):
        (tmp_path / 'd').mkdir()
        (tmp_path / 'd' / 'f.txt').write_text('abc')
        st = os.stat(tmp_path / 'd' / 'f.txt')
        assert throughput.snapshot(str(tmp_path)) == [
            (os.path.join('d', 'f.txt'), 3, st.st_mtime_ns)]
        (tmp_path / 'repo' / '.git').mkdir(parents=True)
        (tmp_path / 'repo' / 'a' / 'b').mkdir(parents=True)
        assert throughput.in_work_tree(str(tmp_path / 'repo' / 'a' / 'b'))
        assert not throughput.in_work_tree(str(tmp_path / 'd'))

    def test_recorder_and_counting_backend(self):
        from src.llm import LLMRequest, Message
        from src.llm.fake import FakeBackend
        import threading
        recorder = throughput.Recorder()
        fake = FakeBackend(['x', 'y'])
        seen = []
        real_complete = fake.complete
        fake.complete = lambda request, cancel: seen.append(cancel) or real_complete(request, cancel)
        counting = throughput.CountingBackend(fake, recorder)
        assert (counting.name, counting.capabilities, counting.default_model,
                counting.auth_hint) == (fake.name, fake.capabilities, fake.default_model,
                                        fake.auth_hint)
        cancel = threading.Event()
        for text in ('one', 'two'):
            counting.complete(LLMRequest(system='', messages=[Message('user', text)],
                                         model='m', timeout=1), cancel)
        assert seen == [cancel, cancel]
        (n1, d1, s1), (n2, d2, s2) = recorder.calls[None]
        assert n1 == n2 == 1 and d1 != d2 and 0 <= s1 < 60 and 0 <= s2 < 60

    def test_errors_without_a_path(self, tmp_path, capsys, monkeypatch):
        not_a_dir = tmp_path / 'file'
        not_a_dir.write_text('')
        assert throughput.main(['--projects-dir', str(not_a_dir), '--project-index', '1']) == 2
        assert capsys.readouterr().out == \
            'ERROR cannot list the projects directory (NotADirectoryError)\n'
        projects = tmp_path / 'projects'
        _invented_projects(str(projects))
        monkeypatch.setattr(throughput.tempfile, 'gettempdir', lambda: str(projects / 'a'))
        assert throughput.main(self._args(projects)) == 2
        assert capsys.readouterr().out == \
            'ERROR the temporary directory would be inside the projects directory\n'

    def test_measurement_details(self, tmp_path, capsys, monkeypatch):
        """The temporary directory's place and name, the timing (a fake
        clock), the logging handler removed, the counted calls through the
        copy's backend, warnings not counted as failures, the keyword arguments
        passed through to translate_segment."""
        import logging
        import src.llm as llm
        from src.core.project import ProjectManager
        from src.llm.fake import FakeBackend
        from tests.conftest import like
        projects = tmp_path / 'projects'
        _invented_projects(str(projects))
        pm = ProjectManager(os.path.join(str(projects), 'b'))
        pm.load()
        pm.set_model('fake-model', backend='copilot-cli')
        pm.save()
        kwargs = []
        real_mkdtemp = throughput.tempfile.mkdtemp
        monkeypatch.setattr(throughput.tempfile, 'mkdtemp',
                            lambda **kw: kwargs.append(kw) or real_mkdtemp(**kw))

        class Clock:
            now = 100.0

            def monotonic(self):
                self.now += 1
                return self.now

        monkeypatch.setattr(throughput, 'time', Clock())
        prompts = []

        def respond(request):
            prompts.append(request.messages[0].content)
            if '>Line 2.<' in request.messages[0].content:
                return like(request, 'Line 2.')  # untranslated: a warning, not a failure
            return like(request, '譯')

        fake = FakeBackend(responder=respond)
        monkeypatch.setitem(llm.BACKENDS, 'copilot-cli', lambda: fake)
        handlers = list(logging.getLogger().handlers)
        import translate
        seen_kwargs = []
        real_translate_segment = translate.translate_segment
        monkeypatch.setattr(translate, 'translate_segment', lambda engine, seg, *a, **kw: (
            seen_kwargs.append(kw), real_translate_segment(engine, seg, *a, **kw))[1])
        assert throughput.main(self._args(projects)) == 0
        out = capsys.readouterr().out.splitlines()
        assert kwargs == [{'prefix': 'phase7-throughput-',
                           'dir': os.path.realpath(throughput.tempfile.gettempdir())}]
        assert out[0].startswith('project-2: segments 2  wall 5.0 s  ')  # 2 calls, 2 ticks each
        assert out[1] == 'calls 2  per call median 1.00 s  total 2.0 s'
        assert out[2:4] == ['status translated 2', 'failures by code none']
        assert logging.getLogger().handlers == handlers
        assert seen_kwargs and all('translations' in kw for kw in seen_kwargs)  # passed through

    def test_backend_option_and_unfinished_segments(self, tmp_path, capsys, monkeypatch):
        """--backend reaches the run; segments left pending: exit 1."""
        import src.llm as llm
        from src.llm import AuthError
        from src.llm.fake import FakeBackend
        projects = tmp_path / 'projects'
        _invented_projects(str(projects))

        def respond(request):
            raise AuthError('expired')  # the run stops, the segments stay pending

        fake = FakeBackend(responder=respond)
        monkeypatch.setitem(llm.BACKENDS, 'copilot-cli', lambda: fake)
        assert throughput.main(self._args(projects, '--backend', 'copilot-cli')) == 1
        assert len(fake.requests) == 1
        assert capsys.readouterr().out.splitlines()[2] == 'status pending 2'


# --- Phase 8 ------------------------------------------------------------------------

extraction = _load('phase8_extraction_compare')
brief_sample = _load('phase8_brief_sample')

_WORDS = ' '.join(['lantern'] * 49)


def _phase8_projects(projects_dir, n=12):
    """Two invented projects (a, b), each with n text segments of 50 words,
    a shorter one after the first and then a code segment."""
    from src.core.project import ProjectManager
    from src.core.segmenter import Segment, SegmentType
    for name in ('a', 'b'):
        segments = [Segment(id=f'c1_seg{i:04d}', html=f'<p>Line{i} {_WORDS}.</p>',
                            text=f'Line{i} {_WORDS}.', word_count=50, segment_type=SegmentType.TEXT)
                    for i in range(1, n + 1)]
        segments.insert(1, Segment(id='c1_segS', html='<p>Short line.</p>', text='Short line.',
                                   word_count=49, segment_type=SegmentType.TEXT))
        segments.insert(2, Segment(id='c1_segC', html='<pre># c\nx</pre>', text='# c\nx',
                                   word_count=2, segment_type=SegmentType.CODE))
        ProjectManager(os.path.join(projects_dir, name)).create(
            '/nowhere/book.epub', 'Invented', 'Nobody', segments)


def _mkdtemps(module, monkeypatch):
    made = []
    real = module.tempfile.mkdtemp
    monkeypatch.setattr(module.tempfile, 'mkdtemp',
                        lambda **kw: made.append(real(**kw)) or made[-1])
    return made


def _extract_responder(calls):
    """Batch i proposes Orrin as 奧林 (odd i) or 歐林 (even i), and Line<i>;
    the consolidation picks 歐倫."""
    def respond(request):
        prompt = request.messages[0].content
        calls.append(prompt)
        if prompt.startswith('你是一位專業的術語編輯'):
            return '[{"term": "Orrin", "translation": "歐倫", "category": "person"}]'
        i = int(prompt.split('文本：\nLine', 1)[1].split(' ', 1)[0])
        return json.dumps([{'term': 'Orrin', 'translation': '奧林' if i % 2 else '歐林',
                            'category': 'person'},
                           {'term': f'Line{i}', 'translation': f'行{i}', 'category': 'term'}],
                          ensure_ascii=False)
    return respond


class TestExtractionCompareScript:

    def _args(self, projects, out, *extra):
        return ['--projects-dir', str(projects), '--project-index', '2', '--out', str(out),
                '--model', 'fake-model', '--anonymize', *extra]

    def test_compares_on_a_copy(self, tmp_path, capsys, monkeypatch):
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        monkeypatch.setattr('src.glossary.term_extractor.MAX_CHARS_PER_BATCH', 450)
        projects, out = tmp_path / 'projects', tmp_path / 'out'
        _phase8_projects(str(projects), n=4)
        made = _mkdtemps(extraction, monkeypatch)
        before = extraction.snapshot(str(projects))
        calls = []
        fake = FakeBackend(responder=_extract_responder(calls))
        with use_fake_backend(fake):
            code = extraction.main(self._args(projects, out, '--chunks', '3'))
        printed = capsys.readouterr().out
        assert code == 0, printed
        assert printed.splitlines() == [
            'project-2 after: batches sent 3  calls 4  proposals 6  terms 4  '
            'terms with conflicting translations 1',
            'wrote terms-after.json']
        assert {r.model for r in fake.requests} == {'fake-model'}
        # the first 3 batches only (the short segment joins batch 1; code is left out)
        assert sum(c.startswith('你是一位專業的術語提取專家') for c in calls) == 3
        assert 'Short line.' in calls[0] and '# c' not in ''.join(calls)
        with open(out / 'terms-after.json', encoding='utf-8') as f:
            written = json.load(f)
        assert written == {
            'terms': [{'term': 'Line1', 'translation': '行1', 'category': 'term'},
                      {'term': 'Line2', 'translation': '行2', 'category': 'term'},
                      {'term': 'Line3', 'translation': '行3', 'category': 'term'},
                      {'term': 'Orrin', 'translation': '歐倫', 'category': 'person'}],
            'conflicts': {'Orrin': {'奧林': 2, '歐林': 1}}}
        for secret in ('Orrin', '奧林', 'Line', 'Invented', 'b:'):
            assert secret not in printed
        assert extraction.snapshot(str(projects)) == before
        assert len(made) == 1 and not os.path.exists(made[0])

    def test_named_before_for_other_code_and_by_name(self, tmp_path, capsys):
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        projects, out = tmp_path / 'projects', tmp_path / 'out'
        _phase8_projects(str(projects), n=2)
        other = tmp_path / 'checkout'
        other.mkdir()
        with use_fake_backend(FakeBackend(responder=lambda r: '[{"term": "Orrin", "translation": "奧林"}]')):
            # src is imported already, from this checkout: not the code of --code
            assert extraction.main(['--projects-dir', str(projects), '--project-index', '1',
                                    '--out', str(out), '--code', str(other)]) == 2
            assert capsys.readouterr().out == \
                'ERROR the code that was loaded is not the one of --code\n'
            assert not out.exists()
            assert extraction.main(['--projects-dir', str(projects), '--project-index', '1',
                                    '--out', str(out), '--name', 'before']) == 0
            assert capsys.readouterr().out.splitlines() == [
                'a before: batches sent 1  calls 1  proposals 1  terms 1  '
                'terms with conflicting translations 0', 'wrote terms-before.json']
        assert json.loads((out / 'terms-before.json').read_text()) == {
            'terms': [{'term': 'Orrin', 'translation': '奧林', 'category': ''}], 'conflicts': {}}

    def test_stopped_extraction_writes_what_it_has(self, tmp_path, capsys, monkeypatch):
        from src.llm import RateLimitError
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        monkeypatch.setattr('src.glossary.term_extractor.MAX_CHARS_PER_BATCH', 400)
        projects, out = tmp_path / 'projects', tmp_path / 'out'
        _phase8_projects(str(projects), n=3)
        fake = FakeBackend(['[{"term": "Orrin", "translation": "奧林"}]', RateLimitError('limit')])
        with use_fake_backend(fake):
            code = extraction.main(self._args(projects, out))
        assert code == 1
        assert capsys.readouterr().out.splitlines() == [
            'project-2 after: batches sent 1  calls 2  proposals 1  terms 1  '
            'terms with conflicting translations 0', 'wrote terms-after.json',
            'FAIL project-2: the extraction stopped (ExtractionError)']
        assert json.loads((out / 'terms-after.json').read_text())['terms'] == [
            {'term': 'Orrin', 'translation': '奧林', 'category': ''}]

    def test_migrates_a_schema_1_copy(self, tmp_path, capsys, monkeypatch):
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        projects, out = tmp_path / 'projects', tmp_path / 'out'
        _phase8_projects(str(projects), n=2)
        migrated = []

        def migrate(path):
            migrated.append(os.path.basename(path))
            from src.core.project import ProjectManager
            pm = ProjectManager(path)
            pm.load()
            pm.data.schema = 2
            pm.save()

        monkeypatch.setattr('src.core.migrate.migrate_project', migrate)
        file = projects / 'b' / 'project.json'
        raw = json.loads(file.read_text())
        raw['schema'] = 1
        file.write_text(json.dumps(raw))
        before = extraction.snapshot(str(projects))
        with use_fake_backend(FakeBackend(responder=lambda r: '[{"term": "Orrin", "translation": "奧林"}]')):
            assert extraction.main(self._args(projects, out)) == 0
        assert migrated == ['2']  # the copy, named by its index
        assert extraction.snapshot(str(projects)) == before

    @pytest.mark.parametrize('args, message', [
        (['--project-index', '3'], 'ERROR no project 3 (there are 2)'),
        (['--project-index', '0'], 'ERROR no project 0 (there are 2)'),
    ])
    def test_refused(self, tmp_path, capsys, args, message):
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=1)
        code = extraction.main(['--projects-dir', str(projects), '--out', str(tmp_path / 'o')]
                               + args)
        assert (code, capsys.readouterr().out.strip()) == (2, message)

    def test_refused_places(self, tmp_path, capsys, monkeypatch):
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=1)
        tree = tmp_path / 'tree'
        (tree / '.git').mkdir(parents=True)
        base = ['--projects-dir', str(projects), '--project-index', '1']
        assert extraction.main(base + ['--out', str(tree / 'o')]) == 2
        assert capsys.readouterr().out == 'ERROR --out would be inside a git work tree\n'
        assert extraction.main(base + ['--out', str(projects / 'o')]) == 2
        assert capsys.readouterr().out == 'ERROR --out would be inside the projects directory\n'
        assert not (projects / 'o').exists()
        out = ['--out', str(tmp_path / 'o')]
        monkeypatch.setattr(extraction.tempfile, 'gettempdir', lambda: str(tree))
        assert extraction.main(base + out) == 2
        assert capsys.readouterr().out == \
            'ERROR the temporary directory would be inside a git work tree\n'
        monkeypatch.setattr(extraction.tempfile, 'gettempdir', lambda: str(projects))
        assert extraction.main(base + out) == 2
        assert capsys.readouterr().out == \
            'ERROR the temporary directory would be inside the projects directory\n'
        assert extraction.main(['--projects-dir', str(tmp_path / 'none'),
                                '--project-index', '1'] + out) == 2
        assert capsys.readouterr().out == \
            'ERROR cannot list the projects directory (FileNotFoundError)\n'

    def test_interrupt_removes_the_copy(self, tmp_path, capsys, monkeypatch):
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=1)
        made = _mkdtemps(extraction, monkeypatch)

        def interrupt(*a, **kw):
            assert os.path.isdir(made[0])
            raise KeyboardInterrupt

        monkeypatch.setattr(extraction, 'run', interrupt)
        assert extraction.main(self._args(projects, tmp_path / 'o')) == 1
        assert capsys.readouterr().out == 'ERROR the comparison stopped (KeyboardInterrupt)\n'
        assert len(made) == 1 and not os.path.exists(made[0])

    def test_cleanup_failure_and_changed_projects(self, tmp_path, capsys, monkeypatch):
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=1)
        made = _mkdtemps(extraction, monkeypatch)

        def touch(args, label, project_dir, *a):
            with open(os.path.join(project_dir, 'extra'), 'w') as f:
                f.write('x')
            return 0

        monkeypatch.setattr(extraction, 'run', touch)
        monkeypatch.setattr(extraction.shutil, 'rmtree',
                            lambda path: (_ for _ in ()).throw(OSError('busy')))
        assert extraction.main(self._args(projects, tmp_path / 'o')) == 1
        assert capsys.readouterr().out.splitlines() == [
            'ERROR cannot remove the temporary directory (OSError)',
            'FAIL project-2: files in the projects directory changed']
        monkeypatch.undo()
        shutil.rmtree(made[0])

    def test_conflicts(self):
        proposals = [[{'term': 'Orrin', 'translation': '奧林'}, {'term': 'Tess', 'translation': '泰絲'}],
                     [{'term': 'orrin', 'translation': '歐林'}, {'term': 'Tess', 'translation': '泰絲'}],
                     [{'term': 5, 'translation': 'x'}, {'term': 'Tess', 'translation': None},
                      {'translation': 'y'}]]
        assert extraction.conflicts(proposals) == {'Orrin': {'奧林': 1, '歐林': 1}}

    def test_help_and_defaults(self, capsys, monkeypatch):
        monkeypatch.setenv('COLUMNS', '300')
        with pytest.raises(SystemExit):
            extraction.main(['--help'])
        out = capsys.readouterr().out
        assert out.split('\n\n')[1] == extraction.__doc__.split('\n')[0]
        for text in ('Directory of the project folders',
                     '1-based position among the directories holding a project.json',
                     'Directory for the term list', 'Model (default: haiku)',
                     'Send only the first N extraction batches (default: all)',
                     'Checkout whose code runs (default: this one); a checkout of main gives '
                     'the extraction before Phase 8',
                     'Name of the term list (default: before for another --code, else after)',
                     'Print project-N, no names or paths'):
            assert f' {text}\n' in out
        seen = {}
        monkeypatch.setattr(extraction, 'compare', lambda args: seen.update(vars(args)) or 0)
        assert extraction.main(['--projects-dir', 'p', '--project-index', '1', '--out', 'o']) == 0
        assert seen == {'projects_dir': 'p', 'project_index': 1, 'out': 'o', 'model': 'haiku',
                        'chunks': None, 'code': extraction.HERE, 'name': 'after',
                        'anonymize': False}

    @pytest.mark.parametrize('argv', [['--project-index', '1', '--out', 'o'],
                                      ['--projects-dir', 'p', '--out', 'o'],
                                      ['--projects-dir', 'p', '--project-index', '1'],
                                      ['--projects-dir', 'p', '--project-index', '1', '--out', 'o',
                                       '--name', 'other']])
    def test_required(self, argv, capsys):
        with pytest.raises(SystemExit) as info:
            extraction.main(argv)
        assert info.value.code == 2

    def test_counting_backend(self):
        from src.llm.fake import FakeBackend
        fake = FakeBackend(['x'])
        counting = extraction.CountingBackend(fake)
        assert (counting.name, counting.default_model, counting.auth_hint,
                counting.capabilities) == (fake.name, fake.default_model, fake.auth_hint,
                                           fake.capabilities)
        assert counting.list_models() == fake.list_models()
        from src.llm import LLMRequest, Message
        import threading
        request = LLMRequest(system='', messages=[Message('user', 'p')], model='m', timeout=1)
        assert counting.complete(request, threading.Event()).text == 'x'
        assert counting.calls == 1


class TestBriefSampleScript:

    def _args(self, tmp_path, projects, *extra):
        brief = tmp_path / 'brief.txt'
        if not brief.exists():
            brief.write_text('  An invented brief: plain tone, general readers.\n',
                             encoding='utf-8')
        return ['--projects-dir', str(projects), '--project-index', '2', '--brief-file',
                str(brief), '--out', str(tmp_path / 'out'), '--model', 'fake-model',
                '--anonymize', *extra]

    def test_samples_with_and_without(self, tmp_path, capsys, monkeypatch):
        from src.llm import LLMError
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=21)
        made = _mkdtemps(brief_sample, monkeypatch)
        before = brief_sample.snapshot(str(projects))

        def respond(request):
            prompt = request.messages[0].content
            briefed = prompt.startswith('本書簡介')
            if '>Line5 ' in prompt and briefed:
                raise LLMError('bad request')
            return like(request, '有簡介譯文' if briefed else '無簡介譯文')

        fake = FakeBackend(responder=respond)
        with use_fake_backend(fake):
            code = brief_sample.main(self._args(tmp_path, projects))
        printed = capsys.readouterr().out
        assert code == 1, printed  # one translation failed: written, but not a full sample
        assert printed.splitlines() == [
            'project-2: sampled 10  calls 20  without brief: translated 10 failed 0  '
            'with brief: translated 9 failed 1',
            'wrote brief-sample.html',
            'FAIL project-2: not every sampled segment has both translations']
        # positions i * 21 // 10 of the 21 long segments: Line1, 3, 5, 7, 9, 11, 13, 15, 17, 19
        sent = [p.split('<p k="1">Line', 1)[1].split(' ', 1)[0] for p in fake.prompts]
        assert sent == [str(n) for n in (1, 1, 3, 3, 5, 5, 7, 7, 9, 9, 11, 11, 13, 13, 15, 15,
                                         17, 17, 19, 19)]
        assert [p.startswith('本書簡介') for p in fake.prompts] == [False, True] * 10
        assert fake.prompts[1].startswith(
            '本書簡介（請依此掌握文體、語氣與讀者；簡介本身不需翻譯）：\n'
            'An invented brief: plain tone, general readers.\n\n')
        assert {r.model for r in fake.requests} == {'fake-model'}
        page = (tmp_path / 'out' / 'brief-sample.html').read_text(encoding='utf-8')
        assert page.count('<td>無簡介譯文</td>') == 10 and page.count('<td>有簡介譯文</td>') == 9
        assert '（翻譯失敗：error）' in page
        assert '<pre>An invented brief: plain tone, general readers.</pre>' in page
        assert '<th colspan="3">c1_seg0005</th></tr><tr><td>Line5 lantern' in page
        for secret in ('Line', 'invented brief', 'Invented', 'lantern', '譯文'):
            assert secret not in printed
        assert brief_sample.snapshot(str(projects)) == before
        assert len(made) == 1 and not os.path.exists(made[0])

    def test_named_and_uses_the_glossary_and_context(self, tmp_path, capsys):
        from src.glossary.glossary_manager import GlossaryManager
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=10)
        GlossaryManager(str(projects / 'b' / 'glossary.json')).add_term(
            'lantern', '燈籠', category='term', note='a note')
        fake = FakeBackend(responder=lambda r: like(r, '譯'))
        args = self._args(tmp_path, projects)
        args.remove('--anonymize')
        with use_fake_backend(fake):
            assert brief_sample.main(args) == 0
        assert capsys.readouterr().out.startswith('b: sampled 10  calls 20  ')
        assert all('lantern → 燈籠 [術語]（備註：a note）' in p for p in fake.prompts)
        assert '後文參考（不需翻譯）：\nShort line.' in fake.prompts[0]

    @pytest.mark.parametrize('n, message', [
        (9, 'ERROR project-2 has fewer than 10 text segments of 50 words or more'),
    ])
    def test_too_few_segments(self, tmp_path, capsys, monkeypatch, n, message):
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=n)
        made = _mkdtemps(brief_sample, monkeypatch)
        assert brief_sample.main(self._args(tmp_path, projects)) == 2
        assert capsys.readouterr().out.strip() == message
        assert not (tmp_path / 'out').exists()
        assert len(made) == 1 and not os.path.exists(made[0])

    def test_pick(self):
        from types import SimpleNamespace as S

        def seg(i, words=50, kind='text', passthrough=False):
            return S(segment_id=i, word_count=words, segment_type=kind, passthrough=passthrough)

        segs = [seg(i) for i in range(10)] + [seg('few', 49), seg('code', 99, 'code'),
                                              seg('pass', 99, passthrough=True)]
        assert [s.segment_id for s in brief_sample.pick(segs)] == list(range(10))
        assert brief_sample.pick(segs[1:]) == []
        assert [s.segment_id for s in brief_sample.pick([seg(i) for i in range(25)])] == \
            [0, 2, 5, 7, 10, 12, 15, 17, 20, 22]
        assert (brief_sample.SAMPLE, brief_sample.MIN_WORDS) == (10, 50)

    def test_stopped_by_a_rate_limit(self, tmp_path, capsys, monkeypatch):
        from src.llm import RateLimitError
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=10)
        made = _mkdtemps(brief_sample, monkeypatch)
        with use_fake_backend(FakeBackend([RateLimitError('limit')])):
            assert brief_sample.main(self._args(tmp_path, projects)) == 1
        assert capsys.readouterr().out == 'ERROR the sample stopped (RateLimitError)\n'
        assert len(made) == 1 and not os.path.exists(made[0])

    def test_refused(self, tmp_path, capsys, monkeypatch):
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=10)
        args = self._args(tmp_path, projects)
        (tmp_path / 'brief.txt').write_text(' \n')
        assert brief_sample.main(args) == 2
        assert capsys.readouterr().out == 'ERROR the brief file is empty\n'
        (tmp_path / 'brief.txt').unlink()
        assert brief_sample.main(args) == 2
        assert capsys.readouterr().out == 'ERROR cannot read the brief file (FileNotFoundError)\n'
        (tmp_path / 'brief.txt').write_text('A brief.')
        for index, message in (('3', 'ERROR no project 3 (there are 2)'),
                               ('0', 'ERROR no project 0 (there are 2)')):
            changed = list(args)
            changed[changed.index('--project-index') + 1] = index
            assert brief_sample.main(changed) == 2
            assert capsys.readouterr().out.strip() == message
        tree = tmp_path / 'tree'
        (tree / '.git').mkdir(parents=True)
        for out, message in ((tree / 'o', 'ERROR --out would be inside a git work tree'),
                             (projects / 'o', 'ERROR --out would be inside the projects directory')):
            changed = list(args)
            changed[changed.index('--out') + 1] = str(out)
            assert brief_sample.main(changed) == 2
            assert capsys.readouterr().out.strip() == message
        for place, message in (
                (tree, 'ERROR the temporary directory would be inside a git work tree'),
                (projects, 'ERROR the temporary directory would be inside the projects directory')):
            monkeypatch.setattr(brief_sample.tempfile, 'gettempdir', lambda p=place: str(p))
            assert brief_sample.main(args) == 2
            assert capsys.readouterr().out.strip() == message
        changed = list(args)
        changed[changed.index('--projects-dir') + 1] = str(tmp_path / 'none')
        assert brief_sample.main(changed) == 2
        assert capsys.readouterr().out == \
            'ERROR cannot list the projects directory (FileNotFoundError)\n'

    def test_cleanup_failure_and_changed_projects(self, tmp_path, capsys, monkeypatch):
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=10)
        made = _mkdtemps(brief_sample, monkeypatch)

        def touch(args, brief, label, project_dir, *a):
            with open(os.path.join(project_dir, 'extra'), 'w') as f:
                f.write('x')
            return 0

        real_rmtree = shutil.rmtree
        monkeypatch.setattr(brief_sample, 'run', touch)
        monkeypatch.setattr(brief_sample.shutil, 'rmtree',
                            lambda path: (_ for _ in ()).throw(OSError('busy')))
        assert brief_sample.main(self._args(tmp_path, projects)) == 1
        assert capsys.readouterr().out.splitlines() == [
            'ERROR cannot remove the temporary directory (OSError)',
            'FAIL project-2: files in the projects directory changed']
        monkeypatch.undo()
        real_rmtree(made[0])

    def test_page(self):
        from types import SimpleNamespace as S
        ok = S(text='<p>譯</p>', issues=[])
        failed = S(text=None, issues=[S(code='tag<count')])
        page = brief_sample.page('a <b> brief', [('c1&s', '<p>Src.</p>', ok, failed)])
        assert page.startswith('<!doctype html>\n<html><head><meta charset="utf-8">')
        assert '<h1>Brief</h1><pre>a &lt;b&gt; brief</pre>\n' in page
        assert '<table><tr><th>原文</th><th>無簡介</th><th>有簡介</th></tr>\n' in page
        assert ('<tr><th colspan="3">c1&amp;s</th></tr><tr><td>Src.</td><td>譯</td>'
                '<td><span class="failed">（翻譯失敗：tag&lt;count）</span></td></tr>\n'
                '</table></body></html>\n') in page

    def test_help_and_defaults(self, capsys, monkeypatch):
        monkeypatch.setenv('COLUMNS', '300')
        with pytest.raises(SystemExit):
            brief_sample.main(['--help'])
        out = capsys.readouterr().out
        assert out.split('\n\n')[1] == brief_sample.__doc__.split('\n')[0]
        for text in ('Directory of the project folders',
                     '1-based position among the directories holding a project.json',
                     'File holding the brief', 'Directory for the side-by-side file',
                     'Model (default: haiku)', 'Print project-N, no names or paths'):
            assert f' {text}\n' in out
        seen = {}
        monkeypatch.setattr(brief_sample, 'sample', lambda args: seen.update(vars(args)) or 0)
        assert brief_sample.main(['--projects-dir', 'p', '--project-index', '1',
                                  '--brief-file', 'b', '--out', 'o']) == 0
        assert seen == {'projects_dir': 'p', 'project_index': 1, 'brief_file': 'b', 'out': 'o',
                        'model': 'haiku', 'anonymize': False}

    def test_counting_backend(self):
        from src.llm import LLMRequest, Message
        from src.llm.fake import FakeBackend
        import threading
        fake = FakeBackend(['x'])
        counting = brief_sample.CountingBackend(fake)
        assert (counting.name, counting.default_model, counting.auth_hint,
                counting.capabilities) == (fake.name, fake.default_model, fake.auth_hint,
                                           fake.capabilities)
        assert counting.list_models() == fake.list_models()
        request = LLMRequest(system='', messages=[Message('user', 'p')], model='m', timeout=1)
        assert counting.complete(request, threading.Event()).text == 'x'
        assert counting.calls == 1


class TestPhase8ScriptSurvivors:
    """Mutants of the first Phase 8 mutation run, for both scripts."""

    def _brief_args(self, tmp_path, projects, *extra, index='2', model='other-model'):
        brief = tmp_path / 'brief.txt'
        brief.write_text('A brief.', encoding='utf-8')
        return ['--projects-dir', str(projects), '--project-index', index, '--brief-file',
                str(brief), '--out', str(tmp_path / 'out'), '--model', model, *extra]

    def _extract_args(self, tmp_path, projects, *extra, index='2'):
        return ['--projects-dir', str(projects), '--project-index', index,
                '--out', str(tmp_path / 'out'), '--model', 'other-model', *extra]

    @pytest.mark.parametrize('module', ['extraction', 'brief'])
    def test_snapshot(self, tmp_path, module):
        script = extraction if module == 'extraction' else brief_sample
        (tmp_path / 'd' / 'sub').mkdir(parents=True)
        (tmp_path / 'd' / 'sub' / 'f.txt').write_text('abc')
        (tmp_path / 'd' / 'g').write_text('x')
        st = os.stat(tmp_path / 'd' / 'sub' / 'f.txt')
        st2 = os.stat(tmp_path / 'd' / 'g')
        assert script.snapshot(str(tmp_path / 'd')) == [
            ('g', 1, st2.st_mtime_ns), (os.path.join('sub', 'f.txt'), 3, st.st_mtime_ns)]

    @pytest.mark.parametrize('module', ['extraction', 'brief'])
    def test_counting_backend_passes_cancel(self, module):
        import threading
        script = extraction if module == 'extraction' else brief_sample
        from src.llm.fake import FakeBackend
        seen = []

        class Inner(FakeBackend):
            def complete(self, request, cancel):
                seen.append(cancel)
                return 'r'

        cancel = threading.Event()
        assert script.CountingBackend(Inner()).complete('req', cancel) == 'r'
        assert seen == [cancel]

    @pytest.mark.parametrize('module', ['extraction', 'brief'])
    def test_temporary_directory_prefix_and_mkdtemp_failure(self, tmp_path, capsys, monkeypatch,
                                                             module):
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        script = extraction if module == 'extraction' else brief_sample
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=10)
        made = _mkdtemps(script, monkeypatch)
        args = (self._extract_args(tmp_path, projects) if module == 'extraction'
                else self._brief_args(tmp_path, projects))
        fake = FakeBackend(responder=lambda r: '[{"term": "Orrin", "translation": "奧林"}]' if module == 'extraction' else like(r, '譯'))
        with use_fake_backend(fake):
            assert script.main(args) == 0
        prefix = 'phase8-extraction-' if module == 'extraction' else 'phase8-brief-'
        assert os.path.basename(made[0]).startswith(prefix)
        capsys.readouterr()

        def broken(**kw):
            raise OSError('full')

        monkeypatch.setattr(script.tempfile, 'mkdtemp', broken)
        assert script.main(args) == 1
        word = 'comparison' if module == 'extraction' else 'sample'
        assert capsys.readouterr().out == f'ERROR the {word} stopped (OSError)\n'

    @pytest.mark.parametrize('module', ['extraction', 'brief'])
    def test_cleanup_failure_alone_fails(self, tmp_path, capsys, monkeypatch, module):
        script = extraction if module == 'extraction' else brief_sample
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=10)
        made = _mkdtemps(script, monkeypatch)
        monkeypatch.setattr(script, 'run', lambda *a: 0)
        monkeypatch.setattr(script.shutil, 'rmtree',
                            lambda path: (_ for _ in ()).throw(OSError('busy')))
        args = (self._extract_args(tmp_path, projects) if module == 'extraction'
                else self._brief_args(tmp_path, projects))
        assert script.main(args) == 1
        assert capsys.readouterr().out == 'ERROR cannot remove the temporary directory (OSError)\n'
        monkeypatch.undo()
        shutil.rmtree(made[0])

    @pytest.mark.parametrize('module', ['extraction', 'brief'])
    def test_the_projects_backend_and_quiet_logging(self, tmp_path, capsys, monkeypatch, module):
        import logging
        import src.llm as llm
        from src.core.project import ProjectManager
        from src.llm.fake import FakeBackend
        from tests.conftest import like
        script = extraction if module == 'extraction' else brief_sample
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=10)
        pm = ProjectManager(str(projects / 'a'))
        pm.load()
        pm.set_model('gpt-6.2-sol', backend='copilot-cli')
        pm.save()

        def respond(request):
            logging.getLogger('src.anything').warning('LOGGED-LINE')
            return '[{"term": "Orrin", "translation": "奧林"}]' if module == 'extraction' else like(request, '譯')

        fake = FakeBackend(responder=respond)
        monkeypatch.setitem(llm.BACKENDS, 'copilot-cli', lambda: fake)
        handlers = list(logging.getLogger().handlers)
        args = (self._extract_args(tmp_path, projects, index='1') if module == 'extraction'
                else self._brief_args(tmp_path, projects, index='1'))
        assert script.main(args) == 0
        assert fake.requests and {r.model for r in fake.requests} == {'other-model'}
        captured = capsys.readouterr()
        assert 'LOGGED-LINE' not in captured.out + captured.err
        assert logging.getLogger().handlers == handlers

    @pytest.mark.parametrize('module', ['extraction', 'brief'])
    def test_schema_1_copy_migrated_and_out_exists(self, tmp_path, capsys, monkeypatch, module):
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        script = extraction if module == 'extraction' else brief_sample
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=10)
        migrated = []

        def migrate(path):
            migrated.append(os.path.basename(path))
            from src.core.project import ProjectManager
            pm = ProjectManager(path)
            pm.load()
            pm.data.schema = 2
            pm.save()

        monkeypatch.setattr('src.core.migrate.migrate_project', migrate)
        file = projects / 'b' / 'project.json'
        raw = json.loads(file.read_text())
        raw['schema'] = 1
        file.write_text(json.dumps(raw))
        (tmp_path / 'out').mkdir()  # an existing --out is used
        args = (self._extract_args(tmp_path, projects) if module == 'extraction'
                else self._brief_args(tmp_path, projects))
        fake = FakeBackend(responder=lambda r: '[{"term": "Orrin", "translation": "奧林"}]' if module == 'extraction' else like(r, '譯'))
        with use_fake_backend(fake):
            assert script.main(args) == 0, capsys.readouterr().out
        assert migrated == ['2']

    def test_brief_context_uses_translations_of_segmentation_2(self, tmp_path, capsys):
        from src.core.project import ProjectManager
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=10)
        pm = ProjectManager(str(projects / 'b'))
        pm.load()
        pm.data.segmentation = 2
        pm.mark_translated('c1_segS', '<p>先前的譯文</p>')
        pm.save()
        fake = FakeBackend(responder=lambda r: like(r, '譯'))
        with use_fake_backend(fake):
            assert brief_sample.main(self._brief_args(tmp_path, projects)) == 0
        # sample position 1 is Line2: its context before is the short segment's translation
        # (the code segment between them is stepped over), both times
        second = [p for p in fake.prompts if '<p k="1">Line2 ' in p]
        assert len(second) == 2
        assert all('前文參考（不需翻譯）：\n先前的譯文' in p for p in second)

    def test_brief_index_1_and_required(self, tmp_path, capsys):
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=10)
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))):
            assert brief_sample.main(self._brief_args(tmp_path, projects, index='1')) == 0
        assert capsys.readouterr().out.startswith('a: sampled 10  ')
        full = self._brief_args(tmp_path, projects)
        for option in ('--projects-dir', '--project-index', '--brief-file', '--out'):
            i = full.index(option)
            with pytest.raises(SystemExit) as info:
                brief_sample.main(full[:i] + full[i + 2:])
            assert info.value.code == 2

    def test_brief_page_exact(self):
        from types import SimpleNamespace as S
        page = brief_sample.page('b', [('i', '<p>s</p>', S(text='<p>t</p>'), S(text='<p>u</p>')),
                                       ('j', '<p>v</p>', S(text='<p>w</p>'), S(text='<p>x</p>'))])
        assert page == (
            '<!doctype html>\n<html><head><meta charset="utf-8"><title>Brief sample</title>'
            '<style>table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;'
            'padding:.5em;vertical-align:top;width:33%;white-space:pre-wrap}'
            'th{background:#eee;text-align:left}'
            '.failed{color:#a00}</style></head><body>\n'
            '<h1>Brief</h1><pre>b</pre>\n'
            '<table><tr><th>原文</th><th>無簡介</th><th>有簡介</th></tr>\n'
            '<tr><th colspan="3">i</th></tr><tr><td>s</td><td>t</td>'
            '<td>u</td></tr>\n'
            '<tr><th colspan="3">j</th></tr><tr><td>v</td><td>w</td>'
            '<td>x</td></tr>\n'
            '</table></body></html>\n')

    def test_extraction_file_text_and_name_after(self, tmp_path, capsys):
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=1)
        fake = FakeBackend(responder=lambda r: '[{"term": "Orrin", "translation": "奧林"}]')
        with use_fake_backend(fake):
            assert extraction.main(self._extract_args(tmp_path, projects, '--name', 'after')) == 0
        expected = {'terms': [{'term': 'Orrin', 'translation': '奧林', 'category': ''}],
                    'conflicts': {}}
        text = (tmp_path / 'out' / 'terms-after.json').read_text(encoding='utf-8')
        assert text == json.dumps(expected, ensure_ascii=False, indent=2)

    def test_conflicts_case_folding(self):
        proposals = [[{'term': 'Straße', 'translation': '街'}], [{'term': 'STRASSE', 'translation': '路'}]]
        assert extraction.conflicts(proposals) == {}  # lowercase keys: two different terms


# --- Phase 8 review round 1 ----------------------------------------------------------

def _package():
    """The name of this repository's package (renamed in a mutation run)."""
    import src.glossary.term_extractor as module
    return module.__name__.split('.')[0]


_STUB = {
    '__init__.py': '',
    'llm/__init__.py': '''
class _Backend:
    name, capabilities, default_model, auth_hint = 'stub', None, 'stub-model', ''
def backend_for(name):
    return _Backend()
''',
    'core/__init__.py': '',
    'core/migrate.py': 'def migrate_project(path):\n    raise AssertionError("not schema 1")\n',
    'core/project.py': '''
import json, os
class _Data:
    pass
class _Segment:
    def __init__(self, raw):
        self.text, self.segment_type = raw.get('text', ''), raw.get('segment_type', 'text')
class ProjectManager:
    def __init__(self, path):
        self.path = path
    def load(self):
        with open(os.path.join(self.path, 'project.json'), encoding='utf-8') as f:
            raw = json.load(f)
        self.data = _Data()
        self.data.schema, self.data.backend = 2, None
        self.data.segments = [_Segment(s) for s in raw['segments']]
        return self.data
''',
    'glossary/__init__.py': '',
    'glossary/term_extractor.py': '''
class ExtractionError(Exception):
    terms = []
class TermExtractor:
    def __init__(self, model, backend):
        self.model = model
    def extract_from_text(self, text):
        return [{'term': 'Stub', 'translation': MARK}]
    def extract_from_segments(self, segments):
        self.extract_from_text('x')
        return [{'term': 'Stub', 'translation': MARK, 'category': self.model}]
''',
}


def _stub_checkout(root, mark):
    """A checkout whose term extractor answers `mark` without any call."""
    package = os.path.join(str(root), _package())
    for name, text in _STUB.items():
        path = os.path.join(package, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text.replace('MARK', repr(mark)))
    return str(root)


class TestPhase8Round1Scripts:

    def _extract_args(self, tmp_path, projects, *extra):
        return ['--projects-dir', str(projects), '--project-index', '2',
                '--out', str(tmp_path / 'out'), '--model', 'other-model', '--anonymize', *extra]

    def _brief_args(self, tmp_path, projects, *extra):
        brief = tmp_path / 'brief.txt'
        brief.write_text('A brief.', encoding='utf-8')
        return ['--projects-dir', str(projects), '--project-index', '2', '--brief-file',
                str(brief), '--out', str(tmp_path / 'out'), '--model', 'm', '--anonymize', *extra]

    def test_code_of_two_checkouts_in_fresh_interpreters(self, tmp_path):
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=1)
        script = os.path.join(ROOT, 'scripts', 'verify', 'phase8_extraction_compare.py')
        for mark, name in (('甲', 'before'), ('乙', 'after')):
            code = _stub_checkout(tmp_path / f'code-{name}', mark)
            run = subprocess.run([sys.executable, script, '--name', name, '--code', code]
                                 + self._extract_args(tmp_path, projects),
                                 capture_output=True, text=True, cwd=str(tmp_path), timeout=60)
            assert run.returncode == 0, run.stdout + run.stderr
            assert run.stdout.splitlines()[0] == (
                f'project-2 {name}: batches sent 1  calls 0  proposals 1  terms 1  '
                'terms with conflicting translations 0')
        for mark, name in (('甲', 'before'), ('乙', 'after')):
            written = json.loads((tmp_path / 'out' / f'terms-{name}.json').read_text('utf-8'))
            assert written['terms'] == [{'term': 'Stub', 'translation': mark,
                                         'category': 'other-model'}]
        (tmp_path / 'empty').mkdir()
        run = subprocess.run([sys.executable, script, '--code', str(tmp_path / 'empty')]
                             + self._extract_args(tmp_path, projects),
                             capture_output=True, text=True, cwd=str(tmp_path), timeout=60)
        assert (run.returncode, run.stdout) == (
            2, 'ERROR --code has no term extractor (ModuleNotFoundError)\n')

    def test_code_loaded_from_the_checkout_in_process(self, tmp_path, capsys, monkeypatch):
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=1)
        code = _stub_checkout(tmp_path / 'code', '丙')
        package = _package()
        saved = {k: v for k, v in sys.modules.items() if k == package or k.startswith(package + '.')}
        # another checkout (this one) already first on the path: --code must still win
        monkeypatch.setattr(sys, 'path', [ROOT] + sys.path)
        try:
            for k in saved:
                del sys.modules[k]
            result = extraction.main(['--code', code] + self._extract_args(tmp_path, projects))
        finally:
            for k in [k for k in sys.modules if k == package or k.startswith(package + '.')]:
                del sys.modules[k]
            sys.modules.update(saved)
        assert result == 0, capsys.readouterr().out
        assert os.listdir(tmp_path / 'out') == ['terms-before.json']  # the exact name
        written = json.loads((tmp_path / 'out' / 'terms-before.json').read_text('utf-8'))
        assert written['terms'][0]['translation'] == '丙'

    @pytest.mark.parametrize('module', ['extraction', 'brief'])
    def test_looks_for_dot_git(self, tmp_path, monkeypatch, module):
        script = extraction if module == 'extraction' else brief_sample
        looked = []
        real = script.os.path.lexists

        def lexists(path):
            looked.append(os.path.basename(path))
            return real(path)

        monkeypatch.setattr(script.os.path, 'lexists', lexists)
        script.in_work_tree(str(tmp_path))
        assert looked and set(looked) == {'.git'}

    def test_brief_glossary_file_name(self, tmp_path, capsys, monkeypatch):
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=10)
        paths = []
        real = brief_sample.load_glossary
        monkeypatch.setattr(brief_sample, 'load_glossary',
                            lambda path: paths.append(os.path.basename(path)) or real(path))
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))):
            assert brief_sample.main(self._brief_args(tmp_path, projects)) == 0
        assert paths == ['glossary.json']

    @pytest.mark.parametrize('module', ['extraction', 'brief'])
    def test_files_read_and_written_as_utf8(self, tmp_path, capsys, monkeypatch, module):
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        script = extraction if module == 'extraction' else brief_sample
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=10)
        opened = []
        real = open

        def recording(path, mode='r', **kwargs):
            opened.append((os.path.basename(path), mode, kwargs))
            return real(path, mode, **kwargs)

        monkeypatch.setattr(script, 'open', recording, raising=False)
        if module == 'extraction':
            fake = FakeBackend(responder=lambda r: '[{"term": "Orrin", "translation": "奧林"}]')
            args = self._extract_args(tmp_path, projects)
            expected = [('terms-after.json', 'w', {'encoding': 'utf-8'})]
        else:
            fake = FakeBackend(responder=lambda r: like(r, '譯'))
            args = self._brief_args(tmp_path, projects)
            expected = [('brief.txt', 'r', {'encoding': 'utf-8'}),
                        ('brief-sample.html', 'w', {'encoding': 'utf-8'})]
        with use_fake_backend(fake):
            assert script.main(args) == 0
        assert opened == expected

    @pytest.mark.parametrize('module', ['extraction', 'brief'])
    def test_copy_left_at_schema_1_refused(self, tmp_path, capsys, monkeypatch, module):
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        script = extraction if module == 'extraction' else brief_sample
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=10)
        file = projects / 'b' / 'project.json'
        raw = json.loads(file.read_text())
        raw['schema'] = 1
        file.write_text(json.dumps(raw))
        monkeypatch.setattr('src.core.migrate.migrate_project', lambda path: None)  # unmatched
        made = _mkdtemps(script, monkeypatch)
        fake = FakeBackend()
        args = (self._extract_args(tmp_path, projects) if module == 'extraction'
                else self._brief_args(tmp_path, projects))
        with use_fake_backend(fake):
            assert script.main(args) == 2
        assert capsys.readouterr().out == 'ERROR project-2: the copy could not be migrated\n'
        assert fake.requests == [] and not (tmp_path / 'out').exists()
        assert not os.path.exists(made[0])

    def test_readable(self):
        markup = ('<h2 class="c" id="x">Title &amp; more</h2>\n<p>One <em>two</em><br/>three'
                  '<img src="https://example.invalid/i.png" onerror="alert(1)"/></p>'
                  '<script>alert("x")</script><p>  </p>')
        # one line per top-level block; script removed; attributes never shown
        assert brief_sample.readable(markup) == 'Title &amp; more\nOne two three'  # round 3: a space at <br/>
        assert brief_sample.readable('<p>a</p><li>b</li><blockquote>c</blockquote>d') == 'a\nb\nc\nd'
        assert brief_sample.readable('<p title="a > b">word</p>') == 'word'
        assert brief_sample.readable('<p>one\n  two</p>') == 'one two'
        assert brief_sample.readable('<p>a <b>b</p>') == '&lt;p&gt;a &lt;b&gt;b&lt;/p&gt;'  # unparsable: as it is
        assert brief_sample.readable('&lt;b&gt; x') == '&lt;b&gt; x'  # decoded, then escaped

    def test_conflicts_count_once_per_batch_and_skip_blanks(self):
        proposals = [[{'term': 'Orrin', 'translation': '奧林'}, {'term': 'orrin ', 'translation': ' 奧林'},
                      {'term': 'Orrin', 'translation': ' '}, {'term': ' ', 'translation': '空'}],
                     [{'term': 'Orrin', 'translation': '歐林'}],
                     [{'term': 'Orrin', 'translation': '歐林'}]]
        assert extraction.conflicts(proposals) == {'Orrin': {'奧林': 1, '歐林': 2}}


class TestPhase8Round1Survivors:

    def test_checkout_without_the_extractor_in_process(self, tmp_path, capsys, monkeypatch):
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=1)
        code = _stub_checkout(tmp_path / 'code', '丁')
        shutil.rmtree(os.path.join(code, _package(), 'glossary'))
        package = _package()
        saved = {k: v for k, v in sys.modules.items() if k == package or k.startswith(package + '.')}
        monkeypatch.setattr(sys, 'path', [ROOT] + sys.path)
        try:
            for k in saved:
                del sys.modules[k]
            result = extraction.main(['--code', code, '--projects-dir', str(projects),
                                      '--project-index', '1', '--out', str(tmp_path / 'out')])
        finally:
            for k in [k for k in sys.modules if k == package or k.startswith(package + '.')]:
                del sys.modules[k]
            sys.modules.update(saved)
        assert result == 2
        assert capsys.readouterr().out == \
            'ERROR --code has no term extractor (ModuleNotFoundError)\n'

    def test_conflicts_read_on_after_an_unusable_proposal(self):
        proposals = [[{'term': 5, 'translation': '五'}, {'term': 'Orrin', 'translation': '奧林'}],
                     [{'term': 'Orrin', 'translation': None}, {'term': 'Orrin', 'translation': '歐林'}]]
        assert extraction.conflicts(proposals) == {'Orrin': {'奧林': 1, '歐林': 1}}

    def test_brief_sample_fails_when_a_translation_without_the_brief_failed(
            self, tmp_path, capsys):
        from src.llm import LLMError
        from src.llm.fake import FakeBackend
        from tests.conftest import like, use_fake_backend
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=10)

        def respond(request):
            prompt = request.messages[0].content
            if '>Line3 ' in prompt and not prompt.startswith('本書簡介'):
                raise LLMError('bad request')
            return like(request, '譯')

        brief = tmp_path / 'brief.txt'
        brief.write_text('A brief.', encoding='utf-8')
        with use_fake_backend(FakeBackend(responder=respond)):
            code = brief_sample.main(['--projects-dir', str(projects), '--project-index', '2',
                                      '--brief-file', str(brief), '--out', str(tmp_path / 'o'),
                                      '--anonymize'])
        assert code == 1
        assert capsys.readouterr().out.splitlines()[-1] == \
            'FAIL project-2: not every sampled segment has both translations'


# --- Phase 8 review round 2 ----------------------------------------------------------

class TestPhase8Round2Scripts:

    def test_modules_of_a_checkout_nested_in_code_refused(self, tmp_path, capsys, monkeypatch):
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=1)
        main_code = _stub_checkout(tmp_path / 'main', '甲')
        nested = _stub_checkout(tmp_path / 'main' / '.worktree' / 'branch', '乙')
        package = _package()
        saved = {k: v for k, v in sys.modules.items() if k == package or k.startswith(package + '.')}
        monkeypatch.setattr(sys, 'path', [nested] + sys.path)
        try:
            for k in saved:
                del sys.modules[k]
            __import__(package + '.glossary.term_extractor')  # the branch's, already loaded
            for name in ('llm', 'core.migrate', 'core.project'):
                __import__(f'{package}.{name}')
            result = extraction.main(['--code', main_code, '--projects-dir', str(projects),
                                      '--project-index', '1', '--out', str(tmp_path / 'out')])
        finally:
            for k in [k for k in sys.modules if k == package or k.startswith(package + '.')]:
                del sys.modules[k]
            sys.modules.update(saved)
        assert result == 2
        assert capsys.readouterr().out == \
            'ERROR the code that was loaded is not the one of --code\n'
        assert not (tmp_path / 'out').exists()

    def test_loaded_from(self, tmp_path):
        import types
        module = types.ModuleType('pkg.sub.mod')
        module.__file__ = str(tmp_path / 'pkg' / 'sub' / 'mod.py')
        assert extraction.loaded_from(module, str(tmp_path))
        assert not extraction.loaded_from(module, str(tmp_path / 'pkg'))
        package = types.ModuleType('pkg.sub')
        package.__path__ = []
        package.__file__ = str(tmp_path / 'pkg' / 'sub' / '__init__.py')
        assert extraction.loaded_from(package, str(tmp_path))
        package.__file__ = str(tmp_path / 'pkg' / 'sub.py')
        assert not extraction.loaded_from(package, str(tmp_path))

    def test_no_proposal_at_all_fails(self, tmp_path, capsys, monkeypatch):
        from src.llm import TransientError
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        monkeypatch.setattr('src.glossary.term_extractor.MAX_CHARS_PER_BATCH', 400)
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=2)
        with use_fake_backend(FakeBackend(responder=lambda r: (_ for _ in ()).throw(
                TransientError('timed out')))):
            code = extraction.main(['--projects-dir', str(projects), '--project-index', '2',
                                    '--out', str(tmp_path / 'out'), '--anonymize'])
        assert code == 1
        assert capsys.readouterr().out.splitlines() == [
            'project-2 after: batches sent 3  calls 3  proposals 0  terms 0  '
            'terms with conflicting translations 0', 'wrote terms-after.json',
            'FAIL project-2: no batch proposed a term']

    def test_brief_over_the_limit_refused_before_any_call(self, tmp_path, capsys, monkeypatch):
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=10)
        made = _mkdtemps(brief_sample, monkeypatch)
        brief = tmp_path / 'brief.txt'
        args = ['--projects-dir', str(projects), '--project-index', '2', '--brief-file',
                str(brief), '--out', str(tmp_path / 'out'), '--anonymize']
        fake = FakeBackend()
        brief.write_text(' ' + 'x' * 2001 + '\n', encoding='utf-8')
        with use_fake_backend(fake):
            assert brief_sample.main(args) == 2
        assert capsys.readouterr().out == 'ERROR the brief is longer than 2000 characters\n'
        assert fake.requests == [] and made == []
        brief.write_text(' ' + 'x' * 2000 + '\n', encoding='utf-8')  # the limit itself is fine
        from tests.conftest import like
        with use_fake_backend(FakeBackend(responder=lambda r: like(r, '譯'))):
            assert brief_sample.main(args) == 0


class TestPhase8Round3Scripts:

    def test_readable_spaces_at_breaks_and_cells(self):
        assert brief_sample.readable('<p>one<br/>two</p>') == 'one two'
        assert brief_sample.readable('<p>a<BR />b</p>') == 'a b'
        assert brief_sample.readable(
            '<table><tr><th>h</th><td>a</td><td>b</td></tr><tr><TD>c</TD></tr></table><p>n</p>') \
            == 'h a b c\nn'

    def test_no_usable_proposal_fails(self, tmp_path, capsys):
        from src.llm.fake import FakeBackend
        from tests.conftest import use_fake_backend
        projects = tmp_path / 'projects'
        _phase8_projects(str(projects), n=1)
        reply = '[{"term": "marker", "translation": null}, {"term": " ", "translation": "空"}]'
        with use_fake_backend(FakeBackend(responder=lambda r: reply)):
            code = extraction.main(['--projects-dir', str(projects), '--project-index', '2',
                                    '--out', str(tmp_path / 'out'), '--anonymize'])
        assert code == 1
        assert capsys.readouterr().out.splitlines()[-1] == 'FAIL project-2: no batch proposed a term'

    def test_usable(self):
        assert extraction.usable({'term': 'A', 'translation': '甲'})
        for bad in ({'term': 'A', 'translation': ' '}, {'term': '', 'translation': '甲'},
                    {'term': 'A', 'translation': None}, {'translation': '甲'}, {'term': 5, 'translation': '甲'}):
            assert not extraction.usable(bad)


# --- Phase 9: Copilot overhead script --------------------------------------------

copilot_overhead = _load('phase9_copilot_overhead')


def _copilot_run(segments_for, seen, tokens=(90, 20), code=0):
    """run_process stand-in: Copilot's JSON output whose system segments
    are `segments_for(cmd)`."""
    from src.llm.cli import ProcessResult

    def run(cmd, **kwargs):
        seen.append((cmd, kwargs))
        events = [{'type': 'assistant.message', 'data': {'content': '答'}},
                  {'type': 'session.usage_checkpoint', 'data': {
                      'accountingSnapshot': {'modelMetrics': {'m': {'usage': {
                          'inputTokens': tokens[0], 'outputTokens': tokens[1]}}}},
                      'promptCacheBreakState': [{'models': {'m': {
                          'system_segments': segments_for(cmd)}}}]}}]
        return ProcessResult(code, '\n'.join(json.dumps(e) for e in events), '')
    return run


def _segments_as_copilot(cmd):
    """What the real CLI reports: replaced only with --experimental and the plugin."""
    from src.llm.copilot_cli import PLUGIN_DIR
    loaded = '--experimental' in cmd and cmd[cmd.index('--plugin-dir') + 1] == PLUGIN_DIR \
        if '--plugin-dir' in cmd else False
    if loaded:
        return [{'segment': 'agent_replacement', 'hash': 'h', 'tokens': 14}]
    return [{'segment': f's{i}', 'hash': 'h', 'tokens': 110} for i in range(18)]


class TestPhase9CopilotOverheadScript:

    def test_run_passes(self, monkeypatch, capsys):
        from src.llm.copilot_cli import PLUGIN_DIR, SYSTEM_PROMPT_ENV
        seen = []
        monkeypatch.setattr(copilot_overhead, 'run_process', _copilot_run(_segments_as_copilot, seen))
        assert copilot_overhead.main(['--repetitions', '2', '--raw']) == 0
        out = capsys.readouterr().out.splitlines()
        assert len(seen) == 10
        names = [name for name, _, _ in copilot_overhead.CONFIGS]
        assert names == ['built-in', 'replaced', 'replaced-long', 'no-experimental',
                         'wrong-plugin-dir']
        built_in, replaced, long, no_exp, wrong = (c for c, _ in seen[:5])
        assert replaced[:2] == ['copilot', '-p'] and replaced[3:] == [
            '--model', 'claude-haiku-4.5', '--output-format', 'json', '--available-tools',
            'none', '--no-custom-instructions', '--disable-builtin-mcps', '--no-ask-user',
            '--no-auto-update', '--experimental', '--plugin-dir', PLUGIN_DIR]
        assert built_in == replaced[:-3]
        assert long[3:] == replaced[3:] and len(long[2]) > 4 * len(replaced[2])
        assert no_exp == replaced[:-3] + ['--no-experimental', '--plugin-dir', PLUGIN_DIR]
        assert wrong == replaced[:-1] + [PLUGIN_DIR + '-missing']
        assert 'robot' in replaced[2] and '術語表' in replaced[2] and 'lantern-59' in long[2]
        assert all(kw['env'][SYSTEM_PROMPT_ENV] for _, kw in seen)
        assert all(kw['timeout'] == 120 for _, kw in seen)
        from src.llm.copilot_cli import CopilotCLI
        assert all(kw['cwd'] == CopilotCLI().working_directory() for _, kw in seen)
        assert out[0] == (f'model claude-haiku-4.5  repetitions 2  prompts {len(replaced[2])} '
                          f'and {len(long[2])} characters (invented paragraphs)')
        assert out[1] == ('  1 built-in           pass not replaced wall '
                          + out[1].split('wall ')[1].split(' ')[0]
                          + ' in 90 out 20 system segments 18 (1980 tokens)')
        assert '  1 replaced           pass replaced ' in out[2]
        assert out[2].endswith('system segments 1 (14 tokens)')
        assert out[-5].startswith('built-in           n=2 passed=2  wall ') and 'nan' not in out[-5]
        assert out[-5].endswith('in      90  out    20  system segments   18 ( 1980 tokens)')
        assert out[-4].endswith('system segments    1 (   14 tokens)')

    def test_replacement_missing_fails(self, monkeypatch, capsys):
        seen = []
        never = lambda cmd: _segments_as_copilot([])  # noqa: E731 - never replaced
        monkeypatch.setattr(copilot_overhead, 'run_process', _copilot_run(never, seen))
        assert copilot_overhead.main(['--repetitions', '1', '--raw', '--only', 'replaced']) == 1
        out = capsys.readouterr().out
        assert '  1 replaced           FAIL not replaced ' in out
        assert 'replaced           n=1 passed=0' in out

    def test_broken_setup_not_detected_fails(self, monkeypatch, capsys):
        always = lambda cmd: _segments_as_copilot(['--experimental', '--plugin-dir',  # noqa: E731
                                                   copilot_overhead.copilot_cli.PLUGIN_DIR])
        monkeypatch.setattr(copilot_overhead, 'run_process', _copilot_run(always, []))
        assert copilot_overhead.main(['--repetitions', '1', '--raw', '--only',
                                      'no-experimental']) == 1
        assert '  1 no-experimental    FAIL replaced ' in capsys.readouterr().out

    def test_failed_calls(self, monkeypatch, capsys):
        from src.llm.cli import ProcessResult

        def broken(cmd, **kwargs):
            raise OSError('x')

        monkeypatch.setattr(copilot_overhead, 'run_process', broken)
        assert copilot_overhead.main(['--repetitions', '1', '--raw', '--only', 'replaced']) == 1
        out = capsys.readouterr().out
        assert '  1 replaced           FAIL OSError\n' in out
        assert 'replaced           n=1 passed=0  wall    nan s  in     nan' in out
        monkeypatch.setattr(copilot_overhead, 'run_process',
                            lambda cmd, **kw: ProcessResult(3, 'no', ''))
        call = copilot_overhead.one_call('replaced', True, 'p', 'm', 5)
        assert call['ok'] is False and call['error'] == 'no usage in the output (exit 3, TransientError)'
        # review round 2, D: the class of the result is named (not its text)
        replaced_only = json.dumps({'type': 'session.usage_checkpoint', 'data': {
            'promptCacheBreakState': [{'models': {'m': {'system_segments': [
                {'segment': 'agent_replacement'}]}}}]}})
        for result, found in ((ProcessResult(0, '', ''), 'BackendUnavailableError'),
                              (ProcessResult(0, replaced_only, ''), 'no error')):
            monkeypatch.setattr(copilot_overhead, 'run_process', lambda cmd, r=result, **kw: r)
            call = copilot_overhead.one_call('replaced', True, 'p', 'm', 5)
            assert call == {'ok': False, 'error': f'no usage in the output (exit 0, {found})'}
        # a rate limit beside the usage: neither replaced nor detected
        limit = json.dumps({'type': 'session.error', 'data': {'message': 'rate limit'}})
        run = _copilot_run(_segments_as_copilot, [])
        monkeypatch.setattr(copilot_overhead, 'run_process',
                            lambda cmd, **kw: ProcessResult(0, run(cmd).stdout + '\n' + limit, ''))
        for name, replaced in (('replaced', True), ('built-in', False)):
            call = copilot_overhead.one_call(name, replaced, 'p', 'm', 5)
            assert (call['ok'], call['error']) == (False, 'RateLimitError')
        # another unavailable error (not the replacement's) is no detection
        other = json.dumps({'type': 'session.error', 'data': {
            'message': 'Execution failed: Model "x" is not available.'}})
        monkeypatch.setattr(copilot_overhead, 'run_process',
                            lambda cmd, **kw: ProcessResult(1, run(cmd).stdout + '\n' + other, ''))
        call = copilot_overhead.one_call('built-in', False, 'p', 'm', 5)
        assert (call['ok'], call['error']) == (False, 'BackendUnavailableError')

    def test_call_values(self, monkeypatch):
        ticks = iter([10.0, 12.5])
        monkeypatch.setattr(copilot_overhead, 'time', SimpleNamespace(monotonic=lambda: next(ticks)))
        monkeypatch.setattr(copilot_overhead, 'run_process',
                            _copilot_run(_segments_as_copilot, [], tokens=(4079, 2447)))
        assert copilot_overhead.one_call('replaced', True, 'p', 'm', 5) == {
            'ok': True, 'wall': 2.5, 'in': 4079, 'out': 2447, 'segments': 1,
            'system_tokens': 14, 'error': 'replaced'}

    def test_system_segments(self):
        def checkpoint(*segment_lists):
            return {'type': 'session.usage_checkpoint', 'data': {'promptCacheBreakState': [
                {'models': {f'm{i}': {'system_segments': [{'tokens': t} for t in segments]}
                            for i, segments in enumerate(segment_lists)}}]}}
        assert copilot_overhead.system_segments([]) == (0, 0)
        assert copilot_overhead.system_segments(
            [checkpoint([5, 6]), {'type': 'other'}, checkpoint([1], [7, 8, 9])]) == (3, 24)
        with pytest.raises(KeyError):
            copilot_overhead.system_segments([{'type': 'session.usage_checkpoint', 'data': {}}])

    def test_unreadable_segments_counted_as_none(self, monkeypatch):
        from src.llm.cli import ProcessResult
        text = json.dumps({'type': 'session.usage_checkpoint', 'data': {'accountingSnapshot': {
            'modelMetrics': {'m': {'usage': {'inputTokens': 1, 'outputTokens': 2}}}}}})
        monkeypatch.setattr(copilot_overhead, 'run_process',
                            lambda cmd, **kw: ProcessResult(0, text, ''))
        call = copilot_overhead.one_call('built-in', False, 'p', 'm', 5)
        assert (call['ok'], call['segments'], call['system_tokens']) == (True, 0, 0)

    def test_prompts_are_the_engines(self):
        from src.core import validation
        from src.core.segmenter import SegmentType
        from src.core.translator import TranslationEngine
        from src.llm.fake import FakeBackend
        engine = TranslationEngine(backend=FakeBackend())
        for long, html, glossary in ((False, copilot_overhead.SHORT, copilot_overhead.GLOSSARY_SHORT),
                                     (True, copilot_overhead.LONG, copilot_overhead.GLOSSARY_LONG)):
            assert copilot_overhead.prompt_text(long) == engine._build_prompt(
                'x', SegmentType.TEXT, glossary, copilot_overhead.BEFORE, copilot_overhead.AFTER,
                html=validation.protect(html).html)
        assert copilot_overhead.LONG.count('<p>') == 8
        assert copilot_overhead.GLOSSARY_LONG.splitlines()[-1] == '- lantern-59 → 燈籠59號 [術語]'

    def test_defaults_and_help(self, monkeypatch, capsys):
        seen = []

        def one_call(name, replaced, prompt, model, timeout):
            seen.append((name, replaced, model, timeout))
            return {'ok': True, 'wall': 0.0, 'error': 'x'}

        monkeypatch.setattr(copilot_overhead, 'one_call', one_call)
        assert copilot_overhead.main([]) == 0
        assert len(seen) == 3 * 5
        assert {(m, t) for _, _, m, t in seen} == {('claude-haiku-4.5', 120)}
        assert [(n, r) for n, r, _, _ in seen[:5]] == [
            ('built-in', False), ('replaced', True), ('replaced-long', True),
            ('no-experimental', False), ('wrong-plugin-dir', False)]
        assert capsys.readouterr().out.splitlines()[0].startswith(
            'model claude-haiku-4.5  repetitions 3  prompts ')
        seen.clear()
        copilot_overhead.main(['--timeout', '7.5', '--repetitions', '1', '--only', 'replaced',
                               'built-in', '--model', 'gpt-x'])
        assert seen == [('built-in', False, 'gpt-x', 7.5), ('replaced', True, 'gpt-x', 7.5)]
        capsys.readouterr()
        with pytest.raises(SystemExit):
            copilot_overhead.main(['--help'])
        help_text = ' '.join(capsys.readouterr().out.split())
        assert copilot_overhead.__doc__.split('\n')[0] in help_text
        assert 'Makes REAL' not in help_text
        for line in ('--model MODEL Model (default: claude-haiku-4.5)',
                     '--repetitions REPETITIONS Calls per configuration',
                     '--timeout TIMEOUT Seconds per call', '--raw Also print every call',
                     '--only NAME [NAME ...] Run only these configurations (names as printed)'):
            assert line in help_text


class TestPhase9ScriptRound1:
    """Review round 1, items E and F."""

    def _never(self, monkeypatch):
        seen = []
        monkeypatch.setattr(copilot_overhead, 'one_call', lambda *a: seen.append(a) or {
            'ok': True, 'wall': 0.0, 'error': 'x'})
        return seen

    @pytest.mark.parametrize('args', [['--only'], ['--only', 'replace'],
                                      ['--only', 'built-in', 'replace'], ['--repetitions', '0'],
                                      ['--repetitions', '-1'], ['--repetitions', 'x']])
    def test_invalid_options_refused(self, monkeypatch, capsys, args):
        seen = self._never(monkeypatch)
        with pytest.raises(SystemExit) as info:
            copilot_overhead.main(args)
        assert info.value.code == 2 and seen == []
        assert 'error: argument' in capsys.readouterr().err

    def test_positive(self):
        assert copilot_overhead.positive('1') == 1 and copilot_overhead.positive('7') == 7
        with pytest.raises(Exception) as info:
            copilot_overhead.positive('0')
        assert str(info.value) == 'must be at least 1: 0'

    def test_no_call_is_no_pass(self, monkeypatch):
        seen = self._never(monkeypatch)
        monkeypatch.setattr(copilot_overhead, 'CONFIGS', [])
        assert copilot_overhead.main(['--repetitions', '1']) == 1 and seen == []

    def test_raw_lines_flushed(self, monkeypatch):
        import io

        class Observed(io.StringIO):
            def __init__(self):
                super().__init__()
                self.flushed_at = []

            def flush(self):
                self.flushed_at.append(self.getvalue().count('\n'))
                super().flush()

        stream = Observed()
        monkeypatch.setattr(sys, 'stdout', stream)
        self._never(monkeypatch)
        copilot_overhead.main(['--repetitions', '2', '--only', 'replaced', '--raw'])
        lines = stream.getvalue().splitlines()
        raw = [i + 1 for i, line in enumerate(lines) if line.startswith('  ')]
        assert raw == [2, 3]
        assert all(n in stream.flushed_at for n in raw)  # flushed right after each

    def test_header_names_the_side_effects(self):
        doc = ' '.join(copilot_overhead.__doc__.split())
        assert 'each call is saved as a session in the user\'s Copilot directory' in doc
        assert '`plugin-data/_direct/`' in doc


# --- Phase 10: reasoning effort script --------------------------------------------

effort_check = _load('phase10_effort_check')
# Imported at collection, before conftest's guard replaces it
from src.llm.cli import run_process as real_run_process  # noqa: E402


class UnexpectedProcess(BaseException):
    """A measuring script started a process its test did not stub. A
    BaseException, so the scripts' `except Exception` cannot count it as a
    failed call and go on."""


# The measuring scripts that import run_process by name: the suite's guard
# (conftest.no_real_model_calls) replaces src.llm.cli.run_process, not
# these copies
MEASURING_SCRIPTS = (overhead, copilot_overhead, effort_check)


@pytest.fixture(autouse=True)
def no_unstubbed_script_process(monkeypatch):
    """Every test here starts with the measuring scripts' run_process
    failing; a test that expects calls installs its own stub over it."""
    def unexpected(cmd, *args, **kwargs):
        raise UnexpectedProcess(f'unexpected process in a test: {cmd[:1]}')
    for module in MEASURING_SCRIPTS:
        monkeypatch.setattr(module, 'run_process', unexpected)


class TestNoRealModelProcess:

    @pytest.mark.parametrize('module', MEASURING_SCRIPTS, ids=lambda m: m.__name__)
    def test_scripts_process_stubbed(self, module):
        with pytest.raises(UnexpectedProcess):
            module.run_process(['copilot', '-p', 'x'], timeout=1, cwd='.', env={})

    @pytest.mark.parametrize('program', ['claude', 'copilot'])
    def test_model_programs_are_stand_ins(self, program, tmp_path):
        """Even the real run_process (or a child process) reaches only the
        session's stand-in (conftest.model_programs_replaced)."""
        from tests.conftest import STAND_IN_EXIT
        result = real_run_process([program, '-p', 'x'], timeout=30, cwd=str(tmp_path),
                                  env=dict(os.environ))
        assert result.returncode == STAND_IN_EXIT
        assert f'tests do not run the real {program}' in result.stderr
        child = subprocess.run([program, '--version'], capture_output=True, text=True)
        assert child.returncode == STAND_IN_EXIT


def _claude_json(thinking=7, error=False, reply='<p k="1">譯文</p>'):
    return json.dumps({'type': 'result', 'is_error': error, 'result': reply, 'usage': {
        'input_tokens': 10, 'cache_creation_input_tokens': 200, 'cache_read_input_tokens': 3000,
        'output_tokens': 50, 'output_tokens_details': {'thinking_tokens': thinking}}})


def _copilot_json(level='high', reasoning=30, reply='<p k="1">譯文</p>'):
    events = [{'type': 'session.start', 'data': {'reasoningEffort': level}},
              {'type': 'assistant.message', 'data': {'content': reply}},
              {'type': 'session.usage_checkpoint', 'data': {
                  'accountingSnapshot': {'modelMetrics': {
                      'a': {'usage': {'inputTokens': 100, 'outputTokens': 40,
                                      'reasoningTokens': reasoning}},
                      'b': {'usage': {'inputTokens': 1, 'outputTokens': 2}}}},
                  'promptCacheBreakState': [{'models': {'a': {'system_segments': [
                      {'segment': 'agent_replacement'}]}}}]}}]
    return '\n'.join(json.dumps(e) for e in events)


class TestPhase10EffortScript:

    def _run(self, monkeypatch, answer):
        from src.llm.cli import ProcessResult
        seen = []

        def run(cmd, **kwargs):
            seen.append((cmd, kwargs))
            return ProcessResult(0, answer(cmd), '')
        monkeypatch.setattr(effort_check, 'run_process', run)
        return seen

    def test_claude(self, monkeypatch, capsys):
        from src.llm.claude_cli import ClaudeCLI, LEAN_FLAGS
        monkeypatch.setenv('CLAUDE_CODE_EFFORT_LEVEL', 'max')
        seen = self._run(monkeypatch, lambda cmd: _claude_json())
        assert effort_check.main(['--backend', 'claude-cli', '--model', 'opus']) == 0
        assert [c[c.index('--effort') + 1] for c, _ in seen] == ['low', 'high']
        cmd, kwargs = seen[1]
        assert cmd == ['claude', '-p', cmd[2], '--model', 'opus', '--effort', 'high',
                       *LEAN_FLAGS, '--output-format', 'json']
        assert 'Priya' in cmd[2] and '<p k="1">' in cmd[2]
        assert kwargs['timeout'] == 180 and kwargs['cwd'] == ClaudeCLI().working_directory()
        assert 'CLAUDE_CODE_EFFORT_LEVEL' not in kwargs['env']
        out = capsys.readouterr().out.splitlines()
        assert out[0] == (f'claude-cli opus  levels low high  timeout 180 s  prompt {len(cmd[2])} '
                          'characters (invented paragraph)')
        assert out[2].startswith('claude-cli   opus               high    reported -       wall ')
        assert out[2].endswith('s  in   3210  out     50  reasoning      7  ok')

    def test_copilot(self, monkeypatch, capsys):
        from src.llm.copilot_cli import SYSTEM_PROMPT_ENV
        seen = self._run(monkeypatch, lambda cmd: _copilot_json(cmd[cmd.index('--reasoning-effort') + 1]))
        assert effort_check.main(['--backend', 'copilot-cli', '--model', 'gemini-4-carbon',
                                  '--levels', 'medium', '--timeout', '33']) == 0
        (cmd, kwargs), = seen
        assert cmd[3:7] == ['--model', 'gemini-4-carbon', '--reasoning-effort', 'medium']
        assert '--output-format' in cmd and cmd.count('--output-format') == 1
        assert kwargs['timeout'] == 33 and kwargs['env'][SYSTEM_PROMPT_ENV]
        out = capsys.readouterr().out.splitlines()
        assert out[1].startswith('copilot-cli  gemini-4-carbon    medium  reported medium  wall ')
        assert out[1].endswith('s  in    101  out     42  reasoning     30  ok')

    @pytest.mark.parametrize('args', [
        ['--backend', 'copilot-cli', '--model', 'gemini-4-carbon', '--levels', 'low', 'max'],
        ['--backend', 'copilot-cli', '--model', 'claude-haiku-4.5', '--levels', 'low'],
        ['--backend', 'claude-cli', '--model', 'opus', '--levels', 'none'],
    ])
    def test_refused_level_makes_no_call(self, monkeypatch, capsys, args):
        seen = self._run(monkeypatch, lambda cmd: _claude_json())
        with pytest.raises(SystemExit) as e:
            effort_check.main(args)
        assert e.value.code == 2 and seen == []
        assert 'does not take' in capsys.readouterr().err

    @pytest.mark.parametrize('answer, error', [
        (lambda cmd: _claude_json(error=True), 'FAIL error result'),
        (lambda cmd: _claude_json(reply='  '), 'FAIL empty reply'),
        (lambda cmd: 'not json at all, ' + 'x' * 400, 'FAIL unreadable output'),
    ])
    def test_failed_claude_calls(self, monkeypatch, capsys, answer, error):
        self._run(monkeypatch, answer)
        assert effort_check.main(['--backend', 'claude-cli', '--model', 'haiku',
                                  '--levels', 'low']) == 1
        assert error in capsys.readouterr().out

    def test_classified_error_and_exception(self, monkeypatch, capsys):
        from src.llm.cli import ProcessResult
        monkeypatch.setattr(effort_check, 'run_process',
                            lambda cmd, **kw: ProcessResult(1, '', "You've hit your limit"))
        assert effort_check.main(['--backend', 'claude-cli', '--model', 'haiku',
                                  '--levels', 'low']) == 1
        assert 'low     FAIL RateLimitError after ' in capsys.readouterr().out

        def broken(cmd, **kw):
            raise OSError('x')
        monkeypatch.setattr(effort_check, 'run_process', broken)
        call = effort_check.one_call(effort_check.llm.backend_for('claude-cli'), 'p', 'haiku',
                                     'low', 5)
        assert (call['ok'], call['error'], call['level']) == (False, 'OSError', 'low')

    def test_copilot_values_without_reasoning(self):
        values = effort_check.copilot_values(_copilot_json(level=None, reasoning=0))
        assert (values['in'], values['out'], values['reasoning'], values['reported']) == (
            101, 42, 0, '-')  # null (a model without levels): not a level
        for reported, shown in (('max', 'max'), ('none', 'none'), ('minimal', '-'), ('bogus', '-'), (5, '-')):
            assert effort_check.copilot_values(_copilot_json(level=reported))['reported'] == shown
        values = effort_check.claude_values(json.dumps({'result': 'r', 'usage': {
            'input_tokens': 1, 'output_tokens': 2}}))
        assert (values['in'], values['reasoning'], values['error']) == (1, 0, None)

    def test_call_values(self, monkeypatch):
        ticks = iter([10.0, 12.5])
        monkeypatch.setattr(effort_check, 'time', SimpleNamespace(monotonic=lambda: next(ticks)))
        self._run(monkeypatch, lambda cmd: _claude_json())
        call = effort_check.one_call(effort_check.llm.backend_for('claude-cli'), 'p', 'haiku',
                                     'max', 5)
        assert call == {'level': 'max', 'ok': True, 'wall': 2.5, 'reply': '<p k="1">譯文</p>',
                        'in': 3210, 'out': 50, 'reasoning': 7, 'reported': '-', 'error': ''}

    def test_help(self, tmp_path):
        result = _run_script('phase10_effort_check', ['--help'], str(tmp_path))
        assert result.returncode == 0 and '--levels' in result.stdout

    def test_request_handed_to_the_product_command(self, monkeypatch):
        from src.llm import LLMRequest, Message
        backend = effort_check.llm.backend_for('copilot-cli')
        seen = []
        monkeypatch.setattr(backend, 'build_command', lambda p, r: seen.append(r) or ['x'])
        assert effort_check.command(backend, 'PROMPT', 'gpt-6.2-sol', 'xhigh') == ['x']
        assert seen == [LLMRequest(system='', messages=[Message('user', 'PROMPT')],
                                   model='gpt-6.2-sol', timeout=0, effort='xhigh')]

    def test_options(self, capsys):
        with pytest.raises(SystemExit) as e:
            effort_check.main(['--help'])
        assert e.value.code == 0
        out = ' '.join(capsys.readouterr().out.split())
        assert out.startswith('usage: ') and '--backend {claude-cli,copilot-cli} --model MODEL' in out
        assert '[--levels LEVEL [LEVEL ...]]' in out
        assert 'Phase 10 measurement: does the project\'s reasoning effort reach the CLI?' in out
        assert "--model MODEL One of the backend's models" in out
        assert '--levels LEVEL [LEVEL ...] Levels to call with, in order (default: low high)' in out
        assert '--timeout TIMEOUT Seconds per call (default 180, the product default)' in out
        assert 'Makes REAL calls' not in out  # the first line only
        for args, message in ((['--model', 'opus'], 'required: --backend'),
                              (['--backend', 'claude-cli'], 'required: --model'),
                              (['--backend', 'nope', '--model', 'opus'], "invalid choice: 'nope'")):
            with pytest.raises(SystemExit) as e:
                effort_check.main(args)
            assert e.value.code == 2
            assert message in capsys.readouterr().err

    @pytest.mark.parametrize('args, message', [
        (['--backend', 'copilot-cli', '--model', 'gemini-4-carbon', '--levels', 'xhigh', 'max'],
         'gemini-4-carbon (copilot-cli) does not take xhigh, max; it takes: low, medium, high'),
        (['--backend', 'copilot-cli', '--model', 'claude-haiku-4.5', '--levels', 'low'],
         'claude-haiku-4.5 (copilot-cli) does not take low; it takes: none'),
    ])
    def test_refusal_message(self, capsys, args, message):
        with pytest.raises(SystemExit):
            effort_check.main(args)
        assert capsys.readouterr().err.splitlines()[-1].endswith(f'error: {message}')

    def test_lines_flushed(self, monkeypatch):
        import io
        self._run(monkeypatch, lambda cmd: _claude_json())

        class Observed(io.StringIO):
            def __init__(self):
                super().__init__()
                self.flushed_at = []

            def flush(self):
                self.flushed_at.append(self.getvalue().count('\n'))
                super().flush()

        stream = Observed()
        monkeypatch.setattr(sys, 'stdout', stream)
        effort_check.main(['--backend', 'claude-cli', '--model', 'haiku'])
        assert 2 in stream.flushed_at and 3 in stream.flushed_at

    def test_failed_call_wall(self, monkeypatch):
        ticks = iter([10.0, 14.0])
        monkeypatch.setattr(effort_check, 'time', SimpleNamespace(monotonic=lambda: next(ticks)))

        def broken(cmd, **kw):
            raise OSError('x')
        monkeypatch.setattr(effort_check, 'run_process', broken)
        call = effort_check.one_call(effort_check.llm.backend_for('claude-cli'), 'p', 'haiku',
                                     'low', 5)
        assert call == {'level': 'low', 'ok': False, 'error': 'OSError', 'wall': 4.0}

    def test_echoed_phrase_is_no_error(self, monkeypatch):
        """The prompt goes to the backend's classification: a short reply that
        repeats a phrase of the request is not taken for an error."""
        reply = json.dumps({'result': 'authentication_error', 'usage': {
            'input_tokens': 1, 'output_tokens': 1}})
        self._run(monkeypatch, lambda cmd: reply)
        call = effort_check.one_call(effort_check.llm.backend_for('claude-cli'),
                                     'translate: authentication_error', 'haiku', 'low', 5)
        assert call['ok'] is True

    def test_missing_reply_and_level(self):
        values = effort_check.claude_values(json.dumps({'result': None, 'usage': {
            'input_tokens': 1, 'output_tokens': 2}}))
        assert values['reply'] == ''
        events = [json.loads(line) for line in _copilot_json().splitlines()][1:]
        values = effort_check.copilot_values('\n'.join(json.dumps(e) for e in events))
        assert values['reported'] == '-'


# --- Phase 12: Copilot SDK check -------------------------------------------------

sdk_check = _load('phase12_sdk_check')


class FakeSDKRuntime:
    """A fake SDK client for phase12_sdk_check, and the runtime processes
    it pretends to start (`children()`; a killed or stopped one is still
    listed for one more poll). `broken` names behaviours that make checks
    fail. Records the sessions' (model, effort, prompt) and deletions."""

    def __init__(self, usage_after_abort=False, logs=None, broken=()):
        self.broken = set(broken)
        self.logs = logs  # where the runtime's logs go (written on the long prompt)
        self.pids = []
        self.dying = 0
        self.next_pid = 100
        self.usage_after_abort = usage_after_abort
        self.sentences = []
        self.all_sentences = None
        self.requests = []
        self.deleted = []
        self.starting = 0

    def children(self):
        if self.dying:
            self.dying -= 1
            if not self.dying:
                self.pids.clear()
        return list(self.pids)

    def kill(self, pid, sig):
        assert pid in self.pids and sig == signal.SIGKILL
        self.dying = 2

    def client(self):
        import asyncio
        runtime = self

        def event(kind, **data):
            if kind == 'assistant.usage':
                data = {'finish_reason': 'stop', 'content_filter_triggered': False, **data}
            return SimpleNamespace(type=SimpleNamespace(value=kind), data=SimpleNamespace(**data))

        class Session:
            def __init__(self, options):
                self.options, self.handlers = options, []

            def on(self, handler):
                self.handlers.append(handler)

            def emit(self, e):
                for handler in self.handlers:
                    handler(e)

            async def send_and_wait(self, prompt, timeout):
                level = (self.options.get('inference') or {}).get('reasoning_effort')
                runtime.requests.append((self.options['model'], level, prompt))
                if 'no_call_start' not in runtime.broken:
                    self.emit(event('model.call_start'))
                if prompt == sdk_check.LONG and 'answers_long' not in runtime.broken:
                    if runtime.logs is not None:
                        runtime.logs.mkdir(exist_ok=True)
                        marker = '' if 'no_marker' in runtime.broken else prompt
                        (runtime.logs / 'process-1.log').write_bytes(
                            b'\xff ' + marker.encode())
                        (runtime.logs / 'process-2.log').write_text('nothing')
                    await asyncio.Event().wait()
                if any(s in prompt for s in sdk_check.SENTENCES):
                    runtime.sentences.append(prompt)
                    if runtime.all_sentences is None:
                        runtime.all_sentences = asyncio.Event()
                    if len(runtime.sentences) == len(sdk_check.SENTENCES):
                        runtime.all_sentences.set()
                    await runtime.all_sentences.wait()
                if 'wrong_level' in runtime.broken and level:
                    level = 'high'
                if 'no_usage' in runtime.broken:
                    return event('assistant.message', content='譯文')
                cost = 1.0 if self.options['model'] == 'claude-haiku-5.5' else 0.333
                self.emit(event('assistant.usage', cost=cost, reasoning_effort=level,
                                input_tokens=min(len(prompt), 500), output_tokens=7))
                if 'Traditional Chinese' not in prompt:  # the translator's prompt: 2 calls
                    self.emit(event('assistant.usage', cost=cost, reasoning_effort=level,
                                    input_tokens=None, output_tokens=None))
                return event('assistant.message',
                             content='text' if 'english' in runtime.broken else '譯文')

            async def abort(self):
                if runtime.usage_after_abort:
                    self.emit(event('assistant.usage', cost=0.333, reasoning_effort=None,
                                    input_tokens=1, output_tokens=1))
                self.emit(event('session.idle', aborted='not_aborted' not in runtime.broken))

        class Client:
            async def start(self):
                # A call that starts alone is slower to get ready than calls
                # that start while it waits: calls started all at once would
                # reach their sessions in another order than they started
                runtime.starting += 1
                if runtime.starting == 1:
                    await asyncio.sleep(0.05)
                runtime.starting -= 1
                if runtime.pids or runtime.dying:
                    return
                if runtime.next_pid > 100 and 'no_restart' in runtime.broken:
                    return
                runtime.pids.append(runtime.next_pid)
                runtime.next_pid += 1

            async def get_auth_status(self):
                return SimpleNamespace(is_authenticated=True, login='someone')

            async def create_session(self, session_id, **options):
                assert options['model'] is not None
                os.makedirs(os.path.join(sdk_check.SESSION_STATE, session_id))
                if options['model'] == 'no-such-model-phase12' and 'accepts' not in runtime.broken:
                    raise Exception('Model "no-such-model-phase12" is not available.')
                if 'inference' in options and options['model'] == 'claude-haiku-4.5':
                    raise Exception("Model 'claude-haiku-4.5' does not support reasoning "
                                    "effort configuration.")
                return Session(options)

            async def delete_session(self, session_id):
                await asyncio.sleep(0.05)  # the runtime takes a moment
                if 'keeps_folders' not in runtime.broken:
                    shutil.rmtree(os.path.join(sdk_check.SESSION_STATE, session_id))
                runtime.deleted.append(session_id)

            async def stop(self):
                if 'left' not in runtime.broken:
                    runtime.dying = 2

        return Client()


PHASE12_EXPECTED = """\
1. refusals before any model call
  ok   unknown model/effort: BackendUnavailableError  BackendUnavailableError
  ok   unknown: no model call
  ok   effort model/effort: BackendUnavailableError  BackendUnavailableError
  ok   effort: no model call
2. three concurrent calls
  concurrent 0 reply                    wall   N s  in    73  out     7  cost [0.333]  effort [None]
  concurrent 1 reply                    wall   N s  in    77  out     7  cost [0.333]  effort [None]
  concurrent 2 reply                    wall   N s  in    72  out     7  cost [0.333]  effort [None]
  ok   all three answered in Chinese
  ok   the three calls overlapped in time
  ok   one runtime process  1
3. runtime killed, then a translation with the product prompt
  ok   the runtime is gone
  translation  reply                    wall   N s  in   500  out     7  cost [0.333, 0.333]  effort [None, None]
  ok   translation reply in Chinese
  ok   a new runtime process
  ok   usage reported
4. effort low on claude-haiku-5.5
  effort       reply                    wall   N s  in    72  out     7  cost [1.0]  effort ['low']
  ok   the model call reports effort low  ['low']
5. cancel and timeout mid-turn
  cancelled    CancelledError           wall   N s  in     0  out     0  cost []  effort []
  ok   cancelled: CancelledError
  ok   cancelled: the model call had started
  ok   cancelled: returned within 1 s  N s
  ok   cancelled: session went idle as aborted  idle events 1
  ok   cancelled: no assistant.usage (nothing reported billed)
  ok   cancelled: session deleted
  timed out    TransientError           wall   N s  in     0  out     0  cost []  effort []
  ok   timed out: TransientError
  ok   timed out: the model call had started
  ok   timed out: returned within 1 s  N s
  ok   timed out: session went idle as aborted  idle events 1
  ok   timed out: no assistant.usage (nothing reported billed)
  ok   timed out: session deleted
6. session files
  ok   no session directory left of the 9 sessions  0 left
  ok   a delete asked for every session
7. stop
  ok   no runtime process left  0
information
  cost the SDK reported in total: 2.67 premium requests
  runtime logs written during the run: 2; hold prompt text: yes
PASS
"""


class TestPhase12SdkCheck:

    def _run(self, monkeypatch, tmp_path, capsys, fake, argv=('--timeout-s', '0.5')):
        from src.llm import copilot_sdk
        monkeypatch.setattr(copilot_sdk, '_new_client', fake.client)
        monkeypatch.setattr(sdk_check, 'children', fake.children)
        monkeypatch.setattr(sdk_check.os, 'kill', fake.kill)
        self.pauses = []

        def pause(seconds):  # recorded, and a short real wait for what the loop does
            self.pauses.append(seconds)
            threading.Event().wait(0.01)

        monkeypatch.setattr(sdk_check, '_pause', pause)
        monkeypatch.setattr(sdk_check, 'SETTLE_SECONDS', 2)
        monkeypatch.setattr(sdk_check, 'CALL_TIMEOUT', 3)
        monkeypatch.setattr(sdk_check, 'SESSION_STATE', str(tmp_path / 'state'))
        monkeypatch.setattr(sdk_check, 'RUNTIME_LOGS', str(tmp_path / 'logs' / '*.log'))
        code = sdk_check.main(list(argv))
        return code, capsys.readouterr().out

    def _fails(self, out):
        return [line[len('  FAIL '):] for line in out.splitlines() if line.startswith('  FAIL ')]

    def test_all_checks_pass(self, monkeypatch, tmp_path, capsys):
        old = tmp_path / 'logs' / 'process-0.log'  # from before the run: not counted
        old.parent.mkdir()
        old.write_text(sdk_check.MARKER)
        os.utime(old, (0, 0))
        fake = FakeSDKRuntime(logs=tmp_path / 'logs')
        code, out = self._run(monkeypatch, tmp_path, capsys, fake)
        assert code == 0, out
        assert re.sub(r'\d+\.\d\d s', 'N s', out) == PHASE12_EXPECTED
        # The calls the script made, and every session deleted
        prompt = fake.requests[3][2]
        assert sorted(fake.requests[:3]) == sorted(
            ('claude-haiku-4.5', None, f'Translate to Traditional Chinese: {s}')
            for s in sdk_check.SENTENCES)
        assert fake.requests[3:] == [
            ('claude-haiku-4.5', None, prompt),
            ('claude-haiku-5.5', 'low',
             'Translate to Traditional Chinese: The clock in the hall stopped at noon.'),
            ('claude-haiku-4.5', None, sdk_check.LONG),
            ('claude-haiku-4.5', None, sdk_check.LONG)]
        from src.core.segmenter import SegmentType
        from src.core.translator import TranslationEngine
        from src.llm.fake import FakeBackend
        assert prompt == TranslationEngine(backend=FakeBackend())._build_prompt(
            '', SegmentType.TEXT, html=f'<p k="1">{sdk_check.PARAGRAPH}</p>')
        done = threading.Event()  # the last delete may still be finishing on the loop
        for _ in range(500):
            if len(fake.deleted) == 9 or done.wait(0.01):
                break
        assert len(fake.deleted) == 9 and len(set(fake.deleted)) == 9
        # The cancel came 1 s (pause) into the model call; it returned at once
        cancelled = re.search(r'cancelled +CancelledError +wall +(\d+\.\d\d) s', out)
        assert float(cancelled.group(1)) < 1
        assert self.pauses.count(1) == 1 and set(self.pauses) == {0.1, 1}
        # No reply text, no account data
        assert '譯文' not in out and 'someone' not in out

    @pytest.mark.parametrize('broken, failed', [
        ('no_call_start', ['cancelled: the model call had started',
                           'timed out: the model call had started']),
        ('wrong_level', ["the model call reports effort low  ['high']"]),
        ('no_usage', ['usage reported', "the model call reports effort low  []"]),
        ('english', ['all three answered in Chinese', 'translation reply in Chinese']),
        ('not_aborted', ['cancelled: session went idle as aborted  idle events 1',
                         'timed out: session went idle as aborted  idle events 1']),
        ('no_restart', ['a new runtime process']),
        ('accepts', ['unknown model/effort: BackendUnavailableError', 'unknown: no model call']),
        ('left', ['no runtime process left  1']),
    ])
    def test_each_check_can_fail(self, monkeypatch, tmp_path, capsys, broken, failed):
        code, out = self._run(monkeypatch, tmp_path, capsys, FakeSDKRuntime(broken=[broken]))
        assert code == 1
        assert self._fails(out) == failed, out
        assert out.splitlines()[-1] == 'FAIL: ' + ', '.join(
            re.sub(r'  .*$', '', f) for f in failed)

    def test_long_reply_not_stopped(self, monkeypatch, tmp_path, capsys):
        """A turn that answers instead of being timed out fails its checks
        (the cancelled one races with the reply and is not asserted)."""
        code, out = self._run(monkeypatch, tmp_path, capsys,
                              FakeSDKRuntime(broken=['answers_long']))
        assert code == 1
        timed_out = [f for f in self._fails(out) if f.startswith('timed out')]
        assert [re.sub(r'  -?\d+\.\d\d s$', '', f) for f in timed_out] == [
            'timed out: TransientError', 'timed out: returned within 1 s',
            'timed out: session went idle as aborted  idle events 0',
            'timed out: no assistant.usage (nothing reported billed)']

    def test_runtime_not_gone(self, monkeypatch, tmp_path, capsys):
        fake = FakeSDKRuntime()
        fake.kill = lambda pid, sig: None  # survives the kill
        code, out = self._run(monkeypatch, tmp_path, capsys, fake)
        assert code == 1
        assert self._fails(out) == ['the runtime is gone', 'a new runtime process']
        assert self.pauses.count(0.1) >= 50  # the 5 s the runtime is given to go

    def test_logs_without_the_prompt(self, monkeypatch, tmp_path, capsys):
        code, out = self._run(monkeypatch, tmp_path, capsys,
                              FakeSDKRuntime(logs=tmp_path / 'logs', broken=['no_marker']))
        assert code == 0
        assert '  runtime logs written during the run: 2; hold prompt text: no\n' in out

    def test_usage_after_abort_fails(self, monkeypatch, tmp_path, capsys):
        code, out = self._run(monkeypatch, tmp_path, capsys,
                              FakeSDKRuntime(usage_after_abort=True))
        assert code == 1
        assert self._fails(out) == ['cancelled: no assistant.usage (nothing reported billed)',
                                    'timed out: no assistant.usage (nothing reported billed)']
        assert re.search(r'cancelled +CancelledError .* in +1  out +1  cost \[0\.333\]', out)

    def test_session_left_fails(self, monkeypatch, tmp_path, capsys):
        code, out = self._run(monkeypatch, tmp_path, capsys,
                              FakeSDKRuntime(broken=['keeps_folders']))
        assert code == 1
        assert self._fails(out) == ['cancelled: session deleted', 'timed out: session deleted',
                                    'no session directory left of the 9 sessions  9 left']

    def test_lines_flushed(self, monkeypatch, tmp_path):
        """Every result line is flushed as it is printed (the run is slow)."""
        import io

        class Observed(io.StringIO):
            flushed_at = []

            def flush(self):
                self.flushed_at.append(self.getvalue().count('\n'))
                super().flush()

        stream = Observed()
        monkeypatch.setattr(sys, 'stdout', stream)
        self._run(monkeypatch, tmp_path, SimpleNamespace(readouterr=lambda: SimpleNamespace(
            out='')), FakeSDKRuntime())
        lines = stream.getvalue().splitlines()
        result_lines = [i + 1 for i, line in enumerate(lines) if line.startswith('  ')
                        and not line.startswith(('  cost the SDK', '  runtime logs'))]
        assert all(n in stream.flushed_at for n in result_lines)

    def test_without_a_runtime_in_tests(self, capsys, monkeypatch):
        """Under the suite's guard (conftest.no_real_sdk_client) the script
        reaches no runtime: every call fails and so does the script."""
        monkeypatch.setattr(sdk_check, '_pause', lambda seconds: None)
        monkeypatch.setattr(sdk_check, 'SETTLE_SECONDS', 0.1)
        assert sdk_check.main(['--timeout-s', '0.5']) == 1
        # Whether three calls that fail at once overlap depends on scheduling: not asserted
        fails = [re.sub(r'  -?\d+\.\d\d s$', '', line)
                 for line in capsys.readouterr().out.splitlines()
                 if line.startswith('  FAIL ') and 'overlapped' not in line]
        assert fails == [
            '  FAIL all three answered in Chinese',
            '  FAIL one runtime process  0',
            '  FAIL translation reply in Chinese',
            '  FAIL a new runtime process',
            '  FAIL usage reported',
            '  FAIL the model call reports effort low  []',
            '  FAIL cancelled: CancelledError',
            '  FAIL cancelled: the model call had started',
            '  FAIL cancelled: returned within 1 s',
            '  FAIL cancelled: session went idle as aborted  idle events 0',
            '  FAIL cancelled: no assistant.usage (nothing reported billed)',
            '  FAIL cancelled: session deleted',
            '  FAIL timed out: TransientError',
            '  FAIL timed out: the model call had started',
            '  FAIL timed out: returned within 1 s',
            '  FAIL timed out: session went idle as aborted  idle events 0',
            '  FAIL timed out: no assistant.usage (nothing reported billed)',
            '  FAIL timed out: session deleted']

    def test_options(self, capsys):
        with pytest.raises(SystemExit) as e:
            sdk_check.main(['--help'])
        assert e.value.code == 0
        out = ' '.join(capsys.readouterr().out.split())
        assert out.startswith('usage: ')
        assert ('Phase 12 check: the Copilot SDK backend (copilot-sdk) against the real '
                'runtime. options:') in out
        for text in ('--model MODEL Model of every call but the effort one (default '
                     'claude-haiku-4.5)',
                     '--effort-model EFFORT_MODEL Model of the effort call (default '
                     'claude-haiku-5.5)',
                     '--level LEVEL Effort level of that call (default low)',
                     '--timeout-s TIMEOUT_S Timeout of the timed-out call (default 6 s; the '
                     'model call must have started by then)'):
            assert text in out
        with pytest.raises(SystemExit) as e:
            sdk_check.main(['--level', 'max', '--effort-model', 'gemini-4-argon'])
        assert e.value.code == 2
        assert 'error: gemini-4-argon does not take max' in capsys.readouterr().err
        with pytest.raises(SystemExit) as e:
            sdk_check.main(['--model', 'claude-haiku-5.5'])
        assert e.value.code == 2
        assert ('--model must take no effort level (step 1 uses it); claude-haiku-5.5 takes some'
                in capsys.readouterr().err)

    def test_children_are_the_runtimes(self, tmp_path):
        """children() lists this process's `copilot … --headless` children only."""
        program = tmp_path / 'copilot'
        program.write_text('#!/bin/sh\nsleep 30\n')
        program.chmod(0o755)
        runtime = subprocess.Popen([str(program), '--headless', '--stdio'], start_new_session=True)
        other = subprocess.Popen([str(program), '--other'], start_new_session=True)
        try:
            assert sdk_check.children() == [runtime.pid]
        finally:
            for proc in (runtime, other):  # with the sleep each one started
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        assert sdk_check.children() == []

    def test_header(self):
        doc = ' '.join(sdk_check.__doc__.split())
        assert 'Makes REAL model calls' in doc
        assert 'It prints no account data' in doc
