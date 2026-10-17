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


def _truncate_overview(value: Any, *, max_chars: int = 80) -> str | None:
    """Truncate a free-text ``overview`` to a fixed character budget.

    Used by the two ``overview`` projections on Radarr (``lookup`` and
    ``movie <id>``) so the cell stays readable inside the 120-char
    NFR-Usability width cap when joined with the surrounding columns.
    The truncation marker matches :func:`_truncate` (U+2026
    HORIZONTAL ELLIPSIS) for visual consistency across the table.

    Returns ``None`` for non-string / missing / empty inputs so the
    human renderer prints ``<null>`` instead of the literal ``""`` or
    a stack trace — the documented safe-default contract for missing
    summary fields.
    """
    if not isinstance(value, str) or not value:
        return None
    if len(value) <= max_chars:
        return value
    if max_chars <= 1:
        return "…"
    return value[: max_chars - 1] + "…"


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


def _unwrap_envelope(payload: Any) -> list[Any]:
    """Unwrap a paginated service envelope into the underlying list.

    The size-to-summary renderers see three input shapes:

    * a bare list (the historical contract pre-PR-#6) — returned
      as-is;
    * a Jellyfin envelope of the shape
      ``{Items: [...], TotalRecordCount: N, StartIndex: 0}``;
    * a Radarr / Sonarr envelope of the shape
      ``{page, pageSize, totalRecords, records: [...]}``.

    The helper probes ``Items`` first (Jellyfin) and falls back to
    ``records`` (Radarr / Sonarr); a ``Mapping`` payload that
    matches neither shape logs a warning via the module logger and
    returns ``[]`` so the caller still receives a defensive empty
    list rather than crashing or surfacing the raw envelope.

    Row-budget policy: this helper is a pure unwrap and does **not**
    cap the result to ``TotalRecordCount`` / ``totalRecords``. The
    ``totalRecords`` field is informational metadata about pages
    that were never fetched (the upstream paginated endpoints
    return one page per request and ``arr-cli`` does not fan out
    across pages). Row-budget truncation lives in :func:`human`'s
    ``limit`` kwarg, which already truncates with the documented
    pagination footer. A future refactor that adds a
    ``totalRecords`` cap here must also add the pagination fan-out
    to honour the cap; doing only the cap would lie about the row
    count without fetching the rows.
    """
    if isinstance(payload, Mapping):
        for key in ("Items", "records"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return candidate
        keys = list(payload.keys())
        if keys:
            _logger.warning(
                "arr_cli.facade.output: envelope shape not recognised; "
                "expected `Items` (Jellyfin) or `records` (Radarr/Sonarr); "
                "got keys=%r",
                keys,
            )
        return []
    if isinstance(payload, list):
        return payload
    return []


# ---------------------------------------------------------------------------
# Summary-renderer shared helpers
# ---------------------------------------------------------------------------


def _join_names(items: Any) -> str | None:
    """Flatten a list of mappings-with-"name" or raw strings to a comma-joined string.

    Honours both ``[{"name": "Foo"}, ...]`` (the Seer detail shape for
    ``genres`` / ``networks`` and the Jellyfin ``Genres`` shape) and
    bare strings (``["Foo", ...]``). Returns ``None`` when ``items``
    is not a list or yields no string names, so the renderer prints
    ``<null>`` instead of a bare ``", "`` for empty collections.

    Lifted out of the per-renderer duplication in
    :func:`_summary_seerr_tv` / :func:`_summary_seerr_movie` so the four
    affected renderers (seerr TV / movie, jellyfin latest, jellyfin
    item) share one body — guards against a future drift where one
    renderer gains a bug the others don't.
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


def _format_runtime_minutes(value: Any) -> str | None:
    """Format Jellyfin ``RunTimeTicks`` (100-ns ticks) as ``"<X>h <Y>m"``.

    Jellyfin documents ``RunTimeTicks`` as "the total runtime in
    100-ns ticks" — i.e. ``10_000_000`` ticks per second. The
    formatter divides by ``10_000_000`` for seconds, then splits the
    result into whole hours + whole minutes. Returns ``None`` for
    missing / zero / negative / non-numeric / ``bool`` inputs so the
    renderer prints ``<null>`` instead of the misleading
    ``"0h 0m"``. ``bool`` is checked first because ``bool`` is a
    subclass of ``int`` in Python — without the guard, ``True`` would
    format as ``"0h 0m"``.

    Shared across US-2 (resume), US-4 (nextup), US-5 (latest),
    US-6 (favorites), and US-8 (item); lifted out of the per-renderer
    duplication so all five runtimes format identically.

    Do **not** feed Radarr whole-minute ``runtime`` into this helper;
    use :func:`_format_runtime_minutes_whole` instead — see that
    function's docstring for the rationale.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    total_seconds = int(value) // 10_000_000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def _format_runtime_minutes_whole(value: Any) -> str | None:
    """Format a whole-minute runtime as ``"<X>h <Y>m"``.

    Used for the Radarr ``calendar`` ``runtime`` projection (US-1):
    Radarr's upstream ``/api/v3/calendar`` rows carry ``runtime``
    as a whole-minute integer (e.g. ``120`` for a 2-hour movie),
    not as Jellyfin-style 100-ns ``RunTimeTicks``. Feeding such a
    value into the sibling :func:`_format_runtime_minutes` helper
    would yield ``None`` for any runtime under ~120 minutes (the
    helper divides by ``10_000_000`` to convert ticks into seconds)
    and ``0h <low>m``-class garbage for normal runtimes.

    The body mirrors the inline ``_format_runtime`` helper inside
    :func:`_summary_seerr_movie` (which has identical semantics on
    Seer's whole-minute ``runtime`` field); lifting it into a
    module-level helper makes the contract explicit and prevents a
    future reader from confusing the two formatters. Both
    definitions carry a code-comment cross-reference naming the
    other.

    Returns ``None`` for missing / zero / negative / non-numeric /
    ``bool`` inputs (matching the safety contract of
    :func:`_format_runtime_minutes`); the human renderer then prints
    ``<null>`` instead of the misleading ``"0h 0m"``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    total = int(value)
    hours = total // 60
    mins = total % 60
    return f"{hours}h {mins}m"


def _format_episode_progress(item: Any) -> str | None:
    """Format ``statistics.episodeFileCount / statistics.episodeCount`` as ``"<file>/<total>"``.

    Used by the Sonarr ``series`` and ``series/lookup`` ``statistics``
    projections so the operator can scan a library row and see, at a
    glance, how many episodes have been imported out of the total
    (the audit-asked cell). Sonarr's upstream ``statistics``
    envelope is a nested ``Mapping``; the helper walks it
    defensively so a future Sonarr release that omits either count
    (e.g. a ``/series/lookup`` candidate row that did not finish
    linking) renders ``<null>`` instead of a misleading ``"0/0"`` or
    ``"0/undefined"``.

    The defensive contract mirrors :func:`_format_runtime_minutes_whole`:
    ``bool`` is checked first because ``bool`` is a subclass of
    ``int`` in Python -- without the guard, ``True`` would format as
    ``"1/1"`` (or similar) on payloads that omit the field and
    default to ``False``. Non-numeric / negative / missing
    components return ``None``. A non-Mapping ``item`` (e.g. a
    scalar) also returns ``None``; the caller projects ``None`` and
    the human renderer prints ``<null>``.
    """
    if not isinstance(item, Mapping):
        return None
    file_count = item.get("statistics")
    if not isinstance(file_count, Mapping):
        return None
    file_count = file_count.get("episodeFileCount")
    total_count = item["statistics"].get("episodeCount")
    for count in (file_count, total_count):
        if isinstance(count, bool) or not isinstance(count, (int, float)):
            return None
        if count < 0:
            return None
    return f"{int(file_count)}/{int(total_count)}"


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
        # Per US-1: surface last-activity timestamp, queue length, and
        # an active-transcoding boolean so the --human table can tell
        # live vs stalled sessions at a glance.
        last_activity = session.get("LastActivityDate")
        queue = session.get("NowPlayingQueue")
        queue_length = len(queue) if isinstance(queue, list) else 0
        transcode_info = session.get("TranscodingInfo")
        if isinstance(transcode_info, Mapping):
            transcoding = _safe_get(
                transcode_info, "IsVideoDirect", default=True
            ) is False
        else:
            transcoding = False
        sessions.append(
            {
                "user": _safe_get(session, "UserName", default=None),
                "device": _safe_get(session, "DeviceName", default=None),
                "client": _safe_get(session, "Client", default=None),
                "playing": playing,
                "progress": progress,
                "LastActivityDate": last_activity,
                "NowPlayingQueue.length": queue_length,
                "transcoding": transcoding,
            }
        )
    return sessions


def _summary_jellyfin_recent(payload: Any) -> list[dict[str, Any]]:
    """Render a Jellyfin ``recent`` payload as the curated summary.

    The ``jellyfin recent`` command requests
    ``GET /Users/{user_id}/Items?SortBy=DatePlayed&Filters=IsPlayed&includeItemTypes=Movie,Episode``
    (see :func:`arr_cli.jellyfin.cmd_recent`). The ``includeItemTypes``
    query parameter is the upstream contract that constrains the
    envelope's ``Items`` array to played Movies and Episodes -- a
    user-facing "recently watched" list. When the upstream filter is
    not honoured (or is mis-applied) the envelope can carry library
    views (``Type=Folder``) and collections (``Type=BoxSet``) on top
    of the real played items, which previously leaked into the
    curated summary as spurious rows.

    To honour the contract defensively, the renderer re-applies the
    type filter locally: only items whose ``Type`` is one of
    ``{"Movie", "Episode"}`` are projected. Items with a missing or
    unexpected ``Type`` are dropped, so a future Jellyfin API drift
    that introduces another item type (or returns the wrong type for
    an existing kind) cannot inflate the ``recent`` summary. The
    filter is intentionally narrow -- the operator-facing semantic of
    "recently watched" is played Movies and Episodes, and anything
    else (Folder, BoxSet, Series, Video, MusicVideo, ...) is not a
    valid row for that command.
    """
    payload = _unwrap_envelope(payload)
    if not isinstance(payload, list):
        return []
    allowed_types = {"Movie", "Episode"}
    return [
        {
            "Name": _safe_get(item, "Name", default=None),
            "Type": _safe_get(item, "Type", default=None),
            "ProductionYear": _safe_get(item, "ProductionYear", default=0),
            "SeriesName": _safe_get(item, "SeriesName", default=None),
            "UserData.LastPlayedDate": _safe_get(
                item, "UserData", "LastPlayedDate", default=None
            ),
            "UserData.PlayedPercentage": _safe_get(
                item, "UserData", "PlayedPercentage", default=0
            ),
        }
        for item in payload
        if isinstance(item, Mapping)
        and _safe_get(item, "Type", default=None) in allowed_types
    ]


def _summary_jellyfin_favorites(payload: Any) -> list[dict[str, Any]]:
    """Render a Jellyfin ``favorites`` payload as the curated summary.

    ``GET /Users/{userId}/Items?Filters=IsFavorite`` returns a paginated
    envelope of the shape ``{Items: [...], TotalRecordCount: N, StartIndex: 0}``;
    iterate ``Items`` so the default summary is non-empty when the envelope
    is well-formed. A bare list is unchanged behaviour.
    """
    if isinstance(payload, Mapping):
        payload = payload.get("Items")
    if not isinstance(payload, list):
        return []
    return [
        {
            "Id": _safe_get(item, "Id", default=None),
            "Name": _safe_get(item, "Name", default=None),
            "Type": _safe_get(item, "Type", default=None),
            "ProductionYear": _safe_get(item, "ProductionYear", default=0),
            "SeriesName": _safe_get(item, "SeriesName", default=None),
            "RunTimeTicks": _format_runtime_minutes(
                item.get("RunTimeTicks")
            ),
            "UserData.LastPlayedDate": _safe_get(
                item, "UserData", "LastPlayedDate", default=None
            ),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_jellyfin_resume(payload: Any) -> list[dict[str, Any]]:
    """Render a Jellyfin ``resume`` payload as the curated summary.

    The ``jellyfin resume`` command requests
    ``GET /Users/{user_id}/Items/Resume``
    (see :func:`arr_cli.jellyfin.cmd_resume`). The resumable
    contract is the user-facing "continue watching" list -- direct
    Movie and Episode items where ``UserData.PlaybackPositionTicks``
    is set. On some Jellyfin versions the endpoint also includes
    parent ``Type=Series`` and ``Type=Season`` roll-up entries that
    aggregate resumable state for a container rather than carry
    their own ``UserData.PlaybackPositionTicks``; those roll-ups
    are not themselves resumable and previously leaked into the
    curated summary as spurious rows (the operator reported
    ``12 rows where API returns 3``).

    To honour the contract defensively, the renderer re-applies the
    type filter locally: only items whose ``Type`` is one of
    ``{"Movie", "Episode"}`` are projected. Items with a missing or
    unexpected ``Type`` are dropped, so a future Jellyfin API drift
    that introduces another item type (or returns the wrong type
    for an existing kind) cannot inflate the ``resume`` summary.
    The filter is intentionally narrow -- the operator-facing
    semantic of "resume" is a Movie or Episode that has a partial
    playback position, and anything else (Series, Season, Folder,
    BoxSet, ...) is not a valid row for that command.
    """
    payload = _unwrap_envelope(payload)
    if not isinstance(payload, list):
        return []
    allowed_types = {"Movie", "Episode"}
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
            "UserData.PlayedPercentage": _safe_get(
                item, "UserData", "PlayedPercentage", default=0
            ),
            "RunTimeTicks": _format_runtime_minutes(
                item.get("RunTimeTicks")
            ),
        }
        for item in payload
        if isinstance(item, Mapping)
        and _safe_get(item, "Type", default=None) in allowed_types
    ]


def _summary_jellyfin_latest(payload: Any) -> list[dict[str, Any]]:
    """Render a Jellyfin ``latest`` payload as the curated summary.

    ``GET /Users/{user_id}/Items/Latest`` returns a slimmer DTO than
    ``GET /Items/{id}`` -- on the operator's Jellyfin v12 instance
    the response does not populate ``DateCreated`` on any row, so
    projecting the field would surface a "spurious null" for every
    row (the symptom that motivated this fix). The endpoint is
    scoped to "what changed in my library recently", so the four
    fields that survive (``Name``, ``Type``, ``ProductionYear``,
    ``SeriesName``) are sufficient to identify the item and chain
    it into ``jellyfin item <id>`` when the operator needs the
    full record. Operators who need ``DateCreated`` can fetch the
    full item via ``jellyfin item <id>`` (chainable from
    ``--verbose`` or the new ``Id`` column on `favorites`); the
    slim DTO the summary renders from does not carry it.
    """
    if not isinstance(payload, list):
        return []
    return [
        {
            "Name": _safe_get(item, "Name", default=None),
            "Type": _safe_get(item, "Type", default=None),
            "ProductionYear": _safe_get(item, "ProductionYear", default=0),
            "SeriesName": _safe_get(item, "SeriesName", default=None),
            "RunTimeTicks": _format_runtime_minutes(
                item.get("RunTimeTicks")
            ),
            "PremiereDate": _safe_get(
                item, "PremiereDate", default=None
            ),
            "Genres": _join_names(item.get("Genres")),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_radarr_calendar(payload: Any) -> list[dict[str, Any]]:
    """Render a Radarr ``calendar`` payload as the curated summary.

    ``GET /api/v3/calendar`` returns the upcoming-releases rows; on
    modern Radarr versions the endpoint wraps the rows in the
    paginated ``{page, pageSize, sortKey, sortDirection, totalRecords,
    records: [...]}`` envelope, so the renderer unwraps it via
    :func:`_unwrap_envelope` (defensive against envelope-drift across
    Radarr versions; mirrors :func:`_summary_radarr_recent`). A bare
    list is unchanged behaviour.

    The per-row projection surfaces ``monitored``, ``hasFile``,
    ``genres``, ``runtime`` (formatted as ``"<X>h <Y>m"`` via
    :func:`_format_runtime_minutes_whole`; Radarr's whole-minute
    ``runtime`` must not be fed into the Jellyfin-ticks formatter
    -- see the cross-reference in that function's docstring), the
    literal flat-with-dots ``collection.name`` key (so the
    ``_row_from_mapping`` dot-path walker resolves the nested
    collection envelope), and the three ISO-8601 release dates
    (``inCinemas``, ``physicalRelease``, ``digitalRelease``). Missing
    fields default to ``None`` so the human renderer prints ``<null>``
    rather than crashing on an upstream payload that omits any of
    them.
    """
    payload = _unwrap_envelope(payload)
    if not isinstance(payload, list):
        return []
    return [
        {
            "title": _safe_get(item, "title", default=None),
            "year": _safe_get(item, "year", default=0),
            "monitored": _safe_get(item, "monitored", default=False),
            "hasFile": _safe_get(item, "hasFile", default=False),
            "genres": _join_names(item.get("genres")),
            "runtime": _format_runtime_minutes_whole(
                item.get("runtime")
            ),
            "collection.name": _safe_get(
                item, "collection", "name", default=None
            ),
            "inCinemas": _safe_get(item, "inCinemas", default=None),
            "physicalRelease": _safe_get(
                item, "physicalRelease", default=None
            ),
            "digitalRelease": _safe_get(
                item, "digitalRelease", default=None
            ),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_radarr_wanted(payload: Any) -> list[dict[str, Any]]:
    """Render a Radarr ``wanted`` payload as the curated summary."""
    payload = _unwrap_envelope(payload)
    if not isinstance(payload, list):
        return []
    return [
        {
            "title": _safe_get(item, "title", default=None),
            "year": _safe_get(item, "year", default=0),
            "tmdbId": _safe_get(item, "tmdbId", default=0),
            "monitored": _safe_get(item, "monitored", default=False),
            "hasFile": _safe_get(item, "hasFile", default=False),
            "genres": _join_names(item.get("genres")),
            "physicalRelease": _safe_get(
                item, "physicalRelease", default=None
            ),
            "releaseDate": _safe_get(
                item, "releaseDate", default=None
            ),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_jellyfin_search(payload: Any) -> list[dict[str, Any]]:
    """Render a Jellyfin ``search`` payload as the curated summary.

    ``GET /Items?searchTerm=...&Recursive=true`` returns a paginated
    envelope of the shape ``{Items: [...], TotalRecordCount: N,
    StartIndex: 0}``; iterate ``Items`` so the default summary is
    non-empty when the envelope is well-formed. A bare list is
    unchanged behaviour. ``search`` is broader than ``recent`` /
    ``resume`` -- Jellyfin also returns Series, Season, BoxSet, etc.
    -- so no defensive type filter is applied.
    """
    payload = _unwrap_envelope(payload)
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


def _summary_jellyfin_nextup(payload: Any) -> list[dict[str, Any]]:
    """Render a Jellyfin ``nextup`` payload as the curated summary.

    ``GET /Shows/NextUp?UserId=...`` returns a paginated envelope of
    the shape ``{Items: [...], TotalRecordCount: N, StartIndex: 0}``;
    iterate ``Items`` so the default summary is non-empty when the
    envelope is well-formed. A bare list is unchanged behaviour.
    """
    payload = _unwrap_envelope(payload)
    if not isinstance(payload, list):
        return []
    return [
        {
            "Name": _safe_get(item, "Name", default=None),
            "SeriesName": _safe_get(item, "SeriesName", default=None),
            "ParentIndexNumber": _safe_get(
                item, "ParentIndexNumber", default=0
            ),
            "IndexNumber": _safe_get(item, "IndexNumber", default=0),
            "PremiereDate": _safe_get(item, "PremiereDate", default=None),
            "RunTimeTicks": _format_runtime_minutes(
                item.get("RunTimeTicks")
            ),
            "Overview": _safe_get(item, "Overview", default=None),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_jellyfin_item(payload: Any) -> dict[str, Any]:
    """Render a Jellyfin ``item <id>`` payload as the curated summary.

    ``GET /Items/{id}`` returns a single object with the item's full
    metadata (name, type, production year, premiere date, runtime in
    100-ns ticks, ``Genres[]``, ``Overview``, ``Status``). The
    curated shape flattens the runtime to ``"<X>h <Y>m"`` and the
    nested ``Genres`` array to a single ``", "-joined`` string so the
    default ``--human`` rendering stays a readable block per key
    (otherwise each cell would render as ``<N items>`` /
    ``<N keys>``).

    Two input shapes are accepted (mirroring the
    size-to-summary-renderer convention):

    * A single ``Mapping`` (the canonical shape for ``cmd_item``).
    * A list containing a single ``Mapping`` (the test fixture
      convention; mirrors :func:`_summary_seerr_tv` /
      :func:`_summary_seerr_movie`). Empty list maps to ``{}`` and
      ``None`` / scalar payloads map to ``{}`` so neither crashes.
    """
    if isinstance(payload, list):
        if not payload:
            return {}
        payload = payload[0]
    if not isinstance(payload, Mapping):
        return {}

    return {
        "Name": _safe_get(payload, "Name", default=None),
        "Type": _safe_get(payload, "Type", default=None),
        "ProductionYear": _safe_get(
            payload, "ProductionYear", default=0
        ),
        "PremiereDate": _safe_get(
            payload, "PremiereDate", default=None
        ),
        "RunTimeTicks": _format_runtime_minutes(
            payload.get("RunTimeTicks")
        ),
        "Genres": _join_names(payload.get("Genres")),
        "Overview": _safe_get(payload, "Overview", default=None),
        "Status": _safe_get(payload, "Status", default=None),
    }


def _summary_radarr_queue(payload: Any) -> list[dict[str, Any]]:
    """Render a Radarr ``queue`` payload as the curated summary.

    The Radarr ``/api/v3/queue`` rows carry ``title``, ``year``,
    ``quality`` (a nested ``{quality: {name}}`` envelope in modern
    Radarr versions -- the renderer surfaces the flat ``quality``
    key the upstream payload also exposes), ``status``, and
    ``timeLeft``. The audit reshaped the column set to surface
    ``year``, ``quality``, and ``timeLeft`` instead of the
    pre-change ``trackedDownloadStatus`` / ``size`` / ``sizeleft``
    fields; the renderer mirrors that change.

    ``timeLeft`` defaults to ``None`` so the human renderer prints
    the verbatim upstream string (typically ``"00:00:00"`` when
    Radarr has not yet started the download) rather than the
    misleading ``<null>`` placeholder -- the field stays visible
    even when the operator has not yet waited on the download.
    """
    payload = _unwrap_envelope(payload)
    if not isinstance(payload, list):
        return []
    return [
        {
            "title": _safe_get(item, "title", default=None),
            "year": _safe_get(item, "year", default=0),
            "quality": _safe_get(item, "quality", default=None),
            "status": _safe_get(item, "status", default=None),
            "timeLeft": _safe_get(item, "timeleft", default=None),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_radarr_recent(payload: Any) -> list[dict[str, Any]]:
    """Render a Radarr ``recent`` payload as the curated summary.

    ``GET /api/v3/history`` returns the paginated activity-log
    envelope of the shape ``{page, pageSize, sortKey, sortDirection,
    totalRecords, records: [...]}``; unwrap to the bare ``records``
    list so the summary is non-empty when the envelope is
    well-formed. A bare list is unchanged behaviour (defensive
    against envelope-drift across Radarr versions). The unwrap
    happens here, inside the renderer, so the ``--verbose`` path
    still emits the verbatim envelope (the upstream pagination
    metadata ``totalRecords`` etc. is then visible to downstream
    consumers). Mirrors the same pattern in
    :func:`_summary_sonarr_recent` and the Jellyfin paginated
    renderers.

    The per-row projection lifts ``movie.title`` and ``movie.year``
    to the top-level ``title`` / ``year`` keys (matching the audit's
    column-set ask) and adds ``sourceTitle``, ``quality``,
    ``language``, and ``client`` (Radarr's ``downloadClient`` is
    mapped to the ``client`` summary key for the audit's column
    name). The original ``movie`` envelope is preserved in the row
    so ``--verbose`` consumers chaining on the original nested
    ``movie.title`` / ``movie.year`` shape keep working.
    """
    payload = _unwrap_envelope(payload)
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
            top_title: Any = movie_obj["title"]
            top_year: Any = movie_obj["year"]
        else:
            movie_obj = {"title": None, "year": 0}
            top_title = None
            top_year = 0
        summaries.append(
            {
                "title": top_title,
                "year": top_year,
                "movie": movie_obj,
                "sourceTitle": _safe_get(
                    item, "sourceTitle", default=None
                ),
                "quality": _safe_get(item, "quality", default=None),
                "language": _safe_get(item, "language", default=None),
                "client": _safe_get(
                    item, "downloadClient", default=None
                ),
                "eventType": _safe_get(item, "eventType", default=None),
                "date": _safe_get(item, "date", default=None),
            }
        )
    return summaries


def _summary_sonarr_wanted(payload: Any) -> list[dict[str, Any]]:
    """Render a Sonarr ``wanted`` payload as the curated summary.

    The per-row projection surfaces the audit-asked ``hasFile``,
    ``runtime`` (formatted as ``"<X>h <Y>m"`` via
    :func:`_format_runtime_minutes_whole`; Sonarr's ``runtime`` is
    whole-minute like Radarr's, so the Jellyfin-ticks formatter is
    not used here), a truncated ``overview`` (via
    :func:`_truncate_overview` with the documented 80-character
    budget; same helper as the radarr audit fix), and
    ``series.title`` lifted from the nested ``series`` envelope
    alongside the pre-change ``title`` / ``seasonNumber`` /
    ``episodeNumber`` / ``airDate`` / ``monitored`` keys. Missing
    fields default to ``None`` / ``0`` / ``False`` per the per-type
    contract so the renderer stays well-formed even when upstream
    drops a field.
    """
    payload = _unwrap_envelope(payload)
    if not isinstance(payload, list):
        return []
    return [
        {
            "title": _safe_get(item, "title", default=None),
            "seasonNumber": _safe_get(item, "seasonNumber", default=0),
            "episodeNumber": _safe_get(item, "episodeNumber", default=0),
            "airDate": _safe_get(item, "airDate", default=None),
            "monitored": _safe_get(item, "monitored", default=False),
            "hasFile": _safe_get(item, "hasFile", default=False),
            "runtime": _format_runtime_minutes_whole(
                _safe_get(item, "runtime", default=None)
            ),
            "overview": _truncate_overview(item.get("overview")),
            "series.title": _safe_get(
                item, "series", "title", default=None
            ),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_sonarr_queue(payload: Any) -> list[dict[str, Any]]:
    """Render a Sonarr ``queue`` payload as the curated summary.

    The per-row projection keeps the pre-change ``title`` /
    ``status`` / ``trackedDownloadStatus`` / ``size`` / ``sizeleft``
    keys in their existing positions and appends the audit-asked
    ``series.title`` (lifted from the nested ``series`` envelope),
    ``episode`` (the flat ``episode.title`` projected to the top
    level via :func:`_safe_get`), ``quality`` (the flat ``quality``
    key the upstream payload exposes; matches the radarr ``queue``
    projection), and ``timeLeft`` (lifted from ``timeleft``).
    ``status`` is a pre-change key and MUST NOT be re-appended at
    the tail; there is no pre-change ``progress`` key on the
    queue summary and the audit does not add one. Missing fields
    default to ``None`` / ``0`` per the per-type contract so the
    renderer stays well-formed even when upstream drops a field.
    """
    payload = _unwrap_envelope(payload)
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
            "series.title": _safe_get(
                item, "series", "title", default=None
            ),
            "episode": _safe_get(
                item, "episode", "title", default=None
            ),
            "quality": _safe_get(item, "quality", default=None),
            "timeLeft": _safe_get(item, "timeleft", default=None),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_sonarr_recent(payload: Any) -> list[dict[str, Any]]:
    """Render a Sonarr ``recent`` payload as the curated summary.

    ``GET /api/v3/history?includeSeries=true&includeEpisode=true``
    returns activity-log rows that carry nested ``series: {title}``
    / ``episode: {title}`` envelopes (populated by Sonarr when the
    operator opts in via the documented include flags). The
    renderer projects both the nested objects (preserving the
    originally documented ``{series: {title}, episode: {title},
    eventType, date}`` contract) and the flat identity fields the
    upstream payload also carries -- the fix is additive so
    operators chaining ``--json`` consumers against the summary
    shape do not break on the field set. A missing envelope (e.g.
    a row from an older Sonarr version that ignores the include
    flag, or a defensive fallback for an unflagged payload) renders
    as ``{title: None}`` instead of crashing.
    """
    payload = _unwrap_envelope(payload)
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        series = item.get("series")
        if isinstance(series, Mapping):
            series_obj: dict[str, Any] = {
                "title": _safe_get(series, "title", default=None),
            }
        else:
            series_obj = {"title": None}
        episode = item.get("episode")
        if isinstance(episode, Mapping):
            episode_obj: dict[str, Any] = {
                "title": _safe_get(episode, "title", default=None),
            }
        else:
            episode_obj = {"title": None}
        summaries.append(
            {
                "series": series_obj,
                "episode": episode_obj,
                "id": _safe_get(item, "id", default=None),
                "seriesId": _safe_get(item, "seriesId", default=None),
                "episodeId": _safe_get(item, "episodeId", default=None),
                "sourceTitle": _safe_get(item, "sourceTitle", default=None),
                "eventType": _safe_get(item, "eventType", default=None),
                "date": _safe_get(item, "date", default=None),
                "quality": _safe_get(item, "quality", default=None),
                "language": _safe_get(item, "language", default=None),
                "client": _safe_get(
                    item, "downloadClient", default=None
                ),
            }
        )
    return summaries


def _summary_sonarr_calendar(payload: Any) -> list[dict[str, Any]]:
    """Render a Sonarr ``calendar`` payload as the curated summary.

    ``GET /api/v3/calendar`` returns the upcoming-episodes rows; on
    modern Sonarr versions the endpoint wraps the rows in the
    paginated ``{page, pageSize, sortKey, sortDirection, totalRecords,
    records: [...]}`` envelope, so the renderer unwraps it via
    :func:`_unwrap_envelope` (defensive against envelope-drift
    across Sonarr versions; mirrors :func:`_summary_radarr_calendar`
    and :func:`_summary_sonarr_recent`). A bare list is unchanged
    behaviour.

    The per-row projection surfaces ``monitored``, ``hasFile``,
    ``runtime`` (formatted as ``"<X>h <Y>m"`` via
    :func:`_format_runtime_minutes_whole`; Sonarr's ``runtime``
    field is whole-minute like Radarr's, not Jellyfin-ticks), and
    a truncated ``overview`` (via :func:`_truncate_overview` with
    the documented 80-character budget) alongside the pre-change
    calendar-shape keys ``title`` / ``seasonNumber`` /
    ``episodeNumber`` / ``airDate`` / ``series.title``. Missing
    fields default to ``None`` / ``0`` / ``False`` per the per-
    type contract so the renderer stays well-formed even when
    upstream drops a field.
    """
    payload = _unwrap_envelope(payload)
    if not isinstance(payload, list):
        return []
    return [
        {
            "title": _safe_get(item, "title", default=None),
            "seasonNumber": _safe_get(item, "seasonNumber", default=0),
            "episodeNumber": _safe_get(item, "episodeNumber", default=0),
            "airDate": _safe_get(item, "airDate", default=None),
            "series.title": _safe_get(
                item, "series", "title", default=None
            ),
            "monitored": _safe_get(item, "monitored", default=False),
            "hasFile": _safe_get(item, "hasFile", default=False),
            "runtime": _format_runtime_minutes_whole(
                _safe_get(item, "runtime", default=None)
            ),
            "overview": _truncate_overview(item.get("overview")),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_sonarr_lookup(payload: Any) -> list[dict[str, Any]]:
    """Render a Sonarr ``lookup`` payload as the curated summary.

    ``GET /api/v3/series/lookup?term=<urlencoded term>`` returns a
    list of candidate series records (TVDB-sourced defaults, not
    the operator's library rows). The per-row projection surfaces
    a ``statistics`` summary cell (formatted as
    ``"<file>/<total>"`` via :func:`_format_episode_progress`),
    ``genres`` (via :func:`_join_names`), a truncated ``overview``
    (via :func:`_truncate_overview` with the documented 80-
    character budget; same helper as the radarr audit fix), and
    ``status`` alongside the existing candidate-identity fields
    ``title`` / ``year`` / ``tvdbId`` / ``tvMazeId`` / ``id`` /
    ``monitored``. Missing fields default to ``None`` / ``0`` /
    ``False`` per the per-type contract so the renderer stays
    well-formed even when upstream drops a field (e.g. a candidate
    row that did not finish linking and lacks ``statistics``).
    """
    if not isinstance(payload, list):
        return []
    return [
        {
            "title": _safe_get(item, "title", default=None),
            "year": _safe_get(item, "year", default=0),
            "tvdbId": _safe_get(item, "tvdbId", default=0),
            "tvMazeId": _safe_get(item, "tvMazeId", default=0),
            "id": _safe_get(item, "id", default=None),
            "monitored": _safe_get(item, "monitored", default=False),
            "statistics": _format_episode_progress(item),
            "genres": _join_names(item.get("genres")),
            "overview": _truncate_overview(item.get("overview")),
            "status": _safe_get(item, "status", default=None),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_sonarr_series(payload: Any) -> list[dict[str, Any]]:
    """Render a Sonarr ``series`` (no id) library-list payload as the curated summary.

    ``GET /api/v3/series`` returns a bare list of library series
    records (every series the operator has added to Sonarr, not
    the candidates from ``/api/v3/series/lookup``). The per-row
    projection surfaces a ``statistics`` summary cell (formatted
    as ``"<file>/<total>"`` via :func:`_format_episode_progress`),
    ``genres`` (via :func:`_join_names`), ``firstAired``, and
    ``lastAired`` (ISO-8601 timestamps from the upstream ``firstAired``
    / ``lastAired` fields) alongside the pre-change library-list
    fields ``title`` / ``year`` / ``monitored`` / ``status`` /
    ``tvdbId`` / ``seasons``. Missing fields default to ``None``
    / ``0`` / ``False`` per the per-type contract so the renderer
    stays well-formed even when upstream drops a field.
    """
    if not isinstance(payload, list):
        return []
    return [
        {
            "title": _safe_get(item, "title", default=None),
            "year": _safe_get(item, "year", default=0),
            "monitored": _safe_get(item, "monitored", default=False),
            "status": _safe_get(item, "status", default=None),
            "tvdbId": _safe_get(item, "tvdbId", default=0),
            "seasons": _safe_get(item, "seasons", default=[]),
            "statistics": _format_episode_progress(item),
            "genres": _join_names(item.get("genres")),
            "firstAired": _safe_get(item, "firstAired", default=None),
            "lastAired": _safe_get(item, "lastAired", default=None),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_sonarr_series_id(payload: Any) -> dict[str, Any]:
    """Render a Sonarr ``series <id>`` single-record payload as the curated summary.

    ``GET /api/v3/series/{id}`` returns a single object with the
    series' full library metadata. The curated shape surfaces
    ``status``, ``monitored``, a ``statistics`` summary cell
    (formatted as ``"<file>/<total>"`` via
    :func:`_format_episode_progress`), ``genres`` (via
    :func:`_join_names`), ``firstAired``, and ``lastAired``
    alongside the pre-change ``title`` / ``year`` / ``runtime``
    (formatted as ``"<X>h <Y>m"`` via
    :func:`_format_runtime_minutes_whole`).

    Two input shapes are accepted (mirroring the
    size-to-summary-renderer convention):

    * A single ``Mapping`` (the canonical ``/api/v3/series/{id}``
      shape).
    * A list containing a single ``Mapping`` (the test fixture
      convention; mirrors :func:`_summary_radarr_movie_id` /
      :func:`_summary_seerr_movie` / :func:`_summary_seerr_tv`).

    Empty list maps to ``{}`` and ``None`` / scalar payloads map
    to ``{}`` so neither crashes. The renderer is invoked via the
    dedicated dispatch key ``("sonarr", "series_id")`` so the
    ``cmd_series`` handler can pick the right renderer per branch
    (no-id list vs with-id single); mirrors the radarr
    ``("radarr", "movie_id")`` dispatch trick.
    """
    if isinstance(payload, list):
        if not payload:
            return {}
        payload = payload[0]
    if not isinstance(payload, Mapping):
        return {}

    return {
        "title": _safe_get(payload, "title", default=None),
        "year": _safe_get(payload, "year", default=0),
        "runtime": _format_runtime_minutes_whole(
            _safe_get(payload, "runtime", default=None)
        ),
        "status": _safe_get(payload, "status", default=None),
        "monitored": _safe_get(payload, "monitored", default=False),
        "statistics": _format_episode_progress(payload),
        "genres": _join_names(payload.get("genres")),
        "firstAired": _safe_get(payload, "firstAired", default=None),
        "lastAired": _safe_get(payload, "lastAired", default=None),
    }


def _summary_seerr_requests(payload: Any) -> list[dict[str, Any]]:
    """Render a Seerr ``requests`` payload as the curated summary.

    ``GET /api/v1/request`` returns a paginated envelope of the shape
    ``{pageInfo: {pages, pageSize, results, page}, results: [...],
    serviceErrors: {...}}``; iterate ``results`` so the default summary
    is non-empty when the envelope is well-formed. A bare list is
    unchanged behaviour.

    The per-item projection surfaces the identity fields the upstream
    payload actually carries on the operator's Seer instance at the
    **top level** -- ``id``, ``mediaType``, ``tmdbId``, ``tvdbId``,
    ``externalServiceSlug`` (lifted from the ``media`` sub-dict).
    AGENTS.md §1 says both ``seerr requests`` and ``seerr available``
    should "project the identity fields instead" of ``media.title``;
    the historical projection nested the identity behind ``media.*``
    keys while :func:`_summary_seerr_available` returned flat identity
    fields at the top level, so the two seerr read commands disagreed
    on what shape that projection took. The fix removes the
    asymmetry so the operator has one mental model across both
    commands (same identity field set, same flat column names).

    The request-level fields (``type``, ``status``, ``createdAt``)
    stay top-level alongside the lifted identity. ``status`` is the
    request status (not the underlying media status) -- the bit the
    operator cares about when scanning the household request queue.
    The ``requestedBy.displayName`` projection was dropped: the
    ``--human`` column list does not include it (the 120-char width
    budget divided across eight columns truncates the 23-char token
    to ``requestedBy.di...``), and the upstream detail is available
    via ``--verbose`` for operators who need it.

    The historical ``title`` projection is still retired -- neither
    ``media.title`` (movie) nor ``media.name`` (TV) is populated on
    the live operator's Seer instance, so every row would have
    projected ``title: null``. Operators who need a friendly title
    can resolve it via ``seerr movie <tmdbId>`` / ``seerr tv
    <tvdbId>`` or ``seerr search <query>``; the curated summary
    here stays as the canonical "what is on the household request
    queue" answer and gives the operator enough identity to chain
    into the detail commands.
    """
    if isinstance(payload, Mapping):
        payload = payload.get("results")
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        # Pull the identity fields from the ``media`` sub-dict when
        # it is well-formed; fall back to ``None`` on every identity
        # field otherwise (mirrors the defensive contract of every
        # other renderer and lets the summary stay well-formed even
        # when upstream drops the ``media`` envelope entirely).
        # Same field set as :func:`_summary_seerr_available` -- the
        # only difference is that ``status`` here is the request
        # status (from the outer record) rather than the media
        # status (which only ``seerr available`` surfaces).
        media = item.get("media")
        if isinstance(media, Mapping):
            id_ = _safe_get(media, "id", default=None)
            media_type = _safe_get(media, "mediaType", default=None)
            tmdb_id = _safe_get(media, "tmdbId", default=None)
            tvdb_id = _safe_get(media, "tvdbId", default=None)
            slug = _safe_get(
                media, "externalServiceSlug", default=None
            )
        else:
            id_ = media_type = tmdb_id = tvdb_id = slug = None
        summaries.append(
            {
                "id": id_,
                "mediaType": media_type,
                "tmdbId": tmdb_id,
                "tvdbId": tvdb_id,
                "externalServiceSlug": slug,
                "type": _safe_get(item, "type", default=None),
                "status": _safe_get(item, "status", default=None),
                "createdAt": _safe_get(item, "createdAt", default=None),
            }
        )
    return summaries


def _summary_seerr_search(payload: Any) -> list[dict[str, Any]]:
    """Render a Seerr ``search`` payload as the curated summary.

    ``GET /api/v1/search`` returns a paginated envelope of the shape
    ``{page, totalPages, totalResults, results: [...]}``; iterate
    ``results`` so the default summary is non-empty when the envelope
    is well-formed. A bare list is unchanged behaviour. ``results[]``
    routinely mixes movie, TV, and person rows in a single envelope.

    The per-item projection surfaces the top-level ``id`` field the
    upstream payload actually carries. Unlike the historical
    ``mediaInfo.tmdbId`` shape, the rows in the operator's Seer
    ``/api/v1/search`` response do not expose a nested ``mediaInfo``
    envelope -- the TMDB/TVDB id lives at the top level as ``id``
    (same convention as :func:`_summary_seerr_available`). The
    historical fabricated ``{"tmdbId": 0}`` placeholder was
    misleading because every row was projected as ``tmdbId: 0`` even
    for hits with well-known ids (e.g. ``603`` for *The Matrix*).
    Branch on ``mediaType`` so TV and person rows source their title
    from ``name`` (the upstream payload exposes ``name`` only on
    those rows, with no top-level ``title``) while movie rows
    source it from ``title``.
    """
    if isinstance(payload, Mapping):
        payload = payload.get("results")
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        media_type = _safe_get(item, "mediaType", default="")
        title_key = "name" if media_type in ("tv", "person") else "title"
        summaries.append(
            {
                "id": _safe_get(item, "id", default=None),
                "title": _safe_get(item, title_key, default=None),
                "mediaType": _safe_get(item, "mediaType", default=None),
                "releaseDate": _safe_get(
                    item, "releaseDate", default=None
                ),
            }
        )
    return summaries


def _summary_seerr_available(payload: Any) -> list[dict[str, Any]]:
    """Render a Seerr ``available`` payload as the curated summary.

    ``GET /api/v1/media`` returns a paginated envelope of the shape
    ``{pageInfo: {pages, pageSize, results, page}, results: [...],
    serviceErrors: {...}}``; iterate ``results`` so the default
    summary is non-empty when the envelope is well-formed. A bare
    list is unchanged behaviour.

    The per-item projection surfaces the top-level identifiers the
    upstream payload actually carries (no nested ``mediaInfo``
    envelope to unwrap -- the operator-visible record is the
    top-level media row, with its ``tmdbId`` / ``tvdbId`` /
    ``externalServiceSlug`` / ``status`` / ``mediaAddedAt`` fields
    living at the top level). The historical
    ``{title, mediaType, releaseDate, mediaInfo.status}`` projection
    was retired because upstream records no longer carry a top-level
    ``title`` field, which made the old client-side substring filter
    a guaranteed no-op (every row was dropped). Operators who need a
    friendly title can resolve it via ``seerr search <query>`` or
    look up ``tmdbId`` / ``tvdbId`` externally; the curated summary
    here stays as the canonical "what is in my library" answer.
    """
    if isinstance(payload, Mapping):
        payload = payload.get("results")
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        summaries.append(
            {
                "id": _safe_get(item, "id", default=None),
                "mediaType": _safe_get(item, "mediaType", default=None),
                "tmdbId": _safe_get(item, "tmdbId", default=None),
                "tvdbId": _safe_get(item, "tvdbId", default=None),
                "externalServiceSlug": _safe_get(
                    item, "externalServiceSlug", default=None
                ),
                "status": _safe_get(item, "status", default=None),
                "mediaAddedAt": _safe_get(
                    item, "mediaAddedAt", default=None
                ),
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

    The ``results[]`` array can mix ``mediaType`` values (both
    ``movie`` and ``tv``) when ``seerr trending`` runs without a
    media-type positional, so the per-item projection branches on
    each item's ``mediaType``: ``movie`` items use ``title`` /
    ``releaseDate``; ``tv`` items use ``name`` / ``firstAirDate``.

    The per-item projection surfaces the top-level ``id`` field the
    upstream payload actually carries. The rows in the operator's
    Seer ``/api/v1/discover/trending`` response do not expose a
    nested ``mediaInfo`` envelope -- the TMDB/TVDB id lives at the
    top level as ``id`` (same convention as :func:`_summary_seerr_search`
    and :func:`_summary_seerr_available`). The historical fabricated
    ``{"tmdbId": 0}`` placeholder was misleading because every row
    was projected as ``tmdbId: 0`` even for hits with well-known
    ids. Branch on ``mediaType`` so TV rows source their title from
    ``name`` (TV rows expose ``name`` only, no top-level ``title``)
    while movie rows source it from ``title``.

    This differs from :func:`_summary_seerr_search` -- search is not
    known to mix media types in a single envelope -- so the
    projections are intentionally **not** byte-identical even
    though the envelopes share the same outer shape.
    """
    if isinstance(payload, Mapping):
        payload = payload.get("results")
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        is_tv = _safe_get(item, "mediaType", default="") == "tv"
        summaries.append(
            {
                "id": _safe_get(item, "id", default=None),
                "title": _safe_get(
                    item, "name" if is_tv else "title", default=None
                ),
                "mediaType": _safe_get(item, "mediaType", default=None),
                "releaseDate": _safe_get(
                    item,
                    "firstAirDate" if is_tv else "releaseDate",
                    default=None,
                ),
            }
        )
    return summaries


def _summary_seerr_upcoming_movies(payload: Any) -> list[dict[str, Any]]:
    """Render a Seerr ``upcoming-movies`` payload as the curated summary.

    ``GET /api/v1/discover/movies/upcoming`` returns a paginated
    envelope of the shape ``{page, totalPages, totalResults,
    results: [...]}`` -- the same envelope
    :func:`_summary_seerr_trending` consumes -- so the projection is
    intentionally identical: ``id``, ``title``, ``mediaType``,
    ``releaseDate``. A bare list is unchanged behaviour (defensive
    against envelope-drift across Seer versions).

    The per-item projection surfaces the top-level ``id`` field the
    upstream payload actually carries. The rows in the operator's
    Seer ``/api/v1/discover/movies/upcoming`` response do not
    expose a nested ``mediaInfo`` envelope -- the TMDB id lives at
    the top level as ``id`` (same convention as
    :func:`_summary_seerr_search`). The historical fabricated
    ``{"tmdbId": 0}`` placeholder was misleading because every row
    was projected as ``tmdbId: 0`` even for hits with well-known
    ids.

    The only difference from :func:`_summary_seerr_trending` is the
    dispatch key -- same envelope, same per-item shape, so the
    renderer is byte-identical to keep row-for-row alignment when
    the operator tabulates ``seerr trending`` and ``seerr
    upcoming-movies`` together.
    """
    if isinstance(payload, Mapping):
        payload = payload.get("results")
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        summaries.append(
            {
                "id": _safe_get(item, "id", default=None),
                "title": _safe_get(item, "title", default=None),
                "mediaType": _safe_get(item, "mediaType", default=None),
                "releaseDate": _safe_get(
                    item, "releaseDate", default=None
                ),
            }
        )
    return summaries


def _summary_seerr_upcoming_tv(payload: Any) -> list[dict[str, Any]]:
    """Render a Seerr ``upcoming-tv`` payload as the curated summary.

    ``GET /api/v1/discover/tv/upcoming`` returns a paginated envelope
    of the shape ``{page, totalPages, totalResults, results: [...]}``
    -- the same outer envelope :func:`_summary_seerr_trending` and
    :func:`_summary_seerr_upcoming_movies` consume -- but TV items
    use ``name`` / ``firstAirDate`` rather than ``title`` /
    ``releaseDate``, so the per-item projection here is **not**
    byte-identical to :func:`_summary_seerr_upcoming_movies``.
    ``mediaType`` and the top-level ``id`` are unchanged. A bare
    list is unchanged behaviour (defensive against envelope-drift
    across Seer versions).

    The per-item projection surfaces the top-level ``id`` field the
    upstream payload actually carries. The rows in the operator's
    Seer ``/api/v1/discover/tv/upcoming`` response do not expose
    a nested ``mediaInfo`` envelope -- the TVDB id lives at the
    top level as ``id`` (same convention as
    :func:`_summary_seerr_search`). The historical fabricated
    ``{"tmdbId": 0}`` placeholder was misleading because every row
    was projected as ``tmdbId: 0`` even for hits with well-known
    ids.
    """
    if isinstance(payload, Mapping):
        payload = payload.get("results")
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        summaries.append(
            {
                "id": _safe_get(item, "id", default=None),
                "title": _safe_get(item, "name", default=None),
                "mediaType": _safe_get(item, "mediaType", default=None),
                "releaseDate": _safe_get(
                    item, "firstAirDate", default=None
                ),
            }
        )
    return summaries


def _summary_seerr_discover_movies(payload: Any) -> list[dict[str, Any]]:
    """Render a Seerr ``discover-movies`` payload as the curated summary.

    ``GET /api/v1/discover/movies`` returns a paginated envelope of
    the shape ``{page, totalPages, totalResults, results: [...]}``
    -- the same envelope :func:`_summary_seerr_trending` /
    :func:`_summary_seerr_upcoming_movies` consume -- so the
    projection is intentionally identical: ``id``, ``title``,
    ``mediaType``, ``releaseDate``. A bare list is unchanged
    behaviour (defensive against envelope-drift across Seer
    versions).

    The per-item projection surfaces the top-level ``id`` field the
    upstream payload actually carries. The rows in the operator's
    Seer ``/api/v1/discover/movies`` response do not expose a
    nested ``mediaInfo`` envelope -- the TMDB id lives at the top
    level as ``id`` (same convention as
    :func:`_summary_seerr_search`). The historical fabricated
    ``{"tmdbId": 0}`` placeholder was misleading because every row
    was projected as ``tmdbId: 0`` even for hits with well-known
    ids.

    The renderer is byte-identical to :func:`_summary_seerr_upcoming_movies`
    so the operator's default summary aligns row-for-row when
    ``seerr upcoming-movies`` and ``seerr discover-movies`` are
    tabulated together.
    """
    if isinstance(payload, Mapping):
        payload = payload.get("results")
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        summaries.append(
            {
                "id": _safe_get(item, "id", default=None),
                "title": _safe_get(item, "title", default=None),
                "mediaType": _safe_get(item, "mediaType", default=None),
                "releaseDate": _safe_get(
                    item, "releaseDate", default=None
                ),
            }
        )
    return summaries


def _summary_seerr_discover_tv(payload: Any) -> list[dict[str, Any]]:
    """Render a Seerr ``discover-tv`` payload as the curated summary.

    ``GET /api/v1/discover/tv`` returns a paginated envelope of the
    shape ``{page, totalPages, totalResults, results: [...]}`` --
    the same outer envelope :func:`_summary_seerr_discover_movies` /
    :func:`_summary_seerr_upcoming_tv` consume -- but TV items use
    ``name`` / ``firstAirDate`` rather than ``title`` /
    ``releaseDate``, so the per-item projection here is **not**
    byte-identical to :func:`_summary_seerr_discover_movies``.
    ``mediaType`` and the top-level ``id`` are unchanged. A bare
    list is unchanged behaviour (defensive against envelope-drift
    across Seer versions).

    The per-item projection surfaces the top-level ``id`` field the
    upstream payload actually carries. The rows in the operator's
    Seer ``/api/v1/discover/tv`` response do not expose a nested
    ``mediaInfo`` envelope -- the TVDB id lives at the top level as
    ``id`` (same convention as :func:`_summary_seerr_search`). The
    historical fabricated ``{"tmdbId": 0}`` placeholder was
    misleading because every row was projected as ``tmdbId: 0``
    even for hits with well-known ids.
    """
    if isinstance(payload, Mapping):
        payload = payload.get("results")
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        summaries.append(
            {
                "id": _safe_get(item, "id", default=None),
                "title": _safe_get(item, "name", default=None),
                "mediaType": _safe_get(item, "mediaType", default=None),
                "releaseDate": _safe_get(
                    item, "firstAirDate", default=None
                ),
            }
        )
    return summaries


def _summary_seerr_genres(payload: Any) -> list[dict[str, Any]]:
    """Render a Seerr ``genres [MEDIA_TYPE]`` payload as the curated summary.

    ``GET /api/v1/genres/<movie|tv>`` returns a bare list of
    ``{id: int, name: str}`` documents (no envelope wrapping) --
    the canonical TMDB genre shape. The projection is the
    passthrough ``{id, name}`` pair per item so the default
    ``--human`` table renders as the documented ``Id | Name``
    (US-4). The handler's ``columns = ["id", "name"]`` literal in
    :mod:`arr_cli.seerr` aligns row-for-row with this summary
    shape (no nested objects, no dot-path traversal).

    Defensive input handling mirrors the other seerr renderers:

    * A bare list of mappings is the canonical input.
    * A single mapping is treated as a one-row degenerate list so
      a future Seer release that returns ``{results: [...]}`` (or
      similar envelope) does not crash the renderer. Empty
      mapping maps to ``[]``.
    * Non-list / non-mapping payloads (scalar / ``None``) map to
      ``[]`` so the renderer prints its "no rows" footer rather
      than a stack trace.

    Items that are not mappings (e.g. raw id ints from a future
    schema change) are dropped without raising so the summary
    shape stays homogeneous.
    """
    if isinstance(payload, Mapping):
        # Treat a single mapping as a one-row degenerate list so a
        # future envelope-shaped variant (``{results: [...]}`` or
        # ``{genres: [...]}``) does not crash the renderer. Empty
        # mapping maps to ``[]``.
        payload = [payload] if payload else []
    if not isinstance(payload, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        summaries.append(
            {
                "id": _safe_get(item, "id", default=None),
                "name": _safe_get(item, "name", default=None),
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

    raw_genres = payload.get("genres")
    raw_ratings = payload.get("ratings")

    def _format_runtime(minutes: Any) -> str | None:
        """Format ``minutes`` as ``"<X>h <Y>m"``.

        The Seer ``movie`` payload's ``runtime`` field is already in
        whole minutes, so this is the simpler minutes-based formatter
        (not the Jellyfin-ticks formatter used by the
        ``_summary_jellyfin_*`` renderers). Returns ``None`` when
        ``minutes`` is missing or zero so the renderer prints
        ``<null>`` instead of ``"0h 0m"`` (which would be a
        misleading "the movie has zero runtime" cell).
        """
        if isinstance(minutes, bool) or not isinstance(
            minutes, (int, float)
        ):
            return None
        if minutes <= 0:
            return None
        total = int(minutes)
        hours = total // 60
        mins = total % 60
        return f"{hours}h {mins}m"
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
        "title": _safe_get(payload, "title", default=None),
        "originalTitle": _safe_get(
            payload, "originalTitle", default=None
        ),
        "releaseDate": _safe_get(payload, "releaseDate", default=None),
        "runtime": _format_runtime(payload.get("runtime")),
        "genres": _join_names(raw_genres),
        "tagline": _safe_get(payload, "tagline", default=None),
        "ratings": ratings_obj,
    }


def _summary_radarr_movie_id(payload: Any) -> dict[str, Any]:
    """Render a Radarr ``movie <id>`` single-record payload as the curated summary.

    ``GET /api/v3/movie/{id}`` returns a single object with the
    movie's full library metadata. The curated shape surfaces
    ``status``, ``monitored``, ``hasFile``, ``genres`` (via
    :func:`_join_names`), a truncated ``overview`` (via
    :func:`_truncate_overview` with the documented 80-character
    budget; same helper as :func:`_summary_radarr_lookup` so the
    two ``overview`` projections stay byte-identical), ``added``,
    and ``studio``.

    Two input shapes are accepted (mirroring the
    size-to-summary-renderer convention):

    * A single ``Mapping`` (the canonical ``/api/v3/movie/{id}``
      shape).
    * A list containing a single ``Mapping`` (the test fixture
      convention; mirrors :func:`_summary_seerr_movie` /
      :func:`_summary_jellyfin_item`).

    Empty list maps to ``{}`` and ``None`` / scalar payloads map
    to ``{}`` so neither crashes. The renderer is invoked via the
    dedicated dispatch key ``("radarr", "movie_id")`` so the
    ``cmd_movie`` handler can pick the right renderer per branch
    (no-id list vs with-id single).
    """
    if isinstance(payload, list):
        if not payload:
            return {}
        payload = payload[0]
    if not isinstance(payload, Mapping):
        return {}

    return {
        "title": _safe_get(payload, "title", default=None),
        "year": _safe_get(payload, "year", default=0),
        "status": _safe_get(payload, "status", default=None),
        "monitored": _safe_get(payload, "monitored", default=False),
        "hasFile": _safe_get(payload, "hasFile", default=False),
        "genres": _join_names(payload.get("genres")),
        "overview": _truncate_overview(payload.get("overview")),
        "added": _safe_get(payload, "added", default=None),
        "studio": _safe_get(payload, "studio", default=None),
    }


def _summary_radarr_movie(payload: Any) -> list[dict[str, Any]]:
    """Render a Radarr ``movie`` (no id) library-list payload as the curated summary.

    ``GET /api/v3/movie`` returns a bare list of library movie
    records (every movie the operator has added to Radarr, not
    the candidates from ``/api/v3/movie/lookup``). The per-row
    projection surfaces ``hasFile`` (so the operator can see at a
    glance which library movies already have a file), ``genres``
    (via :func:`_join_names`), ``added`` (the ISO-8601 timestamp
    the movie was added to the library), ``collection.name`` (a
    literal flat-with-dots key so :func:`_row_from_mapping`'s
    nested-walk fallback resolves the nested ``collection``
    envelope), and ``studio``. Missing fields default to ``None``
    so the human renderer prints ``<null>`` rather than crashing
    on an upstream payload that omits any of them.
    """
    if not isinstance(payload, list):
        return []
    return [
        {
            "title": _safe_get(item, "title", default=None),
            "year": _safe_get(item, "year", default=0),
            "hasFile": _safe_get(item, "hasFile", default=False),
            "genres": _join_names(item.get("genres")),
            "added": _safe_get(item, "added", default=None),
            "collection.name": _safe_get(
                item, "collection", "name", default=None
            ),
            "studio": _safe_get(item, "studio", default=None),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_radarr_lookup(payload: Any) -> list[dict[str, Any]]:
    """Render a Radarr ``lookup`` payload as the curated summary.

    ``GET /api/v3/movie/lookup?term=<urlencoded term>`` returns a
    list of candidate movie records (TMDB-sourced defaults,
    not the operator's library rows). The per-row projection
    surfaces ``hasFile`` (always ``False`` for candidates -- the
    audit asks for it so the operator can scan a lookup result
    and immediately tell which candidates already exist in the
    library), ``genres`` (via :func:`_join_names`), a truncated
    ``overview`` (via :func:`_truncate_overview` with the
    documented 80-character budget; keeps the cell readable
    inside the 120-char NFR-Usability width cap when joined with
    the other nine columns), ``status``, and the existing
    ``tmdbId`` / ``imdbId`` / ``id`` / ``monitored`` identity
    fields. Missing fields default to ``None`` / ``0`` / ``False``
    per the per-type contract so the renderer stays well-formed
    even when upstream omits a field.
    """
    if not isinstance(payload, list):
        return []
    return [
        {
            "title": _safe_get(item, "title", default=None),
            "year": _safe_get(item, "year", default=0),
            "hasFile": _safe_get(item, "hasFile", default=False),
            "genres": _join_names(item.get("genres")),
            "overview": _truncate_overview(item.get("overview")),
            "status": _safe_get(item, "status", default=None),
            "tmdbId": _safe_get(item, "tmdbId", default=0),
            "imdbId": _safe_get(item, "imdbId", default=None),
            "id": _safe_get(item, "id", default=None),
            "monitored": _safe_get(item, "monitored", default=False),
        }
        for item in payload
        if isinstance(item, Mapping)
    ]


def _summary_maintainerr_pending(payload: Any) -> list[dict[str, Any]]:
    """Render a Maintainerr ``pending`` payload as the curated summary.

    ``GET /api/collections/overlay-data`` returns the per-collection
    overlay records Maintainerr uses to decide what is about to be
    cleaned up. The pre-change projection surfaced ``title``,
    ``mediaCount``, ``deleteAfterDays``, and ``isOnHold``; the audit
    widens the row to eleven keys so the operator can identify the
    collection, see how it will be cleaned up, when it was added,
    and whether it is currently active without having to re-fetch
    the record with ``--verbose``.

    The new keys are appended in the audit's left-to-right order so
    the renderer body and ``cmd_pending``'s column list stay
    byte-identical: ``arrAction``, ``addDate`` (ISO-8601 timestamp
    the collection was added), ``mediaServerType`` (e.g.
    ``"plex"`` / ``"jellyfin"`` / ``"emby"``), ``type`` (the
    collection's action category), a truncated ``description`` (via
    :func:`_truncate_overview` with the documented 80-character
    budget; same helper the sibling Radarr / Sonarr ``overview``
    projections use, keeping cell widths visually consistent across
    the five CLIs), ``isActive``, and ``visibleOnHome``. Missing
    fields default to ``None`` / ``0`` / ``False`` per the per-type
    contract so the renderer stays well-formed even when upstream
    drops a field (defensive against future Maintainerr drift).
    """
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
            "arrAction": _safe_get(item, "arrAction", default=None),
            "addDate": _safe_get(item, "addDate", default=None),
            "mediaServerType": _safe_get(
                item, "mediaServerType", default=None
            ),
            "type": _safe_get(item, "type", default=None),
            "description": _truncate_overview(item.get("description")),
            "isActive": _safe_get(item, "isActive", default=False),
            "visibleOnHome": _safe_get(
                item, "visibleOnHome", default=False
            ),
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
    ("jellyfin", "search"): _summary_jellyfin_search,
    ("jellyfin", "nextup"): _summary_jellyfin_nextup,
    ("jellyfin", "item"): _summary_jellyfin_item,
    ("radarr", "wanted"): _summary_radarr_wanted,
    ("radarr", "queue"): _summary_radarr_queue,
    ("radarr", "recent"): _summary_radarr_recent,
    ("radarr", "calendar"): _summary_radarr_calendar,
    ("radarr", "lookup"): _summary_radarr_lookup,
    ("radarr", "movie"): _summary_radarr_movie,
    ("radarr", "movie_id"): _summary_radarr_movie_id,
    ("sonarr", "wanted"): _summary_sonarr_wanted,
    ("sonarr", "queue"): _summary_sonarr_queue,
    ("sonarr", "recent"): _summary_sonarr_recent,
    ("sonarr", "calendar"): _summary_sonarr_calendar,
    ("sonarr", "lookup"): _summary_sonarr_lookup,
    ("sonarr", "series"): _summary_sonarr_series,
    ("sonarr", "series_id"): _summary_sonarr_series_id,
    ("seerr", "requests"): _summary_seerr_requests,
    ("seerr", "search"): _summary_seerr_search,
    ("seerr", "available"): _summary_seerr_available,
    ("seerr", "tv"): _summary_seerr_tv,
    ("seerr", "movie"): _summary_seerr_movie,
    ("seerr", "trending"): _summary_seerr_trending,
    ("seerr", "upcoming-movies"): _summary_seerr_upcoming_movies,
    ("seerr", "upcoming-tv"): _summary_seerr_upcoming_tv,
    ("seerr", "discover-movies"): _summary_seerr_discover_movies,
    ("seerr", "discover-tv"): _summary_seerr_discover_tv,
    ("seerr", "genres"): _summary_seerr_genres,
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
