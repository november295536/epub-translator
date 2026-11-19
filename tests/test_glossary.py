"""Tests for glossary management and term extraction."""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from src.glossary.glossary_manager import GlossaryManager, GlossaryEntry
from src.glossary.term_extractor import TermExtractor, ExtractionError


class TestGlossaryManager:

    @pytest.fixture
    def gm(self, tmp_path):
        path = str(tmp_path / 'glossary.json')
        return GlossaryManager(path)

    def test_add_and_get_term(self, gm):
        gm.add_term('machine learning', '機器學習', category='term')
        entry = gm.get_term('machine learning')

        assert entry is not None
        assert entry.term == 'machine learning'
        assert entry.translation == '機器學習'
        assert entry.category == 'term'

    def test_case_insensitive_lookup(self, gm):
        gm.add_term('API', '應用程式介面')
        assert gm.get_term('api') is not None
        assert gm.get_term('API') is not None

    def test_remove_term(self, gm):
        gm.add_term('test', '測試')
        assert gm.remove_term('test') is True
        assert gm.get_term('test') is None

    def test_remove_nonexistent(self, gm):
        assert gm.remove_term('nonexistent') is False

    def test_update_term(self, gm):
        gm.add_term('container', '容器')
        updated = gm.update_term('container', translation='貨櫃')
        assert updated is not None
        assert updated.translation == '貨櫃'

    def test_update_nonexistent(self, gm):
        assert gm.update_term('nonexistent', translation='x') is None

    def test_get_all_terms_sorted(self, gm):
        gm.add_term('Zlib', '壓縮庫')
        gm.add_term('API', '介面')
        gm.add_term('mutex', '互斥鎖')

        entries = gm.get_all_terms()
        assert len(entries) == 3
        assert entries[0].term == 'API'
        assert entries[1].term == 'mutex'
        assert entries[2].term == 'Zlib'

    def test_save_and_load(self, gm):
        gm.add_term('thread', '執行緒', category='term')
        gm.add_term('cache', '快取', category='term')

        gm2 = GlossaryManager(gm.glossary_path)
        gm2.load()

        assert len(gm2.entries) == 2
        assert gm2.get_term('thread').translation == '執行緒'

    def test_load_nonexistent_file(self, tmp_path):
        gm = GlossaryManager(str(tmp_path / 'missing.json'))
        gm.load()  # Should not raise
        assert len(gm.entries) == 0

    def test_find_terms_in_text(self, gm):
        gm.add_term('machine learning', '機器學習')
        gm.add_term('deep learning', '深度學習')
        gm.add_term('database', '資料庫')

        text = 'This chapter discusses machine learning and deep learning algorithms.'
        found = gm.find_terms_in_text(text)

        assert [e.term for e in found] == ['deep learning', 'machine learning']

    def _found(self, gm, text):
        return [e.term for e in gm.find_terms_in_text(text)]

    def test_term_must_start_at_a_word_boundary(self, gm):
        gm.add_term('AI', '人工智慧')
        assert self._found(gm, 'We maintain the main trail.') == []
        assert self._found(gm, 'Modern AI, explained.') == ['AI']
        assert self._found(gm, 'the ai boom') == ['AI']  # case-insensitive

    def test_any_continuation_matches(self, gm):
        gm.add_term('API', '應用程式介面')
        gm.add_term('render', '算繪')
        assert self._found(gm, 'Public APIs change.') == ['API']
        assert self._found(gm, 'The rendering step.') == ['render']

    def test_terms_with_symbols(self, gm):
        gm.add_term('C++', 'C++')
        gm.add_term('C#', 'C#')
        gm.add_term('.NET', '.NET')
        assert self._found(gm, 'Written in C++, mostly.') == ['C++']
        assert self._found(gm, 'Ported to C# later.') == ['C#']
        assert self._found(gm, 'Runs on .NET today.') == ['.NET']
        assert self._found(gm, 'The (.NET) runtime and (C++) code.') == ['.NET', 'C++']
        # Accepted limitation: '.' is preceded by a word character
        assert self._found(gm, 'Built on ASP.NET Core.') == []

    def test_multi_word_terms_match_any_whitespace(self, gm):
        gm.add_term('machine learning', '機器學習')
        assert self._found(gm, 'About machine\nlearning.') == ['machine learning']
        assert self._found(gm, 'About machine\xa0learning.') == ['machine learning']
        assert self._found(gm, 'About machine  learning.') == ['machine learning']
        assert self._found(gm, 'About machinelearning.') == []

    @pytest.mark.parametrize('term', ['', '   '])
    def test_empty_term_never_matches(self, gm, term):
        gm.entries[term] = GlossaryEntry(term=term, translation='x')
        assert gm.find_terms_in_text('any text at all') == []

    def test_small_glossary_gives_every_entry_to_the_prompt(self, gm):
        from src.glossary.glossary_manager import FULL_GLOSSARY_MAX_TERMS
        assert FULL_GLOSSARY_MAX_TERMS == 150
        for i in range(FULL_GLOSSARY_MAX_TERMS):
            gm.entries[f'term{i:03}'] = GlossaryEntry(term=f'term{i:03}', translation='x')
        terms = [e.term for e in gm.terms_for_prompt('unrelated text')]
        assert terms == sorted(gm.entries)

    def test_large_glossary_gives_matching_entries_to_the_prompt(self, gm):
        from src.glossary.glossary_manager import FULL_GLOSSARY_MAX_TERMS
        for i in range(FULL_GLOSSARY_MAX_TERMS):
            gm.entries[f'term{i:03}'] = GlossaryEntry(term=f'term{i:03}', translation='x')
        gm.entries['ai'] = GlossaryEntry(term='AI', translation='人工智慧')  # 151 entries
        assert [e.term for e in gm.terms_for_prompt('We maintain term007s.')] == ['term007']

    def test_format_for_prompt(self, gm):
        gm.add_term('API', '應用程式介面', category='term')
        gm.add_term('cache', '快取')

        entries = gm.get_all_terms()
        formatted = gm.format_for_prompt(entries)

        assert 'API → 應用程式介面 [術語]' in formatted
        assert 'cache → 快取' in formatted

    def test_format_empty(self, gm):
        assert gm.format_for_prompt([]) == ''

    def test_import_json(self, gm, tmp_path):
        import_data = [
            {'term': 'thread', 'translation': '執行緒', 'category': 'term'},
            {'term': 'socket', 'translation': '套接字', 'category': 'term'},
        ]
        import_path = str(tmp_path / 'import.json')
        with open(import_path, 'w') as f:
            json.dump(import_data, f)

        count = gm.import_json(import_path)
        assert count == 2
        assert gm.get_term('thread') is not None
        assert gm.get_term('socket') is not None

    def test_export_json(self, gm, tmp_path):
        gm.add_term('API', '介面')
        gm.add_term('cache', '快取')

        export_path = str(tmp_path / 'export.json')
        count = gm.export_json(export_path)

        assert count == 2
        with open(export_path, 'r') as f:
            data = json.load(f)
        assert len(data) == 2


