"""Shared test fixtures."""

import os
import re
import pytest
from ebooklib import epub


@pytest.fixture
def sample_epub(tmp_path):
    """Create a small test EPUB file with 3 chapters."""
    book = epub.EpubBook()
    book.set_identifier('test-book-001')
    book.set_title('Test Book')
    book.set_language('en')
    book.add_author('Test Author')

    # Chapter 1 - short content with heading
    c1 = epub.EpubHtml(title='Introduction', file_name='chap01.xhtml', lang='en')
    c1.content = b'''<html><body>
<h1>Introduction</h1>
<p>This is the introduction to our test book. It contains some basic content
for testing the extraction and segmentation process.</p>
<p>The second paragraph provides additional context about the book.</p>
</body></html>'''

    # Chapter 2 - longer content with code block
    long_paragraphs = []
    for i in range(10):
        long_paragraphs.append(
            f'<p>This is paragraph {i+1} of the second chapter. It contains enough words '
            f'to help test the segmentation logic properly. We need multiple sentences here. '
            f'The segmentation algorithm should break content at sentence boundaries. '
            f'Each paragraph adds approximately fifty words to the total count. '
            f'This ensures we can test the 300-500 word range effectively.</p>'
        )

    c2 = epub.EpubHtml(title='Main Content', file_name='chap02.xhtml', lang='en')
    c2.content = (
        '<html><body>\n'
        '<h2>Chapter 1: Main Content</h2>\n'
        + '\n'.join(long_paragraphs)
        + '\n<pre><code>def hello():\n    # This is a comment\n    print("Hello World")\n</code></pre>\n'
        '</body></html>'
    ).encode('utf-8')

    # Chapter 3 - content with various HTML elements
    c3 = epub.EpubHtml(title='Advanced Topics', file_name='chap03.xhtml', lang='en')
    c3.content = b'''<html><body>
<h2>Advanced Topics</h2>
<p>This chapter covers advanced topics in our subject area. Machine learning
and deep learning are important concepts in modern computer science.</p>
<ul>
<li>First item in the list covers natural language processing.</li>
<li>Second item discusses computer vision applications.</li>
<li>Third item explores reinforcement learning techniques.</li>
</ul>
<p>In conclusion, these topics represent the cutting edge of technology.</p>
</body></html>'''

    book.add_item(c1)
    book.add_item(c2)
    book.add_item(c3)

    book.toc = [
        epub.Link('chap01.xhtml', 'Introduction', 'intro'),
        epub.Link('chap02.xhtml', 'Main Content', 'main'),
        epub.Link('chap03.xhtml', 'Advanced Topics', 'advanced'),
    ]

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    book.spine = ['nav', c1, c2, c3]

    epub_path = str(tmp_path / 'test_book.epub')
    epub.write_epub(epub_path, book)
    return epub_path


@pytest.fixture
def rich_sample_epub(tmp_path):
    """Create an EPUB with rich HTML: CSS, classes, blockquotes, lists."""
    book = epub.EpubBook()
    book.set_identifier('rich-book-001')
    book.set_title('Rich Test Book')
    book.set_language('en')
    book.add_author('Rich Author')

    # Add a CSS file
    css = epub.EpubItem(
        uid='style',
        file_name='style/main.css',
        media_type='text/css',
        content=b'.indent { text-indent: 2em; } .note { color: #666; font-style: italic; }'
    )
    book.add_item(css)

    c1 = epub.EpubHtml(title='Chapter One', file_name='chap01.xhtml', lang='en')
    c1.content = (
        '<html><head>'
        '<link href="style/main.css" rel="stylesheet" type="text/css"/>'
        '</head><body>'
        '<h1 id="ch1">Chapter One</h1>'
        '<p class="indent">This is an indented paragraph with a CSS class that should be preserved.</p>'
        '<blockquote>This is a blockquote element that should be preserved in the output.</blockquote>'
        '<p>A regular paragraph following the blockquote for additional context.</p>'
        '<ul>'
        '<li>First list item about important concepts in the domain.</li>'
        '<li>Second list item about other relevant things to discuss.</li>'
        '</ul>'
        '<p class="note">This is a note paragraph with special styling applied.</p>'
        '</body></html>'
    ).encode('utf-8')

    book.add_item(c1)
    book.toc = [epub.Link('chap01.xhtml', 'Chapter One', 'ch1')]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ['nav', c1]

    epub_path = str(tmp_path / 'rich_book.epub')
    epub.write_epub(epub_path, book)
    return epub_path


