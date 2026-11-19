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

        terms = [e.term for e in found]
        assert 'machine learning' in terms or 'deep learning' in terms
        assert 'database' not in terms

    def test_find_terms_whole_word_only(self, gm):
        gm.add_term('AI', '人工智慧')
        assert gm.find_terms_in_text('We maintain the main trail.') == []
        assert [e.term for e in gm.find_terms_in_text('Modern AI, explained.')] == ['AI']
        # All-caps terms are matched case-sensitively (decision 5)
        assert gm.find_terms_in_text('the ai boom') == []

    def test_find_terms_plural_forms(self, gm):
        gm.add_term('API', '應用程式介面')
        gm.add_term('batch', '批次')
        gm.add_term('dependency', '相依性')
        gm.add_term('key', '鍵')

        def found(text):
            return [e.term for e in gm.find_terms_in_text(text)]

        assert found('Public APIs change.') == ['API']
        assert found('Two batches ran.') == ['batch']
        assert found('Too many dependencies.') == ['dependency']
        assert found('Rotate the keys.') == ['key']
        assert found('One dependency.') == ['dependency']
        assert found('A rapid keystroke.') == []

    def test_find_terms_with_symbols(self, gm):
        gm.add_term('C++', 'C++')
        gm.add_term('C#', 'C#')
        gm.add_term('.NET', '.NET')

        def found(text):
            return [e.term for e in gm.find_terms_in_text(text)]

        assert found('Written in C++, mostly.') == ['C++']
        assert found('Ported to C# later.') == ['C#']
        assert found('Runs on .NET today.') == ['.NET']
        assert found('The (.NET) runtime and (C++) code.') == ['.NET', 'C++']
        assert found('Send a magnet or abc# tag.') == []

    def test_find_terms_inflected_forms(self, gm):
        for term in ('render', 'deploy', 'refactor', 'cache', 'debug',
                     'query', 'manage'):
            gm.add_term(term, term)

        def found(text):
            return [e.term for e in gm.find_terms_in_text(text)]

        for word in ('rendering', 'rendered', 'renderer', 'renderers', 'renders'):
            assert found(f'The {word} step.') == ['render'], word
        for word in ('deployment', 'deployments', 'deployed', 'deploying', 'deployer'):
            assert found(f'A {word} here.') == ['deploy'], word
        assert found('Some refactoring.') == ['refactor']
        for word in ('caching', 'cached', 'cacher', 'caches'):
            assert found(f'The {word} layer.') == ['cache'], word
        for word in ('debugging', 'debugged', 'debugger', 'debuggers', 'debugs'):
            assert found(f'A {word} session.') == ['debug'], word
        assert found('Two queries ran.') == ['query']
        assert found('Good management.') == ['manage']
        # Not inflections: other words that merely start with the term
        assert found('Renderman, deployx, cachet, debugx.') == []
        assert found('A cacheing typo.') == []

    def test_four_letter_terms_take_inflections(self, gm):
        gm.add_term('load', '載入')
        for word in ('loading', 'loaded', 'loader', 'loaders'):
            assert [e.term for e in gm.find_terms_in_text(f'The {word}.')] == ['load'], word

    def test_suffix_lists_are_complete(self, gm):
        gm.add_term('cache', '快取')
        gm.add_term('manage', '管理')
        gm.add_term('deploy', '部署')

        def found(text):
            return [e.term for e in gm.find_terms_in_text(text)]

        assert found('Two cachers.') == ['cache']
        assert found('Two managements.') == ['manage']
        assert found('Two deployments.') == ['deploy']

    def test_no_doubling_unless_vowel_then_consonant(self, gm):
        gm.add_term('build', '建置')
        gm.add_term('deploy', '部署')
        gm.add_term('render', '算繪')

        def found(text):
            return [e.term for e in gm.find_terms_in_text(text)]

        assert found('A buildding step.') == []   # consonant before final consonant
        assert found('A deployying step.') == []  # final y is never doubled
        gm.add_term('debug', '除錯')
        assert found('A debugging step.') == ['debug']

    def test_terms_not_ending_in_a_letter(self, gm):
        gm.add_term('Web3', 'Web3')
        gm.add_term('C', 'C')

        def found(text):
            return [e.term for e in gm.find_terms_in_text(text)]

        assert found('Web3 apps and C code.') == ['C', 'Web3']
        assert found('Some Web3s.') == []

    def test_y_terms_by_length(self, gm):
        gm.add_term('y', 'y')
        gm.add_term('xy', 'xy')

        def found(text):
            return [e.term for e in gm.find_terms_in_text(text)]

        assert found('An ies file.') == []
        assert found('Several xies.') == ['xy']

    def test_all_caps_multi_word_term(self, gm):
        gm.add_term('ABC DEF', 'ABC DEF')
        assert [e.term for e in gm.find_terms_in_text('the ABC\nDEF unit')] == ['ABC DEF']
        assert gm.find_terms_in_text('the abc def unit') == []

    def test_short_terms_take_plurals_only(self, gm):
        gm.add_term('Go', 'Go')
        gm.add_term('tag', '標籤')

        def found(text):
            return [e.term for e in gm.find_terms_in_text(text)]

        assert found('We are going to say good things.') == []
        assert found('Tagged and tagging and tagger.') == []
        assert found('Two tags and Go.') == ['Go', 'tag']

    def test_all_caps_terms_are_case_sensitive(self, gm):
        gm.add_term('IT', '資訊科技')
        gm.add_term('US', '美國')
        gm.add_term('API', '應用程式介面')

        def found(text):
            return [e.term for e in gm.find_terms_in_text(text)]

        assert found('Its value is it. It uses us.') == []
        assert found('The IT team in the US.') == ['IT', 'US']
        assert found('Public APIs.') == ['API']
        assert found('An api call.') == []

    def test_mixed_case_terms_are_case_insensitive(self, gm):
        gm.add_term('Kubernetes', 'Kubernetes')
        assert [e.term for e in gm.find_terms_in_text('kubernetes pods')] == ['Kubernetes']

    def test_multi_word_terms_match_any_whitespace(self, gm):
        gm.add_term('machine learning', '機器學習')

        def found(text):
            return [e.term for e in gm.find_terms_in_text(text)]

        assert found('About machine\nlearning.') == ['machine learning']
        assert found('About machine\xa0learning.') == ['machine learning']
        assert found('About machine  learning.') == ['machine learning']
        assert found('About machinelearning.') == []

    def test_dotnet_not_matched_inside_aspnet(self, gm):
        # Documented limitation: '.' is preceded by a word character
        gm.add_term('.NET', '.NET')
        assert gm.find_terms_in_text('Built on ASP.NET Core.') == []

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
        with patch('src.glossary.term_extractor.subprocess.run', return_value=mock_result):
            terms = extractor.extract_from_text('Text about APIs')

        assert len(terms) == 1
        assert terms[0]['term'] == 'API'

    def test_extract_handles_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = 'error'

        extractor = TermExtractor()
        with patch('src.glossary.term_extractor.subprocess.run', return_value=mock_result):
            terms = extractor.extract_from_text('Some text')

        assert terms == []

    def test_extract_from_segments_deduplicates(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '[{"term": "API", "translation": "介面", "category": "term"}]'

        extractor = TermExtractor()
        segments = [{'text': 'First batch'}, {'text': 'Second batch'}]

        with patch('src.glossary.term_extractor.subprocess.run', return_value=mock_result):
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

        with patch('src.glossary.term_extractor.subprocess.run',
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

        with patch('src.glossary.term_extractor.subprocess.run',
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
        with patch('src.glossary.term_extractor.subprocess.run') as mock_run:
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

        with patch('src.glossary.term_extractor.subprocess.run',
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
        with patch('src.glossary.term_extractor.subprocess.run', return_value=mock_result):
            with pytest.raises(ExtractionError, match='認證已過期'):
                extractor.extract_from_text('Some text')
