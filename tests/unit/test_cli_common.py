"""Unit tests for :mod:`arr_cli.facade.cli_common`.

Covers the contract spelled out in task 7.4 of tasks.md:

* ``--debug`` and ``--no-debug`` both work via ``BooleanOptionalAction``.
* ``--human`` accepts both ``--human`` and ``-h``; ``--config /tmp/x``
  overrides the default.
* ``main_wrapper`` returns the documented ``exit_code`` for each
  :class:`ArrError` subclass.
* ``--debug=True`` prints the traceback; ``--debug=False`` does not.
* ``warn_once`` dedupes per ``(service, message)`` pair, respects
  ``quiet=True``, and resets between ``main_wrapper`` invocations.

The tests are stdlib-only (``unittest`` + ``unittest.mock``). They
build a TOML config in a tmp directory so ``load_config`` succeeds
without depending on PyYAML; TOML is parsed by ``tomllib`` from the
standard library (Python 3.11+) so this file runs in the
orchestrator's review phase without extra dependencies.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

# Make the project importable regardless of the test runner's CWD.
_PROJ_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from arr_cli.facade.cli_common import (  # noqa: E402 - sys.path tweak above
    build_parser,
    main_wrapper,
    reset_warnings,
    warn_once,
)
from arr_cli.facade.config import (  # noqa: E402
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_RETRY,
    AuthConfig,
    ServiceConfig,
)
from arr_cli.facade.errors import (  # noqa: E402
    ArrError,
    AuthError,
    ConfigError,
    HttpError,
    NetworkError,
    ParseError,
)
from arr_cli.facade.output import DEFAULT_LIMIT  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_stderr(callable_: Any, *args: Any, **kwargs: Any) -> str:
    """Invoke ``callable_`` with stderr redirected to a StringIO buffer."""
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        callable_(*args, **kwargs)
    return buffer.getvalue()


def _capture_stderr_stdout(callable_: Any, *args: Any, **kwargs: Any) -> tuple[str, str]:
    """Invoke ``callable_`` with stdout and stderr captured separately.

    Swallows ``SystemExit`` raised by ``argparse.parse_args`` (which
    calls ``sys.exit`` on usage errors) so callers can assert on the
    output without first catching the exception themselves.
    """
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    with contextlib.redirect_stdout(stdout_buf), \
            contextlib.redirect_stderr(stderr_buf):
        try:
            callable_(*args, **kwargs)
        except SystemExit:
            # argparse exits via ``sys.exit`` after writing the
            # usage hint; the captured streams already contain the
            # output we want to assert on.
            pass
    return stdout_buf.getvalue(), stderr_buf.getvalue()


def _write_toml_config(
    tmp_dir: Path,
    *,
    services: dict[str, dict[str, Any]] | None = None,
    name: str = "arr.toml",
) -> Path:
    """Write a minimal valid TOML config to ``tmp_dir / name``.

    The default content populates all five services with placeholder
    values. Tests that need a different shape pass ``services``.
    """
    if services is None:
        services = {
            "jellyfin": {
                "url": "https://jellyfin.example",
                "api_key": "jf-key",
                "user_id": "jf-user",
            },
            "radarr": {"url": "https://radarr.example", "api_key": "rd-key"},
            "sonarr": {"url": "https://sonarr.example", "api_key": "sn-key"},
            "maintainerr": {
                "url": "https://maintainerr.example",
                "auth_enabled": False,
            },
            "seerr": {"url": "https://seerr.example", "api_key": "sr-key"},
        }

    lines: list[str] = []
    for svc_name, fields in services.items():
        lines.append(f"[{svc_name}]")
        for key, value in fields.items():
            if isinstance(value, bool):
                lines.append(f"{key} = {str(value).lower()}")
            elif isinstance(value, str):
                # Quote every string to keep TOML happy with keys/values
                # that contain dashes or other punctuation.
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{key} = "{escaped}"')
            else:
                lines.append(f"{key} = {value}")
        lines.append("")  # blank line between sections

    path = tmp_dir / name
    path.write_text("\n".join(lines), encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    return path


def _empty_service_config() -> ServiceConfig:
    """Return a ServiceConfig with all service slots populated as None."""
    return ServiceConfig(
        jellyfin=None,
        radarr=None,
        sonarr=None,
        maintainerr=None,
        seerr=None,
    )


def _ok_handler(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Trivial handler that returns 0 (success)."""
    return 0


