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
366 passed, 31 warnings in 38.20s
```

Baseline before the phase: 246 passed. First submission: 272 passed. After review round 1: 366.
No test that exists on `<base>` was removed. Two `<base>` tests had their sample strings changed
(an invented title instead of a published one, `test_translator.py`), with the same assertions.
Tests added in this phase and changed in round 1 are listed in section 6.

### 2. New tests fail on the old source (first submission)

This was recorded for the first submission, before review round 1 (section 6). One test named
here was later replaced: `test_code_prefix_like_text_not_removed` is now
`test_code_without_fence_left_alone`.

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

### 3. Check script: translated code segments that may need re-translation

Read-only; it only opens each `project.json`. It is a report, not a pass/fail gate: it exits 0
whenever every project could be read, and its first output line says so. A line counts as a
comment when it starts with `//`, or with `#` followed by whitespace, `!` or the end of the line
(`#define` and `#include` are code). Translated or reviewed code segments with an empty
translation are reported separately. `project-N` is the alphabetical position of the directory
among the directories of `--projects-dir` that contain a `project.json`; all 8 directories do,
so the labels are the same as elsewhere in this log.

```
$ python3 scripts/verify/phase0_code_segments.py --projects-dir <repo>/projects --anonymize
Phase 0 code segment report (candidates for re-translation; reporting only, not a pass/fail check)
project-1: translated code segments 0, differ outside comment lines 0, empty translation 0
project-2: translated code segments 0, differ outside comment lines 0, empty translation 0
project-3: translated code segments 0, differ outside comment lines 0, empty translation 0
project-4: translated code segments 0, differ outside comment lines 0, empty translation 0
project-5: translated code segments 32, differ outside comment lines 19, empty translation 0
project-6: translated code segments 0, differ outside comment lines 0, empty translation 0
project-7: translated code segments 0, differ outside comment lines 0, empty translation 0
project-8: translated code segments 0, differ outside comment lines 0, empty translation 0
TOTAL projects 8  translated code segments 32  differ outside comment lines 19  empty translation 0  unreadable 0
$ echo $?
0
```

Without `--anonymize` the script prints the project names and the IDs of the listed segments;
that output stays on the local machine. With `--anonymize`, error messages contain no paths.
The 19 segments are candidates, not confirmed defects: a translated end-of-line comment also
counts as a difference.

### 4. Existing projects keep `main`'s builder walk

The builder now chooses its walk per chapter from the stored segments (section 6, A1/B1). The
legacy walk (comments count as text) is used only when a stored segment shows the old
segmenter's behaviour: a top-level text node equal to the text of a comment in the chapter
body, or a stored div/section/article that holds a comment and would otherwise be a container.
The committed check compares, for every chapter that has segments, the element list chosen now
with the one `main` uses:

```
$ python3 scripts/verify/phase0_builder_alignment.py --projects-dir <repo>/projects --anonymize
PASS project-1: chapters 22, with comment-dependent walk 0, walk changed vs main 0
PASS project-2: chapters 34, with comment-dependent walk 0, walk changed vs main 0
PASS project-3: chapters 39, with comment-dependent walk 2, walk changed vs main 0
PASS project-4: chapters 1763, with comment-dependent walk 0, walk changed vs main 0
PASS project-5: chapters 35, with comment-dependent walk 2, walk changed vs main 0
PASS project-6: chapters 108, with comment-dependent walk 0, walk changed vs main 0
PASS project-7: chapters 68, with comment-dependent walk 0, walk changed vs main 0
PASS project-8: chapters 87, with comment-dependent walk 0, walk changed vs main 0
TOTAL projects 8  chapters 2156  comment-dependent 4  changed vs main 0  failed projects 0
$ echo $?
0
```

4 chapters (2 in project-3, 2 in project-5) depend on the walk; all keep `main`'s walk. The
script exits 1 if any chapter would change. A project created after Phase 0 whose source has
comments is expected to FAIL this check (the new walk is right for it); it is meant for
projects created before Phase 0. Both scripts are covered by `tests/test_verify_scripts.py`.

### 5. Mutation testing

Tool: mutmut 2.5.1, which installs and runs on Python 3.9 and whose `--use-patch-file` option
restricts mutation to the lines of a diff (mutmut 3 was not evaluated). Development only, in `requirements-dev.txt`; installed in a
throwaway virtualenv outside the repository, not in the project one:

```
$ python3.9 -m venv "$TMPDIR/mutvenv"
$ "$TMPDIR/mutvenv/bin/pip" install -r requirements.txt -r requirements-dev.txt
$ MUTATION_VENV="$TMPDIR/mutvenv" scripts/verify/mutation.sh        # base defaults to main
```

