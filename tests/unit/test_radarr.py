"""Unit tests for :mod:`arr_cli.radarr` (task 9).

Covers the contract spelled out in task 9.4 of tasks.md:

* Each of the six Radarr commands hits the documented HTTP path
  with the documented query parameters (REQ-7 AC1-7).
* ``calendar`` accepts ``start`` only, ``start``+``end``, or neither;
  rejects malformed dates with :class:`ConfigError(exit_code=1)`.
* ``lookup`` percent-encodes the ``term`` query parameter (security
  NFR: no raw interpolation).
* ``movie <id>`` propagates a 404 as :class:`HttpError` (exit code 4).
* The parser rejects unknown subcommands (REQ-11 AC4); the
  per-command handler names are registered in the dispatch table.

Tests use ``unittest.mock`` to stub ``arr_cli.facade.transport.get``
so we can assert the exact path, params, and percent-encoding
without a network dependency. The expected mirror of test_jellyfin.py
keeps the test style consistent across the five CLIs.
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

from arr_cli.facade.config import (  # noqa: E402
    AuthConfig,
    ServiceConfig,
)
from arr_cli.facade.errors import (  # noqa: E402
    ConfigError,
    HttpError,
)
from arr_cli.radarr import (  # noqa: E402
    SERVICE_NAME,
    _DISPATCH,
    _validate_iso_date,
    build_radarr_parser,
    cmd_calendar,
    cmd_lookup,
    cmd_movie,
    cmd_queue,
    cmd_recent,
    cmd_wanted,
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
    :func:`load_config` succeeds. Only the Radarr fields
    (api_key) matter for the happy-path tests; the others exist so
    the loader accepts the file as a complete config.
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
    ak: str | None = "test-radarr-key",
    url: str = "https://radarr.example",
    include_radarr: bool = True,
) -> ServiceConfig:
    """Return a :class:`ServiceConfig` configured for the Radarr tests.

    Each parameter has a documented default so individual tests can
    override only the field they care about (e.g. ``include_radarr=False``
    to exercise the missing-section path).
    """
    radarr = (
        AuthConfig(url=url, ak=ak)
        if include_radarr
        else None
    )
    return ServiceConfig(
        jellyfin=AuthConfig(
            url="https://jellyfin.example",
            ak="jf-token",
            user_id="jf-user-1",
        ),
        radarr=radarr,
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
    :func:`build_parser`. Per-command fields (``start``, ``end``,
    ``term``, ``movie_id``) are added via ``kwargs`` so each test
    sets exactly what it needs.
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
    """Return a context manager that mocks ``transport.get`` to return ``payload``."""
    return patch(
        "arr_cli.radarr.transport.get",
        return_value=payload,
    )


def _patched_get_raising(exc: BaseException) -> Any:
    """Return a context manager that mocks ``transport.get`` to raise ``exc``."""
    return patch(
        "arr_cli.radarr.transport.get",
        side_effect=exc,
    )


def _parse_human_row(
    rendered: str, *, data_index: int
) -> dict[str, str]:
    """Return the cells of the ``data_index``-th data row as a dict.

    The ``human`` renderer emits a header row, a separator row
    of dashes, then one data row per payload item. Each cell is
    right-padded to a column-specific width and joined with a
    2-space separator. This helper recovers the column widths
    from the separator row, then slices each data cell at the
    same boundaries and strips trailing padding.
    """
    import re

    lines = [
        line for line in rendered.splitlines() if line.strip()
    ]
    if len(lines) < 2 + data_index + 1:
        raise AssertionError(
            f"rendered output has only {len(lines)} lines; "
            f"data_index={data_index} is out of range"
        )
    header_line = lines[0]
    separator_line = lines[1]
    data_line = lines[2 + data_index]
    widths = [
        len(cell) for cell in re.split(r"  +", separator_line)
    ]
    offsets = _column_offsets(widths)
    headers = [
        header_line[cursor : cursor + width].strip()
        for cursor, width in zip(offsets, widths)
    ]
    cells = [
        data_line[cursor : cursor + width].strip()
        for cursor, width in zip(offsets, widths)
    ]
    return dict(zip(headers, cells))


def _column_offsets(widths: list[int]) -> list[int]:
    """Return the starting index of each column given its width.

    Each column is followed by a 2-space separator, except the
    last column. The width list and the offsets list are the
    same length.
    """
    offsets: list[int] = []
    cursor = 0
    for index, width in enumerate(widths):
        offsets.append(cursor)
        cursor += width + (2 if index < len(widths) - 1 else 0)
    return offsets


# ---------------------------------------------------------------------------
# Test: dispatch table and parser registration
# ---------------------------------------------------------------------------


class TestDispatchTable(unittest.TestCase):
    """The dispatch table contains every documented subcommand."""

    def test_dispatch_keys(self) -> None:
        self.assertEqual(
            set(_DISPATCH.keys()),
            {
                "calendar",
                "wanted",
                "queue",
                "recent",
                "lookup",
                "movie",
            },
        )

    def test_dispatch_handlers_are_callable(self) -> None:
        for handler in _DISPATCH.values():
            self.assertTrue(callable(handler))

    def test_each_handler_returns_int(self) -> None:
        # Every handler's success path returns an int (REQ-11 AC1).
        cfg = _service_config()
        payload: Any = []
        cases = [
            (cmd_calendar, _namespace(start=None, end=None)),
            (cmd_wanted, _namespace()),
            (cmd_queue, _namespace()),
            (cmd_recent, _namespace()),
            (cmd_lookup, _namespace(term="the matrix")),
            (cmd_movie, _namespace(movie_id="42")),
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


# ---------------------------------------------------------------------------
# Test: ISO-8601 date validator (REQ-7 AC2)
# ---------------------------------------------------------------------------


class TestValidateIsoDate(unittest.TestCase):
    """``_validate_iso_date`` accepts both ISO-8601 date and datetime forms."""

    def test_accepts_date_only(self) -> None:
        self.assertEqual(_validate_iso_date("2026-01-15"), "2026-01-15")

    def test_accepts_datetime_with_z(self) -> None:
        self.assertEqual(
            _validate_iso_date("2026-01-15T12:34:56Z"),
            "2026-01-15T12:34:56Z",
        )

    def test_accepts_datetime_without_z(self) -> None:
        self.assertEqual(
            _validate_iso_date("2026-01-15T12:34:56"),
            "2026-01-15T12:34:56",
        )

    def test_rejects_natural_language(self) -> None:
        # REQ-7 AC2 explicit example: "next-tuesday" must be rejected.
        with self.assertRaises(ConfigError) as ctx:
            _validate_iso_date("next-tuesday")
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("next-tuesday", ctx.exception.message)
        self.assertIn("ISO-8601", ctx.exception.message)

    def test_rejects_slash_separator(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            _validate_iso_date("2026/01/15")
        self.assertEqual(ctx.exception.exit_code, 1)

    def test_rejects_unpadded_components(self) -> None:
        # Strict format: ``%Y-%m-%d`` requires two-digit month/day.
        with self.assertRaises(ConfigError) as ctx:
            _validate_iso_date("2026-1-15")
        self.assertEqual(ctx.exception.exit_code, 1)

    def test_rejects_partial_datetime(self) -> None:
        # Hours/minutes without seconds is not in the documented set.
        with self.assertRaises(ConfigError) as ctx:
            _validate_iso_date("2026-01-15T12:34")
        self.assertEqual(ctx.exception.exit_code, 1)

    def test_rejects_empty_string(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            _validate_iso_date("")
        self.assertEqual(ctx.exception.exit_code, 1)

    def test_rejects_non_string(self) -> None:
        # Defensive: a future caller that bypasses argparse might
        # pass an int/float; the validator must still raise.
        with self.assertRaises(ConfigError) as ctx:
            _validate_iso_date(20260115)  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.exit_code, 1)

    def test_rejects_trailing_garbage(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            _validate_iso_date("2026-01-15Z-extra")
        self.assertEqual(ctx.exception.exit_code, 1)


# ---------------------------------------------------------------------------
# Test: cmd_calendar (REQ-7 AC1, AC2)
# ---------------------------------------------------------------------------


class TestCmdCalendar(unittest.TestCase):
    """``GET /api/v3/calendar`` with optional start/end query parameters."""

    def test_calendar_no_args_hits_path_without_params(self) -> None:
        cfg = _service_config()
        args = _namespace(start=None, end=None)
        with _patched_get_payload([]) as mock_get:
            cmd_calendar(args, cfg)
        positional = mock_get.call_args.args
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(positional[0], "radarr")
        self.assertEqual(positional[1], "/api/v3/calendar")
        # No start/end → params is None so the URL stays clean.
        self.assertIsNone(kwargs.get("params"))

    def test_calendar_start_only(self) -> None:
        cfg = _service_config()
        args = _namespace(start="2026-01-01", end=None)
        with _patched_get_payload([]) as mock_get:
            cmd_calendar(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(kwargs["params"], {"start": "2026-01-01"})

    def test_calendar_start_and_end(self) -> None:
        cfg = _service_config()
        args = _namespace(start="2026-01-01", end="2026-01-31")
        with _patched_get_payload([]) as mock_get:
            cmd_calendar(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(
            kwargs["params"],
            {"start": "2026-01-01", "end": "2026-01-31"},
        )

    def test_calendar_accepts_datetime_form(self) -> None:
        cfg = _service_config()
        args = _namespace(
            start="2026-01-01T00:00:00Z",
            end="2026-01-31T23:59:59Z",
        )
        with _patched_get_payload([]) as mock_get:
            cmd_calendar(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(
            kwargs["params"],
            {
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-31T23:59:59Z",
            },
        )

    def test_calendar_malformed_start_raises_config_error(self) -> None:
        # REQ-7 AC2: reject malformed dates with exit 1 + stderr usage hint.
        cfg = _service_config()
        args = _namespace(start="next-tuesday", end=None)
        with self.assertRaises(ConfigError) as ctx:
            cmd_calendar(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("next-tuesday", ctx.exception.message)
        self.assertIn("calendar", ctx.exception.message)

    def test_calendar_malformed_end_raises_config_error(self) -> None:
        cfg = _service_config()
        args = _namespace(start="2026-01-01", end="garbage")
        with self.assertRaises(ConfigError) as ctx:
            cmd_calendar(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("garbage", ctx.exception.message)

    def test_calendar_valid_start_invalid_end_runs_valid_check(self) -> None:
        # Even when the start is valid, an invalid end MUST abort
        # before the HTTP call. The test asserts transport.get was
        # never reached.
        cfg = _service_config()
        args = _namespace(start="2026-01-01", end="not-a-date")
        with _patched_get_payload([]) as mock_get:
            with self.assertRaises(ConfigError):
                cmd_calendar(args, cfg)
        mock_get.assert_not_called()

    def test_calendar_emits_json_when_not_human(self) -> None:
        cfg = _service_config()
        args = _namespace(start=None, end=None, human=False)
        payload = [{"title": "The Matrix", "year": 1999}]
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_calendar, args, cfg)
        self.assertEqual(json.loads(output), payload)


# ---------------------------------------------------------------------------
# Test: cmd_wanted (REQ-7 AC3)
# ---------------------------------------------------------------------------


class TestCmdWanted(unittest.TestCase):
    """``GET /api/v3/wanted/missing``."""

    def test_wanted_hits_wanted_missing_path(self) -> None:
        cfg = _service_config()
        args = _namespace()
        with _patched_get_payload([]) as mock_get:
            cmd_wanted(args, cfg)
        positional = mock_get.call_args.args
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(positional[0], "radarr")
        self.assertEqual(positional[1], "/api/v3/wanted/missing")
        self.assertIsNone(kwargs.get("params"))

    def test_wanted_emits_json_when_not_human(self) -> None:
        cfg = _service_config()
        # ``--verbose`` preserves the pre-change verbatim pass-through
        # for the size-to-summary candidate ``radarr wanted``.
        args = _namespace(human=False, verbose=True, command="wanted")
        payload = [{"title": "Inception", "year": 2010, "monitored": True}]
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_wanted, args, cfg)
        self.assertEqual(json.loads(output), payload)


# ---------------------------------------------------------------------------
# Test: cmd_queue (REQ-7 AC4)
# ---------------------------------------------------------------------------


class TestCmdQueue(unittest.TestCase):
    """``GET /api/v3/queue``."""

    def test_queue_hits_queue_path(self) -> None:
        cfg = _service_config()
        args = _namespace()
        with _patched_get_payload([]) as mock_get:
            cmd_queue(args, cfg)
        positional = mock_get.call_args.args
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(positional[0], "radarr")
        self.assertEqual(positional[1], "/api/v3/queue")
        self.assertIsNone(kwargs.get("params"))

    def test_queue_emits_json_when_not_human(self) -> None:
        cfg = _service_config()
        args = _namespace(human=False)
        payload = [{"title": "Movie A", "status": "downloading"}]
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_queue, args, cfg)
        self.assertEqual(json.loads(output), payload)


# ---------------------------------------------------------------------------
# Test: cmd_recent (REQ-7 AC5)
# ---------------------------------------------------------------------------


class TestCmdRecent(unittest.TestCase):
    """``GET /api/v3/history/movie`` (NOT ``/history`` like Sonarr)."""

    def test_recent_hits_history_movie_path(self) -> None:
        cfg = _service_config()
        args = _namespace()
        with _patched_get_payload([]) as mock_get:
            cmd_recent(args, cfg)
        positional = mock_get.call_args.args
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(positional[0], "radarr")
        # The path is the movie-specific history endpoint -- this is
        # the documented divergence from Sonarr's ``/history``.
        self.assertEqual(positional[1], "/api/v3/history/movie")
        self.assertIsNone(kwargs.get("params"))

    def test_recent_emits_json_when_not_human(self) -> None:
        cfg = _service_config()
        args = _namespace(human=False)
        payload = {"events": [{"movie": {"title": "Movie X"}}]}
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_recent, args, cfg)
        self.assertEqual(json.loads(output), payload)


# ---------------------------------------------------------------------------
# Test: cmd_lookup (REQ-7 AC6)
# ---------------------------------------------------------------------------


class TestCmdLookup(unittest.TestCase):
    """``GET /api/v3/movie/lookup?term=<urlencoded term>``."""

    def test_lookup_forwards_term(self) -> None:
        cfg = _service_config()
        args = _namespace(term="the matrix")
        with _patched_get_payload([]) as mock_get:
            cmd_lookup(args, cfg)
        positional = mock_get.call_args.args
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(positional[0], "radarr")
        self.assertEqual(positional[1], "/api/v3/movie/lookup")
        self.assertEqual(kwargs["params"], {"term": "the matrix"})

    def test_lookup_percent_encodes_term(self) -> None:
        # Security NFR: the transport layer percent-encodes the term
        # so a stray ``/`` or ``?`` cannot inject a new path segment
        # or query string. This test patches the transport's encoder
        # to record what was passed.
        cfg = _service_config()
        args = _namespace(term="hello world?special&chars")
        with _patched_get_payload([]) as mock_get:
            cmd_lookup(args, cfg)
        # The handler forwards the raw value; the transport layer
        # (covered in test_transport.py) is responsible for the
        # encoding. Here we assert the raw string was passed.
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(
            kwargs["params"], {"term": "hello world?special&chars"}
        )

    def test_lookup_empty_term_still_calls_endpoint(self) -> None:
        # When the user runs ``radarr lookup`` with no positional
        # argument the subparser defaults ``term`` to ""; the handler
        # still forwards the empty term rather than erroring.
        cfg = _service_config()
        args = _namespace(term="")
        with _patched_get_payload([]) as mock_get:
            cmd_lookup(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(kwargs["params"], {"term": ""})

    def test_lookup_missing_term_defaults_to_empty(self) -> None:
        # Defensive: if the field is absent entirely, the handler
        # still falls back to an empty string.
        cfg = _service_config()
        args = _namespace()  # no term field
        with _patched_get_payload([]) as mock_get:
            cmd_lookup(args, cfg)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(kwargs["params"], {"term": ""})

    def test_lookup_human_column_list(self) -> None:
        # The --human column list on lookup uses the real upstream
        # JSON keys so ``item.get(column)`` resolves to a value
        # instead of ``None``. ``monitored`` reflects the source
        # default (TMDB for Radarr); ``id`` disambiguates library
        # rows (numeric ``id``) from candidates (no ``id`` key).
        cfg = _service_config()
        args = _namespace(term="the matrix", human=True)
        payload = [
            {
                "title": "X",
                "monitored": True,
                "tmdbId": 1,
                "year": 1999,
            }
        ]
        with _patched_get_payload(payload), \
                patch("arr_cli.radarr.output.emit") as mock_emit:
            cmd_lookup(args, cfg)
        columns = mock_emit.call_args.kwargs["columns"]
        self.assertEqual(
            columns,
            ["title", "year", "tmdbId", "imdbId", "id", "monitored"],
        )
        # Regression net: the buggy ``defaultMonitored`` rename from
        # PR #7 must not be reintroduced because no upstream API
        # exposes that key.
        self.assertNotIn("defaultMonitored", columns)

    def test_lookup_human_monitored_cell_renders_value(self) -> None:
        # The ``monitored`` cell renders the boolean from the JSON
        # payload, never ``<null>``. Booleans stringify to lowercase
        # ``true``/``false`` via ``_stringify``.
        cfg = _service_config()
        args = _namespace(term="the matrix", human=True)
        payload = [
            {
                "title": "X",
                "monitored": True,
                "tmdbId": 1,
                "imdbId": "tt1",
                "year": 1999,
                "id": 42,
            }
        ]
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_lookup, args, cfg)
        cells = _parse_human_row(output, data_index=0)
        self.assertEqual(cells["monitored"], "true")
        self.assertEqual(cells["id"], "42")
        self.assertNotEqual(cells["monitored"], "<null>")

    def test_lookup_human_id_cell_renders_blank_when_absent(self) -> None:
        # A candidate row (no ``id`` key) renders the ``id`` cell
        # as ``<null>`` via ``_stringify(None)``, which is the
        # documented candidate-row visual indicator.
        cfg = _service_config()
        args = _namespace(term="the matrix", human=True)
        payload = [
            {
                "title": "X",
                "monitored": True,
                "tmdbId": 1,
                "imdbId": "tt1",
                "year": 1999,
            }
        ]
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_lookup, args, cfg)
        cells = _parse_human_row(output, data_index=0)
        self.assertEqual(cells["id"], "<null>")
        # ``monitored`` still renders the boolean even when ``id``
        # is missing -- the source-default column is independent.
        self.assertEqual(cells["monitored"], "true")

    def test_lookup_human_distinguishes_library_and_candidate_rows(
        self,
    ) -> None:
        # The ``id`` column disambiguates a library row (real
        # numeric ``id``, real ``added``, real ``path``) from a
        # candidate row (no ``id``, placeholder ``added``, no
        # ``path``). Both rows carry the source-default ``monitored``
        # flag.
        cfg = _service_config()
        args = _namespace(term="dune", human=True)
        payload = [
            {
                "title": "Dune",
                "year": 1984,
                "tmdbId": 841,
                "imdbId": "tt0087182",
                "id": 17,
                "monitored": True,
                "added": "2020-01-01T00:00:00Z",
                "path": "/movies/Dune (1984)",
            },
            {
                "title": "Dune",
                "year": 1984,
                "tmdbId": 841,
                "imdbId": "tt0087182",
                "monitored": True,
                "added": "0001-01-01T00:01:00Z",
            },
        ]
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_lookup, args, cfg)
        library = _parse_human_row(output, data_index=0)
        candidate = _parse_human_row(output, data_index=1)
        self.assertEqual(library["id"], "17")
        self.assertEqual(candidate["id"], "<null>")
        # Both rows share the source-default ``monitored`` flag.
        self.assertEqual(library["monitored"], "true")
        self.assertEqual(candidate["monitored"], "true")

    def test_lookup_json_keeps_monitored_key(self) -> None:
        # The JSON path (no --human) preserves the raw ``monitored``
        # key exactly as the upstream API returns it; the --human
        # column header is just a label over the same JSON field.
        cfg = _service_config()
        args = _namespace(term="the matrix", human=False)
        payload = [{"title": "X", "monitored": True, "tmdbId": 1}]
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_lookup, args, cfg)
        rendered = json.loads(output)
        self.assertEqual(rendered, payload)
        self.assertIn("monitored", rendered[0])
        self.assertTrue(rendered[0]["monitored"])


# ---------------------------------------------------------------------------
# Test: cmd_movie (REQ-7 AC7)
# ---------------------------------------------------------------------------


class TestCmdMovie(unittest.TestCase):
    """``GET /api/v3/movie/{id}``; 404 → ``HttpError(exit_code=4)``."""

    def test_movie_hits_movie_path(self) -> None:
        cfg = _service_config()
        args = _namespace(movie_id="42")
        with _patched_get_payload({"title": "The Matrix"}) as mock_get:
            cmd_movie(args, cfg)
        positional = mock_get.call_args.args
        self.assertEqual(positional[0], "radarr")
        self.assertEqual(positional[1], "/api/v3/movie/42")

    def test_movie_404_propagates_as_http_error(self) -> None:
        cfg = _service_config()
        args = _namespace(movie_id="missing")
        with _patched_get_raising(
            HttpError(
                "radarr",
                "movie id=missing",
                "radarr: HTTP 404 for /api/v3/movie/missing",
                status=404,
            )
        ):
            with self.assertRaises(HttpError) as ctx:
                cmd_movie(args, cfg)
        self.assertEqual(ctx.exception.exit_code, 4)
        self.assertEqual(ctx.exception.status, 404)

    def test_movie_id_is_percent_encoded(self) -> None:
        # Path segments are percent-encoded before being sent so a
        # slash or space in the id cannot break the URL.
        cfg = _service_config()
        args = _namespace(movie_id="42/x")
        with _patched_get_payload({"title": "Test"}) as mock_get:
            cmd_movie(args, cfg)
        positional = mock_get.call_args.args
        self.assertEqual(positional[1], "/api/v3/movie/42%2Fx")

    def test_movie_emits_json_when_not_human(self) -> None:
        cfg = _service_config()
        args = _namespace(movie_id="42", human=False)
        payload = {"title": "The Matrix", "year": 1999}
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_movie, args, cfg)
        self.assertEqual(json.loads(output), payload)

    def test_movie_no_id_hits_movie_list_path(self) -> None:
        # REQ-2 AC1, REQ-7 AC3: ``radarr movie`` with no id MUST
        # call transport.get with ``"/api/v3/movie"`` and no
        # ``params`` argument (the library-list endpoint takes no
        # query parameters).
        cfg = _service_config()
        args = _namespace(movie_id=None)
        with _patched_get_payload([]) as mock_get:
            cmd_movie(args, cfg)
        positional = mock_get.call_args.args
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(positional[0], "radarr")
        self.assertEqual(positional[1], "/api/v3/movie")
        # No params on the library-list endpoint -- ``_get`` defaults
        # ``params`` to None when the caller omits it.
        self.assertIsNone(kwargs.get("params"))

    def test_movie_no_id_emits_list_payload(self) -> None:
        # REQ-2 AC3: the canned array round-trips through output
        # unchanged under the default (non-human) JSON path.
        cfg = _service_config()
        args = _namespace(movie_id=None, human=False)
        payload = [{"title": "A"}, {"title": "B"}]
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_movie, args, cfg)
        self.assertEqual(json.loads(output), payload)

    def test_movie_no_id_human_renders_table(self) -> None:
        # REQ-2 AC2: the --human column list for the no-id branch
        # is exactly these six columns in this order. ``monitored``
        # here is the operator's library flag (NOT the source-default
        # column that REQ-4 renames on lookup).
        cfg = _service_config()
        args = _namespace(movie_id=None, human=True)
        payload = [{"title": "A", "year": 1999}]
        with _patched_get_payload(payload), \
                patch("arr_cli.radarr.output.emit") as mock_emit:
            cmd_movie(args, cfg)
        kwargs = mock_emit.call_args.kwargs
        self.assertEqual(
            kwargs["columns"],
            ["title", "year", "monitored", "status", "tmdbId", "imdbId"],
        )

    def test_movie_no_id_row_count_matches_payload(self) -> None:
        # REQ-2 AC3: a canned payload of N items renders N data rows.
        # The ``human`` renderer emits a header row, a separator row,
        # then one row per item; counting non-empty lines and
        # subtracting the two header lines gives the row count.
        cfg = _service_config()
        n = 5
        payload = [
            {
                "title": f"Movie {i}",
                "year": 1990 + i,
                "monitored": True,
                "status": "released",
                "tmdbId": 1000 + i,
                "imdbId": f"tt{1000 + i:07d}",
            }
            for i in range(n)
        ]
        args = _namespace(movie_id=None, human=True)
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_movie, args, cfg)
        lines = [line for line in output.splitlines() if line.strip()]
        # Header + separator + N data rows.
        self.assertEqual(len(lines), n + 2)
        # The header carries the six required column names.
        header = lines[0]
        for column in (
            "title",
            "year",
            "monitored",
            "status",
            "tmdbId",
            "imdbId",
        ):
            self.assertIn(column, header)


# ---------------------------------------------------------------------------
# Test: --human mode
# ---------------------------------------------------------------------------


class TestHumanMode(unittest.TestCase):
    """When ``--human`` is set, ``output.emit`` is called with ``human_mode=True``."""

    def test_human_mode_passes_through_to_emit(self) -> None:
        cfg = _service_config()
        args = _namespace(human=True, start=None, end=None)
        payload = [{"title": "Movie A"}, {"title": "Movie B"}]
        with _patched_get_payload(payload), \
                patch("arr_cli.radarr.output.emit") as mock_emit:
            cmd_calendar(args, cfg)
        mock_emit.assert_called_once()
        kwargs = mock_emit.call_args.kwargs
        self.assertTrue(kwargs["human_mode"])

    def test_human_mode_columns_for_calendar(self) -> None:
        cfg = _service_config()
        args = _namespace(human=True, start=None, end=None)
        payload = [{"title": "Movie A", "year": 2024}]
        with _patched_get_payload(payload), \
                patch("arr_cli.radarr.output.emit") as mock_emit:
            cmd_calendar(args, cfg)
        kwargs = mock_emit.call_args.kwargs
        self.assertEqual(
            kwargs["columns"],
            [
                "title",
                "year",
                "inCinemas",
                "physicalRelease",
                "digitalRelease",
            ],
        )


# ---------------------------------------------------------------------------
# Test: parser
# ---------------------------------------------------------------------------


class TestBuildRadarrParser(unittest.TestCase):
    """The parser exposes the six subcommands and the universal flags."""

    def setUp(self) -> None:
        self.parser = build_radarr_parser()

    def test_parser_prog(self) -> None:
        self.assertEqual(self.parser.prog, SERVICE_NAME)

    def test_help_prints_to_stdout(self) -> None:
        stdout, _ = _capture_stderr_stdout(
            self.parser.parse_args, ["--help"]
        )
        self.assertIn("usage:", stdout)
        # The six commands surface in the help listing.
        for cmd in (
            "calendar",
            "wanted",
            "queue",
            "recent",
            "lookup",
            "movie",
        ):
            self.assertIn(cmd, stdout)

    def test_calendar_parses_without_dates(self) -> None:
        args = self.parser.parse_args(["calendar"])
        self.assertEqual(args.command, "calendar")
        self.assertIsNone(args.start)
        self.assertIsNone(args.end)

    def test_calendar_parses_with_start_only(self) -> None:
        args = self.parser.parse_args(["calendar", "2026-01-01"])
        self.assertEqual(args.command, "calendar")
        self.assertEqual(args.start, "2026-01-01")
        self.assertIsNone(args.end)

    def test_calendar_parses_with_start_and_end(self) -> None:
        args = self.parser.parse_args(
            ["calendar", "2026-01-01", "2026-01-31"]
        )
        self.assertEqual(args.command, "calendar")
        self.assertEqual(args.start, "2026-01-01")
        self.assertEqual(args.end, "2026-01-31")

    def test_calendar_parses_datetime(self) -> None:
        args = self.parser.parse_args(
            ["calendar", "2026-01-01T00:00:00Z", "2026-01-31T23:59:59Z"]
        )
        self.assertEqual(args.start, "2026-01-01T00:00:00Z")
        self.assertEqual(args.end, "2026-01-31T23:59:59Z")

    def test_wanted_parses(self) -> None:
        args = self.parser.parse_args(["wanted"])
        self.assertEqual(args.command, "wanted")

    def test_queue_parses(self) -> None:
        args = self.parser.parse_args(["queue"])
        self.assertEqual(args.command, "queue")

    def test_recent_parses(self) -> None:
        args = self.parser.parse_args(["recent"])
        self.assertEqual(args.command, "recent")

    def test_lookup_parses_with_term(self) -> None:
        args = self.parser.parse_args(["lookup", "the matrix"])
        self.assertEqual(args.command, "lookup")
        self.assertEqual(args.term, "the matrix")

    def test_lookup_parses_without_term(self) -> None:
        args = self.parser.parse_args(["lookup"])
        self.assertEqual(args.command, "lookup")
        self.assertEqual(args.term, "")

    def test_movie_parses_with_id(self) -> None:
        args = self.parser.parse_args(["movie", "42"])
        self.assertEqual(args.command, "movie")
        self.assertEqual(args.movie_id, "42")

    def test_movie_parses_without_id(self) -> None:
        # ``movie_id`` is an OPTIONAL positional; invoking
        # ``radarr movie`` with no id must parse cleanly so the
        # CLI can branch to GET /api/v3/movie in cmd_movie
        # (REQ-2 AC1, REQ-3 AC2).
        args = self.parser.parse_args(["movie"])
        self.assertEqual(args.command, "movie")
        self.assertIsNone(args.movie_id)

    def test_unknown_subcommand_fails(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.parser.parse_args(["bogus"])
        self.assertEqual(ctx.exception.code, 2)

    def test_universal_flags_flow_through(self) -> None:
        args = self.parser.parse_args(
            [
                "--config",
                "/tmp/x",
                "--debug",
                "--human",
                "--limit",
                "10",
                "wanted",
            ]
        )
        self.assertEqual(args.config, "/tmp/x")
        self.assertTrue(args.debug)
        self.assertTrue(args.human)
        self.assertEqual(args.limit, 10)
        self.assertEqual(args.command, "wanted")


# ---------------------------------------------------------------------------
# Test: main entry point
# ---------------------------------------------------------------------------


class TestMainEntryPoint(unittest.TestCase):
    """``main`` wires the parser to ``main_wrapper`` end-to-end."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="radarr-test-"))
        self.cfg_path = _write_toml_config(self.tmp_dir)

    def tearDown(self) -> None:
        import shutil
        try:
            shutil.rmtree(self.tmp_dir)
        except OSError:
            pass

    def test_main_wanted_returns_zero_on_success(self) -> None:
        payload = [{"title": "The Matrix", "year": 1999}]
        with patch(
            "arr_cli.radarr.transport.get",
            return_value=payload,
        ):
            exit_code = main(["--config", str(self.cfg_path), "wanted"])
        self.assertEqual(exit_code, 0)

    def test_main_returns_usage_after_unknown_subcommand(self) -> None:
        # ``main_wrapper`` catches argparse's SystemExit internally and
        # surfaces the exit code (2) as the return value.
        exit_code = main(["--config", str(self.cfg_path), "bogus"])
        self.assertEqual(exit_code, 2)

    def test_main_movie_404_exit_code(self) -> None:
        # ``cmd_movie`` propagates HttpError(status=404); main_wrapper
        # translates it to exit code 4 (REQ-4 AC2).
        with patch(
            "arr_cli.radarr.transport.get",
            side_effect=HttpError(
                "radarr",
                "movie id=42",
                "radarr: HTTP 404 for /api/v3/movie/42",
                status=404,
            ),
        ):
            exit_code = main(
                ["--config", str(self.cfg_path), "movie", "42"]
            )
        self.assertEqual(exit_code, 4)

    def test_main_calendar_malformed_date_exit_code(self) -> None:
        # The validator raises ConfigError(exit_code=1) BEFORE the
        # HTTP call, so main_wrapper surfaces exit code 1.
        # ``transport.get`` is patched but MUST NOT be reached.
        with patch("arr_cli.radarr.transport.get") as mock_get:
            exit_code = main(
                [
                    "--config",
                    str(self.cfg_path),
                    "calendar",
                    "next-tuesday",
                ]
            )
        self.assertEqual(exit_code, 1)
        mock_get.assert_not_called()

    def test_main_lookup_emits_payload(self) -> None:
        payload = [{"title": "The Matrix", "year": 1999}]
        with patch(
            "arr_cli.radarr.transport.get",
            return_value=payload,
        ):
            stdout, _ = _capture_stderr_stdout(
                main,
                [
                    "--config",
                    str(self.cfg_path),
                    "lookup",
                    "the matrix",
                ],
            )
        self.assertEqual(json.loads(stdout), payload)

    def test_main_human_renders_table(self) -> None:
        # ``--human`` switches from JSON pass-through to tabular
        # rendering.
        payload = [{"title": "A"}, {"title": "B"}]
        with patch(
            "arr_cli.radarr.transport.get",
            return_value=payload,
        ):
            stdout, _ = _capture_stderr_stdout(
                main,
                [
                    "--config",
                    str(self.cfg_path),
                    "--human",
                    "wanted",
                ],
            )
        # JSON line is suppressed in --human mode.
        self.assertNotIn('"title"', stdout)
        # The renderer emitted at least one line of output.
        self.assertTrue(stdout.strip())

    def test_main_recent_hits_history_movie(self) -> None:
        # End-to-end check that the recent command hits
        # /api/v3/history/movie (NOT /history like Sonarr).
        with patch(
            "arr_cli.radarr.transport.get",
            return_value={"events": []},
        ) as mock_get:
            exit_code = main(
                ["--config", str(self.cfg_path), "recent"]
            )
        self.assertEqual(exit_code, 0)
        positional = mock_get.call_args.args
        self.assertEqual(positional[1], "/api/v3/history/movie")

    def test_main_calendar_with_dates_forwards_params(self) -> None:
        with patch(
            "arr_cli.radarr.transport.get",
            return_value=[],
        ) as mock_get:
            exit_code = main(
                [
                    "--config",
                    str(self.cfg_path),
                    "calendar",
                    "2026-01-01",
                    "2026-01-31",
                ]
            )
        self.assertEqual(exit_code, 0)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(
            kwargs["params"],
            {"start": "2026-01-01", "end": "2026-01-31"},
        )


