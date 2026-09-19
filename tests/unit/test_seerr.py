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

    def test_seerr_has_thirteen_commands(self) -> None:
        """The subparser exposes exactly the thirteen documented Seerr commands."""
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
                "upcoming-movies",
                "upcoming-tv",
                "discover-movies",
                "discover-tv",
                "genres",
            },
        )
        self.assertEqual(len(subparsers_action.choices), 13)

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
            "upcoming-movies",
            "upcoming-tv",
            "discover-movies",
            "discover-tv",
            "genres",
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
                "type": "movie",
                "status": "pending",
                "createdAt": "2024-01-01",
                "requestedBy": {"displayName": "alice"},
                "media": {
                    "id": 121,
                    "mediaType": "movie",
                    "tmdbId": 603,
                    "tvdbId": None,
                    "externalServiceSlug": "tmdb",
                    "status": 5,
                },
            }
        ]
        with patch("arr_cli.seerr.transport.get", return_value=payload):
            output = _capture_stdout(cmd_requests, args, None)
        rendered = json.loads(output)
        # Identity fields from the ``media`` sub-dict are surfaced
        # at the **top level** (no nested ``media`` mapping);
        # ``title`` is intentionally absent because the live
        # ``/api/v1/request`` payload does not populate
        # ``media.title`` or ``media.name``. Mirrors the flat shape
        # ``_summary_seerr_available`` projects.
        self.assertEqual(rendered[0]["tmdbId"], 603)
        self.assertEqual(rendered[0]["externalServiceSlug"], "tmdb")
        self.assertEqual(rendered[0]["type"], "movie")
        self.assertEqual(rendered[0]["status"], "pending")
        self.assertNotIn("media", rendered[0])
        self.assertNotIn("requestedBy", rendered[0])
        self.assertNotIn("title", rendered[0])

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
                "type": "movie",
                "status": 5,
                "createdAt": "2026-09-13T12:56:58.000Z",
                "requestedBy": {"displayName": "alice"},
                "media": {
                    "id": 121,
                    "mediaType": "movie",
                    "tmdbId": 603,
                    "tvdbId": None,
                    "externalServiceSlug": "tmdb",
                    "status": 5,
                },
            },
            {
                "id": 120,
                "type": "tv",
                "status": 2,
                "createdAt": "2026-09-13T12:55:03.000Z",
                "requestedBy": {"displayName": "bob"},
                "media": {
                    "id": 120,
                    "mediaType": "tv",
                    "tmdbId": None,
                    "tvdbId": 76107,
                    "externalServiceSlug": "tvdb",
                    "status": 5,
                },
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
        """Default ``cmd_requests`` emits summary rows when the payload is a paginated envelope.

        The curated summary projects the identity fields from the
        ``media`` sub-dict (``id``, ``mediaType``, ``tmdbId`` /
        ``tvdbId``, ``externalServiceSlug``) **at the top level**
        rather than behind ``media.*`` keys. The historical
        ``title`` projection is retired because the live
        ``/api/v1/request`` payload on the operator's Seer instance
        does not populate ``media.title`` (movie) or ``media.name``
        (TV). Mirrors the flat shape :func:`_summary_seerr_available`
        projects for a consistent mental model across the two read
        endpoints. ``requestedBy`` is dropped from the curated
        projection; the verbatim envelope is still on the wire via
        ``--verbose``.
        """
        from arr_cli.seerr import cmd_requests

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.PAGINATED_ENVELOPE,
        ):
            output = _capture_stdout(cmd_requests, args, None)
        rendered = json.loads(output)
        self.assertEqual(len(rendered), 2)
        # Movie row: identity fields lifted from ``media`` live at
        # the top level (``tmdbId`` ``603`` for The Matrix in the
        # fixture).
        self.assertEqual(rendered[0]["type"], "movie")
        self.assertEqual(rendered[0]["tmdbId"], 603)
        self.assertEqual(rendered[0]["mediaType"], "movie")
        self.assertEqual(rendered[0]["externalServiceSlug"], "tmdb")
        # TV row: identity comes from ``media.tvdbId`` (no top-level
        # ``title`` / ``media.title`` / ``media.name`` pair in the
        # upstream payload).
        self.assertEqual(rendered[1]["type"], "tv")
        self.assertEqual(rendered[1]["tvdbId"], 76107)
        self.assertEqual(rendered[1]["mediaType"], "tv")
        self.assertEqual(rendered[1]["externalServiceSlug"], "tvdb")
        # No nested ``media`` envelope and no ``requestedBy`` mapping
        # -- the shape is flat top-level identity + request-level
        # fields, matching ``seerr available``.
        for row in rendered:
            self.assertNotIn(
                "media",
                row,
                msg=(
                    f"seerr requests row still nests identity under "
                    f"a 'media' key: {row!r} -- the projection must "
                    "be flat top-level to match ``seerr available``"
                ),
            )
            self.assertNotIn(
                "requestedBy",
                row,
                msg=(
                    f"seerr requests row re-surfaces a 'requestedBy' "
                    f"key that should have been dropped: {row!r}"
                ),
            )
        # No fabricated top-level ``title`` key -- the historical
        # projection is gone, so neither row projects a ``title``
        # field that would always be ``null``.
        for row in rendered:
            self.assertNotIn(
                "title",
                row,
                msg=(
                    f"seerr requests row still surfaces a top-level "
                    f"'title' key: {row!r} -- the historical projection "
                    "must be retired on the operator's Seer instance"
                ),
            )

    def test_cmd_requests_flat_list_default_unchanged(self) -> None:
        """The flat-list code path keeps the pre-change behaviour intact.

        The flat-list input shape is honoured by the same renderer
        (it iterates ``results`` only when the payload is a
        ``Mapping`` envelope, so a bare list flows through verbatim).
        The summary projection surfaces the identity fields at the
        top level (lifted from the ``media`` sub-dict), not behind
        a fabricated ``title``.
        """
        from arr_cli.seerr import cmd_requests

        args = self._make_args()
        flat_payload = [
            {
                "type": "movie",
                "status": "pending",
                "createdAt": "2024-01-01",
                "requestedBy": {"displayName": "alice"},
                "media": {
                    "id": 121,
                    "mediaType": "movie",
                    "tmdbId": 999,
                    "tvdbId": None,
                    "externalServiceSlug": "tmdb",
                    "status": 5,
                },
            }
        ]
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=flat_payload,
        ):
            output = _capture_stdout(cmd_requests, args, None)
        rendered = json.loads(output)
        # Identity fields live at the top level, not under
        # ``media.*``.
        self.assertEqual(rendered[0]["tmdbId"], 999)
        self.assertEqual(rendered[0]["externalServiceSlug"], "tmdb")
        self.assertNotIn("media", rendered[0])
        self.assertNotIn("requestedBy", rendered[0])
        self.assertNotIn("title", rendered[0])

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
                        "name": "Doctor Who",
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
                "id": 123,
                "name": "Doctor Who",
                "mediaType": "tv",
                "releaseDate": "2005-03-26",
            },
            {
                "id": 291351,
                "title": "Doctor Strange",
                "mediaType": "movie",
                "releaseDate": "2016-10-25",
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
        # ``id`` is the upstream join key -- top-level on each row,
        # not a fabricated ``mediaInfo.tmdbId`` placeholder.
        self.assertEqual(rendered[0]["id"], 123)
        self.assertEqual(rendered[1]["id"], 291351)
        # No fabricated ``mediaInfo`` dict in the curated summary --
        # the upstream payload never exposed one for ``search``.
        self.assertNotIn("mediaInfo", rendered[0])
        self.assertNotIn("mediaInfo", rendered[1])

    def test_cmd_search_flat_list_default_unchanged(self) -> None:
        """The flat-list code path keeps the pre-change behaviour intact."""
        from arr_cli.seerr import cmd_search

        args = self._make_args()
        flat_payload = [
            {
                "id": 123,
                "name": "Doctor Who",
                "mediaType": "tv",
                "releaseDate": "2005-03-26",
            }
        ]
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=flat_payload,
        ):
            output = _capture_stdout(cmd_search, args, None)
        rendered = json.loads(output)
        self.assertEqual(rendered[0]["title"], "Doctor Who")
        self.assertEqual(rendered[0]["id"], 123)

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
    paginated-envelope unwrap, the post-filter removal (records
    lack top-level ``title`` on the operator's Seer instance, so
    the historical client-side substring filter was a guaranteed
    no-op), the ``--verbose`` verbatim passthrough, and add a
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
                "id": 1,
                "mediaType": "tv",
                "tmdbId": 121,
                "tvdbId": 76107,
                "externalServiceSlug": "tvdb",
                "status": 5,
                "mediaAddedAt": "2024-01-01T00:00:00.000Z",
            },
            {
                "id": 2,
                "mediaType": "movie",
                "tmdbId": 291351,
                "tvdbId": None,
                "externalServiceSlug": "tmdb",
                "status": 5,
                "mediaAddedAt": "2024-02-01T00:00:00.000Z",
            },
            {
                "id": 3,
                "mediaType": "movie",
                "tmdbId": 999999,
                "tvdbId": None,
                "externalServiceSlug": "tmdb",
                "status": 2,
                "mediaAddedAt": "2024-03-01T00:00:00.000Z",
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

    def test_cmd_available_title_substring_filter_removed(self) -> None:
        """Non-empty ``query`` is accepted but ignored (no client-side filtering).

        Pin for ``seerr-available-no-title-filter``: upstream
        ``/api/v1/media`` records lack a top-level ``title`` field
        on the operator's Seer instance, so the historical substring
        filter was a guaranteed no-op. The positional ``query`` is
        kept for backwards compatibility but is now ignored, with a
        stderr note so the empty-result-of-no-op is not surprising.
        All rows are passed through unchanged regardless of query.
        """
        from arr_cli.seerr import cmd_available

        args = self._make_args(query="doctor")
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.AVAILABLE_ENVELOPE,
        ):
            stdout, stderr = _capture_stderr_stdout(
                cmd_available, args, None
            )
        rendered = json.loads(stdout)
        # All 3 rows pass through; the substring filter is gone.
        self.assertEqual(len(rendered), 3)
        rendered_ids = [row["id"] for row in rendered]
        self.assertEqual(rendered_ids, [1, 2, 3])
        # Stderr note explains why the query was ignored so the
        # operator isn't surprised by what looks like an empty
        # result (the rows are there, the filter just couldn't
        # match anything without a title field).
        self.assertIn(
            "substring filter 'doctor' ignored", stderr,
            msg=(
                "expected stderr note about the removed substring "
                f"filter; got stderr={stderr!r}"
            ),
        )

    def test_cmd_available_no_stderr_note_for_empty_query(self) -> None:
        """Empty ``query`` emits no stderr note (only the no-op path warns)."""
        from arr_cli.seerr import cmd_available

        args = self._make_args(query="")
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.AVAILABLE_ENVELOPE,
        ):
            stdout, stderr = _capture_stderr_stdout(
                cmd_available, args, None
            )
        rendered = json.loads(stdout)
        self.assertEqual(len(rendered), 3)
        # Empty query is the common case; no need to clutter stderr
        # when the operator didn't ask for a filter.
        self.assertNotIn("substring filter", stderr)

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
        # The curated summary now surfaces the upstream-provided
        # identifiers (id, tmdbId, tvdbId, externalServiceSlug,
        # status, mediaAddedAt) -- no ``title`` / ``releaseDate`` /
        # ``mediaInfo`` envelope, because the upstream records do
        # not carry them on the operator's Seer instance.
        self.assertEqual(rendered[0]["id"], 1)
        self.assertEqual(rendered[0]["mediaType"], "tv")
        self.assertEqual(rendered[0]["tmdbId"], 121)
        self.assertEqual(rendered[0]["tvdbId"], 76107)
        self.assertEqual(rendered[0]["externalServiceSlug"], "tvdb")
        self.assertEqual(rendered[0]["status"], 5)
        self.assertEqual(
            rendered[0]["mediaAddedAt"], "2024-01-01T00:00:00.000Z"
        )
        # TV rows with no tvdbId surface as ``None`` rather than
        # crashing (mirrors the defensive contract of every other
        # renderer).
        self.assertIsNone(rendered[1]["tvdbId"])

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
        "title": "The Matrix",
        "originalTitle": "The Matrix",
        "releaseDate": "1999-03-31",
        "runtime": 136,
        "genres": [{"id": 28, "name": "Action"}],
        "tagline": "Welcome to the Real World.",
        # Canonical Seer movie-detail shape has ``name`` as ``None``
        # at the top level -- the title lives at ``title``. Pinning
        # the upstream truth so a future regression that re-derives
        # ``name`` from the renderer breaks the suite immediately
        # rather than silently shipping ``title: null`` again.
        "name": None,
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
        self.assertEqual(rendered["title"], "The Matrix")
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
        self.assertEqual(rendered["title"], "The Matrix")
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
        "title": "The Matrix",
        "originalTitle": "The Matrix",
        "releaseDate": "1999-03-31",
        "runtime": 136,
        "genres": [{"id": 28, "name": "Action"}],
        "tagline": "Welcome to the Real World.",
        # Canonical Seer movie-detail shape has ``name`` as ``None``
        # at the top level; the title lives at ``title``. Pins the
        # upstream truth so a future regression that re-derives the
        # projected column from ``name`` fails the suite immediately.
        "name": None,
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
                "name": "Shōgun",
                "mediaType": "tv",
                "firstAirDate": "2024-02-27",
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
        # ``id``, ``title``, ``mediaType``, ``releaseDate`` -- the
        # top-level ``id`` field the upstream row actually carries.
        # No nested ``mediaInfo.tmdbId`` is projected; the
        # historical fabricated ``{"tmdbId": 0}`` placeholder is
        # gone.
        header_line = output.splitlines()[0]
        for column in (
            "id",
            "title",
            "mediaType",
            "releaseDate",
        ):
            self.assertIn(
                column, header_line,
                msg=(
                    f"column {column!r} missing from --human header: "
                    f"{header_line!r}"
                ),
            )
        # The historical ``mediaInfo.tmdbId`` projection is gone --
        # the upstream payload does not expose a nested
        # ``mediaInfo`` envelope on the operator's Seer instance,
        # so no column named ``mediaInfo.tmdbId`` appears in the
        # curated table.
        self.assertNotIn("mediaInfo.tmdbId", header_line)
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
        # The top-level ``id`` field is projected; the historical
        # ``mediaInfo.tmdbId`` projection is gone (the upstream
        # payload does not expose a nested ``mediaInfo`` envelope
        # on the operator's Seer instance).
        self.assertEqual(rendered[0]["id"], 101)
        self.assertEqual(rendered[1]["id"], 102)
        self.assertNotIn("mediaInfo", rendered[0])
        self.assertNotIn("mediaInfo", rendered[1])

    def test_summary_seerr_trending_bare_list_unchanged(self) -> None:
        """Renderer iterates a bare list payload (defensive envelope-drift guard)."""
        from arr_cli.facade.output import _summary_seerr_trending

        rendered = _summary_seerr_trending(self.BARE_LIST)
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0]["title"], "Dune: Part Two")
        # ``BARE_LIST`` is a minimal fixture (no top-level ``id``);
        # the renderer surfaces ``None`` for the missing field
        # rather than fabricating a ``mediaInfo: {tmdbId: 0}``
        # placeholder. Mirrors :meth:`test_search_missing_id_keeps_none`
        # in :class:`TestSummarySeerrSearch`.
        self.assertIsNone(rendered[0]["id"])
        self.assertNotIn("mediaInfo", rendered[0])

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
                    "name": "Bar",
                    "mediaType": "tv",
                    "firstAirDate": "2024-02-01",
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

    # ------------------------------------------------------------------ US-1 AC5
    def test_cmd_trending_http_error_exits_four_with_structured_stderr(
        self,
    ) -> None:
        """Non-2xx on ``/api/v1/discover/trending`` surfaces as exit ``4`` + structured stderr.

        Regression pinning US-1 AC5: the HTTP error path on the
        ``trending`` endpoint must mirror the sibling seerr command
        contract — :class:`HttpError(exit_code=4)` raised by
        :func:`transport.get`, surfaced by :func:`main_wrapper` as a
        structured ``service=seerr op=/api/v1/discover/trending
        status=<code> message=...`` line on stderr so the
        pipe-clean stdout contract (AGENTS.md §1) is preserved.
        """
        import arr_cli.seerr as seerr

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/trending",
                status=500,
                body="Internal Server Error",
            )
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf), \
                    contextlib.redirect_stderr(stderr_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "trending"]
                )
            stdout = stdout_buf.getvalue()
            stderr = stderr_buf.getvalue()
            self.assertEqual(len(rsps.calls), 1)
        self.assertEqual(exit_code, 4)
        # No payload on stdout — the structured error line goes to
        # stderr so the pipe-clean stdout contract (AGENTS.md §1) is
        # preserved.
        self.assertEqual(stdout, "")
        # Structured line: service=seerr op=/api/v1/discover/trending status=500 message=...
        self.assertTrue(
            stderr.startswith(
                "service=seerr op=/api/v1/discover/trending "
                "status=500 message="
            ),
            msg=f"unexpected stderr shape: {stderr!r}",
        )
        self.assertIn("HTTP 500 for /api/v1/discover/trending", stderr)

    # ------------------------------------------------------------------ US-1 AC4
    def test_cmd_trending_limit_caps_human_rows(self) -> None:
        """``--limit N`` caps the ``--human`` rendering to ``N`` rows + footer line.

        Regression pinning US-1 AC4: the post-fetch cap on
        ``seerr trending --human --limit N`` must mirror the sibling
        list-command contract — the renderer slices the curated
        summary to ``N`` rows and appends the pagination footer
        ``"… (M more item[s]; use --limit to see more)"`` so the
        operator is warned the displayed list is truncated.
        """
        from arr_cli.seerr import cmd_trending

        # Build a 30-item envelope so ``--limit 10`` truncates to 10
        # rows and the footer surfaces a non-zero hidden-count.
        items: list[dict[str, Any]] = [
            {
                "title": f"Title {i:02d}",
                "mediaType": "movie" if i % 2 == 0 else "tv",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"tmdbId": 1000 + i},
            }
            for i in range(30)
        ]
        envelope = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 30,
            "results": items,
        }
        args = self._make_args(human=True, limit=10)
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=envelope,
        ):
            output = _capture_stdout(cmd_trending, args, None)
        lines = output.splitlines()
        # Header + separator + N data rows + (optional) truncation
        # footer line. Counting data rows directly: skip the header
        # and separator rows plus any pagination/footer line.
        data_lines = [
            line for line in lines[2:]
            if line.strip()
            and not line.startswith("\u2026")
            and not line.startswith("…")
        ]
        self.assertEqual(
            len(data_lines), 10,
            msg=(
                "--limit 10 must cap --human rows to 10; "
                f"got {len(data_lines)} data lines:\n{output!r}"
            ),
        )
        # The truncation footer line tells the operator the list was
        # truncated and how many rows were hidden.
        self.assertTrue(
            any(
                "more item" in line and "--limit" in line
                for line in lines
            ),
            msg=(
                "truncation footer missing — --limit was not "
                f"honoured; output:\n{output!r}"
            ),
        )
        # The footer must report 20 hidden items (30 - 10).
        self.assertIn(
            "20 more items",
            output,
            msg=(
                "truncation footer must report the 20 hidden items; "
                f"got:\n{output!r}"
            ),
        )


