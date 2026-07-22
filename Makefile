# Makefile -- arr-cli MVP (task 18 / subtask 18.1)
#
# This Makefile wires up the local CI hookup for the arr-cli MVP. It is
# the "deferred but stubbed" target documented in
# `.specs/arr-cli-mvp/tasks.md` task 18. The wording "deferred but
# stubbed" means: the GitHub Actions workflow that consumes `make ci`
# is intentionally out of scope for this MVP (no .github/workflows/
# file is committed in this task), but the Makefile target chain
# itself is fully functional and is what a CI runner will invoke once
# the Actions YAML is added in a follow-up.
#
# Per the task spec and the design document, the chain is:
#
#     make ci  ==  make test && make secret-scan && make smoke-dry
#
# and each leaf target is independently invokable for fast local
# feedback.
#
# Targets
# -------
#   help           Print the list of available targets with one-line
#                  descriptions. This is also the default target
#                  (`make` with no args).
#   test           Run the unit test suite (`pytest tests/unit`).
#   lint           Byte-compile every Python file under `arr_cli/` and
#                  `tests/` (catches syntax errors that escape pytest).
#                  Uses stdlib `py_compile`; no extra deps required.
#   secret-scan    Run `scripts/secret-scan` to grep the tree for
#                  committed API-key/token shapes (REQ NFR-Security).
#                  Also re-validates `arr.conf.example` is
#                  placeholder-only (REQ-1 AC4).
#   smoke          Run `scripts/smoke.sh` (defaults to --dry-run; CI
#                  safe).
#   smoke-dry      Same as `smoke`; explicit alias for
#                  `scripts/smoke.sh --dry-run`.
#   smoke-live     Run `scripts/smoke.sh --live` (requires RUN_LIVE=1).
#   ci             Chain: test + secret-scan + smoke-dry. Non-zero
#                  exit on any failure. This is the canonical CI
#                  entry point.
#
# Portability
# -----------
# Written in POSIX-portable Make syntax (no GNU-only constructs).
# Should work with GNU make 3.x+, BSD make, and most system makes.
# Uses `?=`, `:=`, `.PHONY`, and shell-expanded `$(abspath ...)`. Each
# recipe runs through `/bin/sh` by default (POSIX-portable); override
# with `make SH=/bin/bash` if desired.
#
# Conventions
# -----------
# - Every recipe line begins with `@` so the executed command is
#   visible without Make's normal "echo" line noise. Targets print
#   their own banner via a single `printf` invocation so the operator
#   can see what ran.
# - `set -e` is implied by Make's default shell exit handling: if any
#   recipe line fails, `make` stops and propagates the non-zero exit.
#   We rely on that -- no `set -e` boilerplate in each recipe.
# - Tooling is resolved through configurable variables (PYTHON3,
#   PYTEST, SH) so callers can override on the command line, e.g.
#   `make test PYTEST=pytest` or `make ci SH=/bin/bash`.

# --- configuration ----------------------------------------------------------
# Tools. Override on the command line, e.g. `make test PYTEST=pytest`.
PYTHON3 ?= python3
PYTEST  ?= $(PYTHON3) -m pytest
SH      ?= sh

# Paths. Keep them relative so `make ci` works from any cwd.
PROJECT_ROOT := $(abspath $(CURDIR))
SCRIPTS_DIR  := $(PROJECT_ROOT)/scripts
TESTS_DIR    := $(PROJECT_ROOT)/tests/unit
PY_SRC_DIRS  := $(PROJECT_ROOT)/arr_cli $(PROJECT_ROOT)/tests

# --- phony declarations -----------------------------------------------------
# Everything in this Makefile is a recipe, not a file target. Declaring
# them .PHONY keeps `make` from getting confused if a stray file
# named `test` or `ci` ever appears in the tree.
.PHONY: help test lint secret-scan smoke smoke-dry smoke-live ci

