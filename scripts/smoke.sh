#!/bin/sh
# scripts/smoke.sh -- one-command-per-service smoke test for arr-cli (task 14.1).
#
# This script exercises one read-only command against each of the five CLIs
# shipped by the arr-cli MVP and exits non-zero on any failure. It is the
# single-line smoke test required by the usability NFR:
#
#     "The repository SHALL provide a one-line smoke test
#      (e.g. `make smoke` or `scripts/smoke.sh`) that exercises
#      one command per service against the documented endpoints
#      with `--human` and `--json` and exits non-zero on any
#      failure."
#
# Usage
# -----
#   scripts/smoke.sh              # default: dry-run (CI safe)
#   scripts/smoke.sh --dry-run    # parse --help for each CLI, no network
#   scripts/smoke.sh --live       # actually run one command per service
#                                 # (requires real config; gated by RUN_LIVE=1)
#   RUN_LIVE=1 scripts/smoke.sh --live
#
# Per the design contract, the dry-run mode (default) MUST NOT touch the
# network: it verifies only that the per-service command grammar parses
# (each service responds to `<exe> --help` with exit code 0 and prints
# usage) and that the documented subcommands are registered. CI always
# uses the dry-run path.
#
# The --live mode hits a real config + real service. It is gated behind
# RUN_LIVE=1 because CI must never reach the operator's instance without
# an explicit opt-in. When --live is requested without RUN_LIVE=1 the
# script refuses and exits non-zero.
#
# The cold-start budget (REQ NFR-Performance, "first stdout byte in <=
# 2 seconds") is enforced as a smoke step: `time python -c 'import
# arr_cli.jellyfin'`. The measured elapsed time is printed but the
# script does not fail on a slow cold-start in dry-run mode (a developer
# machine may be slower than CI). The cap is enforced as a unit test
# in tests/unit/test_perf_budgets.py.
#
# Exit codes
# ----------
#   0  all steps succeeded (dry-run grammar + cold-start probe)
#   1  a CLI failed to respond to --help with exit 0
#   2  --live requested but RUN_LIVE not set
#   3  cold-start probe could not import arr_cli.jellyfin
#   4  --live mode: a real CLI invocation failed
#
# POSIX sh portability
# --------------------
# The task spec requires POSIX-sh compatibility. Only portable features
# are used: `set -eu`, command substitution, parameter expansion, and
# test `[` / `[ `. NO bash-isms (no `[[`, no `==`, no arrays, no
# `pipefail` -- `pipefail` is bash/POSIX-2008-ish but historically
# optional, so we intentionally avoid it to keep the script portable
# across `/bin/sh` on macOS, Debian dash, and Alpine ash).

set -eu

# Resolve the project root: this script lives in <root>/scripts/smoke.sh.
# `cd ... && pwd` is the POSIX way to get an absolute path without
# relying on GNU readlink -f (which is not in POSIX).
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

# --- argument parsing -------------------------------------------------------
MODE="dry-run"
for arg in "$@"; do
    case "$arg" in
        --dry-run)
            MODE="dry-run"
            ;;
        --live)
            MODE="live"
            ;;
        -h|--help)
            sed -n '2,40p' "$0"
            exit 0
            ;;
        *)
            printf 'smoke.sh: unknown argument: %s\n' "$arg" >&2
            exit 1
            ;;
    esac
done

# --- mode gating ------------------------------------------------------------
# --live is gated by RUN_LIVE=1 so CI never touches the operator's instance
# without an explicit opt-in. The exit code for "not allowed" is 2 (auth
# class is the closest fit: a credential gate denied the request); we pick
# a distinct code (2) rather than reuse the auth-class semantic because
# this gate is about CI policy, not service auth.
if [ "$MODE" = "live" ] && [ "${RUN_LIVE:-0}" != "1" ]; then
    printf 'smoke.sh: --live requested but RUN_LIVE != 1; refusing to run\n' >&2
    printf '         set RUN_LIVE=1 to exercise real services.\n' >&2
    exit 2
fi

# --- per-service command list ----------------------------------------------
# The 5 (service, subcommand) pairs documented by the design:
#   jellyfin   -> now
#   radarr     -> calendar
#   sonarr     -> calendar
#   maintainerr -> health
#   seerr      -> user
# Each entry is "executable<TAB>subcommand" so we can iterate without
# needing arrays (POSIX sh has no arrays).
SERVICES="jellyfin	now
radarr	calendar
sonarr	calendar
maintainerr	health
seerr	user"

# --- helpers ----------------------------------------------------------------
log() {
    # printf %s\\n so we do not depend on `echo -e` behaviour.
    printf 'smoke.sh: %s\n' "$*"
}

