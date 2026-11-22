#!/usr/bin/env bash
# Mutation testing over the lines changed on this branch (mutmut 3.8.1).
#
#   source venv/bin/activate    (the project environment, both requirements files)
#   scripts/verify/mutation.sh [BASE]       (default BASE: main)
#
# Runs scripts/verify/mutation.py (see its docstring) on the active
# environment's python3. The repository is not edited: mutmut works on a
# copy in a temporary directory, removed at the end.
#
# Exit code: 0 when every mutant was killed, 1 when some survived, timed out,
# are suspicious or untested, 2 when the interpreter is too old, mutmut is
# missing or failed, or no mutants were generated.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
# The project's own version check first: it prints why this interpreter cannot run it
if ! python3 -c 'import src'; then
    exit 2
fi
if ! python3 -c 'import mutmut, pytest' 2>/dev/null; then
    echo "mutmut not found: pip install -r requirements.txt -r requirements-dev.txt" >&2
    echo "in the project environment, and activate it" >&2
    exit 2
fi

exec python3 scripts/verify/mutation.py "${1:-main}"
