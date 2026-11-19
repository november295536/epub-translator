# Acceptance log

One entry per phase of `plan/02-improvement-plan.md`: the commands that were run, their output
(trimmed), the date, and what was not verified. Absolute paths are written as `<repo>`; the real
projects are labelled `project-1` … `project-8` (alphabetical order of directory name).

---

## Phase 0 — Standalone fixes

Date: 2026-11-19 (first submission and review rounds 1 to 5). Branch:
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
444 passed, 32 warnings in 44.33s
```

Baseline before the phase: 246 passed. First submission: 272. After review round 1: 366. After
review round 2: 419. After review round 3: 436. After review rounds 4 and 5: 444. No test that exists on `<base>` was removed. Changed `<base>` tests: two
had their sample strings changed (an invented title instead of a published one,
`test_translator.py`), with the same assertions; `test_glossary.py::test_find_terms_in_text`
now asserts the exact list found, and `test_cli.py::test_init_creates_project_json` also
asserts `segmenter_version` 2. Tests added or changed in the review rounds are listed in sections 6 to 9.

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
translation are reported separately; a translated or reviewed code segment whose `text` is
missing or not a string makes the project unreadable, whatever its translation (rounds 3
and 4); pending segments are not checked. `project-N` is the alphabetical position of the directory among the directories of
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
  script (`7cbc050`) left `src/core/project.py` mutated, `project.py.bak`, `.mutmut-cache`
  and the private directory behind (restored by hand afterwards), so the procedure does
  exercise the second-signal path.

Results:

| | mutants | killed | survived | timeout | suspicious |
|---|---|---|---|---|---|
| round 1, final (old script, round-1 code) | 456 | 438 | 14 | 4 | 0 |
| round 2, after the code changes, before the survivor work (commit `8073ca2`) | 356 | 345 | 7 | 4 | 0 |
| round 2, final (commit `124635a`) | 349 | 342 | 3 | 4 | 0 |
| round 3, after the code changes (commit `7ef9b02`) | 361 | 352 | 5 | 4 | 0 |
| round 3, final (commit `5fe6830`) | 361 | 354 | 3 | 4 | 0 |
| round 4, after the code changes | 371 | 363 | 4 | 4 | 0 |
| round 4, after the check-script fix | 370 | 363 | 3 | 4 | 0 |
| round 4, final (commentary rule removed) | 362 | 355 | 3 | 4 | 0 |

```
$ scripts/verify/mutation.sh
...
TOTAL mutants 362  killed 355  survived 3  timeout 4  suspicious 0  untested 0
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
`test_short_text_reply_accepted[X-X\n]` and `test_multi_line_reply_ending_in_colon_accepted`
(the second test and the condition it guarded were removed in round 4).
The other three are the round-2 survivors at new line numbers and ids.

Round 4: the one new survivor was the message of the `TypeError` raised for a non-string
`text` in `phase0_code_segments.py` (string mutated to `'XX…XX'`); the script prints only the
exception type, so the message was dropped ("refactor: drop the unused message of the
non-string text error"). After the commentary rule was removed, `_is_empty_reply` keeps
only the backtick/whitespace check and the source check; they produced no survivors.

The 3 remaining survivors (ids and lines of the round-4 final run), equivalent under the stated
condition:

| id | location | mutation | why no input distinguishes it |
|---|---|---|---|
| 19 | `translator.py:114` | `re.match(p, text + '\n')` → `text + 'XX\nXX'` | the two patterns are `^(?:prefix words)[^\n]*\n+` with `re.match`: what follows `text` matters only through its first newline, which `[^\n]*` reaches in both versions; `XX` cannot complete a prefix word (none contains `X`). |
| 63 | `translator.py:324` | default `source=''` → `'XXXX'` | the default is used only when no source is passed (every production call passes it); both `''` and `'XXXX'` contain no code fence and do not start with a commentary word, the only two ways `source` is used |
| 113 | `phase0_builder_alignment.py:44` | `sys.path.insert(0, root)` → `insert(1, root)` | equivalent with default interpreter path settings: then `sys.path[0]` is the script's own directory, which has no `src` package, and at index 0 or 1 the repository root precedes `PYTHONPATH` entries and site-packages, so the same `src` is imported. Not equivalent when `sys.path[0]` is not the script's directory (`-P` or `PYTHONSAFEPATH`, Python 3.11+) and that first entry, e.g. the first `PYTHONPATH` entry, holds another `src` package (review round 3, A3); not tested |

Round-1 survivors that no longer exist: the glossary ones (`_VOWELS`, `_NO_DOUBLING`,
`lru_cache`, `(?!)`) went with the code; 371/372/379 in the alignment check (skip of a missing,
non-document or body-less spine item) were reachable after all and are killed by
`test_missing_spine_item_is_skipped_not_the_rest`, `test_non_document_spine_item_is_skipped`
and `test_document_without_body_is_skipped_not_the_rest` (ebooklib renders an empty document
without `<body>`); 271 and 357 (`.get` defaults) went with the code; 347 and 355 were on lines
removed in round 2; 337 (`sys.path` index) is 113 above (111 in round 2, 112 in round 3).

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
`translate.py` and `scripts/` from `d22807f`, the last commit before round 1):

