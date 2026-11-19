# Improvement plan: translation mechanism, output fidelity, and runtime

Version 2 — 2026-11-19. Supersedes version 1 of the same date.

Version 1 was reviewed by three models from different vendors (GPT, Claude Opus, Gemini) against
the code. Their findings were checked against the code before being adopted; section 9 records
what was adopted and what was rejected. Version 2 also adds the LLM backend interface and the
runtime control work, which were not in version 1.

Scope: everything that affects the exported EPUB or the cost and reliability of producing it.
UI work is limited to the minimum needed to show validation results.

## 1. Decisions in brief

1. Keep whole-segment HTML translation. Add attribute protection before the call and
   validation after it. Do not adopt two-stage fill (oomol) or line-by-line translation (AiNiee).
2. Segmentation and export both work on the raw XHTML inside the EPUB archive, parsed as XML.
   ebooklib is removed from both paths.
3. A segment is a contiguous range of blocks in one chapter, identified by index and by a
   content fingerprint. Segments never span non-adjacent blocks.
4. Export edits the source archive in place. Anything that fails verification is exported in
   the original language and reported; export never writes unverified model markup.
5. Every translation attempt returns one outcome object, handled identically by the web
   workers, both CLI modes, and the re-translate endpoint.
6. All model calls go through one backend interface. Only a Claude CLI backend is built now.
   The interface is shaped so that a Copilot CLI backend or an HTTP API backend is a new class,
   not a change to callers.
7. Phases were reordered: foundations first, then the block model, then export.

## 2. Current state

Both reference projects follow two principles this project does not:

- Never trust the structure of model output.
- Never rebuild the EPUB; change only the files that need changing.

This project is ahead on the review workflow, glossary management, paragraph-level translation,
and Taiwan-specific style rules. Those stay.

Measurements on the 8 existing projects:

| Finding | Number |
|---|---|
| Chapter stylesheet links surviving export | 0 in every exported book |
| Bilingual chapters linking `bilingual.css` | 0 |
| Segments whose top-level element count differs from source | 55 of 4156 (1.3%) |
| `<em>` tags lost in translation | 76 |
| Segments with a changed set of `id` attributes | about 19 |
| Code segments with no comment marker (project-3) | 1337 of 1429 |
| Text segments under 30 words (project-3) | 1858 of 2956, median 16 words |
| Chapter files that are well-formed XML under a strict parser | 2170 of 2170 |
| Chapter files using HTML named entities | 0 |
| Chapter files with SVG `viewBox` | 3 |

Facts about the current pipeline that version 1 missed (verified in the installed ebooklib 0.20
and in the code):

- `item.get_content()` does not return the file. ebooklib re-parses the chapter, rebuilds
  `<head>`, and re-serializes. The extractor then re-serializes the body children and the
  segmenter parses that a third time. Segments are therefore computed on a normalized document,
  not on the bytes in the archive.
- `html.parser` and lxml's HTML mode lowercase attribute names, so SVG `viewBox` becomes
  `viewbox` in any chapter the builder rewrites.
- HTML comments are `NavigableString` instances. A comment directly under `<body>` becomes a
  translatable block, and a comment inside a wrapper `<div>` makes `_is_container()` false.
- The CLI `init` command stores the path of the external EPUB rather than a project-owned copy
  (`translate.py:80`).
- The sequential CLI exits on the first failed segment (`translate.py:169-172`).
- The update and approve endpoints accept any text (`review.py:61-88`), and the builder exports
  any non-empty translation regardless of status.

## 3. Goals, non-goals, constraints

**Goals**

- An exported EPUB keeps everything from the source except the translated text.
- No model output reaches the EPUB without passing structural validation at export time.
- Every failed or doubtful segment is visible in the review tab with a typed reason.
- Fewer wasted model calls, and unattended recovery from rate limits.
- Adding another CLI or an API as a model provider requires implementing one interface.

**Constraints**

- Model access is through subscription CLIs (Claude Code now, GitHub Copilot CLI next).
  Per-token API use is not planned, but the interface must not preclude it.

**Non-goals**

- Two-stage "translate plain text, then fill an XML template".
- Line-by-line numbered translation.
- Segments that span non-adjacent blocks (prose merged across a code block).
- Multi-language support.

