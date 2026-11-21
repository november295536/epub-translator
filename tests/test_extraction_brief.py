"""Phase 8: term consolidation, the book brief, person notes in the glossary.

Every model call goes to a FakeBackend; all names and texts are invented.
"""

import json
import logging
import os
from collections import Counter

import pytest
from click.testing import CliRunner

from src.core.project import ProjectLock, ProjectManager
from src.core.segmenter import Segment, SegmentType
from src.core.translator import (
    BRIEF_SECTION, CODE_PROMPT, TEXT_HTML_PROMPT, TRANSLATION_PROMPT, TranslationEngine,
    translate_segment,
)
from src.glossary import term_extractor as te
from src.glossary.glossary_manager import GlossaryEntry, GlossaryManager
from src.glossary.term_extractor import ExtractionError, TermExtractor
from src.llm import AuthError, RateLimitError, TransientError
from src.llm.fake import FakeBackend
from tests.conftest import like, use_fake_backend

BRIEF = 'A cosy mystery novel; light, warm tone; adult general readers.'


def _reply(*terms):
    return json.dumps([{'term': t, 'translation': tr, 'category': c} for t, tr, c in terms],
                      ensure_ascii=False)


def _segments(n):
    """n segments, one extraction batch each (MAX_CHARS_PER_BATCH is set small)."""
    return [{'text': f'batch number {i} text'} for i in range(n)]


@pytest.fixture
def small_batches(monkeypatch):
    monkeypatch.setattr(te, 'MAX_CHARS_PER_BATCH', 20)


def _candidates(prompt):
    """The candidate objects of a consolidation prompt."""
    body = prompt.split('（每行一個 JSON 物件）：\n', 1)[1]
    return [json.loads(line) for line in body.splitlines()]


# --- Consolidation ------------------------------------------------------------------

class TestConsolidation:

    def test_conflicting_candidates_become_one_entry(self, small_batches):
        fake = FakeBackend([
            _reply(('Mira Vale', '米拉·維爾', 'person'), ('lantern guild', '燈籠公會', 'organization')),
            _reply(('Mira Vale', '蜜拉·維爾', 'person')),
            _reply(('mira vale', '米拉·維爾', 'person')),
            _reply(('Mira Vale', '蜜拉·薇爾', 'person')),  # the consolidation's decision
        ])
        terms = TermExtractor(model='m', backend=fake).extract_from_segments(_segments(3))
        assert terms == [
            {'term': 'lantern guild', 'translation': '燈籠公會', 'category': 'organization'},
            {'term': 'Mira Vale', 'translation': '蜜拉·薇爾', 'category': 'person'},
        ]
        assert len(fake.requests) == 4
        consolidation = fake.requests[3]
        assert (consolidation.model, consolidation.timeout, consolidation.system) == ('m', 180, '')
        # only the conflicting term, with every proposal and its count
        assert _candidates(consolidation.messages[0].content) == [
            {'term': 'Mira Vale', 'category': 'person',
             'translations': {'米拉·維爾': 2, '蜜拉·維爾': 1}}]

    def test_prompt_is_the_template(self, small_batches):
        fake = FakeBackend([_reply(('Orrin', '奧林', 'person')), _reply(('Orrin', '歐林', 'person')),
                            '[]'])
        TermExtractor(backend=fake).extract_from_segments(_segments(2))
        line = json.dumps({'term': 'Orrin', 'category': 'person',
                           'translations': {'奧林': 1, '歐林': 1}}, ensure_ascii=False)
        assert fake.prompts[2] == te.CONSOLIDATE_PROMPT.format(candidates=line)

    def test_no_conflict_no_consolidating_call(self, small_batches):
        fake = FakeBackend([_reply(('Orrin', '奧林', 'person')), _reply(('ORRIN', '奧林', 'place'))])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(2))
        assert terms == [{'term': 'Orrin', 'translation': '奧林', 'category': 'person'}]
        assert len(fake.requests) == 2

    @pytest.mark.parametrize('reply', [
        'not json at all',
        '{"term": "Orrin", "translation": "歐林"}',              # not a list
        '[{"term": "Orrin"}]',                                  # no translation
        '[{"term": "Orrin", "translation": ""}]',               # empty translation
        '[{"term": "Orrin", "translation": "  "}]',
        '[{"term": "Orrin", "translation": 7}]',                # not text
        '[{"term": 7, "translation": "歐林"}]',
        '[{"term": "Someone Else", "translation": "歐林"}]',   # not asked for
        TransientError('exit 1'),
    ])
    def test_unusable_reply_keeps_the_most_frequent_proposal(self, small_batches, reply, caplog):
        caplog.set_level(logging.WARNING, logger='src.glossary.term_extractor')
        fake = FakeBackend([_reply(('Orrin', '歐林', 'term'), ('Tess', '泰絲', 'person')),
                            _reply(('Orrin', '奧林', 'person'), ('Tess', '黛絲', 'person')),
                            _reply(('Orrin', '奧林', 'person')),
                            reply])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(3))
        # nothing lost: most frequent translation and category; a tie: the first proposed
        assert terms == [{'term': 'Orrin', 'translation': '奧林', 'category': 'person'},
                         {'term': 'Tess', 'translation': '泰絲', 'category': 'person'}]
        assert len(fake.requests) == 4
        messages = [r.getMessage() for r in caplog.records]
        assert messages[-1] == ('Term extraction failed: exit 1'
                                if isinstance(reply, Exception)
                                else 'Term consolidation decided 0 of 2 terms')

    def test_partial_reply(self, small_batches, caplog):
        caplog.set_level(logging.WARNING, logger='src.glossary.term_extractor')
        fake = FakeBackend([
            _reply(('Orrin', '歐林', 'person'), ('Tess', '泰絲', 'person')),
            _reply(('Orrin', '奧林', 'person'), ('Tess', '黛絲', 'person')),
            '```json\n[{"term": "tess", "translation": "黛絲", "category": 5}]\n```'])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(2))
        # decided: the stored spelling, the decided text, the proposals' category
        assert terms == [{'term': 'Orrin', 'translation': '歐林', 'category': 'person'},
                         {'term': 'Tess', 'translation': '黛絲', 'category': 'person'}]
        assert [r.getMessage() for r in caplog.records] == \
            ['Term consolidation decided 1 of 2 terms']

    def test_decided_category_and_free_translation(self, small_batches, caplog):
        caplog.set_level(logging.WARNING, logger='src.glossary.term_extractor')
        fake = FakeBackend([_reply(('Orrin', '歐林', 'person')), _reply(('Orrin', '奧林', 'person')),
                            _reply(('ORRIN', '奧瑞', 'place'))])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(2))
        assert terms == [{'term': 'Orrin', 'translation': '奧瑞', 'category': 'place'}]
        assert caplog.records == []

    @pytest.mark.parametrize('stop', [RateLimitError('limit'), AuthError('login expired')])
    def test_stop_during_consolidation_keeps_every_term(self, small_batches, stop):
        fake = FakeBackend([_reply(('Orrin', '歐林', 'person'), ('Tess', '泰絲', 'person')),
                            _reply(('Orrin', '奧林', 'person')),
                            _reply(('Orrin', '奧林', 'person')),
                            stop])
        with pytest.raises(ExtractionError) as info:
            TermExtractor(backend=fake).extract_from_segments(_segments(3))
        assert str(info.value) == str(stop)
        assert info.value.terms == [
            {'term': 'Orrin', 'translation': '奧林', 'category': 'person'},
            {'term': 'Tess', 'translation': '泰絲', 'category': 'person'}]

    def test_stop_in_a_later_consolidation_keeps_the_earlier_decisions(self, small_batches,
                                                                       monkeypatch):
        monkeypatch.setattr(te, 'MAX_CHARS_PER_CONSOLIDATION', 10)  # one term per call
        fake = FakeBackend([_reply(('Orrin', '歐林', 'person'), ('Tess', '泰絲', 'person')),
                            _reply(('Orrin', '奧林', 'person'), ('Tess', '黛絲', 'person')),
                            _reply(('Orrin', '歐倫', 'person')),
                            RateLimitError('limit')])
        with pytest.raises(ExtractionError) as info:
            TermExtractor(backend=fake).extract_from_segments(_segments(2))
        assert info.value.terms == [
            {'term': 'Orrin', 'translation': '歐倫', 'category': 'person'},
            {'term': 'Tess', 'translation': '泰絲', 'category': 'person'}]

    def test_stop_during_extraction_resolves_by_frequency(self, small_batches):
        fake = FakeBackend([_reply(('Orrin', '歐林', 'person')),
                            _reply(('Orrin', '奧林', 'person')),
                            _reply(('Orrin', '奧林', 'person')),
                            RateLimitError('limit')])
        with pytest.raises(ExtractionError) as info:
            TermExtractor(backend=fake).extract_from_segments(_segments(4))
        assert info.value.terms == [{'term': 'Orrin', 'translation': '奧林', 'category': 'person'}]
        assert len(fake.requests) == 4  # no consolidating call after a stop

    def test_unusable_candidates_skipped(self, small_batches):
        fake = FakeBackend(['[{"term": 5, "translation": "五"}, {"term": "x", "translation": null},'
                            ' {"term": " ", "translation": "空"}, {"term": "y", "translation": " "},'
                            ' {"term": "Orrin", "translation": "奧林", "category": ["bad"]},'
                            ' {"term": "Tess", "translation": "泰絲"}]'])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(1))
        assert terms == [{'term': 'Orrin', 'translation': '奧林', 'category': ''},
                         {'term': 'Tess', 'translation': '泰絲', 'category': ''}]

    def test_large_conflict_sets_are_cut_into_bounded_calls(self, small_batches, monkeypatch):
        first = [(f'Name{i}', f'甲{i}', 'person') for i in range(6)]
        second = [(f'Name{i}', f'乙{i}', 'person') for i in range(6)]
        line = len(te._candidate_line({'term': 'Name0',
                                       'translations': Counter({'甲0': 1, '乙0': 1}),
                                       'categories': Counter({'person': 2})}))
        monkeypatch.setattr(te, 'MAX_CHARS_PER_CONSOLIDATION', 2 * (line + 1))
        fake = FakeBackend([_reply(*first), _reply(*second), '[]', '[]', '[]'])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(2))
        sent = [[c['term'] for c in _candidates(p)] for p in fake.prompts[2:]]
        assert sent == [['Name0', 'Name1'], ['Name2', 'Name3'], ['Name4', 'Name5']]
        assert [t['translation'] for t in terms] == [f'甲{i}' for i in range(6)]

    def test_consolidation_batches(self):
        groups = [{'term': 'a' * n, 'translations': Counter({'x': 1, 'y': 1}),
                   'categories': Counter({'': 2})}
                  for n in (1, 1, 30, 1)]
        size = len(te._candidate_line(groups[0])) + 1
        import unittest.mock as mock
        with mock.patch.object(te, 'MAX_CHARS_PER_CONSOLIDATION', 2 * size):
            parts = te._consolidation_batches(groups)
        assert [[len(g['term']) for g in p] for p in parts] == [[1, 1], [30], [1]]
        assert te._consolidation_batches([]) == []

    def test_default_limit(self):
        assert te.MAX_CHARS_PER_CONSOLIDATION == 8000

    def test_collect_candidates_counts_once_per_batch(self, small_batches):
        fake = FakeBackend([_reply(('Orrin', '歐林', 'person'), ('Tess', '泰絲', 'person')),
                            _reply(('orrin', '歐林', 'place'))])
        groups = TermExtractor(backend=fake).collect_candidates(_segments(2))
        assert groups == {
            'orrin': {'term': 'Orrin', 'translations': {'歐林': 2},
                      'categories': {'person': 1, 'place': 1}},
            'tess': {'term': 'Tess', 'translations': {'泰絲': 1}, 'categories': {'person': 1}}}


