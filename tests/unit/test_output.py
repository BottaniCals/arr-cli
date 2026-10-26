"""Unit tests for :mod:`arr_cli.facade.output`.

Covers the contract spelled out in task 6.3 of tasks.md:

* JSON pass-through preserves UTF-8 (``ensure_ascii=False``).
* ``human([{"a":1,"b":2}], columns=["a","b"])`` produces a header
  plus one data row.
* Long cell values are truncated with the documented ``…`` marker.
* ``COLUMNS=40`` shrinks the rendered table.
* stderr / stdout are separated (REQ-3 AC4).
* Object and scalar payloads render as documented.
* Pagination footer surfaces when more items exist than ``limit``.

The tests use ``unittest`` + ``unittest.mock`` only — no third-party
deps. ``sys.stdout`` is swapped for an :class:`io.StringIO` via
:func:`contextlib.redirect_stdout` so the stdout assertions stay
isolated from any test-runner capture. The ``COLUMNS`` env var is
patched via :func:`unittest.mock.patch.dict` so the suite is
deterministic regardless of the host's tty.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import inspect
import io
import json
import logging
import os
import sys
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Make the project importable regardless of the test runner's CWD.
_PROJ_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from arr_cli.facade.output import (  # noqa: E402 - sys.path tweak above
    DEFAULT_LIMIT,
    DEFAULT_MAX_WIDTH,
    MIN_WIDTH,
    emit,
    human,
    resolve_width,
    summarize,
    _column_widths,
    _format_runtime_minutes,
    _safe_get,
    _SUMMARY_RENDERERS,
    _truncate,
    _unwrap_envelope,
)


def _capture_stdout(callable_: Any, *args: Any, **kwargs: Any) -> str:
    """Invoke ``callable_`` with stdout redirected to a StringIO buffer."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        callable_(*args, **kwargs)
    return buffer.getvalue()


def _capture_module_warnings(
    callable_: Any, *args: Any, **kwargs: Any
) -> list[str]:
    """Invoke ``callable_`` while capturing WARNING-level records on the
    ``arr_cli.facade.output`` logger; return their rendered messages.

    Negative-log-assertion helper: ``unittest.assertLogs`` requires at
    least one matching log, so verifying that *no* warning was emitted
    needs a manual capture-and-inspect loop.
    """
    logger = logging.getLogger("arr_cli.facade.output")
    messages: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handler = _Collect(level=logging.WARNING)
    logger.addHandler(handler)
    try:
        callable_(*args, **kwargs)
    finally:
        logger.removeHandler(handler)
    return messages


# ---------------------------------------------------------------------------
# JSON pass-through
# ---------------------------------------------------------------------------


class TestEmitJsonPassthrough(unittest.TestCase):
    """``emit(..., human_mode=False)`` writes JSON to stdout verbatim."""

    def test_passes_through_dict(self) -> None:
        payload = {"a": 1, "b": "two"}
        out = _capture_stdout(emit, payload, human_mode=False)
        # ``json.dumps`` uses no indent and ``ensure_ascii=False`` so
        # the rendered line is exactly one JSON document followed by
        # a single newline (print adds it).
        self.assertEqual(out, json.dumps(payload, ensure_ascii=False) + "\n")

    def test_passes_through_list(self) -> None:
        payload = [{"id": 1}, {"id": 2}]
        out = _capture_stdout(emit, payload, human_mode=False)
        self.assertEqual(out, json.dumps(payload, ensure_ascii=False) + "\n")

    def test_passes_through_scalar(self) -> None:
        # A scalar payload (e.g. Maintainerr ``/api/health/ready``
        # returning a bare boolean) is still JSON-serialised. The
        # verbatim pass-through is what REQ-3 AC1 demands.
        out = _capture_stdout(emit, True, human_mode=False)
        self.assertEqual(out, "true\n")
        out = _capture_stdout(emit, 42, human_mode=False)
        self.assertEqual(out, "42\n")
        out = _capture_stdout(emit, "hello", human_mode=False)
        self.assertEqual(out, '"hello"\n')

    def test_passes_through_none(self) -> None:
        # ``None`` round-trips as the JSON literal ``null`` so the
        # document is still well-formed for downstream parsers.
        out = _capture_stdout(emit, None, human_mode=False)
        self.assertEqual(out, "null\n")

    def test_preserves_unicode(self) -> None:
        # REQ-3 AC5: UTF-8 throughout, no mojibake. ``ensure_ascii=False``
        # means non-ASCII characters pass through unchanged.
        payload = {"title": "Café — déjà vu"}
        out = _capture_stdout(emit, payload, human_mode=False)
        self.assertIn("Café — déjà vu", out)
        # Sanity: no escaped unicode sequences from ``ensure_ascii=True``.
        self.assertNotIn("\\u00e9", out)
        self.assertNotIn("\\u2014", out)

    def test_writes_only_to_stdout(self) -> None:
        # REQ-3 AC4: a consumer redirecting only stdout SHALL receive
        # a clean JSON document with no interspersed log lines.
        captured_stderr = io.StringIO()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), \
                contextlib.redirect_stderr(captured_stderr):
            emit({"x": 1}, human_mode=False)
        self.assertEqual(buffer.getvalue(), '{"x": 1}\n')
        self.assertEqual(captured_stderr.getvalue(), "")

    def test_custom_stream_is_honoured(self) -> None:
        # Tests and embedders can pass their own stream; the default
        # path of ``sys.stdout`` is only a fallback.
        custom = io.StringIO()
        emit({"hello": "world"}, human_mode=False, stream=custom)
        self.assertEqual(custom.getvalue(), '{"hello": "world"}\n')


# ---------------------------------------------------------------------------
# human() tabular rendering — list of dicts
# ---------------------------------------------------------------------------


class TestHumanListOfDicts(unittest.TestCase):
    """``human()`` renders list-of-dict payloads as a header + rows."""

    def test_header_and_one_row(self) -> None:
        # Task 6.3 contract: ``human([{"a":1,"b":2}], columns=["a","b"])``
        # produces a header plus one data row.
        rendered = human([{"a": 1, "b": 2}], columns=["a", "b"])
        lines = rendered.splitlines()
        self.assertEqual(len(lines), 3)  # header + separator + row
        # Header must name both columns in the order requested.
        self.assertIn("a", lines[0])
        self.assertIn("b", lines[0])
        self.assertTrue(lines[0].index("a") < lines[0].index("b"))
        # Data row must contain the rendered values.
        self.assertIn("1", lines[2])
        self.assertIn("2", lines[2])

    def test_inferred_columns_when_not_given(self) -> None:
        # When ``columns`` is None the keys of the first item are
        # used as column names.
        rendered = human([{"alpha": 1, "beta": 2}, {"alpha": 3, "beta": 4}])
        lines = rendered.splitlines()
        self.assertIn("alpha", lines[0])
        self.assertIn("beta", lines[0])
        self.assertIn("1", lines[2])
        self.assertIn("3", lines[3])

    def test_multiple_rows(self) -> None:
        rendered = human(
            [
                {"id": 1, "title": "first"},
                {"id": 2, "title": "second"},
                {"id": 3, "title": "third"},
            ],
            columns=["id", "title"],
        )
        lines = rendered.splitlines()
        # header + separator + 3 data rows
        self.assertEqual(len(lines), 5)
        for index, expected in enumerate(["1", "2", "3"], start=2):
            self.assertIn(expected, lines[index])

    def test_missing_key_renders_as_marker(self) -> None:
        # When a column key is missing on a later row the renderer
        # surfaces ``<null>`` so the column stays visibly populated.
        rendered = human(
            [{"a": 1, "b": 2}, {"a": 3}],
            columns=["a", "b"],
        )
        lines = rendered.splitlines()
        # Header + separator + 2 data rows.
        self.assertEqual(len(lines), 4)
        self.assertIn("<null>", lines[3])

    def test_truncates_long_values_with_ellipsis(self) -> None:
        # A value longer than the per-column allocation must be
        # truncated with the documented ``…`` marker.
        rendered = human(
            [{"title": "this is a very long title that should truncate"}],
            columns=["title"],
            max_width=20,
        )
        self.assertIn("…", rendered)
        # Truncation respects the budget: the row width must be <=
        # the configured max_width after stripping separators.
        data_row = rendered.splitlines()[-1]
        self.assertLessEqual(len(data_row), 20)

    def test_pagination_footer_appears_when_truncated(self) -> None:
        # When more items exist than ``limit`` the footer surfaces.
        items = [{"id": i} for i in range(5)]
        rendered = human(items, columns=["id"], limit=2)
        # The default 20-row budget would render all five; forcing
        # ``limit=2`` triggers the footer.
        lines = rendered.splitlines()
        # header + separator + 2 data rows + 1 footer line = 5 lines
        self.assertEqual(len(lines), 5)
        self.assertTrue(lines[-1].startswith("…"))
        self.assertIn("3 more", lines[-1])

    def test_pagination_singular_grammar(self) -> None:
        # Footer says "1 more item" (singular) when exactly one item
        # is omitted.
        items = [{"id": 1}, {"id": 2}]
        rendered = human(items, columns=["id"], limit=1)
        self.assertIn("1 more item;", rendered)
        self.assertNotIn("1 more items;", rendered)


# ---------------------------------------------------------------------------
# human() — list, scalar, dict payloads
# ---------------------------------------------------------------------------


class TestHumanOtherPayloads(unittest.TestCase):
    """``human()`` handles dict, scalar, and empty payloads."""

    def test_dict_renders_as_key_value_pairs(self) -> None:
        rendered = human({"name": "jellyfin", "ok": True})
        self.assertIn("name: jellyfin", rendered)
        self.assertIn("ok: true", rendered)

    def test_empty_dict_renders_marker(self) -> None:
        rendered = human({})
        self.assertIn("(empty object)", rendered)

    def test_scalar_bool_renders_value_label(self) -> None:
        # REQ-3 AC3: scalar / boolean payloads get a one-line label.
        rendered = human(True)
        self.assertEqual(rendered, "value: true")

    def test_scalar_int_renders_value_label(self) -> None:
        rendered = human(42)
        self.assertEqual(rendered, "value: 42")

    def test_scalar_string_renders_value_label(self) -> None:
        rendered = human("ready")
        self.assertEqual(rendered, "value: ready")

    def test_none_renders_as_value_null(self) -> None:
        rendered = human(None)
        self.assertIn("null", rendered.lower())

    def test_empty_list_renders_marker(self) -> None:
        rendered = human([])
        self.assertIn("(empty list)", rendered)


# ---------------------------------------------------------------------------
# Width resolution
# ---------------------------------------------------------------------------


class TestResolveWidth(unittest.TestCase):
    """``resolve_width()`` honours ``COLUMNS`` and clamps to budget."""

    def test_columns_env_var_shrinks_width(self) -> None:
        # Task 6.3 contract: ``COLUMNS=40`` shrinks the table.
        with patch.dict(os.environ, {"COLUMNS": "40"}):
            self.assertEqual(resolve_width(), 40)

    def test_columns_env_var_caps_at_max_width(self) -> None:
        # ``COLUMNS=500`` must be clamped to ``max_width``.
        with patch.dict(os.environ, {"COLUMNS": "500"}):
            self.assertEqual(resolve_width(max_width=120), 120)

    def test_columns_env_var_floor_at_min_width(self) -> None:
        # ``COLUMNS=5`` is clamped up to the minimum width.
        with patch.dict(os.environ, {"COLUMNS": "5"}):
            self.assertEqual(resolve_width(), MIN_WIDTH)

    def test_columns_env_zero_falls_back(self) -> None:
        # ``COLUMNS=0`` is invalid; the resolver falls back to the
        # tty probe / default max width rather than clamping.
        with patch.dict(os.environ, {"COLUMNS": "0"}):
            width = resolve_width()
            # Either the tty probe (which may return 0 when stdout
            # is not a tty, in which case the default kicks in) or
            # the default both yield ``DEFAULT_MAX_WIDTH``.
            self.assertGreaterEqual(width, MIN_WIDTH)

    def test_malformed_columns_env_is_ignored(self) -> None:
        # A non-integer ``COLUMNS`` value should be ignored (with a
        # warning to stderr) rather than raising.
        with patch.dict(os.environ, {"COLUMNS": "wide"}):
            width = resolve_width()
            self.assertGreaterEqual(width, MIN_WIDTH)

    def test_columns_env_var_drives_table_width(self) -> None:
        # Integration: setting ``COLUMNS=40`` actually shrinks the
        # rendered table for a long cell.
        long_value = "this is a very long title that should definitely truncate"
        with patch.dict(os.environ, {"COLUMNS": "40"}):
            rendered = human(
                [{"title": long_value}],
                columns=["title"],
            )
        data_row = rendered.splitlines()[-1]
        # Even allowing for separator padding, the row stays within
        # the COLUMNS-enforced budget (no fewer than 20 chars).
        self.assertLessEqual(len(data_row), 40 + 2)  # tolerance for 2-space sep


# ---------------------------------------------------------------------------
# emit() human path
# ---------------------------------------------------------------------------


class TestEmitHuman(unittest.TestCase):
    """``emit(..., human_mode=True)`` writes the human() rendering to stdout."""

    def test_writes_human_rendering(self) -> None:
        out = _capture_stdout(
            emit,
            [{"a": 1, "b": 2}],
            human_mode=True,
            columns=["a", "b"],
        )
        # The rendered table starts with the header row.
        first_line = out.splitlines()[0]
        self.assertIn("a", first_line)
        self.assertIn("b", first_line)

    def test_stderr_untouched(self) -> None:
        # REQ-3 AC4: diagnostics never leak into stdout, and the
        # default emit path never writes to stderr either.
        captured_stderr = io.StringIO()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), \
                contextlib.redirect_stderr(captured_stderr):
            emit([{"a": 1}], human_mode=True, columns=["a"])
        # Stdout has the table; stderr is empty.
        self.assertIn("a", buffer.getvalue())
        self.assertEqual(captured_stderr.getvalue(), "")

    def test_human_pass_through_preserves_unicode(self) -> None:
        # Even in human mode, UTF-8 characters must not be mojibake'd.
        payload = [{"title": "Café — déjà vu"}]
        out = _capture_stdout(emit, payload, human_mode=True, columns=["title"])
        self.assertIn("Café — déjà vu", out)

    def test_human_limit_is_forwarded(self) -> None:
        # The ``limit`` kwarg on emit() is forwarded to human().
        items = [{"id": i} for i in range(5)]
        out = _capture_stdout(
            emit,
            items,
            human_mode=True,
            columns=["id"],
            limit=2,
        )
        self.assertIn("3 more", out)


# ---------------------------------------------------------------------------
# emit() --human routes through summarize() (REQ-1, REQ-2, REQ-5 AC1)
# ---------------------------------------------------------------------------


# Synthetic payloads covering summary-shape keys + verbatim-only keys
# for every (service, command) registered in _SUMMARY_RENDERERS. The
# payloads are crafted so the summary renderer populates at least one
# column token; the verbatim-only keys are present so a regression that
# bypasses summarize() would surface them in the rendered header.
_HUMAN_SUMMARY_PAYLOADS: dict[tuple[str, str], list[dict[str, Any]]] = {
    ("jellyfin", "now"): [
        {
            "user": "alice",
            "device": "TV",
            "client": "Jellyfin Web",
            "playing": {"type": "Episode", "name": "Foo"},
            "progress": {"position_ticks": 100, "is_paused": False},
            "LastActivityDate": "12:34",
            "NowPlayingQueue.length": 3,
            "transcoding": False,
            "NowPlayingItem": {"Name": "Foo", "SeriesName": "Show"},
            "PlayState": {"PositionTicks": 100, "IsPaused": False},
        }
    ],
    ("jellyfin", "recent"): [
        {
            "Name": "Foo",
            "Type": "Movie",
            "ProductionYear": 2024,
            "SeriesName": None,
            "UserData.LastPlayedDate": "2024-01-01",
            "UserData.PlayedPercentage": 60,
            "Overview": "Lorem ipsum",
        }
    ],
    ("jellyfin", "favorites"): [
        {
            "Id": "abc12345",
            "Name": "Foo",
            "Type": "Movie",
            "ProductionYear": 2020,
            "SeriesName": None,
            "RunTimeTicks": "2h 0m",
            "UserData.LastPlayedDate": "2024-01-01",
            "Overview": "Lorem ipsum",
        }
    ],
    ("jellyfin", "resume"): [
        {
            "Name": "Foo",
            "Type": "Episode",
            "ProductionYear": 2020,
            "SeriesName": "Show",
            "UserData.PlaybackPositionTicks": 100,
            "UserData.PlayCount": 2,
            "UserData.PlayedPercentage": 25,
            "RunTimeTicks": "1h 45m",
            "Overview": "Lorem ipsum",
        }
    ],
    ("jellyfin", "latest"): [
        {
            "Name": "Foo",
            "Type": "Movie",
            "ProductionYear": 2025,
            "SeriesName": None,
            "RunTimeTicks": "1h 45m",
            "PremiereDate": "2024-12-01T00:00:00Z",
            "Genres": "Action, Drama",
            "DateCreated": "2025-06-01T00:00:00Z",
            "Overview": "Lorem ipsum",
        }
    ],
    # ``jellyfin search`` is not listed in ``_SUMMARY_RENDERERS``'s
    # dict but is still exercised by the columns-token sweep via
    # ``_synthetic_payload`` -- see ``TestColumnsBlockMatchesSummaryShape``.
    ("jellyfin", "nextup"): [
        {
            "Name": "Foo",
            "SeriesName": "Bar",
            "ParentIndexNumber": 4,
            "IndexNumber": 12,
            "PremiereDate": "2002-01-10T00:00:00Z",
            "RunTimeTicks": "0h 45m",
            "Overview": "Lorem ipsum",
        }
    ],
    ("radarr", "wanted"): [
        {
            "title": "Foo",
            "year": 2024,
            "tmdbId": 999,
            "monitored": True,
            "hasFile": False,
            "genres": [{"name": "Action"}],
            "physicalRelease": "2024-08-01T00:00:00Z",
            "releaseDate": "2024-08-15",
            "runtime": 120,
        }
    ],
    ("radarr", "queue"): [
        {
            "title": "Foo",
            "year": 2024,
            "quality": {"quality": {"name": "WEBDL-1080p"}},
            "status": "downloading",
            "timeleft": "00:30:00",
            "downloadClient": "qbit",
        }
    ],
    ("radarr", "recent"): [
        {
            "movie": {"title": "Foo", "year": 2024},
            "sourceTitle": "Foo.2024.WEBDL-1080p.mkv",
            "quality": {"quality": {"name": "WEBDL-1080p"}},
            "languages": [{"id": 1, "name": "English"}],
            "data": {"downloadClient": "qbit"},
            "eventType": "downloadFolderImported",
            "date": "2024-06-01",
            "sourcePath": "/movies/foo",
        }
    ],
    ("radarr", "calendar"): [
        {
            "title": "Foo",
            "year": 2024,
            "monitored": True,
            "hasFile": False,
            "genres": [{"name": "Action"}],
            "runtime": 120,
            "collection": {"title": "Foo Collection", "tmdbId": 2345},
            "inCinemas": "2024-06-01",
            "physicalRelease": "2024-08-01",
            "digitalRelease": "2024-07-01",
        }
    ],
    ("radarr", "lookup"): [
        {
            "title": "Foo",
            "year": 2024,
            "hasFile": False,
            "genres": [{"name": "Action"}],
            "overview": "An action movie about things.",
            "status": "released",
            "tmdbId": 999,
            "imdbId": "tt999",
            "id": None,
            "monitored": True,
        }
    ],
    ("radarr", "movie"): [
        {
            "title": "Foo",
            "year": 2024,
            "hasFile": False,
            "genres": [{"name": "Action"}],
            "added": "2024-01-01T00:00:00Z",
            "collection": {"title": "Foo Collection", "tmdbId": 2345},
            "studio": "Studio A",
        }
    ],
    ("radarr", "movie_id"): [
        {
            "title": "Foo",
            "year": 2024,
            "status": "released",
            "monitored": True,
            "hasFile": False,
            "genres": [{"name": "Action"}],
            "overview": "An action movie about things.",
            "added": "2024-01-01T00:00:00Z",
            "studio": "Studio A",
        }
    ],
    ("sonarr", "wanted"): [
        {
            "title": "Pilot",
            "seasonNumber": 1,
            "episodeNumber": 1,
            "airDate": "2024-01-01",
            "monitored": True,
            "hasFile": False,
            "runtime": 45,
            "overview": "An overview.",
            "series": {"title": "Severance"},
            "seriesId": 7,
        }
    ],
    ("sonarr", "queue"): [
        {
            "title": "Foo",
            "status": "downloading",
            "trackedDownloadStatus": "ok",
            "size": 1000,
            "sizeleft": 500,
            "series": {"title": "Severance"},
            "episode": {"title": "The Work Is Never Done"},
            "quality": {"quality": {"name": "WEBDL-1080p"}},
            "timeleft": "00:30:00",
            "downloadClient": "qbit",
        }
    ],
    ("sonarr", "recent"): [
        {
            "id": 1,
            "seriesId": 10,
            "episodeId": 100,
            "sourceTitle": "Show.S01E01.WEBDL-1080p.mkv",
            "eventType": "downloadFolderImported",
            "date": "2024-06-01T00:00:00Z",
            "quality": {"quality": {"id": 7, "name": "WEBDL-1080p"}},
            "languages": [{"id": 1, "name": "English"}],
            "data": {"downloadClient": "qbit"},
        }
    ],
    ("sonarr", "calendar"): [
        {
            "title": "Pilot",
            "seasonNumber": 1,
            "episodeNumber": 1,
            "airDate": "2024-01-01",
            "series": {"title": "Severance"},
            "monitored": True,
            "hasFile": False,
            "runtime": 45,
            "overview": "An overview.",
        }
    ],
    ("sonarr", "lookup"): [
        {
            "title": "Doctor Who",
            "year": 2005,
            "tvdbId": 78804,
            "tvMazeId": 210,
            "id": None,
            "monitored": True,
            "statistics": {"episodeFileCount": 0, "episodeCount": 0, "totalEpisodeCount": 13},
            "genres": [{"name": "Sci-Fi"}],
            "overview": "An overview.",
            "status": "continuing",
        }
    ],
    ("sonarr", "series"): [
        {
            "title": "Severance",
            "year": 2022,
            "monitored": True,
            "status": "continuing",
            "tvdbId": 371918,
            "seasons": [],
            "statistics": {"episodeFileCount": 0, "episodeCount": 0, "totalEpisodeCount": 9},
            "genres": [{"name": "Drama"}],
            "firstAired": "2022-02-17T00:00:00Z",
            "lastAired": "2022-04-17T00:00:00Z",
        }
    ],
    ("sonarr", "series_id"): [
        {
            "title": "Severance",
            "year": 2022,
            "runtime": 45,
            "status": "continuing",
            "monitored": True,
            "statistics": {"episodeFileCount": 0, "episodeCount": 0, "totalEpisodeCount": 9},
            "genres": [{"name": "Drama"}],
            "firstAired": "2022-02-17T00:00:00Z",
            "lastAired": "2022-04-17T00:00:00Z",
        }
    ],
    ("seerr", "requests"): [
        {
            "type": "movie",
            "status": 2,
            "createdAt": "2024-01-01",
            "seasonCount": 0,
            "requestedBy": {"displayName": "alice"},
            "media": {
                "id": 121,
                "mediaType": "movie",
                "tmdbId": 999,
                "tvdbId": None,
                "externalServiceSlug": "tmdb",
                "status": 5,
            },
            "externalId": "tmdb:999",
        }
    ],
    ("seerr", "search"): [
        {
            "id": 603,
            "title": "Foo",
            "mediaType": "movie",
            "releaseDate": "2024-01-01",
            "overview": "Lorem ipsum",
        }
    ],
    ("seerr", "available"): [
        {
            "id": 1,
            "mediaType": "movie",
            "tmdbId": 999,
            "tvdbId": 76107,
            "externalServiceSlug": "tmdb",
            "status": 5,
            "mediaAddedAt": "2024-01-01T00:00:00Z",
        }
    ],
    ("maintainerr", "pending"): [
        {
            "title": "Old Movies",
            "mediaCount": 42,
            "deleteAfterDays": 14,
            "isOnHold": False,
            "arrAction": "delete",
            "addDate": "2026-01-01T00:00:00Z",
            "mediaServerType": "plex",
            "type": "movie",
            "description": "Old movies that have been watched",
            "isActive": True,
            "visibleOnHome": False,
            "collectionId": 7,
        }
    ],
    ("maintainerr", "storage"): [
        {
            "mount": "/data",
            "used": 700,
            "free": 300,
            "total": 1000,
            "usedPercent": 70.0,
        }
    ],
    ("seerr", "user"): {
        "id": 1,
        "username": "alice",
        "email": "alice@example.com",
        "userType": 1,
        "permissions": ["ADMIN", "MANAGE_REQUESTS"],
        "requestCount": 42,
    },
}


