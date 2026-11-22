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
- What a person reads is translated; what a machine reads is not. Class names, ids, links and
  every other attribute stay hidden behind the key. `alt`, `title` and `aria-label` are read
  by people: they are shown next to the key and translated
  (`<img src="m.png" alt="A map"/>` becomes `<img k="4" alt="A map"/>`). On restore the reply's
  value replaces the value inside the stored attribute text, escaped for its original quote;
  everything else of the stored attribute text is put back untouched. An element with only such
  attributes still gets a key.
- Inline tags are preserved: every key is required, and every tag without a key must occur as
  often per tag name as in the source. Only the order of inline tags may change (Chinese word
  order moves them).
- Protection and restoration operate on tokens of the stored string. They do not parse and
  re-serialize, so `restore(protect(html)) == html` holds exactly.
- A block that is a run of bare inline content is wrapped in a neutral keyed element for the
  prompt and unwrapped on restore. This removes the current behaviour where the HTML prompt
  forces the model to invent a `<p>` around bare text.

**Validation.** Checks are split by consequence.

| Class | Rules | On failure |
|---|---|---|
| Structural (blocking) | Output is well-formed. The sequence of top-level keys equals the source's, with no text between them. Each key appears exactly once, on the same tag name, in the same top-level unit; the run wrapper only at the top level. For tags without a key, the count per tag name in each block equals the source's. No attribute other than `k` and the key's translatable attributes; those are all present, and one empty in the source stays empty (their values are free). No tag name absent from the source segment. | Retry; if still failing, the segment is `failed` and is exported in the original language. |
| Quality (warning) | Identical to source although the source has translatable words. Length ratio outside the default band. Glossary term not applied. | Stored as translated, with a typed issue shown in review. |

Quality rules never block, because legitimate exceptions are common: headings that are only a
name, formulas, acronyms, URLs.

A model reply is first checked in its keyed form: every key once on its tag and inside the
same top-level block or run as in the source, the top-level keys in order, the wrapper only at
the top level, no other attribute, a translatable value blank exactly when the source's is.
Restoring cannot see these violations (two blocks with equal attributes swapped, a key used
twice, a missing key replaced by a bare tag of the same attributes, inline keys of equal
attributes exchanged between blocks), so they are checked before it.
Then the same comparison checks a model reply, a text saved or approved in the review, and a
stored translation at export. A stored translation has real attributes instead of keys, so this
check works on the restored form: source and translation are parsed and compared block by
block. The block element itself must keep its tag and attributes; the elements inside it, each
taken as its tag name plus its attributes, must form the same multiset. The values of
translatable attributes are free (an empty one stays empty). That is the key rules above in
restored form. A source that cannot be cut into its blocks fails at once, without a model call.

**Retry.** One retry with the specific error as feedback, sent as a follow-up message. If that
fails and the segment has two or more top-level blocks, split the request once at a block
boundary, translate and validate each half, and join them. The split exists only inside the
call; the stored segment, its ID, and its anchor are unchanged. Total model calls per segment
are capped at four. Rate-limit and authentication errors are not structural failures and do not
consume this budget.

**Code segments.** Code is plain text, not HTML. A code block with no comment marker is marked
`passthrough` (new projects, Phase 6: a segment flag, stored translated with its own code):
no model call, and the exporter leaves its DOM untouched in every format. A
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

No schema change apart from one optional `project.json` field (`segmenter_version`); each
item ships alone.

- Bug 3: stop markdown cleanup and leading-whitespace stripping on code output.
- Bug 4: take the *end* of the previous segment as context, not the start; take context only
  from the same chapter; skip code neighbours.
- Glossary terms in the prompt: when the glossary has at most 150 entries
  (`FULL_GLOSSARY_MAX_TERMS`) every entry goes into every text prompt, with no matching (the
  8 real glossaries hold 0 to 6 entries). Above that, an entry is sent when the text contains
  the term starting at a word boundary, with any continuation: `(?<!\w)` + the escaped term
  (internal whitespace as `\s+`), case-insensitive, no trailing boundary. Accepted: other
  words that start with the term also match (`Go` → "going", `C` in "C++"), `.NET` does not
  match inside "ASP.NET", and a term stored in plural form does not match its singular; a
  false positive only adds a few prompt tokens.
- Web term extraction covers all text segments, not the first 50 segments including code
  (`glossary.py:113`).