## 4. Target design

### 4.1 Translation mechanism

A segment is still sent as HTML and translated in one call. Three things change.

**Protection.** The model sees a tag skeleton with short numeric keys instead of attributes.

```html
source   <p class="calibre3" id="p12">See <a href="ch02.xhtml#fn3" id="r3"><sup>3</sup></a>
         for the <em class="italic">core idea</em>.</p>

prompt   <p k="1">See <a k="2"><sup>3</sup></a> for the <em k="3">core idea</em>.</p>
```

- Every top-level block gets a key, with or without attributes, so block order can be checked.
- Every other tag that has attributes gets a key. Tags without attributes are sent bare.
- Keys are *required* for top-level blocks and for tags carrying `id`, `href`, `src`, `name`,
  or `epub:type`. Keys are *optional* for inline tags whose attributes are only `class`,
  `style`, or `lang`.
- Protection and restoration operate on tokens of the stored string. They do not parse and
  re-serialize, so `restore(protect(html)) == html` holds exactly.
- A block that is a run of bare inline content is wrapped in a neutral keyed element for the
  prompt and unwrapped on restore. This removes the current behaviour where the HTML prompt
  forces the model to invent a `<p>` around bare text.

**Validation.** Checks are split by consequence.

| Class | Rules | On failure |
|---|---|---|
| Structural (blocking) | Output is well-formed. The sequence of top-level keys equals the source's. Each required key appears exactly once, on the same tag name. No optional key appears twice. No attribute other than `k`. No tag name absent from the source segment. | Retry; if still failing, the segment is `failed` and is exported in the original language. |
| Quality (warning) | Identical to source although the source has translatable words. Length ratio outside the default band. Optional key dropped. Glossary term not applied. | Stored as translated, with a typed issue shown in review. |

Quality rules never block, because legitimate exceptions are common: headings that are only a
name, formulas, acronyms, URLs.

**Retry.** One retry with the specific error as feedback, sent as a follow-up message. If that
fails and the segment has two or more top-level blocks, split the request once at a block
boundary, translate and validate each half, and join them. The split exists only inside the
call; the stored segment, its ID, and its anchor are unchanged. Total model calls per segment
are capped at four. Rate-limit and authentication errors are not structural failures and do not
consume this budget.

**Code segments.** Code is plain text, not HTML. A code block with no comment marker is marked
`passthrough`: no model call, and the exporter leaves its DOM untouched in every format. A
translated code block is accepted only if it equals the source after comment lines are removed
from both.

### 4.2 Document and block model

- **Source of truth.** Each project owns an immutable copy of the EPUB and stores its SHA-256.
  Export verifies the hash.
- **Package reading.** Resolve `META-INF/container.xml`, then the OPF, then manifest hrefs
  (URL-decoded, relative to the OPF directory), then the spine with duplicates removed.
  The navigation document (`properties="nav"`) and the NCX are never segmented.
- **Parser.** `lxml.etree` in XML mode for both segmentation and export. A chapter that does
  not parse is not segmented, is copied unchanged on export, and is reported. All 2170 existing
  chapters parse; a lenient fallback will be added only if a real book needs it.
- **Blocks.** Walk `<body>`, ignoring comments and processing instructions. A `div`, `section`
  or `article` is descended into when it has only block children (existing rule) *or* when it
  has direct text mixed with block children or `<br>`. Inside a descended element, a block is
  either one block-level child that is not descended into, or one maximal run of inline content
  (text and inline elements) between block children and `<br>` elements. Each block has an
  index within its chapter and a fingerprint (hash of its whitespace-normalized text).
  The `<br>` rule is required, not optional: without it one `div` of bare text and `<br>` is a
  single block of up to 11,509 words (project-2, 22 such blocks) or 16,179 words
  (project-7). With it the largest block in the 8 projects is 7,899 characters.
  Lists stay single blocks (largest: 918 words).
- **Segments.** A segment covers a contiguous half-open range of blocks in one chapter and
  stores the fingerprints of those blocks. A block is never divided between segments. Segment
  HTML is the exact serialization of its blocks.
- **Schema version.** `project.json` gains `schema: 2`. Schema 1 projects keep working for
  translation and review; export of a schema 1 project requires migration.
