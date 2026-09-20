"""Regression tests for ``fix-config-flag-ordering``.

The bug closed by this fix had two independent surfaces:

1. **Bug 1 / Bug 3** -- the universal flag set
   (``--config``, ``--debug``, ``--quiet``, ``--human``/``-h``,
   ``--verbose``, ``--connect-timeout``, ``--read-timeout``,
   ``--retry``, ``--deadline``, ``--limit``) was only registered on
   the top-level argparse parser, so the documented invocation
   ``<cli> <subcommand> --config <path>`` was rejected by argparse
   and the universal flags did not appear in ``<subcommand> --help``.

2. **Bug 2** -- argparse's ``SystemExit(2)`` propagate-through
   collided with the documented ``AuthError`` exit code (``2``),
   so any operator script branching on the five stable exit codes
   mis-classified a parse failure as an auth failure.

The tests below lock the post-fix behaviour in place using
**subprocess invocation** of the per-service console scripts
(matching the pattern in ``tests/unit/test_perf_budgets.py`` --
in-process ``main()`` calls would not exercise the
``argparse`` ``parents=`` plumbing that the bug hides behind).
The tests are hermetic: every invocation points ``--config`` at a
path that does not exist on disk, so the loader fails with a
``ConfigError`` (``exit_code == 1``) immediately after argparse
parses the arguments. No network, no live HTTP.

Per the bug review's testing strategy the coverage is:

* **Both arg orders** for every CLI's representative subcommand
  (``--config`` BEFORE and AFTER the subcommand).
* **Subcommand ``--help``** lists every universal flag for every
  CLI.
* **Top-level ``--help``** still lists every universal flag
  (regression guard for the existing top-level surface).
* **Three classes of argparse error** (unknown flag, unknown
  subcommand, missing required positional) all surface as
  exit code ``1`` with the structured
  ``service=config op=parse message=...`` stderr line.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

# Make the project importable regardless of the test runner's CWD.
_PROJ_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))


# ---------------------------------------------------------------------------
# Documented surfaces
# ---------------------------------------------------------------------------

#: Every CLI shipped by arr-cli. Each (cli, subcommand) pair is
#: tested for the documented "flag before subcommand" and "flag
#: after subcommand" forms. The subcommands here are the no-positional
#: ones so the test cannot fail on a missing positional instead of
#: the flag-ordering regression.
SERVICES: tuple[tuple[str, str], ...] = (
    ("jellyfin", "now"),
    ("radarr", "wanted"),
    ("sonarr", "wanted"),
    ("maintainerr", "health"),
    ("seerr", "request-count"),
)

#: The full set of universal-flag option strings that must appear in
#: every subcommand's ``--help`` output. ``-h`` is the documented
#: short alias for ``--human`` and is tested separately.
UNIVERSAL_FLAGS: tuple[str, ...] = (
    "--config",
    "--debug",
    "--quiet",
    "--human",
    "--verbose",
    "--connect-timeout",
    "--read-timeout",
    "--retry",
    "--deadline",
    "--limit",
)

#: Wall-clock budget for every subprocess invocation. argparse + the
#: facaded config-loader are CPU-fast; 15 s is the same safety net
#: used by ``tests/unit/test_perf_budgets.py``.
SUBPROCESS_TIMEOUT_SECONDS: int = 15

#: Path that does not exist on disk. Pointing ``--config`` here
#: routes the post-parse flow through ``ConfigError`` (loader says
#: "file not found") without any network activity, so the test stays
#: hermetic and the exit code is the documented ``1``.
MISSING_CONFIG_PATH: str = "/tmp/does-not-exist-arr-cli-argparse-test.conf"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_root() -> Path:
    """Return the absolute path to ``/projects/arr-cli``."""
    return _PROJ_ROOT


def _subprocess_env() -> dict[str, str]:
    """Return a clean env for the subprocess so imports work.

    Mirrors ``tests/unit/test_perf_budgets._subprocess_env`` so the
    test stays hermetic and the package is importable without
    touching the developer's shell.
    """
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "PYTHONPATH": str(_project_root()),
    }


def _run_cli(cli: str, argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ``<cli> <argv>`` via ``python -c`` and return the result.

    The invocation pattern matches the existing
    ``tests/unit/test_perf_budgets.py`` tests: a one-liner that
    imports the per-service module's ``main`` and calls it with an
    explicit argv list. Wrapping the call in ``sys.exit(main(...))``
    surfaces the documented exit code as the subprocess's return
    code so the test can assert on it (the ``main_wrapper`` return
    value is NOT the process exit code without the ``sys.exit``
    wrapper -- the `-c` script simply terminates after the
    ``main()`` call with the implicit return of ``None``).
    """
    argv_literal = ", ".join(repr(a) for a in argv)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                f"import sys; from arr_cli.{cli} import main; "
                f"sys.exit(main([{argv_literal}]))"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        env=_subprocess_env(),
        cwd=str(_project_root()),
    )


