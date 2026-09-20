#!/bin/sh
# scripts/example-lint.sh -- assert arr.conf.example is placeholder-only (task 15.1).
#
# Implements REQ-1 AC4: the committed example config file MUST contain only
# documented placeholder values, with no real URL, API key, token, or
# user identifier. The actual checking lives in
# :mod:`arr_cli.facade.example_validator`; this shell wrapper exists so
# CI can call the check without booting a Python module path and so
# the contract is documented at the script level (mirroring
# `scripts/secret-scan`).
#
# Usage
# -----
#   scripts/example-lint.sh                  # lint the default example
#   scripts/example-lint.sh path/to/example  # lint an explicit file
#
# Exit codes
# ----------
#   0  the example file is placeholder-only (no violations)
#   1  at least one violation was found (printed to stderr)
#   2  internal error (could not locate the project root, etc.)
#
# POSIX sh portability
# --------------------
# Same constraints as the other CI scripts: no bash-isms, ``set -eu``
# only, no ``[[`` / arrays / pipefail. The Python call is dispatched
# via ``python3 -c`` so no extra entry points need to be packaged.

set -eu

# Resolve project root from this script's location.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_ROOT"

# The default example path is the repo-root committed file. The first
# positional argument (if any) overrides it so ad-hoc calls can lint
# a draft before committing.
EXAMPLE_PATH=${1:-"$PROJECT_ROOT/arr.conf.example"}

log() {
    printf 'example-lint: %s\n' "$*"
}

fail() {
    printf 'example-lint: INTERNAL ERROR: %s\n' "$*" >&2
    exit 2
}

# Confirm python3 is available; otherwise we cannot run the validator.
command -v python3 >/dev/null 2>&1 || fail "python3 not found on PATH"

# Dispatch to the Python validator. We use ``python3 -c`` via stdin
# so the call works regardless of the current working directory; the
# module is imported by absolute package path so ``PYTHONPATH`` tweaks
# are not required. The script does not depend on PyYAML -- the
# validator is deliberately format-agnostic and only inspects raw text
# -- so this call works on a stdlib-only Python build.
#
# We capture the exit code without ``set -e`` tripping us up by
# wrapping the call in a conditional; the python script itself emits
# the violation lines on stderr before exiting non-zero, so the user
# sees diagnostics even when the call fails.
if python3 - "$EXAMPLE_PATH" <<'PY'
import sys

from arr_cli.facade.example_validator import validate_example_config

path = sys.argv[1]
violations = validate_example_config(path)
if violations:
    for line in violations:
        print(line, file=sys.stderr)
    sys.exit(1)
sys.exit(0)
PY
then
    log "OK: $EXAMPLE_PATH contains only documented placeholders"
    exit 0
else
    printf 'example-lint: FAIL: %s contains non-placeholder values; refusing to pass the build.\n' "$EXAMPLE_PATH" >&2
    printf '                Replace real values with the documented placeholders (YOUR_API_KEY_HERE, <user-id>, https://example.com) and re-run.\n' >&2
    exit 1
fi