@pytest.fixture
def project_dir(tmp_path):
    """Provide a temporary project directory."""
    d = tmp_path / 'test_project'
    d.mkdir()
    return str(d)


@pytest.fixture
def epub_with_duplicate_spine(tmp_path):
    """Create an EPUB with duplicate spine entries (malformed EPUB)."""
    book = epub.EpubBook()
    book.set_identifier('dup-spine-001')
    book.set_title('Duplicate Spine Book')
    book.set_language('en')
    book.add_author('Test Author')

    c1 = epub.EpubHtml(title='Chapter One', file_name='chap01.xhtml', lang='en')
    c1.content = b'<html><body><h1>Chapter One</h1><p>First chapter content.</p></body></html>'

    c2 = epub.EpubHtml(title='Chapter Two', file_name='chap02.xhtml', lang='en')
    c2.content = b'<html><body><h1>Chapter Two</h1><p>Second chapter content.</p></body></html>'

    book.add_item(c1)
    book.add_item(c2)

    book.toc = [
        epub.Link('chap01.xhtml', 'Chapter One', 'ch1'),
        epub.Link('chap02.xhtml', 'Chapter Two', 'ch2'),
    ]

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # Deliberately add c1 twice to simulate malformed spine
    book.spine = ['nav', c1, c2, c1]

    epub_path = str(tmp_path / 'duplicate_spine.epub')
    epub.write_epub(epub_path, book)
    return epub_path


# --- Context call-site fixture ------------------------------------------------

CONTEXT_CODE = '    CODEMARK = 1  # set it\n    return CODEMARK'


def _context_segments():
    from src.core.segmenter import Segment, SegmentType

    def text(seg_id, body):
        return Segment(id=seg_id, html=f'<p>{body}</p>', text=body,
                       word_count=len(body.split()), segment_type=SegmentType.TEXT)

    def code(seg_id):
        return Segment(id=seg_id, html=f'<pre>{CONTEXT_CODE}</pre>', text=CONTEXT_CODE,
                       word_count=6, segment_type=SegmentType.CODE)

    long_previous = 'HEADMARK ' + 'filler words here ' * 20 + 'TAILMARK'
    assert len(long_previous) > 200
    return [
        text('chA_seg0001', 'PREVCHAPTERMARK ends chapter A.'),
        text('chB_seg0001', long_previous),
        code('chB_seg0002'),
        text('chB_seg0003', 'TARGETMARK is the segment under test.'),
        code('chB_seg0004'),
        text('chB_seg0005', 'NEXTTEXTMARK follows the code.'),
        text('chC_seg0001', 'NEXTCHAPTERMARK starts chapter C.'),
    ]


@pytest.fixture
def make_context_project():
    """Create a project whose pending segments exercise context selection.

    Chapter B is pending: a long text segment, code, the target text, code,
    another text. Chapters A and C (one text segment each) are already
    translated, so their text must never appear as context. The glossary
    has one term that occurs in no segment.
    """
    from src.core.project import ProjectManager

    def make(project_dir):
        pm = ProjectManager(project_dir)
        pm.create('/path/to/book.epub', 'Test Book', 'Author', _context_segments())
        pm.mark_translated('chA_seg0001', '甲')
        pm.mark_translated('chC_seg0001', '丙')
        pm.save()
        # A small glossary goes into every text prompt, matched or not
        from src.glossary.glossary_manager import GlossaryManager
        GlossaryManager(os.path.join(project_dir, 'glossary.json')).add_term(
            'GLOSSMARK', '詞彙')
        return project_dir

    return make


def fake_claude_run(cmd, *args, **kwargs):
    """subprocess.run stand-in: echoes code back (with an indented first
    line and a trailing newline), returns fixed HTML for text."""
    from unittest.mock import MagicMock
    result = MagicMock()
    result.returncode = 0
    result.stderr = ''
    prompt = cmd[2]
    result.stdout = (CONTEXT_CODE + '\n') if 'CODEMARK' in prompt and '<p k=' not in prompt \
        else '<p k="1">譯文</p>'
    return result


# Where the text to translate starts in each prompt (src/core/translator.py)
_PROMPT_TEXT_MARKERS = ('第一個字元就是 <\n\n', '不加任何說明。\n\n', '原文：\n')


def echo_reply(prompt: str) -> str:
    """A reply that passes the checks: the HTML (in its prompt form) or the
    code of the prompt's first message, unchanged."""
    for marker in _PROMPT_TEXT_MARKERS:
        if marker in prompt:
            return prompt.split(marker, 1)[1]
    raise AssertionError('not a translation prompt')