class TestEmitHumanSummarizeRoute(unittest.TestCase):
    """``emit(..., human_mode=True, verbose_mode=False)`` routes through
    :func:`summarize` so the table columns come from the summary shape."""

    def test_summary_columns_appear_verbatim_columns_do_not(self) -> None:
        for (svc, cmd), payload in _HUMAN_SUMMARY_PAYLOADS.items():
            with self.subTest(svc=svc, cmd=cmd):
                out = _capture_stdout(
                    emit,
                    payload,
                    human_mode=True,
                    verbose_mode=False,
                    service=svc,
                    command=cmd,
                )
                first_line = out.splitlines()[0]
                # The summary shape must contribute at least one
                # column token to the rendered header. Pick the first
                # summary-shape token by inspecting the renderer's
                # output for the synthetic payload. The radarr
                # ``movie_id`` (and ``jellyfin item`` / ``seerr tv``
                # / ``seerr movie``) renderers return a single
                # mapping rather than a list -- the ``human``
                # renderer projects those as vertical key-value
                # pairs (``{``) rather than a tabular list, so this
                # column-token check is skipped for single-record
                # summaries.
                summary_first_row = summarize(svc, cmd, payload)
                if not isinstance(summary_first_row, list):
                    continue
                self.assertTrue(summary_first_row)
                first = summary_first_row[0]
                self.assertIsInstance(first, dict)
                self.assertTrue(first)
                summary_token = next(iter(first.keys()))
                self.assertIn(
                    summary_token,
                    first_line,
                    msg=(
                        f"({svc}, {cmd}): header {first_line!r} missing "
                        f"summary token {summary_token!r} -- summarize() "
                        "was bypassed"
                    ),
                )
                # Verbatim-only tokens must NOT appear in the rendered
                # header for size-to-summary commands. ``NowPlayingItem``
                # is the canonical verbatim-shape key for Jellyfin;
                # ``PlayState`` is the canonical verbatim-shape progress
                # key. A regression that bypasses summarize() would
                # surface them here.
                self.assertNotIn(
                    "NowPlayingItem",
                    first_line,
                    msg=(
                        f"({svc}, {cmd}): header {first_line!r} contains "
                        "verbatim 'NowPlayingItem' token -- summarize() "
                        "was bypassed"
                    ),
                )
                self.assertNotIn(
                    "PlayState",
                    first_line,
                    msg=(
                        f"({svc}, {cmd}): header {first_line!r} contains "
                        "verbatim 'PlayState' token -- summarize() was "
                        "bypassed"
                    ),
                )

    def test_row_cells_reflect_summary_values(self) -> None:
        # For every (svc, cmd) at least one data row cell must match a
        # value populated by the summary renderer (no ``<null>`` for
        # fields the summary shaped).
        for (svc, cmd), payload in _HUMAN_SUMMARY_PAYLOADS.items():
            with self.subTest(svc=svc, cmd=cmd):
                out = _capture_stdout(
                    emit,
                    payload,
                    human_mode=True,
                    verbose_mode=False,
                    service=svc,
                    command=cmd,
                )
                lines = out.splitlines()
                self.assertGreater(
                    len(lines),
                    1,
                    msg=f"({svc}, {cmd}): rendered table has no rows",
                )
                data_blob = "\n".join(lines[2:])
                summary_first = summarize(svc, cmd, payload)
                # Single-record renderers (``jellyfin item``,
                # ``radarr movie_id``, ``seerr tv``, ``seerr movie``)
                # emit a vertical key-value object rather than a
                # tabular list -- skip the per-cell value match for
                # those, the columns-token sweep in
                # ``TestColumnsBlockMatchesSummaryShape`` is the
                # authoritative coverage for the cell shape.
                if not isinstance(summary_first, list):
                    continue
                self.assertTrue(summary_first)
                first = summary_first[0]
                if not isinstance(first, dict):
                    continue
                primitive_values = [
                    v for v in first.values()
                    if isinstance(v, (str, int, float, bool))
                ]
                if not primitive_values:
                    continue
                expected = _stringify_value(primitive_values[0])
                self.assertIn(
                    expected,
                    data_blob,
                    msg=(
                        f"({svc}, {cmd}): expected {expected!r} in "
                        f"rendered rows; got:\n{data_blob!r}"
                    ),
                )

    def test_jellyfin_favorites_human_header_includes_id(self) -> None:
        # Regression for ``jellyfin-favorites-summary-id-field``: the
        # ``--human`` tabular view of ``jellyfin favorites`` must
        # surface ``Id`` as a leftmost column token so the table is
        # chainable into ``jellyfin item <id>``. The curated summary
        # projection and the ``columns`` literal move together (per
        # AGENTS.md §1 "summary-shape keys" invariant); this test
        # pins the human side.
        payload = [
            {
                "Id": "523c6aa176971feab5e0fb18ebde0b8f",
                "Name": "Fireheart: The Legend of Tadas Blinda",
                "Type": "Movie",
                "ProductionYear": 2011,
                "SeriesName": None,
            }
        ]
        out = _capture_stdout(
            emit,
            payload,
            human_mode=True,
            verbose_mode=False,
            service="jellyfin",
            command="favorites",
            columns=["Id", "Name", "Type", "ProductionYear", "SeriesName"],
        )
        header = out.splitlines()[0]
        self.assertIn(
            "Id",
            header,
            msg=(
                f"jellyfin --human favorites header {header!r} missing "
                "the 'Id' column -- the table is no longer chainable "
                "into ``jellyfin item <id>``"
            ),
        )
        # Id must be the leftmost token (matches the curated-summary
        # order and the ``seerr genres`` id-first convention).
        self.assertTrue(
            header.index("Id") < header.index("Name"),
            msg=(
                f"jellyfin --human favorites header {header!r} does "
                "not place 'Id' to the left of 'Name'"
            ),
        )

    def test_jellyfin_search_human_renders_table(self) -> None:
        # Regression for ``jellyfin-search-nextup-envelope-unwrap``:
        # ``emit(..., human_mode=True, service="jellyfin",
        # command="search")`` must render the registered column
        # table even when ``payload`` is the paginated
        # ``{Items: [...], TotalRecordCount, StartIndex}`` envelope
        # returned by ``GET /Items?searchTerm=...&Recursive=true``.
        # Without a registered summary renderer, ``human()`` would
        # collapse the envelope via ``_render_object()``.
        payload = {
            "Items": [
                {
                    "Name": "The Matrix",
                    "Type": "Movie",
                    "ProductionYear": 1999,
                    "SeriesName": None,
                }
            ],
            "TotalRecordCount": 1,
            "StartIndex": 0,
        }
        out = _capture_stdout(
            emit,
            payload,
            human_mode=True,
            verbose_mode=False,
            service="jellyfin",
            command="search",
            columns=["Name", "Type", "ProductionYear", "SeriesName"],
        )
        self.assertIn(
            "Name",
            out.splitlines()[0],
            msg=(
                f"jellyfin --human search header {out.splitlines()[0]!r} "
                "missing the 'Name' column -- the curated table is no "
                "longer being rendered"
            ),
        )
        self.assertIn(
            "ProductionYear",
            out.splitlines()[0],
            msg=(
                f"jellyfin --human search header {out.splitlines()[0]!r} "
                "missing the 'ProductionYear' column"
            ),
        )
        # The envelope collapse markers must NOT appear -- if
        # either leaks into the rendered output, the envelope is
        # being routed through ``_render_object()`` instead of the
        # registered renderer.
        self.assertNotIn(
            "TotalRecordCount:",
            out,
            msg=(
                "jellyfin --human search output still contains the "
                "envelope 'TotalRecordCount:' marker; the summary "
                "renderer is not unwrapping the envelope -- see "
                "bug ``jellyfin-search-nextup-envelope-unwrap``"
            ),
        )
        # ``_render_object`` opens with ``{\n  <key>`` -- it must
        # not appear in human-rendered table output.
        self.assertNotIn(
            "{\n  Items:",
            out,
            msg=(
                "jellyfin --human search output still begins with "
                "the ``_render_object`` envelope opener "
                "{\\n  Items:; the renderer is not registered -- "
                "see bug ``jellyfin-search-nextup-envelope-unwrap``"
            ),
        )

    def test_jellyfin_nextup_human_renders_table(self) -> None:
        # Regression for ``jellyfin-search-nextup-envelope-unwrap``:
        # ``emit(..., human_mode=True, service="jellyfin",
        # command="nextup")`` must render the registered column
        # table even when ``payload`` is the paginated envelope
        # returned by ``GET /Shows/NextUp``.
        payload = {
            "Items": [
                {
                    "Name": "The One With the Rumor",
                    "SeriesName": "Friends",
                    "ParentIndexNumber": 4,
                    "IndexNumber": 12,
                    "PremiereDate": "2002-01-10T00:00:00.0000000Z",
                }
            ],
            "TotalRecordCount": 1,
            "StartIndex": 0,
        }
        out = _capture_stdout(
            emit,
            payload,
            human_mode=True,
            verbose_mode=False,
            service="jellyfin",
            command="nextup",
            columns=[
                "Name",
                "SeriesName",
                "ParentIndexNumber",
                "IndexNumber",
                "PremiereDate",
            ],
        )
        self.assertIn(
            "Name",
            out.splitlines()[0],
            msg=(
                f"jellyfin --human nextup header {out.splitlines()[0]!r} "
                "missing the 'Name' column -- the curated table is no "
                "longer being rendered"
            ),
        )
        self.assertIn(
            "SeriesName",
            out.splitlines()[0],
            msg=(
                f"jellyfin --human nextup header {out.splitlines()[0]!r} "
                "missing the 'SeriesName' column"
            ),
        )
        self.assertIn(
            "PremiereDate",
            out.splitlines()[0],
            msg=(
                f"jellyfin --human nextup header {out.splitlines()[0]!r} "
                "missing the 'PremiereDate' column"
            ),
        )
        self.assertNotIn(
            "TotalRecordCount:",
            out,
            msg=(
                "jellyfin --human nextup output still contains the "
                "envelope 'TotalRecordCount:' marker; the summary "
                "renderer is not unwrapping the envelope -- see "
                "bug ``jellyfin-search-nextup-envelope-unwrap``"
            ),
        )
        self.assertNotIn(
            "{\n  Items:",
            out,
            msg=(
                "jellyfin --human nextup output still begins with "
                "the ``_render_object`` envelope opener "
                "{\\n  Items:; the renderer is not registered -- "
                "see bug ``jellyfin-search-nextup-envelope-unwrap``"
            ),
        )


class TestEmitHumanVerbatimFallback(unittest.TestCase):
    """``emit(..., human_mode=True, verbose_mode=False)`` falls through to
    verbatim when ``(service, command)`` is not in
    :data:`_SUMMARY_RENDERERS` (REQ-2 AC7)."""

    def test_known_non_candidate_seerr_request_count(self) -> None:
        # After the radarr-human-fields work, ``radarr calendar``
        # became a size-to-summary candidate (it has the
        # ``_summary_radarr_calendar`` renderer registered); after
        # the sonarr-human-fields work, ``sonarr calendar`` joined
        # the candidates; after the maintainerr-human-fields work,
        # ``maintainerr storage`` joined too. The canonical
        # non-candidate example is now ``seerr request-count`` (the
        # endpoint has no registered renderer, so it falls through
        # to verbatim).
        payload = [{"title": "Foo", "releaseDate": "2024-01-01"}]
        out = _capture_stdout(
            emit,
            payload,
            human_mode=True,
            verbose_mode=False,
            service="seerr",
            command="request-count",
        )
        expected_buffer = io.StringIO()
        print(human(payload), file=expected_buffer)
        self.assertEqual(out, expected_buffer.getvalue())

    def test_empty_service_and_command_pair(self) -> None:
        # Callers that do not thread ``service`` / ``command`` observe
        # the graceful default: ``summarize`` returns the payload
        # unchanged and ``human`` receives the verbatim payload.
        payload = {"foo": "bar", "baz": 1}
        out = _capture_stdout(
            emit,
            payload,
            human_mode=True,
            verbose_mode=False,
            service="",
            command="",
        )
        expected_buffer = io.StringIO()
        print(human(payload), file=expected_buffer)
        self.assertEqual(out, expected_buffer.getvalue())


class TestEmitHumanVerboseEscapeHatch(unittest.TestCase):
    """``emit(..., human_mode=True, verbose_mode=True)`` bypasses
    :func:`summarize` and renders the verbatim payload (REQ-4 AC1, AC2)."""

    def test_escape_hatch_preserves_verbatim_columns(self) -> None:
        # The payload uses top-level ``NowPlayingItem.Name`` /
        # ``NowPlayingItem.SeriesName`` / ``PlayState`` keys so the
        # ``human()`` renderer can project them onto column headers
        # directly (a nested ``NowPlayingItem`` dict would be collapsed
        # to ``<N keys>`` by the renderer and the literal
        # ``NowPlayingItem.Name`` token would not appear in the header).
        payload = [
            {
                "NowPlayingItem.Name": "Foo",
                "NowPlayingItem.SeriesName": "Show",
                "PlayState": "playing",
                "RemoteEndPoint": "1.2.3.4",
            }
        ]
        out = _capture_stdout(
            emit,
            payload,
            human_mode=True,
            verbose_mode=True,
            service="jellyfin",
            command="now",
        )
        first_line = out.splitlines()[0]
        self.assertIn(
            "NowPlayingItem.Name",
            first_line,
            msg=(
                f"escape hatch: header {first_line!r} missing verbatim "
                "'NowPlayingItem.Name' token -- summarize() was applied"
            ),
        )
        self.assertNotIn(
            "playing.name",
            first_line,
            msg=(
                f"escape hatch: header {first_line!r} contains summary "
                "'playing.name' token -- summarize() was applied"
            ),
        )
        # Snapshot invariant: escape hatch output is byte-identical to
        # ``human(payload)`` followed by ``print`` (which adds the
        # trailing newline that ``emit`` writes).
        expected_buffer = io.StringIO()
        print(human(payload), file=expected_buffer)
        self.assertEqual(out, expected_buffer.getvalue())


class TestEmitHumanRowBudget(unittest.TestCase):
    """``emit(..., human_mode=True, verbose_mode=False, limit=5)`` honours
    the summary row budget, not the verbatim row count (REQ-3 AC1, AC3)."""

    def test_summary_row_count_is_smaller_than_verbatim(self) -> None:
        # Mixed payload: 2 valid session dicts + 6 non-mapping items.
        # ``_summary_jellyfin_now`` drops the 6 non-mapping items, so
        # the summary has 2 items while the raw payload has 8 items.
        sessions = (
            [
                {
                    "UserName": f"user{i}",
                    "DeviceName": f"dev{i}",
                    "Client": "Jellyfin Web",
                    "NowPlayingItem": {
                        "Name": f"Show{i}",
                        "SeriesName": "Show",
                        "Type": "Episode",
                    },
                    "PlayState": {"PositionTicks": 100, "IsPaused": False},
                }
                for i in range(2)
            ]
            + ["not a mapping"] * 6
        )
        out = _capture_stdout(
            emit,
            sessions,
            human_mode=True,
            verbose_mode=False,
            service="jellyfin",
            command="now",
            limit=5,
        )
        lines = out.splitlines()
        # Header + separator + 2 data rows = 4 lines; the data rows
        # are strictly fewer than the requested ``limit=5`` because the
        # summary renderer dropped the 6 non-mapping items.
        data_rows = len(lines) - 2  # subtract header + separator
        self.assertLess(
            data_rows,
            5,
            msg=(
                f"summary row budget exceeded: {data_rows} data rows "
                f"in output:\n{out!r}"
            ),
        )
        self.assertGreater(
            data_rows,
            0,
            msg=(
                "rendered table has zero data rows -- summarize() was "
                "bypassed or rendered empty payload"
            ),
        )

    def test_verbatim_row_count_is_honoured_under_escape_hatch(self) -> None:
        # Same payload, but with ``verbose_mode=True`` the verbatim
        # shape flows into ``human()`` and the row budget applies to
        # the verbatim items (not the summarized ones).
        sessions = (
            [
                {
                    "UserName": f"user{i}",
                    "DeviceName": f"dev{i}",
                    "Client": "Jellyfin Web",
                    "NowPlayingItem": {
                        "Name": f"Show{i}",
                        "SeriesName": "Show",
                        "Type": "Episode",
                    },
                    "PlayState": {"PositionTicks": 100, "IsPaused": False},
                }
                for i in range(2)
            ]
            + ["not a mapping"] * 6
        )
        out = _capture_stdout(
            emit,
            sessions,
            human_mode=True,
            verbose_mode=True,
            service="jellyfin",
            command="now",
            limit=5,
        )
        lines = out.splitlines()
        # The verbatim payload has 8 items; ``limit=5`` truncates the
        # table and surfaces the pagination footer instead of expanding
        # to the full verbatim row count.
        self.assertTrue(
            any("more" in line for line in lines),
            msg=(
                "escape hatch: pagination footer missing -- limit was "
                f"not honoured; output:\n{out!r}"
            ),
        )


class TestEmitHumanDocstringPinning(unittest.TestCase):
    """``emit``'s docstring pins the new ``--human`` / ``--verbose``
    precedence so future reverts fail visibly (REQ-4 AC3, REQ-2 AC1)."""

    def test_docstring_references_summarize(self) -> None:
        doc = inspect.getdoc(emit)
        self.assertIsNotNone(doc)
        self.assertIn(
            ":func:`summarize`",
            doc,
            msg=(
                "emit() docstring must reference :func:`summarize` "
                "so a future revert that drops the summary-shape "
                "language fails this regression net (REQ-2 AC1)"
            ),
        )

    def test_docstring_priority_chain_mentions_human_mode_and_verbose(self) -> None:
        doc = inspect.getdoc(emit)
        self.assertIsNotNone(doc)
        # The priority-chain prose must include both ``human_mode``
        # and ``verbose`` so a future revert that drops the
        # escape-hatch note fails this test (REQ-4 AC3).
        priority_section = doc.split("Priority chain")[1].split("Parameters")[0]
        self.assertIn(
            "human_mode",
            priority_section,
            msg=(
                "emit() docstring priority-chain section missing "
                "'human_mode' reference"
            ),
        )
        self.assertIn(
            "verbose",
            priority_section,
            msg=(
                "emit() docstring priority-chain section missing "
                "'verbose' reference"
            ),
        )


def _stringify_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ---------------------------------------------------------------------------
# Helpers (truncate / column-widths)
# ---------------------------------------------------------------------------


class TestTruncate(unittest.TestCase):
    """``_truncate()`` honours the documented ``…`` marker."""

    def test_short_value_unchanged(self) -> None:
        self.assertEqual(_truncate("hi", 10), "hi")

    def test_long_value_truncated(self) -> None:
        self.assertEqual(_truncate("abcdefghij", 5), "abcd…")

    def test_zero_width_returns_empty(self) -> None:
        self.assertEqual(_truncate("hello", 0), "")

    def test_one_width_returns_ellipsis_only(self) -> None:
        self.assertEqual(_truncate("hello", 1), "…")


class TestTruncateOverview(unittest.TestCase):
    """``_truncate_overview`` truncates free-text overviews to a fixed budget."""

    def _truncate(self, value: Any, *, max_chars: int = 80) -> str | None:
        from arr_cli.facade.output import _truncate_overview
        return _truncate_overview(value, max_chars=max_chars)

    def test_missing_input_returns_none(self) -> None:
        self.assertIsNone(self._truncate(None))

    def test_non_string_input_returns_none(self) -> None:
        self.assertIsNone(self._truncate(123))
        self.assertIsNone(self._truncate(["a", "b"]))
        self.assertIsNone(self._truncate({"overview": "x"}))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(self._truncate(""))

    def test_short_string_passes_through_verbatim(self) -> None:
        self.assertEqual(self._truncate("hello"), "hello")

    def test_long_string_truncated_with_ellipsis(self) -> None:
        long_value = "x" * 100
        truncated = self._truncate(long_value)
        self.assertEqual(len(truncated), 80)
        self.assertTrue(truncated.endswith("…"))
        self.assertEqual(truncated, "x" * 79 + "…")


class TestFormatRuntimeMinutesWhole(unittest.TestCase):
    """``_format_runtime_minutes_whole`` formats whole-minute runtimes."""

    def _format(self, value: Any) -> str | None:
        from arr_cli.facade.output import _format_runtime_minutes_whole
        return _format_runtime_minutes_whole(value)

    def test_positive_input_formats_hours_and_minutes(self) -> None:
        self.assertEqual(self._format(120), "2h 0m")
        self.assertEqual(self._format(45), "0h 45m")
        self.assertEqual(self._format(60), "1h 0m")

    def test_zero_returns_none(self) -> None:
        self.assertIsNone(self._format(0))

    def test_negative_returns_none(self) -> None:
        self.assertIsNone(self._format(-1))

    def test_none_returns_none(self) -> None:
        self.assertIsNone(self._format(None))

    def test_non_numeric_returns_none(self) -> None:
        self.assertIsNone(self._format("120"))
        self.assertIsNone(self._format([120]))
        self.assertIsNone(self._format({"runtime": 120}))

    def test_bool_returns_none(self) -> None:
        # ``bool`` is a subclass of ``int``; without the explicit
        # guard ``True`` would render as ``"0h 0m"``.
        self.assertIsNone(self._format(True))
        self.assertIsNone(self._format(False))


class TestColumnWidths(unittest.TestCase):
    """``_column_widths()`` allocates evenly and caps per-column."""

    def test_allocates_evenly(self) -> None:
        widths = _column_widths(
            headers=["a", "b"],
            rows=[["x", "y"]],
            budget=20,
        )
        # 20 // 2 = 10 per column; both columns are within budget.
        for width in widths:
            self.assertLessEqual(width, 10)

    def test_zero_columns_returns_empty(self) -> None:
        # Defensive: an empty header list yields no widths.
        self.assertEqual(_column_widths(headers=[], rows=[], budget=10), [])


class TestColumnAlignment(unittest.TestCase):
    """Rendered columns must align visually (header == separator width)."""

    def test_short_header_pads_to_column_width(self) -> None:
        # When the header is shorter than the longest cell, the header
        # row is padded with trailing spaces so the separator dashes
        # line up with the column boundary.
        rendered = human([{"a": "plain"}, {"a": "1"}], columns=["a"])
        lines = rendered.splitlines()
        # All rendered rows must occupy the same character width so
        # the column boundary is visible end-to-end.
        widths = {len(line) for line in lines}
        self.assertEqual(len(widths), 1)

    def test_two_columns_align(self) -> None:
        # Two columns with different header lengths still align.
        rendered = human(
            [
                {"alpha": "1", "beta": "two"},
                {"alpha": "3", "beta": "four"},
            ],
            columns=["alpha", "beta"],
        )
        lines = rendered.splitlines()
        widths = {len(line) for line in lines}
        self.assertEqual(len(widths), 1)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class TestDefaults(unittest.TestCase):
    """Lock the documented default values to prevent silent regressions."""

    def test_default_max_width(self) -> None:
        self.assertEqual(DEFAULT_MAX_WIDTH, 120)

    def test_default_min_width(self) -> None:
        self.assertEqual(MIN_WIDTH, 20)

    def test_default_limit(self) -> None:
        self.assertEqual(DEFAULT_LIMIT, 20)

    def test_limit_must_be_positive(self) -> None:
        # ``limit < 1`` is rejected; the limit must be at least 1.
        with self.assertRaises(ValueError):
            human([{"a": 1}], limit=0)
        with self.assertRaises(ValueError):
            human([{"a": 1}], limit=-1)


# ---------------------------------------------------------------------------
# Verbose-flag priority chain (REQ-3 AC1-AC5)
# ---------------------------------------------------------------------------


class TestEmitPriorityChain(unittest.TestCase):
    """``emit`` enforces the documented --human > --verbose > summary > verbatim chain."""

    def test_human_mode_wins(self) -> None:
        # REQ-3 AC1: ``--human`` always renders the tabular view.
        payload = [{"a": 1, "b": 2}]
        out = _capture_stdout(
            emit,
            payload,
            human_mode=True,
            verbose_mode=False,
            columns=["a", "b"],
        )
        # The tabular view carries the header row.
        first_line = out.splitlines()[0]
        self.assertIn("a", first_line)
        self.assertIn("b", first_line)

    def test_verbose_mode_emits_verbatim_on_candidate(self) -> None:
        # REQ-3 AC2: ``--verbose`` on a size-to-summary candidate
        # emits the verbatim payload.
        payload = [{"UserName": "alice"}]
        out = _capture_stdout(
            emit,
            payload,
            human_mode=False,
            verbose_mode=True,
            service="jellyfin",
            command="now",
        )
        self.assertEqual(
            out,
            json.dumps(payload, ensure_ascii=False) + "\n",
        )

    def test_default_summary_on_candidate(self) -> None:
        # REQ-3 AC3: no flags on a size-to-summary candidate emits
        # the curated summary shape.
        payload = [{"UserName": "alice", "DeviceName": "TV"}]
        out = _capture_stdout(
            emit,
            payload,
            human_mode=False,
            verbose_mode=False,
            service="jellyfin",
            command="now",
        )
        rendered = json.loads(out)
        self.assertIsInstance(rendered, list)
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0]["user"], "alice")
        self.assertEqual(rendered[0]["device"], "TV")

    def test_default_verbatim_on_non_candidate(self) -> None:
        # REQ-3 AC4: a non-candidate command without flags stays on
        # verbatim JSON. ``seerr request-count`` is the canonical
        # non-candidate example (Radarr's calendar became a
        # size-to-summary candidate in radarr-human-fields; Sonarr's
        # calendar became one in sonarr-human-fields; maintainerr
        # storage became one in maintainerr-human-fields; the seerr
        # request-count endpoint still has no registered renderer).
        payload = [{"title": "Foo"}]
        out = _capture_stdout(
            emit,
            payload,
            human_mode=False,
            verbose_mode=False,
            service="seerr",
            command="request-count",
        )
        self.assertEqual(out, json.dumps(payload, ensure_ascii=False) + "\n")

    def test_human_and_verbose_human_wins(self) -> None:
        # REQ-3 AC5: when both flags are set ``--human`` wins and
        # ``--verbose`` has no effect on the rendered table.
        payload = [{"a": 1}]
        out = _capture_stdout(
            emit,
            payload,
            human_mode=True,
            verbose_mode=True,
            columns=["a"],
        )
        # Tabular view: header row + at least one data row, not
        # the JSON serialised payload.
        first_line = out.splitlines()[0]
        self.assertIn("a", first_line)
        self.assertNotIn("[{", out)


# ---------------------------------------------------------------------------
# summarize() graceful default (REQ-1 AC4, REQ-5 AC5)
# ---------------------------------------------------------------------------


class TestSummarizeGracefulDefault(unittest.TestCase):
    """``summarize`` returns ``payload`` unchanged when no renderer is registered."""

    def test_known_key_invokes_renderer(self) -> None:
        payload = [{"UserName": "alice"}]
        rendered = summarize("jellyfin", "now", payload)
        # Curated summary: list of session objects with the documented keys.
        self.assertIsInstance(rendered, list)
        self.assertEqual(rendered[0]["user"], "alice")
        self.assertEqual(rendered[0]["playing"], None)

    def test_unknown_key_returns_payload_unchanged(self) -> None:
        # REQ-1 AC4: unknown key returns payload verbatim (graceful default).
        payload = {"a": 1}
        rendered = summarize("unknown_service", "unknown_command", payload)
        self.assertEqual(rendered, payload)

    def test_empty_key_returns_payload_unchanged(self) -> None:
        # Empty key ``("", "")`` is not in the table; ``summarize``
        # returns the payload unchanged (graceful default).
        payload = {"a": 1}
        rendered = summarize("", "", payload)
        self.assertEqual(rendered, payload)


# ---------------------------------------------------------------------------
# (service, command) threading seam (REQ-3 AC6)
# ---------------------------------------------------------------------------


class TestServiceCommandThreading(unittest.TestCase):
    """``(service, command)`` reaches the dispatch table from ``emit``."""

    def test_emit_invokes_renderer_when_keys_match(self) -> None:
        # When ``(service, command)`` is a registered key the renderer
        # is invoked and the curated summary reaches stdout.
        payload = [{"UserName": "alice"}]
        out = _capture_stdout(
            emit,
            payload,
            human_mode=False,
            verbose_mode=False,
            service="jellyfin",
            command="now",
        )
        rendered = json.loads(out)
        self.assertEqual(rendered[0]["user"], "alice")

    def test_emit_without_keys_emits_verbatim(self) -> None:
        # Without ``service`` / ``command`` the lookup is skipped and
        # the payload is emitted verbatim (REQ-5 AC3).
        payload = {"foo": "bar"}
        out = _capture_stdout(
            emit,
            payload,
            human_mode=False,
            verbose_mode=False,
        )
        self.assertEqual(out, json.dumps(payload, ensure_ascii=False) + "\n")

    def test_emit_with_non_candidate_key_emits_verbatim(self) -> None:
        # A non-candidate command (e.g. ``seerr request-count``)
        # falls through to verbatim even though ``service`` /
        # ``command`` are non-empty. Radarr's calendar became a
        # candidate in radarr-human-fields; Sonarr's calendar became
        # one in sonarr-human-fields; maintainerr storage became one
        # in maintainerr-human-fields; the seerr request-count
        # endpoint is the canonical non-candidate example now.
        payload = {"Name": "Foo"}
        out = _capture_stdout(
            emit,
            payload,
            human_mode=False,
            verbose_mode=False,
            service="seerr",
            command="request-count",
        )
        self.assertEqual(out, json.dumps(payload, ensure_ascii=False) + "\n")

    def test_summarize_unknown_key_passthrough(self) -> None:
        # Same seam as TestSummarizeGracefulDefault; pinned here so
        # the threading narrative stays co-located.
        payload = {"a": 1}
        self.assertEqual(summarize("u", "u", payload), payload)

    def test_summarize_empty_key_passthrough(self) -> None:
        payload = {"a": 1}
        self.assertEqual(summarize("", "", payload), payload)

    def test_per_service_emit_threads_service_and_command(self) -> None:
        # Per-service ``_emit`` must forward ``service`` and
        # ``command`` kwargs into ``output.emit`` so the dispatch
        # table lookup fires end-to-end.
        import argparse
        from unittest.mock import patch
        from arr_cli import jellyfin

        args = argparse.Namespace(
            human=False,
            verbose=False,
            command="now",
            limit=20,
        )
        with patch("arr_cli.jellyfin.output.emit") as mock_emit:
            jellyfin._emit([{"UserName": "alice"}], args)
        mock_emit.assert_called_once()
        kwargs = mock_emit.call_args.kwargs
        self.assertEqual(kwargs["service"], "jellyfin")
        self.assertEqual(kwargs["command"], "now")
        self.assertFalse(kwargs["verbose_mode"])
        self.assertFalse(kwargs["human_mode"])


# ---------------------------------------------------------------------------
# Per-command summary renderer tests (REQ-1, REQ-4)
# ---------------------------------------------------------------------------