- **Migration.** An explicit one-time command, not something `load()` does (the home page calls
  `load()` for every project). It refuses to run while a task is active. For each legacy segment
  it finds the blocks whose normalized text matches, in order. It supports one segment covering
  several blocks and several segments inside one block (their translations are joined in order).
  Segment IDs and translations are preserved. Chapters that cannot be matched are listed in a
  report and are exported in the original language; there is no fallback to count-based mapping.
- **Migration rules found by the dry run** (2026-11-19, read-only prototype over the 8 projects,
  whitespace-insensitive text comparison):
  - Match on the text of the segment's stored `html`, not on its `text` field. In 14 legacy
    segments the two fields disagree (an older segmenter split `text` and `html` at different
    points); `html` is what was sent to the model. Matching on `text` fails 2 chapters.
  - A comment that is a direct child of a descended element may or may not be present in the
    legacy text (see Bug 5); try both. Without this rule 2 chapters fail.
  - When legacy segment boundaries fall inside a block, merge the smallest group of segments
    that starts and ends on block boundaries into one segment: translations joined in order,
    first segment ID kept, status is the lowest of the group. This replaces the "several
    segments inside one block" rule and also covers groups spanning several blocks.
  - Result with these rules: 2155 of 2155 chapters and 9521 of 9521 segments match.
    9462 segments map to whole blocks; 59 segments in 29 groups need merging
    (project-2 26, project-7 31, project-5 2).
    No chapter falls back to original-language export.
  - The prototype compared text only. The real migration must also confirm that each stored
    translation still passes structural validation against the new block HTML.

### 4.3 Segment state and outcome

```python
class SegmentStatus:  PENDING, TRANSLATED, REVIEWED, FAILED

@dataclass
class Issue:
    kind: str        # structure | quality | glossary | backend
    code: str        # e.g. missing_key, length_ratio, term_not_applied, refusal
    detail: str
    blocking: bool

@dataclass
class TranslationOutcome:
    text: str | None          # accepted translation, or None
    status: SegmentStatus
    issues: list[Issue]
    model: str
    usage: Usage | None
```

- `FAILED` segments are not queued by a normal start. They are retried only on request.
- If a re-translation of a `TRANSLATED` or `REVIEWED` segment fails, the previous text and
  status are kept and only the issues are attached.
- Each segment has a `revision` counter. Manual edits increment it. A worker result is applied
  only if the revision is unchanged since the segment was dispatched.
- Validation runs in three places: after a model call, when text is saved or approved through
  the review endpoints, and at export. Export does not trust stored issues.
- One writer per project at a time, enforced by a lock file shared by the web server and the
  CLI. Temporary file names are unique per process.
- One function, `translate_segment()`, builds the request and returns the outcome. The web
  worker, the sequential CLI, the parallel CLI, and the re-translate endpoint all call it and
  persist the outcome the same way. No path aborts the run because one segment failed.

### 4.4 LLM backend interface

Designed around what an HTTP API can do; CLI backends degrade where they cannot.

```python
@dataclass
class LLMRequest:
    system: str                       # stable instructions; backend may cache
    messages: list[Message]           # one message, or three for retry-with-feedback
    model: str
    timeout: float
    temperature: float | None = None  # hints; ignored if unsupported
    max_output_tokens: int | None = None
    effort: str | None = None

@dataclass
class LLMResponse:
    text: str
    stop_reason: str                  # complete | truncated | refusal
    usage: Usage | None               # None when the backend cannot report it
    model: str

@dataclass
class Capabilities:
    separate_system: bool
    multi_turn: bool
    reports_usage: bool
    sampling_params: bool
    suggested_concurrency: int

class LLMBackend(Protocol):
    name: str
    capabilities: Capabilities
    def list_models(self) -> list[ModelInfo]: ...
    def complete(self, request: LLMRequest, cancel: threading.Event) -> LLMResponse: ...
```

- Errors are raised as `RateLimitError(retry_after: float | None)`, `AuthError`,
  `TransientError`, or `RefusalError`. Each backend classifies its own failure output.
- Rate limiting belongs to the backend instance. Workers call `complete()`; the backend decides
  whether to queue.
