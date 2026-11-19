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
        mock_result.stdout = '翻譯結果'

        with patch('src.llm.cli.run_process', return_value=mock_result):
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

        with patch('src.llm.cli.run_process', return_value=fail_result):
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
        mock_result.stdout = '翻譯結果'

        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
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
        mock_result.stdout = '翻譯結果'

        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
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
        mock_result.stdout = '翻譯結果'

        with patch('src.llm.cli.run_process', return_value=mock_result):
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
        mock_result.stdout = '翻譯結果'

        with patch('src.llm.cli.run_process', return_value=mock_result):
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

        with patch('src.llm.cli.run_process',
                   side_effect=fake_claude_run) as mock_run:
            result = runner.invoke(cli, ['start', '--project', project_dir,
                                         '--workers', workers])

        assert result.exit_code == 0, result.output
        prompts = [c[0][0][2] for c in mock_run.call_args_list]
        assert len(prompts) == 5
        assert_context_prompts(prompts, project_dir)


# --- Phase 1: failed segments, retry, lock, version, term extraction -----------

from src.core.project import SegmentStatus
from src.llm import AuthError, RateLimitError, TransientError
from src.llm.fake import FakeBackend
from tests.conftest import hold_lock_in_child, lock_free_for_other_process, use_fake_backend


def _make_project(path, n=4):
    segments = [Segment(id=f'c1_seg{i:04d}', html=f'<p>Sentence MARK{i}.</p>',
                        text=f'Sentence MARK{i}.', word_count=2, segment_type='text')
                for i in range(1, n + 1)]
    pm = ProjectManager(path)
    pm.create('/path/to/book.epub', 'Invented', 'Nobody', segments)
    return path


def _responder(fail_marks=(), error=TransientError('exit 1', raw='RAW')):
    """Translate every segment except those whose text holds a mark in fail_marks."""
    def respond(request):
        prompt = request.messages[-1].content
        for mark in fail_marks:
            # The segment's own HTML; neighbours appear as plain-text context
            if f'<p>Sentence {mark}.</p>' in prompt:
                raise error
        return '<p>譯文</p>'
    return respond


def _statuses(path):
    pm = ProjectManager(path)
    pm.load()
    return {s.segment_id: s.status for s in pm.data.segments}


