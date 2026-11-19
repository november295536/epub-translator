#!/usr/bin/env bash
# Mutation testing over the lines changed on this branch.
#
# Mutates only the lines added or changed versus BASE (default: main) in the
# source files listed in MUT_GROUPS below, and runs only the tests relevant to
# each group, so a run takes minutes. Each group gets its own mutmut cache, so
# its clean baseline is run and timed with that group's tests. Prints every
# surviving, timed-out or suspicious mutant (with its diff) and the totals.
#
# Uses mutmut 2.x from the active environment:
#
#   pip install -r requirements-dev.txt
#   scripts/verify/mutation.sh [BASE]
#
# MUTATION_VENV=<virtualenv> uses that virtualenv's mutmut instead. Tests run
# on the Python interpreter that mutmut is installed in (read from its
# script's #! line).
#
# mutmut edits the source files in place while it runs and restores them
# afterwards; do not edit or commit while it runs. On exit, including Ctrl-C,
# SIGTERM or a mutmut failure, the script stops mutmut and its test processes,
# puts back any file mutmut had mutated (from mutmut's <file>.bak) and warns if
# a mutated file is still modified. mutmut keeps its cache in .mutmut-cache in
# the repository root (git-ignored), deleted after each group and on exit. The
# patch, runner, pytest and TMPDIR temporary files and the bytecode cache go to
# a private directory from mktemp -d, removed on exit.
#
# Exit code: 0 when no mutant survived, timed out or is suspicious, 1 when
# some did, 2 when mutmut failed or generated no mutants.

set -euo pipefail

BASE="${1:-main}"
MUTMUT="$(command -v "${MUTATION_VENV:+$MUTATION_VENV/bin/}mutmut" || true)"
PY="$( [[ -n "$MUTMUT" ]] && head -n 1 "$MUTMUT" | sed 's/^#!//' || true)"
if [[ -z "$MUTMUT" ]] || ! "$PY" -c 'import mutmut, pytest' 2>/dev/null; then
    echo "mutmut not found with a usable interpreter: pip install -r requirements-dev.txt" >&2
    echo "in the active environment, or set MUTATION_VENV to a virtualenv that has it" >&2
    exit 2
fi

cd "$(git rev-parse --show-toplevel)"
MERGE_BASE="$(git merge-base "$BASE" HEAD)"

# group name | comma-separated files to mutate | pytest arguments
# (not named GROUPS: that is a read-only bash variable)
MUT_GROUPS=(
    "core|src/core/project.py,src/core/segmenter.py,src/core/translator.py,src/epub/builder.py,src/epub/extractor.py,src/glossary/glossary_manager.py,src/glossary/term_extractor.py|tests/test_project.py tests/test_segmenter.py tests/test_translator.py tests/test_builder.py tests/test_extractor.py tests/test_glossary.py tests/test_verify_scripts.py"
    "llm|src/llm/__init__.py,src/llm/base.py,src/llm/cli.py,src/llm/claude_cli.py,src/llm/fake.py|tests/test_llm.py tests/test_translator.py tests/test_glossary.py"
    "web|src/web/tasks.py,src/web/server.py,src/web/blueprints/glossary.py,src/web/blueprints/review.py,src/web/blueprints/search.py,src/web/blueprints/translation.py|tests/test_web.py -k TermExtractionCoverage or RetranslateContext or WorkerContext or CreateProjectMarker or WorkerFailedSegment or EditDuringTranslation or RetranslateOutcome or WebProjectLock or FailedInPages or TermExtractionModel or RateLimitPause or RetranslateModel or SegmentCompleteModel or MultiWorker or SearchSegments or StartWithSegmentIds or WorkerEventsAndLogs or RetranslateDetails or LockedSaveResponse"
    "cli|translate.py|tests/test_cli.py"
    "scripts|scripts/verify/phase0_code_segments.py,scripts/verify/phase0_builder_alignment.py,scripts/verify/phase1_load_projects.py|tests/test_verify_scripts.py"
)

ALL_FILES=()
for group in "${MUT_GROUPS[@]}"; do
    IFS='|' read -r _ files _ <<<"$group"
    IFS=',' read -r -a list <<<"$files"
    ALL_FILES+=("${list[@]}")
done
# mutmut rewrites these files in place; refuse to start on uncommitted edits
# or on a leftover backup from an interrupted run
if [[ -n "$(git status --porcelain -- "${ALL_FILES[@]}")" ]]; then
    echo "Uncommitted changes in the files to mutate; commit or restore them first" >&2
    exit 2
fi
for f in "${ALL_FILES[@]}"; do
    if [[ -e "$f.bak" ]]; then
        echo "Leftover $f.bak from an interrupted run: restore $f and remove it first" >&2
        exit 2
    fi
done

