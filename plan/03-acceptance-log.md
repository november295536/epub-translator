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
1408 passed in 144.17s (0:02:24)
```

(After review round 4; 1407 after review round 3; 1386 after review round 2, 1341 after review round 1.)

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

The output above is after review round 1 (section 10); after review rounds 2 and 3 (sections
11, 12) the same command printed the identical lines and exited 0 (H1's stricter anchor rule
keeps no further real segment); after the fix round the totals were
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

Runs 11 and 12 (review round 3):

| run | after | mutants | killed | survived | timeout | suspicious | untested |
|---|---|---|---|---|---|---|---|
| 11 | "fix: drop the ZIP64 record in the check's comparison and stop the whole mutmut group" | 2626 | 2528 | 92 | 6 | 0 | 0 |
| 12 | "test: a changed a@name value is an anchor mismatch" (final) | 2626 | 2531 | 90 | 5 | 0 | 0 |

```
$ scripts/verify/mutation.sh main
...
TOTAL mutants 2626  killed 2531  survived 90  timeout 5  suspicious 0  untested 0
$ echo $?
1
```

Killed in review round 3 (section 12): the five no-namespace mutants of runs 7–10 (`_fill`
`_bilingual(fmt, None)`, `_link_stylesheet` `QName(None, 'head')` / `QName('head')`, `_package`
`QName(None, 'item')` / `QName('item')`) by the prefixed XHTML and OPF tests (their earlier
"equivalent in the output" disposition held only for default-namespace documents); the check
script's processing-instruction mutants of `broken_links`, `id_counts`, `uses_bilingual` and
`Archive.anchors` by `TestExportCheckReviewRound3`; the two `_anchors` name mutants of run 11
by a changed `a@name` value.

Run 12's 90 survivors and 5 timeouts, all with a disposition:

| survivors (run 12 ids) | mutation | disposition |
|---|---|---|
| exporter `_fragment` 12, `_member_info` 11, 15, `_serialize` 21, 32, 43, 49, `has_internal_subset` 14, 17, 20, 23, 26 | codec or `encoding` name in another case (`'UTF-8'`, `'CP437'`, `'UNICODE'`, `'UTF-16-LE'`, `'LATIN-1'`) | equivalent: Python and lxml look encodings up without case |
| exporter `has_internal_subset` 31–33 | the `'replace'` error handler changed or dropped | equivalent: it matters only when decoding fails, and latin-1 never fails; UTF-16 is used only for bytes libxml2 has already parsed as UTF-16 |
| exporter `_serialize` 16, 19, 22, 38, 41, 44 | `with_tail` of the nodes before and after the root | equivalent: lxml keeps no text outside the root element |
| exporter `_serialize` 30, `_code_piece` 25, `_fill` 16 | `copy.deepcopy` → `copy.copy` | equivalent: lxml's `__copy__` copies the subtree |
| exporter `_code_piece` 26, `_fill` 17, `_take_run` 13 | `tail = None` → `""` | equivalent: no tail and an empty tail serialize the same |
| exporter `_fill` 78, 86 | `_put(div, 1, …)` | equivalent: the div is new and empty; inserting past the end appends |
| exporter `_put` 14, 22, 33 | `''.join` → `'XXXX'.join` | equivalent: the lists alternate strings and nodes, so at most one string is joined |
| exporter `_relabel` 106–108 | `changed += 1` variants | equivalent: only its truth value is used |
| exporter `_export` 58, 59 | source language `XXXX` | equivalent unless an element has `lang="XXXX"` |
| exporter `_export` 61, 162 | `css_path = ""`; `css_path if css_used or True` | equivalent: only its truth value is used; the OPF is rewritten only when a chapter was filled, so `css_used` is then true whenever `css_path` is set |
| exporter `_mark_translated` 1, 21, `_toc_targets` 7 | `iter(etree.Element)` → `iter(None)`, so processing instructions are walked too | equivalent: a PI's `attrib` is a copy, so deleting from it changes nothing (checked); no XHTML PI carries an `epub:type` pseudo-attribute; in `_toc_targets` PIs only become unused keys of `owner` |
| exporter `_mark_translated` 25, `_relabel` 69 | missing `epub:type` read as `XXXX` | equivalent: `XXXX` is neither a note type nor `toc` |
| exporter `_toc_targets` 25 | `first_heading = False` → `None` | equivalent: truth value |
| exporter `_member_info` 13, 19 | name flag mask `0x800` → `2049` | equivalent: the extra bit means "encrypted"; an encrypted member cannot be read, so the export fails before any name is written |
| exporter `_write_archive` 14, 15 | length of the random temporary name | equivalent |
| check script `main` 2–5, 8, 11, 16–18, 21, 24, 29–31 (the `--help` and description texts) | texts changed or dropped | subprocess gap: `TestExportCheck::test_runs_as_script_with_help` runs the script as a subprocess and checks these texts; mutmut 3 does not link it to `main`. Confirmed by hand for 29 (help text) and 5 (description) in run 8: applied to a copy, that test fails |
| check script `main` 15 | default `PROJECTS` | equivalent on this machine's case-insensitive file system (`test_default_projects_dir` passes); killed on a case-sensitive one |
| check script `main` 66, 68 | `mkdtemp(dir=parent)` → default | equivalent: the default is `gettempdir()`, of which `parent` is the real path |
| check script `_entries` 10, 22, 27, 37 | `iter(None)`: processing instructions also walked | equivalent: a PI has no local name, so it never matches `nav`, `a` or `text`, the only elements these loops use |
| check script `_entries` 21, `_links_bilingual_css` 15, 27, `chapter_problems` 25, `uses_bilingual` 7 | missing attribute read as `XXXX` | equivalent unless a value is `XXXX` (see run 2) |
| check script `_entries` 45 | `point` computed for a root `text` element | equivalent unless a document's root element is `text` |
| check script `_links_bilingual_css` 29, `toc_labels` 14 | the dummy fragment `#x` → `#X` | equivalent: only the member part is used |
| check script `_links_bilingual_css` 45, `epubcheck_errors` 22, 24, 26 | `'UTF-8'`, or the locale's encoding for the JSON report | equivalent (case; the locale is UTF-8) |
| check script `broken_links` 23 | `_resolve(name, None)` for elements without `href` | equivalent: `urlsplit(None)` gives no fragment (checked), so `None` is returned as before |
| check script `epubcheck_errors` 7–9 | report file suffix | equivalent (run 2) |
| check script `member_problems` 47, 55 | flag mask `2049` | equivalent (encrypted, as above) |
| check script `snapshot` 13, 15 | paths relative to the working directory instead of the project | equivalent: still unique and the same before and after |
| timeouts: exporter `_free_name` 8, `_package` 28, `_without_zip64` 32, 33, check script `epubcheck_errors` 4 | infinite loops (`not in`, `i -= 4 + size`, …); `which('EPUBCHECK')` finds the tool on this case-insensitive file system and the run exceeds the timeout | detected |

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
- A UTF-32 chapter with an internal DOCTYPE subset is not detected (review round 4; UTF-32 is
  not conforming for EPUB).
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

### 12. Review round 3

Opus and Gemini approved with minors; GPT requested changes (one major). Each fix has a test
that fails without it (H3: only on Python 3.12, see below).