# --- The brief in the prompt --------------------------------------------------------

def _engine():
    return TranslationEngine(backend=FakeBackend())


class TestBriefPrompt:

    def test_brief_at_the_top_of_the_html_and_text_prompts(self):
        engine = _engine()
        html = engine._build_prompt('Hi.', SegmentType.TEXT, 'Orrin → 奧林', 'before', 'after',
                                    html='<p k="1">Hi.</p>', brief=BRIEF)
        assert html == BRIEF_SECTION.format(brief=BRIEF) + engine._build_prompt(
            'Hi.', SegmentType.TEXT, 'Orrin → 奧林', 'before', 'after', html='<p k="1">Hi.</p>')
        assert html.startswith('本書簡介（請依此掌握文體、語氣與讀者；簡介本身不需翻譯）：\n'
                               + BRIEF + '\n\n你是一位專業的英文翻譯')
        text = engine._build_prompt('Hi.', SegmentType.TEXT, brief=BRIEF)
        assert text == BRIEF_SECTION.format(brief=BRIEF) + TRANSLATION_PROMPT.format(
            text='Hi.', glossary_section='', context_section='')

    def test_absent_brief_leaves_the_prompt_unchanged(self):
        engine = _engine()
        assert engine._build_prompt('Hi.', SegmentType.TEXT, html='<p k="1">Hi.</p>', brief='') == \
            TEXT_HTML_PROMPT.format(html='<p k="1">Hi.</p>', glossary_section='', context_section='')
        assert engine._build_prompt('Hi.', SegmentType.TEXT) == \
            TRANSLATION_PROMPT.format(text='Hi.', glossary_section='', context_section='')

    def test_code_gets_no_brief(self):
        assert _engine()._build_prompt('# hi\nx', SegmentType.CODE, brief=BRIEF) == \
            CODE_PROMPT.format(text='# hi\nx')

    def test_translate_passes_the_brief(self):
        fake = FakeBackend(['譯文'])
        TranslationEngine(backend=fake).translate('Hi.', brief=BRIEF)
        assert fake.prompts[0].startswith(BRIEF_SECTION.format(brief=BRIEF))

    def test_translate_segment_and_retry_keep_the_brief(self, tmp_path):
        path = _project(tmp_path)
        pm = ProjectManager(path)
        pm.load()
        seg = pm.data.segments[0]
        replies = iter(['<p k="9">bad</p>', None])
        fake = FakeBackend(responder=lambda r: next(replies) or like(r, '譯'))
        outcome = translate_segment(TranslationEngine(backend=fake), seg, pm.data.segments,
                                    brief=BRIEF)
        assert outcome.text == '<p>譯</p>'
        # the retry repeats the first message, brief included
        assert [r.messages[0].content.startswith(BRIEF_SECTION.format(brief=BRIEF))
                for r in fake.requests] == [True, True]

    def test_unexpected_error_diagnostics_keep_the_brief(self, tmp_path, monkeypatch):
        path = _project(tmp_path)
        pm = ProjectManager(path)
        pm.load()
        seg = pm.data.segments[0]

        def broken(*a, **kw):
            raise RuntimeError('boom')

        engine = TranslationEngine(backend=FakeBackend())
        monkeypatch.setattr(engine, 'translate_reply', broken)
        gm = GlossaryManager(os.path.join(path, 'glossary.json'))
        gm.add_term('Orrin', '奧林')
        outcome = translate_segment(engine, seg, pm.data.segments, gm, brief=BRIEF)
        assert outcome.failure.prompt.startswith(BRIEF_SECTION.format(brief=BRIEF))
        assert 'Orrin → 奧林\n' in outcome.failure.prompt  # the glossary too