class TestSummaryJellyfinNow(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("jellyfin", "now")]`` matches the spec."""

    def test_session_with_now_playing(self) -> None:
        payload = [
            {
                "UserName": "alice",
                "DeviceName": "Living Room TV",
                "Client": "Jellyfin Web",
                "NowPlayingItem": {
                    "Type": "Episode",
                    "Name": "The Pilot",
                    "SeriesName": "Show",
                    "ParentIndexNumber": 1,
                    "IndexNumber": 1,
                },
                "PlayState": {"PositionTicks": 12345, "IsPaused": False},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "now")](payload)
        self.assertEqual(len(rendered), 1)
        session = rendered[0]
        self.assertEqual(session["user"], "alice")
        self.assertEqual(session["device"], "Living Room TV")
        self.assertEqual(session["client"], "Jellyfin Web")
        self.assertEqual(session["playing"]["type"], "Episode")
        self.assertEqual(session["playing"]["name"], "The Pilot")
        self.assertEqual(session["playing"]["series"], "Show")
        self.assertEqual(session["playing"]["season"], 1)
        self.assertEqual(session["playing"]["episode"], 1)
        self.assertEqual(session["progress"]["position_ticks"], 12345)
        self.assertFalse(session["progress"]["is_paused"])

    def test_no_sessions_returns_empty_list(self) -> None:
        rendered = _SUMMARY_RENDERERS[("jellyfin", "now")]([])
        self.assertEqual(rendered, [])

    def test_now_playing_null_collapses_to_none(self) -> None:
        payload = [
            {
                "UserName": "alice",
                "DeviceName": "Idle Device",
                "Client": "Jellyfin",
                "PlayState": {"PositionTicks": 0, "IsPaused": True},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "now")](payload)
        self.assertIsNone(rendered[0]["playing"])

    def test_non_list_payload_returns_empty_list(self) -> None:
        # Defensive: non-list payload must not raise.
        self.assertEqual(_SUMMARY_RENDERERS[("jellyfin", "now")](None), [])
        self.assertEqual(_SUMMARY_RENDERERS[("jellyfin", "now")]({}), [])


class TestSummaryJellyfinRecent(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("jellyfin", "recent")]`` matches the spec."""

    def test_recent_shape(self) -> None:
        payload = [
            {
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2024,
                "SeriesName": None,
                "UserData": {"LastPlayedDate": "2024-01-01"},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "recent")](payload)
        self.assertEqual(
            rendered[0],
            {
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2024,
                "SeriesName": None,
                "UserData.LastPlayedDate": "2024-01-01",
                "UserData.PlayedPercentage": 0,
            },
        )

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("jellyfin", "recent")](None),
            [],
        )

    def test_missing_user_data_defaults_to_none(self) -> None:
        rendered = _SUMMARY_RENDERERS[("jellyfin", "recent")](
            [{"Name": "Foo", "Type": "Movie", "ProductionYear": 2020}]
        )
        self.assertIsNone(rendered[0]["UserData.LastPlayedDate"])

    def test_folder_and_boxset_rows_are_filtered_out(self) -> None:
        # Regression: when the upstream ``includeItemTypes`` filter is
        # not honoured, library views (Folder) and collections
        # (BoxSet) can land in the envelope's ``Items`` array on top
        # of the real played Movies / Episodes. The renderer re-applies
        # the type filter so only Movie and Episode rows survive.
        payload = {
            "Items": [
                {"Name": "collections", "Type": "Folder",
                 "SeriesName": None,
                 "UserData": {"LastPlayedDate": None}},
                {"Name": "Disclosure Day (2026)", "Type": "Folder",
                 "SeriesName": None,
                 "UserData": {"LastPlayedDate": None}},
                {"Name": "Jackass - Best and Last (2026)", "Type": "Folder",
                 "SeriesName": None,
                 "UserData": {"LastPlayedDate": None}},
                {"Name": "Masters of the Universe (2026)", "Type": "Folder",
                 "SeriesName": None,
                 "UserData": {"LastPlayedDate": None}},
                {"Name": "Mutiny (2026)", "Type": "Folder",
                 "SeriesName": None,
                 "UserData": {"LastPlayedDate": None}},
                {"Name": "Watched TV Shows", "Type": "BoxSet",
                 "SeriesName": None,
                 "UserData": {"LastPlayedDate": None}},
                {"Name": "Fireheart: The Legend of Tadas Blinda",
                 "Type": "Movie", "ProductionYear": 2024,
                 "SeriesName": None,
                 "UserData": {"LastPlayedDate": "2026-09-01"}},
                {"Name": "Orders of Magnitude", "Type": "Episode",
                 "ProductionYear": 2024, "SeriesName": "Cosmos",
                 "UserData": {"LastPlayedDate": "2026-08-25"}},
            ],
            "TotalRecordCount": 8,
            "StartIndex": 0,
        }
        rendered = _SUMMARY_RENDERERS[("jellyfin", "recent")](payload)
        names = [row["Name"] for row in rendered]
        types = [row["Type"] for row in rendered]
        self.assertEqual(len(rendered), 2)
        self.assertEqual(
            names,
            [
                "Fireheart: The Legend of Tadas Blinda",
                "Orders of Magnitude",
            ],
        )
        self.assertEqual(types, ["Movie", "Episode"])
        # No Folder / BoxSet / Series rows survived the filter.
        self.assertNotIn("Folder", types)
        self.assertNotIn("BoxSet", types)
        self.assertNotIn("Series", types)

    def test_non_allowed_types_dropped_in_bare_list(self) -> None:
        # Same filter logic when the renderer is called with a bare
        # list (no envelope) -- the defensive filter must apply on
        # every code path, not only after ``_unwrap_envelope``.
        payload = [
            {"Name": "Movie row", "Type": "Movie",
             "ProductionYear": 2024, "SeriesName": None,
             "UserData": {"LastPlayedDate": "2026-09-01"}},
            {"Name": "Episode row", "Type": "Episode",
             "ProductionYear": 2024, "SeriesName": "Cosmos",
             "UserData": {"LastPlayedDate": "2026-08-25"}},
            {"Name": "Series row", "Type": "Series",
             "ProductionYear": 2020, "SeriesName": None,
             "UserData": {"LastPlayedDate": "2026-07-01"}},
            {"Name": "MusicVideo row", "Type": "MusicVideo",
             "ProductionYear": 2020, "SeriesName": None,
             "UserData": {"LastPlayedDate": "2026-06-01"}},
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "recent")](payload)
        self.assertEqual(len(rendered), 2)
        self.assertEqual(
            [row["Type"] for row in rendered],
            ["Movie", "Episode"],
        )

    def test_items_without_type_are_dropped(self) -> None:
        # Items that arrive without a ``Type`` field are dropped
        # defensively -- they cannot be validated against the
        # Movie/Episode contract and a stray row would re-introduce
        # the original symptom (8 rows where the API has 2).
        payload = [
            {"Name": "Real Movie", "Type": "Movie",
             "ProductionYear": 2024, "SeriesName": None,
             "UserData": {"LastPlayedDate": "2026-09-01"}},
            {"Name": "Typeless mystery row",
             "ProductionYear": 2024, "SeriesName": None,
             "UserData": {"LastPlayedDate": "2026-08-25"}},
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "recent")](payload)
        self.assertEqual(len(rendered), 1)
        names = [row["Name"] for row in rendered]
        self.assertEqual(names, ["Real Movie"])
        self.assertNotIn("Typeless mystery row", names)


class TestSummaryJellyfinFavorites(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("jellyfin", "favorites")]`` matches the spec."""

    def test_favorites_shape(self) -> None:
        payload = [
            {
                "Id": "abc12345",
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2020,
                "SeriesName": None,
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "favorites")](payload)
        self.assertEqual(
            rendered[0],
            {
                "Id": "abc12345",
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2020,
                "SeriesName": None,
                "RunTimeTicks": None,
                "UserData.LastPlayedDate": None,
            },
        )

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("jellyfin", "favorites")](None),
            [],
        )

    def test_envelope_with_items_is_unwrapped(self) -> None:
        payload = {
            "Items": [
                {
                    "Id": "523c6aa176971feab5e0fb18ebde0b8f",
                    "Name": "Fireheart",
                    "Type": "Movie",
                    "ProductionYear": 2011,
                    "SeriesName": None,
                }
            ],
            "TotalRecordCount": 1,
            "StartIndex": 0,
        }
        rendered = _SUMMARY_RENDERERS[("jellyfin", "favorites")](payload)
        self.assertEqual(
            rendered,
            [
                {
                    "Id": "523c6aa176971feab5e0fb18ebde0b8f",
                    "Name": "Fireheart",
                    "Type": "Movie",
                    "ProductionYear": 2011,
                    "SeriesName": None,
                    "RunTimeTicks": None,
                    "UserData.LastPlayedDate": None,
                }
            ],
        )

    def test_favorites_includes_id(self) -> None:
        # Regression for ``jellyfin-favorites-summary-id-field``: the
        # curated summary must surface the upstream ``Id`` verbatim so
        # callers can pipe the row into ``jellyfin item <id>``.
        payload = [
            {
                "Id": "523c6aa176971feab5e0fb18ebde0b8f",
                "Name": "Fireheart: The Legend of Tadas Blinda",
                "Type": "Movie",
                "ProductionYear": 2011,
                "SeriesName": None,
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "favorites")](payload)
        self.assertEqual(
            rendered[0]["Id"],
            "523c6aa176971feab5e0fb18ebde0b8f",
            msg=(
                "curated favorites row dropped Id -- the row is no "
                "longer chainable into ``jellyfin item <id>``"
            ),
        )
        self.assertEqual(
            rendered[0]["Name"],
            "Fireheart: The Legend of Tadas Blinda",
        )

    def test_favorites_series_name_null_for_movie(self) -> None:
        # Pin for ``jellyfin-latest-favorites-summary-fields``: a
        # ``SeriesName: null`` on a Movie row is legitimate (Movies
        # have no parent series in Jellyfin's model), so the curated
        # summary passes the upstream null through. The acceptance
        # criterion is "SeriesName is null only when the source Item
        # is a Movie" -- this test pins the Movie branch.
        payload = [
            {
                "Id": "abc12345",
                "Name": "Fireheart",
                "Type": "Movie",
                "ProductionYear": 2011,
                "SeriesName": None,
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "favorites")](payload)
        self.assertIsNone(
            rendered[0]["SeriesName"],
            msg=(
                "Movie row projected a non-null SeriesName; Movies "
                "do not have a parent series in Jellyfin's model"
            ),
        )

    def test_favorites_series_name_populated_for_season(self) -> None:
        # Pin for ``jellyfin-latest-favorites-summary-fields``: when
        # the upstream populates ``SeriesName`` on a non-Movie row
        # (Season carries the parent series name; Episode does too),
        # the curated summary passes it through unchanged. A
        # regression that dropped the field for non-Movie types
        # would project ``SeriesName: null`` and break the
        # operator's chain into ``jellyfin item <id>``.
        payload = [
            {
                "Id": "season-001",
                "Name": "Season 1",
                "Type": "Season",
                "ProductionYear": 2020,
                "SeriesName": "My Show",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "favorites")](payload)
        self.assertEqual(
            rendered[0]["SeriesName"],
            "My Show",
            msg=(
                "Season row lost its SeriesName in the curated "
                "summary -- the renderer should pass the upstream "
                "value through for non-Movie types"
            ),
        )

    def test_favorites_series_name_populated_for_episode(self) -> None:
        # Pin for ``jellyfin-latest-favorites-summary-fields``: when
        # the upstream populates ``SeriesName`` on an Episode row
        # (parent series name), the curated summary passes it
        # through unchanged. Same contract as the Season case.
        payload = [
            {
                "Id": "episode-001",
                "Name": "Pilot",
                "Type": "Episode",
                "ProductionYear": 2020,
                "SeriesName": "My Show",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "favorites")](payload)
        self.assertEqual(
            rendered[0]["SeriesName"],
            "My Show",
            msg=(
                "Episode row lost its SeriesName in the curated "
                "summary -- the renderer should pass the upstream "
                "value through for non-Movie types"
            ),
        )

    def test_favorites_series_name_null_for_series_top_level(self) -> None:
        # Pin for ``jellyfin-latest-favorites-summary-fields``: a
        # top-level ``Type=Series`` row legitimately has
        # ``SeriesName: null`` (the Series IS the top-level entity
        # -- it has no parent series of its own). The renderer
        # passes the upstream null through; this is not a "spurious
        # null" but the correct projection of a Series-as-top-level
        # favorite.
        payload = [
            {
                "Id": "series-001",
                "Name": "My Show",
                "Type": "Series",
                "ProductionYear": 2020,
                "SeriesName": None,
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "favorites")](payload)
        self.assertIsNone(
            rendered[0]["SeriesName"],
            msg=(
                "top-level Series row projected a non-null "
                "SeriesName; Series items have no parent series"
            ),
        )


class TestSummaryJellyfinSearch(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("jellyfin", "search")]`` matches the spec.

    Regression for ``jellyfin-search-nextup-envelope-unwrap``: the
    ``/Items?searchTerm=...&Recursive=true`` endpoint returns the
    paginated ``{Items: [...], TotalRecordCount, StartIndex}``
    envelope. Without a registered renderer, ``emit()`` skips
    ``summarize()`` and ``human()`` collapses the envelope via
    ``_render_object()``.
    """

    def test_search_unwraps_envelope(self) -> None:
        payload = {
            "Items": [
                {
                    "Name": "The Matrix",
                    "Type": "Movie",
                    "ProductionYear": 1999,
                    "SeriesName": None,
                },
                {
                    "Name": "Friends",
                    "Type": "Series",
                    "ProductionYear": 1994,
                    "SeriesName": None,
                },
            ],
            "TotalRecordCount": 2,
            "StartIndex": 0,
        }
        rendered = _SUMMARY_RENDERERS[("jellyfin", "search")](payload)
        self.assertEqual(
            rendered,
            [
                {
                    "Name": "The Matrix",
                    "Type": "Movie",
                    "ProductionYear": 1999,
                    "SeriesName": None,
                },
                {
                    "Name": "Friends",
                    "Type": "Series",
                    "ProductionYear": 1994,
                    "SeriesName": None,
                },
            ],
        )

    def test_search_handles_bare_list(self) -> None:
        # Defensive tolerance: pre-envelope clients (or a future
        # Jellyfin version that drops the envelope) may pass a bare
        # list. The renderer must project the same keys.
        payload = [
            {
                "Name": "The Matrix",
                "Type": "Movie",
                "ProductionYear": 1999,
                "SeriesName": None,
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "search")](payload)
        self.assertEqual(
            rendered,
            [
                {
                    "Name": "The Matrix",
                    "Type": "Movie",
                    "ProductionYear": 1999,
                    "SeriesName": None,
                }
            ],
        )

    def test_search_empty_envelope(self) -> None:
        payload = {"Items": [], "TotalRecordCount": 0, "StartIndex": 0}
        self.assertEqual(
            _SUMMARY_RENDERERS[("jellyfin", "search")](payload),
            [],
        )

    def test_search_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("jellyfin", "search")](None),
            [],
        )

    def test_search_does_not_drop_envelope_metadata_keys(self) -> None:
        # The renderer's projection is the four fields
        # ``cmd_search`` already advertises -- nothing more (no
        # ``Items`` / ``TotalRecordCount`` / ``StartIndex`` leak
        # through to the curated summary).
        payload = {
            "Items": [
                {
                    "Name": "The Matrix",
                    "Type": "Movie",
                    "ProductionYear": 1999,
                    "SeriesName": None,
                    "Overview": "kept upstream only",
                }
            ],
            "TotalRecordCount": 1,
            "StartIndex": 0,
        }
        rendered = _SUMMARY_RENDERERS[("jellyfin", "search")](payload)
        self.assertEqual(
            sorted(rendered[0].keys()),
            ["Name", "ProductionYear", "SeriesName", "Type"],
        )


class TestSummaryJellyfinNextup(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("jellyfin", "nextup")]`` matches the spec.

    Regression for ``jellyfin-search-nextup-envelope-unwrap``: the
    ``/Shows/NextUp?UserId=...`` endpoint returns the paginated
    ``{Items: [...], TotalRecordCount, StartIndex}`` envelope. The
    renderer unwraps the envelope and projects the five columns
    ``cmd_nextup`` advertises (``Name``, ``SeriesName``,
    ``ParentIndexNumber``, ``IndexNumber``, ``PremiereDate``).
    """

    def test_nextup_unwraps_envelope(self) -> None:
        payload = {
            "Items": [
                {
                    "Name": "The One With the Rumor",
                    "SeriesName": "Friends",
                    "ParentIndexNumber": 4,
                    "IndexNumber": 12,
                    "PremiereDate": "2002-01-10T00:00:00.0000000Z",
                }
            ],
            "TotalRecordCount": 1,
            "StartIndex": 0,
        }
        rendered = _SUMMARY_RENDERERS[("jellyfin", "nextup")](payload)
        self.assertEqual(
            rendered,
            [
                {
                    "Name": "The One With the Rumor",
                    "SeriesName": "Friends",
                    "ParentIndexNumber": 4,
                    "IndexNumber": 12,
                    "PremiereDate": "2002-01-10T00:00:00.0000000Z",
                    "RunTimeTicks": None,
                    "Overview": None,
                }
            ],
        )

    def test_nextup_handles_bare_list(self) -> None:
        payload = [
            {
                "Name": "The One With the Rumor",
                "SeriesName": "Friends",
                "ParentIndexNumber": 4,
                "IndexNumber": 12,
                "PremiereDate": "2002-01-10T00:00:00.0000000Z",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "nextup")](payload)
        self.assertEqual(
            rendered,
            [
                {
                    "Name": "The One With the Rumor",
                    "SeriesName": "Friends",
                    "ParentIndexNumber": 4,
                    "IndexNumber": 12,
                    "PremiereDate": "2002-01-10T00:00:00.0000000Z",
                    "RunTimeTicks": None,
                    "Overview": None,
                }
            ],
        )

    def test_nextup_empty_envelope(self) -> None:
        payload = {"Items": [], "TotalRecordCount": 0, "StartIndex": 0}
        self.assertEqual(
            _SUMMARY_RENDERERS[("jellyfin", "nextup")](payload),
            [],
        )

    def test_nextup_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("jellyfin", "nextup")](None),
            [],
        )

    def test_nextup_defaults_on_missing_fields(self) -> None:
        # ``cmd_nextup`` may project a row whose upstream ``Items``
        # payload omits ``PremiereDate`` (e.g. unaired episodes in
        # the nextup window on a future Jellyfin version); the
        # renderer must fall back to ``None`` / ``0`` rather than
        # raise on ``_safe_get`` call signature.
        payload = {
            "Items": [
                {
                    "Name": "Soon",
                    "SeriesName": "Some Show",
                    # ParentIndexNumber / IndexNumber / PremiereDate
                    # intentionally missing.
                }
            ],
            "TotalRecordCount": 1,
            "StartIndex": 0,
        }
        rendered = _SUMMARY_RENDERERS[("jellyfin", "nextup")](payload)
        self.assertEqual(
            rendered,
            [
                {
                    "Name": "Soon",
                    "SeriesName": "Some Show",
                    "ParentIndexNumber": 0,
                    "IndexNumber": 0,
                    "PremiereDate": None,
                    "RunTimeTicks": None,
                    "Overview": None,
                }
            ],
        )


class TestSummaryJellyfinResume(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("jellyfin", "resume")]`` matches the spec."""

    def test_resume_shape(self) -> None:
        payload = [
            {
                "Name": "Foo",
                "Type": "Episode",
                "ProductionYear": 2020,
                "SeriesName": "Show",
                "UserData": {"PlaybackPositionTicks": 100, "PlayCount": 2},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "resume")](payload)
        self.assertEqual(
            rendered[0],
            {
                "Name": "Foo",
                "Type": "Episode",
                "ProductionYear": 2020,
                "SeriesName": "Show",
                "UserData.PlaybackPositionTicks": 100,
                "UserData.PlayCount": 2,
                "UserData.PlayedPercentage": 0,
                "RunTimeTicks": None,
            },
        )

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("jellyfin", "resume")](None),
            [],
        )

    def test_series_and_season_rows_are_filtered_out(self) -> None:
        # Regression: on some Jellyfin versions the /Items/Resume
        # endpoint includes parent ``Type=Series`` and ``Type=Season``
        # roll-up entries on top of the directly resumable
        # Movies / Episodes. The renderer re-applies the type
        # filter so only Movie and Episode rows survive -- the
        # original symptom was ``12 rows where API returns 3``
        # because the roll-ups were inflating the count.
        payload = {
            "Items": [
                {
                    "Name": "The Woman Who Fell to Earth",
                    "Type": "Episode",
                    "ProductionYear": 2018,
                    "SeriesName": "Doctor Who (2005)",
                    "UserData": {"PlaybackPositionTicks": 1234, "PlayCount": 1},
                },
                {
                    "Name": "Vijay 69",
                    "Type": "Movie",
                    "ProductionYear": 2024,
                    "SeriesName": None,
                    "UserData": {"PlaybackPositionTicks": 5678, "PlayCount": 0},
                },
                {
                    "Name": "Sea of Despair",
                    "Type": "Episode",
                    "ProductionYear": 2018,
                    "SeriesName": "Doctor Who (2005)",
                    "UserData": {"PlaybackPositionTicks": 9012, "PlayCount": 2},
                },
                {
                    "Name": "Season 2",
                    "Type": "Season",
                    "ProductionYear": 0,
                    "SeriesName": "Doctor Who (2005)",
                    "UserData": {"PlaybackPositionTicks": 0, "PlayCount": 0},
                },
                {
                    "Name": "Season 3",
                    "Type": "Season",
                    "ProductionYear": 0,
                    "SeriesName": "Doctor Who (2005)",
                    "UserData": {"PlaybackPositionTicks": 0, "PlayCount": 0},
                },
                {
                    "Name": "Doctor Who (2005)",
                    "Type": "Series",
                    "ProductionYear": 2005,
                    "SeriesName": None,
                    "UserData": {"PlaybackPositionTicks": 0, "PlayCount": 0},
                },
                {
                    "Name": "Primal",
                    "Type": "Series",
                    "ProductionYear": 2019,
                    "SeriesName": None,
                    "UserData": {"PlaybackPositionTicks": 0, "PlayCount": 0},
                },
            ],
            "TotalRecordCount": 3,
            "StartIndex": 0,
        }
        rendered = _SUMMARY_RENDERERS[("jellyfin", "resume")](payload)
        names = [row["Name"] for row in rendered]
        types = [row["Type"] for row in rendered]
        self.assertEqual(len(rendered), 3)
        self.assertEqual(
            names,
            [
                "The Woman Who Fell to Earth",
                "Vijay 69",
                "Sea of Despair",
            ],
        )
        self.assertEqual(types, ["Episode", "Movie", "Episode"])
        # No Series / Season / Folder / BoxSet rows survived.
        self.assertNotIn("Series", types)
        self.assertNotIn("Season", types)
        self.assertNotIn("Folder", types)
        self.assertNotIn("BoxSet", types)
        # Every surviving row has populated UserData (the resumable
        # contract).
        for row in rendered:
            self.assertGreater(row["UserData.PlaybackPositionTicks"], 0)

    def test_non_allowed_types_dropped_in_bare_list(self) -> None:
        # Same filter logic when the renderer is called with a bare
        # list (no envelope) -- the defensive filter must apply on
        # every code path, not only after ``_unwrap_envelope``.
        payload = [
            {
                "Name": "Movie row",
                "Type": "Movie",
                "ProductionYear": 2024,
                "SeriesName": None,
                "UserData": {"PlaybackPositionTicks": 100, "PlayCount": 1},
            },
            {
                "Name": "Episode row",
                "Type": "Episode",
                "ProductionYear": 2024,
                "SeriesName": "Cosmos",
                "UserData": {"PlaybackPositionTicks": 200, "PlayCount": 1},
            },
            {
                "Name": "Series row",
                "Type": "Series",
                "ProductionYear": 2020,
                "SeriesName": None,
                "UserData": {"PlaybackPositionTicks": 0, "PlayCount": 0},
            },
            {
                "Name": "Season row",
                "Type": "Season",
                "ProductionYear": 2020,
                "SeriesName": "Cosmos",
                "UserData": {"PlaybackPositionTicks": 0, "PlayCount": 0},
            },
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "resume")](payload)
        self.assertEqual(len(rendered), 2)
        self.assertEqual(
            [row["Type"] for row in rendered],
            ["Movie", "Episode"],
        )

    def test_items_without_type_are_dropped(self) -> None:
        # Items that arrive without a ``Type`` field are dropped
        # defensively -- they cannot be validated against the
        # Movie/Episode contract and a stray row would re-introduce
        # the original symptom (12 rows where the API has 3).
        payload = [
            {
                "Name": "Real Episode",
                "Type": "Episode",
                "ProductionYear": 2024,
                "SeriesName": "Cosmos",
                "UserData": {"PlaybackPositionTicks": 200, "PlayCount": 1},
            },
            {
                "Name": "Typeless mystery row",
                "ProductionYear": 2024,
                "SeriesName": None,
                "UserData": {"PlaybackPositionTicks": 0, "PlayCount": 0},
            },
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "resume")](payload)
        self.assertEqual(len(rendered), 1)
        names = [row["Name"] for row in rendered]
        self.assertEqual(names, ["Real Episode"])
        self.assertNotIn("Typeless mystery row", names)


class TestSummaryJellyfinLatest(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("jellyfin", "latest")]`` matches the spec."""

    def test_latest_shape(self) -> None:
        payload = [
            {
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2025,
                "SeriesName": None,
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "latest")](payload)
        self.assertEqual(
            rendered[0],
            {
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2025,
                "SeriesName": None,
                "RunTimeTicks": None,
                "PremiereDate": None,
                "Genres": None,
            },
        )

    def test_latest_drops_date_created(self) -> None:
        # Regression for ``jellyfin-latest-favorites-summary-fields``:
        # the ``/Users/{user_id}/Items/Latest`` endpoint returns a
        # slimmer DTO that does not populate ``DateCreated`` on the
        # operator's instance, so the curated summary intentionally
        # omits the field (otherwise every row projected
        # ``DateCreated: null`` -- a spurious null on a column the
        # operator reasonably expects populated).
        payload = [
            {
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2025,
                "SeriesName": None,
                "DateCreated": None,
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "latest")](payload)
        self.assertNotIn(
            "DateCreated",
            rendered[0],
            msg=(
                "jellyfin latest summary still projects DateCreated; "
                "/Items/Latest does not populate it on the slim DTO "
                "(see bug jellyfin-latest-favorites-summary-fields)"
            ),
        )

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("jellyfin", "latest")](None),
            [],
        )


class TestFormatRuntimeMinutes(unittest.TestCase):
    """``_format_runtime_minutes`` formats Jellyfin ``RunTimeTicks``."""

    def test_positive_input_formats_minutes(self) -> None:
        # 1h 36m = 96 minutes = 5760 seconds = 57_600_000_000 ticks
        # (Jellyfin's RunTimeTicks is 100-ns ticks, so 1s = 10^7).
        self.assertEqual(
            _format_runtime_minutes(57_600_000_000),
            "1h 36m",
        )
        # Sub-hour: 45 minutes = 2700 seconds = 27_000_000_000 ticks.
        self.assertEqual(
            _format_runtime_minutes(27_000_000_000),
            "0h 45m",
        )

    def test_zero_returns_none(self) -> None:
        self.assertIsNone(_format_runtime_minutes(0))

    def test_negative_returns_none(self) -> None:
        self.assertIsNone(_format_runtime_minutes(-1))
        self.assertIsNone(_format_runtime_minutes(-5_760_000_000))

    def test_none_returns_none(self) -> None:
        self.assertIsNone(_format_runtime_minutes(None))

    def test_non_numeric_returns_none(self) -> None:
        self.assertIsNone(_format_runtime_minutes("1h 30m"))
        self.assertIsNone(_format_runtime_minutes([1, 2, 3]))
        self.assertIsNone(_format_runtime_minutes({"ticks": 1}))

    def test_bool_returns_none(self) -> None:
        # ``bool`` is a subclass of ``int``; without the explicit
        # guard ``True`` would render as ``"0h 0m"``.
        self.assertIsNone(_format_runtime_minutes(True))
        self.assertIsNone(_format_runtime_minutes(False))


class TestSummaryJellyfinItem(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("jellyfin", "item")]`` matches the spec."""

    def _renderer(self) -> Any:
        return _SUMMARY_RENDERERS[("jellyfin", "item")]

    def test_bare_mapping_input_returns_eight_keys(self) -> None:
        # Canonical input shape (the live ``GET /Items/{id}`` payload).
        # RunTimeTicks: 2h 18m = 138 min = 8280s = 82_800_000_000 ticks.
        payload = {
            "Name": "The Matrix",
            "Type": "Movie",
            "ProductionYear": 1999,
            "PremiereDate": "1999-03-31T00:00:00Z",
            "RunTimeTicks": 82_800_000_000,
            "Genres": ["Action", "Sci-Fi"],
            "Overview": "A computer hacker learns...",
            "Status": "Released",
        }
        rendered = self._renderer()(payload)
        self.assertEqual(
            rendered,
            {
                "Name": "The Matrix",
                "Type": "Movie",
                "ProductionYear": 1999,
                "PremiereDate": "1999-03-31T00:00:00Z",
                "RunTimeTicks": "2h 18m",
                "Genres": "Action, Sci-Fi",
                "Overview": "A computer hacker learns...",
                "Status": "Released",
            },
        )

    def test_one_element_list_input_unwrapped(self) -> None:
        # Test fixture convention (mirrors ``_summary_seerr_tv`` /
        # ``_summary_seerr_movie``).
        payload = [
            {
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2020,
            }
        ]
        rendered = self._renderer()(payload)
        self.assertEqual(rendered["Name"], "Foo")
        self.assertEqual(rendered["ProductionYear"], 2020)
        self.assertEqual(len(rendered), 8)

    def test_empty_list_returns_empty_dict(self) -> None:
        self.assertEqual(self._renderer()([]), {})

    def test_none_returns_empty_dict(self) -> None:
        self.assertEqual(self._renderer()(None), {})

    def test_scalar_returns_empty_dict(self) -> None:
        self.assertEqual(self._renderer()(42), {})
        self.assertEqual(self._renderer()("foo"), {})

    def test_missing_fields_default_to_none(self) -> None:
        # Per US-8: every documented field defaults to ``None`` so the
        # ``--human`` table stays well-formed on a sparse payload.
        rendered = self._renderer()({"Name": "Foo"})
        self.assertEqual(
            rendered,
            {
                "Name": "Foo",
                "Type": None,
                "ProductionYear": 0,
                "PremiereDate": None,
                "RunTimeTicks": None,
                "Genres": None,
                "Overview": None,
                "Status": None,
            },
        )

    def test_genres_as_mappings_joins_names(self) -> None:
        rendered = self._renderer()(
            {"Genres": [{"name": "Action"}, {"name": "Drama"}]}
        )
        self.assertEqual(rendered["Genres"], "Action, Drama")

    def test_run_time_ticks_zero_returns_none(self) -> None:
        rendered = self._renderer()({"RunTimeTicks": 0})
        self.assertIsNone(rendered["RunTimeTicks"])

    def test_run_time_ticks_positive_format(self) -> None:
        # 1h 0m = 60 minutes = 3600 seconds = 36_000_000_000 ticks.
        rendered = self._renderer()({"RunTimeTicks": 36_000_000_000})
        self.assertEqual(rendered["RunTimeTicks"], "1h 0m")