def like(request, text: str) -> str:
    """A reply that passes the checks for a one-element segment: the
    request's HTML (prompt form) with every text node replaced by `text`."""
    return re.sub(r'>[^<]+<', f'>{text}<', echo_reply(request.messages[0].content))


def echo_process(cmd, *args, **kwargs):
    """run_process stand-in for the Claude CLI: answers with echo_reply."""
    from src.llm.cli import ProcessResult
    return ProcessResult(0, echo_reply(cmd[2]), '')


def assert_context_prompts(prompts, project_dir):
    """Check the prompts sent for chapter B and the stored code translation."""
    from src.core.project import ProjectManager

    def prompt_for(mark):
        matches = [p for p in prompts if f'<p k="1">{mark}' in p]
        assert len(matches) == 1, mark
        return matches[0]

    for mark in ('HEADMARK', 'TARGETMARK', 'NEXTTEXTMARK'):
        assert 'GLOSSMARK → 詞彙' in prompt_for(mark)   # small glossary: all entries

    first = prompt_for('HEADMARK')
    assert 'PREVCHAPTERMARK' not in first          # other chapter
    assert 'TARGETMARK' in first                   # code stepped over

    target = prompt_for('TARGETMARK')
    assert 'TAILMARK' in target                    # tail of the long previous
    assert 'HEADMARK' not in target                # not its head
    assert 'CODEMARK' not in target                # code never used as context
    assert 'NEXTTEXTMARK' in target                # code stepped over

    last = prompt_for('NEXTTEXTMARK')
    assert 'NEXTCHAPTERMARK' not in last           # other chapter
    assert 'TARGETMARK' in last

    pm = ProjectManager(project_dir)
    pm.load()
    stored = {s.segment_id: s for s in pm.data.segments}
    assert stored['chB_seg0002'].translated_text == CONTEXT_CODE
    assert stored['chB_seg0004'].translated_text == CONTEXT_CODE


# --- Backend seam -------------------------------------------------------------

def use_fake_backend(fake):
    """Make every engine and extractor created inside the block use `fake`."""
    from unittest.mock import patch
    return patch('src.llm.default_backend', return_value=fake)


