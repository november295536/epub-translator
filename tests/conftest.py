"""Shared test fixtures."""

import os
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
def project_dir(tmp_path):
    """Provide a temporary project directory."""
    d = tmp_path / 'test_project'
    d.mkdir()
    return str(d)
