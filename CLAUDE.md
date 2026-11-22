# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

EPUB 智能翻譯系統 - Translates English EPUB books to Traditional Chinese through a command-line model backend (`claude-cli` or `copilot-cli`). Glossary with automatic extraction, per-book brief, structural validation with retry, three output formats, Web review interface and CLI. `plan/02-improvement-plan.md` is the design record, `plan/03-acceptance-log.md` the evidence per phase. Python 3.12+ (`src/__init__.py` `require_python` stops older interpreters on import of `src`, so before any other module of `translate.py` or the app factory); POSIX only (macOS/Linux: `fcntl` locks, process groups).

## Development Commands

```bash
python3.12 -m venv venv                  # fresh checkout only (3.12 or newer); an existing older venv: README rebuild steps
source venv/bin/activate                 # required before all commands
pip install -r requirements.txt -r requirements-dev.txt   # dev: test fixtures (ebooklib), mutmut

python3 -m flask --app src.web.server:create_app run --port 3000   # web UI
python3 -m pytest tests/ -q -p no:cacheprovider -rfw               # tests
scripts/verify/mutation.sh main          # mutation run over lines changed vs main (see below)
```

CLI synopsis (not shell; each command has `--help`). `init`, `validate`, `migrate` take a path; the others take `-p/--project DIR`, default: the first project found under `projects/`.

```text
translate.py init EPUB_FILE                 -> projects/<sanitized file name>/ (refuses an existing one)
translate.py status
translate.py start [-b claude-cli|copilot-cli] [-m MODEL] [-w 1..20] [--retry-failed]
translate.py validate PROJECT_DIR [--mark-failed]
translate.py migrate PROJECT_DIR [--dry-run]
translate.py brief show
translate.py brief set TEXT                 (empty TEXT removes it)
translate.py model show
translate.py model set [-b claude-cli|copilot-cli] [-m MODEL] [-e LEVEL] [-t SECONDS]   (at least one; does not translate)
translate.py glossary show | extract | add TERM TRANSLATION [-c CAT] [-g GENDER] [-n NOTE] | import FILE | export FILE
translate.py export [-f chinese-only|bilingual-horizontal|bilingual-vertical]   (default bilingual-horizontal)
translate.py review [--port 5000]
```

Backend, model, reasoning effort and call timeout are stored in the project; from the CLI `model set -b/-m/-e/-t` sets them (takes `project.lock` like `brief set`) and `start -b/-m` sets backend/model and then translates. All of them and the web `PUT …/model` go through `ProjectManager.select_model`: the project's own backend is not stored again, a new backend without a model takes its default model, a model must belong to the backend it will run with, an effort must be one of the model's levels (`ModelInfo.efforts`), a timeout an integer 30–3600; when the backend or model changes, a stored effort the new model does not take is dropped back to the default (the confirmation says so, `effort_reset_note`; an effort- or timeout-only change keeps it); refused values change nothing (`SettingError`, a `ValueError` naming the setting). Calls use `ProjectManager.call_settings()` (`CallSettings.call_args()` → `TranslationEngine` / `TermExtractor`). `glossary extract` uses the stored backend, so a Copilot-only user runs `model set -b copilot-cli` (or picks a Copilot model on the web translation page) first.

## Architecture

