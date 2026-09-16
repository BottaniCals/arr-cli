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
from typing import Any
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

    def test_seerr_has_eight_commands(self) -> None:
        """The subparser exposes exactly the eight documented Seerr commands."""
        from arr_cli.seerr import build_seerr_parser

        parser = build_seerr_parser()
        # Drill in: the subparsers action holds the registered choices.
        subparsers_action = next(
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(
            set(subparsers_action.choices.keys()),
            {
                "requests",
                "request-count",
                "search",
                "available",
                "user",
                "tv",
                "movie",
                "trending",
            },
        )
        self.assertEqual(len(subparsers_action.choices), 8)

    def test_dispatch_table_keys(self) -> None:
        """``_dispatch`` maps every command name to a callable handler."""
        import arr_cli.seerr as seerr

        expected_commands = {
            "requests",
            "request-count",
            "search",
            "available",
            "user",
            "tv",
            "movie",
            "trending",
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

    The seerr ``user`` command is a single ``GET /api/v1/auth/me`` probe.
    :func:`arr_cli.facade.transport.get` raises
    :class:`arr_cli.facade.errors.HttpError` (exit ``4``) on any
    non-2xx response, which :func:`arr_cli.facade.cli_common.main_wrapper`
    surfaces as a structured ``service=seerr op=/api/v1/auth/me status=<code>
    message=...`` stderr line. A 2xx response returns the user JSON
    on stdout and exit ``0``.

    These tests exercise the documented end-to-end contract:

    * HTTP 200 on ``/api/v1/auth/me`` returns the user object on stdout and
      exit ``0``.
    * Any non-2xx response on ``/api/v1/auth/me`` surfaces as exit ``4`` with
      a structured stderr line naming the path.

    Tests are hermetic via :mod:`responses` (AGENTS.md §7.5).
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

    def test_cmd_user_success_returns_user_json_with_exit_zero(self) -> None:
        # Regression: a 2xx response on ``/api/v1/auth/me`` returns the user
        # object on stdout and the process exits ``0``. Pins the
        # bug-report's "Expected Behavior" -- ``echo $?`` should be
        # ``0`` after the CLI prints the authenticated user JSON.
        import arr_cli.seerr as seerr

        user_payload = {
            "id": 1,
            "email": "alice@example.com",
            "username": "alice",
            "plexId": None,
            "jellyfinAuthToken": None,
        }
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/auth/me",
                json=user_payload,
                status=200,
            )
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf), \
                    contextlib.redirect_stderr(stderr_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "user"]
                )
            stdout = stdout_buf.getvalue()
            stderr = stderr_buf.getvalue()
            self.assertEqual(len(rsps.calls), 1)
        self.assertEqual(exit_code, 0)
        # Stdout is the JSON user object pipe-clean so downstream
        # consumers can parse it directly. The renderer keeps the
        # default summary path for object payloads (no tabular
        # columns are registered for ``user``).
        self.assertEqual(json.loads(stdout), user_payload)
        # Nothing on stderr in the success path.
        self.assertEqual(stderr, "")

    def test_cmd_user_non_2xx_exits_four_with_structured_stderr(self) -> None:
        # Regression: any non-2xx response on ``/api/v1/auth/me`` surfaces as
        # exit ``4`` with a structured stderr line that names the path
        # (mirrors the bug-report's actual ``seerr user`` output
        # shape, with the contract: ``op=/api/v1/auth/me``).
        import arr_cli.seerr as seerr

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/auth/me",
                status=503,
                body="Service Unavailable",
            )
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf), \
                    contextlib.redirect_stderr(stderr_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "user"]
                )
            stdout = stdout_buf.getvalue()
            stderr = stderr_buf.getvalue()
            self.assertEqual(len(rsps.calls), 1)
        self.assertEqual(exit_code, 4)
        # No payload on stdout -- the structured error line goes to
        # stderr so the pipe-clean stdout contract (AGENTS.md §1) is
        # preserved.
        self.assertEqual(stdout, "")
        # Structured line: service=seerr op=/api/v1/auth/me status=503 message=...
        self.assertTrue(
            stderr.startswith(
                "service=seerr op=/api/v1/auth/me status=503 message="
            ),
            msg=f"unexpected stderr shape: {stderr!r}",
        )
        self.assertIn("HTTP 503 for /api/v1/auth/me", stderr)


# ---------------------------------------------------------------------------
# Test: paginated envelope unwrap + take cap (bug fix)
# ---------------------------------------------------------------------------


class TestCmdRequestsPaginatedEnvelope(unittest.TestCase):
    """Regression tests pinning the paginated ``/api/v1/request`` contract.

    Seer returns ``/api/v1/request`` wrapped in a paginated envelope of
    the shape ``{pageInfo: {...}, results: [...], serviceErrors: {...}}``.
    The renderer's job is to unwrap ``results`` before summarising so
    the default output is the request list, not an empty ``[]``.
    ``cmd_requests`` also asks for ``take=1000`` so a single response
    covers the household workload rather than the default first page
    of ten.
    """

    PAGINATED_ENVELOPE: dict[str, Any] = {
        "pageInfo": {
            "pages": 11,
            "pageSize": 10,
            "results": 109,
            "page": 1,
        },
        "results": [
            {
                "id": 121,
                "title": "Foo",
                "type": "movie",
                "status": 5,
                "createdAt": "2026-09-13T12:56:58.000Z",
                "requestedBy": {"displayName": "alice"},
            },
            {
                "id": 120,
                "title": "Bar",
                "type": "movie",
                "status": 2,
                "createdAt": "2026-09-13T12:55:03.000Z",
                "requestedBy": {"displayName": "bob"},
            },
        ],
        "serviceErrors": {"radarr": [], "sonarr": []},
    }

    def _make_args(self) -> argparse.Namespace:
        """Build an ``argparse.Namespace`` mirroring the ``requests`` subparser defaults."""
        return argparse.Namespace(
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

    def test_cmd_requests_envelope_default_unwraps_results(self) -> None:
        """Default ``cmd_requests`` emits summary rows when the payload is a paginated envelope."""
        from arr_cli.seerr import cmd_requests

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.PAGINATED_ENVELOPE,
        ):
            output = _capture_stdout(cmd_requests, args, None)
        rendered = json.loads(output)
        self.assertEqual(len(rendered), 2)
        self.assertEqual(rendered[0]["title"], "Foo")
        self.assertEqual(rendered[1]["title"], "Bar")
        # ``requestedBy`` is preserved as the nested mapping the docstring
        # promises, resolved against the unwrapped envelope.
        self.assertEqual(rendered[0]["requestedBy"]["displayName"], "alice")
        self.assertEqual(rendered[1]["requestedBy"]["displayName"], "bob")

    def test_cmd_requests_flat_list_default_unchanged(self) -> None:
        """The flat-list code path keeps the pre-change behaviour intact."""
        from arr_cli.seerr import cmd_requests

        args = self._make_args()
        flat_payload = [
            {
                "title": "Foo",
                "type": "movie",
                "status": "pending",
                "createdAt": "2024-01-01",
                "requestedBy": {"displayName": "alice"},
            }
        ]
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=flat_payload,
        ):
            output = _capture_stdout(cmd_requests, args, None)
        rendered = json.loads(output)
        self.assertEqual(rendered[0]["title"], "Foo")
        self.assertEqual(
            rendered[0]["requestedBy"]["displayName"], "alice"
        )

    def test_cmd_requests_envelope_verbose_emits_verbatim_envelope(self) -> None:
        """``--verbose`` bypasses the renderer and emits the envelope verbatim."""
        from arr_cli.seerr import cmd_requests

        args = self._make_args()
        args.verbose = True
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.PAGINATED_ENVELOPE,
        ):
            output = _capture_stdout(cmd_requests, args, None)
        # ``--verbose`` keeps the envelope shape intact; downstream consumers
        # still see ``pageInfo`` and ``results`` as the service emitted them.
        self.assertEqual(
            json.loads(output), self.PAGINATED_ENVELOPE
        )

    def test_cmd_requests_envelope_dict_with_no_results_returns_empty(self) -> None:
        """A paginated envelope without ``results`` maps to ``[]`` rather than crashing."""
        from arr_cli.seerr import cmd_requests

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get",
            return_value={
                "pageInfo": {"pages": 0, "pageSize": 10, "results": 0, "page": 1},
                "serviceErrors": {"radarr": [], "sonarr": []},
            },
        ):
            output = _capture_stdout(cmd_requests, args, None)
        self.assertEqual(json.loads(output), [])