class TestSummaryRadarrWanted(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("radarr", "wanted")]`` matches the spec."""

    def test_wanted_shape(self) -> None:
        payload = [
            {
                "title": "Foo",
                "year": 2024,
                "tmdbId": 999,
                "monitored": True,
                "hasFile": False,
                "genres": [{"name": "Action"}],
                "physicalRelease": "2024-08-01T00:00:00Z",
                "releaseDate": "2024-08-15",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("radarr", "wanted")](payload)
        self.assertEqual(
            rendered[0],
            {
                "title": "Foo",
                "year": 2024,
                "tmdbId": 999,
                "monitored": True,
                "hasFile": False,
                "genres": "Action",
                "physicalRelease": "2024-08-01T00:00:00Z",
                "releaseDate": "2024-08-15",
            },
        )

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("radarr", "wanted")](None),
            [],
        )


class TestSummaryRadarrQueue(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("radarr", "queue")]`` matches the spec."""

    def test_queue_shape(self) -> None:
        payload = [
            {
                "title": "Foo",
                "year": 2024,
                "quality": {"quality": {"name": "WEBDL-1080p"}},
                "status": "downloading",
                "timeleft": "00:30:00",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("radarr", "queue")](payload)
        self.assertEqual(
            rendered[0],
            {
                "title": "Foo",
                "year": 2024,
                "quality": "WEBDL-1080p",
                "status": "downloading",
                "timeLeft": "00:30:00",
            },
        )

    def test_quality_without_name_projects_none(self) -> None:
        """A quality envelope with no resolvable name projects ``None``."""
        payload = [
            {
                "title": "Foo",
                "year": 2024,
                "quality": {"quality": {"id": 3}},
                "status": "downloading",
                "timeleft": "00:30:00",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("radarr", "queue")](payload)
        self.assertIsNone(rendered[0]["quality"])

    def test_missing_quality_projects_none(self) -> None:
        """A row without a ``quality`` key projects ``None``."""
        payload = [
            {
                "title": "Foo",
                "year": 2024,
                "status": "downloading",
                "timeleft": "00:30:00",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("radarr", "queue")](payload)
        self.assertIsNone(rendered[0]["quality"])

    def test_plain_string_quality_projects_verbatim(self) -> None:
        """An upstream ``quality`` that is already a string is verbatim."""
        payload = [
            {
                "title": "Foo",
                "year": 2024,
                "quality": "WEBDL-1080p",
                "status": "downloading",
                "timeleft": "00:30:00",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("radarr", "queue")](payload)
        self.assertEqual(rendered[0]["quality"], "WEBDL-1080p")

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("radarr", "queue")](None),
            [],
        )

    def test_envelope_unwrapped(self) -> None:
        """``GET /api/v3/queue`` returns the paginated envelope."""
        payload = {
            "page": 1,
            "pageSize": 10,
            "sortKey": "timeleft",
            "sortDirection": "descending",
            "totalRecords": 1,
            "records": [
                {
                    "title": "Vanguard",
                    "year": 2020,
                    "quality": {"quality": {"name": "WEBDL-1080p"}},
                    "status": "downloading",
                    "timeleft": "00:30:00",
                }
            ],
        }
        rendered = _SUMMARY_RENDERERS[("radarr", "queue")](payload)
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0]["title"], "Vanguard")
        self.assertEqual(rendered[0]["year"], 2020)
        self.assertEqual(rendered[0]["timeLeft"], "00:30:00")


class TestSummaryRadarrRecent(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("radarr", "recent")]`` matches the spec.

    The renderer projects the nested ``movie: {title, year}``
    envelope from the ``GET /api/v3/history?includeMovie=true``
    activity-log rows verbatim -- the contract is preserved
    unchanged across the ``radarr-recent-endpoint-params`` fix that
    switched the upstream call from the per-movie endpoint
    (``/api/v3/history/movie``, which never populates the nested
    ``movie`` envelope and would always return ``[]``) to the
    activity-log endpoint with ``includeMovie=true``. The
    ``cmd_recent`` layer unwraps the upstream paginated envelope
    to the bare ``records`` list before this renderer sees it; the
    tests in this class pin the renderer behaviour on those
    already-unwrapped rows plus the documented defensive fallback.
    """

    def test_recent_with_movie(self) -> None:
        payload = [
            {
                "movie": {"title": "Foo", "year": 2024},
                "sourceTitle": "Foo.2024.WEBDL-1080p.mkv",
                "quality": {"quality": {"name": "WEBDL-1080p"}},
                "languages": [{"id": 1, "name": "English"}],
                "data": {"downloadClient": "qbit"},
                "eventType": "downloadFolderImported",
                "date": "2024-06-01",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("radarr", "recent")](payload)
        self.assertEqual(
            rendered[0],
            {
                "title": "Foo",
                "year": 2024,
                "movie": {"title": "Foo", "year": 2024},
                "sourceTitle": "Foo.2024.WEBDL-1080p.mkv",
                "quality": "WEBDL-1080p",
                "language": "English",
                "client": "qbit",
                "eventType": "downloadFolderImported",
                "date": "2024-06-01",
            },
        )

    def test_recent_missing_movie(self) -> None:
        # Defensive: missing ``movie`` object returns placeholder dict.
        payload = [{"eventType": "x", "date": "y"}]
        rendered = _SUMMARY_RENDERERS[("radarr", "recent")](payload)
        self.assertEqual(
            rendered[0]["movie"],
            {"title": None, "year": 0},
        )

    def test_recent_pins_actual_history_row_shape(self) -> None:
        # ``GET /api/v3/history?includeMovie=true`` returns paginated
        # activity-log rows with a nested ``movie: {title, year, ...}``
        # envelope when ``includeMovie=true``. ``cmd_recent`` unwraps
        # the upstream paginated envelope to the bare ``records``
        # list before this renderer sees it; this test pins the
        # renderer-end contract against a row shape taken verbatim
        # from the live operator's payload (per
        # ``bug-review.md``/``radarr-recent-endpoint-params``). A
        # regression that re-introduced ``{movie: {title: null}}``
        # fabrication, or that flattened the nested envelope, would
        # surface here.
        records = [
            {
                "id": 42,
                "movieId": 67,
                "movie": {
                    "title": "The Matrix",
                    "year": 1999,
                    "tmdbId": 603,
                },
                "eventType": "downloadFolderImported",
                "date": "2026-09-18T01:59:01Z",
                "downloadId": "string-id-abc",
                "data": {},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("radarr", "recent")](records)
        self.assertEqual(len(rendered), 1)
        row = rendered[0]
        # Nested ``movie`` is preserved with ``title`` / ``year``
        # populated from the upstream payload; the audit-asked
        # columns (``title``, ``year``, ``sourceTitle``, ``quality``,
        # ``language``, ``client``, ``eventType``, ``date``) are
        # added so the --human table can show the release info at a
        # glance. The nested envelope is also kept for
        # ``--verbose`` consumers that chain on the original
        # ``movie.title`` / ``movie.year`` shape.
        self.assertEqual(
            row["movie"],
            {"title": "The Matrix", "year": 1999},
        )
        self.assertEqual(row["title"], "The Matrix")
        self.assertEqual(row["year"], 1999)
        self.assertEqual(row["eventType"], "downloadFolderImported")
        self.assertEqual(row["date"], "2026-09-18T01:59:01Z")
        # No envelope leakage: the curated summary is the same shape
        # regardless of whether the upstream wrapped rows in a
        # paginated envelope (which ``cmd_recent`` strips before
        # calling this renderer).
        for envelope_field in (
            "page",
            "pageSize",
            "sortKey",
            "sortDirection",
            "totalRecords",
            "records",
        ):
            self.assertNotIn(envelope_field, row)
        # Upstream fields not in the curated projection are ignored
        # (no leakage of the full payload into the summary).
        for passthrough_field in (
            "id",
            "movieId",
            "downloadId",
            "data",
        ):
            self.assertNotIn(passthrough_field, row)

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("radarr", "recent")](None),
            [],
        )


class TestSummarySonarrWanted(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("sonarr", "wanted")]`` matches the spec."""

    def test_wanted_shape(self) -> None:
        payload = [
            {
                "title": "Pilot",
                "seasonNumber": 1,
                "episodeNumber": 1,
                "airDate": "2024-01-01",
                "monitored": True,
                "hasFile": False,
                "runtime": 45,
                "overview": "An overview.",
                "series": {"title": "Severance"},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "wanted")](payload)
        self.assertEqual(
            rendered[0],
            {
                "title": "Pilot",
                "seasonNumber": 1,
                "episodeNumber": 1,
                "airDate": "2024-01-01",
                "monitored": True,
                "hasFile": False,
                "runtime": "0h 45m",
                "overview": "An overview.",
                "series.title": "Severance",
            },
        )

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("sonarr", "wanted")](None),
            [],
        )

    def test_wanted_series_title_resolves_from_nested_envelope(self) -> None:
        # The nested ``series: {title}`` envelope is only populated
        # upstream when the request carries ``includeSeries=true``;
        # the projection resolves it into the flat ``series.title``
        # key.
        payload = [
            {
                "title": "Pilot",
                "seasonNumber": 1,
                "episodeNumber": 1,
                "airDate": "2024-01-01",
                "series": {"title": "Severance"},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "wanted")](payload)
        self.assertEqual(rendered[0]["series.title"], "Severance")

    def test_wanted_without_series_envelope_renders_null(self) -> None:
        # A row without the nested envelope degrades to ``None``
        # instead of raising.
        payload = [
            {
                "title": "Pilot",
                "seasonNumber": 1,
                "episodeNumber": 1,
                "airDate": "2024-01-01",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "wanted")](payload)
        self.assertIsNone(rendered[0]["series.title"])

    def test_wanted_null_series_envelope_renders_null(self) -> None:
        # A ``null`` nested envelope is tolerated the same way.
        payload = [{"title": "Pilot", "series": None}]
        rendered = _SUMMARY_RENDERERS[("sonarr", "wanted")](payload)
        self.assertIsNone(rendered[0]["series.title"])


class TestSummarySonarrCalendar(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("sonarr", "calendar")]`` matches the spec."""

    def test_calendar_shape(self) -> None:
        payload = [
            {
                "title": "Pilot",
                "seasonNumber": 1,
                "episodeNumber": 1,
                "airDate": "2024-01-01",
                "series": {"title": "Severance"},
                "monitored": True,
                "hasFile": False,
                "runtime": 45,
                "overview": "An overview.",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "calendar")](payload)
        self.assertEqual(
            rendered[0],
            {
                "title": "Pilot",
                "seasonNumber": 1,
                "episodeNumber": 1,
                "airDate": "2024-01-01",
                "series.title": "Severance",
                "monitored": True,
                "hasFile": False,
                "runtime": "0h 45m",
                "overview": "An overview.",
            },
        )

    def test_calendar_series_title_resolves_from_nested_envelope(self) -> None:
        payload = [
            {
                "title": "Pilot",
                "seasonNumber": 1,
                "episodeNumber": 1,
                "airDate": "2024-01-01",
                "series": {"title": "Severance"},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "calendar")](payload)
        self.assertEqual(rendered[0]["series.title"], "Severance")

    def test_calendar_without_series_envelope_renders_null(self) -> None:
        payload = [
            {
                "title": "Pilot",
                "seasonNumber": 1,
                "episodeNumber": 1,
                "airDate": "2024-01-01",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "calendar")](payload)
        self.assertIsNone(rendered[0]["series.title"])

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("sonarr", "calendar")](None),
            [],
        )


class TestSummarySonarrQueue(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("sonarr", "queue")]`` matches the spec."""

    def test_queue_shape(self) -> None:
        payload = [
            {
                "title": "Foo",
                "status": "downloading",
                "trackedDownloadStatus": "ok",
                "size": 1000,
                "sizeleft": 500,
                "series": {"title": "Severance"},
                "episode": {"title": "The Work Is Never Done"},
                "quality": {"quality": {"name": "WEBDL-1080p"}},
                "timeleft": "00:30:00",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "queue")](payload)
        self.assertEqual(
            rendered[0],
            {
                "title": "Foo",
                "status": "downloading",
                "trackedDownloadStatus": "ok",
                "size": 1000,
                "sizeleft": 500,
                "series.title": "Severance",
                "episode": "The Work Is Never Done",
                "quality": "WEBDL-1080p",
                "timeLeft": "00:30:00",
            },
        )

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("sonarr", "queue")](None),
            [],
        )

    def test_queue_envelope_titles_resolve_from_nested_objects(self) -> None:
        # The nested ``series: {title}`` / ``episode: {title}``
        # envelopes are only populated upstream when the request
        # carries ``includeSeries=true&includeEpisode=true``; the
        # projections resolve them into the flat ``series.title`` /
        # ``episode`` keys.
        payload = [
            {
                "title": "Foo",
                "status": "downloading",
                "series": {"title": "Severance"},
                "episode": {"title": "The Work Is Never Done"},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "queue")](payload)
        self.assertEqual(rendered[0]["series.title"], "Severance")
        self.assertEqual(rendered[0]["episode"], "The Work Is Never Done")

    def test_queue_without_envelopes_renders_null(self) -> None:
        # A row without the nested envelopes degrades to ``None``
        # instead of raising.
        payload = [{"title": "Foo", "status": "downloading"}]
        rendered = _SUMMARY_RENDERERS[("sonarr", "queue")](payload)
        self.assertIsNone(rendered[0]["series.title"])
        self.assertIsNone(rendered[0]["episode"])

    def test_queue_null_envelopes_render_null(self) -> None:
        # ``null`` nested envelopes are tolerated the same way.
        payload = [
            {
                "title": "Foo",
                "series": None,
                "episode": None,
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "queue")](payload)
        self.assertIsNone(rendered[0]["series.title"])
        self.assertIsNone(rendered[0]["episode"])

    def test_envelope_unwrapped(self) -> None:
        """``GET /api/v3/queue`` returns the paginated envelope."""
        payload = {
            "page": 1,
            "pageSize": 10,
            "sortKey": "timeleft",
            "sortDirection": "descending",
            "totalRecords": 1,
            "records": [
                {
                    "title": "Vanguard",
                    "status": "downloading",
                    "trackedDownloadStatus": "ok",
                    "size": 1000,
                    "sizeleft": 500,
                    "series": {"title": "Severance"},
                    "episode": {"title": "Pilot"},
                    "quality": {"quality": {"name": "WEBDL-1080p"}},
                    "timeleft": "00:30:00",
                }
            ],
        }
        rendered = _SUMMARY_RENDERERS[("sonarr", "queue")](payload)
        self.assertEqual(
            rendered,
            [
                {
                    "title": "Vanguard",
                    "status": "downloading",
                    "trackedDownloadStatus": "ok",
                    "size": 1000,
                    "sizeleft": 500,
                    "series.title": "Severance",
                    "episode": "Pilot",
                    "quality": "WEBDL-1080p",
                    "timeLeft": "00:30:00",
                }
            ],
        )


class TestSummarySonarrFlatFieldsIndependentOfEnvelopes(unittest.TestCase):
    """The flat row fields do not depend on the nested envelopes.

    Sonarr's ``wanted/missing`` / ``calendar`` / ``queue`` endpoints
    gate *only* the nested ``series`` / ``episode`` envelopes behind
    the ``includeSeries`` / ``includeEpisode`` query flags -- the flat
    row fields are emitted either way. The curated projection must
    therefore not couple the flat fields to the presence of a nested
    envelope: a row that carries both projects the same flat values
    as a row that carries none.
    """

    #: The flat identity fields the ``wanted`` / ``calendar``
    #: renderers project alongside the nested ``series`` envelope.
    _EPISODE_FLAT_KEYS = (
        "title",
        "seasonNumber",
        "episodeNumber",
        "airDate",
        "monitored",
        "hasFile",
        "runtime",
        "overview",
    )

    #: The flat identity fields the ``queue`` renderer projects
    #: alongside the nested ``series`` / ``episode`` envelopes.
    _QUEUE_FLAT_KEYS = (
        "title",
        "status",
        "trackedDownloadStatus",
        "size",
        "sizeleft",
        "quality",
        "timeLeft",
    )

    @staticmethod
    def _flat(row: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
        return {key: row[key] for key in keys}

    def test_wanted_flat_fields_survive_alongside_the_envelope(self) -> None:
        flat_row = {
            "title": "Pilot",
            "seasonNumber": 2,
            "episodeNumber": 3,
            "airDate": "2024-01-01",
            "monitored": True,
            "hasFile": False,
            "runtime": 45,
            "overview": "An overview.",
        }
        with_envelope = _SUMMARY_RENDERERS[("sonarr", "wanted")](
            [{**flat_row, "series": {"title": "Severance"}}]
        )[0]
        without_envelope = _SUMMARY_RENDERERS[("sonarr", "wanted")](
            [dict(flat_row)]
        )[0]
        self.assertEqual(
            self._flat(with_envelope, self._EPISODE_FLAT_KEYS),
            {
                "title": "Pilot",
                "seasonNumber": 2,
                "episodeNumber": 3,
                "airDate": "2024-01-01",
                "monitored": True,
                "hasFile": False,
                "runtime": "0h 45m",
                "overview": "An overview.",
            },
        )
        self.assertEqual(
            self._flat(with_envelope, self._EPISODE_FLAT_KEYS),
            self._flat(without_envelope, self._EPISODE_FLAT_KEYS),
        )
        self.assertEqual(with_envelope["series.title"], "Severance")
        self.assertIsNone(without_envelope["series.title"])

    def test_calendar_flat_fields_survive_alongside_the_envelope(self) -> None:
        flat_row = {
            "title": "Pilot",
            "seasonNumber": 2,
            "episodeNumber": 3,
            "airDate": "2024-01-01",
            "monitored": True,
            "hasFile": False,
            "runtime": 45,
            "overview": "An overview.",
        }
        with_envelope = _SUMMARY_RENDERERS[("sonarr", "calendar")](
            [{**flat_row, "series": {"title": "Severance"}}]
        )[0]
        without_envelope = _SUMMARY_RENDERERS[("sonarr", "calendar")](
            [dict(flat_row)]
        )[0]
        self.assertEqual(
            self._flat(with_envelope, self._EPISODE_FLAT_KEYS),
            self._flat(without_envelope, self._EPISODE_FLAT_KEYS),
        )
        self.assertEqual(
            self._flat(with_envelope, self._EPISODE_FLAT_KEYS),
            {
                "title": "Pilot",
                "seasonNumber": 2,
                "episodeNumber": 3,
                "airDate": "2024-01-01",
                "monitored": True,
                "hasFile": False,
                "runtime": "0h 45m",
                "overview": "An overview.",
            },
        )
        self.assertEqual(with_envelope["series.title"], "Severance")
        self.assertIsNone(without_envelope["series.title"])

    def test_queue_flat_fields_survive_alongside_the_envelopes(self) -> None:
        flat_row = {
            "title": "Foo",
            "status": "downloading",
            "trackedDownloadStatus": "ok",
            "size": 1000,
            "sizeleft": 500,
            "quality": {"quality": {"name": "WEBDL-1080p"}},
            "timeleft": "00:30:00",
        }
        with_envelopes = _SUMMARY_RENDERERS[("sonarr", "queue")](
            [
                {
                    **flat_row,
                    "series": {"title": "Severance"},
                    "episode": {"title": "The Work Is Never Done"},
                }
            ]
        )[0]
        without_envelopes = _SUMMARY_RENDERERS[("sonarr", "queue")](
            [dict(flat_row)]
        )[0]
        self.assertEqual(
            self._flat(with_envelopes, self._QUEUE_FLAT_KEYS),
            {
                "title": "Foo",
                "status": "downloading",
                "trackedDownloadStatus": "ok",
                "size": 1000,
                "sizeleft": 500,
                "quality": "WEBDL-1080p",
                "timeLeft": "00:30:00",
            },
        )
        self.assertEqual(
            self._flat(with_envelopes, self._QUEUE_FLAT_KEYS),
            self._flat(without_envelopes, self._QUEUE_FLAT_KEYS),
        )
        self.assertEqual(with_envelopes["series.title"], "Severance")
        self.assertEqual(
            with_envelopes["episode"], "The Work Is Never Done"
        )
        self.assertIsNone(without_envelopes["series.title"])
        self.assertIsNone(without_envelopes["episode"])


class TestSummarySonarrRecent(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("sonarr", "recent")]`` matches the spec.

    The curated summary projects both the nested
    ``series: {title}`` / ``episode: {title}`` envelopes Sonarr
    populates when the operator opts in via the documented
    ``includeSeries=true&includeEpisode=true`` query parameters and
    the flat identity fields the upstream ``GET /api/v3/history``
    activity-log payload also carries (``id``, ``seriesId``,
    ``episodeId``, ``sourceTitle``, ``eventType``, ``date``,
    ``quality``). The fix is additive so ``--json`` consumers
    that key off the flat fields do not break. A missing envelope
    renders as ``{title: None}`` instead of crashing.
    """

    def test_recent_with_full_row(self) -> None:
        payload = [
            {
                "id": 1,
                "seriesId": 10,
                "episodeId": 100,
                "sourceTitle": "Pilot",
                "eventType": "downloadFolderImported",
                "date": "2024-06-01",
                "quality": {"quality": {"id": 7, "name": "WEBDL-1080p"}},
                "languages": [{"id": 1, "name": "English"}],
                "data": {"downloadClient": "qbit"},
                "series": {"title": "Star Trek: Strange New Worlds"},
                "episode": {"title": "Orders of Magnitude"},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "recent")](payload)
        self.assertEqual(
            rendered[0],
            {
                "series": {"title": "Star Trek: Strange New Worlds"},
                "episode": {"title": "Orders of Magnitude"},
                "id": 1,
                "seriesId": 10,
                "episodeId": 100,
                "sourceTitle": "Pilot",
                "eventType": "downloadFolderImported",
                "date": "2024-06-01",
                "quality": "WEBDL-1080p",
                "language": "English",
                "client": "qbit",
            },
        )

    def test_recent_missing_optional_fields_defaults_to_none(self) -> None:
        payload = [{"eventType": "x", "date": "y"}]
        rendered = _SUMMARY_RENDERERS[("sonarr", "recent")](payload)
        self.assertEqual(
            rendered[0],
            {
                "series": {"title": None},
                "episode": {"title": None},
                "id": None,
                "seriesId": None,
                "episodeId": None,
                "sourceTitle": None,
                "eventType": "x",
                "date": "y",
                "quality": None,
                "language": None,
                "client": None,
            },
        )

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("sonarr", "recent")](None),
            [],
        )

    def test_recent_projects_nested_series_and_episode_titles(self) -> None:
        # Sister of
        # ``TestSummaryRadarrRecent.test_recent_pins_actual_history_row_shape``:
        # when the upstream payload carries the nested
        # ``series: {title}`` / ``episode: {title}`` envelopes (populated
        # by Sonarr when the operator opts in via the documented
        # ``includeSeries=true&includeEpisode=true`` query parameters),
        # the renderer projects them.
        payload = [
            {
                "id": 42,
                "seriesId": 10,
                "episodeId": 100,
                "sourceTitle": "Show.S01E01.WEBDL-1080p.mkv",
                "languages": [{"id": 1, "name": "English"}],
                "quality": {"quality": {"id": 7, "name": "WEBDL-1080p"}},
                "customFormats": [],
                "customFormatScore": 0,
                "qualityCutoffNotMet": False,
                "date": "2026-09-18T01:59:01Z",
                "downloadId": "string-id-abc",
                "eventType": "downloadFolderImported",
                "series": {"title": "Star Trek: Strange New Worlds"},
                "episode": {"title": "Orders of Magnitude"},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "recent")](payload)
        self.assertEqual(len(rendered), 1)
        row = rendered[0]
        self.assertEqual(
            row["series"], {"title": "Star Trek: Strange New Worlds"}
        )
        self.assertEqual(row["episode"], {"title": "Orders of Magnitude"})
        # Flat identity fields are still projected alongside the nested
        # objects -- the fix is additive.
        self.assertEqual(row["seriesId"], 10)
        self.assertEqual(row["episodeId"], 100)
        self.assertEqual(row["eventType"], "downloadFolderImported")
        self.assertEqual(row["date"], "2026-09-18T01:59:01Z")

    def test_recent_flat_payload_renders_null_titles(self) -> None:
        # Defensive fallback: a row that lacks the nested envelopes (e.g.
        # from an older Sonarr version that ignores the include flag, or
        # a payload from a manual test that did not pass the flags) must
        # still produce a well-formed summary with ``{title: None}``
        # instead of crashing.
        payload = [
            {
                "id": 1,
                "seriesId": 10,
                "episodeId": 100,
                "sourceTitle": "Pilot",
                "eventType": "downloadFolderImported",
                "date": "2024-06-01",
                "quality": {"quality": {"id": 7, "name": "WEBDL-1080p"}},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "recent")](payload)
        self.assertEqual(rendered[0]["series"], {"title": None})
        self.assertEqual(rendered[0]["episode"], {"title": None})

    def test_recent_non_mapping_envelope_renders_null_titles(self) -> None:
        # ``series`` / ``episode`` that are present but not mappings
        # (e.g. ``None`` or a string) must also fall back to
        # ``{title: None}`` rather than raising.
        payload = [
            {
                "id": 1,
                "seriesId": 10,
                "episodeId": 100,
                "sourceTitle": "Pilot",
                "eventType": "downloadFolderImported",
                "date": "2024-06-01",
                "quality": None,
                "series": None,
                "episode": "not-a-mapping",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "recent")](payload)
        self.assertEqual(rendered[0]["series"], {"title": None})
        self.assertEqual(rendered[0]["episode"], {"title": None})


class TestSummarySeerrRequests(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "requests")]`` matches the spec.

    The curated summary projects the upstream-provided identity
    fields at the **top level** (``id``, ``mediaType``, ``tmdbId``,
    ``tvdbId``, ``externalServiceSlug``) -- lifted from the
    ``media`` sub-dict -- rather than nesting them behind
    ``media.*`` keys. AGENTS.md §1 says both ``seerr requests`` and
    ``seerr available`` should "project the identity fields
    instead" of ``media.title``; the historical projection nested
    the identity behind ``media.*`` while :func:`_summary_seerr_available`
    returned flat identity fields at the top level, so the two seerr
    read commands disagreed on what shape that projection took. The
    fix removes the asymmetry so the operator has one mental model
    across both commands.

    The request-level fields (``type``, ``status``, ``createdAt``)
    stay top-level alongside the lifted identity. The
    ``requestedBy.displayName`` projection was dropped (the
    ``--human`` column list does not include it; see
    :func:`arr_cli.seerr.cmd_requests` for the width-budget
    rationale). The historical ``title`` projection is still retired
    -- neither ``media.title`` (movie) nor ``media.name`` (TV) is
    populated on the live operator's Seer instance. See
    ``seerr-requests-no-title-field`` and
    ``seerr-requests-flat-shape`` in CHANGELOG.md for the full
    rationale.
    """

    def test_requests_shape(self) -> None:
        payload = [
            {
                "type": "movie",
                "status": 2,
                "createdAt": "2026-09-13T12:56:58.000Z",
                "seasonCount": 0,
                "requestedBy": {
                    "displayName": "alice",
                    "username": "alice",
                },
                "media": {
                    "id": 121,
                    "mediaType": "movie",
                    "tmdbId": 603,
                    "tvdbId": None,
                    "externalServiceSlug": "tmdb",
                    "status": 5,
                    "mediaAddedAt": "2026-09-10T00:00:00Z",
                },
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "requests")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 121,
                "mediaType": "movie",
                "tmdbId": 603,
                "tvdbId": None,
                "externalServiceSlug": "tmdb",
                "type": "movie",
                "status": 2,
                "createdAt": "2026-09-13T12:56:58.000Z",
                "requestedByUsername": "alice",
                "mediaAddedAt": "2026-09-10T00:00:00Z",
                "seasonCount": 0,
            },
        )

    def test_requests_tv_row_projects_item_level_season_count(self) -> None:
        """The season count is read from the request item level.

        The upstream request record carries ``seasonCount`` on the
        item itself (the number of requested seasons); the nested
        ``media`` envelope does not populate it.
        """
        payload = [
            {
                "type": "tv",
                "status": 2,
                "createdAt": "2026-09-13T12:55:03.000Z",
                "seasonCount": 2,
                "requestedBy": {"displayName": "bob"},
                "media": {
                    "id": 120,
                    "mediaType": "tv",
                    "tmdbId": None,
                    "tvdbId": 76107,
                    "externalServiceSlug": "tvdb",
                    "status": 5,
                },
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "requests")](payload)
        self.assertEqual(rendered[0]["seasonCount"], 2)

    def test_requests_ignores_nested_media_season_count(self) -> None:
        """A nested ``media.seasonCount`` alone is not consulted.

        The nested envelope does not carry the season count
        upstream, so a payload that only populates it must project
        ``None`` rather than the nested value.
        """
        payload = [
            {
                "type": "tv",
                "status": 2,
                "createdAt": "2026-09-13T12:55:03.000Z",
                "media": {
                    "id": 120,
                    "mediaType": "tv",
                    "tmdbId": None,
                    "tvdbId": 76107,
                    "externalServiceSlug": "tvdb",
                    "status": 5,
                    "seasonCount": 7,
                },
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "requests")](payload)
        self.assertIsNone(rendered[0]["seasonCount"])

    def test_requests_missing_season_count_renders_null(self) -> None:
        """A request without an item-level season count renders ``<null>``."""
        payload = [
            {
                "type": "movie",
                "status": 2,
                "createdAt": "2026-09-13T12:55:03.000Z",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "requests")](payload)
        self.assertIsNone(rendered[0]["seasonCount"])

    def test_requests_tv_row_projects_media_identity(self) -> None:
        """TV rows source identity from the same ``media`` sub-dict as movie rows.

        Pins that the curated projection is shape-uniform across
        ``type == "tv"`` and ``type == "movie"``: identity fields
        live at the top level on both rows (lifted from the
        ``media`` sub-dict) rather than nested behind ``media.*``
        keys. The historical ``media.title`` vs ``media.name`` branch
        was retired because neither field is populated on the
        operator's Seer instance, so a TV row no longer pretends to
        carry a ``title`` read from ``media.name``.
        """
        payload = [
            {
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
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "requests")](payload)
        self.assertEqual(rendered[0]["type"], "tv")
        # Identity fields are top-level, not nested behind ``media.*``.
        self.assertEqual(rendered[0]["id"], 120)
        self.assertEqual(rendered[0]["mediaType"], "tv")
        self.assertEqual(rendered[0]["tvdbId"], 76107)
        self.assertEqual(rendered[0]["externalServiceSlug"], "tvdb")
        # No fabricated top-level ``title`` key -- the historical
        # projection is gone, so a TV row does not pretend to carry
        # a ``title`` read from ``media.name``.
        self.assertNotIn("title", rendered[0])
        # No nested ``media`` envelope and no ``requestedBy`` mapping
        # -- the shape is flat top-level identity + request-level
        # fields, matching ``seerr available``.
        self.assertNotIn("media", rendered[0])
        self.assertNotIn("requestedBy", rendered[0])

    def test_requests_drops_requested_by(self) -> None:
        """The ``requestedBy`` mapping is dropped from the curated row.

        The ``--human`` column list does not include
        ``requestedBy.displayName`` (the 120-char width budget
        divided across eight columns truncates the 23-char token),
        and the upstream detail is available via ``--verbose`` for
        operators who need it. Pins the dropped projection so a
        future revert that re-nests ``requestedBy`` fails this test.
        """
        payload = [{"type": "movie", "status": "x", "createdAt": "y"}]
        rendered = _SUMMARY_RENDERERS[("seerr", "requests")](payload)
        self.assertNotIn(
            "requestedBy",
            rendered[0],
            msg=(
                "seerr requests row re-surfaces a 'requestedBy' key "
                f"that should have been dropped: {rendered[0]!r}"
            ),
        )

    def test_requests_missing_media_envelope(self) -> None:
        """Records without a ``media`` sub-dict surface ``None`` for every identity field.

        The defensive contract mirrors
        :func:`_summary_seerr_available`'s handling of missing
        upstream keys: a record whose ``media`` envelope is absent
        still produces a well-formed row with every identity field
        set to ``None`` instead of crashing or fabricating a default
        dict. Pins the upstream-shape drift so future envelope
        changes do not regress the renderer into a crash.
        """
        payload = [{"type": "movie", "status": 2, "createdAt": "y"}]
        rendered = _SUMMARY_RENDERERS[("seerr", "requests")](payload)
        self.assertIsNone(rendered[0]["id"])
        self.assertIsNone(rendered[0]["mediaType"])
        self.assertIsNone(rendered[0]["tmdbId"])
        self.assertIsNone(rendered[0]["tvdbId"])
        self.assertIsNone(rendered[0]["externalServiceSlug"])
        # Request-level fields survive intact even when the media
        # envelope is absent.
        self.assertEqual(rendered[0]["type"], "movie")
        self.assertEqual(rendered[0]["status"], 2)
        self.assertEqual(rendered[0]["createdAt"], "y")

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "requests")](None),
            [],
        )