### Pipeline
1. **Extraction** (`src/epub/extractor.py`) — reads the package (OPF, spine) with lxml; chapters as parsed XHTML.
2. **Blocks** (`src/epub/blocks.py`) — `walk_blocks`: a chapter body as blocks (block-level elements, or runs of inline content), each with an index and a whitespace-free text fingerprint.
3. **Segmentation** (`src/core/segmenter.py`) — contiguous block ranges; a block is never split. Groups to `MIN_WORDS`=300 without passing `MAX_WORDS`=500 unless one block is larger; code and tables alone. Rules (`project.segmentation`): 1 = heading alone (old projects), 2 = heading joins the following blocks (its first text block always, even past 500 words; alone only before code, a table or the chapter end), and code without a comment line is `passthrough` (no model call, exported as is).
4. **Translation** (`src/core/translator.py`) — `translate_segment` / `TranslationEngine`: text prompts get the brief at the top, the glossary and context (nearest non-code segment of the chapter before/after; with rules 2 the one before as its translation when it has one); code prompts get none of these. Protected HTML in, reply checked, at most `MAX_CALLS`=4 calls per `translate_segment` (request, one retry with the errors, one split in two halves; a requeued segment starts a new budget), the project's timeout per call (default `DEFAULT_TIMEOUT`=180 s) and its effort passed with each call. Returns a `TranslationOutcome` with typed `Issue`s; a failure writes `log/failed/<id>-<digest>.txt`. `ProjectManager.apply_outcome`: a failed outcome makes a pending segment `failed`; a translated/reviewed one keeps its text and status and only gets the issues (so `--retry-failed` does not select it).
5. **Validation** (`src/core/validation.py`) — attributes replaced by keys (`<p k="1">`; `alt`/`title`/`aria-label` stay visible to be translated); `p = protect(html); restore(p.html, p) == html`. Shared rules in `check_html` / `check_code`, used by model replies (after `check_keys` on the keyed form), by `check_segment` (review saves/approvals and `validate`, plus glossary warnings) and by the export (which also checks the chapter's blocks, so passing `validate` does not guarantee export). Structural rules block (`structure_mismatch`, `key_mismatch`, `tag_count`, `code_changed`, …); quality/glossary rules warn (`length_ratio`, `untranslated`, `term_not_applied`).
6. **Review** (`src/web/`) — side-by-side editing; a save or approval that fails the structural check is refused (422).
7. **Export** (`src/epub/exporter.py`) — ZIP written from the source EPUB that `source_epub_path` (`src/core/project.py`) resolves: the project directory's own `source.epub` (every schema 2 project has one, written at creation or by migrate), else the recorded `epub_path`, so a moved or renamed project still exports; the file opened is hash-checked against `source_sha256` (the digest recorded at creation or migration) and may not be the output; `epub_path` is not rewritten. Only translated chapters, OPF, NCX, nav (and one stylesheet for bilingual) differ; every other member keeps identical uncompressed bytes (the archive itself is rewritten). A chapter whose blocks no longer match the stored ranges/fingerprints, or a segment that fails the check, stays in the original language; the report says why. Takes `project.lock`.

### Backends (`src/llm/`)
- `LLMBackend` protocol (`base.py`); `BACKENDS` / `backend_for(name)` in `__init__.py`; project field `backend` (absent = `claude-cli`). `CLIBackend` (`cli.py`) is the only place that starts model processes: own process group, cancel/deadline polling, killed on cancel and at interpreter exit, working directory = system temp dir, one `SYSTEM_PROMPT`.
- `ClaudeCLI` (`claude_cli.py`): `claude -p` plus `--effort LEVEL` when the request has one, plus `LEAN_FLAGS` (`--system-prompt`, no tools, no MCP, no setting sources, no session file); `CLAUDECODE` and `CLAUDE_CODE_EFFORT_LEVEL` (it overrides `--effort`) removed from the environment. The CLI ignores an unknown `--effort` value with a warning and exit 0, so only declared levels are passed. Models `sonnet` (default), `opus`, `haiku`. Error classes from output patterns (rate limit, expired login).
- `CopilotCLI` (`copilot_cli.py`): `copilot -p … [--reasoning-effort LEVEL] --output-format json --available-tools none … --experimental --plugin-dir src/llm/copilot_plugin`. The plugin's extension replaces Copilot's system prompt (read from `EPUB_TRANSLATOR_SYSTEM_PROMPT`). After each call, every reported model call must show exactly one `agent_replacement` system segment, else `BackendUnavailableError` (nothing stored; web pauses, CLI exits 1). Risks: experimental, undocumented interface and telemetry (tested with Copilot CLI 1.0.114); rate-limit messages are Copilot's documented ones, auth/refusal patterns are guesses. A level the model does not take (`Reasoning effort "…" is not supported for model …`, `… does not support reasoning effort configuration`, exit 1 before any model call) is a `BackendUnavailableError` (`EFFORT_REJECTED_MESSAGE`).
- Copilot models (`copilot_cli.MODELS`, a static list): Claude `claude-sonnet-5.5` (default), `claude-sonnet-5`, `claude-opus-5.5`, `claude-haiku-5.5`, `claude-haiku-4.5`; GPT `gpt-6.2-sol`, `gpt-6.2-astra`, `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-luna`; Gemini `gemini-4-carbon`, `gemini-4-argon`, `gemini-4-hydrogen`, `gemini-3.8-flash`; Grok `grok-4.7`, `grok-4.6` (multipliers in the descriptions). The web selector groups models by backend and alias family (`<optgroup>`); `model show` prints one line per family.
- Effort levels per model (`ModelInfo.efforts`; `EFFORT_LEVELS` = `low…max`, `KNOWN_LEVELS` adds Copilot's `none`), from the CLIs' own validation (Claude Code 2.1.330, Copilot CLI 1.0.114; no command lists them, they can change between versions, as can Copilot's model list): the Claude backend's models, Copilot's Claude 5/5.5 and GPT models take `low, medium, high, xhigh, max` (`gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-luna` also `none`); Gemini `low, medium, high`; Grok `low, medium, high, xhigh`; `claude-haiku-4.5` none (nothing passed). `call_settings` uses a stored level only when the model lists it, else the default (the file keeps it until a backend/model change drops it); a value outside `KNOWN_LEVELS` (e.g. `"bogus"`) makes the project fail to load and must be corrected or removed. Known risk: high levels on slow models (Gemini) can exceed the timeout.
- Neither backend reports `retry_after`, so a rate limit waits `RATE_LIMIT_WAIT_SECONDS`.
- `FakeBackend` (`fake.py`) for tests; `tests/conftest.py` refuses any real model process (`run_process`), and for the whole session puts failing `claude`/`copilot` stand-ins first on `PATH` (exit 97, inherited by every child process); other subprocesses (lock tests) are allowed.

### Runtime
- **Web task** (`src/web/tasks.py`, `TaskManager`): coordinator + workers (`MAX_WORKERS`=20) + `result_queue`; only the coordinator applies results, and it saves them (a web export first flushes the running task's saver from the request thread). `EventBroadcaster` feeds SSE: a client gets a status snapshot, then every event from when it connected (no history). Backend/model/effort/timeout/brief re-read per segment, so a switch applies to the next segment. Worker count can change on resume.
- **Run-level errors** (`src/core/runcontrol.py`): rate limit → pause, auto-resume after `retry_after` (clamped 1–3600 s) or `RATE_LIMIT_WAIT_SECONDS`=300; expired login / backend unavailable → web: pause until the user resumes; CLI: exit 1. A web pause (user or error) cancels calls in flight; their segments are queued again. A CLI rate limit stops dispatch and backoff waits; calls already running finish. Transient errors back off 5/15/45 s inside the segment's call budget. `MAX_CONSECUTIVE_UNEXPECTED`=5 unexpected failures in a row stop the run.
- **Saving**: `DebouncedSaver.maybe_save` saves when `SAVE_INTERVAL_SECONDS`=10 have passed or `SAVE_EVERY_RESULTS`=20 results wait. It is checked after each result (web also on each coordinator tick, about 1 s), so the interval is a threshold, not a durability bound: a sequential CLI run checks only after its next result. Pause (at the next tick), stop, end and failure attempt a flush; results not saved are reported. Failure logs are written only after their result is on disk.
- **Concurrency** (covers `project.json`): one in-process RLock per project (`project_mutex`) for every change and save, plus a file lock `project.lock` (`ProjectLock`, flock) held by a CLI run, a web task, migrate, export, `validate --mark-failed`, project create/delete and each web edit transaction (`TaskManager.transaction`); a locked project answers 409. `glossary.json` takes neither: glossary edits go through during a run, are not seen by it (the glossary is loaded at its start) and can overwrite each other. Edits carry a `revision`; a result computed for an older revision is dropped.
- `ProjectManager` mutations are in-memory; callers call `pm.save()` (atomic write, file mode kept).

### Project data (`projects/<name>/`, not in git)
- `project.json`: `schema` 2 for new projects (project-level `source_sha256` and absolute `epub_path`; segments with `block_start`/`block_end`/`fingerprints`). `backend`, `segmentation`, `brief`, `effort`, `timeout` are omitted when absent (None: the default — effort `low` for a model that takes levels, none otherwise; timeout 180 s), so older files round-trip; load refuses an `effort` outside `KNOWN_LEVELS` or a `timeout` outside 30–3600; a missing `model` is filled with the backend's default on load and written by the next save. Status: `pending`, `translated`, `reviewed`, `failed`.
- Schema 1 (pre-plan projects) translates but cannot be exported or validated: `translate.py migrate` (`src/core/migrate.py`) once. Needs the source EPUB (`source_epub_path`: `source.epub`, else `epub_path`); refuses when `project.schema1.json` exists or the project is locked; changes no project data unless every chapter matches (the lock file is still created). A merged group with a pending/failed/empty member keeps no translation (the old text stays in `project.schema1.json`): read the dry run's notes first. Copies `source.epub` when missing.
- Web `POST /api/projects` replaces an idle project of the same name (new `project.json` and `source.epub`, `log/failed/` removed, `glossary.json` kept); CLI `init` refuses an existing one. Known issue: "idle" means no translation task; a web export of that project running in the same server is not refused (the lock is shared by the server's threads), so its result should be discarded (`known-issues.md`).
- Also `glossary.json`, `source.epub`, `log/translation_*.log` (web runs), `log/failed/`, `output/*.epub`.

### Glossary (`src/glossary/`)
- Entries: term, translation, category (`person`, `place`, `work`, `organization`, `term`, `expression`, `other`), and for people `gender` and `note`, which go into text prompts. All entries are in the prompt up to `FULL_GLOSSARY_MAX_TERMS`=150, else only those found in the segment.
- `TermExtractor.extract_from_segments` (the project's timeout and effort per call): batches of ≤5000 chars propose terms; terms proposed with several translations are decided by consolidation calls; undecided ones keep the most frequent proposal. A rate limit, login error or unavailable backend stops it with the terms found so far (kept). The web adds only terms not yet in the glossary; `glossary extract` (CLI) also replaces the translation and category of existing ones but keeps their gender/note.

### Web (`src/web/`)
Flask app factory (`server.py`, `create_app(projects_dir=None)`), one blueprint per area, Jinja2 + Alpine.js 3 (no build step). Alpine calls a component's `init()` itself: do not add `x-init="init()"`.

Pages: `/`, `/new`, `/project/<name>` (`/overview`, `/glossary`, `/translation`, `/review`, `/review/chapter/<chapter_id>`, `/search`, `/export`); `?issues=1` on review pages shows only segments with issues.

API (`/api/...`):
- projects: `GET /projects`, `POST /upload`, `POST /epub/metadata`, `POST /projects`, `GET|DELETE /projects/<name>`, `GET /projects/<name>/chapters`, `GET /projects/<name>/progress`
- glossary: `GET|POST /projects/<name>/glossary`, `PUT|DELETE /projects/<name>/glossary/<term>`, `DELETE /projects/<name>/glossary/batch`, `POST /projects/<name>/glossary/extract`, `GET /projects/<name>/glossary/extract/status`
- translation: `GET /projects/<name>/model` (→ `current`, `backend`, `effort` (null: not configurable), `efforts`, `timeout`, `timeout_min`, `timeout_max`, `available`, `backends`; every model with its `efforts`), `PUT` same path (any of `model`, `backend`, `effort`, `timeout`; left out or null = kept, none of them = 400; the page sends only what the user changed; answers the stored settings and `note`; a failed save restores all four), `GET|PUT /projects/<name>/brief`, `POST /projects/<name>/translate/start` (`workers`, `segment_ids`, `retry_failed`), `POST /projects/<name>/translate/pause` (`{action: pause|resume, workers}`), `GET /projects/<name>/translate/status`, `GET /projects/<name>/translate/stream` (SSE)
- review: `POST /projects/<name>/segments/<id>` (edit), `…/approve`, `…/retranslate`; `POST /projects/<name>/search-segments`
- export: `POST /projects/<name>/export` (`format`), `GET /projects/<name>/export/status`, `GET /projects/<name>/export/files`, `GET /projects/<name>/download/<filename>`

## Testing and verification

- `python3 -m pytest tests/ -q -p no:cacheprovider -rfw` — must pass with no warnings, also when several suites run at once. New tests wait on conditions (`_wait`, events), not on sleeps (a few older timing tests still sleep). No test makes a real model call: besides the two guards above, a measuring script that imports `run_process` by name must be added to `MEASURING_SCRIPTS` in `tests/test_verify_scripts.py`, whose autouse fixture makes that copy fail unless a test stubs it.
- `scripts/verify/mutation.sh [BASE]` — mutmut 3.8.1 over the lines changed vs `BASE` in the files listed in `mutation.py`, on a copy of the tree. Runs on the active environment's `python3` (the project venv with both requirements files). Exits 2 when mutmut is missing or no eligible line changed.
- `scripts/verify/` — one script per phase claim, each with a docstring:
  - read-only checks of real projects (`--projects-dir`, never written): `phase0_code_segments`, `phase1_load_projects`, `phase2_roundtrip` read in place; `phase2_migrate_copies`, `phase3_export_check`, `phase4_validate_check`, `phase5_glossary_check`, `phase6_segmentation_check` work on a temp copy. `--anonymize` covers stdout only: a file written with `--sample-out` (phase5) or `--out` (phase8) holds real book text and must never be published or committed;
  - guards of the test suite itself: `phase1_reverts` (old behaviours put back one by one; each must fail a test) and `phase1_survivor_kills` (recorded mutants must be killed); both work on `git archive HEAD`, so commit first;
  - measurements that make REAL model calls (cost quota, run only on purpose): `phase7_cli_overhead`, `phase7_throughput`, `phase8_brief_sample`, `phase8_extraction_compare`, `phase9_copilot_overhead`, `phase10_effort_check`.

## Troubleshooting

- To stop a run: the web pause button (saved at the next tick), or Ctrl-C in the CLI's terminal (kills the calls in flight, then saves). Killing the web server loses results not yet saved.
- A call left behind: pause / Ctrl-C first; then `pgrep -fl 'claude -p'` (or `copilot -p`), check that it is this project's, `kill <PID>`. Not `pkill -f`: it also hits other sessions. Process arguments contain book text: do not paste them anywhere public.
- Translation and extraction calls time out after the project's timeout (default 180 s; `model set -t` or the translation page); a high effort on a slow model can need more. A timeout is a transient error, retried within the segment's budget: a retry that succeeds leaves no segment failure and no failure file (the web run log keeps a warning). Only when the budget is used up does a pending segment become `failed` (`log/failed/`; retry with `start --retry-failed` or the retry button); a translated/reviewed one keeps its text and status and only gets issues (retranslate it from review/search). Term extraction does not retry: a timed-out batch is logged and proposes nothing, a timed-out consolidation call leaves its terms at the most frequent proposal. Stored translations that fail the check: `validate --mark-failed`, then `start --retry-failed`.
- "project locked" (409 / CLI message): another process (a CLI run, the web task, an export, a migration) holds `project.lock`.
- Export or validate refuses a schema 1 project: run `migrate` first.
- Export cannot read the source EPUB ("無法讀取來源 EPUB"): the project's `source.epub` is missing (removed or renamed) and the recorded `epub_path` does not lead to the book either; put the book back as `source.epub` in the project directory. A hash mismatch means that file is not the book whose digest was recorded at creation or migration.
- Copilot run stopped with "無法確認 Copilot CLI 已套用翻譯用的系統提示": the plugin did not load or the CLI changed; check `copilot --help` for `--experimental`/`--plugin-dir`.
- Web run log: `projects/<name>/log/translation_*.log`.
