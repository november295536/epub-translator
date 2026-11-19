# Acceptance log

One entry per phase of `plan/02-improvement-plan.md`: the commands that were run, their output
(trimmed), the date, and what was not verified. Absolute paths are written as `<repo>`; the real
projects are labelled `project-1` … `project-8` (alphabetical order of directory name).

---

## Phase 0 — Standalone fixes

Date: 2026-11-19 (first submission and review rounds 1 to 3). Branch:
`phase-0/standalone-fixes`. No new runtime dependency. `<base>` below is the commit the branch
starts from.

Interpreters. The project virtualenv holds two Pythons: `venv/bin/python3` (and `python`) is
Python 3.9.6 with `venv/lib/python3.9`, and is what `python3 -m pytest` runs; `venv/bin/pip`,
`venv/bin/pytest` and `venv/bin/mutmut` are scripts for Python 3.12.7 with
`venv/lib/python3.12`. `pip install -r requirements-dev.txt` therefore installs mutmut for
3.12.7, and mutation testing (section 5) runs the tests on 3.12.7. The test suite passes on
both.

### 1. Test suite

```
$ cd <repo> && source venv/bin/activate && python3 -m pytest tests/ -q
436 passed, 33 warnings in 43.16s
```

Baseline before the phase: 246 passed. First submission: 272. After review round 1: 366. After
review round 2: 419. After review round 3: 436. No test that exists on `<base>` was removed. Changed `<base>` tests: two
had their sample strings changed (an invented title instead of a published one,
`test_translator.py`), with the same assertions; `test_glossary.py::test_find_terms_in_text`
now asserts the exact list found, and `test_cli.py::test_init_creates_project_json` also
asserts `segmenter_version` 2. Tests added or changed in the review rounds are listed in
sections 6 and 7.

### 2. New tests fail on the old source (first submission)

This was recorded for the first submission, before review round 1 (section 6). Test names are
those of that submission. Later replacements: `test_code_prefix_like_text_not_removed` is now
`test_code_without_fence_left_alone`; in round 2 the glossary tests became
`test_term_must_start_at_a_word_boundary`, `test_any_continuation_matches` and
`test_terms_with_symbols`, and `TestCommentAlignment` became `TestVersion2Export` /
`TestVersion1ExportMatchesMain` (section 7).

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
(`#define` and `#include` are code). A comment line's wording is ignored but its leading
whitespace is compared (round 2). Translated or reviewed code segments with an empty
translation are reported separately; a code segment without `text` makes the project
unreadable, also when its translation is empty (round 3). `project-N` is the alphabetical position of the directory among the directories of
`--projects-dir` that contain a `project.json`; all 8 directories do, so the labels are the
same as elsewhere in this log.

```
$ python3 scripts/verify/phase0_code_segments.py --projects-dir <repo>/projects --anonymize
Phase 0 code segment report (candidates for re-translation; reporting only, not a pass/fail check)
project-1: translated code segments 0, differ outside comment lines 0, empty translation 0
project-2: translated code segments 0, differ outside comment lines 0, empty translation 0
project-3: translated code segments 0, differ outside comment lines 0, empty translation 0
project-4: translated code segments 0, differ outside comment lines 0, empty translation 0
project-5: translated code segments 32, differ outside comment lines 20, empty translation 0
project-6: translated code segments 0, differ outside comment lines 0, empty translation 0
project-7: translated code segments 0, differ outside comment lines 0, empty translation 0
project-8: translated code segments 0, differ outside comment lines 0, empty translation 0
TOTAL projects 8  translated code segments 32  differ outside comment lines 20  empty translation 0  unreadable 0
$ echo $?
0
```

Without `--anonymize` the script prints the project names and the IDs of the listed segments;
that output stays on the local machine. With `--anonymize`, error messages contain no paths.
The 20 segments (19 before round 2; one more now that comment indentation counts) are
candidates, not confirmed defects: a translated end-of-line comment also counts as a
difference.