```
$ git checkout d22807f -- src translate.py scripts
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
`translate.py` and the two check scripts from `cd2ea1c`, the last commit before round 2):

```
$ git checkout cd2ea1c -- src translate.py scripts/verify/phase0_code_segments.py scripts/verify/phase0_builder_alignment.py
$ python3 -m pytest tests/ -q
90 failed, 329 passed in 43.21s
$ git checkout HEAD -- src translate.py scripts/verify
```

All 90 failures are round-2 tests. Many builder, project and check-script tests fail on the old
source only because the `segmenter_version` field does not exist there; the substantive ones
are listed per item below.

| Item | Verdict | Evidence | Fix |
|---|---|---|---|
| A1 builder signatures misclassify both generations | confirmed | pre-round builder: a new project's `<!--note-->note<h1>Title</h1>` chapter selected the legacy walk; `TestVersion1ExportMatchesMain[comment_holds_markup-*-None]` (old extractor stored `<!--<p>Draft</p>-->` as a paragraph) and `[comment_equals_text-*-None]` differ from `main`'s output on the pre-round builder | `5cf02bf`: `segmenter_version` marker (D1) |
| A2 empty cleaned reply accepted | confirmed | `test_code_reply_empty_after_cleanup_is_retried` (4 shapes), `test_text_reply_empty_after_cleanup_raises` failed before | `1de8807` (D3) |
| A3 groups share one mutmut baseline | confirmed by reading mutmut 2.5.1 (`time_test_suite` reuses the cached time when the tests-directory hash matches; the cache file is `.mutmut-cache` in the working directory) | — | `8073ca2`: empty cache per group |
| A4 cancellation leaves the run alive | confirmed by reading (mutmut and pytest were not in a group the script controlled) | interrupt tests in section 5 | `8073ca2`: own process group, kill and restore on INT/TERM |
| A5 writes outside the worktree | partly: patch, runner and pytest temp files were in the system temp tree; per the decision a private `mktemp -d` directory is enough | — | `8073ca2`: one private directory for patch, runner, `TMPDIR`, `--basetemp`; section 5 says what is written where |
| A6 suffixes match unrelated words (`Go`→goes …) | superseded by simplification | — | `6d9a3ae` (D2); now accepted and documented: other words starting with the term match |
| A7 survivors called equivalent although inputs distinguish them | confirmed for 271, 357, 371, 372 (and 379); 347 and 164 no longer exist | section 5 | survivors removed from code or killed by tests; remaining 3 justified |
| A8 `C` matches inside `C++`/`C#` | superseded by simplification | — | accepted (documented in `_term_pattern`) |
| A9 plural entries do not match singular | superseded by simplification | — | accepted (documented) |
| A10 lost comment-line indentation not reported | confirmed | `TestCodeLines::test_lost_comment_indentation_differs` (2 cases), `test_comment_wording_ignored` failed before | `ece8470` (D5) |
| A11 backup guard misses a single `.bak` | confirmed by reading (`ls` of several paths fails when any is missing) | — | `8073ca2`: each path checked |
| A12 stale bytecode can still be read | confirmed by reading; the same effect was hit by hand during this round (two same-size edits within one second, the second run used the first's bytecode from the macOS `pycache_prefix`) | — | `8073ca2`: private `PYTHONPYCACHEPREFIX` |
| B1 legacy chapter with a comment holding markup or an entity uses the new walk | confirmed | `TestVersion1ExportMatchesMain[comment_holds_markup-*-None]`, `[comment_with_entity-*-None]` failed before | `5cf02bf` (D1) |
| B2 commentary line before unfenced code kept | confirmed | `test_code_preface_before_unfenced_code_removed` (3 cases) failed before; `test_code_preface_without_fence_not_removed`, which locked the old behaviour, replaced | `1de8807` (D4) |
| B3 emptiness check before cleanup; single-newline empty fence not recognised | confirmed | as A2, plus `test_code_empty_fence_cleans_to_empty['```python\n```']` failed before | `1de8807` (D3) |
| B4 suffix false matches; `C` in `C++`/`CS` | superseded by simplification | — | `6d9a3ae` (D2), accepted |
| B5 plural entries; `C++11`, `Python3` no longer matched | superseded by simplification; `C++11`/`Python3` match again (no trailing boundary) | — | `6d9a3ae` (D2) |
| B6 0-based line numbers | confirmed | — | `8073ca2`: 1-based; log locations corrected |
| B7 survivors 371/372 reachable; 271 reason inexact | confirmed | spine tests (section 5) | `124635a`, `5cf02bf` |
| B8 = A10 | confirmed | as A10 | `ece8470` |
| C1 `es`, doubling and `y` rules wrong | superseded by simplification | — | `6d9a3ae` (D2) |
| C2 plural entries | superseded by simplification | — | accepted (documented) |
| C3 empty fenced / prefaced replies saved without retry | confirmed | as A2/B3; for code segments a commentary-only reply (`以下是翻譯結果：\n`) cleans to empty, since code cleanup removes the commentary line, and is retried; for text and HTML segments a commentary-only reply is stored (round 4, B2) | `1de8807` (D3) |
| C4 `C` matches inside `C++`/`C#` | superseded by simplification | — | accepted (documented) |
| C5 = A10 | confirmed | as A10 | `ece8470` |

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
| A1 `mutation.sh`: mutmut failing clears the PID, cleanup skips group kill and restore | confirmed | section 5, round 3: killing mutmut itself now restores the file; the failure path previously set `MUTPID=""` before `exit 2` | `89dcd21`: restore and warning run on every exit; the PID is kept on failure so the group is stopped |
| A2 text/HTML reply of only fences (`` ```html\n``` `` → `` ``` ``) or only a commentary line is saved | confirmed | `test_text_reply_only_fence_or_commentary_is_retried` (8 cases) and `…_exhausts` (2) failed before; `test_short_text_reply_accepted` (`好的`, `當然`, `以下是步驟：` for a source ending in a colon, `X`) passes before and after | `7b5fbe4`: `_is_empty_reply` — empty when only backticks remain, or a single commentary line (existing start patterns) ending in a colon when the source does not end in one. In round 4 the backtick rule became whitespace-aware (A1) and the commentary rule was removed: a commentary-only reply is deliberately not treated as empty, an accepted limitation left to Phase 4's reply validation (B2; section 9) |
| A3 survivor 111 not unconditionally equivalent | confirmed (by reading; not run) | `sys.path[0]` is not the script's directory under `-P`/`PYTHONSAFEPATH` (3.11+) | disposition (section 5) and the script's comment state the condition; no subprocess test, per the round-3 decision |
| A4 code segment without `text` but with an empty translation counted as empty, not unreadable | confirmed | `test_code_segment_without_text_is_unreadable['']` failed before | `023c925`: source read before classifying |
| B1 = A1 | confirmed | as A1 | `89dcd21` |
| B2 cleanup not protected from a second INT/TERM | confirmed by a real run of the round-2 script (section 5) | file left mutated, `.bak`, cache and private directory left | `89dcd21`: `trap '' INT TERM` at the start of `cleanup` |
| B3 alignment check proves only that the legacy walk is chosen, not equality with `main` | confirmed | the check compares the chosen walk with `legacy=True` of the branch's builder | `7ef9b02`: output "not on legacy walk", docstring and section 4 say what it shows; equality with `main` rests on `TestVersion1ExportMatchesMain` |
| B4 C3 row: commentary-only reply retried only for code | confirmed | — | row reworded (and again in round 4: text/HTML commentary-only replies are stored) |
| B5 header says the mutmut cache goes to the private directory | confirmed | `.mutmut-cache` is in the repository root | `89dcd21`: header corrected |
| B6 `main`/`v1.0.0` cannot load a project created on this branch | confirmed | `v1.0.0`'s `src/` on a project created by this branch: `TypeError` | cannot be fixed on the branch; "Known consequences" and the Phase 0 section of `plan/02-improvement-plan.md` |

### 9. Review round 4

Three reviews: A (2 items, changes requested), B (3 items, changes requested), C (no
findings, approved). The reviewers could not run Python; each code finding was reproduced with
a failing test before the fix. Fixes are named by commit subject.

| Item | Verdict | Evidence | Fix |
|---|---|---|---|
| A1 code reply of only fence lines (`` ```\n``` `` for a source holding a fence) saved; a source of one backtick with the same reply rejected | confirmed | `test_code_reply_of_fence_markers_on_two_lines_is_retried` and `test_backtick_only_source_and_matching_reply_accepted` (`` ` `` as text and as code) failed before; its `` ```\n``` `` code case passed before and guards the exception | "fix: treat replies of only backticks and whitespace as empty": a reply of only backticks and whitespace is empty, unless the source holds nothing else |
| A2 = B1 | confirmed | as B1 | as B1 |
| B1 saving a pre-Phase-0 project writes `segmenter_version: 1`, which pre-Phase-0 code cannot load | confirmed | `test_saving_project_without_segmenter_version_keeps_it_absent` failed before; `test_version_2_project_keeps_segmenter_version_through_save` passes before and after | "fix: keep segmenter_version out of project.json when saving a version-1 project"; "Known consequences", the plan and the code comment corrected |
| B2 commentary rule keyed on colons: commentary-only reply kept for a long source ending in a colon; `以下是一個範例：` for "Here's an example." rejected | confirmed; resolved by removing the commentary rule | `test_short_text_reply_accepted[Here's an example.-…]` failed before | "fix: stop treating a commentary-only reply as empty" (after an intermediate commit in this round, "fix: judge a commentary-only reply by the size of the source, not by colons", which was replaced on the owner's decision because it rejected real translations): a reply is empty only when nothing but backticks and whitespace remains, in every mode. A commentary-only reply is deliberately not treated as empty (as on `main`); the first case of the finding is an accepted limitation, to be covered by Phase 4's reply validation |
| B3 code check: `"text": null` with an empty translation counted as empty; log claimed every code segment is checked | confirmed | `test_code_segment_with_null_text_is_unreadable` failed before (`''`: counted as empty; `XXXX`: unreadable, but through `AttributeError`) | "fix: report a code segment whose text is not a string as unreadable"; section 3 reworded (translated or reviewed segments only) |

Accepted limitation (B2): a reply that is only a commentary line (e.g. `以下是翻譯結果：`) is
stored as the translation of a text or HTML segment, as on `main`. Detecting such replies is left to Phase 4's structural validation
of replies. Code segments are not affected in the same way: code cleanup removes a commentary
line followed by a newline, so a commentary-only code reply cleans to empty and is retried.
Tests: `test_text_commentary_only_reply_is_stored` documents the accepted behaviour;
`test_long_one_line_translation_starting_with_commentary_word_accepted` (`當然，…` for a long
sentence), `test_short_text_reply_accepted`, `test_text_reply_only_fence_is_retried` and
`test_text_reply_only_fence_exhausts` (renamed from `…_fence_or_commentary_…`) cover the rest.

### 10. Review round 5

Three reviews, all approved. One reviewer left three minor notes (it could not run Python;
counting test functions and cases it also arrived at 444):

| Item | Disposition |
|---|---|
| 1 a fenced code reply is not unwrapped when the source contains three backticks anywhere | accepted limitation, no code change (see "Known consequences") |
| 2 `.gitignore` entry `html/` ignores any directory named `html`; `mutation.sh` never writes a mutmut HTML report | confirmed (the script only runs `mutmut run` and reads `.mutmut-cache`); entry removed ("chore: drop the unused html/ ignore entry") |
| 3 `CLAUDE.md` states the old test count and test-file list; a comment in `tests/test_translator.py` still describes the removed size rule | test comment fixed (in the same commit as item 2); `CLAUDE.md` not edited in this phase (see "Not done") |

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
- A code reply wrapped in a fence is not unwrapped when the source itself contains three
  backticks anywhere (e.g. a Python string holding a fenced JSON example): the fence lines are
  stored and appear in the exported `<pre>`. Nothing is lost, and the code-segment check
  script (section 3) flags such a segment as differing, so it can be re-translated (review
  round 5, item 1; accepted limitation).

### Not done

- `CLAUDE.md` still states the old test count and test-file list.
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
  test command on 3.9.6; the full suite passes on both (444), but mutants were not run on 3.9.

---

## Phase 1 — Foundations

Date: 2026-11-19. Branch: `phase-1/foundations`, from `main` after Phase 0. No new runtime
dependency. Commands run in `<repo>` with `source venv/bin/activate` (Python 3.9.6 for the
suite, 3.12.7 for mutmut, as in Phase 0).

### 1. What was built

| Item | Where | Main names |
|---|---|---|
| Backend interface (4.4) | `src/llm/base.py` | `Message`, `LLMRequest`, `LLMResponse`, `Usage`, `Capabilities`, `ModelInfo`, `LLMBackend` (Protocol); errors `LLMError` > `RateLimitError(retry_after)`, `AuthError`, `TransientError`, `RefusalError`, `CancelledError` |
| CLI base class | `src/llm/cli.py` | `run_process` (the only process start for model calls: `Popen` in a new session, stdin empty, polls every 0.1 s for cancel and deadline, kills the process group), `CLIBackend` (environment cleaning via `env_remove`, working directory `tempfile.gettempdir()`, timeout → `TransientError`, start failure → `LLMError`), `flatten_prompt` |
| Claude CLI backend | `src/llm/claude_cli.py` | `ClaudeCLI`: `build_command`, `parse_output`, `classify_error`, `list_models`; `MODELS` (the former `AVAILABLE_MODELS` data) |
| Fake backend | `src/llm/fake.py` | `FakeBackend(replies=… or responder=…)`, records `requests` |
| Backend selection | `src/llm/__init__.py` | `default_backend()` (returns `ClaudeCLI()`; tests replace it) |
| One entry point | `src/core/translator.py` | `translate_segment()`, `TranslationOutcome`, `TranslationEngine.translate_reply()` (`translate()` kept, returns the text), `TranslationError(code, prompt, attempts)`, `write_failure_log` |
| Segment state (4.3) | `src/core/project.py` | `SegmentStatus.FAILED`, `Issue`, `SegmentState.issues/revision/model/usage`, `ProjectManager.apply_outcome()`, `get_pending_segments(include_failed=)`, `ProjectLock`, `ProjectLockedError` |
| Call sites | `src/web/tasks.py`, `src/web/blueprints/review.py`, `translate.py` | web worker + coordinator `_apply_result`; re-translate endpoint; CLI `_start_sequential`, `_start_parallel` |
| Term extraction | `src/glossary/term_extractor.py`, `src/web/blueprints/glossary.py`, `translate.py` | `TermExtractor(model=…, backend=…)`; both callers pass the project model |
| FAILED in the UI | `search.py`, `translation.py`, `server.py`, templates, `style.css` | search includes FAILED; `retry_failed` on the start API and a button on the translation tab; failed count on the overview; issue detail as the status tooltip in review; 409 on a save while another process holds the lock |
| Version | `src/__init__.py`, `translate.py` | `__version__ = "1.0.0"`, `--version` |
| Check script | `scripts/verify/phase1_load_projects.py` | per project: counts by status, PASS/FAIL |

Design choices where the plan left room:

- **Lock.** An exclusive `fcntl.flock` on `<project>/project.lock` (the file holds the
  holder's PID, for the message; it is left in place on release). Threads of one process
  share the lock (a per-path count), other processes get `ProjectLockedError`. Taken by every
  `save()` (briefly), by CLI `start` for the whole run, and by the web task from
  `start_translation` until its coordinator ends. The kernel drops a flock when its holder
  exits in any way, so a lock left by a dead process never blocks and no PID check is needed.
  The file descriptor is not inherited by child processes. Not available on Windows (`fcntl`).
- **Retry of FAILED.** A normal start (`get_pending_segments()`) skips FAILED segments. CLI:
  `start --retry-failed`; web: `{"retry_failed": true}` on `/translate/start`, shown as a
  "重試失敗段落（N）" button on the translation tab when N > 0. Selecting segments in the
  search page (which now lists FAILED segments) and re-translating them also works.
- **CLI exit code.** After a run in which some segments failed, the CLI prints the count and
  the diagnostics directory and exits 1; all other segments are translated first. A rate
  limit or an expired login still stops the run (exit 1), leaving the segment pending.
- **CLI flags.** None added: `ClaudeCLI` runs `claude -p <prompt> --model <alias>` as before
  (`-p/--print` and `--model` checked in `claude --help`, version 2.1.328). Plain-text output
  has no usage, so `reports_usage` is false and usage is `None`. The system prompt and turns
  are flattened by `flatten_prompt`; the translator sends one user message and no system
  prompt, so the prompt text is byte-identical to before. Process changes: stdin is
  `/dev/null` (before: inherited) and the process runs in its own session so cancel and
  timeout kill its children too.
- **Classification.** As before: "You've hit your limit" in stdout or stderr → rate limit;
  `authentication_error|OAuth token has expired|please run /login` → auth (both whatever the
  exit code); non-zero exit or timeout → transient (retried, 3 attempts); an empty reply after
  cleanup → retried. New: the program missing (`OSError` on start) → `LLMError`, not retried
  (before: an uncaught exception that killed the worker). Term extraction now also stops on a
  rate limit (before: only on an auth error with a non-zero exit) and passes `--model`.
- **Diagnostics.** `<project>/log/failed/<segment id>.txt` (characters outside `[\w.-]`
  replaced by `_`): segment, model, time, error, the request, and per failed attempt its
  message and raw output (stdout + stderr, at most 20 000 characters each). One file per
  segment, replaced on each failed run: bounded by the number of segments. Removed when the
  segment later translates; nothing is written for a success.
- **Defaults omitted on save.** `issues` `[]`, `revision` 0, `model` `''`, `usage` `None` are
  left out of each segment; `segmenter_version` 1 as in Phase 0. No new top-level field.
  `load()` only reads.
- **Revision.** `update_translation` and `mark_reviewed` (approve, with or without text)
  increment it. The web worker records it when it takes a segment; the coordinator applies
  the result only if it is unchanged (a dropped result is logged and counted as done). The
  re-translate endpoint does the same around its own call and answers 409 if the segment was
  edited meanwhile. The CLI holds the lock, so it passes no revision.
- **Cancel.** `CLIBackend` honours the `threading.Event`; the callers pass none yet, so stop
  and pause still wait for the in-flight call (terminating it is Phase 7).

### 2. Test suite

```
$ python3 -m pytest tests/ -q
622 passed, 32 warnings in 66.09s
```

444 before the phase; 578 at the first submission, 622 after the mutation work (section 6).
New test file `tests/test_llm.py`; new classes in `test_project.py`,
`test_translator.py`, `test_cli.py`, `test_web.py`, `test_glossary.py`,
`test_verify_scripts.py`. An autouse fixture in `tests/conftest.py` stubs `run_process`, so no
test can start the real `claude` (a call reaching it fails like an error exit); the tests of
`run_process` itself start short Python child processes. No test was removed. Changed tests:

- 46 + 9 tests patched `subprocess.run` in `src.core.translator` / `src.glossary.term_extractor`;
  they now patch `src.llm.cli.run_process` (same arguments: command list, `timeout`, `cwd`,
  `env`; same result object), unchanged otherwise.
- `test_subprocess_called_with_timeout` no longer asserts `capture_output`/`text` keyword
  arguments, which `run_process` does not take (it always captures text).
- 10 web tests patched `TranslationEngine` in `src.web.tasks` / `src.web.blueprints.review`;
  they now use `FakeBackend` through `default_backend()` with the same replies.
  `test_retranslate_uses_project_model` asserts the model of the request instead of the
  engine's constructor argument; `test_model_switch_during_translation` records the model of
  each request instead of the engine's attribute.
- `test_original_intact_if_write_fails` blocks the per-process temporary file name.

Acceptance item → tests:

| Item | Tests |
|---|---|
| failing segment, sequential and parallel CLI | `test_cli.py::TestCLIFailedSegments::test_failing_segment_recorded_and_rest_translated[1]`, `[3]` |
| failing segment, web worker (1 and 3 workers) | `test_web.py::TestWorkerFailedSegment::test_failing_segment_recorded_and_rest_translated` |
| FAILED retried only on request | `TestCLIFailedSegments::test_failed_not_retried_without_flag`, `TestWorkerFailedSegment::test_failed_queued_only_with_retry_failed`, `test_project.py::TestFailedStatus` |
| stale-revision result dropped | `test_project.py::TestApplyOutcome` (5 tests), `test_web.py::TestEditDuringTranslation` (an edit through the review endpoint while the fake backend holds the call) |
| second process cannot take the lock | `test_project.py::TestProjectLock::test_second_process_cannot_take_lock`, `test_save_refused_while_other_process_holds_lock`, `test_cli.py::TestCLILock::test_start_refused_while_other_process_holds_lock`, `test_web.py::TestWebProjectLock` (start and edit → 409) |
| lock released on error / normal exit | `TestCLILock::test_lock_released_on_error[1]`, `[3]`, `test_lock_released_after_rate_limit_exit`, `test_lock_held_during_run_and_released_after`, `TestWebProjectLock::test_lock_released_when_loading_fails`, `…_when_coordinator_fails`, `…_when_nothing_to_translate`, `…_held_during_task_and_released_after` |
| stale lock recoverable | `TestProjectLock::test_lock_of_killed_process_is_recoverable` (holder killed with SIGKILL) |
| rate-limit / auth / transient classification | `test_llm.py::TestClaudeCLIClassification`, `TestCLIBackendComplete`, `test_translator.py::TestTranslateSegment::test_failure_is_an_outcome`, `test_run_level_errors_raised_without_retry`, and the existing `TestRateLimitDetection` through `run_process` |
| cancel stops an in-flight call | `test_llm.py::TestRunProcess::test_cancel_stops_in_flight_call`, `test_cancel_kills_grandchildren` |
| term extraction passes the project model | `test_web.py::TestTermExtractionModel`, `test_cli.py::TestCLIGlossaryExtract`, `test_glossary.py::TestTermExtractorBackend` |
| failure diagnostics written and bounded | `test_translator.py::TestFailureDiagnostics` (3 failed runs with 50 000-character outputs → one file under 65 000 bytes) |
| old project round-trips without new keys | `test_project.py::TestOldProjectCompatibility` (a `v1.0.0`-shaped `project.json`) |
| `--version` | `test_cli.py::TestCLIVersion` |
| usage recorded when reported | `TestApplyOutcome::test_success_records_text_model_and_usage`, `TestTranslateSegment::test_success` |

### 3. New tests fail on the old code

Running the branch's tests against `main`'s `src/`, `translate.py` and `scripts/` (in a copy
under a temporary directory) stops at collection: the six changed test modules import
`src.llm` or the new names, and the autouse fixture patches `src.llm.cli.run_process`.

```
$ python3 -m pytest tests/ -q        # tests from the branch, source from main
ERROR tests/test_cli.py
ERROR tests/test_llm.py
ERROR tests/test_project.py
ERROR tests/test_translator.py
ERROR tests/test_verify_scripts.py - FileNotFoundError: …
ERROR tests/test_web.py
!!!!!!!!!!!!!!!!!!! Interrupted: 6 errors during collection !!!!!!!!!!!!!!!!!!!!
```

That proves little, because the seams (`FakeBackend`, `run_process`) do not exist on `main`.
So each old behaviour was also put back, one at a time, into a copy of the branch (after the
mutation work), and the tests that guard it were run (a throw-away script in a temporary directory; each revert is a
one-place text replacement):

```
OK   sequential CLI exits on a failed segment: 1 failed
OK   parallel CLI stops all workers on a failed segment: 1 failed
OK   web worker does not record a failed segment: 3 failed
OK   result applied whatever the revision: 3 failed, 9 passed
OK   manual edits do not change the revision: 3 failed, 9 passed
OK   no project lock: 10 failed, 11 passed
OK   new fields always written: 2 failed, 1 passed
OK   FAILED queued by every start: 4 failed, 3 passed
OK   failed re-translation attaches no issue: 1 failed
OK   web term extraction without the project model: 1 failed
OK   CLI term extraction without the project model: 1 failed
OK   extraction continues after a rate limit: 1 failed
OK   no failure diagnostics: 5 failed, 2 passed
OK   cancel ignored: 1 failed
OK   search skips FAILED: 1 failed
OK   no --version: 1 failed
reverts not detected: 0
```

("N passed" are tests in the same selection that guard something else, e.g. the lock tests
that check the lock is free after release.) The mutation run (section 6) covers the rest.

### 4. `subprocess` only in the backend module

```
$ grep -rn "subprocess" src/ translate.py | cut -d: -f1 | sort | uniq -c
   9 src/llm/cli.py
```

### 5. Existing projects load unchanged

```
$ python3 scripts/verify/phase1_load_projects.py --projects-dir <repo>/projects --anonymize
PASS project-1: segments 220, pending 0, translated 220, reviewed 0, failed 0
PASS project-2: segments 291, pending 0, translated 291, reviewed 0, failed 0
PASS project-3: segments 4385, pending 4349, translated 36, reviewed 0, failed 0
PASS project-4: segments 2236, pending 903, translated 1333, reviewed 0, failed 0
PASS project-5: segments 840, pending 7, translated 833, reviewed 0, failed 0
PASS project-6: segments 581, pending 0, translated 581, reviewed 0, failed 0
PASS project-7: segments 637, pending 74, translated 563, reviewed 0, failed 0
PASS project-8: segments 331, pending 0, translated 331, reviewed 0, failed 0
TOTAL projects 8  segments 9521  pending 5333  translated 4188  reviewed 0  failed 0  failed projects 0
$ echo $?
0
```

A project FAILs when loading raises, when `project.json`'s SHA-256 or modification time
(nanoseconds) changed, or when the set of files in the project directory changed (so a
created lock file would fail it). Size and modification time of every `project.json` and
`project.lock` under `projects/` were also listed before and after the run: identical
(no lock file exists there). Covered by `tests/test_verify_scripts.py::TestLoadCheck`.

Round trip and `v1.0.0`, on copies of the 8 `project.json` files in a temporary directory
(deleted afterwards): load and save with the branch, compare with the original, then load the
saved file with `v1.0.0`'s `src/core/project.py`:

```
project-1: round-trip equal except updated_at True (top-level keys added: ['model']); v1.0.0 load ok
project-2 … project-8: round-trip equal except updated_at True (top-level keys added: []); v1.0.0 load ok
TOTAL 8/8
```

project-1 predates the `model` field; any save adds `model` there, as on `main` and in
`v1.0.0` itself. And on synthetic projects saved by the branch: one with `model`, `revision`
and `issues` on segments (no FAILED segment) loads with `v1.0.0` (its `load()` drops unknown
segment keys); one with a FAILED segment does not (`ValueError: 'failed' is not a valid
SegmentStatus`).

### 6. Mutation testing

`scripts/verify/mutation.sh` now has five groups: core (as in Phase 0), `llm` (`src/llm/*` with
`test_llm.py`, `test_translator.py`, `test_glossary.py`), web (all changed blueprints and
`server.py`, with the Phase 1 web test classes added to `-k`), cli, scripts (with
`phase1_load_projects.py`). Only lines changed versus `main` are mutated.

| run | mutants | killed | survived | timeout | suspicious |
|---|---|---|---|---|---|
| 1, first submission | 559 | 384 | 172 | 3 | 0 |
| 2, after "test: kill surviving mutants of the Phase 1 code" | 532 | 509 | 18 | 5 | 0 |
| 3, after "test: kill the remaining killable mutants…" | not finished when this entry was written | | | | |

Run 1's survivors were mostly untested messages, event payloads, log lines, defaults and file
formats. They were killed with exact tests, or the code was simplified so the mutant no longer
exists (no defaults on internal dataclasses and arguments every caller passes; one issue per
failed outcome; code segments pass their HTML, which code mode ignores; simpler coordinator
loop; no fallbacks in the lock message).

Run 2's 18 survivors and what was done:

| location (run 2) | mutation | disposition |
|---|---|---|
| `project.py:374` | `flock(entry[0], LOCK_UN)` → `entry[1]` | code changed: the explicit unlock removed (closing the only descriptor drops the flock) |
| `tasks.py:127` | `project_lock` default `None` → `""` | killed by `TestTaskWithoutLock` |
| `translate.py:99` | `--retry-failed` help text wrapped in `XX` | killed: exact help test |
| `phase1_load_projects.py:68`, `:70` | help texts wrapped in `XX` | killed: exact help test |
| `project.py:68`, `tasks.py:127` | `X \| None` → `X & None` in an annotation | equivalent: annotations are strings under `from __future__ import annotations` and never evaluated |
| `project.py:342`, `:368` | `self._held = False` → `None` | equivalent: `_held` is only tested for truth |
| `project.py:356` | `os.read(fd, 32)` → `33` | equivalent while a PID has at most 32 digits (macOS/Linux PIDs have at most 7) |
| `project.py:360` | `ftruncate(fd, 0)` → `1` | equivalent: the PID written next at offset 0 has at least one character and overwrites the kept byte |
| `translator.py:405` | `glossary_text = ''` → `None` | equivalent: only its truth value is used (`if glossary_text`) |
| `cli.py:23` | `POLL_SECONDS = 0.1` → `1.1` | performance-only: cancel and timeout are noticed up to 1.1 s later |
| `cli.py:66` | `>= deadline` → `> deadline` | performance-only: differs only at exact float equality, by at most one more 0.1 s poll |
| `tasks.py:386`, `:388` | post-batch exit check disabled (`workers_done = None`; `break` → `continue`) | performance-only: the loop then ends through the queue-timeout path, at most 1 s later |
| `translate.py:248` | `get(timeout=0.5)` → `1.5` | performance-only: the result loop polls less often |
| `phase1_load_projects.py:34` | `sys.path.insert(0, …)` → `insert(1, …)` | equivalent under the same condition as Phase 0's survivor 113 (default interpreter path settings) |

The 5 timeouts of run 2 (`cli.py:23` `POLL_SECONDS = None`, `cli.py:54`
`start_new_session=False`, `cli.py:63` `cancel is None`, `tasks.py` Empty path
`workers_done = None` and `break` → `continue`) are detected: the tests do not finish.

Expected for run 3: the 4 killed/changed rows above gone, the 11 equivalent or
performance-only rows (13 mutants) left. Run 3 was started (`scripts/verify/mutation.sh`, about
1 h) but had not finished when this entry was committed; its TOTAL line is not recorded here.

### 7. One real call

A synthetic project in a temporary directory with one invented sentence ("The small robot
watered the tomato plants every morning before sunrise.", as `<p>` HTML), translated through
the CLI (sequential path → `translate_segment` → `ClaudeCLI`) with the cheapest supported
alias, from an export of the branch at the commit "chore: cover the Phase 1 modules and
tests in the mutation run" (before the mutation work; the later commits do not change
the command, the prompt of a text segment or how the outcome is stored, and the call was not
repeated):

```
$ python3 translate.py start --project <tmp>/p --model haiku
使用模型：haiku
繼續翻譯：0/1 已完成 (0.0%)
剩餘 1 個段落待翻譯

[1/1] 翻譯段落 c1_seg0001...
  ✓ 完成（11 字）

全部翻譯完成！
$ echo $?
0
```

Stored segment (`html`/`text` omitted): `{'segment_id': 'c1_seg0001', 'chapter_id': 'c1',
'word_count': 11, 'segment_type': 'text', 'status': 'translated', 'translated_text':
'<p>小機器人每天日出前都會替番茄植株澆水。</p>', 'translated_at': '2026-11-19 18:10:40',
'model': 'haiku'}`; project `model: haiku`. One call, first attempt, about 3 s wall time. The
project directory held `project.json` and `project.lock` afterwards, and no `log/` directory
(nothing is written for a success). Usage: not reported by this invocation, so not stored.

### Known consequences

- A project stops being loadable by `v1.0.0` (and by `main` before this phase) as soon as one
  of its segments is saved with status `failed`: `v1.0.0`'s `load()` raises
  `ValueError: 'failed' is not a valid SegmentStatus`. Retrying the segment successfully
  makes it loadable again. The new per-segment fields (`issues`, `revision`, `model`, `usage`)
  do not break `v1.0.0`, which drops unknown segment keys. A project with no failed segment
  that existed before Phase 0 stays loadable (Phase 0's `segmenter_version` rule unchanged).
- Each project directory gets a `project.lock` file at its first save (never on load). Saving
  a project while another process holds its lock fails: CLI `start` exits 1 with the holder's
  PID; web saves answer 409. Two web server processes on the same `projects/` exclude each
  other in the same way.
- The CLI exits 1 after a run with failed segments (it used to exit 1 at the first failure).
- Term extraction now passes the project model (before: the CLI's default model) and stops
  with an error on a rate limit.
- The temporary file of `save()` is `project.json.<pid>.tmp` (was `project.json.tmp`).
- A worker result for a segment edited mid-flight is dropped and counted as done in the
  progress of that run.
- The web coordinator ends only when all workers have exited *and* the result queue is empty.
  Before, it ended on a queue timeout as soon as no worker was active, so a result put just
  before the last worker exited could in principle be lost; it also had a separate final
  drain, now removed.
- Windows is not supported by the lock (`fcntl`).

### Not done

- The backend name is not stored in `project.json` (only one backend exists).
- Stop/pause do not cancel the in-flight call: callers pass no cancel event yet (Phase 7).
- Rate limiting is not owned by the backend instance (no queueing); workers still pause on
  `RateLimitError` as before. `retry_after` is always `None` for the Claude CLI.
- Validation on save/approve and at export (4.3) belongs to Phase 4.
- Glossary writes (`glossary.json`) are not under the project lock.
- `CLAUDE.md` is not updated (still describes `subprocess.run` in the translator).

### Not verified

- Only one real call was made (above). Rate-limit, auth and timeout handling against the real
  CLI were not exercised; their patterns are those of the old code, tested with stubs.
- Term extraction through the real CLI was not run.
- The web UI changes (retry button, failed card, tooltip) were checked by rendering the
  templates in tests, not in a browser.
- Mutation testing ran on Python 3.12.7, the suite on 3.9.6 (both pass).