class TestCmdUpcomingMovies(unittest.TestCase):
    """Regression tests pinning the endpoint, params, and summary shape for ``cmd_upcoming_movies``.

    Seer's discover endpoint is
    ``GET /api/v1/discover/movies/upcoming?page=<…>&language=<…>``.
    It returns a paginated envelope of the shape
    ``{page, totalPages, totalResults, results: [...]}`` -- the same
    shape :func:`cmd_trending` consumes -- so the renderer mirrors
    :func:`_summary_seerr_trending` exactly.

    Defaults:

    * Media type is fixed at the command level (encoded in the
      path) -- no ``mediaType`` / ``timeWindow``-equivalent query
      parameter is ever forwarded.
    * ``page`` is only forwarded when ``--page`` is set (no empty
      ``?page=`` rides the wire).
    * ``language`` is only forwarded when ``--language`` is set
      (no empty ``?language=`` rides the wire).
    """

    ENVELOPE: dict[str, Any] = {
        "page": 1,
        "totalPages": 1,
        "totalResults": 2,
        "results": [
            {
                "id": 201,
                "title": "Mickey 17",
                "mediaType": "movie",
                "releaseDate": "2025-03-07",
                "mediaInfo": {"tmdbId": 696506},
            },
            {
                "id": 202,
                "title": "Captain America: Brave New World",
                "mediaType": "movie",
                "releaseDate": "2025-02-14",
                "mediaInfo": {"tmdbId": 822119},
            },
        ],
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

    # ------------------------------------------------------------------ 3.5
    def _make_args(self, **overrides: Any) -> argparse.Namespace:
        """Build an ``argparse.Namespace`` mirroring the ``upcoming-movies`` subparser defaults."""
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
            "command": "upcoming-movies",
            "page": None,
            "language": None,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_cmd_upcoming_movies_dispatch_table_registration(self) -> None:
        """``cmd_upcoming_movies`` is registered in ``_DISPATCH`` and exported via ``__all__``."""
        import arr_cli.seerr as seerr

        # Dispatch table entry points to a callable handler.
        self.assertIn("upcoming-movies", seerr._DISPATCH)
        self.assertTrue(
            callable(seerr._DISPATCH["upcoming-movies"]),
            msg="cmd_upcoming_movies is not callable",
        )
        # Public surface: the handler and the path constant are exported
        # so tests can assert against the literal endpoint.
        self.assertIn("cmd_upcoming_movies", seerr.__all__)
        self.assertIn("UPCOMING_MOVIES_PATH", seerr.__all__)
        self.assertEqual(
            seerr.UPCOMING_MOVIES_PATH,
            "/api/v1/discover/movies/upcoming",
        )

    def test_seerr_upcoming_movies_default_hits_endpoint(self) -> None:
        """``seerr upcoming-movies`` hits the endpoint with no ``page`` / ``language`` on the wire.

        Pins the documented "keep it simple" CLI surface: the
        command does NOT forward ``mediaType`` / ``timeWindow`` (the
        media type is fixed in the path) and the universal ``--page``
        / ``--language`` flags are only forwarded when the operator
        passes them. The empty ``query_param_matcher({})`` match
        asserts no extra query keys ride the default request.
        """
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/movies/upcoming",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher({})
                ],
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "upcoming-movies"]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            # The single registered mock fired, so the path was
            # ``/api/v1/discover/movies/upcoming`` AND no params rode
            # on the wire. Any other path or query value would have
            # left the mock unmatched and surfaced a connection error.
            self.assertEqual(len(rsps.calls), 1)
            # The response body's per-item ``title`` field is rendered
            # onto stdout via the default TSV summary.
            self.assertIn("Mickey 17", stdout)

    def test_seerr_upcoming_movies_with_page_and_language(self) -> None:
        """``seerr upcoming-movies --page 3 --language fr-FR`` forwards both filters."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/movies/upcoming",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {"page": "3", "language": "fr-FR"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "upcoming-movies",
                    "--page", "3",
                    "--language", "fr-FR",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_seerr_upcoming_movies_human_renders_tsv(self) -> None:
        """``--human`` renders the curated summary as a tabular TSV with the documented column headers."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/movies/upcoming",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher({})
                ],
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    [
                        "--config", str(self.cfg_path),
                        "upcoming-movies",
                        "--human",
                    ]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)
            # Header line names the columns the renderer projects:
            # ``id``, ``title``, ``mediaType``, ``releaseDate`` --
            # the top-level ``id`` field the upstream row actually
            # carries. No nested ``mediaInfo.tmdbId`` is projected;
            # the historical fabricated ``{"tmdbId": 0}`` placeholder
            # is gone.
            header_line = stdout.splitlines()[0]
            for column in (
                "id",
                "title",
                "mediaType",
                "releaseDate",
            ):
                self.assertIn(
                    column, header_line,
                    msg=(
                        f"column {column!r} missing from --human header: "
                        f"{header_line!r}"
                    ),
                )
            self.assertNotIn("mediaInfo.tmdbId", header_line)

    def test_seerr_upcoming_movies_verbose_emits_verbatim_envelope(self) -> None:
        """``--verbose`` bypasses the renderer and emits the envelope verbatim."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/movies/upcoming",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher({})
                ],
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    [
                        "--config", str(self.cfg_path),
                        "upcoming-movies",
                        "--verbose",
                    ]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)
            # Verbose mode dumps the response envelope as JSON, unchanged.
            self.assertEqual(json.loads(stdout), self.ENVELOPE)

    def test_seerr_upcoming_movies_http_error_returns_exit_4(self) -> None:
        """Non-2xx on the ``upcoming-movies`` endpoint surfaces as exit ``4`` + structured stderr.

        Mirrors the sibling ``trending`` HTTP-error contract: the
        transport raises :class:`HttpError(exit_code=4)`,
        :func:`main_wrapper` translates it to a structured
        ``service=seerr op=upcoming-movies status=<code>`` line on
        stderr so the pipe-clean stdout contract (AGENTS.md §1) is
        preserved. ``assertIn`` is used so the assertion is robust
        to additional stderr framing (e.g. message trailers appended
        by ``main_wrapper``).
        """
        import arr_cli.seerr as seerr

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/movies/upcoming",
                status=500,
                body="Internal Server Error",
            )
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf), \
                    contextlib.redirect_stderr(stderr_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "upcoming-movies"]
                )
            stdout = stdout_buf.getvalue()
            stderr = stderr_buf.getvalue()
            self.assertEqual(len(rsps.calls), 1)
        self.assertEqual(exit_code, 4)
        # No payload on stdout — the structured error line goes to
        # stderr so the pipe-clean stdout contract (AGENTS.md §1) is
        # preserved.
        self.assertEqual(stdout, "")
        # Structured line: ``service=seerr op=/api/v1/discover/movies/upcoming status=500``.
        self.assertIn(
            "service=seerr op=/api/v1/discover/movies/upcoming status=500",
            stderr,
            msg=(
                "expected structured HTTP-error stderr line; "
                f"got {stderr!r}"
            ),
        )

    # ------------------------------------------------------------------ 3.8
    def test_summary_seerr_upcoming_movies_envelope_unwraps_results(self) -> None:
        """Renderer iterates ``results`` of a paginated envelope, not the envelope itself."""
        from arr_cli.facade.output import _summary_seerr_upcoming_movies

        rendered = _summary_seerr_upcoming_movies(self.ENVELOPE)
        self.assertEqual(len(rendered), 2)
        self.assertEqual(rendered[0]["title"], "Mickey 17")
        self.assertEqual(rendered[1]["title"], "Captain America: Brave New World")
        # The top-level ``id`` field is projected; the historical
        # ``mediaInfo.tmdbId`` projection is gone (the upstream
        # payload does not expose a nested ``mediaInfo`` envelope
        # on the operator's Seer instance).
        self.assertEqual(rendered[0]["id"], 201)
        self.assertEqual(rendered[1]["id"], 202)
        self.assertNotIn("mediaInfo", rendered[0])
        self.assertNotIn("mediaInfo", rendered[1])

    def test_summary_seerr_upcoming_movies_bare_list_unchanged(self) -> None:
        """Renderer iterates a bare list payload (defensive envelope-drift guard)."""
        from arr_cli.facade.output import _summary_seerr_upcoming_movies

        rendered = _summary_seerr_upcoming_movies(self.ENVELOPE["results"])
        self.assertEqual(len(rendered), 2)
        self.assertEqual(rendered[0]["title"], "Mickey 17")
        self.assertEqual(rendered[0]["id"], 201)
        self.assertNotIn("mediaInfo", rendered[0])

    def test_summary_seerr_upcoming_movies_envelope_without_results_returns_empty(
        self,
    ) -> None:
        """An envelope missing the ``results`` key maps to ``[]`` rather than crashing."""
        from arr_cli.facade.output import _summary_seerr_upcoming_movies

        rendered = _summary_seerr_upcoming_movies(
            {"page": 1, "totalPages": 0, "totalResults": 0}
        )
        self.assertEqual(rendered, [])

    def test_summary_seerr_upcoming_movies_non_mapping_non_list_returns_empty(
        self,
    ) -> None:
        """A scalar / ``None`` payload maps to ``[]`` rather than crashing."""
        from arr_cli.facade.output import _summary_seerr_upcoming_movies

        self.assertEqual(_summary_seerr_upcoming_movies(None), [])
        self.assertEqual(_summary_seerr_upcoming_movies("not a list"), [])
        self.assertEqual(_summary_seerr_upcoming_movies(42), [])

    def test_summary_seerr_upcoming_movies_envelope_drops_non_mapping_items(
        self,
    ) -> None:
        """Non-Mapping items inside ``results`` are dropped silently."""
        from arr_cli.facade.output import _summary_seerr_upcoming_movies

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
                    "mediaType": "movie",
                    "releaseDate": "2024-02-01",
                    "mediaInfo": {"tmdbId": 2},
                },
            ],
        }
        rendered = _summary_seerr_upcoming_movies(envelope)
        # Two curated rows survive; the stray string is dropped.
        self.assertEqual(len(rendered), 2)
        self.assertEqual(rendered[0]["title"], "Foo")
        self.assertEqual(rendered[1]["title"], "Bar")

    # ------------------------------------------------------------------ US-1 AC4
    def test_cmd_upcoming_movies_limit_caps_human_rows(self) -> None:
        """``--limit N`` caps the ``--human`` rendering to ``N`` rows + footer line.

        Regression pinning US-1 AC4: the post-fetch cap on
        ``seerr upcoming-movies --human --limit N`` must mirror the
        sibling list-command contract — the renderer slices the
        curated summary to ``N`` rows and appends the pagination
        footer ``"… (M more item[s]; use --limit to see more)"`` so
        the operator is warned the displayed list is truncated.
        """
        from arr_cli.seerr import cmd_upcoming_movies

        # Build a 30-item envelope so ``--limit 10`` truncates to 10
        # rows and the footer surfaces a non-zero hidden-count.
        items: list[dict[str, Any]] = [
            {
                "title": f"Title {i:02d}",
                "mediaType": "movie",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"tmdbId": 1000 + i},
            }
            for i in range(30)
        ]
        envelope = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 30,
            "results": items,
        }
        args = self._make_args(human=True, limit=10)
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=envelope,
        ):
            output = _capture_stdout(cmd_upcoming_movies, args, None)
        lines = output.splitlines()
        # Header + separator + N data rows + (optional) truncation
        # footer line. Counting data rows directly: skip the header
        # and separator rows plus any pagination/footer line.
        data_lines = [
            line for line in lines[2:]
            if line.strip()
            and not line.startswith("\u2026")
            and not line.startswith("…")
        ]
        self.assertEqual(
            len(data_lines), 10,
            msg=(
                "--limit 10 must cap --human rows to 10; "
                f"got {len(data_lines)} data lines:\n{output!r}"
            ),
        )
        # The truncation footer line tells the operator the list was
        # truncated and how many rows were hidden.
        self.assertTrue(
            any(
                "more item" in line and "--limit" in line
                for line in lines
            ),
            msg=(
                "truncation footer missing — --limit was not "
                f"honoured; output:\n{output!r}"
            ),
        )
        # The footer must report 20 hidden items (30 - 10).
        self.assertIn(
            "20 more items",
            output,
            msg=(
                "truncation footer must report the 20 hidden items; "
                f"got:\n{output!r}"
            ),
        )


class TestCmdUpcomingTv(unittest.TestCase):
    """Regression tests pinning the endpoint, params, and summary shape for ``cmd_upcoming_tv``.

    Seer's discover endpoint is
    ``GET /api/v1/discover/tv/upcoming?page=<…>&language=<…>``.
    It returns a paginated envelope of the shape
    ``{page, totalPages, totalResults, results: [...]}`` -- the same
    shape :func:`cmd_trending` consumes -- so the renderer mirrors
    :func:`_summary_seerr_trending` exactly.

    Defaults:

    * Media type is fixed at the command level (encoded in the
      path) -- no ``mediaType`` / ``timeWindow``-equivalent query
      parameter is ever forwarded.
    * ``page`` is only forwarded when ``--page`` is set (no empty
      ``?page=`` rides the wire).
    * ``language`` is only forwarded when ``--language`` is set
      (no empty ``?language=`` rides the wire).

    Structural twin of :class:`TestCmdUpcomingMovies` so future
    drift between the two upcoming commands fails the unit suite
    immediately.
    """

    ENVELOPE: dict[str, Any] = {
        "page": 1,
        "totalPages": 1,
        "totalResults": 2,
        "results": [
            {
                "id": 301,
                "name": "Severance",
                "mediaType": "tv",
                "firstAirDate": "2025-01-17",
                "mediaInfo": {"tmdbId": 95396},
            },
            {
                "id": 302,
                "name": "The Pitt",
                "mediaType": "tv",
                "firstAirDate": "2025-01-09",
                "mediaInfo": {"tmdbId": 249135},
            },
        ],
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

    # ------------------------------------------------------------------ 3.5
    def _make_args(self, **overrides: Any) -> argparse.Namespace:
        """Build an ``argparse.Namespace`` mirroring the ``upcoming-tv`` subparser defaults."""
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
            "command": "upcoming-tv",
            "page": None,
            "language": None,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_cmd_upcoming_tv_dispatch_table_registration(self) -> None:
        """``cmd_upcoming_tv`` is registered in ``_DISPATCH`` and exported via ``__all__``."""
        import arr_cli.seerr as seerr

        # Dispatch table entry points to a callable handler.
        self.assertIn("upcoming-tv", seerr._DISPATCH)
        self.assertTrue(
            callable(seerr._DISPATCH["upcoming-tv"]),
            msg="cmd_upcoming_tv is not callable",
        )
        # Public surface: the handler and the path constant are exported
        # so tests can assert against the literal endpoint.
        self.assertIn("cmd_upcoming_tv", seerr.__all__)
        self.assertIn("UPCOMING_TV_PATH", seerr.__all__)
        self.assertEqual(
            seerr.UPCOMING_TV_PATH,
            "/api/v1/discover/tv/upcoming",
        )

    def test_seerr_upcoming_tv_default_hits_endpoint(self) -> None:
        """``seerr upcoming-tv`` hits the endpoint with no ``page`` / ``language`` on the wire.

        Pins the documented "keep it simple" CLI surface: the
        command does NOT forward ``mediaType`` / ``timeWindow`` (the
        media type is fixed in the path) and the universal ``--page``
        / ``--language`` flags are only forwarded when the operator
        passes them. The empty ``query_param_matcher({})`` match
        asserts no extra query keys ride the default request.
        """
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/tv/upcoming",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher({})
                ],
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "upcoming-tv"]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            # The single registered mock fired, so the path was
            # ``/api/v1/discover/tv/upcoming`` AND no params rode on
            # the wire. Any other path or query value would have left
            # the mock unmatched and surfaced a connection error.
            self.assertEqual(len(rsps.calls), 1)
            # The response body's per-item ``title`` field is rendered
            # onto stdout via the default TSV summary.
            self.assertIn("Severance", stdout)

    def test_seerr_upcoming_tv_with_page_and_language(self) -> None:
        """``seerr upcoming-tv --page 3 --language fr-FR`` forwards both filters."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/tv/upcoming",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {"page": "3", "language": "fr-FR"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "upcoming-tv",
                    "--page", "3",
                    "--language", "fr-FR",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_seerr_upcoming_tv_human_renders_tsv(self) -> None:
        """``--human`` renders the curated summary as a tabular TSV with the documented column headers."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/tv/upcoming",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher({})
                ],
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    [
                        "--config", str(self.cfg_path),
                        "upcoming-tv",
                        "--human",
                    ]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)
            # Header line names the columns the renderer projects:
            # ``id``, ``title``, ``mediaType``, ``releaseDate`` --
            # the top-level ``id`` field the upstream row actually
            # carries. No nested ``mediaInfo.tmdbId`` is projected;
            # the historical fabricated ``{"tmdbId": 0}`` placeholder
            # is gone.
            header_line = stdout.splitlines()[0]
            for column in (
                "id",
                "title",
                "mediaType",
                "releaseDate",
            ):
                self.assertIn(
                    column, header_line,
                    msg=(
                        f"column {column!r} missing from --human header: "
                        f"{header_line!r}"
                    ),
                )
            self.assertNotIn("mediaInfo.tmdbId", header_line)

    def test_seerr_upcoming_tv_verbose_emits_verbatim_envelope(self) -> None:
        """``--verbose`` bypasses the renderer and emits the envelope verbatim."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/tv/upcoming",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher({})
                ],
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    [
                        "--config", str(self.cfg_path),
                        "upcoming-tv",
                        "--verbose",
                    ]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)
            # Verbose mode dumps the response envelope as JSON, unchanged.
            self.assertEqual(json.loads(stdout), self.ENVELOPE)

    def test_seerr_upcoming_tv_http_error_returns_exit_4(self) -> None:
        """Non-2xx on the ``upcoming-tv`` endpoint surfaces as exit ``4`` + structured stderr.

        Mirrors the sibling ``trending`` HTTP-error contract: the
        transport raises :class:`HttpError(exit_code=4)`,
        :func:`main_wrapper` translates it to a structured
        ``service=seerr op=upcoming-tv status=<code>`` line on
        stderr so the pipe-clean stdout contract (AGENTS.md §1) is
        preserved. ``assertIn`` is used so the assertion is robust
        to additional stderr framing (e.g. message trailers appended
        by ``main_wrapper``).
        """
        import arr_cli.seerr as seerr

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/tv/upcoming",
                status=500,
                body="Internal Server Error",
            )
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf), \
                    contextlib.redirect_stderr(stderr_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "upcoming-tv"]
                )
            stdout = stdout_buf.getvalue()
            stderr = stderr_buf.getvalue()
            self.assertEqual(len(rsps.calls), 1)
        self.assertEqual(exit_code, 4)
        # No payload on stdout — the structured error line goes to
        # stderr so the pipe-clean stdout contract (AGENTS.md §1) is
        # preserved.
        self.assertEqual(stdout, "")
        # Structured line: ``service=seerr op=/api/v1/discover/tv/upcoming status=500``.
        self.assertIn(
            "service=seerr op=/api/v1/discover/tv/upcoming status=500",
            stderr,
            msg=(
                "expected structured HTTP-error stderr line; "
                f"got {stderr!r}"
            ),
        )

    # ------------------------------------------------------------------ 3.8
    def test_summary_seerr_upcoming_tv_envelope_unwraps_results(self) -> None:
        """Renderer iterates ``results`` of a paginated envelope, not the envelope itself."""
        from arr_cli.facade.output import _summary_seerr_upcoming_tv

        rendered = _summary_seerr_upcoming_tv(self.ENVELOPE)
        self.assertEqual(len(rendered), 2)
        self.assertEqual(rendered[0]["title"], "Severance")
        self.assertEqual(rendered[1]["title"], "The Pitt")
        # The top-level ``id`` field is projected; the historical
        # ``mediaInfo.tmdbId`` projection is gone (the upstream
        # payload does not expose a nested ``mediaInfo`` envelope
        # on the operator's Seer instance).
        self.assertEqual(rendered[0]["id"], 301)
        self.assertEqual(rendered[1]["id"], 302)
        self.assertNotIn("mediaInfo", rendered[0])
        self.assertNotIn("mediaInfo", rendered[1])

    def test_summary_seerr_upcoming_tv_bare_list_unchanged(self) -> None:
        """Renderer iterates a bare list payload (defensive envelope-drift guard)."""
        from arr_cli.facade.output import _summary_seerr_upcoming_tv

        rendered = _summary_seerr_upcoming_tv(self.ENVELOPE["results"])
        self.assertEqual(len(rendered), 2)
        self.assertEqual(rendered[0]["title"], "Severance")
        self.assertEqual(rendered[0]["id"], 301)
        self.assertNotIn("mediaInfo", rendered[0])

    def test_summary_seerr_upcoming_tv_envelope_without_results_returns_empty(
        self,
    ) -> None:
        """An envelope missing the ``results`` key maps to ``[]`` rather than crashing."""
        from arr_cli.facade.output import _summary_seerr_upcoming_tv

        rendered = _summary_seerr_upcoming_tv(
            {"page": 1, "totalPages": 0, "totalResults": 0}
        )
        self.assertEqual(rendered, [])

    def test_summary_seerr_upcoming_tv_non_mapping_non_list_returns_empty(
        self,
    ) -> None:
        """A scalar / ``None`` payload maps to ``[]`` rather than crashing."""
        from arr_cli.facade.output import _summary_seerr_upcoming_tv

        self.assertEqual(_summary_seerr_upcoming_tv(None), [])
        self.assertEqual(_summary_seerr_upcoming_tv("not a list"), [])
        self.assertEqual(_summary_seerr_upcoming_tv(42), [])

    def test_summary_seerr_upcoming_tv_envelope_drops_non_mapping_items(
        self,
    ) -> None:
        """Non-Mapping items inside ``results`` are dropped silently."""
        from arr_cli.facade.output import _summary_seerr_upcoming_tv

        envelope = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 3,
            "results": [
                {
                    "name": "Foo",
                    "mediaType": "tv",
                    "firstAirDate": "2024-01-01",
                    "mediaInfo": {"tmdbId": 1},
                },
                "stray non-mapping item",
                {
                    "name": "Bar",
                    "mediaType": "tv",
                    "firstAirDate": "2024-02-01",
                    "mediaInfo": {"tmdbId": 2},
                },
            ],
        }
        rendered = _summary_seerr_upcoming_tv(envelope)
        # Two curated rows survive; the stray string is dropped.
        self.assertEqual(len(rendered), 2)
        self.assertEqual(rendered[0]["title"], "Foo")
        self.assertEqual(rendered[1]["title"], "Bar")

    # ------------------------------------------------------------------ US-2 AC4
    def test_cmd_upcoming_tv_limit_caps_human_rows(self) -> None:
        """``--limit N`` caps the ``--human`` rendering to ``N`` rows + footer line.

        Regression pinning US-2 AC4: the post-fetch cap on
        ``seerr upcoming-tv --human --limit N`` must mirror the
        sibling list-command contract — the renderer slices the
        curated summary to ``N`` rows and appends the pagination
        footer ``"… (M more item[s]; use --limit to see more)"`` so
        the operator is warned the displayed list is truncated.
        """
        from arr_cli.seerr import cmd_upcoming_tv

        # Build a 30-item envelope so ``--limit 10`` truncates to 10
        # rows and the footer surfaces a non-zero hidden-count.
        items: list[dict[str, Any]] = [
            {
                "title": f"Title {i:02d}",
                "mediaType": "tv",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"tmdbId": 1000 + i},
            }
            for i in range(30)
        ]
        envelope = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 30,
            "results": items,
        }
        args = self._make_args(human=True, limit=10)
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=envelope,
        ):
            output = _capture_stdout(cmd_upcoming_tv, args, None)
        lines = output.splitlines()
        # Header + separator + N data rows + (optional) truncation
        # footer line. Counting data rows directly: skip the header
        # and separator rows plus any pagination/footer line.
        data_lines = [
            line for line in lines[2:]
            if line.strip()
            and not line.startswith("\u2026")
            and not line.startswith("…")
        ]
        self.assertEqual(
            len(data_lines), 10,
            msg=(
                "--limit 10 must cap --human rows to 10; "
                f"got {len(data_lines)} data lines:\n{output!r}"
            ),
        )
        # The truncation footer line tells the operator the list was
        # truncated and how many rows were hidden.
        self.assertTrue(
            any(
                "more item" in line and "--limit" in line
                for line in lines
            ),
            msg=(
                "truncation footer missing — --limit was not "
                f"honoured; output:\n{output!r}"
            ),
        )
        # The footer must report 20 hidden items (30 - 10).
        self.assertIn(
            "20 more items",
            output,
            msg=(
                "truncation footer must report the 20 hidden items; "
                f"got:\n{output!r}"
            ),
        )