def _none_handler(args: argparse.Namespace, cfg: ServiceConfig) -> None:
    """Handler returning ``None`` -- main_wrapper should coerce to exit 0."""


# ---------------------------------------------------------------------------
# build_parser: defaults
# ---------------------------------------------------------------------------


class TestBuildParserDefaults(unittest.TestCase):
    """The universal flag set has the documented defaults."""

    def setUp(self) -> None:
        self.parser = build_parser(
            "jellyfin", "Jellyfin CLI", epilog="trailing text"
        )

    def test_returns_argument_parser(self) -> None:
        # Sanity: build_parser must return an argparse.ArgumentParser
        # so callers can attach subparsers / further flags to it.
        self.assertIsInstance(self.parser, argparse.ArgumentParser)

    def test_prog_is_set(self) -> None:
        # The prog name surfaces in the usage line; the per-service
        # CLIs will pass the console-script name here.
        self.assertEqual(self.parser.prog, "jellyfin")

    def test_help_text_carries_through(self) -> None:
        # Description + epilog both flow through so the per-service
        # CLIs can inject their own one-paragraph summary.
        self.assertEqual(self.parser.description, "Jellyfin CLI")
        self.assertEqual(self.parser.epilog, "trailing text")

    def test_default_config_is_none(self) -> None:
        args = self.parser.parse_args([])
        self.assertIsNone(args.config)

    def test_default_debug_is_false(self) -> None:
        args = self.parser.parse_args([])
        self.assertFalse(args.debug)

    def test_default_quiet_is_false(self) -> None:
        args = self.parser.parse_args([])
        self.assertFalse(args.quiet)

    def test_default_human_is_false(self) -> None:
        args = self.parser.parse_args([])
        self.assertFalse(args.human)

    def test_default_connect_timeout(self) -> None:
        args = self.parser.parse_args([])
        self.assertEqual(args.connect_timeout, DEFAULT_CONNECT_TIMEOUT)
        self.assertEqual(args.connect_timeout, 5.0)

    def test_default_read_timeout(self) -> None:
        args = self.parser.parse_args([])
        self.assertEqual(args.read_timeout, DEFAULT_READ_TIMEOUT)
        self.assertEqual(args.read_timeout, 30.0)

    def test_default_retry(self) -> None:
        args = self.parser.parse_args([])
        self.assertEqual(args.retry, DEFAULT_RETRY)
        self.assertEqual(args.retry, 0)

    def test_default_deadline_is_none(self) -> None:
        args = self.parser.parse_args([])
        self.assertIsNone(args.deadline)

    def test_default_limit(self) -> None:
        args = self.parser.parse_args([])
        self.assertEqual(args.limit, DEFAULT_LIMIT)
        self.assertEqual(args.limit, 20)


# ---------------------------------------------------------------------------
# build_parser: BooleanOptionalAction flag pairs
# ---------------------------------------------------------------------------


class TestBuildParserBooleanFlags(unittest.TestCase):
    """``--debug/--no-debug`` and ``--quiet/--no-quiet`` round-trip."""

    def setUp(self) -> None:
        self.parser = build_parser("sonarr", "Sonarr CLI")

    def test_debug_enables(self) -> None:
        args = self.parser.parse_args(["--debug"])
        self.assertTrue(args.debug)

    def test_no_debug_disables(self) -> None:
        # Even after a parse, --no-debug explicitly forces False so
        # shell scripts can override a config-driven default.
        args = self.parser.parse_args(["--no-debug"])
        self.assertFalse(args.debug)

    def test_debug_then_no_debug(self) -> None:
        # Order independence: the last flag wins on a single parse.
        args = self.parser.parse_args(["--debug", "--no-debug"])
        self.assertFalse(args.debug)

    def test_quiet_enables(self) -> None:
        args = self.parser.parse_args(["--quiet"])
        self.assertTrue(args.quiet)

    def test_no_quiet_disables(self) -> None:
        args = self.parser.parse_args(["--no-quiet"])
        self.assertFalse(args.quiet)