- Bug 5: the segmenter skips comments. New projects record `segmenter_version: 2` in
  `project.json`; the builder keeps `main`'s walk for projects without it, and saving such a
  project does not add the field. Projects that existed before this phase remain loadable by
  pre-Phase-0 code (tag `v1.0.0`); projects created after it are not, which matters only for a
  rollback.

Acceptance (one unit test per item, each failing on the current code and passing after):

- Code cleanup: a sample with `# comment`, `__init__`, `*args, **kwargs` and an indented first
  line comes back unchanged.
- Context: with a previous segment longer than 200 characters the prompt contains its tail and
  not its head; a neighbour from another chapter or a code segment is not used.
- Glossary: with at most 150 entries the prompt gets all of them; above that, `AI` does not
  match inside `maintain`, `API` matches `APIs`, `render` matches `rendering`, and `C++`,
  `C#` and `.NET` match.
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

As built (2026-11-19), where it differs from 4.3/4.4:

- `project.json` does not store a backend name yet: only `ClaudeCLI` exists; the field comes
  with the second backend (Phase 7).
- `ClaudeCLI` keeps plain-text output (`claude -p <prompt> --model <alias>`), which reports no
  usage, so usage is recorded as absent. `--output-format json` is left to the Phase 7
  measurements.
- The project lock is an exclusive `flock` on `<project>/project.lock`, taken by every save,
  for the whole CLI `start` run, and for the whole web task. The kernel drops it when the
  holder dies, so a stale lock never blocks. Not available on Windows.
- `FAILED` segments are retried with `start --retry-failed` (CLI) or `retry_failed: true` on
  the web start API (a "retry failed segments" button on the translation tab).
- Failure diagnostics: `log/failed/<segment id>-<digest>.txt`, one file per segment, replaced by each
  failed run and removed when the segment later succeeds.

Acceptance:

- `python3 translate.py --version` prints a line containing `1.0.0`.
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
- The schema-2 migration supersedes Phase 0's `segmenter_version` marker in `project.json`
  (1 or absent: comments counted by the builder walk; 2: comments skipped).

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
Inline tag counts are blocking (4.1): an existing translation that dropped or added an inline
tag fails the check and is exported in the original language until it is translated again.
`translate.py validate <project>` reports the existing translations that fail, per rule, and
with `--mark-failed` sets them to `failed` (their text kept) so that `start --retry-failed`
translates them again.

Acceptance:

- `restore(protect(html)) == html` for every stored segment.
- Running the validator over the 4162 existing translations reports, per rule, how many
  dropped or added tags and attributes; the length-ratio band is tuned on the same data so that
  it flags a small, reviewable fraction.
- A reply that is a refusal or is not HTML is recorded as a backend issue, not stored as text.

### Phase 5 — Glossary compliance and review surfacing

Depends on Phase 4.

Split in two parts, so that the check is measured on real data before more machinery is
built around it.

Part A:

- Compliance check as a quality issue. `work`-category terms are checked only where the prompt
  rule applies; parenthetical originals are ignored when comparing.
- Minimal review changes: an issue badge per segment, a filter for segments with issues, and
  search that includes `FAILED` segments. Validation result returned by update and approve.
- Check script with a sample of 20 issues for the user to judge.

Part B, decided only after the user has judged the sample of part A:

- Targeted fix: send source, current translation, and one issue; re-validate the result.
  Built only if the judged sample shows that most issues are real problems and that a fix
  is worth a model call; otherwise dropped or replaced by what the sample suggests.

As built (part A, 2026-11-21):

- One `glossary`/`term_not_applied` warning per term: an entry the prompt gives
  (`terms_for_prompt`) that occurs in the segment's source text (the prompt's matching,
  `term_pattern`) and whose translation is not a substring of the translation. Both sides are
  compared without tags, entities decoded, NFKC, one kind of middle dot, without whitespace,
  case-folded, and without parentheticals that hold no Chinese character (a parenthetical
  original). Entries with an empty translation are skipped; code segments are not checked.
- The prompt applies a `work` translation only where the term is the title of a work; the
  check takes "written in the source with the glossary's capitals" as that condition.
- It runs where the other quality warnings run (after a model call, at review save and
  approve, `translate.py validate`), only on a translation that passes the structural check.
  Approve clears the stored issues, as before; its response still lists them. Warnings are
  stored with the segment; existing translations get them only when saved or translated again
  (`validate` and the check script report them without storing).

