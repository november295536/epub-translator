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
| CLI base class | `src/llm/cli.py` | `run_process` (the only process start for model calls: `Popen` in a new session, stdin empty, polls every 0.1 s for cancel, waits bounded by the deadline, kills and reaps the process group on timeout, cancel or any exception, exit hook for calls still running), `CLIBackend` (environment cleaning via `env_remove`, working directory `tempfile.gettempdir()`, timeout → `TransientError` with the output so far, start failure → `BackendUnavailableError`), `flatten_prompt` |
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
  share the lock (a per-path count), other processes get `ProjectLockedError`; within the
  process, one mutex per project serializes changes and saves (review round 1). Taken by every
  `save()` (briefly), by every web transaction from before its load until after its save
  (review round 2), by CLI `start` for the whole run, and by the web task from
  `start_translation` until its coordinator ends. Order everywhere: the mutex, then the lock. The kernel drops a flock when its holder
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
  cleanup → retried. New: the program missing or not executable (`FileNotFoundError`,
  `PermissionError`, `ENOEXEC` on start) → `BackendUnavailableError`, a run-level error
  (review round 1, item 15; narrowed in review round 2, item 4), not retried (before: an
  uncaught exception that killed the worker); any other start error (e.g. `E2BIG`) fails that
  segment only; `EAGAIN`, `ENOMEM`, `EMFILE`, `ENFILE` at start are transient (retried;
  review round 3, item 4). An exit-0 reply is not taken for an error when the matched phrase
  on stdout also occurs in the request (review round 2, item 15); a phrase on stderr always
  counts (review round 3, item 3). Output is decoded as UTF-8 with replacement (review round 3,
  item 7). Five `unexpected` failures in a row stop a run (`MAX_CONSECUTIVE_UNEXPECTED`,
  review round 3, item 9). Term extraction now also stops on a
  rate limit (before: only on an auth error with a non-zero exit) and passes `--model`.