# ---------------------------------------------------------------------------
# build_parser: --human and -h alias
# ---------------------------------------------------------------------------


class TestBuildParserHumanAlias(unittest.TestCase):
    """``--human`` accepts both long form and the ``-h`` short alias."""

    def setUp(self) -> None:
        self.parser = build_parser("seerr", "Seerr CLI")

    def test_human_long_form(self) -> None:
        args = self.parser.parse_args(["--human"])
        self.assertTrue(args.human)

    def test_human_short_alias(self) -> None:
        # The ``-h`` short option MUST alias ``--human`` (not
        # ``--help``) per the design contract. argparse reserves ``-h``
        # for ``--help`` by default; cli_common registers ``--help``
        # explicitly so ``-h`` is free for the documented purpose.
        args = self.parser.parse_args(["-h"])
        self.assertTrue(args.human)

    def test_help_long_form_still_works(self) -> None:
        # ``--help`` (long form) remains available regardless of
        # the ``-h`` alias.
        with self.assertRaises(SystemExit) as cm:
            self.parser.parse_args(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_help_prints_to_stdout(self) -> None:
        # ``--help`` writes the usage hint to stdout, the argparse
        # default. Confirm so a tester can ``capsys`` capture it.
        stdout, _ = _capture_stderr_stdout(
            self.parser.parse_args, ["--help"]
        )
        # The argparse output starts with "usage:" so we use that
        # as a stable anchor across argparse versions.
        self.assertIn("usage:", stdout)


# ---------------------------------------------------------------------------
# build_parser: --config override and timeout flags
# ---------------------------------------------------------------------------


class TestBuildParserConfigOverride(unittest.TestCase):
    """``--config <path>`` overrides the default config path."""

    def test_config_flag_stores_value(self) -> None:
        parser = build_parser("jellyfin", "Jellyfin CLI")
        args = parser.parse_args(["--config", "/tmp/example.conf"])
        self.assertEqual(args.config, "/tmp/example.conf")

    def test_config_flag_accepts_relative_path(self) -> None:
        parser = build_parser("radarr", "Radarr CLI")
        args = parser.parse_args(["--config", "./local.conf"])
        self.assertEqual(args.config, "./local.conf")


class TestBuildParserTimeoutFlags(unittest.TestCase):
    """Timeout / retry / deadline flags parse to the right types."""

    def setUp(self) -> None:
        self.parser = build_parser("maintainerr", "Maintainerr CLI")

    def test_connect_timeout_parses_float(self) -> None:
        args = self.parser.parse_args(["--connect-timeout", "7.5"])
        self.assertEqual(args.connect_timeout, 7.5)

    def test_read_timeout_parses_float(self) -> None:
        args = self.parser.parse_args(["--read-timeout", "60.0"])
        self.assertEqual(args.read_timeout, 60.0)

    def test_retry_parses_int(self) -> None:
        args = self.parser.parse_args(["--retry", "3"])
        self.assertEqual(args.retry, 3)

    def test_deadline_parses_float(self) -> None:
        args = self.parser.parse_args(["--deadline", "45.5"])
        self.assertEqual(args.deadline, 45.5)

    def test_limit_parses_int(self) -> None:
        args = self.parser.parse_args(["--limit", "50"])
        self.assertEqual(args.limit, 50)


# ---------------------------------------------------------------------------
# main_wrapper: exit-code map
# ---------------------------------------------------------------------------


class TestMainWrapperExitCodes(unittest.TestCase):
    """``main_wrapper`` returns ``exc.exit_code`` for each ArrError subclass."""

    def setUp(self) -> None:
        # Reset the warn-once set so warnings from prior tests do not
        # bleed into this class (REQ-9 AC1).
        reset_warnings()
        # Build a parser that delegates every flag to the wrapper.
        self.parser = build_parser("test", "Test CLI")
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="arr-cli-test-"))
        self.cfg_path = _write_toml_config(self.tmp_dir)

    def tearDown(self) -> None:
        import shutil
        try:
            shutil.rmtree(self.tmp_dir)
        except OSError:
            pass

    def _argv(self, *extra: str) -> list[str]:
        return ["--config", str(self.cfg_path), *extra]

    def test_config_error_returns_one(self) -> None:
        # ConfigError.exit_code == 1 (REQ-4 AC3 / REQ-1 AC2).
        def handler(args: argparse.Namespace, cfg: ServiceConfig) -> int:
            raise ConfigError("jellyfin", "load", "missing section")
        exit_code = main_wrapper(
            "jellyfin", handler, parser=self.parser, argv=self._argv()
        )
        self.assertEqual(exit_code, 1)

    def test_auth_error_returns_two(self) -> None:
        # AuthError.exit_code == 2 (REQ-2 AC5 / REQ-2 AC6).
        def handler(args: argparse.Namespace, cfg: ServiceConfig) -> int:
            raise AuthError("jellyfin", "now", "missing api_key")
        exit_code = main_wrapper(
            "jellyfin", handler, parser=self.parser, argv=self._argv()
        )
        self.assertEqual(exit_code, 2)

    def test_network_error_returns_three(self) -> None:
        # NetworkError.exit_code == 3 (REQ-4 AC1).
        def handler(args: argparse.Namespace, cfg: ServiceConfig) -> int:
            raise NetworkError(
                "radarr",
                "calendar",
                "Timeout",
                url="https://radarr.example/api/v3/calendar",
            )
        exit_code = main_wrapper(
            "radarr", handler, parser=self.parser, argv=self._argv()
        )
        self.assertEqual(exit_code, 3)

    def test_http_error_returns_four(self) -> None:
        # HttpError.exit_code == 4 (REQ-4 AC2).
        def handler(args: argparse.Namespace, cfg: ServiceConfig) -> int:
            raise HttpError("sonarr", "series", "Series not found", status=404)
        exit_code = main_wrapper(
            "sonarr", handler, parser=self.parser, argv=self._argv()
        )
        self.assertEqual(exit_code, 4)

    def test_parse_error_returns_five(self) -> None:
        # ParseError.exit_code == 5 (REQ-4 AC6).
        def handler(args: argparse.Namespace, cfg: ServiceConfig) -> int:
            raise ParseError("seerr", "user", "invalid JSON", byte_offset=17)
        exit_code = main_wrapper(
            "seerr", handler, parser=self.parser, argv=self._argv()
        )
        self.assertEqual(exit_code, 5)


