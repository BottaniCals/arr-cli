"""Unit tests for :mod:`arr_cli.seerr` (task 12).

Minimal representative tests covering the MVP contract for the Seerr
CLI. Uses ``unittest`` + ``unittest.mock`` (no pytest, no ``responses``).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import unittest
from unittest.mock import patch


def _capture_stdout(callable_: object, *args: object, **kwargs: object) -> str:
    """Invoke ``callable_`` with stdout redirected to a StringIO buffer."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        callable_(*args, **kwargs)  # type: ignore[operator]
    return buffer.getvalue()


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


if __name__ == "__main__":
    unittest.main()