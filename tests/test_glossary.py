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