- **Diagnostics.** `<project>/log/failed/<segment id>-<digest>.txt` (characters outside
  `[A-Za-z0-9_.-]` replaced by `_`, at most 100, then the first 10 hex digits of the id's
  SHA-256; review rounds 1 and 2): segment, model, time, error, the request, and per failed attempt its
  message and raw output (stdout + stderr, at most 20 000 characters each). One file per
  segment, replaced on each failed run: bounded by the number of segments. Written, replaced
  or removed only where an outcome is accepted and saved (`record_failure_log`), or by an
  edit or approval inside its transaction (review round 2, items 10 and 17): a stale result
  leaves the file alone; nothing is written for a success. An `unexpected` failure writes a
  short log too: error, the request when it can be rebuilt, and the traceback (review round 3,
  item 7).
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
$ python3 -m pytest tests/ -q -p no:cacheprovider -W ignore
869 passed in 130.33s
```

444 before the phase; 578 at the first submission, 622 after the mutation work (section 6),
708 after the review round 1 fixes (section 8), 749 after the tests that kill that round's
mutation survivors (section 10), 816 after review round 2 (section 11), 858 after review
round 3 (section 14) and the tests that kill its mutation survivors (section 16), 869 after
review round 4 (section 17).
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

After review round 1 (the check now also compares an in-memory save with the file, item 20):

```
$ python3 scripts/verify/phase1_load_projects.py --projects-dir <repo>/projects --anonymize
PASS project-1: segments 220, pending 0, translated 220, reviewed 0, failed 0, round trip model added
PASS project-2: segments 291, pending 0, translated 291, reviewed 0, failed 0, round trip equal
PASS project-3: segments 4385, pending 4349, translated 36, reviewed 0, failed 0, round trip equal
PASS project-4: segments 2236, pending 903, translated 1333, reviewed 0, failed 0, round trip equal
PASS project-5: segments 840, pending 7, translated 833, reviewed 0, failed 0, round trip equal
PASS project-6: segments 581, pending 0, translated 581, reviewed 0, failed 0, round trip equal
PASS project-7: segments 637, pending 74, translated 563, reviewed 0, failed 0, round trip equal
PASS project-8: segments 331, pending 0, translated 331, reviewed 0, failed 0, round trip equal
TOTAL projects 8  segments 9521  pending 5333  translated 4188  reviewed 0  failed 0  round trip equal 7  model added 1  differs 0  failed projects 0
$ echo $?
0
```

Size, modification time and path of every `project.json`, `project.lock` and `*.tmp` under
`projects/` were listed before and after this run: identical (8 `project.json`, no lock or
temporary file).

A project FAILs when loading raises, when `project.json`'s SHA-256 or modification time
(nanoseconds) changed, or when the set of files in the project directory changed (so a
created lock file would fail it). Size and modification time of every `project.json` and
`project.lock` under `projects/` were also listed before and after the run: identical
(no lock file exists there). Covered by `tests/test_verify_scripts.py::TestLoadCheck`.

Round trip is now part of the check above ("round trip"). The earlier, one-off check with
`v1.0.0` (first submission, not repeated), on copies of the 8 `project.json` files in a
temporary directory (deleted afterwards): load and save with the branch, compare with the original, then load the
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

Run 3 was started on that code but not finished; it was abandoned. The rows above marked
performance-only for `cli.py:23` and `cli.py:66` were wrong at the time (review G10: a poll
could accept output after the deadline or after a cancel); `run_process` was fixed in review
round 1, after which the poll interval affects only latency. The complete run on the final
code is in section 10.

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

### 8. Review round 1

Three static reviews (G, O and a third reviewer whose two extra findings became items 24 and
25). Each accepted item got a test that fails on the code before the fix; the evidence is
`scripts/verify/phase1_reverts.py`, which puts every old behaviour back, one at a time, into
a copy of the committed code and runs the guarding tests (section 9). No finding was
rejected as not reproducible.

| # | finding | ruling | what was done | tests |
|---|---|---|---|---|
| 1 | G1, O12: saves from several `ProjectManager`s of one project share `project.json.<pid>.tmp` | accepted | `project_mutex(project_dir)`: one `RLock` per real project path, shared by every manager (`pm.mutex`); `save()` holds it and the flock, writes a `tempfile.mkstemp` file of its own (mode of the existing `project.json`, else 0644) and removes it on failure | `test_project.py::TestSharedProjectMutex` (3), `TestAtomicSave::test_original_intact_if_write_fails` (changed: the temp name is no longer predictable, so the replace is made to fail; also checks no `.tmp` is left) |
| 2 | G2, O3: web handlers without a running task each load their own copy; re-translate saves a stale snapshot | accepted | `TaskManager.transaction()`: the project mutex plus the authoritative manager (the task's while it runs, is paused or its threads are still alive, else a fresh load). Edit, approve, set-model and re-translate use it. Re-translate snapshots the segments in one transaction, calls the model outside it, then in a second transaction rechecks the revision on the current state, applies and saves. `start_translation` loads and registers the task under the mutex | `test_web.py::TestRetranslateDetails::test_edit_during_retranslation_is_kept` (rewritten: a real edit request, no patched `get_pm`), `TestRetranslateTransaction` (3: other segment not reverted; edit from a second request thread; edit while a stopped task is still ending) |
| 3 | G3, O5: revision check and write not atomic | accepted | `apply_outcome`, `update_translation`, `mark_reviewed`, `mark_translated`, `set_model` and the serialization in `to_json()` run under the project mutex | `test_project.py::TestApplyOutcomeAtomic` (3) |
| 4 | G4: a start can overlap a task whose threads are alive; simultaneous starts | accepted | starts serialized by `TaskManager._start_lock`; after waiting `STOP_WAIT_SECONDS` (5) for the old coordinator, a start is refused with 409 "上一個翻譯任務仍在結束中…" while its coordinator or any worker is alive | `test_web.py::TestOverlappingTasks` (2) |
| 5 | G5, O8: lock leaks on logging setup, thread start, PID write | accepted | logging set up inside the coordinator's `try`; a failing `coordinator.start()` releases the lock and marks the task failed (HTTP 500); `ProjectLock.acquire` closes the descriptor when anything after `flock()` fails | `test_web.py::TestStartFailures` (2), `test_project.py::TestLockAcquireFailure` |
| 6 | G6, O8: a failing supervisor releases the lock with workers running | accepted | web: `_fail_task` sets stop and pause events, joins every worker, drains the result queue and reports the segment ids applied but not saved (task error, log, `error` event with `unsaved`). CLI parallel: on any `BaseException` stops dispatch, joins the (now daemon) workers, prints the unsaved ids, re-raises | `test_web.py::TestCoordinatorFailure` (2, one with a worker paused on a rate limit), `test_cli.py::TestCLISupervisorFailure` (`OSError` and `KeyboardInterrupt`) |
| 7 | G7: a run error after stop re-pauses and deadlocks | accepted | the worker's handler checks `stop_event` under the task lock before clearing or waiting | `test_web.py::TestStopThenLateRunError` (auth, rate limit) |
| 8 | G8, O1: children left running on exceptional exit | accepted | `run_process` kills the group and reaps on any exception after `Popen` (then re-raises); the post-kill `communicate` waits at most `KILL_WAIT_SECONDS` (2), then closes the pipes; live groups are tracked and killed by one `atexit` hook. No signal handlers | `test_llm.py::TestRunProcessExceptionalExit` (`KeyboardInterrupt`, a `setsid` grandchild holding the pipes, the hook called directly, a child process left in a daemon thread at interpreter exit) |
| 9 | G9, O12: failure-log I/O errors and unexpected worker exceptions lose work | accepted | writing or removing the failure log: `OSError` logged, outcome returned. Web and CLI workers turn an unexpected exception into a FAILED outcome (issue code `unexpected`) for that segment. `test_lock_released_on_error` now asserts status and exit code (its warning filter removed) | `test_translator.py::TestDiagnosticWriteFailure` (3), `test_web.py::TestWorkerUnexpectedErrors` (2), `test_cli.py::TestCLIUnexpectedErrors` (3), `TestCLILock::test_lock_released_on_error` (changed) |
| 10 | G10: cancel and deadline bypassable within one poll | accepted | cancel checked before `Popen`; each wait is `min(POLL_SECONDS, time left)`; after the process ends, a set cancel or a passed deadline still raises. `POLL_SECONDS` now only affects cancel latency | `test_llm.py::TestRunProcessCancelAndDeadline` (3) |
| 11 | G11: 409 invisible in the UI | accepted | review save/approve: a non-success response marks the segment "未儲存" (failed style, error as tooltip) and alerts; model select reverts to the stored model and shows the error | `test_web.py::TestTemplatesHandleErrors` (string checks of the rendered pages) |
| 12 | G12, O10: Claude specifics outside `src/llm/` | accepted | backends have `default_model` and `auth_hint`; `translator.available_models()` / `default_model()` ask `default_backend()` (constants `AVAILABLE_MODELS` / `DEFAULT_MODEL` removed); `--model` is checked against the backend's list when used; new projects and projects without `model` get the backend's default; templates take the auth hint from a context processor; the review button title no longer names Claude | `test_web.py::TestBackendModels` (4), `test_cli.py::TestCLIBackendModels`, `test_translator.py::TestEngineDefaultModel`, `test_llm.py::TestBackendDefaults`; `TestModelSelection` changed to the functions |
| 13 | G13: unbounded error text | accepted | issue detail and every message in the failure log at most `ERROR_MESSAGE_CHARS` (2000); raw output per attempt 20 000 as before; `ClaudeCLI` quotes at most 500 characters of output in a message; the attempt warning log line is bounded too | `test_translator.py::TestBoundedErrorText` (2), `test_llm.py::TestClassificationOfReplies::test_error_messages_bounded` |
| 14 | G14: output lost on timeout | accepted | the drained stdout/stderr (at most 20 000 characters each) ride on `TimeoutExpired` into `TransientError.raw` | `test_llm.py::TestTimeoutKeepsOutput` (3) |
| 15 | O2: missing executable fails every segment | accepted | `OSError` at start → `BackendUnavailableError` (new, in `src/llm/base.py`), raised like `AuthError`: web pauses the run (`backend_error` event, shown by the page), CLI stops (exit 1), re-translate answers 503, term extraction stops; no segment becomes FAILED | `test_llm.py::TestBackendUnavailable`, `TestCLIBackendComplete::test_missing_program_is_not_transient` (changed: the type), `test_translator.py::TestRunLevelErrors`, `test_web.py::TestBackendUnavailable` (2), `test_cli.py::TestCLIBackendUnavailable` (2) |
| 16 | O6: a dropped result emits `segment_complete` | accepted | a dropped or skipped result counts in progress but sends no success event or "段落完成" log line | `test_web.py::TestQueuedEdits::test_dropped_result_sends_no_success_event` |
| 17 | O7: revision taken at pickup; edits keep issues and FAILED | accepted | `task.queued[segment id] = (revision, status)` at queue time; `update_translation` clears issues and turns FAILED into TRANSLATED (other statuses kept, as on `main`); `mark_reviewed` clears issues | `test_web.py::TestQueuedEdits::test_queued_segment_edited_before_pickup` (edit, approve), `test_project.py::TestManualEditClearsFailure` (3) |
| 18 | O8: delete under a running task or another process's lock | accepted | 409 while the task runs, is paused or its threads are alive; otherwise delete holds the project mutex and the flock (409 with the holder's PID when another process has it) | `test_web.py::TestDeleteGuard` (2) |
| 19 | O9: extraction drops finished terms on a rate limit; CLI traceback; classification | accepted | `ExtractionError.terms` carries the terms of finished batches; web adds them and reports the stop with the count; CLI saves them, prints "提取中斷：…" and exits 1. Classification checked against `main`: the translator searched stdout + stderr for both patterns whatever the exit code; the extractor only on a non-zero exit, auth only. Kept: stderr and any non-zero run are searched; on exit 0, stdout only when at most `SHORT_REPLY_CHARS` (300) long (comment in `claude_cli.py`). Not verified against the real CLI (no limit or expired login was available) | `test_glossary.py::TestExtractionStopKeepsTerms` (2), `test_web.py::TestTermExtractionStop`, `test_cli.py::TestCLIGlossaryExtractStop`, `test_llm.py::TestClassificationOfReplies` (5) |
| 20 | O11: round trip not reproducible | accepted | `ProjectManager.to_json()` (what `save()` writes, without touching `updated_at`); the check compares it, parsed, with `project.json`: `equal`, `model added` (accepted, as on `main`), else FAIL with the differing top-level keys and segment count; per project and in TOTAL | `test_verify_scripts.py::TestLoadCheckRoundTrip` (3); exact-output tests updated |
| 21 | G16: failure log names collide | accepted | `<id with unsafe characters replaced, at most 100>-<first 10 hex of SHA-256 of the id>.txt` | `test_translator.py::TestFailureLogNames` (3); expected file names in 4 tests updated |
| 22 | G17: retry button only after reload | accepted | the button is always in the page, shown by `x-show="failedCount > 0 …"`; the count is fetched from `/progress` on `complete`, `stopped` and `error` | `test_web.py::TestTemplatesHandleErrors::test_retry_button_reactive`; `TestFailedInPages::test_retry_button_hidden_without_failed` (changed: the label is now in the page, hidden) |
| 23 | G15, O4: mutation gate incomplete | accepted | `src/__init__.py` (cli group) and `src/web/blueprints/projects.py` (web group) added, with the new test classes; full runs recorded in section 10 | — |
| 24 | third reviewer: edit/approve keep the failure log | accepted | the edit and approve endpoints remove the segment's failure log (`translator.remove_failure_log`) | `test_web.py::TestEditRemovesFailureLog` (2) |
| 25 | third reviewer: calls spent on edited queued segments | accepted | before the call, a worker skips a segment whose revision changed since queueing, or which became REVIEWED after it was queued (a segment queued while REVIEWED, by explicit re-translation, is still translated); the skip counts in progress, no event | `TestQueuedEdits::test_queued_segment_edited_before_pickup` (asserts one call), `test_queued_reviewed_segment_is_translated_when_asked` |

Rejected by the orchestrator and not done: signal handlers, timing assertions, cleanup of
temporary files left by dead processes, and anything from later phases (cancel wiring for
stop/pause, adaptive concurrency, output validation).

Beyond the list: `ProjectData.model` defaults to `''` (filled from the backend on create and
load); `mark_translated` and `set_model` take the mutex too; the glossary extraction docstring
lists the stop.

### 9. Review round 1: each fix fails without it

`scripts/verify/phase1_reverts.py` puts each old behaviour back, one at a time, into a copy of
the committed code (`git archive HEAD` in a temporary directory, removed afterwards) and runs
the tests that guard it; every revert must make one of them fail. Run on the commit with the
fixes and their tests (before the mutation-survivor tests):

```
$ python3 scripts/verify/phase1_reverts.py
OK   1 mutex per manager, not per project: 2 failed, 1 passed
OK   1 temp file left after a failed save: 1 failed, 1 passed
OK   2 re-translate applies to its stale snapshot: 3 failed, 3 passed
OK   2 stopped task still ending does not own the state: 1 failed
OK   3 apply_outcome check and write not atomic: 2 failed, 1 passed
OK   3 mark_reviewed outside the mutex: 1 failed
OK   4 timed-out join treated as an end: 1 failed, 1 passed
OK   4 starts not serialized: 1 failed
OK   5 logging set up outside the try: 1 failed
OK   5 coordinator start failure keeps the lock: 1 failed
OK   5 lock descriptor leaked after flock: 1 failed
OK   6 web coordinator failure does not join workers: 1 failed, 1 passed
OK   6 web coordinator failure does not wake paused workers: 1 failed
OK   6 CLI supervisor failure does not join workers: 2 failed
OK   7 late run error after stop pauses the task: 2 failed
OK   8 KeyboardInterrupt leaves the child running: 1 failed
OK   8 unbounded wait after the kill: 1 failed
OK   8 no exit hook: 1 failed
OK   9 failure log write error propagates: 2 failed, 6 passed
OK   9 web worker dies on an unexpected exception: 1 failed
OK   9 CLI worker dies on an unexpected exception: 2 failed, 1 passed
OK   10 no cancel check before the start: 1 failed
OK   10 wait not bounded by the deadline: 1 failed
OK   10 output of a cancelled call accepted: 1 failed
OK   11 review save ignores a refused save: 1 failed, 3 passed
OK   11 model select keeps a refused model: 1 failed, 3 passed
OK   22 retry button rendered by the server only: 1 failed, 3 passed
OK   12 model list from the Claude module: 2 failed, 3 passed
OK   12 engine default model hard-coded: 1 failed
OK   12 auth hint hard-coded in the page: 1 failed
OK   13 issue detail unbounded: 1 failed, 1 passed
OK   13 messages in the failure log unbounded: 1 failed, 1 passed
OK   13 stderr quoted whole in the Claude error: 1 failed
OK   14 timeout drops the output: 1 failed, 2 passed
OK   15 missing program is a per-segment error: 3 failed
OK   16 dropped result sends a success event: 1 failed
OK   17 revision taken when the worker picks the segment: 2 failed
OK   17 edit keeps FAILED and issues: 3 failed, 2 passed
OK   18 delete ignores a running task: 1 failed
OK   18 delete ignores the lock of another process: 1 failed
OK   19 extraction stop drops earlier terms: 3 failed, 1 passed
OK   19 CLI extraction stop not caught: 1 failed
OK   19 long successful replies searched for error phrases: 3 failed, 3 passed
OK   19 short successful replies not searched: 3 failed, 16 passed
OK   20 no round trip in the check: 1 failed, 2 passed
OK   21 file name without digest: 3 failed
OK   24 edit leaves the failure log: 1 failed, 1 passed
OK   24 approve leaves the failure log: 1 failed, 1 passed
OK   25 call spent on a segment edited after queueing: 2 failed
OK   25 explicitly queued reviewed segment skipped: 1 failed
reverts not detected: 0
```

(Durations and warning counts trimmed. The 11/22 rows were run separately, after the
others; the templates are checked as rendered text only. Item 23 is the mutation run itself.)

### 10. Mutation testing after review round 1

`scripts/verify/mutation.sh` with `src/__init__.py` (cli group) and
`src/web/blueprints/projects.py` (web group) added, and the round 1 web test classes in the
web group's `-k` (three slow pre-existing classes that cover no changed line —
`StoppedStateSSE`, `DynamicWorkers`, `MidTranslationModelSwitch` — are not in it). Run to
completion on the code of the commit "chore: add the revert check for the review round 1
fixes" (all review round 1 source changes; no source file changed afterwards), about 3 h
15 min, most of it the web group. Afterwards `git status` was clean and no
`.bak` file was left.

```
$ scripts/verify/mutation.sh
...
TOTAL mutants 847  killed 733  survived 104  timeout 6  suspicious 4  untested 0
$ echo $?
1
```

| group | survived | timeout | suspicious |
|---|---|---|---|
| core | 27 | 0 | 0 |
| llm | 18 | 0 | 4 |
| web | 43 | 4 | 0 |
| cli | 11 | 2 | 0 |
| scripts | 5 | 0 | 0 |

The 6 timeouts (web 4, 70, 72, 146; cli 72, 77: a run-error event name, the coordinator's and
the CLI supervisor's exit conditions) and the 4 suspicious mutants (llm 27, 34, 39, 41:
`_kill_live_groups` on `None`, `start_new_session=False`, `wait = None`, the cancel check
inverted) are detected: the tests fail or never finish.

Every one of the 104 survivors, by mutmut id (`<group> <id>`, line in the run's code):

**Killed by a new test (77).** The tests are in the commit "test: kill the surviving mutants
of the review round 1 code" (`TestRound1Exact` in `test_project.py`, `test_translator.py`,
`test_llm.py`, `test_web.py`; `TestCLIRound1Exact`; `TestLoadCheckRoundTripExact`;
`TestExtractionStopKeepsTerms::test_batches_joined_with_blank_line`; the two kill-wait tests
and `test_output_kept_is_bounded` in `test_llm.py` now use the real constants and check the
given-up pipes are closed). `scripts/verify/phase1_survivor_kills.py` applies each of these
mutants by hand to a copy of the committed code and checks a test fails: all 77 killed (73 in one run, the last 4 added and run afterwards; output ends `not killed 0`).

| mutants | what they changed | killing test |
|---|---|---|
| core 18, 19 | `ProjectData.model` default `''` | `test_project.py::TestRound1Exact::test_project_data_model_default_is_empty` |
| core 35, 58 | "No project data to save." / "No project loaded." | `…::test_messages_without_data` |
| core 46, 47 | temporary file prefix / suffix | `…::test_temporary_file_name` |
| core 49, 52 | file mode kept / 0644 | `…::test_file_mode_kept_or_0644` |
| core 62, 63 | `mark_translated` time | `…::test_mark_translated_sets_time` |
| core 89 | `mark_reviewed` text | `…::test_mark_reviewed_with_text` |
| core 211, 248 | log lines for failure-log write / removal errors | `test_translator.py::TestRound1Exact::test_log_messages_exact` |
| core 214–218 | `unexpected_error_outcome` detail, kind, code, blocking | `…::test_unexpected_error_outcome` |
| core 229 | `makedirs(exist_ok=False)` | `…::test_second_failure_replaces_the_log` |
| core 263, 264 | batch separator | `test_glossary.py::TestExtractionStopKeepsTerms::test_batches_joined_with_blank_line` |
| llm 92, 117 | short-reply limit 300 and `<=` | `test_llm.py::TestRound1Exact::test_short_reply_boundary` |
| llm 17 | `KILL_WAIT_SECONDS = None` | `TestRunProcessExceptionalExit::test_kill_wait_is_bounded_when_a_descendant_keeps_the_pipes` (real constant) |
| llm 24, 25 | given-up pipes not closed; `''` output after giving up | same test (`timeout` case: output `('', '')`, pipes closed) |
| llm 18, 19 | `KEPT_OUTPUT_CHARS` | `TestTimeoutKeepsOutput::test_output_kept_is_bounded` (real constant) |
| llm 44, 50 | "cancelled" messages | `TestRound1Exact::test_cancel_message_while_running`, `test_cancel_message_after_finish` |
| llm 130, 131 | fake backend auth hint | `TestRound1Exact::test_fake_auth_hint` |
| web 3 | `auth_error` event name | `test_web.py::TestRound1Exact::test_auth_error_event` |
| web 14 | `threads_alive` `or` → `and` | `…::test_threads_alive_with_any_worker` |
| web 22, 29–32 | states that own the project / `task_active` | `…::test_task_owns_state_by_state_alone` |
| web 37, 50, 56 | "仍在結束中" message; task registered when nothing to do; coordinator a daemon | `…::test_exact_messages_and_registration` |
| web 86–88, 93 | failure report text, log line, event key | `…::test_failure_report_exact`, `test_failure_report_lists_several_ids` |
| web 97 | skip log line | `…::test_skip_logged` |
| web 142 | `continue` after a skip | `…::test_worker_goes_on_after_a_skip` |
| web 144 | unexpected-error log line | `…::test_unexpected_exception_logged` |
| web 155, 160 | extraction `stopped` default; stop message | `…::test_extraction_status_exact` |
| web 162, 163 | delete 409 body | `…::test_delete_refused_message_exact` |
| web 171–176, 180–182 | edit/approve bodies; 404 body and code | `…::test_review_responses_exact` |
| web 177 | `==` → `!=` finding the segment | `…::test_retranslate_sends_its_own_segment`, `test_review_responses_exact` |
| web 204, 205 | 503 log line and body | `…::test_backend_unavailable_retranslate_exact` |
| cli 7, 8 | invalid-model message | `test_cli.py::TestCLIRound1Exact::test_invalid_model_message` |
| cli 11 | `--model` help | `…::test_model_help` |
| cli 56 | workers not daemons | `…::test_workers_are_daemons` |
| cli 75 | `continue` → `break` on an empty wait (results after 0.5 s lost) | `…::test_slow_result_still_applied` |
| cli 78, 80, 82 | supervisor-failure lines | `…::test_supervisor_failure_output` |
| cli 90 | extraction stop line | `…::test_extraction_stop_line` |
| scripts 14, 22, 20 | detail of `equal` / `model added`; `and` → `or` | `test_verify_scripts.py::TestLoadCheckRoundTripExact` (2) |

**Equivalent (18).**

| mutant | mutation | condition |
|---|---|---|
| core 16, web 8 | `X \| None` → `X & None` in an annotation | annotations are strings (`from __future__ import annotations`), never evaluated |
| core 124, 145 | `self._held = False` → `None` | only its truth value is used |
| core 135 | `os.read(fd, 32)` → `33` | a PID has at most 32 digits |
| core 138 | `ftruncate(fd, 0)` → `1` | the PID written next at offset 0 overwrites the kept byte |
| core 203 | `glossary_text = ''` → `None` | only its truth value is used |
| llm 26 | `stdout = stderr = ''` → `None` (after giving up on the pipes) | the value goes through `_text()`, which maps `None` to `''` |
| web 10 | `queued` field `repr=False` → `True` | the task's `repr` is never used |
| web 60 | `project_lock is None or …` → `is not None or …` in `start_translation`'s cleanup | in that branch the coordinator is never alive (nothing after `coordinator.start()` can raise), so both conditions are true |
| web 63, 64 | `task.state = 'failed'` → other value, same branch | the task is not registered yet and is unreachable after the exception |
| web 65 | `handler = None` → `""` | `teardown_project_logging` tests only its truth value |
| web 147 | `break` → `continue` after a run error with stop set | the loop's first statement breaks on the set stop event; nothing runs in between |
| web 185 | `copy.deepcopy(segments)` → `copy.copy` | a segment's source fields (text, html, type, chapter) are never modified, and those are all the call reads |
| scripts 1 | `sys.path.insert(0, …)` → `insert(1, …)` | as Phase 0's survivor 113 (default interpreter path settings) |
| scripts 15 | `'model' not in on_disk` → `'XXmodelXX' not in on_disk` | when `on_disk` has `model`, the dict without `model` can never equal it, so the first condition never decides |

**Performance-only (9)** — only how soon something is noticed, never a result:

| mutant | mutation | effect |
|---|---|---|
| llm 14 | `POLL_SECONDS = 0.1` → `1.1` | cancel noticed up to 1.1 s later; each wait is also bounded by the deadline and late or cancelled output is refused |
| llm 37, 38 | the wait's lower bound `0.0` → `1.0`; `deadline - now` → `deadline + now` | waits up to one poll (0.1 s) past the deadline; the output is still refused by the check after the process ends |
| llm 45 | `>= deadline` → `> deadline` in the poll loop | at exact equality, one more zero-length wait |
| web 75, 77 | post-batch exit check disabled | the coordinator ends through its 1 s queue-timeout path instead |
| cli 74 | `get(timeout=0.5)` → `1.5` | the supervisor polls less often |
| cli 76 | batch drain `while True` → `while False` | results are applied one per loop round, with a save each, instead of in a batch |

No timing assertion was added for any of them.

Corrected in review round 2 (G9, G14): llm 51 is not equivalent (at a clock reading equal to
the deadline `>` accepts the reply and `>=` refuses it); it is killed by
`test_llm.py::TestRound2DeadlineBoundary::test_finished_exactly_at_deadline` (mocked clock).
llm 16 is not performance-only (output written by an escaped descendant between 2 and 3 s
reaches the diagnostics or not); it is killed by
`TestRound2PartialOutputAfterKill::test_captured_output_kept` (mocked process: the post-kill
wait is 2 s). Both kills confirmed with `phase1_survivor_kills.py llm16 llm51` (section 12).
So the counts above become 78 killed by a new test, 17 equivalent, 8 performance-only.
Also corrected (third reviewer): the web group of `mutation.sh` did not select
`TestRound1Exact`, so the 33 web kills in the table were shown by `phase1_survivor_kills.py`
only, not by the single-command run; the group now selects it (section 13).

### 11. Review round 2

Static reviews by G, O and a third reviewer (items 16–21, added by the orchestrator after
checking them against the code). Every item got a test that fails on the code before the
fix; `scripts/verify/phase1_reverts.py` now also puts back each round 2 behaviour (rows
`R2-…`, section 12). No finding was rejected as not reproducible.

| # | finding | ruling | what was done | tests |
|---|---|---|---|---|
| 1 | G1, O5: a web transaction excludes other threads but not other processes (load, edit, save of two processes: the second save drops the first edit) | accepted | `TaskManager.transaction()` holds the project mutex and then the `ProjectLock` from before the load until after the save (ProjectLockedError → 409 as before). `load()` stays lock-free. Re-translate is now refused (409) before the model call when another process holds the lock | `test_web.py::TestRound2TransactionAcrossProcesses` (3: a second process's save inside a transaction is refused; entering is refused; re-translate makes no call) |
| 2 | G2, O4: delete checks for a running task outside the mutex under which a start registers it | accepted | the `task_active` check is inside `with project_mutex(project_dir)`, together with the lock and the removal | `TestRound2DeleteGuardInsideMutex::test_task_registered_while_delete_waits` (a task registered while the delete waits for the mutex → 409, project kept) |
| 3 | G3: parallel CLI starts its workers outside the supervisor's protected block | accepted | the start loop is inside the `try`; `threads` lists only started workers, which the `except` joins | `test_cli.py::TestRound2WorkerStartup` (`RuntimeError`, `KeyboardInterrupt` from the second `Thread.start()`) |
| 4 | G4, O2: every `OSError` at start is `BackendUnavailableError` | accepted | only `FileNotFoundError`, `PermissionError` and `ENOEXEC` are; any other start error (e.g. `E2BIG`) is a plain `LLMError`, not retried: the segment is FAILED (code `error`) and the run goes on | `test_llm.py::TestRound2StartErrors` (6), `test_translator.py::TestRound2StartErrorFailsSegment` |
| 5 | G5, O1: an unexpected exception aborts the sequential CLI run | accepted | `translate_segment` turns any exception other than the run-level ones into the `unexpected` FAILED outcome (logged); the now unreachable handlers in the web and CLI workers were removed | `test_translator.py::TestRound2UnexpectedErrorInEntryPoint` (6, incl. undecodable output), `test_cli.py::TestCLIUnexpectedErrors::test_unexpected_worker_exception[1]`, `[3]` (parametrized); `TestCLILock::test_lock_released_on_error[1]` now expects FAILED |
| 6 | G6: the group is not killed when the parent has already exited | accepted | the `except BaseException` of `run_process` kills the group whatever the parent's return code (once: the in-loop timeout path no longer kills, it raises and the `except` adds the output); the group leaves exit tracking only afterwards (`finally`) | `test_llm.py::TestRound2GroupKilledAfterParentExit` (the parent exits, its child with redirected pipes is killed after a cancel noticed late) |
| 7 | G7: the exit hook takes one snapshot and does not stop new starts | accepted | `_kill_live_groups` sets `_shutting_down` under `_live_groups_guard`; `Popen` and the registration run under the same guard, and after the flag a call raises `CancelledError('shutting down')` (run-level, not retried) | `TestRound2ExitHook` (3) |
| 8 | G8, O7: the survivor-kill script counts any non-zero exit as a kill | accepted | see section 12: named tests, baseline on the unmutated copy, kill only on exit 1 with failed tests, `try/finally` removal, no shell; `phase1_reverts.py` without shell too | the scripts' runs (section 12) |
| 9 | G9: output captured before giving up on held pipes is dropped | accepted | `_kill_group` keeps `TimeoutExpired.output/stderr` (bytes even in text mode), decoded with replacement, bounded by `KEPT_OUTPUT_CHARS` | `TestRound2PartialOutputAfterKill` (4, mocks only); `test_kill_wait_is_bounded_when_a_descendant_keeps_the_pipes[timeout]` now expects `('partial\n', '')` |
| 10 | G10, O6: the failure log is removed inside the model call path, before acceptance | accepted, with 17 | see 17 | see 17 |
| 11 | G11: a run-level pause before the page connects is unexplained | accepted | the worker that pauses the task stores the message in `task.error` (cleared on resume); the status endpoint already returns `error` and the page already shows it on load (`fetchStatus`) | `test_web.py::TestRound2RunErrorVisible` (3) |
| 12 | G12: a stored model the backend does not list is not shown | accepted | `fetchModel` prepends the stored model as an option labelled "目前設定，後端未列出" when the list lacks it; nothing is saved until another model is picked | `TestRound2StoredModelOption` (2, rendered text) |
| 13 | G13: the failure-log name can exceed 255 bytes | accepted | the readable prefix keeps only `[A-Za-z0-9_.-]` | `test_translator.py::TestRound2FailureLogNameBytes` (3) |
| 14 | G14: survivor llm 51 (`>` → `>=` after the process ended) is not equivalent | accepted | mocked-clock test: a call that ends at a reading exactly equal to the deadline is accepted, one after it is refused | `test_llm.py::TestRound2DeadlineBoundary` (2); llm 51 now killed (section 10, corrected) |
| 15 | O3: an exit-0 reply that echoes a phrase of the request is taken for an auth/limit error | accepted | `classify_error(result, prompt)`: on exit 0 a match whose text (case-insensitive) occurs in the request is ignored; `CLIBackend.complete` passes the flattened prompt | `test_llm.py::TestRound2RequestEcho` (6), `test_translator.py::TestRound2CodeEchoingAnErrorPhrase` (a code segment through `translate_segment` ends TRANSLATED) |
| 16 | third reviewer: start takes the file lock before the mutex; a start for a missing project creates or fails on `project.lock` | accepted | `_start_locked` takes the mutex, checks `project.json` exists (else `FileNotFoundError` → 404 "專案不存在", no lock file), then the lock | `TestRound2StartChecks` (3) |
| 17 | third reviewer (extends 10): a stale FAILED outcome leaves a log; edit/approve remove the log outside the transaction | accepted | a failure log is written or removed only where an outcome is accepted and saved or an edit is saved: `translate_segment` writes no file (the outcome carries the error, `failure`, not compared or shown); `record_failure_log` is called by the web coordinator (apply, save and logs under the mutex), the CLI after each save, re-translate inside its second transaction; edit and approve remove inside their transaction | `test_web.py::TestRound2FailureLogAtAcceptance` (7: stale success keeps a newer failure's log; stale failure writes none; accepted outcomes write and remove; refused save keeps the log; stale re-translation writes none; edit/approve remove under the mutex and lock), `test_translator.py::TestRound2EntryPointWritesNoFile` (4) |
| 18 | third reviewer: the web group of `mutation.sh` never selects `TestRound1Exact` | accepted | `Round1Exact or Round2` added to the web group's `-k`; every round 1 and round 2 class that targets web code is selected; the other groups run whole files | the run in section 13 |
| 19 | third reviewer: descriptor leaks in `save()` and `ProjectLock.acquire()` | accepted | `save()` hands the descriptor to `open()` first; `acquire()` closes it in a `finally` when reading the holder fails and decodes with replacement | `test_project.py::TestRound2DescriptorLeaks` (4) |
| 20 | third reviewer: `unsaved` lists skipped and stale results | accepted | `_apply_result` returns whether the outcome was accepted; only accepted results go to `unsaved`; on a coordinator failure, queued skipped results are not listed | `test_web.py::TestRound2UnsavedReport` (2) |
| 21 | third reviewer: the rate-limit message is cut from long stdout | accepted | quoted from stderr when the phrase is there, else from stdout | `test_llm.py::TestRound2RateLimitMessage` (2) |

Changed existing tests: the translator tests that passed `log_dir` to `translate_segment` call
it and then `record_failure_log` (helper `_translated`, what a caller does); three
`TestDiagnosticWriteFailure`/`TestRound1Exact` checks of the removal log line call
`remove_failure_log` directly; `test_atexit_hook_kills_live_groups` restores `_shutting_down`;
the three tests named in items 5 and 9 above; `TestRound1Exact::test_unexpected_exception_logged`
(web) expects the line from `src.core.translator`. No test was removed.

Beyond the list: none.

### 12. Review round 2: evidence scripts

Both scripts were changed (item 8): they take the copy with `git archive HEAD` piped to
`tar` through argument lists (no shell), run pytest without bytecode, and remove the copy in
a `finally`. `phase1_survivor_kills.py` runs the tests the table of section 10 names (node
ids), requires them to pass on the unmutated copy, counts a kill only on pytest exit 1 with
"failed" in the summary, and restores the copy after each mutant. Round 1 entries whose code
round 2 changed follow the new text (llm 44, web 171–176); llm 25 and web 144 are dropped,
their lines no longer exist (the empty output after giving up on the pipes; the worker's own
unexpected-error handler, now in `translate_segment`).

Run on the commit "fix: failure logs at acceptance, transactions under the file lock (review
round 2)" (all round 2 source and test changes):

```
$ python3 scripts/verify/phase1_survivor_kills.py
KILLED core18 1 failed …                  (75 lines, all KILLED)
not killed 0
$ python3 scripts/verify/phase1_survivor_kills.py llm16 llm51     # after the mutation run
KILLED llm16 1 failed
KILLED llm51 1 failed
not killed 0
```

`phase1_reverts.py`: the round 1 reverts of items 5, 8, 9 and 24 now put the old behaviour
back at the place round 2 moved the code to; round 2 reverts added. All 74 detected (50
round 1 rows, all OK, not repeated here; round 2 rows below; durations and warning counts
trimmed):

```
$ python3 scripts/verify/phase1_reverts.py
…
OK   R2-1 transaction without the file lock: 3 failed
OK   R2-2 delete checks the task outside the mutex: 1 failed
OK   R2-3 CLI workers started outside the protected block: 2 failed
OK   R2-4 every start error stops the run: 4 failed, 3 passed
OK   R2-5 unexpected exception escapes the entry point: 5 failed, 3 passed
OK   R2-6 group left alone when the parent has exited: 1 failed
OK   R2-7 start not refused after the exit hook: 2 failed, 1 passed
OK   R2-7 exit hook sets no flag: 3 failed
OK   R2-9 output dropped when the pipes are given up: 3 failed, 3 passed
OK   R2-10 web success removes the log before acceptance: 1 failed
OK   R2-10 re-translate records the log before acceptance and save: 2 failed
OK   R2-17 web failure log written before acceptance: 1 failed
OK   R2-17 edit removes the log outside the transaction: 1 failed, 1 passed
OK   R2-11 run-level error not kept on the task: 2 failed, 1 passed
OK   R2-11 resume keeps the old error: 1 failed
OK   R2-12 stored model not shown: 1 failed, 1 passed
OK   R2-13 Unicode in the file name: 1 failed, 2 passed
OK   R2-15 echoed phrase taken for an error: 4 failed, 3 passed
OK   R2-16 no project check before the lock: 1 failed, 2 passed
OK   R2-16 file lock before the mutex: 1 failed
OK   R2-19 temporary descriptor leaked by save: 1 failed, 3 passed
OK   R2-19 lock descriptor leaked when the holder cannot be read: 2 failed, 2 passed
OK   R2-20 stale and skipped results reported as unsaved: 2 failed
OK   R2-21 rate-limit message cut from long stdout: 2 failed
reverts not detected: 0
```

Item 14 is a mutant kill (section 10, corrected); item 18 is the mutation run itself.

### 13. Mutation testing after review round 2

Only this round's changes: `scripts/verify/mutation.sh <BASE>` with BASE the commit "docs:
record review round 1 of Phase 1 in the acceptance log" (passed on the command line), on the
code of the commit "chore: harden the evidence scripts and select the exact-value web tests"
(no source file changed afterwards). The web group now selects `TestRound1Exact` and the
`TestRound2…` classes. Afterwards `git status` was clean and no `.bak` file was left.

```
$ scripts/verify/mutation.sh <BASE>
...
== scripts: no changed lines
TOTAL mutants 93  killed 81  survived 11  timeout 0  suspicious 1  untested 0
$ echo $?
1
```

| group | survived | timeout | suspicious |
|---|---|---|---|
| core | 3 | 0 | 0 |
| llm | 4 | 0 | 1 |
| web | 4 | 0 | 0 |
| cli | 0 | 0 | 0 |
| scripts | no changed lines | | |

The suspicious mutant (llm 10, `start_new_session=False`) is detected: the tests fail or
never finish, as llm 34 in section 10.

Every survivor is equivalent; no new test was added for them:

| mutant | mutation | condition |
|---|---|---|
| core 8 | `os.read(fd, 32)` → `33` (moved into a `try`) | as core 135 of section 10: a PID has at most 32 digits |
| core 11 | `TranslationError \| None` → `& None` in an annotation | annotations are strings, never evaluated |
| core 16 | `glossary_text = ''` → `None` (moved into the `try`) | as core 203: only its truth value is used |
| llm 2 | `_shutting_down = False` → `None` | only its truth value is used |
| llm 23 | default `prompt=''` → `'XXXX'` in `CLIBackend.classify_error` | the base method only raises `NotImplementedError` |
| llm 24 | default `prompt=''` → `'XXXX'` in `ClaudeCLI.classify_error` | used only when no prompt is passed (`complete` always passes it); no error phrase is contained in `xxxx` |
| llm 27 | `echoed = … else ''` → `else 'XXXX'` (non-zero exit) | no error phrase is contained in `xxxx`, so every match still counts |
| web 3 | `FileNotFoundError('專案不存在')` → `'XX…XX'` | the only caller (the start endpoint) answers its own fixed message for this exception |
| web 4, 7, 8 | cleanup condition `is None` → `is not None`; `task.state = 'failed'` → other value | as web 60, 63, 64 of section 10 (the same lines, re-indented by item 16) |

### 14. Review round 3

Static reviews by G (changes requested), O (approved with minor findings) and a third reviewer
(approved with minor findings; its items 1 and 3 are G7 and G3, its items 4 and 5 became
items 11 and 12). Every accepted item got a test that fails on the code before the fix;
`scripts/verify/phase1_reverts.py` puts each round 3 behaviour back (rows `R3-…`, section 15).
No finding was rejected as not reproducible.

| # | finding | ruling | what was done | tests |
|---|---|---|---|---|
| 1 | G1: replacing a project of the same name overwrites `source.epub` before the lock is taken (a CLI holding the lock: request fails, files already changed) | accepted | `create_project` takes the project mutex, refuses (409) while a task of the project is active, takes the file lock (409 with the holder's PID), and only then copies the EPUB and creates the project; the lock is held until the project is saved | `test_web.py::TestRound3ReplaceProjectUnderLock` (4: other process → 409, files unchanged; active task → 409, files unchanged; mutex and lock held while the EPUB is copied; replacement written) |
| 2 | G2: a task ending `stopped` leaves the tab without Start | accepted | the Start button is shown for `stopped` too (label "已停止"); the `stopped` event sets the state, clears the current segment and closes the stream. The overlap guard is unchanged (409 while the old threads live) | `TestRound3StoppedTaskRestartable` (3: a start after a stopped task → 200; rendered button condition; the `stopped` handler) |
| 3 | G3, part of O4: the exit-0 echo rule also hides an error phrase on stderr | accepted | the echo rule applies to stdout only; a phrase on stderr is always classified | `test_llm.py::TestRound3StderrNotEchoSuppressed` (3) |
| 4 | G4, O2: `EAGAIN`/`ENOMEM` at process start fail the segment for good | accepted | `EAGAIN`, `ENOMEM`, `EMFILE`, `ENFILE` (`RESOURCE_ERRNOS`) → `TransientError`, retried like other transient failures (after 3 attempts the segment is FAILED `transient`); `E2BIG` stays a per-request `LLMError` | `TestRound3ResourceErrors` (5); `TestRound2StartErrors::test_other_start_failure_concerns_the_request` now covers `E2BIG` only |
| 5 | G5: a failing worker start joins unstarted threads and loses the error | accepted | `_threads` lists only started workers; a failed start subtracts the workers never started from `active_workers` and re-raises, so `_fail_task` records the original error | `test_web.py::TestRound3WorkerStartFailure` (the second worker's start fails: state `failed`, error kept, `active_workers` 0, lock free) |
| 6 | G6: the initiating page misses a run-level error that paused the task before it connected | accepted | the stream takes its cursor before the snapshot and the initial `status` event carries `task.error`; the page shows it on the first status | `TestRound3InitialStatusError` (3: error in the first event; an event sent after the snapshot is delivered; page handler) |
| 7 | G7, O3: an accepted `unexpected` failure writes no diagnostics and leaves an older log; undecodable output raises | accepted | `unexpected_error_outcome(model, error, prompt)` carries a failure record (error type and message, the request when it can be rebuilt from the call's arguments, the traceback as the attempt's raw output), so `record_failure_log` writes it and replaces an older log; the process is started with `encoding='utf-8', errors='replace'` | `test_translator.py::TestRound3UnexpectedFailureLog` (4), `test_llm.py::TestRound3UndecodableOutput`; `TestRound2EntryPointWritesNoFile::test_record_unexpected_failure_keeps_older_log` renamed `…_replaces_older_log` and inverted |
| 8 | G8: the failure-path drain reports stale results as unsaved | accepted | under the project mutex, a drained result is listed only when the segment's revision still equals the result's | `test_web.py::TestRound3StaleResultInFailureDrain` |
| 9 | O1: repeated unexpected failures never stop a run | accepted | `UnexpectedStreak` in `translator.py` counts `unexpected` failures in a row in the order the coordinator or CLI loop receives results; any other result (also a skipped one) resets it; at `MAX_CONSECUTIVE_UNEXPECTED` (5) the run stops: web: the coordinator saves the batch and fails the task with "連續 5 個段落發生未預期的錯誤，翻譯已停止（最後一個：…）" (state `failed`, `error` event, in-flight results reported as unsaved); CLI sequential and parallel: the message, then exit 1. Segments already FAILED stay FAILED; the rest stay pending | `test_translator.py::TestRound3UnexpectedStreak` (4), `test_web.py::TestRound3UnexpectedStreakWeb` (2), `test_cli.py::TestCLIRound3UnexpectedStreak` (3) |
| 10 | O5: an exhausted `FakeBackend` is swallowed by the catch-all | accepted | `FakeBackendExhausted(BaseException)`; each over-call is also recorded in `FakeBackend.overcalls`, which an autouse fixture asserts empty after every test (an exception in a worker thread only ends the thread). Whole suite run afterwards: no test newly failed through an over-call; only the two tests that exhaust the fake on purpose changed | `test_llm.py::TestRound3FakeOverCall` (2); `TestFakeBackend::test_scripted_replies_and_errors`, `TestInterfaceDetails::test_fake_identity` expect the new type |
| 11 | third reviewer, item 4: transaction and delete take the file lock in a directory without `project.json` (creates `project.lock`), review endpoints answer 500 | accepted | `transaction()` checks `project.json` inside the mutex before the lock (`FileNotFoundError('專案不存在')`); delete answers 404 for such a directory; a blueprint handler makes edit, approve and re-translate answer 404 "專案不存在", also when the project is deleted between re-translate's two transactions | `test_web.py::TestRound3MissingProject` (6) |
| 12 | third reviewer, item 5: output kept on a late finish and the timeout's raw output are unbounded | accepted | both sliced to `KEPT_OUTPUT_CHARS` | `test_llm.py::TestRound3TimeoutOutputBounded` (2) |
| — | O4 extra rule: on exit 0 also an error when the reply's first line starts with the matched phrase | rejected | recorded as a known limitation (below) | — |
| — | treating `MemoryError` specially (G4) | rejected | not done | — |
| — | third reviewer, item 2, scenario 1: re-translate persists FAILED for an unexpected exception | rejected | consistent with how every other failure is persisted; item 9 covers the batch case | — |
| — | third reviewer: match the echo rule against the segment's source text only | rejected | the backend receives one flattened prompt; covered by the known limitation | — |

Known limitation (from the rejected O4 rule): a request that itself contains a login or limit
phrase, combined with a real error reply on stdout with exit code 0, is stored as a
translation. Output validation (Phase 4) is to catch it.

Changed existing tests: the five named in items 4, 7 and 10 above;
`TestRound1Exact::test_unexpected_error_outcome` passes the new `prompt` argument;
`TestRound2UnsavedReport::test_skipped_result_in_queue_not_reported` gives its task a project
manager (the drain now reads revisions). No test was removed.

Beyond the list: the state label "已停止"; the initial `status` event is encoded with
`ensure_ascii=False` like the other events; deleting a directory without `project.json` now
answers 404 instead of removing it; the web group of `mutation.sh` selects `Round3`. The first
version of `TestRound3StaleResultInFailureDrain` could pass without the fix (both results in
one batch); it now waits for the coordinator failure before releasing the held call (found by
the revert check).

### 15. Review round 3: evidence scripts

Round 1 and 2 reverts whose code round 3 moved (rows 9, 10, 14, 18, 22, R2-1, R2-5, R2-16,
R2-20) follow it; `phase1_survivor_kills.py` entry web162 is re-anchored (a second 409
message with the same start now exists). Run on the commit "chore: re-anchor the evidence
scripts after round 3, list its mutant kills" (durations and warning counts trimmed):

```
$ python3 scripts/verify/phase1_reverts.py
…                                   (74 round 1 and 2 rows, all OK)
OK   R3-1 project replaced before the lock is taken: 3 failed, 1 passed
OK   R3-2 no Start for a stopped task: 1 failed, 2 passed
OK   R3-2 stopped event leaves state and stream: 1 failed, 2 passed
OK   R3-3 echo rule hides stderr: 2 failed, 1 passed
OK   R3-4 resource errors at start fail the segment: 5 failed
OK   R3-5 unstarted workers joined: 1 failed
OK   R3-6 initial status without the error: 1 failed, 2 passed
OK   R3-6 cursor taken after the snapshot: 1 failed
OK   R3-6 page ignores the initial error: 1 failed, 2 passed
OK   R3-7 strict decoding of the output: 2 failed, 3 passed
OK   R3-7 unexpected failure without diagnostics: 4 failed, 4 passed
OK   R3-7 request not in the unexpected-failure log: 1 failed, 3 passed
OK   R3-8 stale result in the failure drain reported as unsaved: 1 failed
OK   R3-9 web run goes on after five unexpected failures: 1 failed, 1 passed
OK   R3-9 sequential CLI goes on after five unexpected failures: 1 failed, 2 passed
OK   R3-9 parallel CLI goes on after five unexpected failures: 1 failed
OK   R3-9 streak not reset by another result: 5 failed, 4 passed
OK   R3-10 fake over-call is an ordinary exception: 2 failed, 2 errors
OK   R3-11 transaction takes the lock of a missing project: 4 failed, 2 passed
OK   R3-11 delete takes the lock of a directory without project.json: 1 failed, 5 passed
OK   R3-11 review endpoints answer 500 for a missing project: 4 failed, 2 passed
OK   R3-12 late finish keeps unbounded output: 1 failed, 1 passed
OK   R3-12 timeout raw not bounded: 1 failed, 1 passed
reverts not detected: 0
$ python3 scripts/verify/phase1_survivor_kills.py
KILLED core18 …                     (82 lines, all KILLED)
not killed 0
```

### 16. Mutation testing after review round 3

Only this round's changes: `scripts/verify/mutation.sh <BASE>` with BASE the commit "docs:
record review round 2 of Phase 1 in the acceptance log" (passed on the command line), on the
code of the commit "chore: revert checks for review round 3, select its web tests in the
mutation run" (the later commits change tests and the evidence scripts only). Afterwards `git
status` showed only two evidence scripts edited during the run (not in any mutated group; see
"Not verified") and no `.bak` file was left.

```
$ scripts/verify/mutation.sh <BASE>
...
== scripts: no changed lines
TOTAL mutants 94  killed 82  survived 10  timeout 1  suspicious 1  untested 0
$ echo $?
1
```

| group | survived | timeout | suspicious |
|---|---|---|---|
| core | 2 | 0 | 0 |
| llm | 1 | 0 | 0 |
| web | 7 | 1 | 0 |
| cli | 0 | 0 | 1 |
| scripts | no changed lines | | |

The timeout (web 3, `active_workers = None` before the workers start) and the suspicious
mutant (cli 5, the parallel CLI's streak result replaced by `None`: the test's held calls
wait their 10 s and the test fails) are detected.

| mutant | mutation | disposition |
|---|---|---|
| core 6 | `''.join(traceback…)` → `'XXXX'.join` | killed: `TestRound3UnexpectedFailureLog::test_log_has_type_message_traceback_and_request` |
| web 23 | `'source.epub'` → `'XXsource.epubXX'` (line moved under the lock) | killed: `TestRound3ReplaceProjectUnderLock::test_replacement_written` |
| web 26, 28 | metadata keys `'title'`, `'author'` | killed: same test |
| web 2 | `FileNotFoundError('專案不存在')` message in `transaction()` | killed: `TestRound3MissingProject::test_set_model_creates_no_lock_file` (set-model answers the message) |
| core 1 | `context = None` → `""` | equivalent: only its truth value is used, and it becomes a non-empty tuple once known |
| llm 7 | `searched = ''` → `'XXXX'` | equivalent: no error pattern matches `XXXX` |
| web 27, 29 | defaults `'未知書名'`, `'未知作者'` | equivalent: `EPUBExtractor.get_metadata()` always returns both keys (`'Unknown'` when absent), so the defaults are never used |
| web 8 | `stop = None` → `""` before the streak loop | equivalent: only its truth value is used, and `x or ""` is falsy exactly when `x or None` is |

The five kills were confirmed with `phase1_survivor_kills.py r3-core6 r3-web23 r3-web26
r3-web28 r3-web2` (all KILLED, `not killed 0`) and are in the full run of section 15.
No timing assertion was added.

### 17. Review round 4

Static reviews by G (changes requested), O (approved with minor findings) and a third reviewer
(approved with minor findings; its two findings became items 5 and 6). Every accepted item got
a test that fails on the code before the fix; `scripts/verify/phase1_reverts.py` puts each
round 4 behaviour back (rows `R4-…`, section 18). No finding was rejected as not reproducible.

| # | finding | ruling | what was done | tests |
|---|---|---|---|---|
| 1 | G1, O3: re-translate releases its transaction during the model call; a project replaced meanwhile (same-name upload) has the segment of the same id at revision 0 again, and the old result is stored | accepted | re-translate keeps the segment's source (`html`, `text`) from its first transaction; the second transaction applies the outcome only when the current segment exists and has the same source, otherwise 409 "專案已在翻譯期間被重新建立，未套用新譯文" and nothing is written | `test_web.py::TestRound4RetranslateAcrossReplacement` (the project is re-created through the upload API while the call is held; the new segment stays PENDING with no translation) |
| 2 | G3, O1: a worker added on resume is listed in `_threads` and counted in `active_workers` before `start()`; a failed start leaves a phantom count, the coordinator never ends and keeps the lock | accepted | the added worker is counted, started inside `try`, and listed only once started; a failed start undoes the count, sets `task.error` ("無法增加 worker：…") and `resume_translation` answers False (the endpoint answers 500 with the error and state `paused`). The task stays paused with the workers it has and can be resumed or stopped | `TestRound4ResumeWorkerStartFailure` (the second added worker fails: resume False, state `paused`, 2 counted and listed; a second resume completes the run, count 0, lock free) |
| 3 | O2: five unexpected failures in a row end a web run through `_fail_task`, which reports the results of calls still in flight as unsaved | accepted | the coordinator sets the stop event (and wakes paused workers) instead of raising; it keeps receiving, applying and saving results until the workers have ended, then fails the task with the streak message. In-flight results are saved as in a normal stop and as in the parallel CLI | `TestRound4StreakKeepsInFlightResults` (a sixth call finishing a good translation after the streak fired: stored TRANSLATED, `unsaved` empty, state `failed`) |
| 4 | O4: a same-name replacement overwrites `source.epub` before the new project is saved | accepted | the EPUB is copied to `source.epub.<random>.tmp` in the project directory and moved into place only after `pm.create()` saved; on any failure the temporary file is removed and the old `source.epub` and `project.json` stay | `TestRound4ReplacementFiles::test_failed_save_leaves_old_files`, `::test_new_epub_in_place_after_save` |
| 5 | third reviewer, 1: a stream opened after the task ended (`completed`, `failed`, `stopped`) gets the initial status and then waits 30 s; the page never closes it or refreshes the failed count | accepted | the stream returns right after the initial status when the state is terminal; on a first status with a terminal state the page clears the current segment, refreshes the failed count and closes the stream | `TestRound4StreamAfterEnd` (3 states: the body is the status event only and the stream never waits; page handler) |
| 6 | third reviewer, 2: the replacement creates the project directory before the mutex (a delete in between removes it: 500), and leaves the old book's `log/failed/` files | accepted | the directory is created under the mutex after the task check, before the file lock; after a successful replacement `log/failed/` is removed (other logs stay) | `TestRound4ReplacementFiles::test_directory_created_under_mutex`, `::test_old_failure_logs_removed` |
| — | G2: with instantaneous failures, workers can fail more than five segments before the coordinator counts the fifth | rejected | recorded as a known consequence (below): the safeguard stops spending model calls on a broken run; instantaneous failures spend none, and the segments are retried with retry-failed | — |

Changed existing tests: `TestRound3UnexpectedStreakWeb::test_run_stops_after_five` (the sixth,
in-flight result is now saved: six segments FAILED, no unsaved suffix, `unsaved` empty);
`TestStoppedStateSSE::test_sse_terminates_on_stopped` sets the task's state to `stopped` together
with the event, as the coordinator does (it used to start the stream on a task already
`stopped`, which now ends after the status); `TestTemplatesHandleErrors::test_retry_button_reactive`
counts 4 calls of `refreshFailedCount()` (the ended first status is the fourth). No test was
removed.

### 18. Review round 4: evidence scripts

Reverts whose code round 4 moved (rows 2, R3-1 and R3-9 web) follow it;
`phase1_survivor_kills.py` entries r3-web26 and r3-web28 are re-anchored (the project creation
of a replacement is indented one level more). Run on the commit "chore: re-anchor the
re-translate revert after round 4" (durations and warning counts trimmed):

```
$ python3 scripts/verify/phase1_reverts.py
…                                   (97 round 1 to 3 rows, all OK)
OK   2 re-translate applies to its stale snapshot: 3 failed, 3 passed
OK   R3-1 project replaced before the lock is taken: 3 failed, 1 passed
OK   R3-9 web run goes on after five unexpected failures: 1 failed, 1 passed
OK   R4-1 re-translation applied to a replaced project: 1 failed
OK   R4-2 worker added on resume listed and counted before its start: 1 failed
OK   R4-3 streak stop discards in-flight results: 1 failed
OK   R4-4 source.epub written before the project is saved: 2 failed, 2 passed
OK   R4-5 stream of an ended task waits for events: 3 failed, 1 passed
OK   R4-5 page keeps the stream of an ended task: 1 failed, 3 passed
OK   R4-6 project directory created before the mutex: 1 failed, 3 passed
OK   R4-6 old failure logs kept after a replacement: 1 failed, 3 passed
reverts not detected: 0
$ python3 scripts/verify/phase1_survivor_kills.py
KILLED core18 …                     (82 lines, all KILLED)
not killed 0
```

The survivor-kill script was run on the commit "chore: re-anchor two survivor kills after
round 4 moved the replacement code". The revert of row 2 was found unanchored by a first full
run on that commit (`BAD … has the old text 0 times`, `reverts not detected: 1`) and fixed by
the next commit; the run above is after it. The load check, run afterwards on the unmutated
code (no mutation run in progress):

```
$ python3 scripts/verify/phase1_load_projects.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  segments 9521  pending 5333  translated 4188  reviewed 0  failed 0  round trip equal 7  model added 1  differs 0  failed projects 0
$ echo $?
0
```

### 19. Mutation testing after review round 4

Only this round's changes: `scripts/verify/mutation.sh <BASE>` with BASE the commit "docs:
record review round 3 of Phase 1 in the acceptance log" (passed on the command line), on the
code of the commit "chore: revert checks for review round 4, select its web tests in the
mutation run" (the web group's test selection now includes `Round4`; the later commits change
only the evidence scripts and this log). Afterwards `git status` was clean and no `.bak` file
was left; nothing was edited during the run.

```
$ scripts/verify/mutation.sh <BASE>
== core: no changed lines
== llm: no changed lines
== web: mutating changed lines of src/web/tasks.py,…,src/web/blueprints/translation.py
...
== cli: no changed lines
== scripts: no changed lines
TOTAL mutants 35  killed 33  survived 2  timeout 0  suspicious 0  untested 0
$ echo $?
1
```

| mutant | mutation | disposition |
|---|---|---|
| web 16, 18 | defaults `'未知書名'`, `'未知作者'` (the lines moved into the replacement's `try`) | equivalent, as round 3's web 27/29: `EPUBExtractor.get_metadata()` always returns both keys (`'Unknown'` when absent), so the defaults are never used |

No survivor needed a new test, so `phase1_survivor_kills.py` gets no new entry. No timing
assertion was added.

### 20. Review round 5 (final)

Static reviews by G, O and the third reviewer, all three approved with minor findings (the
third reviewer's two findings are items 2 and 3). Nothing was changed in
code in this round: the three minor findings below are accepted as known limitations, not
fixed. Five rounds is the limit; each needs an unlikely failure or a very narrow
interleaving, and none loses a saved translation silently on normal use.

| # | finding | ruling | what was done | tests |
|---|---|---|---|---|
| 1 | two reviewers: a same-name replacement (`create_project` in `src/web/blueprints/projects.py`) saves the new `project.json` before the new EPUB is moved into place with `os.replace`; if that final move fails with an I/O error, the request answers 500 and the project has the new segments with the old `source.epub`, the old translations already overwritten | accepted as a known limitation | not fixed (below) | — |
| 2 | all three reviewers: when five `unexpected` failures in a row stop a web run, the coordinator (`src/web/tasks.py`) drains the in-flight results while the state is still `running`, so a pause request is accepted; if it lands between a worker's stop check and its `pause_event.wait()`, that worker blocks and the project lock stays held until the user resumes or stops. The third reviewer adds: a resume with a higher worker count in that window can start workers on a stopping task | accepted as a known limitation | not fixed (below) | — |
| 3 | two reviewers: `_fail_task` (`src/web/tasks.py`) sets the state to `failed` before it waits for the workers, and sets `task.error` and emits the `error` event only after the wait; with long calls in flight, a stream can close (or a page load) showing `failed` without the message. The third reviewer adds: a stream opened right after the state changes can refresh the failed count before the in-flight results are saved | accepted as a known limitation | not fixed (below); the message is in the project log and appears on reload after the wait | — |

No mutation run was needed in this round (no code change). The test count is unchanged: 869.

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
  other in the same way: a web change (edit, approve, set model, re-translate) holds the
  lock from its load to its save (review round 2), and re-translate is refused before the
  model call while another process holds it.
- Starting a task for a directory without `project.json` answers 404 and creates no lock file.
- A run-level error that paused a web task stays in the task's `error` (status endpoint)
  until the task resumes.
- The CLI exits 1 after a run with failed segments (it used to exit 1 at the first failure).
- Term extraction now passes the project model (before: the CLI's default model) and stops
  with an error on a rate limit.
- The temporary file of `save()` is `project.json.<random>.tmp`, one per save, removed when the
  save fails (was `project.json.tmp`). One left by a process killed mid-save stays (not cleaned).
- Web: deleting a project while its task runs or another process holds its lock, and starting
  a task while the previous task's threads are still alive, answer 409.
- A project without a `model` key loads with the backend's default model (`sonnet` for the
  Claude CLI, as before); new projects get it too.
- A manual edit of a FAILED segment makes it TRANSLATED; edit and approve clear its issues and
  remove its failure log.
- The CLI's parallel workers are daemon threads; a second Ctrl-C ends the process without
  waiting for them, and the exit hook kills their model processes.
- A worker result for a segment edited mid-flight is dropped and counted as done in the
  progress of that run.
- The web coordinator ends only when all workers have exited *and* the result queue is empty.
  Before, it ended on a queue timeout as soon as no worker was active, so a result put just
  before the last worker exited could in principle be lost; it also had a separate final
  drain, now removed.
- Windows is not supported by the lock (`fcntl`).
- Review round 3: replacing a project through the web while a task of it is active or another
  process holds its lock answers 409 and changes none of its files; deleting a directory
  without `project.json` answers 404 (it used to be removed); edit, approve and re-translate
  of a missing project answer 404. A web task ending `stopped` can be started again from the
  translation tab. Five `unexpected` failures in a row end a web run as `failed` and a CLI run
  with exit 1; the segments already FAILED stay FAILED. A shortage of processes, memory or file
  descriptors at process start is retried, then fails the segment as `transient`.
- Review round 4: a re-translation whose project was re-created under the same name during
  the call answers 409 and changes nothing. A failed start of a worker added on resume leaves
  the task paused (resume answers 500 with "無法增加 worker：…"). When five `unexpected`
  failures in a row stop a web run, the calls in flight are awaited and their results saved
  before the task fails, so the run can take as long to end as its slowest in-flight call. A
  same-name replacement keeps the old `source.epub` and `project.json` when it fails, and
  removes the old book's `log/failed/` after it succeeds. A progress stream opened on a task
  that has already ended sends its status and ends.
- Known consequence (review round 4, rejected G2): the five-failure cutoff is best-effort. It
  is counted where the coordinator (or CLI loop) receives results, so when failures are
  immediate (no model call spent) the workers can mark more than five segments FAILED before
  the run stops. Those segments are retried with retry-failed.
- Known limitations (review round 5, not fixed): if the final `os.replace` of a same-name
  replacement fails, the request answers 500 and the project keeps the new segments with the
  old `source.epub` (the old translations are already overwritten). A pause request that lands
  between a worker's stop check and its `pause_event.wait()` while a five-failure stop drains
  in-flight results blocks that worker and keeps the project lock until the user resumes or
  stops; a resume with more workers in that window can start workers on the stopping task.
  `_fail_task` shows `failed` before the error message while it waits for long in-flight
  calls; a stream closed or a page loaded in that time shows no message (it is in the project
  log and appears on reload after the wait), and its failed count can miss the in-flight
  results not yet saved.
- Known limitation (review round 3): a request that itself contains a login or limit phrase,
  combined with a real error reply on stdout with exit code 0, is stored as a translation;
  output validation in Phase 4 is to catch it.

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
- Review round 1: the reviewers' scenarios were reproduced with the fake backend and short
  Python child processes, not with the real CLI. No real `claude` call was made in this round
  (the one real call of section 7 predates it). The short-reply rule for exit-code-0 errors
  (item 19) is a judgement from `main`'s behaviour, not from a captured real limit or
  expired-login reply.
- The template changes (items 11, 12, 22) were checked as rendered text, not in a browser.
- `KeyboardInterrupt` in the parallel CLI was tested by raising it from `save()`, not by a real
  Ctrl-C at a terminal; the exit hook was tested by ending a child interpreter normally.
- Concurrency tests that need an edit to land between a check and a write use a 0.3–0.5 s
  wait for the other thread; on a heavily loaded machine the "fails without the fix" side of
  those tests could pass by accident (the tests never fail on the fixed code because of it).
  Review round 2 adds one more such wait (`TestRound2DeleteGuardInsideMutex`, 0.5 s for the
  delete request to reach the mutex).
- Review round 2: no real `claude` call; `E2BIG`, `ENOEXEC`, undecodable output and the
  exit-0 echo were produced with stubs of `run_process`, the partial output after the kill
  and the deadline boundary with mocked processes and clocks. The cross-process transaction
  was tested with a second Python process saving through `ProjectManager`, not with two web
  servers. The model-selector option (item 12) was checked as rendered text, not in a
  browser. The exit hook's shutdown flag was tested by calling the hook directly.
- Review round 3: no real `claude` call. Resource errors at start were raised from a stub of
  `run_process`, not produced by exhausting the system; undecodable output came from a short
  Python child process. The template changes (Start for `stopped`, the `stopped` handler, the
  initial error) were checked as rendered text, not in a browser. The worker-start failure was
  made by patching `Thread.start`. During the mutation run (section 16) the two evidence
  scripts were edited (re-anchored; neither is in a mutated group) and the load check was run
  once while files were mutated in place; it was run again afterwards on the unmutated code
  (same TOTAL line as section 5:
  `TOTAL projects 8  segments 9521  pending 5333  translated 4188  reviewed 0  failed 0  round trip equal 7  model added 1  differs 0  failed projects 0`, exit 0).
- Review round 4: no real `claude` call. The replacement during a re-translation was made
  through the upload API with a patched segmenter (one invented segment per book); the failed
  save by patching `ProjectManager.save`; the delete before the create's mutex by a wrapper of
  `project_mutex` that removes the directory; the failed worker start by patching
  `Thread.start`. The page change (closing the stream on an ended first status) was checked
  as rendered text, not in a browser. The load check was run after the mutation run, on
  unmutated code.

---

## Phase 2 — Document and block model

Date: 2026-11-20. Branch: `phase-2/block-model`, from `main` after Phase 1. No new runtime
dependency (ebooklib stays for the export only). Commands run in the worktree of the branch
with `source <repo>/venv/bin/activate` (Python 3.9.6 for the suite and the scripts, 3.12.7
for mutmut, as in Phases 0 and 1). The real projects are read from `<repo>/projects`; nothing
under it was written (see section 9).

### 1. What was built

| Item | Where | Main names |
|---|---|---|
| Raw package reader | `src/epub/extractor.py` | `read_package` (container.xml → OPF → manifest with URL-decoded hrefs relative to the OPF directory → spine of XHTML items, duplicates removed), `parse_xml` (`lxml.etree`, XML mode: no entity expansion, no network, no recovery), `EPUBExtractor` (same API; `Chapter.body` is the parsed `<body>` instead of `html_content`; `failed` lists chapters missing from the archive or not well-formed) |
| Block walker | `src/epub/blocks.py` | `walk_blocks`, `Block` (`index`, `tag`, `text`, `html`, `fingerprint`), `fingerprint` (first 16 hex digits of the SHA-256 of the text with all whitespace removed), `serialize` |
| Segmenter, schema 2 | `src/core/segmenter.py` | `Segmenter.segment_chapter(chapter_id, body)`, `segment_from_blocks`; `Segment.block_start/block_end/fingerprints` |
| `project.json` schema 2 | `src/core/project.py` | `ProjectData.schema`, `ProjectData.source_sha256`, `SegmentState.block_start/block_end/fingerprints`, `sha256_file` |
| Source copy and hash | `translate.py` (`init`), `src/web/blueprints/projects.py` | CLI `init` copies the book to `<project>/source.epub` and stores that path; both creation paths store `source_sha256`; both report unparsed chapters (CLI line, web `unparsed_chapters`) |
| Migration | `src/core/migrate.py`, `translate.py` | `migrate_project`, `plan_migration`, `MigrationReport`; `translate.py migrate <project> [--dry-run]` |
| Export bridge | `src/epub/builder.py` | `_place_by_blocks` (schema 2), `_place_by_count` (schema 1, the unchanged walk), `_soup_blocks` |
| Check scripts | `scripts/verify/` | `phase2_roundtrip.py`, `phase2_migrate_copies.py` |

How each part behaves, where the plan left room:

- **Blocks.** Exactly as plan 4.2 and the dry-run prototype: comments and processing
  instructions ignored; `div`/`section`/`article` descended into when a child is a block
  element or `<br>`; a block is a non-descended block child or a maximal inline run between
  block children and `<br>`; lists single blocks; blocks without text (whitespace only, an
  image, `<hr/>`) are dropped and get no index. Text excludes comments, processing
  instructions, `script` and `style`. Re-segmenting gives the same indexes and fingerprints
  (test below).
- **Segment HTML.** The blocks' serializations joined by a newline. A block element is
  serialized by lxml without its tail and **without the namespace declarations it only
  inherits** from its parent: lxml repeats every in-scope declaration (`xmlns`, `xmlns:epub`)
  on each serialized element, which would put them in every prompt. Declarations made on the
  element itself (an inline `<svg xmlns=…>`) are kept. A run is its text (escaped) and inline
  elements, comments left out, outer whitespace stripped.
- **Segmentation.** Headings, tables and code (a `<pre>`, or a run that is a single `<code>`
  element, as `main` treated a body-level `<code>`) are segments of their own; other blocks
  are grouped until a segment reaches 300 words, starting a new segment when the next block
  would pass 500. A block is never divided; the sentence splitting, the small-segment merge
  pass and the large-segment split pass are gone (with whole blocks the first pass already
  gives what they produced). Segment IDs keep the `<chapter>_segNNNN` form.
- **Schema.** New projects write `schema: 2`, `source_sha256`, and per segment `block_start`,
  `block_end`, `fingerprints`; `segmenter_version` is not written for schema 2. For a schema 1
  project none of these keys is written (they are left out when they hold their defaults),
  so a schema 1 project saves byte-for-byte in the same layout as before; Phase 0's
  `segmenter_version` rule is unchanged for it. `load()` only reads.
- **Migration.** `translate.py migrate <project>` (and `--dry-run`, which reads only and does
  not even take the lock). Source: the project's own `source.epub`, else the stored
  `epub_path`. Per chapter: the legacy segments' text (from the stored `html`) must equal the
  blocks' text, first without and then with the comments that are direct children of walked
  elements; legacy segments are grouped into the smallest groups that start and end on block
  boundaries (a segment without text joins the next group, or the last one at the end of the
  chapter); a group of several becomes one segment: first ID, translations joined in order
  with nothing between them, lowest status (pending < failed < translated < reviewed), issues
  concatenated, latest `translated_at`, highest `revision`, first non-empty model, no usage.
  A group that is not uniformly translated is listed in the report (status before and after;
  its partial translation is kept). Segment HTML, text and word count are replaced by those of
  the new blocks; the segment type is kept. A group that covers only comment text, a chapter
  not in the spine, missing, not well-formed, without body or without text makes the chapter
  unmatched. Fail-closed: any unmatched chapter → nothing written, report printed, exit 1. On
  success: the source is copied into the project if it was outside, `project.json` is copied
  to `project.schema1.json`, and the new file is written with `save()` (atomic, under the
  project lock, which the command takes first; a web task holds that lock, so migration is
  refused while one runs). The report gives chapters and segments matched, chapters matched
  with comment text, merge groups with their segment IDs, the notes, unmatched chapters with
  reasons, and the non-blocking count of translated text segments whose top-level node count
  differs from the new block HTML.
- **Export (item 7) — decision.** Schema 1 projects go through the old path unchanged
  (`_place_by_count` is the old loop; the golden tests `TestVersion1ExportMatchesMain` pass
  unchanged). For schema 2 the smallest bridge that keeps the existing builder: the builder
  still reads each chapter through ebooklib and BeautifulSoup; `_soup_blocks` repeats the
  block walk on that tree (about 25 lines), the chapter's blocks are checked against every
  segment's stored fingerprints, and each segment gets the text-bearing nodes of its block
  range; the existing apply functions then work as before. If any fingerprint differs or a
  range is out of bounds, the chapter is left in the original language. I chose this over
  walking the raw XML for export because the apply functions operate on BeautifulSoup nodes;
  placing on lxml nodes would be the Phase 3 writer. Cost: the walk rules exist twice until
  Phase 3 replaces the builder; the fingerprint check makes any disagreement visible as an
  untranslated chapter, never as a misplaced translation. On the real data the bridge accepts
  2155 of 2155 migrated chapters (section 4). Fidelity is otherwise today's (ebooklib
  round trip, heads lost, Bugs 1 and 2 unchanged). One difference: inline runs are placed node
  by node, so a run translation is placed in place only when the model returns one top-level
  node per run node (with today's prompt, usually one `<p>` per line of a `<br>`-separated
  text); otherwise the existing whole-segment fallback applies, as it does today for any count
  mismatch. (Changed in review rounds 1 and 2, sections 10 and 13: a chapter is placed only
  when it matches as a whole, and only segments of block elements are paired node by node.)

### 2. Test suite

```
$ python3 -m pytest tests/ -q -p no:cacheprovider -W ignore
1072 passed in 136.83s (0:02:16)
```

869 before the phase. New files `tests/test_blocks.py` (walker, schema 2 segments, raw
package reader, rules spelled out for mutation testing) and `tests/test_migrate.py`; new
classes in `test_builder.py` (`TestSchema2Export`), `test_project.py`
(`TestPhase2ProjectFields`), `test_cli.py` (`TestCLIPhase2`), `test_verify_scripts.py`
(`TestRoundtripCheck`, `TestMigrateCopiesCheck`, `TestPhase2ScriptSurvivors`). Raw-ZIP
fixtures (`write_raw_epub`, `xhtml`, `raw_epub`, `body_of` in `tests/conftest.py`) write the
archive with `zipfile`, invented text only. The `raw_epub` fixture has an `OEBPS/` folder,
chapters in `Text/`, a percent-encoded href (`Text/chapter%20two.xhtml`), a head with
`<link>` and `<style>`, NCX plus a navigation document that is in the spine, inline SVG with
`viewBox`, text directly under `<body>`, `<br>`-separated runs, a body comment and a
duplicated spine entry.

Acceptance item → tests:

| Item | Tests |
|---|---|
| bare text with `<br>` | `test_blocks.py::TestWalk::test_br_splits_runs`, `test_br_directly_under_body`, `TestSchema2Segments::test_bare_text_with_br`, `test_builder.py::TestSchema2Export::test_br_separated_runs[*]` |
| long bare text | `TestSchema2Segments::test_long_bare_text_is_one_block`, `test_long_bare_text_with_br_grouped_by_blocks` |
| body-level comment | `TestWalk::test_body_level_comment_ignored`, `test_comment_inside_run_not_text_nor_html`, `TestSchema2Segments::test_body_level_comment`, `TestSchema2Export::test_comment_between_blocks[*]` |
| chapter that fails to match: reported, nothing written | `test_migrate.py::TestMigrate::test_unmatched_chapter_writes_nothing`, `test_segment_of_only_comment_text_unmatched`, `test_chapter_problems_reported[*]`, `TestMigrateCLI::test_unmatched_exits_non_zero` |
| percent-encoded href, chapters in a subfolder | `TestRawPackage::test_subfolder_and_percent_encoded_href` |
| nav document in the spine, duplicated spine entry | `TestRawPackage::test_spine_order_nav_skipped_duplicates_removed`, `TestRules::test_spine_entries_after_skipped_ones_kept` |
| unparseable chapter reported, not segmented | `TestRawPackage::test_unparseable_and_missing_chapters_reported`, `test_cli.py::TestCLIInit::test_init_reports_unparsed_chapter`, `test_web.py::TestCreateProjectMarker::test_unparsed_chapter_reported` |
| determinism of indexes and fingerprints | `TestSchema2Segments::test_resegmenting_is_deterministic` |
| migration rules (html text, comments both ways, merge, lowest status, partial group, lock, backup, dry run, schema 2 refused) | `test_migrate.py::TestMigrate` (28 tests), `TestGroups`, `TestMigrateSurvivors` |
| schema 1 saves without new keys | `test_project.py::TestModelField::test_saving_schema_1_project_adds_no_keys`, `test_project_without_schema_loads_as_1_unchanged` |
| source copy and hash | `test_cli.py::TestCLIInit::test_init_creates_project_json`, `test_web.py::TestCreateProjectMarker::test_web_created_project_records_schema_2_and_source_hash` |
| export of schema 2 and of migrated projects | `TestSchema2Export` (19 tests incl. `test_raw_epub_end_to_end`, `test_mismatch_leaves_chapter_unchanged[*]`), `test_migrate.py::TestMigrate::test_migrated_project_exports` |

No test was removed. Tests whose expectations changed, and why:

- `test_segmenter.py`: the fixture now passes each HTML string through `body_of` (the
  segmenter takes a parsed body); all 46 other tests unchanged.
  `test_single_large_element_kept_by_split_step` called the removed
  `_split_large_segments`; replaced by `test_single_large_element_kept_whole` (same intent:
  one 800-word `<p>` stays one segment). `test_breaks_at_sentence_boundary` still passes
  (segments end at block boundaries).
- `test_project.py`: the four Phase 0 `segmenter_version` tests became schema tests with the
  same intent (`test_new_project_records_schema_2`,
  `test_project_without_schema_loads_as_1_unchanged`, `test_saving_schema_1_project_adds_no_keys`,
  `test_phase_0_project_keeps_segmenter_version_through_save`); new projects are schema 2,
  so a schema 1 file is produced by rewriting a created one.
- `test_cli.py::test_init_creates_project_json` and
  `test_web.py::test_web_created_project_records_segmenter_version_2` (renamed
  `…_schema_2_and_source_hash`): assert `schema: 2`, no `segmenter_version`, the hash, and
  for the CLI the project-owned copy.
- `test_extractor.py`: `test_load_valid_epub` checks the OPF instead of the ebooklib book;
  `test_chapter_has_html_structure` checks `body` instead of `html_content`; the comment
  pipeline test segments `body`.
- `test_builder.py`: fixtures segment `ch.body`; `TestVersion2Export` (Phase 0 projects,
  schema 1 with `segmenter_version 2`) reads the segments the Phase 0 extractor and
  segmenter stored from `tests/data/phase0_segments.json`, recorded once by running `main`'s
  `src/` on the test's own invented scenarios (that segmenter no longer exists); the
  expectations are unchanged. In `TestWalkByMarker` the schema 1 file is made by removing
  `schema`; "new project uses current walk" became "Phase 0 project uses current walk".

### 3. Check: every chapter re-serializes unchanged

```
$ python3 scripts/verify/phase2_roundtrip.py --projects-dir <repo>/projects --anonymize
PASS project-1: chapters 24, round trip equal 24, whole-document equal 0
PASS project-2: chapters 35, round trip equal 35, whole-document equal 35
PASS project-3: chapters 41, round trip equal 41, whole-document equal 41
PASS project-4: chapters 1764, round trip equal 1764, whole-document equal 1764
PASS project-5: chapters 36, round trip equal 36, whole-document equal 36
PASS project-6: chapters 109, round trip equal 109, whole-document equal 109
PASS project-7: chapters 71, round trip equal 71, whole-document equal 71
PASS project-8: chapters 90, round trip equal 90, whole-document equal 90
TOTAL projects 8  chapters 2170  round trip equal 2170  whole-document equal 2146  failed projects 0
$ echo $?
0
```

Two findings against the plan's wording:

- The 2170 are the XHTML items of the 8 manifests. Spine chapters (duplicates removed) are
  2169: project-3 has one XHTML item outside its spine. The script checks all 2170.
- "Re-serialized without edits" holds only when the root element is serialized in a document
  of its own (`copy.deepcopy(root)`). Serializing the parsed document itself (`tostring` of
  the tree) makes libxml2 use its XHTML 1.0 output mode for documents with an XHTML 1.0
  DOCTYPE and add `<meta http-equiv="Content-Type" content="text/html; charset=…"/>` to
  `<head>`: all 24 chapters of project-1 change that way. The Phase 3 writer must not
  serialize whole documents (or must remove what libxml2 adds). Comparison: C14N 2.0 with
  comments kept on both sides.

### 4. Check: migration of copies of the real projects

```
$ python3 scripts/verify/phase2_migrate_copies.py --projects-dir <repo>/projects --anonymize
PASS project-1: chapters 22/22, segments 220/220, merge groups 0 (0 segments), count differs 0, translated chars 211044 -> 211044, export places 22/22, web load ok
PASS project-2: chapters 33/33, segments 291/291, merge groups 13 (26 segments), count differs 16, translated chars 302113 -> 302113, export places 33/33, web load ok
PASS project-3: chapters 39/39, segments 4385/4385, merge groups 0 (0 segments), count differs 1, translated chars 15637 -> 15637, export places 39/39, web load ok
PASS project-4: chapters 1763/1763, segments 2236/2236, merge groups 0 (0 segments), count differs 32, translated chars 746052 -> 746052, export places 1763/1763, web load ok
PASS project-5: chapters 35/35, segments 840/840, merge groups 1 (2 segments), count differs 1, translated chars 462908 -> 462908, export places 35/35, web load ok
PASS project-6: chapters 108/108, segments 581/581, merge groups 0 (0 segments), count differs 0, translated chars 356677 -> 356677, export places 108/108, web load ok
PASS project-7: chapters 68/68, segments 637/637, merge groups 15 (31 segments), count differs 13, translated chars 471477 -> 471477, export places 68/68, web load ok
PASS project-8: chapters 87/87, segments 331/331, merge groups 0 (0 segments), count differs 16, translated chars 159747 -> 159747, export places 87/87, web load ok
TOTAL projects 8  chapters 2155/2155  segments 9521/9521  merge groups 29 (59 segments)  count differs 79  translated chars unchanged 8  export places 2155/2155  web loads 8  failed projects 0
$ echo $?
0
```

The script copies `project.json`, `source.epub` and `glossary.json` of each project into a
new `mktemp` directory under the system temporary directory (it refuses one inside the
repository), migrates the copies with `migrate_project` (the code of the `migrate`
command), and removes the directory at the end (checked: no `phase2-migrate-*` directory
left). Per project it checks: all chapters and segments matched and the copy written; the
characters of translated text unchanged (translations are joined without separators);
`EPUBBuilder._place_by_blocks` accepts every chapter with segments, on the chapters as
ebooklib serves them (the export bridge's check); the Flask test client on the copies
answers 200 for `/`, `/api/projects/<name>` (with the migrated segment count) and
`/project/<name>/review`. A project also FAILs if any file under its real directory changed.

As predicted by the dry run: 2155/2155 chapters, 9521/9521 segments, 29 merge groups of 59
segments (project-2 26, project-5 2, project-7 31). All 29 groups are uniformly translated
(no note in any report). The non-blocking "count differs" count is 79 translated text
segments (of 4188) whose top-level node count differs from the new block HTML; Phase 4's
validation decides what happens to them. Chapters that matched only with comment text: 2
(project-5), as Bug 5 says.

### 5. Dry run still holds

```
$ cd <repo> && python3 plan/experiments/migrate_dryrun.py --strict | tail -1
TOTAL chapters 2155/2155  segments 9521/9521  on-boundary 9462  to-merge 59 in 29 groups  largest block 7899 chars
$ python3 plan/experiments/migrate_dryrun.py --strict > /dev/null; echo $?
0
```

The prototype is unchanged. The new walker gives the same normalized block texts as the
prototype's walker for all 2169 spine chapters (checked once with a throw-away script, not
kept).

### 6. Mutation testing

`scripts/verify/mutation.sh` groups: core now also mutates `src/core/migrate.py` and
`src/epub/blocks.py` and runs `tests/test_blocks.py tests/test_migrate.py` in addition; cli
also runs `tests/test_migrate.py` (the `migrate` command tests); scripts also mutates
`phase2_roundtrip.py` and `phase2_migrate_copies.py`. Only lines changed versus `main` are
mutated (the block tag set counts as changed: it moved from `segmenter.py` to `blocks.py`).
Each full run took about 70 minutes, so no scoping was needed.

| run | mutants | killed | survived | timeout | suspicious |
|---|---|---|---|---|---|
| 1, after "chore: cover the Phase 2 modules…" | 839 | 723 | 115 | 1 | 0 |
| 2, after "test: kill the Phase 2 mutation survivors" | 821 | 806 | 14 | 1 | 0 |

```
$ scripts/verify/mutation.sh main
...
TOTAL mutants 821  killed 806  survived 14  timeout 1  suspicious 0  untested 0
$ echo $?
1
```

Run 1's survivors and what was done (mutmut ids of run 1):

| survivors (run 1) | disposition |
|---|---|
| `blocks.py` 257–290 (32 entries of the block tag set renamed), 294 (`article` not descended), `segmenter.py` 47–50 (`h3`–`h6` not standalone) | killed: `TestRules::test_block_tag_ends_a_run[*]`, `test_descended_tags[*]`, `test_standalone_tags[*]` |
| `segmenter.py` 64, 65 (`pending + words > MAX_WORDS` → `-`, `>=`) | killed: `test_block_that_would_pass_max_starts_a_new_segment`, `test_block_reaching_exactly_max_joins` |
| `blocks.py` 317, 318 (`>` escape), 340 (empty comment text) | killed: `test_greater_than_escaped`, `test_empty_comment_is_no_comment_run` |
| `blocks.py` 333 (`replace(…, 1)` → `2`) | code changed: the count argument removed (a declaration occurs at most once in a start tag) |
| `extractor.py` 416, 419 (`resolve_entities=True`, default parser) | killed: `test_entities_not_expanded` |
| `extractor.py` 457, 460 (`continue` → `break` in the spine loop) | killed: `test_spine_entries_after_skipped_ones_kept` |
| `extractor.py` 465, 466 (init values) | killed: `test_fresh_extractor` |
| `extractor.py` 492, 493, 501 (whitespace regexes), 499 (`h3` title) | code changed (chapter text is the blocks' texts joined by spaces; titles use `split`), killed by `test_text_content_and_title`. This found a real defect: chapter text joined adjacent blocks without a space (commit "fix: separate block texts…") |
| `builder.py` 389, 390 (early `break` of the schema 1 placement) | code changed: the check removed; segments past the last element already got an empty list, which the apply loop skips (equivalent, golden tests unchanged) |
| `builder.py` 407, 414 (`and` → `or` in `_soup_blocks`) | killed: `TestSchema2Export::test_run_with_inline_element_placed`, `test_content_div_placed_as_one_element` |
| `migrate.py` 103, 104, 109, 110, 116, 117 (report defaults) | code changed: `chapters` and `segments` are constructor arguments; `count_differs` is summed per chapter (killed by `test_count_differs_summed_over_chapters`) |
| `migrate.py` 119 (`written` default `None`), 131, 202 (note text), 246 (message) | killed: exact assertions in `test_dry_run_writes_nothing`, `test_merged_status_is_lowest[*]`, `test_missing_source_refused` |
| `migrate.py` 141, 142 (`_top_level_count`), 163 (`groups[-1]` → `[+1]`), 171 (model default) | killed: `TestMigrateSurvivors::test_top_level_count`, `test_trailing_segment_joins_the_last_of_several_groups`, `test_no_model_stays_empty` |
| `migrate.py` 216, 219, 222, 225 (`continue` → `break` per chapter) | killed: `test_chapters_after_an_unmatched_one_are_still_checked[*]` |
| `project.py` 6 (`1 >> 20`: reads nothing) | killed: `TestPhase2ProjectFields::test_sha256_file_of_a_large_file` |
| `project.py` 27, 28, 29, `segmenter.py` 41, 43 (defaults) | killed: `test_schema_1_project_has_no_source_hash`, `test_create_without_hash_or_ranges` |
| `translate.py` 1 (`exist_ok=False`), 4 (message), 8, 9 (`exists`, `file_okay`), 14 (help) | killed: `TestCLIPhase2::test_init_into_existing_empty_directory`, exact line in `test_init_reports_unparsed_chapter`, `test_migrate_needs_a_directory[*]`, `test_migrate_help` |
| `phase2_roundtrip.py` 6, 14, 21, 27, 42, 66, 70, 83, 88; `phase2_migrate_copies.py` 115, 124, 168, 173, 181, 230, 287 | killed by `TestPhase2ScriptSurvivors` and the reordered fixture items, or code changed (`s['translated_text']`; `if report.unmatched:` — `written` is always true otherwise) |
| `phase2_migrate_copies.py` 198, 199, 202–204 (`ignore_errors`, copies sub-directory name, `TESTING`) | code changed: removed (no effect on the result) |
| the rest | equivalent, same as run 2 below |

Run 2's 14 survivors (ids of run 2) and the timeout:

| id | location | mutation | disposition |
|---|---|---|---|
| 164 | `migrate.py:110` | `standalone=False` → `True` | equivalent: `_merge` reads only `html`, `text`, `word_count`, the block range and the fingerprints of the `Segment`; `standalone` is not part of `SegmentState` |
| 5, 7 | `project.py:45` | read chunk `1 << 20` → `2 << 20`, `1 << 21` | performance-only: chunk size of the hash |
| 20, 22, 40, 42, 459 | `project.py:85-86`, `segmenter.py:39-40`, `extractor.py:102` | `X \| None` → `X & None` in annotations | equivalent: annotations are strings under `from __future__ import annotations` and never evaluated (as in Phase 1) |
| 409 | `extractor.py:24` | `no_network=True` → `False` | equivalent: with `load_dtd` off (lxml default) and `resolve_entities=False` the parser never fetches anything, so the network flag is never consulted |
| 437 | `extractor.py:72` | properties default `''` → `'XXXX'` | equivalent: `'XXXX'.split()` does not contain `nav`, as `''.split()` does not |
| 102, 1 | `phase2_migrate_copies.py:47`, `phase2_roundtrip.py:44` | `sys.path.insert(0, …)` → `insert(1, …)` | equivalent under default interpreter path settings, the condition stated for Phase 0's survivor 113 |
| 31 | `phase2_roundtrip.py:94` | `copy.deepcopy(root)` → `copy.copy(root)` | equivalent: lxml's `_Element.__copy__` also copies the whole subtree into a new document (checked: same bytes, no added `<meta>`) |
| 66 | `phase2_roundtrip.py:125` | `failed += 1` → `= 1` for an unreadable source | killed after run 2 by `test_roundtrip_counts_every_failure` (now two unreadable sources): the test passes on the code and fails with the mutant applied by hand (checked once, file restored); no third full run |
| timeout 9 | `project.py:45` | sentinel `b''` → `b'XXXX'` | detected: the hash never ends, the tests do not finish |

### 7. One real call

Not needed for this phase (no prompt or backend change) and not made.

### 8. Known consequences

- New projects are schema 2 and cannot be loaded by code before this phase (`ProjectData`
  there has no `schema` field: `TypeError`). Schema 1 projects stay loadable by `main` as
  long as they are not migrated; a migrated project keeps its old file as
  `project.schema1.json`.
- Segmentation of new projects differs from before: no sentence splitting; `<br>`-separated
  text and text mixed with block children become separate blocks; segments are between 300
  and 500 words unless one block is larger (largest real block: 7899 characters). A chapter
  that is not well-formed XML is no longer segmented (ebooklib and BeautifulSoup used to accept
  it); it is reported. No existing book has one.
- CLI `init` copies the book into the project; `epub_path` is that copy. CLI `export` names
  the file of a schema 2 project after the project directory (`<project>-<format>.epub`), as
  the web export does; a schema 1 project keeps the old name (after the book's file name).
- Chapter titles and metadata come from the raw XHTML and the OPF: a missing `dc:title` or
  `dc:creator` is `Unknown` (as before).
- The web create response has `unparsed_chapters`; the new project page shows them in an
  alert before it redirects (review round 1).
- Named entity references are not supported: a chapter whose body holds an unresolved
  entity reference (an internal-subset entity, or `&mdash;` under an XHTML DOCTYPE, which
  lxml parses without error when no DTD is loaded) is not segmented and is reported; entity
  expansion and DTD loading stay off. 0 of the real chapters have one; a lenient path is
  added only when a real book needs it. A spine entry that is not XHTML and an unreadable
  chapter are reported the same way; a package that cannot be read is one error with a
  message.
- Migration: a merge group that is not wholly translated (a member pending, failed or with
  an empty translation) loses its partial translation (kept in `project.schema1.json`, named
  in the report); a group whose status is changed is always named; a group that mixes code and text
  segments makes its chapter unmatched (0 such groups in the real data); an existing
  `project.schema1.json` blocks the migration.
- A legacy segment whose stored html has no text (for example a body-level `<script>` that
  an old segmenter stored) is folded into the neighbouring merge group; 0 such segments in
  the 8 projects (review round 3).
- Known limitations accepted in review round 4: the migration reports an internal error of
  the reader or walker as malformed project data (nothing is written); a process killed
  during the backup copy can leave a stale temporary file, and one killed between backup and
  save leaves a backup that makes the next run refuse with a message (`project.json` is
  unchanged); a merged group of several code blocks would keep only the first block's code
  text (0 such groups; the segmenter never builds one); archive members are read without a
  size limit, as on `main`.
- Migrated segments whose legacy text held comment text (Bug 5, two project-5 segments) keep
  their translation, which still contains the translated production note; it now has no
  counterpart in the segment's HTML and is exported with the segment. They need the manual
  edit Bug 5 mentions.
- 82 translated segments of the migrated real projects are not paired node by node and are
  replaced (chinese-only) or wrapped (bilingual) as a whole (section 14; the earlier "79
  export through the fallback, as today" was not proven and is corrected in section 11).
  A chinese-only replacement removes the segment's blocks and the `<br>` and whitespace
  between them; images, anchors and `<hr/>` between them stay, after the translation.
  For 26 of the 82 the blocks have more than one parent element (project-2 2, project-3 1,
  project-7 8, project-8 15; section 17): the whole translation, and in bilingual output
  the originals too, go into the first block's parent, so text can move into or out of a
  container (its class and id then no longer apply as in the source). `main` does the same
  for schema 1; Phase 3 places per block on the source tree.
- Schema 2 export pairs node by node only segments made of block elements, as `main` did; a
  segment with an inline run (text directly in a `div` or under `<body>`, `<br>`-separated
  lines) is always replaced or wrapped as a whole, so such text loses its line structure in
  chinese-only output unless the translation is one node for a whole element (section 13).
  A chapter whose blocks do not match its stored ranges and fingerprints as a whole is
  exported untranslated, silently in this phase (the export report is Phase 3). Chapters where the
  bridge's walk and the XML walk cannot agree are listed in section 10 (text directly under
  `<body>` before its first element, a block inside `<p>`, CDATA); none of the real
  chapters.
- Test fixtures built with ebooklib remain; the new raw-ZIP fixtures exist alongside them. A
  raw EPUB without NCX produces an export that ebooklib itself cannot read back (the spine's
  `toc` attribute names a missing item); this is the current builder's behaviour, seen while
  writing a test, not changed here.

### 9. `projects/` untouched

Before the work and after every run of the real-project scripts:

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

The same digest before review round 1 and after every real-data run in it (sections 10 and
11). Both Phase 2 scripts also fail a project when any file under its directory changes.
The migration was only ever run on copies in a temporary directory, deleted afterwards; no
lock file or backup was created under `projects/`.

### 10. Review round 1

Three static reviews (G, O and the third reviewer, whose verdict was approve with minors).
Each accepted item got a test that fails on the code before the round (checked by running the
new tests against an export of the branch before the round, `git archive` of the commit "docs:
record Phase 2 in the acceptance log", in a scratch directory; where a new name did not exist
there, only that import was stubbed in the scratch copy). The exceptions are named in the
table ("guard": the case already held). Line numbers in the reviews were checked against the
code; every accepted claim reproduced.

| # | finding | reviewer | ruling | what was done | tests |
|---|---|---|---|---|---|
| 1 | a merge group not uniformly translated keeps a joined partial translation; export applies any non-empty text | O | accepted | a group of several whose lowest status is pending or failed keeps no translation (the backup keeps it) and is named in the report ("部分譯文已清除（保留於 project.schema1.json）"); a group of mixed status that is wholly translated keeps the joined text, as before | `test_migrate.py::TestMigrate::test_partial_group_loses_its_translation` (was `…_keeps_its_translation`), `test_failed_group_of_one_status_cleared_and_named`, `test_partial_group_exports_in_the_original`, `test_merged_status_is_lowest[*]` (changed: expects the cleared text and the new note); `test_untranslated_group_has_no_note` (guard); `TestMigrateSurvivors::test_count_differs_summed_over_chapters` (changed: its partial group now has no text to count, so the second segment is translated) |
| 2 | a merge group keeps the first segment's type when code and text segments merge | G, O | accepted, fail closed | real data first (read-only, scratch script): the 29 groups are 28 of 2 text segments and 1 of 3 text segments, **0 mixed**. Such a group now makes its chapter unmatched (`<id> merges code and text segments`), nothing written | `TestMigrate::test_group_mixing_code_and_text_unmatched` |
| 3 | export bridge: covered nodes leave out `<img>`/`<br>` between runs, so the fallback duplicates images, leaves stray `<br>`s or nests a duplicate wrapper | G, O | accepted | `_place_by_blocks` returns (segment, units, span): the span is every node from the first unit to the last, `<br>` and blocks without text included; a whole-segment replacement removes the whole span (chinese-only) or moves it into `div.original` (bilingual). When the translation's top-level node count differs from the units and the span is the whole content of one element below `<body>`, that element is the one unit (no nested duplicate wrapper). 1:1 placement unchanged | `test_builder.py::TestSchema2BridgeReview1::test_image_in_a_run_kept_once[*]`, `test_br_div_single_wrapper_translation_chinese_only`, `test_br_div_single_wrapper_translation_bilingual[*]`, `test_br_run_mismatch_keeps_brs_in_the_original[*]`, `test_br_run_mismatch_chinese_only_leaves_no_br`; `test_br_div_one_to_one_still_placed_line_by_line[*]` (guard) and `TestSchema2Export::test_br_separated_runs[*]` unchanged |
| 4 | the bridge counts script/style text the XML walker excludes and never checks complete coverage | G, O | accepted, fail closed | before placing anything in a chapter: every range valid, the ranges (sorted) contiguous from 0 to the bridge's block count, every fingerprint equal; otherwise the chapter stays in the original language. The bridge's text rule is the walker's: nothing inside `script`/`style`, no comments, processing instructions or CDATA | `TestSchema2BridgeReview1::test_script_text_not_counted[*]`, `test_block_not_covered_by_any_segment`, `test_extra_block_in_the_source`, `test_overlapping_ranges`, `test_missing_segment`, `test_matching_chapter_is_placed` (guard); through real ebooklib: `test_through_ebooklib_leading_body_text_stays_original` (passes on the old code too: the old fingerprint check already refused that chapter) |
| 5 | unresolved entity references are dropped silently from text and run HTML | G, O, the third reviewer | accepted | observed with `PARSER` (`resolve_entities=False`, no DTD loading): (a) an entity declared in an internal subset parses, the reference stays an `_Entity` node (`&term;`), its text is in neither `itertext()` nor the block text; (b) `&mdash;`/`&rsquo;` under the XHTML 1.1 DOCTYPE with no DTD loaded parse **without error**, same `_Entity` nodes; (c) the same without a DOCTYPE is an `XMLSyntaxError` (already reported). Now a chapter whose body holds an entity reference node is not segmented and is in `failed` (`unresolved entity reference: &mdash;, &rsquo;`); migration reports such a chapter unmatched. Entity expansion and DTD loading stay off. Real data: 0 of the 2170 XHTML items contain an entity reference node | `test_blocks.py::TestEntities::test_chapter_reported[*]`, `test_entity_outside_body_ignored` (guard), `TestRules::test_entities_not_expanded` (replaced: it asserted the silent drop; now asserts the reference is reported and not expanded), `test_migrate.py::TestMigrate::test_chapter_with_entity_reference_unmatched` |
| 6 | an existing `project.schema1.json` is overwritten; the source copy is not atomic and a corrupt copy gives a traceback | G, O, the third reviewer | accepted | migration refuses when `project.schema1.json` exists ("已存在，不會覆寫；請先移走它", nothing written); the source is copied to `source.epub.<hex>.tmp` and `os.replace`d (the temporary file removed on failure); an unreadable source is a `MigrationError` ("來源 EPUB 無法讀取：…") | `TestMigrate::test_existing_backup_refused`, `test_restored_schema_1_after_migration_refused`, `test_source_copied_through_a_temporary_file`, `test_corrupt_source_is_a_migration_error` |
| 7 | package reader: no rootfile, item without `href`, bad or encrypted ZIP crash; a non-XHTML spine document is dropped silently | O | accepted | one `EPUBError` (new, `src/epub/extractor.py`) with a message for: not a ZIP or unreadable file, missing `container.xml` or OPF, no rootfile or no `full-path`, container/OPF not well-formed, a manifest item without `href`, an encrypted or otherwise unreadable package member. CLI `init` prints "無法解析 EPUB：…", removes its copy of the book and exits 1; the web create path answers 400 with the message. A spine entry whose media type is not exactly `application/xhtml+xml` is in `failed` ("media type … is not XHTML"); an unreadable chapter member is in `failed` ("unreadable: …") | `test_blocks.py::TestPackageErrors` (11), `TestRawPackage::test_unparseable_and_missing_chapters_reported` (changed: the image in that spine is now reported), `test_cli.py::TestCLIInit::test_init_unreadable_epub_gives_a_message`, `test_web.py::TestCreateProjectMarker::test_unreadable_epub_gives_a_message` |
| 8 | the web create page ignores `unparsed_chapters` | G | accepted | the page alerts "以下章節無法解析，未分段，將以原文保留：" with the chapter ids before redirecting | `test_web.py::TestCreateProjectMarker::test_new_project_page_shows_unparsed_chapters` (rendered page text); JSON: `test_unparsed_chapter_reported` (unchanged) |
| 9 | CLI export name changed for schema 1 projects | G | accepted | schema 1: the book's file name, as before; schema 2: the project directory name | `test_cli.py::TestCLIPhase2::test_schema_1_export_keeps_the_book_name`; `test_export_named_after_the_project` unchanged |
| 10 | check script: temporary directory not checked against `--projects-dir`; "count differs" is not the bridge's count | G, O | accepted | `phase2_migrate_copies.py` checks the resolved `tempfile.gettempdir()` against the repository and `--projects-dir` before creating anything, then creates its directory there; new column "export fallback": translated text segments whose translation's top-level node count differs from the units the bridge places them on (those replaced or wrapped as a whole); "count differs" kept | `test_verify_scripts.py::TestMigrateCopiesCheck::test_refuses_temporary_directory_inside_repository` (changed: patches `gettempdir`, checks nothing is created), `test_refuses_temporary_directory_inside_projects_dir[*]`, `test_export_fallback_counted`; exact-output tests updated for the column |
| 11 | a multi-node run is flattened into separate entries: reordered inline tags land on the wrong word, a run becomes several bilingual rows | the third reviewer | accepted, with 3 | each block is one unit: a run of several nodes is wrapped in a temporary `epub-translator-run` element while placing and unwrapped afterwards | `TestSchema2BridgeReview1::test_reordered_inline_tag`, `test_run_and_paragraph_one_row_per_block[*]` |
| 12 | `block_start`/`block_end` not validated (missing, negative, empty, out of range) | the third reviewer | accepted, with 4 | both must be `int` (not `bool`) with `0 <= start < end <= blocks` | `TestSchema2BridgeReview1::test_invalid_range[*]` (6; `None`/`None` passes on the old code too) |
| 13 | `migrate` on a directory that is not a project leaves a `project.lock` and gives a traceback | the third reviewer | accepted | `project.json` must exist before the lock is taken ("不是專案目錄（沒有 project.json）：…"); an unreadable or malformed project file is a `MigrationError` ("無法讀取 project.json：…"); an unreadable EPUB see 6 | `TestMigrate::test_not_a_project_refused_without_a_lock_file`, `test_corrupt_project_file_is_a_migration_error`, `TestMigrateCLI::test_not_a_project_gives_a_message` |
| 14 | the OPF path from `container.xml` is not percent-decoded | the third reviewer | accepted (first half) | `unquote()` on `full-path` | `TestPackageErrors::test_percent_encoded_opf_path` |
| — | a manifest href with a fragment is not split | G, the third reviewer (second half) | rejected | a fragment is not valid in a manifest href; such a chapter is already reported as missing from the archive | — |

Also checked before fixing, read-only on the real data (scratch script, not kept): the
types of the 29 merge groups (item 2) and the number of XHTML items whose parsed tree holds
an entity reference node (item 5, 0 of 2170).

Where the two walks cannot agree (for Phase 3; checked once through real ebooklib with
invented chapters, scratch script not kept): the bridge walks ebooklib's regenerated
`get_content()`, so these chapters no longer match their stored blocks and are exported
**untranslated**, silently in this phase:

- text directly under `<body>` before its first child element (ebooklib keeps only the
  body's children; `test_through_ebooklib_leading_body_text_stays_original`);
- a block element inside `<p>` (the HTML parser closes the `<p>` before it);
- CDATA sections (text for lxml in XML mode; ignored by the bridge).

Checked and agreeing: upper-case tag names, `<ruby>`/`<rt>` text, `<script>` inside a run
(after this round). None of the 2155 real chapters is affected (section 11).

### 11. Review round 1: checks after the last code change

```
$ python3 -m pytest tests/ -q -p no:cacheprovider
1142 passed in 136.30s (0:02:16)
```

1072 before the round. Tests changed, and why: listed in the table above
(`test_entities_not_expanded`, `test_unparseable_and_missing_chapters_reported`,
`test_merged_status_is_lowest`, `test_partial_group_keeps_its_translation` → `…_loses_…`,
`test_count_differs_summed_over_chapters`, `test_refuses_temporary_directory_inside_repository`,
exact-output tests of the migration check for the new column, `test_fresh_extractor` for the
new `not_xhtml` attribute). None removed.

```
$ python3 scripts/verify/phase2_roundtrip.py --projects-dir <repo>/projects --anonymize | tail -1
TOTAL projects 8  chapters 2170  round trip equal 2170  whole-document equal 2146  failed projects 0
$ echo $?    # of the unpiped run
0
$ python3 scripts/verify/phase2_migrate_copies.py --projects-dir <repo>/projects --anonymize
PASS project-1: chapters 22/22, segments 220/220, merge groups 0 (0 segments), count differs 0, export fallback 0, translated chars 211044 -> 211044, export places 22/22, web load ok
PASS project-2: chapters 33/33, segments 291/291, merge groups 13 (26 segments), count differs 16, export fallback 16, translated chars 302113 -> 302113, export places 33/33, web load ok
PASS project-3: chapters 39/39, segments 4385/4385, merge groups 0 (0 segments), count differs 1, export fallback 1, translated chars 15637 -> 15637, export places 39/39, web load ok
PASS project-4: chapters 1763/1763, segments 2236/2236, merge groups 0 (0 segments), count differs 32, export fallback 32, translated chars 746052 -> 746052, export places 1763/1763, web load ok
PASS project-5: chapters 35/35, segments 840/840, merge groups 1 (2 segments), count differs 1, export fallback 1, translated chars 462908 -> 462908, export places 35/35, web load ok
PASS project-6: chapters 108/108, segments 581/581, merge groups 0 (0 segments), count differs 0, export fallback 0, translated chars 356677 -> 356677, export places 108/108, web load ok
PASS project-7: chapters 68/68, segments 637/637, merge groups 15 (31 segments), count differs 13, export fallback 12, translated chars 471477 -> 471477, export places 68/68, web load ok
PASS project-8: chapters 87/87, segments 331/331, merge groups 0 (0 segments), count differs 16, export fallback 15, translated chars 159747 -> 159747, export places 87/87, web load ok
TOTAL projects 8  chapters 2155/2155  segments 9521/9521  merge groups 29 (59 segments)  count differs 79  export fallback 77  translated chars unchanged 8  export places 2155/2155  web loads 8  failed projects 0
$ echo $?
0
$ cd <repo> && python3 plan/experiments/migrate_dryrun.py --strict | tail -1
TOTAL chapters 2155/2155  segments 9521/9521  on-boundary 9462  to-merge 59 in 29 groups  largest block 7899 chars
$ python3 plan/experiments/migrate_dryrun.py --strict > /dev/null; echo $?
0
```

As before the round: 2155/2155 chapters, 9521/9521 segments, 29 merge groups (59
segments), translated characters unchanged in all 8, every project loads. The export
placement now also requires complete, non-overlapping coverage of every chapter and still
accepts 2155/2155. **Correction of section 4:** "count differs 79" compared the translation
with the new block HTML, not with what the export places, and the claim that those segments
export "through the existing fallback, as today" was not proven and was wrong for
`<br>`-separated runs (the fallback left the `<br>`s behind or nested a duplicate wrapper,
item 3). The new column counts what the export does: **77** translated text segments
(project-2 16, project-3 1, project-4 32, project-5 1, project-7 12, project-8 15) have a
top-level node count different from the units the bridge places them on and are replaced
(chinese-only) or wrapped (bilingual) as a whole; that replacement now consumes the
segment's whole node range. No migration copy was left in the temporary directory.

### 12. Mutation testing after review round 1

`scripts/verify/mutation.sh <base>`, where `<base>` is the commit that recorded Phase 2 in
the log ("docs: record Phase 2 in the acceptance log"), so only the lines changed in this
round are mutated. Run 2 took 22 minutes.

| run | mutants | killed | survived | timeout | suspicious |
|---|---|---|---|---|---|
| 1, after "fix: count export fallbacks…" | 189 | 179 | 10 | 0 | 0 |
| 2, after "test: kill the review round 1 mutation survivors" | 188 | 187 | 1 | 0 | 0 |

```
$ scripts/verify/mutation.sh <base>
...
TOTAL mutants 188  killed 187  survived 1  timeout 0  suspicious 0  untested 0
$ echo $?
1
```

Run 1's survivors (ids of run 1); each kill was also checked by applying the mutant by hand
to a copy of the code and running the test file:

| id | location | mutation | disposition |
|---|---|---|---|
| 24 | `migrate.py:209` | `continue` → `break` after an entity chapter | killed: `test_migrate.py::TestMigrate::test_chapters_after_an_entity_chapter_still_checked` |
| 79, 80 | `builder.py:216` | the "nothing else in the element" condition of the element placement negated / `or` → `and` | killed: `TestSchema2BridgeSurvivors::test_no_element_placement_past_an_element_outside_the_range` (an image outside the range stays); `test_element_placement_with_whitespace_around` added alongside |
| 88 | `builder.py:338` | `_remove(rest)` → `_remove(None)` in the plain-text fallback | killed: `test_whitespace_translation_removes_the_rest` |
| 102 | `builder.py:559` | run wrapper inserted before the run's second node | killed: `test_comment_inside_a_run_keeps_its_place` (a comment between the run's nodes would move) |
| 110 | `extractor.py:78` | `SpineItem.media_type` default → `None` | code changed: the default removed (every caller passes the media type) |
| 132 | `extractor.py:143` | `not_xhtml` initial value → `None` | killed: `TestRules::test_fresh_extractor` (now also checks `not_xhtml`) |
| 143, 149 | `extractor.py:195, 206` | `continue` → `break` after an unreadable / entity chapter | killed: `TestReview1Survivors::test_chapters_after_an_unreadable_chapter_kept`, `test_chapters_after_an_entity_chapter_kept` |
| 91 | `builder.py:537` | `RUN_TAG` renamed | survives run 2, see below |

Run 2's survivor:

| id | location | mutation | disposition |
|---|---|---|---|
| 91 | `builder.py:537` | `RUN_TAG = 'epub-translator-run'` → `'XXepub-translator-runXX'` | equivalent: the name of the temporary wrapper only has to differ from every element name in the chapter, since every wrapper is unwrapped before the chapter is written; it holds for both names unless a source chapter uses one of them as an element name |

### 13. Review round 2

Three static reviews again (G, O, the third reviewer: approve with minors). Direction set by
the orchestrator for the temporary bridge: simpler and safer, not smarter. Each accepted
item got a test that fails on the code before the round (checked against a `git archive` of
the commit that recorded round 1 in the log, in a scratch directory); exceptions are marked
"guard". Every accepted claim reproduced.

| # | finding | reviewer | ruling | what was done | tests |
|---|---|---|---|---|---|
| 1 | the round-1 span made chinese-only whole-segment replacements delete blocks without text between the first and last unit (an image-only `<p>`, `<hr/>`, `<a id>` anchors) that the model never saw | O | accepted | a chinese-only replacement removes only the nodes of the segment's blocks and the `<br>` and whitespace strings between them; other nodes stay in place. Bilingual output still moves the whole span into `div.original` | `test_builder.py::TestSchema2BridgeReview2::test_textless_blocks_between_units_kept_once[*]` (the two bilingual cases are guards: they already kept them) |
| 2 | pairing by node count is not valid when a unit is an inline run (`<div><em>First</em> line<br/>Second</div>` + `<em>第一</em>行\n第二` is mis-paired) | G, the third reviewer | accepted | a segment is paired node by node only when every one of its blocks is a block element, as on `main`; a segment with any inline run is replaced (chinese-only) or wrapped (bilingual) as a whole. The temporary run wrapper of round 1 (items 3 and 11 there) is removed | `TestSchema2BridgeReview2::test_two_runs_not_paired_by_count[*]`, `test_inline_element_run_and_paragraph` (the third reviewer's `<em>Hello</em>` case). Changed, because runs are no longer paired: `TestSchema2Export::test_br_separated_runs[*]`, `test_text_mixed_with_blocks[*]`, `test_raw_epub_end_to_end`, `TestSchema2BridgeReview1::test_br_div_one_to_one_still_placed_line_by_line` → `test_br_div_line_by_line_translation_replaced_whole`, `test_run_and_paragraph_one_row_per_block` → `test_run_and_paragraph_one_row`, `TestSchema2BridgeSurvivors::test_comment_inside_a_run_keeps_its_place` → `…_kept_once` |
| 3 | placement on the enclosing element when the translation's count is neither 1 nor the unit count drops the element's `class`/`id` | O | accepted | the enclosing element is used only when the translation has exactly one top-level node | `TestSchema2BridgeReview2::test_enclosing_element_kept_for_a_multi_node_translation[*]` |
| 4 | migration: the partial-group guard looks at statuses only; the review editor can save an empty translation without changing the status | G | accepted | a member with an empty (or whitespace) translation makes the group not wholly translated: joined translation cleared, status pending when it would have been translated or reviewed, named in the report | `test_migrate.py::TestMigrateReview2::test_translation_cleared_in_the_review_editor` (cleared through the review API), `test_empty_member_of_one_status_named` |
| 5 | migration: valid JSON of the wrong shape (`null`, a segment that is not an object, `epub_path` null) gives a traceback | G, O, the third reviewer | accepted | loading the project and finding its source are inside one `try` at the migration boundary that also catches `AttributeError`; the message is "無法讀取 project.json：…", nothing written | `TestMigrateReview2::test_wrong_json_shape_is_a_migration_error[*]` (the top-level list case is a guard: it was already a `TypeError`), `test_null_epub_path_is_a_migration_error`, `test_wrong_shape_through_the_cli` |
| 6 | a failing save leaves the backup behind, so the next run is refused | O | accepted | the backup is removed when the save raises (`project.json` is unchanged: the save is atomic) | `TestMigrateReview2::test_failed_save_removes_the_backup` |
| 7 | commit hash in the log (round-1 mutation base) | orchestrator | accepted | replaced by a description and `<base>` | — |
| 8 | the enclosing element replaces a structural container of block elements (`<div class="chapter"><p>One.</p><p>Two.</p></div>` + `<p>一二。</p>` leaves bare text in the div; a two-paragraph translation of a one-paragraph div deletes it) | the third reviewer | accepted | the enclosing element is used only for a segment with an inline run (with item 3: and a one-node translation) | `TestSchema2BridgeReview2::test_structural_container_kept_chinese_only[*]`, `test_structural_container_of_one_paragraph_kept` |
| — | the third reviewer's #2 (an inline translation tag lost in the run wrapper's 1:1 path) | the third reviewer | covered by 2 | the wrapper and run pairing are gone | `test_inline_element_run_and_paragraph` |

The placement rules of the bridge after this round, in full: a chapter is placed only when
its blocks match the stored ranges and fingerprints as a whole (round 1); a segment whose
blocks are all block elements is paired node by node when the translation has as many
top-level nodes, otherwise replaced or wrapped as a whole; a segment with an inline run is
always replaced or wrapped as a whole, except that a one-node translation of runs that are
the whole content of one element below `<body>` goes into that element.

### 14. Review round 2: checks after the last code change

```
$ python3 -m pytest tests/ -q -p no:cacheprovider
1168 passed in 133.71s (0:02:13)
$ python3 scripts/verify/phase2_roundtrip.py --projects-dir <repo>/projects --anonymize | tail -1
TOTAL projects 8  chapters 2170  round trip equal 2170  whole-document equal 2146  failed projects 0
$ echo $?    # of the unpiped run
0
$ python3 scripts/verify/phase2_migrate_copies.py --projects-dir <repo>/projects --anonymize
PASS project-1: chapters 22/22, segments 220/220, merge groups 0 (0 segments), count differs 0, export fallback 0, translated chars 211044 -> 211044, export places 22/22, web load ok
PASS project-2: chapters 33/33, segments 291/291, merge groups 13 (26 segments), count differs 16, export fallback 16, translated chars 302113 -> 302113, export places 33/33, web load ok
PASS project-3: chapters 39/39, segments 4385/4385, merge groups 0 (0 segments), count differs 1, export fallback 1, translated chars 15637 -> 15637, export places 39/39, web load ok
PASS project-4: chapters 1763/1763, segments 2236/2236, merge groups 0 (0 segments), count differs 32, export fallback 32, translated chars 746052 -> 746052, export places 1763/1763, web load ok
PASS project-5: chapters 35/35, segments 840/840, merge groups 1 (2 segments), count differs 1, export fallback 1, translated chars 462908 -> 462908, export places 35/35, web load ok
PASS project-6: chapters 108/108, segments 581/581, merge groups 0 (0 segments), count differs 0, export fallback 0, translated chars 356677 -> 356677, export places 108/108, web load ok
PASS project-7: chapters 68/68, segments 637/637, merge groups 15 (31 segments), count differs 13, export fallback 17, translated chars 471477 -> 471477, export places 68/68, web load ok
PASS project-8: chapters 87/87, segments 331/331, merge groups 0 (0 segments), count differs 16, export fallback 15, translated chars 159747 -> 159747, export places 87/87, web load ok
TOTAL projects 8  chapters 2155/2155  segments 9521/9521  merge groups 29 (59 segments)  count differs 79  export fallback 82  translated chars unchanged 8  export places 2155/2155  web loads 8  failed projects 0
$ echo $?
0
$ cd <repo> && python3 plan/experiments/migrate_dryrun.py --strict | tail -1
TOTAL chapters 2155/2155  segments 9521/9521  on-boundary 9462  to-merge 59 in 29 groups  largest block 7899 chars
$ python3 plan/experiments/migrate_dryrun.py --strict > /dev/null; echo $?
0
```

Same totals as before the round. "export fallback" now counts the translated text segments
that are not paired node by node: **82** (was 77). The 5 more are all in project-7: segments
with an inline run whose translation happened to have one top-level node per block, which
round 1 paired node by node and which are now replaced or wrapped as a whole (item 2). The
`projects/` digest was `a7197555c962562a530d6de80fa045af` before the round and after every
real-data run; no migration copy was left in the temporary directory.

### 15. Mutation testing after review round 2

`scripts/verify/mutation.sh <base>`, where `<base>` is the commit that recorded round 1 in
the log, so only the lines changed in this round are mutated.

| run | mutants | killed | survived | timeout | suspicious |
|---|---|---|---|---|---|
| 1, after "docs: name the round 1 mutation base…" | 65 | 55 | 10 | 0 | 0 |
| 2, after "test: kill the review round 2 mutation survivors" | 59 | 59 | 0 | 0 | 0 |

```
$ scripts/verify/mutation.sh <base>
...
TOTAL mutants 59  killed 59  survived 0  timeout 0  suspicious 0  untested 0
$ echo $?
0
```

Run 1's survivors (ids of run 1); the kills were also checked by applying each mutant by
hand to a copy of the code:

| id | location | mutation | disposition |
|---|---|---|---|
| 10 | `migrate.py:175` | `cleared = False` → `None` | code changed: `cleared` is one expression (`incomplete and …`) |
| 39 | `builder.py:225` | enclosing-element placement not paired | killed: `TestSchema2BridgeReview2Survivors::test_enclosing_element_keeps_its_tag_for_a_one_node_translation` |
| 40, 46, 51 | `builder.py:284, 349, 394` | default `pair=True` → `False` | code changed: `remove`/`span` and `pair` are required (both schemas pass them; one older test that called `_apply_chinese_only` with three arguments passes them now) |
| 48, 53 | `builder.py:379, 421` | `span is None` → `is not None` | code changed: the `None` default and its handling are gone |
| 47, 52 | `builder.py:359, 401` | bilingual pairing disabled | killed: `test_block_only_segment_paired_in_bilingual[*]` |
| 58 | `builder.py:567` | whitespace separator test negated | killed: `test_whitespace_between_blocks_removed_with_them` |

### 16. Review round 3

Verdicts: G changes requested, O approve, the third reviewer approve (no findings). A small
round by decision: no new placement rules in the bridge. Each fix got a test that fails on
the code before the round (checked against a `git archive` of the commit that recorded round
2 in the log, in a scratch directory).

| # | finding | reviewer | ruling | what was done | tests |
|---|---|---|---|---|---|
| 1 | migration: a whole-segment placement whose blocks sit in different parent elements puts the whole translation (bilingual: also the originals) into the first block's parent | G (major), O (minor) | accepted as known consequence, measured | no code change; `main` does the same for schema 1, and Phase 3 places per block on the source tree. The migration check counts these segments ("cross-parent"): **26** of the 82 whole-segment placements (project-2 2, project-3 1, project-7 8, project-8 15; 0 elsewhere) | `test_verify_scripts.py::TestMigrateCopiesCheck::test_cross_parent_fallback_counted` |
| 2 | migration: malformed segment fields (`translated_text` null, non-string `html`, unknown `segment_type`, null `translated_at` in a group) give a traceback | G, O | accepted | one error boundary around planning: `TypeError`, `ValueError`, `AttributeError`, `KeyError` there are a `MigrationError` ("project.json 的段落資料格式不正確：…"), nothing written | `test_migrate.py::TestMigrateReview3::test_malformed_segment_field_is_a_migration_error[*]` (4), `test_malformed_segment_through_the_cli` |
| 3 | the backup copy is not atomic; a partial backup refuses later runs | G, O | accepted | the backup is copied to `project.schema1.json.<hex>.tmp` and `os.replace`d; the temporary file is removed on failure | `TestMigrateReview3::test_failed_backup_copy_leaves_no_backup` |
| 4 | a group demoted to pending because its members have no translation is not named when no text was cleared | O | accepted | a note whenever the status is changed ("…合併後為 pending，譯文為空") | `TestMigrateReview3::test_demoted_group_without_text_named` |
| 5 | a legacy segment whose stored html has no text is folded into the neighbouring group | O | accepted as known limitation, measured | real data (read-only, scratch script not kept): **0** such legacy segments in the 8 projects; no code change | — |

### 17. Review round 3: checks after the last code change

```
$ python3 -m pytest tests/ -q -p no:cacheprovider
1176 passed in 126.27s (0:02:06)
$ python3 scripts/verify/phase2_roundtrip.py --projects-dir <repo>/projects --anonymize | tail -1
TOTAL projects 8  chapters 2170  round trip equal 2170  whole-document equal 2146  failed projects 0
$ echo $?    # of the unpiped run
0
$ python3 scripts/verify/phase2_migrate_copies.py --projects-dir <repo>/projects --anonymize
PASS project-1: chapters 22/22, segments 220/220, merge groups 0 (0 segments), count differs 0, export fallback 0 (cross-parent 0), translated chars 211044 -> 211044, export places 22/22, web load ok
PASS project-2: chapters 33/33, segments 291/291, merge groups 13 (26 segments), count differs 16, export fallback 16 (cross-parent 2), translated chars 302113 -> 302113, export places 33/33, web load ok
PASS project-3: chapters 39/39, segments 4385/4385, merge groups 0 (0 segments), count differs 1, export fallback 1 (cross-parent 1), translated chars 15637 -> 15637, export places 39/39, web load ok
PASS project-4: chapters 1763/1763, segments 2236/2236, merge groups 0 (0 segments), count differs 32, export fallback 32 (cross-parent 0), translated chars 746052 -> 746052, export places 1763/1763, web load ok
PASS project-5: chapters 35/35, segments 840/840, merge groups 1 (2 segments), count differs 1, export fallback 1 (cross-parent 0), translated chars 462908 -> 462908, export places 35/35, web load ok
PASS project-6: chapters 108/108, segments 581/581, merge groups 0 (0 segments), count differs 0, export fallback 0 (cross-parent 0), translated chars 356677 -> 356677, export places 108/108, web load ok
PASS project-7: chapters 68/68, segments 637/637, merge groups 15 (31 segments), count differs 13, export fallback 17 (cross-parent 8), translated chars 471477 -> 471477, export places 68/68, web load ok
PASS project-8: chapters 87/87, segments 331/331, merge groups 0 (0 segments), count differs 16, export fallback 15 (cross-parent 15), translated chars 159747 -> 159747, export places 87/87, web load ok
TOTAL projects 8  chapters 2155/2155  segments 9521/9521  merge groups 29 (59 segments)  count differs 79  export fallback 82 (cross-parent 26)  translated chars unchanged 8  export places 2155/2155  web loads 8  failed projects 0
$ echo $?
0
$ cd <repo> && python3 plan/experiments/migrate_dryrun.py --strict | tail -1
TOTAL chapters 2155/2155  segments 9521/9521  on-boundary 9462  to-merge 59 in 29 groups  largest block 7899 chars
$ python3 plan/experiments/migrate_dryrun.py --strict > /dev/null; echo $?
0
```

Same totals as before the round; the new number is the cross-parent count (26). The
`projects/` digest was `a7197555c962562a530d6de80fa045af` before the round and after every
real-data run; no migration copy was left in the temporary directory.

Mutation testing: `scripts/verify/mutation.sh <base>`, where `<base>` is the commit that
recorded round 2 in the log.

```
$ scripts/verify/mutation.sh <base>
...
TOTAL mutants 43  killed 43  survived 0  timeout 0  suspicious 0  untested 0
$ echo $?
0
```

### 18. Review round 4 (final)

Verdicts: G approve, O approve, the third reviewer approve (no findings). No production code
changed in this round, so no mutation run was needed.

| # | finding | reviewer | ruling | what was done | tests |
|---|---|---|---|---|---|
| 1 | the round-3 failure tests do not check that the project lock is released | G | accepted (test only) | the malformed-input and failed-backup-copy tests also assert `lock_free_for_other_process()` afterwards. Checked by hand: with the final `lock.release()` in `migrate_project` skipped (in a scratch copy of the code), all 5 of these tests fail | `test_migrate.py::TestMigrateReview3::test_malformed_segment_field_is_a_migration_error[*]`, `test_failed_backup_copy_leaves_no_backup` |
| 2 | the migration's error boundary can report an internal error of the reader or walker as malformed project data | O | known limitation, not fixed | costs diagnosis time; nothing is written | — |
| 3 | a process killed during the backup copy can leave a stale temporary file; one killed between backup and save leaves a backup that makes the next run refuse with a message | O | known limitation, not fixed | fail-safe: `project.json` is unchanged, the message names the file to move | — |
| 4 | a merged group of several code blocks would store only the first block's code text | O | known limitation, not fixed | the real data has 0 such groups and the normal segmenter never builds one | — |
| 5 | archive members are read without a size limit | O | known limitation, not fixed | as on `main` | — |

```
$ python3 -m pytest tests/ -q -p no:cacheprovider
1176 passed in 127.53s (0:02:07)
```

The real-data scripts were not run in this round (no code change since section 17).

### Not done

- Export does not verify `source_sha256` (plan 4.2 "Export verifies the hash"): that is
  part of the Phase 3 exporter.
- The plan's "confirm that each stored translation still passes structural validation
  against the new block HTML" is reported only as the non-blocking count (Phase 4 has the
  validator).
- No lenient-HTML fallback (no real chapter needs it).
- `CLAUDE.md` and `README.md` are not updated.

### Not verified

- A full export of a migrated real project was not built; the scripts check only that the
  export's placement accepts every chapter. Exports were not opened in a reader.
- The `migrate` CLI was not run on the real copies (the script calls `migrate_project`, the
  function the command calls); the CLI wrapper is covered by tests.
- The web pages of migrated projects were checked through the Flask test client (status
  codes and the segment count), not in a browser.
- The determinism of indexes and fingerprints was tested on the raw-ZIP fixture, not on the
  real books (the round-trip and migration scripts walk each real chapter once per run).
- Mutation testing ran on Python 3.12.7, the suite on 3.9.6. Mutant 66 of run 2 was killed
  by a test written after the run, verified by applying that one mutant by hand.
- Review round 1: the encrypted-archive tests set the "encrypted" flag bit of the ZIP
  entries; no archive encrypted by a real tool was tried. The alert on the new project page
  was checked as rendered page text, not in a browser. The walk-disagreement cases of
  section 10 and the real-data counts of items 2 and 5 come from throw-away scripts (not
  kept). A full export of a migrated real project was still not built.
- Review round 2: the check script counts the segments that are not paired; it does not
  compare images or ids before and after an export (the tests do, on invented chapters).
  The explanation of the 5 more fallbacks (section 14) follows from the rule; the 5
  segments were not listed individually.

---

## Phase 3 — ZIP export

Date: 2026-11-20. Branch: `phase-3/zip-export`, from `main` after Phase 2. No new runtime
dependency; ebooklib is no longer a runtime dependency (moved to `requirements-dev.txt`: only
the test fixtures in `tests/conftest.py` still build EPUBs with it). Commands run in the
worktree of the branch with `source <repo>/venv/bin/activate` (Python 3.9.6 for the suite and
the scripts, 3.12.7 for mutmut, as before). The real projects are read from `<repo>/projects`;
nothing under it was written (section 9). EPUBCheck v5.4.0 is installed on this machine and was
run (section 5).

### 1. What was built

| Item | Where | Main names |
|---|---|---|
| Exporter | `src/epub/exporter.py` (new) | `export_project(project_dir, output_path, fmt)` → `ExportReport` (`chapters`: `ChapterReport` per chapter with translations — `translated`, `kept` as (segment id, reason); `untranslated`; `summary()`, `lines()`); `ExportError`; reasons `chapter_unreadable`, `chapter_mismatch`, `not_well_formed`, `structure_mismatch`, `ambiguous_source`, `code_markup`, `anchor_mismatch` (review round 1: was `missing_anchor`), `chapter_doctype` (review round 1) |
| Run positions | `src/epub/blocks.py` | `Block.parent` (the walked element), `Block.prev` (for a run: the child whose tail begins it) |
| OPF path | `src/epub/extractor.py` | `package_path(zf)` (container.xml → OPF member), used by `read_package` |
| CLI | `translate.py export` | schema 1 → the migrate message, exit 1; prints the report lines; output `<project>/output/<project dir name>-<format>.epub`; an `ExportError` (incl. the project lock held by another process) or any other exception → one line, exit 1, no traceback (review round 1) |
| Web | `src/web/blueprints/export.py`, `templates/project/export.html` | status carries `summary` (`translated`, `kept`, `untranslated`, `reasons`, and since review round 1 `lines`: the CLI's report lines); the export tab shows the lines (reason texts, per-chapter lines), also when opened after the export finished, or the error; a schema 1 project fails with the migrate message |
| Removed | `src/epub/builder.py`, `_is_container` in `src/core/segmenter.py`, `scripts/verify/phase0_builder_alignment.py`, `tests/test_builder.py`, `tests/data/*.json` | the ebooklib builder, its schema 1 walk and the Phase 2 bridge |
| Changed check | `scripts/verify/phase2_migrate_copies.py` | "export places" now uses the exporter's chapter check (`_matches`); the bridge-only "export fallback (cross-parent)" columns are gone |
| Check script | `scripts/verify/phase3_export_check.py` (new) | see section 4 |

Per item of the brief:

1. **Archive.** The source hash (`source_sha256`) is checked first; a missing or different
   source, or an empty stored hash, is an `ExportError` and nothing is written. The output is
   written to a temporary file next to the target and `os.replace`d. `mimetype` first, stored
   (the source's bytes; `application/epub+zip` when it has none); then every source member
   once, in source order (a duplicated name is written once). Untouched members are copied
   with their bytes, date, external attributes and compression (stored or deflated; anything
   else becomes deflated); since review round 1 every member also keeps its raw name bytes,
   internal attributes, system, versions, extra field and comment, and the file gets mode
   `0o666 & ~umask` (section 10, A8/A9). The output may not be the source itself (A1). Replaced: chapters with at least one segment filled, the OPF, the
   NCX and the navigation document when a label changed, plus the bilingual stylesheet.
2. **Per chapter.** The chapter is parsed from the source archive (same strict parser as the
   segmenter), walked with `walk_blocks`, and accepted only when every stored range is valid
   (`int`, `0 <= start < end <= blocks`), the ranges cover the blocks exactly once and every
   fingerprint is equal (the bridge's rule, now on the XML tree). Accepted translations
   (status translated or reviewed, non-empty text) then go through the reduced check (3) and
   are filled on the tree: a block element keeps its tag and attributes and gets the
   translation element's content; a run is replaced by the translation's inline content in
   place (parent text / previous child's tail, then the nodes). Blocks are filled in reverse
   document order, so a filled run never moves a later block's anchor. The head is never
   touched (bilingual adds one `<link>` last). Serialization: XML declaration, the source's
   DOCTYPE, comments and processing instructions before the root, then the root element
   serialized in a document of its own (Phase 2 fact (a): no `<meta>` is added), then the
   comments and processing instructions after the root (review round 1). A chapter, NCX, nav
   or OPF whose DOCTYPE has an internal subset is never rewritten (section 10, A2/E2). lxml in
   XML mode keeps SVG attribute case and namespace declarations.
3. **Reduced check** — section 2.
4. **Bilingual identity.** Each block becomes one row `div.bilingual-container` (horizontal)
   or `div.bilingual-vertical` (vertical) holding `div.original` (the source element, or the
   run's text and nodes, moved) and `div.translation` (a copy of the source element with the
   translation's content, or the run's translated inline content). In the copy, `id` and
   `name` are removed from every element of its subtree (the translation's inner elements
   included), so ids exist only on the originals; the copy (for a run: `div.translation`)
   gets `xml:lang="zh-TW"`, and `lang="zh-TW"` as well in EPUB 3 packages (XHTML 1.1 of
   EPUB 2 has no `lang` attribute). The stylesheet is `bilingual.css` in the OPF's directory,
   or `bilingual-N.css` when a member of that name exists (compared without case); manifest
   id `bilingual-css` or `bilingual-css-N`; linked from each filled chapter with
   `quote(relpath(stylesheet, chapter directory))`. Not added when no chapter was filled.
   Wrapper elements use the namespace (and prefix) of the chapter's `<body>`.
5. **TOC labels.** (Narrowed in review round 1, section 10 A4–A6: only NCX `navMap`
   `navPoint`s and the `a` of the `nav` whose `epub:type` holds `toc`; each id maps to its own
   heading; first occurrence wins; innermost container only; an equal label or a label holding
   an id is left as it is. The first version follows.) For every NCX
   `navPoint`/`navTarget`/`pageTarget` (`content@src`, `navLabel/text`) and every `a` inside a
   `nav` of a navigation document: the href is
   resolved relative to the file holding the entry (percent-decoded; an href with a scheme
   or a network path is skipped); the target is the element with that `id` in the chapter's
   source tree, or the chapter's first `h1`–`h6` without fragment; the block must be a block
   element that is the target or contains it, its segment must have been filled, and the
   translated block must hold a heading (the block itself or its first heading descendant);
   the label is that heading's text, whitespace-normalized, non-empty. Added in the fix round
   (decided by the orchestrator): when the target is not inside a block but contains blocks
   (a wrapper `div`/`section`, or `<body>`), its first block is used if that block is a filled
   `h1`–`h6`. Any other case: label unchanged. A nav `a` gets the label as its only content.
   Applied in all three formats.
6. **Language.** chinese-only: the first `dc:language` becomes `zh-TW`; the root of each
   filled chapter gets `xml:lang="zh-TW"` (and `lang` when the package is EPUB 3 or the root
   already has one); inside filled blocks every `lang`/`xml:lang` whose primary subtag equals
   that of the package's first `dc:language` becomes `zh-TW` (others, e.g. `fr`, stay); since
   review round 1 also on the parent and ancestors of each filled block (A11).
   Bilingual: package and roots unchanged; the copies are marked as in 4, and drop the note
   values `footnote`, `endnote`, `rearnote`, `note` from their `epub:type` (A12).
7. **Failures.** A chapter that is not in the spine, missing, not well-formed, without body,
   with an unresolved entity, or that does not match its segments keeps its bytes; a segment
   that fails the check keeps its blocks; both are in the report with the reason. A bad
   chapter or segment never fails the export. CLI output (invented example):
   ```
   已輸出：<dir>/output/<name>-chinese-only.epub
   以譯文匯出 2 段，保留原文 2 段（另有 0 段未翻譯或翻譯失敗，以原文匯出）
     譯文不是格式正確的 XML（not_well_formed）：1
     譯文的頂層結構與原文區塊不符（structure_mismatch）：1
     章節 two：譯文 0，原文 2（not_well_formed, structure_mismatch）
   ```
8. **Schema 1.** `export_project` raises `ExportError(SCHEMA_1_MESSAGE)`: 「此專案是舊格式
   （schema 1），無法匯出。請先執行 python3 translate.py migrate <專案目錄> 再匯出。」 The CLI
   prints it and exits 1 before writing anything; the web export fails with it. `src/` no
   longer imports ebooklib.

### 2. The reduced structural check and the mapping rule

A segment's stored html is its blocks' serializations joined by `\n` (Phase 2). The
translation must:

1. **be well-formed**: parsed with the strict parser as the content of a wrapper element that
   declares the namespaces in scope at the blocks' parents (review round 1: all of them, not
   only the first block's; so `epub:type` parses; named
   entities such as `&nbsp;` do not);
2. **have the blocks' top-level structure**: its top-level nodes are grouped — each element
   whose name is a block tag is a group of its own; a top-level `<br>` is refused; the text and
   inline elements between block elements form one inline group (whitespace, comments and
   processing instructions alone form none). The groups must correspond one to one to the
   blocks, with each maximal sequence of consecutive runs of the source taken as one entry: a
   block element ↔ an element with the same (namespaced) name; a sequence of one run ↔ the whole
   inline group; a sequence of k > 1 runs (lines separated by `<br>` in the source, the only way
   two runs can be adjacent) ↔ the inline group split at the newlines of its top-level text,
   which must give exactly k pieces with content. Since review round 1 (E4) a piece whose
   source block has text must have text (`structure_mismatch` otherwise).
3. The same split is applied to the stored html itself, and each source piece's text must have
   the fingerprint of its block; otherwise the segment is `ambiguous_source` (a run whose own
   top-level text holds a newline, next to another run).
4. **keep the anchors** (added in the fix round, decided by the orchestrator; made exact in
   review round 1, A3): the `id` values and the `name` values of `<a>` found inside a source
   block, counted, must be the same values with the same counts in that block's translation
   (no missing, extra or repeated anchor, no copy of the block's own id); otherwise the segment
   is `anchor_mismatch` (named `missing_anchor` before review round 1). "Inside" means below the block element itself (its own attributes always
   come from the source) or anywhere in a run. Code segments are not checked (their
   translation is text filled into a copy of the source element). No other attribute or tag is
   checked.

Why this rule: it uses only what the segmenter guarantees (a block element is one element, a
run is inline content, blocks are joined by newlines), so the split of the source is exact and
checkable, and a translation is either mapped block by block or not at all. It accepts no
translation whose structure has to be guessed: a run wrapped by the model in `<p>`, merged or
split paragraphs, text outside the elements, a `<br>` for a newline — all stay in the original
language. Inner structure (inline tags and their attributes) is not checked, except that the
anchors of step 4 must survive, so internal links keep resolving: the rest is Phase 4's keyed
validation; inner attributes come from the translation until then.

Code segments: the translation is plain text. The target is the `<pre>` (or the `<code>` of a
code run), or its only child when that is a `<code>` with no text around it; a target that
holds child elements (syntax highlighting), or whose text is not exactly the stored code
(review round 1, A7: e.g. whitespace outside the `<code>`), is `code_markup`; a translation
with characters XML does not allow is `not_well_formed`. A translation equal to the source
code leaves the block untouched in every format (it appears once in bilingual output) and
counts as exported.

### 3. Test suite

```
$ python3 -m pytest tests/ -q -p no:cacheprovider
1386 passed in 133.93s (0:02:13)
```

(After review round 2; 1341 after review round 1.)

1176 before the phase (1210 at the first hand-back; the fix round added 33; review round 1 and
its mutation runs 98: `TestReviewRound1Archive`, `TestReviewRound1Chapters`,
`TestReviewRound1Toc`, `TestReviewRound1Additions`, `TestRound1Survivors`,
`test_zero_external_attributes_kept` in `tests/test_exporter.py`; `TestExportCheckReviewRound1`,
`TestExportCheckRound1Survivors`, `test_source_member_missing`,
`test_chapters_the_exporter_refuses_not_placed` in `tests/test_verify_scripts.py`; section 10). New: `tests/test_exporter.py` (raw-ZIP fixtures, every assertion reads
the output members from the ZIP), `TestExportCheck`, `TestExportCheckEpubcheck`,
`TestExportCheckSurvivors`, `TestExportCheckEpubcheckSurvivors`, `TestExportCheckTocDenominator`
in `tests/test_verify_scripts.py`; the fix round added `TestAnchorsInTheGate` and
`TestTocContainerTarget` to `tests/test_exporter.py`. `write_raw_epub` (conftest) takes the package `version`.

| Acceptance item | Tests |
|---|---|
| mimetype first and stored; members once | `TestArchive::test_mimetype_first_and_stored_members_once[*]`, `test_duplicate_member_written_once`, `test_missing_mimetype_written` |
| members outside the allowed set identical | `TestArchive::test_members_outside_the_allowed_set_identical[*]`, `test_stored_member_stays_stored`, `TestSurvivors::test_mimetype_bytes_and_member_attributes_copied`, `TestChineseOnly::test_untranslated_chapter_and_package_kept` |
| head links and `<style>` equal the source's | `TestChineseOnly::test_head_unchanged`, `TestBilingual::test_stylesheet_added_registered_and_linked[*]` |
| no duplicate id; bilingual copies without id | `TestBilingual::test_identity_ids_only_on_the_original[*]`, `TestProjectLevel::test_no_duplicate_ids_in_raw_fixture` |
| bilingual chapter links an existing, registered stylesheet | `TestBilingual::test_stylesheet_added_registered_and_linked[*]`, `test_stylesheet_name_and_id_do_not_collide`, `test_stylesheet_href_relative_and_encoded`, `test_head_created_when_missing`, `TestSurvivors::test_first_free_names`, `test_package_and_chapter_at_the_archive_root` |
| SVG attribute case | `TestSvg::test_viewbox_case_kept[*]` |
| well-formed output | `TestProjectLevel::test_every_output_chapter_well_formed`, `TestChineseOnly::test_xhtml_10_doctype_gets_no_meta` |
| failed-fingerprint chapter kept original and reported | `TestChapterVerification::test_mismatched_chapter_kept_and_reported[*]` (7 kinds × 3 formats), `test_chapter_not_in_the_spine`, `test_unreadable_chapter_kept[*]`, `TestSurvivors::test_empty_range_is_a_mismatch` |
| segment failing the reduced check kept original | `TestReducedCheck::test_kept_original_and_reported[*]` (9 translations × 3 formats), `test_run_wrapped_in_a_paragraph_kept`, `test_br_runs_need_one_line_each[*]`, `test_ambiguous_source_kept`, `test_one_failing_segment_does_not_stop_the_chapter`, `TestCode::test_code_with_markup_kept[*]` |
| anchors in the gate (fix round) | `TestAnchorsInTheGate::test_anchor_mismatch_kept_original[*]` (6 cases, 9 since review round 1 × 3 formats: inner id lost, `a@name` lost, a `name` on another tag does not count, id in a run, id moved to another line), `test_anchor_present_accepted[*]`, `test_code_not_checked` |
| what the rule accepts | `TestReducedCheck::test_whitespace_and_comments_between_blocks_accepted`, `test_br_runs_split_at_newlines[*]`, `test_newline_inside_an_inline_element_does_not_split`, `test_single_run_keeps_its_newlines`, `test_runs_and_elements_mixed`, `test_epub_prefix_in_translation_parses` |
| TOC label from the translated heading; unchanged on failure | `TestToc::test_labels_from_translated_headings[*]` (first heading, target heading, `<p>` target, unknown id, container target, external, untranslated chapter, no href, link outside a nav), `test_target_inside_a_heading_and_header_block`, `test_label_unchanged_when_the_heading_segment_fails`, `test_label_unchanged_for_an_empty_heading`, `test_toc_files_kept_when_no_label_changes`, `test_unreadable_toc_kept`, `test_relative_to_the_toc_file`, `TestSurvivors::test_every_heading_level_labels[*]`, `test_nav_list_targets_relabelled`; container targets (fix round): `TestTocContainerTarget::test_first_block_heading[*]` (wrapper, nested wrapper, `<body>`; first block a `<p>` or a run stays), `test_unfilled_heading_leaves_labels`, `test_target_inside_a_block_wins` |
| language | `TestChineseOnly::test_language`, `test_explicit_source_language_rewritten`, `test_epub2_root_gets_only_xml_lang`, `test_existing_root_lang_set_in_epub2`, `TestBilingual::test_epub2_copy_gets_only_xml_lang`, `test_copy_source_language_rewritten`, `TestSurvivors::test_source_language_with_region` |
| schema 1 message | `TestProjectLevel::test_schema_1_message`, `TestCLIAndWeb::test_web_schema_1_message`, `test_cli.py::TestCLIPhase2::test_schema_1_export_asks_for_migration` |
| export of a project with failed segments | `TestProjectLevel::test_failed_and_pending_segments_exported_in_the_original[*]` |
| source hash | `TestProjectLevel::test_source_hash_verified[*]`, `test_unreadable_package_is_an_export_error` |
| report, CLI, web | `TestProjectLevel::test_report_lines`, `TestCLIAndWeb` (6) |
| check script | `test_verify_scripts.py::TestExportCheck` (one test per check, each failing on a tampered export), `TestExportCheckEpubcheck` (incl. the chinese-only and bilingual epubcheck rules), `TestExportCheckTocDenominator`, the two survivor classes |

Tests removed or changed, and why:

| Group | Tests | Reason |
|---|---|---|
| `tests/test_builder.py` | all 164 (`TestEPUBBuilder` 9, `TestHTMLAwareTranslation` 4, `TestNavigableStringCounting` 3, `TestHTMLPreservation` 9, `TestTOCTranslation` 4, `TestContentDivAlignment` 2, `TestWalkByMarker` 6, `TestVersion2Export` 15, `TestVersion1ExportMatchesMain` 42 (schema 1 golden tests), `TestSchema2Export` 19, `TestSchema2BridgeReview1` 30, `TestSchema2BridgeSurvivors` 4, `TestSchema2BridgeReview2` 13, `TestSchema2BridgeReview2Survivors` 4) | they test `EPUBBuilder`, which is removed: the schema 1 walk and golden exports (schema 1 is no longer exported), the Phase 2 bridge, the ebooklib read-back helper (`_get_chapter_html`), the test accepting mismatched element counts (`test_chinese_only_html_mismatched_count`: the whole-segment replace path is removed; a count mismatch is now kept original, `TestReducedCheck`), and the metadata test expecting `en` for chinese-only (`test_metadata_preserved`; now `TestChineseOnly::test_language` expects `zh-TW`, `TestBilingual::test_stylesheet_added_registered_and_linked` keeps `en`). The behaviours still wanted are covered in `tests/test_exporter.py` (formats build, invalid format, translation present, both texts in bilingual, output directory created, HTML structure kept, TOC labels) |
| `tests/data/legacy_walk_main.json`, `phase0_segments.json` | data of the golden and Phase 0 builder tests | nothing reads them any more |
| `test_verify_scripts.py::TestAlignmentCheck` (5), `TestAlignmentOutput` (13) | tests of `phase0_builder_alignment.py` | the script checked which walk the builder chooses; the builder and both walks are gone, so it is removed |
| `test_segmenter.py::TestIsContainer` (7), `test_is_container_ignores_comment`, `test_is_container_legacy_counts_comment` | `_is_container` | only the builder's schema 1 walk used it |
| `test_verify_scripts.py::TestMigrateCopiesCheck::test_export_fallback_counted`, `test_cross_parent_fallback_counted` | the bridge's "export fallback" columns | the bridge is gone; Phase 3's reasons replace these counts |
| `TestMigrateCopiesCheck` (exact-line tests, `test_changed_translation_fails`, `test_unplaced_chapter_fails`, `test_chapter_missing_from_book_not_placed`), `TestPhase2ScriptSurvivors::test_partly_placed_count` | changed | lines without the removed columns; they patch the exporter's `_matches` / `read_package` instead of `EPUBBuilder._place_by_blocks` / ebooklib |
| `test_cli.py::test_schema_1_export_keeps_the_book_name` | changed to `test_schema_1_export_asks_for_migration` | schema 1 projects are no longer exported |
| `test_migrate.py::test_partial_group_exports_in_the_original`, `test_migrated_project_exports` | changed | call `export_project` instead of `EPUBBuilder.build`; same assertions |

### 4. Check: exports of the 8 projects in all three formats

```
$ python3 scripts/verify/phase3_export_check.py --projects-dir <repo>/projects --anonymize
epubcheck found; errors compared by message ID (chinese-only, bilingual-horizontal, bilingual-vertical)
PASS project-1: exports 3, translated segments 220: as translation 218, kept original 2 (not_well_formed 2), toc labels changed 0/22 (22 into translated chapters), epubcheck errors source 51 (HTM-004:24 NCX-001:1 RSC-005:26), exports 51/51/51
PASS project-2: exports 3, translated segments 278: as translation 261, kept original 17 (not_well_formed 10, structure_mismatch 7), toc labels changed 0/35 (33 into translated chapters), epubcheck errors source 27 (OPF-034:1 RSC-005:25 RSC-020:1), exports 27/28/28
PASS project-3: exports 3, translated segments 36: as translation 35, kept original 1 (structure_mismatch 1), toc labels changed 18/1357 (22 into translated chapters), epubcheck errors source 0, exports 0/0/0
PASS project-4: exports 3, translated segments 1333: as translation 1286, kept original 47 (not_well_formed 2, structure_mismatch 45), toc labels changed 123/144 (126 into translated chapters), epubcheck errors source 11 (RSC-012:11), exports 11/22/22
PASS project-5: exports 3, translated segments 832: as translation 830, kept original 2 (anchor_mismatch 1, structure_mismatch 1), toc labels changed 245/245 (245 into translated chapters), epubcheck errors source 470 (HTM-004:25 NCX-001:1 PKG-006:1 RSC-005:440 RSC-007:3), exports 466/509/509
PASS project-6: exports 3, translated segments 581: as translation 581, kept original 0, toc labels changed 19/19 (19 into translated chapters), epubcheck errors source 1 (RSC-005:1), exports 1/1/1
PASS project-7: exports 3, translated segments 551: as translation 532, kept original 19 (anchor_mismatch 4, not_well_formed 6, structure_mismatch 9), toc labels changed 1/45 (44 into translated chapters), epubcheck errors source 151 (RSC-005:151), exports 151/183/183
PASS project-8: exports 3, translated segments 331: as translation 315, kept original 16 (structure_mismatch 16), toc labels changed 0/18 (16 into translated chapters), epubcheck errors source 27 (RSC-005:26 RSC-007:1), exports 27/27/27
$ echo $?
0
```

The script copies `project.json`, `source.epub` and `glossary.json` of each project into a new
`mktemp` directory under the system temporary directory (refused before anything is created
when that would be inside the repository or the projects directory), migrates the copies
(all 8 are schema 1), writes the 3 exports of each project into the same directory (24 files),
and removes the directory at the end (checked: no `phase3-export-*` directory left). Checks per
export: `mimetype` first and stored, no member twice, nothing added but the stylesheet, every
member outside the allowed set byte-identical, every XHTML member well-formed, head
`<link>`/`<style>` equal to the source's (bilingual: plus the one added link), no id
duplicated that the source does not duplicate, internal fragment links (any `href`, NCX `src`)
resolve unless they already fail in the source, bilingual chapters link a stylesheet that is
in the archive and the manifest and defines both classes, SVG attribute names a subset of the
source chapter's, the three formats give the same counts, epubcheck (section 5). A project
also FAILs when any file under its real directory changed. The TOC count is followed by the
number of entries that point into a chapter with at least one segment exported as
translation (the meaningful denominator).

The output above is after review round 1 (section 10); after review round 2 (section 11)
the same command printed the identical lines and exited 0; after the fix round the totals were
`as translation 4072  kept original 90 (missing_anchor 5, not_well_formed 20, structure_mismatch
65)  toc labels changed 406/1895 (527 into translated chapters)`. Since review round 1 the
check also requires every source member, the raw name, order, compression, date and
attributes of untouched members, unchanged labels outside the table of contents, and counts
only real TOC entries (1895 → 1885: 10 labelled entries outside the NCX `navMap` and the toc
nav, such as `docTitle`, are no longer counted). The first run of the round failed 3
projects: 124 untouched members written with external attributes `0o600 << 16` instead of 0
(fixed, section 10). Before the fix round (first hand-back) the same command exited 1
with 4 projects failing (7 unresolved fragment links in chinese-only, and bilingual epubcheck
counts above the source's): the 7 links are gone with the anchor rule (section 2), and the
bilingual epubcheck counts pass under the bilingual rule (section 5).

Translated segments (status translated or reviewed) per project: exported as translation /
kept in the original language, by reason (the same in all three formats):

| project | translated | as translation | kept | anchor_mismatch | not_well_formed | structure_mismatch |
|---|---|---|---|---|---|---|
| project-1 | 220 | 218 | 2 | 0 | 2 | 0 |
| project-2 | 278 | 261 | 17 | 0 | 10 | 7 |
| project-3 | 36 | 35 | 1 | 0 | 0 | 1 |
| project-4 | 1333 | 1286 | 47 | 0 | 2 | 45 |
| project-5 | 832 | 830 | 2 | 1 | 0 | 1 |
| project-6 | 581 | 581 | 0 | 0 | 0 | 0 |
| project-7 | 551 | 532 | 19 | 4 | 6 | 9 |
| project-8 | 331 | 315 | 16 | 0 | 0 | 16 |
| total | 4162 | 4058 (97.5%) | 104 (2.5%) | 5 | 20 | 79 |

Review round 1: `anchor_mismatch` (exact multiset) keeps the same 5 segments as
`missing_anchor` did — no real translation adds or repeats an anchor. 90 → 104: the text rule
of E4 (section 2) keeps 14 more segments of project-4, where 16 blocks with source text came
back without text (element blocks, not runs; counted by a read-only scratch script on a
migrated copy, deleted). The paragraphs below describe the fix round's 90.

No chapter is kept for `chapter_mismatch` or `chapter_unreadable` (all 2155 chapters with
segments match), and no segment is `ambiguous_source` or `code_markup`. 85 → 90: the anchor rule
keeps 5 more segments original (project-5 1, project-7 4), exactly the 5 measured before the
fix round: their translations dropped 6 ids of inner footnote anchors, which broke 7 fragment
links in chinese-only. What the other 85 are (shapes
only, from a read-only scratch script on the same copies, not kept): `not_well_formed` — an
unclosed `<p>` or mismatched end tag (18), a bare `&` (2); `structure_mismatch` — the model
merged or split paragraphs (count of top-level elements differs by 1 to 20; most of the 31 in
project-4 have one element fewer), wrapped a run in `<p>` or `<div>` (project-7, project-8: 16
in project-8 alone, where `<br>`-separated runs between `div`s came back as `div`s), or left
text outside a heading (3). None is a false rejection of a translation whose structure matches.

TOC labels changed: 406 of 1895 entries (review round 1: 406 of 1885 real entries, same 527
into translated chapters, same per-project counts); 527 entries point into a chapter with at least one
segment exported as translation (the other 1368 point at untranslated chapters: 1336 in
project-3 alone). Before the fix round 143 changed; the container rule adds 263 (project-5 245,
project-3 18).

**Known limitation: entries into translated chapters whose label stays in the source
language** — 121 of the 527 (read-only scratch classification over copies of the same
projects, not kept):

| project | unchanged / into translated | why |
|---|---|---|
| project-1 | 22 / 22 | the target block is a `<p>` without heading (headings styled as paragraphs) |
| project-2 | 33 / 33 | 32: the chapter has no `h1`–`h6` at all; 1: target block a `<p>` without heading |
| project-3 | 4 / 22 | the heading's segment was kept original (`structure_mismatch`) |
| project-4 | 3 / 126 | the heading's segment was kept original |
| project-5 | 0 / 245 | — |
| project-6 | 0 / 19 | — |
| project-7 | 43 / 44 | the target has no heading: 24 a `<p>` block, 15 a `<blockquote>`, 1 a `<ul>`, 3 an empty element outside every block (2 `div`, 1 `a`) |
| project-8 | 16 / 16 | the target is `<body>`, whose first block is a `div` (not a heading) |

So 114 stay because the target has no heading element to take a label from, and 7 because the
heading itself was kept in the source language.

### 5. epubcheck

EPUBCheck v5.4.0 (`/opt/homebrew/bin/epubcheck`) was on PATH, so the check script ran it on
each source and on each of the 24 exports (JSON report written to a temporary file outside the
repository, deleted after reading; only message IDs and counts are printed, never messages).
Errors are ERROR and FATAL messages, counted per message ID as reported locations.

The rule (fix round, decided by the orchestrator; also in the script's docstring):

- chinese-only: per message ID, the export's count must not be above the source's.
- bilingual: every error ID of the export must also occur in the source, and per ID the
  export's count must be at most twice the source's.

The bilingual rule is looser than chinese-only on purpose: the translated copy of a block takes
the source element's attributes and the source's markup errors inside it (the model repeats
what it was given), so each source error in a translated block can appear once more. Measured
before the fix round, all bilingual excess was of that kind: compared as sets of (message ID,
message text) the bilingual exports added no new message, except two project-5 RSC-005 whose
"expected elements" list differs because the same disallowed `figure`/`aside` now sits inside
`div.original` instead of `<body>`. An error ID the source does not have still fails, and so
does more than one repetition per source error.

ERROR and FATAL counts, source → chinese-only / bilingual-horizontal / bilingual-vertical:

| project | source | exports | result |
|---|---|---|---|
| project-1 | 51 (HTM-004 24, NCX-001 1, RSC-005 26) | 51 / 51 / 51 | pass |
| project-2 | 27 (OPF-034 1, RSC-005 25, RSC-020 1) | 27 / 28 / 28 | pass (bilingual RSC-020 2 ≤ 2 × 1) |
| project-3 | 0 | 0 / 0 / 0 | pass |
| project-4 | 11 (RSC-012 11) | 11 / 22 / 22 | pass (bilingual RSC-012 22 ≤ 2 × 11) |
| project-5 | 470 (HTM-004 25, NCX-001 1, PKG-006 1, RSC-005 440, RSC-007 3) | 466 / 509 / 509 | pass |
| project-6 | 1 (RSC-005 1) | 1 / 1 / 1 | pass |
| project-7 | 151 (RSC-005 151) | 151 / 183 / 183 | pass |
| project-8 | 27 (RSC-005 26, RSC-007 1) | 27 / 27 / 27 | pass |

0 of the 24 exports fail. Before the fix round, chinese-only of project-5 and project-7 had
RSC-012 +1 and +6 (the dropped anchors, now kept original) and are now at or below the source.

### 6. Mutation testing

`scripts/verify/mutation.sh` groups: core no longer mutates `src/epub/builder.py` (removed)
and also runs `tests/test_exporter.py` (it is the only user of the run positions in
`blocks.py`); new group export mutates `src/epub/exporter.py` and
`src/web/blueprints/export.py` with `tests/test_exporter.py tests/test_migrate.py
tests/test_cli.py`; cli also runs `tests/test_exporter.py` (the CLI export tests); scripts
mutates `phase3_export_check.py` instead of the removed `phase0_builder_alignment.py`. Only
lines changed versus `main` are mutated. Each full run took about 2 hours.

| run | mutants | killed | survived | timeout | suspicious |
|---|---|---|---|---|---|
| 1, after "chore: mutate the exporter and the Phase 3 check script" | 1059 | 933 | 124 | 2 | 0 |
| 2, after "test: kill the Phase 3 mutation survivors of run 1" | 1011 | 978 | 31 | 2 | 0 |
| 3, fix round, after "feat: bilingual epubcheck rule and TOC denominator in the export check" | 1067 | 1033 | 32 | 2 | 0 |
| 4, review round 1, after "fix: keep external attributes 0 of untouched members" | core, export, cli done; scripts group stopped (a test failed under hash randomization) | — | — | — | — |
| 5, review round 1, after "test: kill the mutation survivors of review round 1, run 4" | 1259 | 1215 | 42 | 2 | 0 |

```
$ scripts/verify/mutation.sh main
...
TOTAL mutants 1067  killed 1033  survived 32  timeout 2  suspicious 0  untested 0
$ echo $?
1
```

Run 1's survivors (core 2, export 72, scripts 50) and what was done:

| survivors (run 1) | disposition |
|---|---|
| `blocks.py` `parent`/`prev` defaults | code changed: both are required; every block records its walked parent (`_parent()` in the exporter removed) |
| exporter constants: `HEADINGS` h4–h6, `WRAPPER_CLASS`, the six reason codes, `REASON_TEXT`, `SCHEMA_1_MESSAGE`, the `ValueError` and hash messages | killed: `TestSurvivors::test_every_heading_level_labels[*]`, `test_report_codes_texts_and_messages`; the bilingual rows test now spells the class names |
| `ExportReport.untranslated` default, `export_project` default format | code changed: no defaults (every caller passes them) |
| `epub3` from `version or ''`; `css_href` default; `chapter.kept +=` (3); the `path in replaced or path not in names` skip and its `continue`; `changed = False`; `result is None or …`; `source = None` in the except branch; `'summary': None` in the web task | code changed: `str(version)`, the href computed where it is used, assignments, the skip removed (a missing or unreadable TOC file already returns no change), a counter, `if not block.tag`, `raise _Kept(AMBIGUOUS_SOURCE)` in the except branch, the key dropped (`task.get('summary')`) |
| nav/non-XHTML chapter ids (`and not is_nav` → `or`), `continue` → `break` for a chapter without translation, chapter and OPF at the archive root (`or '.'`), first free names (`n = 1`, `n += 1`, for files and ids), source `mimetype` bytes, `external_attr`, only the first `dc:language`, `navTarget`, links only (`or a.get('href') is None` → `and`), prefixed XHTML namespace (`ns`), empty range (`<` → `<=`), malformed stored html, top-level `<br>` in a single run, run with an inline `<script>` (`_text`), tail after a bilingual element (`wrapper.tail`), run starting with an element (`parent.text or ''`), `en-US` source language (`split('-')`) | killed: the corresponding tests in `test_exporter.py::TestSurvivors` (the `href is None` part of the nav condition was removed instead: `_label_for` already refuses an empty href) |
| check script: `COPIED`, anchors (`root`, `id`, `name`), `_resolve` (`scheme or netloc`, `target = base`), `href`, `resolved[0]`/`[1]`, `duplicate_ids` key, TOC label walk and normalization, epubcheck command and `capture_output`/`text`, per-ID `+=`, per-chapter `+= 1` (3), the chapter loop's `continue`s, non-UTF-8 stylesheet (`'replace'`), `--projects-dir` default, failure counting and `'; '` join in the unmatched branch, `totals['kept'] +=`, source ID list join | killed: `TestExportCheckSurvivors` (default directory, copied files, new valid links and ids, a link after an unparsed member, two chapters per problem, `toc_labels`, non-UTF-8 stylesheet, two unmatched projects with a changed directory, totals over two projects) and `TestExportCheckEpubcheckSurvivors` |
| check script: SVG names through `QName`; `result` initial keys; `epubcheck.get(fmt, '-')` | code changed: attribute names compared as stored; the chinese-only counts are taken in the loop (no initial values); plain indexing |
| the rest (exporter 101/103, 106, 166, 169, 175/176, 475/485, 477/487/522, 502/503, 532/539/544; script 21, 90, 107, 188, 236, 241, 383) | equivalent, same as run 2 below |

Run 2's survivors (ids of run 2) and timeouts:

| id | location | mutation | disposition |
|---|---|---|---|
| 96, 98 | `exporter.py:185` | the source language `''` → `'XXXX'` when `dc:language` is empty or missing | equivalent unless an element carries `lang="XXXX"`: the value is only compared with `lang` attributes by primary subtag, and `XXXX` is not a language tag |
| 101 | `exporter.py:187` | `css_path = None` → `""` | equivalent: only its truth value is used |
| 152 | `exporter.py:249` | the nodes before the root serialized `with_tail=True` | equivalent: lxml keeps no text outside the root element, so they have no tail |
| 155, 455, 465 | `exporter.py:250, 599, 619` | `copy.deepcopy` → `copy.copy` of an element | equivalent: lxml's `_Element.__copy__` copies the whole subtree as well (checked in Phase 2, mutant 31 there) |
| 161, 162 | `exporter.py:257` | the temporary file's prefix or suffix | equivalent: only its uniqueness in the output directory matters; it is replaced or removed on every path (`test_output_directory_created_and_no_temporary_file`, `test_failed_write_leaves_nothing`) |
| 244–246, 260–262 | `exporter.py:337, 350` | `changed += 1` → `= 1`, `-= 1`, `+= 2` | equivalent: `changed` starts at 0 and is only used for its truth value, which each of these makes true (the initial value and the increment as such are killed) |
| 457, 467, 502 | `exporter.py:600, 620, 678` | `tail = None` → `""` | equivalent: an empty tail and no tail serialize the same |
| 482, 483 | `exporter.py:641, 642` | `_put(div, 0, …)` → `_put(div, 1, …)` | equivalent: the two divs are new and empty, and inserting at an index past the end appends |
| 512, 519, 524 | `exporter.py:694, 696, 698` | `''.join` → `'XXXX'.join` of the strings between two nodes | equivalent: the item lists alternate strings and nodes by construction (`_split`, `_lines`, `_take_run`, the code piece), so each joined list holds at most one string |
| timeout 139, 201 | `exporter.py:233, 304` | `while … in` → `not in` for the free stylesheet name / manifest id | detected: the loop never ends |
| 21 | `phase3_export_check.py:66` | `sys.path.insert(0, …)` → `insert(1, …)` | equivalent under default interpreter path settings (as Phase 0 survivor 113 and Phase 2 102) |
| 60 | `phase3_export_check.py:141` | `continue` → `break` after an unparsed member in `broken_links` | killed after run 2: `TestExportCheckSurvivors::test_link_checked_after_an_unparsed_member` now breaks a member that sorts before the one with the link (the nav document of the first version sorted last). The test passes on the code and fails with the mutant applied by hand to a `git archive` copy; no third full run |
| 89, 222 | `phase3_export_check.py:167, 279` | missing `class` / `rel` read as `'XXXX'` | equivalent unless a chapter uses the class or rel value `XXXX` |
| 200, 227 | `phase3_export_check.py:263, 281` | missing `href` of a head `<link>` read as `'XXXX'` | equivalent unless a member is named `XXXX` next to the chapter: without an href the link resolves to the chapter itself, which is not the stylesheet and does not define the bilingual classes |
| 106 | `phase3_export_check.py:191` | the epubcheck report file's suffix | equivalent: a temporary file in the system temporary directory, removed after reading (`test_report_file_removed_and_not_next_to_the_book`) |
| 358 | `phase3_export_check.py:375` | the exports directory name inside the work directory | equivalent: any name inside the work directory that differs from the copy's (`test_work_directory_removed_and_outside_repository` checks that every export lies in the work directory) |

Run 3 (fix round: the anchor rule, the container TOC rule, the bilingual epubcheck rule, the
TOC denominator). Every mutant in the new lines was killed except two. The 32 survivors are
the 31 of run 2 under new ids — exporter 99, 101 (run 2: 96, 98), 104 (101), 155 (152), 158,
475, 485 (155, 455, 465), 164, 165 (161, 162), 247–249, 263–265 (244–246, 260–262), 477, 487,
522 (457, 467, 502), 502, 503 (482, 483), 532, 539, 544 (512, 519, 524); script 21 (21), 89,
241 (89, 222), 219, 246 (200, 227), 119 (106), 389 (358), with the dispositions of the run 2
table — minus run 2's script 60, now killed, plus:

| id | location | mutation | disposition |
|---|---|---|---|
| 275, 276 | `phase3_export_check.py:318` | the initial `'into': 0` renamed or set to 1 | code changed after run 3: the initial value is removed (the chinese-only iteration, always first, sets it, as it already did for the other counts); the two mutants no longer exist. The suite passes after the change; no fourth full run |
| timeout 142, 204 | `exporter.py:235, 306` | `while … in` → `not in` | detected (run 2's 139, 201) |

Runs 4 and 5 (review round 1). Run 4 stopped in its last group (scripts): mutmut's clean
baseline failed because `test_two_stylesheets_added_to_bilingual` depended on set order (with
two added stylesheets the head check may report too); the test now checks only its own
problem. Its other groups' survivors (ids of run 4), handled before run 5:

| id | location | mutation | disposition |
|---|---|---|---|
| 100, 148, 152, 195 | `exporter.py` | the messages for output = source, OPF subset, write error, unwritable name | killed: `TestRound1Survivors::test_messages`, `test_opf_subset_message` |
| 216 | `exporter.py:337` | mode `0o666` → `0o667` | killed: `test_output_file_mode_follows_the_umask[0-438]` (umask 0) |
| 299 | `exporter.py:425` | `entries +=` → `=` (a second toc nav) | killed: `test_two_toc_navs_both_relabelled` |
| 308, 309 | `exporter.py:429` | the equal-label comparison's joins | killed: `test_equal_label_compared_normalized` |
| 369 | `exporter.py:527` | root `lang` read as `XXlangXX` in EPUB 2 | killed: `test_existing_root_lang_set_in_epub2[fr]` (an `en` root is now also rewritten by the ancestor rule) |
| 405 | `exporter.py:567` | `el in h.iterancestors()` → `not in` | killed: `test_id_holding_one_heading_of_several` |
| 658 | `exporter.py:910` | `' '.join` of the kept note types | killed: `test_note_types_dropped_from_the_block_copy[*-footnote one two-one two]` |
| suspicious 7 | `blocks.py:144` | `prev = child` → `None` | reported as slow, not survived; applied by hand to a copy, `tests/test_exporter.py` fails |
| 192, 197 | `exporter.py:315, 318` | name flag mask `0x800` → `2049` | equivalent: the extra bit is "encrypted"; an encrypted member cannot be read, so the export fails before any name is written |
| 214 | `exporter.py:334` | `os.umask(0)` → `os.umask(1)` | equivalent: the call only reads the umask and the next line restores it |
| 296, 652 | `exporter.py:424, 906` | missing `epub:type` read as `XXXX` | equivalent: `XXXX` is neither `toc` nor a note type |
| 390 | `exporter.py:556` | `first_heading = False` → `None` | equivalent: only its truth value is used |
| the other 21 and timeouts 157, 255 | `exporter.py` | as run 3 (source language `XXXX`, `css_path = ""`, `with_tail`, `copy.copy`, `tail = ""`, `_put(…, 1, …)`, `'XXXX'.join`, temporary file names, `changed` increments; `while … not in`) | equivalent or detected, as in the run 2 table |

Run 5: 1259 mutants, 1215 killed, 42 survived, 2 timeouts, 0 suspicious.

```
$ scripts/verify/mutation.sh main
...
TOTAL mutants 1259  killed 1215  survived 42  timeout 2  suspicious 0  untested 0
$ echo $?
1
```

| survivors (run 5) | disposition |
|---|---|
| exporter: the 27 equivalent survivors and 2 timeouts of run 4's last two rows | equivalent / detected, as above |
| script 22 (`sys.path.insert(1, …)`), 94 and 326 (missing `class` / `rel` as `XXXX`), 105 (missing `epub:type` as `XXXX`), 150 (epubcheck report suffix), 278 and 282 (flag mask `2049`), 305 and 331 (missing `href` as `XXXX`) | equivalent, for the reasons of the run 2 table and of 192/197 above |
| script 124 (`navMap` ancestor test negated), 252 (`'mimetype'` first in the expected order), 285 and 287 (the `mimetype` exception compares compression and extra field), 379 (the TOC entries message), 481 (`before = None` → `""`) | killed after run 5: `TestExportCheckRound1Survivors` (a navPoint outside the navMap; a source with `mimetype` last, deflated, with an extra field, which must pass; an added TOC entry; a project whose snapshot fails). Each mutant was applied by hand to a copy of the worktree and `tests/test_verify_scripts.py` fails; no sixth full run |

The exports-directory-name mutant of runs 2 and 3 (358 / 389) no longer survives: the scratch
directories are now `<n>/project` and `<n>/exports`, which
`TestExportCheckReviewRound1::test_scratch_directories_numbered` checks, so changing either name is
killed.

Mutation tool change (after run 5, at the owner's request): `scripts/verify/mutation.sh` now
runs mutmut 3.8.1 through `scripts/verify/mutation.py` (Python 3.10+; the repository's venv
interpreter 3.12 was upgraded from mutmut 2.5.1). mutmut 3 records which tests reach which
function and runs each mutant against those tests only, on all cores, in a copy of the working
tree (the repository is no longer edited during a run). It has more mutation operators
(argument removal, `None` arguments, string case). Run 6 on the same commit as run 5's
survivor tests:

| run | tool | mutants | killed | survived | timeout | suspicious | time |
|---|---|---|---|---|---|---|---|
| 5 | mutmut 2.5.1, groups in sequence | 1259 | 1215 | 42 | 2 | 0 | about 2 h 30 min |
| 6 | mutmut 3.8.1, one run | 2535 | 2403 | 129 | 3 | 0 | about 7 min |

Run 6's survivors were triaged in review round 2 (runs 7–10 below). Scope of the tool
(review round 2, F6; recorded, not worked around): mutmut 3.8.1 mutates only functions and
methods — module-level statements and decorated functions (Flask routes, click commands such as
`translate.py export`, properties) are not mutated even on changed lines; the changed-line
filter selects the node mutmut mutates, so a mutated call may change an argument on an
unchanged continuation line; and a test is linked to a function only when the function runs
in the test's own process, so code reached only through a subprocess is run without that test.
The run fails closed: exit 0 only when every mutant is killed (any other status, known or not,
fails it); `git diff --no-color --no-ext-diff`; on SIGTERM, SIGHUP or Ctrl-C mutmut's process
group is stopped and the copy removed (checked by sending SIGTERM to a run: no process and no
temporary directory left).

Runs 7–10 (review round 2, mutmut 3 through `mutation.sh main`; about 8 minutes each):

| run | after | mutants | killed | survived | timeout | suspicious | untested |
|---|---|---|---|---|---|---|---|
| 7 | "fix: make the mutation run fail closed and stop cleanly on signals" | 2601 | 2460 | 135 | 6 | 0 | 0 |
| 8 | "test: kill the mutmut 3 survivors of run 7" | 2613 | 2505 | 103 | 5 | 0 | 0 |
| 9 | "test: a big-endian UTF-16 chapter with an internal subset" | 2613 | 2507 | 101 | 5 | 0 | 0 |
| 10 | "test: a processing instruction in a translated run is no anchor" (final) | 2613 | 2507 | 100 | 6 | 0 | 0 |

```
$ scripts/verify/mutation.sh main
...
TOTAL mutants 2613  killed 2507  survived 100  timeout 6  suspicious 0  untested 0
$ echo $?
1
```

Killed after run 7 (`TestRound2Survivors` in `tests/test_exporter.py`,
`TestExportCheckRound2Survivors` in `tests/test_verify_scripts.py`): processing
instructions, whose pseudo-attributes lxml returns from `.get()`, are never anchors, TOC
targets or languages (`iter(None)` mutants of `_anchors`, `_relang`, `_toc_targets`'s body
walk, `_relabel`'s id test); a `<pre>` with one non-code child; a package without
`dc:language`; source-language `lang` inside bilingual copies and runs; NCX navPoints without
`content` or `navLabel`; `_without_zip64` (six inputs); the stylesheet deflated; the temporary
file next to the output; another compression becomes deflated; UTF-8 BOM and UTF-16 (BOM
either order, no BOM either order) chapters with an internal subset; no warnings during an
export (catches a `ZipInfo` without name); in the check script two broken links counted,
labels only from the NCX and nav files (an SVG `text` is no label; a `text` directly under the
NCX root), `head_items` without head, a changed or renamed file in the project directory, the
listing error with the path.

Run 10's 100 survivors and 6 timeouts, all with a disposition:

| survivors (run 10 ids) | mutation | disposition |
|---|---|---|
| exporter `_fragment` 12, `_member_info` 11, 15, `_serialize` 21, 32, 43, 49, `has_internal_subset` 28–31 (codec names) | codec or `encoding` name in another case (`'UTF-8'`, `'CP437'`, `'UNICODE'`, `'UTF-16-LE'`) | equivalent: Python and lxml look encodings up without case |
| exporter `_serialize` 16, 19, 22, 38, 41, 44 | `with_tail` of the nodes before and after the root | equivalent: lxml keeps no text outside the root element |
| exporter `_serialize` 30, `_code_piece` 25, `_fill` 16 | `copy.deepcopy` → `copy.copy` | equivalent: lxml's `__copy__` copies the subtree |
| exporter `_code_piece` 26, `_fill` 17, `_take_run` 13 | `tail = None` → `""` | equivalent: no tail and an empty tail serialize the same |
| exporter `_fill` 78, 86 | `_put(div, 1, …)` | equivalent: the div is new and empty; inserting past the end appends |
| exporter `_put` 14, 22, 33 | `''.join` → `'XXXX'.join` | equivalent: the lists alternate strings and nodes, so at most one string is joined |
| exporter `_relabel` 106–108 | `changed += 1` variants | equivalent: only its truth value is used |
| exporter `_export` 58, 59 | source language `XXXX` | equivalent unless an element has `lang="XXXX"` |
| exporter `_export` 61, 162 | `css_path = ""`; `css_path if css_used or True` | equivalent: only its truth value is used; the OPF is rewritten only when a chapter was filled, so `css_used` is then true whenever `css_path` is set |
| exporter `_fill` 68, `_link_stylesheet` 12, 14, `_package` 47, 49 | the new `div` / `head` / `item` without namespace | equivalent in the output: lxml serializes a no-namespace child of a default-namespace parent without `xmlns=""`, so the bytes are the same (checked) |
| exporter `_mark_translated` 1, 21, `_toc_targets` 7 | `iter(etree.Element)` → `iter(None)` where processing instructions only add keys or are read for `id` / `epub:type` | equivalent: a PI's `attrib` is a copy (deleting from it changes nothing); a PI with an `epub:type` pseudo-attribute does not exist in XHTML; `owner` gets unused PI keys |
| exporter `_mark_translated` 25, `_relabel` 69 | missing `epub:type` read as `XXXX` | equivalent: `XXXX` is neither a note type nor `toc` |
| exporter `_toc_targets` 25 | `first_heading = False` → `None` | equivalent: truth value |
| exporter `_member_info` 13, 19 | name flag mask `0x800` → `2049` | equivalent: the extra bit is "encrypted"; an encrypted member cannot be read, so the export fails before |
| exporter `_write_archive` 14, 15 | length of the random temporary name | equivalent |
| exporter `has_internal_subset` 11, 14, 15, 17, 20 | the `'<\x00'` key renamed (falls back to the declared `UTF-16`, which Python decodes little-endian on this host); `decode` error handler changed or dropped; `lstrip('XX\ufeffXX')` | equivalent: little-endian host; libxml2 has already decoded the bytes, so no decoding error occurs; the BOM is still stripped |
| check script `main` 2–5, 8, 11, 16–18, 21, 24, 29–31 (the `--help` and description texts) | texts changed or dropped | subprocess gap: `TestExportCheck::test_runs_as_script_with_help` runs the script as a subprocess and checks these texts; mutmut 3 does not link it to `main`. Confirmed by hand for 29 (help text) and 5 (description): applied to a copy, that test fails |
| check script `main` 15 | default `PROJECTS` | equivalent on this machine's case-insensitive file system (`test_default_projects_dir` passes); killed on a case-sensitive one |
| check script `main` 66, 68 | `mkdtemp(dir=parent)` → default | equivalent: the default is `gettempdir()`, of which `parent` is the real path |
| check script `_entries` 10, 22, 27, 37, `broken_links` 7, `id_counts` 5, `uses_bilingual` 8, `Archive.anchors` 4 | `iter(None)`: processing instructions also walked | equivalent for the checks: PIs have no local name, and an `id`/`href`/`class` pseudo-attribute of a PI would be the same in source and export, which are compared |
| check script `_entries` 21, `_links_bilingual_css` 15, 27, `chapter_problems` 25, `uses_bilingual` 7 | missing attribute read as `XXXX` | equivalent unless a value is `XXXX` (see run 2) |
| check script `_entries` 45 | `point` computed for a root `text` element | equivalent unless a document's root element is `text` |
| check script `_links_bilingual_css` 29, `toc_labels` 14 | the dummy fragment `#x` → `#X` | equivalent: only the member part is used |
| check script `_links_bilingual_css` 45, `epubcheck_errors` 22, 24, 26 | `'UTF-8'`, or the locale's encoding for the JSON report | equivalent (case; the locale is UTF-8) |
| check script `broken_links` 23 | `_resolve(name, None)` for elements without `href` | equivalent: `urlsplit(None)` gives no fragment, so `None` is returned as before |
| check script `epubcheck_errors` 7–9 | report file suffix | equivalent (run 2) |
| check script `member_problems` 47, 55 | flag mask `2049` | equivalent (encrypted, as above) |
| check script `snapshot` 13, 15 | paths relative to the working directory instead of the project | equivalent: still unique and the same before and after |
| timeouts: exporter `_free_name` 8, `_package` 28, `_without_zip64` 31–33, check script `epubcheck_errors` 4 | infinite loops (`not in`, `i = 4 + size`, …); `which('EPUBCHECK')` finds the tool on this case-insensitive file system and the run exceeds the timeout | detected |

The run 4 table's mutant 214 (`os.umask(1)`) no longer exists: review round 2 (F2) removed the
`os.umask` calls.

### 7. Known consequences

- Schema 1 projects can no longer be exported; `translate.py migrate <project>` first (the
  8 real projects are all schema 1).
- The exporter needs `source_sha256`: a schema 2 project without a stored hash (none is
  created that way by the CLI, the web UI or the migration) cannot be exported.
- 104 of 4162 translated segments of the real projects are exported in the original language
  (section 4) until they are re-translated or edited, or Phase 4 brings retries.
- Inner markup comes from the translation: inline elements and their attributes are the
  model's (Phase 4); only the anchors (ids, `a@name`) are required to survive. The bilingual
  copies keep the model's inner hrefs, so a broken link of the source is broken twice.
- Bilingual output repeats the source's markup errors in the translated copies; the check
  script allows at most one repetition per source error (section 5).
- Bilingual runs separated by `<br>` become one row each; the `<br>` elements stay between the
  rows (an extra line break each). Comments inside a run move into `div.original`
  (bilingual) or are dropped (chinese-only). The newlines that the rule splits on are not kept
  in the output.
- TOC labels: 406 of 1885 real entries change (only the `navMap` and the toc nav are
  relabelled; page lists, landmarks and other lists keep their labels); of the 527 that point into translated
  chapters, 121 stay in the source language, 114 of them because the target has no heading
  (section 4).
- In chinese-only a block's own attributes are the source's even when the model changed them
  (e.g. `class`, `id` on the top-level element); the language attributes that matched the
  source language become `zh-TW`.
- `epub3` is decided by the OPF `version` starting with `3`; EPUB 2 copies get only
  `xml:lang` (deviation from the plan's wording `lang="zh-TW"`, which is not valid XHTML 1.1).
- The first commit of the branch ("feat: record where an inline run sits …") also carries the
  deletion of the builder, its tests and their data, which had been staged earlier by mistake;
  the tree builds and passes from the third commit on. History was not rewritten (no amend or
  rebase).
- `CLAUDE.md` and `README.md` still describe the ebooklib builder (not edited by this phase).
- Language, known limitations recorded in review round 1 (A11, no code): in chinese-only a
  block kept in the original language inside a chapter whose root or ancestor is now `zh-TW`
  inherits `zh-TW`; inline spans the model left in the source language inside a filled block
  are retagged `zh-TW` when they carry a source-language `lang`; the language of the navigation
  document and NCX roots is unchanged.
- An OPF with a DOCTYPE internal subset cannot be exported in a bilingual format (the
  stylesheet must be registered); in chinese-only it keeps its `dc:language`.
- Export takes the project lock: it is refused while another process (a CLI translation or
  another web server) holds it.

### 8. Command for exports of one project copy

Produces the three exports of one real project in a directory of one's choice, without
touching `projects/` (`<name>` is the project's directory, `<dir>` the chosen directory):

```
D=<dir>/copy; mkdir -p "$D"
for f in project.json source.epub glossary.json; do [ -f "<repo>/projects/<name>/$f" ] && cp "<repo>/projects/<name>/$f" "$D/"; done
cd <repo> && source venv/bin/activate
python3 translate.py migrate "$D"
for f in chinese-only bilingual-horizontal bilingual-vertical; do python3 translate.py export -p "$D" -f $f; done
ls "$D/output"    # copy-chinese-only.epub copy-bilingual-horizontal.epub copy-bilingual-vertical.epub
```

### 9. `projects/` untouched

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

Before the work and after every run of the check script. The script also FAILs a project when
any file under its directory changes. Exports, migrations, lock files and epubcheck reports
were only written in temporary directories, deleted afterwards. The read-only scratch
diagnostics of sections 4 (reasons, TOC steps, ids, epubcheck messages) ran on a separate copy
in the session's scratch directory, deleted at the end.

### 10. Review round 1

Findings of the GPT and Opus reviews (A–D) and of the Gemini answer (E), and what was done.
Where a case was ambiguous the source is left untouched (segment kept original, label
unchanged) rather than adding logic.

| # | finding | action | tests |
|---|---|---|---|
| A1 | `output_path` could be the source EPUB | `ExportError` when the output exists and `os.path.samefile` it (symlinks and `./` paths included); the source bytes survive | `TestReviewRound1Archive::test_output_is_the_source` |
| A2 | a DOCTYPE internal subset is dropped by `_serialize`; comments/PIs after the root are lost | a chapter whose DOCTYPE has an internal subset is not filled: bytes untouched, every accepted segment reported as `chapter_doctype` (new reason, chapter line in the report); nodes after the root are written after it | `TestReviewRound1Chapters::test_internal_subset_kept[*]`, `test_nodes_after_the_root_kept[*]` |
| A3 | anchor gate was "source ⊆ translation" | the multiset of `id` / `a@name` values inside each block must be equal in its translation (no extra, repeated or copied own id); reason renamed `missing_anchor` → `anchor_mismatch` | `TestAnchorsInTheGate::test_anchor_mismatch_kept_original[*]` (extra id, repeated id, the block's own id added) |
| A4 | page lists, nav lists and landmarks were relabelled | only the NCX `navMap` `navPoint`s and the `nav` whose `epub:type` holds `toc`; the old test asserting a `pageTarget` change now asserts it is kept | `TestReviewRound1Toc::test_only_the_toc_nav`, `test_only_the_nav_map`, `TestToc::test_labels_from_translated_headings[*]`, `TestSurvivors::test_nav_list_targets_not_relabelled` |
| A5 | a target got the block's first heading | each id maps to its own heading (the heading that is it or holds it, else the one heading it holds; none or several → unchanged), the translated block must have as many headings as the source block, the k-th is used; ids assigned in document order, the first occurrence wins (also when it has no label); the container rule takes only the innermost container (the first block's direct parent) | `TestReviewRound1Toc::test_each_id_its_own_heading`, `test_heading_count_changed_leaves_labels`, `test_first_occurrence_of_a_duplicate_wins`, `test_duplicate_whose_first_is_not_a_heading`, `TestTocContainerTarget::test_first_block_heading[*]` |
| A6 | an equal label was rewritten (dropping its markup); a label holding an id lost it | no change when the whitespace-normalized label text already equals the new label; a label with a descendant `id` is left as it is | `TestReviewRound1Toc::test_equal_label_left_as_it_is`, `test_label_with_an_id_left_as_it_is` |
| A7 | `<pre>  <code>…</code></pre>`: the text outside `<code>` was written twice | the fill target's text must equal the stored code text, else `code_markup` | `TestReviewRound1Chapters::test_code_text_outside_the_target_not_duplicated`, `TestCode::test_code_with_markup_kept[*]` |
| A8 | non-ASCII names without the UTF-8 flag were rewritten as UTF-8 | the name is written as the bytes zipfile decoded (UTF-8 when flagged, else code page 437) with the source's flag; a name that cannot be encoded so is an `ExportError` | `test_non_utf8_name_bytes_kept`, `test_utf8_flagged_name_kept`, `test_name_that_cannot_be_written_back` |
| A9 | untouched members lost metadata; mode 0o600 | every written member (also replaced ones and `mimetype`) copies date, external and internal attributes, create system and versions, extra field, comment and the name flag; `mimetype` is stored and without extra field (OCF); a duplicated name is written once at its first position with its last entry's data and metadata (the one zipfile reads by name); output mode `0o666 & ~umask` | `test_untouched_member_metadata_copied[*]`, `test_mimetype_metadata_copied`, `test_output_file_mode_follows_the_umask`, `test_zero_external_attributes_kept` |
| A10 | control characters in code crashed; corrupt member / OSError escaped | a code translation lxml refuses → `not_well_formed`; read and write errors while writing → `ExportError`; the CLI prints any other exception as one line and exits 1 | `test_code_with_control_characters_kept`, `test_corrupt_member_is_an_export_error`, `test_write_error_is_an_export_error`, `test_cli_unexpected_error` |
| A11 | `<body xml:lang="en">` stayed `en` in chinese-only | the parent and ancestors of each filled block get source-language `lang`/`xml:lang` rewritten; known limitations recorded in section 7 | `test_source_language_on_ancestors_rewritten[*]`, `test_bilingual_ancestors_keep_their_language[*]` |
| A12 | readers hiding notes hid the translated row | the bilingual copy drops `footnote`, `endnote`, `rearnote`, `note` from its `epub:type` (other values stay; the original keeps all) | `test_note_types_dropped_from_the_block_copy[*]`, `test_chinese_only_keeps_note_types` |
| B1 | the export tab showed reason codes, and nothing when opened after the export | the status `summary` carries the report `lines` (the CLI's wording, chapter lines included); the tab shows them, and shows the summary or error from the status on load | `TestCLIAndWeb::test_web_status_has_the_summary`, `test_export_page_shows_the_summary` |
| C1 | check script gaps | external/unresolvable hrefs give no target (no TypeError); only real TOC entries counted, compared by position within their file, and the entry list must be the same; labels outside the TOC must not change; every source member must be present; untouched members compared by raw name, order, compression, date, attributes, system, extra and comment; `name` is an anchor only on `a`; duplicate ids compared by count; the project snapshot is compared in a `finally`; scratch directories are `<n>/project` and `<n>/exports`; setup failures are reported by exception type only | `TestExportCheckReviewRound1` (11), `TestExportCheck::test_source_member_missing[*]`, `TestExportCheckSurvivors::test_toc_labels` |
| C2 | `placed_chapters` used its own filter | uses the exporter's `chapter_items` and `load_chapter` (spine XHTML not nav; missing, unreadable, not well-formed, without body, unresolved entity → not placed) | `TestMigrateCopiesCheck::test_chapters_the_exporter_refuses_not_placed[*]` |
| C3 | stale references to the deleted Phase 0 script | the comment is now self-contained in `phase1_load_projects.py` and `phase2_roundtrip.py` | — |
| D1, D2 | acceptance log | "Not verified" replaced; this table; limitations in section 7 | — |
| E1 | the CLI export did not take the project lock | `export_project` holds the project lock while it runs (CLI and web); a lock held by another process → `ExportError` with the lock message, CLI exit 1. Note: the web export did not take it either before this round | `TestReviewRound1Additions::test_cli_refused_while_another_process_holds_the_lock`, `test_lock_released_after_export` |
| E2 | internal subsets in the OPF, NCX and nav | NCX/nav with one are never rewritten (labels unchanged); an OPF with one is left unchanged in chinese-only (`dc:language` stays) and is an `ExportError` when a bilingual stylesheet must be registered | `test_opf_subset_refused_for_bilingual[*]`, `test_opf_subset_left_for_chinese_only`, `test_toc_subset_left[*]` |
| E3 | container rule only for a direct child heading | already so after A5 (the first block's parent must be the container) | `TestTocContainerTarget::test_first_block_heading[*]` (labels B and D unchanged) |
| E4 | a `<br>` line whose translation holds only a text-less anchor | a piece whose source block has text must have text (applied to every block of a text segment, not only runs) → `structure_mismatch` | `test_line_without_text_kept[*]`, `test_element_without_text_kept`, `test_textless_source_line_may_stay_textless` |
| E5 | namespace map of the first block only | the declarations in scope at every block's parent are merged | `test_namespace_declared_on_a_nested_container[*]` |
| E6 | check script: stylesheets added | none allowed in chinese-only, at most one in bilingual | `TestExportCheckReviewRound1::test_stylesheet_added_to_chinese_only`, `test_two_stylesheets_added_to_bilingual` |

C1 note: the directory-name mutant is discussed in section 6.

Found by the real-data check of this round (not in the review): zipfile writes an external
attribute value 0 as `0o600 << 16`; 124 members of three books have 0. `_member_info` passes 0
as an int whose truth value is true so it is written as 0 (`test_zero_external_attributes_kept`).

Known limitations (A11, recorded without code):

- chinese-only: a block kept in the original language inside a chapter whose root (or an
  ancestor) is now `zh-TW` inherits `zh-TW`;
- chinese-only: inline spans the model left in the source language inside a filled block are
  retagged `zh-TW` when they carry a source-language `lang`;
- the language of the navigation document and NCX roots is unchanged.

### 11. Review round 2

Opus approved with minors; GPT requested changes (one major). Each fix has a test that fails
without it.

| # | finding | action | tests |
|---|---|---|---|
| F1 (major) | note types were dropped only from the copy's root (`<td epub:type="footnote">` inside a copied table stayed hidden) | dropped from every element of the bilingual copy's subtree; originals untouched; other tokens kept | `TestReviewRound2::test_note_types_dropped_throughout_the_copy[*]` |
| F2 | `os.umask` called at export time (process-wide, the web server is threaded) | the temporary file is created with `os.open(…, O_CREAT \| O_EXCL \| O_WRONLY, 0o666)` under a random sibling name, so the kernel applies the umask; the run 4 justification of mutant 214 is obsolete (section 6) | `test_umask_not_called`, `test_output_file_mode_follows_the_umask[*]` |
| F3 | subset detection by a byte regex (misses UTF-16, false positives in comments) | the suggested `docinfo.internalDTD is not None` is true for every DOCTYPE (checked: lxml creates it also without a subset and exposes no subset content), so it cannot be used. Instead: the source is decoded (UTF-16 by byte order mark or first character, else the encoding the parser reports) and its prolog read with comments and processing instructions skipped | `test_utf16_internal_subset_kept`, `test_doctype_text_in_comments_is_no_subset`, `TestRound2Survivors::test_internal_subset_found_in_any_encoding[*]` |
| F4 | a copied extra field kept the source's ZIP64 record | the record (tag 1) is dropped, other records kept | `test_zip64_record_dropped_from_the_extra_field`, `TestRound2Survivors::test_without_zip64[*]` |
| F5 | a failing final snapshot escaped the check script | caught: the project FAILs with `project snapshot raised <type>`, the others are still checked | `TestExportCheckReviewRound2::test_final_snapshot_failure_reported_and_others_checked` |
| F6 | `mutation.py` exited 0 on unknown statuses; `git diff` options; signals | exit 0 only when every mutant is killed; `--no-color --no-ext-diff`; SIGTERM/SIGHUP/Ctrl-C stop mutmut's process group and remove the copy; the tool's scope is recorded in its docstring and in section 6 | checked by hand: SIGTERM during a run leaves no process and no temporary directory |
| F7 | triage the mutmut 3 survivors | runs 7–10 and the table in section 6: every survivor killed or justified; the subprocess-gap group is justified by `test_runs_as_script_with_help`, confirmed by hand for two of them | section 6 |

Also changed while triaging: `_Kept` no longer passes its reason to `Exception` (unused); the
unused `compress_type` for a missing `mimetype` is gone (a new `ZipInfo` is stored).

### Not done

- The full structural validation (keyed, required ids) is Phase 4; the reduced check is the
  gate until then.
- `CLAUDE.md` and `README.md` are not updated.

### Not verified

- Reader check (by the orchestrator, after the fix round): the three formats of an invented
  sample project were exported through the web UI on this branch, epubcheck ran on them (0
  errors; source 0), and the chapters were opened in a browser — source stylesheet applied,
  both bilingual layouts correct, TOC label and package language correct. A browser check on
  copies of the real projects follows after review round 1 and will be added by the
  orchestrator if needed.
- The export tab's new report lines (B1) were checked as rendered page source and through the
  status API with the Flask test client, not in a browser.
- epubcheck's message-level comparison (section 5, sets of ID and text) and the TOC
  classification of section 4 were read-only scratch scripts on copies, not kept; the kept
  script compares by ID and count only.
- Mutation testing ran on Python 3.12.7, the suite on 3.9.6 (both pass).
