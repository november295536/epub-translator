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
            raw = json.load(f)
        # Phase 2: schema 2 (was segmenter_version 2), and the project owns
        # a copy of the book whose hash it records
        assert raw['schema'] == 2
        assert 'segmenter_version' not in raw
        copy = os.path.join(full_path, 'source.epub')
        assert raw['epub_path'] == copy
        with open(copy, 'rb') as a, open(sample_epub, 'rb') as b:
            data = a.read()
            assert data == b.read()
        import hashlib
        assert raw['source_sha256'] == hashlib.sha256(data).hexdigest()

    def test_init_reports_unparsed_chapter(self, runner, tmp_path, monkeypatch):
        from tests.conftest import write_raw_epub, xhtml
        monkeypatch.chdir(tmp_path)
        items = [('a', 'a.xhtml', 'application/xhtml+xml', None),
                 ('b', 'b.xhtml', 'application/xhtml+xml', None)]
        members = {'OEBPS/a.xhtml': xhtml('<p>The tide came in.</p>'),
                   'OEBPS/b.xhtml': xhtml('<p>Unclosed paragraph.</div>')}
        epub_path = write_raw_epub(tmp_path / 'bad.epub', items, ['a', 'b'], members)

        result = runner.invoke(cli, ['init', epub_path])

        assert result.exit_code == 0, result.output
        unparsed = [line for line in result.output.splitlines() if 'not well-formed' in line]
        assert len(unparsed) == 1
        assert unparsed[0].startswith('無法解析的章節（不分段）：b：not well-formed XML')
        assert unparsed[0].endswith(')')  # the parser's message, nothing appended
        assert '分段完成：共 1 個段落' in result.output

    def test_init_unreadable_epub_gives_a_message(self, runner, tmp_path, monkeypatch):
        # Review round 1: one clear error, no traceback, no copy left behind
        monkeypatch.chdir(tmp_path)
        bad = tmp_path / 'broken.epub'
        bad.write_bytes(b'not a zip archive')
        result = runner.invoke(cli, ['init', str(bad)])
        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert result.output.splitlines()[-1] == '無法解析 EPUB：無法讀取 EPUB：File is not a zip file'
        assert os.listdir(tmp_path / _get_project_dir(str(bad))) == []

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

        with patch('src.llm.cli.run_process', side_effect=echo_process):
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

        with patch('src.llm.cli.run_process', side_effect=echo_process) as mock_run:
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

        with patch('src.llm.cli.run_process', side_effect=echo_process) as mock_run:
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

        with patch('src.llm.cli.run_process', side_effect=echo_process):
            result = runner.invoke(cli, ['start', '--project', full_path, '--workers', '3'])

        assert result.exit_code == 0
        assert '並行 Workers：3' in result.output
        assert '完成' in result.output

    def test_start_default_workers_is_1(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        runner.invoke(cli, ['init', sample_epub])
        project_dir = _get_project_dir(sample_epub)
        full_path = os.path.join(str(tmp_path), project_dir)

        with patch('src.llm.cli.run_process', side_effect=echo_process):
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
from tests.conftest import (
    echo_process, hold_lock_in_child, lock_free_for_other_process, use_fake_backend,
)


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
            if f'Sentence {mark}.</p>' in prompt:
                raise error
        return '<p k="1">譯文</p>'
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
        # 4 calls for the failing segment (Phase 4), 1 for each other
        assert len(fake.requests) == 7

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
        assert '<p k="1">Sentence MARK2.</p>' in fake.prompts[0]
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
            return '<p k="1">譯文</p>'

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

    FAIL = 'Translation failed after 4 attempts: exit 1'

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
            if 'Sentence MARK4.</p>' in request.messages[-1].content:
                _time.sleep(1)  # still in flight when the save fails
            return '<p k="1">譯文</p>'

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
            return '<p k="1">譯文</p>'

        with use_fake_backend(FakeBackend(responder=respond)):
            runner.invoke(cli, ['start', '--project', path, '--workers', '2'])
        assert daemons == [True, True]

    def test_slow_result_still_applied(self, runner, tmp_path):
        """A result arriving after the supervisor's 0.5 s wait is still taken."""
        path = _make_project(str(tmp_path / 'p'), n=2)

        def respond(request):
            _time.sleep(0.8)
            return '<p k="1">譯文</p>'

        with use_fake_backend(FakeBackend(responder=respond)):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', '2'])
        assert result.exit_code == 0, result.output
        assert set(_statuses(path).values()) == {SegmentStatus.TRANSLATED}

    def test_supervisor_failure_output(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'), n=2)
        both = threading.Barrier(2)

        def respond(request):
            both.wait(10)
            return '<p k="1">譯文</p>'

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
            return '<p k="1">譯文</p>'

        monkeypatch.setattr(threading.Thread, 'start', start)
        with use_fake_backend(FakeBackend(responder=respond)):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', '3'])
        lock_free = lock_free_for_other_process(path)
        assert result.exit_code != 0
        assert workers and not any(t.is_alive() for t in workers)
        assert lock_free
        assert '翻譯中斷，等待進行中的段落結束' in result.output


# --- Review round 3 ---------------------------------------------------------------

import threading as _threading

from src.core.translator import UnexpectedStreak

STREAK_MESSAGE = ("連續 5 個段落發生未預期的錯誤，翻譯已停止"
                  "（最後一個：未預期的錯誤：KeyError: 'missing'）")


class TestCLIRound3UnexpectedStreak:
    """Item 9: five unexpected failures in a row stop the CLI run (exit 1)."""

    def test_sequential(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'), n=8)
        fake = FakeBackend(responder=lambda r: {}['missing'])
        with use_fake_backend(fake):
            result = runner.invoke(cli, ['start', '--project', path])
        assert result.exit_code == 1, result.output
        assert f'  ✗ {STREAK_MESSAGE}\n翻譯中斷。' in result.output
        assert len(fake.requests) == 5
        statuses = list(_statuses(path).values())
        assert statuses == [SegmentStatus.FAILED] * 5 + [SegmentStatus.PENDING] * 3

    def test_sequential_reset_by_success(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'), n=9)

        def respond(request):
            if '<p k="1">Sentence MARK5.</p>' in request.messages[-1].content:
                return '<p k="1">譯文</p>'
            return {}['missing']

        with use_fake_backend(FakeBackend(responder=respond)):
            result = runner.invoke(cli, ['start', '--project', path])
        assert result.exit_code == 1
        assert '8 個段落翻譯失敗' in result.output
        assert '連續' not in result.output

    def test_parallel(self, runner, tmp_path, monkeypatch):
        path = _make_project(str(tmp_path / 'p'), n=8)
        fired = _threading.Event()
        real_add = UnexpectedStreak.add

        def add(self, outcome):
            message = real_add(self, outcome)
            if message:
                fired.set()
            return message

        monkeypatch.setattr(UnexpectedStreak, 'add', add)
        calls = []
        guard = _threading.Lock()

        def respond(request):
            with guard:
                calls.append(1)
                n = len(calls)
            if n > 5:
                assert fired.wait(10)  # held until the run has been stopped
            return {}['missing']

        with use_fake_backend(FakeBackend(responder=respond)):
            result = runner.invoke(cli, ['start', '--project', path, '--workers', '2'])
        assert result.exit_code == 1, result.output
        assert f'  ✗ {STREAK_MESSAGE}\n翻譯中斷。' in result.output
        assert len(calls) <= 7
        statuses = _statuses(path)
        assert list(statuses.values()).count(SegmentStatus.FAILED) >= 5
        assert statuses['c1_seg0008'] == SegmentStatus.PENDING


class TestCLIPhase2:
    """Phase 2: init into an existing directory, export file name, migrate options."""

    def test_init_into_existing_empty_directory(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs(_get_project_dir(sample_epub))
        result = runner.invoke(cli, ['init', sample_epub])
        assert result.exit_code == 0, result.output
        assert os.path.isfile(os.path.join(_get_project_dir(sample_epub), 'project.json'))

    def test_export_named_after_the_project(self, runner, sample_epub, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ['init', sample_epub])
        project_dir = _get_project_dir(sample_epub)
        pm = ProjectManager(project_dir)
        pm.load()
        pm.mark_translated(pm.data.segments[0].segment_id, '<h1>譯</h1>')
        pm.save()
        result = runner.invoke(cli, ['export', '-p', project_dir, '-f', 'chinese-only'])
        assert result.exit_code == 0, result.output
        name = os.path.basename(project_dir)
        assert os.listdir(os.path.join(project_dir, 'output')) == [f'{name}-chinese-only.epub']

    def test_schema_1_export_asks_for_migration(self, runner, sample_epub, tmp_path):
        # Phase 3 (was test_schema_1_export_keeps_the_book_name): the old
        # builder is gone; a schema 1 project must be migrated first
        from src.core.segmenter import Segmenter
        from src.epub.exporter import SCHEMA_1_MESSAGE
        from src.epub.extractor import EPUBExtractor
        ex = EPUBExtractor(sample_epub)
        ex.load()
        segments = [s for ch in ex.extract_chapters()
                    for s in Segmenter().segment_chapter(ch.id, ch.body)]
        project_dir = str(tmp_path / 'review-copy')
        pm = ProjectManager(project_dir)
        pm.create(sample_epub, 'T', 'A', segments)
        pm.mark_translated(pm.data.segments[0].segment_id, '<h1>譯</h1>')
        pm.save()
        with open(pm.project_file, encoding='utf-8') as f:
            raw = json.load(f)
        for key in ('schema', 'source_sha256'):
            raw.pop(key, None)
        for seg in raw['segments']:
            for key in ('block_start', 'block_end', 'fingerprints'):
                seg.pop(key, None)
        with open(pm.project_file, 'w', encoding='utf-8') as f:
            json.dump(raw, f)
        result = runner.invoke(cli, ['export', '-p', project_dir, '-f', 'chinese-only'])
        assert result.exit_code == 1
        assert result.output == SCHEMA_1_MESSAGE + '\n'
        assert 'translate.py migrate' in SCHEMA_1_MESSAGE
        assert not os.path.exists(os.path.join(project_dir, 'output'))

    @pytest.mark.parametrize('make', ['missing', 'file'])
    def test_migrate_needs_a_directory(self, runner, tmp_path, make):
        path = tmp_path / 'p'
        if make == 'file':
            path.write_text('x')
        result = runner.invoke(cli, ['migrate', str(path)])
        assert result.exit_code == 2
        assert ('does not exist' if make == 'missing' else 'is a file') in result.output

    def test_migrate_help(self, runner):
        result = runner.invoke(cli, ['migrate', '--help'])
        assert '--dry-run  Only report; write nothing' in result.output


class TestPhase4Validate:
    """translate.py validate: counts per rule; --mark-failed."""

    def _project(self, tmp_path, translations, statuses=None):
        path = _make_project(str(tmp_path / 'p'), n=4)
        pm = ProjectManager(path)
        pm.load()
        for seg_id, text in translations.items():
            pm.mark_translated(seg_id, text)
        for seg_id, status in (statuses or {}).items():
            pm._find(seg_id).status = status
        pm.save()
        return path

    def test_read_only_report(self, runner, tmp_path):
        path = self._project(tmp_path, {'c1_seg0001': '<p>句子。</p>',
                                        'c1_seg0002': '句子。',
                                        'c1_seg0003': '<p>句<b>子</b>。</p>',
                                        'c1_seg0004': '<p>Sentence MARK4.</p>'},
                             {'c1_seg0003': SegmentStatus.REVIEWED})
        before = open(os.path.join(path, 'project.json'), encoding='utf-8').read()
        result = runner.invoke(cli, ['validate', path])
        assert result.exit_code == 0, result.output
        assert result.output.splitlines() == [
            '已檢查 4 段譯文：2 段未通過結構檢查（匯出時以原文輸出），2 段通過',
            '  structure_mismatch：1',
            '  unknown_tag：1',
            '品質與術語警告（不影響匯出）：',
            '  untranslated：1',
        ]
        assert open(os.path.join(path, 'project.json'), encoding='utf-8').read() == before
        # read-only: works while another process holds the project lock
        child = hold_lock_in_child(path)
        try:
            again = runner.invoke(cli, ['validate', path])
        finally:
            child.stdin.close()
            child.wait()
        assert again.exit_code == 0 and again.output == result.output

    def test_pending_failed_and_empty_not_checked(self, runner, tmp_path):
        # skipped: an empty text, a failed segment with text, a pending one;
        # the translated segment after them is still checked
        path = self._project(tmp_path, {'c1_seg0002': '  ', 'c1_seg0003': '壞',
                                        'c1_seg0004': '<p>句子。</p>'},
                             {'c1_seg0003': SegmentStatus.FAILED})
        result = runner.invoke(cli, ['validate', path])
        assert result.output.splitlines() == ['已檢查 1 段譯文：0 段未通過結構檢查（匯出時以原文輸出），1 段通過']

    def test_mark_failed_then_retry(self, runner, tmp_path):
        path = self._project(tmp_path, {'c1_seg0001': '<p>句子。</p>', 'c1_seg0002': '句子。',
                                        'c1_seg0003': '<p>三。</p>', 'c1_seg0004': '<p>四。</p>'},
                             {'c1_seg0002': SegmentStatus.REVIEWED})
        result = runner.invoke(cli, ['validate', path, '--mark-failed'])
        assert result.exit_code == 0, result.output
        assert result.output.splitlines()[-1] == \
            '已將 1 段標為翻譯失敗（原譯文保留），可用 start --retry-failed 重新翻譯。'
        pm = ProjectManager(path)
        pm.load()
        seg = pm._find('c1_seg0002')
        assert (seg.status, seg.translated_text) == (SegmentStatus.FAILED, '句子。')
        assert [(i.kind, i.code, i.blocking) for i in seg.issues] == \
            [('structure', 'structure_mismatch', True)]
        assert pm._find('c1_seg0001').status == SegmentStatus.TRANSLATED

        # a retry that fails keeps the old text
        with use_fake_backend(FakeBackend(['nope', 'nope'])):
            result = runner.invoke(cli, ['start', '--project', path, '--retry-failed'])
        assert result.exit_code == 1
        pm.load()
        seg = pm._find('c1_seg0002')
        assert (seg.status, seg.translated_text, seg.issues[0].code) == \
            (SegmentStatus.FAILED, '句子。', 'not_html')

        # a retry that succeeds replaces it
        fake = FakeBackend(responder=_responder())
        with use_fake_backend(fake):
            result = runner.invoke(cli, ['start', '--project', path, '--retry-failed'])
        assert result.exit_code == 0, result.output
        assert len(fake.requests) == 1
        pm.load()
        assert pm._find('c1_seg0002').translated_text == '<p>譯文</p>'
        assert runner.invoke(cli, ['validate', path]).output.splitlines()[0] == \
            '已檢查 4 段譯文：0 段未通過結構檢查（匯出時以原文輸出），4 段通過'

    def test_mark_failed_with_nothing_failing_writes_nothing(self, runner, tmp_path):
        path = self._project(tmp_path, {'c1_seg0001': '<p>句子。</p>'})
        before = os.stat(os.path.join(path, 'project.json')).st_mtime_ns
        result = runner.invoke(cli, ['validate', path, '--mark-failed'])
        assert result.exit_code == 0
        assert '標為翻譯失敗' not in result.output
        assert os.stat(os.path.join(path, 'project.json')).st_mtime_ns == before

    def test_mark_failed_refused_while_locked(self, runner, tmp_path):
        path = self._project(tmp_path, {'c1_seg0002': '句子。'})
        child = hold_lock_in_child(path)
        try:
            result = runner.invoke(cli, ['validate', path, '--mark-failed'])
        finally:
            child.stdin.close()
            child.wait()
        assert result.exit_code == 1
        assert '專案正由另一個程序使用' in result.output
        pm = ProjectManager(path)
        pm.load()
        assert pm._find('c1_seg0002').status == SegmentStatus.TRANSLATED
        assert lock_free_for_other_process(path)

    def test_help(self, runner):
        output = ' '.join(runner.invoke(cli, ['validate', '--help']).output.split())
        assert '--mark-failed Set the segments that fail the structural check to failed' in output


class TestRound1Validate:
    """Phase 4 review round 1: validate refuses schema 1 and a missing project."""

    @pytest.mark.parametrize('flag', [[], ['--mark-failed']])
    def test_schema_1_refused(self, runner, tmp_path, flag):
        from tests.test_migrate import _legacy_project
        project_dir = _legacy_project(tmp_path)
        before = open(os.path.join(project_dir, 'project.json'), encoding='utf-8').read()
        files = sorted(os.listdir(project_dir))
        result = runner.invoke(cli, ['validate', str(project_dir), *flag])
        assert result.exit_code == 1
        assert result.output == ('此專案是舊格式（schema 1），無法檢查譯文。'
                                 '請先執行 python3 translate.py migrate <專案目錄> 再檢查。\n')
        assert open(os.path.join(project_dir, 'project.json'), encoding='utf-8').read() == before
        assert sorted(os.listdir(project_dir)) == sorted(files + (['project.lock'] if flag else []))

    @pytest.mark.parametrize('flag', [[], ['--mark-failed']])
    def test_directory_without_project(self, runner, tmp_path, flag):
        empty = tmp_path / 'empty'
        empty.mkdir()
        result = runner.invoke(cli, ['validate', str(empty), *flag])
        assert result.exit_code == 1
        assert result.output == f'找不到專案：{empty} 裡沒有 project.json。\n'
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert os.listdir(empty) == []

    def test_lone_surrogate_is_not_well_formed(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'), n=2)
        pm = ProjectManager(path)
        pm.load()
        pm.mark_translated('c1_seg0001', '<p>句子。</p>')
        pm.mark_translated('c1_seg0002', 'SURROGATE')
        pm.save()
        project_json = os.path.join(path, 'project.json')
        with open(project_json, encoding='utf-8') as f:
            text = f.read()
        with open(project_json, 'w', encoding='utf-8') as f:
            f.write(text.replace('SURROGATE', '<p>\\ud800</p>'))
        result = runner.invoke(cli, ['validate', path])
        assert result.exit_code == 0, result.output
        assert result.output.splitlines()[:2] == [
            '已檢查 2 段譯文：1 段未通過結構檢查（匯出時以原文輸出），1 段通過',
            '  not_well_formed：1']


class TestPhase5ValidateGlossary:
    """translate.py validate counts the glossary warnings with the quality ones."""

    def test_term_not_applied_counted(self, runner, tmp_path):
        from src.glossary.glossary_manager import GlossaryManager
        path = _make_project(str(tmp_path / 'p'), n=3)
        pm = ProjectManager(path)
        pm.load()
        pm.mark_translated('c1_seg0001', '<p>句子 MARK1。</p>')
        pm.mark_translated('c1_seg0002', '<p>語句 MARK2。</p>')
        pm.mark_translated('c1_seg0003', '<p>句子 MARK3.<b>x</b></p>')  # fails: not counted
        pm.save()
        GlossaryManager(os.path.join(path, 'glossary.json')).add_term('sentence', '語句', 'term')
        result = runner.invoke(cli, ['validate', path])
        assert result.exit_code == 0, result.output
        assert result.output.splitlines() == [
            '已檢查 3 段譯文：1 段未通過結構檢查（匯出時以原文輸出），2 段通過',
            '  unknown_tag：1',
            '品質與術語警告（不影響匯出）：',
            '  term_not_applied：1',
        ]

    def test_damaged_glossary(self, runner, tmp_path):
        path = _make_project(str(tmp_path / 'p'), n=1)
        pm = ProjectManager(path)
        pm.load()
        pm.mark_translated('c1_seg0001', '<p>語句 MARK1。</p>')
        pm.save()
        with open(os.path.join(path, 'glossary.json'), 'w', encoding='utf-8') as f:
            f.write('[{"term": ')
        result = runner.invoke(cli, ['validate', path])
        assert result.exit_code == 0, result.output
        assert result.output.splitlines() == [
            '已檢查 1 段譯文：0 段未通過結構檢查（匯出時以原文輸出），1 段通過',
            '術語表無法讀取（glossary.json 損壞或格式不符），未檢查術語。',
        ]
        assert 'Traceback' not in result.output
