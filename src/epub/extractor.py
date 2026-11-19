"""EPUB extraction module - reads EPUB files and extracts chapter content."""

from __future__ import annotations

import re

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
from bs4.element import PreformattedString
from dataclasses import dataclass


def _serialize(node) -> str:
    """Serialize a body child.

    str() of a comment (or CData, processing instruction, doctype) returns
    its bare content without markers, which would turn it into visible text.
    Those keep their markers; the segmenter then skips them.
    """
    if isinstance(node, PreformattedString):
        return node.output_ready()
    return str(node)


@dataclass
class Chapter:
    """Represents a single chapter extracted from an EPUB."""
    id: str
    title: str
    html_content: str
    text_content: str
    order: int


class EPUBExtractor:
    """Extracts chapters and content from EPUB files."""

    def __init__(self, epub_path: str):
        self.epub_path = epub_path
        self.book = None

    def load(self) -> None:
        """Load the EPUB file."""
        self.book = epub.read_epub(self.epub_path)

    def get_metadata(self) -> dict:
        """Extract book metadata."""
        if not self.book:
            raise RuntimeError("EPUB not loaded. Call load() first.")

        title = self.book.get_metadata('DC', 'title')
        author = self.book.get_metadata('DC', 'creator')
        language = self.book.get_metadata('DC', 'language')

        return {
            'title': title[0][0] if title else 'Unknown',
            'author': author[0][0] if author else 'Unknown',
            'language': language[0][0] if language else 'Unknown',
        }

    def extract_chapters(self) -> list[Chapter]:
        """Extract all document chapters from the EPUB.

        Returns chapters in spine order, filtering out non-content items
        like cover pages, table of contents, etc.
        """
        if not self.book:
            raise RuntimeError("EPUB not loaded. Call load() first.")

        chapters = []
        spine_ids = [item_id for item_id, _ in self.book.spine]
        seen_ids: set[str] = set()

        for order, item_id in enumerate(spine_ids):
            # Skip duplicate spine entries (some EPUBs have malformed spines)
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            item = self.book.get_item_with_id(item_id)
            if item is None:
                continue
            if item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue

            html_content = item.get_content().decode('utf-8')
            soup = BeautifulSoup(html_content, 'lxml')

            body = soup.find('body')
            if body is None:
                continue

            text = body.get_text(separator=' ', strip=True)
            text = re.sub(r' +', ' ', text)
            if not text.strip():
                continue

            title = self._extract_title(soup)
            body_html = ''.join(_serialize(child) for child in body.children)

            chapters.append(Chapter(
                id=item_id,
                title=title,
                html_content=body_html,
                text_content=text,
                order=order,
            ))

        return chapters

    def _extract_title(self, soup: BeautifulSoup) -> str:
        """Try to extract a chapter title from heading tags."""
        for tag in ['h1', 'h2', 'h3']:
            heading = soup.find(tag)
            if heading:
                return heading.get_text(strip=True)
        return 'Untitled'
