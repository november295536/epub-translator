"""Glossary manager - manages terminology for consistent translation."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict, field


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
        text_lower = text.lower()
        found = []
        for entry in self.entries.values():
            if entry.term.lower() in text_lower:
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
                line += f' [{e.category}]'
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
