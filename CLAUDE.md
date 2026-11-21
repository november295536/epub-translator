# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

EPUB 智能翻譯系統 - Translates English EPUB books to Traditional Chinese through a command-line model backend (`claude-cli` or `copilot-cli`). Glossary with automatic extraction, per-book brief, structural validation with retry, three output formats, Web review interface and CLI. `plan/02-improvement-plan.md` is the design record, `plan/03-acceptance-log.md` the evidence per phase.

## Development Commands

```bash
source venv/bin/activate                 # required before all commands
pip install -r requirements.txt -r requirements-dev.txt   # dev: test fixtures (ebooklib), mutmut on 3.10+

python3 -m flask --app src.web.server:create_app run --port 3000   # web UI
python3 -m pytest tests/ -q -p no:cacheprovider -rfw               # tests
scripts/verify/mutation.sh main          # mutation run over lines changed vs main (needs Python 3.10+, see below)

# CLI (each command has --help); -p/--project picks the project, else the first one under projects/
python3 translate.py init <epub_file>                    # -> projects/<sanitized file name>/
python3 translate.py status
python3 translate.py start [-b claude-cli|copilot-cli] [-m MODEL] [-w 1..20] [--retry-failed]
python3 translate.py validate <project_dir> [--mark-failed]
python3 translate.py migrate <project_dir> [--dry-run]
python3 translate.py brief show | brief set "TEXT"       # empty TEXT removes it
python3 translate.py glossary show | extract | add TERM TRANSLATION [-c CAT] [-g GENDER] [-n NOTE] | import FILE | export FILE
python3 translate.py export [-f chinese-only|bilingual-horizontal|bilingual-vertical]   # CLI default bilingual-horizontal
python3 translate.py review [--port 5000]
```

## Architecture