class TestMainWrapperConfigLoadError(unittest.TestCase):
    """``load_config`` errors surface through main_wrapper."""

    def setUp(self) -> None:
        reset_warnings()
        self.parser = build_parser("jellyfin", "Jellyfin CLI")

    def test_missing_config_returns_config_error_exit_code(self) -> None:
        # When ``--config`` points at a non-existent file the loader
        # raises ConfigError(exit_code=1); the wrapper translates that
        # to exit code 1 (REQ-1 AC2).
        exit_code = main_wrapper(
            "jellyfin",
            _ok_handler,
            parser=self.parser,
            argv=["--config", "/tmp/does-not-exist-arr-cli-test.conf"],
        )
        self.assertEqual(exit_code, 1)


# ---------------------------------------------------------------------------
# main_wrapper: structured stderr output
# ---------------------------------------------------------------------------


class TestMainWrapperStderrFormat(unittest.TestCase):
    """The structured stderr line mirrors ``str(exc)`` for each ArrError."""

    def setUp(self) -> None:
        reset_warnings()
        self.parser = build_parser("jellyfin", "Jellyfin CLI")
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="arr-cli-test-"))
        self.cfg_path = _write_toml_config(self.tmp_dir)

    def tearDown(self) -> None:
        import shutil
        try:
            shutil.rmtree(self.tmp_dir)
        except OSError:
            pass

    def _argv(self, *extra: str) -> list[str]:
        return ["--config", str(self.cfg_path), *extra]

    def test_structured_line_for_http_error(self) -> None:
        # HttpError carries ``status=`` in its structured line; the
        # wrapper must surface that line verbatim (REQ-4 AC3).
        def handler(args: argparse.Namespace, cfg: ServiceConfig) -> int:
            raise HttpError(
                "sonarr", "series", "Series not found", status=404
            )
        stderr = _capture_stderr(
            main_wrapper,
            "sonarr",
            handler,
            parser=self.parser,
            argv=self._argv(),
        )
        # ``str(HttpError(...))`` builds the canonical line.
        self.assertIn("service=sonarr", stderr)
        self.assertIn("op=series", stderr)
        self.assertIn("status=404", stderr)
        self.assertIn("message=Series not found", stderr)
        # The structured line is the first non-empty line -- a consumer
        # parsing line-by-line can read it directly.
        first = next(
            line for line in stderr.splitlines() if line.strip()
        )
        self.assertTrue(first.startswith("service="))

    def test_structured_line_for_config_error_omits_status(self) -> None:
        # ConfigError carries no status; the structured line MUST
        # not include a stray ``status=`` token (REQ-4 AC3).
        def handler(args: argparse.Namespace, cfg: ServiceConfig) -> int:
            raise ConfigError("config", "load", "missing section")
        stderr = _capture_stderr(
            main_wrapper,
            "config",
            handler,
            parser=self.parser,
            argv=self._argv(),
        )
        self.assertNotIn("status=", stderr)


