"""Unit tests for :mod:`arr_cli.seerr` (task 12).

Minimal representative tests covering the MVP contract for the Seerr
CLI. Uses ``unittest`` + ``unittest.mock`` (no pytest, no ``responses``).
"""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()