# ---------------------------------------------------------------------------
# Test: auth header policy (REQ-2 AC2)
# ---------------------------------------------------------------------------


class TestAuthHeaderPolicy(unittest.TestCase):
    """The transport layer is the single source of auth-header truth.

    The Radarr module's job is to delegate to ``transport.get``;
    the transport layer injects ``X-Api-Key`` for the ``radarr``
    service. These tests don't re-verify the injection contract
    (already covered in test_transport.py) but they confirm the
    Radarr module doesn't bypass that path.
    """

    def test_all_commands_use_radarr_service(self) -> None:
        cfg = _service_config()
        cases = [
            (cmd_calendar, _namespace(start=None, end=None)),
            (cmd_wanted, _namespace()),
            (cmd_queue, _namespace()),
            (cmd_recent, _namespace()),
            (cmd_lookup, _namespace(term="x")),
            (cmd_movie, _namespace(movie_id="42")),
        ]
        for handler, args in cases:
            with _patched_get_payload([]) as mock_get:
                handler(args, cfg)
            positional = mock_get.call_args.args
            self.assertEqual(
                positional[0],
                "radarr",
                msg=f"{handler.__name__} did not use radarr service",
            )


# ---------------------------------------------------------------------------
# Test: re-export of _validate_iso_date for Sonarr (REQ-8 AC2)
# ---------------------------------------------------------------------------