class TestTermExtractor:

    def test_parse_valid_json(self):
        output = '[{"term": "API", "translation": "介面", "category": "term"}]'
        result = TermExtractor._parse_response(output)
        assert len(result) == 1
        assert result[0]['term'] == 'API'

    def test_parse_with_markdown_fences(self):
        output = '```json\n[{"term": "API", "translation": "介面"}]\n```'
        result = TermExtractor._parse_response(output)
        assert len(result) == 1

    def test_parse_with_surrounding_text(self):
        output = 'Here are the terms:\n[{"term": "API", "translation": "介面"}]\nDone.'
        result = TermExtractor._parse_response(output)
        assert len(result) == 1

    def test_parse_invalid_json(self):
        result = TermExtractor._parse_response('not json at all')
        assert result == []

    def test_parse_filters_incomplete(self):
        output = '[{"term": "API", "translation": "介面"}, {"term": "bad entry"}]'
        result = TermExtractor._parse_response(output)
        assert len(result) == 1

    def test_extract_from_text_calls_claude(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '[{"term": "API", "translation": "介面", "category": "term"}]'

        extractor = TermExtractor()
        with patch('src.llm.cli.run_process', return_value=mock_result):
            terms = extractor.extract_from_text('Text about APIs')

        assert len(terms) == 1
        assert terms[0]['term'] == 'API'

    def test_extract_handles_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = 'error'

        extractor = TermExtractor()
        with patch('src.llm.cli.run_process', return_value=mock_result):
            terms = extractor.extract_from_text('Some text')

        assert terms == []

    def test_extract_from_segments_deduplicates(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '[{"term": "API", "translation": "介面", "category": "term"}]'

        extractor = TermExtractor()
        segments = [{'text': 'First batch'}, {'text': 'Second batch'}]

        with patch('src.llm.cli.run_process', return_value=mock_result):
            terms = extractor.extract_from_segments(segments)

        # Same term from both batches should be deduplicated
        assert len(terms) == 1

    def test_batch_separator_counted_in_batch_size(self):
        """Joined batch text must fit the per-call limit, so no tail is cut off."""
        from src.glossary.term_extractor import MAX_CHARS_PER_BATCH

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '[]'

        chunk = MAX_CHARS_PER_BATCH // 5
        segments = [{'text': 'a' * chunk} for _ in range(4)]
        segments.append({'text': 'a' * (chunk - 8) + 'TAILTERM'})

        with patch('src.llm.cli.run_process',
                   return_value=mock_result) as mock_run:
            TermExtractor().extract_from_segments(segments)

        prompts = [c[0][0][2] for c in mock_run.call_args_list]
        assert any('TAILTERM' in p for p in prompts)

    @staticmethod
    def _sent_texts(mock_run):
        marker = '文本：\n'
        return [c[0][0][2].split(marker, 1)[1] for c in mock_run.call_args_list]

    def test_oversized_segment_split_without_loss(self):
        """A single text longer than the batch limit is split, not truncated."""
        from src.glossary.term_extractor import MAX_CHARS_PER_BATCH

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '[]'

        words = ' '.join(f'word{i}' for i in range(2000))
        text = words[:6000] + ' BEYONDLIMIT ' + words[6000:12000] + ' ENDTERM'
        assert text.index('BEYONDLIMIT') > MAX_CHARS_PER_BATCH

        with patch('src.llm.cli.run_process',
                   return_value=mock_result) as mock_run:
            TermExtractor().extract_from_segments(
                [{'text': 'Short first segment.'}, {'text': text}])

        sent = self._sent_texts(mock_run)
        assert all(len(t) <= MAX_CHARS_PER_BATCH for t in sent)
        joined = ''.join(sent)
        assert 'BEYONDLIMIT' in joined and 'ENDTERM' in joined
        assert any('BEYONDLIMIT' in t for t in sent)
        # Lossless: every character of the long text was sent, in order
        assert joined.replace('\n\n', '').endswith(text)

    def test_split_text_is_lossless_and_bounded(self):
        from src.glossary.term_extractor import _split_text
        text = ('alpha beta gamma ' * 50) + 'x' * 120 + ' tail'
        chunks = _split_text(text, 100)
        assert ''.join(chunks) == text
        assert all(0 < len(c) <= 100 for c in chunks)
        # Breaks after whitespace where there is some
        assert chunks[0].endswith(' ')
        assert _split_text('short', 100) == ['short']
        assert _split_text('', 100) == []

    def test_split_text_boundaries(self):
        from src.glossary.term_extractor import _split_text
        assert _split_text('ab cd', 5) == ['ab cd']                 # exactly at the limit
        assert _split_text('aaaa\nbbbb', 6) == ['aaaa\n', 'bbbb']  # newline as break
        assert _split_text('aaaa\tbbbb', 6) == ['aaaa\t', 'bbbb']  # tab as break
        assert _split_text(' abcdef', 4) == [' ', 'abcd', 'ef']     # break after index 0

    def test_segment_without_text_sends_nothing(self):
        with patch('src.llm.cli.run_process') as mock_run:
            assert TermExtractor().extract_from_segments([{}]) == []
        mock_run.assert_not_called()

    def test_batch_filled_exactly_to_limit_is_one_call(self):
        from src.glossary.term_extractor import MAX_CHARS_PER_BATCH
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '[]'
        half = (MAX_CHARS_PER_BATCH - 2) // 2
        segments = [{'text': 'a' * half}, {'text': 'b' * half}]
        assert len('\n\n'.join(s['text'] for s in segments)) == MAX_CHARS_PER_BATCH

        with patch('src.llm.cli.run_process',
                   return_value=mock_result) as mock_run:
            TermExtractor().extract_from_segments(segments)

        assert mock_run.call_count == 1

    def test_extraction_error_propagates(self):
        """ExtractionError (e.g. auth error) should not be swallowed by broad except."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ''
        mock_result.stderr = 'authentication_error: OAuth token has expired'

        extractor = TermExtractor()
        with patch('src.llm.cli.run_process', return_value=mock_result):
            with pytest.raises(ExtractionError, match='認證已過期'):
                extractor.extract_from_text('Some text')


class TestTermExtractorBackend:
    """Term extraction goes through the backend (Phase 1)."""

    def _fake(self, replies):
        from src.llm.fake import FakeBackend
        return FakeBackend(replies)

    def test_model_and_timeout_in_request(self):
        fake = self._fake(['[]'])
        TermExtractor(model='haiku', timeout=99, backend=fake).extract_from_text('Some text')
        request = fake.requests[0]
        assert (request.model, request.timeout, request.system) == ('haiku', 99, '')
        assert 'Some text' in request.messages[0].content

    def test_claude_cli_command_has_model_and_clean_environment(self, monkeypatch):
        import tempfile
        monkeypatch.setenv('CLAUDECODE', '1')
        mock_result = MagicMock(returncode=0, stdout='[]', stderr='')
        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            TermExtractor(model='opus').extract_from_text('Some text')
        cmd = mock_run.call_args[0][0]
        assert cmd[:2] == ['claude', '-p'] and cmd[3:] == ['--model', 'opus']
        assert 'CLAUDECODE' not in mock_run.call_args[1]['env']
        assert mock_run.call_args[1]['cwd'] == tempfile.gettempdir()

    def test_without_model_no_model_flag(self):
        mock_result = MagicMock(returncode=0, stdout='[]', stderr='')
        with patch('src.llm.cli.run_process', return_value=mock_result) as mock_run:
            TermExtractor().extract_from_text('Some text')
        assert mock_run.call_args[0][0][3:] == []

    def test_rate_limit_stops_extraction(self):
        from src.llm import RateLimitError
        fake = self._fake([RateLimitError("You've hit your limit")])
        with pytest.raises(ExtractionError, match='hit your limit'):
            TermExtractor(backend=fake).extract_from_text('Some text')

    def test_auth_error_stops_extraction(self):
        from src.llm import AuthError
        fake = self._fake([AuthError('login expired')])
        with pytest.raises(ExtractionError, match='login expired'):
            TermExtractor(backend=fake).extract_from_text('Some text')

    @pytest.mark.parametrize('error_name', ['TransientError', 'RefusalError', 'LLMError'])
    def test_other_errors_give_no_terms(self, error_name):
        import src.llm as llm
        fake = self._fake([getattr(llm, error_name)('x'), '[{"term": "A", "translation": "甲"}]'])
        extractor = TermExtractor(backend=fake)
        assert extractor.extract_from_text('Some text') == []
        assert extractor.extract_from_text('Some text') == [{'term': 'A', 'translation': '甲'}]

    def test_unparseable_reply_gives_no_terms(self):
        assert TermExtractor(backend=self._fake(['no json here'])).extract_from_text('x') == []

    def test_parse_crash_gives_no_terms(self):
        fake = self._fake(['[]'])
        with patch.object(TermExtractor, '_parse_response', side_effect=TypeError('bad')):
            assert TermExtractor(backend=fake).extract_from_text('x') == []


class TestTermExtractorRequest:

    def test_default_timeout_and_one_user_message(self):
        from src.llm import Message
        from src.llm.fake import FakeBackend
        from src.glossary.term_extractor import EXTRACT_PROMPT
        fake = FakeBackend(['[]'])
        TermExtractor(backend=fake).extract_from_text('Some text')
        request = fake.requests[0]
        assert request.timeout == 180
        assert request.system == ''
        assert request.messages == [Message('user', EXTRACT_PROMPT.format(text='Some text'))]

    def test_failure_logged(self, caplog):
        import logging
        from src.llm import TransientError
        from src.llm.fake import FakeBackend
        caplog.set_level(logging.WARNING, logger='src.glossary.term_extractor')
        TermExtractor(backend=FakeBackend([TransientError('boom')])).extract_from_text('x')
        assert [r.getMessage() for r in caplog.records] == ['Term extraction failed: boom']