def _structured_parse_line(stderr: str) -> str | None:
    """Return the structured stderr line for a successful parse error.

    The bug review's acceptance criterion calls out the shape
    ``service=config op=parse message=...``; this helper pulls the
    first matching line so individual tests can assert on the
    specific key=value tokens.
    """
    for line in stderr.splitlines():
        if line.startswith("service=config op=parse message="):
            return line
    return None


# ---------------------------------------------------------------------------
# Bug 1 / Bug 3: --config accepted in both arg orders, on every CLI
# ---------------------------------------------------------------------------


class TestConfigFlagBothArgOrders(unittest.TestCase):
    """``--config`` is accepted both BEFORE and AFTER the subcommand.

    The pre-fix behaviour was: ``jellyfin now --config X`` raised
    argparse's ``unrecognized arguments`` and exited ``2``. The
    post-fix behaviour recognises ``--config`` after the subcommand
    too (because the universal parent is shared via ``parents=``),
    and routes the inevitable ``load_config`` failure through
    ``ConfigError`` (exit code ``1``) so the loader's existing
    structured stderr line is what the test asserts on.
    """

    def test_config_after_subcommand_accepted(self) -> None:
        for cli, sub in SERVICES:
            with self.subTest(cli=cli, sub=sub):
                completed = _run_cli(cli, [sub, "--config", MISSING_CONFIG_PATH])
                self.assertEqual(
                    completed.returncode,
                    1,
                    (
                        f"{cli} {sub} --config {MISSING_CONFIG_PATH} "
                        f"exited {completed.returncode} (expected 1); "
                        f"stderr={completed.stderr!r}"
                    ),
                )
                # The loader's structured line must be present so an
                # operator can confirm argparse parsed --config
                # (the load_config ConfigError is what fires here).
                self.assertIn(
                    "service=config op=load",
                    completed.stderr,
                    (
                        f"{cli} {sub} --config ...: expected the "
                        f"ConfigError line; got stderr={completed.stderr!r}"
                    ),
                )
                # stdout must be empty (pipe-cleanliness contract).
                self.assertEqual(
                    completed.stdout,
                    "",
                    (
                        f"{cli} {sub} --config ...: stdout must be empty "
                        f"on a parse-then-load failure; got "
                        f"stdout={completed.stdout!r}"
                    ),
                )

    def test_config_before_subcommand_still_accepted(self) -> None:
        # The pre-fix behaviour that DID work (the only working form
        # before the patch). The bug fix must preserve it: a
        # regression that breaks the canonical documented form is
        # caught here.
        for cli, sub in SERVICES:
            with self.subTest(cli=cli, sub=sub):
                completed = _run_cli(cli, ["--config", MISSING_CONFIG_PATH, sub])
                self.assertEqual(
                    completed.returncode,
                    1,
                    (
                        f"{cli} --config {MISSING_CONFIG_PATH} {sub} "
                        f"exited {completed.returncode} (expected 1); "
                        f"stderr={completed.stderr!r}"
                    ),
                )
                self.assertIn(
                    "service=config op=load",
                    completed.stderr,
                    (
                        f"{cli} --config ... {sub}: expected the "
                        f"ConfigError line; got stderr={completed.stderr!r}"
                    ),
                )


# ---------------------------------------------------------------------------
# Bug 3: subcommand --help lists every universal flag
# ---------------------------------------------------------------------------


class TestSubcommandHelpListsUniversalFlags(unittest.TestCase):
    """``<cli> <subcommand> --help`` surfaces every universal flag.

    Pre-fix the subcommand ``--help`` listed only ``-h, --help``;
    operators had no way to discover the universal flag set from a
    subcommand. Post-fix the universal parent is shared via
    ``parents=`` so every subcommand's help listing carries the
    full universal flag set.
    """

    def test_subcommand_help_lists_every_universal_flag(self) -> None:
        for cli, sub in SERVICES:
            with self.subTest(cli=cli, sub=sub):
                completed = _run_cli(cli, [sub, "--help"])
                self.assertEqual(
                    completed.returncode,
                    0,
                    (
                        f"{cli} {sub} --help exited {completed.returncode}; "
                        f"stderr={completed.stderr!r}"
                    ),
                )
                # The argparse usage header is the canonical anchor.
                self.assertIn("usage:", completed.stdout)
                # Every universal flag must appear in the body.
                for flag in UNIVERSAL_FLAGS:
                    self.assertIn(
                        flag,
                        completed.stdout,
                        (
                            f"{cli} {sub} --help does not list "
                            f"{flag!r}; full stdout={completed.stdout!r}"
                        ),
                    )

    def test_top_level_help_still_lists_universal_flags(self) -> None:
        # Regression guard: the top-level --help must keep listing
        # every universal flag. A refactor that accidentally drops
        # the parent from the top-level build_parser would surface
        # here.
        for cli, _ in SERVICES:
            with self.subTest(cli=cli):
                completed = _run_cli(cli, ["--help"])
                self.assertEqual(
                    completed.returncode,
                    0,
                    (
                        f"{cli} --help exited {completed.returncode}; "
                        f"stderr={completed.stderr!r}"
                    ),
                )
                for flag in UNIVERSAL_FLAGS:
                    self.assertIn(
                        flag,
                        completed.stdout,
                        (
                            f"{cli} --help does not list {flag!r}; "
                            f"full stdout={completed.stdout!r}"
                        ),
                    )