- `CLIBackend` is a base class that owns process start, timeout, termination on cancel,
  environment cleaning, and the working directory. A concrete CLI backend supplies
  `build_command`, `parse_output`, `classify_error`, and `list_models`. When a CLI cannot take a
  separate system prompt or multiple turns, the base class flattens them into one prompt.
- `project.json` stores the backend name together with the model. Existing projects default to
  the Claude CLI backend.
- API credentials, when an API backend exists, come from environment variables only.
- A fake backend is used in tests to confirm that callers do not depend on CLI details.

```
LLMBackend
├── CLIBackend
│   ├── ClaudeCLI        (Phase 1)
│   └── CopilotCLI       (Phase 7)
└── APIBackend           (not scheduled; interface reserved)
```

### 4.5 Export

- Open the project's source archive. Write `mimetype` first and uncompressed. Write each
  member exactly once.
- Members that may differ from the source: chapters containing an accepted translation, the
  OPF, the NCX, the navigation document, and one added stylesheet for bilingual formats.
  Every other member is copied with identical uncompressed bytes.
- For each chapter: verify block count and fingerprints; then, per segment, restore and
  validate the translation and fill it into the original elements. Attributes always come from
  the source element.
- **Bilingual identity.** The original element keeps its `id` and `name`. The translated copy
  has them removed. Each translated copy is marked `lang="zh-TW"`.
- **Bilingual stylesheet.** Added under a name that does not collide with an existing member,
  registered in the OPF manifest, and linked from each transformed chapter with an href computed
  relative to that chapter.
- **TOC.** For each NCX and navigation entry, resolve its target relative to the file that
  contains the entry, locate the target element (or the first heading of the file), find the
  block that contains it, and take the label from the translated heading element in that block.
  If any step fails the label is left unchanged.
- **Language.** `chinese-only`: set `dc:language` and the root `lang` / `xml:lang` to `zh-TW`,
  and rewrite explicit source-language `lang` attributes on transformed elements. Bilingual:
  leave the package and root language as the source language.
- **Failure handling.** A chapter or segment that fails verification is exported in the
  original language. Export returns a report (per chapter: translated, kept original, reason).
  One bad chapter never fails the whole export.

## 5. Phases

**Acceptance rule for every phase.** A phase is done only when its acceptance checks can be run
by the user with one command each and the result is unambiguous (exit code or a stated number).
Each phase therefore delivers, together with the code:

1. automated tests in `tests/`, run with `python3 -m pytest tests/ -v`, with no existing test
   removed without a stated reason;
2. where the claim is about the 8 real books, a read-only check script under `scripts/verify/`
   that prints a pass/fail line per project and exits non-zero on failure. `projects/` is not
   in git, so these scripts run on the user's machine and are not part of the test suite;
3. a short entry in `plan/03-acceptance-log.md`: the commands, their output, the date, and
   anything that was not verified;
4. a mutation-test run over the code changed in the phase, reproducible with one command
   (`scripts/verify/mutation.sh`), with every surviving mutant in changed code either killed
   by a new test or justified individually as equivalent in the acceptance log.

Numbers quoted in this plan must be reproducible the same way. The migration dry-run figures in
4.2 come from `python3 plan/experiments/migrate_dryrun.py --strict` (expected last line:
`TOTAL chapters 2155/2155  segments 9521/9521  on-boundary 9462  to-merge 59 in 29 groups
largest block 7899 chars`, exit code 0).

### Phase 0 — Standalone fixes

No schema change; each item ships alone.

- Bug 3: stop markdown cleanup and leading-whitespace stripping on code output.
- Bug 4: take the *end* of the previous segment as context, not the start; take context only
  from the same chapter; skip code neighbours.
- Glossary matching with `(?<!\w)…(?!\w)` lookarounds (`\b` is not used because it fails on
  `C++`, `C#`, `.NET`). A term ending in a letter also matches its plurals (`s`, `es`,
  `y → ies`); if its last word has at least 4 letters it also matches `ed`/`d`, `ing`
  (with e-drop and a doubled final consonant), `er`/`ers` and `ment`/`ments`, because the
  extractor stores base forms. All-caps terms match case-sensitively (`IT` does not match
  "its", `US` does not match "uses"); other terms are case-insensitive. Whitespace inside a
  multi-word term matches any whitespace run (newline, NBSP). `.NET` does not match inside
  `ASP.NET` (accepted). One compiled, cached pattern per term.