# ---------------------------------------------------------------------------
# Test: ``cmd_requests`` issues ``take=1000`` so a single response covers the queue
# ---------------------------------------------------------------------------


class TestCmdRequestsTakesParam(unittest.TestCase):
    """Pin the ``take`` query parameter contract for ``/api/v1/request``.

    Seer defaults to a pageSize of ten; without ``take=1000`` a 109-item
    household queue would have 99 items silently dropped across the
    next ten pages. This test exercises the real HTTP layer via
    ``responses`` to confirm the parameter rides on the wire.
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

    def test_cmd_requests_passes_take_1000_to_seerr(self) -> None:
        """``cmd_requests`` requests ``take=1000`` so a single response covers the queue."""
        envelope = {
            "pageInfo": {"pages": 1, "pageSize": 10, "results": 1, "page": 1},
            "results": [
                {
                    "id": 1,
                    "title": "Foo",
                    "type": "movie",
                    "status": 5,
                    "createdAt": "2024-01-01T00:00:00.000Z",
                    "requestedBy": {"displayName": "alice"},
                }
            ],
            "serviceErrors": {"radarr": [], "sonarr": []},
        }
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/request",
                json=envelope,
                match=[responses.matchers.query_param_matcher({"take": "1000"})],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                ["--config", str(self.cfg_path), "requests"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)
            # The single registered mock fired, so ``take=1000`` was on
            # the wire and matched. Any other ``take`` value would have
            # left the mock unmatched and surfaced a connection error.


# ---------------------------------------------------------------------------
# Test: ``cmd_search`` targets Seer's consolidated ``/api/v1/search`` (bug fix)
# ---------------------------------------------------------------------------


class TestCmdSearch(unittest.TestCase):
    """Regression tests pinning the path + params for ``cmd_search``.

    Bug fix ``seerr-search-wrong-api-path``: the handler was hitting
    the legacy Overseerr ``/api/v1/search/multi`` path that Seer does
    not expose, so every invocation returned ``HTTP 404``. Seer
    consolidates search into ``/api/v1/search``. These tests pin the
    corrected path AND add a defensive guard against future copy-paste
    regressions back to ``/api/v1/search/multi`` (which would fail the
    suite immediately, in milliseconds, rather than at the operator's
    instance as ``HTTP 404``).
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

    def test_cmd_search_hits_seerr_search_with_query(self) -> None:
        """``cmd_search`` hits ``/api/v1/search`` and forwards ``query=<value>``."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/search",
                json=[
                    {
                        "title": "Doctor Who",
                        "mediaType": "tv",
                        "releaseDate": "2005-03-26",
                        "mediaInfo": {"tmdbId": 123},
                    }
                ],
                match=[
                    responses.matchers.query_param_matcher(
                        {"query": "doctor"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "search", "doctor",
                ]
            )
            self.assertEqual(exit_code, 0)
            # The single registered mock fired, so the path was
            # ``/api/v1/search`` AND ``query=doctor`` was on the wire
            # and matched. Any other path or query value would have
            # left the mock unmatched and surfaced a connection error.
            self.assertEqual(len(rsps.calls), 1)

    def test_cmd_search_empty_query_still_hits_endpoint(self) -> None:
        """An absent positional query still hits ``/api/v1/search`` with ``query=\"\"``."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/search",
                json=[],
                match=[
                    responses.matchers.query_param_matcher({"query": ""})
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                ["--config", str(self.cfg_path), "search"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_cmd_search_special_chars_forwarded_raw(self) -> None:
        """Special characters in the query are forwarded raw.

        Encoding is the transport layer's job (verified separately in
        the facade tests); ``cmd_search`` must pass the raw string
        through unchanged. Mirrors the existing
        ``test_jellyfin.test_search_query_with_special_chars`` and
        ``test_radarr.test_lookup_percent_encodes_term`` contracts
        that pin the handler-vs-transport responsibility split.
        """
        raw_query = "hello world?special&chars"
        with patch(
            "arr_cli.seerr.transport.get", return_value=[]
        ) as mock_get:
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "search", raw_query,
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(mock_get.call_args_list), 1)
        # The raw string flowed through to ``transport.get`` unchanged.
        # Percent-encoding happens inside the transport layer (covered
        # in ``test_transport.test_params_are_percent_encoded``); the
        # handler MUST NOT pre-encode.
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(kwargs["params"], {"query": raw_query})

    def test_cmd_search_does_not_hit_legacy_multi_path(self) -> None:
        """Defensive guard: ``cmd_search`` MUST NOT target ``/api/v1/search/multi``.

        Pins the regression guard described in the bug review. A
        future copy-paste back to the legacy Overseerr path is caught
        at the unit layer in milliseconds rather than at the
        operator's instance as ``HTTP 404``.
        """
        with patch(
            "arr_cli.seerr.transport.get", return_value=[]
        ) as mock_get:
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "search", "doctor",
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertGreater(
            len(mock_get.call_args_list), 0,
            msg="cmd_search did not call transport.get at all",
        )
        for call in mock_get.call_args_list:
            # ``transport.get`` is invoked as
            # ``transport.get(SERVICE_NAME, path, ...)``; ``path`` is
            # the second positional argument. Inspect both positional
            # and keyword forms for forward-compat with future
            # signature changes.
            args, kwargs = call
            path: str | None = None
            if len(args) >= 2:
                path = args[1]
            else:
                path = kwargs.get("path")
            self.assertIsNotNone(
                path,
                msg="transport.get called without a path argument",
            )
            self.assertNotEqual(
                path,
                "/api/v1/search/multi",
                msg=(
                    "cmd_search must not target the legacy Overseerr "
                    "/api/v1/search/multi path (Seer consolidated "
                    "search into /api/v1/search)."
                ),
            )
            self.assertEqual(
                path,
                "/api/v1/search",
                msg=f"cmd_search targeted unexpected path: {path!r}",
            )


# ---------------------------------------------------------------------------
# Test: paginated envelope unwrap for ``cmd_search`` (bug fix)
# ---------------------------------------------------------------------------


class TestCmdSearchPaginatedEnvelope(unittest.TestCase):
    """Regression tests pinning the paginated ``/api/v1/search`` contract.

    Seer returns ``/api/v1/search`` wrapped in a paginated envelope of
    the shape ``{page, totalPages, totalResults, results: [...]}``. The
    renderer's job is to unwrap ``results`` before summarising so the
    default output is the result list, not an empty ``[]``.
    """

    PAGINATED_ENVELOPE: dict[str, Any] = {
        "page": 1,
        "totalPages": 92,
        "totalResults": 1839,
        "results": [
            {
                "title": "Doctor Who",
                "mediaType": "tv",
                "releaseDate": "2005-03-26",
                "mediaInfo": {"tmdbId": 123},
            },
            {
                "title": "Doctor Strange",
                "mediaType": "movie",
                "releaseDate": "2016-10-25",
                "mediaInfo": {"tmdbId": 291351},
            },
        ],
    }

    def _make_args(self) -> argparse.Namespace:
        """Build an ``argparse.Namespace`` mirroring the ``search`` subparser defaults."""
        return argparse.Namespace(
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
            command="search",
            query="doctor",
        )

    def test_cmd_search_envelope_default_unwraps_results(self) -> None:
        """Default ``cmd_search`` emits summary rows when the payload is a paginated envelope."""
        from arr_cli.seerr import cmd_search

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.PAGINATED_ENVELOPE,
        ):
            output = _capture_stdout(cmd_search, args, None)
        rendered = json.loads(output)
        self.assertEqual(len(rendered), 2)
        self.assertEqual(rendered[0]["title"], "Doctor Who")
        self.assertEqual(rendered[1]["title"], "Doctor Strange")
        # ``mediaInfo.tmdbId`` is preserved as the nested mapping the
        # docstring promises, resolved against the unwrapped envelope.
        self.assertEqual(rendered[0]["mediaInfo"]["tmdbId"], 123)
        self.assertEqual(rendered[1]["mediaInfo"]["tmdbId"], 291351)

    def test_cmd_search_flat_list_default_unchanged(self) -> None:
        """The flat-list code path keeps the pre-change behaviour intact."""
        from arr_cli.seerr import cmd_search

        args = self._make_args()
        flat_payload = [
            {
                "title": "Doctor Who",
                "mediaType": "tv",
                "releaseDate": "2005-03-26",
                "mediaInfo": {"tmdbId": 123},
            }
        ]
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=flat_payload,
        ):
            output = _capture_stdout(cmd_search, args, None)
        rendered = json.loads(output)
        self.assertEqual(rendered[0]["title"], "Doctor Who")
        self.assertEqual(rendered[0]["mediaInfo"]["tmdbId"], 123)

    def test_cmd_search_envelope_verbose_emits_verbatim_envelope(self) -> None:
        """``--verbose`` bypasses the renderer and emits the envelope verbatim."""
        from arr_cli.seerr import cmd_search

        args = self._make_args()
        args.verbose = True
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.PAGINATED_ENVELOPE,
        ):
            output = _capture_stdout(cmd_search, args, None)
        # ``--verbose`` keeps the envelope shape intact; downstream consumers
        # still see ``page``/``totalPages``/``totalResults``/``results`` as
        # the service emitted them.
        self.assertEqual(
            json.loads(output), self.PAGINATED_ENVELOPE
        )

    def test_cmd_search_envelope_dict_with_no_results_returns_empty(self) -> None:
        """A paginated envelope without ``results`` maps to ``[]`` rather than crashing."""
        from arr_cli.seerr import cmd_search

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get",
            return_value={
                "page": 1,
                "totalPages": 0,
                "totalResults": 0,
            },
        ):
            output = _capture_stdout(cmd_search, args, None)
        self.assertEqual(json.loads(output), [])


