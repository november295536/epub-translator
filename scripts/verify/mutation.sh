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
# afterwards; do not edit or commit while it runs. State is kept in
# .mutmut-cache (git-ignored) and deleted at the start of each run.
# Show one mutant's diff with: "$MUTATION_VENV/bin/mutmut" show <id>
#
# Exit code: 0 when no mutant survived, timed out or is suspicious, else 1.

set -euo pipefail

BASE="${1:-main}"
VENV="${MUTATION_VENV:-}"
if [[ -z "$VENV" || ! -x "$VENV/bin/mutmut" ]]; then
    echo "Set MUTATION_VENV to a virtualenv with requirements.txt and requirements-dev.txt installed" >&2
    exit 2
fi
PY="$VENV/bin/python"
MUTMUT="$VENV/bin/mutmut"

cd "$(git rev-parse --show-toplevel)"
MERGE_BASE="$(git merge-base "$BASE" HEAD)"

# group name | comma-separated files to mutate | pytest arguments
GROUPS=(
    "core|src/core/segmenter.py,src/core/translator.py,src/epub/builder.py,src/epub/extractor.py,src/glossary/glossary_manager.py,src/glossary/term_extractor.py|tests/test_segmenter.py tests/test_translator.py tests/test_builder.py tests/test_extractor.py tests/test_glossary.py tests/test_verify_scripts.py"
    "web|src/web/tasks.py,src/web/blueprints/glossary.py,src/web/blueprints/review.py|tests/test_web.py -k TermExtractionCoverage or RetranslateContext or WorkerContext"
    "cli|translate.py|tests/test_cli.py"
    "scripts|scripts/verify/phase0_code_segments.py,scripts/verify/phase0_builder_alignment.py|tests/test_verify_scripts.py"
)

PATCH="$(mktemp)"
trap 'rm -f "$PATCH"' EXIT

rm -f .mutmut-cache
for group in "${GROUPS[@]}"; do
    IFS='|' read -r name files tests <<<"$group"
    # Working tree versus the merge base, so line numbers match the files mutmut edits
    git diff --no-prefix "$MERGE_BASE" -- ${files//,/ } >"$PATCH"
    if [[ ! -s "$PATCH" ]]; then
        echo "== $name: no changed lines"
        continue
    fi
    if [[ "$tests" == *" -k "* ]]; then
        runner="$PY -m pytest -x -q -p no:cacheprovider ${tests%% -k *} -k '${tests#* -k }'"
    else
        runner="$PY -m pytest -x -q -p no:cacheprovider $tests"
    fi
    echo "== $name: mutating changed lines of $files"
    # mutmut exits non-zero when mutants survive; totals are read from the cache below
    "$MUTMUT" run --paths-to-mutate "$files" --use-patch-file "$PATCH" \
        --runner "$runner" --no-progress >/dev/null || true
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
bad = counts['bad_survived'] + counts['bad_timeout'] + counts['ok_suspicious'] + counts['untested']
raise SystemExit(1 if bad else 0)
EOF
