"""Unit tests for :mod:`arr_cli.maintainerr` (task 11).

Covers the contract spelled out in task 11.3 of tasks.md:

* Each of the three Maintainerr commands hits the documented HTTP
  path with no auth header when ``auth_enabled = False`` (REQ-9
  AC1, AC2, AC3, AC4).
* ``auth_enabled = False`` fires the documented one-line stderr
  warning exactly once per invocation unless ``--quiet`` is passed
  (REQ-9 AC1).
* ``auth_enabled = True`` suppresses the warning (the operator has
  explicitly opted in; no warning is needed because the request
  carries the configured credentials).
* A 401/403 from a Maintainerr endpoint surfaces the documented
  guidance prepended to the :class:`AuthError` message (REQ-9 AC6).
  This logic lives in the transport layer; these tests verify the
  end-to-end behaviour from the handler's perspective.
* A ``rules`` command is NOT exposed (REQ-9 AC5) -- the parser and
  dispatch table must not list it.
* The parser rejects unknown subcommands (REQ-11 AC4); the
  per-command handler names are registered in the dispatch table.
* The ``main`` entry point wires the parser to ``main_wrapper`` and
  surfaces the documented exit codes on every error class.

Tests use ``unittest.mock`` to stub ``arr_cli.facade.transport.get``
so we can assert the exact path, params, and auth-header surface
without a network dependency. Mirrors :mod:`tests.unit.test_sonarr`
and :mod:`tests.unit.test_radarr` for stylistic consistency across
the five CLIs.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Make the project importable regardless of the test runner's CWD.
_PROJ_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from arr_cli.facade.cli_common import reset_warnings  # noqa: E402
from arr_cli.facade.config import (  # noqa: E402
    AuthConfig,
    ServiceConfig,
)
from arr_cli.facade.errors import (  # noqa: E402
    AuthError,
    ConfigError,
    HttpError,
)
from arr_cli.maintainerr import (  # noqa: E402
    AUTH_DISABLED_WARNING,
    SERVICE_NAME,
    _DISPATCH,
    _maybe_warn_auth_disabled,
    build_maintainerr_parser,
    cmd_health,
    cmd_pending,
    cmd_storage,
    main,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _capture_stdout(callable_: Any, *args: Any, **kwargs: Any) -> str:
    """Invoke ``callable_`` with stdout redirected to a StringIO buffer."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        callable_(*args, **kwargs)
    return buffer.getvalue()


def _capture_stderr_stdout(
    callable_: Any, *args: Any, **kwargs: Any
) -> tuple[str, str]:
    """Run ``callable_`` with stdout and stderr captured separately.

    Swallows ``SystemExit`` so the caller can inspect both streams
    without first catching the exception (mirrors the cli_common test
    helper).
    """
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    with contextlib.redirect_stdout(stdout_buf), \
            contextlib.redirect_stderr(stderr_buf):
        try:
            callable_(*args, **kwargs)
        except SystemExit:
            pass
    return stdout_buf.getvalue(), stderr_buf.getvalue()