- Web term extraction covers all text segments, not the first 50 segments including code
  (`glossary.py:113`).
- Bug 5: the segmenter skips comments.

Acceptance (one unit test per item, each failing on the current code and passing after):

- Code cleanup: a sample with `# comment`, `__init__`, `*args, **kwargs` and an indented first
  line comes back unchanged.
- Context: with a previous segment longer than 200 characters the prompt contains its tail and
  not its head; a neighbour from another chapter or a code segment is not used.
- Glossary: `AI` does not match inside `maintain`; `API` matches `APIs`; `C++` and `.NET` match.
- Web extraction: with 60 text segments and a term only in the last one, the term is sent.
- Comments: a body with `<!-- note -->` between two paragraphs yields no segment containing
  `note`.
- Check script: lists the already-translated code segments whose translation differs from the
  source outside comment lines (the candidates for re-translation), per project.

### Phase 1 — Foundations

- Backend interface, `CLIBackend`, `ClaudeCLI`, fake backend (4.4).
- `translate_segment()` and `TranslationOutcome`; `FAILED` status; typed issues; `revision`;
  project lock file (4.3).
- Term extraction goes through the backend, with the project's model and a clean environment.
- For failed segments, keep the raw request and response on disk for diagnosis.
- Record token usage when the backend reports it.

Acceptance:

- `grep -rn "subprocess" src/ translate.py` finds matches only in the backend module.
- Tests with the fake backend: a failing segment in the sequential CLI path and in the parallel
  path is recorded as `FAILED` and the remaining segments are translated; a result for a segment
  edited mid-flight is dropped; a second process cannot take the project lock.
- One real call: translating a single segment of a test project through `ClaudeCLI` succeeds and
  the stored segment records the model. This is a manual check, logged with its output.

### Phase 2 — Document and block model

- Raw package reader and XML block walker (4.2). ebooklib removed from extraction.
- Project-owned source copy and hash for projects created by the CLI.
- Segmenter produces schema 2 segments with anchors and fingerprints. Sentence-splitting of
  bare text is removed.
- Migration command with report.

Acceptance:

- Check script over the 8 projects: every chapter, parsed and re-serialized without edits, is
  equal to the source under XML canonicalization (2170 of 2170).
- Migration run on *copies* of the 8 projects: the report shows 2155 of 2155 chapters and 9521
  of 9521 segments matched, 29 merge groups, as predicted by the dry run. After migration,
  every translated segment's text is still present (total characters of translated text per
  project unchanged) and each project loads in the web UI.
- Tests on raw-ZIP fixtures: bare text with `<br>`, long bare text, a body-level comment, and
  a chapter that fails to match (reported, left unmigrated, nothing written).
- Re-segmenting a source twice gives identical block indexes and fingerprints.

### Phase 3 — ZIP export

Depends on Phase 2. Implements 4.5. Until Phase 4 lands, the export gate uses a reduced
structural check (well-formed, same number of top-level blocks); the wholesale-replace path in
the current builder is removed either way.

Acceptance:

- Stylesheet links and `<style>` blocks in every chapter head equal the source's.
- Members outside the allowed set are byte-identical to the source.
- No duplicate `id` in any chapter; every internal fragment link resolves.
- Every bilingual chapter that uses a bilingual class links a stylesheet that exists.
- SVG attribute case is preserved.
- `epubcheck`, where installed, reports no error that the source does not also have.

All of the above are one check script run on the exports of the 8 projects in all three formats
(24 files), plus the same assertions as tests on the raw-ZIP fixtures. In addition one manual
check, logged: open one export per format in a reader application and confirm that styling, the
side-by-side layout, the table of contents and a footnote link work.

Fixes Bugs 1 and 2.

### Phase 4 — Protection, validation, retry

Depends on Phases 1 and 2. Implements 4.1. Replaces the reduced export check with the full one.

Acceptance:

- `restore(protect(html)) == html` for every stored segment.
- Running the validator over the 4156 existing translations flags the known count mismatches
  and required-attribute losses; the length-ratio band is tuned on the same data so that it
  flags a small, reviewable fraction.
- A reply that is a refusal or is not HTML is recorded as a backend issue, not stored as text.

### Phase 5 — Glossary compliance and review surfacing