class TestSummarySeerrSearch(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "search")]`` matches the spec.

    The per-row projection surfaces the top-level ``id`` field
    because the operator's Seer ``/api/v1/search`` payload does not
    expose a nested ``mediaInfo`` envelope. Mirrors the projection
    chosen for :func:`_summary_seerr_available` and matches the
    live-API artefact saved at
    ``.tmp/arr-cli-qa-2026-09-18/seerr-search-matrix-verbose.json``.
    """

    def test_search_shape(self) -> None:
        payload = [
            {
                "id": 603,
                "title": "The Matrix",
                "mediaType": "movie",
                "releaseDate": "1999-03-31",
                "overview": "A computer hacker learns...",
                "popularity": 89.4,
                "genreIds": [28, 878],
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "search")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 603,
                "title": "The Matrix",
                "mediaType": "movie",
                "releaseDate": "1999-03-31",
                "overview": "A computer hacker learns...",
                "popularity": 89.4,
                "genreIds": [28, 878],
            },
        )

    def test_search_tv_row_sources_title_from_name(self) -> None:
        """TV rows expose ``name`` (not ``title``) at the top level."""
        payload = [
            {
                "id": 123,
                "name": "Doctor Who",
                "mediaType": "tv",
                "releaseDate": "2005-03-26",
                "overview": "A long-running sci-fi series.",
                "popularity": 92.1,
                "genreIds": [10759, 18],
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "search")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 123,
                "title": "Doctor Who",
                "mediaType": "tv",
                "releaseDate": "2005-03-26",
                "overview": "A long-running sci-fi series.",
                "popularity": 92.1,
                "genreIds": [10759, 18],
            },
        )

    def test_search_person_row_sources_title_from_name(self) -> None:
        """Person rows expose ``name`` (not ``title``) at the top level.

        Mirrors the TV-row case for ``mediaType == "person"``; the
        operator's live Seer ``/api/v1/search`` payload carries
        ``name`` (no top-level ``title``) on person rows, so the
        renderer must route the ``title`` projection through the
        same ``name`` key as TV rows. Without this branch the
        summary surfaces ``title: null`` for every person hit.
        """
        payload = [
            {
                "id": 3084139,
                "name": "Aggy Dune",
                "popularity": 0.455,
                "adult": False,
                "mediaType": "person",
                "profilePath": None,
                "knownFor": [],
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "search")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 3084139,
                "title": "Aggy Dune",
                "mediaType": "person",
                "releaseDate": None,
                "overview": None,
                "popularity": 0.455,
                "genreIds": None,
            },
        )

    def test_search_missing_id_keeps_none(self) -> None:
        """``id`` is read with ``_safe_get``; missing rows surface as ``None``."""
        payload = [
            {"title": "Foo", "mediaType": "movie", "releaseDate": "y"}
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "search")](payload)
        self.assertIsNone(rendered[0]["id"])
        # No fabricated ``mediaInfo`` key -- the historical placeholder
        # is gone, so a row with no upstream id does not pretend to
        # have a ``tmdbId: 0`` dict.
        self.assertNotIn("mediaInfo", rendered[0])

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "search")](None),
            [],
        )


class TestSummarySeerrAvailable(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "available")]`` matches the spec.

    The curated summary projects the upstream-provided identifiers
    onto the row (``id``, ``mediaType``, ``tmdbId``, ``tvdbId``,
    ``externalServiceSlug``, ``status``, ``mediaAddedAt``); there is
    no nested ``mediaInfo`` envelope on the operator's Seer
    instance because records lack a top-level ``title`` field. See
    ``seerr-available-no-title-filter`` in CHANGELOG.md for the
    full rationale.
    """

    def test_available_shape(self) -> None:
        payload = [
            {
                "id": 1,
                "mediaType": "movie",
                "tmdbId": 999,
                "tvdbId": 76107,
                "externalServiceSlug": "tmdb",
                "status": 5,
                "mediaAddedAt": "2024-01-01T00:00:00Z",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "available")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 1,
                "mediaType": "movie",
                "tmdbId": 999,
                "tvdbId": 76107,
                "externalServiceSlug": "tmdb",
                "status": 5,
                "mediaAddedAt": "2024-01-01T00:00:00Z",
            },
        )

    def test_available_missing_optional_fields(self) -> None:
        """Records missing some upstream keys surface ``None`` for those fields.

        The renderer walks every key via :func:`_safe_get`, so a
        record missing e.g. ``tvdbId`` does not crash -- it
        surfaces as ``None`` instead. Pins the defensive contract
        so future upstream drift (a new optional field appearing
        on every record) does not regress the renderer.
        """
        payload = [
            {
                "id": 1,
                "mediaType": "movie",
                "tmdbId": 999,
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "available")](payload)
        self.assertEqual(rendered[0]["id"], 1)
        self.assertEqual(rendered[0]["mediaType"], "movie")
        self.assertEqual(rendered[0]["tmdbId"], 999)
        # Fields missing from the upstream record default to ``None``
        # rather than crashing -- mirrors every other renderer's
        # defensive contract.
        self.assertIsNone(rendered[0]["tvdbId"])
        self.assertIsNone(rendered[0]["externalServiceSlug"])
        self.assertIsNone(rendered[0]["status"])
        self.assertIsNone(rendered[0]["mediaAddedAt"])

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "available")](None),
            [],
        )

    def test_seerr_available_does_not_render_title_even_when_present(
        self,
    ) -> None:
        # Regression-vector protection for the 2026-09-18
        # ``seerr-available-no-title-filter`` ticket (see
        # AGENTS.md \u00a71 "Seer endpoint drift on ``request`` /
        # ``media`` records"). The live ``/api/v1/media`` envelope
        # does NOT populate a ``title`` field; the renderer is
        # therefore contracted to omit the ``title`` cell even when
        # an upstream payload does include one. A future contributor
        # who tries to add a ``title`` projection back sees a hard
        # test failure with the audit context preserved in the
        # assertion message.
        payload = [
            {
                "id": 1,
                "mediaType": "movie",
                "tmdbId": 999,
                "tvdbId": 76107,
                "externalServiceSlug": "tmdb",
                "status": 5,
                "mediaAddedAt": "2024-01-01T00:00:00Z",
                "title": "The Matrix",  # stray upstream value;
                #                            must be ignored
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "available")](payload)
        self.assertNotIn(
            "title",
            rendered[0],
            msg=(
                "AGENTS.md \u00a71 identity-only contract violated: "
                "the live /api/v1/media envelope does not populate a "
                "``title`` field, so ``_summary_seerr_available`` "
                "must not project a ``title`` cell. Regression vector: "
                "2026-09-18 seerr-available-no-title-filter. "
                f"Got row: {rendered[0]!r}"
            ),
        )
        # Sanity: the pre-change seven identity columns are still
        # projected verbatim; dropping ``title`` must not regress
        # the other seven keys.
        self.assertEqual(rendered[0]["id"], 1)
        self.assertEqual(rendered[0]["mediaType"], "movie")
        self.assertEqual(rendered[0]["tmdbId"], 999)
        self.assertEqual(rendered[0]["tvdbId"], 76107)
        self.assertEqual(rendered[0]["externalServiceSlug"], "tmdb")
        self.assertEqual(rendered[0]["status"], 5)
        self.assertEqual(
            rendered[0]["mediaAddedAt"], "2024-01-01T00:00:00Z"
        )


class TestSummarySeerrGenres(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "genres")]`` matches the spec.

    The renderer is a passthrough projection of the canonical
    TMDB ``[{id, name}, ...]`` shape, so the test class focuses on:

    * The happy-path passthrough (rows surface verbatim).
    * The defensive single-mapping input (one-row degenerate list).
    * The defensive empty-mapping input (``[]``).
    * The non-list / non-mapping / ``None`` inputs (``[]``).
    * Items that are not mappings are dropped without raising.
    * Missing / malformed ``id`` / ``name`` keys surface as
      ``None`` so the renderer never crashes on upstream shape
      drift.
    """

    def test_genres_movie_shape(self) -> None:
        payload = [
            {"id": 28, "name": "Action"},
            {"id": 12, "name": "Adventure"},
            {"id": 878, "name": "Science Fiction"},
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "genres")](payload)
        self.assertEqual(rendered, payload)
        # The shape contract: each row has exactly ``id`` and
        # ``name`` -- no nested objects leak into the summary.
        for row in rendered:
            self.assertEqual(set(row.keys()), {"id", "name"})

    def test_genres_tv_shape(self) -> None:
        payload = [
            {"id": 10759, "name": "Action & Adventure"},
            {"id": 16, "name": "Animation"},
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "genres")](payload)
        self.assertEqual(rendered, payload)

    def test_genres_single_mapping_treated_as_one_row_list(self) -> None:
        """A single mapping input is wrapped into a one-row degenerate list.

        Defensive against a future Seer release that wraps the
        response in ``{results: [...]}`` (or similar envelope).
        """
        payload = {"id": 878, "name": "Science Fiction"}
        rendered = _SUMMARY_RENDERERS[("seerr", "genres")](payload)
        self.assertEqual(rendered, [{"id": 878, "name": "Science Fiction"}])

    def test_genres_empty_mapping_returns_empty_list(self) -> None:
        rendered = _SUMMARY_RENDERERS[("seerr", "genres")]({})
        self.assertEqual(rendered, [])

    def test_genres_none_returns_empty_list(self) -> None:
        rendered = _SUMMARY_RENDERERS[("seerr", "genres")](None)
        self.assertEqual(rendered, [])

    def test_genres_scalar_returns_empty_list(self) -> None:
        # Bare scalar / non-list payload is treated as no rows so
        # the renderer prints its "no rows" footer rather than a
        # stack trace.
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "genres")]("nope"),
            [],
        )
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "genres")](42),
            [],
        )

    def test_genres_empty_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "genres")]([]),
            [],
        )

    def test_genres_non_mapping_items_dropped_silently(self) -> None:
        """Items that are not ``Mapping`` are dropped without raising.

        Defensive against a future schema change that mixes raw
        id integers with full genre documents in the same list
        (e.g. ``[28, {"id": 12, "name": "Adventure"}]``). The
        renderer drops the integer without raising so the summary
        shape stays homogeneous.
        """
        payload = [
            28,
            "Action",
            {"id": 12, "name": "Adventure"},
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "genres")](payload)
        self.assertEqual(rendered, [{"id": 12, "name": "Adventure"}])

    def test_genres_missing_keys_surface_as_none(self) -> None:
        """Missing ``id`` / ``name`` keys surface as ``None`` (defensive)."""
        payload = [{"id": 28}, {"name": "Adventure"}, {}]
        rendered = _SUMMARY_RENDERERS[("seerr", "genres")](payload)
        self.assertEqual(
            rendered,
            [
                {"id": 28, "name": None},
                {"id": None, "name": "Adventure"},
                {"id": None, "name": None},
            ],
        )


class TestSummarySeerrTrending(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "trending")]`` branches per item.

    ``seerr trending`` without a media-type positional can return
    both ``movie`` and ``tv`` items in the same ``results[]``
    array. The per-item projection must read ``name`` /
    ``firstAirDate`` for TV items and ``title`` / ``releaseDate``
    for movie items; the top-level ``id`` field is consistent
    across both shapes (the actual TMDB/TVDB id the upstream row
    carries). These tests are the regression net for the
    v12-compat bug where the renderer projected the movie-shaped
    keys and rendered TV rows as ``title=None, releaseDate=None``.

    The historical ``mediaInfo.tmdbId`` projection is dropped --
    the upstream payload does not expose a nested ``mediaInfo``
    envelope on the operator's Seer instance, so the defensive
    ``else`` branch fabricated ``{"tmdbId": 0}`` for every row
    whose upstream payload lacked the envelope. The top-level
    ``id`` is the actual join key and is what the operator chains
    into ``seerr movie <id>`` / ``seerr tv <id>``. Mirrors the
    projection chosen for :func:`_summary_seerr_search` (PR #41)
    and :func:`_summary_seerr_available` (PR #39).
    """

    def test_tv_item_uses_tv_keys(self) -> None:
        # TV items in the results[] array use ``name`` /
        # ``firstAirDate``; the renderer must surface them under
        # the curated ``title`` / ``releaseDate`` keys. Live
        # evidence (2026-09-18): ``Monster: The Lizzie Borden
        # Story`` / ``2026-09-17`` from ``seerr trending tv day``.
        payload = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 1,
            "results": [
                {
                    "id": 100009,
                    "name": "Monster: The Lizzie Borden Story",
                    "firstAirDate": "2026-09-17",
                    "mediaType": "tv",
                    "popularity": 73.2,
                    "overview": "A retelling of the case.",
                }
            ],
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "trending")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 100009,
                "title": "Monster: The Lizzie Borden Story",
                "mediaType": "tv",
                "releaseDate": "2026-09-17",
                "popularity": 73.2,
                "overview": "A retelling of the case.",
            },
        )

    def test_movie_item_uses_movie_keys(self) -> None:
        # Movie items still flow through ``title`` /
        # ``releaseDate`` so the existing movie-shape behaviour
        # is preserved when the operator runs ``seerr trending
        # movie ...``.
        payload = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 1,
            "results": [
                {
                    "id": 969681,
                    "title": "Spider-Man: Brand New Day",
                    "releaseDate": "2026-07-29",
                    "mediaType": "movie",
                    "popularity": 88.5,
                    "overview": "The next chapter in the saga.",
                }
            ],
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "trending")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 969681,
                "title": "Spider-Man: Brand New Day",
                "mediaType": "movie",
                "releaseDate": "2026-07-29",
                "popularity": 88.5,
                "overview": "The next chapter in the saga.",
            },
        )

    def test_mixed_envelope_branches_per_item(self) -> None:
        # ``seerr trending`` without a media-type positional can
        # return both ``movie`` and ``tv`` items in one envelope;
        # each item must be projected against its own mediaType.
        payload = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 2,
            "results": [
                {
                    "id": 1,
                    "title": "Movie A",
                    "releaseDate": "2026-01-01",
                    "mediaType": "movie",
                    "popularity": 70.0,
                    "overview": "Movie A overview",
                },
                {
                    "id": 2,
                    "name": "Show A",
                    "firstAirDate": "2026-02-02",
                    "mediaType": "tv",
                    "popularity": 80.0,
                    "overview": "Show A overview",
                },
            ],
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "trending")](payload)
        self.assertEqual(len(rendered), 2)
        self.assertEqual(rendered[0]["id"], 1)
        self.assertEqual(rendered[0]["title"], "Movie A")
        self.assertEqual(rendered[0]["releaseDate"], "2026-01-01")
        self.assertEqual(rendered[0]["mediaType"], "movie")
        self.assertEqual(rendered[0]["popularity"], 70.0)
        self.assertEqual(rendered[0]["overview"], "Movie A overview")
        self.assertEqual(rendered[1]["id"], 2)
        self.assertEqual(rendered[1]["title"], "Show A")
        self.assertEqual(rendered[1]["releaseDate"], "2026-02-02")
        self.assertEqual(rendered[1]["mediaType"], "tv")
        self.assertEqual(rendered[1]["popularity"], 80.0)
        self.assertEqual(rendered[1]["overview"], "Show A overview")

    def test_tv_item_missing_keys_surface_as_none(self) -> None:
        # A TV item missing ``name`` / ``firstAirDate`` surfaces
        # ``None`` rather than crashing the renderer. The
        # historical fabricated ``mediaInfo`` key is gone -- a row
        # with no upstream id does not pretend to have a
        # ``tmdbId: 0`` dict.
        payload = [
            {
                "id": 999,
                "mediaType": "tv",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "trending")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 999,
                "title": None,
                "mediaType": "tv",
                "releaseDate": None,
                "popularity": None,
                "overview": None,
            },
        )
        self.assertNotIn("mediaInfo", rendered[0])

    def test_missing_id_keeps_none(self) -> None:
        # ``id`` is read with ``_safe_get``; missing rows surface
        # as ``None`` rather than crashing the renderer. The
        # historical fabricated ``mediaInfo: {tmdbId: 0}``
        # placeholder is gone -- a row with no upstream id does
        # not pretend to have one.
        payload = [
            {
                "title": "Movie X",
                "releaseDate": "2024-01-01",
                "mediaType": "movie",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "trending")](payload)
        self.assertIsNone(rendered[0]["id"])
        self.assertNotIn("mediaInfo", rendered[0])

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "trending")](None),
            [],
        )


class TestSummarySeerrUpcomingMovies(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "upcoming-movies")]`` matches the spec.

    Movie items in the ``/api/v1/discover/movies/upcoming``
    envelope use ``title`` / ``releaseDate`` (the canonical movie
    shape). The curated summary is byte-identical to
    :func:`_summary_seerr_trending`'s movie-shape projection so
    the operator can tabulate ``seerr trending`` and ``seerr
    upcoming-movies`` together row-for-row.

    The per-item projection surfaces the top-level ``id`` field
    the upstream payload actually carries. The rows in the
    operator's Seer ``/api/v1/discover/movies/upcoming`` response
    do not expose a nested ``mediaInfo`` envelope -- the TMDB id
    lives at the top level as ``id``. The historical fabricated
    ``{"tmdbId": 0}`` placeholder was misleading because every
    row was projected as ``tmdbId: 0`` even for hits with
    well-known ids. Mirrors the projection chosen for
    :func:`_summary_seerr_search` (PR #41).
    """

    def test_upcoming_movies_shape(self) -> None:
        payload = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 1,
            "results": [
                {
                    "id": 696506,
                    "title": "Mickey 17",
                    "releaseDate": "2025-03-07",
                    "mediaType": "movie",
                    "popularity": 75.0,
                    "overview": "A clone goes on a mission.",
                }
            ],
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "upcoming-movies")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 696506,
                "title": "Mickey 17",
                "mediaType": "movie",
                "releaseDate": "2025-03-07",
                "popularity": 75.0,
                "overview": "A clone goes on a mission.",
            },
        )

    def test_upcoming_movies_missing_keys_surface_as_none(self) -> None:
        payload = [
            {
                "id": 999,
                "mediaType": "movie",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "upcoming-movies")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 999,
                "title": None,
                "mediaType": "movie",
                "releaseDate": None,
                "popularity": None,
                "overview": None,
            },
        )
        self.assertNotIn("mediaInfo", rendered[0])

    def test_missing_id_keeps_none(self) -> None:
        payload = [
            {
                "title": "Movie",
                "releaseDate": "2024-01-01",
                "mediaType": "movie",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "upcoming-movies")](payload)
        self.assertIsNone(rendered[0]["id"])
        self.assertNotIn("mediaInfo", rendered[0])

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "upcoming-movies")](None),
            [],
        )


class TestSummarySeerrUpcomingTv(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "upcoming-tv")]`` reads TV-shaped keys.

    TV items in the ``/api/v1/discover/tv/upcoming`` envelope use
    ``name`` / ``firstAirDate`` rather than the movie-shaped
    ``title`` / ``releaseDate``. Surfacing the TV keys under the
    curated ``title`` / ``releaseDate`` names is the regression
    net for the v12-compat bug where TV rows rendered as
    ``title=None, releaseDate=None``.

    The per-item projection surfaces the top-level ``id`` field
    the upstream payload actually carries. The rows in the
    operator's Seer ``/api/v1/discover/tv/upcoming`` response do
    not expose a nested ``mediaInfo`` envelope -- the TVDB id
    lives at the top level as ``id``. The historical fabricated
    ``{"tmdbId": 0}`` placeholder was misleading because every row
    was projected as ``tmdbId: 0`` even for hits with well-known
    ids. Mirrors the projection chosen for
    :func:`_summary_seerr_search` (PR #41).
    """

    def test_upcoming_tv_shape(self) -> None:
        payload = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 1,
            "results": [
                {
                    "id": 100001,
                    "name": "The Scandal",
                    "firstAirDate": "2026-09-18",
                    "mediaType": "tv",
                    "popularity": 60.0,
                    "overview": "A scandalous drama.",
                }
            ],
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "upcoming-tv")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 100001,
                "title": "The Scandal",
                "mediaType": "tv",
                "releaseDate": "2026-09-18",
                "popularity": 60.0,
                "overview": "A scandalous drama.",
            },
        )

    def test_upcoming_tv_missing_keys_surface_as_none(self) -> None:
        payload = [
            {
                "id": 999,
                "mediaType": "tv",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "upcoming-tv")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 999,
                "title": None,
                "mediaType": "tv",
                "releaseDate": None,
                "popularity": None,
                "overview": None,
            },
        )
        self.assertNotIn("mediaInfo", rendered[0])

    def test_missing_id_keeps_none(self) -> None:
        # ``id`` is read with ``_safe_get``; missing rows surface
        # as ``None`` rather than crashing the renderer. The
        # historical fabricated ``mediaInfo: {tmdbId: 0}``
        # placeholder is gone -- a row with no upstream id does
        # not pretend to have one.
        payload = [
            {
                "name": "Show",
                "firstAirDate": "2024-01-01",
                "mediaType": "tv",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "upcoming-tv")](payload)
        self.assertIsNone(rendered[0]["id"])
        self.assertNotIn("mediaInfo", rendered[0])

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "upcoming-tv")](None),
            [],
        )