# ---------------------------------------------------------------------------
# Test: ``cmd_available`` targets Seer's ``/api/v1/media`` (bug fix)
# ---------------------------------------------------------------------------


class TestCmdAvailable(unittest.TestCase):
    """Regression tests pinning the path + params for ``cmd_available``.

    Bug fix ``seerr-available-endpoint-missing``: the handler was
    hitting the legacy Overseerr ``/api/v1/media/available?query=...``
    sub-resource that Seer does not expose, so every invocation
    returned ``HTTP 405``. Seer's general list endpoint
    ``/api/v1/media`` accepts a ``filter`` parameter for the
    "in library" subset and a ``take`` cap to bound the response.
    These tests pin the corrected path + params, the
    paginated-envelope unwrap, the client-side title-substring
    filter, the ``--verbose`` verbatim passthrough, and add a
    defensive guard against future copy-paste regressions back to
    ``/api/v1/media/available``.
    """

    AVAILABLE_ENVELOPE: dict[str, Any] = {
        "pageInfo": {
            "pages": 1,
            "pageSize": 10,
            "results": 3,
            "page": 1,
        },
        "results": [
            {
                "title": "Doctor Who",
                "mediaType": "tv",
                "releaseDate": "2005-03-26",
                "mediaInfo": {"status": 5},
            },
            {
                "title": "Doctor Strange",
                "mediaType": "movie",
                "releaseDate": "2016-10-25",
                "mediaInfo": {"status": 5},
            },
            {
                "title": "Unrelated",
                "mediaType": "movie",
                "releaseDate": "2020-01-01",
                "mediaInfo": {"status": 5},
            },
        ],
        "serviceErrors": {"radarr": [], "sonarr": []},
    }

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="seerr-test-"))
        self.cfg_path = _write_toml_config(self.tmp_dir)

    def tearDown(self) -> None:
        import shutil
        try:
            shutil.rmtree(self.tmp_dir)
        except OSError:
            pass

    def _make_args(self, query: str = "") -> argparse.Namespace:
        """Build an ``argparse.Namespace`` mirroring the ``available`` subparser defaults."""
        return argparse.Namespace(
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
            command="available",
            query=query,
        )

    def test_cmd_available_hits_seerr_media_with_take_and_filter(self) -> None:
        """``cmd_available`` hits ``/api/v1/media`` and forwards ``take=1000&filter=available``."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/media",
                json=self.AVAILABLE_ENVELOPE,
                match=[
                    responses.matchers.query_param_matcher(
                        {
                            "take": "1000",
                            "filter": "available",
                        }
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "available", "doctor",
                ]
            )
            self.assertEqual(exit_code, 0)
            # The single registered mock fired, so the path was
            # ``/api/v1/media`` AND both ``take=1000`` and
            # ``filter=available`` were on the wire and matched. Any
            # other path or query combination would have left the mock
            # unmatched and surfaced a connection error.
            self.assertEqual(len(rsps.calls), 1)

    def test_cmd_available_empty_query_still_hits_endpoint(self) -> None:
        """An absent positional query still hits the endpoint with the same params."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/media",
                json={
                    "pageInfo": {
                        "pages": 0,
                        "pageSize": 10,
                        "results": 0,
                        "page": 1,
                    },
                    "results": [],
                    "serviceErrors": {"radarr": [], "sonarr": []},
                },
                match=[
                    responses.matchers.query_param_matcher(
                        {
                            "take": "1000",
                            "filter": "available",
                        }
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                ["--config", str(self.cfg_path), "available"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_cmd_available_title_substring_filter_applied_client_side(
        self,
    ) -> None:
        """Non-empty ``query`` is matched client-side as a title-substring."""
        from arr_cli.seerr import cmd_available

        args = self._make_args(query="doctor")
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.AVAILABLE_ENVELOPE,
        ):
            output = _capture_stdout(cmd_available, args, None)
        rendered = json.loads(output)
        # Only the two ``Doctor*`` titles match; ``Unrelated`` is dropped.
        self.assertEqual(len(rendered), 2)
        rendered_titles = [row["title"] for row in rendered]
        self.assertEqual(
            rendered_titles, ["Doctor Who", "Doctor Strange"]
        )

    def test_cmd_available_envelope_unwrap(self) -> None:
        """The renderer iterates ``results`` of a paginated envelope, not the envelope itself."""
        from arr_cli.seerr import cmd_available

        args = self._make_args(query="")
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.AVAILABLE_ENVELOPE,
        ):
            output = _capture_stdout(cmd_available, args, None)
        rendered = json.loads(output)
        # The unwrap pulls the 3 items out of ``results`` rather than
        # rendering the envelope as a single summary row.
        self.assertEqual(len(rendered), 3)
        self.assertEqual(rendered[0]["title"], "Doctor Who")
        self.assertEqual(rendered[1]["title"], "Doctor Strange")
        self.assertEqual(rendered[2]["title"], "Unrelated")
        # Nested ``mediaInfo.status`` is preserved.
        self.assertEqual(rendered[0]["mediaInfo"]["status"], 5)

    def test_cmd_available_envelope_dict_with_no_results_returns_empty(
        self,
    ) -> None:
        """A paginated envelope without ``results`` maps to ``[]`` rather than crashing."""
        from arr_cli.seerr import cmd_available

        args = self._make_args(query="")
        with patch(
            "arr_cli.seerr.transport.get",
            return_value={
                "pageInfo": {
                    "pages": 0,
                    "pageSize": 10,
                    "results": 0,
                    "page": 1,
                },
                "serviceErrors": {"radarr": [], "sonarr": []},
            },
        ):
            output = _capture_stdout(cmd_available, args, None)
        self.assertEqual(json.loads(output), [])

    def test_cmd_available_verbose_emits_verbatim_envelope(self) -> None:
        """``--verbose`` bypasses the renderer and emits the envelope verbatim."""
        from arr_cli.seerr import cmd_available

        args = self._make_args(query="")
        args.verbose = True
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.AVAILABLE_ENVELOPE,
        ):
            output = _capture_stdout(cmd_available, args, None)
        # ``--verbose`` keeps the envelope shape intact; downstream
        # consumers still see ``pageInfo`` and ``results`` as the
        # service emitted them.
        self.assertEqual(
            json.loads(output), self.AVAILABLE_ENVELOPE
        )

    def test_cmd_available_does_not_hit_legacy_available_path(self) -> None:
        """Defensive guard: ``cmd_available`` MUST NOT target ``/api/v1/media/available``.

        Pins the regression guard described in the bug review. A
        future copy-paste back to the legacy Overseerr sub-resource
        is caught at the unit layer in milliseconds rather than at
        the operator's instance as ``HTTP 405``.
        """
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.AVAILABLE_ENVELOPE,
        ) as mock_get:
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "available", "doctor",
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertGreater(
            len(mock_get.call_args_list), 0,
            msg="cmd_available did not call transport.get at all",
        )
        for call in mock_get.call_args_list:
            # ``transport.get`` is invoked as
            # ``transport.get(SERVICE_NAME, path, ...)``; ``path``
            # is the second positional argument. Inspect both
            # positional and keyword forms for forward-compat.
            args_, kwargs = call
            path: str | None = None
            if len(args_) >= 2:
                path = args_[1]
            else:
                path = kwargs.get("path")
            self.assertIsNotNone(
                path,
                msg="transport.get called without a path argument",
            )
            self.assertNotEqual(
                path,
                "/api/v1/media/available",
                msg=(
                    "cmd_available must not target the legacy "
                    "Overseerr /api/v1/media/available sub-resource "
                    "(Seer exposes /api/v1/media only)."
                ),
            )
            self.assertEqual(
                path,
                "/api/v1/media",
                msg=(
                    "cmd_available targeted unexpected path: "
                    f"{path!r}"
                ),
            )