Depends on Phase 4.

- Compliance check as a quality issue. `work`-category terms are checked only where the prompt
  rule applies; parenthetical originals are ignored when comparing.
- Targeted fix: send source, current translation, and one issue; re-validate the result.
- Minimal review changes: an issue badge per segment, a filter for segments with issues, and
  search that includes `FAILED` segments. Validation result returned by update and approve.

Acceptance:

- Tests: a translation that renders a glossary term differently gets a compliance issue; the
  same translation with the glossary rendering gets none; a targeted fix whose result fails
  structural validation is rejected and the previous translation is kept.
- Check script: per project, the number of translated segments with a compliance issue, and a
  sample of 20 listed by segment ID for the user to judge. Target agreed after the first run:
  the user marks how many of the 20 are real problems; the check is kept only if most are.
- Web test: the issue filter returns exactly the segments that have issues; search returns
  `FAILED` segments.

### Phase 6 — Segmentation for technical books

Depends on Phases 3 and 4. New projects only.

- Code without a comment marker is `passthrough`.
- A heading is merged into the following text segment. This relies on the element-based TOC
  labelling from Phase 3.
- Code blocks remain hard segment boundaries.
- Context: previous segment's translation when available, tags stripped, last 200 characters;
  otherwise source text.

Acceptance:

- Check script: segmenting project-3 with the new rules needs fewer than half of the
  4385 calls the current segmentation needs (count of non-passthrough segments).
- Tests: a code block without a comment marker is exported byte-identical to the source in all
  three formats and appears once in bilingual output; a merged heading still produces the right
  TOC label.

### Phase 7 — Runtime control, speed, second backend

Depends on Phase 1.

- Stop and pause terminate the in-flight process instead of waiting for it.
- Failed attempts are classified. Transient errors are re-queued with backoff. A rate limit
  pauses and resumes automatically, using `retry_after` when the backend provides it.
- Concurrency adapts: grow slowly while calls succeed, halve after consecutive failures.
- Saves are debounced; `project.json` is 3.7–7.2 MB and is currently rewritten per result batch.
- Prompt reordered so that stable content (rules, book brief) comes first.
- Measure the per-call overhead of the Claude CLI with `--system-prompt`, `--tools ""`,
  `--strict-mcp-config`, `--setting-sources`, `--no-session-persistence`; adopt what helps.
- `CopilotCLI` backend. The installed CLI (1.0.105) has `-p`, `--model`, `--silent`,
  `--output-format`, `--no-custom-instructions`, `--available-tools`; its output and error
  formats still need to be characterized.
- Optional failover: when one backend is rate-limited, dispatch to another. The review tab
  already shows which model produced each segment.

Acceptance:

- Tests with the fake backend: stop returns within 2 seconds while a call is in flight and no
  child process remains; a simulated rate limit pauses and resumes without user action; a
  transient error is retried and a permanent one is not; concurrency falls after consecutive
  failures and rises again; 100 results cause far fewer than 100 writes of `project.json`.
- Measurement, logged with raw numbers: seconds per segment and segments per minute on the same
  50 segments of one book, before and after, same model and worker count. Each CLI flag is
  adopted only if its measured effect is shown.
- `CopilotCLI`: the same single-segment real call as in Phase 1 succeeds, and the backend's
  contract tests (shared by every backend) pass.

### Phase 8 — Extraction quality and book-level context

- Term extraction second pass: group candidates with all proposed translations and occurrence
  counts, one consolidating call.
- Per-book brief (genre, tone, audience) at the top of the prompt.
- Optional `gender` and `note` on person entries, for consistent pronouns in fiction.

Acceptance:

- Tests: two candidates for one term with different translations are consolidated into one
  entry; the brief and the person notes appear in the prompt.
- Comparison, logged: extraction on one existing book before and after, reporting the number of
  terms, the number of terms with conflicting translations, and the two term lists as files for
  the user to compare. Translation quality itself is judged by the user on a fixed sample of 10
  segments translated with and without the brief; this part is not automatable.

## 6. Order

```
Phase 0 ── independent
Phase 1 ─┬─> Phase 7
         └─┐
Phase 2 ───┼─> Phase 3 ─┐
           └─> Phase 4 ─┼─> Phase 5
                        └─> Phase 6
Phase 8 ── after Phase 1
```