# --- The brief in the project file -------------------------------------------------

def _project(tmp_path, n=2, name='p'):
    path = str(tmp_path / name)
    segments = [Segment(id=f'c1_seg{i:04d}', html=f'<p>Line MARK{i}.</p>', text=f'Line MARK{i}.',
                        word_count=2, segment_type=SegmentType.TEXT) for i in range(1, n + 1)]
    ProjectManager(path).create('/nowhere/book.epub', 'Invented', 'Nobody', segments)
    return path


class TestBriefInProject:

    def test_absent_brief_round_trips_unchanged(self, tmp_path):
        path = _project(tmp_path)
        with open(os.path.join(path, 'project.json'), encoding='utf-8') as f:
            before = f.read()
        pm = ProjectManager(path)
        pm.load()
        assert pm.data.brief is None
        assert pm.to_json() == before
        assert 'brief' not in json.loads(before)

    def test_set_save_load_and_remove(self, tmp_path):
        path = _project(tmp_path)
        pm = ProjectManager(path)
        pm.load()
        pm.set_brief(f'  {BRIEF}\n')
        pm.save()
        assert json.loads(pm.to_json())['brief'] == BRIEF
        assert ProjectManager(path).load().brief == BRIEF
        for empty in ('', '  \n'):
            pm.set_brief(empty)
            assert pm.data.brief is None
            assert 'brief' not in json.loads(pm.to_json())

    def test_set_without_project(self, tmp_path):
        with pytest.raises(RuntimeError) as info:
            ProjectManager(str(tmp_path)).set_brief('x')
        assert str(info.value) == 'No project loaded.'

    @pytest.mark.parametrize('value', [5, None, ['x']])
    def test_brief_that_is_not_text_refused(self, tmp_path, value):
        path = _project(tmp_path)
        file = os.path.join(path, 'project.json')
        with open(file, encoding='utf-8') as f:
            raw = json.load(f)
        raw['brief'] = value
        with open(file, 'w', encoding='utf-8') as f:
            json.dump(raw, f)
        with pytest.raises(ValueError) as info:
            ProjectManager(path).load()
        assert str(info.value) == 'the brief is not text'


# --- Person notes in the glossary ---------------------------------------------------

class TestPersonNotes:

    @pytest.fixture
    def gm(self, tmp_path):
        return GlossaryManager(str(tmp_path / 'glossary.json'))

    def test_in_the_prompt_with_the_entry(self, gm):
        gm.add_term('Tess', '泰絲', category='person', gender='female', note='the baker')
        gm.add_term('Orrin', '奧林', category='person', gender='male')
        gm.add_term('Wren', '雷恩', category='person', note='a talking bird')
        gm.add_term('lantern', '燈籠', category='term')
        assert gm.format_for_prompt().splitlines() == [
            'lantern → 燈籠 [術語]',
            'Orrin → 奧林 [人名]（性別：male）',
            'Tess → 泰絲 [人名]（性別：female；備註：the baker）',
            'Wren → 雷恩 [人名]（備註：a talking bird）',
        ]

    def test_reach_the_translation_prompt(self, tmp_path):
        path = _project(tmp_path, n=1)
        gm = GlossaryManager(os.path.join(path, 'glossary.json'))
        gm.add_term('MARK1', '標記', category='person', gender='female', note='the narrator')
        pm = ProjectManager(path)
        pm.load()
        fake = FakeBackend(responder=lambda r: like(r, '譯'))
        translate_segment(TranslationEngine(backend=fake), pm.data.segments[0],
                          pm.data.segments, gm)
        assert 'MARK1 → 標記 [人名]（性別：female；備註：the narrator）\n' in fake.prompts[0]

    def test_add_defaults(self, gm):
        assert gm.add_term('Orrin', '奧林') == GlossaryEntry('Orrin', '奧林', '', 0, '', '')

    def test_absent_notes_keep_the_file_format(self, gm):
        gm.add_term('Orrin', '奧林', category='person')
        with open(gm.glossary_path, encoding='utf-8') as f:
            assert json.load(f) == [{'term': 'Orrin', 'translation': '奧林', 'category': 'person',
                                     'frequency': 0}]
        assert gm.format_for_prompt() == 'Orrin → 奧林 [人名]'

    def test_stored_loaded_updated(self, gm):
        gm.add_term('Tess', '泰絲', category='person', gender='female')
        with open(gm.glossary_path, encoding='utf-8') as f:
            assert json.load(f)[0] == {'term': 'Tess', 'translation': '泰絲', 'category': 'person',
                                       'frequency': 0, 'gender': 'female'}
        gm.update_term('Tess', note='the baker')
        other = GlossaryManager(gm.glossary_path)
        other.load()
        assert other.get_term('tess') == GlossaryEntry('Tess', '泰絲', 'person', 0,
                                                       'female', 'the baker')
        other.update_term('Tess', translation='黛絲')  # None: the notes stay
        assert (other.get_term('Tess').gender, other.get_term('Tess').note) == ('female', 'the baker')
        other.update_term('Tess', gender='', note='')
        with open(gm.glossary_path, encoding='utf-8') as f:
            assert set(json.load(f)[0]) == {'term', 'translation', 'category', 'frequency'}

    @pytest.mark.parametrize('field', ['gender', 'note'])
    def test_not_text_refused_on_load(self, gm, field):
        with open(gm.glossary_path, 'w', encoding='utf-8') as f:
            json.dump([{'term': 'Tess', 'translation': '泰絲', field: 1}], f)
        with pytest.raises(ValueError):
            gm.load()

    def test_import_and_export(self, gm, tmp_path):
        source = tmp_path / 'in.json'
        source.write_text(json.dumps([
            {'term': 'Tess', 'translation': '泰絲', 'category': 'person', 'gender': 'female',
             'note': 'the baker'},
            {'term': 'Orrin', 'translation': '奧林'}]), encoding='utf-8')
        assert gm.import_json(str(source)) == 2
        assert gm.get_term('Tess').note == 'the baker'
        assert gm.get_term('Orrin') == GlossaryEntry('Orrin', '奧林')
        out = tmp_path / 'out.json'
        gm.export_json(str(out))
        assert json.loads(out.read_text(encoding='utf-8')) == [
            {'term': 'Orrin', 'translation': '奧林', 'category': '', 'frequency': 0},
            {'term': 'Tess', 'translation': '泰絲', 'category': 'person', 'frequency': 0,
             'gender': 'female', 'note': 'the baker'}]


# --- CLI ------------------------------------------------------------------------------

@pytest.fixture
def runner():
    return CliRunner()


def _cli():
    from translate import cli
    return cli