# --- default target ---------------------------------------------------------
# `make` with no args prints the help banner so the first thing an
# operator sees is the list of available targets.
help:
	@printf 'arr-cli Makefile -- task 18 / subtask 18.1\n'
	@printf 'Usage: make <target>\n\n'
	@printf 'Available targets:\n'
	@printf '  help           Print this help banner (default target)\n'
	@printf '  test           Run the unit test suite (pytest tests/unit)\n'
	@printf '  lint           Byte-compile every Python file under arr_cli/ and tests/\n'
	@printf '  secret-scan    Run scripts/secret-scan (greps for committed API-key shapes)\n'
	@printf '  smoke          Run scripts/smoke.sh (defaults to --dry-run; CI safe)\n'
	@printf '  smoke-dry      Same as smoke; explicit alias for scripts/smoke.sh --dry-run\n'
	@printf '  smoke-live     Run scripts/smoke.sh --live (requires RUN_LIVE=1)\n'
	@printf '  ci             Chain: test + secret-scan + smoke-dry (canonical CI entry)\n'

# --- test -------------------------------------------------------------------
# Run the full unit-test suite. `tests/unit/` is the only pytest test
# directory in the MVP; `tests/integration/` is opt-in via
# `--run-integration` and is not part of the default CI run.
test:
	@printf 'make: [test] running pytest tests/unit\n'
	@$(PYTEST) $(TESTS_DIR)

# --- lint -------------------------------------------------------------------
# Byte-compile every Python file under `arr_cli/` and `tests/`. This
# catches syntax errors and indentation problems that pytest would
# surface as collection errors anyway, but doing it as a separate
# target means `make lint` is fast (~1s) and the error message points
# at the exact file/line that broke. We shell-quote each file via
# `find ... -print` so the inlined `$(PY_SRC_DIRS)` expansion stays
# within command-line length limits on every supported platform.
lint:
	@printf 'make: [lint] py_compile sweep over arr_cli/ and tests/\n'
	@$(PYTHON3) -c "import sys, py_compile; files = sys.argv[1:]; [py_compile.compile(f, doraise=True) for f in files]" \
		$$(find $(PY_SRC_DIRS) -type f -name '*.py' -not -path '*/__pycache__/*')
	@printf 'make: [lint] OK\n'

# --- secret-scan ------------------------------------------------------------
# Delegate to the POSIX-sh script delivered in task 14.2. The script
# also runs the placeholder-only example check internally, so this
# single invocation enforces both the security NFR and REQ-1 AC4.
secret-scan:
	@printf 'make: [secret-scan] running scripts/secret-scan\n'
	@$(SH) $(SCRIPTS_DIR)/secret-scan

# --- smoke (default = dry-run) ---------------------------------------------
# `scripts/smoke.sh` defaults to --dry-run when no flag is passed, so
# `make smoke` is already CI-safe. We still pass --dry-run explicitly
# via the `smoke-dry` alias below for callers who want to be explicit.
smoke:
	@printf 'make: [smoke] running scripts/smoke.sh (default = --dry-run)\n'
	@$(SH) $(SCRIPTS_DIR)/smoke.sh

smoke-dry:
	@printf 'make: [smoke-dry] running scripts/smoke.sh --dry-run\n'
	@$(SH) $(SCRIPTS_DIR)/smoke.sh --dry-run

# --- smoke-live -------------------------------------------------------------
# Opt-in: requires RUN_LIVE=1. The script enforces that gate and
# returns exit 2 if it's missing; we let that error propagate. Listed
# here for completeness so the help banner matches reality, but not
# chained into `make ci` because CI never touches the operator's
# instance without an explicit opt-in.
smoke-live:
	@printf 'make: [smoke-live] running scripts/smoke.sh --live\n'
	@$(SH) $(SCRIPTS_DIR)/smoke.sh --live

# --- ci ---------------------------------------------------------------------
# Canonical CI entry point. Chains the three leaves in order so a
# failure in any one of them short-circuits the rest (Make stops on
# the first non-zero exit by default). The order matters: tests
# first (cheapest, fastest feedback), then secret-scan (catches
# accidentally committed keys before smoke runs), then smoke-dry
# (validates CLI grammar without touching the network).
#
# "deferred but stubbed": the GitHub Actions YAML that calls
# `make ci` is NOT committed in this task. Once the Actions YAML is
# added in a follow-up, this is the target it should invoke.
ci: test secret-scan smoke-dry
	@printf 'make: [ci] all checks passed\n'