def _write_toml_config(tmp_dir: Path, *, auth_enabled: bool = False) -> Path:
    """Write a minimal valid TOML config under ``tmp_dir``.

    Populates all five services with placeholder values so
    :func:`load_config` succeeds. Only the Maintainerr fields
    (``auth_enabled``) matter for the happy-path tests; the others
    exist so the loader accepts the file as a complete config.
    """
    body = (
        '[jellyfin]\n'
        'url = "https://jellyfin.example"\n'
        'api_key = "***"\n'
        'user_id = "jf-user-1"\n'
        '\n'
        '[radarr]\n'
        'url = "https://radarr.example"\n'
        'api_key = "***"\n'
        '\n'
        '[sonarr]\n'
        'url = "https://sonarr.example"\n'
        'api_key = "***"\n'
        '\n'
        f'[maintainerr]\n'
        f'url = "https://maintainerr.example"\n'
        f'auth_enabled = {"true" if auth_enabled else "false"}\n'
        '\n'
        '[seerr]\n'
        'url = "https://seerr.example"\n'
        'api_key = "***"\n'
    )
    import os
    path = tmp_dir / "arr.toml"
    path.write_text(body, encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    return path


def _service_config(
    *,
    auth_enabled: bool = False,
    url: str = "https://maintainerr.example",
    include_maintainerr: bool = True,
) -> ServiceConfig:
    """Return a :class:`ServiceConfig` configured for the Maintainerr tests.

    Each parameter has a documented default so individual tests can
    override only the field they care about (e.g.
    ``include_maintainerr=False`` to exercise the missing-section
    path).
    """
    maintainerr = (
        AuthConfig(url=url, auth_enabled=auth_enabled)
        if include_maintainerr
        else None
    )
    return ServiceConfig(
        jellyfin=AuthConfig(
            url="https://jellyfin.example",
            ak="jf-token",
            user_id="jf-user-1",
        ),
        radarr=AuthConfig(url="https://radarr.example", ak="rk"),
        sonarr=AuthConfig(url="https://sonarr.example", ak="sk"),
        maintainerr=maintainerr,
        seerr=AuthConfig(url="https://seerr.example", ak="sk"),
    )


def _namespace(
    *,
    human: bool = False,
    limit: int = 20,
    debug: bool = False,
    quiet: bool = False,
    **kwargs: Any,
) -> argparse.Namespace:
    """Build a minimal :class:`argparse.Namespace` for handler tests.

    Defaults match the universal flag set registered by
    :func:`build_parser`. Per-command fields are added via ``kwargs``
    so each test sets exactly what it needs.
    """
    return argparse.Namespace(
        human=human,
        limit=limit,
        debug=debug,
        quiet=quiet,
        connect_timeout=5.0,
        read_timeout=30.0,
        retry=0,
        deadline=None,
        config=None,
        **kwargs,
    )


def _patched_get_payload(payload: Any) -> Any:
    """Return a context manager that mocks ``transport.get`` to return ``payload``."""
    return patch(
        "arr_cli.maintainerr.transport.get",
        return_value=payload,
    )


def _patched_get_raising(exc: BaseException) -> Any:
    """Return a context manager that mocks ``transport.get`` to raise ``exc``."""
    return patch(
        "arr_cli.maintainerr.transport.get",
        side_effect=exc,
    )


# ---------------------------------------------------------------------------
# Test: dispatch table and parser registration
# ---------------------------------------------------------------------------


class TestDispatchTable(unittest.TestCase):
    """The dispatch table contains exactly the three documented subcommands."""

    def test_dispatch_keys(self) -> None:
        # REQ-9 AC2, AC3, AC4: pending, storage, health.
        self.assertEqual(
            set(_DISPATCH.keys()),
            {"pending", "storage", "health"},
        )

    def test_dispatch_handlers_are_callable(self) -> None:
        for handler in _DISPATCH.values():
            self.assertTrue(callable(handler))

    def test_each_handler_returns_int(self) -> None:
        # Every handler's success path returns an int (REQ-11 AC1).
        cfg = _service_config()
        payload: Any = []
        cases = [
            (cmd_pending, _namespace()),
            (cmd_storage, _namespace()),
            (cmd_health, _namespace()),
        ]
        for handler, args in cases:
            with _patched_get_payload(payload):
                result = handler(args, cfg)
            self.assertIsInstance(
                result,
                int,
                msg=f"{handler.__name__} returned {type(result).__name__}, not int",
            )
            self.assertEqual(result, 0)

    def test_rules_is_not_registered(self) -> None:
        # REQ-9 AC5: "the system SHALL NOT silently assume the
        # endpoint exists". The MVP intentionally omits ``rules``.
        self.assertNotIn("rules", _DISPATCH)


# ---------------------------------------------------------------------------
# Test: auth-disabled warning (REQ-9 AC1)
# ---------------------------------------------------------------------------


class TestAuthDisabledWarning(unittest.TestCase):
    """``_maybe_warn_auth_disabled`` fires once per invocation when appropriate."""

    def setUp(self) -> None:
        reset_warnings()

    def test_warning_fires_when_auth_disabled(self) -> None:
        cfg = _service_config(auth_enabled=False)
        args = _namespace(quiet=False)
        stderr = _capture_stderr_stdout(
            _maybe_warn_auth_disabled, args, cfg
        )[1]
        self.assertIn(AUTH_DISABLED_WARNING, stderr)
        self.assertIn(f"{SERVICE_NAME}:", stderr)

    def test_warning_silent_when_auth_enabled(self) -> None:
        # When the operator has explicitly turned auth on, no
        # warning is needed -- the request carries credentials.
        cfg = _service_config(auth_enabled=True)
        args = _namespace(quiet=False)
        stderr = _capture_stderr_stdout(
            _maybe_warn_auth_disabled, args, cfg
        )[1]
        self.assertNotIn(AUTH_DISABLED_WARNING, stderr)
        self.assertEqual(stderr, "")

    def test_warning_silent_when_quiet(self) -> None:
        # REQ-9 AC1: "the warning SHALL NOT appear if --quiet is passed".
        cfg = _service_config(auth_enabled=False)
        args = _namespace(quiet=True)
        stderr = _capture_stderr_stdout(
            _maybe_warn_auth_disabled, args, cfg
        )[1]
        self.assertEqual(stderr, "")

    def test_warning_silent_when_section_missing(self) -> None:
        # Defensive: if the section is missing, the loader's main
        # path raises ConfigError; the helper just stays silent.
        cfg = _service_config(include_maintainerr=False)
        args = _namespace(quiet=False)
        stderr = _capture_stderr_stdout(
            _maybe_warn_auth_disabled, args, cfg
        )[1]
        self.assertEqual(stderr, "")

    def test_warning_dedupes_within_invocation(self) -> None:
        # Calling the helper twice in a row does not double-print
        # (REQ-9 AC1: "the warn-once set must reset each invocation").
        cfg = _service_config(auth_enabled=False)
        args = _namespace(quiet=False)
        _capture_stderr_stdout(_maybe_warn_auth_disabled, args, cfg)
        stderr_second = _capture_stderr_stdout(
            _maybe_warn_auth_disabled, args, cfg
        )[1]
        self.assertEqual(stderr_second, "")

    def test_warning_uses_canonical_text(self) -> None:
        # Defensive: the warning text is frozen in requirements.md;
        # a future "fix typo" change should fail loudly here so
        # reviewers catch it.
        expected_substring = (
            "auth disabled; ensure this CLI is reachable only on a "
            "trusted/private network"
        )
        self.assertEqual(AUTH_DISABLED_WARNING, expected_substring)


# ---------------------------------------------------------------------------
# Test: cmd_pending (REQ-9 AC2)
# ---------------------------------------------------------------------------


class TestCmdPending(unittest.TestCase):
    """``GET /api/collections/overlay-data``."""

    def test_pending_hits_overlay_data_path(self) -> None:
        cfg = _service_config()
        args = _namespace()
        with _patched_get_payload([]) as mock_get:
            cmd_pending(args, cfg)
        positional = mock_get.call_args.args
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(positional[0], "maintainerr")
        self.assertEqual(positional[1], "/api/collections/overlay-data")
        self.assertIsNone(kwargs.get("params"))

    def test_pending_emits_json_when_not_human(self) -> None:
        cfg = _service_config()
        args = _namespace(human=False)
        payload = [{"title": "Old Movies", "mediaCount": 42}]
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_pending, args, cfg)
        self.assertEqual(json.loads(output), payload)