class TestCliBrief:

    def test_show_set_clear(self, runner, tmp_path):
        path = _project(tmp_path)
        cli = _cli()
        assert runner.invoke(cli, ['brief', 'show', '-p', path]).output == '尚未設定書籍簡介。\n'
        result = runner.invoke(cli, ['brief', 'set', BRIEF, '-p', path])
        assert (result.exit_code, result.output) == (0, '已設定書籍簡介。\n')
        assert runner.invoke(cli, ['brief', 'show', '-p', path]).output == BRIEF + '\n'
        result = runner.invoke(cli, ['brief', 'set', '', '--project', path])
        assert (result.exit_code, result.output) == (0, '已移除書籍簡介。\n')
        assert 'brief' not in json.loads((tmp_path / 'p' / 'project.json').read_text())

    def test_no_project(self, runner, tmp_path):
        for args in (['brief', 'show'], ['brief', 'set', 'x']):
            result = runner.invoke(_cli(), args + ['-p', str(tmp_path / 'none')])
            assert (result.exit_code, result.output) == (1, '找不到專案。\n')

    def test_set_refused_while_another_process_runs(self, runner, tmp_path):
        from tests.conftest import hold_lock_in_child
        path = _project(tmp_path)
        child = hold_lock_in_child(path)
        try:
            result = runner.invoke(_cli(), ['brief', 'set', BRIEF, '-p', path])
        finally:
            child.stdin.close()
            child.wait(timeout=10)
        assert result.exit_code == 1
        assert '專案正由另一個程序使用' in result.output
        assert ProjectManager(path).load().brief is None

    @pytest.mark.parametrize('workers', ['1', '2'])
    def test_start_sends_the_brief(self, runner, tmp_path, workers):
        path = _project(tmp_path)
        runner.invoke(_cli(), ['brief', 'set', BRIEF, '-p', path])
        fake = FakeBackend(responder=lambda r: like(r, '譯'))
        with use_fake_backend(fake):
            result = runner.invoke(_cli(), ['start', '-p', path, '--workers', workers])
        assert result.exit_code == 0, result.output
        assert len(fake.prompts) == 2
        assert all(p.startswith(BRIEF_SECTION.format(brief=BRIEF)) for p in fake.prompts)

    @pytest.mark.parametrize('workers', ['1', '2'])
    def test_start_without_brief_sends_todays_prompt(self, runner, tmp_path, workers):
        path = _project(tmp_path)
        fake = FakeBackend(responder=lambda r: like(r, '譯'))
        with use_fake_backend(fake):
            runner.invoke(_cli(), ['start', '-p', path, '--workers', workers])
        assert len(fake.prompts) == 2
        assert all(p.startswith('你是一位專業的英文翻譯') for p in fake.prompts)


class TestCliPersonNotes:

    def test_add_and_show(self, runner, tmp_path):
        path = _project(tmp_path)
        cli = _cli()
        result = runner.invoke(cli, ['glossary', 'add', 'Tess', '泰絲', '-c', 'person',
                                     '--gender', 'female', '--note', 'the baker', '-p', path])
        assert result.exit_code == 0, result.output
        runner.invoke(cli, ['glossary', 'add', 'Orrin', '奧林', '-c', 'person', '-g', 'male',
                            '-n', 'a sailor', '-p', path])
        runner.invoke(cli, ['glossary', 'add', 'lantern', '燈籠', '-p', path])
        assert runner.invoke(cli, ['glossary', 'show', '-p', path]).output.splitlines()[2:] == [
            '  lantern → 燈籠',
            '  Orrin → 奧林  [person]  性別：male  備註：a sailor',
            '  Tess → 泰絲  [person]  性別：female  備註：the baker',
        ]
        with open(os.path.join(path, 'glossary.json'), encoding='utf-8') as f:
            assert json.load(f)[0] == {'term': 'lantern', 'translation': '燈籠', 'category': '',
                                       'frequency': 0}

    def test_extract_keeps_a_persons_notes(self, runner, tmp_path):
        path = _project(tmp_path)
        gm = GlossaryManager(os.path.join(path, 'glossary.json'))
        gm.add_term('Tess', '泰絲', category='person', gender='female', note='the baker')
        fake = FakeBackend([_reply(('Tess', '黛絲', 'person'), ('Orrin', '奧林', 'person'))])
        with use_fake_backend(fake):
            result = runner.invoke(_cli(), ['glossary', 'extract', '-p', path])
        assert result.exit_code == 0, result.output
        gm.load()
        assert gm.get_term('Tess') == GlossaryEntry('Tess', '黛絲', 'person', 0, 'female',
                                                    'the baker')
        assert gm.get_term('Orrin') == GlossaryEntry('Orrin', '奧林', 'person')

    def test_extract_consolidates(self, runner, tmp_path, small_batches):
        path = _project(tmp_path)
        fake = FakeBackend([_reply(('Tess', '泰絲', 'person')), _reply(('Tess', '黛絲', 'person')),
                            _reply(('Tess', '黛絲', 'person'))])
        with use_fake_backend(fake):
            result = runner.invoke(_cli(), ['glossary', 'extract', '-p', path])
        assert result.exit_code == 0, result.output
        assert '提取到 1 個術語' in result.output
        gm = GlossaryManager(os.path.join(path, 'glossary.json'))
        gm.load()
        assert [(e.term, e.translation) for e in gm.get_all_terms()] == [('Tess', '黛絲')]


# --- Web ------------------------------------------------------------------------------

@pytest.fixture
def web(tmp_path):
    from src.web.server import create_app
    from src.web.tasks import TaskManager
    TaskManager._instance = None
    _project(tmp_path, name='book')
    app = create_app(projects_dir=str(tmp_path))
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client, tmp_path
    TaskManager._instance = None


class TestWebBrief:

    def test_get_put_and_clear(self, web):
        client, tmp_path = web
        assert client.get('/api/projects/book/brief').get_json() == {'brief': ''}
        res = client.put('/api/projects/book/brief', json={'brief': f' {BRIEF} '})
        assert (res.status_code, res.get_json()) == (200, {'status': 'ok', 'brief': BRIEF})
        assert client.get('/api/projects/book/brief').get_json() == {'brief': BRIEF}
        assert ProjectManager(str(tmp_path / 'book')).load().brief == BRIEF
        res = client.put('/api/projects/book/brief', json={'brief': ''})
        assert res.get_json() == {'status': 'ok', 'brief': ''}
        assert 'brief' not in json.loads((tmp_path / 'book' / 'project.json').read_text())

    @pytest.mark.parametrize('body', [None, [], {}, {'brief': 3}, {'brief': None}])
    def test_put_refused(self, web, body):
        client, tmp_path = web
        res = client.put('/api/projects/book/brief', data=json.dumps(body),
                         content_type='application/json')
        assert (res.status_code, res.get_json()) == (400, {'error': '請提供 brief 文字'})

    def test_unknown_project(self, web):
        client, _ = web
        assert client.get('/api/projects/none/brief').status_code == 404
        assert client.put('/api/projects/none/brief', json={'brief': 'x'}).status_code == 404

    def test_page_has_the_editor(self, web):
        client, _ = web
        page = client.get('/project/book/translation').get_data(as_text=True)
        assert 'id="brief-text" x-model="brief"' in page
        assert "fetch('/api/projects/book/brief'" in page

    def test_running_task_uses_an_edited_brief_from_the_next_segment(self, web):
        import threading
        from src.web.tasks import TaskManager
        from tests.test_web import _wait
        client, tmp_path = web
        first_in_flight, release = threading.Event(), threading.Event()

        def respond(request):
            if 'MARK1' in request.messages[0].content.split('<p k="1">')[-1]:
                first_in_flight.set()
                assert release.wait(10)
            return like(request, '譯')

        fake = FakeBackend(responder=respond)
        with use_fake_backend(fake):
            client.post('/api/projects/book/translate/start', json={})
            assert first_in_flight.wait(10)
            res = client.put('/api/projects/book/brief', json={'brief': BRIEF})
            assert res.status_code == 200
            release.set()
            task = TaskManager().get_task('book')
            _wait(lambda: not task.threads_alive() and task.state == 'completed')
        assert fake.prompts[0].startswith('你是一位專業的英文翻譯')  # none yet: today's prompt
        assert fake.prompts[1].startswith(BRIEF_SECTION.format(brief=BRIEF))
        assert ProjectManager(str(tmp_path / 'book')).load().brief == BRIEF

    def test_retranslate_sends_the_brief(self, web):
        client, tmp_path = web
        client.put('/api/projects/book/brief', json={'brief': BRIEF})
        fake = FakeBackend(responder=lambda r: like(r, '譯'))
        with use_fake_backend(fake):
            res = client.post('/api/projects/book/segments/c1_seg0001/retranslate')
        assert res.status_code == 200, res.get_json()
        assert fake.prompts[0].startswith(BRIEF_SECTION.format(brief=BRIEF))


