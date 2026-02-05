# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

EPUB 智能翻譯系統 - Translates English EPUB books to Traditional Chinese using Claude Code CLI as the translation engine. Supports terminology management, bilingual output (3 formats), Web review interface, and CLI workflow.

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run web server (recommended)
python3 -m flask --app src.web.server:create_app run --port 3000

# Run tests
python3 -m pytest tests/ -v

# CLI usage
python3 translate.py init <epub_file>
python3 translate.py start
python3 translate.py status
python3 translate.py glossary show
python3 translate.py glossary extract
python3 translate.py glossary add "term" "translation" -c category
python3 translate.py export --format bilingual-horizontal
python3 translate.py review
```

## Architecture

### Translation Workflow
1. **EPUB Extraction** (`src/epub/extractor.py`) — Parse EPUB, extract chapters as HTML
2. **Smart Segmentation** (`src/core/segmenter.py`) — Split into 300-500 word segments at sentence boundaries; merge small segments (<100 words), split large ones (>500 words)
3. **Terminology Extraction** (`src/glossary/term_extractor.py`) — Auto-extract technical terms via Claude CLI
4. **Translation** (`src/core/translator.py`) — Translate via `subprocess.run(['claude', '-p', prompt], input=text)` with segment-type-aware prompts and glossary context
5. **Review** (`src/web/`) — Web interface for side-by-side review and editing
6. **EPUB Reconstruction** (`src/epub/builder.py`) — Rebuild EPUB in chosen format

### Key Design Decisions

- **Claude CLI Integration**: Translations go through `subprocess.run(['claude', '-p', ...])` with stdin pipe. Each segment gets a type-specific prompt (text, heading, code, table).
- **JSON Persistence**: All state stored in `project.json` and `glossary.json` per project. No database required.
- **Web Architecture**: Flask app factory with Blueprints. Alpine.js for frontend reactivity (no build step). SSE for real-time translation progress. Background threading for long-running tasks.
- **Segmentation**: 300-500 words per segment, breaking at sentence boundaries. Code blocks, tables, and headings are isolated. Small consecutive text segments are merged; oversized segments are split.

### Module Structure

```
src/
├── core/
│   ├── project.py         # ProjectManager — JSON-based project state, segment CRUD
│   ├── segmenter.py       # Segmenter — HTML to segments with type detection
│   └── translator.py      # TranslationEngine — Claude CLI wrapper, prompt templates, response cleaning
├── epub/
│   ├── extractor.py       # EPUBExtractor — EPUB parsing, chapter extraction
│   └── builder.py         # EPUBBuilder — EPUB reconstruction (3 output formats)
├── glossary/
│   ├── glossary_manager.py  # GlossaryManager — term CRUD, JSON storage, text search
│   └── term_extractor.py    # TermExtractor — auto-extract terms via Claude CLI
└── web/
    ├── server.py          # Flask app factory with blueprint registration
    ├── tasks.py           # TaskManager — background translation/extraction with threading
    ├── blueprints/
    │   ├── home.py        # Project listing, creation
    │   ├── projects.py    # EPUB upload, project detail pages
    │   ├── glossary.py    # Glossary CRUD API + auto-extraction
    │   ├── translation.py # Translation start/pause/status + SSE streaming
    │   ├── review.py      # Segment update/approve API
    │   └── export.py      # EPUB export + download
    ├── templates/         # Jinja2 + Alpine.js (base, index, project tabs)
    └── static/            # CSS
```

### Special Content Handling

- **Code blocks**: Preserve formatting, translate only comments (`#`, `//`)
- **LaTeX formulas**: `$...$` and `$$...$$` kept unchanged
- **Tables**: `<table>` elements isolated as segments, HTML structure preserved
- **Headings**: Simplified prompt, no extra formatting

### Output Formats

| Format | Description |
|--------|-------------|
| `chinese-only` | Pure Traditional Chinese |
| `bilingual-horizontal` | Side-by-side (CSS flex layout) |
| `bilingual-vertical` | Top-bottom display |

### Web API Routes (key endpoints)

- `GET /` — Project listing
- `GET /new` — New project page
- `POST /api/upload` — Upload EPUB file
- `POST /api/epub/metadata` — Extract EPUB metadata
- `POST /api/projects` — Create project
- `GET /project/<name>/overview` — Project detail with tabs (overview, glossary, translation, review, export)
- `GET /api/projects/<name>` — Get project details JSON
- `GET /api/projects/<name>/chapters` — Get chapter list
- `GET /api/projects/<name>/glossary` — Get glossary entries
- `POST /api/projects/<name>/glossary` — Add glossary term
- `PUT /api/projects/<name>/glossary/<term>` — Update term
- `DELETE /api/projects/<name>/glossary/<term>` — Delete term
- `POST /api/projects/<name>/glossary/extract` — Auto-extract terms
- `POST /api/projects/<name>/translate/start` — Start background translation
- `POST /api/projects/<name>/translate/pause` — Pause translation
- `GET /api/projects/<name>/translate/stream` — SSE progress stream
- `POST /api/projects/<name>/segments/<id>` — Update segment translation
- `POST /api/projects/<name>/segments/<id>/approve` — Mark segment reviewed
- `GET /api/projects/<name>/progress` — Get translation progress
- `POST /api/projects/<name>/export` — Generate EPUB
- `GET /api/projects/<name>/download/<filename>` — Download generated EPUB

## Testing

113 tests covering all modules:
```bash
python3 -m pytest tests/ -v
```

Test files: `test_extractor.py`, `test_segmenter.py`, `test_translator.py`, `test_project.py`, `test_glossary.py`, `test_builder.py`, `test_web.py`, `test_cli.py`

## Dependencies

- `flask>=3.0` — Web server
- `ebooklib>=0.18` — EPUB read/write
- `beautifulsoup4>=4.12` + `lxml>=4.9` — HTML parsing
- `click>=8.1` — CLI framework
- `pytest>=7.4` — Testing