# ---------------------------------------------------------------------------
# Test: cmd_storage (REQ-9 AC3)
# ---------------------------------------------------------------------------


class TestCmdStorage(unittest.TestCase):
    """``GET /api/storage-metrics``."""

    def test_storage_hits_storage_metrics_path(self) -> None:
        cfg = _service_config()
        args = _namespace()
        with _patched_get_payload([]) as mock_get:
            cmd_storage(args, cfg)
        positional = mock_get.call_args.args
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(positional[0], "maintainerr")
        self.assertEqual(positional[1], "/api/storage-metrics")
        self.assertIsNone(kwargs.get("params"))

    def test_storage_emits_json_when_not_human(self) -> None:
        cfg = _service_config()
        args = _namespace(human=False)
        payload = [
            {"name": "/data", "total": 1000, "used": 700, "free": 300}
        ]
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_storage, args, cfg)
        self.assertEqual(json.loads(output), payload)


# ---------------------------------------------------------------------------
# Test: cmd_health (REQ-9 AC4)
# ---------------------------------------------------------------------------


class TestCmdHealth(unittest.TestCase):
    """``GET /api/health/ready`` -- typically returns a bare boolean."""

    def test_health_hits_health_ready_path(self) -> None:
        cfg = _service_config()
        args = _namespace()
        with _patched_get_payload(True) as mock_get:
            cmd_health(args, cfg)
        positional = mock_get.call_args.args
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(positional[0], "maintainerr")
        self.assertEqual(positional[1], "/api/health/ready")
        self.assertIsNone(kwargs.get("params"))

    def test_health_emits_scalar_json_when_not_human(self) -> None:
        cfg = _service_config()
        args = _namespace(human=False)
        with _patched_get_payload(True):
            output = _capture_stdout(cmd_health, args, cfg)
        # Maintainerr's readiness probe returns a bare boolean; the
        # output module must serialise it as ``true`` (REQ-3 AC1).
        self.assertEqual(json.loads(output), True)