Acceptance:

- Tests: a translation that renders a glossary term differently gets a compliance issue; the
  same translation with the glossary rendering gets none; (part B, if built) a targeted fix
  whose result fails structural validation is rejected and the previous translation is kept.
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

As built (2026-11-21), where it differs from or adds to the above:

- The rules are numbered. `project.json` gets `segmentation: 2` for projects created now;
  projects without the field are rules 1 and are saved without it (their stored segments are
  never re-cut); an explicit value is kept as it is. Only the integers 1 and 2 are accepted
  (load, create, `Segmenter(rules=...)`, default 2); anything else is a `ValueError`.
- Headings (rules 2): a heading block (`h1`–`h6`) ends the segment being built, unless that
  segment holds only headings, and starts a new one. The first text block after a heading
  always joins it, even when the two together pass `MAX_WORDS`; later blocks follow the usual
  size rules. Consecutive headings share a segment. A code block, a table or the end of the
  chapter after a heading leaves the heading (or the run of headings) as a segment of its own.
  Tables are still segments of their own.
- Passthrough: a code segment with no comment line (`validation.has_comment_line`, the line rule
  of the code check; a trailing comment, `#include` or a first non-blank line that starts with
  `#!` after its indentation (a shebang) is no comment line, and the code check keeps that
  line unchanged, in every project) gets the segment flag
  `passthrough` and is created `TRANSLATED` with its own code as text and no model. No new
  status: it is never queued (also not by an explicit list of segment IDs), counts as done in
  the progress numbers (which also report it as `passthrough`), and is skipped by `validate`
  and by the export, which leaves its chapter untouched (a chapter holding only passthrough
  code is copied byte for byte). The review page shows it as `passthrough`, without editor;
  save, approve and re-translate answer 409, and the project manager itself refuses to change
  it. Search marks it and does not select it for re-translation. It is not a translation for
  the "nothing translated yet" export guards, the translated word count, the project list
  status or the export report's untranslated count.
- Context (rules 2 only): the text before is the previous text segment's translation when it
  is `TRANSLATED` or `REVIEWED`, as a reader sees it (`validation.plain_text`) with whitespace
  collapsed; otherwise its source text. Status and text are read together under the project
  mutex, which is released before the model call.
  Rules 1 projects keep the source text as context. Their prompts are unchanged except for the
  code prompt's sentence that the shebang is not a comment, which applies to every project.
- TOC labels already came from the translated heading element of the block (Phase 3); a
  heading inside a larger segment is a block of its own, so nothing changed there.

Acceptance:

- Check script: segmenting project-3 with the new rules needs fewer than half of the
  4385 calls the current segmentation needs (count of non-passthrough segments).
  (As built: `scripts/verify/phase6_segmentation_check.py` re-cuts the source with both
  rules; the old rules give 4383 segments for project-3, the stored segmentation has 4385
  because it was migrated from the legacy cut.)
- Tests: a code block without a comment marker is exported byte-identical to the source in all
  three formats and appears once in bilingual output; a merged heading still produces the right
  TOC label.

### Phase 7 — Runtime control, speed, second backend

Depends on Phase 1. Split after measurement into part A (built) and part B (not scheduled).

Part A:

- Stop and pause terminate the in-flight process instead of waiting for it.
- Failed attempts are classified. Transient errors are re-queued with backoff. A rate limit
  pauses and resumes automatically, using `retry_after` when the backend provides it.
- Saves are debounced; `project.json` is 3.7–7.2 MB and is currently rewritten per result batch.
- Prompt reordered so that stable content (rules, book brief) comes first.
- Measure the per-call overhead of the Claude CLI with `--system-prompt`, `--tools ""`,
  `--strict-mcp-config`, `--setting-sources`, `--no-session-persistence`; adopt what helps.
- `CopilotCLI` backend. The installed CLI (1.0.105) has `-p`, `--model`, `--silent`,
  `--output-format`, `--no-custom-instructions`, `--available-tools`; its output and error
  formats still need to be characterized.

Part B (decided after the part A measurements; not scheduled):

- Concurrency adapts: grow slowly while calls succeed, halve after consecutive failures.
- Optional failover: when one backend is rate-limited, dispatch to another. The review tab
  already shows which model produced each segment.

As built, part A (2026-11-21), where it differs from the list above:

