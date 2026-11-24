# Changelog

Newest first. Each pull request that changes `src/`, `translate.py` or
`requirements*.txt` raises `src.__version__` and adds its entry here (see
"Releases" in CLAUDE.md).

## 2.0.1

- Housekeeping: the CLI version tests follow `src.__version__`, and README
  names the license by its identifier (`AGPL-3.0-only`, see `LICENSE`). No
  change in behaviour.

## 2.0.0

The improvement plan (`plan/02-improvement-plan.md`) and run records.

- Standalone fixes and foundations: typed segment states and outcomes, a
  backend interface, project schema 2 with `translate.py migrate`.
- Document and block model: chapters as blocks with fingerprints; segments
  are block ranges, a block is never split.
- ZIP export from the source EPUB: only translated chapters and the package
  files change; chapters that no longer match stay in the original language.
- Protection, validation and retry: attributes replaced by keys, structural
  checks with one retry and one split per segment.
- Glossary compliance and review surfacing: person names follow the
  glossary; issues shown on the review pages.
- Segmentation for technical books: headings join their text, code without
  comments passes through.
- Runtime control, speed and a second backend: coordinator and workers,
  pause/resume, rate-limit waits, debounced saves, Copilot CLI.
- Extraction quality and a per-book brief; Copilot CLI completion.
- Reasoning effort and call timeout per project; Python 3.12 or newer.
- Copilot SDK backend.
- Run records: a persistent process log (`logs/web.log`, `logs/cli.log`), a
  per-project event record without book text (`log/events.jsonl`), the
  `translate.py stats` command and the web statistics page; a fix for a
  resume right after a run's start that could leave a run that never ends.

## 1.0.0

The version tagged v1.0.0.
