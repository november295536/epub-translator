# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

EPUB 智能翻譯系統 - A tool for translating English EPUB books to Traditional Chinese using Claude Code CLI as the translation engine. The system supports bilingual output, professional terminology management, and manual proofreading.

## Development Commands

### Project Setup (when implemented)
```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt
```

### Running the Application (when implemented)
```bash
# GUI mode
python gui_launcher.py

# CLI mode
python translate.py init <epub_file>
python translate.py start
python translate.py resume
python translate.py review
python translate.py export --format bilingual-horizontal
```

## Architecture Overview

### Core Translation Workflow
1. **EPUB Extraction** → Extract HTML content while preserving structure
2. **Smart Segmentation** → Split into 300-500 word segments at natural breaks
3. **Terminology Extraction** → Build glossary with Taiwan-specific terms
4. **Claude Code Translation** → Translate via CLI with terminology consistency
5. **Manual Review** → Edit in GUI with side-by-side preview
6. **EPUB Reconstruction** → Rebuild with chosen output format

### Key Design Decisions

**Claude Code Integration**: All translations go through Claude Code CLI using pipes:
```bash
echo "$text" | claude -p "Translate to Traditional Chinese with glossary: $glossary"
```

**Segmentation Strategy**: 
- Segments are 300-500 words, breaking at sentence boundaries
- Code blocks and formulas are isolated segments
- Maintains paragraph and heading integrity

**Terminology Management**:
- Auto-extract technical terms
- Search for Taiwan-specific translations
- Enforce consistency across entire book
- Terms shown as: 機器學習（machine learning）

**Progress Tracking**: JSON-based project files allow resuming interrupted translations at exact segment.

### Module Structure

- **src/core/**: Business logic (ProjectManager, TranslationEngine, SegmentProcessor)
- **src/epub/**: EPUB handling (EPUBExtractor, EPUBBuilder)
- **src/glossary/**: Terminology (GlossaryManager, Taiwan term search)
- **src/ui/**: Interfaces (MainWindow for GUI, CLI commands)
- **data/**: Glossaries, cache, project files
- **gui/**: PyQt5 components for review editor

### Special Content Handling

- **Code blocks**: Preserve formatting, translate only comments
- **LaTeX formulas**: Keep unchanged
- **Images**: Simple diagrams → translate text; Complex → keep original
- **Tables**: Translate cell content while preserving structure

### Output Format Options

1. **chinese-only**: Pure Chinese translation
2. **bilingual-horizontal**: Side-by-side display
3. **bilingual-vertical**: Top-bottom display
4. **All formats**: Calibre-compatible EPUB

## Current Status

**Documentation Phase** - All specifications complete. Implementation pending for:
- Python package structure
- GUI with PyQt5
- CLI with Click
- EPUB processing with ebooklib
- SQLite glossary database