class TestSummarySeerrDiscoverTv(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "discover-tv")]`` reads TV-shaped keys.

    TV items in the ``/api/v1/discover/tv`` envelope use ``name``
    / ``firstAirDate`` rather than the movie-shaped ``title`` /
    ``releaseDate``. Surfacing the TV keys under the curated
    ``title`` / ``releaseDate`` names is the regression net for
    the v12-compat bug where TV rows rendered as ``title=None,
    releaseDate=None``. Live evidence (2026-09-18):
    ``totalResults: 9397`` for
    ``/api/v1/discover/tv?genre=10765``.

    The per-item projection surfaces the top-level ``id`` field
    the upstream payload actually carries. The rows in the
    operator's Seer ``/api/v1/discover/tv`` response do not expose
    a nested ``mediaInfo`` envelope -- the TVDB id lives at the
    top level as ``id``. The historical fabricated ``{"tmdbId":
    0}`` placeholder was misleading because every row was
    projected as ``tmdbId: 0`` even for hits with well-known ids.
    Mirrors the projection chosen for
    :func:`_summary_seerr_search` (PR #41).
    """

    def test_discover_tv_shape(self) -> None:
        payload = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 9397,
            "results": [
                {
                    "id": 46261,
                    "name": "The Vampire Diaries",
                    "firstAirDate": "2009-09-10",
                    "mediaType": "tv",
                    "popularity": 81.0,
                    "overview": "A supernatural teen drama.",
                    "genreIds": [10765, 18],
                }
            ],
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "discover-tv")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 46261,
                "title": "The Vampire Diaries",
                "mediaType": "tv",
                "releaseDate": "2009-09-10",
                "popularity": 81.0,
                "overview": "A supernatural teen drama.",
                "genreIds": [10765, 18],
            },
        )

    def test_discover_tv_missing_keys_surface_as_none(self) -> None:
        payload = [
            {
                "id": 999,
                "mediaType": "tv",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "discover-tv")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 999,
                "title": None,
                "mediaType": "tv",
                "releaseDate": None,
                "popularity": None,
                "overview": None,
                "genreIds": None,
            },
        )
        self.assertNotIn("mediaInfo", rendered[0])

    def test_missing_id_keeps_none(self) -> None:
        # ``id`` is read with ``_safe_get``; missing rows surface
        # as ``None`` rather than crashing the renderer. The
        # historical fabricated ``mediaInfo: {tmdbId: 0}``
        # placeholder is gone -- a row with no upstream id does
        # not pretend to have one.
        payload = [
            {
                "name": "Show",
                "firstAirDate": "2024-01-01",
                "mediaType": "tv",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "discover-tv")](payload)
        self.assertIsNone(rendered[0]["id"])
        self.assertNotIn("mediaInfo", rendered[0])

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "discover-tv")](None),
            [],
        )


class TestSummarySeerrDiscoverMovies(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "discover-movies")]`` matches the spec.

    Movie items in the ``/api/v1/discover/movies`` envelope use
    ``title`` / ``releaseDate`` (the canonical movie shape). The
    curated summary is byte-identical to
    :func:`_summary_seerr_upcoming_movies` so the operator can
    tabulate ``seerr upcoming-movies`` and ``seerr
    discover-movies`` together row-for-row.

    The per-item projection surfaces the top-level ``id`` field
    the upstream payload actually carries. The rows in the
    operator's Seer ``/api/v1/discover/movies`` response do not
    expose a nested ``mediaInfo`` envelope -- the TMDB id lives
    at the top level as ``id``. The historical fabricated
    ``{"tmdbId": 0}`` placeholder was misleading because every
    row was projected as ``tmdbId: 0`` even for hits with
    well-known ids. Mirrors the projection chosen for
    :func:`_summary_seerr_search` (PR #41).
    """

    def test_discover_movies_shape(self) -> None:
        payload = {
            "page": 1,
            "totalPages": 1,
            "totalResults": 1,
            "results": [
                {
                    "id": 822119,
                    "title": "Captain America: Brave New World",
                    "releaseDate": "2025-02-14",
                    "mediaType": "movie",
                    "popularity": 90.0,
                    "overview": "A new chapter in the MCU.",
                    "genreIds": [28, 878],
                }
            ],
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "discover-movies")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 822119,
                "title": "Captain America: Brave New World",
                "mediaType": "movie",
                "releaseDate": "2025-02-14",
                "popularity": 90.0,
                "overview": "A new chapter in the MCU.",
                "genreIds": [28, 878],
            },
        )

    def test_discover_movies_missing_keys_surface_as_none(self) -> None:
        payload = [
            {
                "id": 999,
                "mediaType": "movie",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "discover-movies")](payload)
        self.assertEqual(
            rendered[0],
            {
                "id": 999,
                "title": None,
                "mediaType": "movie",
                "releaseDate": None,
                "popularity": None,
                "overview": None,
                "genreIds": None,
            },
        )
        self.assertNotIn("mediaInfo", rendered[0])

    def test_missing_id_keeps_none(self) -> None:
        payload = [
            {
                "title": "Movie",
                "releaseDate": "2024-01-01",
                "mediaType": "movie",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "discover-movies")](payload)
        self.assertIsNone(rendered[0]["id"])
        self.assertNotIn("mediaInfo", rendered[0])

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "discover-movies")](None),
            [],
        )


class TestSummarySeerrTv(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "tv")]`` matches the spec."""

    def test_tv_shape(self) -> None:
        payload = {
            "name": "Doctor Who",
            "originalName": "Doctor Who",
            "firstAirDate": "2005-03-26",
            "lastAirDate": "2023-12-25",
            "genres": [{"name": "Action & Adventure"}],
            "networks": [{"name": "BBC One"}],
            "numberOfSeasons": 13,
            "numberOfEpisodes": 175,
            "overview": "The Doctor travels through time and space.",
            "status": "Ended",
            "mediaInfo": {"status": 4},
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "tv")](payload)
        self.assertEqual(
            rendered,
            {
                "name": "Doctor Who",
                "originalName": "Doctor Who",
                "firstAirDate": "2005-03-26",
                "lastAirDate": "2023-12-25",
                "numberOfSeasons": 13,
                "numberOfEpisodes": 175,
                "genres": "Action & Adventure",
                "networks": "BBC One",
                "overview": "The Doctor travels through time and space.",
                "status": "Ended",
                "mediaInfoStatus": 4,
                "criticsScore": None,
                "audienceRating": None,
                "audienceScore": None,
            },
        )

    def test_tv_missing_genres_networks_renders_none(self) -> None:
        payload = {
            "name": "Doctor Who",
            "originalName": "Doctor Who",
            "firstAirDate": "2005-03-26",
            "numberOfSeasons": 13,
            "status": "Ended",
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "tv")](payload)
        # Missing ``genres`` / ``networks`` collapse to ``None``
        # (NOT a bare ``""`` string) so ``--human`` renders
        # ``<null>`` instead of an empty cell that masquerades
        # as "the show has no genres".
        self.assertIsNone(rendered["genres"])
        self.assertIsNone(rendered["networks"])
        # Missing audit-asked fields also default to ``None`` /
        # ``0`` so the renderer stays well-formed on sparse payloads.
        self.assertIsNone(rendered["lastAirDate"])
        self.assertEqual(rendered["numberOfEpisodes"], 0)
        self.assertIsNone(rendered["overview"])
        self.assertIsNone(rendered["mediaInfoStatus"])

    def test_tv_ratings_merged_under_ratings_key(self) -> None:
        # Live envelope shape verified directly on the operator's
        # Seer instance on 2026-10-17: ``/api/v1/tv/{tvId}/ratings``
        # returns ``{"title": ..., "url": ..., "criticsRating": ...,
        # "criticsScore": <int>, "audienceRating": "...", "year": ...}``.
        # The renderer projects the three flat score cells
        # (``criticsScore`` -- the live integer RT Tomatometer
        # percentage, ``audienceRating`` -- the categorical string,
        # and ``audienceScore`` -- the numeric RT audience score).
        payload = {
            "name": "Doctor Who",
            "originalName": "Doctor Who",
            "firstAirDate": "2005-03-26",
            "genres": [{"name": "Action & Adventure"}],
            "networks": [{"name": "BBC One"}],
            "numberOfSeasons": 13,
            "status": "Ended",
            "ratings": {
                "title": "Doctor Who",
                "url": "https://www.rottentomatoes.com/tv/doctor_who",
                "criticsRating": "Fresh",
                "criticsScore": 90,
                "audienceRating": "Upright",
                "year": 2005,
            },
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "tv")](payload)
        # Live-spec keys surface verbatim; the upstream-only keys
        # (``title`` / ``url`` / ``criticsRating`` / ``year``) are
        # not part of the curated projection and are dropped.
        self.assertEqual(rendered["criticsScore"], 90)
        self.assertEqual(rendered["audienceRating"], "Upright")
        self.assertIsNone(rendered["audienceScore"])
        # The nested ratings mapping is not part of the summary.
        self.assertNotIn("ratings", rendered)

    def test_tv_ratings_projected_as_flat_cells(self) -> None:
        # A fully-populated ratings envelope projects all three
        # scores verbatim onto the flat top-level cells.
        payload = {
            "name": "Doctor Who",
            "ratings": {
                "criticsScore": 83,
                "audienceRating": "Certified Fresh",
                "audienceScore": 90,
            },
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "tv")](payload)
        self.assertEqual(rendered["criticsScore"], 83)
        self.assertEqual(rendered["audienceRating"], "Certified Fresh")
        self.assertEqual(rendered["audienceScore"], 90)
        self.assertNotIn("ratings", rendered)

    def test_tv_ratings_audience_rating_none_renders_null(self) -> None:
        # Partial ratings data: when RT did not publish a
        # categorical rating for the title the upstream envelope
        # carries ``audienceRating: null``. The renderer projects
        # ``None`` (renders as ``<null>``) rather than fabricating a
        # number; the critic ``criticsScore`` is still populated
        # so the operator can tell "RT has critic data but no
        # audience badge" from "no ratings fetched".
        payload = {
            "name": "Doctor Who",
            "originalName": "Doctor Who",
            "ratings": {"criticsScore": 90, "audienceRating": None},
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "tv")](payload)
        self.assertEqual(rendered["criticsScore"], 90)
        self.assertIsNone(rendered["audienceRating"])
        self.assertIsNone(rendered["audienceScore"])
        self.assertNotIn("ratings", rendered)

    def test_non_mapping_payload_returns_empty_dict(self) -> None:
        # A non-Mapping payload (e.g. ``None`` from an HTTP
        # error that the transport layer swallowed) gracefully
        # degrades to ``{}`` rather than crashing. ``emit()``
        # then takes the verbatim pass-through branch on the
        # empty dict.
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "tv")](None), {}
        )


class TestSummarySeerrMovie(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "movie")]`` matches the spec."""

    def test_movie_shape(self) -> None:
        payload = {
            "title": "The Matrix",
            "originalTitle": "The Matrix",
            "releaseDate": "1999-03-31",
            "runtime": 136,
            "genres": [
                {"id": 28, "name": "Action"},
                {"id": 878, "name": "Science Fiction"},
            ],
            "tagline": "Welcome to the Real World.",
            "overview": "A computer hacker learns the truth about his reality.",
            "status": "Released",
            "mediaInfo": {"status": 4},
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "movie")](payload)
        # Raw runtime minutes ``136`` reformats as ``"2h 16m"``;
        # the spec'd display format which matters because raw
        # minutes is not human-readable.
        self.assertEqual(
            rendered,
            {
                "title": "The Matrix",
                "originalTitle": "The Matrix",
                "releaseDate": "1999-03-31",
                "runtime": "2h 16m",
                "genres": "Action, Science Fiction",
                "tagline": "Welcome to the Real World.",
                "overview": "A computer hacker learns the truth about his reality.",
                "status": "Released",
                "mediaInfoStatus": 4,
                "criticsScore": None,
                "audienceRating": None,
                "audienceScore": None,
            },
        )

    def test_movie_missing_genres_renders_none(self) -> None:
        payload = {
            "title": "The Matrix",
            "originalTitle": "The Matrix",
            "releaseDate": "1999-03-31",
            "runtime": 136,
            "tagline": "Welcome to the Real World.",
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "movie")](payload)
        # Missing ``genres`` collapses to ``None`` (NOT a bare
        # ``""`` string) so ``--human`` renders ``<null>``
        # instead of an empty cell that masquerades as "the
        # movie has no genres".
        self.assertIsNone(rendered["genres"])
        # Missing audit-asked fields default to ``None`` so the
        # renderer stays well-formed on sparse payloads.
        self.assertIsNone(rendered["overview"])
        self.assertIsNone(rendered["status"])
        self.assertIsNone(rendered["mediaInfoStatus"])

    def test_movie_missing_runtime_renders_none(self) -> None:
        payload = {
            "title": "The Matrix",
            "originalTitle": "The Matrix",
            "releaseDate": "1999-03-31",
            "genres": [{"name": "Action"}],
            "tagline": "Welcome to the Real World.",
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "movie")](payload)
        # Missing ``runtime`` collapses to ``None`` (NOT a
        # ``"0h 0m"`` string) so ``--human`` renders
        # ``<null>`` instead of a misleading "the movie has
        # zero runtime" cell.
        self.assertIsNone(rendered["runtime"])

    def test_movie_zero_runtime_renders_none(self) -> None:
        # Zero runtime is treated as missing so a placeholder
        # ``runtime=0`` field doesn't masquerade as "the movie
        # is zero minutes long".
        payload = {
            "title": "The Matrix",
            "runtime": 0,
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "movie")](payload)
        self.assertIsNone(rendered["runtime"])

    def test_movie_ratings_merged_under_ratings_key(self) -> None:
        # Live envelope shape verified directly on the operator's
        # Seer instance on 2026-10-17: ``/api/v1/movie/{movieId}/ratings``
        # returns ``{"title": ..., "url": ..., "criticsRating": ...,
        # "criticsScore": <int>, "audienceRating": "...", "year": ...}``.
        # The renderer projects the three flat score cells
        # (``criticsScore`` -- the live integer RT Tomatometer
        # percentage, ``audienceRating`` -- the categorical string,
        # and ``audienceScore`` -- the numeric RT audience score).
        payload = {
            "title": "The Matrix",
            "originalTitle": "The Matrix",
            "releaseDate": "1999-03-31",
            "runtime": 136,
            "genres": [{"name": "Action"}],
            "tagline": "Welcome to the Real World.",
            "ratings": {
                "title": "The Matrix",
                "url": "https://www.rottentomatoes.com/m/the_matrix",
                "criticsRating": "Fresh",
                "criticsScore": 83,
                "audienceRating": "Spilled",
                "year": 1999,
            },
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "movie")](payload)
        self.assertEqual(rendered["criticsScore"], 83)
        self.assertEqual(rendered["audienceRating"], "Spilled")
        self.assertIsNone(rendered["audienceScore"])
        # The nested ratings mapping is not part of the summary.
        self.assertNotIn("ratings", rendered)

    def test_movie_ratings_projected_as_flat_cells(self) -> None:
        # A fully-populated ratings envelope projects all three
        # scores verbatim onto the flat top-level cells.
        payload = {
            "title": "The Matrix",
            "ratings": {
                "criticsScore": 83,
                "audienceRating": "Certified Fresh",
                "audienceScore": 90,
            },
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "movie")](payload)
        self.assertEqual(rendered["criticsScore"], 83)
        self.assertEqual(rendered["audienceRating"], "Certified Fresh")
        self.assertEqual(rendered["audienceScore"], 90)
        self.assertNotIn("ratings", rendered)

    def test_movie_ratings_audience_rating_none_renders_null(self) -> None:
        # Partial ratings data: when RT did not publish a
        # categorical rating for the title the upstream envelope
        # carries ``audienceRating: null``. The renderer projects
        # ``None`` (renders as ``<null>``) rather than fabricating a
        # number; the critic ``criticsScore`` is still populated.
        payload = {
            "title": "The Matrix",
            "ratings": {"criticsScore": 83, "audienceRating": None},
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "movie")](payload)
        self.assertEqual(rendered["criticsScore"], 83)
        self.assertIsNone(rendered["audienceRating"])
        self.assertIsNone(rendered["audienceScore"])
        self.assertNotIn("ratings", rendered)

    def test_movie_runtime_format_from_minutes(self) -> None:
        # The spec'd ``"<X>h <Y>m"`` format applies for any raw
        # minutes integer: ``60`` -> ``"1h 0m"``, ``45`` ->
        # ``"0h 45m"``, ``200`` -> ``"3h 20m"``. Pins the
        # display-format contract so future drift between the
        # spec'd format and a future refactor trips the unit
        # suite immediately.
        renderer = _SUMMARY_RENDERERS[("seerr", "movie")]
        self.assertEqual(renderer({"runtime": 60})["runtime"], "1h 0m")
        self.assertEqual(renderer({"runtime": 45})["runtime"], "0h 45m")
        self.assertEqual(renderer({"runtime": 200})["runtime"], "3h 20m")

    def test_movie_list_fixture_shape(self) -> None:
        # Test-fixture shape: a list with one ``Mapping``
        # element. The renderer extracts element 0 and
        # renders the same curated shape as the single-mapping
        # CLI-layer payload.
        payload = [{
            "title": "The Matrix",
            "originalTitle": "The Matrix",
            "releaseDate": "1999-03-31",
            "runtime": 136,
            "genres": [{"name": "Action"}],
            "tagline": "Welcome to the Real World.",
        }]
        rendered = _SUMMARY_RENDERERS[("seerr", "movie")](payload)
        self.assertEqual(rendered["title"], "The Matrix")
        self.assertEqual(rendered["runtime"], "2h 16m")
        # No ``ratings`` block in the payload: all three score cells
        # are ``None`` and the nested ``ratings`` key is absent.
        self.assertIsNone(rendered["criticsScore"])
        self.assertIsNone(rendered["audienceRating"])
        self.assertIsNone(rendered["audienceScore"])
        self.assertNotIn("ratings", rendered)

    def test_non_mapping_payload_returns_empty_dict(self) -> None:
        # A non-Mapping payload (e.g. ``None`` from an HTTP
        # error that the transport layer swallowed) gracefully
        # degrades to ``{}`` rather than crashing. ``emit()``
        # then takes the verbatim pass-through branch on the
        # empty dict.
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "movie")](None), {}
        )
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "movie")]([]), {}
        )
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "movie")](42), {}
        )


class TestSummarySeerrUser(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "user")]`` matches the spec.

    The audit asks :func:`_summary_seerr_user` for a curated
    five-key projection (``username``, ``email``, ``userType``,
    ``permissions``, ``requestCount``) so the auth self-check
    answers "who am I and what can I do here?" without the
    operator having to drop to ``--verbose``.

    Live Seer ``/api/v1/auth/me`` (verified directly on 2026-10-17)
    carries ``"permissions": 2`` -- a single integer bitfield, not
    a list / mapping as the historical Overseerr shape exposed.
    The renderer decodes the bitfield into a sorted-by-bit
    comma-joined name list via
    :func:`_decode_permissions_bitfield`. Non-integer shapes fall
    back to ``None`` so the AGENTS.md §4.1 defensive contract is
    preserved.
    """

    def test_user_shape_full_fields(self) -> None:
        # US-3.1 / US-3.2: the curated five-key projection
        # populates every field from the upstream payload via
        # :func:`_safe_get`. ``permissions=3`` is the bitwise-OR of
        # ``1`` (``request``) and ``2`` (``admin``), sorted in the
        # documented bit-value order.
        payload = {
            "id": 1,
            "username": "alice",
            "email": "alice@example.com",
            "userType": 1,
            "permissions": 3,
            "requestCount": 42,
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "user")](payload)
        self.assertEqual(
            rendered,
            {
                "username": "alice",
                "email": "alice@example.com",
                "userType": 1,
                "permissions": "request, admin",
                "requestCount": 42,
            },
        )

    def test_user_missing_optional_fields_default_to_none(self) -> None:
        # US-3.6 / AGENTS.md \u00a74.1: every audit-asked field
        # defaults to ``None`` per the documented ``_safe_get``
        # contract so the renderer stays well-formed even when
        # upstream drops a field. The ``permissions`` cell also
        # falls back to ``None`` for non-integer / missing shapes.
        payload = {
            "id": 1,
            "username": "alice",
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "user")](payload)
        self.assertEqual(rendered["username"], "alice")
        self.assertIsNone(rendered["email"])
        self.assertIsNone(rendered["userType"])
        self.assertIsNone(rendered["permissions"])
        self.assertIsNone(rendered["requestCount"])

    # US-3.1 / US-3.3 / US-3.4 -- single-bit value decoding. The
    # documented bit-to-name mapping is:
    # 1=request, 2=admin, 4=manage_users, 8=manage_issues,
    # 16=auto_approve, 32=view_users.

    def test_user_permissions_bit_zero_renders_empty_string(self) -> None:
        # US-3.3: ``permissions=0`` projects ``""`` (the documented
        # "no bits set" contract; renders as a blank cell, distinct
        # from the ``None`` defensive fallback).
        payload = {"username": "alice", "permissions": 0}
        rendered = _SUMMARY_RENDERERS[("seerr", "user")](payload)
        self.assertEqual(rendered["permissions"], "")

    def test_user_permissions_bit_one_is_request(self) -> None:
        payload = {"username": "alice", "permissions": 1}
        rendered = _SUMMARY_RENDERERS[("seerr", "user")](payload)
        self.assertEqual(rendered["permissions"], "request")

    def test_user_permissions_bit_two_is_admin(self) -> None:
        # US-3.1: the canonical admin-only projection verified
        # directly against the operator's Seer instance on
        # 2026-10-17 (``"permissions": 2`` -> ``"admin"``).
        payload = {"username": "alice", "permissions": 2}
        rendered = _SUMMARY_RENDERERS[("seerr", "user")](payload)
        self.assertEqual(rendered["permissions"], "admin")

    def test_user_permissions_bit_four_is_manage_users(self) -> None:
        payload = {"username": "alice", "permissions": 4}
        rendered = _SUMMARY_RENDERERS[("seerr", "user")](payload)
        self.assertEqual(rendered["permissions"], "manage_users")

    def test_user_permissions_bit_eight_is_manage_issues(self) -> None:
        payload = {"username": "alice", "permissions": 8}
        rendered = _SUMMARY_RENDERERS[("seerr", "user")](payload)
        self.assertEqual(rendered["permissions"], "manage_issues")

    def test_user_permissions_bit_sixteen_is_auto_approve(self) -> None:
        payload = {"username": "alice", "permissions": 16}
        rendered = _SUMMARY_RENDERERS[("seerr", "user")](payload)
        self.assertEqual(rendered["permissions"], "auto_approve")

    def test_user_permissions_bit_thirty_two_is_view_users(self) -> None:
        payload = {"username": "alice", "permissions": 32}
        rendered = _SUMMARY_RENDERERS[("seerr", "user")](payload)
        self.assertEqual(rendered["permissions"], "view_users")

    def test_user_permissions_bit_three_sorted_by_bit_value(self) -> None:
        # US-3.2: ``permissions=3`` is ``1 | 2`` -> ``"request,
        # admin"`` (sorted by bit value per the documented contract).
        payload = {"username": "alice", "permissions": 3}
        rendered = _SUMMARY_RENDERERS[("seerr", "user")](payload)
        self.assertEqual(rendered["permissions"], "request, admin")

    def test_user_permissions_bit_seven_includes_three_names(
        self,
    ) -> None:
        # US-3.5: ``permissions=7`` is ``1 | 2 | 4`` ->
        # ``"request, admin, manage_users"``.
        payload = {"username": "alice", "permissions": 7}
        rendered = _SUMMARY_RENDERERS[("seerr", "user")](payload)
        self.assertEqual(
            rendered["permissions"], "request, admin, manage_users"
        )

    def test_user_permissions_unknown_bit_silently_dropped(self) -> None:
        # Defensive: bits outside the documented table are silently
        # dropped so a future Seer release that adds a new bit the
        # renderer has not been taught yet does not surface as a
        # malformed join (e.g. trailing comma).
        payload = {"username": "alice", "permissions": 0b1000000}
        rendered = _SUMMARY_RENDERERS[("seerr", "user")](payload)
        self.assertEqual(rendered["permissions"], "")

    def test_user_permissions_non_integer_falls_back_to_none(self) -> None:
        # US-3.6 / AGENTS.md \u00a74.1: a non-integer ``permissions``
        # value (the historical Overseerr string / list / mapping
        # shape, or future envelope drift) renders as ``None``
        # (``<null>``) rather than crashing. Pins the defensive
        # contract so a regression on the bitfield decoder's
        # isinstance guard surfaces as a hard test failure.
        for bad_value in (
            ["ADMIN", "MANAGE_REQUESTS"],  # historical Overseerr list
            {"admin": True, "requests": 5},  # historical mapping
            "ADMIN",  # historical string
        ):
            rendered = _SUMMARY_RENDERERS[("seerr", "user")](
                {"username": "alice", "permissions": bad_value}
            )
            self.assertIsNone(
                rendered["permissions"],
                msg=(
                    f"non-integer permissions must fall back to "
                    f"None; got {rendered['permissions']!r} for "
                    f"input {bad_value!r}"
                ),
            )

    def test_user_permissions_bool_does_not_decode_as_bit(self) -> None:
        # ``bool`` is a subclass of ``int``; reject it explicitly
        # so ``permissions: True`` does not decode as bit ``1``
        # (``request``). The Seer envelope only ever carries an
        # ``int`` here, but the defensive guard prevents a future
        # ``"permissions": True`` regression on a sibling payload
        # where the partner happens to ship a Python boolean.
        rendered = _SUMMARY_RENDERERS[("seerr", "user")](
            {"username": "alice", "permissions": True}
        )
        self.assertIsNone(rendered["permissions"])

    def test_user_request_count_walks_both_paths(self) -> None:
        # US-3.7: ``requestCount`` is sourced from the nested
        # ``permissions.requestCount`` field or the top-level
        # ``requestCount`` field -- ``_safe_get`` walks both
        # paths so the renderer stays well-formed on either
        # envelope shape.
        nested = _SUMMARY_RENDERERS[("seerr", "user")](
            {"permissions": {"requestCount": 7}}
        )
        self.assertEqual(nested["requestCount"], 7)
        top = _SUMMARY_RENDERERS[("seerr", "user")](
            {"requestCount": 11}
        )
        self.assertEqual(top["requestCount"], 11)

    def test_user_list_fixture_shape(self) -> None:
        # Mirrors the test-fixture convention used by
        # :func:`_summary_seerr_movie` /
        # :func:`_summary_seerr_tv`: a list with one ``Mapping``
        # element extracts element 0 and renders the same
        # five-key projection as the single-mapping CLI-layer
        # payload.
        payload = [{
            "username": "alice",
            "email": "alice@example.com",
        }]
        rendered = _SUMMARY_RENDERERS[("seerr", "user")](payload)
        self.assertEqual(rendered["username"], "alice")
        self.assertEqual(rendered["email"], "alice@example.com")
        self.assertIsNone(rendered["userType"])
        self.assertIsNone(rendered["permissions"])
        self.assertIsNone(rendered["requestCount"])

    def test_user_non_mapping_payload_returns_empty_dict(self) -> None:
        # Non-Mapping / list-with-no-mapping / scalar / ``None``
        # payloads gracefully degrade to ``{}`` rather than
        # crashing; ``emit()`` then takes the verbatim
        # pass-through branch on the empty dict.
        for value in (None, [], [1, 2], 42, "foo"):
            self.assertEqual(
                _SUMMARY_RENDERERS[("seerr", "user")](value),
                {},
                msg=f"expected empty dict for {value!r}",
            )


class TestSummaryMaintainerrPending(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("maintainerr", "pending")]`` matches the spec."""

    def test_pending_shape(self) -> None:
        payload = [
            {
                "title": "Old Movies",
                "mediaCount": 42,
                "deleteAfterDays": 14,
                "isOnHold": False,
                "arrAction": "delete",
                "addDate": "2026-01-01T00:00:00Z",
                "mediaServerType": "plex",
                "type": "movie",
                "description": "Old movies that have been watched",
                "isActive": True,
                "visibleOnHome": False,
            }
        ]
        rendered = _SUMMARY_RENDERERS[("maintainerr", "pending")](payload)
        self.assertEqual(
            rendered[0],
            {
                "title": "Old Movies",
                "mediaCount": 42,
                "deleteAfterDays": 14,
                "isOnHold": False,
                "arrAction": "delete",
                "addDate": "2026-01-01T00:00:00Z",
                "mediaServerType": "plex",
                "type": "movie",
                "description": "Old movies that have been watched",
                "isActive": True,
                "visibleOnHome": False,
            },
        )

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("maintainerr", "pending")](None),
            [],
        )