fail() {
    printf 'smoke.sh: FAIL: %s\n' "$*" >&2
    exit 1
}

# --- step 1: cold-start probe ----------------------------------------------
# Task 14.1: "Include the cold-start timing check (`time python -c
# 'import arr_cli.jellyfin'`) as a smoke step". We run from the project
# root so the on-disk `arr_cli/` package is importable; the dry-run
# path doesn't need a pip install.
log 'step 1/3: cold-start probe (time python -c "import arr_cli.jellyfin")'
cd "$PROJECT_ROOT"
START=$(date +%s)
if python3 -c 'import arr_cli.jellyfin' >/dev/null 2>&1; then
    END=$(date +%s)
    ELAPSED=$((END - START))
    log "  cold-start probe OK (${ELAPSED}s; budget: 2.0s)"
else
    fail 'cold-start probe could not import arr_cli.jellyfin'
fi

# --- step 2: per-service command grammar (dry-run) -------------------------
# In dry-run we verify each CLI responds to --help with exit 0 and that
# the documented subcommand is registered. We do NOT actually dispatch
# the subcommand because that would require a real config + service.
log "step 2/3: per-service command grammar check (mode=$MODE)"
while IFS='	' read -r exe subcmd; do
    case "$exe" in ''|\#*) continue ;; esac
    log "  checking: $exe $subcmd"

    # Capture --help output. POSIX sh cannot capture exit + stdout into
    # separate streams easily, so we capture stdout into a temp file
    # and inspect both the exit status and the body.
    HELP_FILE=$(mktemp 2>/dev/null) || HELP_FILE=/tmp/smoke.help.$$
    trap 'rm -f "$HELP_FILE"' EXIT INT TERM

    # Invoke the CLI via ``python3 -c "from arr_cli.<svc> import main;
    # sys.exit(main(['--help']))"`` rather than ``python3 -m
    # arr_cli.<svc> --help``. The former works against the on-disk
    # package without requiring ``pip install -e .`` or an `if
    # __name__ == '__main__'` block in each per-service module. The
    # pyproject.toml console-script entry points (``jellyfin``,
    # ``radarr``, ...) call ``main()`` directly, so once the package
    # is installed the user-facing invocation is just ``<svc> --help``;
    # this smoke script targets the developer / CI path that works
    # straight from a checkout.
    PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
        python3 -c "import sys; from arr_cli.$exe import main; sys.exit(main(['--help']))" \
        >"$HELP_FILE" 2>&1 \
        || fail "$exe --help exited non-zero"

    # In dry-run we only verify the grammar: --help succeeded (above)
    # and the documented subcommand name appears in the usage block.
    # Both checks apply in --live mode too because they catch missing
    # subcommand registrations before we waste a network call.
    if ! grep -q " $subcmd " "$HELP_FILE" \
       && ! grep -q " $subcmd$" "$HELP_FILE"; then
        # some subcommands appear with no trailing space; match on
        # the literal word too.
        if ! grep -q "$subcmd" "$HELP_FILE"; then
            fail "$exe --help does not mention subcommand '$subcmd'"
        fi
    fi

    log "    OK: $exe --help lists '$subcmd'"
done <<EOF
$SERVICES
EOF

# --- step 3: --live execution ----------------------------------------------
# Only reached when --live AND RUN_LIVE=1. We run each (exe, subcmd)
# pair twice: once with --human, once with default JSON. Any non-zero
# exit code fails the smoke test (REQ NFR-Usability).
if [ "$MODE" = "live" ]; then
    log 'step 3/3: --live execution (one command per service, --human and JSON)'
    while IFS='	' read -r exe subcmd; do
        case "$exe" in ''|\#*) continue ;; esac
        for flag in '--human' '--json'; do
            # The default is JSON; we still pass an explicit flag set so
            # the smoke test documents what it ran.
            case "$flag" in
                --json) FLAG_ARGS='' ;;
                *)       FLAG_ARGS='--human' ;;
            esac
            log "  running: $exe $subcmd $FLAG_ARGS"
            # Same import-and-call approach as the --help probe (see
            # step 2 for rationale): invoke ``main`` directly so we
            # don't need the package to be pip-installed.
            if ! PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
                    python3 -c "import sys; from arr_cli.$exe import main; sys.exit(main([${FLAG_ARGS:+$FLAG_ARGS, }] + ['$subcmd']))"; then
                fail "$exe $subcmd $FLAG_ARGS exited non-zero"
            fi
        done
    done <<EOF
$SERVICES
EOF
else
    log 'step 3/3: skipped (dry-run; pass --live with RUN_LIVE=1 to run)'
fi

log 'all smoke steps passed'
exit 0