The script mutates only the lines added or changed versus the merge base with `main`, in four
groups of files, each with only its relevant tests (core modules; web call sites; CLI; check
scripts). It prints every surviving, timed-out or suspicious mutant and a total line; exit 0
when none, 1 otherwise, 2 when mutmut fails or generates no mutants. A run takes about 4
minutes. State is in `.mutmut-cache` (git-ignored); `mutmut show <id>` prints a mutant.

Making the tool trustworthy took three fixes, each in the script: mutmut 2 counts only exit
code 1 as "killed" (a mutant that breaks test collection was reported as survived), so the
runner maps every failure to 1; bytecode is not written during a run (on macOS the cache lives
under `sys.pycache_prefix`, and a mutant and the restored file can share size and mtime
second); and pytest runs under a watchdog thread, because on a timeout mutmut kills only the
wrapper and an infinite-loop mutant then hung the run.

Results (456 mutants on the changed lines):

| | killed | survived | timeout | suspicious |
|---|---|---|---|---|
| before (tests of commit `f349272`, before the tests added for mutants; current script) | 329 | 123 | 4 | 0 |
| after | 438 | 14 | 4 | 0 |

```
$ MUTATION_VENV=... scripts/verify/mutation.sh
...
TOTAL mutants 456  killed 438  survived 14  timeout 4  suspicious 0  untested 0
```

The 4 timeouts are infinite loops in `_split_text` (`term_extractor.py` lines 55, 58, 61:
`len(text) + start`, `cut < 0`, `start = cut`, `start -= cut`); the tests do not finish, so
they are detected, not survivors. Every survivor, with the reason it is equivalent or cannot
be reached:

| id | location | mutation | why it is not killed |
|---|---|---|---|
| 23 | `translator.py:134` | `next(..., -1)` → `-2` | the result is only tested with `idx < 0` |
| 56 | `translator.py:313` | default `source=''` → `'XXXX'` | the default is only tested for containing a code fence |
| 105 | `glossary_manager.py:25` | `_VOWELS` gets `XX` around it | only a lower-cased single character is looked up; it is never `X` |
| 108 | `glossary_manager.py:26` | same for `_NO_DOUBLING` | same |
| 164 | `glossary_manager.py:61` | `@lru_cache` removed | a cache: same results, only slower |
| 167 | `glossary_manager.py:81` | `(?!)` → `XX(?!)XX` | both never match (empty term) |
| 271 | `phase0_code_segments.py:84` | default text `''` → `'XXXX'` | differs only for a code segment with no `text` key whose translation is literally `XXXX` |
| 337 | `phase0_builder_alignment.py:39` | `sys.path.insert(0, …)` → `insert(1, …)` | `sys.path[0]` is the script directory, which has no `src` package |
| 347 | `phase0_builder_alignment.py:57` | default `epub_path` `''` → `'XXXX'` | both are missing files and give the same FAIL line |
| 355 | `phase0_builder_alignment.py:69` | default type `'text'` → `'XXtextXX'` | the type is only compared with `code` |
| 357 | `phase0_builder_alignment.py:70` | default html `''` → `'XXXX'` | matters only if a comment's text is exactly `XXXX` |
| 371, 372 | `phase0_builder_alignment.py:84-85` | skip of a missing or non-document spine item | unreachable with ebooklib: every spine item it reads (the nav too) is a document present in the manifest; reaching it needs a hand-made OPF |
| 379 | `phase0_builder_alignment.py:89` | skip of a chapter without `<body>` → `break` | unreachable: ebooklib can neither write nor render a document without `<body>` |

### 6. Review round 1

Two reviews (A: 8 findings, B: 9 findings) were read against the code; each finding was
reproduced with a test before it was fixed. "Fails before" means the new test fails on the
commit before the fix.