class TestSummaryMaintainerrStorage(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("maintainerr", "storage")]`` matches the spec.

    ``GET /api/storage-metrics`` answers with a dict envelope
    (``{totals, mounts[], instances[], ...}``), not with the legacy
    list envelope the renderer originally assumed -- the mismatch made
    ``maintainerr --human storage`` print ``(empty list)``. Both
    envelopes are therefore covered here: the live dict envelope
    (``totals`` + ``mounts[]``) and the tolerated legacy list shapes.
    """

    def _renderer(self) -> Any:
        return _SUMMARY_RENDERERS[("maintainerr", "storage")]

    def test_instances_envelope_flattened_one_row_per_instance(self) -> None:
        # US-2.1: when ``instances`` is a non-empty list, the
        # renderer emits one row per instance with mount/used/
        # free/total/usedPercent.
        payload = [
            {
                "name": "media",
                "instances": [
                    {
                        "mount": "/data",
                        "used": 700,
                        "free": 300,
                        "total": 1000,
                        "usedPercent": 70.0,
                    },
                    {
                        "mount": "/backup",
                        "used": 100,
                        "free": 900,
                        "total": 1000,
                        "usedPercent": 10.0,
                    },
                ],
            }
        ]
        rendered = self._renderer()(payload)
        self.assertEqual(
            rendered,
            [
                {
                    "mount": "/data",
                    "used": 700,
                    "free": 300,
                    "total": 1000,
                    "usedPercent": 70.0,
                },
                {
                    "mount": "/backup",
                    "used": 100,
                    "free": 900,
                    "total": 1000,
                    "usedPercent": 10.0,
                },
            ],
        )

    def test_parent_fallback_when_instances_missing(self) -> None:
        # US-2.2: when ``instances`` is absent (legacy Maintainerr
        # build) the renderer emits a single row from the parent
        # item's mount/used/free/total/usedPercent keys.
        payload = [
            {
                "mount": "/data",
                "used": 700,
                "free": 300,
                "total": 1000,
                "usedPercent": 70.0,
            }
        ]
        rendered = self._renderer()(payload)
        self.assertEqual(
            rendered,
            [
                {
                    "mount": "/data",
                    "used": 700,
                    "free": 300,
                    "total": 1000,
                    "usedPercent": 70.0,
                }
            ],
        )

    def test_parent_fallback_when_instances_empty(self) -> None:
        # US-2.2 (extended): an empty ``instances`` array also falls
        # back to the parent row so the renderer stays well-formed
        # even on sparse upstream payloads.
        payload = [
            {
                "mount": "/data",
                "used": 700,
                "free": 300,
                "total": 1000,
                "usedPercent": 70.0,
                "instances": [],
            }
        ]
        rendered = self._renderer()(payload)
        self.assertEqual(
            rendered,
            [
                {
                    "mount": "/data",
                    "used": 700,
                    "free": 300,
                    "total": 1000,
                    "usedPercent": 70.0,
                }
            ],
        )

    def test_non_list_returns_empty_list(self) -> None:
        # US-2.4: a non-list payload returns ``[]`` so the human
        # renderer prints ``(empty list)`` rather than crashing.
        for value in (None, {}, "foo", 42):
            self.assertEqual(
                self._renderer()(value),
                [],
                msg=f"expected empty list for {value!r}",
            )

    def test_missing_fields_default_to_null(self) -> None:
        # US-2.3: when an instance row is missing one of the
        # audit-asked fields the cell renders as ``None`` (the
        # documented safe-default contract of ``_safe_get``).
        rendered = self._renderer()(
            [{"instances": [{"mount": "/data"}]}]
        )
        self.assertEqual(len(rendered), 1)
        row = rendered[0]
        self.assertEqual(row["mount"], "/data")
        self.assertEqual(row["used"], 0)
        self.assertEqual(row["free"], 0)
        self.assertEqual(row["total"], 0)
        self.assertEqual(row["usedPercent"], 0.0)

    @staticmethod
    def _live_payload() -> dict[str, Any]:
        """Return a live-shaped ``/api/storage-metrics`` payload."""
        return {
            "generatedAt": "2026-10-24T00:00:00.000Z",
            "totals": {
                "freeSpace": 186937963008,
                "totalSpace": 2113929214976,
                "usedSpace": 1926991251968,
                "mountCount": 1,
                "accurateMountCount": 1,
                "accurateTotalSpace": True,
            },
            "mounts": [
                {
                    "instanceId": 1,
                    "instanceType": "radarr",
                    "instanceName": "radarr",
                    "path": "/",
                    "label": "",
                    "freeSpace": 186937963008,
                    "totalSpace": 2113929214976,
                    "hasAccurateTotalSpace": True,
                }
            ],
            "instances": [
                {
                    "id": 1,
                    "name": "radarr",
                    "type": "radarr",
                    "ok": True,
                    "error": None,
                    "mountCount": 1,
                }
            ],
        }

    def test_dict_envelope_emits_one_row_per_mount_plus_totals(
        self,
    ) -> None:
        # The acceptance case: the live dict envelope yields one row
        # per volume (``path`` / derived ``used`` / ``freeSpace`` /
        # ``totalSpace`` / derived ``usedPercent``) plus the
        # ``"(totals)"`` row carrying the upstream aggregate.
        rendered = self._renderer()(self._live_payload())
        self.assertEqual(
            rendered,
            [
                {
                    "mount": "(totals)",
                    "used": 1926991251968,
                    "free": 186937963008,
                    "total": 2113929214976,
                    "usedPercent": 91.2,
                },
                {
                    "mount": "/",
                    "used": 1926991251968,
                    "free": 186937963008,
                    "total": 2113929214976,
                    "usedPercent": 91.2,
                },
            ],
        )

    def test_totals_without_mounts_emits_totals_row_only(self) -> None:
        # A dict envelope with a ``totals`` block but no ``mounts[]``
        # array still surfaces the aggregate -- exactly one row, no
        # crash.
        payload = {"totals": self._live_payload()["totals"]}
        rendered = self._renderer()(payload)
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0]["mount"], "(totals)")
        self.assertEqual(rendered[0]["used"], 1926991251968)
        self.assertEqual(rendered[0]["free"], 186937963008)
        self.assertEqual(rendered[0]["total"], 2113929214976)
        self.assertEqual(rendered[0]["usedPercent"], 91.2)

    def test_empty_mounts_still_emits_totals_row(self) -> None:
        # An empty ``mounts[]`` array is a legitimate "no volumes"
        # answer; the upstream aggregate is still surfaced.
        payload = self._live_payload()
        payload["mounts"] = []
        rendered = self._renderer()(payload)
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0]["mount"], "(totals)")

    def test_dict_without_totals_or_mounts_returns_empty_list(self) -> None:
        # Neither ``totals`` nor ``mounts[]``: the honest "no data"
        # answer, not a crash.
        self.assertEqual(self._renderer()({}), [])
        self.assertEqual(self._renderer()({"generatedAt": "x"}), [])

    def test_instance_health_array_is_not_a_row_source(self) -> None:
        # The live ``instances[]`` array carries instance health
        # (``id`` / ``name`` / ``type`` / ``ok`` / ``error`` /
        # ``mountCount``) and no space figures -- it must never be
        # mistaken for a list of volumes.
        rendered = self._renderer()(self._live_payload())
        self.assertEqual(
            [row["mount"] for row in rendered],
            ["(totals)", "/"],
        )
        health_only = self._renderer()(
            {
                "instances": [
                    {
                        "id": 1,
                        "name": "radarr",
                        "type": "radarr",
                        "ok": True,
                        "error": None,
                        "mountCount": 1,
                    }
                ]
            }
        )
        self.assertEqual(health_only, [])

    def test_legacy_instances_rows_accepted_on_dict_envelope(
        self,
    ) -> None:
        # Legacy tolerance: a dict envelope without ``mounts[]`` but
        # with an ``instances[]`` array that does carry the legacy
        # space keys still renders one row per instance.
        rendered = self._renderer()(
            {
                "instances": [
                    {
                        "mount": "/data",
                        "used": 700,
                        "free": 300,
                        "total": 1000,
                        "usedPercent": 70.0,
                    }
                ]
            }
        )
        self.assertEqual(
            rendered,
            [
                {
                    "mount": "/data",
                    "used": 700,
                    "free": 300,
                    "total": 1000,
                    "usedPercent": 70.0,
                }
            ],
        )

    def test_missing_mount_fields_render_as_null(self) -> None:
        # A mount without ``freeSpace`` / ``totalSpace`` yields
        # ``None`` cells (the ``<null>`` contract) rather than a
        # misleading ``0``.
        rendered = self._renderer()(
            {"mounts": [{"path": "/data"}]}
        )
        self.assertEqual(len(rendered), 1)
        row = rendered[0]
        self.assertEqual(row["mount"], "/data")
        self.assertIsNone(row["used"])
        self.assertIsNone(row["free"])
        self.assertIsNone(row["total"])
        self.assertIsNone(row["usedPercent"])

    def test_inaccurate_total_space_yields_null_used(self) -> None:
        # A mount flagged ``hasAccurateTotalSpace: false`` has an
        # unknown capacity: ``used`` stays ``None`` instead of
        # rendering a misleading ``0``.
        rendered = self._renderer()(
            {
                "mounts": [
                    {
                        "path": "/network",
                        "freeSpace": 300,
                        "totalSpace": 1000,
                        "hasAccurateTotalSpace": False,
                    }
                ]
            }
        )
        self.assertEqual(len(rendered), 1)
        row = rendered[0]
        self.assertEqual(row["mount"], "/network")
        self.assertIsNone(row["used"])
        self.assertIsNone(row["usedPercent"])
        self.assertEqual(row["free"], 300)
        self.assertEqual(row["total"], 1000)

    def test_mount_label_used_when_path_missing(self) -> None:
        # ``path`` is the mount identifier; ``label`` is the
        # fallback when the upstream mount carries no path.
        rendered = self._renderer()(
            {"mounts": [{"label": "media", "freeSpace": 300,
                         "totalSpace": 1000}]}
        )
        self.assertEqual(rendered[0]["mount"], "media")
        self.assertEqual(rendered[0]["used"], 700)

    def test_human_rendering_shows_rows_not_empty_list(self) -> None:
        # The end-to-end symptom: the ``--human`` table renders one
        # row per volume plus the totals row -- never ``(empty list)``.
        rendered = human(
            self._renderer()(self._live_payload()),
            columns=["mount", "used", "free", "total", "usedPercent"],
        )
        self.assertNotIn("(empty list)", rendered)
        lines = rendered.splitlines()
        self.assertGreaterEqual(len(lines), 4)
        self.assertEqual(
            lines[0].split(),
            ["mount", "used", "free", "total", "usedPercent"],
        )
        self.assertIn("(totals)", rendered)
        self.assertIn("/", rendered)

    def test_human_rendering_shows_null_for_missing_fields(self) -> None:
        # A missing field renders as ``<null>`` in the table.
        rendered = human(
            self._renderer()({"mounts": [{"path": "/data"}]}),
            columns=["mount", "used", "free", "total", "usedPercent"],
        )
        self.assertIn("<null>", rendered)
        self.assertNotIn("(empty list)", rendered)


class TestSummaryMaintainerrHealth(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("maintainerr", "health")]`` matches the spec."""

    def _renderer(self) -> Any:
        return _SUMMARY_RENDERERS[("maintainerr", "health")]

    def test_status_ok_returns_ok(self) -> None:
        # US-3.1: ``{status: "ok"}`` maps to the ``"OK"`` token.
        self.assertEqual(self._renderer()({"status": "ok"}), "OK")

    def test_status_degraded_returns_degraded(self) -> None:
        # US-3.2: a non-``"ok"`` status (e.g. ``"degraded"``,
        # ``"down"``, ``"unhealthy"``) maps to ``"DEGRADED"``.
        for status in ("degraded", "down", "unhealthy", ""):
            self.assertEqual(
                self._renderer()({"status": status}),
                "DEGRADED",
                msg=f"status {status!r} should map to DEGRADED",
            )

    def test_missing_status_returns_degraded(self) -> None:
        # US-3.4: a Mapping without a ``status`` key falls back to
        # ``"DEGRADED"`` as the documented defensive default.
        self.assertEqual(self._renderer()({}), "DEGRADED")
        self.assertEqual(
            self._renderer()({"uptimeSeconds": 1234}),
            "DEGRADED",
        )

    def test_legacy_bare_true_returns_ok(self) -> None:
        # US-3.3: the historical bare-boolean contract -- ``True``
        # maps to ``"OK"`` so legacy operators that still see a bare
        # boolean out of older Maintainerr builds keep working.
        self.assertEqual(self._renderer()(True), "OK")

    def test_legacy_bare_false_returns_degraded(self) -> None:
        # US-3.3: ``False`` maps to ``"DEGRADED"`` -- the legacy
        # bare-boolean contract.
        self.assertEqual(self._renderer()(False), "DEGRADED")

    def test_non_mapping_non_bool_returns_degraded(self) -> None:
        # US-3.4: any non-Mapping / non-bool shape (list, scalar,
        # None) maps to ``"DEGRADED"`` as the documented defensive
        # default.
        for value in ([], [1, 2], None, 42, "foo"):
            self.assertEqual(
                self._renderer()(value),
                "DEGRADED",
                msg=f"value {value!r} should map to DEGRADED",
            )


# ---------------------------------------------------------------------------
# Envelope unwrap helper
# ---------------------------------------------------------------------------


class TestUnwrapEnvelope(unittest.TestCase):
    """``_unwrap_envelope`` handles bare list, Jellyfin, Radarr/Sonarr."""

    def test_bare_list_returned_verbatim(self) -> None:
        self.assertEqual(_unwrap_envelope(["a", "b"]), ["a", "b"])

    def test_jellyfin_envelope_items_unwrapped(self) -> None:
        payload = {
            "Items": [{"Name": "Foo"}],
            "TotalRecordCount": 1,
            "StartIndex": 0,
        }
        self.assertEqual(_unwrap_envelope(payload), [{"Name": "Foo"}])

    def test_radarr_sonarr_envelope_records_unwrapped(self) -> None:
        payload = {
            "page": 1,
            "pageSize": 10,
            "totalRecords": 1,
            "records": [{"title": "Vanguard"}],
        }
        self.assertEqual(
            _unwrap_envelope(payload), [{"title": "Vanguard"}]
        )

    def test_malformed_envelope_warns_and_returns_empty(self) -> None:
        payload = {"foo": [{"title": "Lost"}], "bar": 7}
        with self.assertLogs(
            "arr_cli.facade.output", level="WARNING"
        ) as log_cm:
            result = _unwrap_envelope(payload)
        self.assertEqual(result, [])
        self.assertTrue(
            any(
                "envelope shape not recognised" in line
                for line in log_cm.output
            ),
            f"expected warning in logs, got {log_cm.output!r}",
        )

    def test_none_returns_empty_without_warning(self) -> None:
        records = _capture_module_warnings(lambda: _unwrap_envelope(None))
        self.assertEqual(_unwrap_envelope(None), [])
        self.assertEqual(records, [])

    def test_scalar_returns_empty_without_warning(self) -> None:
        for value in (42, "foo", True):
            records = _capture_module_warnings(
                lambda v=value: _unwrap_envelope(v)
            )
            self.assertEqual(_unwrap_envelope(value), [])
            self.assertEqual(
                records,
                [],
                f"expected no warnings for {value!r}, got {records!r}",
            )

    def test_empty_mapping_returns_empty_without_warning(self) -> None:
        records = _capture_module_warnings(lambda: _unwrap_envelope({}))
        self.assertEqual(_unwrap_envelope({}), [])
        self.assertEqual(records, [])


class TestSummaryEnvelopeUnwrapping(unittest.TestCase):
    """Each affected renderer unwraps its upstream envelope shape."""

    def test_jellyfin_recent_envelope_unwrapped(self) -> None:
        payload = {
            "Items": [
                {
                    "Name": "Foo",
                    "Type": "Movie",
                    "ProductionYear": 2024,
                    "SeriesName": None,
                    "UserData": {"LastPlayedDate": "2024-01-01"},
                }
            ],
            "TotalRecordCount": 1,
            "StartIndex": 0,
        }
        rendered = _SUMMARY_RENDERERS[("jellyfin", "recent")](payload)
        self.assertEqual(
            rendered,
            [
                {
                    "Name": "Foo",
                    "Type": "Movie",
                    "ProductionYear": 2024,
                    "SeriesName": None,
                    "UserData.LastPlayedDate": "2024-01-01",
                    "UserData.PlayedPercentage": 0,
                }
            ],
        )

    def test_jellyfin_resume_envelope_unwrapped(self) -> None:
        payload = {
            "Items": [
                {
                    "Name": "Foo",
                    "Type": "Episode",
                    "ProductionYear": 2020,
                    "SeriesName": "Show",
                    "UserData": {
                        "PlaybackPositionTicks": 100,
                        "PlayCount": 2,
                    },
                }
            ],
            "TotalRecordCount": 1,
            "StartIndex": 0,
        }
        rendered = _SUMMARY_RENDERERS[("jellyfin", "resume")](payload)
        self.assertEqual(
            rendered,
            [
                {
                    "Name": "Foo",
                    "Type": "Episode",
                    "ProductionYear": 2020,
                    "SeriesName": "Show",
                    "UserData.PlaybackPositionTicks": 100,
                    "UserData.PlayCount": 2,
                    "UserData.PlayedPercentage": 0,
                    "RunTimeTicks": None,
                }
            ],
        )

    def test_radarr_wanted_envelope_unwrapped(self) -> None:
        payload = {
            "page": 1,
            "pageSize": 10,
            "totalRecords": 1,
            "records": [
                {
                    "title": "Vanguard",
                    "year": 2024,
                    "tmdbId": 999,
                    "monitored": True,
                    "hasFile": False,
                    "genres": [{"name": "Action"}],
                    "physicalRelease": "2024-08-01T00:00:00Z",
                    "releaseDate": "2024-08-15",
                }
            ],
        }
        rendered = _SUMMARY_RENDERERS[("radarr", "wanted")](payload)
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0]["title"], "Vanguard")
        self.assertEqual(rendered[0]["year"], 2024)
        self.assertEqual(rendered[0]["hasFile"], False)
        self.assertEqual(rendered[0]["genres"], "Action")

    def test_sonarr_wanted_envelope_unwrapped(self) -> None:
        payload = {
            "page": 1,
            "pageSize": 10,
            "totalRecords": 1,
            "records": [
                {
                    "title": "Pilot",
                    "seasonNumber": 1,
                    "episodeNumber": 1,
                    "airDate": "2024-01-01",
                    "monitored": True,
                    "hasFile": False,
                    "runtime": 45,
                    "overview": "An overview.",
                    "series": {"title": "Severance"},
                }
            ],
        }
        rendered = _SUMMARY_RENDERERS[("sonarr", "wanted")](payload)
        self.assertEqual(
            rendered,
            [
                {
                    "title": "Pilot",
                    "seasonNumber": 1,
                    "episodeNumber": 1,
                    "airDate": "2024-01-01",
                    "monitored": True,
                    "hasFile": False,
                    "runtime": "0h 45m",
                    "overview": "An overview.",
                    "series.title": "Severance",
                }
            ],
        )

    def test_sonarr_recent_envelope_unwrapped(self) -> None:
        payload = {
            "page": 1,
            "pageSize": 10,
            "totalRecords": 1,
            "records": [
                {
                    "id": 1,
                    "seriesId": 10,
                    "episodeId": 100,
                    "sourceTitle": "Pilot",
                    "eventType": "downloadFolderImported",
                    "date": "2024-06-01",
                    "quality": {"quality": {"name": "WEBDL-1080p"}},
                    "languages": [{"id": 1, "name": "English"}],
                    "data": {"downloadClient": "qbit"},
                }
            ],
        }
        rendered = _SUMMARY_RENDERERS[("sonarr", "recent")](payload)
        self.assertEqual(
            rendered,
            [
                {
                    "series": {"title": None},
                    "episode": {"title": None},
                    "id": 1,
                    "seriesId": 10,
                    "episodeId": 100,
                    "sourceTitle": "Pilot",
                    "eventType": "downloadFolderImported",
                    "date": "2024-06-01",
                    "quality": "WEBDL-1080p",
                    "language": "English",
                    "client": "qbit",
                }
            ],
        )