WORK="$(mktemp -d)"
MUTPID=""
cleanup() {
    trap '' INT TERM  # a second Ctrl-C must not cut the cleanup short
    if [[ -n "$MUTPID" ]]; then
        # mutmut runs in its own process group (set -m): stop it and its tests
        kill -TERM -- "-$MUTPID" 2>/dev/null || true
        wait "$MUTPID" 2>/dev/null || true
        sleep 1
        kill -KILL -- "-$MUTPID" 2>/dev/null || true
    fi
    # The start-up guard ensures no .bak existed before this run
    for f in "${ALL_FILES[@]}"; do
        if [[ -e "$f.bak" ]]; then
            mv -f "$f.bak" "$f"
            echo "restored $f" >&2
        fi
    done
    if [[ -n "$(git status --porcelain -- "${ALL_FILES[@]}")" ]]; then
        echo "WARNING files still modified: git status -- ${ALL_FILES[*]}" >&2
    fi
    rm -f .mutmut-cache
    rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

mkdir "$WORK/tmp" "$WORK/pycache"
export TMPDIR="$WORK/tmp"
# Never read or write cached bytecode: a mutant and the restored file can have
# the same size and mtime second.
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="$WORK/pycache"
PATCH="$WORK/changes.patch"
RUNNER="$WORK/runner.sh"
TOTALS="$WORK/totals"
set -m  # background jobs get their own process group

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
    # test collection (pytest exit 2-5) would be reported as survived: the
    # runner maps every failure to 1. It is a file because mutmut runs the
    # command both with and without a shell. On a timeout mutmut kills only
    # the wrapper, so pytest runs under a watchdog that exits as soon as its
    # parent is gone (otherwise an infinite-loop mutant keeps the output pipe
    # open and the run hangs).
    cat >"$RUNNER" <<EOF
#!/bin/sh
"$PY" -c '
import os, sys, threading, time
parent = os.getppid()
def watch():
    while True:
        time.sleep(0.5)
        if os.getppid() != parent:
            os._exit(1)
threading.Thread(target=watch, daemon=True).start()
import pytest
sys.exit(pytest.main(sys.argv[1:]))
' -x -q -p no:cacheprovider --basetemp="$WORK/pytest" $pytest_args || exit 1
EOF
    chmod +x "$RUNNER"
    echo "== $name: mutating changed lines of $files"
    rm -f .mutmut-cache  # fresh cache: baseline run and timed for this group's tests
    # --CI: exit 1 only on a fatal error; mutant results are read from the cache below
    "$MUTMUT" run --paths-to-mutate "$files" --use-patch-file "$PATCH" \
        --runner "$RUNNER" --no-progress --CI >/dev/null &
    MUTPID=$!
    if ! wait "$MUTPID"; then
        # MUTPID stays set: cleanup stops any test process left in its group
        echo "ERROR mutmut failed for group $name" >&2
        exit 2
    fi
    MUTPID=""

    "$PY" - "$name" "$TOTALS" >"$WORK/listing" <<'EOF'
import sqlite3, sys
name, totals = sys.argv[1], sys.argv[2]
rows = sqlite3.connect('.mutmut-cache').execute('''
    SELECT m.id, m.status, s.filename, l.line_number
    FROM Mutant m JOIN Line l ON m.line = l.id JOIN SourceFile s ON l.sourcefile = s.id
    ORDER BY s.filename, l.line_number, m."index"
''').fetchall()
names = {'bad_survived': 'survived', 'bad_timeout': 'timeout',
         'ok_suspicious': 'suspicious', 'untested': 'untested'}
for status in names:
    for mid, st, filename, line in rows:
        if st == status:
            # mutmut stores 0-based line numbers
            print(f'{names[st]:10} {name} id {mid:4}  {filename}:{line + 1}')
with open(totals, 'a') as f:
    for _, st, _, _ in rows:
        f.write(st + '\n')
EOF
    while read -r status group_name _ mid location; do
        echo "$status $group_name id $mid  $location"
        "$MUTMUT" show "$mid" </dev/null | sed -n '/^---/,$p' | sed 's/^/    /'
    done <"$WORK/listing"
    rm -f .mutmut-cache
done

"$PY" - "$TOTALS" <<'EOF'
import os, sys
from collections import Counter
path = sys.argv[1]
counts = Counter(open(path).read().split()) if os.path.exists(path) else Counter()
total = sum(counts.values())
print(f"TOTAL mutants {total}  killed {counts['ok_killed']}  "
      f"survived {counts['bad_survived']}  timeout {counts['bad_timeout']}  "
      f"suspicious {counts['ok_suspicious']}  untested {counts['untested']}")
if not total:
    print('ERROR no mutants were generated')
    raise SystemExit(2)
bad = counts['bad_survived'] + counts['bad_timeout'] + counts['ok_suspicious'] + counts['untested']
raise SystemExit(1 if bad else 0)
EOF
