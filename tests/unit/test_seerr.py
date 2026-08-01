"""Unit tests for :mod:`arr_cli.seerr` (task 12).

Minimal representative tests covering the MVP contract for the Seerr
CLI. Uses ``unittest`` + ``unittest.mock`` (no pytest, no ``responses``).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import responses


def _capture_stdout(callable_: object, *args: object, **kwargs: object) -> str:
    """Invoke ``callable_`` with stdout redirected to a StringIO buffer."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        callable_(*args, **kwargs)  # type: ignore[operator]
    return buffer.getvalue()


def _capture_stderr_stdout(
    callable_: object, *args: object, **kwargs: object
) -> tuple[str, str]:
    """Run ``callable_`` with stdout and stderr captured separately.

    Swallows ``SystemExit`` so the caller can inspect both streams
    without first catching the exception (mirrors the cli_common and
    jellyfin test helpers).
    """
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    with contextlib.redirect_stdout(stdout_buf), \
            contextlib.redirect_stderr(stderr_buf):
        try:
            callable_(*args, **kwargs)  # type: ignore[operator]
        except SystemExit:
            pass
    return stdout_buf.getvalue(), stderr_buf.getvalue()


def _write_toml_config(tmp_dir: Path) -> Path:
    """Write a minimal valid TOML config under ``tmp_dir``.

    Populates all five services with placeholder values so
    :func:`load_config` succeeds. The Seerr-specific field
    (``api_key``) is required by the test scenarios.
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
        '[maintainerr]\n'
        'url = "https://maintainerr.example"\n'
        'auth_enabled = false\n'
        '\n'
        '[seerr]\n'
        'url = "https://seerr.example"\n'
        'api_key = "***"\n'
    )
    path = tmp_dir / "arr.toml"
    path.write_text(body, encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    return path


class TestSeerrModule(unittest.TestCase):
    """Five focused tests covering the task 12 contract."""

    def test_seerr_module_loads(self) -> None:
        """The seerr module imports cleanly and exposes the expected public surface."""
        import arr_cli.seerr as seerr
        # Sanity-check that the public surface we depend on is present.
        self.assertTrue(callable(getattr(seerr, "main", None)))
        self.assertTrue(callable(getattr(seerr, "build_seerr_parser", None)))
        self.assertTrue(callable(getattr(seerr, "_dispatch", None)))

    def test_build_seerr_parser_top_level(self) -> None:
        """``build_seerr_parser()`` returns an ``argparse.ArgumentParser``."""
        from arr_cli.seerr import build_seerr_parser

        parser = build_seerr_parser()
        self.assertIsInstance(parser, argparse.ArgumentParser)

    def test_seerr_has_six_commands(self) -> None:
        """The subparser exposes exactly the six documented Seerr commands."""
        from arr_cli.seerr import build_seerr_parser

        parser = build_seerr_parser()
        # Drill in: the subparsers action holds the registered choices.
        subparsers_action = next(
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(
            set(subparsers_action.choices.keys()),
            {"requests", "request-count", "search", "available", "media", "user"},
        )
        self.assertEqual(len(subparsers_action.choices), 6)

    def test_dispatch_table_keys(self) -> None:
        """``_dispatch`` maps every command name to a callable handler."""
        import arr_cli.seerr as seerr

        expected_commands = {
            "requests",
            "request-count",
            "search",
            "available",
            "media",
            "user",
        }
        # Inspect the private dispatch table directly so we cover
        # the registration contract without going through argparse.
        dispatch_table = getattr(seerr, "_DISPATCH", None)
        self.assertIsNotNone(dispatch_table, "_DISPATCH table missing")
        self.assertEqual(set(dispatch_table.keys()), expected_commands)
        for name, handler in dispatch_table.items():
            self.assertTrue(
                callable(handler),
                f"dispatch handler for {name!r} is not callable",
            )

    @patch("arr_cli.facade.transport.get")
    def test_main_help_exits_cleanly(self, _mock_get) -> None:
        """Running ``seerr(["--help"])`` returns exit code 0 cleanly."""
        import arr_cli.seerr as seerr

        # ``main_wrapper`` swallows argparse's ``SystemExit(0)`` for
        # ``--help`` and returns the int code; either form counts as
        # "exits cleanly with code 0".
        exit_code = seerr.main(["--help"])
        self.assertEqual(exit_code, 0)


# ---------------------------------------------------------------------------
# Test: --verbose flag flip for cmd_requests
# ---------------------------------------------------------------------------


class TestVerboseFlagCmdRequests(unittest.TestCase):
    """REQ-6 AC4: ``cmd_requests`` summary vs verbose paths."""

    def test_cmd_requests_default_emits_summary(self) -> None:
        from arr_cli.seerr import cmd_requests

        args = argparse.Namespace(
            config=None,
            debug=False,
            quiet=False,
            human=False,
            verbose=False,
            connect_timeout=5.0,
            read_timeout=30.0,
            retry=0,
            deadline=None,
            limit=20,
            command="requests",
        )
        payload = [
            {
                "title": "Foo",
                "type": "movie",
                "status": "pending",
                "createdAt": "2024-01-01",
                "requestedBy": {"displayName": "alice"},
            }
        ]
        with patch("arr_cli.seerr.transport.get", return_value=payload):
            output = _capture_stdout(cmd_requests, args, None)
        rendered = json.loads(output)
        self.assertEqual(rendered[0]["title"], "Foo")
        self.assertEqual(rendered[0]["type"], "movie")
        self.assertEqual(rendered[0]["status"], "pending")
        self.assertEqual(rendered[0]["requestedBy"]["displayName"], "alice")

    def test_cmd_requests_verbose_emits_verbatim(self) -> None:
        from arr_cli.seerr import cmd_requests

        args = argparse.Namespace(
            config=None,
            debug=False,
            quiet=False,
            human=False,
            verbose=True,
            connect_timeout=5.0,
            read_timeout=30.0,
            retry=0,
            deadline=None,
            limit=20,
            command="requests",
        )
        payload = [
            {
                "title": "Foo",
                "type": "movie",
                "status": "pending",
            }
        ]
        with patch("arr_cli.seerr.transport.get", return_value=payload):
            output = _capture_stdout(cmd_requests, args, None)
        self.assertEqual(json.loads(output), payload)


# ---------------------------------------------------------------------------
# Test: HTTP 4xx exit-code contract for ``seerr user`` (bug review)
# ---------------------------------------------------------------------------


class TestCmdUserHttpErrors(unittest.TestCase):
    """Regression tests pinning the exit-code contract for ``seerr user``.

    The bug review identified the seerr ``user`` two-step probe
    (REQ-10 AC6) as the one site outside :mod:`arr_cli.facade.cli_common`
    that catches ``HttpError``. If the bare ``raise`` in
    ``_try_user_path`` were ever replaced with a swallowed return, the
    CLI would silently report a 400 / 404 as exit ``0`` and break
    operator-side retry logic.

    These tests exercise the documented end-to-end contract:

    * HTTP 400 on the primary path bubbles up unchanged (exit 4,
      structured stderr line naming the primary path).
    * HTTP 404 on both paths bubbles up as a fresh ``HttpError`` that
      names both probe paths (exit 4, structured stderr line).
    """

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="seerr-test-"))
        self.cfg_path = _write_toml_config(self.tmp_dir)

    def tearDown(self) -> None:
        import shutil
        try:
            shutil.rmtree(self.tmp_dir)
        except OSError:
            pass

    def test_cmd_user_400_propagates_with_exit_four(self) -> None:
        # Regression: HTTP 400 on the primary ``/api/v1/user/me``
        # path must bubble up unchanged (the fallback path is NOT
        # tried because 400 is not 404). The structured stderr line
        # surfaces with the underlying HTTP status and the process
        # exits 4. Mirrors the bug-report repro for ``seerr user``.
        import arr_cli.seerr as seerr

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/user/me",
                status=400,
                body="request/params/userId must be number",
            )
            # Single invocation: ``main`` returns the int exit code
            # directly (no SystemExit) and writes the structured
            # line to stderr which we redirect manually.
            stderr_buf = io.StringIO()
            with contextlib.redirect_stderr(stderr_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "user"]
                )
            stderr = stderr_buf.getvalue()
            # The fallback path MUST NOT have been tried (the
            # bug-review confirms only a 404 triggers the fallback).
            # No registered mock means a real network call would be
            # attempted; the strict ``responses`` mock would have
            # raised ``ConnectionError`` if the fallback fired, so
            # by construction this is enforced.
            self.assertEqual(len(rsps.calls), 1)
        self.assertEqual(exit_code, 4)
        # Structured line: service=seerr op=/api/v1/user/me status=400 message=...
        self.assertTrue(
            stderr.startswith(
                "service=seerr op=/api/v1/user/me status=400 message="
            ),
            msg=f"unexpected stderr shape: {stderr!r}",
        )
        self.assertIn("HTTP 400 for /api/v1/user/me", stderr)
        self.assertIn("request/params/userId must be number", stderr)

    def test_cmd_user_both_404_exits_four(self) -> None:
        # Regression: HTTP 404 on BOTH the primary and fallback paths
        # surfaces a fresh :class:`HttpError(exit_code=4)` that names
        # both paths so operators can investigate. The fallback path
        # IS exercised (status == 404 is the documented trigger).
        import arr_cli.seerr as seerr

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/user/me",
                status=404,
                body="Not Found",
            )
            rsps.add(
                responses.GET,
                "https://seerr.example/auth/me",
                status=404,
                body="Not Found",
            )
            # Single invocation: ``main`` returns the int exit code
            # directly (no SystemExit) and writes the structured
            # line to stderr which we redirect manually. Re-using the
            # helper would double the request count and obscure the
            # ``len(rsps.calls) == 2`` check below.
            stderr_buf = io.StringIO()
            with contextlib.redirect_stderr(stderr_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "user"]
                )
            stderr = stderr_buf.getvalue()
            # Both registered mocks fired (fallback path was triggered).
            # Checked inside the ``with`` block because ``responses``
            # resets its call list when the context manager exits.
            self.assertEqual(len(rsps.calls), 2)
        self.assertEqual(exit_code, 4)
        # The synthesized HttpError names the operator-level ``user``
        # op and references both probe paths so the operator can
        # investigate the missing auth self-check endpoint.
        self.assertTrue(
            stderr.startswith("service=seerr op=user status=404 message="),
            msg=f"unexpected stderr shape: {stderr!r}",
        )
        self.assertIn("/api/v1/user/me", stderr)
        self.assertIn("/auth/me", stderr)


if __name__ == "__main__":
    unittest.main()