class TestCLIFailedSegments:

    @pytest.mark.parametrize('workers', ['1', '3'])
    def test_failing_segment_recorded_and_rest_translated(self, runner, tmp_path, workers):
        path = _make_project(str(tmp_path / 'p'))
        fake = FakeBackend(responder=_responder(['MARK2']))
        with use_fake_backend(fake):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', workers])

        assert result.exit_code == 1, result.output
        assert '1 個段落翻譯失敗' in result.output
        assert _statuses(path) == {
            'c1_seg0001': SegmentStatus.TRANSLATED,
            'c1_seg0002': SegmentStatus.FAILED,
            'c1_seg0003': SegmentStatus.TRANSLATED,
            'c1_seg0004': SegmentStatus.TRANSLATED,
        }
        pm = ProjectManager(path)
        pm.load()
        failed = pm.data.segments[1]
        assert [i.code for i in failed.issues] == ['transient']
        assert pm.data.segments[0].model == 'sonnet'
        # Diagnostics for the failed segment only
        assert os.listdir(os.path.join(path, 'log', 'failed')) == ['c1_seg0002-dbf96005d5.txt']
        # 3 attempts for the failing segment, 1 for each other
        assert len(fake.requests) == 6

    @pytest.mark.parametrize('workers', ['1', '3'])
    def test_failed_not_retried_without_flag(self, runner, tmp_path, workers):
        path = _make_project(str(tmp_path / 'p'))
        with use_fake_backend(FakeBackend(responder=_responder(['MARK2']))):
            runner.invoke(cli, ['start', '--project', path])

        fake = FakeBackend(responder=_responder())
        with use_fake_backend(fake):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', workers])
        assert result.exit_code == 0, result.output
        assert fake.requests == []
        assert '--retry-failed' in result.output
        assert _statuses(path)['c1_seg0002'] == SegmentStatus.FAILED

        with use_fake_backend(fake):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', workers,
                                         '--retry-failed'])
        assert result.exit_code == 0, result.output
        assert len(fake.requests) == 1
        assert '<p>Sentence MARK2.</p>' in fake.prompts[0]
        assert set(_statuses(path).values()) == {SegmentStatus.TRANSLATED}
        assert os.listdir(os.path.join(path, 'log', 'failed')) == []

    @pytest.mark.parametrize('workers', ['1', '3'])
    @pytest.mark.parametrize('error', [RateLimitError("You've hit your limit"),
                                       AuthError('expired')])
    def test_rate_limit_or_auth_stops_run(self, runner, tmp_path, workers, error):
        path = _make_project(str(tmp_path / 'p'), n=1)
        with use_fake_backend(FakeBackend([error])):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', workers])
        assert result.exit_code == 1
        assert '翻譯中斷' in result.output
        assert _statuses(path) == {'c1_seg0001': SegmentStatus.PENDING}

    def test_status_shows_failed(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'))
        with use_fake_backend(FakeBackend(responder=_responder(['MARK1', 'MARK3']))):
            runner.invoke(cli, ['start', '--project', path])
        result = runner.invoke(cli, ['status', '--project', path])
        assert '失敗：    2' in result.output


class TestCLILock:

    def test_start_refused_while_other_process_holds_lock(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'))
        child = hold_lock_in_child(path)
        fake = FakeBackend(responder=_responder())
        try:
            with use_fake_backend(fake):
                result = runner.invoke(cli, ['start', '--project', path])
        finally:
            child.stdin.close()
            child.wait(timeout=10)
        assert result.exit_code == 1
        assert '另一個程序' in result.output
        assert fake.requests == []

    def test_lock_held_during_run_and_released_after(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'), n=1)
        seen = []

        def respond(request):
            seen.append(lock_free_for_other_process(path))
            return '<p>譯文</p>'

        with use_fake_backend(FakeBackend(responder=respond)):
            result = runner.invoke(cli, ['start', '--project', path])
        assert result.exit_code == 0, result.output
        assert seen == [False]
        assert lock_free_for_other_process(path)

    @pytest.mark.parametrize('workers', ['1', '3'])
    def test_lock_released_on_error(self, runner, tmp_path, workers):
        path = _make_project(str(tmp_path / 'p'), n=1)
        with use_fake_backend(FakeBackend([RuntimeError('unexpected')])):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', workers])
        assert lock_free_for_other_process(path)
        # Review round 2, item 5: a failed segment in both modes
        assert result.exit_code == 1
        assert _statuses(path) == {'c1_seg0001': SegmentStatus.FAILED}

    def test_lock_released_after_rate_limit_exit(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'), n=1)
        with use_fake_backend(FakeBackend([RateLimitError('limit')])):
            result = runner.invoke(cli, ['start', '--project', path])
        assert result.exit_code == 1
        assert lock_free_for_other_process(path)


class TestCLIVersion:

    def test_version(self, runner):
        result = runner.invoke(cli, ['--version'])
        assert result.exit_code == 0
        assert '1.0.0' in result.output


class TestCLIGlossaryExtract:

    def test_extract_uses_project_model(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'), n=2)
        pm = ProjectManager(path)
        pm.load()
        pm.set_model('haiku')
        pm.save()
        fake = FakeBackend(['[{"term": "MARK1", "translation": "標記", "category": "term"}]'])
        with use_fake_backend(fake):
            result = runner.invoke(cli, ['glossary', 'extract', '--project', path])
        assert result.exit_code == 0, result.output
        assert [r.model for r in fake.requests] == ['haiku']
        assert 'Sentence MARK1.' in fake.prompts[0]


class TestCLIMessages:
    """Exact CLI output (mutation survivors)."""

    FAIL = 'Translation failed after 3 attempts: exit 1'

    def test_version_line(self, runner):
        assert runner.invoke(cli, ['--version']).output == 'epub-translator, version 1.0.0\n'

    def test_retry_failed_help(self, runner):
        output = ' '.join(runner.invoke(cli, ['start', '--help']).output.split())
        assert ('--retry-failed Also translate segments whose earlier translation failed '
                '--help') in output

    def test_sequential_output(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'), n=3)
        with use_fake_backend(FakeBackend(responder=_responder(['MARK1', 'MARK3']))):
            result = runner.invoke(cli, ['start', '--project', path])
        lines = result.output.splitlines()
        assert lines[-8:] == [
            '[1/3] 翻譯段落 c1_seg0001...',
            f'  ✗ 翻譯失敗：{self.FAIL}',
            '[2/3] 翻譯段落 c1_seg0002...',
            '  ✓ 完成（2 字）',
            '[3/3] 翻譯段落 c1_seg0003...',
            f'  ✗ 翻譯失敗：{self.FAIL}',
            '',
            f"翻譯結束：2 個段落翻譯失敗，其餘已完成。失敗紀錄在 "
            f"{os.path.join(path, 'log', 'failed')}，可用 start --retry-failed 重試。",
        ]

    def test_parallel_output(self, runner, tmp_path):
        import re
        path = _make_project(str(tmp_path / 'p'), n=4)
        with use_fake_backend(FakeBackend(responder=_responder(['MARK1', 'MARK3']))):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', '2'])
        lines = result.output.splitlines()
        failed = sorted(l for l in lines if '✗' in l)
        assert [re.sub(r'\[W\d\]', '[W]', l) for l in failed] == [
            f'  [W] ✗ c1_seg0001 翻譯失敗：{self.FAIL}',
            f'  [W] ✗ c1_seg0003 翻譯失敗：{self.FAIL}',
        ]
        done = [re.fullmatch(r'  \[W\d\] \[(\d)/4\] ✓ (c1_seg000\d)（2 字）', l)
                for l in lines if '✓' in l]
        assert all(done)
        assert sorted(m.group(1) for m in done) == ['1', '2']
        assert sorted(m.group(2) for m in done) == ['c1_seg0002', 'c1_seg0004']
        assert lines[-1].startswith('翻譯結束：2 個段落翻譯失敗，其餘已完成。')

    def test_parallel_counts_continue_from_translated(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'), n=3)
        pm = ProjectManager(path)
        pm.load()
        pm.mark_translated('c1_seg0001', '已譯')
        pm.save()
        with use_fake_backend(FakeBackend(responder=_responder())):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', '2'])
        counts = sorted(l.split('] [')[1].split(']')[0] for l in result.output.splitlines()
                        if '✓' in l)
        assert counts == ['2/3', '3/3']

    def test_failed_left_over_message(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'), n=2)
        with use_fake_backend(FakeBackend(responder=_responder(['MARK1', 'MARK2']))):
            runner.invoke(cli, ['start', '--project', path])
        result = runner.invoke(cli, ['start', '--project', path])
        assert result.output.splitlines() == [
            '所有段落已翻譯完成！',
            '2 個段落先前翻譯失敗，可用 start --retry-failed 重試。',
        ]
        assert '失敗：    2' in runner.invoke(cli, ['status', '--project', path]).output.splitlines()

    @pytest.mark.parametrize('workers', ['1', '2'])
    def test_stop_message(self, runner, tmp_path, workers):
        path = _make_project(str(tmp_path / 'p'), n=1)
        with use_fake_backend(FakeBackend([RateLimitError('limit reached')])):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', workers])
        assert result.output.splitlines()[-2:] == [
            '  ✗ limit reached',
            '翻譯中斷。已完成的進度已儲存，可用 start 繼續。',
        ]


# --- Review round 1 ---------------------------------------------------------------

import threading
import time as _time
from src.llm import BackendUnavailableError


class TestCLISupervisorFailure:
    """Item 6: the parallel supervisor joins its workers before the lock goes."""

    @pytest.mark.parametrize('error', [OSError('disk full'), KeyboardInterrupt()])
    def test_save_failure(self, runner, tmp_path, error):
        path = _make_project(str(tmp_path / 'p'), n=4)
        threads = []

        def respond(request):
            threads.append(threading.current_thread())
            if '<p>Sentence MARK4.</p>' in request.messages[-1].content:
                _time.sleep(1)  # still in flight when the save fails
            return '<p>譯文</p>'

        # Without --model the run's first save is the supervisor's
        with use_fake_backend(FakeBackend(responder=respond)), \
                patch.object(ProjectManager, 'save', side_effect=error):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', '3'])
        assert result.exit_code != 0
        assert threads and not any(t.is_alive() for t in threads)
        assert '未儲存的結果' in result.output
        assert lock_free_for_other_process(path)
        # Nothing was saved: every segment is still pending
        assert set(_statuses(path).values()) == {SegmentStatus.PENDING}


class TestCLIUnexpectedErrors:
    """Item 9: diagnostic I/O errors and unexpected worker errors."""

    @pytest.mark.parametrize('workers', ['1', '3'])
    def test_unwritable_failure_log(self, runner, tmp_path, workers):
        path = _make_project(str(tmp_path / 'p'))
        os.makedirs(os.path.join(path, 'log'))
        with open(os.path.join(path, 'log', 'failed'), 'w') as f:
            f.write('a file where the directory should be')
        with use_fake_backend(FakeBackend(responder=_responder(['MARK2']))):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', workers])
        assert result.exit_code == 1, result.output
        assert '1 個段落翻譯失敗' in result.output
        statuses = _statuses(path)
        assert statuses['c1_seg0002'] == SegmentStatus.FAILED
        assert statuses['c1_seg0004'] == SegmentStatus.TRANSLATED

    @pytest.mark.parametrize('workers', ['1', '3'])  # review round 2, item 5
    def test_unexpected_worker_exception(self, runner, tmp_path, workers):
        path = _make_project(str(tmp_path / 'p'))
        with use_fake_backend(FakeBackend(responder=_responder(['MARK2'],
                                                               error=KeyError('surprise')))):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', workers])
        assert result.exit_code == 1, result.output
        assert '1 個段落翻譯失敗' in result.output
        pm = ProjectManager(path)
        pm.load()
        seg = pm.data.segments[1]
        assert seg.status == SegmentStatus.FAILED
        assert seg.issues[0].code == 'unexpected' and 'surprise' in seg.issues[0].detail
        assert pm.data.segments[3].status == SegmentStatus.TRANSLATED


class TestCLIBackendModels:
    """Item 12: --model is checked against the backend's models."""

    def test_backend_model_accepted_and_claude_alias_rejected(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'), n=1)
        fake = FakeBackend(responder=_responder())
        with use_fake_backend(fake):
            result = runner.invoke(cli, ['start', '--project', path, '--model', 'sonnet'])
            assert result.exit_code == 2
            assert "'sonnet' is not one of fake-model" in result.output
            result = runner.invoke(cli, ['start', '--project', path, '--model', 'fake-model'])
        assert result.exit_code == 0, result.output
        assert [r.model for r in fake.requests] == ['fake-model']


class TestCLIBackendUnavailable:
    """Item 15: a missing program stops the run; no segment becomes FAILED."""

    @pytest.mark.parametrize('workers', ['1', '3'])
    def test_stops_run(self, runner, tmp_path, workers):
        path = _make_project(str(tmp_path / 'p'), n=3)
        error = BackendUnavailableError('claude-cli could not be started: not found')
        with use_fake_backend(FakeBackend(responder=lambda r: (_ for _ in ()).throw(error))):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', workers])
        assert result.exit_code == 1
        assert 'could not be started' in result.output and '翻譯中斷' in result.output
        assert set(_statuses(path).values()) == {SegmentStatus.PENDING}
        assert not os.path.exists(os.path.join(path, 'log', 'failed'))


class TestCLIGlossaryExtractStop:
    """Item 19: a rate limit keeps the terms found so far, without a traceback."""

    def test_partial_terms_saved(self, runner, tmp_path, monkeypatch):
        monkeypatch.setattr('src.glossary.term_extractor.MAX_CHARS_PER_BATCH', 20)
        path = _make_project(str(tmp_path / 'p'), n=3)
        fake = FakeBackend(['[{"term": "Alpha", "translation": "甲", "category": "term"}]',
                            RateLimitError("You've hit your limit")])
        with use_fake_backend(fake):
            result = runner.invoke(cli, ['glossary', 'extract', '--project', path])
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert "提取中斷：You've hit your limit" in result.output
        assert '提取到 1 個術語' in result.output
        from src.glossary.glossary_manager import GlossaryManager
        gm = GlossaryManager(os.path.join(path, 'glossary.json'))
        gm.load()
        assert [e.term for e in gm.get_all_terms()] == ['Alpha']


class TestCLIRound1Exact:
    """Exact output of the round 1 CLI code (mutation survivors)."""

    def test_invalid_model_message(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'), n=1)
        result = runner.invoke(cli, ['start', '--project', path, '--model', 'nope'])
        assert result.exit_code == 2
        assert "Invalid value for '--model' / '-m': 'nope' is not one of sonnet, opus, haiku" \
            in result.output

    def test_model_help(self, runner):
        output = ' '.join(runner.invoke(cli, ['start', '--help']).output.split())
        assert "--model TEXT Translation model: one of the backend's models " in output

    def test_workers_are_daemons(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'), n=2)
        daemons = []

        def respond(request):
            daemons.append(threading.current_thread().daemon)
            return '<p>譯文</p>'

        with use_fake_backend(FakeBackend(responder=respond)):
            runner.invoke(cli, ['start', '--project', path, '--workers', '2'])
        assert daemons == [True, True]

    def test_slow_result_still_applied(self, runner, tmp_path):
        """A result arriving after the supervisor's 0.5 s wait is still taken."""
        path = _make_project(str(tmp_path / 'p'), n=2)

        def respond(request):
            _time.sleep(0.8)
            return '<p>譯文</p>'

        with use_fake_backend(FakeBackend(responder=respond)):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', '2'])
        assert result.exit_code == 0, result.output
        assert set(_statuses(path).values()) == {SegmentStatus.TRANSLATED}

    def test_supervisor_failure_output(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'), n=2)
        both = threading.Barrier(2)

        def respond(request):
            both.wait(10)
            return '<p>譯文</p>'

        with use_fake_backend(FakeBackend(responder=respond)), \
                patch.object(ProjectManager, 'save', side_effect=OSError('disk full')):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', '2'])
        lines = result.output.splitlines()
        assert '翻譯中斷，等待進行中的段落結束……' in lines
        assert lines[-1] in (
            '未儲存的結果（2 個段落，下次 start 會重新翻譯）：c1_seg0001, c1_seg0002',
            '未儲存的結果（2 個段落，下次 start 會重新翻譯）：c1_seg0002, c1_seg0001')

    def test_extraction_stop_line(self, runner, tmp_path, monkeypatch):
        monkeypatch.setattr('src.glossary.term_extractor.MAX_CHARS_PER_BATCH', 20)
        path = _make_project(str(tmp_path / 'p'), n=3)
        with use_fake_backend(FakeBackend(['[]', RateLimitError('limit')])):
            result = runner.invoke(cli, ['glossary', 'extract', '--project', path])
        assert '提取中斷：limit' in result.output.splitlines()


# --- Review round 2 ---------------------------------------------------------------

class TestRound2WorkerStartup:
    """Item 3: workers that started are stopped and joined when a later start
    fails or is interrupted, before the lock is released."""

    @pytest.mark.parametrize('error', [RuntimeError("can't start new thread"),
                                       KeyboardInterrupt()])
    def test_later_start_fails(self, runner, tmp_path, monkeypatch, error):
        path = _make_project(str(tmp_path / 'p'), n=4)
        started, workers = [], []
        in_flight = threading.Event()
        real_start = threading.Thread.start

        def start(self):
            if getattr(self, '_target', None) is not None and \
                    getattr(self._target, '__name__', '') == 'worker':
                started.append(self)
                if len(started) == 2:
                    assert in_flight.wait(10)
                    raise error
            real_start(self)

        def respond(request):
            workers.append(threading.current_thread())
            in_flight.set()
            _time.sleep(0.3)  # still in flight when the second start fails
            return '<p>譯文</p>'

        monkeypatch.setattr(threading.Thread, 'start', start)
        with use_fake_backend(FakeBackend(responder=respond)):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', '3'])
        lock_free = lock_free_for_other_process(path)
        assert result.exit_code != 0
        assert workers and not any(t.is_alive() for t in workers)
        assert lock_free
        assert '翻譯中斷，等待進行中的段落結束' in result.output
