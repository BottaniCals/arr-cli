"""Output formatting for the arr-cli facade (task 6).

This module owns two responsibilities:

* :func:`emit` — single entry point used by every service CLI to write
  a payload to stdout. When ``human`` is False the payload is JSON
  pass-through (REQ-3 AC1); when True the payload is rendered as a
  tabular human-readable view (REQ-3 AC2).
* :func:`human` — tabular renderer that handles list-of-dict,
  list-of-list, dict, and scalar payloads per design.md "Components
  and Interfaces / arr_facade.output".

Diagnostics never mix with stdout (REQ-3 AC4). All warnings go to
stderr via the module logger; the renderer itself writes only the
rendered payload to stdout via the standard ``print`` builtin. UTF-8
is preserved end-to-end by passing ``ensure_ascii=False`` to
:func:`json.dumps` (REQ-3 AC5) and by writing to stdout with the
default (locale-aware) encoding.

The width budget follows the NFR-Usability cap of 120 columns and
honors the ``COLUMNS`` environment variable. Width is clamped to
``[20, max_width]`` so tables never balloon past the documented
budget and never collapse below a useful minimum on tiny terminals.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "emit",
    "human",
    "resolve_width",
    "DEFAULT_MAX_WIDTH",
    "MIN_WIDTH",
    "DEFAULT_LIMIT",
]


#: Maximum width (in characters) for human-mode tables. Matches
#: the documented NFR-Usability cap.
DEFAULT_MAX_WIDTH: int = 120

#: Minimum width clamp so tables do not collapse below a usable
#: size on tiny terminals. Anything narrower would force every cell
#: to ``width // n_columns`` which is not displayable.
MIN_WIDTH: int = 20

#: Default row-count cap for human-mode pagination. Overridden
#: per-invocation via ``--limit``; passed in here via ``human(..., limit=...)``.
DEFAULT_LIMIT: int = 20


_logger = logging.getLogger("arr_cli.facade.output")


# ---------------------------------------------------------------------------
# Width resolution
# ---------------------------------------------------------------------------


def resolve_width(*, max_width: int = DEFAULT_MAX_WIDTH) -> int:
    """Return the effective table width honouring ``COLUMNS``.

    The precedence chain mirrors design.md:

    1. ``COLUMNS`` env var, if set and parses to a positive int.
    2. :func:`shutil.get_terminal_size` fallback (the stdlib's
       standard tty probe; the second tuple element is the row count
       which we don't use here).
    3. ``max_width`` when the terminal probe returns the stdlib
       "infinite" sentinel ``0`` (which happens when stdout is
       redirected or not attached to a tty).

    The result is clamped to ``[MIN_WIDTH, max_width]`` so tables
    stay within the documented cap and never collapse below a usable
    minimum on tiny terminals.
    """
    width: int | None = None
    columns_env = os.environ.get("COLUMNS")
    if columns_env:
        try:
            parsed = int(columns_env)
            if parsed > 0:
                width = parsed
        except ValueError:
            # Malformed COLUMNS env var: fall through to the tty probe
            # rather than crashing. A warning to stderr keeps operators
            # informed without leaking into stdout.
            _logger.warning(
                "arr_cli.facade.output: ignoring non-integer COLUMNS=%r",
                columns_env,
            )
    if width is None:
        try:
            width = shutil.get_terminal_size((DEFAULT_MAX_WIDTH, 20)).columns
        except (OSError, ValueError):  # pragma: no cover - defensive
            width = DEFAULT_MAX_WIDTH
    if not width or width <= 0:
        width = DEFAULT_MAX_WIDTH
    # Clamp to [MIN_WIDTH, max_width]. The clamp is inclusive on both
    # ends so a width of exactly ``MIN_WIDTH`` is honoured (just barely
    # usable) and ``max_width`` is the hard cap.
    if width < MIN_WIDTH:
        width = MIN_WIDTH
    if width > max_width:
        width = max_width
    return width


# ---------------------------------------------------------------------------
# Cell rendering helpers
# ---------------------------------------------------------------------------


def _stringify(value: Any) -> str:
    """Render an arbitrary value as a string for cell rendering.

    Scalars render with ``str``; ``None`` becomes the literal
    ``"<null>"`` so the column stays visibly populated when a JSON
    payload carries a null. Mappings and sequences are summarised
    with their length so nested structures never blow up the column
    width budget.
    """
    if value is None:
        return "<null>"
    if isinstance(value, bool):
        # ``bool`` is a subclass of ``int``; check it first so True/False
        # render correctly rather than as 1/0.
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    if isinstance(value, Mapping):
        return f"<{len(value)} keys>"
    if isinstance(value, (list, tuple, set, frozenset)):
        return f"<{len(value)} items>"
    return str(value)


def _truncate(value: str, width: int) -> str:
    """Truncate ``value`` to fit ``width`` chars, appending ``…`` when cut.

    Negative or zero widths are honoured by returning an empty
    string; values that already fit are returned verbatim. The
    trailing ``…`` (U+2026 HORIZONTAL ELLIPSIS) is the documented
    truncation marker (NFR-Usability: "long values SHALL be
    truncated with …").
    """
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width == 1:
        # One character slot — show only the ellipsis so the column
        # boundary stays visible.
        return "…"
    return value[: width - 1] + "…"


def _column_widths(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    budget: int,
) -> list[int]:
    """Compute the per-column width allocation.

    The total width budget is split evenly across ``len(headers)``
    columns (mirrors design.md: ``max_width // len(columns)``). Each
    column is then widened just enough to fit its longest rendered
    cell, capped at the per-column allocation so a single very long
    value cannot starve its neighbours.

    Returns a list with one entry per header.
    """
    n = len(headers)
    if n == 0:
        return []
    base = max(1, budget // n)
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row[:n]):
            if len(cell) > widths[index]:
                widths[index] = len(cell)
    return [min(base, max(headers_width, len(header)))
            for headers_width, header in zip(widths, headers)]


def _format_row(cells: Sequence[str], widths: Sequence[int]) -> str:
    """Render one row with cells padded to ``widths[i]``.

    Cells shorter than their allocation are padded with trailing
    spaces so columns align. Cells longer than their allocation are
    truncated via :func:`_truncate` so a single over-long value
    cannot break the table layout.
    """
    parts: list[str] = []
    for index, cell in enumerate(cells):
        width = widths[index] if index < len(widths) else 0
        truncated = _truncate(cell, width)
        if len(truncated) < width:
            truncated = truncated + " " * (width - len(truncated))
        parts.append(truncated)
    return "  ".join(parts)


# ---------------------------------------------------------------------------
# Human renderer
# ---------------------------------------------------------------------------


def _render_scalar(payload: Any) -> str:
    """Render a scalar payload with a one-line label (REQ-3 AC3)."""
    rendered = _stringify(payload)
    if "\n" in rendered:
        # Multiline content gets a header so the operator sees what
        # they are looking at; collapses newlines into a single line
        # for terminal friendliness.
        first_line, _, rest = rendered.partition("\n")
        if rest.strip():
            return f"value: {first_line}  ({len(rest.splitlines())} more lines)"
    return f"value: {rendered}"


def _render_object(payload: Mapping[str, Any]) -> str:
    """Render an object as vertical ``key: value`` pairs (REQ-3 AC3)."""
    if not payload:
        return "(empty object)"
    lines = ["{"]
    for key, value in payload.items():
        rendered = _stringify(value)
        # Wrap multi-line strings inline so the table stays single-line.
        if "\n" in rendered:
            rendered = rendered.replace("\n", " ⏎ ")
        lines.append(f"  {key}: {rendered}")
    lines.append("}")
    return "\n".join(lines)


def _coerce_list(payload: Sequence[Any]) -> list[Any]:
    """Return ``payload`` as a list of items (handle tuple/frozenset)."""
    if isinstance(payload, list):
        return payload
    return list(payload)


def _coerce_columns(
    payload: Sequence[Any],
    columns: Sequence[str] | None,
) -> list[str]:
    """Resolve the column list, inferring when not provided.

    For list-of-dict payloads the keys of the first item are used as
    column names. For list-of-list payloads positional ``c0``, ``c1``,
    ... names are used. For empty payloads ``columns`` (when provided)
    is honoured verbatim; otherwise an empty list is returned.
    """
    if columns:
        return list(columns)
    if not payload:
        return []
    first = payload[0]
    if isinstance(first, Mapping):
        return list(first.keys())
    return [f"c{i}" for i in range(len(first))]


def _row_from_mapping(
    item: Mapping[str, Any],
    columns: Sequence[str],
) -> list[str]:
    """Project a mapping onto the configured columns."""
    return [_stringify(item.get(column)) for column in columns]


def _row_from_sequence(
    item: Sequence[Any],
    columns: Sequence[str],
) -> list[str]:
    """Project a sequence onto the configured columns."""
    rendered = [_stringify(value) for value in item]
    # Pad short rows so the column alignment stays consistent.
    if len(rendered) < len(columns):
        rendered.extend([""] * (len(columns) - len(rendered)))
    return rendered[: len(columns)]


def human(
    payload: Any,
    columns: Sequence[str] | None = None,
    *,
    limit: int = DEFAULT_LIMIT,
    max_width: int = DEFAULT_MAX_WIDTH,
) -> str:
    """Render ``payload`` as a tabular human-readable string.

    Parameters
    ----------
    payload:
        Arbitrary JSON-decoded value. The renderer accepts:

        * ``list`` of ``dict`` — header row from the ``columns``
          argument (or first row's keys when ``columns`` is None);
          one row per item projected onto those keys.
        * ``list`` of ``list`` / tuple — header row from
          ``columns`` (or positional ``c0``/``c1``/... when None);
          one row per item projected positionally.
        * ``dict`` — vertical ``key: value`` pairs.
        * Scalars / ``None`` — one-line ``value: <rendered>`` label.
    columns:
        Explicit column ordering. When ``None`` the renderer infers
        column names from the payload (dict keys for mappings,
        positional for sequences).
    limit:
        Maximum number of rows to render (REQ-3 AC2). Items beyond
        the limit are summarised in a footer line so the operator
        knows pagination truncated the output. Must be >= 1.
    max_width:
        Hard upper bound on the rendered table width (defaults to
        the documented 120-column NFR cap). Width is also clamped
        to a minimum of 20 characters and to the ``COLUMNS`` env
        var when set; see :func:`resolve_width`.

    Returns
    -------
    str
        The rendered output (no trailing newline; ``emit`` decides
        whether to append one).
    """
    if limit < 1:
        raise ValueError(f"limit must be >= 1; got {limit}")

    width = resolve_width(max_width=max_width)

    if payload is None or isinstance(payload, (bool, int, float, str)):
        return _render_scalar(payload)

    if isinstance(payload, Mapping):
        return _render_object(payload)

    if isinstance(payload, (list, tuple, set, frozenset)):
        items = _coerce_list(payload)
        if not items:
            return "(empty list)"
        resolved_columns = _coerce_columns(items, columns)
        if not resolved_columns:
            # Sequence of scalars with no explicit columns: render
            # one per line with a positional header.
            return "\n".join(
                _stringify(item) for item in items[:limit]
            ) + (
                f"\n… ({len(items) - limit} more items)"
                if len(items) > limit
                else ""
            )

        # Project every item into a row of strings. Mixed payloads
        # (list of dicts AND list of lists) are tolerated by
        # dispatching on the first item's type; subsequent items
        # that don't match are coerced to ``str``.
        rows: list[list[str]] = []
        first = items[0]
        if isinstance(first, Mapping):
            for item in items[:limit]:
                if isinstance(item, Mapping):
                    rows.append(_row_from_mapping(item, resolved_columns))
                else:
                    rows.append(
                        _row_from_sequence(
                            [item], resolved_columns
                        )
                    )
        else:
            for item in items[:limit]:
                if isinstance(item, Mapping):
                    rows.append(_row_from_mapping(item, resolved_columns))
                else:
                    rows.append(_row_from_sequence(item, resolved_columns))

        per_column_widths = _column_widths(
            resolved_columns, rows, budget=width
        )
        lines = [
            _format_row(resolved_columns, per_column_widths),
            _format_row(
                ["-" * width_indicator for width_indicator in per_column_widths],
                per_column_widths,
            ),
        ]
        for row in rows:
            lines.append(_format_row(row, per_column_widths))

        truncated = len(items) - limit
        if truncated > 0:
            lines.append(
                f"… ({truncated} more item"
                f"{'s' if truncated != 1 else ''}; "
                f"use --limit to see more)"
            )

        return "\n".join(lines)

    # Fallback: payload shape we don't enumerate explicitly.
    return _stringify(payload)


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------


def emit(
    payload: Any,
    *,
    human_mode: bool,
    columns: Sequence[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    max_width: int = DEFAULT_MAX_WIDTH,
    stream: Any | None = None,
) -> None:
    """Write ``payload`` to stdout as JSON or human-readable text.

    Parameters
    ----------
    payload:
        Arbitrary JSON-decoded value (the verbatim service response).
    human_mode:
        When True the payload is rendered via :func:`human` and
        written as a multi-line table (REQ-3 AC2). When False the
        payload is serialised with :func:`json.dumps` and written as
        a single line of compact JSON (REQ-3 AC1); UTF-8 characters
        pass through verbatim thanks to ``ensure_ascii=False``
        (REQ-3 AC5).
    columns:
        Forwarded to :func:`human` when ``human_mode`` is True.
    limit:
        Forwarded to :func:`human` when ``human_mode`` is True.
    max_width:
        Forwarded to :func:`human` when ``human_mode`` is True.
    stream:
        Writable file-like object. Defaults to ``sys.stdout``. Kept
        explicit so tests can capture without monkey-patching
        ``sys.stdout``.

    Notes
    -----
    Diagnostics never leak into stdout (REQ-3 AC4). Warnings from
    :func:`resolve_width` flow through the module logger to stderr.

    The parameter is named ``human_mode`` (not ``human``) so the
    function name :func:`human` does not get shadowed inside the
    function body. Callers using ``--human`` continue to pass
    ``human_mode=True``; the argument naming is an implementation
    detail of the facade.
    """
    import sys  # local import keeps top-of-module cost minimal

    out = stream if stream is not None else sys.stdout

    if not human_mode:
        # JSON pass-through: compact (no indent), UTF-8 preserved.
        # The requirements explicitly state that the top-level
        # structure is the verbatim service payload, so we do NOT
        # wrap it in an envelope (REQ-3 AC1).
        text = json.dumps(payload, ensure_ascii=False)
        print(text, file=out)
        return

    rendered = human(
        payload,
        columns=columns,
        limit=limit,
        max_width=max_width,
    )
    print(rendered, file=out)