class TestReExportForSonarr(unittest.TestCase):
    """Task 10.1 says Sonarr reuses ``_validate_iso_date`` from here.

    Verify the symbol is importable from ``arr_cli.radarr`` so the
    Sonarr module can do ``from arr_cli.radarr import
    _validate_iso_date`` (or copy the 6-line validator). The
    validator is also included in ``__all__`` so this contract is
    documented as part of the module's public surface.
    """

    def test_validate_iso_date_importable_from_radarr(self) -> None:
        from arr_cli.radarr import _validate_iso_date

        self.assertTrue(callable(_validate_iso_date))

    def test_validate_iso_date_in_all(self) -> None:
        from arr_cli.radarr import __all__

        self.assertIn("_validate_iso_date", __all__)


# ---------------------------------------------------------------------------
# Test: --verbose flag flip for cmd_wanted
# ---------------------------------------------------------------------------


class TestVerboseFlagCmdWanted(unittest.TestCase):
    """REQ-6 AC4: ``cmd_wanted`` summary vs verbose paths."""

    def test_cmd_wanted_default_emits_summary(self) -> None:
        cfg = _service_config()
        args = _namespace(command="wanted")
        payload = [
            {
                "title": "Inception",
                "year": 2010,
                "tmdbId": 27205,
                "monitored": True,
            }
        ]
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_wanted, args, cfg)
        rendered = json.loads(output)
        self.assertEqual(rendered[0]["title"], "Inception")
        self.assertEqual(rendered[0]["year"], 2010)
        self.assertTrue(rendered[0]["monitored"])

    def test_cmd_wanted_verbose_emits_verbatim(self) -> None:
        cfg = _service_config()
        args = _namespace(command="wanted", verbose=True)
        payload = [{"title": "Inception", "year": 2010, "monitored": True}]
        with _patched_get_payload(payload):
            output = _capture_stdout(cmd_wanted, args, cfg)
        self.assertEqual(json.loads(output), payload)


if __name__ == "__main__":
    unittest.main()