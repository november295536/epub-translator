#!/usr/bin/env bash
# Mutation testing over the lines changed on this branch (mutmut 3.8.1).
#
#   pip install -r requirements-dev.txt     (in an environment on Python 3.10+)
#   scripts/verify/mutation.sh [BASE]       (default BASE: main)
#
# Runs scripts/verify/mutation.py (see its docstring) on the Python
# interpreter that mutmut is installed in (read from its script's #! line).
# MUTATION_VENV=<virtualenv> uses that virtualenv's mutmut instead. The
# repository is not edited: mutmut works on a copy in a temporary directory,
# removed at the end.
#
# Exit code: 0 when every mutant was killed, 1 when some survived, timed out,
# are suspicious or untested, 2 when mutmut failed or generated no mutants.

set -euo pipefail

MUTMUT="$(command -v "${MUTATION_VENV:+$MUTATION_VENV/bin/}mutmut" || true)"
PY="$( [[ -n "$MUTMUT" ]] && head -n 1 "$MUTMUT" | sed 's/^#!//' || true)"
if [[ -z "$MUTMUT" ]] || ! "$PY" -c 'import mutmut, pytest' 2>/dev/null; then
    echo "mutmut not found with a usable interpreter: pip install -r requirements-dev.txt" >&2
    echo "in the active environment, or set MUTATION_VENV to a virtualenv that has it" >&2
    exit 2
fi

cd "$(git rev-parse --show-toplevel)"
exec "$PY" scripts/verify/mutation.py "${1:-main}"