# ---------------------------------------------------------------------------
# Test: ``cmd_tv`` -- per-show TV details + optional Rotten Tomatoes ratings
# ---------------------------------------------------------------------------


class TestCmdTv(unittest.TestCase):
    """Regression tests pinning the endpoint, params, and summary shape for ``cmd_tv``.

    Seer's per-show detail endpoint is ``GET /api/v1/tv/{tvId}?language=...``.
    ``--ratings`` adds ``GET /api/v1/tv/{tvId}/ratings?language=...``
    and merges the RT critic + audience scores into the default
    summary under ``payload["ratings"]``. The Doctor Who payload
    (id=57243) is 228KB on a live Seer instance and includes
    ``seasons[]`` and ``numberOfSeasons``; the renderer flattens the
    ``genres`` / ``networks`` arrays to comma-joined strings so the
    default ``--human`` rendering stays readable instead of dumping
    228KB of JSON.
    """

    DETAIL_PAYLOAD: dict[str, Any] = {
        "id": 57243,
        "name": "Doctor Who",
        "originalName": "Doctor Who",
        "firstAirDate": "2005-03-26",
        "genres": [{"id": 10759, "name": "Action & Adventure"}],
        "networks": [{"id": 97, "name": "BBC One"}],
        "numberOfSeasons": 13,
        "status": "Ended",
    }

    RATINGS_PAYLOAD: dict[str, Any] = {
        "criticsScore": 90,
        "audienceScore": 86,
    }

    def _make_args(self, **overrides: Any) -> argparse.Namespace:
        """Build an ``argparse.Namespace`` mirroring the ``tv`` subparser defaults."""
        base: dict[str, Any] = {
            "config": None,
            "debug": False,
            "quiet": False,
            "human": False,
            "verbose": False,
            "connect_timeout": 5.0,
            "read_timeout": 30.0,
            "retry": 0,
            "deadline": None,
            "limit": 20,
            "command": "tv",
            "id": "57243",
            "ratings": False,
            "language": None,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_cmd_tv_default_summary_shape(self) -> None:
        """Default ``cmd_tv`` emits the curated summary shape (no ratings fetch)."""
        from arr_cli.seerr import cmd_tv

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.DETAIL_PAYLOAD,
        ) as mock_get:
            output = _capture_stdout(cmd_tv, args, None)
        # Single GET -- the --ratings fetch MUST NOT happen on the
        # default path so callers who don't ask for RT data don't pay
        # the extra round trip.
        self.assertEqual(len(mock_get.call_args_list), 1)
        rendered = json.loads(output)
        self.assertEqual(rendered["name"], "Doctor Who")
        self.assertEqual(rendered["originalName"], "Doctor Who")
        self.assertEqual(rendered["firstAirDate"], "2005-03-26")
        self.assertEqual(rendered["genres"], "Action & Adventure")
        self.assertEqual(rendered["networks"], "BBC One")
        self.assertEqual(rendered["numberOfSeasons"], 13)
        self.assertEqual(rendered["status"], "Ended")
        # Ratings absent on the no-flag path so the operator sees a
        # visible-but-empty ``"ratings": null`` cell rather than a
        # misleading ``"<null>"`` placeholder.
        self.assertIsNone(rendered["ratings"])

    def test_cmd_tv_genres_networks_flattened_to_string(self) -> None:
        """Multi-entry ``genres`` / ``networks`` arrays flatten to a comma-joined string."""
        from arr_cli.seerr import cmd_tv

        payload = {
            **self.DETAIL_PAYLOAD,
            "genres": [
                {"id": 10759, "name": "Action & Adventure"},
                {"id": 18, "name": "Drama"},
                {"id": 10765, "name": "Sci-Fi & Fantasy"},
            ],
            "networks": [
                {"id": 97, "name": "BBC One"},
                {"id": 380, "name": "BBC America"},
            ],
        }
        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get", return_value=payload
        ):
            output = _capture_stdout(cmd_tv, args, None)
        rendered = json.loads(output)
        self.assertEqual(
            rendered["genres"],
            "Action & Adventure, Drama, Sci-Fi & Fantasy",
        )
        self.assertEqual(
            rendered["networks"], "BBC One, BBC America"
        )

    def test_cmd_tv_ratings_flag_merges_second_endpoint(self) -> None:
        """``--ratings`` triggers a second GET and merges RT scores under ``ratings``."""
        from arr_cli.seerr import cmd_tv

        args = self._make_args(ratings=True)
        with patch(
            "arr_cli.seerr.transport.get",
            side_effect=[self.DETAIL_PAYLOAD, self.RATINGS_PAYLOAD],
        ) as mock_get:
            output = _capture_stdout(cmd_tv, args, None)
        # Two calls: /api/v1/tv/<id> then /api/v1/tv/<id>/ratings.
        self.assertEqual(len(mock_get.call_args_list), 2)
        rendered = json.loads(output)
        # Top-level fields still come from the detail payload ...
        self.assertEqual(rendered["name"], "Doctor Who")
        # ... and the RT scores arrive nested under ``ratings``.
        self.assertIsNotNone(rendered["ratings"])
        self.assertEqual(rendered["ratings"]["criticsScore"], 90)
        self.assertEqual(rendered["ratings"]["audienceScore"], 86)

    def test_cmd_tv_verbose_emits_verbatim_payload(self) -> None:
        """``--verbose`` bypasses the renderer and emits the verbatim payload."""
        from arr_cli.seerr import cmd_tv

        args = self._make_args(verbose=True)
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.DETAIL_PAYLOAD,
        ):
            output = _capture_stdout(cmd_tv, args, None)
        self.assertEqual(json.loads(output), self.DETAIL_PAYLOAD)

    def test_cmd_tv_language_forwarded_on_both_calls(self) -> None:
        """``--language en`` is forwarded as ``?language=en`` on both endpoints."""
        from arr_cli.seerr import cmd_tv

        args = self._make_args(language="en", ratings=True)
        with patch(
            "arr_cli.seerr.transport.get",
            side_effect=[self.DETAIL_PAYLOAD, self.RATINGS_PAYLOAD],
        ) as mock_get:
            _capture_stdout(cmd_tv, args, None)
        self.assertEqual(len(mock_get.call_args_list), 2)
        for call in mock_get.call_args_list:
            self.assertEqual(call.kwargs.get("params"), {"language": "en"})

    def test_cmd_tv_no_language_omits_language_param(self) -> None:
        """Without ``--language``, no ``language`` key rides on the query string."""
        from arr_cli.seerr import cmd_tv

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.DETAIL_PAYLOAD,
        ) as mock_get:
            _capture_stdout(cmd_tv, args, None)
        self.assertEqual(len(mock_get.call_args_list), 1)
        # ``params`` is forwarded as the documented kwarg shape;
        # ``None`` is the absence-of-language signal so we don't
        # pollute the URL with an empty ``?language=``.
        self.assertIsNone(mock_get.call_args_list[0].kwargs.get("params"))

    def test_cmd_tv_hits_seerr_tv_endpoint(self) -> None:
        """``cmd_tv`` hits ``/api/v1/tv/<id>`` exactly (no legacy ``.../tv/multi`` shape)."""
        from arr_cli.seerr import cmd_tv

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.DETAIL_PAYLOAD,
        ) as mock_get:
            _capture_stdout(cmd_tv, args, None)
        self.assertEqual(len(mock_get.call_args_list), 1)
        call = mock_get.call_args_list[0]
        args_, _ = call
        # ``transport.get`` is invoked as
        # ``transport.get(SERVICE_NAME, path, ...)``; ``path`` is the
        # second positional argument. Inspect both positional and
        # keyword forms for forward-compat with future signature
        # changes.
        if len(args_) >= 2:
            path: str | None = args_[1]
        else:
            path = call.kwargs.get("path")
        self.assertEqual(
            path, "/api/v1/tv/57243",
            msg=f"cmd_tv targeted unexpected path: {path!r}",
        )

    def test_cmd_tv_ratings_call_targets_ratings_subpath(self) -> None:
        """``--ratings`` issues a second GET against ``/api/v1/tv/<id>/ratings``."""
        from arr_cli.seerr import cmd_tv

        args = self._make_args(ratings=True)
        with patch(
            "arr_cli.seerr.transport.get",
            side_effect=[self.DETAIL_PAYLOAD, self.RATINGS_PAYLOAD],
        ) as mock_get:
            _capture_stdout(cmd_tv, args, None)
        self.assertEqual(len(mock_get.call_args_list), 2)
        # First call: detail; second: ratings sub-resource.
        detail_call, ratings_call = mock_get.call_args_list
        detail_args, _ = detail_call
        ratings_args, _ = ratings_call
        detail_path: str | None = (
            detail_args[1] if len(detail_args) >= 2 else None
        )
        ratings_path: str | None = (
            ratings_args[1] if len(ratings_args) >= 2 else None
        )
        self.assertEqual(detail_path, "/api/v1/tv/57243")
        self.assertEqual(ratings_path, "/api/v1/tv/57243/ratings")

    def test_cmd_tv_does_not_target_legacy_overseerr_paths(self) -> None:
        """Defensive guard against copy-paste back to Overseerr-shaped paths.

        Defends against a future regression that swings back to a
        legacy Overseerr shape (e.g. ``/api/v1/tv/<id>`` with a
        ``/ratings/v2`` subresource, or ``/api/v1/tvs/...`` plural
        typos). Caught at the unit layer in milliseconds rather than
        at the operator's instance as ``HTTP 404``.
        """
        from arr_cli.seerr import cmd_tv

        args = self._make_args(ratings=True)
        with patch(
            "arr_cli.seerr.transport.get",
            side_effect=[self.DETAIL_PAYLOAD, self.RATINGS_PAYLOAD],
        ) as mock_get:
            _capture_stdout(cmd_tv, args, None)
        forbidden_substrings = ("/multi", "/v2", "/v3", "/tvs/")
        for call in mock_get.call_args_list:
            args_, _ = call
            path = args_[1] if len(args_) >= 2 else call.kwargs.get("path")
            self.assertIsNotNone(path)
            for forbidden in forbidden_substrings:
                self.assertNotIn(
                    forbidden, path,
                    msg=(
                        f"cmd_tv path must not contain {forbidden!r} "
                        f"(legacy Overseerr shape): got {path!r}"
                    ),
                )