| # | finding | action | tests |
|---|---|---|---|
| H1 (major) | ids and `a@name` values were counted in one multiset: source `<a id="n" name="n">` and translation `<a id="n"/><a id="n"/>` both gave n=2 and passed, with a duplicate id | two multisets, ids and `a@name` values, each must equal the source's; a `name` turned into an `id` (accepted before) is now a mismatch too | `TestAnchorsInTheGate::test_anchor_mismatch_kept_original[*]` (the H1 case, `name` → `id`, a changed `name` value) |
| H2 | a declared encoding Python does not know (`ISO-Latin-1`) raised `LookupError` and failed the export | everything that is not UTF-16 is decoded as latin-1 (the prolog structure is ASCII; a UTF-8 BOM is skipped first) | `TestReviewRound3::test_encoding_python_does_not_know[*]` (with and without a subset) |
| H3 | the raw name came from `info.filename`, which Python 3.12 takes from an Info-ZIP Unicode Path field (0x7075) | built from `info.orig_filename`. Observed: with UTF-8 name bytes, no UTF-8 flag and a 0x7075 field, Python 3.9 gives `filename == orig_filename` (code page 437 reading), Python 3.12 gives `filename` from the field. The test therefore fails without the fix on 3.12 (where mutmut runs) and passes either way on 3.9 | `TestReviewRound3::test_stored_name_bytes_kept_with_a_unicode_path_field` |
| H4 | the check script compared `extra` with the source's ZIP64 record, which the exporter drops | the source's extra is compared without its ZIP64 record (the exporter's `_without_zip64`) | `TestExportCheckReviewRound3::test_source_member_with_a_zip64_record_passes` |
| H5 | no tests with a prefixed XHTML or OPF namespace | added; the code was already correct (no change). They kill the five no-namespace mutants, whose "equivalent" disposition (section 6) held only for default namespaces | `TestReviewRound3::test_prefixed_xhtml_without_head[*]`, `test_prefixed_opf[*]` |
| H6 | the check script's processing-instruction survivors | focused tests with PI pseudo-attributes (`class`, `id`, `name`, `href`); the four mutants of `broken_links`, `id_counts`, `uses_bilingual`, `Archive.anchors` are killed; the remaining `_entries` ones are reworded accurately (section 6) | `TestExportCheckReviewRound3::test_pi_*` |
| H7 | `mutation.py` cleanup | further SIGTERM/SIGHUP are ignored once cleanup starts; the recorded process group is signalled whether or not its leader has exited; members still alive after the grace period get SIGKILL; then the copy is removed | `TestMutationScript::test_stop_group_after_the_leader_exited` |

### 13. Review round 4

Verdicts: Opus approve, GPT approve, Gemini approve; only minors remained. Final small pass.

| # | finding | action | tests |
|---|---|---|---|
| J1 | `mutation.py` cleanup could be cut short by Ctrl-C, and a raising `stop_group` would leave the copy | SIGINT is ignored too once cleanup starts; `stop_group` runs in `try … finally`, which removes the copy | — (two lines; the cleanup path is exercised by `test_stop_group_after_the_leader_exited` and was checked by hand in round 2) |
| J2 | the check script removed the ZIP64 record only from the source's extra field | removed from both operands | `TestExportCheckReviewRound3::test_export_member_with_a_zip64_record_passes` |
| J3 | `test_stop_group_after_the_leader_exited` assumed the child is reaped within one pause; `proc.stdout` left open | polls with a 10 s deadline; closes `proc.stdout` | the test itself |
| J4 | limitation | recorded below and in section 7 | — |

Mutation testing after this pass: run 13 stopped at mutmut's clean baseline (this pass had
dropped an `import zipfile` from `test_untouched_member_compression_changed`; restored in "fix:
restore a test import dropped in the final review pass"). Run 14:

```
$ scripts/verify/mutation.sh main
...
TOTAL mutants 2629  killed 2533  survived 90  timeout 6  suspicious 0  untested 0
$ echo $?
1
```

Against run 12: the three new mutants (J2's line) are killed; the 90 survivors are the same
mutations as run 12's, with the dispositions of section 6; `_without_zip64`'s `i = 4 + size`,
killed in run 12, is a timeout here (an infinite loop either way: detected).

Known limitation (not fixed): a UTF-32 chapter with an internal DOCTYPE subset is not detected
by `has_internal_subset` (UTF-32 is decoded there as UTF-16 or latin-1, so the subset is not
seen and the chapter could be filled, dropping the subset). UTF-32 is not a conforming encoding for EPUB
content documents.

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


## Phase 4 — Protection, validation, retry

Date: 2026-11-20. Branch: `phase-4/protect-validate-retry`, from `main` after Phase 3. No new
dependency. Commands run in the worktree of the branch with `source <repo>/venv/bin/activate`
(Python 3.9.6 for the suite and the scripts, 3.12.7 for mutmut, as before). The real projects
are read from `<repo>/projects`; nothing under it was written (section 8).

Owner decision applied (it replaces the "optional key" rule of the first version of 4.1):
inline tags must be preserved. The count of every tag name in the translation must equal the
source's, with or without attributes; any mismatch is structural (blocking). The order of
inline tags is not checked. Section 4.1 and the Phase 4 text of `plan/02-improvement-plan.md`
now state this as the design.

### 1. What was built

| Item | Where | Main names |
|---|---|---|
| Protection | `src/core/validation.py` (new) | `protect(html)` → `Protected(html, keys)`; `restore(text, protected)`; `top_level_units(html)`; `WRAPPER = 'run'` |
| Validation | `src/core/validation.py` | `check_html(source, translation, fingerprints=None, nsmap=None)`, `check_code(source, translation)`, `check_segment(seg, text)` → `Result(issues, pieces)`; rule codes `STRUCTURAL_CODES`, `QUALITY_CODES`; `LENGTH_RATIO_BAND`, `LENGTH_RATIO_MIN_CHARS`; the block cutting moved here from the exporter (`groups`, `lines`, `source_shape`, `translation_pieces`, `fragment`, `declarations`) |
| Retry | `src/core/translator.py` | `MAX_CALLS = 4`; `TranslationEngine(max_calls=…)` (was `max_retries`); `_Session` (budget, usage, diagnostics); `FEEDBACK_PROMPT`; `TEXT_HTML_PROMPT` rewritten for keys and `<run>`; `TranslationError.issues`; `Reply.issues` (quality warnings) |
| Export | `src/epub/exporter.py` | `_pieces` calls `check_code` / `check_html` (the chapter's real namespace declarations, the blocks' fingerprints); the reduced check (`_split`, `_lines`, `_text`, `_fragment`, `_anchors`, `_has_content`) and `ANCHOR_MISMATCH` are removed; `REASON_TEXT` covers every structural rule |
| Review | `src/web/blueprints/review.py` | save and approve run `check_segment`; a blocking failure answers 422 with the details and changes nothing; warnings of a saved text are stored on the segment |
| Project | `src/core/project.py` | `mark_failed(segment_id, issues)`; `update_translation(…, issues=None)` |
| CLI | `translate.py validate <project> [--mark-failed]` | section 3 |
| UI | `src/web/templates/project/translation.html` | `refreshFailedCount()` (called when a run completes, stops, fails, or was already over when the page opened) also sets the header bar's width and text from `/api/projects/<name>/progress` |
| Check script | `scripts/verify/phase4_validate_check.py` (new) | section 4 |
| Mutation scope | `scripts/verify/mutation.py` | `FILES` gains `src/core/validation.py` and the new script |

Per item of the brief:

1. **Protection.** A regular expression splits the stored string into tokens (comments, CDATA,
   processing instructions, declarations, end tags, start tags with their attribute text, and
   the text between them). A start tag gets a key when it is a top-level block element (depth
   0, local name in `BLOCK_TAGS`) or has any attribute text (namespace declarations included);
   `<p class="a" id="b">` becomes `<p k="1">` and the key remembers the exact attribute text
   (` class="a" id="b"`). A top-level run of inline content — from its first text character or
   element to its last, so the `\n` between blocks stays outside — is wrapped in
   `<run k="N">…</run>`. `restore` gives each `<name k="N">` whose key exists for that tag name
   its attribute text back (keeping the reply's own `>` or `/>`) and drops the wrapper's start
   and end tag (matched with a stack); any other tag is left as written, so validation sees
   it. Nothing is parsed or re-serialized, so `restore(protect(html)) == html` exactly
   (9491/9491 stored segments, section 4). The prompt explains the keys, that tags without a
   key keep their count per tag name and may move, that `<run>` stays and keeps its lines.
2. **Validation** — section 2.
3. **Retry.** `translate_reply` → `_Session.attempt`: the request; when the reply fails a
   blocking rule, one follow-up (`[user, assistant, user]`, the last message
   `FEEDBACK_PROMPT` with one line per blocking issue; `CLIBackend` flattens the three turns
   into one `claude -p` prompt, as built in Phase 1). If that fails too, the HTML has two or
   more top-level units and at least two calls are left, the request is split once at the
   middle unit boundary: each half is protected, sent once (no follow-up), restored and
   checked on its own (without fingerprints), then the halves are joined with the original
   separator and the whole is checked again (with fingerprints). A transient error repeats the
   same request while calls are left; every call counts. `RateLimitError`, `AuthError`,
   `BackendUnavailableError` and `CancelledError` raise at once and are not counted (they end
   or pause the run, as before). At most four calls. A segment that still fails is `FAILED`
   with the blocking issues of its last check (kind `structure`, or `backend` for
   `not_html`, `empty_reply`, `transient`, `refusal`, `error`) and is exported in the source
   language; `apply_outcome` (Phase 1) keeps the previous text and status of a translated or
   reviewed segment and only attaches the issues.
4. **Refusal or not HTML.** A `RefusalError` from the backend fails the segment at once
   (backend issue `refusal`, no retry, as before). For an HTML segment a reply whose first
   character (after the existing commentary/fence cleanup) is not `<` is a backend issue
   `not_html` and is retried with feedback like any other rejected reply; it is never stored
   (a refusal written as prose, e.g. "I cannot help with that.", is one).
5. **Code.** Accepted only when `code_lines(source) == code_lines(translation)`: comment lines
   removed from both (a line whose first non-blank characters are `//`, or `#` followed by
   whitespace, `!` or the end of the line — Phase 0's definition, so `#include` is code),
   trailing whitespace removed, blank lines at either end ignored. An end-of-line comment is
   part of its code line, so translating it is refused (the code prompt already asks for
   comment lines only). A failing code reply gets the one follow-up; code is never split.
   No passthrough marking (Phase 6).
6. **Three places, one check** — section 2.
7. **`validate`** — section 3.
8. **Plan.** 4.1: the key bullets now say every key is required and tags without a key keep
   their count per tag name (only order may change); the structural rule row says so; the
   quality row no longer lists "optional key dropped"; a paragraph says one comparison serves
   all three places on the restored form. Phase 4 states that inline tag counts are blocking
   and names `validate`. Section 8 ("What to protect", "Thresholds") follows; the per-project
   threshold overrides that were never built are no longer mentioned.
9. **Header progress bar** — the table above. Checked as page source (test), not in a
   browser.

### 2. The check (items 2 and 6)

**One comparison, on the restored form.** A stored translation has real attributes, a model
reply has keys. Rather than re-keying stored text, a reply is restored first (after a check
that the reply in its prompt form is well-formed, so a stray `</p>` cannot close a wrapper),
and then every place runs the same function on two HTML strings with real attributes:
`check_html(source, translation, fingerprints, nsmap)`.

1. Both are parsed as XML fragments (content of a wrapper element) under the same namespace
   declarations: at export the chapter's (the blocks' parents'), elsewhere `declarations()` —
   the XHTML default namespace plus a placeholder URI for every prefix that occurs in either
   string (only the prefix matters, since both sides use the same map).
2. **Cutting into blocks.** Top-level nodes are grouped: each block element is a group, the
   text and inline nodes between them one run group; a top-level `<br>` is refused. With the
   segment's fingerprints (schema 2) a run group whose text is not the next block's is cut at
   its newlines (runs that were separated by `<br>` in the chapter are stored one per line),
   and every source piece must have its block's fingerprint, else `ambiguous_source`. Without
   fingerprints (schema 1) a run group is one piece. The translation must have the same groups
   (same number, a block element of the same tag for each block element, a run group cut into
   exactly as many lines with content). This is Phase 3's rule; the shape now comes from the
   stored HTML and the fingerprints instead of the chapter tree, so the same function works
   without the chapter (the export first checks, as before, that the chapter's blocks have
   those fingerprints).
3. **Per block, the elements.** Every element of a source piece and of its translation piece
   (the block element itself included) is reduced to (tag, sorted attributes). The two
   multisets must be equal. In keyed terms that is: every key appears exactly once in its
   block on the same tag name (an attribute-bearing element), every tag without a key occurs
   as often per tag name, and nothing else has attributes. Order inside a block is not
   compared; comments are ignored.

Rules and codes (one issue per code per segment, the detail lists every block and tag):

| Rule | Code | Kind |
|---|---|---|
| not well-formed | `not_well_formed` | structure, blocking |
| top-level groups differ from the blocks | `structure_mismatch` | structure, blocking |
| a block with text has none | `empty_block` | structure, blocking |
| a tag name the source segment does not have | `unknown_tag` | structure, blocking |
| an attribute-bearing element missing or repeated in its block (a key not exactly once) | `key_mismatch` | structure, blocking |
| an element with attributes that no source element of its block has (an attribute other than `k`) | `extra_attributes` | structure, blocking |
| a tag without attributes occurs more or less often in a block | `tag_count` | structure, blocking |
| the stored source cannot be cut into its blocks | `ambiguous_source` | structure, blocking |
| code differs outside comment lines | `code_changed` | structure, blocking |
| identical to the source (whitespace removed), which has a word of two letters or more | `untranslated` | quality, warning |
| translation/source characters (whitespace removed) outside `LENGTH_RATIO_BAND = (0.25, 0.8)`, sources of at least 200 characters | `length_ratio` | quality, warning |
| reply empty / not starting with `<` | `empty_reply`, `not_html` | backend, blocking |

Phase 3's `anchor_mismatch` is covered by `key_mismatch` / `extra_attributes` (ids and `name`
are attributes); `empty_block` was Phase 3's text rule under `structure_mismatch`.

**Where it runs.** (a) After every model call (`_html_check` → `check_html` with the segment's
fingerprints; code → `check_code`). (b) Review save and approve (`check_segment`): a blocking
failure answers 422 `{'error': '譯文未通過結構檢查：…', 'issues': [...]}` and nothing changes (the
review page already shows the error with `alert('未儲存：…')`); approve checks the given text or,
without one, the stored text; an empty text is not checked (it clears a translation; export
treats it as untranslated); a saved text's warnings are stored as its issues, an approval
clears them (a person's decision, as before). (c) Export: `_pieces` runs `check_code` or
`check_html` on every accepted segment; stored issues are not read. The first blocking code
is the "kept original" reason. The exporter's own reduced check is gone.

**Length ratio band.** Measured on the 2925 translated non-code segments that pass the
structural check and whose source has at least 200 characters (section 4): p1 0.312, p5
0.333, p50 0.404, p95 0.547, p99 0.976. Below 0.29 there are 5 segments (lowest 0.23); above
0.8 there are 37, and their translations are on median 73% ASCII letters (reference lists and
text left in English), against 11% for the bulk. The band (0.25, 0.8) flags 38 (1.3%): the one
very short translation and the mostly-English ones. (0.15, 0.65) was measured first and
flagged 65 (2.2%); between 0.65 and 0.8 the translations are on median 45% ASCII letters,
many of them ordinary translations with English terms kept in parentheses.

### 3. `translate.py validate <project> [--mark-failed]`

Read-only by default (no lock, writes nothing): every translated or reviewed segment with
non-blank text is checked with `check_segment` (the stored HTML and fingerprints, derived
namespace declarations: the export's check without the chapter-level checks), and the counts
are printed — segments failing per structural rule (a segment can count under several),
then warnings per quality rule. Exit 0.

With `--mark-failed` (under the project lock, refused while another process holds it): each
failing segment gets status `failed` and its blocking issues; **its text is kept**; one save.
Consequences, all by existing code:

- `start --retry-failed` (CLI) and "retry failed segments" (web) queue them
  (`get_pending_segments(include_failed=True)`); a normal start does not.
- A retry that succeeds replaces the text, status `translated` (a formerly reviewed segment
  loses its `reviewed` mark: its text was broken).
- A retry that fails leaves status `failed`, the old text and the new issues
  (`apply_outcome`: a failure changes only the issues of a non-pending segment), so nothing is
  lost and it can be retried again.
- Export: a `failed` segment is exported in the source language, which is what the check gave
  it anyway. A person can still fix the kept text in the review page (a save that passes the
  check makes it `translated`).
- `validate` again counts only translated and reviewed segments, so the marked ones no longer
  appear.

Run on a temporary migrated copy of project-2 (removed afterwards):

```
$ python3 translate.py validate <copy of project-2>
已檢查 278 段譯文：54 段未通過結構檢查（匯出時以原文輸出），224 段通過
  not_well_formed：10
  structure_mismatch：7
  tag_count：37
品質警告（不影響匯出）：
  length_ratio：14
$ python3 translate.py validate <copy of project-2> --mark-failed
（the same lines）
已將 54 段標為翻譯失敗（原譯文保留），可用 start --retry-failed 重新翻譯。
$ python3 translate.py validate <copy of project-2>
已檢查 224 段譯文：0 段未通過結構檢查（匯出時以原文輸出），224 段通過
品質警告（不影響匯出）：
  length_ratio：14
$ python3 translate.py status --project <copy of project-2>      (last lines)
已翻譯：  224
失敗：    54
```

### 4. Check: round trip and the full check over the real projects

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase4_validate_check.py --projects-dir <repo>/projects --anonymize
PASS project-1: round trip 220/220, translated 220, failing the structural check 9 (key_mismatch 7, not_well_formed 2), warnings (length_ratio 1), length ratio over 205 segments: p1 0.312 p5 0.324 p50 0.359 p95 0.580 p99 0.700; outside 0.25-0.8: 1 (0.5%)
PASS project-2: round trip 278/278, translated 278, failing the structural check 54 (not_well_formed 10, structure_mismatch 7, tag_count 37), warnings (length_ratio 14), length ratio over 218 segments: p1 0.351 p5 0.360 p50 0.402 p95 1.104 p99 1.186; outside 0.25-0.8: 14 (6.4%)
PASS project-3: round trip 4385/4385, translated 36, failing the structural check 2 (structure_mismatch 1, tag_count 1), warnings (none), length ratio over 13 segments: p1 0.363 p5 0.363 p50 0.471 p95 0.580 p99 0.580; outside 0.25-0.8: 0 (0.0%)
PASS project-4: round trip 2236/2236, translated 1333, failing the structural check 57 (empty_block 14, extra_attributes 9, key_mismatch 10, not_well_formed 2, structure_mismatch 31), warnings (untranslated 6), length ratio over 1102 segments: p1 0.342 p5 0.365 p50 0.422 p95 0.484 p99 0.514; outside 0.25-0.8: 2 (0.2%)
PASS project-5: round trip 839/839, translated 832, failing the structural check 38 (code_changed 19, key_mismatch 3, structure_mismatch 1, tag_count 16), warnings (none), length ratio over 430 segments: p1 0.312 p5 0.334 p50 0.394 p95 0.516 p99 0.631; outside 0.25-0.8: 0 (0.0%)
PASS project-6: round trip 581/581, translated 581, failing the structural check 3 (extra_attributes 1, key_mismatch 3), warnings (length_ratio 16, untranslated 2), length ratio over 276 segments: p1 0.347 p5 0.381 p50 0.503 p95 0.885 p99 1.086; outside 0.25-0.8: 16 (5.8%)
PASS project-7: round trip 621/621, translated 551, failing the structural check 27 (key_mismatch 12, not_well_formed 6, structure_mismatch 9), warnings (length_ratio 4), length ratio over 458 segments: p1 0.321 p5 0.340 p50 0.380 p95 0.439 p99 0.732; outside 0.25-0.8: 4 (0.9%)
PASS project-8: round trip 331/331, translated 331, failing the structural check 16 (structure_mismatch 16), warnings (length_ratio 1), length ratio over 223 segments: p1 0.299 p5 0.308 p50 0.340 p95 0.380 p99 0.430; outside 0.25-0.8: 1 (0.4%)
TOTAL projects 8  round trip 9491/9491  translated 4162  failing the structural check 206 (code_changed 19, empty_block 14, extra_attributes 10, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 54)  warnings (length_ratio 36, untranslated 8)  length ratio over 2925 segments: p1 0.312 p5 0.333 p50 0.404 p95 0.547 p99 0.976; outside 0.25-0.8: 38 (1.3%)  failed projects 0
$ echo $?
0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

The script copies `project.json`, `source.epub` and `glossary.json` of each project into a new
`mktemp` directory under the system temporary directory (refused before anything is created
when that would be inside the repository or the projects directory), migrates the copies (all
8 are schema 1; the same code as Phase 3's script), runs the checks and removes the directory
(also on failure). A project FAILs when a round trip does not hold, the migration does not
match, anything raises, or a file under its real directory changed. It prints counts, rule
names and ratios only. 9491 = the 9521 legacy segments after the 29 merge groups of Phase 2.

The numbers the owner asked for: **of the 4162 existing translations, 206 fail the blocking
check**. **88 of them fail only because inline tags or attributes were dropped, added or
changed** (`tag_count`, `key_mismatch`, `extra_attributes`; project-1 7, project-2 37,
project-3 1, project-4 10, project-5 18, project-6 3, project-7 12; counted by a read-only
scratch script on copies, deleted). Of those 88, 5 were already kept as `anchor_mismatch` in
Phase 3. What they are (shapes only, same scratch script): `<em>` dropped or added
(project-2 40 occurrences, project-5 17 — the old plain-text prompt told the model to render
emphasis as 「」), `<span>` with attributes dropped (project-1 9, project-6 3, project-7 8),
`<a>` with attributes (project-5 2, project-7 5), and in project-4 an inner `<p>` holding a
page-break id lost (9) or given another class (1). The other 118: `code_changed` 19 (all in
project-5; every one is model commentary stored as code, e.g. an English or Chinese sentence
saying the block has no comments, or a fence — real damage), and the 99 Phase 3 already kept
(`not_well_formed` 20, `structure_mismatch` 65, `empty_block` 14).

Quality: 36 `length_ratio` + 8 `untranslated` warnings. 38 segments are outside the band; 2 of
them get `untranslated` instead (a warning per segment: identical text is reported as such).

### 5. Check: exports before → after

```
$ python3 scripts/verify/phase3_export_check.py --projects-dir <repo>/projects --anonymize
epubcheck found; errors compared by message ID (chinese-only, bilingual-horizontal, bilingual-vertical)
PASS project-1: exports 3, translated segments 220: as translation 211, kept original 9 (key_mismatch 7, not_well_formed 2), toc labels changed 0/22 (22 into translated chapters), epubcheck errors source 51 (HTM-004:24 NCX-001:1 RSC-005:26), exports 51/51/51
PASS project-2: exports 3, translated segments 278: as translation 224, kept original 54 (not_well_formed 10, structure_mismatch 7, tag_count 37), toc labels changed 0/35 (33 into translated chapters), epubcheck errors source 27 (OPF-034:1 RSC-005:25 RSC-020:1), exports 27/28/28
PASS project-3: exports 3, translated segments 36: as translation 34, kept original 2 (structure_mismatch 1, tag_count 1), toc labels changed 18/1357 (22 into translated chapters), epubcheck errors source 0, exports 0/0/0
PASS project-4: exports 3, translated segments 1333: as translation 1276, kept original 57 (empty_block 14, key_mismatch 10, not_well_formed 2, structure_mismatch 31), toc labels changed 123/144 (126 into translated chapters), epubcheck errors source 11 (RSC-012:11), exports 11/22/22
PASS project-5: exports 3, translated segments 832: as translation 794, kept original 38 (code_changed 19, key_mismatch 3, structure_mismatch 1, tag_count 15), toc labels changed 244/245 (245 into translated chapters), epubcheck errors source 470 (HTM-004:25 NCX-001:1 PKG-006:1 RSC-005:440 RSC-007:3), exports 466/511/511
PASS project-6: exports 3, translated segments 581: as translation 578, kept original 3 (key_mismatch 3), toc labels changed 19/19 (19 into translated chapters), epubcheck errors source 1 (RSC-005:1), exports 1/1/1
PASS project-7: exports 3, translated segments 551: as translation 524, kept original 27 (key_mismatch 12, not_well_formed 6, structure_mismatch 9), toc labels changed 1/45 (44 into translated chapters), epubcheck errors source 151 (RSC-005:151), exports 151/183/183
PASS project-8: exports 3, translated segments 331: as translation 315, kept original 16 (structure_mismatch 16), toc labels changed 0/18 (16 into translated chapters), epubcheck errors source 27 (RSC-005:26 RSC-007:1), exports 27/27/27
TOTAL projects 8  exports 24  translated segments 4162  as translation 3956  kept original 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 53)  toc labels changed 405/1885 (527 into translated chapters)  exports with new epubcheck errors 0  failed projects 0
$ echo $?
0
```

As translation / kept original: **4058 / 104 → 3956 / 206**. Reasons before: `anchor_mismatch`
5, `not_well_formed` 20, `structure_mismatch` 79; after: `code_changed` 19, `empty_block` 14,
`key_mismatch` 35, `not_well_formed` 20, `structure_mismatch` 65, `tag_count` 53. The kept
segments are exactly the 206 of section 4 (the export reports one reason per segment, the
first blocking code, so a project-5 segment failing both `key_mismatch` and `tag_count` counts
once). 79 = 65 + 14 (`empty_block` was part of `structure_mismatch`). The script needed no
change (it prints whatever reasons the report holds). TOC labels changed 406 → 405: one
heading of project-5 is now kept in the source language. project-5's bilingual epubcheck
count 509 → 511 is within the bilingual rule (no new ID, at most twice the source's).

### 6. Test suite

```
$ python3 -m pytest tests/ -q -p no:cacheprovider
1575 passed in 130.22s (0:02:10)
```

1408 before the phase. New: `tests/test_validation.py` (protection, restore, the rules, the
quality rules, code, `check_segment`, survivors); `TestPhase4Protection`, `TestPhase4Retry`,
`TestPhase4BackendIssues`, `TestPhase4Code`, `TestPhase4SurvivorsRun1` in
`tests/test_translator.py`; `TestPhase4FullCheck` in `tests/test_exporter.py`;
`TestPhase4ReviewValidation`, `TestPhase4HeaderProgress` in `tests/test_web.py`;
`TestPhase4Validate` in `tests/test_cli.py`; `TestPhase4MarkFailed` in `tests/test_project.py`;
`TestValidateCheck`, `TestValidateCheckSurvivors` in `tests/test_verify_scripts.py`.
`tests/conftest.py` gains `echo_reply`, `like` and `echo_process` (replies that pass the
check: the prompt's own HTML, or it with every text node replaced).

| Acceptance item | Tests |
|---|---|
| `restore(protect(html)) == html` | `TestProtect::test_round_trip_exact[*]` (12 shapes incl. comments, CDATA, PIs, prefixed names, unbalanced input, empty), the script (section 4) |
| keys and run wrapper | `TestProtect::test_plan_example`, `test_runs_wrapped`, `test_bare_top_level_block_keyed_inner_bare_tag_not`, `TestRestore::*`, `TestPhase4Protection::*` |
| structural rules, order free | `TestCheckHtml::*`, `TestPhase4FullCheck::*`, `TestAnchorsInTheGate::*` |
| quality rules non-blocking, band | `TestQuality::*`, `TestPhase4Protection::test_quality_warning_kept_with_the_translation` |
| retry with feedback, split, four calls, run-level errors not counted | `TestPhase4Retry::*` |
| refusal / not HTML is a backend issue, not stored | `TestPhase4BackendIssues::*`, `TestCodeResponseCleaning::test_text_commentary_only_reply_is_stored[<p>x</p>]` |
| failed re-translation keeps the previous text | `TestPhase4BackendIssues::test_failed_retranslation_keeps_the_previous_text`, `TestPhase4MarkFailed::test_failed_retry_keeps_the_old_text`, `TestPhase4Validate::test_mark_failed_then_retry` |
| code rule | `TestCode::*` (validation), `TestPhase4Code::*`, `TestCode::test_code_text_filled[*]`, `TestAnchorsInTheGate::test_code_checked_outside_comment_lines` |
| review save / approve | `TestPhase4ReviewValidation::*` |
| export does not trust stored issues | `TestPhase4FullCheck::test_stored_issues_not_trusted` |
| `validate` | `TestPhase4Validate::*` |
| header bar | `TestPhase4HeaderProgress::test_header_bar_refreshed_when_a_run_ends` |
| check script | `TestValidateCheck::*`, `TestValidateCheckSurvivors::*` |

No test was removed. Tests changed, and why:

| Tests | Change | Reason |
|---|---|---|
| fake replies and mocked CLI output in `test_translator.py`, `test_cli.py`, `test_web.py` (`'譯文'`, `'翻譯結果'`, `'<p>譯</p>'` for an `<h2>` segment, …) | valid HTML for the segment (`<p>譯文</p>`, `echo_reply`, `like(request, …)`) | a plain-text or wrong-tag reply is now refused, as intended |
| texts posted to the review endpoints in `test_web.py`, the `project_with_translations` fixture's stored texts, and the assertions on them | wrapped in the segment's tag | the endpoints now refuse broken text; the fixture's approve-without-text path checks the stored text |
| prompts searched for `<p>Sentence MARK2.</p>`, `<h1>Introduction</h1>`, `<p>One sentence.</p>` | the protected form (`<p k="1">…`) or `Sentence MARK2.</p>` | the prompt carries keys |
| attempt counts and messages (`3 attempts` → `4`, `[TransientError] * 3` → `* 4`, `test_default_three_attempts` → `test_default_four_calls`, `test_failure_is_an_outcome` cases, diagnostics content) | new numbers | four calls per segment; an empty reply gets one retry with feedback, not three identical ones |
| `max_retries=` | `max_calls=` (also in two test names) | the engine's parameter is the per-segment call budget now |
| `test_code_segment_sent_as_code`, `TestCode::test_code_text_filled`, `test_code_bilingual_copy` | comment lines instead of an end-of-line comment | an end-of-line comment is part of the code line (rule 5) |
| `RAW_TRANSLATIONS` (`<h1 id="c1">`, `<p class="first">`), `TestToc.ONE_TRANSLATED`, several exporter fixtures (`lang`, `class`, `epub:type` on block elements) | the source's block attributes | block attributes are part of the comparison; Phase 3 took them from the source silently |
| `test_inline_attributes_come_from_the_translation` | became `test_inline_attributes_must_match_the_source` | the old behaviour is what Phase 4 forbids |
| `TestAnchorsInTheGate::test_anchor_mismatch_kept_original` | expects one of `key_mismatch`, `extra_attributes`, `unknown_tag` instead of `anchor_mismatch`; the `<span name>` case moved here from the accepted list | `anchor_mismatch` is subsumed; any attribute counts |
| `test_code_not_checked` | became `test_code_checked_outside_comment_lines` | code is checked now |
| `test_br_runs_split_at_newlines`, `test_newline_inside_an_inline_element_does_not_split`, `test_run_with_inline_script` | translations keep the source's inline tags | otherwise `tag_count` / `unknown_tag` |
| `test_line_without_text_kept`, `test_element_without_text_kept` | reason `empty_block` instead of `structure_mismatch` | own code |
| `test_textless_source_line_may_stay_textless` | a translation adding the text-less line's `<img>` (not part of the segment) is now kept original (`unknown_tag`); the one without it exports | the old acceptance duplicated the image |
| `test_reason_texts_cover_every_reason`, `test_report_codes_texts_and_messages` | the new reason set and texts | |
| `test_text_commentary_only_reply_is_stored[<p>x</p>]` | with HTML it is now `not_html` | the Phase 1 "accepted limitation, left to Phase 4" |

### 7. Mutation testing

`scripts/verify/mutation.sh main` (mutmut 3.8.1, Python 3.12.7, only lines changed versus
`main`, all cores). Changed files mutated: `src/core/project.py`, `src/core/translator.py`,
`src/core/validation.py`, `src/epub/exporter.py`, `src/web/blueprints/review.py`,
`translate.py`, `scripts/verify/phase4_validate_check.py`.

| run | after | mutants | killed | survived | timeout | time |
|---|---|---|---|---|---|---|
| 1 | "docs: state that inline tag counts are blocking in the plan" | 1527 | 1431 | 96 | 0 | about 25 min (not timed; it shared the machine with the Phase 3 check) |
| 2 | "test: kill the Phase 4 mutation survivors of run 1" | 1485 | 1481 | 4 | 0 | 470 s |
| 3 | "test: kill two check-script survivors of Phase 4 mutation run 2" | 1485 | 1483 | 2 | 0 | 405 s |

```
$ scripts/verify/mutation.sh main
...
TOTAL mutants 1485  killed 1483  survived 2  timeout 0  suspicious 0  untested 0
$ echo $?
1
```

Run 1's 96 survivors and what was done:

| survivors (run 1) | disposition |
|---|---|
| translator: the mode string (`'html'`, `'text'`) and `source` passed to every call (13); `retry=False` → `None`; `_request_text`'s code guard (4); `declarations(protected.html, reply)` (2) | code changed: each kind of reply has its own clean function (`_clean_html`, `_clean_text`, a code closure), `attempt` takes a retry count, the reply is parsed under its own declarations, the diagnostics prompt always protects the HTML (the code prompt ignores it) |
| translator: feedback, log and diagnostics joins with two issues; `attempts[-1]`; the protected-form well-formedness check, its kind and detail; markdown kept in HTML replies (also in halves); prefixed elements in a reply | killed: `TestPhase4SurvivorsRun1` |
| validation: `check_segment`'s override parameters (3); `sorted(…, key=repr)` (2); `Result([], [])` for code (3); `.encode('utf-8')` case; `drop = False` → `None` | code changed: no overrides (callers pass none), plain tuple sort, `Result([])`, `.encode()`, `drop` computed as a boolean |
| validation: detail joins (`、`, `；`), ambiguous-source issue, untranslated detail, quality text joined over blocks (2), `declarations` of both texts (2), `xmlns` exclusion (2), the default namespace, comments inside inline elements, `_local` of a prefixed name, a run at the very start, depth tracking (3), a run after the last fingerprint, fingerprint cutting through `check_segment` | killed: `TestSurvivorsRun1` and `TestCheckSegment::test_segment_fingerprints_cut_the_runs` in `test_validation.py` |
| review: the joined error text | killed: `TestPhase4ReviewValidation::test_refusal_lists_every_problem` |
| `translate._validate`: `continue` → `break` | killed: `TestPhase4Validate::test_pending_failed_and_empty_not_checked` (now a translated segment follows the skipped ones) |
| check script: argparse help and default (17), docstring line (4) | killed in-process: `TestValidateCheckSurvivors::test_help_in_process`, `test_default_projects_directory` (the subprocess `--help` test is not linked by mutmut) |
| check script: skipped statuses, code left out of the ratios (2), `>=` 200, percentile rank, joins (2), the totals' ratios, `before = None`, failure count (3), snapshot names (3), `mkdtemp(dir=…)` (2) | killed: the extended fixture (a failed segment with text, a long code segment, a 200-character source) in `TestValidateCheck`, and `TestValidateCheckSurvivors` |

Run 2's survivors: the two below, plus two of the check script (`default='PROJECTS'`, equivalent
only on a case-insensitive file system such as this machine's; the docstring split
`'XX\nXX'`), killed by `TestValidateCheckSurvivors::test_default_projects_directory` (the
listed directory name) and `test_help_in_process` (no second docstring paragraph in the help).

Remaining survivors (run 3), each equivalent:

| mutant | mutation | why equivalent |
|---|---|---|
| `translator._Session.__init__` 4 | `self.prompt = ''` → `None` | read only as `if not self.prompt` before the first call sets it; every failure is raised after a call, so the stored prompt is never the initial value |
| `validation.top_level_units` 39 | `run = None` → `""` after a block element starts | `run` is only tested for truth (`if run`, `run[0] if run else …`); `""` and `None` are both false and `run` is reassigned before any index |

### 8. `projects/` untouched

Before and after every real-data run (sections 3, 4, 5), as above:

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

All real-data work was on copies in temporary directories outside the repository, removed
afterwards (checked: no `phase4-validate-*` or `phase3-export-*` directory left). The scratch
data used for the shape counts in section 4 was in the session's scratch directory and was
deleted.

### Known consequences

- 102 more existing translations are exported in the source language than in Phase 3 (88
  with changed inline tags or attributes, 19 code segments holding commentary, minus the 5
  already kept). `validate --mark-failed` then `start --retry-failed` re-translates them.
- An end-of-line comment in code (`x = 1  # set`) can no longer be translated; such a
  translation is refused and, after the retry, the code is exported unchanged.
- Attributes other than `alt`, `title` and `aria-label` are no longer sent to the model. Those
  three are read by people and are shown and translated (changed in review round 1; the first
  version of this phase hid them, so their text stayed in the source language).
- A reviewed segment marked failed by `validate --mark-failed` comes back `translated` after a
  successful retry, not `reviewed`.
- (Superseded in review round 2: each half is now checked with its share of the
  fingerprints.) The split's halves were checked without fingerprints; only the joined text
  was checked with them.

### Not done

- Glossary compliance (Phase 5), passthrough code and heading merging (Phase 6).
- Showing issues in the review page (Phase 5); the review only shows the 422 error.
- `CLAUDE.md` and `README.md` are not updated.

### Not verified

- No real `claude` call was made in this phase (the orchestrator runs one through the web UI
  on a synthetic project). The prompt wording for keys and `<run>` is therefore untested
  against the real model.
- The header progress bar fix was checked as page source with the Flask test client, not in a
  browser.
- The shape classification in section 4 (which tags, project-5 code replies) came from a
  read-only scratch script on copies, not kept; the kept script prints counts per rule only.
- Mutation testing ran on Python 3.12.7, the suite on 3.9.6 (both pass).

## Phase 4 — review round 1

Date: 2026-11-21. Branch: `phase-4/protect-validate-retry`, same worktree and environment as
the Phase 4 entry. One owner requirement (A) and the review's findings (B1–B14).

### A. Attributes people read are translated

Owner rule: whatever a person reads must be translated; what a machine reads need not.
`TRANSLATABLE_ATTRIBUTES = {'alt', 'title', 'aria-label'}` in `src/core/validation.py` is the
one list.

- `protect` shows these attributes next to the key, with their stored quote and raw value:
  `<img src="m.png" alt="A map"/>` → `<img k="2" alt="A map"/>`. Every other attribute stays
  behind the key. An element with only such attributes still gets a key (it has attribute
  text). A key is now a `Key(name, attrs, shown)`; `Protected` gains `top` (the top-level keys
  in order). `restore(protect(html)) == html` still holds exactly (9491/9491, section C).
- `restore` puts the stored attribute text back and, for each translatable attribute whose
  reply value differs from the stored one, replaces only that value inside it: the reply's
  value is decoded (the five XML entities and numeric references) and escaped for the stored
  quote (`&`, `<` and that quote). The other attributes, their order, spacing, quoting and
  entity forms are untouched; a value the reply repeats is left exactly as stored.
- Keyed form (`check_keys`): the reply may carry only `k` and the key's translatable
  attributes, each one present when the source has it (missing → `extra_attributes` "少了 alt
  屬性"; added → "多了 src 屬性"); a value empty in the source must stay empty.
- Stored form (`check_html`, used by review save/approve and export): an element's signature
  takes a translatable attribute's value as "free" when non-empty and as `''` when empty, so a
  translated alt passes and an emptied or filled-in empty one does not.
- An unchanged value is accepted with no issue (no warning was added).
- Prompt rule 5 now says alt/title/aria-label are text for readers and must be translated,
  keeping the attribute (empty stays empty), other attributes untouched. Plan 4.1 states the
  rule; the Phase 4 entry's consequence line about untranslated alt/title is corrected.
- Real data (counted by `phase4_validate_check.py`, every stored text segment, any status):
  **22 segments carry such attributes** (project-2 3, project-3 16, project-7 3; others 0).

### B. Findings

| # | Finding | Change |
|---|---|---|
| 1 | Key violations erased by `restore` | `check_keys(reply, protected)` runs on the well-formed keyed reply before `restore` (`translator._html_check`): every key exactly once on its tag name; top-level keys equal to `protected.top`, with no text between them; `<run k>` only at the top level; no attribute but `k` and the key's translatable ones; a bare tag with attributes refused. Swapped equal blocks, a key used twice, a missing key replaced by a bare tag, a nested or repeated `<run>` are refused (`structure_mismatch` / `key_mismatch`). The restored-form `check_html` still runs afterwards. `_SHARED_RULES` gets the first sentence-structure line per prompt: the plain-text prompt keeps "必要時可合併、拆分或調整語序"; the HTML prompt says sentences may be merged, split or reordered within a block but blocks may not. Tag rules say "同一個區塊內". Fake replies without keys in the tests now carry `k` (list in "Tests changed"). |
| 2 | Block root attributes could move to a descendant | `_compare_elements` compares the block element's own signature separately (`第 i 個區塊的 <x> 本身的屬性與原文不同`: `key_mismatch` when the source root has attributes, else `extra_attributes`), then the descendants' multiset. Tests in both directions at review save, approve and export. |
| 3 | `validate` on schema 1 | Refused with `SCHEMA_1_MESSAGE` (the export's message, which says to run `migrate` first), exit 1, with and without `--mark-failed`; nothing written. Workflow: **migrate first, then `validate [--mark-failed]`, then `start --retry-failed`.** |
| 4 | Asymmetric tag names | One name pattern `[^\W\d][\w:.\-]*` for start and end tags (letters of any script); the wrapper's end is matched by name on a stack. Round trips of `<é>x</é>`, `<span class=x>t</span>` (unquoted attribute: text, as before, end tag no longer pops the wrapper) and an invented non-ASCII tag inside a reply's `<run>` are tested. |
| 5 | `ambiguous_source` spent calls | `validation.source_issues(html, fingerprints)` is checked in `_translate_html` before any call: the segment fails at once (`Translation failed after 0 attempts: 原文無法切成各個區塊`, the prompt kept for the diagnostics). The check script counts uncuttable sources over every stored text segment: **0** on the real data. |
| 6 | Review API types and encodings | Save and approve: non-string `text` → 400 `text must be a string`; a body that is not a JSON object → 400. `fragment` encoding errors (`ValueError`, e.g. a lone surrogate) and control characters are `not_well_formed` in `check_html` and `check_code`, so review (422), CLI `validate` and export all report them (tests: review save/approve, `validate` and export on a hand-written `project.json` holding `\ud800`). Approve: whitespace-only or `null` `text` means "no text supplied"; when the resulting text is empty the approval is refused, 422 `沒有譯文可以核可`. |
| 7 | `check_code` and control characters | A translation holding a character XML cannot hold (`[^\t\n\r\x20-퟿-�\U00010000-\U0010FFFF]`) is `not_well_formed` at check time. |
| 8 | `CODE_PROMPT` comment definition | Now states the check's definition: after leading whitespace, `//`, or `#` followed by whitespace, `!` or the end of the line (`#include` is code); end-of-line comments are not translated. |
| 9 | `_PREFIX` on `x²:y` | `re.ASCII`: only ASCII names are taken as prefixes. |
| 10 | `_quality` word test | Done on the source text before whitespace removal (`A B` is not a word). |
| 11 | Header bar refreshes | Also when `fetchStatus()` finds `completed`/`failed`/`stopped`, when `startTranslation()` returns `completed`, and on `rate_limit`, `auth_error`, `backend_error`. Tested by running the page's rendered `translationController` in Node (stubbed `fetch`, DOM, `EventSource`): 10 transitions refresh the bar, 7 others leave it alone; with the previous template 7 of the 10 fail. Skipped when `node` is absent. |
| 12 | `validate` without `project.json` | `找不到專案：<dir> 裡沒有 project.json。`, exit 1, no traceback. |
| 13 | Split diagnostics | `check_html(..., first_block=N)`; the second half starts at `mid + 1`, so its issues name the segment's block numbers (corrected in review round 2: this held only when no multi-line run precedes the split). A failed outcome carries the usage of its calls (`TranslationError.usage` → `TranslationOutcome.usage`); `apply_outcome` still does not store it on the segment (the stored usage belongs to the kept text). |
| 14 | The 88 figure | `phase4_validate_check.py` prints "only tag rules N" per project and in the total (`TAG_RULES`: unknown_tag, key_mismatch, extra_attributes, tag_count). |

Not changed, as instructed: the `not_html` rule, commented commands such as `# yum install`,
the settled decisions.

### C. Checks

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase4_validate_check.py --projects-dir <repo>/projects --anonymize
PASS project-1: round trip 220/220, translated 220, failing the structural check 9 (key_mismatch 7, not_well_formed 2; only tag rules 7), warnings (length_ratio 1), length ratio over 205 segments: p1 0.312 p5 0.324 p50 0.359 p95 0.580 p99 0.700; outside 0.25-0.8: 1 (0.5%), sources that cannot be cut 0, with translatable attributes 0
PASS project-2: round trip 278/278, translated 278, failing the structural check 54 (not_well_formed 10, structure_mismatch 7, tag_count 37; only tag rules 37), warnings (length_ratio 14), length ratio over 218 segments: p1 0.351 p5 0.360 p50 0.402 p95 1.104 p99 1.186; outside 0.25-0.8: 14 (6.4%), sources that cannot be cut 0, with translatable attributes 3
PASS project-3: round trip 4385/4385, translated 36, failing the structural check 2 (structure_mismatch 1, tag_count 1; only tag rules 1), warnings (none), length ratio over 13 segments: p1 0.363 p5 0.363 p50 0.471 p95 0.580 p99 0.580; outside 0.25-0.8: 0 (0.0%), sources that cannot be cut 0, with translatable attributes 16
PASS project-4: round trip 2236/2236, translated 1333, failing the structural check 57 (empty_block 14, key_mismatch 10, not_well_formed 2, structure_mismatch 31; only tag rules 10), warnings (untranslated 6), length ratio over 1102 segments: p1 0.342 p5 0.365 p50 0.422 p95 0.484 p99 0.514; outside 0.25-0.8: 2 (0.2%), sources that cannot be cut 0, with translatable attributes 0
PASS project-5: round trip 839/839, translated 832, failing the structural check 38 (code_changed 19, key_mismatch 3, structure_mismatch 1, tag_count 16; only tag rules 18), warnings (none), length ratio over 430 segments: p1 0.312 p5 0.334 p50 0.394 p95 0.516 p99 0.631; outside 0.25-0.8: 0 (0.0%), sources that cannot be cut 0, with translatable attributes 0
PASS project-6: round trip 581/581, translated 581, failing the structural check 3 (key_mismatch 3; only tag rules 3), warnings (length_ratio 16, untranslated 2), length ratio over 276 segments: p1 0.347 p5 0.381 p50 0.503 p95 0.885 p99 1.086; outside 0.25-0.8: 16 (5.8%), sources that cannot be cut 0, with translatable attributes 0
PASS project-7: round trip 621/621, translated 551, failing the structural check 27 (key_mismatch 12, not_well_formed 6, structure_mismatch 9; only tag rules 12), warnings (length_ratio 4), length ratio over 458 segments: p1 0.321 p5 0.340 p50 0.380 p95 0.439 p99 0.732; outside 0.25-0.8: 4 (0.9%), sources that cannot be cut 0, with translatable attributes 3
PASS project-8: round trip 331/331, translated 331, failing the structural check 16 (structure_mismatch 16; only tag rules 0), warnings (length_ratio 1), length ratio over 223 segments: p1 0.299 p5 0.308 p50 0.340 p95 0.380 p99 0.430; outside 0.25-0.8: 1 (0.4%), sources that cannot be cut 0, with translatable attributes 0
TOTAL projects 8  round trip 9491/9491  translated 4162  failing the structural check 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 54; only tag rules 88)  warnings (length_ratio 36, untranslated 8)  length ratio over 2925 segments: p1 0.312 p5 0.333 p50 0.404 p95 0.547 p99 0.976; outside 0.25-0.8: 38 (1.3%)  sources that cannot be cut 0  with translatable attributes 22  failed projects 0
$ echo $?
0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase3_export_check.py --projects-dir <repo>/projects --anonymize
epubcheck found; errors compared by message ID (chinese-only, bilingual-horizontal, bilingual-vertical)
PASS project-1: exports 3, translated segments 220: as translation 211, kept original 9 (key_mismatch 7, not_well_formed 2), toc labels changed 0/22 (22 into translated chapters), epubcheck errors source 51 (HTM-004:24 NCX-001:1 RSC-005:26), exports 51/51/51
PASS project-2: exports 3, translated segments 278: as translation 224, kept original 54 (not_well_formed 10, structure_mismatch 7, tag_count 37), toc labels changed 0/35 (33 into translated chapters), epubcheck errors source 27 (OPF-034:1 RSC-005:25 RSC-020:1), exports 27/28/28
PASS project-3: exports 3, translated segments 36: as translation 34, kept original 2 (structure_mismatch 1, tag_count 1), toc labels changed 18/1357 (22 into translated chapters), epubcheck errors source 0, exports 0/0/0
PASS project-4: exports 3, translated segments 1333: as translation 1276, kept original 57 (empty_block 14, key_mismatch 10, not_well_formed 2, structure_mismatch 31), toc labels changed 123/144 (126 into translated chapters), epubcheck errors source 11 (RSC-012:11), exports 11/22/22
PASS project-5: exports 3, translated segments 832: as translation 794, kept original 38 (code_changed 19, key_mismatch 3, structure_mismatch 1, tag_count 15), toc labels changed 244/245 (245 into translated chapters), epubcheck errors source 470 (HTM-004:25 NCX-001:1 PKG-006:1 RSC-005:440 RSC-007:3), exports 466/511/511
PASS project-6: exports 3, translated segments 581: as translation 578, kept original 3 (key_mismatch 3), toc labels changed 19/19 (19 into translated chapters), epubcheck errors source 1 (RSC-005:1), exports 1/1/1
PASS project-7: exports 3, translated segments 551: as translation 524, kept original 27 (key_mismatch 12, not_well_formed 6, structure_mismatch 9), toc labels changed 1/45 (44 into translated chapters), epubcheck errors source 151 (RSC-005:151), exports 151/183/183
PASS project-8: exports 3, translated segments 331: as translation 315, kept original 16 (structure_mismatch 16), toc labels changed 0/18 (16 into translated chapters), epubcheck errors source 27 (RSC-005:26 RSC-007:1), exports 27/27/27
TOTAL projects 8  exports 24  translated segments 4162  as translation 3956  kept original 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 53)  toc labels changed 405/1885 (527 into translated chapters)  exports with new epubcheck errors 0  failed projects 0
$ echo $?
0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

Before → after:

- 206 failing of 4162, unchanged; **88 fail only on tag rules, now printed by the kept script**
  (project-1 7, project-2 37, project-3 1, project-4 10, project-5 18, project-6 3,
  project-7 12 — the scratch figure of the Phase 4 entry, reproduced).
- Per rule: `extra_attributes` 10 → 0 (project-4 9, project-6 1), every other rule unchanged.
  Rule: B2. All 10 changed the attributes of a block element itself; before, that counted as
  a missing signature (`key_mismatch`) plus an added one (`extra_attributes`); now the root is
  compared on its own and reports `key_mismatch` once. The same 10 segments fail; checked by
  a read-only scratch comparison of the old and new check on copies (counts only, deleted).
- Export 3956 / 206 and the kept reasons per rule unchanged (the export reports a segment's
  first blocking rule, `key_mismatch` before and after); TOC labels 405, no new epubcheck
  errors.
- Uncuttable sources 0 (B5); segments with translatable attributes 22 (A).
- No `phase4-validate-*`, `phase3-export-*` or scratch copy directory left.

### Tests

1695 passed (Phase 4 entry: 1575). New: `TestTranslatableAttributes`, `TestCheckKeys`,
`TestBlockRootSeparate`, `TestTokenNames`, `TestRound1Checks`, `TestRound1Survivors`
(`test_validation.py`); `TestRound1Translator` (`test_translator.py`); `TestRound1Validate`
(`test_cli.py`); `TestRound1Review` and the Node transition tests in
`TestPhase4HeaderProgress` (`test_web.py`); `TestRound1Export` (`test_exporter.py`);
`test_sources_and_attributes_counted`, `test_failing_every_tag_rule_counts_as_tags_only`
(`test_verify_scripts.py`).

Tests changed, none removed:

| Test(s) | Change | Reason |
|---|---|---|
| fake replies `'<p>譯文</p>'`, `'<h1>…</h1>'`, `mock_result.stdout = '<p>…</p>'` etc. in `test_cli.py`, `test_translator.py`, `test_web.py` | carry `k="1"`; where the test compared the reply with the result, the expected text is the restored one | B1: a reply without the key is refused before restore |
| `TestPhase4Retry::test_failing_half_fails_the_segment` | `第 2 個區塊` instead of `第 1 個區塊` | B13 |
| `TestBoundedErrorText::test_huge_stderr_through_claude_classification` | bound `4 * (20000 + 2000) + 20000` | four calls since Phase 4; the longer HTML prompt crossed the old three-call bound |
| `TestCheckHtml::test_block_attributes_compared`, `test_details` | root attributes reported once | B2 |
| `TestProtect` (3), `TestRestore` (4) | keys compared as `(name, attrs)`; `alt` shown in the prompt form | A (`Key`) |
| `TestWorkerEventsAndLogs::test_events_for_one_success_and_one_failure` | event text is the restored text | B1 |
| `TestTemplatesHandleErrors::test_retry_button_reactive` | 9 `refreshFailedCount()` calls | B11 |
| `TestPhase4ReviewValidation::test_approve_without_any_text` | 422, nothing changed | B6: approving nothing is refused |
| `TestValidateCheck` lines | the new counts | B14, B5, A |

### Mutation testing

`scripts/verify/mutation.sh main`, mutmut 3.8.1, parallel.

| Run | Mutants | Killed | Survived | Time |
|---|---|---|---|---|
| 1 | 1885 | 1865 | 20 | about 7 min |
| 2 (final) | 1871 | 1868 | 3 | about 7 min |

Run 1's survivors and what was done:

| mutant(s) | done |
|---|---|
| `_translate_html` 10 (`code` → `None`), 47 (first half numbered from 2) | killed: `TestRound1Translator` (failure code; a failing first half reports block 1) |
| `_compare_elements` 1 (default `first_block` 2) | code changed: no default (every caller passes it) |
| `_restored_attrs` 21, 23 (sort by name) | killed: `test_replacements_from_the_end` |
| `_restored_attrs` 31 (`continue` → `break`) | killed: `test_unchanged_value_then_a_changed_one` |
| `_restored_attrs` 7 (`or` → `and` with the `k` count) | code changed: the `k` count test removed (`restore` calls it only with exactly one `k`); `test_repeated_attribute_left_as_written` |
| `check_code` 3, `check_keys` 113, 132, 144 (kind `None`) | killed: `test_kinds_are_structure` |
| `check_keys` 111, 153 (texts), 30 (`continue` → `break` after a comment) | killed: `test_nothing_at_the_top_level`, `test_attribute_problems_joined`, `test_tags_after_a_comment_still_checked` |
| `check_keys` 49 (`get(k) if k is not None`) | code changed: `keys.get(k)` (`get(None)` is `None`) |
| `restore` 58 (wrapper attribute text) | code changed: a wrapper's attribute text is never computed |
| `phase4_validate_check.check_copy` 84 (`<=` → `<`) | killed: `test_failing_every_tag_rule_counts_as_tags_only` |
| `_Session.__init__` 4, `top_level_units` 39 | equivalent, as in the Phase 4 entry |
| `_restored_attrs` 27 (sort by value end instead of start) | equivalent: values of different attributes do not overlap, so ends are ordered as starts |

Remaining survivors (run 2), each equivalent: `_Session.__init__` 4 and `top_level_units` 39
(reasons in the Phase 4 entry), and `_restored_attrs` 20 (the run-1 mutant 27 renumbered: sort
by value end instead of start; values of different attributes do not overlap).

The phase 3 export check ran before the run-1 survivor commit; that commit only removed
conditions that could not change a result (suite green, mutation run 2), and the Phase 4
check rerun on the final code printed the same lines.

### Not done

- `apply_outcome` does not store a failed outcome's usage on the segment (the segment's
  `usage` describes its kept text); the outcome carries it.
- `CLAUDE.md` and `README.md` not updated.

### Not verified

- No real `claude` call: whether the model translates `alt`/`title` and keeps blocks under the
  new prompt wording is untested against the real model.
- The header bar was tested by running the page's script in Node with stubs, not in a browser.
- A `--mark-failed` save of a project whose stored text holds a lone surrogate (only possible
  in a hand-edited `project.json`) was not tested; `validate` without the flag and the export
  report it as `not_well_formed`.

## Phase 4 — review round 2

Date: 2026-11-21. Branch: `phase-4/protect-validate-retry`, same worktree and environment.
Findings of review round 2 (items 1–8) and one added by the third reviewer (9).

### Changes

| # | Finding | Change |
|---|---|---|
| 1 | A translated `alt`/`title`/`aria-label` on a block element never reached the export | `exporter._replace_children` now also copies the values of `TRANSLATABLE_ATTRIBUTES` that the translation's block element carries onto the filled element (chinese-only) or the translated copy (bilingual); the bilingual original is the source element and keeps the source's values; machine attributes are untouched (the check guarantees both carry the same names). Tests: `TestRound2Export`, `p` with `title`, `table` with `title`, `figure` with `aria-label`, all three formats. With the previous exporter all 9 fail. |
| 2 | Inline keys of equal attributes could move between blocks | `Key.owner`: the top-level key of the unit (block or `<run>`) a key is in, recorded by `protect`. `check_keys` requires a nested key to sit in the unit with that key (`<em k="4"> 不在原來的區塊（原文在 k="3" 內）`, `key_mismatch`). Tests: exchange between two blocks, between a block and a run, a nested block key. |
| 3 | An emptied value showed up as a misleading stored-form mismatch | `check_keys` compares blankness (after decoding, whitespace-only counts as blank): a blank reply value for a non-blank source value is `extra_attributes` "`<img k="2"> 的 alt 不可為空`"; the reverse keeps "原文是空的". `_signature` treats a whitespace-only value as empty. |
| 4 | Whitespace character references in translated values | `_unescape` applies attribute-value normalization first (literal tab, CR, LF, CRLF → space), then decodes references; `_escape` writes tab, LF and CR as `&#9;`, `&#10;`, `&#13;`. A value the reply repeats is still put back as its exact stored token. |
| 5 | Split block numbers after a multi-line run | Fixed, not dropped: `validation.pieces_per_group(html, fingerprints)` gives the blocks per top-level group (a run cut by the fingerprints holds several). The halves are checked with their share of the fingerprints, the second half numbered from the number of blocks before it plus one. `<p>A</p>\nL1\nL2\n<p>B</p>`: an error in `<p>B</p>` is block 4. This also replaces the Phase 4 consequence "halves are checked without fingerprints" (marked superseded in that entry) and corrects the round 1 claim (marked in item 13 there). |
| 6 | Approve with a body that is not a JSON object | 400 `Body must be a JSON object` for an array, a string or malformed JSON; an absent or empty body still approves the stored text. |
| 7 | `validate` on schema 1 used the export's message | Own message: `此專案是舊格式（schema 1），無法檢查譯文。請先執行 python3 translate.py migrate <專案目錄> 再檢查。` |
| 8 | Schema 1 text segment with empty `html` | `check_segment` returns no issue for a text segment without HTML: there is no structure to compare. Review save and approve work for such segments (test); blank text is still handled as before. |
| 9 | Joined text failing after two passing halves was not recorded | `_translate_html` calls `session.rejected(result, joined)` before raising: the warning is logged, the attempt (with the joined text as its output) goes into the diagnostics. The failure message now counts calls made (`_Session.used`) instead of recorded attempts: "after 4 attempts" when four calls were made. Test: `TestRound2JoinedFailure` (the joined check is made to fail with a patched `check_html`, since with fingerprint shares two passing halves cannot produce a failing whole in practice). |

### Known limitations (recorded, not fixed)

- A top-level CDATA section that holds text is treated by the split as a separator between
  units, not as content.
- Whitespace written as entities (`&#10;`) between top-level blocks of a reply is refused as
  stray text at the top level.
- `validate --mark-failed` cannot save a hand-edited project whose stored text holds a lone
  surrogate (the save fails encoding it); `validate` without the flag and the export report
  the segment as `not_well_formed`.
- Schema 1 sources were measured only on migrated copies.
- The split assumes one top-level group per top-level unit (true for every source that can be
  cut); were it not, the fingerprint shares would be misaligned and the halves would be
  refused, failing the segment rather than accepting a wrong text.

### Checks

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase4_validate_check.py --projects-dir <repo>/projects --anonymize
PASS project-1: round trip 220/220, translated 220, failing the structural check 9 (key_mismatch 7, not_well_formed 2; only tag rules 7), warnings (length_ratio 1), length ratio over 205 segments: p1 0.312 p5 0.324 p50 0.359 p95 0.580 p99 0.700; outside 0.25-0.8: 1 (0.5%), sources that cannot be cut 0, with translatable attributes 0
PASS project-2: round trip 278/278, translated 278, failing the structural check 54 (not_well_formed 10, structure_mismatch 7, tag_count 37; only tag rules 37), warnings (length_ratio 14), length ratio over 218 segments: p1 0.351 p5 0.360 p50 0.402 p95 1.104 p99 1.186; outside 0.25-0.8: 14 (6.4%), sources that cannot be cut 0, with translatable attributes 3
PASS project-3: round trip 4385/4385, translated 36, failing the structural check 2 (structure_mismatch 1, tag_count 1; only tag rules 1), warnings (none), length ratio over 13 segments: p1 0.363 p5 0.363 p50 0.471 p95 0.580 p99 0.580; outside 0.25-0.8: 0 (0.0%), sources that cannot be cut 0, with translatable attributes 16
PASS project-4: round trip 2236/2236, translated 1333, failing the structural check 57 (empty_block 14, key_mismatch 10, not_well_formed 2, structure_mismatch 31; only tag rules 10), warnings (untranslated 6), length ratio over 1102 segments: p1 0.342 p5 0.365 p50 0.422 p95 0.484 p99 0.514; outside 0.25-0.8: 2 (0.2%), sources that cannot be cut 0, with translatable attributes 0
PASS project-5: round trip 839/839, translated 832, failing the structural check 38 (code_changed 19, key_mismatch 3, structure_mismatch 1, tag_count 16; only tag rules 18), warnings (none), length ratio over 430 segments: p1 0.312 p5 0.334 p50 0.394 p95 0.516 p99 0.631; outside 0.25-0.8: 0 (0.0%), sources that cannot be cut 0, with translatable attributes 0
PASS project-6: round trip 581/581, translated 581, failing the structural check 3 (key_mismatch 3; only tag rules 3), warnings (length_ratio 16, untranslated 2), length ratio over 276 segments: p1 0.347 p5 0.381 p50 0.503 p95 0.885 p99 1.086; outside 0.25-0.8: 16 (5.8%), sources that cannot be cut 0, with translatable attributes 0
PASS project-7: round trip 621/621, translated 551, failing the structural check 27 (key_mismatch 12, not_well_formed 6, structure_mismatch 9; only tag rules 12), warnings (length_ratio 4), length ratio over 458 segments: p1 0.321 p5 0.340 p50 0.380 p95 0.439 p99 0.732; outside 0.25-0.8: 4 (0.9%), sources that cannot be cut 0, with translatable attributes 3
PASS project-8: round trip 331/331, translated 331, failing the structural check 16 (structure_mismatch 16; only tag rules 0), warnings (length_ratio 1), length ratio over 223 segments: p1 0.299 p5 0.308 p50 0.340 p95 0.380 p99 0.430; outside 0.25-0.8: 1 (0.4%), sources that cannot be cut 0, with translatable attributes 0
TOTAL projects 8  round trip 9491/9491  translated 4162  failing the structural check 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 54; only tag rules 88)  warnings (length_ratio 36, untranslated 8)  length ratio over 2925 segments: p1 0.312 p5 0.333 p50 0.404 p95 0.547 p99 0.976; outside 0.25-0.8: 38 (1.3%)  sources that cannot be cut 0  with translatable attributes 22  failed projects 0
$ echo $?
0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase3_export_check.py --projects-dir <repo>/projects --anonymize
epubcheck found; errors compared by message ID (chinese-only, bilingual-horizontal, bilingual-vertical)
PASS project-1: exports 3, translated segments 220: as translation 211, kept original 9 (key_mismatch 7, not_well_formed 2), toc labels changed 0/22 (22 into translated chapters), epubcheck errors source 51 (HTM-004:24 NCX-001:1 RSC-005:26), exports 51/51/51
PASS project-2: exports 3, translated segments 278: as translation 224, kept original 54 (not_well_formed 10, structure_mismatch 7, tag_count 37), toc labels changed 0/35 (33 into translated chapters), epubcheck errors source 27 (OPF-034:1 RSC-005:25 RSC-020:1), exports 27/28/28
PASS project-3: exports 3, translated segments 36: as translation 34, kept original 2 (structure_mismatch 1, tag_count 1), toc labels changed 18/1357 (22 into translated chapters), epubcheck errors source 0, exports 0/0/0
PASS project-4: exports 3, translated segments 1333: as translation 1276, kept original 57 (empty_block 14, key_mismatch 10, not_well_formed 2, structure_mismatch 31), toc labels changed 123/144 (126 into translated chapters), epubcheck errors source 11 (RSC-012:11), exports 11/22/22
PASS project-5: exports 3, translated segments 832: as translation 794, kept original 38 (code_changed 19, key_mismatch 3, structure_mismatch 1, tag_count 15), toc labels changed 244/245 (245 into translated chapters), epubcheck errors source 470 (HTM-004:25 NCX-001:1 PKG-006:1 RSC-005:440 RSC-007:3), exports 466/511/511
PASS project-6: exports 3, translated segments 581: as translation 578, kept original 3 (key_mismatch 3), toc labels changed 19/19 (19 into translated chapters), epubcheck errors source 1 (RSC-005:1), exports 1/1/1
PASS project-7: exports 3, translated segments 551: as translation 524, kept original 27 (key_mismatch 12, not_well_formed 6, structure_mismatch 9), toc labels changed 1/45 (44 into translated chapters), epubcheck errors source 151 (RSC-005:151), exports 151/183/183
PASS project-8: exports 3, translated segments 331: as translation 315, kept original 16 (structure_mismatch 16), toc labels changed 0/18 (16 into translated chapters), epubcheck errors source 27 (RSC-005:26 RSC-007:1), exports 27/27/27
TOTAL projects 8  exports 24  translated segments 4162  as translation 3956  kept original 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 53)  toc labels changed 405/1885 (527 into translated chapters)  exports with new epubcheck errors 0  failed projects 0
$ echo $?
0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

Both outputs are identical, line for line, to review round 1's. No stored translation uses a
translated attribute on a block element, moves a key between blocks, or blanks a value, so
none of items 1–4 changes a count; items 5–9 do not touch stored translations. No
`phase4-validate-*` or `phase3-export-*` directory left. Both scripts ran on the code before
the last refactor commit, which only removed conditions that cannot change a result (suite
green and the final mutation run below).

### Tests

1731 passed (round 1: 1695). New: `TestRound2Keys`, `TestRound2Values`, `TestRound2Segments`
(`test_validation.py`); `TestRound2Split`, `TestRound2JoinedFailure` (`test_translator.py`);
`TestRound2Review` (`test_web.py`); `TestRound2Export` (`test_exporter.py`).

Tests changed, none removed:

| Test | Change | Reason |
|---|---|---|
| `TestRound1Validate::test_schema_1_refused` | the validate message | item 7 |
| `TestPhase4Retry::test_failing_half_fails_the_segment` | "after 4 attempts" | item 9: the message counts calls; four were made |

### Mutation testing

`scripts/verify/mutation.sh main`, mutmut 3.8.1, parallel, about 7 minutes per run.

| Run | Mutants | Killed | Survived |
|---|---|---|---|
| 1 | 1973 | 1959 | 14 |
| 2 (final) | 1961 | 1953 | 8 |

Run 1's survivors:

| mutant(s) | done |
|---|---|
| `_translate_html` 51, 54, 55, 56 (the fallback when the group counts did not match the units) | code changed: the fallback is removed (a source that reaches the split can be cut, and its groups are its units) |
| `check_keys` 51 (`top[-1] if top`) | code changed: the owner is read only below the top level, where a unit is open |
| `_replace_children` 1 (`and` → `or`) | code changed: the value is copied when the translation's element carries it |
| `_translate_html` 99, 102 (the joined text checked without fingerprints) | equivalent: each half has already passed with its share of the fingerprints, so the cut of the joined text is the same with or without them |
| `_signature` 7 (`''` → `'XXXX'`), 8 (blank and non-blank swapped) | equivalent: the value only splits translatable attributes into two classes, blank and not blank; any two distinct labels, in either order, compare the same |
| `_fill` 3 (`deepcopy` → `copy`) | equivalent: `copy.copy` of an lxml element is a deep copy |
| `_restored_attrs` 20, `_Session.__init__` 4, `top_level_units` 39 | equivalent, as in round 1 and the Phase 4 entry |

Remaining survivors (run 2), each equivalent and listed above: `_translate_html` 93 and 96
(run 1's 99 and 102, renumbered), `_signature` 7 and 8, `_fill` 3, `_restored_attrs` 20,
`_Session.__init__` 4, `top_level_units` 39.

### Not verified

- No real `claude` call: the model's handling of the owner rule and of blank values under
  the prompt wording is untested.
- The export of translated block-element attributes was checked by tests and by the
  unchanged real-data export counts; no real book has such a stored translation, and the
  output was not opened in a reader.

## Phase 4 — review round 3

Date: 2026-11-21. Branch: `phase-4/protect-validate-retry`, same worktree and environment.
Minor findings of review round 3; no redesign.

### Changes

| # | Finding | Change |
|---|---|---|
| 1 | Quality text joined blocks without a separator | `check_html` joins the blocks' texts with a line break before `_quality`: an unchanged `<p>A</p>\n<p>B</p>` no longer reads as the word `AB` (no `untranslated` warning); the length ratio is unchanged (whitespace is removed before it). Test through `check_html`, with and without fingerprints. |
| 2 | `not_well_formed` detail carried lxml's position inside the fragment wrapper | `validation.xml_error(e)`: the parser's reason without `line N` / `column N` (e.g. `不是格式正確的 XML：Opening and ending tag mismatch: p and fragment`), used by `check_html` and by the reply check in the translator (so also by the follow-up message). |
| 3 | A blank translatable value in the stored form gave `key_mismatch` + `extra_attributes` | `_compare_elements` pairs a source element with a translation element whose signatures differ only in translatable values left blank (`_emptied`), on a block element itself and inside a block, and reports one `extra_attributes` issue with the keyed check's wording: `第 1 個區塊的 <img> 的 alt 不可為空`. Any other difference is reported as before. |

Round 2's justification of the two `_translate_html` survivors (the joined text checked with or
without fingerprints) was wrong before item 1: for `<p>1</p>\nA\nB`, the fingerprint cut turned
the run into the pieces `A` and `B`, joined without a separator into `1AB`, so the
fingerprinted check could warn `untranslated` where the other did not. With item 1 the two
texts are both `1\nA\nB` and the halves have already passed the structural rules with their
shares of the fingerprints, so the two calls give the same result; the survivors are
equivalent again (re-examined below).

### Known limitations (recorded, not fixed)

- Quality warnings of accepted split halves are not carried to the joined result (only the
  joined text's own check gives warnings).
- When the call budget runs out during the split because of transient errors, the segment's
  issue is `transient`; the structural cause is only in the attempt records (diagnostics).
- An unterminated inline element in a stored source (`<em>x`) does not round-trip through
  protect/restore; such a source is refused before any call (`ambiguous_source`), and none
  exists in the measured data (round trip 9491/9491).
- Approve requires `Content-Type: application/json` when a body is sent (a body with another
  type is "not a JSON object", 400).

### Checks

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase4_validate_check.py --projects-dir <repo>/projects --anonymize
PASS project-1: round trip 220/220, translated 220, failing the structural check 9 (key_mismatch 7, not_well_formed 2; only tag rules 7), warnings (length_ratio 1), length ratio over 205 segments: p1 0.312 p5 0.324 p50 0.359 p95 0.580 p99 0.700; outside 0.25-0.8: 1 (0.5%), sources that cannot be cut 0, with translatable attributes 0
PASS project-2: round trip 278/278, translated 278, failing the structural check 54 (not_well_formed 10, structure_mismatch 7, tag_count 37; only tag rules 37), warnings (length_ratio 14), length ratio over 218 segments: p1 0.351 p5 0.360 p50 0.402 p95 1.104 p99 1.186; outside 0.25-0.8: 14 (6.4%), sources that cannot be cut 0, with translatable attributes 3
PASS project-3: round trip 4385/4385, translated 36, failing the structural check 2 (structure_mismatch 1, tag_count 1; only tag rules 1), warnings (none), length ratio over 13 segments: p1 0.363 p5 0.363 p50 0.471 p95 0.580 p99 0.580; outside 0.25-0.8: 0 (0.0%), sources that cannot be cut 0, with translatable attributes 16
PASS project-4: round trip 2236/2236, translated 1333, failing the structural check 57 (empty_block 14, key_mismatch 10, not_well_formed 2, structure_mismatch 31; only tag rules 10), warnings (untranslated 6), length ratio over 1102 segments: p1 0.342 p5 0.365 p50 0.422 p95 0.484 p99 0.514; outside 0.25-0.8: 2 (0.2%), sources that cannot be cut 0, with translatable attributes 0
PASS project-5: round trip 839/839, translated 832, failing the structural check 38 (code_changed 19, key_mismatch 3, structure_mismatch 1, tag_count 16; only tag rules 18), warnings (none), length ratio over 430 segments: p1 0.312 p5 0.334 p50 0.394 p95 0.516 p99 0.631; outside 0.25-0.8: 0 (0.0%), sources that cannot be cut 0, with translatable attributes 0
PASS project-6: round trip 581/581, translated 581, failing the structural check 3 (key_mismatch 3; only tag rules 3), warnings (length_ratio 16, untranslated 2), length ratio over 276 segments: p1 0.347 p5 0.381 p50 0.503 p95 0.885 p99 1.086; outside 0.25-0.8: 16 (5.8%), sources that cannot be cut 0, with translatable attributes 0
PASS project-7: round trip 621/621, translated 551, failing the structural check 27 (key_mismatch 12, not_well_formed 6, structure_mismatch 9; only tag rules 12), warnings (length_ratio 4), length ratio over 458 segments: p1 0.321 p5 0.340 p50 0.380 p95 0.439 p99 0.732; outside 0.25-0.8: 4 (0.9%), sources that cannot be cut 0, with translatable attributes 3
PASS project-8: round trip 331/331, translated 331, failing the structural check 16 (structure_mismatch 16; only tag rules 0), warnings (length_ratio 1), length ratio over 223 segments: p1 0.299 p5 0.308 p50 0.340 p95 0.380 p99 0.430; outside 0.25-0.8: 1 (0.4%), sources that cannot be cut 0, with translatable attributes 0
TOTAL projects 8  round trip 9491/9491  translated 4162  failing the structural check 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 54; only tag rules 88)  warnings (length_ratio 36, untranslated 8)  length ratio over 2925 segments: p1 0.312 p5 0.333 p50 0.404 p95 0.547 p99 0.976; outside 0.25-0.8: 38 (1.3%)  sources that cannot be cut 0  with translatable attributes 22  failed projects 0
$ echo $?
0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase3_export_check.py --projects-dir <repo>/projects --anonymize
epubcheck found; errors compared by message ID (chinese-only, bilingual-horizontal, bilingual-vertical)
PASS project-1: exports 3, translated segments 220: as translation 211, kept original 9 (key_mismatch 7, not_well_formed 2), toc labels changed 0/22 (22 into translated chapters), epubcheck errors source 51 (HTM-004:24 NCX-001:1 RSC-005:26), exports 51/51/51
PASS project-2: exports 3, translated segments 278: as translation 224, kept original 54 (not_well_formed 10, structure_mismatch 7, tag_count 37), toc labels changed 0/35 (33 into translated chapters), epubcheck errors source 27 (OPF-034:1 RSC-005:25 RSC-020:1), exports 27/28/28
PASS project-3: exports 3, translated segments 36: as translation 34, kept original 2 (structure_mismatch 1, tag_count 1), toc labels changed 18/1357 (22 into translated chapters), epubcheck errors source 0, exports 0/0/0
PASS project-4: exports 3, translated segments 1333: as translation 1276, kept original 57 (empty_block 14, key_mismatch 10, not_well_formed 2, structure_mismatch 31), toc labels changed 123/144 (126 into translated chapters), epubcheck errors source 11 (RSC-012:11), exports 11/22/22
PASS project-5: exports 3, translated segments 832: as translation 794, kept original 38 (code_changed 19, key_mismatch 3, structure_mismatch 1, tag_count 15), toc labels changed 244/245 (245 into translated chapters), epubcheck errors source 470 (HTM-004:25 NCX-001:1 PKG-006:1 RSC-005:440 RSC-007:3), exports 466/511/511
PASS project-6: exports 3, translated segments 581: as translation 578, kept original 3 (key_mismatch 3), toc labels changed 19/19 (19 into translated chapters), epubcheck errors source 1 (RSC-005:1), exports 1/1/1
PASS project-7: exports 3, translated segments 551: as translation 524, kept original 27 (key_mismatch 12, not_well_formed 6, structure_mismatch 9), toc labels changed 1/45 (44 into translated chapters), epubcheck errors source 151 (RSC-005:151), exports 151/183/183
PASS project-8: exports 3, translated segments 331: as translation 315, kept original 16 (structure_mismatch 16), toc labels changed 0/18 (16 into translated chapters), epubcheck errors source 27 (RSC-005:26 RSC-007:1), exports 27/27/27
TOTAL projects 8  exports 24  translated segments 4162  as translation 3956  kept original 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 53)  toc labels changed 405/1885 (527 into translated chapters)  exports with new epubcheck errors 0  failed projects 0
$ echo $?
0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

Both outputs are identical, line for line, to round 2's. The warnings stay `length_ratio 36,
untranslated 8`: the count did not change, so none of the 8 `untranslated` warnings was a
false positive of the missing separator, and the length ratio does not
see the separator. No stored translation has a blank translatable value, and the
not_well_formed detail is not printed by either script. No `phase4-validate-*` or
`phase3-export-*` directory left. The scripts ran on the code of the first round-3 commit;
the later commits change only tests and remove one always-true condition (suite green, final
mutation run below).

### Tests

1748 passed (round 2: 1731). New: `TestRound3`, `TestRound3Survivors`
(`test_validation.py`), `TestRound3Translator` (`test_translator.py`).

Tests changed, none removed:

| Test | Change | Reason |
|---|---|---|
| `TestTranslatableAttributes::test_empty_value_stays_empty`, `TestRound2Values::test_whitespace_only_is_empty_in_the_stored_form` | an emptied value is one `extra_attributes` issue | item 3 |

### Mutation testing

`scripts/verify/mutation.sh main`, mutmut 3.8.1, parallel, about 7 minutes per run.

| Run | Mutants | Killed | Survived |
|---|---|---|---|
| 1 | 2035 | 2022 | 13 |
| 2 | 2035 | 2026 | 9 |
| 3 (final) | 2033 | 2026 | 7 |

| mutant(s) | done |
|---|---|
| `_html_check` 36 (`xml_error(None)`) | killed: `TestRound3Translator::test_not_well_formed_reply_detail` |
| `_compare_elements` 29 (the join of two emptied names), 54 (`>=` for the source element), 64 (`want[sg] = 1`) | killed: `TestRound3Survivors` |
| `_compare_elements` 55 (`>=` for the translation element) | code changed: the condition was always true (the excess elements are recomputed for every source element, and one translation signature is paired at most once per source element); `test_a_balanced_element_is_not_paired` covers what it seemed to guard |
| `_compare_elements` 44 (`want + got` for the source elements) | equivalent: the loop body requires `want[sw] > got[sw]`, which holds exactly for the elements of `want - got` |
| `_translate_html` 93, 96 | equivalent after item 1 (see above) |
| `_fill` 3, `_restored_attrs` 20, `_Session.__init__` 4, `top_level_units` 39 | equivalent, as in the earlier rounds |

Remaining survivors (run 3), each equivalent and listed above: `_translate_html` 93 and 96,
`_compare_elements` 44, `_fill` 3, `_restored_attrs` 20, `_Session.__init__` 4,
`top_level_units` 39.

### Not verified

- No real `claude` call; the follow-up message's not_well_formed wording was not seen by the
  model.

## Phase 5 — part A

Date: 2026-11-21. Branch: `phase-5/glossary-compliance`, worktree `<repo>/.worktree/phase-5`,
shared virtualenv. Part A only: the compliance check, its measurement, and the review surfacing.
The targeted fix (part B) is decided after the user has judged the sample (plan, Phase 5).

### What was built

| Item | Where |
|---|---|
| `glossary` / `term_not_applied` warning, one per term: an entry the prompt gives (`terms_for_prompt`) that occurs in the source text (the prompt's matching, `term_pattern`, now public) and whose translation is not a substring of the translation. Compared without tags, entities decoded, NFKC, one kind of middle dot, no whitespace, case-folded, without parentheticals that hold no Chinese character. Empty translations and code segments skipped. A `work` entry only where written with the glossary's capitals (the prompt applies it only to a title). Never blocking. | `validation.terms_not_applied`, `glossary_issues`, `check_segment(seg, text, glossary=None)` |
| After a model call (all paths through `translate_segment`), at review save and approve, in `translate.py validate` (heading `品質與術語警告（不影響匯出）`). Only on translations that pass the structural check. The export does not run it. | `translator.translate_segment`, `review.update_segment` / `approve_segment`, `translate._validate` |
| Save, approve and re-translate return `issues` (approve: the warnings of the approved text; the stored issues are still cleared, as before). | `review.py` |
| Review page: an issue badge and the issue list (kind label and detail) per segment, updated from the responses; "有問題的段落 (N)" lists all segments with stored issues; per chapter, "只顯示有問題的段落". | `project/review.html`, `style.css`, `review.with_issues` |
| Search already returned `FAILED` segments (Phase 1); only its hint text said otherwise and now names them. | `project/search.html` |
| Check script, sample file. | `scripts/verify/phase5_glossary_check.py` |

### Checks

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase5_glossary_check.py --projects-dir <repo>/projects --anonymize --sample-out <file outside the repository>
PASS project-1: glossary entries 0, translated 220, passing the structural check 211, with term_not_applied 0, terms 0, by category (none)
PASS project-2: glossary entries 0, translated 278, passing the structural check 224, with term_not_applied 0, terms 0, by category (none)
PASS project-3: glossary entries 0, translated 36, passing the structural check 34, with term_not_applied 0, terms 0, by category (none)
PASS project-4: glossary entries 2, translated 1333, passing the structural check 1276, with term_not_applied 6, terms 2, by category (person: 9 warnings, 2 terms)
PASS project-5: glossary entries 5, translated 832, passing the structural check 794, with term_not_applied 0, terms 0, by category (none)
PASS project-6: glossary entries 0, translated 581, passing the structural check 578, with term_not_applied 0, terms 0, by category (none)
PASS project-7: glossary entries 6, translated 551, passing the structural check 524, with term_not_applied 6, terms 2, by category (term: 6 warnings, 2 terms)
PASS project-8: glossary entries 2, translated 331, passing the structural check 315, with term_not_applied 0, terms 0, by category (none)
TOTAL projects 8  glossary entries 15, translated 4162, passing the structural check 3956, with term_not_applied 12, terms 4, by category (person: 9 warnings, 2 terms, term: 6 warnings, 2 terms)  failed projects 0
sample written: 15 warnings
$ echo $?
0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

The "passing" counts equal Phase 4's "as translation" per project. 12 of 3956 checked
segments (0.3%) get a warning; 4 of the 8 projects have no glossary entries at all and project-5
and project-8 have entries that the translations follow. Only 15 warnings exist, so the sample
holds 15, not the 20 the plan asks for. The sample file holds book text; it was written outside
the repository and is not committed. No `phase5-glossary-*` directory left.

First reading of the sample by the implementer (categories only; the user's judgement decides):
10 of 15 look like real problems (a name transliterated against the glossary; a name kept in
English word order, `Miss X` instead of `X 小姐`, in 7 items of one book; a part title in a table
of contents translated differently), 5 look like false alarms (a polysemous term where the
context needs another rendering, 2; a shorter form of the glossary rendering, 1; a work title
kept in English in a bibliography, 2).

Review UI, by hand on an invented project served on a local port: the issue list
(`?issues=1`) shows exactly the segments with issues, each with its chapter link, kind label
and detail; saving a text without the glossary term shows the badge and the detail without
reload, saving the compliant text hides them.

### Tests

1817 passed (main: 1748). New: `TestGlossaryCompliance` (`test_validation.py`),
`TestTranslateSegmentGlossary` (`test_translator.py`), `TestReviewIssues` (`test_web.py`),
`TestPhase5ValidateGlossary` (`test_cli.py`), `TestGlossaryCheck`, `TestGlossaryCheckSurvivors`,
`TestReviewSurvivors` (`test_verify_scripts.py`).

Tests changed, none removed:

| Test | Change | Reason |
|---|---|---|
| `TestTranslateSegment::test_glossary_and_context` | the stub glossary returns a `GlossaryEntry` instead of a string | the check reads the entries the prompt got |
| `TestRound1Exact::test_review_responses_exact` | save and approve responses have `issues: []` | the endpoints return the check's issues |
| `TestPhase4Validate::test_read_only_report` | heading `品質與術語警告（不影響匯出）：` | glossary warnings are listed under it |

### Mutation testing

`scripts/verify/mutation.sh main`, mutmut 3.8.1, parallel, about 7 minutes per run.

| Run | Mutants | Killed | Survived | Timeout |
|---|---|---|---|---|
| 1 | 594 | 538 | 54 | 2 |
| 2 (final) | 587 | 586 | 1 | 0 |

All survivors of run 1 were in the check script and in two lines of `review.py`; the core code
(`validation`, `translator`, `glossary_manager`, `translate.py`) had none.

| mutant(s) | done |
|---|---|
| `main` 2–5, 7, 8, 10, 11, 14–18, 21, 24, 29–31, 42, 45, 46 (description, defaults, help texts) | killed: `TestGlossaryCheckSurvivors::test_help_in_process`, `test_default_projects_directory` |
| `run` 31, 69, 89, 95 (first snapshot fails; joined problems; failed count) | killed: `test_first_snapshot_fails`, `test_check_and_snapshot_fail`, `test_two_failing_projects_with_two_problems` |
| `run` 106, 109, 113 (sample file encoding) | killed: `test_sample_written_as_utf8` |
| `check_copy` 14, `review._glossary` 8 (`GLOSSARY.JSON`: the same file on a case-insensitive file system) | killed: `test_glossary_file_name` (both classes) |
| `check_copy` 25, 28, 38 (skipped segments) | killed: `test_skipped_segments_do_not_end_the_count` |
| `snapshot` 8, 11, 13–15 | killed: `test_snapshot` |
| `sample_item` 8, 13, 14, 23; `sample_markdown` 2–8; `category_text` 4, 5 | killed: `test_sample_item_term_case_and_no_match`, `test_sample_markdown_head`, `test_category_text` |
| `review._render` 1, 2 (`active_tab`) | killed: `TestReviewSurvivors::test_review_tab_active` |
| `pick_sample` 9, 10 (timeouts: the round-robin loop became endless) | code changed: `zip_longest` instead of the loop; same order |
| `category_text` 14 (`term.upper()` for `term.lower()` when counting distinct terms) | equivalent: the glossary keys its entries by `term.lower()`, so two entries never share a lower-case form; `upper()` could merge two of them only for characters whose upper case collides (e.g. `ß` and `ss`), which no counted term has |

### Not done

- Part B (targeted fix), by decision: it waits for the user's judgement of the sample.
- Warnings are not stored on existing translations: `validate` and the check script report
  them without writing. The review filter therefore shows glossary warnings only for segments
  translated or saved after this change.

### Not verified

- No real `claude` call; the warning after a model call is tested with the fake backend.
- `translate.py validate` was not run on the real projects: they are schema 1 and the command
  refuses them (migration needed); the check script migrates copies instead.

## Phase 5 — part A, review round 1

Date: 2026-11-21. Same branch and worktree. Findings of review round 1 (three reviewers).

### Changes

| # | Finding | Change |
|---|---|---|
| 1 | A damaged `glossary.json` broke save / approve / re-translate (invalid JSON gave a misleading 404, `null` or `[{}]` a 500) | `glossary_manager.load_glossary(path)`: the glossary, or None on any load failure (logged as a warning with the exception type). Save, approve and re-translate use it through one helper (`review._glossary`); without a glossary the structural check and the save still happen, and the response carries `glossary_unavailable: true` (re-translate then also runs without the glossary in the prompt). `translate.py validate` prints `術語表無法讀取（glossary.json 損壞或格式不符），未檢查術語。` and goes on. `GlossaryManager.save()` is atomic: a temporary file in the same directory, `os.replace`, the old mode kept (0644 for a new file). |
| 2 | Path guard of the check script missed the main checkout of a linked worktree | `in_work_tree`: refused when the resolved path or any ancestor holds `.git` (file or directory); also refused inside the projects directory, after resolving symlinks and `..`. Applies to `--sample-out` and the temporary directory. |
| 3 | Script errors escaped as tracebacks with paths | `main` catches any exception at the command boundary: `ERROR the check stopped (<type>)`, exit 1; the temporary directory is removed in `finally`, and a failure to remove it prints `ERROR cannot remove the temporary directory (<type>)`. |
| 4 | Categories printed verbatim | only `CATEGORY_LABELS` keys; any other category (also an empty one) is counted as `other`. |
| 5 | Parentheses | balanced groups found with a stack; each group without a Han character (CJK ideographs incl. the supplementary planes) is removed with what it holds, so `介面（API (v2)）` → `介面`; a group with Han stays (its inner groups without Han go); unmatched parentheses stay. |
| 6 | Regex tag removal broke on `>` in an attribute | `validation.plain_text`: the translation parsed as a fragment and its text taken with `element_text` (as the structural check does); text that does not parse (legacy plain text) is used as it is. |
| 7 | A failed re-translation returned only `error` | the response also has the stored `issues` and `issue_count`; the page shows them, the kept text and status stay. |
| 8 | Stale review page state | every review response has `issue_count` (segments with stored issues, after the change); the page sets the sidebar count from it and the status tooltip from the shown issues (removed when none). Approve still returns warnings it does not store; the count stays authoritative. |
| 9 | Tests that would pass without the behaviour | `TestReviewPageScript` runs the page script in Node (badge, list, absent `issues`, detail inserted as text, sidebar count, save, approve, failed re-translation); `distinct_terms` asserted directly. |
| 10 | Survivor `.lower()` → `.upper()` was not equivalent | `straße` / `strasse` are two entries; `upper()` would merge them. Test added. Round 0's justification of `category_text` 14 was wrong. |
| 11 | `sample_item` searched case-insensitively for `work` terms | both use `validation.occurrence_pattern`. |
| 12 | `term_pattern` has no end boundary (`Eve` in "every", `Java` in "JavaScript") | for the check only: `occurrence_pattern` = the prompt's start, then a short inflection, then `(?!\w)`. Names (`person`, `place`, `organization`, `work`): plural `s`/`es` and possessive (`'s`, `’s`, `'`). Other terms also `ed` / `ing`, and for a final `e` (`calibrate`) `es` / `ed` / `ing` with the `e` dropped for `ing`. The prompt's matching is unchanged. |
| 13 | The glossary check in `translate_segment` ran outside its `try` | entries whose term or translation is not a non-empty string are skipped; an unusable category is treated as none; the check is wrapped (`_glossary_warnings`): a failure logs a warning and drops the warnings only. The review endpoints pass only a loaded glossary, whose entries the check skips if malformed. |
| 14 | TOTAL "terms" summed per project, "by category" counted across projects | both sum the per-project counts. |

`terms_not_applied` no longer calls `terms_for_prompt`: it takes every well-formed entry with an
occurrence. That equals "in the prompt and occurring": the prompt holds the whole glossary up
to `FULL_GLOSSARY_MAX_TERMS` entries, and above that every entry whose start matches, which an
occurrence implies.

### Known limitations (recorded, not fixed)

- A term that occurs in the source only inside inline `<code>`, `<kbd>` or `<samp>` still counts
  as an occurrence.
- The check compiles one pattern per entry and segment; fine for the glossary sizes in use
  (0 to 6 entries per project).
- Inflections are a short fixed list: no consonant doubling (`stop` → `stopped`), no `y` → `ies`.
- `translate.py start` and the web translation task still load the glossary with
  `GlossaryManager.load()` and stop before any model call if it is damaged (the prompt needs it);
  only the check and the review endpoints go on without it.

### Checks

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase5_glossary_check.py --projects-dir <repo>/projects --anonymize --sample-out <file outside every git work tree>
(per-project lines identical to part A)
TOTAL projects 8  glossary entries 15, translated 4162, passing the structural check 3956, with term_not_applied 12, terms 4, by category (person: 9 warnings, 2 terms, term: 6 warnings, 2 terms)  failed projects 0
sample written: 15 warnings
$ echo $?
0
$ python3 scripts/verify/phase4_validate_check.py --projects-dir <repo>/projects --anonymize | tail -1
TOTAL projects 8  round trip 9491/9491  translated 4162  failing the structural check 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 54; only tag rules 88)  warnings (length_ratio 36, untranslated 8)  length ratio over 2925 segments: p1 0.312 p5 0.333 p50 0.404 p95 0.547 p99 0.976; outside 0.25-0.8: 38 (1.3%)  sources that cannot be cut 0  with translatable attributes 22  failed projects 0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

The TOTAL line is unchanged: the end boundary removed no real warning (the inflected verb forms
in the data are `-s` and `-d` of a final-`e` term, both covered), and with at most one category
per term in a project the per-project sums equal the earlier cross-project counts. The Phase 4
TOTAL is unchanged. The sample holds the same 15 warnings in the same order; three source
excerpts end two characters later because the match now includes the inflection. The sample
file was rewritten at the same place outside the repository. Two `phase5-glossary-*` directories
left by a failing test run during development (a test whose `rmtree` stub also blocked its own
cleanup) were removed; none is left.

### Tests

1927 passed (part A: 1817). New: `TestDamagedGlossary`, `TestIssueCount`,
`TestReviewPageScript` (Node; skipped where `node` is missing) (`test_web.py`);
`TestGlossaryRound1`, `TestGlossaryRound1Survivors` (`test_validation.py`);
`TestAtomicSave`, `TestLoadGlossary`, `TestRound1Survivors` (`test_glossary.py`);
`TestTranslateSegmentGlossaryRound1` (`test_translator.py`); `TestPhase5ValidateGlossary::
test_damaged_glossary` (`test_cli.py`); `TestGlossaryCheckRound1`,
`TestGlossaryCheckRound1Survivors`, `TestReviewRound1Survivors` (`test_verify_scripts.py`).
Every term and name in the fixtures is invented for the tests.

Tests changed, none removed:

| Test | Change | Reason |
|---|---|---|
| `TestRound1Exact::test_review_responses_exact`, `TestReviewIssues::test_update_returns_and_stores_glossary_warning`, `TestRetranslateDetails::test_failure_diagnostics_and_logs` | responses carry `issue_count`; a failed re-translation also `issues` | items 7, 8 |
| `TestReviewIssues::test_no_segment_with_issues`, `test_badge_and_details` | the count element has `id="issue-count"`; `showIssues(segId, data)` five times | item 8 |
| `TestGlossaryCompliance::test_comparable` | plain text in, no tags | item 6: tags are removed by parsing |
| `TestGlossaryCheck::test_refuses_*_inside[repository]` | a checkout made in the test, message "a git work tree" | item 2; the mutation run's copy of the repository has no `.git` |
| `TestGlossaryCheck::test_plain` | removed; replaced by `test_sample_translation_text_parsed` | the script's own `plain` is gone (item 6) |
| `TestGlossaryCheckSurvivors::test_help_in_process`, `test_glossary_file_name`, `test_category_text`, `TestReviewSurvivors::test_glossary_file_name` | new help text, `load_glossary`, `category_counts`, `_glossary(project_dir)` | items 1, 2, 4, 14 |

### Mutation testing

`scripts/verify/mutation.sh main`, mutmut 3.8.1, parallel.

| Run | Mutants | Killed | Survived |
|---|---|---|---|
| 1 | 809 | 764 | 45 |
| 2 (final) | 807 | 802 | 5 |

| mutant(s) | done |
|---|---|
| `_glossary_warnings` 9, 10, 12–14, 16 (log message, `exc_info`) | killed: `test_check_failure_logged` |
| `occurrence_pattern` 11 (`E?S` for a name), 15, 19, 20, 22, 23 (the final-e condition) | killed: `test_work_plural_case_sensitive`, `test_final_e` |
| `terms_not_applied` 17 (sort by `upper()`) | killed: `test_sorted_by_lower_case` |
| `load_glossary` 4, 6, 9 (warning text) | killed: `test_warning_names_the_exception` |
| `save` 17–26, 33, 36, 40, 44, 47–50 (temporary file place and name, encoding, JSON layout) | killed: `test_temporary_file_next_to_the_glossary`, `test_content` |
| `review._refused` 3 | killed: `test_refused_with_a_glossary_is_not_flagged` |
| `check` 39, 41 (`mkdtemp` without the resolved directory) | killed: `test_work_directory_under_the_resolved_temporary_directory` |
| `check` 44, 45 (`code = 1` before `run`) | code changed: the assignment was dead (when `run` raises, nothing is returned) and is removed |
| `in_work_tree` 10 (`.GIT`, the same on a case-insensitive file system) | killed: `test_looks_for_dot_git` |
| `_without_originals` 21 (`removed[start:i - 1] = [True] * (i + 1 - start)`) | ~~equivalent~~ wrong, corrected in review round 2: an inner group's two extra flags shift the outer group's later `True` onto the text after it (`(a(b))Z` loses `Z`); killed by `TestGlossaryRound2::test_unmatched_after_nested` |
| `occurrence_pattern` 30, 35 (upper-case inflection) | equivalent: these branches are only for categories outside NAME_CATEGORIES, whose pattern is always compiled with IGNORECASE (only `work`, a name category, is case-sensitive) |
| `occurrence_pattern` 5 (`'XXXX'` for a category that is not a string) | equivalent: any value outside NAME_CATEGORIES and other than `work` gives the same pattern |
| `save` 43 (`ensure_ascii=None`) | equivalent: `json.dump` only tests its truth value |

### Not verified

- No real `claude` call.
- The review page was not opened in a browser in this round; its script is exercised in Node
  with a DOM stub (`TestReviewPageScript`), and the endpoints through the Flask test client.
- `translate.py validate` was not run on the real projects (schema 1, and no command is run on a
  real project directory any more; only the check scripts, which work on copies).
- Atomicity is tested by the write path (temporary file in the same directory, `os.replace`,
  old file intact after a failed write), not by a concurrent reader.

## Phase 5 — part A, review round 2

Date: 2026-11-21. Same branch and worktree. Minor findings of review round 2 (three reviewers).

### Changes

| # | Finding | Change |
|---|---|---|
| 1 | Wrong field types loaded (`"term": null` raised `AttributeError` past `load_glossary`; `"category": ["term"]` loaded and broke `format_for_prompt`: re-translate 500) | `GlossaryManager.load()` raises `ValueError` unless the file is a list of objects with a string `term` and with `term`, `translation`, `category`, `notes`, `context` strings where present (`TEXT_FIELDS`); entries are replaced only after the whole file passed. Every caller then treats such a file as unreadable (review: `glossary_unavailable`). Endpoint tests for save, approve, re-translate. `"translation": null` / a number now also make the file unreadable (round 1 skipped such entries; the check still skips blank translations and malformed in-memory entries). |
| 2 | Above 150 entries the prompt may not hold a term the check reported (`calibrate` / "Calibrating it.": the prompt's matcher has no dropped-`e` rule) | the candidates are `terms_for_prompt(source_text)`, then `occurring`. Test below and above the limit. |
| 3 | A `work` term ending in a capital `E` | already a name category (plural / possessive only, no `e` stem); tests that the bare term matches for every kind of term (`VALE` as work and person, `CODE`, a one-letter term, `C#`, ...). |
| 4 | The rendering was normalized like a translation (parentheses removed: `C (language)` → `c`; tags and entities not decoded) | the rendering is read with `plain_text` and normalized, never shortened. It complies when found in the translation as written or after the translation's non-Chinese parentheticals are removed. So `API` complies in "應用程式介面（API）", and (a change from round 1) a rendering that only appears inside a parenthetical original complies too: the rendering is in the text. |
| 5 | The `_without_originals` survivor was not equivalent | test `(a(b))Z` → `Z`; the round 1 table is corrected above. |
| 6 | Names matched case-insensitively (`Will` fired on "will") | a name category is case-sensitive when the glossary term starts with a capital; `work` always is. |
| 7 | A term inside a longer term counted (`key` in "primary key") | `occurring`: longest terms first; an occurrence inside the span of a longer term's occurrence does not count. |
| 8 | A note number after a term blocked the end check ("Ella Vane3") | the end check is "no letter or underscore follows" (`(?![^\W\d])`): a digit may follow. |
| 9 | The page ignored `glossary_unavailable` | each segment has a hidden notice 術語表無法讀取，這次未檢查術語。, shown when a save, approve or re-translate answer carries the flag and hidden by the next answer without it (Node tests). Re-translate still runs without the glossary in the prompt, unlike `translate.py start` and the web translation task, which stop before any model call when the glossary cannot be loaded. |
| 10 | `save()` replaced a symlinked `glossary.json` by a file; new-file mode fixed at 0644; a failing `fchmod` leaked the descriptor | the real path is resolved first and the temporary file made next to it; the temporary file is created with `os.open(..., 0o666)` so the kernel applies the umask; an existing file's mode is copied with `fchmod` on the open file, and a failure is logged and ignored (the descriptor is closed by the `with`). |

### Checks

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase5_glossary_check.py --projects-dir <repo>/projects --anonymize --sample-out <file outside every git work tree>
(per-project lines identical to part A)
TOTAL projects 8  glossary entries 15, translated 4162, passing the structural check 3956, with term_not_applied 12, terms 4, by category (person: 9 warnings, 2 terms, term: 6 warnings, 2 terms)  failed projects 0
sample written: 15 warnings
$ python3 scripts/verify/phase4_validate_check.py --projects-dir <repo>/projects --anonymize | tail -1
(identical to round 1)
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

The TOTAL line is unchanged: the real glossaries hold at most 6 entries (the prompt gets all of
them), the capitalised names in the data occur capitalised, no real term lies inside another,
and no rendering occurs only inside a parenthetical. The sample written by this run was
byte-identical to the stored one and was not kept; the stored sample is unchanged. No temporary
directory left.

### Tests

1981 passed (round 1: 1927). New: `TestGlossaryRound2`, `TestGlossaryRound2Survivors`
(`test_validation.py`), `TestRound2`, `TestRound2Survivors` (`test_glossary.py`), `TestReviewPageGlossaryNotice` (`test_web.py`), damaged-glossary cases in
`TestDamagedGlossary`. Every term and name in the fixtures is invented.

Tests changed, none removed:

| Test | Change | Reason |
|---|---|---|
| `TestGlossaryCompliance::test_parenthetical_original_ignored` | `Velmora` inside parentheses complies | item 4 |
| `TestGlossaryCompliance::test_only_terms_of_the_source` | `Velmora` instead of `VELMORA` | item 6 |
| `TestGlossaryCompliance::test_comparable` | built from `_normalized`, `_without_originals`, `_compact` | `_comparable` removed (item 4) |
| `TestGlossaryRound1::test_nested_original_ignored` | a Chinese rendering | item 4 |
| `TestGlossaryRound1::test_bad_entries_skipped` | the `None` term asserted through `_checkable` | `terms_for_prompt` sorts by term (load() refuses such entries) |
| `TestGlossaryRound1::test_work_case_sensitive_with_inflection` | the case-insensitive counterpart is a `term` | item 6 |
| `TestDamagedGlossary::test_entry_without_translation_skipped` | only blank strings; `null` and numbers moved to the damaged files | item 1 |
| `TestDamagedGlossary::test_retranslate_goes_on` | also `term null`, `category list` | item 1 |
| `TestReviewPageScript` harness | a `.glossary-notice` element, `notice` in its result | item 9 |

### Mutation testing

`scripts/verify/mutation.sh main`, mutmut 3.8.1, parallel.

| Run | Mutants | Killed | Survived |
|---|---|---|---|
| 1 | 867 | 851 | 16 |
| 2 (final) | 867 | 863 | 4 |

| mutant(s) | done |
|---|---|
| `occurrence_pattern` 39, 40 (`work` misspelled) | killed: `test_lower_case_work_stays_case_sensitive` (a lower-case `work` term stays case-sensitive) |
| `occurring` 15 (`x < a`), 18 (`taken = own`) | killed: `test_shorter_term_at_the_same_start`, `test_every_longer_span_kept` |
| `load` 2–4, 13–15 (error messages) | killed: `TestRound2Survivors::test_messages` |
| `save` 20, 21 (`token_hex` length) | killed: the temporary name checked as `.glossary-` + 16 hex digits + `.tmp` |
| `occurrence_pattern` 30, 35 (upper-case inflection) | equivalent, as in round 1: these branches serve only categories outside NAME_CATEGORIES, always compiled with IGNORECASE (only names can be case-sensitive) |
| `occurrence_pattern` 5 (`'XXXX'` for a category that is not a string) | equivalent, as in round 1 |
| `save` 51 (`ensure_ascii=None`) | equivalent: `json.dump` only tests its truth value |

### Not verified

- No real `claude` call; no browser in this round (the page script runs in Node with a DOM stub).
- Atomic save and the umask are tested through the file system in the tests, not with a
  concurrent reader or another user's umask in a running server.
- `translate.py validate` was not run on the real projects (schema 1; only the check scripts
  touch them, on copies).

## Phase 6 — Segmentation for technical books

Date: 2026-11-21. Branch: `phase-6/technical-segmentation`, worktree `<repo>/.worktree/phase-6`,
shared virtualenv. New projects only; projects created before keep their stored segments.

### What was built

| Item | Rule | Where |
|---|---|---|
| Rules marker | `project.json` field `segmentation` (2 for projects created now, written; absent = 1, never written, so existing files save byte-identical). `Segmenter(rules=1\|2)`, default 2; `pm.create(..., segmentation=segmenter.rules)` in CLI `init` and web create. | `project.py`, `segmenter.py`, `translate.py`, `projects.py` |
| Headings merged | A heading (`h1`–`h6`) ends the segment being built unless it holds only headings, and starts a new one. The first text block after a heading always joins (even past `MAX_WORDS`); later blocks follow the usual 300/500 rules. Consecutive headings share a segment. A code block, a table or the chapter end after a heading leaves the heading(s) alone (`standalone`). Tables unchanged (own segment). | `Segmenter.segment_chapter` |
| Code boundaries | Unchanged: every code block is a segment of its own. | same |
| Passthrough | Code segment with no comment line (`validation.COMMENT_LINE`; `#include`, a trailing `# x` are not comment lines) gets the segment flag `passthrough` and is created `TRANSLATED` with its own code as text, no model. Flag, not a status: every status consumer (queue, progress, chapters, export's `accepted`) already does the right thing with `TRANSLATED`; a new status would have touched all of them. The flag is written only when true. | `segmenter.py`, `project.py` |
| … never queued | Not pending; also skipped by the web start's explicit `segment_ids`. | `tasks.start_translation` |
| … progress | Counts as translated; `get_progress()` also returns `passthrough`; `status` and `init` print it. | `project.py`, `translate.py` |
| … review | Shown as `passthrough` without editor or buttons; save, approve and re-translate answer 409, nothing changed, no model call. | `review.html`, `review.py`, `style.css` |
| … validate | Skipped (not counted as checked). | `translate._validate` |
| … export | Skipped: its DOM is never touched; a chapter of only passthrough code is copied byte for byte; it is not counted as translated nor untranslated in the report. | `exporter._export` |
| Context | Rules 2 only: the text before is the previous text segment's translation when it is `TRANSLATED`/`REVIEWED` and non-empty, tags removed, entities decoded (`translation_text`); otherwise its source text. The last 200 characters are taken by the prompt as before. Parallel workers: a neighbour not yet translated gives its source; no ordering added. Rules 1 projects: prompts unchanged. All four callers pass `translations=pm.data.segmentation == 2`. | `translator.get_context`, `translate_segment`, `tasks.py`, `translate.py`, `review.py` |
| TOC labels | Nothing to change: labels come from the translated heading element of the block that holds the target (Phase 3); a merged heading is still a block of its own. Tested in NCX and nav, all formats. | — |
| Check script | `scripts/verify/phase6_segmentation_check.py` | — |

### Checks

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase6_segmentation_check.py --projects-dir <repo>/projects --anonymize
PASS project-1: segments 220 -> 220, calls 220 -> 220 (ratio 1.000), passthrough 0, headings merged 0 of 0, blocks covered identically
PASS project-2: segments 292 -> 292, calls 292 -> 292 (ratio 1.000), passthrough 0, headings merged 0 of 0, blocks covered identically
PASS project-3: segments 4383 -> 3592, calls 4383 -> 2173 (ratio 0.496), passthrough 1419, headings merged 791 of 895, blocks covered identically
PASS project-4: segments 2236 -> 2089, calls 2236 -> 2089 (ratio 0.934), passthrough 0, headings merged 149 of 150, blocks covered identically
PASS project-5: segments 841 -> 520, calls 841 -> 501 (ratio 0.596), passthrough 19, headings merged 322 of 322, blocks covered identically
PASS project-6: segments 580 -> 311, calls 580 -> 311 (ratio 0.536), passthrough 0, headings merged 270 of 270, blocks covered identically
PASS project-7: segments 636 -> 588, calls 636 -> 588 (ratio 0.925), passthrough 0, headings merged 48 of 58, blocks covered identically
PASS project-8: segments 332 -> 258, calls 332 -> 258 (ratio 0.777), passthrough 0, headings merged 75 of 75, blocks covered identically
TOTAL projects 8  segments 9520 -> 7870, calls 9520 -> 6432 (ratio 0.676), passthrough 1438, headings merged 1655 of 1770  failed projects 0
$ echo $?
0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

Plan acceptance: project-3 needs 2173 calls, fewer than half of 4385 (2192.5) and of the 4383
the old rules give when re-cut from the source (the stored 4385 come from the migrated legacy
cut). The margin is small (19 calls): 1419 of the saved calls are passthrough code, 791 are
merged headings. 104 headings of project-3 stay alone (followed by code or a table).

Older checks, rerun at the end; TOTAL lines unchanged from Phase 5:

```
$ python3 scripts/verify/phase3_export_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  exports 24  translated segments 4162  as translation 3956  kept original 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 53)  toc labels changed 405/1885 (527 into translated chapters)  exports with new epubcheck errors 0  failed projects 0
$ python3 scripts/verify/phase4_validate_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  round trip 9491/9491  translated 4162  failing the structural check 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 54; only tag rules 88)  warnings (length_ratio 36, untranslated 8)  length ratio over 2925 segments: p1 0.312 p5 0.333 p50 0.404 p95 0.547 p99 0.976; outside 0.25-0.8: 38 (1.3%)  sources that cannot be cut 0  with translatable attributes 22  failed projects 0
$ python3 scripts/verify/phase5_glossary_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  glossary entries 15, translated 4162, passing the structural check 3956, with term_not_applied 12, terms 4, by category (person: 9 warnings, 2 terms, term: 6 warnings, 2 terms)  failed projects 0
```

All four exited 0; the md5 above was taken before the first and after the last. No
`phase6-segmentation-*` directory left. Review page checked by hand on an invented project on a
local port: the passthrough segment shows its label and note, no editor; a save answers 409;
`/progress` reports `passthrough`.

### Tests

2089 passed (main: 1981). New: `tests/test_technical_segmentation.py` (rules 2 segmentation,
passthrough decision, project state and round trip, progress, context, export in all three
formats including byte identity and a single copy of passthrough code, TOC labels of merged
headings in NCX and nav, a failing merged segment kept whole, rules 1 and rules 2 exports
identical for the same translations, validate, CLI start/init/status, web review page and API,
the context flag on every caller), `TestSegmentationCheck` and `TestSegmentationCheckSurvivors`
(`test_verify_scripts.py`).

Tests changed, none removed:

| Test | Change | Reason |
|---|---|---|
| `test_exporter._project` (helper used by many tests) | takes `rules`, default 1 | its projects stand for existing projects; under rules 2 their segment IDs would change |
| `test_segmenter.py` `test_heading_standalone_segment`, `test_multiple_headings`, `test_heading_breaks_small_merge` | run with rules 1 | they describe standalone headings (plan section 7 announced this); rules 2 counterparts in `TestRules2` |
| `test_blocks.py` `test_ranges_contiguous_and_cover_all_blocks` | both rules, each with its `standalone` list | same |
| `test_blocks.py` `test_standalone_tags` | rules 1 | same |

### Mutation testing

`scripts/verify/mutation.sh main`, mutmut 3.8.1, parallel, about 7 minutes per run. The check
script was added to the mutated files.

| Run | Mutants | Killed | Survived | Timeout |
|---|---|---|---|---|
| 1 | 498 | 459 | 39 | 0 |
| 2 (final) | 498 | 495 | 3 | 0 |

| mutant(s) | done |
|---|---|
| `Segmenter.__init__` 4 (message) | killed: `test_unknown_rules_message` |
| `tasks._worker_loop` 1, 3, 9, 14; `translate._start_parallel` 6, 11–13 (the context flag) | killed: `TestContextOnEveryPath::test_web`, `test_cli[2]` (a rules 2 and a rules 1 project, previous segment already translated) |
| check script `main` 2–5, 8, 11, 15–18, 21, 24, 29–31 (help texts, default directory) | killed: `test_help_in_process`, `test_default_projects_directory_listed` |
| `check` 39, 41 (`dir=parent`) | killed: `test_work_directory_under_the_resolved_temporary_directory` |
| `in_work_tree` 10 (`.GIT`) | killed: `test_looks_for_dot_git` |
| `check_source` 27 (`=` for `+=`) | killed: `test_every_uncovered_segmentation_counted` |
| `run` 27, 50, 56, 57, 69 | killed: `test_first_snapshot_fails`, `test_two_failing_projects_with_two_problems`, `test_counted_project_with_two_problems` |
| `source_of` 8, 10, 16 (encoding) | killed: `test_project_file_read_as_utf8` |
| `get_context` 12 (`nearest_text(1, None)` for `False`) | equivalent: the argument is used only as `(translated and x) or text`; `None` and `False` are both false, so the expression is `text` for every input |
| check script `check_source` 14, 21 (`segment_chapter(None, ...)`) | equivalent: the chapter id only builds segment ids, and the check reads only ranges, fingerprints, block tags and `passthrough`; `f"{None}_seg..."` is a valid string, so every count is the same for every book |

### Known consequences and limitations

- A project created now cannot be loaded by code from before this phase: its `ProjectData(**raw)`
  rejects the unknown `segmentation` key (the unknown segment key `passthrough` would be
  dropped). This matters only for a rollback, as with Phase 0's marker.
- A merged segment that fails the structural check is exported in the original language as a
  whole, heading included (one failure now covers more text). The Phase 4 retry (feedback, then
  a split at a block boundary) applies to merged segments as to any other.
- What a reader sees does not change: each block keeps its own original/translation pair in
  bilingual output (`TestReaderSeesTheSame`).
- Passthrough code cannot be edited in the review; a reader who wants it translated has to
  live with it (it has no comment to translate).
- In parallel runs the context of a segment depends on which neighbour finished first; the
  prompt may get the translation in one run and the source in another.

### Not verified

- No real `claude` call: the effect of larger segments on the structural failure rate, and
  of translated context on quality, is for the owner to measure with the real model.
- The new rules were not applied to any real project (none was created); the check script
  re-cuts copies of the sources only.

## Phase 6 — review round 1

Date: 2026-11-21. Same branch and worktree. Findings of three reviewers, fixed together.

### Changes

| # | Finding | Fix | Tests |
|---|---|---|---|
| 1 | context text: the tag regex broke on `>` in an attribute value and on CDATA, whitespace not collapsed | `translation_text` uses `validation.plain_text` (parsed) and collapses all whitespace to single spaces | `TestRound1ContextText::test_parsed_text[*]` |
| 2 | the check script printed PASS for books with chapters it could not read | a project FAILs with `N chapters could not be checked` (the extractor's failures) and with `no chapter checked` | `TestSegmentationCheckRound1` (partly, wholly unreadable, no chapter) |
| 3 | `covers()` compared the flattened fingerprints | each segment's fingerprints against its own block slice | `test_covers_compares_each_segment_with_its_blocks` (the counterexample `(0,1,[a,b])`, `(1,3,[c])`) |
| 4 | `segmentation` accepted any value; an explicit `1` was dropped on save | only the integers 1 and 2 (`project.check_rules`: not `"2"`, `3`, `true`, `1.0`, `null`) on load, on create and in `Segmenter`; the field is `None` when absent and only then left out, so explicit 1 and 2 save byte-identical | `TestRound1Marker` |
| 5 | passthrough guarded only in the callers | `apply_outcome` returns False, `update_translation`, `mark_reviewed`, `mark_failed`, `mark_translated` raise `PassthroughError` (a `ValueError`) | `TestRound1StateGuards` |
| 6 | search / re-translate page could select passthrough code and start a run of 0 segments | results carry `passthrough`; such rows are labelled, their checkbox disabled, "select all" skips them; a start answering `total: 0` shows an explanation instead of redirecting | `TestRound1Search` |
| 7 | passthrough passed the "nothing translated" export guards and the translated word count | CLI `export` and web export (new guard there, 400) need a translation other than passthrough; the overview's translated words leave passthrough out and its "translated" card says how many are passthrough; the percentage still counts them as done | `TestRound1Guards` |
| 8 | a first-line `#!` counted as a comment line | `validation.is_comment_line`: a `#!` first line is code. Such code without other comments is passthrough, and `check_code` requires the shebang unchanged (all projects: the only change for rules 1); the code prompt says so | `TestRound1Shebang`, `TestPassthrough` cases |
| 9 | the context read was not atomic | `translate_segment(..., mutex=pm.mutex)` (web worker, both CLI paths) reads the context under the project mutex and releases it before the model call; `apply_outcome` and `mark_translated` store the text before the status | `test_mutex_held_only_while_the_context_is_read`, `test_callers_pass_the_project_mutex` |
| 10 | tests that would pass without the behaviour | re-translate endpoint prompts with an already-translated neighbour (rules 2: translation, rules 1: source); a merged heading followed by `<br>` runs exported in all three formats, identical to the rules 1 cut | `TestRound1Retranslate`, `TestRound1MergedExport` |
| 11 | script docstring, `ValueError` | docstring states that a project without its own `source.epub` is read from the path in `project.json` (outside the snapshot) and what PASS proves; any error outside a project ends in `ERROR the check stopped (<type>)` (it already did; now tested) | `test_value_error_of_the_path_check` |
| 12 | project list status counted passthrough | `home._get_status` leaves passthrough out: pending until something is really translated, completed when every other segment is reviewed | `test_overview_and_home_status` |
| 13 | export report could count passthrough as untranslated | never counted as untranslated | `test_export_report_never_counts_passthrough_untranslated` |

Readers of `get_progress()` checked (item 12): changed: home status, CLI `export` guard, web
export guard (new), overview card and word count. Unchanged on purpose: the progress bar and
percentage (layout, review page; passthrough is done), CLI `status` (already says how many of the
translated are passthrough), CLI `start` counter, `/progress` API (reports `passthrough`).

### Checks

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase6_segmentation_check.py --projects-dir <repo>/projects --anonymize
(the same nine lines as in the Phase 6 entry; diffed, identical)
TOTAL projects 8  segments 9520 -> 7870, calls 9520 -> 6432 (ratio 0.676), passthrough 1438, headings merged 1655 of 1770  failed projects 0
$ python3 scripts/verify/phase3_export_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  exports 24  translated segments 4162  as translation 3956  kept original 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 53)  toc labels changed 405/1885 (527 into translated chapters)  exports with new epubcheck errors 0  failed projects 0
$ python3 scripts/verify/phase4_validate_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  round trip 9491/9491  translated 4162  failing the structural check 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 54; only tag rules 88)  warnings (length_ratio 36, untranslated 8)  length ratio over 2925 segments: p1 0.312 p5 0.333 p50 0.404 p95 0.547 p99 0.976; outside 0.25-0.8: 38 (1.3%)  sources that cannot be cut 0  with translatable attributes 22  failed projects 0
$ python3 scripts/verify/phase5_glossary_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  glossary entries 15, translated 4162, passing the structural check 3956, with term_not_applied 12, terms 4, by category (person: 9 warnings, 2 terms, term: 6 warnings, 2 terms)  failed projects 0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

All exited 0. The Phase 6 numbers did not move: no code block of the 8 books has a first-line
shebang as its only comment, and no chapter is unreadable. The stricter shebang check changed
no Phase 4 count (`code_changed 19`). No temporary directory left.

### Tests

2133 passed (before this round: 2089). Changed: `test_exporter._project` writes no marker for
rules 1 (`segmentation=None`, as a project from before Phase 6); context expectations in
`TestContextFromTranslations` and `TestCli` now have collapsed whitespace; the shebang case of
`TestPassthrough::test_comment_lines_decide` is now passthrough (item 8). None removed.

### Mutation testing

| Run | Mutants | Killed | Survived | Timeout |
|---|---|---|---|---|
| round 1 (final) | 561 | 559 | 2 | 0 |

The two survivors are `check_source` 15 and 22 (`segment_chapter(None, ...)`), equivalent as
recorded in the Phase 6 entry. `get_context` was restructured (it returns the neighbouring
segments, then picks the text), so its former `None`-for-`False` survivor no longer exists.

### Not verified

- No real `claude` call.
- The ordering "text before status" in `apply_outcome` / `mark_translated` is not observable
  in a single-threaded test; the mutex test covers the reader side.

## Phase 6 — review round 2

Date: 2026-11-21. Same branch and worktree.

### Changes

| # | Finding | Fix | Tests |
|---|---|---|---|
| 1 | the round 1 shebang rule used absolute line 0, but the source (`element_text(pre)`) keeps the newline after `<pre>` and a cleaned reply loses it: `<pre>\n#!/bin/sh\nls</pre>` failed `code_changed` on every attempt, was not passthrough, and stored rules 1 translations of such blocks would have been kept original | `validation.comment_flags`: the shebang is the first non-blank line starting with `#!`; `has_comment_line` and `code_lines` use it (`is_comment_line` is gone). The code prompt says "第一個非空白行的 #!" | `TestRound2Shebang` (leading newline is passthrough; `check_code('\n#!/bin/sh\n# c\nls', '#!/bin/sh\n# 譯\nls')` passes; a changed shebang fails; a later `#!` is a comment line; blank-only code; the real reply-cleaning path with the fake backend, plain, with blank lines and fenced, and a changed shebang rejected there) |
| 1 | reproducible claim about stored translations | `phase4_validate_check.py` (f): `translated code starting with a shebang N` per project and in TOTAL | `TestValidateCheckShebang` |
| 2 | the web export guard loaded the project outside any `try` (unhandled 500 HTML) | a project gone since the check → 404 JSON; any other load error → 500 JSON `無法讀取專案：…` | `TestRound2WebExportGuard` (not JSON, invalid `segmentation`, gone) |
| 3 | a spine document without a body was skipped silently and the book printed PASS | the check counts spine documents neither returned nor recorded as failed by the extractor that have no body, and FAILs with `N spine documents without a body`. Legitimate skips: the navigation document, and a body without text (nothing to translate, e.g. a cover image) | `TestSegmentationCheckRound2` (mixed readable/bodyless book, legitimate skips pass, the counting helper) |

Other places where source and a cleaned reply can disagree on leading or trailing blank lines:
`check_code` compares `code_lines`, which drop blank lines at both ends, and the commentary
check of `_clean_response` compares the source's first non-blank line: both fine. One remains,
unchanged from `main` and not fixed here: the export fills a code translation unless it equals
the stored source exactly (`exporter._code_piece`), so a reply identical to the source except
for the leading newline the cleaning removed is written as a translation. In `chinese-only` that
block loses its leading blank line; in the bilingual formats it appears twice (original and an
identical-looking copy). This touches only code with comment lines (passthrough code is never
filled) and existing projects as on `main`.

### Checks

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase6_segmentation_check.py --projects-dir <repo>/projects --anonymize
(the same nine lines as in the Phase 6 entry; diffed, identical)
TOTAL projects 8  segments 9520 -> 7870, calls 9520 -> 6432 (ratio 0.676), passthrough 1438, headings merged 1655 of 1770  failed projects 0
$ python3 scripts/verify/phase3_export_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  exports 24  translated segments 4162  as translation 3956  kept original 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 53)  toc labels changed 405/1885 (527 into translated chapters)  exports with new epubcheck errors 0  failed projects 0
$ python3 scripts/verify/phase4_validate_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  round trip 9491/9491  translated 4162  failing the structural check 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 54; only tag rules 88)  warnings (length_ratio 36, untranslated 8)  length ratio over 2925 segments: p1 0.312 p5 0.333 p50 0.404 p95 0.547 p99 0.976; outside 0.25-0.8: 38 (1.3%)  sources that cannot be cut 0  with translatable attributes 22  translated code starting with a shebang 0  failed projects 0
$ python3 scripts/verify/phase5_glossary_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  glossary entries 15, translated 4162, passing the structural check 3956, with term_not_applied 12, terms 4, by category (person: 9 warnings, 2 terms, term: 6 warnings, 2 terms)  failed projects 0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

All exited 0. The Phase 4 line differs only by the new field: no stored code translation of
the 8 books has a shebang as its first non-blank line (0 in every project), so the shebang rule
changes no stored result. All 8 books still PASS the Phase 6 check: none has a bodyless spine
document. No temporary directory left.

### Tests

2148 passed (before this round: 2133). Changed: the Phase 4 check's expected lines gain the new
field (`TestValidateCheck`, 5 places); the code prompt test reads "第一個非空白行". None removed.

### Mutation testing

| Run | Mutants | Killed | Survived | Timeout |
|---|---|---|---|---|
| round 2, 1 | 602 | 598 | 4 | 0 |
| round 2, 2 (final) | 601 | 599 | 2 | 0 |

Run 1: `comment_flags` 5 (no default for `next`: blank-only code would raise) killed by
`test_code_of_blank_lines_only`; `starts_with_shebang` 6 (`'XXXX'` default, equivalent) removed by
rewriting the function without a default. Remaining: `check_source` 15 and 22, equivalent as in
the Phase 6 entry.

### Not verified

- No real `claude` call.

## Phase 6 — review round 3

Date: 2026-11-21. Same branch and worktree.

### Changes

| # | Finding | Fix | Tests |
|---|---|---|---|
| 1 | an indented shebang (a block indented as a whole) was taken for a comment line: not passthrough, a changed shebang passed `check_code`, and the Phase 4 count missed it | `validation.comment_flags` and the Phase 4 check's `starts_with_shebang` test the first non-blank line with its indentation removed | `TestRound3IndentedShebang` (passthrough, changed shebang rejected, unchanged accepted, a later `#!` still a comment line); `TestValidateCheckShebang` (an indented one counted) |
| 2 | plan wording | Phase 6 "As built": "a first non-blank line that starts with `#!` after its indentation"; the code prompt's shebang sentence applies to rules 1 projects too | — |

Changed test: `TestRound1Shebang::test_check_code_keeps_the_shebang` expected an indented `#!`
first line to be a comment; it is now code (this round's rule).

### Checks

Digest `a7197555c962562a530d6de80fa045af` before and after. The four scripts
(`phase6_segmentation_check`, `phase3_export_check`, `phase4_validate_check`,
`phase5_glossary_check`, each with `--projects-dir <repo>/projects --anonymize`) exited 0 and
their whole output is identical to review round 2 (diffed), including
`translated code starting with a shebang 0` in every project and `passthrough 1438` in the Phase 6
TOTAL. No temporary directory left.

### Tests

2149 passed (round 2: 2148).

### Mutation testing

`scripts/verify/mutation.sh main`: `TOTAL mutants 603  killed 601  survived 2  timeout 0
suspicious 0  untested 0`. The survivors are `check_source` 15 and 22 of the Phase 6 check,
equivalent as recorded in the Phase 6 entry.

### Not verified

- No real `claude` call.

## Phase 7 — part A

Date: 2026-11-21. Branch: `phase-7/runtime-control`, worktree `<repo>/.worktree/phase-7`, shared
virtualenv. Part B (adaptive concurrency, backend failover) was split off after measurement and
is not built (plan, Phase 7).

### What was built

| # | Item | What existed (Phase 1) | Built now | Where |
|---|---|---|---|---|
| 1 | Stop / pause end the call in flight | `run_process` already killed the process group on cancel (polls every 0.1 s), and a sequential CLI Ctrl-C already killed the child (the `KeyboardInterrupt` reaches `run_process`). But no caller passed a cancel event: web stop/pause and a parallel CLI Ctrl-C waited for the calls in flight (up to the 120 s timeout). | Web task: `cancel_event`, set by stop and pause, cleared on resume; a cancelled worker puts its segment back in the queue (pending, nothing stored). Parallel CLI: Ctrl-C sets a shared cancel event before joining the workers; a cancelled worker exits. | `src/web/tasks.py`, `translate.py` |
| 2 | Run-level errors | Web: rate limit, auth, backend unavailable paused the task until the user resumed. CLI: all three stopped the run (exit 1). Transient errors: retried at once inside the segment's call budget (`MAX_CALLS` 4). | One rule, `runcontrol.resume_delay(error)`: a rate limit resumes after `retry_after` (when positive) or `RATE_LIMIT_WAIT_SECONDS` 300; auth and backend-unavailable return None (wait for the user: web pause, CLI stop, as before). Web: the worker sets `resume_at`, the coordinator's tick resumes the task and sends `resumed`. CLI sequential: message, flush, sleep, same segment again; CLI parallel: a gate that pauses every worker until the main loop's clock passes the deadline. Transient: the existing per-segment budget, now with a wait of 5, 15, 45 s before each retry (`TRANSIENT_BACKOFF_SECONDS`, the wait returns early on cancel); no second retry layer, because the budget already bounds the retries and a segment that runs out fails `transient` and is retried with `--retry-failed`. Time is injected everywhere: `TaskManager.clock`, `translate._clock`, `translate._sleep`, `TranslationEngine(wait=…)`. | `src/core/runcontrol.py`, `src/core/translator.py`, `src/web/tasks.py`, `translate.py` |
| 3 | Debounced saves | Web: one save per result batch; CLI parallel: one per batch; CLI sequential: one per result. | `runcontrol.DebouncedSaver`: a save at most every `SAVE_INTERVAL_SECONDS` 10 or after `SAVE_EVERY_RESULTS` 20 results, and always on pause (web: the coordinator's tick sees a state other than running; CLI: before a rate-limit wait), stop, completion, error and before the lock is released (web: before the final state, in `_fail_task`; CLI: in `finally` / the interrupt handler, forced). Failure diagnostics are written after the save that stores the result, and (web) only when the segment's revision is still the one the result was applied to. Single writer kept (coordinator / CLI main thread); web edits still save through their transaction. A crash loses at most one window: 10 s or 19 results. (Corrected in review round 1: the window is checked when a result arrives and on the web coordinator's tick; the CLI sequential path checks it only after each result, so it can hold one result unsaved for as long as the next segment takes.) | same |
| 4 | Prompt order | — | Not changed (see below). | `src/core/translator.py` (comment) |
| 5 | Claude CLI flags | `claude -p <prompt> --model <alias>` | measured; all five adopted (below) | `src/llm/claude_cli.py` (`LEAN_FLAGS`) |
| 6 | Throughput script | — | `scripts/verify/phase7_throughput.py` (not run by me) | — |
| 7 | `CopilotCLI` | — | backend `copilot-cli`; `project.json` `backend` (absent = Claude CLI, never written, so existing files round-trip; validated on load like `segmentation`); `llm.BACKENDS`, `llm.backend_for(name)`; every engine and extractor is made for the project's backend (web worker, both CLI paths, re-translate, term extraction); `start --backend/-b`, `--model` checked against the run's backend; web `GET /model` adds `backend` and `backends`, `PUT /model` takes `backend`; the model selector lists `backend：model` for every backend. A rate-limit message in the web says when it resumes. | `src/llm/copilot_cli.py`, `src/llm/__init__.py`, `src/core/project.py`, blueprints, template |

Item 4. Both CLI backends send the whole prompt as one user message (`flatten_prompt`), and
caching works on whole messages up to a cache breakpoint, so moving text inside the message is
not expected to save anything (reasoning; not measured: the overhead run sends an identical
prompt every time, which is cached whole). Moving the glossary and context below the rules
would also put the HTML source directly after the context section, with no line saying where
the source starts: that changes what the model reads. So the order stays as on `main`; the
templates are byte-identical to `main`.

### Claude CLI overhead (`scripts/verify/phase7_cli_overhead.py`)

Claude Code 2.1.329, model haiku, 5 calls per configuration round-robin, prompt = the engine's
text prompt around an invented paragraph (1986 characters). Every configuration adds its flags
to `claude -p <prompt> --model haiku --output-format json`.

```
$ python3 scripts/verify/phase7_cli_overhead.py --raw
base                     n=5 failed=0  wall   3.06 s  api   1.53 s  in    29831  cache_read    29829  out    80  cost $0.00034
system-prompt            n=5 failed=0  wall   2.64 s  api   1.29 s  in    26677  cache_read    26675  out    78  cost $0.00031
tools-none               n=5 failed=0  wall   2.48 s  api   1.17 s  in     5619  cache_read     5617  out    80  cost $0.00010
strict-mcp               n=5 failed=0  wall   2.69 s  api   1.34 s  in    29562  cache_read    29560  out    81  cost $0.00034
setting-sources-none     n=5 failed=0  wall   2.59 s  api   1.39 s  in    20870  cache_read    20868  out    78  cost $0.00025
no-session-persistence   n=5 failed=0  wall   3.03 s  api   1.61 s  in    29831  cache_read    29829  out    89  cost $0.00034
all                      n=5 failed=0  wall   2.43 s  api   1.12 s  in     2294  cache_read     2292  out    82  cost $0.00007
```

(The first round's `cache_read` was lower for some configurations: the cache was cold.) The
transcript column was added after this run; it was taken from the file count of the CLI's
projects folder for the working directory: the 25 calls without `--no-session-persistence`
left 25 new transcripts, the 10 with it none. Re-run of that column, 1 call each:

```
$ python3 scripts/verify/phase7_cli_overhead.py --raw --repetitions 1 --only base no-session-persistence
base                     n=1 failed=0  wall   3.57 s  api   2.01 s  in    29831  cache_read    29829  out    75  cost $0.00034  transcripts 1/1
no-session-persistence   n=1 failed=0  wall   2.93 s  api   1.68 s  in    29831  cache_read    29829  out    79  cost $0.00034  transcripts 0/1
```

Adopted, each for its shown effect on input tokens per call (the wall-time differences of single
flags are within the spread of the raw calls, 2.3–3.2 s; together 3.06 → 2.43 s median, and every
`all` call was at most 2.82 s): `--tools ""` (−24212), `--setting-sources ""` (−8961; also keeps
the user's own CLAUDE.md, memory and hooks out of translation calls), `--system-prompt
'你是一位專業的英文翻譯。'` (−3154; replaces Claude Code's coding-agent system prompt; the request
text is unchanged), `--strict-mcp-config` (−269), `--no-session-persistence` (no transcript per
call; a book is thousands of calls). Together: 29831 → 2294 input tokens, cost per call
$0.00034 → $0.00007 on haiku. The plain-text output is kept (usage still not reported by
`ClaudeCLI`).

### Copilot CLI characterization (1.0.114, invented prompts only)

Flags checked in `copilot --help`. Calls made (all `claude-haiku-4.5`, each an invented sentence;
plus two calls with an empty `COPILOT_HOME`, one of which answered "hello", and two with a
non-existent model):

- `-p <prompt> -s`: exit 0, stdout the reply and a newline, stderr empty.
- `--output-format json`: one JSON object per line (`session.start`, …,
  `assistant.reasoning_delta`, `assistant.message_delta`, `assistant.message` with `content`,
  `session.usage_checkpoint` with `accountingSnapshot.modelMetrics.<model>.usage`
  {`inputTokens`, `outputTokens`, `cacheReadTokens`, …}, `result` with `exitCode`). No account
  data in it (checked: `session.start.context` has only `cwd`).
- Tools: by default 22 tools (10762 tool tokens; 17299 input tokens for one sentence).
  `--available-tools` with no name keeps all 22; `--available-tools view` leaves 1 (2975);
  an unknown name leaves none (2160). `CopilotCLI` passes `--available-tools none`.
- Error, model not available: exit 1; `-s`: stderr `\nExecution failed: Model "x" is not
  available.`; json: a `session.error` event {`errorType`: `query`, `message`: …}, stderr empty.
- Not characterized: a real rate limit (the patterns are the four messages `copilot help limits`
  documents, all containing "rate limit"), expired credentials (an empty `COPILOT_HOME` and an
  invalid `COPILOT_GITHUB_TOKEN` still answered without `--model` and gave "Model … is not
  available" with it, so the error for missing credentials could not be produced; the auth
  pattern is a guess), refusals / content filter (guess), `retry_after` (none observed; the
  default wait applies).
- Each call is saved as a Copilot session under the user's Copilot directory; there is no flag
  to turn that off.

Classification in `CopilotCLI.classify_error`: only error texts (`session.error` type and
message, stderr) are searched, never the reply: `rate.?limit` → `RateLimitError`; `not logged
in|not authenticated|copilot login|unauthorized` → `AuthError`; `Model "…" is not available` →
`BackendUnavailableError` (pauses and waits for the user); `content filter|filtered` →
`RefusalError`; any other error or non-zero exit → `TransientError`. Model list: four models of
the account's catalog (`claude-sonnet-5.5` default, `gpt-6.2-sol`, `gemini-4-carbon`,
`claude-haiku-4.5`), not queried at run time.

Real single-segment call, as in Phase 1 (synthetic project in a temporary directory, the invented
sentence of Phase 1, CLI sequential path → `translate_segment` → `CopilotCLI`):

```
$ python3 translate.py start --project <tmp>/p --backend copilot-cli --model claude-haiku-4.5
使用模型：claude-haiku-4.5（copilot-cli）
繼續翻譯：0/1 已完成 (0.0%)
剩餘 1 個段落待翻譯

[1/1] 翻譯段落 c1_seg0001...
  ✓ 完成（11 字）

全部翻譯完成！
$ echo $?
0
```

Stored segment (`html`/`text`/`fingerprints` omitted): `{'segment_id': 'c1_seg0001', 'status':
'translated', 'translated_text': '<p>小機器人每天早上日出前都澆灌番茄植物。</p>', 'model':
'claude-haiku-4.5', 'usage': {'input_tokens': 3421, 'output_tokens': 538}}`; project
`backend: copilot-cli`, `model: claude-haiku-4.5`. 12.6 s wall for the whole command.

### Throughput (`scripts/verify/phase7_throughput.py`)

Not run by me (it sends book text). For the before/after numbers of the plan:

```
git worktree add <tmp>/main-checkout main
python3 scripts/verify/phase7_throughput.py --projects-dir <repo>/projects --project-index N \
    --segments 50 --model haiku --workers W --anonymize --code <tmp>/main-checkout   # before
python3 scripts/verify/phase7_throughput.py --projects-dir <repo>/projects --project-index N \
    --segments 50 --model haiku --workers W --anonymize                             # after
```

It copies the project to a temporary directory (refused inside a git work tree or the projects
directory; removed in `finally`; the project directory is snapshotted and the run FAILs if it
changed), migrates a schema 1 copy, makes the first 50 pending-or-translated text segments
pending, marks every other pending segment failed (so a normal start queues exactly those 50),
and runs `translate.py start` in-process with the code of `--code`; a counting wrapper around
the backend attributes every call to its segment. It prints wall seconds, seconds per segment,
segments per minute, calls with median and total call time, final statuses, failures by code,
and how many segments needed a retry (more than one call) or a split (two different
single-message requests). The run's own output and log lines (segment ids) are suppressed.

### Checks

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase3_export_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  exports 24  translated segments 4162  as translation 3956  kept original 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 53)  toc labels changed 405/1885 (527 into translated chapters)  exports with new epubcheck errors 0  failed projects 0
$ python3 scripts/verify/phase4_validate_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  round trip 9491/9491  translated 4162  failing the structural check 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 54; only tag rules 88)  warnings (length_ratio 36, untranslated 8)  length ratio over 2925 segments: p1 0.312 p5 0.333 p50 0.404 p95 0.547 p99 0.976; outside 0.25-0.8: 38 (1.3%)  sources that cannot be cut 0  with translatable attributes 22  translated code starting with a shebang 0  failed projects 0
$ python3 scripts/verify/phase5_glossary_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  glossary entries 15, translated 4162, passing the structural check 3956, with term_not_applied 12, terms 4, by category (person: 9 warnings, 2 terms, term: 6 warnings, 2 terms)  failed projects 0
$ python3 scripts/verify/phase6_segmentation_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  segments 9520 -> 7870, calls 9520 -> 6432 (ratio 0.676), passthrough 1438, headings merged 1655 of 1770  failed projects 0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

All four exited 0; their TOTAL lines are identical to Phase 6 review round 3 (this phase changes
no segmentation, validation or export code). No `phase7-throughput-` or other check directory
left.

### Tests

2247 passed (main: 2149). New: `tests/test_runtime.py` (84: stop and pause kill a real child
process in flight, a parallel CLI interrupt kills the other worker's child; web and CLI rate
limits resume by themselves with and without `retry_after`, auth and backend-unavailable wait
for the user; transient retried with 5/15/45 s waits inside the budget, permanent errors not
retried, cancel during a wait; `DebouncedSaver` windows; 100 results → at most 6 writes of
`project.json` (web, CLI sequential, CLI parallel) and all 100 on disk; a pause saves the
window; the failure log waits for the save and is not written for a segment edited first; the
backend contract run on `ClaudeCLI` and `CopilotCLI` with a fake process (registration and
models, success with prompt, model, cancel, timeout, cwd and env passed, system prompt
flattened, a reply quoting a limit is a reply, rate limit / auth / transient classified,
timeout → transient, missing program → unavailable); Copilot command, parsing, usage and
classification; backend selection in `project.json`, CLI and web API, worker following a
backend switch mid-run), `TestThroughputScript`, `TestCliOverheadScript`,
`TestCliOverheadScriptSurvivors` (`test_verify_scripts.py`). Assertions are on process state,
stored state and events; time is injected (no wall-clock assertion; one test bounds a wait at
5 s so that only the injected clock can satisfy it).

Tests changed, none removed:

| Test | Change | Reason |
|---|---|---|
| `test_llm` `TestClaudeCLICommand` (2), `test_glossary` `TestTermExtractorBackend` (2), `test_translator` `test_default_model_passed_to_subprocess` | the command ends with `LEAN_FLAGS` | item 5 |
| `test_cli` `test_rate_limit_or_auth_stops_run` | only `AuthError` | a rate limit no longer stops the CLI run (item 2; tested in `TestCliRateLimitResumes`) |
| `test_cli` `test_lock_released_after_rate_limit_exit`, `test_stop_message` | `AuthError` instead of `RateLimitError` | same |
| `test_cli` `test_supervisor_failure_output`, `test_later_start_fails` | message `翻譯中斷，正在停止進行中的段落……` | the calls in flight are now killed, not awaited |
| `test_web` `test_model_select_reverts_on_error`, `test_unlisted_stored_model_shown` | new template strings | options are `backend/model` keys |
| `test_web` `test_stale_result_not_reported` | the task completes, no error event | with debounced saves a run whose only result is stale has nothing to save, so the failing save is never reached; still nothing reported |
| `test_web` `test_skipped_result_in_queue_not_reported` | passes a `DebouncedSaver` | `_fail_task` takes the saver |
| `test_web` `test_edited_in_flight_segment_not_unsaved` | a saver with a window of one result | the scenario needs the first result's save to fail while a call is in flight |
| `conftest` | autouse: the engine's backoff wait returns at once (recorded), the CLI's rate-limit sleep raises unless a test replaces it | no test sleeps |

### Mutation testing

`scripts/verify/mutation.sh main`, mutmut 3.8.1, parallel (about 10–12 minutes each: more
files changed). Added to the mutated files: `src/core/runcontrol.py`, `src/llm/copilot_cli.py`,
both Phase 7 scripts.

| Run | Mutants | Killed | Survived | Timeout |
|---|---|---|---|---|
| 1 | 1463 | 1338 | 123 | 2 |
| 2 | 1435 | 1403 | 30 | 2 |
| 3 (final) | 1435 | 1418 | 15 | 2 |

Run 1 → 2: the overhead script reads the CLI's JSON strictly (no defaults) and its calls,
help, defaults and header are tested exactly; `_usage` reads the checkpoint inside its `try`;
the web worker makes its engine for the project's `(backend, model)` and remakes it on a
switch (before, the first segment's switch check made the initial values unobservable); tests
for a short `retry_after`, `default_model(backend)`, non-object `data`, joined error texts, the
web time window, the `resumed` event and log line, the failed-save log line, a backend switch
mid-run. Run 2 → 3: an empty `modelMetrics`, the `resumed` event with a completed segment, the
switch log line, the time-window test bounded so the real clock cannot pass it, the help
description being the first doc line.

Remaining, each equivalent for every input:

| mutant(s) | why equivalent |
|---|---|
| `CopilotCLI.classify_error` 1 (`prompt='XXXX'` default) | `prompt` is not read by `CopilotCLI.classify_error` (only Claude's echo rule needs it); the base class signature keeps it |
| overhead `main` 121, 123, 124 (`flush=None/False/omitted` in the `--raw` print) | flushing changes only when the line reaches the terminal, not what is printed |
| overhead `main` 65 (`default=None` dropped for `--only`) | argparse's default for an optional argument is `None` |
| overhead `median` 6 (`float('NAN')`) | `float()` parses `nan` case-insensitively: the same value |
| overhead `prompt_text` 2–7 (engine model / backend) | `_build_prompt` reads neither the model nor the backend; `backend=None` makes a `ClaudeCLI` object, which starts nothing |
| overhead `prompt_text` 8, 20 (`text` `None` / `'XXXX'`) | with `html` given and a text segment, `_build_prompt` formats the HTML prompt and never reads `text` |
| overhead `prompt_text` 9 (`segment_type=None`) | `_build_prompt` only tests `segment_type == SegmentType.CODE`; `None` takes the same branch as `TEXT` |

The two timeouts are detected (the suite does not pass): `wait_or_cancel` 1 (`cancel.wait(None)`)
blocks for ever in `test_default_wait_uses_the_cancel_event`; `_start_parallel` 19 (gate test
inverted) keeps every worker spinning, so the parallel CLI tests never end.

### Known consequences and limitations

- A crash (power loss, `kill -9`) loses up to 10 s or 19 results of a run (CLI sequential:
  also the last result for as long as the next segment takes; see item 3); Ctrl-C, stop,
  pause, errors and the end of a run save.
- `--setting-sources ""` also stops the Claude CLI from loading `env` and `apiKeyHelper` from
  the user's settings file: credentials or an endpoint configured only there are not used, and
  such a setup fails (added in review round 1).
- A rate limit that never clears makes the run retry every 300 s (one quick failing call per
  worker each time) until the user stops it. Neither CLI reports `retry_after`; Claude's
  message names a reset time ("resets 8pm") that is not parsed.
- A run paused by a rate limit does not cancel the other workers' calls in flight; a user
  pause does.
- A segment whose call is cancelled goes to the end of the queue (order changes, nothing else).
- The CLI `start` now keeps running through a rate limit instead of exiting 1.
- `--system-prompt` replaces Claude Code's own system prompt for translation and term
  extraction calls; the effect on translation quality is for the owner's throughput/quality runs
  to show (retry and failure counts are printed).
- A project with `backend` cannot be loaded by code from before this phase (`ProjectData(**raw)`
  rejects the key); only projects where a backend was chosen get the key.
- Copilot calls each leave a session on disk in the user's Copilot directory.

### Not verified

- No throughput numbers (the owner runs the script, before and after).
- No real rate limit, auth failure or refusal of either CLI; Copilot's patterns for these are
  from its documentation or guesses.
- No real `claude` translation call through `ClaudeCLI` with the new flags; the flags were
  exercised only by the overhead script (same flags, same prompt form).
- The web model selector was checked by rendering the page through the Flask test client and
  running its controller in node with stubbed `fetch` (options `claude-cli/…` and
  `copilot-cli/…`, a PUT with `{model, backend}`), not in a browser.
- Windows: unchanged (no `fcntl`).

## Phase 7 — part A, review round 1

Date: 2026-11-21. Same branch and worktree. Findings of three reviewers; each fix has a test
that fails without it (checked by reverting each fix alone and running its test; the
exceptions are named below).

### Changes

| # | Finding | Fix | Tests |
|---|---|---|---|
| 1 | resume cleared the cancel event that calls in flight still held: pause, then a quick resume, let the old child live and its result be stored | resume installs a new `cancel_event`; an event handed to a call is never cleared | `TestRound1CancelGenerations` (real sleeping child: pause and resume at once, the old child is gone, a new one starts) |
| 2 | the pause route toggled on server state, which now changes by itself | body `{"action": "pause" \| "resume"}` (400 otherwise), both idempotent; a pause during a rate-limit wait clears `resume_at` and lasts; the page offers 暫停 during the wait (`auto_resume` in the status, `autoResume` on the page); the tick passes the deadline that was due and `resume_translation(due=…)` re-checks it under the lock | `TestRound1PauseResumeActions` (through the real route: rate limit, user pause twice, clock far on, still paused, resume twice, completed; stale deadlines refused; page strings) |
| 3 | an auth / backend error during a rate-limit wait could be overridden by the automatic resume; a second, longer limit shortened nothing but did not extend | `TaskManager._pause_for`: an error that waits for the user wins (deadline cleared, event sent); a later rate limit extends (`max`), never shortens; a user pause stays. CLI: auth/unavailable stops the run even during the wait (as before) and now also cancels the calls in flight; a later limit extends the gate's deadline | `TestRound1ErrorPrecedence` (web rules; CLI auth during the wait exits 1 without reopening the gate; CLI extension: closed at the first deadline, open at the second) |
| 4, C | the segment was put back before the pause/gate closed; a worker that got a segment from the queue did not look at the pause again | the pause is set (web) / the gate closed (CLI) first, then the segment goes back; after `get()` both paths check pause/stop/gate again and put the segment back | `TestRound1RecheckAfterGet` (web: a pause during `get()`; CLI: a worker waiting in `get()` gets the segment back during the pause and does not send it). The order change alone is not observable in a test: with the re-check after `get()` in place, a wrong order only changes which worker puts the segment back |
| 5 | a retry's backoff ended only on cancel | `TranslationEngine(interrupted=…)`: the default wait polls it every 0.1 s and ends; no call follows (CancelledError, the segment goes back). Web: stop or pause; CLI parallel: run stopped (auth/unavailable, failure streak) or gate closed; a CLI worker whose backoff a pause ended puts its segment back instead of exiting | `TestRound1BackoffInterrupted` (engine; default wait; the web check; CLI: a transient error whose backoff falls in a rate-limit pause makes no call until the gate opens) |
| 6 | `resume_delay` trusted `retry_after` | finite positive int/float (not bool), clamped to [1, 3600]; anything else 300; never raises | `TestRound1ResumeDelay` (0.2, 1, 90, 1e9, inf, nan, True, '12', -1, None, an object) |
| 7 | backend and model read separately | read as one under `pm.mutex` | `TestRound1ModelReadUnderMutex` |
| 8 | sequential CLI: Ctrl-C between `apply_outcome` and the saver lost the result | `flush(force=True)` in `finally` | `TestRound1SequentialForcedFlush` |
| 9 | export read the file while the guard read memory | the export route flushes the running task's saver first (500 if that save fails) | `TestRound1ExportFlushes` |
| 10 | changing only the Claude model wrote `"backend": "claude-cli"`; `start -b` without `--model` replaced the stored model | the requested backend equal to the project's current one (explicit or implicit) stores nothing; `start -b X` keeps the stored model when X is the project's backend | `TestRound1BackendKey` |
| 11 | Copilot: an error event beside a reply failed the call; `filtered` too wide; account names in kept text | exit 0 with a reply fails only for rate-limit or auth patterns; refusal pattern `content.?filter`; e-mail-like strings become `<redacted>` in the message and in the raw output kept by `CLIBackend` (new hook `CLIBackend.redact`, identity for Claude) | `TestRound1Copilot` |
| 12 | throughput script cleanup and output | temp dir made inside the `try`; removal in an unconditional `finally`, a failure reported as `ERROR cannot remove the temporary directory (<type>)`; `BaseException` caught at the top (Ctrl-C prints only the type); setup output suppressed; the whole projects directory snapshotted (as the docstring says); the backend taken from the copy unless `--backend` | `TestThroughputScriptRound1` |
| 13 | tests slept and compared real with injected time; the parallel CLI rate-limit test used a clock jumping 1000 s per read | `task.ticks` (coordinator ticks) replaces every `time.sleep` coordination; the time-window test asserts after ticks, no wall bound; new parallel test with a stationary clock: closed before the deadline (no new call over several main-loop ticks), open after, the printed seconds checked | `TestRound1ParallelCliGate`, the rewritten tests |
| 14 | `flush=` survivors | killed with an observing stream | `TestCliOverheadScriptRound1` |
| 15 | log | `--setting-sources ""` consequence added; the saving description corrected (both in the part A entry) | — |
| B | workers ending on an empty queue while a segment was in flight; completion with a segment still queued | `_workers_done`: no completion while a segment is queued (unless stopping): workers are started again; every resume tops the workers up to `num_workers` (also without a count) | `TestRound1WorkersComeBack` |
| D | a call from before a pause and resume that ends in a run-level error paused the resumed run | web: the worker compares the cancel event it called with to the current one; an older call's error pauses nothing (its segment is sent again) | `TestRound1StaleRunError` |
| E | the error banner stayed after a resume | cleared when the pause/resume answer says `running` (the automatic `resumed` event already cleared it) | page string in `TestRound1PauseResumeActions` |
| F | `PUT /model` with a non-string `backend` → 500 | 400 | `test_web_non_string_backend` |
| G | Copilot: plain-text error on stdout lost; exit 0 with an error event and no reply | non-JSON stdout lines are the message when there is no error event; exit 0 with an error and no reply is classified (it already was: the reply check only spares runs with a reply) | `test_plain_stdout_only_without_error_events`, `test_error_without_a_reply_is_classified` |
| H | home listing read the file during a run | the running task's project from memory (`get_project_manager`) | `TestRound1HomeListing` |

Checked and not changed:

- A (parallel CLI drops results after a run-level stop): false. The main loop runs while any
  worker is alive or the result queue is not empty, so every result queued before a worker
  ends is applied and flushed before the join; the join only waits for threads that have
  already ended. Part of A was taken: the stop now also cancels the calls in flight (item 3).
- D in the parallel CLI: not changed. The gate has no generations; a call started before the
  gate closed that ends in a rate limit after it reopened closes it again. That is a real
  limit reported by the service, so the pause is right; recorded as a limitation.
- The two claims the orchestrator had already disproved (`run_process` pipes, `--code`) were
  not looked at again.

Settled decisions (not done, as decided):

- Re-translate from the review page stays outside project pause/stop (one user-requested call).
- A cancelled `segment_ids` or retry-failed run does not keep its unfinished intent across a
  stop; the segments keep their previous state.
- No timer thread for saves during a call; no stop button.
- Part B.

### Throughput (run by the orchestrator on the owner's machine)

`scripts/verify/phase7_throughput.py`, haiku, the same project copy, anonymized; before =
`--code` a checkout of `main`, after = this branch:

```
50 segments, 4 workers, before: wall 78.0 s, calls 60, failed 3 (key_mismatch 3), retry 6, split 2, per call median 3.15 s
50 segments, 4 workers, after:  wall 79.0 s, calls 51, failed 0, retry 1, split 0, per call median 2.47 s
repeat, before:                 wall 79.7 s, calls 65, failed 4 (key_mismatch 3, tag_count 1), retry 9, split 3, median 2.88 s
repeat, after:                  wall 85.2 s, calls 52, failed 0, retry 2, split 0, median 2.76 s
20 segments, 1 worker, before:  wall 104.9 s, calls 25, failed 1 (tag_count 1), retry 3, split 1, median 2.67 s
20 segments, 1 worker, after:   wall 100.2 s, calls 20, failed 0, retry 0, split 0, median 2.79 s
```

Wall time is unchanged within the run-to-run spread. After the change there were fewer
failures and retries: 0 of 120 segments failed, against 8 of 120 before. Which flag causes that
was not isolated. The orchestrator also checked in the browser: a web pause kills the Claude
process within 1 s with the segments left pending, resume completes the run, and one invented
segment was re-translated through `copilot-cli` chosen in the model selector.

### Checks

Before and after: `stat -f '%m %z' <repo>/projects/*/project.json | md5` =
`a7197555c962562a530d6de80fa045af`, unchanged. The four older read-only checks
(`--projects-dir <repo>/projects --anonymize`), TOTAL lines identical to part A:

```
TOTAL projects 8  exports 24  translated segments 4162  as translation 3956  kept original 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 53)  toc labels changed 405/1885 (527 into translated chapters)  exports with new epubcheck errors 0  failed projects 0
TOTAL projects 8  round trip 9491/9491  translated 4162  failing the structural check 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 54; only tag rules 88)  warnings (length_ratio 36, untranslated 8)  length ratio over 2925 segments: p1 0.312 p5 0.333 p50 0.404 p95 0.547 p99 0.976; outside 0.25-0.8: 38 (1.3%)  sources that cannot be cut 0  with translatable attributes 22  translated code starting with a shebang 0  failed projects 0
TOTAL projects 8  glossary entries 15, translated 4162, passing the structural check 3956, with term_not_applied 12, terms 4, by category (person: 9 warnings, 2 terms, term: 6 warnings, 2 terms)  failed projects 0
TOTAL projects 8  segments 9520 -> 7870, calls 9520 -> 6432 (ratio 0.676), passthrough 1438, headings merged 1655 of 1770  failed projects 0
```

### Tests

2307 passed (part A: 2247). Changed existing tests, with the reason:

- `test_injected_wait_and_cancel_during_backoff`: the injected wait takes the new third
  argument `interrupted`.
- `TestPhase7Survivors::test_short_positive_retry_after`: 0.5 s is now raised to 1 s
  (item 6); 1.5 s added to keep a fractional value.
- `TestDynamicWorkers::test_resume_api_passes_workers`: the body names `"action": "resume"`
  (item 2).
- Copilot classification case `(2, 'garbage', '')`: the plain stdout is now the message (G).
- `TestCliRateLimitResumes::test_resumes_without_user`: sequential only; the parallel case,
  which used a clock that jumped 1000 s per read, is replaced by `TestRound1ParallelCliGate`
  (item 13).
- Five tests that slept 0.1 s for coordinator ticks wait for `task.ticks` instead; the
  time-window test no longer compares with real time (item 13).

### Mutation testing

`scripts/verify/mutation.sh main`, two full runs (no separate targeted run: the round's
mutants are all on changed lines, which is what the script mutates).

| Run | Mutants | Killed | Survived | Timeout |
|---|---|---|---|---|
| 1 (after the fixes) | 1722 | 1671 | 47 | 4 |
| 2 (final, after the survivor tests) | 1716 | 1693 | 13 | 10 |

Run 1's survivors were killed by `TestRound1Survivors`, `TestThroughputScriptRound1Survivors`
(event data, the tick's due argument and count, worker top-up: count with a live worker,
daemon threads, log line, nothing without a project, the worker going on after a put-back
and after a cancelled call, the start-failure message, the backoff slices with a fake clock,
plain text before stderr, the default model, the work-tree message), or removed by
simplifying the code (`error.retry_after` instead of `getattr`; Copilot's pattern list built
instead of skipped with `continue`).

Timeouts count as detected: each one makes a worker loop spin or a wait never end
(`wait_or_cancel` 5, 8, 9; `_start_parallel` 23, 24, 25, 28, 47, 67, 74: the gate or the
put-back inverted).

The 13 survivors of run 2, all equivalent:

| Mutant | Why it is equivalent |
|---|---|
| `classify_error` 1 (`prompt` default `'XXXX'`) | Copilot's `classify_error` never reads `prompt` (the parameter is the base class's signature) |
| `_tick` 17 (`due = ""` for `None`) | `""` reaches `resume_translation(due="")`, which refuses it: `resume_at` is never `""`; nothing resumes, as with `None` |
| overhead `main` 65 (`--only` without `default=None`) | argparse's default for `nargs='*'` is `None` |
| overhead `median` 6, `prompt_text` 2–9, 20 | the same mutants as part A, justified there |

### Known limitations (recorded, not fixed)

- Parallel CLI: a stale call's rate limit after the gate reopened closes it again (D above).
- The backoff wait looks at `interrupted` every 0.1 s (`WAIT_SLICE_SECONDS`), like
  `run_process` looks at cancel.

### Not verified

- No real model call in this round (the orchestrator's browser and throughput runs above are
  the real checks).

## Phase 7 — part A, review round 2

Date: 2026-11-21. Same branch and worktree. Each fix has a test that fails without it
(checked by reverting each fix alone and running its test).

### Changes

| # | Finding | Fix | Tests |
|---|---|---|---|
| 1 | a pause for a run-level error only closed the pause gate: other workers' calls went on with the old cancel event, which nothing set after the resume replaced it (child alive, result stored while paused, stop hanging on the join) | that pause also sets the current cancel event, like a user pause (the calls end, their segments go back to the queue); resume sets the retiring event before it installs the new one; the `is generation` check for stale run errors stays | `TestRound2RunLevelPauseCancels` (real sleeping children: W1's rate limit ends W2's child, both segments pending; after resume, a pause ends every child; after another resume, stop ends them and the coordinator ends) |
| 2 | `resume_delay(10**400)` raised `OverflowError` | ints skip `math.isfinite` and are clamped before the float conversion | `test_huge_retry_after` |
| 3 | `flush()` decided "nothing pending" before taking the mutex | decided under the mutex; the web coordinator applies and adds a result under it, so a flush from the export sees it | `test_flush_decides_under_the_mutex` |
| 4 | the stream's first `status` did not carry an automatic wait in progress | `resume_in` (seconds left, at least 0; `null` without a wait) in the snapshot; the page then shows the pause button and the message | `test_stream_snapshot_has_the_wait`; two existing snapshot assertions gain `"resume_in": null` |
| 5 | a later, longer rate limit extended the wait silently | it sends `rate_limit` with the new `resume_in` | `test_extension_sends_the_new_wait` |
| 6 | the whole drained batch was added before the window was checked | `maybe_save()` after every accepted result (web coordinator and CLI `apply`) | `TestRound2WindowInBatches` (web and CLI: the coordinator held until 45 results are queued, at most 20 unsaved at any save); `TestHundredResults` now also requires at least 5 saves (it would fail if saving happened only at the end) |
| 7 | the final snapshot ran before the copy's removal and could skip it | removal first, in the `finally`; the snapshot after it | `TestThroughputScriptRound2` (a failing final snapshot: the copy is gone, `ERROR the measurement stopped (FileNotFoundError)`) |
| 8 | `src/web/blueprints/home.py` was not mutated | added to `FILES` in `scripts/verify/mutation.py` | the mutation run below |
| 9 | `start -b/-m` normalised and checked against an unlocked load | moved into `_start`, against the project loaded under the lock | `TestRound2CliBackendUnderTheLock` (the web switches backend and model just before the lock is taken: the stored model stays) |
| A | a rejected reply with cancel set was recorded as `FAILED` | `translate_segment` raises `CancelledError` instead of returning a failure when cancel is set (also covers a session that fails without a call) | `test_cancelled_rejected_reply_is_not_a_failure` |
| B | a stopping run could resume by itself (failure-streak stop during a rate-limit wait); the CLI tick could announce or reopen after a stop | `resume_translation` refuses a stopping task (this covers the streak stop, which then ends the run as failed); `stop_translation` also clears `resume_at`; the CLI tick returns after the flush once `error_event` is set | `test_stopping_task_does_not_resume`, `test_cli_stopping_run_does_not_reopen_the_gate` (the stop moves the clock past the deadline and waits for two ticks: no `繼續翻譯`) |
| C | the page kept `autoResume` across runs | reset in `startTranslation()` and in the `stopped`, `complete` and failed-run `error` listeners | `test_page_resets_the_wait` |
| D | a worker woken by a resume that found the task paused again exited for good | it goes back to the top of its loop, which waits for the pause, checks stop and the worker count | `test_worker_goes_on_after_a_resume_and_a_new_pause` |
| E | `_top_up_workers` counted alive threads, including one that has ended its loop | counts `active_workers` (callers hold the task lock) | `test_top_up` (a thread alive but no longer counted gets replaced) |
| F | a segment that failed in the run and was translated again from the review page inside the save window got the run's failure diagnostics | written only when the segment's status is still the outcome's | `test_no_failure_log_for_a_segment_translated_again` |

All of the third reviewer's items A to F were true; each was a small change.

### Checks

Before and after: `stat -f '%m %z' <repo>/projects/*/project.json | md5` =
`a7197555c962562a530d6de80fa045af`, unchanged. The four older read-only checks: TOTAL lines
identical to round 1 (the four lines above).

### Tests

2322 passed (round 1: 2307). Changed existing tests: the two stream-snapshot assertions in
`test_web.py` gain `"resume_in": null` (item 4); `TestHundredResults` gains the lower bound
(item 6); `TestRound1Survivors::test_top_up` adds a thread that is alive but no longer
counted (E).

### Mutation testing

`scripts/verify/mutation.sh main`, now also over `src/web/blueprints/home.py`:

| Run | Mutants | Killed | Survived | Timeout |
|---|---|---|---|---|
| 1 (after the fixes) | 1766 | 1746 | 15 | 5 |
| 2 (final) | 1765 | 1743 | 13 | 9 |

Run 1's two new survivors: `_pause_for` 31 (`remaining` sign in the extension event), killed
by giving the test a completed count; `_run_translation` 35 (`resume_at = ""` in the
failure-streak stop), removed with the line itself, because `resume_translation` already
refuses a stopping task. Every mutant of `home.py` was killed. Timeouts count as detected
(the same inverted gates and waits as in round 1). The 13 survivors of run 2 are the 13
equivalent mutants of round 1, with the reasons given there.

### Not verified

- No real model call in this round.

## Phase 7 — part A, review round 3

Date: 2026-11-21. Same branch and worktree. Minor fixes; each has a test that fails without
it (checked by reverting each fix alone).

### Changes

| # | Finding | Fix | Tests |
|---|---|---|---|
| 1 | round 2's `seg.status == outcome.status` skipped the diagnostics of a failed call on a TRANSLATED or REVIEWED segment (`apply_outcome` keeps that status) | the coordinator records the segment's status right after `apply_outcome` with the saver item; `after_save` compares with that | `test_failed_retranslation_of_a_translated_segment_keeps_diagnostics`; round 2's `test_no_failure_log_for_a_segment_translated_again` still passes |
| 2 | a failed run reported `auto_resume: true` | `_fail_task` clears `resume_at` | `test_failed_task_does_not_wait` |
| 3 | the automatic `resumed` event was published after the workers woke, so a user pause in between was followed by a stale `resumed` | `resume_translation(due=…)` publishes it under the lock, before `pause_event.set()` | `test_resumed_event_before_the_workers_wake` |
| 4 | a pause followed by a resume before the next tick skipped the pause's flush | a pause (user or run-level) sets `flush_due`; the next tick flushes even when the task is running again | `test_pause_flush_survives_a_quick_resume` |
| 5 | an older CLI that rejects a flag was a transient error (four calls and backoff per segment) | `UNKNOWN_OPTION_PATTERN` (`unknown option`, `unrecognised/unrecognized option/argument`) on a failed run is `BackendUnavailableError` for both CLIs: the web run pauses, the CLI stops | `test_unknown_option_is_unavailable` |
| 6 | a save failing partway through a batch left the rest of the batch out of the unsaved report | the rest goes back to the result queue, which `_fail_task` reports | `test_save_failing_in_a_batch_reports_the_rest` |
| 7 | CLI: a second Ctrl-C during the join skipped the forced flush; a failing final flush in the sequential path reported nothing | parallel: the drain and the forced flush run in a `finally` around the join; sequential: a failing final flush prints the unsaved segments (shared `_report_unsaved`) | `test_cli_second_interrupt_during_the_join_still_saves`, `test_cli_sequential_failed_final_save_is_reported` |

### Known limitations (recorded, not fixed)

- Item 1's check cannot tell one case apart. The run translates a segment, the review page
  then translates it again and that call fails, all inside one save window. `apply_outcome`
  keeps TRANSLATED for the failure, so the status and revision are the same as when the run's
  success was applied, and the deferred `after_save` of that success removes the
  diagnostics the review page just wrote. The translation itself is right; only the
  failure note is lost. A fix would need a per-segment change counter beyond `revision`.

### Checks

Before and after: `stat -f '%m %z' <repo>/projects/*/project.json | md5` =
`a7197555c962562a530d6de80fa045af`, unchanged. The four older read-only checks: TOTAL lines
identical to round 1 (md5 of the four lines the same).

### Tests

2330 passed (round 2: 2322). No existing test changed.

### Mutation testing

`scripts/verify/mutation.sh main`, one full run:

| Mutants | Killed | Survived | Timeout |
|---|---|---|---|
| 1790 | 1767 | 14 | 9 |

Timeouts are the same inverted gates and waits as before (detected). Survivors, all
equivalent:

| Mutant | Why it is equivalent |
|---|---|
| `_tick` 8 (`flush_due = None` for `False`) | the flag is only read as `not task.flush_due`; `None` is false like `False` |
| `_tick` 21 (`due = ""` for `None`) | round 1's `_tick` 17 under its new number: `resume_translation(due="")` refuses it |
| `classify_error` 1, overhead `main` 65, `median` 6, `prompt_text` 2–9, 20 | as in rounds 1 and 2 |

## Phase 7 — part A, review round 4

Date: 2026-11-21. All three reviewers approved round 3. This is one last small pass before
the merge; each fix has a test that fails without it (checked by reverting each fix alone).

### Changes

| # | Finding | Fix | Tests |
|---|---|---|---|
| 1 | `test_save_failing_in_a_batch_reports_the_rest` depended on scheduling: under load the coordinator's batches differed, and calls cancelled by the failure reported fewer unsaved segments (seen once by the orchestrator: 1 failure, 2329 passed) | the test fixes its batches: every call after the first waits until the first result has been applied alone; the coordinator is then held until the other four are queued, so they form one batch; the exact unsaved list is asserted | the same test. The other tests added in this phase were checked for the same pattern; the throughput tests that asserted no `phase7-throughput-*` directory anywhere in the system temp directory (which another suite running at the same time can have) now check only the directory they made. The repeated runs below found two more, older tests made racy by this round's item 2: `TestRound3UnexpectedStreakWeb::test_run_stops_after_five` and `TestRound4StreakKeepsInFlightResults::test_good_result_after_streak_saved` in `test_web.py` need a sixth call in flight when the failure streak stops the run, and a worker now makes no call once the run is stopping. Under load the stop sometimes came first (1 failure each in 6 runs). Both now hold the streak's stop until the sixth call has started. `TestRound1BackoffInterrupted::test_engine` now becomes interrupted after the first call, since no call is made at all when interrupted before it |
| 2 | feedback retries and split calls went to `backend.complete` while the parallel CLI gate was closed or the web run paused | `_Session.call` checks `interrupted` before every call and raises `CancelledError` (the segment goes back) | `TestPhase7Round4Interrupted` in `test_translator.py` (a feedback call and a split call not made once interrupted; uninterrupted unchanged) |
| 3 | the page's `paused` listener did nothing and `resumed` set running unconditionally: a reordered or second-tab event showed the wrong state | both refresh from the status (`fetchStatus(false)`, which sets state, error and `autoResume` without reconnecting the stream) | `test_page_takes_the_server_state_on_paused_and_resumed` |
| 4 | sequential CLI: "已完成的進度已儲存" was printed before the forced flush | a run-level stop inside the loop (`_RunStopped`) is reported after the `finally` saved; a failing save prints the unsaved segments and not that line | `test_cli_saved_message_only_after_the_save`, `test_cli_saved_message_after_a_good_save` |
| 5 | an `after_save` that raised after a successful save made the save look failed | logged; the save stands (only diagnostics are lost) | `test_after_save_error_is_logged` |

### Settled / known limitations (not done, as decided)

- Copilot error text: only e-mail-like strings are redacted; other login names that the CLI
  may print are not.
- A second Ctrl-C in the throughput script can race the removal of its copy (the copy may
  then be left in the system temp directory).

### Repeated suites under load

Six full suites, three times two at once on the same machine (`-p no:cacheprovider`):

- First series, after the round's fixes: 4 of 6 runs passed. The other two failed once
  each, in the two failure-streak tests named under item 1 (`2336 passed, 1 failed`). Two
  further failures, in every run, were tests this round's changes had broken:
  `TestRound1BackoffInterrupted::test_engine` and a page-source slice in `test_web.py`
  (`async fetchStatus()` renamed). All four tests were corrected.
- Second series, after the corrections: 6 of 6 runs passed, 2337 tests each, no failure.

### Checks

Before and after: `stat -f '%m %z' <repo>/projects/*/project.json | md5` =
`a7197555c962562a530d6de80fa045af`, unchanged. The four older read-only checks: TOTAL lines
identical to round 1 (md5 of the four lines the same).

### Tests

2356 passed (round 3: 2330). Changed existing tests:
- `TestRound1BackoffInterrupted::test_engine`: becomes interrupted after the first call, since
  no call is made at all when interrupted before it (item 2).
- The page-source slice in `test_web.py` ends at `async fetchStatus(connect = true)` (item 3).
- `test_save_failing_in_a_batch_reports_the_rest` and the two failure-streak tests are now
  deterministic (item 1).
- The two throughput tests check only the directory they made (item 1).
- `TestRound1ParallelCliGate::test_gate` also requires the pause to be announced exactly once
  and `繼續翻譯` on a line of its own.

### Mutation testing

`scripts/verify/mutation.sh main`:

| Run | Mutants | Killed | Survived | Timeout |
|---|---|---|---|---|
| 1 (after the fixes) | 1804 | 1580 | 209 | 15 |
| 2 (the same commit again, no other load) | 1804 | 1586 | 208 | 10 |
| 3 (after the survivor tests) | 1795 | 1724 | 62 | 9 |
| 4 (final) | 1795 | 1732 | 54 | 9 |

**Earlier mutation counts were too high.** Up to round 3, `TestThroughputScript::test_measures_a_copy`
asserted that no `phase7-throughput-*` directory existed anywhere in the system temporary
directory. mutmut runs many mutants at once, and their copies of that test made such
directories at the same time. So the test failed for reasons unrelated to the mutant and
"killed" every mutant linked to it: the throughput script's, and those of `translate.py` and
`translator.py` that the measurement runs. With item 1's correction (the test checks only
its own directory), runs 1 and 2 showed about 195 survivors that earlier rounds had counted
as killed. The flaky failure-streak tests in `test_web.py` had the same effect on a smaller
scale. Rounds 1–3's mutation tables above are therefore too optimistic for those three
files; this round's numbers are the first reliable ones for them.

Run 3's tests cover them: the script's help text, required options, defaults and types, the
exact report lines (arithmetic, defaults, split detection), `prepare`, `snapshot`,
`in_work_tree`, `Recorder`, `CountingBackend`, the error messages without a path, the
temporary directory's arguments, a fake clock for the wall and call times, the logging
handler removed, the copy's backend, `--backend` reaching the run, warnings not counted as
failures, and the exit code for unfinished segments. In the CLI they cover: a second pause
announced again, the announcement made once, the project's backend in parallel runs, the
`使用模型` line, queued results saved at Ctrl-C with their diagnostics, and the sequential
window on the injected clock. They also cover the `CancelledError` messages, the backoff
wait getting `interrupted`, and the save count. Two changes simplify the code: the pause
record starts empty, and `_RunStopped` carries its error in `args`.

Timeouts count as detected (inverted gates and waits). The 54 survivors of run 4, all
equivalent or not reachable in a test process:

| Mutants | Why |
|---|---|
| `classify_error` 1, `_tick` 8 and 21, overhead `main` 65, `median` 6, `prompt_text` 2–9, 20 | as in rounds 1–3 |
| `_worker_loop` 4 (`and` for `or` in the re-check after `get()`) | since item 2 the engine raises `CancelledError` before any call while paused, so the segment is put back either way |
| `_start_parallel` 19 (gate poll 1.5 s for 0.5 s) | only how often a waiting worker looks at the gate |
| `_start_parallel` 23, 27 (`continue` → `break` after putting a segment back) | that worker ends; the worker that hit the rate limit always goes on, so the run completes with fewer workers |
| `_start_parallel` 24 (`and` for `or`), 25, 32 (`break` → `return`) | with `error_event` set the loop ends at its next check; nothing follows the loop in the worker |
| `_start_parallel` 35, throughput `prepare` 12 (`None` for `False`) | read only as `not …`: `None` is false like `False` |
| throughput `in_work_tree` 10 (`.GIT`) | this machine's file system ignores case |
| throughput `main` 75 (no `default=None`) | argparse's default is `None` |
| throughput `measure` 49, 52, 55, 63–67 | the `--code` path: in a test process `src` is already imported, so another checkout's code only takes effect in a new process (tested there, not linked by mutmut) |
| throughput `measure` 85, `run` 27 (migration) | only for a project of the old schema; invented projects cannot be migrated back without real old data |
| throughput `run` 21, 22, 118, 119 (`redirect_*(None)`) | `print` and `click.echo` write nothing when the stream is `None`: output suppressed as before |
| throughput `run` 121, 123, 124 (`standalone_mode`) | `SystemExit` is caught around the call; `None` is false like `False` |
| throughput `run` 135, 137, 143 (encoding) | the locale is UTF-8 here; `UTF-8` is the same codec |
| throughput `run` 152, 154, 157, 158 (status default) | every stored segment has a status |
| throughput `run` 55, 58 | the `getattr` default and the `else` branch serve the code of `main` only (no `backend_for`): reachable with `--code` in a new process |
| throughput `run` 6 (`str(None)`) | only the copy directory's name |
| throughput `run` 72 (`""` for `None`) | no call is made outside a segment |
| throughput `run` 83 (`is None`) | the counting backend already wraps the backend `--backend` names, so whether `--backend` is also passed on does not change where the calls go |

## Phase 7 — part A, pre-merge stability

Date: 2026-11-21. The orchestrator ran the full suite 12 times, three suites at once, on the
round 4 commit. 9 runs passed (2356 tests); 3 runs failed one test each, three different
tests. Cause of each:

| Test | Cause | Fix |
|---|---|---|
| `TestStopKillsCallInFlight::test_stop` (a worker thread still alive after the coordinator ended; `active_workers` 0, 12 ticks: the coordinator had not hung) | **product**: the coordinator ended as soon as the worker count reached 0, which a worker decrements in its `finally`, so the thread could still be finishing. Nothing more was written, but `threads_alive()` was briefly true after the run ended, and a new start right after a stop could be refused as still running | the coordinator joins its worker threads after the last one left its loop, before the final save and state, so an ended run has no thread left. Deterministic test `TestPreMergeRunEndsWithItsThreads`: a worker lingers after its loop; the run's `complete` comes only after that thread ended (it fails without the join) |
| `TestRound3StaleResultInFailureDrain::test_edited_in_flight_segment_not_unsaved` (`in_flight.wait(10)` false) | **test**: it assumed the second worker's call starts before the first result's save fails and stops the run. Under load the stop came first, and a stopping run makes no new call | the first segment's reply now waits until the second call is in flight |
| `TestRound2WindowInBatches::test_web` (the run failed: the patched coordinator step timed out waiting for 45 queued results) | **test**: it assumed the coordinator's first held step comes while at least 45 results are still to come. Under load the coordinator had already applied more than 15 | deterministic batches as in round 4 item 1: the first result is applied alone (every other call waits for it), then the coordinator is held until the other 59 are queued |

The tests added or changed in this phase that start a web task or CLI workers were checked
for the same assumptions: batch order, a thread having reached a point, time bounds. One more
was found and changed: `TestStopKillsCallInFlight::test_pause_kills_and_resume_goes_on`
asserted that the segment was back in the queue as soon as the child was gone. The worker
puts it back only after its call has ended, so the test now waits for that. The CLI batch
test's hold got a longer bound (a condition wait, not a time assertion).

After these changes: the full suite 12 times, four series of three suites at once
(`-p no:cacheprovider`): **12 of 12 passed, 2357 tests each, no failure** (each run 4 to 4.5
minutes under that load).

The coordinator's join is a product change, so the mutation run was repeated
(`scripts/verify/mutation.sh main`): 1796 mutants, 1734 killed, 53 survived, 9 timeout.
The new lines' mutants were all killed. The survivors are round 4's final list without
`_start_parallel` 32 (`break` → `return` after an auth stop), which is now killed. The
reasons are the ones given in round 4.

### Second pass: mechanisms

The orchestrator's own 12-run series (three at once) on the commit above: 10 clean, 2 runs
with one failure each. Each failure below gives its interleaving, a forced reproduction (a
temporary test, not committed, that holds one thread at the exact point and fails every
time), the decision and the fix. The same write-up is given for the first pass's three
failures.

**1. `TestCoordinatorFailure::test_save_failure_wakes_paused_worker`**: coordinator alive
after 20 s; the only log line is `[W1] rate_limit, pausing: limit`.

- Interleaving: two workers, queue `[ch1_seg0003, ch2_seg0001]`.
  1. Worker A takes ch1_seg0003, but has not reached its call yet.
  2. Worker B takes ch2_seg0001, gets `RateLimitError` at once and runs `_pause_for`,
     `pause_event.clear()` and `cancel_event.set()` (round 2 item 1).
  3. Worker A arrives in `_Session.call`, finds `engine.interrupted()` true (round 4 item 2)
     and raises `CancelledError` without a call. It puts its segment back and waits at the
     top of its loop on `pause_event.wait()`.
  4. The coordinator ticks: the state is paused, so `saver.flush()` runs, but nothing is
     pending and it returns without saving. The mocked failing `save` is never called, so
     there is no failure. `_workers_done` is false (two workers in their loops).
  5. The run correctly waits for its automatic resume in 300 s. The test's `paused.set()`
     releases a call that never started, and `_finish` times out.
- Forced: worker A held at `translate_segment` until the state is paused. The e967fd2 test
  body then failed 5 of 5 times with "coordinator did not end". State paused, both segments
  queued, nothing pending, events `['rate_limit']`: the same picture as the failing run. A
  stop then ends it at once.
- Decision: **test**. No thread is blocked; the product does what a paused run should. The
  test assumed worker A's call was in flight before worker B's rate limit.
- Fix: the rate limit is raised only once the other call is in flight. With worker A held as
  late as the new test allows (until worker B's call has started), it passed 5 of 5.

**2. `TestTimeoutKeepsOutput::test_output_kept_is_bounded`**: `output == ''`.

- Interleaving: `run_process` sets `deadline = monotonic() + 1` before the child exists. The
  child interpreter is still starting (not yet at its `print`) when `communicate(timeout=…)`
  last times out after the deadline. `run_process` then raises `TimeoutExpired`, and
  `_kill_group` kills the group and collects stdout and stderr: nothing had been written, so
  both are empty.
- Forced: the e967fd2 body with a child that sleeps 1.5 s before writing (as a start slowed
  by load) failed 5 of 5 times with `'' == 'x' * 20000`.
- Decision: **test**. `run_process` keeps everything written before the kill. `communicate()`
  keeps what it has read across its timeouts, and `_kill_group` drains the pipes after the
  kill. The new form shows this even with the 1.5 s slow start (3 of 3).
- Fix: both timeout tests replace `run_process`'s clock. It stands still until the child,
  after writing and flushing, creates a `ready` file, and is past every deadline after.

**First pass, retroactively:**

- `TestStopKillsCallInFlight::test_stop` (**product**, fixed in ad61da8):
  - A worker runs `active_workers -= 1` in its `finally` and is then still executing as a
    thread.
  - The coordinator's `_workers_done` sees 0 and the coordinator ends.
  - The test's `threads_alive()` sees the worker thread.
  - Forced by `TestPreMergeRunEndsWithItsThreads`: a worker lingers after its loop; it fails
    without the join.
- `TestRound3StaleResultInFailureDrain::test_edited_in_flight_segment_not_unsaved` (**test**):
  - Worker B has taken ch2_seg0001 but not called yet.
  - Worker A's ch1_seg0003 result arrives. With a window of one, `maybe_save` raises and
    `_fail_task` sets stop and cancel.
  - Worker B's `interrupted` check raises `CancelledError`, so there is no call, and the
    test's `in_flight.wait(10)` returns false.
  - Forced: worker B held at `translate_segment` until the state is failed; failed 3 of 3.
- `TestRound2WindowInBatches::test_web` (**test**):
  - The coordinator's first batch took 16 or more results.
  - The hook after it waited for 45 queued results while at most 44 remained.
  - The 20 s condition wait expired, so the coordinator failed.
  - Forced: the coordinator's first `get` held until 16 results were queued; the e967fd2 body
    failed every time ("timed out waiting", state failed).

**Classes of cause, and where else they were looked for (by reading):**

- **A. A test assumes thread A reaches X (its call in flight, a coordinator step) before
  thread B reaches Y (a pause, a stop, a failure, a number of results).** Behind
  `test_save_failure_wakes_paused_worker`, the stale-drain test, the window-batch test,
  round 4's batch-failure test and the two streak tests. Round 4 item 2 (no call once paused
  or stopped) widened the window for every test of this kind. Checked:
  - every test in `test_web.py`, `test_runtime.py` and `test_cli.py` that starts a web task
    with 2 or more workers or the CLI with `--workers` 2 or more;
  - in `test_web.py`: `test_multi_worker_completes_all_segments`,
    `test_rate_limit_pauses_all_workers`,
    `test_save_failure_joins_workers_and_reports_unsaved`, `test_unwritable_failure_log`,
    `test_unexpected_exception`, `test_failure_report_lists_several_ids` (a barrier), the
    thread-start failure test (events), and the fixed ones;
  - in `test_cli.py`: `test_parallel_output`, `test_parallel_counts_continue_from_translated`,
    `test_failed_left_over_message`, `TestCLISupervisorFailure::test_save_failure`,
    `test_workers_are_daemons`, `test_slow_result_still_applied`,
    `test_supervisor_failure_output` (a barrier), the second-start failure test, and the
    streak test `test_parallel`;
  - every `TestRound*` CLI and web test in `test_runtime.py` (barriers, events, a counting
    clock, `task.ticks`).

  No other instance was found. The CLI tests `test_save_failure` and the second-start test
  keep a call in flight with a sleep, but their assertions hold whether the call is still in
  flight or not (the save fails at the end in every order; the stop message is printed in
  every order). There the sleep only makes the scenario likely.
- **B. A fixed time bound stands in for a condition.** Behind the two `run_process` timeout
  tests (1 s had to cover the child's start and its writing) and round 4's pause test (the
  segment back in the queue at once). Checked: every time bound in this phase's tests and in
  the older tests touched by it. The remaining bounds are upper limits on condition waits
  (`_wait`, `Event.wait`, `join(timeout=…)`) that are met within milliseconds. Two
  exceptions are harmless: `test_rate_limit_pauses_all_workers` (`join(5)` always waits the
  5 s, then checks a state reached in milliseconds) and `test_slow_result_still_applied`
  (passes in every order).
- **C. A count is lowered before the thread ends.** Behind `test_stop` (product). Checked
  every place that treats a count or a flag as "the thread has ended":
  - web: `_workers_done` (now followed by the join), `_fail_task` (joins), the dynamic
    reduction (the thread returns right after), `threads_alive` (uses `is_alive`);
  - CLI parallel: the main loop and its shutdown use `t.is_alive()` and `join()`, not a count.

  No other instance was found.

No product code changed in this pass, so the mutation run was not repeated.

Targeted runs, before the emphasis moved to the mechanisms (confirmation only). The six
tests (the two above, the two timeout tests, the three of the first pass) were run in three
loops at once:
- 201 runs on an otherwise idle machine: all passed.
- 201 more during a full-suite series: 197 runs ended with a passed summary. In 4 runs the
  last output line was a `warnings.warn(` line, and their output was not kept (the loop kept
  only runs with "failed"), so their result is not known.
- 90 more runs with every output kept: all passed. The warning did not appear again.

Confirmation series, after the mechanisms were forced and fixed: the full suite 12 times,
four series of three at once (`-rf`, every run's tail kept): **12 of 12 passed, 2357 tests
each, no failure**.

## Phase 8 — Extraction quality and book-level context

Date: 2026-11-21. Branch: `phase-8/extraction-and-brief`, worktree `<repo>/.worktree/phase-8`,
shared virtualenv.

### What was built

| # | Item | Built | Where |
|---|---|---|---|
| 1 | Term extraction second pass | Every batch's proposals are collected (`collect_candidates`): grouped by lowercased term, the first spelling kept, each proposed translation and category counted once per batch that proposed it. Terms proposed with more than one translation go to one consolidating call (`CONSOLIDATE_PROMPT`): one JSON line per term with its translations and counts; the reply is a JSON array of `term`, `translation`, `category`. A term the reply decides (a known term, a non-empty text translation) takes that translation (it may be one the batches did not propose) and its category (the proposals' most frequent one when the reply's is not text). Every other term, and every term when the call or its reply is unusable (an error other than a stop, no JSON, not a list, missing or empty fields), keeps its most frequent proposal, the first proposed on a tie, so no candidate is lost; a partial reply is logged (`Term consolidation decided n of m terms`). Terms without a conflict need no call. A rate limit, expired login or unusable backend in the consolidation raises `ExtractionError` with every term (the decisions made so far, the rest by frequency); a stop during the batches makes no consolidating call. | `src/glossary/term_extractor.py` |
| 2 | Per-book brief | `project.json` `brief` (absent: none, never written, so existing files round-trip byte for byte; a non-text value is refused on load). `ProjectManager.set_brief` (stripped; empty removes it). Placed at the very top of the text and HTML prompts as `BRIEF_SECTION` (`本書簡介（請依此掌握文體、語氣與讀者；簡介本身不需翻譯）：` + the brief + a blank line); code segments get none (their prompt only translates comments). Absent: the prompt is exactly as on `main`. Every caller passes it: CLI sequential and parallel, the web worker (read under the project mutex for every segment, so an edit during a run applies from the next segment), re-translate, and the failure diagnostics. CLI `brief show`, `brief set TEXT` (empty TEXT removes it; under the project lock). Web `GET/PUT /api/projects/<name>/brief` (through the task manager's transaction) and a text area on the translation tab. | `src/core/project.py`, `src/core/translator.py`, `src/web/tasks.py`, `src/web/blueprints/{translation,review}.py`, `src/web/templates/project/translation.html`, `translate.py` |
| 3 | Person notes | Glossary entries have optional `gender` and `note` (text; stored only when not empty, so a glossary without them keeps its file format, also in `glossary export`). In the prompt they follow the entry: `Tess → 泰絲 [人名]（性別：female；備註：the baker）`; absent: the line as before. CLI `glossary add --gender/-g --note/-n`, shown by `glossary show`; `glossary extract` keeps an existing entry's gender and note when it replaces the entry. Web: add and edit (the fields show for the person category; any text accepted, `male`/`female` suggested), shown under the translation; non-text values refused (400). | `src/glossary/glossary_manager.py`, `src/web/blueprints/glossary.py`, `src/web/templates/project/glossary.html`, `translate.py` |
| 4 | Comparison script | `scripts/verify/phase8_extraction_compare.py` (below) | — |
| 5 | Brief sample script | `scripts/verify/phase8_brief_sample.py` (below) | — |

Design choices:

- Size of the consolidating call: only conflicting terms are sent (a term with one proposal has
  nothing to decide), and their lines are cut into parts of at most
  `MAX_CHARS_PER_CONSOLIDATION` 8000 characters, one call each. On the real run below 9 terms
  conflicted (one call); a whole book stays at one or a few calls.
- The consolidation does not drop or add terms: it only decides. Pruning the list was not
  asked for, and a model that drops terms would lose candidates.
- Brief placement: at the top of the prompt, as the plan says ("at the top"); Phase 7 kept the
  rest of the order unchanged. Not in the code prompt.
- `gender` and `note` are not restricted to the person category in the data (the web form shows
  them for persons only); a note on another entry is shown in the prompt the same way.
- The prompt shows the gender value as given (no mapping to pronouns): the model reads
  `性別：female` well enough, and a mapping would be one more rule.

### Comparison script (`scripts/verify/phase8_extraction_compare.py`)

Copies one project (as the Phase 7 throughput script: `project.json`, `source.epub`,
`glossary.json` into a new temporary directory, refused inside a git work tree or the projects
directory, removed in `finally`, the projects directory snapshotted and the run FAILs if it
changed), migrates a schema 1 copy, and runs the term extraction of `--code` on the copy's
text segments. `--code <checkout of main>` gives the extraction before this phase. `--chunks N`
sends only the first N batches (the batching is the same before and after). It records every
batch's proposals and counts calls, writes `terms-before.json` / `terms-after.json` (`terms`,
and `conflicts`: every term proposed with more than one translation, with the counts) to
`--out` (refused inside the projects directory or a git work tree), and prints counts only.

The before checkout was made with `git archive main | tar -x -C <scratch>/main-code` (no
worktree metadata). Claude CLI backend, model haiku, project index 3 (2956 text segments,
about 760 000 characters, 170 batches in all (counted in review round 1); 20 sent):

```
$ python3 scripts/verify/phase8_extraction_compare.py --projects-dir <repo>/projects \
    --project-index 3 --anonymize --model haiku --chunks 20 --out <scratch>/phase8-out \
    --code <scratch>/main-code
project-3 before: batches sent 20  calls 20  proposals 331  terms 225  terms with conflicting translations 10
wrote terms-before.json
$ python3 scripts/verify/phase8_extraction_compare.py --projects-dir <repo>/projects \
    --project-index 3 --anonymize --model haiku --chunks 20 --out <scratch>/phase8-out
project-3 after: batches sent 20  calls 21  proposals 355  terms 233  terms with conflicting translations 9
wrote terms-after.json
```

Both exited 0. The two runs are separate model runs, so their proposals differ (331 / 355).
Counted from the two files (numbers only): every conflicting term is one entry in both lists.
Before, the kept translation was the first proposal (9 of 10 also the most frequent one);
after, the consolidating call chose the most frequent proposal for 5 of 9, another proposal
for 3, and a translation no batch proposed for 1. Which list is better is for the owner to
judge from the two files (kept outside the repository).

### Brief sample script (`scripts/verify/phase8_brief_sample.py`)

Same copy rules. The sample: of the text segments (not code, not passthrough) with at least
`MIN_WORDS` 50 words, in book order, positions `i * count // 10` for i = 0..9 (fixed, no random
seed). Each is translated with `translate_segment` (the copy's glossary and context) once without
and once with the brief of `--brief-file`; nothing is stored. Writes `brief-sample.html` (the
brief, then per segment the source and both translations side by side) to `--out`. The brief
used was invented and generic (genre: general non-fiction for a broad readership; tone: clear,
friendly, conversational; audience: curious adult readers without specialist background).

```
$ python3 scripts/verify/phase8_brief_sample.py --projects-dir <repo>/projects --project-index 3 \
    --anonymize --model haiku --brief-file <scratch>/brief.txt --out <scratch>/phase8-out
project-3: sampled 10  calls 21  without brief: translated 10 failed 0  with brief: translated 10 failed 0
wrote brief-sample.html
```

Exit 0; 21 calls for 20 translations (one retry). The quality comparison is the owner's.

### Checks

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase3_export_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  exports 24  translated segments 4162  as translation 3956  kept original 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 53)  toc labels changed 405/1885 (527 into translated chapters)  exports with new epubcheck errors 0  failed projects 0
$ python3 scripts/verify/phase4_validate_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  round trip 9491/9491  translated 4162  failing the structural check 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 54; only tag rules 88)  warnings (length_ratio 36, untranslated 8)  length ratio over 2925 segments: p1 0.312 p5 0.333 p50 0.404 p95 0.547 p99 0.976; outside 0.25-0.8: 38 (1.3%)  sources that cannot be cut 0  with translatable attributes 22  translated code starting with a shebang 0  failed projects 0
$ python3 scripts/verify/phase5_glossary_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  glossary entries 15, translated 4162, passing the structural check 3956, with term_not_applied 12, terms 4, by category (person: 9 warnings, 2 terms, term: 6 warnings, 2 terms)  failed projects 0
$ python3 scripts/verify/phase6_segmentation_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  segments 9520 -> 7870, calls 9520 -> 6432 (ratio 0.676), passthrough 1438, headings merged 1655 of 1770  failed projects 0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

All four exited 0 with the TOTAL lines of Phase 7 (no glossary entry of the real projects has
`gender` or `note`, and no project has a brief, so the glossary check is unchanged). The
fingerprint of the projects directory was the same before and after all work of this phase,
including the three real runs; no `phase8-*` temporary directory was left.

### Tests

2475 passed (main: 2357), and three suites at once: 2475 passed each, no failure. New:
`tests/test_extraction_brief.py` (75): conflicting proposals consolidated into one entry and the
consolidation request's content; no call without a conflict; nine kinds of unusable reply and
a transient error keep the most frequent proposal; a partial reply; a stop in the consolidation
or in a batch keeps every term; bounded consolidation calls; the brief at the top of the text
and HTML prompts, not in code, kept on a retry and in the diagnostics; an absent brief gives
the prompt and the `project.json` text of `main`; `set_brief`, a non-text brief refused; person
notes in the glossary lines and in the translation prompt, the glossary file unchanged without
them, update, import and export; CLI `brief show/set`, refused while another process holds the
lock, `start` (one and two workers) with and without a brief, `glossary add -g -n`, `show`,
`extract` keeping a person's notes and consolidating; web brief GET/PUT, refused bodies, an edit
during a run used from the next segment (the first prompt is today's), re-translate with the
brief, glossary POST/PUT with notes, non-text refused, both pages' fields. `TestExtractionCompareScript`,
`TestBriefSampleScript`, `TestPhase8ScriptSurvivors` (`test_verify_scripts.py`, 43). Waits are on
events and conditions (`_wait` polls a predicate); no fixed sleep. No existing test was removed
or changed.

### Mutation testing

`scripts/verify/mutation.sh main`, mutmut 3.8.1, parallel (about 8 minutes per run). Added to
the mutated files: both Phase 8 scripts.

| Run | Mutants | Killed | Survived | Timeout |
|---|---|---|---|---|
| 1 | 1180 | 1046 | 134 | 0 |
| 2 | 1177 | 1147 | 30 | 0 |
| 3 (final) | 1164 | 1139 | 25 | 0 |

Run 1 → 2: tests for exact error texts, the diagnostics prompt with a glossary,
`translate_reply` without a brief, the default arguments of `add_term` and `import_json`, a
second extraction batch filled exactly to the limit, the consolidation part boundary, the sort
by lowercase, ties, the cancel event of an extraction call, a reply item read after an
unusable one, today's prompt in the web worker and both CLI paths without a brief; for the
scripts: snapshots, the cancel passed through the counting backend, the temporary directory's
prefix, a `mkdtemp` failure, a cleanup failure alone, the project's backend and model, no log
line on stderr and the root handlers restored, a schema 1 copy, an existing `--out`, context
from translations (segmentation 2), project index 1, required options, the exact page and the
exact term file. Three equivalent forms were removed from the code (an unused default of
`_request_text`, the dead initial `size` of `_consolidation_batches`, `most_common(1)` → `max`).
Run 2 → 3: `mkdtemp(dir=parent)` → `mkdtemp()` (the default directory is the checked one) and
`getattr(pm.data, 'backend', None)` → `pm.data.backend` (every project of this code and of
`main` has the field), removing their equivalent mutants.

Remaining, each equivalent:

| mutant(s) | why equivalent |
|---|---|
| `translate_segment` 4 (`if context` → `if context or True` in the diagnostics after an unexpected error) | with `context` None, `_request_text` unpacks `*None`, raises `TypeError` inside its own `try` and returns `''`, the same as the `else` branch (the logic is from Phase 4; the line changed only by the new argument) |
| `_candidate_line` 2, extraction `run` 82 (`ensure_ascii=None`) | `json` tests the flag for truth: `None` acts as `False` |
| both scripts `in_work_tree` 10 (`'.git'` → `'.GIT'`), brief `run` 35 (`'glossary.json'` → `'GLOSSARY.JSON'`) | the file system here (APFS) is case-insensitive: the same path |
| both scripts `run` 21, 22 (`redirect_stdout(None)` / `redirect_stderr(None)` around load and migration) | with `sys.stdout` / `sys.stderr` set to `None`, `print` writes nothing, as into the discarded buffer; nothing in that block writes to the streams other than through `print` |
| brief `run` 88, 91, 99, `sample` 2, 4, 6; extraction `run` 68, 71, 79 (`encoding='utf-8'` → `None`, omitted, `'UTF-8'`) | `'UTF-8'` is the same codec; `None` / omitted take the locale's encoding, UTF-8 on this machine (Python 3.12, macOS) |
| brief `sample` 63, extraction `compare` 47 (`sys.path.insert(0, None)`) | the tests run in a process that has imported `src` already, so the import does not search the path; a fresh run needs the entry, and the real runs above made it (killed there, not in the suite) |
| brief `sample` 66, extraction `compare` 50 (`insert(1, …)`) | position 1 is directly after the script's own directory, which holds no `src` package: the same module is found |
| extraction `main` 61, 87 (`default=None` dropped for `--chunks` / `--name`) | argparse's default for an optional argument is `None` |

### Known limitations

- A project with `brief`, or a glossary with `gender` / `note`, cannot be loaded by code from
  before this phase (`ProjectData(**raw)` / `GlossaryEntry(**item)` reject the key); only
  projects and entries where one was set get the key.
- The consolidation sees only the proposals' translations and counts, not the text they came
  from; a term proposed once with one translation is not reviewed.
- `glossary extract` in the CLI still replaces existing entries with the extracted translation
  (as on `main`); it now keeps their gender and note. The web extraction adds only new terms
  (as on `main`).
- The brief is not in the code prompt.

### Not verified

- Translation quality with and without the brief, and which term list is better: the owner
  judges `brief-sample.html`, `terms-before.json` and `terms-after.json` (outside the repository).
- The extraction comparison covered 20 of the 170 batches of one book (`--chunks 20`), so the
  consolidation of a whole book (more conflicts, possibly several calls) was not run for real.
- The web pages were checked through the Flask test client (fields, requests), not in a browser.
- The Copilot backend was not used for any real run.

## Phase 8 — review round 1

Date: 2026-11-21. Three reviews (GPT, Opus, Gemini through Copilot); the orchestrator's triage
accepted items A to N below and rejected the rest. Each accepted claim was checked against the
code first; all of them were real.

### Accepted

| Item | Fix | Test |
|---|---|---|
| A. Counting once per batch | `collect_candidates` counts each (term, translation) and (term, category) at most once per batch reply; the comparison script's `conflicts()` uses the same rule, strips texts and skips a blank term or translation | `TestRound1Consolidation::test_a_reply_counts_once_per_batch`, `test_candidates_stripped`; `TestPhase8Round1Scripts::test_conflicts_count_once_per_batch_and_skip_blanks`, `TestPhase8Round1Survivors::test_conflicts_read_on_after_an_unusable_proposal` |
| B. Consolidation reply | `_parse_response`: JSON that decodes to something other than a list is no answer (no search for an array inside an object; this also applies to the extraction replies, logged as before). Any exception while parsing a consolidation reply makes it unusable (logged with its type). Two rows that decide one term differently leave that term to the frequency rule; the same decision twice is kept. Term, translation and category are stripped; an empty category takes the batches' most common one | `test_decoded_reply_that_is_not_a_list_is_unusable`, `test_extraction_reply_that_is_not_a_list`, `test_reply_that_cannot_be_parsed_is_unusable` (a reply nested 100000 deep: `RecursionError`), `test_contradicting_rows_leave_the_term_undecided`, `test_stripped_reply_and_empty_category`, `test_stripped_category` |
| C. `--code` verified | the four modules are imported inside a `try` (an `ImportError`: `ERROR --code has no term extractor (…)`, exit 2) and each must lie inside `--code` (otherwise `ERROR the code that was loaded is not the one of --code`, exit 2) | `test_code_of_two_checkouts_in_fresh_interpreters` (two stub checkouts with different answers, and an empty directory, each in a fresh interpreter); `test_code_loaded_from_the_checkout_in_process` and `test_checkout_without_the_extractor_in_process` (the package unloaded, this checkout first on `sys.path`: the stub must still win); the earlier test with an empty `--code` now expects the refusal |
| D. Survivors | `sys.path`: killed through C; the brief sample's path entry and imports moved to module level, as in the earlier check scripts. Filename case and encodings: tests in the Phase 5 style (`test_looks_for_dot_git`, `test_brief_glossary_file_name`, `test_files_read_and_written_as_utf8`). The `redirect_stdout` / `redirect_stderr` wrappers were not needed (load, migration and glossary loading print nothing) and are removed | mutation runs below |
| E. Glossary import and API | `import_json` checks the whole file with the rules of `load()` (one shared `_check_entries`) before it writes anything; CLI `glossary import` reports `無法匯入：…` and exits 1. The glossary endpoints read the body with `get_json(silent=True)` and refuse a non-object with 400. `gender` and `note` are stripped in `add_term` and `update_term` | `TestRound1Glossary::test_import_checked_before_anything_is_written` (4 cases), `test_cli_import_refused`, `test_person_fields_stripped`, `test_web_non_object_body` (3 cases) |
| F. Brief length | `MAX_BRIEF_CHARS` 2000 in `ProjectManager.set_brief` (after stripping); longer raises `ValueError('書籍簡介最多 2000 字（目前 N 字）')` and changes nothing; web 400 with that message, CLI the message and exit 1, the file unchanged | `TestRound1Brief::test_limit_in_the_setter`, `test_limit_in_the_cli_and_web` |
| G. Brief PUT during a run | the PUT sets and saves under the project mutex and puts the previous brief back when the save raises (the workers read it under the same mutex, so they never see an unsaved one); GET reads it under the mutex | `test_failed_save_restores_the_previous_brief`, `test_get_reads_under_the_mutex` |
| H. Brief text area | disabled until the stored brief is loaded; a save sends a snapshot and replaces the text only if it is still that snapshot (a newer draft stays and shows as not saved) | `test_text_area_waits_for_the_load_and_keeps_a_newer_draft` (rendered page; not run in a browser) |
| I. CLI `glossary add` | `-g` / `-n` default to "not given": an existing entry keeps its value, an explicit `''` clears it; a new entry gets `''` | `test_cli_add_keeps_omitted_person_fields` |
| J. `brief-sample.html` | every cell is plain text: tags removed, one line per block (`</p>`, `</h1>`…, `<br>`, `</li>` …), entities decoded, then escaped; shown with `white-space: pre-wrap`. Chosen over escaped markup because a translation is judged by its words, and the tags would make the Chinese hard to read; no book markup is rendered, so nothing runs or loads | `test_readable`, `test_brief_page_exact`, `test_page` |
| K. Migration result | both scripts reload the copy after `migrate_project` and stop with `ERROR project-N: the copy could not be migrated` (exit 2, no model call) if it is not schema 2 | `test_copy_left_at_schema_1_refused` (both scripts) |
| L. Brief sample with failures | the file and the counts are still written; exit 1 with `FAIL project-N: not every sampled segment has both translations` unless all ten segments have both | `test_samples_with_and_without` (one failure with the brief), `test_brief_sample_fails_when_a_translation_without_the_brief_failed` |
| M. Phase 1 check scripts | seven entries re-anchored: reverts 9 (web, CLI), R2-5 and R3-7 (the diagnostics line passes the brief), 19 (the extraction stop is in `collect_candidates`), survivor kills core263 / core264 (the batching moved into `_batches`) | the runs below |
| N. Whole-book comparison | project 1, the real project with the fewest text segments (220; 105 batches), run in full before and after | below |

Phase 1 scripts (`phase1_reverts.py`, `phase1_survivor_kills.py`, each on its own commit's code):

| Code | reverts not detected | not killed |
|---|---|---|
| `main` (a `git clone --shared` of `main` in the scratch directory) | 23 | 14 |
| this branch before the re-anchoring | 28 | 16 |
| this branch after | 23 | 14 |

The 23 and 14 left are the same entries, line for line, on `main` and on the branch (compared
after removing durations): anchors made stale by Phases 2 to 7 (for example `BAD 3
mark_reviewed outside the mutex`, `BAD core58`). They are not this phase's and were left as
they are. The seven re-anchored entries were run alone too: `reverts not detected: 0`,
`not killed 0`.

Whole-book comparison (Claude CLI, haiku; the before code is that `main` clone):

```
$ python3 scripts/verify/phase8_extraction_compare.py --projects-dir <repo>/projects \
    --project-index 1 --anonymize --model haiku --out <scratch>/phase8-out/whole --code <scratch>/main-clone
project-1 before: batches sent 105  calls 105  proposals 1861  terms 906  terms with conflicting translations 111
wrote terms-before.json
$ python3 scripts/verify/phase8_extraction_compare.py --projects-dir <repo>/projects \
    --project-index 1 --anonymize --model haiku --out <scratch>/phase8-out/whole
project-1 after: batches sent 105  calls 107  proposals 1873  terms 923  terms with conflicting translations 98
wrote terms-after.json
```

Both exited 0. The files were renamed to `terms-before-whole-book.json` and
`terms-after-whole-book.json` in `<scratch>/phase8-out` (the 20-batch files of the first run
keep their names). After: two consolidating calls (98 conflicting terms, more than 8000
characters of candidate lines). Counted from the files (numbers only): every conflicting term
is one entry in both lists; before, the first proposal was kept (also the most frequent one
for 94 of 111); after, the consolidation chose the most frequent proposal for 67 of 98, another
proposal for 28 and a translation no batch proposed for 3. Which list is better is the owner's
judgement.

### Rejected (considered, not done)

- GPT 1 (an existing report file being a link to a project file), GPT 9 (a differently
  capitalized projects directory, `TMPDIR` inside the projects directory), GPT 10 (cleanup on
  SIGHUP / SIGTERM): not realistic for a script the owner runs by hand; the same standard as
  the earlier check scripts.
- GPT 5 (one term whose candidate line exceeds 8000 characters): not realistic.
- GPT 6 (case folding beyond `lower()`): English source books; consistent with the glossary's
  own lookup.
- GPT 8 (logging handlers already installed under `--anonymize`): the scripts run standalone;
  no such handler exists.
- GPT 14 (e-mail addresses in the commit metadata): settled by the owner; the existing git
  identity and the single trailer stay.
- Opus 7 (README / CLAUDE.md): not in this branch; those files have the owner's uncommitted
  edits.

### Mutation testing

`scripts/verify/mutation.sh main`, parallel, about 8 minutes per run.

| Run | Mutants | Killed | Survived | Timeout |
|---|---|---|---|---|
| round 1, first | 1246 | 1231 | 15 | 0 |
| round 1, final | 1246 | 1241 | 5 | 0 |

First → final: the warning text of a non-list reply, a failure only without the brief, the
in-process checkout without the extractor (message, type, exit code), a proposal read after an
unusable one in `conflicts()`, the exact name of the term file (`'BEFORE'` passed the earlier
test only because this file system ignores case).

Remaining, each equivalent on any machine:

| mutant(s) | why equivalent |
|---|---|
| `translate_segment` 4 (`if context` → `if context or True` in the diagnostics) | with `context` None, `_request_text` unpacks `*None`, raises `TypeError` inside its own `try` and returns `''`, the same as the `else` branch |
| `_candidate_line` 2, extraction `run` 84 (`ensure_ascii=None`) | `json` tests the flag for truth: `None` acts as `False` |
| extraction `main` 61, 87 (`default=None` dropped for `--chunks` / `--name`) | argparse's default for an optional argument is `None` |

The survivors of the first Phase 8 entry that held "on this machine only" (file name case,
locale encoding, `sys.path`) are all killed now; no remaining justification depends on the
machine.

### Checks and tests

- `python3 -m pytest tests/ -q -p no:cacheprovider`: 2514 passed; three suites at once:
  2514 passed each, no failure. No existing test removed; changed: the earlier test with an
  empty `--code` (now refused), the brief sample's page and result expectations (text cells;
  exit 1 when a translation failed).
- The four older check scripts (phase 3 to 6, `--anonymize`): the same TOTAL lines as in the
  Phase 8 entry; the projects fingerprint `a7197555c962562a530d6de80fa045af` before and after
  every run of this round.

### Not verified

- The text area's behaviour (H) in a browser: only the rendered page was checked.
- The quality of the two whole-book term lists (the owner's).

## Phase 8 — review round 2

Date: 2026-11-22. GPT requested changes, Opus and Gemini approved. Each accepted claim was
checked against the code first; all were real (F: the double `init()` is real and older than
this phase, see below).

### Accepted

| Item | Fix | Test |
|---|---|---|
| A. Consolidation reply is one list | `_top_level_list`: after removing a Markdown fence the whole reply must decode to a JSON list; no array is searched for inside other text, so a cut-off `{"rejected":[…]` is unusable (frequency rule). The extraction replies keep `_parse_response` and its recovery | `TestRound2::test_cut_off_object_is_not_a_consolidation_answer`, `test_consolidation_answer_is_the_whole_reply` (both forms, and the extraction recovery unchanged), `test_top_level_list`, `TestRound2Survivors::test_fenced_list_over_several_lines` |
| B. Web POST of an existing term | the CLI rule everywhere: a person field that is not sent keeps the stored value, one sent as `''` clears it. The add form sends `gender` / `note` only when the category is person (when the fields are shown), so it never wipes fields it hides; the edit row already sends the values it loaded | `test_web_post_keeps_person_fields_not_sent`, `test_add_form_sends_person_fields_only_when_shown` |
| C. Extraction rewrote the glossary per term | real: both the web task and the CLI read the glossary after the model calls, but then saved the whole file once per term, so an edit saved during that loop could be reverted. Now `add_term(save=False)` for every extracted term and one `save()` after the loop (no new locking) | `test_cli_extraction_merges_after_the_calls_and_saves_once`, `test_web_extraction_merges_after_the_calls_and_saves_once` (an edit made during the model call survives; one save by the extraction) |
| D. Import with a missing key | `_entries` (shared by `load()` and `import_json`) builds every `GlossaryEntry` before anything is applied; a missing or unknown field raises `ValueError('a glossary entry has a missing or unknown field')` (before: `load()` raised `TypeError`, the import skipped the entry). The import saves once | `test_import_with_a_missing_or_unknown_field` (both cases, file unchanged, and `load()`), `test_import_saves_once`, `test_import_keeps_the_frequency` |
| E. `--code` accepted a nested checkout | `loaded_from`: each module must be exactly `<code>/<package path>.py` (or `…/__init__.py` for a package) | `TestPhase8Round2Scripts::test_modules_of_a_checkout_nested_in_code_refused` (a stub under `<main>/.worktree/branch` already imported, `--code <main>`: refused), `test_loaded_from` |
| F. Double `init()` | real, and older than this phase: Alpine 3 calls a component's `init()` by itself and `x-init="init()"` called it again, so on the translation page `fetchModel`, `fetchStatus` (which opens the SSE stream when a run is active, so a second stream replaced the first) and, from this phase, `fetchBrief` ran twice. The `x-init` attribute is removed from that page. The glossary and export pages have the same pattern (`loadTerms` / extraction status, the export page's `init()`); left as on `main`, outside this item | `test_translation_page_initialized_once` (rendered page; not run in a browser) |
| G. No proposal at all | the comparison exits 1 with `FAIL project-N: no batch proposed a term` when no batch proposed anything (no per-batch failure tracking) | `test_no_proposal_at_all_fails` (every call a transient error); the tests whose fake answered `[]` now answer one term |
| H. Brief file over the limit | the sample refuses a brief longer than `MAX_BRIEF_CHARS` (the setter's rule, after stripping) with `ERROR the brief is longer than 2000 characters`, exit 2, before the copy and any call. Project loading unchanged | `test_brief_over_the_limit_refused_before_any_call` (2001 refused, 2000 accepted) |
| I. Batch delete body | a non-object body, a `terms` that is not a list, or a non-text term: 400, nothing deleted | `test_batch_delete_refused` (5 cases) |
| J. Report text | `readable` uses the project's parser: one line per `validation.top_level_units` unit, its text from `validation.plain_text` (parsed, tags, script and style removed, entities decoded), whitespace collapsed, then escaped; markup that cannot be cut or parsed is shown as it is (escaped). A quoted `>` in an attribute no longer leaks | `test_readable` (attribute with `>`, script, unparsable markup) |
| K. Empty category | an empty category is no vote; `_most_common` of no votes is `''` | `test_an_empty_category_is_no_vote` |
| L. Person fields on one line | `gender` / `note` have every whitespace run (also line breaks) collapsed to one space when stored (`add_term`, `update_term`) | `test_person_fields_one_line` |

### Rejected (considered, not done)

- GPT 5 (`--out` with `..` through a not-yet-existing projects subdirectory), GPT 6 (the before
  checkout unpacked inside the projects directory), GPT 7 (argparse echoing a mistyped
  argument): not realistic for hand-run scripts.
- GPT 11 (stored keys with surrounding spaces): entries are stripped on the way in.
- GPT 14 (the separator counted twice at the exact 8000 boundary): at most one extra call in a
  boundary case.

### Mutation testing

`scripts/verify/mutation.sh main`, parallel.

| Run | Mutants | Killed | Survived | Timeout |
|---|---|---|---|---|
| round 2, first | 1319 | 1309 | 10 | 0 |
| round 2, final | 1319 | 1313 | 6 | 0 |

First → final: a fenced list over several lines (kills the joiner and `split(None)` mutants),
an imported frequency; `readable` no longer carries the unused third value of a unit (its
mutant was equivalent). Remaining, each equivalent on any machine: the five of round 1
(`translate_segment` 4, `_candidate_line` 2, extraction `run` 84, `main` 61 and 87, reasons as
recorded there) and `import_json` 9 (`save=False` → `save=None`: `add_term` tests it for
truth, so `None` saves as little as `False`).

### Checks and tests

- `python3 -m pytest tests/ -q -p no:cacheprovider`: three suites at once, 2539 passed each, no
  failure. No existing test removed; changed: the tests whose fake extraction answered `[]`
  (now one term, item G) and the `readable` expectations (item J: one line per top-level
  block, so a `<br>` inside a paragraph no longer breaks the line).
- The four older check scripts (phase 3 to 6, `--anonymize`): the same TOTAL lines; projects
  fingerprint `a7197555c962562a530d6de80fa045af`. No real model call in this round.

### Not verified

- In a browser: the add form (B), the single `init()` (F) and the brief text area of round 1 (H); only the rendered pages were checked.

## Phase 8 — review round 3

Date: 2026-11-22. Opus and Gemini approved, GPT requested changes; a final small pass on the
accepted items, each checked against the code first (all real).

| Item | Fix | Test |
|---|---|---|
| 1. Add form wiped stored person fields left empty | the add form sends `gender` / `note` only for a person and only when filled in; clearing them is done in the edit row | `TestRound3::test_add_form_sends_only_filled_person_fields` (and the round 2 form test updated) |
| 2. One line also on load and import | the shared entry builder `_entries` (used by `load()` and `import_json`) applies `_one_line` to `gender` and `note` | `test_loaded_and_imported_person_fields_one_line` |
| 3. Old brief error left visible | the brief has its own `briefError`, shown next to its save button and cleared by a successful save; the task error (`error`) is no longer used for it | `test_brief_error_is_its_own_and_cleared_on_success` (rendered page) |
| 4. Report text at breaks and cells | a space after `<br/>` and after `</td>` / `</th>` before the text is taken; still one line per top-level block | `TestPhase8Round3Scripts::test_readable_spaces_at_breaks_and_cells`; the round 1 `readable` expectation is now `One two three` |
| 5. "No batch proposed a term" | counts only proposals whose term and translation are non-blank text (`usable`) | `test_no_usable_proposal_fails`, `test_usable` |

Rejected (considered, not done):

- GPT 1 (an atomic load–modify–save across glossary writers, for example a batch delete at the
  instant extraction saves): a single owner, a window of milliseconds now that extraction saves
  once, and this race between glossary writers predates the phase.
- GPT 3 (text after a closing fence on the same line): not a realistic reply.

Mutation (`scripts/verify/mutation.sh main`, parallel): 1336 mutants, 1330 killed, 6 survived,
0 timeout. The six are the equivalent ones recorded in round 2 (`translate_segment` 4,
`_candidate_line` 2, extraction `run` 84, `main` 61 and 87, `import_json` 9).

Checks: three suites at once, 2545 passed each, no failure (no test removed; the two changed
expectations are named above); the four older check scripts give the same TOTAL lines; the
projects fingerprint is `a7197555c962562a530d6de80fa045af`. No real model call.

Not verified: items 1 and 3 in a browser (rendered pages only).

## Phase 9 — Copilot CLI completion

Date: 2026-11-22. Branch: `phase-9/copilot-cli-completion`, worktree `<repo>/.worktree/phase-9`,
shared virtualenv. Copilot CLI 1.0.114, model `claude-haiku-4.5`, invented text only.

### What was built

| # | Item | Built | Where |
|---|---|---|---|
| 1 | Plugin | `src/llm/copilot_plugin/plugin.json` and `extensions/system-prompt/extension.mjs`: `joinSession({systemMessage: {mode: "replace", content: process.env.EPUB_TRANSLATOR_SYSTEM_PROMPT}})`. Kept next to the backend module; `PLUGIN_DIR` is its absolute path, computed from the module's file, so the working directory does not matter, and it is passed as one argument (no shell), so spaces do not matter. | `src/llm/copilot_plugin/` |
| 2 | One system prompt | `SYSTEM_PROMPT` moved to `src/llm/cli.py`; `ClaudeCLI` passes it with `--system-prompt`, `CopilotCLI.environment()` adds it as `EPUB_TRANSLATOR_SYSTEM_PROMPT` (the environment variable is how the prompt reaches the extension; the file has no copy of the text). | `src/llm/cli.py`, `claude_cli.py`, `copilot_cli.py` |
| 3 | Command | `CopilotCLI` adds `--experimental --plugin-dir <PLUGIN_DIR>` (the owner's settings have `experimental: true`, but the backend does not depend on it). | `src/llm/copilot_cli.py` |
| 4 | Check | Every run that `classify_error` would accept is also checked by `_replaced`: every `session.usage_checkpoint` must have `promptCacheBreakState[].models.<model>.system_segments`, and every model call's segment list must be exactly `[agent_replacement]`. Otherwise `BackendUnavailableError(NOT_REPLACED_MESSAGE)`: the web task pauses waiting for the user, the CLI run stops (exit 1), nothing is stored. A failed run keeps its own class (rate limit, auth, …). | `src/llm/copilot_cli.py` |
| 5 | Check script | `scripts/verify/phase9_copilot_overhead.py` | — |

### The detection signal

Candidates, compared on the research outputs and reproduced here:

- `session.extensions_loaded` lists the plugin's extension as `running` whenever it starts, also
  when its `joinSession` call then fails (extension without the environment variable: status
  `running`, then the run fails with `data did not match any variant of untagged enum
  ProtocolSystemMessageConfig`, exit 1). With `--no-experimental` the event is missing, with a
  wrong plugin folder it is an empty list. It says that an extension started, not that the
  prompt was replaced. Not used.
- Input tokens: replaced 94, built-in 2127 for one sentence; but a long prompt with the
  replacement (4079, below) is above the built-in short call (3704). A fixed threshold false-alarms
  on long segments, and a threshold relative to the prompt needs a token estimate of mixed
  English and Chinese text. Not used.
- `system_segments` in the usage checkpoint: the system prompt parts actually sent with each
  model call. Built-in: 18 named segments (`customized_identity_preamble`, …,
  `final_instructions`, 1972–1979 tokens). Replaced: one segment `agent_replacement` of 14
  tokens, whose `hash` is the first 12 hex digits of the SHA-256 of our prompt (`66b1b00b4dea`,
  checked; the research's two different prompts gave their own SHA-256 prefixes too). It does not
  depend on the prompt's length (14 tokens for the 1927- and the 8966-character prompt). Used,
  by name only (the hash is not checked: one more undocumented detail that could change).

Threshold: none; the check is structural (exactly one `agent_replacement` segment per model
call). A missing report counts as not replaced, so a CLI update that drops the field stops the
run with the message instead of translating on unverified.

### Real calls

Before/after per call (`scripts/verify/phase9_copilot_overhead.py`, 3 calls per configuration,
round-robin; the short prompt is the engine's text prompt around an invented paragraph, the long
one has 8 invented paragraphs and a 60-entry invented glossary):

```
$ python3 scripts/verify/phase9_copilot_overhead.py --raw
model claude-haiku-4.5  repetitions 3  prompts 1927 and 8966 characters (invented paragraphs)
  1 built-in           pass not replaced wall 12.19 in 3704 out 810 system segments 18 (1977 tokens)
  1 replaced           pass replaced wall 11.79 in 1670 out 673 system segments 1 (14 tokens)
  1 replaced-long      pass replaced wall 23.60 in 4079 out 2447 system segments 1 (14 tokens)
  1 no-experimental    pass not replaced wall 11.09 in 3706 out 680 system segments 18 (1979 tokens)
  1 wrong-plugin-dir   pass not replaced wall 11.40 in 3704 out 629 system segments 18 (1977 tokens)
  2 built-in           pass not replaced wall 12.85 in 3706 out 859 system segments 18 (1979 tokens)
  2 replaced           pass replaced wall 11.84 in 1670 out 697 system segments 1 (14 tokens)
  2 replaced-long      pass replaced wall 23.94 in 4079 out 2567 system segments 1 (14 tokens)
  2 no-experimental    pass not replaced wall 9.41 in 3702 out 538 system segments 18 (1975 tokens)
  2 wrong-plugin-dir   pass not replaced wall 14.94 in 3705 out 1015 system segments 18 (1978 tokens)
  3 built-in           pass not replaced wall 12.00 in 3703 out 720 system segments 18 (1976 tokens)
  3 replaced           pass replaced wall 9.69 in 1670 out 548 system segments 1 (14 tokens)
  3 replaced-long      pass replaced wall 27.20 in 4079 out 2928 system segments 1 (14 tokens)
  3 no-experimental    pass not replaced wall 13.24 in 3699 out 875 system segments 18 (1972 tokens)
  3 wrong-plugin-dir   pass not replaced wall 12.11 in 3706 out 747 system segments 18 (1979 tokens)
built-in           n=3 passed=3  wall  12.19 s  in    3704  out   810  system segments   18 ( 1977 tokens)
replaced           n=3 passed=3  wall  11.79 s  in    1670  out   673  system segments    1 (   14 tokens)
replaced-long      n=3 passed=3  wall  23.94 s  in    4079  out  2567  system segments    1 (   14 tokens)
no-experimental    n=3 passed=3  wall  11.09 s  in    3702  out   680  system segments   18 ( 1975 tokens)
wrong-plugin-dir   n=3 passed=3  wall  12.11 s  in    3705  out   747  system segments   18 ( 1978 tokens)
$ echo $?
0
```

- Input per call: 3704 → 1670 tokens on the engine's prompt (−2034, the built-in system prompt
  and its framing); on a bare sentence 2127 → 94 (two single calls before the script, same flags).
- Wall time per call: median 12.19 → 11.79 s (single calls 7.77 → 7.44 s): within the spread of
  the raw calls (9.4–14.9 s); the extra Node process costs no measurable time here.
- Broken setups: `--no-experimental` (exit 0, no `extensions_loaded` event, 18 segments) and a
  wrong plugin folder (exit 0, stderr `Warning: [plugin-dir] no plugin.json or SKILL.md found in
  …`, 18 segments) were each detected on all 3 calls (`not replaced` = the backend's
  `BackendUnavailableError` with `NOT_REPLACED_MESSAGE`).
- Output tokens include the model's reasoning; they are what the CLI reports.

Short invented book through the real CLI path (6 text segments, one heading, invented sentences,
project in a scratch directory; the repository copied with `git archive` into a folder whose
path contains spaces, run from `/tmp`, so the plugin path has spaces and is not under the
working directory):

```
$ cd /tmp && python3 "<scratch>/repo with spaces/translate.py" start --project <scratch>/book \
    --backend copilot-cli --model claude-haiku-4.5 --workers 2
使用模型：claude-haiku-4.5（copilot-cli）
繼續翻譯：0/6 已完成 (0.0%)
剩餘 6 個段落待翻譯
並行 Workers：2

  [W1] [1/6] ✓ c1_seg0002（23 字）
  [W0] [2/6] ✓ c1_seg0001（5 字）
  [W1] [3/6] ✓ c1_seg0003（26 字）
  [W0] [4/6] ✓ c1_seg0004（26 字）
  [W1] [5/6] ✓ c1_seg0005（19 字）
  [W0] [6/6] ✓ c1_seg0006（20 字）

全部翻譯完成！
$ echo $?
0
```

6 calls, all passed the check (a call that failed it would have stopped the run); stored input
tokens per segment 1433, 1473, 1499, 1490, 1483, 1434; replies plain (one translated element
each, no alternatives).

Processes. While the book ran, `ps` showed per call `copilot -p …` (its own process group, as
`run_process` starts it) and a child `copilot extension_bootstrap.mjs` — in a process group of
its own (pgid = its pid), so the group kill does not reach it directly. Cancel in flight, real
call through `CopilotCLI.complete` (scratch script: cancel set once the extension process exists,
and once 4 s later during the model call): `CancelledError`, the call returned within 0.05 s,
and the `copilot` process, the extension process and every other child of `copilot` were gone
within 1.1 s and 0.9 s (polled with `ps`). The extension ends by itself when its parent dies;
no process was left.

Calls made in this phase: 28 (5 single probes, 15 by the script, 6 for the book, 2 cancelled).

### Checks

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase3_export_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  exports 24  translated segments 4162  as translation 3956  kept original 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 53)  toc labels changed 405/1885 (527 into translated chapters)  exports with new epubcheck errors 0  failed projects 0
$ python3 scripts/verify/phase4_validate_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  round trip 9491/9491  translated 4162  failing the structural check 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 54; only tag rules 88)  warnings (length_ratio 36, untranslated 8)  length ratio over 2925 segments: p1 0.312 p5 0.333 p50 0.404 p95 0.547 p99 0.976; outside 0.25-0.8: 38 (1.3%)  sources that cannot be cut 0  with translatable attributes 22  translated code starting with a shebang 0  failed projects 0
$ python3 scripts/verify/phase5_glossary_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  glossary entries 15, translated 4162, passing the structural check 3956, with term_not_applied 12, terms 4, by category (person: 9 warnings, 2 terms, term: 6 warnings, 2 terms)  failed projects 0
$ python3 scripts/verify/phase6_segmentation_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  segments 9520 -> 7870, calls 9520 -> 6432 (ratio 0.676), passthrough 1438, headings merged 1655 of 1770  failed projects 0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

All four exited 0 with the TOTAL lines of Phase 8 (this phase changes no segmentation,
validation, glossary or export code). The fingerprint was the same before the first real call and
after all work. No real call sent text from the projects directory.

### Tests

2581 passed (main: 2545), and three suites at once: 2581 passed each, no failure. New:
`tests/test_copilot_plugin.py` (27): the exact command with `--experimental --plugin-dir
<PLUGIN_DIR>` and the environment variable holding `SYSTEM_PROMPT` (the rest of the environment
kept); one prompt object for both backends; the plugin's files in the repository and the
extension reading that variable; a replaced reply returned with its usage; a 112 000-character
prompt with 30 000 input tokens and the replacement passes; 14 forms of a reply without the
replacement (built-in segments, replacement plus another segment, one of two model calls or of
two checkpoints not replaced, empty or missing lists, malformed state, no checkpoint) raise the
unavailable error (`CLIBackend.complete` sets its `raw`, but run-level errors are not written to the failure diagnostics, so only the message reaches the owner; corrected in review round 1); an answer beside an ignored error event is checked
too; a failed run keeps its class; a CLI run (1 and 2 workers, real `CopilotCLI`, fake process)
stops with exit 1 and the message after at most one call per worker, every segment still pending
with no translation, and the same run with the replacement completes.
`TestPhase9CopilotOverheadScript` (`test_verify_scripts.py`, 9): the five commands, prompts,
environment, working directory and timeout, the lines printed, the exit code when a replaced call
fails or a broken setup is not detected, failed calls, another unavailable error not counted as a
detection, defaults and help. No fixed sleep; waits are on results.

Tests changed, none removed:

| Test | Change | Reason |
|---|---|---|
| `test_runtime` `_copilot_ok` (fixture of the backend contract and of `TestCopilotCLI`) | its usage checkpoint reports the replaced system prompt | a successful Copilot run must now report it, as the real one does |
| `test_runtime` `TestCopilotCLI::test_command` | the command ends with `--experimental --plugin-dir <PLUGIN_DIR>` | item 3 |
| `test_runtime` `TestRound1Copilot::test_error_beside_a_reply` | the two accepted runs include the replaced checkpoint | same as the fixture |

### Mutation testing

`scripts/verify/mutation.sh main`, mutmut 3.8.1, parallel (about 8 minutes per run). Added to the
mutated files: `scripts/verify/phase9_copilot_overhead.py`.

| Run | Mutants | Killed | Survived | Timeout |
|---|---|---|---|---|
| 1 | 386 | 329 | 57 | 0 |
| 2 (final) | 376 | 358 | 18 | 0 |

Run 1: every survivor was in the check script; none in `src/llm/` (the check, the command, the
environment, the shared prompt). Run 1 → 2: tests for the exact help text, two names after
`--only`, the working directory passed, a wall time printed, another unavailable error not
counted as a detection; the script's failure records no longer carry a wall time nobody reads,
the built-in command is cut with `del cmd[plugin - 1:]`, and the two prompts are built in one
expression (removing equivalent mutants).

Remaining, each equivalent (corrected in review round 1: the three `flush` mutants are not, see
there; 15 equivalent):

| mutant(s) | why equivalent |
|---|---|
| `command` 6, 7, 9, 14, 15, 16, 19, 20, 21 (the `LLMRequest` handed to `build_command`: `system`, `messages`, the message's role or content, `timeout` changed) | `CopilotCLI.build_command` reads only `request.model` from the request; the prompt is its own argument |
| `main` 132, 134, 138 (`flush=None/False/omitted` in the `--raw` print) | flushing changes when the line reaches the terminal, not what is printed |
| `main` 65 (`default=None` dropped for `--only`) | argparse's default for an optional argument is `None` |
| `median` 6 (`float('NAN')`) | `float()` parses `nan` case-insensitively: the same value |
| `prompt_text` 2 (`backend=None`) | `_build_prompt` does not use the backend; `None` makes a `ClaudeCLI` object, which starts nothing |
| `prompt_text` 6, 18 (`text` `None` / `'XXXX'`) | with `html` given and a text segment, `_build_prompt` formats the HTML prompt and never reads `text` |
| `prompt_text` 7 (`segment_type=None`) | `_build_prompt` only tests `segment_type == SegmentType.CODE`; `None` takes the same branch as `TEXT` |

### Known limitations and risks

- Undocumented: replacing the system prompt from an extension loaded through `--plugin-dir` in
  `-p` mode, and the `system_segments` report the check reads. A Copilot CLI update can break
  either; then every call fails the check and the run stops with `NOT_REPLACED_MESSAGE` (web:
  paused, CLI: exit 1) until the backend is updated. Extensions are an experimental feature.
- `mode: "replace"` drops Copilot's built-in guardrails with the rest of its prompt (the SDK
  documentation says so). The calls have no tools.
- One more process per call (`copilot extension_bootstrap.mjs`). It runs in its own process group,
  so a cancel kills it only indirectly: it exits by itself once `copilot` is killed (measured
  within about 1 s, twice). If a Copilot version kept it alive after its parent died, it would be
  left running; the cancel check above would show it.
- The extension needs `EPUB_TRANSLATOR_SYSTEM_PROMPT`; without it the CLI fails the run (exit 1,
  `ProtocolSystemMessageConfig`). `CopilotCLI` always sets it. (Corrected in review round 1:
  that error now stops the run as backend-unavailable instead of being retried as transient, since
  a Copilot update that changes the extension interface gives the same error.)
- The check accepts any `agent_replacement` content; that it is our prompt is shown by the hash
  (the SHA-256 prefix of `SYSTEM_PROMPT`), which is not checked.
- Copilot calls still each leave a session in the user's Copilot directory (as in Phase 7). The
  CLI also creates one empty folder per plugin location under `plugin-data/_direct/` there (two
  appeared during this phase: the worktree's and the copy's). Its `config.json` and
  `settings.json` do not mention the plugin (checked with `grep`).

### Not verified

- The web path with the real CLI (the CLI path ran for real; the web worker uses the same backend
  and the same error handling, tested with fake processes in Phase 7).
- Term extraction through Copilot with the replacement (same backend call).
- Models other than `claude-haiku-4.5`; Copilot versions other than 1.0.114.
- Translation quality with the short system prompt compared to Copilot's own; the replies were
  plain translations in every real call.

## Phase 9 — review round 1

Date: 2026-11-22. Opus and Gemini approved, GPT requested changes. Accepted items, each checked
against the code (and A and B against real output) first; all were real.

| Item | Finding | Fix | Test |
|---|---|---|---|
| A | `_replaced` pooled the calls of all checkpoints, so a good checkpoint followed (or preceded) by one with an empty `promptCacheBreakState` or `models` passed | every checkpoint must report at least one state, every state at least one model, each with exactly `[agent_replacement]` | six more cases in `test_not_replaced_stops` |
| B | Copilot rejecting the extension's system message (`ProtocolSystemMessageConfig`, exit 1) was transient: 4 calls and the backoff per segment, for the whole book | that text on an unanswered run → `BackendUnavailableError` with the not-replaced message and the CLI's text | `TestRound1::test_rejected_system_message_stops`, `test_cli_run_stops_without_retries` (1 and 2 workers: at most one call per worker, all pending) |
| C | exit 0, no reply, a rate-limit or auth message on stderr was reported as a missing plugin | an unanswered clean exit goes through the patterns first; only an unrecognized one reaches the replacement check (replaced: an empty reply, as before) | `test_clean_exit_without_reply_*` |
| D | the message blamed the extension, gave an absolute path, and did not show what the CLI printed (web pause, CLI stop and re-translate show only `str(e)`; `raw` of a run-level error is not written anywhere) | `無法確認 … 已停止。可能原因：外掛沒有載入（… src/llm/copilot_plugin 資料夾存在），或 Copilot 更新後改變了擴充功能的介面或 system_segments 的回報。` followed by `CLI 訊息：<redacted error texts and stderr, at most 500 characters>` when there are any; the Phase 9 entry's two "raw output kept" sentences corrected | `test_message`, `test_answered_with_warning_on_stderr_quotes_it`, `test_message_redacts_and_is_bounded` |
| E | the script's `--only` took any name or none, `--repetitions 0` was allowed, and a run without calls exited 0 | `--only NAME [NAME ...]` with the configuration names as choices, `--repetitions` at least 1 (`positive`), exit 1 when no call was made | `TestPhase9ScriptRound1` |
| F | the three `flush=True` mutants were recorded as equivalent; they are not when stdout is a pipe | killed with the observing-stream test of the Phase 7 script; the Phase 9 entry's count corrected (15 equivalent) | `TestPhase9ScriptRound1::test_raw_lines_flushed` |
| G | the script's header said it writes nothing | it names the sessions the CLI saves and the `plugin-data/_direct/` folder per plugin location | `test_header_names_the_side_effects` |
| H | two tests replaced `time.monotonic` for the whole process with a two-value iterator | they replace the script module's `time` with a `SimpleNamespace` (Phase 9 script and the Phase 7 overhead script's `_clock`) | the existing tests, changed |

Evidence for A: the 22 research outputs and one more invented call with the long prompt each have
exactly one usage checkpoint with one state and one model, and no real run emitted an empty
report; so requiring a report in every checkpoint rejects no real run. Evidence for B: one invented
call with the extension's variable removed: exit 1, stdout only a `result` event, stderr `Error
executing prompt: Error: GenericFailure, data did not match any variant of untagged enum
ProtocolSystemMessageConfig` (no model call; premium requests 0).

On the intermittent warning seen on `main` (item H): with a process-wide two-value clock, any
other thread that reads `time.monotonic` during the test (a worker or `run_process` poll loop
left running by an earlier test, `_wait`) takes a value or gets `StopIteration`. In that thread
the exception is unhandled and pytest reports it as `PytestUnhandledThreadExceptionWarning`,
possibly under a later test, and the test itself can see a wrong wall time or `StopIteration`. A
plausible cause, not shown to be the one seen.

Rejected (recorded, not done):

- GPT 2 (segment evidence for every model the accounting lists): every real call named one
  model; not realistic.
- GPT 6 (run the extension under a stubbed `joinSession` in the suite): would make the suite
  depend on Node; the check script proves the extension end to end.

Mutation (`scripts/verify/mutation.sh main`, parallel): 412 mutants, 398 killed, 14 survived, 0
timeout. The 14 are equivalent ones of the Phase 9 entry (`command` 6, 7, 9, 14, 15, 16, 19, 20,
21; `median` 6; `prompt_text` 2, 6, 7, 18); the `flush` ones are killed and the `--only` default
no longer exists. None in `src/llm/`. A first run stopped at the clean-run stage: the new location
test checked `src/` in `PLUGIN_DIR`, which the mutation copy renames; it now checks `llm/copilot_plugin`.

Real calls this round: 2 (above; one made no model call). Phase total: 30.

Tests: 2608 passed (was 2581), alone and three suites at once (2608 each), with `-rfw`: no
failure and no warning in any of the four runs. No test removed; changed: the two clock tests
(H), the Phase 9 help-text expectation for `--only` (E) and the message expectations (D). The
only warning seen in this phase was in the very first suite run, before any `.pyc` existed:

```
src/glossary/glossary_manager.py:30
  <worktree>/src/glossary/glossary_manager.py:30: DeprecationWarning: invalid escape sequence \w
    """Regex for a glossary term: it starts at a word boundary, with any
```

Python emits it only when it compiles that file (a docstring with `\w` that is not raw), so it
appears on a run that finds no cached bytecode and not on the next: a likelier source of the
intermittent warning on `main` than a thread.

Cause of the intermittent warning (seen during Phase 7 and after the Phase 8 merge): that docstring, the only one of the repository's Python files that warns when compiled (`py_compile` under `-W error`); it is now a raw string (`__doc__` only; no behaviour change).
Pinned by `tests/test_sources_compile.py`: every `.py` file under `src`, `scripts`, `tests` and `translate.py` is compiled in memory with warnings as errors (no bytecode written); it fails on the old docstring.

The projects fingerprint is `a7197555c962562a530d6de80fa045af` after this round.

## Phase 9 — review round 2

Date: 2026-11-22. Opus approved, GPT and Gemini requested changes. Each accepted item checked
first; all were real.

B. The check on every model of `CopilotCLI.MODELS` (one call each, the engine's prompt around
an invented paragraph; `python3 scripts/verify/phase9_copilot_overhead.py --only replaced
--repetitions 1 --model <m> --raw`, all exit 0). Every family reports exactly one
`agent_replacement` segment, so the check stays as it is:

| model | result | wall | input tokens | output tokens | system segments |
|---|---|---|---|---|---|
| `claude-sonnet-5.5` (default) | pass | 5.11 s | 1723 | 68 | 1 (14 tokens) |
| `gpt-6.2-sol` | pass | 6.81 s | 1366 | 57 | 1 (11 tokens) |
| `gemini-4-carbon` | pass | 24.05 s | 1142 | 1033 | 1 (11 tokens) |
| `claude-haiku-4.5` | pass (Phase 9 run, median of 3) | 11.79 s | 1670 | 673 | 1 (14 tokens) |

(The segment's token count is the vendor's tokenizer on the same one-line prompt.)

| Item | Finding | Fix | Test |
|---|---|---|---|
| A | `tests/test_sources_compile.py` walked a literal `src`, which the mutation runner renames, so its clean run failed; `plan/experiments/` was not compiled | the package is found through `import src.llm` (rewritten by the runner like every package reference); the test walks the repository root and the package, skipping hidden folders, `__pycache__` and virtualenvs (70 files here, the same as `git ls-files '*.py'`); it asserts known files (`translate.py`, itself, `scripts/verify/mutation.py`, the package's `llm/copilot_cli.py` and `glossary/glossary_manager.py`, and `plan/experiments/migrate_dryrun.py` where `plan/` exists, which it does not in the runner's copy) | `test_known_sources_found` |
| C | plain stdout was collected only for a non-zero exit, so an exit-0 run without a reply and with a rate-limit or login line on stdout was neither classified nor quoted | collected whenever there is no error event and either a non-zero exit or no reply | `TestRound2` |
| D | the script showed only the exit code for a call without a usage checkpoint | it names the class `classify_error` gives the result (`no error` when none): `no usage in the output (exit 0, BackendUnavailableError)` | `test_failed_calls` |
| E | three sentences were wrong: the Phase 9 limitation still called the rejected system message transient; the comment said it was observed with an empty variable (it was removed); the module docstring's "So does a run …" followed "does not depend on the prompt's length" | corrected | — |

Rejected (recorded, not done): GPT 1 (a truncated second checkpoint line after a valid one on an
exit-0 run): not a realistic output.

Real calls this round: 3 (B). Phase total: 33.

Mutation (`scripts/verify/mutation.sh main`, parallel): the first run of this round had a clean
baseline (the compile test now finds the renamed package) and 425 mutants, 409 killed, 16
survived; two were new in `classify_error` (`returncode == 0` and `!= 1` in the plain-stdout
condition) and are killed by `test_plain_stdout_of_a_failed_run_with_a_reply_is_quoted` (exit 1
and 2). Final: 425 mutants, 411 killed, 14 survived, 0 timeout: the 14 equivalent ones recorded in
round 1, none in `src/llm/`.

Tests: 2685 passed, alone and three suites at once, with `-rfw`: no failure and no warning in any
of the four runs. No test removed; changed: `test_failed_calls` (D) and the compile test (A).
The projects fingerprint is `a7197555c962562a530d6de80fa045af` after this round.

## Cleanup after Phase 9

Date: 2026-11-22. Branch: `cleanup/docs-and-checks` from `main`, worktree
`<repo>/.worktree/cleanup`, shared virtualenv (Python 3.9). No real model calls. No new feature.

### What was done

| # | Item | Change | Evidence |
|---|---|---|---|
| 1 | `README.md` (Traditional Chinese), `CLAUDE.md` (English) described the state before the plan (`claude -p` through `subprocess.run`, the old builder, 246 tests, ebooklib as a runtime dependency, no backends, migration or validation) | rewritten from the code and the `--help` of every command and subcommand: backends and how to choose them (Copilot's needs and risks in three lines), CLI commands and options, schema 2 and `migrate`, validation and `--retry-failed`, glossary consolidation and person gender/note, the brief, pause/resume/rate limit, debounced saves, the project lock, export, the routes of `app.url_map`, tests, the mutation run, the verify scripts, troubleshooting. No test count | — |
| 2 | `phase1_reverts.py` / `phase1_survivor_kills.py` no longer exited 0 | entries re-anchored at the code's new text; one mutant retired; three tests made to detect three reverts again (table below) | before/after below |
| 3 | glossary and export pages ran `init()` twice (Alpine 3 calls it, and `x-init="init()"` called it again) | `x-init="init()"` removed from both; no other template has it (`grep -rn 'x-init' src/web/templates`: only the toast watcher in `base.html`) | `test_other_pages_initialized_once[glossary|export]` (each fails with its page's old template); server log in a real browser below |

### Task 3: the double initialization, seen in a browser

Flask on port 3917 over a scratch projects directory with one invented project, both pages opened
in Chrome; the API requests the server logged per page load:

```
before (x-init="init()" present):
GET /api/projects/book/glossary            x2
GET /api/projects/book/glossary/extract/status  x2
GET /api/projects/book/export/status       x2
GET /api/projects/book/export/files        x2
after:
each of the four once
```

While an extraction was running, the glossary page polled it in two loops; otherwise the doubled
loads were harmless (the export page's `startPolling` clears its previous timer).

### Task 2: the phase 1 guards

Before (both on `main`):

```
$ python3 scripts/verify/phase1_reverts.py | tail -1
reverts not detected: 23        (82 OK; 22 BAD "has the old text 0 times", 1 MISS)
$ python3 scripts/verify/phase1_survivor_kills.py | tail -1
not killed 14                   (68 KILLED; 13 BAD "old text found 0 times", 1 SURVIVED)
```

After (the last line of each output; the exit codes were taken without a pipeline, see review
round 1 below for the commands):

```
reverts not detected: 0         (105 OK)     exit 0
not killed 0                    (81 KILLED)  exit 0
```

Reverts (`phase1_reverts.py`): every stale entry's guarded code still exists in a recognisable
form, so all were re-anchored (the same old behaviour put back at the new place); none retired.

| entry | what changed in the code | new anchor |
|---|---|---|
| 3 mark_reviewed outside the mutex | `_find` → `_changeable` | `with self.mutex:` before `seg = self._changeable(...)` in `mark_reviewed` |
| 5 logging set up outside the try | set-up of `_run_translation` (saver, `log_dir`) | `handler = None` before `log_dir`; the call after `try:` |
| 6 web coordinator failure does not wake paused workers | `_fail_task` also sets `cancel_event` | `task.pause_event.set()` after `task.cancel_event.set()` |
| 6 CLI supervisor failure does not join workers | the join is in `try/finally`, the message changed | the join loop inside `try:` |
| 7 late run error after stop pauses the task | a generation check follows the stop check | the stop check before `if task.cancel_event is generation:` |
| 11 model select keeps a refused model | `setModel` restores the select element | `document.getElementById('model-select').value = previous;` |
| 12 model list from the Claude module | `available_models` takes a backend | `llm.backend_for(backend).list_models()` |
| 13 issue detail unbounded | the issue is built in `TranslationError` | `detail=message[:ERROR_MESSAGE_CHARS]` |
| 14 timeout drops the output / R3-12 timeout raw not bounded | `raw` goes through `self.redact` | the redacted `raw=` |
| 17 edit keeps FAILED and issues | `update_translation` stores the given issues | `seg.issues = list(issues or [])` |
| 24 approve leaves the failure log | approve checks the text and answers with its warnings | `remove_failure_log` after `pm.save()` before the warnings answer |
| R2-10 re-translate records the log before acceptance and save | the call has more arguments | after the call's last line |
| R2-17 edit removes the log outside the transaction | the answer is built by `_answer` | the block including `answer = _answer(...)` |
| R2-11 run-level error not kept on the task | the error is kept in `_pause_for` | `task.error = str(error)  # for a page opened later` |
| R2-12 stored model not shown | models are keyed `backend/alias` | `m.key === this.modelKey()` |
| R2-20 stale and skipped results reported as unsaved | unsaved results come from `DebouncedSaver.pending`, which only applied results enter | `if self._apply_result(...) or True:` + the drain filter (corrected in review round 1: only the drain filter) |
| R3-2 stopped event leaves state and stream | the handler also clears `autoResume` | the `stopped` handler with `this.autoResume = false;` |
| R3-6 initial status without the error | the status has `resume_in` after the error | `'total': task.total, 'error': task.error,` |
| R3-9 sequential CLI goes on after five unexpected failures | the stop is raised as `_RunStopped` | `stop = streak.add(outcome)` … `raise _RunStopped(stop)` |
| R4-2 worker added on resume listed and counted before its start | moved into `_top_up_workers` | its start block |
| R4-3 streak stop discards in-flight results | a comment was added | the block with `# resume_translation refuses it now` |

The 23rd, `6 web coordinator failure does not join workers`, matched but was a MISS (2 passed):
since the debounced saves the run's first save comes after the workers have ended, so in
`test_save_failure_joins_workers_and_reports_unsaved` nobody is in flight when `_fail_task` runs,
and in `test_save_failure_wakes_paused_worker` the woken workers end before the test looks. With
the re-anchored entries, `6 CLI supervisor failure does not join workers` (same cause in the CLI)
and `7 late run error after stop pauses the task` (the single worker breaks at the loop's top on
the stop event either way) were MISSes too. Three assertions make them detectable again:

- `TestCoordinatorFailure::test_save_failure_wakes_paused_worker` records `Thread.join` calls:
  every worker must have been joined (the save fails at the pause, `_fail_task` does the joins);
- `TestCLISupervisorFailure::test_save_failure` the same for the supervisor;
- `TestStopThenLateRunError::test_stop_then_error`: `task.pause_event` is still set at the end (the
  revert clears it after the stop; this test's worker still exits at the next stop check, but a
  cleared gate could strand another worker already past that check; corrected in review round 1).

Survivor kills (`phase1_survivor_kills.py`):

| entry | decision | reason |
|---|---|---|
| core58 | re-anchored | `mark_translated` uses `_changeable` and has a comment |
| core62, core63 | re-anchored | a `seg.status` line now stands between the text and the time; anchored on status, time and the blank line after |
| web171–176 | re-anchored | the edit and approve answers are built by `_answer(pm, gm, issues, status='ok', segment_id=...)` ; the same key/value mutants there |
| cli8 | re-anchored | `value` → `model` |
| cli75 | re-anchored | `tick()` before `continue` |
| cli78 | re-anchored | message now `翻譯中斷，正在停止進行中的段落……` (the calls in flight are killed) |
| cli82 | re-anchored | `_report_unsaved` joins `saver.pending` |
| web142 | retired | `continue` → `break` after a skipped segment: `_workers_done` starts workers again while a segment is still queued, so the run translates the rest when those replacement workers start (fewer workers run meanwhile, and a replacement that cannot start fails the task; narrowed in review round 1); `test_worker_goes_on_after_a_skip` still checks the run goes on |

The tests named by each entry were run on the re-anchored entry alone first (all OK / KILLED),
then both scripts in full.

### Checks

```
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
$ python3 scripts/verify/phase3_export_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  exports 24  translated segments 4162  as translation 3956  kept original 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 53)  toc labels changed 405/1885 (527 into translated chapters)  exports with new epubcheck errors 0  failed projects 0
$ python3 scripts/verify/phase4_validate_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  round trip 9491/9491  translated 4162  failing the structural check 206 (code_changed 19, empty_block 14, key_mismatch 35, not_well_formed 20, structure_mismatch 65, tag_count 54; only tag rules 88)  warnings (length_ratio 36, untranslated 8)  length ratio over 2925 segments: p1 0.312 p5 0.333 p50 0.404 p95 0.547 p99 0.976; outside 0.25-0.8: 38 (1.3%)  sources that cannot be cut 0  with translatable attributes 22  translated code starting with a shebang 0  failed projects 0
$ python3 scripts/verify/phase5_glossary_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  glossary entries 15, translated 4162, passing the structural check 3956, with term_not_applied 12, terms 4, by category (person: 9 warnings, 2 terms, term: 6 warnings, 2 terms)  failed projects 0
$ python3 scripts/verify/phase6_segmentation_check.py --projects-dir <repo>/projects --anonymize
TOTAL projects 8  segments 9520 -> 7870, calls 9520 -> 6432 (ratio 0.676), passthrough 1438, headings merged 1655 of 1770  failed projects 0
$ stat -f '%m %z' <repo>/projects/*/project.json | md5
a7197555c962562a530d6de80fa045af
```

All four exited 0 with the TOTAL lines recorded in Phase 9.

Tests (`python3 -m pytest tests/ -q -p no:cacheprovider -rfw`): 2687 passed alone (230 s) and in
each of three suites run at once (227, 229, 228 s); no failure and no warning in any of the four
runs. 2687 = 2685 of Phase 9 + the two cases of `test_other_pages_initialized_once`. No test
removed; three tests got an assertion (task 2).

Mutation: not run. No Python file under `src/` changed (two templates, tests, two verify scripts
and the docs); `scripts/verify/mutation.sh` mutates Python only.

### Not verified

- The docs' description of the Copilot backend's real behaviour rests on the Phase 9 record
  (no real calls here); its model list is the one in `copilot_cli.MODELS`.
- The glossary and export pages were checked in a browser only for the requests on load; the
  extraction and export flows themselves were not run in the browser (their tests pass).
- `known-issues.md` still describes a sentence split across segments; the segmenter now never
  divides a block (`src/core/segmenter.py`). Left as it is (outside this pass).

## Cleanup after Phase 9 — review round 1

Date: 2026-11-22. Opus approved, GPT and Gemini requested changes. Every item was checked against
the code first; all were real, none was rejected. Most were documentation accuracy.

### Documentation (`README.md`, `CLAUDE.md`)

| Topic | Correction | Code |
|---|---|---|
| Runnable commands (GPT 2) | `git clone "$REPO_URL"`; the CLI lines with `|` in CLAUDE.md are a `text` synopsis; README examples one command per line; `init`, `validate`, `migrate` take a path, the others `-p` | `translate.py` command definitions |
| Troubleshooting (GPT 1) | lead with the web pause button / Ctrl-C; no `pkill -f` (hits other sessions) or `lsof … \| xargs kill`; one verified PID; process arguments contain book text; killing the web server loses unsaved results | `runcontrol.DebouncedSaver`, `claude_cli.LEAN_FLAGS` (prompt in argv) |
| Failed translations (GPT 6, Opus 3, Gemini 2, 4) | a pending segment becomes `failed` (`--retry-failed`); a translated/reviewed one keeps text and status and only gets issues; stored translations that fail: `validate --mark-failed` then `start --retry-failed` | `ProjectManager.apply_outcome`, `get_pending_segments`, `translate.py validate` |
| Locks (GPT 7, Opus 1, Gemini 3) | `project.lock` and the mutex cover `project.json` (and which operations take the lock); `glossary.json` takes neither: edits go through during a run, are not seen by it, can overwrite each other | `web/blueprints/glossary.py`, `GlossaryManager.save`, glossary loaded once in `_run_translation` / `_start` |
| Pauses (GPT 8, Gemini 5, Opus 4) | a web pause cancels calls in flight; a CLI rate limit stops dispatch and backoff waits, running calls finish; neither backend reports `retry_after` (300 s); unavailable backend: web pauses, CLI exits 1 | `tasks.pause_translation`, worker `RUN_ERRORS` branch, `translate._start_parallel` |
| Saving (GPT 9) | the 10 s / 20 results are a threshold checked after a result (web also per tick), not a durability bound; pause/stop/end/failure attempt a flush | `DebouncedSaver.maybe_save`, `_tick`, `_start_sequential` |
| Migration (GPT 4, Opus 6) | needs the source EPUB; refuses when the backup exists or the project is locked; a merged group with a pending/failed/empty member keeps no translation: read the dry run; the lock file is created even when nothing else is written | `migrate.migrate_project`, `plan_migration` |
| Web project creation (GPT 3) | replaces an idle project of the same name (`project.json`, `source.epub`, `log/failed/`; `glossary.json` kept); CLI `init` refuses | `projects.create_project`, `translate.init` |
| Export source path (GPT 10) | export opens the absolute `epub_path` recorded at creation, not the directory's `source.epub`; a moved project directory cannot export (README note, CLAUDE.md, `known-issues.md`); unchanged members keep identical uncompressed bytes, the archive is rewritten; passing `validate` does not guarantee export | `exporter._export` |
| Copilot-only order (GPT 5) | pick a Copilot model on the web translation page before extraction; `start -b` is the only CLI setter and it translates | `ProjectManager.create` (default backend), `glossary_extract` |
| Platform (GPT 11) | POSIX only (`fcntl`, process groups) | `project.py` import, `cli.run_process` |
| Mutation environment (Gemini 1, GPT 23) | both requirements files in the 3.10+ environment; exits 2 without mutmut or without eligible changed lines | `mutation.sh`, `mutation.main` |
| Verify scripts (GPT 13, Opus 5, Gemini 8) | phase 0, 1, 2-roundtrip read in place, the others work on a copy; `--anonymize` covers stdout only; `--sample-out` / `--out` files hold book text | script docstrings |
| Prompts and limits (GPT 20, 21, Opus 2, Gemini 6, 7) | brief, glossary and context only in text prompts; context is the nearest non-code segment (its translation with rules 2 when it has one); a heading's first text block may pass 500 words; 4 calls per `translate_segment`; 120 s translation, 180 s extraction; 150-entry rule; CLI extraction replaces translation and category; Copilot rate-limit messages documented, auth guessed | `translator._build_prompt`, `get_context`, `segmenter`, `TermExtractor`, `copilot_cli` docstring |
| Minor wording (GPT 12, 19, 22) | validation entry points (`check_html`/`check_code` shared, `check_segment` for review and `validate`); `p = protect(html); restore(p.html, p)`; a missing `model` is written on the next save, `source_sha256` is project-level; SSE gives a snapshot then live events; the export flushes the running saver; `GET …/model` response fields | `validation`, `ProjectManager.load`, `translation` and `export` blueprints |

### Checks and tests

- R2-20 (GPT 14): the `or True` edit put a rejected result into the saver, so its test failed
  because a save happened, not because of a wrong report. Since Phase 7 the saved part of the
  report is `DebouncedSaver.pending`, which only accepted results enter; the old behaviour
  survives only in the queue drain of `_fail_task`. The entry now puts back only that filter
  (`if True:`), and `test_skipped_result_in_queue_not_reported` also queues a stale result, so the
  revert fails on the reported IDs (`boom（1 個段落的結果未儲存：ch1_seg0003）` expected).
- `test_save_failure_wakes_paused_worker` (GPT 16): the first save now fails only once a worker is
  waiting in `pause_event.wait()` (a recording `Event.wait`), so the wake-up is what ends it; the
  test also checks the error is the save's.
- `TestCLISupervisorFailure::test_save_failure` (GPT 15, Opus 7): every result is saved at once,
  so the failing save comes while the MARK4 call is still in flight (the others return only after
  it started); the call ends only when the supervisor joins, and the test checks it ended before
  `invoke` returned and that all four started workers were joined. The `sleep(1)` and its comment
  are gone, also from `test_save_failure_joins_workers_and_reports_unsaved`.
- Log wording (GPT 17, 18, 24): the late-error assertion, the web142 reason ("when those
  replacement workers start") and the exit codes are corrected above.
- `known-issues.md`: its only entry (a sentence split across segments, `block_count`) no longer
  holds: the segmenter never divides a block (`src/core/segmenter.py`). It is replaced by the
  export path limitation.

```
$ python3 scripts/verify/phase1_reverts.py > reverts.txt; echo "exit $?"
exit 0                      (last line: reverts not detected: 0; 105 OK)
$ python3 scripts/verify/phase1_survivor_kills.py > kills.txt; echo "exit $?"
exit 0                      (last line: not killed 0; 81 KILLED)
```

Tests: 2687 passed alone (218 s) and in each of three suites at once (218, 221, 229 s); no
failure and no warning in any run. No test added or removed this round; three changed (above).
Mutation: not run (no Python file under `src/` changed). Projects fingerprint unchanged
(`a7197555c962562a530d6de80fa045af`). No real model calls.

### Product behaviour documented that may be a defect

- Export reads the absolute `epub_path`, so moving a project directory breaks export although
  `source.epub` is inside it.
- Glossary edits are not locked: two concurrent edits (web and CLI, or two tabs) can overwrite
  each other, and a running translation does not see them.
- Web project creation replaces an idle same-name project (the page warns; the API does not ask).
- Migration can clear the translation of a merged group whose members were partly translated
  (kept in `project.schema1.json`).
- A failed re-translation of a translated or reviewed segment is not selected by
  `--retry-failed`.
- The CLI cannot set the backend without starting a translation.

## Fix: export source path and `model` command

Branch `fix/export-source-and-model-cmd`. Two changes; no new options.

### 1. Export survives a moved or renamed project directory

`source_epub_path(project_dir, epub_path)` (`src/core/project.py`, with `SOURCE_FILE`) is the one
choice of the source EPUB: the project directory's `source.epub` when it is a file, else the
stored `epub_path` (projects without a copy). Readers found (every use of `epub_path`,
`EPUBExtractor(`, `open_archive(`, `Archive(`) and how each resolves now:

| Reader | Before | Now |
|---|---|---|
| `exporter._export` (CLI and web export) | `data.epub_path` | `source_epub_path`; the hash check and the "output must not be the source" guard use the file opened; `epub_path` is not rewritten |
| `migrate.migrate_project` | own inline choice | `source_epub_path` (same behaviour) |
| `translate.py status` ("來源：" line) | `data.epub_path` | `source_epub_path` (display only) |
| `phase2_roundtrip.source_path`, `phase6_segmentation_check.source_of` | own copies of the choice | `source_epub_path` |
| `phase2_migrate_copies.placed_chapters`, `phase3_export_check.check_copy` | `data.epub_path` | `source_epub_path` |

Left unchanged: `plan/experiments/migrate_dryrun.py` (`check_project`) opens the stored
`epub_path` directly; it is an old experiment, superseded by `migrate --dry-run`.

Not readers of the source: `validate` and re-translation (they use `project.json` only); `init`
and web `POST /api/projects` write the copy (unchanged); `EPUBExtractor` in the web upload and
metadata endpoints reads the uploaded file.

### 2. `translate.py model show` / `model set [-b] [-m]`

`ProjectManager.select_model(backend, model)` holds the rules once, used by `model set`,
`start -b/-m` (through `translate._select_model`, which turns a refused model into a usage error
of `-m`) and the web `PUT …/model`: the project's own backend is not stored again; a new backend
without a model takes its default model; the model must be one of the models of the backend it
will run with; an unknown backend or model raises `ValueError` and changes nothing; returns
whether anything was set (nothing set: nothing saved). `translate._check_model` and the inline
check of the web blueprint are gone. `model set` takes `project.lock` like `brief set` (locked:
the lock message, exit 1); neither option, an unknown backend (`click.Choice`) or a refused model
is a usage error (exit 2) with `project.json` byte-identical.

Behaviour changes, deliberate: the refused-model message is now
`無效的模型：<model>，可用模型：<a, b, …>` in the CLI too (was `'<model>' is not one of …`); four test
assertions follow it. The web refuses `{"model": null}` with `未提供模型名稱` (before: `無效的模型：None…`),
since `select_model` takes `None` as "keep".

### Tests (32 added, 4 assertions changed)

- `test_exporter.py::TestSourceOfAMovedProject` (7): a project made by `translate.py init`, then
  renamed into another directory with its stored path gone: every format exports and equals the
  unmoved export member by member (names and bytes), `epub_path` unchanged; CLI export of a
  moved project; `source.epub` replaced by a different EPUB is refused (hash), no output; the
  copy is preferred over an existing different file at the stored path; output onto the copy
  of a moved project is refused; a project without a copy exports from the stored path (and
  output onto it is refused); `source_epub_path` with a directory named `source.epub`.
  Five of the seven fail with the old `data.epub_path` in `_export` (checked by editing it back).
- `test_cli.py::TestCLIModelCommand` (12): `show` output; `set -b copilot-cli` then
  `glossary extract` calls the newly stored backend (a fake under `copilot-cli`); `-m` alone;
  both; own backend alone saves nothing; refused values (`-m nope`, a Claude model for Copilot,
  `-b nope`, no option) exit 2 with `project.json` byte-identical; refused while another process
  holds the lock (byte-identical, lock free afterwards); no project; help texts.
- `test_project.py::TestSelectModel` (10), `test_web.py` (2: null model, exact refusal message,
  both byte-identical), `test_verify_scripts.py` (1: `placed_chapters` reads the stored path
  without a copy and the copy in a moved project).

### Commands and results

```
$ source <repo>/venv/bin/activate                     # Python 3.9 suite; mutmut on its 3.12
$ python3 -m pytest tests/ -q -p no:cacheprovider -rfw     # three at once
2719 passed in 218.67s / 220.85s / 221.75s            (no failure, no warning in any)
$ scripts/verify/mutation.sh main                      # run 1
TOTAL mutants 104  killed 103  survived 1  timeout 0  suspicious 0  untested 0
    survivor: phase2_migrate_copies.placed_chapters, epub_path -> None (no test without a copy)
    killed by test_placed_chapters_source_resolved
$ scripts/verify/mutation.sh main                      # run 2 (final)
TOTAL mutants 104  killed 104  survived 0  timeout 0  suspicious 0  untested 0
$ python3 scripts/verify/phase1_reverts.py; echo "exit $?"
exit 0                      (reverts not detected: 0; 105 OK)
$ python3 scripts/verify/phase1_survivor_kills.py; echo "exit $?"
exit 0                      (not killed 0; 81 KILLED)
```

`phase1_survivor_kills.py`: entries `cli7` and `cli8` mutated the old `--model` message in
`translate.py`; they now mutate the same message in `ProjectManager.select_model` (the first run
after the change reported `cli7` SURVIVED: its anchor matched the new `model show` line first).
Click commands (`model show`, `model set`) are decorated, so mutmut does not mutate them; their
lines are covered by `TestCLIModelCommand`. The scripts that read real projects were not run (no
access to the projects directory on this branch). No real model calls.

## Fix: export source path and `model` command — review round 1

Two approvals, one "changes requested". Fixed:

- `PUT …/model` (`src/web/blueprints/translation.py`, `set_model`): a JSON body that is not an
  object (`"opus"`, `["opus"]`, `1`) raised `AttributeError` (500) after the first round; `main`
  answered 400 for `"opus"`/`["opus"]` and 500 for `1`/`true`. It is 400 (`未提供模型名稱`) for
  all of them now (`isinstance(data, dict)`). (Corrected in Phase 10 review round 1: this
  said `main` answered 400 for every such body.)
- Same route: the change and the save now run under `pm.mutex`, and a failed save puts the
  previous backend and model back on the manager, as the brief route does, so a running task's
  workers keep the saved values. `main` had the same gap (`set_model` then `save()` with no
  restore), so this is an existing defect fixed here, not one the branch introduced.
- `model set -b` help: the backend's default model is taken only when the backend differs from
  the project's and `-m` is not given (it said "without --model"). README says the same now;
  CLAUDE.md already said "a new backend".
- `TestCLIModelCommand::test_own_backend_alone_changes_nothing` starts on `opus` and checks
  that `ProjectManager.save` is not called, the bytes are unchanged and the model is still `opus`.
- Docs (CLAUDE.md Export paragraph and troubleshooting, README export note and troubleshooting):
  every schema 2 project has its own `source.epub`, written at creation (web since its first
  version, CLI `init` since schema 2) or by `migrate`. "無法讀取來源 EPUB" therefore means that
  copy is missing (removed or renamed) and the stored path does not work either. The digest is
  the one recorded at creation or migration. The `epub_path` fallback is described as such only
  for the migration of schema 1 projects.
- `plan/experiments/migrate_dryrun.py` is listed above as a reader left unchanged.

Considered and declined:

- Phase 2/6 check scripts load `project.json` even when the copy exists: acceptable (one small
  read; the scripts' results do not depend on it).
- "Hash then reopen" window with a project re-creation or deletion: considered, recorded as a
  known issue (`known-issues.md`). Inside one web server it is not refused: `ProjectLock` is
  shared by the threads of the holding process, the export thread holds only that lock, and
  create/delete check `task_active` (translation tasks), not exports; across processes the
  flock prevents it. `main` had the same window. Docs only; pytest after this change: 2725
  passed, no warning.

Tests: 6 added (`test_web.py`: 4 bodies that are not objects, a failed save with and without a
backend switch), 2 changed (the no-op test, the help text). Reverting each fix makes its new
tests fail (checked by editing the route back: 2 and 3 failures).

```
$ python3 -m pytest tests/ -q -p no:cacheprovider -rfw      # three at once
2725 passed in 224.25s / 231.91s / 231.48s           (no failure, no warning in any)
$ scripts/verify/mutation.sh main
TOTAL mutants 104  killed 104  survived 0  timeout 0  suspicious 0  untested 0
$ python3 scripts/verify/phase1_reverts.py; echo "exit $?"
exit 0                      (reverts not detected: 0; 105 OK)
$ python3 scripts/verify/phase1_survivor_kills.py; echo "exit $?"
exit 0                      (not killed 0; 81 KILLED)
```

The route fixes are in a decorated Flask view, which mutmut 3.8.1 does not mutate (the mutant
count is unchanged); the new web tests cover them. No real model calls.

## Phase 10 — Reasoning effort and call timeout

Branch `feat/reasoning-effort-and-timeout`. Two per-project settings, chosen like backend and
model (plan, Phase 10). Installed CLIs: Claude Code 2.1.330 (`claude --help`: `--effort <level>
(low, medium, high, xhigh, max)`), Copilot CLI 1.0.114 (`copilot --help`: `--reasoning-effort
<level>`, "supported levels depend on the model").

### Design

- Levels per model: `ModelInfo.efforts` (`src/llm/base.py`, `EFFORT_LEVELS` = the five values),
  declared in `MODELS` of `claude_cli.py` and `copilot_cli.py`; `effort_levels(backend, model)`
  (`src/core/project.py`) reads them; a model the backend does not list has none.
- Project fields `effort` and `timeout` (`ProjectData`), None when the file has no key, never
  written while None (like `backend`/`brief`), so old files round-trip. `call_settings(data)` →
  `CallSettings(backend, model, effort, timeout)`: effort = stored or `DEFAULT_EFFORT` (`low`) for a
  model with levels, None otherwise; timeout = stored or `DEFAULT_TIMEOUT` (180).
  `CallSettings.call_args()` gives the keyword arguments of `TranslationEngine` and `TermExtractor`
  (both take `effort` and `timeout`; the engine puts the effort on `LLMRequest.effort`). Load
  refuses an `effort` outside `EFFORT_LEVELS` and a `timeout` outside 30–3600 (as it refuses an
  unknown backend). A stored level the model does not take (only by hand editing, or a CLI
  catalog change) is passed as it is: Copilot then refuses it and the run stops with its message.
  (Corrected in review rounds 1 and 2: load checks `KNOWN_LEVELS`, which adds Copilot's `none`;
  a stored level the model does not list is not passed, `call_settings` gives the default.)
- `ProjectManager.select_model(backend, model, effort=None, timeout=None)`: every refusal is a
  `SettingError` (a `ValueError` with `setting`) raised before anything changes; a stored level
  the resulting model does not take becomes None (the default) (corrected in review round 2:
  only when the backend or model changes); `effort_reset_note` builds the
  message (`原推理強度 X 不適用於 M，已改回預設：low|此模型不可設定`).
- Callers: web worker (`tasks.py`, re-reads `pm.call_settings()` per segment and rebuilds the
  engine when any of the four changes), CLI sequential and parallel runs, `glossary extract`
  (CLI and web), web re-translation.
- Claude: `--effort LEVEL` after `--model`; `CLAUDE_CODE_EFFORT_LEVEL` removed from the child
  environment. Copilot: `--reasoning-effort LEVEL` after `--model`, nothing for
  `claude-haiku-4.5`; its two refusal texts → `BackendUnavailableError`
  (`EFFORT_REJECTED_MESSAGE` + the CLI text); `classify_error`'s pattern table now maps each
  pattern to a function building its error (same messages as before for the others).
- CLI: `model set -e/--effort`, `-t/--timeout` (click `INT`; range from `select_model`), each
  refusal a usage error of its own option; `model show` adds 推理強度, 可用推理強度, 呼叫逾時;
  `start` prints the effort/timeout line after 使用模型.
- Web: `GET …/model` adds `effort`, `efforts`, `timeout`, `timeout_min`, `timeout_max`, and every
  model in `available`/`backends` carries `efforts` (so the page shows the right levels as soon
  as another model is picked). `PUT` takes `effort`, `timeout` beside `model` (still required;
  corrected in review round 1: every setting is optional, at least one required),
  answers the stored settings and `note`, restores all four on a failed save. Page: effort
  `<select>` (`:selected` + `@change`, no `x-model`; disabled "此模型不可設定" when the model has
  no levels), timeout number field; on a refusal the control shows the stored value again.
  Checked in Chrome against a server on an invented project: xhigh saved; switching to Gemini
  showed `low, medium, high`, selected `low`, and the reset note; Haiku 4.5 showed the disabled
  "此模型不可設定"; timeout 10 refused with the message and the field back at 180; 240 saved.

### Tests (115 added, 11 assertions changed)

- `tests/test_effort_timeout.py` (93): per-model levels; exact Claude and Copilot command lines
  with and without a level; Claude environment without `CLAUDE_CODE_EFFORT_LEVEL` (Copilot's
  unchanged); seven backend/model/level combinations from stored settings to the program's
  argv, timeout and environment through the engine; the three Copilot refusal texts (stderr and
  error event) → `BackendUnavailableError`, a reply quoting them is no error; a CLI run exits 1
  and a web task pauses (`backend_error`) after one call with every segment still pending and no
  failure log; `select_model` (defaults, effort/timeout alone, kept or reset on a model change,
  unconfigurable model, 12 refusals changing nothing); project files (old file loads as the
  defaults and saves without the keys, set values written, bad stored values refused, a stored
  level passed as it is (corrected in review round 1: the default is used), an unlisted model
  gets none); CLI show/set/reset note/7 refusals
  byte-identical/start line/effort and timeout reach `start` (1 and 2 workers) and `glossary
  extract`/help; web GET/PUT/reset note/12 refused bodies byte-identical/failed save restores
  all four/extraction and re-translation get both/page markup; a mid-run switch of effort and
  timeout reaches the second segment's request.
- `tests/test_verify_scripts.py::TestPhase10EffortScript` (20).
- Changed: the 180 s default (2 in `test_translator.py`), `ModelInfo` with levels
  (`test_llm.py`, `test_web.py` ×2), `model show`/`set` output and the usage error
  (`test_cli.py` ×5), the PUT answer (`test_runtime.py` ×2).

### Real calls (`scripts/verify/phase10_effort_check.py`, invented paragraph)

13 calls, one per cell, no repeats, no rate limit. `reported` is the level Copilot's
`session.start` names (Claude reports none). Tokens: Claude input = input + cache creation +
cache read; output includes the reasoning tokens.

```
$ python3 scripts/verify/phase10_effort_check.py --backend claude-cli --model haiku --levels low high
claude-cli   haiku              low     reported -       wall    3.82 s  in   2200  out    290  reasoning      0  ok
claude-cli   haiku              high    reported -       wall    9.53 s  in   2200  out   1662  reasoning   1342  ok
$ … --backend claude-cli --model sonnet --levels low high
claude-cli   sonnet             low     reported -       wall    4.91 s  in   2179  out    337  reasoning      0  ok
claude-cli   sonnet             high    reported -       wall    4.57 s  in   2179  out    336  reasoning      0  ok
$ … --backend claude-cli --model sonnet --levels max
claude-cli   sonnet             max     reported -       wall   82.27 s  in   2179  out  11601  reasoning  11270  ok
$ … --backend claude-cli --model opus --levels low high
claude-cli   opus               low     reported -       wall    6.25 s  in   2175  out    321  reasoning      0  ok
claude-cli   opus               high    reported -       wall    7.74 s  in   2175  out    426  reasoning    114  ok
$ … --backend copilot-cli --model claude-sonnet-5.5 --levels low high
copilot-cli  claude-sonnet-5.5  low     reported low     wall   11.66 s  in   1651  out    327  reasoning      0  ok
copilot-cli  claude-sonnet-5.5  high    reported high    wall    7.84 s  in   1651  out    324  reasoning      0  ok
$ … --backend copilot-cli --model gpt-6.2-sol --levels low high
copilot-cli  gpt-6.2-sol        low     reported low     wall    7.38 s  in   1273  out    250  reasoning      0  ok
copilot-cli  gpt-6.2-sol        high    reported high    wall   10.16 s  in   1273  out    565  reasoning    310  ok
$ … --backend copilot-cli --model gemini-4-carbon --levels low medium
copilot-cli  gemini-4-carbon    low     reported low     wall    9.08 s  in   1086  out    215  reasoning      0  ok
copilot-cli  gemini-4-carbon    medium  reported medium  wall   11.52 s  in   1086  out    421  reasoning    209  ok
```

Every call exited 0. Evidence that the flag reaches the CLI: Copilot names the level passed in
every call; Claude's reasoning tokens change with the level (haiku 0 → 1342, opus 0 → 114,
sonnet 0 at low and high but 11270 at max). Sonnet thought nothing at `high` on this paragraph,
so a third Sonnet call at `max` was made (13 calls in all). Gemini was measured at `low` and
`medium`, not `high`: in the research `high` did not finish a comparable paragraph in 300 s,
beyond the 180 s default. `claude-haiku-4.5` on Copilot takes no level and was not called.
Single samples: adaptive reasoning varies from call to call.

### Commands and results

```
$ source <repo>/venv/bin/activate                     # Python 3.9 suite; mutmut on its 3.12
$ scripts/verify/mutation.sh main                      # run 1
TOTAL mutants 642  killed 581  survived 61  timeout 0  suspicious 0  untested 0
$ scripts/verify/mutation.sh main                      # run 2, after tests for the survivors
TOTAL mutants 642  killed 634  survived 8  timeout 0  suspicious 0  untested 0
$ scripts/verify/mutation.sh main                      # run 3 (final)
TOTAL mutants 642  killed 638  survived 4  timeout 0  suspicious 0  untested 0
$ python3 -m pytest tests/ -q -p no:cacheprovider -rfw     # three at once
2840 passed in 218.43s / 225.69s / 220.93s            (no failure, no warning in any)
$ python3 scripts/verify/phase1_reverts.py; echo "exit $?"
exit 0                      (reverts not detected: 0; 105 OK)
$ python3 scripts/verify/phase1_survivor_kills.py; echo "exit $?"
exit 0                      (not killed 0; 81 KILLED)
```

Run 1 survivors were killed by tests (in the commits after the feature): exact messages of
`check_effort` and of `call_settings` without a loaded project; and in the script: the request
handed to `build_command`, every argparse option (required, choices, help, metavar, the
description's first line only), the refusal message with and without levels, flushing each line,
the wall time of a failed call, the prompt passed to `classify_error`, an empty Claude reply, a
Copilot run without `session.start`. Run 2 left `--model` required and the description; killed.

The 4 survivors of run 3 are equivalent on any machine; all are in `prompt_text` of the script,
which builds the engine's real text prompt: `TranslationEngine(backend=None)` (the default
backend instead of a fake; `_build_prompt` never uses the backend), and the first two arguments
of `_build_prompt` as `None`/`'XXXX'` text and `None` segment type (with `html` given the text is
not used, and any type other than CODE takes the same text path).

No phase 1 guard entry had to be re-anchored. Decorated functions (click commands, Flask views)
are not mutated by mutmut 3.8.1; their lines are covered by `TestCLI` and `TestWeb`. The scripts
that read real projects were not run (no access to the projects directory on this branch).

## Phase 10 — review round 1

One approval, two "changes requested". Fixed:

- `PUT …/model` (`src/web/blueprints/translation.py`, `set_model`): every setting is optional;
  a body with none of `model`, `backend`, `effort`, `timeout` (or only nulls) is 400
  (`未提供要設定的項目…`). The page sends only the setting the user changed (`putSettings(body)`),
  so a stale tab that changes the timeout no longer switches a running task's model back, and a
  project whose stored model is not listed can change its timeout. Two message assertions in
  `test_web.py` follow; `{"model": null, "backend": "copilot-cli"}` is now a backend switch.
- `call_settings` (`src/core/project.py`): a stored level the model does not list (a hand-edited
  file, or a list that shrank) gives the default; the route's settings come from it, so the page
  shows what the call uses. The file keeps the level until a set changes it.
- Page: settings errors go to `settingsError`, shown beside the note and cleared by a successful
  settings save; the task's `error` banner is no longer touched by settings.
- `GET …/model`: `available` comes from the same `call_settings()` reading as the rest.
- Tests:
  - `TestWeb::test_web_extraction_passes_effort_and_timeout` waited on a `running` key the
    status answer does not have (`{'status': 'running'}`), so the wait returned at once. Failing
    interleaving: the request thread returns from `POST …/extract` and reaches `call, =
    rec.calls` before the extraction thread has called `run_process`: zero calls, the unpacking
    fails (or, with a slower check, a test would look at calls of an unfinished extraction). It
    now waits for `status == 'completed'`.
  - `TestCopilotRefusal::test_web_task_pauses_with_nothing_stored` waited for `state ==
    'paused'`. `TaskManager._pause_for` sets `task.state = 'paused'` and only then puts the
    `backend_error` event: a test thread scheduled between the two reads no event and fails
    `len(events) == 1`. It now waits for the event itself (and then checks the state).
  - `test_failed_save_restores_effort_and_timeout` started at `high`, which Gemini takes, so it
    passed without the effort rollback. It starts at `xhigh` now: the attempted save sees the
    level reset (None) and the rollback restores `xhigh`.
  - Added: only what is sent changes (stale-tab case), an unlisted stored model changes its
    timeout (an effort for it is still refused), GET shows the fallback level, the fallback
    reaches the command line, page markup (only the changed setting sent, own error field,
    optgroups).
- `scripts/verify/phase10_effort_check.py`: the reported level is printed only when it is one of
  `KNOWN_LEVELS`, else `-` (Haiku 4.5 reports null).
- `src/web/tasks.py`: the engine comment names the call settings.
- The round-1 section of "Fix: export source path and `model` command" said `main` answered 400
  for every non-object body; corrected (400 for `"opus"`/`["opus"]`, 500 for `1`/`true`, checked
  in `main`'s code: `'model' in 1` raises `TypeError`).

Declined:

- An explicit `null` for `effort`/`timeout` in the PUT body means "not given" (as for `model`
  and `backend`); there is no "reset to default" through the API.
- `updated_at` changes on a load and save without edits: older than this branch.
- The invented first names in the script's invented paragraph are not personal data.
- The script fails when Copilot omits token accounting: a measurement without tokens is not one.

### Addition: the full Copilot model list

`copilot_cli.MODELS` lists the 20 chat models the owner named (no `auto`, coding-specific or
internal models), descriptions with the premium-request multiplier the account shows; default
still `claude-sonnet-5.5`.

Levels, found without model calls (the research's method): for each model the product's
`CopilotCLI.build_command` (its environment and working directory) was started with each of
`none, minimal, low, medium, high, xhigh, max` and with no value; the CLI validates the level
before any model call, and the process group was killed at the first of `user.message`,
`assistant.turn_start`, `model.call_start` or an error. A level counts as accepted when the run
reached that point without a `session.model_change` discarding it. 160 starts, 0 model calls.
Script kept outside the repository (it starts the CLI; it is a one-off probe).

Then one real call per model through the product's path (`TranslationEngine(max_calls=1)` with
`CopilotCLI`, which runs the `agent_replacement` check in `classify_error`), an invented
sentence, at the model's lowest level, timeout 180 s. 20 calls, no retries, no rate limit.

| model | multiplier | accepted levels | default (none passed) | lowest-level call | replacement check | reply | wall s |
|---|---|---|---|---|---|---|---|
| claude-sonnet-5.5 | 1x | low…max | medium | low | passed | translation | 5.21 |
| claude-sonnet-5 | 1x | low…max | medium | low | passed | translation | 4.94 |
| claude-opus-5.5 | 15x | low…max | medium | low | passed | translation | 5.05 |
| claude-haiku-5.5 | 1x | low…max | medium | low | passed | translation | 5.18 |
| claude-haiku-4.5 | 0.33x | none (only `none`, discarded) | null | (none passed) | passed | translation | 9.15 |
| gpt-6.2-sol | 1x | low…max | medium | low | passed | translation | 5.61 |
| gpt-6.2-astra | 1x | low…max | medium | low | passed | translation | 5.90 |
| gpt-6.1-sol | 1x | low…max | medium | low | passed | translation | 5.54 |
| gpt-6.1-terra | 1x | low…max | medium | low | passed | translation | 5.92 |
| gpt-6-sol | 1x | none, low…max | medium | none | passed | translation | 5.25 |
| gpt-6-luna | 1x | none, low…max | medium | none | passed | translation | 6.07 |
| gpt-6-astra | 1x | low…max | medium | low | passed | translation | 6.20 |
| gpt-5.6-sol | 1x | none, low…max | medium | none | passed | translation | 4.75 |
| gpt-5.6-luna | 1x | none, low…max | medium | none | passed | translation | 5.22 |
| gemini-4-carbon | 1x | low, medium, high | medium | low | passed | translation | 7.10 |
| gemini-4-argon | 1x | low, medium, high | medium | low | passed | translation | 7.20 |
| gemini-4-hydrogen | 1x | low, medium, high | medium | low | passed | translation | 7.38 |
| gemini-3.8-flash | 14x | low, medium, high | medium | low | passed | translation | 6.32 |
| grok-4.7 | 1x | low, medium, high, xhigh | high | low | passed | translation | 7.68 |
| grok-4.6 | 1x | low, medium, high, xhigh | medium | low | passed | translation | 6.48 |

`low…max` = `low, medium, high, xhigh, max`. `minimal` was refused by every model. For
`gpt-5.6-sol`/`-luna`, `none` is reported through a `session.model_change` from null to `none`
(not a discard), and the `none` call answered normally. No model was left out.

Consequences in the code: `KNOWN_LEVELS = ('none',) + EFFORT_LEVELS` (`src/llm/base.py`) is
what load accepts and what the script prints; the four GPT models declare `none`; Claude Code
models never do (its CLI knows no `none`). The web model selector groups options with
`<optgroup>` by backend and alias family (`claude`, `gpt`, `gemini`, `grok`; generic, from the
alias up to its first `-`); `model show` prints the aliases one line per family (one line for
the Claude backend, as before). Checked in Chrome on an invented project: 5 groups; a timeout
saved from a tab whose model selection was stale left the newer model stored; `gpt-6-sol`
offered `none` and saved it; a refused timeout appeared in the settings error, the task banner
stayed empty, and the next good save cleared it.

### Commands and results (review round 1)

```
$ scripts/verify/mutation.sh main                      # first run after the fixes
TOTAL mutants 670  killed 665  survived 5  timeout 0  suspicious 0  untested 0
    new survivor: translate._model_lines, the group key of aliases without a family '' -> 'XXXX'
    killed by TestSmallMessages::test_model_lines
$ scripts/verify/mutation.sh main                      # final
TOTAL mutants 670  killed 666  survived 4  timeout 0  suspicious 0  untested 0
$ python3 -m pytest tests/ -q -p no:cacheprovider -rfw     # three at once
2850 passed in 228.19s / 231.24s / 224.06s            (no failure, no warning in any)
$ python3 scripts/verify/phase1_reverts.py; echo "exit $?"
exit 0                      (reverts not detected: 0; 105 OK)
$ python3 scripts/verify/phase1_survivor_kills.py; echo "exit $?"
exit 0                      (not killed 0; 81 KILLED)
```

The 4 survivors are the equivalent `prompt_text` mutants justified above. Real model calls in
this round: the 20 listed in the table (one per model) and none else; level probes made no
model call.