### 4. Existing projects resolve to the legacy builder walk

Since round 2 the walk is chosen by a project-level marker, not by the content of the stored
segments. `ProjectManager.create` (used by both CLI `init` and the web project creation) writes
`segmenter_version: 2`; a `project.json` without the field loads as version 1 and is not
rewritten by loading. The builder uses its legacy walk (comments counted, the same code as
`main`'s walk) for version 1, and also when no project data is loaded; it uses the
comment-skipping walk for version 2. The check loads each project through `EPUBBuilder`
(read-only) and compares, for every chapter that has segments, the element list the builder
chooses with the element list of the branch's legacy walk. It therefore shows that each
project resolves to the legacy walk; it does not run `main`'s code. That the legacy walk
gives `main`'s output rests on the golden tests `test_builder.py::TestVersion1ExportMatchesMain`
(outputs recorded from `main`'s builder). Output wording changed in round 3 ("walk changed vs
main" → "not on legacy walk"); the numbers are the same as in round 2:

```
$ python3 scripts/verify/phase0_builder_alignment.py --projects-dir <repo>/projects --anonymize
PASS project-1: segmenter_version 1, chapters 22, with comment-dependent walk 0, not on legacy walk 0
PASS project-2: segmenter_version 1, chapters 34, with comment-dependent walk 0, not on legacy walk 0
PASS project-3: segmenter_version 1, chapters 39, with comment-dependent walk 2, not on legacy walk 0
PASS project-4: segmenter_version 1, chapters 1763, with comment-dependent walk 0, not on legacy walk 0
PASS project-5: segmenter_version 1, chapters 35, with comment-dependent walk 2, not on legacy walk 0
PASS project-6: segmenter_version 1, chapters 108, with comment-dependent walk 0, not on legacy walk 0
PASS project-7: segmenter_version 1, chapters 68, with comment-dependent walk 0, not on legacy walk 0
PASS project-8: segmenter_version 1, chapters 87, with comment-dependent walk 0, not on legacy walk 0
TOTAL projects 8  chapters 2156  comment-dependent 4  not on legacy walk 0  failed projects 0
$ echo $?
0
```

The `project.json` modification times of the 8 projects were the same before and after the
run. The check still fails when an existing project's walk would differ from `main`: with the
builder's rule temporarily changed to use the new walk for version 1, the same command printed
(round-2 wording) `FAIL project-3 … walk changed vs main 2`, `FAIL project-5 … walk changed vs
main 2` and exited 1 (the change was reverted). A project created after Phase 0 (version 2) whose source has
comments is expected to FAIL this check, since the new walk is right for it. Both scripts are
covered by `tests/test_verify_scripts.py`.

### 5. Mutation testing

Tool: mutmut 2.5.1 (its `--use-patch-file` option restricts mutation to the lines of a diff;
mutmut 3 was not evaluated). Development only, in `requirements-dev.txt`, installed in the
active environment:

```
$ source venv/bin/activate
$ pip install -r requirements-dev.txt
$ scripts/verify/mutation.sh            # base defaults to main
```

`MUTATION_VENV=<virtualenv>` optionally takes mutmut from another virtualenv. The tests run on
the interpreter mutmut is installed in, read from the `#!` line of its script (here Python
3.12.7, see the header).

The script mutates only the lines added or changed versus the merge base with `main`, in four
groups of files, each with only its relevant tests (core modules; web call sites; CLI; check
scripts). Each group starts from an empty mutmut cache, so its clean baseline is run and timed
with that group's own tests (round 1 shared one cache, so later groups reused the first
group's baseline). It prints every surviving, timed-out or suspicious mutant with its diff and
1-based line number, then a total line; exit 0 when there are none, 1 otherwise, 2 when mutmut
fails or generates no mutants. A run takes about 6 minutes.

What the script writes: mutmut edits the mutated source files in place (with `<file>.bak`
backups) and keeps `.mutmut-cache` in the repository root while a group runs (git-ignored,
deleted after each group). Everything else (the patch, the runner, the per-group listing,
`TMPDIR`, pytest's `--basetemp`, the bytecode prefix) is in a private directory created with
`mktemp -d` under the system temporary directory and removed on exit. On every exit (normal,
Ctrl-C, SIGTERM, or mutmut failing) the cleanup stops mutmut's process group if mutmut was
started, puts back any `<file>.bak`, warns if a mutated file is still modified, and deletes
`.mutmut-cache` and the private directory; it ignores INT and TERM while it runs (round 3). Bytecode is neither
written (`PYTHONDONTWRITEBYTECODE`) nor read from an existing cache (private
`PYTHONPYCACHEPREFIX`). The script refuses to start if a mutated file has uncommitted changes
or if any `<file>.bak` exists (each checked on its own).

Earlier fixes, still in place: mutmut 2 counts only exit code 1 as "killed", so the runner maps
every failing exit to 1; pytest runs under a watchdog that exits when its parent is gone, since
on a timeout mutmut kills only the wrapper.

Cancellation was tested by interrupting real runs during the core group, while
`src/core/translator.py` was mutated (`git status` showed it modified and
`translator.py.bak` present):

- SIGINT sent to the script (started with SIGINT at its default disposition, as from a
  terminal): exit 130, `restored src/core/translator.py` printed; afterwards `git status`
  showed no change to any source file, no `.bak`, no `.mutmut-cache`, and no mutmut, runner
  or pytest process from the run was left.
- SIGTERM: same result, and the private temporary directory no longer existed.

(SIGINT sent with `kill` to a script started in the background with `&` from a
non-interactive shell is ignored by bash, as for any such background job; that is not a
path a user takes from a terminal.)

Round 3, with a helper script that starts `scripts/verify/mutation.sh` in the background, waits
until a `<file>.bak` exists (both times `src/core/project.py.bak`, with `git status` showing
`src/core/project.py` modified), acts, waits for the script, then checks:

- mutmut itself killed (`kill -KILL` on the `mutmut run` process, the leader of its process
  group): exit 2, `ERROR mutmut failed for group core`, then `restored src/core/project.py`;
  afterwards `git status --porcelain` empty, no `.bak`, no `.mutmut-cache`, no process left
  in mutmut's process group, no mutmut or pytest process from the run. Before round 3 the
  failure path cleared the PID and the cleanup skipped the restore.
- second signal during cleanup: SIGTERM to the script, then 0.3 s later SIGTERM and SIGINT
  again (the cleanup waits for mutmut and then sleeps 1 s, so the second signals arrive
  while it runs; that they landed inside the cleanup is inferred from this timing, not
  observed directly): exit 130, `restored src/core/project.py`, and the same clean state, and
  the run's private `mktemp -d` directory was gone. The same procedure with the round-2
  script (`f47a5ad`) left `src/core/project.py` mutated, `project.py.bak`, `.mutmut-cache`
  and the private directory behind (restored by hand afterwards), so the procedure does
  exercise the second-signal path.

Results:

| | mutants | killed | survived | timeout | suspicious |
|---|---|---|---|---|---|
| round 1, final (old script, round-1 code) | 456 | 438 | 14 | 4 | 0 |
| round 2, after the code changes, before the survivor work (commit `a0a4713`) | 356 | 345 | 7 | 4 | 0 |
| round 2, final (commit `2ce86b2`) | 349 | 342 | 3 | 4 | 0 |
| round 3, after the code changes (commit `bf0b008`) | 361 | 352 | 5 | 4 | 0 |
| round 3, final (commit `db3c816`) | 361 | 354 | 3 | 4 | 0 |

```
$ scripts/verify/mutation.sh
...
TOTAL mutants 361  killed 354  survived 3  timeout 4  suspicious 0  untested 0
$ echo $?
1
```

The mutant count fell because the glossary inflection rules and the builder's content
signatures were removed. The 4 timeouts are infinite loops in `_split_text`
(`term_extractor.py` lines 56, 59 and 62 twice: `len(text) + start`, `cut < 0`, `start = cut`,
`start -= cut`); the tests do not finish, so they are detected, not survivors.

Survivors of the intermediate run and what was done:

| mutant (line, 1-based) | disposition |
|---|---|
| `translator.py:134` `next(..., -1)` → `-2` | code changed: `get_context` checks membership instead of using a sentinel |
| `translator.py:336` `partition('\n')` → `partition('XX\nXX')` on the source's first line | code changed: the commentary check takes the whole text (its patterns stop at the first newline) |
| `phase0_code_segments.py:87` `seg.get('text', '')` → default `'XXXX'` | code changed: `seg['text']`; a code segment without text is now reported as unreadable (`test_code_segment_without_text_is_unreadable`) |
| `phase0_builder_alignment.py:49` `SOURCE_FILE` → `'XXsource.epubXX'` | killed: `test_local_source_copy_preferred_over_epub_path` |
| the 3 below | equivalent |

Round 3: the two new survivors were in `_is_empty_reply` (`translator.py:123` ``strip('`')`` →
``strip('XX`XX')``; `translator.py:125` `'\n' not in t` → `'XX\nXX' not in t`); killed by
`test_short_text_reply_accepted[X-X\n]` and `test_multi_line_reply_ending_in_colon_accepted`.
The other three are the round-2 survivors at new line numbers and ids.

The 3 remaining survivors (ids and lines of the round-3 run), equivalent under the stated
condition:

| id | location | mutation | why no input distinguishes it |
|---|---|---|---|
| 15 | `translator.py:114` | `re.match(p, text + '\n')` → `text + 'XX\nXX'` | the two patterns are `^(?:prefix words)[^\n]*\n+` with `re.match`: what follows `text` matters only through its first newline, which `[^\n]*` reaches in both versions; `XX` cannot complete a prefix word (none contains `X`). Also holds for the new caller `_is_empty_reply` |
| 63 | `translator.py:327` | default `source=''` → `'XXXX'` | the default is used only when no source is passed (every production call passes it); both `''` and `'XXXX'` contain no code fence and do not start with a commentary word, the only two ways `source` is used |
| 112 | `phase0_builder_alignment.py:44` | `sys.path.insert(0, root)` → `insert(1, root)` | equivalent with default interpreter path settings: then `sys.path[0]` is the script's own directory, which has no `src` package, and at index 0 or 1 the repository root precedes `PYTHONPATH` entries and site-packages, so the same `src` is imported. Not equivalent when `sys.path[0]` is not the script's directory (`-P` or `PYTHONSAFEPATH`, Python 3.11+) and that first entry, e.g. the first `PYTHONPATH` entry, holds another `src` package (review round 3, A3); not tested |

Round-1 survivors that no longer exist: the glossary ones (`_VOWELS`, `_NO_DOUBLING`,
`lru_cache`, `(?!)`) went with the code; 371/372/379 in the alignment check (skip of a missing,
non-document or body-less spine item) were reachable after all and are killed by
`test_missing_spine_item_is_skipped_not_the_rest`, `test_non_document_spine_item_is_skipped`
and `test_document_without_body_is_skipped_not_the_rest` (ebooklib renders an empty document
without `<body>`); 271 and 357 (`.get` defaults) went with the code; 347 and 355 were on lines
removed in round 2; 337 (`sys.path` index) is 112 above (111 in round 2).

### 6. Review round 1

Two reviews (A: 8 findings, B: 9 findings) were read against the code; each finding was
reproduced with a test before it was fixed. "Fails before" means the new test fails on the
commit before the fix.

| Finding | Verdict | Evidence | Fix |
|---|---|---|---|
| A1 builder: equal block counts pick the wrong walk (`<div><!-- note --><h2 id>…`) | confirmed | `test_builder.py::TestWalkSelection::test_equal_count_nested_comment_keeps_heading` (3 formats) failed before | builder chose the walk from stored segment content (replaced in round 2 by the `segmenter_version` marker) |
| A2 extractor turns a body-level comment into text before the segmenter sees it | confirmed | `test_extractor.py::TestCommentsThroughPipeline` failed before | comments keep their markers in the extractor |
| A3 a single segment longer than 5000 characters is truncated in term extraction | confirmed | `test_glossary.py::…::test_oversized_segment_split_without_loss`, web test with the term past character 5000, both failed before | lossless chunking |
| A4 existing-project export check not committed | confirmed | — | `scripts/verify/phase0_builder_alignment.py` |
| A5 call sites (web worker, CLI) not covered | confirmed | putting `main`'s `tasks.py` and `translate.py` back keeps the old suite green; the new call-site tests then fail (4 of 4) | `test_web.py::TestWorkerContext`, `test_cli.py::TestCLIContext` |
| A6 check script hides translated code segments with an empty translation | confirmed | `test_verify_scripts.py::TestCheckProject::test_classification` | reported as `empty translation` |
| A7 `--anonymize` leaks paths in error output | confirmed (missing directory printed verbatim; a listing error was uncaught) | `…::test_missing_directory_path_not_printed_when_anonymized` | path replaced by `<projects-dir>`, listing errors caught |
| A8 real names and published titles in prompt examples and a test | confirmed (pre-existing) | repo-wide search | invented names and titles |
| B1 builder walk chosen by counts (scenarios A and B) | confirmed | `TestWalkSelection::test_scenario_a_new_project` (3 formats), `test_scenario_b_wrapper_div_with_comment[chinese-only]` failed before | as A1 |
| B2 code mode keeps a commentary line and fences | confirmed | `test_translator.py::TestCodeResponseCleaning::test_code_preface_and_fence_removed` (3 cases) failed before (with the others added for this finding, 9 failures; some only because the `source` argument did not exist); the test that locked the old behaviour was replaced | source-aware unwrapping |
| B3 inflected forms no longer match | confirmed | `test_glossary.py::…::test_find_terms_inflected_forms` failed before | decision 5 suffixes (replaced in round 2 by the start-boundary rule) |
| B4 = A4 | confirmed | — | as A4 |
| B5 code neighbour gives no context instead of the next text segment | confirmed | `test_translator.py::TestContext::test_code_neighbours_stepped_over_to_text` failed before | step over code within the chapter |
| B6 oversized segment truncated; no extraction progress | partly: truncation confirmed (as A3); progress reporting confirmed but out of scope | — | truncation fixed; progress under "Not done" |
| B7 check script: `#define` counted as a comment; exit code; labels; names | partly: `#` rule confirmed and fixed; exit code 0 is by design (a report, now stated in its header); label rule documented — all 8 directories contain `project.json`, so labels equal the plan's; names and IDs without `--anonymize` are by design | `test_verify_scripts.py::TestCodeLines::test_hash_directives_are_code` | header, docstring |
| B8 = A5 | confirmed | as A5 | as A5 |
| B9 regex edges | partly: `IT`/`US` and whitespace confirmed and fixed; `.NET` in `ASP.NET` accepted and documented by a test; irregular plurals (indices, analyses) not handled (not in decision 5); matching cost measured in round 1 (the rules were removed in round 2) | `test_all_caps_terms_are_case_sensitive`, `test_multi_word_terms_match_any_whitespace` failed before | decision 5 (replaced in round 2; whitespace rule kept) |

Check that the round-1 tests fail on the pre-review source (tests from the branch; `src/`,
`translate.py` and `scripts/` from `9693ae2`, the last commit before round 1):

```
$ git checkout 9693ae2 -- src translate.py scripts
$ python3 -m pytest tests/ -q
68 failed, 298 passed, 31 warnings in 40.05s
$ git checkout HEAD -- src translate.py scripts
```

All 68 failures are tests added or changed in round 1 (the legacy-signature and glossary
inflection tests among them were removed in round 2). The passing 298 are the earlier tests
plus round-1 tests that guard behaviour which was already right. Failures per area: builder
walk selection 8 and legacy signatures 11, CLI context 2, extractor pipeline 1, glossary
matching 8, term extraction 4, code cleanup 13, context 1, check scripts 18, web extraction 1,
web worker context 2.

### 7. Review round 2

Three reviews (A: 12 items, B: 8, C: 5). The reviewers could not run Python; each confirmed
finding was reproduced with a test before it was fixed. The orchestrator's decisions D1 and
D2 replaced the content signatures and the glossary inflection rules by simpler designs, so
the items about them are "superseded" rather than patched.

Check that the round-2 tests fail on the pre-round source (tests from the branch; `src/`,
`translate.py` and the two check scripts from `093dcd4`, the last commit before round 2):

```
$ git checkout 093dcd4 -- src translate.py scripts/verify/phase0_code_segments.py scripts/verify/phase0_builder_alignment.py
$ python3 -m pytest tests/ -q
90 failed, 329 passed in 43.21s
$ git checkout HEAD -- src translate.py scripts/verify
```

All 90 failures are round-2 tests. Many builder, project and check-script tests fail on the old
source only because the `segmenter_version` field does not exist there; the substantive ones
are listed per item below.

| Item | Verdict | Evidence | Fix |
|---|---|---|---|
| A1 builder signatures misclassify both generations | confirmed | pre-round builder: a new project's `<!--note-->note<h1>Title</h1>` chapter selected the legacy walk; `TestVersion1ExportMatchesMain[comment_holds_markup-*-None]` (old extractor stored `<!--<p>Draft</p>-->` as a paragraph) and `[comment_equals_text-*-None]` differ from `main`'s output on the pre-round builder | `3cc2987`: `segmenter_version` marker (D1) |
| A2 empty cleaned reply accepted | confirmed | `test_code_reply_empty_after_cleanup_is_retried` (4 shapes), `test_text_reply_empty_after_cleanup_raises` failed before | `57880dd` (D3) |
| A3 groups share one mutmut baseline | confirmed by reading mutmut 2.5.1 (`time_test_suite` reuses the cached time when the tests-directory hash matches; the cache file is `.mutmut-cache` in the working directory) | — | `a0a4713`: empty cache per group |
| A4 cancellation leaves the run alive | confirmed by reading (mutmut and pytest were not in a group the script controlled) | interrupt tests in section 5 | `a0a4713`: own process group, kill and restore on INT/TERM |
| A5 writes outside the worktree | partly: patch, runner and pytest temp files were in the system temp tree; per the decision a private `mktemp -d` directory is enough | — | `a0a4713`: one private directory for patch, runner, `TMPDIR`, `--basetemp`; section 5 says what is written where |
| A6 suffixes match unrelated words (`Go`→goes …) | superseded by simplification | — | `c9288ab` (D2); now accepted and documented: other words starting with the term match |
| A7 survivors called equivalent although inputs distinguish them | confirmed for 271, 357, 371, 372 (and 379); 347 and 164 no longer exist | section 5 | survivors removed from code or killed by tests; remaining 3 justified |
| A8 `C` matches inside `C++`/`C#` | superseded by simplification | — | accepted (documented in `_term_pattern`) |
| A9 plural entries do not match singular | superseded by simplification | — | accepted (documented) |
| A10 lost comment-line indentation not reported | confirmed | `TestCodeLines::test_lost_comment_indentation_differs` (2 cases), `test_comment_wording_ignored` failed before | `ff999f9` (D5) |
| A11 backup guard misses a single `.bak` | confirmed by reading (`ls` of several paths fails when any is missing) | — | `a0a4713`: each path checked |
| A12 stale bytecode can still be read | confirmed by reading; the same effect was hit by hand during this round (two same-size edits within one second, the second run used the first's bytecode from the macOS `pycache_prefix`) | — | `a0a4713`: private `PYTHONPYCACHEPREFIX` |
| B1 legacy chapter with a comment holding markup or an entity uses the new walk | confirmed | `TestVersion1ExportMatchesMain[comment_holds_markup-*-None]`, `[comment_with_entity-*-None]` failed before | `3cc2987` (D1) |
| B2 commentary line before unfenced code kept | confirmed | `test_code_preface_before_unfenced_code_removed` (3 cases) failed before; `test_code_preface_without_fence_not_removed`, which locked the old behaviour, replaced | `57880dd` (D4) |
| B3 emptiness check before cleanup; single-newline empty fence not recognised | confirmed | as A2, plus `test_code_empty_fence_cleans_to_empty['```python\n```']` failed before | `57880dd` (D3) |
| B4 suffix false matches; `C` in `C++`/`CS` | superseded by simplification | — | `c9288ab` (D2), accepted |
| B5 plural entries; `C++11`, `Python3` no longer matched | superseded by simplification; `C++11`/`Python3` match again (no trailing boundary) | — | `c9288ab` (D2) |
| B6 0-based line numbers | confirmed | — | `a0a4713`: 1-based; log locations corrected |
| B7 survivors 371/372 reachable; 271 reason inexact | confirmed | spine tests (section 5) | `2ce86b2`, `3cc2987` |
| B8 = A10 | confirmed | as A10 | `ff999f9` |
| C1 `es`, doubling and `y` rules wrong | superseded by simplification | — | `c9288ab` (D2) |
| C2 plural entries | superseded by simplification | — | accepted (documented) |
| C3 empty fenced / prefaced replies saved without retry | confirmed | as A2/B3; for code segments a commentary-only reply (`以下是翻譯結果：\n`) also cleans to empty and is retried (text and HTML segments: round 3, A2) | `57880dd` (D3) |
| C4 `C` matches inside `C++`/`C#` | superseded by simplification | — | accepted (documented) |
| C5 = A10 | confirmed | as A10 | `ff999f9` |

Accepted glossary limitations (decision D2): for a glossary of more than 150 entries, a term
also matches other words that start with it (`Go` → "going", `C` in "C++"), `.NET` does not
match inside "ASP.NET", and a term stored in plural form does not match its singular. For
prompt selection a false positive only adds a few tokens. With 150 entries or fewer (all 8
real glossaries hold 0 to 6) every entry is sent and no matching is done. Every caller of
`find_terms_in_text` (web worker `tasks.py`, re-translate `review.py`, both CLI modes in
`translate.py`) builds a translation prompt; they now call `terms_for_prompt`, which applies
the size rule, and `find_terms_in_text` keeps true matching.

### 8. Review round 3

Three reviews: A (4 items, changes requested), B (6 items, approved), C (no findings,
approved). No blockers. The reviewers could not run Python; code findings were reproduced
with a failing test (or, for the shell script, a real run) before the fix.

| Item | Verdict | Evidence | Fix |
|---|---|---|---|
| A1 `mutation.sh`: mutmut failing clears the PID, cleanup skips group kill and restore | confirmed | section 5, round 3: killing mutmut itself now restores the file; the failure path previously set `MUTPID=""` before `exit 2` | `82d3ba9`: restore and warning run on every exit; the PID is kept on failure so the group is stopped |
| A2 text/HTML reply of only fences (`` ```html\n``` `` → `` ``` ``) or only a commentary line is saved | confirmed | `test_text_reply_only_fence_or_commentary_is_retried` (8 cases) and `…_exhausts` (2) failed before; `test_short_text_reply_accepted` (`好的`, `當然`, `以下是步驟：` for a source ending in a colon, `X`) passes before and after | `2e5b536`: `_is_empty_reply` — empty when only backticks remain, or a single commentary line (existing start patterns) ending in a colon when the source does not end in one. A commentary line without a colon (`Here is the translation`) is still accepted, since `好的`/`當然` alone are legitimate translations |
| A3 survivor 111 not unconditionally equivalent | confirmed (by reading; not run) | `sys.path[0]` is not the script's directory under `-P`/`PYTHONSAFEPATH` (3.11+) | disposition (section 5) and the script's comment state the condition; no subprocess test, per the round-3 decision |
| A4 code segment without `text` but with an empty translation counted as empty, not unreadable | confirmed | `test_code_segment_without_text_is_unreadable['']` failed before | `a1f7413`: source read before classifying |
| B1 = A1 | confirmed | as A1 | `82d3ba9` |
| B2 cleanup not protected from a second INT/TERM | confirmed by a real run of the round-2 script (section 5) | file left mutated, `.bak`, cache and private directory left | `82d3ba9`: `trap '' INT TERM` at the start of `cleanup` |
| B3 alignment check proves only that the legacy walk is chosen, not equality with `main` | confirmed | the check compares the chosen walk with `legacy=True` of the branch's builder | `bf0b008`: output "not on legacy walk", docstring and section 4 say what it shows; equality with `main` rests on `TestVersion1ExportMatchesMain` |
| B4 C3 row: commentary-only reply retried only for code | confirmed | — | row reworded; text/HTML now also retried (A2) |
| B5 header says the mutmut cache goes to the private directory | confirmed | `.mutmut-cache` is in the repository root | `82d3ba9`: header corrected |
| B6 `main`/`v1.0.0` cannot load a project created on this branch | confirmed | `v1.0.0`'s `src/` on a project created by this branch: `TypeError` | cannot be fixed on the branch; "Known consequences" and the Phase 0 section of `plan/02-improvement-plan.md` |

### Known consequences

- A project created after this phase has `segmenter_version` in its `project.json` and cannot
  be loaded by pre-Phase-0 code (`main` before this branch, tag `v1.0.0`): its
  `ProjectManager.load` does `ProjectData(**raw, …)`, which raises `TypeError: __init__() got
  an unexpected keyword argument 'segmenter_version'` (reproduced in round 3 with `v1.0.0`'s
  `src/` on a project created by this branch, in a temporary directory), and its project
  listing (`/`) calls `load()` without catching that. This matters only for a rollback or
  for a pre-Phase-0 checkout sharing the same `projects/` directory; it cannot be fixed on
  this branch.
- Projects that existed before this phase remain loadable by pre-Phase-0 code; projects
  created after it are not. A project without the field loads as version 1, and saving it
  (translating, reviewing, changing the model) does not add the field: `save()` leaves
  `segmenter_version` out when it is 1 (round 4;
  `test_saving_project_without_segmenter_version_keeps_it_absent`).

### Not done

- Progress reporting for web term extraction (B6 of round 1, decision 7): the status endpoint
  still only reports "running" while the batches run one after another.

### Not verified

- No call to the real `claude` CLI was made. Whether the model's replies for code segments are
  now stored unchanged in practice, and whether the changed context improves translations, was
  not measured.
- The 20 candidate code segments were not inspected one by one and were not re-translated.
- Web term extraction over a whole book was not run. It makes one CLI call per 5000 characters
  of text, one after another, so its duration on a full book is unknown.
- Export of a newly created project (version 2) whose source contains comments was tested on
  synthetic chapters only (all three formats), not on a real book. Version-1 export equals
  `main`'s output on 7 synthetic scenarios (outputs recorded from `main`'s builder in
  `tests/data/legacy_walk_main.json`) and the walk equals `main`'s on every chapter of the 8
  real projects (section 4); a full export of a real project was not compared byte for byte.
- The two existing segments that contain comment text (Bug 5, "Existing data") are unchanged;
  this phase does not migrate stored data.
- Prompt size with a full glossary: sending all entries of a glossary of up to 150 terms to
  every text prompt was not measured against real translations.
- Mutation testing ran on Python 3.12.7 (the interpreter mutmut is installed in), the normal
  test command on 3.9.6; the full suite passes on both (436), but mutants were not run on 3.9.
