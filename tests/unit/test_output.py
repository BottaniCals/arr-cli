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
    _safe_get,
    _SUMMARY_RENDERERS,
    _truncate,
)


def _capture_stdout(callable_: Any, *args: Any, **kwargs: Any) -> str:
    """Invoke ``callable_`` with stdout redirected to a StringIO buffer."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        callable_(*args, **kwargs)
    return buffer.getvalue()


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
            "Overview": "Lorem ipsum",
        }
    ],
    ("jellyfin", "favorites"): [
        {
            "Name": "Foo",
            "Type": "Movie",
            "ProductionYear": 2020,
            "SeriesName": None,
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
            "Overview": "Lorem ipsum",
        }
    ],
    ("jellyfin", "latest"): [
        {
            "Name": "Foo",
            "Type": "Movie",
            "ProductionYear": 2025,
            "SeriesName": None,
            "DateCreated": "2025-06-01T00:00:00Z",
            "Overview": "Lorem ipsum",
        }
    ],
    ("radarr", "wanted"): [
        {
            "title": "Foo",
            "year": 2024,
            "tmdbId": 999,
            "monitored": True,
            "runtime": 120,
        }
    ],
    ("radarr", "queue"): [
        {
            "title": "Foo",
            "status": "downloading",
            "trackedDownloadStatus": "ok",
            "size": 1000,
            "sizeleft": 500,
            "downloadClient": "qbit",
        }
    ],
    ("radarr", "recent"): [
        {
            "movie": {"title": "Foo", "year": 2024},
            "eventType": "downloadFolderImported",
            "date": "2024-06-01",
            "sourcePath": "/movies/foo",
        }
    ],
    ("sonarr", "wanted"): [
        {
            "title": "Pilot",
            "seasonNumber": 1,
            "episodeNumber": 1,
            "airDate": "2024-01-01",
            "monitored": True,
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
            "downloadClient": "qbit",
        }
    ],
    ("sonarr", "recent"): [
        {
            "series": {"title": "Show"},
            "episode": {"title": "Pilot"},
            "eventType": "downloadFolderImported",
            "date": "2024-06-01",
            "sourcePath": "/tv/show",
        }
    ],
    ("seerr", "requests"): [
        {
            "title": "Foo",
            "type": "movie",
            "status": "pending",
            "createdAt": "2024-01-01",
            "requestedBy": {"displayName": "alice"},
            "externalId": "tmdb:999",
        }
    ],
    ("seerr", "search"): [
        {
            "title": "Foo",
            "mediaType": "movie",
            "releaseDate": "2024-01-01",
            "mediaInfo": {"tmdbId": 999},
            "overview": "Lorem ipsum",
        }
    ],
    ("seerr", "available"): [
        {
            "title": "Foo",
            "mediaType": "movie",
            "releaseDate": "2024-01-01",
            "mediaInfo": {"status": 5},
            "overview": "Lorem ipsum",
        }
    ],
    ("maintainerr", "pending"): [
        {
            "title": "Old Movies",
            "mediaCount": 42,
            "deleteAfterDays": 14,
            "isOnHold": False,
            "collectionId": 7,
        }
    ],
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
                # output for the synthetic payload.
                summary_first_row = summarize(svc, cmd, payload)
                self.assertIsInstance(summary_first_row, list)
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
                self.assertIsInstance(summary_first, list)
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


class TestEmitHumanVerbatimFallback(unittest.TestCase):
    """``emit(..., human_mode=True, verbose_mode=False)`` falls through to
    verbatim when ``(service, command)`` is not in
    :data:`_SUMMARY_RENDERERS` (REQ-2 AC7)."""

    def test_known_non_candidate_jellyfin_search(self) -> None:
        payload = [{"Name": "Foo", "Type": "Movie"}]
        out = _capture_stdout(
            emit,
            payload,
            human_mode=True,
            verbose_mode=False,
            service="jellyfin",
            command="search",
        )
        expected_buffer = io.StringIO()
        print(human(payload), file=expected_buffer)
        self.assertEqual(out, expected_buffer.getvalue())

    def test_known_non_candidate_radarr_calendar(self) -> None:
        payload = [{"title": "Foo", "releaseDate": "2024-01-01"}]
        out = _capture_stdout(
            emit,
            payload,
            human_mode=True,
            verbose_mode=False,
            service="radarr",
            command="calendar",
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
        # verbatim JSON.
        payload = {"foo": "bar"}
        out = _capture_stdout(
            emit,
            payload,
            human_mode=False,
            verbose_mode=False,
            service="jellyfin",
            command="item",
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
        # A non-candidate command (e.g. ``jellyfin item``) falls
        # through to verbatim even though ``service`` / ``command``
        # are non-empty.
        payload = {"Name": "Foo"}
        out = _capture_stdout(
            emit,
            payload,
            human_mode=False,
            verbose_mode=False,
            service="jellyfin",
            command="item",
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


class TestSummaryJellyfinFavorites(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("jellyfin", "favorites")]`` matches the spec."""

    def test_favorites_shape(self) -> None:
        payload = [
            {
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
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2020,
                "SeriesName": None,
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
                    "Name": "Fireheart",
                    "Type": "Movie",
                    "ProductionYear": 2011,
                    "SeriesName": None,
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
            },
        )

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("jellyfin", "resume")](None),
            [],
        )