class TestWebPersonNotes:

    def test_add_and_update(self, web):
        client, tmp_path = web
        res = client.post('/api/projects/book/glossary',
                          json={'term': 'Tess', 'translation': '泰絲', 'category': 'person',
                                'gender': 'female', 'note': 'the baker'})
        assert res.get_json()['entry'] == {'term': 'Tess', 'translation': '泰絲',
                                           'category': 'person', 'frequency': 0,
                                           'gender': 'female', 'note': 'the baker'}
        res = client.put('/api/projects/book/glossary/Tess', json={'note': 'the miller'})
        assert res.get_json()['entry']['note'] == 'the miller'
        assert res.get_json()['entry']['gender'] == 'female'
        res = client.put('/api/projects/book/glossary/Tess', json={'gender': 'male'})
        assert res.get_json()['entry']['gender'] == 'male'
        listed = client.get('/api/projects/book/glossary').get_json()
        assert (listed[0]['gender'], listed[0]['note']) == ('male', 'the miller')

    def test_add_without_notes_keeps_the_file_format(self, web):
        client, tmp_path = web
        client.post('/api/projects/book/glossary', json={'term': 'Orrin', 'translation': '奧林'})
        stored = json.loads((tmp_path / 'book' / 'glossary.json').read_text(encoding='utf-8'))
        assert stored == [{'term': 'Orrin', 'translation': '奧林', 'category': '', 'frequency': 0}]

    @pytest.mark.parametrize('field', ['gender', 'note'])
    def test_not_text_refused(self, web, field):
        client, tmp_path = web
        res = client.post('/api/projects/book/glossary',
                          json={'term': 'Tess', 'translation': '泰絲', field: 1})
        assert (res.status_code, res.get_json()) == (400, {'error': 'gender 和 note 必須是文字'})
        client.post('/api/projects/book/glossary', json={'term': 'Tess', 'translation': '泰絲'})
        res = client.put('/api/projects/book/glossary/Tess', json={field: ['x']})
        assert (res.status_code, res.get_json()) == (400, {'error': 'gender 和 note 必須是文字'})
        assert not (tmp_path / 'book' / 'glossary.json').read_text(encoding='utf-8').count(field)

    def test_page_has_the_fields(self, web):
        client, _ = web
        page = client.get('/project/book/glossary').get_data(as_text=True)
        assert 'x-model="newTerm.gender"' in page and 'x-model="newTerm.note"' in page
        assert 'x-model="editData.gender"' in page and 'x-model="editData.note"' in page


class TestMutationSurvivors:
    """Mutants of the first mutation run."""

    def test_translate_reply_without_brief(self):
        fake = FakeBackend(['譯文'])
        TranslationEngine(backend=fake).translate_reply('Hi.', SegmentType.TEXT, '', '', '', '')
        assert fake.prompts[0].startswith('你是一位專業的英文翻譯')

    def test_second_batch_filled_exactly_is_one_call(self, small_batches):
        assert te._batches([{'text': 'a' * 20}, {'text': 'b' * 9}, {'text': 'c' * 9}]) == \
            ['a' * 20, 'b' * 9 + '\n\n' + 'c' * 9]

    def test_consolidation_batch_boundary(self, monkeypatch):
        groups = [{'term': 'a', 'translations': Counter({'x': 1, 'y': 1}),
                   'categories': Counter({'': 2})} for _ in range(2)]
        size = len(te._candidate_line(groups[0])) + 1
        monkeypatch.setattr(te, 'MAX_CHARS_PER_CONSOLIDATION', 2 * size - 1)
        assert [len(p) for p in te._consolidation_batches(groups)] == [1, 1]
        monkeypatch.setattr(te, 'MAX_CHARS_PER_CONSOLIDATION', 2 * size)
        assert [len(p) for p in te._consolidation_batches(groups)] == [2]

    def test_sorted_case_insensitively(self):
        groups = {k: {'term': k, 'translations': Counter({'x': 1}), 'categories': Counter({'': 1})}
                  for k in ('aB', 'a_b', 'Ac')}
        # by lowercase: '_' (95) sorts before 'b' (98); uppercase would put 'AB' first
        assert [t['term'] for t in te._final(groups, {})] == ['a_b', 'aB', 'Ac']

    def test_most_common(self):
        assert te._most_common(Counter({'x': 1, 'y': 2, 'z': 2})) == 'y'
        assert te._most_common(Counter({'b': 1, 'a': 1})) == 'b'

    def test_call_passes_an_event(self):
        import threading
        from src.llm import LLMResponse
        seen = []

        class Backend(FakeBackend):
            def complete(self, request, cancel):
                seen.append(cancel)
                return LLMResponse(text='[]', stop_reason='complete', usage=None, model='m')

        TermExtractor(backend=Backend()).extract_from_text('x')
        assert len(seen) == 1 and isinstance(seen[0], threading.Event) and not seen[0].is_set()

    def test_later_items_read_after_an_unusable_one(self, small_batches):
        fake = FakeBackend([_reply(('Orrin', '歐林', 'person')), _reply(('Orrin', '奧林', 'person')),
                            _reply(('Someone', '某人', 'person'), ('Orrin', '歐倫', 'person'))])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(2))
        assert terms == [{'term': 'Orrin', 'translation': '歐倫', 'category': 'person'}]


# --- Review round 1 -----------------------------------------------------------------