# ---------------------------------------------------------------------------
# Bug 2: argparse errors -> exit 1 with structured stderr
# ---------------------------------------------------------------------------


class TestArgparseErrorExitOneWithStructuredStderr(unittest.TestCase):
    """argparse errors surface as ``ConfigError`` (exit 1 + structured line).

    Pre-fix the argparse ``SystemExit(2)`` propagated through
    ``main_wrapper`` unchanged, colliding with the documented
    ``AuthError`` exit code. Post-fix the wrapper re-raises as
    ``ConfigError`` so the process exits with the documented ``1``
    and the structured ``service=config op=parse message=...`` line
    is on stderr.
    """

    def test_unknown_flag_after_subcommand_exits_one(self) -> None:
        for cli, sub in SERVICES:
            with self.subTest(cli=cli, sub=sub):
                completed = _run_cli(cli, [sub, "--bogus-flag-xyz"])
                self.assertEqual(
                    completed.returncode,
                    1,
                    (
                        f"{cli} {sub} --bogus-flag-xyz: expected exit 1, "
                        f"got {completed.returncode}; "
                        f"stderr={completed.stderr!r}"
                    ),
                )
                line = _structured_parse_line(completed.stderr)
                self.assertIsNotNone(
                    line,
                    (
                        f"{cli} {sub} --bogus-flag-xyz: expected "
                        f"service=config op=parse message=...; "
                        f"stderr={completed.stderr!r}"
                    ),
                )

    def test_unknown_subcommand_exits_one(self) -> None:
        # Every CLI rejects unknown subcommands at the top-level
        # subparsers step. Pre-fix: exit 2. Post-fix: exit 1 with
        # the structured stderr line.
        for cli, _ in SERVICES:
            with self.subTest(cli=cli):
                completed = _run_cli(
                    cli, ["--config", MISSING_CONFIG_PATH, "not-a-sub"]
                )
                self.assertEqual(
                    completed.returncode,
                    1,
                    (
                        f"{cli} not-a-sub: expected exit 1, "
                        f"got {completed.returncode}; "
                        f"stderr={completed.stderr!r}"
                    ),
                )
                line = _structured_parse_line(completed.stderr)
                self.assertIsNotNone(
                    line,
                    (
                        f"{cli} not-a-sub: expected "
                        f"service=config op=parse message=...; "
                        f"stderr={completed.stderr!r}"
                    ),
                )

    def test_missing_required_positional_exits_one(self) -> None:
        # Subcommands with required positionals:
        #   jellyfin item <id>
        #   radarr lookup <term>     (term is OPTIONAL with default '')
        #   sonarr lookup <term>     (term is OPTIONAL with default '')
        #   maintainerr              (no required positionals)
        #   seerr search <query>     (query is OPTIONAL with default '')
        # To exercise the missing-required-positional argparse path
        # without entangling the universal-flag fix, we pick the
        # two subcommands that have a genuinely required positional.
        cases: tuple[tuple[str, str], ...] = (
            ("jellyfin", "item"),
        )
        for cli, sub in cases:
            with self.subTest(cli=cli, sub=sub):
                completed = _run_cli(cli, ["--config", MISSING_CONFIG_PATH, sub])
                self.assertEqual(
                    completed.returncode,
                    1,
                    (
                        f"{cli} {sub} (no positional): expected exit 1, "
                        f"got {completed.returncode}; "
                        f"stderr={completed.stderr!r}"
                    ),
                )
                line = _structured_parse_line(completed.stderr)
                self.assertIsNotNone(
                    line,
                    (
                        f"{cli} {sub} (no positional): expected "
                        f"service=config op=parse message=...; "
                        f"stderr={completed.stderr!r}"
                    ),
                )


# ---------------------------------------------------------------------------
# End-to-end acceptance: structured stderr shape on a bad invocation
# ---------------------------------------------------------------------------


class TestParseErrorStderrShape(unittest.TestCase):
    """The structured stderr line carries the documented key=value tokens.

    The downstream tooling contract depends on the exact
    ``service=config op=parse message=...`` shape. These tests
    lock the shape tokens (not the wording) so a future refactor
    that drops a key surfaces here.
    """

    def test_structured_line_has_expected_tokens(self) -> None:
        # A single representative invocation is enough to lock the
        # shape across the whole fix.
        completed = _run_cli("jellyfin", ["now", "--bogus"])
        self.assertEqual(completed.returncode, 1)
        line = _structured_parse_line(completed.stderr)
        self.assertIsNotNone(line)
        # The structured line must contain each of the three
        # documented tokens, in order, as whitespace-separated
        # key=value pairs.
        self.assertRegex(
            line,
            r"^service=config op=parse message=\S",
            (f"structured stderr line is not in the documented shape; got {line!r}"),
        )
        # The message value must not be empty (an empty message
        # would still satisfy the regex but defeats the contract).
        self.assertIn(
            " message=",
            line,
            f"structured line missing the message= token: {line!r}",
        )


if __name__ == "__main__":
    unittest.main()