# ---------------------------------------------------------------------------
# main_wrapper: --debug traceback policy
# ---------------------------------------------------------------------------


class TestMainWrapperDebugTraceback(unittest.TestCase):
    """``--debug`` adds the traceback; default mode suppresses it."""

    def setUp(self) -> None:
        reset_warnings()
        self.parser = build_parser("jellyfin", "Jellyfin CLI")
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="arr-cli-test-"))
        self.cfg_path = _write_toml_config(self.tmp_dir)

    def tearDown(self) -> None:
        import shutil
        try:
            shutil.rmtree(self.tmp_dir)
        except OSError:
            pass

    def _argv(self, *extra: str) -> list[str]:
        return ["--config", str(self.cfg_path), *extra]

    def test_debug_off_no_traceback_for_arr_error(self) -> None:
        # REQ-4 AC5: default stderr shows only the structured line;
        # no Python traceback unless ``--debug`` is passed. With no
        # chained __cause__ the wrapper does not emit a traceback at
        # all (this branch covers the common "logged error" case).
        def handler(args: argparse.Namespace, cfg: ServiceConfig) -> int:
            raise ConfigError("config", "load", "missing section")
        stderr = _capture_stderr(
            main_wrapper,
            "config",
            handler,
            parser=self.parser,
            argv=self._argv(),
        )
        self.assertNotIn("Traceback", stderr)

    def test_debug_on_traceback_for_arr_error_with_cause(self) -> None:
        # When the handler raises ``X from Y`` and ``--debug`` is on,
        # the wrapper prints the full traceback including the
        # chained cause (REQ-4 AC5).
        def handler(args: argparse.Namespace, cfg: ServiceConfig) -> int:
            try:
                raise ValueError("inner trigger")
            except ValueError as inner:
                raise ConfigError(
                    "config", "load", "wrapped"
                ) from inner
        stderr = _capture_stderr(
            main_wrapper,
            "config",
            handler,
            parser=self.parser,
            argv=self._argv("--debug"),
        )
        # ``traceback.format_exc`` includes the literal "Traceback"
        # header, so a contains-check is the right assertion.
        self.assertIn("Traceback", stderr)
        # The structured line still appears before the traceback.
        self.assertIn("service=config", stderr)

    def test_debug_off_no_traceback_for_unexpected_error(self) -> None:
        # Unexpected exceptions emit a one-line summary by default;
        # no traceback unless ``--debug`` is passed.
        def handler(args: argparse.Namespace, cfg: ServiceConfig) -> int:
            raise RuntimeError("boom")
        stderr = _capture_stderr(
            main_wrapper,
            "jellyfin",
            handler,
            parser=self.parser,
            argv=self._argv(),
        )
        self.assertIn("unexpected error in jellyfin: RuntimeError: boom", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_debug_on_traceback_for_unexpected_error(self) -> None:
        # With ``--debug`` the full traceback follows the one-line
        # summary so operators can see the call chain.
        def handler(args: argparse.Namespace, cfg: ServiceConfig) -> int:
            raise RuntimeError("boom")
        stderr = _capture_stderr(
            main_wrapper,
            "jellyfin",
            handler,
            parser=self.parser,
            argv=self._argv("--debug"),
        )
        self.assertIn("unexpected error in jellyfin: RuntimeError: boom", stderr)
        self.assertIn("Traceback", stderr)


# ---------------------------------------------------------------------------
# main_wrapper: success / return-value coercion
# ---------------------------------------------------------------------------


class TestMainWrapperReturnValue(unittest.TestCase):
    """Handler return values are coerced into a stable exit code."""

    def setUp(self) -> None:
        reset_warnings()
        self.parser = build_parser("jellyfin", "Jellyfin CLI")
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="arr-cli-test-"))
        self.cfg_path = _write_toml_config(self.tmp_dir)

    def tearDown(self) -> None:
        import shutil
        try:
            shutil.rmtree(self.tmp_dir)
        except OSError:
            pass

    def _argv(self, *extra: str) -> list[str]:
        return ["--config", str(self.cfg_path), *extra]

    def test_handler_returning_zero(self) -> None:
        exit_code = main_wrapper(
            "jellyfin",
            _ok_handler,
            parser=self.parser,
            argv=self._argv(),
        )
        self.assertEqual(exit_code, 0)

    def test_handler_returning_none_is_zero(self) -> None:
        # Handlers that print and fall off the end (returning ``None``
        # implicitly) must produce exit 0 so callers do not need to
        # remember to return an int.
        exit_code = main_wrapper(
            "jellyfin",
            _none_handler,
            parser=self.parser,
            argv=self._argv(),
        )
        self.assertEqual(exit_code, 0)

    def test_handler_returning_int_propagates(self) -> None:
        def handler(args: argparse.Namespace, cfg: ServiceConfig) -> int:
            return 7
        exit_code = main_wrapper(
            "jellyfin",
            handler,
            parser=self.parser,
            argv=self._argv(),
        )
        self.assertEqual(exit_code, 7)