# ---------------------------------------------------------------------------
# Test: --human mode
# ---------------------------------------------------------------------------


class TestHumanMode(unittest.TestCase):
    """When ``--human`` is set, ``output.emit`` is called with ``human_mode=True``."""

    def test_human_mode_passes_through_to_emit(self) -> None:
        cfg = _service_config()
        args = _namespace(human=True)
        payload = [{"title": "A"}, {"title": "B"}]
        with _patched_get_payload(payload), \
                patch("arr_cli.maintainerr.output.emit") as mock_emit:
            cmd_pending(args, cfg)
        mock_emit.assert_called_once()
        kwargs = mock_emit.call_args.kwargs
        self.assertTrue(kwargs["human_mode"])

    def test_human_mode_columns_for_pending(self) -> None:
        cfg = _service_config()
        args = _namespace(human=True)
        payload = [{"title": "Old Movies", "mediaCount": 42}]
        with _patched_get_payload(payload), \
                patch("arr_cli.maintainerr.output.emit") as mock_emit:
            cmd_pending(args, cfg)
        kwargs = mock_emit.call_args.kwargs
        self.assertEqual(
            kwargs["columns"],
            ["title", "mediaCount", "deleteAfterDays", "isOnHold"],
        )

    def test_human_mode_columns_for_storage(self) -> None:
        cfg = _service_config()
        args = _namespace(human=True)
        with _patched_get_payload([]), \
                patch("arr_cli.maintainerr.output.emit") as mock_emit:
            cmd_storage(args, cfg)
        kwargs = mock_emit.call_args.kwargs
        self.assertEqual(
            kwargs["columns"],
            ["name", "total", "used", "free", "percentUsed"],
        )

    def test_human_mode_health_has_no_columns(self) -> None:
        # REQ-3 AC3: scalar / boolean payloads (e.g. health returning
        # a bare True) render under --human with no table; the
        # handler therefore passes columns=None.
        cfg = _service_config()
        args = _namespace(human=True)
        with _patched_get_payload(True), \
                patch("arr_cli.maintainerr.output.emit") as mock_emit:
            cmd_health(args, cfg)
        kwargs = mock_emit.call_args.kwargs
        self.assertIsNone(kwargs["columns"])


# ---------------------------------------------------------------------------
# Test: parser
# ---------------------------------------------------------------------------


