# Acceptance log

One entry per phase of `plan/02-improvement-plan.md`: the commands that were run, their output
(trimmed), the date, and what was not verified. Absolute paths are written as `<repo>`; the real
projects are labelled `project-1` … `project-8` (alphabetical order of directory name).

---

## Phase 0 — Standalone fixes

Date: 2026-11-19. Branch: `phase-0/standalone-fixes`. Python 3.9, existing virtualenv, no new
dependency. `<base>` below is the commit the branch starts from.

### 1. Test suite

```
$ cd <repo> && source venv/bin/activate && python3 -m pytest tests/ -q
272 passed, 31 warnings in 34.87s
```

Baseline before the phase: 246 passed. 26 tests were added; no existing test was removed or
changed.

### 2. New tests fail on the old source

The new tests were run against the source tree of `<base>` (tests from the branch, `src/` and
`translate.py` from `<base>`), then the source was restored:

```
$ git checkout <base> -- src translate.py
$ python3 -m pytest tests/ -q
23 failed, 249 passed, 31 warnings in 36.54s
$ git checkout HEAD -- src translate.py
$ python3 -m pytest tests/ -q
272 passed, 31 warnings in 38.95s
```

The 23 failures are exactly new tests (249 = 246 existing + 3 new tests that guard behaviour which
was already correct). Acceptance item → test that fails on `<base>`:

| Acceptance item | Test | Failure on `<base>` |
|---|---|---|
| Code cleanup: sample with `# comment`, `__init__`, `*args, **kwargs`, indented first line unchanged | `test_translator.py::TestCodeResponseCleaning::test_code_translate_keeps_code_and_first_line_indent` (through `translate()` with a mocked subprocess) | assertion: returned code differs from the sample |
| same, on `_clean_response` directly | `…::test_code_sample_unchanged`, `test_code_wrapping_fence_removed`, `test_code_surrounding_blank_lines_trimmed`, `test_code_prefix_like_text_not_removed` | `TypeError`: the `code_mode` argument does not exist |
| Context: tail of the previous segment, not its head | `test_translator.py::TestContext::test_prompt_uses_tail_of_previous_segment` | assertion: the prompt contains the head marker |
| Context: neighbour from another chapter or a code segment is not used | `…::TestContext::test_neighbour_from_other_chapter_not_used`, `test_code_neighbour_not_used`, `test_neighbours_in_same_chapter_used`, `test_first_last_and_unknown_segment` | `ImportError`: `get_context` does not exist |
| same, through the web re-translate endpoint | `test_web.py::TestRetranslateContext::test_previous_chapter_not_used_as_context` | assertion: text of the previous chapter is in the prompt |
| Glossary: `AI` not inside `maintain` | `test_glossary.py::TestGlossaryManager::test_find_terms_whole_word_only` | assertion |
| Glossary: `API` matches `APIs` (also `es`, `y → ies`) | `…::test_find_terms_plural_forms` | assertion |
| Glossary: `C++`, `C#`, `.NET` match | `…::test_find_terms_with_symbols` | assertion (substring matching also matched inside other words) |
| Web extraction: 60 text segments, term only in the last one, is sent | `test_web.py::TestTermExtractionCoverage::test_term_in_last_of_60_text_segments_is_sent` | assertion: the term is in no prompt |
| Extraction batches are not cut off | `test_glossary.py::TestTermExtractor::test_batch_separator_counted_in_batch_size` | assertion |
| Comments: `<!-- note -->` between two paragraphs yields no segment containing `note` | `test_segmenter.py::TestCommentHandling::test_body_comment_between_paragraphs_skipped` (+ 5 more in the class) | assertion |
| Export still lines up when a chapter has a body-level comment | `test_builder.py::TestCommentAlignment::test_segments_without_comment_block` | assertion: translations land on the wrong elements |

### 3. Check script: translated code segments that differ from the source

Read-only; it only opens each `project.json`.

```
$ python3 scripts/verify/phase0_code_segments.py --projects-dir <repo>/projects --anonymize
project-1: translated code segments 0, differ outside comment lines 0
project-2: translated code segments 0, differ outside comment lines 0
project-3: translated code segments 0, differ outside comment lines 0
project-4: translated code segments 0, differ outside comment lines 0
project-5: translated code segments 32, differ outside comment lines 19
project-6: translated code segments 0, differ outside comment lines 0
project-7: translated code segments 0, differ outside comment lines 0
project-8: translated code segments 0, differ outside comment lines 0
TOTAL projects 8  translated code segments 32  differ outside comment lines 19  unreadable 0
$ echo $?
0
```

Without `--anonymize` the script prints the project names and the IDs of the differing segments;
that output stays on the local machine. The 19 segments are candidates for re-translation, not
confirmed defects: a translated end-of-line comment, for example, also counts as a difference.

### 4. Existing exports are not affected by the comment change

The builder shares the segmenter's block walk, so it was checked (one-off local script, not
committed) that for the 8 existing projects the builder still uses the old walk in every chapter
that has segments:

```
TOTAL chapters 2156, chapters where the builder leaves the old walk: 0
```

### Not verified

- No call to the real `claude` CLI was made. Whether the model's replies for code segments are
  now stored unchanged in practice, and whether the changed context improves translations, was
  not measured.
- The 19 candidate code segments were not inspected one by one and were not re-translated.
- Web term extraction over a whole book was not run. It now makes one CLI call per 5000
  characters of text, one after another, so its duration on a full book is unknown.
- The context change at the call sites in `src/web/tasks.py` and `translate.py` is covered only
  by the shared helper's tests and by the existing tests that run through those paths; only the
  re-translate endpoint has a test that checks the prompt.
- Export of a newly created project whose source contains body-level comments was tested on a
  synthetic chapter only, not on a real book.
- The two existing segments that contain comment text (Bug 5, "Existing data") are unchanged;
  this phase does not migrate stored data.
- The check in item 4 cannot be reproduced from the repository, because its script was not
  committed.
