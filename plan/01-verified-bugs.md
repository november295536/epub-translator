# Verified bugs affecting final output

Found on 2026-11-19 while comparing this project against `oomol-lab/epub-translator`.
All three were reproduced against the current code and the existing exports under `projects/`.
None of them raise an error; the output is silently degraded.

Status: recorded, not fixed.

The local test projects are referred to as `project-1` … `project-8` (alphabetical order of
their directory names) in this file and in the improvement plan.

---

## Bug 1 — Exported EPUBs lose all original styling

**Severity:** high (affects every export, every format)

**Symptom.** Every chapter in an exported EPUB has an empty `<head/>`. The original
`<link rel="stylesheet">` and inline `<style>` elements are gone, so the book renders with
reader-default styling.

**Evidence** (stylesheet links / `<style>` tags in chapter heads, source → export):

| Project | CSS links | `<style>` tags |
|---|---|---|
| project-5 | 35 → 0 | 37 → 0 |
| project-2 | 35 → 0 | 0 → 0 |
| project-7 | 140 → 0 | 1 → 0 |
| project-6 | 216 → 0 | 1 → 0 |
| project-8 | 178 → 0 | 1 → 0 |
| project-4 | 3528 → 0 | 0 → 0 |

The CSS files themselves are still inside the EPUB; nothing references them.

**Cause.** `EPUBBuilder.build()` (`src/epub/builder.py:69-104`) round-trips the book through
`ebooklib` (`epub.read_epub` → `copy.deepcopy` → `epub.write_epub`). On write, ebooklib
regenerates each chapter document from a template and rebuilds `<head>` only from
`item.title` and `item.links`. `read_epub` does not populate `item.links`, so the original
head content is dropped. The same round trip also renames the content folder
(`OEBPS/` → `EPUB/`) and regenerates the OPF and navigation files.

**Fix options.**

1. *Preferred:* stop round-tripping through ebooklib for export. Open the source EPUB as a
   ZIP, replace only the chapter files that were translated (and the TOC files), and copy
   every other entry byte-for-byte. `mimetype` must stay the first entry, stored
   uncompressed. This also fixes Bug 2 and makes it straightforward to set the language
   metadata.
2. *Minimal:* before writing, parse each source chapter's `<head>` and re-add its stylesheet
   links through `item.add_link(...)`. Inline `<style>` blocks would still be lost unless
   moved into a CSS file.

**Verification.** Export a project, then for every chapter compare the set of stylesheet
hrefs and the count of `<style>` elements in `<head>` against the source EPUB. Add a
regression test using the `rich_sample_epub` fixture (it already contains CSS).

---

## Bug 2 — Bilingual stylesheet is added but never linked

**Severity:** high for bilingual formats (both `bilingual-horizontal` and `bilingual-vertical`)

**Symptom.** The side-by-side flex layout, the grey/smaller original text, and the
separators are not applied. Original and translation appear as plain consecutive blocks.

**Evidence.** In all 7 existing bilingual exports, `EPUB/style/bilingual.css` exists in the
archive, chapters use the `bilingual-container` / `bilingual-vertical` classes, and
0 chapters link to the stylesheet.

**Cause.** `src/epub/builder.py:77-83` adds the CSS as a book item but never attaches it to
any chapter. Because of Bug 1, even a `<link>` written directly into the chapter HTML would
be stripped on write.

**Fix.** Depends on the fix chosen for Bug 1:

- With the ZIP-based export: insert a `<link>` into each transformed chapter's `<head>`,
  with the href computed relative to that chapter's path, and register the CSS file in the
  OPF manifest.
- With the minimal ebooklib fix: call `item.add_link(href=..., rel='stylesheet',
  type='text/css')` on each transformed chapter, with a correct relative path.

**Verification.** In a bilingual export, every chapter that contains a bilingual class must
link to `bilingual.css`, and the link must resolve to an entry in the archive.

---

## Bug 3 — Markdown cleanup corrupts translated code blocks

**Severity:** high for technical books

**Symptom.** Code in translated code blocks is altered:

```
input : def __init__(self, *args, **kwargs):
output: def init(self, args, *kwargs):

input : self.__dict__ = {}
output: self.dict = {}

input : # load config        (first line of the block)
output: load config
```