### Pipeline
1. **Extraction** (`src/epub/extractor.py`) — reads the package (OPF, spine) with lxml; chapters as parsed XHTML.
2. **Blocks** (`src/epub/blocks.py`) — `walk_blocks`: a chapter body as blocks (block-level elements, or runs of inline content), each with an index and a whitespace-free text fingerprint.
3. **Segmentation** (`src/core/segmenter.py`) — contiguous block ranges; a block is never split. Groups to `MIN_WORDS`=300 without passing `MAX_WORDS`=500 unless one block is larger; code and tables alone. Rules (`project.segmentation`): 1 = heading alone (old projects), 2 = heading joins the following blocks, and code without a comment line is `passthrough` (no model call, exported as is).
4. **Translation** (`src/core/translator.py`) — `translate_segment` / `TranslationEngine`: prompt with glossary, context (with rules 2 the previous segment's translation), brief at the top; protected HTML in, reply checked, at most `MAX_CALLS`=4 calls per segment (request, one retry with the errors, one split in two halves). Returns a `TranslationOutcome`; failures are `FAILED` with typed `Issue`s and a diagnostics file `log/failed/<id>-<digest>.txt`.
5. **Validation** (`src/core/validation.py`) — attributes replaced by keys (`<p k="1">`; `alt`/`title`/`aria-label` stay visible to be translated), `restore(protect(html)) == html`. `check_segment` is the one check used for model replies, review saves/approvals, `validate` and the export: structural rules block (`structure_mismatch`, `key_mismatch`, `tag_count`, `code_changed`, …), quality/glossary rules warn (`length_ratio`, `untranslated`, `term_not_applied`).
6. **Review** (`src/web/`) — side-by-side editing; a save or approval that fails the structural check is refused (422).
7. **Export** (`src/epub/exporter.py`) — ZIP written from the project's `source.epub` (hash checked): only translated chapters, OPF, NCX, nav (and one stylesheet for bilingual) differ; everything else is byte-identical. A chapter whose blocks no longer match the stored ranges/fingerprints, or a segment that fails the check, stays in the original language; the report says why.

### Backends (`src/llm/`)
- `LLMBackend` protocol (`base.py`); `BACKENDS` / `backend_for(name)` in `__init__.py`; project field `backend` (absent = `claude-cli`). `CLIBackend` (`cli.py`) is the only place that starts model processes: own process group, cancel/deadline polling, killed on pause/stop and at interpreter exit, working directory = system temp dir, one `SYSTEM_PROMPT`.
- `ClaudeCLI` (`claude_cli.py`): `claude -p` plus `LEAN_FLAGS` (`--system-prompt`, no tools, no MCP, no setting sources, no session file); `CLAUDECODE` removed from the environment. Models `sonnet` (default), `opus`, `haiku`. Error classes from output patterns (rate limit, expired login).
- `CopilotCLI` (`copilot_cli.py`): `copilot -p … --output-format json --available-tools none … --experimental --plugin-dir src/llm/copilot_plugin`. The plugin's extension replaces Copilot's system prompt (read from `EPUB_TRANSLATOR_SYSTEM_PROMPT`). Every successful run must report exactly one `agent_replacement` system segment, else `BackendUnavailableError` and the run stops. Risks: experimental, undocumented interface and telemetry (tested with Copilot CLI 1.0.114); rate-limit/auth patterns are from docs, not observed.
- `FakeBackend` (`fake.py`) for tests; `tests/conftest.py` refuses any real process call.

### Runtime
- **Web task** (`src/web/tasks.py`, `TaskManager`): coordinator + workers (`MAX_WORKERS`=20) + `result_queue`; only the coordinator applies results and saves. `EventBroadcaster` feeds SSE (every client sees every event). Backend/model/brief re-read per segment, so a switch applies to the next segment. Worker count can change on resume.
- **Run-level errors** (`src/core/runcontrol.py`): rate limit → pause, auto-resume after `retry_after` (clamped 1–3600 s) or `RATE_LIMIT_WAIT_SECONDS`=300; expired login / backend unavailable → pause until the user resumes (CLI: exit 1). A pause cancels calls in flight; their segments are queued again. Transient errors back off 5/15/45 s inside the segment's call budget. `MAX_CONSECUTIVE_UNEXPECTED`=5 unexpected failures in a row stop the run.
- **Saving**: `DebouncedSaver` saves every `SAVE_INTERVAL_SECONDS`=10 or `SAVE_EVERY_RESULTS`=20 results, and always on pause/stop/end/failure; results not saved are reported. Failure logs are written only after their result is on disk.
- **Concurrency**: one in-process RLock per project (`project_mutex`) for every change and save, plus a file lock `project.lock` (`ProjectLock`, flock) held by a CLI run, a web task, and each web edit transaction; a locked project answers 409. Edits carry a `revision`; a result computed for an older revision is dropped.
- `ProjectManager` mutations are in-memory; callers call `pm.save()` (atomic write, file mode kept).

### Project data (`projects/<name>/`, not in git)
- `project.json`: `schema` 2 for new projects (segments with `block_start`/`block_end`/`fingerprints`, `source_sha256`); fields `model`, `backend`, `segmentation`, `brief` are omitted when absent so older files round-trip unchanged. Status: `pending`, `translated`, `reviewed`, `failed`.
- Schema 1 (pre-plan projects) translates but cannot be exported or validated: `translate.py migrate` (`src/core/migrate.py`) once; fail-closed (writes nothing unless every chapter matches), keeps `project.schema1.json`, copies `source.epub` when missing.
- Also `glossary.json`, `source.epub`, `log/translation_*.log` (web runs), `log/failed/`, `output/*.epub`.

### Glossary (`src/glossary/`)
- Entries: term, translation, category (`person`, `place`, `work`, `organization`, `term`, `expression`, `other`), and for people `gender` and `note`, which go into the prompt. All entries are in the prompt up to 150, else those found in the segment.
- `TermExtractor.extract_from_segments`: batches of ≤5000 chars propose terms; terms proposed with several translations are decided by a consolidation call; undecided ones keep the most frequent proposal. A rate limit/login error stops it with the terms found so far (kept). The web adds only terms not yet in the glossary; `glossary extract` (CLI) also replaces the translation and category of existing ones but keeps their gender/note.

### Web (`src/web/`)
Flask app factory (`server.py`, `create_app(projects_dir=None)`), one blueprint per area, Jinja2 + Alpine.js 3 (no build step). Alpine calls a component's `init()` itself: do not add `x-init="init()"`.

Pages: `/`, `/new`, `/project/<name>` (`/overview`, `/glossary`, `/translation`, `/review`, `/review/chapter/<chapter_id>`, `/search`, `/export`); `?issues=1` on review pages shows only segments with issues.

API (`/api/...`):
- projects: `GET /projects`, `POST /upload`, `POST /epub/metadata`, `POST /projects`, `GET|DELETE /projects/<name>`, `GET /projects/<name>/chapters`, `GET /projects/<name>/progress`
- glossary: `GET|POST /projects/<name>/glossary`, `PUT|DELETE /projects/<name>/glossary/<term>`, `DELETE /projects/<name>/glossary/batch`, `POST /projects/<name>/glossary/extract`, `GET /projects/<name>/glossary/extract/status`
- translation: `GET|PUT /projects/<name>/model` (`{model, backend}`), `GET|PUT /projects/<name>/brief`, `POST /projects/<name>/translate/start` (`workers`, `segment_ids`, `retry_failed`), `POST /projects/<name>/translate/pause` (`{action: pause|resume, workers}`), `GET /projects/<name>/translate/status`, `GET /projects/<name>/translate/stream` (SSE)
- review: `POST /projects/<name>/segments/<id>` (edit), `…/approve`, `…/retranslate`; `POST /projects/<name>/search-segments`
- export: `POST /projects/<name>/export` (`format`), `GET /projects/<name>/export/status`, `GET /projects/<name>/export/files`, `GET /projects/<name>/download/<filename>`

## Testing and verification

- `python3 -m pytest tests/ -q -p no:cacheprovider -rfw` — must pass with no warnings, also when several suites run at once. Tests wait on conditions (`_wait`, events), not on sleeps. No test makes a real model call.
- `scripts/verify/mutation.sh [BASE]` — mutmut 3.8.1 over the lines changed vs `BASE` in the files listed in `mutation.py`, on a copy of the tree. Needs a Python 3.10+ environment with `requirements-dev.txt` (`MUTATION_VENV=<venv>` to point at one); the project venv may be 3.9.
- `scripts/verify/` — one script per phase claim, each with a docstring:
  - read-only checks of real projects (`--projects-dir`, copies to a temp dir, never writes there; `--anonymize` for publishable output): `phase0_code_segments`, `phase1_load_projects`, `phase2_roundtrip`, `phase2_migrate_copies`, `phase3_export_check`, `phase4_validate_check`, `phase5_glossary_check`, `phase6_segmentation_check`;
  - guards of the test suite itself: `phase1_reverts` (old behaviours put back one by one; each must fail a test) and `phase1_survivor_kills` (recorded mutants must be killed); both work on `git archive HEAD`, so commit first;
  - measurements that make REAL model calls (cost quota, run only on purpose): `phase7_cli_overhead`, `phase7_throughput`, `phase8_brief_sample`, `phase8_extraction_compare`, `phase9_copilot_overhead`.

## Troubleshooting

```bash
ps aux | grep -E 'claude -p|copilot -p' | grep -v grep   # model calls running
pkill -f 'claude -p'                                     # or 'copilot -p'
lsof -ti:3000 | xargs kill                               # web server on port 3000
```

- Model calls time out after 120 s; transient errors are retried within the segment's budget, then the segment is `failed` (see `log/failed/`, retry with `start --retry-failed` or the retry button).
- "project locked" (409 / CLI message): another process (a CLI run, the web task) holds `project.lock`.
- Export or validate refuses a schema 1 project: run `migrate` first.
- Copilot run stopped with "無法確認 Copilot CLI 已套用翻譯用的系統提示": the plugin did not load or the CLI changed; check `copilot --help` for `--experimental`/`--plugin-dir`.
- Web run log: `projects/<name>/log/translation_*.log`.