# ---------------------------------------------------------------------------
# main_wrapper: per-flag override application
# ---------------------------------------------------------------------------


class TestMainWrapperOverrides(unittest.TestCase):
    """CLI flags override the parsed config's transport defaults."""

    def setUp(self) -> None:
        reset_warnings()
        self.parser = build_parser("jellyfin", "Jellyfin CLI")
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="arr-cli-test-"))
        self.cfg_path = _write_toml_config(self.tmp_dir)

    def tearDown(self) -> None:
        import shutil
        try:
            shutil.rmtree(self.tmp_dir)
        except OSError:
            pass

    def _argv(self, *extra: str) -> list[str]:
        return ["--config", str(self.cfg_path), *extra]

    def test_connect_timeout_override_reaches_handler(self) -> None:
        captured: dict[str, Any] = {}

        def handler(args: argparse.Namespace, cfg: ServiceConfig) -> int:
            captured["connect_timeout"] = cfg.connect_timeout
            captured["read_timeout"] = cfg.read_timeout
            return 0
        main_wrapper(
            "jellyfin",
            handler,
            parser=self.parser,
            argv=self._argv("--connect-timeout", "1.5"),
        )
        self.assertEqual(captured["connect_timeout"], 1.5)
        # Other defaults stay untouched.
        self.assertEqual(captured["read_timeout"], DEFAULT_READ_TIMEOUT)

    def test_retry_override_reaches_handler(self) -> None:
        captured: dict[str, Any] = {}

        def handler(args: argparse.Namespace, cfg: ServiceConfig) -> int:
            captured["retry"] = cfg.retry
            captured["deadline"] = cfg.deadline
            return 0
        main_wrapper(
            "jellyfin",
            handler,
            parser=self.parser,
            argv=self._argv("--retry", "2", "--deadline", "10"),
        )
        self.assertEqual(captured["retry"], 2)
        self.assertEqual(captured["deadline"], 10.0)


