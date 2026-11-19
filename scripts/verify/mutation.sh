#!/usr/bin/env bash
# Mutation testing over the lines changed on this branch.
#
# Mutates only the lines added or changed versus BASE (default: main) in the
# source files listed in GROUPS below, and runs only the tests relevant to
# each group, so a run takes minutes. Prints killed / survived / timeout /
# suspicious totals and lists every surviving mutant.
#
# Uses mutmut 2.x (supports Python 3.9). Install it in a separate virtualenv,
# not the project one:
#
#   python3.9 -m venv "$TMPDIR/mutvenv"
#   "$TMPDIR/mutvenv/bin/pip" install -r requirements.txt -r requirements-dev.txt
#   MUTATION_VENV="$TMPDIR/mutvenv" scripts/verify/mutation.sh [BASE]
#
# mutmut edits the source files in place while it runs and restores them
# afterwards; do not edit or commit while it runs. If a run is killed, a
# mutated file and its .bak can be left behind (deliberately not ignored by
# git, so they show up); restore with git checkout -- <file>. State is kept in
# .mutmut-cache (git-ignored) and deleted at the start of each run.
# Show one mutant's diff with: "$MUTATION_VENV/bin/mutmut" show <id>
#
# Exit code: 0 when no mutant survived, timed out or is suspicious, 1 when
# some did, 2 when mutmut failed or generated no mutants.

set -euo pipefail

BASE="${1:-main}"
VENV="${MUTATION_VENV:-}"
if [[ -z "$VENV" || ! -x "$VENV/bin/mutmut" ]]; then
    echo "Set MUTATION_VENV to a virtualenv with requirements.txt and requirements-dev.txt installed" >&2
    exit 2
fi
PY="$VENV/bin/python"
# A mutant and the restored file can have the same size and mtime second, so
# bytecode cached for one can be loaded for the other (on macOS the cache is
# under sys.pycache_prefix, not __pycache__). Never write bytecode in this run.
export PYTHONDONTWRITEBYTECODE=1
MUTMUT="$VENV/bin/mutmut"

cd "$(git rev-parse --show-toplevel)"
MERGE_BASE="$(git merge-base "$BASE" HEAD)"

# group name | comma-separated files to mutate | pytest arguments
# (not named GROUPS: that is a read-only bash variable)
MUT_GROUPS=(
    "core|src/core/segmenter.py,src/core/translator.py,src/epub/builder.py,src/epub/extractor.py,src/glossary/glossary_manager.py,src/glossary/term_extractor.py|tests/test_segmenter.py tests/test_translator.py tests/test_builder.py tests/test_extractor.py tests/test_glossary.py tests/test_verify_scripts.py"
    "web|src/web/tasks.py,src/web/blueprints/glossary.py,src/web/blueprints/review.py|tests/test_web.py -k TermExtractionCoverage or RetranslateContext or WorkerContext"
    "cli|translate.py|tests/test_cli.py"
    "scripts|scripts/verify/phase0_code_segments.py,scripts/verify/phase0_builder_alignment.py|tests/test_verify_scripts.py"
)

PATCH="$(mktemp)"
RUNNER="$(mktemp)"
trap 'rm -f "$PATCH" "$RUNNER"' EXIT
chmod +x "$RUNNER"

ALL_FILES=""
for group in "${MUT_GROUPS[@]}"; do
    IFS='|' read -r _ files _ <<<"$group"
    ALL_FILES="$ALL_FILES ${files//,/ }"
done
# mutmut rewrites these files in place; refuse to start on uncommitted edits
# or on a leftover backup from an interrupted run
if [[ -n "$(git status --porcelain -- $ALL_FILES)" ]] || ls $(printf '%s.bak ' $ALL_FILES) >/dev/null 2>&1; then
    echo "Uncommitted changes or *.bak files in the files to mutate; after an interrupted" >&2
    echo "run restore them with: git checkout -- <file> && rm <file>.bak" >&2
    exit 2
fi

rm -f .mutmut-cache
for group in "${MUT_GROUPS[@]}"; do
    IFS='|' read -r name files tests <<<"$group"
    [[ -n "$files" ]] || { echo "empty file list in group $name" >&2; exit 2; }
    # Working tree versus the merge base, so line numbers match the files mutmut edits
    git diff --no-prefix "$MERGE_BASE" -- ${files//,/ } >"$PATCH"
    if [[ ! -s "$PATCH" ]]; then
        echo "== $name: no changed lines"
        continue
    fi
    if [[ "$tests" == *" -k "* ]]; then
        pytest_args="${tests%% -k *} -k '${tests#* -k }'"
    else
        pytest_args="$tests"
    fi
    # mutmut 2.x counts only exit code 1 as killed, so a mutant that breaks
    # test collection (pytest exit 2-5) would be reported as survived: map
    # every failure to 1. A runner file, because mutmut runs the command both
    # with and without a shell.
    printf '#!/bin/sh\n"%s" -m pytest -x -q -p no:cacheprovider %s || exit 1\n' \
        "$PY" "$pytest_args" >"$RUNNER"
    echo "== $name: mutating changed lines of $files"
    # --CI: exit 1 only on a fatal error; mutant results are read from the cache below
    if ! "$MUTMUT" run --paths-to-mutate "$files" --use-patch-file "$PATCH" \
        --runner "$RUNNER" --no-progress --CI >/dev/null; then
        echo "ERROR mutmut failed for group $name" >&2
        exit 2
    fi
done

"$PY" - <<'EOF'
import sqlite3
from collections import Counter

db = sqlite3.connect('.mutmut-cache')
rows = db.execute('''
    SELECT m.id, m.status, s.filename, l.line_number
    FROM Mutant m JOIN Line l ON m.line = l.id JOIN SourceFile s ON l.sourcefile = s.id
    ORDER BY s.filename, l.line_number, m."index"
''').fetchall()
counts = Counter(status for _, status, _, _ in rows)
names = {'ok_killed': 'killed', 'bad_survived': 'survived', 'bad_timeout': 'timeout',
         'ok_suspicious': 'suspicious', 'untested': 'untested', 'skipped': 'skipped'}
for status in ('bad_survived', 'bad_timeout', 'ok_suspicious', 'untested'):
    for mid, st, filename, line in rows:
        if st == status:
            print(f'{names[st]:10} id {mid:4}  {filename}:{line}')
print(f"TOTAL mutants {len(rows)}  killed {counts['ok_killed']}  "
      f"survived {counts['bad_survived']}  timeout {counts['bad_timeout']}  "
      f"suspicious {counts['ok_suspicious']}  untested {counts['untested']}")
if not rows:
    print('ERROR no mutants were generated')
    raise SystemExit(2)
bad = counts['bad_survived'] + counts['bad_timeout'] + counts['ok_suspicious'] + counts['untested']
raise SystemExit(1 if bad else 0)
EOF