class TestRound1Consolidation:

    def test_a_reply_counts_once_per_batch(self, small_batches):
        fake = FakeBackend([
            _reply(('Orrin', '歐林', 'person'), ('orrin', '歐林', 'person'), (' Orrin ', '歐林 ', 'person')),
            _reply(('Orrin', '奧林', 'place')),
            _reply(('Orrin', '奧林', 'place')),
            'no json'])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(3))
        assert _candidates(fake.prompts[3]) == [
            {'term': 'Orrin', 'category': 'place', 'translations': {'歐林': 1, '奧林': 2}}]
        assert terms == [{'term': 'Orrin', 'translation': '奧林', 'category': 'place'}]

    def test_decoded_reply_that_is_not_a_list_is_unusable(self, small_batches):
        fake = FakeBackend([_reply(('Orrin', '歐林', 'person')), _reply(('Orrin', '奧林', 'person')),
                            _reply(('Orrin', '奧林', 'person')),
                            '{"rejected": [{"term": "Orrin", "translation": "歐倫"}]}'])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(3))
        assert terms == [{'term': 'Orrin', 'translation': '奧林', 'category': 'person'}]
    def test_extraction_reply_that_is_not_a_list(self, caplog):
        caplog.set_level(logging.WARNING, logger='src.glossary.term_extractor')
        assert TermExtractor(backend=FakeBackend(
            ['{"terms": [{"term": "A", "translation": "甲"}]}'])).extract_from_text('x') == []
        assert [r.getMessage() for r in caplog.records] == [
            'Could not parse term extraction response']

    def test_reply_that_cannot_be_parsed_is_unusable(self, small_batches, caplog):
        caplog.set_level(logging.WARNING, logger='src.glossary.term_extractor')
        deep = '[' * 100000 + ']' * 100000  # the decoder raises RecursionError
        fake = FakeBackend([_reply(('Orrin', '歐林', 'person')), _reply(('Orrin', '奧林', 'person')),
                            _reply(('Orrin', '奧林', 'person')), deep])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(3))
        assert terms == [{'term': 'Orrin', 'translation': '奧林', 'category': 'person'}]
        assert [r.getMessage() for r in caplog.records][-2:] == [
            'Term consolidation error: RecursionError', 'Term consolidation decided 0 of 1 terms']

    def test_contradicting_rows_leave_the_term_undecided(self, small_batches):
        fake = FakeBackend([
            _reply(('Orrin', '歐林', 'person'), ('Tess', '泰絲', 'person')),
            _reply(('Orrin', '奧林', 'person'), ('Tess', '黛絲', 'person')),
            _reply(('Orrin', '奧林', 'person')),
            _reply(('Orrin', '歐倫', 'person'), ('orrin', '歐林', 'person'),
                   ('Tess', '黛絲', 'person'), ('TESS', '黛絲', 'place'))])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(3))
        # Orrin: two decisions, by frequency; Tess: the same twice, decided (first row's category)
        assert terms == [{'term': 'Orrin', 'translation': '奧林', 'category': 'person'},
                         {'term': 'Tess', 'translation': '黛絲', 'category': 'person'}]

    @pytest.mark.parametrize('category', ['', '  ', None])
    def test_stripped_reply_and_empty_category(self, small_batches, category):
        fake = FakeBackend([_reply(('Orrin', '歐林', 'work')), _reply(('Orrin', '奧林', 'work')),
                            json.dumps([{'term': ' orrin\n', 'translation': ' 歐倫\n',
                                         'category': category}], ensure_ascii=False)])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(2))
        assert terms == [{'term': 'Orrin', 'translation': '歐倫', 'category': 'work'}]

    def test_stripped_category(self, small_batches):
        fake = FakeBackend([_reply(('Orrin', '歐林', 'work')), _reply(('Orrin', '奧林', 'work')),
                            _reply(('Orrin', '歐倫', ' place\n'))])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(2))
        assert terms == [{'term': 'Orrin', 'translation': '歐倫', 'category': 'place'}]

    def test_candidates_stripped(self, small_batches):
        fake = FakeBackend([_reply((' Orrin ', ' 奧林\n', ' person '))])
        assert TermExtractor(backend=fake).extract_from_segments(_segments(1)) == [
            {'term': 'Orrin', 'translation': '奧林', 'category': 'person'}]


class TestRound1Glossary:

    @pytest.fixture
    def gm(self, tmp_path):
        return GlossaryManager(str(tmp_path / 'glossary.json'))

    @pytest.mark.parametrize('content', [
        [{'term': 'Orrin', 'translation': '奧林'}, {'term': 'Tess', 'translation': '泰絲', 'gender': 1}],
        [{'term': 'Orrin', 'translation': '奧林'}, {'term': 'Tess', 'translation': '泰絲', 'note': []}],
        [{'term': 'Orrin', 'translation': '奧林'}, 'Tess'],
        {'term': 'Orrin', 'translation': '奧林'},
    ])
    def test_import_checked_before_anything_is_written(self, gm, tmp_path, content):
        source = tmp_path / 'in.json'
        source.write_text(json.dumps(content), encoding='utf-8')
        with pytest.raises(ValueError):
            gm.import_json(str(source))
        assert gm.entries == {} and not os.path.exists(gm.glossary_path)

    def test_cli_import_refused(self, tmp_path):
        path = _project(tmp_path)
        source = tmp_path / 'in.json'
        source.write_text('[{"term": "Tess", "translation": "泰絲", "gender": 1}]', encoding='utf-8')
        result = CliRunner().invoke(_cli(), ['glossary', 'import', str(source), '-p', path])
        assert (result.exit_code, result.output) == (
            1, '無法匯入：a glossary entry is not an object with text fields\n')
        assert not os.path.exists(os.path.join(path, 'glossary.json'))

    def test_person_fields_stripped(self, gm):
        gm.add_term('Tess', '泰絲', gender=' female ', note=' the baker\n')
        assert (gm.get_term('Tess').gender, gm.get_term('Tess').note) == ('female', 'the baker')
        gm.update_term('Tess', gender='  ', note=' a miller ')
        assert (gm.get_term('Tess').gender, gm.get_term('Tess').note) == ('', 'a miller')
        with open(gm.glossary_path, encoding='utf-8') as f:
            assert 'gender' not in json.load(f)[0]

    def test_cli_add_keeps_omitted_person_fields(self, tmp_path):
        path = _project(tmp_path)
        cli, runner = _cli(), CliRunner()
        runner.invoke(cli, ['glossary', 'add', 'Tess', '泰絲', '-c', 'person', '-g', 'female',
                            '-n', 'the baker', '-p', path])
        gm = GlossaryManager(os.path.join(path, 'glossary.json'))

        def entry():
            gm.load()
            return gm.get_term('Tess')

        runner.invoke(cli, ['glossary', 'add', 'Tess', '黛絲', '-c', 'person', '-p', path])
        assert entry() == GlossaryEntry('Tess', '黛絲', 'person', 0, 'female', 'the baker')
        runner.invoke(cli, ['glossary', 'add', 'Tess', '黛絲', '-c', 'person', '-n', '', '-p', path])
        assert entry() == GlossaryEntry('Tess', '黛絲', 'person', 0, 'female', '')
        runner.invoke(cli, ['glossary', 'add', 'Tess', '黛絲', '-c', 'person', '-g', '', '-p', path])
        assert entry() == GlossaryEntry('Tess', '黛絲', 'person', 0, '', '')
        runner.invoke(cli, ['glossary', 'add', 'Orrin', '奧林', '-p', path])
        gm.load()
        assert gm.get_term('Orrin') == GlossaryEntry('Orrin', '奧林')

    @pytest.mark.parametrize('body', [['term', 'translation'], 'term', 5])
    def test_web_non_object_body(self, web, body):
        client, tmp_path = web
        res = client.post('/api/projects/book/glossary', data=json.dumps(body),
                          content_type='application/json')
        assert (res.status_code, res.get_json()) == (400, {'error': '請提供 term 和 translation'})
        client.post('/api/projects/book/glossary', json={'term': 'Tess', 'translation': '泰絲'})
        res = client.put('/api/projects/book/glossary/Tess', data=json.dumps(body),
                         content_type='application/json')
        assert (res.status_code, res.get_json()) == (400, {'error': '請提供更新資料'})