## 7. Tests

Existing fixtures are built with ebooklib, which itself strips chapter heads, so they cannot
show whether a head survived. New fixtures are written as raw ZIP archives and cover: an
`OEBPS/` folder, chapters in a subfolder, a percent-encoded href, a head with `<link>` and
`<style>`, NCX plus a navigation document that is also in the spine, inline SVG with `viewBox`,
text directly under `<body>`, `<br>`-separated runs, a comment under `<body>`, and a duplicated
spine entry. Assertions read output members directly from the ZIP.

Existing tests whose expectations change: the helper that reads chapters back through ebooklib
(`tests/test_builder.py:85`), the test that accepts mismatched element counts, the metadata test
that expects `en` for `chinese-only`, and (in Phase 6) the tests that require standalone
headings.

New tests also cover: migration, an edit made while a segment is in flight, both CLI modes on a
failing segment, and export of a project with failed segments.

## 8. Former open questions

| Question | Decision |
|---|---|
| Parser | `lxml.etree`, XML mode, for walker and writer. Unparseable chapters pass through unchanged and are reported. |
| What to protect | Every tag with attributes gets a key; top-level blocks always. Required versus optional as in 4.1. |
| Split on failure | Yes, once, inside the call only. No new segments or anchors. |
| Language for bilingual | Package and root stay in the source language; translated copies carry `lang="zh-TW"`. |
| Thresholds | Structural rules fixed in code. Quality heuristics have defaults, optional per-project overrides, and never block. |

## 9. Review record

Reviewers: `gpt-6-astra`, `claude-opus-5.5`, `gemini-4-argon`, same brief, read-only.

Adopted, raised by two or more reviewers:

- Prose merged across code cannot be a contiguous range → dropped.
- Restoring `id` duplicates it in bilingual output → identity policy in 4.5.
- Merging headings breaks segment-based TOC labels → element-based labels, in Phase 3.
- Count-based fallback in migration reintroduces the bug → fail closed, report.
- ZIP export needs its own package and relative-path resolution → 4.2 and 4.5.
- Failures stored as `pending` loop forever; CLI aborts on first failure → `FAILED` status and
  shared outcome handling.
- "Identical to source" and length ratio reject valid output → warnings only.
- Marking skipped code as translated makes the builder rewrite it → `passthrough`.
- Old translations and manual edits bypass validation → validate on save and at export.
- Acceptance criteria contradicted the design; tests encode old behaviour → sections 5 and 7.
- Split-on-retry must not create segments → inside the call.

Adopted, raised by one reviewer and verified:

- ebooklib normalizes content before segmentation, so one parser is not enough (Opus).
- Multiset checks do not detect swapped blocks → ordered keys on top-level blocks (GPT).
- Write races between workers and edits; shared temporary file name (GPT).
- Runs of inline content should be one block; round-trip must be token-level (Opus).
- Context is taken from the wrong end of the previous segment (Gemini, Opus) → Bug 4.
- Bare text is forced into a `<p>` by the HTML prompt (Gemini).
- Web term extraction reads only the first 50 segments (Gemini).
- `\b` fails on `C++`, `C#`, `.NET` (Gemini, Opus).
- Navigation document must not be segmented (Gemini, Opus). No existing project is affected.
- `load()` runs on every home-page request, so migration cannot live there (Gemini, Opus).

Rejected:

- "BeautifulSoup emits unclosed void tags" (Gemini). Tested: both parsers emit `<br/>` and
  `<link .../>`. The parser decision rests on attribute-case and namespace handling instead.
- A wrong line reference for the language assertion in `tests/test_builder.py` (Gemini). The
  assertion exists in `test_metadata_preserved`.
- Setting the package language to `zh-TW` for bilingual output (Gemini). Two reviewers
  preferred per-element language; adopted that.
- Lenient HTML fallback for unparseable chapters (Opus, Gemini). Deferred: no existing chapter
  needs it, and silent recovery was the objection of the third reviewer.

## 10. Not scheduled

Features present in the reference projects that may be worth adding later: translating book
metadata (title), a do-not-translate list, pre- and post-translation replacement rules, a
polishing pass, bilingual order (translation first or original first), MathML protection, and
target languages other than Traditional Chinese.