- Transient errors are retried inside the per-segment call budget of Phase 4 (`MAX_CALLS`
  4), now with a wait of 5, 15, 45 s before each retry (`TRANSIENT_BACKOFF_SECONDS`); no
  second, run-level retry layer. A segment that runs out of calls fails (`transient`) as
  before and is retried with `--retry-failed`.
- A rate limit pauses the run (web: state `paused`, event `rate_limit` with `resume_in`; CLI:
  a message and a wait) and resumes after `retry_after`, or 300 s when the backend gives none
  (neither CLI gives one). Auth and backend-unavailable errors pause the web task and stop a
  CLI run, as before. One rule for both paths: `src/core/runcontrol.resume_delay`.
- Saves: at most every 10 s or 20 results (`src/core/runcontrol.DebouncedSaver`), and always
  on pause, stop, completion, error and before the lock is released. A crash loses at most
  the last window.
- The prompt order is unchanged. Both CLI backends send the prompt as one user message;
  prompt caching works on whole messages up to a cache breakpoint, so moving text inside the
  message is not expected to help (reasoning, not measured). And in the HTML prompt the
  per-segment context would then sit directly before the source, with nothing marking where
  the source starts: that changes what the model reads. Left for an API backend with a
  separate system prompt.
- `ClaudeCLI` passes `--system-prompt <one line> --tools "" --strict-mcp-config
  --setting-sources "" --no-session-persistence`: measured, input per call 29831 → 2294
  tokens, median wall time 3.06 → 2.43 s on haiku (`scripts/verify/phase7_cli_overhead.py`).
- `CopilotCLI` (`copilot-cli`, CLI 1.0.114): `--output-format json`, no tools, no custom
  instructions, no built-in MCP servers; reports usage. `project.json` has `backend` (absent
  means the Claude CLI and is never written); `start --backend`, and the web model selector
  lists every backend's models.
- Throughput before/after: `scripts/verify/phase7_throughput.py` (`--code <checkout of main>`
  for the before numbers), run by the owner.

Acceptance (part A):

- Tests with the fake backend: stop returns within the test's bound while a call is in flight
  and no child process remains; a simulated rate limit pauses and resumes without user action;
  a transient error is retried and a permanent one is not; 100 results cause far fewer than
  100 writes of `project.json`.
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

### Phase 9 — Copilot CLI completion

Depends on Phase 7. The owner translates books with Copilot, but `CopilotCLI` still sends
Copilot's built-in system prompt (a coding agent's, about 2000 tokens) with every call, and
replies to a bare sentence come back with alternatives and explanations. Copilot CLI 1.0.114 has
no flag, environment variable or setting for the system prompt; an extension can replace it.

- A plugin kept in the repository (`src/llm/copilot_plugin/`: `plugin.json` and
  `extensions/system-prompt/extension.mjs`) is loaded with `--experimental --plugin-dir <its
  absolute path>`. Its extension calls `joinSession({systemMessage: {mode: "replace", content}})`
  with the content read from `EPUB_TRANSLATOR_SYSTEM_PROMPT`, which `CopilotCLI` sets to the same
  one-line prompt `ClaudeCLI` passes with `--system-prompt` (`src/llm/cli.SYSTEM_PROMPT`, one
  definition for both).
- The CLI says nothing when the extension does not load. Every successful run is checked: the
  usage checkpoint reports the system prompt segments sent with each model call, and with the
  replacement that is exactly one `agent_replacement` segment. Anything else (the 18 built-in
  segments, no report) raises `BackendUnavailableError`: nothing is stored and the run stops
  with a message naming what to check. No fallback to the built-in prompt.
- Cancellation, error classification, e-mail redaction and usage accounting unchanged.

Acceptance:

- Tests with a fake process: the command (flags, plugin path) and the prompt handed to the
  extension; a reply without the replacement raises the unavailable error and a run stores
  nothing; a long prompt with the replacement passes.
- `scripts/verify/phase9_copilot_overhead.py`: input tokens per call with and without the
  replacement on invented text; exits non-zero unless every replaced call passes the check and
  every broken setup (`--no-experimental`, a wrong plugin folder) is detected.
- Real runs, invented text: a short invented book with 2 workers completes; a cancelled call
  leaves no `copilot` or extension process.

Risks:

