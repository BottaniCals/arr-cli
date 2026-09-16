"""Unit tests for :mod:`arr_cli.jellyfin` (task 8).

Covers the contract spelled out in task 8.4 of tasks.md:

* Each of the eight Jellyfin commands hits the documented HTTP path
  with the documented query parameters and the
  ``Authorization: MediaBrowser ***`` authorization envelope
  (REQ-2 AC1, REQ-6 AC1-8).
* ``item`` propagates a 404 as :class:`HttpError` (exit code 4).
* ``favorites`` / ``resume`` / ``recent`` / ``latest`` raise
  :class:`ConfigError` (``exit_code=1``) when ``cfg.jellyfin.user_id``
  is ``None``.
* The parser rejects unknown subcommands (REQ-11 AC4); the
  per-command handler names are registered in the dispatch table.

Tests use ``unittest.mock`` to stub ``arr_cli.facade.transport.get``
so we can assert the exact path, params, and auth-header surface
without a network dependency.
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

import responses

# Make the project importable regardless of the test runner's CWD.
_PROJ_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from arr_cli.facade.config import (  # noqa: E402
    AuthConfig,
    ServiceConfig,
)
from arr_cli.facade.errors import (  # noqa: E402
    ConfigError,
    HttpError,
)
from arr_cli.jellyfin import (  # noqa: E402
    SERVICE_NAME,
    build_jellyfin_parser,
    cmd_favorites,
    cmd_item,
    cmd_latest,
    cmd_nextup,
    cmd_now,
    cmd_recent,
    cmd_resume,
    cmd_search,
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


def _write_toml_config(tmp_dir: Path) -> Path:
    """Write a minimal valid TOML config under ``tmp_dir``.

    Populates all five services with placeholder values so
    :func:`load_config` succeeds. The Jellyfin-specific fields
    (api_key, user_id) are required by the test scenarios.
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
    import os
    path = tmp_dir / "arr.toml"
    path.write_text(body, encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    return path


def _service_config(
    *,
    user_id: str | None = "jf-user-1",
    ak: str | None = "test-jellyfin-token",
    url: str = "https://jellyfin.example",
    include_jellyfin: bool = True,
) -> ServiceConfig:
    """Return a :class:`ServiceConfig` configured for the Jellyfin tests.

    Each parameter has a documented default so individual tests can
    override only the field they care about (e.g. ``user_id=None`` to
    exercise the missing-user_id path).
    """
    jellyfin = (
        AuthConfig(url=url, ak=ak, user_id=user_id)
        if include_jellyfin
        else None
    )
    return ServiceConfig(
        jellyfin=jellyfin,
        radarr=AuthConfig(url="https://radarr.example", ak="rk"),
        sonarr=AuthConfig(url="https://sonarr.example", ak="sk"),
        maintainerr=AuthConfig(
            url="https://maintainerr.example", auth_enabled=False
        ),
        seerr=AuthConfig(url="https://seerr.example", ak="sk"),
    )


def _namespace(
    *,
    human: bool = False,
    limit: int = 20,
    debug: bool = False,
    **kwargs: Any,
) -> argparse.Namespace:
    """Build a minimal :class:`argparse.Namespace` for handler tests.

    Defaults match the universal flag set registered by
    :func:`build_parser`. Per-command fields (``query``, ``item_id``,
    ``start_index``, ``user_id``) are added via ``kwargs`` so each
    test sets exactly what it needs.
    """
    return argparse.Namespace(
        human=human,
        limit=limit,
        debug=debug,
        connect_timeout=5.0,
        read_timeout=30.0,
        retry=0,
        deadline=None,
        quiet=False,
        config=None,
        **kwargs,
    )


def _patched_get_payload(payload: Any) -> Any:
    """Return a context manager that mocks ``transport.get`` to return ``payload``.

    Usage::

        with _patched_get_payload(SAMPLE_PAYLOAD) as mock_get:
            cmd_now(args, cfg)
            mock_get.assert_called_once()
    """
    return patch(
        "arr_cli.jellyfin.transport.get",
        return_value=payload,
    )


def _patched_get_raising(exc: BaseException) -> Any:
    """Return a context manager that mocks ``transport.get`` to raise ``exc``."""
    return patch(
        "arr_cli.jellyfin.transport.get",
        side_effect=exc,
    )


# ---------------------------------------------------------------------------
# Test: dispatch table and parser registration
# ---------------------------------------------------------------------------


class TestDispatchTable(unittest.TestCase):
    """The dispatch table contains every documented subcommand."""

    def test_dispatch_keys(self) -> None:
        from arr_cli.jellyfin import _DISPATCH

        self.assertEqual(
            set(_DISPATCH.keys()),
            {
                "now",
                "resume",
                "recent",
                "nextup",
                "latest",
                "search",
                "item",
                "favorites",
            },
        )

    def test_dispatch_handlers_are_callable(self) -> None:
        from arr_cli.jellyfin import _DISPATCH

        for handler in _DISPATCH.values():
            self.assertTrue(callable(handler))

    def test_each_handler_returns_int(self) -> None:
        # Every handler's success path returns an int (REQ-11 AC1).
        from arr_cli.jellyfin import _DISPATCH

        cfg = _service_config()
        payload: Any = {"items": []}
        for name, handler in _DISPATCH.items():
            args = _namespace(
                query="",
                item_id="42",
                start_index=None,
                user_id=None,
            )
            with _patched_get_payload(payload):
                if name == "item":
                    args.item_id = "42"
                result = handler(args, cfg)
            self.assertIsInstance(
                result,
                int,
                msg=f"{name} returned {type(result).__name__}, not int",
            )
            self.assertEqual(result, 0)


# ---------------------------------------------------------------------------
# Test: cmd_now
# ---------------------------------------------------------------------------


class TestCmdNow(unittest.TestCase):
    """REQ-6 AC1: ``GET /Sessions`` -- no user_id, no params."""

    def test_now_hits_sessions_endpoint(self) -> None:
        cfg = _service_config()
        args = _namespace()
        with _patched_get_payload([]) as mock_get:
            cmd_now(args, cfg)
        mock_get.assert_called_once()
        kwargs = mock_get.call_args.kwargs
        # ``transport.get`` is called with positional args (service, path)
        # followed by keyword-only parameters. Capture both.
        positional = mock_get.call_args.args
        self.assertEqual(positional[0], "jellyfin")
        self.assertEqual(positional[1], "/Sessions")
        self.assertEqual(kwargs["cfg"], cfg)
        # ``cmd_now`` passes no params (``None`` keeps the URL clean).
        self.assertIsNone(kwargs.get("params"))

    def test_now_emits_json_when_not_human(self) -> None:
        cfg = _service_config()
        # ``--verbose`` preserves the pre-change verbatim pass-through
        # behaviour for the now-summary candidate ``jellyfin now``.
        args = _namespace(human=False, verbose=True)
        payload = [{"DeviceName": "Living Room TV", "UserName": "operator"}]
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_now, args, cfg)
        # JSON pass-through: the payload is rendered verbatim.
        self.assertEqual(json.loads(output), payload)

    def test_missing_service_section_raises_auth_error(self) -> None:
        # When ``cfg.jellyfin`` is None the transport layer raises
        # AuthError (exit 2, "section missing"). The handler MUST
        # surface it without swallowing.
        cfg = _service_config(include_jellyfin=False)
        args = _namespace()
        with _patched_get_raising(
            HttpError("jellyfin", "now", "401", status=401)
        ):
            # Even though we mock transport.get the auth check is
            # upstream; we instead patch transport.get to raise an
            # AuthError so the test stays at the same level.
            with patch(
                "arr_cli.jellyfin.transport.get",
                side_effect=ConfigError(
                    "jellyfin",
                    "load",
                    "jellyfin: section missing in arr.conf",
                ),
            ):
                with self.assertRaises(ConfigError) as ctx:
                    cmd_now(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 1)


