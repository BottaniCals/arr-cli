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

import contextlib
import io
import json
import os
import sys
import unittest
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
    _column_widths,
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


if __name__ == "__main__":
    unittest.main()