class TestRound1Brief:

    def test_limit_in_the_setter(self, tmp_path):
        from src.core.project import MAX_BRIEF_CHARS
        assert MAX_BRIEF_CHARS == 2000
        path = _project(tmp_path)
        pm = ProjectManager(path)
        pm.load()
        pm.set_brief(' ' + 'x' * 2000 + ' ')
        assert pm.data.brief == 'x' * 2000
        with pytest.raises(ValueError) as info:
            pm.set_brief('y' * 2001)
        assert str(info.value) == '書籍簡介最多 2000 字（目前 2001 字）'
        assert pm.data.brief == 'x' * 2000

    def test_limit_in_the_cli_and_web(self, tmp_path, web):
        client, root = web
        before = (root / 'book' / 'project.json').read_text()
        res = client.put('/api/projects/book/brief', json={'brief': 'y' * 2001})
        assert (res.status_code, res.get_json()) == (
            400, {'error': '書籍簡介最多 2000 字（目前 2001 字）'})
        assert (root / 'book' / 'project.json').read_text() == before
        result = CliRunner().invoke(_cli(), ['brief', 'set', 'y' * 2001, '-p', str(root / 'book')])
        assert (result.exit_code, result.output) == (1, '書籍簡介最多 2000 字（目前 2001 字）\n')
        assert (root / 'book' / 'project.json').read_text() == before

    def test_failed_save_restores_the_previous_brief(self, web, monkeypatch):
        from src.web.tasks import TaskManager
        client, root = web
        pm = ProjectManager(str(root / 'book'))
        pm.load()
        pm.set_brief('Old brief.')
        monkeypatch.setattr(TaskManager, 'get_or_load_pm', lambda self, name, d: pm)

        def broken():
            assert pm.data.brief == BRIEF  # the save sees the new one
            raise OSError('disk full')

        monkeypatch.setattr(pm, 'save', broken)
        with pytest.raises(OSError):
            client.put('/api/projects/book/brief', json={'brief': BRIEF})
        assert pm.data.brief == 'Old brief.'

    def test_get_reads_under_the_mutex(self, web, monkeypatch):
        from src.web.tasks import TaskManager
        client, root = web
        pm = ProjectManager(str(root / 'book'))
        pm.load()

        class Mutex:
            def __enter__(self):
                pm.data.brief = 'set while held'

            def __exit__(self, *exc):
                pm.data.brief = 'set after'

        pm.mutex = Mutex()
        monkeypatch.setattr(TaskManager, 'get_or_load_pm', lambda self, name, d: pm)
        assert client.get('/api/projects/book/brief').get_json() == {'brief': 'set while held'}

    def test_text_area_waits_for_the_load_and_keeps_a_newer_draft(self, web):
        client, _ = web
        page = client.get('/project/book/translation').get_data(as_text=True)
        assert ':disabled="!briefLoaded"' in page
        assert 'briefLoaded: false' in page and 'this.briefLoaded = true;' in page
        assert 'if (this.brief === sent) this.brief = data.brief;' in page
        assert 'body: JSON.stringify({ brief: sent })' in page


# --- Review round 2 -----------------------------------------------------------------