# ---------------------------------------------------------------------------
# main_wrapper: SystemExit propagation
# ---------------------------------------------------------------------------


class TestMainWrapperSystemExit(unittest.TestCase):
    """argparse's SystemExit bubbles through with the documented code."""

    def setUp(self) -> None:
        reset_warnings()
        self.parser = build_parser("jellyfin", "Jellyfin CLI")

    def test_unknown_arg_returns_argparse_exit_code(self) -> None:
        # argparse calls ``sys.exit(2)`` on unknown args; the wrapper
        # catches the SystemExit and surfaces the code unchanged.
        exit_code = main_wrapper(
            "jellyfin",
            _ok_handler,
            parser=self.parser,
            argv=["--not-a-real-flag"],
        )
        self.assertEqual(exit_code, 2)

    def test_bad_int_returns_argparse_exit_code(self) -> None:
        # ``--retry`` expects an int; a float string triggers
        # argparse's usage error path.
        exit_code = main_wrapper(
            "jellyfin",
            _ok_handler,
            parser=self.parser,
            argv=["--retry", "not-an-int"],
        )
        self.assertEqual(exit_code, 2)


# ---------------------------------------------------------------------------
# warn_once
# ---------------------------------------------------------------------------


class TestWarnOnce(unittest.TestCase):
    """``warn_once`` dedupes per ``(service, message)`` pair and respects quiet."""

    def setUp(self) -> None:
        reset_warnings()

    def test_prints_once_with_service_prefix(self) -> None:
        stderr = _capture_stderr(
            warn_once,
            "maintainerr",
            "auth disabled; ensure private network",
            quiet=False,
        )
        self.assertEqual(
            stderr, "maintainerr: auth disabled; ensure private network\n"
        )

    def test_dedupes_repeated_calls(self) -> None:
        # Three identical calls collapse to a single line.
        first = _capture_stderr(
            warn_once, "svc", "msg", quiet=False
        )
        second = _capture_stderr(
            warn_once, "svc", "msg", quiet=False
        )
        third = _capture_stderr(
            warn_once, "svc", "msg", quiet=False
        )
        self.assertEqual(first, "svc: msg\n")
        self.assertEqual(second, "")
        self.assertEqual(third, "")

    def test_different_message_prints_again(self) -> None:
        # Different (service, message) keys are independent; both
        # fire on the first call after a reset.
        _capture_stderr(warn_once, "svc", "msg-a", quiet=False)
        second = _capture_stderr(warn_once, "svc", "msg-b", quiet=False)
        self.assertEqual(second, "svc: msg-b\n")

    def test_different_service_prints_again(self) -> None:
        _capture_stderr(warn_once, "svc-a", "msg", quiet=False)
        second = _capture_stderr(warn_once, "svc-b", "msg", quiet=False)
        self.assertEqual(second, "svc-b: msg\n")

    def test_quiet_suppresses(self) -> None:
        # REQ-9 AC1: ``--quiet`` suppresses the Maintainerr
        # auth-disabled warning entirely.
        stderr = _capture_stderr(
            warn_once, "maintainerr", "auth disabled", quiet=True
        )
        self.assertEqual(stderr, "")

    def test_quiet_does_not_mark_as_fired(self) -> None:
        # A ``quiet=True`` call must not consume the dedupe slot;
        # otherwise a subsequent ``quiet=False`` would silently drop.
        _capture_stderr(warn_once, "svc", "msg", quiet=True)
        stderr = _capture_stderr(warn_once, "svc", "msg", quiet=False)
        self.assertEqual(stderr, "svc: msg\n")

    def test_reset_clears_dedupe_set(self) -> None:
        # ``reset_warnings`` re-arms the warning so a new
        # ``main_wrapper`` invocation can fire the same line again
        # (REQ-9 AC1: "the warn-once set must reset each invocation").
        _capture_stderr(warn_once, "svc", "msg", quiet=False)
        reset_warnings()
        stderr = _capture_stderr(warn_once, "svc", "msg", quiet=False)
        self.assertEqual(stderr, "svc: msg\n")

    def test_empty_service_omits_prefix(self) -> None:
        # An empty service identifier is acceptable (the warning was
        # not associated with any particular service). The text
        # passes through without a leading " :" prefix.
        stderr = _capture_stderr(warn_once, "", "standalone", quiet=False)
        self.assertEqual(stderr, "standalone\n")