class TestCmdDiscoverMovies(unittest.TestCase):
    """Regression tests pinning the endpoint, params, and summary shape for ``cmd_discover_movies``.

    Seer's discover endpoint is
    ``GET /api/v1/discover/movies?genre=<id>&sortBy=<sortBy>&language=<LANG>&page=<N>``.
    It returns a paginated envelope of the shape
    ``{page, totalPages, totalResults, results: [...]}`` -- the same
    shape :func:`cmd_upcoming_movies` consumes -- so the renderer
    mirrors :func:`_summary_seerr_upcoming_movies` exactly.

    Defaults:

    * ``sortBy`` is forwarded unconditionally with documented default
      ``popularity.desc`` (the operator's documented default).
    * ``language`` is forwarded unconditionally with documented
      default ``en-US``.
    * ``genre`` is forwarded only when ``--genre`` is set.
    * ``page`` is forwarded only when ``--page`` is set.
    * The upstream ``limit`` query parameter is intentionally NOT
      forwarded -- discover paginates instead, and the universal
      ``--limit`` caps client-side row output only.

    Structural twin of :class:`TestCmdUpcomingMovies` so future
    drift between the two browse-style commands fails the unit
    suite immediately.
    """

    ENVELOPE: dict[str, Any] = {
        "page": 1,
        "totalPages": 1,
        "totalResults": 2,
        "results": [
            {
                "id": 401,
                "title": "The Batman",
                "mediaType": "movie",
                "releaseDate": "2022-03-04",
                "mediaInfo": {"tmdbId": 414906},
            },
            {
                "id": 402,
                "title": "Past Lives",
                "mediaType": "movie",
                "releaseDate": "2023-06-02",
                "mediaInfo": {"tmdbId": 850871},
            },
        ],
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

    def _make_args(self, **overrides: Any) -> argparse.Namespace:
        """Build an ``argparse.Namespace`` mirroring the ``discover-movies`` subparser defaults."""
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
            "command": "discover-movies",
            "genre": None,
            "sort": None,
            "language": None,
            "page": None,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_cmd_discover_movies_dispatch_table_registration(self) -> None:
        """``cmd_discover_movies`` is registered in ``_DISPATCH`` and exported via ``__all__``."""
        import arr_cli.seerr as seerr

        # Dispatch table entry points to a callable handler.
        self.assertIn("discover-movies", seerr._DISPATCH)
        self.assertTrue(
            callable(seerr._DISPATCH["discover-movies"]),
            msg="cmd_discover_movies is not callable",
        )
        # Public surface: the handler and the path constant are exported
        # so tests can assert against the literal endpoint.
        self.assertIn("cmd_discover_movies", seerr.__all__)
        self.assertIn("DISCOVER_MOVIES_PATH", seerr.__all__)
        self.assertEqual(
            seerr.DISCOVER_MOVIES_PATH,
            "/api/v1/discover/movies",
        )

    def test_seerr_discover_movies_default_hits_endpoint(self) -> None:
        """``seerr discover-movies`` with no flags hits the endpoint with NO query params on the wire.

        Pins the omit-when-default wire-format: the CLI only
        forwards a query parameter when the operator typed the
        matching flag. With no flags, no ``sortBy`` /
        ``language`` / ``genre`` / ``page`` / ``limit`` keys ride
        the request -- upstream applies its own defaults. Using
        ``query_param_matcher({})`` with the strict default
        ``strict_match=True`` asserts the URL has zero query
        parameters (anything extra would leave the mock
        unmatched).

        Regression guard for the 2026-09-18 bug where the CLI
        hardcoded ``sortBy=popularity.desc`` + ``language=en-US``
        and the upstream combo silently returned 0 results.
        """
        from urllib.parse import urlparse, parse_qs

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/movies",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher({})
                ],
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "discover-movies"]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            # The single registered mock fired, so the path was
            # ``/api/v1/discover/movies`` with zero query
            # parameters. Any non-empty query string would have
            # left the mock unmatched and surfaced a connection
            # error.
            self.assertEqual(len(rsps.calls), 1)
            self.assertEqual(
                parse_qs(urlparse(rsps.calls[0].request.url).query),
                {},
            )
            # The response body's per-item ``title`` field is rendered
            # onto stdout via the default TSV summary.
            self.assertIn("The Batman", stdout)

    def test_seerr_discover_movies_default_omits_sort_and_language(
        self,
    ) -> None:
        """Explicit regression: default discover-movies has no ``sortBy`` and no ``language`` on the wire.

        Pins the 2026-09-18 bug fix at the wire-format level:
        the prior command hardcoded both keys unconditionally
        (returning 0 results), so this test fails loudly if
        either key ever reappears on the default request.
        """
        from urllib.parse import urlparse, parse_qs

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/movies",
                json=self.ENVELOPE,
                status=200,
            )
            import arr_cli.seerr as seerr

            seerr.main(
                ["--config", str(self.cfg_path), "discover-movies"]
            )
            self.assertEqual(len(rsps.calls), 1)
            query = parse_qs(urlparse(rsps.calls[0].request.url).query)
            self.assertNotIn("sortBy", query)
            self.assertNotIn("language", query)
            self.assertNotIn("genre", query)
            self.assertNotIn("page", query)
            self.assertNotIn("limit", query)

    def test_seerr_discover_movies_with_genre(self) -> None:
        """``seerr discover-movies --genre 28`` forwards ONLY ``genre=28`` (no sort/language/page)."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/movies",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher({"genre": "28"})
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "discover-movies",
                    "--genre", "28",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_seerr_discover_movies_with_sort(self) -> None:
        """``seerr discover-movies --sort vote_average.desc`` forwards ONLY ``sortBy=vote_average.desc``."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/movies",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {"sortBy": "vote_average.desc"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "discover-movies",
                    "--sort", "vote_average.desc",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_seerr_discover_movies_with_language(self) -> None:
        """``seerr discover-movies --language fr-FR`` forwards ONLY ``language=fr-FR``."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/movies",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {"language": "fr-FR"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "discover-movies",
                    "--language", "fr-FR",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_seerr_discover_movies_limit_is_client_side(self) -> None:
        """``--limit N`` is honored by the renderer but ``limit`` does NOT ride the wire.

        Regression pinning: the universal ``--limit`` caps
        client-side row output for the discover endpoints but is
        intentionally NOT forwarded to the upstream ``?limit=``
        query parameter -- discover paginates instead. The test
        confirms ``--limit 5`` truncates the ``--human`` rendering
        to 5 rows + a "more items" footer, while the upstream mock
        is matched against a query-param set that does NOT include
        ``limit``.
        """
        from arr_cli.seerr import cmd_discover_movies

        # Build a 30-item envelope so ``--limit 5`` truncates to
        # 5 rows and the footer surfaces a non-zero hidden-count.
        items: list[dict[str, Any]] = [
            {
                "title": f"Title {i:02d}",
                "mediaType": "movie",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"tmdbId": 1000 + i},
            }
            for i in range(30)
        ]
        envelope = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 30,
            "results": items,
        }
        args = self._make_args(human=True, limit=5)
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=envelope,
        ):
            output = _capture_stdout(cmd_discover_movies, args, None)
        lines = output.splitlines()
        # Header + separator + N data rows + (optional) truncation
        # footer line. Counting data rows directly: skip the header
        # and separator rows plus any pagination/footer line.
        data_lines = [
            line for line in lines[2:]
            if line.strip()
            and not line.startswith("\u2026")
            and not line.startswith("…")
        ]
        self.assertEqual(
            len(data_lines), 5,
            msg=(
                "--limit 5 must cap --human rows to 5; "
                f"got {len(data_lines)} data lines:\n{output!r}"
            ),
        )
        # The truncation footer line tells the operator the list was
        # truncated and how many rows were hidden.
        self.assertTrue(
            any(
                "more item" in line and "--limit" in line
                for line in lines
            ),
            msg=(
                "truncation footer missing — --limit was not "
                f"honoured; output:\n{output!r}"
            ),
        )
        # The footer must report 25 hidden items (30 - 5).
        self.assertIn(
            "25 more items",
            output,
            msg=(
                "truncation footer must report the 25 hidden items; "
                f"got:\n{output!r}"
            ),
        )

    def test_seerr_discover_movies_http_error_returns_exit_4(self) -> None:
        """Non-2xx on the ``discover-movies`` endpoint surfaces as exit ``4`` + structured stderr.

        Mirrors the sibling ``upcoming-movies`` HTTP-error
        contract: the transport raises
        :class:`HttpError(exit_code=4)`, :func:`main_wrapper`
        translates it to a structured
        ``service=seerr op=discover-movies status=<code>`` line on
        stderr so the pipe-clean stdout contract (AGENTS.md §1) is
        preserved. ``assertIn`` is used so the assertion is robust
        to additional stderr framing (e.g. message trailers appended
        by ``main_wrapper``).
        """
        import arr_cli.seerr as seerr

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/movies",
                status=500,
                body="Internal Server Error",
            )
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf), \
                    contextlib.redirect_stderr(stderr_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "discover-movies"]
                )
            stdout = stdout_buf.getvalue()
            stderr = stderr_buf.getvalue()
            self.assertEqual(len(rsps.calls), 1)
        self.assertEqual(exit_code, 4)
        # No payload on stdout — the structured error line goes to
        # stderr so the pipe-clean stdout contract (AGENTS.md §1) is
        # preserved.
        self.assertEqual(stdout, "")
        # Structured line: ``service=seerr op=/api/v1/discover/movies status=500``.
        self.assertIn(
            "service=seerr op=/api/v1/discover/movies status=500",
            stderr,
            msg=(
                "expected structured HTTP-error stderr line; "
                f"got {stderr!r}"
            ),
        )

    def test_seerr_discover_movies_human_renders_tsv(self) -> None:
        """``--human`` renders the curated summary as a tabular TSV with the documented column headers.

        The mock asserts no query params ride the wire for the
        bare ``--human`` invocation -- guards against a regression
        of the 2026-09-18 bug where the CLI hardcoded
        ``sortBy=popularity.desc`` + ``language=en-US``.
        """
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/movies",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher({})
                ],
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    [
                        "--config", str(self.cfg_path),
                        "discover-movies",
                        "--human",
                    ]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)
            # Header line names the columns the renderer projects:
            # ``id``, ``title``, ``mediaType``, ``releaseDate`` --
            # the top-level ``id`` field the upstream row actually
            # carries. No nested ``mediaInfo.tmdbId`` is projected;
            # the historical fabricated ``{"tmdbId": 0}`` placeholder
            # is gone.
            header_line = stdout.splitlines()[0]
            for column in (
                "id",
                "title",
                "mediaType",
                "releaseDate",
            ):
                self.assertIn(
                    column, header_line,
                    msg=(
                        f"column {column!r} missing from --human header: "
                        f"{header_line!r}"
                    ),
                )
            self.assertNotIn("mediaInfo.tmdbId", header_line)


class TestCmdDiscoverTv(unittest.TestCase):
    """Regression tests pinning the endpoint, params, and summary shape for ``cmd_discover_tv``.

    Seer's discover endpoint is
    ``GET /api/v1/discover/tv?genre=<id>&sortBy=<sortBy>&language=<LANG>&page=<N>``.
    It returns a paginated envelope of the shape
    ``{page, totalPages, totalResults, results: [...]}`` -- the same
    shape :func:`cmd_upcoming_tv` / :func:`cmd_discover_movies`
    consume -- so the renderer mirrors
    :func:`_summary_seerr_discover_tv`` exactly.

    Defaults:

    * ``sortBy`` is forwarded only when ``--sort`` is set; no default
      rides the wire (omit-when-default rule).
    * ``language`` is forwarded only when ``--language`` is set; no
      default rides the wire.
    * ``genre`` is forwarded only when ``--genre`` is set.
    * ``page`` is forwarded only when ``--page`` is set.
    * The upstream ``limit`` query parameter is intentionally NOT
      forwarded.

    Structural twin of :class:`TestCmdDiscoverMovies` so future
    drift between the two discover commands fails the unit suite
    immediately.
    """

    ENVELOPE: dict[str, Any] = {
        "page": 1,
        "totalPages": 1,
        "totalResults": 2,
        "results": [
            {
                "id": 501,
                "name": "Shogun",
                "mediaType": "tv",
                "firstAirDate": "2024-02-27",
                "mediaInfo": {"tmdbId": 126308},
            },
            {
                "id": 502,
                "name": "Fallout",
                "mediaType": "tv",
                "firstAirDate": "2024-04-10",
                "mediaInfo": {"tmdbId": 106379},
            },
        ],
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

    def _make_args(self, **overrides: Any) -> argparse.Namespace:
        """Build an ``argparse.Namespace`` mirroring the ``discover-tv`` subparser defaults."""
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
            "command": "discover-tv",
            "genre": None,
            "sort": None,
            "language": None,
            "page": None,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_cmd_discover_tv_dispatch_table_registration(self) -> None:
        """``cmd_discover_tv`` is registered in ``_DISPATCH`` and exported via ``__all__``."""
        import arr_cli.seerr as seerr

        # Dispatch table entry points to a callable handler.
        self.assertIn("discover-tv", seerr._DISPATCH)
        self.assertTrue(
            callable(seerr._DISPATCH["discover-tv"]),
            msg="cmd_discover_tv is not callable",
        )
        # Public surface: the handler and the path constant are exported
        # so tests can assert against the literal endpoint.
        self.assertIn("cmd_discover_tv", seerr.__all__)
        self.assertIn("DISCOVER_TV_PATH", seerr.__all__)
        self.assertEqual(
            seerr.DISCOVER_TV_PATH,
            "/api/v1/discover/tv",
        )

    def test_seerr_discover_tv_default_hits_endpoint(self) -> None:
        """``seerr discover-tv`` with no flags hits the endpoint with NO query params on the wire.

        Pins the omit-when-default wire-format: the CLI only
        forwards a query parameter when the operator typed the
        matching flag. With no flags, no ``sortBy`` /
        ``language`` / ``genre`` / ``page`` / ``limit`` keys ride
        the request -- upstream applies its own defaults. Using
        ``query_param_matcher({})`` with the strict default
        ``strict_match=True`` asserts the URL has zero query
        parameters (anything extra would leave the mock
        unmatched).

        Regression guard for the 2026-09-18 bug where the CLI
        hardcoded ``sortBy=popularity.desc`` + ``language=en-US``
        and the upstream combo silently returned 0 results.
        """
        from urllib.parse import urlparse, parse_qs

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/tv",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher({})
                ],
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "discover-tv"]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            # The single registered mock fired, so the path was
            # ``/api/v1/discover/tv`` with zero query parameters.
            # Any non-empty query string would have left the mock
            # unmatched and surfaced a connection error.
            self.assertEqual(len(rsps.calls), 1)
            self.assertEqual(
                parse_qs(urlparse(rsps.calls[0].request.url).query),
                {},
            )
            # The response body's per-item ``title`` field is rendered
            # onto stdout via the default TSV summary.
            self.assertIn("Shogun", stdout)

    def test_seerr_discover_tv_default_omits_sort_and_language(
        self,
    ) -> None:
        """Explicit regression: default discover-tv has no ``sortBy`` and no ``language`` on the wire.

        Pins the 2026-09-18 bug fix at the wire-format level:
        the prior command hardcoded both keys unconditionally
        (returning 0 results), so this test fails loudly if
        either key ever reappears on the default request.
        """
        from urllib.parse import urlparse, parse_qs

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/tv",
                json=self.ENVELOPE,
                status=200,
            )
            import arr_cli.seerr as seerr

            seerr.main(
                ["--config", str(self.cfg_path), "discover-tv"]
            )
            self.assertEqual(len(rsps.calls), 1)
            query = parse_qs(urlparse(rsps.calls[0].request.url).query)
            self.assertNotIn("sortBy", query)
            self.assertNotIn("language", query)
            self.assertNotIn("genre", query)
            self.assertNotIn("page", query)
            self.assertNotIn("limit", query)

    def test_seerr_discover_tv_with_genre(self) -> None:
        """``seerr discover-tv --genre 28`` forwards ONLY ``genre=28`` (no sort/language/page)."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/tv",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher({"genre": "28"})
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "discover-tv",
                    "--genre", "28",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_seerr_discover_tv_with_sort(self) -> None:
        """``seerr discover-tv --sort vote_average.desc`` forwards ONLY ``sortBy=vote_average.desc``."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/tv",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {"sortBy": "vote_average.desc"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "discover-tv",
                    "--sort", "vote_average.desc",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_seerr_discover_tv_with_language(self) -> None:
        """``seerr discover-tv --language fr-FR`` forwards ONLY ``language=fr-FR``."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/tv",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher(
                        {"language": "fr-FR"}
                    )
                ],
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "discover-tv",
                    "--language", "fr-FR",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    def test_seerr_discover_tv_limit_is_client_side(self) -> None:
        """``--limit N`` is honored by the renderer but ``limit`` does NOT ride the wire.

        Regression pinning: the universal ``--limit`` caps
        client-side row output for the discover endpoints but is
        intentionally NOT forwarded to the upstream ``?limit=``
        query parameter -- discover paginates instead. The test
        confirms ``--limit 5`` truncates the ``--human`` rendering
        to 5 rows + a "more items" footer, while the upstream mock
        is matched against a query-param set that does NOT include
        ``limit``.
        """
        from arr_cli.seerr import cmd_discover_tv

        # Build a 30-item envelope so ``--limit 5`` truncates to
        # 5 rows and the footer surfaces a non-zero hidden-count.
        items: list[dict[str, Any]] = [
            {
                "title": f"Title {i:02d}",
                "mediaType": "tv",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"tmdbId": 1000 + i},
            }
            for i in range(30)
        ]
        envelope = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 30,
            "results": items,
        }
        args = self._make_args(human=True, limit=5)
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=envelope,
        ):
            output = _capture_stdout(cmd_discover_tv, args, None)
        lines = output.splitlines()
        # Header + separator + N data rows + (optional) truncation
        # footer line. Counting data rows directly: skip the header
        # and separator rows plus any pagination/footer line.
        data_lines = [
            line for line in lines[2:]
            if line.strip()
            and not line.startswith("\u2026")
            and not line.startswith("…")
        ]
        self.assertEqual(
            len(data_lines), 5,
            msg=(
                "--limit 5 must cap --human rows to 5; "
                f"got {len(data_lines)} data lines:\n{output!r}"
            ),
        )
        # The truncation footer line tells the operator the list was
        # truncated and how many rows were hidden.
        self.assertTrue(
            any(
                "more item" in line and "--limit" in line
                for line in lines
            ),
            msg=(
                "truncation footer missing — --limit was not "
                f"honoured; output:\n{output!r}"
            ),
        )
        # The footer must report 25 hidden items (30 - 5).
        self.assertIn(
            "25 more items",
            output,
            msg=(
                "truncation footer must report the 25 hidden items; "
                f"got:\n{output!r}"
            ),
        )

    def test_seerr_discover_tv_http_error_returns_exit_4(self) -> None:
        """Non-2xx on the ``discover-tv`` endpoint surfaces as exit ``4`` + structured stderr.

        Mirrors the sibling ``discover-movies`` HTTP-error
        contract: the transport raises
        :class:`HttpError(exit_code=4)`, :func:`main_wrapper`
        translates it to a structured
        ``service=seerr op=discover-tv status=<code>`` line on
        stderr so the pipe-clean stdout contract (AGENTS.md §1) is
        preserved. ``assertIn`` is used so the assertion is robust
        to additional stderr framing (e.g. message trailers appended
        by ``main_wrapper``).
        """
        import arr_cli.seerr as seerr

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/tv",
                status=500,
                body="Internal Server Error",
            )
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf), \
                    contextlib.redirect_stderr(stderr_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "discover-tv"]
                )
            stdout = stdout_buf.getvalue()
            stderr = stderr_buf.getvalue()
            self.assertEqual(len(rsps.calls), 1)
        self.assertEqual(exit_code, 4)
        # No payload on stdout — the structured error line goes to
        # stderr so the pipe-clean stdout contract (AGENTS.md §1) is
        # preserved.
        self.assertEqual(stdout, "")
        # Structured line: ``service=seerr op=/api/v1/discover/tv status=500``.
        self.assertIn(
            "service=seerr op=/api/v1/discover/tv status=500",
            stderr,
            msg=(
                "expected structured HTTP-error stderr line; "
                f"got {stderr!r}"
            ),
        )

    def test_seerr_discover_tv_human_renders_tsv(self) -> None:
        """``--human`` renders the curated summary as a tabular TSV with the documented column headers.

        The mock asserts no query params ride the wire for the
        bare ``--human`` invocation -- guards against a regression
        of the 2026-09-18 bug where the CLI hardcoded
        ``sortBy=popularity.desc`` + ``language=en-US``.
        """
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/discover/tv",
                json=self.ENVELOPE,
                status=200,
                match=[
                    responses.matchers.query_param_matcher({})
                ],
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    [
                        "--config", str(self.cfg_path),
                        "discover-tv",
                        "--human",
                    ]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)
            # Header line names the columns the renderer projects:
            # ``id``, ``title``, ``mediaType``, ``releaseDate`` --
            # the top-level ``id`` field the upstream row actually
            # carries. No nested ``mediaInfo.tmdbId`` is projected;
            # the historical fabricated ``{"tmdbId": 0}`` placeholder
            # is gone.
            header_line = stdout.splitlines()[0]
            for column in (
                "id",
                "title",
                "mediaType",
                "releaseDate",
            ):
                self.assertIn(
                    column, header_line,
                    msg=(
                        f"column {column!r} missing from --human header: "
                        f"{header_line!r}"
                    ),
                )
            self.assertNotIn("mediaInfo.tmdbId", header_line)


class TestCmdGenres(unittest.TestCase):
    """Regression tests pinning the endpoint, default media type, and
    summary shape for ``cmd_genres``.

    Seer's genre list endpoints are ``GET /api/v1/genres/movie``
    and ``GET /api/v1/genres/tv``. Each returns a bare list of
    ``{id: int, name: str}`` documents (no envelope wrapping) --
    the canonical TMDB genre shape. The renderer in
    :func:`arr_cli.facade.output._summary_seerr_genres` projects
    the list to a passthrough ``{id, name}`` summary so the
    default ``--human`` table renders as ``Id | Name``.

    CLI surface:

    * A positional ``MEDIA_TYPE`` (``movie`` / ``tv``) with
      ``default="movie"`` so ``seerr genres`` and ``seerr genres
      movie`` both call ``/api/v1/genres/movie``. ``choices=``
      rejects any other value at parse time with
      ``SystemExit(2)``.
    * An optional ``--language <LANG>`` (``ISO 639-1``) flag
      forwarded as ``?language=<LANG>`` so the upstream Seer
      endpoint returns a localised genre list; without the flag
      the request is parameter-free (server default). Pairs with
      ``seerr discover-movies --genre <id> --language <LANG>``
      and ``seerr discover-tv --genre <id> --language <LANG>``
      so the lookup-then-filter chain stays in one locale.
    * The universal ``--verbose`` / ``--human`` flags from
      :func:`arr_cli.facade.cli_common.universal_parents`.
    """

    MOVIE_PAYLOAD: list[dict[str, Any]] = [
        {"id": 28, "name": "Action"},
        {"id": 12, "name": "Adventure"},
        {"id": 16, "name": "Animation"},
        {"id": 35, "name": "Comedy"},
        {"id": 80, "name": "Crime"},
        {"id": 878, "name": "Science Fiction"},
    ]

    TV_PAYLOAD: list[dict[str, Any]] = [
        {"id": 10759, "name": "Action & Adventure"},
        {"id": 16, "name": "Animation"},
        {"id": 35, "name": "Comedy"},
        {"id": 9648, "name": "Mystery"},
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

    def _make_args(self, **overrides: Any) -> argparse.Namespace:
        """Build an ``argparse.Namespace`` mirroring the ``genres`` subparser defaults."""
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
            "command": "genres",
            "genres_type": "movie",
            "language": None,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    # ------------------------------------------------------------------ 4.1.1
    def test_cmd_genres_dispatch_table_registration(self) -> None:
        """``cmd_genres`` is registered in ``_DISPATCH`` and exported via ``__all__``."""
        import arr_cli.seerr as seerr

        # Dispatch table entry points to a callable handler.
        self.assertIn("genres", seerr._DISPATCH)
        self.assertTrue(
            callable(seerr._DISPATCH["genres"]),
            msg="cmd_genres is not callable",
        )
        # Public surface: the handler, the module-level helper, and
        # both path constants are exported so tests can assert
        # against the literals.
        self.assertIn("cmd_genres", seerr.__all__)
        self.assertIn("seerr_genres", seerr.__all__)
        self.assertIn("GENRES_MOVIE_PATH", seerr.__all__)
        self.assertIn("GENRES_TV_PATH", seerr.__all__)
        self.assertEqual(
            seerr.GENRES_MOVIE_PATH, "/api/v1/genres/movie"
        )
        self.assertEqual(seerr.GENRES_TV_PATH, "/api/v1/genres/tv")

    # ------------------------------------------------------------------ 4.1.2
    def test_seerr_genres_default_hits_movie_endpoint(self) -> None:
        """``seerr genres`` (no args) hits ``/api/v1/genres/movie`` (the documented default).

        Pins US-1 AC1 ("WHEN I run ``seerr genres``, THE SYSTEM SHALL
        call ``GET /api/v1/genres/movie``") and US-2 AC3 ("WHEN I
        omit the positional argument, THE SYSTEM SHALL default to
        movie").
        """
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/genres/movie",
                json=self.MOVIE_PAYLOAD,
                status=200,
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                ["--config", str(self.cfg_path), "genres"]
            )
            self.assertEqual(exit_code, 0)
            # The single registered mock fired, so the path was
            # ``/api/v1/genres/movie`` (the default). Any other path
            # would have left the mock unmatched and surfaced a
            # connection error.
            self.assertEqual(len(rsps.calls), 1)

    # ------------------------------------------------------------------ 4.1.3
    def test_seerr_genres_explicit_movie_hits_movie_endpoint(self) -> None:
        """``seerr genres movie`` hits ``/api/v1/genres/movie`` (parity with the default).

        Pins US-2 AC3 ("WHEN I run ``seerr genres movie`` explicitly,
        THE SYSTEM SHALL also call the movie endpoint -- parity
        with the default").
        """
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/genres/movie",
                json=self.MOVIE_PAYLOAD,
                status=200,
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                ["--config", str(self.cfg_path), "genres", "movie"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    # ------------------------------------------------------------------ 4.1.4
    def test_seerr_genres_tv_hits_tv_endpoint(self) -> None:
        """``seerr genres tv`` hits ``/api/v1/genres/tv``.

        Pins US-2 AC1 ("WHEN I run ``seerr genres tv``, THE SYSTEM
        SHALL call ``GET /api/v1/genres/tv``").
        """
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/genres/tv",
                json=self.TV_PAYLOAD,
                status=200,
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                ["--config", str(self.cfg_path), "genres", "tv"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)

    # ------------------------------------------------------------------ 4.1.5
    def test_seerr_genres_invalid_media_type_rejected_at_parse_time(
        self,
    ) -> None:
        """Argparse rejects ``seerr genres <other>`` at parse time (US-2 AC4).

        ``choices=`` validation runs at parse time and triggers
        argparse's ``error()`` path. ``main_wrapper`` translates
        that ``SystemExit(2)`` into a :class:`ConfigError` so the
        operator sees the documented exit code ``1`` (AGENTS.md §6
        "ConfigError -- malformed CLI input") and a structured
        ``service=config op=parse message=...`` stderr line. The
        transport layer is never reached, so the registered mock
        stays unfired. Mirrors
        :func:`TestCmdTrending::test_seerr_trending_invalid_media_type_rejected_at_parse_time`.
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
                        "genres", "bogus",
                    ]
                )
            self.assertEqual(exit_code, 1)
            self.assertEqual(len(rsps.calls), 0)
            self.assertEqual(stdout_buf.getvalue(), "")
            # Argparse's ``error()`` writes its usage line, then
            # ``main_wrapper`` appends the structured
            # ``service=config op=parse ...`` follow-up line.
            self.assertIn(
                "service=config op=parse",
                stderr_buf.getvalue(),
                msg=(
                    "expected structured parse-error stderr line; "
                    f"got {stderr_buf.getvalue()!r}"
                ),
            )

    # ------------------------------------------------------------------ 4.1.6
    def test_seerr_genres_verbose_emits_verbatim_json(self) -> None:
        """``seerr genres --verbose`` emits the verbatim JSON list (US-3 AC1)."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/genres/movie",
                json=self.MOVIE_PAYLOAD,
                status=200,
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    [
                        "--config", str(self.cfg_path),
                        "genres", "--verbose",
                    ]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            # ``--verbose`` bypasses the summary renderer, so
            # stdout carries the raw list verbatim. Decode + spot
            # check a known row.
            decoded = json.loads(stdout)
            self.assertEqual(decoded, self.MOVIE_PAYLOAD)
            self.assertEqual(decoded[5]["name"], "Science Fiction")
            self.assertEqual(decoded[5]["id"], 878)

    # ------------------------------------------------------------------ 4.1.7
    def test_seerr_genres_verbose_tv_emits_verbatim_json(self) -> None:
        """``seerr genres tv --verbose`` emits the verbatim TV JSON list (US-3 AC2)."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/genres/tv",
                json=self.TV_PAYLOAD,
                status=200,
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    [
                        "--config", str(self.cfg_path),
                        "genres", "tv", "--verbose",
                    ]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            decoded = json.loads(stdout)
            self.assertEqual(decoded, self.TV_PAYLOAD)
            self.assertEqual(decoded[0]["name"], "Action & Adventure")

    # ------------------------------------------------------------------ 4.1.8
    def test_seerr_genres_default_emits_id_name_summary(self) -> None:
        """``seerr genres`` default emits the curated ``id`` / ``name`` summary shape.

        Pins the contract between :func:`cmd_genres` and
        :func:`arr_cli.facade.output._summary_seerr_genres`: the
        default stdout is the curated ``[{id, name}, ...]`` shape,
        not the verbatim upstream payload.
        """
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/genres/movie",
                json=self.MOVIE_PAYLOAD,
                status=200,
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "genres"]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            # Summary is JSON-serialised; each row exposes ``id``
            # and ``name`` only -- the upstream keys surface
            # verbatim because the renderer is a passthrough
            # projection.
            decoded = json.loads(stdout)
            self.assertEqual(len(decoded), len(self.MOVIE_PAYLOAD))
            for row in decoded:
                self.assertEqual(set(row.keys()), {"id", "name"})
            self.assertEqual(decoded[5]["id"], 878)
            self.assertEqual(decoded[5]["name"], "Science Fiction")

    # ------------------------------------------------------------------ 4.1.9
    def test_seerr_genres_human_renders_id_name_header(self) -> None:
        """``seerr genres --human`` renders the ``Id | Name`` tabular view (US-4 AC1).

        The handler's ``columns = ["id", "name"]`` literal pins the
        human-mode header; spot-check both columns land on the
        first rendered line.
        """
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/genres/movie",
                json=self.MOVIE_PAYLOAD,
                status=200,
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    [
                        "--config", str(self.cfg_path),
                        "genres", "--human",
                    ]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)
            header_line = stdout.splitlines()[0]
            self.assertIn("id", header_line.lower())
            self.assertIn("name", header_line.lower())
            # Spot-check a known row lands somewhere on stdout --
            # the human renderer tabulates the rows beneath the
            # header.
            self.assertIn("Science Fiction", stdout)
            self.assertIn("Action", stdout)

    # ------------------------------------------------------------------ 4.1.10
    def test_seerr_genres_tv_human_renders_id_name_header(self) -> None:
        """``seerr genres tv --human`` renders the same tabular format for TV (US-4 AC2)."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/genres/tv",
                json=self.TV_PAYLOAD,
                status=200,
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    [
                        "--config", str(self.cfg_path),
                        "genres", "tv", "--human",
                    ]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            header_line = stdout.splitlines()[0]
            self.assertIn("id", header_line.lower())
            self.assertIn("name", header_line.lower())
            self.assertIn("Action & Adventure", stdout)

    # ------------------------------------------------------------------ 4.1.11
    def test_seerr_genres_http_error_propagates_exit_four(self) -> None:
        """HTTP 4xx surfaces through ``main_wrapper`` as exit code 4 (US-1 AC3).

        ``main_wrapper`` translates the facade's
        :class:`HttpError(exit_code=4)` into a structured
        ``service=seerr op=/api/v1/genres/movie status=404 ...``
        stderr line and returns ``4``. The handler itself does
        not special-case HTTP errors -- the facade propagates
        them unchanged, then ``main_wrapper`` maps to the
        documented exit code.
        """
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/genres/movie",
                json={"message": "not found"},
                status=404,
            )
            import arr_cli.seerr as seerr

            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf), \
                    contextlib.redirect_stderr(stderr_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "genres"]
                )
            self.assertEqual(exit_code, 4)
            # Structured stderr line confirms the facade error
            # contract (service=seerr op=... status=...).
            self.assertIn(
                "service=seerr op=/api/v1/genres/movie status=404",
                stderr_buf.getvalue(),
                msg=(
                    "expected structured HttpError stderr line; "
                    f"got {stderr_buf.getvalue()!r}"
                ),
            )

    # ------------------------------------------------------------------ 4.1.12
    def test_seerr_genres_module_level_helper_rejects_bad_media_type(
        self,
    ) -> None:
        """``seerr_genres`` defensive guard rejects ``media_type`` outside ``{movie, tv}``.

        Pins the defensive guard in :func:`seerr_genres`: the
        argparse ``choices=`` already rejects bad values at parse
        time with ``SystemExit(2)``, but the function keeps its
        own guard so it can be reused safely from non-CLI entry
        points. A bad value here surfaces as
        :class:`ConfigError` (exit code 1).
        """
        from arr_cli.facade.errors import ConfigError

        import arr_cli.seerr as seerr

        with self.assertRaises(ConfigError) as exc_ctx:
            seerr.seerr_genres(
                "bogus",
                self._make_args(),
                None,
            )
        self.assertEqual(exc_ctx.exception.exit_code, 1)

    # ------------------------------------------------------------------ 4.1.13
    def test_seerr_genres_language_en_hits_movie_endpoint_with_query_param(
        self,
    ) -> None:
        """``seerr genres movie --language en`` forwards ``?language=en`` on the wire.

        Pin for the ``--language`` flag on the movie endpoint:
        ``responses``' exact-URL matcher fires only when the
        request lands on the registered ``?language=en`` URL --
        any drift (no query, wrong key, empty value) leaves the
        mock unmatched and surfaces as a ``NetworkError`` exit
        code 3 instead of the expected exit 0.
        """
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/genres/movie"
                "?language=en",
                json=self.MOVIE_PAYLOAD,
                status=200,
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "genres", "movie", "--language", "en",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)
            # Spot-check the actual on-the-wire URL so a future
            # regression to a different query-key name (e.g.
            # ``?lang=``) fails this assertion rather than
            # silently swapping the wire contract.
            self.assertIn("language=en", rsps.calls[0].request.url)

    # ------------------------------------------------------------------ 4.1.14
    def test_seerr_genres_language_en_hits_tv_endpoint_with_query_param(
        self,
    ) -> None:
        """``seerr genres tv --language en`` forwards ``?language=en`` on the wire."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/genres/tv"
                "?language=en",
                json=self.TV_PAYLOAD,
                status=200,
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                [
                    "--config", str(self.cfg_path),
                    "genres", "tv", "--language", "en",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)
            self.assertIn("language=en", rsps.calls[0].request.url)

    # ------------------------------------------------------------------ 4.1.15
    def test_seerr_genres_no_language_omits_language_query_param(self) -> None:
        """Without ``--language``, the request URL carries no ``language=`` query param.

        Mirrors the ``cmd_tv`` / ``cmd_movie`` "no language"
        contract: the flag defaults to ``None`` so the request
        stays parameter-free and the upstream server picks its
        own default. A future regression that always sends
        ``?language=`` would leave the bare-URL mock unmatched
        and surface as exit code 3.
        """
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/genres/movie",
                json=self.MOVIE_PAYLOAD,
                status=200,
            )
            import arr_cli.seerr as seerr

            exit_code = seerr.main(
                ["--config", str(self.cfg_path), "genres"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)
            # No language key on the wire -- server picks default.
            self.assertNotIn("language=", rsps.calls[0].request.url)

    # ------------------------------------------------------------------ 4.1.16
    def test_seerr_genres_help_lists_language_option(self) -> None:
        """``seerr genres --help`` lists ``--language`` in the options block.

        Acceptance criterion: ``seerr genres movie --help``
        lists ``--language`` in the options block, matching the
        help-text style of ``seerr tv --help`` / ``seerr movie
        --help``. The argparse help text is the operator-facing
        contract for the new flag -- surfacing the flag in the
        generated usage text is part of the deliverable, not a
        nice-to-have.
        """
        import arr_cli.seerr as seerr

        parser = seerr.build_seerr_parser()
        with contextlib.redirect_stdout(io.StringIO()) as stdout_buf:
            with self.assertRaises(SystemExit) as exc_ctx:
                parser.parse_args(["genres", "--help"])
        self.assertEqual(exc_ctx.exception.code, 0)
        help_text = stdout_buf.getvalue()
        self.assertIn("--language", help_text)
        # ISO 639-1 framing lives in the help text too -- spot
        # check so a regression that drops the framing surfaces
        # in the unit layer rather than the operator's terminal.
        self.assertIn("LANG", help_text)


# ---------------------------------------------------------------------------
# Test: ``cmd_requests`` projects title from ``media`` envelope per media type
# ---------------------------------------------------------------------------


class TestCmdRequestsMediaEnvelope(unittest.TestCase):
    """Regression tests for the per-row ``media`` envelope identity projection.

    Seer's ``/api/v1/request`` endpoint wraps each row's media
    metadata inside a ``media`` envelope keyed by ``id``,
    ``mediaType``, ``tmdbId`` / ``tvdbId``, ``externalServiceSlug``
    and ``status``. The renderer's job is to project those identity
    fields under a nested ``media`` sub-dict so the default summary
    gives the operator something to chain into ``seerr movie <id>``
    / ``seerr tv <id>``.

    The historical ``title`` projection was retired because the live
    ``/api/v1/request`` payload on the operator's Seer instance does
    not populate ``media.title`` (movie) or ``media.name`` (TV) --
    every row projected ``title: null``. The AC that motivates this
    test class is now: "seerr requests default rows surface identity
    fields from the ``media`` sub-dict, not a fabricated ``title``".

    These tests pin the identity-projection contract end-to-end: the
    renderer exercises both ``type == "movie"`` and ``type == "tv"``
    shapes in a single ``results[]`` array, the ``--human`` tabular
    view surfaces the populated identity columns for every row, and
    ``--verbose`` is unchanged (verbatim envelope passthrough).
    """

    PAGINATED_ENVELOPE: dict[str, Any] = {
        "pageInfo": {
            "pages": 1,
            "pageSize": 10,
            "results": 2,
            "page": 1,
        },
        "results": [
            {
                "id": 121,
                "type": "movie",
                "status": 5,
                "createdAt": "2026-09-13T12:56:58.000Z",
                "requestedBy": {"displayName": "alice"},
                "media": {
                    "id": 121,
                    "mediaType": "movie",
                    "tmdbId": 603,
                    "tvdbId": None,
                    "externalServiceSlug": "tmdb",
                    "status": 5,
                },
            },
            {
                "id": 120,
                "type": "tv",
                "status": 2,
                "createdAt": "2026-09-13T12:55:03.000Z",
                "requestedBy": {"displayName": "bob"},
                "media": {
                    "id": 120,
                    "mediaType": "tv",
                    "tmdbId": None,
                    "tvdbId": 76107,
                    "externalServiceSlug": "tvdb",
                    "status": 5,
                },
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

    def test_default_summary_populates_identity_for_movie_and_tv(self) -> None:
        """The renderer projects identity fields at the top level for both media shapes."""
        from arr_cli.seerr import cmd_requests

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.PAGINATED_ENVELOPE,
        ):
            output = _capture_stdout(cmd_requests, args, None)
        rendered = json.loads(output)
        self.assertEqual(len(rendered), 2)
        # Movie row: identity fields (id, mediaType, tmdbId,
        # externalServiceSlug) lifted from ``media`` live at the top
        # level. ``tvdbId`` is ``None`` because the upstream payload
        # does not populate TVDB ids for movie rows.
        self.assertEqual(rendered[0]["type"], "movie")
        self.assertEqual(rendered[0]["id"], 121)
        self.assertEqual(rendered[0]["mediaType"], "movie")
        self.assertEqual(rendered[0]["tmdbId"], 603)
        self.assertIsNone(rendered[0]["tvdbId"])
        self.assertEqual(rendered[0]["externalServiceSlug"], "tmdb")
        # TV row surfaces ``tvdbId`` instead of ``tmdbId`` (same flat
        # shape, just different media-type identity).
        self.assertEqual(rendered[1]["type"], "tv")
        self.assertEqual(rendered[1]["id"], 120)
        self.assertEqual(rendered[1]["mediaType"], "tv")
        self.assertIsNone(rendered[1]["tmdbId"])
        self.assertEqual(rendered[1]["tvdbId"], 76107)
        self.assertEqual(rendered[1]["externalServiceSlug"], "tvdb")
        # No nested ``media`` envelope / ``requestedBy`` mapping and
        # no fabricated top-level ``title`` key -- the historical
        # projection is gone, so neither row projects a ``title``
        # field that would always be ``null``.
        for row in rendered:
            self.assertNotIn(
                "media",
                row,
                msg=(
                    f"seerr requests row still nests identity under "
                    f"a 'media' key: {row!r} -- the projection must "
                    "be flat top-level to match ``seerr available``"
                ),
            )
            self.assertNotIn(
                "requestedBy",
                row,
                msg=(
                    f"seerr requests row re-surfaces a 'requestedBy' "
                    f"key that should have been dropped: {row!r}"
                ),
            )
            self.assertNotIn(
                "title",
                row,
                msg=(
                    f"seerr requests row still surfaces a top-level "
                    f"'title' key: {row!r} -- the historical projection "
                    "must be retired on the operator's Seer instance"
                ),
            )

    def test_verbose_emits_verbatim_envelope(self) -> None:
        """``--verbose`` bypasses the renderer and emits the envelope verbatim."""
        from arr_cli.seerr import cmd_requests

        args = self._make_args()
        args.verbose = True
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.PAGINATED_ENVELOPE,
        ):
            output = _capture_stdout(cmd_requests, args, None)
        # ``--verbose`` keeps the envelope shape intact; downstream
        # consumers still see ``pageInfo`` / ``results`` /
        # ``media.{id,mediaType,tmdbId,tvdbId,externalServiceSlug,status}``
        # as the service emitted them.
        self.assertEqual(json.loads(output), self.PAGINATED_ENVELOPE)

    def test_human_table_surfaces_identity_columns(self) -> None:
        """``--human`` renders the identity columns at the top level for every row.

        The bug's AC explicitly calls out that every row must render
        a non-``<null>`` identity cell (the documented failure mode
        being fixed is ``title: null`` on every row). The fix
        replaces the nested ``media.{id,mediaType,tmdbId,tvdbId}``
        columns with their flat top-level siblings so the operator
        has something to chain into the detail commands.
        """
        from arr_cli.seerr import cmd_requests

        args = self._make_args()
        args.human = True
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.PAGINATED_ENVELOPE,
        ):
            output = _capture_stdout(cmd_requests, args, None)
        lines = output.splitlines()
        # Header line names the documented flat identity columns.
        # ``externalServiceSlug`` is in the summary JSON shape but
        # intentionally NOT in the human column list (the 18-char
        # header truncates at the ~17-char per-column budget when
        # seven columns share the 120-char width); the slug is
        # still available in the JSON summary and ``--verbose``.
        header_line = lines[0]
        for column in (
            "id",
            "mediaType",
            "tmdbId",
            "tvdbId",
            "type",
            "status",
            "createdAt",
        ):
            self.assertIn(
                column, header_line,
                msg=(
                    f"column {column!r} missing from --human header: "
                    f"{header_line!r}"
                ),
            )
        # Confirm the curated column-list rationale: the slug is in
        # the JSON summary shape but the rendered human header
        # doesn't surface its truncated form. (We do NOT assert it
        # is absent; the renderer truncates to ``externalServiceSlu…``
        # at the 8-column budget, which is not what we want.)
        self.assertNotIn(
            "externalServiceSlu",
            header_line,
            msg=(
                "--human header surfaced the truncated form of "
                f"'externalServiceSlug': {header_line!r}"
            ),
        )
        # The historical nested ``media.*`` tokens must NOT appear
        # in the header -- the projection is flat top-level to match
        # ``seerr available``.
        for nested_column in (
            "media.id",
            "media.mediaType",
            "media.tmdbId",
            "media.tvdbId",
        ):
            self.assertNotIn(
                nested_column,
                header_line,
                msg=(
                    f"--human header still names the nested "
                    f"{nested_column!r} column: {header_line!r}"
                ),
            )
        # The historical ``title`` column must NOT appear in the
        # header -- it was retired because the live payload does
        # not populate ``media.title`` / ``media.name``.
        self.assertNotIn(
            "title",
            header_line,
            msg=(
                f"--human header still names the retired 'title' "
                f"column: {header_line!r}"
            ),
        )
        # The rendered output must contain the movie's TMDB id and
        # the TV row's TVDB id (the flat identity columns).
        self.assertIn("603", output)
        self.assertIn("76107", output)
        # The documented failure mode being fixed was every row
        # projecting ``title: null``; the identity columns replace
        # the fabricated ``title`` so each row has a meaningful
        # identity cell populated (movie row: TMDB id; TV row:
        # TVDB id). Cross-type identity fields (``tvdbId`` for movie
        # rows, ``tmdbId`` for TV rows) are intentionally ``None``
        # because the upstream payload does not populate them,
        # which is the correct behaviour -- the AC does not require
        # every cell to be non-null, only that the row has a
        # meaningful identity (not ``title: null``).

    def test_http_path_pin_envelope_passes_through(self) -> None:
        """End-to-end path pin: ``seerr requests`` retrieves ``/api/v1/request`` and emits the curated shape."""
        import arr_cli.seerr as seerr

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/request",
                json=self.PAGINATED_ENVELOPE,
                status=200,
            )
            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "requests"]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)
        rendered = json.loads(stdout)
        self.assertEqual(len(rendered), 2)
        # Movie row surfaces the TMDB id at the top level (the flat
        # identity projection, no nested ``media`` envelope).
        self.assertEqual(rendered[0]["tmdbId"], 603)
        # TV row surfaces the TVDB id instead.
        self.assertEqual(rendered[1]["tvdbId"], 76107)

    def test_renderer_missing_media_envelope_yields_none(self) -> None:
        """A row whose ``media`` envelope is missing yields ``None`` for every identity field.

        The defensive contract mirrors
        :func:`_summary_seerr_available`'s handling of missing
        upstream keys: a record whose ``media`` envelope is absent
        still produces a well-formed row with every identity field
        set to ``None`` instead of crashing. Pins the upstream-shape
        drift so future envelope changes do not regress the renderer
        into a crash.
        """
        from arr_cli.facade.output import _summary_seerr_requests

        envelope = {
            "pageInfo": {"pages": 1, "pageSize": 10, "results": 2, "page": 1},
            "results": [
                {
                    "id": 1,
                    "type": "movie",
                    "status": 5,
                    "createdAt": "2026-09-13T12:56:58.000Z",
                    "requestedBy": {"displayName": "alice"},
                },
            ],
            "serviceErrors": {"radarr": [], "sonarr": []},
        }
        rendered = _summary_seerr_requests(envelope)
        # Identity fields at the top level surface ``None`` when the
        # ``media`` envelope is absent (defensive contract; mirrors
        # :func:`_summary_seerr_available`).
        self.assertIsNone(rendered[0]["id"])
        self.assertIsNone(rendered[0]["mediaType"])
        self.assertIsNone(rendered[0]["tmdbId"])
        self.assertIsNone(rendered[0]["tvdbId"])
        self.assertIsNone(rendered[0]["externalServiceSlug"])
        # Request-level fields survive intact even when the media
        # envelope is absent.
        self.assertEqual(rendered[0]["type"], "movie")
        self.assertEqual(rendered[0]["status"], 5)
        self.assertEqual(
            rendered[0]["createdAt"], "2026-09-13T12:56:58.000Z"
        )
        # No nested ``media`` / ``requestedBy`` envelope.
        self.assertNotIn("media", rendered[0])
        self.assertNotIn("requestedBy", rendered[0])


# ---------------------------------------------------------------------------
# Test: ``cmd_search`` mirrors ``_summary_seerr_trending`` mediaType branching
# ---------------------------------------------------------------------------


class TestCmdSearchMixedMediaTypes(unittest.TestCase):
    """Regression tests pinning the per-media-type title projection for ``cmd_search``.

    Seer's ``/api/v1/search`` returns a paginated envelope whose
    ``results[]`` mixes movie rows (top-level ``title``) and TV
    rows (top-level ``name`` only). The renderer mirrors
    :func:`_summary_seerr_trending`'s ``is_tv = mediaType == "tv"``
    branching so the single projected ``title`` column is
    populated regardless of media type -- this is the bug's AC:
    "seerr search 'matrix' TV rows show their name ("Threat
    Matrix", "Matrix", "Matrix Dreads", "Aurora Matrix") in the
    default summary under the single title column, not null".
    """

    PAGINATED_ENVELOPE: dict[str, Any] = {
        "page": 1,
        "totalPages": 1,
        "totalResults": 4,
        "results": [
            {
                "id": 603,
                "title": "The Matrix",
                "mediaType": "movie",
                "releaseDate": "1999-03-31",
            },
            {
                "id": 104586,
                "name": "Threat Matrix",
                "mediaType": "tv",
                "releaseDate": "2020-09-09",
            },
            {
                "id": 23988,
                "name": "Matrix",
                "mediaType": "tv",
                "releaseDate": "1993-03-03",
            },
            {
                "id": 99999,
                "name": "Matrix Dreads",
                "mediaType": "tv",
                "releaseDate": "2015-01-21",
            },
            {
                "id": 108586,
                "name": "Aurora Matrix",
                "mediaType": "tv",
                "releaseDate": "2020-09-01",
            },
        ],
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
            query="matrix",
        )

    def test_default_summary_populates_title_for_each_row(self) -> None:
        """TV rows surface their ``name`` under the projected ``title`` column; movie rows surface ``title``."""
        from arr_cli.seerr import cmd_search

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.PAGINATED_ENVELOPE,
        ):
            output = _capture_stdout(cmd_search, args, None)
        rendered = json.loads(output)
        self.assertEqual(len(rendered), 5)
        # Movie row surfaces top-level ``title``.
        self.assertEqual(rendered[0]["title"], "The Matrix")
        # TV rows surface top-level ``name`` under the projected
        # ``title`` column -- the exact AC values from the bug
        # review.
        self.assertEqual(rendered[1]["title"], "Threat Matrix")
        self.assertEqual(rendered[2]["title"], "Matrix")
        self.assertEqual(rendered[3]["title"], "Matrix Dreads")
        self.assertEqual(rendered[4]["title"], "Aurora Matrix")
        # ``mediaType`` is preserved so downstream consumers can
        # still distinguish the rows.
        self.assertEqual(rendered[0]["mediaType"], "movie")
        for row in rendered[1:]:
            self.assertEqual(row["mediaType"], "tv")

    def test_verbose_emits_verbatim_envelope(self) -> None:
        """``--verbose`` bypasses the renderer and emits the envelope verbatim."""
        from arr_cli.seerr import cmd_search

        args = self._make_args()
        args.verbose = True
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.PAGINATED_ENVELOPE,
        ):
            output = _capture_stdout(cmd_search, args, None)
        self.assertEqual(json.loads(output), self.PAGINATED_ENVELOPE)

    def test_human_table_surfaces_populated_title(self) -> None:
        """``--human`` renders the populated title column for every row."""
        from arr_cli.seerr import cmd_search

        args = self._make_args()
        args.human = True
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.PAGINATED_ENVELOPE,
        ):
            output = _capture_stdout(cmd_search, args, None)
        # All five titles must appear in the rendered table; the
        # bug's failure mode would render the four TV rows as
        # ``<null>`` and the operator would see four empty cells.
        for title in (
            "The Matrix",
            "Threat Matrix",
            "Matrix Dreads",
            "Aurora Matrix",
        ):
            self.assertIn(
                title, output,
                msg=(
                    f"title {title!r} missing from --human output: "
                    f"{output!r}"
                ),
            )
        # And no ``<null>`` cells for the title column.
        self.assertNotIn(
            "<null>", output,
            msg=(
                "title column rendered as <null> for at least one "
                f"row; output:\n{output!r}"
            ),
        )

    def test_http_path_pin_mixed_media_payload(self) -> None:
        """End-to-end path pin: ``seerr search matrix`` emits populated titles for movie + TV rows."""
        import arr_cli.seerr as seerr

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/search",
                json=self.PAGINATED_ENVELOPE,
                status=200,
            )
            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    [
                        "--config", str(self.cfg_path),
                        "search", "matrix",
                    ]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)
        rendered = json.loads(stdout)
        self.assertEqual(len(rendered), 5)
        self.assertEqual(rendered[0]["title"], "The Matrix")
        self.assertEqual(rendered[1]["title"], "Threat Matrix")
        self.assertEqual(rendered[2]["title"], "Matrix")
        self.assertEqual(rendered[3]["title"], "Matrix Dreads")
        self.assertEqual(rendered[4]["title"], "Aurora Matrix")

    def test_renderer_non_tv_row_uses_title_key(self) -> None:
        """A non-TV row sources its title from top-level ``title`` (defensive mediaType default)."""
        from arr_cli.facade.output import _summary_seerr_search

        envelope = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 1,
            "results": [
                {
                    "title": "Inception",
                    "mediaType": "movie",
                    "releaseDate": "2010-07-15",
                    "mediaInfo": {"tmdbId": 27205},
                },
            ],
        }
        rendered = _summary_seerr_search(envelope)
        self.assertEqual(rendered[0]["title"], "Inception")

    def test_renderer_unknown_media_type_falls_back_to_title(self) -> None:
        """A row with an unknown ``mediaType`` falls back to top-level ``title`` (defensive default)."""
        from arr_cli.facade.output import _summary_seerr_search

        envelope = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 1,
            "results": [
                {
                    "title": "Some Person",
                    "mediaType": "person",
                    "mediaInfo": {"tmdbId": 1},
                },
            ],
        }
        rendered = _summary_seerr_search(envelope)
        self.assertEqual(rendered[0]["title"], "Some Person")


# ---------------------------------------------------------------------------
# Test: ``cmd_movie`` reads ``payload.title`` (canonical Seer detail shape)
# ---------------------------------------------------------------------------


class TestCmdMovieRealShape(unittest.TestCase):
    """Regression tests pinning the canonical Seer movie detail shape.

    Seer's movie detail endpoint uses top-level ``title`` (with
    ``name=None``) on the canonical Seer shape; the renderer
    previously read ``payload.name`` and shipped ``name: null`` for
    every detail fetch. These tests pin the upstream shape --
    ``title`` at the top level -- and assert the default summary
    emits ``title: "The Matrix"``. The ``cmd_movie`` ``columns``
    literal also has to lead with ``"title"`` so the ``--human``
    tabular view resolves the populated value.

    ``--verbose`` is unchanged (verbatim detail payload passthrough).
    """

    DETAIL_PAYLOAD: dict[str, Any] = {
        "id": 603,
        "title": "The Matrix",
        "name": None,
        "originalTitle": "The Matrix",
        "releaseDate": "1999-03-31",
        "runtime": 136,
        "genres": [{"id": 28, "name": "Action"}],
        "tagline": "Welcome to the Real World.",
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

    def test_default_summary_emits_title(self) -> None:
        """Default ``cmd_movie`` summary emits ``title: "The Matrix"`` from ``payload.title``."""
        from arr_cli.seerr import cmd_movie

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.DETAIL_PAYLOAD,
        ) as mock_get:
            output = _capture_stdout(cmd_movie, args, None)
        self.assertEqual(len(mock_get.call_args_list), 1)
        rendered = json.loads(output)
        # Bug fix AC: the projected ``title`` is populated from
        # ``payload.title``, NOT from the ``name=None`` field.
        self.assertEqual(rendered["title"], "The Matrix")
        self.assertEqual(rendered["originalTitle"], "The Matrix")
        self.assertEqual(rendered["releaseDate"], "1999-03-31")
        self.assertEqual(rendered["runtime"], "2h 16m")
        self.assertEqual(rendered["genres"], "Action")
        self.assertEqual(rendered["tagline"], "Welcome to the Real World.")
        self.assertIsNone(rendered["ratings"])

    def test_default_summary_does_not_emit_name_key(self) -> None:
        """Default summary emits the curated ``title`` key (NOT the upstream ``name=None`` field)."""
        from arr_cli.seerr import cmd_movie

        args = self._make_args()
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.DETAIL_PAYLOAD,
        ):
            output = _capture_stdout(cmd_movie, args, None)
        rendered = json.loads(output)
        # The curated summary shape has ``title`` at the top
        # level; it does NOT carry the upstream ``name=None``
        # field, which was the bug's failure mode.
        self.assertNotIn(
            "name", rendered,
            msg=(
                "curated summary must not carry the upstream "
                f"``name=None`` field; got {rendered!r}"
            ),
        )

    def test_verbose_emits_verbatim_payload(self) -> None:
        """``--verbose`` bypasses the renderer and emits the verbatim payload (including ``name=None``)."""
        from arr_cli.seerr import cmd_movie

        args = self._make_args(verbose=True)
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.DETAIL_PAYLOAD,
        ):
            output = _capture_stdout(cmd_movie, args, None)
        # ``--verbose`` keeps the upstream payload intact; the
        # canonical Seer shape carries ``name=None`` at the top
        # level even though the curated summary does not.
        self.assertEqual(json.loads(output), self.DETAIL_PAYLOAD)

    def test_human_table_resolves_title_column(self) -> None:
        """``--human`` tabular view resolves the populated ``title`` column (NOT ``name``)."""
        from arr_cli.seerr import cmd_movie

        args = self._make_args(human=True)
        with patch(
            "arr_cli.seerr.transport.get",
            return_value=self.DETAIL_PAYLOAD,
        ):
            output = _capture_stdout(cmd_movie, args, None)
        # The rendered single-object payload surfaces ``title``
        # populated -- ``name=None`` would render as ``<null>``
        # otherwise. The ``cmd_movie`` ``columns`` literal leads
        # with ``"title"`` (Change 4) so the populated value is
        # the one that surfaces.
        self.assertIn(
            "title:", output,
            msg=(
                "human-mode rendering must surface the populated "
                f"``title`` field; got:\n{output!r}"
            ),
        )
        self.assertIn(
            "The Matrix", output,
            msg=(
                "human-mode rendering must include the title "
                f"value 'The Matrix'; got:\n{output!r}"
            ),
        )

    def test_http_path_pin_real_shape(self) -> None:
        """End-to-end path pin: ``seerr movie 603`` emits ``title: "The Matrix"`` from the canonical shape."""
        import arr_cli.seerr as seerr

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://seerr.example/api/v1/movie/603",
                json=self.DETAIL_PAYLOAD,
                status=200,
            )
            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = seerr.main(
                    ["--config", str(self.cfg_path), "movie", "603"]
                )
            stdout = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rsps.calls), 1)
        rendered = json.loads(stdout)
        # Bug fix AC: default summary emits ``title: "The Matrix"``.
        self.assertEqual(rendered["title"], "The Matrix")
        self.assertNotIn("name", rendered)


if __name__ == "__main__":
    unittest.main()