class TestRound2:

    def test_cut_off_object_is_not_a_consolidation_answer(self, small_batches):
        fake = FakeBackend([_reply(('Orrin', '歐林', 'person')), _reply(('Orrin', '奧林', 'person')),
                            _reply(('Orrin', '奧林', 'person')),
                            '{"rejected": [{"term": "Orrin", "translation": "歐倫"}]'])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(3))
        assert terms == [{'term': 'Orrin', 'translation': '奧林', 'category': 'person'}]

    @pytest.mark.parametrize('reply', ['Here: [{"term": "Orrin", "translation": "歐倫"}]',
                                       '[{"term": "Orrin", "translation": "歐倫"}] done'])
    def test_consolidation_answer_is_the_whole_reply(self, small_batches, reply):
        fake = FakeBackend([_reply(('Orrin', '歐林', 'person')), _reply(('Orrin', '奧林', 'person')),
                            _reply(('Orrin', '奧林', 'person')), reply])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(3))
        assert terms[0]['translation'] == '奧林'
        # the extraction replies keep their recovery
        assert TermExtractor(backend=FakeBackend([reply])).extract_from_text('x') == [
            {'term': 'Orrin', 'translation': '歐倫'}]

    def test_top_level_list(self):
        assert te._top_level_list('```json\n[{"term": "a", "translation": "b"}, 5, {"term": "c"}]\n```') \
            == [{'term': 'a', 'translation': 'b'}]
        assert te._top_level_list(' {"term": "a", "translation": "b"} ') == []
        with pytest.raises(ValueError):
            te._top_level_list('[{"term": "a"')

    def test_an_empty_category_is_no_vote(self, small_batches):
        fake = FakeBackend([_reply(('Orrin', '奧林', '')), _reply(('Orrin', '奧林', 'person')),
                            _reply(('Tess', '泰絲', ''))])
        terms = TermExtractor(backend=fake).extract_from_segments(_segments(3))
        assert terms == [{'term': 'Orrin', 'translation': '奧林', 'category': 'person'},
                         {'term': 'Tess', 'translation': '泰絲', 'category': ''}]
        assert te._most_common(Counter()) == ''

    def test_person_fields_one_line(self, tmp_path):
        gm = GlossaryManager(str(tmp_path / 'g.json'))
        gm.add_term('Tess', '泰絲', gender=' fe\nmale ', note='the\n  baker\tof town ')
        assert (gm.get_term('Tess').gender, gm.get_term('Tess').note) == \
            ('fe male', 'the baker of town')
        gm.update_term('Tess', note='a\r\nmiller')
        assert gm.get_term('Tess').note == 'a miller'
        assert gm.format_for_prompt().count('\n') == 0

    @pytest.mark.parametrize('bad', [{'term': 'B'}, {'term': 'B', 'translation': 'x', 'extra': 'y'}])
    def test_import_with_a_missing_or_unknown_field(self, tmp_path, bad):
        gm = GlossaryManager(str(tmp_path / 'g.json'))
        gm.add_term('Orrin', '奧林')
        before = (tmp_path / 'g.json').read_text(encoding='utf-8')
        source = tmp_path / 'in.json'
        source.write_text(json.dumps([{'term': 'Orrin', 'translation': '歐林'}, bad]), encoding='utf-8')
        with pytest.raises(ValueError) as info:
            gm.import_json(str(source))
        assert str(info.value) == 'a glossary entry has a missing or unknown field'
        assert (tmp_path / 'g.json').read_text(encoding='utf-8') == before
        assert gm.get_term('Orrin').translation == '奧林'
        # load() has the same rule
        (tmp_path / 'g.json').write_text(json.dumps([bad]), encoding='utf-8')
        with pytest.raises(ValueError):
            GlossaryManager(str(tmp_path / 'g.json')).load()

    def test_import_saves_once(self, tmp_path, monkeypatch):
        gm = GlossaryManager(str(tmp_path / 'g.json'))
        source = tmp_path / 'in.json'
        source.write_text(json.dumps([{'term': 'A', 'translation': '甲'}, {'term': 'B', 'translation': '乙'},
                                      {'term': 'C', 'translation': ''}]), encoding='utf-8')
        saves = []
        real = GlossaryManager.save
        monkeypatch.setattr(GlossaryManager, 'save', lambda self: saves.append(1) or real(self))
        assert gm.import_json(str(source)) == 2
        assert saves == [1]
        other = GlossaryManager(gm.glossary_path)
        other.load()
        assert [e.term for e in other.get_all_terms()] == ['A', 'B']
        empty = tmp_path / 'empty.json'
        empty.write_text('[]', encoding='utf-8')
        assert GlossaryManager(str(tmp_path / 'none.json')).import_json(str(empty)) == 0
        assert not (tmp_path / 'none.json').exists()

    def test_cli_extraction_merges_after_the_calls_and_saves_once(self, tmp_path, monkeypatch):
        path = _project(tmp_path)
        glossary = os.path.join(path, 'glossary.json')
        GlossaryManager(glossary).add_term('Tess', '泰絲', category='person')

        def respond(request):  # meanwhile, a note saved elsewhere
            other = GlossaryManager(glossary)
            other.load()
            other.add_term('Wren', '雷恩', category='person', note='a bird')
            return _reply(('Tess', '黛絲', 'person'), ('Orrin', '奧林', 'person'))

        saves = []
        real = GlossaryManager.save
        with use_fake_backend(FakeBackend(responder=respond)):
            monkeypatch.setattr(GlossaryManager, 'save', lambda self: saves.append(1) or real(self))
            result = CliRunner().invoke(_cli(), ['glossary', 'extract', '-p', path])
        assert result.exit_code == 0, result.output
        assert len(saves) == 2  # the note written meanwhile, then the extraction once
        gm = GlossaryManager(glossary)
        gm.load()
        assert [(e.term, e.translation, e.note) for e in gm.get_all_terms()] == [
            ('Orrin', '奧林', ''), ('Tess', '黛絲', ''), ('Wren', '雷恩', 'a bird')]

    def test_web_extraction_merges_after_the_calls_and_saves_once(self, web, monkeypatch):
        from src.web.blueprints.glossary import _extraction_tasks
        from tests.test_web import _wait
        client, root = web
        glossary = str(root / 'book' / 'glossary.json')
        GlossaryManager(glossary).add_term('Tess', '泰絲', category='person', note='the baker')

        def respond(request):
            other = GlossaryManager(glossary)
            other.load()
            other.update_term('Tess', note='the miller')  # edited while extracting
            return _reply(('Tess', '黛絲', 'person'), ('Orrin', '奧林', 'person'),
                          ('Wren', '雷恩', 'person'))

        saves = []
        real = GlossaryManager.save
        monkeypatch.setattr(GlossaryManager, 'save', lambda self: saves.append(1) or real(self))
        _extraction_tasks.pop('book', None)
        with use_fake_backend(FakeBackend(responder=respond)):
            client.post('/api/projects/book/glossary/extract')
            _wait(lambda: not _extraction_tasks['book']['running'])
        assert _extraction_tasks.pop('book')['extracted_count'] == 2
        assert len(saves) == 2
        gm = GlossaryManager(glossary)
        gm.load()
        assert [(e.term, e.translation, e.note) for e in gm.get_all_terms()] == [
            ('Orrin', '奧林', ''), ('Tess', '泰絲', 'the miller'), ('Wren', '雷恩', '')]

    def test_web_post_keeps_person_fields_not_sent(self, web):
        client, root = web
        client.post('/api/projects/book/glossary', json={
            'term': 'Tess', 'translation': '泰絲', 'category': 'person', 'gender': 'female',
            'note': 'the baker'})
        res = client.post('/api/projects/book/glossary',
                          json={'term': 'tess', 'translation': '黛絲', 'category': 'term'})
        assert res.get_json()['entry'] == {'term': 'tess', 'translation': '黛絲', 'category': 'term',
                                           'frequency': 0, 'gender': 'female', 'note': 'the baker'}
        res = client.post('/api/projects/book/glossary',
                          json={'term': 'Tess', 'translation': '黛絲', 'note': ''})
        assert (res.get_json()['entry']['gender'], res.get_json()['entry']['note']) == ('female', '')
        res = client.post('/api/projects/book/glossary', json={'term': 'Orrin', 'translation': '奧林'})
        assert (res.get_json()['entry']['gender'], res.get_json()['entry']['note']) == ('', '')

    def test_add_form_sends_person_fields_only_when_shown(self, web):
        client, _ = web
        page = client.get('/project/book/glossary').get_data(as_text=True)
        # round 3: sent only when shown and filled in (TestRound3)
        assert "if (entry.category === 'person' && gender.trim()) body.gender = gender;" in page
        assert 'body: JSON.stringify(body)' in page

    @pytest.mark.parametrize('body', [5, ['a'], {'terms': 'a'}, {'terms': ['Tess', 5]}, None])
    def test_batch_delete_refused(self, web, body):
        client, root = web
        client.post('/api/projects/book/glossary', json={'term': 'Tess', 'translation': '泰絲'})
        res = client.delete('/api/projects/book/glossary/batch', data=json.dumps(body),
                            content_type='application/json')
        assert (res.status_code, res.get_json()) == (400, {'error': '請提供 terms 陣列'})
        assert [t['term'] for t in client.get('/api/projects/book/glossary').get_json()] == ['Tess']

    def test_translation_page_initialized_once(self, web):
        client, _ = web
        page = client.get('/project/book/translation').get_data(as_text=True)
        # Alpine 3 calls init() itself; x-init="init()" ran it a second time
        assert '<div x-data="translationController()">' in page
        assert 'x-init="init()"' not in page

    @pytest.mark.parametrize('tab, component', [('glossary', 'glossaryManager'),
                                                ('export', 'exportController')])
    def test_other_pages_initialized_once(self, web, tab, component):
        client, _ = web
        page = client.get(f'/project/book/{tab}').get_data(as_text=True)
        # The same double init() as on the translation page (cleanup after Phase 9)
        assert f'<div x-data="{component}()">' in page
        assert 'x-init="init()"' not in page


class TestRound2Survivors:

    def test_fenced_list_over_several_lines(self):
        reply = '```json\n[\n  {"term": "Mira Vale", "translation": "米拉 維爾"}\n]\n```'
        assert te._top_level_list(reply) == [{'term': 'Mira Vale', 'translation': '米拉 維爾'}]

    def test_import_keeps_the_frequency(self, tmp_path):
        source = tmp_path / 'in.json'
        source.write_text(json.dumps([{'term': 'A', 'translation': '甲', 'frequency': 3}]),
                          encoding='utf-8')
        gm = GlossaryManager(str(tmp_path / 'g.json'))
        gm.import_json(str(source))
        assert gm.get_term('A').frequency == 3


# --- Review round 3 -----------------------------------------------------------------

class TestRound3:

    def test_loaded_and_imported_person_fields_one_line(self, tmp_path):
        path = tmp_path / 'g.json'
        path.write_text(json.dumps([{'term': 'Tess', 'translation': '泰絲', 'gender': ' female\n',
                                     'note': 'the\n baker'}]), encoding='utf-8')
        gm = GlossaryManager(str(path))
        gm.load()
        assert (gm.get_term('Tess').gender, gm.get_term('Tess').note) == ('female', 'the baker')
        assert gm.format_for_prompt() == 'Tess → 泰絲（性別：female；備註：the baker）'
        source = tmp_path / 'in.json'
        source.write_text(json.dumps([{'term': 'Wren', 'translation': '雷恩', 'note': ' a\n\nbird '}]),
                          encoding='utf-8')
        gm.import_json(str(source))
        assert gm.get_term('Wren').note == 'a bird'

    def test_add_form_sends_only_filled_person_fields(self, web):
        client, _ = web
        page = client.get('/project/book/glossary').get_data(as_text=True)
        assert "if (entry.category === 'person' && gender.trim()) body.gender = gender;" in page
        assert "if (entry.category === 'person' && note.trim()) body.note = note;" in page

    def test_brief_error_is_its_own_and_cleared_on_success(self, web):
        client, _ = web
        page = client.get('/project/book/translation').get_data(as_text=True)
        assert 'x-show="briefError" x-text="briefError"' in page
        assert "this.briefError = data.error || '簡介儲存失敗';" in page
        assert "this.savedBrief = data.brief;\n                    this.briefError = '';" in page
        assert "this.error = data.error || '簡介儲存失敗'" not in page
