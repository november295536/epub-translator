"""Tests for CLI commands."""

import json
import os
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from translate import cli, _get_project_dir
from src.core.project import ProjectManager
from src.core.segmenter import Segment


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def sample_epub(tmp_path):
    """Create a minimal test EPUB for CLI testing."""
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier('cli-test-001')
    book.set_title('CLI Test Book')
    book.set_language('en')
    book.add_author('CLI Author')

    c1 = epub.EpubHtml(title='Chapter 1', file_name='chap01.xhtml', lang='en')
    c1.content = b'''<html><body>
<h1>Chapter 1</h1>
<p>This is a test chapter with some content for testing the CLI commands.
It should be long enough to create at least one segment when processed.</p>
</body></html>'''

    book.add_item(c1)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ['nav', c1]

    epub_path = str(tmp_path / 'cli_test.epub')
    epub.write_epub(epub_path, book)
    return epub_path


class TestCLIInit:

    def test_init_creates_project(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(cli, ['init', sample_epub])

        assert result.exit_code == 0
        assert '專案已建立' in result.output
        assert 'CLI Test Book' in result.output

    def test_init_nonexistent_file(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(cli, ['init', '/nonexistent/book.epub'])

        assert result.exit_code != 0

    def test_init_creates_project_json(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        runner.invoke(cli, ['init', sample_epub])

        project_dir = _get_project_dir(sample_epub)
        full_path = os.path.join(str(tmp_path), project_dir)
        assert os.path.isfile(os.path.join(full_path, 'project.json'))
        with open(os.path.join(full_path, 'project.json'), encoding='utf-8') as f:
            assert json.load(f)['segmenter_version'] == 2

    def test_init_duplicate_project(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        runner.invoke(cli, ['init', sample_epub])
        result = runner.invoke(cli, ['init', sample_epub])

        assert result.exit_code != 0
        assert '已存在' in result.output


class TestCLIStatus:

    def test_status_shows_progress(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        runner.invoke(cli, ['init', sample_epub])

        project_dir = _get_project_dir(sample_epub)
        full_path = os.path.join(str(tmp_path), project_dir)
        result = runner.invoke(cli, ['status', '--project', full_path])

        assert result.exit_code == 0
        assert '總段落數' in result.output
        assert '待翻譯' in result.output
        assert '進度' in result.output

    def test_status_no_project(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(cli, ['status'])

        assert result.exit_code != 0
        assert '找不到專案' in result.output


class TestCLIStart:

    def test_start_no_project(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(cli, ['start'])

        assert result.exit_code != 0
        assert '找不到專案' in result.output

    def test_start_translates_segments(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        runner.invoke(cli, ['init', sample_epub])
        project_dir = _get_project_dir(sample_epub)
        full_path = os.path.join(str(tmp_path), project_dir)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '譯文'

        with patch('src.core.translator.subprocess.run', return_value=mock_result):
            result = runner.invoke(cli, ['start', '--project', full_path])

        assert result.exit_code == 0
        assert '完成' in result.output

    def test_start_skips_completed(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        runner.invoke(cli, ['init', sample_epub])
        project_dir = _get_project_dir(sample_epub)
        full_path = os.path.join(str(tmp_path), project_dir)

        # Mark all segments as translated
        pm = ProjectManager(full_path)
        pm.load()
        for seg in pm.data.segments:
            pm.mark_translated(seg.segment_id, '已翻譯')
        pm.save()

        result = runner.invoke(cli, ['start', '--project', full_path])

        assert result.exit_code == 0
        assert '已翻譯完成' in result.output

    def test_start_handles_translation_error(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        runner.invoke(cli, ['init', sample_epub])
        project_dir = _get_project_dir(sample_epub)
        full_path = os.path.join(str(tmp_path), project_dir)

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stderr = 'error'

        with patch('src.core.translator.subprocess.run', return_value=fail_result):
            result = runner.invoke(cli, ['start', '--project', full_path])

        assert result.exit_code != 0
        assert '翻譯失敗' in result.output or '翻譯中斷' in result.output


class TestGetProjectDir:

    def test_derives_from_filename(self):
        assert _get_project_dir('my_book.epub') == os.path.join('projects', 'my-book')

    def test_handles_path(self):
        result = _get_project_dir('/path/to/my_book.epub')
        assert result == os.path.join('projects', 'my-book')

    def test_sanitizes_special_chars(self):
        result = _get_project_dir('Book (2nd Ed.) [Library].epub')
        assert result == os.path.join('projects', 'Book-2nd-Ed.-Library')


class TestCLIModelOption:

    def test_start_with_model_option(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        runner.invoke(cli, ['init', sample_epub])
        project_dir = _get_project_dir(sample_epub)
        full_path = os.path.join(str(tmp_path), project_dir)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '譯文'

        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
            result = runner.invoke(cli, ['start', '--project', full_path, '--model', 'opus'])

        assert result.exit_code == 0
        assert '使用模型：opus' in result.output

        # Verify model persisted
        pm = ProjectManager(full_path)
        pm.load()
        assert pm.data.model == 'opus'

        # Verify --model passed to subprocess
        cmd = mock_run.call_args[0][0]
        assert '--model' in cmd
        assert 'opus' in cmd

    def test_start_invalid_model_rejected(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        runner.invoke(cli, ['init', sample_epub])
        project_dir = _get_project_dir(sample_epub)
        full_path = os.path.join(str(tmp_path), project_dir)

        result = runner.invoke(cli, ['start', '--project', full_path, '--model', 'invalid'])

        assert result.exit_code != 0

    def test_start_without_model_uses_default(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        runner.invoke(cli, ['init', sample_epub])
        project_dir = _get_project_dir(sample_epub)
        full_path = os.path.join(str(tmp_path), project_dir)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '譯文'

        with patch('src.core.translator.subprocess.run', return_value=mock_result) as mock_run:
            result = runner.invoke(cli, ['start', '--project', full_path])

        assert result.exit_code == 0
        assert '使用模型：sonnet' in result.output

        # Verify --model sonnet passed to subprocess
        cmd = mock_run.call_args[0][0]
        assert '--model' in cmd
        assert 'sonnet' in cmd


class TestCLIWorkersOption:

    def test_start_with_workers(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        runner.invoke(cli, ['init', sample_epub])
        project_dir = _get_project_dir(sample_epub)
        full_path = os.path.join(str(tmp_path), project_dir)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '譯文'

        with patch('src.core.translator.subprocess.run', return_value=mock_result):
            result = runner.invoke(cli, ['start', '--project', full_path, '--workers', '3'])

        assert result.exit_code == 0
        assert '並行 Workers：3' in result.output
        assert '完成' in result.output

    def test_start_default_workers_is_1(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        runner.invoke(cli, ['init', sample_epub])
        project_dir = _get_project_dir(sample_epub)
        full_path = os.path.join(str(tmp_path), project_dir)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '譯文'

        with patch('src.core.translator.subprocess.run', return_value=mock_result):
            result = runner.invoke(cli, ['start', '--project', full_path])

        assert result.exit_code == 0
        # Should not show workers line when workers=1
        assert '並行 Workers' not in result.output

    def test_start_workers_invalid_rejected(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        runner.invoke(cli, ['init', sample_epub])
        project_dir = _get_project_dir(sample_epub)
        full_path = os.path.join(str(tmp_path), project_dir)

        result = runner.invoke(cli, ['start', '--project', full_path, '--workers', '0'])
        assert result.exit_code != 0

        result = runner.invoke(cli, ['start', '--project', full_path, '--workers', '25'])
        assert result.exit_code != 0


class TestCLIContext:
    """The prompts built by both CLI modes use same-chapter, non-code context."""

    @pytest.mark.parametrize('workers', ['1', '3'])
    def test_start_prompts(self, runner, tmp_path, make_context_project, workers):
        from tests.conftest import fake_claude_run, assert_context_prompts
        project_dir = make_context_project(str(tmp_path / 'ctx'))

        with patch('src.core.translator.subprocess.run',
                   side_effect=fake_claude_run) as mock_run:
            result = runner.invoke(cli, ['start', '--project', project_dir,
                                         '--workers', workers])

        assert result.exit_code == 0, result.output
        prompts = [c[0][0][2] for c in mock_run.call_args_list]
        assert len(prompts) == 5
        assert_context_prompts(prompts, project_dir)