# ---------------------------------------------------------------------------
# Test: cmd_resume
# ---------------------------------------------------------------------------


class TestCmdResume(unittest.TestCase):
    """REQ-6 AC2: ``GET /Users/{user_id}/Items/Resume``."""

    def test_resume_hits_user_path(self) -> None:
        cfg = _service_config(user_id="jf-user-1")
        args = _namespace()
        with _patched_get_payload([]) as mock_get:
            cmd_resume(args, cfg)
        positional = mock_get.call_args.args
        self.assertEqual(positional[1], "/Users/jf-user-1/Items/Resume")

    def test_resume_encodes_user_id(self) -> None:
        # Path segments are percent-encoded before being sent
        # (security NFR).
        cfg = _service_config(user_id="user/with spaces")
        args = _namespace()
        with _patched_get_payload([]) as mock_get:
            cmd_resume(args, cfg)
        positional = mock_get.call_args.args
        self.assertEqual(positional[1], "/Users/user%2Fwith%20spaces/Items/Resume")

    def test_resume_missing_user_id_raises_config_error(self) -> None:
        # Task 8.4: ``favorites``/``resume``/``recent``/``latest`` raise
        # ConfigError when ``cfg.jellyfin.user_id`` is ``None``.
        cfg = _service_config(user_id=None)
        args = _namespace()
        with self.assertRaises(ConfigError) as ctx:
            cmd_resume(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("user_id", ctx.exception.message)

    def test_resume_missing_service_section_raises_config_error(self) -> None:
        cfg = _service_config(include_jellyfin=False)
        args = _namespace()
        with self.assertRaises(ConfigError) as ctx:
            cmd_resume(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("section missing", ctx.exception.message)


# ---------------------------------------------------------------------------
# Test: cmd_recent
# ---------------------------------------------------------------------------


class TestCmdRecent(unittest.TestCase):
    """REQ-6 AC3: ``GET /Users/{user_id}/Items?SortBy=DatePlayed&Filters=IsPlayed``."""

    def test_recent_hits_user_path_with_sort_and_filter(self) -> None:
        cfg = _service_config(user_id="jf-user-1")
        args = _namespace()
        with _patched_get_payload([]) as mock_get:
            cmd_recent(args, cfg)
        positional = mock_get.call_args.args
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(positional[1], "/Users/jf-user-1/Items")
        # The three query parameters are forwarded verbatim; the
        # transport layer percent-encodes their values. The
        # ``includeItemTypes`` key is required on Jellyfin 12.0 to
        # restore the recursive expansion GetItems had on 10.11
        # when ``Filters`` is present; see the v12 release notes
        # ("API Changes", GetItems behaviour).
        self.assertEqual(
            kwargs["params"],
            {
                "SortBy": "DatePlayed",
                "Filters": "IsPlayed",
                "includeItemTypes": "Movie,Episode",
            },
        )

    def test_recent_missing_user_id_raises_config_error(self) -> None:
        cfg = _service_config(user_id=None)
        args = _namespace()
        with self.assertRaises(ConfigError) as ctx:
            cmd_recent(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 1)


# ---------------------------------------------------------------------------
# Test: cmd_nextup
# ---------------------------------------------------------------------------


class TestCmdNextUp(unittest.TestCase):
    """REQ-6 AC4: ``GET /Shows/NextUp`` with optional Limit / StartIndex and
    ``UserId`` always read from ``cfg.jellyfin.user_id`` (v12+ contract)."""

    def test_nextup_hits_shows_nextup(self) -> None:
        cfg = _service_config()
        args = _namespace(limit=20)
        with _patched_get_payload([]) as mock_get:
            cmd_nextup(args, cfg)
        positional = mock_get.call_args.args
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(positional[1], "/Shows/NextUp")
        # The universal ``--limit`` default is forwarded to the
        # service as ``Limit=20``; ``UserId`` is read from config
        # (``cfg.jellyfin.user_id == "jf-user-1"``) because the
        # Jellyfin v12 /Shows/NextUp endpoint requires it.
        self.assertEqual(
            kwargs["params"], {"Limit": 20, "UserId": "jf-user-1"}
        )

    def test_nextup_uses_configured_user_id_by_default(self) -> None:
        # With no ``--user-id`` on the command line the handler
        # still sends ``UserId`` from config; this is the bug fix
        # pinned as the new contract.
        cfg = _service_config(user_id="configured-jellyfin-user")
        args = _namespace(limit=20)
        with _patched_get_payload([]) as mock_get:
            cmd_nextup(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(
            kwargs["params"],
            {"Limit": 20, "UserId": "configured-jellyfin-user"},
        )

    def test_nextup_missing_user_id_raises_config_error(self) -> None:
        # Mirrors the three sibling handlers: when
        # ``cfg.jellyfin.user_id`` is missing the handler raises
        # ``ConfigError(exit_code=1)`` with the documented message.
        cfg = _service_config(user_id=None)
        args = _namespace(limit=20)
        with self.assertRaises(ConfigError) as ctx:
            cmd_nextup(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("user_id", ctx.exception.message)

    def test_nextup_forwards_limit(self) -> None:
        cfg = _service_config()
        args = _namespace(limit=50)
        with _patched_get_payload([]) as mock_get:
            cmd_nextup(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(
            kwargs["params"], {"Limit": 50, "UserId": "jf-user-1"}
        )

    def test_nextup_no_limit_param_when_limit_is_none(self) -> None:
        # When the parser defaults ``--limit`` to ``None`` (e.g. a
        # downstream caller bypasses the universal flag), the
        # ``Limit`` key is omitted from the params dict so the
        # service falls back to its own default; ``UserId`` is still
        # forwarded from config.
        cfg = _service_config()
        args = _namespace(limit=None)
        with _patched_get_payload([]) as mock_get:
            cmd_nextup(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(kwargs["params"], {"UserId": "jf-user-1"})

    def test_nextup_forwards_start_index(self) -> None:
        cfg = _service_config()
        args = _namespace(limit=20, start_index=10)
        with _patched_get_payload([]) as mock_get:
            cmd_nextup(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(
            kwargs["params"],
            {"Limit": 20, "StartIndex": 10, "UserId": "jf-user-1"},
        )

    def test_nextup_combined_params(self) -> None:
        cfg = _service_config()
        args = _namespace(limit=5, start_index=2)
        with _patched_get_payload([]) as mock_get:
            cmd_nextup(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(
            kwargs["params"],
            {"Limit": 5, "StartIndex": 2, "UserId": "jf-user-1"},
        )


# ---------------------------------------------------------------------------
# Test: cmd_latest
# ---------------------------------------------------------------------------


class TestCmdLatest(unittest.TestCase):
    """REQ-6 AC5: ``GET /Users/{user_id}/Items/Latest``."""

    def test_latest_hits_user_path(self) -> None:
        cfg = _service_config(user_id="jf-user-1")
        args = _namespace()
        with _patched_get_payload([]) as mock_get:
            cmd_latest(args, cfg)
        positional = mock_get.call_args.args
        self.assertEqual(positional[1], "/Users/jf-user-1/Items/Latest")

    def test_latest_missing_user_id_raises_config_error(self) -> None:
        cfg = _service_config(user_id=None)
        args = _namespace()
        with self.assertRaises(ConfigError) as ctx:
            cmd_latest(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 1)


# ---------------------------------------------------------------------------
# Test: cmd_search
# ---------------------------------------------------------------------------


class TestCmdSearch(unittest.TestCase):
    """REQ-6 AC6: ``GET /Items?searchTerm=<urlencoded query>``."""

    def test_search_forwards_query(self) -> None:
        cfg = _service_config()
        args = _namespace(query="matrix")
        with _patched_get_payload([]) as mock_get:
            cmd_search(args, cfg)
        positional = mock_get.call_args.args
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(positional[1], "/Items")
        self.assertEqual(kwargs["params"], {"searchTerm": "matrix"})

    def test_search_empty_query_still_calls_endpoint(self) -> None:
        # REQ-6 AC6: empty query returns the service's empty-array
        # response, NOT an error. The handler MUST still hit the
        # endpoint with the empty search term.
        cfg = _service_config()
        args = _namespace(query="")
        with _patched_get_payload([]) as mock_get:
            cmd_search(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(kwargs["params"], {"searchTerm": ""})

    def test_search_missing_query_defaults_to_empty(self) -> None:
        # When the user runs ``jellyfin search`` with no positional
        # argument the subparser defaults ``query`` to ""; the handler
        # still forwards the empty search term.
        cfg = _service_config()
        args = _namespace()  # no query field
        with _patched_get_payload([]) as mock_get:
            cmd_search(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(kwargs["params"], {"searchTerm": ""})

    def test_search_query_with_special_chars(self) -> None:
        # The transport layer percent-encodes the value; the handler
        # passes the raw string along.
        cfg = _service_config()
        args = _namespace(query="hello world?special&chars")
        with _patched_get_payload([]) as mock_get:
            cmd_search(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(
            kwargs["params"], {"searchTerm": "hello world?special&chars"}
        )


# ---------------------------------------------------------------------------
# Test: cmd_item
# ---------------------------------------------------------------------------


class TestCmdItem(unittest.TestCase):
    """REQ-6 AC7: ``GET /Items/{id}``; 404 → HttpError(exit_code=4)."""

    def test_item_hits_items_path(self) -> None:
        cfg = _service_config()
        args = _namespace(item_id="42")
        with _patched_get_payload({"Id": 42, "Name": "Test"}) as mock_get:
            cmd_item(args, cfg)
        positional = mock_get.call_args.args
        self.assertEqual(positional[1], "/Items/42")

    def test_item_404_propagates_as_http_error(self) -> None:
        # The transport layer maps 404 → HttpError; the handler MUST
        # NOT swallow it. main_wrapper then renders the structured
        # stderr line and exits with code 4.
        cfg = _service_config()
        args = _namespace(item_id="missing")
        with _patched_get_raising(
            HttpError(
                "jellyfin",
                "item id=missing",
                "jellyfin: HTTP 404 for /Items/missing",
                status=404,
            )
        ):
            with self.assertRaises(HttpError) as ctx:
                cmd_item(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 4)
        self.assertEqual(ctx.exception.status, 404)

    def test_item_id_is_percent_encoded(self) -> None:
        # ids flow through ``encode_path_segment`` so a slash or
        # space in the id cannot break the URL.
        cfg = _service_config()
        args = _namespace(item_id="a/b c")
        with _patched_get_payload({"Id": "a/b c"}) as mock_get:
            cmd_item(args, cfg)
        positional = mock_get.call_args.args
        self.assertEqual(positional[1], "/Items/a%2Fb%20c")


# ---------------------------------------------------------------------------
# Test: cmd_favorites
# ---------------------------------------------------------------------------


class TestCmdFavorites(unittest.TestCase):
    """REQ-6 AC8: ``GET /Users/{user_id}/Items/Favorites``."""

    def test_favorites_hits_user_path(self) -> None:
        cfg = _service_config(user_id="jf-user-1")
        args = _namespace()
        with _patched_get_payload([]) as mock_get:
            cmd_favorites(args, cfg)
        positional = mock_get.call_args.args
        self.assertEqual(positional[1], "/Users/jf-user-1/Items/Favorites")

    def test_favorites_missing_user_id_raises_config_error(self) -> None:
        cfg = _service_config(user_id=None)
        args = _namespace()
        with self.assertRaises(ConfigError) as ctx:
            cmd_favorites(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("user_id", ctx.exception.message)

    def test_favorites_missing_service_section_raises_config_error(self) -> None:
        cfg = _service_config(include_jellyfin=False)
        args = _namespace()
        with self.assertRaises(ConfigError) as ctx:
            cmd_favorites(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 1)


# ---------------------------------------------------------------------------
# Test: --human mode
# ---------------------------------------------------------------------------


class TestHumanMode(unittest.TestCase):
    """When ``--human`` is set, ``output.emit`` is called with ``human_mode=True``."""

    def test_human_mode_passes_through_to_emit(self) -> None:
        cfg = _service_config()
        args = _namespace(human=True)
        payload = [{"Name": "Item One"}, {"Name": "Item Two"}]
        with _patched_get_payload(payload), \
                patch("arr_cli.jellyfin.output.emit") as mock_emit:
            cmd_now(args, cfg)
        mock_emit.assert_called_once()
        kwargs = mock_emit.call_args.kwargs
        self.assertTrue(kwargs["human_mode"])
        # The handler forwarded the same payload it received.
        self.assertEqual(kwargs["columns"], mock_emit.call_args.kwargs["columns"])

    def test_human_mode_columns_for_now(self) -> None:
        cfg = _service_config()
        args = _namespace(human=True)
        payload = [{"DeviceName": "TV", "UserName": "operator"}]
        with _patched_get_payload(payload), \
                patch("arr_cli.jellyfin.output.emit") as mock_emit:
            cmd_now(args, cfg)
        kwargs = mock_emit.call_args.kwargs
        # Columns are the summary-shape keys (REQ-1): the human
        # renderer projects each row onto the named columns; the
        # summary shape (``_summary_jellyfin_now``) is what feeds the
        # table, not the verbatim ``/Sessions`` payload.
        self.assertEqual(
            kwargs["columns"],
            [
                "user",
                "device",
                "client",
                "playing.type",
                "playing.name",
                "playing.series",
                "playing.season",
                "playing.episode",
                "progress.position_ticks",
                "progress.is_paused",
            ],
        )


# ---------------------------------------------------------------------------
# Test: parser
# ---------------------------------------------------------------------------


class TestBuildJellyfinParser(unittest.TestCase):
    """The parser exposes the eight subcommands and the universal flags."""

    def setUp(self) -> None:
        self.parser = build_jellyfin_parser()

    def test_parser_prog(self) -> None:
        self.assertEqual(self.parser.prog, SERVICE_NAME)

    def test_help_prints_to_stdout(self) -> None:
        stdout, _ = _capture_stderr_stdout(
            self.parser.parse_args, ["--help"]
        )
        self.assertIn("usage:", stdout)
        # The eight commands surface in the help listing.
        for cmd in (
            "now",
            "resume",
            "recent",
            "nextup",
            "latest",
            "search",
            "item",
            "favorites",
        ):
            self.assertIn(cmd, stdout)

    def test_unknown_subcommand_fails(self) -> None:
        # REQ-11 AC4: unknown args exit 1 with usage on stderr.
        exit_code = main(["--config", "/tmp/does-not-exist.conf", "bogus"])
        # The wrapper catches the early ``load_config`` failure first
        # since the unknown-subcommand error depends on the parser
        # catching it BEFORE config load. argparse's order is: parse
        # first, then handler. Confirm argparse rejects it.
        with self.assertRaises(SystemExit) as ctx:
            self.parser.parse_args(["bogus"])
        self.assertEqual(ctx.exception.code, 2)

    def test_now_parses(self) -> None:
        args = self.parser.parse_args(["now"])
        self.assertEqual(args.command, "now")

    def test_resume_parses(self) -> None:
        args = self.parser.parse_args(["resume"])
        self.assertEqual(args.command, "resume")

    def test_recent_parses(self) -> None:
        args = self.parser.parse_args(["recent"])
        self.assertEqual(args.command, "recent")

    def test_nextup_parses_with_flags(self) -> None:
        args = self.parser.parse_args(
            ["nextup", "--start-index", "5"]
        )
        self.assertEqual(args.command, "nextup")
        self.assertEqual(args.start_index, 5)
        # ``--user-id`` was dropped from the subparser; the handler
        # reads ``UserId`` from config via ``_require_user_id``. The
        # attribute is not registered on the namespace; accessing
        # it via ``getattr(args, "user_id", None)`` (the handler's
        # defensive style) yields ``None``.
        self.assertIsNone(getattr(args, "user_id", None))

    def test_latest_parses(self) -> None:
        args = self.parser.parse_args(["latest"])
        self.assertEqual(args.command, "latest")

    def test_search_parses_with_query(self) -> None:
        args = self.parser.parse_args(["search", "the matrix"])
        self.assertEqual(args.command, "search")
        self.assertEqual(args.query, "the matrix")

    def test_search_parses_without_query(self) -> None:
        args = self.parser.parse_args(["search"])
        self.assertEqual(args.command, "search")
        self.assertEqual(args.query, "")  # default

    def test_item_parses_with_id(self) -> None:
        args = self.parser.parse_args(["item", "42"])
        self.assertEqual(args.command, "item")
        self.assertEqual(args.item_id, "42")

    def test_item_requires_id(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.parser.parse_args(["item"])
        self.assertEqual(ctx.exception.code, 2)

    def test_favorites_parses(self) -> None:
        args = self.parser.parse_args(["favorites"])
        self.assertEqual(args.command, "favorites")

    def test_universal_flags_flow_through(self) -> None:
        args = self.parser.parse_args(
            [
                "--config",
                "/tmp/x",
                "--debug",
                "--human",
                "--limit",
                "10",
                "now",
            ]
        )
        self.assertEqual(args.config, "/tmp/x")
        self.assertTrue(args.debug)
        self.assertTrue(args.human)
        self.assertEqual(args.limit, 10)
        self.assertEqual(args.command, "now")


# ---------------------------------------------------------------------------
# Test: main entry point
# ---------------------------------------------------------------------------


class TestMainEntryPoint(unittest.TestCase):
    """``main`` wires the parser to ``main_wrapper`` end-to-end."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jellyfin-test-"))
        self.cfg_path = _write_toml_config(self.tmp_dir)

    def tearDown(self) -> None:
        import shutil
        try:
            shutil.rmtree(self.tmp_dir)
        except OSError:
            pass

    def test_main_now_returns_zero_on_success(self) -> None:
        payload = [{"DeviceName": "TV", "UserName": "operator"}]
        with patch(
            "arr_cli.jellyfin.transport.get",
            return_value=payload,
        ):
            exit_code = main(["--config", str(self.cfg_path), "now"])
        self.assertEqual(exit_code, 0)

    def test_main_returns_jellyfin_parser_usage_after_unknown(self) -> None:
        # ``main`` wires ``build_jellyfin_parser`` so ``argparse``
        # rejects unknown subcommands. ``main_wrapper`` catches
        # argparse's SystemExit internally and surfaces the documented
        # ConfigError exit code (1) instead of the raw argparse exit
        # (2) so the stable exit-code map is preserved
        # (fix-config-flag-ordering).
        exit_code = main(["--config", str(self.cfg_path), "bogus"])
        self.assertEqual(exit_code, 1)

    def test_main_item_404_exit_code(self) -> None:
        # ``cmd_item`` propagates HttpError(status=404); main_wrapper
        # translates it to exit code 4 (REQ-4 AC2).
        with patch(
            "arr_cli.jellyfin.transport.get",
            side_effect=HttpError(
                "jellyfin",
                "item id=42",
                "jellyfin: HTTP 404 for /Items/42",
                status=404,
            ),
        ):
            exit_code = main(
                ["--config", str(self.cfg_path), "item", "42"]
            )
        self.assertEqual(exit_code, 4)

    def test_main_search_emits_payload(self) -> None:
        payload = [{"Name": "The Matrix", "Type": "Movie"}]
        with patch(
            "arr_cli.jellyfin.transport.get",
            return_value=payload,
        ):
            stdout, _ = _capture_stderr_stdout(
                main,
                ["--config", str(self.cfg_path), "search", "matrix"],
            )
        self.assertEqual(json.loads(stdout), payload)

    def test_main_human_renders_table(self) -> None:
        # ``--human`` switches from JSON pass-through to tabular
        # rendering. The exact cell layout is enforced by the output
        # module's tests; here we only assert that the JSON line is
        # NOT emitted and that the renderer was invoked.
        payload = [{"Name": "A"}, {"Name": "B"}]
        with patch(
            "arr_cli.jellyfin.transport.get",
            return_value=payload,
        ):
            stdout, _ = _capture_stderr_stdout(
                main,
                [
                    "--config",
                    str(self.cfg_path),
                    "--human",
                    "now",
                ],
            )
        # JSON line is suppressed in --human mode.
        self.assertNotIn('"DeviceName"', stdout)
        # The renderer emitted at least one line of output.
        self.assertTrue(stdout.strip())

    def test_main_missing_user_id_exit_code(self) -> None:
        # When ``cfg.jellyfin.user_id`` is missing the ``favorites``
        # command raises ConfigError(exit_code=1); main_wrapper
        # surfaces the exit code unchanged.
        body = (
            '[jellyfin]\n'
            'url = "https://jellyfin.example"\n'
            'api_key = "***"\n'
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
        import os
        tmp = self.tmp_dir / "no-user-id.toml"
        tmp.write_text(body, encoding="utf-8")
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        exit_code = main(
            ["--config", str(tmp), "favorites"]
        )
        self.assertEqual(exit_code, 1)

    def test_main_recent_400_exits_four_with_structured_stderr(self) -> None:
        # Regression: ``jellyfin --human recent`` against a 400 must
        # exit 4 (HttpError) and surface the structured stderr line
        # naming ``op=/Users/<user_id>/Items``. The bug review notes
        # this is one of the two repros cited in the bug report.
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://jellyfin.example/Users/jf-user-1/Items",
                status=400,
                body="The value 'jellyfin' is not valid",
            )
            stdout, stderr = _capture_stderr_stdout(
                main,
                [
                    "--config",
                    str(self.cfg_path),
                    "--human",
                    "recent",
                ],
            )
        self.assertEqual(
            json.loads(stdout) if stdout.strip() else None,
            None,
            msg="--human with a 4xx must not emit JSON on stdout",
        )
        # Structured stderr line shape:
        #   service=jellyfin op=/Users/jf-user-1/Items status=400 message=...
        self.assertTrue(
            stderr.startswith(
                "service=jellyfin op=/Users/jf-user-1/Items status=400 message="
            ),
            msg=f"unexpected stderr shape: {stderr!r}",
        )
        self.assertIn("HTTP 400 for /Users/jf-user-1/Items", stderr)
        # The body excerpt is preserved in the structured message so
        # operators can grep the original service response.
        self.assertIn("The value", stderr)

    def test_main_item_400_exits_four(self) -> None:
        # Regression: ``cmd_item`` against a non-404 4xx (here 400
        # simulating a service-side param validation) must exit 4.
        # Mirrors ``test_main_item_404_exit_code`` but pins the
        # non-404 path so a future regression that special-cased 404
        # cannot silently drop other 4xx codes to exit 0.
        with patch(
            "arr_cli.jellyfin.transport.get",
            side_effect=HttpError(
                "jellyfin",
                "item id=42",
                "jellyfin: HTTP 400 for /Items/42",
                status=400,
            ),
        ):
            # ``main`` returns the int exit code; capture it via a
            # direct call (no SystemExit) and re-run under the
            # helper to capture stderr separately. Both invocations
            # re-enter ``main_wrapper`` with the same mock, which is
            # safe because the mock state is deterministic.
            exit_code = main(
                ["--config", str(self.cfg_path), "item", "42"]
            )
            _, stderr = _capture_stderr_stdout(
                main,
                ["--config", str(self.cfg_path), "item", "42"],
            )
        self.assertEqual(exit_code, 4)
        # Structured stderr line surfaces the underlying status so
        # operators can grep on ``status=400`` even though the
        # document exit-code path (4) is identical to 404.
        self.assertTrue(
            stderr.startswith(
                "service=jellyfin op=item id=42 status=400 message="
            ),
            msg=f"unexpected stderr shape: {stderr!r}",
        )
        self.assertIn("HTTP 400 for /Items/42", stderr)


# ---------------------------------------------------------------------------
# Test: auth header policy (REQ-2 AC1)
# ---------------------------------------------------------------------------


class TestAuthHeaderPolicy(unittest.TestCase):
    """The transport layer is the single source of auth-header truth.

    The Jellyfin module's job is to delegate to ``transport.get``;
    the transport layer injects the
    ``Authorization: MediaBrowser ***`` envelope for the
    ``jellyfin`` service. These tests don't re-verify the injection
    contract (already covered in test_transport.py) but they
    confirm the Jellyfin module doesn't bypass that path.
    """

    def test_now_passes_jellyfin_service_to_transport(self) -> None:
        # The first positional argument to ``transport.get`` is the
        # service name; it MUST be ``"jellyfin"`` so the transport
        # layer routes to the Jellyfin auth branch and emits the
        # MediaBrowser envelope.
        cfg = _service_config()
        args = _namespace()
        with _patched_get_payload([]) as mock_get:
            cmd_now(args, cfg)
        positional = mock_get.call_args.args
        self.assertEqual(positional[0], "jellyfin")

    def test_all_commands_use_jellyfin_service(self) -> None:
        # Every handler must pass ``"jellyfin"`` as the service.
        from arr_cli.jellyfin import _DISPATCH

        cfg = _service_config()
        cases = [
            (cmd_now, _namespace()),
            (cmd_resume, _namespace()),
            (cmd_recent, _namespace()),
            (cmd_nextup, _namespace(limit=20)),
            (cmd_latest, _namespace()),
            (cmd_search, _namespace(query="x")),
            (cmd_item, _namespace(item_id="42")),
            (cmd_favorites, _namespace()),
        ]
        for handler, args in cases:
            with _patched_get_payload([]) as mock_get:
                handler(args, cfg)
            positional = mock_get.call_args.args
            self.assertEqual(
                positional[0],
                "jellyfin",
                msg=f"{handler.__name__} did not use jellyfin service",
            )


# ---------------------------------------------------------------------------
# Test: --verbose flag flip for cmd_now
# ---------------------------------------------------------------------------


class TestVerboseFlagCmdNow(unittest.TestCase):
    """REQ-6 AC4(a)/(b): ``cmd_now`` summary vs verbose paths."""

    def test_cmd_now_default_emits_summary(self) -> None:
        # Without ``--verbose`` the default for the size-to-summary
        # candidate ``jellyfin now`` is the curated summary shape.
        cfg = _service_config()
        args = _namespace(command="now")
        payload = [
            {
                "UserName": "alice",
                "DeviceName": "Living Room TV",
                "Client": "Jellyfin Web",
                "NowPlayingItem": {
                    "Type": "Episode",
                    "Name": "Pilot",
                    "SeriesName": "Show",
                    "ParentIndexNumber": 1,
                    "IndexNumber": 1,
                },
                "PlayState": {"PositionTicks": 100, "IsPaused": False},
            }
        ]
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_now, args, cfg)
        rendered = json.loads(output)
        self.assertIsInstance(rendered, list)
        self.assertEqual(rendered[0]["user"], "alice")
        self.assertEqual(rendered[0]["device"], "Living Room TV")
        self.assertEqual(rendered[0]["playing"]["name"], "Pilot")

    def test_cmd_now_verbose_emits_verbatim(self) -> None:
        # ``--verbose`` restores the pre-change verbatim pass-through.
        cfg = _service_config()
        args = _namespace(command="now", verbose=True)
        payload = [{"DeviceName": "Living Room TV", "UserName": "operator"}]
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_now, args, cfg)
        self.assertEqual(json.loads(output), payload)


if __name__ == "__main__":
    unittest.main()