# --- Project lock held by another process --------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_HOLD_LOCK = '''
import sys
sys.path.insert(0, sys.argv[1])
from src.core.project import ProjectLock
ProjectLock(sys.argv[2]).acquire()
print('locked', flush=True)
sys.stdin.read()  # hold until the parent closes stdin or kills us
'''


def hold_lock_in_child(project_dir):
    """Start a separate process that takes the project lock and keeps it.
    Close its stdin to let it exit normally, or kill it."""
    import subprocess
    import sys
    child = subprocess.Popen([sys.executable, '-c', _HOLD_LOCK, _REPO_ROOT, project_dir],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == 'locked'
    return child


def lock_free_for_other_process(project_dir):
    """True when a separate process can take the project lock now."""
    import subprocess
    import sys
    code = ('import sys; sys.path.insert(0, sys.argv[1]); '
            'from src.core.project import ProjectLock, ProjectLockedError\n'
            'try:\n    ProjectLock(sys.argv[2]).acquire()\n'
            'except ProjectLockedError:\n    sys.exit(3)\n')
    result = subprocess.run([sys.executable, '-c', code, _REPO_ROOT, project_dir])
    return result.returncode == 0


@pytest.fixture(autouse=True)
def no_fake_backend_overcalls():
    """A scripted FakeBackend called more often than scripted fails the test,
    also when the call was made in a worker thread (where the exception
    only ends the thread)."""
    from src.llm.fake import FakeBackend
    FakeBackend.overcalls.clear()
    yield
    overcalls = list(FakeBackend.overcalls)
    FakeBackend.overcalls.clear()
    assert not overcalls, f'FakeBackend called {len(overcalls)} time(s) beyond its script'


@pytest.fixture(autouse=True)
def no_real_model_calls(monkeypatch):
    """No test starts the real `claude` program: a call that reaches the
    process layer without a test's own stub fails like an error exit.
    (Tests of run_process itself import the function directly.)"""
    from src.llm.cli import ProcessResult

    def refuse(cmd, **kwargs):
        return ProcessResult(1, '', 'tests do not run the real CLI')

    monkeypatch.setattr('src.llm.cli.run_process', refuse)


# --- Parsed chapter bodies -----------------------------------------------------

def body_of(html: str):
    """The <body> element of `html` parsed as XML, as the extractor parses a
    chapter; a fragment without <body> is put into one."""
    from src.epub.extractor import find_body, parse_xml
    if '<body' not in html:
        html = f'<html><body>{html}</body></html>'
    return find_body(parse_xml(html.encode('utf-8')))


# --- EPUBs written as raw ZIP archives (plan section 7) -----------------------

def xhtml(body: str, head: str = '<title>Chapter</title>') -> str:
    """An XHTML chapter with the given head and body content."""
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops">\n'
            f'<head>{head}</head>\n<body>{body}</body>\n</html>\n')


def write_raw_epub(path, items, spine, members, opf_path='OEBPS/content.opf',
                   title='The Lantern Keeper', author='Invented Author', version='3.0'):
    """Write an EPUB as a plain ZIP archive, without ebooklib.

    items: (id, href, media type, properties or None) for the manifest;
    spine: item ids in order; members: archive name -> content.
    """
    import zipfile
    from xml.sax.saxutils import quoteattr
    manifest = ''.join(
        f'<item id={quoteattr(i)} href={quoteattr(h)} media-type={quoteattr(m)}'
        + (f' properties={quoteattr(p)}' if p else '') + '/>'
        for i, h, m, p in items)
    refs = ''.join(f'<itemref idref={quoteattr(i)}/>' for i in spine)
    toc = ' toc="ncx"' if any(item[0] == 'ncx' for item in items) else ''
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           f'<package xmlns="http://www.idpf.org/2007/opf" version="{version}" '
           'unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:identifier id="uid">invented-0001</dc:identifier><dc:title>{title}</dc:title>'
           f'<dc:creator>{author}</dc:creator><dc:language>en</dc:language></metadata>'
           f'<manifest>{manifest}</manifest><spine{toc}>{refs}</spine></package>')
    container = ('<?xml version="1.0"?>\n<container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                 f'<rootfile full-path="{opf_path}" media-type="application/oebps-package+xml"/>'
                 '</rootfiles></container>')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip',
                   compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', container)
        z.writestr(opf_path, opf)
        for name, content in members.items():
            z.writestr(name, content)
    return str(path)


RAW_NAV = xhtml('<nav epub:type="toc"><ol><li><a href="Text/one.xhtml">One</a></li>'
                '</ol></nav>', head='<title>Contents</title>')
RAW_NCX = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>'
           '<navPoint id="n1"><navLabel><text>One</text></navLabel>'
           '<content src="Text/one.xhtml"/></navPoint></navMap></ncx>')
RAW_CHAPTER_ONE = xhtml(
    '<h1 id="c1">The Lantern Keeper</h1>'
    '<p class="first">Mira climbed the <em>spiral</em> stairs at dusk.</p>'
    '<!-- editor note: check spelling -->'
    'A line of bare text under the body.'
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="5" height="5"/></svg>'
    '<div class="verse">First line of the song<br/>Second line of the song<br/>'
    'Third line of the song</div>',
    head='<title>One</title><link rel="stylesheet" type="text/css" href="../Styles/book.css"/>'
         '<style>p.first { text-indent: 0; }</style>')
RAW_CHAPTER_TWO = xhtml('<h2>Second Evening</h2><p>The harbour bells rang twice.</p>')


@pytest.fixture
def raw_epub(tmp_path):
    """An EPUB written as a raw ZIP: OEBPS/ folder, chapters in a subfolder,
    a percent-encoded href, a head with <link> and <style>, NCX plus a
    navigation document that is also in the spine, inline SVG with viewBox,
    text directly under <body>, <br>-separated runs, a comment under <body>,
    and a duplicated spine entry."""
    items = [
        ('nav', 'nav.xhtml', 'application/xhtml+xml', 'nav'),
        ('ncx', 'toc.ncx', 'application/x-dtbncx+xml', None),
        ('css', 'Styles/book.css', 'text/css', None),
        ('one', 'Text/one.xhtml', 'application/xhtml+xml', None),
        ('two', 'Text/chapter%20two.xhtml', 'application/xhtml+xml', None),
    ]
    members = {
        'OEBPS/nav.xhtml': RAW_NAV,
        'OEBPS/toc.ncx': RAW_NCX,
        'OEBPS/Styles/book.css': 'p { margin: 0; }',
        'OEBPS/Text/one.xhtml': RAW_CHAPTER_ONE,
        'OEBPS/Text/chapter two.xhtml': RAW_CHAPTER_TWO,
    }
    return write_raw_epub(tmp_path / 'raw.epub', items, ['nav', 'one', 'two', 'one'], members)