class TestSummaryJellyfinLatest(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("jellyfin", "latest")]`` matches the spec."""

    def test_latest_shape(self) -> None:
        payload = [
            {
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2025,
                "SeriesName": None,
                "DateCreated": "2025-06-01T00:00:00Z",
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
                "DateCreated": "2025-06-01T00:00:00Z",
            },
        )

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("jellyfin", "latest")](None),
            [],
        )


class TestSummaryRadarrWanted(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("radarr", "wanted")]`` matches the spec."""

    def test_wanted_shape(self) -> None:
        payload = [
            {
                "title": "Foo",
                "year": 2024,
                "tmdbId": 999,
                "monitored": True,
            }
        ]
        rendered = _SUMMARY_RENDERERS[("radarr", "wanted")](payload)
        self.assertEqual(
            rendered[0],
            {"title": "Foo", "year": 2024, "tmdbId": 999, "monitored": True},
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
                "status": "downloading",
                "trackedDownloadStatus": "ok",
                "size": 1000,
                "sizeleft": 500,
            }
        ]
        rendered = _SUMMARY_RENDERERS[("radarr", "queue")](payload)
        self.assertEqual(
            rendered[0],
            {
                "title": "Foo",
                "status": "downloading",
                "trackedDownloadStatus": "ok",
                "size": 1000,
                "sizeleft": 500,
            },
        )

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("radarr", "queue")](None),
            [],
        )


class TestSummaryRadarrRecent(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("radarr", "recent")]`` matches the spec."""

    def test_recent_with_movie(self) -> None:
        payload = [
            {
                "movie": {"title": "Foo", "year": 2024},
                "eventType": "downloadFolderImported",
                "date": "2024-06-01",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("radarr", "recent")](payload)
        self.assertEqual(
            rendered[0],
            {
                "movie": {"title": "Foo", "year": 2024},
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
            },
        )

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("sonarr", "wanted")](None),
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
            },
        )

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("sonarr", "queue")](None),
            [],
        )


class TestSummarySonarrRecent(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("sonarr", "recent")]`` matches the spec."""

    def test_recent_with_nested_objects(self) -> None:
        payload = [
            {
                "series": {"title": "Show"},
                "episode": {"title": "Pilot"},
                "eventType": "downloadFolderImported",
                "date": "2024-06-01",
            }
        ]
        rendered = _SUMMARY_RENDERERS[("sonarr", "recent")](payload)
        self.assertEqual(
            rendered[0],
            {
                "series": {"title": "Show"},
                "episode": {"title": "Pilot"},
                "eventType": "downloadFolderImported",
                "date": "2024-06-01",
            },
        )

    def test_recent_missing_nested_objects(self) -> None:
        payload = [{"eventType": "x", "date": "y"}]
        rendered = _SUMMARY_RENDERERS[("sonarr", "recent")](payload)
        self.assertEqual(rendered[0]["series"], {"title": None})
        self.assertEqual(rendered[0]["episode"], {"title": None})

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("sonarr", "recent")](None),
            [],
        )


class TestSummarySeerrRequests(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "requests")]`` matches the spec."""

    def test_requests_shape(self) -> None:
        payload = [
            {
                "title": "Foo",
                "type": "movie",
                "status": "pending",
                "createdAt": "2024-01-01",
                "requestedBy": {"displayName": "alice"},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "requests")](payload)
        self.assertEqual(
            rendered[0],
            {
                "title": "Foo",
                "type": "movie",
                "status": "pending",
                "createdAt": "2024-01-01",
                "requestedBy": {"displayName": "alice"},
            },
        )

    def test_requests_missing_requester(self) -> None:
        payload = [{"title": "Foo", "type": "movie", "status": "x", "createdAt": "y"}]
        rendered = _SUMMARY_RENDERERS[("seerr", "requests")](payload)
        self.assertEqual(rendered[0]["requestedBy"], {"displayName": None})

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "requests")](None),
            [],
        )


class TestSummarySeerrSearch(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "search")]`` matches the spec."""

    def test_search_shape(self) -> None:
        payload = [
            {
                "title": "Foo",
                "mediaType": "movie",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"tmdbId": 999},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "search")](payload)
        self.assertEqual(
            rendered[0],
            {
                "title": "Foo",
                "mediaType": "movie",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"tmdbId": 999},
            },
        )

    def test_search_missing_media_info(self) -> None:
        payload = [
            {"title": "Foo", "mediaType": "movie", "releaseDate": "y"}
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "search")](payload)
        self.assertEqual(rendered[0]["mediaInfo"], {"tmdbId": 0})

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "search")](None),
            [],
        )


