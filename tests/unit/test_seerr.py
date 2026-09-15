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

    The seerr ``user`` command is a single ``GET /auth/me`` probe.
    :func:`arr_cli.facade.transport.get` raises
    :class:`arr_cli.facade.errors.HttpError` (exit ``4``) on any
    non-2xx response, which :func:`arr_cli.facade.cli_common.main_wrapper`
    surfaces as a structured ``service=seerr op=/auth/me status=<code>
    message=...`` stderr line. A 2xx response returns the user JSON
    on stdout and exit ``0``.

    These tests exercise the documented end-to-end contract:

    * HTTP 200 on ``/auth/me`` returns the user object on stdout and
      exit ``0``.
    * Any non-2xx response on ``/auth/me`` surfaces as exit ``4`` with
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
        # Regression: a 2xx response on ``/auth/me`` returns the user
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
                "https://seerr.example/auth/me",
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
        # Regression: any non-2xx response on ``/auth/me`` surfaces as
        # exit ``4`` with a structured stderr line that names the path
        # (mirrors the bug-report's actual ``seerr user`` output
        # shape, but with the new contract: ``op=/auth/me`` instead of
        # ``op=/api/v1/user/me``).
        import arr_cli.seerr as seerr

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/auth/me",
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
        # Structured line: service=seerr op=/auth/me status=503 message=...
        self.assertTrue(
            stderr.startswith(
                "service=seerr op=/auth/me status=503 message="
            ),
            msg=f"unexpected stderr shape: {stderr!r}",
        )
        self.assertIn("HTTP 503 for /auth/me", stderr)


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


if __name__ == "__main__":
    unittest.main()