| Finding | Verdict | Evidence | Fix |
|---|---|---|---|
| A1 builder: equal block counts pick the wrong walk (`<div><!-- note --><h2 id>…`) | confirmed | `test_builder.py::TestWalkSelection::test_equal_count_nested_comment_keeps_heading` (3 formats) failed before | builder now chooses the walk from stored segment content |
| A2 extractor turns a body-level comment into text before the segmenter sees it | confirmed | `test_extractor.py::TestCommentsThroughPipeline` failed before | comments keep their markers in the extractor |
| A3 a single segment longer than 5000 characters is truncated in term extraction | confirmed | `test_glossary.py::…::test_oversized_segment_split_without_loss`, web test with the term past character 5000, both failed before | lossless chunking |
| A4 existing-project export check not committed | confirmed | — | `scripts/verify/phase0_builder_alignment.py` |
| A5 call sites (web worker, CLI) not covered | confirmed | putting `main`'s `tasks.py` and `translate.py` back keeps the old suite green; the new call-site tests then fail (4 of 4) | `test_web.py::TestWorkerContext`, `test_cli.py::TestCLIContext` |
| A6 check script hides translated code segments with an empty translation | confirmed | `test_verify_scripts.py::TestCheckProject::test_classification` | reported as `empty translation` |
| A7 `--anonymize` leaks paths in error output | confirmed (missing directory printed verbatim; a listing error was uncaught) | `…::test_missing_directory_path_not_printed_when_anonymized` | path replaced by `<projects-dir>`, listing errors caught |
| A8 real names and published titles in prompt examples and a test | confirmed (pre-existing) | repo-wide search | invented names and titles |
| B1 builder walk chosen by counts (scenarios A and B) | confirmed | `TestWalkSelection::test_scenario_a_new_project` (3 formats), `test_scenario_b_wrapper_div_with_comment[chinese-only]` failed before | as A1 |
| B2 code mode keeps a commentary line and fences | confirmed | `test_translator.py::TestCodeResponseCleaning::test_code_preface_and_fence_removed` (3 cases) failed before (with the others added for this finding, 9 failures; some only because the `source` argument did not exist); the test that locked the old behaviour was replaced | source-aware unwrapping |
| B3 inflected forms no longer match | confirmed | `test_glossary.py::…::test_find_terms_inflected_forms` failed before | decision 5 suffixes |
| B4 = A4 | confirmed | — | as A4 |
| B5 code neighbour gives no context instead of the next text segment | confirmed | `test_translator.py::TestContext::test_code_neighbours_stepped_over_to_text` failed before | step over code within the chapter |
| B6 oversized segment truncated; no extraction progress | partly: truncation confirmed (as A3); progress reporting confirmed but out of scope | — | truncation fixed; progress under "Not done" |
| B7 check script: `#define` counted as a comment; exit code; labels; names | partly: `#` rule confirmed and fixed; exit code 0 is by design (a report, now stated in its header); label rule documented — all 8 directories contain `project.json`, so labels equal the plan's; names and IDs without `--anonymize` are by design | `test_verify_scripts.py::TestCodeLines::test_hash_directives_are_code` | header, docstring |
| B8 = A5 | confirmed | as A5 | as A5 |
| B9 regex edges | partly: `IT`/`US` and whitespace confirmed and fixed; `.NET` in `ASP.NET` accepted and documented by a test; irregular plurals (indices, analyses) not handled (not in decision 5); cost measured, see below | `test_all_caps_terms_are_case_sensitive`, `test_multi_word_terms_match_any_whitespace` failed before | decision 5 |

Glossary matching cost (decision 5): 500 terms (a fifth all-caps, a seventh multi-word) on a
3000-character text take 14 ms per segment after a one-time 57 ms compile, against a
translation call of seconds; patterns stay one per term.

Check that the round-1 tests fail on the pre-review source (tests from the branch; `src/`,
`translate.py` and `scripts/` from `9693ae2`, the last commit before round 1):

```
$ git checkout 9693ae2 -- src translate.py scripts
$ python3 -m pytest tests/ -q
68 failed, 298 passed, 31 warnings in 40.05s
$ git checkout HEAD -- src translate.py scripts
```

All 68 failures are tests added or changed in round 1. The passing 298 are the earlier tests
plus round-1 tests that guard behaviour which was already right. Failures per area: builder
walk selection 8 and legacy signatures 11, CLI context 2, extractor pipeline 1, glossary
matching 8, term extraction 4, code cleanup 13, context 1, check scripts 18, web extraction 1,
web worker context 2.

### Not done

- Progress reporting for web term extraction (B6, decision 7): the status endpoint still only
  reports "running" while the batches run one after another.
- Irregular plurals (indices, analyses) in glossary matching (B9; not in decision 5).

### Not verified

- No call to the real `claude` CLI was made. Whether the model's replies for code segments are
  now stored unchanged in practice, and whether the changed context improves translations, was
  not measured.
- The 19 candidate code segments were not inspected one by one and were not re-translated.
- Web term extraction over a whole book was not run. It makes one CLI call per 5000 characters
  of text, one after another, so its duration on a full book is unknown.
- Export of a newly created project whose source contains comments was tested on synthetic
  chapters only (all three formats), not on a real book. The builder's legacy rule was checked
  on the 8 real projects (section 4) but its two signatures were derived from reading the old
  pipeline, and a legacy chapter whose comment text was sentence-split or merged into adjacent
  bare text by the old extractor would not be recognised (none exists in the 8 projects).
- The two existing segments that contain comment text (Bug 5, "Existing data") are unchanged;
  this phase does not migrate stored data.
- Glossary inflection rules are heuristics: they also accept some non-words (`renderring`,
  `debuging`) and miss others (`y → ied`, as in `queried`).