class TestSummarySeerrAvailable(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "available")]`` matches the spec."""

    def test_available_shape(self) -> None:
        payload = [
            {
                "title": "Foo",
                "mediaType": "movie",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"status": 5},
            }
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "available")](payload)
        self.assertEqual(
            rendered[0],
            {
                "title": "Foo",
                "mediaType": "movie",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"status": 5},
            },
        )

    def test_available_missing_media_info(self) -> None:
        payload = [
            {"title": "Foo", "mediaType": "movie", "releaseDate": "y"}
        ]
        rendered = _SUMMARY_RENDERERS[("seerr", "available")](payload)
        self.assertEqual(rendered[0]["mediaInfo"], {"status": 0})

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("seerr", "available")](None),
            [],
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


class TestSummarySeerrTv(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("seerr", "tv")]`` matches the spec."""

    def test_tv_shape(self) -> None:
        payload = {
            "name": "Doctor Who",
            "originalName": "Doctor Who",
            "firstAirDate": "2005-03-26",
            "genres": [{"name": "Action & Adventure"}],
            "networks": [{"name": "BBC One"}],
            "numberOfSeasons": 13,
            "status": "Ended",
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "tv")](payload)
        self.assertEqual(
            rendered,
            {
                "name": "Doctor Who",
                "originalName": "Doctor Who",
                "firstAirDate": "2005-03-26",
                "genres": "Action & Adventure",
                "networks": "BBC One",
                "numberOfSeasons": 13,
                "status": "Ended",
                "ratings": None,
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

    def test_tv_ratings_merged_under_ratings_key(self) -> None:
        payload = {
            "name": "Doctor Who",
            "originalName": "Doctor Who",
            "firstAirDate": "2005-03-26",
            "genres": [{"name": "Action & Adventure"}],
            "networks": [{"name": "BBC One"}],
            "numberOfSeasons": 13,
            "status": "Ended",
            "ratings": {"criticsScore": 90, "audienceScore": 86},
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "tv")](payload)
        self.assertEqual(
            rendered["ratings"],
            {"criticsScore": 90, "audienceScore": 86},
        )

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
        rendered = _SUMMARY_RENDERERS[("seerr", "movie")](payload)
        # Raw runtime minutes ``136`` reformats as ``"2h 16m"``;
        # the spec'd display format which matters because raw
        # minutes is not human-readable.
        self.assertEqual(
            rendered,
            {
                "name": "The Matrix",
                "originalTitle": "The Matrix",
                "releaseDate": "1999-03-31",
                "runtime": "2h 16m",
                "genres": "Action, Science Fiction",
                "tagline": "Welcome to the Real World.",
                "ratings": None,
            },
        )

    def test_movie_missing_genres_renders_none(self) -> None:
        payload = {
            "name": "The Matrix",
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

    def test_movie_missing_runtime_renders_none(self) -> None:
        payload = {
            "name": "The Matrix",
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
            "name": "The Matrix",
            "runtime": 0,
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "movie")](payload)
        self.assertIsNone(rendered["runtime"])

    def test_movie_ratings_merged_under_ratings_key(self) -> None:
        payload = {
            "name": "The Matrix",
            "originalTitle": "The Matrix",
            "releaseDate": "1999-03-31",
            "runtime": 136,
            "genres": [{"name": "Action"}],
            "tagline": "Welcome to the Real World.",
            "ratings": {"criticsScore": 83, "audienceScore": 85},
        }
        rendered = _SUMMARY_RENDERERS[("seerr", "movie")](payload)
        self.assertEqual(
            rendered["ratings"],
            {"criticsScore": 83, "audienceScore": 85},
        )

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
            "name": "The Matrix",
            "originalTitle": "The Matrix",
            "releaseDate": "1999-03-31",
            "runtime": 136,
            "genres": [{"name": "Action"}],
            "tagline": "Welcome to the Real World.",
        }]
        rendered = _SUMMARY_RENDERERS[("seerr", "movie")](payload)
        self.assertEqual(rendered["name"], "The Matrix")
        self.assertEqual(rendered["runtime"], "2h 16m")
        self.assertIsNone(rendered["ratings"])

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