class TestWarnOnceResetBetweenWrapperInvocations(unittest.TestCase):
    """``main_wrapper`` resets the warn-once set on each invocation."""

    def setUp(self) -> None:
        reset_warnings()
        self.parser = build_parser("jellyfin", "Jellyfin CLI")
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="arr-cli-test-"))
        self.cfg_path = _write_toml_config(self.tmp_dir)

    def tearDown(self) -> None:
        import shutil
        try:
            shutil.rmtree(self.tmp_dir)
        except OSError:
            pass

    def test_wrapper_invocation_resets_warnings(self) -> None:
        # The maintainerr handler prints the auth-disabled warning
        # once per invocation; running ``main_wrapper`` twice
        # produces two warnings (one per invocation), confirming
        # the dedupe set is reset between calls (REQ-9 AC1).
        def maintainerr_handler(
            args: argparse.Namespace, cfg: ServiceConfig
        ) -> int:
            warn_once(
                "maintainerr",
                "auth disabled; ensure private network",
                quiet=args.quiet,
            )
            return 0

        def call_once() -> str:
            return _capture_stderr(
                main_wrapper,
                "maintainerr",
                maintainerr_handler,
                parser=self.parser,
                argv=["--config", str(self.cfg_path)],
            )

        first = call_once()
        second = call_once()
        # Same warning appears once per invocation.
        self.assertIn("maintainerr: auth disabled", first)
        self.assertIn("maintainerr: auth disabled", second)
        # Within a single invocation, repeated warn_once calls are
        # deduped: we do not see the line twice in one capture.
        self.assertEqual(
            first.count("auth disabled; ensure private network"), 1
        )

    def test_wrapper_invocation_quiet_flag_suppresses(self) -> None:
        # ``--quiet`` is propagated: with quiet=True the warning
        # never fires (REQ-9 AC1).
        def maintainerr_handler(
            args: argparse.Namespace, cfg: ServiceConfig
        ) -> int:
            warn_once(
                "maintainerr",
                "auth disabled; ensure private network",
                quiet=args.quiet,
            )
            return 0

        stderr = _capture_stderr(
            main_wrapper,
            "maintainerr",
            maintainerr_handler,
            parser=self.parser,
            argv=["--config", str(self.cfg_path), "--quiet"],
        )
        self.assertNotIn("auth disabled", stderr)


if __name__ == "__main__":
    unittest.main()
