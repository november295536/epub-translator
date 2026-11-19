"""Glossary manager - manages terminology for consistent translation."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict, field
from functools import lru_cache


CATEGORY_LABELS = {
    'person': '人名',
    'place': '地名',
    'work': '作品名',
    'organization': '組織',
    'term': '術語',
    'expression': '慣用語',
    'other': '其他',
}


# Suffixes accepted after a term that ends in a letter and whose last word
# has at least INFLECTION_MIN_LETTERS letters.
INFLECTION_MIN_LETTERS = 4
_VOWELS = 'aeiou'
_NO_DOUBLING = _VOWELS + 'wxy'


def _escape_words(text: str) -> str:
    """Escape text for a regex; any run of whitespace matches \\s+."""
    return r'\s+'.join(re.escape(part) for part in text.split())


def _term_forms(term: str) -> list[tuple[str, tuple[str, ...]]]:
    """Return (stem, suffixes) pairs; a match is a stem followed by one suffix."""
    if not term[-1].isalpha():
        return [(term, ('',))]

    last = term[-1].lower()
    if last == 'y' and len(term) > 1:
        forms = [(term, ('', 's')), (term[:-1], ('ies',))]
    else:
        forms = [(term, ('', 's', 'es'))]

    last_word = re.search(r'[^\W\d_]+$', term).group()
    if len(last_word) < INFLECTION_MIN_LETTERS:
        return forms

    if last == 'e':
        # cache -> cached, cacher(s), caching; manage -> management(s)
        forms.append((term, ('d', 'r', 'rs', 'ment', 'ments')))
        forms.append((term[:-1], ('ing',)))
    else:
        forms.append((term, ('ed', 'ing', 'er', 'ers', 'ment', 'ments')))
        if last not in _NO_DOUBLING and term[-2].lower() in _VOWELS:
            # debug -> debugged, debugging, debugger(s)
            forms.append((term + term[-1], ('ed', 'ing', 'er', 'ers')))
    return forms


@lru_cache(maxsize=None)
def _term_pattern(term: str) -> re.Pattern:
    """Compile a whole-word pattern for a glossary term.

    Lookarounds are used instead of \\b so that terms starting or ending
    with a non-word character (C++, C#, .NET) still match. Because of the
    leading (?<!\\w), '.NET' does not match inside 'ASP.NET'.

    - A term ending in a letter also matches its plurals (s, es, y -> ies).
    - If its last word has at least INFLECTION_MIN_LETTERS letters, it also
      matches ed/d, ing (with e-drop and a doubled final consonant), er/ers
      and ment/ments.
    - All-caps terms (API, IT, US) are case-sensitive, so IT does not match
      "its"; the suffix itself may be in any case. Other terms are
      case-insensitive.
    - Whitespace inside a multi-word term matches any whitespace run
      (newline, NBSP).
    """
    term = term.strip()
    if not term:
        return re.compile(r'(?!)')
    case_sensitive = any(c.isalpha() for c in term) and term == term.upper()

    alternatives = []
    for stem, suffixes in _term_forms(term):
        suffix_group = '|'.join(suffixes)
        if case_sensitive:
            suffix_group = '(?i:' + suffix_group + ')'
        alternatives.append(_escape_words(stem) + '(?:' + suffix_group + ')')

    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(r'(?<!\w)(?:' + '|'.join(alternatives) + r')(?!\w)', flags)


@dataclass
class GlossaryEntry:
    """A single glossary entry."""
    term: str
    translation: str
    category: str = ''
    frequency: int = 0


class GlossaryManager:
    """Manages a glossary of terms for translation consistency.

    Stores entries as JSON and provides CRUD operations,
    text searching, and prompt formatting.
    """

    def __init__(self, glossary_path: str):
        self.glossary_path = glossary_path
        self.entries: dict[str, GlossaryEntry] = {}

    def load(self) -> None:
        """Load glossary from JSON file."""
        if not os.path.isfile(self.glossary_path):
            return
        with open(self.glossary_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        self.entries = {}
        for item in raw:
            key = item['term'].lower()
            self.entries[key] = GlossaryEntry(**item)

    def save(self) -> None:
        """Save glossary to JSON file."""
        os.makedirs(os.path.dirname(self.glossary_path) or '.', exist_ok=True)
        raw = [asdict(e) for e in self.entries.values()]
        raw.sort(key=lambda x: x['term'].lower())
        with open(self.glossary_path, 'w', encoding='utf-8') as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)

    def add_term(self, term: str, translation: str,
                 category: str = '', frequency: int = 0) -> GlossaryEntry:
        """Add or update a glossary entry."""
        key = term.lower()
        entry = GlossaryEntry(
            term=term, translation=translation,
            category=category, frequency=frequency,
        )
        self.entries[key] = entry
        self.save()
        return entry

    def remove_term(self, term: str) -> bool:
        """Remove a glossary entry. Returns True if removed."""
        key = term.lower()
        if key in self.entries:
            del self.entries[key]
            self.save()
            return True
        return False

    def update_term(self, term: str, translation: str | None = None,
                    category: str | None = None) -> GlossaryEntry | None:
        """Update an existing glossary entry. Returns None if not found."""
        key = term.lower()
        entry = self.entries.get(key)
        if entry is None:
            return None
        if translation is not None:
            entry.translation = translation
        if category is not None:
            entry.category = category
        self.save()
        return entry

    def get_term(self, term: str) -> GlossaryEntry | None:
        """Look up a single term."""
        return self.entries.get(term.lower())

    def get_all_terms(self) -> list[GlossaryEntry]:
        """Return all glossary entries sorted alphabetically."""
        return sorted(self.entries.values(), key=lambda e: e.term.lower())

    def find_terms_in_text(self, text: str) -> list[GlossaryEntry]:
        """Find all glossary terms that appear in the given text."""
        found = []
        for entry in self.entries.values():
            if _term_pattern(entry.term).search(text):
                found.append(entry)
        return sorted(found, key=lambda e: e.term.lower())

    def format_for_prompt(self, entries: list[GlossaryEntry] | None = None) -> str:
        """Format glossary entries as a string for inclusion in a translation prompt."""
        if entries is None:
            entries = self.get_all_terms()
        if not entries:
            return ''
        lines = []
        for e in entries:
            line = f'{e.term} → {e.translation}'
            if e.category:
                label = CATEGORY_LABELS.get(e.category, e.category)
                line += f' [{label}]'
            lines.append(line)
        return '\n'.join(lines)

    def import_json(self, json_path: str) -> int:
        """Import entries from a JSON file. Returns number of entries imported."""
        with open(json_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        count = 0
        for item in raw:
            term = item.get('term', '')
            translation = item.get('translation', '')
            if term and translation:
                self.add_term(
                    term=term,
                    translation=translation,
                    category=item.get('category', ''),
                    frequency=item.get('frequency', 0),
                )
                count += 1
        return count

    def export_json(self, json_path: str) -> int:
        """Export entries to a JSON file. Returns number of entries exported."""
        entries = self.get_all_terms()
        raw = [asdict(e) for e in entries]
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        return len(raw)