# ---------------------------------------------------------------------------
# Test: ``cmd_tv`` HTTP path-pinning via the live ``seerr main`` entry point
# ---------------------------------------------------------------------------


class TestCmdTvHttpPath(unittest.TestCase):
    """End-to-end path pin via the real ``seerr main`` entry point.

    Mirrors ``TestCmdSearch::test_cmd_search_does_not_hit_legacy_multi_path``:
    mock the actual HTTP layer with :mod:`responses`, invoke ``seerr main``
    with a real config, and confirm the registered URL matchers fired.
    Any future regression to a wrong path or query string leaves the
    mock unmatched and surfaces as a ``ConnectionError`` exit code
    rather than a silent 404.
    """

    DETAIL_PAYLOAD: dict[str, Any] = {
        "id": 57243,
        "name": "Doctor Who",
        "originalName": "Doctor Who",
        "firstAirDate": "2005-03-26",
        "genres": [{"id": 10759, "name": "Action & Adventure"}],
        "networks": [{"id": 97, "name": "BBC One"}],
        "numberOfSeasons": 13,
        "status": "Ended",
    }

    RATINGS_PAYLOAD: dict[str, Any] = {
        "criticsScore": 90,
        "audienceScore": 86,
    }

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="seerr-test-"))
        self.cfg_path = _write_toml_config(self.tmp_dir)

    def tearDown(self) -> None:
        import shutil
        try:
            shutil.rmtree(self.tmp_dir)
        except OSError:
            pass

    def test_cmd_tv_hits_api_v1_tv_endpoint(self) -> None:
        """``seerr tv 57243`` hits ``/api/v1/tv/57243`` with no required params."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/tv/57243",
                json=self.DETAIL_PAYLOAD,
                status=200,
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                ["--config", str(self.cfg_path), "tv", "57243"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_cmd_tv_with_ratings_hits_both_endpoints(self) -> None:
        """``seerr tv 57243 --ratings`` hits the detail endpoint AND the ratings sub-resource."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/tv/57243",
                json=self.DETAIL_PAYLOAD,
                status=200,
            )
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/tv/57243/ratings",
                json=self.RATINGS_PAYLOAD,
                status=200,
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "tv", "57243", "--ratings",
                ]
            )
            self.assertEqual(exit_code, 0)
            # Both registered mocks fired, so both endpoints were
            # targeted with the documented path shapes. Any other path
            # would have left a mock unmatched.
            self.assertEqual(len(rsps.calls), 2)

    def test_cmd_tv_language_forwards_as_query_param(self) -> None:
        """``--language en`` rides the wire as ``?language=en`` on the detail endpoint."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/tv/57243",
                json=self.DETAIL_PAYLOAD,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {"language": "en"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "tv", "57243", "--language", "en",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)


# ---------------------------------------------------------------------------
# Test: ``cmd_movie`` -- per-movie details + optional Rotten Tomatoes ratings
# ---------------------------------------------------------------------------


class TestCmdMovie(unittest.TestCase):
    """Regression tests pinning the endpoint, params, and summary shape for ``cmd_movie``.

    Seer's per-movie detail endpoint is
    ``GET /api/v1/movie/{movieId}?language=...``.
    ``--ratings`` adds
    ``GET /api/v1/movie/{movieId}/ratings?language=...`` and merges
    the RT critic + audience scores into the default summary under
    ``payload["ratings"]``. The Matrix payload (id=603) is ~110KB on
    a live Seer instance and includes ``genres[]``; the renderer
    flattens the ``genres`` array to a comma-joined string and
    formats the raw ``runtime`` integer (minutes) as
    ``"<X>h <Y>m"`` so the default ``--human`` rendering stays
    readable instead of dumping 110KB of JSON or surfacing raw
    minutes that operators cannot quickly parse.

    Structural twin of :class:`TestCmdTv` so future drift between
    the two commands fails the unit suite immediately.
    """

    DETAIL_PAYLOAD: dict[str, Any] = {
        "id": 603,
        "name": "The Matrix",
        "originalTitle": "The Matrix",
        "releaseDate": "1999-03-31",
        "runtime": 136,
        "genres": [{"id": 28, "name": "Action"}],
        "tagline": "Welcome to the Real World.",
    }

    RATINGS_PAYLOAD: dict[str, Any] = {
        "criticsScore": 83,
        "audienceScore": 85,
    }

    def _make_args(self, **overrides: Any) -> argparse.Namespace:
        """Build an ``argparse.Namespace`` mirroring the ``movie`` subparser defaults."""
        base: dict[str, Any] = {
            "config": None,
            "debug": False,
            "quiet": False,
            "human": False,
            "verbose": False,
            "connect_timeout": 5.0,
            "read_timeout": 30.0,
            "retry": 0,
            "deadline": None,
            "limit": 20,
            "command": "movie",
            "id": "603",
            "ratings": False,
            "language": None,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_cmd_movie_default_summary_shape(self) -> None:
        """Default ``cmd_movie`` emits the curated summary shape (no ratings fetch)."""
        from arr_cli.seerr import cmd_movie

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.DETAIL_PAYLOAD,
        ) as mock_get:
            output = _capture_stdout(cmd_movie, args, None)
        # Single GET -- the --ratings fetch MUST NOT happen on the
        # default path so callers who don't ask for RT data don't pay
        # the extra round trip.
        self.assertEqual(len(mock_get.call_args_list), 1)
        rendered = json.loads(output)
        self.assertEqual(rendered["name"], "The Matrix")
        self.assertEqual(rendered["originalTitle"], "The Matrix")
        self.assertEqual(rendered["releaseDate"], "1999-03-31")
        # Raw runtime minutes (136) is reformatted as ``"<X>h <Y>m"``
        # (``2h 16m``); the spec'd display format which matters because
        # raw minutes is not human-readable.
        self.assertEqual(rendered["runtime"], "2h 16m")
        self.assertEqual(rendered["genres"], "Action")
        self.assertEqual(rendered["tagline"], "Welcome to the Real World.")
        # Ratings absent on the no-flag path so the operator sees a
        # visible-but-empty ``"ratings": null`` cell rather than a
        # misleading ``"<null>"`` placeholder.
        self.assertIsNone(rendered["ratings"])

    def test_cmd_movie_genres_flattened_to_string(self) -> None:
        """Multi-entry ``genres`` array flattens to a comma-joined string."""
        from arr_cli.seerr import cmd_movie

        payload = {
            **self.DETAIL_PAYLOAD,
            "genres": [
                {"id": 28, "name": "Action"},
                {"id": 12, "name": "Adventure"},
                {"id": 878, "name": "Science Fiction"},
            ],
        }
        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get", return_value=payload
        ):
            output = _capture_stdout(cmd_movie, args, None)
        rendered = json.loads(output)
        self.assertEqual(
            rendered["genres"],
            "Action, Adventure, Science Fiction",
        )

    def test_cmd_movie_ratings_flag_merges_second_endpoint(self) -> None:
        """``--ratings`` triggers a second GET and merges RT scores under ``ratings``."""
        from arr_cli.seerr import cmd_movie

        args = self._make_args(ratings=True)
        with patch(
            "arr_cli.seerr.transport.get",
            side_effect=[self.DETAIL_PAYLOAD, self.RATINGS_PAYLOAD],
        ) as mock_get:
            output = _capture_stdout(cmd_movie, args, None)
        # Two calls: /api/v1/movie/<id> then /api/v1/movie/<id>/ratings.
        self.assertEqual(len(mock_get.call_args_list), 2)
        rendered = json.loads(output)
        # Top-level fields still come from the detail payload ...
        self.assertEqual(rendered["name"], "The Matrix")
        # ... and the RT scores arrive nested under ``ratings``.
        self.assertIsNotNone(rendered["ratings"])
        self.assertEqual(rendered["ratings"]["criticsScore"], 83)
        self.assertEqual(rendered["ratings"]["audienceScore"], 85)

    def test_cmd_movie_verbose_emits_verbatim_payload(self) -> None:
        """``--verbose`` bypasses the renderer and emits the verbatim payload."""
        from arr_cli.seerr import cmd_movie

        args = self._make_args(verbose=True)
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.DETAIL_PAYLOAD,
        ):
            output = _capture_stdout(cmd_movie, args, None)
        self.assertEqual(json.loads(output), self.DETAIL_PAYLOAD)

    def test_cmd_movie_language_forwarded_on_both_calls(self) -> None:
        """``--language en`` is forwarded as ``?language=en`` on both endpoints."""
        from arr_cli.seerr import cmd_movie

        args = self._make_args(language="en", ratings=True)
        with patch(
            "arr_cli.seerr.transport.get",
            side_effect=[self.DETAIL_PAYLOAD, self.RATINGS_PAYLOAD],
        ) as mock_get:
            _capture_stdout(cmd_movie, args, None)
        self.assertEqual(len(mock_get.call_args_list), 2)
        for call in mock_get.call_args_list:
            self.assertEqual(call.kwargs.get("params"), {"language": "en"})

    def test_cmd_movie_no_language_omits_language_param(self) -> None:
        """Without ``--language``, no ``language`` key rides on the query string."""
        from arr_cli.seerr import cmd_movie

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.DETAIL_PAYLOAD,
        ) as mock_get:
            _capture_stdout(cmd_movie, args, None)
        self.assertEqual(len(mock_get.call_args_list), 1)
        # ``params`` is forwarded as the documented kwarg shape;
        # ``None`` is the absence-of-language signal so we don't
        # pollute the URL with an empty ``?language=``.
        self.assertIsNone(mock_get.call_args_list[0].kwargs.get("params"))

    def test_cmd_movie_hits_seerr_movie_endpoint(self) -> None:
        """``cmd_movie`` hits ``/api/v1/movie/<id>`` exactly (no legacy shape)."""
        from arr_cli.seerr import cmd_movie

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.DETAIL_PAYLOAD,
        ) as mock_get:
            _capture_stdout(cmd_movie, args, None)
        self.assertEqual(len(mock_get.call_args_list), 1)
        call = mock_get.call_args_list[0]
        args_, _ = call
        # ``transport.get`` is invoked as
        # ``transport.get(SERVICE_NAME, path, ...)``; ``path`` is the
        # second positional argument. Inspect both positional and
        # keyword forms for forward-compat with future signature
        # changes.
        if len(args_) >= 2:
            path: str | None = args_[1]
        else:
            path = call.kwargs.get("path")
        self.assertEqual(
            path, "/api/v1/movie/603",
            msg=f"cmd_movie targeted unexpected path: {path!r}",
        )

    def test_cmd_movie_ratings_call_targets_ratings_subpath(self) -> None:
        """``--ratings`` issues a second GET against ``/api/v1/movie/<id>/ratings``."""
        from arr_cli.seerr import cmd_movie

        args = self._make_args(ratings=True)
        with patch(
            "arr_cli.seerr.transport.get",
            side_effect=[self.DETAIL_PAYLOAD, self.RATINGS_PAYLOAD],
        ) as mock_get:
            _capture_stdout(cmd_movie, args, None)
        self.assertEqual(len(mock_get.call_args_list), 2)
        # First call: detail; second: ratings sub-resource.
        detail_call, ratings_call = mock_get.call_args_list
        detail_args, _ = detail_call
        ratings_args, _ = ratings_call
        detail_path: str | None = (
            detail_args[1] if len(detail_args) >= 2 else None
        )
        ratings_path: str | None = (
            ratings_args[1] if len(ratings_args) >= 2 else None
        )
        self.assertEqual(detail_path, "/api/v1/movie/603")
        self.assertEqual(ratings_path, "/api/v1/movie/603/ratings")

    def test_cmd_movie_does_not_target_legacy_overseerr_paths(self) -> None:
        """Defensive guard against copy-paste back to Overseerr-shaped paths.

        Defends against a future regression that swings back to a
        legacy Overseerr shape (e.g. ``/api/v1/movie/<id>`` with a
        ``/ratings/v2`` subresource, or ``/api/v1/movies/...``
        plural typos). Caught at the unit layer in milliseconds
        rather than at the operator's instance as ``HTTP 404``.
        """
        from arr_cli.seerr import cmd_movie

        args = self._make_args(ratings=True)
        with patch(
            "arr_cli.seerr.transport.get",
            side_effect=[self.DETAIL_PAYLOAD, self.RATINGS_PAYLOAD],
        ) as mock_get:
            _capture_stdout(cmd_movie, args, None)
        forbidden_substrings = ("/multi", "/v2", "/v3", "/movies/")
        for call in mock_get.call_args_list:
            args_, _ = call
            path = args_[1] if len(args_) >= 2 else call.kwargs.get("path")
            self.assertIsNotNone(path)
            for forbidden in forbidden_substrings:
                self.assertNotIn(
                    forbidden, path,
                    msg=(
                        f"cmd_movie path must not contain {forbidden!r} "
                        f"(legacy Overseerr shape): got {path!r}"
                    ),
                )


# ---------------------------------------------------------------------------
# Test: ``cmd_movie`` HTTP path-pinning via the live ``seerr main`` entry point
# ---------------------------------------------------------------------------


class TestCmdMovieHttpPath(unittest.TestCase):
    """End-to-end path pin via the real ``seerr main`` entry point.

    Mirrors :class:`TestCmdTvHttpPath`: mock the actual HTTP layer
    with :mod:`responses`, invoke ``seerr main`` with a real config,
    and confirm the registered URL matchers fired. Any future
    regression to a wrong path or query string leaves the mock
    unmatched and surfaces as a ``ConnectionError`` exit code
    rather than a silent 404.
    """

    DETAIL_PAYLOAD: dict[str, Any] = {
        "id": 603,
        "name": "The Matrix",
        "originalTitle": "The Matrix",
        "releaseDate": "1999-03-31",
        "runtime": 136,
        "genres": [{"id": 28, "name": "Action"}],
        "tagline": "Welcome to the Real World.",
    }

    RATINGS_PAYLOAD: dict[str, Any] = {
        "criticsScore": 83,
        "audienceScore": 85,
    }

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="seerr-test-"))
        self.cfg_path = _write_toml_config(self.tmp_dir)

    def tearDown(self) -> None:
        import shutil
        try:
            shutil.rmtree(self.tmp_dir)
        except OSError:
            pass

    def test_cmd_movie_hits_api_v1_movie_endpoint(self) -> None:
        """``seerr movie 603`` hits ``/api/v1/movie/603`` with no required params."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/movie/603",
                json=self.DETAIL_PAYLOAD,
                status=200,
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                ["--config", str(self.cfg_path), "movie", "603"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_cmd_movie_with_ratings_hits_both_endpoints(self) -> None:
        """``seerr movie 603 --ratings`` hits the detail endpoint AND the ratings sub-resource."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/movie/603",
                json=self.DETAIL_PAYLOAD,
                status=200,
            )
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/movie/603/ratings",
                json=self.RATINGS_PAYLOAD,
                status=200,
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "movie", "603", "--ratings",
                ]
            )
            self.assertEqual(exit_code, 0)
            # Both registered mocks fired, so both endpoints were
            # targeted with the documented path shapes. Any other path
            # would have left a mock unmatched.
            self.assertEqual(len(rsps.calls), 2)

    def test_cmd_movie_language_forwards_as_query_param(self) -> None:
        """``--language en`` rides the wire as ``?language=en`` on the detail endpoint."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/movie/603",
                json=self.DETAIL_PAYLOAD,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {"language": "en"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "movie", "603", "--language", "en",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)


# ---------------------------------------------------------------------------
# Test: ``cmd_trending`` -- live trending discover feed
# ---------------------------------------------------------------------------


class TestCmdTrending(unittest.TestCase):
    """Regression tests pinning the endpoint, params, and summary shape for ``cmd_trending``.

    Seer's discover endpoint is
    ``GET /api/v1/discover/trending?timeWindow=week[&mediaType=...][&language=...]``.
    It returns a paginated envelope of the shape
    ``{page, totalPages, totalResults, results: [...]}`` -- the same
    shape :func:`cmd_search` consumes -- so the renderer mirrors
    :func:`_summary_seerr_search` exactly.

    Defaults:

    * ``timeWindow`` defaults to ``week`` and ALWAYS rides on the
      query string (US-3 AC6: the documented default IS the value
      the operator wants).
    * ``mediaType`` is only forwarded when the operator passes the
      positional ``MEDIA_TYPE`` (omit = all media types).
    * ``language`` is only forwarded when ``--language`` is set
      (no empty ``?language=`` rides the wire).

    ``choices=`` validation on both positionals runs at parse time:
    invalid values raise ``SystemExit(2)`` via argparse before any
    HTTP request is issued.
    """

    ENVELOPE: dict[str, Any] = {
        "page": 1,
        "totalPages": 1,
        "totalResults": 2,
        "results": [
            {
                "id": 101,
                "title": "Dune: Part Two",
                "mediaType": "movie",
                "releaseDate": "2024-03-01",
                "mediaInfo": {"tmdbId": 693134},
            },
            {
                "id": 102,
                "title": "Shōgun",
                "mediaType": "tv",
                "releaseDate": "2024-02-27",
                "mediaInfo": {"tmdbId": 127309},
            },
        ],
    }

    BARE_LIST: list[dict[str, Any]] = [
        {
            "title": "Dune: Part Two",
            "mediaType": "movie",
            "releaseDate": "2024-03-01",
            "mediaInfo": {"tmdbId": 693134},
        }
    ]

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="seerr-test-"))
        self.cfg_path = _write_toml_config(self.tmp_dir)

    def tearDown(self) -> None:
        import shutil
        try:
            shutil.rmtree(self.tmp_dir)
        except OSError:
            pass

    # ------------------------------------------------------------------ 3.1
    def test_cmd_trending_dispatch_table_registration(self) -> None:
        """``cmd_trending`` is registered in ``_DISPATCH`` and exported via ``__all__``."""
        import arr_cli.seerr as seerr

        # Dispatch table entry points to a callable handler.
        self.assertIn("trending", seerr._DISPATCH)
        self.assertTrue(
            callable(seerr._DISPATCH["trending"]),
            msg="cmd_trending is not callable",
        )
        # Public surface: the handler and the path constant are exported
        # so tests can assert against the literal.
        self.assertIn("cmd_trending", seerr.__all__)
        self.assertIn("TRENDING_PATH", seerr.__all__)
        self.assertEqual(
            seerr.TRENDING_PATH, "/api/v1/discover/trending"
        )

    # ------------------------------------------------------------------ 3.2
    def test_seerr_trending_default_hits_endpoint_with_timewindow_week(
        self,
    ) -> None:
        """``seerr trending`` hits ``/api/v1/discover/trending`` with ``timeWindow=week`` default."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/trending",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {"timeWindow": "week"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                ["--config", str(self.cfg_path), "trending"]
            )
            self.assertEqual(exit_code, 0)
            # The single registered mock fired, so the path was
            # ``/api/v1/discover/trending`` AND ``timeWindow=week``
            # was on the wire and matched. Any other path or query
            # value would have left the mock unmatched and surfaced a
            # connection error.
            self.assertEqual(len(rsps.calls), 1)

    # ------------------------------------------------------------------ 3.3
    def test_seerr_trending_movie_passes_media_type_movie(self) -> None:
        """``seerr trending movie`` forwards ``mediaType=movie`` alongside ``timeWindow=week``."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/trending",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {"mediaType": "movie", "timeWindow": "week"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                ["--config", str(self.cfg_path), "trending", "movie"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_seerr_trending_tv_passes_media_type_tv(self) -> None:
        """``seerr trending tv`` forwards ``mediaType=tv`` alongside ``timeWindow=week``."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/trending",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {"mediaType": "tv", "timeWindow": "week"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                ["--config", str(self.cfg_path), "trending", "tv"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_seerr_trending_invalid_media_type_rejected_at_parse_time(
        self,
    ) -> None:
        """``seerr trending foo`` rejects ``foo`` at parse time; no HTTP request issued.

        ``choices=`` validation runs at parse time and triggers
        argparse's ``error()`` path. ``main_wrapper`` translates
        that ``SystemExit(2)`` into a :class:`ConfigError` so the
        operator sees the documented exit code ``1`` (AGENTS.md §6
        "ConfigError -- malformed CLI input") and a structured
        ``service=config op=parse message=...`` stderr line. The
        transport layer is never reached, so the registered mock
        stays unfired.
        """
        import arr_cli.seerr as seerr

        with responses.RequestsMock() as rsps:
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf), \
                    contextlib.redirect_stderr(stderr_buf):
                exit_code = seerr.main(
                    [
                        "--config", str(self.cfg_path),
                        "trending", "foo",
                    ]
                )
            self.assertEqual(exit_code, 1)
            self.assertEqual(len(rsps.calls), 0)
            # argparse's ``error()`` writes its diagnostic to stderr,
            # keeping stdout pipe-clean so downstream consumers
            # aren't disturbed by an invalid invocation.
            self.assertEqual(stdout_buf.getvalue(), "")
            # Structured ConfigError line confirms the parse-error
            # surface (mirrors the cli_common contract). Argparse's
            # ``error()`` writes its own usage line to stderr first,
            # then ``main_wrapper`` appends the structured
            # ``service=config op=parse ...`` follow-up line.
            self.assertIn(
                "service=config op=parse",
                stderr_buf.getvalue(),
                msg=(
                    "expected structured parse-error stderr line; "
                    f"got {stderr_buf.getvalue()!r}"
                ),
            )

    # ------------------------------------------------------------------ 3.4
    def test_seerr_trending_movie_day_passes_timewindow_day(self) -> None:
        """``seerr trending movie day`` forwards ``mediaType=movie&timeWindow=day``."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/trending",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {"mediaType": "movie", "timeWindow": "day"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "trending", "movie", "day",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_seerr_trending_tv_week_passes_explicit_week(self) -> None:
        """``seerr trending tv week`` forwards ``mediaType=tv&timeWindow=week`` (explicit)."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/trending",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {"mediaType": "tv", "timeWindow": "week"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "trending", "tv", "week",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_seerr_trending_one_positional_defaults_timewindow_to_week(
        self,
    ) -> None:
        """``seerr trending movie`` (one positional) defaults ``timeWindow`` to ``week``."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/trending",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {"mediaType": "movie", "timeWindow": "week"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                ["--config", str(self.cfg_path), "trending", "movie"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_seerr_trending_invalid_time_window_rejected_at_parse_time(
        self,
    ) -> None:
        """``seerr trending movie hour`` rejects ``hour`` at parse time; no HTTP issued.

        Mirrors :func:`test_seerr_trending_invalid_media_type_rejected_at_parse_time`:
        ``choices=`` validation runs at parse time, ``main_wrapper``
        translates the resulting ``SystemExit(2)`` to a
        :class:`ConfigError` (exit ``1``), and the transport layer
        is never reached.
        """
        import arr_cli.seerr as seerr

        with responses.RequestsMock() as rsps:
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf), \
                    contextlib.redirect_stderr(stderr_buf):
                exit_code = seerr.main(
                    [
                        "--config", str(self.cfg_path),
                        "trending", "movie", "hour",
                    ]
                )
            self.assertEqual(exit_code, 1)
            self.assertEqual(len(rsps.calls), 0)
            self.assertEqual(stdout_buf.getvalue(), "")
            self.assertIn(
                "service=config op=parse",
                stderr_buf.getvalue(),
                msg=(
                    "expected structured parse-error stderr line; "
                    f"got {stderr_buf.getvalue()!r}"
                ),
            )

    # ------------------------------------------------------------------ 3.5
    def test_seerr_trending_language_forwards_on_wire(self) -> None:
        """``seerr trending --language en`` forwards ``language=en`` alongside other params."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/trending",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {
                            "mediaType": "movie",
                            "timeWindow": "week",
                            "language": "en",
                        }
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "trending", "movie",
                    "--language", "en",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_seerr_trending_no_language_omits_language_key(self) -> None:
        """Without ``--language``, no ``language`` key rides on the query string."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/trending",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {"timeWindow": "week"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                ["--config", str(self.cfg_path), "trending"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    # ------------------------------------------------------------------ 3.6
    def _make_args(self, **overrides: Any) -> argparse.Namespace:
        """Build an ``argparse.Namespace`` mirroring the ``trending`` subparser defaults."""
        base: dict[str, Any] = {
            "config": None,
            "debug": False,
            "quiet": False,
            "human": False,
            "verbose": False,
            "connect_timeout": 5.0,
            "read_timeout": 30.0,
            "retry": 0,
            "deadline": None,
            "limit": 20,
            "command": "trending",
            "media_type": None,
            "time_window": "week",
            "language": None,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_cmd_trending_human_renders_table(self) -> None:
        """``--human`` renders the curated summary as a tabular view."""
        from arr_cli.seerr import cmd_trending

        args = self._make_args(human=True, media_type="movie")
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.ENVELOPE,
        ):
            output = _capture_stdout(cmd_trending, args, None)
        # The header line names the columns the renderer projects:
        # ``title``, ``mediaType``, ``releaseDate``,
        # ``mediaInfo.tmdbId`` (dot-path traversal resolves the
        # nested key).
        header_line = output.splitlines()[0]
        for column in (
            "title",
            "mediaType",
            "releaseDate",
            "mediaInfo.tmdbId",
        ):
            self.assertIn(
                column, header_line,
                msg=(
                    f"column {column!r} missing from --human header: "
                    f"{header_line!r}"
                ),
            )
        # One rendered data row per ``results`` entry.
        data_lines = [
            line for line in output.splitlines()[2:]
            if line.strip()
            and not line.startswith("\u2026")
            and not line.startswith("…")
        ]
        self.assertEqual(len(data_lines), 2)

    # ------------------------------------------------------------------ 3.7
    def test_cmd_trending_verbose_emits_verbatim_envelope(self) -> None:
        """``--verbose`` bypasses the renderer and emits the envelope verbatim."""
        from arr_cli.seerr import cmd_trending

        args = self._make_args(verbose=True, media_type="movie")
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.ENVELOPE,
        ):
            output = _capture_stdout(cmd_trending, args, None)
        self.assertEqual(json.loads(output), self.ENVELOPE)

    # ------------------------------------------------------------------ 3.8
    def test_summary_seerr_trending_envelope_unwraps_results(self) -> None:
        """Renderer iterates ``results`` of a paginated envelope, not the envelope itself."""
        from arr_cli.facade.output import _summary_seerr_trending

        rendered = _summary_seerr_trending(self.ENVELOPE)
        self.assertEqual(len(rendered), 2)
        self.assertEqual(rendered[0]["title"], "Dune: Part Two")
        self.assertEqual(rendered[1]["title"], "Sh\u014dgun")
        # Nested ``mediaInfo.tmdbId`` is preserved.
        self.assertEqual(rendered[0]["mediaInfo"]["tmdbId"], 693134)
        self.assertEqual(rendered[1]["mediaInfo"]["tmdbId"], 127309)

    def test_summary_seerr_trending_bare_list_unchanged(self) -> None:
        """Renderer iterates a bare list payload (defensive envelope-drift guard)."""
        from arr_cli.facade.output import _summary_seerr_trending

        rendered = _summary_seerr_trending(self.BARE_LIST)
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0]["title"], "Dune: Part Two")
        self.assertEqual(rendered[0]["mediaInfo"]["tmdbId"], 693134)

    def test_summary_seerr_trending_envelope_without_results_returns_empty(
        self,
    ) -> None:
        """An envelope missing the ``results`` key maps to ``[]`` rather than crashing."""
        from arr_cli.facade.output import _summary_seerr_trending

        rendered = _summary_seerr_trending(
            {"page": 1, "totalPages": 0, "totalResults": 0}
        )
        self.assertEqual(rendered, [])

    def test_summary_seerr_trending_non_mapping_non_list_returns_empty(
        self,
    ) -> None:
        """A scalar / ``None`` payload maps to ``[]`` rather than crashing."""
        from arr_cli.facade.output import _summary_seerr_trending

        self.assertEqual(_summary_seerr_trending(None), [])
        self.assertEqual(_summary_seerr_trending("not a list"), [])
        self.assertEqual(_summary_seerr_trending(42), [])

    def test_summary_seerr_trending_envelope_drops_non_mapping_items(
        self,
    ) -> None:
        """Non-Mapping items inside ``results`` are dropped silently."""
        from arr_cli.facade.output import _summary_seerr_trending

        envelope = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 3,
            "results": [
                {
                    "title": "Foo",
                    "mediaType": "movie",
                    "releaseDate": "2024-01-01",
                    "mediaInfo": {"tmdbId": 1},
                },
                "stray non-mapping item",
                {
                    "title": "Bar",
                    "mediaType": "tv",
                    "releaseDate": "2024-02-01",
                    "mediaInfo": {"tmdbId": 2},
                },
            ],
        }
        rendered = _summary_seerr_trending(envelope)
        # Two curated rows survive; the stray string is dropped.
        self.assertEqual(len(rendered), 2)
        self.assertEqual(rendered[0]["title"], "Foo")
        self.assertEqual(rendered[1]["title"], "Bar")

    # ------------------------------------------------------------------ 3.9
    def test_cmd_trending_default_invocation_omits_page_param(self) -> None:
        """Default invocation's params dict does not contain a ``page`` key.

        The CLI surface does not expose a ``--page`` flag, so no
        ``?page=`` rides on the wire by default -- matches the
        documented CLI surface in the spec.
        """
        from arr_cli.seerr import cmd_trending

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get", return_value=self.ENVELOPE
        ) as mock_get:
            _capture_stdout(cmd_trending, args, None)
        self.assertEqual(len(mock_get.call_args_list), 1)
        params = mock_get.call_args_list[0].kwargs.get("params") or {}
        self.assertNotIn(
            "page", params,
            msg=(
                "cmd_trending must not inject a `page` key into the "
                "query string when the CLI surface does not expose "
                f"a --page flag; got params={params!r}"
            ),
        )


if __name__ == "__main__":
    unittest.main()