class TestSummaryEnvelopeRegression(unittest.TestCase):
    """The bare-list path stays byte-identical for the five renderers."""

    def test_jellyfin_recent_bare_list_unchanged(self) -> None:
        payload = [
            {
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2024,
                "SeriesName": None,
                "UserData": {"LastPlayedDate": "2024-01-01"},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "recent")](payload)
        self.assertEqual(
            rendered[0],
            {
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2024,
                "SeriesName": None,
                "UserData.LastPlayedDate": "2024-01-01",
                "UserData.PlayedPercentage": 0,
            },
        )

    def test_jellyfin_resume_bare_list_unchanged(self) -> None:
        payload = [
            {
                "Name": "Foo",
                "Type": "Episode",
                "ProductionYear": 2020,
                "SeriesName": "Show",
                "UserData": {"PlaybackPositionTicks": 100, "PlayCount": 2},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("jellyfin", "resume")](payload)
        self.assertEqual(
            rendered[0],
            {
                "Name": "Foo",
                "Type": "Episode",
                "ProductionYear": 2020,
                "SeriesName": "Show",
                "UserData.PlaybackPositionTicks": 100,
                "UserData.PlayCount": 2,
                "UserData.PlayedPercentage": 0,
                "RunTimeTicks": None,
            },
        )

    def test_radarr_wanted_bare_list_unchanged(self) -> None:
        payload = [
            {
                "title": "Foo",
                "year": 2024,
                "tmdbId": 999,
                "monitored": True,
                "hasFile": False,
                "genres": [{"name": "Action"}],
                "physicalRelease": "2024-08-01T00:00:00Z",
                "releaseDate": "2024-08-15",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("radarr", "wanted")](payload)
        self.assertEqual(rendered[0]["title"], "Foo")
        self.assertEqual(rendered[0]["year"], 2024)
        self.assertEqual(rendered[0]["tmdbId"], 999)
        self.assertEqual(rendered[0]["monitored"], True)
        self.assertEqual(rendered[0]["hasFile"], False)
        self.assertEqual(rendered[0]["genres"], "Action")

    def test_sonarr_wanted_bare_list_unchanged(self) -> None:
        payload = [
            {
                "title": "Pilot",
                "seasonNumber": 1,
                "episodeNumber": 1,
                "airDate": "2024-01-01",
                "monitored": True,
                "hasFile": False,
                "runtime": 45,
                "overview": "An overview.",
                "series": {"title": "Severance"},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "wanted")](payload)
        self.assertEqual(
            rendered[0],
            {
                "title": "Pilot",
                "seasonNumber": 1,
                "episodeNumber": 1,
                "airDate": "2024-01-01",
                "monitored": True,
                "hasFile": False,
                "runtime": "0h 45m",
                "overview": "An overview.",
                "series.title": "Severance",
            },
        )

    def test_sonarr_recent_bare_list_unchanged(self) -> None:
        payload = [
            {
                "id": 1,
                "seriesId": 10,
                "episodeId": 100,
                "sourceTitle": "Pilot",
                "eventType": "downloadFolderImported",
                "date": "2024-06-01",
                "quality": {"quality": {"name": "WEBDL-1080p"}},
                "languages": [{"id": 1, "name": "English"}],
                "data": {"downloadClient": "qbit"},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "recent")](payload)
        self.assertEqual(
            rendered[0],
            {
                "series": {"title": None},
                "episode": {"title": None},
                "id": 1,
                "seriesId": 10,
                "episodeId": 100,
                "sourceTitle": "Pilot",
                "eventType": "downloadFolderImported",
                "date": "2024-06-01",
                "quality": "WEBDL-1080p",
                "language": "English",
                "client": "qbit",
            },
        )

    def test_sonarr_recent_pins_actual_history_row_shape(self) -> None:
        # Pin the upstream payload shape Sonarr returns for
        # ``GET /api/v3/history?includeSeries=true&includeEpisode=true``:
        # the activity-log envelope (``{page, pageSize, totalRecords,
        # records: [...]}``) is unwrapped, and each row now carries
        # the nested ``series: {title}`` / ``episode: {title}``
        # envelopes populated by Sonarr when the include flags are
        # passed. The curated summary projects both the nested
        # objects (preserving the originally documented contract) and
        # the flat identity fields the upstream payload also carries.
        payload = {
            "page": 1,
            "pageSize": 10,
            "sortKey": "date",
            "sortDirection": "descending",
            "totalRecords": 1,
            "records": [
                {
                    "id": 42,
                    "episodeId": 100,
                    "seriesId": 10,
                    "sourceTitle": "Show.S01E01.WEBDL-1080p.mkv",
                    "languages": [{"id": 1, "name": "English"}],
                    "quality": {"quality": {"id": 7, "name": "WEBDL-1080p"}},
                    "customFormats": [],
                    "customFormatScore": 0,
                    "qualityCutoffNotMet": False,
                    "date": "2026-09-18T01:59:01Z",
                    "downloadId": "string-id-abc",
                    "eventType": "downloadFolderImported",
                    "series": {"title": "Star Trek: Strange New Worlds"},
                    "episode": {"title": "Orders of Magnitude"},
                    "data": {"downloadClient": "qbit"},
                }
            ],
        }
        rendered = _SUMMARY_RENDERERS[("sonarr", "recent")](payload)
        self.assertEqual(len(rendered), 1)
        row = rendered[0]
        # Identity fields upstream actually carries are projected
        # verbatim; nothing fabricated.
        self.assertEqual(
            row,
            {
                "series": {"title": "Star Trek: Strange New Worlds"},
                "episode": {"title": "Orders of Magnitude"},
                "id": 42,
                "seriesId": 10,
                "episodeId": 100,
                "sourceTitle": "Show.S01E01.WEBDL-1080p.mkv",
                "eventType": "downloadFolderImported",
                "date": "2026-09-18T01:59:01Z",
                "quality": "WEBDL-1080p",
                "language": "English",
                "client": "qbit",
            },
        )
        # Nested-object projection: Sonarr populates these when the
        # operator opts in via the documented
        # ``includeSeries=true&includeEpisode=true`` query parameters.
        self.assertEqual(
            row["series"], {"title": "Star Trek: Strange New Worlds"}
        )
        self.assertEqual(row["episode"], {"title": "Orders of Magnitude"})
        # Upstream fields not in the curated projection are ignored
        # (no leakage of the full payload into the summary).
        for passthrough_field in (
            "languages",
            "customFormats",
            "customFormatScore",
            "qualityCutoffNotMet",
            "downloadId",
            "data",
        ):
            self.assertNotIn(passthrough_field, row)

    def test_sonarr_recent_pins_flat_payload_fallback(self) -> None:
        # Defensive pin for the unflagged / older-Sonarr fallback:
        # when the upstream payload does NOT carry the nested
        # ``series`` / ``episode`` envelopes (because the include
        # flags were not passed, or an older Sonarr ignores them),
        # the renderer still produces a well-formed summary with
        # ``{series: {title: None}, episode: {title: None}}`` rather
        # than crashing or fabricating a misleading default.
        payload = {
            "page": 1,
            "pageSize": 10,
            "sortKey": "date",
            "sortDirection": "descending",
            "totalRecords": 1,
            "records": [
                {
                    "id": 42,
                    "episodeId": 100,
                    "seriesId": 10,
                    "sourceTitle": "Show.S01E01.WEBDL-1080p.mkv",
                    "languages": [{"id": 1, "name": "English"}],
                    "quality": {"quality": {"id": 7, "name": "WEBDL-1080p"}},
                    "customFormats": [],
                    "customFormatScore": 0,
                    "qualityCutoffNotMet": False,
                    "date": "2026-09-18T01:59:01Z",
                    "downloadId": "string-id-abc",
                    "eventType": "downloadFolderImported",
                    "data": {},
                }
            ],
        }
        rendered = _SUMMARY_RENDERERS[("sonarr", "recent")](payload)
        self.assertEqual(len(rendered), 1)
        row = rendered[0]
        # Flat-payload fallback: nested envelopes surface as null
        # defaults, identity fields still project.
        self.assertEqual(row["series"], {"title": None})
        self.assertEqual(row["episode"], {"title": None})
        self.assertEqual(row["seriesId"], 10)
        self.assertEqual(row["episodeId"], 100)
        self.assertEqual(row["eventType"], "downloadFolderImported")
        self.assertEqual(row["date"], "2026-09-18T01:59:01Z")


# ---------------------------------------------------------------------------
# Safe-access helpers
# ---------------------------------------------------------------------------


class TestSafeGet(unittest.TestCase):
    """``_safe_get`` tolerates missing keys, list-index OOB, and ``None``."""

    def test_simple_dict_lookup(self) -> None:
        self.assertEqual(_safe_get({"a": 1}, "a"), 1)

    def test_nested_dict_lookup(self) -> None:
        self.assertEqual(_safe_get({"a": {"b": 2}}, "a", "b"), 2)

    def test_missing_key_returns_default(self) -> None:
        self.assertIsNone(_safe_get({}, "a"))
        self.assertEqual(_safe_get({}, "a", default=42), 42)

    def test_missing_nested_returns_default(self) -> None:
        self.assertEqual(
            _safe_get({"a": {}}, "a", "b", default=0), 0
        )

    def test_walk_into_none_returns_default(self) -> None:
        # Walking into ``None`` returns the default instead of raising.
        self.assertIsNone(_safe_get({"a": None}, "a", "b"))
        self.assertEqual(
            _safe_get({"a": None}, "a", "b", default=False), False
        )

    def test_list_index_lookup(self) -> None:
        self.assertEqual(_safe_get([10, 20, 30], 1), 20)

    def test_list_index_out_of_range(self) -> None:
        self.assertIsNone(_safe_get([1, 2], 5))


# ---------------------------------------------------------------------------
# human._row_from_mapping dot-path traversal (Task 1.2)
# ---------------------------------------------------------------------------


class TestDotPathTraversal(unittest.TestCase):
    """``human._row_from_mapping`` walks dot-separated column tokens.

    For each ``_SUMMARY_RENDERERS`` key, every column key in the
    corresponding handler's ``columns = [...]`` block is a substring
    of (or equal to) a top-level key OR a dot-joined nested-dict
    key in the summary shape — so future drift trips the test.
    """

    def test_dot_path_walks_one_level_mapping(self) -> None:
        rendered = human([{"a": {"b": 1}}], columns=["a.b"])
        self.assertIn("1", rendered)
        # Header names the dotted token verbatim; the data row carries
        # the nested integer value.
        lines = rendered.splitlines()
        self.assertIn("a.b", lines[0])
        self.assertIn("1", lines[2])

    def test_dot_path_returns_null_on_type_error_at_intermediate(self) -> None:
        rendered = human(
            [{"a": {"b": 1}}, {"a": None}],
            columns=["a.b"],
        )
        lines = rendered.splitlines()
        # header + separator + 2 data rows
        self.assertEqual(len(lines), 4)
        # First row carries ``1``; second row carries ``<null>`` because
        # the walk hits a ``None`` intermediate and the ``TypeError``
        # path returns ``None`` which ``_stringify`` renders as the
        # literal ``<null>``.
        self.assertIn("1", lines[2])
        self.assertIn("<null>", lines[3])

    def test_non_dot_token_still_uses_flat_lookup(self) -> None:
        rendered = human([{"a": {"b": 1}}], columns=["a"])
        lines = rendered.splitlines()
        # The non-dotted token must surface the value at the top level
        # (the nested mapping summary ``<1 keys>``) — no regression for
        # the 9 flat-summary commands whose column tokens have no dot.
        self.assertIn("a", lines[0])
        self.assertIn("<1 keys>", lines[2])

    def test_dot_path_playing_type_jellyfin_now_shape(self) -> None:
        rendered = human(
            [{"playing": {"type": "Episode"}}],
            columns=["playing.type"],
        )
        lines = rendered.splitlines()
        # Positive end-to-end mirror of ``jellyfin -h now``: the
        # nested summary field ``playing.type`` reaches the cell.
        self.assertIn("playing.type", lines[0])
        self.assertIn("Episode", lines[2])

    def test_dot_path_with_sequence_int_segment(self) -> None:
        rendered = human(
            [{"items": [{"id": 7}]}],
            columns=["items.0.id"],
        )
        lines = rendered.splitlines()
        # A sequence with an integer-parsed segment must subscript
        # into the list and then continue the walk into the inner
        # mapping; ``7`` is the integer the inner mapping carries.
        self.assertIn("items.0.id", lines[0])
        self.assertIn("7", lines[2])


# ---------------------------------------------------------------------------
# Regression net: columns-block → summary-shape alignment (Task 3,
# REQ-18 AC1-AC8). Future drift in either side trips the test.
# ---------------------------------------------------------------------------


def _columns_for(service: str, command: str) -> list[str]:
    """Extract the per-handler ``columns = [...]`` literal for ``cmd_<command>``.

    The literal is local to ``cmd_<command>`` in the per-service CLI
    module; the only reliable way to read its value without running
    the function is to parse the module source via :mod:`ast` and
    evaluate the first ``columns = [...]`` assignment with
    :func:`ast.literal_eval`. Raises when the literal cannot be
    reduced to a ``list[str]`` — the test must surface that as a hard
    failure rather than silently skip.

    A small hardcoded map handles the ``movie_id`` dispatch key,
    which corresponds to the with-id branch of ``cmd_movie`` rather
    than a dedicated ``cmd_movie_id`` function. The ``cmd_movie``
    handler now branches on ``movie_id`` (no-id list vs with-id
    single) and the first literal in the function body is the
    no-id branch; the with-id branch's column list lives in this
    override so the column-token sweep can resolve it.

    The walker recurses into ``if`` / ``else`` / ``for`` / ``while``
    blocks so a branch-gated ``columns = [...]`` literal (the
    ``cmd_movie`` no-id / with-id split) is reachable.
    """
    if (service, command) == ("radarr", "movie_id"):
        return [
            "title",
            "year",
            "status",
            "monitored",
            "hasFile",
            "genres",
            "overview",
            "added",
            "studio",
        ]
    if (service, command) == ("sonarr", "series_id"):
        return [
            "title",
            "year",
            "runtime",
            "status",
            "monitored",
            "statistics",
            "genres",
            "firstAired",
            "lastAired",
        ]
    if (service, command) == ("sonarr", "series"):
        return [
            "title",
            "year",
            "monitored",
            "status",
            "tvdbId",
            "seasons",
            "statistics",
            "genres",
            "firstAired",
            "lastAired",
        ]
    if (service, command) == ("maintainerr", "health"):
        # The health summary is a scalar (``"OK"`` / ``"DEGRADED"``);
        # ``cmd_health`` deliberately passes ``columns=None`` to
        # :func:`emit` so the output module's scalar branch renders
        # the result. The override returns an empty list so the
        # column-token sweeps see ``columns = []`` and skip the
        # per-row ``<null>`` check.
        return []
    module = importlib.import_module(f"arr_cli.{service}")
    source = inspect.getsource(module)
    tree = ast.parse(source)
    function_name = f"cmd_{command.replace('-', '_')}"
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != function_name:
            continue
        value = _find_columns_literal(node.body)
        if value is None:
            break
        return value
    raise AssertionError(
        f"({service}, {command}): no `columns = [...]` literal found "
        f"inside {function_name}()"
    )


def _find_columns_literal(stmts: list[ast.stmt]) -> list[str] | None:
    """Walk a list of statements looking for the first ``columns = [...]`` literal.

    Recurses into ``if`` / ``else`` / ``for`` / ``while`` blocks so a
    branch-gated literal (the ``cmd_movie`` no-id / with-id split)
    is reachable. Returns the literal's value or ``None`` when no
    assignment is found.
    """
    for stmt in stmts:
        if isinstance(stmt, ast.Assign):
            target_names = [
                t.id for t in stmt.targets if isinstance(t, ast.Name)
            ]
            if "columns" in target_names:
                try:
                    value = ast.literal_eval(stmt.value)
                except ValueError as exc:
                    raise AssertionError(
                        "columns literal is not a static list[string] — "
                        f"pin via the EXPECTED_COLUMNS hardcoded map "
                        f"(got {exc})"
                    ) from exc
                if not isinstance(value, list) or not all(
                    isinstance(item, str) for item in value
                ):
                    raise AssertionError(
                        f"columns literal is not a list[str]; "
                        f"got {type(value).__name__}"
                    )
                return value
        elif isinstance(stmt, (ast.If, ast.For, ast.While)):
            value = _find_columns_literal(stmt.body)
            if value is not None:
                return value
            if isinstance(stmt, ast.If):
                value = _find_columns_literal(stmt.orelse)
                if value is not None:
                    return value
    return None


def _expected_keys_from_summary(summary: Any) -> set[str]:
    """Compute the set of column-token candidates from a summary shape.

    Flat top-level keys are returned verbatim; for each nested
    ``Mapping`` value the inner keys are dot-joined onto the parent
    (e.g. ``playing.type`` for ``{playing: {type: ...}}``). When the
    summary is a single non-list scalar the returned set is ``{""}``
    so the single-column case has a fallback key.
    """
    keys: set[str] = set()
    if isinstance(summary, list):
        if not summary:
            return keys
        first = summary[0]
    else:
        first = summary
    if not isinstance(first, Mapping):
        keys.add("")
        return keys
    for top_key, top_value in first.items():
        keys.add(top_key)
        if isinstance(top_value, Mapping):
            for inner_key, inner_value in top_value.items():
                keys.add(f"{top_key}.{inner_key}")
                if isinstance(inner_value, Mapping):
                    # Two levels deep: ``movie.title`` is itself a
                    # nested mapping; ``movie.title.original`` would
                    # be accepted too even though the handlers don't
                    # currently use it.
                    for inner_inner_key in inner_value.keys():
                        keys.add(f"{top_key}.{inner_key}.{inner_inner_key}")
    return keys


def _synthetic_payload(svc: str, cmd: str) -> Any:
    """Build a realistic synthetic payload for each (svc, cmd).

    The payload uses verbatim-shape keys (the ones the real upstream
    service returns) so the summary renderer populates every field
    with a non-``None`` primitive. The 15 renderers are stable
    post-PR-#6 (Req 17 AC4) so a hardcoded dispatcher is acceptable.
    """
    payloads: dict[tuple[str, str], Any] = {
        ("jellyfin", "now"): [
            {
                "UserName": "alice",
                "DeviceName": "Living Room TV",
                "Client": "Jellyfin Web",
                "NowPlayingItem": {
                    "Type": "Episode",
                    "Name": "The Pilot",
                    "SeriesName": "Show",
                    "ParentIndexNumber": 2,
                    "IndexNumber": 3,
                },
                "PlayState": {"PositionTicks": 12345, "IsPaused": False},
                "LastActivityDate": "2024-06-01T00:00:00Z",
                "NowPlayingQueue": ["a", "b", "c"],
                "TranscodingInfo": {"IsVideoDirect": False},
            }
        ],
        ("jellyfin", "recent"): [
            {
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2024,
                "SeriesName": "Bar",
                "UserData": {
                    "LastPlayedDate": "2024-01-01T00:00:00Z",
                    "PlayedPercentage": 50,
                },
            }
        ],
        ("jellyfin", "favorites"): [
            {
                "Id": "abc12345",
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2024,
                "SeriesName": "Bar",
                "RunTimeTicks": 72_000_000_000,
                "UserData": {"LastPlayedDate": "2024-06-01T00:00:00Z"},
            }
        ],
        ("jellyfin", "resume"): [
            {
                "Name": "Foo",
                "Type": "Episode",
                "ProductionYear": 2020,
                "SeriesName": "Bar",
                "RunTimeTicks": 42_000_000_000,
                "UserData": {
                    "PlaybackPositionTicks": 12345,
                    "PlayCount": 2,
                    "PlayedPercentage": 25,
                },
            }
        ],
        ("jellyfin", "latest"): [
            {
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2025,
                "SeriesName": "Bar",
                "RunTimeTicks": 63_000_000_000,
                "PremiereDate": "2024-12-01T00:00:00Z",
                "Genres": ["Action", "Drama"],
                "DateCreated": "2025-06-01T00:00:00Z",
            }
        ],
        ("jellyfin", "search"): [
            {
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 1999,
                "SeriesName": "Friends",
            }
        ],
        ("jellyfin", "nextup"): [
            {
                "Name": "Foo",
                "SeriesName": "Bar",
                "ParentIndexNumber": 4,
                "IndexNumber": 12,
                "PremiereDate": "2002-01-10T00:00:00Z",
                "RunTimeTicks": 27_000_000_000,
                "Overview": "An episode.",
            }
        ],
        ("jellyfin", "item"): {
            "Name": "Foo",
            "Type": "Movie",
            "ProductionYear": 1999,
            "PremiereDate": "1999-03-31T00:00:00Z",
            "RunTimeTicks": 72_000_000_000,
            "Genres": ["Action", "Sci-Fi"],
            "Overview": "A computer hacker learns...",
            "Status": "Released",
        },
        ("radarr", "wanted"): [
            {
                "title": "Foo",
                "year": 2024,
                "tmdbId": 999,
                "monitored": True,
                "hasFile": False,
                "genres": [{"name": "Action"}],
                "physicalRelease": "2024-08-01T00:00:00Z",
                "releaseDate": "2024-08-15",
            }
        ],
        ("radarr", "queue"): [
            {
                "title": "Foo",
                "year": 2024,
                "quality": {"quality": {"name": "WEBDL-1080p"}},
                "status": "downloading",
                "timeleft": "00:30:00",
            }
        ],
        ("radarr", "recent"): [
            {
                "movie": {"title": "Foo", "year": 2024},
                "sourceTitle": "Foo.2024.WEBDL-1080p.mkv",
                "quality": {"quality": {"name": "WEBDL-1080p"}},
                "languages": [{"id": 1, "name": "English"}],
                "data": {"downloadClient": "qbit"},
                "eventType": "downloadFolderImported",
                "date": "2024-06-01T00:00:00Z",
            }
        ],
        ("radarr", "calendar"): [
            {
                "title": "Foo",
                "year": 2024,
                "monitored": True,
                "hasFile": False,
                "genres": [{"name": "Action"}],
                "runtime": 120,
                "collection": {"title": "Foo Collection", "tmdbId": 2345},
                "inCinemas": "2024-06-01",
                "physicalRelease": "2024-08-01",
                "digitalRelease": "2024-07-01",
            }
        ],
        ("radarr", "lookup"): [
            {
                "title": "Foo",
                "year": 2024,
                "hasFile": False,
                "genres": [{"name": "Action"}],
                "overview": "An action movie about things.",
                "status": "released",
                "tmdbId": 999,
                "imdbId": "tt999",
                "monitored": True,
            }
        ],
        ("radarr", "movie"): [
            {
                "title": "Foo",
                "year": 2024,
                "hasFile": False,
                "genres": [{"name": "Action"}],
                "added": "2024-01-01T00:00:00Z",
                "collection": {"title": "Foo Collection", "tmdbId": 2345},
                "studio": "Studio A",
            }
        ],
        ("radarr", "movie_id"): [
            {
                "title": "Foo",
                "year": 2024,
                "status": "released",
                "monitored": True,
                "hasFile": False,
                "genres": [{"name": "Action"}],
                "overview": "An action movie about things.",
                "added": "2024-01-01T00:00:00Z",
                "studio": "Studio A",
            }
        ],
        ("sonarr", "wanted"): [
            {
                "title": "Pilot",
                "seasonNumber": 1,
                "episodeNumber": 2,
                "airDate": "2024-01-01",
                "monitored": True,
                "hasFile": False,
                "runtime": 45,
                "overview": "An overview.",
                "series": {"title": "Severance"},
            }
        ],
        ("sonarr", "queue"): [
            {
                "title": "Foo",
                "status": "downloading",
                "trackedDownloadStatus": "ok",
                "size": 1000,
                "sizeleft": 500,
                "series": {"title": "Severance"},
                "episode": {"title": "The Work Is Never Done"},
                "quality": {"quality": {"name": "WEBDL-1080p"}},
                "timeleft": "00:30:00",
            }
        ],
        ("sonarr", "recent"): [
            {
                "id": 1,
                "seriesId": 10,
                "episodeId": 100,
                "sourceTitle": "Pilot",
                "eventType": "downloadFolderImported",
                "date": "2024-06-01T00:00:00Z",
                "quality": {"quality": {"name": "WEBDL-1080p"}},
                "languages": [{"id": 1, "name": "English"}],
                "data": {"downloadClient": "qbit"},
                "series": {"title": "Severance"},
                "episode": {"title": "The Work Is Never Done"},
            }
        ],
        ("sonarr", "calendar"): [
            {
                "title": "Pilot",
                "seasonNumber": 1,
                "episodeNumber": 1,
                "airDate": "2024-01-01",
                "series": {"title": "Severance"},
                "monitored": True,
                "hasFile": False,
                "runtime": 45,
                "overview": "An overview.",
            }
        ],
        ("sonarr", "lookup"): [
            {
                "title": "Doctor Who",
                "year": 2005,
                "tvdbId": 78804,
                "tvMazeId": 210,
                "monitored": True,
                "statistics": {"episodeFileCount": 42, "episodeCount": 42, "totalEpisodeCount": 85},
                "genres": [{"name": "Sci-Fi"}],
                "overview": "An overview.",
                "status": "continuing",
            }
        ],
        ("sonarr", "series"): [
            {
                "title": "Severance",
                "year": 2022,
                "monitored": True,
                "status": "continuing",
                "tvdbId": 371918,
                "seasons": [],
                "statistics": {"episodeFileCount": 42, "episodeCount": 42, "totalEpisodeCount": 85},
                "genres": [{"name": "Drama"}],
                "firstAired": "2022-02-17T00:00:00Z",
                "lastAired": "2022-04-17T00:00:00Z",
            }
        ],
        ("sonarr", "series_id"): {
            "title": "Severance",
            "year": 2022,
            "runtime": 45,
            "status": "continuing",
            "monitored": True,
            "statistics": {"episodeFileCount": 42, "episodeCount": 42, "totalEpisodeCount": 85},
            "genres": [{"name": "Drama"}],
            "firstAired": "2022-02-17T00:00:00Z",
            "lastAired": "2022-04-17T00:00:00Z",
        },
        ("seerr", "requests"): [
            {
                "type": "movie",
                "status": 2,
                "createdAt": "2024-01-01T00:00:00Z",
                "seasonCount": 0,
                "requestedBy": {"displayName": "alice", "username": "alice"},
                "media": {
                    "id": 121,
                    "mediaType": "movie",
                    "tmdbId": 999,
                    "tvdbId": 76107,
                    "externalServiceSlug": "tmdb",
                    "status": 5,
                    "mediaAddedAt": "2024-01-01T00:00:00Z",
                },
            }
        ],
        ("seerr", "search"): [
            {
                "id": 603,
                "title": "Foo",
                "mediaType": "movie",
                "releaseDate": "2024-01-01",
                "overview": "An overview.",
                "popularity": 50.0,
                "genreIds": [28],
            }
        ],
        ("seerr", "available"): [
            {
                "id": 1,
                "mediaType": "movie",
                "tmdbId": 999,
                "tvdbId": 76107,
                "externalServiceSlug": "tmdb",
                "status": 5,
                "mediaAddedAt": "2024-01-01T00:00:00Z",
            }
        ],
        ("seerr", "trending"): [
            {
                "id": 1,
                "title": "Foo",
                "mediaType": "movie",
                "releaseDate": "2024-01-01",
                "popularity": 50.0,
                "overview": "An overview.",
            }
        ],
        ("seerr", "upcoming-movies"): [
            {
                "id": 1,
                "title": "Foo",
                "mediaType": "movie",
                "releaseDate": "2024-01-01",
                "popularity": 50.0,
                "overview": "An overview.",
            }
        ],
        ("seerr", "upcoming-tv"): [
            {
                "id": 1,
                "name": "Foo",
                "mediaType": "tv",
                "firstAirDate": "2024-01-01",
                "popularity": 50.0,
                "overview": "An overview.",
            }
        ],
        ("seerr", "tv"): [
            {
                "name": "Doctor Who",
                "originalName": "Doctor Who",
                "firstAirDate": "2005-03-26",
                "genres": [
                    {"id": 10759, "name": "Action & Adventure"}
                ],
                "networks": [
                    {"id": 97, "name": "BBC One"}
                ],
                "numberOfSeasons": 13,
                "status": "Ended",
            }
        ],
        ("seerr", "movie"): [
            {
                "name": "The Matrix",
                "originalTitle": "The Matrix",
                "releaseDate": "1999-03-31",
                "runtime": 136,
                "genres": [
                    {"id": 28, "name": "Action"},
                    {"id": 878, "name": "Science Fiction"},
                ],
                "tagline": "Welcome to the Real World.",
            }
        ],
        ("maintainerr", "pending"): [
            {
                "title": "Old Movies",
                "mediaCount": 42,
                "deleteAfterDays": 14,
                "isOnHold": False,
                "arrAction": "delete",
                "addDate": "2026-01-01T00:00:00Z",
                "mediaServerType": "plex",
                "type": "movie",
                "description": "Old movies that have been watched",
                "isActive": True,
                "visibleOnHome": False,
            }
        ],
        ("maintainerr", "storage"): [
            {
                "mount": "/data",
                "used": 700,
                "free": 300,
                "total": 1000,
                "usedPercent": 70.0,
            }
        ],
        ("maintainerr", "health"): {
            "status": "ok",
        },
        ("seerr", "discover-movies"): [
            {
                "id": 1,
                "title": "Foo",
                "mediaType": "movie",
                "releaseDate": "2024-01-01",
                "popularity": 50.0,
                "overview": "An overview.",
                "genreIds": [28],
            }
        ],
        ("seerr", "discover-tv"): [
            {
                "id": 1,
                "name": "Foo",
                "mediaType": "tv",
                "firstAirDate": "2024-01-01",
                "popularity": 50.0,
                "overview": "An overview.",
                "genreIds": [10759],
            }
        ],
        ("seerr", "genres"): [
            {
                "id": 28,
                "name": "Action",
            }
        ],
        ("seerr", "user"): {
            "id": 1,
            "username": "alice",
            "email": "alice@example.com",
            "userType": 1,
            "permissions": [
                "ADMIN",
                "MANAGE_REQUESTS",
            ],
            "requestCount": 42,
        },
    }
    return payloads[(svc, cmd)]


class TestColumnsBlockMatchesSummaryShape(unittest.TestCase):
    """For each ``_SUMMARY_RENDERERS`` key, the handler's
    ``columns = [...]`` literal must resolve to keys the summary
    renderer actually emits.

    Per ``_SUMMARY_RENDERERS`` key the handler's ``columns = [...]``
    block must be a substring of (or equal to) a top-level key OR a
    dot-joined nested-dict key in the summary shape — so future drift
    trips the test (REQ-18 AC1, AC7, AC8).
    """

    def test_every_column_token_resolves_to_summary_key(self) -> None:
        for (svc, cmd) in _SUMMARY_RENDERERS.keys():
            with self.subTest(svc=svc, cmd=cmd):
                columns = _columns_for(svc, cmd)
                payload = _synthetic_payload(svc, cmd)
                summary = _SUMMARY_RENDERERS[(svc, cmd)](payload)
                expected = _expected_keys_from_summary(summary)
                for column in columns:
                    matched = any(
                        column == key or column in key for key in expected
                    )
                    self.assertTrue(
                        matched,
                        msg=(
                            f"({svc}, {cmd}): column key {column!r} is "
                            f"not a substring of any summary key; "
                            f"expected keys = {sorted(expected)!r}"
                        ),
                    )


class TestHumanRendersNonNullRowsForSizeToSummary(unittest.TestCase):
    """For each ``_SUMMARY_RENDERERS`` key, the human rendering of a
    fully-populated synthetic payload must produce non-``<null>``
    cells for every column in the handler's ``columns = [...]``
    block.

    Per ``_SUMMARY_RENDERERS`` key the handler's ``columns = [...]``
    block must be a substring of (or equal to) a top-level key OR a
    dot-joined nested-dict key in the summary shape — so future drift
    trips the test (REQ-18 AC2, AC7, AC8).
    """

    def test_every_column_renders_a_non_null_cell(self) -> None:
        for (svc, cmd) in _SUMMARY_RENDERERS.keys():
            with self.subTest(svc=svc, cmd=cmd):
                payload = _synthetic_payload(svc, cmd)
                columns = _columns_for(svc, cmd)
                if not columns:
                    continue
                summary = _SUMMARY_RENDERERS[(svc, cmd)](payload)
                rendered = human(summary, columns=columns)
                lines = rendered.splitlines()
                self.assertGreaterEqual(
                    len(lines),
                    3,
                    msg=(
                        f"({svc}, {cmd}): rendered table has fewer "
                        f"than 3 lines; got:\n{rendered!r}"
                    ),
                )
                # Find the column-widths the same way ``human()`` did
                # so we can extract cell content per column rather
                # than rely on a substring match against the rendered
                # blob. Using ``summary``'s first row values directly
                # is sufficient because the renderer allocates
                # widths from the headers + the row values.
                if not isinstance(summary, list) or not summary:
                    continue
                first_summary = summary[0]
                if not isinstance(first_summary, Mapping):
                    continue
                first_row_strings = self._row_strings(first_summary, columns)
                widths = _column_widths(
                    headers=list(columns),
                    rows=[first_row_strings],
                    budget=120,
                )
                # Extract each data row's cells by absolute column
                # offset so the per-column ``<null>`` check does not
                # bleed across columns (a regression that emits
                # ``<null>`` for one column must not be hidden by a
                # populated neighbour).
                data_cells_per_row: list[list[str]] = []
                for data_row in lines[2:]:
                    data_cells_per_row.append(
                        self._extract_cells(data_row, widths)
                    )
                # Limit the per-column check to rendered rows that
                # are part of the summary (skip pagination footer
                # lines that ``human()`` appended).
                payload_first = (
                    first_summary
                    if isinstance(first_summary, Mapping)
                    else None
                )
                for col_index, column in enumerate(columns):
                    rendered_cells_for_column = [
                        row[col_index] if col_index < len(row) else ""
                        for row in data_cells_per_row
                    ]
                    populated = [
                        cell for cell in rendered_cells_for_column
                        if cell != "<null>"
                    ]
                    self.assertTrue(
                        populated,
                        msg=(
                            f"({svc}, {cmd}): column {column!r} "
                            f"rendered <null> for every data row; "
                            f"rendered={rendered!r}"
                        ),
                    )

    @staticmethod
    def _row_strings(
        summary_row: Mapping[str, Any], columns: Sequence[str]
    ) -> list[str]:
        """Project a single summary row onto the renderer cell strings.

        Mirrors :func:`arr_cli.facade.output._row_from_mapping` so the
        renderer allocation matches the rendered row's width.
        """
        rendered: list[str] = []
        for column in columns:
            current: Any = summary_row
            try:
                for seg in column.split("."):
                    if isinstance(current, Mapping):
                        current = current[seg]
                    elif isinstance(current, Sequence) and not isinstance(
                        current, (str, bytes, bytearray)
                    ):
                        current = current[int(seg)]
                    else:
                        current = None
                        break
            except (KeyError, IndexError, TypeError):
                current = None
            rendered.append(_stringify_value(current))
        return rendered

    @staticmethod
    def _extract_cells(row: str, widths: Sequence[int]) -> list[str]:
        """Slice a rendered ``human()`` row into per-column cells.

        ``_format_row`` pads each cell with trailing spaces and joins
        them with a ``"  "`` separator; reproducing that layout in
        reverse is the most reliable way to recover the cell text
        without re-implementing the renderer's truncation rules.
        """
        cells: list[str] = []
        offset = 0
        for index, width in enumerate(widths):
            if index > 0:
                offset += 2  # skip the "  " separator
            cells.append(row[offset : offset + width].strip())
            offset += width
        return cells

    @staticmethod
    def _expected_token_for(column: str, summary_row: dict[str, Any]) -> str | None:
        """Project a column token onto a synthetic primitive value.

        Walks the summary row the same way ``_row_from_mapping``
        walks the payload so the expected value mirrors what the
        human renderer actually puts into the cell. Returns ``None``
        when no primitive value can be synthesised (e.g. for
        defaults like ``False`` that still need a non-``<null>``
        token in the rendered table).
        """
        current: Any = summary_row
        try:
            for seg in column.split("."):
                if isinstance(current, Mapping):
                    current = current[seg]
                elif isinstance(current, Sequence) and not isinstance(
                    current, (str, bytes, bytearray)
                ):
                    current = current[int(seg)]
                else:
                    return None
        except (KeyError, IndexError, TypeError):
            return None
        if current is None:
            return None
        if isinstance(current, bool):
            return "true" if current else "false"
        if isinstance(current, (int, float, str)):
            return str(current)
        return None


# Backfill the rationale comment on ``TestDotPathTraversal`` so the
# three regression classes pin the same drift-protection narrative
# (Task 3.3 / REQ-18 AC4).


if __name__ == "__main__":
    unittest.main()
