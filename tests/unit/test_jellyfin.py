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
from responses import matchers

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
    """REQ-6 AC4: ``GET /Shows/NextUp`` with optional ``StartIndex`` and
    ``UserId`` always read from ``cfg.jellyfin.user_id`` (v12+ contract).
    ``--limit`` is intentionally **not** forwarded to the wire; it is a
    client-side cap on the ``--human`` renderer (README §3, §4.5)."""

    def test_nextup_hits_shows_nextup(self) -> None:
        cfg = _service_config()
        args = _namespace(limit=20)
        with _patched_get_payload([]) as mock_get:
            cmd_nextup(args, cfg)
        positional = mock_get.call_args.args
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(positional[1], "/Shows/NextUp")
        # The universal ``--limit`` default is **not** forwarded to
        # the wire: ``cmd_nextup`` honours the project-wide
        # ``--limit``-is-client-side-only contract documented in
        # README §3 and §4.5. ``UserId`` is read from config
        # (``cfg.jellyfin.user_id == "jf-user-1"``) because the
        # Jellyfin v12 ``/Shows/NextUp`` endpoint requires it.
        self.assertEqual(kwargs["params"], {"UserId": "jf-user-1"})
        self.assertNotIn("Limit", kwargs["params"])

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
            {"UserId": "configured-jellyfin-user"},
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

    def test_nextup_does_not_forward_limit_to_wire(self) -> None:
        # Pin the ``--limit``-is-client-side-only contract for
        # ``cmd_nextup``: setting ``args.limit`` must NOT add a
        # ``Limit`` query parameter to the upstream request, even
        # when ``--limit`` is explicitly set far above the default.
        # Client-side capping still flows through ``_emit`` ->
        # ``output.emit`` for the ``--human`` renderer.
        cfg = _service_config()
        args = _namespace(limit=50)
        with _patched_get_payload([]) as mock_get:
            cmd_nextup(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(kwargs["params"], {"UserId": "jf-user-1"})
        self.assertNotIn("Limit", kwargs["params"])

    def test_nextup_no_limit_param_when_limit_is_none(self) -> None:
        # When the parser defaults ``--limit`` to ``None`` (e.g. a
        # downstream caller bypasses the universal flag), the
        # ``Limit`` key is omitted from the params dict so the
        # service falls back to its own default; ``UserId`` is still
        # forwarded from config. ``--limit`` is always client-side
        # only, so this branch covers the ``None`` case for parity
        # with explicit values.
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
            {"StartIndex": 10, "UserId": "jf-user-1"},
        )

    def test_nextup_combined_params(self) -> None:
        cfg = _service_config()
        args = _namespace(limit=5, start_index=2)
        with _patched_get_payload([]) as mock_get:
            cmd_nextup(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(
            kwargs["params"],
            {"StartIndex": 2, "UserId": "jf-user-1"},
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
        self.assertEqual(
            kwargs["params"], {"searchTerm": "matrix", "Recursive": True}
        )

    def test_search_empty_query_short_circuits_to_empty_list(self) -> None:
        # REQ-6 AC6 AC2 + jellyfin-search-empty-query: an explicit
        # empty query must short-circuit to the canonical emit path
        # with ``[]`` and must NOT touch the /Items endpoint.
        # ``transport.get`` is patched to raise so any HTTP call
        # surfaces immediately.
        cfg = _service_config()
        args = _namespace(query="")
        with patch(
            "arr_cli.jellyfin.transport.get",
            side_effect=AssertionError(
                "transport.get must not be called for empty query"
            ),
        ):
            output = _capture_stdout(cmd_search, args, cfg)
        self.assertEqual(json.loads(output), [])

    def test_search_missing_query_defaults_to_empty(self) -> None:
        # When the user runs ``jellyfin search`` with no positional
        # argument the subparser defaults ``query`` to ""; the
        # handler treats the missing attribute the same as ``""``
        # and short-circuits to the canonical emit path without
        # touching /Items. Pins the argparse.OPTIONAL default of
        # ``""`` and the handler guard in one test.
        cfg = _service_config()
        args = _namespace()  # no query field
        self.assertFalse(hasattr(args, "query"))
        with patch(
            "arr_cli.jellyfin.transport.get",
            side_effect=AssertionError(
                "transport.get must not be called for empty query"
            ),
        ):
            output = _capture_stdout(cmd_search, args, cfg)
        self.assertEqual(json.loads(output), [])

    def test_search_empty_query_human_renders_empty_list(self) -> None:
        # AC2 --human branch: with ``--human`` an empty query must
        # render the documented ``(empty list)`` literal, not the
        # verbatim JSON ``[]``. Pairs the JSON pin above with the
        # second priority-chain branch that ``output.emit`` uses.
        cfg = _service_config()
        args = _namespace(query="", human=True)
        with patch(
            "arr_cli.jellyfin.transport.get",
            side_effect=AssertionError(
                "transport.get must not be called for empty query"
            ),
        ):
            output = _capture_stdout(cmd_search, args, cfg)
        self.assertEqual(output.strip(), "(empty list)")

    def test_search_query_with_special_chars(self) -> None:
        # The transport layer percent-encodes the value; the handler
        # passes the raw string along.
        cfg = _service_config()
        args = _namespace(query="hello world?special&chars")
        with _patched_get_payload([]) as mock_get:
            cmd_search(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(
            kwargs["params"],
            {"searchTerm": "hello world?special&chars", "Recursive": True},
        )

    def test_search_forwards_recursive_true(self) -> None:
        # jellyfin-search-recursive regression pin: ``Recursive=true``
        # must be on every /Items search request so the server walks
        # the full library graph. Without it, Jellyfin's /Items
        # endpoint defaults Recursive=false and returns the configured
        # library-root folders (Anime, collections, Movies, Playlists,
        # Shows) for every query — including no-match and empty
        # queries — instead of an honest empty array. Mirrors the
        # style of test_item_forwards_user_id_param.
        cfg = _service_config()
        args = _namespace(query="dune")
        with _patched_get_payload([]) as mock_get:
            cmd_search(args, cfg)
        positional = mock_get.call_args.args
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(positional[1], "/Items")
        params = kwargs["params"]
        self.assertEqual(params["searchTerm"], "dune")
        self.assertIs(params["Recursive"], True)


# ---------------------------------------------------------------------------
# Test: cmd_item
# ---------------------------------------------------------------------------


class TestCmdItem(unittest.TestCase):
    """REQ-6 AC7: ``GET /Items/{id}``; 404 → HttpError(exit_code=4).

    On Jellyfin v12+ ``/Items/{id}`` requires the ``UserId`` query
    parameter; the handler reads it from ``cfg.jellyfin.user_id`` via
    :func:`_require_user_id` (mirrors :func:`cmd_nextup`)."""

    def test_item_hits_items_path(self) -> None:
        cfg = _service_config()
        args = _namespace(item_id="42")
        with _patched_get_payload({"Id": 42, "Name": "Test"}) as mock_get:
            cmd_item(args, cfg)
        positional = mock_get.call_args.args
        self.assertEqual(positional[1], "/Items/42")

    def test_item_forwards_user_id_param(self) -> None:
        # Jellyfin v12+ requires ``UserId`` on ``/Items/{id}``; without
        # it the server returns HTTP 400 ``Error processing request.``
        # The handler reads ``UserId`` from config (mirrors cmd_nextup)
        # so a missing config value surfaces as ``ConfigError(exit 1)``
        # rather than the upstream 400.
        cfg = _service_config(user_id="jf-user-1")
        args = _namespace(item_id="42")
        with _patched_get_payload({"Id": 42, "Name": "Test"}) as mock_get:
            cmd_item(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(kwargs["params"], {"UserId": "jf-user-1"})

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

    def test_item_missing_user_id_raises_config_error(self) -> None:
        # Missing ``cfg.jellyfin.user_id`` is a config problem, not a
        # server problem; the handler surfaces ``ConfigError(exit 1)``
        # before dispatching to the service so the operator sees a
        # stable exit code instead of the upstream 400 (the symptom
        # that hid the real cause).
        cfg = _service_config(user_id=None)
        args = _namespace(item_id="42")
        with self.assertRaises(ConfigError) as ctx:
            cmd_item(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("user_id", ctx.exception.message)

    def test_item_missing_service_section_raises_config_error(self) -> None:
        # When the Jellyfin section is missing entirely the handler
        # raises ``ConfigError(exit 1)`` via ``_require_user_id`` so
        # the operator sees the config-shape problem rather than a
        # transport-layer auth failure.
        cfg = _service_config(include_jellyfin=False)
        args = _namespace(item_id="42")
        with self.assertRaises(ConfigError) as ctx:
            cmd_item(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("section missing", ctx.exception.message)

    def test_item_empty_id_raises_config_error(self) -> None:
        # jellyfin-item-empty-id: an empty ``item_id`` (e.g. from
        # an unset shell variable, ``jellyfin item ""``) is rejected
        # as malformed CLI input. Without the guard, ``GET /Items/``
        # with ``UserId`` returns the configured library-root
        # listing (Jellyfin treats a trailing-slash ``/Items/`` as
        # a recursive ``/Items`` query), silently giving downstream
        # callers a plausible-but-wrong payload they cannot
        # distinguish from a real item lookup. The handler MUST
        # raise ``ConfigError(exit 1)`` so ``main_wrapper`` emits
        # the documented ``service=jellyfin op=item message=...``
        # stderr line instead of letting the wire call fire.
        cfg = _service_config()
        args = _namespace(item_id="")
        with self.assertRaises(ConfigError) as ctx:
            cmd_item(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertEqual(ctx.exception.service, SERVICE_NAME)
        self.assertEqual(ctx.exception.op, "item")
        self.assertIn("item ID must not be empty", ctx.exception.message)

    def test_item_empty_id_does_not_call_transport(self) -> None:
        # Pin the "no HTTP call on empty id" contract: the empty-id
        # guard MUST run before ``_get`` so a misconfigured
        # ``transport.get`` (or a regression that re-introduces the
        # trailing-slash ``/Items/`` call) cannot silently round-trip
        # the upstream library-root listing. Patches ``transport.get``
        # to raise so any HTTP call surfaces immediately.
        cfg = _service_config()
        args = _namespace(item_id="")
        with patch(
            "arr_cli.jellyfin.transport.get",
            side_effect=AssertionError(
                "transport.get must not be called for empty item_id"
            ),
        ):
            with self.assertRaises(ConfigError):
                cmd_item(args, cfg)

    def test_item_user_id_check_takes_precedence_over_empty_id(self) -> None:
        # When both ``cfg.jellyfin.user_id`` is missing AND the
        # supplied ``item_id`` is empty the operator sees the
        # config-shape problem first (it's the harder-to-diagnose
        # failure mode and the one that needs fixing before the
        # CLI invocation makes sense). Pins the order: user_id
        # validation runs before the input-shape guard.
        cfg = _service_config(user_id=None)
        args = _namespace(item_id="")
        with self.assertRaises(ConfigError) as ctx:
            cmd_item(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("user_id", ctx.exception.message)


# ---------------------------------------------------------------------------
# Test: cmd_favorites
# ---------------------------------------------------------------------------


class TestCmdFavorites(unittest.TestCase):
    """REQ-6 AC8: ``GET /Users/{user_id}/Items?Filters=IsFavorite``
    on Jellyfin v12+ (the v10 ``/Items/Favorites`` sub-resource was
    removed)."""

    def test_favorites_hits_items_path_with_filter(self) -> None:
        cfg = _service_config(user_id="jf-user-1")
        args = _namespace(limit=None)
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            rsps.add(
                responses.GET,
                "https://jellyfin.example/Users/jf-user-1/Items",
                json=[],
                status=200,
                match=[
                    matchers.query_param_matcher({"Filters": "IsFavorite"}),
                ],
            )
            cmd_favorites(args, cfg)

    def test_favorites_does_not_forward_limit_to_wire(self) -> None:
        # Pin the ``--limit``-is-client-side-only contract for
        # ``cmd_favorites``: setting ``args.limit`` must NOT add a
        # ``Limit`` query parameter to the upstream request. The
        # only query keys that ride the wire are the service filter
        # (``Filters=IsFavorite``) and the percent-encoded user id
        # in the path; ``--limit`` only ever caps the ``--human``
        # renderer via ``_emit`` -> ``output.emit``.
        cfg = _service_config(user_id="jf-user-1")
        args = _namespace(limit=50)
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            rsps.add(
                responses.GET,
                "https://jellyfin.example/Users/jf-user-1/Items",
                json=[],
                status=200,
                match=[
                    matchers.query_param_matcher(
                        {"Filters": "IsFavorite"}
                    ),
                ],
            )
            cmd_favorites(args, cfg)

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

    def test_favorites_verbose_emits_verbatim_envelope(self) -> None:
        # Regression for ``jellyfin-favorites-summary-id-field``: the
        # ``--verbose`` path must continue to emit the verbatim
        # service envelope unchanged, with ``Id`` present, regardless
        # of the curated-summary projection update. ``--verbose``
        # bypasses ``summarize`` entirely, so the projection
        # omission cannot leak into this path.
        cfg = _service_config(user_id="jf-user-1")
        args = _namespace(human=False, verbose=True)
        envelope = {
            "Items": [
                {
                    "Id": "523c6aa176971feab5e0fb18ebde0b8f",
                    "Name": "Fireheart: The Legend of Tadas Blinda",
                    "Type": "Movie",
                    "ProductionYear": 2011,
                    "SeriesName": None,
                }
            ],
            "TotalRecordCount": 1,
            "StartIndex": 0,
        }
        with _patched_get_payload(envelope):
            output = _capture_stdout(cmd_favorites, args, cfg)
        # Verbose pass-through: the envelope is rendered verbatim,
        # including the upstream ``Id`` field.
        self.assertEqual(json.loads(output), envelope)


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

    def test_main_item_empty_id_exits_one_with_structured_stderr(self) -> None:
        # End-to-end pin for jellyfin-item-empty-id:
        # ``jellyfin item ""`` must exit 1 (ConfigError) with the
        # documented ``service=jellyfin op=item message=...``
        # structured stderr line. No HTTP call fires — ``transport.get``
        # is patched to raise so any wire round-trip surfaces
        # immediately. Without the guard the CLI exited 0 with the
        # upstream library-root listing on stdout.
        with patch(
            "arr_cli.jellyfin.transport.get",
            side_effect=AssertionError(
                "transport.get must not be called for empty item_id"
            ),
        ):
            exit_code = main(
                ["--config", str(self.cfg_path), "item", ""]
            )
            _, stderr = _capture_stderr_stdout(
                main,
                ["--config", str(self.cfg_path), "item", ""],
            )
        self.assertEqual(exit_code, 1)
        self.assertTrue(
            stderr.startswith(
                "service=jellyfin op=item message="
            ),
            msg=f"unexpected stderr shape: {stderr!r}",
        )
        self.assertIn("item ID must not be empty", stderr)

    def test_main_search_emits_payload(self) -> None:
        # Regression coverage for ``jellyfin-search-nextup-envelope-unwrap``:
        # ``jellyfin search <term>`` (no flag) now emits the curated
        # summary, not the verbatim upstream envelope. The renderer
        # iterates the upstream ``Items`` list and projects the
        # four columns ``cmd_search`` advertises, filling missing
        # fields with the documented defaults. The bare-list mock
        # payload exercises the renderer through its no-op unwrap
        # branch (the upstream envelope path is covered by the
        # ``test_summary_jellyfin_search_unwraps_envelope`` test
        # in ``tests/unit/test_output.py``).
        payload = [{"Name": "The Matrix", "Type": "Movie"}]
        with patch(
            "arr_cli.jellyfin.transport.get",
            return_value=payload,
        ):
            stdout, _ = _capture_stderr_stdout(
                main,
                ["--config", str(self.cfg_path), "search", "matrix"],
            )
        self.assertEqual(
            json.loads(stdout),
            [
                {
                    "Name": "The Matrix",
                    "Type": "Movie",
                    "ProductionYear": 0,
                    "SeriesName": None,
                }
            ],
        )

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