- Extensions are an experimental feature, and replacing the system prompt from an extension
  loaded with `--plugin-dir` in `-p` mode is not documented; the segment report the check reads
  is undocumented telemetry. A CLI update can break either. Then the check stops the run (it does
  not translate on with the full prompt) and the backend needs updating.
- `mode: "replace"` removes Copilot's built-in guardrails along with the rest of its prompt
  (the SDK documentation says so). The calls have no tools, so the model can only answer text.
- One more process per call (the extension runs in Node, started by the CLI).

### Phase 10 — Reasoning effort and call timeout

Depends on Phase 9. Both CLIs let a call set how much the model reasons, and slow models need
more time than the fixed 120 s per translation call (measured with Claude Code 2.1.330 and
Copilot CLI 1.0.114 on invented text: Gemini 4 Carbon at its default `medium` level took 170 s
on one paragraph). Two per-project settings, stored and chosen exactly like backend and model:

- **Effort.** Each model declares the levels it accepts (`ModelInfo.efforts`, from the CLIs'
  own validation): the three Claude Code models and Copilot's Claude 5/5.5 and GPT models take
  `low, medium, high, xhigh, max` (four GPT models also `none`), the Gemini models `low, medium,
  high`, the Grok models `low, medium, high, xhigh`, `claude-haiku-4.5` none (nothing is passed).
  Copilot's model list is the 20 chat models the account offers (review round 1). Default `low`;
  a stored level the model does not list is replaced by the default when calling. `project.json`
  `effort` is written only once set; absent means the default (and none for a model that takes
  no level). `ProjectManager.select_model(backend, model, effort, timeout)` refuses a level the
  model does not take, changing nothing; a model change drops a stored level the new model does
  not take back to the default and the CLI / web confirmation says so. Claude gets `--effort`
  (`CLAUDE_CODE_EFFORT_LEVEL` is removed from its environment: it overrides the flag; the CLI
  ignores an unknown value with a warning, so only declared levels are passed); Copilot gets
  `--reasoning-effort`. Copilot's refusal of a level (`Reasoning effort "…" is not supported for
  model …`, `does not support reasoning effort configuration`) is a `BackendUnavailableError`:
  nothing stored, the web task pauses, the CLI exits 1.
- **Timeout.** One setting for translation and term extraction calls, default 180 s, 30–3600
  integer seconds; `project.json` `timeout` written only once set.
- Both are read per segment by a web task (a change applies to the next segment), and used by
  re-translation and term extraction. CLI `model set -e/-t`, `model show`; web `GET|PUT
  …/model` (`effort`, `efforts`, `timeout`, and each model's levels for the page); the
  translation page has an effort selector (only the model's levels; "此模型不可設定") and a
  timeout field.

Not built, on purpose: a timeout scaled by level, hiding levels that might time out, a check of
which level the CLI really used, reasoning-token accounting, JSON output for the Claude backend.

Acceptance:

- Tests with fake processes: the command line and environment per backend, model and level;
  refused values leave `project.json` byte-identical; the reset on a model change; the Copilot
  refusals stop the run with nothing stored; the timeout reaches translation and extraction
  calls; a mid-run switch applies to the next segment; old project files load as the defaults
  and round-trip.
- `scripts/verify/phase10_effort_check.py`: real calls on an invented paragraph, one per level
  for a model; prints tokens (reasoning included) and wall time; exits non-zero if a call fails.

Risks:

- High levels on slow models can exceed the timeout (Gemini at `high` did not finish in 300 s
  in the research); the user raises the timeout or lowers the level.
- The per-model level lists come from the CLIs' validation and can change between versions:
  a Copilot model that stops taking a level stops the run with the CLI's message; for Claude an
  unknown level would be ignored by the CLI, which is why the lists are checked before calls.

### Phase 11 — Python upgrade (3.12+), upgrade only

Move the project environment from Python 3.9 to 3.12 or later, with no behaviour change: the test
suite and the mutation run on one interpreter. Phase 12 needs it (the Copilot Python SDK needs
3.11+). Nothing else changes: no new feature, no style rewrite (`from __future__ import
annotations`, `typing.List`/`Optional` and the like stay).

Done:

- Minimum Python 3.12. `src/__init__.py` (`MIN_PYTHON`, `require_python(version=None)`) exits
  with one Traditional Chinese message when the package is imported on an older interpreter.
  Importing `src` comes before any other project module in both entry points: `translate.py`
  imports it first (before `click`), and `flask --app src.web.server:create_app` imports the
  package before `src.web.server`. Tested with the version injected (`require_python((3, 11,
  9))`) and, in a child process, with `sys.version_info` replaced before each entry point
  (`tests/test_python_version.py`); also checked once on the real 3.9 interpreter. The import
  calls `require_python()` only when the version is too old: an unconditional call goes through
  mutmut's trampoline on every import, so in mutmut's forced-fail run every child process that
  imports `src` failed, a lock test left such a child unreaped, and mutmut's `os.wait()` then
  collected it (`KeyError` in `wait_for_result`, run aborted).
- Requirements: `lxml>=4.9.3` (the first lxml with 3.12 wheels); `mutmut==3.8.1` without its
  `python_version >= "3.10"` marker, so the project environment runs the mutation tests.
  `scripts/verify/mutation.sh` uses the active environment's `python3`; `MUTATION_VENV` is gone
  (with one environment it no longer earns its place). The other lower bounds are unchanged:
  the suite passes on 3.12 with exactly `beautifulsoup4==4.12.0`, `click==8.1.3` (Flask 3.0
  needs it), `lxml==4.9.3`, `flask==3.0.0`, `pytest==7.4.0`, `ebooklib==0.18`.
- Python 3.14.7 (newest installed) found one real difference: some `zipfile` releases preset
  or keep the UTF-8 bit in a member's `flag_bits` before writing it (CPython source: `= 0x00` up
  to 3.13.13 and 3.14.5, preset to the UTF-8 flag from 3.13.14 and 3.14.6, kept or set on the
  current 3.14 branch), and the exporter's `_ZipInfo` added the source's flag to that, so every
  member was written flagged UTF-8 (on 3.14.7, 50 tests failed on it: export metadata checks and
  the `phase3_export_check` tests built on them). The exporter now replaces that bit with the
  source's. Two test fixtures that write raw names
  without the flag clear it the same way; a unit test covers the preset flag on any version.
  The 51st failure: a test relied on 3.12's JSON decoder failing at 100000 nesting levels (3.14
  parses them); it now stubs the consolidation decoder to raise `RecursionError`.
- 3.9 versus 3.12 versus 3.14: the test fixtures' books exported in all three formats under
  each interpreter are byte-identical archives (acceptance log).

Risks:

- `_ZipInfo` overrides a private `zipfile` method (`_encodeFilenameFlags`); a future Python can
  change it again, as 3.13.14 and 3.14.6 did. The exporter tests that compare member metadata with the source
  are what catches it.
- `scripts/verify/mutation.py` hooks into mutmut 3.8.1 internals; the pin stays.
- An existing virtualenv is bound to the interpreter that made it: owners rebuild it (README).
- Migration: newer patch releases keep the content of some elements as raw text in
  `html.parser` (`title`, `textarea` from 3.12.12, 3.13.6, 3.14.0; `iframe`, `noembed`,
  `noframes`, `xmp`, `plaintext` from 3.12.13, 3.13.10, 3.14.1, by CPython's
  `html/parser.py`; observed: 3.12.7 none, 3.13.7 `title`/`textarea`, 3.14.7 all), so `migrate`
  can refuse a chapter where such an element holds an entity or child markup
  (`known-issues.md`). Not worked around.

### Phase 12 — Copilot SDK backend

An additional backend beside `copilot-cli`, built on the Copilot SDK. To be specified after a
feasibility check: authentication with the logged-in account and how usage is billed,
availability of a Python SDK, and how pause and cancel map to a long-lived session.

## 6. Order

```
Phase 0 ── independent
Phase 1 ─┬─> Phase 7 ─> Phase 9 ─> Phase 10 ─> Phase 12
         └─┐
Phase 2 ───┼─> Phase 3 ─┐
           └─> Phase 4 ─┼─> Phase 5
                        └─> Phase 6
Phase 8 ── after Phase 1
Phase 11 ── independent (before Phase 12)
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
| What to protect | Every tag with attributes gets a key; top-level blocks always. Every key is required and tags without a key keep their count per tag name (4.1). `alt`, `title` and `aria-label` are shown and translated; every other attribute stays hidden. |
| Split on failure | Yes, once, inside the call only. No new segments or anchors. |
| Language for bilingual | Package and root stay in the source language; translated copies carry `lang="zh-TW"`. |
| Thresholds | Structural rules fixed in code. Quality heuristics have defaults and never block. |

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
