"""Glossary manager - manages terminology for consistent translation."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
from dataclasses import dataclass, asdict, field


CATEGORY_LABELS = {
    'person': '人名',
    'place': '地名',
    'work': '作品名',
    'organization': '組織',
    'term': '術語',
    'expression': '慣用語',
    'other': '其他',
}


# Up to this many entries the whole glossary goes into every translation
# prompt; above it, only the entries found in the segment text.
FULL_GLOSSARY_MAX_TERMS = 150


def term_pattern(term: str) -> str:
    """Regex for a glossary term: it starts at a word boundary, with any
    continuation, case-insensitive.

    (?<!\w) is used instead of \b so that terms starting with a non-word
    character (.NET) work; there is no trailing boundary, so every suffix
    inflection matches (API -> APIs, render -> rendering). Whitespace inside
    a term matches any whitespace run (newline, NBSP).

    Accepted, since a false positive only adds a few prompt tokens: other
    words that start with the term also match (Go -> going, C inside C++),
    '.NET' does not match inside 'ASP.NET', and a term stored in plural form
    does not match its singular.
    """
    return r'(?<!\w)' + r'\s+'.join(re.escape(part) for part in term.split())


logger = logging.getLogger(__name__)

# The fields of a stored entry that must be strings where present
TEXT_FIELDS = ('term', 'translation', 'category', 'notes', 'context', 'gender', 'note')

# Optional fields of an entry (for persons): stored only when not empty, so
# a glossary without them keeps its file format
OPTIONAL_FIELDS = ('gender', 'note')


def load_glossary(path: str):
    """The glossary at `path`, or None when it cannot be read (unreadable,
    not JSON, not a list of entries); the failure is logged. For the
    glossary warnings, which are optional: a caller goes on without them."""
    gm = GlossaryManager(path)
    try:
        gm.load()
    except Exception as e:  # noqa: BLE001 - any damage: no glossary warnings
        logger.warning("術語表無法讀取，略過術語檢查 (%s)", type(e).__name__)
        return None
    return gm


@dataclass
class GlossaryEntry:
    """A single glossary entry."""
    term: str
    translation: str
    category: str = ''
    frequency: int = 0
    gender: str = ''
    note: str = ''


def entry_dict(entry: GlossaryEntry) -> dict:
    """The stored form of an entry: OPTIONAL_FIELDS only when not empty."""
    raw = asdict(entry)
    for key in OPTIONAL_FIELDS:
        if not raw[key]:
            del raw[key]
    return raw


class GlossaryManager:
    """Manages a glossary of terms for translation consistency.

    Stores entries as JSON and provides CRUD operations,
    text searching, and prompt formatting.
    """

    def __init__(self, glossary_path: str):
        self.glossary_path = glossary_path
        self.entries: dict[str, GlossaryEntry] = {}

    def load(self) -> None:
        """Load glossary from JSON file. Raises ValueError (also for JSON
        that does not decode) when the file is not a list of entries whose
        text fields (TEXT_FIELDS) are strings where present."""
        if not os.path.isfile(self.glossary_path):
            return
        with open(self.glossary_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            raise ValueError('the glossary is not a list')
        entries = {}
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get('term'), str) \
                    or any(not isinstance(item[k], str) for k in TEXT_FIELDS if k in item):
                raise ValueError('a glossary entry is not an object with text fields')
            entries[item['term'].lower()] = GlossaryEntry(**item)
        self.entries = entries

    def save(self) -> None:
        """Save glossary to JSON file, atomically: written to a temporary
        file in the directory of the real file (a symlinked glossary.json
        stays a symlink) and moved over it, so a reader never sees a
        half-written file. The old file's mode is kept; a new file is created
        with 0o666, so the kernel applies the umask (no os.umask call: it is
        process-wide)."""
        target = os.path.realpath(self.glossary_path)
        directory = os.path.dirname(target)
        os.makedirs(directory, exist_ok=True)
        raw = [entry_dict(e) for e in self.entries.values()]
        raw.sort(key=lambda x: x['term'].lower())
        try:
            mode = os.stat(target).st_mode & 0o777
        except FileNotFoundError:
            mode = None
        tmp = os.path.join(directory, f'.glossary-{secrets.token_hex(8)}.tmp')
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                if mode is not None:
                    try:
                        os.fchmod(f.fileno(), mode)
                    except OSError:  # the file is still written, with the default mode
                        logger.warning("無法沿用術語表的權限設定")
                json.dump(raw, f, ensure_ascii=False, indent=2)
            os.replace(tmp, target)
        except BaseException:
            os.unlink(tmp)
            raise

    def add_term(self, term: str, translation: str,
                 category: str = '', frequency: int = 0,
                 gender: str = '', note: str = '') -> GlossaryEntry:
        """Add or update a glossary entry."""
        key = term.lower()
        entry = GlossaryEntry(
            term=term, translation=translation,
            category=category, frequency=frequency, gender=gender, note=note,
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
                    category: str | None = None, gender: str | None = None,
                    note: str | None = None) -> GlossaryEntry | None:
        """Update an existing glossary entry (None: that field stays).
        Returns None if not found."""
        key = term.lower()
        entry = self.entries.get(key)
        if entry is None:
            return None
        if translation is not None:
            entry.translation = translation
        if category is not None:
            entry.category = category
        if gender is not None:
            entry.gender = gender
        if note is not None:
            entry.note = note
        self.save()
        return entry

    def get_term(self, term: str) -> GlossaryEntry | None:
        """Look up a single term."""
        return self.entries.get(term.lower())

    def get_all_terms(self) -> list[GlossaryEntry]:
        """Return all glossary entries sorted alphabetically."""
        return sorted(self.entries.values(), key=lambda e: e.term.lower())

    def find_terms_in_text(self, text: str) -> list[GlossaryEntry]:
        """Find all glossary terms that appear in the given text (see term_pattern).

        An empty or whitespace-only term never matches."""
        found = []
        for entry in self.entries.values():
            if entry.term.strip() and re.search(term_pattern(entry.term), text, re.IGNORECASE):
                found.append(entry)
        return sorted(found, key=lambda e: e.term.lower())

    def terms_for_prompt(self, text: str) -> list[GlossaryEntry]:
        """Entries to put in the translation prompt for a segment: all of
        them when the glossary is small, otherwise those found in the text."""
        if len(self.entries) <= FULL_GLOSSARY_MAX_TERMS:
            return self.get_all_terms()
        return self.find_terms_in_text(text)

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
            details = [f'{name}：{value}' for name, value in
                       (('性別', e.gender), ('備註', e.note)) if value]
            if details:
                line += '（' + '；'.join(details) + '）'
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
                    gender=item.get('gender', ''),
                    note=item.get('note', ''),
                )
                count += 1
        return count

    def export_json(self, json_path: str) -> int:
        """Export entries to a JSON file. Returns number of entries exported."""
        entries = self.get_all_terms()
        raw = [entry_dict(e) for e in entries]
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        return len(raw)