class TestSummaryMaintainerrPending(unittest.TestCase):
    """``_SUMMARY_RENDERERS[("maintainerr", "pending")]`` matches the spec."""

    def test_pending_shape(self) -> None:
        payload = [
            {
                "title": "Old Movies",
                "mediaCount": 42,
                "deleteAfterDays": 14,
                "isOnHold": False,
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
            },
        )

    def test_non_list_returns_empty_list(self) -> None:
        self.assertEqual(
            _SUMMARY_RENDERERS[("maintainerr", "pending")](None),
            [],
        )


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
    """
    module = importlib.import_module(f"arr_cli.{service}")
    source = inspect.getsource(module)
    tree = ast.parse(source)
    function_name = f"cmd_{command.replace('-', '_')}"
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != function_name:
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            target_names = [
                t.id for t in stmt.targets if isinstance(t, ast.Name)
            ]
            if "columns" not in target_names:
                continue
            try:
                value = ast.literal_eval(stmt.value)
            except ValueError as exc:
                raise AssertionError(
                    f"({service}, {command}): columns literal is not "
                    f"a static list[string] — pin via the "
                    f"EXPECTED_COLUMNS hardcoded map (got {exc})"
                ) from exc
            if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value
            ):
                raise AssertionError(
                    f"({service}, {command}): columns literal is not "
                    f"a list[str]; got {type(value).__name__}"
                )
            return value
    raise AssertionError(
        f"({service}, {command}): no `columns = [...]` literal found "
        f"inside {function_name}()"
    )


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
            }
        ],
        ("jellyfin", "recent"): [
            {
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2024,
                "SeriesName": "Bar",
                "UserData": {"LastPlayedDate": "2024-01-01T00:00:00Z"},
            }
        ],
        ("jellyfin", "favorites"): [
            {
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2024,
                "SeriesName": "Bar",
            }
        ],
        ("jellyfin", "resume"): [
            {
                "Name": "Foo",
                "Type": "Episode",
                "ProductionYear": 2020,
                "SeriesName": "Bar",
                "UserData": {
                    "PlaybackPositionTicks": 12345,
                    "PlayCount": 2,
                },
            }
        ],
        ("jellyfin", "latest"): [
            {
                "Name": "Foo",
                "Type": "Movie",
                "ProductionYear": 2025,
                "SeriesName": "Bar",
                "DateCreated": "2025-06-01T00:00:00Z",
            }
        ],
        ("radarr", "wanted"): [
            {
                "title": "Foo",
                "year": 2024,
                "tmdbId": 999,
                "monitored": True,
            }
        ],
        ("radarr", "queue"): [
            {
                "title": "Foo",
                "status": "downloading",
                "trackedDownloadStatus": "ok",
                "size": 1000,
                "sizeleft": 500,
            }
        ],
        ("radarr", "recent"): [
            {
                "movie": {"title": "Foo", "year": 2024},
                "eventType": "downloadFolderImported",
                "date": "2024-06-01T00:00:00Z",
            }
        ],
        ("sonarr", "wanted"): [
            {
                "title": "Pilot",
                "seasonNumber": 1,
                "episodeNumber": 2,
                "airDate": "2024-01-01",
                "monitored": True,
            }
        ],
        ("sonarr", "queue"): [
            {
                "title": "Foo",
                "status": "downloading",
                "trackedDownloadStatus": "ok",
                "size": 1000,
                "sizeleft": 500,
            }
        ],
        ("sonarr", "recent"): [
            {
                "series": {"title": "Show"},
                "episode": {"title": "Pilot"},
                "eventType": "downloadFolderImported",
                "date": "2024-06-01T00:00:00Z",
            }
        ],
        ("seerr", "requests"): [
            {
                "title": "Foo",
                "type": "movie",
                "status": "pending",
                "createdAt": "2024-01-01T00:00:00Z",
                "requestedBy": {"displayName": "alice"},
            }
        ],
        ("seerr", "search"): [
            {
                "title": "Foo",
                "mediaType": "movie",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"tmdbId": 999},
            }
        ],
        ("seerr", "available"): [
            {
                "title": "Foo",
                "mediaType": "movie",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"status": 5},
            }
        ],
        ("seerr", "trending"): [
            {
                "title": "Foo",
                "mediaType": "movie",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"tmdbId": 999},
            }
        ],
        ("seerr", "upcoming-movies"): [
            {
                "title": "Foo",
                "mediaType": "movie",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"tmdbId": 999},
            }
        ],
        ("seerr", "upcoming-tv"): [
            {
                "title": "Foo",
                "mediaType": "tv",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"tmdbId": 999},
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
            }
        ],
        ("seerr", "discover-movies"): [
            {
                "title": "Foo",
                "mediaType": "movie",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"tmdbId": 999},
            }
        ],
        ("seerr", "discover-tv"): [
            {
                "title": "Foo",
                "mediaType": "tv",
                "releaseDate": "2024-01-01",
                "mediaInfo": {"tmdbId": 999},
            }
        ],
        ("seerr", "genres"): [
            {
                "id": 28,
                "name": "Action",
            }
        ],
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
