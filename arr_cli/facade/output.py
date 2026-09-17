"""Output formatting for the arr-cli facade (task 6).

This module owns three responsibilities:

* :func:`emit` — single entry point used by every service CLI to write
  a payload to stdout. The dispatch chain (``human`` > ``verbose`` >
  default summary > verbatim JSON) selects between :func:`human`,
  the curated per-command summary, and the verbatim pass-through
  (REQ-3 AC1-AC5).
* :func:`human` — tabular renderer that handles list-of-dict,
  list-of-list, dict, and scalar payloads per design.md "Components
  and Interfaces / arr_facade.output".
* :func:`summarize` and :data:`_SUMMARY_RENDERERS` — per-command
  summary renderer registry. For the 15 size-to-summary candidate
  commands the registered renderer maps the verbatim payload to a
  curated, chat-agent-sized shape; for any other key (including the
  14 safe-to-leave-alone commands) :func:`summarize` returns the
  payload unchanged as a graceful default (REQ-1 AC4, REQ-5 AC5).

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
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "emit",
    "human",
    "resolve_width",
    "summarize",
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
    """Project a mapping onto the configured columns.

    For each column token the lookup tries ``item.get(column)`` first
    so flat-with-dots keys (e.g. ``UserData.PlaybackPositionTicks``,
    emitted as a literal top-level key by the jellyfin resume /
    recent summary renderers) resolve without needing a nested
    ``UserData`` mapping. When the flat lookup misses AND the token
    contains a ``.``, the fallback walks the dot-separated path so
    nested-summary tokens (e.g. ``playing.type``, ``movie.title``)
    still reach the nested field. Per REQ-16 AC2 the non-dotted
    branch is a strict subset of the pre-change flat-key lookup.
    """
    row: list[str] = []
    for column in columns:
        current: Any = item.get(column)
        if current is None and "." in column:
            current = item
            try:
                for seg in column.split("."):
                    if isinstance(current, Mapping):
                        current = current[seg]
                    elif isinstance(current, Sequence):
                        current = current[int(seg)]
                    else:
                        current = None
                        break
            except (KeyError, IndexError, TypeError):
                current = None
        row.append(_stringify(current))
    return row


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
# Safe-access helpers
# ---------------------------------------------------------------------------


def _safe_get(payload: Any, *path: Any, default: Any = None) -> Any:
    """Walk ``payload`` along ``path`` and return the resolved value.

    Returns ``default`` (which itself defaults to ``None``) on any
    ``KeyError``, ``IndexError``, ``TypeError``, or ``AttributeError``
    encountered along the walk -- this covers:

    * missing dict keys,
    * list / tuple indices out of range,
    * scalars / ``None`` intermediates where a subscript is attempted,
    * attribute access on a non-object value.

    The defensive no-raise contract is what guarantees that a malformed
    payload (e.g. an empty list, a missing nested field) produces a
    well-formed JSON value instead of a crash (REQ-1 AC4, REQ-5 AC5).

    Parameters
    ----------
    payload:
        The starting value (typically a dict decoded from JSON).
    *path:
        One or more lookup keys. Dict lookups (``payload[path[0]]``)
        are attempted first; when the current value is a ``Mapping``
        or ``list`` and the next segment is a string, dict lookup is
        used; otherwise sequence lookup via ``[int(segment)]``.
    default:
        Returned when the walk fails for any reason. Callers may
        pass ``0`` for int fields, ``False`` for bool fields, ``""``
        for string fields, etc., per the per-command summary spec.
    """
    current = payload
    for segment in path:
        if current is None:
            return default
        if isinstance(current, Mapping):
            try:
                current = current[segment]
                continue
            except (KeyError, TypeError):
                return default
        if isinstance(current, (list, tuple)):
            try:
                index = int(segment)
            except (TypeError, ValueError):
                return default
            try:
                current = current[index]
                continue
            except IndexError:
                return default
        return default
    return current if current is not None else default


def _safe_getattr(obj: Any, name: str, default: Any = None) -> Any:
    """Attribute-flavoured sibling of :func:`_safe_get`.

    Returns ``default`` on any ``AttributeError`` / ``TypeError``
    (e.g. when ``obj`` is ``None``). Mirrors the human-renderer
    helpers' tolerance for missing fields; service payloads are
    JSON-decoded so ``_safe_get`` is the common path, but this is
    here for parity.
    """
    if obj is None:
        return default
    try:
        return getattr(obj, name)
    except AttributeError:
        return default


# ---------------------------------------------------------------------------
# Per-command summary renderers
# ---------------------------------------------------------------------------


def _summary_jellyfin_now(payload: Any) -> list[dict[str, Any]]:
    """Render a Jellyfin ``/Sessions`` payload as the curated summary.

    Top-level shape: list of session objects (one per active
    session); ``[]`` when no sessions are active. ``playing``
    collapses to ``None`` when the session has no ``NowPlayingItem``
    (REQ-4 AC4).
    """
    if not isinstance(payload, list):
        return []
    sessions: list[dict[str, Any]] = []
    for session in payload:
        if not isinstance(session, Mapping):
            continue
        now_playing = session.get("NowPlayingItem")
        if isinstance(now_playing, Mapping):
            playing: dict[str, Any] | None = {
                "type": _safe_get(now_playing, "Type", default=None),
                "name": _safe_get(now_playing, "Name", default=None),
                "series": _safe_get(now_playing, "SeriesName", default=None),
                "season": _safe_get(now_playing, "ParentIndexNumber", default=0),
                "episode": _safe_get(now_playing, "IndexNumber", default=0),
            }
        else:
            playing = None
        play_state = session.get("PlayState")
        progress: dict[str, Any] = {
            "position_ticks": _safe_get(
                play_state, "PositionTicks", default=0
            ),
            "is_paused": _safe_get(
                play_state, "IsPaused", default=False
            ),
        }
        sessions.append(
            {
                "user": _safe_get(session, "UserName", default=None),
                "device": _safe_get(session, "DeviceName", default=None),
                "client": _safe_get(session, "Client", default=None),
                "playing": playing,
                "progress": progress,
            }
        )
    return sessions


def _summary_jellyfin_recent(payload: Any) -> list[dict[str, Any]]:
    """Render a Jellyfin ``recent`` payload as the curated summary."""
    if not isinstance(payload, list):
        return []
    return [
        {
            "Name": _safe_get(item, "Name", default=None),
            "Type": _safe_get(item, "Type", default=None),
            "ProductionYear": _safe_get(item, "ProductionYear", default=0),
            "SeriesName": _safe_get(item, "SeriesName", default=None),
            "UserData.LastPlayedDate": _safe_get(
                item, "UserData", "LastPlayedDate", default=None
            ),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_jellyfin_favorites(payload: Any) -> list[dict[str, Any]]:
    """Render a Jellyfin ``favorites`` payload as the curated summary."""
    if not isinstance(payload, list):
        return []
    return [
        {
            "Name": _safe_get(item, "Name", default=None),
            "Type": _safe_get(item, "Type", default=None),
            "ProductionYear": _safe_get(item, "ProductionYear", default=0),
            "SeriesName": _safe_get(item, "SeriesName", default=None),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_jellyfin_resume(payload: Any) -> list[dict[str, Any]]:
    """Render a Jellyfin ``resume`` payload as the curated summary."""
    if not isinstance(payload, list):
        return []
    return [
        {
            "Name": _safe_get(item, "Name", default=None),
            "Type": _safe_get(item, "Type", default=None),
            "ProductionYear": _safe_get(item, "ProductionYear", default=0),
            "SeriesName": _safe_get(item, "SeriesName", default=None),
            "UserData.PlaybackPositionTicks": _safe_get(
                item, "UserData", "PlaybackPositionTicks", default=0
            ),
            "UserData.PlayCount": _safe_get(
                item, "UserData", "PlayCount", default=0
            ),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_jellyfin_latest(payload: Any) -> list[dict[str, Any]]:
    """Render a Jellyfin ``latest`` payload as the curated summary."""
    if not isinstance(payload, list):
        return []
    return [
        {
            "Name": _safe_get(item, "Name", default=None),
            "Type": _safe_get(item, "Type", default=None),
            "ProductionYear": _safe_get(item, "ProductionYear", default=0),
            "SeriesName": _safe_get(item, "SeriesName", default=None),
            "DateCreated": _safe_get(item, "DateCreated", default=None),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_radarr_wanted(payload: Any) -> list[dict[str, Any]]:
    """Render a Radarr ``wanted`` payload as the curated summary."""
    if not isinstance(payload, list):
        return []
    return [
        {
            "title": _safe_get(item, "title", default=None),
            "year": _safe_get(item, "year", default=0),
            "tmdbId": _safe_get(item, "tmdbId", default=0),
            "monitored": _safe_get(item, "monitored", default=False),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_radarr_queue(payload: Any) -> list[dict[str, Any]]:
    """Render a Radarr ``queue`` payload as the curated summary."""
    if not isinstance(payload, list):
        return []
    return [
        {
            "title": _safe_get(item, "title", default=None),
            "status": _safe_get(item, "status", default=None),
            "trackedDownloadStatus": _safe_get(
                item, "trackedDownloadStatus", default=None
            ),
            "size": _safe_get(item, "size", default=0),
            "sizeleft": _safe_get(item, "sizeleft", default=0),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_radarr_recent(payload: Any) -> list[dict[str, Any]]:
    """Render a Radarr ``recent`` payload as the curated summary."""
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        movie = item.get("movie")
        if isinstance(movie, Mapping):
            movie_obj: dict[str, Any] = {
                "title": _safe_get(movie, "title", default=None),
                "year": _safe_get(movie, "year", default=0),
            }
        else:
            movie_obj = {"title": None, "year": 0}
        summaries.append(
            {
                "movie": movie_obj,
                "eventType": _safe_get(item, "eventType", default=None),
                "date": _safe_get(item, "date", default=None),
            }
        )
    return summaries


def _summary_sonarr_wanted(payload: Any) -> list[dict[str, Any]]:
    """Render a Sonarr ``wanted`` payload as the curated summary."""
    if not isinstance(payload, list):
        return []
    return [
        {
            "title": _safe_get(item, "title", default=None),
            "seasonNumber": _safe_get(item, "seasonNumber", default=0),
            "episodeNumber": _safe_get(item, "episodeNumber", default=0),
            "airDate": _safe_get(item, "airDate", default=None),
            "monitored": _safe_get(item, "monitored", default=False),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_sonarr_queue(payload: Any) -> list[dict[str, Any]]:
    """Render a Sonarr ``queue`` payload as the curated summary."""
    if not isinstance(payload, list):
        return []
    return [
        {
            "title": _safe_get(item, "title", default=None),
            "status": _safe_get(item, "status", default=None),
            "trackedDownloadStatus": _safe_get(
                item, "trackedDownloadStatus", default=None
            ),
            "size": _safe_get(item, "size", default=0),
            "sizeleft": _safe_get(item, "sizeleft", default=0),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_sonarr_recent(payload: Any) -> list[dict[str, Any]]:
    """Render a Sonarr ``recent`` payload as the curated summary."""
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        series = item.get("series")
        series_obj: dict[str, Any] = (
            {"title": _safe_get(series, "title", default=None)}
            if isinstance(series, Mapping)
            else {"title": None}
        )
        episode = item.get("episode")
        episode_obj: dict[str, Any] = (
            {"title": _safe_get(episode, "title", default=None)}
            if isinstance(episode, Mapping)
            else {"title": None}
        )
        summaries.append(
            {
                "series": series_obj,
                "episode": episode_obj,
                "eventType": _safe_get(item, "eventType", default=None),
                "date": _safe_get(item, "date", default=None),
            }
        )
    return summaries


def _summary_seerr_requests(payload: Any) -> list[dict[str, Any]]:
    """Render a Seerr ``requests`` payload as the curated summary.

    ``GET /api/v1/request`` returns a paginated envelope of the shape
    ``{pageInfo: {pages, pageSize, results, page}, results: [...],
    serviceErrors: {...}}``; iterate ``results`` so the default summary
    is non-empty when the envelope is well-formed. A bare list is
    unchanged behaviour.
    """
    if isinstance(payload, Mapping):
        payload = payload.get("results")
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        requester = item.get("requestedBy")
        if isinstance(requester, Mapping):
            requester_obj: dict[str, Any] = {
                "displayName": _safe_get(
                    requester, "displayName", default=None
                ),
            }
        else:
            requester_obj = {"displayName": None}
        summaries.append(
            {
                "title": _safe_get(item, "title", default=None),
                "type": _safe_get(item, "type", default=None),
                "status": _safe_get(item, "status", default=None),
                "createdAt": _safe_get(item, "createdAt", default=None),
                "requestedBy": requester_obj,
            }
        )
    return summaries


def _summary_seerr_search(payload: Any) -> list[dict[str, Any]]:
    """Render a Seerr ``search`` payload as the curated summary.

    ``GET /api/v1/search`` returns a paginated envelope of the shape
    ``{page, totalPages, totalResults, results: [...]}``; iterate
    ``results`` so the default summary is non-empty when the envelope
    is well-formed. A bare list is unchanged behaviour.
    """
    if isinstance(payload, Mapping):
        payload = payload.get("results")
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        media_info = item.get("mediaInfo")
        if isinstance(media_info, Mapping):
            media_info_obj: dict[str, Any] = {
                "tmdbId": _safe_get(media_info, "tmdbId", default=0),
            }
        else:
            media_info_obj = {"tmdbId": 0}
        summaries.append(
            {
                "title": _safe_get(item, "title", default=None),
                "mediaType": _safe_get(item, "mediaType", default=None),
                "releaseDate": _safe_get(
                    item, "releaseDate", default=None
                ),
                "mediaInfo": media_info_obj,
            }
        )
    return summaries


def _summary_seerr_available(payload: Any) -> list[dict[str, Any]]:
    """Render a Seerr ``available`` payload as the curated summary.

    ``GET /api/v1/media`` returns a paginated envelope of the shape
    ``{pageInfo: {pages, pageSize, results, page}, results: [...],
    serviceErrors: {...}}``; iterate ``results`` so the default
    summary is non-empty when the envelope is well-formed. A bare
    list is unchanged behaviour (used after the handler's
    client-side title-substring filter narrows the response).
    """
    if isinstance(payload, Mapping):
        payload = payload.get("results")
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        media_info = item.get("mediaInfo")
        if isinstance(media_info, Mapping):
            media_info_obj: dict[str, Any] = {
                "status": _safe_get(media_info, "status", default=0),
            }
        else:
            media_info_obj = {"status": 0}
        summaries.append(
            {
                "title": _safe_get(item, "title", default=None),
                "mediaType": _safe_get(item, "mediaType", default=None),
                "releaseDate": _safe_get(
                    item, "releaseDate", default=None
                ),
                "mediaInfo": media_info_obj,
            }
        )
    return summaries


def _summary_seerr_trending(payload: Any) -> list[dict[str, Any]]:
    """Render a Seerr ``trending`` payload as the curated summary.

    ``GET /api/v1/discover/trending`` returns a paginated envelope of
    the shape ``{page, totalPages, totalResults, results: [...]}``;
    iterate ``results`` so the default summary is non-empty when the
    envelope is well-formed. A bare list is unchanged behaviour
    (defensive against envelope-drift across Seer versions).

    The per-item projection is intentionally identical to
    :func:`_summary_seerr_search` because both endpoints share the
    same ``TrendingItem`` / ``SearchResult`` shape (``title``,
    ``mediaType``, ``releaseDate``, ``mediaInfo.tmdbId``). Keeping
    the projections byte-identical means the default summary for
    ``seerr search`` and ``seerr trending`` lines up row-for-row
    when both are tabulated together.
    """
    if isinstance(payload, Mapping):
        payload = payload.get("results")
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        media_info = item.get("mediaInfo")
        if isinstance(media_info, Mapping):
            media_info_obj: dict[str, Any] = {
                "tmdbId": _safe_get(media_info, "tmdbId", default=0),
            }
        else:
            media_info_obj = {"tmdbId": 0}
        summaries.append(
            {
                "title": _safe_get(item, "title", default=None),
                "mediaType": _safe_get(item, "mediaType", default=None),
                "releaseDate": _safe_get(
                    item, "releaseDate", default=None
                ),
                "mediaInfo": media_info_obj,
            }
        )
    return summaries


def _summary_seerr_tv(payload: Any) -> dict[str, Any]:
    """Render a Seerr ``tv <id>`` payload as the curated summary.

    The detail endpoint ``GET /api/v1/tv/{tvId}`` returns a single
    object with the show's metadata (name, originalName,
    firstAirDate, ``genres[]``, ``networks[]``, ``seasons[]``,
    ``numberOfSeasons``, ``status``, createdBy, episodeRunTime, ...).
    The curated shape flattens the nested ``genres`` and
    ``networks`` arrays to a single comma-joined string per field
    so the default ``--human`` rendering stays a readable block per
    key (otherwise each cell would render as ``<N items>``).

    When ``cmd_tv`` is invoked with ``--ratings``, the RT critic
    and audience scores arrive under ``payload["ratings"]`` and are
    surfaced as ``ratings.criticsScore`` / ``ratings.audienceScore``.
    A bare detail fetch (no ``--ratings``) leaves ``ratings`` as
    ``None`` so the column is visible-but-empty in the default
    output instead of being silently dropped.

    Two input shapes are accepted (mirroring the
    size-to-summary-renderer convention):

    * A single ``Mapping`` (the CLI-layer payload after the
      handler's optional ratings merge) -- the canonical shape for
      ``cmd_tv``.
    * A list containing a single ``Mapping`` (the test fixture
      convention; every other size-to-summary candidate stores its
      payload as ``[single_dict]`` so the human-renderer unit
      tests can iterate ``payload[0]``). Empty list maps to ``{}``
      and ``None`` / scalar payloads map to ``{}`` so neither crashes.
    """

    if isinstance(payload, list):
        if not payload:
            return {}
        payload = payload[0]
    if not isinstance(payload, Mapping):
        return {}

    def _join_names(items: Any) -> str | None:
        """Flatten ``items`` to a ``", "-joined`` string of names.

        Honours both ``[{"name": "Foo"}, ...]`` (the Seer detail
        shape for ``genres`` / ``networks``) and ``["Foo", ...]``.
        Returns ``None`` when ``items`` is not a list or yields no
        string names, so the renderer prints ``<null>`` instead of
        ``", "`` for empty collections.
        """
        if not isinstance(items, list):
            return None
        names: list[str] = []
        for item in items:
            if isinstance(item, Mapping):
                name = item.get("name")
                if isinstance(name, str):
                    names.append(name)
            elif isinstance(item, str):
                names.append(item)
        return ", ".join(names) if names else None

    raw_genres = payload.get("genres")
    raw_networks = payload.get("networks")
    raw_ratings = payload.get("ratings")
    if isinstance(raw_ratings, Mapping):
        ratings_obj: dict[str, Any] = {
            "criticsScore": _safe_get(
                raw_ratings, "criticsScore", default=None
            ),
            "audienceScore": _safe_get(
                raw_ratings, "audienceScore", default=None
            ),
        }
    else:
        ratings_obj = None

    return {
        "name": _safe_get(payload, "name", default=None),
        "originalName": _safe_get(payload, "originalName", default=None),
        "firstAirDate": _safe_get(payload, "firstAirDate", default=None),
        "genres": _join_names(raw_genres),
        "networks": _join_names(raw_networks),
        "numberOfSeasons": _safe_get(
            payload, "numberOfSeasons", default=0
        ),
        "status": _safe_get(payload, "status", default=None),
        "ratings": ratings_obj,
    }


def _summary_seerr_movie(payload: Any) -> dict[str, Any]:
    """Render a Seerr ``movie <id>`` payload as the curated summary.

    The detail endpoint ``GET /api/v1/movie/{movieId}`` returns a
    single object with the movie's metadata (name, originalTitle,
    releaseDate, ``runtime`` (raw minutes), ``genres[]``, tagline,
    overview, cast, ...). The curated shape flattens the nested
    ``genres`` array to a single comma-joined string so the
    default ``--human`` rendering stays a readable block per key
    (otherwise each cell would render as ``<N items>``). The raw
    ``runtime`` integer (minutes) is reformatted to
    ``"<X>h <Y>m"`` -- the spec'd display format which matters
    because raw minutes is not human-readable.

    When ``cmd_movie`` is invoked with ``--ratings``, the RT
    critic and audience scores arrive under
    ``payload["ratings"]`` and are surfaced as
    ``ratings.criticsScore`` / ``ratings.audienceScore``. A bare
    detail fetch (no ``--ratings``) leaves ``ratings`` as
    ``None`` so the column is visible-but-empty in the default
    output instead of being silently dropped.

    Two input shapes are accepted (mirroring the
    size-to-summary-renderer convention):

    * A single ``Mapping`` (the CLI-layer payload after the
      handler's optional ratings merge) -- the canonical shape
      for ``cmd_movie``.
    * A list containing a single ``Mapping`` (the test fixture
      convention; every other size-to-summary candidate stores
      its payload as ``[single_dict]`` so the human-renderer unit
      tests can iterate ``payload[0]``). Empty list maps to
      ``{}`` and ``None`` / scalar payloads map to ``{}`` so
      neither crashes.
    """

    if isinstance(payload, list):
        if not payload:
            return {}
        payload = payload[0]
    if not isinstance(payload, Mapping):
        return {}

    def _join_names(items: Any) -> str | None:
        """Flatten ``items`` to a ``", "-joined`` string of names.

        Honours both ``[{"name": "Foo"}, ...]`` (the Seer detail
        shape for ``genres``) and ``["Foo", ...]``. Returns
        ``None`` when ``items`` is not a list or yields no string
        names, so the renderer prints ``<null>`` instead of
        ``", "`` for empty collections.
        """
        if not isinstance(items, list):
            return None
        names: list[str] = []
        for item in items:
            if isinstance(item, Mapping):
                name = item.get("name")
                if isinstance(name, str):
                    names.append(name)
            elif isinstance(item, str):
                names.append(item)
        return ", ".join(names) if names else None

    def _format_runtime(minutes: Any) -> str | None:
        """Format ``minutes`` as ``"<X>h <Y>m"``.

        Honours the spec'd display format. Returns ``None`` when
        ``minutes`` is missing or zero so the renderer prints
        ``<null>`` instead of ``"0h 0m"`` (which would be a
        misleading "the movie has zero runtime" cell).
        """
        if not isinstance(minutes, (int, float)) or isinstance(minutes, bool):
            return None
        if minutes <= 0:
            return None
        total = int(minutes)
        hours = total // 60
        mins = total % 60
        return f"{hours}h {mins}m"

    raw_genres = payload.get("genres")
    raw_ratings = payload.get("ratings")
    if isinstance(raw_ratings, Mapping):
        ratings_obj: dict[str, Any] = {
            "criticsScore": _safe_get(
                raw_ratings, "criticsScore", default=None
            ),
            "audienceScore": _safe_get(
                raw_ratings, "audienceScore", default=None
            ),
        }
    else:
        ratings_obj = None

    return {
        "name": _safe_get(payload, "name", default=None),
        "originalTitle": _safe_get(
            payload, "originalTitle", default=None
        ),
        "releaseDate": _safe_get(payload, "releaseDate", default=None),
        "runtime": _format_runtime(payload.get("runtime")),
        "genres": _join_names(raw_genres),
        "tagline": _safe_get(payload, "tagline", default=None),
        "ratings": ratings_obj,
    }


def _summary_maintainerr_pending(payload: Any) -> list[dict[str, Any]]:
    """Render a Maintainerr ``pending`` payload as the curated summary."""
    if not isinstance(payload, list):
        return []
    return [
        {
            "title": _safe_get(item, "title", default=None),
            "mediaCount": _safe_get(item, "mediaCount", default=0),
            "deleteAfterDays": _safe_get(
                item, "deleteAfterDays", default=0
            ),
            "isOnHold": _safe_get(item, "isOnHold", default=False),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


# ---------------------------------------------------------------------------
# Renderer dispatch table
# ---------------------------------------------------------------------------


# Add a new size-to-summary candidate by appending one
# ``_summary_<service>_<command>`` function above and one entry here
# (REQ-3 AC6). The 14 safe-to-leave-alone commands are intentionally
# absent; their ``_emit`` calls still pass ``service`` / ``command``
# but the lookup misses and ``emit`` falls through to the default
# verbatim pass-through (REQ-5 AC3). Misses -- including the empty
# ``("", "")`` key and any unknown ``(service, command)`` pair -- are
# the documented graceful default of :func:`summarize`, which returns
# the payload unchanged rather than raising ``KeyError`` (REQ-1 AC4,
# REQ-5 AC5).
_SUMMARY_RENDERERS: dict[tuple[str, str], Callable[[Any], Any]] = {
    ("jellyfin", "now"): _summary_jellyfin_now,
    ("jellyfin", "recent"): _summary_jellyfin_recent,
    ("jellyfin", "favorites"): _summary_jellyfin_favorites,
    ("jellyfin", "resume"): _summary_jellyfin_resume,
    ("jellyfin", "latest"): _summary_jellyfin_latest,
    ("radarr", "wanted"): _summary_radarr_wanted,
    ("radarr", "queue"): _summary_radarr_queue,
    ("radarr", "recent"): _summary_radarr_recent,
    ("sonarr", "wanted"): _summary_sonarr_wanted,
    ("sonarr", "queue"): _summary_sonarr_queue,
    ("sonarr", "recent"): _summary_sonarr_recent,
    ("seerr", "requests"): _summary_seerr_requests,
    ("seerr", "search"): _summary_seerr_search,
    ("seerr", "available"): _summary_seerr_available,
    ("seerr", "tv"): _summary_seerr_tv,
    ("seerr", "movie"): _summary_seerr_movie,
    ("seerr", "trending"): _summary_seerr_trending,
    ("maintainerr", "pending"): _summary_maintainerr_pending,
}


def summarize(service: str, command: str, payload: Any) -> Any:
    """Apply the per-command summary renderer to ``payload``.

    Looks up ``(service, command)`` in :data:`_SUMMARY_RENDERERS`;
    returns the renderer's output when found, otherwise returns
    ``payload`` unchanged (graceful default). The empty key
    ``("", "")`` is not in the table, so a caller that does not
    populate both fields also observes the graceful default.

    This function is pure: no I/O, no logging, no ``print`` (REQ-5
    AC5). Adding a new size-to-summary candidate is a one-line
    registration in :data:`_SUMMARY_RENDERERS` plus the
    renderer function (REQ-3 AC6).

    Parameters
    ----------
    service:
        The per-service identifier (``"jellyfin"``, ``"radarr"``,
        ``"sonarr"``, ``"maintainerr"``, ``"seerr"``).
    command:
        The subcommand name (``"now"``, ``"wanted"``, ...).
    payload:
        The verbatim service response (already JSON-decoded).

    Returns
    -------
    Any
        Either the curated summary shape (a JSON-serializable
        structure) or ``payload`` unchanged when no renderer is
        registered for the key.
    """
    key = (service, command)
    renderer = _SUMMARY_RENDERERS.get(key)
    if renderer is None:
        return payload
    return renderer(payload)


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------


def emit(
    payload: Any,
    *,
    human_mode: bool,
    verbose_mode: bool = False,
    service: str = "",
    command: str = "",
    columns: Sequence[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    max_width: int = DEFAULT_MAX_WIDTH,
    stream: Any | None = None,
) -> None:
    """Write ``payload`` to stdout.

    Priority chain (REQ-3 AC1-AC4):

    1. ``human_mode`` -- render via :func:`human` over the summary
       shape (the same shape the no-flag default emits, courtesy of
       :func:`summarize`); ``--verbose`` together with ``--human``
       bypasses :func:`summarize` and renders the verbatim payload.
    2. ``verbose_mode`` -- emit verbatim JSON.
    3. ``service`` and ``command`` both non-empty and the
       ``(service, command)`` key is registered in
       :data:`_SUMMARY_RENDERERS` -- emit the curated summary.
    4. Otherwise -- emit verbatim JSON (the pre-change default).

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
    verbose_mode:
        When True (and ``human_mode`` is False) the verbatim service
        payload is emitted on stdout (REQ-2 AC1). When ``human_mode``
        is also True, ``verbose_mode`` keeps its effect: the
        verbatim payload is rendered as a table and
        :func:`summarize` is bypassed; verbose wins for the data
        shape, ``human_mode`` wins for the rendering format
        (REQ-3 AC1, REQ-4 AC3).
    service:
        Per-service identifier used for the renderer dispatch table
        lookup. Defaults to ``""`` so callers that do not thread
        this value continue to observe the verbatim default
        (REQ-5 AC3).
    command:
        Subcommand name used for the renderer dispatch table
        lookup. Same empty-string default as ``service``.
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

    if human_mode:
        if verbose_mode:
            shaped = payload
        else:
            shaped = summarize(service, command, payload)
        rendered = human(
            shaped,
            columns=columns,
            limit=limit,
            max_width=max_width,
        )
        print(rendered, file=out)
        return

    if verbose_mode:
        # Verbatim pass-through: same call as the default branch,
        # kept separate so the dispatch order is auditable in source.
        print(json.dumps(payload, ensure_ascii=False), file=out)
        return

    if service and command and (service, command) in _SUMMARY_RENDERERS:
        rendered_summary = summarize(service, command, payload)
        print(json.dumps(rendered_summary, ensure_ascii=False), file=out)
        return

    # Default verbatim pass-through: byte-identical to the pre-change
    # behaviour for any caller that does not pass ``service`` /
    # ``command`` (or whose command is not a size-to-summary
    # candidate) -- REQ-5 AC3 / NFR-Reliability.
    print(json.dumps(payload, ensure_ascii=False), file=out)