**Cause.** `TranslationEngine.translate()` calls
`_clean_response(translated, html_mode=use_html_mode)` (`src/core/translator.py:204`), and
`use_html_mode` is `False` for `SegmentType.CODE`. The non-HTML branch
(`src/core/translator.py:277-285`) strips markdown headings, bold, and italic markers, which
match `# comment`, `__dunder__`, and `*args` in source code. In addition,
`result.stdout.strip()` (`translator.py:192`) removes the leading indentation of the first
line of a code block.

**Fix.**

- Skip the markdown-stripping step for code segments; only remove a wrapping code fence.
- Preserve leading whitespace for code (strip trailing whitespace and surrounding blank
  lines only).
- Do not send a code segment to the model at all when it contains no comment marker.
  In project-3, 1337 of 1429 code segments contain neither `#` nor `//`.

**Existing data.** Already-translated code segments may be corrupted. After the fix, find
them by comparing each code segment's translation with its source after removing comment
lines, and re-translate the ones that differ.

**Verification.** Unit test: `_clean_response` on a code sample containing `# comment`,
`__init__`, `*args, **kwargs`, and an indented first line returns it unchanged.

---

## Bug 4 — Context is taken from the wrong end of the previous segment

**Severity:** medium (affects every translation call that has a previous segment)

Added 2026-11-19 after the plan review; verified in the code.

**Symptom.** The "previous text" given to the model for continuity is the *beginning* of the
previous segment, which for a 300–500 word segment is the part furthest from the text being
translated.

**Cause.** `_build_prompt()` uses `context_before[:200]` (`src/core/translator.py:242`).
The callers also take neighbours by list position (`src/web/tasks.py:499-502`,
`translate.py:153-154` and `207-208`, `src/web/blueprints/review.py:117-118`), so context can
come from a different chapter or from a code segment.

**Fix.** Use the last 200 characters of the previous segment (`[-200:]`), keep the first 200 of
the next one, take neighbours only from the same chapter, and skip code segments.

**Verification.** Unit test on `_build_prompt` with a previous segment longer than 200
characters: the prompt contains its tail and not its head.

---

## Bug 5 — Source comments are translated as visible text

**Severity:** low (rare), but it puts text into the book that the publisher hid

Added 2026-11-19 from the migration dry run; verified in the code and in project data.

**Symptom.** An XML comment in a chapter body is stored as plain segment text, sent to the
model as content to translate (the exported files were not checked for it). In project-5, two production notes
(45 and 38 characters, in two front-matter segments) are stored
without their `<!-- -->` markers and both segments are translated.

**Cause.** In BeautifulSoup, `Comment` is a subclass of `NavigableString`.
`Segmenter._extract_blocks()` (`src/core/segmenter.py:85-94`) treats every non-empty
`NavigableString` child as a text block and stores `str(child)`, which for a comment is its
content without the markers. `_is_container()` (`segmenter.py:47-49`) has the same blind spot:
a comment makes a container count as a content element. Whether a given comment is affected
depends on which segmenter version created the project.

**Fix.** Skip `Comment` (and other non-text string types) in both places. The block walker in
the improvement plan ignores comments and processing instructions.

**Existing data.** The migration drops comment text from legacy segments; the two affected
segments need re-translation or a manual edit.

**Verification.** Unit test: a body containing `<!-- note -->` between two paragraphs yields no
segment containing `note`.

---

## Notes added after review

- **Bug 1 verification.** The test fixtures are themselves written with ebooklib, which strips
  chapter heads, so a regression test built on `rich_sample_epub` would compare zero links with
  zero links. The test needs a fixture written as a raw ZIP archive, and must read the output
  members directly from the ZIP rather than through `epub.read_epub`.
- **Bug 1 cause, extended.** `item.get_content()` is also what the extractor reads
  (`src/epub/extractor.py:74`), so segmentation runs on ebooklib's normalized document rather
  than on the bytes in the archive.
- **Bug 3 fix, corrected.** "Do not send comment-free code to the model" must not be implemented
  by copying the source into `translated_text`: the builder would then rewrite the block,
  flattening syntax-highlighting markup in `chinese-only` and printing the code twice in
  bilingual formats. The block needs a flag that makes the exporter leave it untouched.
- **Related limitation.** Web term extraction reads only the first 50 segments, including code
  segments (`src/web/blueprints/glossary.py:113`).