class TestBuildMaintainerrParser(unittest.TestCase):
    """The parser exposes the three subcommands and the universal flags."""

    def setUp(self) -> None:
        self.parser = build_maintainerr_parser()

    def test_parser_prog(self) -> None:
        self.assertEqual(self.parser.prog, SERVICE_NAME)

    def test_help_prints_to_stdout(self) -> None:
        stdout, _ = _capture_stderr_stdout(
            self.parser.parse_args, ["--help"]
        )
        self.assertIn("usage:", stdout)
        # The three commands surface in the help listing.
        for cmd in ("pending", "storage", "health"):
            self.assertIn(cmd, stdout)

    def test_help_does_not_advertise_rules(self) -> None:
        # REQ-9 AC5: rules is intentionally NOT in MVP scope and
        # MUST NOT appear in --help.
        stdout, _ = _capture_stderr_stdout(
            self.parser.parse_args, ["--help"]
        )
        self.assertNotIn("rules", stdout)

    def test_pending_parses(self) -> None:
        args = self.parser.parse_args(["pending"])
        self.assertEqual(args.command, "pending")

    def test_storage_parses(self) -> None:
        args = self.parser.parse_args(["storage"])
        self.assertEqual(args.command, "storage")

    def test_health_parses(self) -> None:
        args = self.parser.parse_args(["health"])
        self.assertEqual(args.command, "health")

    def test_unknown_subcommand_fails(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.parser.parse_args(["bogus"])
        self.assertEqual(ctx.exception.code, 2)

    def test_rules_subcommand_rejected(self) -> None:
        # The subparser tree does not register ``rules``; argparse
        # rejects it with the standard usage error.
        with self.assertRaises(SystemExit) as ctx:
            self.parser.parse_args(["rules"])
        self.assertEqual(ctx.exception.code, 2)

    def test_universal_flags_flow_through(self) -> None:
        args = self.parser.parse_args(
            [
                "--config",
                "/tmp/x",
                "--debug",
                "--quiet",
                "--human",
                "--limit",
                "10",
                "pending",
            ]
        )
        self.assertEqual(args.config, "/tmp/x")
        self.assertTrue(args.debug)
        self.assertTrue(args.quiet)
        self.assertTrue(args.human)
        self.assertEqual(args.limit, 10)
        self.assertEqual(args.command, "pending")


# ---------------------------------------------------------------------------
# Test: main entry point
# ---------------------------------------------------------------------------


class TestMainEntryPoint(unittest.TestCase):
    """``main`` wires the parser to ``main_wrapper`` end-to-end."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="maintainerr-test-"))
        self.cfg_path = _write_toml_config(self.tmp_dir)
        reset_warnings()

    def tearDown(self) -> None:
        import shutil
        try:
            shutil.rmtree(self.tmp_dir)
        except OSError:
            pass
        reset_warnings()

    def test_main_pending_returns_zero_on_success(self) -> None:
        payload = [{"title": "Old Movies", "mediaCount": 42}]
        with patch(
            "arr_cli.maintainerr.transport.get",
            return_value=payload,
        ):
            exit_code = main(["--config", str(self.cfg_path), "pending"])
        self.assertEqual(exit_code, 0)

    def test_main_storage_returns_zero_on_success(self) -> None:
        payload = [{"name": "/data", "total": 1000}]
        with patch(
            "arr_cli.maintainerr.transport.get",
            return_value=payload,
        ):
            exit_code = main(["--config", str(self.cfg_path), "storage"])
        self.assertEqual(exit_code, 0)

    def test_main_health_returns_zero_on_success(self) -> None:
        with patch(
            "arr_cli.maintainerr.transport.get",
            return_value=True,
        ):
            exit_code = main(["--config", str(self.cfg_path), "health"])
        self.assertEqual(exit_code, 0)

    def test_main_returns_usage_after_unknown_subcommand(self) -> None:
        # ``main_wrapper`` catches argparse's SystemExit internally and
        # surfaces the exit code (2) as the return value.
        exit_code = main(["--config", str(self.cfg_path), "bogus"])
        self.assertEqual(exit_code, 2)

    def test_main_rules_returns_usage_error(self) -> None:
        # REQ-9 AC5: ``rules`` MUST NOT be a registered subcommand.
        exit_code = main(["--config", str(self.cfg_path), "rules"])
        self.assertEqual(exit_code, 2)

    def test_main_emits_warning_when_auth_disabled(self) -> None:
        # REQ-9 AC1: a one-line stderr warning fires exactly once
        # per invocation when ``auth_enabled = False``.
        with patch(
            "arr_cli.maintainerr.transport.get",
            return_value=True,
        ):
            _, stderr = _capture_stderr_stdout(
                main,
                ["--config", str(self.cfg_path), "health"],
            )
        self.assertIn(AUTH_DISABLED_WARNING, stderr)
        # ``maintainerr:`` prefix is added by warn_once.
        self.assertIn(f"{SERVICE_NAME}:", stderr)

    def test_main_quiet_suppresses_warning(self) -> None:
        # REQ-9 AC1: ``--quiet`` suppresses the auth-disabled warning
        # entirely (no stderr line from warn_once).
        with patch(
            "arr_cli.maintainerr.transport.get",
            return_value=True,
        ):
            _, stderr = _capture_stderr_stdout(
                main,
                ["--config", str(self.cfg_path), "--quiet", "health"],
            )
        self.assertNotIn(AUTH_DISABLED_WARNING, stderr)

    def test_main_warning_fires_once_per_invocation(self) -> None:
        # REQ-9 AC1: the warn-once set must reset each invocation;
        # running main() twice produces two warnings, one per call.
        with patch(
            "arr_cli.maintainerr.transport.get",
            return_value=True,
        ):
            _, stderr_first = _capture_stderr_stdout(
                main,
                ["--config", str(self.cfg_path), "health"],
            )
        with patch(
            "arr_cli.maintainerr.transport.get",
            return_value=True,
        ):
            _, stderr_second = _capture_stderr_stdout(
                main,
                ["--config", str(self.cfg_path), "health"],
            )
        self.assertIn(AUTH_DISABLED_WARNING, stderr_first)
        self.assertIn(AUTH_DISABLED_WARNING, stderr_second)
        # Each invocation emits exactly one line containing the
        # canonical warning substring.
        self.assertEqual(
            stderr_first.count(AUTH_DISABLED_WARNING), 1
        )
        self.assertEqual(
            stderr_second.count(AUTH_DISABLED_WARNING), 1
        )

    def test_main_no_warning_when_auth_enabled(self) -> None:
        # When the operator has explicitly turned auth on, no warning
        # is needed -- the request carries credentials.
        auth_on_path = _write_toml_config(self.tmp_dir, auth_enabled=True)
        with patch(
            "arr_cli.maintainerr.transport.get",
            return_value=True,
        ):
            _, stderr = _capture_stderr_stdout(
                main,
                ["--config", str(auth_on_path), "health"],
            )
        self.assertNotIn(AUTH_DISABLED_WARNING, stderr)

    def test_main_pending_hits_overlay_data(self) -> None:
        with patch(
            "arr_cli.maintainerr.transport.get",
            return_value=[],
        ) as mock_get:
            exit_code = main(
                ["--config", str(self.cfg_path), "pending"]
            )
        self.assertEqual(exit_code, 0)
        positional = mock_get.call_args.args
        self.assertEqual(positional[0], "maintainerr")
        self.assertEqual(positional[1], "/api/collections/overlay-data")

    def test_main_storage_hits_storage_metrics(self) -> None:
        with patch(
            "arr_cli.maintainerr.transport.get",
            return_value=[],
        ) as mock_get:
            exit_code = main(
                ["--config", str(self.cfg_path), "storage"]
            )
        self.assertEqual(exit_code, 0)
        positional = mock_get.call_args.args
        self.assertEqual(positional[1], "/api/storage-metrics")

    def test_main_health_hits_health_ready(self) -> None:
        with patch(
            "arr_cli.maintainerr.transport.get",
            return_value=True,
        ) as mock_get:
            exit_code = main(
                ["--config", str(self.cfg_path), "health"]
            )
        self.assertEqual(exit_code, 0)
        positional = mock_get.call_args.args
        self.assertEqual(positional[1], "/api/health/ready")

    def test_main_pending_emits_payload(self) -> None:
        payload = [{"title": "Old Movies", "mediaCount": 42}]
        with patch(
            "arr_cli.maintainerr.transport.get",
            return_value=payload,
        ):
            stdout, _ = _capture_stderr_stdout(
                main,
                ["--config", str(self.cfg_path), "pending"],
            )
        self.assertEqual(json.loads(stdout), payload)

    def test_main_health_emits_scalar(self) -> None:
        # The readiness probe returns a bare boolean; the JSON
        # pass-through must serialise it as ``true``.
        with patch(
            "arr_cli.maintainerr.transport.get",
            return_value=True,
        ):
            stdout, _ = _capture_stderr_stdout(
                main,
                ["--config", str(self.cfg_path), "health"],
            )
        self.assertEqual(json.loads(stdout), True)


# ---------------------------------------------------------------------------
# Test: auth header policy (REQ-9 AC1)
# ---------------------------------------------------------------------------


class TestAuthHeaderPolicy(unittest.TestCase):
    """The transport layer is the single source of auth-header truth.

    The Maintainerr module's job is to delegate to ``transport.get``;
    the transport layer injects no auth header when
    ``cfg.maintainerr.auth_enabled`` is False (the documented default,
    REQ-2 AC4) and adds the configured ``extra`` headers when
    ``auth_enabled`` is True. These tests don't re-verify the
    injection contract (already covered in test_transport.py) but they
    confirm the Maintainerr module doesn't bypass that path -- every
    command handler must call ``transport.get(..., service="maintainerr")``.
    """

    def test_all_commands_use_maintainerr_service(self) -> None:
        cfg = _service_config()
        cases = [
            (cmd_pending, _namespace()),
            (cmd_storage, _namespace()),
            (cmd_health, _namespace()),
        ]
        for handler, args in cases:
            with _patched_get_payload([]) as mock_get:
                handler(args, cfg)
            positional = mock_get.call_args.args
            self.assertEqual(
                positional[0],
                "maintainerr",
                msg=f"{handler.__name__} did not use maintainerr service",
            )


# ---------------------------------------------------------------------------
# Test: 401/403 guidance (REQ-9 AC6)
# ---------------------------------------------------------------------------


class TestAuthErrorGuidance(unittest.TestCase):
    """On a 401/403 from a Maintainerr endpoint, transport surfaces guidance.

    The guidance lives in the transport layer (per task 4); these
    tests verify the Maintainerr module propagates the
    :class:`AuthError` unchanged so the wrapper can surface the
    guidance on stderr.
    """

    def test_401_propagates_with_guidance_in_message(self) -> None:
        cfg = _service_config()
        args = _namespace()
        guidance_message = (
            "maintainerr: 401/403 received -- set auth.enabled=true "
            "in arr.conf and restart -- maintainerr: 401  for "
            "/api/health/ready"
        )
        with _patched_get_raising(
            AuthError(
                "maintainerr",
                "/api/health/ready",
                guidance_message,
            )
        ):
            with self.assertRaises(AuthError) as ctx:
                cmd_health(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 2)
        self.assertIn(
            "set auth.enabled=true in arr.conf and restart",
            ctx.exception.message,
        )

    def test_404_propagates_as_http_error(self) -> None:
        # The endpoint doesn't exist; transport maps it to
        # HttpError(exit_code=4).
        cfg = _service_config()
        args = _namespace()
        with _patched_get_raising(
            HttpError(
                "maintainerr",
                "/api/collections/overlay-data",
                (
                    "maintainerr: HTTP 404 for "
                    "/api/collections/overlay-data"
                ),
                status=404,
            )
        ):
            with self.assertRaises(HttpError) as ctx:
                cmd_pending(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 4)
        self.assertEqual(ctx.exception.status, 404)


# ---------------------------------------------------------------------------
# Test: missing service section (REQ-1 AC6)
# ---------------------------------------------------------------------------


class TestMissingSection(unittest.TestCase):
    """A missing ``[maintainerr]`` section surfaces a structured ``AuthError``.

    The transport layer's :func:`_resolve_auth` raises
    :class:`AuthError` when ``cfg.maintainerr`` is ``None``. The
    handler does not need to special-case this; the wrapper surfaces
    the structured stderr line and exit code 2.
    """

    def test_missing_section_raises_auth_error(self) -> None:
        cfg = _service_config(include_maintainerr=False)
        args = _namespace()
        with _patched_get_raising(
            AuthError(
                "maintainerr",
                "auth",
                (
                    "maintainerr: section missing in arr.conf -- add "
                    "a maintainerr: block with url"
                ),
            )
        ):
            with self.assertRaises(AuthError) as ctx:
                cmd_pending(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 2)
        self.assertIn("section missing", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
