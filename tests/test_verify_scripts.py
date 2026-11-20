"""Tests for the read-only check scripts under scripts/verify/."""

import importlib.util
import json
import os
import subprocess
import sys

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


class TestExportCheck:

    @pytest.fixture(autouse=True)
    def no_epubcheck(self, monkeypatch):
        real = export_check.shutil.which
        monkeypatch.setattr(export_check.shutil, 'which',
                            lambda name: None if name == 'epubcheck' else real(name))

    PASS = [
        'epubcheck not available (not on PATH): not run',
        'PASS project-1: exports 3, translated segments 3: as translation 3, kept original 0, '
        'toc labels changed 0/0',
        'PASS project-2: exports 3, translated segments 4: as translation 4, kept original 0, '
        'toc labels changed 2/2',
        'TOTAL projects 2  exports 6  translated segments 7  as translation 7  kept original 0  '
        'toc labels changed 2/2  epubcheck not run  failed projects 0',
    ]

    def _main(self, root, anonymize=True):
        return export_check.main(['--projects-dir', str(root)]
                                 + (['--anonymize'] if anonymize else []))

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
                          'toc labels changed 2/2')
        assert out[2] == ('TOTAL projects 1  exports 3  translated segments 4  '
                          'as translation 2  kept original 2 (not_well_formed 1, '
                          'structure_mismatch 1)  toc labels changed 2/2  epubcheck not run  '
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

    def _fails_with(self, tmp_path, capsys, monkeypatch, change, problem,
                    formats=('chinese-only',)):
        root = _export_projects(tmp_path / 'projects', schema_1=False)
        _tamper(monkeypatch, change, formats)
        assert self._main(root) == 1
        line = capsys.readouterr().out.splitlines()[1]
        assert line.startswith('FAIL project-1: ')
        assert line.split('; ', 1)[1] == problem

    def test_mimetype_not_first(self, tmp_path, capsys, monkeypatch):
        self._fails_with(tmp_path, capsys, monkeypatch, lambda m: m[1:] + m[:1],
                         'chinese-only: mimetype not first and stored')

    def test_mimetype_compressed(self, tmp_path, capsys, monkeypatch):
        import zipfile

        def change(members):
            info = zipfile.ZipInfo('mimetype')
            info.compress_type = zipfile.ZIP_DEFLATED
            return [(info, members[0][1])] + members[1:]
        self._fails_with(tmp_path, capsys, monkeypatch, change,
                         'chinese-only: mimetype not first and stored')

    @pytest.mark.filterwarnings('ignore:Duplicate name')
    def test_member_twice(self, tmp_path, capsys, monkeypatch):
        import zipfile

        def change(members):
            info, data = members[-1]
            return members + [(zipfile.ZipInfo(info.filename), data)]
        self._fails_with(tmp_path, capsys, monkeypatch, change,
                         'chinese-only: a member is written twice')

    def test_member_added(self, tmp_path, capsys, monkeypatch):
        import zipfile
        self._fails_with(tmp_path, capsys, monkeypatch,
                         lambda m: m + [(zipfile.ZipInfo('OEBPS/x.txt'), b'x')],
                         'chinese-only: 1 members added')

    @pytest.mark.parametrize('drop', [False, True])
    def test_member_outside_the_allowed_set_differs(self, tmp_path, capsys, monkeypatch, drop):
        if drop:
            def change(members):
                return [(i, d) for i, d in members if i.filename != 'OEBPS/Styles/book.css']
        else:
            change = _edit('OEBPS/Styles/book.css', b'0', b'1')
        self._fails_with(tmp_path, capsys, monkeypatch, change,
                         'chinese-only: 1 members outside the allowed set differ')

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
            'chinese-only': [('RSC-005', 'ERROR', 2), ('X-1', 'USAGE', 3)],
            'bilingual-horizontal': [('RSC-005', 'ERROR', 3), ('RSC-012', 'FATAL', 0)],
            'bilingual-vertical': [('RSC-005', 'ERROR', 1)]})
        assert export_check.main(['--projects-dir', str(root), '--anonymize']) == 1
        out = capsys.readouterr().out
        assert out.splitlines() == [
            'epubcheck found; errors compared by message ID '
            '(chinese-only, bilingual-horizontal, bilingual-vertical)',
            'FAIL project-1: exports 3, translated segments 4: as translation 4, '
            'kept original 0, toc labels changed 2/2, epubcheck errors source 2 (RSC-005:2), '
            'exports 2/4/1; bilingual-horizontal: epubcheck new errors RSC-005 1, RSC-012 1',
            'TOTAL projects 1  exports 3  translated segments 4  as translation 4  '
            'kept original 0  toc labels changed 2/2  exports with new epubcheck errors 1  '
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
        assert export_check.new_errors(Counter(a=2, b=1), Counter(a=3, b=1, c=2)) == {
            'a': 1, 'c': 2}
